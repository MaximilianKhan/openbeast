#!/usr/bin/env python3
"""TensorFold rank-0 log summary: requests, prompt/cached tokens, cache hits, stale notes, the last prompt-cache line.
  python3 tf_logstat.py rank0.log[.gz]   (docs/reports/DEEPSEEK_SERVING_OPT-2026-11-25.md)"""
import re, sys, gzip
f = sys.argv[1]
op = gzip.open if f.endswith(".gz") else open
n = p = c = g = hitn = 0; last = None; stale = 0; resumed = []; spill = 0
for line in op(f, "rt", errors="replace"):
    m = re.search(r"done \S+ prompt=(\d+) cached=(\d+) .*tokens=(\d+)", line)
    if m:
        n += 1; p += int(m[1]); c += int(m[2]); g += int(m[3]); hitn += int(m[2]) > 0
    if "prompt cache:" in line and "hits" in line: last = line.strip()[-110:]
    if "goes stale" in line: stale += 1
    m = re.search(r"resumed at (\d+)", line)
    if m and int(m[1]) > 0: resumed.append(int(m[1]))
    if "spill" in line.lower() and "restor" in line.lower(): spill += 1
print(f"requests {n}  prompt {p}  cached {c} ({100*c/max(p,1):.1f}%)  computed {p-c}  hits {hitn}  gen {g}  mean prompt {p/max(n,1):.0f}  mean gen {g/max(n,1):.0f}")
print(f"stale-notes(>=64K) {stale}  resumed>0 {len(resumed)} mean {sum(resumed)/max(len(resumed),1):.0f}  spill-restore lines {spill}")
print("last:", last)
