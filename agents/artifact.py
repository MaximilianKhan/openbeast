#!/usr/bin/env python3
"""beast-artifact store — durable URLs for model-authored HTML.

The on-disk half of beast-artifact (docs/BEAST_ARTIFACT_PLAN.md). Everything
here is pure filesystem + stdlib: no FastAPI, no network, no GPU. The server
(agents/artifact_server.py), the MCP tools and scripts/artifact.sh all sit on
top of this module, so this file is the single definition of "what an
artifact is".

Layout, under $OPENBEAST_FILES_DIR/artifacts (0700 all the way down):

    <root>/
      <id>/
        meta.json          atomically rewritten; the only mutable file
        v1/index.html      exactly the bytes the author handed over
        v1/files/app.js    supporting files at their published paths
        v2/...
      index.jsonl          append-only publish log (ts, id, n, owner, bytes)
      .lock                flock target; every mutator holds it (three
                           separate processes write this store)

Three invariants the rest of the system leans on:

  * **Versions are immutable.** A vN directory is written once and never
    rewritten. "Publishing again" means vN+1; `current` selects which one a
    bare /a/<id> serves, and rollback only moves that pointer.
  * **The stored page is never modified.** The doctype/charset/viewport
    skeleton is applied at SERVE time (`wrap_skeleton`), so what comes back
    out of the store is byte-identical to what went in — that is what makes
    the per-version sha256 meaningful.
  * **A version exists only once meta.json names it.** A vN directory with no
    meta entry is the debris of a crashed publish: unreachable, swept and
    reused by the next publish rather than wedging the id (security model
    D14). meta.json is the single source of truth for what is published.

Caps mirror Claude Code's artifact tool (CAPS below) so a page written for
one system publishes on the other.

Env:
  OPENBEAST_FILES_DIR            workspace root (default ~/openbeast-files)
  OPENBEAST_ARTIFACT_BASE_URL    public base for artifact_url()
                                 (default https://<hostname>:8446)
"""
from __future__ import annotations

import contextlib
import hashlib
import html as _html
import json
import mimetypes
import os
from contextvars import ContextVar
import re
import shutil
import socket
import tempfile
import threading
import uuid
from datetime import datetime, timezone

try:
    import fcntl                      # POSIX only; absent on Windows
except ImportError:                   # pragma: no cover - not our platform
    fcntl = None

__all__ = [
    "ArtifactError", "CAPS", "store_root", "publish", "get_meta",
    "list_artifacts", "read_file", "set_visibility", "set_description",
    "set_current", "remove", "artifact_url", "can_view", "extract_title",
    "wrap_skeleton", "set_owner_override", "reset_owner_override",
    "default_owner",
]


class ArtifactError(Exception):
    """Any publish/validation/lookup failure. Callers turn this into a 4xx
    (server) or a plain string (tool contract) — never a traceback."""


# Claude Code's caps, verbatim, so pages port both ways.
CAPS = {
    "page_bytes": 16 * 1024 * 1024,      # index.html
    "text_bytes": 16 * 1024 * 1024,      # each text supporting file
    "binary_bytes": 15 * 1024 * 1024,    # each binary supporting file
    "files": 255,                        # supporting files per version
    "version_bytes": 64 * 1024 * 1024,   # page + files, per version
    "versions": 200,                     # versions per artifact id
}

VISIBILITIES = ("private", "tailnet")

# Ids are path segments. uuid4 is what publish() mints, but a caller may pass
# a stable human id (the campaign verdict scripts do — reruns become versions
# of one page), so accept a conservative slug and reject everything else.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# A published file path: relative, forward slashes, no traversal, no dotfile
# segments, no control characters.
# Leading "_" is fine (_app.js); a leading "." is not — no dotfiles, and ".."
# can never form.
_SEG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")

# Types we bill against the (larger) text cap rather than the binary one.
_TEXT_TYPES = {
    "application/json", "application/javascript", "application/xml",
    "application/xhtml+xml", "image/svg+xml", "application/manifest+json",
}

_LOCK = threading.Lock()   # serializes version allocation within a process

# The store is written by THREE processes in the shipped stack (the artifact
# server, the MCP tool host and scripts/artifact.sh), so an in-process lock is
# not enough: two publishes that interleave their read-modify-write of
# meta.json lose a version and leave a vN directory with no meta entry, which
# wedges that id forever. Every mutator therefore takes an flock on
# <store>/.lock for the whole read-modify-write.
_LOCK_NAME = ".lock"


@contextlib.contextmanager
def _store_lock():
    """Exclusive, cross-process lock over the whole store.

    Always takes the in-process lock first (a single, fixed order, so nested
    waiters can never deadlock), then the flock. Degrades to the in-process
    lock alone if the filesystem has no working flock (some network mounts) —
    a store that cannot lock must still publish.
    """
    path = os.path.join(store_root(), _LOCK_NAME)
    with _LOCK:
        fd = None
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            fd = None
        if fd is not None and fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError:
                pass       # no locking on this filesystem; carry on
        try:
            yield
        finally:
            if fd is not None:
                if fcntl is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                try:
                    os.close(fd)
                except OSError:
                    pass


# --- paths -------------------------------------------------------------------

def _files_dir() -> str:
    return os.path.expanduser(
        os.environ.get("OPENBEAST_FILES_DIR", "").strip()
        or os.path.join(os.path.expanduser("~"), "openbeast-files"))


def store_root() -> str:
    """$OPENBEAST_FILES_DIR/artifacts, created 0700 on demand.

    Beside the per-user shards (openapi_tools.shard_for), never inside one:
    an artifact is owned by a login but shared through a URL, so it does not
    belong in a private workspace tree.
    """
    root = os.path.join(_files_dir(), "artifacts")
    if not os.path.isdir(root):
        os.makedirs(root, mode=0o700, exist_ok=True)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
    return root


def _artifact_dir(artifact_id: str) -> str:
    return os.path.join(store_root(), _check_id(artifact_id))


def _meta_path(artifact_id: str) -> str:
    return os.path.join(_artifact_dir(artifact_id), "meta.json")


def _now() -> str:
    # Microseconds, not seconds: `updated_at` is the gallery's sort key, and
    # two publishes inside one second must still order deterministically.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# --- validation --------------------------------------------------------------

def _check_id(artifact_id: str) -> str:
    aid = str(artifact_id or "").strip()
    if not _ID_RE.match(aid) or aid in (".", ".."):
        raise ArtifactError(f"invalid artifact id: {artifact_id!r}")
    return aid


def _check_file_path(path: str) -> str:
    """Normalize a published path or raise.

    Rejects absolute paths, drive letters, backslashes, `..` anywhere, dot
    segments, and the reserved name index.html (the page has its own slot).
    The result is safe to join under vN/files/.
    """
    p = str(path or "").strip()
    if not p:
        raise ArtifactError("empty file path")
    if p.startswith("/") or p.startswith("\\") or "\\" in p:
        raise ArtifactError(f"file path must be relative with / separators: {path!r}")
    if re.match(r"^[A-Za-z]:", p):
        raise ArtifactError(f"absolute file path rejected: {path!r}")
    if "\x00" in p:
        raise ArtifactError(f"invalid file path: {path!r}")
    segs = [s for s in p.split("/")]
    if any(s in ("", ".", "..") for s in segs):
        raise ArtifactError(f"path traversal rejected: {path!r}")
    for s in segs:
        if not _SEG_RE.match(s):
            raise ArtifactError(f"unsupported character in file path: {path!r}")
    norm = "/".join(segs)
    if norm.lower() == "index.html":
        raise ArtifactError(
            "index.html is the page itself — pass it as `html`, not as a file")
    if len(norm) > 512:
        raise ArtifactError(f"file path too long: {path!r}")
    return norm


def _as_bytes(value, what: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    raise ArtifactError(f"{what} must be str or bytes, got {type(value).__name__}")


def content_type_for(path: str) -> str:
    """Content type from the PUBLISHED extension. Never sniffed: a sniffing
    server turns an uploaded .txt into executable HTML."""
    if path.lower() in ("", "index.html"):
        return "text/html; charset=utf-8"
    guessed, _ = mimetypes.guess_type(path)
    if not guessed:
        return "application/octet-stream"
    if guessed.startswith("text/") or guessed in _TEXT_TYPES:
        if "charset=" not in guessed:
            return f"{guessed}; charset=utf-8"
    return guessed


def _is_text(path: str) -> bool:
    guessed, _ = mimetypes.guess_type(path)
    return bool(guessed) and (guessed.startswith("text/") or guessed in _TEXT_TYPES)


# --- meta --------------------------------------------------------------------

def _read_meta(artifact_id: str) -> dict | None:
    try:
        with open(_meta_path(artifact_id), "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, ValueError) as e:
        raise ArtifactError(f"unreadable artifact metadata: {e}")
    return meta if isinstance(meta, dict) else None


def _write_meta(artifact_id: str, meta: dict) -> None:
    """Atomic rewrite (mkstemp + os.replace), the scripts/clients.sh:154
    pattern: a crash mid-write can never leave a half-parsed meta.json."""
    d = _artifact_dir(artifact_id)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".meta.json.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, sort_keys=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, os.path.join(d, "meta.json"))
        _fsync_dir(d)      # the rename itself must survive a power cut
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _fsync_dir(path: str) -> None:
    """fsync a directory so a rename inside it is durable. Best effort: not
    every platform or filesystem allows opening a directory for fsync."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def get_meta(artifact_id: str) -> dict | None:
    """Full meta.json for an artifact, or None if it does not exist."""
    try:
        return _read_meta(artifact_id)
    except ArtifactError:
        raise
    except Exception:
        return None


def _coerce_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _version_numbers(meta) -> list[int]:
    """The version numbers meta.json claims, coerced and de-junked.

    Never raises: a record whose `versions` is the wrong shape (a dict, a
    string, a list of nulls — all of which a truncated or hand-edited file can
    produce) yields the numbers it can and drops the rest, so one bad artifact
    cannot take down a listing or a read of the others.
    """
    out = []
    if not isinstance(meta, dict):
        return out
    versions = meta.get("versions")
    if not isinstance(versions, list):
        return out
    for v in versions:
        if not isinstance(v, dict):
            continue
        try:
            n = int(v.get("n", 0))
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.append(n)
    return out


def _disk_versions(artifact_id: str) -> list[int]:
    """Version numbers actually on disk, from the vN directory names. The
    fallback when meta.json's own list is unusable."""
    out = []
    try:
        names = os.listdir(_artifact_dir(artifact_id))
    except OSError:
        return out
    for name in names:
        m = re.match(r"^v([0-9]{1,9})$", name)
        if m and int(m.group(1)) > 0:
            out.append(int(m.group(1)))
    return sorted(out)


def _resolvable_versions(artifact_id: str, meta) -> list[int]:
    """What a reader may address: meta's list, or — when that is broken — the
    highest existing version on disk."""
    nums = _version_numbers(meta)
    return nums if nums else _disk_versions(artifact_id)


def _append_log(entry: dict) -> None:
    path = os.path.join(store_root(), "index.jsonl")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # the publish log must never break a publish


# --- publish -----------------------------------------------------------------

def publish(html, *, title=None, description=None, favicon=None,
            files=None, artifact_id=None, label=None,
            visibility="private", owner=None) -> dict:
    """Write a new version and return {id, version, url, title, bytes}.

    html        str or bytes — exactly what gets stored as vN/index.html.
    files       {published_path: bytes|str} supporting files.
    artifact_id None mints a uuid4; an existing id adds a version and keeps
                the URL. An unknown-but-valid id creates that artifact (the
                campaign verdict scripts use stable ids so reruns version).
    visibility  applied ON CREATION ONLY. A republish never touches an
                existing artifact's visibility, in either direction:
                `set_visibility()` is the only path (security model D5), so a
                republish can neither silently re-share a page nor un-share
                one.
    owner       the publisher's login; immutable after creation. Republishing
                into an id owned by someone else raises — otherwise a second
                operator could take over the id, flip it to `tailnet` and
                read every earlier private version through /a/<id>/v/<n>.

    Raises ArtifactError on any cap or validation failure — nothing is
    written when it does (the version directory is created exclusively and
    removed on failure, including a failure of the meta write itself).
    """
    page = _as_bytes(html, "html")
    if visibility not in VISIBILITIES:
        raise ArtifactError(
            f"visibility must be one of {VISIBILITIES}, got {visibility!r}")
    if len(page) > CAPS["page_bytes"]:
        raise ArtifactError(
            f"page is {len(page)} bytes, over the "
            f"{CAPS['page_bytes']} byte page cap")
    if not page.strip():
        raise ArtifactError("page is empty")

    payload: dict[str, bytes] = {}
    for raw_path, value in (files or {}).items():
        p = _check_file_path(raw_path)
        if p in payload:
            raise ArtifactError(f"duplicate file path: {p}")
        data = _as_bytes(value, f"file {p!r}")
        cap = CAPS["text_bytes"] if _is_text(p) else CAPS["binary_bytes"]
        if len(data) > cap:
            kind = "text" if _is_text(p) else "binary"
            raise ArtifactError(
                f"{p} is {len(data)} bytes, over the {cap} byte {kind} cap")
        payload[p] = data
    if len(payload) > CAPS["files"]:
        raise ArtifactError(
            f"{len(payload)} supporting files, over the "
            f"{CAPS['files']} file cap")
    total = len(page) + sum(len(v) for v in payload.values())
    if total > CAPS["version_bytes"]:
        raise ArtifactError(
            f"version is {total} bytes, over the "
            f"{CAPS['version_bytes']} byte per-version cap")

    store_root()  # ensure 0700 root exists before we touch anything
    resolved_owner = (owner or "").strip().lower() or default_owner()
    with _store_lock():
        if artifact_id:
            aid = _check_id(artifact_id)
        else:
            aid = str(uuid.uuid4())
        meta = _read_meta(aid)
        new = meta is None
        if new:
            meta = {
                "id": aid,
                "owner": resolved_owner,
                "title": None,
                "description": None,
                "favicon": None,
                "visibility": visibility,
                "created_at": _now(),
                "updated_at": _now(),
                "current": 0,
                "versions": [],
            }
        else:
            # D5: republish requires ownership. The message says nothing about
            # who does own it — a probe must not learn that either.
            existing_owner = str(meta.get("owner") or "").strip().lower()
            if existing_owner and existing_owner != resolved_owner:
                raise ArtifactError("not your artifact")
        if not isinstance(meta.get("versions"), list):
            meta["versions"] = []
        if len(meta["versions"]) >= CAPS["versions"]:
            raise ArtifactError(
                f"{aid} already has {len(meta['versions'])} versions, at the "
                f"{CAPS['versions']} version cap — publish under a new id")
        n = max(_version_numbers(meta) or [0]) + 1
        vdir = os.path.join(_artifact_dir(aid), f"v{n}")
        try:
            os.makedirs(vdir, mode=0o700, exist_ok=False)
        except FileExistsError:
            # D14: a crash between mkdir/write and the meta write leaves a vN
            # directory no meta entry references. It is unreachable (read_file
            # resolves versions through meta), so it is garbage, not history —
            # sweep it and retry once rather than wedging the id forever.
            shutil.rmtree(vdir, ignore_errors=True)
            try:
                os.makedirs(vdir, mode=0o700, exist_ok=False)
            except OSError:
                raise ArtifactError(
                    f"version v{n} of {aid} already exists — refusing to "
                    "rewrite an immutable version")
        try:
            _write_bytes(os.path.join(vdir, "index.html"), page)
            for p, data in sorted(payload.items()):
                dest = os.path.join(vdir, "files", *p.split("/"))
                os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
                _write_bytes(dest, data)
        except BaseException:
            shutil.rmtree(vdir, ignore_errors=True)
            raise

        sha = hashlib.sha256(page).hexdigest()
        meta["versions"].append({
            "n": n,
            "ts": _now(),
            "label": (label or "").strip() or None,
            "sha256": sha,               # of index.html
            "bytes": total,              # page + supporting files
            "files": sorted(payload),
        })
        meta["current"] = n
        meta["updated_at"] = _now()
        if title is not None and str(title).strip():
            meta["title"] = str(title).strip()[:200]
        elif not meta.get("title"):
            meta["title"] = (extract_title(page) or "Untitled")[:200]
        if description is not None and str(description).strip():
            meta["description"] = str(description).strip()[:1000]
        if favicon is not None and str(favicon).strip():
            meta["favicon"] = str(favicon).strip()[:32]
        # No visibility change here, in either direction (D5): set_visibility()
        # is the only path, and it is owner-only.
        if meta.get("visibility") not in VISIBILITIES:
            meta["visibility"] = "private"
        try:
            _write_meta(aid, meta)
        except BaseException:
            # The version is only real once meta names it; if the meta write
            # fails, the directory must not survive to block v{n} forever.
            shutil.rmtree(vdir, ignore_errors=True)
            raise

    _append_log({"ts": _now(), "id": aid, "n": n,
                 "owner": meta.get("owner"), "bytes": total, "sha256": sha})
    return {"id": aid, "version": n, "url": artifact_url(aid),
            "title": meta.get("title"), "bytes": total}


def _write_bytes(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


# --- read --------------------------------------------------------------------

def list_artifacts(*, owner=None, viewer=None, limit=25) -> list[dict]:
    """Artifacts newest-updated first, as compact gallery rows.

    owner  restrict to one login. viewer  drop anything can_view() refuses.

    A record that is unreadable, corrupt or the wrong shape is SKIPPED, never
    raised (D14): the gallery is the one page an operator reaches for when
    something has gone wrong on disk, so one bad meta.json must not turn the
    whole listing into a server error.
    """
    root = store_root()
    rows = []
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    for name in entries:
        if not _ID_RE.match(name):
            continue
        try:
            meta = get_meta(name)
        except Exception:
            continue            # corrupt/unreadable: skip this one, not all
        if not isinstance(meta, dict) or not meta:
            continue
        try:
            if owner is not None and (meta.get("owner") or "") != owner:
                continue
            if viewer is not None and not can_view(meta, viewer):
                continue
            versions = meta.get("versions")
            versions = versions if isinstance(versions, list) else []
            last = (versions[-1] if versions and isinstance(versions[-1], dict)
                    else {})
            aid = meta.get("id") if isinstance(meta.get("id"), str) else name
            if not _ID_RE.match(aid):
                aid = name
            rows.append({
                "id": aid,
                "title": meta.get("title") or "Untitled",
                "description": meta.get("description"),
                "favicon": meta.get("favicon"),
                "owner": meta.get("owner"),
                "visibility": (meta.get("visibility")
                               if meta.get("visibility") in VISIBILITIES
                               else "private"),
                "current": _coerce_int(meta.get("current"), len(versions)),
                "versions": len(versions),
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"),
                "bytes": _coerce_int(last.get("bytes"), 0),
                "url": artifact_url(aid),
            })
        except Exception:
            continue            # any shape surprise: drop the row, keep going
    rows.sort(key=lambda r: (str(r.get("updated_at") or ""), str(r["id"])),
              reverse=True)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 25
    return rows[:limit] if limit > 0 else rows


def read_file(artifact_id, version: int, path="index.html") -> tuple[bytes, str]:
    """(bytes, content_type) for one file of one version.

    path defaults to the page. Supporting files resolve under vN/files/ and
    are re-validated here, so a crafted URL cannot walk out of the store even
    if a caller skipped validation on the way in.
    """
    aid = _check_id(artifact_id)
    meta = _read_meta(aid)
    if meta is None:
        raise ArtifactError(f"no such artifact: {aid}")
    try:
        n = int(version)
    except (TypeError, ValueError):
        raise ArtifactError(f"invalid version: {version!r}")
    # Coerced, and — if meta's own list is the wrong shape — resolved from the
    # vN directories on disk, so a damaged record still serves its pages.
    if n not in _resolvable_versions(aid, meta):
        raise ArtifactError(f"no such version: v{version}")
    rel = (path or "index.html").strip().lstrip("/")
    if rel in ("", "index.html"):
        target = os.path.join(_artifact_dir(aid), f"v{n}", "index.html")
        ctype = "text/html; charset=utf-8"
    else:
        safe = _check_file_path(rel)
        base = os.path.realpath(os.path.join(_artifact_dir(aid), f"v{n}", "files"))
        target = os.path.realpath(os.path.join(base, *safe.split("/")))
        if target != base and not target.startswith(base + os.sep):
            raise ArtifactError(f"path escapes the artifact: {path!r}")
        ctype = content_type_for(safe)
    try:
        with open(target, "rb") as fh:
            return fh.read(), ctype
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        raise ArtifactError(f"no such file in v{n}: {rel}")
    except OSError as e:
        raise ArtifactError(f"unreadable file: {e}")


# --- mutate (metadata only; versions stay immutable) -------------------------

def set_visibility(artifact_id, visibility, *, owner=None) -> dict:
    """The ONLY way an artifact's visibility changes (D5), and owner-only.

    `owner` defaults to the resolved caller (`default_owner()`: the identity
    the server put in the ContextVar, else the rig's first operator, else
    "local"). Sharing someone else's private page is exactly the escalation
    the republish hole gave away, so it is refused here too.
    """
    if visibility not in VISIBILITIES:
        raise ArtifactError(
            f"visibility must be one of {VISIBILITIES}, got {visibility!r}")
    aid = _check_id(artifact_id)
    who = str(owner or "").strip().lower() or default_owner()
    with _store_lock():
        meta = _read_meta(aid)
        if meta is None:
            raise ArtifactError(f"no such artifact: {artifact_id}")
        existing_owner = str(meta.get("owner") or "").strip().lower()
        if existing_owner and existing_owner != who:
            raise ArtifactError("not your artifact")
        meta["visibility"] = visibility
        meta["updated_at"] = _now()
        _write_meta(aid, meta)
    return meta


def set_description(artifact_id, description) -> dict:
    """Gallery subtitle. Metadata only — the stored pages are untouched."""
    aid = _check_id(artifact_id)
    with _store_lock():
        meta = _read_meta(aid)
        if meta is None:
            raise ArtifactError(f"no such artifact: {artifact_id}")
        meta["description"] = (str(description).strip()[:1000]
                               if description is not None else None) or None
        meta["updated_at"] = _now()
        _write_meta(aid, meta)
    return meta


def set_current(artifact_id, version) -> dict:
    """Rollback: move the `current` pointer. Every version stays on disk and
    stays reachable at /a/<id>/v/<n>."""
    aid = _check_id(artifact_id)
    with _store_lock():
        meta = _read_meta(aid)
        if meta is None:
            raise ArtifactError(f"no such artifact: {artifact_id}")
        try:
            n = int(version)
        except (TypeError, ValueError):
            raise ArtifactError(f"invalid version: {version!r}")
        if n not in _resolvable_versions(aid, meta):
            raise ArtifactError(f"no such version: v{version}")
        meta["current"] = n
        meta["updated_at"] = _now()
        _write_meta(aid, meta)
    return meta


def remove(artifact_id) -> bool:
    """Delete an artifact and every version. True if something was removed."""
    aid = _check_id(artifact_id)
    d = _artifact_dir(aid)
    root = os.path.realpath(store_root())
    real = os.path.realpath(d)
    if not real.startswith(root + os.sep):
        raise ArtifactError(f"refusing to remove outside the store: {aid}")
    if not os.path.isdir(real):
        return False
    shutil.rmtree(real)
    _append_log({"ts": _now(), "id": aid, "n": None, "owner": None,
                 "bytes": 0, "event": "remove"})
    return True


# --- urls / visibility -------------------------------------------------------

def artifact_url(artifact_id, version=None) -> str:
    """The durable URL. Base from $OPENBEAST_ARTIFACT_BASE_URL, else
    https://<hostname>:8446 (the tailscale-serve mount from the plan)."""
    base = os.environ.get("OPENBEAST_ARTIFACT_BASE_URL", "").strip()
    if not base:
        host = socket.gethostname() or "localhost"
        base = f"https://{host}:8446"
    base = base.rstrip("/")
    aid = _check_id(artifact_id)
    if version is None:
        return f"{base}/a/{aid}"
    return f"{base}/a/{aid}/v/{int(version)}"


_OWNER_OVERRIDE: ContextVar = ContextVar("openbeast_artifact_owner", default=None)


def set_owner_override(login):
    """Attribute publishes in this context to `login`.

    The identity server knows who is calling; `mcp_server`'s tool functions
    never see the request. Same shape as `tools.set_base_dir_override`:
    the server sets this around the call and resets it after. Returns a
    token for `reset_owner_override()`.
    """
    return _OWNER_OVERRIDE.set((login or "").strip().lower() or None)


def reset_owner_override(token):
    try:
        _OWNER_OVERRIDE.reset(token)
    except Exception:
        pass


def default_owner() -> str:
    """Who owns a publish that named no owner. NEVER None (D3).

    Order: the identity the server put in the ContextVar, else the rig's first
    artifact operator, else its first chat operator, else the literal "local".

    An ownerless artifact used to mean "readable by every operator forever",
    so a CLI or campaign publish on an unconfigured rig silently shared itself
    with the whole tailnet. "local" is a real principal instead: the rig's own
    processes publish as it, and `can_view` treats it like any other owner.
    """
    who = _OWNER_OVERRIDE.get()
    if who:
        return who
    for var in ("OPENBEAST_ARTIFACT_OPERATORS", "OPENBEAST_CHAT_OPERATORS"):
        for part in (os.environ.get(var) or "").split(","):
            part = part.strip().lower()
            if part:
                return part
    return "local"


def can_view(meta, viewer_login) -> bool:
    """Read permission for one artifact. Fails CLOSED (D2).

    True for anything marked `tailnet`, and otherwise only for the owner. An
    ANONYMOUS viewer (`viewer_login is None`) never reads a non-tailnet page:
    the old "no identity configured => single-user rig" branch handed every
    private artifact to any caller who simply omitted the login header.

    A legacy artifact with no owner at all is still readable, but only by an
    IDENTIFIED caller — and the server (D1) gives an anonymous request a 404
    before it ever gets here, so `viewer_login` is never None on a real read.
    """
    if not isinstance(meta, dict):
        return False
    if meta.get("visibility") == "tailnet":
        return True
    owner = str(meta.get("owner") or "").strip()
    if viewer_login is None:
        return False         # NEVER open to anonymous
    if not owner:
        return True          # legacy/unowned: an identified caller may read
    return str(viewer_login).strip().lower() == owner.lower()


# --- html helpers ------------------------------------------------------------

_TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)

# Everything a real document may carry BEFORE its <html> tag: a UTF-8 BOM,
# whitespace, an XML declaration, comments (a licence header is the common
# case) — and, once, a doctype.
_LEAD_RE = re.compile(rb"\xef\xbb\xbf|\s+|<\?xml\b[^>]*\?>|<!--.*?-->", re.S)
_DOCTYPE_RE = re.compile(rb"<!doctype\b[^>]*>", re.I)
_HTML_TAG_RE = re.compile(rb"<html[\s>/]", re.I)


def _skip_lead(data: bytes, pos: int) -> int:
    while True:
        m = _LEAD_RE.match(data, pos)
        if not m or m.end() == pos:
            return pos
        pos = m.end()


def _html_tag_at(data: bytes) -> int:
    """Offset of the document's own <html> tag, or -1 if these bytes are a
    fragment (D19).

    A doctype alone is NOT a document: pages arrive from models with a stray
    `<!doctype html>` on top of a bare `<p>`, and passing those through cost
    them the charset/viewport skeleton. Equally, a document whose first bytes
    are a BOM or a comment IS a document and must not be wrapped a second time
    — the old anchored match got both cases wrong.
    """
    pos = _skip_lead(data, 0)
    m = _DOCTYPE_RE.match(data, pos)
    if m:
        pos = _skip_lead(data, m.end())
    return pos if _HTML_TAG_RE.match(data, pos) else -1


def extract_title(html) -> str | None:
    """The page's <title>, or None.

    Only the first 8 KB is scanned — the same window Claude Code documents,
    so a title buried past it is "no title" on both systems and we never
    walk a 16 MB page looking for one.
    """
    data = (html.encode("utf-8", "replace") if isinstance(html, str)
            else bytes(html or b""))
    m = _TITLE_RE.search(data[:8192])
    if not m:
        return None
    text = _html.unescape(m.group(1).decode("utf-8", "replace"))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def wrap_skeleton(body_html, *, theme=None) -> bytes:
    """Wrap an authored page fragment in the serve-time skeleton.

    Applied on the way OUT, never stored: the version's sha256 is over the
    author's own bytes, and changing this skeleton re-renders every artifact
    ever published without rewriting a single file.

    A page that already carries its own <html> tag is passed through untouched
    (beyond the optional data-theme stamp) — hand-built pages like
    scratch/spare-memory-meta.html predate the tool and must still render —
    even if a BOM, a licence comment or an XML declaration comes first.
    """
    data = (body_html.encode("utf-8") if isinstance(body_html, str)
            else bytes(body_html or b""))
    stamp = ""
    if theme in ("dark", "light"):
        stamp = f' data-theme="{theme}"'
    at = _html_tag_at(data)
    if at >= 0:
        if stamp and b"data-theme" not in data[:2048]:
            # Stamp THE document's tag, found above — never a "<html" that
            # happens to sit inside the leading comment.
            data = data[:at + 5] + stamp.encode() + data[at + 5:]
        return data
    head = (
        "<!doctype html>\n"
        f"<html lang=\"en\"{stamp}>\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<style>\n"
        ":root{color-scheme:light dark}\n"
        "body{margin:0;font:14px system-ui,-apple-system,Segoe UI,Roboto,sans-serif}\n"
        "img{max-width:100%}\n"
        "[hidden]{display:none!important}\n"
        "</style>\n"
        "</head>\n<body>\n"
    ).encode("utf-8")
    return head + data + b"\n</body>\n</html>\n"
