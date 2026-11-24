"""evals/audit/capped_turns.py: the per-unit count of turns that ran to the
output cap, from transcripts (2026-11-24, arm I_T2: Basalt #588 left turns
empty at the 8,192 cap, and the server log that named them was not kept)."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals" / "audit"))

import capped_turns  # noqa: E402


def _transcript(path, turns):
    """turns: (finish_reason, kind) with kind empty | text | call | tools (a normal turn)."""
    t0 = 0
    at = lambda s: (datetime(2026, 11, 24, 5) + timedelta(seconds=s)).isoformat()  # noqa: E731
    lines = [{"type": "start", "timestamp": "2026-11-24T05:00:00"}]
    for i, (finish, kind) in enumerate(turns, 1):
        lines.append({"type": "iteration", "number": i, "timestamp": at(t0)})
        t0 += 16
        lines.append({"type": "response_model", "iteration": i, "finish_reason": finish,
                      "timestamp": at(t0)})
        if kind in ("call", "tools"):
            lines.append({"type": "tool_call", "name": "bash", "args": {"command": "true"}, "result": ""})
        elif kind == "text":
            lines.append({"type": "assistant", "content": "partial"})
            lines.append({"type": "output_cap", "iteration": i, "max_tokens": 8192,
                          "completion_tokens": 8192, "timestamp": at(t0)})
        elif kind == "empty":
            lines.append({"type": "output_cap", "iteration": i, "max_tokens": 8192, "completion_tokens": 8192,
                          "reasoning_chars": 20000, "reasoning_tail_chars": 6000,
                          "timestamp": at(t0)})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_counts_each_kind_of_capped_turn(tmp_path):
    a, b = tmp_path / "agent-a.jsonl", tmp_path / "agent-b.jsonl"
    _transcript(a, [("tool_calls", "tools"), ("length", "empty"), ("length", "call"), ("length", "text"),
                    ("length", "empty"), ("tool_calls", "tools")])
    _transcript(b, [("tool_calls", "tools"), ("stop", "tools")])
    other = tmp_path / "agent-c.jsonl"
    _transcript(other, [("length", "empty")])
    scan = [{"label": "X", "units": {
        "u_fail": [{"unit": "u_fail", "transcript": str(a), "scored": True, "passed": False}],
        "u_pass": [{"unit": "u_pass", "transcript": str(b), "scored": True, "passed": True},
                   {"unit": "u_pass", "transcript": str(other), "scored": False, "passed": None}]}}]
    units = capped_turns.from_scan(scan, None, None)
    f = units["u_fail"]
    assert (f["turns"], f["length"], f["empty"], f["capped_text"], f["length_call"]) == (6, 4, 2, 1, 1)
    assert units["u_pass"]["empty"] == 0  # the unscored transcript is not counted
    s = capped_turns.summary(units)
    assert (s["empty"], s["empty_in_fails"], s["failed_units_with_empty"]) == (2, 2, ["u_fail"])
    assert s["rate_8192"]["n"] == 3 and s["rate_8192"]["median"] == 512  # 8192 tokens in 16 s
    # a copy of the logs elsewhere: --logs-dir re-roots by file name
    moved = tmp_path / "copy"
    moved.mkdir()
    (moved / a.name).write_text(a.read_text())
    (moved / b.name).write_text(b.read_text())
    assert capped_turns.from_scan(scan, "X", str(moved))["u_fail"]["empty"] == 2


def test_needs_one_arm(tmp_path):
    scan = [{"label": "A", "units": {}}, {"label": "B", "units": {}}]
    try:
        capped_turns.from_scan(scan, None, None)
    except SystemExit as e:
        assert "--label" in str(e)
    else:
        raise AssertionError("two arms without --label")
