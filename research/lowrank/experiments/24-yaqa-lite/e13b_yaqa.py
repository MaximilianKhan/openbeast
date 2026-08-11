#!/usr/bin/env python3
"""E24: YAQA-lite Kronecker-metric re-rounding on FROZEN Q2_K grids.

Copy of E13's re-rounder (experiments/13-rerounder/e13_reround.py) with
one upgrade: the objective becomes the Kronecker global metric
    tr(T dW S dW^T),  dW = Wref - Whato
with S = E[x x^T] (input Grams, E10 capture) and T = E[g g^T] (output-
gradient Grams, E20 capture) — YAQA's (2505.22988) Kronecker-factored
Hessian approximation A (x) B, instantiated with our measured factors.

Derivation (the "cleanest correct formulation" the mission asked for):
in column-major vec the Hessian is S (x) T, and
  inv(S (x) T) = (U_S (x) U_T)^T (U_S (x) U_T)
with U_S, U_T upper such that inv(S)=U_S^T U_S, inv(T)=U_T^T U_T.
U_S (x) U_T is upper-triangular in lexicographic (column-major,
row-minor) element order, so the exact GPTQ/LDLQ recursion generalizes
to a NESTED sweep:
  - within each column j: a sequential ROW sweep with T's Cholesky
    feedback  (e~_r = (w_r - w^_r)/U_T[r,r];  w_{r'>r} -= e~_r U_T[r,r'])
  - across columns: E13's update with err replaced by
    f = (U_T^T e~) / U_S[j,j];  W[:, j+1:] -= f * U_S[j, j+1:].
Setting T=I collapses f to E13's err exactly — the input-only control is
the same code path with U_T=I (verified bit-identical in tests).

NOTE (negative result worth its line): a DIAGONAL-T row weighting is
provably a no-op for frozen-grid per-element rounding — each row's
objective is minimized independently and a per-row positive scale cannot
move its argmin. Off-diagonal T (per-row Cholesky feedback) is the only
way the output-side factor can change any code. Hence this file
implements the full Kronecker LDLQ, not a diag(T) reweighting.

Tensors with no T Gram (or blocked T) fall back to the input-only sweep
and are counted/reported. Output stays a byte-compatible Q2_K file.
"""
import argparse
import hashlib
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
    sweep may only feed FUTURE indices) — on PD failure escalate damping
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
    """E13 input-only sweep: rows vectorized, GPTQ column sweep with
    lazy-batch block updates (byte-identical to e13_reround.py)."""
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


def _row_sweep(w, dlj, mlj, Ut, rblk=128):
    """within-column row sweep under T's Cholesky feedback.
    w is modified in place; returns (q codes (m,), e~ = per-row scaled
    errors (m,)). Exact LDLQ in the row direction with lazy blocking."""
    m = w.shape[0]
    q_col = np.empty(m, dtype=np.uint8)
    et = np.zeros(m, dtype=np.float32)
    safe = np.where(dlj > 0, dlj, 1.0)
    for r0 in range(0, m, rblk):
        r1 = min(r0 + rblk, m)
        for r in range(r0, r1):
            qv = min(3.0, max(0.0, round(float((w[r] + mlj[r]) / safe[r]))))
            q_col[r] = int(qv)
            wq = dlj[r] * qv - mlj[r]
            utrr = Ut[r, r]
            if utrr == 0:
                continue
            e = (w[r] - wq) / utrr
            et[r] = e
            if r + 1 < r1:
                w[r + 1:r1] -= e * Ut[r, r + 1:r1]
        if r1 < m:
            w[r1:] -= et[r0:r1] @ Ut[r0:r1, r1:]
    return q_col, et


def reround_tensor_kron(Wref, dl, ml, Us_u, Ut_u, blk=128):
    """exact GPTQ/LDLQ under the Kronecker Hessian S (x) T:
    nested sweep — U_T row feedback inside each column, U_S column
    feedback across columns with f = (U_T^T e~)/U_S[j,j]."""
    W = Wref.astype(np.float32).copy()
    m, n = W.shape
    q_out = np.zeros((m, n), dtype=np.uint8)
    UtT = np.ascontiguousarray(Ut_u.T)
    for b0 in range(0, n, blk):
        b1 = min(b0 + blk, n)
        Err = np.zeros((m, b1 - b0), dtype=np.float32)
        for j in range(b0, b1):
            w = W[:, j]                      # in-place view
            q_col, et = _row_sweep(w, dl[:, j], ml[:, j], Ut_u)
            q_out[:, j] = q_col
            sjj = Us_u[j, j]
            if sjj == 0:
                continue
            f = (UtT @ et) / sjj
            Err[:, j - b0] = f
            if j + 1 < b1:
                W[:, j + 1:b1] -= f[:, None] * Us_u[j, j + 1:b1][None, :]
        if b1 < n:
            W[:, b1:] -= Err @ Us_u[b0:b1, b1:]
    return q_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("quant_gguf")
    ap.add_argument("gram_dir")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--t-gram-dir", default=None,
                    help="E24 YAQA-lite: output-gradient Gram dir "
                         "(gradgrams.json). When a tensor has a full T "
                         "Gram the sweep runs the exact Kronecker LDLQ; "
                         "otherwise it falls back to the input-only sweep "
                         "(counted and reported).")
    ap.add_argument("--t-damp", type=float, default=1e-3,
                    help="relative damping for T (E20 gate: >=1e-3 safe)")
    ap.add_argument("--codes-dir", default=None,
                    help="two-phase mode: compute+cache codes per tensor "
                         "(resume-safe), assemble only when all present")
    ap.add_argument("--max-tensors", type=int, default=0,
                    help="phase-A chunk size (0 = no limit)")
    args = ap.parse_args()

    man = json.loads((Path(args.gram_dir) / "grams.json").read_text())
    t_man = None
    if args.t_gram_dir:
        t_man = json.loads((Path(args.t_gram_dir) /
                            "gradgrams.json").read_text())

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
        """-> list of (col_offset, Hu) from the input Grams, or None."""
        blocks = s_blocks(name, n_in)
        if blocks is None:
            return None
        return [(off, gptq_hinv(S).astype(np.float32))
                for off, S in blocks]

    def ut_for(name, n_out):
        """upper U_T with inv(T) = U_T^T U_T, or None (fallback)."""
        if t_man is None or name not in t_man:
            return None
        meta = t_man[name]
        if meta["n"] != n_out or meta["blocks"] != 1:
            return None
        g = np.fromfile(Path(args.t_gram_dir) /
                        (name.replace("/", "_") + ".gradgram.bin"),
                        dtype=np.float32)
        T = g.reshape(n_out, n_out).astype(np.float64) / meta["count"]
        return gptq_hinv(T, damp=args.t_damp).astype(np.float32)

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
        # B8 repair: fingerprint the cache so a resumed run against a
        # different base/flag set fails loudly instead of silently
        # repacking stale codes onto a mismatched grid.
        fp = {"quant": hashlib.sha256(
                  Path(args.quant_gguf).read_bytes()).hexdigest()[:16],
              "gram_dir": str(Path(args.gram_dir).resolve()),
              "t_gram_dir": (str(Path(args.t_gram_dir).resolve())
                             if args.t_gram_dir else None),
              "t_damp": args.t_damp}
        mfile = codes / "meta.json"
        if mfile.exists():
            old = json.loads(mfile.read_text())
            if old != fp:
                sys.exit(f"[codes-dir] fingerprint mismatch vs {mfile}:\n"
                         f"  cached: {old}\n  now:    {fp}\n"
                         "refusing to mix code caches across bases/flags")
        else:
            mfile.write_text(json.dumps(fp, indent=1))

    n_re = 0
    n_new = 0
    n_kron = 0
    n_fallback = 0
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
            Ut = ut_for(t.name, n_out)
            mode = "kron" if Ut is not None else "input-only"
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
                tt = time.time()
                for off, U in Hu:
                    bsz = U.shape[0]
                    sl = slice(off, off + bsz)
                    if Ut is not None:
                        q[:, sl] = reround_tensor_kron(
                            Wref[:, sl], dl[:, sl], ml[:, sl], U, Ut)
                    else:
                        qb, _ = reround_tensor(Wref[:, sl], dl[:, sl],
                                               ml[:, sl], U)
                        q[:, sl] = qb
                n_new += 1
                print(f"[{mode}] {t.name} ({n_out}x{n_in}) "
                      f"{time.time()-tt:.1f}s", flush=True)
                if cfile:
                    np.save(cfile, q)
            n_kron += mode == "kron"
            n_fallback += mode == "input-only"
            _, _, blocks = q2k_decode(np.asarray(t.data).reshape(-1),
                                      n_out, n_in)
            nb2 = q2k_pack_codes(blocks, q.reshape(-1))
            w.add_tensor(t.name, nb2.reshape(np.asarray(t.data).shape),
                         raw_dtype=gguf.GGMLQuantizationType.Q2_K)
            n_re += 1
        else:
            w.add_tensor(t.name, t.data, raw_dtype=t.tensor_type)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file(progress=False)
    w.close()
    print(f"wrote {args.out}: {n_re} tensors re-rounded "
          f"({n_kron} Kronecker, {n_fallback} input-only fallback), "
          f"{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
