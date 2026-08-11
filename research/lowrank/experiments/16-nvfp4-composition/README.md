> ⚠ see review/ corrections 2026-08-04

# E16 — low-rank correction on NVFP4: the unpublished composition

Direct answer to Max's "are we converging on NVFP4?" question. NVFP4 is
a 4-bit float FORMAT (16-block E2M1 + two-level scaling), not a
factorization; the theory paper (arXiv:2509.23202) proves its residual
is outlier-poor and covariance-structured — the exact food our whitened
correction eats. No published low-rank-on-NVFP4 for LLM PTQ error
correction surfaced — the qualifier is load-bearing (review F12:
SVDQuant/Nunchaku ship low-rank + NVFP4 for diffusion models, and the
2026 LLM-side sweep was budget-capped — treat the claim as unswept,
not cleared). We ran it.

## Setup

- Base: all-NVFP4 heretic-27B via `llama-quantize --tensor-type
  <kind>=nvfp4` overrides (discovered working via --dry-run probe;
  MTP pinned q5_k) = 15.77 GB. gguf-py dequants type 40 natively.
- Correction: fc r128 Q8, full covariance (gram27b-v2) = 925 MB.
  Extraction capture **0.52 — the highest 27B capture of the entire
  campaign** (Q2_K base: 0.36; mixed non-FFN: 0.42).

## Result (vs Q6 reference logits; Q6-vs-BF16 contamination = 0.0032)

| config | GB | KLD | top-1 |
|---|---|---|---|
| NVFP4 bare | 15.77 | 0.0805 | 88.5% |
| **NVFP4 + fc-r128q8** | **16.69** | **0.0625** | **89.9%** |
| IQ3_XS (fewer bytes) | 12.26 | 0.0656 | 89.4% |
| Q4_K_M (equal bytes, from BF16) | 16.84 | **0.0211** | **94.4%** |

(Review 2026-08-04: the NVFP4+fc-vs-IQ3_XS ordering is WITHDRAWN — the
0.0031 gap sits inside the Q6-reference contamination band (0.0032),
and the rows mix provenance (composite requanted from Q6, Q4_K_M from
BF16). Only the Q4_K_M verdict is safe.)

## Verdict

1. **Mechanism confirmed, and it is the campaign's cleanest:** one
   uniform adapter cut NVFP4's KLD 22% — the largest single-correction
   gain at 27B — on precisely the base whose residual theory said is
   most correctable. The capture-and-gain ranking across bases (NVFP4 >
   mixed > K-quants) matches the covariance-structure prediction 1:1.
2. **The composite still loses its byte class to Q4_K_M** (0.0625 vs
   0.0211 at ~16.8 GB): NVFP4's baseline deficit vs K-quants at 27B is
   deeper than the correction can bridge. Same shape as every 27B
   verdict: the correction is real, the K/IQ ladder is simply excellent.
3. Direction answer for Max: we are NOT reinventing NVFP4 — we measured
   a datapoint of correcting it that we found nowhere in print for LLM
   PTQ (novelty sweep unfinished, see header), which is the lane
   NVIDIA's own research (EoRA) parked. On Blackwell hardware where
   NVFP4 has a compute advantage (native FP4 tensor cores), the
   composition could matter despite the byte-class loss — that hardware
   argument, not the byte argument, is its future.
4. Metric divergence again: PPL worsened slightly (7.26 -> 7.37) while
   KLD/top-1 improved decisively — corrections optimize reference-match,
   which is what KLD measures. Task evals remain the final arbiter for
   the paper.
