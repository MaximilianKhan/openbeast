#!/usr/bin/env python3
"""Generate and verify agents/requirements.lock — the hash-pinned closure.

WHY A LOCKFILE WHEN requirements.txt ALREADY PINS VERSIONS.  `==` pins the
6 direct dependencies and says nothing about the other 37 packages that
actually get installed, and it pins no CONTENT at all: a version can be
re-uploaded or an index can be impersonated, and `openai==3.9.0` would accept
whatever bytes arrive. The lock pins the whole closure to specific sha256s, so
`pip install --require-hashes` refuses anything else.

WHY EVERY FILE OF EACH RELEASE, not just the wheel resolved here.  The same
requirements install on Max's Mac (scripts/setup-client.sh) and on CI, under a
different python and a different platform, so a lock holding only this box's
cp314 manylinux wheels would BREAK both. pip accepts many `--hash` lines per
requirement and needs only the file it actually downloads to match, so the
lock lists every file PyPI publishes for that exact version — which is
strictly more permissive about platform and not at all about content.

THE RESOLUTION IS pip's, NOT OURS.  `pip install --dry-run --report` gives the
real resolver's answer, including its own sha256 for each artifact. This
module's only additions are (a) asking PyPI for that release's other files and
(b) cross-checking pip's hash against PyPI's, which is a free consistency
check between two sources that should never disagree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"
TIMEOUT = 30
UA = "openbeast-pydeps-lock/1 (+https://github.com/MaximilianKhan/openbeast)"

#: Files we will not record a hash for. A `.exe` installer is not something
#: this stack can install and listing it only invites confusion.
SKIP_SUFFIXES = (".exe", ".msi")


class LockError(RuntimeError):
    pass


def _norm(name: str) -> str:
    """PEP 503 normalisation. `huggingface_hub` and `huggingface-hub` are one
    package, and a lock that treats them as two silently loses a pin."""
    return re.sub(r"[-_.]+", "-", name).lower()


def resolve(req_files: list[str], extra: list[str], pip: list[str]) -> list[dict]:
    """The installable closure, as pip itself resolves it."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ob-lock-") as tmp:
        report = os.path.join(tmp, "report.json")
        argv = pip + ["install", "--dry-run", "--ignore-installed", "--quiet",
                      "--report", report]
        for f in req_files:
            argv += ["-r", f]
        argv += list(extra)
        p = subprocess.run(argv, capture_output=True, text=True, timeout=900)
        if p.returncode != 0:
            raise LockError(f"pip could not resolve the closure:\n"
                            f"{(p.stderr or p.stdout).strip()[:2000]}")
        with open(report, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    out = []
    for entry in doc.get("install", []):
        md = entry.get("metadata") or {}
        di = entry.get("download_info") or {}
        hashes = (di.get("archive_info") or {}).get("hashes") or {}
        name, version = md.get("name"), md.get("version")
        if not name or not version:
            raise LockError(f"pip reported an entry with no name/version: {entry!r}")
        out.append({
            "name": name,
            "version": version,
            "pip_sha256": hashes.get("sha256"),
            "pip_url": di.get("url"),
        })
    if not out:
        raise LockError("pip resolved an EMPTY closure — refusing to write a "
                        "lock that pins nothing")
    return sorted(out, key=lambda e: _norm(e["name"]))


def release_files(name: str, version: str) -> list[dict]:
    """Every file PyPI publishes for exactly this version."""
    url = PYPI_JSON.format(name=urllib.parse.quote(name),
                           version=urllib.parse.quote(version))
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            doc = json.load(r)
    except urllib.error.HTTPError as e:
        raise LockError(f"PyPI returned {e.code} for {name} {version}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LockError(f"cannot reach PyPI for {name} {version}: {e}") from e
    except ValueError as e:
        raise LockError(f"PyPI sent unparseable JSON for {name}: {e}") from e
    files = []
    for u in doc.get("urls") or []:
        fn = u.get("filename") or ""
        if fn.endswith(SKIP_SUFFIXES):
            continue
        sha = (u.get("digests") or {}).get("sha256")
        if not sha:
            raise LockError(f"{name} {version}: PyPI lists {fn} with no sha256")
        files.append({"filename": fn, "sha256": sha,
                      "kind": u.get("packagetype") or "?"})
    if not files:
        raise LockError(f"{name} {version}: PyPI lists no usable files")
    return sorted(files, key=lambda f: f["filename"])


def build(req_files: list[str], extra: list[str], pip: list[str]) -> dict:
    closure = resolve(req_files, extra, pip)
    packages, mismatches = [], []
    for entry in closure:
        files = release_files(entry["name"], entry["version"])
        known = {f["sha256"] for f in files}
        # Two independent sources for the same artifact. They should never
        # disagree; if they do, something is wrong upstream and writing the
        # lock anyway would bake it in.
        if entry["pip_sha256"] and entry["pip_sha256"] not in known:
            mismatches.append(
                f"{entry['name']} {entry['version']}: pip downloaded "
                f"sha256:{entry['pip_sha256']} but PyPI lists none of it "
                f"({len(known)} files)")
        packages.append({
            "name": entry["name"],
            "version": entry["version"],
            "files": files,
        })
    if mismatches:
        raise LockError("pip and PyPI disagree about artifact content:\n  "
                        + "\n  ".join(mismatches))
    direct = 0
    for f in req_files:
        for k, v in direct_pins(f).items():
            if v:
                direct += 1
    return {
        "generator": "scripts/pydeps.sh lock",
        "direct_pins": direct,
        "resolved_from": sorted(set(extra)) or [],
        "requirement_files": [os.path.relpath(f) for f in req_files],
        "resolver": _pip_version(pip),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": packages,
    }


def _pip_version(pip: list[str]) -> str:
    try:
        p = subprocess.run(pip + ["--version"], capture_output=True,
                           text=True, timeout=60)
        # JUST the version. `pip --version` prints "pip 26.2.1 from
        # /usr/lib/python3.14/site-packages/pip (python 3.14)", and splitting
        # on " (" kept the absolute path — which then went into a COMMITTED
        # file, making the lock carry one machine's filesystem layout for no
        # reason. Same leak as zig's std_dir in the L1 artifacts.
        m = re.search(r"pip\s+(\S+)", p.stdout or "")
        return f"pip {m.group(1)}" if m else "pip ?"
    except (OSError, subprocess.SubprocessError):
        return "pip ?"


# --------------------------------------------------------------------------
# rendering: a real pip requirements file, not a bespoke format
# --------------------------------------------------------------------------

def render(lock: dict) -> str:
    """`pip install --require-hashes -r` must accept this verbatim.

    Written as a requirements file rather than JSON because the consumer is
    pip: a format only our own scripts can read would need a translation step
    on the one path that must work on a box with nothing installed.
    """
    out = [
        "# GENERATED — do not edit. Regenerate: ./scripts/pydeps.sh lock",
        "#",
        "# The hash-pinned transitive closure of agents/requirements.txt.",
        f"# requirements.txt pins {lock.get('direct_pins', '?')} direct "
        f"versions; this pins all {len(lock['packages'])} packages",
        "# that actually get installed, and pins their CONTENT, so",
        "# `pip install --require-hashes` refuses anything else — a "
        "re-uploaded",
        "# version or an impersonated index cannot substitute bytes.",
        "#",
        "# Every file of each release is listed, not just the wheel this box "
        "resolved:",
        "# the same requirements install on macOS (scripts/setup-client.sh) "
        "and on CI",
        "# under other pythons, and pip needs only the file it actually "
        "downloads to",
        "# match. More permissive about platform, not at all about content.",
        "#",
        f"# resolver: {lock.get('resolver', '?')} on python "
        f"{lock.get('python', '?')}",
        f"# from:     {', '.join(lock.get('requirement_files') or [])}"
        + (f" + {', '.join(lock['resolved_from'])}"
           if lock.get("resolved_from") else ""),
        "",
    ]
    for pkg in lock["packages"]:
        lines = [f"{pkg['name']}=={pkg['version']} \\"]
        hashes = [f["sha256"] for f in pkg["files"]]
        for i, h in enumerate(hashes):
            tail = " \\" if i < len(hashes) - 1 else ""
            lines.append(f"    --hash=sha256:{h}{tail}")
        out.extend(lines)
    out.append("")
    return "\n".join(out)


LOCK_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)")
HASH_LINE = re.compile(r"^\s*--hash=sha256:([0-9a-f]{64})\s*\\?\s*$")


def parse(path: str) -> dict:
    """{normalised name: {"name":…, "version":…, "hashes":[…]}} from the file
    pip will actually read — never from a sidecar. A lock that is verified in
    one representation and consumed in another is two artifacts."""
    pkgs: dict = {}
    cur = None
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = LOCK_LINE.match(line.strip())
            if m:
                cur = {"name": m.group(1), "version": m.group(2), "hashes": []}
                pkgs[_norm(m.group(1))] = cur
                continue
            h = HASH_LINE.match(line)
            if h:
                if cur is None:
                    raise LockError(f"{path}:{lineno}: a hash before any "
                                    f"requirement")
                cur["hashes"].append(h.group(1))
                continue
            raise LockError(f"{path}:{lineno}: neither a pin nor a hash: "
                            f"{line.strip()[:80]!r}")
    if not pkgs:
        raise LockError(f"{path} pins nothing")
    return pkgs


def direct_pins(req_path: str) -> dict:
    """{normalised name: version or None} from a requirements.txt."""
    out = {}
    with open(req_path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#")[0].strip()
            if not line:
                continue
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*(\S+)$", line)
            if m:
                out[_norm(m.group(1))] = m.group(2)
            else:
                name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
                if name:
                    out[_norm(name)] = None
    return out


def verify(lock_path: str, req_paths: list[str], extra: list[str]) -> list[str]:
    """Offline consistency. Returns a list of problems (empty == good).

    What this can prove without a network: the lock parses as pip will read
    it, every requirement carries at least one well-formed hash, and every
    direct pin appears at the SAME version. What it cannot prove offline is
    closure completeness — that needs a resolver — so `pydeps.sh lock --check`
    re-resolves when a network is available.
    """
    problems: list[str] = []
    try:
        pkgs = parse(lock_path)
    except (LockError, OSError) as e:
        return [str(e)]
    for key, pkg in sorted(pkgs.items()):
        if not pkg["hashes"]:
            problems.append(f"{pkg['name']}=={pkg['version']} has NO hash — "
                            f"--require-hashes would refuse the whole file")
        if len(set(pkg["hashes"])) != len(pkg["hashes"]):
            problems.append(f"{pkg['name']}: duplicate hash lines")
    for req in req_paths:
        for key, want in direct_pins(req).items():
            if key not in pkgs:
                problems.append(f"{os.path.basename(req)} requires {key!r}, "
                                f"which the lock does not pin at all")
            elif want and pkgs[key]["version"] != want:
                problems.append(
                    f"{key}: {os.path.basename(req)} pins {want}, the lock "
                    f"pins {pkgs[key]['version']} — the lock is stale")
    for name in extra:
        if _norm(name) not in pkgs:
            problems.append(f"{name} is installed by bootstrap but the lock "
                            f"does not pin it")
    return problems


def audit_dir(lock_path: str, directory: str) -> tuple[list[str], list[str]]:
    """(matched, problems) for every file in a wheelhouse.

    A file whose hash is not in the lock is the interesting case: it is what a
    tampered or stale wheelhouse looks like, and installing from it with
    --no-index would never touch an index that could contradict it.
    """
    pkgs = parse(lock_path)
    known = {h for p in pkgs.values() for h in p["hashes"]}
    matched, problems = [], []
    for entry in sorted(os.listdir(directory)):
        path = os.path.join(directory, entry)
        if not os.path.isfile(path) or entry.startswith("."):
            continue
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
        if digest in known:
            matched.append(entry)
        else:
            problems.append(f"{entry}: sha256:{digest} is in no lock entry")
    return matched, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("build", "verify", "audit"))
    ap.add_argument("--lock", default="agents/requirements.lock")
    ap.add_argument("--req", action="append", default=[])
    ap.add_argument("--extra", action="append", default=[],
                    help="a package bootstrap installs by name, unpinned")
    ap.add_argument("--dir", help="wheelhouse directory (audit)")
    ap.add_argument("--pip", default=f"{sys.executable} -m pip")
    args = ap.parse_args(argv)
    reqs = args.req or ["agents/requirements.txt"]
    pip = args.pip.split()

    try:
        if args.action == "build":
            lock = build(reqs, args.extra, pip)
            text = render(lock)
            # Round-trip before writing: a lock that our own parser cannot
            # read is a lock nothing can verify later.
            import tempfile
            with tempfile.NamedTemporaryFile("w", suffix=".lock",
                                             delete=False) as fh:
                fh.write(text)
                tmp = fh.name
            try:
                parsed = parse(tmp)
                if len(parsed) != len(lock["packages"]):
                    raise LockError(
                        f"rendered {len(lock['packages'])} packages but parsed "
                        f"back {len(parsed)} — the renderer and the parser "
                        f"disagree")
            finally:
                os.unlink(tmp)
            with open(args.lock, "w", encoding="utf-8") as fh:
                fh.write(text)
            total = sum(len(p["files"]) for p in lock["packages"])
            print(f"wrote {args.lock}: {len(lock['packages'])} packages, "
                  f"{total} file hashes ({lock['resolver']}, python "
                  f"{lock['python']})")
            return 0

        if args.action == "verify":
            problems = verify(args.lock, reqs, args.extra)
            pkgs = parse(args.lock)
            nh = sum(len(p["hashes"]) for p in pkgs.values())
            if problems:
                print(f"{args.lock}: {len(problems)} problem(s)")
                for p in problems:
                    print(f"  - {p}")
                return 1
            print(f"{args.lock}: OK — {len(pkgs)} packages, {nh} file hashes, "
                  f"every direct pin matches")
            return 0

        if args.action == "audit":
            if not args.dir:
                print("audit needs --dir", file=sys.stderr)
                return 2
            matched, problems = audit_dir(args.lock, args.dir)
            for p in problems:
                print(f"  ! {p}")
            print(f"{args.dir}: {len(matched)} file(s) match the lock, "
                  f"{len(problems)} do not")
            return 1 if problems else 0
    except LockError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
