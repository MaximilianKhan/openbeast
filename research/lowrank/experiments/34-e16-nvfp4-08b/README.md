# E34 / E16 rung 1 — whitened low-rank correction on a self-quantized NVFP4 base, 0.8B (2026-09-11)

The unclaimed flag (E16 verdict, NEXT-STEPS-2026-09-08 item 2): the first
whitened-residual-on-frozen-NVFP4 LLM datapoint under PROTOCOL ground-up
provenance. Qwen3.5-0.8B (qwen35 hybrid GDN, same family as E22), every
base ONE quantize step from BF16 with the gram08b diagonal imatrix, MTP
layer blk.24 pinned q5_k on every base (E22 convention; first-match-wins
override order verified by dry-run), BF16 truth logits 40 chunks
(bf16ref08b-40.logits), pure b10865 build.

## Artifacts (prep done 10:46, CPU only)
- NVFP4-all base (attn_qkv/attn_gate/ssm_out/ffn_up/ffn_gate/ffn_down → nvfp4, 252 overrides): 516.1 MB
- Adapter fc-r128q8 (gram08b full-Gram whitening, rsvd, compute32): 86.9 MB → composite 603.0 MB
- Capture r=128: **0.736 mean over 151 tensors on NVFP4** vs **0.721 on Q3_K_M** (same rank/whitener) — at 0.8B the NVFP4 residual is only marginally more capturable than the K-quant residual (E16-27B: 0.52 vs 0.36). Datapoint for §4.2/T1.10.
- Ladder from the same BF16+imatrix: Q3_K_M 480, IQ3_XXS 412, IQ3_XS 457, IQ3_S 465, Q4_0 516, Q4_K_S 519, IQ4_XS 518, Q4_K_M 543, Q5_K_S 578, Q5_K_M, Q6_K (MB); vendor UD-Q4_K_XL 573 as the external anchor.

## Pre-registered readings (before any KLD lands)
1. **Increment**: NVFP4+fc128 vs NVFP4 bare, paired per-chunk KLD (paired_stats.py). Expect real (t ≤ −3) — the mechanism has never failed to improve its own base.
2. **Equal-byte law**: composite (603 MB) vs the ladder rungs bracketing it — Q5_K_S (578) below and Q5_K_M/Q6_K above. The E27/E32 law predicts the bare rung at ≥ the composite's bytes beats it (t > +2). If the composite beats Q5_K_S AND ties/beats the next rung, that is the first equal-byte win of the campaign — report loudly, then replicate at 27B before believing it.
3. **Base-format comparison at equal bytes**: NVFP4 bare (516) vs Q4_0 (516) / Q4_K_S (519) / IQ4_XS (518) — is NVFP4 a competitive 4-bit carrier on a GDN arch under llama.cpp's quantizer (which has no NVFP4-specific imatrix path)? Their ordering is the context for reading 2.
4. **Capture-vs-quality**: does the capture edge (0.736 vs 0.721) translate to a larger KLD increment on NVFP4 than the same recipe on Q3_K_M (q3km-fc128 vs q3_k_m, also measured)? Surrogate-vs-outcome check #7 candidate.
Stats: paired per-chunk t on 40 chunks, |t| < 2 = tie; bytes column mandatory; serving-tax asterisk applies (adapter decode tax, §5).

## Status
- [x] prep (quantize + extract) — 10:46
- [x] measure — 2026-09-11 14:02 (stage C) → results-e16-08b.txt + paired-e16-08b.txt (in this dir)
- [x] readings — 2026-09-14 (JOURNAL 11:00 entry, rollup Block 6, paper §4.3 sentence): (1) increment real t=−32 40/40; (2) equal-byte law holds — composite loses to Q4_K_S/Q5_K_S; (3) NVFP4 = worst 4-bit carrier under llama-quantize (bare 0.207 vs Q4_0 0.088); (4) capture edge did not translate (Q3_K_M increment larger) — surrogate-vs-outcome #7. **Rung 2 (27B) CLOSED unrun** — dominated composition, predictable outcome.
