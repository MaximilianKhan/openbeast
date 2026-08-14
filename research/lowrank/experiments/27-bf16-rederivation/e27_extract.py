#!/usr/bin/env python3
"""E27: resume-safe wrapper around the E04/E10 extractor (extract_adapter).

Same math and conventions as ../04-served-v0/extract_adapter.py (full-cov
Gram whitening, rsvd, Q8 factors), plus the E13 --codes-dir survival
pattern: every tensor's factors are cached to --cache-dir as .npz the
moment they are computed, and a --time-budget makes the process exit 3
(RESUME) before the background reaper can matter. Re-invoke until it
writes the GGUF (exit 0). The final GGUF is byte-equivalent to a single
uninterrupted run: the per-tensor computation is deterministic (fixed
rsvd seed) and order-independent.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "04-served-v0"))
import extract_adapter as ex                      # noqa: E402

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf                                       # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("quant_gguf")
    ap.add_argument("imatrix_gguf")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--rank", type=int, default=128)
    ap.add_argument("--rank-map", default=None)
    ap.add_argument("--gram-dir", default=None)
    ap.add_argument("--q8-factors", action="store_true")
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--time-budget", type=float, default=480.0,
                    help="seconds of compute before exiting 3 for resume")
    args = ap.parse_args()
    rank_map = ex.parse_rank_map(args.rank_map) if args.rank_map else {}
    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    ref, arch = ex.index_2d(args.ref_gguf)
    qnt, _ = ex.index_2d(args.quant_gguf)
    imat = ex.load_imatrix(args.imatrix_gguf)
    gram_man = ex.load_grams(args.gram_dir) if args.gram_dir else None

    t0 = time.time()
    factors = {}          # name -> (A, B, r_eff, captured)
    todo = 0
    for name, t_ref in ref.items():
        if name not in qnt:
            continue
        shape = [int(d) for d in t_ref.shape if int(d) > 1]
        n_in, n_out = shape[0], shape[1]
        m2 = imat.get(name)
        if m2 is None or len(m2) != n_in:
            continue                      # no imatrix entries (MTP block)
        r_want = rank_map.get(ex.kind_of(name), args.rank)
        if r_want <= 0:
            continue                      # kind skipped (FFN in E27)
        r_eff = min(r_want, n_out, n_in)
        cfile = cache / (name + ".npz")
        if cfile.exists():
            z = np.load(cfile)
            factors[name] = (z["A"], z["B"], int(z["r"]), float(z["cap"]))
            continue
        if time.time() - t0 > args.time_budget:
            todo += 1
            continue
        # compute (fc whitening + rsvd, float32, seed-fixed — E10 path)
        cdtype = np.float32
        R = (ex.deq(t_ref) - ex.deq(qnt[name])).astype(cdtype)
        wh = ex.gram_whitener(args.gram_dir, gram_man, name, n_in) \
            if gram_man else None
        if wh is not None:
            Rd = np.empty_like(R)
            for off, L in wh:
                bs = L.shape[0]
                Rd[:, off:off + bs] = R[:, off:off + bs] @ L.astype(cdtype)
            mode = "fc"
        else:
            d = np.sqrt(np.maximum(m2, 1e-8 * m2.max())).astype(cdtype)
            Rd = R * d[None, :]
            mode = "diag"
        rng = np.random.default_rng(0xBEA57)
        k = min(r_eff + 32, min(R.shape))
        Y = Rd @ rng.standard_normal((R.shape[1], k),
                                     dtype=np.float32).astype(cdtype)
        for _ in range(4):
            Y = Rd @ (Rd.T @ Y)
            Y, _ = np.linalg.qr(Y)
        Q, _ = np.linalg.qr(Y)
        Ub, s, _ = np.linalg.svd(Q.T @ Rd, full_matrices=False)
        U = Q @ Ub
        e_tot = float((Rd ** 2).sum())
        B = U[:, :r_eff]
        A = B.T @ R
        cap = float((s[:r_eff].astype(np.float64) ** 2).sum()
                    / max(e_tot, 1e-30))
        np.savez(cfile, A=A.astype(np.float32), B=B.astype(np.float32),
                 r=r_eff, cap=cap)
        factors[name] = (A, B, r_eff, cap)
        print(f"{name:44s} r={r_eff:<4d} {mode} captured {cap:.2f}",
              flush=True)

    if todo:
        print(f"RESUME: {todo} tensors remaining "
              f"({len(factors)} cached), re-invoke")
        sys.exit(3)

    ranks_used = {r for *_, r, _ in factors.values()}
    uniform = len(ranks_used) == 1
    alpha = float(next(iter(ranks_used))) if uniform else 1.0
    w = gguf.GGUFWriter(args.out, arch)
    w.add_type(gguf.GGUFType.ADAPTER)
    w.add_string(gguf.Keys.Adapter.TYPE, "lora")
    w.add_float32(gguf.Keys.Adapter.LORA_ALPHA, alpha)
    for name, (A, B, r_eff, _) in factors.items():
        A_out = A if uniform else A * r_eff
        if args.q8_factors:
            from gguf.quants import quantize as gquant
            qt = gguf.GGMLQuantizationType.Q8_0
            w.add_tensor(name + ".lora_a",
                         gquant(A_out.astype(np.float32), qt), raw_dtype=qt)
            w.add_tensor(name + ".lora_b",
                         gquant(B.astype(np.float32), qt), raw_dtype=qt)
        else:
            w.add_tensor(name + ".lora_a", A_out.astype(np.float16))
            w.add_tensor(name + ".lora_b", B.astype(np.float16))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file(progress=False)
    w.close()
    size = Path(args.out).stat().st_size
    print(f"wrote {args.out}: {len(factors)} corrected tensors, "
          f"alpha={alpha}, {size/1e6:.1f} MB")
    print(f"mean whitened-energy captured: "
          f"{np.mean([c for *_, c in factors.values()]):.2f}")


if __name__ == "__main__":
    main()
