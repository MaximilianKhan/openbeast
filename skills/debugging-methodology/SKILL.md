---
name: debugging-methodology
description: Systematic root-cause analysis. Activate when something is broken and the obvious fix didn't work — when you've tried two things and need to actually think instead of guessing harder.
allowed_tools: [bash, read_file, edit_file, grep]
recommends_subagent: false
---

# Debugging methodology

You're not here to apply patches until something stops failing. You're here to
**understand why**. The patch falls out of the understanding for free.

## The core loop

```
1. Reproduce reliably
2. Form a specific hypothesis
3. Design the cheapest experiment that would falsify it
4. Run the experiment
5. If hypothesis falsified, form a new one. Otherwise refine.
6. When the hypothesis predicts behavior at the source level, you're done.
```

Each cycle should take minutes, not hours. If you're stuck for more than 30
minutes on one hypothesis without an experiment, your hypothesis is too vague.

## Reproduce reliably first

A bug you can reproduce in 10 seconds is debuggable. A bug that happens "sometimes"
is not — your first job is to find the conditions that make it deterministic.

- **Pin the seed.** Random failures often have an RNG. Capture the seed from a
  failing run, hardcode it, see if the bug recurs.
- **Bisect inputs.** Big input that fails, small input that works → binary
  search for the smallest failing input. Often clarifies the bug by itself.
- **Isolate the environment.** Failing in CI but not locally? Capture the
  environment delta. (Locale, Python version, env vars, working directory,
  installed packages, kernel version.)
- **Write a regression test.** Even before fixing — encoding the repro as a
  test means you'll catch it if it comes back.

## Form specific hypotheses

A specific hypothesis predicts what happens at a specific line of code. Not
"there's a race condition somewhere" — "the value of `count` after line 42 is
zero, but line 43 expects it to be positive."

Vague hypotheses can't be falsified. Specific hypotheses can.

## The cheapest falsifying experiment

This is the lever. For any hypothesis, ask: what's the smallest, fastest thing
I can do that would change my belief about it?

- **`print` at the suspected line**. The first tool. Don't be too proud for it.
- **`assert` invariants** that should hold. If the assert trips, you've moved.
- **`pdb.set_trace()`** when you need to poke around interactively.
- **Strip down the test**. Comment out half the test body. If it still fails,
  the bug isn't in the commented part.
- **Compare working vs broken**. If commit X works and X+1 doesn't, diff them.
  If `python3.10` works and `python3.11` doesn't, run both side by side.

Avoid:
- **Fixing things speculatively** ("let me try changing this and see if it
  helps") — wastes cycles, conflates symptoms, makes the actual cause harder
  to isolate.
- **Reading 1000 lines hoping inspiration strikes**. Read the 10 lines around
  your hypothesis.
- **Asking the user to "try again"** without learning anything from the previous
  attempt.

## Common bug shapes (with diagnostic moves)

### "Works on my machine"

- Diff Python/Go/Node versions: `python3 --version`, `go version`
- Diff installed packages: `pip freeze | diff <(ssh other pip freeze) -`
- Diff env vars: `env | diff <(ssh other env) -`
- Diff working directory contents: do they have `.env`? Different config?
- Diff locale: `locale` — sort order, decimal separator, encoding all change

### "It worked yesterday"

- `git log --since=yesterday` — what changed?
- `git bisect` — find the breaking commit
- Check if a dependency updated: `pip install --upgrade` ran somewhere?
  `apt update && apt upgrade`?
- Check if a server-side dependency changed: external API, database schema,
  filesystem state

### "Sometimes it fails"

- Race condition: add `time.sleep` and see if the failure rate changes
- Flaky test: run it in a loop with `for i in {1..100}; do pytest test_x; done`
- Resource exhaustion: monitor memory, FDs, network connections during failing run
- Order dependence: does test_b fail only after test_a runs? Run them isolated.

### "Stack trace points at framework code"

- Read up the stack to your code. The framework is rarely the bug.
- Look at the values, not just the line. What was `self.thing` when it crashed?
- If there's no obvious-to-you bug at any frame, the bug is in your model of
  the framework's contract. Read the framework docs for the function you called.

### "It's not crashing, the output is just wrong"

- This is harder. The crash gives you a stack trace; wrong output gives you
  nothing.
- Instrument: print the input → input transformation → output at each stage.
  Find where the value diverged from expected.
- Pin the divergence to a specific function. Now you have a crash-style problem.

### "Heisenbug — disappears when I add prints"

- Probably a memory bug (C / C++ / Rust unsafe) or timing-dependent (concurrency).
- For memory bugs: valgrind, AddressSanitizer (`-fsanitize=address`), MSan.
- For concurrency: ThreadSanitizer, run under perf-record, throttle to single core.
- The `print` likely changed memory layout (heap allocation pattern) or timing
  (forced a flush). The bug is real; your repro is fragile.

## When you're truly stuck

After 4-5 cycles of hypothesis → experiment with no progress, you're in one of
these states:

1. **Wrong mental model.** You believe something about the system that isn't
   true. Re-read the docs / source for whatever you're "sure" about. Often the
   bug is exactly where you skipped reading.
2. **Wrong scope.** The bug is in a layer you haven't been looking at —
   filesystem caching, OS scheduler, network MTU, locale. Widen the scope by
   one level.
3. **Wrong abstraction.** You're debugging the symptom in a high-level
   language, but the bug is at the C / kernel / hardware level. Drop a level:
   `strace`, `ltrace`, `gdb`, `perf`.

If genuinely stuck, **spawn a sub-agent with `deep-counsel`**. Hand it the full
problem context — what you've tried, what each experiment told you, where you
think you're stuck. Sometimes a fresh framing breaks it.

## What "done" looks like

- You can explain in one sentence why the bug happened ("the cache invalidation
  fires before the write completes, so the next read sees the stale value").
- The fix is obvious from the explanation, not the other way around.
- A regression test exists for the failure mode.
- You know whether other code paths share the same root cause (often: yes).
