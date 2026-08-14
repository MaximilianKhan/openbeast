#!/usr/bin/env python3
"""E29 part 1 / recon L3: SRR preserve-then-quantize split — proxy test.

SRR (2602.02001) carves the top-k subspace out of W BEFORE quantizing
and keeps it as a high-precision low-rank branch. Its win mechanism
requires that the carve-out (a) holds real energy and/or (b) shrinks
the quantizer's per-block dynamic range. Both are exactly computable
from BF16 + Grams, no quantizer needed. Part 2 (full deflated-base
build + measure100) runs ONLY if this proxy passes its gate.

Per tensor: M = W·L (whitened). Carve C_k = (M V_k^T V_k)·L^-1.
Report: whitened capture of top-k of W; per-16-block absmax ratio
absmax(W_def)/absmax(W) (Q2_K scale proxy), for whitened-metric and
raw-SVD carves, k in {64, 128, 256}.
"""
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "04-served-v0"))
import extract_adapter as ex                      # noqa: E402

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
from gguf.quants import dequantize                # noqa: E402
import gguf                                       # noqa: E402

BF16 = REPO / "weights/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-BF16.gguf"
GRAMDIR = REPO / "research/lowrank/data/gram27b-bf16"
OUT = HERE / "results.txt"
KS = (64, 128, 256)
SAMPLE = [("attn_q", (3, 31, 63)), ("attn_qkv", (0, 26, 58)),
          ("ffn_up", (1, 30, 62)), ("ssm_out", (2, 29, 61))]


def rsvd_rows(M, r, seed=7):
    rng = np.random.default_rng(seed)
    k = min(r + 32, min(M.shape))
    Y = M @ rng.standard_normal((M.shape[1], k)).astype(np.float32)
    Y, _ = np.linalg.qr(Y)
    for _ in range(4):
        Y = M @ (M.T @ Y)
        Y, _ = np.linalg.qr(Y)
    B = Y.T @ M
    _, s, Vt = np.linalg.svd(B, full_matrices=False)
    return s.astype(np.float64), Vt


def blockmax(W):
    n = (W.shape[1] // 16) * 16
    return np.abs(W[:, :n]).reshape(W.shape[0], -1, 16).max(axis=2)


def main():
    ref, _ = ex.index_2d(str(BF16))
    man = ex.load_grams(str(GRAMDIR))
    res = open(OUT, "a")
    res.write(f"# E29 proxy run {time.strftime('%F %T')}\n")
    res.write("# tensor  metric  k  captureW%  absmax_ratio_mean  "
              "absmax_ratio_p95block\n")
    agg = []
    for kind, blks in SAMPLE:
        for blk in blks:
            name = f"blk.{blk}.{kind}.weight"
            if name not in ref:
                continue
            W = dequantize(ref[name].data, ref[name].tensor_type
                           ).astype(np.float32)
            n_in = W.shape[1]
            wh = ex.gram_whitener(str(GRAMDIR), man, name, n_in)
            bm0 = blockmax(W)
            for metric in ("whitened", "raw"):
                if metric == "whitened":
                    if wh is None or len(wh) != 1:
                        continue
                    L = wh[0][1].astype(np.float32)
                    M = W @ L
                else:
                    M = W
                s, Vt = rsvd_rows(M, max(KS))
                tot = float((M.astype(np.float64) ** 2).sum())
                for k in KS:
                    capk = float((s[:k] ** 2).sum()) / tot * 100
                    Ck_w = (M @ Vt[:k].T) @ Vt[:k]
                    if metric == "whitened":
                        Ck = np.linalg.solve(
                            L.T.astype(np.float64),
                            Ck_w.T.astype(np.float64)).T.astype(np.float32)
                    else:
                        Ck = Ck_w
                    bm1 = blockmax(W - Ck)
                    ratio = bm1 / np.maximum(bm0, 1e-12)
                    row = (f"{name} {metric} k={k} capW={capk:.2f}% "
                           f"amax_ratio={float(ratio.mean()):.4f} "
                           f"p95={float(np.quantile(ratio, 0.95)):.4f}")
                    res.write(row + "\n")
                    res.flush()
                    print(row, flush=True)
                    if k == 128:
                        agg.append((metric, capk, float(ratio.mean())))
            del W
    for metric in ("whitened", "raw"):
        rows = [(c, r) for m, c, r in agg if m == metric]
        if rows:
            res.write(f"AGG {metric} k=128: mean capW="
                      f"{np.mean([c for c, _ in rows]):.2f}% "
                      f"mean amax_ratio={np.mean([r for _, r in rows]):.4f}"
                      f" over {len(rows)} tensors\n")
    res.write(f"# done {time.strftime('%F %T')}\n")
    res.close()


if __name__ == "__main__":
    main()
