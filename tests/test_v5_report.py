#!/usr/bin/env python3
"""evals/v5_report.py: the tables of a v5 campaign report.

  - per arm: passes and SCORE as the board scores them, unit-hours, wall hits
  - an in-progress file is "N of M done" with no SCORE
  - deltas list the units gained and lost, by id, over units both arms ran
  - the output carries unit ids and verdicts only (it goes into docs/)
  - the committed DeepSeek board runs give the published ladder

Run: pytest tests/test_v5_report.py
"""

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "evals"))

import v5_report  # noqa: E402

MANIFEST = {
    "1_a_py": {"base": "task-1", "lang": "python", "difficulty": "hard", "k": 2},
    "1_a_go": {"base": "task-1", "lang": "go", "difficulty": "hard", "k": 2},
    "2_b": {"base": "task-2", "lang": "python", "difficulty": "medium", "k": 1},
}
SECRET = "SECRET-VALIDATOR-DETAIL"


def row(uid, passed, secs=600.0, exit_code=0, turns=None):
    r = {"id": uid, "passed": passed, "role": "scored", "elapsed_seconds": secs,
         "tokens_completion": 100, "agent_exit_code": exit_code,
         "validation_output": "OK" if passed else f"FAIL: {SECRET}"}
    if turns is not None:
        r["iterations"] = turns
    return r


def write(d, name, tasks, cap=None):
    switches = {"thinking": "on"} | ({"turn_cap": cap} if cap else {})
    with open(os.path.join(d, name), "w") as f:
        json.dump({"timestamp": "2026-11-19T10:00:00", "suite_version": "v5",
                   "server": {"reasoning_budget": "16384"},
                   "harness": {"era": "era" + name[:2], "agent_switches": switches},
                   "tasks": tasks}, f)
    return name


def setup(tmp_path):
    d = str(tmp_path)
    board = {"runs": [
        {"id": "M-T", "model": "M", "config": "base", "campaign_arm": "camp T",
         "files": [write(d, "t.json", [row("1_a_py", True), row("1_a_go", False, 3600, -1),
                                       row("2_b", True, 3600, -1)])]},
        {"id": "M-T2", "model": "M", "config": "cap", "campaign_arm": "camp T2",
         "files": [write(d, "t2.json", [row("1_a_py", True), row("1_a_go", True, 3600, -1, 30),
                                        row("2_b", False, 1200)], cap=8192)]},
        {"id": "OTHER", "model": "N", "config": "x",
         "files": [write(d, "n.json", [row(u, False) for u in MANIFEST])]},
    ]}
    partial = os.path.join(d, "up.json")
    write(d, "up.json", [row("1_a_py", False, 300)])
    return board, partial


def build(tmp_path, **kw):
    board, partial = setup(tmp_path)
    return v5_report.build("M", [("UP", [partial])], board=board, base_dir=str(tmp_path),
                           manifest=MANIFEST, exclusions={}, context=("OTHER",),
                           pairs=["T:T2", "T2:UP", "T:NOPE"], **kw)


def test_an_unknown_explicit_pair_is_named(tmp_path):
    import pytest
    with pytest.raises(SystemExit, match="NOPE"):
        build(tmp_path, strict_pairs=True)


def test_arms_scores_and_process(tmp_path):
    r = build(tmp_path)
    arms = {a["label"]: a for a in r["arms"]}
    assert list(arms) == ["T", "T2", "UP"]               # campaign_arm's last word; extras last
    t, t2 = arms["T"], arms["T2"]
    assert (t["passed"], t2["passed"]) == (2, 2)
    assert t["complete"] and t2["complete"]
    assert t["unit_hours"] == round((600 + 3600 + 3600) / 3600, 2)
    assert (t["wall_hits"], t["wall_hit_passed"]) == (2, 1)
    assert t["wall_hit_turns"] is None                    # no turn counts on the rows
    assert t2["wall_hit_turns"] == [30, 30]
    assert t["turn_cap"] == 16384 and t2["turn_cap"] == 8192   # declared budget, else the switch
    assert [c["id"] for c in r["context"]] == ["OTHER"]


def test_in_progress_arm_has_no_score(tmp_path):
    r = build(tmp_path)
    up = r["arms"][-1]
    assert not up["complete"] and up["done"] == 1 and not up["on_board"]
    md = v5_report.render(r)
    assert "0 of 1 done (in progress)" in md
    line = next(x for x in md.splitlines() if x.startswith("| UP | UP |"))
    assert line.count("—") >= 3                           # SCORE, SOLVE, LANG withheld


def test_deltas_by_unit_over_common_units(tmp_path):
    r = build(tmp_path)
    d = {(x["base"], x["arm"]): x for x in r["deltas"]}
    assert set(d) == {("T", "T2"), ("T2", "UP")}          # a pair with a missing arm is skipped
    assert d[("T", "T2")]["gained"] == ["1_a_go"] and d[("T", "T2")]["lost"] == ["2_b"]
    assert d[("T", "T2")]["net"] == 0
    assert d[("T2", "UP")]["compared"] == 1 and d[("T2", "UP")]["lost"] == ["1_a_py"]
    assert r["differ"] == ["1_a_go", "1_a_py", "2_b"]


def test_render_carries_ids_and_verdicts_only(tmp_path):
    md = v5_report.render(build(tmp_path))
    assert SECRET not in md                               # never a validator's output
    assert "| 2_b | P | F | · |" in md


def test_published_deepseek_ladder():
    """The committed board: T 61, T2 65 (+4, no regressions), T_s1 61, T3 65."""
    r = v5_report.build()
    arms = {a["label"]: a for a in r["arms"]}
    assert {k: arms[k]["passed"] for k in ("T", "T_s1", "T2", "T3")} == \
        {"T": 61, "T_s1": 61, "T2": 65, "T3": 65}
    assert round(arms["T2"]["score"], 1) == 94.9
    d = {(x["base"], x["arm"]): x for x in r["deltas"]}
    assert d[("T", "T2")]["lost"] == [] and len(d[("T", "T2")]["gained"]) == 4
    assert len(d[("T2", "T3")]["gained"]) == len(d[("T2", "T3")]["lost"]) == 3
    # every arm names its exact weights, and the filter keeps one quantization
    assert {a["weights"] for a in r["arms"]} == {"EXL3 2.9 bpw · Mia EAQ v1.1 -v2 · stock"}
    assert "| T2 | DS-T2 | EXL3 2.9 bpw · Mia EAQ v1.1 -v2 · stock | yes |" in v5_report.render(r)
    one = v5_report.build(weights_id="dsv41f-mia-exl3-2.9bpw-eaq1.1-v2")
    assert [a["id"] for a in one["arms"]] == [a["id"] for a in r["arms"]]
    assert v5_report.build(weights_id="some-other-quant")["arms"] == []


def test_cross_model_pair_names_the_context_run_by_id(tmp_path):
    board, partial = setup(tmp_path)
    r = v5_report.build("M", [], board=board, base_dir=str(tmp_path), manifest=MANIFEST,
                        exclusions={}, context=("OTHER",), pairs=["OTHER:T2"])
    (d,) = r["deltas"]
    assert (d["base"], d["arm"]) == ("OTHER", "T2") and d["gained"] == ["1_a_go", "1_a_py"]
    assert r["differ"] == ["1_a_go", "2_b"]               # still over the model's own arms


def test_published_hy4_against_deepseek():
    """HY4-T2 60/71 on DS-T2's harness9 tree: the board's cross-model pair."""
    r = v5_report.build("Hy4-flash (NVFP4)", context=("DS-T2",), pairs=["DS-T2:H_T2"])
    (h,) = [a for a in r["arms"] if a["id"] == "HY4-T2"]
    assert (h["passed"], round(h["score"], 1)) == (60, 83.4)
    (d,) = r["deltas"]
    assert (d["compared"], d["net"]) == (71, -5)


def test_published_hy4_ablation():
    """HY4-C 55/71 against HY4-T2 60/71 on one harness9 tree: T2's bundle +5 (7 gained, 2 lost)."""
    r = v5_report.build("Hy4-flash (NVFP4)", context=(), pairs=["H_C:H_T2"])
    assert {a["id"]: (a["passed"], round(a["score"], 1)) for a in r["arms"]} == {"HY4-T2": (60, 83.4), "HY4-C": (55, 82.2)}
    (d,) = r["deltas"]
    assert (d["compared"], d["net"], len(d["gained"]), len(d["lost"])) == (71, 5, 7, 2)


def test_cli_runs_and_writes_nothing():
    before = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout
    out = subprocess.run([sys.executable, os.path.join(REPO, "evals", "v5_report.py")],
                         capture_output=True, text=True, check=True).stdout
    assert out.startswith("<!-- generated by evals/v5_report.py")
    after = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout
    assert before == after


def test_write_splices_only_the_block(tmp_path):
    doc = tmp_path / "r.md"
    doc.write_text("head\n<!-- v5-report:start -->\nold\n<!-- v5-report:end -->\ntail\n")
    assert v5_report.write_block(str(doc), "NEW") == 0
    assert doc.read_text() == "head\n<!-- v5-report:start -->\nNEW\n<!-- v5-report:end -->\ntail\n"
    bare = tmp_path / "bare.md"
    bare.write_text("no block\n")
    assert v5_report.write_block(str(bare), "NEW") == 1
    assert bare.read_text() == "no block\n"
