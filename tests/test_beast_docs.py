#!/usr/bin/env python3
"""beast-docs — the platform documentation mirror (docs/BEAST_DOCS.md).

Hermetic: a stub HTTP server on 127.0.0.1 serves synthetic DocC JSON,
Android-shaped markdown + llms.txt and a DevSite-shaped HTML page from
tests/fixtures/beast_docs/ (synthetic text — no publisher's content is
committed). Nothing here touches the network.

Pinned:
  * the adapters, by golden markdown (DocC JSON, HTML, the markdown cleanup)
  * incremental sync: an unchanged ETag is a 304 and rewrites nothing; an
    unchanged body without validators rewrites nothing either; a changed one
    does; a fresh page is not re-requested; an interrupted crawl resumes
  * politeness and scope: robots.txt refusal (page and JSON URL), a robots
    5xx refuses the whole host, redirects off the source's hosts are refused,
    a private address is refused without the explicit opt-in
  * the terms gate on sources that need one
  * search ranking (exact title first; FTS5 and the fallback scan agree)
  * offline / island: sync refuses, live falls back to the mirror and says so
  * the tarball round trip, and its refusals (bad hash, tampered member,
    unsafe member)
  * the CLI end to end, and the shipped sources.toml

Run: python3 -m pytest tests/test_beast_docs.py -q
"""
from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(REPO, "agents")
FIX = os.path.join(REPO, "tests", "fixtures", "beast_docs")
GOLDEN = os.path.join(FIX, "golden")
if AGENTS not in sys.path:
    sys.path.insert(0, AGENTS)

from docs import api, config as C, docc, htmlmd as H, net, store as ST, sync as S  # noqa: E402

UPDATE_GOLDEN = os.environ.get("BEAST_DOCS_UPDATE_GOLDEN") == "1"


def golden(name: str, text: str) -> None:
    path = os.path.join(GOLDEN, name)
    if UPDATE_GOLDEN:
        os.makedirs(GOLDEN, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(path, encoding="utf-8") as fh:
        assert text == fh.read(), f"{name} drifted (BEAST_DOCS_UPDATE_GOLDEN=1 to accept)"


# ── the stub server ──────────────────────────────────────────────────────

class Stub:
    """Serves the fixture tree. DocC: /tutorials/data/documentation/<p>.json
    -> docc/<p>.json; everything else -> android/<path>. ETag = sha of the
    body (off with etag=False, as Android's .md.txt sends none)."""

    def __init__(self):
        self.requests: list = []        # (path, headers dict)
        self.robots = "User-agent: *\nDisallow: /private/\n"
        self.robots_status = 200
        self.overrides: dict = {}       # path -> bytes
        self.redirects: dict = {}       # path -> location
        self.etag = True
        stub = self

        class H_(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                stub.requests.append((self.path, dict(self.headers)))
                path = self.path.split("?", 1)[0]
                if path in stub.redirects:
                    self.send_response(302)
                    self.send_header("Location", stub.redirects[path])
                    self.end_headers()
                    return
                if path == "/robots.txt":
                    body = stub.robots.encode()
                    self.send_response(stub.robots_status)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = stub.overrides.get(path)
                if body is None:
                    if path.startswith("/tutorials/data/documentation/"):
                        f = os.path.join(FIX, "docc", path[len("/tutorials/data/documentation/"):])
                    else:
                        f = os.path.join(FIX, "android", path.lstrip("/"))
                    f = os.path.normpath(f)
                    if not f.startswith(FIX) or not os.path.isfile(f):
                        self.send_response(404)
                        self.end_headers()
                        return
                    with open(f, "rb") as fh:
                        body = fh.read()
                etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
                if stub.etag and self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.end_headers()
                    return
                self.send_response(200)
                ctype = ("application/json" if path.endswith(".json") else
                         "text/markdown" if path.endswith(".md.txt") else "text/html")
                self.send_header("Content-Type", ctype)
                if stub.etag:
                    self.send_header("ETag", etag)
                    self.send_header("Last-Modified", "Mon, 26 Oct 2026 05:37:49 GMT")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H_)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.hostport = f"127.0.0.1:{self.port}"         # hosts entries carry the port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def paths(self):
        return [p for p, _h in self.requests]

    def close(self):
        self.httpd.shutdown()


SOURCES = """
[[source]]
name = "swiftui"
title = "Test SwiftUI"
adapter = "apple-docc-json"
hosts = ["{host}"]
roots = ["{base}/documentation/swiftui"]
allow_prefixes = ["/documentation/swiftui"]
max_pages = 50
max_depth = 4
delay = 0.0
refresh_days = 30
license = "synthetic fixture"
{ack}

[[source]]
name = "android-compose"
title = "Test Compose"
adapter = "markdown"
hosts = ["{host}"]
roots = ["{base}/develop/ui/compose/lists"]
discovery = "llms-txt"
index_url = "{base}/develop/ui/llms.txt"
allow_prefixes = ["/develop/ui/compose"]
max_pages = 50
max_depth = 3
delay = 0.0
refresh_days = 30
license = "synthetic fixture"

[[source]]
name = "html-site"
title = "Test HTML"
adapter = "html"
hosts = ["{host}"]
roots = ["{base}/page.html"]
allow_prefixes = ["/page.html"]
max_pages = 5
max_depth = 0
delay = 0.0
refresh_days = 30
content_selectors = ["div.devsite-article-body"]
strip_selectors = [".nocontent"]
"""


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


@pytest.fixture
def env(tmp_path, monkeypatch, stub):
    """A throwaway store + sources file pointed at the stub."""
    src = tmp_path / "sources.toml"
    src.write_text(SOURCES.format(base=stub.base, host=stub.hostport, ack=""))
    monkeypatch.setenv("OPENBEAST_DOCS_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("OPENBEAST_DOCS_SOURCES", str(src))
    monkeypatch.setenv("OPENBEAST_DOCS_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("OPENBEAST_OFFLINE", "false")
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.delenv("OPENBEAST_DOCS_NO_FTS", raising=False)
    return {"tmp": tmp_path, "stub": stub, "sources": src}


def _sync(name, **kw):
    src = C.load_sources()[name]
    return S.sync(src, ST.Store(), log=lambda *_a: None, **kw)


# ── adapters (golden) ────────────────────────────────────────────────────

def _docc(name):
    with open(os.path.join(FIX, "docc", name)) as fh:
        return json.load(fh)


def test_docc_symbol_golden():
    title, md = docc.to_markdown(_docc("swiftui/navigationstack.json"),
                                 "https://developer.apple.com/documentation/swiftui/navigationstack")
    assert title == "NavigationStack"
    golden("navigationstack.md", md)
    # the load-bearing pieces, so a golden refresh cannot quietly drop them
    assert "```swift\nstruct NavigationStack<Data, Root> where Root : View\n```" in md
    assert "**Availability:** iOS 16.0+ · macOS 13.0+ · visionOS 1.0+ (beta)" in md
    assert "- `root` — The view at the bottom of the stack." in md
    assert "> **Note:** The root view cannot be popped." in md
    assert "| iOS | Pushes \\| slides |" in md
    assert "[image: A diagram of a stack]" in md
    assert "<https://developer.apple.com/documentation/swiftui/navigationview>" in md


def test_docc_deprecation_is_loud_and_grouped():
    _t, md = docc.to_markdown(_docc("swiftui/navigationview.json"))
    assert "**DEPRECATED:** iOS 26.0, macOS 26.0 — use NavigationStack instead" in md
    assert "> Use `NavigationStack` instead." in md
    assert docc.summary(_docc("swiftui/navigationview.json"))["deprecated"] is True
    golden("navigationview.md", md)


def test_docc_url_mapping_and_links():
    assert docc.json_url("https://developer.apple.com/documentation/SwiftUI/View/navigationDestination(for:destination:)") == \
        "https://developer.apple.com/tutorials/data/documentation/swiftui/view/navigationdestination(for:destination:).json"
    links = docc.links(_docc("swiftui.json"), "https://developer.apple.com/documentation/swiftui")
    assert links[0] == "https://developer.apple.com/documentation/swiftui/navigationstack"   # topics first
    assert "https://developer.apple.com/documentation/uikit/elsewhere" in links              # scope is sync's job


def test_html_adapter_golden():
    with open(os.path.join(FIX, "android", "page.html")) as fh:
        html = fh.read()
    r = H.html_to_markdown(html, ["div.devsite-article-body"], [".nocontent"])
    assert r["title"] == "Lazy lists"                       # hidden chrome in the h1 dropped
    assert r["updated"] == "2026-11-14"
    md = r["markdown"]
    golden("page.html.md", md)
    assert '```kotlin\nLazyColumn {\n    items(100) { Text("Row $it") }\n}\n```' in md   # not flattened
    for gone in ("View on GitHub", "invisible chrome", "Copy", "analytics", "Nav only link",
                 "Bookmark this page", "color: red"):
        assert gone not in md, gone
    assert "> **Note:** keys must be stable." in md
    assert "  1. nested one" in md
    links = H.html_links(html, "https://developer.android.com/develop/ui/compose/lists")
    assert "https://developer.android.com/develop/ui/compose/state" in links   # ?hl= and #frag gone


def test_markdown_adapter_cleanup():
    with open(os.path.join(FIX, "android", "develop", "ui", "compose", "lists.md.txt")) as fh:
        raw = fh.read()
    r = H.clean_markdown(raw, "Lists and grids", "Display collections.")
    md = r["markdown"]
    golden("lists.md", md)
    assert md.startswith("# Lists and grids\n\n> Display collections.\n")
    assert "Documentation index" not in md and "<br />" not in md
    assert "Use `LazyColumn` for long lists" in md            # link -> text, nested parens handled
    assert "items(messages, key = { it.id })" in md
    links = H.markdown_links(raw, "https://developer.android.com/develop/ui/compose/lists.md.txt")
    assert "https://developer.android.com/develop/ui/compose/state" in links
    assert any("LazyColumn.composable" in u and not u.endswith(".md.txt") for u in links)


def test_llms_index_and_sitemap_parsing():
    with open(os.path.join(FIX, "android", "develop", "ui", "llms.txt")) as fh:
        entries = H.llms_index(fh.read(), "https://developer.android.com/develop/ui/llms.txt")
    assert entries[0] == ("https://developer.android.com/develop/ui/compose/lists", "Lists and grids",
                          "Display collections with lazy lists and grids.")
    locs, is_index = H.sitemap_locs('<?xml version="1.0"?><sitemapindex><sitemap><loc>https://a/x.xml</loc>'
                                    '</sitemap></sitemapindex>')
    assert is_index and locs == ["https://a/x.xml"]


# ── config ───────────────────────────────────────────────────────────────

def test_shipped_sources_file_is_valid():
    srcs = C.load_sources(C.DEFAULT_SOURCES)
    assert {"swiftui", "uikit", "swift", "xcode", "android-compose", "android-build"} <= set(srcs)
    for s in srcs.values():
        assert s.license, f"{s.name}: every source states its licence"
        for r in s.roots:
            assert s.in_scope(r)
    assert all(srcs[n].requires_ack for n in ("swiftui", "uikit", "swift", "xcode", "app-review"))
    assert not srcs["swift"].in_scope("https://developer.apple.com/documentation/swiftui/view")


def test_toml_subset_parser_matches_tomllib_and_refuses_the_rest():
    text = open(C.DEFAULT_SOURCES).read()
    try:
        import tomllib
    except ImportError:
        pytest.skip("no tomllib on this interpreter")
    assert C.parse_toml_subset(text) == tomllib.loads(text)
    for bad in ('[table]\nx = 1\n', 'x = 2020-01-01\n', '[[source]]\nname = "a"\nname = "b"\n',
                'x = [\n"unterminated"\n'):
        with pytest.raises(C.ConfigError):
            C.parse_toml_subset(bad)


def test_bad_source_is_refused(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('[[source]]\nname = "x"\nadapter = "ftp"\nhosts = ["a"]\nroots = ["https://a/x"]\n'
                 'allow_prefixes = ["/x"]\n')
    with pytest.raises(C.ConfigError, match="adapter"):
        C.load_sources(str(p))
    p.write_text('[[source]]\nname = "x"\nadapter = "html"\nhosts = ["a"]\nroots = ["https://b/x"]\n'
                 'allow_prefixes = ["/x"]\n')
    with pytest.raises(C.ConfigError, match="outside"):
        C.load_sources(str(p))


# ── robots ───────────────────────────────────────────────────────────────

def test_robots_parser_wildcards_longest_match_and_groups():
    rb = net.parse_robots("User-Agent: *\nDisallow: /*?*authuser=\nDisallow: /images/\n"
                          "Allow: /images/social/\nDisallow: /x$\n\n"
                          "User-agent: Google-DevRel\nAllow: /\n")
    assert not rb.allowed("https://h/develop?authuser=1")
    assert rb.allowed("https://h/develop?hl=en")
    assert not rb.allowed("https://h/images/a.png")
    assert rb.allowed("https://h/images/social/a.png")       # longer Allow wins
    assert not rb.allowed("https://h/x") and rb.allowed("https://h/x/y")
    mine = net.parse_robots("User-agent: *\nDisallow: /\n\nUser-agent: openbeast-docs\nDisallow: /secret\n"
                            "Crawl-delay: 5\n")
    assert mine.allowed("https://h/public") and not mine.allowed("https://h/secret/x")
    assert mine.crawl_delay == 5.0


def test_sync_obeys_robots_for_the_page_and_never_requests_it(env):
    env["stub"].robots = "User-agent: *\nDisallow: /documentation/swiftui/navigationview\n"
    st = _sync("swiftui")
    assert st["refused"] >= 1
    assert not any("navigationview" in p for p in env["stub"].paths())
    assert st["new"] >= 3


def test_robots_5xx_refuses_the_whole_host(env):
    env["stub"].robots_status = 503
    st = _sync("swiftui")
    assert st["new"] == 0 and st["refused"] >= 1
    assert env["stub"].paths() == ["/robots.txt"]


def test_robots_404_means_allowed(env):
    env["stub"].robots_status = 404
    assert _sync("swiftui")["new"] >= 4


# ── scope / SSRF ─────────────────────────────────────────────────────────

def test_redirect_off_the_source_hosts_is_refused(env):
    env["stub"].redirects["/tutorials/data/documentation/swiftui/view.json"] = "http://localhost:1/evil"
    st = _sync("swiftui")
    assert st["refused"] >= 1
    assert "view" not in json.dumps(ST.Store().load("swiftui")["pages"].get(
        f"{env['stub'].base}/documentation/swiftui/view", {}))


def test_private_address_refused_without_opt_in(env, monkeypatch):
    monkeypatch.delenv("OPENBEAST_DOCS_ALLOW_PRIVATE")
    f = net.Fetcher([env["stub"].hostport], 0)
    with pytest.raises(net.Refused, match="non-public|blocked"):
        f.get(env["stub"].base + "/robots.txt")
    with pytest.raises(net.Refused, match="not one of this source's hosts"):
        net.Fetcher([env["stub"].hostport], 0).get("http://example.com/x")
    assert env["stub"].requests == []


def test_url_normalizer():
    from docs import urls as U
    n = U.normalize_url
    assert n("https://H.example/docs/../secret/x#f") == "https://h.example/secret/x"
    assert n("https://h/docs/./private/x") == "https://h/docs/private/x"
    assert n("https://h/docs/%2e%2e/secret/x") == "https://h/secret/x"
    assert n("https://h/docs/%2E%2e/%2e/a") == "https://h/a"
    assert n("https://h/../../x") == "https://h/x"                       # never above the root
    assert n("https://h/a/b/..") == "https://h/a/"
    assert n("https://h:443/a?q=1") == "https://h/a?q=1"                 # default port dropped
    assert n("http://h:8080/%7Euser/%41") == "http://h:8080/~user/A"     # unreserved decoded
    assert n("https://h/a%2Fb") == "" and n("https://h/a%5cb") == "" and n("https://h/a\\b") == ""
    assert n("https://developer.apple.com@evil.example/x") == ""         # userinfo
    assert n("https://h:99999/x") == "" and n("ftp://h/x") == "" and n("https:///x") == ""
    assert U.host_allowed(["docs.corp"], "https://docs.corp/x")
    assert U.host_allowed(["docs.corp"], "http://docs.corp:80/x")
    assert not U.host_allowed(["docs.corp"], "http://docs.corp:6379/x")
    assert U.host_allowed(["docs.corp:8443"], "https://docs.corp:8443/x")
    assert not U.host_allowed(["docs.corp:8443"], "https://docs.corp/x")


def test_scope_and_robots_decide_on_the_normalized_url():
    src = C.Source(name="x", title="x", adapter="html", hosts=["docs.corp"],
                   roots=["https://docs.corp/docs/"], allow_prefixes=["/docs/"],
                   deny_prefixes=["/docs/private"])
    assert src.in_scope("https://docs.corp/docs/a")
    for bad in ("https://docs.corp/docs/../secret/x", "https://docs.corp/docs/%2e%2e/secret/x",
                "https://docs.corp/docs/./private/x", "https://docs.corp/docs/%2E/private/x",
                "http://docs.corp:6379/docs/x",                    # another port on the host
                "https://u:p@docs.corp/docs/x"):                    # userinfo
        assert not src.in_scope(bad), bad
    rb = net.parse_robots("User-agent: *\nDisallow: /secret\n")
    assert not rb.allowed("https://docs.corp/docs/../secret/x")
    assert not rb.allowed("https://docs.corp/docs/%2e%2e/secret/x")
    assert rb.allowed("https://docs.corp/docs/x")
    with pytest.raises(ValueError, match="dot segment"):          # the DocC mapping refuses too
        docc.json_url("https://developer.apple.com/documentation/swiftui/%2e%2e/%2e%2e/x")
    # a double-encoded dot stays a literal, re-encoded: never a segment a server resolves
    assert docc.json_url("https://developer.apple.com/documentation/swiftui/%252e%252e/x") == \
        "https://developer.apple.com/tutorials/data/documentation/swiftui/%252e%252e/x.json"


def test_live_dot_segments_are_normalized_before_robots_and_request(env):
    stub = env["stub"]
    stub.robots = "User-agent: *\nDisallow: /documentation/swiftui/navigationview\n"
    for trick in ("/documentation/swiftui/x/../navigationview",
                  "/documentation/swiftui/x/%2e%2e/navigationview",
                  "/documentation/swiftui/./navigationview"):
        out = api.live_text(stub.base + trick, source="swiftui")
        assert "LIVE FETCH UNAVAILABLE" in out and "robots.txt disallows" in out, out
    assert not any("navigationview" in p for p in stub.paths())
    assert not any(".." in p or "%2e" in p.lower() or "/./" in p for p in stub.paths())
    out = api.live_text(stub.base + "/documentation/swiftui/x/../navigationstack", source="swiftui")
    assert out.startswith(f"Source: {stub.base}/documentation/swiftui/navigationstack\nLIVE")
    assert "/tutorials/data/documentation/swiftui/navigationstack.json" in stub.paths()


def test_fetcher_pins_the_port(env):
    stub = env["stub"]
    f = net.Fetcher([stub.hostport], 0)
    with pytest.raises(net.Refused, match="not one of this source's hosts"):
        f.get("http://127.0.0.1:1/robots.txt")
    with pytest.raises(net.Refused, match="not a fetchable URL"):
        f.get(f"http://x@{stub.hostport}/robots.txt")
    stub.redirects["/tutorials/data/documentation/swiftui/view.json"] = "http://127.0.0.1:1/documentation/swiftui/view"
    st = _sync("swiftui")
    assert st["refused"] >= 1
    assert f"{stub.base}/documentation/swiftui/view" not in ST.Store().load("swiftui")["pages"]


def test_terms_gate(env, stub):
    env["sources"].write_text(SOURCES.format(base=stub.base, host=stub.hostport, ack="requires_ack = true"))
    with pytest.raises(S.SyncError, match="--accept-terms"):
        _sync("swiftui")
    assert stub.requests == []
    _sync("swiftui", accept_terms=True)
    assert ST.Store().load("swiftui")["terms_accepted_at"]
    _sync("swiftui", force=True)                               # recorded: no flag needed again


def test_live_save_obeys_the_terms_gate(env, stub):
    env["sources"].write_text(SOURCES.format(base=stub.base, host=stub.hostport, ack="requires_ack = true"))
    url = f"{stub.base}/documentation/swiftui/navigationstack"
    out = api.live_text(url, save=True)
    assert out.startswith("Error: not saved") and "--accept-terms" in out
    assert stub.requests == []                                  # refused before any request
    assert "swiftui" not in ST.Store().sources_on_disk()
    out = api.live_text(url)                                    # reading live needs no acceptance
    assert "LIVE · fetched" in out and "saved" not in out.split("\n")[1]
    out = api.live_text(url, save=True, accept_terms=True)
    assert "saved to the mirror" in out
    m = ST.Store().load("swiftui")
    assert m["terms_accepted_at"] and url in m["pages"]
    assert "saved to the mirror" in api.live_text(url, save=True)   # recorded: no flag needed again


# ── incremental / resumable ──────────────────────────────────────────────

def _mtimes(name):
    d = os.path.join(ST.Store().source_dir(name), "pages")
    return {f: os.stat(os.path.join(d, f)).st_mtime_ns for f in os.listdir(d)}


def test_unchanged_etag_is_a_304_and_rewrites_nothing(env):
    st = _sync("swiftui")
    assert st["new"] == 5 and st["errors"] == 0, st          # root + 4 pages, uikit link out of scope
    before = _mtimes("swiftui")
    man = json.dumps(ST.Store().load("swiftui")["pages"], sort_keys=True)
    time.sleep(0.01)
    env["stub"].requests.clear()
    st2 = _sync("swiftui", force=True)
    assert st2["not_modified"] == 5 and st2["new"] == st2["updated"] == 0
    assert _mtimes("swiftui") == before
    sent = [h for p, h in env["stub"].requests if p.endswith(".json")]
    assert sent and all(h.get("If-None-Match") for h in sent)
    assert all(h.get("If-Modified-Since") for h in sent)
    after = ST.Store().load("swiftui")["pages"]
    assert {u: r["fetched_at"] for u, r in json.loads(man).items()} == \
        {u: r["fetched_at"] for u, r in after.items()}           # fetched_at kept; checked_at moved


def test_fresh_pages_are_not_requested_again(env):
    _sync("swiftui")
    env["stub"].requests.clear()
    st = _sync("swiftui")
    assert st["skipped_fresh"] == 5 and st["fetched"] == 0
    assert env["stub"].requests == []


def test_no_validators_same_body_no_rewrite_changed_body_updates(env):
    env["stub"].etag = False
    st = _sync("android-compose")
    assert st["new"] == 2, st                                  # lists + state; views/interop out of scope
    before = _mtimes("android-compose")
    st2 = _sync("android-compose", force=True)
    assert st2["unchanged"] == 2 and _mtimes("android-compose") == before
    env["stub"].overrides["/develop/ui/compose/state.md.txt"] = b"# State and Jetpack Compose\n\nNow about rememberSaveable.\n"
    st3 = _sync("android-compose", force=True)
    assert st3["updated"] == 1 and st3["unchanged"] == 1
    hits = api.search("rememberSaveable")
    assert hits and hits[0]["title"] == "State and Jetpack Compose"


def test_llms_txt_titles_are_used(env):
    _sync("android-compose")
    rec = ST.Store().load("android-compose")["pages"][f"{env['stub'].base}/develop/ui/compose/lists"]
    assert rec["title"] == "Lists and grids"
    assert not any("views/interop" in p for p in env["stub"].paths())


def test_interrupted_sync_resumes_where_it_stopped(env):
    st = _sync("swiftui", max_pages=2)
    assert st["new"] == 2 and st["frontier_left"] >= 1
    m = ST.Store().load("swiftui")
    assert m["sync_complete"] is False and m["frontier"]
    st2 = _sync("swiftui", max_pages=50)
    assert st2["resumed"] is True and st2["new"] == 3
    assert ST.Store().load("swiftui")["sync_complete"] is True


def test_html_source_sync(env):
    st = _sync("html-site")
    assert st["new"] == 1
    out = api.read_text("html-site:/page.html")
    assert out.startswith(f"Source: {env['stub'].base}/page.html\n")
    assert "# Lazy lists" in out and "View on GitHub" not in out
    assert "page_updated" in open(os.path.join(ST.Store().source_dir("html-site"),
                                               ST.Store().load("html-site")["pages"][
                                                   f"{env['stub'].base}/page.html"]["file"])).read()


# ── search ───────────────────────────────────────────────────────────────

def _search_titles(q, **kw):
    return [r["title"] for r in api.search(q, **kw)]


@pytest.mark.parametrize("no_fts", ["0", "1"])
def test_search_ranking(env, monkeypatch, no_fts):
    monkeypatch.setenv("OPENBEAST_DOCS_NO_FTS", no_fts)
    _sync("swiftui")
    _sync("android-compose")
    assert ST.fts5_available() is (no_fts == "0" and ST.fts5_available())
    # the guide says "NavigationStack" four times; the symbol page still wins
    assert _search_titles("NavigationStack")[0] == "NavigationStack"
    assert _search_titles("navigation stack")[0] in ("NavigationStack", "Understanding navigation")
    assert "NavigationStack" in _search_titles("navigation stack")[:2]
    assert _search_titles("LazyColumn")[0] == "Lists and grids"
    assert _search_titles("LazyColumn", source="swiftui") == []
    assert api.search("") == []
    rows = api.search("NavigationView")
    assert rows[0]["deprecated"] is True
    out = api.search_text("NavigationStack")
    assert out.startswith("=== platform docs:")
    assert f"{env['stub'].base}/documentation/swiftui/navigationstack" in out
    assert "fetched 20" in out                                 # every hit carries its date


def test_status_flags_stale_mirrors(env):
    _sync("swiftui")
    s = ST.Store()
    m = s.load("swiftui")
    for rec in m["pages"].values():
        rec["checked_at"] = "2026-01-01T00:00:00Z"
    s.save("swiftui", m)
    rows = {r["source"]: r for r in api.status_rows()}
    assert rows["swiftui"]["stale"] is True and rows["swiftui"]["pages"] == 5
    assert rows["android-compose"]["state"] == "not mirrored"
    assert "STALE" in api.status_text()
    assert "STALE" in api.search_text("NavigationStack")


# ── live / offline / island ──────────────────────────────────────────────

def test_live_fetches_through_the_adapter(env):
    out = api.live_text(f"{env['stub'].base}/documentation/swiftui/navigationstack")
    assert out.startswith(f"Source: {env['stub'].base}/documentation/swiftui/navigationstack\nLIVE · fetched")
    assert "struct NavigationStack<Data, Root>" in out
    out = api.live_text("NavigationView", source="swiftui", save=True)     # symbol -> guessed URL
    assert "DEPRECATED" in out and "saved to the mirror" in out
    assert api.read_text("swiftui:/documentation/swiftui/navigationview")


def test_live_refuses_urls_outside_every_source(env):
    assert api.live_text("https://example.com/whatever").startswith("Error:")


def test_offline_sync_refuses_and_live_falls_back_to_the_mirror(env, monkeypatch):
    _sync("swiftui")
    monkeypatch.setenv("OPENBEAST_OFFLINE", "true  # air-gapped")
    with pytest.raises(S.SyncError, match="offline"):
        _sync("swiftui")
    env["stub"].requests.clear()
    out = api.live_text(f"{env['stub'].base}/documentation/swiftui/navigationstack")
    assert out.startswith("LIVE FETCH UNAVAILABLE (offline: OFFLINE=true")
    assert "Serving the MIRROR copy" in out and "fetched 20" in out
    assert "struct NavigationStack" in out
    assert env["stub"].requests == []                          # offline means NO request


def test_island_mode_is_offline_too(env, monkeypatch):
    _sync("swiftui")
    import island
    monkeypatch.setattr(island, "read_state",
                        lambda path=None: {"state": "island", "since_iso": "2026-11-15T10:00:00"})
    assert "ISLAND MODE" in C.offline_reason()
    out = api.live_text("NavigationStack", source="swiftui")
    assert out.startswith("LIVE FETCH UNAVAILABLE (offline: ISLAND MODE (no internet route since "
                          "2026-11-15T10:00:00))")


def test_live_failure_with_no_mirror_copy_says_so(env, monkeypatch):
    monkeypatch.setenv("OPENBEAST_OFFLINE", "1")
    out = api.live_text(f"{env['stub'].base}/documentation/swiftui/navigationstack")
    assert "is not in the mirror" in out


# ── bundles ──────────────────────────────────────────────────────────────

def test_tarball_round_trip(env, monkeypatch):
    _sync("swiftui")
    _sync("android-compose")
    tar = str(env["tmp"] / "mirror.tar.gz")
    r = ST.pack(ST.Store(), tar)
    assert r["sources"] == ["android-compose", "swiftui"] and os.path.isfile(tar + ".sha256")
    other = env["tmp"] / "offline-box"
    monkeypatch.setenv("OPENBEAST_DOCS_DIR", str(other))
    monkeypatch.setenv("OPENBEAST_OFFLINE", "true")
    u = ST.unpack(ST.Store(), tar)
    assert u["indexed"] == 7 and set(u["sources"]) == {"swiftui", "android-compose"}
    assert _search_titles("NavigationStack")[0] == "NavigationStack"
    assert api.read_text("swiftui:/documentation/swiftui/navigationstack")
    assert ST.Store().load("swiftui")["pages"]                 # manifest travelled


def test_unpack_refuses_bad_hash_tamper_and_unsafe_members(env, monkeypatch):
    _sync("swiftui")
    tar = str(env["tmp"] / "m.tar.gz")
    ST.pack(ST.Store(), tar)
    monkeypatch.setenv("OPENBEAST_DOCS_DIR", str(env["tmp"] / "dest"))
    with pytest.raises(ST.BundleError, match="does not match"):
        ST.unpack(ST.Store(), tar, expect_sha256="0" * 64)
    # tamper with one page, then re-hash the TARBALL (an attacker can do that
    # much): the per-file hashes in BUNDLE.json still catch it.
    bad = str(env["tmp"] / "bad.tar.gz")
    with tarfile.open(tar) as src, tarfile.open(bad, "w:gz") as dst:
        for m in src.getmembers():
            data = src.extractfile(m).read() if m.isfile() else None
            if m.name.endswith(".md") and data is not None:
                data = data.replace(b"NavigationStack", b"EvilStack")
                m.size = len(data)
            dst.addfile(m, io.BytesIO(data) if data is not None else None)
    with pytest.raises(ST.BundleError, match="sha256 mismatch"):
        ST.unpack(ST.Store(), bad, expect_sha256=ST.sha256_file(bad))
    evil = str(env["tmp"] / "evil.tar.gz")
    with tarfile.open(evil, "w:gz") as tf:
        ti = tarfile.TarInfo("../escape.txt")
        ti.size = 1
        tf.addfile(ti, io.BytesIO(b"x"))
    with pytest.raises(ST.BundleError, match="unsafe"):
        ST.unpack(ST.Store(), evil, expect_sha256=ST.sha256_file(evil))
    assert not (env["tmp"] / "escape.txt").exists()
    assert not ST.Store().sources_on_disk()                    # nothing installed by a refusal


# ── the CLI ─────────────────────────────────────────────

def test_docs_sh_end_to_end(env):
    e = dict(os.environ)
    run = lambda *a: subprocess.run([os.path.join(REPO, "scripts", "docs.sh"), *a],
                                    capture_output=True, text=True, env=e, timeout=60)
    r = run("sync", "swiftui", "--max-pages", "3", "--quiet")
    assert r.returncode == 0, r.stderr
    assert "3 fetched: 3 new" in r.stdout
    r = run("search", "NavigationStack")
    assert r.returncode == 0 and "swiftui:/documentation/swiftui/navigationstack" in r.stdout
    r = run("read", "swiftui:/documentation/swiftui/navigationstack")
    assert r.returncode == 0 and r.stdout.startswith("Source: ")
    assert run("search", "zzzzqqq").returncode == 1
    assert run("sync", "nope").returncode == 2
    e["OPENBEAST_OFFLINE"] = "yes"
    r = run("sync", "swiftui")
    assert r.returncode == 3 and "pack" in r.stderr
    r = run("status", "--json")
    assert json.loads(r.stdout)[0]["source"] == "swiftui"
