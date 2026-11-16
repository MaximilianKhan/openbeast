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

> **v5 supersedes the two paragraphs above for every new unit.** Writing
> `expected.txt` or `check.py` into the agent's working dir hands it the
> answer key (a probe that only `cat`s the file passed v4 units). A v5 unit:
> setup writes 2-5 labelled samples only (`sample_input.txt`,
> `sample_output.txt`); the validator is `type: python` and carries a seeded
> generator + the oracle (or a property check for non-unique outputs), feeds
> hidden cases over stdin from memory, prints only the failing case's index,
> and finishes in <= 15 s at scale 1. Declare `ref_stem`/`dest` in the JSON,
> keep >= 2 wrong solutions with their failure classes in
> `evals/refs/<task_id>/`, and run `python3 tests/prove_task.py --unit <id>`
> until it prints PROVEN. The agent cannot read `evals/tasks/` while a unit
> is measured (the oracle seal). The oracle inside the validator is safe only
> if the validator follows "Validator isolation" below. The seal covers the
> agent's tools, not the solution's own code at validation time.

## Validator isolation (v5, required)

The solution is written by the model under test. It runs with the same uid
as your validator, and it may try to pass without solving: by reading your
argv, environment or files, by planting modules on your import path, by
monkeypatching your functions, or by hiding CPU in forked children. The v5
verifiers passed a dozen families exactly this way. The full rationale is in
`evals/README.md`, "Validator isolation".

**What the harness already does** (`run_eval.run_validation`; you get it
for free):

- The script arrives on **stdin**, from an already-unlinked file. A python
  validator runs as `python -I -S -`; a bash validator runs as
  `sh -c '<read stdin>; eval'`. Your source is in no argv.
- The cwd is a fresh, empty 0700 dir, removed after the unit. It is not on
  `sys.path`, and neither is the harness cwd. `setup`, `pre_validate` and
  `cleanup` get their own fresh cwd too, with `PYTHONSAFEPATH=1`: graded
  code can find any ancestor's cwd through the ppid chain and plant a
  module there, so never make a fixture command depend on its cwd.
- The environment is scrubbed: PATH, HOME, locale, toolchain vars, and
  `OPENBEAST_TIMEOUT_SCALE`. On Linux the validator is non-dumpable.
- `import vlib` works, injected from source; it is never a file on disk.

**What your validator must do:**

1. **Run the solution in a SEPARATE process.** Judge only its output. Never
   import the solution into the process that holds the oracle, the seed or
   the expected answers. Never trust the solution's in-process state: a
   module you imported can rebind your functions, `time.perf_counter`, or
   `sys.exit`. If a scenario driver has to load the solution in-process (a
   Python API task), that driver is a separate child that holds **no**
   answers. It prints observations; the parent compares them.
2. **Use `vlib.run_solution`** to run it:
   `r = vlib.run_solution(cmd, input=data, cpu_s=4)`, then check
   `r.verdict == "ok"` and `r.returncode == 0`, or just `r.ok`. It runs the
   solution in its own session and process group. The CPU budget counts
   every descendant, through a cgroup, or else RLIMIT_CPU plus the wait4
   rusage plus a process-tree sampler with a Linux subreaper. Orphans and
   survivors get verdict `daemon`, every descendant is killed afterwards,
   and fork is refused by default (`allow_fork=True` to permit it). Report
   `r.reason`, never the oracle.
3. **Scale every fixed limit.** Write limits at scale 1 and wrap them in
   `vlib.t(seconds)`, or pass them to `run_solution`, which scales them
   itself. Under `--jobs 4` the scale is 2.0. Use `scale_cpu=False` only for a
   perf gate whose subject is CPU time. A cold zig or rust compile can't be
   predicted: do an untimed warm-up exec of a fresh binary before the timed
   run (first exec on macOS costs 3-11 s of Gatekeeper scanning).
4. **No `python -c SRC` children.** A child's argv is readable too. Start a
   driver as `[sys.executable, "-I", "-S", "-"]` and feed SRC on stdin. Pass
   the driver's data in a file or a pipe. In a bash validator, use a quoted
   heredoc to `python3 -I -`. The prover lints for both.
5. **Run a Python solution with `vlib.python_cmd(path)`** (`-I -S`). Then
   site-packages (numpy, torch) is really unavailable when the prompt says
   so, and the script's dir is not on its path.
6. **Lint with the AST, not a regex**: `vlib.lint_python(src,
   ban_modules=("numpy",), ban_calls=("divmod",))`. It catches
   `import math, torch`, `from x import y`, `__import__`, importlib, exec,
   eval, getattr with a computed or dunder name, dunder walks,
   `"{0.__globals__}".format(f)` field lookups, and `sys.modules`. For C,
   C++ and zig, lint *after* stripping comments **and** resolving aliases
   (`const s = std; s.crypto`, `@field(std, "crypto")`), and ban the
   sanitizer escape hatches (`__tsan_*`, `__sanitizer_*`, `no_sanitize`)
   wherever TSan/ASan is the check. A lint is the second line of defence,
   after process separation.
7. **Check all of the output.** A checker that tests only part of the output
   (one field, the last case, a containment prefix without the separator)
   is a pass for a partial solution. Assert the count, every field, and
   every case.
8. **Keep secrets in memory.** Files you write are readable by the same uid
   wherever they live. Write only inputs to disk. Expected outputs stay in
   your process.

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
- [ ] Validator follows "Validator isolation": solution in a separate process
      via `vlib.run_solution`, every fixed limit through `vlib.t`, no
      `python -c` children (`tests/prove_task.py` lints the last two)
- [ ] `tests/test_scripts.sh` passes
- [ ] (If new subcategory introduced) noted in commit message
