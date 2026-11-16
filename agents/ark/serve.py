"""beast-ark serve: one read-only HTTP server over a drive. docs/BEAST_ARK.md §8.

    /                       an index of what is on the drive, and the client lines
    /files/… /git/… /oci/… /pkgs/…   the stored files (Range supported), ONLY
                            paths ark.lock lists — the stage, and anything
                            planted on the drive after the fact, is never served
    /ark.lock /SHA256SUMS /ARK.md /catalog.toml
    /v2/…                   an OCI distribution registry, PULL ONLY, answered
                            straight out of the *.oci.tar archives (no copy,
                            no daemon): docker, podman, containerd, k3s and
                            kind pull from it by tag or by the pinned digest
    /pypi/simple/…          a PEP 503 index over the stored wheels, hash-tagged
    /npm/…                  an npm registry (packuments built from the stored
                            package-lock.json) for the stored npm sets
    /goproxy/…              a GOPROXY over the stored go module trees

Bound to 127.0.0.1 unless told otherwise; `--tailnet` binds this box's
Tailscale address so the other boxes reach it and the LAN does not. No
write method exists. Every response is software that is already public, so
there is no auth; the bind address is the boundary.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from . import oci, store as S

TOP = ("ark.lock", "SHA256SUMS", "ARK.md", "catalog.toml")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class Index:
    """Everything the server can answer, built once from ark.lock."""

    def __init__(self, drive: S.Drive):
        self.drive = drive
        self.lock = drive.read_lock()
        self.paths = set(TOP)
        self.repos: Dict[str, List[oci.OciArchive]] = {}
        self.wheels: Dict[str, List[Tuple[str, str]]] = {}
        self.npm: Dict[str, Dict[str, dict]] = {}
        self.goproxy: List[str] = []
        self._lk = threading.Lock()
        for it in self.lock["items"].values():
            for f in it.get("files", []):
                self.paths.add(f["path"])
            k = it.get("kind")
            if k == "oci":
                arc = oci.OciArchive(drive.p(it["primary"]))
                self.repos.setdefault(oci.repo_path(it["image"]), []).append(arc)
            elif k == "pip":
                for f in it["files"]:
                    fn = os.path.basename(f["path"])
                    if fn.endswith((".whl", ".tar.gz", ".zip")):
                        proj = fn.split("-", 1)[0] if fn.endswith(".whl") else fn.rsplit("-", 1)[0]
                        self.wheels.setdefault(_norm(proj), []).append((f["path"], f["sha256"]))
            elif k == "npm":
                self._add_npm(it)
            elif k == "gomod":
                self.goproxy.append(it["primary"])

    def _add_npm(self, it: dict) -> None:
        d = it["primary"]
        try:
            with open(self.drive.p(f"{d}/package-lock.json"), encoding="utf-8") as f:
                pl = json.load(f)
        except (OSError, ValueError):
            return
        stored = {os.path.basename(f["path"]): f["path"] for f in it["files"] if "/tarballs/" in f["path"]}
        for path, meta in (pl.get("packages") or {}).items():
            if not path or not meta.get("resolved"):
                continue
            name = meta.get("name") or path.split("node_modules/")[-1]
            fn = os.path.basename(meta["resolved"].split("?")[0])
            scope = name.split("/")[0] if name.startswith("@") else ""
            rel = stored.get((scope + "__" if scope else "") + fn)
            if not rel:
                continue
            doc = {k: meta[k] for k in ("dependencies", "optionalDependencies", "peerDependencies",
                                        "bin", "engines", "os", "cpu") if k in meta}
            doc.update({"name": name, "version": meta["version"],
                        "dist": {"tarball": rel, "integrity": meta.get("integrity", "")}})
            self.npm.setdefault(name, {})[meta["version"]] = doc

    def find_manifest(self, repo: str, ref: str) -> Optional[Tuple[bytes, str, str]]:
        for arc in self.repos.get(repo, []):
            with self._lk:
                if ref == arc.tag or ref == arc.digest:
                    return arc.blob(arc.digest), arc.media_type, arc.digest
                if ref.startswith("sha256:") and arc.has(ref):
                    top = json.loads(arc.blob(arc.digest))
                    for d in top.get("manifests", []):
                        if d["digest"] == ref:
                            return arc.blob(ref), d.get("mediaType", oci.MANIFEST_TYPES[0]), ref
        return None

    def find_blob(self, repo: str, digest: str) -> Optional[oci.OciArchive]:
        for arc in self.repos.get(repo, []):
            if arc.has(digest):
                return arc
        return None


def make_handler(ix: Index):
    class H(BaseHTTPRequestHandler):
        server_version = "beast-ark"
        sys_version = ""

        def log_message(self, fmt, *args):     # one quiet line, no headers (no secrets to leak, but still)
            sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

        def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200, extra=None):
            self._send(code, json.dumps(obj).encode(), "application/json", extra)

        def do_POST(self):  # noqa: N802
            self._send(405, b"beast-ark serve is read-only\n")

        do_PUT = do_PATCH = do_DELETE = do_POST

        def do_HEAD(self):  # noqa: N802
            self.do_GET()

        def do_GET(self):  # noqa: N802
            path = unquote(urlsplit(self.path).path)
            try:
                if path in ("/", "/index.html"):
                    return self._index()
                if path == "/healthz":
                    return self._send(200, b"ok\n")
                if path == "/v2" or path.startswith("/v2/"):
                    return self._v2(path)
                if path.startswith("/pypi/simple"):
                    return self._pypi(path)
                if path.startswith("/npm/"):
                    return self._npm(path[5:])
                if path.startswith("/goproxy/"):
                    return self._goproxy(path[len("/goproxy/"):])
                return self._file(path.lstrip("/"))
            except BrokenPipeError:
                return None

        # -- static ----------------------------------------------------------
        def _file(self, rel: str, ctype: str = "application/octet-stream"):
            if rel not in ix.paths:
                return self._send(404, b"not on this drive\n")
            p = ix.drive.p(rel)
            if not os.path.isfile(p):
                return self._send(404, b"listed in ark.lock but missing from the drive\n")
            size = os.path.getsize(p)
            start, end = 0, size - 1
            rng = self.headers.get("Range", "")
            m = re.fullmatch(r"bytes=(\d+)-(\d*)", rng.strip())
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
                if start >= size:
                    return self._send(416, b"", extra={"Content-Range": f"bytes */{size}"})
                end = min(end, size - 1)
            if rel.endswith((".md", ".lock", ".toml")) or rel == "SHA256SUMS":
                ctype = "text/plain; charset=utf-8"
            self.send_response(206 if m else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if m:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return None
            with open(p, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    b = f.read(min(1 << 20, left))
                    if not b:
                        break
                    self.wfile.write(b)
                    left -= len(b)
            return None

        def _base(self) -> str:
            return f"http://{self.headers.get('Host') or 'localhost'}"

        def _index(self):
            items = ix.lock["items"]
            ents = sorted({v["entry"] for v in items.values()})
            host = self.headers.get("Host") or "HOST:PORT"
            rows = "".join(f"<li>{e}</li>" for e in ents)
            body = f"""<!doctype html><meta charset=utf-8><title>beast-ark</title>
<h1>beast-ark</h1><p>{len(items)} items, {len(ents)} entries. Read-only. <a href=/ARK.md>ARK.md</a> ·
<a href=/ark.lock>ark.lock</a> · <a href=/SHA256SUMS>SHA256SUMS</a> · <a href=/pypi/simple/>pypi</a></p>
<pre>
images   docker pull {host}/library/postgres:TAG      (add "{host}" to insecure-registries,
         or a k3s/containerd mirror: see docs/BEAST_ARK.md §8)
pip      pip install --index-url http://{host}/pypi/simple/ PACKAGE
npm      npm install --registry http://{host}/npm/ PACKAGE
go       GOPROXY=http://{host}/goproxy GOSUMDB=off go build ./...
files    curl -O http://{host}/files/ENTRY/VERSION/PLATFORM/FILE
</pre><ul>{rows}</ul>"""
            return self._send(200, body.encode(), "text/html; charset=utf-8")

        # -- registry --------------------------------------------------------
        def _v2(self, path: str):
            hdr = {"Docker-Distribution-API-Version": "registry/2.0"}
            if path in ("/v2", "/v2/"):
                return self._json({}, extra=hdr)
            if path == "/v2/_catalog":
                return self._json({"repositories": sorted(ix.repos)}, extra=hdr)
            m = re.fullmatch(r"/v2/(.+)/(manifests|blobs|tags)/(.+)", path)
            if not m:
                return self._json({"errors": [{"code": "NAME_UNKNOWN"}]}, 404, hdr)
            repo, what, ref = m.groups()
            if what == "tags":
                arcs = ix.repos.get(repo)
                if not arcs:
                    return self._json({"errors": [{"code": "NAME_UNKNOWN"}]}, 404, hdr)
                return self._json({"name": repo, "tags": sorted({a.tag for a in arcs})}, extra=hdr)
            if what == "manifests":
                hit = ix.find_manifest(repo, ref)
                if not hit:
                    return self._json({"errors": [{"code": "MANIFEST_UNKNOWN",
                                                   "message": "not archived on this drive"}]}, 404, hdr)
                body, mt, dig = hit
                return self._send(200, body, mt, dict(hdr, **{"Docker-Content-Digest": dig}))
            arc = ix.find_blob(repo, ref)
            if not arc:
                return self._json({"errors": [{"code": "BLOB_UNKNOWN"}]}, 404, hdr)
            mem = arc.member(ref)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(mem.size))
            self.send_header("Docker-Content-Digest", ref)
            for k, v in hdr.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command == "HEAD":
                return None
            # The archive is an uncompressed tar: a blob is one contiguous
            # byte range, read through a handle of this request's own.
            with open(arc.path, "rb") as f:
                f.seek(mem.offset_data)
                left = mem.size
                while left > 0:
                    b = f.read(min(1 << 20, left))
                    if not b:
                        break
                    self.wfile.write(b)
                    left -= len(b)
            return None

        # -- pip -------------------------------------------------------------
        def _pypi(self, path: str):
            rest = path[len("/pypi/simple"):].strip("/")
            if not rest:
                links = "".join(f'<a href="/pypi/simple/{p}/">{p}</a><br>\n' for p in sorted(ix.wheels))
                return self._send(200, f"<!doctype html><html><body>\n{links}</body></html>".encode(), "text/html")
            files = ix.wheels.get(_norm(rest))
            if not files:
                return self._send(404, b"no such project on this drive\n")
            links = "".join(f'<a href="/{p}#sha256={h}">{os.path.basename(p)}</a><br>\n'
                            for p, h in sorted(set(files)))
            return self._send(200, f"<!doctype html><html><body>\n{links}</body></html>".encode(), "text/html")

        # -- npm -------------------------------------------------------------
        def _npm(self, name: str):
            name = name.strip("/")
            if name in ix.paths:                       # a tarball, by its drive path
                return self._file(name)
            if name.startswith("pkgs/"):
                return self._file(name)
            vers = ix.npm.get(name)
            if not vers:
                return self._json({"error": "not on this drive"}, 404)
            base = self._base()
            out = {}
            for v, doc in vers.items():
                d = json.loads(json.dumps(doc))
                d["dist"]["tarball"] = f"{base}/npm/{doc['dist']['tarball']}"
                out[v] = d
            latest = sorted(out, key=_semver_key)[-1]
            return self._json({"name": name, "versions": out, "dist-tags": {"latest": latest}})

        # -- go --------------------------------------------------------------
        def _goproxy(self, rel: str):
            for base in ix.goproxy:
                cand = f"{base}/{rel}"
                if cand in ix.paths:
                    return self._file(cand, "text/plain; charset=utf-8" if rel.endswith(
                        ("/list", ".info", ".mod")) else "application/zip")
            return self._send(404, b"not found\n")

    return H


def _semver_key(v: str):
    parts = re.split(r"[.+-]", v)
    return [(0, int(p)) if p.isdigit() else (1, p) for p in parts]


def tailnet_ip() -> str:
    ts = shutil.which("tailscale")
    if not ts:
        raise RuntimeError("--tailnet needs the tailscale CLI on this box")
    r = subprocess.run([ts, "ip", "-4"], capture_output=True, text=True, timeout=10)
    ip = (r.stdout.split() or [""])[0]
    if r.returncode != 0 or not re.fullmatch(r"100\.\d+\.\d+\.\d+", ip):
        raise RuntimeError("tailscale has no IPv4 address here (is it up?)")
    return ip


def serve(drive: S.Drive, bind: str, port: int, ready=None) -> ThreadingHTTPServer:
    ix = Index(drive)
    httpd = ThreadingHTTPServer((bind, port), make_handler(ix))
    httpd.daemon_threads = True
    if ready:
        ready(httpd, ix)
    return httpd
