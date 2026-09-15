#!/usr/bin/env python3
"""beast-artifact server (agents/artifact_server.py) — isolation, auth, API.

The load-bearing tests here are the two CSP assertions: the exact policy on
/raw/ (model-authored content) and the different, stricter policy on our own
shell and gallery. They pin the strings character for character on purpose —
if someone ever adds `allow-same-origin`, drops `connect-src 'none'`, or
widens the CDN allowlist, this file fails loudly rather than the isolation
quietly disappearing.

Also covers:
  - publish → /raw/ round trip, skeleton applied at serve time
  - republish: same URL, v2 current, v1 still served
  - supporting files: content type from the extension, nosniff, traversal 404
  - read auth: unlisted Tailscale-User-Login → 404 on every route
  - a private artifact of another owner → 404; tailnet visibility → 200
  - write auth: POST/PATCH/DELETE without the locality token → 404, with → 201
  - caps rejected with a 400 and a readable message
  - audit log: 0600, one row per request, no HTML inside
  - /api/artifacts/health and /metrics

Run: pytest tests/test_artifact_server.py
"""
import json
import os
import sys

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
    monkeypatch.delenv("OPENBEAST_ARTIFACT_OPERATORS", raising=False)
    monkeypatch.delenv("OPENBEAST_CHAT_OPERATORS", raising=False)
    return tmp_path


def make_client(operators: str = ""):
    if operators:
        os.environ["OPENBEAST_ARTIFACT_OPERATORS"] = operators
    app = artifact_server.create_app()
    c = TestClient(app)
    c.app_token = app.state.local_token          # type: ignore[attr-defined]
    return c


def local(c, extra=None):
    h = {"X-OpenBeast-Local": c.app_token}
    h.update(extra or {})
    return h


def publish(c, **kw):
    body = {"html": PAGE}
    body.update(kw)
    r = c.post("/api/artifacts", json=body, headers=local(c))
    assert r.status_code == 201, r.text
    return r.json()


# --- isolation (the load-bearing part) ---------------------------------------

def test_raw_csp_is_exactly_the_pinned_policy(env):
    c = make_client()
    a = publish(c)
    r = c.get(f"/raw/{a['id']}/v/1/")
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


def test_raw_headers_on_supporting_files_too(env):
    c = make_client()
    a = publish(c, files={"app.js": "console.log(1)"})
    r = c.get(f"/raw/{a['id']}/v/1/app.js")
    assert r.status_code == 200
    assert r.text == "console.log(1)"
    assert r.headers["content-security-policy"] == EXPECTED_RAW_CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-type"].startswith("text/javascript") or \
        r.headers["content-type"].startswith("application/javascript")


def test_shell_and_gallery_carry_a_different_stricter_policy(env):
    c = make_client()
    a = publish(c)
    for path in ("/", f"/a/{a['id']}", f"/a/{a['id']}/v/1"):
        r = c.get(path)
        assert r.status_code == 200, path
        csp = r.headers["content-security-policy"]
        assert csp == artifact_server.SHELL_CSP
        assert csp != EXPECTED_RAW_CSP
        assert "frame-ancestors 'none'" in csp   # our UI is never framed
        assert "script-src 'self' 'unsafe-inline'" in csp
        assert "sandbox" not in csp              # sandboxing our own UI = broken
        assert "cdnjs.cloudflare.com" not in csp
        assert r.headers["x-frame-options"] == "DENY"


def test_shell_embeds_the_raw_route_in_a_sandboxed_iframe(env):
    c = make_client()
    a = publish(c)
    body = c.get(f"/a/{a['id']}").text
    assert f'src="/raw/{a["id"]}/v/1/"' in body
    assert 'sandbox="allow-scripts allow-forms allow-modals allow-popups"' in body


def test_head_returns_the_headers_without_a_body(env):
    """`curl -I` and header probes must see the policy, not a 405."""
    c = make_client()
    a = publish(c)
    r = c.head(f"/raw/{a['id']}/v/1/")
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == EXPECTED_RAW_CSP
    assert r.content == b""


def test_unknown_content_type_defaults_to_octet_stream(env):
    c = make_client()
    a = publish(c, files={"blob.weird": {"b64": "AAE="}})
    r = c.get(f"/raw/{a['id']}/v/1/blob.weird")
    assert r.headers["content-type"] == "application/octet-stream"


# --- serving ------------------------------------------------------------------

def test_publish_round_trip_and_skeleton(env):
    c = make_client()
    a = publish(c, description="a greeting", favicon="👋")
    assert a["url"] == f"https://beast:8446/a/{a['id']}"
    assert a["title"] == "Hello" and a["version"] == 1

    raw = c.get(f"/raw/{a['id']}/v/1/").text
    assert raw.startswith("<!doctype html>")     # skeleton applied at SERVE time
    assert "width=device-width" in raw
    assert PAGE in raw
    # ...and never stored
    on_disk, _ = store.read_file(a["id"], 1)
    assert on_disk.decode() == PAGE

    shell = c.get(f"/a/{a['id']}").text
    assert "Hello" in shell and "a greeting" in shell


def test_theme_query_stamps_the_skeleton(env):
    c = make_client()
    a = publish(c)
    assert 'data-theme="dark"' in c.get(f"/raw/{a['id']}/v/1/?theme=dark").text
    assert "data-theme" not in c.get(f"/raw/{a['id']}/v/1/").text
    assert "data-theme" not in c.get(f"/raw/{a['id']}/v/1/?theme=bogus").text


def test_republish_keeps_url_adds_version(env):
    c = make_client()
    a = publish(c)
    b = publish(c, html="<title>Two</title>second", artifact_id=a["id"],
                label="pass 2")
    assert b["id"] == a["id"] and b["version"] == 2

    assert "second" in c.get(f"/raw/{a['id']}/v/2/").text
    assert PAGE in c.get(f"/raw/{a['id']}/v/1/").text      # v1 still served
    shell = c.get(f"/a/{a['id']}").text
    assert 'value="2" selected' in shell and 'value="1"' in shell
    assert "pass 2" in shell
    meta = c.get(f"/api/artifacts/{a['id']}").json()
    assert meta["current"] == 2 and len(meta["versions"]) == 2

    # rollback moves the pointer; both versions stay reachable
    r = c.patch(f"/api/artifacts/{a['id']}", json={"current": 1},
                headers=local(c))
    assert r.status_code == 200 and r.json()["current"] == 1
    assert 'value="1" selected' in c.get(f"/a/{a['id']}").text
    assert "second" in c.get(f"/raw/{a['id']}/v/2/").text


def test_missing_things_are_404(env):
    c = make_client()
    a = publish(c)
    assert c.get("/a/00000000-0000-4000-8000-000000000000").status_code == 404
    assert c.get(f"/a/{a['id']}/v/7").status_code == 404
    assert c.get(f"/raw/{a['id']}/v/7/").status_code == 404
    assert c.get(f"/raw/{a['id']}/v/1/nope.js").status_code == 404
    assert c.get(f"/raw/{a['id']}/v/1/../../meta.json").status_code == 404
    assert c.get("/a/not a valid id").status_code == 404


def test_gallery_lists_artifacts(env):
    c = make_client()
    publish(c, html="<title>Alpha</title>a", description="first")
    publish(c, html="<title>Beta</title>b")
    body = c.get("/").text
    assert "Alpha" in body and "Beta" in body and "first" in body
    assert "2 published" in body


# --- read auth ----------------------------------------------------------------

def test_unlisted_login_gets_404_everywhere(env):
    c = make_client(operators="max@example.com,kid@example.com")
    a = publish(c, owner="max@example.com", visibility="tailnet")
    for path in ("/", f"/a/{a['id']}", f"/a/{a['id']}/v/1",
                 f"/raw/{a['id']}/v/1/", "/api/artifacts",
                 f"/api/artifacts/{a['id']}"):
        r = c.get(path, headers=STRANGER)
        assert r.status_code == 404, path          # never 403
    # a listed operator sees the same paths
    for path in ("/", f"/a/{a['id']}", f"/raw/{a['id']}/v/1/",
                 "/api/artifacts"):
        assert c.get(path, headers=KID).status_code == 200, path


def test_private_artifact_of_another_owner_is_404(env):
    c = make_client(operators="max@example.com,kid@example.com")
    a = publish(c, owner="max@example.com")        # default: private
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


def test_no_operators_configured_allows_everyone(env):
    """Single-user default: no allowlist, so nobody is turned away at the
    door. Ownership still governs individual artifacts — an unowned CLI
    publish is visible to all, max's private page stays his."""
    c = make_client()
    unowned = publish(c)                           # CLI publish, no operators
    assert store.get_meta(unowned["id"])["owner"] is None
    assert c.get(f"/a/{unowned['id']}").status_code == 200
    assert c.get(f"/a/{unowned['id']}", headers=STRANGER).status_code == 200
    mine = publish(c, owner="max@example.com")
    assert c.get(f"/a/{mine['id']}", headers=MAX).status_code == 200
    assert c.get(f"/a/{mine['id']}", headers=STRANGER).status_code == 404


def test_chat_operators_is_the_fallback_allowlist(env, monkeypatch):
    monkeypatch.setenv("OPENBEAST_CHAT_OPERATORS", "max@example.com")
    c = make_client()
    assert c.get("/", headers=MAX).status_code == 200
    assert c.get("/", headers=STRANGER).status_code == 404


# --- write auth ---------------------------------------------------------------

def test_writes_need_the_locality_token(env):
    c = make_client(operators="max@example.com")
    a = publish(c, owner="max@example.com")

    # no token: 404 on every write route, even for a listed operator
    assert c.post("/api/artifacts", json={"html": PAGE},
                  headers=MAX).status_code == 404
    assert c.patch(f"/api/artifacts/{a['id']}", json={"visibility": "tailnet"},
                   headers=MAX).status_code == 404
    assert c.delete(f"/api/artifacts/{a['id']}", headers=MAX).status_code == 404
    # a wrong token is no better
    assert c.post("/api/artifacts", json={"html": PAGE},
                  headers={"X-OpenBeast-Local": "deadbeef"}).status_code == 404
    # a malformed body from the tailnet must not leak a 422 either
    assert c.post("/api/artifacts", json={"nope": 1},
                  headers=MAX).status_code == 404
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


def test_local_token_file_is_0600(env, tmp_path):
    make_client()
    path = tmp_path / "run" / "artifact-local.token"
    assert path.exists()
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_publish_owner_defaults_to_the_caller(env):
    c = make_client(operators="max@example.com,kid@example.com")
    a = publish(c, **{})                               # no owner, no login
    assert store.get_meta(a["id"])["owner"] == "max@example.com"  # 1st operator
    r = c.post("/api/artifacts", json={"html": PAGE}, headers=local(c, KID))
    assert store.get_meta(r.json()["id"])["owner"] == "kid@example.com"


# --- caps + validation --------------------------------------------------------

def test_caps_rejected_with_400(env):
    c = make_client()
    big = "<p>" + "x" * store.CAPS["page_bytes"]
    r = c.post("/api/artifacts", json={"html": big}, headers=local(c))
    assert r.status_code == 400 and "page cap" in r.json()["detail"]

    files = {f"f{i}.txt": "x" for i in range(store.CAPS["files"] + 1)}
    r = c.post("/api/artifacts", json={"html": PAGE, "files": files},
               headers=local(c))
    assert r.status_code == 400 and "file cap" in r.json()["detail"]


def test_path_traversal_in_a_files_key_is_rejected(env):
    c = make_client()
    for bad in ("../escape.js", "/etc/cron.d/x", "a/../../b.js", "index.html"):
        r = c.post("/api/artifacts", json={"html": PAGE, "files": {bad: "x"}},
                   headers=local(c))
        assert r.status_code == 400, bad
    assert store.list_artifacts() == []
    root = store.store_root()
    assert os.listdir(root) == [] or os.listdir(root) == ["index.jsonl"]


def test_publish_requires_html(env):
    c = make_client()
    r = c.post("/api/artifacts", json={"title": "x"}, headers=local(c))
    assert r.status_code == 400


def test_base64_page_and_files(env):
    c = make_client()
    import base64
    a = publish(c, html=None,
                html_b64=base64.b64encode(PAGE.encode()).decode(),
                files={"logo.png": {"b64": base64.b64encode(
                    b"\x89PNG\r\n\x1a\n").decode()}})
    assert PAGE in c.get(f"/raw/{a['id']}/v/1/").text
    r = c.get(f"/raw/{a['id']}/v/1/logo.png")
    assert r.content == b"\x89PNG\r\n\x1a\n"
    assert r.headers["content-type"] == "image/png"


# --- conventions --------------------------------------------------------------

def test_health_and_metrics(env):
    c = make_client()
    publish(c)
    h = c.get("/api/artifacts/health").json()
    assert h["status"] == "ok" and h["artifacts"] == 1 and h["auth"] == "open"
    m = c.get("/metrics").text
    assert "openbeast_artifact_requests_total" in m
    assert "openbeast_artifacts_stored 1" in m


def test_api_listing_shape(env):
    c = make_client()
    a = publish(c, description="d")
    body = c.get("/api/artifacts").json()
    assert body["count"] == 1
    row = body["artifacts"][0]
    assert row["id"] == a["id"] and row["title"] == "Hello"
    assert row["versions"] == 1 and row["visibility"] == "private"
    assert row["url"].endswith(f"/a/{a['id']}")
    meta = c.get(f"/api/artifacts/{a['id']}").json()
    assert meta["description"] == "d"
    assert meta["versions"][0]["url"].endswith(f"/a/{a['id']}/v/1")


def test_audit_log_is_0600_and_has_no_content(env, tmp_path):
    c = make_client()
    a = publish(c)
    c.get(f"/raw/{a['id']}/v/1/")
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


def test_unlisted_login_is_audited_too(env, tmp_path):
    c = make_client(operators="max@example.com")
    c.get("/", headers=STRANGER)
    rows = [json.loads(x) for x in
            (tmp_path / "run" / "artifact-audit.jsonl").read_text().splitlines()]
    denied = [r for r in rows if r["outcome"] == 404]
    assert denied and denied[-1]["login"] == "nobody@example.com"
