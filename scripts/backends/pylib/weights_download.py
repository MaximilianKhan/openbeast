#!/usr/bin/env python3
"""Download a Hugging Face model's weights from a URL into one known folder, verified.

    weights-download.sh URL [DEST]            download (or, if already there, re-verify)
    weights-download.sh URL [DEST] --dry-run  list what would download and where; write nothing
    weights-download.sh --verify DIR          re-hash DIR against its manifest (offline; no network)

URL is any Hub address for a model, or the bare repo id:
    https://huggingface.co/ORG/REPO                       the whole repo at main
    https://huggingface.co/ORG/REPO/tree/REV[/SUBDIR]     a branch, tag or commit (and only SUBDIR)
    https://huggingface.co/ORG/REPO/blob/REV/FILE         one file (resolve/ links too)
    ORG/REPO                                              same as the first form
DEST is the model's own folder. Without it the folder is
    $WEIGHTS_DOWNLOAD_ROOT/ORG/REPO    (WEIGHTS_DOWNLOAD_ROOT defaults to ~/Documents/weights)

What it guarantees:
  1. The revision is resolved to a commit SHA once; every file is requested at that commit only.
  2. Every file is checked against the Hub's own hash (LFS sha256, or git blob id for small files).
     A bad file is deleted and the run fails.
  3. Files land in a hidden stage beside DEST (.NAME.partial) and appear in DEST only after all
     of them passed. An interrupted run resumes from the stage: re-run the same command.
  4. DEST/.openbeast-weights.json records the URL, the commit and every file's size and sha256.
     A second run of the same command re-verifies instead of downloading; a different repo or
     commit is never written into an existing DEST (pick another DEST, or move the old one aside).
Dotfiles (.gitattributes, .github/) are skipped; --include/--exclude GLOB narrow the selection.
The Hub is reached only through hfapi.py: HF_ENDPOINT, HF_TOKEN / HF_TOKEN_FILE, OFFLINE.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import hfapi
import model_fetch as mf

MANIFEST = ".openbeast-weights.json"
STAGE_META = ".stage.json"
DEFAULT_ROOT = "~/Documents/weights"
EXIT_FAIL, EXIT_USAGE, EXIT_MISMATCH = 1, 2, 3
HUB_HOSTS = {"huggingface.co", "www.huggingface.co", "hf.co"}
_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class DownloadError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- URL

def parse_url(url: str) -> dict:
    """-> {repo, revision (None = main), subdir, file}. Raises DownloadError on anything else."""
    raw = (url or "").strip()
    if not raw:
        raise DownloadError("no URL given")
    if "://" in raw or raw.split("/", 1)[0].lower() in HUB_HOSTS:
        u = urllib.parse.urlsplit(raw if "://" in raw else "https://" + raw)
        host = u.netloc.lower().split("@")[-1].split(":")[0]
        if host not in HUB_HOSTS and _origin_host(hfapi.endpoint()) != host:
            raise DownloadError(f"{host} is not huggingface.co (or HF_ENDPOINT's host)")
        parts = [urllib.parse.unquote(p) for p in u.path.strip("/").split("/") if p]
    else:
        parts = [p for p in raw.strip("/").split("/") if p]
    if parts and parts[0] in ("datasets", "spaces"):
        raise DownloadError(f"{raw} is a {parts[0][:-1]}, not a model: only model repos are supported")
    if parts and parts[0] == "models":
        parts = parts[1:]
    if len(parts) < 2 or not (_PART.match(parts[0]) and _PART.match(parts[1])):
        raise DownloadError(f"cannot read ORG/REPO from {raw!r} (expected https://huggingface.co/ORG/REPO)")
    out = {"repo": f"{parts[0]}/{parts[1]}", "revision": None, "subdir": None, "file": None}
    rest = parts[2:]
    if not rest:
        return out
    kind = rest[0]
    if kind not in ("tree", "blob", "resolve") or len(rest) < 2:
        raise DownloadError(f"cannot read {raw!r}: after ORG/REPO expected tree/, blob/ or resolve/")
    out["revision"] = rest[1]
    path = "/".join(rest[2:])
    if path:
        _safe(path)
        if kind == "tree":
            out["subdir"] = path
        else:
            out["file"] = path
    elif kind != "tree":
        raise DownloadError(f"{raw!r} names no file after the revision")
    return out


def _origin_host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower().split("@")[-1].split(":")[0]


def _safe(path: str) -> str:
    pp = PurePosixPath(path)
    if not path or pp.is_absolute() or ".." in pp.parts or "\\" in path:
        raise DownloadError(f"unsafe path {path!r}")
    return path


def default_dest(repo: str) -> Path:
    root = os.environ.get("WEIGHTS_DOWNLOAD_ROOT") or DEFAULT_ROOT
    return Path(os.path.expanduser(root)) / repo


# --------------------------------------------------------------------------- selection

def _hit(path: str, name: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(name, g) for g in globs)


def select(entries: list[dict], spec: dict, include: list[str], exclude: list[str]) -> list[dict]:
    chosen = []
    for e in entries:
        path = _safe(e["path"])
        name = PurePosixPath(path).name
        if any(p.startswith(".") for p in PurePosixPath(path).parts):
            continue
        if spec.get("file") and path != spec["file"]:
            continue
        if spec.get("subdir") and not path.startswith(spec["subdir"].rstrip("/") + "/"):
            continue
        if include and not _hit(path, name, include):
            continue
        if exclude and _hit(path, name, exclude):
            continue
        chosen.append(e)
    if not chosen:
        what = spec.get("file") or (spec.get("subdir") and spec["subdir"] + "/") or "the repo"
        raise DownloadError(f"nothing to download: no file of {what} matches (check the URL and --include/--exclude)")
    big = [e for e in chosen if int(e.get("size") or 0) > 10 * 1024 * 1024 and not (e.get("lfs") or {}).get("oid")]
    if big:
        raise DownloadError(f"{big[0]['path']} is large but the Hub gives no sha256 to verify it against — refusing")
    return chosen


# --------------------------------------------------------------------------- manifest

def read_manifest(d: Path) -> dict:
    p = d / MANIFEST
    if not p.is_file():
        return {}
    try:
        m = json.loads(p.read_text())
    except ValueError as e:
        raise DownloadError(f"{p} is not valid JSON ({e}) — move {d} aside and download again") from None
    return m if isinstance(m, dict) else {}


def write_manifest(d: Path, m: dict) -> None:
    tmp = d / f".{MANIFEST}.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, d / MANIFEST)


def verify(d: Path, only: set[str] | None = None) -> list[str]:
    """Re-hash d against its manifest; returns the problems (empty = every file matches)."""
    m = read_manifest(d)
    if not m.get("files"):
        return [f"{d} has no {MANIFEST} (not downloaded by weights-download.sh)"]
    files = {k: v for k, v in m["files"].items() if only is None or k in only}
    probs, present = [], []
    for rel, want in sorted(files.items()):
        p = d / rel
        if not p.is_file():
            probs.append(f"{rel}: missing")
        elif p.stat().st_size != int(want["size"]):
            probs.append(f"{rel}: {p.stat().st_size} bytes, the manifest says {want['size']}")
        else:
            present.append(p)
    for p, got in mf.sha256_many(present).items():
        rel = p.relative_to(d).as_posix()
        if got != files[rel]["sha256"]:
            probs.append(f"{rel}: sha256 {got} is not the manifest's {files[rel]['sha256']}")
    return probs


# --------------------------------------------------------------------------- download

class Progress:
    """A line every 30 s while files download: big shards otherwise look like a hang."""

    def __init__(self, stage: Path, entries: list[dict], total: int):
        self.stage, self.entries, self.total = stage, entries, total
        self.done = threading.Event()
        self.t0 = time.time()
        self.t = threading.Thread(target=self._run, daemon=True)

    def have(self) -> int:
        n = 0
        for e in self.entries:
            p = self.stage / e["path"]
            if p.is_file():
                n += p.stat().st_size
        return n

    def _run(self):
        while not self.done.wait(30):
            have, dt = self.have(), time.time() - self.t0
            log(f"  … {have / 1e9:.2f} / {self.total / 1e9:.2f} GB ({100 * have / max(self.total, 1):.0f}%), "
                f"{dt / 60:.0f} min")

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.done.set()


def _fetch_one(repo: str, sha: str, e: dict, stage: Path) -> dict:
    dest = stage / e["path"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        mf._download(repo, sha, e, dest)
        local = mf._verify_entry(dest, e)
    except mf.FetchError as err:
        dest.unlink(missing_ok=True)
        raise DownloadError(str(err)) from None
    log(f"  ok {e['path']} ({int(e.get('size') or 0) / 1e9:.2f} GB)")
    return {"size": int(e.get("size") or 0), "sha256": local}


def download(url: str, dest: Path | None, revision: str | None, include: list[str], exclude: list[str],
             jobs: int = 4, dry_run: bool = False) -> int:
    spec = parse_url(url)
    if revision:
        if spec["revision"] and spec["revision"] != revision:
            raise DownloadError(f"the URL names revision {spec['revision']} and --revision says {revision}")
        spec["revision"] = revision
    repo, asked = spec["repo"], spec["revision"] or "main"
    dest = (dest or default_dest(repo)).expanduser().resolve()
    log(f"repo {repo} @ {asked}")
    sha = hfapi.resolve_sha(repo, asked)
    if sha != asked:
        log(f"  {asked} is commit {sha}")
    entries = select(hfapi.tree(repo, sha), spec, include, exclude)
    total = sum(int(e.get("size") or 0) for e in entries)
    log(f"{len(entries)} file(s), {total / 1e9:.2f} GB -> {dest}")

    have = read_manifest(dest) if dest.is_dir() else {}
    if dest.exists() and not dest.is_dir():
        raise DownloadError(f"{dest} exists and is not a folder")
    if have and (have.get("repo") != repo or have.get("revision") != sha):
        raise DownloadError(f"{dest} already holds {have.get('repo')}@{have.get('revision')} — never overwritten. "
                            "Give another DEST, or move that folder aside")
    if dest.is_dir() and not have and any(dest.iterdir()):
        raise DownloadError(f"{dest} is not empty and has no {MANIFEST} — never overwritten. "
                            "Give another DEST, or move that folder aside")
    known = (have.get("files") or {})
    todo = [e for e in entries if e["path"] not in known]
    if dry_run:
        for e in entries:
            mark = "have" if e["path"] in known else "get "
            log(f"  {mark} {e['path']}  {int(e.get('size') or 0) / 1e9:.2f} GB")
        log("dry run: nothing written")
        return 0
    if not todo:
        log(f"already downloaded: re-verifying {len(entries)} file(s) in {dest}")
        probs = verify(dest, only={e["path"] for e in entries})
        for p in probs:
            log(f"  MISMATCH {p}")
        if probs:
            log(f"{dest} does not match its manifest: move it aside and download again")
            return EXIT_MISMATCH
        log(f"OK {dest}: every file matches the manifest")
        print(dest)
        return 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = dest.parent / f".{dest.name}.partial"
    meta = {"repo": repo, "revision": sha}
    if stage.exists():
        try:
            m = json.loads((stage / STAGE_META).read_text())
        except (OSError, ValueError):
            m = {}
        if m != meta:
            raise DownloadError(f"{stage} holds another download ({m.get('repo')}@{m.get('revision')}) — "
                                "delete it to start over")
        log(f"resuming from {stage}")
    else:
        stage.mkdir()
        (stage / STAGE_META).write_text(json.dumps(meta))
    need = total - sum(int(e.get("size") or 0) for e in entries if e["path"] in known)
    staged = sum((stage / e["path"]).stat().st_size for e in todo if (stage / e["path"]).is_file())
    free = shutil.disk_usage(dest.parent).free
    if need - staged + 10**9 > free:
        raise DownloadError(f"not enough space on {dest.parent}: need {(need - staged) / 1e9:.1f} GB "
                            f"(+1 GB margin), {free / 1e9:.1f} GB free")

    files: dict[str, dict] = {}
    with Progress(stage, todo, need), ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = {e["path"]: ex.submit(_fetch_one, repo, sha, e, stage) for e in todo}
        errors = []
        for path, f in futs.items():
            try:
                files[path] = f.result()
            except DownloadError as err:
                errors.append(str(err))
    if errors:
        for err in errors:
            log(f"FAILED {err}")
        log(f"{len(errors)} file(s) failed; the good ones stay in {stage}. Re-run the same command to resume")
        return EXIT_FAIL

    # Publish: everything verified. A new DEST is one rename; an existing one (same repo and
    # commit, more files) takes the new files one rename each, then the merged manifest.
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    m = have or {"schema": 1, "repo": repo, "revision": sha, "files": {}, "sources": []}
    m["files"].update(files)
    m["sources"].append({"url": url, "requested_revision": asked, "include": include, "exclude": exclude,
                         "downloaded_at": now, "files": sorted(files)})
    if not dest.exists():
        write_manifest(stage, m)
        (stage / STAGE_META).unlink()
        os.rename(stage, dest)
    else:
        for rel in files:
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / rel, dest / rel)
        write_manifest(dest, m)
        shutil.rmtree(stage)
    log(f"verified and in place: {dest} ({len(m['files'])} file(s), manifest {MANIFEST})")
    print(dest)
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="weights-download.sh", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?", help="a huggingface.co model URL, or ORG/REPO")
    ap.add_argument("dest", nargs="?", type=Path, help=f"the model's folder (default {DEFAULT_ROOT}/ORG/REPO)")
    ap.add_argument("--revision", help="branch, tag or commit (default: the URL's, else main)")
    ap.add_argument("--include", action="append", default=[], metavar="GLOB", help="only matching files (repeatable)")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="skip matching files (repeatable)")
    ap.add_argument("--jobs", type=int, default=4, help="files downloaded at once (default 4)")
    ap.add_argument("--dry-run", action="store_true", help="list the files and the folder; write nothing")
    ap.add_argument("--verify", type=Path, metavar="DIR", help="re-hash DIR against its manifest; no network")
    a = ap.parse_args(argv)
    try:
        if a.verify:
            if a.url:
                ap.error("--verify takes only DIR")
            probs = verify(a.verify.expanduser())
            for p in probs:
                log(f"MISMATCH {p}")
            if not probs:
                log(f"OK {a.verify}: every file matches the manifest")
            return EXIT_MISMATCH if probs else 0
        if not a.url:
            ap.print_usage(sys.stderr)
            return EXIT_USAGE
        return download(a.url, a.dest, a.revision, a.include, a.exclude, a.jobs, a.dry_run)
    except hfapi.Offline as e:
        log(f"Error: {e}. Offline, copy the folder from another machine instead (the other Spark, or "
            "the backup SSD), then check it with: weights-download.sh --verify DIR")
        return EXIT_FAIL
    except (DownloadError, hfapi.HubError, mf.FetchError) as e:
        log(f"Error: {e}")
        return EXIT_FAIL
    except KeyboardInterrupt:
        log("interrupted: re-run the same command to resume")
        return 130


if __name__ == "__main__":
    sys.exit(main())
