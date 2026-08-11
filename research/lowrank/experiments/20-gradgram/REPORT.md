> ⚠ see review/ corrections 2026-08-04

# E20 - output-gradient Gram capture (grad Grams for M2/M3)

Status: SHIPPED and live-verified on Qwen3-0.6B-Q8_0, 2026-08-04.
Deliverable: T = sum_t g g^T per weight tensor, g = dLoss/d(pre-activation
output row), 196 tensors (28 layers x 7 kinds) at
`research/lowrank/data/gradgram06b/` (2.9 GB), schema-compatible with the
E10 input Grams so python consumers are shared.

## Approach

New tool `tools/gradmatrix/gradmatrix.cpp` (target `llama-gradmatrix`),
modeled on `examples/training/finetune.cpp` + the imatrix Gram patch
(`tools/imatrix/imatrix.cpp` gram_*). It runs `llama_opt_epoch`
forward+backward over calibration text with learning disabled and captures
gradients through the sched eval callback (same hook imatrix uses; it is
armed by the warmup decode at `src/llama-context.cpp:1350` and persists on
the sched, which `ggml_opt_eval` shares).

Key structural facts the design rests on:

- In the backward graph, for each forward `mul_mat(W, x)` the input
  gradient is built as `dx = out_prod(W, transpose(G))`
  (`ggml/src/ggml.c:6808`, MUL_MAT backward). That OUT_PROD node carries
  the weight tensor's name on src[0] and the output gradient
  G = [n_out, T] on src[1]->src[0]. We hook exactly those nodes - no
  dependence on weight-grad (dW) nodes existing.
- That matters because `llama_set_param` is F32-only
  (`src/llama-context.cpp:3266`): quantized weights can never be params,
  so dW nodes do not exist for them. Gradient flow is kept alive by the
  F32 norm weights, which are params in every layer. Q8_0 works directly
  (CPU has `out_prod_q_f32`, `ggml/src/ggml-cpu/ops.cpp:4363`); no F16
  conversion of the model was needed.
- Learning disabled: SGD with constant alpha = 1e-30 (ggml-opt asserts
  alpha > 0 at `ggml/src/ggml-opt.cpp:812`; 1e-30 makes `w - alpha*g`
  round to exactly `w` in fp32, relative change < 2^-24, and only norm
  tensors are params anyway). Verified: per-window loss shows no drift
  over 32 windows (3.49 -> 3.75 -> 3.38, tracks data difficulty only).
- Accumulation mirrors the E10 patch verbatim: fp32, one Gram per tensor
  (outputs are distinct per projection, unlike the shared-input groups),
  blocked 8x above 8192 dims (all 0.6B dims are below), threaded rank-k
  update, written as `<tensor>.gradgram.bin` + `gradgrams.json`
  manifest `{n, blocks, count}`.

## The seam: llama.cpp training never backprops through the KV cache

This is the report's most load-bearing finding — independently found,
NOT first: llama.cpp #21037 (Bug 2) already reports SET_ROWS missing
from the backward pass, with a proposed gradient (review 2026-08-04).
Note the two reports disagree on failure mode (#21037 saw a loud
GGML_ASSERT abort in the backward build; we analyze a silent
zero-gradient path via leaf disconnection) — reconcile before posting
anything upstream; our silent-path analysis + KV-bypass workaround go
as a comment/reopen on #21037, not a new issue. Two independent breaks:

1. The cache write is `ggml_set_rows` (`src/llama-kv-cache.cpp:1333`),
   which has no backward pass; `ggml_build_backward_expand` aborts on it
   (`ggml/src/ggml.c:7276`, "inplace operations are currently not
   supported").
2. Even pre-set_rows, the attention read (`get_k`,
   `src/llama-kv-cache.cpp:1249`) is a view of the cache LEAF tensor, not
   of the write node - the read is edge-disconnected from the write in
   autodiff terms. Upstream `llama-finetune` therefore trains with ZERO
   gradient through attention scores into wk/wv (only the dead-end path
   into the cache write ever sees k_cur/v_cur grads). Consistent with the
   example's "technically functional, WIP" label; worth knowing before
   ever trusting llama.cpp finetuning for attention weights.

Fix (env-gated on `LLAMA_GRADMATRIX_DIR`, `src/llama-graph.cpp`
build_attn for `llm_graph_input_attn_kv`, ~line 2775): when
n_kv == n_tokens, skip the cache store entirely and feed `k_cur`/`v_cur`
straight to `build_attn_mha` (the same shapes the no-cache BERT path
passes; `build_attn_mha` infers layout from strides at line 2509). This
is mathematically identical to the cache path for the training loop's
regime - fresh cache per window, whole window in one ubatch - hence the
tool enforces `-c == -b == -ub`. Reserve/warmup graphs (n_kv != n_tokens)
keep the normal path. With the bypass, gradients flow through softmax
into K and V; the smoke run's attn_k/attn_v Grams are nonzero and full
structure (they would be exactly zero without it).

Two support patches, same env gate:
- `src/llama-context.cpp:2351` graph_max_nodes x8 (backward needs several
  nodes per forward node; default capacity overflowed).
- `src/llama-graph.cpp:468` `llm_graph_input_attn_kv::set_input` guards
  the k/v idxs inputs by buffer presence (unallocated when the store is
  skipped), matching the existing kq_mask guard style.
- `ggml/src/ggml.c:7275` (not gated, error-path only): prints op/name of
  the offending node before the backward view assert fires.

## Run

32 non-overlapping 512-token windows of wikitext-2 train = 16384 tokens,
CPU-only build (training README requires no CUDA backend for CPU
training), 24 threads, ~3.5 min wall. Mean loss 3.3755 +- 0.0734 per
token (ppl ~29.2 at 512 ctx - plausible for Qwen3-0.6B, confirms mask and
bypass semantics are right).

## Sanity gates

(a) Symmetric PSD: max asymmetry over all 196 Grams is exactly 0
(deterministic accumulation order). Eigenvalues bounded below by fp32
accumulation noise only: worst evmin/evmax = -2.7e-7; worst evmin
relative to mean diagonal = -1.5e-4 (blk.27.attn_v - last-layer K/V
grads are tiny, noise-floor limited). slogdet finite and positive for
194/196 at damping 1e-8*(trace/n)*I, and for all 196 at >= 2e-4;
consumers should damp at 1e-3 relative for safety.

(b) diag(T) vs the E07 kind-probe (dKLD/MB: attn_k .0128 > attn_v .0074 >
ffn_up .0038 > ffn_gate .0035 > attn_q .0034 > ffn_down .0029 > attn_o
.0015), Spearman over the 7 kinds:
- grad-side alone, trace(T)/count: rho = -0.29. NEGATIVE - the grad-only
  proxy fails exactly the way the input-only proxy failed in M2's first
  pass (attn_output has the loudest g, residual-stream gradient, but the
  lowest measured sensitivity). Named result: EITHER single side is a
  trap.
- two-sided trace(S)*trace(T) (Kronecker-Fisher mass, S from the E10
  input Grams, shared-input mapping q/k/v<-attn_k, gate/up<-ffn_up):
  rho = +0.39 (per-element +0.29). Positive, weak (n=7, ffn_down is the
  outlier via its loud input Grams - the known super-weight geography).
  Directional support for two-sided Fisher; M2's rerun must use the full
  closed-form with both factors, not trace mass.

(c) Counts: all 196 tensors report count = 16384 = 32 x 512, matching
tokens processed exactly.

Bonus, prices M3 directly - leftover correlation structure of T after a
diagonal whitener (nats/dim, damp 1e-6, mean over layers):
attn_v 1.46, attn_k 1.32, attn_q 0.63, ffn_up 0.37, ffn_gate 0.27,
ffn_down 0.22, attn_output 0.22. Note the INVERSION vs the input side
(where attn_output was highest at 0.57): the grad-side headroom lives in
attention K/V, the input-side headroom in attn_output/ffn_down. Two-sided
whitening is the only lane that collects both.

## Usage

```
cmake -B build-gradgram -DGGML_CUDA=OFF -DCMAKE_BUILD_TYPE=Release \
      -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=OFF -DLLAMA_CURL=OFF
cmake --build build-gradgram --target llama-gradmatrix -j 24

LLAMA_GRADMATRIX_DIR=research/lowrank/data/gradgram06b \
LLAMA_GRADMATRIX_CHUNKS=32 \
  build-gradgram/bin/llama-gradmatrix \
  -m weights/Qwen3-0.6B-Q8_0.gguf \
  -f research/lowrank/data/wikitext-2-raw/wiki.train.raw \
  -c 512 -b 512 -ub 512 -t 24
```

Env: `LLAMA_GRADMATRIX_DIR` (required; also gates the kv bypass +
graph-size bump in llama), `LLAMA_GRADMATRIX_CHUNKS` (default 32),
`LLAMA_GRADMATRIX_MAXDIM` (default 8192, block-8x threshold).

## Scope and limits

- Dense, non-SWA, attn-kv archs only. MUL_MAT_ID (MoE) is not collected;
  the iswa/mla/attn-k build_attn variants are unpatched and would still
  hit the set_rows backward abort.
- The bypass requires the whole window in one ubatch (`-c == -b == -ub`);
  with a mismatch it silently keeps the cache path and K/V Grams come out
  zero - check trace > 0 before consuming.
- The loss is mean-per-token cross-entropy, so g carries a 1/512 window
  scale; it is constant across the run and cancels in any whitening or
  allocation ratio.
- 27B capture: untested; will need the blocked path (dims > 8192) which
  is live but unexercised, plus F32-norm coverage checking for the
  hybrid layers.

## qwen35 hybrid capture (Qwen3.5-0.8B-BF16, 2026-08-04)

Status: SHIPPED, tier-2 as defined - all FFN + all classic-attention
layers + the two linear-branch projections that sit above the gradient
cut. 132 Grams at `research/lowrank/data/gradgram08b/` (3.2 GB), counts
all 16384, no all-zero Grams, exactly symmetric, fully PSD (worst
eigmin = 0), slogdet finite+positive for all 132 at 1e-3 damping.
Mean loss 3.1740 +- 0.0715 per token over 32x512 wikitext tokens.

Census: 24 layers in the default graph (blk.24 is the MTP/nextn layer,
not built). 6 classic attention layers (blk 3,7,11,15,19,23) x
{attn_q 2048, attn_k 512, attn_v 1024, attn_output} + 18 linear
(delta-net) layers x {ssm_out, attn_gate 2048} + 24 x
{ffn_gate/up 4096, ffn_down 1024}.

The original "silent exit 0 with zero files" did not reproduce - the
binary aborted (exit 134) at every failure stage when rerun in the
foreground. Root causes fixed, in the order they surfaced:

1. SIGMOID had no backward pass in ggml (`ggml/src/ggml.c` unary
   switch) - qwen3.5's classic layers gate the attention output with
   sigmoid, so this blocked ALL attention Grams. Implemented exact:
   dsigmoid = y - y^2 (upstreamable).
2. The delta-net branch is built from ops with no backward
   (`ggml_gated_delta_net`, `tri`/`solve_tri`, `ssm_conv`, `l2_norm`,
   `set_inplace`, conv-state `concat`, state `set_rows`;
   `src/models/delta-net-base.cpp`). Fix: env-gated GRADIENT CUT in
   `ggml_build_backward_expand` (`ggml/src/ggml.c`,
   ggml_gradmatrix_cut_op) - these ops are treated as stop-gradients
   instead of aborting; gradients keep flowing past each linear layer
   through the residual stream. Any op NOT on the cut list still aborts
   loudly.
3. CPU OUT_PROD did not support BF16 src0 - the sched found no backend
   for the dx nodes. Added BF16 to the row-dequant path
   (`ggml/src/ggml-cpu/ops.cpp` out_prod switch + supports_op in
   `ggml-cpu.cpp`); it reuses the type-traits to_float route.
4. `llm_graph_input_mem_hybrid::set_input` (`src/llama-graph.cpp:1066`)
   needed the same unallocated-idxs guard as attn_kv (the kv bypass
   leaves the store inputs unallocated; qwen35's classic layers route
   through the hybrid memory input).
5. graph_max_nodes override made arch-aware (8x the arch's own formula,
   `src/llama-context.cpp:2351`) - the flat override under-budgeted
   hybrid archs which already need n_tokens*40 nodes.

What the cut means for the data (report honestly, always):

- EXACT G: ffn_* in all 24 layers, attn_q/k/v/output in the 6 classic
  layers (kv bypass active there), ssm_out (branch output projection)
  and attn_gate (z-gate projection) in the 18 linear layers - all of
  these receive their gradient from ABOVE the cut.
- APPROXIMATION: gradients reaching any layer from above ignore
  contributions that would flow THROUGH the delta-net token-mixing of
  higher linear layers (the residual path is intact). FFN/attention
  Grams in lower layers are therefore slightly under-informed; same
  caveat class as the KV-read disconnect was for upstream finetune,
  but now explicit and bounded to 18 scan branches.
- SKIPPED (all-zero by construction, not collected): attn_qkv,
  ssm_alpha, ssm_beta, ssm_conv1d - their only gradient path enters
  the cut ops. Getting them needs a real delta-net backward
  (chunked-scan adjoint) - that is the precise remaining blocker, noted
  as future work, not faked.

Fail-loud hardening (the campaign requirement): the tool now (i) probes
LLAMA_GRADMATRIX_DIR writability at startup and exits 1 before loading
the model if it is missing/unwritable, (ii) counts Gram files actually
written and exits 1 with an error if zero, (iii) warns per-tensor on
all-zero Grams. Exit 0 now guarantees at least one Gram file plus the
manifest are on disk.

Regression: 0.6B Q8_0 rerun after all changes is bit-identical
(loss 3.614811, blk.0.attn_k trace 4.342248e-02).

Usage (identical, just point at the hybrid model):

```
LLAMA_GRADMATRIX_DIR=research/lowrank/data/gradgram08b \
LLAMA_GRADMATRIX_CHUNKS=32 \
  build-gradgram/bin/llama-gradmatrix \
  -m weights/Qwen3.5-0.8B-BF16.gguf \
  -f research/lowrank/data/wikitext-2-raw/wiki.train.raw \
  -c 512 -b 512 -ub 512 -t 24
```
