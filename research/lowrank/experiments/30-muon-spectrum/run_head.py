#!/usr/bin/env python3
"""E30 stage 2: top-64 spectrum head on the pre-registered 24-tensor sample."""
import json
import sys

import numpy as np

sys.path.insert(0, "/home/max/Documents/openbeast/llama.cpp/gguf-py")
from gguf import GGUFReader  # noqa: E402
from gguf.quants import dequantize  # noqa: E402

MODELS = {
    "qwen36": "/home/max/Documents/openbeast/weights/Qwen3.6-27B-UD-Q5_K_XL.gguf",
    "qwen38": "/home/max/Documents/openbeast/weights/Qwen3.8-27B-UD-Q5_K_XL.gguf",
}
LAYERS = [2, 5, 8, 20, 26, 32, 44, 52, 60]          # pre-registered
SUFFIXES = ("attn_q.weight", "ffn_down.weight")      # pre-registered
K = 64


def topk_sv(W, k=K, seed=0, oversample=16, iters=4):
    rng = np.random.default_rng(seed)
    m, n = W.shape
    Q = rng.standard_normal((n, k + oversample)).astype(np.float32)
    for _ in range(iters):
        Q, _ = np.linalg.qr(W @ Q)
        Q, _ = np.linalg.qr(W.T @ Q)
    B = W @ Q
    s = np.linalg.svd(B, compute_uv=False)
    return s[:k]


def head_entropy(s):
    p = (s ** 2) / np.sum(s ** 2)
    return float(-np.sum(p * np.log(p)) / np.log(len(p)))  # normalized 0..1


def main():
    out = {}
    for tag, path in MODELS.items():
        r = GGUFReader(path)
        idx = {t.name: t for t in r.tensors}
        rows = {}
        for L in LAYERS:
            for suf in SUFFIXES:
                name = f"blk.{L}.{suf}"
                t = idx.get(name)
                if t is None:
                    continue
                W = dequantize(t.data, t.tensor_type).reshape(
                    tuple(int(x) for x in reversed(t.shape))).astype(np.float32)
                s = topk_sv(W)
                rows[name] = {"head_entropy": head_entropy(s),
                              "sv_ratio_1_64": float(s[0] / s[-1]),
                              "sv": [float(x) for x in s]}
                print(f"{tag} {name:<26} Hn={rows[name]['head_entropy']:.4f} "
                      f"s1/s64={rows[name]['sv_ratio_1_64']:.2f}", flush=True)
                del W
        out[tag] = rows
    common = sorted(set(out["qwen36"]) & set(out["qwen38"]))
    dH = np.array([out["qwen38"][n]["head_entropy"] - out["qwen36"][n]["head_entropy"]
                   for n in common])
    print(f"\nPAIRED head-entropy delta (3.8 − 3.6; >0 = 3.8 flatter head): "
          f"n={len(dH)} mean={dH.mean():+.5f} ± {dH.std(ddof=1)/np.sqrt(len(dH)):.5f}")
    with open(sys.argv[1] if len(sys.argv) > 1 else "e30-stage2.json", "w") as f:
        json.dump(out, f)


if __name__ == "__main__":
    main()
