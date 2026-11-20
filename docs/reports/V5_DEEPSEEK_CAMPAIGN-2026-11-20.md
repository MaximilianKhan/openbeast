# v5 campaign report: DeepSeek-V4.1-Flash (Mia EXL3) on the DGX Spark pair (2026-11-20)

> **Interim (UP running).** Arms T, T_s1, T2, T3 and U are finished. UP was
> in progress when this was written: 37 passed of 38 units done (snapshot of
> 2026-11-20 09:23 PST). UPA follows UP; U_8k, UP_8k and UPA_8k are queued
> after a gap. The final report replaces this one when the series ends
> (`evals/campaign.py report`, [MODEL_CAMPAIGN.md §6](../MODEL_CAMPAIGN.md#6-comparison)).

**Regenerate the tables.** Every number in the tables below comes from
result files, through [`evals/v5_report.py`](../../evals/v5_report.py). That
script scores the way [`evals/v5_board.py`](../../evals/v5_board.py) does,
exclusions included. It prints unit ids and verdicts, never task or
validator text, and writes nothing except, with `--write`, the generated
block of this file. The board arms are committed in
[`evals/boards/v5/`](../../evals/boards/v5/). U and UP are not on the board
yet, so their files come from the harness host's untracked `evals/results/`:

```bash
python3 evals/v5_report.py \
    --arm U=evals/results/eval-deepseek-v4-1-flash-v5-thinking-first-u-20261119-235955.json \
    --arm UP=evals/results/eval-deepseek-v4-1-flash-v5-thinking-first-up-20261120-063508.json
python3 evals/v5_report.py --json        # the same, machine-readable
# the same --arm flags plus --write docs/reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md
# replace the block between the v5-report markers in §4
```

Once an arm joins the board (`evals/v5_board.py --add`), drop its `--arm`.
Without those files (a fresh clone, offline), the script prints the board
arms alone.

---

## 1. Executive summary

- **DeepSeek-V4.1-Flash (Mia EXL3) is the strongest local model measured on
  v5: 65/71 (SCORE 94.9) under arm T2.** T2 is the composite harness with an
  8,192-token per-turn thinking cap and an act nudge. It is the v5 board's
  #1 and the model's default setup, `AGENT_PROFILE=deepseek-best`
  ([SPARK_HYDRA.md E2b](../SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2)).
  For comparison, GLM-5.3-Flash's best is 51/71 scored (54 raw) and the
  Claude Opus 5.5 reference is 71/71 under a different scaffold.
- **The ladder so far:**

  | Arm | Change | Passed | SCORE |
  |---|---|---:|---:|
  | T | the composite, thinking on, 16K cap | 61/71 | 88.5 |
  | T_s1 | T under seed 1 | 61/71 | 85.7 |
  | T2 | T + 8K cap + act nudge | **65/71** | **94.9** |
  | T3 | T2 at a 4K cap | 65/71 | 91.5 |
  | U | T2's switches with the forced-act turn, done-when-verified, no web tools, at a 16K cap | 59/71 | 85.5 |
  | UP | U + three planning lines | in progress: 37 of 38 done | — |

- **Noise is about ±1 unit.** T and T_s1 both pass 61, swapping one unit
  each way. SCORE moves about 3 points on that swap, so read units first.
- **The per-turn thinking cap is the lever that moves this model.** 16K → 8K
  gave +4 with no regressions. 8K → 4K was a wash: 65 again, three units in
  and three out, and SCORE fell from 94.9 to 91.5, so 8K stays. U went back
  to 16K and lost 6 units against T2. Every unit U lost ran out of time or
  turns, and four of them did so in far fewer turns than T2 used. The cap,
  not the forced-act turn, cost U. The forced-act turn itself worked as
  designed: all 24 forced turns in U ended in a tool call.
- **What remains is design choice and time, not knowledge.** T2's six
  failures are five variants of one task family (168) and 192_pike_regex.
  Across all five finished arms, only three units (168_linearizability_kv_b,
  168_linearizability_kv_f, 192_pike_regex) were never passed. The 168
  investigation found a design-choice failure, not a knowledge gap, and its
  remedies are general (§6.4).
- **Integrity: every DeepSeek run was scanned, 0 contaminated after
  review.** The sandbox was hardened in review 1118, and every leak found
  since has been fixed and its material sealed under `evals/audit/` (§3.5).
- **Hy4-flash** is staged as NVFP4 on vLLM across both Sparks, with arm
  H_T2 (T2 exactly) ready. The EXL3 pack we downloaded cannot span two
  Sparks and is kept on purpose. **Native TensorFold 1.2.10** is installed
  on both Sparks and has no effect on DeepSeek, which serves from its own
  recipe engine (§7).

## 2. Setup

### 2.1 Hardware and serving

| Piece | What |
|---|---|
| Inference | Two NVIDIA DGX Sparks (spark-1, spark-2) as one tensor-parallel pair, one rank per Spark over two RoCE rails ([SPARK_HYDRA.md](../SPARK_HYDRA.md)) |
| Model | DeepSeek-V4.1-Flash (Mia EXL3), served as `DeepSeek-V4.1-Flash-1017`: the stock Mia EXL3 2.9 bpw pack, TP2, 524,288-token window, 4 streams (`deepseek-v41-inference` unit; [DeepSeek deployment](../SPARK_HYDRA.md#deepseek-deployment)) |
| Engine | TensorFold in the image built from Mia's two-Spark recipe at `c7f2b85` (`tensorfold-dsv41:exl3-tp2-8a98ae503d85`), a forked engine separate from the native `~/.local/bin/tensorfold` (§7) |
| Front door | beast-gate on spark-1, which every eval request goes through (`https://spark-1…:8443/v1`). `--served-model DeepSeek-V4.1-Flash-1017` is checked at the start of each run and around every unit, so a swapped model stops the run |
| Harness host | The Mac. The agents' tools, compilers and validators run there. Only chat calls go to the Sparks ([MODEL_CAMPAIGN.md](../MODEL_CAMPAIGN.md)) |
| Decode | About 30 tok/s per stream with 4 streams (measured during arm T; `evals/campaigns/v5-thinking-first.json`, arm T_j2 note). A capped 16K thinking turn therefore takes about 9 minutes, an 8K one about 4.5 |

### 2.2 The harness

Every DeepSeek arm builds on **T**, the thinking-first composite
([MODEL_CAMPAIGN.md §5b](../MODEL_CAMPAIGN.md#5b-the-arms)):

- runner v2 and wall-scaled turns (G1);
- thinking on, at `--wall-scale 2`;
- push diagnostics, the edit hint and verify-done (G13, G14, G9);
- zig pack v3 and the host pack (G19, G20);
- the environment block and wall warnings (G22, G23);
- the capped-think recovery: a turn cut off by the cap hands its reasoning
  tail back ([HARNESS_NOTES.md](../HARNESS_NOTES.md#thinking-stall-a-capped-think-keeps-its-tail-2026-11-17--adopted-pending-an-arm)).

The run settings are `--jobs 4 --slots 4`, `--no-cache` and the v5 manifest.
The unit wall is the task's own, scaled by `--wall-scale 2` and by `--jobs`
contention. Every wall-killed DeepSeek row in these arms died at 3,600 s.

## 3. Methodology

### 3.1 The suite and the score

v5.0 has 71 scored units: 34 task bases in Python (32 units), Go, C, C++,
Rust (8 each), Zig (6) and shell (1). Each base runs in one or more
languages. **SCORE** = 0.75 × SOLVE + 0.25 × LANG. SOLVE weights each base
by difficulty, and LANG covers only the multi-language bases
([`evals/README.md`](../../evals/README.md), "Scoring (v5)"). Two arms with
the same pass count can therefore differ in SCORE, as T and T_s1 do.

### 3.2 The arms

From [`evals/campaigns/v5-thinking-first.json`](../../evals/campaigns/v5-thinking-first.json).
Each arm changes one thing against the arm it is built from.

| Arm | Built from | What changes | Harness | Status |
|---|---|---|---|---|
| T | — | the composite (§2.2), 16,384-token cap | harness8 + review 1118 | done, 61/71 |
| T_s1 | T | `--seed 1` (noise) | harness9 | done, 61/71 |
| T2 | T | 8,192-token cap + act nudge (a text push to call a tool after capped turns) | harness9 | done, 65/71 |
| T3 | T2 | 4,096-token cap | harness9 | done, 65/71 |
| U | T2 | the forced-act turn replaces the nudge (after 2 capped thinking turns in a row, the next request goes with thinking off and must act), done-when-verified, web tools not advertised; cap back to 16,384 | harness10 | done, 59/71 |
| UP | U | + three general planning lines in the system prompt (they name no task) | harness10 | **running** |
| UPA | UP | + the frozen algorithmic procedure pack (`agents/packs/algo-v1.md`, sha256 pinned, names no task) | harness10 | queued (after UP) |
| U_8k, UP_8k, UPA_8k | U, UP, UPA | the same three at T2's 8,192 cap | harness10 | queued (after a gap) |
| H_T2 | T2 | T2 exactly, against Hy4-flash (NVFP4) | harness9 | staged (§7) |

The arms T_noturns, T_nopacks, T_noassist, T_noenv, N and N0 (the
ablations and the no-thinking twins) have not run for DeepSeek.

### 3.3 Seeds

A Spark server that gets no seed uses a fixed one, so every run is one
deterministic draw (G12). T, T2, T3, U and UP send no seed. T_s1 sends
`--seed 1`. Every arm runs at **one seed**. The only noise measurement is
T against T_s1 (§6.1). The campaign's decision rules use a noise SD of 1.0
(`recommend.noise_sd`).

### 3.4 Eras and harness versions

An eval **era** is a hash of every file that shapes the agent's environment:
the runner, the tools, the validator library, the system prompts, and the
suite and harness markers (`scripts/eval-era.sh`, `evals/cache.py`). Rows
from different eras are never mixed in a cache or replayed across a
change. A harness change that is itself the treatment moves the era, and
is then judged on a full re-run of all 71 units, never on a re-roll of
failed units. `HARNESS_VERSION` names the eras that changed run_eval's
agent-shaping code.

| Label | Era | What it added | DeepSeek arms |
|---|---|---|---|
| harness8 + review 1118 | `06c82c4335dd6a0c` | the sandbox hardening of review 1118 (§3.5) | T |
| harness9 | `036638ddd68698ce` | the act nudge and the per-turn cap as switches, partial stats on a wall kill, the eval fetch guard | T_s1, T2, T3 |
| harness10 | `9bcd5aa6030836d1` | the forced-act turn, done-when-verified, planning lines, the algorithmic pack, web tools not advertised, reasoning text logged | U, UP, UPA, U_8k, UP_8k, UPA_8k |

**Why arms are compared within an era.** Each harness version only adds
switches that are off by default. With a switch off, the request and the
prompt are as before ([`evals/CHANGELOG.md`](../../evals/CHANGELOG.md)).
Even so, the cleanest comparison changes one variable inside one era:
T2 → T3 (the cap alone, harness9), and U → UP → UPA, U_8k → UP_8k →
UPA_8k (harness10). Two comparisons in this report cross an era, and are
marked as such:

- **T → T2** crosses harness8 → harness9. T_s1 is T's configuration
  measured on harness9, and it scored the same 61. That is evidence that
  the harness9 infrastructure (partial stats, the fetch guard) did not move
  T's score, but it is confounded with the seed change.
- **T2 → U** crosses harness9 → harness10 and changes four things at once.
  The cap question inside harness10 is U_8k against U (queued).

### 3.5 Integrity

Every run is scanned by
[`evals/audit/integrity_scan.py`](../../evals/audit/integrity_scan.py). The
scan checks every tool call in every transcript for reads or listings of
what a measured agent must not see: a checkout's `evals/` and `tests/`,
other transcripts, Claude Code's journals and scratch, run ledgers, the
project's own repository on the network, and embedded absolute paths. A
unit with a successful read is CONTAMINATED and counts as not passed. A
refused attempt is recorded but does not contaminate.

| Run | Scan | Result |
|---|---|---|
| T | 2026-11-18 | 0 contaminated; no transcript reached the repo |
| T2 | 2026-11-19 | 0 contaminated after review: one flagged network attempt was blocked by the sandbox (review notes sealed in `evals/audit/reviews/`) |
| T_s1 | 2026-11-19 | 0 contaminated |
| T3 | 2026-11-20 | 0 contaminated |
| U | 2026-11-20 (for this report) | 0 contaminated (75 transcripts, 71 units) |
| UP (38 of 71 done) | 2026-11-20 (for this report) | 0 contaminated so far (42 transcripts) |

**What hardened the eval, in order.** Each step is in
[`evals/CHANGELOG.md`](../../evals/CHANGELOG.md):

1. **G25, #195 (2026-11-17).** In GLM-5.3-Flash's arm C, three units' agents
   reached sealed locations: Claude Code journals, the harness source and a
   cached result. The three passes are excluded on the board (51 scored,
   54 raw), and the seal was widened to every checkout's `evals/`,
   `tests/`, `agents/logs/`, `~/.claude` and Claude Code's scratch.
2. **Review 1118 (#224, #223; 2026-11-18).** The public repository, which
   holds the reference solutions, was clonable from the eval shell. The
   macOS eval sandbox now denies all outbound network except loopback. The
   diagnostics compilers and `grep` run under the same read-deny as `bash`.
   Writes to the repo, the eval venv and Python's own install are refused.
   The scan learned to flag network commands and embedded absolute paths.
   Separately, four validators' lint checks read comments or docstrings;
   they now lint code only. Earlier board runs carry a footnote on those
   four units, not adjusted (no board run failed any of them).
3. **harness9 (2026-11-18).** An eval fetch guard refuses any URL that
   names this project's repository or a checkout's answer trees, at every
   redirect hop, and the scan gained a `selfrepo` kind with the same
   patterns.
4. **Docs pass (2026-11-19).** Two items the measured agent could read gave
   eval help: a recipe in a skill, and old eval rows in `scratch/`. Both now
   live under the sealed `evals/audit/`.
5. **#244 (2026-11-19).** A board note, rendered into `README.md` and
   `docs/RESULTS.md` (both readable by the agent), named a detail of how
   one unit was solved. The note is now neutral, and the detail is sealed in
   `evals/audit/reviews/`. The scan of the next arm (T_s1) found no read.
6. **The 168 review (2026-11-19)** describes a unit's intended solution, so
   it was written straight into `evals/audit/reviews/`, never into `docs/`.
   This report gives only its high-level conclusion.

The rule this report follows: **`docs/` carries unit ids and verdicts, never
a unit's solution, its reference algorithm or its checker's specifics.**
The measured agent can read `docs/`.

## 4. Results

The tables below are the output of the command at the top, run on
2026-11-20 against the board files and the U and UP result files.

<!-- v5-report:start -->
<!-- generated by evals/v5_report.py for DeepSeek-V4.1-Flash (Mia EXL3): do not hand-edit -->

**Arms**

| Arm | Run | On the board | Era | Turn cap | Passed | SCORE | SOLVE | LANG |
|---|---|---|---|---:|---:|---:|---:|---:|
| T | DS-T | yes | 06c82c4335dd6a0c | 16384 | 61/71 | 88.5 | 84.6 | 100.0 |
| T2 | DS-T2 | yes | 036638ddd68698ce | 8192 | 65/71 | 94.9 | 96.9 | 88.9 |
| T_s1 | DS-Ts1 | yes | 036638ddd68698ce | 16384 | 61/71 | 85.7 | 84.6 | 88.9 |
| T3 | DS-T3 | yes | 036638ddd68698ce | 4096 | 65/71 | 91.5 | 90.8 | 93.9 |
| U | U | not yet | 9bcd5aa6030836d1 | 16384 | 59/71 | 85.5 | 81.5 | 97.4 |
| UP | UP | not yet | 9bcd5aa6030836d1 | 16384 | 37 of 38 done (in progress) | — | — | — |
| GLM-5.3-Flash: C | GLM-C | yes | 8eaa5e5f61638a1d | — | 51/71 (54 raw) | 76.7 | 75.4 | 80.5 |
| Claude Opus 5.5: OPUS-REF | OPUS-REF | yes | — | — | 71/71 | 100.0 | 100.0 | 100.0 |

**Per language** (passed / units of that language; an in-progress arm's passes are over the units done so far)

| Arm | Py | Go | C | C++ | Rust | Zig | Sh |
|---|---:|---:|---:|---:|---:|---:|---:|
| T | 27/32 | 7/8 | 7/8 | 7/8 | 7/8 | 5/6 | 1/1 |
| T2 | 30/32 | 7/8 | 7/8 | 7/8 | 8/8 | 5/6 | 1/1 |
| T_s1 | 27/32 | 7/8 | 7/8 | 7/8 | 7/8 | 5/6 | 1/1 |
| T3 | 28/32 | 7/8 | 8/8 | 8/8 | 8/8 | 5/6 | 1/1 |
| U | 26/32 | 7/8 | 7/8 | 6/8 | 7/8 | 5/6 | 1/1 |
| UP | 7/32 | 7/8 | 7/8 | 6/8 | 6/8 | 4/6 | 0/1 |
| GLM-5.3-Flash: C | 24/32 | 6/8 | 6/8 | 6/8 | 5/8 | 3/6 | 1/1 |
| Claude Opus 5.5: OPUS-REF | 32/32 | 8/8 | 8/8 | 8/8 | 8/8 | 6/6 | 1/1 |

**Time and the wall** (wall hit = the harness killed the agent at the unit's wall, `agent_exit_code == -1`; turns are counted only where the row carries them, from harness9 on)

| Arm | Units done | Unit-hours | Mean min / unit | Wall hits | Passed at the wall | Failed at the wall | Turns of the wall-hit units |
|---|---:|---:|---:|---:|---:|---:|---|
| T | 71 | 26.6 | 22.5 | 16 | 6 | 10 | — |
| T2 | 71 | 24.5 | 20.7 | 13 | 7 | 6 | 19-49 |
| T_s1 | 71 | 26.6 | 22.5 | 15 | 6 | 9 | 8-32 |
| T3 | 71 | 21.5 | 18.2 | 10 | 6 | 4 | 25-56 |
| U | 71 | 26.1 | 22.1 | 15 | 4 | 11 | 11-53 |
| UP | 38 | 9.9 | 15.7 | 3 | 2 | 1 | 15-27 |

**Units gained and lost** (arm against base, over the units both have a verdict for)

| Base → arm | Compared | Net | Gained | Lost |
|---|---:|---:|---|---|
| T → T_s1 | 71 | +0 | 168_linearizability_kv_a | 175_crdt_orset_rga |
| T → T2 | 71 | +4 | 168_linearizability_kv_e, 173_taskgroup_cancel, 183_heston_little_trap, 190_autograd_mlp | — |
| T2 → T3 | 71 | +0 | 168_linearizability_kv_a, 168_linearizability_kv_c, 168_linearizability_kv_d | 167_q15_biquad_a, 173_taskgroup_cancel, 183_heston_little_trap |
| T2 → U | 71 | -6 | — | 163_bigint_divmod_d, 168_linearizability_kv_e, 173_taskgroup_cancel, 175_crdt_orset_rga, 183_heston_little_trap, 190_autograd_mlp |
| U → UP | 38 | +1 | 168_linearizability_kv_a | — |

**Units whose verdict differs across the arms** (P pass, F fail, · not run yet, L lost to infrastructure); every other unit has the same verdict in every arm

| Unit | T | T2 | T_s1 | T3 | U | UP |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| 163_bigint_divmod_d | P | P | P | P | F | · |
| 167_q15_biquad_a | P | P | P | F | P | P |
| 168_linearizability_kv_a | F | F | P | P | F | P |
| 168_linearizability_kv_c | F | F | F | P | F | · |
| 168_linearizability_kv_d | F | F | F | P | F | · |
| 168_linearizability_kv_e | F | P | F | P | F | · |
| 173_taskgroup_cancel | F | P | F | F | F | · |
| 175_crdt_orset_rga | P | P | F | P | F | · |
| 183_heston_little_trap | F | P | F | F | F | · |
| 190_autograd_mlp | F | P | F | P | F | · |
<!-- v5-report:end -->

How to read the tables:

- **UP is partial.** Its per-language counts are passes among the units done
  so far, against the full denominators. Its gained/lost row compares only
  the 38 units it has finished.
- **No unit was lost to infrastructure** in any DeepSeek arm (no `L`).
  Every row is a real verdict.
- **Arm wall-clock** (campaign log, `--jobs 4`): T 6.76 h, T2 6.19 h,
  T_s1 6.69 h, T3 5.79 h, U 6.59 h.
- **Unit-hours** is the sum of the 71 units' elapsed times. With 4 units in
  flight it runs about 4× the arm's wall-clock.
- **T's turn counts** are missing because harness8 rows killed at the wall
  carried no counters. Partial stats on a wall kill arrived with harness9.
- **The three units no DeepSeek arm has passed** are
  168_linearizability_kv_b, 168_linearizability_kv_f and 192_pike_regex.
  The union of the five finished arms is 68/71.

## 5. Per-arm deltas

Read against the noise of about ±1 unit (§6.1):

- **T → T_s1 (noise; crosses harness8 → harness9 and the seed): 0 net, one
  unit each way.** This sets the margin.
- **T → T2: +4, 0 lost.** Four units gained, none lost, which is outside
  the noise. All four gains had hit T's wall. T2 also used fewer
  unit-hours (24.5 against 26.6) and had fewer wall hits (13 against 16).
- **T2 → T3: 0 net, 3 in and 3 out (same era: the cap alone).** The gains
  are three 168 variants. The losses are 167_q15_biquad_a, 173 and 183;
  two of those three are units T2 had gained over T. The board describes
  the trade as iterate-and-test units in and long-derivation units out.
  T3 is the fastest arm (21.5 unit-hours, 5.79 h, 10 wall hits) but scores
  lower on SCORE (91.5 against 94.9), because the units it trades are not
  equal weight.
- **T2 → U: −6, 0 gained (crosses an era; four changes at once).** The lost
  units, by how they ended in U against T2:

  | Unit | U | T2 |
  |---|---|---|
  | 163_bigint_divmod_d | wall at 3,600 s, 26 turns | passed at 3,162 s, 45 turns |
  | 168_linearizability_kv_e | wall, 11 turns | wall, 29 turns (passed) |
  | 173_taskgroup_cancel | wall, 17 turns | wall, 28 turns (passed) |
  | 183_heston_little_trap | wall, 17 turns | wall, 49 turns (passed) |
  | 175_crdt_orset_rga | wall, 53 turns | wall, 45 turns (passed) |
  | 190_autograd_mlp | used all 60 turns at 2,818 s | passed at 3,034 s, 56 turns |

  Four of the six ran out of wall with 11-26 turns, where T2 had 28-49.
  That is the signature of 16K-token capped turns, each about twice as
  long as an 8K one. (The campaign file's UP_8k note says "every unit U lost
  hit the wall with 11-26 turns". The per-unit data above is more exact:
  175 hit the wall at 53 turns, and 190 ran out of turns, not wall.)
- **U → UP (partial, 38 units): +1, 0 lost so far.** The gain is
  168_linearizability_kv_a. This is no verdict yet.

## 6. Analysis

### 6.1 Noise

T and T_s1 pass the same 61 units but one: each passes a unit the other
fails (168_linearizability_kv_a, 175_crdt_orset_rga). That is about ±1
unit at one seed, and it matches the campaign's `noise_sd` of 1.0. SCORE is
noisier than the pass count: the same 61 gave 88.5 against 85.7, because
the swapped units carry different weights in SOLVE and LANG. A single
extra seed is thin evidence, and T_s2 is the optional second draw.

### 6.2 The cap trade-off

The per-turn thinking cap sets two things at once: how deep one turn can
think, and how many turns fit on the wall. On this model:

- **16K (T, T_s1, U):** turns of up to ~9 minutes. Failures are wall kills
  with few turns. In T, all 10 failures ended on the wall.
- **8K (T2):** best overall (65, SCORE 94.9), +4 over T with no regressions.
- **4K (T3):** also 65, and the fastest arm. It gains units that reward
  many short iterate-and-test turns, and loses units that need one long
  derivation.

**Decision: 8K stays the default** (`AGENT_PROFILE=deepseek-best`,
`recommend.composite_by_model`). T3 did not beat T2 by more than the
noise, and T2 holds the higher SCORE.

**The adaptive-cap idea (IDEA, not built).** The units that 4K wins and
the units that 8K wins are different units. A cap that adapts within a
unit could take both: for example, a deeper cap for the first,
design-setting turns and a shorter one once the agent is in a write-test
loop, or a cap that shrinks after consecutive capped turns. It must be a
general rule that names no task. It would be a runner change, so it would
move the era and need its own arm, read against T2 and T3. Logged in
[HARNESS_NOTES.md](../HARNESS_NOTES.md#deepseek-v41-flash-mia-exl3-the-v5-campaign-2026-11-20).

### 6.3 What U taught

U changed four things against T2. The data separates two of them:

- **The forced-act turn works mechanically.** In U's transcripts there were
  24 forced turns in 18 of the 71 units (transcripts created in U's
  window). All 24 ended in a tool call (`force_act_result`,
  `finish_reason: tool_calls`). The text nudge it replaced was ignored
  every time in the 168 investigation.
- **The 16K cap cost the score (§5).** U spent about as many unit-hours as T
  (26.1 against 26.6) and had about as many wall hits (15 against 16). Its
  lost units ran out of wall with far fewer turns than T2's.

The clean test of the forced-act turn at the better cap is **U_8k against
T2**, and U_8k against U isolates the cap inside harness10. Both are
queued. Until they report, the forced-act turn, done-when-verified and the
hidden web tools are **unmeasured at 8K**, not rejected.

### 6.4 The 168 family, at a high level

168 is one task in six languages, and it accounts for five of T2's six
failures. A sealed review in `evals/audit/reviews/` investigated it. Its
conclusion, in general terms:

- **A design-choice failure, not a knowledge gap.** The model can solve
  the unit: its passing runs show it knows the fact that makes the problem
  tractable. More often it commits early to an approach that cannot meet
  the unit's limits, and does not revisit that choice.
- **Thinking stalls compound it.** Under a cap, a turn that is all
  reasoning is cut off. Most of that reasoning is discarded, so the design
  is re-decided from scratch each turn, and much of the wall goes into
  capped turns that write nothing.
- **The spec and validator are fair.** No change is recommended.
- **The remedies are general and name no task:** the forced-act turn,
  done-when-verified, planning lines (bound the problem first; test a
  doubtful simplification against a brute force before rejecting it;
  redesign on a large miss of the time limit), and a frozen procedure pack.
  These are arms U, UP and UPA. Any task-specific hint or fact sheet, or
  rewording the spec, is ruled out for the scored suite.

The pack and the planning lines are legitimate only on four conditions:
they are frozen before measurement, they name no task, they are reported
per unit with a no-harm check on the other units, and they run with a GLM
control. All four are in the campaign file.

### 6.5 Per language

**Python carries the swings** (26-30 of 32 in the finished arms). The units
that move between arms are mostly single-language Python units
(167_q15_biquad_a, 173_taskgroup_cancel, 175_crdt_orset_rga,
183_heston_little_trap, 190_autograd_mlp), plus the Python variant of 168.
The compiled languages move by one or two units at most, and those moves
are 168 variants (C, C++, Rust) and 163_bigint_divmod_d (C++). Go's one
miss (7/8 in every arm) and Zig's (5/6 in every finished arm) are the 168
variants no arm has passed. The language packs (zig v3, host pack) are not
the bottleneck for this model. Its residual is algorithmic and temporal.

### 6.6 Against GLM-5.3-Flash and the reference

DeepSeek's T2 (65/71) leads GLM-5.3-Flash's best (C, 51/71 scored, 54 raw)
by 14 scored units. That is not a like-for-like harness comparison. GLM's C
had no packs or diagnostics, and its E arm, with most of T's components,
scored 50. The GLM control for UP (`--only UP --model-name GLM-5.3-Flash`)
is the first same-arm comparison. The Claude Opus 5.5 reference passes
71/71 (68/71 at the 1× wall) with Claude Code's own tools and no turn cap:
a ceiling, not a peer. DeepSeek-V4.1-Flash (Mia EXL3) under T2 is 6 units
below it.

## 7. Hy4-flash and TensorFold 1.2.10

**Hy4-flash** (Tencent, `hy_v4_flash`):

- **The EXL3 pack we downloaded** (`LibertAIDAI/Hy4-flash-EXL3-4bpw`, on
  both Sparks) cannot be served across two Sparks: ExLlamaV3's
  tensor-parallel workers are local to one host. On one Spark it has no
  server, only a reference-speed PyTorch path. It is **kept on purpose**:
  it documents the single-host limit, and nothing in the Hy4 scripts may
  delete it ([review](../reviews/HY4_FLASH_ENGINES-2026-11-19.md),
  [EXL3_PLAYBOOK.md](../EXL3_PLAYBOOK.md)).
- **NVFP4 on vLLM TP2** across both Sparks (`atakans/Hy4-flash-NVFP4` @
  30f201ac, about 82 GiB per Spark) is **staged in code**:
  `spark-hy4-stage.sh`, `beast-spark hy4`, and a unit that is never a boot
  default ([SPARK_HYDRA.md](../SPARK_HYDRA.md#hy4-flash-nvfp4-vllm-tp2-staged)).
- **Arm H_T2** is T2 exactly against the Hy4 server, so its row differs
  from DS-T2 in the model only. It runs after the U series. Its ETA (8-12 h)
  is unverified, because Hy4 decodes more slowly than DeepSeek (CLAIMED
  figures in the review).
- **Thinking.** Hy4 ignores `enable_thinking` and defaults to
  `reasoning_effort` high, so H_T2 thinks. Thinking-off arms (N, N0, and
  the U arms' forced-act turn) are not Hy4 arms until the runner can send
  `reasoning_effort`, which would be an era change.

**TensorFold 1.2.10:**

- **Native TensorFold** is upgraded from 1.2.7 to 1.2.10 on both Sparks
  (pinned; `scripts/engine.sh rollback tensorfold` reverts). It is
  installed, not serving
  ([EXL3_PLAYBOOK.md](../EXL3_PLAYBOOK.md)).
- **No effect on DeepSeek.** DeepSeek serves from the forked engine in Mia's
  recipe image, not the native binary, so no DeepSeek arm is confounded by
  the upgrade. Native 1.2.10 still refuses `deepseek_v41` and
  `hy_v4_flash`.
- **GLM recipe update: pending a separate test.** GLM-5.3-Flash serves
  from Mia's recipe v1.8 (`33b50fd`: TensorFold v0.6.0 plus patches, in its
  own image), and that recipe has an upstream update. It is tested and profiled on its own,
  between arms, before any GLM row is measured on it. An engine change is
  a confound, so a GLM row on a new engine is a new configuration, never a
  re-roll.

## 8. Limitations

- **One seed per arm, except T** (T and T_s1). A ±1-unit noise estimate
  from one pair is thin. Differences of 1-2 units, and SCORE differences of
  a few points, are not findings.
- **Cross-era comparisons.** T → T2 and T2 → U cross harness versions
  (§3.4). Only T2 → T3 and the harness10 series compare one variable inside
  one era.
- **Several changes at once.** U changed four things against T2. Its loss is
  attributed to the cap by the turn evidence, not by an isolating arm. U_8k
  is that arm.
- **v5's scope is agentic coding.** v5 has 71 units: algorithmic and
  systems tasks in seven languages, each judged by a hidden validator. It
  does not measure chat quality, long-context retrieval, tool use outside
  coding, or speed for interactive use.
- **The reference uses a different scaffold.** Claude Opus 5.5 ran with
  Claude Code's own tools, no turn cap, and over the API. It is a ceiling
  for the suite, not a same-harness peer, and is never ranked.
- **One host, one quantization.** Every row is the Mia EXL3 2.9 bpw pack on
  the Spark pair at `--jobs 4`. A different pack, engine or concurrency is a
  different configuration.
- **The wall is doubled** (`--wall-scale 2`) for every thinking arm, so
  these rows are experiment arms on the board, not the L0 protocol.
- **UP is partial.** Its 37/38 so far covers the units that finished first,
  and is no estimate of its final score.

## 9. Next steps

1. **UP finishes, then UPA.** Scan both for integrity, add them to the board
   (`evals/v5_board.py --add`), and regenerate this report's tables.
2. **The gap between series:** the GLM recipe engine update is tested on
   its own (§7). Nothing on the pair changes while an arm runs.
3. **U_8k, UP_8k, UPA_8k.** U_8k against T2 is the forced-act turn at the
   better cap. U_8k → UP_8k → UPA_8k changes one variable per step at 8K.
   If any of them beats T2 by more than the noise, move
   `composite_by_model` and the profile together, with the evidence.
4. **Hy4-flash:** stage the NVFP4 weights and image (memory-capped), bring it
   up hands-on (conformance, bench), run H_T2, put it on the board, then
   `beastds` back to DeepSeek.
5. **The GLM control for UP,** to tell a DeepSeek-specific effect from a
   general one.
6. **The adaptive cap** (§6.2), as an arm after the 8K series, if the series
   leaves the cap question open.
7. **A second seed** (T_s2, or a seed arm of T2) before any 1-2 unit claim.
8. **The final report:** `evals/campaign.py report`, then HARNESS_NOTES
   "Per-model vs unified", which records which components were kept for
   both models and which for only one.

## Sources

- The board: [`evals/boards/v5/board.json`](../../evals/boards/v5/board.json),
  [`exclusions.json`](../../evals/boards/v5/exclusions.json), and
  `evals/v5_board.py --json`.
- U and UP: the harness host's `evals/results/` (untracked), read-only.
- [`evals/CHANGELOG.md`](../../evals/CHANGELOG.md) (harness8, review 1118,
  harness9, harness10), [`evals/campaigns/v5-thinking-first.json`](../../evals/campaigns/v5-thinking-first.json).
- [MODEL_CAMPAIGN.md](../MODEL_CAMPAIGN.md), [HARNESS_NOTES.md](../HARNESS_NOTES.md),
  [RESULTS.md](../RESULTS.md), [SPARK_HYDRA.md](../SPARK_HYDRA.md) (E2b,
  Hy4-flash), [HY4_FLASH_ENGINES-2026-11-19](../reviews/HY4_FLASH_ENGINES-2026-11-19.md).
- Sealed, and summarized here at a high level only: `evals/audit/reviews/`.
