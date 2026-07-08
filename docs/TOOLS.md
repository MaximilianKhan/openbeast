# Tool inventory & provenance

The single source of truth for **every tool a model can call in OpenBeast**:
what it does, where the code lives, what external software powers it, and
which surfaces can see it.

TL;DR: **all 17 MCP tools are custom OpenBeast code** — there is no
third-party tool plugin in the chain. The open source projects we pull in
(llama.cpp, Open WebUI, SearXNG, MCPO, OpenCode) provide *serving,
frontends, search, and transport*; the tools themselves live in this repo.

## The three tool surfaces

| Surface | Transport | Tools visible |
|---|---|---|
| **Open WebUI** (browser chat) | MCPO proxy → OpenAPI (`localhost:3001`) | 17 (admin) / 1 (guest — see RBAC) |
| **OpenCode** (terminal agent) | MCP stdio (`opencode.json`) | 17 from us, *plus OpenCode's own built-in tools* (see below) |
| **Autonomous runner** (`agent.sh`, `start_agent`) | in-process (`agents/runner.py` → `agents/tools.py`) | 9 |

## The 17 MCP tools

All implemented in this repo. `agents/tools.py` holds the hardened
implementations; `agents/mcp_server.py` registers them with MCP and adds the
agent-management and skills layers on top.

### Code & files (5) — `agents/tools.py`

| Tool | Notes |
|---|---|
| `read_file` | Numbered lines; refuses non-regular files (`/dev/zero`, FIFOs) and >64 MB slurps |
| `write_file` | Creates parent dirs; **refuses credential/persistence paths** (`~/.ssh`, `~/.gnupg`, `~/.aws`, shell rc files, `/etc`, `.git/config`) — realpath-resolved so symlinks can't dodge |
| `edit_file` | Exact-string replacement with uniqueness checks; same write guard |
| `list_files` | Recursive glob, capped at 200 entries |
| `grep` | Shells out to system `grep -rn -E` through the reaped runner |

### Shell + web (3) — `agents/tools.py`

| Tool | Powered by |
|---|---|
| `bash` | `/bin/sh` via `run_reaped`: whole-process-group SIGKILL on timeout, 32 GB `RLIMIT_AS` on children, parent-side output capped at 4 MB (a `cat /dev/zero` cannot OOM the box — learned the hard way, see `docs/TODO.md` post-mortem) |
| `fetch` | Python stdlib `urllib` + in-repo HTML→text stripper. No third-party fetch service |
| `web_search` | **SearXNG** (self-hosted container, `localhost:8888`) — the one tool backed by a pulled-in service. No external API keys, no tracking |

### Agent management (5) — `agents/mcp_server.py`

`start_agent`, `check_agent`, `tail_agent`, `list_agents`, `stop_agent` —
spawn and supervise autonomous background agents (`agents/runner.py`). All
custom; agents log to `agents/logs/agent-{id}.jsonl`.

### Skills (4) — `agents/mcp_server.py`

`list_skills`, `load_skill`, `start_skill_agent`, `reload_skills` —
progressive-disclosure access to the markdown skill library in `skills/`.
All custom.

## The autonomous runner's 9 tools

`agents/runner.py` binds `TOOL_SCHEMAS` from `agents/tools.py` directly (no
MCP hop): `bash`, `read_file`, `write_file`, `edit_file`, `list_files`,
`grep`, `fetch`, `web_search`, plus `task_done` (the runner's completion
signal — not exposed over MCP). Background agents deliberately do **not**
get agent-management or skills tools; no recursive agent spawning.

## What the pulled-in projects contribute (and what they don't)

| Project | Contributes | Does NOT contribute tools |
|---|---|---|
| **llama.cpp** | Model serving (`llama-server`, OpenAI-compatible API) | — |
| **Open WebUI** | Chat UI, accounts, RBAC enforcement, per-chat tool toggles | We don't enable its built-in web-search/code-interpreter/image-gen; the tool surface it shows is ours via MCPO |
| **MCPO** | Transport only: wraps our MCP server as OpenAPI for WebUI | — |
| **SearXNG** | The search backend behind our `web_search` tool | It's a service, not a tool — the tool code is ours |
| **OpenCode** | Its *own* native tool suite (its `bash`, `edit`, `view`, …) alongside our 17 via MCP stdio | OpenCode's built-ins are upstream's code and are documented upstream |
| **MCP Python SDK** | The protocol plumbing `mcp_server.py` is written against | — |

## RBAC visibility (who sees what)

Two MCPO connections are configured by `scripts/configure-webui.sh`
(details: `docs/RBAC_PLAN.md`):

- **Admin** (WebUI admin role): all 17 tools.
- **Guest** (WebUI user role): `web_search` only. No filesystem, no shell.

## Why 17 and not more

Deliberate. The production review (`docs/PRODUCTION_ROADMAP.md` §B) found
the current pain is *too much always-on meta-machinery for a local model's
context* (9 of 17 tools are agent-mgmt/skills plumbing), not missing
capabilities. Expansion is planned and researched — sandboxed execution
(Sandlock), semantic code search (ChunkHound), and a Playwright browsing
*skill* — in `docs/TOOL_ARSENAL_RESEARCH.md`, gated behind Arsenal Phase 1
so new power arrives together with stronger sandboxing.

## Verifying the live surface

```bash
# What MCPO is actually exposing right now:
curl -s http://localhost:3001/openapi.json | python3 -c \
  "import json,sys; [print(p) for p in json.load(sys.stdin)['paths']]"

# What the test suite pins (schema/handler parity, MCP registration):
python3 -m pytest tests/test_tools.py -q
```
