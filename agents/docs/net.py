"""The polite fetcher: one source's hosts, robots.txt, a delay, conditional GET.

Every request goes through the SAME SSRF machinery as the `fetch` tool
(agents/tools.py: resolve, vet every address, dial the vetted IP, re-vet each
redirect hop) — imported, not copied, because two copies of a security guard
drift silently. On top of it, beast-docs adds the one rule a crawler needs
that a fetch tool does not: **nothing outside the source's `hosts` is ever
contacted**, redirects included — host AND port (agents/docs/urls.py), and
every URL is normalized before it is checked, so the URL checked is the URL
requested.

OPENBEAST_DOCS_ALLOW_PRIVATE=1 drops the address vet (not the host
allowlist): for an intranet documentation server, and for the hermetic tests,
whose stub server is on 127.0.0.1.
"""
from __future__ import annotations

import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import config as C
from . import urls as U

# robots.txt answers shared by every live() call in this process (the MCP
# server is long-lived): origin -> (expires at, monotonic; Robots). RFC 9309
# §2.4 allows caching up to 24 h; one hour keeps a rule change visible the
# same day. An UNREACHABLE answer is never cached, so recovery is immediate.
LIVE_ROBOTS: dict = {}
LIVE_ROBOTS_TTL = 3600.0
_LIVE_ROBOTS_LOCK = threading.Lock()

MAX_BYTES = 16 * 1024 * 1024          # one page; Apple's biggest JSON is ~2 MB
DEADLINE = 60.0                        # whole request, connect to last byte
ROBOTS_MAX = 512 * 1024


class FetchError(Exception):
    pass


class Refused(FetchError):
    """Not fetched on purpose: robots, scope, or a host outside the source."""


@dataclass
class Response:
    url: str
    status: int                 # 200, or 304 for "unchanged"
    body: bytes = b""
    etag: str = ""
    last_modified: str = ""
    content_type: str = ""


# ── robots.txt (RFC 9309, with Google's * and $ wildcards) ───────────────

@dataclass
class Robots:
    rules: list = field(default_factory=list)     # [(allow: bool, pattern)]
    crawl_delay: float = 0.0
    disallow_all: bool = False
    unreachable: bool = False     # 5xx / 429 / transport: an outage, not a verdict
    note: str = ""

    @staticmethod
    def _match(pattern: str, path: str) -> bool:
        if not pattern:
            return False
        rx = re.escape(pattern).replace(r"\*", ".*")
        if rx.endswith(r"\$"):
            rx = rx[:-2] + "$"
        return re.match(rx, path) is not None

    def allowed(self, url: str) -> bool:
        """Matched against the NORMALIZED path: `/docs/../secret` is judged
        as `/secret`, which is what the server would serve."""
        if self.disallow_all:
            return False
        url = U.normalize_url(url)
        if not url:
            return False
        p = urllib.parse.urlsplit(url)
        path = (p.path or "/") + (("?" + p.query) if p.query else "")
        best_len, verdict = -1, True
        for allow, pat in self.rules:
            if self._match(pat, path):
                # Longest match wins; on a tie, Allow wins (RFC 9309 §2.2.2).
                if len(pat) > best_len or (len(pat) == best_len and allow):
                    best_len, verdict = len(pat), allow
        return verdict


def parse_robots(text: str, agent: str = C.UA_TOKEN) -> Robots:
    groups: list = []                  # [(agents[], rules[], delay)]
    cur = None
    last_was_agent = False
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (s.strip() for s in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if cur is None or not last_was_agent:
                cur = ([], [], None)
                groups.append(cur)
            cur[0].append(v.lower())
            last_was_agent = True
            continue
        last_was_agent = False
        if cur is None:
            continue
        if k in ("allow", "disallow"):
            if v or k == "allow":
                cur[1].append((k == "allow", v))
        elif k == "crawl-delay":
            try:
                groups[-1] = cur = (cur[0], cur[1], float(v))
            except ValueError:
                pass
    agent = agent.lower()
    mine = [g for g in groups if any(a != "*" and a in agent for a in g[0])]
    chosen = mine or [g for g in groups if "*" in g[0]]
    rb = Robots()
    for g in chosen:
        rb.rules += g[1]
        if g[2]:
            rb.crawl_delay = max(rb.crawl_delay, g[2])
    return rb


# ── the opener ───────────────────────────────────────────────────────────

def _tools():
    if C.AGENTS_DIR not in sys.path:
        sys.path.insert(0, C.AGENTS_DIR)
    import tools  # noqa: PLC0415  — the fetch tool's SSRF guard, not a copy
    return tools


def _allow_private() -> bool:
    import os  # noqa: PLC0415
    return os.environ.get("OPENBEAST_DOCS_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")


class _ScopedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts, vet):
        self.hosts, self.vet = list(hosts), vet

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        norm = U.normalize_url(newurl)
        if not norm or not U.host_allowed(self.hosts, norm):
            raise Refused(f"redirect to {newurl} leaves this source's hosts")
        newurl = norm
        if self.vet:
            reason = self.vet(newurl)
            if reason:
                raise Refused(f"redirect to {newurl}: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    """One crawl's HTTP client. Not thread-safe; one per sync."""

    def __init__(self, hosts, delay: float = 1.0, user_agent: str = C.USER_AGENT,
                 sleep=time.sleep, clock=time.monotonic, robots_cache=None):
        self.hosts = list(hosts)
        self.delay = max(0.0, float(delay))
        self.ua = user_agent
        self.sleep, self.clock = sleep, clock
        self._last: dict = {}           # origin -> monotonic time of last request
        self.robots: dict = {}          # origin (scheme://host[:port]) -> Robots
        self.robots_cache = robots_cache  # optional, shared across Fetchers (LIVE_ROBOTS)
        self.requests = 0
        if _allow_private():
            self._vet = None
            self._opener = urllib.request.build_opener(_ScopedRedirect(self.hosts, None))
        else:
            t = _tools()
            self._vet = t._fetch_url_blocked
            self._opener = urllib.request.build_opener(
                t._PinnedHTTPHandler(), t._PinnedHTTPSHandler(),
                _ScopedRedirect(self.hosts, self._vet))

    # politeness ------------------------------------------------------------
    def _wait(self, host: str) -> None:
        rb = self.robots.get(host)
        gap = max(self.delay, rb.crawl_delay if rb else 0.0)
        last = self._last.get(host)
        if last is not None:
            left = gap - (self.clock() - last)
            if left > 0:
                self.sleep(left)
        self._last[host] = self.clock()

    def robots_for(self, url: str) -> Robots:
        norm = U.normalize_url(url)
        if not norm:
            return Robots(disallow_all=True, note=f"{url}: not a fetchable URL")
        host = U.origin(norm)
        if host in self.robots:
            return self.robots[host]
        if self.robots_cache is not None:
            with _LIVE_ROBOTS_LOCK:
                hit = self.robots_cache.get(host)
            if hit and hit[0] > time.monotonic():
                self.robots[host] = hit[1]
                return hit[1]
        robots_url = f"{host}/robots.txt"
        try:
            r = self._request(robots_url, max_bytes=ROBOTS_MAX, check_robots=False)
            rb = parse_robots(r.body.decode("utf-8", "replace"))
            rb.note = f"{robots_url}: {len(rb.rules)} rule(s) for us"
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                rb = Robots(note=f"{robots_url}: HTTP {e.code} (no rules: crawl allowed)")
            else:                      # RFC 9309 §2.3.1.4: unreachable = disallow all
                rb = Robots(disallow_all=True, unreachable=True,
                            note=f"{robots_url}: HTTP {e.code} (unreachable: crawl refused)")
        except Refused as e:           # our own refusal (scope, SSRF vet): a verdict
            rb = Robots(disallow_all=True, note=f"{robots_url}: {e} (refused)")
        except (OSError, FetchError, urllib.error.URLError) as e:
            rb = Robots(disallow_all=True, unreachable=True,
                        note=f"{robots_url}: {e} (unreachable: crawl refused)")
        self.robots[host] = rb
        if self.robots_cache is not None and not rb.unreachable:
            with _LIVE_ROBOTS_LOCK:
                self.robots_cache[host] = (time.monotonic() + LIVE_ROBOTS_TTL, rb)
        return rb

    # the request -----------------------------------------------------------
    def get(self, url: str, etag: str = "", last_modified: str = "",
            max_bytes: int = MAX_BYTES) -> Response:
        """GET `url` politely. 304 comes back as status 304 (not an error).
        Raises Refused (scope / robots), urllib.error.HTTPError (4xx/5xx),
        or FetchError (transport)."""
        return self._request(url, etag, last_modified, max_bytes, check_robots=True)

    def _request(self, url, etag="", last_modified="", max_bytes=MAX_BYTES,
                 check_robots=True) -> Response:
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https"):
            raise Refused(f"scheme {p.scheme!r} not allowed")
        # Normalize, then check AND send that one spelling: a raw
        # `/docs/../secret` must not pass a prefix test and then be served
        # as /secret by the server.
        norm = U.normalize_url(url)
        if not norm:
            raise Refused(f"{url}: not a fetchable URL (userinfo, an encoded slash, or a bad port)")
        url = norm
        if not U.host_allowed(self.hosts, url):
            p = urllib.parse.urlsplit(url)
            raise Refused(f"{p.netloc} is not one of this source's hosts {sorted(self.hosts)}")
        host = U.origin(url)
        if check_robots:
            rb = self.robots_for(url)
            if not rb.allowed(url):
                raise Refused(f"robots.txt disallows {url} ({rb.note})")
        if self._vet:
            reason = self._vet(url)
            if reason:
                raise Refused(f"fetch blocked: {reason}")
        self._wait(host)
        headers = {"User-Agent": self.ua, "Accept": "application/json,text/html;q=0.9,*/*;q=0.5"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        req = urllib.request.Request(url, headers=headers)
        self.requests += 1
        return self._open(req, max_bytes)

    def _open(self, req, max_bytes) -> Response:
        # A whole-request deadline: urllib's timeout is per socket operation,
        # so a server dripping a byte every 29 s would hold us for days. The
        # pinned connections register their sockets in tools._fetch_state;
        # the watchdog shuts them down at the deadline.
        socks: list = []
        state = None
        if self._vet:
            state = _tools()._fetch_state
            state.socks = socks
        expired = threading.Event()

        def _expire():
            expired.set()
            for s in list(socks):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        dog = threading.Timer(DEADLINE, _expire)
        dog.daemon = True
        dog.start()
        try:
            try:
                resp = self._opener.open(req, timeout=30)
            except urllib.error.HTTPError as e:
                if e.code == 304:
                    return Response(url=req.full_url, status=304,
                                    etag=e.headers.get("ETag", "") or "",
                                    last_modified=e.headers.get("Last-Modified", "") or "")
                raise
            except Refused:
                raise
            except urllib.error.URLError as e:
                if isinstance(e.reason, Refused):
                    raise e.reason from None
                raise FetchError(f"{req.full_url}: {e.reason}") from None
            except (OSError, ValueError) as e:
                raise FetchError(f"{req.full_url}: {e}") from None
            with resp:
                buf = bytearray()
                read = getattr(resp, "read1", resp.read)
                while True:
                    try:
                        chunk = read(65536)
                    except (OSError, ValueError) as e:
                        raise FetchError(f"{req.full_url}: read failed: {e}") from None
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise FetchError(f"{req.full_url}: larger than {max_bytes} bytes")
                if expired.is_set():
                    raise FetchError(f"{req.full_url}: exceeded the {DEADLINE:.0f}s deadline")
                h = resp.headers
                return Response(url=resp.geturl(), status=resp.status, body=bytes(buf),
                                etag=h.get("ETag", "") or "",
                                last_modified=h.get("Last-Modified", "") or "",
                                content_type=h.get("Content-Type", "") or "")
        finally:
            dog.cancel()
            if state is not None:
                state.socks = None
            for s in socks:
                try:
                    s.close()
                except OSError:
                    pass
