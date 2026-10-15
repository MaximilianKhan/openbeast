#!/usr/bin/env python3
"""E36-L test helper: write a SYNTHETIC imatrix GGUF (lognormal per-channel
sums, real llama tensor names) for CPU-only checks. NOT a measurement
artifact — never quantize a scored file with it. Uses:
  1. byte-plan check: quantize with vs without an imatrix -> same bytes;
  2. tool audit: drive the extractor's target selection on CPU.

usage: synth_imatrix.py <model.gguf> <out.imatrix.gguf> [--layers N]
"""
import argparse
import re
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("model")
ap.add_argument("out")
ap.add_argument("--layers", type=int, default=10**9,
                help="only blocks < N (default all)")
a = ap.parse_args()

rng = np.random.default_rng(7)
w = gguf.GGUFWriter(a.out, arch="")
w.add_type("imatrix")
w.add_array("imatrix.datasets", ["SYNTHETIC-lognormal"])
w.add_uint32("imatrix.chunk_count", 1)
w.add_uint32("imatrix.chunk_size", 512)
n = 0
for t in gguf.GGUFReader(a.model).tensors:
    m = re.match(r"blk\.(\d+)\.(attn_[qkv]|attn_output|ffn_gate|ffn_up|"
                 r"ffn_down)\.weight$", t.name)
    if not m or int(m.group(1)) >= a.layers:
        continue
    n_in = int(t.shape[0])
    w.add_tensor(t.name + ".in_sum2",
                 rng.lognormal(0.0, 1.0, n_in).astype(np.float32) * 512)
    w.add_tensor(t.name + ".counts", np.array([512.0], dtype=np.float32))
    n += 1
w.write_header_to_file()
w.write_kv_data_to_file()
w.write_tensors_to_file(progress=False)
w.close()
print(f"wrote {a.out}: {n} entries")
