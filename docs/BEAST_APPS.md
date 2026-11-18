# beast-apps — external applications through MCP servers

beast-apps is how the model drives applications outside OpenBeast: iOS builds
(Xcode, simulators), Android builds (Gradle, adb, emulators), mobile UI
automation, Blender and Figma, and later 3D printers and robot simulators. It
does this through third-party MCP servers. beast-apps decides *whether* a
server can work on this machine, *says exactly what is missing* when it
cannot, wires it into opencode when it can, and notices when the server
changes upstream.

```
  catalog.toml (data)  ──►  probes on THIS machine  ──►  READY / MISSING + fix
        │                                                   │
        │                              enable (after a passing check)
        ▼                                                   ▼
  overlays (catalog.d)        opencode.json  mcp.servers.<id>  (+ ask rules)
                                                            │
                          refresh / upgrade  ◄── initialize + tools/list
                          (versions, tool manifests, diffs, risk flags)
```

## Where it runs

Tools execute where the **process** runs ([BEAST_SLOT.md](BEAST_SLOT.md)). In
client mode, opencode on the Mac runs OpenBeast's MCP server locally and sends
only completions to the rig. Apps work the same way: `apps.sh` probes the Mac,
the servers it enables run on the Mac next to `openbeast-tools`, and Xcode,
adb and Blender are the Mac's. The model on the rig (or the Spark pair) only
sees tool calls and results. Nothing here changes the rig.

## Commands

| Command | What it does | Exit |
|---|---|---|
| `scripts/apps.sh list [--json]` | every capability, its status here, enabled or not | 0 |
| `scripts/apps.sh find <words…> [--json]` | which capability covers a request ("mobile build on the mac") | 0, 2 = no match |
| `scripts/apps.sh check <cap> [--server S] [--json]` | runs the probes: READY, or MISSING with numbered remediation | 0 READY · 3 MISSING · 4 UNSUPPORTED |
| `scripts/apps.sh enable <cap> [--server S] [--key K] [--dry-run] [--accept-risk] [--ungated] [--allow-duplicate]` | stages the server (npm: into `<state>/npm/`), merges the first READY one into opencode, backing the old file up first | 0 · 3/4 refused · 6 risk / gates cannot be written · 7 conflict (a key or a copy that already exists) |
| `scripts/apps.sh disable <cap\|server\|key> [--dry-run]` | removes exactly what `enable` wrote | 0 · 2 not ours |
| `scripts/apps.sh refresh [server…] [--no-introspect] [--json]` | latest published version vs the pin; live `tools/list`; manifest + diff | 0 · 1 all failed |
| `scripts/apps.sh upgrade <server> [--to V] [--accept-new-tools]` | moves a pin (up, or down: a rollback): re-probe, stage, re-read tools, rewrite the entry | 0 · 2 `--to` is not an exact version · 3 · 5 offline · 6 new ungated risky tools |
| `scripts/apps.sh tools <server> [--json]` | the recorded tool manifest (names, descriptions, schemas) | 0 · 2 none yet |
| `scripts/apps.sh validate` | checks the catalog | 0 · 1 |

`--json` output is for the model. It never carries a credential: values of a
server's `secret_env` (and of any variable named like `*KEY*`, `*TOKEN*`,
`*SECRET*`, `*PASSWORD*`) show as `"<redacted>"` there, because what the model
reads travels to the rig and into agent transcripts. The config file keeps
the real value (mode 600). The skill
[`skills/app-control`](../skills/app-control/SKILL.md) tells it how to use
the output: check first, relay the remediation verbatim, propose `enable`, and
never run `upgrade` unasked.

Example (a Mac with Xcode.app installed but the Command Line Tools selected):

```
$ scripts/apps.sh check ios-build
ios-build — Build, run and test iOS/macOS apps with Xcode and the iOS Simulator
  platform: darwin    status: MISSING
  server mobile-build-tools 2.7.1 — MobileBuildMCP (getsentry; formerly XcodeBuildMCP)  [MISSING]
    ✓ macOS 14.5 or later — 26.6.2
    ✓ Node.js 18 or later — 22.14.0
    ✓ Xcode.app (full Xcode, not only the Command Line Tools) — /Applications/Xcode.app
    ✗ xcodebuild from a full Xcode 16+ (selected with xcode-select) — xcode-select: error: tool 'xcodebuild' requires Xcode, but active developer directory '/Library/Developer/CommandLineTools' is a command line tools instance
    ✗ the iOS Simulator tools (xcrun simctl) — xcrun: error: unable to find utility "simctl", not a developer tool or in PATH
  What to do:
    1. xcodebuild does not run a full Xcode (…). If Xcode.app is installed, select it: `sudo xcode-select -s /Applications/Xcode.app && sudo xcodebuild -runFirstLaunch` …
    2. `xcrun simctl` is unavailable (…). It ships with full Xcode only …
```

## The catalog

`agents/apps/catalog.toml` is data. Adding a capability, a server, a probe or
a new version means editing the catalog. No code changes. It has three
tables:

```toml
[capability.android-build]
title    = "Build Android apps with Gradle; install, launch and debug them over adb"
stage    = "mobile"                       # mobile | 2d | 3d | fabrication | simulation
keywords = ["android", "gradle", "adb", "apk"]   # used by `find`
servers  = ["replicant-mcp"]              # PREFERENCE order: first READY wins

[server.replicant-mcp]
title    = "replicant-mcp (Gradle builds, emulators, adb, logcat, UI)"
source   = "https://github.com/thecombatwombat/replicant-mcp"   # primary source
checked  = "2026-11-15"                   # when it was verified there
registry = "npm"                          # npm | pypi | github (+ repo = "o/n") | app
package  = "replicant-mcp"
version  = "1.6.7"                        # exact pin; `latest` is refused
transport = "stdio"                       # stdio (command) | http (url)
command  = ["npx", "-y", "{package}@{version}"]   # must contain {version}
platforms = ["darwin", "linux"]
probes   = ["node-18", "npx", "adb", "java-17", "android-emulator"]
env      = {}                             # fixed env (workflows, telemetry off)
env_passthrough = []                      # copied from YOUR shell at enable time
secret_env = []                           # any of these present → config chmod 600
ask_tools  = ["adb-shell"]                # → opencode "ask" rules (globs allowed)
deny_tools = []                           # → opencode "deny" rules
risk_patterns = ["shell", "exec"]         # a NEW tool matching these is flagged on refresh
default_enabled = true                    # false → enable needs --accept-risk (and a `risk` text)
notes    = "…"                            # shown on enable

[probe.adb]
kind     = "command"                      # command | path | env | tcp | any
what     = "adb (Android platform-tools)"
command  = ["adb", "version"]
search   = ["${ANDROID_HOME}/platform-tools", "~/Library/Android/sdk/platform-tools"]
off_path = "warn"                         # found only in `search`: ok | warn | fail
export_env = { ANDROID_HOME = "{dir}/.." }   # set in the MCP entry when found off PATH
fix      = "adb was not found … `brew install --cask android-platform-tools`."
fix_off_path = "adb exists at {path} but is not on PATH. …"
```

How probes behave:

- **command**: the PATH hit first, then each `search` dir. The first candidate
  that runs, matches `match`/`reject` and reaches `min_version` (read with
  `version_regex`) wins. A JDK 11 on PATH therefore does not hide the JDK 21
  inside Android Studio. A candidate found only in a search dir gets its dir
  put on the MCP server's PATH, plus any `export_env`.
- **path**: any glob in `paths` exists. **env**: the variable is set (and
  matches `match`). **tcp**: something accepts a connection (Blender's addon
  socket, Figma's 3845, rosbridge's 9090). **any**: one of `of` passes; the
  alternatives run in order and stop at the first that passes (`subprobes`
  lists only those that ran).
- One `list` / `find` / `check` runs each probe, and each distinct command,
  once: servers that share `npx`, `node`, `adb` or `simctl` reuse the result.
- `required = false` turns a failure into a warning: the server starts, but
  one workflow will not work (the Android emulator, a running Blender).
- `fix` text is shown verbatim, so it names what is missing and the exact
  command that fixes it. `{path}` `{dir}` `{parent}` `{found}` `{output}`
  expand from the result, and `${VAR:-default}` expands in `search`, `paths`,
  `host` and `port`. The tcp probes for Blender and ROS read `BLENDER_HOST` /
  `BLENDER_PORT` and `ROSBRIDGE_HOST` / `ROSBRIDGE_PORT` (the last two are
  beast-apps' own names, so a remote robot can be checked).

### Adding a capability or a server

1. Find the server's **primary source**: the repo README, the package
   registry entry, its env-var and security docs. Record `source` and
   `checked`.
2. Pin the exact version (`npm view <pkg> version`,
   `https://pypi.org/pypi/<pkg>/json`). The launch command must contain
   `{version}`.
3. Write probes for each prerequisite the docs name, and give each one a
   `fix` that a user can act on without searching for anything.
4. Gate anything that executes code, touches hardware, or sends work off the
   machine (`ask_tools`). List those words in `risk_patterns`. If the server
   has no confirmation step of its own for physical or destructive actions,
   set `default_enabled = false` and explain why in `risk`.
5. Run `scripts/apps.sh validate`, then `check <cap>` on a real machine.

To try a server without editing the checkout, put a `*.toml` file in
`~/.openbeast-client/apps/catalog.d/` (or `$OPENBEAST_APPS_CATALOG_D`). Its
entries merge field by field over the repo catalog, so an overlay can add a
whole capability, bump one server's pin, or move a probe's port.

## Scaling with upstream updates

Upstream servers change between releases: new tools appear, old ones are
renamed, a better server for the same job shows up. beast-apps handles each
case through data, not code:

- **New version published.** `refresh` compares the effective pin (catalog, or
  an `upgrade` override in `pins.json`) with the npm, PyPI or GitHub latest
  and reports `UPDATE AVAILABLE`. It never moves the pin itself.
  `upgrade <server>` does: it re-runs the probes, stages and starts the new
  version, reads its `tools/list` (it aborts and keeps the pin if the new
  version does not answer), records the manifest, rewrites the opencode entry
  and backs the old one up. Before writing anything it diffs the new tool
  list: a tool that says it runs code, or a NEW tool matching
  `risk_patterns`, that no gate covers makes it refuse (exit 6, pin and
  config untouched) until the tool is gated in the catalog or an overlay, or
  you pass `--accept-new-tools`. `refresh` reports the same flags; it never
  rewires anything, so it does not refuse.
- **Versions are versions.** `--to`, the registry's "latest" answer and every
  `pins.json` value must be an exact version (`1.2.3`, `2.3.0rc1`,
  `1.0.0-beta.1`). The value is substituted into `{package}@{version}`, where
  `npm:other-pkg@9`, `file:../x` or a repository spec would install different
  code under the trusted server's name and gates: `upgrade` refuses them
  (exit 2), and a pin that is not a version is ignored.
- **Rollback.** `upgrade <server> --to <older>` works: the pin records the
  catalog version it was set against (`base` in `pins.json`) and wins, in
  either direction, until the catalog pin moves. `refresh` then says
  "pinned below the catalog's X" instead of asking you to undo it.
- **Same version, different tools** (or an app-bundled server like Figma,
  whose version follows the app). `refresh` starts the server, speaks MCP
  (`initialize` + `tools/list`, paged, with a timeout), and records a manifest
  in `<state>/manifests/<server>/<version>.json` and `latest.json`. The
  manifest holds each tool's name, description and input schema, plus a hash
  over all of them. `refresh` diffs it against the previous manifest and
  reports added, removed and changed tools. A **new** tool whose name
  matches `risk_patterns` and is not covered by `ask_tools` or `deny_tools`
  is flagged for review. Any tool, on the first manifest too, whose
  description says it runs code or installs something (`execute_code`,
  "Python code", "login item", …) and is not gated is flagged as well, so an
  exec bridge hiding behind a bland name ("blender_mcp_call") is caught.
- **New capability, or a new server for an existing one.** Add it to the
  catalog (or an overlay). `servers` is a preference list. When the preferred
  server is not the one you enabled, `refresh` says so.
- **The catalog pin moves** (a repo update). `refresh` notices that your entry
  still runs the old version and prints the `upgrade --to` command.

## opencode

The opencode config is `$OPENBEAST_OPENCODE_CONFIG`, else
`${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json`, or `--config`.
Both schemas are supported. The choice comes from `OPENBEAST_OPENCODE_SCHEMA`,
then the file itself, then `opencode --version`. `upgrade` re-reads the file
every time rather than trusting the schema recorded at enable: once
`setup-client.sh` has moved v1 entries into `mcp.servers` (opencode 2.x), an
entry enabled as v1 is rewritten as v2 and its gates move from `permission`
to `permissions`. Writing v1 next to `mcp.servers` is the mixed layout
opencode 2 reads as "No MCP servers configured".

| | v1 (opencode 1.x) | v2 (opencode 2.x, e.g. 2.0.25) |
|---|---|---|
| entry | `mcp.<id> = {type, command, environment, enabled: true}` | `mcp.servers.<id> = {type, command, environment, codemode: false}` |
| remote | `{type: "remote", url, enabled: true}` | `{type: "remote", url, codemode: false}` |
| gates | `permission["<id>_<tool>"] = "ask"` | `permissions += [{action: "<id>_<tool>", resource: "*", effect: "ask"}]` |

The v2 facts were read from opencode 2.0.25 itself on 2026-11-15:

- `opencode mcp add` writes `mcp.servers.<name>`.
- The server schema is `additionalProperties: false`, so v1's `enabled` key
  would make the file invalid there.
- `codemode` defaults to true ("Expose this server's tools through Code
  Mode"). We turn it off so the model gets the tools directly.
- A v1 entry is migrated without a codemode field.
- An MCP tool's permission action is `<server>_<tool>`, with characters
  outside `[a-zA-Z0-9_-]` replaced by `_`.
- Rules match with `*` wildcards and the last match wins, so our rules go at
  the end.

Guarantees:

- `enable` refuses when the check fails. It also refuses when an entry with
  the same key exists that beast-apps did not write, when your own entry
  already runs the same package, and when beast-apps already wired the same
  capability or server under another `--key` (pass `--allow-duplicate` to add
  it anyway).
- A gate never weakens a rule you already have for the same action. v1: a
  `"deny"` (or an equal `"ask"`) of yours is kept and not recorded; an
  `"allow"` is tightened to `"ask"` and put back on `disable`. v2: our rule is
  appended only when your last rule for that action is weaker. `disable`
  removes only what `enable` recorded.
- `enable` refuses (exit 6, nothing written) when the config cannot hold the
  server's gates: a v1 `"permission": "allow"` string has no room for a
  per-tool rule. Make `permission` an object, or pass `--ungated` to wire the
  server in without them; `gated:` lists only rules that were written.
- Every write first copies the old file to `opencode.json.bak-beast-apps-<ts>`
  (created 600, never widened first), then writes atomically, keeping the
  file's mode. The file becomes 600 when a secret env var is in the entry,
  on `enable` and on `upgrade` alike.
- `upgrade` keeps the `env_passthrough` values recorded at enable when the
  shell you run it from does not set them (a fresh terminal does not drop the
  printer's token); a value the shell does set replaces the recorded one.
- `disable` removes only the keys and rules recorded in
  `<state>/enabled.json`, and removes containers it emptied. A file that was
  enabled and then disabled is identical to the original. Ownership lives in
  the state dir, because v2 forbids marker fields in the entry.
- The config is never rewritten if it is not plain JSON (JSONC comments).
- `argv[0]` is written as an absolute path, and every stdio entry gets a
  fixed `PATH`: the dirs the probes found (node, adb, java, …), the
  launcher's dir, then the system dirs. A GUI-launched opencode gives its
  servers launchd's `/usr/bin:/bin:/usr/sbin:/sbin`, where `spawn npx ENOENT`
  and `env: node: No such file or directory` (every npm bin is
  `#!/usr/bin/env node`) both used to happen.

### How a server is launched

opencode starts an MCP server in the **project directory it has open**. For
an npm server, `npx -y pkg@1.2.3` there is resolved against that project: its
own `node_modules/pkg` at 1.2.3 runs instead of the registry copy, its
`.npmrc` can point `registry` and `cache` elsewhere, and npm exec puts its
`node_modules/.bin` on the server's `PATH` (a planted `node` or `xcodebuild`
wins). An exact pin stops none of that, and it happens when opencode starts,
before any ask gate. So the catalog's `["npx", "-y", "{package}@{version}", …]`
is never run through npx:

- `enable` and `upgrade` install that exact version into
  `<state>/npm/<server>@<version>/` with `npm install --prefix` from that
  directory (your `~/.npmrc` applies, no project's does), and record npm's
  lockfile there. Offline, or with the package already cached, the install
  uses the local npm cache only (`--offline` / `--prefer-offline`).
- The entry runs `node <state>/npm/…/node_modules/<pkg>/<bin>` by absolute
  path. No npm at launch, no lookup relative to the project; the server
  still starts in the project dir, so tools that work on "the project" (for
  replicant, `./gradlew`) are unchanged.
- `refresh` and `upgrade` start servers from the state dir with a minimal
  environment (`PATH`, `HOME`, locale, proxy and CA settings, `npm_config_*`
  and `UV_*`, plus the server's own catalog env): a release nobody has
  reviewed yet does not get your cloud credentials or tokens.
  `OPENBEAST_APPS_INTROSPECT_ENV` names extra variables to pass.
- An entry enabled before this still launches through npx; `refresh` says
  so. Re-run `enable <capability>` to move it.

uvx servers keep `uvx pkg@version`: `uv tool run` does not read the opened
project's `uv.toml`/`pyproject.toml` (checked with uv 0.8.22: a project
`index-url` is ignored).

The state dir is `$OPENBEAST_APPS_STATE`, else `~/.openbeast-client/apps/`. It
holds `enabled.json`, `pins.json`, `manifests/`, `npm/` (staged npm servers,
one dir per version, so a rollback needs no download) and `catalog.d/`.

## Offline

The catalog, the probes, `check` and `disable` never touch the network.
`enable` touches it only to stage an npm server that is not in the local npm
cache yet; offline, it stages from that cache alone (`npm --offline`) and
fails fast with nothing written when the package is not there. `enable`,
`refresh` and `upgrade` decide whether they are offline in this
order: `OPENBEAST_APPS_OFFLINE=1`; `OFFLINE=true` (the install-level switch);
the rig's island state; and finally beast-island's connect probe
([BEAST_ISLAND.md](BEAST_ISLAND.md), `OPENBEAST_ISLAND_PROBES`). Offline,
`refresh` prints the beast-island banner, keeps every pin, skips the registry
lookups, and runs introspection from what is already staged (npm) or with
`uvx --offline`, so a cached package still answers and an uncached one fails
fast. `upgrade` without `--to` exits 5 and says why; `upgrade --to V` stages V
from the local npm cache only.

### Staging servers for an offline machine

A server needs its package on the machine once; after that it starts with no
network. Stage it while online, or carry it over:

- **Online first** (the simple route): `scripts/apps.sh enable <capability>`
  stages an npm server into `<state>/npm/`; `scripts/apps.sh refresh <server>`
  fills uv's cache for a uvx server. Both keep working offline afterwards.
- **Air-gapped, npm**: on a connected machine of the same OS and CPU, run
  `enable` (or `npm cache add <pkg>@<version>` for every package the
  server's lockfile names), then copy `~/.npm/_cacache` to the same path on
  the offline machine. `enable` there installs from that cache
  (`--offline`). Or copy `<state>/npm/<server>@<version>/` itself: it is
  self-contained.
- **Air-gapped, uvx**: copy uv's cache (`uv cache dir`) from a connected
  machine where `refresh <server>` ran. mcp-for-blender pins
  `UV_PYTHON_PREFERENCE=only-managed`, so it also needs uv's managed Python:
  run `uv python install 3.12` there and copy `uv python dir` across too.
- `enable` offline with nothing cached fails with npm's `ENOTCACHED` and
  writes nothing.

### Offline client

Installing client mode needs a checkout and the python deps, neither of
which has to come from the internet:

1. **Checkout.** Run `scripts/setup-client.sh` from INSIDE a checkout (a
   copied clone, or `git clone /media/usb/openbeast.bundle openbeast` from a
   bundle made on a connected machine). It then installs in place and never
   clones; outside a checkout it clones from GitHub, and says what to do when
   that fails.
2. **Python deps.** On a connected machine with the same OS, CPU and Python
   minor version: `scripts/pydeps.sh wheelhouse /media/usb/wheels` (it prints
   the lock's sha256). On the client:
   `scripts/setup-client.sh --host <rig> --wheelhouse /media/usb/wheels`
   (or `OPENBEAST_WHEELHOUSE=…`). `pydeps.sh install --from` checks every
   wheel against the lock committed in the checkout (or
   `OPENBEAST_LOCK_SHA256`) and contacts no index.
3. **Updates.** Update the checkout from a new bundle
   (`git pull /media/usb/openbeast.bundle main`), then
   `openbeast-client update --no-pull --wheelhouse /media/usb/wheels`.

The rig itself is reached over the tailnet, not the internet, so an islanded
client keeps working once installed (BEAST_ISLAND.md).

## Safety

- **The server runs with your user's rights.** Building a project runs its
  build scripts (MobileBuildMCP `build_*`/`test_*`, replicant `gradle-build`,
  `gradle-test`, `gradle-list`). Installing and launching an app runs it: a
  Simulator app is a native macOS process running as you, and a project can
  ship a prebuilt one (MobileBuildMCP `install_app_sim`/`launch_app_sim`,
  replicant `adb-app`, Mobile MCP `mobile_install_app`/`mobile_launch_app`).
  Mobile MCP's `mobile_batch_commands` runs any of its other tools as steps,
  so it is gated too, or it would reach every gated tool with no approval.
  `execute_blender_code` runs arbitrary Python in Blender. replicant's
  `adb-shell` runs device commands behind its own denylist. Each of these is
  an opencode **ask** rule, and the user approves every call.
- **Gates are checked against real tool names.** `tests/test_apps.py` checks
  every gate of every gated stdio server against the pinned release's tool
  list in `tests/fixtures/apps/<server>-<version>.tools.json`, and fails when
  a gated server has no such list. MobileBuildMCP, replicant and
  mcp-3d-printer-server were read from `tools/list`; Mobile MCP 1.0.17,
  mcp-for-blender 2.3.0 and ros-mcp 3.1.2 were read statically from the
  published package source (the npm tarball, the PyPI wheel) without running
  it, the way upstream registers them (`tool(...)` / `@mcp.tool`) under the
  catalog's env (Mobile MCP's telemetry-only `get_more_tools` is absent).
  `refresh` records the live list on your machine.
- **Physical actions.** `mcp-3d-printer-server` has no confirmation prompt of
  its own (upstream: "the client's own print action is the approval"), so it
  is **off by default**. `enable 3d-print` needs `--accept-risk`, and every
  tool that starts, resumes, heats or cancels a print is gated. So are its
  Blender/Fusion bridges and installers (`blender_mcp_call`, which reaches
  `execute_blender_code`; `fusion_mcp_call`, which reaches `execute_code`;
  `fusion_mcp_setup`, which installs an add-in and a login item; `cad_run`;
  the `*_setup` tools). The programs those bridges spawn come only from your
  shell: the entry pins them empty, so a project's `.env` (which upstream
  loads) cannot choose them. The same `.env` cannot switch the server to its
  unauthenticated HTTP transport (`MCP_TRANSPORT` is pinned to `stdio`) or
  pick the CAD runtime's python, which the ungated `cad_check_fit` runs
  (`CAD_DATA_DIR` is pinned under the beast-apps state dir). ROS publish, service
  and action calls move real robots and are gated with globs.
- **Egress.** Telemetry is switched off where the server offers a switch:
  `MOBILEBUILDMCP_SENTRY_DISABLED`, `MOBILEMCP_DISABLE_TELEMETRY`, Blender's
  `DISABLE_TELEMETRY`. Mobile MCP's cloud-device tools and Blender's
  asset/generation tools send work off the machine. The cloud-device tools
  are gated, and the asset tools are called out in the server notes.
- **Pins, not `@latest`.** Every launch command pins an exact version. The
  validator refuses an unpinned `npx`/`uvx` command and a catalog version
  that is not an exact release. The pin covers the top-level package only:
  npm resolves its dependencies when `enable` stages it and records them in
  `<state>/npm/<server>@<version>/package-lock.json` (with integrity hashes),
  so they stay fixed on that machine but can differ between machines; uvx
  resolves a tool's dependencies when it first fills its cache. A compromised
  transitive release published before you stage can still land. The Python
  side of OpenBeast itself is hash-locked (`scripts/pydeps.sh`).
- **Tool surface.** MobileBuildMCP enables only the `simulator` workflow.
  Every advertised tool costs a local model context, so widen it in the
  catalog (`MOBILEBUILDMCP_ENABLED_WORKFLOWS`) only when needed.

## Seeded entries (verified 2026-11-15)

| Capability | Server | Pin | Source |
|---|---|---|---|
| ios-build | `mobile-build-tools` (npm `mobilebuildmcp`, getsentry, formerly XcodeBuildMCP; launch `… mcp`; id until 2026-11-17: `mobilebuildmcp`, an alias that `enable`/`disable`/`refresh`/`upgrade` rename) | 2.7.1 | github.com/getsentry/MobileBuildMCP, npm |
| android-build | `replicant-mcp` | 1.6.7 | github.com/thecombatwombat/replicant-mcp, npm |
| mobile-ui | `@mobilenext/mobile-mcp` | 1.0.17 | github.com/mobile-next/mobile-mcp, npm |
| blender | `mcp-for-blender` (PyPI; formerly `blender-mcp`) | 2.3.0 | github.com/ahujasid/mcp-for-blender, PyPI |
| figma | Figma desktop server, `http://127.0.0.1:3845/mcp` | app | developers.figma.com/docs/figma-mcp-server/local-server-installation |
| 3d-print | `mcp-3d-printer-server` (off by default) | 1.2.77 | github.com/DMontgomery40/mcp-3D-printer-server, npm |
| sim-ros | `ros-mcp` (`--transport=stdio`) | 3.1.2 | github.com/robotmcp/ros-mcp-server (server.json), PyPI |

Not seeded yet (not researched): Inkscape, Isaac Sim, Gazebo-native and
slicer-only servers. Each is one catalog entry away.

Tests: `tests/test_apps.py` (hermetic: stub toolchains, a stub stdio MCP
server in `tests/fixtures/apps/`, a local npm registry, a closed port for
"offline").
