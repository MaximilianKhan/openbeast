#!/usr/bin/env python3
"""E33 addendum — model-vs-base separation at 27B.
Same recipe on both models (one llama-quantize step from BF16, our
48-chunk wikitext imatrix, MTP pin blk.64=q5_k, Q2_K): heretic (h27b-Q2K)
vs Qwen3.8 (q38-27b-Q2K-ours). Reference rows: Qwen3.8 UD-Q2_K_XL (Unsloth
recipe) to show how much of the earlier same-d gap was the base recipe.
Writes addendum_summary.txt."""
import csv
from collections import defaultdict
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
PTS = "/home/max/Documents/openbeast/research/lowrank/data/spectra-e33/points.csv"
LANES = {"h27b-Q2K": "heretic Q2_K(ours)", "q38-27b-Q2K-ours": "Qwen3.8 Q2_K(ours)",
         "q38-27b-UD-Q2KXL": "Qwen3.8 UD-Q2_K_XL"}
rng = np.random.default_rng(0)
rows = [r for r in csv.DictReader(open(PTS)) if r["config"] in LANES]
for r in rows:
    r["r"] = int(r["r"]); r["capture"] = float(r["capture"]); r["d_in"] = int(r["d_in"])
    r["wh"] = "fc" if r["whitener"].startswith("fc") else r["whitener"]
L = []
P = L.append
P("# E33 addendum — model-vs-base separation at d_model=5120 (same Q2_K recipe on both models)")
P(f"lanes: {LANES}; n tensors per lane: " + ", ".join(
    f"{c}={len(set(r['tensor'] for r in rows if r['config']==c))}" for c in LANES))
# 1. mean capture + prefactors
P("\n## mean capture over tensors, and prefactor c (c_model = cap·5120/r ; c_in = mean cap·d_in/r)")
for wh in ("diag", "fc"):
    for rk in (64, 128):
        P(f"  {wh:4s} r={rk:3d}: " + "  |  ".join(
            f"{LANES[c]}: cap={np.mean([r['capture'] for r in rows if r['config']==c and r['wh']==wh and r['r']==rk]):.3f} "
            f"c_model={np.mean([r['capture'] for r in rows if r['config']==c and r['wh']==wh and r['r']==rk])*5120/rk:.1f} "
            f"c_in={np.mean([r['capture']*r['d_in']/rk for r in rows if r['config']==c and r['wh']==wh and r['r']==rk]):.1f}"
            for c in LANES))
# 2. per kind, same-recipe delta with bootstrap CI (and the UD delta for reference)
P("\n## per kind: Qwen3.8(ours) − heretic(ours), mean capture, 95% bootstrap CI  [UD-Q2_K_XL − heretic in brackets]")
res = {}
for wh in ("diag", "fc"):
    for rk in (64, 128):
        kinds = sorted(set(r["kind"] for r in rows))
        for kd in kinds:
            a = np.array([r["capture"] for r in rows if r["config"]=="h27b-Q2K" and r["wh"]==wh and r["r"]==rk and r["kind"]==kd])
            b = np.array([r["capture"] for r in rows if r["config"]=="q38-27b-Q2K-ours" and r["wh"]==wh and r["r"]==rk and r["kind"]==kd])
            u = np.array([r["capture"] for r in rows if r["config"]=="q38-27b-UD-Q2KXL" and r["wh"]==wh and r["r"]==rk and r["kind"]==kd])
            if not len(a) or not len(b):
                continue
            diff = [rng.choice(b, len(b)).mean() - rng.choice(a, len(a)).mean() for _ in range(1000)]
            lo, hi = np.percentile(diff, [2.5, 97.5])
            sig = "*" if (lo > 0 or hi < 0) else " "
            res[(wh, rk, kd)] = (a.mean(), b.mean(), lo, hi, u.mean() if len(u) else np.nan)
            P(f"  {wh:4s} r={rk:3d} {kd:9s}: heretic {a.mean():.3f}  Qwen3.8 {b.mean():.3f}  Δ={b.mean()-a.mean():+.3f} [{lo:+.3f},{hi:+.3f}]{sig}   "
              f"[UD: {u.mean() if len(u) else float('nan'):.3f}, Δ={u.mean()-a.mean() if len(u) else float('nan'):+.3f}]")
# 3. tally
P("\n## tally of kinds where the same-recipe difference is significant (CI excludes 0), by sign")
for wh in ("diag", "fc"):
    for rk in (64, 128):
        ks = [(kd, v) for (w, r_, kd), v in res.items() if w == wh and r_ == rk]
        pos = [kd for kd, v in ks if v[2] > 0]; neg = [kd for kd, v in ks if v[3] < 0]
        P(f"  {wh:4s} r={rk:3d}: Qwen3.8 higher at {len(pos)}/{len(ks)} kinds {pos}; lower at {len(neg)}/{len(ks)} {neg}")
# 4. within-model rank exponent, same recipe
P("\n## within-model rank exponent b_r (per-tensor log-log slope, r in [32,256]), same recipe")
for wh in ("diag", "fc"):
    for c in LANES:
        byt = defaultdict(list)
        for r in rows:
            if r["config"] == c and r["wh"] == wh and 32 <= r["r"] <= 256:
                byt[r["tensor"]].append((np.log(r["r"]), np.log(r["capture"])))
        sl = [np.polyfit(*zip(*v), 1)[0] for v in byt.values() if len(v) >= 4]
        if sl:
            P(f"  {wh:4s} {LANES[c]:20s}: b_r = {np.mean(sl):.2f} ± {np.std(sl):.2f} (n={len(sl)})")
(HERE / "addendum_summary.txt").write_text("\n".join(L) + "\n")
print("\n".join(L))
