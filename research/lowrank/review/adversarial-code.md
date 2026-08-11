# Adversarial code review — load-bearing research code (2026-08-04)

Lens: correctness of the code the published numbers stand on. Method: line-level
reading of the five Python tools + the imatrix.cpp Gram patch + llama.cpp's LoRA
runtime, cross-checked against experiment READMEs/RESULTS_ROLLUP.md, plus
executed numpy repro tests (scratchpad `t1_codec.py`, `t2_gptq.py`, `t3_whiten.py`).

Severity scale: KILLS (invalidates a published number) / WEAKENS (published claim
overstated, fragile, or mislabeled) / COSMETIC (real defect, no published impact).
Headline: **no KILLS found against any published RESULTS_ROLLUP.md row.** The
core math and codecs survived every numeric attack. The damage is concentrated in
claim-labeling, statistical margins, provenance mixing, and latent silent-wrong
traps.

---

## A. Verified-correct (attacks that FAILED — run, not argued)

These are the things the prompt-level suspicion targeted; all check out:

1. **Q2_K codec is bit-exact** (`experiments/13-rerounder/e13_reround.py:41-72`).
   Decoded `dl*q - ml` matches gguf-py `dequantize` to 0.0 max error on real
   tensors of `qwen3-0.6b-Q2_K-imat.gguf`; re-deriving codes from the dequant and
   repacking with `q2k_pack_codes` reproduces the original 84-byte superblocks
   **byte-identically** (element order e = g·128+s·32+b → qs byte g·32+b, shift
   2s — matches ggml's C dequant exactly). `d`@80:82, `dmin`@82:84, scales@0:16
   all correct.
2. **GPTQ Hinv convention is right** (`e13_reround.py:75-91`).
   `cholesky(inv(H)).T` yields upper U with inv(H) = UᵀU (checked to 2e-16,
   triangularity checked). The sweep update `err=(w−wq)/U_jj; W_F −= err·U[j,F]`
   matches a brute-force leave-trailing reference (explicit trailing-submatrix
   inverses) to 1.3e-7 across a full sweep, and reduces the weighted loss vs
   naive rounding (65.39 vs 67.09 in the repro). Lazy-batch blocking
   (`reround_tensor:94-121`) is exact, not approximate.
3. **Projection form B=U_r, A=BᵀR is EXACTLY optimal for any invertible right
   whitener** — including the block-diagonal L (verified: matches
   [RL]_r L⁻¹ to 7e-15 for full chol and 3.6e-15 for blocked). The docstring's
   "D cancels" argument generalizes; no math error in the one-sided path.
4. **Two-sided unwhitening solve is exact** (`extract_adapter.py:238-254`).
   B = Lg⁻ᵀU_r via `solve(Lg.T, ·)`, A per-block = SV·L_b⁻¹ via
   `solve(L.T, SV.T).T` — matches the closed-form argmin Lg⁻ᵀ[M]_r L⁻¹ to
   1.3e-15. Factor balancing (`:250-254`) provably preserves BA (drift 8.9e-16)
   and correctly equalizes ‖B_i‖,‖A_i‖ → sqrt(‖B_i‖·‖A_i‖) each.
5. **Alpha/rank folding matches the runtime** (`extract_adapter.py:294-305` vs
   `llama.cpp/src/llama-adapter.h:53-57`). Runtime scale = alpha/rank with rank
   = b->ne[0] = per-tensor r; uniform→alpha=r (scale 1), mixed→alpha=1 with
   A·r_t folded so (1/r_t)·B·(A·r_t) = BA. e11's offline application
   (`e11_alternate.py:73-76`) uses the identical formula. Consistent.
6. **Q8_0 after folding loses no precision** — Q8_0 stores a per-32 fp16 scale;
   multiplying A by r_t scales the block max identically, so relative precision
   is unchanged (scale ~|A|·r/127 ≪ fp16 max for any plausible magnitudes).
   The prompt's suspected "Q8-after-fold precision loss at high rank" does not
   exist. (F16 mixed-rank factors after ×r_t folding are the risky variant —
   see B7 — but every published mixed-rank adapter is Q8.)
7. **dl==0 handling is degenerate-correct** (`e13_reround.py:107-111`): codes
   become arbitrary but dequant is code-independent when dl=0; `wq` is computed
   with the true `dlj` (not `safe`), so the error feedback propagates the real
   error. Zero dl==0 elements found in the tensors tested anyway.
8. **imatrix Gram threading: no race.** `gram_accumulate` runs entirely under
   `m_mutex` (lock at `tools/imatrix/imatrix.cpp:278`, call at :420-422); worker
   threads write disjoint row stripes (`i = ti; i += nth`) of the block Gram and
   are joined before return; `m_gram_x` is only touched under the lock. The
   `xi==0 → skip` shortcut is mathematically a no-op contribution. Gram count
   semantics (`gs.count += T` tokens) match the Python normalization
   (`gram_whitener`: S = g/count) and the dense imatrix count
   (`e.counts += nrows/n_mat` with n_mat==1 for all gram_wanted tensors).
9. **Adapters vs promoted tensors: no silent mismatch.** Residuals are computed
   against `deq()` of the tensor's ACTUAL stored type (extract_adapter.py:195),
   so ffn_down/attn_v promoted to Q3_K/Q4_K inside a "Q2_K" file are corrected
   against their true dequant; e13 re-rounds only `tensor_type == Q2_K`
   (`e13_reround.py:213`) and passes everything else through. The E13-27B
   README correctly discloses the resulting codec-coverage gap.

---

## B. Findings (defects / overstated claims)

### B1. "Full covariance" at 27B is block-diagonal covariance for the widest
tensors — and the README's optimality framing hides the approximation.
**WEAKENS.** `extract_adapter.py:99-125` + `imatrix.cpp:447` (`blocks = n >
8192 ? 8 : 1`): 27B ffn_down (n_in 25600) is whitened with 8 independent
3200-blocks. The projection form is exactly optimal *for the block-diagonal
surrogate metric* (verified), but NOT for the true E[xxᵀ] metric — repro
`t3_whiten.py`: true-metric loss 3367 (blocked) vs 2696 (full argmin), a 25%
excess in a synthetic cross-correlated case. E10 README:28-31 ("optimality
proof holds for any invertible whitener, D or L") is literally true but the
"full-covariance whitening" label on every 27B fc row silently means
"full-cov except ffn_down, which is 8-block-diagonal." Published measurements
stand (they measure the end artifact); the *mechanism* narrative ("the metric
was the bottleneck") is untested against a genuinely full metric at 27B.

### B2. The flagship 27B claim ("first ladder victory") is a ~1σ result.
**WEAKENS.** `experiments/10-full-covariance/results.txt:19-21` vs
`experiments/04b-27b/results.txt:24`: MIXED+fc KLD 0.081186 ± 0.003944 vs
Q3_K_S 0.087248 ± 0.004605 → unpaired Δ = 0.0060 ± 0.0061 ≈ **1.0σ**. PPL
7.313±0.261 vs 7.486±0.270 is likewise <1σ unpaired, and top-1 is EXACTLY tied
(88.039% both). Same-text evaluations are positively correlated, so a paired
per-chunk test would likely tighten this — but nobody computed it, and
RESULTS_ROLLUP.md:60-61 presents "geometry-aware MIXED+fc beats Q3_K_S (first
ladder victory)" as settled. Same pattern on E13-27B: bare KLD 0.1529 vs
re-round 0.1461 ± 0.0058 ≈ 1.2σ (`13-rerounder/results.txt`), with PPL moving
the WRONG way (7.891 → 7.952 ± 0.29). Recommendation: paired per-chunk
differences exist in the logs — compute them before daylight.

### B3. "bit-correct" in the rollup contradicts the Phase-1 report itself.
**COSMETIC (labeling), but reviewer-falsifiable.** RESULTS_ROLLUP.md:77-78
says "Fused kernel Phase 1: shipped, bit-correct." REPORT.md:54-56 says the
shipped warp-cooperative epilogue makes greedy output **diverge at a near-tie
token (~#9)** — only the abandoned serial epilogue was byte-identical; the
shipped artifact is PPL-digit-identical, not bit-correct. Phases 2/2B/3
consistently and honestly claim only "digit-identical"; the rollup should say
the same.

### B4. Reference-provenance mixing across the Block-4 table.
**WEAKENS one ordering; disclosed-but-diffuse elsewhere.** E04c/E10/E13 built
residuals toward the **Q6_K** artifact (bases requanted FROM Q6 — E04c
README:10-16 explicitly); E15-big and E22 used **BF16-pure residuals**; E16's
NVFP4 base was built before the BF16 reference arrived (JOURNAL.md Block 4)
while its rival row "Q4_K_M (from BF16)" is single-quantized from BF16 — then
all are scored against Q6 logits with measured Q6↔BF16 contamination 0.0032
KLD. That contamination equals the NVFP4+fc-vs-IQ3_XS gap (0.0625 vs 0.0656,
Δ=0.0031, E16 README:23-25): **that ordering is not established.** The E16
headline (loses its byte class to Q4_K_M by 3×) is safe. The journal's "all
configs shift by ≈ that constant" (JOURNAL.md:489-491) is also not a theorem —
KL against a swapped reference does not shift additively; it is an empirical
approximation that holds only where gaps ≫ 0.003.

### B5. `--columns` under `--gram-dir` ranks the wrong columns.
**WEAKENS (latent — published col-patch runs used diagonal whitening).**
`extract_adapter.py:224-232`: `col_e = (Rd**2).sum(0)` — under Gram whitening
Rd's columns are block-rotated mixtures (col j of R·L is a combination of
input channels), so "top-r WHITENED-ENERGY input columns" is no longer a
per-channel quantity; selection quality degrades silently (repro: 5/6 overlap
with the true diag-whitened ranking in a mild synthetic case). The patch
itself stays exact (B = R[:,cols]). E08's 0.6B and 27B col results predate fc
extraction, so nothing published is touched — but the flag combination is
accepted today with no warning.

### B6. The 27B "measured-alloc" adapter uses 0.6B sensitivities.
**WEAKENS (labeling/methodology).** `emit_alloc.py:25-30` hard-codes
`MEASURED_VALUE_PER_MB` from the **0.6B** r64 kind-probe ("kind-probe
2026-08-03, 0.6B") and the 27B flagship row "+ measured-alloc Q8 (E07)"
(RESULTS_ROLLUP.md:42) was emitted with those weights. Cross-scale transfer
of kind sensitivity was never re-measured at 27B (and E08/E20 both show
kind-level geometry SHIFTS with scale). The row is still an honest end-to-end
measurement of the resulting artifact — but "measured" means "measured on a
model 45× smaller."

### B7. Latent overflow: F16 mixed-rank folding after balancing.
**COSMETIC today.** `extract_adapter.py:305` multiplies A by r_t (up to
512 in E15-big) then casts to F16 unless `--q8-factors`. The two-sided
balancing was added precisely because F16 factors overflowed once (journal:
"9548 F16 infs → NaN serve"); folding re-scales A by ×r AFTER balancing and
only `maxB` is printed, not maxA. Every published mixed-rank adapter is Q8_0
(scale absorbs ×r safely), so this is a trap, not a casualty. Guard: print/
assert on `abs(A_out).max()` before an F16 write.

### B8. Cache dirs have no provenance fingerprint → one wrong flag = silent
garbage. **KILLS-if-touched (no published number affected).**
- `pass1_cache.py:59-65`: per-tensor npz keyed by tensor name only; `meta.json`
  records neither ref path, quant path, imatrix hash, nor kmax. Re-running
  against a different base with an existing `--cache-dir` reuses the OLD
  base's residual spectra without any error. The campaign dodged this by hand
  (`data/cache27b` vs `data/cache27b-iq2`).
- Same class: `e13_reround.py:221-243` `--codes-dir` caches 2-bit codes with
  no grid fingerprint; if the quant file differs on resume, cached codes are
  repacked onto a mismatched frozen grid byte-for-byte, no error, plausible
  output. Chunked resume ("~70 min, resume-safe") makes this the single most
  likely future silent-wrong in the repo.

### B9. ProjQ deflation silently skips blocked-gram tensors.
**WEAKENS (latent).** `e13_reround.py:186-193`: deflation applies only when
`len(blocks) == 1`; blocked tensors fall to the undeflated branch with no
warning. The published ProjQ number (25.94) is 0.6B where all grams are full
— valid. A 27B ProjQ run would be partially deflated and reported as if fully.

### B10. MoE is a gram blind spot (and would be WRONG if naively enabled).
**COSMETIC today; KILLS-if-assumed for the MoE roadmap.** `imatrix.cpp`:
`gram_accumulate` is called only in the dense MUL_MAT branch (:420); the
MUL_MAT_ID branch collects per-expert imatrix values (:337-363) but no grams —
so the announced next rung (35B-A3B MoE, E22 README:52-53) will silently fall
back to diagonal whitening for all expert tensors. Worse, calling the current
`gram_accumulate` there would be incorrect (src1 rows are expert-slot-mixed;
a per-expert row mask like the imatrix loop is required). Also note
`load_imatrix` (`extract_adapter.py:67-75`) divides per-expert sums by
`counts.mean()` — wrong per-expert normalization if it ever meets a MoE
imatrix (today it is saved by the `len(m2) != n_in` skip).

### B11. Dead/vestigial code in the e13 hot path.
**COSMETIC.** `e13_reround.py:142,183-184`: `hinv_cache` is declared, a `key`
is built, and the lookup is a no-op `pass` — Hinv is recomputed for every
tensor (q/k/v each redo the chol-inverse of the SAME shared S, 3× waste at
5120³). Correctness unaffected. Also `e13_reround.py:107`: `dlj > 0` treats a
hypothetical negative fp16 `d` as degenerate (codes would be wrong if
llama-quantize ever emits d<0; none observed in tested files).

### B12. Hyper-parameter drift across files.
**COSMETIC.** rsvd: extractor uses p=32/q=4 (`extract_adapter.py:262-275`),
pass1 uses p=16/q=3 (`pass1_cache.py:22`) — spectra cached for allocation are
slightly noisier than extraction spectra, and `rsvd_topk` silently returns
fewer than kmax components when min(dim) < kmax+p. Damping: 0.01·mean-diag
(gram whitening, GPTQ) vs 1e-3 (grad-gram, `extract_adapter.py:220`) —
each defensible, none cross-documented. E22 vendor row compares against a
UD-Q4_K_XL with +30 MB and a different layer recipe — used only as an upper
anchor, fair as framed.

---

## C. Does any RESULTS_ROLLUP.md row fall?

No row's number is wrong as a measurement. Three *claims* need softening:
1. "first ladder victory" (B2) — report the paired-difference significance or
   call it "wins point-estimate at ~1σ".
2. "bit-correct" Phase 1 (B3) — "digit-identical PPL; output diverges at
   near-tie tokens by FP reduction order".
3. NVFP4+fc "beats IQ3_XS's KLD" implication in the Block-4 ordering (B4) —
   inside the reference-contamination band; only the Q4_K_M verdict is safe.
And every "full covariance" 27B row means block-diagonal for ffn_down (B1).
