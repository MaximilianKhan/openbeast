# evals/decisions — decision-quality harness for beast-instinct

This is its own namespace. It never touches `evals/SUITE_VERSION` or the v4
cache hash, and `evals/run_eval.py` never imports it (a boundary test checks
this). Instinct is an eval **subject** here. It is never a grader.

| File | Purpose |
|---|---|
| `run.py` | Evaluate, fit `linear` (`--fit-linear`), calibrate (`--calibrate`), and write gate records (`--gate`) |
| `metrics.py` | Accuracy, macro-F1, NLL, Brier, ECE (equal-mass bins), AURC, Wilson, exact McNemar and bootstrap. Stdlib only. |
| `loadgen.py` | Open-loop Poisson load; its `p95_ms` feeds `latency_p95_ms@load` |
| `<decision>/` | `{train,calib,test,ood,adversarial}.jsonl`, `MANIFEST.toml` (sha pins, status) and `README.md` (labelling rules) |
| `<decision>/calib/`, `gates/`, `linear/` | Records keyed by `decision_hash[:16]`. A gate record counts only once committed. |

The harness enforces three rules, and tests pin each one:
- `test` and `ood` never contain synthetic rows.
- Splits are by source `group`, never by row.
- The MANIFEST sha pins must match the files.

A gate fails closed when it has no loadgen report, when a split is below
`min_n`, or when there is no calibration record.

Runs write `.run/instinct/eval/<decision>/<ts>/{report.json,report.txt,samples.jsonl}`.

**Current data status:**
- `router.spawn_intent` is a **SEED**: mostly synthetic, and far below the gate floors.
- `hydra.task_class` is a scaffold with no rows yet.

Neither set supports a quality claim. See each decision's README for how it
grows into a gate set.
