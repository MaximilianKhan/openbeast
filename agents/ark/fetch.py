"""beast-ark fetch: fill a drive from the catalog, verifying everything. docs/BEAST_ARK.md §6.

Per item, in order, and nothing reaches its final name until all pass:

  file  download (resumable .part) -> sha256 == the catalog pin -> the
        upstream checksum file names THIS file with the same hash -> the
        signature, where the catalog names one, verifies (minisign / ed25519
        here; gpg with a pinned keyring; else kept and reported "stored")
  oci   manifest by the pinned digest -> each kept platform's manifest,
        config and layers, each checked against its own digest
  git   fetch the pinned tag -> its commit == the catalog's commit ->
        a bundle (full history) or a tarred shallow bare repo
  pkgs  resolved by the ecosystem's own tool (pip / npm / go / cargo) and
        locked at fetch time: every file's sha256 goes in ark.lock, and the
        pip set gets a --require-hashes requirements file of its own

A mismatch is never "fixed" by re-recording: the .part is deleted, the item
fails, and the run exits non-zero. An artifact the catalog marks
`verified = "unverified"` (no upstream checksum exists) is refused unless
--allow-unverified, and is then recorded as "tofu" — trust on first use,
said out loud in the lock and in `ark verify`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import List, Optional, Tuple

from . import catalog as C
from . import net, oci, store as S
from . import verify as V


class FetchError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ── paths ────────────────────────────────────────────────────────────────

def git_rel(e: C.Entry) -> str:
    ref = e.tag_x if e.tag else e.commit[:12]
    ext = ".bundle" if e.history == "full" else ".shallow.git.tar"
    return f"git/{e.name}/{e.name}-{S.slug(ref)}{ext}"


def file_dir(a: C.Artifact) -> str:
    return f"files/{a.entry}/{S.slug(a.owner.version)}/{a.platform}"


def oci_rel(a: C.Artifact) -> str:
    img = a.image.rsplit("/", 1)[-1]
    return f"oci/{a.entry}/{S.slug(img)}_{S.slug(a.tag_x or a.digest[7:19])}.oci.tar"


def pkg_dir(a: C.Artifact) -> str:
    return f"pkgs/{a.kind}/{a.entry}-{S.slug(a.owner.version)}/{a.platform}"


def _rec(drive: S.Drive, rel: str) -> dict:
    p = drive.p(rel)
    return {"path": rel, "sha256": net.sha256_file(p), "size": os.path.getsize(p)}


# ── selection ────────────────────────────────────────────────────────────

def plan_items(cat: C.Catalog, entries: List[C.Entry], platforms: List[str], kinds=None,
               with_git: bool = True) -> List[Tuple[str, object]]:
    """[(key, Entry-for-git | Artifact)] in a stable order."""
    out: List[Tuple[str, object]] = []
    oci_want = {C.OCI_FOR[p] for p in platforms}
    for e in entries:
        if with_git and e.git and (not kinds or "git" in kinds):
            out.append((f"git:{e.name}", e))
        for a in e.artifacts:
            if kinds and a.kind not in kinds:
                continue
            if a.kind == "oci":
                if oci_want & set(a.platforms):
                    out.append((a.id, a))
            elif a.platform == "any" or a.platform in platforms:
                out.append((a.id, a))
    return out


# ── git ──────────────────────────────────────────────────────────────────

def _git(*args: str, cwd: Optional[str] = None, timeout: int = 3600) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true")
    r = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, capture_output=True,
                       text=True, timeout=timeout, env=env)
    if r.returncode != 0:
        raise FetchError(f"git {args[0]} failed: {r.stderr.strip()[-400:]}")
    return r.stdout.strip()


def fetch_git(drive: S.Drive, e: C.Entry, allow_unverified: bool) -> dict:
    if not shutil.which("git"):
        raise FetchError("git is not installed on this box")
    if not e.commit and not allow_unverified:
        raise FetchError(f"{e.name}: no commit pin in the catalog (run `ark lock`, or --allow-unverified)")
    rel = git_rel(e)
    stage = drive.stage("git", e.name)
    bare = os.path.join(stage, "repo.git")
    if not os.path.isdir(bare):
        _git("init", "-q", "--bare", bare)
    depth = [] if e.history == "full" else ["--depth", "1"]
    if e.tag:
        ref = f"refs/tags/{e.tag_x}"
        _git("fetch", "-q", "--no-tags", *depth, e.git, f"+{ref}:{ref}", cwd=bare)
        got = _git("rev-parse", f"{ref}^{{commit}}", cwd=bare)
    else:
        ref = "refs/heads/ark-pin"
        _git("fetch", "-q", "--no-tags", *depth, e.git, e.commit, cwd=bare)
        _git("update-ref", ref, e.commit, cwd=bare)
        got = _git("rev-parse", f"{ref}^{{commit}}", cwd=bare)
    if e.commit and got != e.commit:
        raise FetchError(f"{e.name}: tag {e.tag_x} is commit {got} upstream, the catalog pins {e.commit} — "
                         f"REFUSED (a moved tag is exactly what a pin is for)")
    verified = "pinned" if e.commit else "tofu"
    dest = drive.p(rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    if e.history == "full":
        _git("bundle", "create", "-q", tmp, ref, cwd=bare)
        _git("bundle", "verify", "-q", tmp, cwd=bare)
    else:
        _git("gc", "-q", "--prune=now", cwd=bare)
        with tarfile.open(tmp, "w") as t:
            t.add(bare, arcname=f"{e.name}.git")
    os.replace(tmp, dest)
    shutil.rmtree(stage, ignore_errors=True)
    return {"entry": e.name, "kind": "git", "platform": "any", "version": e.version,
            "tag": e.tag_x if e.tag else "", "commit": got, "ref": ref, "history": e.history,
            "source": e.git, "verified": verified, "files": [_rec(drive, rel)]}


# ── files ────────────────────────────────────────────────────────────────

def _fetch_small(url: str, dest: str) -> str:
    data, _ = net.get_bytes(url, limit=64 << 20)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def fetch_file(drive: S.Drive, a: C.Artifact, allow_unverified: bool) -> dict:
    d = file_dir(a)
    rel = f"{d}/{a.filename}"
    dest = drive.p(rel)
    url = a.url_x
    has_pin = bool(a.sha256 or a.upstream_hash)
    if not has_pin and not a.checksum_url and not allow_unverified:
        raise FetchError(f"{a.id}: no hash pin and no upstream checksum — refused "
                         f"(--allow-unverified records it as trust-on-first-use)")
    extra: List[str] = []
    sums_path = ""
    if a.checksum_url:
        sname = os.path.basename(a.x(a.checksum_url).split("?")[0]) or "checksums"
        if sname == a.filename:
            sname += ".sums"
        sums_rel = f"{d}/{sname}"
        sums_path = _fetch_small(a.x(a.checksum_url), drive.p(sums_rel))
        extra.append(sums_rel)
    part = net.download(url, dest, size=a.size)
    try:
        got = net.sha256_file(part)
        how = []
        if a.sha256:
            if got != a.sha256:
                raise FetchError(f"{a.id}: sha256 MISMATCH — downloaded {got}, the catalog pins {a.sha256}. "
                                 f"REFUSED and deleted.")
            how.append("pinned")
        if a.upstream_hash:
            algo, _, hx = a.upstream_hash.partition(":")
            if net.hash_file(part, algo) != hx:
                raise FetchError(f"{a.id}: {algo} MISMATCH against the catalog's upstream_hash. REFUSED and deleted.")
            how.append(f"pinned-{algo}")
        if sums_path:
            with open(sums_path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            name = a.x(a.checksum_name) or a.filename
            algo, hx = V.parse_checksum(text, name, a.x(a.checksum_format),
                                        url_specific=a.x(a.checksum_url).endswith(
                                            (a.filename + ".sha256", a.filename + ".sha256sum",
                                             a.filename + ".sha512", a.filename + ".sha256.txt")))
            if net.hash_file(part, algo) != hx:
                raise FetchError(f"{a.id}: the upstream checksum file says {algo} {hx[:16]}…, the download does "
                                 f"not match. REFUSED and deleted.")
            how.append("upstream-sums")
        sig = {"status": "none"}
        if a.signature_url:
            sig_rel = f"{d}/{os.path.basename(a.x(a.signature_url).split('?')[0])}"
            _fetch_small(a.x(a.signature_url), drive.p(sig_rel))
            extra.append(sig_rel)
            over_sums = bool(sums_path) and a.x(a.signature_url).startswith(a.x(a.checksum_url))
            target = sums_path if (over_sums or a.signature_kind == "ed25519-sums") else part
            if a.signature_kind == "ed25519-sums" and not sums_path:
                raise FetchError(f"{a.id}: an ed25519-sums signature needs the checksum file")
            st, detail = V.check_signature(a.signature_kind, a.signature_key, C.REPO_DIR, target,
                                           drive.p(sig_rel), a.signature_prefix,
                                           a.entry)
            sig = {"status": st, "kind": a.signature_kind, "over": "checksum" if target == sums_path else "file",
                   "detail": detail, "path": sig_rel, "key": a.signature_key, "prefix": a.signature_prefix,
                   "target": (extra[0] if target == sums_path else rel)}
            if st == "verified":
                how.append("signature")
        if not how:
            how.append("tofu")
    except (FetchError, V.VerifyError):
        os.remove(part)
        raise
    os.replace(part, dest)
    return {"entry": a.entry, "kind": "file", "platform": a.platform, "version": a.owner.version,
            "source": url, "install": a.install, "bins": [a.x(b) for b in a.bins], "strip": a.strip,
            "verified": "+".join(how), "signature": sig,
            "files": [_rec(drive, rel)] + [_rec(drive, r) for r in extra], "primary": rel}


# ── oci ──────────────────────────────────────────────────────────────────

def _oci_want(a: C.Artifact, platforms: List[str], prev: Optional[dict]) -> List[str]:
    """The image platforms to keep: what this run asks for PLUS what the drive
    already holds. One archive per image, rewritten whole on every pull, so a
    top-up for a second platform must carry the first one along — or the rig
    loses every amd64 image the day arm64 is added for the Sparks."""
    asked = {C.OCI_FOR[x] for x in platforms if x in C.OCI_FOR}
    held = set((prev or {}).get("platform", "").split(",")) - {""}
    return [p for p in a.platforms if p in asked | held]


def _seed_stage(drive: S.Drive, prev: Optional[dict], digest: str, stage: str) -> None:
    """Copy the blobs of the archive already on the drive into the stage, so a
    top-up downloads only the new platform's layers."""
    if not prev or prev.get("digest") != digest or not prev.get("primary"):
        return
    try:
        arc = oci.OciArchive(drive.p(prev["primary"]))
    except (OSError, tarfile.TarError, ValueError, KeyError, IndexError, S.StoreError):
        return
    try:
        blobs = os.path.join(stage, "blobs", "sha256")
        os.makedirs(blobs, exist_ok=True)
        for name in arc.members:
            if name.startswith("blobs/sha256/"):
                dst = os.path.join(blobs, name.rsplit("/", 1)[1])
                if not os.path.exists(dst):
                    with arc.tar.extractfile(arc.members[name]) as fi, open(dst + ".tmp", "wb") as fo:
                        shutil.copyfileobj(fi, fo, 1 << 20)
                    os.replace(dst + ".tmp", dst)       # re-hashed by Registry.blob before it is used
    finally:
        arc.close()


def fetch_oci(drive: S.Drive, a: C.Artifact, platforms: List[str], allow_unverified: bool,
              prev: Optional[dict] = None) -> dict:
    want = _oci_want(a, platforms, prev)
    digest, verified = a.digest, "registry-pinned"
    if not digest:
        if not allow_unverified:
            raise FetchError(f"{a.id}: no digest pin (run `ark lock`, or --allow-unverified)")
        digest, _ = oci.Registry(a.image).resolve(a.tag_x)
        verified = "tofu"
    rel = oci_rel(a)
    stage = drive.stage("oci", S.slug(a.id))
    _seed_stage(drive, prev, digest, stage)
    res = oci.pull(a.image, a.tag_x, digest, want, drive.p(rel), stage, log=log)
    shutil.rmtree(stage, ignore_errors=True)
    return {"entry": a.entry, "kind": "oci", "platform": ",".join(want), "version": a.owner.version,
            "image": a.image, "tag": a.tag_x, "digest": digest, "manifests": res["platforms"],
            "source": f"{a.image}@{digest}", "verified": verified, "files": [_rec(drive, rel)], "primary": rel}


# ── package sets ─────────────────────────────────────────────────────────

def _run(cmd: List[str], cwd: Optional[str] = None, env=None, timeout: int = 7200) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env=dict(os.environ, **(env or {})))
    if r.returncode != 0:
        raise FetchError(f"{os.path.basename(cmd[0])} failed: {(r.stderr or r.stdout).strip()[-600:]}")
    return r.stdout


def _all_files(drive: S.Drive, rel_dir: str) -> List[dict]:
    out = []
    base = drive.p(rel_dir)
    for root, _dirs, files in os.walk(base):
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(root, f), drive.root).replace(os.sep, "/")
            out.append(_rec(drive, rel))
    return out


def fetch_pip(drive: S.Drive, a: C.Artifact) -> dict:
    d = pkg_dir(a)
    out = drive.p(d)
    os.makedirs(out, exist_ok=True)
    reqs = [a.x(r) for r in a.requirements]
    py = sys.executable
    cmd = [py, "-m", "pip", "download", "--disable-pip-version-check", "--no-input", "--only-binary=:all:",
           "--dest", out]
    if a.python:
        cmd += ["--python-version", a.python, "--implementation", "cp"]
    for t in a.pip_platforms:
        cmd += ["--platform", t]
    _run(cmd + reqs)
    files = _all_files(drive, d)
    lockf = os.path.join(out, "requirements.ark.txt")
    with open(lockf, "w", encoding="utf-8") as f:
        f.write("# beast-ark: every wheel in this directory, hash-pinned. Install offline with\n"
                "#   pip install --no-index --find-links . --require-hashes -r requirements.ark.txt\n")
        for rec in files:
            name = os.path.basename(rec["path"])
            if name.endswith((".whl", ".tar.gz", ".zip")):
                proj, ver = _wheel_name(name)
                f.write(f"{proj}=={ver} --hash=sha256:{rec['sha256']}\n")
    files = _all_files(drive, d)
    return {"entry": a.entry, "kind": "pip", "platform": a.platform, "version": a.owner.version,
            "requirements": reqs, "python": a.python, "source": "pypi", "verified": "tofu", "files": files,
            "primary": d}


def _wheel_name(fn: str) -> Tuple[str, str]:
    if fn.endswith(".whl"):
        parts = fn[:-4].split("-")
        return parts[0], parts[1]
    stem = fn[:-7] if fn.endswith(".tar.gz") else fn[:-4]
    proj, _, ver = stem.rpartition("-")
    return proj, ver


def fetch_npm(drive: S.Drive, a: C.Artifact) -> dict:
    npm = shutil.which("npm")
    if not npm:
        raise FetchError("npm is not installed on this box")
    d = pkg_dir(a)
    out = drive.p(d)
    os.makedirs(os.path.join(out, "tarballs"), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        deps = {}
        for p in a.packages:
            p = a.x(p)
            name, _, ver = p.rpartition("@")
            deps[name] = ver
        with open(os.path.join(tmp, "package.json"), "w") as f:
            json.dump({"name": f"ark-{a.entry}", "version": "0.0.0", "private": True, "dependencies": deps}, f)
        _run([npm, "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=tmp)
        with open(os.path.join(tmp, "package-lock.json")) as f:
            plock = json.load(f)
        shutil.copy2(os.path.join(tmp, "package.json"), os.path.join(out, "package.json"))
        shutil.copy2(os.path.join(tmp, "package-lock.json"), os.path.join(out, "package-lock.json"))
    for path, meta in sorted((plock.get("packages") or {}).items()):
        if not path or not meta.get("resolved") or meta.get("link"):
            continue
        url, integ = meta["resolved"], meta.get("integrity", "")
        fn = os.path.basename(url.split("?")[0])
        scope = path.split("node_modules/")[-1].split("/")[0] if "/@" in "/" + path.split("node_modules/")[-1] else ""
        rel = f"{d}/tarballs/{(scope + '__') if scope.startswith('@') else ''}{fn}"
        part = net.download(url, drive.p(rel))
        if integ:
            algo, _, b64 = integ.partition("-")
            h = hashlib.new(algo)
            with open(part, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
            if base64.b64encode(h.digest()).decode() != b64:
                os.remove(part)
                raise FetchError(f"{a.id}: {fn} does not match the registry's integrity {integ[:24]}… — REFUSED")
        os.replace(part, drive.p(rel))
    return {"entry": a.entry, "kind": "npm", "platform": a.platform, "version": a.owner.version,
            "packages": [a.x(p) for p in a.packages], "source": "registry.npmjs.org",
            "verified": "npm-integrity", "files": _all_files(drive, d), "primary": d}


def _clone_from_drive(drive: S.Drive, e: C.Entry, dest: str, lock: dict) -> None:
    it = lock["items"].get(f"git:{e.name}")
    if not it:
        raise FetchError(f"{e.name}: fetch the git pin first (a {e.name} package set is built from it)")
    src = drive.p(it["files"][0]["path"])
    if src.endswith(".bundle"):
        _git("clone", "-q", src, dest)
    else:
        tmp = dest + ".bare"
        os.makedirs(tmp, exist_ok=True)
        with tarfile.open(src, "r|") as t:
            _safe_extract(t, tmp)
        _git("clone", "-q", os.path.join(tmp, f"{e.name}.git"), dest)
    _git("checkout", "-q", it["commit"], cwd=dest)


_DATA_FILTER = hasattr(tarfile, "data_filter")    # 3.12+, and the 3.9.17+/3.10.12+/3.11.4+ backports


def _inside(root: str, p: str) -> bool:
    return p == root or p.startswith(root + os.sep)


def _safe_extract(t: tarfile.TarFile, dest: str) -> None:
    """Unpack `t` into `dest`, refusing anything that would land outside it.

    One forward pass, one member at a time, and every check is made against
    what is ON DISK at that moment — not against an empty destination. A
    check made up front is fooled by a link chain (y -> ".", then x -> "y/..",
    then x/evil): before y exists, x looks like it stays inside. Here x is
    checked after y has been written, so it resolves to the parent and is
    refused. At the end every symlink is resolved once more against the final
    tree, so a link made to point outside by a LATER member is caught too
    (and removed). The 3.12 `data` filter is applied on top where the
    interpreter has it; nothing depends on it (a Mac's /usr/bin/python3 3.9.6
    has none). The archive is read once, front to back, so a caller can open
    it in stream mode ("r|*") and a .tar.xz is decompressed once, not twice.
    """
    os.makedirs(dest, exist_ok=True)
    root = os.path.realpath(dest)
    links: List[Tuple[str, str]] = []
    kw = {"filter": "data"} if _DATA_FILTER else {}
    try:
        for m in t:
            if m.isdev():
                raise FetchError(f"archive member {m.name!r} is a device node or fifo — refused")
            if not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
                raise FetchError(f"archive member {m.name!r} has an unsupported type — refused")
            full = os.path.normpath(os.path.join(root, m.name))
            # where the member itself lands: its parent as it resolves NOW, plus its own name
            loc = os.path.join(os.path.realpath(os.path.dirname(full)), os.path.basename(full))
            if not _inside(root, loc) or (not m.issym() and not _inside(root, os.path.realpath(loc))):
                raise FetchError(f"archive member {m.name!r} escapes the target — refused")
            if m.issym():
                to = os.path.realpath(os.path.join(os.path.dirname(loc), m.linkname))
                if os.path.isabs(m.linkname) or not _inside(root, to):
                    raise FetchError(f"archive link {m.name!r} -> {m.linkname!r} escapes the target — refused")
                links.append((loc, m.name))
            elif m.islnk():
                src = os.path.realpath(os.path.join(root, m.linkname))
                if not _inside(root, src):
                    raise FetchError(f"archive link {m.name!r} -> {m.linkname!r} escapes the target — refused")
            if not _DATA_FILTER and m.isfile():
                m.mode &= 0o755                     # no setuid/setgid/sticky, no group/other write
            t.extract(m, root, set_attrs=not m.isdir(), **kw)  # noqa: S202 — checked above
    finally:
        # a refused archive, or one whose links point out once complete:
        # take every such link away, so nothing later follows it
        bad = [name for loc, name in links if not _inside(root, os.path.realpath(loc))]
        for loc, _name in links:
            if os.path.islink(loc) and not _inside(root, os.path.realpath(loc)):
                os.unlink(loc)
    if bad:
        raise FetchError(f"archive link {bad[0]!r} resolves outside the target once unpacked — refused")


def fetch_gomod(drive: S.Drive, a: C.Artifact, lock: dict) -> dict:
    go = shutil.which("go")
    if not go:
        raise FetchError("go is not installed on this box")
    d = pkg_dir(a)
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src")
        _clone_from_drive(drive, a.owner, src, lock)
        cache = os.path.join(tmp, "modcache")
        _run([go, "mod", "download", "-x"], cwd=os.path.join(src, a.moddir or "."),
             env={"GOMODCACHE": cache, "GOFLAGS": "-mod=mod", "GOTOOLCHAIN": "local"})
        out = drive.p(f"{d}/goproxy")
        if os.path.isdir(out):
            shutil.rmtree(out)
        shutil.copytree(os.path.join(cache, "cache", "download"), out,
                        ignore=shutil.ignore_patterns("*.lock", "*.partial", "*.tmp"))
    return {"entry": a.entry, "kind": "gomod", "platform": a.platform, "version": a.owner.version,
            "source": "proxy.golang.org (checked against go.sum)", "verified": "go.sum",
            "files": _all_files(drive, d), "primary": f"{d}/goproxy"}


def fetch_cargo(drive: S.Drive, a: C.Artifact, lock: dict) -> dict:
    cargo = shutil.which("cargo")
    if not cargo:
        raise FetchError("cargo is not installed on this box")
    d = pkg_dir(a)
    rel = f"{d}/vendor.tar"
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src")
        _clone_from_drive(drive, a.owner, src, lock)
        work = os.path.join(src, a.moddir or ".")
        cfg = _run([cargo, "vendor", "--locked", "--versioned-dirs", os.path.join(tmp, "vendor")], cwd=work)
        os.makedirs(drive.p(d), exist_ok=True)
        with open(drive.p(f"{d}/cargo-config.toml"), "w") as f:
            f.write(cfg.replace(os.path.join(tmp, "vendor"), "vendor"))
        with tarfile.open(drive.p(rel), "w") as t:
            t.add(os.path.join(tmp, "vendor"), arcname="vendor")
    return {"entry": a.entry, "kind": "cargo", "platform": a.platform, "version": a.owner.version,
            "source": "crates.io (checked against Cargo.lock)", "verified": "Cargo.lock",
            "files": _all_files(drive, d), "primary": rel}


# ── driver ───────────────────────────────────────────────────────────────

def item_present(drive: S.Drive, item: Optional[dict], deep: bool = False) -> bool:
    if not item or not item.get("files"):
        return False
    for f in item["files"]:
        p = drive.p(f["path"])
        if not os.path.isfile(p) or os.path.getsize(p) != f["size"]:
            return False
        if deep and net.sha256_file(p) != f["sha256"]:
            return False
    return True


def run(cat: C.Catalog, drive: S.Drive, items: List[Tuple[str, object]], platforms: List[str],
        allow_unverified: bool = False, recheck: bool = False, dry_run: bool = False) -> int:
    net.require_online("ark fetch")
    os.makedirs(drive.root, exist_ok=True)
    lock = drive.read_lock()
    lock.setdefault("platforms", [])
    lock["platforms"] = sorted(set(lock["platforms"]) | set(platforms))
    with open(cat.path, "rb") as f:
        lock["catalog_sha256"] = hashlib.sha256(f.read()).hexdigest()
    shutil.copy2(cat.path, os.path.join(drive.root, "catalog.toml"))
    S.install_tool(drive)
    done = skipped = failed = 0
    failures: List[str] = []
    for key, obj in items:
        prev = lock["items"].get(key)
        if item_present(drive, prev, deep=recheck) and _still_matches(prev, obj, platforms):
            skipped += 1
            continue
        label = key
        if dry_run:
            log(f"  would fetch {label}")
            continue
        log(f"  fetch {label}")
        try:
            if isinstance(obj, C.Entry):
                rec = fetch_git(drive, obj, allow_unverified)
            elif obj.kind == "file":
                rec = fetch_file(drive, obj, allow_unverified)
            elif obj.kind == "oci":
                rec = fetch_oci(drive, obj, lock["platforms"], allow_unverified, prev)
            elif obj.kind == "pip":
                rec = fetch_pip(drive, obj)
            elif obj.kind == "npm":
                rec = fetch_npm(drive, obj)
            elif obj.kind == "gomod":
                rec = fetch_gomod(drive, obj, lock)
            elif obj.kind == "cargo":
                rec = fetch_cargo(drive, obj, lock)
            else:
                raise FetchError(f"unknown kind {obj.kind}")
        except (FetchError, V.VerifyError, oci.OciError, net.HTTPFailure, S.StoreError, OSError) as ex:
            failed += 1
            failures.append(f"{label}: {ex}")
            log(f"  FAILED {label}: {ex}")
            continue
        rec["fetched_at"] = S.now()
        lock["items"][key] = rec
        drive.write_lock(lock)
        done += 1
        log(f"    ok {label} ({S.item_bytes(rec) / 2**20:.1f} MiB, {rec['verified']})")
    drive.write_lock(lock)
    S.write_readme(drive, cat, lock)
    log(f"fetch: {done} fetched, {skipped} already present, {failed} failed — {drive.root}")
    for f in failures:
        log(f"  - {f}")
    return 4 if failed else 0


def _still_matches(prev: dict, obj, platforms: List[str]) -> bool:
    """A present item still answers the catalog: same pin, enough platforms."""
    if isinstance(obj, C.Entry):
        return not obj.commit or prev.get("commit") == obj.commit
    a = obj
    if a.kind == "file":
        if prev.get("source") != a.url_x:
            return False
        return not a.sha256 or any(f["sha256"] == a.sha256 for f in prev["files"])
    if a.kind == "oci":
        want = {p for p in a.platforms if p in {C.OCI_FOR[x] for x in platforms}}
        have = set((prev.get("platform") or "").split(","))
        return (not a.digest or prev.get("digest") == a.digest) and want <= have
    return prev.get("version") == a.owner.version
