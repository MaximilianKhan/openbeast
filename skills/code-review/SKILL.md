---
name: code-review
description: Multi-pass code review for correctness, security, performance, idioms, and tests. Activate when the user asks for review, audit, "look this over", or post-PR analysis.
allowed_tools: [bash, read_file, grep]
recommends_subagent: false
---

# Code review

You are reviewing code as a senior engineer would — multiple passes, each with
a specific focus. Don't try to do all five at once; one pass at a time, write
findings, then move to the next pass.

## The five passes

Run them in this order. Each pass has a distinct mental mode.

### Pass 1 — Correctness

Read for what the code does. Does it match what the spec / function name /
docstring claims? Find:
- Off-by-one (loop bounds, array indices, range ends)
- Edge cases unhandled (empty input, single element, max value, negative,
  null/None, integer overflow)
- Wrong invariants (e.g., sum should equal initial total + sum of additions)
- Mishandled error paths (exception swallowed, error returned but ignored,
  resource leaked on error)
- Off-by-default-by-one (counts vs indices)

If you can't follow the logic, that's a finding too — code that's hard to
review is hard to maintain.

### Pass 2 — Security (only if user-facing)

Activate this pass on any code that handles input from outside the trust
boundary (HTTP, file uploads, config, env vars, command-line args, deserialized
payloads). See `skills/security-audit` for a deeper checklist; quick hits:

- SQL: parameterized queries only, never string-concat
- Shell: never `shell=True` with user data; use `shlex.quote` or argv lists
- Auth: timing-safe compares for tokens (`hmac.compare_digest`, not `==`)
- Crypto: don't roll your own; use stdlib or vetted libraries
- Secrets: not in source, not in logs, not in error messages

### Pass 3 — Performance

Look for obvious complexity bombs:
- O(n²) inside a hot loop where O(n log n) is straightforward
- `list.pop(0)` or `del list[0]` in a loop (use `collections.deque`)
- Repeated re-computation of an invariant (move out of loop)
- Unbounded memory growth (caches without eviction, accumulating logs)
- Synchronous I/O in tight loops (especially network)

Don't micro-optimize. Focus on asymptotic + obvious wins.

### Pass 4 — Idioms

Read against the language and project conventions:
- Python: list comprehensions over explicit loops where natural; `with` for
  resources; type annotations on public APIs; no mutable default args
- Go: error returns checked, `defer` for cleanup, no needless interface{}
- C: `static` for file-scoped helpers, `const` where applicable, free what you
  malloc
- C++: RAII over manual delete, `auto` where it aids readability, prefer
  algorithms over hand loops

For project-specific conventions, read the surrounding code and match it. If
the project uses tabs and the change uses spaces, that's a finding.

### Pass 5 — Tests

Are there tests? Do they actually test the change? Look for:
- Tests that pass even when the implementation is wrong (false-positive
  validations — see `skills/eval-task-author` pitfalls)
- Missing edge case tests (empty, single, max, error path)
- Tests that test the test framework, not the code (overspecified mocks)
- Tests that don't actually run (skipped, xfail, commented out)

If tests are missing for non-trivial logic, request them.

## Output format

For each finding, give:
1. **Severity** — `critical` (must fix before merge) / `major` (fix before
   merge unless explicit waiver) / `minor` (cleanup, optional) / `nit`
   (style preference)
2. **Location** — `file.py:42` or function name
3. **What's wrong** — one sentence
4. **Why it matters** — one sentence. If you can't explain why it matters,
   it's probably not worth raising.
5. **Suggested fix** — concrete code or one-line description. Don't say "you
   might consider"; just say what to do.

Example:
```
[major] storage.py:38 — `if password == stored:` is a non-timing-safe compare.
Why: an attacker measuring response time can extract the password byte by byte.
Fix: use `hmac.compare_digest(password.encode(), stored.encode())`.
```

## Anti-patterns in code reviews

Don't:
- Bikeshed naming (unless the name is actively misleading)
- Suggest rewrites of code that works (scope creep)
- Recommend speculative generalization ("what if we need this for X later")
- Pile on minor findings while missing a critical one — prioritize ruthlessly

Do:
- Lead with the highest-severity findings
- Quote the exact lines you're flagging (so the author can find them)
- Suggest the fix, don't just point at the problem
- Acknowledge what's good — gives signal that you actually read it

## When to spawn a sub-agent for review

If the diff is large (>500 lines or >5 files), consider spawning specialized
sub-agents in parallel:

```
start_skill_agent("code-review", "review the diff at /tmp/work/changes.patch focusing on Pass 1 (correctness)")
start_skill_agent("security-audit", "audit /tmp/work/changes.patch for security issues")
start_skill_agent("debugging-methodology", "trace the failure path in test_X for the changes in /tmp/work/changes.patch")
```

Then gather their findings and consolidate.
