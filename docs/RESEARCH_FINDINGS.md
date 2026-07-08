# OpenBeast research findings

Durable log of what we've learned. Newest sections appended over time.
Numbers are from the RTX 5090 (32 GB) reference box unless noted.

---

## 1. MTP speculative decoding is lossless (2026-07-08)

**Claim tested:** MTP (Multi-Token Prediction speculative decoding) produces
the same output distribution as the base model, just faster.

**Evidence:** on the v3.5 suite, Qwen 35B-A3B MoE scored 93.74 non-MTP vs
**93.76 MTP** — a 0.02 delta, i.e. statistically identical accuracy. The main
model verifies every drafted token, so accepted sequences match what the model
would have produced alone.

**Consequence:** the speculation knobs (`--spec-draft-n-max`,
`--spec-draft-p-min`) can be brute-forced for throughput with **zero accuracy
risk** and no eval suite — only the lossy knobs (weight quant, KV-cache quant,
context) change accuracy. This underpins the profiling work (§5).

## 2. MTP delivers a large single-stream speedup (2026-07-08)

Whole-suite average tok/s (v4 for MTP, v3.5 for the non-MTP baselines):

| Model | MTP tok/s | non-MTP tok/s | speedup |
|---|---:|---:|---:|
| Qwen 27B (dense) | 73.0 | 53.7 | **+36%** |
| Qwen 35B-A3B (MoE) | 83.0 | 74.3 | +12% |

Peak single-stream decode is much higher than the suite average (the profiler
measured 167 tok/s peak for the 27B MTP on a fast fixed workload vs 73 average
including the hard tasks). **MTP is a big win for single-stream / interactive
use at no accuracy cost.**

**Constraint:** MTP is pinned to `-np 1` upstream — concurrent requests
serialize, no parallelism. So MTP suits a solo/orchestrator role; parallel
worker fleets want plain non-MTP with high `-np`. (See docs/TODO.md multi-node
INVESTIGATION.)

## 3. v4 MTP leaderboard — the three-model result (2026-07-08)

Hardened v4 suite, 291 units, RTX 5090:

| Rank | Model | Acc (weighted) | Raw pass | Hard | tok/s | Wall | Zig |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | Qwen 27B MTP (dense) | **95.63%** | 273/291 | 98/104 | 73.0 | 3.8h | 66.6% |
| 2 | Qwen 35B-A3B MTP (MoE) | 93.76% | 254/291 | 85/104 | **83.0** | 4.3h | 34.5% |
| 3 | Qwopus 27B v2 MTP | 93.00% | 260/291 | 89/104 | 75.3 | 4.6h | 44.7% |

**Findings:**
- **The dense 27B wins outright** — highest accuracy AND second-fastest tok/s
  AND fastest wall-clock. Best all-around; the deployment default.
- **Qwopus ≈ MoE, a statistical dead-heat** (0.76 pts apart). By RAW pass
  count Qwopus wins (260 vs 254); by WEIGHTED accuracy the MoE edges it (93.76
  vs 93.00). See §4 for why these can disagree.
- **The "reasoning-enhanced" fine-tune does NOT beat its base.** Qwopus (SFT
  fine-tune of Qwen 27B, Trace Inversion from Claude Opus) scores ~2.6 pts
  under the dense 27B base on this coding/math benchmark. The fine-tune's
  value here is lateral, not upward.
- **MoE trades accuracy for speed:** the 35B-A3B is the fastest (83 tok/s) but
  weakest on the hard tier (85/104) — fewer active params show up on hard
  algorithms.

## 4. Raw pass-count and weighted accuracy can disagree (2026-07-08)

The leaderboard `accuracy` is **difficulty-weighted AND variant-fractional**:
a whole single-variant hard task (~2 pts) is worth ~6× one language-variant of
a 6-way task (~0.33 pts). So a model can pass MORE raw units yet earn FEWER
weighted points if its passes are spread across partially-completed variant
tasks. Concretely, Qwopus passed 89 hard units earning 98.2 pts, while the MoE
passed 85 hard units but earned 100.2 pts by completing higher-value whole
tasks. **Report both metrics; they answer different questions.**

## 5. Zig is the suite's strongest discriminator (2026-07-08)

Per-language accuracy on the v4 variant tasks, MTP models:

| Model | Zig | (other 5 langs) |
|---|---:|---|
| Qwen 27B MTP | 66.6% | ~93-97% |
| Qwopus 27B v2 MTP | 44.7% | ~85-97% |
| Qwen 35B-A3B MTP | 34.5% | ~85-97% |

Every model is near-saturated on Python/Go/C/C++/Rust and **collapses on Zig**
(a 30-point spread across models). Zig is niche and under-represented in
training data. It is the single most useful axis for ranking these models —
a single-language benchmark would have missed the entire distinction. The
dense 27B is best at Zig too; Qwopus's early apparent "Zig edge" over its base
washed out at full sample (though Qwopus does clearly beat the MoE on Zig).

## 6. Context configs (VRAM-measured, 32 GB card)

| Model | `-c` | Context | Why |
|---|---:|---:|---|
| Qwen 27B MTP Q5 | 294912 | 288K | dense Q5 heavy; n-max 8 draft buffers cost ~600 MiB extra |
| Qwopus 27B v2 MTP Q5 | 344064 | 336K | dense Q5, n-max 4 |
| Qwen 35B-A3B MTP Q4 | 524288 | 512K | Q4 MoE — lighter weights + cheaper KV → most room |

The eval tasks use tiny context (well under a few K tokens), so these ceilings
did NOT affect benchmark scores — they matter for deployment (how much
code/conversation you can hold), not for these results.

## 7. MTP throughput profiling — peak deployment configs (2026-07-08)

Sweeping the lossless speculation knobs to find peak tok/s per model
(evals/profile_mtp.py; plan docs/MTP_PROFILING_PLAN.md). HOLDS FIXED the lossy
knobs at each model's leaderboard config.

**RESULT: all three MTP models are already at their optimal config — the
sweep found NO free speedup.** A valuable negative result: the serve scripts
were tuned correctly. For every model, deeper drafting (n12/n16) is slower and
any acceptance gate (p_min > 0) is slower; peak is always the benchmark config.

| Model | Optimal (= benchmark) | Peak tok/s | Gain vs benchmark |
|---|---|---:|---:|
| Qwen 27B MTP | n_max=8, p_min=0.0 | 167.5 | 0% |
| Qwen 35B-A3B MTP (MoE) | n_max=4, p_min=0.0 | 385.3 | 0% |
| Qwopus 27B v2 MTP | n_max=4, p_min=0.0 | 148.7 | 0% |

Full curves (tok/s, generation, on the fixed workload):

| depth @ p0 | n2 | n4 | n6 | n8 | n12 | n16 |
|---|---:|---:|---:|---:|---:|---:|
| 27B MTP | 134.0 | 148.4 | 143.3 | **167.5** | 144.8 | 125.9 |
| 35B-A3B MTP | 335.1 | **385.3** | 352.0 | 256.5 | 230.8 | 212.1 |
| Qwopus MTP | 127.4 | **148.2** | 136.6 | 135.9 | 121.4 | OOM |

p_min gate (at each model's best depth) LOWERS throughput for all three
(never a gain — confirming p_min=0.0 is optimal everywhere):
- 27B @ n8: p0 **167.5** → p0.1 160.6 → p0.25 152.9 → p0.5 142.7
- MoE @ n4: p0 **385.3** → p0.1 382.5 → p0.25 339.3 → p0.5 322.4
- Qwopus @ n4: p0 **148.2** → p0.1 145.7 → p0.25 146.4 → p0.5 140.1

(Measurement noise is ~0.5%: Qwopus n4/p0 read 148.7 in the aborted first run,
148.2 in the clean re-run — same optimum.)

**METHOD NOTE / correction:** mid-run I briefly reported the MoE's
`n4/p0.1 = 382.5` as a speedup — that was premature, before the `n4/p0 = 385.3`
baseline was measured. The gate is a tiny LOSS, not a gain. Lesson: don't call
an optimum before the baseline config is in hand. The MoE's per-token speed
(385 tok/s peak, ~2× the dense 27B) reflects its smaller active-parameter
count, not a tuning win.

**Two tooling findings from the run:**
- **n16 draft buffers can OOM at tight context.** Qwopus at c=344064 (336K,
  ~2.5 GB headroom) crashed llama-server on load at n16 — the deep draft
  buffers exceeded VRAM. The 27B survived n16 at 288K. n16 is never optimal
  anyway, so no deployment impact, but the profiler now skips a failed config
  and continues instead of aborting the whole run (fixed 2026-07-08).
- The sweep is worth running per-model even when the answer is "no change":
  each model has a DIFFERENT optimal depth (27B→n8, MoE/Qwopus→n4), so a
  one-size config would have left the 27B ~13% slower (n4 148 vs n8 167).

## Suite provenance note

Every leaderboard row now records `suite_version` (v3.5 vs v4) + a runtime
block (python/openai/mcp) + full llama.cpp build/commit/source_head + GPU.
The v4 rows are the 3 MTP models; the other 5 are still v3.5 pending a rerun.
The v4 suite itself was rebuilt from a 117-defect review of v3.5 — see
docs/EVAL_REVIEW_2026-07-07.md and docs/EVAL_V4_PLAN.md.

---

## 8. Meta-tool usage: skills fire, agent-spawning does NOT (2026-07-08)

**Test:** `tests/verify_agent_spawn.py` — Qwen 27B MTP (the v4 winner), real
system prompt + realistic 12-tool menu (base tools + start_agent + skills),
prompts that should spawn / should skill / should do neither. Talks straight
to llama-server /v1 (the same decision the model makes behind MCPO/WebUI).

**Results:**

| Behavior | Hit-rate | Verdict |
|---|---|---|
| Skills (`load_skill` on audit/authoring prompts) | 2/2 | ✅ WORKS |
| Controls (don't spawn on math / simple file read) | 2/2 | ✅ correct |
| **`start_agent` on explicit spawn requests** | **0/5 → 1/5** | ❌ **DOES NOT reliably fire** |

- **Skills validated.** The skill-index-in-prompt fix (§ SKILLS_PLAN) works —
  the 27B reliably loads the right skill. Confirms the "inject the menu"
  approach beats blind `list_skills` discovery.
- **Agent-spawning is a hard blocker.** Even when the user *explicitly* says
  "spawn a background agent / kick off an autonomous agent / in the
  background", the 27B does the work INLINE (read_file, grep, bash) and
  ignores `start_agent`. It has a strong "just start coding" prior.
- **Prompt engineering barely moved it.** Directive system-prompt guidance:
  still 0/5. Aggressive *tool-description* ("MANDATORY: … MUST NOT do the work
  yourself"): 0/5 → **1/5**. The tool description is the higher-leverage lever
  (the model weights it at decision time), but 1/5 is still a failure.

**Conclusion:** a 27B local model will NOT reliably delegate to background
agents through tool-choice judgment alone. This is the prerequisite gate for
the whole distributed-agent / multi-node vision — and it is currently RED.

**The real fix (architectural, not prompt):** a **pre-flight intent router** —
a small classifier (cheap heuristic or a tiny model call) that detects a
spawn-request BEFORE the main model runs, and either (a) sets `tool_choice` to
FORCE `start_agent`, or (b) invokes the agent directly and hands the model the
agent ID. Do not rely on the model choosing to delegate; detect the intent and
force it. Same conclusion the SKILLS_PLAN reached for skills (Phase-5
auto-router) — meta-tools on weak local models need routing, not hope.

Applied now regardless: the aggressive `start_agent` docstring (0→1 win) is in
mcp_server.py so production gets the small lift. Per Max's gate ("validate on
the optimal model before profiling others"), we do NOT proceed to profiling
other models' spawn behavior — the blocker must be solved first.

## 9. Agent-spawn router: the mechanism that works (2026-07-08)

Building the pre-flight router (§8 blocker). Tested two forcing mechanisms on
the Qwen 27B MTP:

**Mechanism A — force `tool_choice=start_agent`: FAILS (1/5).** llama.cpp does
NOT strictly honor OpenAI's forced-function semantics — the model still emitted
read_file/grep/list_files despite tool_choice naming start_agent. Cannot rely
on tool_choice forcing at the llama.cpp layer.

**Mechanism B — grammar-constrained JSON classification: WORKS (5/5 + 2/2).**
A pre-flight call with `response_format: json_schema` forcing
`{spawn: bool, task: str, workdir: str}`:
- Spawn detection: **5/5** on explicit requests — and it writes a clean task
  description AND extracts the workdir (e.g. pulled `/home/max/proj` from an
  auth.py path).
- Controls: **2/2** (math question, simple file read → spawn=false).

**The design that follows:**
1. Pre-flight: a grammar-constrained classification call →
   `{spawn, task, workdir}`. Grammar enforcement is what makes it reliable
   (llama.cpp DOES enforce sampling-level grammars, unlike tool_choice).
2. If spawn=true → the router calls start_agent(task, workdir) DIRECTLY —
   deterministic, does not depend on the model choosing to call the tool.
3. If spawn=false → normal conversation.

**This validates the 0.8B-on-CPU idea (Max's question).** The mechanism is a
grammar-constrained JSON call — model-size-agnostic. Proven on the 27B here;
the SAME call should work on a 0.8B served CPU-only (GPU-free, ~100-200ms),
which is the better deployment: the classifier shouldn't burn a GPU inference
on every user turn. The JSON contract `{spawn, task, workdir}` answers "what
does it return" — grammar-forced JSON, not free text or a bare boolean (the
task/workdir extraction comes free). Next: test a 0.8B does 5/5 too; fine-tune
only if it doesn't.

## 10. Router decision: grammar-only on the main model (no CPU model needed) (2026-07-08)

Per the minimal-components directive: add the CPU 0.8B model ONLY if the
grammar method doesn't work all the time. It does.

16-case stress test (explicit spawn, IMPLICIT spawn w/ no keywords, inline
controls, and traps that mention "agent/background" but aren't spawn requests):

| Router | Accuracy | Latency | Notes |
|---|---|---|---|
| 27B, grammar, thinking ON | 13/13 then empty-JSON fail | slow | reasoning ate the token budget before the JSON |
| **27B, grammar, `enable_thinking=false`** | **16/16** | ~493ms | bulletproof; the answer |
| Qwen3-0.6B CPU, grammar | 5/5 explicit + 4/4 control, missed 1 implicit | ~500ms | GPU-free, but 9/10 |

**DECISION: the router is a grammar-constrained pre-flight call to the main
model with `enable_thinking=false` — ZERO new model components.** Classifies
`{spawn, task, workdir}` at 16/16 across explicit/implicit/trap cases. No CPU
model, no second server. The ~500ms/turn cost is a GPU inference on the model
we already run — acceptable for a pre-flight gate.

**THE load-bearing implementation detail:** the pre-flight call MUST disable
thinking (`chat_template_kwargs: {enable_thinking: false}`). A reasoning model
left thinking will intermittently exhaust max_tokens on its reasoning block and
return empty content before the JSON — the exact failure seen at thinking-ON.

The CPU 0.8B (unsloth/Qwen3.5-0.8B-GGUF) stays a valid FUTURE option if we ever
want the router fully off-GPU (frees the main model from per-turn double-duty)
or need many turns/sec — but it's not needed for correctness. Reserve, not shipped.

**Per-request toggle confirmed isolated (2026-07-08).** On the SAME running
27B server, back-to-back: default request → 1036 chars reasoning (thinks);
`enable_thinking=true` → thinks; router call `enable_thinking=false` → 0
reasoning, clean JSON; default request AGAIN → 1036 chars reasoning. The
thinking-off router call does NOT contaminate subsequent normal calls — the
toggle is stateless/per-request. So normal usage keeps thinking ON (default,
no config change) while the router opts itself out for its one classification
call. One model, one server. Confirms: no CPU model required.

## 11. Router: final design, ready to build (2026-07-08)

Consolidated conclusion of §8–10. The agent-spawn router is fully specified
and proven; only implementation remains.

**Design (zero new model components):**
1. Pre-flight, per user turn: one grammar-constrained call to the MAIN 27B with
   `chat_template_kwargs: {enable_thinking: false}` and a json_schema forcing
   `{spawn: bool, task: str, workdir: str}`. ~500ms, 16/16 accuracy.
2. If `spawn == true`: call `start_agent(task, workdir)` directly (deterministic
   — bypasses the model's unreliable tool-choice judgment) and return
   "started agent <id>", keeping the conversation going.
3. If `spawn == false`: proceed with the normal turn (thinking ON, default).

**Where it lives:** a thin proxy in front of llama-server `/v1/chat/completions`
(WebUI/OpenCode point at it). One interception point, both frontends.

**Proven facts backing it:**
- tool_choice forcing is unreliable in llama.cpp (1/5) — do NOT use it.
- grammar/json_schema IS enforced at the sampler — reliable structure.
- enable_thinking is per-request and isolated — normal turns keep thinking;
  the router call opts itself out; neither affects the other.
- start_agent won't fire on model judgment (0/5→1/5) — hence force via the
  deterministic direct call, not via asking the model.

**Not needed:** a CPU classifier model. Reserve option only (unsloth/
Qwen3.5-0.8B-GGUF) if we later want the router fully off-GPU or high-throughput.

**Remaining work:** build the proxy + the forced-spawn UX; then re-run
tests/verify_agent_spawn.py through the proxy to confirm end-to-end. This
unlocks the distributed-agents feature (docs/DISTRIBUTED_AGENTS_PLAN.md).
