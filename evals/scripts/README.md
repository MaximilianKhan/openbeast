# Eval scripts — one-shot generators preserved for re-derivation

These scripts authored the v3.5 variant task JSONs in `evals/tasks/`. Once
the JSONs are committed they're authoritative; the scripts here are
preserved for two reasons:

1. **Auditability** — they show exactly what fixtures and validation
   logic were generated, with the math/proofs inlined as Python.
2. **Reproducibility** — if you want to regenerate a task with a tweak
   (different test fixture, different expected values), the script is the
   right starting point.

## Files

- `easy_setups.py` — generates the 5 easy variant tasks (Phase 5b easy)
  and defines the `variant_template` helper used by the medium + hard
  scripts. Idempotent — re-running rewrites the JSON files.
- `medium_setups.py` — 9 medium variant tasks. Imports `easy_setups`.
- `hard_setups.py` — 6 hard variant tasks. Imports `easy_setups`. Includes
  inline Python verifiers for the BS / FFT / N-body / CRT expected values.
- `hide_expected.py` — **live, not an archive** (suite v4.1). Keeps the
  answer out of the agent's working directory for the 132 stdin/stdout
  variant units: `setup` writes the sample input only, and `pre_validate`
  installs the sample plus hidden cases and the matching `expected.txt`,
  computed from `evals/refs/<stem>.py`. Idempotent; `--check` exits 1 if a
  committed spec differs from what it would write
  (`tests/test_eval_hidden_cases.py` runs that). Safe to re-run in the repo.
- `patch_zig_tasks.py` — one-shot patch for the 13 existing Zig variant
  task fields in Phase A (replaces the broken pre-`&fr.interface` template
  with the corrected guidance). Already applied; preserved as a record.

## Re-running — DON'T (against the live tasks)

> **⚠️ The `*_setups.py` generators and `patch_zig_tasks.py` are v3.5-era archives
> (`hide_expected.py` is not: it is idempotent on the committed tasks). The committed task JSONs have
> since been v4-hardened, and two tasks the generators emit were pruned from
> the suite entirely. Re-running them in the repo would REVERT that hardening
> and resurrect the pruned tasks** (verified 2026-07-17: 19 of the generated
> files diverge from the committed v4 versions).

The committed JSONs in `evals/tasks/` are authoritative. To re-derive or
tweak a task, run the generator into a scratch copy and diff:

```bash
mkdir -p /tmp/regen/evals && cp -r evals/scripts evals/tasks /tmp/regen/evals/
(cd /tmp/regen && python3 evals/scripts/easy_setups.py)   # writes to the copy
diff -u /tmp/regen/evals/tasks/32_dot_product.json evals/tasks/32_dot_product.json
```

Port only the piece you need into the committed JSON, then run
`bash tests/test_scripts.sh` and `python3 tests/audit_variants.py`.
