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
      .locks/<id>.lock     one flock target PER ARTIFACT; every mutator of
                           that id holds it (three separate processes write
                           this store). Never store-wide: one exclusive lock
                           over everything, held across a 64 MB fsync, made
                           the health endpoint and every page view wait on a
                           publish (security model D25). Read paths take no
                           lock at all.

Three invariants the rest of the system leans on:

  * **Versions are immutable.** A vN directory is written once and never
    rewritten. "Publishing again" means vN+1; `current` selects which one a
    bare /a/<id> serves, and rollback only moves that pointer.
  * **The stored page is never modified.** The doctype/charset/viewport
    skeleton is applied at SERVE time (`wrap_skeleton`), so what comes back
    out of the store is byte-identical to what went in — that is what makes
    the per-version sha256 meaningful.
  * **A version exists only once meta.json names it.** A vN directory with no
    meta entry is the debris of a crashed publish: unreachable, and STEPPED
    OVER by the next publish rather than wedging the id (D14) — never deleted,
    because "this directory is debris" is a judgement a corrupt meta.json can
    make wrongly, and the version it deletes is the only copy (R4).

Caps mirror Claude Code's artifact tool (CAPS below) so a page written for
one system publishes on the other.

Env:
  OPENBEAST_FILES_DIR            workspace root (default ~/openbeast-files)
  OPENBEAST_ARTIFACT_BASE_URL    public base for artifact_url()
                                 (default https://<hostname>:8446)
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import html as _html
import json
import mimetypes
import os
from contextvars import ContextVar
import re
import shutil
import socket
import stat as _stat
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone

try:
    import fcntl                      # POSIX only; absent on Windows
except ImportError:                   # pragma: no cover - not our platform
    fcntl = None

__all__ = [
    "ArtifactError", "CAPS", "store_root", "is_store_path", "publish",
    "get_meta", "list_artifacts", "read_file", "set_visibility",
    "set_description", "set_current", "remove", "artifact_url", "can_view",
    "extract_title", "wrap_skeleton", "set_owner_override",
    "reset_owner_override", "default_owner", "default_owner_alias",
    "valid_email",
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

# Ids that collide with the store's own files or the server's own routes (R7).
# `index.jsonl` is the ledger: <root>/index.jsonl is a FILE, so publishing to
# that id made os.makedirs raise NotADirectoryError — an OSError no handler
# caught, i.e. an HTTP 500 handed to the page's own owner. `health` is the
# server's one unauthenticated route. Compared lowercased: refusing
# `INDEX.JSONL` costs nothing and a case-insensitive filesystem collides too.
_RESERVED_IDS = frozenset({"index.jsonl", "health"})

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

# The store is written by THREE processes in the shipped stack (the artifact
# server, the MCP tool host and scripts/artifact.sh), so an in-process lock is
# not enough: two publishes that interleave their read-modify-write of
# meta.json lose a version and leave a vN directory with no meta entry, which
# wedges that id forever.
#
# The lock is therefore an flock — but PER ARTIFACT, non-blocking, and
# bounded (D25). Round one took one exclusive flock over the whole store with
# no timeout and held it across fsyncs of up to 64 MB, on sync handlers that
# occupy anyio's bounded threadpool: with it held, /api/artifacts/health and
# /a/<id> both timed out at 8 s, which blinds doctor.sh and healthcheck.sh.
# Publishing into page A now contends only with page A, a caller that cannot
# get in raises a named error instead of hanging, and no read path locks at
# all (read_file, get_meta and list_artifacts are lock-free by construction —
# versions are immutable and meta.json is replaced atomically).
#
# The lock files live in <store>/.locks/, NOT inside the artifact directory:
# remove() rmtree's that directory, and a lock held on an unlinked inode
# excludes nobody. A hard kill cannot wedge anything either — the kernel drops
# an flock when the process dies, and the leftover file is just an empty file.
_LOCKS_DIR = ".locks"
_LOCK_DEFAULT_TIMEOUT = 10.0        # seconds, per mutation; env-overridable

# Per-id in-process locks, so threads in ONE process queue on the mutex rather
# than burning the flock retry budget against themselves (flock conflicts even
# between two file descriptions in the same process).
_LOCKS_MUTEX = threading.Lock()
_ID_LOCKS: dict[str, threading.Lock] = {}
_HELD = threading.local()           # ids this thread already holds: re-entrant


def _lock_timeout() -> float:
    raw = (os.environ.get("OPENBEAST_ARTIFACT_LOCK_TIMEOUT") or "").strip()
    try:
        val = float(raw)
    except ValueError:
        return _LOCK_DEFAULT_TIMEOUT
    return val if val > 0 else _LOCK_DEFAULT_TIMEOUT


def _busy(aid: str, waited: float) -> "ArtifactError":
    return ArtifactError(
        f"artifact {aid} is busy: another process is publishing to it "
        f"(waited {waited:.1f}s). Try again.")


def _id_mutex(aid: str) -> threading.Lock:
    with _LOCKS_MUTEX:
        lk = _ID_LOCKS.get(aid)
        if lk is None:
            lk = _ID_LOCKS[aid] = threading.Lock()
        return lk


def _lock_path(aid: str):
    d = os.path.join(store_root(), _LOCKS_DIR)
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
    except OSError:
        return None
    return os.path.join(d, f"{aid}.lock")


def _flock_nb(path: str, aid: str, deadline: float):
    """LOCK_EX|LOCK_NB with a bounded retry. Returns the fd (locked, or
    unlocked on a filesystem with no working flock), or raises ArtifactError.

    Never LOCK_EX-blocking: an unbounded wait here is what took health down.
    """
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None            # cannot even open it: in-process lock only
    delay = 0.005
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as e:
            if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES,
                               errno.EINTR):
                return fd      # no locking on this filesystem; carry on
            left = deadline - time.monotonic()
            if left <= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise _busy(aid, _lock_timeout())
            time.sleep(min(delay, left))
            delay = min(delay * 2, 0.05)


@contextlib.contextmanager
def _artifact_lock(artifact_id: str):
    """Exclusive, cross-process lock over ONE artifact id (D25).

    Bounded: a caller that cannot get in within OPENBEAST_ARTIFACT_LOCK_TIMEOUT
    seconds (default 10) raises ArtifactError rather than hanging forever.
    Re-entrant per thread, and always in-process-mutex-then-flock, one fixed
    order, so nested or concurrent mutators cannot deadlock.
    """
    aid = _check_id(artifact_id)
    held = getattr(_HELD, "ids", None)
    if held is None:
        held = _HELD.ids = set()
    if aid in held:
        yield                                  # already ours; do not re-lock
        return
    timeout = _lock_timeout()
    deadline = time.monotonic() + timeout
    mutex = _id_mutex(aid)
    if not mutex.acquire(timeout=timeout):
        raise _busy(aid, timeout)
    fd = None
    try:
        path = _lock_path(aid)
        if path is not None and fcntl is not None:
            fd = _flock_nb(path, aid, deadline)
        held.add(aid)
        try:
            yield
        finally:
            held.discard(aid)
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
        mutex.release()


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


def is_store_path(path) -> bool:
    """True when `path` IS the artifact store, or anything inside it (D26).

    The tool surface refuses to publish a page from outside the caller's
    workspace — but the store lives *inside* that workspace whenever no
    per-user shard is set (FILES_SHARDING=off, and the MCP stdio surface), so
    the workspace check alone let a second user publish another user's private
    page by naming the store's own internal path (…/artifacts/<id>/v1/
    index.html) and then re-share it as `tailnet`. Callers reading a file to
    publish must refuse anything this returns True for.

    Correct for the four shapes that beat a string comparison:
      * a relative path (resolved against the working directory first),
      * a symlinked parent — both sides are realpath'd, so a symlink ANYWHERE
        on either path still lands on the same real directory,
      * a path that resolves into the store from outside it (a symlink in the
        workspace pointing at …/artifacts, the classic bypass),
      * a HARDLINK to a page inside the store (R3). realpath resolves symlinks;
        a hardlink has nothing to resolve, so `ln <store>/<id>/v1/index.html
        loot.html` named the store's own bytes from a path comfortably outside
        it — a reviewer published another user's private page that way and
        re-shared it as tailnet. A second link to the same inode is therefore
        refused outright: the page a caller just wrote with write_file always
        has exactly one, so this costs an honest publisher nothing, and "how
        many links" is the only question that can be asked of a file WITHOUT
        walking the whole store on every publish.
    A path that does not exist is still judged: realpath resolves the part
    that does, which is what makes "publish into the store" refusable before
    the file is ever opened.
    """
    raw = str(path or "").strip()
    if not raw:
        return False
    try:
        target = os.path.realpath(os.path.abspath(os.path.expanduser(raw)))
        root = os.path.realpath(store_root())
    except (OSError, ValueError):
        return False
    if target == root or target.startswith(root + os.sep):
        return True
    try:
        st = os.stat(target)
    except (OSError, ValueError):
        return False              # absent, unreadable: not the store
    return _stat.S_ISREG(st.st_mode) and st.st_nlink > 1


def _artifact_dir(artifact_id: str) -> str:
    return os.path.join(store_root(), _check_id(artifact_id))


def _meta_path(artifact_id: str) -> str:
    return os.path.join(_artifact_dir(artifact_id), "meta.json")


def _now() -> str:
    # Microseconds, not seconds: `updated_at` is the gallery's sort key, and
    # two publishes inside one second must still order deterministically.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# --- validation --------------------------------------------------------------

def _valid_id(name: str) -> bool:
    """Is `name` usable as an artifact id — and as a directory beside the
    store's own files (R7)?"""
    return (bool(_ID_RE.match(name)) and name not in (".", "..")
            and name.lower() not in _RESERVED_IDS)


def _check_id(artifact_id: str) -> str:
    aid = artifact_id.strip() if isinstance(artifact_id, str) else ""
    if not _valid_id(aid):
        raise ArtifactError(f"invalid artifact id: {artifact_id!r}")
    return aid


def _norm_login(value) -> str:
    """One spelling for an identity: stripped, lowercased. Owners are stored
    this way, so every comparison in this module goes through here.

    A non-string is NOT an identity (R1). This used to be `str(value or "")`,
    so a claim that arrived as a list became the owner `"['max@example.com']"`
    — a principal nobody can ever present, on a page nobody can ever manage.
    """
    return value.strip().lower() if isinstance(value, str) else ""


# A login a reader can actually present (R1). Deliberately not RFC 5322: this
# is the shape an identity provider hands over — one `@`, a non-empty local
# part, a domain of ordinary labels. Single-label domains are allowed because
# real tailnet logins have them (`max@github`, `max@passkey`).
_EMAIL_RE = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$")


def valid_email(value) -> str:
    """`value` as a normalized login, or "" when it is not a plausible one.

    The single definition of "an identity a reader can present", for every
    surface that turns a forwarded header or a JWT claim into an owner (R1).
    The test it replaces was `"@" in login`, which minted the owner `'@'` from
    a bare at-sign and stringified a non-string claim into nonsense. An owner
    nobody can present is a tombstone: unreadable AND unmanageable.
    """
    login = _norm_login(value)
    if not login or len(login) > 254:
        return ""
    local, sep, domain = login.partition("@")
    if not sep or not local or not domain or len(local) > 64:
        return ""
    return login if _EMAIL_RE.match(login) else ""


def _owner_of(meta) -> str:
    """The ONE identity that owns this artifact: meta["owner"], normalized.

    There used to be two (R6). `owner_webui_id`, the publishing surface's own
    id, was flattened into the same set as the login a reader presents — so
    the id AUTHENTICATED: on a rig with no operator allowlist a stranger who
    presented the UUID as their login read the private page. It is pure
    provenance now, consulted by nothing here and by no guard anywhere.
    """
    return _norm_login(meta.get("owner")) if isinstance(meta, dict) else ""


def _require_owner(meta, owner) -> str:
    """The ownership guard every mutator shares (D5/D22).

    `owner` is who is asking (the server passes the resolved principal);
    absent, the resolved caller. An artifact with no recorded identity at all
    is legacy and stays mutable — everything else is owner-only, and the
    message says nothing about who the owner is, so a probe learns nothing.
    """
    who = _norm_login(owner) or default_owner()
    known = _owner_of(meta)
    if known and who != known:
        raise ArtifactError("not your artifact")
    return who


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
            visibility="private", owner=None, owner_alias=None) -> dict:
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
    owner       an ASSERTION, not an identity, and it is IGNORED unless it
                matches the resolved caller (D28). Attribution comes from
                `default_owner()` — the identity the server put in the
                ContextVar, else the rig's first operator, else "local".
                Round one deleted the `owner` field from the HTTP body but
                left this kwarg, so every in-process caller kept a primitive
                that could publish a page under anyone's name. Ownership is
                immutable after creation: republishing into an id owned by
                someone else raises — otherwise a second operator could take
                over the id, flip it to `tailnet` and read every earlier
                private version through /a/<id>/v/<n>.
    owner_alias IGNORED unless it is the resolved caller's own alias (R6), and
                therefore never a way to plant a page in a third party's name.
                The caller's alias — its identity in its OWN namespace, the
                raw Open WebUI user id — comes from the context the server
                set, and is recorded once, at creation, as
                meta["owner_webui_id"]. That field is PURE PROVENANCE: no
                guard here consults it and can_view() does not know it exists.
                It used to be an alias for the owner, which made it a
                credential (a stranger presenting the UUID as their login read
                the page) and made this kwarg an attribution-forging primitive.

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
    # D28: `owner=` never names the publisher. The resolved caller does, and a
    # kwarg that disagrees with it is ignored rather than honoured (the server
    # passes the principal it already resolved, so agreement is the norm).
    resolved_owner = default_owner()
    # R6: the same shape as `owner=` above — an alias that disagrees with the
    # resolved caller's own is ignored, not honoured. Provenance may only ever
    # record who actually published.
    alias = default_owner_alias()
    # The id is minted BEFORE the lock because the lock is per-artifact now
    # (D25): a publish into page A must not make page B, health or any read
    # wait on it.
    aid = _check_id(artifact_id) if artifact_id else str(uuid.uuid4())
    with _artifact_lock(aid):
        meta = _read_meta(aid)
        new = meta is None
        if new:
            meta = {
                "id": aid,
                "owner": resolved_owner,
                "owner_webui_id": alias or None,
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
            # D5: republish requires ownership, against meta["owner"] alone
            # (R6). The message says nothing about who does own it — a probe
            # must not learn that either.
            #
            # KNOWN, DELIBERATE, and the one mutator R2 did not de-load: this
            # check authorizes off `default_owner()` — the ContextVar — while
            # description/current/visibility/remove take an explicit `owner=`
            # from the server. A ship check confirmed that removing the
            # ContextVar lets a second operator add a version to someone
            # else's page (blind deface; visibility is untouched, so they
            # still cannot read it, and it needs >=2 operators plus shell
            # access to the rig).
            #
            # Honouring `owner=` here instead was tried and REVERTED: on a
            # republish it would let any in-process caller assert their way
            # past this guard, which is exactly the forging primitive D28
            # closed, and test_publish_owner_kwarg_cannot_forge_attribution
            # catches it. Fixing it properly means giving publish a way to be
            # told the principal that cannot also be used to claim one —
            # a real change, not a patch, and not one to make at the end of
            # three rounds of churn in this file.
            _require_owner(meta, resolved_owner)
            # Backfill provenance, never rewrite it: the alias identifies the
            # creator, and a later publisher must not overwrite whose it was.
            if alias and not _norm_login(meta.get("owner_webui_id")):
                meta["owner_webui_id"] = alias
        if not isinstance(meta.get("versions"), list):
            meta["versions"] = []
        if len(meta["versions"]) >= CAPS["versions"]:
            raise ArtifactError(
                f"{aid} already has {len(meta['versions'])} versions, at the "
                f"{CAPS['versions']} version cap — publish under a new id")
        # R4: the number a READER would resolve next, and clear of every vN
        # directory that already exists.
        #
        # It used to come from meta's list alone while every reader resolves
        # through _resolvable_versions() (meta, falling back to disk). With a
        # version list corrupted into non-numeric entries — an ordinary
        # corruption, one the suite below exercises — n computed to 1, and the
        # orphan sweep that used to live here rmtree'd the REAL v1 as debris;
        # v2 then fell out of the resolver and became unreachable. Silent and
        # irreversible, so the sweep is GONE: publish never deletes a version
        # directory it did not itself create this call. Debris from a crashed
        # publish (D14) is stepped over instead of destroyed — it stays
        # unreachable, meta names it nowhere, and the id is not wedged, which
        # is all D14 ever asked for.
        n = max(_resolvable_versions(aid, meta) + _disk_versions(aid) or [0]) + 1
        vdir = os.path.join(_artifact_dir(aid), f"v{n}")
        try:
            os.makedirs(vdir, mode=0o700, exist_ok=False)
        except OSError as e:
            # R7: every failure here is an ArtifactError the caller turns into
            # a 4xx. A reserved id used to reach this line as a bare
            # NotADirectoryError (<root>/index.jsonl is a file) — neither
            # `except FileExistsError` nor `except ArtifactError` caught it, so
            # the page's own owner got a 500.
            raise ArtifactError(f"cannot create version v{n} of {aid}: {e}")
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
        if not _valid_id(name):
            continue        # index.jsonl, .locks, junk: not artifacts (R7)
        try:
            meta = get_meta(name)
        except Exception:
            continue            # corrupt/unreadable: skip this one, not all
        if not isinstance(meta, dict) or not meta:
            continue
        try:
            if owner is not None:
                # D29: owners are STORED lowercased, so a case-sensitive
                # compare here dropped every row for a caller who spelled
                # their own login the way their identity provider does
                # (Max@Example.com). meta["owner"] alone (R6).
                want = _norm_login(owner)
                have = _owner_of(meta)
                if want:
                    if want != have:
                        continue
                elif have:
                    continue        # owner="" asks for the unowned records
            if viewer is not None and not can_view(meta, viewer):
                continue
            versions = meta.get("versions")
            versions = versions if isinstance(versions, list) else []
            last = (versions[-1] if versions and isinstance(versions[-1], dict)
                    else {})
            aid = meta.get("id") if isinstance(meta.get("id"), str) else name
            if not _valid_id(aid):
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
    with _artifact_lock(aid):
        meta = _read_meta(aid)
        if meta is None:
            raise ArtifactError(f"no such artifact: {artifact_id}")
        _require_owner(meta, owner)
        meta["visibility"] = visibility
        meta["updated_at"] = _now()
        _write_meta(aid, meta)
    return meta


def set_description(artifact_id, description, *, owner=None) -> dict:
    """Gallery subtitle. Metadata only — the stored pages are untouched.

    Owner-only, the same guard set_visibility carries (D22). Round one gated
    publish and visibility and left this one open: a second operator holding
    the locality token could rewrite the subtitle of anyone's page, which is
    the gallery text every other operator reads.
    """
    aid = _check_id(artifact_id)
    with _artifact_lock(aid):
        meta = _read_meta(aid)
        if meta is None:
            raise ArtifactError(f"no such artifact: {artifact_id}")
        _require_owner(meta, owner)
        meta["description"] = (str(description).strip()[:1000]
                               if description is not None else None) or None
        meta["updated_at"] = _now()
        _write_meta(aid, meta)
    return meta


def set_current(artifact_id, version, *, owner=None) -> dict:
    """Rollback: move the `current` pointer. Every version stays on disk and
    stays reachable at /a/<id>/v/<n>.

    Owner-only (D22): an ungated rollback silently serves an OLDER page at a
    URL the owner believes is current — a deface that leaves no trace in the
    version list.
    """
    aid = _check_id(artifact_id)
    with _artifact_lock(aid):
        meta = _read_meta(aid)
        if meta is None:
            raise ArtifactError(f"no such artifact: {artifact_id}")
        _require_owner(meta, owner)
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


def remove(artifact_id, *, owner=None) -> bool:
    """Delete an artifact and every version. True if something was removed.

    Owner-only (D22), and it is the loudest of the three: round one left
    DELETE the one mutation with no ownership check at all, so a second
    operator could destroy another owner's page and every version it ever
    had — irreversibly, since versions are the only copy.
    """
    aid = _check_id(artifact_id)
    d = _artifact_dir(aid)
    root = os.path.realpath(store_root())
    real = os.path.realpath(d)
    if not real.startswith(root + os.sep):
        raise ArtifactError(f"refusing to remove outside the store: {aid}")
    with _artifact_lock(aid):
        meta = _read_meta(aid) if os.path.isdir(real) else None
        if meta is not None:
            _require_owner(meta, owner)
        if not os.path.isdir(real):
            return False
        shutil.rmtree(real)
    _append_log({"ts": _now(), "id": aid, "n": None,
                 "owner": _norm_login(owner) or default_owner(),
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


def set_owner_override(login, alias=None):
    """Attribute publishes in this context to `login`, with an optional
    `alias` — the caller's id in its own namespace (D21).

    The identity server knows who is calling; `mcp_server`'s tool functions
    never see the request. Same shape as `tools.set_base_dir_override`:
    the server sets this around the call and resets it after. Returns a
    token for `reset_owner_override()`.

    `login` must be an identity a reader can actually present (a tailnet
    login / email); `valid_email()` is the test for that. The `alias` is the
    surface's own opaque id for the same human — recorded as provenance on
    what this context publishes, and NOTHING else (R6): it authorizes no read
    and no mutation, so it can never stand in for a login that is missing.
    """
    return _OWNER_OVERRIDE.set(
        (_norm_login(login) or None, _norm_login(alias) or None))


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
    who = _owner_override()[0]
    if who:
        return who
    for var in ("OPENBEAST_ARTIFACT_OPERATORS", "OPENBEAST_CHAT_OPERATORS"):
        for part in (os.environ.get(var) or "").split(","):
            # R1: a login, not any non-empty string. A stray "@" in the
            # allowlist used to become the owner of every unattributed page.
            who = valid_email(part)
            if who:
                return who
    return "local"


def _owner_override() -> tuple:
    """(login, alias) from the ContextVar, tolerating the bare-string shape
    an older caller may still set."""
    cur = _OWNER_OVERRIDE.get()
    if isinstance(cur, tuple):
        return (cur[0] or None, (cur[1] if len(cur) > 1 else None) or None)
    return ((cur or None), None)


def default_owner_alias() -> str:
    """The caller's id in its own namespace for this context, or "" (D21).

    Recorded as meta["owner_webui_id"] at publish, so an operator reading the
    store can tie a page owned by a login back to the account that published
    it. PURE PROVENANCE (R6): can_view() and the ownership guards do not
    consult it, and no API returns it.
    """
    return _owner_override()[1] or ""


def can_view(meta, viewer_login) -> bool:
    """Read permission for one artifact. Fails CLOSED (D2).

    True for anything marked `tailnet`, and otherwise only for the owner. An
    ANONYMOUS viewer (`viewer_login is None`) never reads a non-tailnet page:
    the old "no identity configured => single-user rig" branch handed every
    private artifact to any caller who simply omitted the login header.

    A legacy artifact with no owner at all is still readable, but only by an
    IDENTIFIED caller — and the server (D1) gives an anonymous request a 404
    before it ever gets here, so `viewer_login` is never None on a real read.

    "The owner" is meta["owner"] and nothing else (R6). meta["owner_webui_id"]
    is provenance, not a credential: flattening it into the same set as the
    login a reader presents meant the Open WebUI id AUTHENTICATED, so on a rig
    with no operator allowlist a stranger who simply presented that id as
    their login read the private page.
    """
    if not isinstance(meta, dict):
        return False
    if meta.get("visibility") == "tailnet":
        return True
    if viewer_login is None:
        return False         # NEVER open to anonymous
    owner = _owner_of(meta)
    if not owner:
        return True          # legacy/unowned: an identified caller may read
    return _norm_login(viewer_login) == owner


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
