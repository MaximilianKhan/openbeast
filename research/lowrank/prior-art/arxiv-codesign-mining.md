# arXiv mining: quantizer/corrector co-design, recovery-vs-bitwidth, and calibration-only model-level correction

Date: 2026-08-03. Third-pass DEEP mining for the beast-rank campaign. Scope deliberately narrow:
this report does NOT re-summarize the lineage (see `arxiv-qec-lineage.md`); it extracts the exact
algorithmic mechanics of the co-design papers, assembles every published fragment of the
recovery-vs-bitwidth curve, and pins down the calibration-only cross-layer question against QEP
and LRC (both now read at algorithm level, not abstract level).

Grounding facts from our lab (JOURNAL.md): diagonal-whitened low-rank correction at 27B plateaus —
KLD 0.1415 at Q2_K base (2.56 bpw), 0.2175 at IQ2_XS base (2.06 bpw) vs bare 0.264; recovery
fraction rises toward the cliff (7.5% → 18%); extra rank bytes buy nothing past ~870 MB.

---

## Part 1 — Co-design mechanics: the exact algorithms

Five algorithms, ordered from simplest to most coupled. Column convention varies per paper; all
pseudocode below is rewritten in OUR convention: `W` is (d_out × d_in), activations `X` is
(d_in × n_tokens), layer output `Y = W X`, Gram `H = X Xᵀ / n` (d_in × d_in), imatrix diag ≈
`diag(H)`.

### 1.1 LoftQ (2310.08659) — the black-box alternation template

The only paper in the family whose quantize step is EXPLICITLY a black box. Verbatim algorithm
(their Algorithm 1, transposed to our convention):

```
Input: W, rank r, quantizer q(·), rounds T
A0, B0 = 0, 0
for t = 1..T:
    Q_t      = q(W - A_{t-1} B_{t-1})        # quantize the LR-corrected weight, whole tensor
    A_t, B_t = SVD_r(W - Q_t)                # plain truncated SVD of the new residual
return Q_T, A_T, B_T
```

- Objective: `min ‖W − Q − AB‖_F` (unweighted weight-space — their known weakness; QERA Fig. 1
  shows this objective and output error can diverge across iterations).
- Black-box status: explicit — "our algorithm is compatible with different quantization functions
  q_N(·)"; NF4/NF2/uniform all applied to the full matrix `W − AB`.
- Rounds ablation: T ∈ {0, 1, 5, 10}. **T=1 "is sufficient"; diminishing returns beyond; the
  paper's one caveat: 2-bit scenarios benefit most from multiple iterations.** No convergence
  guarantee; they note that as the residual shrinks, alternation struggles to keep improving
  (the quantizer's own noise floor becomes the binding constraint).
- What we take: the loop shape. What we replace: the unweighted SVD step (ours is whitened) and
  the quantizer (llama-quantize + imatrix).

### 1.2 CLoQ (2501.18475) — closed-form init, strictly one pass, no alternation

Confirmed at algorithm level: **no quantizer/corrector iteration at all.** Pipeline order:

```
1. W' = MagR(W)                              # ℓ∞-shrinking preprocessing (outlier flattening)
2. Q  = OPTQ(W', X)                          # GPTQ-class layerwise quantizer
3. ΔW = W' - Q
4. H  = XᵀX (their convention);  H += λI,  λ = 0.01·Tr(H)/d_in      # their damping rule
   H  = U_H Σ_H U_Hᵀ ;  R = Σ_H^{1/2} U_Hᵀ   # NON-symmetric square root (Cholesky-like)
5. AB = R⁻¹ · SVD_r(R · ΔW)                  # Thm 3.1: exactly optimal for FIXED Q
   concretely: R·ΔW = UΣVᵀ;  A = R⁻¹ U_{:r} Σ_{:r},  B = V_{:r}ᵀ
```

- Thm 3.1 is QERA-exact with a different (non-symmetric) root — same optimum, cheaper factor.
  Two SVDs total per tensor.
- **The one number we needed: λ = 0.01·Tr(H)/d_in.** First explicit damping constant in the
  closed-form lineage (QERA says "small perturbation, never needed"; CALDERA silent). Adopt it.
- Calibration: 128 × 2048 WikiText-2 tokens; Table 8: stable from 32 to 256 samples at INT2.
- No init-only (pre-fine-tune) INT2 metrics published — their INT2 numbers all include LoRA
  training. So CLoQ ≠ evidence for calibration-only 2-bit quality; it is evidence that
  **closed-form whitened init beats LoftQ's 5-round alternation** (INT2 post-FT: 6.51 vs 7.85
  Wiki2 at 7B). Init quality > iteration count, when a fine-tune follows.

### 1.3 CALDERA (2405.18886) — the fully-coupled reference (what we CANNOT copy, and what we can)

Their Algorithm 1 / 2 in full (our convention; their `Q` is 2-bit lattice, `L,R` 4-bit lattice):

```
# Algorithm 1 (outer loop)
L0, R0 = 0, 0;  best = +inf
for t = 1..T_out:
    Q_t = LDLQ(W - L_{t-1}R_{t-1}, H)                 # QuIP-style column-wise quant with
                                                      # linear error feedback from LDL(H)
    L_t, R_t = LPLRFactorize(W - Q_t, r, H)           # Algorithm 2
    err = ‖(Q_t + L_tR_t - W) X‖_F²
    if err < best: best = err; (Q*,L*,R*) = (Q_t,L_t,R_t)     # BEST-ITERATE TRACKING
return Q*, L*, R*

# Algorithm 2 (LPLRFactorize: min ‖(LR - A)X‖_F with quantized factors)
init (L,R) by rank-constrained regression:                     # Lemma 4.2, globally optimal
    two SVDs: project A onto rowspace structure of X, truncate
for s = 1..T_in:
    R = quantize_4bit( L† A H H† )                    # closed-form R-update, then requant
    L = quantize_4bit( A H Rᵀ (R H Rᵀ)⁻¹ )            # closed-form L-update, then requant
    track best (L,R) under ‖(LR-A)X‖_F
```

- **Convergence: no theorem. Monotonicity is NOT proven — it is enforced by best-iterate
  tracking** (the `if err < best` line). This is the honest published answer to "does alternation
  converge with an imperfect quantize step": it doesn't provably, so keep the best iterate.
- T_out/T_in defaults are not in the main text (buried in appendix/code); complexity
  O(T_out(n² + m²(n+d) + n·d·k·T_in)) implies both are small single digits.
- LDLQ coupling: the Q-step uses the LDL decomposition of H for error feedback — substituting an
  arbitrary quantizer voids their Theorem 4.1 constants but NOT the loop structure. The loop
  structure is LoftQ-with-weighting + best-iterate tracking.
- What we take: best-iterate tracking, the closed-form factor updates (if we ever quantize
  factors below Q8_0), and Theorem 4.1's (1 − k/2n)² capture law (already in the lineage report).

### 1.4 GPTQ-intrinsic LoRA + OLrC + Bid-Up (2606.01412) — the black-box theory we were hunting

Three separable pieces:

**(a) GPTQ-intrinsic (fold the corrector INTO GPTQ).** Requires a WHITE-box GPTQ:

```
H = XᵀX (their conv.);  eigendecompose X = UΣVᵀ;  L = V_r  (top-r right sing. vectors of X)
Augment:  𝕏 = [X | XL]   (n × (d+r)),   𝕎 = [W ; 0_{r×d_out}]
          ℍ = [[H, HV_r], [V_rᵀH, V_rᵀHV_r]]  (+ λI)
Factor (ℍ+λI)⁻¹ = 𝕃𝕃ᵀ via QR (they warn: NOT Cholesky, for stability)
Run GPTQ on 𝕎 for exactly d_in iterations → quantizes first d_in rows only
Return: Q (quantized rows), Rfp (last r rows, kept full precision), L
```

Reading: "the low-rank correction can be viewed as adding r extra full-precision columns to the
calibration matrix." Their bound replaces GPTQ's ‖X‖²_F dependence with ‖X − X_r‖²_F — the
corrector eats exactly the top-r spectral energy of the CALIBRATION matrix, and the quantizer
only has to pay for the tail. This is not implementable against llama-quantize (needs the inner
loop), but it IS implementable if we ever run our own GPTQ pass in numpy — and it is one-shot,
no alternation needed.

**(b) OLrC (their Algorithm 1)** = QERA-exact / CLoQ closed form, same formula:
`L*R* = (XᵀX)^{-1/2} · SVD_r((XᵀX)^{1/2} (W − Q))`. Nothing new for us — we run the diagonal
version of this today.

**(c) Bid-Up — THE black-box alternation result.** The alternation is:

```
repeat:
    (OLrC step)   AB ← whitened-SVD_r(W − Q)          # optimal LR for FIXED Q
    (Bid-Up step) for each entry (i,j):               # FIXED quantization grid 𝒜 (scales frozen)
                      Q_ij ← argmin over 𝒜 of layer-wise reconstruction error
                      given all other entries + AB fixed
```

Their claim (conditions matter): *each OLrC step exactly minimizes over LR for fixed Q; each
Bid-Up coordinate update exactly minimizes over the fixed finite grid; therefore a full
refinement loop cannot increase the layer-wise reconstruction error.* **Black-box compatible:
"Bid-Up requires only that the quantizer evaluates entries against a fixed finite alphabet 𝒜."**
No convergence-rate theorem; empirically 1–2 loops show the gains.

**The theory answer for llama-quantize as the quantize step.** Assemble the pieces:

1. Alternation `Q ← q(W − AB); AB ← argmin_r ‖(W − Q − AB)S‖` is 2-block coordinate descent on
   `f(Q, AB) = ‖(W − Q − AB)S‖²_F` **iff both steps minimize the same f**. The LR step does
   (exactly, closed form). llama-quantize with an imatrix approximately minimizes a per-block
   DIAGONALLY-weighted MSE over its scale/code class — i.e., f with S = diag(imatrix)^{1/2}, up
   to (i) its heuristic scale search and (ii) block structure. So with DIAGONAL whitening in the
   LR step (our current pipeline), the loop is approximate coordinate descent on one shared
   objective: descent up to the quantizer's approximation slack ε_q per step.
2. Monotonicity is therefore NOT guaranteed (ε_q can exceed the LR step's gain near the fixed
   point — exactly LoftQ's observed "harder to consistently minimize as the gap shrinks").
   **The published fix is CALDERA's: evaluate f (or KLD) after every round and return the best
   iterate. Guarantee: never worse than one-shot.** This is the whole theory; nobody has more.
3. Bid-Up's monotone guarantee needs the grid FROZEN. llama-quantize re-fits scales every call,
   breaking the premise — so scale oscillation is possible in a requant loop. Two mitigations:
   (a) best-iterate tracking (sufficient, cheap); (b) a true Bid-Up polish pass in numpy: after
   the final llama-quantize call, freeze the emitted K-quant scales, and do coordinate re-rounding
   of the 4/2-bit codes against the imatrix-weighted layer error with AB fixed — the grid is now
   genuinely fixed, so the non-increase guarantee applies. K-quant codes and scales are fully
   recoverable from the GGUF blocks (see `gguf-tooling.md`), so this is implementable.
4. If the LR step uses FULL-covariance whitening while the quantizer optimizes the diagonal
   objective, the two steps descend different functions — expect non-monotone behavior sooner.
   Still legal with best-iterate tracking; just budget T=2–3 max.
5. **Consensus round count across all five papers: 1–3 rounds pay, then flat.** LoftQ: T=1
   sufficient except 2-bit; ProjQ: stabilized by iteration 3 (of a nominal 55 cap); LRC: "only
   modest improvements" beyond T=1..5; 2606.01412: 1–2 loops; CALDERA: single-digit T_out.

**The implementable LoftQ-with-our-whitening loop (numpy + llama-quantize):**

```
# One extra round beyond our current one-shot pipeline. All tensors, or the E05 top-K set.
Round 0 (done):   Q0 = llama-quantize(W, imatrix);  A0B0 = diag-whitened-SVD_r(W − dequant(Q0))
Round t ≥ 1:
    W_shift  = W − A_{t-1}B_{t-1}                   # numpy, per tensor
    write W_shift into an F16/F32 GGUF              # gguf-py; MTP/invisible tensors: passthrough
    Q_t      = llama-quantize(GGUF, SAME imatrix)   # imatrix unchanged: layer input dist is
                                                    # unchanged — the correction is additive
    E_t      = W − dequant(Q_t)                     # NOTE: residual vs ORIGINAL W
    A_tB_t   = whitened-SVD_r(E_t)                  # diagonal today; full-cov when E06 lands
    KLD_t    = eval(Q_t + A_tB_t)                   # best-iterate tracking on the real metric
stop when KLD_t ≥ KLD_{t-1}  (expected: t = 1 or 2)
```

Correctness details: (i) the imatrix stays valid across rounds because the layer's INPUT
distribution never changes (corrections are additive at output of the same linear map);
(ii) residual is against original W, not W_shift — `W − Q_t − A_tB_t` is what serving computes;
(iii) K-quants' per-superblock min/scale re-fit each round is exactly the "re-fit grid" caveat
above — keep every round's (Q_t, A_tB_t) pair on disk and let KLD choose.

### 1.5 ProjQ (2606.00494) — shape the noise INTO the correctable subspace

The exact loop (their P-step/W-step), quantizer fully black-box (they use GPTQ unchanged):

```
Ŵ_0 = PTQ(W, X);  t = 0
repeat:
    (P-step)  R_t = (W − Ŵ_t) X                     # residual IN ACTIVATION SPACE
              V_r = top-r right singular vectors of R_t
              P   = V_r V_rᵀ                        # (n×n token-space projector… in their conv.
                                                    #  effectively: the correctable subspace)
    (W-step)  X_⊥ = X (I − P)                       # delete the correctable directions from X
              Ŵ_{t+1} = PTQ_Solver(W, X_⊥)          # re-run the SAME black-box quantizer,
                                                    # calibrated against X_⊥ only
until stable (Prop 5.4: objective monotonically non-increasing → stationary; converged by iter 3)
finally:  AB = argmin_r ‖(W − Ŵ + AB) X‖            # standard corrector on the shaped residual
```

- **Proposition 5.4 gives the alternation-with-black-box-quantizer monotone convergence that
  Bid-Up only gives for frozen grids** — the trick is that ProjQ never asks the quantizer to
  agree with the corrector's objective; it changes the quantizer's CALIBRATION DATA so the
  quantizer stops spending bits on directions the corrector will fix anyway.
- r_d = r_a = 64, 128×2048 C4 calibration, 2/3/4-bit g128.
- Numbers flag: our first extract gave 2-bit LLaMA-2-7B Wiki2 22.42 / C4 21.50 (vs LoftQ
  30.02/28.77); the search snippet of the same paper gives "2-bit Wiki2 8.17, avg acc 57.59%."
  Almost certainly different tables (with/without fine-tune or different group size). **Pull the
  PDF before citing ProjQ numbers.** Directionally both say the same thing: shaping ≫ LoftQ.
- **The llama.cpp mapping is cleaner than it looks.** llama-quantize consumes only
  diag-of-Gram (imatrix). The W-step needs the quantizer calibrated on X(I−P). We hold full
  activation captures for the E06 covariance work → compute the PROJECTED imatrix
  `imatrix'_i = [diag((I−P)ᵀ H (I−P))]_i` (P built in the d_in-space version of the P-step: top-r
  left singular directions of S·E, which we also already compute) and re-run llama-quantize with
  imatrix'. That is a faithful diagonal approximation of the W-step: tell the quantizer these
  directions are already paid for. One extra requant per round, converged by round 3.

---

## Part 2 — Recovery-vs-bitwidth: every published fragment of the curve

**Direct answer: no paper publishes recovery FRACTION as a function of base bitwidth. Our
7.5% @ 2.56 bpw → 18% @ 2.06 bpw measurement (fixed model, fixed rank budget, fixed whitening,
same quant family) has no published counterpart.** What exists are fragments, all pointing the
same direction:

| Source | Fragment | Numbers (7B+ unless noted) |
|---|---|---|
| ZeroQuant-V2/LoRC (2303.08302) T7 | gain grows as bits fall (their words: "more substantial as the bit size diminishes, especially W2") | OPT-30B coarse: W3 11.28→11.14 (−0.14); W2 25.74→14.39 (−11.35). OPT-66B W2: 225→13.0 |
| LQER (2402.02446) | required rank vs bits at fixed d | W4A8 needs r=32; W2A8 needs r=256 (8× rank for 2 fewer bits, d=4096) |
| QERA (2410.06040) T3 | full-vs-diagonal whitening gap vs bits | 4.25-bit: exact−approx ≈ 0.05 ppl (7B 9.12 vs 9.17); 3.25-bit: ≈ 0.32 ppl (10.67 vs 10.99) and LQER's heuristic breaks (14.00). No 2-bit PTQ at 7B+ |
| EoRA (2410.21271) | task-level gain vs bits, and diag-vs-full at 3-bit | W4: gains ≈ noise (7.00→6.80); W3 GSM8K 0.45→11.90 with full eigenspace vs 4.09 diagonal (**~3× full-vs-diag at 3-bit**); 2-bit MathQA 18.22→36.89 one-shot |
| CALDERA (2405.18886) T1–2 | ppl vs bpw with rank as the bpw increment | 7B: 2.0 bpw 8.23 → 2.1 (r64) 7.37 → 2.2 (r128) 6.76 → 2.4 (r256) 6.19 |
| LoftQ ablation | rounds-needed vs bits | T=1 suffices at 4/3-bit; only 2-bit rewards more rounds |
| QEP (2504.09629) T1 | propagation gain vs bits (no low-rank) | INT3g128 7B: 6.41→6.16 (−0.25); INT2g32 7B RTN: 90.69→12.25; AWQ INT2: 15,887→51.87 |
| RILQ | rank-insensitivity onset | at 2-bit, layer-local SVD saturates (σ across r16..256 = 0.69 ppl) — the wall exists only below 3-bit |
| 2604.07955 "Rethinking Residual Errors" | claims the curve qualitatively | "compensation impact scales inversely with bit-width"; PDF extract too lossy for numbers — **verify before citing** (saved: `~/.claude/projects/.../webfetch-1785822448350-wziunj.pdf`) |

**Synthesis into one curve (all sources agree):** correction benefit ≈ 0 at 4-bit, small but real
at 3-bit (0.2–0.3 ppl closed-form; 3× task-level with full whitening), large at 2.5 bpw, and
divergence-rescuing below 2.2 bpw. Our 7.5%→18% sits exactly on the published slope and extends
it with the first fixed-everything measurement pair.

**Does full-covariance whitening jump a quant rung at 2–2.5 bpw?** Nobody has run the comparison
that answers it (full-vs-diag at 2-bit PTQ, 7B+ — QERA stops at 3.25-bit, EoRA's 2-bit table has
no diagonal column, CALDERA never runs diagonal). Best available inference:

- QERA's exact-vs-approx gap grows ~6× going 4.25→3.25 bit (0.05→0.32 ppl). Extrapolated one more
  rung, expect ~1–2 ppl at ~2.25 bit — material, but rung spacing at 2 bpw is ALSO huge
  (CALDERA: 2.0→2.4 bpw is 8.23→6.19, i.e. ~2 ppl per 0.4 bpw at 7B).
- CALDERA's own ladder shows the honest benchmark: r256 of full-Gram-optimized correction
  (+0.4 bpw) ≈ one rung (~0.4–0.5 bpw) of base quantizer. **Correction bytes and quantizer bytes
  are roughly AT PAR at 2 bpw when the correction is full-covariance + factor-quantized** — the
  win is therefore not "jump a rung for free" but (a) the rung you can't buy (no ladder step
  exists, e.g. between IQ2_XS and Q2_K), and (b) our measured regime where correction bytes beat
  base bytes (our E04 byte-fair triangle showed corrected Q2_K > spending the same bytes on the
  next rung — that finding remains unpublished anywhere).
- Verdict for the paper: frame full-cov whitening at 2 bpw as **the missing measurement**, with
  QERA's 3.25-bit trend + EoRA's 3-bit 3× as the two published anchors bracketing it.

---

## Part 3 — Calibration-only model-level correction: QEP, LRC, and the precise gap

### 3.1 QEP (2504.09629) — sequential propagation, formalized, closed-form, quantizer-only

The paper we needed. Exact mechanics:

- **Objective (their Eq. 3):** `min_{Ŵ_ℓ} ‖W_ℓ X_ℓ − Ŵ_ℓ X̂_ℓ‖²_F` — **target = FP weights on FP
  activations; input = activations propagated through the already-quantized upstream network.**
  Quantizing layer ℓ explicitly compensates the accumulated upstream error.
- **Closed form (Prop 5.1):** with δ_ℓ = X_ℓ − X̂_ℓ and Ĥ_ℓ = X̂_ℓX̂_ℓᵀ (+ GPTQ-style damping,
  λ = mean of diag):

  ```
  W*_ℓ(α) = W_ℓ + α · W_ℓ δ_ℓ X̂_ℓᵀ Ĥ_ℓ⁻¹        α ∈ [0,1]
  then quantize W*_ℓ(α) against X̂_ℓ with ANY standard PTQ (RTN/GPTQ/AWQ unchanged)
  ```

  α=1 full compensation, α=0 plain layer-wise. **α is regularization: full compensation on MLP
  blocks overfits calibration; zeroing α on MLPs cuts cost 50–66% and acts as implicit
  regularization.** (Direct echo of FBQuant's corrector-overfitting warning.)
- Overhead: NEGATIVE vs GPTQ in their table (QEP+RTN 10.9 min vs GPTQ 14.9 min at 7B) — the δX̂ᵀ
  term is cheap, Ĥ⁻¹ precomputed.
- The INT2 numbers are the largest calibration-only 2-bit gains in this whole literature:
  7B INT2g32 RTN 90.69 → **12.25**; 13B INT2g128 122.06 → **12.78**; zero-shot avg
  0.4296 → 0.5598. Errors otherwise grow ~exponentially with depth (their Fig. 1) — propagation
  suppresses the growth. Robust across calibration sets where GPTQ alone overfits.
- **No low-rank component anywhere.** QEP corrects by MOVING THE QUANTIZATION TARGET, then throws
  the correction to the quantizer's mercy. The compensation matrix `W_ℓ δ_ℓ X̂_ℓᵀ Ĥ_ℓ⁻¹` is
  computed exactly, dense, then destroyed by quantizing W*.

### 3.2 LRC (2412.07902) — verified: does NOT propagate

Corrected reading (algorithm-level fetch, resolving ambiguity in earlier reports): calibration
activations X_ℓ come from ONE forward pass of the ORIGINAL FP model ("the sequence of activations
obtained along the forward pass of ℳ"); covariances Σ_X, Σ_Y, Σ_XY collected online; **quantized
activations Y = Q_a(X) are the ACTIVATION-quantized versions of the same FP activations, not
propagated ones. No propagation between layers; no propagated-vs-FP ablation exists in the
paper.** Within a layer it DOES alternate (GPTQ-step ↔ closed-form U,V eigendecomposition step,
T ∈ {1,5}, "modest improvements" beyond T=1) — but each layer is corrected in isolation against
FP calibration. W4A4 only, ranks 10–30% of d, no 2/3-bit, no rank-vs-d theory (their own n≃200k
sample-count remark is about full-rank Grams, not rank scaling).

### 3.3 The rest of the model-level field (already surveyed; one-line placement)

- RILQ (2412.01129): model-level objective, but gradient training (~40 min A100) — the benchmark
  our calibration-only variant must approach (+9.7 acc pts over layer-local at 2-bit).
- Cross-Layer Error Compensation (2607.14630): exact error recursion, but autodiff-optimized
  discrete codes, 1.5B-scale only, no low-rank term.
- GPTQ pipelines themselves (GPTQ/AWQ/OPTQ codebases): sequential propagation of QUANTIZED
  activations layer-to-layer is standard practice for the quantizer — which is exactly why its
  absence for correctors is an anomaly, not an oversight we imagined.

### 3.4 The gap, now named precisely

**Nobody extracts low-rank correctors against the running corrected network.** The four nearest
claims and why each misses:

| Method | Propagates? | Low-rank? | Calibration-only? | Bits |
|---|---|---|---|---|
| QEP | YES (defining feature) | no — dense target shift, then quantized away | yes | 2–4 |
| LRC | no (verified) | yes (r/d 10–30%) | yes | 4 only |
| RILQ | model-level loss (stronger than propagation) | yes | NO — trained | 2 |
| GPTQ-intrinsic | within-layer only | yes (in-loop) | yes | 2–3 |

The open method — call it **sequential residual propagation (SRP)** for the paper: *"propagated-
activation low-rank correction: each layer's corrector is fit, in topological order, to the
residual between the FP target and the already-quantized-and-corrected upstream network's
activations, closed-form, no training."* Composition of QEP's Eq. 3 with QERA/CLoQ's Thm:

```
# SRP: one topological pass; needs activation capture from the CORRECTED quantized net
for layer ℓ in forward order:
    X_ℓ  = FP activations           (captured once, FP run)
    X̂_ℓ  = activations from the quantized+corrected net so far   (re-captured as we go)
    δ_ℓ  = X_ℓ − X̂_ℓ;   Ĥ_ℓ = X̂_ℓX̂_ℓᵀ + λI      (λ = 0.01·Tr/d, CLoQ rule)
    # dense optimal correction of BOTH quantization error and upstream drift:
    C_ℓ  = W_ℓ X_ℓ X̂_ℓᵀ Ĥ_ℓ⁻¹ − Q_ℓ              # ( = QEP target shift + residual, exact )
    A_ℓB_ℓ = whitened-SVD_r( C_ℓ )  in metric Ĥ_ℓ  # rank-truncate the QEP correction instead
                                                    # of re-quantizing it (QEP's lossy step)
    install A_ℓB_ℓ; activations downstream of ℓ now flow through the corrected layer
```

Two design knobs published theory already prices for us: α-damping on MLP/FFN tensors (QEP:
full α overfits exactly there — and our E05 map independently found o/down are the hard tensors);
and whitening metric = Ĥ (quantized-net Gram), which is ARHQ's "whiten where the error lives"
argument arriving for free. At 2-bit the upstream drift δ is LARGE (QEP Fig. 1: exponential
growth), so C_ℓ ≠ per-layer residual — this is where the model-level headroom that RILQ proved
(9.7 pts) lives, and no calibration-only method has claimed it. **RILQ's rank-insensitivity is
the second reason to believe: if layers cooperate, small ranks suffice — precisely our plateau.**

Practical note for our harness: X̂ capture requires evaluating the partially-corrected GGUF
stack layer by layer. Feasible two ways: (a) block-wise — correct all tensors of block b, run one
capture pass of blocks 0..b, proceed (64 passes at 27B — heavy but overnight-able on short
calibration, 32×2048 tokens is QEP/CLoQ-validated); (b) one-shot approximation — capture X̂ once
from the UNcorrected quantized net, fit all correctors against that (zeroth-order SRP, one extra
capture pass total, strictly better-grounded than the FP-activation status quo).

---

## Tonight's implementable algorithms, ranked by expected KLD gain per engineering hour

Ranked for the 27B / Q2_K-and-below regime, using our measured plateau as the baseline.
(Effort assumes: whitened-SVD pipeline, activation capture, GGUF read/write, KLD eval all exist.)

1. **SRP-0: propagated-residual targets (zeroth-order — single extra capture pass).**
   Re-capture activations once from the CURRENT corrected Q2_K model; refit all correctors
   against `C_ℓ = W_ℓX_ℓX̂_ℓᵀĤ_ℓ⁻¹ − Q_ℓ` with α-damping (α=1 attn, α∈{0,0.5} FFN — two cheap
   sweeps). ~2–4 h. Evidence: QEP's INT2 rescues are the largest calibration-only 2-bit gains in
   print, and it attacks the model-level headroom (RILQ: 9.7 pts) that our per-layer objective
   cannot see. Highest expected KLD/hour.
2. **LoftQ-loop, T=1 extra round (black-box requant alternation).** Write `W − AB` to F16 GGUF,
   re-run llama-quantize with the SAME imatrix, re-extract whitened corrector, keep best iterate
   by KLD. ~2–3 h, fully mechanical. Evidence: LoftQ (2-bit is where extra rounds pay),
   Bid-Up/CALDERA loop structure; expect a real but bounded step (the quantizer re-centers its
   scale grid on the LR-corrected weight — exactly what Q2_K's coarse superblock mins waste most
   on). Combines multiplicatively with #1 (alternate on propagated targets).
3. **Full-covariance whitening (QERA-exact/CLoQ form, λ = 0.01·Tr(H)/d).** Already the forced
   E06 experiment; the co-design angle adds: use the NON-symmetric root R = Σ^{1/2}Uᵀ (one eigh,
   no matrix sqrt of the inverse) and CLoQ's damping constant. ~3–5 h incl. capture. Evidence:
   EoRA 3× at 3-bit; QERA gap ×6 per rung; at 2 bpw it is the missing measurement — worth doing
   for the paper even if the KLD step is moderate.
4. **ProjQ-lite: projected imatrix.** One round: P = top-r whitened residual directions per
   tensor; imatrix' = diag((I−P)H(I−P)); requantize with imatrix'; refit corrector. ~4–6 h
   (imatrix surgery + validation). Evidence: ProjQ beats LoftQ-style pipelines ~25–30% ppl at
   2-bit with a black-box quantizer, monotone convergence, done by round 3. The one mechanism
   that attacks WHY the residual is incompressible instead of compressing it harder.
5. **Bid-Up polish pass (frozen-grid coordinate re-rounding).** After the final quantize: freeze
   K-quant scales, re-round codes per entry against imatrix-weighted layer error with AB fixed —
   the only step here with a genuine non-increase guarantee. ~1–2 days (K-quant block bit-surgery
   in numpy). Highest confidence, lowest expected magnitude per hour — do it for the camera-ready,
   not tonight.

Anti-recommendations from this pass: do NOT implement CALDERA's LPLRFactorize (its value is
factor quantization below Q8_0, which LoRC's Table 8 says we don't need); do NOT chase
GPTQ-intrinsic's augmented Hessian until/unless we run our own GPTQ (llama-quantize has no such
hook); do NOT expect alternation alone (#2) to break the plateau — every published fixed-point
argument says it converges to a nearby basin; the plateau-breakers are the objective changes
(#1, #4) and the metric change (#3).

## Verification flags

- ProjQ 2-bit numbers conflict across extracts (Wiki2 22.42 vs 8.17) — pull PDF before citing.
- 2606.01412 experimental tables (Qwen3) did not survive HTML extraction — pull PDF for numbers.
- CALDERA T_out/T_in defaults live in appendix/code, not main text — check repo configs if quoted.
- 2604.07955 ("Rethinking Residual Errors") extract was lossy; PDF saved locally (path in Part 2
  table) — read before citing anything from it.
- QERA calibration sample count still unverified (inherited flag from lineage report).

## Reference index (new IDs this pass)

2504.09629 (QEP) · 2412.07902v1 (LRC, algorithm-level) · 2310.08659v4 (LoftQ, algorithm-level) ·
2405.18886v2 (CALDERA, algorithm-level) · 2501.18475 (CLoQ, algorithm-level) · 2606.00494 (ProjQ,
algorithm-level) · 2606.01412 (GPTQ-intrinsic/OLrC/Bid-Up, algorithm-level) · 2604.07955
(Rethinking Residual Errors — unverified) · 2601.02455, 2508.12094, 2601.11200 (adjacent
propagation/compensation, ASR/diffusion/calibration-data — not mined, listed for completeness).
