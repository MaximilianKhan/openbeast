---
name: api-design
description: Design a public API contract — function signatures, types, error model, example usage — BEFORE implementing. Activate when adding a new public function / endpoint / library, when changing an existing public surface, or when the user says "design an API for". Frontier models do this naturally; local models often code first and accidentally lock in bad shapes.
allowed_tools: [read_file, grep]
recommends_subagent: false
---

# API design

The shape of your public API outlives the implementation. You can rewrite the
internals next quarter; you can't easily change a function signature once
callers depend on it. Design the contract first, code the implementation
second.

This is the discipline frontier models apply to anything they consider
"public surface" — library functions, REST endpoints, CLI flags, message
schemas, plugin hooks. The cost of getting it right upfront is one short
design pass. The cost of getting it wrong is a deprecation cycle.

## When to apply

- New public function in a library
- New REST / RPC endpoint
- New CLI command or flag
- New message schema (queue, event, webhook)
- Change to any of the above (signature, behavior, error model)
- Plugin / extension contract

## The four-element contract

Before any implementation, write down:

### 1. Signature

The shape of the call. Names + types.

```
def historical_var(returns: list[float], confidence: float = 0.95) -> float:
```

```
POST /todos
Body: {"title": str, "description"?: str}
Response 201: Todo
Response 400: ValidationError
```

```
mytool [--verbose] [--format=text|json] <input-path>
```

Be specific about types. `int` not "number". `list[float]` not "array".
`Decimal` not `float` if precision matters. Optional vs required.

### 2. Behavior on the happy path

What does it DO? In one or two sentences.

> "Returns the loss threshold at the given confidence level — i.e., the
> negation of the (1 - confidence) quantile, so a positive number
> representing the loss."

Be specific enough that two implementations would do the same thing. "Computes
the average" is too vague — average of what, weighted how, on which subset?

### 3. Error model

What can go wrong, and how does the API tell the caller?

For functions:
- Exceptions raised (which ones, when)
- Error return values (sentinel, error tuple, Result type)
- Side-effect failures (does it leave state in a partial state?)

For HTTP:
- Status codes used (201, 400, 401, 404, 409, 422, 500 — what means what?)
- Error body format (consistent across endpoints)
- Idempotency (does retrying produce different behavior?)

For CLI:
- Exit codes
- stderr content
- Whether partial work is rolled back on failure

Be explicit. Many API papercuts trace back to under-specified error
behavior. "Don't know what to do on error" is itself a bug.

### 4. Examples (3-5 of them)

THIS is the part most often skipped. Examples make the contract concrete in a
way prose can't.

```python
# Happy path
historical_var([-0.05, -0.04, -0.03, -0.02, -0.01], 0.95)
# → 0.05  (loss threshold; 95% confident loss won't exceed)

# Default confidence
historical_var([-0.05, -0.04, -0.03])  # uses 0.95
# → 0.05

# Single value
historical_var([-0.05])
# → 0.05

# Edge: empty input
historical_var([])
# → ValueError("input must be non-empty")

# Edge: positive returns only (no losses)
historical_var([0.01, 0.02, 0.03])
# → -0.012  (negative VaR — gain, not loss; documented behavior)
```

Examples expose ambiguity. If you can't write 5 examples without inventing
new behavior or hedging, your spec isn't complete yet.

## Naming conventions

Names are part of the contract. Get them right.

### Functions

- **Verbs for actions** that do something: `compute_var`, `fetch_user`,
  `send_email`
- **Nouns for queries** that just retrieve: `user`, `current_balance`,
  `is_admin`
- **Predicate names start with `is_`, `has_`, `should_`**: `is_palindrome`,
  `has_permission`, `should_retry`
- **Avoid `get_`** for cheap accessors in many languages; use bare nouns
  (`user.name`, not `user.get_name()`). Reserve `get_` for actually-doing-work
  (`get_remote_config`).

### Parameters

- **Order: most-essential to least-essential**, required before optional.
- **Same parameter name across related functions.** `path` everywhere; not
  `path` in one place and `filepath` in another.
- **Booleans named affirmatively** with a default that matches the
  function's likely use (`include_drafts=False`, not `exclude_drafts=True`).
- **Avoid more than 3 positional parameters.** Beyond 3, use keyword args
  (Python) or an options struct (Go, JS).

### Types

- **Domain types over primitives.** `UserId` not `int`, `Money` not
  `Decimal`. Even a thin wrapper prevents mixing semantically distinct
  values.
- **Specific types over generic.** `list[Trade]` not `list`.
- **Make impossible states unrepresentable.** If two fields can't be set at
  the same time, use a tagged union / sum type instead of two optional
  fields.

## Error-model patterns

### Exceptions vs. error returns

- **Python**: exceptions are idiomatic. Use them. Define custom exception
  classes for domain errors (`InsufficientFundsError`, not bare `ValueError`).
- **Go**: error returns. The last value should be `error`. Don't panic for
  expected errors.
- **Rust**: `Result<T, E>` for recoverable, `panic!` for invariant
  violations.

### Error message quality

```
# Bad
raise ValueError("invalid input")

# Better
raise ValueError("expected non-empty list of returns")

# Good
raise ValueError(f"expected non-empty list of returns, got {type(returns).__name__} of length 0")
```

The error message should help the caller fix the call. Include what was
expected, what was received (with context), and what can be done about it.

### HTTP status codes

| Code | When to use |
|---|---|
| 200 | Read succeeded, content in body |
| 201 | Resource created, location in `Location` header or body |
| 204 | Success, no body to return |
| 400 | Client sent malformed data (parse error) |
| 401 | Not authenticated |
| 403 | Authenticated but not authorized |
| 404 | Resource doesn't exist |
| 409 | Conflict (concurrent modification, duplicate, etc.) |
| 422 | Well-formed but semantically invalid (validation error) |
| 429 | Rate-limited |
| 500 | Server bug |
| 503 | Server temporarily unavailable |

Use them faithfully. A 200 with `{"error": "not found"}` in the body is
broken. Use 404.

## Versioning

Decide upfront: is this API versioned, and how?

- **Library functions**: rely on the language's import mechanism + semver.
  Breaking changes bump major version.
- **HTTP**: URL versioning (`/v1/users`) is the most readable. Header
  versioning is more elegant but harder to explore.
- **CLI**: don't break. Add new flags, deprecate old ones with warnings,
  remove only on major versions with notice.

Even if you "won't have v2", design as if you will. Future-proofing is cheap
when you don't have callers yet.

## Anti-patterns

- **Implementation-shaped APIs.** The signature reflects how the function
  is implemented, not what the caller wants. (E.g., taking a `Connection`
  parameter when callers don't have one — leaks the impl detail.)
- **Boolean flags that should be enums.** `process(force=True)` —
  `force` is fine, but `process(mode='strict' | 'lenient' | 'force')` is
  often clearer.
- **Magic strings.** Returning `"OK"` vs `"NOT_FOUND"` instead of an enum.
  Callers will misspell, drift.
- **Mixing levels of abstraction.** `serve_user(filepath: str)` reads a
  file AND serves a user — two functions waiting to be split.
- **Over-engineered.** Adding optional callbacks, extension points, and
  config knobs "in case." YAGNI applies — design for the use case you have.
- **Names that lie.** `get_user()` that creates one if missing is a lie.
  Call it `find_or_create_user()`.

## Pairing with other skills

- After `spec-extraction` produces inputs/outputs/edges, this skill turns
  them into a concrete signature.
- `architecture-proposal` covers the BIGGER design (modules, dataflow);
  this skill covers the surface contract. They compose.
- `test-driven-development` writes tests against the contract you produce
  here; the examples in section 4 ARE your initial test cases.

## Done criteria

A good API contract:
- [ ] Signature is precise (types, optionality, defaults)
- [ ] Happy-path behavior described in 1-2 sentences
- [ ] Error model explicit (which errors, when, how signaled)
- [ ] 3-5 examples covering happy path + edge cases + at least one error
- [ ] Names follow language/project conventions
- [ ] Future versions / extensions thought through (even if not added)
- [ ] You'd be willing to publish this signature and be held to it for a year
