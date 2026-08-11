> ⚠ see review/ corrections 2026-08-04

# Function-Preserving Factorization, Light Healing, and the Functional-Rank Escape Routes

**Purpose.** E01 measured the founding obstacle: W is energy-full-rank (r95 ≈ 50–80% of full
rank on every projection kind; C@95 ≈ 1.0 — truncation saves nothing). Yet the
intrinsic-dimension literature keeps insisting FUNCTIONAL subspaces are tiny. This survey
sweeps arXiv 2023–2026 for the escape routes to Max's pure-factorization dream
(W → A·B at ≥2× with preserved quality, calibration-week budget, no full retraining):
(a) factor-fitting objectives beyond activation-weighted SVD, (b) healing at tiny budgets
with exact costs, (c) pruning+factorization hybrids at 2×/7B+, (d) the sharpest
functional-vs-spectral-rank measurements, (e) serving formats for natively-factorized layers.

**Survey date:** 2026-08-04. Companions: `survey-lowrank-compression.md` (dense-LR lineage
FWSVD→ASVD→SVD-LLM→Dobi-SVD→QERA/EoRA — read that first for the baseline ladder),
`arxiv-structures.md` (§0 scaling law: capture ≈ 5.9·r/d; §0.5 rank must scale with d).

---

## A. Function-preserving factorization beyond activation-weighted SVD

The objective ladder, weakest → strongest, each rung a published constant-factor win
(never a scaling escape): weight Frobenius (vanilla SVD, ppl 20,061 @ 20% on LLaMA-7B)
→ Fisher-diagonal-weighted (FWSVD — collapses, ppl 1,727) → activation-whitened
(ASVD/SVD-LLM, 7.73 @ 20%) → differentiable truncation (Dobi-SVD) → **output/loss-aware
with closed forms (the 2025–26 wave, below)**.

- **LLM Surgeon** (arXiv:2312.17244, ICLR 2024). The Fisher/natural-gradient existence
  proof: Kronecker-factored (K-FAC) curvature of the full loss, multi-shot structured
  removal with **closed-form updates of all remaining weights** compensating each removal.
  20–30% structured (row/col) compression of OPT/Llama-2-7B at "negligible" loss, no
  gradient fine-tuning. Costs hours-to-a-day of curvature estimation. This is the strongest
  training-free objective in print — and it still stalls in the 1.25–1.4× band.
- **SVD-Surgeon** (arXiv:2606.23568, 2026). OBS transplanted to the singular-value basis:
  truncate, then closed-form second-order rescale of the *retained* singular values against
  model loss. Training-free, improves SVD-LLM's ppl-compression trade-off. Directly
  portable to our pipeline (it is a per-tensor post-fit of the factors we already build).
- **LACE-SVD** (arXiv:2607.03057, 2026). Loss-aware rank allocation (candidate ratios
  scored by calibration NLL) + closed-form local factor updates + propagation-aware
  correction down the residual stream. **The sharpest lane-(a) SOTA number: LLaMA-7B at
  0.6 ratio (≈1.67×) → wikitext-2 ppl 32.57** (Dobi-SVD 46.18, dense 5.68). Read that
  again: 2026 state-of-the-art closed-form loss-aware factorization at 1.67× is a **5.7×
  perplexity multiple**. The objective ladder buys constants; E01's wall stands at scale.
- **BALF** (arXiv:2509.25136). Fine-tuning-free budgeted factorization; generalizes
  whitening to rank-deficient activations, closed-form per-layer distortion proxy +
  Lagrangian rank allocation. Mostly vision-scale evidence; the allocation math is reusable.
- **SAES-SVD** (arXiv:2602.03051) / **Pareto-guided low-rank** (arXiv:2510.05544) /
  **CoCurve** (arXiv:2607.17568): 2026 refinements of the same two ideas (suppress
  cumulative cross-layer error; curvature-guided allocation). Constants again.
- **Fisher-aligned subspace diagnostics** (arXiv:2601.07197): Fisher information
  concentrates in subspaces that plain activation second-moments miss — a possible upgrade
  to our whitening metric (the E-series lesson "metric quality gates everything" says this
  is worth one experiment).

**Lane verdict.** Every objective upgrade (weight → activation → output → loss/Fisher)
is a real but bounded win; none changes capture-vs-rank scaling. Published winning ranks
remain ∝ d (structures §0.5). Pure factorization of a *general-purpose* W at 2× is as dead
in the 2026 literature as in E01 — the best 7B number at merely 1.67× is ppl 32.6.

## B. Healing lightly — exact budgets vs recovery, the full ladder

Ordered by budget, zero → billions. The pattern to hold onto: **tiny budgets recover the
easy bulk (perplexity, zero-shot commonsense); nobody's tiny budget recovers the hard tail
(math/coding/reasoning).**

| method | heal budget | damage → recovery | scale |
|---|---|---|---|
| FLAP (arXiv:2312.11983, AAAI'24) | **zero training** — bias terms recomputed from calibration baseline activations; 3–5 min, 1 GPU | beats LLM-Pruner-with-retraining at 20–50% structured pruning | 7B–13B |
| Ghosted Layers (arXiv:2605.15491) | **closed-form** linear operator from small calibration set, aligns activation discrepancy of removed layers | consistently beats prior training-free baselines for depth pruning | multiple LLMs |
| ReplaceMe (arXiv:2505.02819) | **closed-form** linear transform replaces pruned blocks, training-free | ~25% depth pruning, near-baseline on easy suites | 7B+ |
| SVD-Surgeon (arXiv:2606.23568) | **closed-form** second-order singular-value rescale | improves SVD-LLM trade-off | OPT, Llama-2-7B |
| SliceGPT RFT (arXiv:2401.15024) | **~5k Alpaca sequences**, LoRA r=32, seqlen 1024 (+3.5 h on 1 GPU for the 70B PCA itself) | 25%-sliced Llama-2-70B → **99% of dense** zero-shot avg (Phi-2 → 90%); 30%-sliced 70B: 74.3% vs 76.6% dense | 70B |
| LLM-Pruner (arXiv:2305.11627) | ~50k Alpaca samples, ~3 h, 1 GPU, LoRA | 20% pruned → ~95% retention (easy suites) | 7B |
| CompactifAI (arXiv:2401.14109) | **1 node × 8 × A10G, distributed, <1 epoch** of generic chat (Ultrachat/Alpaca/OpenHermes) | see below | Llama-2-7B |
| TransMLA (arXiv:2502.07864, NeurIPS'25) | **6B tokens** fine-tune after GQA→MLA SVD conversion | comparable benchmarks, 93% KV compressed | 7B–70B class |
| Sheared-LLaMA (arXiv:2310.06694) | 0.4B tokens prune + **50B tokens** continued pretraining | 2.7B from 7B, beats same-size scratch models | 7B→2.7B |
| Minitron (arXiv:2407.14679, NVIDIA) | **~94B tokens** distillation | 15B→8B/4B at parity-ish | 15B |

**CompactifAI's healing, exactly** (from the paper, our extraction 2026-08-04): healed on
one AWS EC2 8×A10G instance, distributed, "less than a single epoch" of generic chat
datasets. 88%-memory-compressed model (70% params cut, 7B→2.1B effective):
MMLU 46.41→45.32, HellaSwag 80.55→77.87, BoolQ 79.76→77.90, TriviaQA 19.03→18.33,
**GSM8K 23.05→22.58; at 93% (+4-bit) GSM8K → 17.74 (−23%)**. Two deflations: (i) Llama-2-7B
is a weak model — these are low-bar metrics near chance-adjacent ranges; (ii) the
2606.03465 head-to-head showed tensor networks *without* healing lose to plain SVD, so the
healing is load-bearing, and what it restores is exactly the easy bulk. The hardest task
in their own table is the one that stays broken.

**The tail is the casualty — this generalizes.** Junk-DNA hypothesis (arXiv:2310.02277):
pruning small weights damages "difficult" downstream tasks monotonically and
**irreversibly — fine-tuning does not recover it**. SliceGPT's own ablation: RFT on Alpaca
≫ RFT on WikiText-2 (recovery is *distribution-matching*, not repair). Calibration-mix
work (arXiv:2606.03328) finds multi-source calibration matters precisely because
single-source heals overfit the easy source. For OpenBeast — whose whole point is
max-intelligence coding/reasoning — the tiny-heal literature recovers the metrics we care
about least and forfeits the ones we care about most.

**Also standard and free:** quantization-style bias correction (DFQ, arXiv:1906.04721) —
calibration-only mean-shift fix; the BN-recompute analog for transformers is FLAP's
baseline-bias trick. Cheap, composes, worth folding into any E-series factor fit.

## C. Structured pruning + factorization hybrids at ≥2×, 7B+, <1 GPU-day heal

- **MoDeGPT** (arXiv:2408.09632, ICLR 2025). The lane's best citizen: module-level
  (matrix-*pair*) output reconstruction via Nyström/CR/SVD, provable module error bounds,
  no backprop, few hours on one GPU. **25–30% compression retains 90–95% zero-shot** on
  Llama-2/3; +46% throughput. Note the ceiling: 30%, not 50%.
- **LoRAP** (arXiv:2404.09695). Structure-by-module-type: attention → activation-weighted
  low-rank, FFN → channel pruning (FFN resists factorization — matches our E01 kind table).
- **SSLC** (arXiv:2510.26446) / **HASSLE-free** (arXiv:2502.00899): joint sparse+low-rank
  with second-order reconstruction — matrix-level; see structures §A (already ranked #3).
- **CFSP** (COLING 2025), **SlimGPT** (arXiv:2412.18110), **SLEB** (arXiv:2402.09025),
  joint prune+quant (arXiv:2606.07819): the 20–35% band, calibration-only or light LoRA.
- The ≥2× survivors all pay in tokens: Sheared-LLaMA (50B), Minitron (94B),
  NVIDIA Puzzle (arXiv:2411.19146, ~45B distill tokens for Nemotron-51B from 70B).

**Lane verdict.** The literature has a hard empirical frontier: **calibration-week budgets
top out at ~1.3–1.4× structural compression at 90–99% of the easy metrics.** Every
published ≥2× at quality burned 10⁹–10¹¹ tokens. Nobody crosses 2× on a strong 7B+ with
<1 GPU-day of recovery. Our quant campaign already beats this frontier on bytes: Q3-band
GGUF is ~5× smaller than F16 at KLD 0.06–0.08 — structural 1.35× at "95% zero-shot" is
not even on the same map.

## D. Functional vs spectral rank — the sharpest measurements

The intrinsic-dimension claims, with their actual scopes:

- **LASER** (arXiv:2312.13558, ICLR 2024). Truncating *selected later-layer MLP* matrices
  to ~1–10% rank **improves** factual-task accuracy, sometimes by 20–30 pp. Sharpest
  possible statement that the deep spectral tail of *some* matrices stores noise-like,
  task-harmful components. But: per-task, per-matrix, found by search; a handful of
  matrices, not the network; general perplexity survives only for those selections.
- **Intrinsic dimension of fine-tuning** (Aghajanyan, arXiv:2012.13255): d90 ≈ 200 for
  RoBERTa-large on MRPC — the *delta* needed to adapt is tiny (this is why LoRA works,
  r=1–16). Scope: the objective landscape of adaptation, never the base function.
- **Function vectors** (Todd, arXiv:2310.15213) / **task vectors** (Hendel,
  arXiv:2310.15916): a single mid-layer residual vector carries an entire ICL task.
- **Refusal** (Arditi, arXiv:2406.11717): one direction mediates refusal across 13 open
  models to 72B. 2025–26 correction: it's a **multi-dimensional cone**, and single-direction
  ablation fails at ≥8B (Wollschläger arXiv:2502.17420; arXiv:2603.27518; arXiv:2607.02396)
  — even "one behavior = one direction" inflates with scale.
- **ICL subspace geometry** (arXiv:2505.14808): generalization is governed by angles
  between task subspaces — task representations are unions of low-dim subspaces, and
  breadth of the union is what buys OOD behavior.

**The reconciliation (write this on the wall).** Functional rank is small **per behavior,
in activation space, at a fixed input distribution**. W is the superposition of thousands
of such behaviors *plus* the residual-stream read/write basis they all share — the union is
spectrally dense, which is exactly what E01 measured. There is no contradiction and no
free lunch: "functional subspaces are small" licenses *editing* (steering, LoRA, LASER
surgery), not *global factorization*. The one compression-relevant corollary:
**fix the task distribution and functional rank collapses.** Evidence already in hand:
LASER's per-task gains; SliceGPT's Alpaca-RFT ≫ WikiText-RFT; calibration-mix sensitivity
(2606.03328); MoE-SVD working across experts *because* experts share input distribution
(structures §D). Task-conditioned factorization of a 27B — a coding-beast, not a
general beast — is the only reading of the intrinsic-dimension literature that leaves
Max's dream alive, and no paper we found has tested it at ≥2× on a modern 7B+.

## E. Serving natively-factorized layers — status of the stacks

- **llama.cpp**: runtime LoRA adapters already compute (xA)B (our measured −33% decode
  tax; fusion sketch drafted in `upstream/`). **No native factorized-tensor GGUF format,
  and no open issue/PR proposing one** (searched ggml-org 2026-08-04 — the lane is empty;
  our upstream draft would be first). MLA (DeepSeek V2/V3 latent-KV attention — a
  *natively factorized* layer) is supported and optimized.
- **vLLM/SGLang**: MLA heavily optimized; LoRA via Punica SGMV kernels;
  `compressed-tensors` format covers quant + sparsity schemes but **has no low-rank
  scheme**. SVD-LLM/ASVD-style outputs serve today only as plain twin-Linear HF modules.
- **TransMLA** (arXiv:2502.07864, NeurIPS 2025 spotlight): SVD-factorizes GQA K/V into
  MLA form for *any* Llama/Qwen/Mistral-class model, 93% KV-cache compression, ~10×
  long-context speedup, rides existing DeepSeek kernels — the proof that mainstream stacks
  reward factorization **when the architecture declares it**. Recovery: 6B tokens
  (~10 H100-days at 27B scale — beyond calibration-week, but the only ≥2×-adjacent
  factorization with a first-class serving story).
- **FlashSVD** (arXiv:2508.01506): streaming tile kernels for SVD-format inference, −70%
  peak activation memory; BERT-scale, standalone stack.
- **Nunchaku** (SVDQuant, arXiv:2411.05007): the fused low-rank-branch kernel exists and
  erases the LoRA decode tax — structures §F already ranks the lesson.

**Lane verdict.** Serving is not the blocker and never was — (xA)B is two GEMMs
everywhere, and MLA proves factorized layers go mainstream when quality holds. The blocker
is, was, and remains quality-at-ratio.

---

## Verdict — is there a calibration-week path to pure factorization at ≥2× on a 27B?

**For a general-purpose 27B: NO — triangulated four independent ways.** (1) E01: W is
energy-full-rank; (2) lane (a): 2026's best closed-form loss-aware factorization posts ppl
32.6 at 1.67× on a 7B; (3) lane (b): tiny heals recover only easy metrics, and hard-tail
damage is measured irreversible (Junk DNA), while honest ≥2× recovery costs 10⁹–10¹¹
tokens; (4) lane (c): the calibration-budget frontier sits at ~1.3–1.4×. CompactifAI's
"70% params, 2–3% drop" is the dream's best advertisement and it deflates on contact:
weak model, easy metrics, 8-GPU healing epoch, GSM8K −23% at the headline ratio.

**Surviving escape routes, ranked:**
1. **Task-conditioned factorization** (lane d) — fix the distribution (code/agentic), let
   functional rank collapse; heal by self-distillation on self-generated domain text
   (<10k samples, SliceGPT-Alpaca effect). Untested at ≥2× in the literature. Ours to take.
2. **Closed-form heal stack as a free rider** — SVD-Surgeon rescale + FLAP bias
   compensation + Ghosted-Layers linear patch, all calibration-only; worth folding into
   the quant-residual campaign regardless of the dream.
3. **TransMLA-style attention factorization** for KV wins — real, mainstream-served, but
   6B-token recovery puts it outside the home rig.

**Minimal experiment (proposed E14 — "the conditional dream"):** at 0.6B, whitened-SVD
factorize to 1.25×/1.5×/2× with calibration + whitening computed on **code-only** text;
apply the closed-form heal stack (no training); measure the KLD triangle on code vs
wikitext. Control: same ranks, general calibration. **Live** if code-conditioned 2×
approaches the Q3 rung's KLD *on code* while the general control dies on both.
**Kill-line:** if conditional 2× at 0.6B cannot beat Q2_K-imat+fc at equal bytes even on
its own distribution, the pure-factorization dream is dead conditionally too, and the
quant-residual road is the road. Cost: one day at 0.6B on existing machinery (E05
whitening + gguf twin-tensor export); 27B confirmation ~1–2 days only if 0.6B fires.
