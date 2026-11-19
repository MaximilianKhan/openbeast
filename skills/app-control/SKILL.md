---
name: app-control
description: Control an external application through an MCP server with beast-apps — iOS builds (Xcode, simulators), Android builds (Gradle, adb, emulators), mobile UI automation, Blender, Figma, 3D printers, ROS robots/simulators. Activate BEFORE any task that needs one of those apps ("build the iOS app", "run it on the emulator", "model this in Blender", "slice and print", "move the robot"), when the user asks whether an app can be driven from here, or after an app or MCP server was updated. It decides from a probe, not a guess: check first, report the exact fix when something is missing, enable only after a passing check.
allowed_tools: [bash, read_file]
recommends_subagent: false
prompt_index: false
---

# app-control — drive external apps through beast-apps

**What it is.** `scripts/apps.sh` (docs/BEAST_APPS.md) holds a catalog of
*capabilities* (`ios-build`, `android-build`, `mobile-ui`, `blender`, `figma`,
`3d-print`, `sim-ros`), each served by pinned MCP servers, and probes that
tell whether THIS machine has what a server needs.

**Where things run.** Tools execute where the opencode process runs. In client
mode that is the user's Mac: `apps.sh` checks and configures *that* Mac, and
the MCP servers it enables run there, beside `openbeast-tools`. The model on
the rig only sees tool calls. So run `apps.sh` with your `bash` tool — never
reason about the rig's toolchain. (opencode running on spark-1 or spark-2
has no OpenBeast MCP servers wired yet: there `apps.sh` checks that Spark.)

## The rule

> Never improvise an app integration. Probe, then act on what the probe said.

No ad-hoc `xcodebuild` / `adb` / `npx some-mcp` setup, no installing
toolchains, no editing `opencode.json` by hand.

## Steps

1. **Which capability?** If the request does not name one:
   `scripts/apps.sh find <words from the request> --json`.
   No match → tell the user nothing in the catalog covers it. Stop.

2. **Check it:** `scripts/apps.sh check <capability> --json`.
   Exit 0 = READY, 3 = MISSING, 4 = UNSUPPORTED on this platform.

3. **MISSING → stop and report.** Give the user the `remediation` list
   *verbatim*, in order (it names exactly what is absent and the command
   that fixes it, e.g. "Xcode.app is not installed…", "select it with
   `sudo xcode-select -s …`"). Do not try the fixes yourself: they need sudo,
   the App Store, or a GUI. Ask them to re-run the check when done.
   Do not fall back to a different approach that pretends the app works.

4. **UNSUPPORTED → say so.** E.g. iOS builds need macOS. Name the platform it
   needs. Stop.

5. **READY but not enabled** (`enabled` is null) → propose, don't assume:
   "I can enable `<server>` with `scripts/apps.sh enable <capability>` — it
   adds one MCP entry to your opencode config (backed up first). Then restart
   opencode." Run it only after the user agrees. Exit 6 means the server is
   off by default for safety (e.g. `3d-print`: no print confirmation) — show
   the `risk` text and let the USER decide on `--accept-risk` — or that the
   opencode config cannot hold its ask rules (the output says so; `--ungated`
   is the user's call too). Exit 7 means the user already has their own entry
   for it, or beast-apps already wired it: use what is there.

6. **READY and enabled** → use its tools. Pass `warnings` on to the user once
   (e.g. "adb is not on your shell PATH"). They do not block.

7. **Tools you do not know yet.** `scripts/apps.sh tools <server> --json`
   prints the recorded tool manifest (names, descriptions, input schemas).
   No manifest yet → `scripts/apps.sh refresh <server>`.

## After an update

When the user says an app or MCP server was updated, or a tool you expected is
missing or new: `scripts/apps.sh refresh --json`. It reports, per server:

- `update_available` — a newer published version. Tell the user; an upgrade is
  THEIR call: `scripts/apps.sh upgrade <server>`. Never run it unasked.
- `diff.added` / `diff.removed` / `diff.changed` — tools that appeared,
  vanished or changed schema. Re-read them with `apps.sh tools <server>`.
- `diff.flagged` with `gated: false` — a new tool that looks dangerous
  (exec, shell, print…) and is not behind an ask rule. Point it out.
- `offline: true` — no internet; pins were kept. Not an error.

## Gated tools

Some tools are behind opencode "ask" rules (Blender's `execute_blender_code`,
adb shell, printer start/heat, robot publish). When opencode asks, the user
decides. Never phrase a call to slip past that, and never retry a denied call.
