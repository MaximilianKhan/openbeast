> ⚠ see review/ corrections 2026-08-04

# E09 — the sub-2.5 bpw main event: corrected IQ2_XS vs the ladder

**Premise (from the night's synthesis):** correction value grows as the
base approaches its cliff, and the ladder has no good rungs below
~2.5 bpw — so if diagonal-whitened correction can mint a frontier point
anywhere, it is here. Win condition: IQ2_XS + adapter at FEWER total
bytes than bare Q2_K (10.86 GB) with BETTER quality than its
0.153 KLD / 83.6% top-1.

## Finding en route (independently found; already reported upstream):
## IQ2-class quants are IMPOSSIBLE for MTP models

`llama-quantize ... IQ2_XS` hard-fails on our MTP-preserved 27B:
`Missing importance matrix for tensor blk.64.attn_k.weight in a very
low-bit quantization`. blk.64 is the MTP/NextN draft layer —
llama-imatrix's calibration pass never executes the MTP graph, so the
draft tensors get no imatrix, and every very-low-bit quant type refuses
to proceed. **Stock tooling cannot produce sub-2.5-bpw quants of any
MTP-preserved GGUF.** Workaround found and validated:
`--tensor-type "blk\.64\.=q5_k"` pins the draft layer at q5_k (correct
choice anyway — the draft head should stay sharp). Review 2026-08-04:
NOT a new discovery — both the bug and our proposed fixes already have
open upstream PRs (#23476: activate MTP/NextN during imatrix; #23575:
static fallback for missing-imatrix tensors). Our workaround + the
pin-the-draft-head-high argument belong as comments on those threads,
not a new issue.

## Results (20-chunk PPL, KLD vs Q6 logits; adapters = measured-policy
Q8, cache kmax 128)

| config | total GB | PPL20 | mean KLD | top-1 |
|---|---|---|---|---|
| IQ2_XS bare (MTP@q5k) | 9.38 | 8.854 | 0.2642 | 78.5% |
| **IQ2_XS + 866 MB adapter** | **10.25** | **8.404** | **0.2175** | **80.6%** |
| IQ2_XS + 925 MB (saturated) | 10.31 | 8.419 | 0.2153 | 80.4% |
| Q2_K bare (the rung above) | 10.86 | 7.891 | 0.1529 | 83.6% |
| Q3_K_M | 13.50 | 7.243 | 0.0555 | 90.1% |

**Verdict: NULL — the ladder holds even at its cliff.** The corrected
IQ2_XS, 0.6 GB lighter than bare Q2_K, remains decisively behind it.
Budget saturation repeated (925 ≡ 866 MB): the diagonal-whitened
spectrum is exhausted long before the gap closes.

**The ember:** recovery FRACTION grows toward the cliff — correction
recovered 7.5% of Q2_K's KLD damage but **18% of IQ2_XS's** — the
gradient the cliff hypothesis predicted, just far too small under
diagonal whitening. Published full-covariance gains are largest exactly
in this regime (QERA-exact at 2-bit). E10 (full-covariance Gram capture,
blueprint in `prior-art/arxiv-whitening-allocation.md`) inherits a
sharpened target: multiply the 18% recovery by enough to jump a rung.

Serving (RTX 5090, -c 8192, greedy): IQ2_XS + adapter = **12.43 GB VRAM,
88.2 tok/s** — the lightest 27B configuration measured tonight, decode
within noise of Q3_K_M (89.2) and 45% faster than the Q6 reference.
Quality (80.6% top-1) is its weakness; bytes and speed are not.

**Night-final claim, now complete at three byte points:** at 27B under
diagonal (imatrix) whitening, quantization-residual correction loses to
the K-quant ladder at every measured byte point — 10.25, 11.7, and
13.5 GB class — while its recovery fraction rises monotonically as the
base degrades. Both halves of that sentence are new measurements.
