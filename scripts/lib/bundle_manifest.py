#!/usr/bin/env python3
"""The offline bundle's manifest: what is in it, and whether it still is.

A bundle is the four artifacts a first install cannot fetch on a closed
network — llama.cpp source, the python wheels, the container images, and
optionally a weight — collected on a connected box and carried in.

THE MANIFEST IS THE POINT, not the tarball. A directory that travelled on a
USB stick is not trusted just because it arrived: every file is recorded with
its sha256 here, `verify` re-hashes before anything is used, and `install`
refuses on a mismatch. That is the same discipline as the python lockfile
(scripts/lib/pydeps_lock.py) and scripts/fetch-weight.sh, which deletes a
weight whose hash is wrong rather than keeping it around to be used by
accident.

WHY IMAGES ARE RECORDED BY IMAGE ID.  docker-compose.yml pins images by
REGISTRY MANIFEST DIGEST (`repo@sha256:…`). `docker save` / `docker load` does
not carry that digest — the loaded image has no RepoDigest at all — so a
digest-pinned reference can never be satisfied from a tarball, which is the
trap the air-gap review flagged and the reason `docker save`/`load` alone does
not work.

The way out is not to abandon content addressing. An image's ID *is* a content
digest (of its config), it DOES survive save/load, and `docker compose`
accepts `image: sha256:<id>` and resolves it locally without pulling —
measured 2026-09-15, not assumed. So the manifest records the image ID, the
installer verifies the loaded image matches it, and the compose reference is
rewritten to that ID. Integrity is preserved, no registry is involved, and the
rewrite is reversible because the original file is kept.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

MANIFEST = "MANIFEST.json"
#: The detached signature over MANIFEST.json. Not a manifest entry by
#: construction — it is made AFTER the manifest exists, so a manifest
#: claiming a hash for its own signature could never be satisfied. It is
#: skipped when recording AND when scanning for unrecorded files, or every
#: signed bundle would fail its own verification for carrying a signature.
SIGNATURE = MANIFEST + ".sig"
#: Bump when a field changes meaning. An installer that does not recognise a
#: version must refuse rather than guess at a layout it does not know.
VERSION = 1
CHUNK = 1 << 20


class BundleError(RuntimeError):
    pass


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_path(root: str) -> str:
    return os.path.join(root, MANIFEST)


def safe_join(root: str, rel: str) -> str:
    """Resolve `rel` INSIDE `root`, or raise.

    Every path in a manifest is attacker-controlled in the threat model this
    bundle exists for (a directory that travelled on a stick), and os.path.join
    is not a containment primitive: join(root, "/etc/hostname") returns
    "/etc/hostname", and "../../../etc/hostname" walks out. Measured — before
    this, a manifest entry naming an absolute path made verify() hash the
    TARGET MACHINE'S file and report it as verified bundle content, and
    install() reads image tarballs from a manifest-supplied path.
    """
    if not rel or os.path.isabs(rel) or rel.startswith(("/", "\\")):
        raise BundleError(f"manifest path is not relative: {rel!r}")
    if "\x00" in rel:
        raise BundleError(f"manifest path contains a NUL: {rel!r}")
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise BundleError(f"manifest path is not contained: {rel!r}")
    full = os.path.join(root, *parts)
    # Belt: resolve and confirm containment, so a symlink component cannot
    # redirect the result either.
    rroot = os.path.realpath(root)
    rfull = os.path.realpath(full)
    if rfull != rroot and not rfull.startswith(rroot + os.sep):
        raise BundleError(
            f"manifest path escapes the bundle: {rel!r} -> {rfull}")
    return full


def load(root: str) -> dict:
    path = manifest_path(root)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        raise BundleError(f"{path} is missing — this is not a bundle") from None
    except ValueError as e:
        raise BundleError(f"{path} is not parseable JSON: {e}") from e
    if not isinstance(doc, dict):
        raise BundleError(f"{path} is not an object")
    got = doc.get("bundle_version")
    if got != VERSION:
        raise BundleError(
            f"{path} is bundle_version {got!r}, this tool speaks {VERSION} — "
            f"refusing to guess at a layout it does not know")
    return doc


def add_file(entries: list, root: str, rel: str) -> dict:
    """Record one file. Raises rather than recording a file it cannot read:
    a manifest entry for something unreadable is worse than no entry."""
    full = safe_join(root, rel)
    if os.path.islink(full):
        raise BundleError(
            f"{rel}: symlink. A bundle carries wheels, tarballs, image tars "
            f"and weights — all regular files — and a symlink inside one both "
            f"hides its target from this recorder and redirects whatever "
            f"reads it later.")
    if not os.path.isfile(full):
        raise BundleError(f"{rel}: not a file")
    rec = {"path": rel, "bytes": os.path.getsize(full),
           "sha256": sha256_file(full)}
    entries.append(rec)
    return rec


def walk_component(root: str, rel_dir: str) -> list:
    """Every file under one component directory, recorded."""
    entries: list = []
    base = os.path.join(root, rel_dir)
    if not os.path.isdir(base):
        return entries
    for dirpath, dirs, files in os.walk(base):
        # os.walk does NOT descend into a symlinked directory, so a symlink
        # here would hide every file beneath it from the manifest AND from
        # verify()'s unrecorded-file scan — measured: a bundle carrying a
        # hidden payload.whl behind one reported "1 ok, 0 problems".
        for name in sorted(dirs):
            if os.path.islink(os.path.join(dirpath, name)):
                raise BundleError(
                    f"{os.path.relpath(os.path.join(dirpath, name), root)}: "
                    f"symlinked directory. Everything under it would be "
                    f"invisible to this manifest and to verification.")
        for name in sorted(files):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if os.path.basename(rel) in (MANIFEST, SIGNATURE):
                continue
            add_file(entries, root, rel)
    return sorted(entries, key=lambda e: e["path"])


def verify(root: str) -> tuple[list, list]:
    """(ok, problems). Re-hashes every recorded file.

    Also reports files present in the directory that the manifest does NOT
    record — an unrecorded file is exactly what a tampered or half-rebuilt
    bundle looks like, and `install` reads from this directory.
    """
    doc = load(root)
    ok, problems = [], []
    recorded = set()
    for comp in doc.get("components", []):
        for rec in comp.get("files", []):
            rel = rec.get("path")
            if not rel:
                problems.append(f"{comp.get('kind')}: an entry with no path")
                continue
            recorded.add(rel)
            try:
                full = safe_join(root, rel)
            except BundleError as e:
                problems.append(str(e))
                continue
            if os.path.islink(full):
                problems.append(f"{rel}: is a symlink, not a file")
                continue
            if not os.path.isfile(full):
                problems.append(f"{rel}: recorded but MISSING")
                continue
            size = os.path.getsize(full)
            if size != rec.get("bytes"):
                problems.append(f"{rel}: {size} bytes, manifest says "
                                f"{rec.get('bytes')}")
                continue
            got = sha256_file(full)
            if got != rec.get("sha256"):
                problems.append(f"{rel}: sha256 {got[:16]}… != recorded "
                                f"{str(rec.get('sha256'))[:16]}…")
                continue
            ok.append(rel)
    for dirpath, dirs, files in os.walk(root):
        for name in sorted(dirs):
            if os.path.islink(os.path.join(dirpath, name)):
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                problems.append(
                    f"{rel}: symlinked directory — everything under it is "
                    f"invisible to this scan")
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if rel in (MANIFEST, SIGNATURE) or rel in recorded:
                continue
            if os.path.islink(full):
                problems.append(f"{rel}: unrecorded symlink")
                continue
            problems.append(f"{rel}: present but NOT in the manifest")
    return ok, problems


def summarise(doc: dict) -> str:
    lines = [
        f"bundle_version {doc.get('bundle_version')}  "
        f"built {doc.get('built_at', '?')}",
        f"repo commit    {doc.get('repo_commit', '?')}",
        f"eval era       {doc.get('eval_era', '?')}",
    ]
    if doc.get("built_on"):
        lines.append(f"built on       {doc['built_on']}")
    total = 0
    for comp in doc.get("components", []):
        n = len(comp.get("files", []))
        b = sum(f.get("bytes", 0) for f in comp.get("files", []))
        total += b
        extra = ""
        if comp.get("kind") == "source" and comp.get("commit"):
            extra = f"  llama.cpp {comp['commit']}"
        if comp.get("kind") == "images":
            extra = "  " + ", ".join(
                f"{i.get('ref', '?')} -> {str(i.get('id', '?'))[:19]}…"
                for i in comp.get("images", []))
        if comp.get("kind") == "weights":
            extra = "  " + ", ".join(w.get("file", "?")
                                     for w in comp.get("weights", []))
        lines.append(f"  {comp.get('kind', '?'):8} {n:4} file(s)  "
                     f"{_h(b):>9}{extra}")
    lines.append(f"  {'TOTAL':8} {'':4}          {_h(total):>9}")
    for skip in doc.get("skipped", []):
        lines.append(f"  NOT INCLUDED: {skip}")
    return "\n".join(lines)


def _h(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return str(n)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("write", "verify", "show"))
    ap.add_argument("root")
    ap.add_argument("--built-at", default="")
    ap.add_argument("--built-on", default="")
    ap.add_argument("--repo-commit", default="")
    ap.add_argument("--eval-era", default="")
    ap.add_argument("--component", action="append", default=[],
                    help="kind:reldir  (may repeat)")
    ap.add_argument("--meta", action="append", default=[],
                    help="kind:json  extra metadata for a component")
    ap.add_argument("--skipped", action="append", default=[])
    args = ap.parse_args(argv)

    try:
        if args.action == "write":
            comps = []
            metas = {}
            for m in args.meta:
                kind, _, blob = m.partition(":")
                try:
                    metas[kind] = json.loads(blob)
                except ValueError as e:
                    raise BundleError(f"--meta {kind}: {e}") from e
            for spec in args.component:
                kind, _, rel = spec.partition(":")
                if not kind or not rel:
                    raise BundleError(f"--component {spec!r} is not kind:reldir")
                files = walk_component(args.root, rel)
                if not files:
                    raise BundleError(
                        f"component {kind!r} at {rel!r} has no files — refusing "
                        f"to record an empty component, which would verify "
                        f"clean and install nothing")
                comp = {"kind": kind, "dir": rel, "files": files}
                comp.update(metas.get(kind) or {})
                comps.append(comp)
            doc = {
                "bundle_version": VERSION,
                "built_at": args.built_at,
                "built_on": args.built_on,
                "repo_commit": args.repo_commit,
                "eval_era": args.eval_era,
                "components": comps,
                "skipped": args.skipped,
            }
            with open(manifest_path(args.root), "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, sort_keys=True)
                fh.write("\n")
            n = sum(len(c["files"]) for c in comps)
            print(f"wrote {manifest_path(args.root)}: "
                  f"{len(comps)} component(s), {n} file(s)")
            return 0

        if args.action == "verify":
            ok, problems = verify(args.root)
            for p in problems:
                print(f"  ! {p}")
            print(f"{args.root}: {len(ok)} file(s) verified, "
                  f"{len(problems)} problem(s)")
            return 1 if problems else 0

        if args.action == "show":
            print(summarise(load(args.root)))
            return 0
    except BundleError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
