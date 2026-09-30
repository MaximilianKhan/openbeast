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
  `binwiederhier/ntfy:v2.28.0`, bound to `127.0.0.1:${NTFY_PORT:-3005}`,
  read-only root, `cap_drop: ALL`, `no-new-privileges`, message cache and
  auth database in volumes. beast-chat posts to it when a session ends
  (`CHAT_NOTIFY_URL`; title, state and a link, never transcript text).
  `setup-tailscale.sh --publish-ntfy` mounts it tailnet-only at `:8447` for
  the phone app, `--unpublish-ntfy` takes it down. That is a whole-port
  mount, the one exception to the "publish one path" rule below: the phone
  app needs ntfy's own subscribe routes, and the topic (or, locked down, an
  ntfy token) is the access check. Its image is carried by `bundle.sh` and
  bumped by `update.sh --images` like the core images. Setup, lockdown and
  the iOS relay caveat: [`ntfy/README.md`](ntfy/README.md).

- **`dashboard`** — a lightweight read-only status page (GPU / model / services)
  on top of the tool server's `/metrics` and `doctor`. `KIND=process`, stdlib
  Python, no new dependency. Serves three routes on `${OPENBEAST_BIND}:${DASHBOARD_PORT:-3002}`:
  `/` (the page), `GET /api/status` (what the page polls), and
  **`GET /api/slot`** — the **beast-slot discovery contract** a remote client
  reads to learn the rig's real model, slot pool, shared-KV capacity, and
  service health (`docs/BEAST_SLOT.md`).

  `./scripts/setup-tailscale.sh --publish-slot` publishes **only** `/api/slot`
  as `:8444`, via `tailscale serve --set-path` — the HTML page and `/api/status`
  stay rig-local. (On a tailscale build without `--set-path` it falls back to
  mounting the whole dashboard and says so; `--unpublish-slot` reverses either.)

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
