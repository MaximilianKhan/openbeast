# E32 / T1.17 — GSQ-RCO head-to-head on Qwen3.8-27B (staged 2026-09-09)

The decisive experiment (CONTRIBUTIONS-PLAN C2; NEXT-STEPS-2026-09-08
synthesis): same shipped Qwen3.8-27B artifacts, IST-DASLab's GSQ-RCO
refined checkpoints vs our one-shot whitened gguf-refine, graded by KLD
vs BF16 truth + v5-fast capability. Framing per L6 Claim 2 (PDF-verified):
**search-and-granularity**, not surrogate-vs-outcome — at layer
granularity GSQ optimizes our exact quadratic, by 20 GPU-epochs of
stochastic relaxation where we take one exact CPU pass.

## Staged artifacts (verified on disk 2026-09-09)
- weights/research-staging/BF16/Qwen3.8-27B-BF16-0000{1,2}-of-00002.gguf (54.7 GB)
- weights/research-staging/Qwen3.8-27B-GSQ-RCO-IQ2_XS.gguf (8.4 GB)
- weights/research-staging/Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf (11.8 GB)
- weights/research-staging/imatrix-qwen3.8-27b.gguf (theirs, 1000×4096-chunk)
- research/lowrank/data/wikitext-2-raw/ (corpus, shared with E27)

## Sequence (GPU; starts after the diagnostics A/B verdict)
a. `gen_ref38.sh` — BF16 reference logits, 40 chunks. E27 measured
   ~15 s/pass with partial offload → ~10-15 min. Output:
   data/bf16ref38-40.logits (~? GB; E27's 27B-40ch was the same shape).
b. Gram capture on 3.8 — needs the RESEARCH build
   (llama.cpp/build-research/, b10866 = b10865 + kernel patch); standard
   mechanism first, L5 MTP-graph variant only if the standard capture is
   clean. 3.8 is qwen35-arch (hybrid GDN) — the E22-era MoE/GDN capture
   gates (PSD/symmetry/counts) apply.
c. gguf-refine our arm ON THE SAME shipped baselines their model card
   compares against (Unsloth UD quants). Baseline downloads TBD (which
   exact rungs their card names — check card before pulling).
d. `measure38.sh <label> <gguf>` per artifact: KLD table
   {shipped baseline, GSQ-RCO, ours} × {IQ2_XS, IQ3_S} + paired
   per-chunk stats (PROTOCOL #3/#4).
e. v5-fast capability rows for the GSQ artifacts via temp serve scripts
   — leaderboard-INELIGIBLE research rows (partial suite; also a
   different-provenance base). Anchor numbers to beat/match, from their
   card (PDF-verified 09-09): Q2_K-class avg 50.03→56.28 on Qwen3-8B —
   note that is 8B/K-quant; their 27B IQ rows have no published task
   table, which is exactly the gap our v5-fast rows fill.
   NOTE: serve scripts for these get the family reasoning cap 20480
   (qwen family policy, PR #45); provenance stamps it — same era as the
   09-09 A/B cells.

## Registered readings (pre-committed before any number lands)
1. Primary: paired per-chunk KLD, ours-vs-GSQ-RCO at each rung. |t|<2 ⇒
   tie ⇒ one-shot whitened matches 20-epoch trained refinement at ~0 GPU
   cost (the consumer-hardware asymmetry IS the positioning). t≤−2 ⇒ we
   beat trained search (report loudly). t≥+2 ⇒ the gap SIZES what
   block-granularity training buys — the table is the contribution
   either way (L6 Claim 2c).
2. GSQ's token-reduction side effect: v5-fast rows record completion
   tokens — replicate/refute at 27B.
3. Both arms vs shipped baseline: does refinement (either kind) close
   the IQ2_XS→IQ3_S gap fraction that K-quant re-rounding measured
   (~half a tier, §3)?

## Open pre-flight items
- [x] GSQ code eyeball — README-LEVEL RESOLVED 2026-09-09
      (github.com/IST-DASLab/GSQ): "GSQ refines the discrete
      assignments and projects the result back into the same K-Quant
      format" — K-quant path = codes refined, format projection;
      scales are trainable in their GENERAL recipe (scales_lr in
      configs) but the K-quant refit wording says assignments only.
      CODE-LEVEL confirmation — CLOSED 2026-09-11 as UNVERIFIABLE: the
      public repo (@03fc164) contains NO GGUF path at all (0 gguf/ggml
      hits in code); scales are trained fp32 parameters emitted verbatim
      to safetensors/Humming; production configs don't even load. The
      shipped GGUFs come from an unreleased pipeline → mechanism
      attribution for their K/IQ artifacts is unknown; we compare
      artifacts, not mechanisms (JOURNAL 2026-09-11 11:10).
- [x] Their model card's named baselines → UD-IQ2_S, UD-Q2_K_XL, UD-IQ3_S staged + measured (09-10).
- [ ] bf16ref38 disk check (~data/ has 440G free — fine).

## Status 2026-09-11
KLD half FINAL (paired-wikitext-40ch.txt = canonical stats; 09-10 journal t values superseded).
Running: capability rows (np1, v2/v3 chains), FineWeb control, PPL@2048 check, IQ stability repro — orchestrated by scratch/post_e32.sh (+ _b).
