#!/usr/bin/env python3
"""E36-L: exact tensor bytes of a llama-quantize plan, from a --dry-run log.

llama-quantize --dry-run prints every tensor's shape and chosen type but
rounds sizes to MiB. This recomputes them exactly from ggml block sizes
(gguf-py GGML_QUANT_SIZES), so the plan is exact to the byte; add the
GGUF header (measured from a real file, see README) for file bytes.

usage: bytes.py dry.log [--by-kind] [--header N]
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
from gguf.constants import GGML_QUANT_SIZES, GGMLQuantizationType  # noqa

LINE = re.compile(r"\]\s+(\S+)\s+-\s+\[\s*([\d,\s]+)\],\s+type\s+=\s+(\w+),"
                  r".*?(?:\((\w+)\))?\s*$")


def tensor_bytes(ne, tname):
    qt = GGMLQuantizationType[tname.upper()]
    blk, tb = GGML_QUANT_SIZES[qt]
    n = 1
    for d in ne:
        n *= d
    assert ne[0] % blk == 0, (ne, tname)
    return n // blk * tb


def parse(path):
    out = []
    for ln in Path(path).read_text().splitlines():
        m = LINE.search(ln)
        if not m:
            continue
        name = m.group(1)
        ne = [int(x) for x in m.group(2).replace(" ", "").split(",") if x]
        src, dst = m.group(3), m.group(4)
        t = dst if dst else src
        out.append((name, ne, t, tensor_bytes(ne, t)))
    return out


def kind(name):
    m = re.match(r"blk\.\d+\.(\w+)\.weight", name)
    return m.group(1) if m else name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--by-kind", action="store_true")
    ap.add_argument("--header", type=int, default=0)
    a = ap.parse_args()
    ts = parse(a.log)
    tot = sum(b for *_, b in ts)
    if a.by_kind:
        agg = defaultdict(lambda: [0, 0, set()])
        for name, ne, t, b in ts:
            k = kind(name)
            agg[k][0] += 1
            agg[k][1] += b
            agg[k][2].add(t)
        for k, (n, b, types) in sorted(agg.items()):
            print(f"  {k:24s} n={n:3d} bytes={b:>13,d} types={','.join(sorted(types))}")
    print(f"tensors={len(ts)} tensor_bytes={tot} file_bytes={tot + a.header}")


if __name__ == "__main__":
    main()
