#!/usr/bin/env python3
"""beast-gate (agents/edge.py) — the 2026-11-15 adversarial-review findings.

One class per finding. Each builds its own registry and stub upstream under
tmp_path (the helpers are test_edge.py's); nothing here opens a real port.

Run: python3 -m pytest tests/test_edge_review1115.py -q
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys

from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_edge as _te  # noqa: E402
from test_edge import (DEVICE_KEY, _FakeResponse,  # noqa: E402
                       _laptop_bucket_app, _local_headers, _registry)

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


def _stub(e, *, health_status=200, health_text='{"status":"ok"}',
          health_exc=None, resp=None):
    """A stub upstream whose /health and completion reply are configurable."""
    class _Client:
        def build_request(self, method, url, content=None, headers=None):
            return object()

        async def send(self, req, stream=False):
            return resp() if resp else _FakeResponse()

        async def get(self, url, timeout=None):
            if health_exc:
                raise health_exc

            class R:
                status_code = health_status
                text = health_text
            return R()

        async def aclose(self):
            pass

    @e.asynccontextmanager
    async def _lifespan(a):
        a.state.client = _Client()
        a.state.registry = e.Registry()
        a.state.limiter = e.Limiter()
        yield

    e.app.router.lifespan_context = _lifespan


# A real engine /health body (tensorfold on spark-1, values illustrative).
_RICH_HEALTH = json.dumps({
    "backend": "tensorfold", "requests_total": 53,
    "prompt_tokens_total": 499347, "completion_tokens_total": 4137,
    "cached_tokens_total": 427136, "kept_prompts": 32,
    "streams": {"decoding": 0}})


class TestHealthIsNormalized:
    """sec: the proxied /health relayed the upstream's JSON verbatim, handing
    every enrolled device rig-wide cross-tenant usage counters."""

    def test_rich_upstream_body_is_not_echoed(self, edge, tmp_path):
        _registry(tmp_path)
        _stub(edge, health_text=_RICH_HEALTH)
        with TestClient(edge.app) as c:
            r = c.get("/health", headers=HDR)
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        for leak in ("requests_total", "cached_tokens_total", "tensorfold",
                     "streams", "kept_prompts"):
            assert leak not in r.text

    def test_loading_upstream_keeps_its_status_but_not_its_body(self, edge,
                                                                 tmp_path):
        # 503 "still loading" is what setup-client.sh tells apart from 502.
        _registry(tmp_path)
        _stub(edge, health_status=503,
              health_text='{"error":{"code":503,"message":"Loading model",'
                          '"requests_total":7}}')
        with TestClient(edge.app) as c:
            r = c.get("/health", headers=HDR)
        assert r.status_code == 503
        assert "requests_total" not in r.text and "Loading" not in r.text
        # client.sh greps the body for "ok": a not-ready rig must not match.
        assert "ok" not in r.text

    def test_unreachable_upstream_does_not_name_it(self, edge, tmp_path):
        _registry(tmp_path)
        _stub(edge, health_exc=edge.httpx.ConnectError(
            "All connection attempts failed for http://127.0.0.1:8000"))
        with TestClient(edge.app) as c:
            r = c.get("/health", headers=HDR)
        assert r.status_code == 502
        assert "127.0.0.1" not in r.text and "ok" not in r.text


NEW_KEY = "c" * 64


def _reenroll(tmp_path, enrolled_at="2026-08-15T09:00:00Z", nonce=None):
    """What `clients.sh remove laptop --yes && enroll laptop` leaves behind:
    the same id, a new key, a new enrollment."""
    dev = {"id": "laptop", "label": "New holder",
           "key_sha256": hashlib.sha256(NEW_KEY.encode()).hexdigest(),
           "enrolled_at": enrolled_at, "revoked_at": None}
    if nonce:
        dev["enroll_nonce"] = nonce
    (tmp_path / ".run" / "clients.json").write_text(
        json.dumps({"version": 1, "devices": [dev]}))


class TestReenrolledIdIsAFreshDevice:
    """sec + cbp: per-device metrics were keyed by the reusable id, so a
    re-enrolled id read its predecessor's usage; same-second re-enrolls
    shared a device_uid; last-seen throttling followed the id."""

    def test_new_holder_does_not_read_predecessor_metrics(self, edge,
                                                          tmp_path):
        _registry(tmp_path)
        _stub(edge)
        with TestClient(edge.app) as c:
            assert c.post(CHAT, json={"messages": []},
                          headers=HDR).status_code == 200
            _reenroll(tmp_path)
            new = {"Authorization": f"Bearer {NEW_KEY}"}
            mine = c.get("/gate/metrics", headers=new).text
            rig = c.get("/gate/metrics", headers=_local_headers(edge)).text
            # The new holder's own traffic does show up for it.
            c.post(CHAT, json={"messages": []}, headers=new)
            after = c.get("/gate/metrics", headers=new).text
        assert 'openbeast_edge_prompt_tokens_total{device="laptop"}' not in mine
        assert "openbeast_edge_requests_total{" not in mine
        # The rig still sees the old enrollment's usage, under the id.
        assert 'openbeast_edge_prompt_tokens_total{device="laptop"} 10' in rig
        assert 'openbeast_edge_prompt_tokens_total{device="laptop"} 10' in after

    def test_rig_view_sums_enrollments_without_duplicate_series(self, edge,
                                                                tmp_path):
        _registry(tmp_path)
        _stub(edge)
        with TestClient(edge.app) as c:
            c.post(CHAT, json={"messages": []}, headers=HDR)
            _reenroll(tmp_path)
            c.post(CHAT, json={"messages": []},
                   headers={"Authorization": f"Bearer {NEW_KEY}"})
            rig = c.get("/gate/metrics", headers=_local_headers(edge)).text
        lines = [ln for ln in rig.splitlines()
                 if ln.startswith('openbeast_edge_prompt_tokens_total{')]
        assert lines == [
            'openbeast_edge_prompt_tokens_total{device="laptop"} 20']

    def test_same_second_reenroll_gets_a_distinct_uid(self, edge):
        ts = "2026-08-15T09:00:00Z"
        a = edge.device_uid({"id": "laptop", "enrolled_at": ts,
                             "enroll_nonce": "1111"})
        b = edge.device_uid({"id": "laptop", "enrolled_at": ts,
                             "enroll_nonce": "2222"})
        assert a != b
        # Rows written before the nonce keep the uid they always had.
        assert edge.device_uid({"id": "laptop", "enrolled_at": ts}) == \
            hashlib.sha256(f"laptop:{ts}".encode()).hexdigest()[:12]

    def test_reenrolled_id_records_last_seen_at_once(self, edge, tmp_path):
        _registry(tmp_path)
        _stub(edge)
        seen = tmp_path / ".run" / "clients-lastseen.json"
        with TestClient(edge.app) as c:
            c.post(CHAT, json={"messages": []}, headers=HDR)
            assert seen.exists()
            seen.unlink()                 # what `clients.sh remove` prunes
            _reenroll(tmp_path)
            c.post(CHAT, json={"messages": []},
                   headers={"Authorization": f"Bearer {NEW_KEY}"})
        assert seen.exists() and "laptop" in json.loads(seen.read_text()), \
            "the new enrollment's first use was throttled away"


class _CorsResponse(_FakeResponse):
    """llama-server reflects the caller's Origin back."""

    def __init__(self):
        super().__init__()
        self.headers = {"content-type": "application/json",
                        "access-control-allow-origin": "https://evil.example",
                        "Access-Control-Allow-Credentials": "true"}


class TestBrowserPagesCannotDriveAnon:
    """sec: in EDGE_ALLOW_ANON mode a web page's preflight-free text/plain
    POST was served, and the relayed ACAO header let it read the reply."""

    def _post(self, e, **headers):
        _stub(e, resp=_CorsResponse)
        with TestClient(e.app) as c:
            return c.post(CHAT, content=b'{"messages":[]}', headers=headers)

    def test_cross_site_text_plain_post_is_refused(self, tmp_path,
                                                    monkeypatch):
        e = _anon_edge(tmp_path, monkeypatch)
        r = self._post(e, **{"Content-Type": "text/plain",
                             "Origin": "https://evil.example"})
        assert r.status_code == 403, r.text

    def test_sec_fetch_site_cross_site_is_refused(self, tmp_path,
                                                   monkeypatch):
        e = _anon_edge(tmp_path, monkeypatch)
        r = self._post(e, **{"Content-Type": "text/plain",
                             "Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403

    def test_non_browser_anon_caller_still_served(self, tmp_path,
                                                  monkeypatch):
        # Negative control: curl / an SDK sends neither header.
        e = _anon_edge(tmp_path, monkeypatch)
        r = self._post(e, **{"Content-Type": "application/json"})
        assert r.status_code == 200

    def test_access_control_headers_are_not_relayed(self, edge, tmp_path):
        _registry(tmp_path)
        r = self._post(edge, Authorization=f"Bearer {DEVICE_KEY}")
        assert r.status_code == 200
        assert not [k for k in r.headers
                    if k.lower().startswith("access-control-")]
