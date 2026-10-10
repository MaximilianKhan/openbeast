"""What the board says about its own rows (review 2026-10-09, evals F12 / F9
/ F3): how many units were killed rather than judged, a rebuild that finds
nothing, and rows measured under different regimes.

Every results file and leaderboard here is built in a temp dir; nothing
reads evals/results or evals/leaderboard.json.
"""

import json
import sys
from pathlib import Path

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


def _timeouts(board: str, model: str) -> str:
    """The row's T/O cell, found by the header (a footnote mark may follow)."""
    return _cells(board, model)[board.splitlines()[0].split().index("T/O")]


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
    assert _timeouts(board, "AA") == "2" and _timeouts(board, "BB") == "0"
    assert "T/O = units whose agent was killed" in board
    # An entry scored before the field existed is shown as unknown, not as 0.
    del b["killed_units"]
    assert _timeouts(scoring.format_leaderboard([a, b]), "BB") == "?"


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
    # Negative control: no stray column on a board with no killed units.
    clean = scoring.format_leaderboard_html([scoring.score_run(_results("aa", passed=280))])
    assert '<td class="n">280/291</td><td class="n">0</td></tr>' in clean


# --- F3: rows measured under different regimes are footnoted ---------------

_FULL = dict(runtime={"openbeast_commit": "a" * 40, "openbeast_dirty": False},
             inference_engine={"build": 10254, "commit": "abc"},
             server={"cmdline": "llama-server --reasoning-budget -1", "reasoning_budget": "-1"},
             jobs=4)


def _entry(slug, passed, **prov):
    return scoring.score_run(_results(slug, passed=passed, **prov))


def _notes(entries):
    rows = sorted(entries, key=scoring.rank_key)
    return {i: (marks, text) for i, marks, text in scoring.provenance_notes(rows)}


def test_provenance_is_carried_from_the_results_file():
    assert _entry("aa", 280, **_FULL)["provenance"] == {
        "openbeast_commit": "a" * 40, "openbeast_dirty": False, "engine_build": "10254",
        "reasoning_budget": "-1", "jobs": 4}
    # A server that was read and carries no flag ran llama-server's default.
    e = _entry("aa", 280, **{**_FULL, "server": {"cmdline": "llama-server -c 8192"}})
    assert e["provenance"]["reasoning_budget"] == "default"
    # The era hash, for runs new enough to stamp it.
    e = _entry("aa", 280, **_FULL, harness={"era": "b5596c660b5ab819"})
    assert e["provenance"]["era"] == "b5596c660b5ab819"
    # Nothing recorded (the July 2026 rows; a --cache-only null server).
    assert _entry("aa", 280, server=None, inference_engine=None)["provenance"] == {}


def test_a_row_without_provenance_is_footnoted():
    notes = _notes([_entry("aa", 290, **_FULL),
                    _entry("bb", 280, inference_engine={"build": 10254})])
    assert 1 not in notes                      # complete, and it IS row 1
    marks, text = notes[2]
    assert marks == "†"
    assert text == "no repo commit, reasoning budget, jobs recorded"


def test_a_row_under_a_different_regime_is_footnoted():
    other = {**_FULL, "inference_engine": {"build": 9690}, "jobs": 1,
             "server": {"cmdline": "x", "reasoning_budget": "20480"},
             "runtime": {"openbeast_commit": "b" * 40, "openbeast_dirty": True}}
    notes = _notes([_entry("aa", 290, **_FULL), _entry("bb", 280, **other)])
    marks, text = notes[2]
    assert marks == "‡"
    for want in ("engine build 9690 (row 1: 10254)", "reasoning budget 20480 (row 1: -1)",
                 "jobs 1 (row 1: 4)", "repo commit bbbbbbbbb (row 1: aaaaaaaaa",
                 "uncommitted changes"):
        assert want in text, (want, text)


def test_the_same_regime_gets_no_footnote():
    """Negative control: two complete rows from one regime are left alone,
    and the board prints no legend at all."""
    entries = [_entry("aa", 290, **_FULL), _entry("bb", 280, **_FULL)]
    assert _notes(entries) == {}
    board = scoring.format_leaderboard(entries)
    assert "†" not in board and "‡" not in board


def test_era_is_the_harness_identity_when_both_rows_stamp_it():
    a = _entry("aa", 290, **_FULL, harness={"era": "1111111111111111"})
    same_era_new_commit = {**_FULL, "runtime": {"openbeast_commit": "c" * 40}}
    b = _entry("bb", 280, **same_era_new_commit, harness={"era": "1111111111111111"})
    assert _notes([a, b]) == {}                # a docs commit is not a new era
    c = _entry("cc", 270, **_FULL, harness={"era": "2222222222222222"})
    assert "era 222222222 (row 1: 111111111" in _notes([a, c])[2][1]


def test_what_row_one_does_not_record_is_not_assumed_equal():
    notes = _notes([_entry("aa", 290, inference_engine={"build": 9690}),
                    _entry("bb", 280, **_FULL)])
    assert notes[1][0] == "†"
    assert notes[2][0] == "‡" and "jobs 4 (row 1: unrecorded)" in notes[2][1]


def test_footnotes_never_reorder_or_drop_rows():
    entries = [_entry("aa", 250), _entry("bb", 290, **_FULL), _entry("cc", 270, jobs=4)]
    board = scoring.format_leaderboard(entries)
    ranked = [ln.split()[1] for ln in board.splitlines()
              if ln[:2].strip().isdigit() and " v4 " in ln]
    assert ranked == ["BB", "CC", "AA"]        # by score, exactly as before
    assert _cells(board, "CC")[-1] == "†" and _cells(board, "AA")[-1] == "†"
    assert _cells(board, "BB")[-1] == "0"      # T/O is the last cell: no mark
    assert scoring.PROVENANCE_LEGEND in board


def test_an_entry_scored_before_the_field_says_so():
    e = _entry("aa", 290, **_FULL)
    del e["provenance"]
    assert "predates the provenance field" in _notes([e])[1][1]


def test_html_board_carries_the_footnotes():
    page = scoring.format_leaderboard_html(
        [_entry("aa", 290, **_FULL), _entry("bb", 280, inference_engine={"build": 1})])
    assert "BB †‡</td>" in page
    assert "no repo commit, reasoning budget, jobs recorded" in page
    assert "engine build 1 (row 1: 10254)" in page


def test_run_eval_stamps_the_era_it_ran_under(tmp_path, monkeypatch):
    import importlib
    # A plain baseline run, whatever arm an earlier test (or the caller's
    # shell) left exported: run_eval writes OPENBEAST_PACKS into os.environ.
    for flag in ("BEAST_PACKS", "OPENBEAST_PACKS", "BEAST_ASSIST", "OPENBEAST_DIAGNOSTICS",
                 "BEAST_ESCALATE", "OPENBEAST_ESCALATE", "OPENBEAST_EVAL_GREEDY",
                 "OPENBEAST_EVAL_ENV_ERA"):
        monkeypatch.delenv(flag, raising=False)
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "01_a.json").write_text(json.dumps({
        "id": "01_a", "name": "a", "difficulty": "easy", "task": "t", "max_iter": 2,
        "validation": {"type": "bash", "script": "true"}}))
    run_eval.TASKS_DIR = str(tmp_path / "tasks")
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    monkeypatch.setattr(run_eval, "capture_server_config", lambda *a, **k: {})
    monkeypatch.setattr(run_eval, "capture_gpu_info", lambda: {})
    monkeypatch.setattr(run_eval, "capture_inference_engine_info", lambda: {})
    monkeypatch.setattr(run_eval, "run_agent", lambda *a, **k: {
        "exit_code": 0, "elapsed_seconds": 1.0, "stdout": "", "stderr": "",
        "tokens": {"prompt": 1, "completion": 2, "total": 3}})
    res = run_eval.run_eval(model_name="m")
    assert res["harness"]["era"] == cache.context_hash()
    assert scoring.run_provenance(res)["era"] == cache.context_hash()
    assert scoring.ineligibility_reasons(res) == []    # the stamp is not an arm
