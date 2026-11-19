# beast-hydra: operating the inference router

beast-hydra (`agents/hydra.py`) is an opt-in, OpenAI-compatible router on
`127.0.0.1:8095`. It sits in front of one or more inference engines (the
rig's llama-server, TensorFold or vLLM on a DGX Spark, anything
OpenAI-compatible on the tailnet; `engine = llama | vllm | tensorfold |
openai`) and routes each request on its `model` field. This page covers
running it. The design, the reasoning behind it and the hardware beliefs
still to verify are in [`BEAST_HYDRA_PLAN.md`](BEAST_HYDRA_PLAN.md).

## Status

- Built and tested against fake engines (`tests/test_hydra_*.py`,
  `tests/test_hydra_sim.sh`) and against the rig's own llama-server.
- Not yet run against a DGX Spark or the 3090 Ti rig. The plan marks those
  beliefs **VERIFY**. The Spark pair (spark-hydra) does not run it: its
  command center talks to the TensorFold engine on `127.0.0.1:8000`
  directly, and nothing listens on `:8095` there (checked 2026-11-19).
- **Off by default.** With `HYDRA` unset or false, the stack is
  byte-identical to one without hydra (`tests/test_hydra_instinct_wiring.sh`).

## What changes when `HYDRA=true`

Every consumer on the rig talks to hydra instead of the engine:

| Consumer | Before | With `HYDRA=true` |
|---|---|---|
| Open WebUI (`MODEL_URL`) | llama-server `:8080` | hydra `:8095` (or the agent router `:8088`, whose upstream becomes hydra) |
| beast-gate (`EDGE_GATE=true`) | llama-server | hydra |
| Spawned agents (`AGENT_INFERENCE_URL`, unless set) | llama-server | hydra, with the route id (`HYDRA_DEFAULT_MODEL`, default `beast`) as the model |

`INFERENCE_URL` still means the engine this rig manages. Evals bypass hydra.
A deployment id, and the `/pin/<deployment>/v1` path, are strict: no rules,
no fallback, and a 503 when that deployment is down.

If WebUI or the gate misbehaves with hydra on, remember the extra hop:
WebUI → (router) → **hydra `:8095`** → engine. `scripts/hydra.sh status`
shows what hydra thinks of every node.

## Configuration

- `openbeast.conf` keys (`HYDRA`, `HYDRA_PORT`, `HYDRA_CONFIG`,
  `HYDRA_DEFAULT_MODEL`, `HYDRA_READY_GRACE`): [`REFERENCE.md`](REFERENCE.md).
- The fleet file: `hydra.toml` (or `HYDRA_CONFIG`). Its schema, with every
  field commented, is [`hydra.toml.example`](../hydra.toml.example). With no
  file, hydra builds an implicit single-node config from `INFERENCE_*`, which
  behaves like the stack without hydra plus provenance headers.
- An invalid config stops `./start.sh`. Validate first with
  `scripts/hydra.sh check`.

### Family policy

**Decision (Max, 2026-11-19): uncensored-preferred.** In Max's words:
"It's uncensored-preferred, as we want to enable researchers, lawyers, and professionals to get the most honest and neutral answers when weighing critical business decisions (some of these models have alignment and post-training that lead to wrong decisions)."

This replaces the 2026-09-30 "uncensored only" rule (#122). That rule
refused to route to any stock model. The Spark pair's default,
DeepSeek-V4.1-Flash (Mia EXL3), is a **stock** pack (`ABLIT=0`), so the old
rule would have refused it.

The policy is set in `[hydra]` in `hydra.toml`:

```toml
family_policy       = "uncensored-preferred"   # | "uncensored-only" | "any"
uncensored_families = ["qwen3.8-27b-uncensored", "glm-5.3-flash-uncensored"]
```

| `family_policy` | Effect |
|---|---|
| `uncensored-preferred` (default) | Every family may serve. The preference is a tie-breaker inside one priority group. First, every filter runs: health, breaker, conformance, caps, ctx, drains and rules. Then session or sticky affinity places its cache-warm deployment first. Among the candidates left with free capacity, an uncensored family goes before a stock one, ahead of least load. The same order holds in the engine-queue fallback, the failover tail and the ctx last resort (largest context first; uncensored wins a tie). Priority still wins: a stock target at priority 0 is tried before an uncensored one at priority 1. A down, drained, saturated or incapable uncensored target never blocks a stock one. |
| `uncensored-only` | The 2026-09-30 rule, now opt-in. A hard filter on every non-strict candidate: first choice, spill, sticky affinity, failover, the ctx last resort and after a rule hop. A route target outside `uncensored_families` is a config error. A stock deployment can be reached only by its strict id (a pin), and `check` warns about it. Once the routes span more than one family, `uncensored_families` is required. The proxy re-checks the policy before every failover attempt, so a reload into strict mode stops a stale plan. |
| `any` | Families play no part in routing. |

- `uncensored_families` declares which families are uncensored. A
  deployment's `family` is compared against it. List a family only when its
  weights really are abliterated. **DeepSeek-V4.1-Flash (Mia EXL3) is
  stock**, so `hydra.toml.example` gives it the family
  `deepseek-v4.1-flash`, which is not in the list.
- Under `uncensored-preferred`, with routes that span families and an empty
  list, `check` warns that the preference has nothing to prefer.
- `allowed_families`, the pre-2026-11-19 key, still loads as
  `uncensored_families`, and `check` warns. A config that only ever routed
  those families behaves the same under the new default. Add
  `family_policy = "uncensored-only"` to get the old hard filter back.
- None of the three policies changes a route's `same_family` / `family`
  anchor. `beast` in the example stays on the uncensored 27B family in every
  mode.
- Strict pins (a deployment id, `/pin/<d>/v1`, `X-Hydra-Pin`) never go through
  the policy.
- `/hydra/status` shows `family_policy` and `uncensored_families`. A decision
  trace notes `family_policy uncensored-preferred: uncensored first in group N`
  whenever the preference reordered a group.
- The example's `beast:spark` and `beast:long` routes list GLM (uncensored)
  and DeepSeek (stock) at the same priority on the pair's node. The pair
  serves one of them at a time, and the other is MISMATCH, so the one being
  served answers. If both ever answered, GLM would go first.

Tests: `tests/test_hydra_core.py`, the "family policy" section.

### Environment knobs (not in `openbeast.conf`)

| Variable | Default | Effect |
|---|---|---|
| `OPENBEAST_HYDRA_TRUSTED_HOSTS` | empty | Extra `Host` header values hydra answers to, comma-separated. Hydra always accepts loopback names, this machine's hostname and `*.ts.net`, and refuses any other `Host` with a 400 (the DNS-rebinding guard shared with beast-chat and beast-artifact, `agents/hostpolicy.py`). Add a name here when a client reaches hydra under a name that is none of those. |
| `OPENBEAST_HYDRA_RUN_DIR` | `.run` | Where the pid, the local admin token and the caller token live. |
| `OPENBEAST_HYDRA_LEASE_CMD` | `scripts/gpu-lease.sh` | The GPU-lease check for a node marked `gpu_lease = true`: while another process holds the lease, hydra drains that node. |

## `scripts/hydra.sh`

| Verb | What it does |
|---|---|
| `check [path]` | Validate `hydra.toml` (or the implicit config) |
| `status [--json]` | Nodes, deployments and routes, with health |
| `explain '<json>'` | Dry-run a routing decision for a request body |
| `reload` | Validate and swap the config; a bad file keeps the old one |
| `drain <node>` / `undrain <node>` | Stop or resume new traffic to a node |
| `decisions [n]` | Recent decision traces |
| `metrics` | Prometheus text |
| `tail [-f] [n]` | The audit log, pretty-printed (never prompt text) |
| `add-node <id> --url U --engine E ...` | Probe a node and print the TOML stanza to paste (never edits the file). Every value is written as an escaped TOML string, and a served id from the node that is not a plain model id (`[A-Za-z0-9._:/@+ -]`, at most 200) is left as `SERVED-ID` with a warning: the node is untrusted input |
| `conformance <deployment>` | Check that a deployment behaves as its engine claims |
| `pin-smoke [deployment\|all]` | A one-token and a streaming chat per deployment, through `/pin` |
| `sim` | The simulated fleet (`scripts/hydra-sim.sh`) |

Admin calls present the per-start local token (`.run/hydra-local.token`).
Inference calls present `LLAMA_API_KEY`. With `LLAMA_API_KEY` empty (the
`conf.sh` default) inference is open to every local process, and hydra says
so at start when it holds keys for any node. Browser pages are refused
either way: a request a browser marks cross-site (`Sec-Fetch-Site:
cross-site`, or an `Origin` whose host is not on hydra's Host allowlist) gets
a 403 `hydra_cross_site` before it reaches a node, and an engine's
`Access-Control-*` headers are not relayed. Requests the router or the gate
vouch for with the caller token are exempt; they do their own browser check.

## Health

`scripts/healthcheck.sh` reads hydra's `/health`: 200 is OK, and 503 means up
but no routable default route (a WARN; a restart cannot fix it, so the
watchdog never restarts it). No answer is DOWN, and `--restart` relaunches
it. `scripts/doctor.sh` prints a per-node, per-deployment and per-route table.

## Timeouts and the breaker

Each node has a first-byte deadline and a circuit breaker
([`hydra.toml.example`](../hydra.toml.example) has the fields). Four rules
are not obvious from the file:

- **The implicit single-node config waits as long as the stack did without
  hydra.** With no `hydra.toml`, the node's first-byte and non-stream
  deadlines are 580 s, because a one-slot engine queues a second request
  behind the first for as long as that takes. An explicit `hydra.toml` keeps
  the 120 s default unless it sets its own.
- **A request queued behind a busy node does not trip the breaker.** A
  first-byte timeout is not counted as a node failure when the request had
  exactly one candidate and that node was already full (in-flight ≥ its
  `slots`) when the request arrived: that is the engine's queue, not a
  failing node. This applies to **every** config, explicit `hydra.toml`
  included, not only the implicit one. Counting them opened the breaker on a
  rig that was merely busy, and every caller then got 503 for
  `breaker.open_s`. The caller still gets its timeout; the decision trace
  says `queued behind a saturated single candidate: not a breaker failure`.
  A timeout on a node with free capacity, or on a route with somewhere else
  to go, counts as before.
- **An engine's own JSON 500 about a request is not, by itself, a node
  failure.** A 500/501 whose body is a JSON `error` object (llama.cpp's
  answer when the chat template refuses the messages) still fails over, but
  it is charged to the breaker only if another deployment then serves the
  same body. When every target refuses it, the caller gets the engine's own
  status and body (audit outcome `upstream_5xx`) and no breaker moves: one
  caller with a malformed body cannot black out the route. A 500 with no
  body, a cut body or a non-JSON body, and any 502/503/504, count at once.
- **A HALF_OPEN trial lasts until the first byte, not the whole stream.**
  After `breaker.open_s` one request at a time is let through to test the
  deployment. Once it has its first upstream byte the next caller may become
  a trial too, so a long generation no longer 503s everyone else. The
  breaker still closes only on `success_threshold` completed requests, and
  a trial stream that dies mid-way reopens it.

`python3 agents/hydra.py --print-default-config` prints the implicit config,
580 s deadlines included and `slots` omitted when `INFERENCE_SLOTS` is unset
(the engine then judges a client's `id_slot`). Saved as a `hydra.toml`, that
output becomes an *explicit* config: an omitted `slots` means 1 and
`id_slot` is range-checked against it again.

Hydra never forwards identity headers (`X-OpenWebUI-*`,
`X-OpenBeast-Device`, `Tailscale-*`) to a node; it uses them for routing and
its own audit row only.

## beast-instinct

Hydra can ask beast-instinct (`:8094`, `INSTINCT=true`) which task class a
request is, over `instinct-route/1`. It only acts on an answer that is
`act` and `enforce`, and it works unchanged with instinct absent. See
[`BEAST_INSTINCT.md`](BEAST_INSTINCT.md).
