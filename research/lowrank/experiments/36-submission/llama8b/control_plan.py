#!/usr/bin/env python3
"""E36-L: the same-byte CONTROL, by a rule fixed before any measurement.

Rule (registered in README before the GPU run): start from Q3_K_S; promote
tensor kinds to q4_k in the order attn_k, attn_v, attn_output, attn_q.
A kind is promoted whole while that fits under the target; the first kind
that does not fit whole is promoted layer by layer at evenly spaced layers
(layer = floor((i + 0.5) * n_layer / k), i < k, k = the largest count that
fits) and the fill stops there. Target = corrected configuration's bytes
(MIXED file + adapter file); CONTROL is filled to it from below. Both
sides carry the same quantize header (same imatrix KVs), so the
comparison is made on tensor bytes.

usage: control_plan.py <dry-run log of Q3_K_S, with an imatrix>
                       <target tensor bytes>
prints the --tensor-type args and the predicted tensor bytes.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bytes import parse, tensor_bytes  # noqa: E402

ORDER = ["attn_k", "attn_v", "attn_output", "attn_q"]

ts = parse(sys.argv[1])
target = int(sys.argv[2])
total = sum(b for *_, b in ts)
n_layer = 1 + max(int(m.group(1)) for n, *_ in ts
                  if (m := re.match(r"blk\.(\d+)\.", n)))
args, notes = [], []
for kind in ORDER:
    rows = [(n, ne, t, b) for n, ne, t, b in ts
            if re.match(rf"blk\.\d+\.{kind}\.weight$", n)]
    deltas = {int(n.split(".")[1]): tensor_bytes(ne, "q4_K") - b
              for n, ne, t, b in rows}
    whole = sum(deltas.values())
    if total + whole <= target:
        total += whole
        args.append(f"--tensor-type '{kind}=q4_k'")
        notes.append(f"{kind}: all {len(rows)} layers (+{whole:,d})")
        continue
    # partial: equal per-layer delta for a kind (same shape every layer)
    d = max(deltas.values())
    assert len(set(deltas.values())) == 1, "per-layer deltas differ"
    k = (target - total) // d
    layers = sorted({int((i + 0.5) * n_layer / k) for i in range(k)}) \
        if k > 0 else []
    assert len(layers) == k
    for li in layers:
        args.append(f"--tensor-type 'blk\\.{li}\\.{kind}=q4_k'")
    total += k * d
    notes.append(f"{kind}: {k} of {n_layer} layers {layers} (+{k*d:,d})")
    break
print("\n".join(notes))
print("ARGS: " + " ".join(args))
print(f"predicted tensor bytes {total:,d} vs target {target:,d} "
      f"({100.0*(total-target)/target:+.4f}%)")
