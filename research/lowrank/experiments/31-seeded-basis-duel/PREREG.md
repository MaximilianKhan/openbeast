# E31 — AWSRC seeded-basis vs learned whitened factors (capture-per-byte duel)

**Pre-registered 2026-09-08, before computation.** Trigger: recon-2026-09-08
§B2 — AWSRC (arXiv 2608.23144) repairs frozen INT4 backbones with
seed-generated bases (sign/permutation/Hadamard; ~zero basis bytes) and
claims to beat a low-rank arm at equal bytes (3B, unwhitened, PPL). If
seeded bases win in OUR whitened geometry at 27B, they are the fourth
member — or first escapee — of the equal-byte equivalence class.

## Data & scope (registered limitation)
Cached top-192 whitened-residual SVDs (data/cache27b/*.npz: U 5120-side,
s2, right factor; 401 tensors; provenance = the E10-era flagship lane).
The BF16 reference is pruned (PR #22), so total ||R_w||_F² is not
recomputable — the MEASURED metric is **dominant-subspace capture**:
capture(B) = Σ_j s2_j·||P_B u_j||² / Σ_j s2_j over the cached top-192.
The full-residual comparison is completed ANALYTICALLY via E03's measured
near-isotropy of the residual tail: on an isotropic tail, ANY k-dim basis
(seeded or learned) captures ≈ k/5120 of tail energy — a wash per vector
— so the equal-byte verdict is decided by the dominant subspace plus the
byte model. This registered decomposition (measured head + modeled tail)
is the claim's scope.

## Arms & byte model
Learned arm: rank-r truncated cached SVD, bytes = r·(m+n)·q8.
Seeded arm (AWSRC-style): seeded ±1/Hadamard-class left bases (bases
free), learned coefficient rows, two byte variants:
  S1: k = round(r·(m+n)/n), Q8 coefficients (strict byte parity)
  S2: k = round(2·r·(m+n)/n), 4-bit coefficients (AWSRC's low-bit lever)
r = 128 (the campaign's standard rung). Sample: 9 pre-registered layers
{2,5,8,20,26,32,44,52,60} × {ffn_down, attn_qkv} = 18 tensors; 3 seeds
per tensor, mean reported.

## Predictions (registered)
- P1: learned r=128 captures ~all of the top-128/192 cached energy;
  seeded S1 (k≈m·small) captures ≈ k/5120 of it — an order of magnitude
  less. Seeded arms LOSE dominant-subspace capture per byte decisively.
- P2 (escape condition, honest): if seeded capture materially exceeds
  the k/5120 random-subspace expectation (>2×), the Hadamard structure
  is aligning with residual structure and AWSRC's mechanism deserves a
  full-residual rerun once a BF16 reference is restored.
- Interpretation for the paper: AWSRC's published win is attributable to
  (a) low-bit coefficient bytes and (b) an unwhitened low-rank baseline
  — both testable later; our equivalence class (scoped: low-rank
  mechanisms, 27B, K-quant, equal bytes) is not contradicted either way.

---

## VERDICT (2026-09-08, same day) — P1 CONFIRMED, P2 decisively unmet

18 tensors × 3 seeds (full pre-registered sample; see e31.json):
- Learned r=128: **0.983** of cached dominant-192 whitened energy.
- Seeded S1 (byte parity, Q8 coeffs): **0.0349** — excess over the
  random-subspace expectation k/m: **1.00×**.
- Seeded S2 (2× vectors, 4-bit coeffs): **0.0698** — excess **1.00×**.

**In whitened geometry at 27B, AWSRC-class seeded bases are
statistically indistinguishable from random subspaces.** They lose
dominant-subspace capture ~28× at byte parity and ~14× even with the
low-bit-coefficient lever. Combined with E03's tail-isotropy (where all
bases tie per coefficient byte), the equal-byte verdict goes to learned
factors wherever the whitened head carries signal.

**Paper placement:** the AWSRC related-work paragraph now carries this
measurement — seeded repair is not a member of the equal-byte
equivalence class; it sits strictly below the low-rank mechanisms on
head capture and ties on the tail. Their 3B/unwhitened/PPL win is
attributed (as hypothesis, testable post-BF16-restore) to low-bit
coefficient packing plus an unwhitened low-rank baseline.
