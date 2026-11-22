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
  | `/` | the page (`static/index.html`, `app.css`, `app.js`, the Geist fonts): polls `/api/status` and `/api/cluster` every 3 s (and `/api/power` for the chart) and updates values in place |
  | `GET /api/status` | this node's model, serving, gate, services, tool activity, events (v1 keys `gpu`, `model`, `services`, `metrics` unchanged; `gpu` now comes from the head sampler, plus an additive `power_w`) |
  | `GET /api/cluster` | this node and every peer: GPU, memory, util, temperature, GPU power (nvidia-smi `power.draw`: board power on discrete cards, GPU rail only on GB10), CPU, RAM, model volume, uptime, fabric ports, 15-min history with timestamps; plus `power` (below) |
  | `GET /api/power?window=` | the power chart's series for `15m`, `1h`, `6h` or `24h` |
  | **`GET /api/slot`** | the **beast-slot discovery contract** a remote client reads (`docs/BEAST_SLOT.md`); byte-identical to v1, pinned by a golden test |

  What the page shows: the served model id, engine (llama.cpp, vLLM,
  TensorFold), the active inference unit and its uptime, context length,
  current tokens/s (from the engine's Prometheus counters; beast-gate's
  completion tokens when the engine has none), requests running / waiting,
  slots, KV-cache use; the **cluster** (one card per node, a GB10's memory
  labelled *unified*, a discrete card's *VRAM*, a host without a GPU says
  so); **power** (below); **beast-gate** (devices enrolled, active in the
  last 15 min, requests/min, errors, refusals without an identity
  (no_key / unknown_key / revoked) and refusals for every reason, with the
  per-reason counts on hover: counts only, never a device name); every
  **service** with its port (model, gate, tools, WebUI, search, island,
  chat, artifact); the tool server's calls, errors and top tools; and the
  dashboard's own event log (a peer lost or back, the model down, an island
  transition).

  **Power.** A *Power* section leads with the measured watts now (the
  cluster total on the Spark pair, "n of m nodes" when one is unreachable),
  the trailing averages over 1 min, 5 min, 15 min, 1 hour and 24 hours (each
  with its low-to-peak range, the energy used in Wh, kWh for 24 h, and how
  much of the window was covered), and a trend arrow: the 5 min average
  against the 15 min one (the hour's until 15 min exist), *steady* within
  ±5 %. Then a row per node (what is measured, each rail, its own averages
  and trend, and on a discrete card the share of the board power limit), and
  a chart with a 15 m / 1 h / 6 h / 24 h selector: a line per node, the
  cluster total, the window averages dashed. Hover, tap, or focus it and use
  the arrow keys for the values at a time.

  *What is measured, in plain words.* Each figure is labelled by its sensor,
  never called "system power" without a system sensor:
  - **DGX Spark (GB10):** `GPU power` is the GPU rail only. GB10 reports no
    module or system power (nvidia-smi module power and every power limit
    read N/A; there is no hwmon, RAPL or powercap power sensor, checked on
    both Sparks 2026-11-22). The whole box draws noticeably more; wall power
    needs an external meter (a smart plug, or a USB-C PD meter on the 240 W
    supply).
  - **RTX 5090 rig:** the card's board power (GPU and VRAM, nvidia-smi,
    with its 600 W limit) **plus** the Ryzen's CPU package power (the
    amdgpu iGPU's `PPT` hwmon channel, readable without root), shown as
    `GPU + CPU package`. That leaves out the motherboard, RAM, drives, fans
    and PSU losses. The APU firmware files whole watts in the driver's mW
    field (an idle 9950X3D reads "24-59 mW"); a reading under 256 "mW" in
    whole-mW steps is read as watts, and the page marks it †.
  - **Any host with an ACPI power meter** (`power_meter` hwmon): that is
    shown as `system power`, and the other rails as its parts.

  *How it is computed.* Every node is sampled every 5 s (peers through the
  same fixed ssh probe, which now also reads `nvidia-smi
  power.draw.instant / .average / enforced.power.limit`, NVML's cumulative
  GPU energy counter through a guarded `python3 -I` one-liner, and every
  readable hwmon power channel by name and label). "Now" is the
  instantaneous GPU reading. Each pair of samples is one interval: for the
  GPU its exact mean power from the **energy counter** (mJ over the node's
  own monotonic clock), so bursty load between samples is counted; else the
  trapezoid of the two 1 s readings; other rails by trapezoid. Intervals
  land in wall-clock slots, 5 s for the last hour and 1 min for 24 h.
  Averages are energy / covered time; a gap (a node unreachable, the
  dashboard down, a sample over 20 s late) is **not covered**, shown as
  coverage below 100 %, never as zero watts; an average with under 10 % of
  its window covered reads *collecting*. The range is the lowest and highest
  ~5 s mean (1 min means for 24 h); the cluster's range uses only slots
  where every node was measured. History survives a restart:
  `.run/dashboard-power.json` (mode 600) is rewritten every 30 s and on
  SIGTERM, through a temp file and a rename, so a crash leaves the previous
  file whole; it is capped at 4 MB (the 5 s tier is dropped first) and a
  corrupt or foreign file is ignored and replaced. JSON: `/api/cluster`
  `power` (now, windows, trend, cost per node and for the cluster) and
  `GET /api/power?window=15m|1h|6h|24h` (the chart series; any other value
  is 15m). The `gpu.power_w` field is nvidia-smi `power.draw` (the GPU's
  ~1 s average), not wall or system power.

  *Cost (optional).* Set `DASHBOARD_KWH_PRICE` (openbeast.conf, or env
  `OPENBEAST_DASHBOARD_KWH_PRICE`; a positive number per kWh) and,
  optionally, `DASHBOARD_CURRENCY` (`$` by default; `EUR`, `£`...). The
  page then shows the cost per day and per month at the 24 h average (the
  1 h or 15 min one until a day is covered) and the last 24 h's measured
  cost. Unset, it is hidden. It prices the measured rails only, so on a
  Spark it is the GPU's share, not the electricity bill.

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
  page. nvidia-smi and the NVML read run under `timeout -k` inside that cap,
  and a timed-out probe's whole process group is killed, so a wedged driver
  costs the GPU lines and leaves no process behind. `/api/status` takes its
  `gpu` block from the head's sampler, so no request ever runs nvidia-smi. The model volume shown is `DASHBOARD_DISK_PATH`, else `WEIGHTS_DIR`,
  else spark.env `MODELS_DIR`.

  **Read-only.** GET only (every other method, TRACE and made-up verbs
  included, is 405). The only controls are the chart's window buttons, which
  change what the page asks for, never anything on the host. The page
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
  dashboard` stops the recorded dashboard process (which saves its power
  history on the way out), waits for it to exit, relaunches `run.sh`, and
  reports success only once `/api/slot` answers (manifest `HEALTH_PATH`,
  within about 10 s). It also starts a stopped dashboard. It touches nothing else (model server, gate, tool server, inference unit),
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
