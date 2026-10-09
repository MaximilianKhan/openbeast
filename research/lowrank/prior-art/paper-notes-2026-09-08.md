# Deep-read notes: RCO / DAM / OBD-LLM (2026-09-08, agent-read, quotes
# to be eyeballed vs PDFs once before related-work freeze)

## Ledger deltas these notes force (fold into paper + claims):
1. **OBD-LLM (2604.00821) DOES apply its two-sided whitened objective to
   post-GPTQ residual correction** (settles the 08-11 open question):
   "Δw = w − ŵ − vec(BA)", adapters from SVD of L_gᵀ(W−Ŵ)L_x; rank-128
   ties EoRA on PPL, beats on accuracy (surrogate-vs-outcome datapoint
   #7!). → Our claim scopes to: **first on shipped in-format K-quant
   GGUF grids, KLD-graded, with equal-byte mechanism controls, one-sided
   backprop-free (no gradients needed from a quantized artifact — the
   practical differentiator), at 27B.** Our objective = their L_g=I
   special case; L6 must state this and defend one-sidedness (workflow:
   GGUF-only, no backprop) or scope a two-sided probe. Their Table 5
   input-only-vs-two-sided gap (49.88→32.92 Wiki2 on decomposition)
   quantifies what L_g adds — on W-decomposition, not residuals, at 8B.
   Their dampening = 10% of mean diagonal on BOTH covariances.
2. **RCO (2605.00649) verified against FULL text: zero mechanism-class
   comparison, zero low-rank/correction content.** Per-linear-tensor
   bitwidth assignment (252 tensors, Qwen3-8B, GPTQ, calibration-KL
   objective), exact-budget MCKP/DP projection, 62–215 min vs 11–14h
   EvoPress. Precision fix for our text: their dense scale is 8B (MoE
   expert-pruning up to 30B-A3B) — never imply 27B-class dense. The
   exact-budget DP machinery is reusable if we ever optimize K-quant
   type maps under a byte budget.
3. **DAM (2607.20434): theorem does NOT cover residual correction** —
   it analyzes SVD_r(Q(W)) (composing two compressions of one matrix),
   not Q(W)+lowrank(W−Q(W)). Cross-term positivity is a heuristic-sign
   argument ("usually positive") under additive-independent noise; the
   "first mathematical proof" is existential. No budget analysis, no
   whitening in the optimization. Cite as motivation that interactions
   must be MEASURED + contrast sentence; do not let reviewers think it
   bounds our setting.

(Full agent notes with verbatim quotes archived below.)

---
(verbatim agent notes preserved in session transcript; key content captured above)
