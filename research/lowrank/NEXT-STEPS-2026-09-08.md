# Forward plan — locked 2026-09-08 midday (Max: "figure out next best steps
# and move forward with what we have")

## Intelligence that reshaped the plan (post-recon, found via HF probe)
**IST-DASLab shipped GSQ+RCO artifacts of Qwen3.8-27B** (updated ~09-02,
480k downloads): IQ2_XS→IQ3_S, MTP variants, their 1000×4096-chunk
imatrix, task-lossless claims at IQ3_S, +10pt AIME over Unsloth-Dynamic
at IQ2_XS. **RCO = Riemannian Constrained Optimization (arXiv
2605.00649, May)** — per-tensor TYPE allocation optimized on task loss.
URGENT read: it is the method-side twin of our "only TYPE allocation
escapes the equivalence class" measurement (E28/E29). Complementary on
its face (they optimize allocation; we proved allocation is the only
escaping mechanism at equal bytes) — verify the paper contains no
equal-byte mechanism comparison before any claim freezes.

## The synthesis: T1.17 lands on OUR default family with OUR instrument
The head-to-head is no longer "reproduce their 8B configs" — it is:
same shipped Qwen3.8-27B artifacts, their refined checkpoints vs our
gguf-refine, KLD vs BF16 reference + task capability via **v5-fast
(imputation-scored, ~0.85h/row on the eval stack we shipped last
night)**. Consumer-hardware asymmetry stays the positioning fact
(their production ran on ISTA institute compute; ours runs on the 5090
it serves from).

## STAGING (running now, sequential, network-only)
1. ✅ heretic-v2 BF16 restore (54.7GB, llmfan46) — re-arms the flagship
   lane: new adapters, E31 full-residual follow-up, E16-27B lane.
2. GSQ-RCO IQ2_XS + IQ3_S + their imatrix (~20GB).
3. Qwen3.8-27B BF16 (unsloth shards, 54.7GB) — reference for T1.17 KLD
   + future 3.8 Grams.

## GPU SEQUENCE (post-sweep + post-patch-up, in order)
0. **Kernel/serving rebase to ≥b10829** (GDN-norm fix; semver caution)
   + rebuild + bridge check (paired 10-unit A/B old-vs-new build to
   size the graph change). All rows below are post-fix era.
1. **T1.17 v2 (GSQ/RCO head-to-head on Qwen3.8-27B):**
   a. Generate 3.8 BF16 reference logits (40+ chunks, PROTOCOL #3).
   b. Gram capture on 3.8 (whitening stats; MTP-graph mechanism from
      the L5 lane if feasible, else standard).
   c. gguf-refine our arm on the SAME shipped baseline artifacts their
      card compares against (Unsloth UD-IQ2_S etc.).
   d. KLD table: {shipped baseline, GSQ-RCO, ours} × {IQ2_XS, IQ3_S}
      vs BF16 logits + paired per-chunk stats.
   e. **v5-fast capability rows for GSQ-RCO IQ2_XS/IQ3_S** (also
      feeds the OpenBeast leaderboard conversation — low-bit 27B rungs
      on consumer VRAM is the product thesis in miniature).
2. ✅ DONE 2026-09-11/14 (E34; flag claimed as a LOSING datapoint — equal-byte law holds, NVFP4 worst 4-bit carrier; journal 09-14). **E16 rung 1 at 0.8B** (PROTOCOL ground-up order): self-quantized
   NVFP4 base from the on-disk 0.8B BF16 + gram08b whitening → first
   whitened-residual-on-frozen-NVFP4 LLM datapoint. Cheap, fast,
   claims the flag while the 27B lane stages.
3. ❌ CLOSED UNRUN 2026-09-14 (rung 1 + legacy 27B row both dominated at equal bytes; no lane spent). ~~**E16 rung 2 at 27B** on the restored heretic BF16 lane~~ (base:
   self-quantized NVFP4) and/or stock-3.8 lane once Grams exist.

## PAPER LANE (CPU, anytime)
- Read 2605.00649 (RCO) + DAM + OBD-LLM PDFs; fold into related work.
- ✅ L6 three-family subsumption math — DONE 2026-09-09 (theory-L6-family-subsumption.md + draft integration incl. new §4.3b).
- Claim rewordings (B1/B2 from recon) into paper draft.
- E30/E31 writeups into JOURNAL/MASTER-TABLE.
