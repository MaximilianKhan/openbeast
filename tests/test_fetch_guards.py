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

import tools
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


if __name__ == "__main__":
    unittest.main()
