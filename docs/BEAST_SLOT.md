# beast-slot — client/server OpenBeast

**Status: SHIPPED 2026-07-30.** Supersedes [MAC_CLIENT_PLAN.md](MAC_CLIENT_PLAN.md)
(the thin-client prototype) with a first-class client/server architecture.

## The concept

The rig stays the fully-armed **command center** — every service it runs today
(llama.cpp, identity tool server, Open WebUI, SearXNG, extensions) keeps
running, unchanged. **beast-slot** is what the rig additionally publishes to
your tailnet: its intelligence as a consumable surface.

```
COMMAND CENTER (rig)                             CLIENT (any Mac/Linux device)
llama-server :8080                               opencode + mcp_server.py (stdio)
     ▲                                                  │
     │  EDGE_GATE=true                                  │
beast-gate :8090 ◀──── tailscale :8443 ◀────────────────┘  inference only
     ▲   per-device keys · path allowlist
     │   rate caps · inference audit
     └── (EDGE_GATE=false: :8443 maps straight at llama-server —
          its WHOLE route table, see "What beast-slot access grants")

dashboard   :3002 ◀──── tailscale :8444 ◀───── client.sh status   (discovery)
SearXNG     :8888 ◀──── tailscale :8889 ◀───── web_search          (default)
                        (or --local-search: client runs its own SearXNG)

tools execute on the CLIENT — bash/read/write/edit act on the client's files
inference happens on the RIG — only chat/completion calls cross the tailnet
```

The load-bearing fact: **tools execute where the process runs, not where the
model runs.** The machine boundary is one OpenAI-compatible HTTP call. A
spawned agent on the laptop edits laptop files while thinking on the rig's
GPU.

**Data flow, stated plainly:** file contents the agent reads on the client are
sent to the rig as model context — the model must see data to reason about it.
Both machines are on your tailnet, so the promise is **"nothing leaves your
tailnet"**, not "nothing leaves this machine". Never expose any of this with
`tailscale funnel` — the tailnet is the security perimeter.

## What beast-slot access grants

**Inference, not filesystem.** Tools are executed by frontends (the client's own
MCP process), never by llama-server, so a tailnet device that reaches `:8443`
cannot touch the rig's files or tools — those sit behind the separate `:3001`
identity server, which is never published. See docs/RBAC_PLAN.md.

**But `:8443` is NOT "chat-only".** `tailscale serve --https=8443 →
127.0.0.1:8080` mounts `/`, publishing llama-server's *entire* route table to
every tailnet device. Verified on the reference rig: `/health`, `/props`,
`/slots`, `/v1/models`, and `GET /lora-adapters` all answer 200. Notably
reachable:

| Route | Risk |
|---|---|
| `GET /slots`, `/props` | Other sessions' metadata: context size, token counts, sampling params. Prompt *text* stays redacted unless `LLAMA_SERVER_SLOTS_DEBUG` is set — never set it on a published rig |
| `GET /metrics` | llama-server's Prometheus counters (we pass `--metrics` so the slot contract can report real queue depth). Aggregate only — no prompts — but it is a new surface on the raw path |
| `POST /lora-adapters` | Global model mutation — affects every user of the rig |
| `GET/DELETE /v1/stream/:conv_id` | Sessions are keyed only by a caller-chosen `X-Conversation-Id`; knowing an id lets you read or cancel that generation. Enumeration is blocked (`/v1/streams/lookup` only answers for ids you supply), guessing is not |
| `/infill`, `/embeddings`, `/rerank`, `/tokenize` | Extra compute surface beyond chat |

Gated on our config and safe as shipped: `POST /slots/:id` needs
`--slot-save-path` (never passed) and `POST /props` needs `--props` (off).

On a personal tailnet where you own every device, this is acceptable — it is
the same trust boundary as the chat endpoint itself. On a tailnet with users
or devices you do not own, a bearer key (below) gates all of it behind one
shared secret but does **not** shrink the surface. The fix is
**[beast-gate](#beast-gate--the-identity-aware-inference-edge)** — shipped,
opt-in, and documented below: it allowlists the OpenAI routes and gives each
device its own revocable key.

**`id_slot` is hostile input.** llama-server accepts a client-chosen `id_slot`
on completion requests. It is unauthenticated, wraps modulo the slot count
(so an out-of-range value silently lands on someone else's slot), and a client
that always pins the same slot jumps the deferred queue ahead of unpinned
callers. Harmless at `-np 1`; strip it at the proxy before serving multiple
tenants.

## Server side (the rig)

```bash
./scripts/setup-tailscale.sh                     # :443 WebUI, :8443 inference
./scripts/setup-tailscale.sh --publish-searxng   # + :8889 search for clients
./scripts/ext.sh enable dashboard                # slot API lives in the dashboard
                                                 # (must precede --publish-slot; see below)
./scripts/setup-tailscale.sh --publish-slot      # + :8444 discovery API
```

**Order matters.** The slot API is served by the dashboard *extension*, and
`EXTENSIONS` is empty by default. Publishing before enabling it (and
restarting) leaves `:8444` proxying to a port nothing is listening on, so
clients get **502**, not a clean "not published" — `setup-tailscale.sh`
warns but does not block. `openbeast-client status` treats a missing slot
API as informational, so a client will look healthy while silently flying
blind about what the rig is serving.

### The `/api/slot` discovery contract (version 2)

`GET https://<rig>:8444/api/slot` — read-only JSON. `--publish-slot` mounts
*only* this path (`tailscale serve --set-path`), so the dashboard's HTML page
and `/api/status` stay rig-local:

```json
{
  "beast_slot": 2,
  "min_client": 1,
  "healthy": true,
  "model": {"id": "heretic-v2-27b-mtp-q6", "ctx": 212992},
  "slots": {"total": 1, "busy": 0},
  "capacity": {
    "ctx_shared": true,
    "ctx_total": 212992,
    "queue_deferred": 0,
    "serving_profile": "mtp-single-slot"
  },
  "services": {"model": true, "tools": true, "webui": true, "search": true},
  "auth": "open"
}
```

**v2 is purely additive** — every v1 field keeps its name and meaning, so v1
clients keep working and `min_client` stays 1. Bump `min_client` only when a
field is removed or its meaning changes. A client should compare its own
understood version against `beast_slot` (newer rig → unknown fields ignored,
still safe) and against `min_client` (rig demands newer → update the client);
`openbeast-client status` does exactly this and warns without failing.

- `model.id` is the **real loaded model** (from `/v1/models`, falling back to
  `/props`) — llama-server ignores the model name clients request, so this is
  how a client knows what it's actually talking to.
- `slots` is **data, not an assumption**. Today's MTP default serves `-np 1`:
  one slot, concurrent requests (your WebUI turn + a remote client's turn)
  queue FIFO. Switching between conversations is cheaper than it sounds — the
  server saves the displaced conversation's KV state into a global RAM prompt
  cache (8 GiB default) and restores it later, so a handoff costs a RAM copy,
  not a full reprocess. A multi-slot profile answers the **same shape** with
  bigger numbers and `serving_profile: "batched-multi-slot"`; clients must not
  hard-code 1.
- **`capacity` answers "how much can this rig actually take", which
  `slots.total` × `model.ctx` does not.** Under `--kv-unified` (our default)
  every slot advertises the *full* context while all slots draw on ONE pool,
  so a 6-slot rig publishing `ctx: 358400` has 358400 tokens **shared**, not
  6×. `ctx_shared` tells you which arithmetic applies and `ctx_total` does it
  for you; both are `null` when the launch path can't be read — never a guess.
  Note the sharp edge `capacity` describes but cannot fix: when the unified
  pool runs dry, llama-server purges the *lowest-numbered* idle slot's cache,
  deterministically. For real per-tenant isolation serve
  `--no-kv-unified -np N -c (N × per-tenant)` and accept the lower efficiency.
- `slots.busy` counts only *in-flight* generations, so it is a liveness
  signal; `capacity.queue_deferred` is the load signal (llama-server's
  `requests_deferred`). It is `null` unless the server runs with `--metrics`,
  which `serve.sh` now passes by default.
- On `--no-slots`, `busy` is `null` but `ctx` still resolves from `/props`.
- Never contains prompt text, sampling params, or key material.

## Client side (any Mac/Linux device)

```bash
# on the tailnet, then:
git clone https://github.com/MaximilianKhan/openbeast && cd openbeast
./scripts/setup-client.sh                        # auto-detects the rig ('beast')
```

Or without a clone: fetch just the script and it makes its own slim checkout.
Flags: `--host <fqdn>` (multiple rigs / non-default name), `--api-key <key>`
(keyed rig), `--no-search`, `--local-search` (own SearXNG container via Docker
Desktop/Engine — bridge network, loopback-only port map), `--uninstall`.

What lands: an isolated venv + slim checkout under `~/.openbeast-client`,
`~/.openbeast-client.env` (0600), non-clobbering `opencode.json` merge (the
full model list + the local MCP tool server as a stdio subprocess — no daemon,
no open port, dies with OpenCode), and `~/.local/bin/openbeast-client` when
that directory exists.

### The client CLI

```bash
openbeast-client status        # client doctor + live beast-slot view
openbeast-client agent "task"  # CLI agent HERE, thinking on the rig
openbeast-client search up     # local SearXNG lifecycle (--local-search installs)
openbeast-client update        # refresh checkout + pinned deps
openbeast-client uninstall
```

`status` checks: env file, venv imports, tailscale up, rig API reachable
(with bearer when keyed), beast-slot discovery, search reachable, opencode
wiring. RBAC deliberately does not apply to the client path — it's your
device; `OPENBEAST_MCP_TOOLS` is the scoping lever if a shared client ever
needs one.

## beast-gate — the identity-aware inference edge

Everything above describes the *raw* topology, where `:8443` maps straight to
llama-server. That is fine for a rig only you can reach. The moment more than
one person or device shares it, three facts bite (all verified in the vendored
llama.cpp source):

- **llama-server has no users.** One flat API key, no per-caller attribution,
  no per-caller limits. Every WebUI user, laptop, and spawned agent collapses
  into one anonymous caller.
- **Slot assignment is opportunistic**, by longest-common-prefix then LRU —
  never by identity. And `id_slot` from a client is unauthenticated, wraps
  modulo the slot count onto someone else's slot, and jumps the deferred queue.
- **There is no admission control.** The task queue is an unbounded deque with
  no per-request timeout and no preemption, so one remote agent loop can
  monopolize the rig indefinitely.

`agents/edge.py` is the missing hop — the one place on the inference path
where identity exists. Enable it:

```bash
# rig
echo "EDGE_GATE=true" >> openbeast.conf
./scripts/clients.sh enroll laptop-air --label "MacBook Air"   # prints the key ONCE
./stop.sh && ./start.sh -d
./scripts/setup-tailscale.sh          # repoints :8443 at the gate

# client
./scripts/setup-client.sh --api-key <the key from enroll>
```

What each remote request now passes through:

| Control | Behavior |
|---|---|
| **Per-device keys** | Bearer key per device, matched against sha256 in `.run/clients.json`. Hot-reloaded — `clients.sh revoke` blocks the **next** request from that device within seconds, with no llama-server restart (a restart would destroy your KV cache and every live stream). It does **not** kill a generation already streaming; that request runs to completion. To cut one off immediately, restart the gate (`pkill -f agents/edge.py`) — the local stack is unaffected |
| **Path allowlist** | Only `/health`, `/v1/models`, `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`. Everything else is **404** — not 403, so a remote caller learns nothing about what exists. `/lora-adapters`, `/slots`, `/props`, `/v1/stream`, `/infill` stop existing for remote callers |
| **Tenancy knobs** | Client `id_slot` stripped unconditionally; re-injected only from the server-side device→slot map |
| **Session isolation** | `X-Conversation-Id` namespaced per device, so two devices can neither collide on nor cancel each other's stream sessions |
| **Admission control** | Token bucket (`EDGE_RATE_LIMIT`, default 120/min) plus an in-flight cap (`EDGE_MAX_INFLIGHT`, default 2) per device → 429 with `Retry-After` |
| **Audit + metering** | One line per completion in `.run/inference-audit.jsonl`: `request_id`, `device`, `device_uid`, `user_claimed`, model, status, duration, prompt/completion tokens (the gate sets `stream_options.include_usage` so the streaming path meters too). Never content, never key material. Prometheus at `/gate/metrics`. Rotated by `scripts/logrotate-openbeast.conf` |
| **Attribution rules** | `device` is the **authenticated** identity. `device_uid` binds the *enrollment*, so a removed-and-re-enrolled `laptop-air` is a distinct device in the trail rather than inheriting its predecessor's history — **join on `device_uid`**, not `device`. `user_claimed` is a client-supplied header and is not proof of anything. `request_id` is echoed to the caller as `X-OpenBeast-Request-Id`, so "what happened to my 11:04 request" is answerable exactly. Unauthenticated rejects are counted in metrics and logged with a key fingerprint, but deliberately not written to the audit file — otherwise any tailnet peer could grow it without bound |
| **Introspection** | `/gate/health` and `/gate/metrics` require an enrolled key from non-loopback callers (they carry the device roster and per-device usage). Loopback — start.sh, healthcheck, doctor — is exempt |

**Fails closed.** With `EDGE_GATE=true` and no devices enrolled, remote callers
get 401 — an empty registry never means "everyone is welcome". `EDGE_ALLOW_ANON=true`
opts out, at the cost of attribution and revocation. A corrupt or half-written
registry keeps the last good device map rather than opening up.

**Your local command center is untouched.** Open WebUI and the agent router
keep talking to llama-server on loopback exactly as before. Enabling the gate
changes what the *tailnet* sees, not what the rig does.

### Device enrollment

```bash
./scripts/clients.sh enroll <id> [--label "…"] [--slot N] [--rate N]
./scripts/clients.sh list
./scripts/clients.sh revoke <id>      # lost laptop — effective in seconds
./scripts/clients.sh rotate <id>      # new key, same device identity
```

The registry (`.run/clients.json`, mode 0600) stores **only the sha256** of
each key — a key is printed exactly once, at enroll time, and is not
recoverable. Revoked devices stay in the file so the audit trail keeps
resolving their id.

### Per-device slot affinity

`--slot N` at enroll time pins a device to a specific llama-server slot. The
gate injects it server-side (never trusting the client's). Two caveats worth
internalizing before relying on it:

- It only means anything with a **multi-slot serving profile** (`-np > 1`).
  Our MTP default is `-np 1`, where every request lands on slot 0 anyway.
- Under `--kv-unified`, pinning reserves a **slot, not KV**. Slots share one
  pool, and pool exhaustion purges the lowest-numbered idle slot's cache. For
  real isolation serve `--no-kv-unified -np N -c (N × per-tenant)`.

## Keyed mode (optional, off by default)

Tailnet device identity is the default boundary. Enable a bearer the day your
tailnet includes devices or users you don't fully own:

```bash
# rig: set the key and restart
echo "LLAMA_API_KEY=$(openssl rand -hex 32)" >> openbeast.conf && chmod 600 openbeast.conf
./stop.sh && ./start.sh -d
# client: re-run setup with the key
./scripts/setup-client.sh --api-key <the-key>
```

When `LLAMA_API_KEY` is set, the whole stack presents it: serve.sh passes
`--api-key`, WebUI (compose), healthcheck, the dashboard's probes, the
router's classify call, the agent runner (`OPENBEAST_API_KEY`/`OPENAI_API_KEY`
env or `--api-key`), the eval harness, and clients installed with
`--api-key`. Rig-side OpenCode against a keyed rig: add
`"apiKey": "<key>"` to `provider.llama-cpp.options` in your **user-level**
opencode config (the repo file stays keyless; OpenCode 1.18.x does not
substitute `{env:...}` in provider apiKey — upstream #27853/#19946).

### Keyed-mode verification checklist (run once after enabling)

1. `./scripts/healthcheck.sh` — all rows OK (bearer sent automatically).
2. WebUI chat works (compose passes the key).
3. `./agent.sh "echo hi"` completes (runner env resolution).
4. `openbeast-client status` on the client — rig API reachable.
5. Negative: `curl https://<rig>:8443/v1/models` **without** a bearer → 401.

## Live two-machine verification (the three localities)

The check that proves the architecture. **Quit NordVPN / any full-tunnel VPN
first** — its kill switch severs the tailnet mid-stream while everything else
looks healthy (README § Remote access).

1. **Local tools:** in OpenCode on the client, have the model edit a file —
   confirm the change landed on the **client's** disk.
2. **Search locality:** run a `web_search` — confirm the rig's SearXNG
   answered (or the local container with `--local-search`).
3. **Remote brain:** watch `nvidia-smi` on the rig spike while tokens stream
   to the client.

Then repeat chat + agent + OpenCode with keyed mode (checklist above).

## Roadmap behind the same contract

- **Multi-slot serving profile:** a non-MTP high-`-np` config serves parallel
  clients; `/api/slot` reports the real slot pool. No client change.
- **Fleet router ("Mark of the Beast"):** a least-loaded router across worker
  boxes answers the same discovery shape. No client change.
- **Slot fairness:** per-user concurrency caps when slots are contended
  (docs/TODO.md).
