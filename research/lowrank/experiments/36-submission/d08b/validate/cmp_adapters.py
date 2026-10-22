#!/usr/bin/env python3
"""Compare the tensors two LoRA GGUFs share: raw-byte identity and the
relative Frobenius difference of the dequantized products B@A.
usage: cmp_adapters.py A.gguf B.gguf"""
import sys
from pathlib import Path
import numpy as np
EXP = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(EXP / "11-alternation"))
from e11_alternate import load_adapter  # noqa: E402
from gguf import GGUFReader  # noqa: E402

ra = {t.name: t for t in GGUFReader(sys.argv[1]).tensors}
rb = {t.name: t for t in GGUFReader(sys.argv[2]).tensors}
A, B = load_adapter(sys.argv[1]), load_adapter(sys.argv[2])
for base in sorted(set(A) & set(B)):
    same = all(np.array_equal(np.asarray(ra[base + s].data), np.asarray(rb[base + s].data))
               for s in (".lora_a", ".lora_b"))
    pa = A[base][1] @ A[base][0]
    pb = B[base][1] @ B[base][0]
    rel = np.linalg.norm(pa - pb) / np.linalg.norm(pb)
    print(f"{base:32s} bytes-identical={same}  rel|dBA|={rel:.2e}")
