# Reference attempt (the ceiling row)

This is a frontier model's attempt at the same suite: the same tasks,
fixtures, validators and thresholds as the measured models. It answers "how
much of the gap is the model, and how much is the suite?". On 2026-11-16
Claude Opus 5.5 passed **71/71** v5 units (68/71 inside the strict 1x wall),
and its integrity scan was clean. That row is **model-independent**, so one
ledger per suite version serves every campaign's report. Rerun it only when
the suite changes (a new `SUITE_VERSION`, or a task or validator fix).

The lifecycle is split so the solver never sees a validator's output, just
as a measured model never does (`evals/reference_attempt.py`):

- `reference_attempt.py setup <unit>` cleans up, sets up, and prints only the
  task text and the wall budget.
- the solver works with its own tools. It has no turn cap, and the clock
  starts at setup.
- `reference_attempt.py grade <unit>` runs as a SEPARATE agent: pre_validate,
  validate at `--scale 2.0`, stash the solution, and append a ledger row
  (passed, indeterminate, elapsed, wall budget, over_budget).

| File | What it is |
|---|---|
| `prepare.py` | creates the run directory (`ledger.jsonl`, `state/`, `solutions/`) and `args.json`: the task groups and every solver and grader prompt, rendered |
| `prompts/solver.md`, `grader.md` | the prompt templates: the solver's strict rules and the grader's one command |
| `reference.workflow.js` | the Claude Code Workflow script: per task group, for each variant in order, a solver agent and then a grader agent |
| `integrity_scan.py` | scans the solver transcripts for forbidden reads (validators, refs, tests, other runs' logs, the ledger, solutions), a grade call, or the web |

## Run it

```bash
. ~/.local/opt/openbeast-eval-env.sh
python3 evals/reference/prepare.py --suite v5 --env-sh ". ~/.local/opt/openbeast-eval-env.sh" \
    --python "$OPENBEAST_EVAL_PY" \
    --forbid <any other run's results or audit directories on this host>
# -> evals/results/reference/claude-opus-5-5-v5-<ts>/args.json
```

Ask Claude Code to run the Workflow script
`evals/reference/reference.workflow.js` with that `args.json` as its args.
Run it from the repo, with nothing else using the `/tmp/eval_*` fixtures:
a measured run on the same host shares them, and the fixture lock
serializes only the grade step. On 2026-11-16 the main Workflow run took
about 1.6 h for 71 units.

Then check the run:

```bash
python3 evals/reference/integrity_scan.py \
    ~/.claude/projects/<project>/<session>/subagents/workflows/<run id>/ \
    --forbid-from evals/results/reference/<run>/args.json --json <run>/integrity.json
```

`CLEAN` is part of the row's claim. If a solver is flagged, look at the
command. A `relative` flag is a relative `evals/` or `tests/` path that may
point into the fixture's own directory: read it before you conclude
anything. A real forbidden read means that unit is attempted again from
scratch or dropped from the row. Scan every Workflow run that contributed
rows (a resumed run leaves its transcripts in a second run directory).

With another agent runner, use the same two prompts per unit from
`args.json` (`solver[unit]`, then `grader[unit]`) and keep the transcripts
for the scan. The scan reads Claude Code's format (assistant `tool_use`
blocks). Convert other formats to it, or extend `tool_uses()`.

Report the row as a reference, never as a leaderboard peer. It used a
different scaffold (its own tools, not `agents/runner.py` and
`agents/tools.py`) and had no turn cap. `campaign.py report
--reference-ledger <run>/ledger.jsonl` puts it at the top of the totals
table, both as passed and as passed inside the 1x wall.
