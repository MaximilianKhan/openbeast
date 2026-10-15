#!/usr/bin/env python3
"""E36-E gates for e36_freegrid.py (CPU only).

gate 1 (identity): for every covered Q2_K tensor, ggml_quantize_chunk on
  the reference weights with the llama-quantize-normalised imatrix must
  reproduce the base file's bytes (a) on whole rows and (b) one
  256-column superblock slice at a time; and (c) for the selected
  tensors, the free-grid sweep with feedback disabled must too.
gate 2 (objective): for the selected tensors, tr(E S Eᵀ) for bare
  llama-quantize, E13 frozen-grid re-round, and free-grid GPTQ.
"""
import argparse
import time

import numpy as np

import e36_freegrid as fg
from e36_freegrid import QK_K, gguf
from e13_reround import q2k_decode, reround_tensor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("quant_gguf")
    ap.add_argument("gram_dir")
    ap.add_argument("--imatrix", required=True)
    ap.add_argument("--select", default="", help="comma list of tensors")
    args = ap.parse_args()

    grams = fg.Grams(args.gram_dir)
    imx = fg.load_imatrix_llq(args.imatrix)
    ref, _ = fg.index_2d(args.ref_gguf)
    rdr = fg.GGUFReader(args.quant_gguf)
    cov = []
    for t in rdr.tensors:
        shape = [int(d) for d in t.shape if int(d) > 1]
        if (t.tensor_type == gguf.GGMLQuantizationType.Q2_K
                and len(shape) == 2 and t.name in ref
                and grams.S_blocks(t.name, shape[0]) is not None):
            cov.append((t, shape[1], shape[0]))
    print(f"covered Q2_K tensors: {len(cov)}")

    # ---- gate 1a/1b on every covered tensor
    n_a = n_b = 0
    bad = []
    t0 = time.time()
    for t, n_out, n_in in cov:
        raw = np.asarray(t.data).reshape(-1, 84)
        W = fg.deq(ref[t.name])
        im = imx[t.name]
        a = fg.quantize_q2k(W, im)
        ok_a = np.array_equal(a, raw)
        b = np.empty((n_out, n_in // QK_K, 84), np.uint8)
        for sb in range(n_in // QK_K):
            sl = slice(sb * QK_K, (sb + 1) * QK_K)
            b[:, sb] = fg.quantize_q2k(W[:, sl], im[sl])
        ok_b = np.array_equal(b.reshape(-1, 84), raw)
        n_a += ok_a
        n_b += ok_b
        if not (ok_a and ok_b):
            bad.append((t.name, ok_a, ok_b,
                        int((a != raw).any(1).sum()), raw.shape[0]))
    print(f"gate1a whole-row  bytes == base: {n_a}/{len(cov)}")
    print(f"gate1b per-superblock bytes == base: {n_b}/{len(cov)}"
          f"   ({time.time()-t0:.0f}s)")
    for x in bad[:20]:
        print("  MISMATCH", x)

    sel = [s for s in args.select.split(",") if s]
    if sel == ["all"]:
        sel = [t.name for t, _, _ in cov]
    by = {t.name: (t, o, i) for t, o, i in cov}
    sums = np.zeros(3)
    wins = [0, 0]
    print("\ngate1c (free-grid sweep, feedback off) and gate2 (tr(E S E^T))")
    print(f"{'tensor':28s} {'shape':>11s} {'1c':>4s} "
          f"{'bare':>11s} {'e13 frozen':>11s} {'free-grid':>11s} "
          f"{'e13/bare':>8s} {'free/bare':>9s} {'free/e13':>8s} "
          f"{'t_e13':>6s} {'t_free':>6s}")
    for name in sel:
        t, n_out, n_in = by[name]
        raw = np.asarray(t.data).reshape(-1)
        W = fg.deq(ref[name])
        S = grams.S_blocks(name, n_in)
        Hu = grams.Hu_blocks(name, n_in)
        im = imx[name]
        nf, _ = fg.freegrid_tensor(W, im, Hu, feedback=False)
        ok_c = np.array_equal(nf.reshape(-1), raw)
        Wb = fg.frozen_wq(raw, n_out, n_in)
        e_bare = fg.whitened_err(W, Wb, S)
        ts = time.time()
        dl, ml, _ = q2k_decode(raw, n_out, n_in)
        We = np.empty_like(W)
        for off, U in Hu:
            sl = slice(off, off + U.shape[0])
            _, We[:, sl] = reround_tensor(W[:, sl], dl[:, sl], ml[:, sl], U)
        t_e13 = time.time() - ts
        e_e13 = fg.whitened_err(W, We, S)
        ts = time.time()
        fr, Wf = fg.freegrid_tensor(W, im, Hu, feedback=True)
        t_fr = time.time() - ts
        e_fr = fg.whitened_err(W, Wf, S)
        # the packed bytes must decode to the Wq the sweep reported
        assert np.allclose(fg.frozen_wq(fr.reshape(-1), n_out, n_in), Wf,
                           rtol=0, atol=0)
        print(f"{name:28s} {n_out:>5d}x{n_in:<5d} {'OK' if ok_c else 'FAIL':>4s} "
              f"{e_bare:11.4e} {e_e13:11.4e} {e_fr:11.4e} "
              f"{e_e13/e_bare:8.3f} {e_fr/e_bare:9.3f} {e_fr/e_e13:8.3f} "
              f"{t_e13:6.1f} {t_fr:6.1f}", flush=True)
        sums += (e_bare, e_e13, e_fr)
        wins[0] += e_fr <= e_e13
        wins[1] += 1
    if sel:
        print(f"\nsum over {len(sel)} tensors: bare {sums[0]:.4e}  "
              f"e13 {sums[1]:.4e} ({sums[1]/sums[0]:.3f})  free-grid "
              f"{sums[2]:.4e} ({sums[2]/sums[0]:.3f}); free/e13 "
              f"{sums[2]/sums[1]:.3f}; free <= e13 on {wins[0]}/{wins[1]}")


if __name__ == "__main__":
    main()
