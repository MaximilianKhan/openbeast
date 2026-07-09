#!/usr/bin/env python3
"""Signed identity (JWT mode) + /metrics on the identity tool server.

JWT mode (OPENBEAST_IDENTITY_JWT_SECRET set — mirrors Open WebUI's
FORWARD_USER_INFO_HEADER_JWT_SECRET, HS256, iss=open-webui):
  - a valid token's `sub` drives workspace sharding
  - plain X-OpenWebUI-User-Id headers are IGNORED (forgery dies)
  - forged / expired tokens -> 401
  - absent token -> anonymous shared root (router/CLI callers keep working)
/metrics: Prometheus text with per-tool counters after calls.

Run: pytest tests/test_identity_jwt.py
"""

import importlib
import os
import sys
import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

import openapi_tools  # noqa: E402
import tools as _tools  # noqa: E402

SECRET = "test-jwt-secret"


def mint(sub="alice", role="admin", exp_delta=300, secret=SECRET, iss="open-webui"):
    now = int(time.time())
    return pyjwt.encode(
        {"sub": sub, "role": role, "iss": iss, "iat": now, "exp": now + exp_delta},
        secret, algorithm="HS256",
    )


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "files"
    ws.mkdir()
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(ws))
    monkeypatch.setenv("OPENBEAST_IDENTITY_JWT_SECRET", SECRET)
    monkeypatch.setenv("OPENBEAST_FILES_SHARDING", "user")
    monkeypatch.delenv("AGENT_WORKDIR", raising=False)
    monkeypatch.delenv("OPENBEAST_MCPO_ADMIN_KEY", raising=False)
    monkeypatch.delenv("OPENBEAST_MCPO_GUEST_KEY", raising=False)
    importlib.reload(_tools)
    return ws


def client():
    return TestClient(openapi_tools.create_app())


def test_health_reports_jwt_mode(workspace):
    assert client().get("/health").json()["identity"] == "jwt"


def test_valid_jwt_sub_drives_sharding(workspace):
    c = client()
    r = c.post("/write_file", json={"path": "j.md", "content": "x"},
               headers={"X-OpenWebUI-User-Jwt": mint(sub="alice")})
    assert r.status_code == 200
    assert (workspace / "users" / "alice" / "j.md").exists()


def test_plain_headers_ignored_in_jwt_mode(workspace):
    """The forgery kill: claiming an identity via plain header does nothing."""
    c = client()
    r = c.post("/write_file", json={"path": "f.md", "content": "x"},
               headers={"X-OpenWebUI-User-Id": "victim"})
    assert r.status_code == 200
    assert (workspace / "f.md").exists()          # anonymous → shared root
    assert not (workspace / "users").exists()      # no shard for the forger


def test_forged_token_rejected(workspace):
    c = client()
    r = c.post("/write_file", json={"path": "x.md", "content": "x"},
               headers={"X-OpenWebUI-User-Jwt": mint(secret="wrong-secret")})
    assert r.status_code == 401


def test_expired_token_rejected(workspace):
    c = client()
    r = c.post("/write_file", json={"path": "x.md", "content": "x"},
               headers={"X-OpenWebUI-User-Jwt": mint(exp_delta=-10)})
    assert r.status_code == 401


def test_wrong_issuer_rejected(workspace):
    c = client()
    r = c.post("/write_file", json={"path": "x.md", "content": "x"},
               headers={"X-OpenWebUI-User-Jwt": mint(iss="evil")})
    assert r.status_code == 401


def test_absent_token_is_anonymous(workspace):
    c = client()
    r = c.post("/list_files", json={"directory": ".", "pattern": "*"})
    assert r.status_code == 200


def test_metrics_exposition(workspace):
    c = client()
    c.post("/write_file", json={"path": "m.md", "content": "x"},
           headers={"X-OpenWebUI-User-Jwt": mint()})
    body = c.get("/metrics").text
    assert "# TYPE openbeast_tool_calls_total counter" in body
    assert 'openbeast_tool_calls_total{tool="write_file",profile="open",outcome="ok"} 1' in body
    assert 'openbeast_tool_latency_ms_total{tool="write_file"}' in body
