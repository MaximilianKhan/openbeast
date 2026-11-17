# Failure audit

Every failed unit of a run gets an **auditor** and then a **skeptic**. The
auditor reads the transcript, rebuilds the model's final solution, replays it
through the real validator, and classifies the failure. The skeptic argues
the opposite direction. Only units the audit clears get rerun: a true fail
stands and is never re-rolled. The procedure is step 3 of
[`docs/MODEL_CAMPAIGN.md`](../../docs/MODEL_CAMPAIGN.md).

| File | What it is |
|---|---|
| `prepare.py` | results file(s) → `units.json` (one row per unit, with its transcript and wall budget), `groups.json` (failed units by base task), `args.json` (the rendered prompts), `prompts/` (the same prompts as plain files), `work/` (the auditors' scratch) |
| `prompts/common.md`, `auditor.md`, `skeptic.md` | the prompt templates (`{{PLACEHOLDER}}`s; prepare.py fills every one except `{{AUDIT_JSON}}`, which the runner fills with the auditor's output) |
| `failure-audit.workflow.js` | the Claude Code Workflow script: an auditor per group, then a skeptic per group, with structured output |
| `replay.py` | grade a candidate solution exactly as run_eval does (cleanup, setup, the solution at dest, pre_validate, validate at scale 2.0, cleanup) under the fixture lock |
| `verdicts.py` | the audit's output → final verdicts (the skeptic wins) + a Markdown table + rerun candidates |
| `transcripts.py` | maps result rows to `agents/logs/agent-*.jsonl` (run_eval does not record the path) |
| `langaudit.py` | the per-language failure taxonomy (knowledge / lint / borrow / host / research), the evidence for or against a language pack |

## The verdicts

| Verdict | Meaning | What happens |
|---|---|---|
| `TRUE_FAIL` | the model's own mistake under a fair, clear task | stands; never re-rolled |
| `HARNESS` | the environment or a tool hindered the agent | fix the harness (a runner/tools change moves the era), rerun |
| `PROMPT` | the task text is ambiguous or misleading | fix the task, re-prove it (`tests/prove_task.py`), rerun |
| `VALIDATOR_BUG` | the validator rejects a correct solution | fix the validator, re-prove, rerun |
| `INFRA` | server, gate or interpreter trouble | rerun as is |
| `UNCLEAR` | cannot tell | rerun (indeterminate) |

## Run it

```bash
. ~/.local/opt/openbeast-eval-env.sh          # the harness host's toolchains + $OPENBEAST_EVAL_PY (docs/SPARK_HYDRA.md F6)
RUN=evals/results/audit-<model>-<arm>          # anywhere; evals/results/ is untracked
python3 evals/audit/prepare.py $(python3 evals/campaign.py paths --campaign v5-harness --model-name "<Model>" --arm A) \
    --out-dir $RUN --model "<Model>" --serving "2x DGX Spark, TensorFold, thinking off" \
    --env-sh ". ~/.local/opt/openbeast-eval-env.sh" --python "$OPENBEAST_EVAL_PY"
# an L0 profile row instead: pass its results file, e.g. evals/results/eval-<slug>-L0-<ts>.json
```

`--repo` is the tree the run used (default: this checkout). Point it at that
tree when the arm ran from another one (`campaign.py status` prints each
arm's tree): its task text and validators are the ones the model saw, and
its `agents/logs/` holds the transcripts (`--logs-dir` overrides).
prepare.py warns about any failed unit with no matching transcript.

**Claude Code (Workflow).** Ask Claude Code to run the Workflow script
`evals/audit/failure-audit.workflow.js` with the contents of
`$RUN/args.json` as its args. Save the result it returns as
`$RUN/audit-result.json`.

**Any other agent runner.** For each group in `$RUN/groups.json`, run one
agent on `$RUN/prompts/<task>.auditor.md` and ask for the JSON shape in
`failure-audit.workflow.js` (`VERDICT`). Then run one agent on
`$RUN/prompts/<task>.skeptic.md` with `{{AUDIT_JSON}}` replaced by the
auditor's JSON (`CHALLENGE` shape). Collect
`[{"task", "audit", "challenge"}, ...]` into `$RUN/audit-result.json`.
The agents need shell access to the harness host and its toolchains. They
must not write anywhere but `$RUN/work/`.

Then:

```bash
python3 evals/audit/verdicts.py $RUN/audit-result.json --out $RUN/final.json --md $RUN/verdicts.md
# rerun what the audit cleared (TRUE_FAIL is refused), after fixing what is real:
python3 evals/campaign.py rerun-lost --campaign v5-harness --model-name "<Model>" --arm A \
    --units <u1,u2> --verdicts $RUN/final.json
```

A fix to a task or validator changes the task's hash, and a runner/tools fix
moves the era. Rerun rows from after such a fix are measured under the new
code: say so wherever you report them, and prefer a full rerun of the arm
when the fix is harness-wide.

## The language taxonomy

```bash
python3 evals/audit/langaudit.py L0=evals/results/eval-<slug>-L0-<ts>.json \
    A="$(python3 evals/campaign.py paths ... --arm A | tr ' ' ,)" --out $RUN/taxonomy.json [--errors]
```

The table counts, per language, failed units whose transcripts hit a
knowledge error (K: an API the model remembers that the installed toolchain
no longer has, e.g. the zig 0.16 std drift), a lint error (L), a Rust borrow
error (B), a BSD/macOS userland error (H), or turns spent reading the
toolchain's own sources (R). A pack goes into arm E only for languages where
K is a real share of the failures (default threshold 30%). H means a host
pack or the env block will help, whatever the language. On GLM-5.3-Flash
(2026-11-16, arm A) the table said zig 5/5 K, rust 3/6 K, and host errors in
9 failed units. Go, C, C++ and Python showed none.
