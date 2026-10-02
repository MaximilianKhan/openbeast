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
            ctools["⚙️ <b>18 tools</b> — bash · files · grep · spawn<br/>they act on THIS laptop's disk"]
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
- **The brain can move off the rig.** `INFERENCE_BACKEND=vllm|tensorfold`
  plus `INFERENCE_URL` point the whole command center (WebUI, router, gate,
  spawned agents, `/api/slot`) at an OpenAI-compatible server on other boxes —
  planned: two DGX Sparks, tensor-parallel. The rig then launches, supervises
  and kills no model server (`INFERENCE_MANAGED=false`); everything else in
  this picture is unchanged. Plan, security and runbook:
  [`DGX_SPARK_PLAN.md`](DGX_SPARK_PLAN.md).

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
        mcp["<b>MCP tool surface — 18 tools</b><br/><code>agents/mcp_server.py</code> · stdio, no port<br/>adds skill · start/start_skill/check/tail/list/stop_agent<br/>publish_artifact · list_artifacts · language_reference"]
        core["<b>Tool primitives</b> · <code>agents/tools.py</code><br/>bash · read/write/edit/list/grep<br/>fetch (SSRF-guarded) · web_search<br/><i>+ push-diagnostics on write/edit (opt-in)</i>"]
        its --> mcp --> core
    end

    subgraph INFPLANE["🧠 INFERENCE PLANE — the ONLY surface published to the tailnet"]
        gate["🛡️ <b>beast-gate</b> · :8090 <i>(opt-in)</i><br/><code>agents/edge.py</code><br/>per-device keys · route allowlist<br/>rate + in-flight caps · audit<br/><i>authenticates the DEVICE</i>"]
        llama["<b>llama.cpp server</b> · :8080<br/>OpenAI-compatible · continuous batching<br/>unified KV · MTP speculative decode<br/>context auto-scaled to VRAM · 24 GB floor"]
        hydra["🐉 <b>beast-hydra</b> · :8095 <i>(opt-in)</i><br/><code>agents/hydra.py</code><br/>routes on <code>model</code> · health · failover<br/>before the first byte · strict pins"]
        gate --> llama
        gate -.->|"HYDRA=true"| hydra
        hydra -.-> llama
    end
    instinct["🧿 <b>beast-instinct</b> · :8094 <i>(opt-in)</i><br/><code>agents/instinct/</code><br/>calibrated decisions · shadow until gated<br/><i>never a pool, never behind hydra or the gate</i>"]

    router["🧭 <b>Agent router</b> · :8088<br/><i>(opt-in; OFF by default —<br/>WebUI then calls llama.cpp direct)</i>"]
    searxng["🔎 <b>SearXNG</b> · :8888<br/>private metasearch"]
    dash["📊 <b>Dashboard</b> · :3002 <i>(extension)</i><br/>serves /api/slot"]
    artifact["🎨 <b>beast-artifact</b> · :3004 <i>(opt-in)</i><br/><code>agents/artifact_server.py</code><br/>versioned page store · gallery + viewer<br/>sandboxed opaque-origin render · CSP<br/>owner: the rig or a login · admins<br/><i>publish: loopback-only · manage: + artifact-scoped key</i>"]
    chat["📱 <b>beast-chat</b> · :3003 <i>(opt-in)</i><br/><code>agents/chat_server.py</code> + <code>sessions.py</code><br/>session ledger · SSE reattach by offset<br/>say / pause / stop · export · presets<br/>spawns agents + jobs (own scope under -d)<br/><i>reads: tailnet identity · writes: device key (chat scope)</i>"]
    jobs["🧾 <b>job.sh</b><br/>any long command, registered in the ledger"]
    ntfy["🔔 <b>ntfy</b> · :3005 <i>(extension)</i><br/>push server · tailnet :8447<br/><i>title + state + link only</i>"]

    subgraph LANG["📚 BEAST-LANG — <code>agents/lang/</code> · the installed toolchain is ground truth"]
        corpus["📖 <b>L0 corpus</b><br/><code>lang-library.sh acquire</code>"]
        intro["🔬 <b>L1 introspection</b><br/>what the compiler HAS"]
        verify["✅ <b>verifier</b><br/>old fails · new compiles"]
        escal["🎯 <b>escalation index</b><br/>diagnostic → confirmed card"]
        corpus --> verify
        intro --> verify
        verify --> escal
    end

    webui -->|"tool calls +<br/>identity headers"| its
    opencode -->|"MCP over stdio"| mcp
    agentsh -->|"imports the<br/>primitives directly"| core
    remote ==> gate

    webui -->|"chat completions"| router
    router -->|"no spawn intent →<br/>pass through"| llama
    router -.->|"HYDRA=true"| hydra
    hydra -.->|"instinct-route/1"| instinct
    router -.->|"ROUTER_INSTINCT"| instinct
    router -.->|"spawn intent →<br/>start_agent"| its

    core -->|"web_search"| searxng
    core -.->|"spawned agents<br/>think on the GPU"| llama
    dash -.->|"health · slots · queue"| llama
    mcp -.->|"publish_artifact"| artifact
    mcp -.->|"language_reference"| verify
    core -.->|"beast-assist error<br/>(opt-in escalation)"| escal
    chat -->|"--session-id --steer"| agentsh
    chat --> jobs
    chat -.->|"export"| artifact
    chat -.->|"session ended"| ntfy

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
    class gate,router,artifact,chat,hydra,instinct optin;
    class searxng,dash,jobs,ntfy aux;
    classDef lang fill:#fff7ed,stroke:#ea580c,stroke-width:2px,color:#431407;
    class corpus,intro,verify,escal lang;
    style LANG fill:#fffbeb,stroke:#ea580c,stroke-width:2px,color:#431407;
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
  than reimplementing it, so the HTTP and MCP surfaces expose the identical 18
  tools and cannot drift. `mcp_server.py` in turn delegates its eight file /
  shell / network primitives to `agents/tools.py` and adds `skill`, the six
  agent-orchestration tools, the two beast-artifact publishing tools and
  beast-lang's read-only `language_reference` on top. The **headless path** (`agent.sh` →
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
- **beast-hydra and beast-instinct change the inference path when on.**
  With `HYDRA=true`, every consumer (WebUI's `MODEL_URL` or the router's
  upstream, beast-gate, spawned agents) is re-pointed at hydra on
  `127.0.0.1:8095`, which routes each request on its `model` field across
  the rig's engine and any tailnet engines in `hydra.toml`. So with hydra on,
  WebUI → llama-server is really WebUI → (router) → hydra → engine.
  `INSTINCT=true` runs beast-instinct on `:8094` (and, with
  `INSTINCT_SCORER=true`, its scorer on `:8082`): hydra and the router may
  ask it for a decision, act only on a gated `enforce` answer, and work
  unchanged when it is down. Both are off by default, and with them off the
  stack is byte-identical to one without them.
  [`BEAST_HYDRA.md`](BEAST_HYDRA.md), [`BEAST_INSTINCT.md`](BEAST_INSTINCT.md).
- **Push-diagnostics (opt-in, experiment-gated).** With
  `OPENBEAST_DIAGNOSTICS=1`, every `write_file`/`edit_file` of a source file
  appends the language's real compiler/checker verdict to the tool result —
  pushed automatically, so it works identically for every model and frontend
  that reaches `tools.py`. Default OFF until its A/B gate passes; design,
  measured checker table, and security spec:
  [`LANG_AWARENESS_PLAN.md`](LANG_AWARENESS_PLAN.md).
- **beast-chat and beast-artifact are frontends with a THIRD identity rule.**
  Both are opt-in (`BEAST_CHAT`, `BEAST_ARTIFACT`), bind loopback, and are
  published separately (`:8445`, `:8446`). Reads need a tailnet identity that
  their operator list allows (an unlisted login gets 404, never 403). Anything
  that *changes* state needs proof of being on the rig (a 0600 locality token
  no browser can read) or an enrolled device key with the right scope: `chat`
  to steer, stop or start a session, `artifact` to manage (never publish) a
  page. Publishing a page is locality-only. Under `./start.sh -d`, sessions
  the console starts run in their own memory-capped systemd scope so
  `./stop.sh` never takes a phone-started job with it; the artifact viewer
  frames every page in an opaque-origin sandbox and serves its supporting
  files under a capability path, because an opaque origin is cross-site even
  to the server that hosts it. The two meet in one place: a console export
  (and anything a session publishes, via `OPENBEAST_SESSION_ID`) links a page
  back to its session. [`BEAST_CHAT.md`](BEAST_CHAT.md), [`BEAST_ARTIFACT.md`](BEAST_ARTIFACT.md).
- **ntfy is an extension, not a service of the stack.** `EXTENSIONS=ntfy`
  runs a digest-pinned ntfy server on `127.0.0.1:3005` (compose fragment,
  read-only root, `cap_drop: ALL`); `setup-tailscale.sh --publish-ntfy`
  mounts it tailnet-only at `:8447` for the phone app. beast-chat is its only
  publisher, and the topic URL — the credential under ntfy's default access
  — reaches the chat server's process and nothing else.
  [`extensions/ntfy/README.md`](../extensions/ntfy/README.md).
- **beast-lang is a library, not a service.** Nothing in `agents/lang/`
  listens on a port or runs at start. It is consulted from two places: the
  `language_reference` tool (MCP/WebUI surface only, never the runner's
  10-tool registry) answers from claims the installed toolchain verified; and,
  once its A/B lands, beast-assist's checker verdict carries the confirmed fix
  for an error whose shape the escalation index knows. Model-drafted claims
  (`lang-synthesize.sh`) reach nothing until the same verifier accepts them.
  Every entry point is a no-raise facade that is silent under `OPENBEAST_EVAL`.
  [`BEAST_LANG_PLAN.md`](BEAST_LANG_PLAN.md).
- **The GPU has one owner at a time.** `scripts/gpu-lease.sh` is an advisory
  lease (pid + start time) that campaigns take before measuring; the watchdog
  and `stop.sh` consult it before touching any llama-server, and
  `scripts/eval-era.sh` names the hash of the six files that define what an
  eval unit sees, so rows are only ever compared within one era.
  [`BEAST_CAMPAIGN_PLAN.md`](BEAST_CAMPAIGN_PLAN.md).
- **Nothing in the tool plane is ever published.** With RBAC Phase 2 keys
  (`scripts/setup-mcpo-keys.sh`), every `:3001` tool call must present a
  profile key — **admin** reaches all 18 tools, **guest** reaches `web_search`
  + `fetch` only (anything else 404s). `:3001`, `:8088`, `:8888` and `:8080`
  are loopback-only; remote devices arrive exclusively through Tailscale's
  authenticated HTTPS proxy (see [Remote access](REMOTE_ACCESS_PLAN.md)).
- **Inference.** llama.cpp serves an OpenAI-compatible API with MTP
  speculative decoding; `serve.sh` auto-scales context down to the card's VRAM
  (the shipped default, Qwen3.8 27B Uncensored MTP, serves its native 262K
  context on a **single** slot on the 32 GB reference card — its serve script
  pins `-np 1`, as MTP scripts do; a serve script that passes no `-np` gets
  `serve.sh`'s six unified-KV slots), and bootstrap refuses GPUs under the
  24 GB floor.

## Project structure

```
start.sh                     # Launch the full stack (llama.cpp + tool server + Open WebUI + SearXNG + opt-ins)
stop.sh                      # Stop everything (lease-aware: never a campaign's llama-server)
agent.sh                     # Run an autonomous agent
bootstrap.sh                 # git clone → working stack (--preflight, --minimal; OFFLINE-aware)

scripts/                     # Server, ops, and feature CLIs
  serve.sh / run.sh          # Generic launchers (pick model with -m)
  serve-<model>.sh           # Model-specific API servers (23 pre-configured)
  serve-bootstrap.sh         # Tiny 0.6B bridge for fast-boot (FAST_BOOT)
  configure-webui.sh         # Auto-configure Open WebUI (tools + system prompt)
  healthcheck.sh             # Watchdog (--restart): knows a LOADING model from a dead one
  doctor.sh                  # Config/security/supply-chain/health diagnosis (./start.sh doctor)
  update.sh                  # Update llama.cpp, images, Python deps (relocks the lockfile)
  fetch-weight.sh            # Download one registry weight, staged + sha256-verified
  verify-weights.sh          # Verify downloaded weights against weights.registry
  weights.registry           # sha256 + size pins for every shipped GGUF
  setup-tailscale.sh         # Publish to the tailnet (--publish-{searxng,slot,chat,artifact,ntfy})
  clients.sh                 # RIG: device enrollment/revocation (beast-gate; chat + artifact scopes)
  setup-client.sh            # CLIENT: install client mode (macOS/Linux)
  client.sh                  # CLIENT: the openbeast-client CLI
  artifact.sh                # beast-artifact CLI: publish/list/show/versions/rollback/visibility/
                             #   pin/tag/chown/prune/remove
  publish-verdict.sh         # A campaign verdict or leaderboard → one stable artifact URL
  job.sh                     # beast-chat: run/list/show/stop a tracked long job
  lang-library.sh            # beast-lang: acquire/check/list/verify/pack/where
  lang-introspect.sh         # beast-lang: probe the installed toolchains
  lang-synthesize.sh         # beast-lang: model-drafted claims → verifier → staging → promote
  bundle.sh                  # Air-gap: build/sign/verify/install the offline bundle
  pydeps.sh                  # Hash-pinned Python lockfile: lock/verify/wheelhouse/install
  gpu-lease.sh               # Advisory GPU lease: status/acquire/release/run
  eval-era.sh                # The eval era hash and the six files behind it
  land-dependabot.sh         # Rebase → relock → approve → merge, one Dependabot PR at a time
  skill-import.sh            # Remote-skill gate: fetch a pinned commit, scan, promote with a reviewer, verify
  uninstall.sh               # Rig decommissioning (dry run by default; --go; --purge-*)
  logrotate.sh               # Log rotation for .run/ (policy: logrotate-openbeast.conf; --install = daily user timer)
  ext.sh                     # Extension manager (enable/disable/list optional services)
  ssd-wear.sh                # SMART-based drive wear report
  lib/                       # conf.sh (config), hardware.sh, weights.sh, extensions.sh,
                             #   proc.sh (identity-checked signalling), portown.sh (does OUR pid hold the port),
                             #   bundle_manifest.py, pydeps_lock.py, skill_import.py,
                             #   backend.sh (per-backend readiness: llama / vLLM / TensorFold)
  backends/                  # DGX Spark inference (docs/DGX_SPARK_PLAN.md): {vllm,tensorfold}/spark-node.sh
                             #   rank launchers (--profile), spark.env.example (host settings),
                             #   models/<name>.env per-model profiles + TEMPLATE.env, model-inspect.sh,
                             #   model-fetch.sh (pinned + verified + locked), conformance.sh and
                             #   use-model.sh (rig), pylib/ (their python), data/ (vendored engine lists)

agents/                      # Agent framework + servers
  mcp_server.py              # MCP tool surface (18 tools; stdio for OpenCode)
  openapi_tools.py           # Identity tool server on :3001 (RBAC keys, per-user shards, audit)
  tools.py                   # Tool primitives + beast-assist push-diagnostics (+ escalation, opt-in)
  runner.py                  # Autonomous agent loop (10-tool registry; steering inbox as a session)
  router.py                  # Agent-spawn router on :8088 (opt-in)
  edge.py                    # beast-gate on :8090 — identity-aware inference edge (opt-in)
  hydra.py / hydra_core.py   # beast-hydra on :8095 — routes inference across engines (opt-in)
  instinct/                  # beast-instinct on :8094 — calibrated routing decisions (opt-in)
  chat_server.py             # beast-chat on :3003 — sessions API + the phone console (opt-in)
  sessions.py                # The session ledger (records, inbox, liveness by pid+start time)
  artifact.py                # beast-artifact store (versions, ownership, visibility)
  artifact_server.py         # beast-artifact on :3004 — gallery, viewer, sandboxed raw pages (opt-in)
  hostpolicy.py              # The Host-header allowlist both published servers share
  chat_ui/ · artifact_ui/    # The console and the gallery/viewer (self-contained HTML)
  lang/                      # beast-lang: drivers, introspect, verify, packs, escalate, reference, synthesize
  requirements.txt / .lock   # Pinned deps and the hash-pinned closure CI installs
  logs/                      # Agent run logs (JSONL) [gitignored]

extensions/                  # Optional hot-pluggable services (see extensions/README.md)
  dashboard/                 # Status dashboard (GPU/model/services) on :3002
  ntfy/                      # Self-hosted push server for beast-chat notifications on :3005

searxng/settings.yml         # Custom config: enables JSON format + disables limiter

tests/                       # pytest + standalone shell suites (tests/run_tests.sh runs all)
  test_scripts.sh            # Script behaviour under set -e/pipefail, incl. end-to-end healthcheck
  test_offline_fixes.sh      # bundle/pydeps/fetch-weight with stubbed hf/pip/docker/git
  test_skill_import.sh       # the remote-skill gate against a stub scanner and a stub git
  test_job_sh.sh · test_artifact_cli.sh · test_clients.sh · test_ssd_wear.sh
  test_chat_*.py · test_sessions.py · test_steering.py · test_artifact_*.py
  test_e2e_chat_artifact.py  # both, end to end, in a phone-sized headless Chromium (skips without one)
  test_ops_chat_artifact.sh  # start/healthcheck/doctor/logrotate/conf.sh behaviour for both
  test_lang_*.py             # beast-lang: drivers, verifier, escalation, hardening, tool, synthesis
  test_edge.py · test_identity_*.py · test_tools.py · test_diagnostics.py · test_cache.py …

evals/                       # Eval harness — 137 tasks / 291 units + multi-model benchmark
  README.md                  # Distribution table, schema, scoring (start here)
  run_eval.py                # Single-model runner (--jobs, --suite, --packs; greedy via OPENBEAST_EVAL_GREEDY)
  benchmark_all.py           # Multi-model sweep orchestration (server start/stop + recovery)
  scoring.py                 # v2 capability metric + per-category & per-language breakdown
  cache.py                   # Durable result cache, keyed on the era hash of the harness code
  suites/ · tasks/ · results/ · leaderboard.json

docs/                        # 34 documents — see README.md § Documentation
skills/                      # 15 curated expertise packages (skills/README.md)
system-prompt.md             # Soul file (persona, applied to all frontends)    [era-hashed]
system-prompt-tools.md       # Tool guidance (Open WebUI only)                    [era-hashed]
opencode.json                # OpenCode project config (MCP wiring + model list) [era-hashed]
docker-compose.yml           # Open WebUI + SearXNG containers (digest-pinned)
openbeast.conf.example       # Config template — copy to openbeast.conf to customize
weights/                     # GGUF model files (relocatable — see MODELS.md) [gitignored]
llama.cpp/                   # Inference engine, built with CUDA [gitignored]
```
