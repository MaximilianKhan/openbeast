# Agent Instructions for this Repository

You're working in **OpenBeast**, a fully local AI workstation — llama.cpp
serving Qwen and Gemma models, MCP-based tool server, OpenCode + Open WebUI as frontends,
a 137-task eval suite (v4 — 291 effective units with multi-language variants,
across 12 categories), and 14 curated skills for specialized work.

This file is auto-loaded as project-wide instructions. Read it once at the
start of a session.

## Architecture in 60 seconds

OpenBeast is **not** a single-box stack any more. Full picture:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (topology + project layout) and
[`docs/BEAST_SLOT.md`](docs/BEAST_SLOT.md) (the client/server contract). The
four facts that change how you edit code here:

- **Rig + clients.** The rig ("command center") runs everything: llama-server
  `:8080`, the identity tool server `:3001`, Open WebUI `:3000`, SearXNG
  `:8888`, optional extensions. Any Mac/Linux box can additionally install
  *client mode* (`scripts/setup-client.sh` → `~/.openbeast-client`), which runs
  the **same tool arsenal locally** and sends only chat/completion calls to the
  rig over Tailscale. **Tools execute where the process runs, not where the
  model runs** — so `agents/tools.py` and `agents/mcp_server.py` ship to
  clients, and a change there is a client-facing change.
- **beast-slot** (`/api/slot`, served by the dashboard extension) is the
  versioned discovery contract clients read: model, slots, capacity, services.
  Pinned by `tests/test_beast_slot.py`.
- **beast-gate** (`agents/edge.py`, opt-in `EDGE_GATE=true`) is the
  identity-aware **inference** edge — per-device bearer keys, path allowlist,
  rate + in-flight caps, inference audit. It is the one place on the inference
  path where identity exists (llama-server has no users). Devices are enrolled
  with `scripts/clients.sh`.
- **Two identity layers, deliberately separate.** `:3001` authenticates the
  *human* (WebUI user → RBAC tier → file shard); beast-gate authenticates the
  *device*. Don't conflate them.

**Unified KV, one shared pool.** `serve.sh` passes `--kv-unified`
unconditionally: with `-np N`, every slot advertises the full `-c` context
while all slots draw on ONE pool. Never divide context by slot count — that
arithmetic is wrong, and `/api/slot`'s `capacity.ctx_*` exists because of it.

## Use skills first

You have access to **14 curated skills** via two MCP tools:

- `skill()` — see all available skills (one line each; rescans disk every call)
- `skill(name)` — read the full skill body inline
- `start_skill_agent(skill, task, ...)` — spawn a sub-agent with the skill activated

**Before approaching any non-trivial task, call `skill()`** and check
whether one matches. Skills encode hard-won lessons — don't reinvent them.

## Task → skill mapping

| Situation | Skill |
|---|---|
| Working in this repo (or any unfamiliar codebase) for the first time this session | `codebase-onboarding` |
| User request is vague / open-ended | `spec-extraction` |
| About to commit, push, or open a PR | `git-discipline` |
| Input is huge (10K+ lines, 30+ files, big PR, paper, transcript) | `long-context-synthesis` |
| Adding a feature with tests, in TDD style | `test-driven-development` |
| Non-trivial change: new subsystem, public API change, refactor crossing modules | `architecture-proposal` |
| User wants something faster / spotted a perf issue / hot path code | `performance-optimization` |
| Designing a new function signature / endpoint / library API | `api-design` |
| User asks for "review" / "audit" / "look this over" | `code-review` |
| Security review / vulnerability check / threat model | `security-audit` |
| Hard debugging — obvious fix didn't work | `debugging-methodology` |
| Intractable problem, exhausted obvious paths, "consult the council" | `deep-counsel` |
| User wants to add a new eval task to the suite | `eval-task-author` |
| User wants multi-language variants (Py/Go/C/C++/Rust/Zig) on an existing task | `eval-variant-porter` |

When multiple skills could apply, pick the one most-specific to the task.
You can also chain — e.g., `codebase-onboarding` → `spec-extraction` →
`architecture-proposal` → implementation, for a meaty change in a new repo.

For long-running specialized work (eval task authoring, deep counsel,
multi-pass review of a large PR), prefer `start_skill_agent` to delegate;
your main conversation stays responsive while the sub-agent grinds.

## Project conventions

### Default model

`Qwen3.8 27B Uncensored MTP Q5_K_M` (JonathanColetti abliterated Qwen3.8,
`serve-qwen38-27b-uncensored-mtp-q5.sh`), launched by `./start.sh` with no
args — picked for uncensored behavior plus speed/headroom (140 tok/s, 262K
native, 4.76 GB free). Benchmarked 2026-08-21: capability 98.4 (#3 on v4),
statistically identical to stock Qwen3.8 — abliteration cost ≈ zero.

Ranking is by the **v4 SCORE** metric (`SCORE = 0.75·problem-solving +
0.25·language-breadth`), not raw accuracy. The dense `Qwen3.6-27B Q5_K_XL`
leads at **98.7%**; the 35B-A3B MoEs are much faster at a small SCORE cost.
Each is one arg away, e.g. `./start.sh serve-qwen-27b-q5.sh`. Board:
`docs/RESULTS.md`.

### Where things live

| What | Where |
|---|---|
| Source: model serving, agents, MCP tools | `agents/`, `scripts/`, `start.sh`, `stop.sh` |
| Eval suite (137 tasks across 12 categories) | `evals/` — see `evals/README.md` |
| Skills (this system) | `skills/` — see `skills/README.md` |
| Tests | `tests/run_tests.sh` (runs all) + `python3 -m pytest tests/ -q`. Full list is `ls tests/`; the load-bearing ones: `test_tools.py`, `test_scripts.sh`, `test_smoke.sh` (needs a live stack), `test_cache.py`, `test_proc_hygiene.py`, `test_edge.py` (beast-gate), `test_clients.sh` (enrollment CLI), `test_beast_slot.py` (`/api/slot` contract), `test_identity_server.py` (RBAC/sharding/audit), `test_fetch_guards.py` (SSRF) |
| Documentation (technical) | **Live** (start here): `docs/ARCHITECTURE.md`, `docs/BEAST_SLOT.md`, `docs/INSTALL.md`, `docs/REFERENCE.md`, `docs/TOOLS.md`, `docs/RESULTS.md`, `docs/MODELS.md`, `docs/UPDATING.md`, `docs/TODO.md` — `ls docs/` for the rest (plans, SANDBOXING, RBAC_PLAN, HARDWARE_PROFILES…). **Do not cite as current:** anything under `docs/archive/` (historical, v3-era), and `docs/MAC_CLIENT_PLAN.md`, superseded by `docs/BEAST_SLOT.md` |
| Documentation (overview / persona / tools) | `README.md`, `system-prompt.md`, `system-prompt-tools.md` |

### Eval suite

- 137 base tasks (v4); 31 of them have multi-language variants (291
  effective test units across Python / Go / C / C++ / Rust / Zig)
- Run a single model: `python3 evals/benchmark_all.py --models <slug>`
- Full sweep (11 configured models — `--list` to see them):
  `python3 evals/benchmark_all.py` (budget well over a day on the 5090)
- Score: `python3 evals/scoring.py --show` (ranked by SCORE = capability)
- Distribution + methodology: `evals/README.md`

### Skills

- Repo skills: `skills/<name>/SKILL.md` (this directory)
- Global skills: `~/.local/share/local-llm-skills/<name>/SKILL.md`
- Repo wins on name collision
- After editing a skill: just call `skill()` — the index rescans disk on
  every call (no need to restart MCP)

### Commit and PR style

The `git-discipline` skill encodes the rules. Quick version:
- Atomic commits (one logical change per commit)
- Imperative subject ≤72 chars
- Body explains WHY, not WHAT
- Never `git add .` blindly; review staged diff first
- Never amend / force-push commits already pushed to shared branches

## When NOT to use skills

For trivial tasks — typo fixes, one-line changes, simple questions — skills
are overhead. Use them when the task involves design choices, multi-step
reasoning, or domain-specific discipline. The bar: would loading the skill
prevent a real failure mode? If yes, load it. If no, just do the task.

## Tool surface (15 MCP tools)

Beyond skills (`skill`, `start_skill_agent`), you have:
- File / code: `read_file`, `write_file`, `edit_file`, `list_files`, `grep`
- Shell: `bash`
- Web: `fetch`, `web_search` (via local SearXNG)
- Long-running agents: `start_agent`, `check_agent`, `tail_agent`,
  `list_agents`, `stop_agent`

Prefer `edit_file` over `write_file` for existing files. Prefer running code
over reasoning about what it should do.

## Don't reinvent

If you find yourself thinking "I should write some code to do X" — first
check if there's a skill for X, an existing helper in `agents/tools.py`, or
a script in `scripts/`. The repo has accumulated tooling; use it.
