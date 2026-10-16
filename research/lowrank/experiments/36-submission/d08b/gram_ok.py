#!/usr/bin/env python3
"""Gate a Gram capture directory (LLAMA_IMATRIX_GRAM_DIR output) before any
consumer reads it: grams.json parses, every entry's .gram.bin exists with
the size its (n, blocks) implies, every count equals the expected token
count, and the diagonal imatrix is present. Optionally (--diag) reads each
Gram and checks its diagonal is finite and non-negative (0.8B only — at 27B
that is tens of GB of reads).

usage: gram_ok.py DIR EXPECTED_COUNT [--diag]   (exit 0 = good)
"""
import json
import os
import sys

import numpy as np


def main():
    d, want = sys.argv[1], int(sys.argv[2])
    diag = "--diag" in sys.argv
    man_p = os.path.join(d, "grams.json")
    if not os.path.exists(man_p):
        sys.exit(f"{d}: no grams.json")
    if not os.path.getsize(os.path.join(d, "diag.imatrix.gguf")) > 0:
        sys.exit(f"{d}: no diag.imatrix.gguf")
    man = json.load(open(man_p))
    bad = []
    for name, m in man.items():
        n, b, c = m["n"], m["blocks"], m["count"]
        f = os.path.join(d, name.replace("/", "_") + ".gram.bin")
        want_sz = b * (n // b) ** 2 * 4
        if not os.path.exists(f) or os.path.getsize(f) != want_sz:
            bad.append(f"{name}: file size")
        elif c != want:
            bad.append(f"{name}: count {c} != {want}")
        elif diag:
            g = np.fromfile(f, dtype=np.float32)
            bs = n // b
            for k in range(b):
                blk = g[k * bs * bs:(k + 1) * bs * bs].reshape(bs, bs)
                dg = np.diag(blk)
                if not np.all(np.isfinite(dg)) or dg.min() < 0:
                    bad.append(f"{name}: diagonal not finite/non-negative")
                    break
    print(f"{d}: {len(man)} Grams, count {want}, "
          f"{'OK' if not bad else f'{len(bad)} BAD'}")
    for x in bad[:10]:
        print("  ", x)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
