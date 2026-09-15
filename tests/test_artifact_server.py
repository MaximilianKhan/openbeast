#!/usr/bin/env python3
"""beast-artifact server (agents/artifact_server.py) — isolation, auth, API.

The load-bearing tests here are the two CSP assertions: the exact policy on
/raw/ (model-authored content) and the different, stricter policy on our own
shell and gallery. They pin the strings character for character on purpose —
if someone ever adds `allow-same-origin`, drops `connect-src 'none'`, or
widens the CDN allowlist, this file fails loudly rather than the isolation
quietly disappearing.

The second load-bearing group is the security model
(docs/BEAST_ARTIFACT_PLAN.md as amended after three adversarial reviews).
Each of these tests failed before the fix it names:

  D1   identity is REQUIRED — anonymous is 404 on every route but health,
       and is never more privileged than a caller who names themselves
  D4   the publish body cannot name an owner
  D9   no /docs, /redoc, /openapi.json; every refusal is one flat 404,
       including method-not-allowed and parameter validation
  D10  size and auth run in middleware, before any body is parsed
  D11  /metrics is operator-only, and an unmatched route cannot forge a
       metric series named after the request path
  D12  health is minimal and does not scan the store; limit=0 is clamped
  D16  template substitution is single pass — a title cannot delete the
       iframe's src and sandbox
  D18  a failed second start never overwrites a live server's token
  D23  every version resolution goes through the store's hardened helper:
       eleven ordinary corruptions of meta.json, through every route, are
       the page or a flat 404 — never a 500 for the legitimate owner
  D24  a trailing slash is not a route oracle (the 307 outlived D9)
  D27  refusals cannot grow the audit file without bound
  D29  an ownership refusal is the flat 404; HEAD works on health; two
       identity headers are refused rather than silently resolved
  R2   all FOUR mutations name their owner explicitly — proven with the
       ContextVar taken away, because a ContextVar crossing
       BaseHTTPMiddleware into the threadpool was the only thing gating
       three of them
  R5   the audit budget is per REASON and per WINDOW, the reason is a
       bounded metric label, and a flood of the cheapest refusal cannot
       blind the log to the ambiguous-identity signal
  R6   the provenance id (meta["owner_webui_id"]) is never returned by the
       API — see tests/test_artifact_mcp_tools.py, which can publish one

Also covers:
  - publish → /raw/ round trip, skeleton applied at serve time
  - republish: same URL, v2 current, v1 still served
  - supporting files: content type from the extension, nosniff, traversal 404
  - read auth: unlisted Tailscale-User-Login → 404 on every route
  - a private artifact of another owner → 404; tailnet visibility → 200
  - write auth: POST/PATCH/DELETE without the locality token → 404, with → 201
  - caps rejected with a 400 and a readable message
  - audit log: 0600, one row per request, no HTML inside

Run: pytest tests/test_artifact_server.py
"""
import json
import os
import socket
import sys
import time

import pytest
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

import artifact as store          # noqa: E402
import artifact_server            # noqa: E402

PAGE = "<title>Hello</title><p>hi</p>"
MAX = {"Tailscale-User-Login": "max@example.com"}
KID = {"Tailscale-User-Login": "kid@example.com"}
STRANGER = {"Tailscale-User-Login": "nobody@example.com"}

# The body EVERY refusal carries, byte for byte (D9).
FLAT_404 = {"detail": "Not Found"}

# The exact policy /raw/ must carry. Duplicated here (not imported) so a typo
# in the server constant cannot silently agree with itself.
EXPECTED_RAW_CSP = (
    "sandbox allow-scripts allow-forms allow-modals allow-popups; "
    "default-src 'none'; "
    "script-src 'unsafe-inline' https://cdnjs.cloudflare.com "
    "https://cdn.jsdelivr.net/npm/ https://cdn.tailwindcss.com "
    "https://code.jquery.com; "
    "style-src 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com data:; "
    "img-src 'self' data: blob:; "
    "media-src 'self' data: blob:; "
    "connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("OPENBEAST_ARTIFACT_BASE_URL", "https://beast:8446")
    # A port nothing answers on: create_app() probes it before reusing a
    # token file (D18), and a test must never depend on the real rig.
    monkeypatch.setenv("OPENBEAST_ARTIFACT_PORT", "39917")
    monkeypatch.delenv("OPENBEAST_ARTIFACT_OPERATORS", raising=False)
    monkeypatch.delenv("OPENBEAST_CHAT_OPERATORS", raising=False)
    return tmp_path


@pytest.fixture()
def make_client(env, monkeypatch):
    """Client factory.

    It is a FIXTURE, not a module function, so the operator allowlist goes
    through `monkeypatch.setenv`: the old version assigned os.environ
    directly and leaked OPENBEAST_ARTIFACT_OPERATORS into every test that ran
    after it in the session — an allowlist appearing out of nowhere is
    exactly the kind of drift these tests exist to catch.
    """
    def _make(operators: str = ""):
        if operators:
            monkeypatch.setenv("OPENBEAST_ARTIFACT_OPERATORS", operators)
        app = artifact_server.create_app()
        # A REAL Host header. TrustedHostMiddleware now pins it (the v1.4.0
        # review found this server had no Host validation at all), and
        # TestClient's default "testserver" is exactly the kind of foreign
        # name a rebinding attack arrives under.
        c = TestClient(app, base_url="http://127.0.0.1:3004")
        c.app_token = app.state.local_token      # type: ignore[attr-defined]
        c.asgi_app = app                         # type: ignore[attr-defined]
        return c
    return _make


def local(c, extra=None):
    h = {"X-OpenBeast-Local": c.app_token}
    h.update(extra or {})
    return h


def publish(c, headers=None, **kw):
    body = {"html": PAGE}
    body.update(kw)
    r = c.post("/api/artifacts", json=body, headers=headers or local(c))
    assert r.status_code == 201, r.text
    return r.json()


# --- isolation (the load-bearing part) ---------------------------------------

def test_raw_csp_is_exactly_the_pinned_policy(make_client):
    c = make_client()
    a = publish(c)
    r = c.get(f"/raw/{a['id']}/v/1/", headers=local(c))
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert csp == EXPECTED_RAW_CSP
    # the properties the string exists for, asserted individually so a future
    # reader sees WHY each clause matters
    assert "allow-same-origin" not in csp     # opaque origin: no storage/cookies
    assert "allow-downloads" not in csp
    assert "allow-top-navigation" not in csp
    assert "connect-src 'none'" in csp        # no fetch/XHR/WebSocket
    assert "default-src 'none'" in csp
    assert "https://unpkg.com" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"


def test_raw_headers_on_supporting_files_too(make_client):
    c = make_client()
    a = publish(c, files={"app.js": "console.log(1)"})
    r = c.get(f"/raw/{a['id']}/v/1/app.js", headers=local(c))
    assert r.status_code == 200
    assert r.text == "console.log(1)"
    assert r.headers["content-security-policy"] == EXPECTED_RAW_CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"].startswith("text/javascript") or \
        r.headers["content-type"].startswith("application/javascript")


def test_shell_and_gallery_carry_a_different_stricter_policy(make_client):
    c = make_client()
    a = publish(c)
    for path in ("/", f"/a/{a['id']}", f"/a/{a['id']}/v/1"):
        r = c.get(path, headers=local(c))
        assert r.status_code == 200, path
        csp = r.headers["content-security-policy"]
        assert csp == artifact_server.SHELL_CSP
        assert csp != EXPECTED_RAW_CSP
        assert "frame-ancestors 'none'" in csp   # our UI is never framed
        assert "script-src 'self' 'unsafe-inline'" in csp
        assert "sandbox" not in csp              # sandboxing our own UI = broken
        assert "cdnjs.cloudflare.com" not in csp
        assert r.headers["x-frame-options"] == "DENY"


def test_shell_embeds_the_raw_route_in_a_sandboxed_iframe(make_client):
    """The src and the sandbox must be on the SAME TAG.

    This test used to assert that each string appeared ANYWHERE in the body,
    which is vacuous against the bug it sits next to: the D16 splice closed
    the <iframe> tag early, leaving `src=` and `sandbox=` in the document —
    as loose text, on no element — while the frame that actually loaded had
    neither. Two `in body` checks pass happily through that. Parse the tag.
    """
    c = make_client()
    a = publish(c)
    body = c.get(f"/a/{a['id']}", headers=local(c)).text
    tag = _iframe_tag(body)
    assert f'src="/raw/{a["id"]}/v/1/"' in tag
    assert IFRAME_SANDBOX in tag
    # exactly one frame, and the sandbox never gains the escape hatch
    assert body.count("<iframe") == 1
    assert "allow-same-origin" not in tag


def test_head_returns_the_headers_without_a_body(make_client):
    """`curl -I` and header probes must see the policy, not a 405."""
    c = make_client()
    a = publish(c)
    r = c.head(f"/raw/{a['id']}/v/1/", headers=local(c))
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == EXPECTED_RAW_CSP
    assert r.content == b""


def test_unknown_content_type_defaults_to_octet_stream(make_client):
    c = make_client()
    a = publish(c, files={"blob.weird": {"b64": "AAE="}})
    r = c.get(f"/raw/{a['id']}/v/1/blob.weird", headers=local(c))
    assert r.headers["content-type"] == "application/octet-stream"


# --- serving ------------------------------------------------------------------

def test_publish_round_trip_and_skeleton(make_client):
    c = make_client()
    a = publish(c, description="a greeting", favicon="👋")
    assert a["url"] == f"https://beast:8446/a/{a['id']}"
    assert a["title"] == "Hello" and a["version"] == 1

    raw = c.get(f"/raw/{a['id']}/v/1/", headers=local(c)).text
    assert raw.startswith("<!doctype html>")     # skeleton applied at SERVE time
    assert "width=device-width" in raw
    assert PAGE in raw
    # ...and never stored
    on_disk, _ = store.read_file(a["id"], 1)
    assert on_disk.decode() == PAGE

    shell = c.get(f"/a/{a['id']}", headers=local(c)).text
    assert "Hello" in shell and "a greeting" in shell


def test_theme_query_stamps_the_skeleton(make_client):
    c = make_client()
    a = publish(c)
    h = local(c)
    assert 'data-theme="dark"' in \
        c.get(f"/raw/{a['id']}/v/1/?theme=dark", headers=h).text
    assert "data-theme" not in c.get(f"/raw/{a['id']}/v/1/", headers=h).text
    assert "data-theme" not in \
        c.get(f"/raw/{a['id']}/v/1/?theme=bogus", headers=h).text


def test_republish_keeps_url_adds_version(make_client):
    c = make_client()
    h = local(c)
    a = publish(c)
    b = publish(c, html="<title>Two</title>second", artifact_id=a["id"],
                label="pass 2")
    assert b["id"] == a["id"] and b["version"] == 2

    assert "second" in c.get(f"/raw/{a['id']}/v/2/", headers=h).text
    assert PAGE in c.get(f"/raw/{a['id']}/v/1/", headers=h).text   # v1 served
    shell = c.get(f"/a/{a['id']}", headers=h).text
    assert 'value="2" selected' in shell and 'value="1"' in shell
    assert "pass 2" in shell
    meta = c.get(f"/api/artifacts/{a['id']}", headers=h).json()
    assert meta["current"] == 2 and len(meta["versions"]) == 2

    # rollback moves the pointer; both versions stay reachable
    r = c.patch(f"/api/artifacts/{a['id']}", json={"current": 1}, headers=h)
    assert r.status_code == 200 and r.json()["current"] == 1
    assert 'value="1" selected' in c.get(f"/a/{a['id']}", headers=h).text
    assert "second" in c.get(f"/raw/{a['id']}/v/2/", headers=h).text


def test_missing_things_are_404(make_client):
    c = make_client()
    h = local(c)
    a = publish(c)
    assert c.get("/a/00000000-0000-4000-8000-000000000000",
                 headers=h).status_code == 404
    assert c.get(f"/a/{a['id']}/v/7", headers=h).status_code == 404
    assert c.get(f"/raw/{a['id']}/v/7/", headers=h).status_code == 404
    assert c.get(f"/raw/{a['id']}/v/1/nope.js", headers=h).status_code == 404
    assert c.get(f"/raw/{a['id']}/v/1/../../meta.json",
                 headers=h).status_code == 404
    assert c.get("/a/not a valid id", headers=h).status_code == 404


def test_gallery_lists_artifacts(make_client):
    c = make_client()
    alpha = publish(c, html="<title>Alpha</title>a", description="first")
    beta = publish(c, html="<title>Beta</title>b")
    body = c.get("/", headers=local(c)).text
    assert "Alpha" in body and "Beta" in body and "first" in body
    # a card linking to each artifact, whatever the template's wording is
    for a in (alpha, beta):
        assert f'href="/a/{a["id"]}"' in body


# --- D1: identity is required -------------------------------------------------

def test_anonymous_gets_404_on_every_route_but_health(make_client):
    """D1. A caller with no login header and no locality token does not get
    to find out that any of this exists."""
    c = make_client()
    a = publish(c, files={"app.js": "console.log(1)"})
    for path in ("/", f"/a/{a['id']}", f"/a/{a['id']}/v/1",
                 f"/raw/{a['id']}/v/1/", f"/raw/{a['id']}/v/1/app.js",
                 "/api/artifacts", f"/api/artifacts/{a['id']}", "/metrics"):
        r = c.get(path)
        assert r.status_code == 404, path
        assert r.json() == FLAT_404, path
    # health, and only health, answers
    r = c.get("/api/artifacts/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_anonymous_is_not_more_privileged_than_a_named_caller(make_client):
    """D1, the inversion itself. On the SHIPPED default config (no
    allowlist) a caller who sent nothing read every owner's private
    artifacts, while a caller who named themselves correctly got 404."""
    c = make_client()
    mine = publish(c, headers=local(c, MAX))         # owner: max
    assert store.get_meta(mine["id"])["owner"] == "max@example.com"
    assert c.get(f"/a/{mine['id']}", headers=MAX).status_code == 200
    assert c.get(f"/a/{mine['id']}").status_code == 404          # was 200
    assert c.get("/api/artifacts").status_code == 404            # was the lot
    # an unowned/legacy artifact is not a back door either
    assert c.get("/").status_code == 404


def test_anonymous_writes_are_404_before_the_body_is_read(make_client):
    c = make_client()
    r = c.post("/api/artifacts", json={"html": PAGE})
    assert r.status_code == 404 and r.json() == FLAT_404


def test_identified_caller_without_an_allowlist_is_a_viewer(make_client):
    """No allowlist means identity is not CHECKED against a list — it is
    still required, and ownership still governs each artifact."""
    c = make_client()
    mine = publish(c, headers=local(c, MAX))
    assert c.get(f"/a/{mine['id']}", headers=MAX).status_code == 200
    assert c.get(f"/a/{mine['id']}", headers=STRANGER).status_code == 404
    shared = publish(c, headers=local(c, MAX), visibility="tailnet")
    assert c.get(f"/a/{shared['id']}", headers=STRANGER).status_code == 200


# --- read auth ----------------------------------------------------------------

def test_unlisted_login_gets_404_everywhere(make_client):
    c = make_client(operators="max@example.com,kid@example.com")
    a = publish(c, visibility="tailnet")
    for path in ("/", f"/a/{a['id']}", f"/a/{a['id']}/v/1",
                 f"/raw/{a['id']}/v/1/", "/api/artifacts",
                 f"/api/artifacts/{a['id']}"):
        r = c.get(path, headers=STRANGER)
        assert r.status_code == 404, path          # never 403
        assert r.json() == FLAT_404, path
    # a listed operator sees the same paths
    for path in ("/", f"/a/{a['id']}", f"/raw/{a['id']}/v/1/",
                 "/api/artifacts"):
        assert c.get(path, headers=KID).status_code == 200, path


def test_private_artifact_of_another_owner_is_404(make_client):
    c = make_client(operators="max@example.com,kid@example.com")
    a = publish(c)                                 # owner: first operator
    assert store.get_meta(a["id"])["owner"] == "max@example.com"
    assert c.get(f"/a/{a['id']}", headers=MAX).status_code == 200
    assert c.get(f"/a/{a['id']}", headers=KID).status_code == 404
    assert c.get(f"/raw/{a['id']}/v/1/", headers=KID).status_code == 404
    assert c.get(f"/api/artifacts/{a['id']}", headers=KID).status_code == 404
    assert c.get("/api/artifacts", headers=KID).json()["count"] == 0

    r = c.patch(f"/api/artifacts/{a['id']}", json={"visibility": "tailnet"},
                headers=local(c))
    assert r.status_code == 200 and r.json()["visibility"] == "tailnet"
    assert c.get(f"/a/{a['id']}", headers=KID).status_code == 200
    assert c.get("/api/artifacts", headers=KID).json()["count"] == 1


def test_chat_operators_is_the_fallback_allowlist(make_client, monkeypatch):
    monkeypatch.setenv("OPENBEAST_CHAT_OPERATORS", "max@example.com")
    c = make_client()
    assert c.get("/", headers=MAX).status_code == 200
    assert c.get("/", headers=STRANGER).status_code == 404


# --- write auth ---------------------------------------------------------------

def test_writes_need_the_locality_token(make_client):
    c = make_client(operators="max@example.com")
    a = publish(c)

    # no token: 404 on every write route, even for a listed operator
    assert c.post("/api/artifacts", json={"html": PAGE},
                  headers=MAX).status_code == 404
    assert c.patch(f"/api/artifacts/{a['id']}", json={"visibility": "tailnet"},
                   headers=MAX).status_code == 404
    assert c.delete(f"/api/artifacts/{a['id']}", headers=MAX).status_code == 404
    # a wrong token is no better
    assert c.post("/api/artifacts", json={"html": PAGE},
                  headers={"X-OpenBeast-Local": "deadbeef"}).status_code == 404
    # a non-ASCII token must not become a 500 (latin-1 header decoding)
    assert c.post("/api/artifacts", json={"html": PAGE},
                  headers={"X-OpenBeast-Local": b"t\xc3\xb6k\xc3\xa9n"}
                  ).status_code == 404

    # with the token: 201
    r = c.post("/api/artifacts", json={"html": PAGE}, headers=local(c))
    assert r.status_code == 201
    assert store.get_meta(a["id"]) is not None
    assert c.delete(f"/api/artifacts/{a['id']}",
                    headers=local(c)).status_code == 200
    assert store.get_meta(a["id"]) is None


def test_a_malformed_body_from_the_tailnet_leaks_nothing(make_client):
    """D10. The old version of this test posted WELL-FORMED json ({"nope":1})
    and so asserted the case that already worked. FastAPI parses the body
    BEFORE it solves dependencies, so unparseable json from an unauthorised
    caller used to come back 422 — which confirms the route exists, names the
    fields, and happens after the bytes are already in memory."""
    c = make_client(operators="max@example.com")
    bad = [
        (b"{not json", "application/json"),
        (b"", "application/json"),
        (b"[1,2,3]", "application/json"),
        (b"<xml/>", "application/xml"),
    ]
    for payload, ctype in bad:
        r = c.post("/api/artifacts", content=payload,
                   headers={**MAX, "Content-Type": ctype})
        assert r.status_code == 404, payload
        assert r.json() == FLAT_404, payload
    # ...and a well-formed body that is missing required fields, too
    r = c.post("/api/artifacts", json={"nope": 1}, headers=MAX)
    assert r.status_code == 404 and r.json() == FLAT_404


def test_local_token_file_is_0600(make_client, tmp_path):
    make_client()
    path = tmp_path / "run" / "artifact-local.token"
    assert path.exists()
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_publish_owner_defaults_to_the_caller(make_client):
    c = make_client(operators="max@example.com,kid@example.com")
    a = publish(c)                                     # no login header
    assert store.get_meta(a["id"])["owner"] == "max@example.com"  # 1st operator
    r = c.post("/api/artifacts", json={"html": PAGE}, headers=local(c, KID))
    assert store.get_meta(r.json()["id"])["owner"] == "kid@example.com"


def test_the_body_cannot_name_an_owner(make_client):
    """D4. `owner` in the publish body let a local writer attribute a page to
    anyone — including a login no operator uses, which made the artifact
    invisible to every real person on the rig."""
    c = make_client(operators="max@example.com,kid@example.com")
    r = c.post("/api/artifacts",
               json={"html": PAGE, "owner": "ghost@example.invalid"},
               headers=local(c))
    assert r.status_code == 201                        # ignored, not rejected
    meta = store.get_meta(r.json()["id"])
    assert meta["owner"] == "max@example.com"          # the principal, not the body
    assert c.get(f"/a/{r.json()['id']}", headers=MAX).status_code == 200
    # the model no longer carries the field at all
    assert "owner" not in artifact_server.PublishBody.model_fields
    # nor can it forge one through the login header without the token
    assert c.post("/api/artifacts", json={"html": PAGE},
                  headers=KID).status_code == 404


def test_patch_is_attributed_to_the_caller_not_the_first_operator(make_client):
    """The store gates visibility changes on ownership (D5), so the server
    has to say WHO is asking instead of letting it guess."""
    c = make_client(operators="boss@example.com,max@example.com")
    a = publish(c, headers=local(c, MAX))          # owner: max, not boss
    assert store.get_meta(a["id"])["owner"] == "max@example.com"
    r = c.patch(f"/api/artifacts/{a['id']}", json={"visibility": "tailnet"},
                headers=local(c, MAX))
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "tailnet"
    # ...and a local caller who is somebody else cannot share max's page.
    # The refusal is the FLAT 404 (D29): "not your artifact" is a 400 that
    # confirms a page exists at an id where a nonexistent one answers 404, so
    # the second operator could map the store by the shape of the error.
    r = c.patch(f"/api/artifacts/{a['id']}", json={"visibility": "private"},
                headers=local(c))                  # resolves to boss
    assert r.status_code == 404 and r.json() == FLAT_404
    assert "not your artifact" not in r.text
    # and it really was refused, not quietly applied
    assert store.get_meta(a["id"])["visibility"] == "tailnet"
    # an id that does not exist at all is indistinguishable from it
    r = c.patch("/api/artifacts/00000000-0000-4000-8000-000000000000",
                json={"visibility": "private"}, headers=local(c))
    assert r.status_code == 404 and r.json() == FLAT_404


# --- R2: every mutation names its owner, without help from the ContextVar ----
# D22's server half was written for `set_visibility` and never reached the
# other three. They passed their tests anyway, because a ContextVar set in
# BaseHTTPMiddleware happens to survive into Starlette's threadpool — the
# least stable corner of that framework, and the one a reviewer removed to
# PATCH a description, roll back a version and DELETE another owner's page,
# all 200. These tests take that mechanism away on purpose: with the
# override neutered, only an explicit `owner=` can carry the caller.


def _drop_the_contextvar(monkeypatch):
    """Delete the mechanism R2 says is load-bearing but must not be.

    `store.default_owner()` then falls back to the env allowlist's first
    entry (boss), exactly as it would if the ContextVar silently stopped
    crossing BaseHTTPMiddleware into the threadpool one Starlette release
    from now. Applied AFTER the fixture publishes, because `publish()` is
    owned by the ContextVar by design (D28: `owner=` is an assertion there,
    not an identity) — it is the four METADATA mutations R2 is about.
    """
    monkeypatch.setattr(store, "set_owner_override", lambda *a, **k: None)
    monkeypatch.setattr(store, "reset_owner_override", lambda token: None)


def _maxs_artifact(c, monkeypatch, versions: int = 1) -> dict:
    a = publish(c, headers=local(c, MAX))
    for _ in range(versions - 1):
        publish(c, headers=local(c, MAX), artifact_id=a["id"])
    assert store.get_meta(a["id"])["owner"] == "max@example.com"
    _drop_the_contextvar(monkeypatch)
    return a


def test_patching_a_description_refuses_a_non_owner_over_http(
        make_client, monkeypatch):
    """R2. The gallery subtitle is the text every other operator reads."""
    c = make_client(operators="boss@example.com,max@example.com")
    a = _maxs_artifact(c, monkeypatch)
    r = c.patch(f"/api/artifacts/{a['id']}", json={"description": "pwned"},
                headers=local(c))                  # resolves to boss
    assert r.status_code == 404 and r.json() == FLAT_404
    assert store.get_meta(a["id"]).get("description") != "pwned"
    # and the owner still can, with the ContextVar still gone: the identity
    # travelled as an argument, which is the whole point.
    r = c.patch(f"/api/artifacts/{a['id']}", json={"description": "mine"},
                headers=local(c, MAX))
    assert r.status_code == 200, r.text
    assert store.get_meta(a["id"])["description"] == "mine"


def test_rolling_back_a_version_refuses_a_non_owner_over_http(
        make_client, monkeypatch):
    """R2. An ungated rollback serves an OLDER page at a URL its owner
    believes is current — a deface that leaves no trace in the version list."""
    c = make_client(operators="boss@example.com,max@example.com")
    a = _maxs_artifact(c, monkeypatch, versions=2)
    assert store.get_meta(a["id"])["current"] == 2
    r = c.patch(f"/api/artifacts/{a['id']}", json={"current": 1},
                headers=local(c))
    assert r.status_code == 404 and r.json() == FLAT_404
    assert store.get_meta(a["id"])["current"] == 2
    r = c.patch(f"/api/artifacts/{a['id']}", json={"current": 1},
                headers=local(c, MAX))
    assert r.status_code == 200, r.text
    assert store.get_meta(a["id"])["current"] == 1


def test_deleting_refuses_a_non_owner_over_http(make_client, monkeypatch):
    """R2, the irreversible one: versions are the only copy there is."""
    c = make_client(operators="boss@example.com,max@example.com")
    a = _maxs_artifact(c, monkeypatch)
    r = c.delete(f"/api/artifacts/{a['id']}", headers=local(c))
    assert r.status_code == 404 and r.json() == FLAT_404
    assert store.get_meta(a["id"]) is not None, "the page was destroyed"
    assert c.get(f"/raw/{a['id']}/v/1/", headers=MAX).status_code == 200
    r = c.delete(f"/api/artifacts/{a['id']}", headers=local(c, MAX))
    assert r.status_code == 200 and r.json()["removed"] is True
    assert store.get_meta(a["id"]) is None


def test_sharing_refuses_a_non_owner_over_http_too(make_client,
                                                   monkeypatch):
    """The fourth mutation — the only one D22 actually reached — held up
    under the same conditions, which is what made the other three look safe."""
    c = make_client(operators="boss@example.com,max@example.com")
    a = _maxs_artifact(c, monkeypatch)
    r = c.patch(f"/api/artifacts/{a['id']}", json={"visibility": "tailnet"},
                headers=local(c))
    assert r.status_code == 404 and r.json() == FLAT_404
    assert store.get_meta(a["id"])["visibility"] == "private"


def test_a_mixed_patch_body_cannot_slip_one_field_past_the_guard(
        make_client, monkeypatch):
    """All three fields in ONE request: the guard is per-mutator, so a body
    that sets every field must be refused on the first one and change
    nothing at all."""
    c = make_client(operators="boss@example.com,max@example.com")
    a = _maxs_artifact(c, monkeypatch, versions=2)
    before = dict(store.get_meta(a["id"]))
    r = c.patch(f"/api/artifacts/{a['id']}",
                json={"visibility": "tailnet", "description": "pwned",
                      "current": 1},
                headers=local(c))
    assert r.status_code == 404 and r.json() == FLAT_404
    after = store.get_meta(a["id"])
    for field in ("visibility", "description", "current"):
        assert after.get(field) == before.get(field), field


def test_the_store_sees_the_caller_for_the_whole_request(make_client):
    """The owner ContextVar is set for the duration of the request, so a
    store call that resolves the caller itself agrees with the one the route
    passes explicitly."""
    c = make_client(operators="boss@example.com,max@example.com")
    seen = []
    publish(c, headers=local(c, MAX))

    @c.asgi_app.get("/_probe_owner")
    def _probe():
        seen.append(store.default_owner())
        return {"ok": True}

    c.get("/_probe_owner", headers=local(c, MAX))
    assert seen == ["max@example.com"]


def test_a_local_publish_cannot_attribute_to_a_non_operator(make_client):
    c = make_client(operators="max@example.com")
    r = c.post("/api/artifacts", json={"html": PAGE},
               headers=local(c, STRANGER))
    assert r.status_code == 201
    assert store.get_meta(r.json()["id"])["owner"] == "max@example.com"


# --- D9: nothing announces the route table ------------------------------------

def test_there_is_no_schema_and_no_docs(make_client):
    """D9. /openapi.json documented the entire write API to anyone who
    asked, and /docs pulled unpinned third-party script into the same origin
    as the viewer shell."""
    c = make_client()
    for path in ("/openapi.json", "/docs", "/redoc",
                 "/docs/oauth2-redirect"):
        for headers in ({}, dict(MAX), local(c)):
            r = c.get(path, headers=headers)
            assert r.status_code == 404, (path, headers)
            assert r.json() == FLAT_404, (path, headers)
            assert "openapi" not in r.text.lower()
            assert "swagger" not in r.text.lower()


def test_every_refusal_is_the_same_flat_404(make_client):
    """D9. A reviewer walked the route table without credentials: 405 named
    the verbs a path takes, and a bad path parameter 422'd with the
    parameter's name — both fire before any route code runs."""
    c = make_client()
    a = publish(c)
    probes = [
        ("options", "/api/artifacts", {}),                 # 405: allowed verbs
        ("options", f"/a/{a['id']}", {}),
        ("get", f"/a/{a['id']}/v/not-a-number", {}),       # 422: parameter name
        ("get", "/api/artifacts?limit=not-a-number", {}),
        ("get", "/definitely/not/a/route", {}),
        ("get", "/api/artifacts/%2e%2e", {}),
    ]
    seen = set()
    for verb, path, extra in probes:
        r = getattr(c, verb)(path, headers={**MAX, **extra})
        assert r.status_code == 404, (verb, path)
        assert r.json() == FLAT_404, (verb, path)
        assert "allow" not in {k.lower() for k in r.headers}
        seen.add((r.text, r.headers.get("content-length")))
    # identical in content AND length — including to an ordinary miss
    r = c.get("/a/00000000-0000-4000-8000-000000000000", headers=MAX)
    seen.add((r.text, r.headers.get("content-length")))
    assert len(seen) == 1, seen


def test_the_rig_itself_still_gets_real_errors(make_client):
    """The flat 404 is for strangers. An operator debugging the CLI keeps
    the diagnosis — they already hold the locality token."""
    c = make_client()
    a = publish(c)
    assert c.get(f"/a/{a['id']}/v/nope", headers=local(c)).status_code == 422
    r = c.post("/api/artifacts", json={"title": "no html"}, headers=local(c))
    assert r.status_code == 400 and "html" in r.json()["detail"]


def test_a_crash_does_not_announce_the_route_to_a_stranger(make_client):
    """D9, the last exit: an unhandled 500 names the route as loudly as a
    405 does. The rig still gets the real failure."""
    c = make_client()

    @c.asgi_app.get("/_boom")
    def _boom():
        raise RuntimeError("kaboom")

    quiet = TestClient(c.asgi_app, raise_server_exceptions=False,
                         base_url="http://127.0.0.1:3004")
    r = quiet.get("/_boom", headers=MAX)
    assert r.status_code == 404 and r.json() == FLAT_404
    assert "kaboom" not in r.text
    r = quiet.get("/_boom", headers=local(c))
    assert r.status_code == 500                    # operators see the truth


# --- D10: middleware runs before the body ------------------------------------

def test_oversized_content_length_is_refused_before_parsing(make_client,
                                                            monkeypatch):
    """D10. An unauthenticated caller could stream 50 MB into this process
    before anything looked at it, and the validation error that came back
    disclosed the route."""
    c = make_client()
    cap = store.CAPS["version_bytes"]
    monkeypatch.setitem(store.CAPS, "version_bytes", 64)
    payload = b'{"html": "' + b"x" * 200 + b'"}'
    # even WITH the locality token, and even unparseable, it is one flat 404
    for headers in (local(c), dict(MAX), {}):
        r = c.post("/api/artifacts", content=payload,
                   headers={**headers, "Content-Type": "application/json"})
        assert r.status_code == 404
        assert r.json() == FLAT_404
    r = c.post("/api/artifacts", content=b"{not json even" + b"x" * 200,
               headers={**local(c), "Content-Type": "application/json"})
    assert r.status_code == 404                  # size first, parsing never
    assert r.json() == FLAT_404
    # under the cap the same request works
    monkeypatch.setitem(store.CAPS, "version_bytes", cap)
    assert c.post("/api/artifacts", content=payload,
                  headers={**local(c), "Content-Type": "application/json"}
                  ).status_code == 201


def test_the_gate_never_reads_the_body(make_client):
    """The proof that the refusal is in MIDDLEWARE and not in the route:
    drive the ASGI app by hand and count how many times it asks for body
    bytes. Zero, for a caller it is going to refuse."""
    import asyncio
    c = make_client()
    reads, sent = [], []

    async def receive():
        reads.append(1)
        return {"type": "http.request", "body": b'{"html": "x"}',
                "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/api/artifacts", "raw_path": b"/api/artifacts",
        "root_path": "", "query_string": b"",
        # A trusted Host: this test is about the IDENTITY gate, and Host
        # pinning is now outside it (a foreign Host is 400'd before this
        # point — see test_foreign_host_refused_before_the_identity_gate).
        "headers": [(b"host", b"127.0.0.1:3004"),
                    (b"content-type", b"application/json"),
                    (b"content-length", b"13"),
                    (b"tailscale-user-login", b"max@example.com")],
        "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 3004),
    }
    asyncio.run(c.asgi_app(scope, receive, send))
    start = [m for m in sent if m["type"] == "http.response.start"][0]
    assert start["status"] == 404
    assert reads == [], "the body was read before the caller was refused"


def test_publish_still_works_for_the_rig(make_client):
    c = make_client()
    a = publish(c)
    assert c.get(f"/raw/{a['id']}/v/1/", headers=local(c)).status_code == 200


# --- D11: metrics --------------------------------------------------------------

def test_metrics_need_an_operator_or_the_rig(make_client):
    c = make_client(operators="max@example.com")
    assert c.get("/metrics").status_code == 404                  # anonymous
    assert c.get("/metrics", headers=STRANGER).status_code == 404
    assert c.get("/metrics", headers=MAX).status_code == 200
    assert c.get("/metrics", headers=local(c)).status_code == 200


def test_an_unmatched_route_cannot_forge_a_metric_series(make_client):
    """D11. The metric label was the RAW request path, so a stranger could
    mint an unbounded number of series with names of their choosing."""
    c = make_client()
    for path in ('/nope/%22injected%22%20forged',
                 "/also/missing", "/x/y/z", "/api/artifacts/../../etc"):
        c.get(path, headers=MAX)
    m = c.get("/metrics", headers=local(c)).text
    assert 'route="<unmatched>"' in m
    for needle in ("injected", "forged", "also/missing", "/x/y/z"):
        assert needle not in m, needle
    # one series per (route, outcome), not one per path
    unmatched = [ln for ln in m.splitlines()
                 if ln.startswith("openbeast_artifact_requests_total")
                 and "<unmatched>" in ln]
    assert len(unmatched) == 1, unmatched
    # and the labels are still well formed
    for ln in m.splitlines():
        if ln.startswith("openbeast_artifact"):
            assert ln.count('"') % 2 == 0, ln


def test_metric_label_escaping():
    esc = artifact_server._metric_label
    assert esc('a"b') == 'a\\"b'
    assert esc("a\\b") == "a\\\\b"
    assert esc("a\nb") == "a\\nb"
    assert esc("a\r\nb") == "a\\nb"
    assert esc(artifact_server.UNMATCHED_ROUTE) == "<unmatched>"


# --- D12: health + listing limits ---------------------------------------------

def test_health_is_minimal_for_anyone_unauthenticated(make_client):
    """D12. It handed a stranger the absolute store path, the auth mode and
    a count of everyone's artifacts."""
    c = make_client()
    publish(c)
    r = c.get("/api/artifacts/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    for leak in ("store", "artifacts", "auth", str(store.store_root())):
        assert leak not in r.text
    # the rig still gets the detail it needs
    detail = c.get("/api/artifacts/health", headers=local(c)).json()
    assert detail["status"] == "ok" and detail["artifacts"] == 1
    assert detail["store"] == store.store_root()
    assert detail["auth"] in ("identified", "allowlist")


def test_health_does_not_scan_the_store_for_a_stranger(make_client,
                                                       monkeypatch):
    """D12. 48 ms of CPU per unauthenticated hit at 5k artifacts."""
    c = make_client()
    calls = []
    real = store.list_artifacts
    monkeypatch.setattr(store, "list_artifacts",
                        lambda **kw: (calls.append(kw), real(**kw))[1])
    assert c.get("/api/artifacts/health").json() == {"status": "ok"}
    assert calls == []
    c.get("/api/artifacts/health", headers=local(c))
    assert calls, "the operator body still counts"


def test_listing_limit_is_clamped(make_client, monkeypatch):
    """D12. limit=0 meant UNLIMITED in the store, so the cheapest request to
    write was the most expensive one to serve."""
    c = make_client()
    for i in range(3):
        publish(c, html=f"<title>T{i}</title>x")
    h = local(c)
    assert c.get("/api/artifacts?limit=0", headers=h).json()["count"] == 1
    assert c.get("/api/artifacts?limit=-9", headers=h).json()["count"] == 1
    seen = []
    real = store.list_artifacts
    monkeypatch.setattr(store, "list_artifacts",
                        lambda **kw: (seen.append(kw.get("limit")),
                                      real(**kw))[1])
    c.get("/api/artifacts?limit=999999999", headers=h)
    assert seen == [artifact_server.MAX_LIST_LIMIT]


# --- D16: single-pass template substitution -----------------------------------

IFRAME_SANDBOX = 'sandbox="allow-scripts allow-forms allow-modals allow-popups"'


def _iframe_tag(body: str) -> str:
    i = body.index("<iframe")
    return body[i:body.index(">", i) + 1]


def test_a_placeholder_in_a_title_cannot_delete_the_iframe_sandbox(make_client):
    """D16. Values were substituted one key at a time, so an inserted value
    was re-scanned for the placeholders that had not run yet. A title of
    `{{VERSION_OPTIONS}}` spliced the <option> markup into the iframe's title
    attribute, and the `>` inside it closed the <iframe> tag BEFORE its src
    and sandbox attributes — deleting the sandbox this module calls a
    security control."""
    c = make_client()
    a = publish(c, title="{{VERSION_OPTIONS}}")
    publish(c, artifact_id=a["id"], html="<p>v2</p>", label="two")
    body = c.get(f"/a/{a['id']}", headers=local(c)).text
    tag = _iframe_tag(body)
    assert f'src="/raw/{a["id"]}/v/2/"' in tag      # the iframe still loads
    assert IFRAME_SANDBOX in tag                    # ...and is still boxed in
    assert "<option" not in tag
    # the title renders as text, not as a placeholder
    assert "&#123;&#123;VERSION_OPTIONS&#125;&#125;" in body


@pytest.mark.parametrize("evil", [
    "{{RAW_URL}}", "{{ARTIFACT_ID}}", "{{DESCRIPTION}}", "{{SANDBOX}}",
    "{{VERSION}}", "{{ROWS}}", "{{COUNT}}", "{{VIEWER}}", "{{",
])
def test_no_placeholder_in_a_title_reaches_the_second_pass(make_client, evil):
    c = make_client()
    a = publish(c, title=evil)
    body = c.get(f"/a/{a['id']}", headers=local(c)).text
    tag = _iframe_tag(body)
    assert f'src="/raw/{a["id"]}/v/1/"' in tag
    assert IFRAME_SANDBOX in tag
    gallery = c.get("/", headers=local(c)).text
    assert f'href="/a/{a["id"]}"' in gallery


def test_fill_is_one_pass():
    fill = artifact_server._fill
    assert fill("{{A}}", {"A": "{{B}}", "B": "boom"}) == "{{B}}"
    assert fill("{{A}} {{B}}", {"A": "x", "B": "y"}) == "x y"
    assert fill("{{UNKNOWN}}", {"A": "x"}) == "{{UNKNOWN}}"
    # css and js braces are not placeholders
    assert fill("body{margin:0}", {}) == "body{margin:0}"
    assert fill("function(){ return {}; }", {}) == "function(){ return {}; }"


def test_esc_neutralises_braces():
    assert artifact_server._esc("{{X}}") == "&#123;&#123;X&#125;&#125;"
    assert artifact_server._esc('<b>"') == "&lt;b&gt;&quot;"


# --- D18: the token is minted after the bind ----------------------------------

def test_a_live_server_keeps_its_token(make_client, tmp_path, monkeypatch):
    """D18. A second start used to overwrite the running server's token, so
    scripts/artifact.sh authenticated with a secret nobody honoured and the
    operator was told "404 Not Found" for what was really an auth failure."""
    c = make_client()
    first = c.app_token
    token_file = tmp_path / "run" / "artifact-local.token"
    assert token_file.read_text() == first

    monkeypatch.setattr(artifact_server, "_health_answers",
                        lambda *a, **k: True)          # something IS live
    app2 = artifact_server.create_app()
    assert app2.state.local_token == first
    assert token_file.read_text() == first

    monkeypatch.setattr(artifact_server, "_health_answers",
                        lambda *a, **k: False)         # nothing is
    app3 = artifact_server.create_app()
    assert app3.state.local_token != first
    assert token_file.read_text() == app3.state.local_token


def test_main_refuses_a_taken_port_without_touching_the_token(env, monkeypatch,
                                                              tmp_path):
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    port = held.getsockname()[1]
    try:
        monkeypatch.setenv("OPENBEAST_ARTIFACT_PORT", str(port))
        run = tmp_path / "run"
        run.mkdir(parents=True, exist_ok=True)
        token_file = run / "artifact-local.token"
        token_file.write_text("the-live-servers-token")
        with pytest.raises(SystemExit):
            artifact_server.main()
        assert token_file.read_text() == "the-live-servers-token"
    finally:
        held.close()


# --- conventions --------------------------------------------------------------

def test_health_and_metrics(make_client):
    c = make_client()
    publish(c)
    h = c.get("/api/artifacts/health", headers=local(c)).json()
    assert h["status"] == "ok" and h["artifacts"] == 1
    m = c.get("/metrics", headers=local(c)).text
    assert "openbeast_artifact_requests_total" in m
    assert "openbeast_artifacts_stored 1" in m


def test_api_listing_shape(make_client):
    c = make_client()
    h = local(c)
    a = publish(c, description="d")
    body = c.get("/api/artifacts", headers=h).json()
    assert body["count"] == 1
    row = body["artifacts"][0]
    assert row["id"] == a["id"] and row["title"] == "Hello"
    assert row["versions"] == 1 and row["visibility"] == "private"
    assert row["url"].endswith(f"/a/{a['id']}")
    meta = c.get(f"/api/artifacts/{a['id']}", headers=h).json()
    assert meta["description"] == "d"
    assert meta["versions"][0]["url"].endswith(f"/a/{a['id']}/v/1")


def test_audit_log_is_0600_and_has_no_content(make_client, tmp_path):
    c = make_client()
    a = publish(c)
    c.get(f"/raw/{a['id']}/v/1/", headers=local(c))
    path = tmp_path / "run" / "artifact-audit.jsonl"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    assert len(rows) >= 2
    for row in rows:
        assert {"ts", "login", "route", "id", "n", "outcome", "ms"} <= set(row)
    pub = [r for r in rows if r["route"] == "/api/artifacts"
           and r["outcome"] == 201][0]
    assert len(pub["sha256"]) == 64 and pub["bytes"] == len(PAGE.encode())
    blob = path.read_text()
    assert "<title>" not in blob and "<p>hi</p>" not in blob


def test_unlisted_login_is_audited_too(make_client, tmp_path):
    c = make_client(operators="max@example.com")
    c.get("/", headers=STRANGER)
    rows = [json.loads(x) for x in
            (tmp_path / "run" / "artifact-audit.jsonl").read_text().splitlines()]
    denied = [r for r in rows if r["outcome"] == 404]
    assert denied and denied[-1]["login"] == "nobody@example.com"
    assert denied[-1]["denied"] == "anonymous"     # why, for the operator


# --- D23: every version resolution goes through the store ---------------------
# The store learned to survive a damaged meta.json (D14) and the SERVER kept
# re-deriving the version list from raw metadata in three more places, so the
# corruptions below turned five routes into an HTTP 500 — for the artifact's
# legitimate owner, over a record the store itself can still serve pages out
# of. `test_corrupt_record_does_not_poison_the_listing` in the store suite
# only ever covered list_artifacts(); nothing covered a route. These do.

def _meta_file(artifact_id: str) -> str:
    return os.path.join(store.store_root(), artifact_id, "meta.json")


def _corrupt(artifact_id: str, mutate) -> None:
    """Damage meta.json the way a truncated write or a hand-edit does."""
    with open(_meta_file(artifact_id), "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    mutate(meta)
    with open(_meta_file(artifact_id), "w", encoding="utf-8") as fh:
        json.dump(meta, fh)


def _set(key, value):
    def mutate(meta):
        meta[key] = value
    return mutate


def _drop(key):
    def mutate(meta):
        meta.pop(key, None)
    return mutate


def _prepend(entry):
    def mutate(meta):
        meta["versions"] = [entry] + list(meta.get("versions") or [])
    return mutate


CORRUPTIONS = {
    # `versions` is not a list at all (a dict is what a hand-merge produces)
    "versions-is-a-dict": _set("versions", {"1": {"n": 1}}),
    "versions-is-a-string": _set("versions", "v1"),
    "versions-is-missing": _drop("versions"),
    # the list is a list, but the entries are not records
    "entry-is-null": _prepend(None),
    "entry-is-a-string": _prepend("v1"),
    "n-is-not-a-number": _set("versions", [{"n": "two", "ts": "2026-01-01"}]),
    # the pointer is junk, or points at a version that is not there
    "current-points-nowhere": _set("current", 99),
    "current-is-a-string": _set("current", "latest"),
    "current-is-null": _set("current", None),
    # the record cannot even name itself — meta["id"] was a KeyError away
    # from a 500 on five routes
    "id-is-missing": _drop("id"),
    "id-is-not-a-string": _set("id", 7),
}


@pytest.mark.parametrize("corruption", sorted(CORRUPTIONS))
def test_a_corrupt_record_never_500s_any_route(make_client, corruption):
    """D23. Every route, for the OWNER, on a record whose vN directories are
    all still on disk: the answer is the page, not a server error."""
    c = make_client()
    a = publish(c, files={"app.js": "console.log(1)"})
    _corrupt(a["id"], CORRUPTIONS[corruption])
    quiet = TestClient(c.asgi_app, raise_server_exceptions=False,
                         base_url="http://127.0.0.1:3004")
    h = local(c)
    for path in ("/",
                 f"/a/{a['id']}",
                 f"/a/{a['id']}/v/1",
                 f"/raw/{a['id']}/v/1/",
                 f"/raw/{a['id']}/v/1/app.js",
                 f"/api/artifacts/{a['id']}",
                 "/api/artifacts"):
        r = quiet.get(path, headers=h)
        assert r.status_code == 200, (corruption, path, r.status_code, r.text)
    # the page still renders, from the version that really exists
    assert PAGE in quiet.get(f"/raw/{a['id']}/v/1/", headers=h).text
    assert "console.log(1)" in \
        quiet.get(f"/raw/{a['id']}/v/1/app.js", headers=h).text
    # ...and the shell still frames it, sandbox intact
    tag = _iframe_tag(quiet.get(f"/a/{a['id']}", headers=h).text)
    assert f'src="/raw/{a["id"]}/v/1/"' in tag and IFRAME_SANDBOX in tag


@pytest.mark.parametrize("corruption", sorted(CORRUPTIONS))
def test_a_corrupt_record_never_500s_a_stranger_either(make_client,
                                                       corruption):
    """The same corruptions through a caller who may NOT see the page: the
    answer must be the flat 404, never a 500 (which announces the route as
    loudly as a 405 does — D9)."""
    c = make_client(operators="max@example.com,kid@example.com")
    a = publish(c)                                    # owner: max
    _corrupt(a["id"], CORRUPTIONS[corruption])
    quiet = TestClient(c.asgi_app, raise_server_exceptions=False,
                         base_url="http://127.0.0.1:3004")
    for path in (f"/a/{a['id']}", f"/a/{a['id']}/v/1", f"/raw/{a['id']}/v/1/",
                 f"/raw/{a['id']}/v/1/app.js", f"/api/artifacts/{a['id']}"):
        r = quiet.get(path, headers=KID)
        assert r.status_code == 404, (corruption, path)
        assert r.json() == FLAT_404, (corruption, path)


def test_a_dangling_current_resolves_to_the_newest_version(make_client):
    """D23, the coercion. `current` is a pointer, not a fact: a rollback that
    lost its write, or a hand-edit, must not 404 the owner out of a page
    whose versions are all present."""
    c = make_client()
    a = publish(c)
    publish(c, artifact_id=a["id"], html="<title>Two</title>second")
    _corrupt(a["id"], _set("current", 99))
    h = local(c)
    quiet = TestClient(c.asgi_app, raise_server_exceptions=False,
                         base_url="http://127.0.0.1:3004")
    assert quiet.get(f"/a/{a['id']}", headers=h).status_code == 200
    assert 'value="2" selected' in quiet.get(f"/a/{a['id']}", headers=h).text
    assert quiet.get(f"/api/artifacts/{a['id']}", headers=h).json()["current"] == 2
    # an explicitly addressed version is still exactly what was asked for
    assert PAGE in quiet.get(f"/raw/{a['id']}/v/1/", headers=h).text


def test_a_version_the_meta_lost_is_still_served_from_disk(make_client):
    """D23/D14 together: meta's list is unusable, the vN directories are
    fine, and the store's helper falls back to them. The owner keeps their
    page instead of meeting a 500."""
    c = make_client()
    a = publish(c)
    publish(c, artifact_id=a["id"], html="<title>Two</title>second")
    _corrupt(a["id"], _set("versions", "gone"))
    h = local(c)
    quiet = TestClient(c.asgi_app, raise_server_exceptions=False,
                         base_url="http://127.0.0.1:3004")
    assert "second" in quiet.get(f"/raw/{a['id']}/v/2/", headers=h).text
    assert PAGE in quiet.get(f"/raw/{a['id']}/v/1/", headers=h).text
    body = quiet.get(f"/a/{a['id']}", headers=h)
    assert body.status_code == 200
    assert 'value="2"' in body.text and 'value="1"' in body.text
    meta = quiet.get(f"/api/artifacts/{a['id']}", headers=h).json()
    assert [v["n"] for v in meta["versions"]] == [1, 2]
    assert meta["versions"][1]["url"].endswith(f"/a/{a['id']}/v/2")


def test_the_server_asks_the_store_to_resolve_versions(make_client,
                                                       monkeypatch):
    """The mechanism, not just the symptom: every route resolves through
    store._resolvable_versions() rather than re-deriving the list itself."""
    c = make_client()
    a = publish(c, files={"app.js": "x"})
    seen = []
    real = store._resolvable_versions
    monkeypatch.setattr(store, "_resolvable_versions",
                        lambda aid, meta: (seen.append(aid),
                                           real(aid, meta))[1])
    h = local(c)
    for path in (f"/a/{a['id']}", f"/a/{a['id']}/v/1", f"/raw/{a['id']}/v/1/",
                 f"/raw/{a['id']}/v/1/app.js", f"/api/artifacts/{a['id']}"):
        assert c.get(path, headers=h).status_code == 200, path
        assert seen, f"{path} resolved versions without the store helper"
        seen.clear()


# --- D24: the slash redirect was an oracle ------------------------------------

def test_a_trailing_slash_is_not_a_route_oracle(make_client):
    """D24. Starlette answers an unmatched `/api/artifacts/` with a 307 to
    `/api/artifacts` — from the ROUTER, after the identity middleware and
    before any route code, so no auth check could suppress it. 307 here and
    404 there reads the route table out one path at a time."""
    c = make_client()
    a = publish(c)
    probes = [
        "/api/artifacts/",                       # exists without the slash
        f"/api/artifacts/{a['id']}/",
        "/api/artifacts/health/",
        f"/a/{a['id']}/",
        f"/a/{a['id']}/v/1/",
        "/metrics/",
        "/nope/",                                # does not exist either way
        "/definitely/not/a/route/",
    ]
    seen = set()
    for path in probes:
        r = c.get(path, headers=MAX, follow_redirects=False)
        assert r.status_code == 404, (path, r.status_code)
        assert r.json() == FLAT_404, path
        assert "location" not in {k.lower() for k in r.headers}, path
        seen.add((r.text, r.headers.get("content-length")))
    # byte-identical to an ordinary miss, exactly like every other refusal
    r = c.get("/a/00000000-0000-4000-8000-000000000000", headers=MAX)
    seen.add((r.text, r.headers.get("content-length")))
    assert len(seen) == 1, seen
    # and the real paths still work, for everyone who should have them
    assert c.get("/api/artifacts", headers=local(c)).status_code == 200
    assert c.get(f"/a/{a['id']}", headers=local(c)).status_code == 200


def test_the_slash_oracle_is_shut_for_writes_too(make_client):
    c = make_client()
    r = c.post("/api/artifacts/", json={"html": PAGE}, headers=local(c),
               follow_redirects=False)
    assert r.status_code == 404 and r.json() == FLAT_404
    assert "location" not in {k.lower() for k in r.headers}


# --- D27: refusals cannot grow the audit file without bound -------------------

def _rows(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _denied(path, reason):
    return [r for r in _rows(path) if r.get("denied") == reason]


def _metric(text, **labels):
    """The counter for one exact label set, or 0."""
    want = [f'{k}="{v}"' for k, v in labels.items()]
    for ln in text.splitlines():
        if ln.startswith("openbeast_artifact_requests_total") and \
                all(w in ln for w in want):
            return int(ln.rsplit(" ", 1)[1])
    return 0


def test_refusals_stop_growing_the_audit_file(make_client, tmp_path,
                                              monkeypatch):
    """D27. ~89 bytes a row, no rotation, no bound, and writable by anyone
    who can reach the port: a disk-fill primitive that needed no credentials
    at all. Past the budget, refusals of THAT REASON are counter-only.

    R5 corrected what this test asserted. The budget was one process-lifetime
    counter for every reason at once, so ~1000 anonymous GETs — about a
    second of typing — blinded the audit log to every later refusal for the
    life of the process; and the code's claim that "nothing is lost but the
    repetition" was false, which this test faithfully repeated. What is lost
    is the per-request detail. What survives is the count AND the reason.
    """
    monkeypatch.setattr(artifact_server, "DENY_AUDIT_ROWS", 3)
    c = make_client()
    publish(c)                                   # a real row, not a refusal
    path = tmp_path / "run" / "artifact-audit.jsonl"

    for _ in range(40):
        assert c.get("/api/artifacts").status_code == 404      # anonymous
    settled = path.stat().st_size
    assert len(_denied(path, "anonymous")) == 3
    # one row says where the trail went, so the operator is never puzzled —
    # and it names the reason, because the budget is per reason now.
    notes = _denied(path, "audit-budget")
    assert len(notes) == 1
    assert notes[0]["reason"] == "anonymous"
    assert "anonymous" in notes[0]["note"] and "metrics" in notes[0]["note"]

    for _ in range(40):
        c.get("/api/artifacts")
    assert path.stat().st_size == settled, "the file is still growing"

    # What is NOT lost: the count, and the REASON, as a bounded label (R5).
    # A middleware refusal never reaches the router, so the route labels as
    # the constant "<unmatched>" (D11) — no attacker-named series either.
    m = c.get("/metrics", headers=local(c)).text
    n = _metric(m, route=artifact_server.UNMATCHED_ROUTE, outcome="404",
                reason="anonymous")
    assert n >= 80, m
    # What IS lost, stated honestly: the suppressed rows themselves. The
    # comment in the server used to claim otherwise.
    assert len(_denied(path, "anonymous")) == 3 < n

    # The shipped budget is finite and the window is finite. Asserted on the
    # REAL constants, so raising either past sanity fails here instead of
    # leaving a monkeypatched test green.
    assert isinstance(artifact_server.DENY_AUDIT_ROWS, int)
    assert 0 < artifact_server.DENY_AUDIT_ROWS <= 10_000
    assert 0 < artifact_server.DENY_AUDIT_WINDOW_S <= 3600


def test_a_flood_of_one_refusal_cannot_blind_the_others(make_client, tmp_path,
                                                        monkeypatch):
    """R5, the finding itself. The budget was shared, so the cheapest refusal
    an anonymous caller can mint bought silence for every other kind — the
    `ambiguous-identity` signal D29 exists to catch included. Per reason, the
    flood only silences itself."""
    monkeypatch.setattr(artifact_server, "DENY_AUDIT_ROWS", 2)
    c = make_client()
    path = tmp_path / "run" / "artifact-audit.jsonl"

    for _ in range(30):                          # spend the anonymous budget
        assert c.get("/api/artifacts").status_code == 404
    assert len(_denied(path, "anonymous")) == 2

    two = [("tailscale-user-login", "max@example.com"),
           ("tailscale-user-login", "kid@example.com")]
    assert c.get("/", headers=two).status_code == 404
    assert len(_denied(path, "ambiguous-identity")) == 1, \
        "the signal D29 exists to catch was suppressed by an unrelated flood"

    # a third reason keeps its own budget too
    monkeypatch.setitem(store.CAPS, "version_bytes", 64)
    r = c.post("/api/artifacts", content=b'{"html": "' + b"x" * 200 + b'"}',
               headers={"Content-Type": "application/json"})
    assert r.status_code == 404
    assert len(_denied(path, "oversize")) == 1

    # every reason is its own metric series, and the set is bounded: five
    # constants, none of them attacker-named.
    m = c.get("/metrics", headers=local(c)).text
    for reason in ("anonymous", "ambiguous-identity", "oversize"):
        assert _metric(m, outcome="404", reason=reason) >= 1, reason
    reasons = {ln.split('reason="', 1)[1].split('"', 1)[0]
               for ln in m.splitlines()
               if ln.startswith("openbeast_artifact_requests_total")}
    assert reasons <= (set(artifact_server.DENY_REASONS)
                       | {"", artifact_server.DENY_OTHER}), reasons


def test_the_audit_budget_recovers_when_the_window_turns_over(
        make_client, tmp_path, monkeypatch):
    """R5. A process-lifetime budget stays spent until the next restart, so
    one flood blinded the log for as long as the server ran. It resets."""
    monkeypatch.setattr(artifact_server, "DENY_AUDIT_ROWS", 1)
    monkeypatch.setattr(artifact_server, "DENY_AUDIT_WINDOW_S", 0.05)
    c = make_client()
    path = tmp_path / "run" / "artifact-audit.jsonl"
    for _ in range(5):
        c.get("/api/artifacts")
    assert len(_denied(path, "anonymous")) == 1
    time.sleep(0.08)
    for _ in range(5):
        c.get("/api/artifacts")
    assert len(_denied(path, "anonymous")) == 2, "the window never turned over"


def test_a_refusal_reason_can_never_forge_a_metric_series(make_client):
    """R5's label is bounded by construction, not by hoping the middleware
    only ever writes constants."""
    assert artifact_server._deny_label("anonymous") == "anonymous"
    assert artifact_server._deny_label("") == ""
    assert artifact_server._deny_label(None) == ""
    for hostile in ('x" nasty="1', "../../etc", "a" * 500, 17, ["x"]):
        assert artifact_server._deny_label(hostile) == artifact_server.DENY_OTHER


def test_a_refused_operator_is_still_audited(make_client, tmp_path,
                                             monkeypatch):
    """The budget applies to callers the server cannot identify. The rig's
    own failures — an oversized publish, say — stay in the log."""
    monkeypatch.setattr(artifact_server, "DENY_AUDIT_ROWS", 0)
    c = make_client()
    monkeypatch.setitem(store.CAPS, "version_bytes", 64)
    r = c.post("/api/artifacts", content=b'{"html": "' + b"x" * 200 + b'"}',
               headers={**local(c), "Content-Type": "application/json"})
    assert r.status_code == 404
    rows = [json.loads(x) for x in
            (tmp_path / "run" / "artifact-audit.jsonl").read_text().splitlines()
            if x.strip()]
    assert [r for r in rows if r.get("denied") == "oversize"]


# --- D29: the small ones ------------------------------------------------------

def test_head_on_health_is_not_a_404(make_client):
    """D29. FastAPI does not add HEAD to an @app.get route, so `curl -I` —
    the cheapest liveness probe there is — reported a healthy server down."""
    c = make_client()
    r = c.head("/api/artifacts/health")
    assert r.status_code == 200
    assert r.content == b""
    assert c.head("/api/artifacts/health", headers=local(c)).status_code == 200
    # GET is unchanged, body and all
    assert c.get("/api/artifacts/health").json() == {"status": "ok"}


def test_two_identity_headers_are_refused_not_resolved(make_client):
    """D29. Starlette keeps every copy of a header and `.get()` returns the
    first, so two logins used to authorise silently as whichever one a proxy
    chain happened to put first. Ambiguous identity is no identity."""
    c = make_client(operators="max@example.com,kid@example.com")
    a = publish(c, headers=local(c, MAX))
    two = [("tailscale-user-login", "max@example.com"),
           ("tailscale-user-login", "kid@example.com")]
    for path in ("/", f"/a/{a['id']}", "/api/artifacts", "/metrics",
                 "/api/artifacts/health"):
        r = c.get(path, headers=two)
        assert r.status_code == 404, path
        assert r.json() == FLAT_404, path
    # the order does not rescue it either way round
    assert c.get(f"/a/{a['id']}", headers=two[::-1]).status_code == 404
    # ...nor does doubling the locality token on a write
    r = c.post("/api/artifacts", json={"html": PAGE},
               headers=[("x-openbeast-local", c.app_token),
                        ("x-openbeast-local", c.app_token),
                        ("content-type", "application/json")])
    assert r.status_code == 404
    # one header each is still fine
    assert c.get(f"/a/{a['id']}", headers=MAX).status_code == 200


def test_the_ambiguity_is_audited_with_its_reason(make_client, tmp_path):
    c = make_client()
    c.get("/", headers=[("tailscale-user-login", "max@example.com"),
                        ("tailscale-user-login", "kid@example.com")])
    rows = [json.loads(x) for x in
            (tmp_path / "run" / "artifact-audit.jsonl").read_text().splitlines()
            if x.strip()]
    assert rows[-1]["denied"] == "ambiguous-identity"


def test_an_ownership_refusal_is_indistinguishable_from_a_miss(make_client):
    """D29. `400 "not your artifact"` confirmed a page exists at an id where
    a nonexistent one answers 404 — a map of the store, one id at a time, for
    anyone holding the locality token."""
    c = make_client(operators="boss@example.com,max@example.com")
    a = publish(c, headers=local(c, MAX))          # owner: max
    miss = c.patch("/api/artifacts/00000000-0000-4000-8000-000000000000",
                   json={"description": "x"}, headers=local(c))
    hit = c.patch(f"/api/artifacts/{a['id']}", json={"visibility": "tailnet"},
                  headers=local(c))                # boss, not max
    assert hit.status_code == miss.status_code == 404
    assert hit.text == miss.text
    assert hit.headers.get("content-length") == miss.headers.get("content-length")
    # the republish path says exactly as little
    r = c.post("/api/artifacts", json={"html": PAGE, "artifact_id": a["id"]},
               headers=local(c))
    assert r.status_code == 404 and r.json() == FLAT_404
    # and a real failure is still a real failure for the rig
    r = c.post("/api/artifacts", json={"title": "no html"}, headers=local(c))
    assert r.status_code == 400 and "html" in r.json()["detail"]


# --- v1.4.0 adversarial review ----------------------------------------------

def test_a_foreign_host_is_refused_before_the_identity_gate(make_client):
    """DNS rebinding. This server had NO Host validation while beast-chat,
    written the same week and published the same way, had it.

    A page loaded from `http://evil.example:3004/` that then rebinds that name
    to 127.0.0.1 becomes SAME-ORIGIN with this server — and same-origin lets
    it set arbitrary request headers, including the `Tailscale-User-Login`
    header that IS the read gate. On a default rig the owner string is the
    public constant LOCAL_LOGIN, so nothing had to be guessed. A browser
    cannot forge `Host`; that is what makes pinning it the fix.
    """
    c = make_client()
    a = publish(c)
    evil = TestClient(c.asgi_app, base_url="http://evil.example:3004",
                      raise_server_exceptions=False)
    me = {"Tailscale-User-Login": artifact_server.LOCAL_LOGIN}
    for path in ("/", f"/a/{a['id']}", "/api/artifacts",
                 f"/api/artifacts/{a['id']}", f"/raw/{a['id']}/v/1/",
                 "/api/artifacts/health"):
        r = evil.get(path, headers=me)
        assert r.status_code == 400, f"{path} answered a rebound Host: {r.status_code}"
    # and the trusted client is unaffected
    assert c.get("/api/artifacts", headers=me).status_code == 200


def test_a_rebound_host_never_reaches_the_audit_log(make_client, tmp_path):
    """Host pinning is OUTSIDE the audit middleware, so a rebinding flood
    cannot write rows either. Ordering, asserted rather than assumed."""
    c = make_client()
    path = tmp_path / "run" / "artifact-audit.jsonl"
    before = path.stat().st_size if path.exists() else 0
    evil = TestClient(c.asgi_app, base_url="http://evil.example:3004",
                      raise_server_exceptions=False)
    for _ in range(20):
        assert evil.get("/api/artifacts").status_code == 400
    after = path.stat().st_size if path.exists() else 0
    assert after == before, "a refused Host still wrote audit rows"


def test_anonymous_health_cannot_grow_the_audit_file(make_client, tmp_path,
                                                     monkeypatch):
    """The other half of D27, which D27 missed.

    `/api/artifacts/health` is deliberately exempt from the anonymity gate,
    so a health hit never sets `denied` — and the budget was keyed on
    `denied`. The row therefore took the un-budgeted branch: an
    unauthenticated, unrotated, unbounded append. This is the same assertion
    test_refusals_stop_growing_the_audit_file makes for /api/artifacts, which
    is exactly why its absence here was the signpost.
    """
    monkeypatch.setattr(artifact_server, "DENY_AUDIT_ROWS", 3)
    c = make_client()
    path = tmp_path / "run" / "artifact-audit.jsonl"
    for _ in range(40):
        assert c.get("/api/artifacts/health").status_code == 200
    settled = path.stat().st_size
    for _ in range(40):
        assert c.get("/api/artifacts/health").status_code == 200
    assert path.stat().st_size == settled, "the file is still growing"
    notes = _denied(path, "audit-budget")
    assert len(notes) == 1 and notes[0]["reason"] == "anon-success"
    # liveness is NOT what got budgeted — the probe still answers
    assert c.get("/api/artifacts/health").json()["status"] == "ok"


def test_an_audit_row_can_never_carry_an_8kb_identity(make_client, tmp_path):
    """A bounded row COUNT with an unbounded row SIZE is not a bound: an
    8 KB login header produced an 8 KB audit row, so the D27 budget still
    bought ~8 MB per reason per window. The raw path beside it was already
    capped; the login was not."""
    c = make_client()
    path = tmp_path / "run" / "artifact-audit.jsonl"
    huge = {"Tailscale-User-Login": "A" * 8000}
    c.get("/api/artifacts/health", headers=huge)
    c.get("/api/artifacts", headers=huge)          # a refusal, also capped
    rows = _rows(path)
    assert rows, "nothing was audited at all"
    assert max(len(r.get("login") or "") for r in rows) <= 128
    assert max(len(json.dumps(r)) for r in rows) < 600


def test_a_failed_mixed_patch_never_widens_visibility(make_client):
    """Three independent store writes, no rollback. With `visibility` applied
    FIRST, `{"visibility": "tailnet", "current": 999}` answered 400 *having
    already made the artifact tailnet-readable* — the caller is told the
    request failed while the page is now shared, at a pointer they were
    trying to move. The only widening write goes last."""
    c = make_client()
    a = publish(c)
    aid = a["id"]
    assert store.get_meta(aid)["visibility"] == "private"
    r = c.patch(f"/api/artifacts/{aid}",
                json={"visibility": "tailnet", "current": 999},
                headers=local(c))
    assert r.status_code == 400
    meta = store.get_meta(aid)
    assert meta["visibility"] == "private", "the failed patch widened it anyway"
    assert not store.can_view(meta, "stranger@example.com")
    # description is non-widening, so a partial there is acceptable — but the
    # valid single-field patch must still work
    assert c.patch(f"/api/artifacts/{aid}", json={"visibility": "tailnet"},
                   headers=local(c)).status_code == 200
    assert store.get_meta(aid)["visibility"] == "tailnet"
