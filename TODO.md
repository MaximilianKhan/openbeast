# TODO

## Up Next

### Tailscale remote access
Set up Tailscale so the local AI stack can be accessed from the work laptop (or
any device) over a private encrypted mesh — no port forwarding or static IP needed.

**Steps:**
1. Install Tailscale on the home machine (Arch): `sudo pacman -S tailscale`
2. Enable and start: `sudo systemctl enable --now tailscaled && sudo tailscale up`
3. Install Tailscale on the work laptop
4. Note the home machine's Tailscale IP (e.g., `100.64.x.x`) from `tailscale status`
5. On the work laptop, create `~/.config/opencode/opencode.json` with the provider
   pointing to `http://<tailscale-ip>:8080/v1`
6. Verify: `curl http://<tailscale-ip>:8080/health` from the work laptop
7. Open WebUI is also accessible at `http://<tailscale-ip>:3000`

**Why Tailscale:** Zero config networking. WireGuard-encrypted, NAT-traversing,
works from any network. Free for personal use (up to 100 devices). No router
config, no dynamic DNS, no exposed ports.

### Speculative decoding
Pair the 27B model with a small ~0.5B Qwen draft model for 1.5-3x inference
speedup. llama.cpp supports this natively via `--model-draft`. Biggest gains
on structured output (code, JSON) where draft tokens are predictable.

**Steps:**
1. Download a small Qwen 3.6 draft model (0.6B or similar) to `weights/`
2. Add `--model-draft` flag to `scripts/serve.sh`
3. Benchmark before/after with the eval harness
4. Update serve scripts and docs

### Expand eval harness
Current: 10 tasks (easy→hard). Expand to 25-30 with harder multi-step tasks,
agentic tasks (tasks that require web search or agent delegation), and tasks
that test tool selection (does the model pick `edit_file` over `write_file`?).

## Future Horizon

### Multi-model routing
Run two models simultaneously on different ports (e.g., 35B-A3B on :8080 for fast
agent work, 27B Q5 on :8081 for deep reasoning). Build a lightweight router that
picks the right model based on task type or explicit preference.

### RAG pipeline for local codebases
Embed local codebases into a vector store and give agents semantic search beyond
grep. Use llama.cpp's embedding endpoint with ChromaDB/LanceDB for local vector
storage. New MCP tool: `semantic_search(query, codebase_path)`.

---

## Completed

- [x] Debug Open WebUI MCP connection — MCPO proxy, native function calling
- [x] Verify OpenCode MCP stdio transport
- [x] Test agent.sh end-to-end (3 iterations)
- [x] Validate 35B-A3B KV cache (~6.3 KB/token measured)
- [x] Open WebUI persistence confirmed
- [x] Git init + version control
- [x] Long-running agent management via MCP (start/check/tail/list/stop)
- [x] Claude Code-caliber tool suite (edit_file, fetch, web_search)
- [x] 6-slot parallel serving with unified KV cache
- [x] Script refactor (scripts/ directory, 3 root entry points)
- [x] Test suite (79 tests — structure + tools + MCP)
- [x] Fixed grep repr() quoting bug (shlex.quote)
- [x] Context-aware agent spawning with context briefing
- [x] Local web search via SearXNG
- [x] Agent log tailing (tail_agent)
- [x] Model-aware context budgeting (~85K per slot)
- [x] Agent resumption from JSONL logs
- [x] System prompt split (soul file + tools addendum)
- [x] OpenCode global config for models from any directory
- [x] Eval harness — 10 tasks, 10/10 pass rate
- [x] Smoke test (end-to-end stack validation)
- [x] Health monitor with auto-restart
- [x] Default model documented (27B Uncensored Q5_K_P)
