#!/usr/bin/env python3
"""Identity tool server (agents/openapi_tools.py) — auth, sharding, audit.

Covers:
  - all 15 tools registered in openapi.json; /health reports mode
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


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "files"
    ws.mkdir()
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(ws))
    monkeypatch.delenv("AGENT_WORKDIR", raising=False)
    monkeypatch.delenv("OPENBEAST_MCPO_ADMIN_KEY", raising=False)
    monkeypatch.delenv("OPENBEAST_MCPO_GUEST_KEY", raising=False)
    monkeypatch.setenv("OPENBEAST_FILES_SHARDING", "user")
    importlib.reload(_tools)
    return ws


def client(monkeypatch=None, keyed=False, sharding=None):
    if keyed:
        os.environ["OPENBEAST_MCPO_ADMIN_KEY"] = "test-admin-key"
        os.environ["OPENBEAST_MCPO_GUEST_KEY"] = "test-guest-key"
    if sharding:
        os.environ["OPENBEAST_FILES_SHARDING"] = sharding
    return TestClient(openapi_tools.create_app())


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
    assert h["status"] == "ok" and h["tools"] == 15 and h["auth"] == "open"


# --- auth matrix -------------------------------------------------------------

def test_unkeyed_mode_is_open(workspace):
    c = client()
    r = c.post("/list_files", json={"directory": ".", "pattern": "*"})
    assert r.status_code == 200


def test_keyed_mode_auth_matrix(workspace):
    c = client(keyed=True)
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
    assert (workspace / "users" / "alice" / "report.md").exists()
    # alice's manifest lives in HER shard, not the root
    assert [e["path"] for e in manifest(workspace / "users" / "alice")] == ["report.md"]
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


def test_per_chat_sharding(workspace):
    c = client(sharding="chat")
    hdr = {"X-OpenWebUI-User-Id": "alice", "X-OpenWebUI-Chat-Id": "chat-42"}
    c.post("/write_file", json={"path": "n.md", "content": "x"}, headers=hdr)
    assert (workspace / "users" / "alice" / "chats" / "chat-42" / "n.md").exists()


def test_sharding_off(workspace):
    c = client(sharding="off")
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
    audit = os.path.join(REPO, ".run", "tool-audit.jsonl")
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
    assert entry["ok"] is True and "args_sha256" in entry
