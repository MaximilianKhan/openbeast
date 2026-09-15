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
    <id>/inbox.jsonl   append-only steering ops for that session (0600)

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
a single campaign is routine, not theoretical.

EVERYTHING HERE IS FAIL-SOFT. This module sits on `runner.py`'s hot path
(`log_event` calls `touch` on every transcript event). A corrupt, partial, or
unreadable record is skipped, never raised: a ledger problem must never take
down an agent that is otherwise working.
"""

from __future__ import annotations

import json
import os
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


def pid_start_time(pid: int) -> int | None:
    """Process start time (field 22 of /proc/<pid>/stat), or None.

    Field 2 (`comm`) is parenthesised and may itself contain spaces and
    parentheses — `rpartition(')')` is the only correct way to skip it. After
    the split, field N lives at index N-3, so field 22 is index 19.
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
        return int(fields[19])
    except ValueError:
        return None


def _alive(pid, pid_start) -> bool:
    """True when `pid` is running AND is the same process we registered.

    A None `pid_start` means the record predates start-time capture (or /proc
    was unreadable); we fall back to bare pid existence, which is weaker but
    never worse than the old behaviour.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    current = pid_start_time(pid)
    if current is None:
        return False
    if pid_start is None:
        return True
    try:
        return int(pid_start) == current
    except (TypeError, ValueError):
        return True


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
             transcript: str | None = None, meta: dict | None = None) -> dict:
    """Create (or re-create) a `running` record and return it.

    Captures `meta["pid_start"]` so `reconcile` can tell our process from a
    later one that inherited the same pid. Does NOT create the inbox
    directory: an inbox comes into being only when somebody writes an op.
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
    rec["meta"].setdefault("pid_start", pid_start_time(rec["pid"]))
    rec["meta"].setdefault("cursor", 0)
    _write_record(rec)
    return rec


def touch(session_id: str, **fields) -> None:
    """Bump `updated_at` and merge `fields` into the record.

    Silently ignores an unknown session — the runner calls this from inside
    `log_event`, and a missing ledger must never interrupt a transcript write.
    `meta` merges shallowly rather than replacing, so two writers (the runner
    bumping `cursor`, the chat server annotating) do not clobber each other.
    """
    try:
        path = record_path(session_id)
    except ValueError:
        return
    rec = _read_record(path)
    if rec is None:
        return
    for key, value in fields.items():
        if key in _IMMUTABLE:
            continue
        if key == "meta" and isinstance(value, dict):
            base = rec.get("meta")
            rec["meta"] = {**(base if isinstance(base, dict) else {}), **value}
        else:
            rec[key] = value
    rec["updated_at"] = _now()
    _write_record(rec)


def finalize(session_id: str, state: str, *, summary: str | None = None) -> None:
    """Move a session to a terminal state. Unknown session is ignored."""
    if state not in STATES:
        state = "failed"
    fields: dict = {"state": state}
    if summary is not None:
        fields["summary"] = str(summary)[:2000]
    fields["ended_at"] = _now()
    touch(session_id, **fields)


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
    if _alive(record.get("pid"), pid_start):
        return record
    out = dict(record)
    out["state"] = "lost"
    if not out.get("summary"):
        out["summary"] = "process gone without a terminal event"
    return out


def _reconciled(rec: dict) -> dict:
    """Reconcile and persist the `lost` transition (best effort)."""
    fixed = reconcile(rec)
    if fixed is not rec and fixed.get("state") != rec.get("state"):
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

def append_op(session_id: str, op: dict) -> None:
    """Append one steering op as a single JSON line.

    One `write()` to an `O_APPEND` fd: concurrent writers interleave whole
    lines, never halves, which is what lets the reader treat a trailing
    partial line as "not yet complete" rather than corruption.
    """
    try:
        path = inbox_path(session_id)
    except ValueError:
        return
    if op is not None and not isinstance(op, dict):
        return                           # fail-soft: junk never reaches the runner
    payload = dict(op or {})
    payload.setdefault("ts", time.time())
    try:
        line = (json.dumps(payload) + "\n").encode("utf-8")
    except (TypeError, ValueError):
        return
    try:
        os.makedirs(os.path.dirname(path), mode=_DIR_MODE, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _FILE_MODE)
    except OSError:
        return
    try:
        os.write(fd, line)
    except OSError:
        pass
    finally:
        os.close(fd)


def read_new_ops(session_id: str, cursor: int = 0) -> tuple[list[dict], int]:
    """Ops appended since byte offset `cursor`, and the new offset.

    Only COMPLETE lines are consumed: a half-written trailing line leaves the
    cursor where it was so the op is picked up whole on the next turn. A file
    shorter than the cursor (truncated or replaced) restarts from 0. A missing
    inbox returns `([], cursor)` and creates nothing.
    """
    try:
        cursor = max(int(cursor), 0)
    except (TypeError, ValueError):
        cursor = 0
    try:
        path = inbox_path(session_id)
    except ValueError:
        return [], cursor
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            if size < cursor:
                cursor = 0               # rotated/truncated: start over
            if size == cursor:
                return [], cursor
            f.seek(cursor)
            chunk = f.read()
    except OSError:
        return [], cursor
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], cursor                # nothing complete yet
    complete = chunk[:end + 1]
    ops: list[dict] = []
    for raw in complete.split(b"\n"):
        if not raw.strip():
            continue
        try:
            op = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue                     # malformed line: skip, keep going
        if isinstance(op, dict):
            ops.append(op)
    return ops, cursor + len(complete)


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def prune(days: int = 30) -> int:
    """Delete terminal records (and their inboxes) older than `days`.

    Transcripts under `agents/logs/` are deliberately left alone — the ledger
    is an index, not the archive.
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
        # Drop the session's inbox directory too; it is never reused.
        box = os.path.join(d, name[:-len(".json")])
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
