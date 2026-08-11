#!/usr/bin/env python3
"""E07 v2 pass 2: emit an allocated adapter from a pass1 cache in seconds.

Allocation = global greedy on s2 × kind_weight / byte_cost. Kind weights
default to the MEASURED sensitivity (ΔKLD per byte, kind-probe 2026-08-03,
0.6B), rescaled so each kind's TOTAL cached energy per byte matches its
measured value per byte — grounding cross-kind comparability in KL
measurement instead of raw activation scale.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "04-served-v0"))
from extract_adapter import REPO  # noqa: E402

sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf  # noqa: E402
from gguf.quants import quantize as gquant  # noqa: E402

# measured ΔmeanKLD per MB, kind-probe (r64, 0.6B, Q2_K-imat base)
MEASURED_VALUE_PER_MB = {
    "attn_q": 0.0034, "attn_k": 0.0128, "attn_v": 0.0074,
    "attn_o": 0.0015, "ffn_gate": 0.0035, "ffn_up": 0.0038,
    "ffn_down": 0.0029, "other": 0.0035,   # attn_gate etc: mid value
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget-mb", type=float, required=True)
    ap.add_argument("--policy", choices=["energy", "measured"],
                    default="measured")
    ap.add_argument("--q8", action="store_true",
                    help="Q8_0 factors (~53%% of F16 bytes); ranks rounded "
                         "down to multiples of 32")
    ap.add_argument("--min-rank", type=int, default=8)
    args = ap.parse_args()
    cache = Path(args.cache_dir)
    meta = json.loads((cache / "meta.json").read_text())
    tens = meta["tensors"]
    names = list(tens)
    bpe = 1.0625 if args.q8 else 2.0     # bytes per factor element

    s2s = {nm: np.load(cache / tens[nm]["file"])["s2"] for nm in names}
    weight = {}
    if args.policy == "measured":
        # per-kind rescale: kind total energy/byte -> measured value/byte
        agg = {}
        for nm in names:
            k = tens[nm]["kind"]
            c = bpe * (tens[nm]["m"] + tens[nm]["n"])
            a = agg.setdefault(k, [0.0, 0.0])
            a[0] += s2s[nm].sum()
            a[1] += c * len(s2s[nm])
        for k, (e, b) in agg.items():
            mv = MEASURED_VALUE_PER_MB.get(k, MEASURED_VALUE_PER_MB["other"])
            weight[k] = (mv / 1e6) / max(e / b, 1e-30)
    else:
        weight = {k: 1.0 for k in MEASURED_VALUE_PER_MB}

    gains, owner = [], []
    for ti, nm in enumerate(names):
        w = weight.get(tens[nm]["kind"], weight.get("other", 1.0))
        cost = bpe * (tens[nm]["m"] + tens[nm]["n"])
        gains.append(s2s[nm] * w / cost)
        owner.append(np.full(len(s2s[nm]), ti, dtype=np.int32))
    gains = np.concatenate(gains)
    owner = np.concatenate(owner)
    order = np.argsort(-gains)

    budget = args.budget_mb * 1e6
    ranks = {nm: 0 for nm in names}
    spend = 0.0
    for idx in order:
        nm = names[owner[idx]]
        c = bpe * (tens[nm]["m"] + tens[nm]["n"])
        if spend + c > budget:
            continue
        ranks[nm] += 1
        spend += c
    if args.q8:
        ranks = {nm: (r // 32) * 32 for nm, r in ranks.items()}
    ranks = {nm: r for nm, r in ranks.items() if r >= args.min_rank}

    w = gguf.GGUFWriter(args.out, meta["arch"])
    w.add_type(gguf.GGUFType.ADAPTER)
    w.add_string(gguf.Keys.Adapter.TYPE, "lora")
    w.add_float32(gguf.Keys.Adapter.LORA_ALPHA, 1.0)
    qt = gguf.GGMLQuantizationType.Q8_0
    for nm, r in ranks.items():
        z = np.load(cache / tens[nm]["file"])
        A = z["Afull"][:r].astype(np.float32) * r    # fold rank (alpha=1)
        B = z["U"][:, :r].astype(np.float32)
        if args.q8:
            w.add_tensor(nm + ".lora_a", gquant(A, qt), raw_dtype=qt)
            w.add_tensor(nm + ".lora_b", gquant(B, qt), raw_dtype=qt)
        else:
            w.add_tensor(nm + ".lora_a", A.astype(np.float16))
            w.add_tensor(nm + ".lora_b", B.astype(np.float16))
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file(progress=False)
    w.close()

    by_kind = {}
    for nm, r in ranks.items():
        by_kind.setdefault(tens[nm]["kind"], []).append(r)
    print(f"wrote {args.out}: {Path(args.out).stat().st_size/1e6:.0f} MB, "
          f"{len(ranks)}/{len(names)} tensors, policy={args.policy}, "
          f"q8={args.q8}")
    for k in sorted(by_kind):
        rs = by_kind[k]
        print(f"  {k:10s} n={len(rs):3d} mean_r={np.mean(rs):6.1f} "
              f"max={max(rs)}")


if __name__ == "__main__":
    main()
