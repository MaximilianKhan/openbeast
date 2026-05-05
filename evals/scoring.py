#!/usr/bin/env python3
"""
Scoring + leaderboard for the eval harness.

Two metrics, reported as separate columns plus a composite:

  CORRECTNESS  — difficulty-weighted pass rate (0-100)
                 weights: easy=1, medium=3, hard=5
                 max points on the 30-task suite: 3+42+65 = 110

  SPEED        — average speed factor on PASSED tasks (0-100)
                 factor = max(0, 1 - elapsed / time_budget)
                 budgets: easy=30s, medium=90s, hard=300s
                 (failed tasks don't drag speed; they're already 0 in correctness)

  COMPOSITE    — 0.75 * correctness + 0.25 * speed

Tie-breakers (in order): raw pass count, hard pass count, average elapsed time.
"""

import json
import os
from collections import defaultdict
from datetime import datetime

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(EVALS_DIR, "results")
LEADERBOARD_PATH = os.path.join(EVALS_DIR, "leaderboard.json")

DIFFICULTY_WEIGHTS = {"easy": 1, "medium": 3, "hard": 5}
TIME_BUDGETS = {"easy": 30, "medium": 90, "hard": 300}

CORRECTNESS_WEIGHT = 0.75
SPEED_WEIGHT = 0.25


def compute_correctness(tasks: list[dict]) -> tuple[float, dict]:
    """Returns (score 0-100, per-difficulty breakdown)."""
    earned = 0
    total = 0
    by_diff = defaultdict(lambda: {"earned": 0, "total": 0, "passed": 0, "count": 0})
    for t in tasks:
        diff = t.get("difficulty", "medium")
        w = DIFFICULTY_WEIGHTS.get(diff, 3)
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


def compute_composite(correctness: float, speed: float) -> float:
    return round(CORRECTNESS_WEIGHT * correctness + SPEED_WEIGHT * speed, 2)


def score_run(results: dict) -> dict:
    """Compute scores for a single eval run. Returns dict suitable for the
    leaderboard."""
    tasks = results.get("tasks", [])
    correctness, breakdown = compute_correctness(tasks)
    speed = compute_speed(tasks)
    composite = compute_composite(correctness, speed)

    passed = sum(1 for t in tasks if t.get("passed"))
    hard_passed = sum(1 for t in tasks if t.get("passed") and t.get("difficulty") == "hard")
    elapsed_total = sum(t.get("elapsed_seconds", 0) for t in tasks)

    return {
        "model": results.get("model", "unknown"),
        "model_slug": results.get("model_slug", "unknown"),
        "timestamp": results.get("timestamp"),
        "gpu": results.get("gpu", {}),
        "correctness": correctness,
        "speed": speed,
        "composite": composite,
        "tasks_total": len(tasks),
        "tasks_passed": passed,
        "tasks_hard_passed": hard_passed,
        "elapsed_total_seconds": round(elapsed_total, 1),
        "breakdown": breakdown,
    }


def rank_key(entry: dict) -> tuple:
    """Sort key for leaderboard (descending). Higher composite wins; ties
    broken by raw pass count, then hard passes, then speed (lower elapsed)."""
    return (
        -entry["composite"],
        -entry["tasks_passed"],
        -entry["tasks_hard_passed"],
        entry["elapsed_total_seconds"],
    )


def load_leaderboard(path: str = LEADERBOARD_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    return data.get("entries", []) if isinstance(data, dict) else []


def update_leaderboard(score_entry: dict, path: str = LEADERBOARD_PATH) -> list[dict]:
    """Insert or replace the entry for this model_slug. Returns sorted list."""
    entries = load_leaderboard(path)
    entries = [e for e in entries if e.get("model_slug") != score_entry["model_slug"]]
    entries.append(score_entry)
    entries.sort(key=rank_key)
    payload = {"updated_at": datetime.now().isoformat(), "entries": entries}
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    return entries


def format_leaderboard(entries: list[dict]) -> str:
    """Pretty-print as a table."""
    if not entries:
        return "(leaderboard is empty)"

    header = f"{'#':>2}  {'MODEL':<32}  {'COMP':>6}  {'CORR':>6}  {'SPEED':>6}  {'PASS':>7}  {'HARD':>5}  {'TIME':>8}"
    sep = "-" * len(header)
    lines = [header, sep]
    for i, e in enumerate(entries, 1):
        model = e.get("model", "?")[:32]
        comp = f"{e['composite']:.1f}"
        corr = f"{e['correctness']:.1f}"
        speed = f"{e['speed']:.1f}"
        passed = f"{e['tasks_passed']}/{e['tasks_total']}"
        hard = str(e["tasks_hard_passed"])
        elapsed = f"{e['elapsed_total_seconds']:.0f}s"
        lines.append(f"{i:>2}  {model:<32}  {comp:>6}  {corr:>6}  {speed:>6}  {passed:>7}  {hard:>5}  {elapsed:>8}")
    return "\n".join(lines)


def score_results_file(path: str) -> dict:
    """Load a single eval-*.json file and score it."""
    with open(path) as f:
        results = json.load(f)
    return score_run(results)


def main():
    """CLI: rescore all results files and rebuild the leaderboard."""
    import argparse
    parser = argparse.ArgumentParser(description="Score eval results + manage leaderboard")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild leaderboard from all eval-*.json files in results/")
    parser.add_argument("--score", help="Score a single eval-*.json file and print")
    parser.add_argument("--show", action="store_true", help="Print current leaderboard")
    args = parser.parse_args()

    if args.score:
        entry = score_results_file(args.score)
        print(json.dumps(entry, indent=2))
        return

    if args.rebuild:
        # Latest result per model_slug wins
        by_model: dict[str, dict] = {}
        for path in sorted(os.listdir(RESULTS_DIR)) if os.path.isdir(RESULTS_DIR) else []:
            if not path.startswith("eval-") or not path.endswith(".json"):
                continue
            full = os.path.join(RESULTS_DIR, path)
            entry = score_results_file(full)
            slug = entry.get("model_slug", "unknown")
            existing = by_model.get(slug)
            if not existing or entry.get("timestamp", "") > existing.get("timestamp", ""):
                by_model[slug] = entry
        entries = sorted(by_model.values(), key=rank_key)
        payload = {"updated_at": datetime.now().isoformat(), "entries": entries}
        with open(LEADERBOARD_PATH, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"Rebuilt leaderboard from {len(entries)} model(s).")

    if args.show or args.rebuild or not (args.score or args.rebuild):
        entries = load_leaderboard()
        print(format_leaderboard(entries))


if __name__ == "__main__":
    main()
