# Theory note — L6: one master objective, four families
# (ReQuant / GSQ / SchurQuant subsumption + OBD-LLM two-sided scoping)
# Drafted 2026-09-09 from the banked recon + deep-read notes.
# VERIFICATION PASS same day: ReQuant (pp.3-6), SchurQuant (pp.3-6 +
# text grep), GSQ (pp.3-9 + App. E/F text) read from the PDFs directly;
# every resolved item is marked [VERIFIED], and the pass CORRECTED the
# recon on two load-bearing points (SchurQuant's metric IS a full Gram;
# GSQ's objective is reconstruction, not task loss). Remaining [EYEBALL]
# items are explicitly listed at the end.

Setting and notation (matches §2 of the draft). Reference weights
W in R^{m x n}, shipped quantized base Q with dequantization deq(.),
residual R = W - deq(Q). Input Gram G = sum x x^T over calibration
tokens, L_x its damped Cholesky factor (G/count = L_x L_x^T after
damping). Output-gradient Gram T = sum g g^T with factor L_g. The
MASTER OBJECTIVE over a correction object Delta drawn from a feasible
set F:

  Q(Delta; L_g, L_x, F) = || L_g^T (R - Delta) L_x ||_F^2 ,
  Delta in F.

Every method this note touches is a descent procedure on a
specialization of Q along three orthogonal axes:

  METRIC    : L_g = I or not; L_x full-Gram, diagonal, or I.
  FEASIBLE  : F = rank-r matrices (corrector)
              F = frozen-grid code choices (re-rounder)
              F = joint (scales, codes) on the block grid (SchurQuant)
              F = per-tensor type/bitwidth maps (RCO; the escaping axis)
  ALGORITHM : closed-form whitened SVD / Babai-GPTQ causal sweep /
              greedy coordinate descent / Gumbel-Softmax stochastic
              relaxation / Schur block alternation / MCKP-DP.

Our pipeline occupies (L_g = I, L_x = full damped Gram) with F =
rank-r (closed form, exactly optimal — draft §2.2) and F = frozen
codes (Babai sweep — draft §2.3). The claims below place the other
three families on these axes exactly, with proofs where the statement
is provable and flags where it is bibliographic.

## Claim 1 — ReQuant's scoring rule is exact coordinate descent on Q
## with L_g = I. (Provable; proved here.)

ReQuant (arXiv:2608.07019) scores a candidate single-code change dq at
coordinate j by dL(dq_j) = -dq_j.g_j + dq_j^2.H~_jj (their Eq. 7) and
accepts strictly improving moves in cyclic sweeps over a K-neighborhood
of grid steps (Alg. 1; defaults T=4 sweeps, K=2). [VERIFIED from PDF:]
their H~ = X~X~^T is the FULL row-level Gram, not a diagonal; the
gradient g = 2(eH~ - wB) is maintained EXACTLY via a rank-one refresh
after every accepted move (their Eq. 10), so there is no stale-gradient
approximation; and there is no 1/2-factor discrepancy — their H~ is the
Gram itself (L(e) = e^T H~ e - 2 w B e + c), so the exact quadratic
change is -dq.g_j + dq^2.H~_jj as written. One genuine nuance the recon
missed: their objective is the GPTAQ ASYMMETRIC variant
||WX - W_q X~||^2 with X~ the activations under the already-quantized
prefix (mismatch term B = dX X~^T, dX = X~ - X); at dX = 0 it reduces
exactly to our symmetric quadratic. The asymmetric term is an
orthogonal, adoptable refinement (track quantized-prefix activations
during capture), not a metric difference.

Take one output row w of W (rows separate under L_g = I), current
rounded row w_hat, and the row loss

  l(w_hat) = (w - w_hat)^T G (w - w_hat),

which is exactly Q with Delta ranging over code choices of this row
(||(R - Delta) L_x||^2 restricted to the row, L_x L_x^T = G). l is
quadratic, so a single-coordinate move w_hat -> w_hat + dq.e_j changes
it EXACTLY (no Taylor truncation) by

  dl = dq . dl/dw_hat_j + (1/2) dq^2 . d2l/dw_hat_j^2
     = -2 dq . [G (w - w_hat)]_j + dq^2 . G_jj .

With g := 2 [G (w - w_hat)]_j (the negative loss gradient at the
current iterate) and H := 2G this is dl = -dq.g + (1/2) dq^2 H_jj —
ReQuant's score up to the H-vs-2G factor convention [EYEBALL: their
paper's 1/2]. Two consequences:

(a) IDENTITY [VERIFIED]. For single-coordinate moves the diagonal
    entry H~_jj is not an approximation: cross-terms dq_i H~_ij dq_j
    arise only for simultaneous multi-coordinate moves, and their
    rank-one g-refresh makes every move exact. Their procedure is
    therefore exact cyclic coordinate descent ON OUR OBJECTIVE
    (L_g = I, their asymmetric G) — the identity holds with no
    approximation on either side. Their App. A.2 proves strict
    decrease + convergence to a coordinate-wise (K-neighborhood-local)
    optimum, the CD analogue of Bid-Up's per-sweep non-increase for
    the Babai pass.

(b) SUBSUMPTION, stated honestly. The subsumption is at the OBJECTIVE
    level: their update rule is exact coordinate descent on
    Q(.; I, L_x, frozen codes). It is NOT an algorithmic dominance
    claim — a cyclic accept-if-improving schedule and a causal Babai
    pass are incomparable per-iteration; both sit in the frozen-grid
    setting where monotonicity is provable (their App. A.2; Bid-Up,
    arXiv:2606.01412, for the sweep). Our additional axes over
    ReQuant: the in-format GGUF codec (bit-exact Q2_K repack — theirs
    is generic HF/PyTorch W4A16/W4A4 with QuaRot rotations), the
    whitened rank-r corrector arm sharing the same metric, and the
    Kronecker two-sided generalization (E24). Since their curvature is
    the full Gram [VERIFIED], the specialization chain to the imatrix
    diagonal runs through LQER/QERA-approx only, not through ReQuant.

## Claim 2 — GSQ searches the same feasible set with a trained
## stochastic optimizer against a coarser-granularity reconstruction
## objective. ([VERIFIED from PDF pp.3-9 + App. E/F.])

GSQ (arXiv:2604.18556) treats a shipped GGUF K-quant checkpoint as the
initialization of a discrete optimization, relaxes each code choice by
Gumbel-Softmax (full relaxation for <=4-value sets; a 5-logit LOCAL
SHIFT {-2..+2} around the shipped index for larger sets), trains
block-wise for 20 epochs on FineWeb-Edu, and "projects the result back
into the same GGUF K-Quant format" (their App. E). Placement:

(a) FEASIBLE SET: the discrete code choices of a shipped K-quant
    artifact — ours, with two verified nuances: their local-shift
    formulation restricts moves to a +/-2 neighborhood of the shipped
    code (an assumption their App. F flags as init-dependent; our
    Babai sweep has no such restriction), and their general recipe
    TRAINS group scales alongside logits (Table 7 carries a "group
    scales lr"), so their feasible set may not hold the shipped grid
    bytes fixed. Whether the K-quant projection re-emits the shipped
    6-bit sub-block scale codes or re-derives them is not stated
    [EYEBALL-REMAINING: their released code, not the paper].

(b) OBJECTIVE [CORRECTED vs the recon]: NOT task loss or KL — it is
    output-reconstruction Frobenius error at staged granularity
    (their Eq. 1 + §4.1 staging: q/k under linear reconstruction, v/o
    under attention-output reconstruction, MLPs under full block
    reconstruction, quantized-prefix inputs throughout). Restricted
    to a single linear layer, their objective IS our quadratic
    Q(.; I, L_x, codes) with the identical input Gram — so at layer
    granularity the two methods optimize the SAME function, theirs by
    20-epoch stochastic relaxation on GPUs, ours by one exact CPU
    sweep. Their block-level staging is the genuine objective
    difference: it sees within-block nonlinear composition and
    accumulated prefix error that a layerwise quadratic cannot.

(c) POSITIONING. Both objectives are reconstruction SURROGATES — the
    honest framing of T1.17 is therefore search-and-granularity, not
    surrogate-vs-outcome: exact one-shot layerwise-whitened vs
    trained block-staged stochastic, on the same shipped artifacts,
    KLD-graded + v5-fast capability. Verified anchor numbers for the
    table: their Qwen3-8B Q2_K average 50.03 -> 56.28 and Q3_K_M
    60.52 -> 61.61 (AIME25/GPQA-D/MMLU-Pro; App. J adds Qwen3-4B).
    Pre-registered reading: if one-shot lands within their curve's
    noise at ~0 GPU cost, exact-local search is vindicated at this
    scale; if not, the gap SIZES what block-granularity training buys
    — either way the table is the contribution. (Their measured
    side-effect — refined codes REDUCE generated reasoning tokens —
    is worth replicating in the v5-fast rows: token counts are
    already recorded.)

## Claim 3 — SchurQuant is exact block-coordinate optimization of the
## same full-Gram quadratic: suffix-elimination Schur conditioning for
## codes, closed-form (scale, zero-point) refits, alternated.
## ([VERIFIED from PDF pp.3-6; the recon needed TWO corrections.])

SchurQuant (arXiv:2608.15567), verified mechanics: objective
L(W) = 1/2 ||(WX - Y) Omega^{1/2}||_F^2 + lambda/2 ||W - W_ref||_F^2
with sufficient statistics G = X Omega X^T + lambda I — a FULL Gram
metric (the recon's "not whitened" was WRONG in the metric sense;
their G is our G plus two augmentations: a QEP-style reference anchor
lambda and teacher-decision token weighting Omega, which upweights
tokens whose top-1 flipped under the quantized prefix by rho = 8 — an
outcome-aware calibration weighting worth noting as a lever). Their
Schur complement is NOT a scale elimination: it eliminates the
CONTINUOUS SUFFIX — for the current chunk W_c with suffix W_r free,
S = G_cc - G_cr G_rr^{-1} G_rc is the exact curvature after the best
possible continuous suffix correction (their Prop 1), maintained
cheaply by a block-inverse recursion. That is precisely the chunk-
block generalization of the conditioning our Babai/GPTQ sweep applies
column-by-column (they cite the same Babai equivalence; GPTQ is the
chunk-size-1 case with nearest-level decisions). On top of that they
alternate: (i) coordinate descent over integer codes against the
Schur-reduced quadratic with an exact cross-term (their Eq. 17 — the
"key difference from independent rounding"), and (ii) closed-form
row-wise refits of CONTINUOUS scale a_i > 0 and zero-point o_i over
2^b candidates (their Prop 2). Verified numbers: with the objective
held exactly at GPTQ's, the optimizer alone (+SchurOpt) is worth
+11.88pp mean zero-shot at 2-bit Qwen3-4B; their Table 3 isolates
Schur conditioning (PPL 2344.82 -> 324.81 grid-fixed) from grid
refitting. Format: "the same scalar per-row group format as GPTQ" —
continuous per-row-group scales, NOT the K-quant superblock hierarchy,
not GGUF, not shipped artifacts.

For OUR in-format scale-refit extension the relevant derivation is
scale ELIMINATION (distinct from their suffix elimination — do not
conflate): fix one sub-block with integer codes q, scale s, weights w;
the block's contribution to Q is f(s, q) = (w - s q)^T G_blk (w - s q);
minimizing over CONTINUOUS s first gives s*(q) = (q^T G_blk w)/
(q^T G_blk q) and the reduced code objective f(s*(q), q) = w^T G_blk w
- (q^T G_blk w)^2/(q^T G_blk q). In the K-quant hierarchy, though, s is
NOT continuous — sub-block scales are 6-bit codes against a superblock
scale — so in-format scale refit is another FROZEN-GRID discrete
problem one level up, coverable by the same Babai sweep. (SchurQuant's
continuous-scale Prop 2 does not transfer as-is; the discrete analogue
enumerates the 64 scale codes per sub-block, which is cheap.)
Consequences:

(a) Their move class STRICTLY CONTAINS our re-rounder's (we hold s at
    the shipped bytes; they re-fit it). This is the B1 narrowing and
    it is real: "nobody re-optimizes scales alongside codes" is
    punctured. What survives, verified against draft §2.3: our tool is
    the first IN-FORMAT CODE re-optimization on the SHIPPED K-quant
    superblock hierarchy, whitened, one-shot, KLD-graded, emitting a
    byte-compatible GGUF. (The recon's stronger "scale+code" wording
    is NOT currently true of gguf-refine — §2.3 freezes grid bytes —
    and must not enter the draft unless the scale-refit extension
    below actually ships.)

(b) The derivation above IS the design for that extension: K-quant
    superblocks quantize sub-block scales themselves (6-bit codes
    against a superblock scale), so "scale refit" in-format is another
    FROZEN-GRID discrete problem one level up the hierarchy — the
    master objective covers it with F = {sub-block scale codes}, and
    the same Babai sweep applies with the Schur-reduced objective.
    Cost: one more sweep pass; format compatibility preserved by
    construction. This is the honest response to B1 if T1.17 shows
    grid-choice headroom; it is queued as an option, not a claim.

(c) [VERIFIED quotes] Their abstract: "At higher precision, however,
    tighter reconstruction does not consistently improve end-model
    metrics"; their §1: "Layer-wise reconstruction is a loose
    surrogate for the final language-model loss"; their §3.5: "Even
    an exact optimizer can overfit a layer-wise surrogate." Published
    corroboration of our surrogate-vs-outcome series and E28's
    KLD-null — cite the §1 sentence, it is the sharpest.

## Claim 4 — OBD-LLM scoping: we are their L_g = I special case, and
## one-sidedness is a defended choice, not an omission.

OBD-LLM (arXiv:2604.00821) applies the two-sided whitened objective
Q(Delta; L_g, L_x, rank-r) to post-GPTQ residual correction (adapters
from SVD of L_g^T (W - W_hat) L_x, rank-128, ties EoRA on PPL, beats
on accuracy — surrogate-vs-outcome datapoint #7). Our §2.2 objective
is exactly their L_g = I case. The defense of one-sidedness, in
descending order of strength:

(1) WORKFLOW: L_g requires backprop through the model. Our tool path
    starts from a shipped GGUF on consumer hardware — no gradients
    exist and llama.cpp provides none (our llama-gradmatrix needed two
    structural repairs to the trainer, draft §2.1, and is a research
    instrument, not the product path). One-sided is what
    "backprop-free from an artifact" forces; that is the practical
    differentiator, stated as such.

(2) THE E24 NEGATIVE THEOREM covers the cheap end of two-sidedness for
    the RE-ROUNDER exactly: a diagonal output-side weighting is
    provably a no-op for frozen-grid per-element rounding (each row's
    argmin is scale-invariant; draft §2.3). So the only lever an
    output metric has on codes is off-diagonal feedback — the
    expensive end — and for the CORRECTOR the general two-sided
    weighted low-rank problem is NP-hard (Gillis-Glineur,
    arXiv:1012.0197). The gap between "free" and "hard" is precisely
    where L_g = I sits.

(3) EVIDENCE TRANSFER: their measured two-sided gain (Table 5,
    49.88 -> 32.92 Wiki2) is on W-DECOMPOSITION at 8B — not residual
    correction, not K-quant grids, not 27B. No number they publish
    bounds our setting; DAM's theorem (arXiv:2607.20434) likewise
    covers SVD_r(Q(W)) composition, not Q(W) + lowrank(W - Q(W)), and
    its cross-term positivity is a heuristic sign argument. Both are
    cited as motivation that interactions must be MEASURED — which
    E24's nested Kronecker sweep and the T1.12 lane exist to do.
    Their dampening recipe (10% of mean diagonal on BOTH covariances,
    vs our 1% input-side) is the published candidate for the T1.12
    estimator repair — an adoptable detail, independent of sidedness.

## Claim 5 — the family table and what escapes it.

  method      metric (L_g, L_x)          feasible set         algorithm            format/scale
  ----------- -------------------------- -------------------- -------------------- -------------------
  imatrix     I, diag(G)                 (allocation input)   heuristic            GGUF, all
  LQER/QERA-a I, diag(G)                 rank-r               closed form          research, <=7B
  ours §2.2   I, full damped G           rank-r               exact SVD            shipped GGUF, 27B
  ours §2.3   I, full damped G           frozen codes         Babai/GPTQ pass      shipped GGUF, 0.8-27B
  ReQuant     I, full G (asym. GPTAQ)    frozen codes (K=2)   exact cyclic CD      generic PTQ, 8-235B
  GSQ         block-recon (staged)       codes (shift +/-2)   Gumbel-Softmax, 20ep shipped GGUF, 8B/1T-MoE
              + trained group scales
  SchurQuant  I, XOmegaX^T+lambdaI       codes + cont. scales suffix-Schur CD +    per-row-group PTQ,
              (token-weighted full Gram)  + zero-points        closed-form refits   0.6-14B (dense 8B-class)
  OBD-LLM     full (L_g, L_x)            rank-r               two-sided SVD        research, 8B
  AWSRC       I, act.-weighted (LQER-    seeded-basis codec   Hadamard codec       INT4-RTN, 3B
              style; their LR baseline
              = rank-34 INT8, weighted)
  RCO         outcome (calib. KL)        per-tensor types     MCKP/DP              GPTQ stack, 8B

Reading of the table that the paper should state: the LOW-RANK rows
above the RCO line are (metric, algorithm) choices over feasible sets
that E28/E29 MEASURED as an equivalence class at equal bytes —
correction, carve, shared-basis are byte-fair-indistinguishable, and
only TYPE ALLOCATION escapes. AWSRC's codec row is the measured
EXCEPTION BELOW the class, not a member: E31 (pre-registered,
2026-09-08) finds seeded bases capture exactly the random-subspace
expectation of dominant whitened energy at 27B (0.035 vs learned 0.983
at byte parity) — strictly under the low-rank mechanisms on the head,
tied on the isotropic tail. RCO occupies exactly the escaping axis (allocation,
outcome-objective, exact-budget DP) and contains zero mechanism-class
comparison (verified against full text 09-08) — so the measurement we
publish and the optimization they publish are complementary halves of
one statement: allocation is the only lever that moves at equal bytes,
and it can be optimized exactly. That sentence, with both citations, is
the related-work anchor.

## Fold-in list (draft edits this note licenses)
1. DONE 2026-09-09 — §2.3 ReQuant/SchurQuant placement sentence.
2. DONE 2026-09-09 — intro claim (1) re-narrowed, class named (B1).
3. OPEN — GSQ search-vs-granularity framing wired to the T1.17 table
   when it lands (Claim 2c's anchor numbers are in this note).
4. DONE 2026-09-09 — §2.2 OBD-LLM L_g = I scoping (Claim 4).
5. DONE 2026-09-09 — references.md +SchurQuant/AWSRC/RCO.
6. DONE 2026-09-09 — §4.3b equivalence-class section with AWSRC scope
   + RCO anchor (Claim 5).

## Remaining [EYEBALL] items after the 2026-09-09 verification pass
- GSQ: README-level resolved 2026-09-09 (repo IST-DASLab/GSQ): K-quant
  path "refines the discrete assignments and projects back" — codes
  only per their own wording; scales trainable only in the general
  (non-K-quant) recipe. Byte-level projection detail = one grep in the
  cloned repo at T1.17 step (c).
- OBD-LLM: VERIFIED this pass — "we add 10% average diagonal value
  dampening to each covariance matrix" (verbatim, implementation
  section); the 49.88/32.92 numbers sit in their low-rank
  DECOMPOSITION table (cite as decomposition results, not by the
  agent notes' "Table 5" label). Bonus verified fact for E24/T1.12:
  their Fig. 4 measures <=0.1 correlation between X and G across all
  LLaMA-3-8B projection layers — published support for the K-FAC
  factorization our Kronecker metric assumes.
- AWSRC: VERIFIED this pass — their low-rank baseline is
  ACTIVATION-WEIGHTED rank-34 INT8 factors (LQER-style), so the
  recon's "their low-rank baseline is unwhitened" is WRONG; the B2
  scope sentence survives (weighting is diagonal-class, 3B, INT4-RTN
  base) but must not say "unwhitened."
- DAM / RCO: verified by the 09-08 full-text agent pass; spot-check
  only if a reviewer leans on them.
- ReQuant/GSQ/SchurQuant: VERIFIED THIS PASS at page level; the
  quotes in Claims 1-3 can be cited as read.


## Addendum 2026-09-11 — GSQ mechanism scoping (code check)
The public GSQ repository (IST-DASLab/GSQ @ 03fc164) contains no GGUF reader, writer, or scale-code projection; its quantizers train the per-group scales as free fp32 parameters (src/trainer.py:69-75) and emit them verbatim to compressed-tensors/Humming. The K-quant "codes refined, format projection" description exists only in their README. Claim 2's placement of GSQ (block-reconstruction objective on shipped grids) therefore rests on the paper + README, and the codes-only vs codes+scales question for the released Qwen3.8-27B GGUF artifacts is UNKNOWN — the E32 head-to-head is scoped as an artifact-level comparison ("GSQ-RCO as released"), not a mechanism ablation. See JOURNAL 2026-09-11 11:10.
