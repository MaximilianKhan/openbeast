#!/usr/bin/env python3
"""OpenBeast beast-chat — the sessions API and the mobile console.

The problem this solves: agents and long jobs on the rig outlive the terminal
that started them. `check_agent` polling from a laptop is a poor substitute for
watching one work, and there was no way at all to say something to a running
agent. beast-chat turns every long-running thing on the box into an addressable
SESSION with a live transcript you can attach to from a phone, steer, and stop.

Three pieces, and only the first two live here:

  LEDGER    agents/sessions.py owns the on-disk record of every session
            (state, pid/pgid, transcript path, inbox path). This module never
            writes those files directly — it goes through that API so the CLI,
            the MCP surface and this server cannot drift.
  STREAM    GET /api/chat/sessions/{id}/events is an SSE reattach stream
            modelled on llama.cpp's GET /v1/stream?conv_id&from=N
            (llama.cpp/tools/server/server-stream.cpp): the client passes a
            BYTE OFFSET, gets everything from there, then stays attached for
            live bytes. Every frame carries its own end offset in the SSE `id:`
            field, so a phone that drops mid-stream reconnects with
            `from=<last id>` and loses nothing and duplicates nothing. That is
            the whole contract; there is no server-side per-client cursor to
            get out of sync.
  CONSOLE   agents/chat_ui/console.html, served at / under a strict
            same-origin CSP. Self-contained: no framework, no CDN, no fonts.

Transcripts are the source of truth for the stream, not an in-memory buffer.
A session's transcript is an append-only file — `kind="agent"` writes the
runner's JSONL event stream (agents/runner.py log_event), `kind="job"` writes
plain text. Following a file means the server can restart, the producer can
restart, and a reader from an hour ago can still resume exactly.

Auth. IDENTITY IS REQUIRED — there is no anonymous read. A transcript is
file contents, command output and everything the model has been shown, so a
request carrying no credential at all must never stream one: combined with an
unchecked Host header that is a browser-rebinding read of the whole rig from
any page the operator happens to visit, published or not.

  READ    one of: the rig-local token (below); a Tailscale-User-Login the
          operator list allows (the list is an ALLOWLIST when set, and when
          unset any *identified* login passes — single-user default, which is
          still not "anonymous"); or an enrolled device key with the `chat`
          scope. Nothing at all => 404. Unlisted => 404, never 403: an
          unauthorized reader learns nothing about what exists.
  WRITE   additionally an enrolled device key (.run/clients.json, schema in
          scripts/clients.sh) carrying the `chat` scope. Missing, unknown,
          revoked or unscoped all answer 404 — identically, because a 401/404
          split is a membership oracle for the operator list. Rate limited
          per device.
  LOCAL   a caller that can read .run/chat-local.token is ON this box and
          bypasses both — the same proof-of-locality trick as agents/edge.py,
          because `tailscale serve` makes every remote caller look like
          127.0.0.1 and the peer address therefore proves nothing.
  HOST    TrustedHostMiddleware pins the Host header to loopback, this
          machine's names and the tailnet (`*.ts.net`). The second half of
          the rebinding defence: a hostile name that resolves to 127.0.0.1
          never reaches a route.
  SCHEMA  openapi_url=None. /docs and /redoc were already off; the schema
          endpoint was still publishing the entire write contract to a caller
          404'd everywhere else.
  HEALTH  the one ungated route, and to an unidentified caller it answers
          {"status": "ok"} and nothing more (start.sh probes it with no
          credential). Counts and paths need a read credential.

Lifecycle. A session spawned HERE has no job wrapper and no other writer, so
this module keeps the Popen handle, reaps it, and finalizes the ledger from
the exit status (0 -> done, >0 -> failed, <0 -> stopped). Without that the
child becomes a zombie, a zombie still answers the liveness check, and the
session reads `running` forever while /send queues into a corpse.

Env:
  OPENBEAST_CHAT_PORT          listen port            (default 3003)
  OPENBEAST_CHAT_BIND          bind address           (default 127.0.0.1)
  OPENBEAST_CHAT_OPERATORS     comma-separated logins (unset = open reads)
  OPENBEAST_CHAT_RATE_PER_MIN  write rate per device  (default 60)
  OPENBEAST_CHAT_STOP_TERM_S   SIGTERM escalation     (default 30)
  OPENBEAST_CHAT_STOP_KILL_S   SIGKILL escalation     (default 60)
  OPENBEAST_CHAT_POLL_MS       transcript poll period (default 250)
  OPENBEAST_CHAT_HEARTBEAT_S   SSE comment heartbeat  (default 15)
  OPENBEAST_CHAT_RUN_DIR       override .run          (tests)
  OPENBEAST_CHAT_ALLOWED_HOSTS extra trusted Host values, comma separated
  OPENBEAST_CHAT_AUTH_RECHECK_S  re-authorize an open stream every N seconds
                               (default: the heartbeat period, capped at 5)
"""
import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               StreamingResponse)
from starlette.middleware.trustedhost import TrustedHostMiddleware

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import sessions  # noqa: E402  frozen contract — agents/sessions.py

REPO_DIR = os.path.dirname(_HERE)
RUN_DIR = os.path.join(REPO_DIR, ".run")
CONSOLE_PATH = os.path.join(_HERE, "chat_ui", "console.html")
RUNNER_PATH = os.path.join(_HERE, "runner.py")
# Where transcripts are archived. Matches agents/runner.py
# DEFAULT_LOG_DIR and mcp_server._LOG_DIR — one archive, not three.
LOG_DIR = os.path.join(_HERE, "logs")

DEFAULT_PORT = 3003
TERMINAL_STATES = frozenset(s for s in sessions.STATES if s != "running")

# agents/runner.py truncates every tool result to this many characters before
# logging it. The console must be able to say "there was more" rather than
# silently presenting a clipped result as complete.
TOOL_RESULT_LIMIT = 2000

# The closed set of SSE event names a client can bind. Anything the runner
# grows in future arrives as `unknown` with its real type inside the payload,
# so an older console degrades to "show it as raw" instead of dropping it.
AGENT_EVENT_TYPES = frozenset({
    "start", "spawn", "iteration", "assistant", "tool_call", "compaction",
    "steer", "paused", "error", "context_overflow_unrecoverable", "done",
    "max_iterations",
})
CONTROL_EVENT_TYPES = frozenset({"hello", "end", "lost", "log", "unknown"})

MAX_MESSAGE_BYTES = 32 * 1024

# The console is entirely self-contained; say so in a header so a stray
# <script src> or webfont can never start working by accident.
CSP = ("default-src 'none'; "
       "img-src 'self' data:; "
       "style-src 'unsafe-inline'; "
       "script-src 'unsafe-inline'; "
       "connect-src 'self'; "
       "manifest-src 'self' data:; "
       "base-uri 'none'; "
       "form-action 'none'; "
       "frame-ancestors 'none'")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Proof of locality (same construction as agents/edge.py)
# ---------------------------------------------------------------------------

def _mint_local_token(path: str) -> str:
    """Shared secret proving the caller has filesystem access to this box.

    NOT derived from the peer address: `tailscale serve` reverse-proxies into
    127.0.0.1, so every remote tailnet caller arrives looking like loopback.
    Reading a 0600 file in .run/ is the real local/remote boundary. Minted
    fresh per process so a stale copy is worthless.
    """
    token = uuid.uuid4().hex
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # O_TRUNC on an existing path keeps the OLD mode — fchmod explicitly,
        # or a token left 0644 by some earlier run stays world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
    except OSError as e:
        # Loud, not swallowed: without the file no local tool can present the
        # token and loopback introspection silently stops working.
        print(f"[beast-chat] WARNING: could not write {path} ({e}) — "
              f"rig-local callers will need an enrolled device key",
              file=sys.stderr)
    return token


# ---------------------------------------------------------------------------
# Device registry (.run/clients.json — schema owned by scripts/clients.sh)
# ---------------------------------------------------------------------------

class DeviceRegistry:
    """Stat-gated reader for the enrolled-device registry.

    Deliberately a local reimplementation rather than an import of
    agents/edge.py: that module is the beast-gate's, carries gate-specific
    global state, and this server must not acquire a reason to be restarted
    whenever the gate changes. The file format is the contract, not the code.
    """

    def __init__(self, path: str):
        self.path = path
        self._stamp = None
        self._by_hash: dict[str, dict] = {}
        self._present = False
        self.reload()

    def reload(self) -> None:
        try:
            st = os.stat(self.path)
        except OSError:
            self._by_hash, self._present, self._stamp = {}, False, None
            return
        # (mtime, size, inode) — mtime alone misses two writes inside one
        # filesystem timestamp tick, and a stale map is a MISSED REVOCATION.
        stamp = (st.st_mtime, st.st_size, st.st_ino)
        if stamp == self._stamp:
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return  # half-written file: keep serving the last good map
        by_hash = {}
        for dev in data.get("devices", []):
            if not isinstance(dev, dict):
                continue
            digest = (dev.get("key_sha256") or "").strip().lower()
            if digest:
                by_hash[digest] = dev
        self._by_hash, self._present, self._stamp = by_hash, True, stamp

    @property
    def configured(self) -> bool:
        self.reload()
        return self._present and bool(self._by_hash)

    def lookup(self, presented_key: str) -> dict | None:
        """Device for a bearer key, or None. Revoked devices return None."""
        self.reload()
        if not presented_key:
            return None
        digest = hashlib.sha256(presented_key.encode()).hexdigest()
        for key_hash, dev in self._by_hash.items():
            if hmac.compare_digest(key_hash, digest):
                return None if dev.get("revoked_at") else dev
        return None


def _has_scope(device: dict, scope: str) -> bool:
    """Scopes are opt-in and fail closed.

    Devices enrolled before scopes existed have no `scopes` field at all. Those
    devices must NOT inherit chat access: enrolling a laptop for inference is
    not consent to steer agents on the rig. No field => no scope.
    """
    scopes = device.get("scopes")
    if not isinstance(scopes, (list, tuple)):
        return False
    return scope in {str(s).strip().lower() for s in scopes}


class OperatorList:
    """The read allowlist, re-read on every check.

    Revoking a reader must not need a stack restart — the device registry has
    always reloaded itself that way and the operator list was the one
    credential that did not. Two sources, unioned:

      OPENBEAST_CHAT_OPERATORS   read from the environment at CHECK time
      <run_dir>/chat-operators   one login per line, `#` comments, stat-gated
                                 exactly like clients.json (mtime+size+inode,
                                 because mtime alone misses two writes inside
                                 one filesystem timestamp tick)

    Empty from both sources is the single-user default: any *identified*
    login passes. It is not an anonymous bypass — read_gate still demands a
    credential of some kind.
    """

    def __init__(self, path: str, env_var: str = "OPENBEAST_CHAT_OPERATORS"):
        self.path = path
        self.env_var = env_var
        self._stamp = None
        self._file: set[str] = set()

    def _reload_file(self) -> None:
        try:
            st = os.stat(self.path)
        except OSError:
            self._file, self._stamp = set(), None
            return
        stamp = (st.st_mtime, st.st_size, st.st_ino)
        if stamp == self._stamp:
            return
        try:
            with open(self.path) as f:
                raw = f.read()
        except OSError:
            return  # half-written: keep serving the last good list
        out = set()
        for line in raw.splitlines():
            login = line.split("#", 1)[0].strip().lower()
            if login:
                out.add(login)
        self._file, self._stamp = out, stamp

    def current(self) -> set[str]:
        self._reload_file()
        env = {x.strip().lower()
               for x in os.environ.get(self.env_var, "").split(",")
               if x.strip()}
        return env | self._file

    @property
    def configured(self) -> bool:
        return bool(self.current())

    def allows(self, login: str) -> bool:
        listed = self.current()
        if not listed:
            return True
        return (login or "").strip().lower() in listed


def trusted_hosts(extra: str = "") -> list[str]:
    """Host values this server answers to (the rebinding allowlist).

    Loopback, whatever this machine calls itself, and the tailnet. `*.ts.net`
    is safe to wildcard: those names exist only inside MagicDNS, an attacker
    cannot mint one, and the published deployment is reached by exactly that
    name. Anything else — including a hostile DNS name pointed at 127.0.0.1 —
    is refused before a route ever runs.
    """
    hosts = {"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"}
    try:
        # gethostname() only. NOT getfqdn(), which does a reverse DNS lookup
        # and blocks for seconds whenever the resolver is slow or captured —
        # at startup, on a server whose whole job is to be reachable.
        name = (socket.gethostname() or "").strip().lower()
    except OSError:
        name = ""
    if name:
        hosts.add(name)
        hosts.add(name.split(".")[0])
    hosts.add("*.ts.net")
    for item in (extra or "").split(","):
        item = item.strip().lower()
        if item:
            hosts.add(item)
    return sorted(hosts)


# ---------------------------------------------------------------------------
# Transcript reading — the stream's whole source of truth
# ---------------------------------------------------------------------------

def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def read_lines_from(path: str, offset: int) -> tuple[list[tuple[str, int]], int]:
    """Complete lines at/after `offset`, plus the new offset.

    Returns [(text, end_offset), ...] where end_offset is the byte position
    immediately AFTER that line's terminating newline — i.e. exactly the value
    a client passes back as `from=` to receive the next line and nothing else.

    A partial trailing line (the producer is mid-write) is never emitted and
    never advances the offset, so a reader can never observe half an event.
    """
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            buf = f.read()
    except OSError:
        return [], offset
    if not buf:
        return [], offset
    out: list[tuple[str, int]] = []
    start = 0
    while True:
        nl = buf.find(b"\n", start)
        if nl < 0:
            break
        text = buf[start:nl].decode("utf-8", "replace").rstrip("\r")
        out.append((text, offset + nl + 1))
        start = nl + 1
    return out, offset + start


def parse_agent_event(text: str, offset: int, seq: int) -> tuple[str, dict]:
    """One JSONL transcript line -> (sse event name, payload)."""
    try:
        ev = json.loads(text)
        if not isinstance(ev, dict):
            raise ValueError("not an object")
    except Exception:
        return "unknown", {"type": "unparsed", "raw": text[:TOOL_RESULT_LIMIT],
                           "offset": offset, "seq": seq}
    ev = dict(ev)
    etype = str(ev.get("type") or "unknown")
    if etype == "tool_call":
        result = ev.get("result")
        # The runner clips to exactly TOOL_RESULT_LIMIT chars, so a result at
        # the limit is (all but certainly) clipped. Surfacing the flag is the
        # difference between "the command printed this" and "the command
        # printed at least this".
        ev["result_truncated"] = (isinstance(result, str)
                                  and len(result) >= TOOL_RESULT_LIMIT)
    ev["offset"] = offset
    ev["seq"] = seq
    ev["type"] = etype
    return (etype if etype in AGENT_EVENT_TYPES else "unknown"), ev


def sse_frame(event: str, data: dict, offset: int | None = None) -> str:
    """One SSE frame. `id:` carries the end offset — the resume token.

    Using the native `id:` field is not decoration: a browser EventSource
    replays it as Last-Event-ID on its own automatic reconnect, so the reattach
    works even when the reconnect is the browser's idea rather than ours. The
    same value is repeated inside `data` for curl and non-EventSource clients.
    """
    parts = []
    if offset is not None:
        parts.append(f"id: {offset}")
    parts.append(f"event: {event}")
    # json.dumps escapes newlines, so `data:` is always exactly one line.
    parts.append("data: " + json.dumps(data, default=str))
    return "\n".join(parts) + "\n\n"


def derive_status(record: dict) -> dict:
    """Roll the transcript up into the numbers the console header shows.

    One streaming pass, never loading the file into memory — a long agent run
    is megabytes and this endpoint gets polled.
    """
    path = record.get("transcript") or ""
    kind = record.get("kind") or "agent"
    status = {
        "kind": kind,
        "events": 0,
        "iterations": 0,
        "compactions": 0,
        "tool_calls": 0,
        "errors": 0,
        "tokens": {"prompt": 0, "completion": 0, "total": 0},
        "last_event": None,
        "last_line": "",
        "transcript_bytes": _file_size(path),
        "terminal": record.get("state") in TERMINAL_STATES,
    }
    if not path or not os.path.isfile(path):
        return status
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                status["events"] += 1
                status["last_line"] = line[:500]
                if kind != "agent":
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if not isinstance(ev, dict):
                    continue
                etype = ev.get("type")
                if etype == "iteration":
                    status["iterations"] = max(status["iterations"],
                                               int(ev.get("number") or 0))
                elif etype == "compaction":
                    status["compactions"] += 1
                elif etype == "tool_call":
                    status["tool_calls"] += 1
                elif etype in ("error", "context_overflow_unrecoverable"):
                    status["errors"] += 1
                # done / max_iterations carry the run's token totals; any
                # event that carries them is allowed to update (monotonic).
                for src, dst in (("tokens_prompt", "prompt"),
                                 ("tokens_completion", "completion"),
                                 ("tokens_total", "total")):
                    if isinstance(ev.get(src), int):
                        status["tokens"][dst] = max(status["tokens"][dst],
                                                    ev[src])
                if etype in ("done", "max_iterations"):
                    status["iterations"] = max(status["iterations"],
                                               int(ev.get("iterations") or 0))
                    status["compactions"] = max(status["compactions"],
                                                int(ev.get("compactions") or 0))
                status["last_event"] = {k: v for k, v in ev.items()
                                        if k != "result"}
                if etype == "tool_call":
                    res = ev.get("result")
                    status["last_event"]["result_truncated"] = (
                        isinstance(res, str) and len(res) >= TOOL_RESULT_LIMIT)
    except OSError:
        pass
    return status


# ---------------------------------------------------------------------------
# Process signalling (module level so tests can monkeypatch it)
# ---------------------------------------------------------------------------

def _int_or_zero(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def signal_identity_ok(record: dict) -> bool:
    """True only when the recorded pid is STILL the process we recorded.

    A pid is a reusable integer. On a box that spawns thousands of eval
    subprocesses, a stale ledger row pointing at a recycled pid is routine —
    and every path here ends in `killpg`, which would take down an unrelated
    process GROUP (a reviewer did exactly that through the bare-pid
    fallback). `meta["pid_start"]` is the process's start time captured at
    register(); it never repeats for the same pid. No pid_start at all means
    no proof of identity, and a path that SIGNALS must then refuse.
    """
    pid = _int_or_zero(record.get("pid"))
    if pid <= 1:
        return False
    meta = record.get("meta")
    want = meta.get("pid_start") if isinstance(meta, dict) else None
    if want is None:
        return False
    try:
        current = sessions.pid_start_time(pid)
    except Exception:
        return False
    if current is None:
        return False
    return _int_or_zero(current) == _int_or_zero(want)


def signal_session(record: dict, sig: int) -> bool:
    """Signal a session's process GROUP, falling back to the bare pid.

    The group is what matters: a runner that shelled out leaves children, and
    SIGTERM to the leader alone orphans them. Refuses to signal pgid <= 1 or
    our own group — a ledger record with a garbage pgid must not be able to
    take down this server or init — and refuses entirely unless the recorded
    pid is still the process we registered (see signal_identity_ok).
    """
    if not isinstance(record, dict) or not signal_identity_ok(record):
        return False
    pid = _int_or_zero(record.get("pid"))
    pgid = _int_or_zero(record.get("pgid") or record.get("pid"))
    if pgid > 1 and pgid != os.getpgrp():
        # The leader is verifiably ours; only signal the group if that is
        # still the group it leads, so a recycled pgid cannot borrow the
        # identity proof we just made about the pid.
        try:
            live_pgid = os.getpgid(pid)
        except OSError:
            live_pgid = None
        if live_pgid is None or live_pgid == pgid:
            try:
                os.killpg(pgid, sig)
                return True
            except (ProcessLookupError, PermissionError, OSError):
                pass
    if pid > 1:
        try:
            os.kill(pid, sig)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return False


# ---------------------------------------------------------------------------
# E3 — children spawned HERE are ours to reap and ours to finalize
# ---------------------------------------------------------------------------

#: Live children by session id. Dropping a Popen on the floor is what made
#: every phone-started session immortal: nobody wait()s, the child becomes a
#: zombie, /proc still lists it, the liveness check says `running`, the SSE
#: stream never ends and /send keeps queueing into a corpse.
_CHILDREN: dict[str, subprocess.Popen] = {}
_CHILDREN_LOCK = threading.Lock()


def terminal_state_for(returncode: int) -> str:
    """Exit status -> ledger state. 0 done, >0 failed, <0 (signalled) stopped."""
    rc = _int_or_zero(returncode)
    if rc == 0:
        return "done"
    if rc > 0:
        return "failed"
    return "stopped"


def exit_summary(returncode: int) -> str:
    rc = _int_or_zero(returncode)
    if rc >= 0:
        return f"exited with status {rc}"
    try:
        name = signal.Signals(-rc).name
    except (ValueError, AttributeError):
        name = f"signal {-rc}"
    return f"killed by {name}"


def annotate_when_registered(session_id: str, meta: dict, *,
                             timeout: float = 15.0, poll: float = 0.05,
                             proc: subprocess.Popen | None = None) -> bool:
    """Merge our provenance into a record the CHILD owns (E4).

    We must not register() an id the runner registers for itself: register is
    a full overwrite, so whichever write lands last wins and started_by /
    device / command vanish non-deterministically — and it resets the
    runner's consumed-message cursor, which replays the operator's last
    instruction to a resumed agent. So wait for the owner's record to appear
    and touch() ours in, which merges.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    grace = None
    while True:
        exists = False
        try:
            exists = sessions.get(session_id) is not None
        except Exception:
            exists = False
        if exists:
            with contextlib.suppress(Exception):
                sessions.touch(session_id, meta=dict(meta or {}))
            return True
        now = time.monotonic()
        if now >= deadline:
            return False
        if proc is not None and proc.poll() is not None:
            # The child died before it ever registered; one last look (it may
            # have written the record microseconds before exiting) and stop.
            if grace is None:
                grace = now + 0.5
            elif now >= grace:
                return False
        time.sleep(poll)


def reap_session(session_id: str, proc: subprocess.Popen, *,
                 annotate: dict | None = None, annotate_timeout: float = 15.0,
                 poll: float = 0.05) -> str | None:
    """Wait on our child, then record the truth. Blocking; see start_reaper.

    Finalizes ONLY from `running`/`lost`: an agent runner writes its own
    terminal state (an operator stop lands `stopped` on a process that then
    exits 0), and the exit status must not overwrite a verdict its owner
    already made. `lost` is the reconciler's guess and is exactly what we are
    here to replace with the exit status.
    """
    if annotate is not None:
        annotate_when_registered(session_id, annotate,
                                 timeout=annotate_timeout, poll=poll,
                                 proc=proc)
    try:
        returncode = proc.wait()
    except Exception:
        returncode = None
    finally:
        with _CHILDREN_LOCK:
            _CHILDREN.pop(session_id, None)
    if returncode is None:
        return None
    state = terminal_state_for(returncode)
    rec = read_record_raw(session_id)
    if rec is not None and rec.get("state") not in ("running", "lost"):
        return rec.get("state")          # its owner already decided
    record_terminal_state(session_id, state, exit_summary(returncode))
    return state


def start_reaper(session_id: str, proc: subprocess.Popen,
                 annotate: dict | None = None,
                 annotate_timeout: float = 15.0) -> threading.Thread:
    """One daemon thread per spawned child: reaps it and finalizes the ledger."""
    with _CHILDREN_LOCK:
        _CHILDREN[session_id] = proc
    t = threading.Thread(
        target=reap_session, args=(session_id, proc),
        kwargs={"annotate": annotate, "annotate_timeout": annotate_timeout},
        name=f"chat-reap-{session_id}", daemon=True)
    t.start()
    return t


def read_record_raw(session_id: str) -> dict | None:
    """Read a ledger record WITHOUT the reconcile-on-read side effect.

    sessions.get() PERSISTS the `lost` transition it infers. For a path that
    is about to record the real terminal state that write is a race we lose:
    finalize() is a compare-and-set and `lost` is terminal, so whoever writes
    first wins — and an operator stop would be permanently filed as a crash.
    sessions.reconcile() is pure; only the read wrapper writes. So read the
    file (record_path is public API) and reconcile it ourselves.
    """
    try:
        with open(sessions.record_path(session_id)) as f:
            rec = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return rec if isinstance(rec, dict) and rec.get("id") else None


def record_terminal_state(session_id: str, state: str,
                          summary: str | None = None) -> bool:
    """Write a terminal state, correcting a `lost` GUESS if one got in first.

    finalize() refuses to re-decide a record that is already terminal, which
    is right: the first VERDICT wins, and a late atexit must not overwrite
    it. But `lost` is not a verdict — it is the reconciler's inference about
    a record nobody finalized, and it can be persisted by any concurrent
    reader (an attached SSE stream polls four times a second). We are the
    writer that actually knows: we hold the child, or we signalled it and
    watched it go. So a `lost` is corrected, and a done/failed/stopped that a
    session wrote for ITSELF is left exactly as it is.
    """
    try:
        if sessions.finalize(session_id, state, summary=summary):
            return True
    except Exception:
        return False
    rec = read_record_raw(session_id)
    if rec is None:
        return False
    if rec.get("state") == state:
        return True                      # it landed (older finalize -> None)
    if rec.get("state") != "lost":
        return False                     # a real verdict; leave it alone
    with contextlib.suppress(Exception):
        sessions.touch(session_id, state=state,
                       summary=summary if summary is not None
                       else rec.get("summary"),
                       ended_at=_now_iso())
    return True


def _still_running(session_id: str) -> bool:
    try:
        rec = read_record_raw(session_id)
        if not rec:
            return False
        rec = sessions.reconcile(rec)    # pure: infers `lost`, writes nothing
        return rec.get("state") == "running"
    except Exception:
        return False


def start_escalation(session_id: str, term_after: float, kill_after: float,
                     poll: float = 0.5) -> threading.Thread:
    """Watch a stopping session and escalate if it does not go quietly.

    An agent's cooperative stop lands at the next turn, which can be a minute
    if it is mid-tool-call. So: ask nicely (the inbox op, written by the
    caller), SIGTERM the group at `term_after`, SIGKILL at `kill_after`. The
    thread exits the moment the session leaves `running`, so the common case
    costs one wakeup per half second for a few seconds and nothing after.

    WHATEVER WE SIGNALLED, THE RECORD LANDS ON `stopped`. Only the SIGKILL
    branch used to finalize, so a process that went quietly on the polite
    signal was reconciled to `lost` — which reads as "it crashed" and
    contradicts the plan's own checklist ("stop during a tool call: process
    gone, ledger `stopped`"). A session nobody here signalled keeps whatever
    terminal state its owner wrote.
    """
    started = time.monotonic()
    done = threading.Event()

    def run():
        sent_term = False
        sent_kill = False
        kill_deadline = None

        def finalize_stopped():
            summary = ("SIGKILL after stop request" if sent_kill
                       else "SIGTERM after stop request")
            record_terminal_state(session_id, "stopped", summary)

        while not done.wait(poll):
            if not _still_running(session_id):
                # It is gone. If we are why, say so; a cooperative exit keeps
                # the terminal state the session wrote for itself.
                if sent_term or sent_kill:
                    finalize_stopped()
                return
            now = time.monotonic()
            elapsed = now - started
            rec = read_record_raw(session_id) or {}
            if sent_kill:
                # SIGKILL cannot be caught, but the record only flips once
                # something reaps the child. Give it a few polls, then record
                # the truth we already know rather than looping forever.
                if kill_deadline is not None and now >= kill_deadline:
                    finalize_stopped()
                    return
                continue
            if elapsed >= kill_after:
                signal_session(rec, signal.SIGKILL)
                sent_kill = True
                kill_deadline = now + max(poll * 4, 0.5)
                continue
            if elapsed >= term_after and not sent_term:
                signal_session(rec, signal.SIGTERM)
                sent_term = True

    t = threading.Thread(target=run, name=f"chat-stop-{session_id}",
                         daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """App factory — every knob is read HERE, at call time, so a test can vary
    the environment without a module reload and without leaking config between
    apps (see tests/test_chat_server.py)."""
    run_dir = (os.environ.get("OPENBEAST_CHAT_RUN_DIR") or "").strip() or RUN_DIR
    # Re-read per check, not captured here: a revoked reader must lose access
    # without a stack restart (E11).
    operators = OperatorList(os.path.join(run_dir, "chat-operators"))
    rate_per_min = max(1, int(os.environ.get("OPENBEAST_CHAT_RATE_PER_MIN") or 60))
    term_after = float(os.environ.get("OPENBEAST_CHAT_STOP_TERM_S") or 30)
    kill_after = float(os.environ.get("OPENBEAST_CHAT_STOP_KILL_S") or 60)
    poll = max(0.01, float(os.environ.get("OPENBEAST_CHAT_POLL_MS") or 250) / 1000.0)
    heartbeat = float(os.environ.get("OPENBEAST_CHAT_HEARTBEAT_S") or 15)
    # An open stream re-authorizes on this period. Capped at the heartbeat so
    # a long-lived attachment is re-checked at least as often as it is poked.
    auth_recheck = float(os.environ.get("OPENBEAST_CHAT_AUTH_RECHECK_S")
                         or min(heartbeat, 5.0))
    port = int(os.environ.get("OPENBEAST_CHAT_PORT") or DEFAULT_PORT)
    allowed_hosts = trusted_hosts(
        os.environ.get("OPENBEAST_CHAT_ALLOWED_HOSTS", ""))

    registry = DeviceRegistry(os.path.join(run_dir, "clients.json"))
    audit_path = os.path.join(run_dir, "chat-audit.jsonl")
    local_token = _mint_local_token(os.path.join(run_dir, "chat-local.token"))

    metrics_lock = threading.Lock()
    counters: dict[tuple, int] = defaultdict(int)
    gauges: dict[str, int] = defaultdict(int)
    rate_hits: dict[str, deque] = defaultdict(deque)
    rate_lock = threading.Lock()

    app = FastAPI(
        title="OpenBeast beast-chat",
        version="1.0",
        description="Sessions API + mobile console (see agents/chat_server.py).",
        # /docs and /redoc were already off; the SCHEMA was not, and it
        # published the whole write contract — routes, bodies, parameters —
        # to a caller 404'd on every one of them.
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    # Half of the rebinding defence (the other half is that identity is now
    # required): a hostile DNS name pointed at 127.0.0.1 is refused here,
    # before any route runs.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.state.local_token = local_token
    app.state.registry = registry
    app.state.run_dir = run_dir
    app.state.port = port
    app.state.operators = operators
    app.state.allowed_hosts = allowed_hosts

    # -- audit -------------------------------------------------------------

    def audit(principal, route: str, session: str | None, outcome: str,
              ms: int, extra: dict | None = None) -> None:
        """Append-only operator trail. Message TEXT never appears here — a
        /send row carries the sha256 and the length, which is enough to prove
        what was sent without the audit log becoming a transcript of it."""
        try:
            row = {
                "ts": _now_iso(),
                "login": (principal or {}).get("login"),
                "device": (principal or {}).get("device"),
                "route": route,
                "session": session,
                "outcome": outcome,
                "ms": ms,
            }
            if extra:
                row.update(extra)
            os.makedirs(os.path.dirname(audit_path), exist_ok=True)
            fd = os.open(audit_path,
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception:
            pass  # the audit trail must never break the request

    def claimed(request: Request) -> dict:
        """Who the caller SAYS they are, before anything is verified.

        Seeded into the audit context BEFORE the gate runs: a denial whose
        row says `login: null` records that somebody probed but not who, and
        the probe is the whole reason this log exists.
        """
        login = (request.headers.get("tailscale-user-login") or "").strip()
        return {"login": login or "anonymous", "device": None,
                "verified": False}

    @contextlib.contextmanager
    def audited(route: str, session: str | None = None,
                request: Request | None = None):
        """Wrap a handler so denials are audited too — a probe from an
        unlisted login is exactly the event this log exists for."""
        t0 = time.monotonic()
        principal = claimed(request) if request is not None else None
        ctx = {"principal": principal, "outcome": "ok", "extra": {},
               "session": session}
        try:
            yield ctx
        except HTTPException as e:
            ctx["outcome"] = f"http_{e.status_code}"
            raise
        except Exception:
            ctx["outcome"] = "error"
            raise
        finally:
            ms = int((time.monotonic() - t0) * 1000)
            audit(ctx["principal"], route, ctx["session"], ctx["outcome"], ms,
                  ctx["extra"])
            with metrics_lock:
                counters[(route, ctx["outcome"])] += 1

    # -- auth --------------------------------------------------------------

    def is_local(request: Request) -> bool:
        presented = request.headers.get("x-openbeast-local", "")
        if not presented:
            return False
        # Compare BYTES: compare_digest on str raises TypeError for any
        # non-ASCII char and Starlette decodes headers as latin-1, so a single
        # hostile 0x80-0xFF byte would otherwise become an unhandled 500.
        return hmac.compare_digest(
            presented.encode("utf-8", "surrogateescape"), local_token.encode())

    def device_for(request: Request) -> dict | None:
        """The enrolled, chat-scoped device behind this request, or None.

        Unknown, revoked and unscoped are all None: the caller learns nothing
        about which keys exist or what they are missing.
        """
        auth = request.headers.get("authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not key:
            key = (request.headers.get("x-openbeast-device-key") or "").strip()
        if not key:
            return None
        dev = registry.lookup(key)
        if dev is None or not _has_scope(dev, "chat"):
            return None
        return dev

    def read_gate(request: Request) -> dict:
        """Identity or nothing. A request with NO credential is 404.

        There is no anonymous read: a transcript is file contents, command
        output and every byte the model has been shown. `tailscale serve`
        injects Tailscale-User-Login on the published deployment; an enrolled
        chat-scoped device key is accepted as identity too (the client CLI
        and curl have no header to be injected into); a caller that can read
        the 0600 token in .run/ is on the box.
        """
        if is_local(request):
            return {"login": "local", "device": "local", "local": True}
        login = (request.headers.get("tailscale-user-login") or "").strip()
        if login and operators.allows(login):
            # Unset operator list = single-user default: any identified login
            # reads. Set = allowlist, and anything else falls through to 404.
            return {"login": login, "device": None, "local": False}
        dev = device_for(request)
        if dev is not None:
            dev_id = dev.get("id") or "device"
            return {"login": login or f"device:{dev_id}", "device": dev_id,
                    "local": False}
        # 404, never 403. A stranger must not learn that beast-chat is here.
        raise HTTPException(status_code=404, detail="Not Found")

    def rate_check(key: str) -> None:
        now = time.monotonic()
        with rate_lock:
            q = rate_hits[key]
            while q and now - q[0] > 60.0:
                q.popleft()
            if len(q) >= rate_per_min:
                raise HTTPException(status_code=429,
                                    detail="write rate limit exceeded")
            q.append(now)

    def write_gate(request: Request, principal: dict) -> dict:
        """Reads already passed. Writes additionally need an enrolled device
        key with the `chat` scope — steering an agent is a different act from
        watching one.

        Missing, unknown, revoked and unscoped all answer 404, identically.
        The old 401-for-missing-key was a membership oracle: a 401 told an
        unlisted prober that the login they had just guessed IS in
        CHAT_OPERATORS, while everyone else got 404.
        """
        if principal.get("local"):
            rate_check("local")
            return principal
        dev = device_for(request)
        if dev is None:
            raise HTTPException(status_code=404, detail="Not Found")
        out = dict(principal)
        out["device"] = dev.get("id") or "device"
        rate_check(out["device"])
        return out

    def load_session(session_id: str) -> dict:
        rec = sessions.get(session_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Not Found")
        return sessions.reconcile(rec)

    # -- console -----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def console(request: Request):
        with audited("GET /", request=request) as ctx:
            ctx["principal"] = read_gate(request)
            try:
                with open(CONSOLE_PATH, encoding="utf-8") as f:
                    html = f.read()
            except OSError:
                raise HTTPException(status_code=500,
                                    detail="console asset missing")
            return HTMLResponse(html, headers={
                "Content-Security-Policy": CSP,
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            })

    # The PWA manifest is inlined in the console as a data: URI (no extra
    # asset to ship), but an icon cannot be nested inside it without double
    # encoding. Serving one route is simpler and it is what makes
    # "Add to Home Screen" produce a real app rather than a screenshot.
    @app.get("/icon.svg")
    def icon():
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect width="64" height="64" rx="14" fill="#141312"/>'
            '<path d="M14 40c0-10 8-18 18-18s18 8 18 18" fill="none" '
            'stroke="#f0913f" stroke-width="5" stroke-linecap="round"/>'
            '<circle cx="24" cy="38" r="3.5" fill="#f0913f"/>'
            '<circle cx="40" cy="38" r="3.5" fill="#f0913f"/>'
            '<path d="M22 48h20" stroke="#8a8279" stroke-width="4" '
            'stroke-linecap="round"/></svg>'
        )
        return PlainTextResponse(svg, media_type="image/svg+xml",
                                 headers={"Cache-Control": "max-age=86400"})

    # -- ledger ------------------------------------------------------------

    @app.get("/api/chat/sessions")
    def list_sessions(request: Request, state: str = "", kind: str = "",
                      limit: str = "200"):
        # `limit` is typed as a STRING on purpose. As `int` FastAPI validated
        # it in the routing layer, so `?limit=abc` returned 422 — before the
        # auth gate ran, and only for a URL that exists. That 422 told an
        # anonymous prober the route was real while every honest request got
        # 404. Coerce after the gate instead.
        with audited("GET /api/chat/sessions", request=request) as ctx:
            ctx["principal"] = read_gate(request)
            state = (state or "").strip() or None
            kind = (kind or "").strip() or None
            if state and state not in sessions.STATES:
                raise HTTPException(status_code=400, detail="unknown state")
            try:
                count = int(str(limit).strip() or "200")
            except (TypeError, ValueError):
                raise HTTPException(status_code=400,
                                    detail="'limit' must be an integer")
            rows = sessions.list_sessions(state=state, kind=kind,
                                          limit=max(1, min(count, 1000)))
            out = []
            for rec in rows:
                rec = sessions.reconcile(rec)
                # reconcile() can move a record out of the filter (a `running`
                # row whose pid is gone becomes `lost`), so re-apply it.
                if state and rec.get("state") != state:
                    continue
                path = rec.get("transcript") or ""
                out.append({
                    **rec,
                    "transcript_bytes": _file_size(path),
                    "last_line": _tail_line(path),
                })
            return {"sessions": out, "count": len(out),
                    "states": list(sessions.STATES)}

    @app.get("/api/chat/sessions/{session_id}")
    def get_session(request: Request, session_id: str):
        with audited("GET /api/chat/sessions/{id}", session_id,
                     request=request) as ctx:
            ctx["principal"] = read_gate(request)
            rec = load_session(session_id)
            status = derive_status(rec)
            return {
                "session": rec,
                "status": status,
                "artifacts": {
                    "events": f"/api/chat/sessions/{session_id}/events",
                    "transcript": rec.get("transcript"),
                    "inbox": rec.get("inbox") or _inbox_path(session_id),
                    "workdir": rec.get("workdir"),
                },
            }

    # -- the stream --------------------------------------------------------

    @app.get("/api/chat/sessions/{session_id}/events")
    def events(request: Request, session_id: str):
        # NOT wrapped in `audited`: the context manager would close (and
        # therefore time) the request before a single byte is streamed. The
        # open is audited explicitly below, and SSE connections are kept OUT
        # of the in-flight gauge on purpose — a phone parked on a stream for
        # an hour is not a request in flight, and counting it there makes
        # every capacity number a lie.
        t0 = time.monotonic()
        try:
            principal = read_gate(request)
        except HTTPException as e:
            audit(claimed(request), "GET /events", session_id,
                  f"http_{e.status_code}",
                  int((time.monotonic() - t0) * 1000))
            with metrics_lock:
                counters[("GET /events", f"http_{e.status_code}")] += 1
            raise
        rec = load_session(session_id)

        # Last-Event-ID WINS over the query param, and that ordering is the
        # whole reason browser auto-reconnect works. A reconnecting
        # EventSource re-requests its ORIGINAL url — `from=` still pinned to
        # wherever the client started an hour ago — and replays the last
        # frame's `id:` in this header. Preferring `from` there would replay
        # the whole session on every radio blip. The header is only ever
        # non-empty on a reconnect (a freshly constructed EventSource has no
        # last event id), so it is always the more current of the two.
        raw_from = request.headers.get("last-event-id")
        if raw_from in (None, ""):
            raw_from = request.query_params.get("from")
        try:
            start = int(raw_from) if raw_from not in (None, "") else 0
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="'from' must be an integer")
        if start < 0:
            raise HTTPException(status_code=400, detail="'from' must be >= 0")

        audit(principal, "GET /events", session_id, "stream_open",
              int((time.monotonic() - t0) * 1000), {"from": start})
        with metrics_lock:
            counters[("GET /events", "stream_open")] += 1
            gauges["sse_open"] += 1

        return StreamingResponse(
            _stream(request, rec, start, principal),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                # nginx and tailscale serve both buffer by default, which
                # turns a live stream into a batch delivery at EOF.
                "X-Accel-Buffering": "no",
                "Content-Security-Policy": CSP,
            },
        )

    def _file_ident(path: str):
        """(device, inode) — the identity of the file we are following.

        A transcript that is rotated or replaced keeps its path and can come
        back SHORTER or LONGER; size alone cannot see that, and following the
        old offset into a new file hands the reader half an event.
        """
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_dev, st.st_ino)

    async def _stream(request: Request, record: dict, start: int,
                      principal: dict):
        session_id = record["id"]
        kind = record.get("kind") or "agent"
        path = record.get("transcript") or ""
        offset = start
        seq = 0
        closing_at = None
        ident = _file_ident(path)
        last_auth = time.monotonic()

        def lost_frame(reason: str, requested: int, size: int) -> str:
            # The llama.cpp OFFSET_LOST case. Rather than 400 a phone that did
            # nothing wrong, say so and restart from zero — the console clears
            # its buffer on this frame.
            return sse_frame("lost", {
                "type": "lost", "requested": requested, "size": size,
                "reason": reason,
                "message": ("transcript is shorter than the requested offset "
                            "— replaying from the start"
                            if reason == "offset_beyond_eof" else
                            "transcript was replaced — replaying from the "
                            "start"),
            })

        try:
            size = _file_size(path)
            if offset > size:
                yield lost_frame("offset_beyond_eof", offset, size)
                offset = 0
            # Reconnect hint for EventSource, sent once before the first
            # frame. Bare `retry:` is a legal SSE field, not a frame.
            yield "retry: 2000\n\n"
            yield sse_frame("hello", {
                "type": "hello", "session": session_id, "kind": kind,
                "state": record.get("state"), "title": record.get("title"),
                "model": record.get("model"), "from": offset, "size": size,
                "transcript": path, "poll_ms": int(poll * 1000),
                "server_time": _now_iso(),
            }, offset)
            last_out = time.monotonic()

            while True:
                if await request.is_disconnected():
                    return

                # E11: a reader revoked while attached must LOSE the stream.
                # An allowlist that only applies at open means removing a
                # login needs a full restart, and the open attachment
                # survives even that.
                now = time.monotonic()
                if now - last_auth >= auth_recheck:
                    last_auth = now
                    try:
                        read_gate(request)
                    except HTTPException as e:
                        audit(principal, "GET /events", session_id,
                              f"revoked_{e.status_code}", 0, {"offset": offset})
                        with metrics_lock:
                            counters[("GET /events", "revoked")] += 1
                        yield sse_frame("end", {
                            "type": "end", "session": session_id,
                            "state": record.get("state"), "offset": offset,
                            "reason": "unauthorized",
                            "summary": "read access was revoked",
                        }, offset)
                        return

                # E6: the shrink/replace guard runs on EVERY pass, not once at
                # open. A transcript truncated mid-stream used to leave the
                # reader's offset past EOF forever — it silently skipped
                # everything written after the truncation and, once the file
                # grew back past the stale offset, handed out half an event.
                size = _file_size(path)
                now_ident = _file_ident(path)
                if ident is None:
                    ident = now_ident     # it did not exist yet at open
                replaced = (now_ident is not None and ident is not None
                            and now_ident != ident)
                if replaced or offset > size:
                    reason = "transcript_replaced" if replaced else "offset_beyond_eof"
                    yield lost_frame(reason, offset, size)
                    ident = now_ident
                    offset = 0
                    closing_at = None
                    continue

                lines, offset = read_lines_from(path, offset)
                for text, end in lines:
                    seq += 1
                    if kind == "agent":
                        name, payload = parse_agent_event(text, end, seq)
                    else:
                        name, payload = "log", {"type": "log", "line": text,
                                                "offset": end, "seq": seq}
                    yield sse_frame(name, payload, end)
                    last_out = time.monotonic()

                if lines:
                    # More may already be waiting; drain before sleeping.
                    closing_at = None
                    continue

                fresh = sessions.get(session_id)
                fresh = sessions.reconcile(fresh) if fresh else record
                state = fresh.get("state")
                if state in TERMINAL_STATES:
                    # Grace pass: the producer writes its last line and THEN
                    # the ledger flips terminal, so ending the instant we see
                    # the flip would drop the `done` event. Wait one more poll
                    # with an empty drain before closing.
                    now = time.monotonic()
                    if closing_at is None:
                        closing_at = now + max(poll * 2, 0.5)
                    elif now >= closing_at:
                        yield sse_frame("end", {
                            "type": "end", "session": session_id,
                            "state": state, "offset": offset,
                            "summary": fresh.get("summary"),
                            "reason": "terminal",
                        }, offset)
                        return

                now = time.monotonic()
                if now - last_out >= heartbeat:
                    # A comment frame: ignored by every SSE parser, but it is
                    # bytes on the wire, which is what keeps tailscale serve /
                    # nginx / a phone radio from idling the connection out.
                    yield f": hb {int(time.time())}\n\n"
                    last_out = now
                await asyncio.sleep(poll)
        finally:
            with metrics_lock:
                gauges["sse_open"] -= 1

    # -- writes ------------------------------------------------------------

    @app.post("/api/chat/sessions/{session_id}/send")
    async def send(request: Request, session_id: str):
        with audited("POST /send", session_id, request=request) as ctx:
            with _inflight(metrics_lock, gauges):
                principal = read_gate(request)
                ctx["principal"] = principal
                principal = write_gate(request, principal)
                ctx["principal"] = principal
                body = await _json_body(request)
                text = body.get("text") or body.get("message") or ""
                if not isinstance(text, str) or not text.strip():
                    raise HTTPException(status_code=400, detail="empty message")
                blob = text.encode("utf-8")
                if len(blob) > MAX_MESSAGE_BYTES:
                    raise HTTPException(status_code=413, detail="message too long")
                rec = load_session(session_id)
                if rec.get("state") in TERMINAL_STATES:
                    raise HTTPException(
                        status_code=409,
                        detail=f"session is {rec.get('state')} — nothing is "
                               f"listening on its inbox")
                op_id = uuid.uuid4().hex[:12]
                sessions.append_op(session_id, {
                    "op": "say",
                    "id": op_id,
                    "text": text,
                    "ts": _now_iso(),
                    "by": principal.get("login"),
                    "device": principal.get("device"),
                })
                # Hash + length only. The whole point of the audit trail is to
                # prove WHO steered an agent and WHEN, not to keep a copy of
                # everything anyone ever typed into their phone.
                ctx["extra"] = {
                    "op": "say", "op_id": op_id,
                    "message_sha256": hashlib.sha256(blob).hexdigest(),
                    "message_len": len(text),
                }
                with metrics_lock:
                    counters[("ops", "say")] += 1
                return {
                    "queued": True,
                    "op_id": op_id,
                    "session": session_id,
                    "state": rec.get("state"),
                    "delivery": "next_turn",
                    # The console shows this verbatim. An agent picks its
                    # inbox up between turns, so "sent" would be a lie while
                    # it is 90 seconds into a tool call.
                    "detail": "queued — lands at the next turn",
                }

    @app.post("/api/chat/sessions/{session_id}/stop")
    async def stop(request: Request, session_id: str):
        with audited("POST /stop", session_id, request=request) as ctx:
            with _inflight(metrics_lock, gauges):
                principal = read_gate(request)
                ctx["principal"] = principal
                principal = write_gate(request, principal)
                ctx["principal"] = principal
                rec = load_session(session_id)
                if rec.get("state") in TERMINAL_STATES:
                    return {"stopped": True, "session": session_id,
                            "state": rec.get("state"),
                            "detail": "already finished"}
                kind = rec.get("kind") or "agent"
                if kind == "agent":
                    # Cooperative first: the agent finishes the tool call it
                    # is inside, writes its own `done`, and the transcript
                    # stays coherent. Escalation only if it does not.
                    op_id = uuid.uuid4().hex[:12]
                    sessions.append_op(session_id, {
                        "op": "stop", "id": op_id, "ts": _now_iso(),
                        "by": principal.get("login"),
                        "device": principal.get("device"),
                    })
                    start_escalation(session_id, term_after, kill_after,
                                     poll=min(1.0, max(0.05, poll)))
                    ctx["extra"] = {"op": "stop", "op_id": op_id,
                                    "escalation": [term_after, kill_after]}
                    with metrics_lock:
                        counters[("ops", "stop")] += 1
                    return {
                        "stopped": True, "queued": True, "op_id": op_id,
                        "session": session_id, "state": rec.get("state"),
                        "delivery": "next_turn",
                        "detail": (f"stop queued — lands at the next turn; "
                                   f"SIGTERM after {int(term_after)}s, "
                                   f"SIGKILL after {int(kill_after)}s"),
                    }
                # Jobs have no inbox and no turn boundary — a shell command
                # cannot be asked politely. Signal the group now and escalate.
                sent = signal_session(rec, signal.SIGTERM)
                start_escalation(session_id, 0.0,
                                 max(1.0, kill_after - term_after),
                                 poll=min(1.0, max(0.05, poll)))
                ctx["extra"] = {"op": "signal", "signal": "SIGTERM",
                                "delivered": sent}
                with metrics_lock:
                    counters[("ops", "signal")] += 1
                return {
                    "stopped": True, "queued": False, "signalled": sent,
                    "session": session_id, "state": rec.get("state"),
                    "delivery": "immediate",
                    "detail": (f"SIGTERM sent to the process group; SIGKILL "
                               f"after {int(max(1.0, kill_after - term_after))}s"),
                }

    @app.post("/api/chat/sessions")
    async def create_session(request: Request):
        with audited("POST /api/chat/sessions", request=request) as ctx:
            with _inflight(metrics_lock, gauges):
                principal = read_gate(request)
                ctx["principal"] = principal
                principal = write_gate(request, principal)
                ctx["principal"] = principal
                body = await _json_body(request)
                kind = _body_str(body, "kind", "agent").strip().lower()
                if kind not in ("agent", "job"):
                    raise HTTPException(status_code=400,
                                        detail="kind must be 'agent' or 'job'")
                workdir = os.path.abspath(os.path.expanduser(
                    _body_str(body, "workdir") or REPO_DIR))
                if not os.path.isdir(workdir):
                    raise HTTPException(status_code=400,
                                        detail=f"workdir does not exist: {workdir}")
                meta_in = body.get("meta")
                if meta_in is not None and not isinstance(meta_in, dict):
                    raise HTTPException(status_code=400,
                                        detail="'meta' must be an object")
                session_id = sessions.new_id(kind)
                # Transcripts live in agents/logs/, NOT under SESSIONS_DIR.
                # Two reasons, both load-bearing: sessions.prune() deletes a
                # session's whole directory, so a transcript stored there
                # would be destroyed with the index that points at it; and
                # mcp_server.start_agent already writes agent-<id>.jsonl
                # here, so check_agent/tail_agent and this console read the
                # same files instead of two divergent archives.
                os.makedirs(LOG_DIR, exist_ok=True)
                if kind == "agent":
                    transcript = os.path.join(LOG_DIR, f"agent-{session_id}.jsonl")
                else:
                    transcript = os.path.join(LOG_DIR, f"job-{session_id}.log")

                if kind == "agent":
                    task = _body_str(body, "task").strip()
                    if not task:
                        raise HTTPException(status_code=400,
                                            detail="agent sessions need a task")
                    title = (_body_str(body, "title") or task[:80]).strip()
                    model = _body_str(body, "model")
                    max_iter = _body_int(body, "max_iter", 200, lo=1, hi=1000)
                    cmd = [sys.executable, RUNNER_PATH,
                           "--log-file", transcript,
                           "--workdir", workdir,
                           "--max-iter", str(max_iter),
                           # The steering opt-in is EXPLICIT ARGV and nothing
                           # else (the env opt-in is gone, and it leaked into
                           # measured eval units through inherited
                           # environments). --session-id also pins the id the
                           # runner registers ITSELF under, which is what
                           # keeps this server from owning that record.
                           "--session-id", session_id,
                           "--steer"]
                    if model:
                        cmd += ["--model", model]
                    base_url = _body_str(body, "base_url")
                    if base_url:
                        cmd += ["--base-url", base_url]
                    context = _body_str(body, "context")
                    if context:
                        cmd += ["--context", context]
                    cmd.append(task)
                    # The WHOLE argv (clipped by the writers below), not the
                    # first six words: the flags that decide what this agent
                    # may do — --session-id, --steer, --max-iter, --model —
                    # all sort after the sixth token.
                    display = " ".join(shlex.quote(c) for c in cmd)
                else:
                    shell_cmd = (_body_str(body, "cmd")
                                 or _body_str(body, "command")).strip()
                    if not shell_cmd:
                        raise HTTPException(status_code=400,
                                            detail="job sessions need a cmd")
                    title = (_body_str(body, "title") or shell_cmd[:80]).strip()
                    model = ""
                    # Equivalent in power to the stack's existing `bash` tool,
                    # and gated by the same class of credential (an enrolled
                    # device key, or proof of being on the box).
                    cmd = ["/bin/bash", "-lc", shell_cmd]
                    display = shell_cmd

                try:
                    # Fresh session => pgid == pid, so stop/escalation can
                    # signal the whole tree rather than orphaning children.
                    if kind == "agent":
                        proc = subprocess.Popen(
                            cmd, cwd=workdir, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
                    else:
                        # 0600, matching scripts/job.sh. A plain open() took
                        # the umask and left every job transcript on the box
                        # world-readable — command output is exactly as
                        # sensitive as the transcript it is quoted into.
                        fd = os.open(transcript,
                                     os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                                     0o600)
                        try:
                            os.fchmod(fd, 0o600)
                        except OSError:
                            pass
                        log = os.fdopen(fd, "ab", buffering=0)
                        try:
                            proc = subprocess.Popen(
                                cmd, cwd=workdir, stdout=log,
                                stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                start_new_session=True)
                        finally:
                            log.close()
                except Exception as e:
                    raise HTTPException(status_code=500,
                                        detail=f"spawn failed: {e}")

                meta = dict(meta_in or {})
                meta.update({"started_by": principal.get("login"),
                             "device": principal.get("device"),
                             "command": display[:500]})

                if kind == "job":
                    # Nothing else writes this record: an API job bypasses
                    # scripts/job.sh entirely, so this server IS its ledger
                    # writer — and its reaper.
                    rec = sessions.register(
                        session_id, kind=kind, title=title, pid=proc.pid,
                        pgid=proc.pid, workdir=workdir, model=model or None,
                        transcript=transcript, meta=meta)
                    start_reaper(session_id, proc)
                else:
                    # E4 — ONE WRITER PER RECORD. The runner registers this id
                    # itself (we passed it --session-id); register()ing it here
                    # too is a full overwrite racing a full overwrite, which
                    # loses started_by/device/command at random AND resets the
                    # runner's consumed-message cursor, replaying the
                    # operator's last instruction into a resumed agent. Wait
                    # for its record, then merge ours in with touch().
                    start_reaper(session_id, proc, annotate=meta)
                    rec = sessions.get(session_id) or {
                        "id": session_id, "kind": kind, "title": title,
                        "pid": proc.pid, "pgid": proc.pid, "state": "running",
                        "workdir": workdir, "model": model or None,
                        "transcript": transcript,
                        "inbox": _inbox_path(session_id), "meta": meta,
                    }
                ctx["session"] = session_id
                # The command is the one action the scope system gates, so it
                # is the one thing this row must carry. Hash + workdir too:
                # the hash survives truncation and proves the exact bytes.
                ctx["extra"] = {
                    "kind": kind, "pid": proc.pid, "workdir": workdir,
                    "command": display[:500],
                    "command_sha256": hashlib.sha256(
                        display.encode("utf-8", "replace")).hexdigest(),
                }
                with metrics_lock:
                    counters[("sessions", kind)] += 1
                return JSONResponse(status_code=201, content={
                    "session": rec or {"id": session_id},
                    "events": f"/api/chat/sessions/{session_id}/events",
                })

    # -- repo conventions --------------------------------------------------

    @app.get("/api/chat/health")
    def health(request: Request):
        # The ONE ungated route, because start.sh / healthcheck.sh probe it
        # from the box with no credential to learn the process is alive. To
        # an unidentified caller that is all it says: liveness. Session
        # counts, the ledger path and the auth posture are a map of the rig
        # and need a read credential like everything else.
        try:
            principal = read_gate(request)
        except HTTPException:
            return {"status": "ok"}
        try:
            live = len(sessions.list_sessions(state="running", limit=1000))
            total = len(sessions.list_sessions(limit=1000))
        except Exception:
            live, total = -1, -1
        return {
            "status": "ok",
            "port": port,
            "sessions_dir": sessions.SESSIONS_DIR,
            "running": live,
            "sessions": total,
            "reads": "operators" if operators.configured else "any-identified",
            "devices": registry.configured,
            "streams": max(0, gauges.get("sse_open", 0)),
            "login": principal.get("login"),
        }

    @app.get("/api/chat/metrics", response_class=PlainTextResponse)
    def metrics(request: Request):
        # Route names, outcome counts and live attachment counts are an
        # operational map of this box. Read credential required, and 404 —
        # not 403 — to everyone else.
        read_gate(request)
        lines = [
            "# HELP openbeast_chat_requests_total Requests by route/outcome",
            "# TYPE openbeast_chat_requests_total counter",
        ]
        with metrics_lock:
            for (route, outcome), n in sorted(counters.items()):
                lines.append(f'openbeast_chat_requests_total{{route="{route}",'
                             f'outcome="{outcome}"}} {n}')
            lines += [
                "# HELP openbeast_chat_streams_open Live SSE attachments",
                "# TYPE openbeast_chat_streams_open gauge",
                f"openbeast_chat_streams_open {max(0, gauges.get('sse_open', 0))}",
                "# HELP openbeast_chat_inflight Non-stream requests in flight",
                "# TYPE openbeast_chat_inflight gauge",
                f"openbeast_chat_inflight {max(0, gauges.get('inflight', 0))}",
            ]
        return "\n".join(lines) + "\n"

    return app


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _inflight(lock, gauges):
    """In-flight accounting for real requests only. SSE never enters here."""
    with lock:
        gauges["inflight"] += 1
    try:
        yield
    finally:
        with lock:
            gauges["inflight"] -= 1


def _body_str(body: dict, key: str, default: str = "") -> str:
    """A string field, or 400. Explicit types: a list where a string belongs
    used to reach shlex/Popen and 500 from deep inside the spawn."""
    value = body.get(key, None)
    if value is None:
        return default
    if not isinstance(value, str):
        raise HTTPException(status_code=400,
                            detail=f"'{key}' must be a string")
    return value


def _body_int(body: dict, key: str, default: int, *, lo: int, hi: int) -> int:
    """An integer field, CLAMPED to [lo, hi].

    `max_iter: "abc"` used to raise ValueError inside the handler and answer
    500; `max_iter: -5` was accepted verbatim and handed to the runner, where
    a negative budget means the loop never runs. Junk is a 400, an
    out-of-range number is clamped (a phone typing 100000 wants "lots", not
    an error).
    """
    value = body.get(key, None)
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise HTTPException(status_code=400,
                            detail=f"'{key}' must be an integer")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise HTTPException(status_code=400,
                            detail=f"'{key}' must be an integer")
    return max(lo, min(hi, number))


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    return body


def _inbox_path(session_id: str) -> str | None:
    try:
        return sessions.inbox_path(session_id)
    except Exception:
        return None


def _tail_line(path: str, window: int = 8192) -> str:
    """Last non-empty line, read from the tail — a transcript can be large and
    the session LIST renders one of these per row."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - window))
            chunk = f.read()
    except OSError:
        return ""
    for raw in reversed(chunk.split(b"\n")):
        text = raw.decode("utf-8", "replace").strip()
        if text:
            return text[:500]
    return ""


def main() -> None:
    import uvicorn
    host = os.environ.get("OPENBEAST_CHAT_BIND", "127.0.0.1")
    port = int(os.environ.get("OPENBEAST_CHAT_PORT") or DEFAULT_PORT)
    print(f"OpenBeast beast-chat on {host}:{port} "
          f"(sessions: {sessions.SESSIONS_DIR})")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
