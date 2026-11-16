#!/usr/bin/env python3
"""Download a profile's pinned checkpoint, verify every byte, and lock it.

    model-fetch.sh --profile NAME            fetch (or, if present, verify) → MODELS_DIR/NAME
    model-fetch.sh --profile NAME --verify   re-hash what is on disk against the lock; download nothing.
                                             No lock = exit 3 (a local SOURCE: --trust-first-use records one)
    model-fetch.sh --profile NAME --locate   (launchers) print the locked dir; fast: weight files by
                                             size, every other file by sha256. No lock = exit 3

Guarantees, in the order they matter:
  1. Only the profile's REVISION (a commit SHA) is ever requested — resolve/<sha>/…, never a branch.
  2. Every file is checked against the Hub's own hash for that commit before it counts: LFS files
     against lfs.oid (sha256 of the content), small git files against their git blob SHA-1.
  3. Nothing appears under the final name until every file has passed. Downloads land in
     MODELS_DIR/.NAME.partial (same filesystem, so the last step is one atomic rename), a bad file
     is deleted and the run fails, and an interrupted run resumes from the stage.
  4. The lock (models/NAME.lock: file → size → sha256, plus source and revision) is written on the
     FIRST fetch, before the rename. From then on it is the trust root: a later fetch of the same
     revision (another Spark, a wiped directory, a mirror) must reproduce its file set, sizes and
     sha256s exactly — the Hub's hashes only vouch for a first fetch — and never rewrites it. A run
     re-hashes an existing directory instead of downloading, and refuses to touch one the lock does
     not describe.
  5. Crash-safe: the stage keeps its .stage.json until the rename, so an interrupted publish is
     recognised, re-verified (not re-downloaded) and completed by the next run.
Pickled weights (*.bin/*.pt/…), GGUF and original/ copies are skipped unless FETCH_INCLUDE names
them. The Hub is reached only through hfapi.py (HF_ENDPOINT, HF_TOKEN / HF_TOKEN_FILE, OFFLINE).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import hfapi  # noqa: E402
import obprofile  # noqa: E402

CHUNK = 8 * 1024 * 1024
MARKER = ".openbeast-model.json"
EXIT_MISMATCH, EXIT_ABSENT = 1, 3
STAGE_META = ".stage.json"


class FetchError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- settings

def env_file_value(path: Path, key: str) -> str:
    """spark.env grammar (parsed, never sourced); the last KEY=... wins, like lib.sh's _sp_env_value."""
    if not path or not path.is_file():
        return ""
    val = ""
    for line in path.read_text().splitlines():
        s = line.strip()
        if s.startswith(f"{key}=") or s.startswith(f"{key} ="):
            try:
                val = obprofile._unquote(s.split("=", 1)[1])
            except obprofile.ProfileError:
                val = ""
    return val


def models_dir(arg: str | None, env_path: Path | None) -> Path:
    raw = arg or os.environ.get("MODELS_DIR") or (env_file_value(env_path, "MODELS_DIR") if env_path else "")
    if not raw:
        raise FetchError("MODELS_DIR is not set — put it in scripts/backends/spark.env (e.g. MODELS_DIR=~/openbeast-models)")
    return Path(os.path.expanduser(raw)).resolve()


# --------------------------------------------------------------------------- hashing

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def hash_workers() -> int:
    """Files hashed at once. hashlib releases the GIL on large updates, so
    file-level threads scale until the disk is the limit: measured on a GB10
    (20 cores, ~8 GB/s NVMe), 1 thread ~1.8 GB/s, 4 threads ~5.5 GB/s. 4 by
    default; MODEL_FETCH_HASH_WORKERS overrides (1 = serial)."""
    raw = os.environ.get("MODEL_FETCH_HASH_WORKERS", "")
    try:
        n = int(raw) if raw else min(4, os.cpu_count() or 1)
    except ValueError:
        n = 1
    return max(1, min(n, 32))


def sha256_many(paths: list[Path]) -> dict[Path, str]:
    """sha256 of every path, hashed hash_workers() at a time; order-free result."""
    n = hash_workers()
    if n == 1 or len(paths) < 2:
        return {p: sha256_file(p) for p in paths}
    with ThreadPoolExecutor(max_workers=n) as ex:
        return dict(zip(paths, ex.map(sha256_file, paths)))


def git_blob_sha1(p: Path) -> str:
    h = hashlib.sha1(f"blob {p.stat().st_size}\0".encode())  # noqa: S324 - git's object id, not security
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


# --------------------------------------------------------------------------- selection

def _safe_rel(path: str) -> str:
    pp = PurePosixPath(path)
    if pp.is_absolute() or ".." in pp.parts or not path or path.startswith("/") or "\\" in path:
        raise FetchError(f"the Hub listed an unsafe path {path!r} — refusing the whole revision")
    return path


def select(entries: list[dict], include: list[str], exclude: list[str]) -> tuple[list[dict], list[str]]:
    chosen, skipped = [], []
    ex = obprofile.DEFAULT_EXCLUDE + list(exclude)
    for e in entries:
        path = _safe_rel(e["path"])
        name = PurePosixPath(path).name
        if include:
            ok = any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(name, g) for g in include)
        else:
            ok = not any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(name, g) for g in ex)
        if any(part.startswith(".") for part in PurePosixPath(path).parts):
            ok = False                          # .gitattributes, .github/, .eval_results/ …
        (chosen if ok else skipped).append(e if ok else path)
    if not any(e["path"] == "config.json" for e in chosen):
        raise FetchError("config.json is not in the selection — FETCH_INCLUDE/EXCLUDE removed it, or this "
                         "revision is not a transformers-style checkpoint")
    return chosen, skipped


# --------------------------------------------------------------------------- lock

def read_lock(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else {}
    except ValueError as e:
        raise FetchError(f"{path} is not valid JSON ({e}) — restore it from git or delete it deliberately") from None


def write_lock(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- artifacts

def artifacts(p: obprofile.Profile) -> list[dict]:
    out = []
    if p.is_hf:
        out.append({"key": "model", "repo": p.source, "revision": p.get("REVISION"), "dir": p.name})
    else:
        out.append({"key": "model", "local": p.source, "revision": p.get("REVISION") or None, "dir": None})
    if p.get("DRAFTER_SOURCE"):
        out.append({"key": "drafter", "repo": p.get("DRAFTER_SOURCE"), "revision": p.get("DRAFTER_REVISION"),
                    "dir": f"{p.name}.drafter"})
    return out


def _download(repo: str, rev: str, e: dict, dest: Path) -> None:
    """One file into dest, resuming a partial; verified by the caller."""
    size = int(e.get("size") or 0)
    have = dest.stat().st_size if dest.exists() else 0
    if have > size:
        dest.unlink()
        have = 0
    if have == size and size > 0:
        return
    mode = "ab" if have else "wb"
    rng = (have, None) if have else None
    tries = 0
    while True:
        try:
            with hfapi.open_file(repo, rev, e["path"], rng, timeout=300) as r:
                if rng and getattr(r, "status", 206) == 200:
                    mode, have = "wb", 0          # server ignored Range: start over
                with open(dest, mode) as f:
                    while True:
                        b = r.read(CHUNK)
                        if not b:
                            break
                        f.write(b)
            return
        except (hfapi.HubError, OSError) as err:
            tries += 1
            if tries >= 3 or isinstance(err, hfapi.Offline):
                raise FetchError(f"{e['path']}: {err}") from None
            time.sleep(2 * tries)
            have = dest.stat().st_size if dest.exists() else 0
            mode, rng = ("ab", (have, None)) if have else ("wb", None)


def _verify_entry(p: Path, e: dict) -> str:
    """Hash p against the Hub's id for it; returns the local sha256. Raises on mismatch."""
    size = int(e.get("size") or 0)
    if p.stat().st_size != size:
        raise FetchError(f"{e['path']}: {p.stat().st_size} bytes, the Hub says {size}")
    local = sha256_file(p)
    lfs = (e.get("lfs") or {}).get("oid") or (e.get("lfs") or {}).get("sha256")
    if lfs:
        if local != lfs:
            raise FetchError(f"{e['path']}: sha256 {local} does not match the Hub's LFS oid {lfs}")
    else:
        oid = e.get("oid")
        if not oid:
            raise FetchError(f"{e['path']}: the Hub gave no hash to verify against")
        got = git_blob_sha1(p)
        if got != oid:
            raise FetchError(f"{e['path']}: git blob id {got} does not match the Hub's {oid}")
    return local


def fetch_artifact(a: dict, mdir: Path, include: list[str], exclude: list[str], pinned: dict | None = None) -> dict:
    """Download + verify into the stage. With `pinned` (the lock's entry for this same source+revision)
    the LOCK is the trust root: the file set, every size and every sha256 must equal it — the Hub's own
    hashes only vouch for a first fetch, and a mirror (or a re-pointed Hub) can serve different bytes
    with self-consistent hashes."""
    repo, rev, dirname = a["repo"], a["revision"], a["dir"]
    final, stage = mdir / dirname, mdir / f".{dirname}.partial"
    if final.exists():
        raise FetchError(f"{final} exists but the lock does not describe it at {rev} — move it aside "
                         "(never overwritten)")
    log(f"[{a['key']}] listing {repo}@{rev}")
    info = hfapi.revision_info(repo, rev)
    if info.get("sha") and info["sha"] != rev:
        raise FetchError(f"asked the Hub for {rev}, it answered for {info['sha']}")
    entries, skipped = select(hfapi.tree(repo, rev), include, exclude)
    big = [e for e in entries if int(e.get("size") or 0) > 10 * 1024 * 1024 and not (e.get("lfs") or {}).get("oid")]
    if big:
        raise FetchError(f"{big[0]['path']} is large but has no LFS sha256 to verify against — refusing")
    if pinned:
        want = pinned.get("files") or {}
        have_paths = {e["path"] for e in entries}
        if have_paths != set(want):
            extra, gone = sorted(have_paths - set(want)), sorted(set(want) - have_paths)
            raise FetchError(f"the Hub's files at {rev} differ from the lock (new: {extra[:3]}, missing: "
                             f"{gone[:3]}) — the lock is the pin; refusing")
        bad = [e["path"] for e in entries if int(e.get("size") or 0) != int(want[e["path"]]["size"])]
        if bad:
            raise FetchError(f"{bad[0]}: the Hub now lists a different size than the lock pins — refusing "
                             "(the lock is the pin, not the Hub)")
    if skipped:
        log(f"[{a['key']}] skipping {len(skipped)} file(s): {', '.join(skipped[:6])}{' …' if len(skipped) > 6 else ''}")
    stage_meta = stage / STAGE_META
    if stage.exists():
        try:
            m = json.loads(stage_meta.read_text())
        except (OSError, ValueError):
            m = {}
        if m.get("repo") != repo or m.get("revision") != rev:
            raise FetchError(f"{stage} holds a different download ({m.get('repo')}@{m.get('revision')}) — "
                             "delete it to start over")
        log(f"[{a['key']}] resuming from {stage}")
    else:
        stage.mkdir(parents=True)
        stage_meta.write_text(json.dumps({"repo": repo, "revision": rev}))
    total = sum(int(e.get("size") or 0) for e in entries)
    have = sum((stage / e["path"]).stat().st_size for e in entries if (stage / e["path"]).is_file())
    free = shutil.disk_usage(mdir).free
    need = total - have
    if need + 10**9 > free:
        raise FetchError(f"not enough space in {mdir}: need {need / 1e9:.1f} GB (+1 GB margin), "
                         f"{free / 1e9:.1f} GB free")
    log(f"[{a['key']}] {len(entries)} file(s), {total / 1e9:.2f} GB ({need / 1e9:.2f} GB to download)")
    files = {}
    for e in sorted(entries, key=lambda x: x["path"]):
        dest = stage / e["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        _download(repo, rev, e, dest)
        try:
            local = _verify_entry(dest, e)
            if pinned and local != pinned["files"][e["path"]]["sha256"]:
                raise FetchError(f"{e['path']}: sha256 {local} is not the one the lock pins "
                                 f"({pinned['files'][e['path']]['sha256']}) — refusing")
        except FetchError:
            dest.unlink(missing_ok=True)
            raise
        files[e["path"]] = {"size": int(e.get("size") or 0), "sha256": local,
                            "hub_oid": (e.get("lfs") or {}).get("oid") or e.get("oid"),
                            "lfs": bool((e.get("lfs") or {}).get("oid"))}
        log(f"  ok {e['path']}")
    (stage / MARKER).write_text(json.dumps({"source": repo, "revision": rev, "files": len(files)}, indent=1))
    # .stage.json stays until the directory is published: a run interrupted between here and the
    # rename finds a stage it recognises, re-verifies it (nothing is downloaded again) and publishes.
    return {"source": repo, "revision": rev, "dir": dirname, "files": files, "_stage": str(stage),
            "_final": str(final)}


# What --locate leaves to a size check: the big weight files. Everything else (config.json,
# tokenizer files, chat_template.jinja, *.py) is small and is what changes behaviour on a
# same-size edit, so it is hashed even on the fast path.
WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".pth", ".gguf", ".pkl", ".ckpt", ".h5", ".msgpack", ".npz")


def verify_dir(d: Path, entry: dict, full: bool = True) -> list[str]:
    """full=False (--locate): sizes for the weight files, sha256 for everything else."""
    problems = []
    if not d.is_dir():
        return [f"{d} does not exist"]
    marker = d / MARKER
    if entry.get("revision") and marker.is_file():
        try:
            m = json.loads(marker.read_text())
            if m.get("revision") != entry["revision"]:
                problems.append(f"{d} was fetched at {m.get('revision')}, the lock says {entry['revision']}")
        except ValueError:
            problems.append(f"{marker} is not valid JSON")
    per_file: dict[str, str] = {}
    to_hash: dict[Path, tuple[str, str]] = {}
    for rel, f in sorted((entry.get("files") or {}).items()):
        p = d / rel
        if not p.is_file():
            per_file[rel] = f"missing {rel}"
            continue
        if p.stat().st_size != f["size"]:
            per_file[rel] = f"{rel}: {p.stat().st_size} bytes, locked {f['size']}"
            continue
        if full or not rel.endswith(WEIGHT_SUFFIXES):
            to_hash[p] = (rel, f["sha256"])
    # Hashed in parallel (a 164 GB tree is minutes of CPU on one thread);
    # reported in the lock's sorted order, so the output never depends on timing.
    for p, got in sha256_many(list(to_hash)).items():
        rel, want = to_hash[p]
        if got != want:
            per_file[rel] = f"{rel}: sha256 differs from the lock"
    problems += [per_file[rel] for rel in sorted(per_file)]
    locked = set(entry.get("files") or {})
    for p in d.rglob("*"):
        rel = p.relative_to(d).as_posix()
        # Hidden paths (.cache/huggingface/ download metadata a `hf download
        # --local-dir` copy carries) are skipped exactly as local_lock_entry
        # skips them when it writes the lock: no engine loads from them.
        if any(part.startswith(".") for part in PurePosixPath(rel).parts):
            continue
        # Every other file counts, whatever its suffix: engines and tokenizers also read
        # *.bin / *.pt (pickle), *.tiktoken, *.yaml, ... — an allowlist would miss one.
        if (p.is_file() or p.is_symlink()) and rel not in (MARKER, STAGE_META) and rel not in locked:
            problems.append(f"unlocked file {rel} (not from the pinned revision)")
    return problems


def local_lock_entry(d: Path) -> dict:
    paths = []
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d).as_posix()
        if p.is_file() and rel != MARKER and not any(part.startswith(".") for part in PurePosixPath(rel).parts):
            paths.append(p)
    sums = sha256_many(paths)
    files = {p.relative_to(d).as_posix(): {"size": p.stat().st_size, "sha256": sums[p], "hub_oid": None, "lfs": False}
             for p in paths}
    return {"source": str(d), "revision": None, "dir": None, "files": files}


def run(p: obprofile.Profile, mdir: Path | None, mode: str, trust_first_use: bool = False) -> int:
    lock = read_lock(p.lock_path)
    arts = lock.get("artifacts") or {}
    rc = 0
    changed = False
    for a in artifacts(p):
        entry = arts.get(a["key"]) or {}
        if a.get("local"):
            d = Path(a["local"])
            if not entry:
                # No lock = nothing to verify against. --verify and --locate (the launchers,
                # sync-weights on a worker) must say so, never record whatever bytes are there
                # and call them verified. Recording a lock is an explicit act: a plain fetch, or
                # --verify --trust-first-use.
                if mode == "locate" or (mode == "verify" and not trust_first_use):
                    log(f"[{a['key']}] local SOURCE {d} has no lock in {p.lock_path} — nothing to verify against. "
                        f"Record one where the copy is known-good (model-fetch.sh --profile {p.name}) and carry "
                        f"the lock with the repo; --verify --trust-first-use records one from these bytes")
                    rc = max(rc, EXIT_ABSENT)
                    continue
                log(f"[{a['key']}] local SOURCE {d}: recording a lock (trust on first use — nothing to compare to)")
                arts[a["key"]] = local_lock_entry(d)
                changed = True
                continue
            probs = verify_dir(d, entry, full=(mode != "locate"))
        else:
            if mdir is None:
                raise FetchError("MODELS_DIR is not set")
            d = mdir / a["dir"]
            stale = entry and (entry.get("source") != a["repo"] or entry.get("revision") != a["revision"])
            if stale:
                if mode in ("verify", "locate"):
                    log(f"[{a['key']}] the lock pins {entry.get('source')}@{entry.get('revision')}, the profile "
                        f"{a['repo']}@{a['revision']} — run model-fetch.sh --profile {p.name}")
                    rc = max(rc, EXIT_MISMATCH)
                    continue
                if d.exists():
                    raise FetchError(f"{d} holds {entry.get('revision')}; the profile now pins {a['revision']}. "
                                     f"Move {d} aside, then re-run (the old revision is never overwritten)")
                entry = {}
            if not entry or not d.exists():
                if mode in ("verify", "locate"):
                    log(f"[{a['key']}] not fetched: {d} — run model-fetch.sh --profile {p.name}")
                    rc = max(rc, EXIT_ABSENT)
                    continue
                if entry:
                    log(f"[{a['key']}] the lock pins this revision: every file must match it")
                new = fetch_artifact(a, mdir, p.fetch_include, p.fetch_exclude, pinned=entry or None)
                stage, final = Path(new.pop("_stage")), Path(new.pop("_final"))
                if not entry:                   # first fetch: the Hub's hashes become the pin
                    arts[a["key"]] = new
                    lock.update({"schema": 1, "profile": p.name, "artifacts": arts})
                    write_lock(p.lock_path, lock)
                if final.exists():
                    raise FetchError(f"{final} appeared while downloading — not overwriting it")
                os.rename(stage, final)
                (final / STAGE_META).unlink(missing_ok=True)
                log(f"[{a['key']}] verified and moved into place: {final}")
                log(f"[{a['key']}] lock: {p.lock_path}")
                if mode == "locate":
                    print(f"{a['key']}\t{final}")
                continue
            probs = verify_dir(d, entry, full=(mode != "locate"))
        if probs:
            for x in probs:
                log(f"[{a['key']}] MISMATCH {x}")
            rc = max(rc, EXIT_MISMATCH)
        else:
            what = "sizes match the lock" if mode == "locate" else "every file matches the lock"
            log(f"[{a['key']}] OK {d} — {what}")
            if mode == "locate":
                print(f"{a['key']}\t{d}")
    if changed:
        lock.update({"schema": 1, "profile": p.name, "artifacts": arts})
        write_lock(p.lock_path, lock)
        log(f"lock: {p.lock_path}")
    return rc


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--verify", action="store_true", help="re-hash against the lock; never download")
    g.add_argument("--locate", action="store_true",
                   help="print '<artifact>\\t<dir>' for locked dirs (weights by size, other files by sha256)")
    ap.add_argument("--trust-first-use", action="store_true",
                    help="with --verify: a local SOURCE with no lock gets one recorded from the bytes on disk "
                         "(otherwise --verify fails: there is nothing to verify against)")
    ap.add_argument("--models-dir", help="default: $MODELS_DIR, then MODELS_DIR in --env")
    ap.add_argument("--env", type=Path, default=HERE.parent / "spark.env")
    ap.add_argument("--profiles-dir", type=Path, default=Path(os.environ.get("OPENBEAST_PROFILES_DIR")
                                                               or obprofile.MODELS))
    a = ap.parse_args(argv)
    try:
        p = obprofile.load(a.profile, None, a.profiles_dir)
        needs_dir = p.is_hf or bool(p.get("DRAFTER_SOURCE"))
        if needs_dir and (a.locate or a.verify):
            try:
                mdir = models_dir(a.models_dir, a.env)
            except FetchError as e:
                log(f"not fetched: {e}")
                return EXIT_ABSENT
        else:
            mdir = models_dir(a.models_dir, a.env) if needs_dir else None
        if mdir is not None and not a.verify and not a.locate:
            mdir.mkdir(parents=True, exist_ok=True)
        if a.trust_first_use and not a.verify:
            log("Error: --trust-first-use only applies to --verify (a plain fetch already records a first lock)")
            return 2
        return run(p, mdir, "verify" if a.verify else "locate" if a.locate else "fetch", a.trust_first_use)
    except (obprofile.ProfileError, FetchError, hfapi.HubError) as e:
        log(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
