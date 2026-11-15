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
| `scripts/apps.sh enable <cap> [--server S] [--key K] [--dry-run] [--accept-risk] [--ungated]` | merges the first READY server into opencode, backing the old file up first | 0 · 3/4 refused · 6 risk / gates cannot be written · 7 conflict |
| `scripts/apps.sh disable <cap\|server\|key> [--dry-run]` | removes exactly what `enable` wrote | 0 · 2 not ours |
| `scripts/apps.sh refresh [server…] [--no-introspect] [--json]` | latest published version vs the pin; live `tools/list`; manifest + diff | 0 · 1 all failed |
| `scripts/apps.sh upgrade <server> [--to V]` | moves a pin: re-probe, re-read tools, rewrite the entry | 0 · 3 · 5 offline |
| `scripts/apps.sh tools <server> [--json]` | the recorded tool manifest (names, descriptions, schemas) | 0 · 2 none yet |
| `scripts/apps.sh validate` | checks the catalog | 0 · 1 |

`--json` output is for the model; the skill
[`skills/app-control`](../skills/app-control/SKILL.md) tells it how to use
the output: check first, relay the remediation verbatim, propose `enable`, and
never run `upgrade` unasked.

Example (a Mac with Xcode.app installed but the Command Line Tools selected):

```
$ scripts/apps.sh check ios-build
ios-build — Build, run and test iOS/macOS apps with Xcode and the iOS Simulator
  platform: darwin    status: MISSING
  server mobilebuildmcp 2.7.1 — MobileBuildMCP (getsentry; formerly XcodeBuildMCP)  [MISSING]
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
  socket, Figma's 3845, rosbridge's 9090). **any**: one of `of` passes.
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
  `upgrade <server>` does: it re-runs the probes, starts the new version,
  reads its `tools/list` (it aborts and keeps the pin if the new version does
  not answer), records the manifest, rewrites the opencode entry and backs
  the old one up.
- **Same version, different tools** (or an app-bundled server like Figma,
  whose version follows the app). `refresh` starts the server, speaks MCP
  (`initialize` + `tools/list`, paged, with a timeout), and records a manifest
  in `<state>/manifests/<server>/<version>.json` and `latest.json`. The
  manifest holds each tool's name, description and input schema, plus a hash
  over all of them. `refresh` diffs it against the previous manifest and
  reports added, removed and changed tools. A **new** tool whose name
  matches `risk_patterns` and is not covered by `ask_tools` or `deny_tools`
  is flagged for review.
- **New capability, or a new server for an existing one.** Add it to the
  catalog (or an overlay). `servers` is a preference list. When the preferred
  server is not the one you enabled, `refresh` says so.
- **The catalog pin moves** (a repo update). `refresh` notices that your entry
  still runs the old version and prints the `upgrade --to` command.

## opencode

The opencode config is `$OPENBEAST_OPENCODE_CONFIG`, else
`${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json`, or `--config`.
Both schemas are supported. The choice comes from `OPENBEAST_OPENCODE_SCHEMA`,
then the file itself, then `opencode --version`:

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
  the same key exists that beast-apps did not write, and when your own entry
  already runs the same package (pass `--allow-duplicate` to add it anyway).
- `enable` refuses (exit 6, nothing written) when the config cannot hold the
  server's gates: a v1 `"permission": "allow"` string has no room for a
  per-tool rule. Make `permission` an object, or pass `--ungated` to wire the
  server in without them; `gated:` lists only rules that were written.
- Every write first copies the old file to `opencode.json.bak-beast-apps-<ts>`
  (mode 600), then writes atomically, keeping the file's mode. The file
  becomes 600 when a secret env var is copied in.
- `disable` removes only the keys and rules recorded in
  `<state>/enabled.json`, and removes containers it emptied. A file that was
  enabled and then disabled is identical to the original. Ownership lives in
  the state dir, because v2 forbids marker fields in the entry.
- The config is never rewritten if it is not plain JSON (JSONC comments).
- `argv[0]` is written as an absolute path, so a GUI-launched client does not
  hit `spawn npx ENOENT`.

The state dir is `$OPENBEAST_APPS_STATE`, else `~/.openbeast-client/apps/`. It
holds `enabled.json`, `pins.json`, `manifests/` and `catalog.d/`.

## Offline

The catalog, the probes, `check`, `enable` and `disable` never touch the
network. `refresh` and `upgrade` decide whether they are offline in this
order: `OPENBEAST_APPS_OFFLINE=1`; `OFFLINE=true` (the install-level switch);
the rig's island state; and finally beast-island's connect probe
([BEAST_ISLAND.md](BEAST_ISLAND.md), `OPENBEAST_ISLAND_PROBES`). Offline,
`refresh` prints the beast-island banner, keeps every pin, skips the registry
lookups, and runs introspection with `npx --offline` / `uvx --offline`, so a
cached package still answers and an uncached one fails fast. `upgrade` without
`--to` exits 5 and says why.

## Safety

- **The server runs with your user's rights.** Building a project runs its
  build scripts. `execute_blender_code` runs arbitrary Python in Blender.
  replicant's `adb-shell` runs device commands behind its own denylist. Each
  of these is an opencode **ask** rule, and the user approves every call.
- **Physical actions.** `mcp-3d-printer-server` has no confirmation prompt of
  its own (upstream: "the client's own print action is the approval"), so it
  is **off by default**. `enable 3d-print` needs `--accept-risk`, and every
  tool that starts, resumes or heats a print is gated. ROS publish, service
  and action calls move real robots and are gated with globs.
- **Egress.** Telemetry is switched off where the server offers a switch:
  `MOBILEBUILDMCP_SENTRY_DISABLED`, `MOBILEMCP_DISABLE_TELEMETRY`, Blender's
  `DISABLE_TELEMETRY`. Mobile MCP's cloud-device tools and Blender's
  asset/generation tools send work off the machine. The cloud-device tools
  are gated, and the asset tools are called out in the server notes.
- **Pins, not `@latest`.** Every launch command pins an exact version. The
  validator refuses an unpinned `npx`/`uvx` command.
- **Tool surface.** MobileBuildMCP enables only the `simulator` workflow.
  Every advertised tool costs a local model context, so widen it in the
  catalog (`MOBILEBUILDMCP_ENABLED_WORKFLOWS`) only when needed.

## Seeded entries (verified 2026-11-15)

| Capability | Server | Pin | Source |
|---|---|---|---|
| ios-build | `mobilebuildmcp` (getsentry, formerly XcodeBuildMCP; launch `… mcp`) | 2.7.1 | github.com/getsentry/MobileBuildMCP, npm |
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
