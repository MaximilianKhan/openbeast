---
name: spec-extraction
description: Extract a precise spec from an open-ended user request BEFORE implementing. Activate at the start of any task where the desired behavior is not fully nailed down — most user requests, in other words. Frontier models pause here naturally; local models often skip this and produce the wrong thing.
allowed_tools: [read_file, grep]
recommends_subagent: false
---

# Spec extraction

The user said "build a thing that does X." Before you write a line of code,
you need to know:

- What are the **inputs**? (types, ranges, formats, edge cases)
- What are the **outputs**? (types, formats, what to do on error)
- What are the **invariants**? (what must be true throughout)
- What is **out of scope**? (what NOT to build)
- What are the **constraints**? (perf, memory, deps, compat)

If you can't answer these, you're not ready to implement. The most common
local-model failure: starting to code, then realizing on iteration 3 that
the original interpretation was wrong. Pause early; save loops later.

## The extraction template

Write this down (literally, in a comment or scratch file) before coding:

```
SPEC: <one-line summary>

Inputs:
  - <name>: <type, range, format, who provides it>
  - ...

Outputs:
  - <type, format, what to do on success vs error>

Invariants / contracts:
  - <what must remain true>
  - <what's preserved across calls>
  - <thread-safety / concurrency requirements>

Edge cases (what to do when):
  - <empty input>
  - <max-size input>
  - <malformed input>
  - <duplicate input>
  - <input I haven't thought of yet>

Out of scope:
  - <thing I am NOT building>
  - <thing the user might assume but didn't say>

Open questions:
  - <ambiguity I'm choosing to resolve as X — flag for confirmation>
```

The "Open questions" section is critical. If you find yourself making >2
non-obvious assumptions, surface them before coding. One round of "I assumed
X — confirm?" beats three rounds of "this isn't what I wanted."

## Concrete questions to ask yourself

Read the request and run through this list. Each question that you can't
answer is a gap.

### Types and shapes

- What's the type of every parameter? (`int`, `str`, `bytes`, custom struct?)
- What's the type of the return value? (Including the error path.)
- What does "list of items" mean — list, set, generator, paginated stream?
- Are nulls / None / empty allowed? Distinguishable from "not provided"?

### Boundaries

- What's the minimum input? (Empty? Single element? Zero?)
- What's the maximum? (Memory limits? Time limits?)
- What charset? (ASCII, UTF-8, raw bytes, locale-dependent?)
- Numerical ranges? (Signed? Unsigned? Float precision?)

### Error handling

- What happens on bad input? (Throw? Return error? Return default?)
- What's "bad input"? (Be specific: empty list okay or error?)
- Is the function expected to validate, or trust its caller?
- Should errors include context (which input was bad) or just signal failure?

### Side effects

- Is this pure or does it have side effects? (Filesystem, network, state?)
- If state: what's the state lifecycle? Who creates it, who destroys it?
- Is this called once or many times? (Per request? Per process?)
- Concurrency: is this safe under multiple threads / coroutines?

### Performance

- Expected input size? (10? 1K? 1M?)
- Expected call frequency? (Once per session? Per request? Hot loop?)
- Are there hard deadlines? (P99 < 100ms? Within a frame budget?)

### Compatibility

- Does this need to match an existing API somewhere? (If so, find and
  read it.)
- Are there standards involved? (RFC, ISO, language convention?)
- What versions / dependencies are allowed?

## Examples

### Bad request → good spec

**User:** "Add a function that validates emails."

**Bad reaction:** start writing a regex.

**Good extraction:**

```
SPEC: validate_email(s: str) -> bool — returns True if s is a syntactically
valid email address per a chosen RFC.

Open questions (resolve before coding):
  - Q1: RFC 5321 (transport) or RFC 5322 (message format)? They differ in
        what's allowed (5322 is more permissive).
  - Q2: Internationalized addresses (IDN)? Local part with non-ASCII?
  - Q3: Validate just syntax, or also DNS / MX existence?
  - Q4: What about plus-addressing (foo+bar@x)?

Default resolutions (proceed unless told otherwise):
  - Q1: RFC 5322 syntactic only — most common interpretation
  - Q2: Yes, accept IDN per RFC 6531
  - Q3: Syntactic only — DNS check is a different function
  - Q4: Allowed — it's valid in 5322

Out of scope: deliverability check, normalization, mailbox existence.
```

Now you can write the regex (or, better, use `email.utils` / a vetted lib).

### Bad request → good spec, harder example

**User:** "Make the deploy script faster."

**Bad reaction:** parallelize the first thing you see.

**Good extraction:**

```
SPEC: reduce wall-clock time of `./deploy.sh` while preserving correctness.

What I need to know first:
  - Q1: Current wall-clock? (Need a baseline.)
  - Q2: Where's the time spent? (Need profiling — `time` per stage at minimum.)
  - Q3: What's "fast enough"? (10% improvement worth shipping? 2x?)
  - Q4: Are there parts I can't change? (External APIs, manual gates?)

Constraints:
  - Idempotency must hold (deploy.sh can run multiple times safely)
  - No new dependencies without explicit approval
  - Must work on the same hosts (no infra changes)

Out of scope: the parts that are slow because of network/external APIs we
don't control.
```

The user's request is "make it faster"; the SPEC turns this into measurable,
falsifiable work.

## When the user is in the room

If the user is interactive: surface 1-3 specific questions where the answer
significantly changes the implementation. Don't ask 10 questions; pick the
ones with the biggest impact.

Format:
> "To make sure I build the right thing: <Q1>? <Q2>? My default is <X> if
> you don't have a preference."

The "default if you don't have a preference" framing lets the user say
"sounds good" and move on, OR redirect — both fast.

## When the user is not in the room

If you're working autonomously (background agent, scheduled run): write your
assumptions explicitly into the spec. Then proceed. The PR description / done
summary should re-list the assumptions so the human reviewing knows what
choices you made.

## Anti-patterns

- **Diving in.** "Let me just write a quick prototype" — you'll commit to
  the wrong shape and have to undo it.
- **Asking too many questions.** Three questions is engaged. Ten is
  paralysis.
- **Implicit assumptions.** "I assumed UTF-8" — fine, but write it down so
  the reviewer knows.
- **Treating the spec as immutable.** If you discover during implementation
  that the spec was wrong, update the spec FIRST, then continue. Don't
  silently drift.

## Done criteria

You've got a usable spec when:
- [ ] One-line summary fits in a sentence
- [ ] Every parameter has a type
- [ ] Return value (success and error paths) is defined
- [ ] Edge cases enumerated (empty, max, malformed, duplicate)
- [ ] Out-of-scope list is non-empty (you've said "not this")
- [ ] Open questions either answered (with "default: X") or surfaced to user

Now write the function signature. Then implement.
