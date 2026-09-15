# beast-campaign — the GPU campaign runner (design, 2026-09-15)

A campaign is a sequence of GPU measurements that has to run for hours or days
without supervision and produce rows a paper can cite. OpenBeast runs them
today out of `scratch/`: 24 shell scripts, ~400 lines across the five that
matter, rewritten by hand for each campaign.

**Every campaign failure this month was an orchestration failure, not a
science failure.** That is the case for this feature, and it is the whole case
— so it is worth being specific rather than persuasive.

---

## 1. The evidence

| date | what happened | what it cost |
|---|---|---|
| 09-11 | `setsid`-wrapped stages forked, so every recorded pid was a dead parent and all waiters fired at once | PR #59 merged early, E16 rung 1 completed, the rest of the queue killed |
| 09-14 | six parallel build agents exhausted OpenBLAS's thread budget *inside* row B's window | 5 eval units contaminated, asymmetrically, in the direction that flattered the result; a repair pass and a re-run |
| 09-15 | campaign logs lived in `/tmp`; a hard power-off erased them | the CUDA-error axis of a 19-hour paired measurement became unauditable |
| 09-15 | the per-row validity guard was *written but broken* — `crashes=$(grep -c … \|\| echo 0)` yields `"0\n0"`, and its zero-token half was never implemented at all | a row was called INVALID for a day on evidence nobody had checked; it was valid, and re-running it would have burned ~19 GPU-hours |
| 09-15 | stopping cleanly, handing the GPU back, and recording how to resume were all written by hand, live | ~40 minutes of operator time, and the resume instructions exist only because someone remembered to write them |

Five distinct classes. Not one of them is about quantization, perplexity or
capability. They are all *the runner*: process supervision, concurrency,
durability, validation, and lifecycle.

A sixth, from 09-08: the *previous* plan scheduled the greedy churn floor
before a `git pull` and the Tier-3 cells it calibrates after it — a latent
cross-era mismatch that survived review because era is a property nothing
enforced.

---

## 2. What a campaign actually is

Reading the five scripts back, every one of them is the same five things,
hand-rolled differently each time:

1. **Stages with dependencies**, run sequentially because they share one GPU.
2. **One cache era across the whole campaign** — rows measured either side of
   a change to the six hashed files are not comparable, and nothing currently
   checks.
3. **An exclusive GPU lease** — a stage must not start while another holds the
   card, and *nothing else on the box* may compete for the resources a
   measurement needs (which is how six build agents contaminated a row).
4. **Durable, per-stage logs** with the provenance to audit a row afterwards.
5. **A validity verdict per row**, computed while the evidence still exists.

Plus lifecycle: resumable after a stop, cancellable at a boundary, and
self-documenting about how to resume.

None of that is research-specific. All of it is exactly what the five failures
above were.

---

## 3. Design

### The campaign is a file, not a script

```toml
# campaigns/t117-capability.toml
name    = "T1.17 capability — IQ3 pair"
era     = "3b7c2adb8da7968d"        # refuse to run if the tree hashes differently
exclusive = "gpu"                    # one lease, held for the whole campaign

[[stage]]
id      = "tripwire-repair"
run     = "scratch/patchup_tripwires.sh"
expect  = ["research/.../capability-verdict-iq3-final.txt"]

[[stage]]
id      = "greedy-floor"
needs   = ["tripwire-repair"]
rows    = 2                          # this stage produces 2 eval rows
validate = "row"                     # stamp each one at exit

[[stage]]
id      = "iq2-pair"
needs   = ["greedy-floor"]
last    = true                       # Max's standing order: IQ2 last
```

The runner owns what the scripts keep re-implementing:

- **Sequential by default, one process.** No `setsid`, no pid chaining, no
  `$!` recorded for a process that already forked. The 09-11 failure is
  structurally impossible because there is nothing to chain.
- **Era assertion at start and before every stage.** `era = "<hash>"` is a
  precondition, not a comment. A campaign whose tree no longer hashes to its
  declared era refuses to start and says which of the six files moved. The
  09-08 latent mismatch becomes an error message.
- **A real GPU lease.** A lockfile with the holder's pid *and start time*
  (pid alone is not identity — that lesson is already in
  `agents/sessions.py`), plus a pre-flight that refuses to start when VRAM is
  held by anything else.
- **A concurrency budget.** The campaign declares what it needs (threads,
  processes) and the runner refuses to start a stage while the box is over
  budget — and, critically, **exports that budget** so an agent or a human
  can ask "may I run six builds right now?" and be told no. The 09-14
  contamination was invisible because nothing published the answer.
- **Logs under `scratch/logs/<campaign>/<stage>/`**, never `/tmp`, with the
  stage's command, era, GPU state and toolchain provenance written *before*
  the stage runs.
- **Row validation as a first-class step**, calling the same
  `row_validity.py` the eval harness uses, at stage exit, while the serve log
  still exists. Not a `grep` written fresh per campaign.
- **Resume and cancel.** State in `scratch/logs/<campaign>/state.json`:
  which stages completed, with which era, and what remains. `--resume`
  continues; `--stop-after-stage` ends at a boundary rather than mid-row; and
  the state file *is* the resume instruction, so it cannot be forgotten.

### What it does not do

- **Not a scheduler.** One campaign at a time, one GPU, sequential stages.
  Parallelism across stages is how row B got contaminated; the runner's job is
  to *prevent* that, not to offer it.
- **Not a workflow engine.** No DAG UI, no retries-with-backoff, no
  distributed anything. Stages are shell commands; the value is in the
  guarantees around them.
- **Not a replacement for the stage scripts.** `tier3_zig_ab.sh` and friends
  stay — they encode real experimental design. What moves out of them is the
  plumbing they should never have owned.

---

## 4. Why this and not a general job runner

Because the guarantees are domain-specific and that is the whole point. A
generic runner does not know what a cache era is, cannot refuse to start when
the tree hashes differently, has no opinion about VRAM, and will happily run
two stages at once. Every failure above needed one of those.

It also composes with what shipped this week: a campaign registered through
`scripts/job.sh` is already watchable and steerable from a phone via
beast-chat, so `beast-campaign` inherits observability for free rather than
building a second one.

---

## 5. Phases

**P0 — the lease and the era assertion (hours).** The two guarantees that
prevent the two worst failures. Usable immediately from the existing scripts:
`beast-campaign lease --exclusive gpu -- bash scratch/whatever.sh`.

**P1 — the spec file and the sequential runner (days).** Parse, validate,
run, log, record state. Port `campaign_master2.sh` as the first real campaign
and delete it.

**P2 — row validation and the concurrency budget (days).** Stamp every row at
stage exit; publish the budget so nothing else on the box can contaminate a
measurement in ignorance.

**P3 — resume, stop-at-boundary, and the verdict hook (days).** Including
folding the four bespoke verdict scripts into one paired-stats module — that
is where the zero-token classifier bug lived, and the same classifier
currently exists in two places with a comment telling the reader to change
both.

---

## 6. Open questions

1. **TOML or JSON for the spec?** The repo has no TOML dependency today;
   JSON needs no new dependency and the eval harness already speaks it.
   Recommendation: JSON, with the comment discipline the claim sets use
   (`_comment` keys).
2. **Does the lease belong to beast-campaign or to the stack?** A GPU lease is
   useful to anything that loads a model — `serve.sh`, the eval harness, a
   research script. Recommendation: `scripts/lib/gpu-lease.sh`, owned by the
   stack, *used* by the campaign runner.
3. **How much of `campaign_master2.sh` should survive the port?** It is 113
   lines and about 70 of them are commentary explaining failures the runner
   would make impossible. Recommendation: port the stage list, keep the
   commentary in this document, delete the script.
