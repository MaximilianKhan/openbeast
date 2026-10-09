# E30 — Muon-provenance spectrum flatness: Qwen3.8-27B vs Qwen3.6-27B

**Pre-registered 2026-09-08, before any computation.** Trigger:
recon-2026-09-08 §C1 — Qwen 2608.30320 discloses Muon (2-D linear maps
only; AdamW for embeddings/router) for Qwen3.8-Next and, per third-party
coverage, the 2.4T flagship; the 27B dense is UNCONFIRMED and no tech
report is coming. Muon's orthogonalized updates predict FLATTER singular
spectra on the tensors it trains.

## Question
Are Qwen3.8-27B's 2-D weight spectra measurably flatter than
Qwen3.6-27B's — and, discriminatively, is the flattening CONFINED to the
tensor classes Muon would have touched?

## Design (the control is the point)
Per-tensor paired comparison across the two models (identical `qwen35`
arch, identical tensor names, matched UD-Q5_K_XL quants):
- **Treatment class:** attention q/k/v/o + FFN gate/up/down projections
  (2-D linear maps — Muon-trained under the disclosed recipe).
- **Control class:** token embedding + lm_head (+ any router-like/1-D
  params excluded entirely) — AdamW under the recipe, so NO flattening
  predicted there.
- **Signature:** flattening in treatment AND not in control ⇒ consistent
  with Muon provenance. Flattening in both ⇒ generic training-recipe
  difference (data/tokens/duration), NOT attributable to Muon. Neither ⇒
  null; L8 keeps Moonlight as the only Muon capture point.

## Metrics (cheap-first)
1. **Stable rank** srank = ||W||_F² / σ_max² per tensor (Frobenius
   exact; σ_max via 30-step power iteration, tolerance-checked).
   Statistic: per-tensor-pair log-ratio log(srank_3.8/srank_3.6),
   reported mean ± sem per class (paired, PROTOCOL #4).
2. **Top-64 spectrum head** via randomized SVD on a fixed sample of 24
   matched tensors (stratified: 8 early/8 mid/8 late layers, q_proj +
   ffn_down), for normalized head-entropy and visual shape. Sample
   pinned by layer index BEFORE compute: layers {2,5,8, 20,26,32,
   44,52,60}.

## Predictions (registered)
- P1 (Muon-consistent): treatment log-ratio > 0 (3.8 flatter) with
  |mean| ≥ 2·sem; control log-ratio ≈ 0 (|mean| < 2·sem).
- P2 (capture implication, tested later in T1.10): if P1 holds, whitened
  residual capture at matched rank should be LOWER on 3.8 than 3.6 —
  correction budgets on the 3.8 lineup shift toward TYPE allocation.

## Caveats (registered)
- Spectra measured on Q5_K-quantized tensors, both sides same quant —
  quantization perturbs spectra approximately symmetrically; a BF16
  re-derivation rung is owed if the effect is borderline (<3·sem).
- 3.6 vs 3.8 differ in data/tokens too; only the CLASS CONTRAST
  (treatment vs control) speaks to Muon, never the raw flattening alone.
- 27B optimizer is undisclosed; a null does NOT falsify 2608.30320.

## Provenance
- 3.6: weights/Qwen3.6-27B-UD-Q5_K_XL.gguf · 3.8:
  weights/Qwen3.8-27B-UD-Q5_K_XL.gguf (sha256 in weights.registry).
- Dequant: llama.cpp gguf-py (IQ/K dequant previously verified).
- CPU-only; runs concurrently with the 2026-09-08 eval sweep (GPU
  untouched; disk reads only).

---

## VERDICT (2026-09-08, same day) — NULL, both metrics

- Stage 1 (stable rank, n=256 paired 2-D tensors): treatment log-ratio
  **+0.0015 ± 0.0035** (z=+0.4). No flattening.
- Stage 2 (top-64 head entropy, n=9 ffn_down pairs): delta
  **+0.00045 ± 0.00073**. No flattening.
- Deviation logged: the pre-registered attn_q sample didn't materialize —
  hybrid GDN layers name projections differently at the sampled indices;
  the head sample is effectively ffn_down-only. Stage-1's 256 pairs span
  all matched 2-D suffixes and carry the conclusion.
- Control class (n=2, embd+output): +0.104 ± 0.091 — uninformative at
  n=2; moot given the treatment null.

**Interpretation (per pre-registration):** no Muon spectral signature in
Qwen3.8-27B vs Qwen3.6-27B. Consistent with (a) the 27B dense NOT being
Muon-trained (2608.30320 confirms the recipe only for Flash-Next; the
flagship attribution is third-party), or (b) Muon's flatness effect
washing out at convergence on this family, or (c) both. A null does NOT
falsify 2608.30320.

**Consequences:**
1. **P2 vacated — good news for the product lane:** no predicted
   whitened-capture penalty on the Qwen3.8 lineup; correction budgets
   derived on 3.6 transfer to 3.8 without a Muon discount.
2. **L8 reverts:** Moonlight-16B-A3B remains the only known-Muon capture
   point; the "PTQ of Muon-trained weights" question stays open AND
   unclaimed — now with a published-ready negative control (this
   experiment) for whoever runs the Moonlight arm.
3. T1.10's capture regression does NOT gain an in-family Muon axis.
