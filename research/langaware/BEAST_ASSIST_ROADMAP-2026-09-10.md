# beast-assist Improvement Roadmap & Stopping Rule (2026-09-10)

*10-agent research (2 data-miners on our own campaign files, 4 designers, 3 SOTA researchers, 1 synthesis; 56 findings).*

# beast-assist: Synthesis Report — Improvement Roadmap & Stopping Rule

**Chair's verdict up front:** The reactive diagnostics channel is saturated. Two levers remain with any chance of clearing the measurement floor — a write-path guard and a zig-0.16 awareness pack — and one piece of infrastructure (low-churn eval mode) that converts every remaining effect from unprovable to provable. After the Tier-3 mini-A/B, the program hits a model-capability floor and stops, regardless of outcome. beast-assist is drift insurance, not a capability program.

---

## 1. The Ceiling (what the mined taxonomy says is winnable at all)

The denominator: **111 zig-variant failures** across the 8 full-run 2026-09-09/10 result JSONs (`evals/results/eval-qwen3-8-*` and `eval-qwen-27b-*`):

| Bucket | Count | % | Addressable by |
|---|---|---|---|
| Compile-visible (stale 0.16 API / syntax / type) | 56 | 50.5% | Diagnostics (partial, saturated) + packs |
| Path/FileNotFound (file absent at validation) | 28 | 25.2% | Path guard only — diagnostics can never touch this (ON=13 vs OFF=15, identical) |
| Logic/output-mismatch | 23 | 20.7% | Nothing harness-side |
| Timeout/thrash | 4 | 3.6% | Nothing (2× karatsuba burned full 3600s) |

Key structural facts:

- **Qwen3.8 owns 82/111 failures and 26/28 path failures.** Champion Qwen3.6 has 29 total (5–8/run) — already inside the ±5-14 churn floor, half logic-class. **Every remaining harness lever is really a Qwen3.8 lever.**
- **The absolute ceiling of any knowledge+feedback stack was directly measured:** the A′ paired McNemar (champion vs 3.8) was 13-0 all-zig, p<0.001 — 13 net units, +0.32 capability. Discounted for the observed compile→logic migration under treatment (2–3 units/run produce wrong *output* once they compile: 11_bst, 32_dot_product, 38_monte_carlo, 127_aes_keysched), the realizable perfect-stack ceiling is **~9-11 net units, ~+0.25 capability** — at most ~2× one churn-floor SD even executed perfectly.
- **Reactive diagnostics are done.** OFF runs end with ~8.5 compile-visible failures/run; ON runs still end with ~5.5 (42% of ON failures). The residual 22 had the compiler error pushed and the model *still* couldn't fix it in max_iter=10 — it doesn't know the zig 0.16 replacement API even when told the old one is wrong. Marginal gain from pushing the reactive channel harder: **~0, with certainty.**
- **~46% of remaining ON-run failures (~24/52) are addressable by neither packs nor path guard** — wrong algorithms, wrong numerics, comptime/type semantics. That is model capability. The champion sits at that floor already.
- **Battlefield stochasticity warning:** 29/30 zig units flip across just 4 B-arm runs; only 115_fft_f fails all 4 and only bst/trimRight converts deterministically. Single-run rescue narratives are churn until they repeat.

**Combined realistic ceiling if both surviving levers land: recover ~50-55% of Qwen3.8's zig failures (~+8-10 tasks/run pooled), then a hard stop.**

---

## 2. Ranked Roadmap

Ranked by (expected effect ÷ floor) × confidence, cheapest-provable first. Floor context: measured untreated churn is **±5-14 task flips/run** (3×, both models); the mode in §3 shrinks it to ~±0-2.

### R1. Write-path guard (path/FileNotFound bucket) — **the only single lever above the floor's midpoint**
- **What:** In `agents/tools.py`, compare each written path against the task's declared target file; push "task expects /tmp/eval_X/foo.zig, you wrote ./foo.zig" into the same tool-result channel. Precondition: grep transcripts/server logs to confirm wrote-elsewhere dominates over never-wrote (short-elapsed rows like 82_sigmoid 54.7s/57k-tok suggest it does).
- **Expected effect:** up to **+6 tasks/run on Qwen3.8** (stable repeat offenders: 32_dot_product ×3, 82_sigmoid, 61_extgcd, 71_reverse_list); ~+0.5 on champion. Rate identical ON/OFF — fully outside the diagnostics mechanism, 2× the size of the entire measured diagnostics effect.
- **Effort:** ~1 day. **Measurement:** paired runs on the recurring path-failure task set; also reclassifies 2-3 units/run *out* of the Tier-3 pool (61_extgcd, 74_palindrome are path failures masquerading as compile failures — uncorrected they contaminate the pack A/B).

### R2. Zig-0.16 awareness pack (Tier 3) — **the last arm with a chance of clearing detection**
- **What:** Per `docs/LANG_AWARENESS_PLAN.md` §5. Two sections, ≤2,000 tokens: (1) curated old→new rename/idiom map (~500 tok, machine-verified via compile fixtures) covering the 3 observed API families — ArrayList unmanaged transition (~6 instances, both models), std.Io Reader/Writer contract (~6), removed helpers math.abs/fmt.format/trimRight-class (~3); (2) generated signature digest from the installed stdlib, ranked by in-tree reference count, checksum-pinned. Injection: **system prompt via run_eval's existing `--context` plumbing, per-task language-gated — zero runner.py edits**, non-zig requests byte-identical by construction. Task-scoped `pack1-<sha8>` cache component; drift-abort at run start.
- **Pack design is literature-backed:** lead with the rename map (77-97% per-instance conversion at ≥7B — arXiv 2406.09834); signature + usage example format, not prose (+24pt adoption — arXiv 2604.09515); keep it small (context-ignore grows with length — 2406.14497).
- **Expected effect:** **+3-6 net zig units** on 3.8 (residual addressable pool is exactly 6 units, ~4-5 genuinely pack-fixable: 115_fft, 20_priority_queue, 31_is_power_of_two, 62_crt, 82_sigmoid; RustEvo² +13.5% and CodeUpdateArena's 27B-scale discount both point at the same range). ~0 on champion. At/below the suite floor — **provable only via the zig-only paired mini-A/B** (below).
- **Effort:** days. **Measurement:** pre-registered zig-only cells via `--tasks` (32 units, ~15 min/cell, ~1h GPU total): P0 packs-OFF ×2, P1 packs-ON ×2, + one champion guard run. Pooled-replicate paired McNemar; co-primary readout **iterations-to-fix/tokens-to-fix** (GitChameleon shows part of a genuine pack's effect appears as thrash reduction, not new passes — pass-rate alone can read a useful pack as null). Zig-only churn on 3.8 is near zero, so even the low end is detectable. Per-unit audit of every new pass for pack-parroting.

### R3. Diagnostics-quality bundle (one `diag2` era, batched)
Four formatting fixes, shipped together to spend one cache era, not four:
- Strip zig reference-trace noise (measured 56% of payload), keep `note:` lines carrying declared-here signatures — the banked NTT failure had the fix truncated away at `run_eval.py:554`'s cap while shim traces survived.
- "Did you mean" on unknown-member errors, sourced from installed stdlib only (hybrid prefix+difflib matcher; difflib alone misses trimEnd); zig-only — rustc/gcc/shellcheck self-suggest.
- Curated ≤5-entry fix-hint table for the two arity/Io migration idioms.
- Fix the error-count footer (`^\S+:\d+:\d+:\s+error:` anchored, "12 errors (3 shown)").
- **Expected effect:** sub-floor by construction on suite score; honest claim channel is **iterations-to-fix telemetry** (the field: full diagnostics with line+message+remedy wins ablation — arXiv 2601.17670; focused-fix instruction prevents unrelated churn — RUSTASSISTANT). Aider's tree-sitter enclosing-function rendering is the copyable stretch goal (LLMs are bad at bare line numbers).
- **Effort:** hours-to-a-day.

### R4. Bounded fix loop + loop cap (protective + literature's biggest design lever)
- Every large published effect comes from a bounded iterate-to-convergence loop, not one-shot pushes (Idris: 1-shot +4 vs 20-iter loop +31 — arXiv 2602.11481; LLMloop 76.2→90.2% — 2603.23613). Syntax/name errors repair at 66-77%/round, so 2-3 enforced rounds capture most value (2604.10508). Pair with Cursor's ~3-per-file cap to protect the measured zero-harm property and the 20480 reasoning budget.
- **Expected effect:** plausibly pushes the zig effect above the old floor, but capped by non-zig headroom; genuinely new behavior → own era, own mini-A/B. **Effort:** days. Rank below R2 because it overlaps R2's target pool.

### R5. Bash-write diagnostics coverage hole
- Sources written via `cat > f.zig`/`sed -i` bypass write_file and get no diagnostics. Stat-and-check after bash calls. **Effect:** mechanism completeness, bounded by an unmeasured (likely ~0 in evals) frequency — grep transcripts first; ship for agent.sh robustness, skip the A/B. **Effort:** hours.

### R6. Repeated-failure escalation to stdlib-grep steering (TODO item 5)
- Fire only on 2nd consecutive same-fingerprint stdlib-class error. Attacks marathon thrash. Gated behind its own era + mini-A/B; deferred until after R2's verdict. **Effort:** day.

### Rejected (with reasons, all measured)
- **Persistent LSP servers in the harness:** zls is AstGen-only — structurally blind to the exact stale-stdlib class we target; models don't pull optional tools (12 LSP vs 539 Grep calls in a 4-day Claude Code trace); rust-analyzer costs 2-4GB against a budgeted rig. Binaries' customer is opencode. **0 net units expected.**
- **Cross-task error memory:** breaks cache semantics and task independence (the assumption the floor was measured under); measured within-run recurrence ceiling ~1-2 units. Park as interactive-session feature.
- **Unconditional pre-write nudges:** taxes the ~85% of zig tasks that pass; dominated by R2 (system prompt) and the error-triggered form (R3).
- **Go/rust packs, pyright checker:** zero observed stale surface in the suite; the field says effects exist only where compile-failure headroom is high. Build on evidence of a surface, never speculatively.

---

## 3. The Measurement Unlock (low-churn eval mode — infrastructure that multiplies everything)

**This is the highest-leverage build in the queue.** Every roadmap effect is +1 to +8 units against a ±5-14 floor — unprovable at n≤2 forever, until the floor shrinks.

Four measured churn sources, in order:
1. **Unseeded sampling** — `runner.py:293-298` sends only temperature=0.6; llama-server draws a fresh random seed per request. Same task swings 2,116→21,982 completion tokens between identical-condition runs.
2. **-np 6 continuous batching + --jobs 4** — batch composition varies run-to-run; CUDA reductions make *logits* differ in ulps; seed alone cannot fix this. **Half-measure warning: seeding while keeping -np 6/jobs 4 is not worth shipping** — do the full mode or keep the current one.
3. **Wall-clock timeout cliff** — decode-speed-dependent kills flip marathon tasks (124_rkf45: 3600s death vs 2925s pass) and exit=-1 rows are excluded from cache, so the highest-variance rows re-roll every run *by construction*. Also a systematic bias: the DIAG arm's longer rescued runs get differentially timed out.
4. **CPU contention races** — shared /tmp scratch collisions, 30s validation timeouts under load, and the per-process `_DIAG_SLOTS` semaphore silently dropping diagnostics under load — **treatment dose in the ON arm currently varies run-to-run, unmeasured.**

**The mode (design finalized in the findings):** `--low-churn` flag → seed via `OPENBEAST_EVAL_SEED` + pinned sampler chain in extra_body (keep temperature 0.6 — a pinned draw, not greedy; greedy is a different model behavior and breaks era comparability); jobs=1; verify `/props total_slots==1` (serve with `-np 1` passthrough); deterministic termination by completion-token budget with wall timeout as 3× hang-catcher; toolchain warm-up; diagnostic delivery-rate logged into `results.harness`; `.s{seed}` cache component so low-churn rows never contaminate legacy eras; **`--check-determinism` probe** (3 tasks twice, assert identical token counts) validates the stack in ~30 min before any full run.

**Economics:** ~2.2-2.5× wall per run (~9-11h vs ~4h), but the A/B economics invert — 1-2 runs/arm instead of ≥3 that still can't prove +2-5. **Net GPU-hours per decided question go down.** Expected floor: **±0-2** (confirm with one calibration re-roll pair before any arm). Keep low-churn rows off leaderboard.json; use the mode for A/B, regression detection, and paper numbers with the era disclosed.

---

## 4. What the Field Knows (effect sizes, cited)

Our numbers are lawful, not anomalous:

- **Compiler-only feedback is the weakest feedback class:** 49.2% single-round repair vs test 57.9%, mixed 63.6% — and a content-free "it failed" scored 53.1%, i.e. compiler *text* adds ~nothing over knowing failure occurred (FeedbackEval, arXiv 2504.06939). Self-repair gains "often modest… sometimes not present" (ICLR'24, 2306.09896); inference-time iterative feedback "modest gains at best" without training (RLEF, 2410.02089). **Our +2-5 is exactly the predicted size.**
- **Effect size is governed by compile-failure headroom:** Idris (low-resource) baseline 39% → 96% with a 20-iteration compiler loop (+31 tasks), vs no headroom in Python at 90% baseline (2602.11481). **Zig 0.16 staleness is our only headroom — the zig confinement is structural.**
- **Loops beat one-shot pushes:** the single biggest design lever everywhere (2602.11481; LLMloop 2603.23613: pass@10 76.2→90.2%; 2604.10508: syntax/name errors 66-77%/round).
- **Docs/packs stack with diagnostics:** RustEvo² (2503.16922): 56.1% pre-cutoff vs 32.5% post-cutoff APIs, RAG recovers +13.5% — and catches compile-clean *behavioral* changes diagnostics can never see. Idris: docs alone +7-11, additive with the loop.
- **But doc-injection is scale-dependent:** CodeUpdateArena (2407.06249): +31-56pts at frontier, +2.4-8.6pts at 7B; GitChameleon 2.0 (2507.12367): utilization degrades monotonically with size. **Qwen3.8-27B sits between — expect a third to a half of the residue, i.e. +3-6, matching our pool arithmetic independently.**
- **Rename maps are the highest-yield ingredient:** one-line old→new mapping fixes 77-97% at ≥1.3B (2406.09834). Signature+example format beats prose by ~24pts (2604.09515).
- **Anchoring threshold:** sub-~10B models get literally zero information from feedback (feedback−placebo ≈ +0.00; 2607.26117); 32-70B gain +5-23pp. Our 27B reasoning models are on the right side — keeps default-OFF-per-model sensible.
- **Measurement practice:** the field never scores against full-suite churn — score the diagnosable-error subset, use placebo arms, report per-error-class conversion (2602.11481, 2607.26117, 2604.10508). This is precisely the zig-only mini-A/B design.
- **Industry convergence validates the architecture:** push-into-tool-result is universal (Cursor, Claude Code LSP hooks, opencode, aider); pull tools go unused; CLI checkers over persistent servers is opencode's own recommendation; per-language uneven enablement is universal practice — licensing **zig-ON, others opt-in** as the ship shape.

---

## 5. Where It Ends (the stopping rule — adopt verbatim)

**Clause 1 — ship gate (unchanged):** default-ON only on ≥7 net zig rescues, paired same-day McNemar p<0.05, champion-guard clean. Otherwise default-OFF opt-in stands.

**Clause 2 — investment gate (new, self-executing):** before building any further arm, compute its addressable pool from banked failures = (untreated compile-class) − (compile→logic migrations) − (path fails), and require pool ≥ detectable threshold at affordable n (~15 net at n=1 legacy; ~4-7 at paired n=2 under low-churn). **Tier 3 passes today (pool 4-6, marginal). Every arm after Tier 3 fails this arithmetic** — the post-Tier-3 pool (~1-3 units) can never clear detection on this suite.

**Clause 3 — terminal statement:** serving-stack interventions add knowledge, feedback, and brakes; they cannot add reasoning depth. The aes-keysched/gf256/fft wrong-output failures and the champion's rotating residuals are untouched in all 8 cells. **beast-assist's ceiling equals the served model's toolchain-knowledge staleness — an asset that decays to zero at every model refresh** (the 3.6 champion closed all 13 knowledge units at zero engineering cost; any model trained past zig 0.16 re-zeroes the program for free). It is drift insurance. Cap the program at Tier 1 + Tier 3, record the cap in LANG_AWARENESS_PLAN.md as a non-goal, and spend the reasoning-depth budget where it lives: model selection and suite power.

Competing levers now dominate: model swap (13 units, zero engineering), low-churn mode (doubles power for *every* future experiment, not just this one), suite power. Even the token-efficiency case dies in churn (B1a 15.4M vs B0a 13.3M run-level, +16%, dominated by which marathons fire).

---

## 6. Do-Next (three moves, in order)

**1. Build the low-churn eval mode and calibrate it.** Seed+sampler pin in runner.py, `--low-churn` in run_eval, `.s{seed}` cache component, deterministic termination, delivery-rate logging, `--check-determinism` probe. Run the 30-min probe, then one same-day calibration pair to measure the new floor. Everything downstream is unprovable until this exists. (~1 day build + probe.)

**2. Ship the path guard + diag2 formatting bundle as one era.** First grep the treated-run transcripts/server logs to split wrote-elsewhere vs never-wrote; then land the write-path warning and the four formatting fixes together (one `diag2` cache era, not five). This attacks the single biggest bucket (+up to 6/run on 3.8) and de-contaminates the Tier-3 pool by reclassifying the 2-3 path-failures-in-compile-clothing. (~1-2 days.)

**3. Build the zig-0.16 pack and run the pre-registered zig-only mini-A/B — then apply the stopping rule.** Curated rename map + generated signature digest, ≤2,000 tokens, `--context` injection, `pack1-` era, drift-abort. Cells: P0×2 / P1×2 + one champion guard under low-churn (~1h GPU). Ship on Clause 1; regardless of outcome, **stop** — Clause 2's arithmetic says nothing after this can ever be proven on this suite. (~2-3 days build, 1h GPU.)

The honor roll for what got us here: two days of not believing our own headlines. The churn floor was the real finding; everything above is priced against it.