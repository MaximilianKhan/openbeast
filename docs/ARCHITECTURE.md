# Architecture & project layout

Two diagrams, two different jobs. **§1 is the trust model** — which machine
does what, and what is allowed to cross between them. **§2 is the service
map** — what actually runs inside the rig, and where identity is enforced.
Read §1 to understand the product; read §2 to understand the implementation.

## 1. The trust model — only inference crosses the wire

OpenBeast runs as one **command center** (the rig) and, optionally, any number
of **clients**. The load-bearing fact: **tools execute where the process runs,
not where the model runs.** A laptop runs the complete tool arsenal against its
*own* disk while every token is generated on the rig's GPU. The boundary
between the two machines is a single OpenAI-compatible HTTP call, and the
tailnet is the security perimeter around all of it.

> **This diagram's job:** show what crosses the machine boundary and what
> never does. Ports are annotations, not the structure.

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 340, "curve": "basis", "nodeSpacing": 45, "rankSpacing": 55}}}%%
flowchart LR
    subgraph TAILNET["🔒 YOUR TAILNET — WireGuard · device-authenticated · never funneled to the public internet"]

        subgraph CLIENT["💻 CLIENT — any Mac / Linux laptop &nbsp;(optional, purely additive)"]
            cagent["⌨️ <b>OpenCode</b> · <b>openbeast-client</b><br/>the agent loop runs HERE"]
            ctools["⚙️ <b>15 tools</b> — bash · files · grep · spawn<br/>they act on THIS laptop's disk"]
            cagent --> ctools
        end

        subgraph OTHER["📱 ANY OTHER TAILNET DEVICE — browser only"]
            phone["📱 <b>Phone · tablet · work laptop</b><br/>no OpenBeast install needed"]
        end

        subgraph RIG["🖥️ RIG — the command center &nbsp;(full stack, always on)"]
            gate["🛡️ <b>beast-gate</b> &nbsp;<i>(opt-in)</i><br/>per-device keys · route allowlist · audit"]
            brain["🧠 <b>llama.cpp + GPU</b><br/>every token is generated HERE"]
            cc["🌐 Open WebUI · 🔑 tool server<br/>🔎 SearXNG · 📊 dashboard<br/><i>all bound to 127.0.0.1</i>"]
            gate --> brain
        end
    end

    ctools ==>|"<b>INFERENCE ONLY</b> — prompts up, tokens down<br/>files &amp; shell never cross &nbsp;·&nbsp; :8443"| gate
    ctools -.->|"gate OFF = the default:<br/>straight to llama-server"| brain
    cagent -.->|"rig status :8444<br/>web search :8889"| cc
    phone ==>|"browser chat · :443"| cc

    classDef client fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0c2733;
    classDef rig fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#0b2417;
    classDef sec fill:#fef3c7,stroke:#d97706,stroke-width:2px,stroke-dasharray:5 3,color:#3a2503;
    class cagent,ctools,phone client;
    class brain,cc rig;
    class gate sec;
    style TAILNET fill:#fafaf9,stroke:#dc2626,stroke-width:3px,color:#7f1d1d;
    style CLIENT fill:#f0f9ff,stroke:#0284c7,stroke-width:2px,stroke-dasharray:6 4;
    style RIG fill:#f0fdf4,stroke:#16a34a,stroke-width:2px;
    style OTHER fill:#f8fafc,stroke:#64748b,stroke-width:2px,stroke-dasharray:6 4;
    linkStyle 2 stroke:#0284c7,stroke-width:4px;
```

**Reading it.**

- **The red box is the whole security perimeter.** Nothing in this
  architecture is ever reachable from the public internet: every service binds
  `127.0.0.1`, and the only way in is Tailscale's authenticated WireGuard mesh.
  `tailscale funnel` is deliberately never used, and `setup-tailscale.sh` never
  offers it.
- **The thick blue arrow is the only load-bearing crossing.** Prompts go up,
  tokens come down. The client's `bash`, `read_file`, `edit_file` and `grep`
  execute in the client's own process against the client's own disk — the rig
  never runs them and has no path to the client's filesystem.
- **But file *contents* do cross, as model context.** The model has to see data
  to reason about it, so whatever an agent reads on the laptop goes up in the
  next prompt. Both machines are yours and the transport is WireGuard, so the
  promise is **"nothing leaves your tailnet"**, not "nothing leaves this
  machine". The client's other dotted edge is the same trade for the rig's
  search and status surfaces — both opt-in publishes, and a client can run its
  own SearXNG instead (`--local-search`).
- **The client is purely additive.** The rig keeps running its full stack
  unchanged whether zero or ten clients are attached.
- **The dashed beast-gate box is the honest default.** With `EDGE_GATE=false`
  (shipped default) `:8443` maps straight at llama-server and publishes its
  *entire* route table — `/slots`, `/props`, `POST /lora-adapters`,
  `/v1/stream/<id>` — to every tailnet peer. That is fine on a tailnet you
  fully own. `EDGE_GATE=true` routes it through beast-gate instead, which
  allowlists the OpenAI routes and gives each device its own revocable key.
  Full detail: [`BEAST_SLOT.md`](BEAST_SLOT.md).

## 2. Inside the command center — two planes

The rig splits cleanly into two planes. The **tool plane** does things — runs
shell commands, reads and writes files, searches the web — and is *never*
published to the tailnet. The **inference plane** generates tokens and is the
only surface that is. Everything else is a frontend or an optional service
hanging off one of the two.

> **This diagram's job:** name the real processes, their ports, and the two
> places where identity is enforced. This is the implementation view; §1 is
> the one to reason about trust with.

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 380, "curve": "basis", "nodeSpacing": 45, "rankSpacing": 70}}}%%
flowchart TB
    webui["🌐 <b>Open WebUI</b> · :3000<br/>browser chat · accounts · roles"]
    opencode["⌨️ <b>OpenCode</b><br/>terminal coding agent"]
    agentsh["🔁 <b>agent.sh</b> → <code>runner.py</code><br/>headless autonomous agent"]
    remote["📡 <b>Remote tailnet device</b><br/>(diagram 1) · arrives on :8443"]

    subgraph TOOLPLANE["🔑 TOOL PLANE — runs on the rig, acts on the rig · NEVER published"]
        its["<b>Identity tool server</b> · :3001<br/><code>agents/openapi_tools.py</code><br/>RBAC profile keys · per-user file shards · audit<br/><i>authenticates the HUMAN</i>"]
        mcp["<b>MCP tool surface — 15 tools</b><br/><code>agents/mcp_server.py</code> · stdio, no port<br/>adds skill · start/start_skill/check/tail/list/stop_agent"]
        core["<b>Tool primitives</b> · <code>agents/tools.py</code><br/>bash · read/write/edit/list/grep<br/>fetch (SSRF-guarded) · web_search"]
        its --> mcp --> core
    end

    subgraph INFPLANE["🧠 INFERENCE PLANE — the ONLY surface published to the tailnet"]
        gate["🛡️ <b>beast-gate</b> · :8090 <i>(opt-in)</i><br/><code>agents/edge.py</code><br/>per-device keys · route allowlist<br/>rate + in-flight caps · audit<br/><i>authenticates the DEVICE</i>"]
        llama["<b>llama.cpp server</b> · :8080<br/>OpenAI-compatible · continuous batching<br/>unified KV · MTP speculative decode<br/>context auto-scaled to VRAM · 11 GB floor"]
        gate --> llama
    end

    router["🧭 <b>Agent router</b> · :8088<br/><i>(opt-in; OFF by default —<br/>WebUI then calls llama.cpp direct)</i>"]
    searxng["🔎 <b>SearXNG</b> · :8888<br/>private metasearch"]
    dash["📊 <b>Dashboard</b> · :3002 <i>(extension)</i><br/>serves /api/slot"]

    webui -->|"tool calls +<br/>identity headers"| its
    opencode -->|"MCP over stdio"| mcp
    agentsh -->|"imports the<br/>primitives directly"| core
    remote ==> gate

    webui -->|"chat completions"| router
    router -->|"no spawn intent →<br/>pass through"| llama
    router -.->|"spawn intent →<br/>start_agent"| its

    core -->|"web_search"| searxng
    core -.->|"spawned agents<br/>think on the GPU"| llama
    dash -.->|"health · slots · queue"| llama

    classDef fe fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0c2733;
    classDef tool fill:#ede9fe,stroke:#7c3aed,stroke-width:2px,color:#2a1150;
    classDef inf fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#0b2417;
    classDef sec fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#3a2503;
    classDef optin fill:#fef3c7,stroke:#d97706,stroke-width:2px,stroke-dasharray:5 3,color:#3a2503;
    classDef aux fill:#f1f5f9,stroke:#64748b,stroke-width:2px,color:#0f172a;
    class webui,opencode,agentsh,remote fe;
    class mcp,core tool;
    class its sec;
    class llama inf;
    class gate,router optin;
    class searxng,dash aux;
    style TOOLPLANE fill:#faf5ff,stroke:#7c3aed,stroke-width:3px,color:#3b0764;
    style INFPLANE fill:#f0fdf4,stroke:#16a34a,stroke-width:3px,color:#052e16;
```

**How to read it.**

- **Three frontends, one tool implementation.** The **browser path** (Open
  WebUI) calls the **identity tool server** (`agents/openapi_tools.py`, which
  replaced the generic MCPO proxy in v1.1): it reads the identity headers Open
  WebUI forwards on every tool call — plain `X-OpenWebUI-User/Chat` or, in
  enterprise mode, a signed JWT — then enforces the per-profile RBAC keys,
  shards each user's files into their own `users/<id>/` workspace, and writes
  an audit trail. The **terminal path** (OpenCode) speaks MCP over stdio to
  `agents/mcp_server.py`. The `:3001` server *imports* that same module rather
  than reimplementing it, so the HTTP and MCP surfaces expose the identical 15
  tools and cannot drift. `mcp_server.py` in turn delegates its eight file /
  shell / network primitives to `agents/tools.py` and adds `skill` plus the
  six agent-orchestration tools on top. The **headless path** (`agent.sh` →
  `agents/runner.py`) skips both servers and imports the primitives directly.
- **Two identity layers, deliberately separate — and they are the only two.**
  `:3001` (amber, solid) authenticates the *human*: WebUI user → RBAC tier →
  file shard. beast-gate on `:8090` (amber, dashed) authenticates the *device*:
  enrolled key → rate limits → inference audit. Before the gate, the inference
  path had no identity at all — every WebUI user, laptop, and spawned agent
  collapsed into one anonymous caller, because llama-server itself has no
  concept of a user.
- **Dashed borders and dashed arrows mean opt-in.** beast-gate needs
  `EDGE_GATE=true`; the agent router needs `AGENT_ROUTER=true` (off by default,
  in which case Open WebUI calls llama.cpp directly); the dashboard is an
  *extension* and `EXTENSIONS` ships empty, which matters because `:8444`
  publishes the dashboard's `/api/slot` — publish it before enabling the
  extension and clients get a 502 (see [`BEAST_SLOT.md`](BEAST_SLOT.md)).
- **Nothing in the tool plane is ever published.** With RBAC Phase 2 keys
  (`scripts/setup-mcpo-keys.sh`), every `:3001` tool call must present a
  profile key — **admin** reaches all 15 tools, **guest** reaches `web_search`
  + `fetch` only (anything else 404s). `:3001`, `:8088`, `:8888` and `:8080`
  are loopback-only; remote devices arrive exclusively through Tailscale's
  authenticated HTTPS proxy (see [Remote access](REMOTE_ACCESS_PLAN.md)).
- **Inference.** llama.cpp serves an OpenAI-compatible API with MTP
  speculative decoding; `serve.sh` auto-scales context to the card's VRAM
  (the shipped default serves 350K context across six unified-KV slots on the
  32 GB reference card), and bootstrap refuses GPUs under the 11 GB floor.

## Project structure

```
start.sh                     # Launch full stack (llama.cpp + identity tool server + Open WebUI + SearXNG)
stop.sh                      # Stop everything
agent.sh                     # Run an autonomous agent

scripts/                     # Server, chat, and ops scripts
  serve.sh / run.sh          # Generic launchers (pick model with -m)
  serve-<model>.sh           # Model-specific API servers
  serve-bootstrap.sh         # Tiny 0.6B bridge for fast-boot (FAST_BOOT)
  run-<model>.sh             # Model-specific interactive chat
  configure-webui.sh         # Auto-configure Open WebUI (tools + system prompt)
  healthcheck.sh             # Service health monitor (--restart to auto-recover)
  doctor.sh                  # Config/security/health diagnosis (./start.sh doctor)
  ext.sh                     # Extension manager (enable/disable/list optional services)
  verify-weights.sh          # Verify downloaded weights against weights.registry
  weights.registry           # sha256 + size pins for every shipped GGUF
  setup-tailscale.sh         # Publish to the tailnet (--publish-searxng/--publish-slot)
  clients.sh                 # RIG: device enrollment/revocation for beast-gate
  ssd-wear.sh                # SMART-based drive wear report (doctor row; --json/--snapshot)
  setup-client.sh            # CLIENT: install client mode (macOS/Linux; --api-key, --local-search)
  setup-mac-client.sh        # Back-compat wrapper → setup-client.sh
  client.sh                  # CLIENT: the openbeast-client CLI (status/agent/search/update)
  client-searxng.compose.yml # CLIENT: optional local SearXNG (bridge net, Docker Desktop-safe)
  lib/                       # Shared libs: conf.sh, hardware.sh, weights.sh, extensions.sh

agents/                      # Agent framework + tool servers
  mcp_server.py              # MCP tool server (15 tools, stdio MCP surface for OpenCode)
  openapi_tools.py           # Identity tool server on :3001 (WebUI surface: RBAC keys, per-user shards, audit)
  edge.py                    # beast-gate on :8090 — identity-aware INFERENCE edge (opt-in EDGE_GATE)
  runner.py                  # Autonomous agent loop (LLM + tool use)
  router.py                  # Agent-spawn router on :8088 (opt-in via AGENT_ROUTER=true)
  tools.py                   # Tool primitives (bash/files/grep/fetch/web_search) — the shared
                             #   core mcp_server.py wraps and runner.py imports directly
  requirements.txt           # openai, mcp, fastapi, uvicorn, PyJWT (pinned)
  logs/                      # Agent run logs (JSONL) [gitignored]

extensions/                  # Optional hot-pluggable services (see extensions/README.md)
  dashboard/                 # Status dashboard (GPU/model/services) on :3002

searxng/
  settings.yml               # Custom config: enables JSON format + disables limiter

tests/                       # Test suite
  run_tests.sh               # Run all tests
  test_tools.py              # MCP tool unit tests
  test_identity_server.py    # Identity tool server tests (headers, RBAC keys, sharding, audit)
  test_edge.py               # beast-gate: auth matrix, allowlist, admission control, audit
  test_clients.sh            # Device enrollment/revocation CLI
  test_ssd_wear.sh           # Drive-wear math + degraded paths (stubbed smartctl)
  test_beast_slot.py         # /api/slot discovery contract (v2 capacity math)
  test_manifest.py           # Per-shard write-manifest tests
  test_scripts.sh            # Script structure validation
  test_smoke.sh              # End-to-end stack smoke test (requires running stack)

evals/                       # Eval harness — 137 tasks / 291 units + multi-model benchmark
  README.md                  # Distribution table, schema, scoring (start here)
  run_eval.py                # Single-model eval runner (model-tagged results)
  scoring.py                 # Accuracy / speed / tokens + per-category & per-language breakdown
  benchmark_all.py           # Multi-model sweep orchestration
  tasks/                     # Per-task JSON definitions (numbered; gaps from v4 pruning) with category tags
  results/                   # Per-run results (kept all, model-tagged) [gitignored]
  leaderboard.json           # Latest score per model + per-category drilldown (auto-updated)

docs/                        # All technical documentation
  INSTALL.md                 # Step-by-step installation guide
  ARCHITECTURE.md            # This file — architecture diagrams + project layout
  BEAST_SLOT.md              # Client/server: the slot contract, beast-gate, enrollment, keyed mode
  MODELS.md                  # Full model lineup (17 models, measured)
  FEATURES.md                # Comprehensive feature breakdown
  REFERENCE.md               # VRAM tables, architecture, configuration
  RESULTS.md                 # Eval distribution + leaderboards + cross-host sweep results
  TODO.md                    # Roadmap and completed work

skills/                      # Curated expertise packages — loaded on-demand by the model (14 total)
  README.md                  # Skill schema + how to add new ones
  codebase-onboarding/       # Orient before editing — Tier 1
  spec-extraction/           # Extract precise spec from vague request — Tier 1
  git-discipline/            # Atomic commits + meaningful messages — Tier 1
  long-context-synthesis/    # Process huge inputs via chunked passes — Tier 1
  test-driven-development/   # Real TDD — red, green, refactor — Tier 2
  architecture-proposal/     # Design doc before code — Tier 2
  performance-optimization/  # Measure-driven perf work — Tier 2
  api-design/                # Signature + types + examples first — Tier 2
  code-review/               # Multi-pass code review
  security-audit/            # Threat-model-driven security review
  debugging-methodology/     # Hypothesis-driven root-cause analysis
  deep-counsel/              # Slow-mode reasoning for intractable problems
  eval-task-author/          # Authoring eval tasks (encodes the 6 pitfalls)
  eval-variant-porter/       # Adding multi-language variants to existing tasks

system-prompt.md             # Soul file (persona, applied to all frontends)
system-prompt-tools.md       # Tool guidance (Open WebUI only)
docker-compose.yml           # Open WebUI + SearXNG containers
opencode.json                # OpenCode project config (MCP wiring + model list)
weights/                     # GGUF model files (default location; relocatable — see MODELS.md) [gitignored]
openbeast.conf.example       # Config template — copy to openbeast.conf to customize
llama.cpp/                   # Inference engine, built with CUDA [gitignored]
```
