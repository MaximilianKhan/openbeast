"""Map run_eval result rows to the agent transcripts that produced them.

run_eval does not record a row's transcript path. The runner writes one
JSONL per unit, agents/logs/agent-<YYYYmmdd-HHMMSS>-<hex8>.jsonl, whose first
event is {"type": "start", "task": <the task text run_eval passed>,
"timestamp": ...}. A row is matched to the latest transcript whose task text
is the unit's task text and that started inside the results file's window
(its header timestamp minus slack, to the file's last write: rows are saved
after every unit, so the last write always follows the last unit's start and
needs no forward slack; a forward slack would let the next run's first units
in). Both the audit (prepare.py) and the language taxonomy (langaudit.py) use
this.

Timestamps without a zone (run_eval's `timestamp`, the runner's start events)
are read in DEFAULT_TZ when set (set_default_tz), else in this host's local
zone. Scan on the host that wrote the files, or set the writing host's zone.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, tzinfo

SLACK_S = 120
DEFAULT_TZ: tzinfo | None = None


def set_default_tz(name: str | None) -> None:
    """Read naive timestamps in zone `name` (an IANA name such as "UTC" or
    "America/Los_Angeles"); None restores this host's local zone."""
    global DEFAULT_TZ
    if not name:
        DEFAULT_TZ = None
        return
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    DEFAULT_TZ = ZoneInfo(name)


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None and DEFAULT_TZ is not None:
        d = d.replace(tzinfo=DEFAULT_TZ)
    return d.timestamp()


def parse_time(s: str) -> float:
    """An ISO time given on a command line (naive = DEFAULT_TZ or local)."""
    t = _ts(s)
    if t is None:
        raise ValueError(f"not an ISO time: {s!r}")
    return t


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
        if until is not None and t > until:
            continue
        out.append({"path": p, "t": t, "task": _norm(first.get("task", ""))})
    return out


def results_window(results_path: str, until: float | None = None) -> tuple[float | None, float]:
    """(start, end) of the run that wrote a results file. The end is `until`
    when given (integrity_scan --until: the run's end from its campaign log),
    else the file's own end when it records one (`saved`, written by run_eval
    on every save since 2026-11-22). Only an older file falls back to its
    mtime, which a git checkout or copy resets: for a committed pre-`saved`
    file pass --until, or the window runs into later runs."""
    with open(results_path) as f:
        head = json.load(f)
    if until is not None:
        return _ts(head.get("timestamp")), until
    end = next((t for t in (_ts(head.get(k)) for k in ("saved", "finished", "end_timestamp"))
                if t is not None), None)
    return _ts(head.get("timestamp")), end if end is not None else os.path.getmtime(results_path)


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
