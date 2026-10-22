#!/usr/bin/env python3
"""From a llama-quantize --dry-run log, list every tensor whose target type
requires an imatrix (IQ1/IQ2/IQ3_XXS) and check the imatrix has its entry —
the one failure --dry-run does not surface (the real run throws
'Missing importance matrix ... bailing out').
usage: check_dry_imatrix.py DRY.log IMATRIX.gguf"""
import re
import sys
from collections import Counter
from gguf import GGUFReader

need = ("iq1_s", "iq1_m", "iq2_xxs", "iq2_xs", "iq2_s", "iq3_xxs")
have = {t.name[:-len(".in_sum2")] for t in GGUFReader(sys.argv[2]).tensors
        if t.name.endswith(".in_sum2")}
types, missing, total = Counter(), [], 0.0
for line in open(sys.argv[1]):
    m = re.search(r"\]\s+(\S+)\s+- .*-> +([\d.]+) MiB \((\w+)\)", line)
    if not m:
        continue
    name, mib, ty = m.group(1), float(m.group(2)), m.group(3)
    types[ty] += 1
    total += mib
    if ty in need and name not in have and not name.startswith("token_embd"):
        missing.append((name, ty))
print("target types:", dict(types))
print(f"quantized tensor MiB (dry-run sum): {total:.1f}")
print(f"tensors needing an imatrix but absent from it: {len(missing)}")
for n, t in missing[:20]:
    print("  MISSING", n, t)
sys.exit(1 if missing else 0)
