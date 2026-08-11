#!/usr/bin/env python3
"""E11: one CALDERA-style alternation round with a black-box quantizer.

Round 0 (done): Q0 = quant(W), C0 = lowrank correction of W - Q0.
Round 1 (this): W' = W - C0  →  Q1 = quant(W')  →  C1 = correction of
W - Q1. The quantizer then spends its bits on what the correction does
NOT carry, and the new correction mops up what the new quantizer leaves.
Stop-on-worse rule applies (math-methods §4: initialization dominates,
alternation helps little at >=3-bit — 2-bit is where it might pay).

This script performs the W' model write (step 1). Quantize/extract/
measure are driven by the shell wrapper.
"""
import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "04-served-v0"))
from extract_adapter import REPO, deq  # noqa: E402

sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf  # noqa: E402
from gguf import GGUFReader, GGUFWriter  # noqa: E402
from gguf.quants import dequantize  # noqa: E402


def load_adapter(path):
    """-> {base_name: (A f32 (r,n_in), B f32 (n_out,r), alpha)}"""
    r = GGUFReader(path)
    alpha = float(r.get_field("adapter.lora.alpha").contents())
    pairs = {}
    for t in r.tensors:
        if t.name.endswith(".lora_a"):
            pairs.setdefault(t.name[:-7], {})["A"] = t
        elif t.name.endswith(".lora_b"):
            pairs.setdefault(t.name[:-7], {})["B"] = t
    out = {}
    for base, ab in pairs.items():
        A = dequantize(ab["A"].data, ab["A"].tensor_type)
        B = dequantize(ab["B"].data, ab["B"].tensor_type)
        shpA = [int(d) for d in ab["A"].shape if int(d) > 1]
        shpB = [int(d) for d in ab["B"].shape if int(d) > 1]
        A = A.reshape(shpA[::-1]) if A.ndim == 1 else A
        B = B.reshape(shpB[::-1]) if B.ndim == 1 else B
        out[base] = (A.astype(np.float32), B.astype(np.float32), alpha)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("adapter_gguf")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    adapters = load_adapter(args.adapter_gguf)
    rdr = GGUFReader(args.ref_gguf)
    arch = rdr.get_field("general.architecture").contents()
    w = GGUFWriter(args.out, arch)
    # KV copy-through per the live-verified R3 recipe (gguf-tooling.md)
    for key, f in rdr.fields.items():
        if key.startswith("GGUF.") or key == "general.architecture":
            continue
        sub = f.types[-1] if f.types[0] == gguf.GGUFValueType.ARRAY else None
        w.add_key_value(key, f.contents(), f.types[0], sub_type=sub)

    n_mod = 0
    for t in rdr.tensors:
        if t.name in adapters:
            A, B, alpha = adapters[t.name]
            r = A.shape[0]
            dW = (B @ A) * (alpha / r)
            Wf = deq(t) - dW                      # (n_out, n_in)
            w.add_tensor(t.name, Wf.astype(np.float16))
            n_mod += 1
        else:
            w.add_tensor(t.name, t.data, raw_dtype=t.tensor_type)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file(progress=False)
    w.close()
    print(f"wrote {args.out}: {n_mod} tensors corrected-then-subtracted, "
          f"rest passthrough")


if __name__ == "__main__":
    main()
