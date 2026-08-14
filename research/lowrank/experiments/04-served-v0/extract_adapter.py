#!/usr/bin/env python3
"""E04: extract activation-whitened low-rank residual-correction factors and
export them as a LoRA-form GGUF servable by stock llama.cpp (--lora).

Math (prior-art/math-methods.md Box 3, QERA closed form, diagonal S):
  R = W_ref - W_quant                (n_out, n_in)
  D = diag(sqrt(E[x_j^2]))          from llama-imatrix
  B = U_r  = top-r eigenvectors of G = (R D)(R D)^T     (n_out, r)
  A = B^T R                                            (r, n_in)
  => B A is the rank-r minimizer of ||(R - BA) D||_F  (D cancels: the
     optimal column space is whitened, the coefficients are unwhitened).

Conventions (prior-art/gguf-tooling.md, live-verified):
  tensors <base>.lora_a (rank, n_in) + <base>.lora_b (n_out, rank), PEFT
  layout untransposed; skip token_embd (tied) and anything not in the
  imatrix; runtime dW = (alpha/rank)*B*A -> uniform rank uses alpha=rank,
  mixed rank uses alpha=1 and folds rank_t into A.
"""
import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf  # noqa: E402
from gguf import GGUFReader  # noqa: E402
from gguf.quants import dequantize  # noqa: E402

KIND_PATTERNS = [
    (r"attn_q\.", "attn_q"), (r"attn_k\.", "attn_k"),
    (r"attn_v\.", "attn_v"), (r"attn_output\.", "attn_o"),
    (r"ffn_gate\.", "ffn_gate"), (r"ffn_up\.", "ffn_up"),
    (r"ffn_down\.", "ffn_down"),
]


def kind_of(name):
    for pat, kind in KIND_PATTERNS:
        if re.search(pat, name):
            return kind
    return "other"


def index_2d(path, min_dim=64):
    """Map name -> reader tensor for 2D tensors; dequantize lazily (a 27B
    model fully dequantized to fp32 is ~100 GB — stream one at a time)."""
    out = {}
    r = GGUFReader(path)
    arch = r.get_field("general.architecture").contents()
    for t in r.tensors:
        shape = [int(d) for d in t.shape if int(d) > 1]
        if len(shape) == 2 and min(shape) >= min_dim:
            out[t.name] = t
    return out, arch


def deq(t):
    shape = [int(d) for d in t.shape if int(d) > 1]
    w = dequantize(t.data, t.tensor_type).astype(np.float32)
    return w.reshape(shape[::-1]) if w.ndim == 1 else w    # (n_out, n_in)


def load_imatrix(path):
    sums, counts = {}, {}
    for t in GGUFReader(path).tensors:
        data = np.asarray(t.data, dtype=np.float64).ravel()
        if t.name.endswith(".in_sum2"):
            sums[t.name[: -len(".in_sum2")]] = data
        elif t.name.endswith(".counts"):
            counts[t.name[: -len(".counts")]] = float(data.mean())
    return {k: v / counts.get(k, 1.0) for k, v in sums.items()}


def parse_rank_map(s):
    return {k: int(v) for k, v in
            (item.split("=") for item in s.split(","))}


# E10: full-covariance whitening from a Gram capture directory
# (LLAMA_IMATRIX_GRAM_DIR patch). One Gram per distinct layer input:
# q/k/v share attn_k's, gate/up share ffn_up's.
_GRAM_SOURCE = [(r"attn_q\.|attn_v\.", lambda n: re.sub(
                    r"attn_[qv]\.", "attn_k.", n)),
                (r"ffn_gate\.", lambda n: n.replace("ffn_gate.", "ffn_up.")),
                (r"attn_gate\.", lambda n: n.replace("attn_gate.",
                                                     "attn_qkv."))]


def load_grams(gram_dir):
    import json
    man = json.loads((Path(gram_dir) / "grams.json").read_text())
    return man


def gram_whitener(gram_dir, man, name, n_in, damp=0.01):
    """-> list of (offset, L) Cholesky factors covering [0, n_in), or None."""
    src = name
    for pat, fn in _GRAM_SOURCE:
        if re.search(pat, name):
            src = fn(name)
            break
    if src not in man or man[src]["n"] != n_in:
        return None
    m = man[src]
    fn = Path(gram_dir) / (src.replace("/", "_") + ".gram.bin")
    g = np.fromfile(fn, dtype=np.float32)
    bs = n_in // m["blocks"]
    out = []
    for b in range(m["blocks"]):
        S = g[b * bs * bs:(b + 1) * bs * bs].reshape(bs, bs).astype(
            np.float64) / m["count"]
        S = 0.5 * (S + S.T)      # accumulation order leaves tiny asymmetry
        S[np.diag_indices(bs)] += damp * float(np.mean(np.diag(S)))
        try:
            L = np.linalg.cholesky(S)
        except np.linalg.LinAlgError:
            ev, evec = np.linalg.eigh(S)
            ev = np.maximum(ev, 1e-10 * ev.max())
            L = evec * np.sqrt(ev)[None, :]   # S = L L^T up to clip
        out.append((b * bs, L.astype(np.float32)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("quant_gguf")
    ap.add_argument("imatrix_gguf")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--rank", type=int, default=64,
                    help="uniform rank (ignored if --rank-map given)")
    ap.add_argument("--rank-map", default=None,
                    help="per-kind ranks, e.g. attn_q=64,ffn_down=16 "
                         "(kinds omitted fall back to --rank; 0 = skip)")
    ap.add_argument("--f32", action="store_true",
                    help="store factors in F32 (default F16)")
    ap.add_argument("--compute32", action="store_true",
                    help="run the SVD in float32 (for 9B+ scale; top-r "
                         "directions are insensitive to this per "
                         "math-methods §5.1)")
    ap.add_argument("--rsvd", action="store_true",
                    help="randomized SVD (rank+32 oversampling, 4 power "
                         "iterations) — required at 27B scale")
    ap.add_argument("--gram-dir", default=None,
                    help="E10: whiten with full/blocked activation "
                         "covariance from a Gram capture dir instead of "
                         "the imatrix diagonal")
    ap.add_argument("--grad-gram-dir", default=None,
                    help="M3: additionally whiten the OUTPUT side with "
                         "gradient Grams (two-sided Kronecker-Fisher; "
                         "requires --gram-dir; full grams only)")
    ap.add_argument("--q8-factors", action="store_true",
                    help="store A/B as Q8_0 (~half the bytes of F16; "
                         "requires rank %% 32 == 0; LoRC: <=0.02 ppl cost)")
    ap.add_argument("--columns", action="store_true",
                    help="E08 mode: instead of SVD directions, patch the "
                         "top-r WHITENED-ENERGY input columns exactly "
                         "(B = R[:,cols], A = one-hot) — targets outlier "
                         "channels; same serving cost as rank r")
    args = ap.parse_args()
    rank_map = parse_rank_map(args.rank_map) if args.rank_map else {}

    ref, arch = index_2d(args.ref_gguf)
    qnt, _ = index_2d(args.quant_gguf)
    imat = load_imatrix(args.imatrix_gguf)
    gram_man = load_grams(args.gram_dir) if args.gram_dir else None
    gg_man = None
    if args.grad_gram_dir:
        import json as _json
        gg_man = _json.loads((Path(args.grad_gram_dir) /
                              "gradgrams.json").read_text())
    n_whitened_full = 0

    ranks_used = set()
    factors = {}   # name -> (A, B, rank, captured)
    t_all = time.time()
    for name, t_ref in ref.items():
        if name not in qnt:
            continue
        w_ref = deq(t_ref)
        m2 = imat.get(name)
        n_out, n_in = w_ref.shape
        if m2 is None or len(m2) != n_in:
            print(f"skip (no imatrix): {name}")
            continue
        r_want = rank_map.get(kind_of(name), args.rank)
        if r_want <= 0:
            continue
        r_eff = min(r_want, n_out, n_in)
        cdtype = np.float32 if args.compute32 else np.float64
        R = (w_ref - deq(qnt[name])).astype(cdtype)
        del w_ref
        wh = gram_whitener(args.gram_dir, gram_man, name, n_in) \
            if gram_man else None
        if wh is not None:
            Rd = np.empty_like(R)
            for off, L in wh:
                bs = L.shape[0]
                Rd[:, off:off + bs] = R[:, off:off + bs] @ L.astype(cdtype)
        else:
            d = np.sqrt(np.maximum(m2, 1e-8 * m2.max())).astype(cdtype)
            Rd = R * d[None, :]
        # M3 two-sided: left-whiten by chol(T), and the projection form no
        # longer cancels -> explicit unwhitened factors below
        Lg = None
        if args.grad_gram_dir and gg_man is not None and wh is not None:
            key = name
            if key in gg_man and gg_man[key]["blocks"] == 1 \
                    and gg_man[key]["n"] == n_out:
                g = np.fromfile(Path(args.grad_gram_dir) /
                                (key.replace("/", "_") + ".gradgram.bin"),
                                dtype=np.float32)
                T = g.reshape(n_out, n_out).astype(np.float64)
                T /= gg_man[key]["count"]
                T = 0.5 * (T + T.T)
                T[np.diag_indices(n_out)] += 1e-3 * float(
                    np.mean(np.diag(T)))
                Lg = np.linalg.cholesky(T)
                Rd = (Lg.T.astype(cdtype) @ Rd)
        if args.columns:
            # exact patch of the r loudest whitened input columns:
            # dW = B·(A/r)·r with A one-hot ⇒ exactly restores R[:,cols]
            col_e = (Rd ** 2).sum(axis=0)
            cols = np.argsort(-col_e)[:r_eff]
            B = R[:, cols]                          # (n_out, r)
            A = np.zeros((r_eff, R.shape[1]), dtype=np.float32)
            A[np.arange(r_eff), cols] = 1.0
            captured = float(col_e[cols].sum() / max(col_e.sum(), 1e-30))
            factors[name] = (A, B, r_eff, captured)
            ranks_used.add(r_eff)
            print(f"{name:44s} r={r_eff:<4d} col-patch whitened-energy "
                  f"{captured:.2f}")
            continue
        if Lg is not None:
            # M = Lg^T R Li ; BA = Lg^{-T} [M]_r Li^{-1}
            U, s, Vt = np.linalg.svd(Rd, full_matrices=False)
            B = np.linalg.solve(Lg.T, U[:, :r_eff]).astype(np.float64)
            SV = (s[:r_eff, None] * Vt[:r_eff]).astype(np.float64)
            A = np.empty((r_eff, n_in), dtype=np.float64)
            for off, L in wh:
                bs = L.shape[0]
                A[:, off:off + bs] = np.linalg.solve(
                    L.astype(np.float64).T, SV[:, off:off + bs].T).T
            # balance per-component magnitudes (product unchanged) so
            # inverse-whitening amplification cannot overflow F16
            bn = np.linalg.norm(B, axis=0)
            an = np.linalg.norm(A, axis=1)
            eta = np.sqrt(np.maximum(bn, 1e-30) / np.maximum(an, 1e-30))
            B = B / eta[None, :]
            A = A * eta[:, None]
            e = s.astype(np.float64) ** 2
            captured = float(e[:r_eff].sum() / max(e.sum(), 1e-30))
            factors[name] = (A, B, r_eff, captured)
            ranks_used.add(r_eff)
            print(f"{name:44s} r={r_eff:<4d} 2side-whitened captured "
                  f"{captured:.2f} maxB={float(np.abs(B).max()):.1f}")
            continue
        if args.rsvd:
            # randomized range finder w/ power iteration (Halko et al.):
            # exact SVD of 5120x25600-class tensors is the 27B wall-clock
            # wall. Whitened residual spectra are steep enough for q=4
            # (math-methods recommends more for FLAT spectra; ours aren't
            # after whitening, and captured-energy is re-checked exactly).
            rng = np.random.default_rng(0xBEA57)
            k = min(r_eff + 32, min(R.shape))
            Y = Rd @ rng.standard_normal((R.shape[1], k), dtype=np.float32
                                         ).astype(cdtype)
            for _ in range(4):
                Y = Rd @ (Rd.T @ Y)
                Y, _ = np.linalg.qr(Y)
            Q, _ = np.linalg.qr(Y)
            Ub, s, _ = np.linalg.svd(Q.T @ Rd, full_matrices=False)
            U = Q @ Ub
            # captured fraction vs TOTAL energy needs the full spectrum;
            # use ||Rd||_F^2 for the denominator (exact, cheap)
            e_tot = float((Rd ** 2).sum())
        else:
            # economy SVD of Rd: U = eigvecs of Rd·Rdᵀ (fine at 0.6B scale)
            U, s, _ = np.linalg.svd(Rd, full_matrices=False)
            e_tot = float((s.astype(np.float64) ** 2).sum())
        B = U[:, :r_eff]                    # (n_out, r) top left-vecs
        A = B.T @ R                         # (r, n_in)
        e = s.astype(np.float64) ** 2
        captured = float(e[:r_eff].sum() / max(e_tot, 1e-30))
        factors[name] = (A, B, r_eff, captured)
        ranks_used.add(r_eff)
        print(f"{name:44s} r={r_eff:<4d} whitened-energy captured "
              f"{captured:.2f}")

    uniform = len(ranks_used) == 1
    alpha = float(next(iter(ranks_used))) if uniform else 1.0
    dtype = np.float32 if args.f32 else np.float16

    w = gguf.GGUFWriter(args.out, arch)
    w.add_type(gguf.GGUFType.ADAPTER)
    w.add_string(gguf.Keys.Adapter.TYPE, "lora")
    w.add_float32(gguf.Keys.Adapter.LORA_ALPHA, alpha)
    for name, (A, B, r_eff, _) in factors.items():
        # runtime applies alpha/rank_t: uniform -> alpha=rank (scale 1);
        # mixed -> alpha=1, fold rank_t into A so (1/r_t)*B*(A*r_t) = B*A
        A_out = A if uniform else A * r_eff
        if args.q8_factors:
            from gguf.quants import quantize as gquant
            qt = gguf.GGMLQuantizationType.Q8_0
            w.add_tensor(name + ".lora_a",
                         gquant(A_out.astype(np.float32), qt), raw_dtype=qt)
            w.add_tensor(name + ".lora_b",
                         gquant(B.astype(np.float32), qt), raw_dtype=qt)
        else:
            w.add_tensor(name + ".lora_a", A_out.astype(dtype))
            w.add_tensor(name + ".lora_b", B.astype(dtype))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file(progress=False)
    w.close()

    size = Path(args.out).stat().st_size
    total_params = sum(B.shape[0] * A.shape[1]
                       for A, B, *_ in factors.values())
    print(f"\nwrote {args.out}: {len(factors)} corrected tensors, "
          f"alpha={alpha} ({'uniform' if uniform else 'mixed-rank folded'}), "
          f"{size/1e6:.1f} MB "
          f"(+{size*8/total_params:.2f} bpw over corrected tensors), "
          f"{time.time()-t_all:.0f}s")
    print(f"mean whitened-energy captured: "
          f"{np.mean([c for *_, c in factors.values()]):.2f}")


if __name__ == "__main__":
    main()
