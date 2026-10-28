#!/usr/bin/env python3
"""Review 2026-10-27 — network-facing services (R2-1 … R2-7, R7-07).

One class per finding. Each test builds its own registry / store / stub under
tmp_path; nothing binds a real port.

Run: python3 -m pytest tests/test_review1027_services.py -q
"""

from __future__ import annotations

import importlib
import json
import os
import sys

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_edge as _te  # noqa: E402
from test_edge import (DEVICE_KEY, _local_headers, _registry,  # noqa: E402
                       _stub_upstream)

edge = _te.edge          # fixture re-export (fresh module, scratch REPO_DIR)

HDR = {"Authorization": f"Bearer {DEVICE_KEY}"}
CHAT = "/v1/chat/completions"


def _anon_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_REPO_DIR", str(tmp_path))
    monkeypatch.setenv("OPENBEAST_EDGE_ALLOW_ANON", "true")
    import edge as _edge
    importlib.reload(_edge)
    return _edge


class TestR2_1RegistryLatch:
    """A registry that has existed stays authoritative when the file goes
    missing: moving it aside must not reopen the gate under ALLOW_ANON."""

    def test_moved_aside_registry_stays_closed(self, tmp_path, monkeypatch):
        e = _anon_edge(tmp_path, monkeypatch)
        path = _registry(tmp_path)
        _stub_upstream(e, {})
        with TestClient(e.app) as c:
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 200
            os.rename(path, str(path) + ".aside")
            r = c.post(CHAT, json={"messages": []})
            assert r.status_code == 401
            # The removed file's keys are gone with it.
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 401
            assert c.get("/gate/health",
                          headers=_local_headers(e)).json()["auth"] == "closed"
            # Re-enroll (a fresh file) — devices work again, no restart.
            os.rename(str(path) + ".aside", path)
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 200

    def test_registry_identify_after_vanish(self, tmp_path, monkeypatch):
        e = _anon_edge(tmp_path, monkeypatch)
        path = _registry(tmp_path)
        reg = e.Registry(str(path))
        assert reg.configured
        os.remove(path)
        assert reg.exists is True
        assert e._auth_mode(reg) == "closed"

    def test_never_existed_still_anon(self, tmp_path, monkeypatch):
        # Negative control: the documented migration mode is untouched.
        e = _anon_edge(tmp_path, monkeypatch)
        reg = e.Registry(str(tmp_path / ".run" / "clients.json"))
        assert reg.exists is False
        assert e._auth_mode(reg) == "anon"

    def test_repair_message_no_longer_advises_moving_aside(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "agents", "edge.py")).read()
        assert "move it aside" not in src


# ---------------------------------------------------------------------------
# R2-4 — beast-chat's registry reader on wrong-shaped JSON
# ---------------------------------------------------------------------------

def _chat_server():
    import chat_server
    return chat_server


class TestR2_4ChatRegistryShape:
    KEY = "k" * 40

    def _good(self):
        import hashlib
        return {"version": 1, "devices": [
            {"id": "lap", "scopes": ["chat"],
             "key_sha256": hashlib.sha256(self.KEY.encode()).hexdigest()}]}

    @pytest.mark.parametrize("content", ["[]", "null", "5",
                                         '{"devices": 5}',
                                         '{"devices":[{"key_sha256":5}]}'])
    def test_wrong_shape_at_startup_does_not_raise(self, tmp_path, content):
        cs = _chat_server()
        p = tmp_path / "clients.json"
        p.write_text(content)
        reg = cs.DeviceRegistry(str(p))          # used to AttributeError
        assert reg.lookup(self.KEY) is None

    @pytest.mark.parametrize("content", ["[]", "null", '{"devices": 5}'])
    def test_wrong_shape_at_runtime_keeps_last_good_map(self, tmp_path,
                                                        content):
        cs = _chat_server()
        p = tmp_path / "clients.json"
        p.write_text(json.dumps(self._good()))
        reg = cs.DeviceRegistry(str(p))
        assert reg.lookup(self.KEY)["id"] == "lap"
        p.write_text(content + " " * 7)          # new size → new stamp
        assert reg.lookup(self.KEY)["id"] == "lap"   # no 500, last good map
        assert reg.configured is True

    def test_valid_file_unchanged(self, tmp_path):
        # Negative control: a good file still loads and a missing devices
        # key still reads as "no devices".
        cs = _chat_server()
        p = tmp_path / "clients.json"
        p.write_text(json.dumps(self._good()))
        assert cs.DeviceRegistry(str(p)).lookup(self.KEY)["id"] == "lap"
        p.write_text('{"version": 1}')
        assert cs.DeviceRegistry(str(p)).configured is False
