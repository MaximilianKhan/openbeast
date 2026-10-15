# E36-E — free-grid GPTQ baseline for the frozen-grid re-rounder

A baseline for E13 (`../../13-rerounder/`) that the referee asked for: GPTQ
with the grid allowed to move, at the identical Q2_K format and the
identical byte count. Built and validated on CPU only (2026-10-15) while
the GPU was leased to E36-A.

## Method

Input: the BF16 reference, a Q2_K GGUF produced by llama-quantize, the
Gram directory, and the imatrix llama-quantize used for that file.
Coverage is E13's exactly: every tensor that is Q2_K in the base file,
2-D with both dims ≥ 64, and has a Gram (via E13's `_GRAM_SOURCE`:
q/v use attn_k's Gram, ffn_gate uses ffn_up's, attn_gate uses
attn_qkv's). Everything else (ssm_alpha/beta, token_embd, the
Q3_K/Q4_K/Q5_K tensors, the MTP-pinned layer) is copied byte for byte.

Per tensor (W is n_out × n_in, rows vectorized):

1. S = Gram / count; H = S + λI with λ = 0.01 · mean(diag S), escalated
   ×10 on PD failure; U = chol(inv(H))ᵀ (upper). This is E13's
   `gptq_hinv`, imported, not copied.
2. Sweep columns j = 0 … n_in−1 in natural order (no act-order), with
   E13's lazy batching (128-column blocks, one GEMM per block tail).
3. **At j ≡ 0 (mod 256)** — entry to a Q2_K superblock — call
   llama.cpp's `ggml_quantize_chunk(GGML_TYPE_Q2_K, …)` (ctypes,
   `llama.cpp/build/bin/libggml-base.so`) on the current, error-updated
   W[:, j:j+256] for all rows, with the imatrix slice for those 256
   columns. Keep the returned d, dmin and 16 packed 4-bit scale/min
   indices as this superblock's grid. All feedback from earlier columns
   has already landed in W at this point (256 is a multiple of the
   lazy-batch width).
4. For each column inside the superblock: q = clip(rint((w + m)/d), 0, 3)
   with that column's sub-block (d·sc, dmin·mn); decode;
   err = (w − ŵ)/U[j,j]; propagate err·U[j, j+1:] to every later column,
   including later superblocks. Identical to E13's inner loop. If a
   sub-block scale is 0, every code decodes to −m; the quantizer's own
   code is kept so bytes match llama-quantize.
5. Pack the codes into the superblock's qs bytes. Output layout, tensor
   order, metadata and file size equal the base file's.

`imatrix` normalisation mirrors `tools/quantize/quantize.cpp`
`load_imatrix` (GGUF format): weights = `in_sum2 / counts` in float32,
per expert slice, count 0 → 1. `remap_imatrix` is the identity (no pruned
layers), so names map directly. `quantize_q2_K`'s grid fit is
per-superblock (its `sigma2` and imatrix slice are both per 256
elements), which is why slicing works; gate 1b checks this on real data.

### How it differs from E13 and from canonical GPTQ

| | E13 frozen-grid | this (free-grid) | GPTQ (Frantar et al.), groupsize g |
|---|---|---|---|
| grid source | llama-quantize on the original W, never changed | llama.cpp's Q2_K quantizer on the error-updated W | min/max (or MSE) fit of the group on the error-updated W |
| grid granularity | Q2_K: 256-col superblock (fp16 d/dmin) × 16 sub-blocks of 16 (4-bit scale/min) | same format; the whole superblock (all 16 sub-blocks) is refit once, at superblock entry | one scale/zero per g columns, refit at each group entry |
| static vs dynamic groups | static (all grids fixed before the sweep) | dynamic, at 256-col resolution | dynamic by default (`static_groups` is the option E13 resembles) |
| grid-fit objective | imatrix-weighted diagonal (llama.cpp) | same, applied to updated weights; not Gram-aware | unweighted |
| codes | nearest on the grid + full-Gram error feedback | same | same |
| act-order | none | none | optional |

One consequence of refitting at 256-column granularity: the scales of
sub-blocks 2…16 of a superblock are chosen before the feedback from
columns earlier in the same superblock has arrived. A finer variant (keep
d/dmin, refit each 16-column sub-block scale at its own entry) is not
implemented; it would need a re-implementation of the 4-bit scale coding
outside ggml rather than a call to llama.cpp's quantizer.

## Gates (Qwen3.5-0.8B, CPU, 12 threads)

Inputs: `weights/research-staging/Qwen3.5-0.8B-BF16.gguf`,
`../../22-qwen35-08b/q35-08b-Q2_K.gguf`, `data/gram08b` (wiki.train, 40
chunks, its `diag.imatrix.gguf` is the imatrix recorded in the Q2_K
file's `quantize.imatrix.file`). 96 covered tensors.

**Gate 1 — identity** (`gates-08b-all.log`):
- 1a, `ggml_quantize_chunk` on whole rows of the BF16 weights with the
  normalised imatrix: 96/96 tensors byte-identical to the base file.
- 1b, the same, one 256-column slice at a time: 96/96 byte-identical.
- 1c, the full free-grid sweep with feedback off (`--no-feedback`):
  96/96 tensors byte-identical; the whole output file (`cmp`) is
  byte-identical to `q35-08b-Q2_K.gguf`.

The same gate on the 27B (`gates-27b-legacygram.log`, base
`h27bf16-Q2_K.gguf` built by b10254, imatrix `gram27b-bf16`): 1a and 1b
256/256, 1c on the 5 tensors run. So the current libggml-base Q2_K
quantizer reproduces that file too.

**Gate 2 — objective**, tr(E S Eᵀ) with E = W_BF16 − Ŵ and S the
calibration Gram (in-sample: the same Gram drives the sweep):

| tensors (0.8B) | n | E13 / bare | free-grid / E13 (mean, min–max) |
|---|---|---|---|
| attn_gate | 18 | 0.425 | 0.875 (0.865–0.885) |
| attn_q | 6 | 0.423 | 0.874 (0.859–0.884) |
| attn_k | 6 | 0.420 | 0.874 (0.864–0.891) |
| ffn_gate | 24 | 0.460 | 0.882 (0.869–0.896) |
| ffn_up | 24 | 0.518 | 0.889 (0.877–0.903) |
| ssm_out | 18 | 0.489 | 0.906 (0.794–0.947) |
| **sum over all 96** | | **0.432** | **0.880** (free/bare 0.380) |

Free-grid ≤ frozen-grid on 96/96 tensors. On 5 27B tensors (legacy wiki
Grams `gram27b-bf16`, timing/sanity only): free/E13 = 0.79–0.87, sum
0.814 (free/bare 0.435, E13/bare 0.535).

This is the proxy objective only. Whether it carries to KLD/PPL is the
question the GPU evaluation answers; E13's own history (lower whitened
error, worse KLD under composition) is the reason not to assume it does.

## Full 0.8B run

`q35-08b-Q2K-freegrid-wiki.gguf` (sha256 `e8519646…5ef2`), wall 69 s
(60 s compute), codes cached in `codes-08b-wiki/`. Verified
(`verify-08b.log`): file size 436,408,832 bytes = base; tensor list
(name, type, shape, data offset) identical for all 335 tensors; metadata
identical; only the 96 covered tensors differ. In those, 100% of
superblocks have changed codes and 76% have a changed grid (the first
superblock of each row sees no feedback yet, so its grid is the base's).
Not evaluated — scoring is a GPU job.

## Wall time and resources

0.8B: 69 s. 27B, measured on the first 5 tensors (12 threads): attn_gate
6144×5120 ≈ 7.5 s, ffn_gate/ffn_up 17408×5120 ≈ 20 s each, ssm_out
5120×6144 ≈ 9 s; the same as E13's sweep within noise (the quantizer
calls are a small fraction). 256 tensors → ≈ 60–70 min compute plus
≈ 5 min to load and to write the 9 GB GGUF. The codes cache holds the
covered tensors' Q2_K bytes, ≈ 5.1 GB at 27B. Peak RAM a few GB
(one tensor in fp32 plus its Gram factor; the BF16 file is mmapped).

## Caveats

- The base file must be the one llama-quantize built with the given
  imatrix; gate 1 is the check. Run `gates.py` (1a/1b take ~5 min at
  27B) before trusting a new base/imatrix pairing.
- `--imatrix` is the base file's imatrix (its `quantize.imatrix.file`),
  not the diagonal of the Gram capture used for the sweep. With
  `gram27b-e36-mixed` this means the grid fit is weighted by the wiki
  imatrix while the feedback uses the mixed Gram — the same split E13
  has (its frozen grid comes from that same wiki imatrix). Passing the
  new capture's `diag.imatrix.gguf` instead is a different arm, and gate 1
  no longer applies to it.
- Grams with several blocks (ffn_down at 27B) are swept block by block
  as in E13, with feedback not crossing block boundaries; blocks must be
  256-aligned (asserted). No covered tensor in either model uses them,
  since ffn_down is Q3_K in both files.
- `E36_THREADS` sets the quantizer thread count (default 12);
  `E36_GGML_BASE` overrides the library path. The script sets
  `CUDA_VISIBLE_DEVICES=""` and 12 BLAS threads if not already set;
  libggml-base has no GPU code anyway.

## Files

- `e36_freegrid.py` — the tool (imports E13's Gram source map, damping
  and sweep pieces).
- `gates.py` — gates 1 and 2.
- `gates-08b.log`, `gates-08b-all.log`, `gates-27b-legacygram.log`,
  `run-08b-wiki.log`, `verify-08b.log`.
