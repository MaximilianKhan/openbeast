#!/usr/bin/env python3
"""beast-gate (agents/edge.py) — the 2026-10-09 review findings.

One class per finding. Each builds its own registry and stub upstream under
tmp_path (the helpers are test_edge.py's); nothing here opens a real port.

Run: python3 -m pytest tests/test_edge_review1009.py -q
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_edge import (DEVICE_KEY, REVOKED_KEY, _ChunkedResponse,  # noqa: E402
                       _FakeResponse, _last_audit, _laptop_bucket_app,
                       _local_headers, _registry, _stub_upstream,
                       edge)  # noqa: F401  (pytest fixture)

HDR = {"Authorization": f"Bearer {DEVICE_KEY}"}
CHAT = "/v1/chat/completions"


def _anon_edge(tmp_path, monkeypatch):
    """The module with EDGE_ALLOW_ANON=true (config is read at import)."""
    monkeypatch.setenv("OPENBEAST_REPO_DIR", str(tmp_path))
    monkeypatch.setenv("OPENBEAST_EDGE_ALLOW_ANON", "true")
    import edge as _edge
    importlib.reload(_edge)
    return _edge


class TestRegistryFileClosesAnon:
    """S7 + F11: EDGE_ALLOW_ANON covers a rig with NO registry file. A file
    that exists — emptied, or unreadable on first load — never reads as
    "no registry"."""

    def test_removing_the_last_device_does_not_reopen_the_gate(
            self, tmp_path, monkeypatch):
        e = _anon_edge(tmp_path, monkeypatch)
        path = _registry(tmp_path)
        _stub_upstream(e, {})
        with TestClient(e.app) as c:
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 200
            # What `clients.sh remove` leaves behind for the last device.
            path.write_text(json.dumps({"version": 1, "devices": []}))
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 401      # the removed key
            assert c.post(CHAT, json={"messages": []}).status_code == 401
            body = c.get("/gate/health", headers=_local_headers(e)).json()
        assert body["auth"] == "closed" and body["devices"] == 0

    def test_corrupt_registry_at_first_load_is_closed(self, tmp_path,
                                                      monkeypatch, capsys):
        e = _anon_edge(tmp_path, monkeypatch)
        path = _registry(tmp_path)
        path.write_text(path.read_text()[:40])       # truncated, then restart
        _stub_upstream(e, {})
        with TestClient(e.app) as c:
            r = c.post(CHAT, json={"messages": []})
            assert r.status_code == 401
            assert "registry_unreadable" in r.text and "clients.sh" in r.text
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 401
            c.post(CHAT, json={"messages": []})
            # Repaired on disk: enrolled devices work again with no restart.
            _registry(tmp_path)
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 200
        out = capsys.readouterr().out
        # Named once per bad file, not once per request.
        assert out.count("cannot be read as a device registry") == 1
        assert "refusing every caller" in out

    @pytest.mark.parametrize("content", ["[]", '{"devices": 5}',
                                         '{"devices": ["x"]}'])
    def test_wrong_shape_is_unreadable_not_a_500(self, tmp_path, monkeypatch,
                                                 content):
        e = _anon_edge(tmp_path, monkeypatch)
        (tmp_path / ".run").mkdir()
        (tmp_path / ".run" / "clients.json").write_text(content)
        _stub_upstream(e, {})
        with TestClient(e.app) as c:
            assert c.post(CHAT, json={"messages": []}).status_code == 401

    def test_no_file_at_all_still_honors_the_opt_in(self, tmp_path,
                                                    monkeypatch):
        # Negative control: the documented migration mode is untouched.
        e = _anon_edge(tmp_path, monkeypatch)
        _stub_upstream(e, {})
        with TestClient(e.app) as c:
            assert c.post(CHAT, json={"messages": []}).status_code == 200
            assert c.get("/gate/health",
                         headers=_local_headers(e)).json()["auth"] == "anon"


class TestIntrospectionIsPerDevice:
    """S6: a device key reads its own series. Only the rig-local token sees
    every device, the denial counters and the roster."""

    def _traffic(self, c):
        for key in (DEVICE_KEY, REVOKED_KEY):       # "laptop" and "stolen"
            assert c.post(CHAT, json={"messages": []}, headers={
                "Authorization": f"Bearer {key}"}).status_code == 200
        c.post(CHAT, json={"messages": []},
               headers={"Authorization": "Bearer nope"})

    def test_device_key_sees_only_its_own_series(self, edge, tmp_path):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            self._traffic(c)
            body = c.get("/gate/metrics", headers=HDR).text
        assert 'openbeast_edge_requests_total{device="laptop"' in body
        assert 'openbeast_edge_prompt_tokens_total{device="laptop"} 10' in body
        assert "stolen" not in body
        assert "openbeast_edge_denied_total" not in body

    def test_local_token_keeps_the_full_view(self, edge, tmp_path):
        # Negative control: rig tooling loses nothing.
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            self._traffic(c)
            body = c.get("/gate/metrics", headers=_local_headers(edge)).text
        for dev in ("laptop", "stolen"):
            assert f'openbeast_edge_prompt_tokens_total{{device="{dev}"}}' in body
        assert 'openbeast_edge_denied_total{reason="bad_key"} 1' in body

    def test_device_key_gets_no_roster_from_health(self, edge, tmp_path):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            remote = c.get("/gate/health", headers=HDR).json()
            local = c.get("/gate/health", headers=_local_headers(edge)).json()
        assert remote == {"status": "ok", "service": "beast-gate"}
        assert local["devices"] == 2 and "upstream" in local


class TestSanitizeOffTheLoop:
    """S3 (perf F10): a large body is parsed in a worker thread, and the
    default body cap is single-digit megabytes."""

    def _where(self, edge, monkeypatch):
        """Record whether each _sanitize_body call ran on the event loop."""
        seen = []
        real = edge._sanitize_body

        def spy(raw, device, path):
            try:
                asyncio.get_running_loop()
                seen.append("loop")
            except RuntimeError:
                seen.append("thread")
            return real(raw, device, path)

        monkeypatch.setattr(edge, "_sanitize_body", spy)
        return seen

    def test_large_body_is_sanitized_in_a_thread(self, edge, tmp_path,
                                                 monkeypatch):
        _registry(tmp_path)
        captured = {}
        _stub_upstream(edge, captured)
        seen = self._where(edge, monkeypatch)
        big = {"messages": [{"role": "user", "content": "x" * 70000}],
               "id_slot": 3}
        with TestClient(edge.app) as c:
            assert c.post(CHAT, json=big, headers=HDR).status_code == 200
            # Still sanitized, just elsewhere: the tenancy strip held.
            assert "id_slot" not in json.loads(captured["content"])
            # A refusal raised in the thread is still a 400, not a 500.
            deep = b"[" * 70000
            r = c.post(CHAT, content=deep, headers=HDR)
            assert r.status_code == 400 and "nests deeper" in r.text
        assert seen == ["thread", "thread"]

    def test_small_body_stays_inline(self, edge, tmp_path, monkeypatch):
        # Negative control for the spy, and the fast path is kept.
        _registry(tmp_path)
        _stub_upstream(edge, {})
        seen = self._where(edge, monkeypatch)
        with TestClient(edge.app) as c:
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 200
        assert seen == ["loop"]

    def test_default_cap_is_8_mib_and_stays_configurable(self, edge, tmp_path,
                                                         monkeypatch):
        monkeypatch.delenv("OPENBEAST_EDGE_MAX_BODY", raising=False)
        importlib.reload(edge)
        assert edge.MAX_BODY_BYTES == 8 * 1024 * 1024
        monkeypatch.setenv("OPENBEAST_EDGE_MAX_BODY", "2048")
        importlib.reload(edge)
        assert edge.MAX_BODY_BYTES == 2048
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            pad = {"messages": [{"role": "user", "content": "x" * 4096}]}
            assert c.post(CHAT, json=pad, headers=HDR).status_code == 413
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 200


def _part(ptype, **media):
    return {"messages": [
        {"role": "system", "content": "plain string content"},
        {"role": "user", "content": [
            {"type": "text", "text": "what is this? http://example.com"},
            {"type": ptype, ptype: media}]}]}


PNG = "data:image/png;base64,iVBORw0KGgo="


class TestMediaUrlsStayInline:
    """S4: llama-server fetches any media reference a chat part names, from
    the rig's loopback. The gate forwards inline media only."""

    @pytest.mark.parametrize("body", [
        _part("image_url", url="http://127.0.0.1:3000/api/config"),
        _part("image_url", url="https://169.254.169.254/latest/meta-data"),
        _part("image_url", url="HTTP://10.0.0.1/"),
        _part("image_url", url="file:///etc/passwd"),
        _part("image_url", url="httpd"),              # llama tests "http" only
        _part("input_audio", data="http://127.0.0.1:8888/"),
        _part("input_audio", url="http://127.0.0.1:8888/"),
        # `data` is read first upstream, but never trust which one wins.
        _part("input_video", data="AAAA", url="http://127.0.0.1:3001/"),
        {"messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": "http://127.0.0.1:3000/"}]}]},
    ])
    def test_media_references_never_reach_upstream(self, edge, tmp_path, body):
        _registry(tmp_path)
        captured = {}
        _stub_upstream(edge, captured)
        with TestClient(edge.app) as c:
            r = c.post(CHAT, json=body, headers=HDR)
        assert r.status_code == 400, r.text
        assert "OPENBEAST_EDGE_ALLOW_MEDIA_URLS" in r.text
        assert captured == {}, "the body was forwarded"
        assert _last_audit(tmp_path)["outcome"] == "bad_request"

    @pytest.mark.parametrize("body", [
        _part("image_url", url=PNG),
        _part("input_audio", data="UklGRiQAAABXQVZF", format="wav"),
        _part("input_video", data="data:video/mp4;base64,AAAA"),
        # A URL in TEXT is just text, and odd shapes are llama-server's to
        # refuse — the gate must not 500 on them.
        {"messages": [{"role": "user", "content": "see http://127.0.0.1/"}]},
        {"messages": [{"role": "user", "content": [
            "str", 5, {"type": ["image_url"]}, {"type": "image_url"},
            {"type": "image_url", "image_url": {"url": None}}]}, "x", None]},
        {"messages": "http://127.0.0.1/"},
    ])
    def test_inline_media_and_plain_text_pass(self, edge, tmp_path, body):
        _registry(tmp_path)
        captured = {}
        _stub_upstream(edge, captured)
        with TestClient(edge.app) as c:
            assert c.post(CHAT, json=body, headers=HDR).status_code == 200
        assert json.loads(captured["content"])["messages"] == body["messages"]

    def test_operator_opt_out_forwards_urls(self, edge, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENBEAST_EDGE_ALLOW_MEDIA_URLS", "true")
        importlib.reload(edge)
        _registry(tmp_path)
        captured = {}
        _stub_upstream(edge, captured)
        body = _part("image_url", url="https://example.com/cat.png")
        with TestClient(edge.app) as c:
            assert c.post(CHAT, json=body, headers=HDR).status_code == 200
        assert "example.com/cat.png" in captured["content"].decode()
