## What & why

## Verification

CI is off, so these local runs are the gate: paste their results in the PR.

- [ ] `./tests/run_tests.sh` green locally
- [ ] Docs updated in the same PR if behavior/surface changed
      (`docs/TOOLS.md` for tools, `docs/REFERENCE.md` for config,
      `python3 scripts/generate-skill-index.py` if skills changed)
- [ ] Eval task changes: reference passes validation, wrong impl fails it,
      `python3 tests/audit_variants.py` green (for variants)
- [ ] `./scripts/eval-era.sh` prints the same hash before and after, or the
      PR says why the eval era moves

## Notes for the reviewer
