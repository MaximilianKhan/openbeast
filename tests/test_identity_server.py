#!/usr/bin/env python3
"""Identity tool server (agents/openapi_tools.py) — auth, sharding, audit.

Covers:
  - all 20 tools (the 19 MCP tools + weather) registered in openapi.json; /health reports mode
  - unkeyed mode: calls pass with no Authorization (Phase-1 parity)
  - keyed mode: 401 no key / 403 wrong key / 404 guest on admin tool /
    200 admin; guest can reach its own tools (auth layer)
  - per-user sharding: identity header → writes land in users/<id>/ with a
    per-shard manifest; headerless → shared root; per-chat adds chats/<id>
  - header sanitization (path-traversal attempts neutralized)
  - audit log: entries carry user/tool/ok and a digest, never raw args

Run: pytest tests/test_identity_server.py
"""

import importlib
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

import openapi_tools  # noqa: E402
import tools as _tools  # noqa: E402

ADMIN = {"Authorization": "Bearer test-admin-key"}
GUEST = {"Authorization": "Bearer test-guest-key"}


# Every env var a test here sets. They must all come back exactly as found:
# a key written straight into os.environ outlived this module and silently
# put every later tool-server test in the session into keyed mode.
_ENV_SET_HERE = ("OPENBEAST_MCPO_ADMIN_KEY", "OPENBEAST_MCPO_GUEST_KEY",
                 "OPENBEAST_FILES_SHARDING", "OPENBEAST_IDENTITY_JWT_SECRET",
                 "OPENBEAST_FILES_DIR")


@pytest.fixture(autouse=True, scope="module")
def _module_leaves_env_as_found():
    before = {k: os.environ.get(k) for k in _ENV_SET_HERE}
    yield
    after = {k: os.environ.get(k) for k in _ENV_SET_HERE}
    assert after == before, "test_identity_server leaked env into the session"


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "files"
    ws.mkdir()
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(ws))
    monkeypatch.delenv("AGENT_WORKDIR", raising=False)
    monkeypatch.delenv("OPENBEAST_MCPO_ADMIN_KEY", raising=False)
    monkeypatch.delenv("OPENBEAST_MCPO_GUEST_KEY", raising=False)
    # Header mode unless a test opts in: a --with-jwt rig's sourced conf
    # exports the secret, and the app would then ignore the plain headers.
    monkeypatch.delenv("OPENBEAST_IDENTITY_JWT_SECRET", raising=False)
    monkeypatch.setenv("OPENBEAST_FILES_SHARDING", "user")
    importlib.reload(_tools)
    return ws


def client(monkeypatch=None, keyed=False, sharding=None):
    # Env goes through monkeypatch ONLY, so pytest undoes it at teardown.
    if (keyed or sharding) and monkeypatch is None:
        raise AssertionError("client(keyed/sharding) needs monkeypatch")
    if keyed:
        monkeypatch.setenv("OPENBEAST_MCPO_ADMIN_KEY", "test-admin-key")
        monkeypatch.setenv("OPENBEAST_MCPO_GUEST_KEY", "test-guest-key")
    if sharding:
        monkeypatch.setenv("OPENBEAST_FILES_SHARDING", sharding)
    return TestClient(openapi_tools.create_app(), base_url="http://127.0.0.1:3001")


def manifest(root):
    p = root / ".manifest.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


# --- surface -----------------------------------------------------------------

def test_all_tools_in_spec_and_health(workspace):
    c = client()
    spec = c.get("/openapi.json").json()
    for name in openapi_tools.TOOL_NAMES:
        assert f"/{name}" in spec["paths"], name
    h = c.get("/health").json()
    assert h["status"] == "ok" and h["tools"] == 20 and h["auth"] == "open"


# --- auth matrix -------------------------------------------------------------

def test_unkeyed_mode_is_open(workspace):
    c = client()
    r = c.post("/list_files", json={"directory": ".", "pattern": "*"})
    assert r.status_code == 200


def test_keyed_mode_auth_matrix(workspace, monkeypatch):
    c = client(monkeypatch, keyed=True)
    body = {"directory": ".", "pattern": "*"}
    assert c.post("/list_files", json=body).status_code == 401           # no key
    bad = {"Authorization": "Bearer nope"}
    assert c.post("/list_files", json=body, headers=bad).status_code == 403
    assert c.post("/list_files", json=body, headers=ADMIN).status_code == 200
    # guest on an admin tool: 404 — the tool "doesn't exist" for guests
    assert c.post("/list_files", json=body, headers=GUEST).status_code == 404
    assert c.post("/bash", json={"command": "true"}, headers=GUEST).status_code == 404
    assert c.get("/health").status_code == 200                           # spec/liveness open


# --- sharding ----------------------------------------------------------------

def test_per_user_sharding(workspace):
    c = client()
    hdr = {"X-OpenWebUI-User-Id": "alice"}
    r = c.post("/write_file", json={"path": "report.md", "content": "hi"}, headers=hdr)
    assert r.status_code == 200
    shard = workspace / "users" / openapi_tools._sanitize("alice")
    assert (shard / "report.md").exists()
    # alice's manifest lives in HER shard, not the root
    assert [e["path"] for e in manifest(shard)] == ["report.md"]
    assert manifest(workspace) == []


def test_headerless_uses_shared_root(workspace):
    c = client()
    r = c.post("/write_file", json={"path": "shared.md", "content": "x"})
    assert r.status_code == 200
    assert (workspace / "shared.md").exists()
    assert not (workspace / "users").exists()


def test_users_are_isolated_by_namespace(workspace):
    c = client()
    c.post("/write_file", json={"path": "a.md", "content": "A"},
           headers={"X-OpenWebUI-User-Id": "alice"})
    r = c.post("/list_files", json={"directory": ".", "pattern": "**/*"},
               headers={"X-OpenWebUI-User-Id": "bob"})
    assert "a.md" not in r.text


def test_per_chat_sharding(workspace, monkeypatch):
    c = client(monkeypatch, sharding="chat")
    hdr = {"X-OpenWebUI-User-Id": "alice", "X-OpenWebUI-Chat-Id": "chat-42"}
    c.post("/write_file", json={"path": "n.md", "content": "x"}, headers=hdr)
    assert (workspace / "users" / openapi_tools._sanitize("alice") / "chats"
            / openapi_tools._sanitize("chat-42") / "n.md").exists()


def test_sharding_off(workspace, monkeypatch):
    c = client(monkeypatch, sharding="off")
    c.post("/write_file", json={"path": "root.md", "content": "x"},
           headers={"X-OpenWebUI-User-Id": "alice"})
    assert (workspace / "root.md").exists()
    assert not (workspace / "users").exists()


def test_hostile_user_id_is_sanitized(workspace):
    c = client()
    hdr = {"X-OpenWebUI-User-Id": "../../etc"}
    c.post("/write_file", json={"path": "x.md", "content": "x"}, headers=hdr)
    shards = os.listdir(workspace / "users")
    assert len(shards) == 1 and "/" not in shards[0] and ".." not in shards[0]
    # the write stayed inside the workspace
    assert (workspace / "users" / shards[0] / "x.md").exists()


# --- audit -------------------------------------------------------------------

def test_audit_log_written_without_raw_args(workspace):
    audit = os.environ["OPENBEAST_TOOL_AUDIT_PATH"]   # conftest isolates it
    before = os.path.getsize(audit) if os.path.exists(audit) else 0
    c = client()
    secret = "the-secret-content-should-not-appear"
    c.post("/write_file", json={"path": "s.md", "content": secret},
           headers={"X-OpenWebUI-User-Id": "alice", "X-OpenWebUI-User-Role": "admin"})
    with open(audit) as f:
        f.seek(before)
        tail = f.read()
    assert secret not in tail
    entry = json.loads(tail.strip().splitlines()[-1])
    assert entry["tool"] == "write_file" and entry["user"] == "alice"
    assert entry["ok"] is True and "args_hmac" in entry


# --- perfection-pass regressions (2026-07-09 adversarial review) --------------

def test_sanitize_is_injective():
    """Distinct raw ids must NEVER share a shard (review bug #3)."""
    ids = ["max", "max!", "max?", "日本語", "中文", "a" * 100, "a" * 101]
    shards = [openapi_tools._sanitize(i) for i in ids]
    assert len(set(shards)) == len(ids), shards
    assert all("/" not in s and ".." not in s for s in shards)


def test_denied_calls_reach_audit_and_metrics(workspace, monkeypatch):
    """401/403/404 must be visible in the audit trail (review bug #4)."""
    audit = os.environ["OPENBEAST_TOOL_AUDIT_PATH"]   # conftest isolates it
    before = os.path.getsize(audit) if os.path.exists(audit) else 0
    c = client(monkeypatch, keyed=True)
    c.post("/bash", json={"command": "true"},
           headers={"Authorization": "Bearer test-guest-key",
                    "X-OpenWebUI-User-Id": "prober"})   # guest on admin tool: 404
    with open(audit) as f:
        f.seek(before)
        entries = [json.loads(x) for x in f.read().splitlines() if x.strip()]
    denied = [e for e in entries if e["profile"] == "denied"]
    assert denied and denied[-1]["tool"] == "bash" and "404" in denied[-1]["error"]
    assert 'profile="denied"' in c.get("/metrics").text


def test_spawn_workdir_anchored_to_shard(workspace, monkeypatch):
    """A relative start_agent workdir must resolve inside the caller's shard,
    not the server CWD (review bug #5). Stub the spawn (signature-preserving —
    the endpoint factory introspects it) and capture what it receives."""
    captured = {}

    def stub(task: str, workdir: str = ".", max_iter: int = 200,
             context: str = "", base_url: str = "") -> str:
        captured.update(task=task, workdir=workdir)
        return "agent stub"

    monkeypatch.setattr(openapi_tools.impl, "start_agent", stub)
    c = TestClient(openapi_tools.create_app(), base_url="http://127.0.0.1:3001")   # endpoint closes over the stub
    r = c.post("/start_agent", json={"task": "t", "workdir": "proj"},
               headers={"X-OpenWebUI-User-Id": "alice"})
    assert r.status_code == 200
    shard = str(workspace / "users" / openapi_tools._sanitize("alice"))
    assert captured["workdir"] == os.path.join(shard, "proj"), captured


def test_spawn_workdir_dotdot_escape_rejected(workspace, monkeypatch):
    """A relative workdir that normpath-collapses OUT of the shard must be a
    400, not a silent escape ("../../x" used to anchor then walk right out)."""
    def stub(task: str, workdir: str = ".", max_iter: int = 200,
             context: str = "", base_url: str = "") -> str:
        return "agent stub"

    monkeypatch.setattr(openapi_tools.impl, "start_agent", stub)
    c = TestClient(openapi_tools.create_app(), base_url="http://127.0.0.1:3001")
    r = c.post("/start_agent",
               json={"task": "t", "workdir": "../../../../etc"},
               headers={"X-OpenWebUI-User-Id": "alice"})
    assert r.status_code == 400
    assert "escapes" in r.json()["detail"]


def test_single_key_fails_closed(workspace, monkeypatch):
    """ONE configured key must flip the server to keyed mode. It used to
    require BOTH keys, so setting only the admin key — the intuitive way to
    lock down — silently left every tool open."""
    monkeypatch.setenv("OPENBEAST_MCPO_ADMIN_KEY", "test-admin-key")
    monkeypatch.delenv("OPENBEAST_MCPO_GUEST_KEY", raising=False)
    c = TestClient(openapi_tools.create_app(), base_url="http://127.0.0.1:3001")
    body = {"directory": ".", "pattern": "*"}
    assert c.get("/health").json()["auth"] == "keyed"
    assert c.post("/list_files", json=body).status_code == 401          # no key
    assert c.post("/list_files", json=body, headers=ADMIN).status_code == 200
    # The unconfigured guest profile doesn't exist — its old key is invalid.
    assert c.post("/fetch", json={"url": "http://example.com"},
                  headers=GUEST).status_code == 403


# --- 2026-09-29 review: audit isolation, file mode, denial integrity ----------

def _rows(path):
    if not os.path.exists(path):
        return []
    return [json.loads(x) for x in open(path).read().splitlines() if x.strip()]


def _keyed(monkeypatch, **kw):
    monkeypatch.setenv("OPENBEAST_MCPO_ADMIN_KEY", "test-admin-key")
    monkeypatch.setenv("OPENBEAST_MCPO_GUEST_KEY", "test-guest-key")
    return TestClient(openapi_tools.create_app(), base_url="http://127.0.0.1:3001", **kw)


def test_audit_path_is_overridable_and_repo_trail_untouched(workspace, tmp_path,
                                                            monkeypatch):
    """Tests used to append thousands of fixture rows (forged users, denied
    /bash probes, "../../etc") to the rig's REAL .run/tool-audit.jsonl."""
    real = os.path.join(REPO, ".run", "tool-audit.jsonl")
    before = os.path.getsize(real) if os.path.exists(real) else None
    target = tmp_path / "elsewhere" / "audit.jsonl"
    monkeypatch.setenv("OPENBEAST_TOOL_AUDIT_PATH", str(target))
    c = client()
    c.post("/write_file", json={"path": "z.md", "content": "z"},
           headers={"X-OpenWebUI-User-Id": "alice"})
    assert [r["user"] for r in _rows(str(target))] == ["alice"]
    after = os.path.getsize(real) if os.path.exists(real) else None
    assert after == before, "a test run wrote into the repo's live audit trail"


def test_audit_path_follows_run_dir(tmp_path, monkeypatch):
    """Without the explicit override, OPENBEAST_RUN_DIR decides (as it does
    for the artifact server's audit), then the repo's .run."""
    monkeypatch.delenv("OPENBEAST_TOOL_AUDIT_PATH", raising=False)
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(tmp_path / "run"))
    assert openapi_tools._audit_path() == str(tmp_path / "run" / "tool-audit.jsonl")
    monkeypatch.delenv("OPENBEAST_RUN_DIR")
    assert openapi_tools._audit_path() == os.path.join(
        openapi_tools.REPO_DIR, ".run", "tool-audit.jsonl")


def test_audit_file_is_private(workspace, tmp_path, monkeypatch):
    """0600 like every sibling audit (edge/artifact/chat) — and an existing
    0644 trail from an older build is tightened at startup."""
    new = tmp_path / "a" / "new.jsonl"
    monkeypatch.setenv("OPENBEAST_TOOL_AUDIT_PATH", str(new))
    client().post("/list_files", json={"directory": ".", "pattern": "*"})
    assert (os.stat(new).st_mode & 0o777) == 0o600
    old = tmp_path / "old.jsonl"
    old.write_text("")
    os.chmod(old, 0o644)
    monkeypatch.setenv("OPENBEAST_TOOL_AUDIT_PATH", str(old))
    client()
    assert (os.stat(old).st_mode & 0o777) == 0o600


def test_non_ascii_bearer_is_denied_and_audited(workspace, monkeypatch):
    """compare_digest on str raised TypeError for `Bearer caf\\xe9` (Starlette
    decodes headers as latin-1): an unhandled 500 with no audit row and no
    denied metric. Now it is an ordinary wrong key."""
    c = _keyed(monkeypatch, raise_server_exceptions=False)
    body = {"directory": ".", "pattern": "*"}
    for raw in (b"Bearer caf\xe9", b"Bearer \xff\xfe"):
        r = c.post("/list_files", json=body, headers={"Authorization": raw})
        assert r.status_code == 403, (raw, r.status_code)
    # Negative control: the real key still works.
    assert c.post("/list_files", json=body, headers=ADMIN).status_code == 200
    denied = [e for e in _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])
              if e["profile"] == "denied"]
    assert len(denied) == 2 and all("403" in e["error"] for e in denied)
    assert 'profile="denied"' in c.get("/metrics").text


def test_jwt_mode_denial_row_does_not_trust_typed_ids(workspace, monkeypatch):
    """In JWT mode plain identity headers are forgeable and ignored — but the
    DENIAL row copied them as `user`, so anyone could write "alice probed
    /bash" into the trail. The typed id is kept only as claimed_user."""
    monkeypatch.setenv("OPENBEAST_IDENTITY_JWT_SECRET", "x" * 40)
    c = _keyed(monkeypatch)
    r = c.post("/bash", json={"command": "true"},
               headers={**GUEST, "X-OpenWebUI-User-Id": "alice-victim",
                        "X-OpenWebUI-User-Role": "admin"})
    assert r.status_code == 404
    row = _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])[-1]
    assert row["profile"] == "denied" and row["identity"] == "jwt"
    assert row["user"] is None and row["role"] is None
    assert row["claimed_user"] == "alice-victim"


def test_header_mode_denial_row_keeps_header_identity(workspace, monkeypatch):
    """Negative control: header mode trusts the headers everywhere (loopback
    threat model), so the denial row still names the caller as `user`."""
    c = _keyed(monkeypatch)
    c.post("/bash", json={"command": "true"},
           headers={**GUEST, "X-OpenWebUI-User-Id": "prober"})
    row = _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])[-1]
    assert row["user"] == "prober" and "claimed_user" not in row


# --- 2026-11 systems review: pre-body gate, audit hardening -----------------

def _asgi_post(app, path, headers, receive):
    """Drive the ASGI app by hand, so a test controls (and can forbid) the
    body read. Returns (status, body bytes)."""
    import asyncio
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": path,
             "raw_path": path.encode(), "root_path": "", "query_string": b"",
             "server": ("127.0.0.1", 3001), "client": ("127.0.0.1", 50000),
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}
    sent = []

    async def send(msg):
        sent.append(msg)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent
                    if m["type"] == "http.response.body")
    return status, body


def test_unauthenticated_body_is_never_read(workspace, monkeypatch):
    """The key was checked INSIDE the endpoint, after FastAPI had read and
    parsed the whole body: a keyless caller could make the process hold a
    300 MB POST in (unified) memory. The key is now checked from the headers
    before one body byte is received."""
    monkeypatch.setenv("OPENBEAST_MCPO_ADMIN_KEY", "test-admin-key")
    monkeypatch.setenv("OPENBEAST_MCPO_GUEST_KEY", "test-guest-key")
    app = openapi_tools.create_app()

    async def receive():
        raise AssertionError("the body was read before the key check")

    for hdrs, want in (([], 401), ([("authorization", "Bearer nope")], 403),
                       ([("authorization", "Bearer test-guest-key")], 404)):
        status, _ = _asgi_post(app, "/bash",
                               [("host", "127.0.0.1:3001"),
                                ("content-type", "application/json"),
                                ("content-length", "300000000")] + hdrs,
                               receive)
        assert status == want, (hdrs, status)
    denied = [e for e in _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])
              if e["profile"] == "denied"]
    assert len(denied) == 3 and all(e["tool"] == "bash" for e in denied)


def test_malformed_body_without_key_is_401_not_422(workspace, monkeypatch):
    """Validation used to run first: no key + a bad body was a 422 that
    echoed the input back and wrote no audit row."""
    c = _keyed(monkeypatch)
    r = c.post("/bash", content=b"{not json",
               headers={"Content-Type": "application/json"})
    assert r.status_code == 401
    assert "not json" not in r.text
    assert _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])[-1]["profile"] == "denied"


def test_oversized_body_is_413(workspace, monkeypatch):
    """A body cap, on Content-Length AND on a streamed (chunked) body, so even
    a key holder cannot park an unbounded body in memory."""
    monkeypatch.setenv("OPENBEAST_TOOLS_MAX_BODY_BYTES", "1024")
    c = _keyed(monkeypatch)
    big = {"path": "x.md", "content": "y" * 4096}
    r = c.post("/write_file", json=big, headers=ADMIN)
    assert r.status_code == 413
    assert not (workspace / "x.md").exists()

    def chunks():
        yield b'{"path": "x.md", "content": "'
        for _ in range(8):
            yield b"y" * 512
        yield b'"}'

    r = c.post("/write_file", content=chunks(),
               headers={**ADMIN, "Content-Type": "application/json"})
    assert r.status_code == 413
    assert not (workspace / "x.md").exists()
    rows = [e for e in _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])
            if "413" in e.get("error", "")]
    assert len(rows) == 2 and all(e["profile"] == "admin" for e in rows)
    # Negative control: a body under the cap still goes through.
    r = c.post("/write_file", json={"path": "x.md", "content": "small"},
               headers=ADMIN)
    assert r.status_code == 200 and (workspace / "x.md").read_text() == "small"


def test_validation_failure_is_audited(workspace, monkeypatch):
    """A 422 for malformed arguments (key valid) reaches the trail + metrics."""
    c = _keyed(monkeypatch)
    r = c.post("/bash", json={"bad": 1}, headers=ADMIN)
    assert r.status_code == 422
    row = _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])[-1]
    assert row["tool"] == "bash" and row["ok"] is False and "422" in row["error"]
    assert row["profile"] == "admin"
    assert 'tool="bash",profile="admin",outcome="error"' in c.get("/metrics").text


def test_workdir_escape_is_audited(workspace, monkeypatch):
    def stub(task: str, workdir: str = ".", max_iter: int = 200,
             context: str = "", base_url: str = "") -> str:
        return "agent stub"

    monkeypatch.setattr(openapi_tools.impl, "start_agent", stub)
    c = TestClient(openapi_tools.create_app(), base_url="http://127.0.0.1:3001")
    r = c.post("/start_agent", json={"task": "t", "workdir": "../../../../etc"},
               headers={"X-OpenWebUI-User-Id": "alice"})
    assert r.status_code == 400
    row = _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])[-1]
    assert row["tool"] == "start_agent" and row["ok"] is False
    assert row["user"] == "alice" and "escapes" in row["error"]
    assert 'tool="start_agent"' in c.get("/metrics").text


def test_audit_identity_fields_are_capped(workspace, monkeypatch):
    """user/role/chat were written untruncated: one keyless request with a
    15 KB header appended a 15 KB row."""
    c = _keyed(monkeypatch)
    huge = "v" * 15000
    c.post("/bash", json={"command": "true"},
           headers={**ADMIN, "X-OpenWebUI-User-Id": huge,
                    "X-OpenWebUI-User-Role": huge, "X-OpenWebUI-Chat-Id": huge})
    c.post("/bash", json={"command": "true"},
           headers={"X-OpenWebUI-User-Id": huge, "X-OpenWebUI-User-Role": huge,
                    "X-OpenWebUI-Chat-Id": huge})
    for raw in open(os.environ["OPENBEAST_TOOL_AUDIT_PATH"]).read().splitlines()[-2:]:
        assert len(raw) < 2000, len(raw)


def test_header_mode_keyless_denial_marks_typed_id_as_claimed(workspace,
                                                              monkeypatch):
    """A caller that failed the KEY check proved nothing, so the id it typed
    is kept only as claimed_user — the rule JWT mode already follows. (A
    VALID key with a header id stays `user`: see the negative control
    test_header_mode_denial_row_keeps_header_identity.)"""
    c = _keyed(monkeypatch)
    for hdrs in ({}, {"Authorization": "Bearer nope"}):
        c.post("/bash", json={"command": "true"},
               headers={**hdrs, "X-OpenWebUI-User-Id": "alice-victim",
                        "X-OpenWebUI-User-Role": "admin"})
        row = _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])[-1]
        assert row["user"] is None and row["role"] is None
        assert row["claimed_user"] == "alice-victim"


def test_audit_digest_is_keyed_and_size_is_bucketed(workspace):
    """An unsalted sha256 of canonical JSON let anyone holding the log
    confirm a guessed web_search query offline, with args_bytes giving its
    exact length. The digest is now an HMAC under a per-install key kept
    beside (never in) the trail, and the size is a power-of-two bound."""
    import hashlib
    c = client()
    args = {"path": "s.md", "content": "guessable"}
    c.post("/write_file", json=args)
    row = _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])[-1]
    blob = json.dumps(args, sort_keys=True).encode()
    assert "args_sha256" not in row and "args_bytes" not in row
    assert row["args_hmac"] != hashlib.sha256(blob).hexdigest()[:16]
    assert row["args_bytes_max"] >= len(blob)
    assert row["args_bytes_max"] & (row["args_bytes_max"] - 1) == 0
    key = os.path.join(os.path.dirname(os.environ["OPENBEAST_TOOL_AUDIT_PATH"]),
                       "tool-audit.key")
    assert (os.stat(key).st_mode & 0o777) == 0o600
    # Stable across restarts (same install key), so identical calls correlate.
    client().post("/write_file", json=args)
    assert _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])[-1]["args_hmac"] \
        == row["args_hmac"]


def test_no_cdn_docs_pages(workspace):
    """/docs and /redoc loaded floating swagger-ui@5 / redoc@2 from a CDN
    into the same origin as /bash. WebUI needs only /openapi.json."""
    c = client()
    assert c.get("/docs").status_code == 404
    assert c.get("/redoc").status_code == 404
    assert c.get("/openapi.json").status_code == 200


def test_guest_key_reaches_its_own_tools(workspace, monkeypatch):
    """The module docstring claimed this was covered; every guest assertion
    was negative, so GUEST_TOOLS = set() passed the whole suite."""
    def web_search(query: str, max_results: int = 10, pageno: int = 1,
                   time_range: str = "") -> str:
        return "stub results"

    def weather(location: str = "", days: int = 3, units: str = "") -> str:
        return "stub weather"

    def fetch(url: str, max_length: int = 50_000) -> str:
        return "stub page"

    for fn in (web_search, weather, fetch):
        monkeypatch.setattr(openapi_tools.impl, fn.__name__, fn)
    import weather as _weather
    monkeypatch.setattr(_weather, "caller_location", lambda *a, **k: None)
    c = _keyed(monkeypatch)
    for path, body in (("/web_search", {"query": "q"}), ("/weather", {}),
                       ("/fetch", {"url": "https://example.com"})):
        r = c.post(path, json=body, headers=GUEST)
        assert r.status_code == 200, (path, r.status_code, r.text)
        row = _rows(os.environ["OPENBEAST_TOOL_AUDIT_PATH"])[-1]
        assert row["profile"] == "guest" and row["ok"] is True, row
