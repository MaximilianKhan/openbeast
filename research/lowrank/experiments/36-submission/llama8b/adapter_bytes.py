#!/usr/bin/env python3
"""E36-L byte plan: exact file size of the r128 Q8_0 fc adapter the
extractor (e27_extract.py) would write for this model, by writing one with
the same GGUFWriter calls, names, shapes and dtype but zero factors.

Target set mirrors e27_extract on llama: every 2D tensor with imatrix
entries (blk.*.attn_q/k/v/output/ffn_*) minus the rank-map zeros
(ffn_gate/ffn_up/ffn_down) -> attn_q/k/v/output, 4 x n_layer tensors.
Tensor bytes are elems*34/32 (Q8_0), verified against the 27B adapter
(336,461,824 B tensors for 316,669,952 elems).

usage: adapter_bytes.py <model.gguf> <scratch-out.gguf> [--rank 128]
"""
import argparse
import os
import re
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf  # noqa: E402
from gguf.quants import quantize as gquant  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("model")
ap.add_argument("out")
ap.add_argument("--rank", type=int, default=128)
a = ap.parse_args()

r = gguf.GGUFReader(a.model)
arch = r.get_field("general.architecture").contents()
qt = gguf.GGMLQuantizationType.Q8_0
w = gguf.GGUFWriter(a.out, arch)
w.add_type(gguf.GGUFType.ADAPTER)
w.add_string(gguf.Keys.Adapter.TYPE, "lora")
w.add_float32(gguf.Keys.Adapter.LORA_ALPHA, float(a.rank))
elems = 0
n = 0
for t in r.tensors:
    if not re.match(r"blk\.\d+\.(attn_[qkv]|attn_output)\.weight$", t.name):
        continue
    n_in, n_out = int(t.shape[0]), int(t.shape[1])
    rr = min(a.rank, n_in, n_out)
    A = np.zeros((rr, n_in), np.float32)
    B = np.zeros((n_out, rr), np.float32)
    w.add_tensor(t.name + ".lora_a", gquant(A, qt), raw_dtype=qt)
    w.add_tensor(t.name + ".lora_b", gquant(B, qt), raw_dtype=qt)
    elems += A.size + B.size
    n += 1
w.write_header_to_file()
w.write_kv_data_to_file()
w.write_tensors_to_file(progress=False)
w.close()
size = os.path.getsize(a.out)
print(f"adapter tensors={n} elems={elems} tensor_bytes={elems*34//32} "
      f"file_bytes={size} header={size - elems*34//32}")
