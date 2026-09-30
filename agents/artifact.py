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
  OPENBEAST_ARTIFACT_BASE_URL    public base for artifact_url(). Unset, it is
                                 DETECTED (see _detect_base_url): the tailnet
                                 name `tailscale serve` publishes :8446 under,
                                 else http://localhost:<ARTIFACT_PORT>.
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
import stat as _stat
import subprocess
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
    "url_caveat", "extract_title", "wrap_skeleton", "set_owner_override",
    "reset_owner_override", "default_owner", "default_owner_alias",
    "valid_email", "RIG_OWNER", "conf_value", "operators", "admins",
    "is_admin", "set_pinned", "set_tags", "set_owner", "remove_version",
    "list_page", "migrate_legacy_owners", "sweep_retention", "human_ts",
    "publish_notice",
]


class ArtifactError(Exception):
    """Any publish/validation/lookup failure. Callers turn this into a 4xx
    (server) or a plain string (tool contract) — never a traceback."""


class ArtifactMetaCorrupt(ArtifactError):
    """meta.json exists and does not PARSE.

    A subclass, so every existing handler (and the server's _store_error)
    treats it exactly as before. It exists for one caller: remove() may treat
    an unparseable record as having no recorded owner — that is what makes a
    corrupted artifact deletable at all — but it must NOT do that for a record
    it merely failed to READ. _read_meta folded OSError and ValueError into
    one error, so a transient EIO would have skipped the ownership check and
    deleted somebody else's page. The undeletable-record fix would then have
    introduced a worse bug than the one it closed.
    """


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

# The RIG principal (F-A1). Everything this box publishes with no human
# identity attached — scripts/artifact.sh (the locality token), campaign
# scripts, background agents, the OpenCode stdio tool — is owned by this one
# stable name, whatever the operator allowlist says TODAY. It used to be the
# allowlist's first entry when there was one and the literal "local" when
# there was not, so adding ARTIFACT_OPERATORS later stranded every page
# published before it: owned by "local", which no principal could present any
# more, unreadable and unmanageable through every surface.
#
# Not an email, on purpose: no reader can ever present it by header (the
# server refuses both reserved names), so it is never a read password. Who
# may act AS the rig is decided by is_admin(): the locality token, and the
# configured admins (ARTIFACT_ADMINS, else the operator allowlist).
RIG_OWNER = "rig"
# The pre-F-A1 spelling of the same principal; migrate_legacy_owners()
# rewrites it, and _owner_of() reads it as RIG_OWNER in the meantime.
LEGACY_LOCAL_OWNER = "local"
RESERVED_LOGINS = frozenset({RIG_OWNER, LEGACY_LOCAL_OWNER})

# Tags a page may carry (F-A2): a short, boring alphabet — they are rendered
# as chips and matched byte for byte by the gallery filter.
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9 _.-]{0,31}\Z")
MAX_TAGS = 16
# A session id stamped as provenance (F-A3). beast-chat's ids are
# "<kind>-<stamp>-<hex>"; accept that shape and nothing that could smuggle
# markup or a path into the shell's link.
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")

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
# `\Z`, not `$`: Python's `$` also matches BEFORE a single trailing newline,
# so `_SEG_RE.match("dir\n")` was True and a published path could carry one
# raw newline — an on-disk directory named "dir\n" — which made the "no
# control characters" rule above a comment rather than a check. (Review [23].)
_SEG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}\Z")

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
_ID_LOCKS: dict[str, list] = {}     # aid -> [Lock, holders+waiters]
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


def _id_mutex_acquire(aid: str, timeout: float) -> bool:
    """Take the in-process mutex for `aid`, REFCOUNTED so the table drains.

    Every publish mints a fresh uuid4, and the table used to keep one Lock per
    id for the life of the process: a server that published all day grew it
    without bound. An entry now lives exactly as long as somebody holds or
    waits on it.
    """
    with _LOCKS_MUTEX:
        slot = _ID_LOCKS.get(aid)
        if slot is None:
            slot = _ID_LOCKS[aid] = [threading.Lock(), 0]
        slot[1] += 1
    if slot[0].acquire(timeout=timeout):
        return True
    _id_mutex_forget(aid, slot)
    return False


def _id_mutex_forget(aid: str, slot) -> None:
    with _LOCKS_MUTEX:
        slot[1] -= 1
        if slot[1] <= 0 and _ID_LOCKS.get(aid) is slot:
            del _ID_LOCKS[aid]


def _id_mutex_release(aid: str) -> None:
    with _LOCKS_MUTEX:
        slot = _ID_LOCKS.get(aid)
    if slot is None:                       # pragma: no cover - defensive
        return
    slot[0].release()
    _id_mutex_forget(aid, slot)


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
    if not _id_mutex_acquire(aid, timeout):
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
        _id_mutex_release(aid)


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
    if not isinstance(meta, dict):
        return ""
    owner = _norm_login(meta.get("owner"))
    # The legacy spelling of the rig principal IS the rig principal (F-A1),
    # before and after migrate_legacy_owners() has rewritten it on disk.
    return RIG_OWNER if owner == LEGACY_LOCAL_OWNER else owner


def _require_owner(meta, owner, admin: bool = False) -> str:
    """The ownership guard every mutator shares (D5/D22).

    `owner` is who is asking (the server passes the resolved principal);
    absent, the resolved caller. An artifact with no recorded identity at all
    is legacy and stays mutable — everything else is owner-only, and the
    message says nothing about who the owner is, so a probe learns nothing.

    `admin` (F-A1) is the explicit administrator path: the caller has already
    been established as the rig (locality token) or a configured admin, and
    may manage every page. It is a flag the SERVER sets from the principal it
    resolved — never something a request body can say about itself.
    """
    who = _norm_login(owner) or default_owner()
    if who == LEGACY_LOCAL_OWNER:
        who = RIG_OWNER
    if admin:
        return who
    known = _owner_of(meta)
    if known and who != known:
        raise ArtifactError("not your artifact")
    return who


def _rig_may_republish(meta, who) -> bool:
    """The one widening of the republish guard: the RIG principal (the CLI,
    OpenCode stdio, a campaign — anything on this box with no identity) may
    add a version to a page owned by the rig's own human.

    Before F-A1 a CLI publish on a rig with ARTIFACT_OPERATORS set was owned
    by operators[0], and `artifact.sh publish <f> --id <id>` updated it in
    place. migrate_legacy_owners() only re-owns the literal "local", because
    an operators[0] page may equally have been published by that human from
    a browser and nothing on disk tells the two apart reliably — so without
    this, every such page stopped accepting the documented update-in-place
    publish after the upgrade (404).

    Narrow on purpose: the first operator (the old CLI owner) and configured
    admins (who may already act as the rig), never another operator's page.
    The owner is not rewritten — republish never changes who owns a page."""
    if _norm_login(who) != RIG_OWNER:
        return False
    known = _owner_of(meta)
    if not known or known == RIG_OWNER:
        return False
    return known in set(admins()) | set(operators()[:1])


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
    except ValueError as e:
        # The record EXISTS and does not parse. Distinct from "could not be
        # read", because remove() treats this case as having no recorded owner
        # and therefore deletable — see the note there.
        raise ArtifactMetaCorrupt(f"unreadable artifact metadata: {e}")
    except OSError as e:
        raise ArtifactError(f"cannot read artifact metadata: {e}")
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
            visibility=None, owner=None, owner_alias=None,
            source_session=None) -> dict:
    """Write a new version and return {id, version, url, title, bytes,
    visibility, created, owner, notice}.

    `visibility` in the result is the page's EFFECTIVE visibility — on a
    republish that is the stored one, whatever was asked for (D5 below) — so
    no caller can report a share that did not happen. `notice` is a sentence
    to show the publisher when the page, as published, will not open where
    they probably expect it to (publish_notice), or "".

    title       None keeps the rule Claude Code uses: the page's own <title>
                wins when it has one — on EVERY version, so a republished
                page whose title changed is not stuck under the first one's
                name — else the stored title, else "Untitled".
    favicon     fixed for the life of the artifact: taken from the first
                publish that supplies one, ignored after (people find a tab
                by its icon, and the docs promise it does not move).
    source_session  provenance (F-A3): the beast-chat session that published
                this version, when there was one. Recorded on the version and
                as the artifact's latest `source_session`.

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
    requested = visibility
    if visibility is None:
        visibility = "private"
    if visibility not in VISIBILITIES:
        raise ArtifactError(
            f"visibility must be one of {VISIBILITIES}, got {visibility!r}")
    if len(page) > CAPS["page_bytes"]:
        raise ArtifactError(
            f"page is {len(page)} bytes, over the "
            f"{CAPS['page_bytes']} byte page cap")
    if not page.strip():
        raise ArtifactError("page is empty")
    session = str(source_session or "").strip()
    if session and not _SESSION_RE.match(session):
        session = ""          # provenance is best effort, never a failure

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
    # [5] Two paths where one is a "/"-prefix of the other ("a" + "a/b") are
    # each individually valid and are not duplicates, but on disk "a" is
    # written as a FILE and then os.makedirs(".../files/a") re-raises
    # FileExistsError (exist_ok only forgives an existing DIRECTORY). That
    # escaped publish() as a bare OSError, so a caller who handed in colliding
    # paths got a 500 where the contract promises a 400. Reject it here, in the
    # validation pass, before any directory exists.
    for p in payload:
        parts = p.split("/")
        for i in range(1, len(parts)):
            anc = "/".join(parts[:i])
            if anc in payload:
                raise ArtifactError(
                    f"file path {p!r} collides with file {anc!r}: one cannot be "
                    f"both a file and a directory")
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
            _require_owner(meta, resolved_owner,
                           admin=_rig_may_republish(meta, resolved_owner))
            # Backfill provenance, never rewrite it: the alias identifies the
            # creator, and a later publisher must not overwrite whose it was.
            if alias and not _norm_login(meta.get("owner_webui_id")):
                meta["owner_webui_id"] = alias
        if not isinstance(meta.get("versions"), list):
            meta["versions"] = []
        if len(meta["versions"]) >= CAPS["versions"]:
            raise ArtifactError(
                f"{aid} already has {len(meta['versions'])} versions, at the "
                f"{CAPS['versions']} version cap — free room at the same URL "
                f"with `./scripts/artifact.sh prune {aid} --keep 50 --yes` "
                f"(old versions only; the current one is never pruned)")
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
        adir_existed = os.path.isdir(_artifact_dir(aid))
        try:
            # [6] os.makedirs recurses WITHOUT mode, so `mode=` reaches only the
            # leaf: one deep call left <root>/<id> at 0755 while vN got 0700,
            # and the header's "0700 all the way down" was false. Create the
            # parent explicitly, then the version dir exclusively (os.mkdir
            # raises FileExistsError — an OSError — so D14's exclusive-create
            # semantics and the handler below are both preserved).
            os.makedirs(_artifact_dir(aid), mode=0o700, exist_ok=True)
            os.chmod(_artifact_dir(aid), 0o700)
            os.mkdir(vdir, 0o700)
        except OSError as e:
            # R7: every failure here is an ArtifactError the caller turns into
            # a 4xx. A reserved id used to reach this line as a bare
            # NotADirectoryError (<root>/index.jsonl is a file) — neither
            # `except FileExistsError` nor `except ArtifactError` caught it, so
            # the page's own owner got a 500.
            _drop_empty_dir(aid, adir_existed)
            if isinstance(e, (FileExistsError, NotADirectoryError)):
                raise ArtifactError(
                    f"cannot create version v{n} of {aid}: {e}")
            raise _storage_error(f"cannot create version v{n} of {aid}", e)
        try:
            _write_bytes(os.path.join(vdir, "index.html"), page)
            for p, data in sorted(payload.items()):
                dest = os.path.join(vdir, "files", *p.split("/"))
                # [6] One level at a time, each at 0700 — a single deep
                # makedirs would leave "files" and every intermediate
                # subdirectory at 0777 & ~umask.
                _mkdir_chain_0700(vdir, ["files"] + p.split("/")[:-1])
                _write_bytes(dest, data)
        except BaseException as e:
            shutil.rmtree(vdir, ignore_errors=True)
            _drop_empty_dir(aid, adir_existed)
            if isinstance(e, OSError):
                # A full disk used to surface as a bare OSError: HTTP 500 with
                # a traceback and no reason, and an empty <store>/<id>/ left
                # behind for a brand-new id. Name it (correctness-09).
                raise _storage_error(f"cannot write v{n} of {aid}", e)
            raise

        sha = hashlib.sha256(page).hexdigest()
        entry = {
            "n": n,
            "ts": _now(),
            "label": (label or "").strip() or None,
            "sha256": sha,               # of index.html
            "bytes": total,              # page + supporting files
            "files": sorted(payload),
        }
        if session:
            entry["source_session"] = session
            meta["source_session"] = session
        meta["versions"].append(entry)
        meta["current"] = n
        meta["updated_at"] = _now()
        page_title = extract_title(page)
        if title is not None and str(title).strip():
            meta["title"] = str(title).strip()[:200]
        elif page_title:
            # correctness-08: the page's own <title> on EVERY version, not
            # only the first — a republish that renamed the page used to keep
            # the first version's name forever.
            meta["title"] = page_title[:200]
        elif not meta.get("title"):
            meta["title"] = "Untitled"
        if description is not None and str(description).strip():
            meta["description"] = str(description).strip()[:1000]
        if (favicon is not None and str(favicon).strip()
                and not str(meta.get("favicon") or "").strip()):
            # Fixed for the life of the artifact (the documented contract):
            # the first icon given sticks, later ones are ignored.
            meta["favicon"] = str(favicon).strip()[:32]
        # No visibility change here, in either direction (D5): set_visibility()
        # is the only path, and it is owner-only.
        if meta.get("visibility") not in VISIBILITIES:
            meta["visibility"] = "private"
        try:
            _write_meta(aid, meta)
        except BaseException as e:
            # The version is only real once meta names it; if the meta write
            # fails, the directory must not survive to block v{n} forever.
            shutil.rmtree(vdir, ignore_errors=True)
            if new:
                _drop_empty_dir(aid, adir_existed, meta_too=True)
            if isinstance(e, OSError):
                raise _storage_error(f"cannot record v{n} of {aid}", e)
            raise

    log = {"ts": _now(), "id": aid, "n": n,
           "owner": meta.get("owner"), "bytes": total, "sha256": sha}
    if session:
        log["source_session"] = session
    _append_log(log)
    return {"id": aid, "version": n, "url": artifact_url(aid),
            "title": meta.get("title"), "bytes": total,
            "visibility": meta.get("visibility"), "created": new,
            "owner": meta.get("owner"),
            "notice": publish_notice(meta, requested=requested,
                                     created=new)}


class ArtifactStorageError(ArtifactError):
    """The disk said no (ENOSPC, EDQUOT, EIO, ...) — not the caller's input.
    `errno` rides along so the server can answer 507 for a full disk."""

    def __init__(self, message: str, err=None):
        super().__init__(message)
        self.errno = err


def _storage_error(what: str, e: OSError) -> "ArtifactStorageError":
    reason = e.strerror or str(e)
    return ArtifactStorageError(f"storage error: {what}: {reason}", e.errno)


def _drop_empty_dir(aid: str, existed: bool, meta_too: bool = False) -> None:
    """Remove <store>/<id>/ when THIS publish created it and nothing real is
    in it. Never touches a directory that existed before the call."""
    if existed:
        return
    d = _artifact_dir(aid)
    try:
        names = set(os.listdir(d))
    except OSError:
        return
    names = {n for n in names if not n.startswith(".meta.json.")}
    if names and not (meta_too and names <= {"meta.json"}):
        return
    if meta_too:
        # A meta.json that names no version on disk is debris of this very
        # call (the rename can land before the error surfaces).
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(d, "meta.json"))
    with contextlib.suppress(OSError):
        os.rmdir(d)


def _mkdir_chain_0700(base: str, parts: list) -> None:
    """Create base/parts/... one component at a time, each mode 0700.

    os.makedirs(mode=) only applies the mode to the LEAF (CPython recurses
    without it), so a deep call leaves every intermediate directory at
    0777 & ~umask. Review [6].
    """
    cur = base
    for part in parts:
        cur = os.path.join(cur, part)
        try:
            os.mkdir(cur, 0o700)
        except FileExistsError:
            pass
        else:
            continue
        # Pre-existing (a second file under the same subdirectory): tighten it
        # if it is ours to tighten, but never follow a symlink out of the tree.
        if os.path.isdir(cur) and not os.path.islink(cur):
            try:
                os.chmod(cur, 0o700)
            except OSError:
                pass


def _write_bytes(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


# --- read --------------------------------------------------------------------

def _rows(*, owner=None, viewer=None, admin=False, session=None,
          tag=None, pinned_first=False, query=None) -> list[dict]:
    """Every gallery row the filters admit, sorted. Never raises (D14)."""
    root = store_root()
    rows = []
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    want_session = str(session or "").strip()
    want_tag = str(tag or "").strip().lower()
    want_q = str(query or "").strip().lower()
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
                if want == LEGACY_LOCAL_OWNER:
                    want = RIG_OWNER
                have = _owner_of(meta)
                if want:
                    if want != have:
                        continue
                elif have:
                    continue        # owner="" asks for the unowned records
            if viewer is not None and not can_view(meta, viewer, admin=admin):
                continue
            if want_session and meta.get("source_session") != want_session:
                continue
            tags = _tags_of(meta)
            if want_tag and want_tag not in tags:
                continue
            if want_q and want_q not in " ".join(
                    str(x or "") for x in (meta.get("title"),
                                           meta.get("description"), name,
                                           " ".join(tags))).lower():
                continue
            versions = meta.get("versions")
            versions = versions if isinstance(versions, list) else []
            last = (versions[-1] if versions and isinstance(versions[-1], dict)
                    else {})
            aid = meta.get("id") if isinstance(meta.get("id"), str) else name
            if not _valid_id(aid):
                aid = name
            sess = meta.get("source_session")
            rows.append({
                "id": aid,
                "title": meta.get("title") or "Untitled",
                "description": meta.get("description"),
                "favicon": meta.get("favicon"),
                "owner": _owner_of(meta) or None,
                "visibility": (meta.get("visibility")
                               if meta.get("visibility") in VISIBILITIES
                               else "private"),
                "current": _coerce_int(meta.get("current"), len(versions)),
                "versions": len(versions),
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"),
                "bytes": _coerce_int(last.get("bytes"), 0),
                "pinned": meta.get("pinned") is True,
                "tags": tags,
                "source_session": (sess if isinstance(sess, str)
                                   and _SESSION_RE.match(sess) else None),
                "url": artifact_url(aid),
            })
        except Exception:
            continue            # any shape surprise: drop the row, keep going
    rows.sort(key=lambda r: (str(r.get("updated_at") or ""), str(r["id"])),
              reverse=True)
    if pinned_first:
        rows.sort(key=lambda r: not r.get("pinned"))     # stable: keeps order
    return rows


def list_artifacts(*, owner=None, viewer=None, limit=25, admin=False,
                   session=None, tag=None, pinned_first=False,
                   offset=0, query=None) -> list[dict]:
    """Artifacts newest-updated first, as compact gallery rows.

    owner  restrict to one login. viewer  drop anything can_view() refuses
    (`admin` is passed through to it: the explicit administrator path).
    session / tag  F-A3 / F-A2 filters. pinned_first  pinned rows lead.

    A record that is unreadable, corrupt or the wrong shape is SKIPPED, never
    raised (D14): the gallery is the one page an operator reaches for when
    something has gone wrong on disk, so one bad meta.json must not turn the
    whole listing into a server error.
    """
    return list_page(owner=owner, viewer=viewer, limit=limit, admin=admin,
                     session=session, tag=tag, pinned_first=pinned_first,
                     offset=offset, query=query)[0]


def list_page(*, owner=None, viewer=None, limit=25, offset=0, admin=False,
              session=None, tag=None, pinned_first=False, query=None):
    """(rows, total): one page of list_artifacts() plus how many rows the
    same filters admit in all — so a caller can say "showing 25 of 1002"
    instead of silently truncating (correctness-06)."""
    rows = _rows(owner=owner, viewer=viewer, admin=admin, session=session,
                 tag=tag, pinned_first=pinned_first, query=query)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 25
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    total = len(rows)
    rows = rows[offset:]
    return (rows[:limit] if limit > 0 else rows), total


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

def set_visibility(artifact_id, visibility, *, owner=None,
                   admin=False) -> dict:
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
        who = _require_owner(meta, owner, admin)
        before = meta.get("visibility")
        meta["visibility"] = visibility
        meta["updated_at"] = _now()
        _write_meta_or_raise(aid, meta)
    if before != visibility:
        # The one WIDENING act on the store, and it used to leave no trace
        # outside the server's audit row (correctness-04).
        _append_log({"ts": _now(), "id": aid, "n": None, "owner": who,
                     "event": "visibility", "from": before,
                     "to": visibility})
    return meta


def set_description(artifact_id, description, *, owner=None,
                    admin=False) -> dict:
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
        _require_owner(meta, owner, admin)
        meta["description"] = (str(description).strip()[:1000]
                               if description is not None else None) or None
        meta["updated_at"] = _now()
        _write_meta_or_raise(aid, meta)
    return meta


def set_current(artifact_id, version, *, owner=None,
                admin=False) -> dict:
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
        who = _require_owner(meta, owner, admin)
        try:
            n = int(version)
        except (TypeError, ValueError):
            raise ArtifactError(f"invalid version: {version!r}")
        if n not in _resolvable_versions(aid, meta):
            raise ArtifactError(f"no such version: v{version}")
        before = meta.get("current")
        meta["current"] = n
        meta["updated_at"] = _now()
        _write_meta_or_raise(aid, meta)
    if before != n:
        _append_log({"ts": _now(), "id": aid, "n": n, "owner": who,
                     "event": "rollback", "from": before})
    return meta


def remove(artifact_id, *, owner=None, admin=False,
           reason=None) -> bool:
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
    # [18] The cheap existence test belongs ABOVE the lock: taking it first
    # meant a DELETE of an id that never existed permanently added an
    # _ID_LOCKS entry and created a zero-byte <root>/.locks/<id>.lock. The
    # in-lock check below stays as the race-safe one.
    if not os.path.isdir(real):
        return False
    with _artifact_lock(aid):
        # [32] An unparseable meta.json used to raise out of remove() as a 400,
        # while every READ path already treats such a record as absent — so a
        # truncated record was invisible, unservable AND undeletable through
        # every API, recoverable only by rm -rf inside the 0700 store. Treat it
        # as the no-recorded-owner case the next line already handles; the
        # store-root containment check above still bounds what can be removed.
        try:
            meta = _read_meta(aid) if os.path.isdir(real) else None
        except ArtifactMetaCorrupt:
            # Unparseable: treat as the no-recorded-owner case the next line
            # handles. This is what makes a corrupted record deletable.
            meta = None
        # NOT `except ArtifactError`: a plain read failure (EIO, permissions)
        # must propagate, or a failing disk would bypass the ownership check
        # below and delete another owner's page. Ownership is the one thing
        # DELETE cannot guess at.
        if meta is not None:
            _require_owner(meta, owner, admin)
        if not os.path.isdir(real):
            return False
        shutil.rmtree(real)
    entry = {"ts": _now(), "id": aid, "n": None,
             "owner": _norm_login(owner) or default_owner(),
             "bytes": 0, "event": "remove"}
    if reason:
        entry["reason"] = str(reason)[:64]
    _append_log(entry)
    return True


def _write_meta_or_raise(aid: str, meta: dict) -> None:
    """_write_meta, with a disk failure named (correctness-09) rather than
    escaping as a bare OSError the server turns into a 500 + traceback."""
    try:
        _write_meta(aid, meta)
    except OSError as e:
        raise _storage_error(f"cannot update {aid}", e)


def _tags_of(meta) -> list:
    """meta["tags"], de-junked: only strings that pass _TAG_RE, in order."""
    raw = meta.get("tags") if isinstance(meta, dict) else None
    out: list = []
    for t in raw if isinstance(raw, list) else []:
        if isinstance(t, str):
            t = t.strip().lower()
            if _TAG_RE.match(t) and t not in out:
                out.append(t)
    return out[:MAX_TAGS]


def _check_tags(tags) -> list:
    if not isinstance(tags, (list, tuple)):
        raise ArtifactError("tags must be a list of strings")
    out: list = []
    for t in tags:
        if not isinstance(t, str):
            raise ArtifactError("tags must be a list of strings")
        t = t.strip().lower()
        if not t:
            continue
        if not _TAG_RE.match(t):
            raise ArtifactError(
                f"invalid tag {t[:40]!r}: letters, digits, space . _ - "
                f"only, 32 characters max")
        if t not in out:
            out.append(t)
    if len(out) > MAX_TAGS:
        raise ArtifactError(f"{len(out)} tags, over the {MAX_TAGS} tag cap")
    return out


def _set_field(artifact_id, owner, admin, event, apply) -> dict:
    """Locked, owner-gated read-modify-write of one meta field. `apply`
    mutates meta and returns (before, after) for the ledger row."""
    aid = _check_id(artifact_id)
    with _artifact_lock(aid):
        meta = _read_meta(aid)
        if meta is None:
            raise ArtifactError(f"no such artifact: {artifact_id}")
        who = _require_owner(meta, owner, admin)
        before, after = apply(meta)
        if before != after:
            meta["updated_at"] = meta.get("updated_at") or _now()
            _write_meta_or_raise(aid, meta)
    if before != after:
        _append_log({"ts": _now(), "id": aid, "n": None, "owner": who,
                     "event": event, "from": before, "to": after})
    return meta


def set_pinned(artifact_id, pinned, *, owner=None, admin=False) -> dict:
    """Pin or unpin (F-A2). A pinned page leads the gallery and is never
    touched by the retention sweep. Metadata only; owner-gated like every
    other mutator. Deliberately does NOT bump updated_at: pinning is not an
    edit, and the gallery's order must not jump because someone starred."""
    want = bool(pinned)

    def apply(meta):
        before = meta.get("pinned") is True
        if want:
            meta["pinned"] = True
        else:
            meta.pop("pinned", None)
        return before, want
    return _set_field(artifact_id, owner, admin, "pin", apply)


def set_tags(artifact_id, tags, *, owner=None, admin=False) -> dict:
    """Replace the page's tags (F-A2). Validated: at most MAX_TAGS, each a
    short lowercase label. An empty list clears them."""
    clean = _check_tags(tags)

    def apply(meta):
        before = _tags_of(meta)
        if clean:
            meta["tags"] = clean
        else:
            meta.pop("tags", None)
        return before, clean
    return _set_field(artifact_id, owner, admin, "tags", apply)


def set_owner(artifact_id, new_owner, *, owner=None, admin=False) -> dict:
    """Hand a page to another principal (correctness-03). ADMIN ONLY — the
    rig (locality token) or a configured admin — because it is the one
    mutation that decides who can read a private page. The new owner must be
    something a reader can present (valid_email) or the rig itself."""
    if not admin:
        raise ArtifactError("not your artifact")
    target = _norm_login(new_owner)
    if target == LEGACY_LOCAL_OWNER:
        target = RIG_OWNER
    if target != RIG_OWNER:
        target = valid_email(target)
    if not target:
        raise ArtifactError(
            f"invalid owner {str(new_owner)[:80]!r}: a tailnet login "
            f"(an email address) or 'rig'")

    def apply(meta):
        before = _owner_of(meta) or None
        meta["owner"] = target
        return before, target
    return _set_field(artifact_id, owner, admin, "owner", apply)


def remove_version(artifact_id, version, *, owner=None, admin=False) -> dict:
    """Delete ONE old version (F-A2 / correctness-10): the escape from the
    200-version cap that keeps the URL. Refuses the version `current` points
    at (roll back first) and the last remaining version (remove the artifact
    instead). Only a version meta NAMES is removed — R4: debris judgement is
    never made here."""
    aid = _check_id(artifact_id)
    try:
        n = int(version)
    except (TypeError, ValueError):
        raise ArtifactError(f"invalid version: {version!r}")
    with _artifact_lock(aid):
        meta = _read_meta(aid)
        if meta is None:
            raise ArtifactError(f"no such artifact: {artifact_id}")
        who = _require_owner(meta, owner, admin)
        versions = meta.get("versions")
        if not isinstance(versions, list) or n not in _version_numbers(meta):
            raise ArtifactError(f"no such version: v{version}")
        if len(_version_numbers(meta)) <= 1:
            raise ArtifactError(
                f"v{n} is the only version of {aid} — remove the artifact "
                f"instead")
        if _coerce_int(meta.get("current"), 0) == n:
            raise ArtifactError(
                f"v{n} is the version {aid} currently serves — roll back to "
                f"another one first")
        keep = []
        for v in versions:
            try:
                vn = int(v.get("n")) if isinstance(v, dict) else None
            except (TypeError, ValueError):
                vn = None
            if vn != n:
                keep.append(v)
        meta["versions"] = keep
        _write_meta_or_raise(aid, meta)
        # Meta first, THEN the directory: a crash between the two leaves an
        # unreferenced vN, which is exactly the debris D14 already tolerates.
        shutil.rmtree(os.path.join(_artifact_dir(aid), f"v{n}"),
                      ignore_errors=True)
    _append_log({"ts": _now(), "id": aid, "n": n, "owner": who,
                 "event": "remove-version"})
    return meta


def migrate_legacy_owners() -> list:
    """Re-own every page stored under the pre-F-A1 "local" owner to the rig
    principal. Idempotent (a second run finds nothing), locked per id, and
    written to the index.jsonl ledger — one row per page it touched. Returns
    the ids it re-owned. Never raises: a page it cannot rewrite keeps its old
    owner, which _owner_of() already reads as the rig."""
    done = []
    try:
        names = os.listdir(store_root())
    except OSError:
        return done
    for name in sorted(names):
        if not _valid_id(name):
            continue
        try:
            meta = get_meta(name)
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        if _norm_login(meta.get("owner")) != LEGACY_LOCAL_OWNER:
            continue
        try:
            with _artifact_lock(name):
                meta = _read_meta(name)
                if (not isinstance(meta, dict) or _norm_login(
                        meta.get("owner")) != LEGACY_LOCAL_OWNER):
                    continue
                meta["owner"] = RIG_OWNER
                _write_meta(name, meta)
        except Exception:
            continue
        _append_log({"ts": _now(), "id": name, "n": None, "owner": RIG_OWNER,
                     "event": "reown", "from": LEGACY_LOCAL_OWNER,
                     "to": RIG_OWNER, "reason": "migration"})
        done.append(name)
    return done


def _parse_ts(value):
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def retain_days() -> int:
    """ARTIFACT_RETAIN_DAYS (env OPENBEAST_ARTIFACT_RETAIN_DAYS, else
    openbeast.conf). 0 — the default — means the sweep is OFF."""
    raw = conf_value("ARTIFACT_RETAIN_DAYS")
    try:
        days = int(str(raw or "0").strip())
    except ValueError:
        return 0
    return days if days > 0 else 0


def sweep_retention(days=None, *, now=None) -> list:
    """Opt-in retention (F-A2): delete every UNPINNED artifact whose last
    update is older than `days` days (default: retain_days()). Pinned pages
    are never touched; so is anything whose timestamp cannot be read (a
    sweep must not guess). Each deletion is a ledger row with
    reason "retention". Returns the removed ids."""
    days = retain_days() if days is None else int(days)
    if days <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - days * 86400
    removed = []
    for row in _rows():
        if row.get("pinned"):
            continue
        ts = _parse_ts(row.get("updated_at"))
        if ts is None or ts.timestamp() >= cutoff:
            continue
        try:
            meta = get_meta(row["id"])
        except Exception:
            continue
        # Re-checked on the record itself: the row is a snapshot.
        if not isinstance(meta, dict) or meta.get("pinned") is True:
            continue
        try:
            if remove(row["id"], owner=RIG_OWNER, admin=True,
                      reason="retention"):
                removed.append(row["id"])
        except ArtifactError:
            continue
    return removed


def human_ts(value) -> str:
    """'2026-09-30 05:29 UTC' from a stored ISO stamp (browser-10). The
    stored value keeps its microseconds — it is the gallery's sort key."""
    dt = _parse_ts(value)
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# --- configuration -----------------------------------------------------------

def _repo_dir() -> str:
    return (os.environ.get("OPENBEAST_REPO_DIR", "").strip()
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _conf_path() -> str:
    return (os.environ.get("OPENBEAST_CONF", "").strip()
            or os.path.join(_repo_dir(), "openbeast.conf"))


def _conf_file_value(key: str):
    """KEY= out of openbeast.conf: last assignment wins, quotes trimmed —
    the same reading scripts/artifact.sh's _conf_value and lib/conf.sh's
    _ob_conf_value do, without sourcing anything. None when absent."""
    try:
        with open(_conf_path(), "r", encoding="utf-8",
                  errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    found = None
    pat = re.compile(r"^\s*%s\s*=(.*)$" % re.escape(key))
    for line in lines:
        m = pat.match(line)
        if m:
            found = m.group(1)
    if found is None:
        return None
    val = found.strip()
    for q in ('"', "'"):
        if len(val) >= 2 and val.startswith(q) and val.endswith(q):
            val = val[1:-1]
    return val.strip() or None


def conf_value(key: str, *env_names):
    """A setting: the first non-empty of $OPENBEAST_<KEY>, the extra env
    names given, then openbeast.conf's KEY=. None when unset everywhere.

    The conf fallback is what lets a process NOT started by start.sh — the
    OpenCode stdio MCP server launches agents/mcp_server.py with the user's
    plain environment — see the rig's settings (correctness-02)."""
    for name in (f"OPENBEAST_{key}",) + tuple(env_names):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return _conf_file_value(key)


def conf_exists() -> bool:
    return os.path.isfile(_conf_path())


def _logins(raw) -> list:
    out: list = []
    for part in str(raw or "").split(","):
        who = valid_email(part)
        if who and who not in out:
            out.append(who)
    return out


def operators() -> list:
    """The read allowlist as valid logins, in order: ARTIFACT_OPERATORS,
    else CHAT_OPERATORS (env first, then openbeast.conf)."""
    return (_logins(conf_value("ARTIFACT_OPERATORS"))
            or _logins(conf_value("CHAT_OPERATORS")))


def admins() -> list:
    """Who may act AS THE RIG from a browser (F-A1): see and manage every
    page, and hand a page to someone else.

    ARTIFACT_ADMINS when set; else the FIRST operator on the allowlist —
    the login a CLI publish used to be owned by, i.e. the rig's own human.
    Not the whole list: D22 exists because a second operator must not be
    able to read, re-share or delete another operator's private page, and
    making every listed reader an administrator would quietly undo it. A
    rig that wants several administrators lists them in ARTIFACT_ADMINS.

    With neither set it is EMPTY: the first identified tailnet login is
    never auto-trusted, so on an unconfigured rig only the locality token
    (this box) administers anything."""
    return _logins(conf_value("ARTIFACT_ADMINS")) or operators()[:1]


def is_admin(login) -> bool:
    who = _norm_login(login)
    if not who:
        return False
    if who in RESERVED_LOGINS:
        return True               # the rig itself; never presentable by header
    return who in admins()


def publish_notice(meta, *, requested=None, created=True) -> str:
    """What the publisher needs to hear about where this page will open, or
    "". Two cases (F-A1, correctness-05):

      * a republish asked for a visibility the page does not have — D5 keeps
        the stored one, and saying nothing let a model tell a user a page was
        shared when it was not;
      * a private page owned by the rig on a rig with NO admin configured —
        no tailnet login can open it, the phone included.
    """
    if not isinstance(meta, dict):
        return ""
    vis = meta.get("visibility")
    aid = meta.get("id") or "<id>"
    notes = []
    if not created and requested and requested != vis:
        notes.append(
            f"visibility unchanged ({vis}): it is set when a page is first "
            f"published and changed only with `./scripts/artifact.sh "
            f"visibility {aid} {requested}`.")
    if vis == "private" and _owner_of(meta) == RIG_OWNER and not admins():
        notes.append(
            "No operator is configured, so this private page opens for no "
            "tailnet login yet — your phone included. Set "
            "ARTIFACT_OPERATORS=you@example.com in openbeast.conf and "
            "restart the stack (./stop.sh && ./start.sh); or share this one "
            f"page with `./scripts/artifact.sh visibility {aid} tailnet`.")
    return " ".join(notes)


# --- urls / visibility -------------------------------------------------------

# The tailnet port `setup-tailscale.sh --publish-artifact` mounts the viewer on.
_PUBLISHED_PORT = 8446
_BASE_URL_TTL = 60.0
_BASE_URL_CACHE = {"at": 0.0, "value": ""}
_BASE_URL_LOCK = threading.Lock()


def _serve_status() -> str:
    """`tailscale serve status`, or "" — never raises, never hangs."""
    try:
        done = subprocess.run(
            ["tailscale", "serve", "status"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3,
            text=True, errors="replace")
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    return done.stdout or ""


_PUBLISHED_CACHE: dict = {}


def published_base(port: int) -> str:
    """https://<name>:<port> when `tailscale serve` publishes that port, else
    "". Cached like _detect_base_url (same anchoring rule). The shell uses it
    for :8445 so a "made by session" link renders only when beast-chat is
    actually reachable on the tailnet (F-A3)."""
    now = time.monotonic()
    with _BASE_URL_LOCK:
        hit = _PUBLISHED_CACHE.get(port)
        if hit and now - hit[0] < _BASE_URL_TTL:
            return hit[1]
    m = re.search(r"^https://([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?):%d(?=\s|$)"
                  % int(port), _serve_status(), re.M)
    value = f"https://{m.group(1).lower()}:{int(port)}" if m else ""
    with _BASE_URL_LOCK:
        _PUBLISHED_CACHE[port] = (now, value)
    return value


def _detect_base_url() -> str:
    """Where a reader can ACTUALLY open an artifact, when nobody configured it.

    The old default was https://<gethostname()>:8446, which is wrong on both
    counts that matter. The tailnet machine name is chosen independently of
    the OS hostname (setup-tailscale.sh says so; this rig is `omarchy` on the
    box and `beast.<tailnet>.ts.net` on the tailnet), and the serve
    certificate is issued for the full ts.net name only — so every URL the
    model handed a user was a dead link or a certificate error. And on a rig
    that never ran --publish-artifact nothing listens on :8446 at all.

    So ask `tailscale serve` what it publishes on :8446 and use that exact
    name; failing that, the viewer is reachable on loopback only and the
    honest URL says so. Cached briefly: a gallery listing builds one URL per
    row, and --publish-artifact run after start must still be picked up
    without a restart.
    """
    now = time.monotonic()
    with _BASE_URL_LOCK:
        if _BASE_URL_CACHE["value"] and now - _BASE_URL_CACHE["at"] < _BASE_URL_TTL:
            return _BASE_URL_CACHE["value"]
    # ANCHORED to the start of a line: a mount HEADER, never text inside one.
    # Unanchored, the first match won — and a lower-port mount whose proxy
    # target or path merely mentioned "https://x:8446" became the base of
    # every URL the model handed out.
    m = re.search(r"^https://([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?):%d(?=\s|$)"
                  % _PUBLISHED_PORT, _serve_status(), re.M)
    if m:
        value = f"https://{m.group(1).lower()}:{_PUBLISHED_PORT}"
    else:
        port = _coerce_int(os.environ.get("OPENBEAST_ARTIFACT_PORT"), 3004)
        value = f"http://localhost:{port if 0 < port < 65536 else 3004}"
    with _BASE_URL_LOCK:
        _BASE_URL_CACHE.update(at=now, value=value)
    return value


def url_caveat(url) -> str:
    """One sentence to hand over WITH a URL that a browser cannot open, or "".

    The loopback fallback is the true address of the viewer, but the viewer
    refuses anonymous callers and a browser cannot present an identity to it
    (that arrives from `tailscale serve`, or from the locality token the CLI
    reads). So an unpublished rig's link is a 404 in every browser — and a
    model that hands it over without saying so has handed over a dead link.
    """
    if str(url or "").startswith(("http://localhost", "http://127.0.0.1")):
        return ("This rig's artifact viewer is not published on the tailnet "
                "yet, so that link will not open in a browser. The operator "
                "publishes it once with: ./scripts/setup-tailscale.sh "
                "--publish-artifact (the link then becomes "
                "https://<rig>.<tailnet>.ts.net:8446/a/<id>; the page and "
                "its id are already saved).")
    return ""


def artifact_url(artifact_id, version=None) -> str:
    """The durable URL. Base from $OPENBEAST_ARTIFACT_BASE_URL (conf key
    ARTIFACT_BASE_URL), else detected — see _detect_base_url()."""
    base = os.environ.get("OPENBEAST_ARTIFACT_BASE_URL", "").strip()
    if not base:
        base = _detect_base_url()
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

    The identity the server put in the ContextVar, else the RIG principal
    (F-A1). It used to fall back to the allowlist's first entry, else the
    literal "local" — so the owner of a CLI publish depended on the config of
    the day, and changing that config stranded every page published before
    it. Rig pages are now owned by one stable name, and who may act as the
    rig (is_admin) is the part the config decides.

    An ownerless artifact used to mean "readable by every operator forever",
    so a CLI or campaign publish on an unconfigured rig silently shared itself
    with the whole tailnet. The rig principal is a real owner instead.
    """
    who = _owner_override()[0]
    if who and who != LEGACY_LOCAL_OWNER:
        return who
    return RIG_OWNER


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


def can_view(meta, viewer_login, *, admin=False) -> bool:
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
    if admin:
        return True          # the explicit administrator path (F-A1)
    owner = _owner_of(meta)
    if not owner:
        return True          # legacy/unowned: an identified caller may read
    viewer = _norm_login(viewer_login)
    if viewer == LEGACY_LOCAL_OWNER:
        viewer = RIG_OWNER
    return viewer == owner


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


# browser-3. The viewer shell frames artifacts under `frame-src 'self'`, so an
# ordinary off-site <a href> navigated the FRAME to a blocked URL and replaced
# the page with Chromium's "This content is blocked" — model-written reports
# cite sources with exactly such links. Served (never stored) into every raw
# page: an http(s) link to another host opens in a new tab instead, where
# allow-popups-to-escape-sandbox lets the site run as itself. In-page anchors
# and the page's own supporting files (same host) are left alone.
LINK_GUARD = (
    b'<script>(function(){document.addEventListener("click",function(e){'
    b'var a=e.target&&e.target.closest?e.target.closest("a[href]"):null;'
    b'if(!a||a.target==="_blank")return;var u;try{u=new URL(a.href,'
    b'location.href)}catch(x){return}if((u.protocol==="http:"||'
    b'u.protocol==="https:")&&u.host!==location.host){a.target="_blank";'
    b'a.rel="noopener noreferrer"}},true)})();</script>')


def wrap_skeleton(body_html, *, theme=None, link_guard=False) -> bytes:
    """Wrap an authored page fragment in the serve-time skeleton.

    Applied on the way OUT, never stored: the version's sha256 is over the
    author's own bytes, and changing this skeleton re-renders every artifact
    ever published without rewriting a single file.

    A page that already carries its own <html> tag is passed through untouched
    (beyond the optional data-theme stamp and link guard) — hand-built pages
    like scratch/spare-memory-meta.html predate the tool and must still
    render — even if a BOM, a licence comment or an XML declaration comes
    first.

    theme       "dark"/"light" stamps data-theme on <html> and, for a
                fragment, pins `color-scheme` to it (browser-2).
    link_guard  inject LINK_GUARD (raw artifact pages only — never the
                shell or gallery, whose CSP admits hashed scripts only).
    """
    data = (body_html.encode("utf-8") if isinstance(body_html, str)
            else bytes(body_html or b""))
    stamp = ""
    scheme = "light dark"
    if theme in ("dark", "light"):
        stamp = f' data-theme="{theme}"'
        scheme = theme
    guard = LINK_GUARD if link_guard else b""
    at = _html_tag_at(data)
    if at >= 0:
        if stamp and b"data-theme" not in data[:2048]:
            # Stamp THE document's tag, found above — never a "<html" that
            # happens to sit inside the leading comment.
            data = data[:at + 5] + stamp.encode() + data[at + 5:]
        if guard:
            end = data.find(b">", at)
            if end >= 0:
                data = data[:end + 1] + guard + data[end + 1:]
        return data
    head = (
        "<!doctype html>\n"
        f"<html lang=\"en\"{stamp}>\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<style>\n"
        f":root{{color-scheme:{scheme}}}\n"
        "body{margin:0;font:14px system-ui,-apple-system,Segoe UI,Roboto,sans-serif}\n"
        "img{max-width:100%}\n"
        "[hidden]{display:none!important}\n"
        "</style>\n"
    ).encode("utf-8") + guard + b"\n</head>\n<body>\n"
    return head + data + b"\n</body>\n</html>\n"
