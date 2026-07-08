## What & why

## Verification

- [ ] `./tests/run_tests.sh` green locally
- [ ] Docs updated in the same PR if behavior/surface changed
      (`docs/TOOLS.md` for tools, `docs/REFERENCE.md` for config,
      skill index regenerated if skills changed)
- [ ] Eval task changes: reference passes validation, wrong impl fails it,
      `python3 tests/audit_variants.py` green (for variants)

## Notes for the reviewer
