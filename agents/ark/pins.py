"""beast-ark pins: `ark lock` fills them from upstream, `ark update` moves them.

`lock` (online) writes into catalog.toml, one line per value, only where a
field is empty (or --refresh):
  commit         git ls-remote of the pinned tag (the peeled commit)
  sha256         from the artifact's upstream checksum file, which must name
                 THIS file (verify.parse_checksum) — never from a download
                 we made ourselves; a non-sha256 upstream (sha512, sha3)
                 goes in upstream_hash instead
  size           Content-Length of a HEAD (a 404 here means the pin is wrong)
  digest, platform_sizes   the registry's digest for the tag, and the
                 per-platform bytes summed from the manifests
  verified       where the hash came from: upstream-sums / upstream-json /
                 registry — or "unverified" when upstream publishes nothing
  git_size       GitHub's repository size (an upper bound for a shallow pin)
`lock --from DIR` copies hashes the drive recorded on a trust-on-first-use
fetch, marked verified = "tofu", and real git bundle sizes.

`update` (online) finds each entry's newest upstream release and prints the
catalog diff. It writes nothing without --write, and when it writes, the
bumped entry's hashes, digests and commit are CLEARED, so the next `lock`
re-derives them from upstream and lint fails until it has.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from . import catalog as C
from . import net, oci, store as S
from . import tomlsub
from . import verify as V


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class Report(list):
    """Report lines, printed as they happen: a full `lock` is a few hundred
    upstream calls, and a silent one looks hung."""

    def append(self, line: str) -> None:
        super().append(line)
        log(line)


# ── edits, applied bottom-up so line numbers above stay valid ───────────

class Edits:
    def __init__(self):
        self.by_table: Dict[int, Dict[str, object]] = {}

    def set(self, table: tomlsub.Table, key: str, value) -> None:
        if table.get(key) == value:
            return
        self.by_table.setdefault(table.header_line, {})[key] = value

    def __len__(self):
        return sum(len(v) for v in self.by_table.values())

    def apply(self, cat: C.Catalog) -> str:
        text = cat.text
        tables = {t.header_line: t for t in cat.tables if t.kind}
        for hl in sorted(self.by_table, reverse=True):
            t = tables[hl]
            kv = self.by_table[hl]
            inserts = [(k, v) for k, v in kv.items() if k not in t.lines]
            for k, v in reversed(inserts):               # all land after the last key, in order
                text = tomlsub.set_key(text, t, k, v)
            for k, v in sorted(((k, v) for k, v in kv.items() if k in t.lines),
                               key=lambda kv2: t.lines[kv2[0]][0], reverse=True):
                text = tomlsub.set_key(text, t, k, v)
        return text


def show_diff(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        f"{path} (current)", f"{path} (proposed)", n=1))


def write(cat: C.Catalog, new: str) -> None:
    C.loads(new, cat.path)                      # never write a catalog that does not parse
    tmp = cat.path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, cat.path)


# ── upstream lookups ─────────────────────────────────────────────────────

def ls_remote(url: str, tag: str) -> str:
    r = subprocess.run(["git", "ls-remote", url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
                       capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if r.returncode != 0:
        raise RuntimeError(f"git ls-remote {url}: {r.stderr.strip()[-200:]}")
    refs = dict(reversed(ln.split("\t", 1)) for ln in r.stdout.splitlines() if "\t" in ln)
    sha = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    if not sha:
        raise RuntimeError(f"tag {tag} does not exist at {url}")
    return sha


_SUMS: Dict[str, bytes] = {}


def ls_remote_head(url: str) -> str:
    r = subprocess.run(["git", "ls-remote", url, "HEAD"], capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if r.returncode != 0 or not re.match(r"^[0-9a-f]{40}\tHEAD", r.stdout):
        raise RuntimeError(f"git ls-remote {url} HEAD: {r.stderr.strip()[-200:]}")
    return r.stdout.split("\t", 1)[0]


def ls_tags(url: str) -> List[str]:
    r = subprocess.run(["git", "ls-remote", "--tags", "--refs", url], capture_output=True, text=True,
                       timeout=120, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if r.returncode != 0:
        raise RuntimeError(f"git ls-remote {url}: {r.stderr.strip()[-200:]}")
    return [ln.split("refs/tags/", 1)[1] for ln in r.stdout.splitlines() if "refs/tags/" in ln]


_TOKEN: Optional[str] = None


def github_token() -> Optional[str]:
    """GITHUB_TOKEN, else `gh auth token`. Held in memory, sent only to
    api.github.com, never printed."""
    global _TOKEN
    if _TOKEN is None:
        tok = os.environ.get("GITHUB_TOKEN", "")
        if not tok and shutil.which("gh"):
            try:
                tok = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                tok = ""
        _TOKEN = tok
    return f"Bearer {_TOKEN}" if _TOKEN else None


def github_repo(url: str) -> Optional[str]:
    p = urlsplit(url)
    if p.hostname != "github.com":
        return None
    parts = p.path.strip("/").removesuffix(".git").split("/") if hasattr(str, "removesuffix") else \
        re.sub(r"\.git$", "", p.path.strip("/")).split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else None


def github_meta(repo: str) -> dict:
    data, _ = net.get_bytes(f"https://api.github.com/repos/{repo}", limit=1 << 20,
                            headers={"Accept": "application/vnd.github+json"}, token=github_token())
    return json.loads(data)


def _stable(v: str) -> bool:
    """A release version: starts with a digit, and no pre-release marker
    (rc, alpha, beta, dev, …, nor PEP 440's 2.22.0b2 / 1.0a1)."""
    return bool(re.match(r"^\d[\w.+-]*$", v)) and not re.search(
        r"(?i)(rc|alpha|beta|pre|dev|nightly|snapshot|test)|\d(a|b)\d", v)


def _vkey(v: str):
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.\-+_]", v)]


def tag_regex(e: C.Entry) -> re.Pattern:
    tmpl = e.tag
    out, i = "", 0
    for m in re.finditer(r"\{([A-Za-z_]+)\}", tmpl):
        out += re.escape(tmpl[i:m.start()])
        out += "(?P<version>.+)" if m.group(1) == "version" else ".+?"
        i = m.end()
    out += re.escape(tmpl[i:])
    return re.compile(f"^{out}$")


def latest(e: C.Entry) -> Tuple[str, str]:
    """-> (version, how). Raises on a lookup failure."""
    kind, _, arg = e.update.partition(":")
    if kind == "github":
        _st, _h, final = net.head(f"https://github.com/{arg}/releases/latest")
        m = re.search(r"/releases/tag/([^/?#]+)$", final)
        if not m:
            raise RuntimeError(f"{arg} has no latest release")
        tag = net.urllib.parse.unquote(m.group(1))
        mm = tag_regex(e).match(tag)
        if not mm or "version" not in mm.groupdict():
            return "", f"latest release tag {tag} does not fit tag template {e.tag!r} (bump by hand)"
        if not _stable(mm.group("version")):
            return "", f"latest release {tag} is a pre-release; left alone"
        return mm.group("version"), f"github release {tag}"
    if kind == "github-tag":
        repo, _, pat = arg.partition(":")
        rx = tag_regex(e)
        cands = []
        for t in ls_tags(f"https://github.com/{repo}.git"):
            mm = rx.match(t)
            if mm and "version" in mm.groupdict() and _stable(mm.group("version")):
                if not pat or re.match(pat, mm.group("version")):
                    cands.append(mm.group("version"))
        if not cands:
            raise RuntimeError(f"no stable tag of {repo} fits {e.tag!r}")
        return max(cands, key=_vkey), f"highest stable tag of {repo}"
    if kind == "node-lts":
        data, _ = net.get_bytes("https://nodejs.org/dist/index.json", limit=16 << 20)
        major = arg or ""
        for rel in json.loads(data):
            if rel.get("lts") and (not major or rel["version"].lstrip("v").split(".")[0] == major):
                return rel["version"].lstrip("v"), f"nodejs.org LTS {rel['lts']}"
        raise RuntimeError("no LTS in nodejs.org/dist/index.json")
    if kind == "go":
        data, _ = net.get_bytes("https://go.dev/dl/?mode=json", limit=4 << 20)
        for rel in json.loads(data):
            if rel.get("stable"):
                return rel["version"].removeprefix("go") if hasattr(str, "removeprefix") else rel["version"][2:], "go.dev/dl"
        raise RuntimeError("no stable go release")
    return "", "manual (no update source)"


# ── lock ─────────────────────────────────────────────────────────────────

def lock(cat: C.Catalog, entries: List[C.Entry], refresh: bool = False, from_drive: Optional[S.Drive] = None,
         sizes: bool = True, git_sizes: bool = True) -> Tuple[Edits, List[str]]:
    """-> (edits, report lines). Nothing is written here."""
    ed = Edits()
    rep: List[str] = Report()
    if from_drive:
        dl = from_drive.read_lock()
        for e in entries:
            it = dl["items"].get(f"git:{e.name}")
            if it and (refresh or not e.git_size):
                ed.set(e.table, "git_size", S.item_bytes(it))
            for a in e.artifacts:
                it = dl["items"].get(a.id)
                if not it:
                    continue
                if a.kind == "file" and not a.pinned() and it.get("source") == a.url_x:
                    f = next(x for x in it["files"] if x["path"] == it["primary"])
                    ed.set(a.table, "sha256", f["sha256"])
                    ed.set(a.table, "size", f["size"])
                    ed.set(a.table, "verified", "tofu")
                    rep.append(f"  {a.id}: sha256 from the drive (trust on first use)")
                elif a.kind in ("pip", "npm", "gomod", "cargo"):
                    ed.set(a.table, "est_size", S.item_bytes(it))
        return ed, rep
    net.require_online("ark lock")
    for e in entries:
        if e.git and not e.tag and not e.commit:
            # No tags upstream (DevDocs): pin today's default-branch commit.
            # It is a pin like any other from here on; `--refresh` never moves it.
            try:
                sha = ls_remote_head(e.git)
                ed.set(e.table, "commit", sha)
                rep.append(f"  {e.name}: HEAD = {sha[:12]} (no tags upstream)")
            except RuntimeError as ex:
                rep.append(f"  {e.name}: COMMIT NOT FOUND — {ex}")
        if e.git and e.tag and (refresh or not e.commit):
            try:
                sha = ls_remote(e.git, e.tag_x)
                ed.set(e.table, "commit", sha)
                if e.git_verified == "unverified":
                    ed.set(e.table, "git_verified", "")
                rep.append(f"  {e.name}: {e.tag_x} = {sha[:12]}")
            except RuntimeError as ex:
                rep.append(f"  {e.name}: COMMIT NOT FOUND — {ex}")
                if not e.commit:
                    ed.set(e.table, "git_verified", "unverified")
        if git_sizes and e.git and (refresh or not e.git_size):
            repo = github_repo(e.git)
            if repo:
                try:
                    ed.set(e.table, "git_size", int(github_meta(repo).get("size", 0)) * 1024)
                except (net.HTTPFailure, ValueError):
                    pass
        for a in e.artifacts:
            try:
                if a.kind == "file":
                    _lock_file(a, ed, rep, refresh, sizes)
                elif a.kind == "oci":
                    _lock_oci(a, ed, rep, refresh)
            except (net.HTTPFailure, V.VerifyError, oci.OciError, C.CatalogError, ValueError, KeyError) as ex:
                rep.append(f"  {a.id}: FAILED — {ex}")
    return ed, rep


def _lock_file(a: C.Artifact, ed: Edits, rep: List[str], refresh: bool, sizes: bool) -> None:
    if (refresh or not a.size) and sizes:
        st, h, _ = net.head(a.url_x)
        n = h.get("content-length", "")
        if n.isdigit() and int(n) > 0:
            ed.set(a.table, "size", int(n))
    if a.pinned() and not refresh:
        return
    if not a.checksum_url:
        if not a.pinned():
            ed.set(a.table, "verified", "unverified")
            rep.append(f"  {a.id}: no upstream checksum — marked unverified")
        return
    cu = a.x(a.checksum_url)
    if cu not in _SUMS:                         # one fetch per sums file per run
        _SUMS[cu] = net.get_bytes(cu, limit=64 << 20)[0]
    data = _SUMS[cu]
    name = a.x(a.checksum_name) or a.filename
    specific = a.x(a.checksum_url).split("?")[0].endswith(
        (name + ".sha256", name + ".sha256sum", name + ".sha512", name + ".sha256.txt"))
    algo, hx = V.parse_checksum(data.decode("utf-8", "replace"), name, a.x(a.checksum_format), url_specific=specific)
    how = "upstream-json" if a.checksum_format.startswith("json:") else "upstream-sums"
    if algo == "sha256" and a.checksum_format != "sqlite-csv":
        if a.sha256 and a.sha256 != hx:
            rep.append(f"  {a.id}: UPSTREAM CHANGED — the catalog pins {a.sha256[:16]}…, upstream now says {hx[:16]}… "
                       f"(never overwritten: investigate, and blank the sha256 by hand to accept the new file)")
            return
        ed.set(a.table, "sha256", hx)
    else:
        ed.set(a.table, "upstream_hash", f"{algo}:{hx}")
    ed.set(a.table, "verified", how)
    rep.append(f"  {a.id}: {algo} {hx[:16]}… ({how})")


def _lock_oci(a: C.Artifact, ed: Edits, rep: List[str], refresh: bool) -> None:
    if a.digest and a.platform_sizes and not refresh:
        return
    d = oci.describe(a.image, a.digest if (a.digest and not refresh) else a.tag_x, a.platforms)
    if d["missing"]:
        rep.append(f"  {a.id}: upstream has no {', '.join(d['missing'])} — narrow `platforms`")
    if a.digest and a.digest != d["digest"] and not refresh:
        rep.append(f"  {a.id}: tag {a.tag_x} now points to {d['digest'][:19]}, the catalog pins {a.digest[:19]}")
        return
    ed.set(a.table, "digest", d["digest"])
    ed.set(a.table, "platform_sizes", [f"{p}={n}" for p, n in sorted(d["sizes"].items())])
    ed.set(a.table, "verified", "registry")
    rep.append(f"  {a.id}: {a.tag_x} = {d['digest'][:19]} "
               f"({', '.join(f'{p} {n >> 20} MiB' for p, n in sorted(d['sizes'].items()))})")


# ── update ───────────────────────────────────────────────────────────────

def update(cat: C.Catalog, entries: List[C.Entry]) -> Tuple[Edits, List[str]]:
    net.require_online("ark update")
    ed = Edits()
    rep: List[str] = Report()
    for e in entries:
        if not e.update or e.update in ("none", "manual"):
            continue
        try:
            v, how = latest(e)
        except (RuntimeError, net.HTTPFailure, ValueError) as ex:
            rep.append(f"  {e.name}: lookup failed — {ex}")
            continue
        if not v:
            rep.append(f"  {e.name}: {how}")
            continue
        if v == e.version:
            continue
        if _vkey(v) < _vkey(e.version):
            rep.append(f"  {e.name}: upstream {v} is OLDER than the pin {e.version} — left alone")
            continue
        rep.append(f"  {e.name}: {e.version} -> {v} ({how})")
        ed.set(e.table, "version", v)
        if e.git and e.tag and "{version}" in e.tag:
            ed.set(e.table, "commit", "")
        for a in e.artifacts:
            templ = "{version}" in (a.url + a.tag + a.checksum_url + "".join(a.requirements + a.packages))
            if not templ:
                continue
            if a.kind == "file":
                for k, empty in (("sha256", ""), ("upstream_hash", ""), ("size", 0)):
                    if a.table.get(k):
                        ed.set(a.table, k, empty)
                ed.set(a.table, "verified", "")
            elif a.kind == "oci":
                ed.set(a.table, "digest", "")
                ed.set(a.table, "platform_sizes", [])
                ed.set(a.table, "verified", "")
    return ed, rep
