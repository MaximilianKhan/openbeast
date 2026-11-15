# Eval suite changelog

## v5 (2026-11-15) — the measured suite (CURRENT; era break)

`evals/SUITE_VERSION` = v5 rolls every cache era; the v4 board is frozen
and nothing is compared across versions. The suite is the manifest
`evals/suites/v5.json`: 111 scored units over 39 base tasks (50 kept from v4
on 16 bases, all upgraded; 61 new on 23 bases, ids 163-196) plus 7 unscored
zig probe units, every cell of 12 categories x 6 languages covered.

- Hidden seeded tests inside the validator; no expected file or `check.py`
  in the agent's dir; a mismatch prints only the case index.
- Zig API tutoring banned from prompts (L0 is unassisted).
- Oracle seal: `evals/tasks|refs|suites` unreadable under `OPENBEAST_EVAL=1`;
  `fetch`/`web_search` off under eval (or recorded).
- Every unit proven without a model (`tests/prove_task.py`).
- Arms L0/L1/L2 in one process (`run_eval.py --arms`), diagnostics keyed per
  unit, per-unit wall caps, longest-first scheduling, `--slots`.
- `evals/profile.py --budget-h 4` (calibrate k on L0, fixed L2 cut order,
  `budget_skip` never scored) and `evals/ablation.py` (McNemar, MDE /
  within_churn, strata, Horvitz-Thompson for stratified L2, `--pool`).
- Scoring: LANG over multi-language bases only; imputation retired for v5
  and every arm; v5 full row = 111 scored units.
- The v4 fast pin is renamed `v4-fast` (was `v5-fast`) and stays v4-only.

## v5-fast overlay (2026-09-08) — pinned fast subset, imputation-scored

NOT a new suite version — v4 stays current and frozen; v5-fast is a pinned
SELECTION of it (`evals/suites/v5-fast.json`): the 86 units that
discriminate between reference models + 20 all-pass tripwires. Runs via
`--suite v5-fast`, scores by imputing the 185 saturated units, which
reproduces full-291 capability exactly for in-family models (identity
verified per reference run by `make_fast_suite.py`; raw-subset ranking
measured at τ = +0.810 and rejected). Fast runs never enter the
leaderboard and share cache keys with full runs. ~0.85 h with `--jobs 4`
vs ~5.6 h full.

## v4 (2026-07-08) — the hardened suite (CURRENT)

137 base tasks / 291 effective units across all 12 topic categories.

A full adversarial re-review of v3.5 (see `docs/archive/EVAL_REVIEW_2026-07-07.md`)
found 117 of 159 tasks flagged — inflators (empty submissions / naive
algorithms / dead perf gates passing), deflators (correct code failing on
broken fixtures or comment-tripping lints), and unverifiable properties.
v4 is the rebuild (plan: `docs/archive/EVAL_V4_PLAN.md`):

- **Pruned 25** overlapping / unverifiable tasks (159 → 134).
- **Added 3 DSP tasks** (160 FIR, 161 Goertzel, 162 biquad IIR) so Signal
  Processing is a real category (1 → 4 tasks). Net: 137 tasks.
- **Every surviving flagged task hardened + verified BOTH ways**
  (reference passes; the documented cheat is empirically rejected) under
  9 systemic rules: pre_validate on all fixture tasks, comment/string-
  proof anchored lints, no readable answers, perf/memory gates measured
  to ≥10x, printf fixtures, self-contained variant texts, promise↔assert
  parity, durable 6-language refs, ≥5x timing margins.

v4 numbers are NOT comparable to v3.5. The v3.5 leaderboard is retained as
the "before" for the launch narrative and will be archived on a later
formal-display pass.

## v3.5 (2026-05-07) — legacy

159 base tasks / 323 units. Full report and leaderboard in
`docs/RESULTS.md`. Retained for the evolution story; see the review above
for why it was rebuilt.
