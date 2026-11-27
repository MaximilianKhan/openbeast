# Why DeepSeek-V4.1-Flash (Mia EXL3) usually fails 168_linearizability_kv (2026-11-19)

Kept under `evals/audit/` on purpose: it describes the unit's intended
algorithm and the reference's decoys, and `evals/audit/` is sealed from the
measured agent (`agents/tools.py`). Do not move it to `docs/`.

## Verdict

The model can solve it. When it picks the right design it passes (T_s1 kv_a:
first draft correct and fast at 1,182 s, 40/40 in 0.54 s CPU; T2 kv_e
passed). Two compounding causes make it fail most of the time:

1. **Orientation, not knowledge.** Only a per-key search memoised on
   (placed set, value) passes. Linearizability is local (Herlihy & Wing), so
   keys can be checked independently. The obvious alternative, a memoised
   global search, is the reference's planted `slow_global` decoy and must
   time out (measured: 13 of 40 histories in 20 s CPU). Design predicted
   every outcome: 6 of the 8 solution files DeepSeek wrote used the global
   search and all 6 failed (CPU or build). Of the 3 per-key files, 2 passed;
   the third was a Zig bug written 73 s before the wall. The passing kv_e
   file cites "per-key decomposition (linearizability is P-compositional)",
   so the fact is known. A live probe with a general "decompose into
   non-interacting parts" checklist reached the idea and then rejected it:
   *"Decompose by key? But completed operations across keys? Real-time
   constraints across keys couple order."* That is exactly the doubt the
   locality theorem settles. Once committed, it anchors: T2 kv_a measured
   22.7 s and 10.9M nodes on one history and kept optimising the global
   search.
2. **Thinking stalls.** T (16K cap): 314 of 360 unit-minutes went to turns
   capped with only reasoning; 4 of 6 units never wrote a file. T2 (8K cap):
   247 of 360. The text act nudge fired up to 11 times in one unit and was
   ignored every time: with thinking on the model always thinks first and
   hits the cap again. Each cap discards about 90% of the reasoning (about
   65K characters generated, about 6K of tail handed back), so the
   global-versus-per-key decision is re-made from scratch each turn.

Implementation bugs are secondary: T2 kv_e's first drafts had 6 and 10 wrong
verdicts, which its own brute-force stress test caught. The time budget is
not binding alone: the right first design passes in about 20 minutes.

## Spec and validator: fair, no change recommended

The six variants are one task in six languages (Python, Go, C, C++, Rust,
Zig) sharing one hidden input of 40 histories. The largest histories have
2,000 operations over 26-30 keys and 37-45 pending operations; the busiest
key holds about 200-260 operations with at most 6 pending. The CPU limit is
12 s at wall_scale 2; the reference Python takes 0.5 s (about 24x margin).

The reference groups operations by key, drops pending GETs, and runs a DFS
over (bitmask placed, current value) with a visited set, trying only
operations invoked at or before the earliest response among unplaced
completed operations. The proof requires `slow_global` and `slow_nomemo` to
time out, and `wrong_tie`, `wrong_nopend` and `wrong_memoset` to give wrong
verdicts. So the unit tests exactly two ideas: per-key decomposition and
memoisation on (set, value).

The semantics are precise, and "at most 64 distinct keys" hints at splitting
by key. The global bound "at most 12 completed operations in progress" is a
fair lure (it makes a global search look small), not an ambiguity. Opus 5.5
took 720-1,317 s on these units.

## Per-unit evidence

| Arm/unit | Capped turns (min) | First write (s) | Design | Result |
|---|---|---|---|---|
| T a-e | 5-6 each (48-54) | a: 3122; b-e: never | a: global | a: CPU timeout; b-e: no file |
| T f | 6 (57) | 3512 | global | build fail |
| T2 a/b/c/d | 6-9 (28-44) | 1178-2491 | global | CPU timeout |
| T2 e | 10 (49), 8 nudges | 2668 | per-key | pass |
| T2 f | 12 (57), 11 nudges | 3527 | per-key | exit 1 (bug) |
| T_s1 a | 4 (35) | 1182 | per-key | pass |

No unit called `task_done`; all ran to the 3,600 s wall, the passes included.
The T2 integrity JSON maps 168_a to two transcripts (one of them T_s1's);
mapping by the `Create /tmp/eval_linz/linz.<ext>` line is reliable.

## Gate probe (3 requests, C variant, "state the algorithm and bound; no code")

1. **Thinking on, no hint** (5,000-token cap): all reasoning, no answer.
   Stuck in the global search, worried about 2^48 subsets; never raised
   splitting by key.
2. **Thinking on, plus a general checklist:** reached "decompose by key",
   then rejected it (quoted above). A general "decompose" hint alone is not
   enough.
3. **Thinking off, no hint:** answered in 26 s with a confident global-search
   plan. A thinking-off turn fixes the stall but not the design choice.

## Remedies (general only; nothing specific to 168)

1. A system-prompt or pack line: when a simplification would make the
   problem tractable but you doubt it is sound, implement it and test it
   against a brute force on many small random inputs before rejecting it.
   Pair it with "bound first" and "redesign on a >5x miss".
2. A forced-act turn: after 2 capped thinks, the next request goes with
   thinking off, plus a decisions summary.
3. Done when verified: call task_done once your own checks pass.
4. Log the reasoning text of capped turns.
5. Stop advertising web_search under eval, where it is disabled.

Not acceptable for the scored suite: any 168-specific hint, a fact sheet
stating that linearizability is local, or rewording the spec.

A general algorithmic-procedure pack (bound first, decompose, test
simplifications against a brute force, memoised DFS over ordering states,
standard reductions, two-stage verification, redesign on a 5x miss) is
legitimate only if it is frozen before measurement, names no task, and is
reported per unit with a no-harm check on the other 65 units and a GLM
control.

Next arms built on this: U (forced-act + done-when-verified), UP (+ planning
lines), UPA (+ frozen pack), and a UP control on GLM-5.3-Flash.
