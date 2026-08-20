# A faster eval suite — measured proposal (2026-08-19)

Target: run the capability eval in **1/3 to 1/4** of current wall-clock.

Everything below is measured against the **7 completed v4 runs** in
`evals/results/`, not estimated. The headline finding is that the obvious
levers (drop easy questions, cut languages) do **not** get there, and one
unglamorous lever gets there comfortably with zero loss of fidelity.

---

## 1. Where the time actually goes

Reference run: `qwen-27b-mtp-q5-k-xl`, 291 units, 3.83 h.

| Tier | Units | Runtime | % of run | Avg/unit | Pass rate |
|---|---|---|---|---|---|
| easy | 64 | 0.25 h | **6.5%** | 14 s | 93.8% |
| medium | 123 | 1.20 h | 31.4% | 35 s | 93.5% |
| hard | 104 | 2.38 h | **62.2%** | 82 s | 94.2% |

**Dropping the entire easy tier saves 6.5% of runtime.** Easy questions are
cheap precisely because they are easy. Cutting them is nearly free — and
therefore nearly pointless as a speed lever.

~80% of wall-clock is token generation (1.75M completion tokens at the model's
decode rate). Failures are *not* the problem: 18 failed units cost 15% of the
run. The cost is 273 *passing* units at ~5,600 completion tokens each.

## 2. The suite is saturated

Across all 7 v4 models, counting how many passed each unit:

| Models passing | Units | |
|---|---|---|
| 7 / 7 | **202** | zero signal — everyone passes |
| 1–6 / 7 | 86 | discriminating |
| 0 / 7 | 3 | zero signal — nobody passes |

**205 of 291 units (70%) cannot distinguish any two models we have ever run.**
Pass rates are ~94% in *every* tier including hard. The suite is not measuring
much at the top of the range any more.

But note the trap: those 205 units burn only **42%** of runtime. The
discriminating 86 units burn **58%**. *The signal lives in the expensive
units.* This is why pure question-cutting cannot hit 4×.

## 3. Language findings — this overturns the stated assumption

Max's guess was that Zig and Rust might be the droppable pair. The data says
the opposite for Zig:

| Language | Units | Discriminating | Rate |
|---|---|---|---|
| **zig** | 31 | **29** | **94%** ← most valuable in the suite |
| go | 31 | 14 | 45% |
| c | 31 | 7 | 23% |
| cpp | 31 | 6 | 19% |
| python | 136 | 25 | 18% |
| **rust** | 31 | **5** | **16%** ← least valuable |

**Zig is the single most discriminating language we test** — it is where models
actually separate, presumably because it is least represented in training data.
Rust is nearly inert: 5 of 31 units tell us anything. If a language pair must
go, the evidence says **rust + cpp**, and keep zig.

## 4. Candidate suites, ranked by measured fidelity

Ranking fidelity = does the reduced suite reproduce the full suite's model
order? Kendall's τ against the full-v4 ranking across the same 7 models.

| Suite | Units | Hours | vs full | τ | Order preserved |
|---|---|---|---|---|---|
| A — full v4 (baseline) | 291 | 5.58 | 100% | +1.000 | — |
| B — drop easy tier | 227 | 5.29 | 95% | +0.810 | ✗ |
| C — drop zig+rust *(the guess)* | 229 | 3.56 | 64% | +0.905 | ✗ |
| D — drop rust+cpp+c | 198 | 3.87 | 69% | +0.810 | ✗ |
| **E — discriminating only** | **86** | 3.22 | 58% | **+1.000** | **✓** |
| **F — E + 20 tripwires** | **106** | 3.41 | 61% | **+1.000** | **✓** |
| G — E capped at 120 s/unit | 61 | 0.74 | **13%** | +0.619 | ✗ |
| H — hard+medium discriminating | 71 | 3.08 | 55% | +0.810 | ✗ |

Read carefully: **B, C, D and H all break the ranking.** Dropping easy
questions or cutting zig/rust changes who wins. G hits the speed target and
destroys fidelity — more evidence that the slow units carry the signal.

Only **E and F reproduce the leaderboard exactly**, and they cap out at ~1.6×.

## 5. The lever that actually gets to 4×

**`evals/run_eval.py` has no concurrency at all** — no `--jobs`, no thread
pool, no asyncio. It runs 291 units strictly sequentially.

Meanwhile `serve.sh` ships **6 parallel slots** with a unified KV cache and
continuous batching. The eval has been leaving 5 of 6 slots idle for its entire
existence.

Projected, on the MTP-equivalent config (suite F, 70% scaling efficiency —
conservative, since batching usually does better on a 32 GB card):

| Configuration | Hours | Speedup |
|---|---|---|
| full v4, sequential *(today)* | 3.83 | 1.0× |
| suite F, sequential | 2.37 | 1.6× |
| **suite F, 4-way parallel** | **0.85** | **4.5×** |
| suite F, 6-way parallel | 0.56 | 6.8× |

Parallelism costs **zero** fidelity — the question set is untouched, so scores
remain directly comparable. It is the only lever here with no accuracy tradeoff.

One constraint: **MTP forces `-np 1`**, so a parallel harness must run against
`serve-qwen38-27b-uncensored-q5.sh` (non-MTP, 6 slots). Single-stream is half
the tok/s, but 6 concurrent streams beat 1 fast one by a wide margin — and
suite F on the *non-MTP* 27B was 4.91 h sequential, so 4-way lands near 1.75 h,
still a 2.2× win over today's fastest.

## 6. Recommendation

Two suites, not one — because the saturated units still have a job:

- **`v5-fast` (suite F, 106 units)** for routine model comparison. Verified to
  reproduce the v4 leaderboard order exactly. With a `--jobs 4` harness this is
  **~0.85 h, comfortably inside the 1/4 target.**
- **`v4-full` retained** for new model *classes*. The 202 all-pass units are
  saturated *against 27B–35B Qwen variants specifically*; a weaker or
  differently-shaped model would fail plenty of them. Dropping them permanently
  would make the suite blind to regressions and unable to score anything below
  the current tier.

Order of work, highest value first:

1. **Add `--jobs N` to `run_eval.py`.** ✅ **DONE 2026-08-20.** Biggest win,
   zero fidelity cost, helps every future suite. Isolation is by *scheduling*,
   not per-worker workspaces: every `/tmp/eval_*` fixture dir belongs to
   exactly one task file (verified, and now enforced by
   `tests/test_eval_jobs.py`), so units are grouped by base task — variants
   serialize on one worker, distinct tasks parallelize. Prompts, validations,
   and cache keys are untouched, so parallel scores stay comparable to every
   sequential run. `--jobs` is clamped to the server's `/props total_slots`
   (MTP = `-np 1` clamps to sequential). Plumbed through `benchmark_all.py`.
2. **Generate `v5-fast` from the discrimination analysis** and pin the unit list
   in `evals/` so it is reproducible rather than recomputed.
3. **Re-examine the language mix on evidence** — add zig variants, consider
   retiring rust/cpp ones. Do this *after* 1 and 2, and re-verify τ.

Do **not** simply drop the easy tier: it buys 6.5% and costs ranking fidelity.

## 7. Caveats

- 7 models, all Qwen 27B/35B variants. "Zero signal" means *within this
  family*. A Gemma or Llama entrant could light those units up.
- The 4×/6.8× figures are projections at an assumed 70% scaling efficiency.
  The 1.6× from suite F is measured; the parallel multiplier is not. Measure it
  on a real `--jobs` run before quoting it as fact.
- Ranking fidelity was checked on difficulty-weighted pass rate, not the full
  v2 capability metric (`0.75·problem_solving + 0.25·language_breadth`).
  Re-verify τ against `scoring.py` output once the subset is pinned.
