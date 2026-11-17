# Model campaign: from a new model on the Sparks to the final report

This is the procedure that found, on 2026-11-16, how best to serve and harness
GLM-5.3-Flash on the spark-hydra pair. It is written so that any model can go
through it unchanged. The next one is DeepSeek v4.1 Flash ("1017"; checklist in
[§9](#9-next-deepseek-v41-flash-1017-thinking-first)). Every step is a command. Where the
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
| why each harness change exists | [`docs/HARNESS_NOTES.md`](HARNESS_NOTES.md) (G1-G20) |

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

Four things, checked before the first scored request. Each one cost us a run
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
| C (B + thinking, `--wall-scale 2` so its turns equal B's) | 18 of the first 21 units, against B's 8 on the same units: 10 rescued, 0 regressed, and 14 of the 18 passes inside B's original wall |

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
| **T** | — | the best-first composite | `--turns-per-wall-s 15 --thinking on --wall-scale 2 --reasoning-budget 16384`; `BEAST_ASSIST=1 OPENBEAST_DIAGNOSTICS=1 OPENBEAST_DIAG_EDIT_HINT=1 OPENBEAST_AGENT_VERIFY_DONE=1 BEAST_PACKS=1 OPENBEAST_PACKS=1 OPENBEAST_PACK_VARIANT=v2 OPENBEAST_AGENT_ENV_BLOCK=1 OPENBEAST_AGENT_WALL_WARN=1` | 2.3 | ~5.8 h | the headline row: the best the harness can do today |
| **T_noturns** | T | − wall-scaled turns | drops `--turns-per-wall-s` (the task's own `max_iter`, same doubled wall) | 2.0 | ~5.0 h | do turns still bind once the model thinks? |
| **T_nopacks** | T | − packs | `BEAST_PACKS=0 OPENBEAST_PACKS=0 OPENBEAST_PACK_VARIANT=` | 2.3 | ~5.8 h | the zig 0.16 pack v2 and the host pack (G19, G20) |
| **T_noassist** | T | − diagnostics, edit hint, verify-done | `BEAST_ASSIST=0 OPENBEAST_DIAGNOSTICS=0 OPENBEAST_DIAG_EDIT_HINT=0 OPENBEAST_AGENT_VERIFY_DONE=0` | 2.3 | ~5.8 h | G9, G13, G14 |
| **T_noenv** | T | − env block, wall warnings | `OPENBEAST_AGENT_ENV_BLOCK=0 OPENBEAST_AGENT_WALL_WARN=0` | 2.3 | ~5.8 h | G22, G23 |
| **T_s1** | T | seed noise | `--seed 1` | 2.3 | ~5.8 h | the noise margin every decision uses |
| **N** | T | thinking off, normal wall | `--thinking off --reasoning-budget 0`, drops `--wall-scale` | 1.1 | ~2.8 h | the daily-latency config: what thinking adds to the full harness, and its cost |
| **N0** | — | the bare no-thinking baseline | `--turns-per-wall-s 15 --thinking off` (= 2026-11-16 arm B, thinking pinned off) | 1.0 | ~2.5 h | the floor. N0 → N is what the components add without thinking; N0 → T is everything |
| T_s2 | T | a second seed (optional) | `--seed 2`; only with `--include T_s2` | 2.3 | ~5.8 h | a tighter noise margin |

Total for the eight default arms: about 39 h at GLM's speed. Escalation
stays off in every arm (G16: zig-only on the Mac host).

Choices in the file, and why:
- **Packs v2.** `OPENBEAST_PACK_VARIANT=v2` is set wherever packs are on.
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
20% for B's extra turns. The ETAs scale linearly from `eta.jobs` (4) to
`--jobs`. `--dry-run` prints one per arm and the total; `START` lines in
`campaign.log` carry them.

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
| C (+ thinking, wall x2) | [PENDING] final; **18 of the first 21 units** | B passed 8 of the same 21: 10 rescued, 0 regressed; 14 of the 18 passes inside B's original wall; ≈ 2.3x B's time per unit. This is why the default campaign is thinking-first (§5a) |
| D (+ diagnostics, edit hint, verify-done) | [PENDING] | |
| E (+ packs, env block, wall warnings, thinking iff C > B) | [PENDING] | decision: [PENDING] |
| F (B under `--seed 1`) | [PENDING] | noise floor: [PENDING] flips |

That chain was a hand-written shell script. With this tooling, the same
night is one command:

```bash
. ~/.config/openbeast/eval-env.sh
python3 evals/campaign.py run --campaign v5-harness --model glm-5.3-flash --jobs 4 \
    --baseline L0=evals/results/eval-glm-5-3-flash-L0-20261116-060847.json
```

The thinking-first campaign was written after C's first 21 units. GLM has
not run it. On GLM, its N0 is arm B (36/71, thinking pinned off).

## 9. Next: DeepSeek v4.1 Flash ("1017"), thinking first

- [ ] Weights: `model-inspect.sh <repo@sha> --write-profile deepseek-v4.1-flash`,
      `model-fetch.sh --profile deepseek-v4.1-flash`, then to both Sparks (§0.1).
- [ ] Serve it (TensorFold or vLLM via `engine.sh use … --profile …`) behind
      beast-gate, and run `conformance.sh`. Check the tool-call format and the
      reasoning split: DeepSeek's chat template differs from GLM's.
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
- [ ] A `benchmark_all.py` MODELS entry (§0.4) with `reasoning_budget`
      declared for the daily (profile) config: `"0"` if DeepSeek's daily
      config is thinking off. The campaign arms declare their own (§7).
- [ ] §1: re-prove the suite if anything changed since the last proof,
      `admission_check` at `--jobs 4`, and `EDGE_READ_TIMEOUT` still 3600.
- [ ] §2 L0 profile, §3 audit and taxonomy, §4 reuse the v5 Opus ledger
      (no rerun unless the suite changed).
- [ ] §5, thinking first. Dry run first, then the chain. T, the headline,
      lands first. If DeepSeek runs much faster or slower than GLM, pass
      `--base-hours` from its L0 time, or let the first finished arm
      recalibrate the ETAs:
      ```bash
      . ~/.config/openbeast/eval-env.sh
      python3 evals/campaign.py run --model deepseek-v4.1-flash --jobs 4 --dry-run
      python3 evals/campaign.py run --campaign v5-thinking-first --model deepseek-v4.1-flash --jobs 4 \
          --baseline L0=evals/results/eval-<deepseek-slug>-L0-<ts>.json
      # without a MODELS entry yet:
      python3 evals/campaign.py run --campaign v5-thinking-first --model-name "DeepSeek-v4.1-Flash" \
          --base-url https://spark-1.tail4109f9.ts.net:8443/v1 --api-key-env OPENBEAST_SPARK_API_KEY \
          --jobs 4 --slots 4 --baseline L0=evals/results/eval-<deepseek-slug>-L0-<ts>.json
      ```
      To read the noise floor early, add `--order T,T_s1`. For a tighter
      margin, add `--include T_s2`.
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
