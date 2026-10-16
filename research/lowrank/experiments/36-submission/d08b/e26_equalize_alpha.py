#!/usr/bin/env python3
"""E36-D T1.13 COPY of experiments/26-lloyd-gauge/e26_equalize.py (the
original is untouched). Two additions, nothing else changed:
  --alpha A   partial exponent: s_j = 2^round(A * 0.5 * log2(d_j/geomean))
              (A = 1 is the original transform, bit-for-bit; still pow2,
              so the fold stays an exact gauge transformation)
  --gram-dir  Gram source (the original hardcodes data/gram08b)
  imatrix args are optional: with only BF16_IN OUT_MODEL the model is
              equalized and no imatrix is written (the imatrix-free arm).
usage: e26_equalize_alpha.py BF16_IN OUT_MODEL [IMX_IN OUT_IMX]
                             [--alpha A] [--gram-dir DIR]

--- original docstring ---
E26-M5: principled gauge-fixing on qwen35 — EXACT diagonal
equalization folded through the RMSNorm gammas (HeRo-Q/FlatQuant
lineage, restricted to what folds EXACTLY on this graph).

Graph facts (src/models/qwen35.cpp, read before building):
  - attn_norm output (rmsnorm(h) * gamma) feeds ONLY:
      classic layers: attn_q, attn_k, attn_v
      linear layers:  attn_qkv, attn_gate, ssm_beta, ssm_alpha
  - post_attention_norm output feeds ONLY: ffn_up, ffn_gate
  - full ROTATIONS do not fold: gamma is a learnable diagonal, so a
    rotation R cannot be absorbed into it without rewriting the whole
    residual stream (QuaRot-style global gauge — out of scope). The
    exactly-foldable group on this arch is DIAGONAL. That is the result.

Transform (per norm, per layer): s_j = nearest POWER OF TWO to
sqrt(diag(S)_j / geomean(diag S)), S = the norm-output Gram
(attn_qkv / attn_k / ffn_up sources, data/gram08b).
  gamma'    = gamma / s        (F32, exact — pow2)
  W'[:, j]  = W[:, j] * s_j    (BF16 bit-exact — pow2 exponent bump,
                                verified elementwise, hard fail if not)
Power-of-two restriction makes the fold a bit-exact gauge transformation
(FP multiply commutes with pow2 scaling), so the equalized BF16 is the
SAME function — verified by KLD vs the stored BF16 logits.

The imatrix transforms exactly with the gauge: in_sum2' = in_sum2 / s^2
(pow2, exact) for every consumer of a folded norm. MTP blk.24 has no
Grams (imatrix blindspot, known) -> not equalized, disclosed.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "04-served-v0"))  # copy lives in 36-submission/d08b
from extract_adapter import REPO  # noqa: E402
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf  # noqa: E402
from gguf import GGUFReader, GGUFWriter  # noqa: E402

RES = Path(__file__).resolve().parents[3]   # research/lowrank (copy is one level deeper)
GRAM_DIR = RES / "data" / "gram08b"
N_TRUNK = 24


def pow2_s(gram_file, n, count, alpha=1.0):
    v = np.fromfile(gram_file, dtype=np.float32).reshape(n, n)
    d = np.diag(v).astype(np.float64) / count
    ok = d > 0
    ln = np.log2(d[ok])
    s = np.ones(n)
    if alpha == 1.0:      # the original expression, untouched
        s[ok] = 2.0 ** np.round(0.5 * (ln - ln.mean()))
    else:
        s[ok] = 2.0 ** np.round(alpha * 0.5 * (ln - ln.mean()))
    return s.astype(np.float32)


def scale_bf16_cols(raw, n_out, n_in, s):
    """raw uint8 (n_out, n_in*2) -> columns scaled by pow2 s, bit-exact."""
    u16 = raw.reshape(n_out, n_in, 2).copy().view(np.uint16)[..., 0]
    f32 = (u16.astype(np.uint32) << 16).view(np.float32)
    f32 = f32 * s[None, :]
    bits = f32.view(np.uint32)
    if int((bits & 0xFFFF).sum()) != 0:
        raise SystemExit("BF16 fold not exact — pow2 assumption violated")
    out = np.zeros((n_out, n_in, 2), dtype=np.uint8)
    out.view(np.uint16)[..., 0] = (bits >> 16).astype(np.uint16)
    return out.reshape(n_out, n_in * 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bf16_in")
    ap.add_argument("out_model")
    ap.add_argument("imx_in", nargs="?", default=None)
    ap.add_argument("out_imx", nargs="?", default=None)
    ap.add_argument("--alpha", type=float, default=1.0)
    ap.add_argument("--gram-dir", default=str(GRAM_DIR))
    a = ap.parse_args()
    if (a.imx_in is None) != (a.out_imx is None):
        sys.exit("give both IMX_IN and OUT_IMX, or neither")
    bf16_in, out_model, imx_in, out_imx = (a.bf16_in, a.out_model,
                                           a.imx_in, a.out_imx)
    gram_dir = Path(a.gram_dir)

    man = json.loads((gram_dir / "grams.json").read_text())
    rdr = GGUFReader(bf16_in)
    names = {t.name for t in rdr.tensors}

    # build the fold plan: {consumer/gamma tensor -> s (columns) or 1/s}
    col_scale = {}      # weight name -> s vector (input-column scaling)
    gam_scale = {}      # norm name -> s vector (divide)
    s_stats = []
    for il in range(N_TRUNK):
        classic = f"blk.{il}.attn_q.weight" in names
        groups = [
            (f"blk.{il}.attn_norm.weight",
             f"blk.{il}." + ("attn_k" if classic else "attn_qkv")
             + ".weight",
             [f"blk.{il}.{k}.weight" for k in
              (("attn_q", "attn_k", "attn_v") if classic else
               ("attn_qkv", "attn_gate", "ssm_beta", "ssm_alpha"))]),
            (f"blk.{il}.post_attention_norm.weight",
             f"blk.{il}.ffn_up.weight",
             [f"blk.{il}.{k}.weight" for k in ("ffn_up", "ffn_gate")]),
        ]
        for gamma, src, consumers in groups:
            meta = man[src]
            s = pow2_s(gram_dir / (src.replace("/", "_") + ".gram.bin"),
                       meta["n"], meta["count"], a.alpha)
            gam_scale[gamma] = s
            for c in consumers:
                if c not in names:
                    raise SystemExit(f"missing consumer {c} — graph "
                                     "assumption broken, refusing")
                col_scale[c] = s
            s_stats.append((gamma, float(s.min()), float(s.max()),
                            int((s != 1).sum())))

    # --- rewrite the model ------------------------------------------
    arch = rdr.get_field("general.architecture").contents()
    w = GGUFWriter(out_model, arch)
    for key, f in rdr.fields.items():
        if key.startswith("GGUF.") or key == "general.architecture":
            continue
        sub = f.types[-1] if f.types[0] == gguf.GGUFValueType.ARRAY else None
        w.add_key_value(key, f.contents(), f.types[0], sub_type=sub)
    n_fold = 0
    for t in rdr.tensors:
        if t.name in col_scale:
            if t.tensor_type != gguf.GGMLQuantizationType.BF16:
                raise SystemExit(f"{t.name}: expected BF16")
            shape = [int(x) for x in t.shape]
            n_in, n_out = shape[0], shape[1]
            data = scale_bf16_cols(np.asarray(t.data), n_out, n_in,
                                   col_scale[t.name])
            w.add_tensor(t.name, data,
                         raw_dtype=gguf.GGMLQuantizationType.BF16)
            n_fold += 1
        elif t.name in gam_scale:
            g = np.asarray(t.data, dtype=np.float32) / gam_scale[t.name]
            w.add_tensor(t.name, g)
            n_fold += 1
        else:
            w.add_tensor(t.name, t.data, raw_dtype=t.tensor_type)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file(progress=False)
    w.close()

    smin = min(x[1] for x in s_stats)
    smax = max(x[2] for x in s_stats)
    if imx_in is None:
        print(f"folded {n_fold} tensors ({len(gam_scale)} gammas, "
              f"{len(col_scale)} weights), alpha={a.alpha}, NO imatrix; "
              f"s range [{smin}, {smax}]; MTP blk.24 untouched (no Grams)")
        return

    # --- transform the imatrix --------------------------------------
    ir = GGUFReader(imx_in)
    iw = GGUFWriter(out_imx, ir.get_field("general.architecture").contents()
                    if ir.get_field("general.architecture") else "imatrix")
    for key, f in ir.fields.items():
        if key.startswith("GGUF.") or key == "general.architecture":
            continue
        sub = f.types[-1] if f.types[0] == gguf.GGUFValueType.ARRAY else None
        iw.add_key_value(key, f.contents(), f.types[0], sub_type=sub)
    n_imx = 0
    have = {t.name for t in ir.tensors}
    for c, s in col_scale.items():
        if c + ".in_sum2" not in have:
            print(f"[imatrix] WARNING no entry for {c}")
    for t in ir.tensors:
        if t.name.endswith(".in_sum2") and t.name[:-8] in col_scale:
            s = col_scale[t.name[:-8]]
            v = np.asarray(t.data, dtype=np.float32) / (s * s)
            iw.add_tensor(t.name, v)
            n_imx += 1
        else:
            iw.add_tensor(t.name, np.asarray(t.data),
                          raw_dtype=t.tensor_type)
    iw.write_header_to_file()
    iw.write_kv_data_to_file()
    iw.write_tensors_to_file(progress=False)
    iw.close()

    smin = min(x[1] for x in s_stats)
    smax = max(x[2] for x in s_stats)
    print(f"folded {n_fold} tensors ({len(gam_scale)} gammas, "
          f"{len(col_scale)} weights), alpha={a.alpha}, "
          f"{n_imx} imatrix entries rescaled; "
          f"s range [{smin}, {smax}]; MTP blk.24 untouched (no Grams)")


if __name__ == "__main__":
    main()
