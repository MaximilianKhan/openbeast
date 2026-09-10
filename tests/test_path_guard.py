"""R1 path guard tests (2026-09-10): warn on wrong-place writes, refuse
task_done while expected task files are missing."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
import tools  # noqa: E402


def test_inert_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENBEAST_TASK_PATHS", raising=False)
    out = tools.write_file(str(tmp_path / "x.py"), "x = 1\n")
    assert "path check" not in out
    assert tools.task_done("done").startswith("TASK_DONE")


def test_warns_on_wrong_place_same_basename(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("OPENBEAST_TASK_PATHS",
                       json.dumps([str(tmp_path / "expected" / "sol.py")]))
    out = tools.write_file(str(tmp_path / "elsewhere" / "sol.py"), "x = 1\n")
    assert "path check" in out and "expected/sol.py" in out


def test_no_warning_at_expected_path(tmp_path, monkeypatch):
    import json
    target = tmp_path / "eval_x" / "sol.py"
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(target)]))
    out = tools.write_file(str(target), "x = 1\n")
    assert "path check" not in out


def test_task_done_refuses_on_missing_expected_file(tmp_path, monkeypatch):
    import json
    missing = tmp_path / "eval_y" / "answer.zig"
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(missing)]))
    out = tools.task_done("all good")
    assert out.startswith("NOT DONE") and "answer.zig" in out


def test_task_done_ignores_extensionless_artifacts(tmp_path, monkeypatch):
    import json
    src = tmp_path / "eval_z" / "prog.zig"
    src.parent.mkdir(parents=True)
    src.write_text("pub fn main() void {}\n")
    binary = tmp_path / "eval_z" / "prog"  # built by validation, not the agent
    monkeypatch.setenv("OPENBEAST_TASK_PATHS",
                       json.dumps([str(src), str(binary)]))
    assert tools.task_done("done").startswith("TASK_DONE")


def test_task_done_passes_when_files_exist(tmp_path, monkeypatch):
    import json
    f = tmp_path / "eval_w" / "s.py"
    f.parent.mkdir(parents=True)
    f.write_text("x = 1\n")
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(f)]))
    assert tools.task_done("done").startswith("TASK_DONE")
