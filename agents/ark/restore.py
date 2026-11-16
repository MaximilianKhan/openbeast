"""beast-ark restore: put one entry from a drive onto this box, with no network.

Every file is re-hashed against ark.lock BEFORE it is used, and a mismatch
refuses the whole entry. Where things go (all user-owned, no sudo):

  release files   ~/.local/opt/<entry>-<version>/   (unpacked; a single top
                  directory is stripped), ~/.local/opt/<entry> -> that,
                  and its binaries linked into ~/.local/bin
  git             ~/src/<entry>  (cloned from the bundle / shallow repo,
                  checked out at the pinned commit, which is re-checked)
  OCI images      --registry HOST:PORT  pushed (digests unchanged), or
                  --docker / --podman   loaded (one platform: this box's), or
                  neither               the archive path + the skopeo line
  pip set         a venv at ~/.local/opt/<entry>-<version>/venv, installed with
                  --no-index --require-hashes from the drive
  npm set         the tarballs into npm's cache, then `npm ci --offline`
  go modules      a GOPROXY=file://… line for the drive's module tree
  cargo vendor    unpacked to ~/.local/opt/<entry>-<version>/vendor + config
  manual / data   the path, and the catalog's note on what to do with it
"""
from __future__ import annotations

import bz2
import gzip
import lzma
import os
import platform as _pf
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from typing import List, Optional

from . import catalog as C
from . import net, oci, store as S
from .fetch import FetchError, _safe_extract


class RestoreError(RuntimeError):
    pass


def host_platform() -> str:
    s, m = _pf.system(), _pf.machine().lower()
    if s == "Linux" and m in ("x86_64", "amd64"):
        return "linux-x86_64"
    if s == "Linux" and m in ("aarch64", "arm64"):
        return "linux-aarch64"
    if s == "Darwin" and m == "arm64":
        return "darwin-arm64"
    return f"{s.lower()}-{m}"


# ark.lock lives on the drive it describes, so nothing in it is trusted to
# name a place on THIS box: entry names, bin paths, tags and commits are
# checked before any of them reaches a path or a command line.
_ENTRY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]{0,199}$")


def _inside(root: str, p: str) -> bool:
    r = os.path.realpath(root)
    q = os.path.realpath(p)
    return q == r or q.startswith(r + os.sep)


def _bin_name(name: str) -> str:
    """A single file name for <root>/bin/ and the bin dir, never a path."""
    if name in ("", ".", "..") or "/" in name or "\\" in name or "\0" in name:
        raise RestoreError(f"ark.lock names the binary {name!r}, which is not a file name — refused")
    return name


def _contained(root: str, rel: str) -> str:
    """root/rel, refused unless it resolves (links and all) inside root."""
    if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
        raise RestoreError(f"ark.lock names {rel!r} outside the install root — refused")
    p = os.path.join(root, rel)
    if not _inside(root, p):
        raise RestoreError(f"{rel!r} resolves outside {root} — refused")
    return p


def check_item(item: dict) -> None:
    """The lock values that become paths or git arguments on this box."""
    if not _ENTRY.match(str(item.get("entry", ""))):
        raise RestoreError(f"ark.lock entry name {item.get('entry')!r} is not a catalog name — refused")
    if item.get("kind") == "git":
        if not _COMMIT.match(str(item.get("commit", ""))):
            raise RestoreError(f"ark.lock commit {item.get('commit')!r} is not a full hex commit id — refused")
        tag = str(item.get("tag") or "")
        if tag and (not _TAG.match(tag) or ".." in tag):
            raise RestoreError(f"ark.lock tag {tag!r} is not a plain tag name — refused")


def check_files(drive: S.Drive, item: dict) -> None:
    for f in item["files"]:
        p = drive.p(f["path"])
        if not os.path.isfile(p):
            raise RestoreError(f"{f['path']} is missing from the drive")
        got = net.sha256_file(p)
        if got != f["sha256"]:
            raise RestoreError(f"{f['path']} is CORRUPT (sha256 {got[:16]}…, ark.lock says {f['sha256'][:16]}…) "
                               f"— refusing to restore it")


def _strip_name(fn: str) -> str:
    for ext in (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz", ".tar.zst", ".zip", ".gz", ".bz2", ".xz"):
        if fn.endswith(ext):
            return fn[: -len(ext)]
    return fn


def _link(target: str, link: str) -> None:
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if os.path.islink(link) or os.path.exists(link):
        if os.path.isdir(link) and not os.path.islink(link):
            raise RestoreError(f"{link} is a real directory; not replacing it")
        os.remove(link)
    os.symlink(target, link)


def _merge(src: str, dst: str) -> None:
    """Move src's tree into dst; a path present in both is replaced by src's."""
    if not os.path.isdir(dst) or os.path.islink(dst):
        if os.path.lexists(dst):
            os.remove(dst)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.rename(src, dst)
        return
    for name in os.listdir(src):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if os.path.isdir(s) and not os.path.islink(s) and os.path.isdir(d) and not os.path.islink(d):
            _merge(s, d)
        else:
            if os.path.isdir(d) and not os.path.islink(d):
                shutil.rmtree(d)
            elif os.path.lexists(d):
                os.remove(d)
            os.rename(s, d)


def _unzstd(src: str, dst: str) -> None:
    try:
        from compression import zstd  # type: ignore  # noqa: PLC0415 — python 3.14+
        with zstd.open(src, "rb") as fi, open(dst, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        return
    except ImportError:
        pass
    z = shutil.which("zstd")
    if not z:
        raise RestoreError("a .zst archive needs python >= 3.14 or the zstd binary")
    subprocess.run([z, "-q", "-d", "-f", src, "-o", dst], check=True)


def _extract(src: str, dest: str) -> None:
    fn = os.path.basename(src)
    if fn.endswith(".zip"):
        root = os.path.realpath(dest)
        with zipfile.ZipFile(src) as z:
            for info in z.infolist():
                target = os.path.realpath(os.path.join(dest, info.filename))
                if not (target == root or target.startswith(root + os.sep)):
                    raise RestoreError(f"zip member {info.filename!r} escapes the target — refused")
            for info in z.infolist():
                z.extract(info, dest)
                mode = (info.external_attr >> 16) & 0o777
                if mode and not info.is_dir():
                    os.chmod(os.path.join(dest, info.filename), mode)
        return
    if fn.endswith(".tar.zst"):
        with tempfile.TemporaryDirectory() as tmp:
            raw = os.path.join(tmp, "x.tar")
            _unzstd(src, raw)
            with tarfile.open(raw, "r|") as t:
                _safe_extract(t, dest)
        return
    with tarfile.open(src, "r|*") as t:          # stream mode: decompressed once, front to back
        _safe_extract(t, dest)


def install_file(drive: S.Drive, item: dict, prefix: str, bindir: str, note: str = "") -> List[str]:
    primary = drive.p(item["primary"])
    fn = os.path.basename(primary)
    root = os.path.join(prefix, f"{item['entry']}-{S.slug(item['version'])}")
    kind = item.get("install") or ""
    bins = item.get("bins") or []
    out: List[str] = []
    if kind in ("manual", "data", ""):
        out.append(f"{item['entry']}: {item['primary']} is kept as-is ({kind or 'no install rule'}) — {primary}")
        if note:
            out.append(f"  note: {note}")
        return out
    part = root + ".partial"
    shutil.rmtree(part, ignore_errors=True)
    os.makedirs(part)
    try:
        if kind == "bin":
            name = _bin_name(bins[0] if bins else fn)
            os.makedirs(os.path.join(part, "bin"))
            shutil.copy2(primary, os.path.join(part, "bin", name))
            bins = [f"bin/{name}"]
        elif kind in ("gz", "bz2", "xz"):
            opener = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open}[kind]
            name = _bin_name(bins[0] if bins else _strip_name(fn))
            os.makedirs(os.path.join(part, "bin"))
            with opener(primary, "rb") as fi, open(os.path.join(part, "bin", name), "wb") as fo:
                shutil.copyfileobj(fi, fo)
            bins = [f"bin/{name}"]
        elif kind in ("tar", "zip", "zst"):
            _extract(primary, part)
            kids = [k for k in os.listdir(part) if not k.startswith("._")]
            strip = item.get("strip", -1)
            if (strip == -1 and len(kids) == 1 and os.path.isdir(os.path.join(part, kids[0]))) or strip == 1:
                inner = os.path.join(part, kids[0])
                tmp = part + ".inner"
                os.rename(inner, tmp)
                shutil.rmtree(part)
                os.rename(tmp, part)
        else:
            raise RestoreError(f"unknown install rule {kind!r}")
        for b in bins:
            p = _contained(part, b)
            if os.path.isfile(p):
                os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        # Merge, never replace: one entry's artifacts (kubeadm + kubelet +
        # kubectl, loki + logcli) all land in the same versioned root.
        _merge(part, root)
    finally:
        shutil.rmtree(part, ignore_errors=True)
    _link(root, os.path.join(prefix, item["entry"]))
    if not bins and os.path.isdir(os.path.join(root, "bin")):
        bins = [f"bin/{b}" for b in sorted(os.listdir(os.path.join(root, "bin")))
                if os.access(os.path.join(root, "bin", b), os.X_OK) and not os.path.isdir(os.path.join(root, "bin", b))]
    linked = []
    for b in bins:
        p = _contained(root, b)
        if not os.path.exists(p):
            out.append(f"  warning: {b} is not in the archive")
            continue
        _link(p, os.path.join(bindir, _bin_name(os.path.basename(b))))
        linked.append(os.path.basename(b))
    out.append(f"{item['entry']} {item['version']}: {root}" + (f"  (linked into {bindir}: {', '.join(linked)})" if linked else ""))
    return out


def restore_git(drive: S.Drive, item: dict, srcdir: str, force: bool) -> List[str]:
    src = drive.p(item["files"][0]["path"])
    dest = os.path.join(srcdir, item["entry"])
    if os.path.exists(dest):
        if not force:
            return [f"{item['entry']}: {dest} exists (pass --force to replace it)"]
        shutil.rmtree(dest)
    os.makedirs(srcdir, exist_ok=True)
    from .fetch import _git  # noqa: PLC0415
    if src.endswith(".bundle"):
        _git("clone", "-q", "--no-checkout", src, dest)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(src, "r|") as t:
                _safe_extract(t, tmp)
            bare = os.path.join(tmp, f"{item['entry']}.git")
            _git("clone", "-q", "--no-checkout", bare, dest)
            # keep the objects: the clone's alternates would point into tmp
            _git("repack", "-a", "-d", "-q", cwd=dest)
            alt = os.path.join(dest, ".git", "objects", "info", "alternates")
            if os.path.exists(alt):
                os.remove(alt)
    _git("checkout", "-q", item["commit"], cwd=dest)
    head = _git("rev-parse", "HEAD", cwd=dest)
    if head != item["commit"]:
        raise RestoreError(f"{item['entry']}: checked out {head}, the lock pins {item['commit']}")
    if item.get("tag"):
        _git("tag", "-f", item["tag"], item["commit"], cwd=dest)
    return [f"{item['entry']}: source at {dest} ({item.get('tag') or 'commit'} {item['commit'][:12]}, verified)"]


def restore_oci(drive: S.Drive, item: dict, registry: str, engine: str, plat: str) -> List[str]:
    path = drive.p(item["primary"])
    arc = oci.OciArchive(path)
    try:
        repo = oci.repo_path(item["image"])
        tag = item.get("tag") or "latest"
        if registry:
            res = oci.push(arc, registry, repo, tag, log=lambda s: print(s, file=sys.stderr))
            note = " (reduced index: the registry wanted every platform's manifest)" if res["reduced"] else ""
            return [f"{item['entry']}: pushed {registry}/{repo}:{tag} index {res['index'][:19]}{note}"]
        if engine:
            exe = shutil.which(engine)
            if not exe:
                raise RestoreError(f"{engine} is not installed")
            op = C.OCI_FOR.get(plat, "linux/amd64")
            ref = f"{item['image']}:{tag}"
            with tempfile.TemporaryDirectory() as tmp:
                one = os.path.join(tmp, "one.tar")
                mdig = oci.single_platform_tar(arc, op, one, ref)
                r = subprocess.run([exe, "load", "-i", one], capture_output=True, text=True)
                if r.returncode != 0:
                    raise RestoreError(f"{engine} load failed: {r.stderr.strip()[-300:]}")
            return [f"{item['entry']}: {engine} load {ref} ({op}, manifest {mdig[:19]})"]
        return [f"{item['entry']}: OCI archive {path}",
                f"  load it:  docker load -i {path}      (docker >= 25, OCI layout)",
                f"  or:       skopeo copy oci-archive:{path} docker://HOST:PORT/{repo}:{tag} --all",
                f"  or:       ark restore {item['entry']} --from {drive.root} --registry HOST:PORT"]
    finally:
        arc.close()


def restore_pip(drive: S.Drive, item: dict, prefix: str, python: str) -> List[str]:
    d = drive.p(item["primary"])
    root = os.path.join(prefix, f"{item['entry']}-{S.slug(item['version'])}")
    venv = os.path.join(root, "venv")
    py = python or sys.executable
    want = item.get("python") or ""
    if want:
        r = subprocess.run([py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], capture_output=True, text=True)
        if r.stdout.strip() != want:
            raise RestoreError(f"{item['entry']}'s wheels are for python {want}; {py} is {r.stdout.strip() or '?'} "
                               f"(restore cpython first, then --python ~/.local/opt/cpython-…/bin/python3)")
    subprocess.run([py, "-m", "venv", venv], check=True)
    vpy = os.path.join(venv, "bin", "python")
    r = subprocess.run([vpy, "-m", "pip", "install", "--disable-pip-version-check", "--no-index", "--find-links", d,
                        "--require-hashes", "-r", os.path.join(d, "requirements.ark.txt")], capture_output=True, text=True)
    if r.returncode != 0:
        raise RestoreError(f"pip install (offline) failed: {r.stderr.strip()[-500:]}")
    return [f"{item['entry']}: venv {venv} (offline, --require-hashes)"]


def restore_npm(drive: S.Drive, item: dict, prefix: str) -> List[str]:
    npm = shutil.which("npm")
    if not npm:
        raise RestoreError("npm is not installed (restore node first: ark restore node --from DIR)")
    d = drive.p(item["primary"])
    root = os.path.join(prefix, f"{item['entry']}-{S.slug(item['version'])}")
    os.makedirs(root, exist_ok=True)
    tb = os.path.join(d, "tarballs")
    for fn in sorted(os.listdir(tb)):
        subprocess.run([npm, "cache", "add", os.path.join(tb, fn)], check=True, capture_output=True)
    for fn in ("package.json", "package-lock.json"):
        shutil.copy2(os.path.join(d, fn), os.path.join(root, fn))
    r = subprocess.run([npm, "ci", "--offline", "--no-audit", "--no-fund"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        raise RestoreError(f"npm ci --offline failed: {r.stderr.strip()[-500:]}")
    return [f"{item['entry']}: node_modules in {root} (npm ci --offline)"]


def restore(cat: Optional[C.Catalog], drive: S.Drive, name: str, plat: str = "", prefix: str = "",
            bindir: str = "", srcdir: str = "", registry: str = "", engine: str = "",
            kinds: Optional[List[str]] = None, python: str = "", force: bool = False) -> List[str]:
    if not _ENTRY.match(name):
        raise RestoreError(f"{name!r} is not an entry name")
    lock = drive.read_lock()
    items = [(k, v) for k, v in sorted(lock["items"].items()) if v.get("entry") == name]
    if not items:
        raise RestoreError(f"{name} is not on this drive ({drive.root}); `ark list --from {drive.root}` shows what is")
    plat = plat or host_platform()
    prefix = os.path.expanduser(prefix or "~/.local/opt")
    bindir = os.path.expanduser(bindir or "~/.local/bin")
    srcdir = os.path.expanduser(srcdir or "~/src")
    note = ""
    if cat and name in cat.entries:
        note = cat.entries[name].note
    notes = {a.id: a.note for a in (cat.entries[name].artifacts if cat and name in cat.entries else [])}
    out: List[str] = []
    for key, it in items:
        k = it["kind"]
        if kinds and k not in kinds:
            continue
        if k in ("file", "pip", "npm") and it["platform"] not in ("any", plat):
            continue
        try:
            check_item(it)
            check_files(drive, it)
        except RestoreError as e:
            raise RestoreError(f"{key}: {e}") from None
        try:
            if k == "git":
                out += restore_git(drive, it, srcdir, force)
            elif k == "file":
                out += install_file(drive, it, prefix, bindir, notes.get(key) or note)
            elif k == "oci":
                out += restore_oci(drive, it, registry, engine, plat)
            elif k == "pip":
                out += restore_pip(drive, it, prefix, python)
            elif k == "npm":
                out += restore_npm(drive, it, prefix)
            elif k == "gomod":
                out.append(f"{name}: GOFLAGS=-mod=mod GONOSUMDB=* GOPROXY=file://{drive.p(it['primary'])} go build ./...")
            elif k == "cargo":
                root = os.path.join(prefix, f"{name}-{S.slug(it['version'])}")
                os.makedirs(root, exist_ok=True)
                with tarfile.open(drive.p(it["primary"]), "r|") as t:
                    _safe_extract(t, root)
                shutil.copy2(drive.p(os.path.dirname(it["primary"]) + "/cargo-config.toml"),
                             os.path.join(root, "cargo-config.toml"))
                out.append(f"{name}: vendored crates in {root}/vendor — copy {root}/cargo-config.toml into the "
                           f"project's .cargo/config.toml (with directory = \"{root}/vendor\")")
        except (FetchError, subprocess.CalledProcessError, OSError, oci.OciError) as e:
            raise RestoreError(f"{key}: {e}") from None
    if not out:
        out.append(f"{name}: nothing on this drive for {plat} (the drive holds {sorted({v['platform'] for _k, v in items})})")
    return out
