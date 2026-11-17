"""Map run_eval result rows to the agent transcripts that produced them.

run_eval does not record a row's transcript path. The runner writes one
JSONL per unit, agents/logs/agent-<YYYYmmdd-HHMMSS>-<hex8>.jsonl, whose first
event is {"type": "start", "task": <the task text run_eval passed>,
"timestamp": ...}. A row is matched to the latest transcript whose task text
is the unit's task text and that started inside the results file's window
(its header timestamp to the file's last write, plus slack). Both the audit
(prepare.py) and the language taxonomy (langaudit.py) use this.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime

SLACK_S = 120


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def index_logs(logs_dir: str, since: float | None = None, until: float | None = None) -> list[dict]:
    """[{path, t, task}] for every transcript whose start lies in [since, until]."""
    out = []
    for p in sorted(glob.glob(os.path.join(logs_dir, "agent-*.jsonl"))):
        try:
            with open(p) as f:
                first = json.loads(f.readline() or "{}")
        except (OSError, ValueError):
            continue
        if first.get("type") != "start":
            continue
        t = _ts(first.get("timestamp"))
        if t is None:
            t = os.path.getmtime(p)
        if since is not None and t < since - SLACK_S:
            continue
        if until is not None and t > until + SLACK_S:
            continue
        out.append({"path": p, "t": t, "task": _norm(first.get("task", ""))})
    return out


def results_window(results_path: str) -> tuple[float | None, float]:
    with open(results_path) as f:
        head = json.load(f)
    return _ts(head.get("timestamp")), os.path.getmtime(results_path)


def match_transcripts(unit_texts: dict, index: list[dict]) -> dict:
    """{unit: transcript path} — the latest transcript per unit whose task
    text equals the unit's (or extends it: a harness may append to it)."""
    norm = {u: _norm(t) for u, t in unit_texts.items() if t}
    out: dict = {}
    best_t: dict = {}
    for e in index:
        for u, text in norm.items():
            if e["task"] == text or e["task"].startswith(text):
                if e["t"] >= best_t.get(u, float("-inf")):
                    best_t[u] = e["t"]
                    out[u] = e["path"]
    return out


def load_task_texts(repo: str) -> dict:
    """{effective unit id: task text} from a checkout's evals/tasks/."""
    import sys
    sys.path[:0] = [os.path.join(repo, "evals"), os.path.join(repo, "agents")]
    import run_eval  # noqa: PLC0415
    return {t["id"]: t for t in run_eval.load_tasks(None)}


def read_events(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out
