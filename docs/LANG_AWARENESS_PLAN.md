# Language awareness for the harness — push-diagnostics + awareness packs (design, 2026-09-08)

> **NAMING (locked by Max 2026-09-09): the shipped feature is BEAST-ASSIST** (`BEAST_ASSIST=1`, alias of `OPENBEAST_DIAGNOSTICS=1`); "push-diagnostics" remains the mechanism term in this doc. A/B rounds 1–2 + B-replicate verdict (2026-09-10): direction unanimous, magnitude within churn at n=2, harm excluded → ships default-OFF documented opt-in; Tier 3 is the next zig arm. Full record: research langaware journal.

> **Status: Tier 1 BUILT (PR #37, merge held until the current eval era is
> banked — §6.2); A/B pending.** Everything else is design. The A/B
> experiment in §7 gates every build phase and is queued behind the
> 2026-09-08 Phase A′ results work. A 4-agent parallel adversarial review
> (security / eval-integrity+statistics / systems-with-measurements /
> product-scope) was applied 2026-09-08; §10 records the verdicts and
> every fold. The review DELETED the original Tier 2 (LSP sidecar) and
> rewrote the checker table with measured commands.

## 0. The one-paragraph version

Make language staleness *visible inside agent loops*: every
`write_file`/`edit_file` of a source file returns real toolchain
diagnostics in the tool result, pushed, never dependent on the model
remembering to verify (**Tier 1**); version-pinned stdlib digests
generated from the installed toolchains fix staleness *proactively*
(**Tier 3**). Interactive editing already has a better answer — opencode's
built-in client-side LSP, which needs only binaries installed (§8 step 0,
ungated, and honestly the largest user-visible win in this document).
This is not "LSP for models" — it is batch compiler feedback plus
knowledge injection; fluency comes from the model, currency from the
packs, and editor-grade LSP from opencode. Tier 1's effect is
**hypothesized**, not measured — measuring it is what §7 is for.

## 1. Evidence — why this feature, why now

Measured 2026-09-08 (Phase A′ + paired analysis):

- **The Qwen3.6→3.8 capability gap is 100% zig.** Paired McNemar,
  champion vs 3.8-uncensored: zig 13–0 (p<0.001); the other 260 units
  3–4 (p=1.0, tie). Same-model rerun churn is large (23 discordant
  units/291) but symmetric — 13–0 is real signal.
- **The failures are stdlib drift, not missing knowledge.** Every zig
  unit 3.8 fails it solves in another language (`problem_solving` tied
  99.1). Recorded errors: `std.Io.Writer` moves, `ascii` member renames,
  `@intCast` inference changes — zig 0.16 churn postdating training data.
- **Access is not the gap — discipline is.** Agents already hold `bash`
  and the rig has zig/rustc/go/gcc on PATH; they fail anyway (local
  models don't reliably *choose* to verify; skills fire ~0% locally).
  **Push, don't pull** is the load-bearing principle.
- Honest scoping: the measurable effect concentrates in zig by
  construction — python (136 units, 47% of the suite) gets a checker no
  competent model trips (§3.2), and rust is nearly inert in the suite
  (5/31 discriminating). "Equal assistance" is equal in mechanism, not in
  expected substance, across languages.
- The zig battlefield in v5-fast: **30 measured zig units + 1 pinned
  assumed-failed** (`158_karatsuba_bytes_f`) — §7 measures all 31.

## 2. Design principles

1. **Push, not pull.** Diagnostics arrive in the write/edit result,
   unasked. No new tool the model must learn.
2. **Feedback (Tier 1), then knowledge (Tier 3).** Compilers report
   "no member named X" without naming the replacement — an error-loop fix
   for a rename cascade costs full agent iterations per guess and may not
   converge inside `max_iter` (§3.6 names this risk; §7 readout 6
   measures it). Packs answer once. Tier 1 ships first only because it is
   generic and half a day of work; §7 pre-commits Tier 3 as the *next
   scheduled arm* if Tier 1 closes less than half the gap.
3. **Equal assistance, honestly versioned.** Applies identically to every
   model; changes what the leaderboard measures (model+harness, as
   always); eras never silently mix (§6).
4. **The harness must never do the task.** Verbatim toolchain output on
   the model's own files only; no auto-repair; no validation awareness
   (§6.3).
5. **Zero VRAM.** CPU only, bounded RAM (§3.3).

## 3. Tier 1 — push-diagnostics

### 3.1 Mechanism

After a successful `write_file`/`edit_file` whose path matches a source
extension, run that language's checker and append the verdict:

```
Wrote 2311 bytes to /tmp/eval_crt/crt.zig
── diagnostics (zig) ───────────────────────────────
crt.zig:79:57: error: expected type '[]const u8', found '?[]u8'
── 1 error ─────────────────────────────────────────
```

Clean check appends one line: `diagnostics: OK (zig)`. No wall-time in
the appended text (nondeterministic tokens pollute transcript diffing and
A/B token accounting).

### 3.2 Checker table — MEASURED on the rig, mirrors validation flags

| ext | checker command | measured latency | notes |
|---|---|---|---|
| .zig | `zig build-exe -fno-emit-bin FILE` | 0.14–0.18 s | **The load-bearing row.** `ast-check` is AstGen-only — measured passing stale `std.io`/`std.ascii` code CLEAN; it cannot see the §1 failure class and would emit *active false assurance*. `build-obj -fno-emit-bin` is also invalid (lazy analysis skips unreferenced fns — measured rc=0 on a stale call). For files without `pub fn main`, fall back to `ast-check` (build-exe would emit a bogus missing-main error). Scratch `ZIG_GLOBAL_CACHE_DIR`. |
| .rs | `rustc --emit=metadata --out-dir <mkdtemp, removed after> FILE` | 0.011 s | NOT `-o /dev/null` (measured: rustc fails creating temp files in /dev → bogus error on every clean write). No `--edition` pin — the validator compiles default-edition; the checker must mirror validation flags or checker-OK/validator-fail confusion results (measured with edition-2021 async fn). Emit to scratch, never the eval dir. |
| .go | `go vet` on all sibling `*.go` in the file's dir | 0.025 s | Works module-less in file mode (measured — the "go.mod not found" failure is dir-mode only). Sibling globbing avoids false `undefined:` on multi-file packages. Env-hardened: `GOTOOLCHAIN=local GOPROXY=off GOFLAGS=-mod=readonly CGO_ENABLED=0` (§3.3). |
| .c | `gcc -fsyntax-only -std=c11 -Wall -Wextra -I<filedir> FILE` | 0.005 s | Flags mirror validation; `-I` for fixture headers (none in the suite today; free-agent robustness). |
| .cpp | `g++ -fsyntax-only -std=c++17 -Wall -Wextra -I<filedir> FILE` | 0.077 s | Mirrors validation. |
| .py | `python3 -I -m py_compile FILE` | 0.024 s | `-I` (isolated) is a security requirement, not style (§3.3). Syntax-only — near-vacuous for the suite's majority language; upgrade path: pyflakes (tiny dep) for undefined-name detection if the OK signal should mean something for python. |
| .sh | `shellcheck -S warning FILE` | 0.004 s | |

Single dict in `tools.py`; missing toolchain ⇒ append nothing (never an
error). **Mandatory fixture tests per language (§8): a stale-stdlib file
(not merely a syntax-error file) MUST produce a diagnostic** — this is
the test that would have caught the ast-check blindness at review time
instead of after a dead A/B.

### 3.3 Execution safety (security review applied — all mandatory)

- **Shell injection:** `run_reaped` is `shell=True` with a
  model-controlled path (`write_file` accepts arbitrary paths). Every
  interpolated path is `shlex.quote()`d (or `run_reaped` grows list-argv
  support). Not implementation detail — a spec requirement.
- **Sandbox parity:** checkers run with `env=_scrubbed_env()` and under
  `OPENBEAST_BASH_WRAPPER` when set. Without this, the checker is a
  clean escape from Sandlock carrying unscrubbed secrets.
- **Honest filesystem claim:** compiler include/import resolution
  (`#include "/any/path"`, `include_str!`, `#[path]`) reads whatever it
  reaches *with the harness's privileges* and quotes contents into
  diagnostics. Today the model can `read_file` those paths anyway; under
  a future sandbox the wrapper (above) is what keeps the checker from
  becoming a read proxy. The original draft's "no new filesystem
  surface" claim was false and is retracted.
- **Go execution channels closed:** `GOTOOLCHAIN=local` (a model-written
  `go.mod` `toolchain` directive otherwise *downloads and executes* a
  different toolchain), `GOPROXY=off` (no network), `CGO_ENABLED=0`.
- **Python:** `-I` — bare `python3 -m py_compile` executes `.pth` files
  from user site-packages at startup (model-writable, outside the bash
  wrapper). "py_compile doesn't execute code" is only true in isolated
  mode.
- **Resource bounds:** checkers get their own `RLIMIT_AS` (2–4 GB) and
  `RLIMIT_CPU`, not the 32 GB child default — 4 parallel agents ×
  pathological input × 10 s runway is otherwise the 2026-07-07 OOM class
  again. Per-language concurrency cap so `--jobs N` checkers can't sum
  past a RAM budget. 10 s hard timeout; timeout/crash ⇒
  `diagnostics: unavailable (<reason>)`, never a failed write.
- Output cap: first 30 lines / 2 KB.

### 3.4 Cache-key rule (do not skip)

Assisted and unassisted rows must never mix. Editing `tools.py` changes
the context hash (separates code eras automatically); the env toggle
does not, so the cache key gains a diag component with these properties:
- **Omitted entirely when diagnostics are off** — off-keys keep the
  legacy shape, or every banked row (incl. today's A′ rows) becomes
  unreachable and the §7 off-arms die.
- When on: `diag1-<toolchain-fingerprint>` — a short hash of the checker
  table's toolchain versions (zig 0.16.0 vs 0.16.1 produce different
  diagnostics → different trajectories; a boolean under-keys).
- The flag is derived ONCE in `run_eval` and exported explicitly into
  the agent subprocess env — key and behavior must come from the same
  read, or they can disagree.
- Tests: cache-key separation test AND a legacy-key compatibility test.

### 3.5 Toggle & rollback

Pre-gate the feature ships **default OFF** — `OPENBEAST_DIAGNOSTICS=1`
opts in (this is what the A/B's on-arms set). Only after §7 passes does
the default flip to on, with `=0` as the kill switch. Stamped into
provenance (§6.1) and the cache key (§3.4).

### 3.6 Cost model & known risks

Latency: ≤0.2 s typical, worst measured 0.18 s — noise against
multi-minute generations; §7 gates on measured p95 anyway. Token cost:
~100–600 prompt tokens per failed check. **Non-convergence risk:** a
compiler names what's wrong, not what's right; a stale-API cascade under
Tier 1 is guess-and-check where each guess is a full iteration on a
2×-verbose model — it may not converge inside `max_iter`. §7 readout 6
measures iterations-to-fix so thrash is visible, and Tier 3 is the
pre-committed answer if it thrashes.

## 4. Tier 2 — LSP sidecar: DELETED (parking note)

The adversarial review removed this tier; the note stays so it isn't
re-invented:
- Warm LSP servers serve an *editor's* <100 ms budget; an agent loop has
  none — richer diagnostics = swap the Tier 1 command, no daemon needed.
- The pull half (hover/signature tools) contradicts §1's own evidence
  (models don't call optional tools), and cannot fix staleness: hover on
  a removed symbol returns nothing; discovering the new name is
  knowledge = Tier 3.
- Measured/verified besides: pacman zls is 0.15.1 vs rig zig 0.16.0
  (version-locked pair), zls publishes ast-check-grade diagnostics
  (WEAKER than fixed Tier 1), rust-analyzer executes model-written
  `build.rs`/proc-macros on load by default, and single-file /tmp
  fixtures give an LSP nothing cross-file to be richer about.
- If ever revisited: the security review's config table is mandatory
  (build-script/proc-macro execution off, realpath+commonpath path
  allowlist, pinned workspace roots, guest key rejected for
  file-tool-class endpoints).

The LSP *binaries* still install in §8 step 0 — their honest customer is
opencode's client-side LSP, not the harness.

## 5. Tier 3 — awareness packs (proactive staleness fix)

Version-pinned digests injected into the agent system prompt for the
task's target language. Corrected per review:

- **Sources that actually exist** (the draft's didn't): zig — parse
  `$ZIG/lib/std/*.zig` directly (zig 0.16 has no std-docs JSON mode;
  the doc server is HTML+wasm); go — `go list std` + per-package
  `go doc`; rust — stable rustdoc JSON does not exist (nightly-only),
  parse `rustc --print` surfaces or ship curated signatures.
- **Scope = the observed-failure surface, not "what moved."** "What
  moved" requires the old surface or changelog curation — human work
  wearing a generator's costume (zig 0.15→0.16 I/O rework alone exceeds
  any 2,000-token budget). The pack is: top-N current symbols with
  signatures for the failure-prone areas (zig: Io/Writer plumbing,
  ascii, casts), ≤2,000 tokens, fully derivable ⇒ checksum-pinnable.
  Anything requiring curation is named as curated, owned, and NOT
  checksum-pinned as if generated.
- Injection edits `runner.py` ⇒ full cache-era break — Tier 3 ships in
  its own era bump, never piggybacked. Own zig-only mini-A/B.

## 6. Eval integrity, comparability, era policy

### 6.1 Provenance & leaderboard display
Results gain `harness: {diagnostics: bool, toolchains: {...},
awareness_packs: {lang: version}}`. Display policy (review-capped): the
board shows ONE flag (`+assist`); full detail lives in provenance JSON
only. **No per-feature leaderboard columns, ever** — the era matrix must
not compound against a one-maintainer reality. Era joins
`entry_dedup_key` so assisted/unassisted rows coexist.

### 6.2 Cache eras
`tools.py`/`runner.py` edits land only between sweeps, immediately after
banking an era's results. §3.4 covers same-code toggling.

### 6.3 Threat model
- Checkers see only model-written files; validation scripts, fixtures,
  expected outputs never reach a checker or pack generator (pack code
  must not read `evals/`).
- **Anti-cheat audited (review):** sampled zig/rust validations all
  compile-AND-RUN against fixtures, several with forbidden-API greps —
  no v4 unit passes on compilation alone; push-diagnostics cannot
  complete a unit without demonstrated behavior. Keep the per-unit audit
  of suspicious new passes in §7.
- **`scoring.py --rebuild` poisoning (review HIGH, fix REQUIRED before
  any assisted run exists):** `--rebuild` scores raw task rows — a
  v5-fast results file yields garbage capability over 106 hard units,
  and with era in the dedup key an assisted era with only fast-suite
  files would seat that garbage on the board (the 291-guard doesn't run
  on the rebuild path). Fix ships WITH Tier 1: rebuild drops/flags
  entries with `suite_selection` set or `is_full_suite` false, and the
  291-guard is ported into the rebuild loop.
- run_eval guard: `cache_misses_skipped > 0` ⇒ suppress/flag the
  `fast_suite` imputed block (a partial-miss replay must not silently
  deflate a score).

## 7. The A/B experiment (QUEUED — the gate)

**Statistical redesign (review):** the draft's "+0.5 capability" ship bar
was innumerate — the 13-unit zig deficit is worth ~0.32 capability
(~0.025/variant), so a PERFECT intervention had ~5% probability of
clearing +0.5 against measured churn (net-flip SD ≈ 0.11). The rule is
now the paired test the repo already uses.

**Pre-flight (required):**
1. Re-pin v5-fast with the Phase A′ rows as references (already queued in
   TODO) AND diff 3.8's A′ full-run outcomes against the pin's assumed
   lists — any 3.8 fail sitting in `assumed_passed` distorts the absolute
   imputed scores the thresholds are defined on (tripwires cover only
   11% of assumed units; verified: zig has NO units in assumed_passed).
2. Add the 3 `assumed_failed` units to the measured set for this
   experiment (minutes of GPU) — one is zig (`158_karatsuba_bytes_f`),
   and a diagnostics rescue there is otherwise structurally invisible.

**Cells** (v5-fast + the 3 assumed-failed units, `--jobs 4`, non-MTP):

| cell | model | diag | source | cost |
|---|---|---|---|---|
| A0 | Qwen3.6 champion | off | **live run on the Tier-1 branch** | ~0.85 h |
| A1 | Qwen3.6 champion | on | live | ~0.85 h |
| B0 | Qwen3.8 Uncensored | off | **offline from banked A′ full-run results** (subset + impute — the make_fast_suite arithmetic) | 0 h |
| B1 | Qwen3.8 Uncensored | on | live | ~0.85 h |

Why: A0 "cache replay" is impossible — the champion's July rows predate
the current context hash (measured: 106/106 miss ⇒ 20/20 tripwire alarm),
and a July-row offline baseline confounds the champion cell with two
months of harness drift, so A0 runs live. B0's banked rows are from
*today* — offline derivation is clean. **True GPU budget ≈ 2.6 h**, not
the draft's 2 h.

**Readouts:**
1. **Primary/ship: B1-vs-B0 zig McNemar — ship iff ≥7 net zig rescues at
   p<0.05** (well-powered: 3.8's zig churn is near zero).
2. Non-zig guard, stated honestly: at n=1 it detects only ≥~8-net-unit
   regressions — a catastrophe alarm, not a safety proof.
3. Token/iteration overhead per cell; **readout 6:** iterations-to-fix on
   rescued units (thrash visibility → Tier 3 trigger).
4. Champion guard (now part of the SHIP RULE, not decoration): champion
   total delta within churn (McNemar p>0.05, zig included) — a feature
   that degrades the #1 model users actually run does not ship.
5. **p95 added write latency ≤ 1 s, measured in the A/B** (agent
   experience is part of the bar for default-on).
6. Tripwires: a failed tripwire is rerun once before counting (single
   churn flips must not veto); then 0 required.
7. Per-unit audit of suspicious new passes (§6.3).

**Pre-committed next step:** zig rescues < half the gap ⇒ the Tier 3
zig-only mini-A/B is the next scheduled arm — not "iterate or stop."

**Queue position:** after today's Phase A′ results work → Tier 1 build
(~half day + tests) → this. Max-triggered, ~2.6 h GPU.

## 8. Rollout order

0. **Binaries — execute NOW, ungated** (already Max-approved in TODO;
   needs his sudo): `sudo pacman -S --needed gopls rust-analyzer pyright`
   + `npm i -g bash-language-server`; **zls from a zig-0.16-matched
   release, NOT pacman** (0.15.1 there, version-locked to zig minor).
   This step alone lights up opencode's client-side LSP for every
   language — the largest user-visible win in this doc, independent of
   everything else. Who benefits, by surface: opencode (client LSP —
   step 0), eval agents + `agent.sh` + MCP write_file callers (Tier 1;
   note the rig's shellcheck push on a 99-shell-file repo is a real
   agent.sh win), WebUI chat (nothing — no file tools in that path),
   client laptops (nothing unless their toolchains exist — checker table
   silently no-ops there).
1. **Tier 1** + §3.3 hardening + §3.4 key + §6.3 rebuild fix + tests
   (per-language broken-file AND stale-stdlib fixtures; timeout, missing
   checker, output cap; cache-key separation + legacy-key compat).
   Toolchain-dependent fixture tests skip cleanly where the toolchain is
   absent (CI) and run for real on the rig. **Merge timing:** the PR may
   go green early but MERGES only after the current era's results are
   fully banked (§6.2) — building on a branch never touches a live
   sweep's tree, code, or cache.
2. **A/B (§7).** Gate.
3. Ship default-on + `+assist` flag + docs.
4. Tier 3 (own era bump, own mini-A/B).

## 9. Non-goals

Real-time completion into generations; multi-file refactoring
intelligence; replacing opencode's interactive LSP; auto-fix loops in
the harness; LSP sidecar (deleted, §4).

## 10. Adversarial review log (2026-09-08)

Four parallel reviewers; every HIGH and all actionable MEDs folded:
- **Systems (measured):** zig ast-check blind to std-drift (rc=0 on
  stale std.io/ascii — the feature's own target class); build-obj
  lazy-analysis hole; rustc `-o /dev/null` breaks on clean files;
  edition/flag mismatches vs validators; go vet file-mode works, gofmt
  fallback useless; zls 0.15.1-vs-zig-0.16 lock + ast-check-grade
  diagnostics; zig-std-JSON/rustdoc-JSON sources don't exist; measured
  latency table. → §3.2, §4, §5.
- **Security:** shell=True path injection + sandbox/env bypass;
  GOTOOLCHAIN download-and-execute; py_compile `.pth` execution;
  rust-analyzer build.rs/proc-macro execution (Tier 2); include-path
  read surface honesty; checker rlimits vs the 07-07 OOM class;
  toolchain versions in the cache key. → §3.3, §3.4, §4.
- **Eval-integrity (quantified):** +0.5 ship bar ≈ 5% power vs its own
  max effect (+0.32) → paired zig McNemar; A0 cache replay impossible
  (context hash) + partial-miss silent deflation → live A0, offline B0,
  miss-guard; `--rebuild` era poisoning; diag key legacy-shape
  compatibility; pin predates 3.8 (assumed-list diff pre-flight);
  assumed_failed has no canary and holds a zig unit; anti-cheat audit
  PASSED (validations run behavior, compilation alone completes
  nothing). → §7, §3.4, §6.3.
- **Product:** Tier 2 deleted (dominated by upgraded Tier 1 + Tier 3);
  step 0 named as the biggest user win and un-gated; "measured 80%" →
  hypothesized; champion + latency added to the ship rule; Tier 3
  "what moved" derivability; one `+assist` flag display cap;
  who-benefits table; python-checker honesty; analogy reset. → §0, §4,
  §6.1, §7, §8.
- Conflict resolved: eval-integrity's `build-obj -fno-emit-bin`
  recommendation lost to systems' deeper test (unreferenced-fn hole);
  `build-exe -fno-emit-bin` + main-less ast-check fallback stands.

---

# RESULTS ADDENDUM — the A/B campaign, final (2026-09-09/10)

*Written per Max's directive: "We want to be honest about our results.
We don't need this to yield massive results — the happy piece is that
we showed SOME progress." Full experimental record: the langaware
research journal (research branch).*

**Design run:** seven paired cells over two days — B-arm (Qwen3.8-
Uncensored) off/on ×2 full replicates, A-arm (Qwen3.6 champion) off/on
×2 full replicates, plus three untreated replicate pairs that measured
the suite's own noise. All cells: pristine llama.cpp b10865, reasoning
budget 20480, v5-fast 112 units + the 2 assumed-failed minis, paired
same-day arms, own cache eras, `--no-cache` replicates.

## What we can assert

1. **The mechanism works, replicated on both models.** The flagship
   stale-stdlib failure (`bst.zig`'s `trimRight`) converts under
   diagnostics in every treated run and never untreated; failure modes
   migrate from compile-death to logic exactly where the compiler
   speaks; several rescues arrived faster and cheaper than the
   baseline's failures.
2. **Zero harm, everywhere.** No treated cell regressed any non-target
   language in any run (guards p=0.77-0.82); the checker no-ops on
   non-source files and missing toolchains by construction.
3. **The capability effect is small: ≈ +2 to +5 net task units per
   run, confined to the targeted language.** B-arm combined over two
   replicates: zig +17/−11, net +6, p=0.345. Champion arm: flat-null
   (run 2 paired net −2, p=0.82).
4. **The suite's untreated churn floor is ±5-14 task flips per
   re-roll** — measured three times, both models (B0'↔B0'': 21 flips;
   A0'↔A0'': 22 flips; imputed swings up to ±0.75). This floor, not
   the treatment, explains round-2's apparent champion record (98.92
   diag-ON vs 98.89 untreated the next morning) and most single-run
   "rescues." Any future tool-effect claim on this suite needs paired
   same-day arms minimum, replicates by default, and either effects
   ≥ ~15 net units or a lower-churn eval mode.
5. **Two confounds found and owned:** the reasoning-budget change
   (PR #45) independently unlocked marathon-class zig units the
   diagnostics were initially credited for; and the zig checker's
   `has_main` substring bug pushed occasional false errors into the
   treated arms (fixed in the tools-hardening PR), attenuating the
   measured effect.

## The decision this supports

beast-assist ships **default-OFF as a documented opt-in** (v1.2.0):
free when idle, token-saving when active, provably harmless, honestly
sized. The decisive next arms are pre-committed: **Tier 3 awareness
packs** (proactive staleness fix — structurally larger expected
effect), the **fixed checker** as the new era's baseline, and a
**low-churn eval mode** (greedy single-slot) to shrink the floor
itself. Progress: real, small, and measured to its exact size — which
is the only kind of progress a measurement stack should claim.
