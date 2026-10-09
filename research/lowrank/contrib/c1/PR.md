# DRAFT NOTES - NOT A READY-TO-POST PR DESCRIPTION

llama.cpp's AGENTS.md forbids AI-written PR descriptions ("non-overridable").
This file is an evidence pack and a suggested structure. Rewrite the prose in
your own words before opening the PR, and keep the AI-usage disclosure line.

Branch: `imatrix-blind-spots` (2 commits on top of b10865 / d4389a4dd)
Patches: `0001-quantize-*.patch`, `0002-imatrix-*.patch` (this directory)

```
 src/llama-quant.cpp       | 19 +++++++++-
 tools/imatrix/imatrix.cpp | 88 +++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 106 insertions(+), 1 deletion(-)
```

---

## Overview

`llama-imatrix` only collects statistics for tensors that the compute graph
runs. NextN/MTP (draft) layers are never executed during calibration, so
their tensors get no imatrix entry. Two consequences:

1. `llama-quantize` hard-fails for every very-low-bit type (IQ1/IQ2/IQ3_XXS,
   Q2_K_S) with `Missing importance matrix for tensor blk.24.attn_k.weight in
   a very low-bit quantization` and no hint about what to do. Sub-3-bit
   quants of any MTP-preserved GGUF are impossible with stock tooling unless
   the user already knows the `--tensor-type "blk\.24\.=q5_k"` workaround.
2. For k-quant types the same tensors are quantized without imatrix
   guidance, silently.

This PR makes both visible and makes (1) succeed:

- **quantize**: when `--imatrix` is given and a tensor that needs imatrix data
  has no entry, the tensor is quantized as Q4_K instead of failing. One
  warning per tensor plus a summary count at the end. Q4_K is chosen because
  it needs no imatrix, it is the type the low-bit mixes already use for
  their most sensitive tensors (attn_v, MoE attn_k), and it is never a
  downgrade from the requested type (this addresses the downcast concern
  raised on #23575). Without `--imatrix` the existing hard error is
  unchanged. The fallback uses the existing `tensor_type_fallback()` so
  unusual shapes still get the normal shape fallback.
- **imatrix**: at the end of a run, the tensor names in the model file(s) are
  compared with the collected statistics and the tensors with no data are
  listed, grouped per block, with a one-line hint. Names are read from the
  GGUF file (metadata only, split-aware) because the model loader drops
  tensors the graph does not use ("model has unused tensor ... -- ignoring"),
  so they are not in the loaded model. The existing "entry has no data" /
  "partial data" warnings in `save_imatrix` are per-entry checks for
  tensors the collector *did* see (unexercised experts); this check covers
  tensors it never saw. `ssm_conv1d` / `shortconv.conv` weights are skipped
  because they are not `mul_mat` inputs and `llama-quantize` does not
  quantize them.

Default-behaviour note: the quantize fallback is on by default (no new flag).
On #23575 @bartowski1182 asked for an opt-in flag, but that was for a design
that *downcast* to Q4_0; this one only upcasts, so the worst case is a
slightly larger file where previously there was a hard error. If the
maintainers still prefer opt-in, the check can be gated on a
`llama_model_quantize_params` field in a follow-up.

## Before / after

Model: Qwen3.5-0.8B BF16 (25 blocks, `qwen35.nextn_predict_layers = 1`, so
blk.24 is the NextN layer). Imatrix: 40 chunks of wikitext-2, 186 entries.
CPU-only build.

### llama-quantize ... IQ3_XXS

Before (exit 1):
```
[ 321/ 335] blk.24.attn_k.weight                 - [  1024,    512,      1,      1], type =   bf16,
====== llama_model_quantize_impl: did not find weights for blk.24.attn_k.weight

============================================================
Missing importance matrix for tensor blk.24.attn_k.weight in a very low-bit quantization
The result will be garbage, so bailing out
============================================================

llama_model_quantize: failed to quantize: Missing importance matrix for tensor blk.24.attn_k.weight in a very low-bit quantization
llama_quantize: failed to quantize model from '.../Qwen3.5-0.8B-BF16.gguf'
```

After (exit 0):
```
llama_model_quantize_impl: blk.24.attn_k.weight                 - no imatrix data, using q4_K instead of iq2_s
llama_model_quantize_impl: blk.24.attn_q.weight                 - no imatrix data, using q4_K instead of iq2_s
llama_model_quantize_impl: blk.24.ffn_down.weight               - no imatrix data, using q4_K instead of iq3_xxs
llama_model_quantize_impl: blk.24.ffn_gate.weight               - no imatrix data, using q4_K instead of iq3_xxs
llama_model_quantize_impl: blk.24.ffn_up.weight                 - no imatrix data, using q4_K instead of iq3_xxs
llama_model_quantize_impl: blk.24.nextn.eh_proj.weight          - no imatrix data, using q4_K instead of iq3_xxs
...
llama_model_quantize_impl: model size  =  1475.05 MiB (16.01 BPW)
llama_model_quantize_impl: quant size  =   379.83 MiB (4.12 BPW)
llama_model_quantize_impl: WARNING: 6 tensor(s) had no imatrix data and were quantized with a fallback type
```
(blk.24.attn_v and blk.24.attn_output are not listed because the IQ3_XXS mix
already gives them q4_K / iq3_s, which need no imatrix. No warning for any
other block.)

### llama-imatrix -c 512 --chunks 2

Before: no warning at all (only the loader's per-tensor "model has unused
tensor blk.24.* -- ignoring" lines at load time, which say nothing about
imatrix coverage).

After:
```
Final estimate: PPL = 16.5378 +/- 2.45705

check_coverage: 8 weight tensor(s) received no activation data:
check_coverage:   blk.24.* (8 tensors)
check_coverage: quantization of these tensors will not use imatrix data
```
Nothing else is listed (the 18 `ssm_conv1d` weights of the linear-attention
blocks are correctly skipped).

## Test evidence

- CPU build (`-DGGML_CUDA=OFF`, Release): no warnings in the touched files;
  `git diff --check` clean.
- `test-quantize-fns`: pass.
- Regression checks with the same model/imatrix:
  - `--dry-run IQ3_XXS --imatrix`: same 6 warnings, size 379.83 MiB, summary line printed.
  - `IQ3_XXS` **without** `--imatrix`: still fails with the existing
    "ERROR: this quantization requires an importance matrix!" (unchanged path).
  - `Q4_K_M --imatrix`: 0 fallback warnings, output identical in type to before.
  - `IQ2_M --imatrix`: 6 fallbacks (all `iq2_s -> q4_K`), succeeds.
- Output file loads and runs: `llama-perplexity -c 512 --chunks 2 -ngl 0`
  - IQ3_XXS output (4.12 BPW): PPL = 20.2835 +/- 3.01724
  - BF16 reference, same settings: PPL = 16.5378 +/- 2.45705
- MTP inference with the fallback-quantized draft layer: `llama-cli
  --spec-type draft-mtp --spec-draft-n-max 3 -ngl 0 -n 48 --temp 0 -st`.
  With MTP on, the loader no longer reports blk.24.* as unused (it is loaded
  and used for drafting), generation is coherent, 8.4 t/s vs 6.8 t/s for the
  BF16 file on 4 CPU threads. (llama-cli prints no acceptance-rate stats.)

## Additional information

- #23575 (closed, unmerged): "llama-quantize: use static quantization level
  for tensors missing from imatrix data". Same problem; the review there
  asked for (a) no downcasting and (b) using the "requires imatrix" helper
  to decide. This PR follows both. The (1)+(2) approach here was proposed
  in a comment on that PR on 2026-09-08.
- #26903 is a merged PR about the nemotron `--mtp` conversion export
  (lm-head quant scales). It is not about imatrix coverage, so it is not
  referenced as prior discussion of this problem.
- #23258 (abandoned) prototyped running the MTP graph during collection;
  that would give real statistics for the draft layer and is complementary.

## Requirements

- I have read and agree with the [contributing guidelines](https://github.com/ggml-org/llama.cpp/blob/master/CONTRIBUTING.md)
- AI usage disclosure: YES - <describe: code drafted with Claude Code from
  a design you own (fallback type, default-on, coverage check via GGUF
  metadata); you reproduced the failure, reviewed every line and ran the
  verification yourself>

## Not verified / known limits (for your judgement before posting)

- The coverage check's split-model path (`split.count > 1`) is untested here
  (no split model on this box).
- The coverage filter mirrors the collector's `blk.*` rule, not
  `tensor_allows_quantization()`. On archs with small 2D `blk.*` weights that
  are not `mul_mat` inputs and that quantize also skips (e.g. some RWKV
  `time_mix_*`, Gemma3n `altup`/`laurel`) the list may include harmless
  extra names. Not tested on those archs.
- Manual `--tensor-type` overrides that pick a very-low-bit type for an
  imatrix-less tensor are also upgraded to Q4_K (with the warning) rather
  than failing.
