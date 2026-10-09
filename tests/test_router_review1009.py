"""Network-facing review 2026-10-09, S1 and S4: the agent router.

S1  The router holds the admin tool key and spends it on the caller's
    say-so. A page on another site could borrow it: a no-preflight text/plain
    POST, a rebound DNS name, a typed role header.
S4  With an mmproj loaded llama-server downloads any URL named as a media
    part; the router forwarded them.

In-process only: router.app under an ASGI test client, its upstream client
replaced by a recorder that answers the classify with spawn=true. No port is
opened, no stack is touched, nothing is spawned.

Run: python3 -m pytest tests/test_router_review1009.py -q
"""
import asyncio
import json
import os
import sys

import pytest
from starlette.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

import router  # noqa: E402
from instinct.routerhook import RouterInstinct  # noqa: E402

SPAWN_TEXT = "spawn a background agent to migrate the whole test suite"
AGENT_ID = "20261009-120000-0123abcd"


# ── the router, end to end with a recorded upstream ─────────────────────────

class _Upstream:
    """The router's httpx client: answers the classify with spawn=true,
    /start_agent with an agent id, and records every call."""

    def __init__(self):
        self.classified = []
        self.spawned = []
        self.proxied = []

    async def post(self, url, json=None, headers=None, timeout=None):
        rec = {"url": url, "json": json, "headers": dict(headers or {})}
        if url.endswith("/start_agent"):
            self.spawned.append(rec)
            text = AGENT_ID
        else:
            self.classified.append(rec)
            text = '{"spawn": true, "task": "migrate the whole test suite", "workdir": "."}'

        class R:
            def json(self_inner):
                if url.endswith("/start_agent"):
                    return text
                return {"choices": [{"message": {"content": text}}]}
        R.text = text
        return R()

    def build_request(self, method, url, content=None, headers=None):
        req = {"method": method, "url": url, "content": content,
               "headers": dict(headers or {})}
        self.proxied.append(req)
        return req

    async def send(self, req, stream=False):
        class Resp:
            status_code = 200
            headers = {"content-type": "application/json"}

            async def aiter_raw(self_inner):
                yield b'{"ok": true}'

            async def aclose(self_inner):
                pass
        return Resp()

    async def aclose(self):
        pass


@pytest.fixture()
def rig(monkeypatch):
    """A single-user rig: no WebUI auth, no JWT, no inference key, a keyed
    tool server (the router holds ADMINKEY) — netsec S1 scenario A."""
    monkeypatch.setattr(router, "REQUIRE_IDENTITY", False)
    monkeypatch.setattr(router, "JWT_SECRET", "")
    monkeypatch.setattr(router, "_LLAMA_KEY", "")
    monkeypatch.setattr(router, "MCPO_HEADERS", {"Authorization": "Bearer ADMINKEY"})
    monkeypatch.setattr(router, "_INSTINCT", RouterInstinct("off"))

    def send(path="/v1/chat/completions", *, host="127.0.0.1:8088", body=None,
             headers=None, ctype="application/json", text=SPAWN_TEXT):
        up = _Upstream()
        if body is None:
            body = json.dumps({"model": "m", "messages": [
                {"role": "user", "content": text}]}).encode()
        hdrs = dict(headers or {})
        if ctype:
            hdrs["Content-Type"] = ctype
        with TestClient(router.app, base_url=f"http://{host}") as c:
            router.app.state.client = up
            r = c.post(path, content=body, headers=hdrs)
        asyncio.run(router._INSTINCT.drain())
        return r, up
    return send


def test_the_frontend_request_still_spawns(rig):
    """The control for everything below: WebUI's own request shape works."""
    r, up = rig()
    assert r.status_code == 200
    assert AGENT_ID in r.json()["choices"][0]["message"]["content"]
    assert len(up.spawned) == 1
    assert up.spawned[0]["headers"]["Authorization"] == "Bearer ADMINKEY"


# ── S1: the confused deputy ─────────────────────────────────────────────────

def test_s1_as_reported_spawns_nothing(rig):
    """Host: evil.example:8088, Origin: http://evil.example, text/plain."""
    r, up = rig(host="evil.example:8088", ctype="text/plain",
                headers={"Origin": "http://evil.example"})
    assert r.status_code == 400
    assert up.classified == [] and up.spawned == [] and up.proxied == []


def test_rebound_host_is_refused_even_with_json_and_an_admin_header(rig):
    """A rebound page is same-origin: it CAN send JSON and type the header."""
    r, up = rig(host="evil.example:8088",
                headers={"X-OpenWebUI-User-Role": "admin"})
    assert r.status_code == 400
    assert "OPENBEAST_ROUTER_ALLOWED_HOSTS" in r.text
    assert up.classified == [] and up.spawned == [] and up.proxied == []


def test_rebound_host_is_refused_on_passthrough_and_root(rig):
    with TestClient(router.app, base_url="http://evil.example:8088") as c:
        up = _Upstream()
        router.app.state.client = up
        assert c.get("/").status_code == 400
        assert c.get("/v1/models").status_code == 400
        assert up.proxied == []
    with TestClient(router.app, base_url="http://localhost:8088") as c:
        up = _Upstream()
        router.app.state.client = up
        assert c.get("/").status_code == 200
        assert c.get("/v1/models").status_code == 200
        assert len(up.proxied) == 1


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "https://evil.example:8088"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "cross-site", "Origin": "http://localhost:3000"},
    {"Origin": "http://localhost.evil.example"},
])
def test_cross_site_post_is_refused(rig, headers):
    """Right Host (a plain CSRF, no rebinding), a browser's own headers."""
    for ctype in ("text/plain", "application/json", ""):
        r, up = rig(headers=headers, ctype=ctype)
        assert r.status_code == 403, (headers, ctype)
        assert r.json()["error"]["type"] == "cross_site_refused"
        assert up.classified == [] and up.spawned == [] and up.proxied == []
    # ...and on the proxied POST routes, which reach llama-server just the same.
    r, up = rig(path="/v1/completions", headers=headers, body=b'{"prompt": "x"}')
    assert r.status_code == 403
    assert up.proxied == []


@pytest.mark.parametrize("headers", [
    {},                                                     # WebUI backend, OpenCode, curl
    {"Origin": "http://localhost:3000"},                    # WebUI's own page
    {"Origin": "http://127.0.0.1:3000", "Sec-Fetch-Site": "same-site"},
    {"Origin": "https://beast.tail1234.ts.net", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://[::1]:3000"},
])
def test_our_own_origins_are_not_cross_site(rig, headers):
    r, up = rig(headers=headers)
    assert r.status_code == 200
    assert len(up.spawned) == 1


@pytest.mark.parametrize("ctype", [
    "text/plain", "", "application/x-www-form-urlencoded",
    "multipart/form-data; boundary=x", "application/jsonx", "text/json",
])
def test_only_a_json_content_type_reaches_the_spawn_path(rig, ctype):
    """What a page can send cross-site with no preflight must never spawn.
    It is still a chat turn: proxied untouched, not refused."""
    r, up = rig(ctype=ctype)
    assert r.status_code == 200
    assert up.classified == [] and up.spawned == []
    assert len(up.proxied) == 1
    assert json.loads(up.proxied[0]["content"])["messages"][0]["content"] == SPAWN_TEXT


@pytest.mark.parametrize("ctype", ["application/json", "Application/JSON; charset=utf-8"])
def test_json_content_type_spellings_are_accepted(rig, ctype):
    r, up = rig(ctype=ctype)
    assert len(up.spawned) == 1


def test_keyed_rig_needs_the_inference_key_to_spawn(rig, monkeypatch):
    """Scenario B: a local process types the admin role header. On a keyed
    rig it must also hold what every configured frontend holds."""
    monkeypatch.setattr(router, "_LLAMA_KEY", "sk-rig")
    monkeypatch.setattr(router, "REQUIRE_IDENTITY", True)      # WEBUI_AUTH=true
    admin = {"X-OpenWebUI-User-Role": "admin"}
    for extra in ({}, {"Authorization": "Bearer wrong"},
                  {"Authorization": "Bearer café".encode("latin-1")},
                  {"Authorization": "sk-rig"},
                  {"Authorization": "Bearer ADMINKEY"}):
        r, up = rig(headers={**admin, **extra})
        assert r.status_code == 200, extra
        assert up.classified == [] and up.spawned == [], extra
        assert len(up.proxied) == 1, extra
    r, up = rig(headers={**admin, "Authorization": "Bearer sk-rig"})
    assert len(up.spawned) == 1
    # The key is proof of a frontend, not of a role: a guest turn WebUI
    # relays carries it too.
    r, up = rig(headers={"X-OpenWebUI-User-Role": "user",
                         "Authorization": "Bearer sk-rig"})
    assert up.classified == [] and up.spawned == []


def test_keyed_single_user_rig_no_longer_spawns_for_an_anonymous_caller(rig, monkeypatch):
    """Scenario A with LLAMA_API_KEY set: no identity is fail-open here, so
    the key is the only thing between a local caller and the admin key."""
    monkeypatch.setattr(router, "_LLAMA_KEY", "sk-rig")
    r, up = rig()
    assert up.classified == [] and up.spawned == []
    r, up = rig(headers={"Authorization": "Bearer sk-rig"})
    assert len(up.spawned) == 1


def test_presents_inference_key_is_inert_without_a_key():
    assert router._presents_inference_key({}, key="")
    assert router._presents_inference_key({"Authorization": "Bearer x"}, key="")
    assert not router._presents_inference_key({}, key="k")
    assert router._presents_inference_key({"authorization": "bearer k"}, key="k")


# ── S4: media URLs ──────────────────────────────────────────────────────────

def _chat(part):
    return json.dumps({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "what is this"}, part]}]}).encode()


REMOTE_PARTS = [
    {"type": "image_url", "image_url": {"url": "http://127.0.0.1:3000/api/config"}},
    {"type": "image_url", "image_url": {"url": "https://169.254.169.254/latest"}},
    {"type": "image_url", "image_url": {"url": "HTTP://10.0.0.1/x.png"}},
    {"type": "image_url", "image_url": {"url": "  http://10.0.0.1/x.png"}},
    {"type": "image_url", "image_url": {"url": "file:///etc/passwd"}},
    {"type": "image_url", "image_url": {"url": "ftp://10.0.0.1/x"}},
    {"type": "image_url", "image_url": {"url": "httpbin.org/image/png"}},   # curl adds http://
    {"type": "input_audio", "input_audio": {"url": "http://10.0.0.1/a.wav"}},
    {"type": "input_audio", "input_audio": {"data": "http://10.0.0.1/a.wav", "format": "wav"}},
    {"type": "input_video", "input_video": {"url": "http://10.0.0.1/a.mp4"}},
    {"type": "input_video", "input_video": {"data": "file://clip.mp4"}},
    # The rule shared with beast-gate (agents/mediapolicy.py): a bare string
    # under input_audio is judged, and " data:" is not a data: URI.
    {"type": "input_audio", "input_audio": "http://10.0.0.1/a.wav"},
    {"type": "image_url", "image_url": {"url": " data:image/png;base64,iVBORw0KGgo="}},
]
INLINE_PARTS = [
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
    {"type": "image_url", "image_url": {"url": "DATA:image/png;base64,iVBORw0KGgo="}},
    {"type": "image_url", "image_url": {"url": "iVBORw0KGgo/+A=="}},        # raw base64
    {"type": "input_audio", "input_audio": {"data": "UklGRiQAAABXQVZF", "format": "wav"}},
    {"type": "input_video", "input_video": {"data": "data:video/mp4;base64,AAAA"}},
    {"type": "text", "text": "see http://example.com/pic.png and file:///x"},
    {"type": "image_url", "image_url": {"url": None}},                      # upstream's error
]


@pytest.mark.parametrize("part", REMOTE_PARTS, ids=lambda p: json.dumps(p)[:70])
def test_remote_media_is_refused_before_forwarding(rig, part):
    r, up = rig(body=_chat(part))
    assert r.status_code == 400
    msg = r.json()["error"]["message"]
    assert "remote media URL refused" in msg and "data:" in msg
    assert up.proxied == [] and up.classified == [] and up.spawned == []


@pytest.mark.parametrize("part", INLINE_PARTS, ids=lambda p: json.dumps(p)[:70])
def test_inline_media_and_plain_text_pass_untouched(rig, part):
    body = _chat(part)
    r, up = rig(body=body)
    assert r.status_code == 200
    assert len(up.proxied) == 1
    assert up.proxied[0]["content"] == body         # byte for byte


@pytest.mark.parametrize("path,body", [
    # llama-server answers chat under these names too.
    ("/chat/completions", {"messages": [{"role": "user", "content": [REMOTE_PARTS[0]]}]}),
    ("/v1/chat/completions/input_tokens",
     {"messages": [{"role": "user", "content": [REMOTE_PARTS[0]]}]}),
    ("/apply-template", {"messages": [{"role": "user", "content": [REMOTE_PARTS[7]]}]}),
    ("/v1/responses", {"input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": "http://127.0.0.1:3000/x.png"}]}]}),
    ("/v1/messages", {"messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "url", "url": "http://10.0.0.1/x.png"}}]}]}),
    ("/v1/messages", {"messages": [{"role": "user", "content": [
        {"type": "tool_result", "content": [
            {"type": "image", "source": {"type": "url", "url": "http://10.0.0.1/x"}}]}]}]}),
])
def test_remote_media_is_refused_on_every_chat_shaped_route(rig, path, body):
    r, up = rig(path=path, body=json.dumps(body).encode())
    assert r.status_code == 400
    assert "remote media URL refused" in r.json()["error"]["message"]
    assert up.proxied == []


@pytest.mark.parametrize("path,body", [
    ("/v1/responses", {"input": [{"role": "user", "content": [
        {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0KGgo="}]}]}),
    ("/v1/messages", {"messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": "iVBORw0KGgo="}}]}]}),
    # `source` means a media source only inside an image block.
    ("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}],
                              "metadata": {"source": {"url": "https://example.com/a"}}}),
    ("/v1/embeddings", {"input": "http://example.com is a string, not a media part"}),
    ("/tokenize", {"content": "file:///etc/passwd"}),
])
def test_bodies_without_remote_media_are_forwarded(rig, path, body):
    raw = json.dumps(body).encode()
    r, up = rig(path=path, body=raw, text="")
    assert r.status_code == 200
    assert up.proxied[0]["content"] == raw


@pytest.mark.parametrize("raw", [
    # Python cannot parse these; llama-server's parser can, so forwarding
    # them unread would carry the URL past the check.
    b'{"x": ' + b"[" * 100_000 + b"]" * 100_000
    + b', "messages": [{"role": "user", "content": [{"type": "image_url", '
      b'"image_url": {"url": "http://127.0.0.1:3000/"}}]}]}',
    b'{"n": ' + b"9" * 5000 + b', "messages": [{"role": "user", "content": '
      b'[{"type": "image_url", "image_url": {"url": "http://127.0.0.1:3000/"}}]}]}',
    b"not json at all",
    b'\xff\xfe{\x00"\x00',                       # UTF-16: json.loads would sniff it
], ids=["deep", "bigint", "garbage", "utf16"])
def test_unparseable_post_bodies_fail_closed(rig, raw):
    for path in ("/v1/chat/completions", "/chat/completions", "/v1/responses"):
        r, up = rig(path=path, body=raw)
        assert r.status_code == 400, path
        assert "JSON" in r.json()["error"]["message"] or "UTF-8" in r.json()["error"]["message"]
        assert up.proxied == [] and up.spawned == []


def test_deeply_nested_media_is_found_without_recursing():
    body = {"image_url": {"url": "http://10.0.0.1/"}}
    for _ in range(50_000):
        body = [body]
    assert router._remote_media(body) == "image_url"


def test_bodiless_and_multipart_posts_still_pass(rig):
    """POST /slots/0?action=erase has no body; a transcription upload is
    multipart, which no chat route can read as JSON."""
    r, up = rig(path="/slots/0", body=b"", ctype="")
    assert r.status_code == 200 and len(up.proxied) == 1
    form = b'--x\r\nContent-Disposition: form-data; name="file"\r\n\r\nRIFF\r\n--x--\r\n'
    r, up = rig(path="/v1/audio/transcriptions", body=form,
                ctype="multipart/form-data; boundary=x")
    assert r.status_code == 200 and up.proxied[0]["content"] == form
    # ...but a multipart LABEL does not excuse a JSON body from the check.
    r, up = rig(path="/chat/completions", body=_chat(REMOTE_PARTS[0]),
                ctype="multipart/form-data; boundary=x")
    assert r.status_code == 400 and up.proxied == []
