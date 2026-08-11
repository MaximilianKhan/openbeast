> ⚠ see review/ corrections 2026-08-04

# E04 — v0 end-to-end: served low-rank residual correction

The decisive experiment. Q2_K base + activation-whitened rank-r residual
correction, exported as a LoRA-form GGUF, served by **stock** llama.cpp
(zero code changes), measured byte-fairly against the imatrix quant ladder.

**Hypothesis (H4):** at equal total bytes, `low-bit base + whitened rank-r
correction` lands materially closer to the F16/Q8 reference than the best
pure quant of the same size. The upstream-demanded bar (per
`prior-art/upstream-landscape.md`): beat same-bytes IQ3/IQ4 on KL-div at
≤10% decode overhead — with the caveat that v0's adapter path is unfused,
so decode overhead is measured but the ≤10% target belongs to the fused
kernel (E06).

## Pipeline (all commands reproducible)

1. `extract_adapter.py` — Gram/SVD route on the whitened residual
   (B = top-r left singular vectors of R·D, A = BᵀR), F16 factors,
   uniform-rank alpha=rank (scale 1.0). 196 tensors, skips token_embd
   (tied) per conventions.
2. Bases: Q2_K (no imatrix, from E03) and Q2_K-imat; ladder: IQ2_M,
   IQ3_XXS, Q3_K_M, Q4_K_M (all imatrix-weighted, same imatrix as the
   whitening — no information advantage either way).
3. Measure: full wikitext-2-test PPL; KL-divergence to Q8_0 logits
   (40-chunk slice — logits files are ~6 GB at this vocab); decode tok/s
   (llama-cli, greedy, 128 tok).
4. Round-trip check: rank-1024 adapter must recover ≈ Q8_0 PPL exactly
   (validates conventions end-to-end; catches silent permutation bugs).

## Early signals (16-chunk quick pass, 2026-08-03 evening)

| config | PPL (16 chunks) |
|---|---|
| Q8_0 reference | 25.18 |
| Q2_K bare | 300.43 |
| Q2_K + r64 adapter (81 MB) | **36.54** |

The correction recovered an 8× perplexity collapse. Serving worked in
stock `llama-perplexity --lora` on first try (after one interactive-mode
false alarm with llama-cli). Bytes: Q2_K file 296 MB + 81 MB adapter =
377 MB, vs Q3_K_M 347 MB / Q4_K_M 397 MB.

**Scale caveat for 0.6B:** token_embd is ~25% of this model's params and
is NOT corrected (tied, uncorrectable via this path) — file-size ratios
understate what the method does to the 2D blocks it actually touches. At
27B the embed fraction is negligible and the blocks dominate; 0.6B is the
hardest case for the bytes ledger, not the friendliest.

## Result — 0.6B triangle complete (2026-08-03 night)

**Validation first: the round-trip PASSES.** Q2_K + rank-1024 adapter =
PPL 22.07 vs Q8_0 reference 21.94 (Δ0.6%, attributable to F16 factor
storage). The pipeline is faithful end-to-end; no silent permutation bug.

Full wikitext-2-test PPL; KLD/top-1 on the 40-chunk reference logits:

| config | total MB | PPL | mean KLD | top-1 agree |
|---|---|---|---|---|
| Q8_0 (reference) | 639 | 21.94 | 0 | 100% |
| Q2_K bare | 296 | 258.07 | — | 35.1% |
| Q2_K-imat bare | 296 | 43.33 | 0.766 | 58.2% |
| IQ2_M | 265 | 65.70 | — | 52.9% |
| IQ3_XXS | 279 | 43.27 | 0.734 | 60.5% |
| Q3_K_M | 347 | 26.06 | 0.238 | 76.2% |
| Q4_K_M | 397 | 23.14 | 0.066 | 86.3% |
| Q2_K + r16 | 316 | 38.63 | 0.678 | 61.1% |
| Q2_K + r32 | 337 | 36.00 | 0.606 | 63.0% |
| Q2_K + r64 | 377 | 32.70 | 0.507 | 66.2% |
| Q2_K + r128 | 458 | 29.66 | 0.405 | 69.3% |
| Q2_K-imat + r32 | 337 | 33.63 | 0.490 | 65.8% |
| Q2_K-imat + r64 | 377 | 31.29 | 0.420 | 68.5% |
| Q2_K-imat + r128 | 458 | 28.48 | 0.338 | 71.5% |
| Q2_K + r1024 (check) | 1588 | 22.07 | — | — |

Decode (greedy, 512 tok, llama-server): bare 667 tok/s; +r64 444 (−33%);
+r16 407 (−39%). Rank-16 SLOWER than rank-64 → read at the time as
per-kernel launch overhead (196 tensors × 3 extra ops), not FLOPs.
[Review 2026-08-04: the launch-overhead mechanism was later falsified —
under graph replay launches were near-free; the wall was tail-load
serialization (E14 Phase 2). Single runs, no variance recorded; the
r16<r64 anomaly is unexplained as mechanism evidence.] The fused-kernel
case (E06 / upstream PR 3) still writes itself on the measured tax.

**Reading (0.6B verdict):**
1. The correction WORKS and composes with imatrix quantization: imat base
   + r64 takes top-1 from 58.2% → 68.5% for +81 MB, and every corrected
   config beats the IQ2/IQ3 points near its byte cost.
2. But at THIS scale, **Q3_K_M wins at equal bytes** (0.238 KLD @ 347 MB
   vs our 0.490 @ 337 MB). Named null: diagonal-whitened rank-r correction
   does not beat the K-quant ladder on a 0.6B.
3. Structural reasons 0.6B is the worst case: (a) rank bytes scale as
   r(m+n) vs mn — at d≈1–3k, r64 costs +1.47 bpw; at 27B's d≈5–25k the
   SAME rank costs ~+0.3 bpw; (b) token_embd (25% of file) is
   uncorrectable and dilutes byte ratios; (c) diagonal whitening is the
   weak variant (QERA: full covariance wins below ~4-bit effective).
4. → The scale hypothesis (does the verdict flip at 27B?) is the decisive
   remaining question → `../04b-27b/`.
