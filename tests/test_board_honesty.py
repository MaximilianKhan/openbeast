"""What the board says about its own rows (review 2026-10-09, evals F12 / F9
/ F3): how many units were killed rather than judged, a rebuild that finds
nothing, and rows measured under different regimes.

Every results file and leaderboard here is built in a temp dir; nothing
reads evals/results or evals/leaderboard.json.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

import scoring  # noqa: E402


def _results(slug: str = "m", passed: int = 291, exits: dict | None = None, **extra) -> dict:
    """A full v4-shaped results file. exits: {unit index: agent_exit_code}."""
    tasks = []
    for i in range(291):
        t = {"id": f"t{i:03d}", "difficulty": "easy", "passed": i < passed,
             "elapsed_seconds": 1.0, "agent_exit_code": 0,
             "tokens_prompt": 10, "tokens_completion": 5, "tokens_total": 15}
        if exits and i in exits:
            t.update(agent_exit_code=exits[i], tokens_prompt=0, tokens_completion=0,
                     tokens_total=0)
        tasks.append(t)
    return {"timestamp": "2026-10-01T00:00:00", "model": slug.upper(), "model_slug": slug,
            "suite_version": "v4", "gpu": {"host_id": "rig"}, "harness": {},
            "summary": {"total": 291}, "tasks": tasks, **extra}


def _cells(board: str, model: str) -> list[str]:
    (line,) = [ln for ln in board.splitlines() if f"  {model} " in ln]
    return line.split()


# --- F12: killed units are counted beside the row --------------------------

def test_killed_units_counts_timeouts_and_signals_passed_or_not():
    # 0 = a timed-out PASS, 290 = a timed-out FAIL, 289 = SIGKILLed.
    entry = scoring.score_run(_results(passed=289, exits={0: -1, 290: -1, 289: -9}))
    assert entry["killed_units"] == 3
    # Negative control: a clean run, and rows that predate the exit code.
    assert scoring.score_run(_results(passed=250))["killed_units"] == 0
    legacy = _results()
    for t in legacy["tasks"]:
        del t["agent_exit_code"]
    assert scoring.score_run(legacy)["killed_units"] == 0


def test_board_prints_the_killed_count_and_explains_it():
    a = scoring.score_run(_results("aa", passed=280, exits={285: -1, 286: -1}))
    b = scoring.score_run(_results("bb", passed=270))
    board = scoring.format_leaderboard([a, b])
    assert board.splitlines()[0].split()[-1] == "T/O"
    assert _cells(board, "AA")[-1] == "2" and _cells(board, "BB")[-1] == "0"
    assert "T/O = units whose agent was killed" in board
    # An entry scored before the field existed is shown as unknown, not as 0.
    del b["killed_units"]
    assert _cells(scoring.format_leaderboard([a, b]), "BB")[-1] == "?"


def test_killed_count_changes_neither_eligibility_nor_score():
    clean = scoring.score_run(_results(passed=280))
    killed = scoring.score_run(_results(passed=280, exits={285: -1, 286: -9}))
    assert killed["ineligible_reasons"] == [] == clean["ineligible_reasons"]
    for k in ("capability", "problem_solving", "language_breadth", "tasks_passed"):
        assert killed[k] == clean[k]


def test_html_board_carries_the_killed_column():
    page = scoring.format_leaderboard_html(
        [scoring.score_run(_results("aa", passed=280, exits={285: -1}))])
    assert "<th>T/O</th>" in page
    assert '<td class="n">280/291</td><td class="n">1</td></tr>' in page
