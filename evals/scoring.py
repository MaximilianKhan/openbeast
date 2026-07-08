#!/usr/bin/env python3
"""
Scoring + leaderboard for the eval harness.

Two SEPARATE metrics — accuracy is primary, speed is secondary informational:

  ACCURACY     — difficulty-weighted pass rate (0-100)
                 weights: easy=1, medium=1.5, hard=2

  SPEED        — average speed factor on PASSED tasks (0-100)
                 factor = max(0, 1 - elapsed / time_budget)
                 budgets: easy=30s, medium=90s, hard=300s
                 (failed tasks don't drag speed; they're already 0 in accuracy)

  COMPOSITE    — 0.75 * accuracy + 0.25 * speed (kept for backwards compat,
                 shown as a derived column but NOT the ranking key)

Ranking: accuracy first (this is what discriminates models), speed as
tie-breaker, then hard pass count, then total elapsed.
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


def _entry_weight(t: dict) -> float:
    """Difficulty weight for a single task or variant entry. Variants
    fractionally split their base task's weight: weight = DIFFICULTY_WEIGHTS[diff]
    / variant_count. Legacy entries with no variant_count default to 1, so the
    math is unchanged for the existing 144 single-variant tasks."""
    diff = t.get("difficulty", "medium")
    base = DIFFICULTY_WEIGHTS.get(diff, 3)
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
        "gpu": results.get("gpu", {}),
        "inference_engine": results.get("inference_engine", {}),
        "runtime": results.get("runtime", {}),
        "accuracy": accuracy,
        "speed": speed,
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
    """Sort key for leaderboard (descending). Accuracy is PRIMARY because
    speeds cluster too tightly to discriminate well. Tie-breakers: raw pass
    count, hard pass count, then speed (higher = better)."""
    return (
        -entry["accuracy"],
        -entry["tasks_passed"],
        -entry["tasks_hard_passed"],
        -entry["speed"],
    )


def entry_host_id(entry: dict) -> str:
    """Return the host_id of an entry. Falls back to `gpu.name` for legacy
    entries that predate multi-host support, or 'unknown-host' if no GPU
    info."""
    gpu = entry.get("gpu", {})
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


def update_leaderboard(score_entry: dict, path: str = LEADERBOARD_PATH) -> list[dict]:
    """Insert or replace the entry for this (host_id, model_slug). Returns sorted list.
    Multi-host: results from different machines coexist in the same leaderboard."""
    entries = load_leaderboard(path)
    target = entry_dedup_key(score_entry)
    entries = [e for e in entries if entry_dedup_key(e) != target]
    entries.append(score_entry)
    entries.sort(key=rank_key)
    payload = {"updated_at": datetime.now().isoformat(), "entries": entries}
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
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
    """Pretty-print as a table. If show_host is True, include the host column."""
    if not entries:
        return "(leaderboard is empty)"

    if show_host:
        header = f"{'#':>2}  {'HOST':<22}  {'MODEL':<32}  {'SUITE':>5}  {'ACC':>6}  {'SPEED':>6}  {'COMP':>6}  {'PASS':>7}  {'HARD':>5}  {'TIME':>8}  {'TOKENS':>7}"
    else:
        header = f"{'#':>2}  {'MODEL':<32}  {'SUITE':>5}  {'ACCURACY':>9}  {'SPEED':>6}  {'COMP':>6}  {'PASS':>7}  {'HARD':>5}  {'TIME':>8}  {'TOKENS':>7}"
    sep = "-" * len(header)
    lines = [header, sep]
    for i, e in enumerate(entries, 1):
        model = e.get("model", "?")[:32]
        suite = str(e.get("suite_version", "?"))[:5]
        accuracy = f"{e['accuracy']:.1f}"
        speed = f"{e['speed']:.1f}"
        comp = f"{e['composite']:.1f}"
        passed = f"{e['tasks_passed']}/{e['tasks_total']}"
        hard = str(e["tasks_hard_passed"])
        elapsed = f"{e['elapsed_total_seconds']:.0f}s"
        toks = _fmt_tokens(e.get("tokens_total", 0))
        if show_host:
            host = entry_host_id(e)[:22]
            lines.append(f"{i:>2}  {host:<22}  {model:<32}  {suite:>5}  {accuracy:>6}  {speed:>6}  {comp:>6}  {passed:>7}  {hard:>5}  {elapsed:>8}  {toks:>7}")
        else:
            lines.append(f"{i:>2}  {model:<32}  {suite:>5}  {accuracy:>9}  {speed:>6}  {comp:>6}  {passed:>7}  {hard:>5}  {elapsed:>8}  {toks:>7}")
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
    # Sort by best accuracy across any host
    def model_rank(slug):
        return -max(e["accuracy"] for e in by_model[slug].values())
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
        # Latest result per (host_id, model_slug) wins — keeps results from
        # different machines side by side.
        by_key: dict[tuple, dict] = {}
        for path in sorted(os.listdir(RESULTS_DIR)) if os.path.isdir(RESULTS_DIR) else []:
            if not path.startswith("eval-") or not path.endswith(".json"):
                continue
            full = os.path.join(RESULTS_DIR, path)
            entry = score_results_file(full)
            key = entry_dedup_key(entry)
            existing = by_key.get(key)
            if not existing or entry.get("timestamp", "") > existing.get("timestamp", ""):
                by_key[key] = entry
        entries = sorted(by_key.values(), key=rank_key)
        payload = {"updated_at": datetime.now().isoformat(), "entries": entries}
        with open(LEADERBOARD_PATH, "w") as f:
            json.dump(payload, f, indent=2)
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
