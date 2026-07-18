> **⚠️ ARCHIVED — historical record.** Describes work that has shipped or been
> superseded; kept for provenance, not current docs. Live state:
> [archive index](README.md) · [`../TODO.md`](../TODO.md) · [`../REFERENCE.md`](../REFERENCE.md).

# Eval suite double-pass review — 2026-07-07

**Charter (Max):** review every eval task one-by-one — spec ↔ validation ↔
reference coherence across all languages — and note desired improvements
WITHOUT changing anything. The current suite stays frozen so the pending
sweep remains comparable; improvements land as a batch afterward, then the
suite re-runs from the top.

**Method:** 8 parallel reviewers, ~20 tasks each, 7 lenses per task (spec
precision, validation robustness, spec↔validation coherence,
cheat-resistance, setup/cleanup hygiene, per-language reference audit for
variant tasks, difficulty sanity), calibrated on the v3.5 post-mortem
defect classes. Claims verified against file contents; several verified
empirically by running code.

**Rule for applying fixes later:** fixes that only tighten validation
(adding assertions, pre_validate) may flip prior PASSes to FAILs — that is
the point, but scores are only comparable within a suite version. Batch
everything, bump a suite version marker, note it in docs/RESULTS.md.

---

## Executive summary

**All 159 tasks reviewed. 42 fully clean (26%), 117 flagged: 32 high,
~99 med, ~111 low findings.** Many mechanical claims were verified by
executing code, not inferred. The defects split into two families that
corrupt the leaderboard in OPPOSITE directions:

**Score inflators (models get credit they didn't earn):**
- Empty/stub submissions pass: 09 (nothing at all), 07 (`sys.exit(1)`),
  141 (`return 6.0`), 125 (`return initial`), 139 (return input
  untouched), 140 (A≡1), 21 (unfixed race passes 5/5).
- Named algorithm never forced: 137 (typo'd semiprime — trial division
  suffices), 155, 159, 138, 122, 130, 47, 95, 100, 117, 144; dead perf
  gates 145/146/147 (measured), thin gates 150/152.
- Fixture cheats: readable expected.txt (S3), rewritable check.py with
  no pre_validate (S1 — 20+ tasks).

**Score deflators (correct solutions fail):**
- Echo-mangled fixtures in 02–06, 08, 10 (S4 — verified broken).
- Comment-trippable lints: 24, 40, 43, 49 (bare `'numpy' not in src` —
  the Phase-1 defect fixed only for 42), 71, 150, 151, 158, plus lint
  bait in 11/19/20/23/92/100.
- Unpromised assertions: 118 (`t1 == 1`), 120 (`state` attribute name),
  14 (associativity), 108/109 (str/bytes coin-flips), 123 (spec says
  "fx fy", expected file holds positions), 126 (spec formula is WRONG —
  omits the key XOR), 63 (`-0.000000` diff brittleness), 19e/f + 31e/f +
  148/155/158/159 Rust/Zig (I/O contract referenced but never shown).
- Timing flakes on a loaded box: 106, 107, 13.
- Self-test consumes the fixture → correct solution fails: 29, 16.

**Reading the pending sweep:** the suite is FROZEN for comparability, so
run it as-is — but interpret with this ledger open. Deflators depress
all models roughly equally (relative ranking mostly survives); inflators
compress the top (weak and strong models both "pass" unenforced tasks).
Absolute accuracy numbers are soft by several points in both directions.

**Fix-batch plan (post-sweep, suite version v4):**
1. **Mechanical sweep, low risk:** S4 echo→printf; add pre_validate to
   every fixture task (S1); per-step `|| exit 1` in 07/09; pytest exit
   code in 04; delete-out.db in 16; 55's /tmp collision; 123's field
   names; anchored-regex lints everywhere (S2); inline Rust/Zig
   contracts (S6).
2. **Enforcement repairs:** recalibrate/replace perf gates (145/146/147/
   150/152, memory-bound 147); fix 137's semiprime; large cases for
   155/159/138/122/158; validation-side verification for 143/109/100.
3. **Spec repairs:** 126's formula; 118/120/134/135 return contracts;
   untested-promise probes (~20 tasks, one assert each).
4. **Bookkeeping:** difficulty relabels (~12 tasks); commit the missing
   refs (S5); regenerate docs/RESULTS.md notes with the version bump.

Each fix batch re-verifies per the eval-task-author skill: reference
passes, deliberately-wrong impl fails.

## Systemic findings (affect many tasks)

- **S1 [high] pre_validate is opt-in and almost nobody opts in.**
  WORK_PLAN 1.4 described re-running `setup` before validation; as
  implemented, `run_eval.py` only re-runs the opt-in `pre_validate`, and
  exactly ONE task in the suite (17_deploy_rollback) declares it. Every
  task whose validation reads fixture files (input.txt/expected.txt) can
  be silently flipped by an agent that overwrites a fixture while
  debugging — or deliberately. Desired: add `pre_validate` (= the
  idempotent setup command) to every fixture-bearing task, or make the
  harness re-run `setup` unconditionally before validation.
- **S2 [med] Comment-trippable forbidden-API lints.** Same class as the
  fixed 42_value_at_risk defect: substring greps for forbidden calls
  (`.bit_count(`, `hmac.compare_digest`, intrinsics) fire on a model
  writing a compliance COMMENT ("# not using int.bit_count()"), which the
  spec text actively invites. Desired: strip comments/strings before
  grepping, or grep only genuine call sites.
- **S3 [med] `cat expected.txt` cheat shape.** Stdin/expected-file tasks
  run validation in the fixture dir; a program that ignores stdin and
  prints the expected file passes. Desired: validation copies input to a
  private name / runs from another cwd / regenerates expected values
  in-script.
- **S4 [high] echo-mangled fixtures.** `/bin/sh`'s `echo` doesn't
  interpret `\n`: seven early tasks (02–06, 08, 10) ship one-line
  fixture files with literal backslash-n — empirically verified broken.
  Score deflator. Desired: `printf` everywhere (16/17 already do).
- **S5 [med] Missing durable references.** For several variant tasks
  only the Zig ref was ever committed to evals/refs/ (topo, uf, xgcd,
  mr, vowels, pal, pow2, qs) — the py/go/c/cpp/rust refs lived in /tmp
  and are gone, so 5/6 of those variants can't be re-audited. Desired:
  re-author and commit the missing refs. (Related tooling fix landed
  tonight: tests/audit_variants.py had a hardcoded pre-rename repo path
  — now repo-relative.)
- **S6 [med] Rust/Zig variant texts not self-contained.** 19 and 31 say
  "same I/O contract as the other variants" — but agents only receive
  their own variant's text (verified in run_eval.load_tasks), so they're
  never told the format. Desired: inline the full contract per variant.

## Slice 81–100 (reviewed: 20, OK: 10, improve: 10 — 1 high, 8 med, 12 low)

- **88_thread_safe_counter [high]** — validation cannot detect the missing
  lock: an unsynchronized `self.n += 1` counter passed the exact
  validation workload 20/20 times (and 10/10 with a 1µs switch interval)
  on CPython 3.14 — the task's entire discriminating property is
  unverifiable black-box. Desired: source-level check for
  `threading.Lock`/`RLock` (spec explicitly mandates it), or accept the
  task tests API shape only. Also: `increment(n)` return value and
  `initial=` param promised but untested.
- **100_constant_time_compare [med×3]** — non-constant-time solutions
  (`a == b` in every language, `strncmp` in C) pass all lints and the
  functional diff — the security property is unmeasured. Desired: require
  the XOR accumulator the spec already licenses (`grep -q '\^'`), add
  `strncmp` to deny-list, strip comments before grepping (S2), add
  pre_validate (S1). Low: all 6 refs walk only `a`'s bytes on length
  mismatch while the spec says "length-padded" — align spec text or refs.
- **85_base64 [med]** — Phase-1 str/bytes fix verified landed and sound.
  But the headline URL-safe-alphabet property is untested: the chosen
  test string's standard-b64 encoding contains no `+`/`/` (verified
  byte-identical to URL-safe), so a standard-alphabet solution passes.
  Desired: add an input whose encoding contains `-`/`_`, assert `+`/`/`
  absent; also test the padded-input decode path.
- **92_popcount [med×2]** — comment-trippable intrinsic lints in all 6
  languages (S2); no pre_validate (S1). Low: Python lint blocks
  `bin(n).count('1')` but not `format(n,'b').count('1')` — inconsistent;
  spec's "up to 64-bit unsigned, i.e. up to 2^63-1" is self-contradictory.
  Refs verified clean (Kernighan) in all 6 languages.
- **95_swap_xor [med]** — the only distinguishing constraint (XOR, no
  temp/tuple swap) is never verified: `return (b, a)` passes. Desired:
  `assert '^' in src`.
- **82_sigmoid [med]** — S1 pre_validate hole + `cat expected.txt` shape
  (S3); trailing-whitespace sensitivity unstated in spec. All 6 refs
  verified correct (stable two-branch sigmoid, values to 9 dp).
- **86_uuid4 [low]** — variant-digit rejection untested (bad-variant
  string would pass `is_uuid4`).
- **90_file_hash [low×2]** — `algorithm`/`chunk_size` params promised,
  untested; no pre_validate for the hardcoded-digest fixture (false-FAIL
  risk if agent overwrites data.txt).
- **91_retry [low]** — `functools.wraps` and exception-filter mandates
  unchecked (bare-Exception catcher passes).
- **83_manhattan [low]** — promised ValueError on length mismatch never
  tested.
- **OK (verified clean through all 7 lenses):** 81_relu, 84_sha256,
  87_backoff, 89_env_var, 93_horner, 94_saxpy, 96_temperature,
  97_kinetic_energy, 98_hookes_law (exact float == verified safe),
  99_wien (tolerance verified).

## Slice 41–60 (reviewed: 20, OK: 7, improve: 13 — 2 high, 9 med, 12 low)

- **43_sharpe_sortino [high]** and **49_markov_steady_state [high]** —
  both still carry the bare `assert 'numpy' not in src` lint that Phase 1
  fixed ONLY for task 42, with spec text ("no numpy") that primes models
  to echo the trigger word in a comment. **These will zero correct
  solutions in the pending sweep** — treat their sweep scores as
  depressed. Desired: import-anchored regex lint as in 42.
- **42_value_at_risk [med]** — Phase-1 fix verified landed and
  mathematically sound (empirically: reference passes, docstring-echo
  variant still fails). Residual bait: the spec sentence itself contains
  "do not import numpy or scipy" — reword + line-anchored regex.
- **47_branchless_min [med×2]** — the branchless constraint (the entire
  point) is unenforced: builtin `min()` passes all 6 variants. Plus S1
  (no pre_validate) and S3 (expected.txt readable). Refs verified clean.
- **51_toposort / 52_unionfind [med×2 each]** — S1; check.py itself is
  agent-rewritable (`print('OK')` cheat); durable refs exist ONLY for Zig
  — py/go/c/cpp/rust refs were never migrated from /tmp. Desired: commit
  the missing refs. Low: Rust/Zig task text mentions a nonexistent
  expected.txt.
- **54_astar [med]** — S1 + S3. All 3 expected answers re-derived, all 6
  refs verified correct BFS.
- **55_projectile [med]** — /tmp dir collision: uses `/tmp/eval_proj`,
  same as 03_grep_and_fix, whose cleanup `rm -rf`s it — cross-task
  interference. Desired: rename to /tmp/eval_projectile.
- **44_rmsnorm [low×2]** — wrong eps placement passes the loose gate
  (verified: 0.999 vs 0.707 both inside `0<v<2`); "hard" label for a
  5-line verbatim formula. Desired: tighten test 4 to ±1e-3; relabel easy.
- **53_bloom [low×2]** — item type unspecified (str vs bytes class);
  a set-backed non-bloom impl passes. Desired: pin type; assert m/k
  formula values.
- **56_rodrigues [low]** — promised zero-axis behavior untested.
- **41 [low], 48 [low]** — difficulty labels inflated ("hard" for
  verbatim closed-form formulas). Desired: relabel.
- **OK:** 45_kv_cache, 46_rope, 50_secretary (seed pinned, tolerance
  ≈7σ), 57_damped, 58_bond, 59_binomial, 60_irr (NPV-residual validation
  — robust to any method).

## Slice 61–80 (reviewed: 20, OK: 11, improve: 9 — 0 high, 9 med, 9 low)

- **Dominant family: S1 (no pre_validate)** on all 7 fixture-based stdio
  tasks — 61_extgcd, 62_crt, 63_det, 65_miller_rabin, 71_reverse_list,
  73_count_vowels, 74_palindrome. 61's check.py is also rewritable.
- **63_det [med]** — genuine float brittleness: exact diff on `%.6f` can
  fail a correct impl printing `-0.000000` for the singular case (pivot
  residual sign is implementation-dependent; verified ~6e-30). Desired:
  check.py with 1e-6 tolerance instead of diff. Also: spec's "pivot
  exactly zero" wording contradicts fp reality (refs use `<1e-15`).
- **71_reverse_list [med]** — comment-trippable forbidden-API lints in
  all 6 languages (S2); `reversed\(` also false-fires on a helper named
  `my_reversed(`. Refs verified: none trip their own lints.
- **61/65 [low]** — fixed visible inputs + readable expected answers make
  hardcoding possible; durable refs missing for 5/6 languages (only
  xgcd.zig / mr.zig / vowels.zig / pal.zig committed).
- **79_factorial [low]** — "iteratively" promised, unverifiable
  (recursion and math.factorial both pass). **80_cosine_similarity
  [low]** — two promised ValueErrors untested.
- **OK:** 64, 66, 67, 68, 69, 70 (all conventions pinned, tolerances
  verified sane), 72, 75, 76, 77, 78. Also verified suite-wide: all /tmp
  dirs unique EXCEPT the 55/03 collision noted above.

## Slice 101–120 (reviewed: 21, OK: 5, improve: 16 — 3 high, 10 med, 12 low)

- **108_hmac_verify [high]** — exact replay of the 85_base64 str/bytes
  coin-flip: `hmac_sign(message, key)` types unstated, stdlib wants
  bytes, validation passes str. Desired: annotate `message: str,
  key: str` in the task text.
- **118_distributed_lock [high]** — `assert t1 == 1` but the spec only
  promises "monotonically increasing" — a 0-based counter (equally
  natural) fails a correct impl. Desired: spec "first token is 1" or
  relax the assert. Also: `renew()` fully spec'd, never called.
- **115_fft [high]** — no pre_validate while validation trusts
  agent-writable check.py/expected.txt/input.txt (S1's worst instance);
  S3 cheat shape; Rust variant suppresses compiler errors
  (`2>/dev/null`), starving the agent's iteration loop of feedback. All
  6 refs verified as genuine radix-2 CT FFTs; expected values checked.
- **Timing flakes [med]:** 106_debouncer (50 ms windows — a loaded box
  fails correct impls) and 107_singleflight (all 10 threads must start
  within 50 ms). Desired: 5-10x margins, same semantics.
- **Validations that don't measure the titular property [med]:**
  104_threadsafe_lru (lock-free LRU passes under GIL; no concurrent
  eviction), 109_jwt (round-trip only — a non-JWT passes; recompute the
  HMAC in validation), 117_iter_refinement (residual gate can't see
  refinement; test forward error on Hilbert 8×8), 119_vector_clock
  (instance API never asserted), 121_quorum_kv (read repair provably
  unverifiable through this API given r+w>n — drop the mandate or spec an
  inspectable hook), 112_american_option (put only lower-bounded; pin to
  CRR reference 6.0884±0.05, value verified), 120_2pc (asserts an
  attribute name the spec never promises).
- **[low]:** 101 (secrets usage unmeasured), 103 (format promise
  untested), 110 (plaintext-retention promise unverifiable — cheap repr
  probe suggested), 116 (both polys monic+real — add non-monic and
  complex-root cases), 109/121 minor untested promises.
- **OK:** 102_email_regex, 105_bounded_queue, 111_login_ratelimit,
  113_bayesian_ab (thresholds ≥14σ from MC noise), 114_svd.

## Slice 01–20 (reviewed: 20, OK: 2, improve: 18 — 7 high, 15 med, 17 low)

**All mechanical claims in this slice were verified by executing the
setup/validation snippets — these are reproduced defects, not inferences.**

- **S4 [high, systemic] echo-mangled fixtures (7 tasks: 02, 03, 04, 05,
  06, 08, 10).** `/bin/sh`'s `echo` does not interpret `\n`, so these
  setups write ONE-LINE files containing literal backslash-n — invalid
  Python/CSV fixtures (verified by running each setup). The advertised
  premise ("the add function subtracts", "app crashes on Bob's row") is
  false; the real task silently becomes whole-file reconstruction. This
  deflates every model's score on 7 tasks. Desired: convert all these
  setups to `printf` (as 16/17 already use).
- **07_cli_tool / 09_bash_script [high] — empty submission passes.** The
  `cmd && ... ; test $? -eq 1 && echo OK` pattern: when an early step
  fails with status 1, validation prints OK. Reproduced: a wc.py that is
  only `sys.exit(1)` passes 07; NO submission at all passes 09. Silent
  score inflators. Desired: per-step `|| exit 1` guards.
- **04_write_tests [high×2]** — `pytest | tail -1 | grep -q 'passed'`
  matches "1 failed, 3 passed"; and a single trivial test passes (no
  coverage check). Desired: check pytest exit code; mutate calc.py and
  require the suite to catch it.
- **16_csv_to_sqlite [high]** — validation runs against a possibly
  pre-existing out.db: an agent that tested its own code fails
  ("table already exists") while a stub + stale db passes. Desired:
  validation deletes out.db first.
- **19_three_way_quicksort [high]** — Rust/Zig variant text says "same
  I/O contract as the other variants" but agents only ever see their own
  variant (verified via run_eval.load_tasks): they are never told the
  format or the sorting requirement. Also: no sort-prohibition in
  Rust/Zig; refs missing for 5/6 languages; expected.txt readable-cheat.
- **11/20 [med]** — expected.txt readable beside the program (S3);
  lint asymmetry (C/Zig variants have no forbidden-API lint at all —
  qsort+dedupe passes 11 without a BST); comment-trippable lints (S2).
- **05 [med×3]** — mangled fixtures + validation assumes a JSON layout
  and user names the spec never pins.
- **[low] highlights:** 12 (O(1) promised, never enforced), 13 (100 ms
  timing slack), 14 (asserts associativity the spec doesn't state), 15
  (`is True/False` identity vs unpromised False), 17 (pre_validate
  present and correct — the one task that has it — but previous_version
  never checked), 07/10 difficulty overlabels.
- **OK:** 01_create_file, 18_flatten_json.

## Slice 21–40 (reviewed: 20, OK: 4, improve: 16 — 4 high, 12 med, 13 low)

- **21_race_condition [high] — dead validation.** The buggy fixture
  `self.value = self.value + 1` produced exactly 100000 in 5/5 trials on
  CPython 3.14 (eval-breaker only fires at calls/backward jumps — the
  RMW is never interrupted). A model that does NOTHING passes; the task
  measures nothing. Desired: make the race manifest (split the RMW
  across an eval-breaker point) and verify buggy code fails 5/5.
- **24_reentrant_lock [high]** and **40_attention [high]** — the
  42-class lint bait again: spec text contains the trigger string
  ("do NOT use threading.RLock" / "no numpy/torch") and validation
  substring-lints it — correct solutions with compliance comments fail.
  Desired: usage-anchored regexes; reword specs.
- **29_db_migration [high]** — one-shot fixture, no pre_validate: any
  agent that tests its own migrate.py consumes the fixture and then a
  correct solution FAILS validation (already-migrated db). Desired:
  pre_validate = setup (idempotent).
- **40_attention [med]** — "numerically stable" unchecked: max score
  70.7, exp() ≈ 5e30 — an unstable softmax passes. Desired: Q magnitudes
  ~1000 so instability overflows.
- **39_blocked_transpose [med×2]** — blocking (the task's point) is
  unenforced: max fixture 4×2, no perf gate — a naive transpose passes a
  "Performance & HW Opt" task. Plus S1.
- **31_is_power_of_two [med×3]** — Rust/Zig texts not self-contained
  (same as 19); refs missing 5/6; S1. **27/32/36/38 [med]** — S1
  (no pre_validate; 36's corruptible fixture is check.py itself).
- **23_sql_injection [med]** — comment-stripping needed: a solution that
  leaves the old vulnerable line commented out fails the lint despite
  passing all behavioral injection tests.
- **25_memory_leak [med] / 22, 30 [low]** — difficulty mislabels
  (25's entire fix is one `.remove()` line at hard weight 2.0).
- **OK:** 28_tcp_state_machine, 33_compound_interest, 34_z_score,
  35_descriptive_stats (its lint correctly uses import-form — the
  pattern the whole suite should follow).

## Slice 121–140 (reviewed: 20, OK: 0, improve: 20 — 4 high, 21 med, 24 low)

All four highs are false-positive/cheat paths that INFLATE scores:

- **137_pollard_rho [high]** — the fixture never requires Pollard rho:
  100000007000000049 is divisible by 3 (almost certainly a typo for
  100000007 × 1000000007 = 100000007700000049), and the smallest factors
  across all five cases are {83, 2, 3, 3, 37} — a 5-line trial-division
  loop passes instantly in every language; the 128-bit mulmod content of
  this "hard" task is never exercised. Desired: fix the typo'd semiprime
  + add one with both factors ~1e9.
- **125_wave_fdtd [high]** — the only functional check evolves sin(πx)
  over exactly one FULL period and asserts return ≈ initial: `return
  initial` passes with error 0 (verified). Desired: add a half-period
  check against −initial.
- **139_yield_curve [high]** — flat-curve-only validation: returning the
  par rates untouched passes both asserts (true zero rate 0.04938 vs
  0.05 — indistinguishable at the 1e-3 gate). Desired: sloped input with
  precomputed zeros at 1e-6.
- **140_vasicek [high]** — the A(t,T) term (the only nontrivial part) is
  unverified: P = exp(−B·r0) with A ≡ 1 passes every assert. Desired:
  one absolute closed-form price at 1e-9.
- **Unenforced-algorithm family [med]:** 122_gemm (naive 3-loop GEMM
  passes a "hard" blocking task — no perf gate, N=64), 130 (iterative
  loop passes the "recursive + ABI" task), 138_bsgs (p ≤ 7919 —
  brute force passes; needs ~1e12-scale p).
- **RISC-V trio 129/130/131 [med each]** — rvi.py (the judge itself!) is
  agent-writable with no pre_validate; and the interpreter's narrow
  instruction subset is undocumented — a legal RV32I solution dies with
  NotImplementedError. Desired: pre_validate + "read rvi.py first" note.
- **126_hmac_scratch [med×2]** — "WITHOUT the hmac module" wholly
  unenforced; AND the spec's formula is wrong as written (omits the key
  XOR into ipad/opad and the .digest() on the hashed key) — only prior
  HMAC knowledge saves a literal-minded model.
- **123_nbody [med]** — spec says fields are "fx fy vx vy" but
  expected.txt is positions (verified independently) — a literal model
  prints forces and fails. Desired: 'x y vx vy'.
- **S1 instances:** 122, 123, 127, 129, 130, 131, 136, 137 (8 more
  fixture tasks without pre_validate).
- **132/133 [med]** — γ>0 ferroelectric branch satisfiable by P=0 (a
  root of the residual); quantum task observes only |ψ[0]|² with all-real
  H's — σy sign errors and all phase errors pass.
- **[low] cluster:** difficulty overlabels (126, 132, 133, 135, 140 all
  "hard" for formula transcription; 131 harder-labeled than the harder
  129), promised ValueErrors untested (121, 125, 132), return contracts
  implicit (134, 135 — the WORK_PLAN 1.3 class), 127's garbled Rcon hint.

## Slice 141–159 (reviewed: 19, OK: 3, improve: 16 — 11 high, 15 med, 12 low)

The hard tier — where the 2.0 weights live — has the most broken
enforcement. All perf measurements below were run empirically.

- **Dead perf gates [high×3, measured]:** 145_segment_tree_lazy (naive
  O(n)-per-update finishes in 0.47s vs the 2.5s budget), 146_aho_corasick
  (naive per-pattern `str.find` = 0.003s vs 2.0s — C-speed find beats any
  Python automaton), 147_persistent_bst (copy-list + bisect = 0.04s vs
  3.0s; the real discriminator is MEMORY — bound peak via tracemalloc).
  Two more gates are coin-flips on faster hardware [med]: 150 (cheat
  only 1.6x over budget) and 152 (1.8x) — the skill demands ≥10x.
- **Vectors that never force the named algorithm [high×2]:**
  155_tonelli_shanks (only large p is ≡3 mod 4 — the exponent shortcut
  suffices; brute force passes everything) and 159_ntt_convolution (max
  case 30×30, expected.txt itself generated by a naive double loop — a
  5-line O(n²) loop earns hard×6-variant credit). Same family as 137.
- **Lint bait [high×2]:** 150_concurrent_queue and 158_karatsuba_bytes
  (+ 151 [med]) — specs name the forbidden strings verbatim, validations
  substring-grep them; compliance comments fail correct code.
- **S1 false-pass vectors [high]:** 148, 155, 158, 159 all lack
  pre_validate with agent-writable expected.txt/check.py (155/159's
  check.py is the sole verdict source — `print('OK')` is a guaranteed
  pass).
- **158_karatsuba [med]** — the Zig reference is SCHOOLBOOK, not
  Karatsuba (its own comment admits it): the ref violates the spec it
  certifies; with ≤80-byte inputs, "implement Karatsuba" is unenforced
  in all 6 variants. Rust variant also carries no forbidden-shortcut
  constraint while python/go/cpp do — cross-language scoring unfairness.
- **143_uov [med]** — verify() is agent-written, so a shared-secret stub
  passes without any UOV math; validation must evaluate the public polys
  itself. **141_asian_call [med]** — a constant `return 6.0` passes.
  **144_dsp [med]** — "implement the DFT yourself" unenforced
  (numpy.fft passes).
- **S6 instances:** 148, 155, 158, 159 (Rust/Zig dangling "see siblings"
  contract). **S5 instances:** refs Zig-only for 148/155/158 pipelines.
- **[low]:** 142/154 difficulty labels, 145's inverted-range perf loop,
  147's 2s-spec vs 3s-gate mismatch, 157's over-enumerated sign combos,
  159's unchecked empty-input promise.
- **OK:** 149_suffix_automaton, 153_coroutine_scheduler,
  156_berlekamp_massey. (154's spec/validation otherwise called
  exemplary — every return value promised and tested.)

---

*Note: 121_quorum_kv was independently reviewed by two slices; their
findings agree (read repair unverifiable through the current API).*
