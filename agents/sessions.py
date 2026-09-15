#!/usr/bin/env python3
"""
Session ledger — the durable record of every long-running session on the rig.

Written by the *session itself*, not by its spawner, so every spawn path
(MCP `start_agent`, `agent.sh`, `openbeast-client agent`, `scripts/job.sh`)
appears in the ledger without changing the spawners. This is what closes the
"registry dies with the tool server" gap: `agents/mcp_server.py` keeps an
in-memory `_agents` map that vanishes on restart, while these records live on
disk and are reconciled against `/proc` on read.

Layout (all under `<repo>/.run/sessions`, mode 0700):

    <id>.json          one record per session, 0600, written atomically
    <id>.log           combined stdout+stderr for a job (scripts/job.sh)
    <id>/inbox.jsonl   append-only steering ops for that session (0600)
    .<id>.lock         flock sidecar serialising the record's read-modify-write

Record shape:

    {id, kind, title, pid, pgid, started_at, updated_at, state,
     workdir, model, transcript, inbox, summary, meta}

    kind  ∈ {"agent", "job"}
    state ∈ STATES

`lost` is the crash case: the record still says `running` but the process is
gone without a terminal event. Liveness compares **pid AND process start
time** (field 22 of `/proc/<pid>/stat`, captured at `register()` time as
`meta["pid_start"]`), so a recycled pid can never make a dead session look
alive — on a box that spawns thousands of eval subprocesses, pid reuse inside
a single campaign is routine, not theoretical. It also rejects the **zombie**
state (field 3 == `Z`): an unreaped child keeps both its /proc entry and its
start time, which is why every console-started session used to read `running`
forever. And a record with NO recorded start time is alive enough to show in
a list but NOT alive enough to signal — see `is_alive(require_start=True)`.

Record writes are serialised by an exclusive flock per record: `touch` is a
read-modify-write and `finalize` is a compare-and-set, so a slow writer can
never clobber a co-writer's field or resurrect a terminal session.

EVERYTHING HERE IS FAIL-SOFT. This module sits on `runner.py`'s hot path
(`log_event` calls `touch` on every transcript event). A corrupt, partial, or
unreadable record is skipped, never raised: a ledger problem must never take
down an agent that is otherwise working.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat as _stat
import tempfile
import time
import uuid
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.environ.get("OPENBEAST_REPO_DIR") or os.path.dirname(_HERE)

#: Directory holding the ledger. Tests monkeypatch this module global; every
#: function reads it at call time (never captured at import) so that works.
SESSIONS_DIR = os.environ.get("OPENBEAST_SESSIONS_DIR") or os.path.join(
    REPO_DIR, ".run", "sessions")

STATES = ("running", "done", "failed", "stopped", "lost")

#: States that mean the session will never change again.
TERMINAL_STATES = ("done", "failed", "stopped", "lost")

_DIR_MODE = 0o700
_FILE_MODE = 0o600

# Fields callers may not overwrite through touch() — identity is immutable.
_IMMUTABLE = ("id", "started_at")

# --- Inbox caps (E12) -------------------------------------------------------
# read_new_ops used to read the whole tail in one go: a 25 MB inbox allocated
# ~151 MB of Python objects on a turn boundary, and an oversized `say` could
# never be evicted from the context (see runner._STEER_STUB_AFTER_TURNS), so
# ONE bad message ended the run. Everything the inbox hands the runner is now
# bounded; the cursor still advances past what was consumed, so a big backlog
# drains over several turns instead of arriving as one allocation.
#: Most inbox bytes consumed at a single turn boundary.
INBOX_MAX_READ = 256 * 1024
#: Most ops handed to the runner from a single turn boundary.
INBOX_MAX_OPS = 64
#: Longest operator message text kept; the rest is dropped with a marker.
OP_MAX_TEXT = 4000
_TRUNC_MARK = " […operator message truncated]"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _dir() -> str:
    """Current ledger directory (read late so monkeypatching works)."""
    return SESSIONS_DIR


def _ensure_dir() -> str:
    d = _dir()
    try:
        os.makedirs(d, mode=_DIR_MODE, exist_ok=True)
    except OSError:
        return d
    try:
        # makedirs() honours the umask, so re-assert 0700 on a dir we made.
        if os.stat(d).st_mode & 0o777 != _DIR_MODE:
            os.chmod(d, _DIR_MODE)
    except OSError:
        pass
    return d


def record_path(session_id: str) -> str:
    """Absolute path of a session's JSON record."""
    return os.path.join(_dir(), f"{_safe_id(session_id)}.json")


def inbox_path(session_id: str) -> str:
    """Absolute path of a session's steering inbox.

    Returning the path neither creates nor opens anything — callers under the
    eval guard must be able to compute it without touching the filesystem.
    """
    return os.path.join(_dir(), _safe_id(session_id), "inbox.jsonl")


def _safe_id(session_id: str) -> str:
    """Reject path traversal in an id that may come from a request body."""
    sid = str(session_id or "").strip()
    if not sid or sid in (".", "..") or "/" in sid or "\\" in sid or "\x00" in sid:
        raise ValueError(f"invalid session id: {session_id!r}")
    return sid


# ---------------------------------------------------------------------------
# Ids and process identity
# ---------------------------------------------------------------------------

def new_id(kind: str = "agent") -> str:
    """Fresh session id: sortable timestamp + 8 random hex.

    The timestamp prefix makes plain lexical sort equal newest-last, which is
    what `list_sessions` reverses. `kind` is accepted for call-site clarity
    and deliberately not encoded — ids stay a fixed shape.
    """
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _proc_stat(pid: int) -> tuple[str, int] | None:
    """(state char, start time) from /proc/<pid>/stat, or None.

    Field 2 (`comm`) is parenthesised and may itself contain spaces and
    parentheses — `rpartition(')')` is the only correct way to skip it. After
    the split, field N lives at index N-3: field 3 (state) is index 0 and
    field 22 (starttime) is index 19. One read gives us both, so the zombie
    check below costs nothing extra.
    """
    try:
        with open(f"/proc/{int(pid)}/stat", "rb") as f:
            raw = f.read().decode("utf-8", "replace")
    except (OSError, ValueError, TypeError):
        return None
    _, sep, rest = raw.rpartition(")")
    if not sep:
        return None
    fields = rest.split()
    if len(fields) < 20:
        return None
    try:
        return fields[0], int(fields[19])
    except ValueError:
        return None


def pid_start_time(pid: int) -> int | None:
    """Process start time (field 22 of /proc/<pid>/stat), or None."""
    got = _proc_stat(pid)
    return got[1] if got else None


#: /proc states that mean "this process is over". `Z` is the one that matters:
#: a child whose parent never wait()s keeps its /proc entry AND its start time
#: forever, so every console-started session read `running` for good.
_DEAD_STATES = ("Z", "X", "x")


def _alive(pid, pid_start, *, require_start: bool = False) -> bool:
    """True when `pid` is running AND is the same process we registered.

    A ZOMBIE is not alive (E3). Its /proc entry survives — with the SAME
    start time — until somebody reaps it, so the old check called every
    unreaped child "running" forever; that is exactly what made a finished
    console session never end its SSE stream.

    `require_start` is the difference between *reporting* and *signalling*.
    With a None `pid_start` (a record that predates start-time capture, or
    /proc unreadable at register() time) we can only prove that SOME process
    holds this pid, not that it is ours. That is tolerable for a status
    column and unacceptable for anything that delivers a signal: a reviewer
    rode that fallback into SIGKILLing an unrelated live process group. So
    every signalling path passes require_start=True and a record with no
    recorded start time is simply not alive.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid_start is None and require_start:
        return False
    got = _proc_stat(pid)
    if got is None:
        return False
    state, current = got
    if state in _DEAD_STATES:
        return False                     # zombie/dead: the pid is a tombstone
    if pid_start is None:
        return True
    try:
        return int(pid_start) == current
    except (TypeError, ValueError):
        return not require_start


def _boot_id() -> str | None:
    """This boot's identity, or None if the kernel will not say.

    /proc/sys/kernel/random/boot_id is a fresh uuid per boot. None is a
    legitimate answer (a kernel without it, a container): callers MUST treat
    "no boot id" as "no information" and fall through to the pid+start proof.
    """
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def from_another_boot(record: dict) -> bool:
    """True only when the record PROVES it was written before this boot.

    Absent-is-unknown is load-bearing in both directions. Every record
    written before this change has no boot_id, and treating absent as
    mismatched would flip every live session on the rig to `lost` the moment
    this lands; a kernel that will not report a boot id must likewise not
    invalidate anything. Only two different NON-EMPTY strings are a mismatch.
    """
    if not isinstance(record, dict):
        return False
    meta = record.get("meta")
    was = meta.get("boot_id") if isinstance(meta, dict) else None
    if not isinstance(was, str) or not was:
        return False
    now = _boot_id()
    if not now:
        return False
    return was != now


def is_alive(record: dict, *, require_start: bool = True) -> bool:
    """Public liveness for a record. Defaults to the SIGNALLING contract.

    `chat_server.signal_session` must call this immediately before killpg —
    the record it was handed may be seconds old, and a pid recycled in that
    window belongs to somebody else.
    """
    if not isinstance(record, dict):
        return False
    if from_another_boot(record):
        return False                     # [54] its pid means nothing here
    meta = record.get("meta")
    pid_start = meta.get("pid_start") if isinstance(meta, dict) else None
    return _alive(record.get("pid"), pid_start, require_start=require_start)


# ---------------------------------------------------------------------------
# Atomic record IO
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat()


def _write_record(rec: dict) -> bool:
    """Atomically write a record. Returns True on success, never raises."""
    try:
        sid = _safe_id(rec.get("id", ""))
    except ValueError:
        return False
    d = _ensure_dir()
    path = os.path.join(d, f"{sid}.json")
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{sid}.", suffix=".tmp", dir=d)
        with os.fdopen(fd, "w") as f:   # fdopen owns fd from here, incl. on raise
            json.dump(rec, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, _FILE_MODE)
        os.replace(tmp, path)          # atomic: readers see old or new, never half
        return True
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def _read_record(path: str) -> dict | None:
    """Load one record. A corrupt or partial file is skipped, never raised."""
    try:
        with open(path, "r") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or not rec.get("id"):
        return None
    return rec


def _lock_path(session_id: str) -> str:
    """Sidecar lock file for a record's read-modify-write.

    The record itself cannot be locked: `_write_record` replaces it by
    rename, so two writers would hold flocks on two different inodes. A
    stable sidecar (never renamed, never unlinked while the ledger lives) is
    what actually serialises them.
    """
    return os.path.join(_dir(), f".{_safe_id(session_id)}.lock")


class _record_lock:
    """Exclusive flock around a record RMW. Fail-soft: if the lock cannot be
    taken (read-only dir, exotic filesystem, fd exhaustion) the body still
    runs unlocked — a ledger problem must never stop an agent (module
    contract), and unlocked is exactly the old behaviour."""

    def __init__(self, session_id: str):
        self._sid = session_id
        self._fd = None

    def __enter__(self):
        try:
            _ensure_dir()
            fd = os.open(_lock_path(self._sid),
                         os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, _FILE_MODE)
        except (OSError, ValueError):
            return self
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._fd = fd
        except OSError:
            try:
                os.close(fd)
            except OSError:
                pass
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
        return False


def _blank(session_id: str) -> dict:
    return {
        "id": session_id,
        "kind": "agent",
        "title": "",
        "pid": None,
        "pgid": None,
        "started_at": _now(),
        "updated_at": _now(),
        "state": "running",
        "workdir": None,
        "model": None,
        "transcript": None,
        "inbox": None,
        "summary": None,
        "meta": {},
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register(session_id: str, *, kind: str = "agent", title: str = "",
             pid: int | None = None, pgid: int | None = None,
             workdir: str | None = None, model: str | None = None,
             transcript: str | None = None,
             meta: dict | None = None) -> dict | None:
    """Create (or re-create) a `running` record; return it, or None (E19).

    Captures `meta["pid_start"]` so `reconcile` can tell our process from a
    later one that inherited the same pid. Does NOT create the inbox
    directory: an inbox comes into being only when somebody writes an op.

    Returns None when the record could not be written (an unwritable ledger
    dir, a bad id). It used to report success unconditionally, so a caller on
    a read-only .run/ believed it owned a session that did not exist.
    """
    rec = _blank(session_id)
    rec["kind"] = kind if kind in ("agent", "job") else "agent"
    rec["title"] = str(title or "")[:500]
    rec["pid"] = int(pid) if pid is not None else os.getpid()
    if pgid is not None:
        rec["pgid"] = int(pgid)
    else:
        try:
            rec["pgid"] = os.getpgid(rec["pid"])
        except OSError:
            rec["pgid"] = None
    rec["workdir"] = workdir
    rec["model"] = model
    rec["transcript"] = transcript
    try:
        rec["inbox"] = inbox_path(session_id)
    except ValueError:
        rec["inbox"] = None
    rec["meta"] = dict(meta or {})
    # ASSIGNED, not setdefault: this is the process-identity proof, and it is
    # derived from the pid WE were handed. setdefault let any caller who could
    # reach a register() with a meta dict pre-empt it with a wrong value, and a
    # wrong pid_start does not read as corruption — it reads as a session that
    # already finished, while its command keeps running. Belt to the caller-side
    # strip in chat_server (RESERVED_META); this is the braces.
    rec["meta"]["pid_start"] = pid_start_time(rec["pid"])
    # [54] pid_start is field 22 of /proc/<pid>/stat: ticks since BOOT. Two
    # boots share one number space, and SESSIONS_DIR lives in the repo, so
    # records survive a reboot unreconciled — which made the documented
    # promise ("reconcile matches pid AND process start time, so a recycled
    # pid is never mistaken for a live session") void by construction across
    # exactly the event it names. Stamp the boot so the comparison has a
    # frame of reference.
    boot = _boot_id()
    if boot:
        rec["meta"]["boot_id"] = boot
    rec["meta"].setdefault("cursor", 0)
    with _record_lock(session_id):
        if not _write_record(rec):
            return None
    return rec


def _apply(rec: dict, fields: dict) -> dict:
    """Merge `fields` into `rec` in place-ish. `meta` merges shallowly."""
    for key, value in fields.items():
        if key in _IMMUTABLE:
            continue
        if key == "meta" and isinstance(value, dict):
            base = rec.get("meta")
            rec["meta"] = {**(base if isinstance(base, dict) else {}), **value}
        else:
            rec[key] = value
    return rec


def touch(session_id: str, **fields) -> None:
    """Bump `updated_at` and merge `fields` into the record.

    Silently ignores an unknown session — the runner calls this from inside
    `log_event`, and a missing ledger must never interrupt a transcript write.
    `meta` merges shallowly rather than replacing, so two writers (the runner
    bumping `cursor`, the chat server annotating) do not clobber each other.

    SERIALISED (E5). The merge is a read-modify-write, and the docstring above
    used to claim co-writers were safe while nothing enforced it: four
    concurrent writers lost ~10% of their writes, and a touch that read before
    a finalize and wrote after it resurrected the terminal record as
    `running` in 29 of 30 trials. The whole RMW now runs under an exclusive
    flock on a sidecar lock file, and a terminal record can never be moved
    back to `running` by a merge.
    """
    try:
        path = record_path(session_id)
    except ValueError:
        return
    if not os.path.exists(path):
        # Cheap pre-check: never create a lock sidecar for an id that has no
        # record (ids can arrive from a request body).
        return
    with _record_lock(session_id):
        rec = _read_record(path)
        if rec is None:
            return
        prior_state = rec.get("state")
        _apply(rec, fields)
        if prior_state in TERMINAL_STATES and rec.get("state") == "running":
            # A stale in-flight writer must not undo a terminal transition.
            rec["state"] = prior_state
        rec["updated_at"] = _now()
        _write_record(rec)


def finalize(session_id: str, state: str, *, summary: str | None = None) -> bool:
    """Compare-and-set a session into a terminal state (E5).

    Returns True when this call made the transition. Unknown session, or a
    record that is ALREADY terminal, is a no-op returning False: the first
    terminal verdict wins, so a late atexit `failed` cannot overwrite the
    `done` the loop already recorded, and nothing can walk a finished session
    back to `running`. Runs under the same flock as `touch`.
    """
    if state not in STATES:
        state = "failed"
    if state == "running":
        return False                     # finalize means terminal, full stop
    try:
        path = record_path(session_id)
    except ValueError:
        return False
    if not os.path.exists(path):
        return False
    fields: dict = {"state": state}
    if summary is not None:
        fields["summary"] = str(summary)[:2000]
    fields["ended_at"] = _now()
    with _record_lock(session_id):
        rec = _read_record(path)
        if rec is None:
            return False
        if rec.get("state") in TERMINAL_STATES:
            return False                 # already decided; do not re-decide
        _apply(rec, fields)
        rec["updated_at"] = _now()
        return _write_record(rec)


def reconcile(record: dict) -> dict:
    """Return `record` with a stale `running` state corrected to `lost`.

    Pure: it does not write. `get`/`list_sessions` persist the transition.
    """
    if not isinstance(record, dict):
        return record
    if record.get("state") != "running":
        return record
    meta = record.get("meta")
    pid_start = meta.get("pid_start") if isinstance(meta, dict) else None
    if not from_another_boot(record) and _alive(record.get("pid"), pid_start):
        return record
    out = dict(record)
    out["state"] = "lost"
    if not out.get("summary"):
        out["summary"] = ("started before this boot — the rig restarted while it "
                          "was running" if from_another_boot(record)
                          else "process gone without a terminal event")
    return out


def _reconciled(rec: dict) -> dict:
    """Reconcile and persist the `lost` transition (best effort).

    The lock is taken ONLY when there is a transition to persist, and the
    record is re-read inside it: a session that finalized between our read
    and the lock must keep its own verdict rather than be overwritten with
    `lost`.
    """
    fixed = reconcile(rec)
    if fixed is rec or fixed.get("state") == rec.get("state"):
        return fixed
    try:
        path = record_path(rec.get("id", ""))
    except ValueError:
        return fixed
    with _record_lock(rec["id"]):
        current = _read_record(path)
        if current is None or current.get("state") != "running":
            return current if current is not None else fixed
        _write_record({**fixed, "updated_at": _now()})
    return fixed


def get(session_id: str) -> dict | None:
    """One reconciled record, or None if it is missing or unreadable."""
    try:
        path = record_path(session_id)
    except ValueError:
        return None
    rec = _read_record(path)
    return _reconciled(rec) if rec is not None else None


def list_sessions(*, state: str | None = None, kind: str | None = None,
                  limit: int | None = None) -> list[dict]:
    """Reconciled records, newest first.

    Filtering happens AFTER reconciliation, so `state="lost"` finds the
    sessions whose process died without ever writing a terminal event — the
    whole reason the state exists.
    """
    d = _dir()
    try:
        names = os.listdir(d)
    except OSError:
        return []
    out: list[dict] = []
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        rec = _read_record(os.path.join(d, name))
        if rec is None:
            continue                      # corrupt/partial: skip, never raise
        rec = _reconciled(rec)
        if state is not None and rec.get("state") != state:
            continue
        if kind is not None and rec.get("kind") != kind:
            continue
        out.append(rec)
    out.sort(key=lambda r: (str(r.get("started_at") or ""), str(r.get("id") or "")),
             reverse=True)
    if limit is not None and limit >= 0:
        out = out[:limit]
    return out


# ---------------------------------------------------------------------------
# Steering inbox
# ---------------------------------------------------------------------------

def append_op(session_id: str, op: dict) -> bool:
    """Append one steering op as a single JSON line. True if it landed.

    One `write()` to an `O_APPEND` fd: concurrent writers interleave whole
    lines, never halves, which is what lets the reader treat a trailing
    partial line as "not yet complete" rather than corruption.

    That guarantee was a comment, not a check: the write's return value was
    discarded and its OSError swallowed, so on a full disk a /send was DROPPED
    while the API answered `{"queued": true}`. Worse in the short-write case
    (legal on a regular file — ENOSPC after a partial transfer, a quota, an
    RLIMIT_FSIZE), which only the 32 KiB /send op is big enough to hit: the
    newline-less remnant swallowed the NEXT op too, because read_new_ops
    advances its cursor past a line before json.loads rejects it, so TWO ops
    vanished with no error anywhere. Now the write is looped, a partial line
    is TERMINATED so it can never merge with its successor (costing one op
    instead of two), and the caller is told.

    O_NOFOLLOW (E13): the ledger is 0700, but a symlink planted at the inbox
    path by anything that ever ran as this user would turn "append an
    operator message" into "append attacker-chosen JSON to an arbitrary
    file". We never follow a link here; a linked path simply fails soft.
    """
    try:
        path = inbox_path(session_id)
    except ValueError:
        return False
    if op is not None and not isinstance(op, dict):
        return False                     # fail-soft: junk never reaches the runner
    payload = dict(op or {})
    payload.setdefault("ts", time.time())
    try:
        line = (json.dumps(payload) + "\n").encode("utf-8")
    except (TypeError, ValueError):
        return False
    try:
        os.makedirs(os.path.dirname(path), mode=_DIR_MODE, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                     | os.O_NOFOLLOW | os.O_CLOEXEC, _FILE_MODE)
    except OSError:
        return False                     # incl. ELOOP: the path is a symlink
    mv = memoryview(line)
    try:
        while mv:
            wrote = os.write(fd, mv)
            if wrote <= 0:
                break
            mv = mv[wrote:]
    except OSError:
        pass
    finally:
        if mv:
            # A partial line is worse than no line: unterminated, it merges
            # with the next op and takes that one down with it. Close it.
            try:
                os.write(fd, b"\n")
            except OSError:
                pass
        os.close(fd)
    return not mv


def _clamp_op(op: dict) -> dict:
    """Bound the one field an operator controls the size of (E12)."""
    text = op.get("text")
    if isinstance(text, str) and len(text) > OP_MAX_TEXT:
        op = dict(op)
        op["text"] = text[:OP_MAX_TEXT] + _TRUNC_MARK
    return op


def read_new_ops(session_id: str, cursor: int = 0) -> tuple[list[dict], int]:
    """Ops appended since byte offset `cursor`, and the new offset.

    Only COMPLETE lines are consumed: a half-written trailing line leaves the
    cursor where it was so the op is picked up whole on the next turn. A file
    shorter than the cursor (truncated or replaced) restarts from 0. A missing
    inbox returns `([], cursor)` and creates nothing.

    BOUNDED (E12). At most `INBOX_MAX_READ` bytes and `INBOX_MAX_OPS` ops
    leave this function per call, and each op's `text` is clipped to
    `OP_MAX_TEXT`. The cursor advances past exactly what was consumed, so a
    backlog drains across turns instead of arriving as one 151 MB allocation.

    NON-BLOCKING (E13). A FIFO at the inbox path used to hang the runner
    forever inside `open()` at EVERY turn boundary — the module's fail-soft
    contract inverted into a hard hang, from a path anyone who can write the
    ledger dir controls. We open O_RDONLY|O_NONBLOCK (which returns
    immediately even on a FIFO with no writer) and then require S_ISREG.
    O_NOFOLLOW matches append_op: a symlink planted at the inbox path would
    otherwise let anything readable be parsed as a stream of operator ops.
    """
    try:
        cursor = max(int(cursor), 0)
    except (TypeError, ValueError):
        cursor = 0
    try:
        path = inbox_path(session_id)
    except ValueError:
        return [], cursor
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                     | os.O_CLOEXEC)
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode):
            return [], cursor            # FIFO, device, directory: not an inbox
        size = st.st_size
        if size < cursor:
            cursor = 0                   # rotated/truncated: start over
        if size == cursor:
            return [], cursor
        os.lseek(fd, cursor, os.SEEK_SET)
        chunk = os.read(fd, INBOX_MAX_READ)
    except OSError:
        return [], cursor
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    end = chunk.rfind(b"\n")
    if end < 0:
        if len(chunk) >= INBOX_MAX_READ:
            # A single line longer than the cap would wedge the cursor here
            # forever. Skip the unreadable run of bytes rather than stall.
            return [], cursor + len(chunk)
        return [], cursor                # nothing complete yet
    complete = chunk[:end + 1]
    ops: list[dict] = []
    consumed = 0
    for raw in complete.split(b"\n")[:-1]:
        consumed += len(raw) + 1
        if not raw.strip():
            continue
        try:
            op = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue                     # malformed line: skip, keep going
        if isinstance(op, dict):
            ops.append(_clamp_op(op))
            if len(ops) >= INBOX_MAX_OPS:
                break                    # the rest waits for the next turn
    return ops, cursor + consumed


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def prune(days: int = 30) -> int:
    """Delete terminal records (and their inboxes) older than `days`.

    Transcripts under `agents/logs/` are deliberately left alone — the ledger
    is an index, not the archive. The job log `<id>.log` IS ours, though
    (scripts/job.sh streams stdout+stderr to it inside this directory), and
    used to be orphaned forever by a prune that removed only the record (E17).
    """
    cutoff = datetime.now() - timedelta(days=max(days, 0))
    d = _dir()
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    removed = 0
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        path = os.path.join(d, name)
        rec = _read_record(path)
        if rec is None:
            continue
        if rec.get("state") not in TERMINAL_STATES:
            continue
        stamp = rec.get("updated_at") or rec.get("started_at") or ""
        try:
            when = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue                     # unparseable: leave it for a human
        if when >= cutoff:
            continue
        try:
            os.unlink(path)
        except OSError:
            continue
        removed += 1
        sid_name = name[:-len(".json")]
        # The job wrapper's combined stdout+stderr stream, and the sidecar
        # lock — both live in this directory and belong to this record.
        for leaf in (f"{sid_name}.log", f".{sid_name}.lock"):
            try:
                os.unlink(os.path.join(d, leaf))
            except OSError:
                pass
        # Drop the session's inbox directory too; it is never reused.
        box = os.path.join(d, sid_name)
        try:
            for leaf in os.listdir(box):
                try:
                    os.unlink(os.path.join(box, leaf))
                except OSError:
                    pass
            os.rmdir(box)
        except OSError:
            pass
    return removed
