# TODO

## Up Next

### Tonight — overnight v3 sweep on the 5090

The v3 eval suite is fully landed (Phases 1–4). Time to actually run it. Plan:

**Step 1: Smoke test the winning model first (~70–80 min).**

```bash
python3 evals/benchmark_all.py --models qwen-35b-a3b-uncensored-q4
```

This validates that:
- All 51 multi-language variants behave correctly under a real agent (not just
  reference impls)
- Token tracking populates the new column on the leaderboard
- The 15 hardening tasks (145–159) produce reasonable pass rates on a top-tier model
- The 4 Phase-1 spec fixes flip pass/fail as predicted (42, 85, 121, 17)

If anything blows up, fix it before launching the full sweep.

**Step 2: Full 5-model overnight sweep (~10.5–11.5 hours wall-clock).**

```bash
python3 evals/benchmark_all.py
```

Per-model wall-clock estimate (linearly scaled from the 144-task sweep at
1.37× the unit count, plus ~5–10% for the harder hardening tasks):

| Model | Old (144) | Projected (197) |
|---|---:|---:|
| Qwen 35B-A3B Uncensored Q4_K_M | 50 min | **~69 min** |
| Qwen 35B-A3B MoE Q4_K_M | 49 min | **~67 min** |
| Qwen 27B Uncensored Q5_K_P | 97 min | **~133 min** |
| Qwen 27B Q5_K_XL | 116 min | **~159 min** |
| Gemma 4 31B-it Q5_K_XL | 127 min | **~174 min** |
| **Sum** | **~7h 21m** | **~10h 0m + 5–10% buffer** |

Adjustments folded in:
- +5–10% for the 15 new hard-tier tasks (lazy segtree, AC machine, NTT, etc.)
  pushing into longer iteration loops
- +~5 min for variant compilation (47 non-Python variants × ~1s × 5 models)
- Token tracking, pre_validate, fixture re-assertion: all negligible

**Step 3: After the sweep finishes, review the v3 leaderboard.**

```bash
python3 evals/scoring.py --show                    # top-line + tokens column
python3 evals/scoring.py --by-category             # which categories de-saturated
python3 evals/scoring.py --by-language             # per-language drilldown across the 51 variants
```

Update `docs/RESULTS.md` with the v3 sweep section once leaderboard is final.

**Expected impact** (from WORK_PLAN.md): ~+1.3 accuracy points across all
models from the Phase-1 spec fixes alone, with new discrimination signal in
the 3 previously-saturated categories (Algorithms & DS, Concurrency &
Systems, Pure & Abstract Math) from the hardening tasks. Token column gives a
new view on per-model efficiency.

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

### Phase 4 follow-up — variant the 5 deferred tasks
Phase 4 shipped 13 of 18 originally-planned tasks with multi-language variants
(see docs/WORK_PLAN.md "Phase 4 deferred items" for full breakdown). Five
tasks remain: 53_bloom (probabilistic test), 145_segment_tree_lazy, 146_aho_corasick,
152_chase_lev_deque, 153_coroutine_scheduler. Each has a specific reason it
was held back. Pick up in a focused follow-up session — the audit pattern from
the 13 completed tasks transfers cleanly.

### Eval harness — agentic + tool-selection tasks
Next axis after variants: agentic tasks (require `web_search` / `start_agent`)
and tool-selection tasks (does the model prefer `edit_file` over `write_file`?
Does it know when to spawn a subagent?). These don't fit the current
deterministic-validation pattern and need a separate harness path.

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
- [x] Gemma 4 31B-it integrated as 5th model (220K context, validated)
- [x] Context lengths re-tuned to measured VRAM ceilings (Qwen + Gemma)
- [x] SearXNG fixed: granian port collision + JSON format gate
- [x] Eval harness expanded: 10 → 30 → 50 → 70 → 128 → 133 → 144 tasks (40 easy / 53 medium / 51 hard)
- [x] Model-tagged eval results with GPU snapshot via nvidia-smi
- [x] Multi-model benchmark runner (`evals/benchmark_all.py`)
- [x] Accuracy-primary leaderboard with composite (0.75×accuracy + 0.25×speed) shown
- [x] 12-category taxonomy with subcategory drilldown (`scoring.py --by-category`)
- [x] All 144 tasks verified end-to-end against canonical solutions
- [x] 4 task bugs fixed (23 SQLi tripwire, 27 BF typo, 33 daily-compounding ref, 36 BS ref)
- [x] Qwen 35B-A3B Uncensored Q4_K_M added as 5th active model
- [x] Removed redundant Qwen 27B Q4_K_M (Q5 variant supersedes)
- [x] VRAM measurements re-calibrated (35B-A3B: 23.1 → 27.8 GB at 512K)
- [x] Multi-host leaderboard schema (host_id keying, `--compare-hosts`, `--host` filter)
- [x] Full 144-task × 5-model sweep on RTX 5090 (7h 21m, all 5 succeeded — see RESULTS.md)
- [x] Post-sweep post-mortem: identified 4 spec/harness defects (42 numpy lint, 85 type ambiguity, 121 return contract, 17 fixture corruption)
- [x] Phase 1 — 4 spec/harness fixes landed; `pre_validate` field added to harness for opt-in fixture re-assertion
- [x] Phase 2 — 15 hardening tasks added (145–159) across the three saturated categories; suite is now 40 easy / 53 medium / 66 hard = 159 total
- [x] Cheat-resistance perf gates added to 150 + 152 (catches list.pop(0) impls)
- [x] Phase 3 — multi-language variant architecture in `run_eval.py` + `scoring.py` + result schema; backward-compat regression bit-identical
- [x] Token tracking through runner → eval → scoring → leaderboard (separate column, not part of rank)
- [x] `evals/README.md` with full distribution table, schema, scoring, and pitfall-lessons-learned section
- [x] Phase 4 (substantial) — 51 variant entries across 13 tasks (Python / Go / C / C++); reference impls verified end-to-end. 5 tasks deferred (see "Up Next").
- [x] Default model swap to Qwen 35B-A3B Uncensored Q4_K_M (top of leaderboard); start.sh, healthcheck.sh, opencode.json reordering, README/INSTALL/REFERENCE all updated
- [x] Docs reorganized: INSTALL/REFERENCE/RESULTS/WORK_PLAN/TODO moved to `docs/`; README and system-prompt files stay at base
- [x] RESULTS.md eval distribution section (categories × difficulty + subcategory drilldown + variant matrix)
- [x] **Skills system landed** — Phases 1-4 complete. `list_skills` / `load_skill` / `start_skill_agent` / `reload_skills` MCP tools; 6 starter skills (code-review, security-audit, debugging-methodology, deep-counsel, eval-task-author, eval-variant-porter); repo + global discovery with repo-wins-on-collision; `scripts/install-skills.sh` for global symlinks. Phase 5 (auto-routing layer) deferred. See [docs/SKILLS_PLAN.md](SKILLS_PLAN.md).
- [x] **8 more skills authored** (Tier 1 + Tier 2 from frontier-model behaviors) — `codebase-onboarding`, `spec-extraction`, `git-discipline`, `long-context-synthesis` (Tier 1, universal); `test-driven-development`, `architecture-proposal`, `performance-optimization`, `api-design` (Tier 2, situational). Total: 14 skills.
- [x] **AGENTS.md at project root** — auto-loaded by OpenCode; nudges the model toward skills with a task→skill mapping. Pairs with the MCP tools to make skills first-class for any OpenCode session in this repo.
