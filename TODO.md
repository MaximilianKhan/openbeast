# TODO

## Completed (2026-04-27)

- [x] Debug Open WebUI MCP connection failure — replaced direct MCP HTTP with MCPO (MCP→OpenAPI proxy). Open WebUI's native MCP Streamable HTTP support has a known bug; MCPO wraps our MCP server as OpenAPI endpoints that Open WebUI consumes natively. Also set model function_calling to `native` (prompt-based default mode broke with Qwen's thinking). Both configs are now automated via `scripts/configure-webui.sh`, called by `start.sh`.
- [x] Verify OpenCode MCP stdio transport — works correctly, confirmed via initialize handshake.
- [x] Test `./agent.sh` end-to-end — completed a real task in 3 iterations with tool use.
- [x] Validate 35B-A3B actual KV cache allocation — measured ~6.3 KB/token (model=20GB, KV at 512K=3.1GB). Much better than the 11 KB/token estimate. Could safely run at 1M context.
- [x] Confirm Open WebUI persistence survives stop/start — Docker named volume persists across `docker compose down` (only `-v` flag removes it).
- [x] `git init` and first commit — done.
- [x] Long-running agent management via MCP — start_agent, check_agent, list_agents, stop_agent.
- [x] Claude Code-caliber tool suite — edit_file (targeted string replacement), fetch (URL content with HTML→text).
- [x] 7-slot parallel serving with unified KV cache and continuous batching.
- [x] Script refactor — moved 11 scripts to `scripts/`, root has 3 entry points.
- [x] Test suite — 78 tests across script structure and Python tool unit tests.
- [x] Fixed grep `repr()` bug — replaced with `shlex.quote()` for correct regex escaping.
- [x] Context-aware agent spawning — `start_agent` accepts `context` param, agents get context budget info.
- [x] Local web search via SearXNG — `web_search` tool + Docker container on port 8888.
- [x] Agent tail tool — `tail_agent` returns raw JSONL log events for detailed debugging.
- [x] Model-aware context budgeting — agents informed of ~73K token budget per slot.
- [x] Agent resumption — `--resume` flag reconstructs conversation from JSONL log and continues.

## Tier 3 — Future Horizon

### Multi-model routing
Run two models simultaneously on different ports (e.g., 35B-A3B on :8080 for fast agent
work, 27B Q5 on :8081 for deep reasoning). Build a lightweight router that picks the
right model based on task type, context requirements, or explicit user preference.

**Why:** The MoE 35B-A3B is fast but the dense 27B Q5 has higher weight fidelity. Different
tasks have different quality/speed tradeoffs. A router lets agents use the fast model for
file exploration and the quality model for complex code generation, without manual switching.

**Implementation sketch:**
- Second serve script on a different port (VRAM permitting — would need smaller contexts)
- Router service (Python, ~100 lines) that accepts OpenAI API requests and forwards to
  the appropriate backend based on a `model` field or heuristic
- Update `runner.py` to support model selection per-call or per-tool-phase

### RAG pipeline for local codebases
Embed local codebases into a vector store and give agents semantic search beyond grep.
When grep can find exact strings but the agent needs to ask "where is authentication
handled?" or "what modules touch the database?", semantic search fills the gap.

**Implementation sketch:**
- Use llama.cpp's embedding endpoint (`/v1/embeddings`) with a small embedding model
- ChromaDB or LanceDB for local vector storage (no external dependencies)
- New MCP tool: `semantic_search(query, codebase_path)` that returns ranked file
  chunks with relevance scores
- Indexing script that walks a codebase, chunks files, and embeds them
- Re-index on git hooks or manual trigger

### Eval harness for model comparison
Benchmark different quantizations (Q4 vs Q5), models (27B vs 35B-A3B), and context
lengths against standardized coding tasks. Know when to upgrade — and whether a new
model is actually better for your workloads before swapping it in.

**Implementation sketch:**
- Curated set of 20-30 coding tasks (bug fixes, refactors, feature adds) with known
  correct outputs or test suites that validate correctness
- Runner that executes each task against a specified model/config and records:
  success/fail, iterations to completion, time, token usage
- Results stored as structured JSON, diffable across runs
- Could reuse `runner.py` with `task_done` output validation
