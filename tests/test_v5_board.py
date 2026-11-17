#!/usr/bin/env python3
"""evals/v5_board.py: the v5 leaderboard (best configuration per model).

  - each model shows once, with its best configuration by SCORE
  - a repair run supersedes infrastructure rows and never a real verdict
  - a reference ledger is marked as a ceiling and never ranked
  - rendering is idempotent, and the committed docs match the generator
    (the CI drift gate)

Run: pytest tests/test_v5_board.py
"""

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "evals"))

import v5_board  # noqa: E402

# A three-unit manifest: one two-language base (python + go) and one singleton.
MANIFEST = {
    "1_a_py": {"base": "task-1", "lang": "python", "difficulty": "hard", "k": 2},
    "1_a_go": {"base": "task-1", "lang": "go", "difficulty": "hard", "k": 2},
    "2_b": {"base": "task-2", "lang": "python", "difficulty": "medium", "k": 1},
}
CNFP = "Could not find platform independent libraries <prefix>"


def row(uid, passed, **kw):
    r = {"id": uid, "passed": passed, "role": "scored", "elapsed_seconds": 60.0,
         "tokens_completion": 100, "agent_exit_code": 0, "validation_output": "ok" if passed else "FAIL"}
    r.update(kw)
    return r


def results(tasks, ts="2026-11-16T10:00:00", **header):
    return {"timestamp": ts, "suite_version": "v5", "harness": {"arm": "L0", "board_arm": "L0"},
            "tasks": tasks, **header}


def write(d, name, obj):
    path = os.path.join(d, name)
    with open(path, "w") as f:
        if name.endswith(".jsonl"):
            f.write("\n".join(json.dumps(r) for r in obj) + "\n")
        else:
            json.dump(obj, f)
    return name


def board(tmp_path):
    d = str(tmp_path)
    runs = [
        {"id": "M-weak", "model": "Model M", "config": "bare",
         "files": [write(d, "m1.json", results([row("1_a_py", True), row("1_a_go", False),
                                                 row("2_b", False)]))]},
        {"id": "M-strong", "model": "Model M", "config": "thinking on",
         "files": [write(d, "m2.json", results([row("1_a_py", True), row("1_a_go", True),
                                                 row("2_b", True)], ts="2026-11-17T10:00:00",
                                                wall_scale=2.0))]},
        {"id": "N", "model": "Model N", "config": "bare",
         "files": [write(d, "n.json", results([row("1_a_py", True), row("1_a_go", False),
                                                row("2_b", True)]))]},
        {"id": "REF", "model": "Ref Model", "config": "other scaffold", "reference": True,
         "files": [write(d, "ref.jsonl", [
             {"unit": u, "passed": True, "indeterminate": False, "elapsed_s": 30.0,
              "over_budget": u == "2_b", "graded_at": 1794860750.0} for u in MANIFEST])]},
    ]
    return {"runs": runs, "not_yet_run": ["Model Q"]}


def test_best_configuration_per_model(tmp_path):
    b = v5_board.build(board(tmp_path), base_dir=str(tmp_path), manifest=MANIFEST)
    best = {r["model"]: r for r in b["best"]}
    assert len(b["best"]) == 3                    # one row per model, not per run
    assert best["Model M"]["id"] == "M-strong"
    assert best["Model M"]["passed"] == 3 and best["Model M"]["score"] == 100.0
    assert best["Model M"]["experiment"] == ["wall ×2"]
    assert best["Model N"]["experiment"] == []
    # ranked: M (100) above N, reference last whatever its score
    assert [r["model"] for r in b["best"]] == ["Model M", "Model N", "Ref Model"]
    assert best["Model N"]["by_lang"] == {**{k: (0, 0) for k, _ in v5_board.LANGS},
                                          "python": (2, 2), "go": (0, 1)}


def test_repair_supersedes_infra_rows_only(tmp_path):
    d = str(tmp_path)
    main = write(d, "main.json", results([
        row("1_a_py", False, tokens_completion=0, elapsed_seconds=0.0, agent_exit_code=1,
            validation_output=CNFP),                         # agent never started
        row("1_a_go", False, validation_output=CNFP),         # validator never started
        row("2_b", False),                                    # a real fail
    ]))
    repair = write(d, "repair.json", results([row("1_a_py", True), row("1_a_go", True),
                                              row("2_b", True)], ts="2026-11-16T12:00:00"))
    _, rows = v5_board.merge_runs([os.path.join(d, main), os.path.join(d, repair)])
    assert rows["1_a_py"]["passed"] and rows["1_a_go"]["passed"]
    assert not rows["2_b"]["passed"]                         # never re-rolled
    # ...and an infra row arriving later never replaces a real verdict
    late = write(d, "late.json", results([row("2_b", False, reason="server_error")]))
    _, rows = v5_board.merge_runs([os.path.join(d, repair), os.path.join(d, late)])
    assert rows["2_b"]["passed"]


def test_unrepaired_lost_unit_counts_as_fail(tmp_path):
    d = str(tmp_path)
    f = write(d, "r.json", results([row("1_a_py", True),
                                    row("1_a_go", False, reason="validator_timeout")]))
    r = v5_board.score_run({"id": "x", "model": "m", "config": "c", "files": [f]},
                           MANIFEST, base_dir=d)
    assert r["passed"] == 1 and r["units"] == 3
    assert r["lost"] == {"1_a_go": "validator_timeout", "2_b": "missing"}
    assert "2 units lost" in v5_board._status(r)


def test_reference_is_marked_and_unranked(tmp_path):
    b = v5_board.build(board(tmp_path), base_dir=str(tmp_path), manifest=MANIFEST)
    ref = b["best"][-1]
    assert ref["reference"] and ref["passed"] == 3 and ref["passed_strict_wall"] == 2
    text = v5_board.render_board(b)
    lines = [ln for ln in text.splitlines() if ln.startswith("| ")]
    assert lines[1].startswith("| 1 | **Model M**") and lines[2].startswith("| 2 | **Model N**")
    assert lines[3].startswith("| ref | *Ref Model* (reference)")
    assert "reference ceiling, not ranked" in lines[3]
    assert lines[4].startswith("| — | Model Q |  | not yet run on v5")
    # every row has the header's column count
    assert len({ln.count("|") for ln in lines}) == 1
    assert "2/3 at the 1× wall" in v5_board.render_runs(b)


def test_render_is_idempotent(tmp_path):
    b = v5_board.build(board(tmp_path), base_dir=str(tmp_path), manifest=MANIFEST)
    doc = "intro\n<!-- v5-board:start -->\nold\n<!-- v5-board:end -->\ntail\n"
    once = v5_board.splice(doc, "v5-board", v5_board.render_board(b))
    twice = v5_board.splice(once, "v5-board", v5_board.render_board(b))
    assert once == twice and once.startswith("intro\n") and once.endswith("tail\n")
    assert v5_board.splice("no markers", "v5-board", "x") is None


def test_update_docs_detects_and_fixes_drift(tmp_path):
    b = v5_board.build(board(tmp_path), base_dir=str(tmp_path), manifest=MANIFEST)
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    for rel in v5_board.DOCS:
        (root / rel).write_text("x\n<!-- v5-board:start -->\n<!-- v5-board:end -->\n")
    assert sorted(v5_board.update_docs(b, write=True, root=str(root))) == sorted(v5_board.DOCS)
    assert v5_board.update_docs(b, write=False, root=str(root)) == []
    # links are relative to each doc
    assert "](evals/boards/v5/m2.json)" in (root / "README.md").read_text()
    assert "](../evals/boards/v5/m2.json)" in (root / "docs" / "RESULTS.md").read_text()


def test_add_run_copies_and_replaces(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    bdir = tmp_path / "board"
    bdir.mkdir()
    f = write(str(src), "eval-x.json", results([row("2_b", True)]))
    b = {"runs": [{"id": "X", "model": "m", "config": "old", "files": []}]}
    v5_board.add_run(b, "X", "m", "new", [str(src / f)], campaign_arm="arm E",
                     board_dir=str(bdir))
    assert (bdir / "eval-x.json").exists()
    assert b["runs"] == [{"id": "X", "model": "m", "config": "new", "files": ["eval-x.json"],
                          "campaign_arm": "arm E"}]


def test_exclusions_count_as_not_passed_and_footnote(tmp_path):
    excl = {"exclusions": [{"unit": "1_a_go", "run": "M-strong", "reason": "pending integrity review"},
                           {"unit": "2_b", "file": "n.json", "reason": "contaminated"}],
            "validator_notes": [{"unit": "2_b", "run": "M-weak", "note": "a validator false FAIL"}]}
    b = v5_board.build(board(tmp_path), base_dir=str(tmp_path), manifest=MANIFEST, exclusions=excl)
    runs = {r["id"]: r for r in b["runs"]}
    assert runs["M-strong"]["passed"] == 2 and runs["M-strong"]["passed_raw"] == 3
    assert runs["M-strong"]["excluded"] == {"1_a_go": "pending integrity review"}
    assert runs["N"]["passed"] == 1 and runs["N"]["excluded"] == {"2_b": "contaminated"}
    assert runs["M-weak"]["passed"] == 1 and runs["M-weak"]["excluded"] == {}  # a note never adjusts
    assert runs["REF"]["excluded"] == {}                                    # run/file scoping
    text = v5_board.render_board(b)
    assert "**2/3**\\*" in text
    assert "\\* Model M, M-strong: 3/3 raw; 1 unit (1_a_go) excluded pending integrity review." in text
    runs_text = v5_board.render_runs(b)
    assert "2_b is a validator false FAIL; not adjusted" in runs_text
    # an absent exclusions file excludes nothing
    assert v5_board.load_exclusions(str(tmp_path / "none.json")) == {"exclusions": [], "validator_notes": []}


def test_committed_board_numbers():
    """The committed inputs reproduce the numbers the docs claim."""
    b = v5_board.build()
    runs = {r["id"]: r for r in b["runs"]}
    assert b["units"] == 71 and b["bases"] == 34
    assert {k: runs[k]["passed"] for k in runs} == {
        "GLM-L0": 26, "GLM-A": 31, "GLM-B": 36, "GLM-C": 53, "OPUS-REF": 71}
    assert runs["GLM-C"]["passed_raw"] == 54  # 122_gemm_blocked_b excluded pending review
    assert runs["OPUS-REF"]["passed_strict_wall"] == 68
    assert all(not r["lost"] for r in runs.values())
    best = {r["model"]: r["id"] for r in b["best"]}
    assert best == {"GLM-5.3-Flash": "GLM-C", "Claude Opus 5.5": "OPUS-REF"}


def test_docs_are_current():
    """The CI drift gate: README.md and docs/RESULTS.md carry the generated blocks."""
    p = subprocess.run([sys.executable, os.path.join(REPO, "evals", "v5_board.py"), "--check"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
