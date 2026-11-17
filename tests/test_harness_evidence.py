#!/usr/bin/env python3
"""Evidence-driven harness batch (docs/HARNESS_NOTES.md, 2026-11-16).

Pure unit tests — no model server, no GPU:
  * cache-key safety: OPENBEAST_AGENT_THINKING / OPENBEAST_AGENT_SEED (and
    the two opt-in switches below) are in the cache key, OMITTED when unset
    so legacy keys stay reachable; stamped in the results header and on
    every row; pinned into the agent's env; run_eval --seed / --thinking
  * G9 "test before done" (OPENBEAST_AGENT_VERIFY_DONE=1): one non-blocking
    nudge when a destination was written after its last run
  * G13 "edit, don't rewrite" (OPENBEAST_DIAG_EDIT_HINT=1): a failing
    diagnostics block ends with the edit_file line, N from the first error
  * G15 edit_file reports only lint findings new relative to the pre-edit
    file (default on — a fix)
  * G16 escalation-card eligibility in the run_eval header and the
    ablation exposure line

Run: python3 -m pytest tests/test_harness_evidence.py -q
"""

import importlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "evals"))

import tools  # noqa: E402

AGENT_ENV = ("OPENBEAST_AGENT_THINKING", "OPENBEAST_AGENT_SEED",
             "OPENBEAST_AGENT_VERIFY_DONE", "OPENBEAST_DIAG_EDIT_HINT",
             "OPENBEAST_AGENT_ENV_BLOCK", "OPENBEAST_AGENT_WALL_WARN")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in AGENT_ENV + ("OPENBEAST_TASK_PATHS", "OPENBEAST_DIAGNOSTICS", "BEAST_ASSIST"):
        monkeypatch.delenv(k, raising=False)
    tools.reset_verify_done()
    tools._DIAG_SEEN.clear()
    yield
    tools.reset_verify_done()
    tools._DIAG_SEEN.clear()


# ---------------------------------------------------------------------------
# cache-key safety
# ---------------------------------------------------------------------------

def _run_eval_mod():
    sys.modules.pop("run_eval", None)
    return importlib.import_module("run_eval")


def test_agent_switches_unset_is_none():
    re_ = _run_eval_mod()
    comp, meta = re_.agent_switches({})
    assert comp is None
    assert meta == {"thinking": None, "seed": None, "verify_done": False,
                    "diag_edit_hint": False, "env_block": False, "wall_warn": False}


@pytest.mark.parametrize("env,want", [
    ({"OPENBEAST_AGENT_THINKING": "on"}, "ag-think-on"),
    ({"OPENBEAST_AGENT_THINKING": " OFF "}, "ag-think-off"),
    ({"OPENBEAST_AGENT_SEED": "3"}, "ag-seed3"),
    ({"OPENBEAST_AGENT_SEED": "0"}, "ag-seed0"),
    ({"OPENBEAST_AGENT_VERIFY_DONE": "1"}, "ag-vdone"),
    ({"OPENBEAST_DIAG_EDIT_HINT": "1"}, "ag-ehint"),
    ({"OPENBEAST_AGENT_ENV_BLOCK": "1"}, "ag-envb"),
    ({"OPENBEAST_AGENT_WALL_WARN": "1"}, "ag-wwarn"),
    ({"OPENBEAST_AGENT_ENV_BLOCK": "1", "OPENBEAST_AGENT_WALL_WARN": "1"},
     "ag-envb-wwarn"),
    ({"OPENBEAST_AGENT_ENV_BLOCK": "0", "OPENBEAST_AGENT_WALL_WARN": "yes"}, None),
    ({"OPENBEAST_AGENT_THINKING": "on", "OPENBEAST_AGENT_SEED": "2",
      "OPENBEAST_AGENT_VERIFY_DONE": "1", "OPENBEAST_DIAG_EDIT_HINT": "1"},
     "ag-think-on-seed2-vdone-ehint"),
    # What the runner would NOT send is not keyed either (same parse rules).
    ({"OPENBEAST_AGENT_THINKING": "maybe"}, None),
    ({"OPENBEAST_AGENT_SEED": "abc"}, None),
    ({"OPENBEAST_AGENT_VERIFY_DONE": "0"}, None),
])
def test_agent_switches_component(env, want):
    assert _run_eval_mod().agent_switches(env)[0] == want


def test_agent_switch_parse_matches_the_runner(monkeypatch):
    """The key reads the env with the runner's own rules: whatever reaches
    the request is keyed, and nothing else."""
    import runner
    re_ = _run_eval_mod()
    for think in ("on", "off", "ON", "", "yes"):
        for seed in ("1", "", "x", "-4"):
            monkeypatch.setenv("OPENBEAST_AGENT_THINKING", think)
            monkeypatch.setenv("OPENBEAST_AGENT_SEED", seed)
            _comp, meta = re_.agent_switches()
            sent = runner._thinking_kwargs()
            want_think = (None if not sent else
                          ("on" if sent["extra_body"]["chat_template_kwargs"]["enable_thinking"]
                           else "off"))
            assert meta["thinking"] == want_think, (think, seed)
            assert meta["seed"] == runner._seed_kwargs().get("seed"), (think, seed)


def test_cache_key_agent_component_omitted_when_unset():
    cache = importlib.import_module("cache")
    t = {"id": "01_x", "task": "t", "validation": "v"}
    legacy = cache.cache_key(t, "slug", max_iter=10)
    assert cache.cache_key(t, "slug", max_iter=10, agent=None) == legacy
    seeded = cache.cache_key(t, "slug", max_iter=10, agent="ag-seed2")
    assert seeded != legacy and ".ag-seed2." in seeded
    think = cache.cache_key(t, "slug", max_iter=10, agent="ag-think-on")
    assert think not in (legacy, seeded)


_UNITS = [{"id": "20_one", "name": "one", "difficulty": "easy", "task": "t",
           "validation": {"type": "bash", "script": "true"}, "max_iter": 1}]


def _world(td: Path):
    cache_dir = td / "cache"; cache_dir.mkdir()
    results_dir = td / "results"; results_dir.mkdir()
    tasks_dir = td / "tasks"; tasks_dir.mkdir()
    for t in _UNITS:
        (tasks_dir / f"{t['id']}.json").write_text(json.dumps(t))
    for mod in ("cache", "run_eval"):
        sys.modules.pop(mod, None)
    cache = importlib.import_module("cache")
    cache.CACHE_DIR = cache_dir
    cache.STRIKES_DIR = cache.CACHE_DIR / "env-strikes"
    cache._context_cache.clear()
    run_eval = importlib.import_module("run_eval")
    run_eval.RESULTS_DIR = str(results_dir)
    run_eval.TASKS_DIR = str(tasks_dir)
    calls = []

    def agent(task, *a, env_overlay=None, **kw):
        calls.append({k: (env_overlay or {}).get(k, "<absent>") for k in AGENT_ENV})
        return {"exit_code": 0, "elapsed_seconds": 0.1, "stdout": "", "stderr": "",
                "tokens": {"prompt": 1, "completion": 3, "total": 4}}
    run_eval.run_agent = agent
    return cache, run_eval, calls, cache_dir


def test_run_eval_keys_stamps_and_pins_the_seed(td, monkeypatch):
    """The trap: an arm run without --no-cache replayed the default arm's
    rows. A seeded run must miss the unseeded rows, bank under its own key,
    say so in the header and on the row, and hand the agent the same value."""
    cache, run_eval, calls, cache_dir = _world(td)
    plain = run_eval.run_eval(model_name="m", use_cache=True)
    assert len(calls) == 1
    assert plain["harness"]["agent_switches"]["seed"] is None
    assert "agent_component" not in plain["harness"]
    assert plain["tasks"][0]["agent_seed"] is None
    assert calls[0]["OPENBEAST_AGENT_SEED"] is None          # pinned: removed
    legacy_keys = {p.stem for p in cache_dir.glob("*.json")}
    assert not any(".ag-" in k for k in legacy_keys)

    monkeypatch.setenv("OPENBEAST_AGENT_SEED", "2")
    seeded = run_eval.run_eval(model_name="m", use_cache=True)
    assert len(calls) == 2, "a seeded run replayed the unseeded row"
    assert calls[1]["OPENBEAST_AGENT_SEED"] == "2"
    assert seeded["harness"]["agent_switches"]["seed"] == 2
    assert seeded["harness"]["agent_component"] == "ag-seed2"
    assert seeded["tasks"][0]["agent_seed"] == 2
    keys = {p.stem for p in cache_dir.glob("*.json")} - legacy_keys
    assert len(keys) == 1 and ".ag-seed2." in next(iter(keys))

    # Same seed again replays; unset again replays the LEGACY row.
    run_eval.run_eval(model_name="m", use_cache=True)
    monkeypatch.delenv("OPENBEAST_AGENT_SEED")
    again = run_eval.run_eval(model_name="m", use_cache=True)
    assert len(calls) == 2
    assert again["summary"]["cache_hits"] == 1


def test_run_eval_thinking_is_its_own_era(td, monkeypatch):
    cache, run_eval, calls, cache_dir = _world(td)
    run_eval.run_eval(model_name="m", use_cache=True)
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", "on")
    out = run_eval.run_eval(model_name="m", use_cache=True)
    assert len(calls) == 2 and calls[1]["OPENBEAST_AGENT_THINKING"] == "on"
    assert out["tasks"][0]["agent_thinking"] == "on"
    assert any(".ag-think-on." in p.stem for p in cache_dir.glob("*.json"))


def test_env_block_and_wall_warn_are_keyed_stamped_and_pinned(td, monkeypatch):
    """G19/G20: arm E (both on) must not replay or bank into default rows."""
    cache, run_eval, calls, cache_dir = _world(td)
    plain = run_eval.run_eval(model_name="m", use_cache=True)
    assert calls[0]["OPENBEAST_AGENT_ENV_BLOCK"] is None      # pinned: removed
    assert calls[0]["OPENBEAST_AGENT_WALL_WARN"] is None
    assert "agent_env_block" not in plain["tasks"][0]
    assert "agent_wall_warn" not in plain["tasks"][0]
    monkeypatch.setenv("OPENBEAST_AGENT_ENV_BLOCK", "1")
    monkeypatch.setenv("OPENBEAST_AGENT_WALL_WARN", "1")
    arm = run_eval.run_eval(model_name="m", use_cache=True)
    assert len(calls) == 2, "arm E replayed a default row"
    assert calls[1]["OPENBEAST_AGENT_ENV_BLOCK"] == "1"
    assert calls[1]["OPENBEAST_AGENT_WALL_WARN"] == "1"
    assert arm["harness"]["agent_component"] == "ag-envb-wwarn"
    assert arm["harness"]["agent_switches"]["env_block"] is True
    assert arm["harness"]["agent_switches"]["wall_warn"] is True
    assert arm["tasks"][0]["agent_env_block"] is True
    assert arm["tasks"][0]["agent_wall_warn"] is True
    assert any(".ag-envb-wwarn." in p.stem for p in cache_dir.glob("*.json"))


def test_cli_seed_and_thinking_flags_set_the_env(monkeypatch):
    run_eval = _run_eval_mod()
    seen = {}

    def fake_run_eval(**kw):
        import os
        seen.update({k: os.environ.get(k) for k in AGENT_ENV})
        return {"summary": {"failed": 0}}
    # main() writes os.environ directly: register both so teardown restores
    # them (a leaked seed reached later runner tests' requests).
    monkeypatch.setenv("OPENBEAST_AGENT_SEED", "")
    monkeypatch.setenv("OPENBEAST_AGENT_THINKING", "")
    monkeypatch.setattr(run_eval, "run_eval", fake_run_eval)
    monkeypatch.setattr(run_eval, "_install_signal_reaper", lambda: None)
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--tasks", "x", "--seed", "3",
                                      "--thinking", "on"])
    run_eval.main()
    assert seen["OPENBEAST_AGENT_SEED"] == "3"
    assert seen["OPENBEAST_AGENT_THINKING"] == "on"
    assert run_eval.agent_switches()[0] == "ag-think-on-seed3"


# ---------------------------------------------------------------------------
# G9 — test before done
# ---------------------------------------------------------------------------

def _dest(tmp_path, monkeypatch, name="sol.py"):
    d = tmp_path / "eval_x" / name
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(d)]))
    return d


def test_verify_done_off_is_unchanged(tmp_path, monkeypatch):
    d = _dest(tmp_path, monkeypatch)
    tools.write_file(str(d), "print(1)\n")
    assert tools.task_done("ok") == "TASK_DONE: ok"


def test_verify_done_nudges_once_when_dest_never_run(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_VERIFY_DONE", "1")
    d = _dest(tmp_path, monkeypatch)
    tools.write_file(str(d), "print(1)\n")
    first = tools.task_done("ok")
    assert first.startswith(f"Before finishing: re-run your test against {d} itself")
    assert not first.startswith("TASK_DONE")           # the runner keeps going
    assert tools.task_done("ok") == "TASK_DONE: ok"    # once per run, non-blocking


def test_verify_done_accepts_after_a_run_of_the_dest(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_VERIFY_DONE", "1")
    d = _dest(tmp_path, monkeypatch)
    tools.write_file(str(d), "print(1)\n")
    tools.bash(f"true {d}")
    assert tools.task_done("ok") == "TASK_DONE: ok"


def test_verify_done_scratch_fix_after_the_last_run_nudges(tmp_path, monkeypatch):
    """The 36_c / 36_d / 143 shape: the dest was tested, then the real fix
    was made in a scratch file and never written back."""
    monkeypatch.setenv("OPENBEAST_AGENT_VERIFY_DONE", "1")
    d = _dest(tmp_path, monkeypatch)
    tools.write_file(str(d), "print(1)\n")
    tools.bash(f"true {d}")
    tools.write_file(str(tmp_path / "scratch_try.py"), "print(2)\n")
    assert tools.task_done("ok").startswith("Before finishing:")


def test_verify_done_stem_counts_as_a_run(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_VERIFY_DONE", "1")
    d = _dest(tmp_path, monkeypatch, "solver.zig")
    tools.write_file(str(d), "pub fn main() void {}\n")
    tools.bash("true ./solver")
    assert tools.task_done("ok") == "TASK_DONE: ok"


def test_verify_done_ignores_data_files_and_files_it_never_wrote(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_VERIFY_DONE", "1")
    out = tmp_path / "eval_x" / "answer.txt"
    fixture = tmp_path / "eval_x" / "given.py"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("x = 1\n")
    monkeypatch.setenv("OPENBEAST_TASK_PATHS", json.dumps([str(out), str(fixture)]))
    tools.write_file(str(out), "42\n")
    assert tools.task_done("ok") == "TASK_DONE: ok"


def test_verify_done_builds_on_the_missing_file_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_VERIFY_DONE", "1")
    d = _dest(tmp_path, monkeypatch)
    assert tools.task_done("ok").startswith("NOT DONE")
    tools.write_file(str(d), "print(1)\n")
    assert tools.task_done("ok").startswith("Before finishing:")
    assert tools.task_done("ok").startswith("TASK_DONE")


def test_verify_done_state_is_per_run(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_AGENT_VERIFY_DONE", "1")
    d = _dest(tmp_path, monkeypatch)
    tools.write_file(str(d), "print(1)\n")
    assert tools.task_done("ok").startswith("Before finishing:")
    tools.reset_verify_done()
    tools.write_file(str(d), "print(2)\n")
    assert tools.task_done("ok").startswith("Before finishing:")


def test_runner_resets_the_verify_ledger_each_run():
    src = (ROOT / "agents" / "runner.py").read_text()
    assert "_tools.reset_verify_done()" in src


# ---------------------------------------------------------------------------
# G13 — edit, don't rewrite
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lang,out,want", [
    ("zig", "/t/a.zig:7:5: error: use of undeclared identifier 'x'\n", 7),
    ("zig", "/t/a.zig:2:1: note: x\n/t/a.zig:9:3: error: bad\n", 9),
    ("c", "a.c:4:12: error: 'x' undeclared\n", 4),
    ("c++", "a.cpp:11:1: fatal error: nope.h: No such file\n", 11),
    ("go", "vet: ./a.go:5:2: undefined: y\n", 5),
    ("rust", "warning: unused\n --> a.rs:1:1\nerror[E0425]: cannot find value `x`\n"
             " --> a.rs:6:13\n", 6),
    ("python", "Sorry: IndentationError: unexpected indent (a.py, line 3)", 3),
    ("shell", "\nIn /t/a.sh line 8:\ncd /x\n^--^ SC2164 (warning): Use cd || exit\n", 8),
    ("zig", "something unparseable\n", None),
])
def test_first_error_line(lang, out, want):
    assert tools._diag_first_error_line(lang, out) == want


def test_edit_hint_off_leaves_the_block_byte_identical():
    out = "/t/a.zig:7:5: error: nope\n"
    base = tools._diag_format("zig", 1, out, "")
    assert tools._diag_format("zig", 1, out, "", edit_hint=False) == base
    hinted = tools._diag_format("zig", 1, out, "", edit_hint=True)
    assert hinted == base + "\nFix the first error with edit_file at line 7; don't rewrite the file"


def test_edit_hint_never_on_a_clean_check():
    assert tools._diag_format("zig", 0, "", "", edit_hint=True) == "\ndiagnostics: OK (zig)"


def test_edit_hint_without_a_line_number():
    s = tools._diag_format("zig", 1, "garbage\n", "", edit_hint=True)
    assert s.endswith("\nFix the first error with edit_file; don't rewrite the file")


def test_edit_hint_end_to_end_on_a_write(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_DIAGNOSTICS", "1")
    monkeypatch.setenv("OPENBEAST_DIAG_EDIT_HINT", "1")
    out = tools.write_file(str(tmp_path / "bad.py"), "x = 1\ndef f(:\n")
    assert out.rstrip().endswith("Fix the first error with edit_file at line 2; "
                                 "don't rewrite the file"), out
    monkeypatch.delenv("OPENBEAST_DIAG_EDIT_HINT")
    assert "Fix the first error" not in tools.write_file(str(tmp_path / "bad2.py"),
                                                         "def f(:\n")


# ---------------------------------------------------------------------------
# G15 — edit_file shows only new lint findings
# ---------------------------------------------------------------------------

_SC_BEFORE = """
In /t/a.sh line 3:
cd /tmp
^-----^ SC2164 (warning): Use 'cd ... || exit' or 'cd ... || return' in case cd fails.

Did you mean:
cd /tmp || exit


In /t/a.sh line 4:
read x
     ^-- SC2034 (warning): x appears unused.

For more information:
  https://www.shellcheck.net/wiki/SC2164 -- Use 'cd ... || exit'
  https://www.shellcheck.net/wiki/SC2034 -- x appears unused.
"""

# The same two findings, shifted down a line, plus one new one.
_SC_AFTER = """
In /t/a.sh line 4:
cd /tmp
^-----^ SC2164 (warning): Use 'cd ... || exit' or 'cd ... || return' in case cd fails.

Did you mean:
cd /tmp || exit


In /t/a.sh line 5:
read x
     ^-- SC2034 (warning): x appears unused.


In /t/a.sh line 6:
echo $a
     ^-- SC2154 (warning): a is referenced but not assigned.

For more information:
  https://www.shellcheck.net/wiki/SC2164 -- Use 'cd ... || exit'
  https://www.shellcheck.net/wiki/SC2034 -- x appears unused.
  https://www.shellcheck.net/wiki/SC2154 -- a is referenced but not assigned.
"""


def test_shellcheck_new_keeps_only_new_findings_across_a_line_shift():
    out, dropped = tools._shellcheck_new(_SC_BEFORE, _SC_AFTER)
    assert dropped == 2
    assert "SC2154" in out and "line 6" in out
    assert "SC2164" not in out and "SC2034" not in out
    assert "wiki/SC2154" in out and "wiki/SC2164" not in out


def test_shellcheck_new_with_nothing_new_is_empty():
    out, dropped = tools._shellcheck_new(_SC_BEFORE, _SC_BEFORE)
    assert out == "" and dropped == 2


def test_shellcheck_new_counts_duplicates():
    body, trailer = _SC_BEFORE.split("For more information:")
    twice = body + body + "For more information:" + trailer
    out, dropped = tools._shellcheck_new(_SC_BEFORE, twice)
    assert dropped == 2
    assert out.count("^-----^ SC2164") == 1 and out.count("SC2034") == 2  # finding + link


def test_compiled_languages_are_never_filtered(tmp_path, monkeypatch):
    """A compile error that survives an edit still means the file does not
    build: only lint findings are diffed."""
    monkeypatch.setenv("OPENBEAST_DIAGNOSTICS", "1")
    p = tmp_path / "a.py"
    p.write_text("x = 1\ndef f(:\n")
    assert tools._diag_baseline(str(p), p.read_text()) is None


def test_baseline_is_none_with_diagnostics_off(tmp_path):
    p = tmp_path / "a.sh"
    p.write_text("cd /tmp\n")
    assert tools._diag_baseline(str(p), p.read_text()) is None


sck = pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")


@sck
def test_edit_file_drops_preexisting_shellcheck_warnings(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_DIAGNOSTICS", "1")
    p = tmp_path / "a.sh"
    w = tools.write_file(str(p), "#!/bin/sh\ncd /tmp\nread x\necho hi\n")
    assert "SC2164" in w and "SC2034" in w            # write_file shows them all
    e = tools.edit_file(str(p), "echo hi", "echo hello")
    assert "SC2164" not in e and "SC2034" not in e
    assert "diagnostics: no new findings (shell; 2 pre-existing not repeated)" in e
    e2 = tools.edit_file(str(p), "echo hello", "echo hello\ncd /var")
    assert "line 5" in e2 and "SC2164" in e2 and "line 2:" not in e2


@sck
def test_edit_file_baseline_when_file_changed_outside_the_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_DIAGNOSTICS", "1")
    p = tmp_path / "a.sh"
    p.write_text("#!/bin/sh\ncd /tmp\necho hi\n")          # never checked: memo miss
    e = tools.edit_file(str(p), "echo hi", "echo hello")
    assert "SC2164" not in e and "no new findings" in e


@sck
def test_edit_file_without_diagnostics_is_unchanged(tmp_path):
    p = tmp_path / "a.sh"
    p.write_text("#!/bin/sh\ncd /tmp\necho hi\n")
    e = tools.edit_file(str(p), "echo hi", "echo hello")
    assert "diagnostics" not in e


# ---------------------------------------------------------------------------
# G16 — escalation-card eligibility
# ---------------------------------------------------------------------------

def test_card_eligible_langs_from_the_gate():
    re_ = _run_eval_mod()
    gate = {"cpp": {"served": False}, "zig": {"served": True},
            "python": {"served": True}, "go": {"served": True}}
    # python is served but a card can never be attached to its checker.
    assert re_.card_eligible_langs(gate) == ["go", "zig"]
    assert re_.card_eligible_langs(None) == []


def test_arm_ctx_counts_card_eligible_units(monkeypatch, tmp_path):
    re_ = _run_eval_mod()
    monkeypatch.setattr(re_, "diagnostics_flag", lambda env=None: (True, "diag2-x", {}))
    monkeypatch.setattr(re_, "escalate_flag", lambda on, env=None: (
        True, "esc1-y", {"gate": {"gate": {"zig": {"served": True},
                                           "cpp": {"served": False}}}}))
    monkeypatch.setattr(re_, "packs_flag", lambda env=None: (False, None, {}))
    c = re_.ArmCtx("L2", {"OPENBEAST_DIAG_LANGS": "all"}, str(tmp_path), "s")
    assert c.card_langs == ["zig"]
    units = [{"id": "a", "language": "zig"}, {"id": "b", "language": "cpp"},
             {"id": "c", "language": "python"}]
    assert [c.card_eligible(t) for t in units] == [True, False, False]


def test_ablation_exposure_line_names_card_eligible_units():
    import ablation as A
    rows = [{"id": f"u{i}_f", "passed": False, "language": "zig", "agent_exit_code": 0,
             "tokens_completion": 10} for i in range(2)]
    rows += [{"id": "v_c", "passed": True, "language": "c", "agent_exit_code": 0,
              "tokens_completion": 10},
             {"id": "w_f", "passed": False, "language": "zig", "reason": "not_in_arm"}]
    b = {"model_slug": "m", "tasks": rows,
         "harness": {"arm": "L2", "diag_scope": ["zig", "c"],
                     "escalation_eligible_langs": ["zig"],
                     "diag_exposure": {"units_exposed": 2, "units_hint_fired": 1,
                                       "units_card_fired": 0}}}
    ce = A.card_eligibility(b)
    assert ce == {"langs": ["zig"], "units_eligible": 2, "units": 3}
    a = {"model_slug": "m", "tasks": [dict(r, passed=False) for r in rows],
         "harness": {"arm": "L0"}}
    text = A.render(A.report(a, b))
    assert "card-eligible units: 2/3 (zig)" in text
    assert A.card_eligibility(a) is None
