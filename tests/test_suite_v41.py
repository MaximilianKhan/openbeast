"""Suite v4.1 is a suite of its own (2026-10 era roll).

v4.1 has the same 137 tasks / 291 units as v4, with two validators, 132
fixtures and the eval harness corrected. Its scores are not comparable to
v4's, so:

  * a v4.1 row never replaces, removes or re-ranks a v4 row;
  * the board ranks each suite in its own section;
  * the v5-fast pin, built from v4 runs, is marked stale and not re-guessed.

Every case builds its own board / results / reference runs in tmp_path. The
only real files read are the committed pin and task specs.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

import make_fast_suite as mfs  # noqa: E402
import run_eval  # noqa: E402
import scoring  # noqa: E402

PIN = json.loads((ROOT / "evals" / "suites" / "v5-fast.json").read_text())


def _entry(model, suite, cap, total=None, host="rig"):
    return {"model": model, "model_slug": model.lower(), "suite_version": suite,
            "gpu": {"host_id": host}, "capability": cap, "accuracy": cap,
            "problem_solving": cap, "language_breadth": cap,
            "tasks_passed": 1, "tasks_total": total or scoring.SUITE_EXPECTED_UNITS.get(suite, 5),
            "elapsed_total_seconds": 60, "timestamp": "2026-10-10T00:00:00"}


def _board(tmp_path, entries):
    p = tmp_path / "leaderboard.json"
    p.write_text(json.dumps({"entries": entries}))
    return str(p)


def test_the_checkout_runs_v41_and_knows_its_size():
    assert (ROOT / "evals" / "SUITE_VERSION").read_text().strip() == "v4.1"
    assert scoring.current_suite_version() == "v4.1"
    assert run_eval.capture_suite_version() == "v4.1"
    assert scoring.SUITE_EXPECTED_UNITS["v4.1"] == 291 == len(run_eval.load_tasks())
    assert scoring.SUITE_EXPECTED_UNITS["v4"] == 291      # v4 rows stay "full"


def test_a_v41_row_and_a_v4_row_of_one_model_are_two_entries():
    v4, v41 = _entry("Champ", "v4", 98.7), _entry("Champ", "v4.1", 97.0)
    assert scoring.entry_dedup_key(v4) != scoring.entry_dedup_key(v41)
    # Unchanged for the suites up to v4: one line per (host, model), so a v4
    # run still replaces the model's v3.5 row as it always did.
    assert scoring.entry_dedup_key(v4) == ("rig", "champ")
    assert scoring.entry_dedup_key(_entry("Champ", "v3.5", 90.0)) == ("rig", "champ")
    # ...and any later suite gets its own line without a code change.
    assert scoring.entry_dedup_key(_entry("Champ", "v4.2", 1.0)) == ("rig", "champ", "v4.2")


def test_seating_a_v41_run_keeps_the_v4_row(tmp_path):
    path = _board(tmp_path, [_entry("Champ", "v4", 98.7), _entry("Other", "v4", 97.5)])
    after = scoring.update_leaderboard(_entry("Champ", "v4.1", 96.0), path=path)
    got = sorted((e["model"], e["suite_version"], e["capability"]) for e in after)
    assert got == [("Champ", "v4", 98.7), ("Champ", "v4.1", 96.0), ("Other", "v4", 97.5)]
    # A second v4.1 run of the model replaces the v4.1 row only.
    after = scoring.update_leaderboard(_entry("Champ", "v4.1", 99.0), path=path)
    got = sorted((e["model"], e["suite_version"], e["capability"]) for e in after)
    assert got == [("Champ", "v4", 98.7), ("Champ", "v4.1", 99.0), ("Other", "v4", 97.5)]


def test_a_partial_v41_run_is_refused_like_a_partial_v4_run(tmp_path, capsys):
    path = _board(tmp_path, [_entry("Champ", "v4", 98.7)])
    for suite in ("v4.1", "v4"):
        after = scoring.update_leaderboard(_entry("Smoke", suite, 100.0, total=112), path=path)
        assert [e["model"] for e in after] == ["Champ"], suite
        assert "REFUSED" in capsys.readouterr().out


def _results(slug, suite, ts, passed=291):
    return {"timestamp": ts, "model": slug.upper(), "model_slug": slug, "suite_version": suite,
            "gpu": {"host_id": "rig"}, "harness": {}, "summary": {"total": 291},
            "tasks": [{"id": f"t{i:03d}", "difficulty": "easy", "passed": i < passed,
                       "elapsed_seconds": 1.0} for i in range(291)]}


def test_rebuild_seats_both_suites_for_the_same_model(tmp_path, monkeypatch, capsys):
    res = tmp_path / "results"
    res.mkdir()
    (res / "eval-m-20260708-000000.json").write_text(json.dumps(_results("m", "v4", "2026-07-08T00:00:00", 280)))
    (res / "eval-m-20261020-000000.json").write_text(json.dumps(_results("m", "v4.1", "2026-10-20T00:00:00", 250)))
    # A file from before the suite_version field existed: 291 units = v4.
    old = _results("n", "v4", "2026-07-09T00:00:00", 270)
    del old["suite_version"]
    (res / "eval-n-20260709-000000.json").write_text(json.dumps(old))
    monkeypatch.setattr(scoring, "RESULTS_DIR", str(res))
    monkeypatch.setattr(scoring, "LEADERBOARD_PATH", str(tmp_path / "leaderboard.json"))
    monkeypatch.setattr(sys, "argv", ["scoring.py", "--rebuild"])
    scoring.main()
    rows = json.loads((tmp_path / "leaderboard.json").read_text())["entries"]
    assert sorted((e["model_slug"], e["suite_version"], e["tasks_passed"]) for e in rows) == [
        ("m", "v4", 280), ("m", "v4.1", 250), ("n", "v4", 270)]


def _ranked(lines):
    """[rank, model] of the board rows in `lines` (footnote lines excluded)."""
    rows = [ln.split() for ln in lines if ln.strip()[:1].isdigit()]
    return [r[:2] for r in rows if len(r) > 6 and r[1][0] not in "†‡"]


def test_board_ranks_each_suite_in_its_own_section():
    entries = [_entry("A", "v4", 98.7), _entry("B", "v4", 97.7), _entry("C", "v4", 95.0),
               _entry("Old", "v3.5", 99.9),
               _entry("A", "v4.1", 96.0), _entry("C", "v4.1", 97.0)]
    sections = scoring.board_sections(entries)
    assert [(sv, [e["model"] for e in rows]) for sv, rows, _ in sections] == [
        ("v4.1", ["C", "A"]), ("v4", ["A", "B", "C"]), ("v3.5", ["Old"])]
    text = scoring.format_leaderboard(entries)
    lines = text.splitlines()
    at = lambda needle: next(i for i, ln in enumerate(lines) if needle in ln)
    assert at("SUITE v4 (older suite") < at("SUITE v3.5 (older suite")
    # v4 rows keep ranks 1..3 among themselves, whatever v4.1 scored.
    v4_block = lines[at("SUITE v4 (older suite"):at("SUITE v3.5 (older suite")]
    ranked = _ranked(v4_block)
    assert ranked == [["1", "A"], ["2", "B"], ["3", "C"]]
    assert "not comparable to v4.1 rows" in text
    html = scoring.format_leaderboard_html(entries)
    assert html.index("<h2>Suite v4.1</h2>") < html.index("<h2>Suite v4</h2>") < html.index(
        "<h2>Suite v3.5</h2>")


def test_board_with_no_v41_rows_still_shows_the_v4_board_intact():
    """The state right after the merge: nothing has been rerun yet."""
    entries = [_entry("B", "v4", 97.7), _entry("A", "v4", 98.7), _entry("Old", "v3.5", 99.9)]
    text = scoring.format_leaderboard(entries)
    assert "no v4.1 rows yet" in text
    body = _ranked(text.splitlines())
    assert body == [["1", "A"], ["2", "B"], ["1", "Old"]]
    html = scoring.format_leaderboard_html(entries)
    assert "no v4.1 rows yet" in html and "<h2>Suite v4</h2>" in html


def test_the_committed_board_keeps_every_v4_row_in_order():
    """This branch must not touch the seated rows. Whatever is on the board,
    its v4 rows print in their own rank order under the v4 heading."""
    entries = scoring.load_leaderboard(str(ROOT / "evals" / "leaderboard.json"))
    v4 = sorted((e for e in entries if e.get("suite_version") == "v4"), key=scoring.rank_key)
    assert v4, "the v4 rows are gone from evals/leaderboard.json"
    section = dict((sv, rows) for sv, rows, _ in scoring.board_sections(entries))
    assert [e["model_slug"] for e in section["v4"]] == [e["model_slug"] for e in v4]


def test_suite_order_is_newest_first():
    got = sorted(["v3.5", "legacy", "v4", "v4.1", "v10", "unknown"], key=scoring.suite_order)
    assert got == ["v10", "v4.1", "v4", "v3.5", "legacy", "unknown"]


# --- v5-fast: marked, not re-guessed ----------------------------------------

def test_the_pin_is_marked_for_repinning_and_nothing_was_reguessed():
    assert PIN["base_suite_version"] == "v4"
    mark = PIN["repin_required"]
    assert mark["since"] == "v4.1"
    assert mark["assumed_failed_known_wrong"] == ["23_sql_injection"]
    # Still exactly where the v4 reference runs put it.
    assert "23_sql_injection" in PIN["assumed_failed"]
    assert "23_sql_injection" not in PIN["units"] + PIN["assumed_passed"]
    assert set(mark["assumed_passed_respecified"]) <= set(PIN["assumed_passed"])
    assert len(mark["assumed_passed_respecified"]) == 68 and mark["measured_respecified"] == 65


def test_a_stale_pin_says_so_and_a_fresh_one_does_not(monkeypatch):
    why = mfs.pin_is_stale(PIN)
    assert why and "v4" in why and "v4.1" in why and "23_sql_injection" in why
    assert run_eval._stale_pin(PIN) == why
    fresh = {k: v for k, v in PIN.items() if k != "repin_required"}
    fresh["base_suite_version"] = "v4.1"
    assert mfs.pin_is_stale(fresh) is None
    # Control: on a v4 checkout the unmarked v4 pin was not stale.
    monkeypatch.setattr(scoring, "current_suite_version", lambda: "v4")
    assert mfs.pin_is_stale({**fresh, "base_suite_version": "v4"}) is None


def test_reference_runs_are_never_mixed_across_suites(tmp_path, monkeypatch):
    for slug, suite in (("a", "v4"), ("b", "v4"), ("a", "v4.1"), ("c", "v4.1")):
        (tmp_path / f"eval-{slug}-{suite}.json").write_text(
            json.dumps(_results(slug, suite, "2026-10-20T00:00:00")))
    legacy = _results("d", "v4", "2026-07-08T00:00:00")
    del legacy["suite_version"]                       # pre-field file: 291 units = v4
    (tmp_path / "eval-d-old.json").write_text(json.dumps(legacy))
    monkeypatch.setattr(mfs, "RESULTS_DIR", str(tmp_path))
    assert sorted(mfs.load_reference_runs(set())) == ["a", "c"]            # current = v4.1
    assert sorted(mfs.load_reference_runs(set(), suite_version="v4")) == ["a", "b", "d"]


def test_a_regenerated_pin_is_stamped_with_the_current_suite(monkeypatch):
    units = run_eval.load_tasks(None)
    singles = [t["id"] for t in units if "base_id" not in t]

    def ref(fails):
        return ("synthetic.json", {"tasks": [
            {"id": t["id"], "base_id": t.get("base_id"), "variant_count": t.get("variant_count", 1),
             "difficulty": t.get("difficulty", "medium"), "language": t.get("language", "python"),
             "passed": t["id"] not in fails, "elapsed_seconds": 1.0} for t in units]})

    refs = {"a": ref({singles[0]}), "b": ref({singles[1]}), "c": ref(set())}
    monkeypatch.setattr(mfs, "load_reference_runs", lambda exclude: refs)
    pin = mfs.generate(set())
    assert pin["base_suite_version"] == "v4.1" and "repin_required" not in pin
    assert mfs.pin_is_stale(pin) is None


def test_a_fast_run_on_a_stale_pin_is_told_the_imputed_score_is_invalid(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(run_eval, "capture_server_config", lambda *a, **k: {})
    monkeypatch.setattr(run_eval, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(run_eval, "capture_inference_engine_info", lambda: {})
    monkeypatch.setattr(run_eval, "run_setup", lambda *a, **k: True)
    monkeypatch.setattr(run_eval, "run_cleanup", lambda *a, **k: None)
    monkeypatch.setattr(run_eval, "run_pre_validate", lambda *a, **k: None)
    monkeypatch.setattr(run_eval, "run_validation", lambda *a, **k: (True, "OK"))
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: {
        "exit_code": 0, "elapsed_seconds": 1.0, "stdout": "", "stderr": "",
        "tokens": {"prompt": 1, "completion": 1, "total": 2}, "iterations": 1,
        "compactions": 0, "api_errors": 0})
    res = run_eval.run_eval(suite="v5-fast", model_name="m", use_cache=False)
    out = capsys.readouterr().out
    assert "STALE PIN" in out and "NOT valid" in out
    assert "pin_stale" in res["fast_suite"]
    assert len(res["tasks"]) == len(PIN["units"])
