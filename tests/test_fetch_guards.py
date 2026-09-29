#!/usr/bin/env python3
"""
Unit tests for the fetch() SSRF guards (agents/tools.py — RBAC Phase 2).

fetch executes server-side as the stack's Unix user, and (since Phase 2) is
exposed to guest-tier WebUI accounts. These tests pin the guard contract:
http/https only, and NO request may target loopback / private / link-local /
reserved address space — whether named directly, via a hostname that
resolves privately, or via a redirect hop.

All guard tests are network-free (getaddrinfo is monkeypatched where a real
resolution would leave the box). The one live-network assertion is gated
behind OPENBEAST_SKIP_NETWORK_TESTS like the tests in test_tools.py.

Run: python -m pytest tests/test_fetch_guards.py -v
  or: python3 tests/test_fetch_guards.py
"""

import os
import socket
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))

from tools import fetch, _fetch_url_blocked, _FetchRedirectHandler


class TestSchemeAllowlist(unittest.TestCase):
    def test_file_scheme_refused(self):
        result = fetch("file:///etc/passwd")
        self.assertIn("Error: fetch blocked", result)
        self.assertIn("scheme", result)

    def test_ftp_scheme_refused(self):
        result = fetch("ftp://ftp.example.com/pub/file.txt")
        self.assertIn("Error: fetch blocked", result)
        self.assertIn("scheme", result)

    def test_gopher_scheme_refused(self):
        self.assertIn("Error: fetch blocked", fetch("gopher://example.com/"))

    def test_no_hostname_refused(self):
        self.assertIn("Error: fetch blocked", fetch("http://"))


class TestPrivateAddressGuard(unittest.TestCase):
    """Literal private/loopback/link-local targets must be refused BEFORE any
    request is made (these resolve locally, no network needed)."""

    BLOCKED = [
        "http://127.0.0.1:3001/openapi.json",   # MCPO admin tools
        "http://localhost/",                      # loopback via name
        "http://192.168.1.1/",                    # RFC1918
        "http://10.0.0.1/",                       # RFC1918
        "http://172.16.0.1/",                     # RFC1918
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://0.0.0.0/",                        # unspecified
        "http://[::1]/",                          # v6 loopback
    ]

    def test_blocked_targets_refused(self):
        for url in self.BLOCKED:
            result = fetch(url)
            self.assertIn("Error: fetch blocked", result,
                          f"private target not blocked: {url}")

    def test_error_names_the_offending_address(self):
        result = fetch("http://127.0.0.1:3001/bash")
        self.assertIn("127.0.0.1", result)


class TestDnsRebindShape(unittest.TestCase):
    """A PUBLIC hostname that resolves to a private address (attacker-run
    DNS) must be refused — simulate by monkeypatching getaddrinfo."""

    def _patch_resolution(self, addrs):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port or 80))
                    for a in addrs]
        self._orig = socket.getaddrinfo
        socket.getaddrinfo = fake_getaddrinfo
        self.addCleanup(setattr, socket, "getaddrinfo", self._orig)

    def test_public_name_resolving_privately_refused(self):
        self._patch_resolution(["10.13.37.1"])
        result = fetch("http://evil-but-public-looking.example.com/")
        self.assertIn("Error: fetch blocked", result)
        self.assertIn("10.13.37.1", result)

    def test_mixed_resolution_refused_if_any_private(self):
        # One public A record + one private: MUST still refuse (the OS may
        # pick either; the guard checks ALL results).
        self._patch_resolution(["93.184.216.34", "127.0.0.1"])
        result = fetch("http://sneaky.example.com/")
        self.assertIn("Error: fetch blocked", result)

    def test_all_public_resolution_passes_guard(self):
        self._patch_resolution(["93.184.216.34"])
        self.assertIsNone(_fetch_url_blocked("http://fine.example.com/"))

    def test_unresolvable_host_refused(self):
        result = fetch("https://this-domain-does-not-exist-xyz.invalid/")
        self.assertIn("Error: fetch blocked", result)
        self.assertIn("resolve", result)


class TestRedirectRevalidation(unittest.TestCase):
    """Every redirect hop goes back through the guard: a public URL 302'ing
    to localhost must raise inside the opener (surfaced as a URL error)."""

    def test_redirect_to_private_raises(self):
        handler = _FetchRedirectHandler()
        with self.assertRaises(urllib.error.URLError) as ctx:
            handler.redirect_request(
                req=None, fp=None, code=302, msg="Found", headers={},
                newurl="http://127.0.0.1:3001/bash")
        self.assertIn("fetch blocked", str(ctx.exception.reason))

    def test_redirect_to_file_scheme_raises(self):
        handler = _FetchRedirectHandler()
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(
                req=None, fp=None, code=301, msg="Moved", headers={},
                newurl="file:///etc/shadow")


class TestPublicUrlAllowed(unittest.TestCase):
    @unittest.skipIf(os.environ.get("OPENBEAST_SKIP_NETWORK_TESTS") == "1",
                     "network tests disabled (OPENBEAST_SKIP_NETWORK_TESTS=1)")
    def test_guard_allows_public_https(self):
        # Guard-level check with real DNS on a stable public name (no HTTP
        # request, but DNS is still network — gated). The mocked equivalent
        # is TestDnsRebindShape.test_all_public_resolution_passes_guard.
        self.assertIsNone(_fetch_url_blocked("https://example.com"))

    @unittest.skipIf(os.environ.get("OPENBEAST_SKIP_NETWORK_TESTS") == "1",
                     "network tests disabled (OPENBEAST_SKIP_NETWORK_TESTS=1)")
    def test_fetch_public_url_end_to_end(self):
        result = fetch("https://example.com/")
        self.assertNotIn("Error: fetch blocked", result)
        self.assertIn("Example Domain", result)


class TestDNSRebindingPin(unittest.TestCase):
    """The IP the guard vets must be the IP the socket dials — no separate
    connect-time resolution a rebinding DNS server could flip."""

    def setUp(self):
        self._gai = socket.getaddrinfo
        self._cc = socket.create_connection

    def tearDown(self):
        socket.getaddrinfo = self._gai
        socket.create_connection = self._cc

    def test_socket_dials_vetted_ip_no_reresolution_at_connect(self):
        dialed = {}
        socket.getaddrinfo = lambda h, p, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", p or 80))]

        def cc(addr, *a, **k):
            dialed["ip"] = addr[0]
            raise OSError("stub dial")
        socket.create_connection = cc
        fetch("http://pin.example/")
        self.assertEqual(dialed["ip"], "93.184.216.34")

    def test_connect_time_flip_to_loopback_is_refused(self):
        n = {"i": 0}

        def flip(h, p, *a, **k):
            n["i"] += 1
            ip = "93.184.216.34" if n["i"] == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, p or 80))]
        socket.getaddrinfo = flip
        out = fetch("http://rebind.example/")
        self.assertIn("non-public address", out)


class TestReadFileHazardMounts(unittest.TestCase):
    """read_file must refuse pseudo-filesystems (procfs/sysfs/devfs) — they
    look regular but can be infinite/side-effecting."""

    def test_proc_refused(self):
        from tools import read_file
        self.assertIn("pseudo-filesystem", read_file("/proc/self/stat"))

    def test_dev_zero_refused(self):
        from tools import read_file
        self.assertIn("pseudo-filesystem", read_file("/dev/zero"))

    def test_normal_file_still_reads(self, ):
        import tempfile
        from tools import read_file
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("a\nb\nc\n")
            name = f.name
        out = read_file(name)
        self.assertIn("a\n", out)
        os.unlink(name)


class TestTailnetCGNAT(unittest.TestCase):
    """Tailscale CGNAT (100.64.0.0/10) policy is pinned in _vet_addr, not
    inherited from the interpreter's is_private (which flipped for this range
    in CPython 3.12.4/3.11.9): blocked by default on EVERY Python, allowed
    only via OPENBEAST_FETCH_ALLOW_TAILNET."""

    def setUp(self):
        self._saved = os.environ.pop("OPENBEAST_FETCH_ALLOW_TAILNET", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("OPENBEAST_FETCH_ALLOW_TAILNET", None)
        else:
            os.environ["OPENBEAST_FETCH_ALLOW_TAILNET"] = self._saved

    def test_cgnat_blocked_by_default(self):
        from tools import _vet_addr
        reason = _vet_addr("100.100.1.1")
        self.assertIsNotNone(reason)
        self.assertIn("tailnet", reason)

    def test_cgnat_allowed_with_env(self):
        from tools import _vet_addr
        os.environ["OPENBEAST_FETCH_ALLOW_TAILNET"] = "1"
        self.assertIsNone(_vet_addr("100.100.1.1"))

    def test_rfc1918_blocked_regardless(self):
        from tools import _vet_addr
        os.environ["OPENBEAST_FETCH_ALLOW_TAILNET"] = "1"
        self.assertIsNotNone(_vet_addr("10.0.0.1"))
        self.assertIsNotNone(_vet_addr("192.168.1.1"))
        self.assertIsNotNone(_vet_addr("127.0.0.1"))

    def test_cgnat_edges(self):
        from tools import _vet_addr
        # 100.64.0.0 and 100.127.255.255 are in-range; 100.63.x / 100.128.x out.
        self.assertIsNotNone(_vet_addr("100.64.0.0"))
        self.assertIsNotNone(_vet_addr("100.127.255.255"))
        self.assertIsNone(_vet_addr("100.63.255.255"))
        self.assertIsNone(_vet_addr("100.128.0.0"))

    def test_v4_mapped_cgnat_blocked(self):
        # Regression: a v4-mapped v6 literal skipped the v4-only CGNAT check
        # while still dialing the v4 target — SSRF onto the tailnet with the
        # opt-in OFF. Modern CPython reports is_private False for this form.
        from tools import _vet_addr
        reason = _vet_addr("::ffff:100.64.1.2")
        self.assertIsNotNone(reason)
        self.assertIn("tailnet", reason)

    def test_v4_mapped_private_still_blocked(self):
        from tools import _vet_addr
        self.assertIsNotNone(_vet_addr("::ffff:127.0.0.1"))
        self.assertIsNotNone(_vet_addr("::ffff:192.168.1.5"))
        self.assertIsNotNone(_vet_addr("::ffff:169.254.169.254"))

    def test_tailscale_v6_ula_follows_the_optin(self):
        # MagicDNS returns A *and* AAAA; the opt-in must cover both families
        # or fetch-by-name stays blocked despite FETCH_ALLOW_TAILNET=1.
        from tools import _vet_addr
        ula = "fd7a:115c:a1e0::1234"
        reason = _vet_addr(ula)
        self.assertIsNotNone(reason)
        self.assertIn("tailnet", reason)
        os.environ["OPENBEAST_FETCH_ALLOW_TAILNET"] = "1"
        self.assertIsNone(_vet_addr(ula))

    def test_other_ula_stays_blocked_under_optin(self):
        # The opt-in is for TAILNET hosts, not every private v6 range.
        from tools import _vet_addr
        os.environ["OPENBEAST_FETCH_ALLOW_TAILNET"] = "1"
        self.assertIsNotNone(_vet_addr("fd00::1"))
        self.assertIsNotNone(_vet_addr("::1"))


class _FakeResp:
    """Minimal urllib response: a byte body behind read()/read1()."""

    def __init__(self, body: bytes, ctype="text/html; charset=utf-8"):
        import io
        self._buf = io.BytesIO(body)
        self.headers = {"Content-Type": ctype}

    def read(self, n=-1):
        return self._buf.read(n)

    def read1(self, n=-1):
        return self._buf.read1(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, body: bytes):
        self.body = body

    def open(self, req, timeout=None):
        return _FakeResp(self.body)


class TestHtmlStripIsLinear(unittest.TestCase):
    """The HTML stripper runs under the GIL inside the shared tool server:
    a page of unterminated openers must not trigger quadratic backtracking
    (the old patterns took ~6 s per pass on a default-size 200 KB body)."""

    def setUp(self):
        import tools
        self.tools = tools
        self._opener = tools._fetch_opener
        self._blocked = tools._fetch_url_blocked
        tools._fetch_url_blocked = lambda url: None  # no DNS: body is stubbed

    def tearDown(self):
        self.tools._fetch_opener = self._opener
        self.tools._fetch_url_blocked = self._blocked

    def _timed_fetch(self, body: bytes):
        import time
        self.tools._fetch_opener = _FakeOpener(body)
        t0 = time.monotonic()
        out = self.tools.fetch("http://tarpit.example/")
        return out, time.monotonic() - t0

    def test_unterminated_script_openers(self):
        _, dt = self._timed_fetch(b"<html>" + b"<script>" * 25_000)
        self.assertLess(dt, 1.5)

    def test_bare_angle_brackets(self):
        _, dt = self._timed_fetch(b"<html>" + b"<" * 200_000)
        self.assertLess(dt, 1.5)

    def test_unterminated_style_and_break_openers(self):
        _, dt = self._timed_fetch(b"<html>" + b"<style" * 30_000 + b"<br" * 60_000)
        self.assertLess(dt, 1.5)

    def test_stripping_semantics_kept(self):
        out, _ = self._timed_fetch(
            b"<html><head><style>p{color:red}</style>"
            b"<script type='x'>var SECRET_JS = 1;</script></head>"
            b"<body><p>Hello <b>world</b></p><br/>next &amp; last"
            b"<script>never closed SCRIPT_TAIL")
        self.assertIn("Hello world", out)
        self.assertIn("next & last", out)
        for gone in ("SECRET_JS", "color:red", "SCRIPT_TAIL", "<b>", "<p>"):
            self.assertNotIn(gone, out)


class TestFetchTotalDeadline(unittest.TestCase):
    """urllib's timeout= is per socket operation: a server dripping a byte
    every few seconds used to hold a tool-server worker until the 8 MB cap
    or EOF. fetch now has a whole-request deadline (_FETCH_DEADLINE)."""

    def setUp(self):
        import threading
        import tools
        self.tools = tools
        self._saved = (tools._fetch_url_blocked, tools._resolve_vetted,
                       getattr(tools, "_FETCH_DEADLINE", None))
        # Loopback tarpit: bypass the SSRF guard for this server only.
        tools._fetch_url_blocked = lambda url: None
        tools._resolve_vetted = lambda host, port, scheme: (["127.0.0.1"], None)
        tools._FETCH_DEADLINE = 1.0
        self.stop = threading.Event()
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.threads = []

    def tearDown(self):
        (self.tools._fetch_url_blocked, self.tools._resolve_vetted,
         self.tools._FETCH_DEADLINE) = self._saved
        self.stop.set()
        self.srv.close()
        for t in self.threads:
            t.join(timeout=5)

    def _serve(self, head: bytes, drip: bytes, every: float):
        import threading

        def run():
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.recv(65536)
                    conn.sendall(head)
                    for b in drip:
                        if self.stop.wait(every):
                            return
                        conn.sendall(bytes([b]))
                    self.stop.wait(30)  # then stall, holding the socket open
                except OSError:
                    return  # the client hung up — the point of the test
        t = threading.Thread(target=run, daemon=True)
        t.start()
        self.threads.append(t)

    def _timed_fetch(self):
        import time
        t0 = time.monotonic()
        out = self.tools.fetch(f"http://127.0.0.1:{self.port}/")
        return out, time.monotonic() - t0

    def test_body_drip_is_cut_at_the_deadline(self):
        self._serve(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                    b"Content-Length: 300\r\n\r\n", b"a" * 300, 0.05)
        out, dt = self._timed_fetch()
        self.assertLess(dt, 5.0, out)          # was ~15 s: the whole drip
        self.assertIn("deadline", out)
        self.assertIn("aaa", out)              # partial body kept

    def test_header_drip_is_cut_at_the_deadline(self):
        self._serve(b"HTTP/1.1 200 OK\r\n", b"X-Slow: " + b"z" * 300, 0.05)
        out, dt = self._timed_fetch()
        self.assertLess(dt, 5.0, out)
        self.assertIn("deadline", out)

    def test_stalled_tls_handshake_is_cut_at_the_deadline(self):
        # The socket is registered BEFORE wrap_socket, so a server that
        # never answers the ClientHello is cut too (the dup survives the
        # detach TLS wrapping does to the original socket object).
        self._serve(b"", b"", 0)
        import time
        t0 = time.monotonic()
        out = self.tools.fetch(f"https://127.0.0.1:{self.port}/")
        self.assertLess(time.monotonic() - t0, 5.0, out)
        self.assertIn("deadline", out)

    def test_fast_server_unaffected(self):
        # Negative control: a prompt response is returned whole, no note.
        self._serve(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                    b"Content-Length: 5\r\n\r\nhello", b"", 0)
        out, _ = self._timed_fetch()
        self.assertEqual(out, "hello")


class TestFetchThroughProxy(unittest.TestCase):
    """With http_proxy/https_proxy set, fetch used to pin the TARGET's IP but
    keep the PROXY's port (dialing target_ip:3128), so every proxied fetch
    failed. It must dial the proxy and still vet the target by name."""

    _NAMES = {"pub.example": "93.184.216.34", "priv.example": "10.0.0.5",
              "proxy.test": "10.9.9.9"}  # a private proxy is normal

    def setUp(self):
        import tools
        self.tools = tools
        self._env = {k: os.environ.pop(k) for k in list(os.environ)
                     if k.lower() in ("http_proxy", "https_proxy", "no_proxy",
                                      "all_proxy")}
        os.environ["http_proxy"] = "http://proxy.test:3128"
        os.environ["https_proxy"] = "http://proxy.test:3128"
        self._opener = tools._fetch_opener
        tools._fetch_opener = tools._build_fetch_opener()
        self._gai = socket.getaddrinfo
        self._cc = socket.create_connection
        socket.getaddrinfo = lambda h, p, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (self._NAMES[h], p or 80))]
        self.dialed = []

        def cc(addr, *a, **k):
            self.dialed.append(addr)
            raise OSError("stub dial")
        socket.create_connection = cc

    def tearDown(self):
        socket.getaddrinfo = self._gai
        socket.create_connection = self._cc
        self.tools._fetch_opener = self._opener
        for k in ("http_proxy", "https_proxy"):
            os.environ.pop(k, None)
        os.environ.update(self._env)

    def test_http_target_dials_the_proxy(self):
        self.tools.fetch("http://pub.example/page")
        self.assertEqual(self.dialed, [("proxy.test", 3128)])

    def test_https_target_dials_the_proxy(self):
        self.tools.fetch("https://pub.example/page")
        self.assertEqual(self.dialed, [("proxy.test", 3128)])

    def test_private_target_still_refused_through_proxy(self):
        # Negative control: the proxy path must not skip the SSRF vet. The
        # name passes fetch()'s up-front check, then resolves privately at
        # open time — the proxied branch must re-vet and refuse to dial.
        n = {"i": 0}

        def flip(h, p, *a, **k):
            if h == "flip.example":
                n["i"] += 1
                ip = "93.184.216.34" if n["i"] == 1 else "10.0.0.5"
            else:
                ip = self._NAMES[h]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, p or 80))]
        socket.getaddrinfo = flip
        for url in ("https://flip.example/", "http://flip.example/"):
            n["i"] = 0
            out = self.tools.fetch(url)
            self.assertIn("non-public address", out)
        self.assertEqual(self.dialed, [])

    def test_unproxied_opener_still_pins_target_ip(self):
        for k in ("http_proxy", "https_proxy"):
            os.environ.pop(k, None)
        self.tools._fetch_opener = self.tools._build_fetch_opener()
        self.tools.fetch("http://pub.example/page")
        self.assertEqual(self.dialed, [("93.184.216.34", 80)])


if __name__ == "__main__":
    unittest.main()
