"""One URL normalizer for every scope, robots and fetch decision.

A scope check on the raw string is not a scope check: `/docs/../secret`
starts with `/docs/`, and so does `/docs/%2e%2e/secret`, but every
normalizing server (nginx, Apache, any CDN) serves `/secret` for both. So the
URL is normalized ONCE, here, and the normalized form is both the URL that is
checked (Source.in_scope, robots) and the URL that is requested (net.Fetcher)
— there is no second spelling for a check to miss.

Normalizing means (RFC 3986 §6.2.2): lower-case scheme and host, drop the
default port, decode percent-escapes of UNRESERVED characters (`%2e` -> `.`,
`%7e` -> `~`), then remove dot segments (§5.2.4). A URL that cannot be made
unambiguous is refused outright (returned as ""): userinfo
(`https://developer.apple.com@evil/`), an encoded slash or backslash (a
server may or may not split on it), a bad port, a scheme other than http(s).

Pure functions, stdlib only, Python 3.9.
"""
from __future__ import annotations

import re
import urllib.parse

DEFAULT_PORT = {"http": 80, "https": 443}
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_HOST_ENTRY = re.compile(r"^(?P<host>[A-Za-z0-9.-]+)(?::(?P<port>\d{1,5}))?$")


def _decode_unreserved(s: str) -> str:
    def sub(m):
        ch = chr(int(m.group(1), 16))
        return ch if ch in _UNRESERVED else "%" + m.group(1).upper()
    return re.sub(r"%([0-9A-Fa-f]{2})", sub, s)


def remove_dot_segments(path: str) -> str:
    """RFC 3986 §5.2.4 for an absolute path: `/a/./b/../c` -> `/a/c`;
    `..` above the root stays at the root (`/../x` -> `/x`)."""
    if not path:
        return path
    segs = path.split("/")
    out: list = []
    last = len(segs) - 1
    for i, seg in enumerate(segs):
        if seg in (".", ".."):
            if seg == ".." and len(out) > 1:
                out.pop()
            if i == last:
                out.append("")                 # `/a/b/..` -> `/a/`, a directory
            continue
        out.append(seg)
    if not out or out[0] != "":
        out.insert(0, "")
    return "/".join(out) or "/"


def normalize_url(url: str, drop_query: bool = False) -> str:
    """The one spelling of `url` that is checked AND requested, or "" when
    there is no safe one. The fragment is always dropped (never sent)."""
    try:
        p = urllib.parse.urlsplit((url or "").strip())
        port = p.port                          # raises on a non-numeric / out-of-range port
    except ValueError:
        return ""
    scheme = p.scheme.lower()
    if scheme not in DEFAULT_PORT:
        return ""
    if p.username is not None or p.password is not None or "@" in p.netloc:
        return ""
    host = (p.hostname or "").lower()
    if not host:
        return ""
    path = _decode_unreserved(p.path or "/")
    if "\\" in path or re.search(r"%(2f|5c)", path, re.I):
        return ""
    path = remove_dot_segments(path)
    if any(seg in (".", "..") for seg in path.split("/")):   # cannot happen; belt and braces
        return ""
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != DEFAULT_PORT[scheme]:
        netloc += f":{port}"
    return urllib.parse.urlunsplit((scheme, netloc, path, "" if drop_query else p.query, ""))


def parse_host_entry(entry: str):
    """`developer.apple.com` -> (host, None); `127.0.0.1:8080` -> (host, 8080).
    None when the entry is not a host[:port]."""
    m = _HOST_ENTRY.match((entry or "").strip())
    if not m:
        return None
    port = int(m.group("port")) if m.group("port") else None
    if port is not None and not 0 < port < 65536:
        return None
    return m.group("host").lower(), port


def host_allowed(hosts, url: str) -> bool:
    """Is (host, EFFECTIVE port) of `url` one of `hosts`? A bare host entry
    means the scheme's default port only: `docs.corp` admits
    https://docs.corp/ and http://docs.corp/, never http://docs.corp:6379/."""
    try:
        p = urllib.parse.urlsplit(url or "")
        port = p.port
    except ValueError:
        return False
    scheme = p.scheme.lower()
    if scheme not in DEFAULT_PORT or p.username is not None or "@" in p.netloc:
        return False
    host = (p.hostname or "").lower()
    port = port if port is not None else DEFAULT_PORT[scheme]
    for h in hosts:
        e = parse_host_entry(h)
        if e and e[0] == host and (e[1] if e[1] is not None else DEFAULT_PORT[scheme]) == port:
            return True
    return False


def origin(url: str) -> str:
    """scheme://host[:port] — the key robots.txt and politeness are per."""
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme.lower()}://{p.netloc.lower()}"
