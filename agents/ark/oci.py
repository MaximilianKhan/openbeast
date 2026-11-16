"""beast-ark OCI: pull by digest, write/read OCI archives, push, serve read-only.

No skopeo, oras or docker is needed to FETCH: the registry API is plain
HTTPS, and every byte it returns is content-addressed, so this module checks
each manifest and blob against its digest as it lands. A pinned image is
stored as an **OCI image layout in an uncompressed tar** (`*.oci.tar`), the
format `skopeo copy oci-archive:` and `docker load` (25+) read:

    oci-layout                      {"imageLayoutVersion": "1.0.0"}
    index.json                      -> the pinned top-level digest, ref.name = tag
    blobs/sha256/<hex>              the index, the kept platform manifests,
                                    their configs and layers
    manifest.json                   docker-archive compatibility (one entry
                                    per kept platform), for older `docker load`

Only the platforms the catalog asks for are kept (linux/amd64, linux/arm64).
The ORIGINAL index blob is kept too, so `image@sha256:<pinned>` still means
what it meant upstream; its other platforms' manifests are simply absent,
which a platform-selecting pull never asks for.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tarfile
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from . import net

INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")
MANIFEST_TYPES = ("application/vnd.oci.image.manifest.v1+json",
                  "application/vnd.docker.distribution.manifest.v2+json")
ACCEPT = ", ".join(INDEX_TYPES + MANIFEST_TYPES)
_DIG = re.compile(r"^sha256:[0-9a-f]{64}$")


class OciError(RuntimeError):
    pass


def split_image(image: str) -> Tuple[str, str]:
    """'docker.io/library/x' -> ('registry-1.docker.io', 'library/x')."""
    host, _, repo = image.partition("/")
    if host in ("docker.io", "index.docker.io"):
        host = "registry-1.docker.io"
        if "/" not in repo:
            repo = "library/" + repo
    return host, repo


def repo_path(image: str) -> str:
    """The name a mirror is asked for: the repo path without the registry."""
    host, repo = split_image(image)
    return repo


def _scheme(host: str) -> str:
    h = host.split(":")[0]
    return "http" if h in ("127.0.0.1", "localhost", "::1") else "https"


class Registry:
    """A pull client for one repository. Anonymous bearer tokens only; the
    token is never printed and never follows a redirect (net._req)."""

    def __init__(self, image: str):
        self.host, self.repo = split_image(image)
        self.base = f"{_scheme(self.host)}://{self.host}/v2/{self.repo}"
        self.tokens: Dict[str, str] = {}

    def _token_for(self, challenge: str) -> Optional[str]:
        m = re.match(r"^\s*Bearer\s+(.*)$", challenge or "", re.I)
        if not m:
            return None
        params = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        realm = params.get("realm")
        if not realm or not realm.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            return None
        q = []
        if params.get("service"):
            q.append("service=" + quote(params["service"], safe=""))
        q.append("scope=" + quote(params.get("scope") or f"repository:{self.repo}:pull", safe=":/"))
        data, _ = net.get_bytes(realm + ("&" if "?" in realm else "?") + "&".join(q), limit=1 << 20)
        j = json.loads(data)
        tok = j.get("token") or j.get("access_token")
        return f"Bearer {tok}" if tok else None

    def _open(self, url: str, method: str = "GET", accept: str = ""):
        headers = {"Accept": accept} if accept else {}
        target, tok = url, None
        for _ in range(3):
            try:
                return net.open_url(target, method, headers=headers, token=tok)
            except net.HTTPFailure as e:
                if e.status != 401:
                    raise
                host = re.sub(r"^\w+://([^/]+)/.*$", r"\1", e.final_url)
                tok = self.tokens.get(host) or self._token_for(e.headers.get("www-authenticate", ""))
                if not tok:
                    raise
                self.tokens[host] = tok
                target = e.final_url
        raise OciError(f"registry auth loop for {url}")

    def resolve(self, ref: str) -> Tuple[str, str]:
        """tag or digest -> (digest, media type), from a HEAD (Docker Hub does
        not count a HEAD against its pull limit)."""
        try:
            with self._open(f"{self.base}/manifests/{ref}", "HEAD", ACCEPT) as r:
                dig = r.headers.get("Docker-Content-Digest", "")
                mt = (r.headers.get("Content-Type") or "").split(";")[0]
            if _DIG.match(dig):
                return dig, mt
        except net.HTTPFailure as e:
            if e.status not in (405, 403):
                raise
        body, mt = self.manifest(ref)
        return "sha256:" + hashlib.sha256(body).hexdigest(), mt

    def manifest(self, ref: str, want_digest: str = "") -> Tuple[bytes, str]:
        with self._open(f"{self.base}/manifests/{ref}", "GET", ACCEPT) as r:
            body = r.read(16 << 20)
            mt = (r.headers.get("Content-Type") or "").split(";")[0]
        got = "sha256:" + hashlib.sha256(body).hexdigest()
        want = want_digest or (ref if ref.startswith("sha256:") else "")
        if want and got != want:
            raise OciError(f"{self.repo}@{want}: the registry served a manifest whose digest is {got} — refused")
        if not mt:
            mt = json.loads(body).get("mediaType", "")
        return body, mt

    def blob(self, digest: str, dest: str, size: int = 0) -> None:
        if os.path.isfile(dest) and _file_digest(dest) == digest:
            return
        part = net.download(f"{self.base}/blobs/{digest}", dest, size=size, quiet=True,
                            token=self._blob_token())
        got = _file_digest(part)
        if got != digest:
            os.remove(part)
            raise OciError(f"{self.repo} blob {digest}: downloaded bytes hash to {got} — refused")
        os.replace(part, dest)

    def _blob_token(self) -> Optional[str]:
        tok = self.tokens.get(self.host)
        if tok:
            return tok
        try:                                    # prime a token with a cheap call
            with self._open(f"{self.base}/tags/list?n=1"):
                pass
        except net.HTTPFailure:
            pass
        return self.tokens.get(self.host)


def _file_digest(path: str) -> str:
    return "sha256:" + net.sha256_file(path)


def platform_of(desc: dict) -> str:
    p = desc.get("platform") or {}
    return f"{p.get('os', '?')}/{p.get('architecture', '?')}"


def select_children(index: dict, platforms: List[str]) -> Dict[str, dict]:
    """platform -> child descriptor. arm64 prefers variant v8 (or none)."""
    out: Dict[str, dict] = {}
    for d in index.get("manifests", []):
        if (d.get("annotations") or {}).get("vnd.docker.reference.type"):
            continue                              # attestation manifests
        pl = platform_of(d)
        if pl not in platforms:
            continue
        var = (d.get("platform") or {}).get("variant", "")
        if pl in out and var not in ("", "v8"):
            continue
        out[pl] = d
    return out


def describe(image: str, ref: str, platforms: List[str]) -> dict:
    """For `ark lock`: the pinned digest and per-platform byte counts, from
    manifests only (no layer is downloaded)."""
    reg = Registry(image)
    digest, mt = reg.resolve(ref)
    body, mt = reg.manifest(digest)
    doc = json.loads(body)
    mt = mt or doc.get("mediaType", "")
    sizes: Dict[str, int] = {}
    if mt in INDEX_TYPES or "manifests" in doc:
        kids = select_children(doc, platforms)
        for pl, d in kids.items():
            mb, _ = reg.manifest(d["digest"])
            m = json.loads(mb)
            sizes[pl] = len(mb) + m["config"]["size"] + sum(l["size"] for l in m.get("layers", []))
        missing = [p for p in platforms if p not in kids]
    else:
        # A single-platform manifest: which platform it is lives in the config.
        n = len(body) + doc["config"]["size"] + sum(l["size"] for l in doc.get("layers", []))
        with reg._open(f"{reg.base}/blobs/{doc['config']['digest']}") as r:
            cfg = json.loads(r.read(4 << 20))
        if not isinstance(cfg, dict) or "architecture" not in cfg:   # an artifact (a DB), not an image
            sizes, missing = {p: n for p in platforms}, []
        else:
            pl = f"{cfg.get('os', 'linux')}/{cfg['architecture']}"
            sizes = {pl: n} if pl in platforms else {}
            missing = [p for p in platforms if p != pl]
    return {"digest": digest, "media_type": mt, "sizes": sizes, "missing": missing}


# ── pull into an OCI archive ─────────────────────────────────────────────

def pull(image: str, tag: str, digest: str, platforms: List[str], dest_tar: str, stage: str,
         log=lambda s: None) -> dict:
    """Pull `image@digest` (the pin; `tag` is only the ref.name) for the given
    platforms into `dest_tar`. Blobs are staged in `stage/blobs` and reused on
    resume. Returns {"digest", "platforms": {pl: manifest digest}, "bytes"}."""
    if not _DIG.match(digest):
        raise OciError(f"{image}: refusing to pull without a pinned digest (got {digest!r}); run `ark lock`")
    reg = Registry(image)
    blobs = os.path.join(stage, "blobs", "sha256")
    os.makedirs(blobs, exist_ok=True)

    def keep(dig: str, data: bytes) -> None:
        with open(os.path.join(blobs, dig.split(":")[1]), "wb") as f:
            f.write(data)

    top, mt = reg.manifest(digest, want_digest=digest)
    keep(digest, top)
    doc = json.loads(top)
    mt = mt or doc.get("mediaType", "")
    kept: Dict[str, Tuple[str, dict, int]] = {}
    if mt in INDEX_TYPES or "manifests" in doc:
        kids = select_children(doc, platforms)
        missing = [p for p in platforms if p not in kids]
        if missing:
            raise OciError(f"{image}@{digest} has no {', '.join(missing)} manifest — fix `platforms`")
        for pl, d in kids.items():
            mb, _ = reg.manifest(d["digest"], want_digest=d["digest"])
            keep(d["digest"], mb)
            kept[pl] = (d["digest"], json.loads(mb), len(mb))
    else:
        kept[platforms[0] if platforms else "linux/amd64"] = (digest, doc, len(top))
    for pl, (mdig, m, _n) in kept.items():
        for d in [m["config"]] + list(m.get("layers", [])):
            if d.get("mediaType", "").endswith("foreign.diff.tar.gzip") or d.get("urls"):
                raise OciError(f"{image}: {pl} has a foreign (non-distributable) layer; not archivable")
            log(f"    {pl} {d['digest'][:19]} {d['size'] >> 20} MiB")
            reg.blob(d["digest"], os.path.join(blobs, d["digest"].split(":")[1]), d["size"])
    write_layout_tar(stage, dest_tar, image, tag, digest, mt, len(top), kept)
    return {"digest": digest, "platforms": {pl: v[0] for pl, v in kept.items()},
            "bytes": os.path.getsize(dest_tar)}


def write_layout_tar(stage: str, dest_tar: str, image: str, tag: str, digest: str, mt: str,
                     top_size: int, kept: Dict[str, Tuple[str, dict, int]]) -> None:
    ref = f"{image}:{tag}" if tag else image
    index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
             "manifests": [{"mediaType": mt or INDEX_TYPES[0], "digest": digest, "size": top_size,
                            "annotations": {"org.opencontainers.image.ref.name": tag or "latest",
                                            "io.containerd.image.name": ref}}]}
    docker_manifest = [{"Config": f"blobs/sha256/{m['config']['digest'].split(':')[1]}",
                        "RepoTags": [ref] if tag else [],
                        "Layers": [f"blobs/sha256/{l['digest'].split(':')[1]}" for l in m.get("layers", [])]}
                       for _pl, (_d, m, _n) in sorted(kept.items())]
    need = {digest}
    for _pl, (mdig, m, _n) in kept.items():
        need |= {mdig, m["config"]["digest"]} | {l["digest"] for l in m.get("layers", [])}
    tmp = dest_tar + ".tmp"
    os.makedirs(os.path.dirname(dest_tar) or ".", exist_ok=True)
    with tarfile.open(tmp, "w", format=tarfile.PAX_FORMAT) as t:
        def add_bytes(name: str, data: bytes) -> None:
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime, ti.mode = len(data), 0, 0o644
            t.addfile(ti, io.BytesIO(data))
        add_bytes("oci-layout", b'{"imageLayoutVersion":"1.0.0"}')
        add_bytes("index.json", json.dumps(index, sort_keys=True).encode())
        add_bytes("manifest.json", json.dumps(docker_manifest).encode())
        for dig in sorted(need):
            p = os.path.join(stage, "blobs", "sha256", dig.split(":")[1])
            ti = t.gettarinfo(p, f"blobs/sha256/{dig.split(':')[1]}")
            ti.mtime, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname = 0, 0o644, 0, 0, "", ""
            with open(p, "rb") as f:
                t.addfile(ti, f)
    os.replace(tmp, dest_tar)


class OciArchive:
    """Random access to an `*.oci.tar` without unpacking it (serve, restore)."""

    def __init__(self, path: str):
        self.path = path
        self.tar = tarfile.open(path, "r:")
        self.members = {m.name: m for m in self.tar.getmembers() if m.isfile()}
        self.index = json.loads(self.read("index.json"))
        top = self.index["manifests"][0]
        self.digest = top["digest"]
        self.media_type = top.get("mediaType", INDEX_TYPES[0])
        ann = top.get("annotations") or {}
        self.tag = ann.get("org.opencontainers.image.ref.name", "latest")
        name = ann.get("io.containerd.image.name", "")
        self.image = name.rsplit(":", 1)[0] if ":" in name.rsplit("/", 1)[-1] else name

    def has(self, digest: str) -> bool:
        return f"blobs/sha256/{digest.split(':')[-1]}" in self.members

    def member(self, digest: str) -> tarfile.TarInfo:
        return self.members[f"blobs/sha256/{digest.split(':')[-1]}"]

    def read(self, name: str) -> bytes:
        f = self.tar.extractfile(self.members[name])
        return f.read() if f else b""

    def blob(self, digest: str) -> bytes:
        return self.read(f"blobs/sha256/{digest.split(':')[-1]}")

    def open_blob(self, digest: str):
        return self.tar.extractfile(self.member(digest))

    def manifests(self) -> Dict[str, Tuple[str, dict, str]]:
        """platform -> (digest, manifest, media type) for what is present."""
        top = json.loads(self.blob(self.digest))
        if "manifests" in top:
            out = {}
            for d in top["manifests"]:
                if self.has(d["digest"]) and platform_of(d) != "?/?":
                    if (d.get("annotations") or {}).get("vnd.docker.reference.type"):
                        continue
                    out[platform_of(d)] = (d["digest"], json.loads(self.blob(d["digest"])), d.get("mediaType", ""))
            return out
        cfg = json.loads(self.blob(top["config"]["digest"]))
        if not isinstance(cfg, dict) or "architecture" not in cfg:
            return {"any": (self.digest, top, self.media_type)}       # a non-image artifact
        return {f"{cfg.get('os', 'linux')}/{cfg['architecture']}": (self.digest, top, self.media_type)}

    def verify(self) -> List[str]:
        """Every blob present hashes to its name."""
        bad = []
        for name, m in self.members.items():
            if name.startswith("blobs/sha256/"):
                h = hashlib.sha256()
                f = self.tar.extractfile(m)
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
                if h.hexdigest() != name.rsplit("/", 1)[1]:
                    bad.append(name)
        return bad

    def close(self) -> None:
        self.tar.close()


def single_platform_tar(arc: OciArchive, platform: str, dest: str, ref: str) -> str:
    """A docker-loadable tar for ONE platform (index.json -> that manifest),
    so `docker load` never trips over the platforms that were not kept."""
    mans = arc.manifests()
    if platform not in mans and "any" in mans:
        platform = "any"
    if platform not in mans:
        raise OciError(f"{arc.path} holds {sorted(mans)}, not {platform}")
    mdig, m, mt = mans[platform]
    mb = arc.blob(mdig)
    index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
             "manifests": [{"mediaType": mt or m.get("mediaType") or MANIFEST_TYPES[0], "digest": mdig,
                            "size": len(mb), "annotations": {"org.opencontainers.image.ref.name": ref.rsplit(':', 1)[-1],
                                                             "io.containerd.image.name": ref}}]}
    dm = [{"Config": f"blobs/sha256/{m['config']['digest'].split(':')[1]}", "RepoTags": [ref],
           "Layers": [f"blobs/sha256/{l['digest'].split(':')[1]}" for l in m.get("layers", [])]}]
    with tarfile.open(dest, "w") as t:
        for name, data in (("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
                           ("index.json", json.dumps(index).encode()), ("manifest.json", json.dumps(dm).encode())):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
        for dig in [mdig, m["config"]["digest"]] + [l["digest"] for l in m.get("layers", [])]:
            mem = arc.member(dig)
            ti = tarfile.TarInfo(mem.name)
            ti.size = mem.size
            t.addfile(ti, arc.open_blob(dig))
    return mdig


# ── push (restore into a registry) ───────────────────────────────────────

def push(arc: OciArchive, registry: str, repo: str, tag: str, log=print) -> dict:
    """Push every kept platform, then the original index under `tag`. A
    registry that refuses an index with absent children (distribution v3
    validates them) gets a reduced index of the kept platforms instead — the
    per-platform manifest digests, which is what a pull resolves to, are
    unchanged either way. Plain http only to loopback / an explicit host:port
    the operator named."""
    scheme = "http" if not registry.startswith("https://") else "https"
    host = registry.split("://", 1)[-1].rstrip("/")
    base = f"{scheme}://{host}/v2/{repo}"

    def exists(dig: str) -> bool:
        try:
            with net.open_url(f"{base}/blobs/{dig}", "HEAD"):
                return True
        except net.HTTPFailure:
            return False

    def put_blob(dig: str) -> None:
        if exists(dig):
            return
        with net.open_url(f"{base}/blobs/uploads/", "POST", headers={"Content-Length": "0"}) as r:
            loc = r.headers.get("Location", "")
        if loc.startswith("/"):
            loc = f"{scheme}://{host}{loc}"
        sep = "&" if "?" in loc else "?"
        data = arc.blob(dig)
        req = net._req(f"{loc}{sep}digest={dig}", "PUT", {"Content-Type": "application/octet-stream"})
        req.data = data
        try:
            net.urllib.request.urlopen(req, timeout=net.TIMEOUT).close()
        except net.urllib.error.HTTPError as e:
            raise OciError(f"push {repo} blob {dig[:19]}: HTTP {e.code}") from None

    def put_manifest(ref: str, body: bytes, mt: str) -> int:
        req = net._req(f"{base}/manifests/{ref}", "PUT", {"Content-Type": mt})
        req.data = body
        try:
            net.urllib.request.urlopen(req, timeout=net.TIMEOUT).close()
            return 0
        except net.urllib.error.HTTPError as e:
            return e.code

    mans = arc.manifests()
    for pl, (mdig, m, mt) in sorted(mans.items()):
        for d in [m["config"]] + list(m.get("layers", [])):
            put_blob(d["digest"])
        code = put_manifest(mdig, arc.blob(mdig), mt or m.get("mediaType") or MANIFEST_TYPES[0])
        if code:
            raise OciError(f"push {repo}@{mdig[:19]} ({pl}): HTTP {code}")
        log(f"    pushed {pl} {mdig[:19]}")
    top = arc.blob(arc.digest)
    if "manifests" not in json.loads(top):
        code = put_manifest(tag, top, arc.media_type)
        return {"tag": tag, "index": arc.digest, "reduced": False, "code": code}
    code = put_manifest(tag, top, arc.media_type)
    if not code:
        return {"tag": tag, "index": arc.digest, "reduced": False, "code": 0}
    doc = json.loads(top)
    doc["manifests"] = [d for d in doc["manifests"] if arc.has(d["digest"])
                        and not (d.get("annotations") or {}).get("vnd.docker.reference.type")]
    red = json.dumps(doc, separators=(",", ":")).encode()
    code2 = put_manifest(tag, red, arc.media_type)
    if code2:
        raise OciError(f"push {repo}:{tag}: the registry refused the index (HTTP {code}) and the reduced one (HTTP {code2})")
    return {"tag": tag, "index": "sha256:" + hashlib.sha256(red).hexdigest(), "reduced": True, "code": 0}
