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
- `patch_zig_tasks.py` — one-shot patch for the 13 existing Zig variant
  task fields in Phase A (replaces the broken pre-`&fr.interface` template
  with the corrected guidance). Already applied; preserved as a record.

## Re-running

These are idempotent (writing to the same JSON paths). Safe to re-run if
you need to regenerate after a manual edit. Always run
`tests/test_scripts.sh` and `tests/audit_variants.py` afterwards to
confirm nothing broke.

```bash
python3 evals/scripts/easy_setups.py
python3 evals/scripts/medium_setups.py
python3 evals/scripts/hard_setups.py
bash tests/test_scripts.sh
python3 tests/audit_variants.py
```
