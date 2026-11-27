Context: OpenBeast's {{SUITE}} eval suite was run against {{MODEL}} ({{SERVING}}) through the agent runner agents/runner.py. The agent's tools (bash, write_file, edit_file, ...) executed on THIS harness host, not where the model runs. Results for every unit — transcript path, validator output, iterations, tokens, elapsed seconds, task file, dest, wall budget — are in {{UNITS_FILE}}. Each transcript is a JSONL of the agent's run: start, iteration, tool calls with arguments, tool results, done.

Repo with the task definitions and the harness that ran: {{REPO}}. It is READ-ONLY for you: never edit, commit or check out anything there{{READONLY_EXTRA}}. Task JSON: evals/tasks/<id>_*.json (task text, setup, validation script, dest; variants a=python b=go c=c d=cpp e=rust f=zig). References: evals/refs/. Harness: evals/run_eval.py, evals/vlib.py. Every unit of the suite has been PROVEN model-free (`tests/prove_task.py --suite {{SUITE}}`: the reference passes, wrong solutions fail), so a validator bug would have to be one the reference does not trip.

Replay tool: `{{REPLAY_CMD}} <unit> <solution-file-or-dir>` runs the real validator lifecycle on a candidate under the fixture lock (it may wait for another auditor) and prints PASS/FAIL and the validator output. Put scratch files ONLY under {{WORK_DIR}}/<unit>/.

Toolchains on this host: {{TOOLCHAINS}}. The agent's bash may have used a different python than the validator: check which.

Time: a unit's wall budget is in {{UNITS_FILE}} (wall_s: its max_wall_s x {{CONTENTION}}, the --jobs contention scale). A run that ended on the turn cap (iterations == max_iter) at a small fraction of its wall is turn-bound, not clock-bound.

Verdicts:
- TRUE_FAIL = the model's own mistake under a fair, clear task (wrong algorithm, a bug, a compile error in its code, gave up, ran out of its budget while making normal progress).
- HARNESS = the agent was hindered by the environment or tools (command missing, PATH, sandbox denial, a tool bug, a fixture missing, truncated tool output, a path guard misfire).
- PROMPT = the task text is ambiguous, misleading or underspecified so that a reasonable, correct reading fails.
- VALIDATOR_BUG = the validator rejects a correct solution or judges something the task never asked for.
- INFRA = server/API errors, gate timeouts, an interpreter that failed to start: not caused by the model.
- UNCLEAR = cannot tell.

Be rigorous and skeptical in both directions: do not excuse the model, and do not blame the model for the harness. Quote concrete evidence (transcript lines, validator lines, file:line).
