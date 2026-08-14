#!/usr/bin/env python3
"""E07 v2 pass 1: cache whitened top-K spectra + factor bases to disk so
allocation policies can be emitted/re-emitted in seconds (the v1 monolith
died when the energy-greedy policy proved wrong mid-flight — never bind
an expensive computation to a policy choice again).

Writes per-tensor npz: U (n_out,K) f16, Afull (K,n_in) f16, s2 (K,) f64.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "04-served-v0"))
from extract_adapter import deq, index_2d, kind_of, load_imatrix  # noqa: E402


def rsvd_topk(Rd, k, iters=3, p=16, seed=0xBEA57):
    rng = np.random.default_rng(seed)
    k = min(k + p, min(Rd.shape))
    Y = Rd @ rng.standard_normal((Rd.shape[1], k), dtype=np.float32)
    for _ in range(iters):
        Y = Rd @ (Rd.T @ Y)
        Y, _ = np.linalg.qr(Y)
    Q, _ = np.linalg.qr(Y)
    Ub, s, _ = np.linalg.svd(Q.T @ Rd, full_matrices=False)
    return (Q @ Ub)[:, :k - p], s[:k - p]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("quant_gguf")
    ap.add_argument("imatrix_gguf")
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--kmax", type=int, default=192)
    args = ap.parse_args()
    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    ref, arch = index_2d(args.ref_gguf)
    qnt, _ = index_2d(args.quant_gguf)
    imat = load_imatrix(args.imatrix_gguf)
    meta = {"arch": arch, "tensors": {}}
    t0 = time.time()
    n_done = 0
    for name, t_ref in ref.items():
        if name not in qnt:
            continue
        w_ref = deq(t_ref)
        n_out, n_in = w_ref.shape
        m2 = imat.get(name)
        if m2 is None or len(m2) != n_in:
            continue
        fn = cache / (name.replace("/", "_") + ".npz")
        if fn.exists():
            n_done += 1
            meta["tensors"][name] = {"kind": kind_of(name),
                                     "m": n_out, "n": n_in,
                                     "file": fn.name}
            continue
        R = w_ref - deq(qnt[name])
        del w_ref
        d = np.sqrt(np.maximum(m2, 1e-8 * m2.max())).astype(np.float32)
        U, s = rsvd_topk((R * d[None, :]).astype(np.float32), args.kmax)
        np.savez(fn, U=U.astype(np.float16),
                 Afull=(U.T @ R).astype(np.float16),
                 s2=s.astype(np.float64) ** 2)
        del R
        meta["tensors"][name] = {"kind": kind_of(name), "m": n_out,
                                 "n": n_in, "file": fn.name}
        n_done += 1
        if n_done % 25 == 0:
            print(f"[cache] {n_done} tensors, {time.time()-t0:.0f}s",
                  flush=True)
            # incremental index: a partial cache must stay emittable
            (cache / "meta.json").write_text(json.dumps(meta))
    (cache / "meta.json").write_text(json.dumps(meta))
    print(f"[cache] DONE: {n_done} tensors, {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
