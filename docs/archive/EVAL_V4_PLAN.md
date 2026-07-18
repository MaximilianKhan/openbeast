> **⚠️ ARCHIVED — historical record.** Describes work that has shipped or been
> superseded; kept for provenance, not current docs. Live state:
> [archive index](README.md) · [`../TODO.md`](../TODO.md) · [`../REFERENCE.md`](../REFERENCE.md).

# Suite v4 — the hardened benchmark

**Directive (Max, 2026-07-07):** fix everything from the double-pass
review (docs/EVAL_REVIEW_2026-07-07.md), prune tests that overlap
capabilities already measured elsewhere, kill every inflator and
deflator, keep all 12 topic categories, and make the suite something
researchers cannot question. Fewer total tests is acceptable. Prompts
must not leave shortcut room for local models.

v4 is a **version break**: v3.5 numbers are NOT comparable to v4. But we
**preserve v3.5 in full** — its leaderboard, its results, and the review
that exposed its defects are the evolution narrative for OpenBeast's
public launch (found 117 issues across 159 tasks → pruned + hardened →
an unbreakable 134-task suite). We slide into v4 gradually as fixes land;
v3.5 stays displayed and labeled "legacy — see docs/EVAL_REVIEW for why
we rebuilt it," and is only archived/removed at a later formal-display
pass, on Max's call. The sweep runs on v4 once verification is green;
the v3.5 leaderboard remains in docs/RESULTS.md as the "before".

## Pruned tasks (25 — 159 → 134 base tasks)

Criteria: (a) trivial formula-transcription duplicates of a stronger
sibling in the same category, (b) the discriminating property is
fundamentally unverifiable (per review), (c) capability fully covered
elsewhere. Every category retains coverage.

| Category | Pruned | Rationale / surviving coverage |
|---|---|---|
| Math Finance | 75_simple_interest, 76_fv_annuity, 77_apr_apy | one-line formulas; 33_compound_interest keeps the easy anchor; 58/59/60/36/112/139/140/141/142 carry the rest |
| Physics | 96_temperature, 97_kinetic_energy, 98_hookes_law | one-line formulas; 99_wien stays as the easy anchor (constant-handling nuance) |
| LLM / ML | 81_relu, 83_manhattan | one-liners; 80_cosine (edge cases) + 82_sigmoid (6-lang) keep the easy tier |
| Prob & Stats | 34_z_score, 78_sample_variance | both subsumed by 35_descriptive_stats |
| Algorithms | 72_find_max | trivial; 71/73/74 (6-lang) are the easy anchors |
| Pure Math | 79_factorial | trivial; "iteratively" unverifiable |
| Concurrency | 88_thread_safe_counter, 89_env_var, 90_file_hash | 88's property proven unverifiable black-box (review); 89 trivial; 90 duplicates 84_sha256; 91_retry stays as the easy anchor |
| Distributed | 86_uuid4 | trivial format check; 84/85/87 keep the easy tier |
| Performance | 94_saxpy, 95_swap_xor, 39_blocked_transpose, 47_branchless_min, 130_riscv_factorial | 94 duplicates 93_horner; 95/47 constraints only enforceable by source-grep (exactly what researchers question); 39 duplicates 122_gemm's capability with no gate; 130's ABI/recursion mandate unenforceable — 129+131 keep RISC-V |
| Security | 101_secrets_token, 110_password_reset | both properties unmeasurable through the API (review); 100/102/103 keep the easy tier |
| SWE / DevOps | 30_api_versioning | duplicates 08_api_endpoint (Flask routes + renames) |
| Pure Math (2) | 135_cg | validation cannot distinguish CG from Gaussian elimination; 117_iter_refinement (fixed with forward-error gate) covers "solve Ax=b well" |

6-lang variant tasks pruned: 39, 47 (12 variant entries). All other
variant tasks stay and get fixed.

## Fix protocol (every surviving flagged task)

1. Apply the task's findings from EVAL_REVIEW_2026-07-07.md — spec,
   validation, setup, difficulty — plus the systemic rules below.
2. **Verify both ways (mandatory, empirical):**
   - Reference implementation → validation PASSES.
   - The documented cheat/wrong impl (empty submission, `cat
     expected.txt`, naive algorithm, stub) → validation FAILS.
   Nothing lands without both runs.

## Systemic rules (v4 standards)

- **R1 pre_validate everywhere it applies:** every task whose validation
  reads fixture files the agent doesn't legitimately modify gets
  `pre_validate` = its (idempotent) setup. Fix-in-place tasks (the
  deliverable IS the fixture) do not.
- **R2 lints are comment/string-proof and import/call-anchored:** strip
  comments and string literals, then match `^\s*(?:import|from)\s+X` or
  a call-site regex. Never a bare substring. Spec text never contains
  the literal trigger string (say "the numpy library" not "`import
  numpy`").
- **R3 no readable answers:** validation regenerates expected values
  in-script (or copies inputs to a private name and diffs privately).
  `_gen.py`/`expected.txt` never sit readable-and-authoritative in the
  agent's cwd.
- **R4 perf/memory gates calibrated ≥10x:** measured on this box —
  reference comfortably inside budget, the naive cheat ≥10x over (or a
  memory bound where time can't discriminate, e.g. 147 via tracemalloc).
- **R5 printf, never echo-with-\n.** All fixtures byte-verified after
  setup.
- **R6 every variant text self-contained:** full I/O contract inline in
  all 6 languages; no "see siblings".
- **R7 every promise tested, every assertion promised:** ValueErrors get
  probes; validations assert nothing the spec doesn't state.
- **R8 durable refs for all 6 languages** of every variant task,
  committed under evals/refs/, re-verified by tests/audit_variants.py.
- **R9 timing margins ≥5x** on concurrency tests (loaded-box proof).

## Execution

Wave A: prune + this plan (done in the same commit).
Wave B: 8 slice-fix agents apply the protocol to all flagged survivors.
Wave C: suite-wide verification — audit_variants, task-structure tests,
empirical re-verification of every fixed task (correct→PASS, cheat→FAIL).
Docs: introduce v4 counts as the CURRENT suite while KEEPING the v3.5
leaderboard in docs/RESULTS.md under a "Legacy (v3.5)" heading pointing
to docs/EVAL_REVIEW_2026-07-07.md — the before/after is a feature. Tag
the suite version (v4); do NOT delete v3.5 data. Formal-display archival
of legacy data is a separate later pass on Max's explicit call.
Then: the full sweep runs on v4, producing the first v4 leaderboard
ALONGSIDE (not overwriting) the v3.5 one.
