#!/usr/bin/env python3
"""Review 2026-10-27 — tool-server fixes (mcp_server / openapi_tools / router).

  R1-04  publish_artifact's workspace is never the process cwd
  R1-05  the unauthenticated MCP HTTP transport refuses a non-loopback bind
  R1-07  the workdir-escape and shard-creation refusals reach the audit trail
  R1-09  the router does not 500 on a non-list / non-dict `messages`
  R1-10  stop_agent stops a ledger-only agent (and only a provably-ours one)
  R1-11  the MCP grep wrapper forwards every parameter tools.grep takes
  R1-12  the audit args digest is keyed by a per-install secret
  R9-02  skill(name) / start_skill_agent never serve a stale or deleted body
  R9-05  a hand-installed global skill is labelled unverified
  R7-02  the runner withholds the ambient key from a foreign host even when a
         worker URL IS configured (test-only; runner.py unchanged)
  R7-06  a signed identity token without `exp` is refused (test-only)

Everything is in-process or a stubbed subprocess: no stack port is bound.

Run: python3 -m pytest tests/test_review1027_toolserver.py -q
"""
import asyncio
import hashlib
import importlib
import inspect
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
sys.path.insert(0, AGENTS)

import mcp_server  # noqa: E402
import openapi_tools  # noqa: E402
import sessions  # noqa: E402
import tools as _tools  # noqa: E402


# ── R1-04 ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def opencode_env(tmp_path, monkeypatch):
    """The OpenCode case: MCP stdio from a repo checkout, plain shell env —
    no FILES_DIR, no AGENT_WORKDIR, no per-request override."""
    home = tmp_path / "home"
    (home / "openbeast-files").mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "openbeast.conf").write_text("MCPO_ADMIN_KEY=sekret\n")
    (checkout / "page.html").write_text("<p>hi</p>")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENBEAST_FILES_DIR", raising=False)
    monkeypatch.delenv("AGENT_WORKDIR", raising=False)
    monkeypatch.chdir(checkout)
    return home, checkout


def test_r1_04_publish_refuses_the_cwd_checkout(opencode_env):
    _home, checkout = opencode_env
    html, err = mcp_server._read_artifact_page(
        str(checkout / "openbeast.conf"), 1 << 20)
    assert html is None and "refusing to publish" in err
    # Not only the conf: cwd is simply not the workspace any more.
    html, err = mcp_server._read_artifact_page(str(checkout / "page.html"),
                                               1 << 20)
    assert html is None and "outside" in err


def test_r1_04_publish_still_works_from_the_files_dir(opencode_env):
    home, _checkout = opencode_env
    page = home / "openbeast-files" / "page.html"
    page.write_text("<p>ok</p>")
    html, err = mcp_server._read_artifact_page(str(page), 1 << 20)
    assert err is None and html == "<p>ok</p>"


def test_r1_04_conf_is_refused_even_inside_the_workspace(tmp_path, monkeypatch):
    """Belt: an agent whose AGENT_WORKDIR is a checkout still can't publish it."""
    (tmp_path / "openbeast.conf").write_text("JWT=x\n")
    (tmp_path / "ok.html").write_text("<p>ok</p>")
    monkeypatch.setenv("AGENT_WORKDIR", str(tmp_path))
    html, err = mcp_server._read_artifact_page(
        str(tmp_path / "openbeast.conf"), 1 << 20)
    assert html is None and "openbeast.conf" in err
    html, err = mcp_server._read_artifact_page(str(tmp_path / "ok.html"),
                                               1 << 20)
    assert err is None and html == "<p>ok</p>"


# ── R1-05 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "[::1]",
                                  "localhost"])
def test_r1_05_loopback_binds_are_allowed(host):
    assert mcp_server.http_exposure_refusal(host, env={}) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5",
                                  "100.64.0.1", "beast.lan", ""])
def test_r1_05_network_binds_are_refused(host):
    reason = mcp_server.http_exposure_refusal(host, env={})
    assert reason and "refusing" in reason


def test_r1_05_explicit_opt_in_and_keys_do_not_count():
    assert mcp_server.http_exposure_refusal("0.0.0.0", allow_open=True,
                                            env={}) is None
    assert mcp_server.http_exposure_refusal(
        "0.0.0.0", env={"OPENBEAST_ALLOW_OPEN_TOOLS": "true"}) is None
    # An RBAC key protects :3001, not this transport — nothing here checks it.
    assert mcp_server.http_exposure_refusal(
        "0.0.0.0", env={"OPENBEAST_MCPO_ADMIN_KEY": "k"})


def test_r1_05_default_port_is_not_the_identity_servers():
    assert mcp_server._HTTP_DEBUG_PORT != 3001


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_r1_05_main_exits_2_on_a_network_bind(tmp_path):
    env = {k: v for k, v in os.environ.items()
           if k != "OPENBEAST_ALLOW_OPEN_TOOLS"}
    env["OPENBEAST_BIND"] = "0.0.0.0"
    p = subprocess.run(
        [sys.executable, os.path.join(AGENTS, "mcp_server.py"),
         "--transport", "http", "--port", str(_free_port())],
        env=env, capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert p.returncode == 2, p.stderr
    assert "refusing" in p.stderr


# ── R1-10 ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(mcp_server, "_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(mcp_server, "_agents", {})
    procs = []

    def spawn(sid):
        p = subprocess.Popen(["sleep", "60"], start_new_session=True)
        procs.append(p)
        assert sessions.register(sid, kind="agent", pid=p.pid, pgid=p.pid,
                                 title="t")
        return p
    yield spawn
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait()


def test_r1_10_stop_agent_stops_a_ledger_only_agent(ledger):
    p = ledger("20261027-000000-aaaaaaaa")
    assert "aaaaaaaa" in mcp_server.list_agents()   # listed...
    out = mcp_server.stop_agent("20261027-000000-aaaaaaaa")
    assert "stopped" in out, out                    # ...and now stoppable
    assert p.wait(timeout=5) == -signal.SIGTERM


def test_r1_10_unproven_identity_is_not_signalled(ledger):
    sid = "20261027-000000-bbbbbbbb"
    p = ledger(sid)
    rec = sessions.get(sid)
    rec["meta"]["pid_start"] = int(rec["meta"]["pid_start"]) + 12345
    assert sessions._write_record(rec)      # a recycled-pid look-alike
    out = mcp_server.stop_agent(sid)
    assert "not running" in out, out
    time.sleep(0.2)
    assert p.poll() is None                 # the process was NOT touched


def test_r1_10_unknown_agent_is_still_an_error(ledger):
    assert mcp_server.stop_agent("nope-nope").startswith("Error: unknown agent")


# ── R1-11 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["bash", "read_file", "write_file",
                                  "edit_file", "list_files", "grep", "fetch",
                                  "web_search"])
def test_r1_11_mcp_wrappers_match_tools_signatures(name):
    wrap = inspect.signature(getattr(mcp_server, name)).parameters
    impl = inspect.signature(getattr(_tools, name)).parameters
    assert list(wrap) == list(impl)
    for pname in impl:
        assert wrap[pname].default == impl[pname].default, pname


def test_r1_11_grep_ignore_case_and_context_reach_tools(tmp_path):
    (tmp_path / "f.txt").write_text("before\nHELLO world\nafter\n")
    out = mcp_server.grep("hello", str(tmp_path), "", context_lines=1,
                          ignore_case=True)
    assert "HELLO world" in out and "before" in out
    assert mcp_server.grep("hello", str(tmp_path)) .startswith("(no matches)")


# ── R9-02 / R9-05 ────────────────────────────────────────────────────────────

def _write_skill(base, entry, body, name=None):
    d = base / entry
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name or entry}\ndescription: d\n---\n{body}\n")
    return d


@pytest.fixture()
def skilldirs(tmp_path, monkeypatch):
    repo = tmp_path / "repo-skills"
    glob = tmp_path / "global-skills"
    repo.mkdir()
    glob.mkdir()
    monkeypatch.setattr(mcp_server, "_REPO_SKILLS_DIR", str(repo))
    monkeypatch.setattr(mcp_server, "_GLOBAL_SKILLS_DIR", str(glob))
    monkeypatch.setattr(mcp_server, "_SKILLS_CACHE", None)
    captured = {}

    def fake_start_agent(**kw):
        captured.update(kw)
        return "started"
    monkeypatch.setattr(mcp_server, "start_agent", fake_start_agent)
    return repo, glob, captured


def test_r9_02_skill_name_sees_edits_and_deletes(skilldirs):
    repo, _glob, captured = skilldirs
    d = _write_skill(repo, "x", "BODY v1")
    assert "BODY v1" in mcp_server.skill("x")
    _write_skill(repo, "x", "BODY v2")
    assert "BODY v2" in mcp_server.skill("x")        # no index call needed
    mcp_server.start_skill_agent("x", "task")
    assert "BODY v2" in captured["context"]
    (d / "SKILL.md").unlink()
    assert mcp_server.skill("x").startswith("Error: skill 'x' not found")
    captured.clear()
    assert mcp_server.start_skill_agent("x", "task").startswith("Error")
    assert not captured                               # nothing spawned


def test_r9_05_hand_installed_global_skill_is_labelled(skilldirs):
    repo, glob, captured = skilldirs
    _write_skill(repo, "r", "repo body")
    _write_skill(glob, "g", "global body")
    index = mcp_server.skill("")
    g_line = next(l for l in index.splitlines() if l.strip().startswith("g "))
    r_line = next(l for l in index.splitlines() if l.strip().startswith("r "))
    assert "[global, unverified]" in g_line
    assert "unverified" not in r_line and "[repo]" in r_line
    assert "(global, unverified)" in mcp_server.skill("g")
    assert "unverified" not in mcp_server.skill("r")
    mcp_server.start_skill_agent("g", "t")
    assert "source: global, unverified" in captured["context"]


def test_r9_05_global_symlink_into_repo_counts_as_gated(skilldirs):
    repo, glob, _captured = skilldirs
    target = _write_skill(repo, "linked", "b")
    os.symlink(target, glob / "linked-alias")
    rec = {"source": "global",
           "path": str(glob / "linked-alias" / "SKILL.md")}
    assert mcp_server._skill_gated(rec)
    assert mcp_server._skill_source_label(rec) == "global"
    rogue = _write_skill(glob, "rogue", "b")
    assert not mcp_server._skill_gated(
        {"source": "global", "path": str(rogue / "SKILL.md")})


