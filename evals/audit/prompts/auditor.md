{{COMMON}}

YOUR GROUP: task {{TASK}}, failed units: {{UNITS}}.

For EACH unit:
1. Read the task text and the validator for that variant, and the unit's row in {{UNITS_FILE}}.
2. Read the transcript end to end. Note every tool error, command-not-found, timeout, refusal, path warning, truncated output, compaction, and how the run ended (task_done? the turn cap? the wall kill?). Compare elapsed_s with the unit's wall_s, and iterations with max_iter.
3. Reconstruct the model's FINAL solution file(s) from the transcript's write_file / edit_file / bash calls into {{WORK_DIR}}/<unit>/, and replay it. Does the replay reproduce the recorded failure? If the failure is a wrong answer, find the actual bug in the model's code (or show the validator is wrong).
4. If several variants of the same task failed the same way, say whether it is systemic (the same prompt flaw, the same harness issue) in systemic_note.
5. If the verdict is not TRUE_FAIL, give an exact proposed_fix: the change to the task text, validator or harness.

Return the structured result: for each unit its verdict, confidence, root_cause, evidence, replay (the replay's result, or why it was not possible) and proposed_fix (empty for TRUE_FAIL); and one systemic_note for the group.
