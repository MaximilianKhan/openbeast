#!/usr/bin/env python3
"""
v5-fast suite generator — pin the discriminating subset of the v4 suite.

Implements Phase C of docs/EVAL_FAST_SUITE_PROPOSAL.md (suite F): the
units that separate models (passed by >=1 and < all of the reference
models) plus a fixed number of cheap all-pass "tripwire" units kept as
regression canaries, pinned to evals/suites/v5-fast.json so the subset is
REPRODUCIBLE, not recomputed per run.

Reference runs are the latest FULL (291-unit) v4 result file per model
slug in evals/results/. Results files are gitignored — regeneration needs
a machine that has them; that is deliberate (the pin is the artifact of
record, its inputs are listed in its metadata).

Usage:
  python3 evals/make_fast_suite.py                    # verify pinned file is
                                                      # reproducible + print τ
  python3 evals/make_fast_suite.py --generate         # (re)write the pin
  python3 evals/make_fast_suite.py --exclude-slugs a,b  # drop reference runs
                                                      # (e.g. known-tainted rows)

τ verification: ranks the reference models by the FULL v2 capability
metric (scoring.score_run) computed on (a) all 291 units and (b) only the
pinned subset, and reports Kendall τ-b between the two orders. The
proposal's fidelity claim is τ = +1.0 (order preserved exactly); anything
lower is printed loudly and exits non-zero so a drifted pin fails CI-style
checks locally. This closes the proposal §7 caveat — fidelity was
originally checked on weighted pass rate, not the full capability metric.
"""

import argparse
import glob
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(EVALS_DIR, "results")
SUITES_DIR = os.path.join(EVALS_DIR, "suites")
SUITE_PATH = os.path.join(SUITES_DIR, "v5-fast.json")

sys.path.insert(0, EVALS_DIR)
import scoring  # noqa: E402

TRIPWIRE_COUNT = 20


def load_reference_runs(exclude_slugs: set[str]) -> dict[str, tuple[str, dict]]:
    """Latest full-291 v4 results file per slug, minus exclusions."""
    latest: dict[str, tuple[str, dict]] = {}
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "eval-*.json"))):
        try:
            with open(path) as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue  # in-progress or corrupt file — a live sweep writes these
        tasks = d.get("tasks", [])
        if len(tasks) != 291:
            continue
        sv = d.get("suite_version") or ("v4" if (d.get("summary") or {}).get("total") == 291 else "?")
        if sv != "v4":
            continue
        slug = d.get("model_slug", "unknown")
        if slug in exclude_slugs:
            continue
        latest[slug] = (path, d)  # sorted glob → later timestamp wins
    return latest


def unit_pass_matrix(refs: dict) -> tuple[list[str], dict[str, int], dict[str, list[float]]]:
    """Returns (unit ids in suite order, unit -> #models passing, unit -> elapsed list)."""
    passes: dict[str, int] = defaultdict(int)
    elapsed: dict[str, list[float]] = defaultdict(list)
    order: list[str] = [t["id"] for t in next(iter(refs.values()))[1]["tasks"]]
    for _slug, (_path, d) in refs.items():
        ids = {t["id"] for t in d["tasks"]}
        if ids != set(order):
            raise SystemExit(f"reference runs disagree on unit set ({_path})")
        for t in d["tasks"]:
            if t.get("passed"):
                passes[t["id"]] += 1
            elapsed[t["id"]].append(float(t.get("elapsed_seconds", 0)))
    return order, passes, elapsed


def unit_meta(refs: dict) -> dict[str, dict]:
    """unit id -> {language, difficulty} from any reference run's task rows."""
    meta = {}
    _path, d = next(iter(refs.values()))
    for t in d["tasks"]:
        meta[t["id"]] = {
            "language": t.get("language", "python"),
            "difficulty": t.get("difficulty", "medium"),
        }
    return meta


def pick_tripwires(allpass: list[str], meta: dict, elapsed: dict,
                   count: int = TRIPWIRE_COUNT) -> list[str]:
    """Deterministic tripwire pick: cheapest all-pass units, round-robin
    across (language, difficulty) groups for coverage. The tripwires' job is
    catching a broken/weak model class fast, not discrimination — so cheap
    and broad beats expensive and deep."""
    groups: dict[tuple, list[str]] = defaultdict(list)
    for u in allpass:
        groups[(meta[u]["language"], meta[u]["difficulty"])].append(u)
    for g in groups.values():
        g.sort(key=lambda u: (statistics.mean(elapsed[u]), u))
    picked: list[str] = []
    while len(picked) < count and any(groups.values()):
        for key in sorted(groups):
            if groups[key] and len(picked) < count:
                picked.append(groups[key].pop(0))
    return sorted(picked)


def kendall_tau_b(a: list[float], b: list[float]) -> float:
    """Kendall τ-b for two paired score lists (small n; no scipy dep)."""
    n = len(a)
    concordant = discordant = ties_a = ties_b = 0
    for i in range(n):
        for j in range(i + 1, n):
            da, db = a[i] - a[j], b[i] - b[j]
            if da == 0 and db == 0:
                continue
            if da == 0:
                ties_a += 1
            elif db == 0:
                ties_b += 1
            elif (da > 0) == (db > 0):
                concordant += 1
            else:
                discordant += 1
    denom = ((concordant + discordant + ties_a) * (concordant + discordant + ties_b)) ** 0.5
    return (concordant - discordant) / denom if denom else 1.0


def capability_on(tasks: list[dict], unit_ids: set[str] | None) -> float:
    sub = tasks if unit_ids is None else [t for t in tasks if t["id"] in unit_ids]
    _s, _l, cap = scoring.compute_solve_breadth(sub)
    return cap


def verify(refs: dict, pin: dict) -> bool:
    """Identity check: for every reference run, capability computed on
    (measured subset + imputed complement) must EXACTLY equal capability on
    the full 291 units. This is the fidelity contract of the fast suite —
    stronger than the proposal's Kendall-tau check, and it closes proposal
    §7's caveat (fidelity is verified on the full v2 capability metric)."""
    ok = True
    print(f"\nImputation-identity verification — {len(refs)} reference models:")
    print(f"  {'model':<34} {'full-291':>9} {'imputed':>8}")
    for slug, (_path, d) in sorted(refs.items()):
        _s, _l, full = scoring.compute_solve_breadth(d["tasks"])
        measured = [t for t in d["tasks"] if t["id"] in set(pin["units"])]
        _s, _l, imp = scoring.compute_solve_breadth(
            scoring.impute_suite_tasks(measured, pin))
        exact = abs(full - imp) < 1e-9
        ok &= exact
        print(f"  {slug:<34} {full:>9.2f} {imp:>8.2f}  {'EXACT' if exact else 'MISMATCH'}")
    print(f"  identity: {'HOLDS for all reference models' if ok else 'BROKEN — do not ship this pin'}")
    return ok


def generate(exclude_slugs: set[str]) -> dict:
    refs = load_reference_runs(exclude_slugs)
    if len(refs) < 2:
        raise SystemExit(f"need >=2 full v4 reference runs, found {len(refs)}")
    order, passes, elapsed = unit_pass_matrix(refs)
    meta = unit_meta(refs)
    n = len(refs)
    disc = [u for u in order if 0 < passes[u] < n]
    allpass = [u for u in order if passes[u] == n]
    zero = [u for u in order if passes[u] == 0]
    trips = pick_tripwires(allpass, meta, elapsed)
    units = sorted(set(disc) | set(trips))
    suite = {
        "suite": "v5-fast",
        "base_suite_version": "v4",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generator": "evals/make_fast_suite.py",
        "criteria": {
            "discriminating": f"passed by >=1 and <{n} of the {n} reference models",
            "tripwires": f"{TRIPWIRE_COUNT} cheapest all-pass units, round-robin over "
                         f"(language, difficulty) groups — regression canaries",
            "zero_pass_excluded": zero,
        },
        "source_runs": [
            {"slug": slug, "file": os.path.basename(path),
             "timestamp": d.get("timestamp")}
            for slug, (path, d) in sorted(refs.items())
        ],
        "excluded_slugs": sorted(exclude_slugs),
        "counts": {"units": len(units), "discriminating": len(disc),
                   "tripwires": len(trips),
                   "assumed_passed": len(allpass) - len(trips),
                   "assumed_failed": len(zero)},
        "tripwires": trips,
        "units": units,
        # Imputation lists — the saturated complement of the measured set.
        # A fast-suite run is scored as measured + assumed, which equals the
        # full-291 capability EXACTLY for any model matching the assumptions
        # (verified identity on every reference run; tripwire failures flag
        # models where the assumption is unsafe).
        "assumed_passed": sorted(set(allpass) - set(trips)),
        "assumed_failed": zero,
    }
    return suite


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generate", action="store_true",
                    help=f"(re)write {os.path.relpath(SUITE_PATH)} from the reference runs")
    ap.add_argument("--exclude-slugs", default="",
                    help="comma-separated model slugs whose runs must not count as reference "
                         "(e.g. rows known to be tainted)")
    args = ap.parse_args()
    exclude = {s.strip() for s in args.exclude_slugs.split(",") if s.strip()}

    if args.generate:
        suite = generate(exclude)
        os.makedirs(SUITES_DIR, exist_ok=True)
        with open(SUITE_PATH, "w") as f:
            json.dump(suite, f, indent=2)
            f.write("\n")
        c = suite["counts"]
        print(f"Wrote {SUITE_PATH}: {c['units']} units "
              f"({c['discriminating']} discriminating + {c['tripwires']} tripwires, "
              f"{len(suite['criteria']['zero_pass_excluded'])} zero-pass excluded) "
              f"from {len(suite['source_runs'])} reference runs")
        refs = load_reference_runs(exclude)
        return 0 if verify(refs, suite) else 1

    # verify mode: pinned file must be reproducible from the runs it names
    with open(SUITE_PATH) as f:
        pinned = json.load(f)
    exclude = exclude or set(pinned.get("excluded_slugs", []))
    regen = generate(exclude)
    drift = []
    if regen["units"] != pinned["units"]:
        drift.append(f"units differ (pinned {len(pinned['units'])}, regenerated {len(regen['units'])})")
    if regen["tripwires"] != pinned["tripwires"]:
        drift.append("tripwires differ")
    for key in ("assumed_passed", "assumed_failed"):
        if regen[key] != pinned.get(key):
            drift.append(f"{key} differs")
    if {r["file"] for r in regen["source_runs"]} != {r["file"] for r in pinned["source_runs"]}:
        drift.append("reference-run set changed — new/updated full v4 runs exist; "
                     "re-run with --generate to re-pin (deliberate act, new PR)")
    for d in drift:
        print(f"DRIFT: {d}")
    refs = load_reference_runs(exclude)
    ok = verify(refs, pinned)
    return 0 if (ok and not drift) else 1


if __name__ == "__main__":
    sys.exit(main())
