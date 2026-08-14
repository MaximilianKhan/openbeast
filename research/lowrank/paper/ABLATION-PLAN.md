# ABLATION PLAN — the definitive missing-runs matrix (beast-rank paper)
# Ablation audit 2026-08-04. Sources: paper/draft/*.md, MASTER-TABLE.md,
# PROTOCOL.md, RESULTS_ROLLUP.md (corrections header), experiments/*/README.md
# + REPORT*.md, review/adversarial-stats.md, paper/DEPLOYABLE-WINS.md (R7 arc).
# STATUS UPDATE 2026-08-04 (coherence-audit P2.10): T1.4 (E27 core) has
# RUN — ladder + control + paired stats + fc adapter complete; only
# T1.5's 100-chunk quartet + serving columns remain from it. The header
# claim "nothing has been run" is false for T1.4 and is amended here.
# Rows T1.11-T1.16 added 2026-08-04: the discriminating runs demanded by
# review/adversarial-round2-{paper,experiments}.md.
# Nothing else in this file has been run; it is the plan. All wall-time estimates
# are for our rig (RTX 5090 32 GB + 32-thread CPU) and are anchored on measured
# campaign times: 27B adapter extraction 269 s; 0.8B full pipeline 35 min;
# 35B MoE Gram capture 9 min; 27B Q2_K re-round sweep ~70 min; R7 held-out
# arc ~90 min; 27B 20-chunk KLD eval ~10 min; adversarial-stats pricing
# ("~1 h total at 27B" for contested pairs, "one evening" for 100-chunk).

## 0. Audit headline

Across every component/lever of the final methods, 51 lever-at-scale cells
were audited. Verdict: **17 CLEAN, 15 CONFOUNDED, 19 MISSING** (details §1).
The paper's *nulls* are its cleanest ablations (water-filling, shared-basis,
equalization, Kronecker-anti-help, MoE-vs-control — all isolated, most
paired, 2.7–60σ). The paper's *positive headline arcs* are where the
confounds live: nearly every multi-point curve or 27B lever comparison
varies a second factor alongside the named one.

The three worst confounds found (full register in §2):

1. **The recovery curve switched whitening metric mid-curve** (7.5/18/41%
   printed with points 1–2 diagonal, point 3 full-covariance — retired to
   20/28/41 by the review), and even the fc-consistent replacement still
   varies adapter budget (866/866/799 MB) and rank mix (uniform vs
   r192/r64 vs r512/r256 across the three bases). No point on any published
   recovery curve holds the correction recipe fixed while varying only the
   base.
2. **The 27B "measured-allocation wins" row varies THREE factors at once**:
   uniform-r64-**F16** vs allocated-**Q8** (allocation and factor dtype
   changed together), with sensitivity weights **transferred from 0.6B**
   (never measured at 27B), on a 0.5σ margin — and its serving comparison
   (81.0 vs 66.5 tok/s) is cross-session. The allocation lever has never
   been isolated at production scale.
3. **Provenance mixing in the 27B tables**: every corrected composite is a
   requant-of-Q6 while its Block-4 rival Q4_K_M is from-BF16; the
   BF16-rescore covers only 4 configs and the shifts are non-constant
   (+0.0015…+0.0038 — a 2.5× spread of the same order as the 1.0σ flagship
   margin); Q3_K_S, the rung the flagship claims parity with, was never
   truth-rescored. (E27 RAN this repair — status updated 2026-08-04 per
   coherence-audit P2.10; note the round-2 caveat that the rebuild also
   changed the imatrix, so the "provenance tax" is a provenance+
   calibration rebuild tax pending the F2 isolating run.)

Runner-up (named in review F6/§4.2 and kept explicit here): the
capture-collapse "two-point observation" confounds width with architecture,
calibration budget, AND reference — the two diagonal r64 points differ in
all four.

---

## 1. Component census — every lever, its best isolated measurement, verdict

Legend: CLEAN = one factor varied, rest fixed, resolved statistics (paired,
or ≥5σ unpaired), scale stated. CONFOUNDED = a second factor varied
alongside, or margin unresolved (<2σ unpaired with no paired test), or
comparison-set defect (missing control / post-hoc selection). MISSING = no
isolated measurement exists at any scale.

### 1.1 Calibration

| lever | verdict | evidence / what varied together |
|---|---|---|
| Corpus conditioning of the **corrector** (wiki vs code Grams, same base, same bytes) | **CLEAN** @0.6B | E14-cond 2×2 (`17-task-conditioned/README.md`): +10.3% on-dist ~8σ, −27% off-dist. Single runs, no paired stats, one scale. |
| Corpus mix for the **re-rounder** | **CONFOUNDED** (partial) @0.8B | R7 arc (`paper/DEPLOYABLE-WINS.md`, `22-qwen35-08b/heldout-results.txt`): wiki-only RR *anti-generalizes* on code (3.470 vs bare 3.083); one 2:1 interleaved mix rescues both corpora. One ratio, PPL-only, no paired stats, one scale — a rescue demo, not an ablation. |
| Calibration **mix-ratio × eval-corpus grid** | **MISSING** — now MANDATORY | The held-out finding (RR anti-generalizes; fc loses to Q3_K_M on held-out code 2.649 vs 2.473 despite tying it on-dist) makes this the paper's gating ablation. → T1.1 |
| Calibration **chunk count / token budget** (Grams) | **MISSING** | Budgets varied wildly across experiments (16/32/40/48/64/100/300 chunks) but never isolated. Worse: the one place it moved a result — 27B fc round 1 → v2 (0.1387 → 0.1282) — changed chunk count AND hybrid-layer coverage together (`10-full-covariance/README.md`). → T2.2 |
| Calibration **subsample variance** | **MISSING** | Review F12a: adapter-quality variance across calibration subsamples never bounded; every downstream number inherits it. → T2.3 |
| Gradient-Gram **token budget** (T estimator) | **MISSING** | E24 shows measured T is anti-helpful and lists the separating runs (T-shrinkage sweep, larger capture, 0.6B no-cut rerun) — none run. → T2.4 |

### 1.2 Gram capture variant

| lever | verdict | evidence |
|---|---|---|
| diag vs full covariance, equal bytes | **CLEAN** @0.6B | E10 r64: 27.58 vs 31.29, ~10σ; beats double-rank diag at 2.5–2.6σ. |
| diag vs full covariance @27B | **CONFOUNDED** | 0.1460 → 0.1282 is 2.1σ unpaired, but the diag adapter used the 100-chunk imatrix while fc used 64-chunk Grams (budget varied), and "full" is block-diagonal-8 for ffn_down (§2.2 caveat). E27's BF16 rerun (DONE 2026-08-04) fixed provenance but not the budget confound. → T2.5 |
| full vs **blocked** (8-block) whitening on a real model | **MISSING** | Only a synthetic repro exists (adversarial-code §B1: 25% true-metric excess). Force 8-blocking on a ≤8192-dim input (0.8B ffn: 4096) vs full, same everything. → T2.6 |
| one-sided vs **two-sided** fc | **CONFOUNDED** @0.6B/0.8B | 0.6B r64: 27.27 vs 27.58; 0.8B: 24.16 vs 24.61 / KLD 0.2066 vs 0.2227 (MASTER-TABLE, no paired flag) — isolated in design but sub-2σ unpaired with no paired test, and the 0.8B T carries the delta-net gradient cut on exactly the 18 layers it biases (E20). Estimator quality is entangled with the metric claim (§4.6's own lesson). → T2.4 |
| Whitening **damping** (S: 0.01·mean-diag; T: 1e-3) | **MISSING** | Both fixed a priori by convention (GPTQ/CLoQ), never swept anywhere. A PD-failure escalation path exists in E13 but its quality effect is unmeasured. → T2.7 |

### 1.3 Correction factors

| lever | verdict | evidence |
|---|---|---|
| Rank (uniform sweep 32–256) | **CLEAN** (trend) @0.6B | E21: monotone concave, resolved trend; "no landing spots" overreaches resolution (F14 — single runs, adjacent deltas 1–2σ). BF16-pure paired repeat (R5) missing. → T2.8 |
| Factor dtype F16 vs Q8_0 | **CLEAN** @0.6B | E08: r64 parity (31.26 vs 31.29), r128-Q8 ≡ r128-F16 at 53% bytes. Honest parity framing. At 27B the dtype never appears isolated (it rides the allocation row) — **CONFOUNDED** @27B. → folds into T2.9 |
| Uniform vs energy water-filling | **CLEAN** @0.6B (negative) | E07: 34.17 vs 31.29 at equal bytes — large margin, mechanism identified. |
| Uniform vs measured-sensitivity allocation | **CONFOUNDED** @27B | Worst-confound #2 above: dtype + transferred priors + 0.5σ + cross-session speed. → T2.9 |
| Per-kind sensitivity weights | **CLEAN** top / **CONFOUNDED** middle @0.6B | E07 kind probe: attn_k safely separated (+additivity check 0.352≈0.346); middle ranking spans 0.019 at σd≈0.011 (F13 — noise-ordered). 27B kind probe **MISSING** (transferred priors). → T2.10 |
| Per-kind / structural variants (column patches, shared bases) | **CLEAN** (nulls) | E08: columns lose@0.6B/tie@27B (correctly framed); cross-layer shared basis decisively null. MoE shared basis null at capture level + 4.2σ paired (E23). |

### 1.4 Base recipe

| lever | verdict | evidence |
|---|---|---|
| Base quant type → recovery fraction (the §4.4 curve) | **CONFOUNDED** | Worst-confound #1. Method-consistent 20/28/41 exists on paper but budget/rank-mix vary per point; Q6-referenced. → T1.9 |
| Correction on/off per base (IQ1_S, IQ2_XS, NVFP4) | **CLEAN** @27B | 16.5σ / 4.2σ / 3.0σ on/off gains stand (F15). The NVFP4-vs-IQ3_XS *ordering* is withdrawn (inside reference contamination) — that cross-base comparison is **CONFOUNDED**. |
| Mixed-geometry split (MIXED vs uniform Q2_K vs MIXED2) | **CONFOUNDED** @27B | Selected post-hoc from ≥2 designs (F7); flagship margin 1.0σ; provenance mixed; Q3_K_S never truth-rescored; interpolation control absent at build time. E27 (DONE 2026-08-04) ran the repair — Q3_K_S truth-rescored, control built, flagship demoted to paired parity. → residuals ride T1.5 |
| Provenance (from-BF16 vs requant-of-Q6) | **CONFOUNDED** | Worst-confound #3. F10: non-constant shifts, 4-config rescore only. → T1.4 |
| MTP pin (blk.64/blk.24 → q5_k) | **MISSING** | The pin was chosen by argument ("draft head should stay sharp"), never ablated (q4_k/q5_k/q6_k/q8_0 × decode-speed-with-MTP × quality). Load-bearing for every sub-Q3 27B artifact. → T3.4 |

### 1.5 Re-rounding

| lever | verdict | evidence |
|---|---|---|
| RR on/off @0.8B | **CLEAN** — the flagship | E24: paired t = 30–45 (ΔNLL −0.1637±0.0036; ΔKLD 40/40 chunks), BF16 truth, identical bytes. |
| RR on/off @0.6B | **CLEAN** (legacy ref) | E13: ~20σ PPL / ~19σ KLD, Q8_0-referenced scoring (labeled legacy per protocol). |
| RR on/off @27B | **CONFOUNDED** | 0.78σ KLD / 1.4σ top-1, paired test never run, AND the varied factor is only partially applied — the Q2_K-only codec leaves llama-quantize's promoted tensors (ffn_down/attn_output→Q3_K, attn_qkv/attn_v→Q4_K) unswept, so "RR@27B" measures a partial treatment. → T1.3 (paired test of what exists) + T2.1 (codec coverage) |
| RR metric: input-only vs Kronecker S⊗T | **CLEAN** @0.8B (negative) | E24: kron loses to input-only at t = +15.5 paired; solver verified bit-exact vs brute force; pre-registered single config. |
| RR ordering vs corrector (RR+fc vs alt+fc) | **CLEAN** @0.8B (+0.6B) | E24 paired t = +17.6, third confirmation; 0.6B triad 26.80/25.94/25.27. |
| ProjQ-deflated re-round | **CLEAN**-ish @0.6B | 25.94, 2.7σ vs naive; single run, single scale. |
| RR codec coverage (Q3_K/Q4_K/NVFP4) | **MISSING** | Unwritten codecs; the stated repair for the muted 27B result and the `gguf-refine` tool claim. → T2.1, T3.1 |
| RR calibration mix (ratios × corpora) | **MISSING** — mandatory | See §1.1; the DEPLOYABLE-WINS caveat says "the tool ships with a held-out gate or not at all". → T1.1 |
| Lloyd grid-refit (E26 M4: scales-step on/off; scales-only arm) | **MISSING** (incomplete) | E26 README still has `[FILLED FROM MEASUREMENT]` placeholders; the scales-only ablation that would separate the decoder half was explicitly not pre-registered and not run. → T3.3 |
| Gauge equalization (E26 M5) | **CLEAN** @0.8B (negative) | Paired t = +28…+60, 0/40 chunks, mechanism measured (double-counting), bit-exact fold verified. Pre-registered, no sweep. |

### 1.6 Alternation

| lever | verdict | evidence |
|---|---|---|
| Rounds 0/1/2 | **CONFOUNDED** at every scale | 0.6B: arc measured (0.246→0.209 KLD) but unpaired; round-2 stop fired on a 0.2σ delta (F7). 0.8B: round 1 recorded with NO error bars, run conditionally after observing round 0 (F11). 27B: fc→fc+alt is 1.04σ, "−6%/round" is a 1σ claim (F8). "Twice measured" is once-resolved. Paired rounds ablation → T2.11; 27B paired → rides T1.5. |

### 1.7 Kernel fusion / serving

| lever | verdict | evidence |
|---|---|---|
| Fusion on/off @0.6B (per phase) | **CLEAN** | Same-session pairs, +44/+62/+79% (≥5× drift), digit-identical PPL, kill-switched. |
| Fusion on/off @27B | **CONFOUNDED** | +2.6% single pair ≈ 1–1.5σ vs realistic run noise; ">20σ" struck; direction corroborated (rank-0 ceiling 89.4), magnitude unresolved; boost drift up to 9%. → T2.12 |
| Allocator fix on/off @0.6B | **CLEAN** | +19.6% base / +7–12% adapter, alternated pairs, 12,996-case suite + string-identical generation. |
| Allocator fix @27B | **CONFOUNDED** (unresolved) | 6 sessions, on wins 3/loses 2/1 outlier; drift-dominated; structurally expected small. → T2.12 |
| Fusion under contention (+42%) | **CONFOUNDED** | Single uncontrolled run; direction plausible (Phase-3 finding 4 mechanism), magnitude unreproduced. → T3.6 |
| Gate thresholds (MIN_ROWS=4096, 448K NR, fold-K) | **MISSING** (generality) | Tuned on exactly two rigs; crossover unswept (Phase 3 blocker). → T3.5 |
| Locked-clock speed protocol for the whole serving table | **MISSING** | Review R8: nvidia-smi -lgc, interleaved N=10, median±IQR — never run. → T2.12 |

### 1.8 E17 reconstruction tables

| lever | verdict | evidence |
|---|---|---|
| Per-code bias existence + offline effect size | **CLEAN** (offline) @0.6B | Kill-test survived; whitened-error −1.17–1.32%/kind measured on tables. |
| End-to-end quality effect (LUT in the dequant path) + dither control arm | **MISSING** | Never served; the honest 0.1–1% KLD band is a projection, not a measurement. → T3.2 |

### 1.9 Comparison infrastructure (the audit items that gate §4.3's claims)

| item | verdict |
|---|---|
| Interpolation control @27B / @MoE | **CLEAN** (exists; MoE version demolished the correction at 6.9σ — the model ablation control done right) |
| Interpolation control @0.6B/0.8B | **MISSING** (review's single most consequential gap) → T1.6 |
| Held-out corpora for crown configs + rivals | **MISSING** (partial 0.8B seed exists in heldout-results.txt) → T1.7 |
| Task eval (any, anywhere) | **MISSING** — zero in the campaign; 27B endgame sits inside KLD's silent zone → T1.8 |
| 100-chunk 27B eval (σ÷√5) for the endgame table | **MISSING** → T1.5 |
| Capture-vs-width regression (E21 points + 0.8B + 27B cached spectra) | **MISSING** (compute-only) → T1.10 |
| VRAM columns for Q3_K_S / IQ3_XS / E15 composite | **MISSING** (E15's "frontier <10 GB" projects to 10.3–10.5 GB VRAM, unmeasured) → T2.13 |

**Census totals: 17 CLEAN, 15 CONFOUNDED, 19 MISSING.**

---

## 2. Confound register (what varied together, per confounded cell)

1. Recovery curve: whitening metric (retired points) + adapter budget +
   rank mix + Q6 reference, across base types.
2. 27B allocation: allocation policy + factor dtype (F16→Q8) + prior
   provenance (0.6B transfer) simultaneously; speed cross-session.
3. 27B tables: base provenance (requant-of-Q6 vs from-BF16) + reference
   contamination (non-constant, same order as margins) + post-hoc design
   selection (MIXED of ≥2, best-of-≥10 configs at ~1σ).
4. 27B diag→fc: metric + calibration budget (100-chunk imatrix vs
   64-chunk Grams) + blocked ffn_down.
5. Capture-vs-width: width + architecture + calibration + reference
   (two points, one parameter).
6. Two-sided@0.8B: metric + estimator quality (gradient cut biases the
   18 linear-attention layers) — inseparable as recorded.
7. RR@27B: treatment partially applied (codec gap) + sub-1σ + Q6 ref.
8. Alternation@0.8B: conditional continuation + no recorded error bars.
9. 0.6B/0.8B crown-vs-rung: comparison set lacks the interpolation
   control + calibration-fitted capacity asymmetry (F4) — the held-out
   code result (fc loses to Q3_K_M off-distribution) shows this confound
   has teeth.
10. 27B fused/allocator speed: GPU boost drift (up to 9%) vs 1–3% effects.
11. E22 "all three metrics": PPL was 1.6σ — metric selection inside one row.
12. IQ2_XS serving row: measured with the adapter attached (footnoted).
13. E07 middle kind-ranking: single runs at σd≈0.011 over 0.019 span.
14. NVFP4-vs-IQ3_XS ordering: inside Q6↔BF16 contamination + provenance.
15. Block-4 "Q4_K_M wins the byte class": single-quantized rival vs
    double-quantized composites.

---

## 3. The missing-runs matrix

Format per row: **ID | model scale | fixed config | varied factor | metric |
est. wall time (rig)**. GPU-h = wall hours occupying the 5090; CPU-h =
CPU-bound (sweeps/regressions can overlap GPU runs). Runs marked ◆ share
prerequisites (build once, reuse).

### TIER 0 — paper-math queue (recon 2026-08-11; pre-GPU, CPU/cached-data
### only — run BEFORE committing the GPU block, results may re-rank T1)
From prior-art/recon-2026-08-11.md levers L1–L3/L6/L10 (full specs in
the research TODO "PAPER-MATH LANE"): GlowQ shared-A principal angles
on cached 27B Grams (possible E27-parity flip → would change T1.5's
config set); SRR k-split criterion on cached spectra (predicts whether
a pre-quant carve-out belongs in T1.9's recipe); BaKron
K-quant-constrained recursion (upgrades the T2.1 codec metric and
supplies T1.12's repair candidate alongside OBD-LLM dampening);
ReQuant/GSQ objective math; the cached-spectra micro-checks (SVDQuant
order duel, ARCQuant duel, LoRaQ byte model, ARCHead, DuQuant++
rotation test). GATE inherited from recon: no new speed/GPU number
until the llama.cpp pin is verified against #26177 (--fit/NextN
miscount) — affects every serving column below.

### TIER 1 — required for the paper's main claims (free lever §3, walls §4)
Rule for inclusion: a T1 run either (a) supplies the isolated measurement a
headline sentence currently lacks, or (b) closes a confound the draft
already discloses as pending.

| ID | scale | fixed | varied | metric | est. |
|---|---|---|---|---|---|
| **T1.1 Calibration-sensitivity grid** (the mandated one) | 0.8B | Q2_K base from BF16, MTP pin, r128-Q8 fc recipe, E13 RR sweep, damping conventions | calibration mix ∈ {wiki-only, code-only, 2:1 wiki:code interleaved, 1:1, +C4-broad} × method ∈ {RR, fc} | PPL on {wiki-test, code-test, C4 slice} for all cells; KLD+top-1 vs BF16 logits for the wiki column; paired stats vs bare per corpus | 5 Gram captures ×10 min + 5 RR sweeps ×10 min + 5 fc extractions ×5 min + ~36 PPL evals ×4 min ≈ **6–7 GPU-h** |
| **T1.2 ◆ 100-chunk BF16 truth logits @27B** — **DONE 2026-08-11** (bf16ref27b-100.logits, 12 G, [20]/[40] cumulative verified vs older refs; ACTUAL 3m46s — page cache, not 3 h) | 27B | BF16 reference, wiki-test, n_ctx 512 | — (infrastructure) | logits file | ACTUAL: **~4 min** |
| **T1.3 RR paired test @27B** — **DONE 2026-08-11** (legacy-provenance pair, labeled): KLD t=−7.02 (−13.3%), top-1 t=+4.72 (+1.07 pt), NLL tie — free lever paired-FINAL at 27B; BF16-provenance RR pair still unbuilt (rides T2.1 codec work) | 27B | existing Q2_K-imat + rr artifact pair (E13), same eval tokens | re-round on/off | 100-chunk paired per-chunk ΔKLD/Δtop-1 (paired_stats.py) | ACTUAL: 2 evals ≈ 1 min |
| **T1.4 ◆ E27 completion** — **DONE-with-residuals 2026-08-04** (core ran: ladder + control + paired stats + fc adapter; residuals = T1.5's 100-chunk quartet + serving columns; coherence-audit P2.10) | 27B | all from BF16, one quantize step, gram27b-bf16, MTP pin | base ∈ {Q2_K, Q3_K_S, Q3_K_M, IQ3_XXS, IQ3_XS, Q4_K_M, MIXED, **interpolation control @12.5 GB**}; ± fc r128-Q8 on MIXED | 20-chunk PPL/KLD/top-1 vs BF16 + paired stats on every <2σ pair | ~8 quantizes ×15 min + extraction 5 min + 10 evals ×10 min ≈ **4 GPU-h residual** |
| **T1.5 100-chunk endgame quartet** — **DONE-with-rider 2026-08-11**: flagship BEATS Q3_K_S (KLD t=−2.85/NLL −5.75/top-1 +2.04 — n=40 tie resolved to a win); CONTROL still beats flagship (t=+2.62) — allocation > correction > rung; fc increment paired (t=−8.52). OPEN rider: alternation round (no 27B artifact exists — new registration when built); serving columns still gated on R8 + #26177 | 27B | E27 artifacts, T1.2 logits | config ∈ {MIXED+fc, MIXED-bare, Q3_K_S, IQ3_XS, interpolation control}; +alternation round on MIXED+fc (paired) | 100-chunk paired ΔKLD — settles parity-vs-victory AND 27B alternation in one pass | ACTUAL: 6 evals + stats ≈ 10 min |
| **T1.6 Interpolation controls @small scale** | 0.8B + 0.6B | ladder recipes, from BF16 (0.8B) / Q8_0 (0.6B legacy-labeled) | mixed-quant build at the crown byte points (523 MB; 382 MB) | PPL + KLD vs reference, paired vs crown | 2 builds + 4 evals ≈ **1 GPU-h** |
| **T1.7 Held-out corpora pass** | 0.6B + 0.8B + 27B | crown configs + rival rungs + interpolation controls as built | eval corpus ∈ {C4 slice, code-test} (calibration untouched: wiki) | PPL-only, paired where corpus shared | ~14 configs × 2 corpora, mostly small models ≈ **2 GPU-h** |
| **T1.8 First task eval** — **DONE 2026-08-11**: HellaSwag full val (10042, identical set): 82.25 / 82.39 / 82.85% — no separation at the registered 1.4 pp bar; post-hoc paired McNemar (per-task outcomes reconstructed from running-acc lines): flagship-vs-S z=−0.81 TIE at ΔKLD≈0.005, Q3_K_M visible z=+3.48/+2.82 at ΔKLD≈0.027 agreeing with KLD's ordering — the silent zone is a MEASURED resolution floor; §6.1's "zero task evals" sentence deleted | 27B | flagship trio {MIXED+fc, Q3_K_S, Q3_K_M} from E27 | config | llama-perplexity --hellaswag, same task set | ACTUAL: ~50 min total |
| **T1.9 Recovery curve, recipe-held-fixed** | 27B | fc r128-Q8 **uniform** adapter recipe, gram27b-bf16, from-BF16 bases, fixed adapter byte budget | base ∈ {Q2_K, IQ2_XS, IQ1_S} (rebuild IQ bases from BF16) | recovery fraction of KLD damage, with propagated CIs; replaces F7 | 2 quantizes ×20 min + 3 extractions ×5 min + 6 evals ×10 min ≈ **3 GPU-h** |
| **T1.10 Capture-vs-width regression** | all | cached E21 spectra + 27B cache27b + new 0.8B capture point | r/d across ≥3 scales, per whitener | fitted form + CI (upgrades/kills the §4.2 observation) | python only ≈ **4 CPU-h** |
| **T1.11 Third-corpus held-out test** (round-2 experiments F1c — the extrapolation question) | 0.8B | RR-mixed2 + bare + wiki-only RR + fc-r128q8 artifacts as built | eval corpus = a THIRD, never-calibrated corpus (C4 slice) | PPL per config; does mixed-calibrated RR still beat bare OFF-coverage? | 4 evals ×4 min ≈ **0.5 GPU-h** |
| **T1.12 0.6B clean-gram Kronecker rerun** (round-2 experiments F6 — estimator-class vs broken-capture) | 0.6B | e13b kron config, existing gram06b + gradgram06b (196 exact Grams, NO delta-net gradient cut, classic arch) | T ∈ {measured, I} | paired ΔNLL/ΔKLD kron-vs-input — if kron still loses, single-pass-K-FAC explanation dominates; if it wins, E24's capture was the fault | sweep + 2 evals ≈ **1 GPU-h** |
| **T1.13 M5 mechanism ablations** (round-2 experiments F7 — diagnostic, not post-hoc selection) | 0.8B | e26_equalize pow2 fold, byte-parity discipline | (a) equalization WITHOUT imatrix (double-count hypothesis predicts harm vanishes); (b) alpha ∈ {0.25, 0.5, 0.75, 1} (AWQ lineage predicts interior optimum) | paired ΔKLD vs bare per cell | (a) 1 quantize + 1 eval <1 h; (b) 4 cells ≈ **3 GPU-h total** |
| **T1.14 RR-mixed2 KLD/top-1** (round-2 experiments F1d — the deployable variant is half-measured) | 0.8B | q35-08b-Q2K-rr-mixed2.gguf + bf16ref08b-40.logits; scripted-corpus gram rebuild as verification (HELDOUT-METHOD.md) | — | KLD + top-1 both corpora' logits where available; paired vs bare | 2 evals ≈ **0.5 GPU-h** |
| **T1.15 Calibration-sampling-alone bound** (round-2 experiments F1a — formalize the accidental 27.54-vs-28.09 control) | 0.8B | wiki-only calibration, E13 RR config | chunk budget/sampling ∈ {40ch as gram08b, 48ch resample ×2 disjoint} | spread of RR PPL across resamples — names the F12a variance floor every sub-0.5-PPL margin inherits | 3 captures + 3 sweeps + 3 evals ≈ **1.5 GPU-h** |
| **T1.16 ≥40-chunk E27 control extension** (round-2 experiments F3 — the t=1.9 verdict is below the campaign's own 2σ bar on n=20) — **DONE 2026-08-04 evening** (pre-registered JOURNAL 18:08; +Q3_K_M anchor and the paired E13-27B RR pair). VERDICT: control-beats-flagship LICENSED — KLD t=+2.26, 28/40 chunks, sign p=0.017, outlier trims raise to t≈2.8; flagship-Q3_K_S parity holds (KLD t=−0.04, PPL edge t=−3.33); E13-27B re-round RESOLVED in RR's favor (KLD t=−3.70, top-1 t=+2.56, legacy-provenance pair). Raw: experiments/27-bf16-rederivation/results-40ch{,-paired}.txt; MASTER-TABLE 27B rows marked [40]. | 27B | E27 artifacts {MIXED+fc, CONTROL, Q3_K_S}; 40-chunk BF16 logits (subset of T1.2's 100) | — | 40-chunk paired ΔKLD + sign test printed beside t — licenses or demotes "matches-or-beats" | ~20 min/eval ×3 + logits ≈ **1.5 GPU-h** (subsumed by T1.5 if T1.2 runs first); ACTUAL: logits ~14 min + 6 evals ~15 s each ≈ 0.3 GPU-h |

| **T1.17 GSQ head-to-head** (recon 2026-08-11 T1 — the frozen-grid frame is published; the paper cannot ship without this comparison) | 8B (their artifact) | Unsloth Qwen3-8B Q2_K checkpoint (the exact artifact GSQ improves), our Q2_K codec + mixed calibration per HELDOUT-METHOD, their public code (github.com/IST-DASLab/GSQ) at defaults | method ∈ {bare, gguf-refine (one-shot whitened), GSQ (trained)} | KLD + top-1 vs BF16 logits on shared eval tokens, paired; plus wall-clock + peak-memory cost per method (the cost axis IS the claim) | their training run is the unknown (budget-cap it and report); ours ~minutes CPU + 3 evals ≈ **1 GPU-day ceiling, likely well under** |

**T1 totals: 17 runs ≈ 38–46 GPU-h + 4 CPU-h ≈ 3–4 rig-days.**
(Was "10 runs ≈ 30 GPU-h" — T1.11-T1.16 added 2026-08-04 from the
round-2 reviews' repair lists; T1.17 added 2026-08-11 from the recon;
T1.2/T1.4/T1.5/T1.16 chain; T1.1/T1.6/T1.7/T1.11-T1.15 can run on the
0.8B lane in parallel; T1.17 is independent of every chain.)
Recon riders on existing rows: **T1.10** gains the Muon-provenance
axis (L8: Muon flattens spectra → optimizer geometry as a candidate
setter of c; optional Moonlight-16B-A3B out-of-family point);
**T1.12** gains two published repair candidates to test alongside the
rerun (OBD-LLM's 10% diagonal dampening; BaKron's solver) —
"fundamentally different estimator" now has named instances.

### TIER 2 — strengthens (composition table, lever isolation, speed rigor)

| ID | scale | fixed | varied | metric | est. |
|---|---|---|---|---|---|
| T2.1 Q3_K + Q4_K re-round codecs, then full-coverage RR @27B | 27B | E27 Q2_K base, grams | codec coverage {Q2-only (=T1.3), +Q3_K, +Q4_K} | paired ΔKLD per coverage step — turns the 27B "codec gap" diagnosis into a measurement | codec eng ~1–2 CPU-days; sweep+2 evals ≈ **4 GPU-h + 2 eng-days** |
| T2.2 Gram chunk-count ablation | 0.8B | fc r128-Q8 recipe, wiki calibration | capture chunks ∈ {8, 16, 32, 64, 128} | KLD vs BF16, paired adjacent | 5 captures + 5 extractions + 5 evals ≈ **3 GPU-h** |
| T2.3 Calibration-resample bound (F12a) | 0.6B | crown recipe | 3 disjoint calibration subsamples | spread of PPL/KLD across resamples | **1 GPU-h** |
| T2.4 Two-sided estimator repair | 0.6B + 0.8B | kron RR + fc-2side recipes | (a) T-shrinkage λT∈{0, .25, .5, .75, 1→input-only}; (b) grad capture 32→128 windows; (c) 0.6B kron rerun (no gradient cut) | paired ΔNLL vs input-only — separates estimator noise from the K-FAC approximation | grad captures ~15 min CPU each; ~8 sweep+eval cells ≈ **5 GPU-h** |
| T2.5 diag→fc @27B, budget-matched | 27B | E27 pipeline, SAME 48-chunk capture for both | whitener only (diag vs fc), r64 | paired ΔKLD — the clean version of the 2.1σ cell | 2 extractions + 2 evals ≈ **1.5 GPU-h** |
| T2.6 Blocked-vs-full whitening, real model | 0.8B | fc recipe on ffn tensors (4096-dim inputs) | whitener ∈ {full, forced 8-block} | paired ΔKLD + true-metric excess — grounds the §2.2 caveat | **2 GPU-h** |
| T2.7 Damping sweep | 0.8B | fc + RR recipes | S damping ∈ {1e-3, 1e-2, 1e-1}·mean-diag | paired ΔKLD (flat = convention safe; else a new lever) | **2 GPU-h** |
| T2.8 Rank sweep, BF16-pure + paired (R5) | 0.8B | crown recipe | r ∈ {32…256, step 32} | full-corpus PPL + paired adjacent deltas — earns or retires "smooth" | 8 points × ~20 min ≈ **3 GPU-h** |
| T2.9 Allocation duel, dtype-fixed @27B | 27B | E27 base, Q8 factors BOTH arms, equal bytes | uniform-r vs allocated (with T2.10 weights) | paired ΔKLD | emits are seconds from cache; 2 evals ≈ **1 GPU-h** |
| T2.10 27B kind-sensitivity probe | 27B | E27 base, r64 per kind in isolation, Q8 | kind ∈ 7 projections | ΔKLD/MB per kind + additivity check — replaces transferred priors | 7 emits + 7 × 10 min evals ≈ **1.5 GPU-h** |
| T2.11 Alternation rounds, paired | 0.8B | crown recipe | rounds ∈ {0, 1, 2} | paired per-chunk ΔKLD per round, pre-registered stop | 2 extra rounds × ~30 min + evals ≈ **2 GPU-h** |
| T2.12 Locked-clock speed table (R8) | 0.6B + 27B | nvidia-smi -lgc, interleaved A/B, N=10, quiet host | {fused/unfused} × {alloc on/off} × {bare/adapter} per rig | median ± IQR tok/s — resolves the 27B +2.6% and allocator verdicts, prices the serving-tax table [F5/F6] | one evening ≈ **4 GPU-h** |
| T2.13 Missing rivals + VRAM columns | 27B | E27/E15 artifacts | build IQ1_M + IQ2_XXS (E15 rivals); measure VRAM for Q3_K_S, IQ3_XS, E15 composite, MIXED+fc | KLD + measured VRAM | 2 quantizes + 4 evals + serve probes ≈ **3 GPU-h** |

**T2 totals: 13 runs ≈ 33 GPU-h + ~2 eng-days (codec work) ≈ 3 rig-days + eng.**

### TIER 3 — nice-to-have

| ID | scale | varied | note | est. |
|---|---|---|---|---|
| T3.1 NVFP4 re-round codec (nearest-of-8 E2M1 on frozen FP8 scales) | 27B | codec | the §6.3 lane; best-correcting base | ~1 eng-day + 2 GPU-h |
| T3.2 E17 LUT end-to-end + dither control arm | 0.6B | LUT on/off, ± dither | rides the fused kernel; measures the projected 0.1–1% KLD | 3 GPU-h |
| T3.3 E26 Lloyd completion + scales-only arm | 0.8B | codes-step vs scales-step vs both | fills the README placeholders; separates the decoder half | 3 GPU-h |
| T3.4 MTP-pin ablation | 27B | blk.64 pin ∈ {q4_k, q5_k, q6_k, q8_0} | quality + MTP-draft acceptance rate; underpins every sub-Q3 build | 4 GPU-h |
| T3.5 Kernel gate crossover sweep | mid-size (~7–9B) | MIN_ROWS ∈ {2048…8192}, fold-K | generalizes gates beyond the two tuned rigs | 3 GPU-h |
| T3.6 Controlled-contention fusion pair | 0.6B + 27B | fused on/off × contention {quiet, synthetic load} | reproduces the +42% direction with a controlled load | 2 GPU-h |
| T3.7 MoE dense-tensor correction | 35B MoE | correct attn/shexp only (E23's named follow-up) | the one un-rescued MoE route | 4 GPU-h |
| T3.8 Sub-2-bit expert carrier | 35B MoE | exps at IQ1-class + PE correction | where the ladder has no promotion rung | 5 GPU-h |

**T3 totals: 8 runs ≈ 26 GPU-h + ~1–2 eng-days.**

---

## 4. Grand totals and sequencing

| tier | runs | GPU-h | CPU/eng | verdict gate |
|---|---|---|---|---|
| T1 | 16 | ~38 | ~4 CPU-h | paper submittable: free-lever generalization bounded (incl. third-corpus + sampling bound), walls provenance-clean, mechanism claims discriminated (T1.12/T1.13), first task eval, F7 replaced. (Updated 2026-08-04: +6 round-2 rows.) |
| T2 | 13 | ~33 | ~2 eng-days | every lever isolated at both scales; speed table locked-clock; composition table complete |
| T3 | 8 | ~26 | ~1–2 eng-days | open lanes promoted or closed |

Sequencing notes:
- T1.2 → {T1.3, T1.5} is the only hard chain; T1.4 (E27) is already in
  flight and gates T1.5/T1.8/T1.9.
- The 0.8B lane (T1.1, T1.6, T1.7 small-scale halves) is CPU-quantize +
  short GPU evals and can interleave with the 27B lane.
- T2.12 (locked clocks) must be a dedicated quiet-host evening — no
  concurrent agents (E25's own finding 5: host load poisons short runs 2×).
- Per PROTOCOL §8, every sweep above pre-registers its grid in the
  experiment README before running; post-hoc additions get the
  "exploratory" label. Configs-tried counts print beside every best-of
  table (review R10).

## 5. What T1 changes in the draft, sentence by sentence

- §3.1's 27B row gains a paired verdict (T1.3) and, after T2.1, a
  full-coverage number — until then the codec-gap caveat stands.
- §3/§6 free-lever claims inherit T1.1's grid as the generalization
  boundary ("calibrate broadly, improve broadly" becomes a measured
  surface, not a slogan).
- §4.3's parity claim re-lands on E27+T1.5 provenance-clean paired stats,
  with the interpolation control drawn at all three scales (T1.4/T1.6).
- §4.4's F7 figure is replaced by T1.9 (recipe-held-fixed, CIs propagated).
- §4.2's observation becomes a fitted regression with a CI or is demoted
  (T1.10).
- §6.1's "zero task evaluations" sentence is deleted (T1.8).
