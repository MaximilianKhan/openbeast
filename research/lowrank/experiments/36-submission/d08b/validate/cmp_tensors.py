#!/usr/bin/env python3
"""Per-tensor raw-byte comparison of two GGUFs (types + data).
usage: cmp_tensors.py A.gguf B.gguf  -> 'N/M tensors byte-identical' + diffs"""
import sys
import numpy as np
from gguf import GGUFReader

a = {t.name: t for t in GGUFReader(sys.argv[1]).tensors}
b = {t.name: t for t in GGUFReader(sys.argv[2]).tensors}
same, diff = 0, []
for n in a:
    if n in b and a[n].tensor_type == b[n].tensor_type and \
            np.array_equal(np.asarray(a[n].data), np.asarray(b[n].data)):
        same += 1
    else:
        diff.append(n)
print(f"{same}/{len(a)} tensors byte-identical; only-in-B: {len(set(b) - set(a))}")
for n in diff[:20]:
    print("  differs:", n, a[n].tensor_type.name, b[n].tensor_type.name if n in b else "-")
