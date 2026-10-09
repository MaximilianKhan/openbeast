"""Leaderboard eligibility is ENFORCED, not just documented.

Pins the 2026-09-29 review findings eval-harness-3 / eval-harness-4 /
open-prs-2: experiment arms (greedy, packs, diagnostics, escalate) were
labelled "leaderboard-ineligible" but the newest full-suite run won
regardless, and a --cache-only replay whose units all missed seated a 0/291
row. Every results file here is built in a temp dir.
"""

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
sys.path.insert(0, str(ROOT / "agents"))

import scoring  # noqa: E402


def _results(ts: str, passed: int = 291, harness=None, reason=None, **extra) -> dict:
    tasks = []
    for i in range(291):
        t = {"id": f"t{i:03d}", "difficulty": "easy", "passed": i < passed,
             "elapsed_seconds": 1.0}
        if reason and i >= passed:
            t["reason"] = reason
        tasks.append(t)
    return {"timestamp": ts, "model": "M", "model_slug": "m", "suite_version": "v4",
            "gpu": {"host_id": "rig"}, "harness": harness or {},
            "summary": {"total": 291}, "tasks": tasks, **extra}


def test_reasons_cover_every_arm_and_infra_rows():
    assert scoring.ineligibility_reasons(_results("t")) == []
    assert scoring.ineligibility_reasons(_results("t", harness={"diagnostics": False,
                                                                 "greedy": False,
                                                                 "packs": {}})) == []
    for harness, word in (({"greedy": True}, "greedy"),
                          ({"diagnostics": True}, "diagnostics"),
                          ({"packs": {"zig": "abcd1234"}}, "packs"),
                          ({"escalate_component": "esc1-deadbeef"}, "escalation")):
        (r,) = scoring.ineligibility_reasons(_results("t", harness=harness))
        assert word in r
    (r,) = scoring.ineligibility_reasons(_results("t", suite_selection="v5-fast"))
    assert "v5-fast" in r
    for reason in ("skipped_cache_miss", "server_error", "env_error",
                   "server_unhealthy", "setup_failed"):
        (r,) = scoring.ineligibility_reasons(_results("t", passed=290, reason=reason))
        assert reason in r
    # A plain model FAIL carries no reason and stays eligible.
    assert scoring.ineligibility_reasons(_results("t", passed=250)) == []


def test_update_leaderboard_refuses_ineligible(tmp_path):
    lb = str(tmp_path / "leaderboard.json")
    base = scoring.score_run(_results("2026-09-01T00:00:00", passed=255))
    scoring.update_leaderboard(base, path=lb)
    treated = scoring.score_run(_results("2026-09-02T00:00:00", passed=290,
                                         harness={"escalate_component": "esc1-x",
                                                  "greedy": True}))
    entries = scoring.update_leaderboard(treated, path=lb)
    assert [e["tasks_passed"] for e in entries] == [255]
    # force=True is the deliberate override and still works.
    entries = scoring.update_leaderboard(treated, path=lb, force=True)
    assert [e["tasks_passed"] for e in entries] == [290]


def test_update_leaderboard_refuses_cache_only_misses(tmp_path):
    lb = str(tmp_path / "leaderboard.json")
    empty = scoring.score_run(_results("t", passed=0, reason="skipped_cache_miss"))
    assert scoring.update_leaderboard(empty, path=lb) == []
    ok = scoring.score_run(_results("t", passed=280))
    assert len(scoring.update_leaderboard(ok, path=lb)) == 1


def test_rebuild_keeps_baseline_over_newer_experiment(tmp_path, monkeypatch, capsys):
    res = tmp_path / "results"
    res.mkdir()
    lb = tmp_path / "leaderboard.json"
    files = {
        "eval-m-20260901-000000.json": _results("2026-09-01T00:00:00", passed=255),
        "eval-m-20260902-000000.json": _results("2026-09-02T00:00:00", passed=248,
                                                harness={"greedy": True,
                                                         "diagnostics": True,
                                                         "packs": {"zig": "x"}}),
        "eval-m-20260903-000000.json": _results("2026-09-03T00:00:00", passed=0,
                                                reason="skipped_cache_miss"),
    }
    for name, data in files.items():
        (res / name).write_text(json.dumps(data))
    monkeypatch.setattr(scoring, "RESULTS_DIR", str(res))
    monkeypatch.setattr(scoring, "LEADERBOARD_PATH", str(lb))
    monkeypatch.setattr(sys, "argv", ["scoring.py", "--rebuild"])
    scoring.main()
    entries = json.loads(lb.read_text())["entries"]
    assert [e["tasks_passed"] for e in entries] == [255]
    assert "2 partial/fast-suite/ineligible" in capsys.readouterr().out


def test_benchmark_all_experiment_arms_imply_no_leaderboard():
    for mod in ("cache", "run_eval", "benchmark_all"):
        sys.modules.pop(mod, None)
    ba = importlib.import_module("benchmark_all")
    assert ba.experiment_arms({}) == []
    assert ba.experiment_arms({"BEAST_ASSIST": "0", "BEAST_PACKS": "0"}) == []
    assert ba.experiment_arms({"OPENBEAST_EVAL_GREEDY": "1"}) == ["--greedy"]
    assert ba.experiment_arms({"BEAST_PACKS": "1"}) == ["--packs"]
    assert ba.experiment_arms({"BEAST_ASSIST": "1"}) == ["beast-assist diagnostics"]
    assert ba.experiment_arms({"OPENBEAST_DIAGNOSTICS": "1"}) == ["beast-assist diagnostics"]


def test_full_hit_cache_only_replay_is_not_seated(tmp_path, monkeypatch, capsys):
    """A --cache-only replay where EVERY unit hit has no live host: seated,
    it became a second (unknown-host, m) row beside the real rig's row."""
    lb = str(tmp_path / "leaderboard.json")
    live = scoring.score_run(_results("2026-09-01T00:00:00", passed=255))
    scoring.update_leaderboard(live, path=lb)
    replay = _results("2026-09-02T00:00:00", passed=291, cache_only=True)
    replay.update(gpu=None, inference_engine=None, server=None)
    (r,) = scoring.ineligibility_reasons(replay)
    assert "cache-only" in r
    entries = scoring.update_leaderboard(scoring.score_run(replay), path=lb)
    assert [(scoring.entry_host_id(e), e["tasks_passed"]) for e in entries] == [("rig", 255)]
    # Legacy replays predate the flag: gpu/engine/server all null.
    legacy = _results("t")
    legacy.update(gpu=None, inference_engine=None, server=None)
    assert scoring.ineligibility_reasons(legacy)
    # Negative control: a LIVE run on a host without nvidia-smi records {}
    # and stays eligible.
    no_nv = _results("t")
    no_nv.update(gpu={}, inference_engine={}, server={}, cache_only=False)
    assert scoring.ineligibility_reasons(no_nv) == []


def test_run_eval_stamps_cache_only(tmp_path):
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = tmp_path / "cache"
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "01_a.json").write_text(json.dumps({
        "id": "01_a", "name": "a", "difficulty": "easy", "task": "a",
        "validation": {"type": "bash", "script": "true"}, "max_iter": 3}))
    run_eval.TASKS_DIR = str(tasks)
    run_eval.RESULTS_DIR = str(tmp_path / "results")
    task = run_eval.load_tasks(None)[0]
    cache.cache_put(cache.cache_key(task, "m", max_iter=3),
                    {"id": "01_a", "passed": True, "elapsed_seconds": 1.0,
                     "tokens_completion": 5})
    res = run_eval.run_eval(model_name="m", cache_only=True, reasoning_budget="-1")
    assert res["tasks"][0]["from_cache"] is True       # a full hit
    assert res["cache_only"] is True
    assert scoring.ineligibility_reasons(res)


def test_full_hit_replay_through_benchmark_all_and_rebuild_adds_no_row(
        tmp_path, monkeypatch):
    """End to end, the two paths the review named: `benchmark_all.py
    --cache-only` (run_sweep -> update_leaderboard) and `scoring.py
    --rebuild`, with the REAL run_eval replaying a full hit. The suite is
    given no expected unit count so the partial-run guards cannot mask the
    cache-only check; the only thing keeping the replay off the board is
    that it is a replay."""
    for mod in ("cache", "run_eval", "benchmark_all"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "STRIKES_DIR", tmp_path / "cache" / "env-strikes")
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    ba = importlib.import_module("benchmark_all")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "01_a.json").write_text(json.dumps({
        "id": "01_a", "name": "a", "difficulty": "easy", "task": "a",
        "validation": {"type": "bash", "script": "true"}, "max_iter": 3}))
    results_dir = tmp_path / "results"
    monkeypatch.setattr(run_eval, "TASKS_DIR", str(tasks))
    monkeypatch.setattr(run_eval, "RESULTS_DIR", str(results_dir))
    monkeypatch.setattr(run_eval, "capture_suite_version", lambda: "vtest")
    task = run_eval.load_tasks(None)[0]
    cache.cache_put(cache.cache_key(task, "m", max_iter=3),
                    {"id": "01_a", "passed": True, "elapsed_seconds": 1.0,
                     "tokens_completion": 5})

    # The real rig's row for the model, and its live results file.
    lb = tmp_path / "leaderboard.json"
    live = {"timestamp": "2026-09-01T00:00:00", "model": "m", "model_slug": "m",
            "suite_version": "vtest", "gpu": {"host_id": "rig"},
            "inference_engine": {}, "server": {}, "cache_only": False,
            "harness": {}, "summary": {"total": 1},
            "tasks": [{"id": "01_a", "difficulty": "easy", "passed": False,
                       "elapsed_seconds": 1.0}]}
    results_dir.mkdir()
    (results_dir / "eval-live.json").write_text(json.dumps(live))
    real_update = scoring.update_leaderboard
    monkeypatch.setattr(ba.scoring, "update_leaderboard",
                        lambda e, **k: real_update(e, path=str(lb), **k))
    real_update(scoring.score_run(live), path=str(lb))

    def rows():
        return [(scoring.entry_host_id(e), e["model_slug"], e["tasks_passed"])
                for e in scoring.load_leaderboard(str(lb))]
    assert rows() == [("rig", "m", 0)]

    out = ba.run_sweep([{"slug": "m", "name": "m", "serve": "x"}], None, None,
                       cache_only=True, reasoning_budget="-1")
    assert out["models_succeeded"] == 1
    assert out["scores"][0]["tasks_passed"] == 1           # a full hit
    assert "cache-only" in " ".join(out["scores"][0]["ineligible_reasons"])
    assert rows() == [("rig", "m", 0)], "the replay was seated"

    # scoring.py --rebuild over the live file + the replay's results file.
    assert len(list(results_dir.glob("eval-*.json"))) == 2
    monkeypatch.setattr(scoring, "RESULTS_DIR", str(results_dir))
    monkeypatch.setattr(scoring, "LEADERBOARD_PATH", str(lb))
    monkeypatch.setattr(sys, "argv", ["scoring.py", "--rebuild"])
    scoring.main()
    assert rows() == [("rig", "m", 0)], "--rebuild re-seated the replay"
