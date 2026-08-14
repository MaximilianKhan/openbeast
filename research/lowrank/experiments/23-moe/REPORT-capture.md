# E23: MoE (MUL_MAT_ID) activation-Gram capture -- Qwen3.6-35B-A3B

Date: 2026-08-03/04. Status: **capture PASS, all gates green.**

## Mission

Extend the E10 activation-Gram patch in `tools/imatrix/imatrix.cpp` (which only
handled the dense MUL_MAT branch) to MUL_MAT_ID, so expert-stack corrections on
qwen35moe models can be whitened. Validate on 16 chunks of wikitext-2 train
against `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`.

## Patch

File: `/home/max/Documents/openbeast/llama.cpp/tools/imatrix/imatrix.cpp`
(local research patch, uncommitted; builds in `build-moegram/`, CPU Release,
GGML_CUDA=OFF, separate from other agents' build dirs).

- `gram_wanted()` now also matches `ffn_up_exps.` and `ffn_down_exps.`.
  `ffn_gate_exps` is intentionally NOT captured: gate and up receive the exact
  same routed input (verified bitwise below), mirroring the dense
  "ffn_up covers gate/up" convention. On the python side, map
  `ffn_gate_exps -> ffn_up_exps` when whitening gate corrections.
- Old `gram_accumulate` refactored into `gram_get` (lazy init) + packing +
  shared threaded `gram_update`. New `gram_accumulate_id` packs MUL_MAT_ID
  rows: for each token `row` and expert slot `idx`, the input row is
  `data + (idx % ne[1])*nb[1] + row*nb[2]` (same indexing as the imatrix
  accumulation in the MUL_MAT_ID branch). `ne[1] == 1` is the broadcast case
  (gate/up share one row across all 8 slots); `ne[1] == n_expert_used` is the
  per-slot case (down: each slot has its own gate*up intermediate).
- Called from the MUL_MAT_ID branch of `collect_imatrix` after the imatrix
  sums, using the already host-copied `data`. The routing ids are NOT needed:
  every (token, slot) pair holds a valid routed expert (asserted upstream), and
  the pooled design uses all pairs regardless of which expert was picked.
- Manifest schema unchanged: `grams.json = {name: {n, blocks, count}}`,
  payload `<name>.gram.bin` = f32 row-major n*n (blocks=1 at these dims).

### Fail-loud hardening (mirrors llama-gradmatrix)

`gram_save()` now counts successful writes (short writes rejected); a failed
`grams.json` write voids the capture. `main()` exits 1 with a loud error when
`LLAMA_IMATRIX_GRAM_DIR` is set but zero Gram files got written.
Verified both ways on Qwen3-0.6B-Q8_0, 2 chunks:
- read-only dir: `exit=1`, "LLAMA_IMATRIX_GRAM_DIR is set but no Gram files
  were written to ..."
- writable dir: `exit=0`, 112 entries, count=1024 (= 2 x 512), schema identical.

## Design decision: one POOLED Gram per expert stack

**Decision: a single Gram per stack input, accumulated over all routed
(token, slot) pairs, no per-expert Grams.** Rationale:

1. The shared-basis-across-experts hypothesis is exactly a statement about the
   pooled input distribution: all 256 experts of a stack read from the same
   input space (gate/up literally receive the same hidden state; down receives
   per-expert intermediates that live in one shared n_ff_exp=512 space). A
   whitening basis shared across experts must come from the union of routed
   inputs -- that is the pooled Gram.
2. Per-expert Grams are statistically hopeless at this scale: 65536 routed
   pairs / 256 experts = ~256 samples per expert for a 2048-dim Gram
   (rank-deficient by 8x), and 256 x 2048^2 floats = 4.3 GB per layer.
   Pooled: 16 MB per layer (up), 1 MB (down), full rank at 65536 samples.
3. Routed-pair counting makes the capture exactly the second-moment matrix
   whose diagonal the imatrix already stores summed per expert, which gives a
   sharp validation gate (below), and `G/count` is directly the E[x x^T] that
   `gram_whitener` expects -- no consumer change.

**Count semantics:** `count` = number of routed (token, slot) contributions
= n_tokens x n_expert_used (65536 = 8192 x 8 here). For gate/up the 8 slots
per token are identical rows (broadcast), so the pooled Gram equals
8 x the unique-token Gram; after `G/count` normalization this is identical to
the unique-token second moment. For down the 8 slots are genuinely distinct
vectors, and pooling them is the point. Known accepted cost: the broadcast case
packs 8 duplicate rows (8x accumulation work); a weight argument on
`gram_update` could remove this later, not worth the extra path at capture
scale (whole 16-chunk 35B run: ~9 min wall on 32 CPU threads).

Note: if a tensor were ever driven by both MUL_MAT and MUL_MAT_ID, its count
would mix token- and pair-semantics; does not occur for qwen35moe.

## Model ground truth (verified from the GGUF, not assumed)

arch `qwen35moe`, 40 blocks, n_embd=2048, 256 experts, 8 used,
expert_feed_forward_length=512. Hybrid attention: 30 linear-attention layers
(`attn_qkv` + `ssm_out`) and 10 full-attention layers (`attn_k`/`attn_output`).
Expert stacks per block: `ffn_gate_exps`/`ffn_up_exps` ne = [2048, 512, 256],
`ffn_down_exps` ne = [512, 2048, 256]. So src1 n_in = **2048** for gate/up and
**512** for down -- matches the brief. Every block also has a dense shared
expert (`ffn_{gate,up,down}_shexp`) plus routers.

## Validation run

```
LLAMA_IMATRIX_GRAM_DIR=.../data/gram35b build-moegram/bin/llama-imatrix \
  -m weights/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
  -f research/lowrank/data/wikitext-2-raw/wiki.train.raw \
  --chunks 16 --no-ppl -t 32 -o .../data/gram35b/diag.imatrix.gguf
```

exit=0; ~9 min wall (CPU only); output 4.0 GB in
`/home/max/Documents/openbeast/research/lowrank/data/gram35b/`:
160 grams + `grams.json` + `diag.imatrix.gguf`.

| kind | n | layers | count | logdet(damped) range |
|---|---|---|---|---|
| ffn_up_exps | 2048 | 40 | 65536 | [-6198.1, -724.8] |
| ffn_down_exps | 512 | 40 | 65536 | [-3349.6, -758.7] |
| attn_qkv | 2048 | 30 | 8192 | [-4517.0, -2453.8] |
| ssm_out | 4096 | 30 | 8192 | [-49982.9, -30828.2] |
| attn_k | 2048 | 10 | 8192 | [-4783.1, -3629.3] |
| attn_output | 4096 | 10 | 8192 | [-50904.2, -32999.9] |

### Gates (`validate_gram35b.py`, exit 0 = PASS) -- ALL PASS

1. **Symmetry**: max rel asymmetry = 0.0 exactly, every entry. (The inner loop
   computes G[i][j] and G[j][i] from the same products in the same t-order, so
   they are bitwise equal.)
2. **PSD**: worst min-eigenvalue ratio = +2.2e-10 (>= 0); `slogdet` of
   `G/count + 1e-3*mean(diag)*I` finite with sign +1 for all 160 entries.
3. **Diag vs imatrix (the pooled check)**: for MoE entries,
   `diag(G) == in_sum2.reshape(256, n).sum(axis=0)` -- max rel err 1.3e-4
   (down), 6.0e-5 (up); pure f32 summation-order noise across 65536 adds.
   Dense entries match with rel err 0.0 (identical add order). This confirms
   the routed-pair indexing reproduces the imatrix accumulation exactly.
4. **Counts**: `count == counts.sum()` from the imatrix (exact) and
   `== 65536 = 16 x 512 x 8` for MoE, `8192` dense, all entries.
5. **Bonus**: `ffn_gate_exps.in_sum2` is bitwise equal to `ffn_up_exps.in_sum2`
   on every layer -- gate/up provably share the routed input, justifying the
   single up_exps Gram.

Routing coverage note: at 16 chunks a few experts stay unrouted (save_imatrix
warns "partial data", 87.9% coverage mid-run -> 96.5-99.2% at final save on the
warned entries). This affects only the per-expert imatrix entries; the pooled
Grams are unaffected (an unrouted expert contributes no pairs to anything).

## WARNING for the python consumer (from adversarial review)

`load_imatrix` in `experiments/04-served-v0/extract_adapter.py` (and the copy
in `05-whitened-residual/whitened_census.py`) does
`counts = data.mean(); sums / counts`. That is only correct when counts has one
entry (dense). For MoE entries `*.counts` is a length-256 per-expert vector of
routed-token counts; dividing every expert's sums by the *mean* count skews each
expert's importance by its routing frequency and breaks entirely for
zero-count experts. MoE-aware consumers must:

- per-expert mean-square (for imatrix-style weighting):
  `sums.reshape(n_expert, n_in) / np.maximum(counts, 1)[:, None]`,
  masking `counts == 0` experts (llama-quantize substitutes 1.0 there);
- pooled diag second moment (to match the Gram):
  `sums.sum(axis=0) / counts.sum()`.

`gram_whitener` itself needs **no change**: `grams.json`'s `count` is the
routed-pair total and `G/count` is already the pooled E[x x^T].

## Gaps / next

- `ffn_{up,down}_shexp` (dense shared expert) are not in `gram_wanted`:
  `"ffn_up."` does not substring-match `ffn_up_shexp.`. up_shexp's input space
  is covered by the up_exps Gram (same hidden state, uniform 8x weighting
  cancels under normalization), but **down_shexp's input space (its own
  intermediate) is uncaptured** -- add `"ffn_down_shexp."` before whitening
  shared-expert down corrections.
- 8x duplicate packing for broadcast gate/up inputs (see above) if capture
  runs ever need to be faster.
- 16 chunks is a smoke-scale capture; scale chunks for production whitening.

## Artifacts

- patch: `/home/max/Documents/openbeast/llama.cpp/tools/imatrix/imatrix.cpp` (uncommitted)
- build: `/home/max/Documents/openbeast/llama.cpp/build-moegram/bin/llama-imatrix`
- data: `/home/max/Documents/openbeast/research/lowrank/data/gram35b/` (4.0 GB)
- gates: `/home/max/Documents/openbeast/research/lowrank/experiments/23-moe/validate_gram35b.py`
