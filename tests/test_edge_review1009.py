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
import test_edge as _te  # noqa: E402
from test_edge import (DEVICE_KEY, REVOKED_KEY, _FakeResponse,  # noqa: E402
                       _last_audit, _laptop_bucket_app, _local_headers,
                       _registry, _stub_upstream)

edge = _te.edge          # the fixture: a fresh module on a scratch REPO_DIR

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


def _scope(app, path=CHAT, method="POST", key=DEVICE_KEY):
    return {"type": "http", "method": method, "app": app, "path": path,
            "raw_path": b"", "query_string": b"", "root_path": "",
            "scheme": "http", "server": ("127.0.0.1", 8090),
            "client": ("127.0.0.1", 1),
            "headers": [(b"authorization", f"Bearer {key}".encode()),
                        (b"content-type", b"application/json")]}


def _direct_app(edge, client):
    """edge.app wired for handler-level tests (no TestClient, one loop)."""
    app = edge.app
    app.state.registry = edge.Registry()
    app.state.limiter = edge.Limiter()
    app.state.client = client
    return app


class _HealthClient:
    def __init__(self, fail=False, delay=0.0):
        self.hits, self.fail, self.delay = 0, fail, delay

    async def get(self, url, timeout=None):
        self.hits += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            import httpx
            raise httpx.ConnectError("refused")

        class R:
            status_code = 200
            text = '{"status":"ok"}'
        return R()

    async def aclose(self):
        pass


class TestUpstreamHealthIsCached:
    """S8: /health is exempt from the rate limit, so the gate must not turn
    every call into an upstream request."""

    def _serve(self, edge, client):
        @edge.asynccontextmanager
        async def _lifespan(a):
            a.state.client = client
            a.state.registry = edge.Registry()
            a.state.limiter = edge.Limiter()
            yield
        edge.app.router.lifespan_context = _lifespan

    @pytest.mark.parametrize("fail,code", [(False, 200), (True, 502)])
    def test_a_burst_is_one_upstream_request(self, edge, tmp_path,
                                             monkeypatch, fail, code):
        _registry(tmp_path, rate=2)
        up = _HealthClient(fail=fail)
        self._serve(edge, up)
        now = [1000.0]
        monkeypatch.setattr(edge, "_clock", lambda: now[0])
        with TestClient(edge.app) as c:
            for _ in range(50):
                assert c.get("/health", headers=HDR).status_code == code
            assert up.hits == 1
            # Negative control: the answer is not pinned forever.
            now[0] += edge._HEALTH_TTL_S + 0.01
            assert c.get("/health", headers=HDR).status_code == code
            assert up.hits == 2

    def test_concurrent_callers_share_one_probe(self, edge, tmp_path):
        _registry(tmp_path)
        up = _HealthClient(delay=0.05)
        app = _direct_app(edge, up)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def run():
            return await asyncio.gather(*[
                edge.gate(Request(_scope(app, "/health", "GET"), receive))
                for _ in range(20)])

        assert {r.status_code for r in asyncio.run(run())} == {200}
        assert up.hits == 1


class _SlowUpstream:
    """Upstream whose headers arrive after `delay` (None = never)."""

    def __init__(self, delay=None, response=None):
        self.delay, self.response = delay, response or _FakeResponse()
        self.cancelled = self.sent = 0

    def build_request(self, *a, **kw):
        return object()

    async def send(self, req, stream=False):
        self.sent += 1
        try:
            await asyncio.sleep(3600 if self.delay is None else self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return self.response


def _receive(payload: bytes, hangup_after=None):
    """ASGI receive: the body once, then a live socket — or, `hangup_after`
    seconds later, http.disconnect (what uvicorn delivers on a hang-up)."""
    state = {"sent": False, "t0": None}

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            state["t0"] = asyncio.get_running_loop().time()
            return {"type": "http.request", "body": payload,
                    "more_body": False}
        if hangup_after is None:
            await asyncio.Event().wait()
        left = state["t0"] + hangup_after - asyncio.get_running_loop().time()
        if left > 0:
            await asyncio.sleep(left)
        return {"type": "http.disconnect"}

    return receive


PAYLOAD = json.dumps({"messages": [], "model": "m"}).encode()


class TestDisconnectBeforeUpstreamHeaders:
    """ops F2: a caller that hangs up while the gate waits for upstream
    headers must free its in-flight unit and stop the upstream work."""

    def test_hangup_cancels_upstream_and_frees_the_slot(self, edge, tmp_path,
                                                        monkeypatch):
        monkeypatch.setattr(edge, "_DISCONNECT_POLL_S", 0.01)
        _registry(tmp_path)
        up = _SlowUpstream()                       # never answers
        app = _direct_app(edge, up)

        async def run():
            return await asyncio.wait_for(edge.gate(Request(
                _scope(app), _receive(PAYLOAD, hangup_after=0.05))), 5)

        resp = asyncio.run(run())
        assert resp.status_code == 499
        assert (up.sent, up.cancelled) == (1, 1)
        assert _laptop_bucket_app(edge, app).inflight == 0
        row = _last_audit(tmp_path)
        assert (row["outcome"], row["status"]) == ("client_disconnect", 499)
        assert row["model"] == "m"

    def test_abandoned_requests_do_not_wedge_the_device(self, edge, tmp_path,
                                                        monkeypatch):
        # The reviewer's scenario: two give-ups filled EDGE_MAX_INFLIGHT=2
        # and the retry got 429 until the abandoned generations finished.
        monkeypatch.setattr(edge, "_DISCONNECT_POLL_S", 0.01)
        _registry(tmp_path)
        up = _SlowUpstream()
        app = _direct_app(edge, up)

        async def run():
            for _ in range(2):
                await asyncio.wait_for(edge.gate(Request(
                    _scope(app), _receive(PAYLOAD, hangup_after=0.03))), 5)
            up.delay = 0
            return await edge.gate(Request(_scope(app), _receive(PAYLOAD)))

        assert asyncio.run(run()).status_code == 200

    def test_patient_caller_still_gets_a_slow_answer(self, edge, tmp_path,
                                                     monkeypatch):
        # Negative control: slower than several polls, socket still open.
        monkeypatch.setattr(edge, "_DISCONNECT_POLL_S", 0.01)
        _registry(tmp_path)
        up = _SlowUpstream(delay=0.1)
        app = _direct_app(edge, up)

        async def run():
            resp = await edge.gate(Request(_scope(app), _receive(PAYLOAD)))
            async for _ in resp.body_iterator:
                pass
            await resp.background()
            return resp

        assert asyncio.run(run()).status_code == 200
        assert up.cancelled == 0
        row = _last_audit(tmp_path)
        assert (row["outcome"], row["prompt_tokens"]) == ("ok", 10)
        assert _laptop_bucket_app(edge, app).inflight == 0

    def test_response_that_wins_the_race_is_closed(self, edge):
        # The hang-up is noticed just as headers land: cancel() no-ops on a
        # finished send, and its response must not leak an upstream socket.
        closed = []

        class _R:
            async def aclose(self):
                closed.append(1)

        async def run():
            async def send():
                return _R()
            task = asyncio.ensure_future(send())
            await asyncio.sleep(0)
            await edge._abandon(task)

        asyncio.run(run())
        assert closed == [1]


def _revoke(path, device_id="laptop"):
    """What `clients.sh revoke <id>` writes."""
    data = json.loads(path.read_text())
    for d in data["devices"]:
        if d["id"] == device_id:
            d["revoked_at"] = "2026-10-09T00:00:00Z"
    path.write_text(json.dumps(data))


class _Stream(_FakeResponse):
    """SSE upstream that runs `hook(i)` before chunk i and records aclose."""

    def __init__(self, n=6, hook=None):
        super().__init__()
        self.n, self.hook, self.closed = n, hook, 0

    async def aiter_raw(self):
        for i in range(self.n):
            if self.hook:
                self.hook(i)
            yield b'data: {"choices":[{"delta":{"content":"t"}}]}\n\n'

    async def aclose(self):
        self.closed += 1


STREAM = json.dumps({"messages": [], "stream": True}).encode()


class TestRevocationReachesOpenRequests:
    """S9: `clients.sh revoke` ends a generation that is already running,
    instead of only refusing the device's next request."""

    def _run(self, edge, app, payload=STREAM):
        async def run():
            resp = await asyncio.wait_for(
                edge.gate(Request(_scope(app), _receive(payload))), 5)
            chunks = []
            if hasattr(resp, "body_iterator"):
                async for c in resp.body_iterator:
                    chunks.append(c)
                await resp.background()
            return resp, chunks
        return asyncio.run(run())

    def test_revoke_mid_stream_closes_the_upstream(self, edge, tmp_path,
                                                   monkeypatch):
        monkeypatch.setattr(edge, "_REAUTH_INTERVAL_S", 0)
        path = _registry(tmp_path)
        stream = _Stream(hook=lambda i: i == 2 and _revoke(path))
        app = _direct_app(edge, _SlowUpstream(delay=0, response=stream))
        resp, chunks = self._run(edge, app)
        assert resp.status_code == 200          # headers were already sent
        assert len(chunks) == 2                 # nothing after the revoke
        assert stream.closed >= 1
        assert _laptop_bucket_app(edge, app).inflight == 0
        row = _last_audit(tmp_path)
        assert (row["outcome"], row["device"]) == ("revoked", "laptop")

    def test_enrolled_device_streams_to_the_end(self, edge, tmp_path,
                                                monkeypatch):
        # Negative control: re-checked on every chunk, never cut. Revoking
        # a DIFFERENT device mid-stream must not touch this one either.
        monkeypatch.setattr(edge, "_REAUTH_INTERVAL_S", 0)
        path = _registry(tmp_path)
        stream = _Stream(hook=lambda i: i == 2 and _revoke(path, "stolen"))
        app = _direct_app(edge, _SlowUpstream(delay=0, response=stream))
        _, chunks = self._run(edge, app)
        assert len(chunks) == 6
        assert _last_audit(tmp_path)["outcome"] == "ok"

    def test_check_is_throttled_to_the_interval(self, edge, tmp_path,
                                                monkeypatch):
        # At the shipped 5 s interval a fast stream is not re-checked per
        # chunk; the clock crossing the interval is what triggers it.
        now = [1000.0]
        monkeypatch.setattr(edge, "_clock", lambda: now[0])
        path = _registry(tmp_path)

        def hook(i):
            if i == 1:
                _revoke(path)
            if i == 4:
                now[0] += edge._REAUTH_INTERVAL_S
        stream = _Stream(hook=hook)
        app = _direct_app(edge, _SlowUpstream(delay=0, response=stream))
        _, chunks = self._run(edge, app)
        assert len(chunks) == 4
        assert _last_audit(tmp_path)["outcome"] == "revoked"

    def test_revoke_while_waiting_for_headers(self, edge, tmp_path,
                                              monkeypatch):
        # A non-streaming generation has no chunks to check on.
        monkeypatch.setattr(edge, "_REAUTH_INTERVAL_S", 0)
        monkeypatch.setattr(edge, "_DISCONNECT_POLL_S", 0.01)
        path = _registry(tmp_path)
        _revoked = []
        up = _SlowUpstream()                    # never answers
        app = _direct_app(edge, up)
        real = edge._identify

        def identify(request, registry):
            # Admit the request, then revoke before the first re-check.
            if _revoked:
                return real(request, registry)
            _revoked.append(1)
            out = real(request, registry)
            _revoke(path)
            return out

        monkeypatch.setattr(edge, "_identify", identify)
        resp, _ = self._run(edge, app, PAYLOAD)
        assert resp.status_code == 401 and b"revoked" in resp.body
        assert (up.sent, up.cancelled) == (1, 1)
        assert _laptop_bucket_app(edge, app).inflight == 0
        row = _last_audit(tmp_path)
        assert (row["outcome"], row["status"]) == ("revoked", 401)
