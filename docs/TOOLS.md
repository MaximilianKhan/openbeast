# Tool inventory & provenance

The single source of truth for **every tool a model can call in OpenBeast**:
what it does, where the code lives, what external software powers it, and
which surfaces can see it.

TL;DR: **all 19 MCP tools are custom OpenBeast code** — there is no
third-party tool plugin in the chain. The open source projects we pull in
(llama.cpp, Open WebUI, SearXNG, OpenCode) provide *serving, frontends,
and search*; the transport is our own identity tool server
(`agents/openapi_tools.py`); the tools themselves live in this repo.

## The four tool surfaces

| Surface | Transport | Where tools execute | Tools visible |
|---|---|---|---|
| **Open WebUI** (browser chat) | identity tool server (`agents/openapi_tools.py`) → OpenAPI (`localhost:3001`) | rig | 20 (admin: the 19 MCP tools + `weather`) / 3 (guest — see RBAC) |
| **OpenCode** (terminal agent) | MCP stdio (`opencode.json`) | rig | 19 from us, *plus OpenCode's own built-in tools* (see below) |
| **Autonomous runner** (`agent.sh`, `start_agent`) | in-process (`agents/runner.py` → `agents/tools.py`) | rig | 10 |
| **OpenBeast client** (`scripts/setup-client.sh`) | MCP stdio into OpenCode *on the client device* (`~/.config/opencode/opencode.json`) | **the client's disk** | 19, inference over the tailnet at `:8443` |

The client row is the one that breaks the usual assumption: tools execute
where the *process* runs, not where the model runs. On a client, `bash`,
`write_file`, and `edit_file` act on that laptop's files while thinking on the
rig's GPU — the only thing crossing the tailnet is an OpenAI-compatible call to
`https://<rig>:8443/v1` (plus `web_search` to `:8889` when the rig publishes
it). RBAC **deliberately does not apply on this path**: the identity server
(`:3001`) is rig-local and never published, and a client is a single-user
device, so there is no role to enforce. `OPENBEAST_MCP_TOOLS` (a registration
allowlist read by `agents/mcp_server.py`) is the only scoping lever if a shared
client ever needs one. See `docs/BEAST_SLOT.md`.

## The 19 MCP tools

All implemented in this repo. `agents/tools.py` holds the hardened
implementations; `agents/mcp_server.py` registers them with MCP and adds the
agent-management and skills layers on top.

### Code & files (5) — `agents/tools.py`

| Tool | Notes |
|---|---|
| `read_file` | Numbered lines; refuses non-regular files (`/dev/zero`, FIFOs) and >64 MB slurps |
| `write_file` | Creates parent dirs; **refuses credential/persistence paths** (`~/.ssh`, `~/.gnupg`, `~/.aws`, shell rc files, `/etc`, `.git/config`) — realpath-resolved so symlinks can't dodge |
| `edit_file` | Exact-string replacement with uniqueness checks; same write guard |
| `list_files` | Recursive glob, capped at 200 entries; the walk itself stops after 20 000 matches or 5 s with a "stopped early" note (a huge tree no longer runs whole in-process) |
| `grep` | Shells out to system `grep -rn -E` through the reaped runner |

### Shell + web (3) — `agents/tools.py`

| Tool | Powered by |
|---|---|
| `bash` | `/bin/bash -c` (`/bin/sh -c` where bash is absent; runner v2) via `run_reaped`: whole-process-group SIGKILL on timeout (and on return: background processes do not outlive the call), 32 GB `RLIMIT_AS` plus NPROC/FSIZE/CPU caps on children on Linux — macOS never enforces `RLIMIT_AS`, so there a parent-side guard SIGKILLs the group past 32 GB RSS or 1024 processes (no FSIZE/CPU cap), parent-side output capped at 4 MB (a `cat /dev/zero` cannot OOM the box — learned the hard way, see `docs/TODO.md` post-mortem). Sandbox hook: set `OPENBEAST_BASH_WRAPPER` to a command prefix (`sandlock run -p openbeast -w "$PWD" --`, single-quoted — see `docs/SANDBOXING.md`) and every model command runs through it — Arsenal Phase 1 ships the Sandlock profile; unset (default) is the eval-validated configuration. The child env is scrubbed of stack secrets, and the spawning process (tool server, MCP server, runner) makes itself non-dumpable (`PR_SET_DUMPABLE=0`) before its first shell or agent spawn, so a model shell can't read the parent's secrets back from `/proc/$PPID/environ`; `OPENBEAST_KEEP_DUMPABLE=1` opts out (e.g. to attach py-spy/gdb) |
| `fetch` | Python stdlib `urllib` + in-repo HTML→text stripper. No third-party fetch service. SSRF-guarded at resolve time *and* re-checked at connect time against a pinned IP (see below). A 45 s whole-request deadline (connect, TLS, headers, redirects, body) cuts slow-drip servers, returning any partial body with a note; the HTML stripper is linear-time on hostile markup, and a byte cut inside a tag of any length drops that half tag instead of leaking it as text. Returns at most 200 000 chars whatever `max_length` asks for (a truncation marker says so; env `OPENBEAST_FETCH_MAX_CHARS` overrides, up to 2 000 000) |
| `web_search` | **SearXNG** (self-hosted container, `localhost:8888`) — the one tool backed by a pulled-in service. No external API keys, no tracking. The endpoint is `SEARXNG_URL`-indirected, which is how client mode points a laptop's local tool at the rig's search over the tailnet (`docs/BEAST_SLOT.md`). On a rig with a specific `BIND_HOST`, `scripts/lib/conf.sh` exports `SEARXNG_URL=http://<BIND_HOST>:8888` unless you set it yourself |

**`web_search` deliberately bypasses the `fetch` guard.** It calls `SEARXNG_URL`
through plain `urllib`, with no `_fetch_url_blocked` check, because that URL is
*supposed* to be non-public: `http://localhost:8888` on a rig, or
`https://<rig>:8889` (a tailnet address) on a client. Routing it through the
guard would refuse both. The exposure is bounded — the URL comes from operator
config, not from the model, and only `/search?q=…` is ever constructed — but it
is a deliberate hole, not an oversight.

**In island mode both web tools refuse at once** (beast-island,
`docs/BEAST_ISLAND.md`). The check sits in `agents/mcp_server.py`, so it covers
the MCP and `:3001` surfaces. It deliberately stays out of `agents/tools.py`,
which is part of the eval era hash, so the autonomous runner still fails these
calls by timeout. Open WebUI's own Web Search toggle goes to SearXNG directly
and is not covered either.

### Agent management (5) — `agents/mcp_server.py`

`start_agent`, `check_agent`, `tail_agent`, `list_agents`, `stop_agent` —
spawn and supervise autonomous background agents (`agents/runner.py`). All
custom; agents log to `agents/logs/agent-{id}.jsonl`.
`start_agent(task, workdir, max_iter, context, base_url)` — the `base_url`
parameter (opt-in, distributed agents Phase 1) routes the spawned agent's
*inference* to a worker box on your tailnet while it keeps executing on this
machine; empty defaults to `OPENBEAST_AGENT_INFERENCE_URL`, else the local
server. See `docs/DISTRIBUTED_AGENTS_PLAN.md`.
`stop_agent` also stops a detached agent this process did not spawn (from
the session ledger), but only `kind="agent"` sessions: a `scripts/job.sh`
job (eval campaign, quantization run) is refused and stays stoppable with
`scripts/job.sh stop <id>`.

### Skills (2) — `agents/mcp_server.py`

`skill`, `start_skill_agent` — progressive-disclosure access to the markdown
skill library in `skills/`. `skill()` with no name returns the index (fresh
disk re-scan every time, so no reload tool is needed); `skill(name)` loads
one skill's full body, with fuzzy-match suggestions on a miss.
`start_skill_agent(skill, task, ...)` spawns a background agent with the
skill pre-activated (also accepts `base_url`). All custom. The former
`list_skills` / `load_skill` / `reload_skills` trio was collapsed into
`skill` (PRODUCTION_ROADMAP §B — fewer always-on meta-tools in a local
model's context).

### Artifacts (2) — `agents/mcp_server.py`

`publish_artifact`, `list_artifacts` — hand a self-contained HTML file to
beast-artifact and get back a durable tailnet URL (`:8446/a/<uuid>`), private
by default, re-publishable into a new version at the same URL. The store and
client helpers live in `agents/artifact.py` (deliberately *not* in
`agents/tools.py`, which stays byte-identical so the eval cache era doesn't
roll). The tools call that store **in process** — they do not speak HTTP to
the artifact server; `:3004` exists to *serve* the pages, and
`scripts/artifact.sh` is the client that talks to it (with the proof-of-
locality token, on write verbs only). Two consequences: the tool path writes
no `.run/artifact-audit.jsonl` row, and it checks `BEAST_ARTIFACT` itself
rather than discovering the service is off by failing to connect. Opt-in via
`BEAST_ARTIFACT=true`. Neither tool is in `GUEST_TOOLS` (guest → 404) and
neither is in the autonomous runner's registry — background agents publish
through `scripts/artifact.sh` with `bash`. Authoring rules, the sandbox/CSP
posture, versions and visibility: [`docs/BEAST_ARTIFACT.md`](BEAST_ARTIFACT.md).

### Language reference (1) — `agents/mcp_server.py`

`language_reference(language, topic="", error="")` — beast-lang's **pull**
surface ([`docs/BEAST_LANG_PLAN.md`](BEAST_LANG_PLAN.md) §7 P5): a read-only
window onto what the toolchain **installed on this rig** confirmed about a
language. `error` given → the cards for that compiler diagnostic (the same
matcher the escalation layer uses); `topic` given → the VERIFIED claims whose
topic, summary or named identifiers match, most specific first, plus what the
toolchain answers when asked about that one name (GENERATED, labelled —
"`std.io` does NOT exist … the installed spelling is `std.Io`"); neither →
the language's pack. Three properties are the point:

- **It never improvises.** Every line is VERIFIED (old form compiled and
  failed, new form compiled — re-checked at the moment of asking) or
  GENERATED (the toolchain was asked). A miss says "no verified reference
  for …"; there is no fuzzy match and no prose tier.
- **It cannot raise and it is bounded.** It goes through the no-raise facade
  in `agents/lang/__init__.py`, imports it lazily (a broken beast-lang costs
  this one tool an `Error:`, not the surface), and clips at ~8 KB on a line
  boundary, saying so. With the toolchain absent, or a language off the
  `LANG_PACKS` allow list, the answer is the list of languages this rig *can*
  serve. Every toolchain call it makes (`agents/lang/_proc.py`) is reaped as
  a process group and memory-capped at `OPENBEAST_LANG_AS_LIMIT_MB` (8 GiB):
  `RLIMIT_AS` on Linux; on macOS, which never enforces `RLIMIT_AS`, a
  parent-side guard SIGKILLs the group past that much RSS (polled every
  0.5 s).
- **A snippet that would read the host is refused, not compiled.** A host
  `#include`/`#embed`, a Rust `include!`/`env!`, or a zig `@embedFile` of an
  absolute path is refused before the compiler starts
  (`agents/lang/drivers.py`). A leading UTF-8 BOM is stripped before that scan
  and before the file is written, because the compilers skip it and the scan's
  `^` once sat in front of it (`tests/test_lang_hardening.py`).
- **It is not how a local model learns a language.** The repo measured that
  local models do not call optional tools (plan §2.1), so delivery for them is
  push (the pack) and escalate (the compile-error card). This tool is for the
  caller that does stop to look something up — a cloud model, a person at the
  WebUI — which is why it is **not** in the runner's registry below and must
  clear a v5-suite eval before it ever is. The companion for cloud models
  working *in this repo* is the `beast-lang` skill.

Admin profile only: it is harmless, and `GUEST_TOOLS` stays web-only anyway
(guest → 404) — a profile that is "web-only plus whatever looked safe" is not
one anybody can reason about.

### Platform docs (1) — `agents/mcp_server.py` → `agents/docs/`

`platform_docs(query="", source="", doc="", live=False)` — beast-docs
([`docs/BEAST_DOCS.md`](BEAST_DOCS.md)): current Apple (SwiftUI, UIKit,
Swift, Xcode, App Review Guidelines), Android (Compose, Kotlin, AGP) and
other developer documentation from a **local mirror** built by
`scripts/docs.sh sync` (or carried in with `docs.sh unpack`). `query` →
ranked hits (SQLite FTS5 / bm25, exact title first) with doc id, URL and
fetched date; `doc` → that page as markdown; `doc` + `live=true` → the
official page now, through the same adapter; nothing → what is mirrored and
how old. Properties:

- **Every answer carries its URL and fetched date**, and says STALE past the
  source's refresh window. It is the publisher's text, labelled as such —
  not compiler-verified (that is `language_reference`).
- **Offline-first.** Search and read never touch the network. Live mode is
  island-aware (`OFFLINE=true` or island mode → it does not dial, and serves
  the mirror copy with its age, saying so on the first line).
- **Live mode fetches only configured doc hosts** (`agents/docs/sources.toml`),
  redirects included, obeys robots.txt, and goes through the same
  resolve-vet-pin SSRF machinery as `fetch` (imported from `agents/tools.py`,
  not copied).
- **No-raise and bounded:** lazy import, the package's no-raise facade
  (`agents/docs/__init__.py`), output clipped at 12 000 chars with a notice.

Admin profile only (not in `GUEST_TOOLS`); **not** in the runner's registry.
In client mode it reads the laptop's own mirror (`~/.openbeast/docs`), like
every tool. The `platform-docs` skill teaches when to use it and the
`scripts/docs.sh` equivalent for bash.

## The WebUI-only tool: `weather`

The WebUI tool server (`:3001`, `agents/openapi_tools.py`) serves the 19 MCP
tools **plus `weather`** (`agents/weather.py`). It is not an MCP tool on
purpose: it needs to know *who* is calling, and only the WebUI path carries
that.

- **Why a tool:** `web_search` for "weather Danville, CA" can land on
  Danville, Quebec, because "CA" is also Canada's country code. `weather`
  resolves the place deliberately and names the exact place in its reply:
  - `City, XX` with a US state code means that state whenever the state
    has such a town; otherwise XX is a country code, then a Canadian
    province (`Berlin, DE`, `Mumbai, IN`, `Toronto, ON`);
  - a spelled-out state must match exactly (`Charleston, VA` is not West
    Virginia); a spelled-out country matches the geocoder's country name
    (`Tokyo, Japan`, `Sao Paulo, Brazil`);
  - `Georgia` alone is the state or the country by the caller's position,
    and with no position the tool lists both and asks; `Georgia, US` (or
    `USA`, `GA`, `United States`) is the state, never one of the hamlets
    named Georgia, `Georgia, GE` the country, `Georgia, AL` the town.
- **Where the caller is:** a bare town name picks the one **nearest the
  caller**, and no location means the weather where the caller is now. The
  position is the device geolocation Open WebUI keeps when the user turns on
  **Settings → Interface → Allow User Location** (`user.info.location`). The
  tool server reads it for the calling user with the WebUI admin credentials
  (`WEBUI_ADMIN_*`). That signin and lookup go straight to Open WebUI on
  `127.0.0.1:3000`: they ignore `http_proxy` (an operator proxy set for
  `fetch` would otherwise receive the admin password in cleartext) and
  never follow a redirect (it would carry the admin bearer elsewhere).
- **No fixed home.** With no position, the tool asks which place.
- **Units** follow the place's country; for the caller's own position, the
  country of its timezone (from the forecast reply, no extra request). The
  model can pass `units='metric'|'imperial'` when the user asks.
- **Failures** (Open-Meteo down, 429, timeout) come back as one sentence
  telling the model not to retry, never as a 500. The admin token used for
  the position lookup is cached; a failed signin backs off five minutes,
  because Open WebUI rate-limits signin per email and the human admin
  shares that budget.
- **Data:** Open-Meteo forecast and geocoding, two fixed hosts with no key.
  In island mode it refuses at once, like the other web tools.
- **RBAC:** it is a web read, so guests get it next to `web_search` and
  `fetch`: it is in `GUEST_TOOLS` and on the all-users connection that
  `configure-webui.sh` writes (re-run it after upgrading). It reads only
  the caller's own WebUI record, with the admin credentials.

## The autonomous runner's 10 tools

`agents/runner.py` binds `TOOL_SCHEMAS` from `agents/tools.py` directly (no
MCP hop): `bash`, `read_file`, `write_file`, `edit_file`, `list_files`,
`grep`, `fetch`, `web_search`, plus two loop-only tools not exposed over
MCP: `task_done` (the completion signal) and `update_plan` (a step ladder —
pending / in_progress / done / skipped, one in_progress at a time — held on
a ContextVar and re-injected into every request as a compact block, so it
survives context compaction; Codex `update_plan` semantics). Background
agents deliberately do **not** get agent-management or skills tools; no
recursive agent spawning.

The loop also manages its own context window (2026-09-11): on the server's
`exceed_context_size_error` (or with `--context-budget` set, past ~70% of
the budget at 4 chars/token) it replaces the oldest tool results with
one-line stubs (`[tool result elided: N chars, call #k]`) — never the system
prompt, the task, or the plan — logs each event to stderr and the JSONL log,
and reports `COMPACTIONS: n` next to the `TOKENS:` line.

## What the pulled-in projects contribute (and what they don't)

| Project | Contributes | Does NOT contribute tools |
|---|---|---|
| **llama.cpp** | Model serving (`llama-server`, OpenAI-compatible API) | — |
| **Open WebUI** | Chat UI, accounts, RBAC enforcement, per-chat tool toggles | We don't enable its built-in web-search/code-interpreter/image-gen; the tool surface it shows is ours via the identity tool server. One Open WebUI *function* is ours: the global `random_seed` filter (`agents/webui_functions/random_seed.py`, installed by `configure-webui.sh`, off with `WEBUI_RANDOM_SEED=false`) gives each unseeded chat request a random seed, because TensorFold's fixed default seed made Regenerate repeat the same answer. It is not a tool and the model never sees it |
| **Identity tool server** (`agents/openapi_tools.py`, ours) | Transport + identity: serves the tools as OpenAPI for WebUI, shards workspaces per user, checks RBAC keys, writes the audit trail | — |
| **SearXNG** | The search backend behind our `web_search` tool | It's a service, not a tool — the tool code is ours |
| **OpenCode** | Its *own* native tool suite (its `bash`, `edit`, `view`, …) alongside our 18 via MCP stdio | OpenCode's built-ins are upstream's code and are documented upstream |
| **MCP Python SDK** | The protocol plumbing `mcp_server.py` is written against | — |

## RBAC visibility (who sees what)

Two WebUI connections to the one identity server are configured by `scripts/configure-webui.sh`
(details: `docs/RBAC_PLAN.md`):

- **Admin** (WebUI admin role): all 20 tools (the 19 MCP tools + `weather`).
- **Guest** (WebUI user role): `web_search`, `fetch` and `weather`. No filesystem, no
  shell. Guest `fetch` is SSRF-guarded: http/https only, loopback/private/
  link-local/reserved targets refused, redirects re-validated per hop, and the
  vetted IP is pinned for the actual connect so a DNS flip can't slip through
  (the guard applies to all users — defense in depth). With `http_proxy` /
  `https_proxy` set (honoring `no_proxy`), fetch dials the operator's proxy
  and the target is vetted by name on every hop; the IP pin cannot cover a
  proxied target, because the proxy does its own resolution.
- **Tailnet is its own blocked class.** `_vet_addr` in `agents/tools.py` pins
  Tailscale's CGNAT range `100.64.0.0/10` and the default v6 ULA range
  `fd7a:115c:a1e0::/48` explicitly, rather than relying on the stdlib: CPython's
  `is_private` classification of CGNAT changed in 3.12.4/3.11.9 (gh-113171), so
  inheriting it would make the tool's behavior depend on the interpreter.
  `_unwrap_v6` first unwraps IPv4-mapped (`::ffff:100.64.1.2`), 6to4, and
  Teredo forms, which would otherwise sail past a v4-only range check while
  still dialing the v4 target. Blocked on every interpreter by default; opt out
  with `OPENBEAST_FETCH_ALLOW_TAILNET=true` (`1`/`yes` also accepted), which
  covers both families because MagicDNS answers A *and* AAAA. This is what stops
  a model from using `fetch` to reach the rig's `:8443`, another tailnet peer's
  services, or the dashboard on `:8444`.

> ✅ **Router is identity-aware (2026-07-08).** Open WebUI forwards
> `X-OpenWebUI-User-Role` (`ENABLE_FORWARD_USER_INFO_HEADERS=true` in
> docker-compose), and the agent-spawn router only runs its spawn path for
> `admin` turns — guest turns pass through untouched (and skip the classify
> entirely, so guests add zero latency). With signed identity
> (`IDENTITY_JWT_SECRET`), WebUI sends the role only inside
> `X-OpenWebUI-User-Jwt`: the router verifies that token (HS256,
> `iss=open-webui`, `exp`/`sub` required) and ignores the plain headers.
> A turn with no identity fails closed on its own whenever `WEBUI_AUTH=true`
> or signed identity is on; `OPENBEAST_ROUTER_REQUIRE_IDENTITY=true` forces
> that elsewhere too. Spawns carry the caller's identity to the tool server,
> which does the attribution and sharding. Details: `docs/RBAC_PLAN.md`.

## Why 19 and not more

Deliberate, and the number has moved in both directions. The production review
(`docs/archive/PRODUCTION_ROADMAP.md` §B, historical) found the pain was *too much
always-on meta-machinery for a local model's context* — which is why the
skill-discovery trio was collapsed into the single `skill` tool, taking the
surface from 17 down to 15 (7 of those 15 still agent-mgmt/skills plumbing).
beast-artifact's two tools took it back to 17 in 2026-09 and beast-lang's
`language_reference` to 18, beast-docs' `platform_docs` to 19 (2026-11); all
four are capability, not plumbing, so the meta-machinery share went *down*,
from 7/15 to 7/19.

**The real argument is tool-selection accuracy, which degrades as a registry
grows — so note which surface the three new tools do and do not appear on.** The
surface where a bad pick costs most is the autonomous runner's: it chooses a
tool every turn, unattended, with nobody to correct it. `publish_artifact`,
`list_artifacts`, `language_reference` and `platform_docs` ship on the **MCP/WebUI surface only**
and are deliberately **not** in `agents/runner.py`'s 10-tool registry, so the
runner's selection pressure is byte-for-byte what it was before — a background
agent that wants a URL shells out to `scripts/artifact.sh` through `bash`, and
one that wants language notes gets them pushed (`BEAST_PACKS`) rather than
asking. (The same split is what keeps `agents/tools.py` unchanged, so the eval
cache era doesn't roll; `evals/cache.py`.) On the MCP/WebUI side a human is in
the loop and "publish this as a page" is an unambiguous ask.

Further expansion was researched in 2026-07 — sandboxed execution (Sandlock),
semantic code search (ChunkHound), and a Playwright browsing *skill* — in
`docs/archive/TOOL_ARSENAL_RESEARCH.md` (historical). Of those, Sandlock
shipped as an opt-in wrapper ([`SANDBOXING.md`](SANDBOXING.md)) and a
Playwright skill was imported; the rest is not a current plan.

## Verifying the live surface

```bash
# Rig — what the identity tool server is actually exposing right now:
curl -s http://localhost:3001/openapi.json | python3 -c \
  "import json,sys; [print(p) for p in json.load(sys.stdin)['paths']]"

# Rig — the beast-slot discovery contract (dashboard extension must be enabled):
curl -s http://127.0.0.1:3002/api/slot

# Client — env file, venv, tailnet, rig reachability, opencode wiring, slot view:
openbeast-client status

# What the test suite pins (schema/handler parity, MCP registration):
python3 -m pytest tests/test_tools.py -q
```

**With beast-gate on, 404 is the success signal.** `agents/edge.py` allowlists
exactly `/health`, `/v1/models`, `/v1/chat/completions`, `/v1/completions`,
`/v1/embeddings`; every other path returns **404, not 403**, so a remote caller
cannot learn which routes exist. So:

```bash
curl -o /dev/null -w '%{http_code}\n' https://<rig>:8443/slots     # want 404
curl -s https://<rig>:8443/v1/models -H "Authorization: Bearer <key>"  # want 200
```

A `200` from `/slots` means the tailnet is still pointed at raw llama-server —
re-run `./scripts/setup-tailscale.sh` after setting `EDGE_GATE=true`. Details
and the full control table: `docs/BEAST_SLOT.md` § beast-gate.
