#!/usr/bin/env python3
"""beast-gate (agents/edge.py) — the identity-aware inference edge.

These tests pin the security contract that makes the gate worth having:
fail-closed auth, a strict path allowlist, client tenancy knobs stripped,
per-device admission control, and an audit trail that records cost without
recording content or key material.

Run: python3 -m pytest tests/test_edge.py -q
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "agents"))

DEVICE_KEY = "a" * 64
REVOKED_KEY = "b" * 64


def _registry(tmp_path, *, slot=None, rate=None, revoked=False):
    run = tmp_path / ".run"
    run.mkdir(exist_ok=True)
    reg = {
        "version": 1,
        "devices": [
            {"id": "laptop", "label": "Test laptop",
             "key_sha256": hashlib.sha256(DEVICE_KEY.encode()).hexdigest(),
             "enrolled_at": "2026-07-30T00:00:00Z", "revoked_at": None,
             "slot": slot, "rate_limit_per_min": rate, "last_seen": None},
            {"id": "stolen", "label": "Lost laptop",
             "key_sha256": hashlib.sha256(REVOKED_KEY.encode()).hexdigest(),
             "enrolled_at": "2026-07-30T00:00:00Z",
             "revoked_at": "2026-07-30T01:00:00Z" if revoked else None,
             "slot": None, "rate_limit_per_min": None, "last_seen": None},
        ],
    }
    (run / "clients.json").write_text(json.dumps(reg))
    return run / "clients.json"


@pytest.fixture
def edge(tmp_path, monkeypatch):
    """Fresh module bound to a scratch REPO_DIR (module-level config).

    Starlette's TestClient reports the peer as "testclient", not 127.0.0.1,
    so treat it as loopback by default — that matches how these routes are
    reached in production by start.sh/healthcheck/doctor. Tests that mean
    "a remote tailnet caller" override _is_loopback explicitly.
    """
    monkeypatch.setenv("OPENBEAST_REPO_DIR", str(tmp_path))
    monkeypatch.delenv("OPENBEAST_EDGE_ALLOW_ANON", raising=False)
    monkeypatch.delenv("OPENBEAST_API_KEY", raising=False)
    monkeypatch.setenv("OPENBEAST_EDGE_RATE_LIMIT", "120")
    monkeypatch.setenv("OPENBEAST_EDGE_MAX_INFLIGHT", "2")
    import edge as _edge
    importlib.reload(_edge)
    _real_peer = _edge._peer
    _edge._is_loopback = lambda r: _real_peer(r) in (
        "127.0.0.1", "::1", "localhost", "testclient")
    return _edge


class _FakeResponse:
    def __init__(self, status=200, body=b'{"usage":{"prompt_tokens":10,'
                                        b'"completion_tokens":5}}'):
        self.status_code = status
        self.headers = {"content-type": "application/json"}
        self._body = body

    async def aiter_raw(self):
        yield self._body

    async def aclose(self):
        pass


def _stub_upstream(edge, captured):
    """Capture what the gate would send upstream; never make a real call."""
    class _Client:
        def build_request(self, method, url, content=None, headers=None):
            captured["method"] = method
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers or {}
            return object()

        async def send(self, req, stream=False):
            return _FakeResponse()

        async def get(self, url, timeout=None):
            class R:
                status_code = 200
                text = '{"status":"ok"}'
            return R()

        async def aclose(self):
            pass

    app = edge.app
    orig = edge._lifespan

    @edge.asynccontextmanager
    async def _lifespan(a):
        a.state.client = _Client()
        a.state.registry = edge.Registry()
        a.state.limiter = edge.Limiter()
        yield

    app.router.lifespan_context = _lifespan
    return orig


class TestAuthFailsClosed:
    def test_no_registry_is_closed_not_open(self, edge):
        # The 2026-07-17 RBAC lesson: absent config must never mean "open".
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            r = c.post("/v1/chat/completions", json={"messages": []})
        assert r.status_code == 401
        assert "no_registry" in r.text

    def test_anon_opt_in_allows(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENBEAST_REPO_DIR", str(tmp_path))
        monkeypatch.setenv("OPENBEAST_EDGE_ALLOW_ANON", "true")
        import edge as _edge
        importlib.reload(_edge)
        _stub_upstream(_edge, {})
        with TestClient(_edge.app) as c:
            r = c.post("/v1/chat/completions", json={"messages": []})
        assert r.status_code == 200

    def test_auth_matrix(self, edge, tmp_path):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            assert c.post("/v1/chat/completions",
                          json={"messages": []}).status_code == 401
            assert c.post("/v1/chat/completions", json={"messages": []},
                          headers={"Authorization": "Bearer wrong"}
                          ).status_code == 401
            assert c.post("/v1/chat/completions", json={"messages": []},
                          headers={"Authorization": f"Bearer {DEVICE_KEY}"}
                          ).status_code == 200

    def test_revoked_device_refused_without_restart(self, edge, tmp_path):
        path = _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            assert c.post("/v1/chat/completions", json={"messages": []},
                          headers={"Authorization": f"Bearer {REVOKED_KEY}"}
                          ).status_code == 200
            # Revoke on disk mid-flight; the gate hot-reloads on mtime.
            data = json.loads(path.read_text())
            for d in data["devices"]:
                if d["id"] == "stolen":
                    d["revoked_at"] = "2026-07-30T02:00:00Z"
            os.utime(path, (0, 0))          # force a different mtime
            path.write_text(json.dumps(data))
            assert c.post("/v1/chat/completions", json={"messages": []},
                          headers={"Authorization": f"Bearer {REVOKED_KEY}"}
                          ).status_code == 401

    def test_corrupt_registry_keeps_last_good_map(self, edge, tmp_path):
        path = _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            assert c.post("/v1/chat/completions", json={"messages": []},
                          headers={"Authorization": f"Bearer {DEVICE_KEY}"}
                          ).status_code == 200
            path.write_text("{ this is not json")
            os.utime(path, None)
            # Must not fall back to "no registry => 401 for everyone" churn
            # NOR open up; the last good map stands.
            assert c.post("/v1/chat/completions", json={"messages": []},
                          headers={"Authorization": f"Bearer {DEVICE_KEY}"}
                          ).status_code == 200


class TestPathAllowlist:
    @pytest.mark.parametrize("path", [
        "/lora-adapters",        # global model mutation
        "/slots",                # other tenants' metadata
        "/props",
        "/v1/stream/abc",        # read/cancel another generation
        "/infill",
        "/tokenize",
        "/apply-template",
        "/metrics",
    ])
    def test_dangerous_routes_are_404(self, edge, tmp_path, path):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            r = c.get(path, headers={"Authorization": f"Bearer {DEVICE_KEY}"})
        # 404 not 403: a remote caller learns nothing about what exists.
        assert r.status_code == 404

    def test_allowed_routes_pass(self, edge, tmp_path):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            for p in ("/v1/models", "/v1/chat/completions"):
                r = c.post(p, json={"messages": []},
                           headers={"Authorization": f"Bearer {DEVICE_KEY}"})
                assert r.status_code == 200, p


class TestTenancyKnobs:
    def test_client_id_slot_is_stripped(self, edge, tmp_path):
        _registry(tmp_path)
        cap = {}
        _stub_upstream(edge, cap)
        with TestClient(edge.app) as c:
            c.post("/v1/chat/completions",
                   json={"messages": [], "id_slot": 7},
                   headers={"Authorization": f"Bearer {DEVICE_KEY}"})
        sent = json.loads(cap["content"])
        # Unauthenticated upstream: wraps modulo slot count onto someone
        # else's slot AND jumps the deferred queue. Never forwarded.
        assert "id_slot" not in sent

    def test_server_side_affinity_is_injected(self, edge, tmp_path):
        _registry(tmp_path, slot=3)
        cap = {}
        _stub_upstream(edge, cap)
        with TestClient(edge.app) as c:
            c.post("/v1/chat/completions",
                   json={"messages": [], "id_slot": 7},
                   headers={"Authorization": f"Bearer {DEVICE_KEY}"})
        assert json.loads(cap["content"])["id_slot"] == 3

    def test_device_key_never_reaches_upstream(self, edge, tmp_path):
        _registry(tmp_path)
        cap = {}
        _stub_upstream(edge, cap)
        with TestClient(edge.app) as c:
            c.post("/v1/chat/completions", json={"messages": []},
                   headers={"Authorization": f"Bearer {DEVICE_KEY}"})
        assert DEVICE_KEY not in json.dumps(cap["headers"])

    def test_conversation_id_is_namespaced_per_device(self, edge, tmp_path):
        _registry(tmp_path)
        cap = {}
        _stub_upstream(edge, cap)
        with TestClient(edge.app) as c:
            c.post("/v1/chat/completions", json={"messages": []},
                   headers={"Authorization": f"Bearer {DEVICE_KEY}",
                            "X-Conversation-Id": "shared-guessable-id"})
        sent = cap["headers"].get("X-Conversation-Id")
        # Upstream keys stream sessions on this header with no ownership
        # check — an un-namespaced id lets one device cancel another's.
        assert sent and sent != "shared-guessable-id"


def _laptop_bucket(edge, client):
    """Buckets are keyed by ENROLLMENT uid, not the reusable device id."""
    uid = edge.device_uid({"id": "laptop", "enrolled_at": "2026-07-30T00:00:00Z"})
    return client.app.state.limiter._buckets[uid]


class TestSlotAccounting:
    """The in-flight counter is the device's admission budget: any path that
    increments without releasing wedges that device at 429 permanently."""

    def test_slot_released_after_normal_request(self, edge, tmp_path):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            for _ in range(6):
                r = c.post("/v1/chat/completions", json={"messages": []},
                           headers={"Authorization": f"Bearer {DEVICE_KEY}"})
                assert r.status_code == 200
            b = _laptop_bucket(edge, c)
        assert b.inflight == 0, "in-flight slot leaked across requests"

    def test_slot_released_when_upstream_errors(self, edge, tmp_path):
        _registry(tmp_path)
        cap = {}
        _stub_upstream(edge, cap)
        with TestClient(edge.app) as c:
            async def boom(req, stream=False):
                raise edge.httpx.ConnectError("upstream down")
            c.app.state.client.send = boom
            r = c.post("/v1/chat/completions", json={"messages": []},
                       headers={"Authorization": f"Bearer {DEVICE_KEY}"})
            assert r.status_code == 502
            assert _laptop_bucket(edge, c).inflight == 0

    def test_release_is_idempotent(self, edge):
        b = edge.Bucket(60, 2)
        b.reserve()
        b.release()
        b.release()
        assert b.inflight == 0      # never negative — that would grant extra

    def test_reserve_is_atomic_check_and_increment(self, edge):
        b = edge.Bucket(1000, 2)
        assert b.reserve() is None
        assert b.reserve() is None
        # Third concurrent request must be refused, not admitted-then-counted.
        assert b.reserve() == "max_inflight"
        assert b.inflight == 2


class TestRegistryFreshness:
    def test_enrolling_the_first_device_needs_no_restart(self, edge, tmp_path):
        # The gate boots with NO registry (start.sh prints "enroll a device"),
        # then the operator enrolls one. Without a reload on the configured
        # path, that device 401s forever.
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            assert c.post("/v1/chat/completions", json={"messages": []},
                          headers={"Authorization": f"Bearer {DEVICE_KEY}"}
                          ).status_code == 401
            _registry(tmp_path)          # enroll happens now
            assert c.post("/v1/chat/completions", json={"messages": []},
                          headers={"Authorization": f"Bearer {DEVICE_KEY}"}
                          ).status_code == 200

    def test_gate_never_writes_the_registry(self, edge, tmp_path):
        # The authorization source must not be rewritten by the data path:
        # an unlocked read-modify-write here can resurrect a revoked device.
        path = _registry(tmp_path)
        before = path.read_bytes()
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            for _ in range(5):
                c.post("/v1/chat/completions", json={"messages": []},
                       headers={"Authorization": f"Bearer {DEVICE_KEY}"})
        assert path.read_bytes() == before, "gate mutated clients.json"
        # last_seen lands in the gate-owned sidecar instead.
        seen = tmp_path / ".run" / "clients-lastseen.json"
        assert seen.exists() and "laptop" in json.loads(seen.read_text())

    def test_first_touch_writes_even_on_a_freshly_booted_host(self, edge, tmp_path):
        # Regression: the throttle used 0.0 as the "never touched" sentinel and
        # compared against time.monotonic(), which is time since BOOT. On a host
        # up for less than the interval (CI runners, a rig rebooting), the very
        # first last-seen write for every device was silently skipped.
        _registry(tmp_path)
        reg = edge.Registry(str(tmp_path / ".run" / "clients.json"))
        real_monotonic = edge.time.monotonic
        edge.time.monotonic = lambda: 3.0        # 3 seconds since boot
        try:
            reg.touch("laptop")
        finally:
            edge.time.monotonic = real_monotonic
        seen = tmp_path / ".run" / "clients-lastseen.json"
        assert seen.exists(), "first touch skipped on a freshly booted host"
        assert "laptop" in json.loads(seen.read_text())

    def test_touch_is_throttled_after_the_first(self, edge, tmp_path):
        _registry(tmp_path)
        reg = edge.Registry(str(tmp_path / ".run" / "clients.json"))
        reg.touch("laptop")
        seen = tmp_path / ".run" / "clients-lastseen.json"
        first = seen.read_text()
        seen.write_text('{"laptop": "sentinel"}')
        reg.touch("laptop")                       # immediately again
        assert seen.read_text() == '{"laptop": "sentinel"}', \
            "touch rewrote inside the throttle window (write amplification)"
        assert first                              # the first one did land

    def test_rate_limit_change_takes_effect_live(self, edge, tmp_path):
        path = _registry(tmp_path, rate=100)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            hdr = {"Authorization": f"Bearer {DEVICE_KEY}"}
            assert c.post("/v1/chat/completions", json={"messages": []},
                          headers=hdr).status_code == 200
            data = json.loads(path.read_text())
            data["devices"][0]["rate_limit_per_min"] = 1
            path.write_text(json.dumps(data))
            os.utime(path, None)
            codes = [c.post("/v1/chat/completions", json={"messages": []},
                            headers=hdr).status_code for _ in range(4)]
        assert 429 in codes, "clients.sh rate change never took effect"


class TestAdmissionControl:
    def test_rate_limit_returns_429(self, edge, tmp_path):
        _registry(tmp_path, rate=2)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            hdr = {"Authorization": f"Bearer {DEVICE_KEY}"}
            codes = [c.post("/v1/chat/completions", json={"messages": []},
                            headers=hdr).status_code for _ in range(4)]
        assert 429 in codes
        assert codes[0] == 200

    def test_health_is_not_rate_limited(self, edge, tmp_path):
        _registry(tmp_path, rate=1)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            hdr = {"Authorization": f"Bearer {DEVICE_KEY}"}
            for _ in range(5):
                assert c.get("/health", headers=hdr).status_code == 200


class TestAuditAndMetrics:
    def test_audit_records_cost_not_content(self, edge, tmp_path):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        secret = "the-user-private-prompt-text"
        with TestClient(edge.app) as c:
            c.post("/v1/chat/completions",
                   json={"messages": [{"role": "user", "content": secret}],
                         "model": "heretic-v2"},
                   headers={"Authorization": f"Bearer {DEVICE_KEY}"})
        audit = (tmp_path / ".run" / "inference-audit.jsonl").read_text()
        assert secret not in audit
        assert DEVICE_KEY not in audit
        row = json.loads(audit.strip().splitlines()[-1])
        assert row["device"] == "laptop"
        assert row["prompt_tokens"] == 10
        assert row["completion_tokens"] == 5
        assert row["model"] == "heretic-v2"
        # Correlation handle + enrollment identity: the two fields that make
        # "who used what, when" answerable across device-id reuse.
        assert row["request_id"] and len(row["request_id"]) == 16
        assert row["device_uid"] == edge.device_uid(
            {"id": "laptop", "enrolled_at": "2026-07-30T00:00:00Z"})

    def test_request_id_is_returned_to_the_caller(self, edge, tmp_path):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            r = c.post("/v1/chat/completions", json={"messages": []},
                       headers={"Authorization": f"Bearer {DEVICE_KEY}"})
        rid = r.headers.get("x-openbeast-request-id")
        assert rid, "no correlation id returned"
        audit = (tmp_path / ".run" / "inference-audit.jsonl").read_text()
        assert rid in audit, "returned id does not match the audit row"

    def test_reenrolled_device_id_does_not_inherit_history(self, edge, tmp_path):
        # `clients.sh remove laptop-air` then `enroll laptop-air` is a
        # DIFFERENT physical device wearing the same name. It must not inherit
        # the previous holder's audit identity or rate/in-flight counters.
        a = edge.device_uid({"id": "laptop", "enrolled_at": "2026-07-30T00:00:00Z"})
        b = edge.device_uid({"id": "laptop", "enrolled_at": "2026-08-15T09:00:00Z"})
        assert a != b

    def test_authenticated_denials_are_audited(self, edge, tmp_path):
        # A KNOWN device hitting its limit is audited (it has an identity to
        # attribute). Unauthenticated rejects are counted + logged instead —
        # see test_unauth_denials_do_not_grow_the_audit_file.
        _registry(tmp_path, rate=1)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            hdr = {"Authorization": f"Bearer {DEVICE_KEY}"}
            for _ in range(4):
                c.post("/v1/chat/completions", json={"messages": []},
                       headers=hdr)
        audit = (tmp_path / ".run" / "inference-audit.jsonl").read_text()
        assert "rate_limited" in audit

    def test_unauth_denial_is_counted_in_metrics(self, edge, tmp_path):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            c.post("/v1/chat/completions", json={"messages": []},
                   headers={"Authorization": "Bearer nope"})
            body = c.get("/gate/metrics").text
        assert 'openbeast_edge_denied_total{reason="bad_key"}' in body

    def test_metrics_exposes_per_device_tokens(self, edge, tmp_path):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            c.post("/v1/chat/completions", json={"messages": []},
                   headers={"Authorization": f"Bearer {DEVICE_KEY}"})
            # TestClient presents as loopback, which is exempt (local tooling).
            body = c.get("/gate/metrics").text
        assert 'openbeast_edge_prompt_tokens_total{device="laptop"}' in body
        assert DEVICE_KEY not in body

    def test_unauth_denials_do_not_grow_the_audit_file(self, edge, tmp_path):
        # The gate is published at the tailnet root, so an unauthenticated
        # caller must not be able to append to the audit file at will.
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            for _ in range(20):
                c.post("/v1/chat/completions", json={"messages": []},
                       headers={"Authorization": "Bearer nope"})
        audit = tmp_path / ".run" / "inference-audit.jsonl"
        assert not audit.exists() or audit.read_text().strip() == ""


class TestIntrospectionAuth:
    """/gate/health and /gate/metrics carry the device roster and per-device
    usage. The gate is mounted at the tailnet ROOT, so remote callers must
    not read them; loopback tooling still must."""

    def _remote(self, edge, tmp_path, path, key=None):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        hdr = {"Authorization": f"Bearer {key}"} if key else {}
        with TestClient(edge.app) as c:
            # Force a non-loopback peer.
            orig = edge._is_loopback
            edge._is_loopback = lambda r: False
            try:
                return c.get(path, headers=hdr)
            finally:
                edge._is_loopback = orig

    def test_remote_metrics_without_key_is_404(self, edge, tmp_path):
        assert self._remote(edge, tmp_path, "/gate/metrics").status_code == 404

    def test_remote_metrics_with_valid_device_key_allowed(self, edge, tmp_path):
        r = self._remote(edge, tmp_path, "/gate/metrics", key=DEVICE_KEY)
        assert r.status_code == 200

    def test_remote_health_hides_roster(self, edge, tmp_path):
        r = self._remote(edge, tmp_path, "/gate/health")
        assert r.status_code == 200          # liveness stays public
        assert "devices" not in r.json()     # roster size does not
        assert "upstream" not in r.json()

    def test_loopback_health_keeps_detail(self, edge, tmp_path):
        _registry(tmp_path)
        _stub_upstream(edge, {})
        with TestClient(edge.app) as c:
            body = c.get("/gate/health").json()
        assert body["devices"] == 2 and "upstream" in body


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
