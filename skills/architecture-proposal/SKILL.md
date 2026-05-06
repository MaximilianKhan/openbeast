---
name: architecture-proposal
description: Produce a design doc BEFORE writing code for any non-trivial change — new subsystems, public API changes, anything >500 lines or crossing module boundaries. Activate when the user asks for a design, when you find yourself about to invent a non-trivial structure on the fly, or when "let me just start coding" would be premature.
allowed_tools: [read_file, grep, list_files]
recommends_subagent: true
---

# Architecture proposal

Frontier models pause and design before writing non-trivial code. Local models
often dive in, end up with the wrong abstraction, and then have to refactor
twice. The discipline is to **write the design first** — short, structured,
explicit about alternatives.

This isn't an essay. It's a 1-3 page doc that lets you (and reviewers) make
informed choices before any code commits to a direction.

## When to apply

ALWAYS for changes that meet ANY of:
- New subsystem / module / package
- Public API change (signature, behavior, or contract)
- Refactor crossing >2 files or >300 lines
- Anything you'd hesitate to call "obvious"
- Anything where you can imagine 2+ reasonable approaches

SKIP for:
- Bugfixes (use `debugging-methodology` instead)
- Small features that fit in one file
- Mechanical refactors (rename, move, formatter)

## The proposal template

Five sections. Total length: half-a-page to two pages depending on scope.

### 1. Motivation (why this change is needed)

What problem are we solving? What's the cost of not solving it? Who's
affected (users, callers, ourselves later)?

If you can't articulate why, stop — you might be solving a symptom or a
non-problem.

### 2. Goals + non-goals

**Goals:** specific, measurable. "Reduce P99 deploy time from 12 min to
< 5 min." "Add a /todos POST endpoint that supports the workflow in
ticket #4521."

**Non-goals:** what this change explicitly does NOT do. "Doesn't change the
deploy auth model." "Doesn't add support for nested todos."

The non-goals list is your scope guardrail. Reviewers will ask "what about
X?" — non-goals lets you say "out of scope; tracked as #XXXX" without scope
creep.

### 3. Design (the proposed approach)

The actual structure. Length scales with complexity:
- Simple change: 5-10 sentences + maybe a code sketch
- Complex change: 1-2 pages including diagrams, sequence flows, interface
  signatures

What to include:
- **Interface**: the public API as the user / caller sees it. Function
  signatures, REST routes, CLI flags, message schemas.
- **Data flow**: what state lives where, who owns it, how it changes.
- **Failure modes**: what can go wrong and how the system responds (errors,
  retries, degraded mode).
- **Boundaries**: what's inside this change and what's existing /
  unchanged.

Include code sketches for non-obvious internal structures. Half a page of
pseudocode beats two pages of prose.

### 4. Alternatives considered

This section is the most-skipped and the most-valuable. List 2-3 alternative
approaches and explain why you rejected them.

- **Alternative A:** <description> — Rejected because: <reason>
- **Alternative B:** <description> — Rejected because: <reason>
- **Alternative C: do nothing.** What happens if we don't make this change?
  Often illuminating — sometimes "do nothing" is actually best.

The reviewer's first question is always "did you consider X?" Answering it in
the doc saves a round trip.

### 5. Risks and rollout

What could go wrong with the implementation? Specifically:
- **Migration risks**: existing data, existing callers — what if they're
  incompatible?
- **Performance risks**: does this introduce new hot paths or bottlenecks?
- **Operational risks**: what new things have to be monitored?
- **Reversibility**: if this turns out wrong, can we undo it? At what cost?

Rollout plan:
- **Phased**: feature flag, gradual ramp, fallback path?
- **All at once**: when, with what observability, with what rollback plan?

If the rollout is non-trivial, this section may be the longest in the doc.

## Examples

### Small feature (half-page proposal)

```
PROPOSAL: Add POST /todos endpoint for creating todos

Motivation: Ticket #4521 — frontend needs to create todos via API; currently
only GET is exposed.

Goals:
  - Accept JSON { "title": str (required), "description": str (optional) }
  - Return 201 with the created todo (id auto-assigned, completed=false)
  - Persist to existing todos table

Non-goals:
  - Bulk create
  - Nested todos / parent-child
  - Multi-user sharing (single-user app)

Design:
  POST /todos
  Body: {"title": str, "description"?: str}
  Response 201: full Todo object
  Response 400: validation error

  Implementation: extend existing Flask app. New handler in app.py:
    @app.route('/todos', methods=['POST'])
    def create_todo(): ...
  Reuse the existing in-memory todos list + auto-incrementing id counter.

Alternatives considered:
  A: Use a separate /api/v1/todos namespace — rejected, the app doesn't
     version routes elsewhere; consistency wins.
  B: Add a CLI / script instead — rejected, the spec says "frontend needs",
     so HTTP is required.

Risks:
  - In-memory storage means todos are lost on restart. Pre-existing
    limitation; not worse with this change.
  - No auth on the endpoint. Pre-existing limitation; non-goal here.

Rollout: deploy with the rest of the next release. No flag needed (additive,
no behavior change for existing routes).
```

That's enough doc for a 30-line change. Don't write more than the change
deserves.

### Larger change (1-2 page proposal)

For a change like "introduce a job queue" or "split the monolith into two
services," the same five sections apply but each grows. Include diagrams
(ASCII or referenced images), sequence flows for the critical paths, and
explicit data ownership.

## Anti-patterns

- **Skipping the alternatives section.** Without alternatives, you haven't
  shown your work. Reviewers can't trust the recommendation.
- **Hiding risks.** "What could go wrong" should be candid, not corporate.
  If migration is scary, say so plainly.
- **Over-designing.** A two-page proposal for a one-day change is overkill.
  Calibrate.
- **Designing without reading existing code.** Pair with `codebase-onboarding`
  first — your design should match the project's existing patterns where
  possible.
- **Implementing while writing the proposal.** No. Finish the proposal
  first; then implement. If during implementation the design proves wrong,
  update the proposal — don't silently drift.

## Working with humans

If the user is interactive: post the proposal and explicitly ask "any
concerns or alternatives I missed before I start coding?" One round of
feedback at the design stage is worth ten rounds at the code stage.

If working autonomously: write the proposal as a comment / doc / commit
message, proceed to implementation, and reference the proposal in the PR
description. Reviewers can challenge the design and the implementation
independently.

## Pairing with other skills

- `spec-extraction` produces the inputs to this skill — once you know what
  to build, this skill decides how.
- `codebase-onboarding` informs the "match existing patterns" choices.
- `test-driven-development` operationalizes the design once approved.
- For ambiguous direction, spawn `deep-counsel` BEFORE writing the proposal —
  the war council can identify which problem you should actually be solving.

## Done criteria

A good proposal:
- [ ] Five sections present and non-empty
- [ ] Goals and non-goals are specific (not "make it better")
- [ ] At least 2 alternatives considered with reasons for rejection
- [ ] Risks named candidly
- [ ] Rollout plan matches the change's blast radius
- [ ] Length proportional to scope (don't pad; don't underspec)
