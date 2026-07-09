#!/usr/bin/env python3
"""Smoke test for evals/tool_efficiency.py.

Builds a synthetic agent log with known tool-call counts, runs the
analyzer over it, and checks the computed metrics. No reliance on real
log data, so the test is deterministic regardless of agent log churn."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import tool_efficiency  # noqa: E402


PASSED = 0
FAILED = 0


def check(label: str, cond: bool, detail: str = ""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label}  {detail}")
    # Also assert so failures propagate properly under pytest (the print
    # counters alone would let pytest report a silent pass).
    assert cond, f"{label}  {detail}"


def make_log(path: Path, model: str, tool_calls: list[tuple[str, dict]],
             iters: int = 5):
    """Write a synthetic agent log."""
    with open(path, "w") as f:
        f.write(json.dumps({"type": "start", "model": model, "task": "t",
                            "workdir": "/tmp", "timestamp": "2026-05-07T00:00:00"}) + "\n")
        for i in range(iters):
            f.write(json.dumps({"type": "iteration", "number": i + 1,
                                "timestamp": "2026-05-07T00:00:00"}) + "\n")
        for name, args in tool_calls:
            f.write(json.dumps({"type": "tool_call", "name": name, "args": args,
                                "result": "ok", "timestamp": "2026-05-07T00:00:00"}) + "\n")
        f.write(json.dumps({"type": "max_iterations", "iterations": iters,
                            "tokens_prompt": 1000, "tokens_completion": 100,
                            "tokens_total": 1100,
                            "timestamp": "2026-05-07T00:00:00"}) + "\n")


def test_basic_aggregation():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Two logs for model A: 4 edits, 1 write → ratio 4.0
        make_log(td / "agent-001.jsonl", "model-a", [
            ("edit_file", {"path": "/tmp/a"}),
            ("edit_file", {"path": "/tmp/b"}),
            ("write_file", {"path": "/tmp/c"}),
            ("read_file", {"path": "/tmp/x"}),
            ("read_file", {"path": "/tmp/x"}),  # re-read
            ("read_file", {"path": "/tmp/y"}),
            ("bash", {"cmd": "ls"}),
        ], iters=3)
        make_log(td / "agent-002.jsonl", "model-a", [
            ("edit_file", {"path": "/tmp/d"}),
            ("edit_file", {"path": "/tmp/e"}),
        ], iters=2)
        # One log for model B: 1 edit, 4 writes → ratio 0.25
        make_log(td / "agent-003.jsonl", "Model B", [
            ("edit_file", {"path": "/tmp/a"}),
            ("write_file", {"path": "/tmp/a"}),
            ("write_file", {"path": "/tmp/b"}),
            ("write_file", {"path": "/tmp/c"}),
            ("write_file", {"path": "/tmp/d"}),
            ("bash", {"cmd": "ls"}),
            ("bash", {"cmd": "pwd"}),
            ("list_skills", {}),
        ], iters=10)

        by_model = tool_efficiency.collect(td)
        check("two models discovered", set(by_model.keys()) == {"model-a", "model-b"},
              str(set(by_model.keys())))

        m_a = tool_efficiency.compute_metrics(by_model["model-a"])
        check("model-a: 2 tasks", m_a["tasks"] == 2)
        check("model-a: 4 edits + 1 write → ratio 4.0",
              abs(m_a["edit_write_ratio"] - 4.0) < 1e-9, str(m_a["edit_write_ratio"]))
        # Per-log semantics: only log 001 reads (3 reads / 2 unique = 1.5);
        # log 002 has no reads and is excluded from the average → 1.5.
        check("model-a: read_redundancy = per-log avg = 1.5",
              abs(m_a["read_redundancy"] - 1.5) < 1e-9, str(m_a["read_redundancy"]))
        check("model-a: bash/task = 1/2 = 0.5",
              abs(m_a["bash_per_task"] - 0.5) < 1e-9, str(m_a["bash_per_task"]))
        check("model-a: iters_avg = (3+2)/2 = 2.5",
              abs(m_a["iters_avg"] - 2.5) < 1e-9, str(m_a["iters_avg"]))

        m_b = tool_efficiency.compute_metrics(by_model["model-b"])
        check("model-b: 1 task", m_b["tasks"] == 1)
        check("model-b: 1 edit + 4 writes → ratio 0.25",
              abs(m_b["edit_write_ratio"] - 0.25) < 1e-9, str(m_b["edit_write_ratio"]))
        check("model-b: agent_calls counts list_skills",
              m_b["agent_calls"] == 1, str(m_b["agent_calls"]))


def test_filters():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        make_log(td / "agent-001.jsonl", "model-a", [("edit_file", {"path": "/x"})])
        make_log(td / "agent-002.jsonl", "model-b", [("write_file", {"path": "/x"})])
        # model filter
        by_model = tool_efficiency.collect(td, model_filter="model-a")
        check("model filter restricts to one", list(by_model.keys()) == ["model-a"],
              str(list(by_model.keys())))


def test_zero_writes_edge_case():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # All edits, no writes → ratio = inf
        make_log(td / "agent-001.jsonl", "model-c", [
            ("edit_file", {"path": "/a"}),
            ("edit_file", {"path": "/b"}),
        ])
        by_model = tool_efficiency.collect(td)
        m = tool_efficiency.compute_metrics(by_model["model-c"])
        check("edits>0, writes=0 → ratio is inf",
              m["edit_write_ratio"] == float("inf"))

        # No edits, no writes → ratio = 0
        make_log(td / "agent-002.jsonl", "model-d", [
            ("bash", {"cmd": "ls"}),
        ])
        by_model = tool_efficiency.collect(td)
        m = tool_efficiency.compute_metrics(by_model["model-d"])
        check("no edits, no writes → ratio is 0",
              m["edit_write_ratio"] == 0.0)


def test_read_redundancy_is_per_log():
    """Two logs each reading the SAME path once must NOT count as a re-read.
    The old global-set computation gave 2 reads / 1 unique = 2.0 here; the
    corrected per-log average gives 1.0."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        make_log(td / "agent-001.jsonl", "model-e", [("read_file", {"path": "/tmp/shared"})])
        make_log(td / "agent-002.jsonl", "model-e", [("read_file", {"path": "/tmp/shared"})])
        # A third log that genuinely re-reads: 4 reads over 2 paths = 2.0
        make_log(td / "agent-003.jsonl", "model-e", [
            ("read_file", {"path": "/tmp/a"}),
            ("read_file", {"path": "/tmp/a"}),
            ("read_file", {"path": "/tmp/b"}),
            ("read_file", {"path": "/tmp/b"}),
        ])
        by_model = tool_efficiency.collect(td)
        m = tool_efficiency.compute_metrics(by_model["model-e"])
        # avg(1.0, 1.0, 2.0) = 4/3
        check("shared-path reads across logs are not re-reads (per-log avg = 4/3)",
              abs(m["read_redundancy"] - 4.0 / 3.0) < 1e-9, str(m["read_redundancy"]))
        check("total read count still aggregated globally", m["read_count"] == 6,
              str(m["read_count"]))


def test_incomplete_log_ignored():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Log with no 'start' record — should be skipped
        with open(td / "agent-001.jsonl", "w") as f:
            f.write(json.dumps({"type": "iteration", "number": 1}) + "\n")
        # And one good log
        make_log(td / "agent-002.jsonl", "model-x", [("edit_file", {"path": "/a"})])
        by_model = tool_efficiency.collect(td)
        check("incomplete log skipped, good log included",
              "model-x" in by_model and len(by_model) == 1)


def main():
    print("\ntest_basic_aggregation:")
    test_basic_aggregation()
    print("\ntest_filters:")
    test_filters()
    print("\ntest_zero_writes_edge_case:")
    test_zero_writes_edge_case()
    print("\ntest_read_redundancy_is_per_log:")
    test_read_redundancy_is_per_log()
    print("\ntest_incomplete_log_ignored:")
    test_incomplete_log_ignored()

    print(f"\n{'='*40}")
    print(f"Tool-efficiency tests: {PASSED} passed, {FAILED} failed")
    print(f"{'='*40}")
    sys.exit(0 if FAILED == 0 else 1)


if __name__ == "__main__":
    main()
