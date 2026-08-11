#!/usr/bin/env python3
# ⚠ see review/ corrections 2026-08-04
"""beast-rank: geometry-aware compression of GGUF models via
full-covariance low-rank residual correction ("full covariance" =
block-diagonal-8 above 8192-dim inputs, e.g. 27B ffn_down; ladder-
relative results are paired-tie parity / wins-from-below on the
calibration distribution -- see RESULTS_ROLLUP.md header).

WHEN NOT TO USE THIS RECIPE (E27 lesson, added 2026-08-04 per
coherence-audit P2.3): at >=2.5 bpw a PURE-QUANT mixed-TYPE base at
the same bytes -- Q3_K_S + attn_k/v/output->q4_k + partial attn_qkv
promotion, i.e. the E27 control built with llama-quantize
--tensor-type overrides alone -- matches-or-beats this tool's default
MIXED+fc output with NO adapter and NO decode tax (E27: control KLD
0.0723 vs flagship 0.0863 at equal bytes, paired). The corrected
recipe below only pays where no ladder/mix point exists: the
sub-2.5-bpw lane (E15 regime). Defaults are kept for that lane; for
>=2.5 bpw emit the control-style quantize command instead.

The distilled method of the 2026-08 campaign (RESULTS_ROLLUP.md):
  1. capture  — activation Grams + imatrix on calibration text
                (patched llama-imatrix, LLAMA_IMATRIX_GRAM_DIR)
  2. base     — mixed-precision requant: cheap bits where correction is
                strong (small-dim tensors), one tier up where it is not
                (FFN); MTP layers pinned
  3. correct  — whitened rank-r residual factors (QERA closed form,
                Cholesky of the damped Gram, projection form), Q8_0,
                exported as a stock-servable LoRA-form GGUF
  4. report   — bytes, PPL, KLD/top-1 vs the reference

Usage:
  beastrank.py --model ref.gguf --calib text.txt --out-dir out/ \
      [--ffn-type q3_k] [--rest-type q2_k] [--rank 128] \
      [--mtp-blk N] [--chunks 64] [--eval-text wiki.test.raw]

Serve the result with stock llama.cpp:
  llama-server -m out/base.gguf --lora out/correction.gguf
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
BIN = REPO / "llama.cpp" / "build" / "bin"
sys.path.insert(0, str(HERE / "experiments" / "04-served-v0"))

FFN_KINDS = ["ffn_up", "ffn_gate", "ffn_down"]


def run(cmd, env=None, tail=3):
    e = dict(os.environ)
    if env:
        e.update(env)
    print(f"+ {' '.join(str(c) for c in cmd)}", flush=True)
    r = subprocess.run([str(c) for c in cmd], env=e, capture_output=True,
                       text=True)
    lines = (r.stdout + r.stderr).strip().splitlines()
    for ln in lines[-tail:]:
        print(f"  {ln}", flush=True)
    if r.returncode != 0:
        sys.exit(f"FAILED ({r.returncode}): {cmd[0]}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="reference GGUF "
                    "(BF16/F16/Q8/Q6 — the artifact to approximate)")
    ap.add_argument("--calib", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--ffn-type", default="q3_k")
    ap.add_argument("--rest-type", default="Q2_K",
                    help="base file type (quantize positional arg)")
    ap.add_argument("--rank", type=int, default=128)
    ap.add_argument("--mtp-blk", type=int, default=None,
                    help="MTP/NextN block index to pin at q5_k (draft "
                         "layers are invisible to calibration)")
    ap.add_argument("--chunks", type=int, default=64)
    ap.add_argument("--eval-text", default=None)
    ap.add_argument("--skip-capture", action="store_true")
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gram_dir = out / "grams"
    gram_dir.mkdir(exist_ok=True)
    imat = gram_dir / "diag.imatrix.gguf"
    base = out / "base.gguf"
    adapter = out / "correction.gguf"

    if not args.skip_capture:
        print("== [1/4] capture: Grams + imatrix ==", flush=True)
        run([BIN / "llama-imatrix", "-m", args.model, "-f", args.calib,
             "-ngl", "99", "--chunks", str(args.chunks), "-o", imat],
            env={"LLAMA_IMATRIX_GRAM_DIR": str(gram_dir)})

    print("== [2/4] base: geometry-aware mixed requant ==", flush=True)
    cmd = [BIN / "llama-quantize", "--allow-requantize",
           "--imatrix", imat]
    if args.mtp_blk is not None:
        cmd += ["--tensor-type", rf"blk\.{args.mtp_blk}\.=q5_k"]
    for k in FFN_KINDS:
        cmd += ["--tensor-type", f"{k}={args.ffn_type}"]
    cmd += [args.model, base, args.rest_type]
    run(cmd, tail=1)

    print("== [3/4] correct: whitened factors on non-FFN ==", flush=True)
    rank_map = ",".join(f"{k}=0" for k in FFN_KINDS)
    run(["python3", HERE / "experiments" / "04-served-v0" /
         "extract_adapter.py", args.model, base, imat,
         "-o", adapter, "--rank", str(args.rank),
         "--rank-map", rank_map, "--q8-factors", "--rsvd",
         "--compute32", "--gram-dir", gram_dir],
        env={"OMP_NUM_THREADS": "24"}, tail=2)

    print("== [4/4] report ==", flush=True)
    gb = lambda p: Path(p).stat().st_size / 1e9  # noqa: E731
    print(f"reference : {gb(args.model):6.2f} GB  {args.model}")
    print(f"base      : {gb(base):6.2f} GB  {base}")
    print(f"correction: {gb(adapter):6.2f} GB  {adapter}")
    print(f"total     : {gb(base) + gb(adapter):6.2f} GB  "
          f"({gb(args.model) / (gb(base) + gb(adapter)):.2f}x vs ref)")
    if args.eval_text:
        o = run([BIN / "llama-perplexity", "-m", base, "--lora", adapter,
                 "-f", args.eval_text, "-ngl", "99", "--chunks", "20"],
                tail=1)
    # E27 comparison notice (coherence-audit P2.3): at >=2.5 bpw the
    # pure-quant mixed-TYPE control matched-or-beat this recipe at
    # equal bytes, adapter-free. Print the alternative next to the
    # serve line so no user ships the losing recipe unwarned.
    print("\nNOTE (E27): if your target is >=2.5 bpw, a pure-quant "
          "mixed-TYPE base at the same bytes matches-or-beats this "
          "output with no adapter and no decode tax. Control-style "
          "command:")
    print(f"  llama-quantize --allow-requantize --imatrix {imat} "
          f"--tensor-type 'attn_(k|v|output)=q4_k' "
          f"{args.model} <out.gguf> Q3_K_S")
    print("This corrected recipe pays only below ~2.5 bpw "
          "(see experiments/27-bf16-rederivation/README.md).")
    print(f"\nserve: llama-server -m {base} --lora {adapter}")


if __name__ == "__main__":
    main()
