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

## Status: P0 built and wired (opt-in; nothing enforces until gated)

Decisions run on a **full model** (plan revision 2026-09-30, Max's call): the
rig's own 27B now, Open-Jev-27B on a GPU of its own next. See "Engine tiers".

| Piece | Where |
|---|---|
| Library: specs, rendering, label locks, core math, calibration, lifecycle | `agents/instinct/` |
| Engines: `rules`, `linear`, `llamacpp_logprobs`, `sglang_score`, `openjev_head` | `agents/instinct/engines/` |
| Service on `127.0.0.1:8094` | `agents/instinct/server.py` |
| Fail-open client and the router glue | `agents/instinct/client.py`, `routerhook.py` |
| Decisions | `agents/instinct/decisions/*.toml` |
| Hermetic stub scorer (llama.cpp + SGLang wire formats, fault injection) | `scripts/instinct/stub_scorer.py` |
| Reference hydra consumer | `scripts/instinct/mock_hydra.py` |
| Operator CLI | `scripts/instinct.sh` |
| CPU **fallback** scorer, Qwen3-0.6B (`start.sh` runs it when `INSTINCT_SCORER=true`) | `scripts/serve-instinct-scorer.sh` |
| Open-Jev-27B host launcher (a dedicated GPU host; pinned container + authenticating gate) | `scripts/serve-openjev.sh`, `scripts/instinct/openjev_gate.py`, `scripts/instinct/openjev/` |
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
| I7 | Engine URLs (`url` and `sis_url`) on the hydra (`:8095`), beast-gate (`:8443`) or router (`:8088`) port on any host and under any loopback spelling, or at `HYDRA_URL`, are refused. A URL equal to the primary (`INFERENCE_URL`, default `http://127.0.0.1:8080`) needs `allow_primary = true` (a real TOML bool), and then only a decision whose `policy.primary_use` is `async` or `substitute` may use it — checked when the chain is built AND on every call (below). | `test_instinct_spec.py`, `test_instinct_primary.py` |
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

The client sets `context.eval = true` itself whenever its process carries the
eval markers that `evals/run_eval.py` puts in every child's environment
(`OPENBEAST_EVAL`, `OPENBEAST_TASK_PATHS`). A caller inside an eval unit can't
opt out.

The response keys are frozen by `tests/test_instinct_contract.py`:
`contract, decision, decision_version, decision_hash, trace_id, request_id,
mode, enforce, action, answer{type,label,probabilities,raw_probabilities,calibrated,confidence{p_top,margin,shape},label_mass,labels_truncated,expected_value},
items, would{label,action}, fallback{used,reason}, engine{id,adapter,model,model_sha256,exec,label_token_ids},
cascade[{engine,action,reason,ms,hash?,label?,p_top?,probabilities?,label_mass?,mode?,error?}], latency_ms{queue,engine,total}`.

**A caller acts only on `enforce == true`.**

The cascade walks the chain under the deadline. The answer is the first `act`
whose engine's own lifecycle reaches the request's target mode (the lower of
the spec's mode and the ceiling). An `act` from an engine that can only shadow
(for example `linear` without a gate) does not hide a later engine that can
enforce. If nothing can enforce, the answer is the first `act` (what the
service *would* have done), else the last result from a probabilistic engine,
else `rules`. When the target can act (canary/enforce) the walk stops at the
answer; below that (shadow/off) it goes on through the whole chain, so shadow
data measures every engine, including on rows an earlier engine was confident
about. Every attempted engine's own `probabilities`, `label_mass` and the
16-char `hash` it scored under are kept in its cascade entry. Any exception from an engine, including a malformed
response, is a `fallback` entry for that engine and never an HTTP 500. An
unexpected `/tokenize` shape fails that engine's label lock and never stops
the service.

A calibration record's fitted thresholds can only *raise* the spec's
`policy.act` value. The spec value is the reviewed floor. A fitted `null`
means the fit found no feasible threshold, and the label never acts.

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
- `engine_busy` (the primary's slot was serving someone), `primary_async_only`, `primary_not_substitute` (cascade-entry reasons for a primary engine the rules kept out)
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
- the whole engine identity: adapter, model sha AND revision, exec mode, the
  SGLang request shape (`score_query`), image digest, engine commit, MIS
  delimiter, and for `openjev_head` the adapter revision, head sha256 and
  loader digest.

A retrained `linear` model, a new tokenizer, an SGLang server forced from MIS
to SIS or an image bump all produce a new hash. The decision then drops to
shadow on its own.

Promotion and demotion:
- **Promote** (`scripts/instinct.sh promote D --engine E`) only checks, with the service's own rules: a calibration record and a gate record for this exact hash — the gate computed against THAT calibration, passed, committed; the `gate.shadow` soak (`min_decisions` ledger rows this engine scored under this hash, spanning `min_days`); a passing conformance probe; no demotion; the spec edited to `mode = "enforce"`. Then commit and SIGHUP. It never edits a file.
- **Demote** (`scripts/instinct.sh demote D`) writes `.run/instinct/demoted.json` and sends SIGHUP. No commit is needed. An id the registry does not know is refused (`--force` records it anyway), so a typo in an emergency never reports success.
- **Auto-demotion** happens when the fallback/timeout rate exceeds 5% over the last 100 calls, or when `label_mass` p50 falls more than 0.2 below the calibration reference over the last 200 calls.
  - A call counts as failed when *any* engine it attempted timed out or errored, even if `rules` then answered, or when the call was shed.
  - `label_mass` is watched per engine, against that engine's own reference, over every row it scored.
  - Auto-demotions persist to `.run/instinct/auto-demoted.json`: a reload AND a restart keep them. One ends only when that decision's chain or a known engine hash changes (a hash that is unknown because the engine is down is not a change), or on `undemote`. `stats` shows them under `_auto_demotions`, and a demoted decision's fallback reason is `demoted`.
- `probe_interval_s = 0` turns periodic conformance off. An LLM engine then never counts as freshly probed, so it can never enforce.

## Operating it

```bash
scripts/instinct.sh stub                              # hermetic scorer on :18082 (plumbing)
INSTINCT_ENGINE_OVERRIDE=stub scripts/instinct.sh up  # service on :8094, mints .run/instinct.key
scripts/instinct.sh status
python3 evals/decisions/run.py --decision router.spawn_intent --engine linear --fit-linear --calibrate
python3 evals/decisions/run.py --decision router.spawn_intent --engine rig-27b --calibrate   # the 27B primary
python3 evals/decisions/loadgen.py --decision router.spawn_intent --engine rig-27b --qps 0.1,0.2,0.5 \
    --with-primary-decode --primary-url http://127.0.0.1:8080 --out load.json
python3 evals/decisions/run.py --decision router.spawn_intent --engine rig-27b --gate --load-report load.json
python3 evals/decisions/run.py --decision router.spawn_intent --engine rig-cpu --calibrate   # the 0.6B fallback
scripts/instinct.sh down
```

A gate always fails closed on:
- a missing loadgen report (when one is given, its path and sha256 are recorded in the gate record), or one that is for another decision, engine or `decision_hash`, or whose calls failed more than 1% of the time (its p95 covers only the survivors);
- `min_n` not met;
- an LLM engine that doesn't beat `linear` with significance (a significant *loss* never passes);
- a criterion over a composite split (`test+ood+adversarial`) with any empty component;
- a dataset whose MANIFEST `status` is not `gated`, or a split a criterion reads that is not sha-pinned in MANIFEST `[files]`;
- an LLM engine gated with `--no-probe`.

The harness scores the population the service serves. Rows whose facts force
a mechanical label are removed before any engine is scored (`mechanical_excluded`
in the report). Every other row has its mechanical labels zeroed by the same
`core.mask_mechanical` the service uses.

## Seed baseline: `linear` on the seed set

**SEED, SYNTHETIC. Do not quote this as decision quality.** It was measured on
2026-09-30 with `run.py --engine linear --fit-linear --calibrate` (re-run the
same day at the effective threshold — the fitted 0.705 can only RAISE the
reviewed floor `inline = 0.90`, so 0.90 is what acts):
- train is 52 synthetic rows;
- calib is 40 synthetic rows, and its thresholds are fitted in-sample;
- test is 15 battery rows lifted from existing repo tests;
- adversarial is 47 synthetic rows.

| split | n | acc [Wilson 95%] | NLL | Brier | ECE* | act[inline] @ thr 0.90 |
|---|---|---|---|---|---|---|
| calib (in-sample thresholds) | 40 | 0.875 [0.739, 0.945] | 0.195 | 0.128 | 0.053 | 11 acts, 0 errors, coverage 0.55 |
| test (battery) | 15 | 0.867 [0.621, 0.963] | 0.250 | 0.187 | 0.051 | 6 acts, 0 errors, coverage 0.60 |
| adversarial | 47 | 0.723 [0.582, 0.831] | 0.892 | 0.487 | 0.216 | 17 acts, **3 errors**, coverage 0.39 |

\* ECE is indicative only: n < 150, 5 bins.

On the adversarial split, `linear` would skip three real (implicit) spawns.
That is exactly what the `act_errors[inline] == 0` gate exists to block, so
this engine does not pass. The incumbent `rules` scores 1.000 on the battery
test split, because it was tuned on those phrasings, and 0.255 on the
adversarial split. The McNemar comparison between the two is p = 0.5 on test
and p < 0.001 on adversarial.

## Engine tiers (plan revision 2026-09-30)

Decisions route into a **full model**, not a small one. For
`router.spawn_intent` the chain is `["rig-27b", "rig-cpu", "linear", "rules"]`:

| Tier | Binding | What | Role |
|---|---|---|---|
| target | `openjev` (commented in `instinct.toml`) | Open-Jev-27B-v1.1 — LoRA + scalar decision head on the Qwen3.8-27B backbone — run **locally** by `scripts/serve-openjev.sh` on a GPU of its own | first in the chain once its host exists and it is re-validated (below) |
| interim default | `rig-27b` | the rig's own primary 27B (Qwen3.8-27B-Uncensored MTP Q5), answer-boundary logprobs through `llamacpp_logprobs` | substitute engine: replaces the router's generative classify on that same model |
| fallback | `rig-cpu` | Qwen3-0.6B on CPU (`INSTINCT_SCORER=true`) | answers only when the 27B is skipped (busy slot, deadline, failed identity probe) |
| baseline | `linear` | tier-0 bag-of-words | McNemar reference; shadow-only until it clears its gate (it does not: 3 adversarial errors) |
| incumbent | `rules` | today's behaviour | always last |

Never TypeSafe's hosted Jev API: cloud engines are forbidden. All our served
models are uncensored, so every 27B binding names the uncensored weights.

**Why the primary may score a synchronous decision now.** The old rule let a
primary binding serve only `async_only` decisions, because a decision call
competes for the primary's single (`-np 1`) MTP slot. But `router.spawn_intent`
REPLACES a call the router already makes on that same slot — the generative
`json_schema` classify on hinted turns — so scoring it there adds no primary
load; a one-token prefill costs less than ~20 decoded JSON tokens. The spec
says so explicitly and the service enforces it:

- `policy.primary_use = "none" | "async" | "substitute"` (default `none`;
  `async_only = true` is a one-version alias of `async`).
- `substitute` needs `policy.substitutes = "<file>:<function>"` — the replaced
  call site (`agents/router.py:_classify`).
- At chain build, a primary binding is dropped from a `none` decision. On every
  call, a primary engine is skipped for an `async` decision whose caller can
  act on the answer (`primary_async_only`), and for a `substitute` decision
  unless the caller declares it would make the substituted call
  (`baseline = "hint"`, else `primary_not_substitute`).
- `busy_skip = true` (primary bindings only): each call first asks `GET /slots`;
  a slot with `is_processing` (a user's turn, an agent) raises `engine_busy` in
  about a millisecond and the chain moves to the 0.6B — never queueing behind
  the user. A busy call records no latency sample and is not an engine fault.
  The periodic conformance probe is deferred the same way, and runs 3 replays
  on a primary instead of 10 (each probe call swaps the conversation out of
  the slot).
- `key_env = "LLAMA_API_KEY"` (loopback URLs only): the primary's bearer key
  reaches the service through its environment. `scripts/instinct.sh up`
  resolves it the way `conf.sh` does (env `OPENBEAST_API_KEY`, else
  `openbeast.conf`'s `LLAMA_API_KEY`) and never puts it on argv.
- A default-model swap changes `/props`' alias, the identity probe fails, and
  the chain falls to the 0.6B on its own.

The hydra decisions (`task_class`, `pool_fit`) keep their 25 ms deadlines,
which no 27B prefill can meet, so they stay on `linear`/`rules` (their P3
SGLang binding is re-aimed at the uncensored 27B and still refused until
pinned). Whether to raise those deadlines for a 27B box is an open question in
the plan (§8).

### Open-Jev host (`scripts/serve-openjev.sh`)

Open-Jev needs HF BF16 weights (~54 GB) and its own loader (torch + peft), so
it never runs in the rig's llama.cpp stack and never touches the rig's Python
lock. On a DGX Spark, or the 5090 once the Sparks serve generation:

```bash
docker build -t openbeast-openjev:1 scripts/instinct/openjev     # on the GPU host
OPENJEV_IMAGE=<repo>@sha256:<digest> OPENJEV_CHECKPOINT_DIR=…/package/checkpoint \
OPENJEV_HF_CACHE=<hub cache with the base> scripts/serve-openjev.sh up --gpu 0 --bind <tailnet-ip>
scripts/serve-openjev.sh status | down
```

It verifies the checkpoint (adapter + head sha256 from the Open-Jev-27B-v1.1
card, rev `28cf7306…`), refuses an unpinned image, a stack port, `0.0.0.0` and a
host already running `llama-server`, then starts the loader in the container
(loopback-published, weights read-only, `HF_HUB_OFFLINE=1`) behind
`scripts/instinct/openjev_gate.py`: bearer key from a 0600 file, a path
allowlist (`/health`, `/v1/identity`, `POST /v1/systemone`) and an identity
endpoint the binding's probe checks. The base is the **uncensored**
Qwen3.8-27B; the package's `model.json` (which pins the stock base) is
rewritten in a derived copy, never in place.

**The adapter was trained on the STOCK base.** Abliteration edited `o_proj`
(a LoRA target) and the residual stream the head reads, so on the uncensored
base it must be re-validated with `evals/decisions` — A/B against the stock
base (`--base stock --validation-only`, loopback only) — before any
calibration counts. Until a gate record exists for its exact hash it can only
shadow, by construction.

## Wiring (as built; opt-in)

`start.sh` starts the scorer, then the service, after the tool server and
before the router, NON-fatally (consumers fail open). The router gets
`ROUTER_INSTINCT`, `INSTINCT_URL` and `INSTINCT_KEY_FILE` (the config's key
path) in its own environment only, and start.sh notes when `ROUTER_INSTINCT`
is on without `INSTINCT=true`. `services.instinct` is present in `/api/slot`
only while `INSTINCT=true` (absent, not `false`, on a default rig, so its
`/api/slot` is unchanged). `healthcheck.sh --restart` restarts the service
through `instinct.sh up`; `stop.sh` runs `instinct.sh down`, then stops the
scorer by pidfile. doctor checks `/health`, the key's mode, the engines and
any decision held below its target.

**`agents/router.py`** (`ROUTER_INSTINCT` defaults to off, which leaves the
router byte-identical — `test_router_off_is_byte_identical`):

```python
from instinct.routerhook import RouterInstinct      # agents/ is on sys.path
_INSTINCT = RouterInstinct()                          # reads ROUTER_INSTINCT=off|shadow|enforce
# at the decision site, identity gate unchanged and FIRST:
if user_text and _spawn_allowed(request.headers):
    hinted = bool(_HINTS.search(user_text))
    turn = await _INSTINCT.consult(user_text, hinted)
    if turn.skip:
        return await _proxy_through(request, client, raw)
if hinted:
    spawn, task, workdir = await _classify(client, user_text)   # unchanged
    _INSTINCT.classified(turn, spawn)                           # paired baseline
    ...
```

- Only **hinted** turns are scored: the decision's engine is the primary, and
  an unhinted turn would add a primary call that replaces nothing.
  `ROUTER_INSTINCT_SHADOW_UNHINTED=true` restores fire-and-forget shadow on
  unhinted turns (the service keeps the primary out of those).
- A hinted turn's decide is **awaited** before the classify, in shadow as in
  enforce, so on a `-np 1` primary it serializes with the classify instead of
  racing the user's turn for the slot. Shadow is capped at 2 in flight and
  drops beyond that; everything is bounded by 600 ms and fails open.
- After the classify, its verdict goes back on the decide's `trace_id`
  (`POST /v1/instinct/feedback {trace_id, request_id, outcome: {source:
  "classify", label}}`), and each decide carries a `request_id`, which also
  makes `canary_pct` usable for the router.

Conf keys (`scripts/lib/conf.sh`, `docs/REFERENCE.md`): `INSTINCT`,
`INSTINCT_PORT`, `INSTINCT_SCORER`, `INSTINCT_CONFIG`, `ROUTER_INSTINCT` (the
router's environment only). `ROUTER_INSTINCT_SHADOW_UNHINTED` is not a conf
key: it is read from the router process's own environment, for a deliberate
experiment only (start.sh does not pass it).

## VERIFY on hardware (implemented to the documented belief, configurable)

| Belief | Where | Knob |
|---|---|---|
| `/completion` with `temperature:-1, n_probs:K` returns `completion_probabilities[0].top_logprobs[{id,token,logprob}]` from the pre-sampler, full-vocabulary softmax (F9) | `engines/llamacpp.py` | `n_probs` |
| llama.cpp `/tokenize {content, add_special:false, with_pieces:true}` parses ChatML specials in the text and returns `{id, piece}` | `engines/llamacpp.py` | `tokenize_path` |
| `/props` exposes `model_path` / `model_alias` for the identity probe | `engines/llamacpp.py` | binding `model` |
| `qwen3-nothink/1` equals the served checkpoint's own template (the Qwen3-0.6B GGUF's AND the Qwen3.8-27B-Uncensored's), and `yes`/`no` are single tokens after `\n\n` (checked by the label lock at attach, per engine) | `render.py` | spec `prompt.format` |
| SGLang `/tokenize` takes `{text, add_special_tokens}` and returns `{tokens:[int]}` | `engines/sglang.py` | `tokenize_path` |
| SGLang `/v1/score` accepts `query: ""` with the full prompt as the one item | `engines/sglang.py` | binding `score_query = "prompt"` (the prompt as the query plus one empty item; it enters `decision_hash`) |
| Single-item requests on an MIS server are a valid SIS reference for the equivalence probe, unless `sis_url` names a separate SIS server. When the probe forces `sis`, rank requests are sent one item per request on that same reference path. | `engines/sglang.py` | `sis_url` (same scheme and host as `url`, because it receives the key) |
| Before 20 latency samples, the deadline skip uses the worst sample and ignores one outlier once 10 samples exist, so a single timeout doesn't bench an engine until the next probe | `engines/__init__.py` | — |
| FlashInfer MIS works on sm_120 / sm_121a | R2/R3 | `exec` |
| The replay-std 0.02 threshold and the +2σ threshold guard band | `engines/_llm.py`, `service.py` | constants |
| CPU p95 fits the 600 ms router budget; `-t` = physical cores / 2 and `-ctk f16` are sane on the 0.6B | `serve-instinct-scorer.sh` | `INSTINCT_SCORER_THREADS` |
| A global `REASONING*` in `openbeast.conf` reaches the scorer through `serve.sh`. This is expected to be harmless, because raw `/completion` never uses the chat template. | `serve-instinct-scorer.sh` | — |
| **rig-27b:** spawn_intent p50/p95 on the idle 27B at 128/512/1,600 prompt tokens fits the 600 ms deadline (estimate ~180 ms at 512 tokens, ~580 ms at 1,600 — not measured); compare with the generative classify on the same rows | `instinct.toml` `rig-27b` | `timeout_ms`, spec `deadline_ms` |
| **rig-27b:** the cost of the slot swap: each call to the `-np 1` primary saves the conversation to host RAM (`--cache-ram`, 8 GiB default) and restores it on the next turn; find the context size where that stops fitting | `serve.sh` | `--cache-ram` |
| **rig-27b:** MTP leaves the first token's `n_probs` untouched (`n_predict: 1` stops before any draft); label locks and replay std identical with the MTP and non-MTP serve scripts | `engines/llamacpp.py` | — |
| **rig-27b:** `GET /slots` answers within 50 ms while the primary decodes (a timeout counts as busy), and `is_processing` is the field at the pinned tree | `engines/llamacpp.py` | `busy_skip` |
| **rig-27b:** under load (`loadgen --with-primary-decode`): fraction skipped `engine_busy`, the primary's tok/s change, the 0.6B beside it | `loadgen.py` | — |
| **openjev:** the loader accepts a `model.json` naming the uncensored base (it pins the stock revision); the image builds for the host arch (aarch64 on a Spark) with a CUDA torch for its GPU; the package layout mounts as documented | `serve-openjev.sh`, `scripts/instinct/openjev/` | `OPENJEV_BASE_*` |
| **openjev:** `/v1/systemone` shape (`answers.<id>.noul` / `.probabilities`), and that `state` = the filled template, `instructions` = the spec's system text is a good mapping for Open-Jev's prompt | `engines/openjev.py` | — |
| **openjev:** the adapter trained on the stock base still holds on the uncensored base (accuracy within 1 pt, ECE <= 0.08, >= 97% per-row agreement on the Open-Jev test/OOD sets and our seed set); if not, re-train r8 LoRA + head on the uncensored base with the Open-Jev recipe | `evals/decisions` | `OPENJEV_*_SHA256` |
