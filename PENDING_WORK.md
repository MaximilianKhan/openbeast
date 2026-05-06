# Pending work — handoff note from Max (2026-05-05)

## STATUS UPDATE — items 2-5 complete, item 1 (docs) pending the new sweep
- Item 2: re-weighted to easy=1, medium=1.5, hard=2; ranking now by accuracy primary; speed tie-breaker. ✅
- Item 3: task 23 setup rewritten with heredoc instead of printf. Verified runs clean. ✅
- Item 4: 20 new tasks added (31-50). 5 easy, 5 medium, 10 hard. Spans software, hardware, finance, LLM, prob/stats. test_scripts.sh validates all 50. ✅
- Item 5a: agents/runner.py `_AGENT_INSTRUCTIONS` rewritten — explicitly enumerates all 13 tools (incl. start_agent for sub-agents), encourages running code over reasoning, encourages decomposing hard tasks via sub-agents. ✅
- Item 5b: benchmark relaunching now (50 tasks × 5 models, est ~3 hours).
- Item 1: snapshot of 30-task leaderboard saved at `evals/leaderboard_v1_30tasks.json`. Will update README/REFERENCE after the new sweep completes (using new 50-task numbers).

## Snapshot — 30-task v1 leaderboard (rebuilt with new weights)
```
1. Qwen 35B-A3B MoE Q4_K_M     acc 89.1  speed 89.7  comp 89.3  27/30 11h
2. Qwen 27B Uncensored Q5_K_P  acc 89.1  speed 83.6  comp 87.7  27/30 11h
3. Qwen 27B Q4_K_M             acc 89.1  speed 81.8  comp 87.3  27/30 11h
4. Qwen 27B Q5_K_XL            acc 86.1  speed 80.5  comp 84.7  26/30 11h
5. Gemma 4 31B-it Q5_K_XL      acc 86.1  speed 68.1  comp 81.6  26/30 11h
```

---

# Original handoff note (preserved below)

## Sweep completed (2026-05-05 16:56). Total: 6040s (1h 40min).

```
 #  MODEL                          COMP   CORR   SPEED   PASS    HARD   TIME
 1  Qwen 35B-A3B MoE Q4_K_M        88.7   88.4   89.7    27/30   11     1684s
 2  Qwen 27B Uncensored Q5_K_P     87.2   88.4   83.6    27/30   11      748s  ← fastest
 3  Qwen 27B Q4_K_M                86.7   88.4   81.8    27/30   11     1065s
 4  Qwen 27B Q5_K_XL               84.4   85.7   80.5    26/30   11     1039s
 5  Gemma 4 31B-it Q5_K_XL         81.3   85.7   68.1    26/30   11     1423s
```

Sweep summary file: `evals/results/sweep-20260505-165627.json`
Per-run results: `evals/results/eval-{slug}-20260505-*.json`
Leaderboard: `evals/leaderboard.json`
Live log: `/tmp/claude-1000/-home-max-Documents-models/e4593464-535c-44fc-beb3-e41d31c168b1/tasks/bytc7sdel.output`

**Key findings:**
- Task 23 (SQL injection) — setup `printf` failed for ALL models with "invalid format character". Max called this. **Fix in item 3.**
- Task 27 (Brainfuck) — at least one model failed validation (truncated traceback in log). Re-check.
- Accuracy cluster is TIGHT (85.7-88.4). Speed spread is wide (68-90). Max's hypothesis confirmed: re-weighting should expose meaningful differences.
- Every model passed exactly 11/13 hard tasks. The 2 it fails are 23 (setup bug, not the model's fault) and one of {Brainfuck, the other variable one}. So real hard pass rate is closer to 11/12.

When resuming, execute these 5 items in order:

## 1. Review leaderboard + update docs
Wait for the sweep to finish (or read the partial state if I resume mid-sweep).
Score-rebuild from results: `python3 evals/scoring.py --rebuild`. Update README's
"Evals & Benchmarking" section with the actual ranked leaderboard. Also update
REFERENCE.md if any model showed unexpected behavior worth documenting.

## 2. Re-weight scoring
Change difficulty weights from `easy=1, medium=3, hard=5` to **`easy=1, medium=1.5, hard=2`** (sums, not factors).
Edit `evals/scoring.py` `DIFFICULTY_WEIGHTS`. Then keep speed and accuracy as
SEPARATE columns — Max suspects accuracy is what discriminates here. Print the
leaderboard with both columns visible (already the case) but emphasize accuracy.
Re-run `--rebuild` after the formula change.

## 3. Fix task 23 (`evals/tasks/23_sql_injection.json`)
All models failed it because the setup script has a string-escaping bug. Read
the task, identify the broken `printf` (likely the nested escapes in the SQL
string-concat injection points). Fix and re-validate with `bash -n`.

## 4. Add 20 more tests (50 total)
Distribution: **5 easy, 5 medium, 10 hard**. Subjects:
- Software engineering
- Hardware optimization (cache locality, SIMD, branch prediction)
- Financial mathematics (option pricing, VaR, portfolio optimization, Monte Carlo)
- LLM model architecture (attention math, transformer internals, KV-cache impl)
- Probability/statistics questions of the kind hedge funds ask in interviews

File names: `31_*.json` … `50_*.json`. Same JSON schema as existing tasks.
Validation must be deterministic.

## 5. Re-run benchmark with stronger tool/agent prompting
Before re-running, modify the agent's prompt (in `agents/runner.py` —
`_AGENT_INSTRUCTIONS` — and/or `system-prompt-tools.md`) to **encourage tool +
sub-agent use**. Models should know:
- They can spawn sub-agents via `start_agent` for hard subproblems
- They have web_search, fetch, bash — should use them when stuck
- This mirrors a production scenario where a model has a full toolset

Then save state (commit the changes? or just note them) and run
`python3 evals/benchmark_all.py` again. Compare new leaderboard vs. old.

## Notes
- The 30-task suite is already validated (test_scripts.sh check passes)
- Don't break the JSON schema or the validation contract
- Q4 model + 35B MoE haven't been re-tuned for context — if either OOMs, drop their `-c` and continue
- `tests/test_scripts.sh` should pass after every change
