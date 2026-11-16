#!/usr/bin/env python3
"""beast-gate (agents/edge.py) — the 2026-11-16 review findings.

One class per finding. Each builds its own registry and stub upstream under
tmp_path (the helpers are test_edge.py's); nothing here opens a real port.

Run: python3 -m pytest tests/test_edge_review1116.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_edge as _te  # noqa: E402
from test_edge import (DEVICE_KEY, _last_audit, _registry,  # noqa: E402
                       _stub_upstream)

edge = _te.edge          # the fixture: a fresh module on a scratch REPO_DIR

HDR = {"Authorization": f"Bearer {DEVICE_KEY}"}
CHAT = "/v1/chat/completions"


class TestAuditModelIsBounded:
    """The client's `model` went into every audit row verbatim, refused
    requests included: a 7 MiB model string on a fan-out body was refused
    with 400 before anything went upstream and still appended 7 MiB."""

    def test_a_huge_model_on_a_refused_body_does_not_bloat_the_audit(
            self, edge, tmp_path):
        _registry(tmp_path)
        captured = {}
        _stub_upstream(edge, captured)
        body = {"model": "A" * (1024 * 1024), "n": 99, "messages": []}
        with TestClient(edge.app) as c:
            r = c.post(CHAT, json=body, headers=HDR)
        assert r.status_code == 400, r.text
        assert captured == {}, "a fan-out body must not be forwarded"
        audit = tmp_path / ".run" / "inference-audit.jsonl"
        assert audit.stat().st_size < 4096
        row = _last_audit(tmp_path)
        assert row["model"] == "A" * edge._AUDIT_MODEL_CHARS

    def test_the_forwarded_body_keeps_the_model_as_sent(self, edge, tmp_path):
        _registry(tmp_path)
        captured = {}
        _stub_upstream(edge, captured)
        name = "m" * 300
        with TestClient(edge.app) as c:
            r = c.post(CHAT, json={"model": name, "messages": []}, headers=HDR)
        assert r.status_code == 200, r.text
        assert json.loads(captured["content"])["model"] == name
        assert _last_audit(tmp_path)["model"] == name[:128]

    @pytest.mark.parametrize("model", [{"x": "y" * 5000}, ["a"] * 500, 7, True,
                                       None, ""])
    def test_a_non_string_model_is_not_recorded(self, edge, model):
        out = edge._sanitize_body(
            json.dumps({"model": model, "messages": []}).encode(),
            {"slot": None})
        assert out[1] is None

    def test_a_normal_model_name_is_recorded_whole(self, edge):
        out = edge._sanitize_body(b'{"model":"qwen3.8-27b","messages":[]}',
                                  {"slot": None})
        assert out[1] == "qwen3.8-27b"


class TestForwardedBodyStaysUtf8:
    """json.dumps' default escaped every non-ASCII character, so a CJK body
    reached upstream 2x larger and an emoji body 3x."""

    @pytest.mark.parametrize("text", ["中文" * 5000, "😀" * 5000,
                                      "naïve café — ok"])
    def test_non_ascii_is_forwarded_at_its_own_size(self, edge, text):
        raw = json.dumps({"model": "m", "messages": [
            {"role": "user", "content": text}]}, ensure_ascii=False).encode()
        out = edge._sanitize_body(raw, {"slot": None})[0]
        assert len(out) <= len(raw) + 16
        assert json.loads(out)["messages"][0]["content"] == text

    def test_a_lone_surrogate_still_forwards_as_escaped_json(self, edge):
        raw = b'{"model":"m","messages":[{"role":"user","content":"a\\ud800b"}]}'
        out = edge._sanitize_body(raw, {"slot": None})[0]
        assert b"\\ud800" in out
        assert json.loads(out)["messages"][0]["content"] == "a\ud800b"


class TestBigSanitizeDoesNotStallTheLoop:
    """asyncio.to_thread does not stop C calls that hold the GIL (a regex
    match over one huge string, json.loads/dumps) from pausing the loop."""

    def test_the_depth_scan_skips_a_body_with_few_brackets(self, edge,
                                                           monkeypatch):
        calls = []

        class _Spy:
            def finditer(self, raw):
                calls.append(len(raw))
                return iter(())

        monkeypatch.setattr(edge, "_JSON_TOKEN", _Spy())
        raw = json.dumps({"messages": [{"content": "a" * 100_000}]}).encode()
        assert edge._json_too_deep(raw) is False
        assert calls == [], "the per-token scan ran on a body that cannot nest"

    @pytest.mark.parametrize("raw,deep", [
        (b"[" * 65 + b"]" * 65, True),
        (b"[" * 64 + b"]" * 64, False),
        # brackets inside strings over-count, so the full scan decides
        (b'{"a":"' + b"[" * 200 + b'"}', False),
        (b'{"a":' + b"[" * 70 + b"]" * 70 + b"}", True),
    ])
    def test_the_fast_path_never_changes_the_answer(self, edge, raw, deep):
        assert edge._json_too_deep(raw) is deep

    def test_big_body_sanitizes_run_one_at_a_time(self, edge, tmp_path,
                                                  monkeypatch):
        """Two big bodies from two in-flight requests: the threaded sanitize
        never overlaps (it would only queue on the GIL and stack pauses)."""
        _registry(tmp_path)
        _stub_upstream(edge, {})
        real = edge._sanitize_body
        lock = threading.Lock()
        state = {"now": 0, "peak": 0}

        def _slow(raw, device, path="/v1/chat/completions"):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.15)
            try:
                return real(raw, device, path)
            finally:
                with lock:
                    state["now"] -= 1

        monkeypatch.setattr(edge, "_sanitize_body", _slow)
        big = {"model": "m", "messages": [{"role": "user",
                                           "content": "x" * 64_000}]}

        async def _two(c):
            loop = asyncio.get_running_loop()
            return await asyncio.gather(*[
                loop.run_in_executor(None, lambda: c.post(CHAT, json=big,
                                                          headers=HDR))
                for _ in range(2)])

        with TestClient(edge.app) as c:
            rs = asyncio.run(_two(c))
        assert [r.status_code for r in rs] == [200, 200]
        assert state["peak"] == 1
