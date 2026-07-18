> **⚠️ ARCHIVED — historical record.** Describes work that has shipped or been
> superseded; kept for provenance, not current docs. Live state:
> [archive index](README.md) · [`../TODO.md`](../TODO.md) · [`../REFERENCE.md`](../REFERENCE.md).

# Tool Arsenal Research — adopt vs build (2026-07-07)

Deep-research run: 5 search angles, 20 sources fetched, 100 claims extracted,
25 adversarially verified (3-vote panels, 0 refuted), synthesized to 9
findings. Question: how to expand OpenBeast's 17-tool agent arsenal —
open-source adoption vs building on our own MCP tool server. Hard
constraints: local/self-hosted only, Python-friendly, OpenAI-compatible
function calling, and Qwen3.6-class tool-selection weakness (tool count and
description budget are binding).

## Verdict table

| Area | Verdict | What | License | Effort |
|---|---|---|---|---|
| Semantic code search | **ADOPT** | ChunkHound | MIT | 1–2 days |
| Sandboxed execution | **ADOPT** | Sandlock (+ keep rlimits) | Apache-2.0 | 2–4 days |
| Sandbox, heavy tier | Defer | Firecracker microVMs | Apache-2.0 | 1–2 weeks |
| Browser automation | **ADOPT pattern** | Playwright CLI as a *skill*, NOT the MCP server | Apache-2.0 | ~1 day |
| Agent memory | **BUILD** | Small memory tool over sqlite-vec/LanceDB + llama.cpp embeddings | ours | 2–4 days |
| SQL / structured data | **BUILD (adopt the pattern)** | Canned-query-as-tool à la mcp-sqlite | ours | ~1 day |
| PDF/office parsing | No verified rec | Docling/marker/unstructured = next research targets | — | — |
| Multi-agent orchestration | **BUILD** | Extend our start/check/tail/stop with slot-aware queuing | ours | — |
| MCP registries | Manual gate stands | No registry offers review/signing guarantees meeting our bar | — | — |

## Findings (all 3-vote verified; votes shown)

### 1. ChunkHound — semantic code search (HIGH, 3-0/2-0/3-0/3-0)
Local-first by design (air-gapped positioning), research-backed cAST
chunking (arXiv 2506.15655 — verified in code, not just README) over
tree-sitter for 32+ languages, ships as an stdio MCP server
(`uv tool install chunkhound`, `.chunkhound.json`). Embedding provider
takes any OpenAI-compatible `base_url` → our llama.cpp embedding endpoint
works via the documented Ollama/vLLM mechanism; Qwen3 embedding tuning docs
exist in-repo. Building this ourselves = reimplementing cAST + 30 grammars.
Not worth it. **Caveat:** default embedder is VoyageAI (cloud) — local
operation is explicit config, not the default path.
Fulfills the "RAG pipeline" item on the TODO Future Horizon.
- https://github.com/chunkhound/chunkhound · https://chunkhound.ai/docs/configuration · https://arxiv.org/abs/2506.15655

### 2. mcp-vector-search — design reference only (MEDIUM, 3-0/3-0/2-1)
Fully local (sentence-transformers default, no cloud), 13 languages via
tree-sitter. Two useful signals: it migrated its default vector store
**ChromaDB → LanceDB** in v2.1+ (real-world datapoint for our vector-DB
choice), and it's **Elastic License 2.0** — source-available, not OSI —
which disqualifies it for adoption under our open-source requirement.
- https://github.com/bobmatnyc/mcp-vector-search

### 3. Sandlock — sandboxed execution (HIGH, 3-0/3-0/3-0)
Unprivileged Linux confinement (Landlock + seccomp-bpf + seccomp user
notification): no root, no images, ~5 ms startup vs ~300 ms `docker run`,
near-zero steady-state overhead (Redis 75.2k vs 75.5k rps bare metal).
Purpose-built for confining AI-agent-written code — the exact failure class
from our 2026-07-07 OOM (orphaned model code). Needs Linux 6.12+ for
Landlock ABI v6 (Arch: satisfied). **Composes with, does not replace, our
rlimit/killpg layer** — Sandlock does filesystem/network/IPC/syscall policy,
deliberately not memory/CPU caps. **Caveats:** all benchmarks are
first-party (Multikernel Technologies, single low-end machine, no
independent replication); project announced March 2026 — young. Security
review before integration per our gate.
- https://arxiv.org/html/2605.26298v1 · https://github.com/multikernel/sandlock

### 4. Firecracker — heavy-isolation tier (HIGH, 3-0)
≤125 ms boot, ≤5 MiB VMM overhead (CI-tested spec; production: Lambda,
Fargate, Fly.io, E2B). The right tier for running *fetched/untrusted* code,
but integration is heavy (rootfs build, vsock plumbing, snapshots — 1–2
weeks). Phase 2 at the earliest; Sandlock covers the common case.
- https://github.com/firecracker-microvm/firecracker/blob/main/SPECIFICATION.md

### 5–6. Browser automation — Playwright CLI as a skill, NOT the MCP server (HIGH, 6× 3-0)
Playwright MCP works text-only (accessibility tree, no vision needed) but
exposes **68 tools**, and Microsoft's own docs concede it loads "large tool
schemas and verbose accessibility trees into the model context" —
recommending CLI+skills for coding agents. For Qwen-class models, adopting
the MCP server is the wrong call. Instead wrap **@playwright/cli**
(playwright.dev/agent-cli) as a skill invoked through our existing bash
tool: token cost benchmarked ~27k (CLI) vs ~114k (MCP) per typical task;
accessibility snapshots go to disk as YAML with stable element refs
(`.playwright-cli/page-*.yml`) and the agent reads only what it needs.
**Zero new tools in the schema** — fits our skills system exactly.
- https://playwright.dev/agent-cli/introduction · https://github.com/microsoft/playwright-mcp

### 7–8. Agent memory — build beats adopt (HIGH, 3-0/3-0/3-0 + 3-0/2-1/3-0)
Mem0 (Apache-2.0) self-hosts via Docker but defaults to OpenAI cloud
(gpt-5-mini + text-embedding-3-small); local operation = reconfigure LLM
*and* embedder, and its LLM-driven memory extraction is **unproven on 27B
local models**. Letta (Apache-2.0) self-hosts with documented
OpenAI-compatible backends, but explicitly recommends frontier models and
warns its harness is demanding for open-weights models; the letta-ai/letta
repo is now legacy (dev moved to the App Server stack — evaluate that if
revisiting). Given we already run a tool server + skills system, **a small
memory MCP tool over sqlite-vec/LanceDB with llama.cpp embeddings is the
pragmatic call** — no cloud-default deps, no frontier-model assumptions,
sized to what Qwen can actually drive.
- https://github.com/mem0ai/mem0 · https://docs.letta.com/guides/selfhosting

### 9. SQL — adopt the canned-query pattern, build the implementation (HIGH, 3-0/3-0)
panasenco/mcp-sqlite (Apache-2.0): each predefined query in a
Datasette-compatible metadata file becomes its own narrow, well-described
MCP tool — replacing open-ended SQL generation entirely. **Tailor-made for
weak-tool-selection models.** But the project is tiny (~23 stars, last
commit ~5.5 months) — reimplementing the pattern in our tool server is
~1 day and skips a supply-chain review of a low-activity dependency.
- https://github.com/panasenco/mcp-sqlite

## Coverage gaps (honest accounting)

- **Areas 6 (orchestration) and 7 (MCP registries): zero claims survived
  verification.** Orchestration under our 6-slot/1-MTP-slot constraint stays
  build-your-own (extend the existing subagent manager with slot-aware
  queuing). Registry vetting stays manual per skills/REMOTE_PROVENANCE
  discipline — no registry's review/signing guarantees met the bar.
- **Area 5 covered for SQL only** — PDF/office/spreadsheet parsing produced
  no verified recommendation. Next research targets: Docling, marker,
  unstructured (tool-vs-skill question matters — document dumps are token-expensive).
- Mem0/ChunkHound/Letta all *support* fully-local but *default* to cloud —
  expect rougher edges on the local path.
- This space moves monthly (Letta mid-migration, Playwright CLI < 1 year
  old, Sandlock 4 months old) — re-verify versions before integrating.

## Prototype recon — Sandlock + ChunkHound both GO (2026-07-07)

Two isolated recon agents built working prototypes on this box (scratch
dirs only, nothing installed into the live stack, nothing committed). Both
returned **GO**. Details below feed Phase 1 implementation.

### Sandlock — GO (built + adversarially tested, commit `5c9bafe9`, v0.8.4)
- **Builds clean** on Arch in ~21s (rustc 1.95). Kernel `7.0.10-arch1-1`
  exposes **Landlock ABI 8** — above every Sandlock requirement (needs v6).
- **Source audit clean**: no telemetry/phone-home/sudo/setuid. Two things
  to keep *off*: the `--http-inject-ca` MITM feature and the `bollard`
  Docker/OCI path — build only what we use.
- **8/8 isolation tests held**: blocked `/etc/passwd` + credential reads
  outside the allowlist, capped a fork bomb, SIGKILL'd a memory bomb,
  default-denied network, allowed a scoped `--net-allow`. Kernel-enforced
  FS allowlist is the strong guarantee.
- **Composition with our `run_reaped`**: Landlock/seccomp = *what* code may
  touch; RLIMIT_AS + killpg = *how much / how long*. Disjoint axes, they
  stack. Keep sandlock's `-m` just under the hard rlimit so the friendly
  SIGKILL fires first; killpg still reaps the supervisor+child tree.
- **THE GOTCHA**: `bash()` shells out → easy to wrap (argv via
  `sandlock run … -- /bin/bash -c`). But `read_file`/`write_file`/
  `edit_file` run **in-process** (`open()`), which Sandlock's subprocess
  model can't wrap. **Fix independent of Sandlock and worth doing day one:
  app-level path allowlist on the file tools** (realpath must be under the
  work dir / `/tmp/eval_*`, never `~/.ssh`, `~/.config`, `.git/config`).
  Sandboxing bash alone while file tools can still read `~/.ssh` is theater.
- Maps directly onto RBAC guest/admin via TOML profiles (`guest.toml`:
  `write=[]`, no network; `admin.toml`: scoped writes + egress) — the RBAC
  and Arsenal workstreams converge in the sandbox policy.
- Effort for solid v1 (bash sandboxed + file-tool path gate + 2 profiles):
  **~1 week**. Pin the commit, vendor source, re-audit on bump (pre-1.0).

### ChunkHound — GO (working local index proven, v3.3.1)
- **Installed clean** (venv, Python 3.14, all wheels — no compiler).
  Indexed the OpenBeast repo: **34 files → 264 chunks in 10.3s**, CPU
  embeddings, **zero VRAM**. Both target semantic queries returned exact
  ground-truth code as the #1 hit.
- **Lean MCP surface** — only 4 tools (`search_semantic`, `search_regex`,
  `get_stats`, `health_check`), good for weak tool-selectors. **BUT
  `max_response_tokens` defaults to 20000** — must pin `page_size: 3-5` +
  `max_response_tokens: 1500-3000` in tool guidance or it floods context.
- **Cloud-leak guardrail**: code default provider is `openai` at
  `api.openai.com` — if `base_url` is unset and an `OPENAI_API_KEY` is in
  the env, indexing silently ships code to OpenAI. **Always set `base_url`.**
- **Local embedding path (the single-5090 answer)**: chat model saturates
  VRAM (30/32.6 GB), so **run a CPU llama.cpp embedding sidecar** on port
  8081 reusing our own `llama-server` binary (`--embeddings -ngl 0`), zero
  VRAM. **Embedding model must be ≥2048-ctx** — bge-small (512-ctx) fails
  whole batches on cAST's larger chunks; **nomic-embed-text-v1.5** works.
- **Pitfalls found**: `--include "agents/**/*.py"` silently matched nothing
  (use simple root-anchored patterns + verify with `get_stats`); one DB is
  bound to one project root (index the whole repo, not subtree-by-subtree).
- **Ops hazard (learned the hard way)**: the recon agent ran
  `pkill -f llama-server` and killed the production chat server. **Never
  pattern-kill llama-server; target by explicit PID.** Add to serve/stop
  discipline.
- Effort: **~0.5 day** (CPU embedding sidecar wired into start.sh/stop.sh +
  `.chunkhound.json` with local base_url + reindex script + opencode.json
  stdio entry + tight-output tool guidance).

## Proposed attack plan

1. **Phase 1 (~1 week):** Sandlock wrap of the bash tool (extends the OOM
   hardening — sandbox review first) + the **file-tool path allowlist**
   (the real credential-exfil fix, Sandlock-independent) + ChunkHound with
   a CPU llama.cpp embedding sidecar (closes the RAG Future Horizon item).
2. **Phase 2 (~3 days):** Playwright CLI skill + in-house canned-query
   SQLite tool.
3. **Phase 3 (2–4 days):** in-house memory tool (sqlite-vec vs LanceDB
   bake-off, llama.cpp embeddings, MCP surface of 2–3 narrow tools).
4. **Deferred:** Firecracker tier; PDF tooling research round 2;
   slot-aware orchestration when multi-agent load actually demands it.

Every adoption passes the security gate from docs/TODO.md "Selectively pull
skills" — per-tool review, pinned hashes, sandbox-probe before landing.
