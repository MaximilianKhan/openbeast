# Prior Art: The Quantization-Error-Compensation-via-Low-Rank Lineage

**Purpose:** deep per-paper extraction of the QEC lineage — every published idea for compensating
quantization error with low-rank terms — with the exact objectives, whitening/calibration machinery,
rank regimes, 2–3-bit tables, and everything the literature says (or fails to say) about
capture-vs-dimension scaling. This is the deep companion to `survey-lowrank-compression.md`
(which is breadth-first); nothing here supersedes that file's licensing table except where noted.

**Sweep date:** 2026-08-03. Method: four parallel research agents over arXiv abs/html/ar5iv pages,
OpenReview, GitHub, and the Semantic Scholar citation graphs of QERA/LQER/EoRA/CALDERA/ZeroQuant-V2.
All numbers below are from the cited papers' own tables as extracted from their HTML renders;
verification flags are collected in §9. Numbers, not adjectives.

**Our measured anchor (what this sweep is for):** diagonal (imatrix) whitened low-rank capture of the
residual R = W − dequant(Q2_K) at rank 64 is **0.37** at d=1024 (Qwen3-0.6B, E05) and **0.07** at
d≈5120 (27B, E04c) — a 5× collapse tracking the 5× shrink of r/d (6% → 1.25%). Uniform-r64
correction moved 27B KLD only 0.153→0.146 vs Q3_K_M's 0.0555. The question this file answers:
who has seen this, who explains it, who fixed it, and what remains unclaimed.

**Notation.** W ∈ R^{m×n} pretrained weight, Q = quantized base, E_q = R = W − dequant(Q),
correction C = A·B (rank r), d = hidden dim, y = xW row-vector convention unless noted.
"Diagonal whitening" = S = diag(√E[x_i²]) (exactly the llama-imatrix statistic);
"full whitening" = R_XX^{1/2} with R_XX = E[xᵀx] the input autocorrelation/Gram.

---

## Part I — The core one-shot lineage (calibration-only, no training)

### 1.1 ZeroQuant-V2 / LoRC — arXiv:2303.08302v3 (Yao, Wu, Li, Youn, He; Microsoft, 2023)

The origin of quant + low-rank-residual. arXiv preprint only (no verified venue).

- **Objective:** none minimized against data. Two-stage, data-free correction: E = W − Ŵ, plain
  unweighted truncated SVD, factors split as Û = U_m Σ_m^{1/2}, V̂ = Σ_m^{1/2} V_m,
  Ŵ_lorc = Ŵ + ÛV̂. No weighting matrix on either side.
- **Calibration:** none for LoRC itself (base GPTQ uses its usual calibration).
- **Rank regime:** m ∈ {1,4,8,16,32} tested (Table 9); gains plateau past m≈4–8; **recommended
  m=8 regardless of model size** → r/d from 0.002 (6.7B) down to 0.0006 (BLOOM-176B).
  Justification: Fig. 2 eigenvalue analysis — "rapid flattening of eigenvalues after index 8" for
  GPTQ error matrices. Footnote 6 caveat: m=8-suffices "is only true for PTQ."
- **2/3-bit results (Table 7, WikiText ppl, GPTQ base, INT8 factors, m=8; coarse / fine-grained):**

  | Bits | LoRC | OPT-13b | OPT-30b | OPT-66b | BLOOM-176b |
  |---|---|---|---|---|---|
  | W3A16 coarse | ✗ / ✓ | 12.43 / 11.90 | 11.28 / 11.14 | 17.77 / 10.63 | 49.46 / 11.30 |
  | W3A16 fine | ✗ / ✓ | 11.63 / 11.57 | 10.90 / 10.83 | 11.34 / 10.42 | 11.13 / 11.08 |
  | W2A16 coarse | ✗ / ✓ | 40.17 / 18.53 | 25.74 / 14.39 | 225.45 / 13.01 | explode / 14.15 |
  | W2A16 fine | ✗ / ✓ | 15.55 / 14.30 | 12.68 / 12.37 | 308.49 / 11.54 | 12.64 / 11.51 |

  (W8A16 reference: 11.22 / 10.70 / 10.33 / 10.90.) Their words: "the enhancement brought about by
  LoRC becomes more substantial as the bit size diminishes, especially noticeable for W2A16."
  Rank-8 rescues divergence at 2-bit but leaves 3–7 ppl on the table — unweighted tiny-rank is
  a stabilizer, not a fixer.
- **Factor quantization:** Table 8 — INT8 factors indistinguishable from FP16 (≤0.02 ppl).
  Directly licenses Q8_0 GGUF factors for us.
- **Capture-vs-d:** none theoretical; only the empirical eigen-flattening-after-8 claim.
- **Allocation / loop:** uniform rank; strictly one-shot. **Code:** DeepSpeed ecosystem (no
  standalone repo URL in the paper).

### 1.2 LQER / L²QER — arXiv:2402.02446v3 (Zhang, Cheng, Constantinides, Zhao; ICML 2024)

- **Objective.** LQER: truncated SVD of E_q itself (pure weight-space, no activation term).
  **L²QER:** SVD(S·E_q) = U′Σ′V′ᵀ, A = S⁻¹U′_k, B = Σ′_kV′_kᵀ; forward
  Ỹ = XW_q + (XS⁻¹U′_k)(Σ′_kV′_kᵀ). S is an explicit **heuristic**, not an optimality condition.
- **The Marchenko–Pastur statement (verbatim, §3):** "assuming the trained weights to be
  independent and identically distributed (i.i.d.), and given a sufficiently high chosen precision,
  E_q can be approximated as a random matrix formed by the round-off error. The Marchenko–Pastur
  distribution suggests that there exhibits an asymptotic behavior for the distribution of singular
  values of large random matrices" — with empirical confirmation on OPT-1.3B layers. **This is the
  published statement of our premise: raw quantization residual has an MP (flat-bulk) spectrum,
  so plain SVD capture ≈ r/d.** Their diagonal scaling exists precisely to escape the MP bulk;
  they admit "singular values of E_q decay slowly for most linear layers, requiring a sufficiently
  large k," but never analyze k vs d.
- **Whitening/calibration:** **diagonal**, from mean-|x| (not E[x²]): per-channel a_i = mean(|X_i|),
  ã = max over samples, s_i = a_i / √(min(ã)·max(ã)) (App. A, Eq. 13–14 — check the PDF before
  reimplementing; extracted from an appendix paraphrase). Calibration: SlimPajama subset,
  **32 samples × 2048 tokens ≈ 65K tokens**. No damping.
- **Rank regimes (uniform, constant in d — r/d silently shrinks with size):**

  | Setting | d | rank k | r/d |
  |---|---|---|---|
  | OPT-1.3B W3A8 sweep (Fig. 3) | 2048 | LQER needs ~600 for FP16 parity; L²QER ~64 | 0.03–0.29 |
  | LLaMA-1/2 7B–70B, OPT, Mistral W4A8 | 4096–8192 | **32** | 0.004–0.008 |
  | LLaMA-7B/13B **W2A8** (§4.4) | 4096/5120 | **256** | 0.0625/0.05 |

  Their own configs are the capture-collapse in disguise: rank must jump 32→256 (8×) going
  W4→W2 even at fixed d.
- **Results.** W4A8 L²QER-MXINT (Table 3, WikiText-2 ppl): OPT-13B 10.27 (+0.14 vs FP16);
  LLaMA-7B 5.89 (+0.22); LLaMA-13B 5.21 (+0.11); LLaMA-33B 4.25 (+0.15); LLaMA-2-7B 5.69 (+0.21);
  LLaMA-2-70B 3.44 (+0.12). Downstream −0.3% avg over 6 tasks (Table 4).
  **W2A8 (Table 6, k=256): LLaMA-7B ppl 10.30** (vs FP16 5.67; OmniQuant 12.97); LLaMA-13B **8.42**
  (vs OmniQuant 10.36) — 2-bit nearly doubles ppl even at r/d=0.0625 with diagonal scaling.
  No 3-bit on 7B+ in main tables.
- **Allocation / loop:** uniform; strictly one-shot ("no expensive knowledge distillation,
  hyper-parameter search, or iterative optimization"; ~1.2 h for LLaMA-33B).
- **Code:** github.com/ChengZhang-98/lqer (archived; no license file — reference only).

### 1.3 QERA — arXiv:2410.06040v2 (Zhang, Wong, Xiao, Constantinides, Zhao; ICLR 2025) ★ our reference math

- **Exact objective (Problem 2):** argmin over rank-k C_k of **E_{x∼X} ‖x(W̃ + C_k) − xW‖₂²** —
  layer *output* error in expectation, explicitly contrasted with weight-space ‖W − W̃ − C_k‖_F.
  Fig. 1 shows the wedge: LoftQ's weight error decreases monotonically with iteration while model
  output error does not.
- **Theorem 1 (QERA-exact):** with R_XX = E[xᵀx] (m×m input autocorrelation), symmetric PSD square
  root via eigendecomposition R_XX = QΛQᵀ ⇒ R_XX^{1/2} = QΛ^{1/2}Qᵀ:
  **SVD(R_XX^{1/2}(W − W̃)) = UΣVᵀ; C_k = (R_XX^{1/2})⁻¹ U_{:,:k} Σ_{:k,:k} V_{:k,:}ᵀ.**
  Whitened-SVD-then-unwhiten, whitener on the input-dim side.
- **Theorem 2 (QERA-approx):** under **Assumption 1 (E[x_i x_j] = 0 for i≠j)** the whitener
  degenerates to **S = diag(√E[x_i²])** — *literally the imatrix statistic*. The paper notes this is
  "remarkably similar to the activation-induced heuristics in LQER." **Our E05/E04c pipeline is
  exactly QERA-approx; QERA-exact vs -approx is precisely full-vs-diagonal whitening.** They profile
  Llama-2-7B/Llama-3-8B autocorrelations: diagonal dominance "strong but not perfect."
  Invertibility (Remark 1): add small diagonal perturbation if R_XX singular; never needed in
  practice (no damping value given).
- **Calibration:** SlimPajama, R_XX by batch averaging; exact sample count NOT recoverable from the
  HTML (appendix truncated — likely inherits LQER's 32×2048; UNVERIFIED). Fig. 3: QERA improves
  monotonically with more calibration data; LQER's heuristic does not.
- **Rank regimes (uniform, constant across sizes):** r=32 @ 4.25-bit, r=64 @ 3.25-bit, from
  TinyLlama-1.1B (d=2048) to Llama-3.1-70B (d=8192) → r/d 0.031 down to 0.004. QPEFT: RoBERTa
  r 8/64 @ 2/3-bit (fine-tuned). **No remark anywhere on the shrinking r/d.**
- **Results (Table 3, WikiText2 ppl; quantizer is plain nearest-rounding MXINT/HQQ-class, hence
  high absolute ppls):**

  | Model | Precision/rank | ZeroQuant-V2 | LQER | QERA-approx | QERA-exact |
  |---|---|---|---|---|---|
  | LLaMA-2-7B | 4.25b, r32 | 9.42 | 9.22 | 9.17 | **9.12** |
  | LLaMA-3.1-8B | 4.25b, r32 | 8.83 | 8.45 | 8.45 | **8.33** |
  | LLaMA-3.1-70B | 4.25b, r32 | 4.48 | 4.10 | 4.10 | **3.82** |
  | LLaMA-2-7B | 3.25b, r64 | 13.00 | 14.00 | 10.99 | **10.67** |
  | LLaMA-3.1-8B | 3.25b, r64 | — | 11.86 | 11.73 | **11.39** |
  | LLaMA-3.1-70B | 3.25b, r64 | — | 7.05 | 6.99 | **6.68** |

  **The bit-width pattern that matters to us:** at 4-bit exact≈approx (gap ~0.05 ppl); at 3-bit the
  diagonal approximation starts losing to full R_XX (10.99 vs 10.67) and LQER's heuristic outright
  breaks (14.00, worse than unweighted ZeroQuant-V2's 13.00). The lower the bits, the more the
  off-diagonal covariance matters. No 2-bit PTQ on 7B+; 2-bit appears only in fine-tuned QPEFT on
  RoBERTa (Table 1: 2-bit r64 QERA-exact 76.23% GLUE vs LoftQ 70.18%, +6.05).
- **Capture-vs-d:** no statement; error decreases monotonically in rank at fixed d (k ∈ {4..64}).
- **Allocation / loop:** uniform; one-shot closed form. **Code:** github.com/ChengZhang-98/QERA,
  Apache-2.0 (license re-verified in the earlier survey; this sweep did not refetch).
- Flag: HTML renders show the largest model as "Llama-3.1-80B"; abstract says 70B — treat as 70B.

### 1.4 EoRA — arXiv:2410.21271v6 (Liu et al.; NVIDIA + HKUST; v6 Mar 2026, venue soft: "ICLR 2026 workshops" on abs page)

- **Formulation (column convention Y = WX):** ΔW = W − Ŵ. Eigendecompose **X̃X̃ᵀ = QΛQᵀ** where X̃
  is the *average* input activation over calibration (an aggregate proxy for E[xxᵀ] — slightly
  weaker than QERA's true autocorrelation). Projection Q′ = Q√Λ; ΔW′ = ΔW·Q′; truncated
  SVD(ΔW′) ⇒ B′A′; unproject A = A′Λ^{-1/2}Qᵀ (precomputed; zero inference overhead).
  **Theorem 1:** minimizing ‖ΔW′ − B′A′‖_F ⇔ minimizing ‖ΔWX − BAX‖_F. Mathematically QERA-exact
  in different clothes; the practical deltas are the X̃-averaging and task-specific calibration.
- **Calibration:** tiny and task-matched — WikiText2: 128×2048; ARC-C: 32 ARC + 32 C4; MathQA/GSM8K:
  32 task + 32 C4. Ablation (Table 8): plateau ~128–256 samples.
- **Rank regimes:** uniform r=128 in main tables (r/d 0.025–0.031); rank sweep on 2:4-pruned
  LLaMA3-8B: r ∈ {64,128,256,512} (r/d up to 0.125). Constant across model sizes; no scaling rule.
- **Results at 3-bit (GPTQ LLaMA3-8B, r128; Tables 2/5):** ARC-C 20.90→31.74 (FP 50.42), MathQA
  22.37→29.11, GSM8K 0.45→11.90. Baselines on GSM8K: plain SVD of ΔW (ZeroQuant-V2-style) 3.79;
  **diagonal Act-S scaling 4.09 vs full-eigenspace EoRA 11.90 — a ~3× accuracy gap between diagonal
  and full whitening at 3-bit.** W3 ppl: LLaMA3-8B 15.64→10.06; LLaMA2-7B 7.76→6.84; LLaMA2-13B
  5.99→5.75. W4: gains shrink to noise (7.00→6.80).
  **2-bit (Table 19, GPTQ LLaMA3-8B MathQA): uncompensated 18.22, LoftQ 35.80, EoRA one-shot 36.89,
  RILQ 37.60, RILQ-init-from-EoRA 38.90** — the best 2-bit pipeline is eigenspace one-shot as init
  + model-level fine-tune.
  **Rank sweep (Table 10, 2:4 LLaMA3-8B GSM8K): 10.77 / 13.95 / 17.06 / 23.28 at r = 64/128/256/512
  — still climbing steeply at r/d = 0.125, no saturation.** At aggressive compression the capture
  curve flattens far above the r/d ≈ 0.0125 we ran at 27B.
- **Capture-vs-d:** none.
- **Allocation / loop:** uniform, per-layer independent, strictly one-shot (minutes, no backprop).
- **Code:** github.com/NVlabs/EoRA — license extracted this sweep as **CC BY-NC-SA 4.0** (earlier
  survey recorded NVIDIA Source Code License-NC; either way non-commercial — do not derive code).

### 1.5 RILQ — arXiv:2412.01129v2/v3 (Lee, Lee, Hong, Kim, Ahn, Chang, Choi; Hanyang + KT; AAAI 2025)

The paper about *why low-rank adapters saturate* — the direct commentary on our collapse.

- **Diagnosis:** "2-bit quantization errors are inherently high-rank, challenging the effectiveness
  of typical SVD-based low-rank adaptation techniques." Fig. 3(c): 3-bit needs only small rank;
  2-bit demands much higher rank to suppress weight discrepancy. Any layer-local objective
  (weight-space OR activation-space) is rank-limited at 2-bit because the residual spectrum is flat.
- **Their escape (NOT calibration-only — gradient training):** model-level loss.
  Eq. 5: argmin_{L1,L2} ‖Y_N − Y_N^q‖_F on **final-layer hidden states** (FP teacher vs
  quantized+adapters), plus Eq. 6 causal-LM ground-truth loss, weighted 0.5/0.5.
  Layers cooperate — cancel each other's errors instead of each fixing its own.
  Loss ablation (Table 7, avg acc): Linear-loss 45.02 < Layer-loss 51.34 < Model-loss 52.77 <
  Model+GT **54.71** — a 9.7-point verdict for model-level over layer-level at 2-bit.
- **Training:** Adam, lr 1e-4, batch 8, ≤10K steps, ~40 min on one A100/A6000; data = 256 C4
  sentences × 512 tokens (~131K tokens). LoRA init random/zero (not SVD; EoRA-init adds +1.3).
- **The rank-insensitivity table (Tables 4–5, OmniQuant 2-bit LLaMA-2-7B, acc / ppl):**

  | Rank | SVD-based | RILQ |
  |---|---|---|
  | 16 | 51.63 / 11.45 | **55.54 / 9.24** |
  | 64 | 52.01 / 10.96 | 55.52 / 9.18 |
  | 256 | 54.63 / 9.56 | 55.21 / 9.17 |

  Std-dev across ranks: SVD σ=0.69, RILQ σ=**0.07**. RILQ @ r16 beats SVD @ r256: once the
  objective goes global, rank buys almost nothing.
- **Main 2-bit results (Table 1, LLaMA-2-7B W2A16, avg commonsense acc, base→+RILQ):** LoftQ
  37.06→49.54; OmniQuant 51.88→55.52; QuIP# 54.38→60.12; QuaRot 45.41→55.44. Ppl: OmniQuant
  11.19→9.18; QuIP# 8.90→6.94. Consistent through 70B (Table 9); no width-dependence analysis.
- **Allocation / loop:** uniform rank; adapters trained jointly across all layers (that joint
  training IS the mechanism); no quantize-correct alternation.
- **Code:** github.com/aiha-lab/RILQ (license unverified).

---

## Part II — The joint / alternating lineage

### 2.1 LPLR — arXiv:2310.11028v1 (Saha, Srivastava, Pilanci; NeurIPS 2023)

Theory precursor to CALDERA. A ≈ LR with both factors low-precision, via randomized sketching —
data-oblivious plain Frobenius objective.

- **Algorithm 1:** Gaussian sketch S (S_ij ∼ N(0,1/m)); L = Q(AS) (quantized column-space basis);
  W* = argmin ‖Q(AS)W − A‖_F (closed form Q(AS)†A); R = Q′(W*). The sketch's equalization property
  makes vector-quantization error O(1)/coordinate instead of O(d).
- **Theorem 3.2:** with row norms O(1) and dynamic ranges set as prescribed,
  **E‖LR − A‖²_F ≤ (1 + k/(m−k−1))·‖A_k − A‖²_F + ε.**
  The oversampling factor is benign; the bound **floors at the tail energy Σ_{i>k}σᵢ²** — for
  near-flat residual spectra that tail ≈ (1 − k/d)·‖A‖²_F, i.e. fixed-k capture decays ∝ k/d.
  Bit budget scales with κ(A_k)/ε (conditioning explicit). Compute O(ndm), SVD-free.
- **LLM numbers:** relative Frobenius error only (LLaMA-7B, Table 8): B=B′=8/B_nq=4 → LPLR 0.672 vs
  naive 0.836; no perplexity, no 2-bit LLM tables (1-bit results are image/embedding experiments).
- **Code:** github.com/pilancilab/matrix-compressor, MIT.

### 2.2 LoftQ — arXiv:2310.08659v4 (Li et al.; Georgia Tech/Microsoft; ICLR 2024)

- **Objective:** min_{Q,A,B} ‖W − Q − ABᵀ‖_F — plain unweighted Frobenius, fully data-free init
  (data enters only in downstream fine-tuning).
- **Alternating algorithm (T steps):** Q_t = q_N(W − A_{t−1}B_{t−1}ᵀ); then SVD(W − Q_t) with
  square-root split. T=1 reduces to LoRC. Quantizers: NF4/NF2/uniform.
- **Ablation on T (Fig. 3, T ∈ {0,1,5,10}):** "alternating optimization yields substantial
  improvements even with a minimal alternating step"; diminishing returns past T≈5. **T=1 captures
  most of the gain — the win is quantize-aware init, not deep joint optimization.**
- **Rank regimes:** DeBERTa r16/32 @2-bit; BART r8/16; **LLaMA-2-7b/13b: r=64 only** across
  2/2.25/2.5/3/4 bits (mixed = first k layers 4-bit). Constant r again.
- **2-bit results (Table 5, NF2 r64 — POST-FINE-TUNING, not PTQ):** LLaMA-2-7b Wiki2 **7.85**
  (QLoRA: fails to converge), GSM8K 20.9%; 13b 7.69 / 25.4%. Mixed 2.25–2.5 bit: 7B → 5.78 @
  ~2.9 bits.
- **Capture-vs-d:** none. **Allocation:** uniform (depth-based mixed precision only).
- **Code:** github.com/yxli2123/LoftQ, MIT; in HF PEFT.

### 2.3 LQ-LoRA — arXiv:2311.12023v4 (Guo, Greengard, Xing, Kim; ICLR 2024)

- **Algorithm:** iterate L₁,L₂ ← SVD(W − Q, r); Q ← Quantize(W − L₁L₂) (NF + double quantization);
  **early-stop when the error increases** (no fixed T). (LoftQ order reversed within an iteration.)
- **Fisher-weighted variant (the published twin of our diagonal whitening, two-sided):**
  objective **‖√F ⊙ (W − Q − L₁L₂)‖²_F** (elementwise Fisher). Weighted low-rank is NP-hard in
  general → approximate F as rank-one: solve ‖D_row(E − L₁L₂)D_col‖²_F with D_row, D_col diagonal
  from row/col means of √F; SVD the pre-scaled matrix, unscale: L₁ = D_row⁻¹U√Σ, L₂ = √ΣVᵀD_col⁻¹.
  Fisher from **10,000 C4 samples × 1024 tokens** (gradients required, unlike imatrix).
- **ILP mixed-precision allocation (Eq. 3):** per-matrix quantizer configs c = (b₀,b₁,b₂,B₀,B₁),
  3⁵ = 243 options; min Σ_i Σ_c error(W_i, c)·X[i,c] s.t. Σ storage·X ≤ budget, one config per
  matrix. **Allocates precision per matrix at FIXED rank — never rank itself.**
- **Rank:** r=64 everywhere (70B → r/d 0.0078). Table 5: LQ-LoRA benefits more from rank than QLoRA.
- **Sub-3-bit (Table 6, Fisher, post-continual-training):** LLaMA-2-7B @2.75-bit: Wiki 6.78, C4
  8.25, MMLU 0.43. 70B @2.75: Wiki 4.32, MMLU **0.67** (27 GB). 2.5-bit degrades sharply
  (70B Wiki 4.80).
- **Factors kept fp16/bf16 (trainable) — never quantized.**
- **Code:** github.com/HanGuo97/lq-lora, MIT.

### 2.4 QLoRA — arXiv:2305.14314 (Dettmers et al.; NeurIPS 2023) — context only

The frozen-NF4-base + LoRA baseline this lineage corrects. Contributes NF4 (quantile-optimal for
N(0,1) weights), double quantization (~0.37 bits/param), paged optimizers. LoRA init B=0 — **no
residual correction at all**; diverges at 2-bit (LoftQ's "N.A." rows). r=64, α=16, adapters on all
linear layers; rank barely matters, all-layer coverage critical. 4-bit only. MIT.

### 2.5 CALDERA — arXiv:2405.18886v2 (Saha, Sagan, Srivastava, Goldsmith, Pilanci; NeurIPS 2024) ★ the 2-bit reference

- **Objective (Eq. 1):** min_{Q,L,R} **‖(Q + LR − W)Xᵀ‖²_F** s.t. Q, L, R all on quantized lattices;
  H = (1/m)XᵀX full calibration Gram. QuIP#-style randomized-Hadamard incoherence on both sides
  (decompose W̃ = H_Lᵀ W H_R; H̃ = H_Rᵀ H H_R).
- **Algorithm 1:** init L₀=R₀=0; iterate T_out times:
  (1) **Q-update:** Q ← LDLQ(W − LR) — QuIP's column-wise quantization with linear error feedback
  from the LDL decomposition of mH;
  (2) **LR-update:** LPLRFactorize(W − Q, k, X, ...) minimizing ‖(LR − A)Xᵀ‖²_F, with inner
  alternation T_in: closed forms **R̀ = L†AHH†** and **L̀ = AHRᵀ(RHRᵀ)⁻¹**, each followed by
  requantization of the factor, with best-iterate tracking.
  Init of the LR factorization: rank-constrained regression, globally optimal via two SVDs
  (Lemma 4.2). Finally optionally LoRA-fine-tune top-r factors in BF16.
- **Factor quantization:** B_L = B_R = 4-bit E8 lattice, B_Q = 2-bit ("typically B_Q < B_L, B_R").
- **Theorem 4.1 — the capture-vs-dimension bound (our collapse, in print):**
  (1/nm)·E‖(Q + LR − W)Xᵀ‖²_F ≤ (1/n)Σ_{i>k} Eλᵢ(ηDηᵀ) + ε
  ≲ [4dλ_max R²/(π(2^{B_Q}−1)²)] · **(1 − k/2n)²** + ε,
  where η is the LDLQ quantization error. The low-rank benefit is the removed top-k eigenvalue mass
  of the noise spectrum, entering as (1 − k/2n)². **Two regimes stated explicitly:**
  (i) k ≪ n: at fixed small rank the benefit vanishes with dimension and the bit budget must absorb
  it (B_Q must grow like log₂(√(nmd)·...));
  (ii) k = O(n): at fixed B_Q, error ≤ 2ε requires **k ≥ 2n − c√n — rank proportional to width.**
  For near-isotropic η the tail sum is ≈ (1 − k/n) of total energy: capture ∝ r/d.
  Our numbers sit exactly on this: k/2n ≈ 0.031 at 0.6B vs 0.006 at 27B.
- **2-bit results (Tables 1–2, no fine-tuning, B_Q=2):**

  | Model (FP16 Wiki2) | config | avg bits | Wiki2 | C4 |
  |---|---|---|---|---|
  | LLaMA-2-7B (5.12) | QuIP# rank-0 | 2.0 | 8.23 | 10.8 |
  | | r64 / 4-bit factors | 2.1 | 7.37 | 9.74 |
  | | r128 / 4b | 2.2 | 6.76 | 8.83 |
  | | r256 / 4b | 2.4 | **6.19** | **8.14** |
  | LLaMA-2-13B (4.57) | r64→r256 | 2.08–2.32 | 6.04 → **5.41** | 7.98 → **7.21** |
  | LLaMA-2-70B (3.12) | QuIP# rank-0 | 2.0 | 4.16 | 6.01 |
  | | r128 / 4b | 2.1 | 4.11 | 5.95 |
  | | r256 / 4b | 2.2 | **3.98** | **5.76** |
  | LLaMA-3-8B (5.54) | rank-0 → r256 | 2.0→2.4 | 13.8 → **8.22** | 15.6 → **9.56** |

  **Read the width-scaling out of their own tables: rank-64 buys 0.86 ppl at 7B (8.23→7.37) but
  rank-128 buys only 0.05 ppl at 70B (4.16→4.11)** — the fixed-rank benefit collapses with scale
  exactly as (1 − k/2n)² predicts; they compensate by always reporting r=256 at 70B.
- **Fine-tuned (Table 5):** 7B r256 +RHT-FT @2.7 bits: Wiki2 **5.55**; r64 @2.4: 5.89. In-table
  baselines: LoftQ 2-bit r64 7.85; LQ-LoRA 2.75-bit 5.67 @2.95 bits.
- **Throughput (Table 6, A10G bs1):** FP16 7B 31.75 tok/s; QuIP# 2-bit 87.74; CALDERA r64 16-bit
  factors 61.68; r64/128/256 4-bit factors 46.29/46.19/45.89 (lattice dequant on factors is slower
  than fp16 factors!); 70B QuIP# 18.18 vs CALDERA r256 4.66. No fused LR kernels.
- **Calibration:** RedPajama (extracted as 256–512 samples, 4096/8192-token contexts, RelaxML
  precomputed Hessians; sub-extraction — verify before citing precisely). No damping mentioned.
- **Allocation:** uniform rank across layers/matrices, no per-layer rule.
- **Code:** github.com/pilancilab/caldera — **GitHub API license: null** (all-rights-reserved);
  depends on QuIP# and LoftQ.

### 2.6 ODLRI — arXiv:2506.02077v1 (Cho, Kim, Jeon, Lee, Lee, No; Findings of ACL 2025)

CALDERA follow-up: same alternation, better init, roles inverted.

- **Objective / init:** L₀,R₀ = argmin ‖(W − LR) H_o (W − LR)ᵀ‖ where **H_o is the calibration
  Hessian restricted to top-k outlier activation channels** (X split X_o + X_r by channel norm);
  solved by selective Cholesky whitening + SVD of WS_o. **Low-rank captures activation-salient
  directions BEFORE quantization; Q expresses W − L₀R₀** — the mirror image of residual correction.
  Demonstrably shrinks quantization scales of Q. Then CALDERA-style alternation
  (Q_t ← Quantize(W − L_{t−1}R_{t−1}); L_t,R_t ← LRApprox(W − Q_t)) with QuIP# E8 2-bit.
- **Capture stat:** with outlier-restricted H_o, rank-r init attains **0.999** normalized capture on
  the outlier subspace WX_o while keeping 0.903 on non-outliers — rank spent on structure, not bulk.
- **Calibration:** full H = XXᵀ, 256 RedPajama samples, 4096-token (8192 for Llama3) contexts.
- **Ranks:** uniform per model, r ∈ {64,128,256} (70B: 128/256); grid does NOT grow with width;
  gains largest at r=256.
- **2-bit results (Table 2; Q 2-bit + LR 4-bit; CALDERA → +ODLRI, Wiki2):** Llama2-7B r64
  7.34→7.20, r128 6.90→6.72, r256 6.47→6.33; Llama2-13B r256 5.56→5.46; **Llama2-70B r256
  3.99→3.94**; Llama3-8B r256 6.42→6.18; Mistral-7B 5.77→5.69. Note 7B rank trend 64→128→256:
  7.34→6.90→6.47 — still improving at r/d = 0.0625, no saturation. No 3-bit tables.
- **Read:** thin gains (0.05–0.15 ppl) but directional signal: at 2-bit, rank pays more on
  structured (outlier) directions than on the noise-like residual.

### 2.7 CLoQ — arXiv:2501.18475 (Deng, Zhang, Wang, Gurses, Yang, Yin; SUNY Albany/IBM, 2025)

- **Objective:** min_{Q,A,B} ‖X(Q + ABᵀ − W)‖²_F, solved **closed-form, gradient-free, no
  alternation** (Thm 3.1): from H = XᵀX (full Gram), R = Σ_H^{1/2}U_Hᵀ, then
  ABᵀ = R⁻¹·LR_r(R·ΔW) — provably optimal for fixed Q. Q via GPTQ-class solver + MagR
  preprocessing. Calibration-only **initialization** for subsequent LoRA fine-tuning (Q frozen).
- **Rank:** r=64 at d=4096 (r/d ≈ 0.016).
- **INT2 numbers (post-fine-tune):** Llama2-7B Wiki **6.51 vs LoftQ 7.85**; GSM8K **33.7% vs
  20.9%**; Llama2-13B 5.73 vs 7.69, GSM8K 41.7% vs 25.4%; commonsense INT2 7B avg 80.1% vs 67.0%.
  **Correct whitened closed-form init beats LoftQ's alternating minimization at 2-bit** —
  initialization quality > iteration count.
- **Code:** github.com/AozhongZhang/CLoQ (no license stated).

### 2.8 ProjQ — arXiv:2606.00494 (Yu, Zhang, Wang, Lasaulce, Debbah; ICML 2026)

Attacks our exact failure mode from the quantizer side: PTQ noise is spread across the spectrum,
so a rank-r corrector can't fix it — **shape the noise onto a low-rank manifold during
quantization.**

- **Objective:** min_{Ŵ∈Q} min_{B,A} ‖(W − Ŵ + BA)X‖²_F; alternating **P-step** (truncated SVD of
  (W − Ŵ)X → projector P = V_rV_rᵀ) and **W-step** (quantize against the orthogonal complement
  X(I − P) — spend bits only on directions the corrector cannot fix).
- **Ranks:** r = 64 uniform; 2/3/4-bit.
- **Numbers:** Llama2-7B 2-bit C4 ppl **21.50** vs LoftQ 28.77, GPTQ+SVD-LLM 26.26; 3-bit Wiki2
  5.69 ≈ 4-bit baselines. No per-layer allocation or width analysis.
- **Read:** confirms the diagnosis (unshaped residual is low-rank-incompressible) and demonstrates
  quantizer/corrector co-adaptation as the fix.

### 2.9 GPTQ-intrinsic LoRA — arXiv:2606.01412 (Shihao Zhang, Rayan Saab; UCSD, 2026) ★ the theory paper

- **First information-theoretic lower bounds** for min ‖XW − X(Q + LR)‖²_F under finite-alphabet Q
  and bounded LR: bound scales as (2B+1)^{−J/(J−1)} with **J = NN′/(r(N+N′+1)+2).**
  **Fixed r ⇒ r(N+N′)/NN′ ≈ 2r/d → 0 as width grows: the low-rank term's share of the achievable
  error budget vanishes ~1/d; rank must scale ~linearly in width to retain constant share.**
  The closest thing to a proof of our measured collapse.
- **Algorithm:** fold the correction INTO GPTQ — choose L = V_r (top right singular vectors of
  calibration X), augment the Hessian (run GPTQ on [W;0] with augmented X including X̂ = XL); the
  low-rank part is co-optimized during, not after, quantization; error bounds where GPTQ's ‖X‖²_F
  dependence is replaced by the rank-r residual ‖X − X_r‖²_F. Plus "Bid-Up": alternate fixed-grid
  requantization with optimal low-rank compensation, provably non-increasing error.
- **Experiments:** Qwen3 LLMs + DeiT, sub-4-bit, beats GPTQ and GPTQ-then-low-rank. Exact table
  rows did not survive HTML extraction — **pull the PDF before citing numbers.**

### 2.10 Remaining alternating-family (short entries)

- **TwinQuant — arXiv:2606.01556 (ICML 2026):** W4; high-precision low-rank outlier subspace +
  low-precision residual, but subspaces **learned on Stiefel/GL manifolds** so both components are
  jointly quantization-friendly (vs fixed SVD). Custom kernels, 1.8× end-to-end over FP16. Also
  observes LLaMA3-8B weight spectra "remaining relatively flat even beyond rank 256."
- **LoPRo — arXiv:2601.19675:** fine-tuning-free 2/3-bit; block-wise permutation grouping similar-
  importance columns + Walsh–Hadamard rotation within blocks (salient blocks exempted) + R1SVD
  mixed-precision decomposition; preconditioning the residual *before quantizing it*.
- **FBQuant — arXiv:2501.16385 (IJCAI 2025):** sub-branch correctors **overfit calibration**;
  negative-feedback construction bounds reconstructed weights; CUDA kernel cuts 60% of sub-branch
  latency; 3-bit Llama2-7B +1.2% zero-shot. The "regularize the corrector" cautionary result.
- **ApiQ — arXiv:2402.05147v3:** joint init of LoRA + quantized weights to preserve activations;
  fine-tuning framework, 2-bit focus.
- **QuAILoRA — arXiv:2410.14713 (NeurIPS 2024 wksp):** calibrated-SVD quantization-aware LoRA init;
  4-bit; recovers ~75% of the 4→8-bit ppl benefit.
- **LoQT — arXiv:2405.16528v3:** factors periodically merged into quantized weights during
  training — quantized pre-training, not PTQ.
- **IR-QLoRA (ICML 2024, commonly cited 2402.05445 — ID not re-verified):** entropy-maximizing
  quantization + information-elastic LoRA; fine-tuning method.
- **Codebook-init basin — arXiv:2604.08118:** initialization determines the optimization basin for
  ≤2-bit codebook quantization — echoes ODLRI/CLoQ: init dominates.

---

## Part III — The 2025–2026 wave (allocation, saliency, routing, theory)

### 3.1 SRR ("Preserve-Then-Quantize") — arXiv:2602.02001 (Cho, Jeon, Kim, Jeon, No; ICML 2026) ★ first principled rank-split

Same group as ODLRI; the most relevant single find of the citation chase.

- **Objective:** min_{0≤k≤r} min_{rank(Δ₁)≤k, rank(Δ₂)≤r−k}
  ‖S(W − (Δ₁ + Q(W − Δ₁) + Δ₂))‖_F — the rank budget r is **split**: k ranks preserve the top
  singular subspace of the activation-scaled weight SW *before* quantization; r−k reconstruct the
  quantization error afterward.
- **Theory-guided split:** optimal error factorizes as L(k)² = ‖SE_k‖²_F · ρ_{r−k}(SE_k)
  (error energy × unrecoverable spectral fraction); under a **random-matrix proxy for the
  quantization-error spectrum**, k* = argmin ρ_k(SW)·ρ_{r−k}(SE) with E a uniform random probe.
  **k* is chosen per weight matrix and differs across Q/K/V/O** — the first principled per-matrix
  rank allocation inside the QEC lineage. Their ρ (spectral concentration of error) is exactly the
  quantity whose width-scaling we measured; the random-probe model implicitly predicts capture ~ r/d.
  No explicit width-scaling analysis (only that k* is stable ±1 across seeds).
- **Numbers (modest):** QERA-exact → +SRR, Llama2-7B: 10.84→10.76 (3-bit MXINT), 10.06→9.98
  (GPTQ 3-bit), 15.03→14.55 (QuIP# 2-bit); Llama3.1-70B 6.68→6.63. 2-bit QPEFT +5.9 GLUE.
  Ranks r ∈ {32,64}. Project: ai-isl.github.io/srr.

### 3.2 FLRQ — arXiv:2601.05684 (Gu, Hu, Niu, Liu; Jan 2026) ★ strongest anti-uniform-rank evidence

- **Per-layer rank analysis (Llama2-7B):** optimal per-layer ranks span 0–128 — 7 layers want ≤8,
  19 want 8–16, 44 want 16–32... **fixed-rank schemes waste most of the budget.**
- **Allocator:** R1-Sketch (rank-1 Gaussian sketch, no full SVD) iteratively peels components; a
  rank is accepted while precision gain q = (d+d′)/d (d′ = log₂ of amax reduction of the residual
  still to be quantized) exceeds memory cost k = 1 + d_fp·r(m+n)/(d·m·n) — **rank is bought only
  while it shrinks the quantization scale faster than it costs bits.** Plus BLC (clipping-aware
  low-rank fit). Cheap enough to replicate in an afternoon.
- **Numbers:** Llama2-7B 2-bit Wiki2 **9.14 with average rank 39 (0.24 extra bpw) vs LQER rank-256
  (1.60 extra bpw) at 10.33**; 3-bit 5.88 vs OmniQuant 6.03. Runtime 2.5 h vs AffineQuant 14.4 h.

### 3.3 SERQ — arXiv:2603.08185v1 (Park, Kim, Choi; Mar 2026)

- **Single-matrix, row-structured compensation** (not spectral): prior two-factor form forces an
  intermediate requantization (Ŷ = X_qW_q + Q(X_qL₁)L₂); SERQ uses ONE thin R ∈ R^{r×d}:
  Ŷ = X̂_qŴ_q + X̃_{s,q}·Q(R), where **R = the quantization error of the r most activation-salient
  weight rows only**, and only the matching activation channels enter the side path. "Rank r" is
  really row-sparsity r.
- **Pipeline (all offline):** (1) static activation flattening (SmoothQuant-style scales folded
  into weights); (2) saliency ranking by per-channel activation scale, top-r rows' error → R;
  (3) offline weight/column permutation propagated to preceding layers (no runtime gather).
- **Calibration:** 128 WikiText-2 samples, activation scales only. **Rank:** r=128 primary
  ("equivalent effective bits to two rank-64 factors"); ablation r ∈ {0..256}, fast saturation.
- **Numbers (W4 regime only, no 2/3-bit):** Llama2-7B W4A4: L²QER ppl 7.37 → SERQ **5.97**
  (SpinQuant 6.0); Llama3-8B W4A4: L²QER 11.44 → SERQ **7.75**. W4A8: 5.59 vs L²QER 5.83.
- **Read:** two-factor low-rank degrades badly at W4A4 while row-structured correction survives —
  the flattened residual is spectrally flat but **row-heterogeneous**. Sidesteps width-collapse by
  not asking SVD to compress an unstructured residual. Code: github.com/acalabys/SERQ, CC BY 4.0.

### 3.4 LRC — arXiv:2412.07902v1 (Scetbon & Hensman; Microsoft Research) ★ rank ∝ d, in print

- **Objective:** ‖W_ℓX_ℓ − Ŵ_ℓQ_a(X_ℓ) − U_ℓV_ℓᵀX_ℓ‖²₂ (corrector acts on UNQUANTIZED
  activations); **alternating** GPTQ-step / closed-form eigendecomposition step. Full covariances
  Σx = XXᵀ + εI (ε = 1e-2·Tr(XXᵀ)/d_in — a concrete damping recipe), Σy over quantized
  activations, cross-covariance Σxy; 128×2048-token WikiText-2 calibration.
- **The rank statement:** no adaptive formula — flat fractions: **r/d = 10% halves the W4A4
  accuracy gap; r/d = 30% closes it** (Llama-3 8B W4A4: ppl 7.94, 0.698 avg acc vs 0.733 FP16).
  No 2/3-bit results. **The strongest published empirical corroboration that useful compensation
  rank scales proportionally with width — hundreds-to-thousands of ranks on 7B.**

### 3.5 ResQ — arXiv:2412.14363 (Saxena, Sharify, Roy, Wang; Purdue/d-Matrix; ICML 2025) ★ r = d/8

- **The one method with rank explicitly proportional to d:** keep a rank **r = d/8** ("in practice
  1/8 of the hidden dimension") subspace in 8-bit, rest 4-bit; projection P from top-r eigenvectors
  of activation covariance XXᵀ. Theorem 4.2: error bound minimized by maximizing ‖XP_h‖_F ⇒ PCA is
  the optimal high-precision subspace under the bound.
- **Numbers (W4A4KV4 + d/8 in 8-bit, ppl):** Llama-2-13B 5.1 (SpinQuant 5.2, QuaRot 5.4, FP16 4.9);
  Llama-3-8B **7.1** (7.4/7.8; FP16 6.1); Llama-3-70B **4.1** (6.2/5.7; FP16 2.9) — "up to 33%
  lower perplexity than SpinQuant." Code: github.com/utkarsh-dmx/project-resq.

### 3.6 GuidedQuant — arXiv:2505.07004v4 (Kim et al.; SNU/Samsung/Google; ICML 2025)

Not low-rank — the strongest published 2-bit *whitening* (what "full covariance done right" buys):

- **Objective:** block-diagonal Fisher per output-channel group, Σ_k Σ_{j∈J_k}
  (w_j − ŵ_j)ᵀH̄_k(w_j − ŵ_j), with end-loss gradient weighting. Explicitly richer than diagonal
  Fisher: "the Fisher matrix exhibits strong off-diagonal values and a prominent block-diagonal
  structure."
- **2-bit Wiki2 ppl:** scalar LNQ+Guided — Llama-2-7B **8.83** (SqueezeLLM 39.58), 13B 7.26, 70B
  5.04; vector QTIP+Guided — 7B **6.11** (AQLM 6.59), 13B 5.33, 70B **3.80**. 3-bit: 7B 5.57,
  70B 3.47. Code: github.com/snu-mllab/GuidedQuant.

### 3.7 SVDQuant — arXiv:2411.05007v2 (Li et al.; MIT Han Lab; ICLR 2025) — why rank 32 works for them

SVDs the **smoothed weight itself** (activation outliers migrated in), not a residual: "The first
32 singular values of Ŵ exhibit a steep drop, while the remaining values are much more gradual."
"Increasing the rank from 16 to 64 significantly enhances image quality but increases parameter and
latency overhead... we select a rank of 32." Diffusion only (FLUX-12B etc.), 4-bit. **The lesson:
small rank succeeds only because smoothing manufactures a spike on top of the MP bulk — the
"much more gradual" tail is exactly the bulk we're fighting.** Fusion data (unfused +50% latency →
5–10% fused) remains the llama.cpp warning.

### 3.8 ARHQ — arXiv:2605.00140v1 (Wang, Sun, Sakaguchi; Tohoku, 2026)

"Low-bit quantization error is not generally isotropic and need not align with those
[activation-whitening] directions" — whitens with the **residual covariance** G_x = (1/N)E_xᵀE_x
(E_x = Q_x(X) − X), objective min_{rank(L)≤r} ‖(W − L)G_x^{1/2}‖²_F. r=128 fixed, only Qwen3-4B,
no 7B+/2-bit tables. Code: github.com/BeautMoonQ/ARHQ, CC BY-SA 4.0. Early-stage but conceptually
distinct: whiten by where the *error* lives, not where activations are large.

### 3.9 Cross-layer & input-adaptive compensation

- **RILQ (§1.5)** — model-level loss, the cross-layer verdict.
- **Cross-Layer Error Compensation — arXiv:2607.14630v1 (Noda, Jul 2026):** recursion
  e_{ℓ+1} = Â_ℓ(e_ℓ) + q_ℓ proven **exact** (no first-order approximation) for nonlinear layers
  when instantiated on the quantized net; joint autodiff optimization of all layers' discrete
  codes/scales; random-projected covariances (k=64); no low-rank term. Only 1.5B-scale (1.125 bits:
  ppl ratio 9.56±0.15 over FP16); explicitly warns 8B+ is extrapolation. CC BY 4.0, patent pending.
- **SPEAR — arXiv:2606.11244:** quantization error is **highly token-dependent** — static
  correctors over-correct easy tokens, under-correct hard ones; per-token-gated compensators only
  at the most error-sensitive layers (CKA-guided selection); recovers 56–75% of the W4→FP16 ppl gap
  at <1% memory. The CKA layer-sensitivity diagnostic is a ready-made allocation signal.
- **Quant Experts — arXiv:2602.24059 (CVPR 2026):** VLMs; mixture-of-experts of low-rank error
  compensators with token-aware routing replacing one static corrector.
- **MixQuant — arXiv:2607.23047:** per-layer BIT allocation with cross-layer coupling — a layer's
  sensitivity depends on upstream layers' bitwidths; marginalize over random upstream configs for
  budget-agnostic scores, one greedy pass any budget. Llama2-7B: ppl 12.43→10.70 at tightest
  budget. **Applies verbatim to rank allocation: a per-layer rank score measured against an FP
  upstream context will misallocate.**
- **GlowQ — arXiv:2603.25385:** LQER-lineage, systems-flavored: one shared right factor per
  input-sharing group (QKV share input); GlowQ-S restores only the layers with highest accuracy
  benefit. TTFB −5.6/−23.4%, throughput +9.6/+37.4%, ppl −0.17%. Another vote that most layers
  don't repay their rank budget.

### 3.10 Allocation methods outside the quant lineage (rules in math)

- **UniRank — arXiv:2606.21847v1 (2026):** global sort-and-truncate on
  s_{ℓ,i} = I_ℓ·(σ²_{ℓ,i}/‖Σ_ℓ‖²_F), I_ℓ = 1 − E[cos(f_{ℓ−1}(x), f_ℓ(x))]; closed-form from
  calibration stats, no training. SVD-only: Llama3.1-8B @25%: ppl 10.29; up to 61.8% ppl reduction
  vs uniform rank.
- **ARA — arXiv:2510.19389v1 (2025):** gradient-learned per-module rank masks under a global budget
  (L = L_CE + guidance + compression penalty). At 80% compression Llama2-7B: ppl 6.42 vs 8.38
  uniform. Finding: q/k tolerate compression; v/gate/down want full rank (NB: partially opposite to
  our E05 map, where q/k were the steep-capture winners — different objective: they truncate W, we
  correct residuals).
- **AutoQRA — arXiv:2602.22268:** the only paper treating **(bit-width, rank) as a coupled
  per-layer budget** — multi-fidelity evolutionary search + trust-region BO; QPEFT-side (training).
- **StatLoRA 2607.20205, IGU-LoRA 2603.13792, Differentiable Rank Selection 2512.13733,
  Layer-wise Dynamic Rank 2509.25622:** LoRA-fine-tuning-side rank allocation, one-liners.

### 3.11 Adjacent regimes (bracketing ours)

- **LittleBit — arXiv:2506.13771 (NeurIPS 2025):** sub-1-bit (to 0.1 bpw): the ENTIRE weight is a
  binarized low-rank factorization (Dual-SVID) + multi-scale compensation; Llama2-13B < 0.9 GB.
  Brackets our regime from below: at extreme rates low-rank structure carries everything.
- **D-QRELO — arXiv:2604.16940:** delta (SFT−base) compression = 1-bit delta + low-rank correction
  of ITS quantization error — structurally our recipe applied to deltas.
- **TileQ — arXiv:2605.09281:** MoE 2D-tiled structured low-rank quantization, factors shared
  across expert dims; −10× memory, ~5% latency. Relevant if we target the 35B-A3B.
- **MoE low-rank compensators — arXiv:2512.17073:** router-guided compensation of only Top-n
  experts per token; offloading/NDP focus.
- **Recover-LoRA — arXiv:2606.04238 (AMD):** RILQ-family training route; 2-bit + LoRA via logit
  distillation on 10k synthetic samples; selective mixed precision (W4 with W2 GateUp) +7.5–23.3%
  TPS; 4B–20B models.
- **LCQ 2405.20973** (rank>1 codebooks), **NoWag 2504.14569**, **SLiM 2410.09615** (quant + 2:4
  sparsity + saliency low-rank adapter), **ASER 2411.07762** (whitened-SVD compensation + smoothing,
  W4A8 — direct LQER sibling), **CDQuant 2406.17542** (coordinate-descent GPTQ, composable),
  **LR-QAT 2406.06385**, **LLaVA-FA 2602.00135** (frequency-domain joint decomposition),
  **Metis 2509.00404** (residual-after-spectral-split is flat — independent flatness confirmation),
  **OBD-LLM 2604.00821** (pure low-rank; Kronecker-Hessian shows input-only whitening suboptimal —
  optimal decomposition needs **bi-directional whitening**; claims 20–40% over SVD-LLM; worth
  replicating on R instead of W).
- **Surveys:** arXiv:2507.17417 (fair same-harness eval; taxonomy: activation-statistics-scaling
  [SLiM/OATS/LQER/QERA] vs iterative [LRC/CALDERA] vs training-based [LR-QAT/SLoPe]);
  arXiv:2505.05530 (low-bit DNN survey, 24 sub-categories); arXiv:2507.09428 (operator-factorization
  theory of why alternating steps converge and when they trap).

---

## Part IV — Synthesis against our four priorities

### 4.1 Does published theory predict the 27B capture collapse? YES — three independent anchors

1. **LQER §3 (Marchenko–Pastur, verbatim):** quantization residual ≈ iid round-off noise → MP
   singular spectrum → plain SVD_r captures ~r/d. The premise of our E03 null, in print since 2024.
2. **CALDERA Theorem 4.1:** low-rank benefit enters as (1 − k/2n)²; regime (i) fixed small k —
   benefit vanishes with n, bits must absorb it; regime (ii) fixed B_Q — need **k = O(n)**
   (k ≥ 2n − c√n) to hold error constant. Empirically visible in their own tables: r64 buys
   0.86 ppl at 7B, r128 buys 0.05 ppl at 70B.
3. **GPTQ-intrinsic LoRA (2606.01412):** lower bound with J = NN′/(r(N+N′+1)+2) — fixed r's share
   of the achievable error decays ~2r/d; rank must scale linearly with width for constant share.
   Supporting empirics: LRC needs r/d = 10–30% to close W4A4; ResQ hard-codes r = d/8;
   LPLR's bound floors at the tail energy Σ_{i>k}σᵢ² ≈ (1 − k/d)‖A‖² for flat spectra.
   **No paper publishes a measured capture-vs-width curve. Our 0.37→0.07 is the first
   instantiation; the three bounds above are its citation triangle.**

### 4.2 Whitening ladder — where our diagonal sits

None/Frobenius (LoRC, LoftQ, LPLR) → **diagonal** (LQER mean-|x| heuristic; QERA-approx = imatrix
diag E[x²] = US; LQ-LoRA's two-sided diagonal Fisher congruence D_row·E·D_col) → **full
covariance/Gram** (QERA-exact R_XX^{1/2}; EoRA eigenspace; CALDERA/ODLRI H = XXᵀ with LDL/Cholesky;
CLoQ Σ_H^{1/2}; LRC Σx,Σy,Σxy with ε-damping) → **beyond**: block-diagonal end-loss Fisher
(GuidedQuant), residual covariance (ARHQ), bi-directional/Kronecker (OBD-LLM).
The measured cost of staying diagonal grows as bits shrink: 4-bit ~0.05 ppl (QERA), 3-bit ~0.3 ppl
and 3× on GSM8K (QERA, EoRA-vs-ActS), 2-bit fatal (LQER heuristic inverts vs unweighted).
**Forced move confirmed: E-next must be full-covariance whitening (QERA Thm 1 math, one
eigendecomposition per tensor; d ≤ ~6K is minutes of fp64 work).**

### 4.3 Rank allocation — what exists

| Method | Rule | Domain |
|---|---|---|
| FLRQ 2601.05684 | accept rank while amax-reduction gain (d+d′)/d > byte cost 1 + d_fp·r(m+n)/(d·mn) | QEC, 2–4-bit |
| SRR 2602.02001 | per-matrix split k* = argmin ρ_k(SW)·ρ_{r−k}(SE), random-matrix probe | QEC |
| LQ-LoRA 2311.12023 | ILP over 243 quantizer configs per matrix, fixed rank | precision, not rank |
| UniRank 2606.21847 | global sort of I_ℓ·σ²_{ℓ,i}/‖Σ_ℓ‖²_F | SVD-only |
| ARA 2510.19389 | learned masks + budget penalty (backprop) | SVD-only |
| AutoQRA 2602.22268 | evolutionary + BO over (bits, rank) per layer | QPEFT (training) |
| MixQuant 2607.23047 | upstream-marginalized sensitivity, greedy | bits, not rank |
| SPEAR 2606.11244 / GlowQ-S 2603.25385 | place correctors only at sensitive layers | selective placement |

FLRQ's Llama2-7B 2-bit result (avg rank 39, 0.24 bpw → ppl 9.14, beating LQER's uniform r256 at
1.60 bpw → 10.33) is the cleanest published proof that uniform rank wastes most of its budget —
the quantitative twin of our E04c "870 MB mostly wasted" observation. MixQuant's caveat applies to
us: sensitivity measured against an FP upstream context misallocates.

### 4.4 Alternating vs two-stage; factor quantization

- LoftQ's own ablation: T=1 captures most of the gain, plateau T≈5. CLoQ: closed-form whitened
  init BEATS LoftQ's alternation at INT2 (6.51 vs 7.85). ODLRI: better init worth 0.05–0.15 ppl on
  top of CALDERA's full alternation. 2604.08118: init determines the basin at ≤2-bit.
  **Verdict: whitened closed-form init ≫ iteration count; 1–2 alternations of
  (requantize Q against W − AB) ↔ (refit AB against W − Q) is the cheap proven upgrade;
  long alternation from a bad start loses to a good one-shot.**
- Factor quantization is nearly free and converts bytes into rank: LoRC INT8 ≤0.02 ppl;
  CALDERA 4-bit E8 factors ~0.00–0.27 ppl at fixed rank and buy r256 within a 2.4-bpw budget —
  though their 4-bit-factor configs decode SLOWER than 16-bit factors (lattice dequant overhead).
  For llama.cpp: Q8_0 factors are directly supported and directly licensed by LoRC's Table 8.
- ProjQ and GPTQ-intrinsic LoRA go further: co-optimize quantizer and corrector so the noise is
  SHAPED into the correctable subspace — the direction the alternating family is converging on.

### 4.5 What the 2-bit winners actually run (d ≈ 4096–8192)

- CALDERA/ODLRI: r=256, 4-bit factors, 2.2–2.4 bpw → 70B ppl 3.94–3.98, 7B 6.19–6.33.
  Still unsaturated at r/d = 0.0625 (7B).
- LQER W2A8: k=256 at d=4096 → ppl 10.30 (weak quantizer, diagonal whitening).
- FLRQ: average rank 39 ALLOCATED (0.24 bpw) → 9.14 at 2-bit — allocation substitutes for raw rank.
- RILQ: r=16 with model-level training loss → matches r=256 (rank-insensitive once cross-layer).
- GuidedQuant + QTIP (no low-rank at all): 7B 6.11, 70B 3.80 — **the best 2-bit numbers in this
  entire file use better whitening + vector quantization, no correction branch.** The corrector is
  not the only route to 2-bit quality; it is the route that works WITHOUT changing the base format.
- EoRA one-shot at 2-bit: helps (18.22→36.89 MathQA) but needs RILQ-style training on top for the
  last points; rank sweep still climbing at r=512.

---

## Part V — Gap analysis: what nobody has tried

Ordered by how directly each gap feeds our campaign.

1. **A measured capture-vs-width curve.** Nobody publishes whitened rank-r energy capture as a
   function of hidden dimension. LQER states MP flatness, CALDERA and 2606.01412 bound it, SRR
   models it with a random probe — none instantiates numbers across scales. Our 0.37@d1024 →
   0.07@d5120 (same quantizer, same rank, same whitening) is an unclaimed, defensible measurement;
   the writeup frame is "spike-plus-MP-bulk: correctors capture the spike, bits must buy the bulk,"
   with LQER §3 + CALDERA Thm 4.1 + 2606.01412's J-formula as the citation triangle.
2. **Whitened-capture-driven rank allocation at 2-bit.** FLRQ allocates by amax-reduction; SRR
   splits preserve-vs-reconstruct; UniRank global-sorts singular energies (SVD-only). Nobody
   allocates rank by **measured whitened residual capture per tensor under a global byte budget**
   (water-filling on whitened σ², the map E05/E04c already gives us for free). Also unclaimed:
   allocation that accounts for MixQuant's finding (sensitivity depends on upstream quantized
   config, not FP context).
3. **Rank-vs-bits joint optimization per tensor, calibration-only.** LQ-LoRA's ILP fixes rank and
   allocates bits; AutoQRA couples them but needs training/search; FLRQ's criterion is greedy and
   local. A per-tensor ILP over (base quant type, rank r ∈ {0,16,...}) with measured whitened
   capture and exact GGUF byte costs — nobody has published it, and we have all inputs on disk.
4. **The llama.cpp/GGUF serving path and the byte-fair ladder comparison.** The entire lineage
   evaluates against QuIP#/OmniQuant/GPTQ bases and reports bpw, never against the K-quant/IQ
   imatrix ladder at equal TOTAL bytes, and none ships corrections as stock-servable LoRA adapters
   (EoRA is LoRA-shaped but HF/GPTQ-bound; compilade's llama.cpp LQER proposal was never built).
   Our E04 triangle (corrected Q2_K vs IQ3/Q3_K at equal bytes, KLD + tok/s measured) has no
   published counterpart.
5. **Full-covariance whitening at Q2_K/Q3_K on a 20B+ production model.** QERA-exact stops at
   3.25-bit uniform r32/64; CALDERA-class full-Gram methods use QuIP# lattices, not K-quants.
   Nobody has run R_XX^{1/2} whitening of a K-quant residual at 27B — our forced next experiment is
   also unpublished territory.
6. **Mixed whitening granularity.** Our E05 map shows diagonal suffices for q/k/gate/up but fails
   for o/down (inputs are internal activations). A pipeline that runs diagonal where it is enough
   and full covariance only where it pays (halving instrumentation cost) appears nowhere.
7. **Two-sided whitening of the residual.** OBD-LLM proves input-only whitening suboptimal for
   decomposing W (bi-directional Kronecker-Hessian); LQ-LoRA does two-sided but diagonal-Fisher
   only. Full two-sided whitening of the QUANTIZATION residual: untested by anyone.
8. **Residual-covariance whitening at scale.** ARHQ's idea (whiten by E_xᵀE_x, where the error
   lives) exists only at 4B scale with no low-bit tables — untested in the 2-bit/large-d regime
   where it would matter most.
9. **Calibration-only cross-layer compensation.** RILQ's cross-layer cooperation needs gradient
   training; 2607.14630's exact error recursion is autodiff-optimized and stops at 1.5B. A
   closed-form/calibration-only version (e.g., sequentially correcting each layer against the
   RUNNING quantized network's activations, GPTQ-style error feedback at network level) is open —
   and is the theoretically-supported escape from the per-layer rank wall.
10. **Correcting what the calibration pass cannot see.** Our E04c found 154/554 tensors (MTP/NextN
    block) invisible to llama-imatrix. No paper addresses correction (or even calibration) coverage
    for speculative/MTP heads — an upstream-worthy gap that any imatrix-based method inherits.

**Strategic read for the campaign:** the literature validates every component we built (whitened
residual SVD, LoRA-form serving, factor quantization, byte-scaling) and independently confirms both
our nulls (unweighted SVD dead; uniform small-rank diagonal correction dead at large d). The three
published levers we have not yet pulled, in order of evidence strength: full-covariance whitening
(QERA-exact/EoRA math — 3× at 3-bit), allocated rank under a byte budget (FLRQ — 2-bit ppl 9.14 at
0.24 bpw vs uniform r256's 10.33 at 1.60 bpw), and quantizer/corrector co-adaptation (ProjQ,
2606.01412). Beyond those, the unclaimed contributions we can own are #1 (the capture-vs-width law,
measured), #2/#3 (capture-driven byte-optimal allocation), and #4 (the GGUF/llama.cpp deployment
path with byte-fair evidence).

---

## Part VI — Master reference table

| arXiv ID | Name | Venue | One-line role |
|---|---|---|---|
| 2303.08302 | ZeroQuant-V2 / LoRC | preprint 2023 | Origin: unweighted SVD of E, m=8, INT8 factors free |
| 2402.02446 | LQER / L²QER | ICML 2024 | Diagonal-scaled residual SVD; the MP statement; W2 k=256 |
| 2410.06040 | QERA | ICLR 2025 | Closed-form optimum; Thm 2 = our imatrix whitening |
| 2410.21271 | EoRA | (wkshp) | Eigenspace projection; 3-bit +11 pts; rank sweep unsaturated at 512 |
| 2412.01129 | RILQ | AAAI 2025 | 2-bit error is high-rank; model-level loss ⇒ rank-insensitive |
| 2310.11028 | LPLR | NeurIPS 2023 | Sketching theory; bound floors at tail energy |
| 2310.08659 | LoftQ | ICLR 2024 | Alternating quantize↔SVD; T=1 captures most |
| 2311.12023 | LQ-LoRA | ICLR 2024 | Two-sided diagonal Fisher; ILP precision allocation |
| 2305.14314 | QLoRA | NeurIPS 2023 | NF4 baseline; no correction; diverges at 2-bit |
| 2405.18886 | CALDERA | NeurIPS 2024 | Full-Gram alternating, quantized factors, Thm 4.1 (1−k/2n)² |
| 2506.02077 | ODLRI | ACL-F 2025 | Outlier-driven init; LR before Q; 0.999 outlier capture |
| 2501.18475 | CLoQ | 2025 | Closed-form whitened init beats LoftQ at INT2 |
| 2606.00494 | ProjQ | ICML 2026 | Shape noise onto correctable subspace during quantization |
| 2606.01412 | GPTQ-intrinsic LoRA | 2026 | Lower bounds; J-formula ⇒ rank must scale with width |
| 2606.01556 | TwinQuant | ICML 2026 | Manifold-learned outlier subspace + residual |
| 2601.19675 | LoPRo | 2026 | Permutation+rotation preconditioning of residual |
| 2501.16385 | FBQuant | IJCAI 2025 | Correctors overfit calibration; negative feedback |
| 2602.02001 | SRR | ICML 2026 | Per-matrix rank split preserve-vs-reconstruct (theory) |
| 2601.05684 | FLRQ | 2026 | Per-layer rank allocation; avg r39 beats uniform r256 |
| 2603.08185 | SERQ | 2026 | Row-structured single-matrix correction; W4A4 |
| 2412.07902 | LRC | 2024 | r/d = 10–30% needed to close W4A4 — rank ∝ d empirics |
| 2412.14363 | ResQ | ICML 2025 | r = d/8 high-precision subspace, PCA-optimal |
| 2505.07004 | GuidedQuant | ICML 2025 | Block-Fisher whitening; best pure-2-bit numbers |
| 2411.05007 | SVDQuant | ICLR 2025 | Rank-32 spike-only; fusion cost data |
| 2605.00140 | ARHQ | 2026 | Whiten by residual covariance (4B-scale only) |
| 2607.14630 | Cross-layer EC | 2026 | Exact cross-layer error recursion (1.5B-scale) |
| 2606.11244 | SPEAR | 2026 | Token-dependent error; gated compensators |
| 2602.24059 | Quant Experts | CVPR 2026 | MoE of compensators, token routing |
| 2607.23047 | MixQuant | 2026 | Upstream-coupled per-layer sensitivity |
| 2603.25385 | GlowQ | 2026 | Shared factors per input group; selective restoration |
| 2606.21847 | UniRank | 2026 | Global sort-and-truncate allocation (SVD-only) |
| 2510.19389 | ARA | 2025 | Learned rank masks under budget (SVD-only) |
| 2602.22268 | AutoQRA | 2026 | Coupled (bits, rank) per-layer search (QPEFT) |
| 2506.13771 | LittleBit | NeurIPS 2025 | Sub-1-bit all-low-rank; brackets from below |
| 2604.00821 | OBD-LLM | 2026 | Bi-directional whitening optimal for decomposition |
| 2604.16940 | D-QRELO | 2026 | Our recipe applied to SFT deltas |
| 2605.09281 | TileQ | 2026 | MoE tiled low-rank quantization |
| 2512.17073 | MoE-LRC | 2025 | Router-guided selective compensation |
| 2606.04238 | Recover-LoRA | 2026 | 2-bit distillation-trained recovery (AMD) |
| 2410.14713 | QuAILoRA | NeurIPS-w 2024 | Calibrated-SVD LoRA init, 4-bit |
| 2402.05147 | ApiQ | 2024 | Joint LoRA+quant init (training) |
| 2405.16528 | LoQT | 2024 | Periodic factor merge during training |
| 2411.07762 | ASER | 2024 | Whitened-SVD compensation + smoothing, W4A8 |
| 2410.09615 | SLiM | 2024 | Quant + 2:4 sparse + saliency adapter |
| 2406.17542 | CDQuant | 2024 | Coordinate-descent quantizer, composable |
| 2406.06385 | LR-QAT | 2024 | Low-rank QAT (training) |
| 2604.08118 | Codebook-init | 2026 | Init determines basin at ≤2-bit |
| 2507.17417 | Eval survey | 2025 | Same-harness comparison + lineage taxonomy |
| 2505.05530 | Low-bit survey | 2025 | 24-subcategory map |
| 2507.09428 | Operator factorization | 2025 | Convergence theory for alternating compression |

Ruled out this sweep (off-lineage, one line): 3BASiL 2603.01376 (sparse+LR ADMM, no quant),
ROSAQ 2506.13472, TTQ 2603.19296, EinSort 2606.08565, Binary Quadratic Quant 2510.18650,
BiSCo-LLM 2607.08643, Double Binary Factorization 2505.11076, NoWag/PATCH/Pivoting/1+1>2 (pruning),
A³ 2505.12942 / NBL 2505.21077 / 2510.01718 / 2607.09694 (attention-side), SAES-SVD 2602.03051,
ResSVD/ERC-SVD 2505.20112, SigmaScale 2606.07098 (pure-SVD family, covered in the breadth survey).

---

## §9 Verification flags (carry into any paper citation pass)

- QERA calibration sample count: appendix truncated in HTML/ar5iv — UNVERIFIED (likely 32×2048
  SlimPajama, inherited from LQER). QERA Table 3 "80B" render = 70B.
- LQER scale formula (Eq. 13–14) extracted from an appendix paraphrase — verify in PDF before
  reimplementing.
- CALDERA calibration details ("256–512 RedPajama samples") from sub-extraction of App. E.2 —
  verify. CALDERA repo license: null (GitHub API) — all-rights-reserved.
- EoRA license: this sweep read CC BY-NC-SA 4.0; earlier survey read NVIDIA Source Code License-NC.
  Both non-commercial; resolve before any code contact (we won't have any — QERA math instead).
- GPTQ-intrinsic LoRA (2606.01412): table numbers did not survive HTML extraction — pull PDF.
- EoRA venue ("ICLR 2026 workshops") soft; LoftQ/LQ-LoRA ICLR-2024 venues from common knowledge,
  not re-verified on arXiv pages. IR-QLoRA arXiv ID (2402.05445) not re-verified.
- LoftQ 2-bit table numbers are POST-fine-tuning — never compare them against PTQ perplexities.
- Semantic Scholar: EoRA shows only 7 citations (indexing lag); LQER/QERA citation lists
  near-identical (verified genuine via cache-busted refetch).
