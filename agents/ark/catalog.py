"""beast-ark catalog: load, expand, lint. docs/BEAST_ARK.md §3.

The catalog is two flat lists in one TOML file, `agents/ark/catalog.toml`:

  [[entry]]     one piece of software: name, category, licence, version, the
                upstream git repo and the tag + commit it is pinned to.
  [[artifact]]  one thing to download for an entry: a release file for one
                platform, an OCI image (multi-arch) by digest, or a package
                set (pip wheels / npm tarballs / go modules / cargo vendor).

Flat on purpose: a pin is one line a reviewer reads in a diff, and `ark lock`
and `ark update` rewrite exactly that line (tomlsub.set_key).

A string field may name `{version}`, `{tag}` or any `vars = ["k=v"]` of its
entry; nothing else is templated.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from . import tomlsub

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG = os.path.join(HERE, "catalog.toml")
REPO_DIR = os.path.dirname(os.path.dirname(HERE))

PLATFORMS = ("linux-x86_64", "linux-aarch64", "darwin-arm64")
FILE_PLATFORMS = PLATFORMS + ("any",)
# OCI platform -> the ark platform whose boxes run it. A Mac runs linux/arm64
# images in its Linux VM (Docker Desktop, colima, podman machine), so the
# darwin-arm64 plan counts the linux/arm64 layers too.
OCI_PLATFORM = {"linux/amd64": "linux-x86_64", "linux/arm64": "linux-aarch64"}
OCI_FOR = {"linux-x86_64": "linux/amd64", "linux-aarch64": "linux/arm64", "darwin-arm64": "linux/arm64"}

CATEGORIES = (
    "containers", "orchestration", "languages", "build", "databases", "messaging",
    "observability", "networking", "dev", "storage", "security", "ai", "docs",
    "iac", "os", "virtualization", "cli",
)
KINDS = ("file", "oci", "pip", "npm", "gomod", "cargo")
INSTALL = ("bin", "tar", "zip", "gz", "bz2", "xz", "zst", "manual", "data", "")
SIG_KINDS = ("minisign", "gpg", "cosign", "ed25519-sums", "")
VERIFIED = ("upstream-sums", "upstream-json", "signature", "registry", "pinned",
            "downloaded", "tofu", "unverified", "")
HISTORY = ("shallow", "full", "none")
HASH_ALGOS = ("sha256", "sha512", "sha3-256", "sha1")

# OSI-approved SPDX ids (opensource.org/licenses), plus the exceptions used
# with them. A licence outside this list needs a `license_note` saying why it
# is fine for a personal archive; lint refuses it otherwise.
OSI = {
    "0BSD", "AFL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later", "Apache-2.0", "Artistic-2.0",
    "BlueOak-1.0.0", "BSD-2-Clause", "BSD-3-Clause", "BSL-1.0", "CDDL-1.0", "ECL-2.0",
    "EPL-1.0", "EPL-2.0", "EUPL-1.2", "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only",
    "GPL-3.0-or-later", "ISC", "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only",
    "LGPL-3.0-or-later", "MIT", "MIT-0", "MPL-2.0", "MS-PL", "NCSA", "OFL-1.1",
    "PostgreSQL", "PSF-2.0", "Python-2.0", "UPL-1.0", "Unlicense", "Zlib",
    "Unicode-DFS-2016", "Unicode-3.0", "W3C", "LPL-1.02",
}
SPDX_EXCEPTIONS = {"Classpath-exception-2.0", "LLVM-exception", "GCC-exception-3.1",
                   "Linux-syscall-note", "OpenSSL-exception"}

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TMPL = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CatalogError(ValueError):
    pass


@dataclass
class Entry:
    name: str
    category: str
    description: str
    homepage: str
    license: str
    version: str
    license_note: str = ""
    profiles: List[str] = field(default_factory=list)
    update: str = ""
    git: str = ""
    tag: str = "v{version}"
    commit: str = ""
    history: str = "shallow"
    git_size: int = 0
    git_verified: str = ""
    vars: List[str] = field(default_factory=list)
    note: str = ""
    table: Optional[tomlsub.Table] = None
    artifacts: List["Artifact"] = field(default_factory=list)

    def ctx(self) -> Dict[str, str]:
        # Built-ins for upstreams that spell the version two ways: Temurin's
        # 21.0.13+9 is 21.0.13_9 in file names; postgres 18.7 is tag REL_18_7.
        c = {"version": self.version, "name": self.name,
             "version_pu": self.version.replace("+", "_"),
             "version_du": self.version.replace(".", "_")}
        c["tag"] = _expand(self.tag, c, f"{self.name}.tag") if self.tag else ""
        for kv in self.vars:
            if "=" not in kv:
                raise CatalogError(f"{self.name}: vars entry {kv!r} is not k=v")
            k, v = kv.split("=", 1)
            c[k.strip()] = _expand(v.strip(), c, f"{self.name}.vars")
        return c

    @property
    def tag_x(self) -> str:
        return self.ctx()["tag"]

    @property
    def core(self) -> bool:
        return "core" in self.profiles


@dataclass
class Artifact:
    entry: str
    kind: str
    id: str = ""
    platform: str = "any"
    url: str = ""
    sha256: str = ""
    upstream_hash: str = ""
    size: int = 0
    checksum_url: str = ""
    checksum_format: str = "sums"
    checksum_name: str = ""
    signature_url: str = ""
    signature_kind: str = ""
    signature_key: str = ""
    signature_prefix: str = ""
    install: str = ""
    bins: List[str] = field(default_factory=list)
    strip: int = -1
    verified: str = ""
    note: str = ""
    # oci
    image: str = ""
    tag: str = ""
    digest: str = ""
    platforms: List[str] = field(default_factory=list)
    platform_sizes: List[str] = field(default_factory=list)
    # package sets
    requirements: List[str] = field(default_factory=list)
    python: str = ""
    pip_platforms: List[str] = field(default_factory=list)
    packages: List[str] = field(default_factory=list)
    moddir: str = ""
    est_size: int = 0
    table: Optional[tomlsub.Table] = None
    owner: Optional[Entry] = None

    # -- expanded views ------------------------------------------------------
    def x(self, s: str) -> str:
        return _expand(s, self.owner.ctx() if self.owner else {}, self.id) if s else s

    @property
    def url_x(self) -> str:
        return self.x(self.url)

    @property
    def tag_x(self) -> str:
        return self.x(self.tag)

    @property
    def filename(self) -> str:
        if self.kind == "file":
            return os.path.basename(urlsplit(self.url_x).path) or "download"
        return ""

    def sizes(self) -> Dict[str, int]:
        """ark platform -> bytes this artifact costs on a box of it (0 = unknown)."""
        if self.kind == "oci":
            per = {}
            for item in self.platform_sizes:
                p, _, n = item.partition("=")
                per[p.strip()] = int(n) if n.strip().isdigit() else 0
            out = {}
            for plat in PLATFORMS:
                op = OCI_FOR[plat]
                if op in self.platforms:
                    out[plat] = per.get(op, 0)
            return out
        n = self.size or self.est_size
        if self.platform == "any":
            return {p: n for p in PLATFORMS}
        return {self.platform: n}

    def is_estimate(self) -> bool:
        if self.kind == "oci":
            return not self.platform_sizes
        return not self.size

    def pinned(self) -> bool:
        if self.kind == "file":
            return bool(self.sha256 or self.upstream_hash)
        if self.kind == "oci":
            return bool(self.digest)
        return False


@dataclass
class Catalog:
    path: str
    text: str
    meta: dict
    entries: Dict[str, Entry]
    artifacts: List[Artifact]
    tables: list

    def by_id(self) -> Dict[str, Artifact]:
        return {a.id: a for a in self.artifacts}

    def select(self, only=(), category=(), profile: str = "full", platform: str = "all") -> List[Entry]:
        out = []
        for e in self.entries.values():
            if only and e.name not in only:
                continue
            if category and e.category not in category:
                continue
            if profile == "core" and not e.core:
                continue
            out.append(e)
        return out


def _expand(s: str, ctx: Dict[str, str], where: str) -> str:
    def rep(m):
        k = m.group(1)
        if k not in ctx:
            raise CatalogError(f"{where}: unknown template variable {{{k}}} in {s!r}")
        return ctx[k]
    return _TMPL.sub(rep, s)


_ENTRY_FIELDS = {f for f in Entry.__dataclass_fields__} - {"table", "artifacts"}
_ART_FIELDS = {f for f in Artifact.__dataclass_fields__} - {"table", "owner"}


def _typed(cls, raw: dict, where: str):
    fields = cls.__dataclass_fields__
    kw = {}
    for k, v in raw.items():
        if k not in fields or k in ("table", "artifacts", "owner"):
            raise CatalogError(f"{where}: unknown field {k!r}")
        f = fields[k]
        want = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", "")
        if want.startswith("List") and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            raise CatalogError(f"{where}: {k} must be an array of strings")
        if want == "int" and (isinstance(v, bool) or not isinstance(v, int)):
            raise CatalogError(f"{where}: {k} must be an integer")
        if want == "str" and not isinstance(v, str):
            raise CatalogError(f"{where}: {k} must be a string")
        kw[k] = v
    return kw


def load(path: Optional[str] = None) -> Catalog:
    path = path or os.environ.get("ARK_CATALOG") or DEFAULT_CATALOG
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        raise CatalogError(f"cannot read catalog {path}: {e}") from None
    return loads(text, path)


def loads(text: str, path: str = "<catalog>") -> Catalog:
    try:
        doc, tables = tomlsub.parse(text)
    except tomlsub.TomlError as e:
        raise CatalogError(f"{path}: {e}") from None
    meta = doc.get("ark") or {}
    if not isinstance(meta, dict):
        raise CatalogError(f"{path}: [ark] must be a table")
    if meta.get("schema") != 1:
        raise CatalogError(f"{path}: [ark] schema = 1 is required (found {meta.get('schema')!r})")
    entries: Dict[str, Entry] = {}
    for t in doc.get("entry", []):
        where = f"{path}:{t.header_line} [[entry]]"
        for req in ("name", "category", "description", "homepage", "license", "version"):
            if not t.get(req):
                raise CatalogError(f"{where}: missing {req}")
        e = Entry(**_typed(Entry, dict(t), where))
        e.table = t
        if e.name in entries:
            raise CatalogError(f"{where}: duplicate entry {e.name!r}")
        entries[e.name] = e
    arts: List[Artifact] = []
    seen = set()
    for t in doc.get("artifact", []):
        where = f"{path}:{t.header_line} [[artifact]]"
        for req in ("entry", "kind"):
            if not t.get(req):
                raise CatalogError(f"{where}: missing {req}")
        a = Artifact(**_typed(Artifact, dict(t), where))
        a.table = t
        if a.entry not in entries:
            raise CatalogError(f"{where}: entry {a.entry!r} is not defined")
        a.owner = entries[a.entry]
        if not a.id:
            a.id = default_id(a)
        if a.id in seen:
            raise CatalogError(f"{where}: duplicate artifact id {a.id!r} (set id = \"...\")")
        seen.add(a.id)
        entries[a.entry].artifacts.append(a)
        arts.append(a)
    return Catalog(path, text, meta, entries, arts, tables)


def default_id(a: Artifact) -> str:
    if a.kind == "oci":
        img = a.image.rsplit("/", 1)[-1]
        return f"{a.entry}/oci/{img}"
    if a.kind == "file":
        base = os.path.basename(urlsplit(a.url).path)
        base = base.replace("{version}", "V")
        base = re.sub(r"\{[a-z_]+\}", "X", base)
        return f"{a.entry}/{a.platform}/{base}"
    return f"{a.entry}/{a.kind}/{a.platform}"


# ── lint ─────────────────────────────────────────────────────────────────

def _license_ids(expr: str) -> List[str]:
    toks = re.split(r"\s+(?:OR|AND)\s+|[()]", expr)
    ids = []
    for t in toks:
        t = t.strip()
        if not t:
            continue
        lic, _, exc = t.partition(" WITH ")
        ids.append(lic.strip())
        if exc:
            ids.append(exc.strip())
    return ids


def license_is_osi(expr: str) -> bool:
    ids = _license_ids(expr)
    return bool(ids) and all(i in OSI or i in SPDX_EXCEPTIONS for i in ids)


def _url_ok(u: str) -> bool:
    p = urlsplit(u)
    if p.scheme == "https" and p.netloc:
        return True
    # loopback only: the test fixture server, or a box's own ark serve
    return p.scheme == "http" and (p.hostname or "") in ("127.0.0.1", "localhost", "::1")


def lint(cat: Catalog) -> List[str]:
    """Every problem with the catalog, as human-readable lines. Empty = clean.

    The rules the drive's integrity rests on: every entry has a licence (OSI,
    or a note saying why it is fine), a version, and either a pinned commit or
    an explicit `git_verified = "unverified"`; every file artifact has a
    sha256/upstream hash or an explicit `verified = "unverified"`; every OCI
    artifact a digest or the same explicit mark. Nothing is left silently blank.
    """
    errs: List[str] = []
    for e in cat.entries.values():
        w = f"entry {e.name}"
        if not _NAME.match(e.name):
            errs.append(f"{w}: name must match {_NAME.pattern}")
        if e.category not in CATEGORIES:
            errs.append(f"{w}: category {e.category!r} not in {CATEGORIES}")
        if not license_is_osi(e.license) and not e.license_note:
            errs.append(f"{w}: license {e.license!r} is not OSI-approved SPDX; add license_note saying why it may be archived")
        if not e.homepage.startswith("https://"):
            errs.append(f"{w}: homepage must be https://")
        if e.history not in HISTORY:
            errs.append(f"{w}: history must be one of {HISTORY}")
        for p in e.profiles:
            if p not in ("core",):
                errs.append(f"{w}: unknown profile {p!r}")
        try:
            e.ctx()
        except CatalogError as ex:
            errs.append(str(ex))
            continue
        if e.git:
            if not (_url_ok(e.git) or e.git.startswith("file://")):
                errs.append(f"{w}: git must be an https:// URL")
            if e.commit and not _HEX40.match(e.commit):
                errs.append(f"{w}: commit must be 40 hex chars")
            if not e.commit and e.git_verified != "unverified":
                errs.append(f"{w}: no commit pin — run `ark lock` (fills it from git ls-remote) or mark git_verified = \"unverified\"")
            if not e.tag and not e.commit:
                errs.append(f"{w}: a git pin needs a tag or a commit")
        if not e.artifacts and not e.git:
            errs.append(f"{w}: no git repo and no artifacts — nothing to archive")
        if e.update and not re.match(r"^(github|github-tag|node-lts|go|none|manual)(:.+)?$", e.update):
            errs.append(f"{w}: update {e.update!r} not understood")
    for a in cat.artifacts:
        w = f"artifact {a.id}"
        if a.kind not in KINDS:
            errs.append(f"{w}: kind {a.kind!r} not in {KINDS}")
            continue
        if a.verified not in VERIFIED:
            errs.append(f"{w}: verified {a.verified!r} not in {VERIFIED}")
        try:
            a.x(a.url)
            a.x(a.checksum_url)
            a.x(a.signature_url)
            a.x(a.tag)
            a.x(a.checksum_name)
        except CatalogError as ex:
            errs.append(str(ex))
            continue
        if a.kind == "file":
            if a.platform not in FILE_PLATFORMS:
                errs.append(f"{w}: platform {a.platform!r} not in {FILE_PLATFORMS}")
            if not a.url or not _url_ok(a.url_x):
                errs.append(f"{w}: url must be https:// (got {a.url_x!r})")
            for u in (a.checksum_url, a.signature_url):
                if u and not _url_ok(a.x(u)):
                    errs.append(f"{w}: {u!r} must be https://")
            if a.sha256 and not _HEX64.match(a.sha256):
                errs.append(f"{w}: sha256 must be 64 lowercase hex chars")
            if a.upstream_hash:
                algo, _, hx = a.upstream_hash.partition(":")
                if algo not in HASH_ALGOS or not re.fullmatch(r"[0-9a-f]{40,128}", hx):
                    errs.append(f"{w}: upstream_hash must be <algo>:<hex> with algo in {HASH_ALGOS}")
            if not a.pinned() and a.verified != "unverified":
                errs.append(f"{w}: no sha256 — run `ark lock` (fills it from {a.checksum_url or 'a checksum_url'}) or mark verified = \"unverified\"")
            if a.pinned() and a.verified in ("", "unverified"):
                errs.append(f"{w}: has a hash but verified = {a.verified!r}; say where the hash came from")
            if a.install not in INSTALL:
                errs.append(f"{w}: install {a.install!r} not in {INSTALL}")
            if a.signature_kind not in SIG_KINDS:
                errs.append(f"{w}: signature_kind {a.signature_kind!r} not in {SIG_KINDS}")
            if a.signature_url and not a.signature_kind:
                errs.append(f"{w}: signature_url without signature_kind")
            if a.signature_kind in ("minisign", "ed25519-sums") and not a.signature_key:
                errs.append(f"{w}: a {a.signature_kind} signature needs signature_key (a vendored public key)")
            if a.signature_key and not a.signature_key.startswith("RW"):
                kp = os.path.join(REPO_DIR, a.signature_key)
                if not os.path.isfile(kp):
                    errs.append(f"{w}: signature_key {a.signature_key} does not exist in the repo")
        elif a.kind == "oci":
            if not a.image or "/" not in a.image or ":" in a.image.rsplit("/", 1)[-1]:
                errs.append(f"{w}: image must be registry/repo with no tag (tag goes in `tag`)")
            if not a.tag and not a.digest:
                errs.append(f"{w}: an image needs a tag or a digest")
            if a.digest and not _DIGEST.match(a.digest):
                errs.append(f"{w}: digest must be sha256:<64 hex>")
            if not a.digest and a.verified != "unverified":
                errs.append(f"{w}: no digest — run `ark lock` (resolves the tag at the registry) or mark verified = \"unverified\"")
            if not a.platforms:
                errs.append(f"{w}: platforms = [\"linux/amd64\", ...] is required")
            for p in a.platforms:
                if p not in OCI_PLATFORM:
                    errs.append(f"{w}: platform {p!r} not in {tuple(OCI_PLATFORM)}")
        else:
            if a.platform not in FILE_PLATFORMS:
                errs.append(f"{w}: platform {a.platform!r} not in {FILE_PLATFORMS}")
            if a.kind == "pip" and not a.requirements:
                errs.append(f"{w}: a pip set needs requirements = [\"pkg==ver\", ...]")
            if a.kind == "pip":
                for r in a.requirements:
                    if not re.match(r"^[A-Za-z0-9._\[\],-]+==[A-Za-z0-9.+!-]+$", a.x(r)):
                        errs.append(f"{w}: requirement {r!r} must be name==exact-version")
            if a.kind == "npm" and not a.packages:
                errs.append(f"{w}: an npm set needs packages = [\"name@ver\", ...]")
            if a.kind in ("gomod", "cargo") and not cat.entries[a.entry].git:
                errs.append(f"{w}: a {a.kind} set is built from the entry's git pin, which is missing")
            if a.verified not in ("tofu", "unverified"):
                errs.append(f"{w}: a package set is locked at fetch time — mark verified = \"tofu\"")
    return errs
