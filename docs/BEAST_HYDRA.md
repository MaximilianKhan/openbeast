# beast-hydra: operating the inference router

beast-hydra (`agents/hydra.py`) is an opt-in, OpenAI-compatible router on
`127.0.0.1:8095`. It sits in front of one or more inference engines (the
rig's llama-server, vLLM on the Sparks, anything OpenAI-compatible on the
tailnet) and routes each request on its `model` field. This page covers
running it. The design, the reasoning behind it and the hardware beliefs
still to verify are in [`BEAST_HYDRA_PLAN.md`](BEAST_HYDRA_PLAN.md).

## Status

- Built and tested against fake engines (`tests/test_hydra_*.py`,
  `tests/test_hydra_sim.sh`) and against the rig's own llama-server.
- Not yet run against a DGX Spark or the 3090 Ti rig. The plan marks those
  beliefs **VERIFY**.
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
| `add-node <id> --url U --engine E ...` | Probe a node and print the TOML stanza to paste (never edits the file) |
| `conformance <deployment>` | Check that a deployment behaves as its engine claims |
| `pin-smoke [deployment\|all]` | A one-token and a streaming chat per deployment, through `/pin` |
| `sim` | The simulated fleet (`scripts/hydra-sim.sh`) |

Admin calls present the per-start local token (`.run/hydra-local.token`).
Inference calls present `LLAMA_API_KEY`.

## Health

`scripts/healthcheck.sh` reads hydra's `/health`: 200 is OK, and 503 means up
but no routable default route (a WARN; a restart cannot fix it, so the
watchdog never restarts it). No answer is DOWN, and `--restart` relaunches
it. `scripts/doctor.sh` prints a per-node, per-deployment and per-route table.

## beast-instinct

Hydra can ask beast-instinct (`:8094`, `INSTINCT=true`) which task class a
request is, over `instinct-route/1`. It only acts on an answer that is
`act` and `enforce`, and it works unchanged with instinct absent. See
[`BEAST_INSTINCT.md`](BEAST_INSTINCT.md).
