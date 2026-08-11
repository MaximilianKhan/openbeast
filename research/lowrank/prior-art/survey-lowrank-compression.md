# Prior Art: Low-Rank & Hybrid Quant+Low-Rank LLM Weight Compression

**Purpose:** ground the OpenBeast investigation of low-rank decomposition for VRAM reduction in
llama.cpp/CUDA against everything already published. Scope: SVD-on-weights methods, activation-aware
variants, and quantized-base + low-rank-residual hybrids, 2022 → mid-2026.

**Survey date:** 2026-08-03. All numbers below are taken from the cited papers/repos (arXiv abstracts,
paper tables read directly, or GitHub READMEs), fetched on the survey date. Where a paper does not
publish a number, that absence is stated rather than papered over.

**Notation.** `W ∈ R^{m×n}` is a pretrained weight. "Plain low-rank" = truncated SVD of `W` served as
two linears `y = (x·A)·B`. "Residual form" = `W ≈ Q + L·R` with `Q` a quantized base, served as
`y = Q·x + B·(A·x)` — the LoRA-shaped path llama.cpp can already execute against a quantized base.

---

## 1. Method-by-method record

### 1.1 FWSVD — Fisher-Weighted SVD (ICLR 2022)
*Hsu et al., "Language model compression with weighted low-rank factorization", arXiv:2207.00112.*

- **(a) Target:** `W` itself; SVD weighted row-wise by Fisher information (needs task gradients).
- **(b) Numbers:** BERT-era. On already-compact models it removes a further **9–30% of parameters
  with "insignificant" task-accuracy impact** (abstract). No LLM-scale results. When later applied to
  LLaMA-7B by SVD-LLM's authors, FWSVD collapses: **Wikitext2 ppl 1,727 at 20% compression**
  (vs FP16 ≈ 5.68) [SVD-LLM Table, arXiv:2403.07378].
- **(c) Calibration:** needs labeled training data + gradients (Fisher), heavier than PTQ calibration.
- **(d) Inference:** two low-rank linears `y=(x·A)·B`. No LLM speed numbers.
- **(e) Code:** no maintained official repo of note.
- **Verdict for us:** historically important (importance-weighted SVD), obsolete at LLM scale.

### 1.2 ASVD — Activation-aware SVD (arXiv 2023, v5 2025)
*Yuan et al., arXiv:2312.05821. Code: `hahnyuan/ASVD4LLM`, **MIT**.*

- **(a) Target:** `W` itself, transformed by a diagonal activation-magnitude scale:
  SVD of `(W·S)`, serve `S⁻¹` folded in. Also applies low-rank to K/V projections to shrink KV cache.
- **(b) Numbers (Wikitext2 ppl, paper Table 1):**
  | model | 95% params | 90% | 85% | plain SVD @95% |
  |---|---|---|---|---|
  | LLaMA-7B (FP16 5.68) | **5.78** | 6.09 | 6.80 | 2,800 |
  | Llama-2-7B | **5.64** | 5.93 | 6.74 | — |
  | Llama-2-13B | **4.94** | 5.12 | 5.54 | — |
  So near-lossless only to ~**10% parameter reduction**; 15% costs ~+19% ppl (7B).
  KV cache: stable above 40% KV ratio; abstract claims **50% KV memory reduction** without
  degradation. Combining ASVD with **4-bit weight quant degrades significantly** (paper Table 4);
  8-bit is fine — i.e., SVD-then-quantize composes poorly at low bits.
- **(c) Calibration:** yes — 16–32 sequences (WikiText2/C4/PTB) for activation scales + a binary
  search over per-layer ranks driven by calibration perplexity.
- **(d) Inference:** two linears per decomposed matrix (`--sigma_fuse` picks where Σ is folded);
  ships as a standard HF checkpoint. No kernel work; compute per token goes *up* unless rank is low.
- **(e) Code:** MIT, maintained enough to load with `AutoModelForCausalLM`.

### 1.3 SVD-LLM (ICLR 2025) and SVD-LLM V2 (NAACL 2025)
*Wang, Zheng, Wan, Zhang, arXiv:2403.07378; V2: arXiv:2503.12340 / ACL Anthology 2025.naacl-long.217.
Code (both): `AIoT-MLSys-Lab/SVD-LLM`, **Apache-2.0**.*

- **(a) Target:** `W`, after **truncation-aware data whitening**: Cholesky-style whitening of the
  activation Gram matrix makes truncated singular values map directly to compression loss; plus a
  sequential low-rank update of the remaining factors.
- **(b) Numbers (Wikitext2 ppl, LLaMA-7B, from paper):**
  | compression | SVD-LLM | ASVD | FWSVD | vanilla SVD |
  |---|---|---|---|---|
  | 20% | **7.73** | 11.14 | 1,727 | 20,061 |
  | 40% | **9.27** | 1,407 | 18,156 | 52,489 |
  | 60% | **15.00** | 57,057 | 32,194 | 105,474 |
  Even the best pure-SVD result is **+36% ppl at 20% memory saved** — far from lossless.
  Hybrid: SVD-LLM 40% + QuIP# 2-bit reaches **ppl 9.83**, beating the training-required OneBit
  (10.20). V2 (NAACL 2025) adds per-matrix compression-ratio allocation + loss-optimized truncation
  and "outperforms SOTA SVD-based methods" across 5 models/10 datasets — the abstract publishes
  **no headline numbers** (they live in the paper tables only).
- **(c) Calibration:** yes (whitening matrix from calibration activations).
- **(d) Inference:** two linears; memory and throughput scale ~linearly with compression ratio
  (peak-memory measured generating 128 tokens @ bs32); also supports post-compression LoRA to claw
  quality back, and GPTQ-4bit stacking.
- **(e) Code:** Apache-2.0, actively maintained, covers V1+V2.

### 1.4 Dobi-SVD (ICLR 2025) — differentiable truncation
*Wang et al., arXiv:2502.02723. Code: `wangqinsi1/Dobi-SVD`, **MIT**.*

- **(a) Target:** `W`/activations — argues the right object to truncate is the **activation**, learns
  per-matrix truncation positions differentiably (Taylor-expansion-stabilized), then reconstructs weights.
- **(b) Numbers:** the abstract/page publishes no single headline ppl; positioned as beating SVD-LLM
  at equal ratios. Treat as "current best pure-SVD family member" pending our own reproduction.
- **(c) Calibration:** yes, plus a gradient-based optimization pass (heavier than one-shot SVD-LLM).
- **(d) Inference:** two linears, same serving story as SVD-LLM.
- **(e) Code:** MIT.

### 1.5 ESPACE (NeurIPS 2024) — activation-side projection
*Sakr & Khailany (NVIDIA), arXiv:2410.05437.*

- **(a) Target:** **activations**: project `x` onto pre-calibrated principal components `P`;
  weight decomposition falls out at inference by associativity (`(xP)(P⊤W)`).
- **(b) Numbers:** **50% compression of GPT3/Llama2/Nemotron4 with small degradation — GPT3-22B
  +0.18 ppl**; at 20–40% compression GPT3-8B *improves* by up to −0.38 ppl. Caveat: ESPACE
  **retrains** the model after inserting projections — not a PTQ-style method.
- **(c) Calibration:** yes, plus full retraining (the expensive part; out of scope for us).
- **(d) Inference:** reduces GEMM time and prefill latency on existing hardware (paper claim).
- **(e) Code:** no public release found.
- **Why it matters:** the strongest published evidence that ~50% low-rank-style reduction is possible
  at near-zero quality cost *if* you pay a retraining bill.

### 1.6 ZeroQuant-V2 / LoRC (2023) — the original quant+low-rank residual
*Yao et al. (Microsoft DeepSpeed), arXiv:2303.08302.*

- **(a) Target:** **quantization residual**: `E = W − Ŵ`, plain SVD, keep rank *m*.
- **(b) Numbers (from paper, W4A16 ppl with→without fine-grained quant):** rank **m=8–16**
  (gains plateau past m≈8), parameter overhead ≈ **3m/2h ≈ 0.1–1.6%**. OPT-13B 11.42→11.36;
  OPT-30B 10.78→10.76; **OPT-66B 10.52→10.34**; BLOOM-176B 11.02→10.98. Gains are *larger* under
  W4A8. Honest read: at W4A16 INT4, LoRC's rank-8 recovery is **small** (0.02–0.2 ppl).
- **(c) Calibration:** none for LoRC itself (pure SVD of the error) — its weakness and its cheapness.
- **(d) Inference:** exactly `y = Q·x + B·(A·x)`, tiny ranks, negligible overhead.
- **(e) Code:** in DeepSpeed (Apache-2.0 lineage).

### 1.7 LQER / L²QER (ICML 2024) — activation-scaled residual SVD
*Zhang, Cheng, Constantinides, Zhao, arXiv:2402.02446. Code: `ChengZhang-98/lqer`
(archived 2025-11; **no LICENSE file at repo root** — treat as all-rights-reserved reference code).*

- **(a) Target:** quantization residual, but SVD of `S·E` where `S` is an **activation-induced scale
  matrix** that reshapes the error's singular-value spectrum so a low rank captures what matters.
- **(b) Numbers:** **near-lossless W4A8** on six downstream tasks (paper claim) with **1.36× fewer
  hardware resources** than the leading SOTA method; no distillation/grid-search/gradients needed.
- **(c) Calibration:** yes (activation statistics for `S`).
- **(d) Inference:** "a high-rank low-precision GEMM plus a group of low-rank high-precision GEMMs
  **in parallel**" — i.e., exactly the `Q·x + B·(A·x)` shape, with regular memory access (no
  scatter/gather), which is the paper's hardware pitch.
- **(e) Code:** available but unlicensed; the successor QERA repo is the clean one.

### 1.8 QERA (ICLR 2025) — the closed-form optimum for residual reconstruction
*Zhang, Wong, Xiao, Constantinides, Zhao, arXiv:2410.06040. Code: `ChengZhang-98/qera`, **Apache-2.0**.*

- **(a) Target:** quantization residual; derives the **analytical, closed-form minimizer of layer
  *output* error** (whitened-SVD of the residual), of which LQER/LoftQ-style heuristics are
  approximations.
- **(b) Numbers (abstract):** RoBERTa-base 2-bit: **+6.05% GLUE vs LoftQ**; Llama-3.1-70B 4-bit PTQ:
  **+2.97% accuracy vs ZeroQuant-V2 (LoRC)** on average; **Wikitext2 ppl 0.28 lower than LQER**.
- **(c) Calibration:** yes (activation second-moment statistics for the whitening).
- **(d) Inference:** same parallel `Q·x + B·(A·x)` form as LQER.
- **(e) Code:** Apache-2.0 — **the best-licensed reference implementation of the exact math we need.**

### 1.9 CALDERA (NeurIPS 2024) — everything quantized, sub-2.5 bpp regime
*Saha, Sagan, Srivastava, Goldsmith, Pilanci, arXiv:2405.18886. Code: `pilancilab/caldera`
(**no LICENSE file in repo root** as of 2026-08 — usable as reference only until clarified).*

- **(a) Target:** `W ≈ Q + L·R` optimized jointly (alternating LDLQ/QuIP#-E8 quantization of `Q` and
  calibration-Hessian-weighted updates of `L,R`); **L and R are themselves quantized** (4-bit E8),
  Hadamard-incoherence preprocessing throughout.
- **(b) Numbers (paper Tables 1–2, Wikitext2 ppl, `B_Q`=2 bits, no fine-tuning):**
  | model (FP16 ppl) | QuIP# 2-bit, rank 0 | CALDERA rk64/4b (2.1 bpp) | rk128/4b (2.2) | rk256/4b (2.4) |
  |---|---|---|---|---|
  | Llama-2-7B (5.12) | 8.23 | 7.37 | 6.76 | **6.19** |
  | Llama-2-13B (4.57) | 6.06 | 6.04 (2.08) | 5.72 (2.16) | **5.41** (2.32) |
  | Llama-2-70B (3.12) | 4.16 | — | 4.11 (2.1) | **3.98** (2.2) |
  | Llama-3-8B (5.54) | 13.8 | 10.6 (2.1) | 9.21 (2.2) | **8.22** (2.4) |
  With low-rank fine-tuning (rank-64 factors in BF16), Llama-2-7B reaches **5.55–5.91 ppl at
  2.4–2.7 bpp** (Table 5), beating LoftQ (7.85 @2.4) and LQ-LoRA (5.67 @2.95).
  **Throughput cost (Table 6, A10G, bs1):** 7B: QuIP# 87.7 tok/s → CALDERA rk64-4bit **46.3 tok/s**
  (rank-64 16-bit factors: 61.7; FP16 uncompressed: 31.8) — the unfused low-rank branch roughly
  halves decode speed vs plain QuIP#, though it still beats FP16.
- **(c) Calibration:** yes (Hessians à la QuIP#).
- **(d) Inference:** `X⊤(Q + LR)⊤` with on-the-fly dequant (`CalderaQuantizedLinear`); no fused kernel.
- **(e) Code:** builds on QuIP# + LoftQ; license unclear.

### 1.10 EoRA (NVIDIA, 2024→2025) — training-free eigenspace residual compensation
*Liu et al., arXiv:2410.21271. Code: `NVlabs/EoRA`, **NVIDIA Source Code License-NC (non-commercial)**;
also integrated in GPTQModel since Feb 2025 (that integration is in GPTQModel's own licensing).*

- **(a) Target:** compression residual `ΔW = W − W_compressed` (works over quantization *or* pruning),
  projected into the **eigenspace of the calibration activations' covariance** before truncated SVD —
  i.e., approximately the QERA/LQER objective, framed as eigenspace projection. Fine-tuning-free.
- **(b) Numbers (abstract, LLaMA3-8B quantized to 3-bit):** **+10.84% ARC-C, +6.74% MathQA,
  +11.45% GSM8K** over the uncompensated quantized model; further improvable if the adapter is then
  fine-tuned. Ranks 16–128 in examples (GPTQModel uses 16/32); calibration = 1,024 C4 samples.
- **(c) Calibration:** yes, activations only; no gradients, no training.
- **(d) Inference:** **exactly `y = Q·x + B·(A·x)`** — a LoRA-shaped adapter riding on a GPTQ base;
  with their fused CUDA kernel the compensated model runs **up to 1.4× faster** than the
  FP16-compensation baseline. This is the closest published match to llama.cpp's existing LoRA path.
- **(e) Code:** NC license on NVlabs repo — fine for research, **not for shipping**; the math is
  published and QERA (Apache-2.0) covers the same objective.

### 1.11 SVDQuant (ICLR 2025 spotlight) — low-rank absorbs outliers, W4A4 base
*Li et al. (MIT Han Lab), arXiv:2411.05007. Engine: Nunchaku; quantizer: `nunchaku-tech/deepcompressor`,
**Apache-2.0** (nunchaku repo's root LICENSE file not resolvable at survey time; deepcompressor verified).*

- **(a) Target:** inverted residual — smooth activations→weights, take **SVD top-r of Ŵ (r=16–32,
  16-bit)** as the low-rank branch, then **quantize the residual** `R = Ŵ − L₁L₂` to 4 bits (W4A4).
  The low-rank branch absorbs the outliers that make W4A4 otherwise lossy.
- **(b) Numbers:** diffusion models (FLUX.1-12B, SDXL, PixArt-Σ), not LLMs: **3.5× memory reduction**,
  **3.0× faster than W4A16 on RTX 4090**, 3.1× with NVFP4 on RTX 5090.
- **(c) Calibration:** yes (smoothing statistics).
- **(d) Inference — the key lesson:** a naïvely separate low-rank branch costs **~50% of the 4-bit
  branch's latency** (extra activation memory traffic); Nunchaku's kernel fusion (share input load with
  the quant kernel, fuse `L₂` output into the epilogue) cuts that to **5–10%**. Directly transferable
  warning for a llama.cpp implementation: fuse or suffer.
- **(e) Code:** deepcompressor Apache-2.0; production-quality kernels exist (CUDA).

### 1.12 Palu (ICLR 2025) — KV-cache low-rank (brief, adjacent)
*Chang et al., arXiv:2407.21118. Code: `shadowpa0327/Palu`, **MIT**.*

Low-rank-projects K/V projection layers, caches the compressed latent, reconstructs K/V on the fly.
**50% KV-cache compression** with accuracy comparable/better than KV quantization; **up to 1.89×**
faster RoPE attention module, **2.91×** combined with quantization; ppl up to **1.19 lower** than
quant-only KV baselines. Calibration-based (Fisher-guided rank allocation). Relevant to us as the
KV-side counterpart — orthogonal to weight compression and stackable with it.

### 1.13 The 2025–2026 wave (same axis, newer)

- **ODLRI** — "Assigning Distinct Roles to Quantized and Low-Rank Matrices Toward Optimal Weight
  Decomposition" (Findings of ACL 2025, arXiv:2506.02077). `W ≈ Q + LR` where **L·R is initialized to
  capture activation-salient (outlier) columns *before* quantization**, shrinking the quantization
  scale of Q — SVDQuant's idea ported to LLM weight-only low-bit. Llama-2-7/13/70B, Llama-3-8B,
  Mistral-7B; abstract reports improved low-bit ppl/zero-shot but no headline digits.
- **SERQ** — "Saliency-Aware Low-Rank Error Reconstruction" (arXiv:2603.08185, Mar 2026). A **single**
  low-rank compensation matrix (not two sequential factors) + static activation flattening + offline
  weight permutation; claims to beat prior error-reconstruction methods at **W4A8 and W4A4** while
  preserving pure 4-bit GEMM. Numbers in paper body only; no code found.
- **SAES-SVD** (arXiv:2602.03051, Feb 2026): suppresses **cross-layer accumulated error** in SVD
  compression via closed-form second-order compensation — attacks the main failure mode of layerwise
  SVD pipelines. No abstract numbers.
- **Global Rank & Sparsity Optimization** (arXiv:2505.03801, ICLR 2026): RPCA-style split of W into
  low-rank + sparse with **global** (not per-layer) budget allocation.
- **FlashSVD** (arXiv:2508.01506, AAAI 2026) and **FlashSVD v1.5** (arXiv:2605.08314): serving-side —
  streams `(x·A)·B` through fused attention/FFN kernels, cutting **peak activation memory up to 71%**;
  v1.5 reports **up to 2.55× decode / 2.39× end-to-end speedup** for SVD-compressed transformers.
  Proof that the two-linear serving form needs (and now has) dedicated kernels.
- **BALF** (arXiv:2509.25136): budgeted, fine-tuning-free activation-aware factorization with
  closed-form rank allocation under FLOP/param budgets — results are CNN/ViT-scale.
- Adjacent, not deep-dived here: Basis Sharing (cross-layer shared SVD bases, ICLR 2025,
  arXiv:2410.03765), AdaSVD (adaptive compensation, arXiv:2502.01403), MoDeGPT (modular decomposition,
  ICLR 2025, arXiv:2408.09632) — all pure-decomposition-family variants that do not change the
  conclusions below.

---

## 2. Synthesis

### 2.1 What compression is realistically achievable near-losslessly

**(1) Plain low-rank (truncated SVD of W): effectively zero.** Vanilla SVD at 20% parameter reduction
gives ppl 20,061 on LLaMA-7B (SVD-LLM tables). Weight matrices of LLMs are not low-rank; their
*error-relevant* subspace is only exposed after activation weighting. Dead end alone — this axis is
settled in the literature.

**(2) Activation-aware low-rank: ~10% near-lossless, ~20% with a visible dent, 40–50% only with
retraining.** ASVD holds ppl to +2% at a 10% parameter cut (5.78 vs 5.68, LLaMA-7B) and +7% at 10%
on Llama-2-7B; by 15% you pay ~+19%. SVD-LLM/V2/Dobi-SVD push the *graceful degradation* frontier
(7.73 ppl @20%, 9.27 @40%) but nothing PTQ-style in this family is near-lossless past ~10–15%.
ESPACE's 50% @ +0.18 ppl (GPT3-22B) shows the ceiling moves dramatically **only with retraining**.
Meanwhile a plain llama.cpp Q4_K quant removes ~72% of FP16 bytes at roughly the quality these methods
pay for 10–15%. **Pure low-rank on W is dominated by quantization for VRAM reduction and should not
be our mechanism** — its legitimate niches are KV-cache (Palu: 50%) and FLOP reduction at prefill.

**(3) Quant-base + low-rank residual: this is the live frontier, and the numbers are strong.**
- At **W4**: LQER/QERA report near-lossless W4A8 across six tasks; QERA beats LoRC by ~3% accuracy on
  Llama-3.1-70B 4-bit. But W4 is already near-lossless in llama.cpp for large models, so the
  interesting gains are lower.
- At **W3**: EoRA recovers +6.7 to +11.5 points on reasoning benchmarks for 3-bit LLaMA3-8B —
  turning a badly damaged model into a usable one, for ~0.25–0.5 effective bits of adapter.
- At **W2**: CALDERA is the reference point. 2-bit backbone + rank-256 4-bit factors (2.2–2.4 bpp):
  Llama-2-70B ppl 3.98 vs 3.12 FP16 (+28%); Llama-2-7B 6.19 vs 5.12 (+21%); and crucially it
  **halves** the gap left by rank-0 QuIP# (8.23→6.19 on 7B; 13.8→8.22 on Llama-3-8B). Sub-2.5 bpp
  is usable-but-not-lossless; the low-rank term is what makes it usable.
- Rule of thumb from LoRC/QERA/CALDERA together: the residual's useful spectrum is steep — **rank
  8–64 captures most of the recoverable error at W4; W2–W3 rewards rank 128–256**. Adapter overhead
  in 16-bit for a square d-dim layer is `32r/d` bits/param (r=64, d=4096 → 0.5 bpp; 4-bit factors à la
  CALDERA quarter that).

### 2.2 Best published match to llama.cpp's `y = Q·x + B·(A·x)` LoRA path

**EoRA is the exact shape**: a GPTQ (aggressively quantized) base plus a calibration-derived,
training-free LoRA-form adapter `B·(A·x)` — literally servable today by llama.cpp's existing
apply-LoRA-on-quantized-base path with zero inference-code changes (export the compensation as a GGUF
LoRA, pick rank 32–128). **QERA supplies the mathematically optimal way to compute those factors**
(closed-form whitened SVD of `W − Q`, Apache-2.0 reference code), so the clean-room recipe is:
quantize base to Q2_K/Q3_K/IQ-series → compute QERA/EoRA-style whitened residual SVD per tensor with
a small calibration set → ship as adapter. LQER's parallel-GEMM argument and SVDQuant's fusion data
(+50% latency unfused → 5–10% fused) bound what the llama.cpp CUDA work must achieve; CALDERA's
Table 6 (87.7→46.3 tok/s unfused) is the cautionary tale. LoRC is the floor (works with no
calibration at rank 8, tiny but real gains); CALDERA is the ceiling (joint optimization + quantized
factors, sub-2.5 bpp) if we later want to go past what a bolt-on adapter can do.

Licensing note for anything we ship: build from the **QERA (Apache-2.0)** / paper-math direction, not
from NVlabs/EoRA code (NC) or the unlicensed LQER/CALDERA repos.

---

## 3. References

| # | Paper | Venue/Year | arXiv | Why it matters to us |
|---|---|---|---|---|
| 1 | FWSVD: Language model compression with weighted low-rank factorization (Hsu et al.) | ICLR 2022 | [2207.00112](https://arxiv.org/abs/2207.00112) | First importance-weighted SVD; baseline that fails at LLM scale. |
| 2 | ZeroQuant-V2: PTQ study + Low Rank Compensation (Yao et al.) | arXiv 2023 | [2303.08302](https://arxiv.org/abs/2303.08302) | Origin of quant+low-rank-residual (LoRC); rank-8, ~0.1–1.6% overhead, calibration-free floor. |
| 3 | ASVD: Activation-aware SVD for Compressing LLMs (Yuan et al.) | arXiv 2023 (v5 2025) | [2312.05821](https://arxiv.org/abs/2312.05821) | Defines the near-lossless ceiling (~10%) for PTQ low-rank on W; shows SVD-then-4bit composes badly. MIT code. |
| 4 | LQER: Low-Rank Quantization Error Reconstruction (Zhang et al.) | ICML 2024 | [2402.02446](https://arxiv.org/abs/2402.02446) | Activation-scaled residual SVD; near-lossless W4A8; parallel-GEMM serving argument. |
| 5 | SVD-LLM: Truncation-aware SVD (Wang et al.) | ICLR 2025 | [2403.07378](https://arxiv.org/abs/2403.07378) | Best-known whitened pure-SVD numbers (7.73 @20%); Apache-2.0; +QuIP# hybrid beats OneBit. |
| 6 | CALDERA: Low Rank and Low Precision Decomposition (Saha et al.) | NeurIPS 2024 | [2405.18886](https://arxiv.org/abs/2405.18886) | The sub-2.5 bpp Q+LR reference: full ppl tables and the unfused-throughput warning. |
| 7 | Palu: Compressing KV-Cache with Low-Rank Projection (Chang et al.) | ICLR 2025 | [2407.21118](https://arxiv.org/abs/2407.21118) | 50% KV compression, MIT code — the KV-side complement to weight work. |
| 8 | ESPACE: Dimensionality Reduction of Activations (Sakr & Khailany, NVIDIA) | NeurIPS 2024 | [2410.05437](https://arxiv.org/abs/2410.05437) | Proof that 50% @ ~+0.2 ppl exists — but only with retraining. |
| 9 | QERA: Analytical Framework for Quantization Error Reconstruction (Zhang et al.) | ICLR 2025 | [2410.06040](https://arxiv.org/abs/2410.06040) | Closed-form optimum for the residual adapter; Apache-2.0 — our reference math + code. |
| 10 | EoRA: Fine-tuning-free Compensation via Eigenspace Low-Rank Approximation (Liu et al., NVIDIA) | arXiv 2024 (GPTQModel-integrated 2025) | [2410.21271](https://arxiv.org/abs/2410.21271) | Exact `Q·x + B·(A·x)` over GPTQ; +10.8/+11.5 pt recoveries at 3-bit; NC-licensed code. |
| 11 | SVDQuant: Absorbing Outliers by Low-Rank Components (Li et al., MIT Han Lab) | ICLR 2025 spotlight | [2411.05007](https://arxiv.org/abs/2411.05007) | Inverted residual (LR first, quantize rest); fusion data: +50% latency unfused → 5–10% fused. |
| 12 | Dobi-SVD: Differentiable SVD for LLM Compression (Wang et al.) | ICLR 2025 | [2502.02723](https://arxiv.org/abs/2502.02723) | Current pure-SVD frontier via learned truncation positions; MIT code. |
| 13 | SVD-LLM V2: Optimizing Singular Value Truncation (Wang et al.) | NAACL 2025 | [2503.12340](https://arxiv.org/abs/2503.12340) | Per-matrix ratio allocation; shares the Apache-2.0 SVD-LLM repo. |
| 14 | LLM Compression with Global Rank and Sparsity Optimization | ICLR 2026 | [2505.03801](https://arxiv.org/abs/2505.03801) | Global (cross-layer) budget allocation via RPCA — rank allocation matters. |
| 15 | ODLRI: Distinct Roles for Quantized and Low-Rank Matrices | Findings of ACL 2025 | [2506.02077](https://arxiv.org/abs/2506.02077) | LR takes outliers *before* quantizing Q — SVDQuant's trick for LLM weights. |
| 16 | FlashSVD: Memory-Efficient Streaming Inference for Low-Rank Models | AAAI 2026 | [2508.01506](https://arxiv.org/abs/2508.01506) | Fused kernels for `(x·A)·B`; −71% peak activation memory; v1.5 ([2605.08314](https://arxiv.org/abs/2605.08314)) 2.55× decode. |
| 17 | BALF: Budgeted Activation-Aware Low-Rank Factorization | arXiv 2025 | [2509.25136](https://arxiv.org/abs/2509.25136) | Closed-form rank allocation under explicit FLOP/param budgets. |
| 18 | SAES-SVD: Self-Adaptive Suppression of Accumulated and Local Errors | arXiv 2026 | [2602.03051](https://arxiv.org/abs/2602.03051) | Attacks cross-layer error accumulation — the failure mode of any per-tensor pipeline. |
| 19 | SERQ: Saliency-Aware Low-Rank Error Reconstruction | arXiv 2026 | [2603.08185](https://arxiv.org/abs/2603.08185) | 2026 SOTA claim for residual reconstruction at W4A4/W4A8 with a *single* compensation matrix. |

*Code/license snapshot (verified 2026-08-03 via GitHub API/raw):* SVD-LLM Apache-2.0 · ASVD4LLM MIT ·
Palu MIT · Dobi-SVD MIT · QERA Apache-2.0 · deepcompressor (SVDQuant) Apache-2.0 · EoRA
NVIDIA-Source-Code-License-NC · CALDERA and LQER repos have **no license file** · ESPACE/SERQ no code
found · ZeroQuant-V2/LoRC in DeepSpeed.
