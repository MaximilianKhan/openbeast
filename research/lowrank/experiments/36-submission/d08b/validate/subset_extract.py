#!/usr/bin/env python3
"""Validation driver: run 04-served-v0/extract_adapter.py UNCHANGED on a
subset of tensors (index_2d is filtered; the math is untouched).
usage: subset_extract.py "name1,name2,..." <extract_adapter args...>"""
import sys
from pathlib import Path
EXP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(EXP / "04-served-v0"))
import extract_adapter as ex  # noqa: E402

keep = set(sys.argv[1].split(","))
_orig = ex.index_2d


def index_2d(path, min_dim=64):
    d, arch = _orig(path, min_dim)
    return {k: v for k, v in d.items() if k in keep}, arch


ex.index_2d = index_2d
sys.argv = [sys.argv[0]] + sys.argv[2:]
ex.main()
