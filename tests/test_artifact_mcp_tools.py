#!/usr/bin/env python3
"""beast-artifact tool surface (agents/mcp_server.py) — the model-facing half.

The store and the HTTP server have their own suites; this one covers the two
tools the MODEL calls, where the attacker in the threat model is the model
itself:

  - publish_artifact reads through tools.read_file's guards (D7): no
    /proc,/sys,/dev, no FIFO wedge, no non-regular file, size before read —
    plus the containment those four do not give: /etc/passwd is a regular
    file under the cap, so the page must come from the caller's workspace
  - it returns a string for every failure, including a NUL byte in the path
    (which used to raise ValueError straight through the tool server as a 500)
  - it refuses to publish when BEAST_ARTIFACT is off (D8)
  - list_artifacts passes a viewer, so it cannot enumerate another operator's
    private artifacts (D6)
  - the store lives INSIDE the workspace, so the page may not come from it
    (D26): a reviewer published another user's private page by naming the
    store's own internal path
  - and the one that crosses BOTH surfaces (D21): a page published through
    the tool server, with the identity headers Open WebUI really forwards, is
    read through the HTTP API as the tailnet login. Every other ownership
    test in this repo compares the publisher to itself, which is why a
    blocker — publisher named by UUID, reader named by email, never a
    match — was invisible.

Run: pytest tests/test_artifact_mcp_tools.py
"""
import json
import os
import re
import sys

import pytest
from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

import artifact  # noqa: E402
import mcp_server  # noqa: E402

PAGE = "<title>Hello</title><p>hi</p>"


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    """An enabled beast-artifact with its store in a temp dir."""
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("OPENBEAST_ARTIFACT_BASE_URL", "https://beast:8446")
    monkeypatch.setenv("BEAST_ARTIFACT", "true")
    monkeypatch.delenv("OPENBEAST_BEAST_ARTIFACT", raising=False)
    monkeypatch.setenv("OPENBEAST_ARTIFACT_OPERATORS", "max@example.com")
    # The tool publishes out of the caller's WORKSPACE (tools._base_dir()),
    # which is what write_file writes to — see _read_artifact_page.
    ws = tmp_path / "files"
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("AGENT_WORKDIR", raising=False)
    return ws


def _page(ws, name="page.html", body=PAGE):
    p = ws / name
    p.write_text(body)
    return str(p)


# --- D7: the read runs under tools.read_file's guards ------------------------

def test_publish_refuses_pseudo_filesystem_paths(rig):
    """The whole machine is NOT publishable. A reviewer turned /etc/passwd and
    /proc/self/maps into durable URLs through the old open().read()."""
    for path in ("/proc/self/maps", "/proc/version", "/sys/kernel/vmcoreinfo",
                 "/dev/zero"):
        out = mcp_server.publish_artifact(path)
        assert isinstance(out, str)
        assert out.startswith("Error:"), (path, out)
        assert "pseudo-filesystem" in out, (path, out)
        assert "http" not in out.lower() or "Published" not in out


def test_publish_refuses_a_named_pipe(rig):
    """A FIFO blocks open() forever without O_NONBLOCK — one tool call wedged
    a worker thread of the tool server for good."""
    fifo = rig / "wedge.html"
    os.mkfifo(str(fifo))
    out = mcp_server.publish_artifact(str(fifo))
    assert isinstance(out, str)
    assert out.startswith("Error:")
    assert "regular file" in out


def test_publish_refuses_a_directory(rig):
    d = rig / "adir"
    d.mkdir()
    out = mcp_server.publish_artifact(str(d))
    assert out.startswith("Error:")
    assert "regular file" in out or "cannot read" in out


def test_publish_refuses_a_page_over_the_cap(rig, monkeypatch):
    """Size is checked from fstat BEFORE any bytes are read."""
    monkeypatch.setitem(artifact.CAPS, "page_bytes", 1024)
    big = rig / "big.html"
    big.write_text("<p>" + "x" * 4096 + "</p>")
    out = mcp_server.publish_artifact(str(big))
    assert out.startswith("Error:")
    assert "page cap" in out


def test_publish_refuses_a_file_outside_the_workspace(rig, tmp_path):
    """The four guards ported from read_file are necessary, not sufficient:
    /etc/passwd is a regular file under the cap and published cleanly with all
    four in place. Publishing mints a durable URL, so the page must come from
    the caller's own workspace."""
    out = mcp_server.publish_artifact("/etc/passwd")
    assert out.startswith("Error:"), out
    assert "outside" in out and "workspace" in out
    assert artifact.list_artifacts(viewer="max@example.com") == []
    outsider = tmp_path / "elsewhere.html"
    outsider.write_text(PAGE)
    assert mcp_server.publish_artifact(str(outsider)).startswith("Error:")


def test_publish_refuses_a_symlink_out_of_the_workspace(rig):
    """The path is realpath'd first, so a symlink is not a way around it."""
    link = rig / "innocent.html"
    os.symlink("/etc/passwd", str(link))
    out = mcp_server.publish_artifact(str(link))
    assert out.startswith("Error:")
    assert "workspace" in out


def test_publish_returns_a_string_on_a_null_byte(rig):
    """A NUL in the path raises ValueError from os.open/realpath. The tool
    contract is 'always a string' — this used to surface as a 500."""
    out = mcp_server.publish_artifact("/tmp/evil\x00.html")
    assert isinstance(out, str)
    assert out.startswith("Error:")


def test_publish_returns_a_string_for_a_missing_file(rig):
    out = mcp_server.publish_artifact(str(rig / "nope.html"))
    assert isinstance(out, str)
    assert out.startswith("Error:")


def test_publish_still_works_for_a_real_page(rig):
    """The guards must not break the happy path."""
    out = mcp_server.publish_artifact(_page(rig), title="A page")
    assert out.startswith('Published "A page"'), out
    assert "https://beast:8446/a/" in out


# --- D8: no confident URL to nothing -----------------------------------------

def test_publish_refuses_when_the_feature_is_off(rig, monkeypatch):
    monkeypatch.setenv("BEAST_ARTIFACT", "false")
    path = _page(rig)
    out = mcp_server.publish_artifact(path)
    assert out.startswith("Error:")
    assert "BEAST_ARTIFACT=true" in out
    # And it wrote nothing: the store must still be empty.
    assert artifact.list_artifacts(viewer="max@example.com") == []


def test_publish_refuses_when_the_flag_is_unset(rig, monkeypatch):
    monkeypatch.delenv("BEAST_ARTIFACT", raising=False)
    out = mcp_server.publish_artifact(_page(rig))
    assert out.startswith("Error:")
    assert "BEAST_ARTIFACT=true" in out


# --- D6: the listing tool passes a viewer ------------------------------------

def test_list_artifacts_passes_the_viewer(rig, monkeypatch):
    """Without viewer=, the gallery tool enumerated every operator's private
    artifacts to whoever called it."""
    seen = {}
    real = artifact.list_artifacts

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(artifact, "list_artifacts", spy)
    token = artifact.set_owner_override("kid@example.com")
    try:
        mcp_server.list_artifacts(limit=5)
    finally:
        artifact.reset_owner_override(token)
    assert "viewer" in seen, "list_artifacts was called without a viewer"
    # The literal identity, not `artifact.default_owner()` — asserting the
    # same expression the code evaluates is a test that cannot fail, and this
    # one sat next to a blocker (D21) for exactly that reason.
    assert seen["viewer"] == "kid@example.com"
    assert seen["limit"] == 5


def test_list_artifacts_hides_another_owners_private_page(rig):
    # `owner=` is an assertion, not an identity (D28), so the other operator's
    # page is published UNDER their own resolved identity — the only way one
    # exists at all now.
    mine = artifact.publish(PAGE, title="Mine")            # the rig: max
    token = artifact.set_owner_override("someone@example.com")
    try:
        artifact.publish(PAGE, title="Theirs")
    finally:
        artifact.reset_owner_override(token)
    out = mcp_server.list_artifacts()
    assert isinstance(out, str)
    assert "Mine" in out or mine["id"] in out
    assert "Theirs" not in out


# --- D26: the store lives INSIDE the workspace -------------------------------

def _publish_as(login: str, title: str = "Theirs", body: str = PAGE):
    """A page owned by somebody else, published the only way one can be."""
    token = artifact.set_owner_override(login)
    try:
        return artifact.publish(body, title=title)
    finally:
        artifact.reset_owner_override(token)


def test_publish_refuses_the_stores_own_internal_path(rig):
    """D26. D7's stated invariant — "the page comes from your workspace" —
    was false: store_root() is $OPENBEAST_FILES_DIR/artifacts and the
    workspace is $OPENBEAST_FILES_DIR itself whenever no per-user shard is
    set (FILES_SHARDING=off, and every MCP stdio caller). A reviewer
    published another user's PRIVATE page by naming the store's own path —
    the bytes are read as a file, so ownership never came into it — and then
    re-shared the copy as `tailnet`."""
    theirs = _publish_as("someone@example.com", body="<title>T</title>secret")
    victim = os.path.join(artifact.store_root(), theirs["id"], "v1",
                          "index.html")
    assert os.path.exists(victim), "fixture wrong: nothing to steal"

    out = mcp_server.publish_artifact(victim, title="Mine now",
                                      visibility="tailnet")
    assert out.startswith("Error:"), out
    assert "artifact store" in out
    assert "https://" not in out                    # no URL was minted
    # nothing was copied, and their page is untouched and still private
    assert artifact.list_artifacts(viewer="max@example.com") == []
    assert artifact.get_meta(theirs["id"])["visibility"] == "private"
    assert artifact.get_meta(theirs["id"])["owner"] == "someone@example.com"


@pytest.mark.parametrize("name", ["", "index.jsonl", ".lock"])
def test_publish_refuses_the_store_root_itself(rig, name):
    target = os.path.join(artifact.store_root(), name) if name \
        else artifact.store_root()
    out = mcp_server.publish_artifact(target)
    assert out.startswith("Error:"), (name, out)
    assert artifact.list_artifacts(viewer="max@example.com") == []


def test_a_symlink_into_the_store_is_refused_too(rig):
    """The workspace is writable by the model, so a link is the obvious way
    around a string comparison. Both sides are realpath'd."""
    theirs = _publish_as("someone@example.com")
    link = rig / "innocent.html"
    os.symlink(os.path.join(artifact.store_root(), theirs["id"], "v1",
                            "index.html"), str(link))
    out = mcp_server.publish_artifact(str(link))
    assert out.startswith("Error:"), out
    assert artifact.list_artifacts(viewer="max@example.com") == []


def test_the_workspace_itself_still_publishes(rig):
    """The refusal is the store, not the workspace around it: the ordinary
    write_file -> publish_artifact path must be untouched."""
    out = mcp_server.publish_artifact(_page(rig), title="A page")
    assert out.startswith('Published "A page"'), out


# --- D21: the two identity surfaces must name the same person ----------------
# THE blocker, and the reason it was invisible: every ownership test in this
# repo compares the publisher to ITSELF. The tool server hands the store an
# Open WebUI user id — a UUID — and agents/artifact_server.py authorises a
# reader by their TAILNET LOGIN, an email. They can never match, so on any rig
# with identity forwarding on (the configured default) a page the model
# published was 404 to the human who asked for it, 404 to the rig, and
# unrecoverable: sharing is owner-only and no principal can present a UUID.
# These tests cross the surfaces: publish THROUGH the tool server, read
# THROUGH the HTTP API.

WEBUI_ID = "8f3c1c2e-0f77-4a4c-9a0b-1f2d3e4a5b6c"   # what WebUI really sends
TAILNET_LOGIN = "max@example.com"                   # what the reader presents


@pytest.fixture()
def surfaces(rig, tmp_path, monkeypatch):
    """Both halves of the stack, configured the way the rig ships: identity
    forwarding on, an operator allowlist, per-user workspace shards."""
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("OPENBEAST_ARTIFACT_PORT", "39917")
    monkeypatch.setenv("OPENBEAST_FILES_SHARDING", "user")
    for var in ("OPENBEAST_IDENTITY_JWT_SECRET", "OPENBEAST_MCPO_ADMIN_KEY",
                "OPENBEAST_MCPO_GUEST_KEY"):
        monkeypatch.delenv(var, raising=False)
    import artifact_server            # noqa: E402
    import openapi_tools              # noqa: E402
    server = artifact_server.create_app()
    return (TestClient(openapi_tools.create_app()), TestClient(server),
            server)


def _identity(email=TAILNET_LOGIN, user=WEBUI_ID):
    """The headers Open WebUI forwards with ENABLE_FORWARD_USER_INFO_HEADERS
    (docker-compose.yml sets it, and it is on for this box)."""
    h = {"X-OpenWebUI-User-Id": user, "X-OpenWebUI-User-Role": "admin",
         "X-OpenWebUI-Chat-Id": "chat-1"}
    if email:
        h["X-OpenWebUI-User-Email"] = email
    return h


def _id_from(url_text: str) -> str:
    m = re.search(r"/a/([A-Za-z0-9][A-Za-z0-9._-]{0,63})", url_text)
    assert m, url_text
    return m.group(1)


def _publish_through_the_tool_server(tools, headers, title="Quarterly report"):
    """The model's real journey: write the page into its workspace, publish
    it, and get back a URL it will hand to the human."""
    r = tools.post("/write_file",
                   json={"path": "report.html", "content": PAGE},
                   headers=headers)
    assert r.status_code == 200, r.text
    r = tools.post("/publish_artifact",
                   json={"path": "report.html", "title": title},
                   headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert isinstance(out, str) and out.startswith("Published"), out
    return out


def test_a_page_the_model_publishes_is_readable_by_the_human_who_asked(
        surfaces):
    """D21, the whole blocker in one test. Publish through the tool server
    with realistic identity headers; read through the HTTP API as the tailnet
    login. Before the bridge this was 404 on every route, for everyone."""
    tools, web, app = surfaces
    out = _publish_through_the_tool_server(tools, _identity())
    aid = _id_from(out)

    me = {"Tailscale-User-Login": TAILNET_LOGIN}
    assert web.get(f"/a/{aid}", headers=me).status_code == 200
    assert web.get(f"/a/{aid}/v/1", headers=me).status_code == 200
    assert PAGE in web.get(f"/raw/{aid}/v/1/", headers=me).text
    assert web.get(f"/api/artifacts/{aid}", headers=me).status_code == 200
    # it is in their gallery, by name
    gallery = web.get("/", headers=me)
    assert gallery.status_code == 200 and aid in gallery.text
    assert web.get("/api/artifacts", headers=me).json()["count"] == 1

    # the rig can manage it too — the page is not a tombstone
    token = {"X-OpenBeast-Local": app.state.local_token,
             "Tailscale-User-Login": TAILNET_LOGIN}
    r = web.patch(f"/api/artifacts/{aid}", json={"visibility": "tailnet"},
                  headers=token)
    assert r.status_code == 200 and r.json()["visibility"] == "tailnet"


def test_the_owner_is_the_login_a_reader_can_present_never_the_uuid(surfaces):
    """The invariant, stated where it can fail: a WebUI UUID must never reach
    meta["owner"]. It is recorded beside it, as provenance."""
    tools, _web, _app = surfaces
    aid = _id_from(_publish_through_the_tool_server(tools, _identity()))
    meta = artifact.get_meta(aid)
    assert meta["owner"] == TAILNET_LOGIN
    assert meta["owner"] != WEBUI_ID
    assert WEBUI_ID not in str(meta["owner"])
    assert meta.get("owner_webui_id") == WEBUI_ID        # provenance kept
    # and the alias really is an alias: the publishing surface can still see
    # its own page when all it knows about itself is that id
    assert artifact.can_view(meta, WEBUI_ID) is True
    assert artifact.can_view(meta, TAILNET_LOGIN) is True
    assert artifact.can_view(meta, "kid@example.com") is False


def test_without_a_forwarded_email_the_rig_operator_owns_it(surfaces):
    """No email (an older WebUI, or the header switched off) must NOT fall
    back to the UUID. The rig's first operator is a principal someone can
    actually present; a UUID is a tombstone."""
    tools, web, _app = surfaces
    aid = _id_from(_publish_through_the_tool_server(
        tools, _identity(email=None)))
    meta = artifact.get_meta(aid)
    assert meta["owner"] == "max@example.com"    # OPENBEAST_ARTIFACT_OPERATORS[0]
    assert meta["owner"] != WEBUI_ID
    assert web.get(f"/a/{aid}",
                   headers={"Tailscale-User-Login": TAILNET_LOGIN}
                   ).status_code == 200


def test_the_jwt_surface_bridges_through_the_token_email(surfaces,
                                                         monkeypatch):
    """Signed-identity mode (the enterprise default) takes the email from the
    VERIFIED token — never from an unsigned header sitting next to it, which
    would be a way to publish as somebody else."""
    import datetime

    import jwt as pyjwt
    import openapi_tools               # noqa: E402
    secret = "x" * 48        # RFC 7518 §3.2 minimum, so no warning
    monkeypatch.setenv("OPENBEAST_IDENTITY_JWT_SECRET", secret)
    tools = TestClient(openapi_tools.create_app())
    claims = {
        "sub": WEBUI_ID, "role": "admin", "iss": "open-webui",
        "email": TAILNET_LOGIN,
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(minutes=5),
    }
    signed = {"X-OpenWebUI-User-JWT": pyjwt.encode(claims, secret,
                                                   algorithm="HS256"),
              # forged, and ignored: the token is the identity
              "X-OpenWebUI-User-Email": "attacker@example.invalid"}
    aid = _id_from(_publish_through_the_tool_server(tools, signed))
    meta = artifact.get_meta(aid)
    assert meta["owner"] == TAILNET_LOGIN
    assert meta["owner_webui_id"] == WEBUI_ID


def test_two_webui_users_do_not_share_a_page(surfaces):
    """The bridge must not collapse everyone onto one owner: two accounts,
    two owners, and neither reads the other's private page."""
    tools, web, _app = surfaces
    mine = _id_from(_publish_through_the_tool_server(
        tools, _identity(), title="Mine"))
    theirs = _id_from(_publish_through_the_tool_server(
        tools, _identity(email="kid@example.com", user="other-uuid"),
        title="Theirs"))
    assert mine != theirs
    assert artifact.get_meta(theirs)["owner"] == "kid@example.com"
    me = {"Tailscale-User-Login": TAILNET_LOGIN}
    assert web.get(f"/a/{mine}", headers=me).status_code == 200
    assert web.get(f"/a/{theirs}", headers=me).status_code == 404


def test_the_tool_listing_shows_the_caller_their_own_pages(surfaces):
    """D6 + D21: the model's own list_artifacts must find what it published
    (it used to look for pages owned by a UUID and find nothing)."""
    tools, _web, _app = surfaces
    _publish_through_the_tool_server(tools, _identity(), title="Mine")
    _publish_through_the_tool_server(
        tools, _identity(email="kid@example.com", user="other-uuid"),
        title="Theirs")
    out = tools.post("/list_artifacts", json={"limit": 25},
                     headers=_identity()).json()
    assert "Mine" in out
    assert "Theirs" not in out


def test_the_audit_row_carries_both_halves_of_the_identity(surfaces,
                                                           tmp_path):
    """Provenance an operator can act on: the page is owned by an email, and
    the row says which WebUI account published it."""
    tools, _web, _app = surfaces
    _publish_through_the_tool_server(tools, _identity())
    path = os.path.join(REPO, ".run", "tool-audit.jsonl")
    rows = [json.loads(x) for x in open(path).read().splitlines() if x.strip()]
    pub = [r for r in rows if r.get("tool") == "publish_artifact"][-1]
    assert pub["user"] == WEBUI_ID
    assert pub["artifact_owner"] == TAILNET_LOGIN
