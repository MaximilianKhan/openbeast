---
name: eval-task-author
description: Author a new task for the eval suite — JSON spec + reference implementation + verified validation. Activate when the user asks to add an eval task, write a benchmark task, or extend the suite. Encodes the 6 hard-won pitfalls from prior post-mortems.
allowed_tools: [bash, read_file, write_file, edit_file, grep]
recommends_subagent: true
---

# Eval task author

You are writing a single task for the eval suite at `evals/tasks/`. Your output:
1. A new `evals/tasks/{NN}_{slug}.json` file
2. A reference implementation that passes the validation
3. Confirmation that the validation actually accepts the reference and rejects an obvious wrong impl

## The schema

```json
{
  "id": "999_my_task",
  "name": "Human-readable task name",
  "difficulty": "easy" | "medium" | "hard",
  "category": "Algorithms & DS" | "Concurrency & Systems" | "Pure & Abstract Math" | …,
  "subcategory": "Trees" | "Number theory" | …,
  "max_iter": 20,
  "setup": "shell command — create fixture files in /tmp/eval_X/",
  "task": "What the agent should produce. Be specific about types, file paths, edge cases.",
  "validation": {"type": "python" | "bash", "script": "..."},
  "cleanup": "rm -rf /tmp/eval_X",
  "pre_validate": "OPTIONAL — only if setup writes harness fixtures the agent might corrupt"
}
```

If the task should have multi-language variants (Python / Go / C / C++ / Rust /
Zig — 6 supported languages), use the `variants` array instead of top-level
setup/task/validation/cleanup. See `skills/eval-variant-porter` for that pattern.

## Workflow

1. **Pick an ID.** Use the next free integer above the current max in
   `evals/tasks/`.
2. **Write the spec first.** Be explicit about types, return values, edge cases.
   Don't leave conventions implicit (sign convention, indexing, etc.) — the
   model will get them wrong half the time.
3. **Write the reference implementation YOURSELF.** Run it against the
   validation. Confirm `OK`. This is non-negotiable — your hand-written expected
   values are easy to miscount.
4. **Try a wrong impl.** Pick the most obvious incorrect interpretation a model
   might write. Confirm the validation REJECTS it. If it accepts, your
   validation is too loose.
5. **Test syntax.** `bash tests/test_scripts.sh` —
   parses every task JSON and checks validation scripts compile.
6. **Document the discrimination signal.** In the WORK_PLAN or a comment, note
   what "wrong" answer this task catches that simpler tasks don't.

## The six pitfalls

These are real bugs we shipped before learning. Read every spec against this list.

### 1. Don't substring-lint forbidden libraries

```python
# WRONG
assert 'numpy' not in src
```

This trips on docstrings citing the library by name. Models naturally write
`# Following numpy's linear convention` and fail. Use:

```python
# RIGHT
assert 'import numpy' not in src and 'from numpy' not in src
```

For function-call patterns (e.g. `int(arr, 256)`), use a regex:

```python
import re
assert not re.search(r'int\s*\(\s*\w+\s*,\s*256\s*\)', src)
```

### 2. Specify input and output types explicitly

If a function takes `data`, say whether it's `str` or `bytes`. If validation
passes a `str` but the model assumes `bytes`, you ship a coin-flip. Type
annotations in the task field are cheap; ambiguity is expensive.

### 3. Specify return-value contracts

If the validation has `assert kv.write(...)` (truthy on success), the spec must
SAY that `write` returns `True`. Otherwise models default to implicit `None` and
fail the assertion despite correct math.

### 4. Re-assert harness fixtures with `pre_validate`

Agents have full bash and **will** overwrite fixture files during their own
testing. If your setup creates a `healthcheck.sh` the agent must NOT modify,
add `pre_validate` that rewrites just that file. Skip `pre_validate` if your
setup seeds buggy code the agent must FIX in place — re-running setup would
clobber the fix.

### 5. Calibrate perf gates against the slowest valid implementation

To force a model toward an efficient algorithm, add a perf assertion. Calibrate
empirically: run a correct O(1)/O(log n) reference at increasing N, then run a
naive O(n)/O(n²) cheat. Pick N where the gap is ≥10× and set the budget at
~3× the reference time. Example: a linked-list queue at N=50000 takes 80ms;
list.pop(0) takes 2.5s — set the gate at 1.5s.

### 6. Solve the task yourself before committing

This catches:
- Off-by-one in expected outputs (especially for graph and string tasks)
- Edge cases the spec doesn't handle (empty input, single element, all-same)
- Validation logic that accepts wrong answers (false positives)

If the spec is silent on an edge case, decide deterministically and document.

## Stdio vs function-call validation

**Function-call** (Python only): validation imports the agent's module and
calls functions directly. Test setup is `import` + assertions. Most legacy
tasks use this.

**Stdio** (language-agnostic): agent writes a program that reads stdin and
writes stdout. Validation is `setup writes input.txt + expected.txt → run
program → diff`. Required for multi-language variants. Use for any new task
that might get variants.

For stdio, generate fixtures via heredoc when the data is non-trivial:

```bash
"setup": "mkdir -p /tmp/eval_x && cat > /tmp/eval_x/_gen.py <<'PYEOF'\n
import random\n
... fixture generation ...\n
PYEOF\npython3 /tmp/eval_x/_gen.py"
```

Avoid `python3 -c "...\n..."` — JSON `\n` becomes a real newline AFTER decoding,
but Python's `-c` parsing chokes on multiline. Heredocs sidestep the trap.

For non-deterministic outputs (e.g. multiple valid topological orders), write a
`check.py` via heredoc in setup that programmatically verifies the output.

## Difficulty tiers (with concrete examples)

| Tier | Weight | Time budget | Example |
|---|---|---|---|
| easy | 1.0 | 30s | "Count vowels in a string" |
| medium | 1.5 | 90s | "Three-way quicksort" |
| hard | 2.0 | 300s | "Number-theoretic transform with iterative bit-reversal" |

Multi-variant tasks split this by variant count: 4 variants of a hard task →
each is worth 0.5.

## Common categories (existing taxonomy — don't invent new ones lightly)

Algorithms & DS, Concurrency & Systems, Distributed / SysDesign, LLM / ML,
Mathematical Finance, Performance & HW Opt, Physics, Probability & Stats,
Pure & Abstract Math, SWE / DevOps, Security, Signal Processing & DSP.

If your task genuinely doesn't fit, you can add a new subcategory (no code
changes needed — `scoring.py` derives them from the JSON). Adding a new
top-level CATEGORY requires updating `docs/RESULTS.md` and `evals/README.md`.

## Done criteria

- [ ] Task JSON file in `evals/tasks/`
- [ ] Reference impl in `evals/refs/{slug}.{ext}` (durable; was `/tmp/refs/` pre-2026-05-07)
- [ ] Validation accepts reference impl (`OK`)
- [ ] Validation rejects an obvious wrong impl
- [ ] `tests/test_scripts.sh` passes
- [ ] (If new subcategory introduced) noted in commit message
