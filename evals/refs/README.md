# Reference implementations for variant tasks

These are the canonical-correct solutions used by `tests/audit_variants.py`
to verify each variant task's setup/validation pipeline works end-to-end.

## Naming

`{task_stem}.{ext}` where `task_stem` matches the dest_dir naming in the
task JSON (e.g., `pow2` for `31_is_power_of_two`, `mcpi` for
`38_monte_carlo_pi`). Mapping is in `tests/audit_variants.py::TARGETS`.

## Languages

`py | go | c | cpp | rs | zig`. Some tasks only have a subset (e.g.,
`122_gemm_blocked` skips Python — perf-flavored task).

## Coverage matrix

| Task tier | Languages with ref impl | Notes |
|---|---|---|
| Phase B easy + medium + hard (20 tasks) | All 6 (py/go/c/cpp/rs/zig) | Full 6-lang refs added 2026-05-07 |
| Phase A 13 existing (`19, 31, 51, 52, 61, 65, 73, 74, 122, 148, 155, 158, 159`) | **Zig only** | Other 5 langs were verified during Phase 4/4.5 rollout (prior commit) but the audit's ephemeral `/tmp/refs/` was never persisted. To re-audit those across all langs, the missing refs would need to be re-authored. The audit reports `[MISS]` (not `[FAIL]`) for absent refs. |

## Why these are committed

The audit needs them to run, and they represent significant cross-language
porting work — losing them to a `/tmp` cleanup would be expensive to
recreate. They also serve as documentation: this is what a passing
solution looks like in each language for each task.

## To run the audit

```bash
python3 tests/audit_variants.py                       # all tasks
python3 tests/audit_variants.py 31_is_power_of_two    # one task
```

Exit is 0 even on `[MISS]` (missing ref); only `[FAIL]` flags a real
problem (validation pipeline broken or ref impl wrong).
