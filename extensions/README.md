# OpenBeast extensions

Optional services that attach to the stack **without editing core files**. This
is the sanctioned way to add anything beyond OpenBeast's opinionated core — the
core stays lean; extras are opt-in. (ODS-absorbed; see `docs/TODO.md`.)

## Using extensions

```bash
./scripts/ext.sh list                # available extensions + enabled state
./scripts/ext.sh enable  dashboard   # writes EXTENSIONS= in openbeast.conf
./scripts/ext.sh disable dashboard
./scripts/ext.sh status              # what's enabled + running
./scripts/ext.sh restart dashboard   # restart one process extension alone (nothing else)
```

Enable/disable edits `openbeast.conf` (`EXTENSIONS="a b"`, space-separated) and
takes effect on the next `./start.sh` — a running stack isn't touched until
restart. Empty by default. A name is the extension's directory name under
`extensions/` — letters, digits, `-` and `_`; `ext.sh` refuses anything else
(a trailing `/` from tab completion included).

## Writing an extension

Create `extensions/<name>/` with a `manifest` (required) plus one of the two
kinds:

```
extensions/<name>/
  manifest        # KEY=value metadata (required)
  compose.yaml    # KIND=compose: a docker-compose fragment (merged via -f)
  run.sh          # KIND=process: a script start.sh runs in the background
```

**`manifest`** — simple `KEY=value` lines:

```
NAME=My Extension
DESCRIPTION=One-line summary shown in `ext.sh list`.
KIND=process          # 'compose' or 'process'
```

**`KIND=compose`** — ship a `compose.yaml` fragment. `start.sh`/`stop.sh` merge
it alongside the core `docker-compose.yml` with `-f` when the extension is
enabled, so its services start/stop with the stack (`stop.sh` passes every
fragment on disk to `down`, so one you just disabled still comes down). Follow the core file's
hardening conventions (`network_mode: host` or an explicit port, `cap_drop`,
`no-new-privileges`, digest-pinned images).

**`KIND=process`** — ship an executable `run.sh` that `exec`s its server in the
**foreground** (start.sh backgrounds it, pidfiles it in `.run/ext-<name>.pid`,
logs to `.run/ext-<name>.log`, and reaps it on shutdown). Bind to
`${OPENBEAST_BIND:-127.0.0.1}` — remote access goes through Tailscale, never a
raw bind.

### Publishing an extension path to the tailnet

The `dashboard` slot API established the sanctioned pattern. Follow it:

- **Publish one path, never a bare port.** `tailscale serve --set-path=/api/x`
  mounts exactly that route; `--https=<port> http://127.0.0.1:<port>` mounts `/`
  and hands every tailnet device the whole service — its HTML, its other
  endpoints, and anything you add later. Publish the contract, not the server.
- **Version the contract additively.** Carry a version integer and a
  `min_client` floor in the payload; keep every existing field's name and
  meaning so old clients keep working, and bump the floor only on a removal or
  a semantic change (`docs/BEAST_SLOT.md` § discovery contract is the worked
  example).
- **Read-only.** A published path answers `GET` and mutates nothing.
- **Never emit prompt text, sampling params, or key material** — a published
  path has no authentication beyond tailnet membership. Report *that* auth is
  on, never the secret.

## Shipped extensions

- **`ntfy`** — a self-hosted ntfy push server for beast-chat's opt-in
  notifications. `KIND=compose`, digest-pinned
  `binwiederhier/ntfy:v2.38.0`, bound to `127.0.0.1:${NTFY_PORT:-3005}`,
  read-only root, `cap_drop: ALL`, `no-new-privileges`, message cache and
  auth database in volumes. beast-chat posts to it when a session ends
  (`CHAT_NOTIFY_URL`; title, state and a link, never transcript text).
  `setup-tailscale.sh --publish-ntfy` mounts it tailnet-only at `:8447` for
  the phone app, `--unpublish-ntfy` takes it down. That is a whole-port
  mount, the one exception to the "publish one path" rule
  [above](#publishing-an-extension-path-to-the-tailnet): the phone
  app needs ntfy's own subscribe routes, and the topic (or, locked down, an
  ntfy token) is the access check. Its image is carried by `bundle.sh` and
  bumped by `update.sh --images` like the core images. Setup, lockdown and
  the iOS relay caveat: [`ntfy/README.md`](ntfy/README.md).

- **`dashboard`** — the read-only status page of the command center, in the
  openbeast.ai look (phosphor on near-black, glass panels, Geist), and the
  beast-slot contract. `KIND=process`, stdlib Python, no new dependency.
  Serves on `${OPENBEAST_BIND}:${DASHBOARD_PORT:-3002}` (conf key
  `DASHBOARD_PORT`; healthcheck and `--publish-slot` follow it):

  | Route | What |
  |---|---|
  | `/` | the page (`static/index.html`, `app.css`, `app.js`, the Geist fonts): polls the two JSON routes below every 3 s and updates values in place |
  | `GET /api/status` | this node's model, serving, gate, services, tool activity, events (v1 keys `gpu`, `model`, `services`, `metrics` unchanged) |
  | `GET /api/cluster` | this node and every peer: GPU, memory, util, temperature, power, CPU, RAM, model volume, uptime, fabric ports, 15-min history |
  | **`GET /api/slot`** | the **beast-slot discovery contract** a remote client reads (`docs/BEAST_SLOT.md`); byte-identical to v1, pinned by a golden test |

  What the page shows: the served model id, engine (llama.cpp, vLLM,
  TensorFold), the active inference unit and its uptime, context length,
  current tokens/s (from the engine's Prometheus counters; beast-gate's
  completion tokens when the engine has none), requests running / waiting,
  slots, KV-cache use; the **cluster** (one card per node, a GB10's memory
  labelled *unified*, a discrete card's *VRAM*, a host without a GPU says
  so); **beast-gate** (devices enrolled, active in the last 15 min,
  requests/min, errors, refusals: counts only, never a device name); every
  **service** with its port (model, gate, tools, WebUI, search, island,
  chat, artifact); the tool server's calls, errors and top tools; and the
  dashboard's own event log (a peer lost or back, the model down, an island
  transition).

  **Peers.** The cluster view shows this node plus `DASHBOARD_PEERS` (ssh
  targets, comma or space separated; `none` = this node alone). Unset, it
  reads the pair's own config, `scripts/backends/cluster.env`
  `CLUSTER_NODES` (rank order; `local` is this node), and names a node by
  its `~/.ssh/config` alias (`max@10.100.216.2` -> `spark-2`). A rig without
  that file has no peers. Each peer is read every 5 s by a background thread
  that runs **one fixed, read-only shell script** (`node_script()`:
  nvidia-smi, `/proc`, `df`, `/sys/class/infiniband` counters) as `ssh -o
  BatchMode=yes -o ConnectTimeout=3 <peer> sh -s`, capped at 6 s. No HTTP
  request triggers, schedules or parameterises it; a peer that does not
  answer shows **unreachable** (with its last reading's age), never an error
  page. The model volume shown is `DASHBOARD_DISK_PATH`, else `WEIGHTS_DIR`,
  else spark.env `MODELS_DIR`.

  **Read-only.** GET only (every other method is 405), no buttons. The page
  sends a strict CSP (`default-src 'none'`; script, style, font and connect
  from `'self'` only), loads nothing from outside (fonts vendored in
  `static/`, Geist 1.9.2 under the SIL OFL, `static/GEIST-OFL.txt`), and
  writes every server value as text, never HTML. The gate figures come from
  beast-gate's rig-local introspection (`/gate/health`, `/gate/metrics`
  with `.run/edge-local.token`, as `doctor.sh` reads them).

  `./scripts/setup-tailscale.sh --publish-slot` publishes **only** `/api/slot`
  as `:8444`, via `tailscale serve --set-path`. (On a tailscale build without
  `--set-path` it falls back to mounting the whole dashboard and says so;
  `--unpublish-slot` reverses either.) To see the page from another tailnet
  device, mount the whole dashboard on a port of its own, as spark-1 and the
  rig do on `:8447` (`tailscale serve --bg --https=8447
  http://127.0.0.1:3002`; on a host that runs the ntfy extension pick another
  port, `--publish-ntfy` owns `:8447`). The page and its JSON are
  Host-pinned against DNS rebinding: they answer only to loopback, this
  machine's name, `*.ts.net` names and IP literals (400 otherwise; more names
  via `OPENBEAST_DASHBOARD_ALLOWED_HOSTS`, comma-separated). `/api/slot`
  answers any Host.

  **Restarting it alone** (after a pull, say): `./scripts/ext.sh restart
  dashboard` stops the recorded dashboard process and relaunches `run.sh`.
  It touches nothing else (model server, gate, tool server, inference unit),
  so it is safe while an eval runs.

  **Footgun: `EXTENSIONS` is empty by default.** `--publish-slot` publishes the
  tailnet route whether or not anything is listening, so with the dashboard
  disabled `:8444/api/slot` returns **502** (nothing on `:3002`) and
  `openbeast-client status` reports the slot API as unavailable. The script
  preflights `EXTENSIONS` and warns, but does not block. Order matters:

  ```bash
  ./scripts/ext.sh enable dashboard    # writes EXTENSIONS= in openbeast.conf
  ./stop.sh && ./start.sh -d           # enable only takes effect on restart
  ./scripts/setup-tailscale.sh --publish-slot
  ```
