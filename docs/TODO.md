# TODO

## Up Next

### v3.5 prereq — fix `159_ntt_convolution` spec defect (BLOCKS overnight sweep)

The 2026-05-06 smoke test on the 35B-A3B Uncensored revealed the same class
of spec defect we caught 4× in the v1 post-mortem — input-format ambiguity —
this time in a Phase-2 hardening task we authored ourselves.

**The bug.** `159_ntt_convolution` test fixtures include an empty-array case
`([], [1,2,3])` to exercise the "if either input is empty, output '0'" branch.
The setup script writes the empty array as a blank line in `input.txt`. All 4
language variants fail in different ways:

- **Python:** `IndexError` on parse — naive `line.split()` returns empty list,
  next-line `NA, NB = na_nb[0], na_nb[1]` blows up
- **Go:** outputs `"0 0"` instead of expected `"0"` for the empty case
- **C:** same as Go — outputs an extra `"0 "`
- **C++:** missing the leading `"0"` line entirely

Because the failure mode is per-language, the cross-language matrix shows
`159_ntt_convolution: FFFF` which spuriously suggests "the model can't do
NTT in any language." It can — we proved 8/12 NTT-equivalent variant tasks
pass cleanly. The issue is our test format.

**The fix.** Two equivalent approaches:

1. **Drop the empty-array case** from the test fixtures. The remaining 14
   cases still cover correctness; the empty edge case isn't load-bearing for
   the algorithm itself.

2. **Change the input format** so empty arrays are unambiguous. E.g., put
   `NA NB` and the values on the same line: `0 3 1 2 3` for an empty `a`
   followed by `b = [1, 2, 3]`. Update all 4 variant tasks to expect this
   format.

Recommend (1) — smaller diff, clearer intent, and we're not specifically
trying to test empty-input handling here. The other 14 test cases exercise
the algorithm well enough.

**Verification before unblocking the overnight sweep:**

```bash
# After the fix, drop the reference impls into /tmp/eval_ntt and re-verify
python3 tests/audit_variants.py 159_ntt_convolution
# Should show: 4/4 variants passed (was 0/4 in the smoke test)

# Then run regression
bash tests/test_scripts.sh   # still 47/47

# Then a quick single-task confirmation against the live model:
python3 evals/run_eval.py --tasks 159_ntt_convolution
# Expect 4/4 passes
```

**Cost of NOT fixing before overnight:** every model in the 5-model sweep
would burn ~16 task slots (4 NTT variants × 4 models reaching that task)
on a known-broken test. ~80 wasted task entries in the overnight data.

**Estimated effort:** 30 minutes including verification. Pick up next session
before kicking off `python3 evals/benchmark_all.py`.

### Tonight — overnight v3 sweep on the 5090 (DEFERRED — gated on the NTT fix above)

The v3 eval suite is fully landed (Phases 1–4) and the smoke test on the
winning model validated everything except the NTT spec defect noted above.
Once that's fixed, the overnight sweep is the next step. Plan:

**Run command:**

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

**Smoke test recalibration.** The 35B-A3B Uncensored took **207 min** for
197 tasks (vs the 70-80 min original estimate) due to four 18-30 min
outliers (rkf45 max_iter, aes_keysched timeout, berlekamp_massey timeout,
karatsuba_cpp 28-min grind). Apply this realism to the per-model estimates
above — the slower models likely overshoot too. Plan for **12-14 hour total
overnight wall-clock**, not 10. Kick off well before bed.

**Smoke test scoreboard for reference:**

```
Qwen 35B-A3B Uncensored Q4_K_M  v3:  91.8  86.4  90.4  177/197  65/80  12388s  9.8M
                                v1:  97.3  86.7  94.6  140/144  49/51   3024s    —
```

The accuracy drop (97.3 → 91.8) is expected — harder suite + variant work
surfaced real cross-language gaps that didn't exist in the Python-only v1.
4 cross-language differentials confirmed (`31_is_power_of_two`, `51_toposort`,
`155_tonelli_shanks`, `158_karatsuba_bytes`).

**After the sweep finishes:**

```bash
python3 evals/scoring.py --show                    # top-line + tokens column
python3 evals/scoring.py --by-category             # which categories de-saturated
python3 evals/scoring.py --by-language             # per-language drilldown across variants
```

Update `docs/RESULTS.md` with the v3 sweep section once the leaderboard is
final. The smoke-test post-mortem (in chat history but not yet documented)
should also land here as a reference v3.5 baseline for the 35B-A3B Uncensored.

### Inference-time strategy — "Python-first, then port" for hard cross-language tasks

**Empirical finding (2026-05-06):** Qwen 35B-A3B Uncensored shows a stark
language asymmetry — **Python 93.21 %**, C++ 74.02 %, C 69.29 %, **Go 66.93 %**
on the v3 variant matrix. Same algorithm, same model, ~26-point delta across
languages. The Tonelli-Shanks Go failure made the mechanism concrete: the
model knew the math (Python ✓, C ✓, C++ ✓) but kept thrashing on Go's typed-int
edges around `(*big.Int).Lsh(n, uint(j))` for negative `j`. Five debug cycles,
239K tokens, max_iter exhaustion — the algorithmic knowledge transferred
across three languages; the *language-specific* edge case did not.

**Hypothesis on the cause:** Qwen's training mix is Python-heavy. Algorithmic
content (LeetCode, competitive-programming archives, library implementations,
StackOverflow Q&A) is overwhelmingly in Python. For hard / niche algorithms
(NTT, Tonelli-Shanks, Karatsuba on byte arrays), the Go / Rust / Zig training
density is *much* thinner — the model has fewer examples of the algorithm
expressed in those languages, so it has to compose from first principles
while *also* navigating language-specific gotchas (Go's `uint` cast wrap, C's
manual lifetimes, Zig's explicit error unions). That double cognitive load
is where it cracks.

**Strategy for hard-tier cross-language work — both at eval time and in
real coding sessions:**

1. **Solve in Python first.** Get a working, tested reference. The model
   is fluent in Python — it'll converge fast, with high accuracy.
2. **Then port.** Hand the model the working Python and ask for a port to
   the target language (Go / Rust / Zig / etc.). Porting is a *narrower*
   cognitive task — the algorithm structure is fixed, only the syntax and
   language-idiom translation remain. The model's port-from-Python success
   rate is plausibly higher than its from-scratch success rate in the target
   language, because it isolates the failure mode (typed-int handling,
   memory management) from the algorithmic part.
3. **Reserve for advanced / extreme circumstances.** This isn't worth the
   round-trip overhead on easy tasks — Qwen handles `is_power_of_two` in
   any language without help. Apply when (a) the task is hard-tier *and*
   (b) the target language is non-Python *and* (c) the model has already
   failed once or shows signs of language-specific thrashing.

**How we could test the hypothesis empirically (eval framework changes):**

- Add a `port-from-reference` variant flavor: same task spec, but the
  agent's prompt includes the Python reference impl as context, and the
  ask is "port this to Go" (or Rust / Zig). Compare pass rate vs. the
  current "write from scratch" Go variant on the same base task.
- If the port-from-reference rate is materially higher (say ≥10 points)
  on hard tasks, the strategy is empirically validated. Could become a
  documented best practice in `AGENTS.md` and a candidate skill
  (`python-first-then-port`).

**Why this isn't a substitute for testing the model directly.** The eval
suite measures unaided capability — what the model can do without us
front-loading the algorithm. Both views matter: unaided capability tells
us the *honest* gap; the port-from-reference view tells us how to
*operate around* that gap in production.

**Action items:**
- [ ] Document the strategy in `AGENTS.md` so OpenCode users see it
- [ ] Consider authoring a `python-first-then-port` skill (Tier 3 — not
      universal, but high-value for the niche it covers)
- [ ] Add 1-2 port-from-reference variant pairs to the eval suite to test
      the hypothesis quantitatively (pick from the hard-tier 4 confirmed
      differentials: 31 / 51 / 155 / 158)

### ~~Complete Rust + Zig variant rollout~~ ✓ DONE (2026-05-06)

**Shipped.** All 13 variant base tasks now cover all 6 languages
(python/go/c/cpp/rust/zig). 77 variant entries total, all verified end-to-end
via `python3 tests/audit_variants.py`. See "Completed" section below for the
landing entry.

Toolchain prerequisites added to `docs/INSTALL.md`:
- Rust 1.95.0 (system pacman install)
- Zig 0.16.0 (mise-installed; new Zig 0.16 I/O API uses
  `pub fn main(init: std.process.Init)` with `std.Io.File.stdin()/stdout()`
  and `takeDelimiter('\n')` for line reads)

Effective test units rose from **197 → 223** (+26 = +13 Rust + +13 Zig).

**Trade-off worth noting:** Zig builds take ~8s per task (cold compile).
For the audit pass that's 13 × 8 = ~100s overhead; for the full overnight
sweep, Zig variants will add ~13 × 8 × 5 models = ~9 minutes of pure
compile time across the run. Acceptable; documented for awareness.

### Skills don't fire spontaneously — Phase 5 (auto-routing) priority increase

Smoke test signal: **0 of 197 task agents called `list_skills` /
`load_skill` / `start_skill_agent`**. Despite AGENTS.md being at the
project root, despite the skills system being fully wired, despite the
system-prompt-tools.md mentioning skills as a tool group — the model
doesn't invoke skills proactively on eval-style tasks.

Why this is OK in the short term: eval task descriptions are too directive
("Create /tmp/eval_X/file.py with…") for a skill description to feel
relevant. We saw `list_skills` fire ONCE in OpenCode interactive use
(per `opencode stats`), which is the more realistic case.

Why this matters longer term: the skills are valuable specifically when
the model's task is open-ended ("review this code", "audit this for bugs",
"write a new eval task"). For those, AGENTS.md task→skill mapping is the
nudge; we want to confirm it's working in practice over the next few
weeks of normal use.

**Action:** if real OpenCode usage over the next ~2 weeks shows skill-fire
rate stays near-zero, escalate Phase 5 (auto-routing layer from
`docs/SKILLS_PLAN.md`). The pre-flight classifier becomes worth the
engineering cost. If skills do fire on real conversational work, hold.

### Expand multi-language variant coverage (driven by Tonelli-Shanks finding)

**The Tonelli-Shanks Go failure during the 2026-05-06 smoke test was the
first piece of evidence that variant testing surfaces information the
Python-only suite cannot.** That alone reshapes the priority of variant
expansion. Documenting here so we don't lose the thread.

**What happened.** Task `155_tonelli_shanks` is a hard-tier number-theory
task (modular square root). The 35B-A3B Uncensored solved it cleanly in
Python ✅, C ✅, and C++ ✅. The Go variant **failed at max_iter (25)** after
**239K tokens** and 5 visible debugging cycles. The model knew the
algorithm — it had just written it correctly in three other languages.

**Root cause from the agent log:** the model kept tripping on Go's strict
typed-int arithmetic around the bit-shift step in the Tonelli-Shanks
inner loop. Specifically `j = M - i - 1` going negative and underflowing
when used with `(*big.Int).Lsh(n, uint(j))` — Go's `uint` cast on a
negative number wraps to a huge value, where Python's `<<` would raise
ValueError or just compute. The five recorded fix attempts all
re-encountered the same edge case from different angles.

**Why this is important.** This is exactly the kind of cross-language gap
the variant system was built to surface, and which a Python-only test
literally could not. The algorithmic knowledge transfers; the
language-specific handling of edge cases (typed int overflow, big-int API
ergonomics, memory management discipline) does not. Whether a model can
write correct Go is a separate capability from whether it knows
Tonelli-Shanks.

**Implication for the suite.**

If the smoke test (and tonight's full sweep) produces more findings of
this shape — Python passes, one or two compiled languages fail — the case
for expanding variant coverage gets much stronger. The original Phase 4
plan deferred 5 tasks (53_bloom, 145, 146, 152, 153) as "too heavy this
session." After the Tonelli finding, **at least 145 (segment tree) and
146 (Aho-Corasick) jump in priority** — both are algorithmically dense
in ways that exercise language-specific data-structure idioms (Go's
slice-of-slice aliasing, C's manual lifetime management, C++'s
RAII-vs-raw-pointer choices). They're exactly the right shape to produce
more cross-language differentials.

**Concrete next-step plan, gated on tonight's full sweep results:**

1. **After the 5-model overnight sweep**, audit per-language pass rates
   across all 77 variant entries (post-Phase 4.5). Count how many tasks show
   the "Python ✓ / one-or-more compiled-lang ✗" pattern. With Rust + Zig
   added, expect *new* failure modes (Rust borrow checker, Zig explicit
   error unions) that the original 4-language matrix could not surface.
2. **If ≥3 tasks show that pattern**, escalate variant expansion. Phase 4
   deferred items move up the priority list. Consider adding variants to
   currently single-variant tasks where they'd be most informative
   (likely candidates: `27_brainfuck_interpreter`, `54_astar`,
   `45_kv_cache`, `108_hmac_verify`, `137_pollard_rho`).
3. **If only 1-2 tasks show the pattern** (Tonelli-Shanks alone +
   maybe one more), variants are still valuable but the marginal value of
   expanding from 13 → 20 base tasks is lower. Prioritize other axes
   (skills routing, agentic tasks, speculative decoding).
4. **Also useful:** look at WHICH language fails most often. If Go is
   consistently the weak point, that's a different signal than "C is the
   weak point." Could inform skill creation (e.g., a `go-bigint` skill
   encoding the typed-int gotchas).

**Don't forget:** add this story to `docs/RESULTS.md` v3 sweep section
once the post-mortem is written. The Tonelli-Shanks Go anecdote is the
single most concrete piece of evidence for why we did Phase 4.

### Token usage aggregator (`scripts/token-stats.py`)

Production-mode telemetry — pull from existing sources, no new service, no
runtime overhead. Built once, run on demand.

**The pitch.** Tokens are tracked everywhere already, but fragmented across
three stores:

| Source | What it has |
|---|---|
| `opencode stats` (sqlite under `~/.local/share/opencode/`) | Per-session input/output/cache tokens, tool usage |
| `agents/logs/agent-*.jsonl` | Per-task tokens for autonomous agents + MCP-spawned sub-agents (already added) |
| Open WebUI sqlite (Docker volume) | Per-conversation tokens |

A small aggregator reads all three and prints a unified report. **Zero
overhead at runtime** — only runs when you ask for a snapshot. Local model
is free, so this is for awareness/efficiency analysis, not billing.

**Output shape (target):**

```
$ ./scripts/token-stats.py --last-7d

LAST 7 DAYS
================================================================
Source          Sessions    In tokens    Out tokens    Cache rd
opencode             18         1.2M          187K        21M
agents/logs/         34         580K           47K          —
open-webui            5         220K           29K       4.5M
================================================================
TOTAL                57         2.0M          263K       25.5M

Equivalent Sonnet 4.6 spend: ~$13.85
Equivalent Opus 4.7 spend:   ~$45.20
```

**Design notes (work out details when we build it):**
- Read-only against each source — never modifies anything
- Optional flags: `--last-Nd`, `--by-client`, `--by-model`, `--cost-as=sonnet|opus|gpt5`
- Cache reads are billed at ~10% of input rate at frontier APIs — model that into the cost-translation
- Only blind spot: interactive `run.sh` chat (no API path; no usage data emitted). Acceptable — marginal use.
- Optional flag `--watch` for periodic refresh, but not the default

**Why not the proxy idea (`token_proxy.py` on :8090):**
Investigated and rejected. OpenCode's existing telemetry is comprehensive
enough that a proxy would be ~80% redundant, add 1-2 ms to every request,
and require streaming-SSE handling. The aggregator hits the same data with
zero overhead. Revisit only if a real-time use case appears (none yet).

**Implementation:** ~150 lines of Python, one new file
`scripts/token-stats.py`. Estimated 1-2 hours including the cost-translation
table and a couple of useful flags.

**Trigger:** build after the v3 sweep lands and the post-mortem is written.

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
- [x] Phase 4 (initial) — 51 variant entries across 13 tasks (Python / Go / C / C++); reference impls verified end-to-end. 5 base tasks deferred (see "Up Next"). [Superseded by Phase 4.5: Rust + Zig added across all 13 → 77 entries — see entry below.]
- [x] Default model swap to Qwen 35B-A3B Uncensored Q4_K_M (top of leaderboard); start.sh, healthcheck.sh, opencode.json reordering, README/INSTALL/REFERENCE all updated
- [x] Docs reorganized: INSTALL/REFERENCE/RESULTS/WORK_PLAN/TODO moved to `docs/`; README and system-prompt files stay at base
- [x] RESULTS.md eval distribution section (categories × difficulty + subcategory drilldown + variant matrix)
- [x] **Skills system landed** — Phases 1-4 complete. `list_skills` / `load_skill` / `start_skill_agent` / `reload_skills` MCP tools; 6 starter skills (code-review, security-audit, debugging-methodology, deep-counsel, eval-task-author, eval-variant-porter); repo + global discovery with repo-wins-on-collision; `scripts/install-skills.sh` for global symlinks. Phase 5 (auto-routing layer) deferred. See [docs/SKILLS_PLAN.md](SKILLS_PLAN.md).
- [x] **8 more skills authored** (Tier 1 + Tier 2 from frontier-model behaviors) — `codebase-onboarding`, `spec-extraction`, `git-discipline`, `long-context-synthesis` (Tier 1, universal); `test-driven-development`, `architecture-proposal`, `performance-optimization`, `api-design` (Tier 2, situational). Total: 14 skills.
- [x] **AGENTS.md at project root** — auto-loaded by OpenCode; nudges the model toward skills with a task→skill mapping. Pairs with the MCP tools to make skills first-class for any OpenCode session in this repo.
- [x] **Rust + Zig variant rollout complete** (2026-05-06) — all 13 variant base tasks now cover 6 languages (python/go/c/cpp/rust/zig); 77 variant entries audited end-to-end with reference impls. Zig 0.16.0 installed via mise; Rust 1.95.0 from pacman. Effective test units: 197 → 223. The cross-language matrix can now surface failure modes specific to ownership/borrow-checking (Rust) and explicit error unions / compile-time checks (Zig). Audit script persisted at `tests/audit_variants.py`.
