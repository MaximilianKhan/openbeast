#!/usr/bin/env python3
"""E30 stage 1: paired stable-rank census over 2-D tensors (see PREREG.md)."""
import json
import sys
import time

import numpy as np

sys.path.insert(0, "/home/max/Documents/openbeast/llama.cpp/gguf-py")
from gguf import GGUFReader  # noqa: E402
from gguf.quants import dequantize  # noqa: E402

MODELS = {
    "qwen36": "/home/max/Documents/openbeast/weights/Qwen3.6-27B-UD-Q5_K_XL.gguf",
    "qwen38": "/home/max/Documents/openbeast/weights/Qwen3.8-27B-UD-Q5_K_XL.gguf",
}
TREAT_KEYS = ("attn_q.weight", "attn_k.weight", "attn_v.weight",
              "attn_output.weight", "ffn_gate.weight", "ffn_up.weight",
              "ffn_down.weight")
CONTROL_KEYS = ("token_embd.weight", "output.weight")


def sigma_max(W, iters=30, seed=0):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(W.shape[1]).astype(np.float32)
    v /= np.linalg.norm(v)
    prev = 0.0
    for _ in range(iters):
        u = W @ v
        nu = np.linalg.norm(u)
        if nu == 0:
            return 0.0
        v = W.T @ (u / nu)
        s = np.linalg.norm(v)
        v /= s
        if abs(s - prev) < 1e-4 * s:
            break
        prev = s
    return float(s)


def census(path):
    r = GGUFReader(path)
    out = {}
    for t in r.tensors:
        name = t.name
        cls = ("treat" if any(name.endswith(k) for k in TREAT_KEYS)
               else "control" if any(name.endswith(k) for k in CONTROL_KEYS)
               else None)
        if cls is None or len(t.shape) != 2:
            continue
        t0 = time.time()
        W = dequantize(t.data, t.tensor_type).reshape(tuple(int(x) for x in reversed(t.shape))).astype(np.float32)
        fro2 = float(np.sum(W.astype(np.float64) ** 2))
        smax = sigma_max(W)
        srank = fro2 / (smax * smax) if smax else 0.0
        out[name] = {"class": cls, "shape": list(W.shape),
                     "srank": srank, "sigma_max": smax,
                     "fro2": fro2, "sec": round(time.time() - t0, 1)}
        print(f"{name:<44} {cls:<7} srank={srank:9.1f} ({out[name]['sec']}s)", flush=True)
        del W
    return out


def main():
    res = {}
    for tag, path in MODELS.items():
        print(f"=== {tag}: {path}", flush=True)
        res[tag] = census(path)
    common = sorted(set(res["qwen36"]) & set(res["qwen38"]))
    rows = {"treat": [], "control": []}
    for n in common:
        a, b = res["qwen36"][n], res["qwen38"][n]
        rows[a["class"]].append(np.log(b["srank"] / a["srank"]))
    summary = {}
    for cls, v in rows.items():
        v = np.array(v)
        summary[cls] = {"n": len(v), "mean_logratio": float(v.mean()),
                        "sem": float(v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 1 else None}
    out = {"summary": summary, "tensors": res}
    with open(sys.argv[1] if len(sys.argv) > 1 else "e30-stage1.json", "w") as f:
        json.dump(out, f, indent=1)
    print("\nSUMMARY (log srank ratio 3.8/3.6; >0 = 3.8 flatter):")
    for cls, s in summary.items():
        print(f"  {cls:<8} n={s['n']:>3} mean={s['mean_logratio']:+.4f} ± {s['sem']:.4f}"
              if s["sem"] else f"  {cls}: n={s['n']}")


if __name__ == "__main__":
    main()
