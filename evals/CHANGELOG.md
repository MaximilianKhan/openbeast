# Eval suite changelog

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
