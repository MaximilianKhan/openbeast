#!/usr/bin/env python3
"""E07: rank-allocated correction adapter via global greedy water-filling.

Pass 1: per tensor, top-K whitened spectrum + basis (rsvd), A_full = UᵀR.
Pass 2: global greedy — spend bytes on the (tensor, next-rank-step) with
the best marginal whitened-energy per byte until the budget is gone.
Pass 3: emit ONE adapter per requested budget (mixed ranks, alpha=1 with
rank folded into A — the convention validated on 0.6B, PPL 31.28 vs
31.29 uniform).

Objective note (paper §Method): greedy on whitened eigenvalues optimizes
total activation-weighted squared error across tensors; it ignores depth
compounding (early-layer errors amplify). First-order allocation only.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "04-served-v0"))
from extract_adapter import (  # noqa: E402
    REPO, deq, index_2d, kind_of, load_imatrix)

sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf  # noqa: E402


def rsvd_topk(Rd, k, iters=4, seed=0xBEA57):
    rng = np.random.default_rng(seed)
    k = min(k, min(Rd.shape))
    Y = Rd @ rng.standard_normal((Rd.shape[1], k), dtype=np.float32)
    for _ in range(iters):
        Y = Rd @ (Rd.T @ Y)
        Y, _ = np.linalg.qr(Y)
    Q, _ = np.linalg.qr(Y)
    Ub, s, _ = np.linalg.svd(Q.T @ Rd, full_matrices=False)
    return Q @ Ub, s  # U (n_out,k), singular values (k,)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("quant_gguf")
    ap.add_argument("imatrix_gguf")
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--budgets-mb", default="870,2640",
                    help="comma list; one adapter per budget")
    ap.add_argument("--kmax", type=int, default=256)
    ap.add_argument("--min-rank", type=int, default=8,
                    help="tensors allocated below this get 0 (skip)")
    args = ap.parse_args()
    budgets = [float(b) * 1e6 for b in args.budgets_mb.split(",")]

    ref, arch = index_2d(args.ref_gguf)
    qnt, _ = index_2d(args.quant_gguf)
    imat = load_imatrix(args.imatrix_gguf)

    store = {}   # name -> dict(U, Afull, s2, m, n)
    t0 = time.time()
    for i, (name, t_ref) in enumerate(ref.items()):
        if name not in qnt:
            continue
        w_ref = deq(t_ref)
        n_out, n_in = w_ref.shape
        m2 = imat.get(name)
        if m2 is None or len(m2) != n_in:
            continue
        R = w_ref - deq(qnt[name])
        del w_ref
        d = np.sqrt(np.maximum(m2, 1e-8 * m2.max())).astype(np.float32)
        U, s = rsvd_topk(R * d[None, :], args.kmax)
        store[name] = dict(U=U.astype(np.float32),
                           Afull=(U.T @ R).astype(np.float32),
                           s2=(s.astype(np.float64) ** 2),
                           m=n_out, n=n_in)
        del R
        if i % 50 == 0:
            print(f"[pass1] {i} tensors, {time.time()-t0:.0f}s", flush=True)
    print(f"[pass1] done: {len(store)} tensors, {time.time()-t0:.0f}s",
          flush=True)

    # ---- pass 2: global greedy per budget --------------------------------
    # marginal gain of rank step i on tensor t = s2[i]; cost = 2*(m+n) bytes
    # (F16 A row + B column). Vectorized: sort ALL (gain/cost) steps once.
    names = list(store)
    gains, owner, step = [], [], []
    for ti, nm in enumerate(names):
        st = store[nm]
        cost = 2.0 * (st["m"] + st["n"])
        gains.append(st["s2"] / cost)
        owner.append(np.full(len(st["s2"]), ti, dtype=np.int32))
        step.append(np.arange(len(st["s2"]), dtype=np.int32))
    gains = np.concatenate(gains)
    owner = np.concatenate(owner)
    order = np.argsort(-gains)

    for budget in budgets:
        ranks = {nm: 0 for nm in names}
        spend = 0.0
        for idx in order:
            nm = names[owner[idx]]
            st = store[nm]
            c = 2.0 * (st["m"] + st["n"])
            if spend + c > budget:
                continue
            ranks[nm] += 1          # steps arrive in spectral order per
            spend += c              # tensor since s2 is sorted descending
        ranks = {nm: r for nm, r in ranks.items() if r >= args.min_rank}
        out = f"{args.out_prefix}-b{int(budget/1e6)}mb.gguf"
        w = gguf.GGUFWriter(out, arch)
        w.add_type(gguf.GGUFType.ADAPTER)
        w.add_string(gguf.Keys.Adapter.TYPE, "lora")
        w.add_float32(gguf.Keys.Adapter.LORA_ALPHA, 1.0)
        cap_tot = cap_got = 0.0
        for nm, r in ranks.items():
            st = store[nm]
            A = st["Afull"][:r] * r          # fold rank_t (alpha=1)
            B = st["U"][:, :r]
            w.add_tensor(nm + ".lora_a", A.astype(np.float16))
            w.add_tensor(nm + ".lora_b", B.astype(np.float16))
            cap_tot += st["s2"].sum()
            cap_got += st["s2"][:r].sum()
        w.write_header_to_file()
        w.write_kv_data_to_file()
        w.write_tensors_to_file(progress=False)
        w.close()
        rk = sorted(ranks.values())
        by_kind = {}
        for nm, r in ranks.items():
            by_kind.setdefault(kind_of(nm), []).append(r)
        print(f"\n== {out}: {Path(out).stat().st_size/1e6:.0f} MB, "
              f"{len(ranks)}/{len(names)} tensors, ranks "
              f"min/med/max {rk[0]}/{rk[len(rk)//2]}/{rk[-1]}, "
              f"whitened capture (allocated tensors) {cap_got/cap_tot:.2f}")
        for k in sorted(by_kind):
            rs = by_kind[k]
            print(f"   {k:10s} n={len(rs):3d} mean_r={np.mean(rs):6.1f} "
                  f"max={max(rs)}")


if __name__ == "__main__":
    main()
