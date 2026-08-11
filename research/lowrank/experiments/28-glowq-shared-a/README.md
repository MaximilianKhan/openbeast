# E28 — GlowQ shared-A: input-sharing groups share whitened residual subspaces — GO

**Recon lever L2** (GlowQ 2603.25385 transplant; recon-2026-08-11.md).
Pre-registered JOURNAL 2026-08-11 15:10; run same day, CPU-only,
cached inputs (BF16 ref + E27 MIXED base + gram27b-bf16). Runtime
~80 s total (rsvd on cached Grams — the estimate of "an afternoon"
was off by 100×; the whole verdict cost less than a coffee).

## Hypothesis

Tensors consuming the same input (attn q/k/v on the 16 full-attention
layers; attn_qkv/attn_gate on the 48 GDN layers) have overlapping
whitened-residual right-subspaces, so one SHARED A-factor per group
beats separate per-tensor A-factors at equal total adapter bytes.

## Method

Per group instance: residual R_t = W_bf16 − dequant(W_MIXED), whitened
M_t = R_t·L (L = Cholesky of the group's shared input Gram — the Gram
store already dedupes q/v→attn_k, gate→qkv, which IS the premise).
Per-tensor top-R (R=128) right subspaces via rsvd → separate capture.
Stacked [M_q;M_k;M_v] top-r_s subspace where r_s = byte-parity rank:
r_s = R·Σ(n+out_t)/(n+Σout_t) → 195 (attn), 158 (gdn). Decision rule
(pre-registered): byte-parity capture ratio ≥ 1.0 → GO; < 0.9 → DEAD.

## Result: GO on both groups, uniformly across depth

| group | layers | ratio (shared@r_s / separate@R) | mean pairwise cos² | shared@R / separate@R |
|---|---|---|---|---|
| attn {q,k,v} | 16/16 measured | **1.1218** (range 1.089–1.153, all >1) | 0.43–0.51 | 0.9955 |
| gdn {qkv,gate} | 12/48 sampled | **1.0687** (range 1.063–1.089, all >1) | 0.71–0.78 | 0.9975 |

Full per-layer table: `results.txt`. No layer anywhere below 1.06.

Two readings of the same measurement:
- **Equal bytes → more capture:** +12.2% (attn) / +6.9% (gdn) whitened
  residual energy captured at byte parity.
- **Equal rank → fewer bytes:** the shared basis at the original R=128
  keeps 99.55%/99.75% of separate capture while deleting 80 of the
  adapter's 208 A-tensors ≈ 56 MB ≈ **−17% adapter bytes for ~0.3%
  capture loss**.

GDN subspaces overlap strongly (cos² ≈ 0.75); attention q/k/v only
moderately (cos² ≈ 0.47) — yet attn gains MORE at byte parity because
its A-side byte share is larger (3×5120 → 1×5120 vs out-dims 14336).
The lever is byte-geometry as much as subspace overlap.

## Caveats

- Capture is the surrogate, KLD is the outcome — and this campaign's
  own fifth law is "the surrogate is not the outcome." A shared-A
  adapter must be BUILT and evaluated (extraction + measure100) before
  any E27-table claim. Queued next.
- gdn measured on a 12/48 depth-spanning sample (uniform verdict,
  tight range); byte accounting is exact for all layers.
- ssm_out and attn_output have no input-sharing partner — untouched.

## Reproduction

    OMP_NUM_THREADS=16 python3 glowq_angles.py   # ~80 s, no GPU
