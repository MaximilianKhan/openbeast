#!/usr/bin/env python3
"""Tool-selection efficiency analyzer.

Reads `agents/logs/agent-*.jsonl` and reports per-model tool-use metrics.
The leaderboard ranks accuracy + speed; this view ranks *how* a model gets
to its answer. A model that hammers `write_file` when `edit_file` would do
is wasteful; one that re-reads the same file 5× isn't planning well; one
that loops through 30 iterations on an easy task is thrashing.

Metrics (per model, aggregated across all that model's logs):

  Tasks            — number of agent runs we found logs for
  iters_avg        — mean iteration count per task
  tool_calls_avg   — mean total tool invocations per task
  edit/write       — ratio of edit_file:write_file. >1 = targeted edits;
                     <1 = wasteful overwrites
  bash/task        — bash calls per task. Lower is more targeted (each bash
                     call has lower information density than a dedicated
                     read_file/edit_file)
  read_redundancy  — per-log average of (read_file calls / unique paths
                     read in that log). 1.0 = every read within a task was
                     a fresh path; 2.0 = each path re-read once on average
                     within a task; >2.0 indicates thrashing. Computed
                     per-log (not across a global path set) so two tasks
                     that each read the same file once don't count as a
                     re-read.
  agent_calls      — start_agent / start_skill_agent invocations. Currently
                     near-zero in eval sweeps (see WEAK_SPOT axis 3 and
                     the skills-don't-fire-spontaneously TODO note)

Usage:
  python3 evals/tool_efficiency.py
  python3 evals/tool_efficiency.py --logs-dir agents/logs/
  python3 evals/tool_efficiency.py --since 2026-05-06
  python3 evals/tool_efficiency.py --model qwen-27b-q5-k-xl   # drill into one model
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOGS = ROOT / "agents" / "logs"


def slugify(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown"


def parse_log(path: Path) -> dict | None:
    """Read one agent log file. Returns a dict of per-task aggregates, or
    None if the file doesn't have a 'start' record (incomplete log)."""
    model = None
    iters = 0
    tool_calls: list[tuple[str, dict]] = []  # (name, args)
    final_tokens = None
    try:
        with open(path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = d.get("type")
                if t == "start":
                    model = d.get("model")
                elif t == "iteration":
                    iters = max(iters, int(d.get("number", 0)))
                elif t == "tool_call":
                    tool_calls.append((d.get("name", "?"), d.get("args") or {}))
                elif t in ("max_iterations", "complete", "done"):
                    final_tokens = {
                        "prompt": d.get("tokens_prompt", 0),
                        "completion": d.get("tokens_completion", 0),
                        "total": d.get("tokens_total", 0),
                    }
    except FileNotFoundError:
        return None

    if model is None:
        return None  # incomplete log without start record

    return {
        "model": model,
        "iters": iters,
        "tool_calls": tool_calls,
        "tokens": final_tokens,
    }


def compute_metrics(per_log: list[dict]) -> dict:
    """Aggregate a list of per-log dicts into one metrics dict."""
    if not per_log:
        return {"tasks": 0}

    tasks = len(per_log)
    iters_total = 0
    tool_call_total = 0
    edit_count = 0
    write_count = 0
    bash_count = 0
    read_count = 0
    unique_paths = set()
    agent_count = 0
    other_count = 0

    read_ratios = []  # per-log (reads / unique paths); only logs that read

    for entry in per_log:
        iters_total += entry["iters"]
        log_reads = 0
        log_paths: set[str] = set()
        for name, args in entry["tool_calls"]:
            tool_call_total += 1
            if name == "edit_file":
                edit_count += 1
            elif name == "write_file":
                write_count += 1
            elif name == "bash":
                bash_count += 1
            elif name == "read_file":
                read_count += 1
                log_reads += 1
                p = (args or {}).get("path") or (args or {}).get("file_path")
                if p:
                    unique_paths.add(p)
                    log_paths.add(p)
            # "skill" is the current tool; list_skills/load_skill kept so
            # historical logs (pre-2026-07-08 collapse) still classify.
            elif name in ("start_agent", "start_skill_agent", "skill", "list_skills", "load_skill"):
                agent_count += 1
            else:
                other_count += 1
        if log_paths:
            read_ratios.append(log_reads / len(log_paths))

    # edit:write ratio — guard against div by zero
    if write_count == 0:
        edit_write = float("inf") if edit_count > 0 else 0.0
    else:
        edit_write = edit_count / write_count

    # read redundancy — averaged PER LOG (reads in log / unique paths in
    # log), not across a global path set. A global set structurally inflated
    # the metric: N tasks each reading the same fixture once looked like
    # N reads / 1 path = N× "redundancy" despite zero actual re-reads.
    if read_ratios:
        read_redundancy = sum(read_ratios) / len(read_ratios)
    else:
        read_redundancy = 0.0

    return {
        "tasks": tasks,
        "iters_avg": iters_total / tasks,
        "tool_calls_avg": tool_call_total / tasks,
        "edit_count": edit_count,
        "write_count": write_count,
        "edit_write_ratio": edit_write,
        "bash_per_task": bash_count / tasks,
        "read_count": read_count,
        "unique_read_paths": len(unique_paths),
        "read_redundancy": read_redundancy,
        "agent_calls": agent_count,
        "other_calls": other_count,
    }


def collect(logs_dir: Path, since: datetime | None = None,
            model_filter: str | None = None) -> dict[str, list[dict]]:
    """Walk logs_dir, parse each .jsonl, group by slugified model name."""
    by_model: dict[str, list[dict]] = defaultdict(list)
    if not logs_dir.exists():
        return by_model
    for p in sorted(logs_dir.glob("agent-*.jsonl")):
        if since:
            try:
                ts = datetime.fromtimestamp(p.stat().st_mtime)
                if ts < since:
                    continue
            except OSError:
                continue
        entry = parse_log(p)
        if entry is None:
            continue
        slug = slugify(entry["model"])
        if model_filter and slug != model_filter:
            continue
        by_model[slug].append(entry)
    return by_model


def fmt_ratio(r: float) -> str:
    if r == float("inf"):
        return "∞"
    if r == 0:
        return "—"
    return f"{r:.2f}"


def print_table(per_model_metrics: dict[str, dict]) -> None:
    if not per_model_metrics:
        print("(no logs found)")
        return
    rows = sorted(per_model_metrics.items(), key=lambda kv: -kv[1].get("tasks", 0))
    print(f"{'Model':<35} {'Tasks':>6} {'iters':>6} {'tools/T':>8} {'edit:wr':>8} {'bash/T':>7} {'rd_dup':>7} {'agent':>5}")
    print("-" * 90)
    for slug, m in rows:
        if m.get("tasks", 0) == 0:
            continue
        print(f"{slug:<35} {m['tasks']:>6} "
              f"{m['iters_avg']:>6.1f} "
              f"{m['tool_calls_avg']:>8.1f} "
              f"{fmt_ratio(m['edit_write_ratio']):>8} "
              f"{m['bash_per_task']:>7.2f} "
              f"{fmt_ratio(m['read_redundancy']):>7} "
              f"{m['agent_calls']:>5}")
    print()
    print("Legend: edit:wr = edit_file/write_file ratio (higher = more targeted)")
    print("        bash/T = bash calls per task (lower = more targeted)")
    print("        rd_dup = per-log avg of read_file calls / unique paths read (1.0 = no rereads within any task)")
    print("        agent  = total start_agent / skill / start_skill_agent calls")


def print_drilldown(slug: str, entries: list[dict]) -> None:
    print(f"\n=== Drilldown: {slug} ({len(entries)} agent runs) ===\n")
    name_counts: dict[str, int] = defaultdict(int)
    for e in entries:
        for name, _ in e["tool_calls"]:
            name_counts[name] += 1
    print("Tool-call frequencies (across all runs):")
    for name, n in sorted(name_counts.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {name:<30} {n}")
    print()


def main():
    p = argparse.ArgumentParser(description="Per-model tool-use efficiency analyzer")
    p.add_argument("--logs-dir", default=str(DEFAULT_LOGS), help="Directory of agent-*.jsonl logs")
    p.add_argument("--since", help="ISO date (YYYY-MM-DD) — only include logs whose mtime is on or after this date")
    p.add_argument("--model", help="Filter to one model slug (also enables drilldown)")
    args = p.parse_args()

    since = datetime.fromisoformat(args.since) if args.since else None
    by_model = collect(Path(args.logs_dir), since=since, model_filter=args.model)
    metrics = {slug: compute_metrics(entries) for slug, entries in by_model.items()}
    print_table(metrics)
    if args.model and args.model in by_model:
        print_drilldown(args.model, by_model[args.model])


if __name__ == "__main__":
    main()
