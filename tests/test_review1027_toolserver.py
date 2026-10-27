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
    openapi_tools._AUDIT_KEY_CACHE.clear()
    return {"ws": ws, "audit": audit}


def _client(**kw):
    return TestClient(openapi_tools.create_app(),
                      base_url="http://127.0.0.1:3001", **kw)


def _rows(audit):
    if not audit.exists():
        return []
    return [json.loads(x) for x in audit.read_text().splitlines() if x.strip()]


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


# ── R1-07 ────────────────────────────────────────────────────────────────────

def test_r1_07_workdir_escape_is_audited(toolserver):
    c = _client()
    r = c.post("/start_agent",
               json={"task": "x", "workdir": "../../../etc"},
               headers={"X-OpenWebUI-User-Id": "alice"})
    assert r.status_code == 400 and "escapes" in r.text
    rows = _rows(toolserver["audit"])
    assert len(rows) == 1
    row = rows[0]
    assert row["tool"] == "start_agent" and row["ok"] is False
    assert row["user"] == "alice" and "escapes" in row["error"]
    # The raw workdir never lands in the trail — only the digest.
    assert "../../../etc" not in toolserver["audit"].read_text()
    m = c.get("/metrics").text
    assert 'tool="start_agent",profile="open",outcome="error"} 1' in m


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


def test_r1_07_control_normal_call_still_one_ok_row(toolserver):
    c = _client()
    r = c.post("/write_file", json={"path": "a.md", "content": "x"},
               headers={"X-OpenWebUI-User-Id": "carol"})
    assert r.status_code == 200
    rows = _rows(toolserver["audit"])
    assert len(rows) == 1 and rows[0]["ok"] is True


# ── R1-09 ────────────────────────────────────────────────────────────────────

import router  # noqa: E402
from starlette.testclient import TestClient as _StarletteClient  # noqa: E402


@pytest.mark.parametrize("messages", ["hi", [1], [None, "x"], {"a": 1}, 7,
                                      None])
def test_r1_09_last_user_text_tolerates_garbage(messages):
    assert router._last_user_text(messages) == ""


def test_r1_09_last_user_text_still_reads_real_turns():
    msgs = [1, {"role": "user", "content": "first"}, "junk",
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": [{"type": "text", "text": "last"}, 3]}]
    assert router._last_user_text(msgs) == "last"


class _Upstream:
    def __init__(self):
        self.proxied = []

    async def post(self, *a, **k):  # classify / spawn: must not happen here
        raise AssertionError("garbage messages must not reach the classifier")

    def build_request(self, method, url, content=None, headers=None):
        self.proxied.append(url)
        return {"url": url}

    async def send(self, req, stream=False):
        class Resp:
            status_code = 400
            headers = {"content-type": "application/json"}

            async def aiter_raw(self_inner):
                yield b'{"error": "upstream says no"}'

            async def aclose(self_inner):
                pass
        return Resp()

    async def aclose(self):
        pass


@pytest.mark.parametrize("messages", ["hi", [1]])
def test_r1_09_garbage_messages_are_proxied_not_500(monkeypatch, messages):
    from instinct.routerhook import RouterInstinct
    monkeypatch.setattr(router, "REQUIRE_IDENTITY", False)
    monkeypatch.setattr(router, "JWT_SECRET", "")
    monkeypatch.setattr(router, "_LLAMA_KEY", "")
    monkeypatch.setattr(router, "_INSTINCT", RouterInstinct("off"))
    up = _Upstream()
    with _StarletteClient(router.app, base_url="http://127.0.0.1:8088") as c:
        router.app.state.client = up
        r = c.post("/v1/chat/completions",
                   content=json.dumps({"model": "m", "messages": messages}),
                   headers={"Content-Type": "application/json"})
    asyncio.run(router._INSTINCT.drain())
    assert r.status_code == 400 and "upstream says no" in r.text
    assert len(up.proxied) == 1


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


# ── R1-12 ────────────────────────────────────────────────────────────────────

def test_r1_12_args_digest_is_keyed_not_a_bare_sha256(toolserver):
    c = _client()
    body = {"command": "ls -la", "timeout": 120}
    blob = json.dumps(body, sort_keys=True, default=str).encode()
    plain = hashlib.sha256(blob).hexdigest()[:16]
    for _ in range(2):
        assert c.post("/bash", json=body).status_code == 200
    rows = _rows(toolserver["audit"])
    assert len(rows) == 2
    assert rows[0]["args_sha256"] != plain          # dictionary-proof
    assert rows[0]["args_sha256"] == rows[1]["args_sha256"]  # still correlates
    assert len(rows[0]["args_sha256"]) == 16
    assert rows[0]["args_digest"] == "hmac-sha256"
    key = toolserver["audit"].parent / "tool-audit.key"
    assert key.exists() and stat.S_IMODE(key.stat().st_mode) == 0o600
    assert key.read_text().strip() not in toolserver["audit"].read_text()


def test_r1_12_key_is_per_install(tmp_path):
    a = openapi_tools.args_digest(str(tmp_path / "a" / "t.jsonl"), b"{}")
    b = openapi_tools.args_digest(str(tmp_path / "b" / "t.jsonl"), b"{}")
    assert a != b
    # ...and stable for one install, across a cold cache (a restart).
    openapi_tools._AUDIT_KEY_CACHE.clear()
    assert openapi_tools.args_digest(str(tmp_path / "a" / "t.jsonl"),
                                     b"{}") == a


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


# ── R7-02 (test-only) ────────────────────────────────────────────────────────

def test_r7_02_key_withheld_from_foreign_host_with_worker_configured(
        monkeypatch):
    import runner
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENBEAST_API_KEY", "rig-key")
    monkeypatch.setenv("OPENBEAST_AGENT_INFERENCE_URL",
                       "https://beast.ts.net:8443/v1")
    assert runner.resolve_api_key(
        base_url="https://attacker.example/v1") == "not-needed"
    # Control: the configured worker host does get it.
    assert runner.resolve_api_key(
        base_url="https://beast.ts.net:8443/v1") == "rig-key"


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
