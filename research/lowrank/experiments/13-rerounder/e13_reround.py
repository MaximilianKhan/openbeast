#!/usr/bin/env python3
"""E13: GPTQ-style error-feedback re-rounding on FROZEN Q2_K grids.

llama-quantize picks per-block scales/mins with imatrix weighting but
rounds each element independently (no cross-column error feedback). We
keep its grid (scales/d/dmin bytes untouched) and re-choose only the
2-bit codes with GPTQ error propagation driven by the captured
activation covariance S: quantize column j, spread the whitened error
onto not-yet-quantized columns via chol(inv(S)). Frozen-grid re-rounding
is the Bid-Up-legal alternation step (prior-art/arxiv-codesign-mining).

Output: a new GGUF where re-rounded Q2_K tensors replace the originals
byte-for-byte in format (only the code bits differ).
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "04-served-v0"))
from extract_adapter import REPO, deq, index_2d  # noqa: E402

sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf  # noqa: E402
from gguf import GGUFReader, GGUFWriter  # noqa: E402

QK_K = 256

_GRAM_SOURCE = [(r"attn_q\.|attn_v\.", lambda n: re.sub(
                    r"attn_[qv]\.", "attn_k.", n)),
                (r"ffn_gate\.", lambda n: n.replace("ffn_gate.", "ffn_up.")),
                (r"attn_gate\.", lambda n: n.replace("attn_gate.",
                                                     "attn_qkv."))]


def q2k_decode(raw, n_out, n_in):
    """raw uint8 (n_out*n_in/256, 84) -> dl, ml (n_out, n_in) f32 and the
    raw block array for later re-packing."""
    blocks = raw.reshape(-1, 84)
    nb = blocks.shape[0]
    scales = blocks[:, :16]
    d = blocks[:, 80:82].copy().view(np.float16).astype(np.float32)
    dmin = blocks[:, 82:84].copy().view(np.float16).astype(np.float32)
    dl = (d * (scales & 0xF).astype(np.float32))          # (nb, 16)
    ml = (dmin * (scales >> 4).astype(np.float32))
    dl = np.repeat(dl, 16, axis=1).reshape(n_out, n_in)
    ml = np.repeat(ml, 16, axis=1).reshape(n_out, n_in)
    return dl, ml, blocks


def q2k_pack_codes(blocks, q):
    """write 2-bit codes q (n_out*n_in,) back into blocks' qs bytes."""
    nb = blocks.shape[0]
    q = q.reshape(nb, 256).astype(np.uint8)
    out = blocks.copy()
    # element e: group g=e//128, s=(e%128)//32, byte b=e%32 -> qs byte
    # g*32+b, shift 2*s (mirrors gguf-py Q2_K dequant ordering)
    qs = np.zeros((nb, 64), dtype=np.uint8)
    e = np.arange(256)
    g, r = e // 128, e % 128
    s, b = r // 32, r % 32
    byte_idx = g * 32 + b
    shift = (2 * s).astype(np.uint8)
    for i in range(256):
        qs[:, byte_idx[i]] |= (q[:, i] & 3) << shift[i]
    out[:, 16:80] = qs
    return out


def gptq_hinv(S, damp=0.01):
    """upper U with inv(H) = U^T U. Triangularity is load-bearing (the
    sweep may only feed FUTURE columns) — on PD failure escalate damping
    rather than fall back to a dense factor."""
    n = S.shape[0]
    H0 = 0.5 * (S + S.T)
    base = float(np.mean(np.diag(H0)))
    for k in range(6):
        H = H0.copy()
        H[np.diag_indices(n)] += damp * (10 ** k) * base
        try:
            Hi = np.linalg.inv(H)
            Hi = 0.5 * (Hi + Hi.T)
            return np.linalg.cholesky(Hi).T
        except np.linalg.LinAlgError:
            continue
    raise np.linalg.LinAlgError("no PD damping found")


def reround_tensor(Wref, dl, ml, Hinv_u, blk=128):
    """rows vectorized, GPTQ column sweep with lazy-batch block updates
    (sequential inside a block, one GEMM per block for the tail)."""
    W = Wref.astype(np.float32).copy()
    m, n = W.shape
    q_out = np.zeros((m, n), dtype=np.uint8)
    Wq = np.empty((m, n), dtype=np.float32)
    for b0 in range(0, n, blk):
        b1 = min(b0 + blk, n)
        Err = np.zeros((m, b1 - b0), dtype=np.float32)
        for j in range(b0, b1):
            dlj = dl[:, j]
            mlj = ml[:, j]
            safe = np.where(dlj > 0, dlj, 1.0)
            q = np.clip(np.rint((W[:, j] + mlj) / safe), 0, 3)
            wq = dlj * q - mlj
            q_out[:, j] = q.astype(np.uint8)
            Wq[:, j] = wq
            djj = Hinv_u[j, j]
            if djj == 0:
                continue
            err = (W[:, j] - wq) / djj
            Err[:, j - b0] = err
            if j + 1 < b1:
                W[:, j + 1:b1] -= err[:, None] * Hinv_u[j, j + 1:b1][None, :]
        if b1 < n:
            W[:, b1:] -= Err @ Hinv_u[b0:b1, b1:]
    return q_out, Wq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("quant_gguf")
    ap.add_argument("gram_dir")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--deflate-adapter", default=None,
                    help="ProjQ-lite: deflate each tensor's metric by the "
                         "adapter's A row-space so the grid stops paying "
                         "for corrector-owned directions")
    ap.add_argument("--codes-dir", default=None,
                    help="two-phase mode: compute+cache codes per tensor "
                         "(resume-safe), assemble only when all present")
    ap.add_argument("--max-tensors", type=int, default=0,
                    help="phase-A chunk size (0 = no limit)")
    args = ap.parse_args()

    man = json.loads((Path(args.gram_dir) / "grams.json").read_text())
    hinv_cache = {}

    defl = {}
    if args.deflate_adapter:
        sys.path.insert(0, str(HERE.parent / "11-alternation"))
        from e11_alternate import load_adapter
        for base, (A, B, alpha) in load_adapter(args.deflate_adapter).items():
            Q, _ = np.linalg.qr(A.T.astype(np.float64))   # (n_in, r)
            defl[base] = Q.astype(np.float32)

    s_cache = {}

    def s_blocks(name, n_in):
        src = name
        for pat, fn in _GRAM_SOURCE:
            if re.search(pat, name):
                src = fn(name)
                break
        if src not in man or man[src]["n"] != n_in:
            return None
        if src not in s_cache:
            g = np.fromfile(Path(args.gram_dir) /
                            (src.replace("/", "_") + ".gram.bin"),
                            dtype=np.float32)
            nb = man[src]["blocks"]
            bs = n_in // nb
            s_cache[src] = [(b * bs,
                             g[b * bs * bs:(b + 1) * bs * bs]
                             .reshape(bs, bs).astype(np.float64)
                             / man[src]["count"]) for b in range(nb)]
        return s_cache[src]

    def hinv_for(name, n_in):
        """-> list of (col_offset, Hu); optionally deflated by the
        adapter's A row-space (full grams only)."""
        blocks = s_blocks(name, n_in)
        if blocks is None:
            return None
        Q = defl.get(name)
        key = (name if Q is not None else "shared", n_in,
               name if Q is not None else id(blocks))
        if Q is None and name in hinv_cache.get("_shared", {}):
            pass
        out = []
        for off, S in blocks:
            if Q is not None and len(blocks) == 1:
                Sd = S - Q @ (Q.T @ S)
                Sd = Sd - (Sd @ Q) @ Q.T
                Sd = 0.5 * (Sd + Sd.T)
                out.append((off, gptq_hinv(Sd).astype(np.float32)))
            else:
                out.append((off, gptq_hinv(S).astype(np.float32)))
        return out

    ref, _ = index_2d(args.ref_gguf)
    rdr = GGUFReader(args.quant_gguf)
    arch = rdr.get_field("general.architecture").contents()
    w = GGUFWriter(args.out, arch)
    for key, f in rdr.fields.items():
        if key.startswith("GGUF.") or key == "general.architecture":
            continue
        sub = f.types[-1] if f.types[0] == gguf.GGUFValueType.ARRAY else None
        w.add_key_value(key, f.contents(), f.types[0], sub_type=sub)

    codes = Path(args.codes_dir) if args.codes_dir else None
    if codes:
        codes.mkdir(parents=True, exist_ok=True)
    n_re = 0
    n_new = 0
    t0 = time.time()
    for t in rdr.tensors:
        is_q2k = t.tensor_type == gguf.GGMLQuantizationType.Q2_K
        shape = [int(d) for d in t.shape if int(d) > 1]
        if is_q2k and len(shape) == 2 and t.name in ref:
            n_out, n_in = shape[1], shape[0]
            Hu = hinv_for(t.name, n_in)
            if Hu is None:
                w.add_tensor(t.name, t.data, raw_dtype=t.tensor_type)
                continue
            cfile = codes / (t.name.replace("/", "_") + ".npy") \
                if codes else None
            if cfile and cfile.exists():
                q = np.load(cfile)
            else:
                if args.max_tensors and n_new >= args.max_tensors:
                    print(f"[chunk] limit reached ({n_new} new), "
                          f"rerun to continue", flush=True)
                    return
                Wref = deq(ref[t.name])
                dl, ml, _ = q2k_decode(np.asarray(t.data).reshape(-1),
                                       n_out, n_in)
                q = np.zeros((n_out, n_in), dtype=np.uint8)
                for off, U in Hu:
                    bsz = U.shape[0]
                    sl = slice(off, off + bsz)
                    qb, _ = reround_tensor(Wref[:, sl], dl[:, sl],
                                           ml[:, sl], U)
                    q[:, sl] = qb
                n_new += 1
                if cfile:
                    np.save(cfile, q)
            _, _, blocks = q2k_decode(np.asarray(t.data).reshape(-1),
                                      n_out, n_in)
            nb2 = q2k_pack_codes(blocks, q.reshape(-1))
            w.add_tensor(t.name, nb2.reshape(np.asarray(t.data).shape),
                         raw_dtype=gguf.GGMLQuantizationType.Q2_K)
            n_re += 1
            if n_re % 25 == 0:
                print(f"[reround] {n_re} tensors ({n_new} new), "
                      f"{time.time()-t0:.0f}s", flush=True)
        else:
            w.add_tensor(t.name, t.data, raw_dtype=t.tensor_type)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file(progress=False)
    w.close()
    print(f"wrote {args.out}: {n_re} tensors re-rounded, "
          f"{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
