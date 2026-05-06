---
name: test-driven-development
description: Test-driven development discipline — red, green, refactor. Activate when the user asks to TDD a feature, when adding non-trivial functionality to a codebase that has tests, or when the user explicitly wants test-first.
allowed_tools: [bash, read_file, write_file, edit_file, grep]
recommends_subagent: false
---

# Test-driven development

The actual discipline of TDD — not "write tests after". The cycle is short,
strict, and produces code that's testable by construction.

The piece local models almost always skip is **seeing the test fail first**.
They write the test, write the code, run the test, and call it done. That's
not TDD — that's writing tests. TDD is the loop where each step is verified.

## The Red-Green-Refactor cycle

```
1. Red:      Write a single failing test. Run it. Confirm it fails for the
             reason you expect (not a syntax error, not an import error —
             actually red because the behavior isn't there).

2. Green:    Write the MINIMUM code to make that test pass. Resist the urge
             to write more. Run all tests. Confirm green.

3. Refactor: Improve the structure now that you have a passing test as a
             safety net. Run all tests after each change. Stay green.

4. Repeat:   Pick the next test. Smallest meaningful behavior next.
```

Each cycle is minutes, not hours. If you're spending more than 10 minutes in
red, your test is too big.

## What "minimum code" means

The minimum code is the SMALLEST change that flips this one test from red to
green. Even if it looks ridiculous.

For a `sum_list` function with a test asserting `sum_list([3]) == 3`:

```python
def sum_list(items):
    return 3   # yes, really
```

This isn't a joke. It forces you to write the next test
(`sum_list([1, 2]) == 3`), at which point hardcoding stops working and you
have to actually implement.

This discipline pays off because:
- Each test pins down a real piece of behavior. You never write code that
  isn't justified by a failing test.
- Your test suite is always the spec — every line of production code traces
  to a test that demanded it.
- You can't over-engineer. Speculative code can't get past "make the next
  test pass".

## What to test next

The Kent-Beck sequence for picking the next test:

1. **Degenerate cases first.** Empty input. Single element. Zero. Null.
2. **Smallest non-trivial case.** Two elements. The basic addition.
3. **General case.** What the function is actually for.
4. **Edge cases.** Boundary values, max sizes, weird inputs.
5. **Error cases.** Invalid input, exception paths.

For each, write the test BEFORE the production code that handles it.

## Refactor — the underloved third step

After green, before the next red, take 30 seconds to ask:
- Are there magic numbers that should be named?
- Is there duplication this test introduced that I can collapse?
- Are the names good?
- Is the function doing one thing?

Make small, safe improvements. Run tests after each. Don't refactor and add
behavior in the same step — that's how you end up debugging two changes at
once.

## Common failure modes

### Skipping red

You write the test, run it, see green immediately. Now you don't know if the
test actually works. Maybe it's never asserting. Maybe the behavior was
already there. **The discipline:** if you didn't see red, you don't know
your test is real. Break the production code on purpose, see red, fix it,
see green. Or write the test first, before the code.

### Tests that test the test framework

```python
mock_db = MagicMock()
mock_db.query.return_value = [User(id=1)]
result = service.get_user(1, db=mock_db)
assert result == [User(id=1)]
```

This passes if the function returns whatever the mock told it to return — but
it's not testing anything you wrote. Avoid heavy mocking; prefer real
collaborators (in-memory SQLite, fakes you control). If you must mock,
mock at the boundary, not the center.

### Cycles that take an hour

If your red phase takes more than 10 minutes, your "next test" is too big.
Split it. Pick a smaller behavior to drive next.

### Refactoring during red

You're in the middle of making a test pass and you notice some unrelated
code that's ugly. Don't refactor it now. Note it (TODO comment, or a
follow-up commit). Stay focused on getting to green first.

## When TDD doesn't fit

- **Exploratory / spike code.** When you don't know what the code should do
  yet, TDD gets in the way. Spike, learn, throw it away, then TDD the real
  implementation.
- **UI / visual code.** TDD is awkward for layout / appearance. Test the
  underlying logic; for visuals, use snapshot or visual-regression tools.
- **Performance optimization.** TDD validates correctness, not speed. After
  green, profile. See `performance-optimization` skill.

## Workflow with the rest of the toolset

- After `spec-extraction`, hand the spec to TDD: each spec assertion becomes
  a test.
- Pair with `code-review` for the green-but-ugly diff: review surfaces
  refactor candidates that the next refactor step should address.
- Use `bash` to run tests after every step. The cycle has to be fast.

## Setup for fast cycles

The longer your test runs, the harder TDD is. Before starting:

- Find a test runner that gives <2 second feedback for a single test
- Use `pytest path/to/test.py::test_name` (not the whole suite each time)
- For Go: `go test -run TestName ./pkg/path`
- For JS: `vitest path/to/test.ts -t "test name"`

If your test takes 10 seconds to run, your TDD cycle is broken. Fix the
test infrastructure before applying TDD.

## Done criteria for a TDD session

- Every production-code line is justified by a test that, if removed, would
  flip a corresponding test from green to red
- The test suite documents the function's contract — read it like a spec
- All tests passed at the end of every commit (use `git stash` if needed)
- The refactor step actually happened (don't end on "I'll clean it up later")
