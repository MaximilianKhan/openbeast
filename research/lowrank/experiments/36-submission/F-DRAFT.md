# E36-F — paper text, drafted before the result (2026-10-22)

Fill-in drafts for the six `[PENDING:F]` markers. Written while round 3 was
still running, so no sentence here depends on the outcome except where a
variant is chosen. Procedure when F completes:

1. `python3 f_analysis.py` (refuses unless 3 runs per arm) → read the verdicts.
2. `python3 f_analysis.py --md` → paste both tables into §5.5 verbatim.
3. For each `{slot}` take the number from step 1; for each **[V: …]** keep the
   variant whose condition matches the registered verdict and delete the rest.
4. The registered rule decides the wording. If the sensitivity pass (no
   validator-timeout units) changes a verdict, say so in §5.5; it never
   replaces the primary verdict.
5. Delete every marker; `build.py --final` must then pass.

Registered wording map (PLAN.md §F): **differs** → "X passes more units than Y"
· **consistent with no difference** → "we cannot tell X from Y on this suite"
· **unresolved** → "X leads Y at p = …, but the run ranges overlap, so the
registered rule leaves the pair unresolved".

---

## 1. §5.5 Capability — replaces the first `[PENDING:F]` (04b-gsq-head-to-head.md:83)

We therefore ran each IQ3-class arm three times in the current eval era, as registered before any run: GSQ-RCO IQ3_S, the Unsloth UD-IQ3_S baseline, and the same baseline served with our correction adapter (`--lora`, confirmed loaded at scale 1.0 from the server's adapter endpoint). Runs were interleaved in rounds of (GSQ-RCO, baseline, corrected) so that a stop at a round boundary left the arms balanced, on the same 112 units, single slot, no cache, q4_0 KV cache, all nine runs on one llama.cpp build (b10865), one eval era and one set of validator toolchains, which the analysis script checks. The registered primary analysis pairs each unit's pass rate over its three runs between two arms (Wilcoxon signed-rank and sign test over the 112 units); a pair differs only if p < 0.05 and the three run-level counts of the two arms do not overlap [source: research/lowrank/experiments/36-submission/PLAN.md §F; experiments/36-submission/f_analysis.py; experiments/36-submission/rows-f.txt].

{TABLE 1 from `f_analysis.py --md`}

{TABLE 2 from `f_analysis.py --md`}

**GSQ-RCO against the baseline.**
[V: differs] Across three runs GSQ-RCO passes {d_gu} more units per run than the baseline (Wilcoxon p = {w_gu}; {pos_gu} units better, {neg_gu} worse), and its run range ({lo_g}–{hi_g}) lies above the baseline's ({lo_u}–{hi_u}). The single-run +12 of the earlier era was larger than the replicated difference; the direction holds.
[V: unresolved] GSQ-RCO leads the baseline by {d_gu} units per run (Wilcoxon p = {w_gu}), but its run range ({lo_g}–{hi_g}) overlaps the baseline's ({lo_u}–{hi_u}), so the registered rule leaves the pair unresolved. The single-run +12 of the earlier era did not replicate at that size.
[V: no difference] Across three runs we cannot tell GSQ-RCO from the baseline on this suite ({d_gu} units per run, Wilcoxon p = {w_gu}). The single-run +12 of the earlier era did not replicate.

**Our correction against the baseline.**
[V: no difference] The correction, which moves the baseline closer to the BF16 reference on every corpus and context we measured (§5.3, §5.4), does not change the baseline's task pass rate that we can detect ({d_ou} units per run, Wilcoxon p = {w_ou}).
[V: differs/unresolved, positive] The corrected arm passes {d_ou} more units per run than its own baseline (Wilcoxon p = {w_ou}; ranges {overlap_ou}), [registered verdict].
[V: negative] The corrected arm passes {d_ou} units per run fewer than its own baseline (Wilcoxon p = {w_ou}; ranges {overlap_ou}), [registered verdict], although it is closer to the BF16 reference on every corpus we scored.

**Our correction against GSQ-RCO.** {one sentence, registered verdict, d_og, w_og}

**Timeouts.** A unit fails with a validator timeout when the agent's program does not finish within the 30-second validation limit, and with a wall timeout when the agent loop itself reaches 30 minutes. Both count as failures in the primary analysis, as registered. Validator timeouts were {vt_g}, {vt_u} and {vt_o} per arm over three runs. Dropping every unit that hit one in any run ({n_vt} units) [V: leaves every verdict unchanged | changes GSQ − baseline from "{verdict}" to "{verdict}", so that difference rests partly on units the baseline lost to the validator's time limit].

The earlier single-run pair (88 against 76, 2026-09-14) belongs to a previous eval era and is reported beside these runs, not pooled with them.

## 2. §5.6 What the head-to-head shows — replaces the opening `[PENDING:F]` (04b-gsq-head-to-head.md:87)

Keep the existing paragraph and replace the marker with one opening sentence, then append the closing sentence below.

Opening, chosen by the GSQ − baseline verdict:
[V: differs] On the task suite, GSQ-RCO's training buys capability that KL divergence ranks the other way.
[V: unresolved / no difference] On the task suite, three runs per arm do not resolve the gap between GSQ-RCO and its baseline that a single run suggested.

Closing, chosen by OURS − baseline:
[V: no difference] Our correction's KLD gain does not show up as a task gain we can detect: fidelity to the reference and task pass rate are different measurements here as well.
[V: positive] Our correction's KLD gain also shows up as {d_ou} units per run on the task suite, at +0.9 GB.

## 3. Abstract — replaces `[PENDING:F — capability replication.]` (00-abstract.md:3)

[V: GSQ differs, OURS no difference] Over three runs per arm on an agentic coding suite, the trained checkpoint passes more tasks than its baseline ({d_gu} of 112 per run, p = {w_gu}); our correction, closer to BF16 than both, does not change its baseline's pass rate.
[V: GSQ unresolved/no difference] Over three runs per arm on an agentic coding suite, the trained checkpoint's lead over its baseline in task pass rate (+12 in one earlier run) is {d_gu} of 112 per run and not resolved under the registered rule.

## 4. Introduction contribution (3) — replaces `[PENDING:F — capability replication, three runs per arm.]` (01-introduction.md:21)

Same choice as the abstract, one sentence, ending "(§5.5)".

## 5. §7 changes table — replaces `§5.5 [PENDING:F]` (06-limitations-future.md:47)

`§5.5`

## 6. §7.5 Summary — replaces `[PENDING:F — capability replication]` (06-limitations-future.md:69)

[V: GSQ differs] on an agentic coding suite the trained checkpoint also passes more tasks over three runs, so there KL divergence ranked the more capable file lower.
[V: otherwise] three runs per arm on an agentic coding suite do not resolve a capability difference between them, and our correction's KLD gain does not appear as a task gain.

---

Also update when filling: FINDINGS.md (capability row), RESULTS-F.md (new, from
`f_analysis.py` output + the timeout breakdown), the response-to-referees note
for referee 4, and the supplementary manifest (add f_analysis.py, rows-f.txt,
logs-f/cap-*.log).
