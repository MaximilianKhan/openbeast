#!/usr/bin/env python3
"""Predict a GGUF's file size when only string KV values change length.
usage: header_bytes.py FILE.gguf [KEY=NEWVALUE ...]
Prints unpadded header length, data offset, file size, and the predicted
file size with the given string KVs replaced (tensor data unchanged)."""
import sys
import numpy as np
from gguf import GGUFReader

r = GGUFReader(sys.argv[1])
hdr = 4 + 4 + 8 + 8
for f in r.fields.values():
    if f.name.startswith("GGUF."):
        continue
    hdr += sum(np.asarray(p).nbytes for p in f.parts)
for t in r.tensors:
    hdr += 8 + len(t.name.encode()) + 4 + 8 * len(t.shape) + 4 + 8
align = 32
if "general.alignment" in r.fields:
    align = int(r.fields["general.alignment"].contents())
pad = lambda n: (n + align - 1) // align * align  # noqa: E731
size = __import__("os").path.getsize(sys.argv[1])
data = size - r.data_offset
print(f"header {hdr} B unpadded, data_offset {r.data_offset} "
      f"(pad32 {pad(hdr)}), data {data} B, file {size} B")
new = hdr
for kv in sys.argv[2:]:
    k, v = kv.split("=", 1)
    old = r.fields[k].contents()
    new += len(v.encode()) - len(str(old).encode())
    print(f"  {k}: {len(str(old))} -> {len(v)} chars")
if len(sys.argv) > 2:
    print(f"predicted file size: {pad(new) + data} B")
