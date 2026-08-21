# Router classify sidecar — CPU-resident 0.8B pre-flight classifier

**Status: PROPOSED (2026-08-21). Experiment-gated — nothing here is built.**
Staged in `docs/TODO.md`; run the validation gate (§6) before wiring anything
into a default. **Adversarially reviewed 2026-08-21** (independent security
and performance passes); the reviews found 3 high-severity design errors and
2 false claims in the original draft — all corrected below, and the notable
ones called out inline as ⚠️ REVIEW so they aren't re-lost.

## 1. Why

The opt-in agent-spawn router (`agents/router.py`, `AGENT_ROUTER=true`) makes
a pre-flight classification call **to the primary model** on every turn that
passes its keyword prefilter. On the shipped default that primary is an MTP
config with a **single slot** (`-np 1`), so the ~500 ms classify does not just
add latency — it *occupies the only generation slot* ahead of the user's real
turn. Measured 2026-08-14: hint-triggering turns cost **17.8 s / 45.2 s vs
15.1 s** for a no-hint turn. The classify is also the likely source of the
sub-second "thinking flash" users see before some replies.

The fix direction: move the classify to a model that never competes with the
primary. The classify task is tiny — binary spawn-intent plus two short
strings, emitted under a **strict JSON-schema grammar constraint** with
thinking disabled and `temperature 0` (`agents/router.py _classify`). That is
a job for the smallest model we own, on hardware the primary doesn't use: the
CPU.

Explicitly in scope: pre-flight intent classification only. **Not** in scope:
tool-call selection (the primary model decides its own tool calls inside its
own context — a sidecar cannot speak for it), and generation of any
user-visible content.

## 2. Current architecture (facts, verified in source)

- `router.py` listens on `OPENBEAST_ROUTER_PORT` (8088), proxies to
  `OPENBEAST_LLAMA_UPSTREAM` (8080). **The classify call uses the same
  `UPSTREAM`** — there is no second-model plumbing today.
- Flow: identity gate (`X-OpenWebUI-User-Role`) → precision-tuned keyword
  prefilter (`_HINTS`) → grammar-constrained classify (`max_tokens 400`,
  `temperature 0`, `enable_thinking false`, strict `json_schema`) → on
  `spawn=true`, POST MCPO `/start_agent`; otherwise transparent proxy.
- Fail-safe is already correct: **any classify failure → treated as no-spawn
  → pass through**. A dead classifier can never block a chat turn.
- Proven baseline: 16/16 on the spawn-intent battery **on the 27B primary**
  (docs/RESEARCH_FINDINGS §8–11). That record does NOT transfer to a smaller
  model — re-validation is the gate (§6).

⚠️ REVIEW (security): the spawn authorization chain is **one link, not
three**. The original draft claimed identity gate + MCPO key + MCPO RBAC as
independent layers. In fact `_spawn` always presents the single
`OPENBEAST_MCPO_ADMIN_KEY` and never forwards the caller's identity headers
to MCPO — every spawn reaches MCPO *as admin* regardless of who triggered
it. The only per-caller gate is `_spawn_allowed`, which reads a
**client-settable header** with no origin verification: any loopback peer on
:8088 can send `X-OpenWebUI-User-Role: admin`, and
`REQUIRE_IDENTITY=true` only rejects an *absent* header, not a forged one.
This gate is a WebUI-trust boundary, valid solely because :8088 is loopback
on a single-user box; on any multi-tenant host, a trusted front proxy must
strip-and-reinject the role header. The sidecar neither strengthens nor
weakens this — but the design must not pretend the chain is deeper than it
is. Also: the router announces a spawn *after* `_spawn` has executed —
that is post-hoc notice, **not** a mitigation against a captured or
substituted classifier.

## 3. Proposal

Run a second llama-server, CPU-resident, serving
`Qwen3.5-0.8B-UD-Q4_K_XL.gguf` (547 MB, already on disk), loopback-only,
and point ONLY the router's classify at it.

⚠️ REVIEW (both passes): the weight is **NOT yet sha256-pinned** —
`scripts/weights.registry` has no Qwen3.5-0.8B entry (the draft claimed it
did). Pinning is rollout step 0; a launcher that enforces the registry would
fail on day one otherwise.

### Sidecar serve sketch (`scripts/serve-router-classify.sh`, not yet shipped)

```bash
#!/bin/bash
# Router classify sidecar — CPU-only 0.8B, loopback, grammar-constrained JSON.
# MUST route through serve.sh (never exec llama-server directly): serve.sh is
# where the weights.registry pin check and the LLAMA_API_KEY --api-key
# injection live. A direct exec ships an unpinned, keyless listener.
# CUDA_VISIBLE_DEVICES= (empty) forces a pure-CPU ggml init: with -ngl 0
# alone the CUDA build still creates a GPU context (same trick as
# evals/run_eval.py capture_inference_engine_info).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
CUDA_VISIBLE_DEVICES= exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.5-0.8B-UD-Q4_K_XL.gguf" \
  -a "router-classify-0.8b" \
  -ngl 0 \
  -c 8192 -np 2 \
  --threads 8 \
  --port 8081
```

Notes on the numbers (post-review):
- **`-c 8192 -np 2` = 4096 tokens PER SLOT** — llama-server splits total
  context across slots. The draft's `-c 4096` gave 2048/slot, which a
  120-token system prompt + long user turn + 400-token decode budget can
  overflow → mid-generation truncation → `json.loads` failure → silent
  no-spawn. 8192 total is still trivial RAM for a hybrid-state 0.8B.
- `-np 2`: one slot live, one spare. More is waste.
- `--threads 8` is a starting point only — §6.4 measures the real knee,
  including CCD placement (see §8 for why the draft's pinning advice was
  premature).
- RAM: ~0.6 GB weights + state ≈ well under 1.5 GB, against 122 GB on the
  host. VRAM: **zero** — the default keeps its 4.76 GB headroom.
- No `--metrics`: an unauthenticated /metrics on :8081 is a classify-volume
  oracle for any loopback peer. Add it back only if the port is keyed and a
  dashboard actually consumes it.
- The GGUF carries a baked MTP head (`nextn_predict_layers = 1`) — dead RAM
  on a CPU serve without `--spec-type`. Harmless; noted so nobody "fixes" it.
- The bootstrap bridge (`Qwen3-0.6B-Q8_0`, FAST_BOOT) stays untouched.

### Router changes (small, backward-compatible)

```
CLASSIFY_UPSTREAM = os.environ.get("OPENBEAST_ROUTER_CLASSIFY_UPSTREAM", UPSTREAM)
CLASSIFY_KEY      = os.environ.get("OPENBEAST_ROUTER_CLASSIFY_KEY", _LLAMA_KEY)
```

`_classify()` posts to `CLASSIFY_UPSTREAM`; default preserves today's
behavior exactly. Additionally, **when the sidecar path is active**:
- `max_tokens` drops 400 → **160** and `_SCHEMA.task` gains
  `"maxLength": 600` (llama.cpp's json_schema→grammar supports string
  bounds). ⚠️ REVIEW (perf): without these, a legitimate long task
  description at CPU decode speeds runs 5.7–10 s — past any sane timeout —
  and every timeout is a *silent no-spawn*, so the feature would quietly
  degrade to "never spawns on long tasks."
- The classify timeout drops from 60 s to **measured p99 × 3** (§6.2), not
  a guessed constant. A hung sidecar must cost seconds, not a minute.
- User-turn truncation happens router-side **by tokens, not chars**, with a
  keep-head-and-tail policy (first ~200 + last ~1,200 tokens): delegation
  phrasing ("report back", "check back") concentrates at the *end* of long
  prompts, so head-only truncation flips verdicts (§6.1c tests this).

`start.sh` gains the sidecar launch + health probe under the existing
`AGENT_ROUTER=true` block only when `OPENBEAST_ROUTER_CLASSIFY_UPSTREAM` is
set — opt-in squared. It must also: verify :8081 is **free before** launch,
confirm the sidecar's own bind succeeded after, and write a pidfile that
`stop.sh` tears down explicitly and `doctor.sh` port-checks (⚠️ REVIEW
(security): none of that lifecycle exists today, and the draft implied it
did; without the pre-bind check, a malicious local process that squats :8081
*becomes the classifier* — see §7).

## 4. Why THIS model file (and not the others on disk)

| Candidate | Verdict | Reason |
|---|---|---|
| `Qwen3.5-0.8B-UD-Q4_K_XL` (547 MB) | **chosen** | Same instruct weights as the BF16 file at ~⅓ the bytes/token; CPU decode is memory-bandwidth-bound, so smallest-bytes wins. Q4 loss on a grammar-constrained JSON verdict is noise. Caveat: `qwen35` hybrid arch — see the §8 re-prefill finding. |
| `Qwen3.5-0.8B-BF16` (1.5 GB) | rejected | 3× the bandwidth per token for zero benefit here. The 9950X3D HAS native `avx512_bf16`, so the rejection is pure bandwidth math, not instruction support. |
| `Qwen3-0.6B-Q8_0` (610 MB) | **promoted fallback** | Older generation, but a *classic transformer* — partial prompt-cache reuse works, unlike the hybrid 0.8B (§8). If per-call prefill dominates measured latency, this may beat the 0.8B outright. Already registry-pinned (it's the FAST_BOOT bridge). Test it in §6 alongside the 0.8B, not after. |
| A classification fine-tune from HF | deferred | Only if both small models fail the §6 gate. New weights = new supply-chain pin + new validation. |

Quant ≠ fine-tune: the BF16 and Q4 files are the *same model* at different
precision. "Raw vs fine-tuned" is not the axis these files differ on.

## 5. Expected performance (estimates — §6 measures; ⚠️ REVIEW-corrected)

- **Every classify re-prefills the full prompt** (~120-token system + bounded
  user turn ≈ 600–1,600 tokens). The draft assumed prompt-cache reuse; the
  `qwen35` hybrid arch cannot do partial recurrent-state rollback in
  llama.cpp (full-sequence `seq_rm` only), so there is no incremental
  prefill for this file. Prefill is therefore the *dominant, every-call*
  cost and a first-class §6 metric.
- Bandwidth ceiling, corrected for this host: 122 GB usable RAM implies a
  4-DIMM (2DPC) AM5 population → realistic sustained read ~55–70 GB/s (not
  the draft's 80–90), and single-CCD pinning caps at the ~64 GB/s GMI link.
  Decode ceiling ≈ **95–115 tok/s**; real-world estimate 40–70 tok/s before
  the grammar haircut (below).
- Grammar-constrained sampling runs on the same CPU threads as decode
  (unlike the GPU primary, where grammar work overlaps GPU compute):
  expect a 10–30% effective tok/s haircut. §6.2 A/Bs it.
- Decode budget after the router-side caps (§3): ≤160 tokens, typical
  verdict 30–60.
- `enable_thinking: false` is **verified working** for this exact GGUF: the
  embedded chat template branches on the flag (else prefills an empty think
  block), and llama-server parses `chat_template_kwargs.enable_thinking`.
  The strict grammar additionally forces `{` from token 0, so a template
  regression would surface as an accuracy problem, not a latency bomb.
- Honest wall-clock guess for a hint turn: **~1.5–4 s** added in series
  (prefill-dominated), replacing today's 2.7–30 s slot-contention penalty.
  If measurement lands above ~5 s p95, try `--threads-batch 16` (prefill is
  compute-bound and can use both CCDs while decode stays at 8) and the
  0.6B transformer fallback before escalating model size.
- Break-even honesty: on a *non-MTP* primary (6 slots) the contention
  argument weakens. The sidecar's win is largest on the shipped MTP
  default, which is exactly where the router runs today.

## 6. Validation gate (must pass BEFORE any default wiring)

1. **Accuracy** — three sub-gates, run for BOTH the 0.8B and the 0.6B:
   (a) 16/16 on the existing spawn-intent battery (same `_CLASSIFIER_SYS`,
   same schema, sidecar caps applied);
   (b) **zero false-positive spawns** on ≥16 adversarial negatives
   (questions *about* agents, code containing hint words, prompt-injection
   attempts). Asymmetric by design: a false negative costs convenience, a
   false positive spawns an agent;
   (c) the battery repeated **through the truncation path** — long prompts
   with the delegation phrasing at the END, verifying the keep-head-and-tail
   policy preserves verdicts.
2. **Latency**: p50/p95/p99 classify wall over 50 calls; set the router
   timeout to p99 × 3. A/B with and without `response_format` to measure the
   grammar haircut. Assert thinking stayed suppressed on every call
   (`completion_tokens` ≤ budget, no `reasoning_content`).
3. **Interference, both directions**: classify battery while the primary
   decodes a long generation (primary tok/s must not move — the PCIe story
   is arithmetic-bounded: a VRAM-resident 27B streams ~0.6 MB/token of
   logits ≈ 85 MB/s at 140 tok/s vs ~60 GB/s DRAM, <1%; the real channel to
   watch is CPU-thread competition with the primary server's host-side
   sampling/grammar threads). AND classify p95 **under host load** (a
   compile or docker build running) — the `--threads 8` choice claims
   coexistence; prove it.
4. **Placement matrix**: {X3D CCD, non-X3D CCD, unpinned} ×
   {4/8/12 threads} × {prefill, decode} via `llama-bench` (pp512 + tg128)
   plus a STREAM triad for the true bandwidth number. The draft's "pin to
   the non-X3D CCD" guess is likely backwards for decode (96 MB V-cache
   holds ~17% of the weights) and right only for compute-bound prefill —
   measure, don't assume.
5. **Concurrency**: two simultaneous classifies plus a queued third
   (exercises both `-np 2` slots and the queue path).

## 7. Security posture (post-review)

- **The sidecar's output is trusted input to an admin-privileged action.**
  Whoever answers on :8081 controls `{spawn, task, workdir}`, which the
  router forwards to MCPO under its admin key. Therefore the sidecar port
  must be treated like the primary's: **keyed when the stack is keyed**
  (`serve.sh` injects `--api-key` — which is why the sketch routes through
  it), loopback-bound, pre-bind-checked, pidfile-tracked, doctor-monitored.
  The port-squat substitution attack (malicious local process binds :8081
  first and *becomes* the classifier) is strictly stronger than prompt
  injection and is closed by the key + the start.sh pre-bind check.
- **Prompt injection / classifier capture**: the classifier reads raw user
  text, and a smaller model is easier to steer. The §6.1b negative set is
  the evidence gate. The blast radius is bounded by the identity gate ahead
  of the classify (§2 caveat: a *WebUI-trust* boundary, not
  authentication) — and honestly NOT by the announcement, which fires after
  the spawn.
- **Output handling**: strict `json_schema` + `temperature 0` bounds shape;
  `json.loads` failure → no-spawn (existing fail-safe). Never relax
  `strict`; keep the new `maxLength` bounds.
- **Information flow**: the sidecar sees only `_last_user_text` (truncated),
  never the conversation; it logs to the stack's journald scope like every
  llama-server. No new egress (loopback bind).
- **Resource abuse**: hint-spam burns ≤8 CPU threads instead of the GPU
  slot — an improvement. Token-bucket in the router only if abuse is
  observed.
- **Supply chain**: rollout step 0 pins the weight (sha256 + bytes +
  upstream repo, cross-checked against the HF LFS oid like the 08-19 pins).
  Until that row exists, nothing launches.
- **Provenance**: `capture_server_config()` snapshots the *first*
  llama-server process. Evals don't route through the router today; if that
  ever changes, extend it to record every llama-server (tracked here so the
  eval provenance guarantee doesn't silently erode).

## 8. Performance reality checks (post-review)

- **No prompt-cache savings for the 0.8B — plan for full re-prefill every
  call.** llama.cpp cannot partially roll back recurrent/hybrid state
  (`qwen35` arch): prefix reuse works at whole-sequence granularity only,
  and after a classify the slot state sits past the divergence point. This
  single fact reshapes the latency budget (prefill-dominated) and is why
  the 0.6B classic transformer is promoted to co-candidate rather than
  afterthought.
- The bandwidth arithmetic in §5 is a *ceiling sanity check*, not a
  prediction — DeltaNet-layer CPU kernels may be compute-bound, in which
  case the streaming model doesn't even apply. `llama-bench` on this exact
  GGUF is the only number that counts.
- Cold start: ~0.6 GB from page cache loads in well under a second; the
  sidecar starts with the stack and stays resident. Do NOT lazy-start per
  request.
- Failure latency: a *hung* (not dead) sidecar costs the classify timeout
  on every hint turn. Hence p99 × 3, measured — not 60 s, not a guessed
  10 s (the draft's guess was below legitimate long-task decode time and
  would have silently disabled spawns; both numbers were wrong in
  opposite directions, which is the argument for measuring).

## 9. Rollout / rollback

0. **Pin the weight**: `sha256sum` + `stat -c%s` →
   `scripts/weights.registry` row for `Qwen3.5-0.8B-UD-Q4_K_XL.gguf`
   (cross-check the upstream HF LFS oid; do the BF16 sibling while there —
   it's on disk unpinned too).
1. Router env split + sidecar-path caps (`max_tokens 160`, `maxLength`,
   p99-derived timeout) — inert without the env.
2. `serve-router-classify.sh` (through `serve.sh`) + start.sh pre-bind
   check/pidfile + stop.sh teardown + doctor.sh port check.
3. Run the §6 gate on BOTH small models; record results in this doc.
4. Only then: document the recommended config in REFERENCE.md.
5. Rollback = unset `OPENBEAST_ROUTER_CLASSIFY_UPSTREAM`: classify returns
   to the primary. No data, no state, no migration.

## 10. Alternatives considered

- **Status quo** (classify on primary): zero moving parts, keeps the
  measured 2.7–30 s hint-turn penalty on the MTP default. Baseline to beat.
- **GPU sidecar**: faster classify (~100 ms) but spends ~1 GB of the VRAM
  headroom we optimize for, and contends during generation. Wrong resource.
- **BF16 on CPU**: §4 — 3× bandwidth for zero accuracy gain at this task.
- **Bigger CPU model (4–8B)**: accuracy insurance at 3–10× the latency;
  only if both small models fail the gate.
- **No model at all** (regex/heuristic spawn): the prefilter already is the
  heuristic layer; RESEARCH_FINDINGS §8–11 showed pure heuristics either
  over-spawn or under-spawn — the grammar-constrained classify exists
  because of that evidence.
