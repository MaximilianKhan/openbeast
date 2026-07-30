# Architecture & project layout

## 1. Topology — where the work happens

OpenBeast runs as one **command center** (the rig) and, optionally, any number
of **clients**. The load-bearing fact: **tools execute where the process runs,
not where the model runs.** A laptop runs the complete tool arsenal against its
*own* disk while every token is generated on the rig's GPU. The boundary
between the two machines is a single OpenAI-compatible HTTP call.

```mermaid
flowchart LR
    subgraph CLIENT["💻 CLIENT — any Mac / Linux device (optional)"]
        direction TB
        coc["⌨️ OpenCode"]
        cmcp["🔌 MCP server (stdio, no port)<br/><b>agents/mcp_server.py</b>"]
        ctools["⚙️ Tool arsenal — 15 tools<br/>bash · files · grep · agents<br/><b>acts on THIS machine's disk</b>"]
        ccli["🧰 openbeast-client<br/>status · agent · search · update"]
        coc --> cmcp --> ctools
    end

    subgraph TAILNET["🔒 Tailscale — WireGuard + auto-HTTPS, tailnet-only, never funneled"]
        p8443(["🛡️ :8443 — inference"])
        p8444(["📊 :8444/api/slot — discovery"])
        p8889(["🔎 :8889 — SearXNG (opt-in)"])
        p443(["🌐 :443 — Open WebUI"])
    end

    subgraph RIG["🖥️ COMMAND CENTER — the rig (full stack, always)"]
        direction TB
        gate["🛡️ <b>beast-gate</b> · :8090 <i>(opt-in EDGE_GATE)</i><br/>per-device keys · path allowlist<br/>rate + in-flight caps · inference audit"]
        llama2["🧠 llama.cpp · :8080"]
        rest["🌐 WebUI :3000 · 🔑 tools :3001<br/>🔎 SearXNG :8888 · 📊 dashboard :3002"]
        gate --> llama2
    end

    ctools -->|"inference only"| p8443
    ctools -->|"web_search"| p8889
    ccli --> p8444
    phone["📱 phone · browser"] --> p443

    p8443 -->|"gate ON"| gate
    p8443 -.->|"gate OFF — raw, whole route table"| llama2
    p8444 --> rest
    p8889 --> rest
    p443 --> rest

    classDef cli fill:#e0f2fe,stroke:#0284c7,color:#0c2733;
    classDef net fill:#fef3c7,stroke:#d97706,color:#3a2503;
    classDef rig fill:#dcfce7,stroke:#16a34a,color:#0b2417;
    classDef sec fill:#fee2e2,stroke:#dc2626,color:#3b0a0a;
    class coc,cmcp,ctools,ccli,phone cli;
    class p8443,p8444,p8889,p443 net;
    class llama2,rest rig;
    class gate sec;
```

**Reading it.** File contents the client's agent *reads* travel to the rig as
model context — the model must see data to reason about it. Both machines are
yours and the transport is WireGuard, so the promise is **"nothing leaves your
tailnet"**, not "nothing leaves this machine".

The dashed arrow is the honest default: with `EDGE_GATE=false`, `:8443` maps
straight at llama-server and publishes its *entire* route table (`/slots`,
`/props`, `POST /lora-adapters`, `/v1/stream/<id>`) to every tailnet peer.
That is fine on a tailnet you fully own. `EDGE_GATE=true` routes it through
beast-gate instead, which allowlists the OpenAI routes and gives each device
its own revocable key. Full detail: [`BEAST_SLOT.md`](BEAST_SLOT.md).

## 2. Inside the command center

```mermaid
flowchart TB
    dev["📱 Your devices — phone · laptop · desktop"]
    dev -->|"Tailscale · WireGuard + auto-HTTPS<br/>authenticated, tailnet-only"| edge
    edge{{"Ingress — loopback 127.0.0.1 by default<br/>remote only via Tailscale Serve"}}
    gate["🛡️ beast-gate · :8090 <i>(opt-in EDGE_GATE)</i><br/><b>agents/edge.py</b> — the one place on the<br/>INFERENCE path where identity exists<br/>per-device keys (hot-revocable) · path allowlist<br/>id_slot stripped · per-device rate + in-flight caps<br/>inference audit + /gate/metrics"]
    edge -->|"remote devices, gate ON"| gate
    gate --> llama

    subgraph FE["Frontends"]
        direction LR
        webui["🌐 Open WebUI · :3000<br/>browser chat · accounts · RBAC roles"]
        opencode["⌨️ OpenCode<br/>terminal coding agent"]
        agentsh["🔁 agent.sh<br/>headless autonomous agent"]
    end
    edge --> webui

    subgraph TOOLS["Tool layer — ONE arsenal, TWO surfaces (imported → can't drift)"]
        direction TB
        its["🔑 Identity Tool Server · :3001<br/><b>agents/openapi_tools.py</b><br/>RBAC profile keys · per-user file shards<br/>audit trail + Prometheus /metrics<br/>signed-JWT or header identity"]
        mcp["🔌 MCP Server (stdio)<br/><b>agents/mcp_server.py</b>"]
        arsenal["⚙️ <b>Tool Arsenal — agents/tools.py</b> · 15 tools<br/>bash · read/write/edit_file · grep · list_files<br/>fetch (SSRF-guarded) · web_search<br/>start / check / tail / list / stop_agent · skill · skill_agent<br/>workspace → ~/openbeast-files/users/USER/"]
        its --> arsenal
        mcp --> arsenal
    end

    webui -->|"tool calls + identity headers<br/>X-OpenWebUI-User / Chat / JWT"| its
    opencode -->|"MCP over stdio"| mcp
    agentsh --> arsenal

    router["🧭 Agent Router · :8088 <i>(opt-in)</i><br/>grammar-constrained spawn-intent<br/>+ admin-only identity gate"]
    webui -->|"chat completions"| router
    router -->|"no spawn → pass through"| llama
    router -.->|"spawn intent → start_agent"| its

    arsenal -->|"web_search"| searxng
    arsenal -.->|"spawned agents' inference"| llama

    searxng["🔎 SearXNG · :8888<br/>private metasearch (no tracking)"]
    llama["🧠 llama.cpp Server · :8080<br/>OpenAI-compatible API<br/>6 parallel slots · unified KV cache<br/>continuous batching · MTP spec-decode"]
    gpu["🎮 GPU — RTX 5090 · 32 GB (reference)<br/>context auto-scaled to card VRAM · 11 GB floor"]
    llama --> gpu

    classDef fe fill:#e0f2fe,stroke:#0284c7,color:#0c2733;
    classDef tool fill:#ede9fe,stroke:#7c3aed,color:#2a1150;
    classDef inf fill:#dcfce7,stroke:#16a34a,color:#0b2417;
    classDef sec fill:#fef3c7,stroke:#d97706,color:#3a2503;
    class webui,opencode,agentsh fe;
    class its,mcp,arsenal tool;
    class llama,gpu,searxng inf;
    class edge,router,gate sec;
```

**How to read it.** Two frontends, one tool arsenal, two ways into it. The
**browser path** (Open WebUI) calls the **identity tool server**
(`agents/openapi_tools.py`, which replaced the generic MCPO proxy in v1.1):
it reads the identity headers Open WebUI forwards on every tool call —
plain `X-OpenWebUI-User/Chat` or, in enterprise mode, a signed JWT — then
enforces the per-profile RBAC keys, shards each user's files into their own
`users/<id>/` workspace, and writes an audit trail. The **terminal path**
(OpenCode) speaks MCP over stdio to `agents/mcp_server.py`. Both import the
**same 15 tool functions** from `agents/tools.py`, so the two surfaces
cannot drift.

- **Solid arrows** are the request path; **dashed arrows** are opt-in or
  conditional (the agent router only runs when `AGENT_ROUTER=true`; spawned
  background agents route their *inference* to llama-server while still
  executing files/shell locally).
- **Security boundaries (amber/red):** everything binds `127.0.0.1`; remote
  devices arrive only through Tailscale's authenticated HTTPS proxy (see
  [Remote access](REMOTE_ACCESS_PLAN.md)). With RBAC Phase 2 keys
  (`scripts/setup-mcpo-keys.sh`), every :3001 tool call must present a
  profile key — **admin** reaches all 15 tools, **guest** reaches
  `web_search` + `fetch` only (anything else 404s). The `:3001` tool server
  and `:8088` router are **never** published to the tailnet.
- **Two identity layers, deliberately separate.** `:3001` authenticates the
  *human* (WebUI user → RBAC tier → file shard). beast-gate on `:8090`
  authenticates the *device* (enrolled key → rate limits → inference audit).
  Before the gate, the inference path had no identity at all: every WebUI
  user, laptop, and spawned agent collapsed into one anonymous caller,
  because llama-server itself has no concept of a user.
- **Inference (green):** llama.cpp serves an OpenAI-compatible API with
  MTP speculative decoding; `serve.sh` auto-scales context to the card's
  VRAM, and bootstrap refuses GPUs under the 11 GB floor.

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
  tools.py                   # Tool schemas/handlers for the standalone runner
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
