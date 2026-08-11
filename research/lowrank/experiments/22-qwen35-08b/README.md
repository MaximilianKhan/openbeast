> ⚠ see review/ corrections 2026-08-04

# E22 — Qwen3.5-0.8B: BF16-pure replication on a second architecture

Max provided Qwen3.5-0.8B in BF16 + vendor UD-Q4_K_XL (2026-08-04).
Arch `qwen35`: a hybrid MINI-SIBLING of our production 27B — 25 layers
(7 classic attention + 18 linear-attention qkv/gate/ssm), MTP nextn at
blk.24. Everything here is scored against the TRUE BF16 reference
(PPL 16.61; 40-chunk logits) — no reference asterisk anywhere.

## Pipeline (all single-quantization from BF16; captures on BF16)

Grams+imatrix (40 chunks) -> ladder (Q2_K w/ MTP pin, Q3_K_M, IQ3_XXS,
Q4_K_M) -> fc extractions (one-sided r64 / r128-Q8; two-sided r64) ->
one alternation round. Total wall time from first capture to final
verdict: ~35 minutes, three lanes in parallel — for the lanes that
succeeded, on our locally patched toolchain (Gram-capture imatrix; the
clock excludes BF16 acquisition, and the two-sided lane failed —
finding 4).

## Results (KLD/top-1 vs BF16 truth; full corpus PPL)

| config | MB | PPL | KLD | top-1 |
|---|---|---|---|---|
| BF16 reference | 1558 | 16.61 | 0 | 100% |
| Q2_K bare | 436 | 33.09 | 0.490 | 66.4% |
| IQ3_XXS | 412 | 23.95 | 0.257 | 74.3% |
| Q3_K_M (the rung) | 480 | 23.28 | 0.167 | 79.2% |
| Q2_K + fc-r64 | 518 | 24.61 | 0.223 | 77.0% |
| Q2_K + fc-r128q8 | 523 | 23.03 | 0.168 | 79.4% |
| **+ alternation x1** | **523** | **22.79** | **0.147** | **81.1%** |
| Q4_K_M | 543 | 19.79 | 0.040 | 89.4% |
| vendor Q4_K_XL | 573 | 18.94 | 0.027 | 90.7% |

## Findings

1. **The method transfers: a between-rungs win-from-below on a second
   architecture** (review 2026-08-04 reframe of "clean ladder win").
   fc-r128q8 ties Q3_K_M; ONE alternation round beats it at +9% bytes
   (−12% KLD, +1.9pt top-1) with true-reference scoring throughout —
   but KLD is the only resolved metric (~3σ estimated; PPL ≈ 1.6σ, so
   "all three metrics" overreached by one; results.txt logged no error
   bars — the campaign's only file without them). No interpolation
   control: ladder interpolation at 523 MB ≈ KLD 0.080 vs our 0.147,
   and Q4_K_M at +3.8% bytes is 3.7× better (finding 3). Serving tax
   unpriced: on stock llama.cpp the adapter path costs 20–40% decode
   that the bare rung does not. The real finding is the TRANSFER — the machinery working unchanged on a
   second arch, in the same competitive window (between rungs 3 and 4)
   as 0.6B. The 0.6B recipe needed two rounds; the hybrid arch needed
   one.
2. **Ladder shape is architecture-dependent:** IQ3_XXS < Q3_K_M here
   (reverse of the 27B ordering) — the IQ family's strength is not
   universal; correction's competitive window varies by arch.
3. Above ~540 MB the Q4 tier (esp. the vendor's UD-Q4_K_XL, excellent
   at 0.027) rules, as everywhere: correction competes at the 2-3 bit
   frontier, not against strong 4-bit.
4. Two-sided on this arch is BLOCKED pending gradmatrix support for
   qwen35's hybrid backward (silent zero-file failure diagnosed; the
   tool's author-agent is on it; loud-failure guard being added).
   → RESOLVED same day (annotation 2026-08-04 per coherence-audit
   P2.2): gradmatrix hybrid capture SHIPPED (E20 §"qwen35 hybrid
   capture", 132 Grams, fail-loud hardening added); E24 consumed the
   T Grams; two-sided r64 measured 24.16 / KLD 0.2066 vs one-sided
   24.61 / 0.2227 — see MASTER-TABLE 0.8B.
5. Beast-rank pipeline note: everything ran from existing tools
   (captures, extractor, e11) with zero NEW code changes — "existing
   tools" includes our locally patched llama-imatrix (Gram capture),
   not stock llama.cpp; the method is genuinely model-agnostic within
   llama.cpp's arch support.

Next on this rat: 2-round alternation + r-sweep spot-check; gradmatrix
fix -> two-sided [done, see finding 4 annotation]; then the SCALE
LADDER toward Max's target: DeepSeek-V4-Flash-class MoE on one 5090
(the 35B-A3B MoE in weights/ is the natural next rung — MoE expert
tensors are the untested frontier).
→ STALE (annotation 2026-08-04 per coherence-audit P2.2): E23 RAN the
MoE rung with a CORRECT pooled-Gram MUL_MAT_ID capture (the B10 trap
below was bypassed by design — the extractor consumes only Grams);
verdict: decisive paired negative, see experiments/23-moe/README.md.
⚠ MoE trap before that run (review B10, kept for the record):
gram_accumulate only hooks the dense MUL_MAT branch — expert tensors
would SILENTLY fall back to diagonal whitening, and naively enabling
grams on MUL_MAT_ID would be WRONG (expert-slot-mixed rows need a
per-expert mask); the extractor's imatrix loader also mis-normalizes
per-expert sums if it ever meets a MoE imatrix.

Held-out / mixed-calibration arc artifacts in this dir (R7 first
pass): `heldout-results.txt` (raw numbers), `HELDOUT-METHOD.md`
(exact corpus construction incl. the attempt-1 sequential-concat BUG
and the sampling-alone control), `build_mixed_calib.sh` (scripted
2:1 interleave; code-test provably held out).
