#!/usr/bin/env python3
"""beast-artifact — regressions for the 2026-09-30 adversarial review.

Every test here failed on origin/main 7699117 (or asserts a feature that did
not exist there), and each names the finding it pins. They build their own
case: a temp store, a temp run dir, an in-process app, and — where a device
key matters — a hand-written .run/clients.json. No server is started; the
browser-level half lives in tests/test_artifact_browser.py and the CLI half in
tests/test_artifact_cli_live.py.

Run: pytest tests/test_artifact_review_0930.py
"""
import base64
import errno
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

import artifact as store          # noqa: E402
import artifact_server            # noqa: E402

PAGE = "<title>Hello</title><p>hi</p>"
MAX = {"Tailscale-User-Login": "max@example.com"}
KID = {"Tailscale-User-Login": "kid@example.com"}
FLAT_404 = {"detail": "Not Found"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("OPENBEAST_ARTIFACT_BASE_URL", "https://beast:8446")
    monkeypatch.setenv("OPENBEAST_ARTIFACT_PORT", "39918")
    monkeypatch.setenv("OPENBEAST_CONF", str(tmp_path / "absent.conf"))
    monkeypatch.setenv("OPENBEAST_CHAT_BASE_URL", "off")
    for var in ("OPENBEAST_ARTIFACT_OPERATORS", "OPENBEAST_CHAT_OPERATORS",
                "OPENBEAST_ARTIFACT_ADMINS", "OPENBEAST_ARTIFACT_RETAIN_DAYS",
                "OPENBEAST_BIND", "OPENBEAST_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.fixture()
def make_client(env, monkeypatch):
    def _make(operators: str = "", peer="127.0.0.1",
              base="http://127.0.0.1:3004"):
        if operators:
            monkeypatch.setenv("OPENBEAST_ARTIFACT_OPERATORS", operators)
        else:
            monkeypatch.delenv("OPENBEAST_ARTIFACT_OPERATORS", raising=False)
        app = artifact_server.create_app()
        c = TestClient(app, client=(peer, 50000), base_url=base)
        c.token = app.state.local_token
        c.app = app
        c.tmp = env
        return c
    return _make


def local(c, extra=None):
    h = {"X-OpenBeast-Local": c.token}
    h.update(extra or {})
    return h


def publish(c, headers=None, **kw):
    body = {"html": PAGE}
    body.update(kw)
    r = c.post("/api/artifacts", json=body, headers=headers or local(c))
    assert r.status_code == 201, r.text
    return r.json()


def enroll(tmp_path, dev_id="phone", scopes=("artifact",), revoked=False):
    key = f"key-{dev_id}-{'-'.join(scopes)}-{int(revoked)}"
    path = tmp_path / "run" / "clients.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        doc = {"version": 1, "devices": []}
    doc["devices"] = [d for d in doc["devices"] if d.get("id") != dev_id]
    doc["devices"].append({
        "id": dev_id, "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "scopes": list(scopes), "revoked_at": "2026-01-01" if revoked else None})
    path.write_text(json.dumps(doc))
    return {"Authorization": f"Bearer {key}"}


def page_rows(html: str) -> int:
    """Gallery rows actually rendered — the template's own comment documents
    the row markup, so it is stripped before counting."""
    return re.sub(r"<!--.*?-->", "", html, flags=re.S).count('<li class="row"')


def audit_rows(tmp_path):
    path = tmp_path / "run" / "artifact-audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def ledger(tmp_path):
    path = tmp_path / "files" / "artifacts" / "index.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def legacy_local_page(aid="old-report", visibility="private"):
    """A page exactly as a pre-F-A1 rig left it: owner "local"."""
    token = store.set_owner_override("local")
    try:
        store.publish(PAGE, artifact_id=aid, visibility=visibility)
    finally:
        store.reset_owner_override(token)
    meta = store.get_meta(aid)
    meta["owner"] = "local"
    store._write_meta(aid, meta)
    return aid


# --- F-A1: the rig principal, the migration, the admin path -------------------

def test_legacy_local_pages_are_migrated_to_the_rig_and_audited(make_client):
    """artifact-browser-1 / correctness-01 / integration-ops-1: 14 of 15 pages
    on Max's rig were owner "local", and setting ARTIFACT_OPERATORS stranded
    them — no principal could present "local" any more."""
    store.store_root()
    aid = legacy_local_page()
    c = make_client(operators="max@example.com")
    assert store.get_meta(aid)["owner"] == "rig"
    reown = [r for r in ledger(c.tmp) if r.get("event") == "reown"]
    assert [r["id"] for r in reown] == [aid]
    assert any(r.get("event") == "migrate-owner" and r["count"] == 1
               for r in audit_rows(c.tmp))
    # idempotent: a second start finds nothing to do
    make_client(operators="max@example.com")
    assert len([r for r in ledger(c.tmp) if r.get("event") == "reown"]) == 1
    # the operator can now open it from the phone...
    assert c.get(f"/a/{aid}", headers=MAX).status_code == 200
    # ...and the rig can still manage it (it returned 404 before)
    r = c.patch(f"/api/artifacts/{aid}", json={"visibility": "tailnet"},
                headers=local(c))
    assert r.status_code == 200, r.text
    assert c.delete(f"/api/artifacts/{aid}", headers=local(c)).status_code == 200


def test_an_unmigrated_local_owner_is_still_the_rig(make_client):
    """The migration is best effort; _owner_of reads "local" as the rig in
    the meantime, so a page it could not rewrite is not stranded either."""
    c = make_client(operators="max@example.com")
    aid = legacy_local_page("late-page")          # written AFTER the start
    assert store.get_meta(aid)["owner"] == "local"
    assert c.get(f"/a/{aid}", headers=MAX).status_code == 200
    assert c.get(f"/api/artifacts/{aid}", headers=local(c)).json()["owner"] == "rig"


def test_no_operator_means_nobody_is_auto_trusted_and_publish_says_so(make_client):
    """With no allowlist the first identified tailnet login is NOT the admin:
    the rig's private page stays closed to it, and the publisher is told why
    and how to fix it, instead of handing out a link that 404s on the phone."""
    c = make_client()
    a = publish(c)
    assert a["owner"] == "rig" and a["visibility"] == "private"
    assert "ARTIFACT_OPERATORS" in a["notice"]
    assert c.get(f"/a/{a['id']}", headers=MAX).status_code == 404
    # ...but once configured, the SAME page opens for the operator
    c2 = make_client(operators="max@example.com")
    assert c2.get(f"/a/{a['id']}", headers=MAX).status_code == 200
    assert publish(c2)["notice"] == ""


def test_the_rig_names_are_never_a_header_identity(make_client):
    """The rig principal owns every CLI publish, so presenting it by header
    would be a read password — in BOTH modes, even if a careless allowlist
    lists it."""
    c = make_client()
    a = publish(c)
    for name in ("rig", "RIG", "local", " Local "):
        r = c.get(f"/a/{a['id']}", headers={"Tailscale-User-Login": name})
        assert r.status_code == 404, name
    c2 = make_client(operators="local,rig,max@example.com")
    for name in ("rig", "local"):
        r = c2.get(f"/a/{a['id']}", headers={"Tailscale-User-Login": name})
        assert r.status_code == 404, name


def test_the_admin_sees_every_page_and_the_owner_of_each(make_client):
    """correctness-03: a page owned by a WebUI email that is not the tailnet
    login was unmanageable by anyone. The admin (first operator, or the rig)
    lists it with its owner, opens it, and can hand it over."""
    c = make_client(operators="max@example.com,kid@example.com")
    token = store.set_owner_override("admin@localhost.example")
    try:
        webui = store.publish(PAGE, title="From WebUI")
    finally:
        store.reset_owner_override(token)
    rows = c.get("/api/artifacts", headers=MAX).json()
    assert rows["admin"] is True
    assert [(r["id"], r["owner"]) for r in rows["artifacts"]] == \
        [(webui["id"], "admin@localhost.example")]
    assert c.get(f"/a/{webui['id']}", headers=MAX).status_code == 200
    # a plain operator sees nothing of it
    assert c.get("/api/artifacts", headers=KID).json()["count"] == 0
    assert c.get(f"/a/{webui['id']}", headers=KID).status_code == 404
    # hand it to kid: admin-only, audited in the ledger
    r = c.patch(f"/api/artifacts/{webui['id']}",
                json={"owner": "kid@example.com"}, headers=local(c))
    assert r.status_code == 200 and r.json()["owner"] == "kid@example.com"
    assert c.get(f"/a/{webui['id']}", headers=KID).status_code == 200
    ev = [x for x in ledger(c.tmp) if x.get("event") == "owner"]
    assert ev and ev[-1]["to"] == "kid@example.com"
    # a non-admin cannot chown — not even their own page — flat 404
    phone = enroll(c.tmp)
    r = c.patch(f"/api/artifacts/{webui['id']}", json={"owner": "x@example.com"},
                headers={**KID, **phone})
    assert r.status_code == 404 and r.json() == FLAT_404
    # and a garbage owner is refused for the admin too
    r = c.patch(f"/api/artifacts/{webui['id']}", json={"owner": "@"},
                headers=local(c))
    assert r.status_code == 400


def test_artifact_admins_names_the_admins(make_client, monkeypatch):
    c = make_client(operators="max@example.com,kid@example.com")
    a = publish(c)
    monkeypatch.setenv("OPENBEAST_ARTIFACT_ADMINS", "kid@example.com")
    assert c.get(f"/a/{a['id']}", headers=KID).status_code == 200
    assert c.get(f"/a/{a['id']}", headers=MAX).status_code == 404


# --- security-1 / integration-ops-4: a LAN bind address ----------------------

def test_a_lan_bind_trusts_tailscale_serve_on_the_same_host(make_client,
                                                            monkeypatch):
    """With BIND_HOST=192.168.1.50, setup-tailscale mounts :8446 at that
    address and tailscaled connects FROM it. Every tailnet reader used to be
    anonymous — 404 on everything, `tailnet` pages included."""
    monkeypatch.setenv("OPENBEAST_BIND", "192.168.1.50")
    c = make_client(peer="192.168.1.50", base="http://192.168.1.50:3004")
    a = publish(c, visibility="tailnet")
    assert c.get(f"/a/{a['id']}", headers=MAX).status_code == 200
    # negative control: a DIFFERENT LAN host forging the header is anonymous
    lan = TestClient(c.app, client=("192.168.1.60", 50000),
                     base_url="http://192.168.1.50:3004")
    r = lan.get(f"/a/{a['id']}", headers=MAX)
    assert r.status_code == 404 and r.json() == FLAT_404


# --- security-2 / browser-7 / correctness-04: the audit row -------------------

def test_audit_rows_name_the_resolved_principal_method_and_publish(make_client):
    c = make_client(operators="max@example.com")
    a = publish(c)
    c.patch(f"/api/artifacts/{a['id']}", json={"visibility": "tailnet"},
            headers=local(c))
    c.get(f"/api/artifacts/{a['id']}", headers=local(c))
    rows = audit_rows(c.tmp)
    pub = [r for r in rows if r.get("route") == "/api/artifacts"
           and r.get("outcome") == 201][0]
    assert pub["login"] == "rig" and pub["local"] is True
    assert pub["method"] == "POST"
    assert pub["id"] == a["id"] and pub["n"] == 1 and pub["owner"] == "rig"
    patch = [r for r in rows if r.get("method") == "PATCH"][0]
    get = [r for r in rows if r.get("method") == "GET"
           and r.get("route") == "/api/artifacts/{artifact_id}"][0]
    assert patch["changed"] == {"visibility": "private->tailnet"}
    assert "changed" not in get
    # the ledger records the widening act too
    assert any(x.get("event") == "visibility" and x["to"] == "tailnet"
               for x in ledger(c.tmp))


def test_a_forged_refused_login_is_not_logged_as_the_victim(make_client):
    c = make_client(operators="max@example.com")
    lan = TestClient(c.app, client=("10.0.0.9", 50000),
                     base_url="http://127.0.0.1:3004")
    lan.get("/api/artifacts", headers=MAX)
    row = [r for r in audit_rows(c.tmp) if r.get("denied")][-1]
    assert row["login"] is None
    assert row["claimed_login"] == "max@example.com"
    assert row["peer"] == "10.0.0.9"


# --- security-3: the shell/gallery CSP admits only its own scripts ----------

@pytest.mark.parametrize("path", ["/", "/a/{id}"])
def test_ui_script_src_is_exactly_the_templates_hashes(make_client, path):
    c = make_client()
    a = publish(c, title='x</title><script>alert(1)</script>')
    r = c.get(path.format(id=a["id"]), headers=local(c))
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    src = re.search(r"script-src ([^;]*);", csp).group(1).split()
    blocks = re.findall(r"<script>(.*?)</script>",
                        re.sub(r"<!--.*?-->", "", r.text, flags=re.S), re.S)
    want = sorted("'sha256-%s'" % base64.b64encode(
        hashlib.sha256(b.encode()).digest()).decode() for b in blocks)
    assert sorted(src) == want and want      # every block the page runs...
    assert "'self'" not in src and "'unsafe-inline'" not in src
    # ...and the title's script was escaped, never a block of its own
    assert "<script>alert(1)" not in r.text


def test_a_template_block_carrying_a_placeholder_is_not_hashed():
    assert artifact_server.inline_script_hashes(
        "<script>var x='{{TITLE}}'</script><script>ok()</script>") == [
        base64.b64encode(hashlib.sha256(b"ok()").digest()).decode()]
    assert artifact_server.inline_script_hashes(
        "<!-- <script>no()</script> -->") == []


# --- browser-2/3/4/6/9/10: rendering details ---------------------------------

def test_raw_policy_admits_eval_and_escaping_popups():
    """browser-4 (Alpine/Vue compile at runtime) and browser-3 (a popup of an
    external link must be the real site, not an opaque-origin copy)."""
    assert "'unsafe-eval'" in artifact_server.RAW_CSP
    assert "allow-popups-to-escape-sandbox" in artifact_server.RAW_CSP
    assert "allow-popups-to-escape-sandbox" in artifact_server.IFRAME_SANDBOX
    assert "allow-same-origin" not in artifact_server.RAW_CSP
    assert "connect-src 'none'" in artifact_server.RAW_CSP


def test_raw_pages_carry_the_link_guard_and_the_store_stays_byte_true(make_client):
    c = make_client()
    full = "<!doctype html><html><head><title>F</title></head><body>x</body></html>"
    a = publish(c)
    b = publish(c, html=full)
    for aid in (a["id"], b["id"]):
        body = c.get(f"/raw/{aid}/v/1/", headers=local(c)).content
        assert store.LINK_GUARD in body
    # the guard lands INSIDE the document, right after <html>
    body = c.get(f"/raw/{b['id']}/v/1/", headers=local(c)).content
    assert body.startswith(b"<!doctype html><html>" + store.LINK_GUARD)
    # served, never stored
    assert store.read_file(b["id"], 1)[0] == full.encode()


def test_theme_light_is_a_light_color_scheme():
    """browser-2: the shell must SAY light, or a dark device keeps the frame
    dark; and a fragment served with ?theme=light pins color-scheme too."""
    shell = open(os.path.join(REPO, "agents", "artifact_ui", "shell.html")).read()
    assert re.search(r':root\[data-theme="light"\]\{\s*color-scheme:\s*light;?\s*\}',
                     shell)
    out = store.wrap_skeleton(b"<p>x</p>", theme="light")
    assert b":root{color-scheme:light}" in out
    assert b":root{color-scheme:light dark}" in store.wrap_skeleton(b"<p>x</p>")


def test_the_shell_has_a_tab_icon_and_favicon_ico_answers(make_client):
    c = make_client()
    a = publish(c, favicon="📊")
    body = c.get(f"/a/{a['id']}", headers=local(c)).text
    m = re.search(r'<link rel="icon" href="(data:image/svg\+xml,[^"]+)"', body)
    assert m
    from urllib.parse import unquote
    import html as _h
    assert "📊" in unquote(_h.unescape(m.group(1)))
    assert c.get("/favicon.ico", headers=MAX).status_code == 204
    r = c.get("/favicon.ico")                       # anonymous: flat 404
    assert r.status_code == 404 and r.json() == FLAT_404


def test_supporting_files_answer_byte_ranges(make_client):
    """browser-9: <video src="demo.mp4"> needs 206 to play on iOS and to seek
    anywhere."""
    c = make_client()
    blob = bytes(range(256)) * 4
    a = publish(c, files={"v.mp4": {"b64": base64.b64encode(blob).decode()}})
    url = f"/raw/{a['id']}/v/1/v.mp4"
    r = c.get(url, headers=local(c, {"Range": "bytes=10-19"}))
    assert r.status_code == 206 and r.content == blob[10:20]
    assert r.headers["content-range"] == f"bytes 10-19/{len(blob)}"
    assert r.headers["accept-ranges"] == "bytes"
    r = c.get(url, headers=local(c, {"Range": "bytes=-4"}))
    assert r.status_code == 206 and r.content == blob[-4:]
    r = c.get(url, headers=local(c, {"Range": f"bytes={len(blob)}-"}))
    assert r.status_code == 416
    r = c.get(url, headers=local(c))
    assert r.status_code == 200 and r.content == blob
    # the security headers ride on the partial answer too
    r = c.get(url, headers=local(c, {"Range": "bytes=0-1"}))
    assert r.headers["content-security-policy"] == artifact_server.RAW_CSP


def test_timestamps_are_human(make_client):
    """browser-10: '2026-09-30T05:29:39.044636+00:00' in the strip, the
    picker and every gallery row."""
    c = make_client()
    a = publish(c)
    iso = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+")
    shell = c.get(f"/a/{a['id']}", headers=local(c)).text
    gallery = c.get("/", headers=local(c)).text
    assert not iso.search(shell) and not iso.search(gallery)
    assert re.search(r"updated \d{4}-\d\d-\d\d \d\d:\d\d UTC", shell)
    assert store.human_ts("2026-09-30T05:29:39.044636+00:00") == \
        "2026-09-30 05:29 UTC"


# --- browser-5: the gallery's rows are the documented contract ---------------

def test_gallery_rows_follow_the_template_contract(make_client):
    c = make_client()
    publish(c, title="alpha")
    publish(c, title="beta", description="second")
    body = re.sub(r"<!--.*?-->", "", c.get("/", headers=local(c)).text,
                  flags=re.S)
    rows = re.findall(r'<li class="row" data-search="([^"]*)"><a class="card"',
                      body)
    assert len(rows) == 2 and any("beta" in r and "second" in r for r in rows)
    gallery = open(os.path.join(REPO, "agents", "artifact_ui",
                                "gallery.html")).read()
    # the author rule that beat [hidden] is out-ranked now
    assert re.search(r"\[hidden\]\{display:none!important\}", gallery)


# --- correctness-05 / 08: republish semantics --------------------------------

def test_republish_reports_the_real_visibility(make_client):
    c = make_client(operators="max@example.com")
    a = publish(c)
    r = publish(c, artifact_id=a["id"], visibility="tailnet")
    assert r["visibility"] == "private" and r["created"] is False
    assert "visibility unchanged (private)" in r["notice"]
    # not asking says nothing
    assert publish(c, artifact_id=a["id"])["notice"] == ""


def test_republish_takes_the_new_title_and_keeps_the_first_icon(make_client):
    c = make_client()
    a = publish(c, html="<title>Q3 report</title>x", favicon="📊")
    b = publish(c, html="<title>Q4 report</title>y", favicon="🔥",
                artifact_id=a["id"])
    meta = store.get_meta(a["id"])
    assert b["title"] == meta["title"] == "Q4 report"
    assert meta["favicon"] == "📊"
    # an explicit title still wins, and a page with no <title> keeps it
    publish(c, html="<title>Q5</title>z", title="Pinned", artifact_id=a["id"])
    publish(c, html="<p>no title</p>", artifact_id=a["id"])
    assert store.get_meta(a["id"])["title"] == "Pinned"


# --- correctness-06: pagination ----------------------------------------------

def test_listing_pages_and_totals(make_client, monkeypatch):
    c = make_client()
    ids = [publish(c, html=f"<title>T{i}</title>x")["id"] for i in range(7)]
    r = c.get("/api/artifacts?limit=3&offset=3", headers=local(c)).json()
    assert r["total"] == 7 and r["count"] == 3 and r["offset"] == 3
    assert [x["id"] for x in r["artifacts"]] == list(reversed(ids))[3:6]
    monkeypatch.setattr(artifact_server, "GALLERY_PAGE", 3)
    c2 = make_client()
    g = c2.get("/", headers=local(c2)).text
    assert "1&ndash;3 of 7 published" in g and "older &rarr;" in g
    g3 = c2.get("/?page=3", headers=local(c2)).text
    assert "7&ndash;7 of 7" in g3 and "&larr; newer" in g3
    # server-side search reaches rows on every page
    hit = c2.get("/?q=T0", headers=local(c2)).text
    assert page_rows(hit) == 1 and "q: <b>T0</b>" in hit
    miss = c2.get("/?q=zzzz", headers=local(c2)).text
    assert "No artifact matches that" in miss


# --- correctness-09: disk full ----------------------------------------------

def test_disk_full_is_a_named_507_and_leaves_no_empty_dir(make_client,
                                                          monkeypatch):
    c = make_client()

    def full(path, data):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(store, "_write_bytes", full)
    r = c.post("/api/artifacts", json={"html": PAGE, "artifact_id": "diskfull"},
               headers=local(c))
    assert r.status_code == 507
    assert "No space left" in r.json()["detail"]
    assert not os.path.exists(os.path.join(store.store_root(), "diskfull"))


# --- correctness-10 / F-A2: versions, pins, tags, retention ------------------

def test_one_old_version_can_be_deleted_but_never_the_current_or_last(make_client):
    c = make_client()
    a = publish(c)
    for _ in range(2):
        publish(c, artifact_id=a["id"])
    aid = a["id"]
    r = c.delete(f"/api/artifacts/{aid}/v/3", headers=local(c))
    assert r.status_code == 400 and "currently serves" in r.json()["detail"]
    r = c.delete(f"/api/artifacts/{aid}/v/1", headers=local(c))
    assert r.status_code == 200 and r.json()["versions"] == 2
    assert not os.path.exists(os.path.join(store.store_root(), aid, "v1"))
    assert c.get(f"/a/{aid}/v/1", headers=local(c)).status_code == 404
    assert c.get(f"/a/{aid}/v/2", headers=local(c)).status_code == 200
    # the next publish never reuses a deleted number
    assert publish(c, artifact_id=aid)["version"] == 4
    c.patch(f"/api/artifacts/{aid}", json={"current": 2}, headers=local(c))
    for n in (3, 4):
        assert c.delete(f"/api/artifacts/{aid}/v/{n}",
                        headers=local(c)).status_code == 200
    r = c.delete(f"/api/artifacts/{aid}/v/2", headers=local(c))
    assert r.status_code == 400 and "only version" in r.json()["detail"]


def test_the_version_cap_points_at_prune_not_a_new_url(env, monkeypatch):
    monkeypatch.setitem(store.CAPS, "versions", 2)
    a = store.publish(PAGE)
    store.publish(PAGE, artifact_id=a["id"])
    with pytest.raises(store.ArtifactError) as e:
        store.publish(PAGE, artifact_id=a["id"])
    assert "prune" in str(e.value) and "new id" not in str(e.value)
    store.set_current(a["id"], 2)
    store.remove_version(a["id"], 1)
    assert store.publish(PAGE, artifact_id=a["id"])["version"] == 3


def test_pins_and_tags_round_trip_and_filter(make_client):
    c = make_client()
    a = publish(c, html="<title>A</title>x")
    b = publish(c, html="<title>B</title>x")
    r = c.patch(f"/api/artifacts/{a['id']}",
                json={"pinned": True, "tags": ["Report", "q3", "q3"]},
                headers=local(c))
    assert r.status_code == 200
    assert r.json()["pinned"] is True and r.json()["tags"] == ["report", "q3"]
    # pinned leads the gallery even though b is newer
    g = c.get("/", headers=local(c)).text
    assert g.index(f'/a/{a["id"]}') < g.index(f'/a/{b["id"]}')
    rows = c.get("/api/artifacts?tag=q3", headers=local(c)).json()["artifacts"]
    assert [x["id"] for x in rows] == [a["id"]]
    assert c.patch(f"/api/artifacts/{a['id']}", json={"tags": ["<b>"]},
                   headers=local(c)).status_code == 400
    assert c.patch(f"/api/artifacts/{a['id']}", json={"tags": []},
                   headers=local(c)).json()["tags"] == []


def test_retention_is_off_by_default_and_never_touches_pinned(env, monkeypatch):
    old = store.publish(PAGE, title="old")
    kept = store.publish(PAGE, title="old but pinned")
    fresh = store.publish(PAGE, title="fresh")
    store.set_pinned(kept["id"], True, admin=True)
    for aid in (old["id"], kept["id"]):
        meta = store.get_meta(aid)
        meta["updated_at"] = (datetime.now(timezone.utc)
                              - timedelta(days=40)).isoformat()
        store._write_meta(aid, meta)
    assert store.sweep_retention() == []                  # off: unset
    monkeypatch.setenv("OPENBEAST_ARTIFACT_RETAIN_DAYS", "30")
    assert store.sweep_retention() == [old["id"]]
    assert store.get_meta(old["id"]) is None
    assert store.get_meta(kept["id"]) and store.get_meta(fresh["id"])
    assert any(x.get("event") == "remove" and x.get("reason") == "retention"
               for x in ledger(env))


def test_the_server_sweep_is_audited(make_client, monkeypatch):
    c = make_client()
    a = publish(c)
    meta = store.get_meta(a["id"])
    meta["updated_at"] = "2001-01-01T00:00:00+00:00"
    store._write_meta(a["id"], meta)
    monkeypatch.setenv("OPENBEAST_ARTIFACT_RETAIN_DAYS", "7")
    assert c.app.state.sweep() == [a["id"]]
    assert any(r.get("route") == "retention" and r["id"] == a["id"]
               for r in audit_rows(c.tmp))


# --- F-A2: lifecycle from the phone with an artifact-scoped device key -------

def test_a_phone_manages_its_owners_page_with_an_artifact_key(make_client):
    c = make_client(operators="boss@example.com,max@example.com")
    a = publish(c, headers=local(c, MAX))               # owned by max
    phone = enroll(c.tmp, "phone", ("artifact",))
    h = {**MAX, **phone}
    assert c.patch(f"/api/artifacts/{a['id']}", json={"pinned": True},
                   headers=h).status_code == 200
    assert c.patch(f"/api/artifacts/{a['id']}", json={"visibility": "tailnet"},
                   headers=h).status_code == 200
    row = [r for r in audit_rows(c.tmp) if r.get("method") == "PATCH"][-1]
    assert row["device"] == "phone" and row["login"] == "max@example.com"
    assert c.delete(f"/api/artifacts/{a['id']}", headers=h).status_code == 200


@pytest.mark.parametrize("case", ["no-key", "wrong-scope", "revoked",
                                  "no-login", "publish", "unknown-key"])
def test_remote_writes_fail_closed(make_client, case):
    c = make_client(operators="max@example.com")
    a = publish(c, headers=local(c, MAX))
    headers = dict(MAX)
    if case == "wrong-scope":
        headers.update(enroll(c.tmp, "laptop", ("chat",)))
    elif case == "revoked":
        headers.update(enroll(c.tmp, "lost", ("artifact",), revoked=True))
    elif case == "no-login":
        headers = enroll(c.tmp, "phone", ("artifact",))
    elif case in ("publish",):
        headers.update(enroll(c.tmp, "phone", ("artifact",)))
    elif case == "unknown-key":
        headers["Authorization"] = "Bearer not-enrolled"
    if case == "publish":
        r = c.post("/api/artifacts", json={"html": PAGE}, headers=headers)
    else:
        r = c.patch(f"/api/artifacts/{a['id']}", json={"pinned": True},
                    headers=headers)
    assert r.status_code == 404 and r.json() == FLAT_404, case
    assert store.get_meta(a["id"]).get("pinned") is not True


def test_remote_writes_are_small_and_rate_limited(make_client, monkeypatch):
    c = make_client(operators="max@example.com")
    a = publish(c, headers=local(c, MAX))
    h = {**MAX, **enroll(c.tmp)}
    big = {"tags": ["x"], "pad": "y" * (artifact_server.REMOTE_WRITE_MAX_BYTES)}
    r = c.patch(f"/api/artifacts/{a['id']}", json=big, headers=h)
    assert r.status_code == 404
    monkeypatch.setattr(artifact_server, "REMOTE_WRITES_PER_MIN", 2)
    c2 = make_client(operators="max@example.com")
    for _ in range(2):
        assert c2.patch(f"/api/artifacts/{a['id']}", json={"pinned": True},
                        headers=h).status_code == 200
    assert c2.patch(f"/api/artifacts/{a['id']}", json={"pinned": True},
                    headers=h).status_code == 429


def test_the_shell_offers_manage_only_to_owner_or_admin(make_client):
    c = make_client(operators="boss@example.com,max@example.com,kid@example.com")
    a = publish(c, headers=local(c, MAX), visibility="tailnet")
    def attr(h):
        body = c.get(f"/a/{a['id']}", headers=h).text
        return re.search(r'data-manage="([^"]*)"', body).group(1)
    assert attr(MAX) == "1"
    assert attr({"Tailscale-User-Login": "boss@example.com"}) == "1"   # admin
    assert attr(KID) == ""


# --- F-A3: session provenance ------------------------------------------------

def test_source_session_is_stamped_linked_and_filterable(make_client,
                                                         monkeypatch):
    c = make_client()
    a = publish(c, source_session="agent-20260930-abc123")
    publish(c)
    bad = publish(c, source_session="<script>")
    assert store.get_meta(a["id"])["source_session"] == "agent-20260930-abc123"
    assert "source_session" not in store.get_meta(bad["id"])
    body = c.get(f"/a/{a['id']}", headers=local(c)).text
    assert "made by session agent-20260930-abc123" in body
    assert ":8445/#/s/" not in body                       # chat not published
    monkeypatch.setenv("OPENBEAST_CHAT_BASE_URL", "https://beast.tail.ts.net:8445")
    body = c.get(f"/a/{a['id']}", headers=local(c)).text
    assert ('href="https://beast.tail.ts.net:8445/#/s/agent-20260930-abc123"'
            in body)
    rows = c.get("/api/artifacts?session=agent-20260930-abc123",
                 headers=local(c)).json()["artifacts"]
    assert [r["id"] for r in rows] == [a["id"]]
    g = c.get("/?session=agent-20260930-abc123", headers=local(c)).text
    assert page_rows(g) == 1


def test_the_tool_stamps_the_session_and_falls_back_to_the_filename(
        env, monkeypatch):
    import mcp_server
    monkeypatch.setenv("BEAST_ARTIFACT", "true")
    monkeypatch.setenv("OPENBEAST_SESSION_ID", "agent-x-1")
    monkeypatch.delenv("AGENT_WORKDIR", raising=False)
    ws = env / "files"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "weekly-numbers.html").write_text("<p>no title here</p>")
    out = mcp_server.publish_artifact(str(ws / "weekly-numbers.html"))
    assert out.startswith('Published "weekly numbers"'), out
    assert "visibility private" in out
    aid = re.search(r"id ([0-9a-f-]{36})", out).group(1)
    assert store.get_meta(aid)["source_session"] == "agent-x-1"


# --- correctness-02 / integration-ops-2 / tests-docs-5: the tool surface -----

def test_the_tool_reads_beast_artifact_from_openbeast_conf(env, monkeypatch):
    """OpenCode launches mcp_server.py with the user's PLAIN environment: the
    flag must come from the rig's openbeast.conf."""
    import mcp_server
    for var in ("BEAST_ARTIFACT", "OPENBEAST_BEAST_ARTIFACT"):
        monkeypatch.delenv(var, raising=False)
    conf = env / "openbeast.conf"
    monkeypatch.setenv("OPENBEAST_CONF", str(conf))
    conf.write_text("BEAST_ARTIFACT=false\n")
    assert mcp_server._artifact_enabled() is False
    conf.write_text('# rig\nBEAST_ARTIFACT="true"\n')
    assert mcp_server._artifact_enabled() is True
    # env still wins
    monkeypatch.setenv("BEAST_ARTIFACT", "false")
    assert mcp_server._artifact_enabled() is False


def test_a_client_machine_is_told_artifacts_live_on_the_rig(env, monkeypatch):
    import mcp_server
    for var in ("BEAST_ARTIFACT", "OPENBEAST_BEAST_ARTIFACT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENBEAST_CONF", str(env / "none.conf"))
    monkeypatch.setenv("OPENBEAST_AGENT_INFERENCE_URL", "https://rig:8443/v1")
    out = mcp_server.publish_artifact("page.html")
    assert "published on the rig, not from a client" in out
    assert "openbeast.conf" not in out
    assert "published on the rig" in mcp_server.list_artifacts()


def test_the_workspace_refusal_does_not_misstate_the_privacy_model(env,
                                                                  monkeypatch):
    import mcp_server
    monkeypatch.setenv("BEAST_ARTIFACT", "true")
    monkeypatch.delenv("AGENT_WORKDIR", raising=False)
    (env / "outside.html").write_text(PAGE)
    out = mcp_server.publish_artifact(str(env / "outside.html"))
    assert out.startswith("Error: refusing to publish")
    assert "anyone with the link" not in out
    assert "on your tailnet" in out


# --- fixup: upgrade path for operators[0]-owned pages, PATCH oracle ---------

def test_the_rig_republishes_into_a_page_its_first_operator_owned(make_client):
    """Before F-A1 a CLI publish on a rig with ARTIFACT_OPERATORS set was
    owned by operators[0]; after the upgrade every CLI publish is the rig, and
    `artifact.sh publish <f> --id <id>` into those pages answered 404. The
    rig may add a version to the first operator's (or an admin's) page — and
    the owner is not rewritten."""
    c = make_client(operators="max@example.com,kid@example.com")
    a = publish(c, headers=local(c, MAX))                # what main's CLI wrote
    assert store.get_meta(a["id"])["owner"] == "max@example.com"
    r = c.post("/api/artifacts", json={"html": PAGE + "v2",
                                       "artifact_id": a["id"]},
               headers=local(c))
    assert r.status_code == 201, r.text
    meta = store.get_meta(a["id"])
    assert meta["owner"] == "max@example.com"
    assert len(meta["versions"]) == 2
    # the same through the store, with no identity at all (OpenCode stdio)
    store.publish(PAGE + "v3", artifact_id=a["id"])
    assert len(store.get_meta(a["id"])["versions"]) == 3
    # NEGATIVE CONTROL: another operator's page stays owner-only.
    k = publish(c, headers=local(c, KID))
    r = c.post("/api/artifacts", json={"html": PAGE, "artifact_id": k["id"]},
               headers=local(c))
    assert r.status_code == 404 and r.json() == FLAT_404
    with pytest.raises(store.ArtifactError):
        store.publish(PAGE, artifact_id=k["id"])
    assert len(store.get_meta(k["id"])["versions"]) == 1


def test_the_rig_republishes_into_a_configured_admins_page(make_client,
                                                            monkeypatch):
    monkeypatch.setenv("OPENBEAST_ARTIFACT_ADMINS", "kid@example.com")
    c = make_client(operators="max@example.com,kid@example.com,x@example.com")
    k = publish(c, headers=local(c, KID))
    store.publish(PAGE + "v2", artifact_id=k["id"])
    assert len(store.get_meta(k["id"])["versions"]) == 2
    x = publish(c, headers=local(c, {"Tailscale-User-Login": "x@example.com"}))
    with pytest.raises(store.ArtifactError):
        store.publish(PAGE, artifact_id=x["id"])


def test_a_logged_in_non_owner_cannot_republish_an_admins_page(make_client):
    """The widening is for the RIG principal only: a second operator
    publishing through the store with their own identity is still refused."""
    make_client(operators="max@example.com,kid@example.com")
    token = store.set_owner_override("max@example.com")
    try:
        aid = store.publish(PAGE)["id"]
    finally:
        store.reset_owner_override(token)
    token = store.set_owner_override("kid@example.com")
    try:
        with pytest.raises(store.ArtifactError):
            store.publish(PAGE, artifact_id=aid)
    finally:
        store.reset_owner_override(token)

