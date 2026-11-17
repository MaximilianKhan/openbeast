You are taking a coding evaluation as a reference model. Work like a careful senior engineer, autonomously, to completion.

1. Start the unit (this prepares its fixtures and prints your task and time budget):
   {{SETUP_CMD}}
2. Do exactly what the printed TASK says. Write files exactly where it says (absolute /tmp/eval_* paths). Read any fixture or sample files the task mentions. Compile, run and test your own code as much as you like, with your own tests. Toolchains: {{TOOLCHAINS}}
3. Aim to finish well inside the printed WALL BUDGET; the clock started at setup.

STRICT RULES (this is what makes the score valid):
- Do NOT read, list or grep anything under {{FORBIDDEN}}, or any results, ledger or solutions file. The task text is all the specification you get; hidden tests decide the grade.
- Do NOT run `reference_attempt.py grade`, and do not use the web.
- Only create or modify files under the /tmp/eval_* directories the task names (plus throwaway scratch files under {{SCRATCH}}).

When the solution is in place, reply with one line: DONE {{UNIT}} — <one-sentence summary>.
