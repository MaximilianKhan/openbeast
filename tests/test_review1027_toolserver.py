#!/usr/bin/env python3
"""Review 2026-10-27 — tool-server fixes (mcp_server / openapi_tools).

What is left of the review after main re-landed the rest on its own:

  R1-04  publish_artifact's workspace is never the process cwd
  R1-05  the unauthenticated MCP HTTP transport refuses a non-loopback bind
  R1-07  a shard that cannot be created is an audited 500
  R1-11  the MCP grep wrapper forwards every parameter tools.grep takes
  R9-03  the skill header names the skill's own directory
  R9-05  a hand-installed global skill is labelled unverified
  R7-06  a signed identity token without `exp` is refused (test-only)

Everything is in-process or a stubbed subprocess: no stack port is bound.

Run: python3 -m pytest tests/test_review1027_toolserver.py -q
"""
import importlib
import inspect
import json
import os
import socket
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
import tools as _tools  # noqa: E402


# ── shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture()
def toolserver(tmp_path, monkeypatch):
    """Header-mode, unkeyed identity tool server with a temp workspace and a
    temp audit trail (never the rig's real one)."""
    ws = tmp_path / "files"
    ws.mkdir()
    audit = tmp_path / "run" / "tool-audit.jsonl"
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(ws))
    monkeypatch.setenv("OPENBEAST_TOOL_AUDIT_PATH", str(audit))
    monkeypatch.setenv("OPENBEAST_FILES_SHARDING", "user")
    for k in ("AGENT_WORKDIR", "OPENBEAST_MCPO_ADMIN_KEY",
              "OPENBEAST_MCPO_GUEST_KEY", "OPENBEAST_IDENTITY_JWT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    importlib.reload(_tools)
    return {"ws": ws, "audit": audit}


def _client(**kw):
    return TestClient(openapi_tools.create_app(),
                      base_url="http://127.0.0.1:3001", **kw)


def _rows(audit):
    if not audit.exists():
        return []
    return [json.loads(x) for x in audit.read_text().splitlines() if x.strip()]


# ── R1-04 ────────────────────────────────────────────────────────────────────

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


# ── R1-07 ────────────────────────────────────────────────────────────────────

def test_r1_07_shard_creation_failure_is_an_audited_500(tmp_path, toolserver,
                                                        monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(blocker))
    c = _client(raise_server_exceptions=False)
    r = c.post("/write_file", json={"path": "a.md", "content": "x"},
               headers={"X-OpenWebUI-User-Id": "bob"})
    assert r.status_code == 500
    rows = _rows(toolserver["audit"])
    assert len(rows) == 1 and rows[0]["ok"] is False
    assert rows[0]["user"] == "bob" and "500" in rows[0]["error"]


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


# ── R9-03 / R9-05 ────────────────────────────────────────────────────────────

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


def test_r9_03_skill_header_names_its_directory(skilldirs):
    repo, glob, captured = skilldirs
    d = _write_skill(repo, "r", "body")
    g = _write_skill(glob, "g", "body")
    for name, where in (("r", d), ("g", g)):
        want = f"— files: {os.path.abspath(where)}/ ==="
        assert want in mcp_server.skill(name).splitlines()[0]
        mcp_server.start_skill_agent(name, "t")
        assert want in captured["context"].splitlines()[0]


# ── R7-06 (test-only) ────────────────────────────────────────────────────────

def test_r7_06_jwt_without_exp_is_rejected(toolserver, monkeypatch):
    secret = "test-jwt-secret"
    monkeypatch.setenv("OPENBEAST_IDENTITY_JWT_SECRET", secret)
    now = int(time.time())
    base = {"sub": "alice", "role": "admin", "iss": "open-webui", "iat": now}
    no_exp = pyjwt.encode(base, secret, algorithm="HS256")
    good = pyjwt.encode({**base, "exp": now + 300}, secret, algorithm="HS256")
    c = _client()
    r = c.post("/list_files", json={},
               headers={"X-OpenWebUI-User-Jwt": no_exp})
    assert r.status_code == 401 and "exp" in r.text
    r = c.post("/list_files", json={}, headers={"X-OpenWebUI-User-Jwt": good})
    assert r.status_code == 200
