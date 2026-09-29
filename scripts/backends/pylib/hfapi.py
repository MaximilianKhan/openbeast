"""A small, read-only Hugging Face Hub client on the standard library.

Why not huggingface_hub: it is in the rig's lock but not guaranteed on a
Spark host (DGX OS's system python), and model-fetch has to verify every file
against the Hub's own hashes anyway. The four endpoints used here are the
Hub's public REST API:

    GET {endpoint}/api/models/{repo}/revision/{rev}           model info at a commit
    GET {endpoint}/api/models/{repo}/tree/{rev}?recursive=true  files, sizes, lfs.oid (sha256)
    GET {endpoint}/{repo}/resolve/{rev}/{path}                file bytes (302 to a CDN for LFS)

Settings (never argv):
    HF_ENDPOINT      default https://huggingface.co (a mirror, or a test stub)
    HF_TOKEN         the token itself, from the environment
    HF_TOKEN_FILE    a file holding the token; must not be group/world readable
    OFFLINE=true / OPENBEAST_OFFLINE=true / HF_HUB_OFFLINE=1   refuse the network
The token is sent only to the endpoint's own host: a redirect to another host
(the LFS CDN) drops the Authorization header.
"""
from __future__ import annotations

import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TRUE = {"true", "yes", "1", "on"}


class HubError(RuntimeError):
    pass


class Offline(HubError):
    pass


def offline() -> bool:
    for key in ("OPENBEAST_OFFLINE", "OFFLINE", "HF_HUB_OFFLINE"):
        v = (os.environ.get(key) or "").strip().split()
        if v and v[0].strip("\"'").lower() in TRUE:
            return True
    return False


def endpoint() -> str:
    return (os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")


def token() -> str | None:
    t = (os.environ.get("HF_TOKEN") or "").strip()
    if t:
        return t
    f = (os.environ.get("HF_TOKEN_FILE") or "").strip()
    if not f:
        return None
    p = Path(os.path.expanduser(f))
    try:
        st = p.stat()
    except OSError:
        raise HubError(f"HF_TOKEN_FILE {p} does not exist") from None
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise HubError(f"HF_TOKEN_FILE {p} is mode {stat.S_IMODE(st.st_mode):o} — chmod 600 it")
    t = p.read_text().strip()
    return t or None


class _SameHostAuth(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never carry the token to a different host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            for h in ("Authorization", "authorization"):
                new.headers.pop(h, None)
                new.unredirected_hdrs.pop(h, None)
        return new


_OPENER = urllib.request.build_opener(_SameHostAuth)


def _request(url: str, headers: dict | None = None, timeout: float = 60):
    if offline():
        raise Offline("OFFLINE is set — the Hugging Face API is not reachable by policy; use a local directory")
    h = {"User-Agent": "openbeast-model-tools/1"}
    tok = token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        return _OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(300).decode(errors="replace")
        except Exception:  # noqa: BLE001
            pass
        hint = ""
        if e.code in (401, 403):
            hint = " (gated or private: accept the license on the Hub and set HF_TOKEN or HF_TOKEN_FILE)"
        elif e.code == 404:
            hint = " (no such repo, revision or file)"
        raise HubError(f"HTTP {e.code} for {url}{hint} {body}".rstrip()) from None
    except urllib.error.URLError as e:
        raise HubError(f"cannot reach {url}: {e.reason}") from None


def _q(repo: str) -> str:
    return "/".join(urllib.parse.quote(part, safe="") for part in repo.split("/"))


def get_json(url: str):
    with _request(url) as r:
        return json.load(r)


def revision_info(repo: str, rev: str) -> dict:
    return get_json(f"{endpoint()}/api/models/{_q(repo)}/revision/{urllib.parse.quote(rev, safe='')}")


def resolve_sha(repo: str, rev: str = "main") -> str:
    sha = revision_info(repo, rev).get("sha") or ""
    if not re.match(r"^[0-9a-f]{40}$", sha):
        raise HubError(f"the Hub did not return a commit SHA for {repo}@{rev}")
    return sha


_LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


def tree(repo: str, rev: str) -> list[dict]:
    """Every file at rev: [{path, size, oid, lfs:{oid(sha256), size}}], following pagination."""
    url = f"{endpoint()}/api/models/{_q(repo)}/tree/{urllib.parse.quote(rev, safe='')}?recursive=true"
    out: list[dict] = []
    seen = 0
    while url:
        with _request(url) as r:
            page = json.load(r)
            link = r.headers.get("Link") or ""
        out.extend(e for e in page if e.get("type") == "file")
        m = _LINK_NEXT.search(link)
        nxt = m.group(1) if m else ""
        if nxt and nxt.startswith("/"):
            nxt = endpoint() + nxt
        url = nxt
        seen += 1
        if seen > 1000:
            raise HubError("tree pagination did not end after 1000 pages")
    return out


def file_url(repo: str, rev: str, path: str) -> str:
    return f"{endpoint()}/{_q(repo)}/resolve/{urllib.parse.quote(rev, safe='')}/{urllib.parse.quote(path)}"


def open_file(repo: str, rev: str, path: str, byte_range: tuple[int, int | None] | None = None, timeout: float = 120):
    headers = {}
    if byte_range is not None:
        lo, hi = byte_range
        headers["Range"] = f"bytes={lo}-{'' if hi is None else hi}"
    return _request(file_url(repo, rev, path), headers, timeout)


def read_text(repo: str, rev: str, path: str, limit: int = 16 * 1024 * 1024) -> str:
    with open_file(repo, rev, path) as r:
        return r.read(limit).decode("utf-8", errors="replace")


def read_range(repo: str, rev: str, path: str, lo: int, hi: int) -> bytes:
    with open_file(repo, rev, path, (lo, hi)) as r:
        data = r.read(hi - lo + 1)
        if getattr(r, "status", 200) == 200 and lo > 0:
            raise HubError("the server ignored the Range header")
        return data
