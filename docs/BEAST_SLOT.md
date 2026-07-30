# beast-slot — client/server OpenBeast

**Status: SHIPPED 2026-07-30.** Supersedes [MAC_CLIENT_PLAN.md](MAC_CLIENT_PLAN.md)
(the thin-client prototype) with a first-class client/server architecture.

## The concept

The rig stays the fully-armed **command center** — every service it runs today
(llama.cpp, identity tool server, Open WebUI, SearXNG, extensions) keeps
running, unchanged. **beast-slot** is what the rig additionally publishes to
your tailnet: its intelligence as a consumable surface.

```
COMMAND CENTER (rig)                          CLIENT (any Mac/Linux device)
llama-server :8080 ◀── tailscale :8443 ────── opencode + mcp_server.py (stdio)
dashboard   :3002 ◀── tailscale :8444 ────── client.sh status   (discovery)
SearXNG     :8888 ◀── tailscale :8889 ────── web_search          (default)
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
| `POST /lora-adapters` | Global model mutation — affects every user of the rig |
| `GET/DELETE /v1/stream/:conv_id` | Sessions are keyed only by a caller-chosen `X-Conversation-Id`; knowing an id lets you read or cancel that generation. Enumeration is blocked (`/v1/streams/lookup` only answers for ids you supply), guessing is not |
| `/infill`, `/embeddings`, `/rerank`, `/tokenize` | Extra compute surface beyond chat |

Gated on our config and safe as shipped: `POST /slots/:id` needs
`--slot-save-path` (never passed) and `POST /props` needs `--props` (off).

On a personal tailnet where you own every device, this is acceptable — it is
the same trust boundary as the chat endpoint itself. On a tailnet with users
or devices you do not own, a bearer key (below) gates all of it behind one
shared secret but does **not** shrink the surface; the right fix is an
identity-aware edge proxy that allowlists the OpenAI routes. Tracked in
docs/TODO.md.

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
./scripts/setup-tailscale.sh --publish-slot      # + :8444 discovery API
```

### The `/api/slot` discovery contract (version 1)

`GET https://<rig>:8444/api/slot` — read-only JSON. `--publish-slot` mounts
*only* this path (`tailscale serve --set-path`), so the dashboard's HTML page
and `/api/status` stay rig-local:

```json
{
  "beast_slot": 1,
  "healthy": true,
  "model": {"id": "heretic-v2-27b-mtp-q6", "ctx": 212992},
  "slots": {"total": 1, "busy": 0},
  "services": {"model": true, "tools": true, "webui": true, "search": true},
  "auth": "open"
}
```

- `model.id` is the **real loaded model** (from `/v1/models`) — llama-server
  ignores the model name clients request, so this is how a client knows what
  it's actually talking to.
- `slots` is **data, not an assumption**. Today's MTP default serves `-np 1`:
  one slot, concurrent requests (your WebUI turn + a remote client's turn)
  queue FIFO. Switching between conversations is cheaper than it sounds — the
  server saves the displaced conversation's KV state into a global RAM prompt
  cache (8 GiB default) and restores it later, so a handoff costs a RAM copy,
  not a full reprocess. A future multi-slot serving profile (or a fleet
  router) answers the **same shape** with bigger numbers; clients must not
  hard-code 1.
- ⚠️ **`slots.total` × `model.ctx` is NOT your capacity.** We serve with
  `--kv-unified`, under which every slot advertises the *full* context while
  all slots share one pool. A default non-MTP install publishes
  `slots.total: 6` and `ctx: 358400` — that is 358400 tokens **shared**, not
  per slot. Worse, when the pool runs dry the server purges the
  *lowest-numbered* idle slot's cache to make room, deterministically. For
  real per-tenant isolation, serve `--no-kv-unified -np N -c (N × per-tenant)`
  and accept the lower pool efficiency.
- `slots.busy` counts only *in-flight* generations. Queued work is invisible
  (with `-np 1`, `busy` is 1 whether one request or fifty are waiting), so it
  is a liveness signal, not a load signal. llama-server's `/metrics` carries
  `requests_deferred` — the number a future contract version should surface.
- `busy` is `null` when the server runs `--no-slots`.
- Never contains prompt text or key material.

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
