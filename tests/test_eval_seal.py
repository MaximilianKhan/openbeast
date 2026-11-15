"""The oracle seal and the eval-time tool rules in agents/tools.py (v5 brief
§6.3/§6.4).

While a unit is measured (OPENBEAST_EVAL=1) the agent must not be able to
read the suite's answer key — evals/tasks (validators: hidden-test
generators and oracles), evals/refs (reference solutions), evals/suites
(the manifest) — nor look answers up online. Outside eval nothing changes.
Also pinned here: the per-unit diagnostics language allowlist and the
exposure telemetry the A/B needs to tell "never fired" from "no effect".
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))

import tools  # noqa: E402


@pytest.fixture
def sealed(tmp_path, monkeypatch):
    """A fake checkout's evals/{tasks,refs,suites}, sealed via the env hook,
    plus an unsealed sibling dir."""
    ev = tmp_path / "repo" / "evals"
    for d in ("tasks", "refs", "suites", "results"):
        (ev / d).mkdir(parents=True)
    (ev / "tasks" / "163_x.json").write_text('{"validation": "SECRET-ORACLE"}')
    (ev / "refs" / "ref.py").write_text("print('SECRET-REF')\n")
    (ev / "results" / "open.txt").write_text("not sealed\n")
    monkeypatch.setenv("OPENBEAST_EVAL_SEALED_DIRS",
                       os.pathsep.join(str(ev / d) for d in ("tasks", "refs", "suites")))
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    return ev


def test_read_file_refuses_sealed_paths(sealed):
    out = tools.read_file(str(sealed / "tasks" / "163_x.json"))
    assert out.startswith("Error: refusing") and "SECRET" not in out
    assert "not sealed" in tools.read_file(str(sealed / "results" / "open.txt"))


def test_this_checkouts_own_suite_is_sealed(monkeypatch):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    out = tools.read_file(str(ROOT / "evals" / "suites" / "v5.json"))
    assert out.startswith("Error: refusing")
    assert tools.read_file(str(ROOT / "evals" / "SUITE_VERSION")).startswith("[")


def test_symlink_into_the_seal_is_refused(sealed, tmp_path):
    link = tmp_path / "innocent.json"
    link.symlink_to(sealed / "tasks" / "163_x.json")
    assert tools.read_file(str(link)).startswith("Error: refusing")


def test_list_files_and_grep_drop_sealed_paths(sealed):
    listing = tools.list_files(str(sealed), "**/*")
    assert "open.txt" in listing and "163_x" not in listing and "ref.py" not in listing
    assert tools.list_files(str(sealed / "refs")).startswith("Error: refusing")
    hits = tools.grep("SECRET|sealed", str(sealed))
    assert "SECRET" not in hits and "open.txt" in hits
    assert tools.grep("SECRET", str(sealed / "tasks")).startswith("Error: refusing")


def test_bash_refuses_commands_naming_the_seal(sealed):
    out = tools.bash(f"cat {sealed}/tasks/163_x.json")
    assert out.startswith("Error: refusing") and "SECRET" not in out
    assert tools.bash("cat ../evals/refs/ref.py").startswith("Error: refusing")
    assert "hello" in tools.bash("echo hello")


def test_writes_into_the_seal_are_refused(sealed):
    out = tools.write_file(str(sealed / "tasks" / "163_x.json"), "{}")
    assert out.startswith("Error: refusing")
    assert "SECRET-ORACLE" in (sealed / "tasks" / "163_x.json").read_text()


def test_no_seal_outside_eval(sealed, monkeypatch):
    monkeypatch.delenv("OPENBEAST_EVAL")
    assert "SECRET-ORACLE" in tools.read_file(str(sealed / "tasks" / "163_x.json"))
    assert "SECRET-REF" in tools.bash(f"cat {sealed}/refs/ref.py")


def test_web_tools_are_off_under_eval(monkeypatch):
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.delenv("OPENBEAST_EVAL_WEB", raising=False)
    assert "disabled while an eval unit is measured" in tools.fetch("https://example.com/")
    assert "disabled while an eval unit is measured" in tools.web_search("knuth algorithm d")


def test_web_tools_record_mode_logs_every_call(monkeypatch, tmp_path):
    log = tmp_path / "web.jsonl"
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.setenv("OPENBEAST_EVAL_WEB", "record")
    monkeypatch.setenv("OPENBEAST_EVAL_WEB_LOG", str(log))
    monkeypatch.setenv("OPENBEAST_EVAL_UNIT", "163_x_f")
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:9")    # nothing listens
    out = tools.web_search("q")
    assert "disabled" not in out                                # it really ran
    (row,) = [json.loads(ln) for ln in log.read_text().splitlines()]
    assert row["tool"] == "web_search" and row["unit"] == "163_x_f" and row["arg"] == "q"


def test_web_tools_untouched_outside_eval(monkeypatch):
    monkeypatch.delenv("OPENBEAST_EVAL", raising=False)
    assert tools._eval_web_gate("fetch", "https://example.com/") is None


# --- diagnostics: per-unit language allowlist + exposure telemetry ------------

def test_diag_lang_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENBEAST_DIAGNOSTICS", "1")
    monkeypatch.setenv("OPENBEAST_DIAG_LANGS", "zig,c++")
    p = tmp_path / "x.py"
    p.write_text("x = (\n")                      # a syntax error py_compile would flag
    assert tools._run_diagnostics(str(p)) == ""   # python not in the allowlist
    monkeypatch.setenv("OPENBEAST_DIAG_LANGS", "python")
    assert "diagnostics (python)" in tools._run_diagnostics(str(p))
    monkeypatch.delenv("OPENBEAST_DIAG_LANGS")
    assert "diagnostics (python)" in tools._run_diagnostics(str(p))   # unset = all


def test_exposure_telemetry_names_the_unit(monkeypatch, tmp_path):
    log = tmp_path / "diag.jsonl"
    monkeypatch.setenv("OPENBEAST_DIAGNOSTICS", "1")
    monkeypatch.delenv("OPENBEAST_DIAG_LANGS", raising=False)
    monkeypatch.setenv("OPENBEAST_DIAG_TIMING_LOG", str(log))
    monkeypatch.setenv("OPENBEAST_EVAL_UNIT", "40_attention")
    monkeypatch.setenv("OPENBEAST_EVAL_ARM", "L2")
    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n")
    tools._run_diagnostics(str(bad))
    good = tmp_path / "good.py"
    good.write_text("x = 1\n")
    tools._run_diagnostics(str(good))
    rows = [json.loads(ln) for ln in log.read_text().splitlines()]
    assert [r["unit"] for r in rows] == ["40_attention"] * 2
    assert rows[0]["arm"] == "L2"
    assert rows[0]["rc"] != 0 and rows[0]["errors"] >= 1
    assert rows[1]["rc"] == 0 and rows[1]["errors"] == 0
    assert rows[0]["hint_fired"] is False and rows[0]["card_fired"] is False


def test_run_eval_summarizes_exposure(tmp_path):
    sys.path.insert(0, str(ROOT / "evals"))
    import run_eval
    log = tmp_path / "d.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"ms": 10, "unit": "u1", "rc": 1, "errors": 2, "hint_fired": True, "card_fired": False},
        {"ms": 20, "unit": "u1", "rc": 0, "errors": 0, "hint_fired": False, "card_fired": False},
        {"ms": 30, "unit": "u2", "rc": 1, "errors": 1, "hint_fired": False, "card_fired": True},
    ]) + "\n")
    lat, exp = run_eval._summarize_diag_log(str(log))
    assert lat["n"] == 3 and lat["max"] == 30
    assert exp["units_exposed"] == 2 and exp["units_hint_fired"] == 1
    assert exp["units_card_fired"] == 1 and exp["units_with_failed_check"] == 2
    assert exp["per_unit"]["u1"] == {"writes": 2, "errors": 2, "failed_checks": 1,
                                     "hint_fired": True, "card_fired": False}
