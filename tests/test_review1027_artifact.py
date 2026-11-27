#!/usr/bin/env python3
"""beast-artifact — review 2026-10-27.

R7-07: the two layers of the chown admin gate (the PATCH route and
store.set_owner) are each tested on their own, so a refactor that drops
either one fails here.

Each test builds its own temp store, run dir and in-process app (fixtures
from test_artifact_review_0930); no server or port.

Run: python3 -m pytest tests/test_review1027_artifact.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_artifact_review_0930 import (FLAT_404, KID, PAGE,  # noqa: E402,F401
                                       enroll, env, local, make_client,
                                       store)


def _owned_by(login, **kw):
    token = store.set_owner_override(login)
    try:
        return store.publish(PAGE, **kw)
    finally:
        store.reset_owner_override(token)


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
