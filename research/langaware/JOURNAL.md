# Language-Awareness Campaign Journal — undertrained-language capability via environment feedback

**Thesis (paper candidate, named by Max 2026-09-09):** an LLM's
capability in a language it was undertrained on (zig: fast-moving
stdlib, post-training-cutoff churn) can be partially recovered at
inference time by the SERVING STACK — pushing compiler diagnostics into
the agent loop — rather than by retraining. "Models breathe through the
stack." This journal backfills the arc from discovery to measurement.
Public design doc: openbeast docs/LANG_AWARENESS_PLAN.md. Eval
infrastructure: openbeast evals/ (v5-fast pinned suite, imputation
scoring, rb/diag cache eras).

---

## 2026-08-20/21 — DISCOVERY: the gap is one language (Phase A)

Qwen3.8-27B (stock + uncensored abliteration) benchmarked against the
Qwen3.6-27B champion on the 291-task v4 suite. Headline: champion 98.7,
3.8 pair ~98.4 tied — and the ENTIRE deficit localizes to zig ports:
**paired McNemar 13–0, p<0.001; the other five languages (python, go,
c, cpp, rust) a dead tie (3–4 discordant)**. Crucial observation: 3.8
solves every one of those 13 base problems in other languages —
staleness, not missing capability. The model writes zig against a
pre-churn mental model (std.mem.trimRight vs trimEnd class) and never
learns what broke. Qwen3.8 measured 2× more verbose than 3.6.

## 2026-09-08 — DESIGN DAY: three tiers, one deleted by review

docs/LANG_AWARENESS_PLAN.md written + adversarially reviewed (4-agent
review log in the doc; PR #36):
- **Tier 1 (push diagnostics):** run the language's checker after every
  write_file/edit_file and push its output into the tool result. The
  trigger lives in the harness because a MEASURED finding says models
  don't call optional tools unprompted.
- **Tier 3 (awareness packs):** version-pinned stdlib digests generated
  from the installed toolchains, injected proactively. Pre-committed
  next arm if Tier 1 under-delivers.
- **LSP sidecar tier: DELETED by review** (hover-on-demand assumes the
  model asks; it doesn't).
- **Measured checker table (§3.2), the load-bearing discovery:** zig
  `ast-check` is AstGen-only and passes stale-stdlib code CLEAN —
  active false assurance; `build-obj` skips unreferenced fns. The
  correct command is `zig build-exe -fno-emit-bin` (full Sema), with
  ast-check only for main-less files. Generalized rule: every
  language's checker must be MEASURED against a stale-stdlib fixture,
  never assumed (fixture pair = admission gate).
- Tier 1 BUILT same day (PR #37, default OFF, env OPENBEAST_DIAGNOSTICS):
  checkers zig/rustc/go(vet)/gcc/g++/py_compile/shellcheck; §3.3
  hardening (timeouts, AS limits, 2-slot semaphore, scratch caches,
  GOTOOLCHAIN=local network-off posture); diag cache-era = toolchain-
  version fingerprint (zig 0.16.0 vs 0.16.1 = different eras).
  Supporting: PR #35 (never cache environmental deaths), PR #42 (warm
  toolchains before cold-CI fixture assertions).
- **A/B experiment §7 pre-registered** after a statistical redesign
  (the draft's "+0.5 capability" bar had ~5% power; replaced with the
  paired test): cells A0/A1 champion off/on, B0/B1 3.8-unc off/on,
  SHIP RULE = ≥7 net zig rescues p<0.05 + champion unharmed + p95
  write latency ≤1s + tripwires clean.
- **Zig loss-leader doctrine (Max):** keep shipping zig-weak models as
  default; fix the language at the stack layer, not model selection.

## 2026-09-09 00:xx–02:26 — ROUND 1 (uncapped): launched, killed

Chain A0→A1→B1 launched post-Phase-II (pristine b10865 serving).
A0 completed (champion diag-OFF, imputed 98.75, pre-swap era);
externally killed mid-A1 ~02:26. Cleanup: orphaned llama-server +
benchmark process; cacheable_result guard audit confirmed no poisoned
rows (last diag cache write 02:23:53; the kill-window validation-
timeout FAIL never cached).

## 2026-09-09 morning — THE BUDGET INTERACTION + ROUND 2 REDESIGN

Phase A' finding: reasoning-capped rows scored ~+0.75 over uncapped —
uncapped thinking bought nothing and lost wall-timeout marathons.
Derived from paired eras + per-request server-log distributions
(n=1852/2885 requests): productive thinking p99.5 ≈ 17.6–17.8k
tokens/request; >20k = 0.2–0.4% and pathology-correlated; the old 4096
cap clipped 7.6% of requests and cost 18 real rescues.
**REASONING_BUDGET=20480 shipped for the whole Qwen family (PR #45)**
with an rb<N> cache-era key component (capped/uncapped rows can never
cross-replay). ROUND 2 DECISION (Max): restart the A/B all-capped —
zig ports are where 3.8 thinks longest, so the diagnostics×budget
interaction is largest exactly on the measured units; and a fresh
capped B0' kills the era confound in the old banked-B0 design while
doubling as the budget validation row.

## 2026-09-09 09:10–12:44 — B0' (capped baseline): budget validated,
## failure taxonomy banked

112-unit v5-fast, diag OFF, capped 20480, b10865: 82/112, imputed
**97.70 vs banked uncapped 97.63** (solve identical 98.19; zig
identical 23/30 failed with SYMMETRIC ±5 cross-era churn) — the cap is
capability-free. Wall/tokens sane; zero marathons (max task 1798s vs
the uncapped era's 3600–4200s deaths). **Zig failure taxonomy (the
treatment's target surface):** stale-stdlib API (mem.trimRight),
syntax-class errors, shadowing-as-error (habit transfer from lenient
languages), 2× FileNotFound (path mistakes — registered as NOT
diag-rescuable), validation timeouts (thrash). NOTE FOR THE PAPER: zig
cross-era churn is ±5, not "near zero" as the §7 power note assumed —
within-era pairing is what makes the McNemar clean.

## 2026-09-09 13:04–17:47 — B1' (treatment): the primary readout

Same 112 units, diag ON (era diag1-28843372), capped, same build/day.
**PRIMARY: 8 rescues − 4 losses = NET +4, exact p=0.388 → SHIP RULE
NOT MET** (bar: net ≥7, p<0.05).
- Rescues: 123_nbody, 136_gf256, 148_convex_hull, 155_tonelli_shanks,
  52_unionfind, 63_det, 73_count_vowels, 74_palindrome — mostly STABLE
  two-era fails.
- Losses: 100_constant_time, 127_aes_keysched, 31_is_power_of_two,
  92_popcount — **all four are cross-era churn units** (each flipped
  between uncapped/capped eras the same morning). The McNemar counts
  them equally; the per-unit audit records the asymmetry.
- **Failure-mode migration** (mechanism evidence independent of
  rescues): 11_bst went compile-death (trimRight) → output-diff; the
  diagnostics demonstrably moved it past the wall they target.
- **First-rescue profile:** nbody B1 PASS 444s/18.5k tok vs B0 FAIL
  566s/23.1k tok — the treatment rescued it FASTER AND CHEAPER
  (diagnostics cut thrash, readout-6 signal).
- Cell level: B1 84/112 > B0 82/112; non-zig guard clean (7v5,
  p=0.77); completion tokens −4% (1.68M vs 1.75M); summed agent wall
  +32% (18.5h vs 14.0h) — the latency price of acting on feedback.
  B1 imputed 97.12 (−0.58 vs B0; solve −0.9 from karatsuba a/e
  non-zig churn drops). Tripwires: one per arm
  (122_gemm_blocked_d / 127_aes_keysched_c) → rerun-once --no-cache.

## 2026-09-09 18:22 — THE MINI RESCUE (structurally-invisible class)

**158_karatsuba_bytes_f — an assumed-failed unit (no reference model
ever passed it; twice-confirmed honest fail in Phase I) — PASSED under
diagnostics** (2099s, capped). B0'-mini failed it (1193s). This is the
measurement the plan called structurally invisible without the minis —
and why the verdict script's B0 filter was patched to include
assumed_failed. Updated primary WITH minis: **+9/−4, NET +5, p=0.27.**
sql_injection mini: concordant fail both arms (non-zig logic task, no
signal — as designed).

## PENDING (as of 19:31): champion guard cells

A0' (champion diag OFF) 65/112 at ~1 min/verdict; A1' next. Then:
tripwire reruns, full verdict table, decision brief. The decision tree
lands in a PRE-REGISTRATION GAP: no-ship (net<7) but Tier-3 trigger
unfired (9 raw rescues ≥ half the 13-unit gap). Options staged for
Max: (a) default-OFF + documented `+assist` opt-in, (b) second
B-replicate (~7h GPU) for McNemar power, (c) Tier-3 awareness-pack
arm. Paper-relevant regardless of ship decision: the effect exists,
its price is wall not tokens, and its failure classes are exactly the
taxonomized compile-visible ones.

## Paper-shaping notes (accumulate here)
- Positioning: capability recovery WITHOUT weight updates, measured
  with paired within-era statistics on a production serving stack;
  contrast retraining/fine-tuning routes and tool-calling routes
  (measured: models don't call optional tools — the trigger must be
  pushed by the harness).
- The reasoning-budget chapter is part of the same story: environment
  shaping (cap + feedback) vs model-internal search (unbounded
  thinking). Budget: capability-free at 5× headroom; feedback: −tokens
  +wall net-positive-rescues.
- Echoes to cite when writing: GSQ's refined-codes-reduce-generated-
  tokens side effect (2604.18556); SchurQuant's "layer-wise
  reconstruction is a loose surrogate" (2608.15567) for why write-time
  signals beat end-only validation; the zls/pacman version-lock and
  edition-axis material (docs/TODO.md items 4–6) as the
  generalization framework.
- Candidate title fragments: "Compiler in the Loop"; "Serving-Stack
  Remediation of Training-Data Staleness"; "The Language the Model
  Forgot".

---

## LEARNINGS SYNTHESIS — 2026-09-09 evening (Max: "jot down now,
## revisit when conclusive"). Written mid-experiment, A1' running;
## every claim carries its measurement. These are the paper'ssix load-
## bearing findings pending the champion guard + any replicates.

### L1. Undertrained-language failure is STALENESS, not missing
### intelligence — and it is precisely taxonomizable.
The models solve the same base problems in other languages (Phase A:
13-0 zig McNemar vs 3-4 ties elsewhere). Measured zig failure classes
(B0' taxonomy): renamed stdlib calls (mem.trimRight→trimEnd class),
syntax churn, shadowing-as-hard-error (habit transfer from lenient
languages), path mistakes, thrash timeouts. Algorithmic knowledge
intact; the model's WORLD MODEL OF THE LANGUAGE is fossilized at
training cutoff. Consequence: unfixable by prompting, addressable by a
fresher source of truth in the loop. (This is the thesis sentence.)

### L2. Environment feedback recovers real capability — including
### capability NO model in the fleet had.
9 rescues under push-diagnostics, headlined by 158_karatsuba_bytes_f:
never passed by any reference model, failed cold by the CHAMPION
diag-OFF the same evening (A0'-mini, 2026-09-09 20:30), passed by the
WEAKER 3.8 with diagnostics (2099s). The stack made a weaker model do
what the stronger model cannot do unaided. Mechanism visible even
without rescue: 11_bst migrated compile-death → honest logic failure
(the targeted wall came down; a different, legitimate problem remains).

### L3. Feedback inverts the economics: costs WALL, saves TOKENS.
B1' vs B0': +32% summed agent wall, −4% completion tokens, MORE total
passes (84 vs 82). Without feedback the model burns tokens polishing
code that can never compile (nbody: 23.1k tok to FAIL; 18.5k tok to
PASS with diagnostics, and 122s faster). Feedback converts speculation
into iteration. Deployment reading: latency price, quality+efficiency
gain — relevant to always-on/long-horizon jobs.

### L4. Task-level churn is LARGER than assumed and eats single-run
### experiments (the methodological finding).
Zig units flip at ±5 per run with NO intervention (uncapped↔capped
same-day cross-era: 5v5 symmetric). The A/B's +9/−4 (net +5) is
mechanistically persuasive — rescues are stable multi-era fails,
ALL four losses are known coin-flippers — yet p=0.27, because one run
cannot separate a real +5 from a ±5 churn floor. The plan's power
note assumed near-zero churn; it was wrong by the size of the effect
itself. LAW: measure the churn floor BEFORE designing the experiment
on it; pre-register churn-stratified audits; expect replicates for
effects of order the churn.

### L5. Bounded thinking is free — the model does not know when to
### stop, and the environment can know for it.
Productive per-request thinking tops out ~p99.5 = 16.6–17.8k tokens
across THREE models (3.6 champion, 3.8 stock, 3.8 uncensored); beyond
~20k only pathology was measured (0.2–0.4% of requests; runaway
65–77k-token failed tasks; 3600–4200s wall-timeouts). The 20480 cap:
imputed 97.70 vs uncapped 97.63, solve identical, zig identical,
champion paired-clean (3v5 on 46 units) — zero capability cost,
marathon class eliminated. Same doctrine as L2 from the other side:
the stack supplies judgment the weights lack — feedback when wrong,
brakes when it cannot stop. (Full derivation: openbeast PR #45.)

### L6. The fix's scope equals its theory — which is what makes it
### trustworthy and extensible.
Diagnostics helped nowhere they shouldn't (non-zig guard 7v5, p=0.77;
no collateral harm) and failed exactly where predicted (logic errors,
FileNotFound path mistakes — registered non-rescuable in advance). A
mechanism that works only where its theory says is one you can extend
deliberately: the language-pack registry (drop-in languages), edition
axis (C++), doc-site escalation for what compilers cannot say
(openbeast docs/TODO.md language-awareness queue items 4–6).

### Open at time of writing
Champion guard (A1', running), p95 write-latency readout, the ship
decision (pre-registration gap: no-ship by net<7/p=0.27, Tier-3
trigger unfired at 9 raw rescues ≥ half-gap). Candidate next moves:
default-OFF + documented +assist; B-replicate for power; Tier-3
awareness-pack arm. Whatever ships, L1–L6 stand on today's data.

## 2026-09-09 22:20 — A1' (champion + diagnostics): THE HEADLINE CELL

The guard cell outperformed the experiment it was guarding.
**Champion diag-ON: 100/112, imputed 98.92 — the highest capability
score ever recorded on this stack, above the champion's own historic
98.7 crown — at −2% tokens (0.96M vs 0.98M) and −5% wall (7.0h vs
7.4h) vs its diag-OFF twin.** Flips +12/−5 (p=0.14), zig +6/−3
(p=0.51). Ship-rule guard: PASSES by letter (both p>0.05) and by
intent (direction positive).

### L7 (provisional, needs replicate): feedback efficiency SCALES WITH
### model quality — the opposite of the compensatory assumption.
The design assumed diagnostics compensate weakness (3.8's zig gap).
Measured: the STRONGER model converted feedback better — champion
+12/−5 at negative cost (fewer, more decisive iterations); 3.8 +11/−9
overall at +32% wall. The champion landed 11_bst_f (the trimRight
poster child 3.8 could only migrate to a logic failure) and
158_karatsuba_bytes_c. Interpretation candidate: acting on a compiler
message requires capability headroom — feedback is a multiplier on
skill, not a substitute for it. If it survives replication, this
reframes the feature from "remediation for weak languages" to
"amplifier for all agentic coding" — and predicts the benefit GROWS
with future model quality (future-proof, not stopgap).

### Cross-arm synthesis (pre-verdict): consistent direction, n=1 power
B arm net +5 (p=0.27 w/ mini), A arm net +7 (p=0.14) — same sign on
two models, neither significant alone against the measured churn
floor (L4). Naive pooled zig discordants +15/−7 (p≈0.13, post-hoc,
labeled as such). The statistical portrait of a real ~+5/model/run
effect sitting just under n=1 resolving power.

### NAMING LOCKED (Max, 2026-09-09 ~23:00): the feature is BEAST-ASSIST
Product name: beast-assist (repo idiom: beast-gate/-slot/-rank).
Mechanism term (docs+paper): push-diagnostics. Era flag: +assist
(unchanged). Max: "this feature will soon be integral to openbeast."
Env alias BEAST_ASSIST=1 (alongside OPENBEAST_DIAGNOSTICS) + docs
rename ride the ship-decision PR — no live-harness rename mid-campaign.

## 2026-09-09 ~22:50 — ROUND-2 FORMAL VERDICT (all clauses resolved)

ab_verdict (patched, minis merged) confirms: PRIMARY zig B net +5
(9v4), p=0.2668 → ship rule NOT met. Non-zig guard clean (7v5,
p=0.774). Champion guard PASSES positive (+13/−5, p=0.096; zig +7/−3,
p=0.344). **TRIPWIRES CLEAN after rerun-once — both arms' single fails
(122_gemm_blocked_d, 127_aes_keysched_c) PASSED on --no-cache retry**
(churn, not saturation breaks). Cross-model karatsuba convergence:
158_karatsuba_bytes_f passed by BOTH models with diagnostics (champion
1019s, 3.8 2099s), by NEITHER without — the suite's never-passed unit.
Cell imputed: A0 97.43 / A1 98.92 (stack record) / B0 97.70 / B1 97.12.
Decision: pre-registration gap → Max authorized the overnight B-pair
replicate (--no-cache, fresh measurement); combined 2-run McNemar in
the morning decides among (1) default-ON 3.8-family ship, (2) A-pair
replicate, (3) default-OFF + Tier-3 arm. p95-latency clause remains
uninstrumented (cell-wall only: B +32%, A −5%) — instrumentation rides
the ship PR.

## 2026-09-10 03:16 — B0'' REPLICATE BASELINE: the TRUE within-era
## churn floor, and it is humbling

First direct same-era zero-intervention replicate pair (B0' vs B0'',
identical config, --no-cache): **21 total flips (9v12), 11 zig flips
(4v7), imputed 97.70 → 98.45 (+0.75) from pure run-to-run variance.**
The cross-era ±5 estimate UNDERSTATED the floor.

**Direct hit on last night's attribution:** 5 of B1''s 8 in-suite zig
"rescues" (136_gf256_f, 148_convex_hull_f, 52_unionfind_f,
73_count_vowels_f, 51_toposort_f) passed in the run-2 DIAG-OFF
baseline — the "stable multi-era fails" label did not survive one
within-era replicate. Even 152_chase_lev_deque (twice-honest marathon
fail) passed untreated. L4 sharpened: "stable" at n=2 eras ≠ stable at
n=2 runs; attribution stories about individual units are UNRELIABLE at
this churn level — only paired within-run McNemars + replicate
aggregation count.

Implications for the morning verdict: the B-arm's round-2 net +5 is
now at genuine risk of being churn. The test that decides: B1''-vs-B0''
paired (run 2's own contrast) + combined 2-run discordants. What still
stands apart pending the B0''-mini (running now): 158_karatsuba_bytes_f
— 0-of-2 diag-OFF, 2-of-2 diag-ON across two MODELS. If the diag-OFF
mini replicate passes it, even that collapses; if it fails again
(making 0-of-3), the cross-model convergence remains the single
strongest datapoint. The champion's +1.49 imputed gain also inherits
churn risk (+0.75 measured swing on one model pair). This is the
replicate doing exactly what Max ordered it to do.

## 2026-09-10 03:35 — THE HERO UNIT FALLS: karatsuba_f passes UNTREATED

B0''-mini: **158_karatsuba_bytes_f PASSED diag-OFF (1114.9s).** The
score is now 1-of-3 diag-OFF vs 2-of-2 diag-ON in the current era —
directionally positive, statistically nothing. The "never-passed"
framing was true of the OLD eras (uncapped; capped-4096); under
capped-20480 on b10865 the unit is evidently within untreated reach.
NEW ALTERNATIVE HYPOTHESIS (log it before the morning analysis): the
REASONING CAP may be the actual unlock on marathon-class zig units —
capped thinking prevents the thrash-spiral that killed prior attempts
— and diagnostics received credit the budget earned. Testable
post-hoc: prior-era karatsuba attempts died at 0-token/wall-timeout
(thrash class); current-era attempts converge ~17-35 min regardless of
arm. CONSEQUENCE: no hero units remain. The beast-assist case now
rests entirely on paired within-run McNemars aggregated across
replicates — which is where it always should have rested (L4 final
form). sql_injection: failed again (3-of-3 concordant fail, stable).

## 2026-09-10 07:49 — B1'' AND THE COMBINED VERDICT: not demonstrable
## above the churn floor at n=2

Run-2 paired zig: **net +2 (8v6), p=0.79** — null on its own. Combined
2-run zig discordants: **+17/−10, NET +7, p=0.248** — the ship bar
(p<0.05) is NOT met, and at the observed 1.7:1 discordant ratio ~3-4
more replicate pairs would be needed IF the ratio held. Rescue/loss
sets churn wildly between runs (136_gf256_f: rescue in run 1, LOSS in
run 2). Only TWO units rescued in both runs: 123_nbody_f,
155_tonelli_shanks_f — the honest repeat-rescue list.

What remains consistently positive: EVERY diag-ON cell beat its
diag-OFF twin on total passes (B1' 84>82, B1'' 91>85, A1 100>93) —
direction unanimous, size within churn. Non-zig guards clean both
runs. B1'' imputed 98.06 vs B0'' 98.45 (imputation weighting flips the
raw-count direction — another churn symptom). Champion arm (+13/−5,
p=0.096) remains UN-replicated: the strongest open signal.

**The honest scientific statement for the paper: push-diagnostics
produces a consistent small positive direction (~+2 to +5 net
zig/run, +2 to +7 total passes/cell) that two replicates cannot
distinguish from the ±5-11 within-era churn floor. The 13-0 Phase A
gap it targeted was measured across eras with different budgets — the
churn floor and the budget change account for an unknown fraction of
it. Effect exists in direction; magnitude unproven; harm excluded.**

## 2026-09-10 07:59 — CHAIN COMPLETE, TABLE SEALED

B1''-mini: karatsuba_f FAILED under diagnostics — while B0'' passed it
untreated. The unit's full record in-era: diag-OFF 2-of-3, diag-ON
2-of-3. Perfect churn; cap-unlock hypothesis strengthened (it passes
~half the time in the current era REGARDLESS of arm — prior eras 0%).
sql_injection: 4-of-4 concordant fail (stable control, as designed).

**FINAL SEALED COMBINED (2 runs, minis included): zig +17/−11,
NET +6, p=0.345.** The n=2 verdict is definitive for what it is: the
primary effect is not demonstrable above the churn floor. Direction
unanimous (all 3 diag-ON cells > their OFF twins on total passes),
harm excluded, magnitude unproven. Champion pair un-replicated
(strongest open signal). Next arms per pre-commitment + council
recommendation: beast-assist ships default-OFF documented opt-in;
A-pair replicate (~4h) settles L7; Tier-3 awareness packs take the
zig fight (attacks staleness directly, not via iteration); T1.17
claims the GPU after the A-pair.

## 2026-09-10 — SOTA tool review finds a bug INSIDE the treatment:
## zig has_main substring false-positive

The 9-agent tool review (72 findings, scratch/TOOLS_SOTA_REVIEW-
2026-09-10.md) caught: `"fn main" in src` (tools.py has_main check)
false-positives on `fn mainLoop`/comments → selects `zig build-exe`
on main-less library code → FABRICATED "no member named main" errors
pushed into the ON arms. Implication for rounds 1-2: the treatment
carried self-inflicted noise — false diagnostics on some zig units —
meaning the measured effect is a LOWER BOUND on a correctly-
implemented beast-assist. May partially explain ON-arm zig losses.
DECISION (era discipline): NOT hotfixed mid-A-pair — A1'' must run
the same harness as A1' or the champion replicate is uninterpretable.
Fix (`\bpub\s+fn\s+main\s*\(` + unit test) ships in the next-era
tools PR with the review's other fixes, and the fixed checker
becomes part of the Tier-3-era baseline. Lesson for the paper:
instrument-quality bugs are treatment-attenuating, another reason
single-run effects read small.

## 2026-09-10 10:04 — A0'' (untreated champion): the 98.92 "record"
## dissolves into the churn floor

A0'' — diag OFF, nothing but a re-roll — scored **99/112, imputed
98.89**, statistically indistinguishable from A1''s celebrated 98.92
(diag ON). Champion within-era untreated churn A0'→A0'': 22 flips
(8v14, zig 6v6). The kill shot: **10-11 of A1''s 12 round-2 "diagnostic
gains" (constant_time, bst, gemm_e, quantum_superposition, gf256_e,
pollard_rho, persistent_bst, tonelli, karatsuba_c, count_vowels,
palindrome_b) just passed in this UNTREATED run.** Round-2's A0' was a
low-side draw; everything after regressed toward a ~98.9 era mean that
has nothing to do with diagnostics. L7 ("feedback scales with skill")
is on life support — its evidence was this exact gain list. The only
remaining valid contrast is A1''-vs-A0'' (same-day paired, running
next). Methodological close: L4 now measured on BOTH models — champion
churn (±8-14) is as fat as 3.8's. Any future tool-effect experiment on
this suite needs paired same-day arms as the MINIMUM unit, replicates
as the default, and effect sizes ≥ ~15 net units to clear the floor at
n=1.

## 2026-09-10 12:23 — A1'' SEALS THE CAMPAIGN: champion arm flat-null,
## L7 dead

Final champion paired (same-day, same-era): **A1'' 97/112 vs A0''
99/112 — net −2 (+9/−11), p=0.824; zig net +1, p=1.000. Tokens +8%,
wall +13%** (round-2's "cheaper AND better" was ALSO a churn draw —
the cost story flipped with the capability story). **L7 is dead**: the
one-run "feedback scales with skill" finding does not survive its
replicate; the champion converts diagnostics no better than churn.
Champion imputed sequence tells the whole tale: A0' 97.43 → A1' 98.92
→ A0'' 98.89 → A1'' 98.12 — a ±0.75 band with no treatment structure.

CAMPAIGN FINAL (7 paired cells, 2 models, 3 untreated replicate
pairs): mechanism replicated (bst_f converts every treated run, never
untreated; failure-mode migration; compile-visible-error targeting);
capability effect ≈ +2-5 net/run on the TARGETED language only,
inside a measured ±5-14 churn floor; zero harm anywhere; token effect
small and direction-inconsistent at champion scale (−2-4% on 3.8,
+8% this champion run). VERDICT STANDS: default-OFF opt-in was the
right ship; decisive proof assigned to Tier-3 (bigger expected
effect), the fixed checker (PR #52), and a low-churn eval mode.

## 2026-09-10 24:00 — DAY CLOSED AT MAX'S ORDER (system rest)
Session-end state: beast-assist shipped (v1.2.0) + honestly documented
at every level; R1 path guard merged (#55); low-churn greedy mode
merged (#56, unpulled-then-pulled post-sweep); hardening era live
(#52); roadmap + stopping rule banked. Next langaware work in order:
re-measure the churn floor ON GREEDY MODE (cheap, calibrates
everything), then the Tier-3 zig-only mini-A/B on that floor with the
FIXED checker (post-#52) as baseline. beast-rank campaign holds the
GPU claim first (E32 capability pair on resume).

## 2026-09-11 11:35 — CAMPAIGN RELAUNCHED; greedy floor queued; R2/R3 builds started
Max: "go, launch the E32 chain and all our remaining tasks to complete
in order." GPU order: E32 capability rows (np1) → FineWeb control → IQ2
pair → IQ stability repro → **greedy churn-floor pair** (scratch/
greedy_floor.sh: uncensored 3.8 Q5, --greedy, capped 20480, --jobs 4,
run 2 --no-cache; verdict = e32_cap_verdict.py pairwise on the two rows
→ scratch/greedy-floor-verdict.txt). That pair calibrates the floor
every later arm is powered against (roadmap §3/§6 move 1's second half).
Meanwhile, in isolated worktrees (era discipline — main tree untouched
until the GPU queue drains): R3 diag2 bundle (trace strip, did-you-mean,
verified migration hints, anchored error count; cache component diag1→
diag2) and R2 Tier-3 zig-0.16 awareness pack (curated map verified by
compile fixtures + generated signature digest, ≤2k tokens, BEAST_PACKS=1,
pack1-<sha8> era, --packs flag) + the pre-registered zig-only mini-A/B
harness (P0×2/P1×2 + champion guard, greedy). Sequence after the floor
lands: merge R3 → merge R2 → run tier3_zig_ab.sh (~1h GPU) → verdict →
apply the stopping rule (Clause 1 ship gate; Clause 2 says stop after).

## 2026-09-17 20:37 — Tier-3 zig-0.16 awareness pack: VERDICT SHIP (net +13, p=0.019)

Pre-registered zig-only mini-A/B (scratch/tier3_zig_ab.sh; manifest
scratch/tier3_cells-20260917.txt; verdict scratch/tier3-verdict.txt), greedy,
jobs=4, 30 zig units of v5-fast, era 3b7c2adb8da7968d, pack sha8 5cdf4b64.
Campaign resumed 14:38 after Max's 09-15 hand-back; P0a replayed from cache
(29/30), everything else measured live.

| cell | model | packs | pass |
|---|---|---|---|
| P0a / P0b | Qwen3.8-27B-Uncensored Q5_K_M | off | 10 / 9 |
| P1a / P1b | same | on | 15 / 17 |
| C0 / C1 | champion Qwen3.6-27B Q5_K_XL | off / on | 21 / 14 |

R1 pooled McNemar: rescues 20, regressions 7, **net +13, p = 0.0192**.
R2 co-primary (n=12 passed in both arms): iterations-to-fix **−2.92, sign p = 0.008**;
completion tokens-to-fix −3221 (p = 0.39, n.s.); all-unit completion −1007,
prompt overhead −11118/unit (the pack is in the system prompt, so the
prompt-token column is not the pack's cost — read it as context reuse).
R3 guard on the champion: net −7, p = 0.167 → CLEAN by the pre-registered
rule (p > 0.05). **Honest caveat:** the DIRECTION on the champion is negative
(13 regressions vs 6 rescues, one replicate only). The pack helps the model
it was tuned against and is neutral-to-harmful on Qwen3.6 at n=1; do not
enable it rig-wide by default, gate it per model (the deployment allow list
already does) and give the champion its own replicate before any claim.
R4 audit: rescued in EVERY replicate — 122_gemm_blocked_e, 54_astar_f,
63_det_f, 92_popcount_f. 127_aes_keysched_f regressed in both.

Clause 2 stopping rule: this was the last arm on this suite — STOP.
Next: beast-lang P4 escalation A/B (draft #90; diagnostics ON in both arms,
--escalate the only difference) once the greedy floor + IQ2 stages finish.
Greedy floor started 20:37.

**22:05 addendum — campaign paused by Max; the floor did NOT land.** Greedy
floor run 1 was SIGKILLed at 21:12:04 by something outside the campaign (no
OOM in the journal), its llama-server kept the VRAM, run 2 died on
"unable to allocate CUDA0 buffer", so `greedy-floor-verdict.txt` says
"FEWER THAN 2 FULL GREEDY ROWS". 21 units of run 1 are in the eval cache.
The Tier-3 +13 therefore has no churn floor yet from THIS era; the 09-09
cross-era churn of ±5 on zig is the only calibration on file, and +13 with
both replicates in the same direction sits well outside it. Read SHIP as
provisional-strong until the floor is measured.

## 2026-09-29 — Tier-3 RE-AUDIT: the SHIP does not survive clean rows → UNRESOLVED

The adversarial review (docs/reviews/FULL-REVIEW-2026-09-29.md, findings
eval-harness-1, tools-mcp-security-1 and research-stats-1/2/3) found two
kinds of row the model never produced in the 09-17 cells. Record:
openbeast `scratch/tier3-verdict-reaudit-2026-09-29.txt`. No GPU was used.

- **Dead server.** llama-server died mid-unit. The agent logged
  "Connection error." on every remaining iteration, and the runner still
  exited 0 with its tokens, so the harness banked a model FAIL. P0a has 7
  such rows (148, 155, 159, 19, 51, 62, 92 _f), all replayed from the
  09-15 12:33 server death. P1a has 4 (62, 71, 74, 82), C0 has 4 and C1
  has 4.
- **EAGAIN.** 10 C1 rows died in validation on fork/thread exhaustion
  (uid-wide RLIMIT_NPROC=2048 with the desktop's threads counted). 8 of
  those passed in C0.

| read | b | c | net | p | Clause 1 |
|---|---|---|---|---|---|
| as registered | 20 | 7 | +13 | 0.0192 | SHIP |
| clean (unit dropped from its pair) | 17 | 7 | +10 | 0.0639 | **NO-SHIP** |
| sensitivity: keep 62_crt_f (lost only 2 iterations) | 18 | 7 | +11 | 0.0433 | SHIP |

The verdict is **UNRESOLVED**. Direction holds in both replicates, and
iterations-to-fix is still −3.18 (sign p = 0.008). Pair 1 is untouched
(11/3, p = 0.057); pair 0 shrinks to 20 units (6/4). The call hangs on a
single row.

**Champion guard.** 6/13 raw becomes 4/1 on 14 clean units (EAGAIN-only
exclusion gives 6/5). The "negative direction" in the 09-17 entry above,
and the "gate it per model, off for the champion" decision drawn from it,
were mostly EAGAIN artifact. The guard is uninformative, not negative.

**Greedy churn is not ~0.** The same-config replicates flipped 9/30
(P0a/P0b) and 8/30 (P1a/P1b) zig units, about 30%, because `--jobs 4`
against the `-np 6 --kv-unified` server is not single-slot. The design's
power premise was wrong. The exact test is still valid.

**In-sample.** The pack's curated section was written against failures on
the same 30 zig units. A frozen, firewalled held-out zig set is proposed in
LANG_AWARENESS_PLAN §5 (20 new units + 158_karatsuba_bytes_f).

**Cache.** 74 contaminated entries across all eras (29 in the current era
3b7c2adb8da7968d) were MOVED to `evals/cache-quarantine-2026-09-29/`, with
MANIFEST.txt. The IQ3 pair (E32) is clean under the fixed classifier:
+12, p = 0.012, unchanged.

**Next (Max's go).** Land the NPROC fix and the connection-error fix (the
era rolls). Then `FRESH=1 scratch/tier3_zig_ab.sh`: all six cells live,
~7 GPU-h, optionally with the held-out set (+~1.5 h). Do not patch
new-era rows into the 09-17 cells. Wire the pack into production only if
the rerun ships.

**Addendum (same day, review of the fix).** The row guard now also voids a
row on any live zero-token fail with a normal exit (the dead-server shape
that api_errors() cannot pin by tokens); none of the 09-17 cells or the
IQ3 pair has one, so every number above stands. `greedy_floor.sh
--single-slot` refuses to start while anything serves :8080 and requires
`total_slots == 1` from its own server — run it with the stack down.
`e32_cap_verdict.py` exits 1 on a refused or INVALID verdict.

**Addendum 2 (round 2 of the review fixes).** The row guard also reads the
harness's own infra reasons from the 2026-09-29 run_eval (`server_error`,
`env_error`, `low_disk`) and a row's `api_errors` count, so rows from the new
harness no longer need the agent-log match to be voided. `greedy_floor.sh
--single-slot` now refuses any answer on the port (a loading 503 too), falls
back to `/slots` when `/props` has no `total_slots`, and re-checks one slot
before run 2. Both IQ3 rows re-checked clean under it; nothing above moves.

## 2026-09-30 — Tier-3 FRESH rerun: SHIP (settles the 09-29 re-audit)

Run 2026-09-29 21:04 → 09-30 05:11 on the rig, under the GPU lease, era `b5596c660b5ab819` (post-#102
harness: server deaths and fork/thread EAGAIN never banked as FAILs). FRESH=1: every cell `--no-cache`.
Manifest: openbeast `scratch/tier3_cells-fresh-20260929.txt`; verdict `scratch/tier3-verdict-fresh-20260930.txt`.
All six cells clean — 0 timeouts, 0 infrastructure rows.

| Cell | Model | Pack | Passed |
|---|---|---|---|
| P0a / P0b | Qwen3.8-27B-Unc Q5_K_M | off | 10 / 11 of 30 |
| P1a / P1b | Qwen3.8-27B-Unc Q5_K_M | on | 24 / 21 of 30 |
| C0 / C1 | Qwen3.6-27B Q5_K_XL (champion) | off / on | 23 / 28 of 30 |

- R1 primary: pairs 14/0 and 12/2 → pooled b=26, c=2, net +24, exact McNemar p < 0.0001. Rescued in both
  replicates: 115_fft_f, 11_bst_f, 123_nbody_f, 148_convex_hull_f, 20_priority_queue_f, 61_extgcd_f, 92_popcount_f.
- R2: completion tokens-to-fix −4,681 (sign p = 0.064); iterations-to-fix −1.8 (p = 0.55); all-unit completion
  tokens −5,041; prompt tokens −5,189/unit.
- R3 champion guard: 7/2, net +5, p = 0.18 → CLEAN and positive. The 09-17 "negative on the champion" was the
  uid-global RLIMIT_NPROC EAGAIN artifact; the per-model gating rationale is withdrawn.
- Same-config churn P0a↔P0b 9/30 (~30%), matching 09-17 — the replicate design was needed.
- VERDICT: SHIP (Clause 1). Clause 2: last arm on this suite → STOP.
- Caveat unchanged: in-sample (pack written against these units' failures); held-out check (LANG_AWARENESS_PLAN §5)
  not yet run. Production wiring left to Max.

Doctrine: the contaminated 09-17 run understated the effect (+13 with 7 regressions); cleaning the harness
before re-measuring, not re-analysing dirty rows, is what produced a decisive answer.

## 2026-09-30 — CORRECTION to the fresh-rerun entry: 2 wall timeouts, not 0 (SHIP unchanged)

An independent double-pass review recomputed the fresh rerun from the six results files and all 180 agent
logs. Every headline number reproduces. One claim in the entry above does not: **"0 timeouts" is false.**

- Two rows hit the harness wall timeout (`agent_exit_code` −1, 2400.1 s, tokens recorded as 0, iterations
  None), and both **passed**:
  - P1a `65_miller_rabin_f`, a pair-0 rescue.
  - C1 `27_brainfuck_interpreter_f`, which C0 passed too, so the guard is unaffected.
- Both passes are real:
  - The model's own check had already succeeded at iteration 10 (`DIFF_OK: outputs match`).
  - The iteration-11 request then hung for exactly 20 minutes, and the 2400 s wall killed the agent.
  - The files on disk validated.
  - Why the requests hung is **unverified**: a server stall or a runaway generation.
- Why the classifier missed them: `row_validity.audit()` walked only failed rows, so a timed-out PASS was
  invisible. `api_errors()` skips (0,0)-token rows and indexes only finished logs.
- **Sensitivity** (timed-out rescue dropped): b=25, c=2, **net +23, p = 5.6e-6**. The guard is still 7/2,
  p = 0.18. **SHIP stands.**
- **The R2 prompt figure was biased.** run_eval writes 0 tokens for a timeout, and `paired()` filtered only
  None, so a recorded 0 counted as a real value. The −5,189/unit above is **−2,163/unit** on the 59 pairs with
  recorded tokens. The median is **+23,399**, so it is not a saving. It is a per-unit total dominated by the
  iteration count, not the pack's per-request overhead. All-unit completion Δ moves from −5,041 to −4,913.
  Tokens-to-fix and iterations-to-fix don't change, because the timeout row was never a both-pass unit.
- **R4 pack-parroting review** (pre-registered, previously left open): done, no parroting.
  - The longest verbatim pack substring in any solution is ≤46 chars in P0 and P1 alike.
    The common match is the `main(init: std.process.Init)` signature the task prompt itself requires.
  - The effect is idiom adoption: `= .empty` ArrayList in 14 units per P1 cell vs 3 per P0 cell.
  - `20_priority_queue_f` was rescued in both replicates without `std.PriorityQueue`.
- Provenance caveat: every cell ran at 9fe5de96 with a dirty tree, and the diff can't be recovered.
- Tooling fixed in openbeast (branch `fix/tier3-record-timeouts`):
  - `tier3_verdict.py` blanks token and iteration fields on timeout or zero-token rows, lists every timeout,
    prints the sensitivity read, warns on cross-commit or cross-engine cells, and adds `--heldout`. The
    held-out arm is read alone with the pre-registered one-sided sign test, never pooled into R1.
  - `row_validity.py` lists every wall timeout, passed or failed.
  - The record gained a dated correction block. The original text is kept.

Doctrine: a record's "0 X" claim needs a classifier that can see X in **every** row, passes included. A
0-token row is an unmeasured value, not a zero, and every mean has to be told that.
