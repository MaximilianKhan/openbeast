#!/usr/bin/env python3
"""
Scoring + leaderboard for the eval harness.

PRIMARY metric is CAPABILITY (scoring v2, 2026-07-10) — a problem-solving-led
decomposition of the old fused accuracy score:

  PROBLEM_SOLVING   — difficulty-weighted fraction of BASE problems solved in
                      >=1 language. "Can the model crack the problem at all?"

  LANGUAGE_BREADTH  — among solved base problems, difficulty-weighted average
                      fraction of language variants passed. "Can it carry the
                      solution across languages?"

  CAPABILITY        — 0.75 * problem_solving + 0.25 * language_breadth (PRIMARY,
                      the ranking key). Problem-solving-led on purpose. Shown as
                      the SCORE column in the leaderboard readout.

  ACCURACY          — legacy v1 metric: difficulty-weighted pass rate over all
                      variant ENTRIES (weights easy=1, med=1.5, hard=2, each
                      variant = difficulty/variant_count). Retained as a column
                      for continuity; NO LONGER the ranking key.

  SPEED             — avg speed factor on PASSED tasks (0-100);
                      factor = max(0, 1 - elapsed / time_budget)
                      budgets: easy=30s, medium=90s, hard=300s.

  COMPOSITE         — 0.75*accuracy + 0.25*speed (legacy derived column).

Ranking: capability first, then problem_solving, then hard pass count, speed.

── SCORING CHANGELOG ────────────────────────────────────────────────────────
v1 (thru 2026-07-09): ranked by ACCURACY = difficulty-weighted pass rate over
   every language-variant entry. A base problem ported to 6 languages required
   passing all 6 for full credit; solving it in one language earned only 1/6.
   This FUSED two capabilities (problem-solving + language-porting) into one
   number and weighted them equally — so a model that was language-robust but
   solved fewer distinct problems could outrank a stronger problem-solver.
   (Concretely: NVFP4-27B, worst problem-solver / near-best polyglot, ranked
   ABOVE Qwopus on v1 purely on language redundancy.)

v2 (2026-07-10, Max's call): split into PROBLEM_SOLVING + LANGUAGE_BREADTH and
   rank by CAPABILITY = 0.75*solve + 0.25*breadth. Rationale — cracking a problem
   ONCE, in any language, is the scarce capability; porting that working
   solution across languages is an increasingly *automatable* problem (LSP
   feedback loops, MCP language servers, in-context translation). So we reward
   the model for genuinely SOLVING, and treat language breadth as real-but-
   secondary. Difficulty weights (1/1.5/2) and the per-base-task normalization
   are unchanged; singletons and full-6-language solves score identically to
   v1 — only the partial-language cases rebalance toward problem-solving.
   Applies to BOTH v4 and v3.5 rows (both carry variant metadata). The readout
   leads with per-tier % completed (EASY/MED/HARD, visual only), then ACC /
   SOLVE / LANG, then SCORE. Split started at 0.7/0.3, refined to 0.75/0.25 the
   same day (Max: weight solving a touch harder).
──────────────────────────────────────────────────────────────────────────────
"""

import json
import os
from collections import defaultdict
from datetime import datetime

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(EVALS_DIR, "results")
TASKS_DIR = os.path.join(EVALS_DIR, "tasks")
LEADERBOARD_PATH = os.path.join(EVALS_DIR, "leaderboard.json")

# Cache of task_id -> (category, subcategory) loaded lazily from tasks/*.json
_TASK_META_CACHE: dict | None = None


def _task_meta() -> dict:
    """Lazy-load category/subcategory metadata from evals/tasks/*.json."""
    global _TASK_META_CACHE
    if _TASK_META_CACHE is not None:
        return _TASK_META_CACHE
    meta = {}
    if os.path.isdir(TASKS_DIR):
        for fn in os.listdir(TASKS_DIR):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(TASKS_DIR, fn)) as f:
                    d = json.load(f)
                tid = d.get("id")
                if tid:
                    meta[tid] = (d.get("category"), d.get("subcategory"))
            except Exception:
                pass
    _TASK_META_CACHE = meta
    return meta

DIFFICULTY_WEIGHTS = {"easy": 1, "medium": 1.5, "hard": 2}
TIME_BUDGETS = {"easy": 30, "medium": 90, "hard": 300}

ACCURACY_WEIGHT = 0.75
SPEED_WEIGHT = 0.25

# Scoring v2 (2026-07-10): capability = SOLVE_WEIGHT*problem_solving
# + LANG_WEIGHT*language_breadth. Problem-solving-led (0.75/0.25 as of the
# 2026-07-10 refinement). See the scoring changelog in the module docstring.
SOLVE_WEIGHT = 0.75
LANG_WEIGHT = 0.25
SCORING_VERSION = "v2-solve-breadth"


def _entry_weight(t: dict) -> float:
    """Difficulty weight for a single task or variant entry. Variants
    fractionally split their base task's weight: weight = DIFFICULTY_WEIGHTS[diff]
    / variant_count. Legacy entries with no variant_count default to 1, so the
    math is unchanged for the existing 144 single-variant tasks."""
    diff = t.get("difficulty", "medium")
    # Unknown difficulty falls back to the medium weight (1.5). The old
    # fallback of 3 made a mislabeled task count HEAVIER than hard=2, which
    # silently skewed accuracy.
    base = DIFFICULTY_WEIGHTS.get(diff, 1.5)
    vc = t.get("variant_count") or 1
    return base / max(1, vc)


def compute_accuracy(tasks: list[dict]) -> tuple[float, dict]:
    """Returns (score 0-100, per-difficulty breakdown)."""
    earned = 0
    total = 0
    by_diff = defaultdict(lambda: {"earned": 0, "total": 0, "passed": 0, "count": 0})
    for t in tasks:
        diff = t.get("difficulty", "medium")
        w = _entry_weight(t)
        passed = bool(t.get("passed"))
        total += w
        by_diff[diff]["total"] += w
        by_diff[diff]["count"] += 1
        if passed:
            earned += w
            by_diff[diff]["earned"] += w
            by_diff[diff]["passed"] += 1
    score = (earned / total * 100) if total > 0 else 0.0
    return round(score, 2), dict(by_diff)


def compute_solve_breadth(tasks: list[dict]) -> tuple[float, float, float]:
    """Scoring v2 — two-axis capability decomposition. Returns
    (problem_solving, language_breadth, capability), all 0-100.

    Base problems are grouped by `base_id` (a singleton's base_id is its own id,
    giving a group of one). For a base problem of difficulty weight d with k
    language variants of which p passed:

      PROBLEM_SOLVING — d counts toward the earned total iff p >= 1 (solved in
                        at least one language). Difficulty-weighted.
      LANGUAGE_BREADTH — among base problems with p >= 1, earns d * (p / k):
                        the difficulty-weighted fraction of languages covered.

      CAPABILITY = SOLVE_WEIGHT * problem_solving + LANG_WEIGHT * language_breadth

    Singletons (k=1) and full-6-language solves score identically to the v1
    accuracy metric; only partial-language cases rebalance toward solving.
    """
    by_base: dict = defaultdict(list)
    for t in tasks:
        by_base[t.get("base_id") or t.get("id")].append(t)

    solve_earned = solve_total = 0.0
    lang_earned = lang_total = 0.0
    for ents in by_base.values():
        d = DIFFICULTY_WEIGHTS.get(ents[0].get("difficulty", "medium"), 1.5)
        # variant_count is the declared number of language ports; fall back to
        # the number of entries present so a partial run still divides sanely.
        k = ents[0].get("variant_count") or len(ents)
        p = sum(1 for x in ents if x.get("passed"))
        solve_total += d
        if p >= 1:
            solve_earned += d
            lang_total += d
            lang_earned += d * (p / max(1, k))

    solve = (solve_earned / solve_total * 100) if solve_total else 0.0
    lang = (lang_earned / lang_total * 100) if lang_total else 0.0
    capability = SOLVE_WEIGHT * solve + LANG_WEIGHT * lang
    return round(solve, 2), round(lang, 2), round(capability, 2)


def compute_category_breakdown(tasks: list[dict]) -> dict:
    """Per-category accuracy + speed. Returns {category: {accuracy, speed, count, passed, subcats}}."""
    by_cat = defaultdict(lambda: {
        "earned": 0.0, "total": 0.0, "passed": 0, "count": 0,
        "speed_factors": [], "subcats": defaultdict(lambda: {"passed": 0, "count": 0}),
    })
    meta = _task_meta()
    for t in tasks:
        cat = t.get("category")
        sub = t.get("subcategory")
        if cat is None or sub is None:
            # For variant entries, look up by base_id since /tasks/*.json carries the base meta
            lookup_id = t.get("base_id") or t.get("id")
            m_cat, m_sub = meta.get(lookup_id, (None, None))
            cat = cat or m_cat or "Uncategorized"
            sub = sub or m_sub or "—"
        diff = t.get("difficulty", "medium")
        w = _entry_weight(t)
        budget = TIME_BUDGETS.get(diff, 90)
        passed = bool(t.get("passed"))
        bucket = by_cat[cat]
        bucket["total"] += w
        bucket["count"] += 1
        bucket["subcats"][sub]["count"] += 1
        if passed:
            bucket["earned"] += w
            bucket["passed"] += 1
            bucket["subcats"][sub]["passed"] += 1
            elapsed = t.get("elapsed_seconds", budget)
            bucket["speed_factors"].append(max(0.0, 1.0 - elapsed / budget))
    out = {}
    for cat, b in by_cat.items():
        accuracy = (b["earned"] / b["total"] * 100) if b["total"] > 0 else 0.0
        speed = (sum(b["speed_factors"]) / len(b["speed_factors"]) * 100) if b["speed_factors"] else 0.0
        out[cat] = {
            "accuracy": round(accuracy, 2),
            "speed": round(speed, 2),
            "passed": b["passed"],
            "count": b["count"],
            "subcategories": {
                sub: {"passed": s["passed"], "count": s["count"]}
                for sub, s in sorted(b["subcats"].items())
            },
        }
    return out


def compute_language_breakdown(tasks: list[dict]) -> dict:
    """Per-language accuracy + speed across variant entries.

    Returns {language: {accuracy, speed, passed, count}}. Tasks with no
    `language` field (legacy single-variant Python tasks) are bucketed under
    "python" for cleaner UX. If the result file has no variants at all, the
    only key is "python".
    """
    by_lang = defaultdict(lambda: {
        "earned": 0.0, "total": 0.0, "passed": 0, "count": 0,
        "speed_factors": [],
    })
    for t in tasks:
        lang = t.get("language") or "python"
        diff = t.get("difficulty", "medium")
        w = _entry_weight(t)
        budget = TIME_BUDGETS.get(diff, 90)
        passed = bool(t.get("passed"))
        bucket = by_lang[lang]
        bucket["total"] += w
        bucket["count"] += 1
        if passed:
            bucket["earned"] += w
            bucket["passed"] += 1
            elapsed = t.get("elapsed_seconds", budget)
            bucket["speed_factors"].append(max(0.0, 1.0 - elapsed / budget))
    out = {}
    for lang, b in by_lang.items():
        accuracy = (b["earned"] / b["total"] * 100) if b["total"] > 0 else 0.0
        speed = (sum(b["speed_factors"]) / len(b["speed_factors"]) * 100) if b["speed_factors"] else 0.0
        out[lang] = {
            "accuracy": round(accuracy, 2),
            "speed": round(speed, 2),
            "passed": b["passed"],
            "count": b["count"],
        }
    return out


def compute_speed(tasks: list[dict]) -> float:
    """Average speed factor over passed tasks, scaled 0-100. Failed tasks are
    excluded — they're already counted in correctness."""
    factors = []
    for t in tasks:
        if not t.get("passed"):
            continue
        diff = t.get("difficulty", "medium")
        budget = TIME_BUDGETS.get(diff, 90)
        elapsed = t.get("elapsed_seconds", budget)
        factor = max(0.0, 1.0 - elapsed / budget)
        factors.append(factor)
    if not factors:
        return 0.0
    return round(sum(factors) / len(factors) * 100, 2)


def compute_composite(accuracy: float, speed: float) -> float:
    return round(ACCURACY_WEIGHT * accuracy + SPEED_WEIGHT * speed, 2)


def current_suite_version() -> str:
    """The suite version this checkout runs (from evals/SUITE_VERSION)."""
    try:
        with open(os.path.join(EVALS_DIR, "SUITE_VERSION")) as f:
            return f.read().strip() or "unknown"
    except OSError:
        return "unknown"


# Expected effective-unit counts per suite version. Entries whose suite has
# no expected count here (legacy runs) are treated as full.
SUITE_EXPECTED_UNITS = {"v4": 291, "v3.5": 323}


def _suite_version(results: dict) -> str:
    """Suite version for a run. Prefer the recorded field (new runs write it);
    otherwise infer from the effective-unit count so `--rebuild` from older
    result files (which predate the field) still tags rows correctly instead
    of flipping everything to 'unknown'. 291 units = v4, 323 = v3.5."""
    sv = results.get("suite_version")
    if sv:
        return sv
    total = (results.get("summary") or {}).get("total")
    if total == 291:
        return "v4"
    if total == 323:
        return "v3.5"
    return "legacy" if total else "unknown"


def score_run(results: dict) -> dict:
    """Compute scores for a single eval run. Returns dict suitable for the
    leaderboard."""
    tasks = results.get("tasks", [])
    accuracy, breakdown = compute_accuracy(tasks)
    problem_solving, language_breadth, capability = compute_solve_breadth(tasks)
    speed = compute_speed(tasks)
    composite = compute_composite(accuracy, speed)
    by_category = compute_category_breakdown(tasks)
    by_language = compute_language_breakdown(tasks)

    passed = sum(1 for t in tasks if t.get("passed"))
    hard_passed = sum(1 for t in tasks if t.get("passed") and t.get("difficulty") == "hard")
    elapsed_total = sum(t.get("elapsed_seconds", 0) for t in tasks)
    tokens_total = sum(t.get("tokens_total", 0) for t in tasks)
    tokens_prompt = sum(t.get("tokens_prompt", 0) for t in tasks)
    tokens_completion = sum(t.get("tokens_completion", 0) for t in tasks)

    return {
        "model": results.get("model", "unknown"),
        "model_slug": results.get("model_slug", "unknown"),
        "timestamp": results.get("timestamp"),
        "suite_version": _suite_version(results),
        # `or {}` (not .get default): a cache-only result file may store an
        # explicit null, and .get returns that None instead of the default.
        "gpu": results.get("gpu") or {},
        "inference_engine": results.get("inference_engine") or {},
        "runtime": results.get("runtime") or {},
        "scoring_version": SCORING_VERSION,
        # v2 primary metric + its two axes
        "capability": capability,
        "problem_solving": problem_solving,
        "language_breadth": language_breadth,
        # v1 legacy metric, retained as a column (no longer the ranking key)
        "accuracy": accuracy,
        "speed": speed,
        # effective decode throughput over the whole run (completion tokens /
        # wall-clock). Shown as the SPD (tok/s) column.
        "toks_per_sec": round(tokens_completion / elapsed_total, 1) if elapsed_total else 0.0,
        "composite": composite,
        "tasks_total": len(tasks),
        "tasks_passed": passed,
        "tasks_hard_passed": hard_passed,
        "elapsed_total_seconds": round(elapsed_total, 1),
        "tokens_total": tokens_total,
        "tokens_prompt": tokens_prompt,
        "tokens_completion": tokens_completion,
        "breakdown": breakdown,
        "by_category": by_category,
        "by_language": by_language,
    }


def rank_key(entry: dict) -> tuple:
    """Sort key for leaderboard (descending). CAPABILITY is PRIMARY (v2:
    0.7*problem_solving + 0.3*language_breadth). Legacy rows scored before v2
    have no `capability`/`problem_solving`; they fall back to `accuracy` (a
    comparable 0-100 scale) so mixed boards still sort sanely. Tie-breakers:
    problem_solving, hard pass count, then speed (higher = better)."""
    return (
        -entry.get("capability", entry.get("accuracy", 0)),
        -entry.get("problem_solving", entry.get("accuracy", 0)),
        -entry.get("tasks_hard_passed", 0),
        -entry.get("speed", 0),
    )


def entry_host_id(entry: dict) -> str:
    """Return the host_id of an entry. Falls back to `gpu.name` for legacy
    entries that predate multi-host support, or 'unknown-host' if no GPU
    info."""
    gpu = entry.get("gpu") or {}  # `or {}`: tolerate an explicit null
    return gpu.get("host_id") or gpu.get("name") or "unknown-host"


def entry_dedup_key(entry: dict) -> tuple:
    """The (host_id, model_slug) pair used to deduplicate leaderboard entries.
    Allows the same model to appear once per host."""
    return (entry_host_id(entry), entry.get("model_slug", "unknown"))


def load_leaderboard(path: str = LEADERBOARD_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    return data.get("entries", []) if isinstance(data, dict) else []


def _atomic_write_json(path: str, payload: dict) -> None:
    """tmp + os.replace so a crash mid-write never leaves a torn file."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def is_full_suite(entry: dict) -> bool:
    """True if a score entry covers its suite's full task set. Suites with
    no expected count (legacy) are treated as full."""
    expected = SUITE_EXPECTED_UNITS.get(entry.get("suite_version"))
    return expected is None or entry.get("tasks_total") == expected


def update_leaderboard(score_entry: dict, path: str = LEADERBOARD_PATH,
                       force: bool = False) -> list[dict]:
    """Insert or replace the entry for this (host_id, model_slug). Returns sorted list.
    Multi-host: results from different machines coexist in the same leaderboard.

    Partial-run guard: a v4 entry that doesn't cover the full 291 effective
    units is REFUSED (aborted/smoke runs would otherwise enter with accuracy
    computed over the subset that happened to complete). Pass force=True to
    override deliberately."""
    if (not force and score_entry.get("suite_version") == "v4"
            and score_entry.get("tasks_total") not in (291,)):
        print(f"REFUSED leaderboard update for {score_entry.get('model', '?')}: "
              f"v4 entry covers {score_entry.get('tasks_total')} tasks, expected 291. "
              f"Partial/aborted runs must not enter the leaderboard "
              f"(use force=True to override). Existing leaderboard unchanged.")
        return load_leaderboard(path)
    entries = load_leaderboard(path)
    target = entry_dedup_key(score_entry)
    entries = [e for e in entries if entry_dedup_key(e) != target]
    entries.append(score_entry)
    entries.sort(key=rank_key)
    _atomic_write_json(path, {"updated_at": datetime.now().isoformat(), "entries": entries})
    return entries


def _fmt_tokens(n: int) -> str:
    """Compact token count: 12345 → '12K', 1234567 → '1.2M'. '—' if unset."""
    if not n:
        return "—"
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n/1000:.0f}K"
    return f"{n/1_000_000:.1f}M"


def format_leaderboard(entries: list[dict], show_host: bool = False) -> str:
    """Pretty-print as a table. If show_host is True, include the host column.

    Rows are partitioned by suite version: entries from the CURRENT suite
    (evals/SUITE_VERSION) form the main ranked table; entries from older
    suites follow under a clearly labeled legacy section (their task sets
    differ, so their scores are not comparable to current rows). Within each
    partition, rank_key (accuracy-primary) ordering applies."""
    if not entries:
        return "(leaderboard is empty)"

    # Column flow (v2, per Max 2026-07-10): per-difficulty % completed (EASY/
    # MED/HARD — visual only) -> ACC (legacy weighted accuracy) + SOLVE
    # (problem-solving "true accuracy") + LANG (language breadth) -> SCORE
    # (capability = 0.75*SOLVE + 0.25*LANG, the end score) -> SPD (effective
    # decode tok/s) -> WALL (total wall-clock) -> PASS.
    if show_host:
        header = f"{'#':>2}  {'HOST':<18}  {'MODEL':<28}  {'SU':>4}  {'EASY':>5}  {'MED':>5}  {'HARD':>5}  {'ACC':>6}  {'SOLVE':>6}  {'LANG':>6}  {'SCORE':>6}  {'SPD':>6}  {'WALL':>7}  {'PASS':>7}"
    else:
        header = f"{'#':>2}  {'MODEL':<28}  {'SU':>4}  {'EASY':>5}  {'MED':>5}  {'HARD':>5}  {'ACC':>6}  {'SOLVE':>6}  {'LANG':>6}  {'SCORE':>6}  {'SPD':>6}  {'WALL':>7}  {'PASS':>7}"
    sep = "-" * len(header)

    def _f(v):  # format a possibly-missing 0-100 metric
        return f"{v:.1f}" if isinstance(v, (int, float)) else "—"

    def _tier(e: dict, tier: str) -> str:  # raw % completed for a difficulty tier
        b = (e.get("breakdown") or {}).get(tier) or {}
        c = b.get("count")
        return f"{100 * b['passed'] / c:.0f}" if c else "—"

    def _toks(e: dict) -> str:  # effective decode throughput, tok/s
        tc = e.get("toks_per_sec")
        if isinstance(tc, (int, float)):
            return f"{tc:.0f}"
        tcomp = e.get("tokens_completion") or 0  # fall back for pre-v2 rows
        el = e.get("elapsed_total_seconds") or 0
        return f"{tcomp / el:.0f}" if el else "—"

    def _wall(e: dict) -> str:  # total wall-clock, Hh MMm
        s = e.get("elapsed_total_seconds") or 0
        if not s:
            return "—"
        h, m = divmod(int(s) // 60, 60)
        return f"{h}h{m:02d}m" if h else f"{m}m"

    def _row(i: int, e: dict) -> str:
        model = e.get("model", "?")[:28]
        suite = str(e.get("suite_version", "?"))[:4]
        easy, med, hard = _tier(e, "easy"), _tier(e, "medium"), _tier(e, "hard")
        accuracy = _f(e.get("accuracy"))         # v1 legacy weighted accuracy
        solve = _f(e.get("problem_solving"))     # problem-solving ("true accuracy")
        lang = _f(e.get("language_breadth"))
        score = _f(e.get("capability"))          # end score (v2 primary)
        spd = _toks(e)                           # effective tok/s
        wall = _wall(e)                          # total wall-clock
        passed = f"{e.get('tasks_passed','?')}/{e.get('tasks_total','?')}"
        if show_host:
            host = entry_host_id(e)[:18]
            return f"{i:>2}  {host:<18}  {model:<28}  {suite:>4}  {easy:>5}  {med:>5}  {hard:>5}  {accuracy:>6}  {solve:>6}  {lang:>6}  {score:>6}  {spd:>6}  {wall:>7}  {passed:>7}"
        return f"{i:>2}  {model:<28}  {suite:>4}  {easy:>5}  {med:>5}  {hard:>5}  {accuracy:>6}  {solve:>6}  {lang:>6}  {score:>6}  {spd:>6}  {wall:>7}  {passed:>7}"

    cur = current_suite_version()
    current_rows = sorted((e for e in entries if str(e.get("suite_version")) == cur), key=rank_key)
    legacy_rows = sorted((e for e in entries if str(e.get("suite_version")) != cur), key=rank_key)

    lines = [header, sep]
    for i, e in enumerate(current_rows, 1):
        lines.append(_row(i, e))
    if legacy_rows:
        if current_rows:
            lines.append("")
        lines.append(f"LEGACY SUITES (task sets differ — not comparable to {cur} rows above)")
        lines.append(sep)
        for i, e in enumerate(legacy_rows, 1):
            lines.append(_row(i, e))
    lines.append("")
    lines.append("EASY/MED/HARD = % tasks passed per tier (visual).  "
                 "SOLVE = % base problems solved in >=1 lang.  LANG = % of language ports passed.")
    lines.append("SCORE = capability = 0.75*SOLVE + 0.25*LANG (ranking key).  "
                 "SPD = effective tok/s (completion tokens / wall-clock).  WALL = total run time.")
    return "\n".join(lines)


def format_host_comparison(entries: list[dict]) -> str:
    """Side-by-side comparison: one row per model, one column per host.
    Cells show accuracy (with pass count and elapsed seconds for context).
    Useful for comparing the SAME model across different GPU/system configs."""
    if not entries:
        return "(no entries to compare)"
    hosts = sorted({entry_host_id(e) for e in entries})
    if len(hosts) < 2:
        return f"Only one host present ({hosts[0] if hosts else 'none'}); nothing to compare."

    by_model: dict[str, dict[str, dict]] = {}
    for e in entries:
        slug = e.get("model_slug", "unknown")
        host = entry_host_id(e)
        by_model.setdefault(slug, {})[host] = e

    short = lambda s: s if len(s) <= 18 else s[:17] + "…"
    header = f"{'MODEL':<32}  " + "  ".join(f"{short(h):>22}" for h in hosts)
    lines = [header, "-" * len(header)]
    # Sort by best capability across any host (v2; falls back to accuracy for
    # legacy rows scored before capability existed).
    def model_rank(slug):
        return -max(e.get("capability", e.get("accuracy", 0)) for e in by_model[slug].values())
    for slug in sorted(by_model, key=model_rank):
        first_entry = next(iter(by_model[slug].values()))
        name = first_entry.get("model", slug)[:32]
        row = f"{name:<32}  "
        cells = []
        for h in hosts:
            if h in by_model[slug]:
                e = by_model[slug][h]
                cells.append(f"{e['accuracy']:>5.1f} ({e['tasks_passed']:>3}/{e['tasks_total']:<3} {e['elapsed_total_seconds']:>5.0f}s)")
            else:
                cells.append(f"{'—':>22}")
        lines.append(row + "  ".join(cells))
    return "\n".join(lines)


def score_results_file(path: str) -> dict:
    """Load a single eval-*.json file and score it."""
    with open(path) as f:
        results = json.load(f)
    return score_run(results)


def format_language_table(entries: list[dict]) -> str:
    """For each model, show per-language accuracy as a wide table."""
    if not entries:
        return "(no entries)"
    langs = sorted({l for e in entries for l in e.get("by_language", {}).keys()})
    if not langs:
        return "(no per-language data — re-run scoring with --rebuild after variants land)"
    header = f"{'MODEL':<28}" + "".join(f"  {l.upper():>14}" for l in langs)
    lines = [header, "-" * len(header)]
    for e in entries:
        bl = e.get("by_language", {})
        row = f"{e.get('model','?')[:28]:<28}"
        for l in langs:
            v = bl.get(l, {})
            if "accuracy" in v and v.get("count", 0) > 0:
                row += f"  {v['accuracy']:>5.1f} ({v['passed']:>2}/{v['count']:<2}) "
            else:
                row += f"  {'—':>14}"
        lines.append(row)
    return "\n".join(lines)


def format_category_table(entries: list[dict]) -> str:
    """For each model, show per-category accuracy as a wide table."""
    if not entries:
        return "(no entries)"
    cats = sorted({c for e in entries for c in e.get("by_category", {}).keys()})
    if not cats:
        return "(no per-category data — re-run scoring with --rebuild after categories were added)"
    # Truncate category names for the header
    short = lambda s: s if len(s) <= 14 else s[:13] + "…"
    header = f"{'MODEL':<28}" + "".join(f"  {short(c):>14}" for c in cats)
    lines = [header, "-" * len(header)]
    for e in entries:
        bc = e.get("by_category", {})
        row = f"{e.get('model','?')[:28]:<28}"
        for c in cats:
            v = bc.get(c, {})
            if "accuracy" in v and v.get("count", 0) > 0:
                row += f"  {v['accuracy']:>5.1f} ({v['passed']:>2}/{v['count']:<2}) "
            else:
                row += f"  {'—':>14}"
        lines.append(row)
    return "\n".join(lines)


def main():
    """CLI: rescore all results files and rebuild the leaderboard."""
    import argparse
    parser = argparse.ArgumentParser(description="Score eval results + manage leaderboard")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild leaderboard from all eval-*.json files in results/")
    parser.add_argument("--score", help="Score a single eval-*.json file and print")
    parser.add_argument("--show", action="store_true", help="Print current leaderboard")
    parser.add_argument("--by-category", action="store_true",
                        help="Show per-category accuracy breakdown for all leaderboard entries")
    parser.add_argument("--by-language", action="store_true",
                        help="Show per-language accuracy breakdown across variant tasks")
    parser.add_argument("--host", help="Filter leaderboard to a specific host_id")
    parser.add_argument("--compare-hosts", action="store_true",
                        help="Side-by-side comparison: one row per model, one column per host")
    parser.add_argument("--show-host", action="store_true",
                        help="Include host column in the leaderboard table")
    args = parser.parse_args()

    if args.score:
        entry = score_results_file(args.score)
        print(json.dumps(entry, indent=2))
        return

    if args.rebuild:
        # Best result per (host_id, model_slug) wins — keeps results from
        # different machines side by side. Preference order: full-suite runs
        # beat partials regardless of recency (a recent 5-task smoke run must
        # NOT replace a full 291-task row), then latest timestamp.
        def _preference(e: dict) -> tuple:
            return (is_full_suite(e), e.get("timestamp") or "")

        by_key: dict[tuple, dict] = {}
        for path in sorted(os.listdir(RESULTS_DIR)) if os.path.isdir(RESULTS_DIR) else []:
            if not path.startswith("eval-") or not path.endswith(".json"):
                continue
            full = os.path.join(RESULTS_DIR, path)
            entry = score_results_file(full)
            key = entry_dedup_key(entry)
            existing = by_key.get(key)
            if not existing or _preference(entry) > _preference(existing):
                by_key[key] = entry
        entries = sorted(by_key.values(), key=rank_key)
        _atomic_write_json(LEADERBOARD_PATH,
                           {"updated_at": datetime.now().isoformat(), "entries": entries})
        n_hosts = len({entry_host_id(e) for e in entries})
        print(f"Rebuilt leaderboard from {len(entries)} entries across {n_hosts} host(s).")

    if args.compare_hosts:
        entries = load_leaderboard()
        print(format_host_comparison(entries))
        return

    if args.by_category:
        entries = load_leaderboard()
        if args.host:
            entries = [e for e in entries if entry_host_id(e) == args.host]
        print(format_category_table(entries))
        return

    if args.by_language:
        entries = load_leaderboard()
        if args.host:
            entries = [e for e in entries if entry_host_id(e) == args.host]
        print(format_language_table(entries))
        return

    if args.show or args.rebuild or not (args.score or args.rebuild):
        entries = load_leaderboard()
        if args.host:
            entries = [e for e in entries if entry_host_id(e) == args.host]
        # Auto-show host column when multiple hosts are present
        n_hosts = len({entry_host_id(e) for e in entries})
        show_host = args.show_host or n_hosts > 1
        print(format_leaderboard(entries, show_host=show_host))


if __name__ == "__main__":
    main()
