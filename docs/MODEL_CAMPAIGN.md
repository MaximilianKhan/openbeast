# Model campaign: from a new model on the Sparks to the final report

This is the procedure that found, on 2026-11-16, how best to serve and harness
GLM-5.3-Flash on the spark-hydra pair. It is written so that any model can go
through it unchanged. The second model through it is DeepSeek-V4.1-Flash
(Mia EXL3), the pair's default since 2026-11-18 ("1017"; checklist and status in
[§9](#9-next-deepseek-v41-flash-1017-thinking-first), results so far in
[the interim campaign report](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md)). Every step is a command. Where the
step needs judgment, the decision rule is written down next to it.

```
 0  onboard the model           weights, engine, conformance, a MODELS entry
 1  prerequisites               suite proven, toolchains pinned, gate admits --jobs and waits long enough
 2  L0 baseline                 evals/profile.py: the daily config, L0 -> plan -> L1 + L2
 3  failure audit               auditor + skeptic per failed group; fix what is real; rerun only lost/cleared units
 4  reference ceiling           Claude Opus 5.5 on the same suite (model-independent: reuse the ledger)
 5  harness arms, thinking-first evals/campaign.py run: the best-first composite, its ablations, noise, no-thinking last
 6  comparison                  evals/campaign.py report: the recommended daily config, flips, thinking's cost, noise
```

The code is in the repo:

| Piece | Where |
|---|---|
| the chained arms, rerun-lost, report | [`evals/campaign.py`](../evals/campaign.py), campaign files in [`evals/campaigns/`](../evals/campaigns/): `v5-thinking-first.json` (the default), `v5-harness.json` (the 2026-11-16 GLM build-up chain A-F) |
| the failure audit (replay, prompts, Workflow, verdicts, language taxonomy) | [`evals/audit/`](../evals/audit/README.md) |
| the reference attempt (prompts, Workflow, integrity scan) | [`evals/reference/`](../evals/reference/README.md), on top of [`evals/reference_attempt.py`](../evals/reference_attempt.py) |
| the L0 profile and its contrasts | [`evals/profile.py`](../evals/profile.py), [`evals/ablation.py`](../evals/ablation.py) |
| why each harness change exists | [`docs/HARNESS_NOTES.md`](HARNESS_NOTES.md) (G1-G27) |

Every command below runs on the **harness host** (the Mac today): it is where
the agents' tools, the compilers and the validators run. Only chat calls go
to the Sparks.

---

## 0. Onboard the model

The model has to be served behind beast-gate before any of this. Each step is
in an existing runbook. Do them in order:

1. **Inspect and fetch the weights.**
   `scripts/backends/model-inspect.sh <repo@sha> --write-profile NAME` writes
   a profile pinned to a revision. `scripts/backends/model-fetch.sh --profile
   NAME` downloads exactly that revision and verifies it (or re-hashes a copy
   already on disk). Then carry the weights to every worker:
   [SPARK_HYDRA.md §C](SPARK_HYDRA.md#c-weights) (C1 fetch / verify, C2 the
   HF-cache layout, C3 to every worker, C4 a drafter if the recipe has one).
   The engine matrix and the profile format are in
   [ENGINES.md](ENGINES.md).
2. **Serve it.** Natively through `spark-node.sh` / `spark-cluster.sh`:
   [SPARK_HYDRA.md §D2](SPARK_HYDRA.md#d2-native-tensorfold-through-spark-nodesh--spark-clustersh).
   Or through the engine plane, `scripts/engine.sh list | install | use
   <engine> --profile NAME | status | rollback`:
   [SPARK_HYDRA.md §D3](SPARK_HYDRA.md#d3-the-engine-plane-scriptsenginesh)
   and [ENGINES.md](ENGINES.md).
3. **Conformance.** Run `scripts/backends/conformance.sh` against the served
   endpoint. Tool calls must parse and the reasoning must be split from the
   content. A model that fails it will fail the eval for reasons that have
   nothing to do with its skill.
4. **A `benchmark_all.py` MODELS entry** (`evals/benchmark_all.py`, next to
   `glm-5.3-flash`): `slug`, `name`, `base_url`, `api_key_env` (the variable
   holding the device key, never the key), `slots`, `engine`, and
   `reasoning_budget` declared (`"0"` = thinking off), because no process on
   the harness host can see the remote server's flags. This is the daily
   (profile) config's declaration. The campaign arms declare their own, per
   arm (§7). `profile.py --model
   <slug>` and `campaign.py --model <slug>` then fill name, URL, slots and key
   from it.
5. **A device key** for the harness host: on spark-1, `./scripts/clients.sh
   enroll eval-host --label "eval harness"`. Store it in a 0600 file and
   export it as the variable the MODELS entry names:
   `export OPENBEAST_SPARK_API_KEY="$(cat ~/.config/openbeast/spark-hydra-gate.key)"`.

## 1. Prerequisites

Five things (1a-1e), checked before the first scored request. Each one cost us a run
on 2026-11-16 when it was missing.

**1a. Every suite unit is proven model-free.**

```bash
python3 tests/prove_task.py --suite v5        # the reference passes, every wrong solution fails
```

A unit that is not proven cannot be audited. Without the proof, "the
validator is wrong" is always a possible verdict. With it, a validator bug
must be one the reference does not trip. Re-prove after any task or
validator fix.

**1b. The harness host has pinned toolchains.** These are python 3.12 (a
durable venv with `agents/requirements.lock`), zig exactly `0.16.0`, rustc,
go, a C/C++ compiler, git, and shellcheck, all in a directory that lasts.
`scripts/eval-host.sh` provisions them from the pins in
`evals/toolchains.lock` (#189), per platform, under
`~/.local/opt/openbeast-eval`, never as root, idempotent. Offline, it
installs from an ark drive or a flat mirror with `--from`. The recipe is
[SPARK_HYDRA.md F6](SPARK_HYDRA.md#f6-the-eval-harness-host-benchmarking-the-pair).
Write its exports to a file once, so every later command (and every audit
or reference agent) can source the same one:

```bash
./scripts/eval-host.sh setup                                  # install what is missing or wrong (exit 3 = sha256 mismatch)
./scripts/eval-host.sh env > ~/.config/openbeast/eval-env.sh  # PATH, RUSTUP_HOME, CARGO_HOME, OPENBEAST_EVAL_PY
. ~/.config/openbeast/eval-env.sh
./scripts/eval-host.sh check && "$OPENBEAST_EVAL_PY" -c 'import openai'
```

`run_eval.py` and `profile.py` run the same check before a live run and
provision when it fails. `campaign.py run` runs it too
(`preflight.host_check` in the campaign file, `scripts/eval-host.sh check`)
together with `evals/profile.py`'s preflight. A failure refuses the
campaign. (The 2026-11-16 GLM host predates the script: its toolchains
were hand-placed under `~/.local/opt` and sourced from
`~/.local/opt/openbeast-eval-env.sh`.)

*Lesson: a durable venv.* On 2026-11-16 at 16:46, something rewrote the
python symlink of a scratch venv under a running arm. 22 units of arm B
"failed" in 0.0 s with `Could not find platform independent libraries`.
Never run an arm from a scratch or temp interpreter. `campaign.py` takes
`--python` (default `$OPENBEAST_EVAL_PY`). It imports `openai` with that
interpreter before every arm and every rerun, and refuses to start if the
import fails.

**1c. The gate admits `--jobs` concurrent requests.** Nothing to run by
hand: `profile.py` and `campaign.py run` both call
`evals/profile.py:admission_check` before anything is scored. It sends
`--jobs` one-token requests at once, and any 429 refuses the run. The gate admits
`EDGE_MAX_INFLIGHT` generations per device key, which defaults to
`INFERENCE_SLOTS` (4 on the pair). The first 2026-11-16 L0 ignored this,
logged 2,107 × 429, and was thrown away.

**1d. The gate waits longer than the longest turn.** In spark-1's
`openbeast.conf`, set `EDGE_READ_TIMEOUT=3600`, then `systemctl --user
restart openbeast`. A non-streaming completion sends nothing until it is
done. The gate's default 600 s read timeout turned long turns into 504s that
the harness recorded as server errors (G7: 168_c and 168_f lost 1,200 s each
and never wrote code). Size it from the eval token cap:

| Setting | Value | Why |
|---|---|---|
| eval turn cap (runner, `_EVAL_TURN_TOKENS`) | 8,192 tokens, thinking off; 16,384 with `OPENBEAST_AGENT_THINKING=on` | a v5 solution is a few thousand tokens; the cap only bites on a runaway (G11) |
| the runner's client read timeout | cap ÷ **8 tok/s** = 1,024 s / 2,048 s | 8 tok/s is the pessimistic per-stream decode under `--jobs 4` load. GLM measured ~12 tok/s at long context under 4-way load. The first sizing (16K at 20 tok/s, 819 s) fired before the cap and lost the turn twice |
| `EDGE_READ_TIMEOUT` on the gate | **3600 s** | above the client's 2,048 s, so the client's own timeout, never the gate's, ends a turn |

For a new model, measure its per-stream decode rate under `--jobs` load
before the L0. Send four long-context requests at once and divide the tokens
by the seconds. Below 8 tok/s, the client times out before a capped turn
finishes, and the turn is lost instead of truncated. Raise the read timeout
(and `EDGE_READ_TIMEOUT` above it), or run fewer `--jobs`. Eval runs never
retry a timed-out turn (`_EVAL_CLIENT_MAX_RETRIES = 0`): the agent loop owns
recovery.

**1e. Seed determinism: know what one run is.** When a request sends no
seed, the Spark TensorFold server falls back to a FIXED seed. At
temperature 0.6, identical requests return identical text, and every run is
one deterministic draw. L1/L2 transcripts were byte-identical to L0 until
the first injected byte (G12). A 23-byte diagnostics line flipped two units
both ways. So a single-run arm delta on a few units is trajectory chaos,
not an effect. Check a new engine the same way: send one request twice with
no seed, then twice with seed 1. The campaign's seed arm (**T_s1**, the
composite under `--seed 1`; in `v5-harness`, arm **F**, B under `--seed 1`)
is the noise floor every other contrast is read against.

## 2. L0 baseline: the daily config

```bash
. ~/.config/openbeast/eval-env.sh
python3 evals/profile.py --model <slug> --budget-h 4 --jobs 4
# or explicitly:
python3 evals/profile.py --model-name "<Model>" --base-url https://spark-1.tail4109f9.ts.net:8443/v1 \
    --api-key-env OPENBEAST_SPARK_API_KEY --slots 4 --jobs 4 --reasoning-budget 0 --budget-h 4
python3 evals/ablation.py --profile evals/results/profile-<slug>-<ts>.json      # the contrasts again
```

The profile runs preflight, then L0 (the whole suite, bare: the board row;
it measures k = wall seconds per reference second). Then it plans L1 (packs)
and L2 (packs + diagnostics + escalation) to fit the budget, and runs them
in one process. It writes `evals/results/eval-<slug>-L0-<ts>.json` (and L1,
L2) plus a state file. `--resume <state>` continues an interrupted profile.

What to look for:
- `summary`: passed / total, and the infra rows (`reason`: `server_error`,
  `budget_skip`, ...). These are not failures. `campaign.py`'s rules treat
  them as lost.
- The turn-vs-wall binding. For every failed unit, did it end on the turn
  cap (`iterations == max_iter`) or on the wall (`agent_exit_code == -1`)?
  On GLM L0, most failures ended on the turn cap after using only 10-30% of
  their wall (G1). That one observation is why wall-scaled turns exist
  (`v5-harness` arm B).
- The L1 − L0 per-language contrast. A language whose units rise with
  packs (zig 0/6 → 3/6 on GLM) has a knowledge gap. §3's taxonomy says how
  much.

## 3. Failure audit

**Needs Claude Code, and so the internet.** The audit's auditors and
skeptics are Claude Code subagents run by a Workflow; neither the Sparks'
local model nor an offline harness host can run this step. Offline, keep
the L0 results and run the audit when the network is back. The `prepare`,
`verdicts` and `langaudit` scripts are local.

Every failed L0 unit gets an auditor (who reads the transcript, rebuilds
the final solution, and replays it through the real validator) and then a
skeptic (who argues the other way). Full instructions:
[`evals/audit/README.md`](../evals/audit/README.md).

```bash
RUN=evals/results/audit-<slug>-L0
python3 evals/audit/prepare.py evals/results/eval-<slug>-L0-<ts>.json --out-dir $RUN \
    --model "<Model>" --serving "2x DGX Spark, <engine>, thinking off" \
    --env-sh ". ~/.config/openbeast/eval-env.sh" --python "$OPENBEAST_EVAL_PY"
# Claude Code: run the Workflow evals/audit/failure-audit.workflow.js with $RUN/args.json as args,
#              save its result as $RUN/audit-result.json
python3 evals/audit/verdicts.py $RUN/audit-result.json --out $RUN/final.json --md $RUN/verdicts.md
python3 evals/audit/langaudit.py L0=evals/results/eval-<slug>-L0-<ts>.json --out $RUN/taxonomy.json
```

Verdicts: `TRUE_FAIL` · `HARNESS` · `PROMPT` · `VALIDATOR_BUG` · `INFRA` ·
`UNCLEAR`. The skeptic's final verdict wins.

Decision rules:
- **Fix what is real.** For `PROMPT` and `VALIDATOR_BUG`, change the task
  or validator, re-prove it (`tests/prove_task.py --unit <id>`), and note it
  in `evals/CHANGELOG.md`. For `HARNESS`, change the runner or tools as a
  HARNESS_NOTES entry (it moves the era, §7). For `INFRA`, fix the
  infrastructure (the gate timeout, the venv).
- **Then rerun selectively.** Rerun only `HARNESS`, `INFRA` and `UNCLEAR`
  (indeterminate) units, and the fixed `PROMPT` / `VALIDATOR_BUG` units:
  `campaign.py rerun-lost --arm <arm> --units <list> --verdicts
  $RUN/final.json`, or `run_eval.py --tasks <list>` for a profile row.
- **Never re-roll a true fail.** On a deterministic server a rerun is just
  another draw. Re-rolling the failures alone (and never the passes) only
  ratchets the score up: it is selection, not measurement. `rerun-lost`
  refuses any unit the verdicts file calls `TRUE_FAIL`.
- **Systemic notes become harness hypotheses.** Every pattern the auditors
  saw across a group (turn-cap endings, BSD userland, no-op plan updates,
  verify-before-done) becomes a G-entry in
  [HARNESS_NOTES.md](HARNESS_NOTES.md), with its evidence, and is measured
  as an arm. It is never adopted on the audit alone.
- **Packs only where knowledge fails.** The taxonomy counts knowledge (K)
  failures per language (`langaudit.py` prints "Language packs indicated").
  A pack for a language whose failures are reasoning failures costs context
  and buys nothing. The composite carries packs regardless; its `T_nopacks`
  ablation measures whether they pay, and the report drops them when they
  do not.

On 2026-11-16 the GLM L0 audit found 43 of 45 failures `TRUE_FAIL` and 2
harness. The systemic notes became G1-G8. The taxonomy found zig K in
nearly every zig failure, rust partly, and host-userland friction across
languages (hence the host pack and the env block).

## 4. Reference ceiling

**Needs Claude Code, and so the internet** (Claude Opus 5.5 is the solver).
Offline, reuse the existing ledger, which is the rule anyway (below).

Claude Opus 5.5 attempts every unit through
`evals/reference_attempt.py`'s setup/grade split. The solver never sees
validator output, and a separate agent grades. Afterwards, a transcript
integrity scan checks for forbidden reads. Instructions:
[`evals/reference/README.md`](../evals/reference/README.md).

```bash
python3 evals/reference/prepare.py --suite v5 --env-sh ". ~/.config/openbeast/eval-env.sh" --python "$OPENBEAST_EVAL_PY"
# Claude Code: run the Workflow evals/reference/reference.workflow.js with <run-dir>/args.json
python3 evals/reference/integrity_scan.py ~/.claude/projects/<project>/<session>/subagents/workflows/<run id>/ \
    --forbid-from <run-dir>/args.json
```

The row does not depend on the model under test. **Reuse the ledger for
every model on the same suite version.** Rerun it only when the suite
changes. The 2026-11-16 v5 ledger scored 71/71 (68/71 inside the strict 1x
wall). Pass it to `campaign.py report --reference-ledger`. It answers "is
the gap the model or the suite": a unit the reference passes and the model
fails is the model's (or the harness's) to win.

## 5. Harness arms, thinking first

```bash
. ~/.config/openbeast/eval-env.sh
python3 evals/campaign.py run --model <slug> --jobs 4 --dry-run     # every arm's command and its ETA
python3 evals/campaign.py run --model <slug> --jobs 4 \
    --baseline L0=evals/results/eval-<slug>-L0-<ts>.json
# explicitly:
python3 evals/campaign.py run --campaign v5-thinking-first --model-name "<Model>" \
    --base-url https://spark-1.tail4109f9.ts.net:8443/v1 --api-key-env OPENBEAST_SPARK_API_KEY \
    --jobs 4 --slots 4 --baseline L0=evals/results/eval-<slug>-L0-<ts>.json
python3 evals/campaign.py status --model <slug>
```

`--campaign` defaults to `v5-thinking-first`
([`evals/campaigns/v5-thinking-first.json`](../evals/campaigns/v5-thinking-first.json)).
Each arm is a full v5 run with `--no-cache` and its own `--model-name`
(`"<Model> v5-thinking-first <ARM>"`).

### 5a. Why thinking first

The 2026-11-16 GLM-5.3-Flash night built the harness up one change at a
time (`v5-harness`, §5d), with thinking as one arm among six. The numbers,
on one seed:

| Row | v5 (71 units) |
|---|---|
| L0 (runner v1, thinking off) | 26/71 |
| A (runner v2) | 31/71 |
| B (A + wall-scaled turns) | 36/71 |
| C (B + thinking, `--wall-scale 2` so its turns equal B's) | **54/71 raw, 51/71 scored** (three passes withheld by the contamination scan, §8). On the first 21 units, when this order was decided: 18 against B's 8 on the same units, 10 rescued, 0 regressed, and 14 of the 18 passes inside B's original wall |

Thinking is by far the largest lever. No other single change moved more
than five units over the whole suite. C moved ten in its first 21. So the
order changes, as Max put it: "start with thinking and eventually end with
no-thinking… front load having better results first and knowing where all
things stand."

- **The best config runs first.** The headline row (everything believed to
  help, thinking on) exists after one arm, not after six.
- **Each component is measured where it will be used.** A component's value
  with thinking on can differ from its value without it. Wall-scaled turns
  may matter less when each turn is smarter; packs supply knowledge thinking
  cannot. So each ablation removes one component from the thinking composite.
- **No-thinking runs last.** It is the latency question ("is thinking
  worth 2.3x the time?"), not the capability question.
- **The build-up chain is kept.** `--campaign v5-harness` reproduces the GLM
  night (§5d).

### 5b. The arms

In run order. ETAs are for GLM-like timing at `--jobs 4` (base 2.5 h, see
below). `--dry-run` prints them for your settings.

| Arm | Built from | Change | Flags and switches | time x | ETA | What it answers |
|---|---|---|---|---|---|---|
| **T** | — | the best-first composite | `--turns-per-wall-s 15 --thinking on --wall-scale 2 --reasoning-budget 16384`; `BEAST_ASSIST=1 OPENBEAST_DIAGNOSTICS=1 OPENBEAST_DIAG_EDIT_HINT=1 OPENBEAST_AGENT_VERIFY_DONE=1 BEAST_PACKS=1 OPENBEAST_PACKS=1 OPENBEAST_PACK_VARIANT=v3 OPENBEAST_AGENT_ENV_BLOCK=1 OPENBEAST_AGENT_WALL_WARN=1` | 2.3 | ~5.8 h | the headline row: the best the harness can do today |
| **T_noturns** | T | − wall-scaled turns | drops `--turns-per-wall-s` (the task's own `max_iter`, same doubled wall) | 2.0 | ~5.0 h | do turns still bind once the model thinks? |
| **T_nopacks** | T | − packs | `BEAST_PACKS=0 OPENBEAST_PACKS=0 OPENBEAST_PACK_VARIANT=` | 2.3 | ~5.8 h | the zig 0.16 pack v3 and the host pack (G19, G20, #194) |
| **T_noassist** | T | − diagnostics, edit hint, verify-done | `BEAST_ASSIST=0 OPENBEAST_DIAGNOSTICS=0 OPENBEAST_DIAG_EDIT_HINT=0 OPENBEAST_AGENT_VERIFY_DONE=0` | 2.3 | ~5.8 h | G9, G13, G14 |
| **T_noenv** | T | − env block, wall warnings | `OPENBEAST_AGENT_ENV_BLOCK=0 OPENBEAST_AGENT_WALL_WARN=0` | 2.3 | ~5.8 h | G22, G23 |
| **T_s1** | T | seed noise | `--seed 1` | 2.3 | ~5.8 h | the noise margin every decision uses |
| **N** | T | thinking off, normal wall | `--thinking off --reasoning-budget 0`, drops `--wall-scale` | 1.1 | ~2.8 h | the daily-latency config: what thinking adds to the full harness, and its cost |
| **N0** | — | the bare no-thinking baseline | `--turns-per-wall-s 15 --thinking off` (= 2026-11-16 arm B, thinking pinned off) | 1.0 | ~2.5 h | the floor. N0 → N is what the components add without thinking; N0 → T is everything |
| T_s2 | T | a second seed (optional) | `--seed 2`; only with `--include T_s2` | 2.3 | ~5.8 h | a tighter noise margin |
| T2 | T | 8192 turn cap + act nudge (optional, harness9) | `--reasoning-budget 8192`; `OPENBEAST_AGENT_MAX_TOKENS=8192 OPENBEAST_AGENT_ACT_NUDGE=1`; `"jobs": 4, "slots": 4` like T (decided 2026-11-18); only with `--only T2` or `--include T2`, **and first in every DeepSeek-V4.1-Flash chain** (`recommend.composite_by_model`); needs a harness9 tree | 2.3 | ~5.8 h (6.2 h for DeepSeek) | do shorter thinking turns and a push to act turn wall kills into passes? **DeepSeek: yes, 65/71 against T's 61/71** |
| T3 | T2 | 4096 turn cap (optional, config only) | `--reasoning-budget 4096`; `OPENBEAST_AGENT_MAX_TOKENS=4096 OPENBEAST_AGENT_ACT_NUDGE=1`; only with `--only T3`; same harness9 tree and era as T2 | 2.3 | ~5.8 h | does faster iteration beat deeper single turns? (T3 vs T2 isolates the cap) **DeepSeek: no, 65/71 like T2, 3 units in and 3 out, SCORE 91.5 against 94.9; 8192 stays** |
| U | T2 | forced-act turn instead of the act nudge + done-when-verified, 16384 cap (optional, harness10) | `--reasoning-budget 16384`; `OPENBEAST_AGENT_MAX_TOKENS=16384 OPENBEAST_AGENT_ACT_NUDGE=0 OPENBEAST_AGENT_FORCE_ACT=1 OPENBEAST_AGENT_FORCE_ACT_AFTER=2 OPENBEAST_AGENT_DONE_VERIFIED=1 OPENBEAST_AGENT_HIDE_WEB=1`; only with `--only U`; needs a harness10 tree | 2.3 | ~6-7 h for DeepSeek | does a thinking-off act turn end the capped-think stalls the text nudge could not? (U vs T2) **DeepSeek: 59/71, −6 against T2. Every forced turn acted (24 of 24), but the lost units ran out of wall at the 16384 cap; U_8k is the clean test** |
| U_8k | U | U at T2's 8192 cap (optional, harness10) | `--reasoning-budget 8192`; `OPENBEAST_AGENT_MAX_TOKENS=8192` | 2.3 | ~6.2 h | the cap question under the forced act (U_8k vs U; read with T3) |
| UP | U | + three planning lines (optional, harness10) | `OPENBEAST_AGENT_ALGO_LINES=1`; also the GLM control (`--model-name GLM-5.3-Flash` when the pair serves GLM) | 2.3 | ~6-7 h | do "bound first / test the doubtful split against a brute force / redesign on a >5x miss" fix the wrong-algorithm failures? (UP vs U) |
| UPA | UP | + the frozen algorithmic procedure pack (optional, harness10) | `OPENBEAST_ALGO_PACK=1` (`agents/packs/algo-v1.md`, sha256 `6c532400...`, packs on) | 2.3 | ~6-7 h | does a ~400-token procedure pack add to the lines? (UPA vs UP) |
| UP_8k | UP | UP at T2's 8192 cap (optional, harness10) | `--reasoning-budget 8192`; `OPENBEAST_AGENT_MAX_TOKENS=8192` | 2.3 | ~6.2 h | the planning lines at the better cap (UP_8k vs U_8k), and the whole stack against T2 |
| UPA_8k | UPA | UPA at T2's 8192 cap (optional, harness10) | `--reasoning-budget 8192`; `OPENBEAST_AGENT_MAX_TOKENS=8192` | 2.3 | ~6.2 h | the pack at the better cap (UPA_8k vs UP_8k) |
| T_j2 | T | 2 concurrent units (optional) | `"jobs": 2, "slots": 2` in the arm (wins over the CLI); only with `--only T_j2` or `--include T_j2` | 2.3 | ~7.7 h (~9 h for DeepSeek) | the contention model: same tokens per unit, at 2 streams (see the note below) |

**DeepSeek's composite is T2 (2026-11-19).** DeepSeek-V4.1-Flash (Mia EXL3)
scored 65/71 under T2 against 61/71 under T: +4 (168_kv_e, 173, 183, 190), 0
regressions, the board's best DeepSeek row (DS-T2, `evals/boards/v5/board.json`).
T2 is therefore DeepSeek's best-first composite, recorded in the campaign file
as `recommend.composite_by_model: {"DeepSeek-V4.1-Flash": "T2"}` (matched on
the slug, so `DeepSeek-V4.1-Flash (Mia EXL3)` and `deepseek-v4.1-flash` match
too). For such a model `campaign.py run` puts T2 at the head of the chain
(`--order` still decides when given), and `report` starts the recommended daily
config from T2: each ablation and the no-thinking twin are still compared with
T, the arm they remove from, a dropped component is removed from T2, and the
noise margin comes from T's seed arm while T2 has none of its own. Until T2 is
measured, the report uses T and says so. Arm T is unchanged for every model
(the GLM history depends on it). **State on 2026-11-20:** T_s1 61/71 (noise
about ±1 unit), T3 65/71 (a wash against T2: 8192 stays), U 59/71 (the 16384
cap cost it), **UP running**, then UPA, then U_8k, UP_8k and UPA_8k after a
gap; the tables and their deltas are in [the interim campaign report](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md). The
daily-agent form of T2 is one line in openbeast.conf,
`AGENT_PROFILE=deepseek-best` ([SPARK_HYDRA.md](SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2)),
live on spark-1 since 2026-11-19.

Total for the eight default arms: about 39 h at GLM's speed. Escalation
stays off in every arm (G16: zig-only on the Mac host).

Choices in the file, and why:
- **Packs v3.** `OPENBEAST_PACK_VARIANT=v3` is set wherever packs are on (v3 = v2 + the `std.posix.write`, `@clz` shift, no-`erf` and init-skeleton claims from the 2026-11-17 residue audit, #194). The `v5-harness` campaign keeps v2 so its arm E stays reproducible.
  The variant defaults to v1 (the measured pack production serves), so
  `BEAST_PACKS=1` alone measures the old pack. The host pack goes with
  every packs-on unit either way. Packs reach every language because the
  arm is a single-arm run (no `--arms`). Under `--arms L1`, the plan runs
  only the zig units.
- **Thinking off is sent, not assumed.** N and N0 send `--thinking off`
  (`enable_thinking: false`) instead of leaving it to the server, because a
  model's chat template may think by default (check DeepSeek's, §9). On GLM,
  whose default is off, N0 is arm B.
- **The ablation order.** By expected effect, largest first, so a chain cut
  short still answers the biggest questions. Wall-scaled turns came first:
  +5 on GLM (B vs A), the largest measured lever after thinking, and the one
  thinking might make redundant. Packs come next: zig 0/6 → 3/6 on L1, a
  knowledge gap that thinking cannot close. Diagnostics and hints follow,
  never measured on their own (v5-harness D). The env block and wall
  warnings come last, the smallest expected effect. The seed arm runs after
  the ablations: decisions are made at report time, so it only has to exist
  before the report.
- **The order is configurable.** The file's `order` list sets the default.
  `--order T,T_s1` moves arms to the front (here, the noise floor early),
  and the rest follow in the default order. `--include T_s2` adds an
  optional arm. `--from ARM` and `--only ARM[,ARM]` work as before. An order
  that would take a decision before the arms it compares have run is
  refused.

**ETA.** Every arm has a `time_x`, its expected time relative to the
campaign's reference arm (N0, `eta.reference`). The thinking arms' 2.3 is
measured: C against B per unit on GLM. The reference's hours come from
`--base-hours H` if given. Otherwise they are calibrated from this
campaign's finished arms (the median of hours ÷ `time_x`), so the ETAs
correct themselves after the first arm. Failing both, the file's
`eta.base_h` applies: 2.5 h, which is GLM's arm A at 2.1 h plus an estimated
20% for B's extra turns. The ETAs scale from `eta.jobs` (4) to the arm's
jobs by throughput, `jobs / contention_scale(jobs)` (4 jobs -> 2.0 units per
unit time, 2 -> 1.5, 1 -> 1.0), not linearly: per-stream decode speeds up as
streams drop, and run_eval's walls shrink with it. So `--jobs 2` is 1.33x the
`--jobs 4` hours, not 2x, and a finished arm calibrates the base after the
same correction. `--dry-run` prints one per arm and the total; `START`
lines in `campaign.log` carry them.

**An arm's own concurrency.** An arm may set `"jobs"` and `"slots"`. They
win over the CLI's `--jobs`/`--slots` for that arm (and for any arm built on
it), go into its run_eval command, its reruns and its preflight admission
check (the largest jobs of the arms to run), are recorded in the state
(`arms.<ARM>.jobs`/`slots`, and per rerun), and show in `--dry-run`
(`#   jobs 2, slots 2: the arm's own (the CLI said --jobs 4 --slots 4)`).
run_eval records the `--jobs` it ran at in the results header. The report's
"at base wall" columns use each arm's own jobs.

**T_j2, read before its result (2026-11-18).** DeepSeek-V4.1-Flash (Mia
EXL3)'s arm T lost all 10 of its failures to the 3,600 s wall with turns that
were slow, not scarce (~30 tok/s per stream at 4 streams, ~45 at 2). T_j2 runs
T at 2 streams. But run_eval widens each unit's wall by
`contention_scale(jobs)` (4 -> 2.0x, 2 -> 1.33x), so at `--jobs 2` the wall
is 2,400 s, and 2,400 s x 45 tok/s is the same ~108K tokens as 3,600 s x 30
tok/s. T_j2 therefore tests the contention model on this model, not more
tokens per unit. An arm that gives each unit more tokens keeps the absolute
wall: `--wall-scale 3` at `--jobs 2`.

### 5c. What `run` does for you

- **Chains** the arms in order, resumably. Re-run the same command after
  an interruption: finished arms are skipped. An interrupted arm keeps its
  banked rows, and its missing units rerun as lost. `--only ARM[,ARM]`
  reruns specific arms from scratch, and the old rows go to the arm's
  history.
- **Resolves each arm from its base.** An arm adds flags, switches one the
  base set (`--thinking off` over `--thinking on`: the new value replaces
  the old one in place), removes one (`"drop_args": ["--wall-scale"]`), and
  sets env switches. `common.env` pins every switch off or empty, so an
  ambient shell variable cannot leak into an arm.
- **Pins the tree.** The commit is recorded at the start, and an arm refuses
  to start if the tree has moved since. Use `--arm-tree ARM=<checkout>` for
  an arm that needs code the others do not have (the report shows each
  arm's commit and era). `--allow-tree-move` accepts the move on purpose.
- **Refuses what the tree cannot run.** An arm whose flag (`--wall-scale`)
  or switch (`OPENBEAST_AGENT_ENV_BLOCK`, `OPENBEAST_PACK_VARIANT`) the tree
  lacks is marked `unsupported` and skipped. It is never run silently
  without its change. Merge the PR that adds it, or pin a tree that has it.
- **Checks every arm's reasoning budget.** The preflight refuses an arm that
  declares no finite `--reasoning-budget` (§7).
- **Makes run-time decisions once** (`v5-harness`'s `thinking`: E thinks iff
  C beat B). The decision is compared on the units both arms scored,
  recorded, and reused on resume. `--set-decision thinking=then` overrides
  it. The thinking-first campaign has no run-time decisions: its rules are
  applied by `report` (§6).
- **Never scores infra.** After each arm, every row the agent never really
  ran is **lost**: interpreter startup, no output in < 2 s, signal death,
  server error, budget skip. `rerun_lost.rounds` (1) reruns them at once
  through a sub-manifest (`evals/suites/_lost-*.json`, untracked, coverage
  floor relaxed to zero, the same `suite` field). If more than
  `max_lost_fraction` (5%) are still lost, the chain stops: fix the cause,
  then `campaign.py rerun-lost --arm <arm>` and `run` again.

State, logs and sub-manifests live under
`evals/results/campaigns/<campaign>-<slug>/` (`state.json`, `campaign.log`,
`arm-<ARM>.log`, `arm-<ARM>-r<k>.log`). `campaign.py paths --arm T` prints
an arm's results files, main run first, for the audit tools.

Rules for every arm:
- **The wall vs turn binding.** Read the turn-cap endings and wall timeouts
  in the report. Wall-scaled turns fixed turn-bound units by giving them
  turns. A slower arm (thinking) at the same wall would become clock-bound.
  That is why every thinking arm runs at `--wall-scale 2` with the same
  turns. The report's "at base wall" columns say how many of its passes
  needed the extra time.
- **One draw per arm.** Read every flip table against the seed arm's churn
  (§1e). A rescue claim needs the unit to hold across seeds (`--include
  T_s2`, or more seed arms in a copy of the file).
- **Era and cache key.** See §7. Every arm runs with `--no-cache` and a
  distinct `--model-name`.

### 5d. The build-up campaign: `v5-harness` (A-F)

The 2026-11-16 GLM chain, kept under its name so that night can be
reproduced: `--campaign v5-harness`.

| Arm | On top of | Change | What it tests |
|---|---|---|---|
| A | — | runner v2, the merged default (G1-G4, G8, G11) | the default harness vs the L0 audit's frictions |
| B | A | `--turns-per-wall-s 15` | turn-bound vs wall-bound (G1): one turn per 15 s of the unit's wall |
| C | B | `--thinking on --wall-scale 2 --reasoning-budget 16384` | thinking at B's turn count. The wall doubles so the slower thinking turns are not clock-bound: the contrast is thinking, not lost time |
| D | B | diagnostics + `OPENBEAST_DIAG_EDIT_HINT=1` + `OPENBEAST_AGENT_VERIFY_DONE=1` | push diagnostics, edit-don't-rewrite (G13), test-before-done (G9) |
| E | D | packs v2 (`BEAST_PACKS=1 OPENBEAST_PACK_VARIANT=v2`: zig pack v2, and the host pack for every packs-on unit) + `OPENBEAST_AGENT_ENV_BLOCK=1` + `OPENBEAST_AGENT_WALL_WARN=1`, and thinking **iff C > B** | the best combination the evidence supports |
| F | B | `--seed 1` | the noise floor: B again under another sampling seed |

Two fixes since #191, both in the file. E sets `OPENBEAST_PACK_VARIANT=v2`;
#191's E measured the v1 pack. C, and E's thinking branch, declare
`--reasoning-budget 16384` rather than the common `0` (§7). Neither change
affects the cache key's separation of the arms. To replay #191's exact E,
copy the file and drop the variant. C's new declaration moves its key's
`rb` component (`.rb0` → `.rb16384`); campaigns run `--no-cache`, so
nothing replays either way.

## 6. Comparison

```bash
python3 evals/campaign.py report --model <slug> \
    --reference-ledger evals/results/reference/<run>/ledger.jsonl \
    --out evals/results/campaigns/v5-thinking-first-<slug>/report.md
```

The report reads the results files run_eval wrote and merges each arm's
reruns. It renders:
- **Totals**: per row (reference, baselines, arms), passed / scorable /
  lost, % of the reference, mean turns, mean agent seconds, turn-cap endings,
  wall timeouts, run hours, and each arm's commit and era.
- **Recommended daily config** (campaigns with a `recommend` block): the
  verdict on every component and on thinking, the noise margin and where it
  came from, thinking's measured cost, and the resulting run_eval command
  line.
- **Decisions** taken at run time, and arms not run (with the reason).
- **Noise**: the seed arm's churn against the composite.
- **Flips**: each arm against its base, against every baseline (L0, and
  L1/L2 when you pass a profile state as `--baseline`), and the file's extra
  `contrasts` (N0 → N, N0 → T). Each row gives the common units, the
  rescued and regressed lists, the net, and a two-sided exact sign test.
- **Thinking vs no thinking**: per arm, thinking on or off, the wall scale,
  passed, **passed at base wall** (the passes inside the unwidened wall: the
  unit's wall × the `--jobs` contention scale), mean agent seconds and mean
  completion tokens per unit (the reasoning tokens included), and run hours.
  Below the table, every contrast in which thinking differs (N → T first),
  with flips and the per-unit time and token ratios on the common units.
- **Wall-scaled arms at the original wall**: the same base-wall reading,
  with the units that needed the extra time.
- **Per language**, every row side by side with the reference.
- **Still lost** units and the reruns made.

### The decision rules

Written down before the numbers, and applied by `report` (`recommend()` in
`campaign.py`):

1. **Noise margin.** The seed arm T_s1 measures `c`, the number of units
   that flip between two draws of one config. With no effect, those flips
   land up or down with equal odds, so a contrast's net delta has a
   standard deviation of about √c. The margin is `ceil(noise_sd × √c)`
   (`noise_sd` 1.0 in the file; with T_s2, the noisier seed arm sets it).
   With no seed arm measured, the margin is `noise_fallback` (3), and the
   report labels it an assumption.
2. **Components.** For each ablation, net = composite passes − ablation
   passes, on the units both scored. If net ≤ margin, removing the
   component did not hurt beyond noise, and it is **dropped** from the
   recommended config. Otherwise it is **kept**. An ablation not yet
   measured keeps its component.
3. **Thinking.** Net = T passes − N passes, on the common units. If net ≤
   margin, recommend **no thinking**, for its latency. Otherwise recommend
   **thinking**, and state its measured cost: time and completion tokens
   per unit, T against N, and how many of T's passes fit the unwidened
   wall.
   A model with its own composite (`recommend.composite_by_model`;
   DeepSeek-V4.1-Flash: T2) still has each ablation compared with the arm it
   removes from (T); the verdict then applies to the model's composite.
4. **The recommended daily config** is the composite, minus the dropped
   components, with thinking off if rule 3 says so. The report prints it as
   one run_eval command line. When thinking is off, it notes that the
   component verdicts were measured with thinking on. Without thinking, only
   N0 → N speaks for the components (as a group).

Then decide what to adopt:
1. A kept component is a candidate for the default harness. A dropped one
   is noise until more seeds say otherwise.
2. Was the gain bought with time? Compare mean agent seconds, tokens, and
   the base-wall reading.
3. Does the gain hold for another model? See the HARNESS_NOTES "Per-model vs
   unified" rule. A change that helps one model and hurts another is a
   per-model knob, not a default.

Write the results into HARNESS_NOTES (the components' G-entries move to
ADOPTED or REJECTED) and [RESULTS.md](RESULTS.md).

## 7. The rules that keep rows comparable

- **Era.** The cache key's era is the hash of `system-prompt.md`,
  `agents/runner.py`, `agents/tools.py`, `evals/SUITE_VERSION`,
  `evals/HARNESS_VERSION` (pinned to the AST of run_eval's agent-shaping
  functions) and `evals/vlib.py`. With `OPENBEAST_EVAL_ENV_ERA=1` (set by
  profile.py and the campaign file), the weights, engine and toolchain
  versions are in it too. Any change to those files is a new era. Rows from
  different eras do not pair, so an arm that needs a runner change is a full
  run of its own tree, never a re-roll of some units.
- **Agent switches are in the key.** `OPENBEAST_AGENT_THINKING`,
  `_SEED`, `_VERIFY_DONE`, `OPENBEAST_DIAG_EDIT_HINT`,
  `OPENBEAST_AGENT_ENV_BLOCK` and `_WALL_WARN` add an `ag-...` component
  (`ag-think-on-vdone-ehint-envb-wwarn` for T), so a thinking arm cannot
  replay a no-thinking arm's rows (G18: before #185 it would have). Packs
  key as `pack1-<sha8>` over the pack bytes, so v1 and v2 rows never
  share a key. The campaign file pins every switch in `common.env` (off or
  empty), so an ambient shell variable cannot leak into an arm.
- **The reasoning budget is declared per arm.** For a remote server
  (TensorFold, vLLM), `--reasoning-budget` is a *declaration*: nothing on
  the harness host can read the server's flags. It is stamped as
  `server.reasoning_budget` in the results and becomes the key's `.rb<N>`
  component. `profile.py`'s preflight refuses an undeclared or uncapped
  (`-1`) budget. `0` means thinking off. With per-request thinking
  (`--thinking on`, `chat_template_kwargs.enable_thinking`), the server
  has no budget of its own. What bounds the thinking is the runner's eval
  turn cap, `_EVAL_TURN_TOKENS_THINKING` = 16,384 completion tokens
  (reasoning plus answer) per turn. So the thinking arms declare
  `--reasoning-budget 16384`, and the no-thinking arms declare `0`. Before
  this, `v5-harness` declared `0` for every arm, so arm C's rows were
  stamped "thinking off" while thinking. The key still separated C from B
  (by `ag-think-on`), but the provenance was wrong. A test pins the
  thinking arms' declaration to the runner's constant: if the cap moves,
  the campaign files move with it.
- **Never pull mid-campaign.** run_eval re-reads the era per unit and
  stamps `era_moved_to` if the tree changes under it. campaign.py refuses to
  start an arm once the tree has moved.
- **Sub-manifests are not in the era.** `evals/suites/_lost-*.json` restrict
  the unit set only, so the rows they produce merge with their arm's.
- **Review 1118 (2026-11-18): integrity fixes that move the era but not a
  correct run's outcome.** The macOS eval sandbox denies the agent's shell,
  its push-diagnostics checkers and its `grep` every outbound connection
  except loopback, keeps `evals/{tasks,refs,suites}` sealed under the
  `/tmp/eval*` allow-back, and makes the repo, the eval venv, the eval
  prefix and the pack files read-only to the agent; content-addressed pack
  files are re-verified on reuse and on read. These are edits to
  `agents/tools.py` and `agents/runner.py` (reporting only: the API-error
  tail, `RESPONSE_MODELS`, `REASONING_TURNS`), so the era moves
  (`595634987879411a` -> the new tree's). `evals/HARNESS_VERSION` does not:
  run_eval's agent-shaping functions are untouched (still `harness8`).
  Label a run of this tree **"harness8 + sandbox hardening (review 1118)"**
  on the board: a correct solution passes the same way (no unit needs the
  network beyond loopback or writes outside `/tmp/eval*` and `$TMPDIR`), so
  its rows stand next to the GLM rows, whose four files were themselves
  measured under four different eras. The era rule above is about pairing
  rows inside a campaign (flips, ablations), not about the board.
- **harness9 (2026-11-18): T2's switches, partial stats, the fetch guard.**
  The era moves (`06c82c4335dd6a0c` -> `036638ddd68698ce`) and
  `evals/HARNESS_VERSION` goes to `harness9`: `agents/runner.py` (the act
  nudge behind `OPENBEAST_AGENT_ACT_NUDGE=1`, the partial-stats sidecar),
  `agents/tools.py` (the eval fetch guard) and run_eval's `run_agent` (it
  hands out the sidecar and reads it on a wall kill). The per-turn cap
  `OPENBEAST_AGENT_MAX_TOKENS` and the nudge are agent switches in the key
  (`cap8192`, `anudge`). `common.env` pins both off, so only T2 sets them.
  A thinking arm that sets its own cap declares that cap as its
  `--reasoning-budget` (a test pins it). A wall-killed row now keeps the
  tokens, turns and reasoning turns it had (`stats_partial: true`). These
  are reporting only and never count as a turn-cap ending. Rows of T2 pair
  only with harness9 rows. Run it from a merged tree with
  `--allow-tree-move` (the campaign's pinned commit is T's), or
  `--arm-tree T2=<checkout>`. The CHANGELOG has the detail.
- **harness10 (2026-11-19): the U arms' switches, reasoning text, no web
  advertised.** The era moves (`036638ddd68698ce` -> `9bcd5aa6030836d1`) and
  `evals/HARNESS_VERSION` goes to `harness10` (run_eval's agent-shaping code
  is unchanged, so the fingerprint stays `cd729e38ca1e3030`): `agents/runner.py`
  (the forced-act turn, the done-when-verified and planning lines, the
  reasoning text in the transcript) and `agents/tools.py` (the web tools left
  out under eval). Four new agent switches in the key: `fact<N>`
  (`OPENBEAST_AGENT_FORCE_ACT=1`, N = `OPENBEAST_AGENT_FORCE_ACT_AFTER`, default
  2), `dverif` (`OPENBEAST_AGENT_DONE_VERIFIED=1`), `alines`
  (`OPENBEAST_AGENT_ALGO_LINES=1`) and `noweb` (`OPENBEAST_AGENT_HIDE_WEB=1`).
  `OPENBEAST_ALGO_PACK=1` adds the frozen algorithmic pack to every unit's
  pack file, so it moves the `pack1-` component instead. `common.env` pins
  all of them off. Every harness10 run logs each turn's reasoning text
  (`reasoning` events, capped at `OPENBEAST_AGENT_REASONING_LOG_CHARS`,
  default 100,000 chars per turn; reporting only). Rows of the U arms pair only with
  harness10 rows. T3 stays on harness9 (it was launched from the tree
  before this change).
- **A row is lost to API errors only when they ENDED the run** (review
  1118). run_eval records `api_errors_tail` (failed calls after the last
  successful one); a failed row with `api_errors > 0` but a zero tail is a
  verdict, as a pass with an error always was. Rows without the field
  (every published board row) keep the old rule, so no published score
  moves.
- **The served model is checked around every unit** (review 1118). A live
  run refuses to start unless `/v1/models` lists `--served-model` /
  `OPENBEAST_INFERENCE_MODEL`; before and after each unit it re-reads it,
  waits up to `OPENBEAST_EVAL_MODEL_WAIT_S` (600 s) for a missing model to
  come back, then stops as an infra stop (`served_model_changed`, never
  banked, a pass under the wrong model discarded). Each row records
  `response_models` (what the server named) and `reasoning_turns` (the
  effective thinking).
- **Infra is never a verdict, a pass is always one.** That is the rule in
  run_eval's cache (a server error is not banked), in profile.py (a budget
  skip is not a failure), and in campaign.py (lost units).

## 8. Worked example: GLM-5.3-Flash, 2026-11-16

Served on the spark-hydra pair (TensorFold, `GLM-5.3-Flash-EXL3`, 4 slots,
thinking off), through beast-gate, from the Mac harness host.

In the order it happened (local times from the run logs where they exist):

| Time | Step | What happened |
|---|---|---|
| before 06:08 | L0, first attempt | 2,107 × 429: the gate's in-flight cap was below `--jobs 4`. Thrown away, and `admission_check` added to the preflight |
| 06:08 | L0 | 26/71. 168_c and 168_f lost to the gate's 600 s read timeout (G7) → `EDGE_READ_TIMEOUT=3600` |
| 09:00 | L0 rerun of the infra units | the read timeout fixed; 168 then ran into the client's own 600 s timeout and its retry (G11) → the eval turn cap and the 8 tok/s client sizing |
| 10:00 | L1 + L2 | zig 0/6 → 3/6 with packs; the L2 audit found decoding deterministic (G12) |
| after L0 | the L0 failure audit | 45 failures: 43 TRUE_FAIL, 2 harness; the systemic notes became G1-G8 and runner v2 (#179) |
| 13:58 | arm A (runner v2) | **31/71** (single seed), 2.1 h |
| 16:06 | arm B | 22 units lost at 16:46 when a scratch venv's python symlink was rewritten, and 2 never started → the durable venv, the interpreter check and rerun-lost |
| — | Claude Opus 5.5 reference | **71/71** (68/71 at the 1x wall), integrity scan clean |
| 18:49 | B repair, then C, D, E, F (one chain) | from pinned trees, `--no-cache`, distinct model names, on the durable venv |

| Row | v5 (71 units) | Notes |
|---|---|---|
| Claude Opus 5.5 (reference) | **71/71** (68/71 at 1x wall) | own tools, no turn cap |
| L0 (daily config, runner v1) | **26/71** | 43/45 fails audited TRUE_FAIL under that harness |
| L1 (zig packs) | zig 0/6 → 3/6 | |
| A (runner v2) | **31/71** | +5 net vs L0 (11 rescued, 6 regressed) |
| B (+ wall-scaled turns) | **36/71** | its 24 lost units rerun (B repair) |
| C (+ thinking, wall x2) | **54/71 raw; 51/71 scored (SCORE 76.7)** | +18 raw / +15 scored over B, one seed. Three passes (122_gemm_blocked_b, _d, _e) are withheld: the contamination scan (#195) found their agents read sealed paths (below). On the first 21 units: 18 vs B's 8, 10 rescued, 0 regressed, 14 of the 18 passes inside B's original wall, ≈ 2.3x B's time per unit. This is why the default campaign is thinking-first (§5a) |
| D (+ diagnostics, edit hint, verify-done) | **not run** | Max paused the chain after E (2026-11-17) |
| E (+ packs, env block, wall warnings, thinking iff C > B) | **50/71 (SCORE 74.7)** | C > B, so E ran thinking on (zig pack v2, as `v5-harness` pins). Integrity scan clean; on the board (#201). One under C scored, four under C raw: a wash at one seed. Its 122_gemm_blocked_c FAIL is a validator false FAIL (the pre-#195 lint matched `dlopen` in a comment), footnoted and not adjusted. **Decision:** C stays GLM's best row and becomes the serving default (thinking on, nothing else; [SPARK_HYDRA.md E2a](SPARK_HYDRA.md#e2a-recommended-serving-configuration-from-v5)); E's additions stay opt-in |
| F (B under `--seed 1`) | **not run** | Max paused the chain after E; the noise floor is still unmeasured, so every delta above is single seed |

**Integrity caveat on C.** In 122_gemm_blocked_b (turns 15-24), stuck on a
harness bug (the path regex kept a sentence's final period, so verify-done
asked for `gemm.c.`), the agent went looking for the checker. It read Claude
Code workflow journals under `~/.claude/projects/`, the repo's
`agents/tools.py` and `evals/run_eval.py`, and a cached validation result in
`evals/cache/`. The eval sandbox did not deny those reads. The unit passed,
and the contamination scan (#195, `evals/audit/integrity_scan.py`, every v5
row of every arm) found two more C passes whose agents reached into
`~/.claude` (122_gemm_blocked_d grepped it; 122_gemm_blocked_e tried), so the
board scores C at 51/71 with all three withheld; 54/71 is the raw count.
#195 also seals those paths for eval agents from harness8 on. Detail:
[HARNESS_NOTES.md](HARNESS_NOTES.md), "Language-specific residue under
thinking"; the exclusion is recorded in `evals/boards/v5/exclusions.json`
(#193).

That chain was a hand-written shell script. With this tooling, the same
night is one command:

```bash
. ~/.config/openbeast/eval-env.sh
python3 evals/campaign.py run --campaign v5-harness --model glm-5.3-flash --jobs 4 \
    --baseline L0=evals/results/eval-glm-5-3-flash-L0-20261116-060847.json
```

The thinking-first campaign was written after C's first 21 units, and C's
final (51/71 scored, 54 raw, against B's 36/71) bore it out. GLM has not run it as a
campaign. On GLM, its N0 is arm B (36/71, thinking pinned off).

## 9. Next: DeepSeek v4.1 Flash ("1017"), thinking first

- [x] Prepared the stock Mia EXL3 checkpoint, both Engram shards and both rank caches. [Pinned deployment and verification](SPARK_HYDRA.md#deepseek-deployment).
- [x] Served DeepSeek via TensorFold TP2 behind beast-gate. Conformance passed chat, streaming, reasoning-field extraction, tools and four-way concurrency. The context-overflow probe was skipped.
- [ ] **Make sure the pair serves DeepSeek** (first: every live check below, and L0, need it serving). Since 2026-11-18
      DeepSeek-V4.1-Flash (Mia EXL3) is the pair's default (unit
      `deepseek-v41-inference` enabled), so this is normally a no-op check;
      GLM-5.3-Flash is the staged alternative. The only supported switch
      is `beast-spark` on spark-1 (aliases `beastds` / `beastglm`;
      [SELF_DEVELOPMENT.md §0](SELF_DEVELOPMENT.md#0-before-going-offline-bring-both-sparks-to-main)
      installs it from the repo):
      ```bash
      ssh spark-1.tail4109f9.ts.net '~/.local/bin/beast-spark ds && ~/.local/bin/beast-spark status'
      # status: selected: ds (pack ...-v2) ... serving on http://127.0.0.1:8000: DeepSeek-V4.1-Flash-1017
      # from the harness host, through the gate the campaign will use:
      curl -s -H "Authorization: Bearer $OPENBEAST_SPARK_API_KEY" \
          https://spark-1.tail4109f9.ts.net:8443/v1/models | python3 -m json.tool | grep '"id"'
      ```
      `/v1/models` must list `DeepSeek-V4.1-Flash-1017` and nothing else.
      If it lists `GLM-5.3-Flash-EXL3`, every row below would be GLM's under
      DeepSeek's name. Since review 1118, pass `--served-model
      DeepSeek-V4.1-Flash-1017`: the campaign preflight and run_eval both
      refuse to start unless `/v1/models` lists it, and run_eval re-checks
      it around every unit and stops as an infra stop if the gate swaps
      models mid-arm (nothing banked). Without the flag the guard pins
      whatever the gate lists first, so check the id yourself. Nobody may
      switch the pair to GLM (`beastglm`) until the last arm ends.
- [ ] Record the reproducible campaign profile and seed artifact before starting the scored arms.
- [ ] **Thinking, before anything else: the whole campaign leans on it.**
      How does the server toggle thinking per request? The runner sends
      `chat_template_kwargs.enable_thinking` true/false. Verify both values
      live. `true` must return `reasoning_content` (or the server's
      equivalent), and `false` must return none. Check also what the
      template does when nothing is sent. N and N0 send `false` explicitly
      for this reason. If DeepSeek takes a different switch (e.g. a
      `thinking` kwarg), the runner's `_thinking_kwargs` must learn it
      first. Until then, T measures nothing.
- [ ] Seed determinism (§1e) on this engine: same request twice with no seed,
      then with seed 1.
- [ ] Per-stream decode under 4-way load (§1d), **with thinking on**: a
      thinking turn can run to the 16,384-token cap. Below 8 tok/s, the
      client times out before a capped turn ends. Resize the client timeout
      and `EDGE_READ_TIMEOUT` before L0.
- [ ] A `benchmark_all.py` MODELS entry (§0.4). **`evals/benchmark_all.py`
      has none for DeepSeek yet** (checked on `main`, 2026-11-19), so
      `--model deepseek-v4.1-flash` below fails until it is added. The entry,
      appended to `MODELS` after `glm-5.3-flash` and in the same form:
      ```python
          {"slug": "deepseek-v4.1-flash",
           "name": "DeepSeek-V4.1-Flash",
           "base_url": "https://spark-1.tail4109f9.ts.net:8443/v1",
           "api_key_env": "OPENBEAST_SPARK_API_KEY",
           "slots": 4,
           "engine": "tensorfold",
           "reasoning_budget": "0"},
      ```
      `base_url` is the gate (the same one GLM's entry uses: one gate, one
      model at a time). `name` is the `--model-name` the campaign ran under
      (`DeepSeek-V4.1-Flash`, not the served id): the campaign's state lives
      under `evals/results/campaigns/v5-thinking-first-deepseek-v4-1-flash/`,
      named from it, so another name starts a new campaign instead of
      resuming this one. The served id goes in `--served-model`. `reasoning_budget` declares the daily (profile)
      config: `"0"` if DeepSeek's daily config is thinking off; change it if
      not. The campaign arms declare their own (§7).
- [ ] §1: re-prove the suite if anything changed since the last proof,
      `admission_check` at `--jobs 4`, and `EDGE_READ_TIMEOUT` still 3600.
- [ ] §2 L0 profile, §3 audit and taxonomy, §4 reuse the v5 Opus ledger
      (no rerun unless the suite changed).
- [ ] §5, thinking first. Dry run first, then the chain. T, the headline,
      lands first. If DeepSeek runs much faster or slower than GLM, pass
      `--base-hours` from its L0 time, or let the first finished arm
      recalibrate the ETAs:
      ```bash
      # eval-env.sh is made once per host (§1b):
      #   ./scripts/eval-host.sh env > ~/.config/openbeast/eval-env.sh && . ~/.config/openbeast/eval-env.sh
      . ~/.config/openbeast/eval-env.sh
      python3 evals/campaign.py run --model deepseek-v4.1-flash --jobs 4 --dry-run
      python3 evals/campaign.py run --campaign v5-thinking-first --model deepseek-v4.1-flash --jobs 4 \
          --baseline L0=evals/results/eval-<deepseek-slug>-L0-<ts>.json
      # without a MODELS entry yet:
      python3 evals/campaign.py run --campaign v5-thinking-first --model-name "DeepSeek-V4.1-Flash" \
          --served-model DeepSeek-V4.1-Flash-1017 \
          --base-url https://spark-1.tail4109f9.ts.net:8443/v1 --api-key-env OPENBEAST_SPARK_API_KEY \
          --jobs 4 --slots 4 --baseline L0=evals/results/eval-<deepseek-slug>-L0-<ts>.json
      # the headline arm alone (no L0 yet): --only T, no --baseline
      ```
      Since review 1118, `scripts/eval-host.sh check` (the campaign's
      preflight hook) fails a toolchain that resolves to a user-writable copy
      outside the eval prefix (a Homebrew `go`: run `scripts/eval-host.sh
      setup --only go` once) and an `OPENBEAST_EVAL_PY` outside the prefix
      (the old `~/.local/opt/openbeast-eval-venv`: `eval "$(scripts/eval-host.sh
      env)"` sets the right one). After the arm, check the rows'
      `reasoning_turns` (T must be > 0 on almost every row: thinking really
      happened) and `response_models` (only `DeepSeek-V4.1-Flash-1017`), and
      run `evals/audit/integrity_scan.py` (it now also flags network commands
      and embedded absolute paths).
      To read the noise floor early, add `--order T,T_s1`. For a tighter
      margin, add `--include T_s2`.
- [x] **Arms T and T2 (2026-11-18/19).** T 61/71; T2 (T + 8192-token turn
      cap + act nudge) 65/71, +4 / 0 regressions, board row DS-T2. Max's
      decision, 2026-11-19: T2 is OpenBeast's default and best setup for
      DeepSeek-V4.1-Flash (Mia EXL3): `recommend.composite_by_model` for
      campaigns (§5b), `AGENT_PROFILE=deepseek-best` for daily agents
      ([SPARK_HYDRA.md](SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2)).
- [x] **Arms T_s1 and T3 (2026-11-19/20).** T_s1 61/71, the same as T with
      one unit swapped each way: the noise is about ±1 unit. T3 (4096 cap)
      65/71, the same as T2 with 3 units in and 3 out and a lower SCORE (91.5
      against 94.9), so the 8192 cap stays. Board rows DS-Ts1 and DS-T3;
      integrity scans clean.
- [x] **Arm U (harness10, 2026-11-20): 59/71**, −6 against T2 and nothing
      gained. Every forced-act turn ended in a tool call (24 of 24), but the
      units U lost ran out of wall with far fewer turns than T2's at the
      16384 cap. The cap, not the forced act, cost U. Integrity scan clean.
      Not on the board yet.
- [ ] **Arm UP: running** (from 2026-11-20 06:35; 37 passed of 38 done at
      09:23). **UPA** follows. Read UP against U and UPA against UP.
- [ ] **Then a gap** (the GLM recipe engine update, tested on its own),
      **then U_8k, UP_8k, UPA_8k** (#250): the same three at T2's 8192 cap,
      one variable per step. U_8k against T2 is the forced act at the better
      cap. Then UP on GLM-5.3-Flash as the control, and Hy4-flash's H_T2
      ([SPARK_HYDRA.md](SPARK_HYDRA.md#hy4-flash-nvfp4-vllm-tp2-staged)).
- [x] **Interim report** (2026-11-20): [the interim campaign report](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md).
      Its tables come from `evals/v5_report.py`; regenerate them after each
      arm (`--write`).
- [ ] Model-specific harness hypotheses: start from
      [docs/reviews/DEEPSEEK_HARNESS-2026-10-09.md](reviews/DEEPSEEK_HARNESS-2026-10-09.md),
      the evaluation of DeepSeek's own agent harness (`dsh`). Its "ideas
      worth porting" are the vendor's view of what its models need: the
      repeat-call reminder at 3/5/8 identical calls (compare G3), spilling
      oversized tool results to a file, read-before-edit and the stale-file
      guard, fresh-context rounds under 40K tokens, and a usage-anchored
      token estimate. Each one that the DeepSeek L0 audit supports becomes a
      G-entry. Add it to the composite in a copy of the campaign file
      (`evals/campaigns/v5-thinking-first-deepseek.json`), with its own
      `T_no<x>` ablation. A change goes into the shared default only if it
      also holds on GLM.
- [ ] §6 report: the recommended daily config, and thinking's cost on
      DeepSeek. Then HARNESS_NOTES "Per-model vs unified": which components
      were kept for both models, and which for only one.
- [ ] **After the last arm (or when the campaign stops), leave DeepSeek serving:**
      it is the pair's default since 2026-11-18, so there is nothing to switch
      back. Confirm with
      `ssh spark-1.tail4109f9.ts.net '~/.local/bin/beast-spark status'`
      (`selected: ds`, serving `DeepSeek-V4.1-Flash-1017`). (The full path
      because a non-interactive ssh command does not have `~/.local/bin` on its
      `PATH`; at a shell on spark-1, `beaststatus` does the same.) Only a
      campaign that switched to GLM itself switches back, with `beast-spark ds`.
