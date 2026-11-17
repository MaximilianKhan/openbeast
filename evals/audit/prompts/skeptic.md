{{COMMON}}

You are the SKEPTIC for task {{TASK}} (units {{UNITS}}). An auditor produced these verdicts:
{{AUDIT_JSON}}

Challenge every verdict from the OPPOSITE direction, with your own reading of the transcript, the task and the validator:
- for TRUE_FAIL, actively look for a harness, prompt or validator cause the auditor missed: tool output truncation, a missing toolchain, the sandbox, an underspecified requirement the validator enforces, a misleading example, a fixture the task mentions that was absent, the contention timing;
- for any other verdict, try to show it was really the model's own mistake (would a careful, competent agent have succeeded with the same text and tools?), and check that the proposed fix would not make the task easier than intended or leak the answer.

You may replay solutions too. Return, per unit: agree (with the auditor), final_verdict, and your reasoning.
