# beast-instinct

beast-instinct is a decision plane. It answers named, typed questions
(`yes_no | choice | score | rank`) with a label distribution, `label_mass`, a
calibrated confidence and one of `act | review | abstain | fallback`. The
design and its reasoning are in `docs/BEAST_INSTINCT_PLAN.md`. This page
covers what is built, the contracts, and how to operate it.

Two rules hold everywhere:

1. **Instinct never grants anything.** It can only skip work, or reorder or
   narrow options that the deterministic layer already made eligible.
2. **Nothing is enforced until its quality is measured on our data.**
   Promotion needs a gate record written by `evals/decisions/run.py` and
   committed to git. A config toggle can never promote a decision.

## Status: P0 (built, not wired)

| Piece | Where |
|---|---|
| Library: specs, rendering, label locks, core math, calibration, lifecycle | `agents/instinct/` |
| Engines: `rules`, `linear`, `llamacpp_logprobs`, `sglang_score` | `agents/instinct/engines/` |
| Service on `127.0.0.1:8094` | `agents/instinct/server.py` |
| Fail-open client and the router glue | `agents/instinct/client.py`, `routerhook.py` |
| Decisions | `agents/instinct/decisions/*.toml` |
| Hermetic stub scorer (llama.cpp + SGLang wire formats, fault injection) | `scripts/instinct/stub_scorer.py` |
| Reference hydra consumer | `scripts/instinct/mock_hydra.py` |
| Operator CLI | `scripts/instinct.sh` |
| CPU scorer launcher (P1; `start.sh` runs it when `INSTINCT_SCORER=true`) | `scripts/serve-instinct-scorer.sh` |
| Decision-quality harness | `evals/decisions/{run,metrics,loadgen}.py` |
| Seed dataset (synthetic, not a gate set) | `evals/decisions/router.spawn_intent/` |

All of it is OFF by default. The stack wiring (below) is opt-in through
`INSTINCT`, `INSTINCT_SCORER` and `ROUTER_INSTINCT`; with them unset the stack
is byte-identical to one without instinct (`tests/test_hydra_instinct_wiring.sh`).

## Invariants (each has a test)

| # | Invariant | Test |
|---|---|---|
| I1 | A rank result's ids are the input ids, in input order. The client refuses any other id. | `test_instinct_boundaries.py`, `test_instinct_client.py` |
| I2 | No auth, RBAC, SSRF, gate, runner/tools or eval-grading module imports instinct. | `test_instinct_boundaries.py` (with a planted-import control) |
| I3 | On any failure the caller runs today's path. | `test_instinct_client.py`, `test_router_instinct.py` |
| I4 | Only labels in `[policy.act]` can act. For `router.spawn_intent` that is `inline` only, so instinct can never cause a spawn. | `test_instinct_boundaries.py`, `test_router_instinct.py` |
| I5 | Every call writes one ledger row, including shadow, fallback and eval-refused calls. | `test_instinct_server.py` |
| I6 | An engine without probabilities (`rules`) can never enforce, even with forged records. | `test_instinct_boundaries.py` |
| I7 | Engine URLs on the hydra (`:8095`), beast-gate (`:8443`) or router (`:8088`) port, or at `HYDRA_URL`, are refused. | `test_instinct_spec.py` |
| I8 | Era-locked files are untouched. | `test_instinct_boundaries.py` |

## Contracts

### `instinct/1`: `POST /v1/instinct/decide`

The request uses `extra=forbid`, so an unknown field is a 400. The caller
can't set the mode. It may pass a `ceiling`, and the lower of the two wins.

```json
{"contract": "instinct/1", "decision": "router.spawn_intent", "request_id": "…",
 "inputs": {"user_turn": "…"}, "items": null, "baseline": "hint",
 "ceiling": "enforce", "deadline_ms": 600, "context": {"caller": "router", "eval": false}}
```

The response keys are frozen by `tests/test_instinct_contract.py`:
`contract, decision, decision_version, decision_hash, trace_id, request_id,
mode, enforce, action, answer{type,label,probabilities,raw_probabilities,calibrated,confidence{p_top,margin,shape},label_mass,labels_truncated,expected_value},
items, would{label,action}, fallback{used,reason}, engine{id,adapter,model,model_sha256,exec,label_token_ids},
cascade[{engine,action,reason,ms,label?,p_top?}], latency_ms{queue,engine,total}`.

**A caller acts only on `enforce == true`.**

Status codes:
- 400: malformed JSON, an unknown field, or the wrong contract.
- 401: bad key.
- 404: unknown decision.
- 422: the inputs don't match the decision's input schema.
- 200: everything else. Engine trouble comes back as `action: "fallback"`.

The client treats every non-200 as a fallback.

Fallback reasons:
- `lifecycle_shadow`, `lifecycle_off`, `caller_ceiling`, `canary_out`, `demoted`, `eval_context`
- `uncalibrated`, `no_gate`, `conformance_failed`
- `engine_unavailable`, `engine_timeout`, `deadline`, `overload`, `label_lock_failed`, `registry_invalid`
- `labels_truncated`, `low_label_mass`, `below_threshold`, `not_act_label`, `ood_input`

### `instinct-route/1`: `POST /v1/instinct/route` (hydra)

```json
{"contract": "instinct-route/1", "request_id": "…", "deadline_ms": 25,
 "features": {"prompt_head": "≤2000 chars", "est_prompt_tokens": 38000, "has_images": false,
              "has_tools": true, "stream": true, "client_class": "interactive|agent|batch"},
 "pools": null}
```

The response carries `contract, request_id, trace_id, action, enforce, mode,
task_class{label,probabilities,calibrated,mechanical,decision_hash}, pool_fit, reason, latency_ms`.
`vision` and `long_context` are **mechanical**:
- The `rules` engine computes them from the facts, and marks the answer `mechanical: true`.
- A model can never answer them; their probability is zeroed.
- By I6, a mechanical answer never enforces. Hydra applies those facts itself.

### Hydra's obligations

These are the only coupling between hydra and instinct:

1. **Hard filters come first.** Apply identity, capability (vision, `ctx_max ≥ est` tokens) and health before anything else.
2. **Call `/route` only when it can matter.** At least two eligible pools must remain, and `GET /v1/instinct/contract` must list `instinct-route/1`.
3. **Use the answer only when `enforce == true` and `action == "act"`.** On anything else, use the static policy.
4. **The class-to-pool mapping lives in hydra's config.** Capacity may override the class, and hydra logs it when it does.
5. **Hydra stays fully functional with instinct absent.** Instinct on a dead port means the static policy.
6. **Never register an instinct engine as a pool.** Hydra refuses a node with `role = "instinct-engine"`.
7. **Optional feedback.** Hydra may `POST /v1/instinct/feedback` with `{trace_id, served_pool, ttft_ms, error, outcome}`.

`scripts/instinct/mock_hydra.py` implements all seven. `tests/test_instinct_contract.py`
drives it against the real service: with instinct dead, in shadow, and enforced.

## Lifecycle

`effective_mode` is the lowest of these (`off < shadow < canary < enforce`):
- the spec's target mode;
- `enforce` only if a committed gate record for this exact `decision_hash` says `passed` against the current calibration record;
- `shadow` without a calibration record;
- `shadow` if the engine has no probabilities;
- `shadow` if the engine is unhealthy or its probe is stale;
- `shadow` if the decision is demoted (auto or operator);
- the caller's ceiling.

`decision_hash` covers everything that fixes what a probability means:
- the wording, the labels and their locked token ids;
- the adapter, the model sha and the exec mode.

A retrained `linear` model, a new tokenizer, or an SGLang server forced from
MIS to SIS all produce a new hash. The decision then drops to shadow on its
own.

Promotion and demotion:
- **Promote** (`scripts/instinct.sh promote D --engine E`) only checks. It needs a gate record that has passed and is committed, the spec edited to `mode = "enforce"`, and a SIGHUP. It never edits a file.
- **Demote** (`scripts/instinct.sh demote D`) writes `.run/instinct/demoted.json` and sends SIGHUP. No commit is needed.
- **Auto-demotion** happens when the fallback/timeout rate exceeds 5% over the last 100 calls, or when `label_mass` p50 falls more than 0.2 below the calibration reference over the last 200 calls.

## Operating it

```bash
scripts/instinct.sh stub                              # hermetic scorer on :18082 (plumbing)
INSTINCT_ENGINE_OVERRIDE=stub scripts/instinct.sh up  # service on :8094, mints .run/instinct.key
scripts/instinct.sh status
python3 evals/decisions/run.py --decision router.spawn_intent --engine linear --fit-linear --calibrate
python3 evals/decisions/run.py --decision router.spawn_intent --engine rig-cpu --calibrate   # P1
python3 evals/decisions/loadgen.py --decision router.spawn_intent --engine rig-cpu --qps 0.2,0.5,1 --out load.json
python3 evals/decisions/run.py --decision router.spawn_intent --engine rig-cpu --gate --load-report load.json
scripts/instinct.sh down
```

A gate always fails closed on:
- a missing loadgen report;
- `min_n` not met;
- an LLM engine that doesn't beat `linear` with significance (a significant *loss* never passes).

## Seed baseline: `linear` on the seed set

**SEED, SYNTHETIC. Do not quote this as decision quality.** It was measured on
2026-09-30 with `run.py --engine linear --fit-linear --calibrate`:
- train is 52 synthetic rows;
- calib is 40 synthetic rows, and its thresholds are fitted in-sample;
- test is 15 battery rows lifted from existing repo tests;
- adversarial is 47 synthetic rows.

| split | n | acc [Wilson 95%] | NLL | Brier | ECE* | act[inline] @ thr 0.705 |
|---|---|---|---|---|---|---|
| calib (in-sample thresholds) | 40 | 0.875 [0.739, 0.945] | 0.195 | 0.128 | 0.053 | 15 acts, 0 errors, coverage 0.75 |
| test (battery) | 15 | 0.867 [0.621, 0.963] | 0.250 | 0.187 | 0.051 | 7 acts, 0 errors, coverage 0.70 |
| adversarial | 47 | 0.723 [0.582, 0.831] | 0.892 | 0.487 | 0.216 | 22 acts, **3 errors**, coverage 0.53 |

\* ECE is indicative only: n < 150, 5 bins.

On the adversarial split, `linear` would skip three real (implicit) spawns.
That is exactly what the `act_errors[inline] == 0` gate exists to block, so
this engine does not pass. The incumbent `rules` scores 1.000 on the battery
test split, because it was tuned on those phrasings, and 0.255 on the
adversarial split. The McNemar comparison between the two is p = 0.5 on test
and p < 0.001 on adversarial.

## Wiring (landed with the shared stack wiring; opt-in)

As specified below, with these as-built details: `start.sh` starts the scorer
then the service after the tool server and before the router, NON-fatally
(consumers fail open); the router gets `ROUTER_INSTINCT`, `INSTINCT_URL` and
`INSTINCT_KEY_FILE` (the config's key path) in its own environment only, and
start.sh notes when `ROUTER_INSTINCT` is on without `INSTINCT=true`;
`services.instinct` is present in `/api/slot` only while `INSTINCT=true`
(absent, not `false`, on a default rig, so its `/api/slot` is unchanged);
`healthcheck.sh --restart` restarts the service through `instinct.sh up`.
The spec as planned:

**`agents/router.py`** (about 6 lines; `ROUTER_INSTINCT` defaults to off, which leaves the router byte-identical):

```python
from instinct.routerhook import RouterInstinct      # agents/ is on sys.path
_INSTINCT = RouterInstinct()                          # reads ROUTER_INSTINCT=off|shadow|enforce
# at the decision site, identity gate unchanged and FIRST:
if user_text and _spawn_allowed(request.headers):
    hinted = bool(_HINTS.search(user_text))
    if await _INSTINCT.skip_classify(user_text, hinted):
        return await _proxy_through(request, client, raw)
    if hinted:
        spawn, task, workdir = await _classify(client, user_text)   # unchanged
        ...
```

When this lands, replace `test_router_py_is_unwired_today` in
`tests/test_router_instinct.py` with the byte-identity test for
`ROUTER_INSTINCT=off`.

**`scripts/lib/conf.sh`**: add these keys, and don't export them globally.
`ROUTER_INSTINCT` goes into the router's own environment only.

| Key | Default |
|---|---|
| `INSTINCT` | `false` |
| `INSTINCT_PORT` | `8094` |
| `INSTINCT_SCORER` | `false` |
| `ROUTER_INSTINCT` | `off` |
| `INSTINCT_CONFIG` | `agents/instinct/instinct.toml` |

**`start.sh` / `stop.sh`**:
- `INSTINCT_SCORER=true` runs `scripts/serve-instinct-scorer.sh` with a pidfile at `.run/instinct-scorer.pid` and a readiness probe on `/health`. It needs no GPU lease, because it runs on CPU.
- `INSTINCT=true` runs `scripts/instinct.sh up`, which does its own pre-bind check and pidfile. Start it after the scorer.
- `stop.sh` runs them in reverse order: `instinct.sh down`, then kill the scorer by pidfile.

**`scripts/doctor.sh`**: add four checks.
- `GET :8094/health`.
- `.run/instinct.key` is mode 0600.
- `/v1/instinct/engines` shows the scorer healthy.
- No decision's target is enforce while its effective mode is below it; print the reason if one is.

**`scripts/healthcheck.sh` / watchdog**: probe `:8094/health` when `INSTINCT=true`.

**`extensions/dashboard/dashboard.py` `services_status()`**: set
`out["instinct"] = GET 127.0.0.1:8094/health == 200`, as a **bool** (F6). Add a
`tests/test_beast_slot.py` case asserting it is a bool and that the top-level
key set is unchanged.

**`docs/REFERENCE.md`**: add the conf-key table above.

**`scripts/backends/conformance.sh --scoring`** (P1): wrap `Engine.probe()`.
The probes themselves are already implemented in `engines/_llm.py`.

## VERIFY on hardware (implemented to the documented belief, configurable)

| Belief | Where | Knob |
|---|---|---|
| `/completion` with `temperature:-1, n_probs:K` returns `completion_probabilities[0].top_logprobs[{id,token,logprob}]` from the pre-sampler, full-vocabulary softmax (F9) | `engines/llamacpp.py` | `n_probs` |
| llama.cpp `/tokenize {content, add_special:false, with_pieces:true}` parses ChatML specials in the text and returns `{id, piece}` | `engines/llamacpp.py` | `tokenize_path` |
| `/props` exposes `model_path` / `model_alias` for the identity probe | `engines/llamacpp.py` | binding `model` |
| `qwen3-nothink/1` equals the Qwen3-0.6B GGUF's own template, and `yes`/`no` are single tokens after `\n\n` (checked by the label lock at attach) | `render.py` | spec `prompt.format` |
| SGLang `/tokenize` takes `{text, add_special_tokens}` and returns `{tokens:[int]}` | `engines/sglang.py` | `tokenize_path` |
| SGLang `/v1/score` accepts `query: ""` with the full prompt as the one item | `engines/sglang.py` | — |
| Single-item requests on an MIS server are a valid SIS reference for the equivalence probe, unless `sis_url` names a separate SIS server | `engines/sglang.py` | `sis_url` |
| FlashInfer MIS works on sm_120 / sm_121a | R2/R3 | `exec` |
| The replay-std 0.02 threshold and the +2σ threshold guard band | `engines/_llm.py`, `service.py` | constants |
| CPU p95 fits the 600 ms router budget; `-t` = physical cores / 2 and `-ctk f16` are sane on the 0.6B | `serve-instinct-scorer.sh` | `INSTINCT_SCORER_THREADS` |
| A global `REASONING*` in `openbeast.conf` reaches the scorer through `serve.sh`. This is expected to be harmless, because raw `/completion` never uses the chat template. | `serve-instinct-scorer.sh` | — |
