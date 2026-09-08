"""Pinned fast-suite (evals/suites/v5-fast.json) integrity + imputation tests.

The fast suite's fidelity contract: a v5-fast run scored as
(measured units + imputed complement) equals the full-291 capability
EXACTLY for any model whose outcomes match the pin's assumptions.
These tests pin the machinery that keeps that contract honest:

  1. The pin partitions the CURRENT effective unit set exactly
     (units + assumed_passed + assumed_failed == all 291, no overlap).
  2. Declared counts match the lists (drift between metadata and content
     would silently mis-describe the suite).
  3. Tripwires are a subset of the measured units.
  4. impute_suite_tasks reconstructs the full-suite capability exactly
     when reality matches the assumptions, and real measurements always
     beat assumptions when both exist.
  5. load_suite hard-fails on pin/tasks drift instead of mis-scoring.
  6. --suite and --tasks are mutually exclusive.

No evals/results/ files are needed — everything checks against the pin
and evals/tasks/ specs, so this runs in CI.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

import run_eval  # noqa: E402
import scoring  # noqa: E402

PIN_PATH = ROOT / "evals" / "suites" / "v5-fast.json"


@pytest.fixture(scope="module")
def pin() -> dict:
    with open(PIN_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def all_units() -> list[dict]:
    return run_eval.load_tasks(None)


def test_pin_partitions_unit_set_exactly(pin, all_units):
    units = set(pin["units"])
    ap = set(pin["assumed_passed"])
    af = set(pin["assumed_failed"])
    actual = {t["id"] for t in all_units}
    assert units | ap | af == actual, "pin does not cover the current unit set"
    assert not units & ap and not units & af and not ap & af, "pin sets overlap"
    assert len(pin["units"]) == len(units), "duplicate ids in units"


def test_declared_counts_match_content(pin):
    c = pin["counts"]
    assert c["units"] == len(pin["units"])
    assert c["tripwires"] == len(pin["tripwires"])
    assert c["assumed_passed"] == len(pin["assumed_passed"])
    assert c["assumed_failed"] == len(pin["assumed_failed"])
    assert c["discriminating"] == c["units"] - c["tripwires"]


def test_tripwires_are_measured_units(pin):
    assert set(pin["tripwires"]) <= set(pin["units"])


def test_imputation_reconstructs_full_capability_exactly(pin, all_units):
    """Simulate a model matching the pin's assumptions: passes everything
    except 30 arbitrary measured units. Full-list capability must equal
    imputed capability from the measured subset alone — exactly."""
    fail = set(sorted(pin["units"])[::4][:30])
    full = [{"id": t["id"], "base_id": t.get("base_id"),
             "variant_count": t.get("variant_count", 1),
             "difficulty": t.get("difficulty", "medium"),
             "passed": t["id"] not in fail and t["id"] not in set(pin["assumed_failed"])}
            for t in all_units]
    measured = [t for t in full if t["id"] in set(pin["units"])]
    _s, _l, cap_full = scoring.compute_solve_breadth(full)
    _s, _l, cap_imputed = scoring.compute_solve_breadth(
        scoring.impute_suite_tasks(measured, pin))
    assert abs(cap_full - cap_imputed) < 1e-9


def test_measured_rows_beat_assumptions(pin):
    """A measured row for a unit in assumed_passed must not be duplicated
    or overridden by the assumption."""
    uid = pin["assumed_passed"][0]
    measured = [{"id": uid, "base_id": None, "variant_count": 1,
                 "difficulty": "easy", "passed": False}]
    out = scoring.impute_suite_tasks(measured, pin)
    rows = [t for t in out if t["id"] == uid]
    assert len(rows) == 1 and rows[0]["passed"] is False


def test_impute_unknown_unit_raises(pin):
    bad = dict(pin, assumed_passed=pin["assumed_passed"] + ["999_not_a_task"])
    with pytest.raises(ValueError, match="drift"):
        scoring.impute_suite_tasks([], bad)


def test_load_suite_detects_drift(tmp_path, monkeypatch, pin):
    incomplete = dict(pin, units=pin["units"][:-1])  # drop one unit
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "broken.json").write_text(json.dumps(incomplete))
    monkeypatch.setattr(run_eval, "SUITES_DIR", str(suites))
    with pytest.raises(SystemExit, match="drifted"):
        run_eval.load_suite("broken")


def test_load_suite_unknown_name(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "SUITES_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="unknown suite"):
        run_eval.load_suite("nope")


def test_suite_and_tasks_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        run_eval.run_eval(task_filter=["01_create_file"], suite="v5-fast",
                          cache_only=True, model_name="x")


def test_pinned_suite_loads_cleanly(pin):
    loaded = run_eval.load_suite("v5-fast")
    assert loaded["counts"]["units"] == len(pin["units"])
