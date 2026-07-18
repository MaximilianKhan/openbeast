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
restart. Empty by default.

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
enabled, so its services start/stop with the stack. Follow the core file's
hardening conventions (`network_mode: host` or an explicit port, `cap_drop`,
`no-new-privileges`, digest-pinned images).

**`KIND=process`** — ship an executable `run.sh` that `exec`s its server in the
**foreground** (start.sh backgrounds it, pidfiles it in `.run/ext-<name>.pid`,
logs to `.run/ext-<name>.log`, and reaps it on shutdown). Bind to
`${OPENBEAST_BIND:-127.0.0.1}` — remote access goes through Tailscale, never a
raw bind.

## Shipped extensions

- **`dashboard`** — a lightweight read-only status page (GPU / model / services)
  on top of the tool server's `/metrics` and `doctor`. `KIND=process`, stdlib
  Python, no new dependency.
