#!/usr/bin/env python3
"""v5 suite manifest (evals/suites/v5.json): format, loader, coverage floor.

A v5 manifest is NOT a fast-suite pin. It names every unit of the suite and
how a profile spends time on it; nothing is imputed:

  {suite: "v5", budget: {ref_seq_h, wall_h}, arms: {L0: {}, L1: {packs},
   L2: {packs, diag, escalate}}, coverage_floor: {cell_min, categories,
   languages}, units: [{id, task, lang, category, tier, role, est_s,
   max_wall_s, arms, stratum}]}

  role      "scored" (counts toward SCORE) or "probe" (zig probe units: run in
            every arm for the pack contrast, never scored)
  est_s     reference-sequential seconds (qwen-27b-mtp-q5-k-xl, 5090, --jobs 1);
            a time class used for scheduling and budgeting, never a weight
  max_wall_s  the unit's agent wall cap before timeout_scale
  arms      which arms RUN the unit; elsewhere its L0 row is shared
  stratum   "heldout" | "insample" | null — the pack-contrast strata

Unit ids. A manifest id is the effective unit id when the task file exists
(`159_ntt_convolution_f`). Task files for new bases are written separately
from the manifest, so an id that does not match is resolved by
(task number, language) against evals/tasks/ — an author may pick any slug
and any variant letter without breaking the manifest. Resolution is strict:
exactly one unit of that number and language, or the load refuses.

The coverage floor (12 categories x 6 languages, cell_min scored units per
cell) is checked on the manifest's own metadata, so a manifest that leaves a
cell empty is refused before any task file is read.

Usage:
  python3 evals/suite_manifest.py            # summary + floor check (+ resolution)
  python3 evals/suite_manifest.py --json     # resolved units as JSON
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
SUITES_DIR = os.path.join(EVALS_DIR, "suites")

# Variant letters by convention (skills/eval-variant-porter). Not every v4 task
# follows it (122_gemm_blocked letters a..e from go), which is why resolution
# goes by language, never by letter.
LANG_LETTERS = {"a": "python", "b": "go", "c": "c", "d": "cpp", "e": "rust", "f": "zig"}
SIX_LANGS = ("python", "go", "c", "cpp", "rust", "zig")
COMPILED_LANGS = frozenset({"go", "c", "cpp", "rust", "zig"})
_LANG_ALIASES = {"py": "python", "python3": "python", "golang": "go", "c++": "cpp",
                 "cxx": "cpp", "rs": "rust", "sh": "shell", "bash": "shell"}

# Per-unit agent wall caps by tier (brief §1), before timeout_scale.
TIER_WALL_S = {"easy": 300, "medium": 600, "hard": 900, "xl": 1500}
# Planning rates (R-s) for a unit with no est_s: v4 tier rates (§1).
TIER_EST_S = {"easy": 14, "medium": 35, "hard": 82, "xl": 330}
DEFAULT_ARMS = {"L0": {}, "L1": {"packs": True},
                "L2": {"packs": True, "diag": True, "escalate": True}}
ROLES = ("scored", "probe")
STRATA = ("heldout", "insample", None)

# The v4 task universe: base ids 01..162. Pins built from v4 runs
# (evals/suites/v4-fast.json) are checked against this universe only, so new
# v5 task files never read as "drift" of a frozen v4 pin.
V4_MAX_TASK = 162

# Zig API tutoring that v4 prompts carried and v5 bans (L0 is unassisted;
# zig guidance belongs to the treatment arms). Literal substrings.
ZIG_TUTORING_BANNED = ("&fr.interface", "init.io", "takeDelimiter")


def norm_lang(lang: str | None) -> str:
    lang = (lang or "python").strip().lower()
    return _LANG_ALIASES.get(lang, lang)


def task_number(task_id: str) -> int | None:
    m = re.match(r"^0*(\d+)_", str(task_id) + "_")
    return int(m.group(1)) if m else None


def tier_of(difficulty: str | None) -> str:
    d = (difficulty or "medium").strip().lower()
    return {"m": "medium", "h": "hard", "e": "easy", "x": "xl"}.get(d, d)


def default_wall_s(difficulty: str | None) -> int:
    return TIER_WALL_S.get(tier_of(difficulty), TIER_WALL_S["medium"])


def manifest_path(name: str) -> str:
    return os.path.join(SUITES_DIR, f"{name}.json")


def is_manifest(suite: dict) -> bool:
    """A v5-format manifest (vs a v4 fast-suite pin)."""
    return isinstance(suite.get("units"), list) and bool(suite["units"]) \
        and isinstance(suite["units"][0], dict)


def coverage_cells(units: list[dict]) -> Counter:
    """(category, language) -> count of SCORED units."""
    c: Counter = Counter()
    for u in units:
        if u.get("role", "scored") == "scored":
            c[(u.get("category"), norm_lang(u.get("lang") or u.get("language")))] += 1
    return c


def coverage_gaps(units: list[dict], floor: dict | None,
                  categories: list[str] | None = None,
                  languages: tuple = SIX_LANGS) -> list[str]:
    """Every (category, language) cell under the floor, as readable strings.
    `categories` defaults to the categories the units name; the floor's
    `categories` count is then checked too, so dropping a whole category is
    a gap, not a smaller grid."""
    if not floor:
        return []
    cell_min = int(floor.get("cell_min", 1))
    cells = coverage_cells(units)
    cats = sorted(categories or {cat for cat, _ in cells})
    gaps = []
    want_cats = floor.get("categories")
    if want_cats is not None and len(cats) < int(want_cats):
        gaps.append(f"only {len(cats)} categories covered, floor {want_cats}")
    langs = list(languages)[: int(floor.get("languages", len(languages)))]
    for cat in cats:
        for lang in langs:
            n = cells.get((cat, lang), 0)
            if n < cell_min:
                gaps.append(f"{cat} x {lang}: {n} scored unit(s) < {cell_min}")
    return gaps


def validate_manifest(man: dict) -> list[str]:
    """Structural problems with a manifest ([] = valid)."""
    errs = []
    if man.get("suite") is None:
        errs.append("no 'suite' name")
    arms = man.get("arms") or {}
    if not arms:
        errs.append("no arms")
    seen = set()
    for u in man.get("units", []):
        uid = u.get("id")
        if not uid:
            errs.append(f"unit without id: {u}")
            continue
        if uid in seen:
            errs.append(f"duplicate unit id {uid}")
        seen.add(uid)
        if u.get("role", "scored") not in ROLES:
            errs.append(f"{uid}: role {u.get('role')!r} not in {ROLES}")
        if u.get("stratum") not in STRATA:
            errs.append(f"{uid}: stratum {u.get('stratum')!r} not in {STRATA}")
        for a in u.get("arms", []):
            if a not in arms:
                errs.append(f"{uid}: arm {a!r} not declared")
        if not u.get("arms"):
            errs.append(f"{uid}: runs in no arm")
        if not isinstance(u.get("est_s", 0), (int, float)) or u.get("est_s", 0) < 0:
            errs.append(f"{uid}: bad est_s")
        if u.get("max_wall_s") is not None and u["max_wall_s"] <= 0:
            errs.append(f"{uid}: bad max_wall_s")
    gaps = coverage_gaps(man.get("units", []), man.get("coverage_floor"))
    errs += [f"coverage floor: {g}" for g in gaps]
    return errs


def _index_tasks(tasks: list[dict]) -> tuple[dict, dict]:
    by_id = {t["id"]: t for t in tasks}
    by_num_lang: dict = defaultdict(list)
    for t in tasks:
        n = task_number(t.get("base_id") or t["id"])
        if n is not None:
            by_num_lang[(n, norm_lang(t.get("language")))].append(t)
    return by_id, by_num_lang


def resolve_units(man: dict, tasks: list[dict], strict: bool = True) -> tuple[dict, list[str]]:
    """Manifest unit id -> effective task id, plus problems. A unit whose id
    is an effective id resolves to it; otherwise by (task number, language).
    strict: a missing or ambiguous unit is a problem (the caller refuses)."""
    by_id, by_nl = _index_tasks(tasks)
    out, problems = {}, []
    for u in man.get("units", []):
        uid = u["id"]
        if uid in by_id:
            t = by_id[uid]
            want = norm_lang(u.get("lang"))
            have = norm_lang(t.get("language"))
            if u.get("lang") and want != have:
                problems.append(f"{uid}: manifest says {want}, task file says {have}")
            out[uid] = uid
            continue
        cands = by_nl.get((u.get("task") if u.get("task") is not None else task_number(uid),
                           norm_lang(u.get("lang"))), [])
        if len(cands) == 1:
            out[uid] = cands[0]["id"]
        elif not cands:
            problems.append(f"{uid}: no task file provides task {u.get('task')} in {u.get('lang')}")
        else:
            problems.append(f"{uid}: ambiguous — {[c['id'] for c in cands]}")
    if strict and problems:
        return out, problems
    return out, problems


def load_manifest(name: str = "v5", tasks: list[dict] | None = None,
                  strict: bool = True) -> dict:
    """Load and validate a manifest. With `tasks` (run_eval.load_tasks(None)),
    resolve every unit to its effective id: the result carries
    `resolved` {manifest id: effective id} and `by_task` {effective id: unit}.
    Refuses (SystemExit) on a structural error, a coverage-floor gap, or —
    strict — an unresolvable unit."""
    path = manifest_path(name)
    if not os.path.isfile(path):
        raise SystemExit(f"unknown suite manifest {name!r} ({path})")
    with open(path) as f:
        man = json.load(f)
    if not is_manifest(man):
        raise SystemExit(f"{path} is not a v5-format manifest")
    errs = validate_manifest(man)
    if errs:
        raise SystemExit(f"suite manifest {name!r} refused:\n  " + "\n  ".join(errs[:20]))
    if tasks is not None:
        resolved, problems = resolve_units(man, tasks, strict=strict)
        if strict and problems:
            raise SystemExit(f"suite manifest {name!r} does not resolve against evals/tasks/ "
                             f"({len(problems)} problem(s)):\n  " + "\n  ".join(problems[:20]))
        man["resolved"] = resolved
        man["by_task"] = {resolved[u["id"]]: u for u in man["units"] if u["id"] in resolved}
        man["resolution_problems"] = problems
    return man


def scored_units(man: dict) -> list[dict]:
    return [u for u in man["units"] if u.get("role", "scored") == "scored"]


def arm_units(man: dict, arm: str) -> list[dict]:
    return [u for u in man["units"] if arm in u.get("arms", [])]


def zig_tutoring_violations(text: str) -> list[str]:
    """Banned zig-API tutoring phrases present in a task prompt."""
    return [p for p in ZIG_TUTORING_BANNED if p in (text or "")]


def summary(man: dict) -> str:
    sc = scored_units(man)
    probes = [u for u in man["units"] if u.get("role") == "probe"]
    lines = [f"suite {man['suite']}: {len(sc)} scored units over "
             f"{len({u['task'] for u in sc})} base tasks + {len(probes)} probe units"]
    for arm in man["arms"]:
        us = arm_units(man, arm)
        lines.append(f"  arm {arm:<3} runs {len(us):>3} units, "
                     f"{sum(u.get('est_s', 0) for u in us) / 3600:.2f} R-s h")
    b = man.get("budget") or {}
    lines.append(f"  budget: ref_seq_h {b.get('ref_seq_h')} / wall_h {b.get('wall_h')}")
    return "\n".join(lines)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", default="v5")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-resolve", action="store_true",
                    help="check the manifest alone (no evals/tasks/ resolution)")
    args = ap.parse_args()
    tasks = None
    if not args.no_resolve:
        sys.path.insert(0, EVALS_DIR)
        import run_eval  # noqa: PLC0415
        tasks = run_eval.load_tasks(None)
    man = load_manifest(args.suite, tasks=tasks, strict=False)
    if args.json:
        print(json.dumps({"resolved": man.get("resolved"),
                          "problems": man.get("resolution_problems")}, indent=1))
        return 0
    print(summary(man))
    probs = man.get("resolution_problems") or []
    if probs:
        print(f"  UNRESOLVED ({len(probs)}):")
        for p in probs:
            print(f"    {p}")
        return 1
    if tasks is not None:
        print("  every unit resolves against evals/tasks/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
