#!/usr/bin/env python3
"""beast-artifact — review 2026-10-27.

R2-5: OPENBEAST_ARTIFACT_LOGIN_FROM=unix (beast-chat's LOGIN_FROM=unix,
ported): the Tailscale-User-Login header then counts only on the 0600 Unix
socket, so a host-network container on TCP loopback cannot claim an owner.

R7-07: the two layers of the chown admin gate (the PATCH route and
store.set_owner) are each tested on their own, so a refactor that drops
either one fails here.

Each test builds its own temp store, run dir and in-process app (fixtures
from test_artifact_review_0930); no server or port.

Run: python3 -m pytest tests/test_review1027_artifact.py -q
"""
import os
import socket
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_artifact_review_0930 import (FLAT_404, KID, MAX, PAGE,  # noqa: E402,F401
                                       artifact_server, enroll, env, local,
                                       make_client, publish, store)


def _owned_by(login, **kw):
    token = store.set_owner_override(login)
    try:
        return store.publish(PAGE, **kw)
    finally:
        store.reset_owner_override(token)


# --- R2-5 -------------------------------------------------------------------

def _scope_without_client(monkeypatch):
    """Patch the TestClient transport so scope['client'] is None."""
    import starlette.testclient as tc
    real = tc._TestClientTransport.handle_request

    def handle(self, request):
        saved = self.client
        self.client = None
        try:
            return real(self, request)
        finally:
            self.client = saved
    monkeypatch.setattr(tc._TestClientTransport, "handle_request", handle)


class TestLoginFromUnix:
    def test_default_honours_tcp_loopback_login(self, make_client):  # noqa: F811
        # Negative control: the default is unchanged.
        c = make_client()
        a = _owned_by("max@example.com")
        assert c.get(f"/a/{a['id']}", headers=MAX).status_code == 200

    def test_unix_mode_ignores_a_tcp_loopback_login(self, make_client,  # noqa: F811
                                                    monkeypatch):
        monkeypatch.setenv("OPENBEAST_ARTIFACT_LOGIN_FROM", "unix")
        c = make_client()
        a = _owned_by("max@example.com")
        r = c.get(f"/a/{a['id']}", headers=MAX)          # a host-net container
        assert r.status_code == 404 and r.json() == FLAT_404
        assert c.get("/api/artifacts", headers=MAX).status_code == 404
        # The locality token still works over TCP.
        assert c.get(f"/a/{a['id']}", headers=local(c)).status_code == 200

    def test_unix_mode_honours_the_login_on_the_socket(self, make_client,  # noqa: F811
                                                       monkeypatch):
        monkeypatch.setenv("OPENBEAST_ARTIFACT_LOGIN_FROM", "unix")
        c = make_client()
        a = _owned_by("max@example.com")
        _scope_without_client(monkeypatch)
        assert c.get(f"/a/{a['id']}", headers=MAX).status_code == 200
        assert c.get(f"/a/{a['id']}", headers=KID).status_code == 404

    def test_unknown_value_means_loopback(self, monkeypatch):
        monkeypatch.setenv("OPENBEAST_ARTIFACT_LOGIN_FROM", "bogus")
        assert artifact_server.login_source() == "loopback"
        monkeypatch.setenv("OPENBEAST_ARTIFACT_LOGIN_FROM", " UNIX ")
        assert artifact_server.login_source() == "unix"

    def test_unix_listener_is_0600_in_a_0700_dir(self, tmp_path):
        path = tmp_path / "sockdir" / "artifact.sock"
        s = artifact_server._unix_listener(str(path))
        try:
            assert stat.S_ISSOCK(os.lstat(path).st_mode)
            assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
            assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
            assert s.family == socket.AF_UNIX
        finally:
            s.close()
        # A stale socket is replaced; a regular file is not.
        artifact_server._unix_listener(str(path)).close()
        path.unlink()
        path.write_text("not a socket")
        with pytest.raises(SystemExit):
            artifact_server._unix_listener(str(path))
        assert path.read_text() == "not a socket"


# --- R7-07 ------------------------------------------------------------------

class TestChownStoreLayer:
    def test_set_owner_refuses_a_non_admin_even_the_owner(self, env):  # noqa: F811
        a = _owned_by("max@example.com")
        with pytest.raises(store.ArtifactError):
            store.set_owner(a["id"], "x@example.com",
                            owner="max@example.com", admin=False)
        assert store.get_meta(a["id"])["owner"] == "max@example.com"

    def test_set_owner_admin_control(self, env):  # noqa: F811
        a = _owned_by("max@example.com")
        store.set_owner(a["id"], "x@example.com", owner=None, admin=True)
        assert store.get_meta(a["id"])["owner"] == "x@example.com"


class TestChownRouteLayer:
    def test_route_refuses_a_non_admin_with_the_store_guard_removed(
            self, make_client, monkeypatch):  # noqa: F811
        c = make_client(operators="max@example.com,kid@example.com")
        a = _owned_by("kid@example.com")
        real = store.set_owner

        def permissive(artifact_id, new_owner, *, owner=None, admin=False):
            return real(artifact_id, new_owner, owner=owner, admin=True)
        monkeypatch.setattr(store, "set_owner", permissive)
        phone = enroll(c.tmp)
        r = c.patch(f"/api/artifacts/{a['id']}",
                    json={"owner": "x@example.com"}, headers={**KID, **phone})
        assert r.status_code == 404 and r.json() == FLAT_404
        assert store.get_meta(a["id"])["owner"] == "kid@example.com"
        # Control: the rig (an admin) still hands it over through the route.
        r = c.patch(f"/api/artifacts/{a['id']}",
                    json={"owner": "x@example.com"}, headers=local(c))
        assert r.status_code == 200
        assert store.get_meta(a["id"])["owner"] == "x@example.com"
