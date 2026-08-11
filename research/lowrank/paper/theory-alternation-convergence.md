# Theory note — convergence of quantizer/corrector alternation with a
# black-box quantizer (M12; ours to publish)

Setting. Reference weights W in R^{m x n} fixed. An alternation round t:
  Q_{t+1} = quant(W - C_t)         (black-box quantizer, e.g. llama-quantize)
  C_{t+1} = corr(W - Q_{t+1})      (rank-r whitened correction, closed form)
where corr(R) = argmin over rank-r Delta of phi(R - Delta), and
phi(M) = ||M L||_F^2 is the whitened objective (L fixed by calibration).
This is E11 exactly. The mining survey (arxiv-codesign-mining.md) found
no published convergence statement for the re-fitting-grid case; Bid-Up
proves non-increase only for a frozen grid. The following elementary
argument covers the practical case we actually run.

## Claim 1 (finite-codes termination under best-iterate tracking)

The quantizer's output space is FINITE: Q_{t+1} is determined by its
input, and its input space collapses to the finite set of possible code
words: a K-quant tensor has a finite set of representable (scale, min,
code) combinations, so the set Q of reachable quantized tensors is
finite (astronomically large, but finite). Define the potential
  V_t = phi(W - Q_t - C_t)   (the served model's whitened error).
The corrector step is an exact argmin given Q_{t+1}, so
  phi(W - Q_{t+1} - C_{t+1}) <= phi(W - Q_{t+1} - C_t).
The quantizer step carries no such guarantee (it minimizes its OWN
internal per-block objective against W - C_t, not phi given the future
corrector). Hence V_t need not be monotone — and empirically is not
required to be. But since Q is finite, the sequence (Q_t) either
converges to a cycle or repeats values; under BEST-ITERATE TRACKING
(keep the argmin over t of V_t — what CALDERA implements and what our
stop-on-worse rule approximates), the tracked value is non-increasing
by construction and takes values in a finite set
  { min over rank-r C of phi(W - Q - C) : Q in Q },
so it is CONSTANT after finitely many rounds: the procedure terminates
in the exact sense that no further round can improve the tracked
iterate. This upgrades stop-on-worse from heuristic to certificate.

## Claim 2 (one-step improvement condition — why round 1 pays)

Round 1 improves the tracked value iff
  min_C phi(W - quant(W - C_0) - C) < min_C phi(W - quant(W) - C),
i.e. iff pre-subtracting the corrector steers the quantizer to a grid
point whose RESIDUAL is more rank-r-compressible in the phi-metric.
Measured: yes at both scales (0.6B 31.29 -> 30.58; whitened capture
0.37 -> 0.46 -> 0.51 across rounds — the residual's top-r whitened
energy fraction is exactly the quantity the inequality compares).

## Claim 3 (why gains decay — a basin argument, informal)

Write the quantizer as a projection-like map onto its finite grid set
under its internal metric psi (imatrix-weighted per-block LS; psi != phi
— the metric mismatch of our E13 findings). Once (Q_t, C_t) reaches a
pair where quant(W - C_t) = Q_t (a fixed point of the composed map,
i.e. the corrector's subtraction no longer moves any code across a
psi-decision boundary), the alternation is stationary. The number of
codes that flip per round is a non-negative integer that empirically
collapses geometrically (0.6B: PPL deltas -0.71, -0.08); a fixed point
was reached for practical purposes by round 2. The mismatch psi != phi
bounds the achievable fixed-point quality — the formal version of "the
quantizer's own objective is the binding constraint," and the reason
ProjQ-deflation (metric surgery on psi) is the principled next step,
with the Bregman-damped scheme of arXiv:2507.09428 giving the proven
descent variant when phi-monotonicity is wanted per-round.

## Status

Claims 1-2: complete elementary proofs as stated (finite-set + exact
argmin arguments; nothing deeper needed). Claim 3: informal, matches
all measurements; formalizing the geometric flip-decay would need
assumptions on residual isotropy (a Marchenko-Pastur noise model would
do).
For the paper's theory box: state Claims 1-2, cite the measurements,
present Claim 3 as observed mechanism. Related-work anchors: CALDERA
(best-iterate practice), Bid-Up (frozen-grid monotonicity),
LoftQ/CLoQ (init dominates iteration), 2507.09428 (damped variant).
