# E36 — the submission campaign (pre-registered 2026-10-15)

Max, 2026-10-15: "take the GPU and finish what we need to complete a formal
submission of our research." This experiment runs the EXPERIMENT-class items
the 2026-10-09 referee review asked for (paper §7.2; `../../TODO.md`, top
block) and the six ablation cells the audit priced and never ran. Every
section below states its configuration and decision rule **before** its run
starts; a section added later carries its own registration timestamp.

## 0. Build, references, and why every 27B anchor is re-scored

All E36 rows use `llama.cpp/build` = b10865 (d4389a4dd), which includes
the Gated-DeltaNet norm fix #28068 (b10829). Every 27B row in the paper
(E27, 08-04..08-11) was scored on a pre-fix build against a pre-fix BF16
reference. The fix is documented to move Qwen3.8-27B Q4_K_M KLD by 0.00175,
the same order as the contested 27B margins (0.0048, 0.0063). Paired A-vs-B
on one build remains valid, so the paper's numbers stand as measured; E36
regenerates every reference and re-scores every comparator on the current
build, and never mixes a pre-fix row into an E36 table.

Statistics: per-chunk paired differences recovered with the canonical
parser (`../24-yaqa-lite/paired_stats.py`), plus the E35 robust set
(bootstrap CI, sign test, Wilcoxon, block bootstrap). Thresholds below are
on the paired t of per-chunk KLD unless stated. No family-wise correction is
applied in the decision rules; the Bonferroni-adjusted p is reported beside
each contested pair.

## A. 27B ladder, current build, three corpora (`a_ladder27.sh`)

Model: Qwen3.6-27B heretic-v2, BF16 reference (54.66 GB, `-ngl 20`).
Corpora, 100 chunks × 512 tokens each:
- **wiki**: wikitext-2 test, the same 100 chunks as the paper. This is a
  replication of the paper's 27B table on the fixed graph.
- **fw**: FineWeb-Edu (sample-10BT, first 400 docs; the E32 control text).
  Held out: never used for calibration, never used at 27B before.
- **code**: `17-task-conditioned/code-test.txt`, disjoint from code-train by
  construction. Held out.
Calibration is unchanged (wiki.train Grams and imatrix, as built in E27).
Configs: the existing E27 files — MIXEDfc (MIXED + r128 Q8 adapter),
MIXEDbare, Q3_K_S, CONTROL, IQ3_XS, IQ3_XXS, Q3_K_M, Q2_K; plus the legacy
re-rounded Q2_K as a cross-build continuity row only.

Claims under test (paper §4.3) and rules, per corpus:
- **C1** corrected (MIXEDfc) better than Q3_K_S. Holds if t ≤ −2;
  not supported if |t| < 2; reversed if t ≥ +2.
- **C2** the same-byte control better than the corrected configuration.
  Holds if t(MIXEDfc − CONTROL) ≥ +2; not supported if |t| < 2; reversed if
  t ≤ −2.
- **C3** the correction's increment over its own base (MIXEDfc − MIXEDbare)
  is negative with t ≤ −2. The paper predicts it holds everywhere (E32 found
  it held on FineWeb at Qwen3.8-27B).
- **C4** IQ3_XS better than both MIXEDfc and Q3_K_S at fewer bytes.
- wiki only — **replication**: C1 and C2 replicate on the fixed graph if
  each has the paper's sign and |t| ≥ 2. A sign flip is reported as a
  failed replication and the paper's §4.3 is rewritten around it.
The paper will report all three corpora whatever they show.

## B. 27B re-rounder, single-step provenance (`bc_queue.sh`; registered 2026-10-15 16:25, before launch)

The paper's 27B re-round result (−13.3% KLD, t = −7.0) is a legacy pair: a
Q2_K requantized from the served Q6_K, Grams captured on a pre-fix graph,
wiki-only calibration, no held-out test. Referees 1 and 6 ask for it under
single-step provenance and against the IQ rungs. Configuration:
- Base: `h27bf16-Q2_K.gguf` (E27; one quantize step from BF16, E27 imatrix,
  MTP pin). Re-rounding changes code bits only, so every arm has its bytes.
- Grams: two fresh captures on the post-fix research build (b10866 =
  b10865 + the Gram patch), BF16, `-ngl 20 -c 512 --chunks 48`, E27 pattern:
  `gram27b-e36-wiki` (wiki.train) and `gram27b-e36-mixed`
  (`data/mixed-calib2.txt`, the 2:1 wiki:code interleave from E22; it
  contains code-train only, so code-test stays held out). The two arms
  differ in calibration text only.
- Sweep: `13-rerounder/e13_reround.py`, unchanged, BF16 as reference
  (same tensor coverage as the legacy run: the Q2_K-coded tensors;
  ffn_down is Q3_K in this file and is not touched).
- New stock rung: IQ2_M from BF16 with the E27 imatrix and MTP pin, the
  smaller bracket the referee asks for; IQ3_XXS (larger, from E27) is the
  other.
- Scored on the three E36-A references (wiki, fw, code; 100 chunks each).

Rules (per corpus; paired KLD t):
- **B1** single-step replication: rr-wiki − Q2_K ≤ −2 on wiki.
- **B2** held-out effect of wiki-only calibration: report rr-wiki − Q2_K on
  fw and code. The 0.8B result predicts harm on code (t ≥ +2 there would
  replicate it at 27B); no prediction is made for fw.
- **B3** mixed calibration: rr-mixed − Q2_K ≤ −2 on all three corpora is
  "improves the file everywhere measured". The deployable recipe is
  rr-mixed, fixed now; the paper's 27B headline becomes the rr-mixed row
  whichever arm looks better afterwards.
- **B4** ladder: rr-mixed against IQ2_M (smaller) and IQ3_XXS (larger),
  reported with bytes. No win rule; the paper states where the re-rounded
  file sits on the byte–KLD curve of the stock types.

## C. KLD at a 2048-token context for the E32 files (`bc_queue.sh`; registered 2026-10-15 16:25)

Referee 4: the one cross-family reversal (GSQ-RCO behind its untrained
baseline on 512-token KLD, ahead on 2048-token PPL and on the coding suite)
confounds context length with metric. Configuration: Qwen3.8-27B BF16
reference logits on wikitext-2 test at `-c 2048`, 40 chunks (81,920
tokens; llama-perplexity scores the second half of each window, so every
scored token has ≥ 1,024 tokens of context). The current build, as E32 used.
Scored: UD-IQ2_S, GSQ-RCO IQ2_XS, UD-IQ3_S, GSQ-RCO IQ3_S, UD-Q2_K_XL, and
our three corrected arms (each UD base + its r128 Q8 adapter).

Rule, per bit class, on GSQ-RCO − UD:
- t ≤ −2: KLD@2048 agrees with PPL@2048 and the suite → the 512-token
  disagreement is a context-length effect; the paper says so.
- t ≥ +2: KLD still disagrees at 2048 → the disagreement is the metric
  (distribution match vs likelihood of the observed token); the paper says so.
- |t| < 2: unresolved at this n; reported as such.
Our arms' increments over their bases (t ≤ −2 expected, as at 512) are
reported alongside.

## D. Small-scale ablations: T1.6, T1.13, T1.15, T1.1 at 0.8B; T1.9 at 27B (registered 2026-10-15 16:45, before any D run)

Qwen3.5-0.8B is `qwen35` (Gated-DeltaNet), so its paper rows are pre-fix
too. D re-derives the 0.8B lane on the current build first, then runs the
ablations on that footing. Everything from `Qwen3.5-0.8B-BF16.gguf`, one
quantize step, MTP pin `blk.24=q5_k`, 40-chunk Gram+imatrix captures (the
E22 budget), r128 Q8_0 fc adapters with the E22 target set, e13 sweep for
RR. References: BF16 logits on wiki, fw and code, 40 chunks × 512 each,
current build. Calibration texts never overlap evaluation texts: wiki.train
vs wiki.test; code-train vs code-test; FineWeb-Edu calibration docs are a
different slice of sample-10BT from the first-400-docs evaluation text.

- **D0 re-derivation gate.** Rebuild Q2_K, RR-wiki, Q2_K+fc, +alternation
  ×1 (E22's crown), IQ3_XXS, Q3_K_M, Q4_K_M, and new IQ2_M (the referee's
  lower bracket). Gate: RR-wiki − Q2_K KLD on wiki t ≤ −2. The paper's
  0.8B numbers are restated from D0 if they move by more than their sem.
- **T1.6 control at 0.8B.** A pure-quantization control at the crown's
  bytes (Q2_K + r128 Q8 adapter). Recipe rule fixed now: start from
  Q3_K_M; promote tensor kinds to q4_k in the order attn_k, attn_v,
  attn_output, attn_qkv, attn_q, attn_gate, ssm_out, ffn_down, ffn_gate,
  ffn_up, whole kinds first and then layer subsets evenly spaced, until
  the file reaches the corrected configuration's bytes from below
  (within 0.5%). One build, scored once. Rule: report control − crown and
  control − fc with paired t on all three corpora; the 27B reading
  ("the control is at least as good") is replicated at 0.8B if
  t(fc − control) ≥ +2 on wiki.
- **T1.15 sampling bound.** Three disjoint 40-chunk samples of wiki.train
  (byte slices 0–⅓, ⅓–⅔, ⅔–1), each → Grams → RR. Report the spread (SD and
  range) of RR's KLD and PPL across the three, per eval corpus. This is the
  floor every calibration-dependent margin in the paper inherits.
- **T1.1 calibration grid.** Calibration mix ∈ {wiki, code, fineweb,
  wiki:code 2:1 interleave (= E22's mixed-calib2), wiki:code:fineweb 1:1:1
  interleave} × method ∈ {RR, fc} → 10 artifacts, each scored on wiki, fw
  and code (KLD, PPL, top-1). Paired against bare Q2_K per corpus. No
  winner is chosen from this grid for any other claim; the deployable
  recipe stays the one fixed in §B (2:1 interleave).
- **T1.13 equalization mechanism.** e26 diagonal equalization at α = 1
  WITHOUT an imatrix (with the matching imatrix-free bare Q2_K as its
  pair), and α ∈ {0.25, 0.5, 0.75, 1} WITH it. Predictions written in
  E26: if the harm comes from double-counting importance, it vanishes
  without the imatrix; AWQ lineage predicts an interior optimum in α.
  Paired ΔKLD vs the matching bare file, wiki.
- **T1.9 recovery curve at 27B, recipe held fixed.** Bases Q2_K, IQ2_XS,
  IQ1_S from BF16 (E27 imatrix, MTP pin); the SAME adapter recipe on each
  (r128 Q8_0, the E27 non-FFN target set, Grams `gram27b-e36-wiki`).
  Recovery fraction = (KLD_base − KLD_base+fc) / KLD_base, with a paired
  bootstrap CI, on wiki and fw. Replaces the confounded F7 curve.

## E. Baselines for the re-rounder (referee 13) (registered 2026-10-15 16:50)

**Free-grid GPTQ at identical format and bytes** (`freegrid/`): the e13
sweep, except the Q2_K grid of each 256-column superblock is refit, at the
moment the sweep enters it, from the error-updated weights by llama.cpp's
own Q2_K quantizer with the same imatrix. Same Grams, same damping, same
tensor coverage, same output bytes. This isolates "freeze the vendor's grid"
against "let GPTQ refit it" with everything else fixed. Gate before any
use: with feedback off, the tool reproduces llama-quantize's bytes.
Arms, each scored on wiki/fw/code against the bare file and the frozen-grid
arm on the same Grams: 0.8B (wiki Grams from D0, mixed Grams from T1.1),
27B (mixed Grams from §B), and the non-Qwen model of §G.
Rule: report free − frozen paired t per corpus. No direction is predicted.
If free-grid wins, the paper's framing changes from "frozen-grid
re-rounding" as a method to "GPTQ on GGUF formats" with frozen-grid as the
zero-search special case, and says so.

A learned-rounding baseline (AdaRound/OmniQuant class) needs a
block-reconstruction training loop that does not exist here; it is not run
and stays listed in §7.

## F. Capability replication (referee 4) (registered 2026-10-15 16:50)

The paper's only capability evidence is one run per arm (GSQ-RCO IQ3_S 88,
UD-IQ3_S 76 of 112, 2026-09-14, an older eval era) against a Q5 reference
band of 82–91 over four runs: the +12 is about the size of the band. The
runner samples at temperature 0.6, unseeded, so repeat runs are
independent draws.
- Arms: GSQ-RCO IQ3_S; UD-IQ3_S; OURS = UD-IQ3_S + `adapter-fc-r128q8-UD-IQ3_S.gguf`
  (served with `--lora`). Three runs each, `--no-cache`, `v5-fast` (112
  units), single slot (`-np 1 --jobs 1`), reasoning budget 20480, current
  eval era (whatever `evals/` hashes to at launch; recorded per row).
- Order: rounds of (GSQ, UD, OURS), so stopping at a round boundary leaves
  balanced arms.
- Validity per row as in E32 v2: any CUDA error in the serve log or >10%
  zero-token failures marks the row invalid and it is rerun once.
- Primary analysis: per-unit pass rate over the three runs, paired by unit
  (112 units): mean difference, Wilcoxon signed-rank, sign test, per arm
  pair (GSQ − UD, OURS − UD, OURS − GSQ). Secondary: run-level pass counts,
  mean ± SD per arm, Welch t (df ≈ 4, reported but not leaned on).
- Rule: an arm pair "differs" if Wilcoxon p < 0.05 AND the run-level ranges
  do not overlap; "consistent with no difference" if Wilcoxon p ≥ 0.05;
  otherwise "unresolved". The 2026-09-14 pair is reported beside, labelled
  with its era, never pooled.

## G. A non-Qwen model (referee 13) (registered 2026-10-15 16:50)

Llama-3.1-8B base (`NousResearch/Meta-Llama-3.1-8B`, ungated mirror; plain
transformer, no Gated-DeltaNet, d_model 4096 — also a width between the
paper's 1024 and 5120). The 27B protocol, unchanged: BF16 GGUF, wiki and
mixed Gram captures, ladder Q2_K / IQ2_M / IQ3_XXS / IQ3_XS / Q3_K_S /
Q3_K_M / Q4_K_M from BF16, MIXED base + r128 Q8 non-FFN adapter, a
same-byte CONTROL by the §D rule order (starting from Q3_K_S as at 27B),
RR-wiki and RR-mixed on Q2_K, free-grid on Q2_K; all scored on wiki, fw,
code, 100 chunks. The claims C1–C4 and B1–B4 are evaluated on this model
with the same rules. Results are reported whatever they show.

### Amendment 2026-10-15 17:30 (before any §G run): the 8B CONTROL

At Llama-3.1-8B the corrected configuration (MIXED + 115.9 MB adapter,
3,676,698,208 B) is only 12.2 MB above Q3_K_S, less than one whole kind
(all of attn_k = 17.8 MB). The §D rule therefore stops inside its first
step, with its own evenly-spaced layer subset: CONTROL = Q3_K_S + attn_k →
q4_k at 21/32 layers, 500,928 B below the corrected file. Consequence,
stated now: at 8B, C2 (corrected vs control) is close to a re-test of C1
(corrected vs Q3_K_S), and is reported that way. The §E free-grid arm
(mixed Grams, wiki imatrix) is added to the 8B pipeline as at 27B.
