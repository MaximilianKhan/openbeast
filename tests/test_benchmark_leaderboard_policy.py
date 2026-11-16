"""--tasks implies --no-leaderboard.

benchmark_all used to print that partial scores "will be recorded in
leaderboard.json and mix incomparably" — which scoring's 291-unit guard then
refused, and that guard is v4-only (review finding campaign-evals). The rule
now lives in one place and does not depend on the suite version.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

import benchmark_all as ba  # noqa: E402


def _ns(**kw):
    return SimpleNamespace(**{"no_leaderboard": False, "suite": None, "tasks": None, **kw})


def test_full_live_sweep_records():
    assert ba._leaderboard_policy(_ns(), []) is True


def test_tasks_implies_no_leaderboard(capsys):
    assert ba._leaderboard_policy(_ns(tasks="21,22"), []) is False
    out = capsys.readouterr()
    assert "implies --no-leaderboard" in out.out
    assert "will be recorded" not in out.out + out.err


def test_other_implications_unchanged():
    assert ba._leaderboard_policy(_ns(suite="v5-fast"), []) is False
    assert ba._leaderboard_policy(_ns(), ["greedy decoding"]) is False
    assert ba._leaderboard_policy(_ns(no_leaderboard=True), []) is False
