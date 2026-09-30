#!/usr/bin/env python3
"""The Tier-3 zig awareness pack in PRODUCTION (agents/lang/pack_context.py).

What was measured (scratch/tier3-verdict-fresh-20260930.txt): the committed
agents/packs/zig-0.16.md handed to agents/runner.py as --context-file on zig
tasks. These tests pin that production launchers now do exactly that — and
nothing else: never under eval, never over an explicit --context-file, never
a stale or edited pack, never on a non-zig task.

Hermetic: the switch, LANG_PACKS, the eval markers, the installed zig version
and the combined-file dir are all set per test; no real agent is spawned
(Popen is stubbed, or the "runner" is a stub script that prints its argv).

Run: python3 -m pytest tests/test_pack_context.py -q
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
TESTS = os.path.dirname(os.path.abspath(__file__))
for _p in (AGENTS, TESTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lang import pack_context as PC  # noqa: E402
from lang import packs as P  # noqa: E402

PACK = os.path.join(AGENTS, "packs", "zig-0.16.md")
PACK_SHA8 = hashlib.sha256(open(PACK, "rb").read()).hexdigest()[:8]
LABEL = f"zig@{PACK_SHA8}"


@pytest.fixture(autouse=True)
def hermetic(tmp_path, monkeypatch):
    for k in ("OPENBEAST_EVAL", "OPENBEAST_TASK_PATHS", "OPENBEAST_LANG_IN_EVAL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENBEAST_LANG_PACK_CONTEXT", "auto")
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "auto")
    monkeypatch.setenv("OPENBEAST_PACK_CONTEXT_DIR", str(tmp_path / "pack-ctx"))
    # The pack says zig 0.16.0; the drift check compares against the
    # installed compiler. Pin it rather than depend on this box's zig.
    monkeypatch.setattr(PC, "_installed_zig", lambda ttl=60.0: "0.16.0")
    PC._LOGGED.clear()
    yield


def _ctx(task, workdir=None, context=""):
    return PC.production_context(task, workdir, context)


# --- detection: task text -----------------------------------------------------

@pytest.mark.parametrize("task", [
    "Write a zig program that prints the first 10 primes",
    "port this module to Zig",
    "fix the ZIG build",
    "the build.zig fails to link",
    "edit src/main.zig so the tests pass",
    "zig: add a tokenizer",
])
def test_zig_task_text_gets_the_committed_pack(task, tmp_path):
    path, info = _ctx(task, str(tmp_path))
    assert path == PACK                     # the file itself — the measured delivery
    assert info["label"] == LABEL and info["packs"] == {"zig": PACK_SHA8}


@pytest.mark.parametrize("task", [
    "implement a zigzag traversal of a binary tree",
    "zigbee sensor bridge in python",
    "write a C function that reverses a string",
    "summarise the README",
    "",
])
def test_non_zig_task_text_gets_nothing(task, tmp_path):
    path, info = _ctx(task, str(tmp_path))
    assert path is None and info["label"] == ""


# --- detection: workdir -------------------------------------------------------

def test_workdir_with_build_zig_is_a_zig_task(tmp_path):
    (tmp_path / "build.zig").write_text("// build\n")
    assert _ctx("make the tests pass", str(tmp_path))[0] == PACK


def test_workdir_with_nested_zig_source_is_a_zig_task(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.zig").write_text("pub fn main() void {}\n")
    assert _ctx("make the tests pass", str(tmp_path))[0] == PACK


def test_workdir_scan_is_shallow(tmp_path):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "x.zig").write_text("")
    assert _ctx("make the tests pass", str(tmp_path))[0] is None


def test_workdir_scan_skips_dependency_and_cache_dirs(tmp_path):
    for d in ("node_modules", ".zig-cache", "zig-cache", ".git"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "dep.zig").write_text("")
    assert _ctx("make the tests pass", str(tmp_path))[0] is None


def test_workdir_without_markers_gets_nothing(tmp_path):
    (tmp_path / "main.py").write_text("print(1)\n")
    assert _ctx("make the tests pass", str(tmp_path))[0] is None


def test_task_naming_another_language_overrides_a_stray_zig_file(tmp_path):
    (tmp_path / "tool.zig").write_text("")
    assert _ctx("fix the python tests", str(tmp_path))[0] is None


def test_huge_workdir_scan_is_bounded(tmp_path, monkeypatch):
    for i in range(40):
        d = tmp_path / f"d{i:02}"
        d.mkdir()
        for j in range(40):
            (d / f"f{j}.txt").write_text("")
    seen = {"n": 0}
    real = os.scandir

    class Counting:
        def __init__(self, p):
            self._it = real(p)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._it.close()

        def __iter__(self):
            for e in self._it:
                seen["n"] += 1
                yield e

    monkeypatch.setattr(PC.os, "scandir", Counting)
    assert PC.workdir_langs(str(tmp_path)) == []
    assert seen["n"] <= PC.SCAN_MAX_ENTRIES + 1, seen    # not 1640


def test_unreadable_or_missing_workdir_is_not_an_error(tmp_path):
    assert _ctx("make the tests pass", str(tmp_path / "nope"))[0] is None
    assert _ctx("make the tests pass", None)[0] is None


# --- the switch ---------------------------------------------------------------

@pytest.mark.parametrize("raw", ["off", "OFF", "false", "0", "no", "off  # not yet"])
def test_off_switch_env(raw, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENBEAST_LANG_PACK_CONTEXT", raw)
    path, info = _ctx("write zig", str(tmp_path))
    assert path is None and info["setting"] == "off"


def test_typo_is_off_not_on(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENBEAST_LANG_PACK_CONTEXT", "atuo")
    assert _ctx("write zig", str(tmp_path))[0] is None
    assert "not auto|off" in capsys.readouterr().err


def test_off_switch_from_openbeast_conf(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENBEAST_LANG_PACK_CONTEXT")
    (tmp_path / "openbeast.conf").write_text('LANG_PACK_CONTEXT="off"   # held\n')
    monkeypatch.setattr(P, "_REPO", str(tmp_path))
    assert PC.setting()[0] == "off"
    assert _ctx("write zig", str(tmp_path))[0] is None
    (tmp_path / "openbeast.conf").write_text("# nothing\n")
    assert PC.setting()[0] == "auto"                        # absent = default


def test_lang_packs_without_zig_disables_it(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "cpp,go")
    assert _ctx("write zig", str(tmp_path))[0] is None
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "off")
    assert _ctx("write zig", str(tmp_path))[0] is None
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "cpp,zig")
    assert _ctx("write zig", str(tmp_path))[0] == PACK


# --- never under eval -----------------------------------------------------------

@pytest.mark.parametrize("marker", ["OPENBEAST_EVAL", "OPENBEAST_TASK_PATHS"])
def test_eval_marker_suppresses_injection(marker, monkeypatch, tmp_path):
    monkeypatch.setenv(marker, "1")
    monkeypatch.setenv("OPENBEAST_LANG_IN_EVAL", "1")      # does NOT re-enable it
    path, info = _ctx("write zig", str(tmp_path))
    assert path is None and "under eval" in info["reason"]


# --- the pack must be the measured, undrifted file ------------------------------

def _packs_dir_with(tmp_path, text):
    d = tmp_path / "packs"
    d.mkdir()
    (d / "zig-0.16.md").write_text(text)
    return str(d)


def test_drifted_generated_section_injects_nothing(monkeypatch, tmp_path, capsys):
    text = open(PACK).read().rstrip("\n") + "\n- tampered line\n"
    monkeypatch.setattr(PC, "PACKS_DIR", _packs_dir_with(tmp_path, text))
    path, info = _ctx("write zig", str(tmp_path))
    assert path is None and "drifted" in info["reason"]
    _ctx("write zig again", str(tmp_path))
    assert capsys.readouterr().err.count("not injecting") == 1     # logged once


def test_edited_curated_section_is_an_unmeasured_treatment(monkeypatch, tmp_path):
    # Curated section edits keep the generated digest valid — the harness
    # check passes — but it is no longer the pack that was measured.
    text = open(PACK).read().replace("(1) CURATED", "(1) CURATED (edited)", 1)
    monkeypatch.setattr(PC, "PACKS_DIR", _packs_dir_with(tmp_path, text))
    assert PC.pack_problem("zig", "x", text.encode()) is None
    path, info = _ctx("write zig", str(tmp_path))
    assert path is None and "measured pack" in info["reason"]


def test_other_installed_zig_injects_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(PC, "_installed_zig", lambda ttl=60.0: "0.17.0")
    path, info = _ctx("write zig", str(tmp_path))
    assert path is None and "0.17.0" in info["reason"]


def test_run_eval_uses_the_same_check():
    """One drift check, not two: the harness delegates to pack_context."""
    src = open(os.path.join(REPO, "evals", "run_eval.py")).read()
    assert "_pack_context.pack_problem(lang, path, data)" in src
    assert "PACK_FILES = _pack_context.PACK_FILES" in src


# --- client-mode safety -----------------------------------------------------------

def test_client_checkout_without_packs_is_silent(monkeypatch, tmp_path):
    monkeypatch.setattr(PC, "PACKS_DIR", str(tmp_path / "no-packs-here"))
    path, info = _ctx("write zig", str(tmp_path))
    assert path is None and "not present" in info["reason"]
    assert "NOT SERVED" in PC.status_line()


def test_internal_error_is_no_pack_not_a_crash(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(PC, "detect", boom)
    assert _ctx("write zig", str(tmp_path)) == (None, {
        "setting": "auto", "langs": [], "packs": {}, "label": "",
        "reason": "error: RuntimeError('x')"})


# --- caller context --------------------------------------------------------------

def test_caller_context_is_folded_into_one_private_file(tmp_path):
    path, info = _ctx("write zig", str(tmp_path), context="SKILL BODY")
    assert path != PACK and info["label"] == LABEL
    body = open(path).read()
    assert body.startswith("SKILL BODY\n\n") and open(PACK).read().rstrip("\n") in body
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    # content-addressed: a dry run and the real start plan the same argv
    assert _ctx("write zig", str(tmp_path), context="SKILL BODY")[0] == path


def test_context_args_without_pack_keep_the_callers_context(tmp_path):
    assert PC.context_args("summarise", str(tmp_path), "ctx")[0] == ["--context", "ctx"]
    assert PC.context_args("summarise", str(tmp_path), "")[0] == []
    assert PC.context_args("write zig", str(tmp_path), "")[0] == ["--context-file", PACK]


# --- argv rewrite (agent.sh / client.sh) -------------------------------------------

def test_rewrite_prepends_context_file(tmp_path):
    out, info = PC.rewrite_runner_argv(["-w", str(tmp_path), "write a zig lexer"])
    assert out == ["--context-file", PACK, "-w", str(tmp_path), "write a zig lexer"]


def test_rewrite_folds_inline_context(tmp_path):
    out, _ = PC.rewrite_runner_argv(["--context=hello", "--max-iter", "3", "zig please"])
    assert out[0] == "--context-file" and out[2:] == ["--max-iter", "3", "zig please"]
    assert "--context=hello" not in out and open(out[1]).read().startswith("hello\n\n")


def test_rewrite_respects_an_explicit_context_file(tmp_path):
    argv = ["--context-file", "/mine.md", "write zig"]
    out, info = PC.rewrite_runner_argv(argv)
    assert out == argv and "--context-file" in info["reason"]


def test_rewrite_reads_the_task_file_and_honours_double_dash(tmp_path):
    tf = tmp_path / "task.md"
    tf.write_text("Port the parser to zig 0.16\n")
    out, _ = PC.rewrite_runner_argv(["-f", str(tf)])
    assert out[:2] == ["--context-file", PACK]
    # after `--` everything is task text, even a --context-file lookalike
    out, _ = PC.rewrite_runner_argv(["--", "--context-file x", "in zig"])
    assert out == ["--context-file", PACK, "--", "--context-file x", "in zig"]


def test_rewrite_leaves_non_zig_argv_byte_identical(tmp_path):
    argv = ["--context", "c", "-w", str(tmp_path), "tidy the docs"]
    assert PC.rewrite_runner_argv(argv)[0] == argv


# --- launchers -------------------------------------------------------------------

class _FakeProc:
    pid = 424242
    returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


@pytest.fixture()
def mcp(monkeypatch, tmp_path):
    import mcp_server
    calls = []

    def fake_popen(cmd, **kw):
        calls.append(cmd)
        return _FakeProc()
    monkeypatch.setattr(mcp_server.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(mcp_server, "_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(mcp_server._tools, "harden_process", lambda: None)
    before = set(mcp_server._agents)
    yield mcp_server, calls
    for k in set(mcp_server._agents) - before:
        mcp_server._agents.pop(k, None)


def _spawn_event(out, mcp_server):
    log = [ln for ln in out.splitlines() if ln.startswith("Log: ")][0][5:]
    return json.loads(open(log).readline())


def test_mcp_start_agent_passes_the_pack(mcp, tmp_path):
    mcp_server, calls = mcp
    out = mcp_server.start_agent("Write a zig program that sums a file", workdir=str(tmp_path))
    cmd = calls[-1]
    assert cmd[cmd.index("--context-file") + 1] == PACK
    assert "--context" not in cmd and cmd[-1] == "Write a zig program that sums a file"
    assert f"Language pack: {LABEL}" in out
    assert _spawn_event(out, mcp_server)["pack"] == LABEL


def test_mcp_start_agent_non_zig_is_unchanged(mcp, tmp_path):
    mcp_server, calls = mcp
    out = mcp_server.start_agent("tidy the README", workdir=str(tmp_path), context="bg")
    cmd = calls[-1]
    assert "--context-file" not in cmd and cmd[cmd.index("--context") + 1] == "bg"
    assert "Language pack" not in out and "pack" not in _spawn_event(out, mcp_server)


def test_mcp_start_agent_under_eval_is_unchanged(mcp, tmp_path, monkeypatch):
    mcp_server, calls = mcp
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    mcp_server.start_agent("write zig", workdir=str(tmp_path))
    assert "--context-file" not in calls[-1]


def test_mcp_start_skill_agent_folds_skill_and_pack(mcp, tmp_path, monkeypatch):
    mcp_server, calls = mcp
    monkeypatch.setattr(mcp_server, "_resolve_skill",
                        lambda name: {"source": "test", "body": "SKILL-BODY-XYZ"})
    mcp_server.start_skill_agent("code-review", "review the zig allocator", workdir=str(tmp_path))
    cmd = calls[-1]
    assert "--context" not in cmd
    body = open(cmd[cmd.index("--context-file") + 1]).read()
    assert "SKILL-BODY-XYZ" in body and open(PACK).read().rstrip("\n") in body


def test_mcp_start_agent_survives_a_missing_lang_package(mcp, tmp_path, monkeypatch):
    """Client laptops: an agents/ tree without lang/ must still spawn."""
    mcp_server, calls = mcp
    import lang
    monkeypatch.setitem(sys.modules, "lang.pack_context", None)   # import fails
    monkeypatch.delattr(lang, "pack_context", raising=False)
    out = mcp_server.start_agent("write zig", workdir=str(tmp_path))
    assert "Agent started" in out and "--context-file" not in calls[-1]


# chat_server (console / API agents)

@pytest.fixture()
def chat_rig(tmp_path, monkeypatch):
    import chat_server
    from test_chat_server import Rig
    monkeypatch.setattr(chat_server, "_SCOPE_PREFIX", [])
    return chat_server, Rig(tmp_path, monkeypatch)


def test_chat_agent_plan_carries_the_pack(chat_rig, tmp_path):
    chat_server, rig = chat_rig
    wd = tmp_path / "proj"
    wd.mkdir()
    (wd / "build.zig").write_text("")
    d = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "agent", "task": "make the tests pass", "workdir": str(wd),
        "dry_run": True}).json()
    argv = d["argv"]
    assert argv[argv.index("--context-file") + 1] == PACK
    assert argv[-2:] == ["--", "make the tests pass"] and d["pack"] == LABEL
    plain = tmp_path / "plain"
    plain.mkdir()
    d2 = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "agent", "task": "make the tests pass", "workdir": str(plain),
        "dry_run": True}).json()
    assert "--context-file" not in d2["argv"] and d2["pack"] is None


def test_chat_agent_start_records_pack_provenance(chat_rig, tmp_path, monkeypatch):
    chat_server, rig = chat_rig
    stub = tmp_path / "stub_runner.py"
    stub.write_text("import time; time.sleep(0.3)\n")
    monkeypatch.setattr(chat_server, "RUNNER_PATH", str(stub))
    r = rig.client.post("/api/chat/sessions", headers=rig.local, json={
        "kind": "agent", "task": "write a zig allocator", "workdir": str(tmp_path),
        "meta": {"pack": "forged@00000000"}})
    assert r.status_code == 201, r.text
    sid = r.json()["session"]["id"]
    import sessions
    deadline = time.time() + 10
    while time.time() < deadline:
        rec = sessions.get(sid) or {}
        if (rec.get("meta") or {}).get("pack"):
            break
        time.sleep(0.05)
    assert rec["meta"]["pack"] == LABEL                  # the caller cannot forge it


# agent.sh / client.sh — the real scripts, a stub runner that prints its argv

def _fake_tree(tmp_path):
    root = tmp_path / "repo"
    (root / "agents").mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy(os.path.join(REPO, "agent.sh"), root / "agent.sh")
    shutil.copy(os.path.join(REPO, "scripts", "client.sh"), root / "scripts" / "client.sh")
    shutil.copytree(os.path.join(REPO, "scripts", "lib"), root / "scripts" / "lib")
    for d in ("lang", "packs"):
        os.symlink(os.path.join(AGENTS, d), root / "agents" / d)
    (root / "agents" / "runner.py").write_text(
        "import json, sys; print(json.dumps(sys.argv[1:]))\n")
    return root


def _env(tmp_path, home=None, **extra):
    """A scrubbed env. HOME stays real for agent.sh (its deps check imports
    openai from the user site); client.sh gets a temp HOME for its venv."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("OPENBEAST_") and k not in ("BEAST_PACKS",)}
    env.update({"HOME": str(home or os.path.expanduser("~")),
                "OPENBEAST_LANG_PACK_CONTEXT": "auto",
                "OPENBEAST_LANG_PACKS": "auto",
                "OPENBEAST_PACK_CONTEXT_DIR": str(tmp_path / "pack-ctx")})
    env.update(extra)
    os.makedirs(env["HOME"], exist_ok=True)
    return env


def _zig_matches():
    """The scripts run a real python with this box's zig: skip the positive
    case where an installed zig would (correctly) refuse the 0.16 pack."""
    if not shutil.which("zig"):
        return True
    v = subprocess.run(["zig", "version"], capture_output=True, text=True).stdout.strip()
    return v == "0.16.0"


def test_agent_sh_passes_the_pack(tmp_path):
    if not _zig_matches():
        pytest.skip("installed zig is not 0.16.0")
    root = _fake_tree(tmp_path)
    run = subprocess.run(["bash", str(root / "agent.sh"), "-w", str(tmp_path),
                          "write a zig tokenizer"],
                         capture_output=True, text=True, env=_env(tmp_path), timeout=60)
    assert run.returncode == 0, run.stderr
    argv = json.loads(run.stdout.strip().splitlines()[-1])
    assert argv[:2] == ["--context-file", str(root / "agents" / "packs" / "zig-0.16.md")]
    assert argv[-1] == "write a zig tokenizer"
    assert f"awareness pack {LABEL}" in run.stderr


def test_agent_sh_off_and_eval_leave_argv_alone(tmp_path):
    root = _fake_tree(tmp_path)
    for extra in ({"OPENBEAST_LANG_PACK_CONTEXT": "off"}, {"OPENBEAST_EVAL": "1"}):
        run = subprocess.run(["bash", str(root / "agent.sh"), "write zig"],
                             capture_output=True, text=True, env=_env(tmp_path, **extra),
                             timeout=60)
        assert run.returncode == 0, run.stderr
        assert json.loads(run.stdout.strip().splitlines()[-1]) == ["write zig"]


def test_client_sh_agent_passes_the_pack(tmp_path):
    if not _zig_matches():
        pytest.skip("installed zig is not 0.16.0")
    root = _fake_tree(tmp_path)
    env = _env(tmp_path, home=tmp_path / "home",
               OPENBEAST_AGENT_INFERENCE_URL="http://rig.example:8443/v1")
    venv_bin = tmp_path / "home" / ".openbeast-client" / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    os.symlink(sys.executable, venv_bin / "python3")
    run = subprocess.run(["bash", str(root / "scripts" / "client.sh"), "agent",
                          "write a zig tokenizer"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert run.returncode == 0, run.stderr
    argv = json.loads(run.stdout.strip().splitlines()[-1])
    assert argv[:2] == ["--base-url", "http://rig.example:8443/v1"]
    assert argv[2:4] == ["--context-file", str(root / "agents" / "packs" / "zig-0.16.md")]


def test_client_sh_without_packs_still_runs(tmp_path):
    root = _fake_tree(tmp_path)
    os.unlink(root / "agents" / "packs")
    env = _env(tmp_path, home=tmp_path / "home",
               OPENBEAST_AGENT_INFERENCE_URL="http://rig.example:8443/v1")
    venv_bin = tmp_path / "home" / ".openbeast-client" / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    os.symlink(sys.executable, venv_bin / "python3")
    run = subprocess.run(["bash", str(root / "scripts" / "client.sh"), "agent", "write zig"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout.strip().splitlines()[-1]) == [
        "--base-url", "http://rig.example:8443/v1", "write zig"]


def test_doctor_row_reports_the_pack():
    src = open(os.path.join(REPO, "scripts", "doctor.sh")).read()
    assert "agents/lang/pack_context.py status" in src
    assert PC.status_line() == f"zig pack: auto (agents on zig tasks get agents/packs/zig-0.16.md @{PACK_SHA8})"
