"""beast-ark network layer: stdlib urllib, resumable, offline-aware, quiet about secrets.

Rules this file holds for the rest of the package:
  * Nothing dials while OFFLINE=true or island mode says the route is gone
    (agents/docs/config.offline_reason, shared with beast-docs): the caller
    gets OfflineError at once instead of a stall.
  * A bearer token (a registry token, GITHUB_TOKEN) is attached as an
    UNREDIRECTED header, so a 307 to a CDN or an S3 bucket never carries it,
    and it is never printed or logged — errors name the URL, not the headers.
  * Downloads land in `<dest>.part` and resume with a Range request; the
    final name appears only after the caller has verified the bytes.
"""
from __future__ import annotations

import hashlib
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

UA = "openbeast-ark/1 (cold-storage software archive; +https://github.com/MaximilianKhan/openbeast)"
CHUNK = 1 << 20
TIMEOUT = float(os.environ.get("ARK_HTTP_TIMEOUT", "60"))


class OfflineError(RuntimeError):
    pass


class HTTPFailure(RuntimeError):
    def __init__(self, url: str, status: int, msg: str = ""):
        super().__init__(f"HTTP {status} for {url}{(': ' + msg) if msg else ''}")
        self.url, self.status = url, status
        self.headers: dict = {}
        self.final_url = url


def offline_reason() -> Optional[str]:
    if os.environ.get("ARK_FORCE_OFFLINE") == "1":
        return "ARK_FORCE_OFFLINE=1"
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        from docs import config as dc  # noqa: PLC0415 — shared with beast-docs
        return dc.offline_reason()
    except Exception:                  # noqa: BLE001 — never fatal
        return None


def require_online(what: str) -> None:
    r = offline_reason()
    if r:
        raise OfflineError(f"{what} needs the network, and {r}. Use `ark verify` / `restore` / `serve`, which never dial.")


def _req(url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None,
         token: Optional[str] = None) -> urllib.request.Request:
    r = urllib.request.Request(url, method=method)
    r.add_header("User-Agent", UA)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    if token:
        r.add_unredirected_header("Authorization", token)
    return r


def _loopback(host: str) -> bool:
    h = (host or "").strip("[]").lower()
    return h in ("localhost", "::1") or h.startswith("127.")


class _NoDowngrade(urllib.request.HTTPRedirectHandler):
    """urllib follows an https -> http redirect without a word: everything
    after it (a checksum file, a release, a registry token exchange) travels
    in clear text, open to whoever sits on the path. Refused, except to
    loopback, where the test fixtures and `ark serve` live."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if old.scheme == "https" and new.scheme != "https" and not _loopback(new.hostname or ""):
            raise HTTPFailure(req.full_url, code, f"redirect to {new.scheme}://{new.hostname} downgrades https — refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_NoDowngrade)


def open_url(url: str, method: str = "GET", headers=None, token=None, timeout: float = TIMEOUT):
    try:
        return _OPENER.open(_req(url, method, headers, token), timeout=timeout)
    except urllib.error.HTTPError as e:
        hdrs = dict(e.headers or {})
        e.close()
        err = HTTPFailure(url, e.code, e.reason if isinstance(e.reason, str) else "")
        err.headers = {k.lower(): v for k, v in hdrs.items()}
        err.final_url = e.geturl() or url
        raise err from None
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise HTTPFailure(url, 0, str(reason)) from None


def get_bytes(url: str, limit: int = 64 << 20, headers=None, token=None) -> Tuple[bytes, dict]:
    with open_url(url, headers=headers, token=token) as r:
        data = r.read(limit + 1)
        if len(data) > limit:
            raise HTTPFailure(url, r.status, f"response larger than {limit} bytes")
        return data, {k.lower(): v for k, v in r.headers.items()}


def head(url: str, headers=None, token=None) -> Tuple[int, dict, str]:
    """-> (status, lower-cased headers, final URL). Falls back to a 1-byte
    ranged GET where a server refuses HEAD (some CDNs answer 403/405)."""
    try:
        with open_url(url, "HEAD", headers=headers, token=token) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.geturl()
    except HTTPFailure as e:
        if e.status not in (403, 405, 501):
            raise
    h = dict(headers or {})
    h["Range"] = "bytes=0-0"
    with open_url(url, headers=h, token=token) as r:
        hd = {k.lower(): v for k, v in r.headers.items()}
        cr = hd.get("content-range", "")
        if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
            hd["content-length"] = cr.rsplit("/", 1)[1]
        return r.status, hd, r.geturl()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def hash_file(path: str, algo: str) -> str:
    name = {"sha256": "sha256", "sha512": "sha512", "sha3-256": "sha3_256", "sha1": "sha1"}[algo]
    h = hashlib.new(name)
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def download(url: str, dest: str, size: int = 0, token=None, quiet: bool = False,
             headers=None, retries: int = 3) -> str:
    """Download to `dest + ".part"`, resuming what is there. Returns the .part
    path; the caller verifies it and renames it. `size`, when known, lets a
    complete .part skip the network entirely."""
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    for attempt in range(retries):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if size and have == size:
            return part
        if size and have > size:
            os.remove(part)
            have = 0
        h = dict(headers or {})
        if have:
            h["Range"] = f"bytes={have}-"
        try:
            with open_url(url, headers=h, token=token) as r:
                mode = "ab"
                if have and r.status != 206:
                    mode, have = "wb", 0            # server ignored Range: start over
                total = r.headers.get("Content-Length")
                total = (int(total) + have) if (total and total.isdigit()) else size
                last = time.time()
                with open(part, mode) as f:
                    got = have
                    while True:
                        b = r.read(CHUNK)
                        if not b:
                            break
                        f.write(b)
                        got += len(b)
                        if not quiet and sys.stderr.isatty() and time.time() - last > 1:
                            pct = f"{100 * got // total}%" if total else f"{got >> 20} MiB"
                            sys.stderr.write(f"\r    {os.path.basename(dest)}: {pct}   ")
                            last = time.time()
                if not quiet and sys.stderr.isatty():
                    sys.stderr.write("\r" + " " * 70 + "\r")
                if total and os.path.getsize(part) < total:
                    raise HTTPFailure(url, 0, "connection closed early")
                return part
        except HTTPFailure as e:
            if e.status == 416 and have:              # already complete
                return part
            if e.status in (0, 429, 500, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(2 ** attempt)
                continue
            raise
    return part
