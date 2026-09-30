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
  OPENBEAST_CHAT_BIND          bind address           (default 127.0.0.1;
                               off loopback, the login header is ignored
                               from non-loopback peers — device keys only)
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
  OPENBEAST_CHAT_SOCKET        also listen on this Unix socket (0600)
  OPENBEAST_CHAT_LOGIN_FROM    loopback (default) | unix — where a
                               Tailscale-User-Login header counts
  OPENBEAST_CHAT_AUDIT_MAX_MB  rotate chat-audit.jsonl past this (default 50)
  OPENBEAST_CHAT_AUDIT_DENIALS_PER_MIN  unverified denial rows per peer (60)
  OPENBEAST_CHAT_NOTIFY_URL    ntfy-compatible topic URL; empty = no alerts
  OPENBEAST_CHAT_NOTIFY_ON     states that alert (default failed,lost,done)
  OPENBEAST_CHAT_NOTIFY_TOKEN_FILE  bearer token for the notify URL (a file,
                               never argv or env)
  OPENBEAST_CHAT_NOTIFY_PERIOD_S  ledger diff period (default 5)
  OPENBEAST_CHAT_PUBLIC_URL    console URL for deep links (default: detected
                               from `tailscale serve status`, :8445)
  OPENBEAST_CHAT_SLOT_URL      beast-slot URL for the model picker
                               (default http://127.0.0.1:$DASHBOARD_PORT/api/slot)
  OPENBEAST_CHAT_GPU_LEASE     GPU lease file (default .run/gpu.lease)
  OPENBEAST_CHAT_SCOPE         off = never wrap spawns in systemd-run (tests)
  OPENBEAST_CHAT_LOG_DIR       transcript dir for spawned sessions (tests)
  <run_dir>/chat-presets.json  operator-authored job presets (0600)
"""
import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               Response, StreamingResponse)
from starlette.middleware.trustedhost import TrustedHostMiddleware

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import sessions  # noqa: E402  frozen contract — agents/sessions.py

REPO_DIR = os.path.dirname(_HERE)
RUN_DIR = os.path.join(REPO_DIR, ".run")
CONSOLE_PATH = os.path.join(_HERE, "chat_ui", "console.html")
RUNNER_PATH = os.path.join(_HERE, "runner.py")
# API jobs run under the same supervisor `job.sh run` uses (see create_session).
JOB_SH_PATH = os.path.join(REPO_DIR, "scripts", "job.sh")
# Where transcripts are archived. Matches agents/runner.py
# DEFAULT_LOG_DIR and mcp_server._LOG_DIR — one archive, not three.
LOG_DIR = os.path.join(_HERE, "logs")


def _log_dir() -> str:
    """LOG_DIR, unless OPENBEAST_CHAT_LOG_DIR moves it (a test server run as
    a separate process must never write into the repo's agents/logs/)."""
    return (os.environ.get("OPENBEAST_CHAT_LOG_DIR") or "").strip() or LOG_DIR

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
# The longest operator message an agent actually RECEIVES: sessions.py clips
# every op to OP_MAX_TEXT and the runner clips again to the same figure (its
# _SAY_MAX_CHARS — runner.py is era-locked, so this side conforms). /send used
# to accept 32 KiB, answer "queued — lands at the next turn", and deliver the
# first 4000 characters: a 10 KB instruction ending "FINAL INSTRUCTION: do not
# delete anything" arrived without its last line and without any marker the
# model could see. A message the agent cannot receive whole is now refused.
MAX_MESSAGE_CHARS = sessions.OP_MAX_TEXT
# How long POST /api/chat/sessions waits for the child to register itself
# before answering with a provisional record.
REGISTER_WAIT_S = 5.0
# Longest caller-controlled string (a claimed login, a device id) an audit row
# will carry. The login header alone can be ~16 KB (the h11 header limit).
AUDIT_FIELD_MAX = 256
# Ledger meta keys the SERVER owns. A caller may attach free-form meta to a
# session it starts; it may not attach these, because they are load-bearing
# for liveness (`pid_start`, `boot_id`) and for steering (`cursor`). The list
# lives in sessions.py beside the code that writes them: this copy omitted
# boot_id after [54] added it, and a caller's boot_id — merged over the
# runner's own by annotate_when_registered — bore a live agent as `lost`.
RESERVED_META = sessions.SERVER_OWNED_META
# How much transcript one read may pull. The SSE reader used to do an
# uncapped f.read() from the requested offset, and every replay-from-zero —
# a fresh page load, the Replay button, or the mid-stream `lost` reset —
# pulled a whole job transcript into one bytes object (plus a tuple per
# line) while the event loop was blocked, starving every other attached
# stream and /api/chat/health with it. Matches sessions.INBOX_MAX_READ.
STREAM_MAX_READ = 256 * 1024
# A producer that never emits a newline must not wedge the reader. Past
# this, the chunk is emitted as one line and the offset advances over it —
# the same escape agents/mcp_server.py uses for tail_transcript.
STREAM_MAX_LINE = 1024 * 1024

# The console is entirely self-contained; say so in a header so a stray
# <script src> or webfont can never start working by accident.
CSP = ("default-src 'none'; "
       "img-src 'self' data:; "
       "style-src 'unsafe-inline'; "
       "script-src 'unsafe-inline'; "
       "connect-src 'self'; "
       "manifest-src 'self'; "
       "worker-src 'self'; "
       "base-uri 'none'; "
       "form-action 'none'; "
       "frame-ancestors 'none'")


def _peer_is_loopback(request) -> bool:
    """May this connection's peer assert an identity by HEADER?

    Tailscale-User-Login is a credential only because `tailscale serve` is
    the one thing that can set it — it strips client copies and dials us
    from 127.0.0.1. OPENBEAST_CHAT_BIND is an operator knob, and set to
    0.0.0.0 or a LAN/tailnet address it let any host that could reach the
    port send `Host: localhost` + any login and read every transcript. The
    header therefore counts only from a loopback peer (or a Unix socket,
    which has no address and is on this box by construction). A device key
    and the locality token are secrets, not claims, and work from anywhere.

    Loopback is necessary, never sufficient: `tailscale serve` makes every
    remote caller loopback, which is why LOCAL is the token (see module
    docstring) and not this. Anything that is not an IP literal fails closed.
    """
    client = getattr(request, "client", None)
    if client is None:
        return True
    try:
        addr = ipaddress.ip_address((client.host or "").split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    return bool(addr.is_loopback or (mapped is not None and mapped.is_loopback))


def login_source() -> str:
    """OPENBEAST_CHAT_LOGIN_FROM: 'loopback' (default) or 'unix'."""
    v = (os.environ.get("OPENBEAST_CHAT_LOGIN_FROM") or "loopback").strip().lower()
    return "unix" if v == "unix" else "loopback"


def _unix_listener(path: str):
    """Bind the login-bearing Unix socket: 0600 in a 0700 directory.

    tailscaled runs as root, so 0600 costs it nothing; every OTHER local
    user, and every container, is kept out by the mode — which is the whole
    point of moving the login header off TCP loopback. A stale socket from
    a previous run is replaced; anything else at the path is left alone.
    """
    import socket
    import stat as _st
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        st = os.lstat(path)
        if _st.S_ISSOCK(st.st_mode):
            os.unlink(path)
        else:
            raise SystemExit(f"ERROR: {path} exists and is not a socket — "
                             f"refusing to replace it")
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        sock.bind(path)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    sock.listen(128)
    return sock


def _bind_is_loopback(host: str) -> bool:
    """Is a bind address loopback-only? A NAME counts only if it is
    `localhost`; anything that has to be resolved is treated as off-box."""
    h = (host or "").strip().strip("[]")
    if h.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


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

      OPENBEAST_CHAT_OPERATORS   read from THIS PROCESS's environment at
                                 check time — which conf.sh fixed at start,
                                 so an openbeast.conf edit needs a restart
      <run_dir>/chat-operators   one login per line, `#` comments, stat-gated
                                 (the hot-revocable half)
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


# The rebinding allowlist now lives in agents/hostpolicy.py so beast-artifact
# shares this exact list instead of carrying a second copy that can drift.
# Re-exported here because this is where callers have always found it.
from hostpolicy import trusted_hosts  # noqa: E402,F401


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

    BOUNDED at STREAM_MAX_READ bytes per call. The caller's loop already
    re-reads without sleeping while lines keep coming, so a big backlog is
    paged rather than slurped; the returned offset is the exact resume point,
    which is all a capped read needs to be correct.
    """
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            buf = f.read(STREAM_MAX_READ)
            if buf and b"\n" not in buf and len(buf) >= STREAM_MAX_READ:
                # No line ending in the whole window. Grow ONCE, then give up
                # and treat the chunk as a line — otherwise a producer writing
                # a single enormous line (or only \r) makes this reader return
                # nothing, forever, at the same offset.
                buf += f.read(STREAM_MAX_LINE - STREAM_MAX_READ)
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
    if not out and len(buf) >= STREAM_MAX_LINE:
        # The long-line escape. Emit it and advance past it: a stream that
        # cannot move is worse than one line that arrives unterminated.
        return ([(buf.decode("utf-8", "replace").rstrip("\r"),
                  offset + len(buf))], offset + len(buf))
    return out, offset + start


def tail_start(path: str, tail: int) -> int:
    """Byte offset of the first WHOLE line within the last `tail` bytes.

    0 when the transcript is no bigger than that. The byte before the
    window decides: if it is a newline the window starts on a line; if not,
    skip forward past the next one. A window with no newline at all (one
    enormous line) starts at 0 — the reader's long-line escape handles it.
    """
    size = _file_size(path)
    if tail <= 0 or size <= tail:
        return 0
    pos = size - tail
    try:
        with open(path, "rb") as f:
            f.seek(pos - 1)
            chunk = f.read(min(STREAM_MAX_LINE, size - pos + 1))
    except OSError:
        return 0
    nl = chunk.find(b"\n")
    if nl < 0:
        return 0
    return pos + nl                     # (pos - 1) + nl + 1


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
    # [54] pid_start is ticks since BOOT, so across a reboot the equality
    # below compares two different clocks and can match by coincidence —
    # precisely the killpg-a-stranger case this function exists to prevent.
    # Checking it in sessions.is_alive alone would fix the status column and
    # leave the signal path open, because this function reads meta itself.
    if sessions.from_another_boot(record):
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


def _proc_table() -> dict[int, tuple[int, int, int]]:
    """{pid: (ppid, pgid, start)} for every process /proc will show us."""
    table: dict[int, tuple[int, int, int]] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return table
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                raw = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        fields = raw.rpartition(")")[2].split()
        # After comm: state=0, ppid=1, pgrp=2, ..., starttime=19.
        if len(fields) < 20:
            continue
        try:
            table[int(name)] = (int(fields[1]), int(fields[2]),
                                int(fields[19]))
        except ValueError:
            continue
    return table


def _descendant_groups(pid: int, session_pgid: int) -> list[tuple[int, int]]:
    """Process groups LED by a descendant of `pid`, outside its own group.

    Why this exists: agents/tools.py runs every bash-tool command with
    start_new_session=True, so the command sits in a group of its own, and
    the only thing enforcing its timeout is the runner's proc.wait(). Killing
    the runner's group — what Stop escalates to when the cooperative stop
    cannot land mid-tool-call — left that command running as an orphan with
    no timeout at all, holding its ports, RAM or GPU. The tree has to be
    read BEFORE the runner dies: after, the orphan's parent is the subreaper
    and nothing links it to this session any more.

    Only groups a descendant LEADS (pgid == its own pid) are returned, so a
    descendant that joined somebody else's group can never make us signal
    that group. Each entry carries the leader's start time so the caller can
    refuse a pid recycled between this snapshot and the signal.
    """
    table = _proc_table()
    children: dict[int, list[int]] = defaultdict(list)
    for p, (ppid, _pg, _st) in table.items():
        children[ppid].append(p)
    out: list[tuple[int, int]] = []
    seen = {pid}
    stack = list(children.get(pid, ()))
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        stack.extend(children.get(p, ()))
        _ppid, pg, start = table[p]
        if pg == p and pg != session_pgid and pg > 1:
            out.append((pg, start))
    return out


def _signal_descendant_groups(groups: list[tuple[int, int]], sig: int) -> None:
    own = os.getpgrp()
    for pg, start in groups:
        if pg == own:
            continue
        try:
            if sessions.pid_start_time(pg) != start:
                continue                 # gone, or a recycled pid: not ours
            os.killpg(pg, sig)
        except (ProcessLookupError, PermissionError, OSError):
            continue


def signal_session(record: dict, sig: int) -> bool:
    """Signal a session's process GROUP, falling back to the bare pid.

    The group is what matters: a runner that shelled out leaves children, and
    SIGTERM to the leader alone orphans them. Refuses to signal pgid <= 1 or
    our own group — a ledger record with a garbage pgid must not be able to
    take down this server or init — refuses entirely unless the recorded pid
    is still the process we registered (see signal_identity_ok), and signals
    the GROUP only when the session actually LEADS it (pgid == pid). A session
    that merely belongs to a group gets a bare-pid signal: killing a group
    this session did not create means killing processes that are not ours.
    """
    if not isinstance(record, dict) or not signal_identity_ok(record):
        return False
    pid = _int_or_zero(record.get("pid"))
    pgid = _int_or_zero(record.get("pgid") or record.get("pid"))
    # The session's tree reaches past its own group (tool commands run in
    # sessions of their own — see _descendant_groups). Snapshot it while the
    # parent links still exist; signal it after the session itself.
    try:
        subtree = _descendant_groups(pid, pgid)
    except Exception:
        subtree = []
    try:
        return _signal_session_group(pid, pgid, sig)
    finally:
        _signal_descendant_groups(subtree, sig)


def _signal_session_group(pid: int, pgid: int, sig: int) -> bool:
    # LEADERSHIP, not just membership. `pgid == pid` is what makes this pid the
    # group's leader, and it is the only case where killing the group is
    # killing *this session's* tree. Every intended producer satisfies it by
    # construction: scripts/job.sh turns on `set -m` specifically so its
    # supervisor is a group leader, and the console's own spawns use
    # start_new_session and record pgid=pid.
    #
    # What this rejects is a session that registered itself into SOMEONE
    # ELSE'S group: agents/runner.py calls sessions.register() with no pgid,
    # so sessions.py fills in os.getpgid(pid) — the group the process BELONGS
    # to. Start such an agent from a non-interactive script (no job control,
    # so the child inherits the script's group) and a Stop from the phone
    # killpg'd the script and every sibling it had — on this rig, a campaign
    # and all its stages. The old guard compared the live pgid to the recorded
    # one, which catches a RECYCLED pgid but passes a non-led one trivially,
    # because the process really is in that group. Found in the v1.4.0 review.
    leads_group = pgid == pid
    if pgid > 1 and pgid != os.getpgrp() and leads_group:
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


# ---------------------------------------------------------------------------
# Spawned sessions must not live inside the STACK's systemd unit
# ---------------------------------------------------------------------------
# `./start.sh -d` (what docs/BEAST_CHAT.md tells operators to run) puts the
# supervisor, this server and therefore every session spawned below into ONE
# transient unit. start_new_session=True leaves the process group; it does not
# leave the cgroup. Measured: `systemctl --user stop <unit>` killed a setsid
# child and spared one started through `systemd-run --scope`. Two consequences,
# both contrary to what this feature promises:
#   * a job started from the phone dies on ANY ./stop.sh — including the
#     `./stop.sh && ./start.sh` that update.sh asks for — and a job that
#     itself restarts the stack kills itself halfway through;
#   * the job shares llama-server's MemoryMax, so an OOM kill inside the unit
#     can take the MODEL down: an optional console costing the core stack.
# `systemd-run --user --scope` execs the command in place (the pid Popen
# returns IS the command, so the ledger, the reaper and signal_session are
# unchanged) inside a scope of its own. Probed once, and skipped entirely
# when we are not in a service cgroup or systemd-run cannot reach the user
# manager — a session that starts un-scoped beats one that does not start.

_SCOPE_PREFIX: list[str] | None = None
_SCOPE_LOCK = threading.Lock()
# The parent every console-started scope lands in. Per-scope MemoryMax bounds
# ONE runaway session; two of them at 50% each (or one plus the stack) still
# filled the box, because the scopes shared no parent — the "never the box"
# promise held only for a session running alone. The slice carries the SAME
# cap as an aggregate, so the whole set of phone-started work is bounded.
JOB_SLICE = "openbeast-chat-jobs.slice"


def _in_service_cgroup() -> bool:
    try:
        with open("/proc/self/cgroup", "r", encoding="utf-8") as fh:
            leaf = fh.read().strip().splitlines()[-1].rsplit("/", 1)[-1]
    except (OSError, IndexError):
        return False
    return leaf.endswith(".service")


def _job_mem_max_bytes() -> int:
    """The memory bound each spawned session carries in its own scope.

    Leaving the stack's unit also leaves the stack's MemoryMax, and an
    UNBOUNDED phone-started job is the 2026-07-07 OOM incident again — the one
    start.sh's cap exists to prevent ("a runaway process can only take down
    the stack, never the box"). So every scope gets a cap of its own:
    OPENBEAST_CHAT_JOB_MEM_PCT percent of RAM (default 50; 0 disables).
    The same figure bounds all of them TOGETHER, via JOB_SLICE.
    """
    try:
        pct = int(os.environ.get("OPENBEAST_CHAT_JOB_MEM_PCT") or 50)
    except ValueError:
        pct = 50
    if pct <= 0:
        return 0
    total_kb = 0
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                    break
    except (OSError, ValueError, IndexError):
        total_kb = 0
    return total_kb * 1024 // 100 * min(pct, 100) if total_kb > 0 else 0


def _probe_scope() -> list[str]:
    import shutil
    exe = shutil.which("systemd-run")
    if (os.environ.get("OPENBEAST_CHAT_SCOPE") or "").strip().lower() in (
            "off", "0", "false", "no"):
        return []            # tests, and hosts where transient scopes misbehave
    if not exe or not _in_service_cgroup():
        return []
    prefix = [exe, "--user", "--scope", "--quiet", "--collect"]
    cap = _job_mem_max_bytes()
    if cap:
        # The aggregate bound first: a runtime drop-in on the shared slice
        # (a slice needs no unit file to be loaded, and --runtime keeps it off
        # disk). Only if it took do the scopes go into that slice — a slice
        # without its cap would be a promise with nothing behind it.
        if _cap_job_slice(cap):
            prefix.append(f"--slice={JOB_SLICE}")
        # No swap escape hatch either: a job thrashing swap takes the box's
        # responsiveness with it just as surely as one that fills RAM.
        prefix += ["-p", f"MemoryMax={cap}", "-p", "MemorySwapMax=0"]
    prefix.append("--")
    # The probe carries the SAME properties the real spawn will, so a systemd
    # too old for one of them falls back to a plain spawn instead of failing
    # every job.
    try:
        ok = subprocess.run(prefix + ["true"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        # Once, and out loud: a user bus that is slow at boot silently
        # disabled this for the life of the process.
        print("[beast-chat] systemd-run --scope is unavailable — sessions "
              "started from the console will live inside this unit (they die "
              "with ./stop.sh and share its memory cap)", file=sys.stderr)
    return prefix if ok else []


def _cap_job_slice(cap: int) -> bool:
    """Set MemoryMax/MemorySwapMax on JOB_SLICE; True when it took."""
    import shutil
    exe = shutil.which("systemctl")
    if not exe:
        return False
    try:
        ok = subprocess.run(
            [exe, "--user", "set-property", "--runtime", JOB_SLICE,
             f"MemoryMax={cap}", "MemorySwapMax=0"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        print(f"[beast-chat] could not cap {JOB_SLICE} — each console "
              f"session keeps its own memory cap, but they are not bounded "
              f"together", file=sys.stderr)
    return ok


def scope_prefix() -> list[str]:
    """argv prefix that detaches a spawned session from our unit, or []."""
    global _SCOPE_PREFIX
    with _SCOPE_LOCK:
        if _SCOPE_PREFIX is None:
            _SCOPE_PREFIX = _probe_scope()
        return list(_SCOPE_PREFIX)


# ---------------------------------------------------------------------------
# What a spawned session may inherit (review chat-security-9)
# ---------------------------------------------------------------------------
# start.sh exports the stack's configuration — conf.sh's OPENBEAST_*KEY /
# SECRET / PASSWORD / TOKEN, the identity-JWT signing secret among them — and
# a console-started job inherited all of it, so any job that printed its
# environment (set -x, a crash handler, `env` while debugging) published those
# secrets to every READER of its transcript, a lower tier than the writer who
# started it. The bash tool strips exactly these names for model-authored
# commands; spawned sessions get the same list.

#: Mirrors agents/tools.py _scrubbed_env (era-locked, so read, not edited).
#: Used only if that import fails; tests pin the two to the same answers.
_SECRET_EXACT = frozenset({"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN",
                           "GITHUB_TOKEN", "GH_TOKEN"})
_SECRET_PREFIXES = ("OPENBEAST_", "WEBUI_", "LLAMA_", "SEARXNG_")
_SECRET_MARKS = ("KEY", "SECRET", "PASSWORD", "TOKEN")


def is_secret_env_name(name: str) -> bool:
    up = str(name).upper()
    return up in _SECRET_EXACT or (up.startswith(_SECRET_PREFIXES)
                                   and any(t in up for t in _SECRET_MARKS))


def child_env(extra: dict | None = None, keep: tuple = ()) -> dict:
    """This process's environment minus the stack's secrets.

    `keep` re-admits named variables the child genuinely needs (the runner's
    inference key — it scrubs its own tools' env again before running
    anything the model wrote). `extra` is added last.
    """
    try:
        from tools import _scrubbed_env     # the bash tool's own list
        env = _scrubbed_env()
    except Exception:
        env = {k: v for k, v in os.environ.items()
               if not is_secret_env_name(k)}
    for name in keep:
        if name in os.environ:
            env[name] = os.environ[name]
    env.update(extra or {})
    return env


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
                             proc: subprocess.Popen | None = None,
                             fields: dict | None = None) -> bool:
    """Merge our provenance into a record the CHILD owns (E4).

    We must not register() an id the runner registers for itself: register is
    a full overwrite, so whichever write lands last wins and started_by /
    device / command vanish non-deterministically — and it resets the
    runner's consumed-message cursor, which replays the operator's last
    instruction to a resumed agent. So wait for the owner's record to appear
    and touch() ours in, which merges.

    `fields` are top-level keys merged the same way — the caller's `title`:
    the runner registers itself titled with its task, so a title given to
    POST /api/chat/sessions was echoed in the 201 and then never reached the
    ledger (review chat-browser-9).
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
                # Belt to create_session's strip: this merge lands OVER the
                # runner's own record, so a server-owned key here would
                # replace the runner's real pid_start/boot_id/cursor.
                extra = {k: v for k, v in (fields or {}).items()
                         if k in ("title",) and v}
                sessions.touch(session_id, meta={
                    k: v for k, v in (meta or {}).items()
                    if k not in RESERVED_META}, **extra)
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
                 poll: float = 0.05, fields: dict | None = None,
                 fallback: dict | None = None) -> str | None:
    """Wait on our child, then record the truth. Blocking; see start_reaper.

    Finalizes ONLY from `running`/`lost`: an agent runner writes its own
    terminal state (an operator stop lands `stopped` on a process that then
    exits 0), and the exit status must not overwrite a verdict its owner
    already made. `lost` is the reconciler's guess and is exactly what we are
    here to replace with the exit status.

    `fallback` is the record to file when the child exits WITHOUT ever
    registering (a runner that died on bad input, an import error): the API
    had already answered 201 with a `running` record, and the session then
    simply never existed — no list row, no transcript, no error anywhere
    (review chat-security-6). It lands as `failed` with the exit status.
    """
    registered = True
    if annotate is not None:
        registered = annotate_when_registered(
            session_id, annotate, timeout=annotate_timeout, poll=poll,
            proc=proc, fields=fields)
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
    if rec is None and not registered and fallback:
        with contextlib.suppress(Exception):
            fb = dict(fallback)
            sessions.register(
                session_id, kind=fb.get("kind") or "agent",
                title=fb.get("title") or "", pid=proc.pid, pgid=proc.pid,
                workdir=fb.get("workdir"), model=fb.get("model"),
                transcript=fb.get("transcript"), meta=fb.get("meta") or {})
            sessions.finalize(
                session_id, "failed" if state == "done" else state,
                summary=(f"exited before registering in the ledger "
                         f"({exit_summary(returncode)}) — the command never "
                         f"started as a session"))
        return "failed" if state == "done" else state
    if rec is not None and rec.get("state") not in ("running", "lost"):
        return rec.get("state")          # its owner already decided
    record_terminal_state(session_id, state, exit_summary(returncode))
    return state


def start_reaper(session_id: str, proc: subprocess.Popen,
                 annotate: dict | None = None,
                 annotate_timeout: float = 15.0, *,
                 fields: dict | None = None,
                 fallback: dict | None = None) -> threading.Thread:
    """One daemon thread per spawned child: reaps it and finalizes the ledger."""
    with _CHILDREN_LOCK:
        _CHILDREN[session_id] = proc
    t = threading.Thread(
        target=reap_session, args=(session_id, proc),
        kwargs={"annotate": annotate, "annotate_timeout": annotate_timeout,
                "fields": fields, "fallback": fallback},
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
        if sessions.finalize(session_id, state, summary=summary,
                             override_lost=True):
            return True
    except Exception:
        return False
    rec = read_record_raw(session_id)
    # False from finalize is also "it already says exactly this".
    return bool(rec) and rec.get("state") == state


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
                     poll: float = 0.5, *,
                     already_signalled: bool = False,
                     on_event=None) -> threading.Thread:
    """Watch a stopping session and escalate if it does not go quietly.

    `on_event(dict)` hears every signal this thread actually sends and the
    terminal state it records — the audit trail's view of an escalation,
    which used to exist only as a ledger summary (review chat-security-8).

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
        # The /stop handler SIGTERMs a JOB itself before starting this thread.
        # Seeded False, a job that died on that signal looked like "gone, and
        # not by us": nothing finalized it and the reconciler filed an
        # operator stop as `lost`. (It only showed once a job could outlive
        # the server that held its reaper — which is now the normal case.)
        sent_term = bool(already_signalled)
        term_at = term_after              # local: the retry below moves it
        sent_kill = False
        kill_deadline = None

        def report(**ev):
            if on_event is not None:
                with contextlib.suppress(Exception):
                    on_event(ev)

        def finalize_stopped():
            summary = ("SIGKILL after stop request" if sent_kill
                       else "SIGTERM after stop request")
            record_terminal_state(session_id, "stopped", summary)
            report(outcome="finalized", state="stopped", summary=summary)

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
                delivered = bool(signal_session(rec, signal.SIGKILL))
                report(outcome="signal", signal="SIGKILL", delivered=delivered,
                       pgid=rec.get("pgid"))
                sent_kill = True
                kill_deadline = now + max(poll * 4, 0.5)
                continue
            if elapsed >= term_at and not sent_term:
                # The RETURN VALUE, not True: a session that died on its own
                # in this window was not stopped by us and must not say so.
                sent_term = bool(signal_session(rec, signal.SIGTERM))
                if sent_term:          # failures retry each second: not rows
                    report(outcome="signal", signal="SIGTERM", delivered=True,
                           pgid=rec.get("pgid"))
                if not sent_term:
                    term_at = elapsed + max(poll, 1.0)      # try again shortly

    t = threading.Thread(target=run, name=f"chat-stop-{session_id}",
                         daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# PWA shell: manifest, PNG icons, service worker (feature F-C3)
# ---------------------------------------------------------------------------
# The manifest used to be a data: URL — which several platforms ignore for
# install — and the only icon was an SVG, which iOS does not honour for
# apple-touch-icon, so "Add to Home Screen" on an iPhone produced a
# screenshot tile (review chat-browser-13). The PNGs are rasterised here from
# the same geometry as /icon.svg with nothing but zlib + struct: no Pillow, no
# checked-in binaries, and nothing to fetch on an offline rig.

_ICON_BG = (0x14, 0x13, 0x12)
_ICON_FG = (0xf0, 0x91, 0x3f)
_ICON_MUTED = (0x8a, 0x82, 0x79)
_ICON_SIZES = (180, 192, 512)
_ICON_CACHE: dict[int, bytes] = {}
_ICON_LOCK = threading.Lock()


def _icon_colour(x: float, y: float) -> tuple:
    """The /icon.svg drawing, evaluated at one point of its 64x64 viewBox."""
    import math
    # the grin: an upper half-ring centred (32,40), r 18, stroke 5, round caps
    d = math.hypot(x - 32, y - 40)
    if (abs(d - 18) <= 2.5 and y <= 40) or \
            math.hypot(x - 14, y - 40) <= 2.5 or math.hypot(x - 50, y - 40) <= 2.5:
        return _ICON_FG
    if math.hypot(x - 24, y - 38) <= 3.5 or math.hypot(x - 40, y - 38) <= 3.5:
        return _ICON_FG
    # the chin: a segment (22,48)-(42,48), stroke 4, round caps
    cx = min(max(x, 22.0), 42.0)
    if math.hypot(x - cx, y - 48) <= 2.0:
        return _ICON_MUTED
    return _ICON_BG


def render_icon_png(size: int) -> bytes:
    """A size x size RGB PNG of the console icon (2x2 supersampled)."""
    import struct
    import zlib
    rows = bytearray()
    scale = 64.0 / size
    offs = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))
    for py in range(size):
        rows.append(0)                           # filter: None
        for px in range(size):
            r = g = b = 0
            for ox, oy in offs:
                c = _icon_colour((px + ox) * scale, (py + oy) * scale)
                r += c[0]
                g += c[1]
                b += c[2]
            rows += bytes((r // 4, g // 4, b // 4))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))


def icon_png(size: int) -> bytes:
    with _ICON_LOCK:
        if size not in _ICON_CACHE:
            _ICON_CACHE[size] = render_icon_png(size)
        return _ICON_CACHE[size]


MANIFEST = {
    "name": "OpenBeast beast-chat",
    "short_name": "beast-chat",
    "start_url": "/#/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#141312",
    "theme_color": "#141312",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any maskable"},
        {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml",
         "purpose": "any"},
    ],
}

#: What the service worker may keep: markup and icons, identical for every
#: viewer. NEVER /api/*: a session list or a transcript at rest in a phone's
#: cache outlives the read grant that fetched it.
SHELL_PATHS = ("/", "/manifest.webmanifest", "/icon.svg", "/icon-180.png",
               "/icon-192.png", "/icon-512.png")

SERVICE_WORKER_JS = """'use strict';
// beast-chat service worker. Caches the SHELL only (markup + icons) so the
// console opens on a flaky link; everything under /api/ — the session list,
// transcripts, the event stream — is network-only and never stored.
const CACHE = 'beast-chat-shell-v1';
const SHELL = %s;
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL))
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;          // network only
  const accept = req.headers.get('accept') || '';
  if (accept.indexOf('text/event-stream') >= 0) return;  // never a stream
  const key = req.mode === 'navigate' ? '/' : url.pathname;
  if (SHELL.indexOf(key) < 0) return;
  // Network first, so a new console lands the moment the rig answers; the
  // cached copy only when the network does not.
  e.respondWith(fetch(req).then((res) => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(key, copy));
    }
    return res;
  }).catch(() => caches.match(key)));
});
""" % json.dumps(list(SHELL_PATHS))


# ---------------------------------------------------------------------------
# Rig status strip (feature F-C7)
# ---------------------------------------------------------------------------

def gpu_lease_status(path: str) -> dict:
    """What `scripts/gpu-lease.sh status` says, without running it.

    The lease file is `key=value` lines (pid, start, label, since); it is
    HELD only while that pid is still the process with that start time —
    the same pid+start identity rule as the ledger.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read(4096)
    except OSError:
        return {"state": "free"}
    info = {}
    for line in raw.splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() in ("pid", "start", "label", "since"):
            info[k.strip()] = v.strip()
    pid = _int_or_zero(info.get("pid"))
    want = info.get("start") or ""
    live = False
    if pid > 1 and want:
        try:
            with open(f"/proc/{pid}/stat", "rb") as fh:
                fields = fh.read().decode("utf-8", "replace").rpartition(")")[2].split()
            live = len(fields) > 19 and fields[19] == want
        except OSError:
            live = False
    out = {"state": "held" if live else "stale",
           "label": (info.get("label") or "")[:120],
           "since": (info.get("since") or "")[:40]}
    if live:
        out["pid"] = pid
    return out


def inference_base_url() -> str:
    """The llama-server this rig serves (conf.sh's INFERENCE_URL), no /v1."""
    for name in ("OPENBEAST_INFERENCE_URL", "INFERENCE_URL"):
        v = (os.environ.get(name) or "").strip().rstrip("/")
        if v.startswith(("http://", "https://")):
            return v[:-3] if v.endswith("/v1") else v
    return "http://127.0.0.1:8080"


def probe_http(url: str, timeout: float = 1.5) -> bool:
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, method="GET"), timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Push notifications (feature F-C4)
# ---------------------------------------------------------------------------

NOTIFY_STATES_DEFAULT = ("failed", "lost", "done")
_NOTIFY_TAGS = {"done": "white_check_mark", "failed": "x", "lost": "warning",
                "stopped": "stop_button"}


def _iso_epoch(value) -> float | None:
    try:
        when = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    try:
        return when.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


class Notifier:
    """Tells the operator's phone when a session ENDS — opt-in, off by default.

    Every `period` seconds it diffs the ledger against a persisted snapshot
    (.run/notify-state.json, 0600) and fires on running -> one of `on`. The
    snapshot is what makes a job that ended while this server was down still
    notify on the next start: its last known state was `running`. A session
    that started AND ended entirely while we were down notifies too, if it
    started after the snapshot was last written.

    PAYLOAD RULE: title + state + a deep link. Never transcript text — a
    notification crosses a relay and sits on a lock screen. The session title
    is clipped; the token (CHAT_NOTIFY_TOKEN_FILE) is read at send time and
    never logged or put on argv.
    """

    def __init__(self, url: str, *, on=NOTIFY_STATES_DEFAULT,
                 token_file: str = "", state_path: str,
                 public_url: str = "", min_interval: float = 60.0,
                 burst: int = 10, poster=None):
        self.url = url
        self.on = tuple(s for s in on if s in TERMINAL_STATES)
        self.token_file = token_file
        self.state_path = state_path
        self.public_url = public_url.rstrip("/")
        self.min_interval = min_interval
        self.burst = max(1, burst)
        self.poster = poster or self._post
        self._last_sent: dict[str, float] = {}
        self._last_err = 0.0
        self.lock = threading.Lock()

    @classmethod
    def from_env(cls, run_dir: str, port: int):
        url = (os.environ.get("OPENBEAST_CHAT_NOTIFY_URL") or "").strip()
        if not url:
            return None
        if not url.startswith(("http://", "https://")):
            print("[beast-chat] CHAT_NOTIFY_URL is not an http(s) URL — "
                  "notifications are OFF", file=sys.stderr)
            return None
        raw_on = os.environ.get("OPENBEAST_CHAT_NOTIFY_ON")
        on = ([s.strip().lower() for s in raw_on.split(",") if s.strip()]
              if raw_on else list(NOTIFY_STATES_DEFAULT))
        return cls(url, on=on,
                   token_file=(os.environ.get("OPENBEAST_CHAT_NOTIFY_TOKEN_FILE")
                               or "").strip(),
                   state_path=os.path.join(run_dir, "notify-state.json"),
                   public_url=chat_public_url(port))

    # -- state -------------------------------------------------------------
    def _load(self) -> dict | None:
        try:
            with open(self.state_path) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) else None

    def _save(self, doc: dict) -> None:
        tmp = self.state_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(doc, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    # -- sending -----------------------------------------------------------
    def _token(self) -> str:
        if not self.token_file:
            return ""
        try:
            with open(os.path.expanduser(self.token_file)) as f:
                return f.read().strip()
        except OSError:
            return ""

    def _post(self, body: str, headers: dict) -> bool:
        import urllib.request
        req = urllib.request.Request(self.url, data=body.encode("utf-8"),
                                     method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return 200 <= r.status < 300
        except Exception as e:
            now = time.monotonic()
            if now - self._last_err > 300:       # loud once, not every tick
                self._last_err = now
                host = self.url.split("//", 1)[-1].split("/", 1)[0]
                print(f"[beast-chat] notification to {host} failed "
                      f"({type(e).__name__})", file=sys.stderr)
            return False

    def send(self, *, title: str, body: str, click: str = "",
             tags: str = "", priority: str = "default") -> bool:
        headers = {"Title": _ascii(title)[:120],
                   "Content-Type": "text/plain; charset=utf-8"}
        if click:
            headers["Click"] = _ascii(click)
        if tags:
            headers["Tags"] = _ascii(tags)
        if priority != "default":
            headers["Priority"] = priority
        token = self._token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return bool(self.poster(body, headers))

    def link(self, session_id: str) -> str:
        return f"{self.public_url}/#/s/{session_id}" if self.public_url else ""

    def notify_session(self, rec: dict) -> bool:
        sid = str(rec.get("id") or "")
        state = str(rec.get("state") or "")
        kind = str(rec.get("kind") or "agent")
        title = " ".join(str(rec.get("title") or "").split())
        # The notify URL is often public ntfy.sh (anyone with the topic reads
        # it) and the text lands on a lock screen. A job's default title IS
        # its shell command (console/API: cmd[:80]; job.sh: the first word)
        # and an agent's is its task prompt, so an inline HF_TOKEN=… or
        # Authorization header went out verbatim. A job whose title is just
        # a piece of its command sends no title at all — kind, state and
        # a short id say which one — and every title is scrubbed regardless.
        meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
        raw_cmd = meta.get("command") or ""
        if isinstance(raw_cmd, list):      # job.sh records the argv
            raw_cmd = " ".join(str(c) for c in raw_cmd)
        command = " ".join(str(raw_cmd).split())
        if kind != "agent" and title and command and title in command:
            title = ""
        title = scrub_secrets(title) or f"{kind} {sid[-8:]}"
        if len(title) > 80:
            title = title[:79] + "…"
        return self.send(
            title=f"beast-chat: {kind} {state}",
            body=f"{title} — {state}",
            click=self.link(sid),
            tags=_NOTIFY_TAGS.get(state, ""),
            priority="high" if state in ("failed", "lost") else "default")

    # -- the diff ----------------------------------------------------------
    def tick(self, now: float | None = None) -> list[str]:
        """One pass. Returns the session ids it notified about."""
        with self.lock:
            now = time.time() if now is None else now
            doc = self._load()
            first_run = doc is None
            known = (doc or {}).get("sessions") or {}
            if not isinstance(known, dict):
                known = {}
            last_tick = _iso_epoch((doc or {}).get("last_tick")) or 0.0
            try:
                rows = sessions.list_sessions(limit=5000)
            except Exception:
                return []
            due = []
            current = {}
            for rec in rows:
                sid = str(rec.get("id") or "")
                if not sid:
                    continue
                cur = rec.get("state")
                current[sid] = cur
                prev = known.get(sid)
                if cur not in self.on:
                    continue
                if prev == "running":
                    due.append(rec)
                elif (prev is None and not first_run
                      and (_iso_epoch(rec.get("started_at")) or 0) >= last_tick):
                    due.append(rec)      # started and ended while we were away
            fired = []
            overflow = 0
            mono = time.monotonic()
            for rec in due:
                sid = rec["id"]
                if mono - self._last_sent.get(sid, -1e9) < self.min_interval:
                    continue
                if len(fired) >= self.burst:
                    overflow += 1
                    continue
                self._last_sent[sid] = mono
                if self.notify_session(rec):
                    fired.append(sid)
            if overflow:
                self.send(title="beast-chat: more sessions ended",
                          body=f"{overflow} more session(s) ended — open the "
                               f"console for the list",
                          click=self.public_url + "/#/" if self.public_url else "")
            self._save({"version": 1,
                        "last_tick": datetime.fromtimestamp(now).isoformat(),
                        "sessions": current})
            return fired

    def run_forever(self, period: float, stop: threading.Event) -> None:
        while not stop.wait(period):
            with contextlib.suppress(Exception):
                self.tick()


def _ascii(value: str) -> str:
    """HTTP header values must be latin-1; keep them plain ASCII."""
    return str(value).encode("ascii", "replace").decode("ascii")


_PUBLIC_URL_CACHE = {"at": 0.0, "value": ""}


def chat_public_url(port: int) -> str:
    """Where a phone opens the console — for notification deep links.

    OPENBEAST_CHAT_PUBLIC_URL wins. Otherwise ask `tailscale serve` what it
    publishes on :8445 (--publish-chat); failing that, loopback — honest, if
    only useful on the rig itself.
    """
    v = (os.environ.get("OPENBEAST_CHAT_PUBLIC_URL") or "").strip().rstrip("/")
    if v.startswith(("http://", "https://")):
        return v
    now = time.monotonic()
    if _PUBLIC_URL_CACHE["value"] and now - _PUBLIC_URL_CACHE["at"] < 300:
        return _PUBLIC_URL_CACHE["value"]
    import re
    import shutil
    value = f"http://localhost:{port}"
    exe = shutil.which("tailscale")
    if exe:
        try:
            out = subprocess.run([exe, "serve", "status"], capture_output=True,
                                 text=True, timeout=3).stdout
            m = re.search(r"^https://([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)"
                          r":8445(?=\s|$)", out or "", re.M)
            if m:
                value = f"https://{m.group(1).lower()}:8445"
        except (OSError, subprocess.SubprocessError):
            pass
    _PUBLIC_URL_CACHE.update(at=now, value=value)
    return value


# ---------------------------------------------------------------------------
# Transcript export as a beast-artifact (feature F-C6)
# ---------------------------------------------------------------------------

#: Most transcript bytes an export reads — from the END, so a huge campaign
#: log exports its conclusion rather than its preamble.
EXPORT_MAX_BYTES = 8 * 1024 * 1024
_EXPORT_TEXT_MAX = 20000

_SECRET_ASSIGN_RE = None


def scrub_secrets(text: str) -> str:
    """Redact what the bash tool would never have shown the model.

    Three passes: (1) the VALUE of every secret-named variable in this
    server's environment, wherever it appears — the names come from the bash
    tool's own list (is_secret_env_name mirrors tools._scrubbed_env);
    (2) any NAME=value / NAME: value whose name is secret-shaped, whatever
    process printed it (quoted JSON keys and hyphenated headers too); (3)
    --api-key/--token/--password flags and Authorization credentials.
    """
    import re
    global _SECRET_ASSIGN_RE
    if not text:
        return text
    for name, value in os.environ.items():
        if is_secret_env_name(name) and value and len(value) >= 6:
            text = text.replace(value, f"[redacted:{name}]")
    if _SECRET_ASSIGN_RE is None:
        # The NAME may be quoted (a JSON config a tool printed) and may use
        # hyphens (an HTTP header) — including this stack's own two
        # credentials, X-OpenBeast-Device-Key and X-OpenBeast-Local.
        _SECRET_ASSIGN_RE = re.compile(
            r"(?i)([\"']?)\b([A-Z0-9_-]*(?:API[_-]?KEY|SECRET|PASSWORD|PASSWD"
            r"|TOKEN|DEVICE[_-]KEY|OPENBEAST[_-]LOCAL)[A-Z0-9_-]*)\1"
            r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s\"',;}]+)")
    text = _SECRET_ASSIGN_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}[redacted]",
        text)
    # --api-key VALUE / --token=VALUE / --password VALUE on a command line.
    text = re.sub(
        r"(?i)(--[A-Za-z0-9-]*(?:api-?key|token|password|passwd|secret)"
        r"[A-Za-z0-9-]*)(=|\s+)(\"[^\"]*\"|'[^']*'|[^\s\"']+)",
        r"\1\2[redacted]", text)
    # Authorization: <any scheme> <credential> — token, Basic, Bearer…
    text = re.sub(r"(?i)\b(authorization\s*:\s*[A-Za-z][A-Za-z0-9_-]*)\s+"
                  r"[^\s\"',;]{4,}", r"\1 [redacted]", text)
    text = re.sub(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}", r"\1 [redacted]",
                  text)
    return text


def _read_tail(path: str, limit: int) -> tuple[str, bool]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > limit:
                f.seek(size - limit)
                data = f.read(limit)
                nl = data.find(b"\n")
                data = data[nl + 1:] if nl >= 0 else data
                return data.decode("utf-8", "replace"), True
            return f.read().decode("utf-8", "replace"), False
    except OSError:
        return "", False


def render_transcript_html(record: dict, *, exported_at: str = "") -> str:
    """A static, fully escaped page of one session. No script, no external
    anything: every byte of transcript text goes through scrub_secrets and
    then html.escape, and the page is published PRIVATE."""
    import html as _html

    def e(value, limit=_EXPORT_TEXT_MAX) -> str:
        text = scrub_secrets(str(value if value is not None else ""))
        if len(text) > limit:
            text = text[:limit] + f"\n… [{len(text) - limit} more characters]"
        return _html.escape(text, quote=True)

    sid = str(record.get("id") or "")
    kind = record.get("kind") or "agent"
    state = record.get("state") or "?"
    title = str(record.get("title") or sid)
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    raw, clipped = _read_tail(record.get("transcript") or "", EXPORT_MAX_BYTES)
    parts = []
    if clipped:
        parts.append('<p class="note">Only the last '
                     f'{EXPORT_MAX_BYTES // (1024 * 1024)} MB of the transcript '
                     'is included.</p>')
    if kind != "agent":
        parts.append(f"<pre class=\"log\">{e(raw, EXPORT_MAX_BYTES)}</pre>")
    else:
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                if not isinstance(ev, dict):
                    raise ValueError
            except ValueError:
                parts.append(f'<pre class="raw">{e(line, 2000)}</pre>')
                continue
            t = str(ev.get("type") or "")
            if t in ("start", "spawn"):
                parts.append(f'<div class="ev task"><h3>Task</h3>'
                             f'<div class="body">{e(ev.get("task"))}</div></div>')
            elif t == "iteration":
                parts.append(f'<div class="div">iteration {e(ev.get("number"), 20)}</div>')
            elif t == "assistant":
                parts.append(f'<div class="ev asst"><h3>Assistant</h3>'
                             f'<div class="body">{e(ev.get("content"))}</div></div>')
            elif t == "tool_call":
                try:
                    args = json.dumps(ev.get("args"), ensure_ascii=False, indent=1)
                except (TypeError, ValueError):
                    args = str(ev.get("args"))
                res = ev.get("result")
                more = (' <span class="trunc">result truncated</span>'
                        if isinstance(res, str) and len(res) >= TOOL_RESULT_LIMIT
                        else "")
                parts.append(
                    f'<details class="ev tool"><summary><b>{e(ev.get("name"), 200)}'
                    f'</b>{more}</summary><pre class="args">{e(args, 2000)}</pre>'
                    f'<pre class="res">{e(res, TOOL_RESULT_LIMIT)}</pre></details>')
            elif t == "steer":
                who = ev.get("from") or "operator"
                if ev.get("op") == "say":
                    parts.append(f'<div class="ev steer"><h3>{e(who, 200)} → agent'
                                 f'</h3><div class="body">{e(ev.get("text"))}</div></div>')
                else:
                    parts.append(f'<div class="div">{e(ev.get("op"), 40)} requested '
                                 f'by {e(who, 200)}</div>')
            elif t in ("done", "max_iterations"):
                parts.append(f'<div class="ev done"><h3>'
                             f'{"Complete" if t == "done" else "Max iterations"}'
                             f'</h3><div class="body">{e(ev.get("summary"))}</div></div>')
            elif t in ("error", "context_overflow_unrecoverable"):
                msg = ev.get("error") or ev.get("reason") or ev.get("message")
                parts.append(f'<div class="ev err"><h3>{e(t, 60)}</h3>'
                             f'<div class="body">{e(msg)}</div></div>')
            elif t == "compaction":
                parts.append('<div class="div">context compacted</div>')
            else:
                try:
                    blob = json.dumps(ev, ensure_ascii=False)
                except (TypeError, ValueError):
                    blob = str(ev)
                parts.append(f'<pre class="raw">{e(blob, 2000)}</pre>')
    head = (
        f"<h1>{e(title, 300)}</h1>"
        f'<p class="meta">{e(kind, 20)} · {e(state, 20)} · session '
        f'<code>{e(sid, 100)}</code> · started {e(record.get("started_at"), 40)}'
        + (f" · model {e(record.get('model'), 120)}" if record.get("model") else "")
        + "</p>"
        + (f'<p class="meta">command: <code>{e(meta.get("command"), 500)}</code></p>'
           if meta.get("command") else "")
        + (f'<p class="meta">summary: {e(record.get("summary"), 2000)}</p>'
           if record.get("summary") else "")
        + f'<p class="meta">exported {e(exported_at, 40)} from beast-chat; '
          f'secret-shaped values are redacted.</p>')
    css = (
        ":root{color-scheme:light dark;--fg:#1b1917;--bg:#faf9f7;--mut:#79736b;"
        "--card:#fff;--bd:#e3dfd8;--code:#f3f1ed;--acc:#b4530a}"
        "@media (prefers-color-scheme:dark){:root{--fg:#ecebe8;--bg:#121110;"
        "--mut:#938c83;--card:#1a1918;--bd:#332f2c;--code:#201e1d;--acc:#f0913f}}"
        "body{margin:0 auto;max-width:860px;padding:16px;background:var(--bg);"
        "color:var(--fg);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}"
        "h1{font-size:20px;margin:8px 0}h3{font-size:11px;text-transform:uppercase;"
        "letter-spacing:.05em;color:var(--mut);margin:0 0 4px}"
        ".meta{color:var(--mut);font-size:13px;margin:2px 0}"
        ".ev{background:var(--card);border:1px solid var(--bd);border-radius:10px;"
        "padding:10px 12px;margin:10px 0}.steer{border-color:var(--acc)}"
        ".body{white-space:pre-wrap;overflow-wrap:anywhere}"
        "pre{background:var(--code);border-radius:8px;padding:8px;overflow-x:auto;"
        "white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}"
        ".div{color:var(--mut);font-size:11px;text-transform:uppercase;"
        "text-align:center;margin:12px 0}.trunc{color:var(--acc);font-size:11px}"
        ".note{color:var(--acc)}code{font-size:12px}")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<meta name=\"openbeast-source-session\" content=\"{e(sid, 100)}\">"
            f"<title>Transcript — {e(title, 120)}</title><style>{css}</style>"
            f"</head><body>{head}{''.join(parts)}</body></html>")


def export_artifact_id(session_id: str) -> str:
    """Stable per session: re-exporting adds a version at the same URL."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"openbeast:chat-export:{session_id}"))


def artifact_endpoint() -> tuple[str, str]:
    """(base URL, locality-token path) for beast-artifact's loopback write
    path — the same dial and token rules as scripts/artifact.sh."""
    run = (os.environ.get("OPENBEAST_RUN_DIR") or "").strip() or RUN_DIR
    port = _int_or_zero(os.environ.get("OPENBEAST_ARTIFACT_PORT") or 3004) or 3004
    bind = (os.environ.get("OPENBEAST_BIND") or "").strip()
    if bind in ("", "0.0.0.0", "::", "[::]", "localhost"):
        dial = "127.0.0.1"
    elif ":" in bind and not bind.startswith("["):
        dial = f"[{bind}]"
    else:
        dial = bind
    return f"http://{dial}:{port}", os.path.join(run, "artifact-local.token")


class ExportError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def export_owner_login(principal: dict, allowed) -> str:
    """The login an export should be OWNED by, or "" for the rig default.

    beast-artifact files a locality-token publish under the login header it
    carries (no allowlist, or a listed login) and otherwise under 'local' /
    the first allowlist entry. can_view on a private page is owner-only, so
    publishing without the exporter's login handed the phone that pressed
    Export a link it then got 404 on. Only a VERIFIED tailnet login is
    forwarded: never 'local', never a 'device:<id>' placeholder, never a
    login this server's own operator list refuses, never anything that is
    not a plain header-safe token.
    """
    login = str(principal.get("login") or "").strip()
    if (not principal.get("verified") or principal.get("local")
            or not login or login == "local" or login.startswith("device:")):
        return ""
    if len(login) > 254 or any(ord(c) <= 32 or ord(c) >= 127 for c in login):
        return ""
    try:
        if not allowed(login):
            return ""
    except Exception:
        return ""
    return login


def publish_export(record: dict, owner_login: str = "") -> dict:
    """Render and publish one session PRIVATE; return the store's answer.

    `owner_login` (see export_owner_login) rides along as the login header
    so the page belongs to — and opens for — the operator who exported it.
    """
    import urllib.error
    import urllib.request
    if (os.environ.get("BEAST_ARTIFACT") or os.environ.get("OPENBEAST_BEAST_ARTIFACT")
            or "").strip().lower() != "true":
        raise ExportError(409, "beast-artifact is off — set BEAST_ARTIFACT=true "
                               "in openbeast.conf and restart the stack to "
                               "export transcripts")
    base, token_path = artifact_endpoint()
    try:
        with open(token_path) as f:
            token = f.read().strip()
    except OSError:
        token = ""
    if not token:
        raise ExportError(409, "beast-artifact is not running (no locality "
                               "token in .run/) — start it with ./start.sh")
    sid = str(record.get("id") or "")
    page = render_transcript_html(record, exported_at=_now_iso())
    body = {
        "html": page,
        # The <h1> is scrubbed by the renderer; the store's title (shown in
        # the gallery and the tab) must be too — a job's title is its command.
        "title": scrub_secrets(
            f"Transcript — {str(record.get('title') or sid)[:150]}"),
        "description": scrub_secrets(
            f"beast-chat {record.get('kind') or 'agent'} session "
            f"{sid} ({record.get('state')})"),
        "artifact_id": export_artifact_id(sid),
        "label": f"session {sid}"[:60],
        "visibility": "private",
        # Provenance for the artifact side; an older artifact_server ignores
        # unknown fields, and the page carries it in a <meta> tag regardless.
        "source_session": sid,
    }
    headers = {"Content-Type": "application/json", "X-OpenBeast-Local": token}
    if owner_login:
        headers["Tailscale-User-Login"] = owner_login
    req = urllib.request.Request(
        base + "/api/artifacts", data=json.dumps(body).encode("utf-8"),
        method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("detail")
        except Exception:
            detail = None
        raise ExportError(502, f"beast-artifact refused the export "
                               f"(HTTP {e.code}{': ' + str(detail) if detail else ''})")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ExportError(409, f"beast-artifact is not answering on {base} "
                               f"({type(e).__name__}) — is it running?")
    if not isinstance(out, dict) or not out.get("url"):
        raise ExportError(502, "beast-artifact answered without a URL")
    out["bytes_html"] = len(page.encode("utf-8"))
    return out


# ---------------------------------------------------------------------------
# Operator-authored job presets (feature F-C1)
# ---------------------------------------------------------------------------

PRESET_MAX_BYTES = 256 * 1024
_PRESET_NAME_OK = frozenset("abcdefghijklmnopqrstuvwxyz"
                            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def load_presets(path: str) -> tuple[list[dict], str]:
    """(presets, problem). The file is RCE-by-design configuration — every
    entry is a command the phone can start with one tap — so it is honoured
    only when it is a regular file (not a symlink), owned by this user, and
    not writable or readable by anyone else (0600). Anything else is ignored
    with a reason, never partially trusted.

    Format (docs/BEAST_CHAT.md):
      {"presets": [{"name": "doctor", "title": "openbeast doctor",
                    "cmd": "./scripts/doctor.sh", "workdir": "~/openbeast",
                    "description": "health check"}]}
    """
    import stat as _st
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return [], ""
    except OSError as e:
        return [], f"cannot read {path}: {e.strerror}"
    if not _st.S_ISREG(st.st_mode):
        return [], f"{path} is not a regular file (symlinks are refused)"
    if st.st_uid != os.getuid():
        return [], f"{path} is not owned by this user"
    if st.st_mode & 0o077:
        return [], f"{path} must be mode 0600 (chmod 600 {path})"
    if st.st_size > PRESET_MAX_BYTES:
        return [], f"{path} is larger than {PRESET_MAX_BYTES} bytes"
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        return [], f"{path} is not valid JSON ({type(e).__name__})"
    rows = doc.get("presets") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return [], f"{path} needs a top-level \"presets\" list"
    out, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        cmd = row.get("cmd")
        if (not isinstance(name, str) or not name or len(name) > 64
                or not set(name) <= _PRESET_NAME_OK or name in seen):
            continue
        if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 8192:
            continue
        item = {"name": name, "cmd": cmd.strip()}
        for key, cap in (("title", 200), ("workdir", 1024),
                         ("description", 500)):
            v = row.get(key)
            if isinstance(v, str) and v.strip():
                item[key] = v.strip()[:cap]
        seen.add(name)
        out.append(item)
    return out, ""


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
    login_from = login_source()
    presets_path = os.path.join(run_dir, "chat-presets.json")
    lease_path = (os.environ.get("OPENBEAST_CHAT_GPU_LEASE") or "").strip() or \
        os.path.join((os.environ.get("OPENBEAST_RUN_DIR") or "").strip()
                     or RUN_DIR, "gpu.lease")
    notifier = Notifier.from_env(run_dir, port)
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
    app.state.notifier = notifier

    # -- audit -------------------------------------------------------------

    # [review chat-security-2] Every unauthenticated 404 used to append a
    # row, with the CLAIMED login copied verbatim: one loopback client grew
    # the file ~1.3 GB/hour (x100 with a padded header) on the disk that holds
    # the ledger and the inboxes. Now: identity strings are clipped, denial
    # rows are sampled per peer (the rest become one counted summary row), and
    # the file rotates once it passes a size cap — logrotate is opt-in.
    audit_max = int(float(os.environ.get("OPENBEAST_CHAT_AUDIT_MAX_MB")
                          or 50) * 1024 * 1024)
    denial_budget = max(1, int(os.environ.get("OPENBEAST_CHAT_AUDIT_DENIALS_PER_MIN")
                               or 60))
    denials: dict[str, list] = {}      # peer -> [window_start, rows, dropped]
    denials_lock = threading.Lock()

    def _clip(value, n: int = AUDIT_FIELD_MAX):
        if isinstance(value, str) and len(value) > n:
            return value[:n] + "…"
        return value

    def _admit_denial(peer: str) -> tuple[bool, int]:
        """(write this denial row?, rows dropped in the window that ended)."""
        now = time.monotonic()
        with denials_lock:
            slot = denials.get(peer)
            if slot is None or now - slot[0] >= 60.0:
                dropped = slot[2] if slot else 0
                if len(denials) >= 1024 and peer not in denials:
                    denials.pop(next(iter(denials)))   # bounded: oldest peer out
                denials[peer] = [now, 1, 0]
                return True, dropped
            if slot[1] < denial_budget:
                slot[1] += 1
                return True, 0
            slot[2] += 1
            return False, 0

    def _append_audit(row: dict) -> None:
        os.makedirs(os.path.dirname(audit_path), exist_ok=True)
        try:
            if os.path.getsize(audit_path) >= audit_max:
                # One generation kept, 0600 like the live file. logrotate
                # (scripts/logrotate-openbeast.conf) still does better when
                # installed; this is the floor when it is not.
                os.replace(audit_path, audit_path + ".1")
        except OSError:
            pass
        fd = os.open(audit_path,
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(row) + "\n")

    def audit(principal, route: str, session: str | None, outcome: str,
              ms: int, extra: dict | None = None) -> None:
        """Append-only operator trail. Message TEXT never appears here — a
        /send row carries the sha256 and the length, which is enough to prove
        what was sent without the audit log becoming a transcript of it.

        Every row says WHERE it came from (`peer`, the socket address) and
        whether the identity was checked (`verified`): a denial carrying a
        forged login is otherwise indistinguishable from the operator's own
        phone (review chat-security-8)."""
        try:
            p = principal or {}
            verified = bool(p.get("verified"))
            peer = p.get("peer")
            row = {
                "ts": _now_iso(),
                "login": _clip(p.get("login")),
                "device": _clip(p.get("device")),
                "verified": verified,
                "peer": peer,
                "route": route,
                "session": _clip(session),
                "outcome": outcome,
                "ms": ms,
            }
            if extra:
                row.update(extra)
            if not verified and outcome.startswith("http_4"):
                ok, dropped = _admit_denial(str(peer))
                if dropped:
                    _append_audit({"ts": _now_iso(), "peer": peer,
                                   "verified": False,
                                   "route": "*", "outcome": "denials_suppressed",
                                   "count": dropped})
                if not ok:
                    return
            _append_audit(row)
        except Exception:
            pass  # the audit trail must never break the request

    def _peer_of(request: Request):
        client = getattr(request, "client", None)
        if client is None:
            return "unix"
        return _clip(client.host or "", 64)

    def claimed(request: Request) -> dict:
        """Who the caller SAYS they are, before anything is verified.

        Seeded into the audit context BEFORE the gate runs: a denial whose
        row says `login: null` records that somebody probed but not who, and
        the probe is the whole reason this log exists. CLIPPED: the header is
        caller-controlled and up to ~16 KB.
        """
        login = (request.headers.get("tailscale-user-login") or "").strip()
        return {"login": _clip(login) or "anonymous", "device": None,
                "verified": False, "peer": _peer_of(request)}

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

    def steer_op(op: str, op_id: str, principal: dict, **fields) -> dict:
        """One inbox op, attributed. `from` is the field agents/runner.py
        reads into the transcript's steer event (era-locked, so this side
        conforms); `by` and `device` stay for the audit-minded reader. Only
        `by` used to be written, so every steer in every transcript said
        from:"" and the console could not tell one operator from another
        (review chat-browser-5 / chat-security-7)."""
        login = principal.get("login")
        dev = principal.get("device")
        sender = login if not dev or dev == "local" else f"{login} ({dev})"
        return {"op": op, "id": op_id, "ts": _now_iso(), **fields,
                "from": sender, "by": login, "device": dev}

    def note_stopper(session_id: str, principal: dict) -> None:
        """Who asked for the stop, on the RECORD — the ledger used to say
        `stopped` and nothing about by whom; only the audit log knew."""
        with contextlib.suppress(Exception):
            sessions.touch(session_id, meta={
                "stop_requested_by": principal.get("login"),
                "stop_requested_device": principal.get("device"),
                "stop_requested_at": _now_iso()})

    def escalation_audit(principal: dict, session_id: str):
        """on_event for start_escalation: every signal it actually delivers,
        and the state it records, become audit rows under the stopper's
        identity — not just a ledger summary nobody can attribute."""
        def emit(ev: dict) -> None:
            audit(principal, "stop escalation", session_id,
                  str(ev.get("outcome") or "event"), 0, dict(ev))
        return emit

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

    def login_header_trusted(request: Request) -> bool:
        """May this connection assert an identity by Tailscale-User-Login?

        `loopback` (the default): any loopback peer — which includes every
        process on the box and every host-network container (Open WebUI and
        SearXNG run network_mode: host), none of which can read the 0600
        token files but all of which could claim any login and read every
        transcript (review chat-security-5). `unix`: ONLY a connection that
        arrived on OPENBEAST_CHAT_SOCKET, the 0600 socket `tailscale serve`
        (root) proxies to; TCP loopback then needs the locality token or a
        device key like everyone else.
        """
        if login_from == "unix":
            return getattr(request, "client", None) is None
        return _peer_is_loopback(request)

    def principal_or_none(request: Request):
        """The caller's identity, or None — never raises.

        read_gate() refuses; this one only reports. Used by routes that are
        safe to serve to anyone a trusted Host let through, so the audit trail
        still names who asked.
        """
        try:
            return read_gate(request)
        except HTTPException:
            return None


    def read_gate(request: Request) -> dict:
        """Identity or nothing. A request with NO credential is 404.

        There is no anonymous read: a transcript is file contents, command
        output and every byte the model has been shown. `tailscale serve`
        injects Tailscale-User-Login on the published deployment; an enrolled
        chat-scoped device key is accepted as identity too (the client CLI
        and curl have no header to be injected into); a caller that can read
        the 0600 token in .run/ is on the box.
        """
        peer = _peer_of(request)
        if is_local(request):
            return {"login": "local", "device": "local", "local": True,
                    "verified": True, "peer": peer}
        # Off-box peers cannot claim a login (see _peer_is_loopback); they
        # still get in with a device key, which is a secret, not a claim.
        login = ((request.headers.get("tailscale-user-login") or "").strip()
                 if login_header_trusted(request) else "")
        if login and operators.allows(login):
            # Unset operator list = single-user default: any identified login
            # reads. Set = allowlist, and anything else falls through to 404.
            return {"login": login, "device": None, "local": False,
                    "verified": True, "peer": peer}
        dev = device_for(request)
        if dev is not None:
            dev_id = dev.get("id") or "device"
            return {"login": login or f"device:{dev_id}", "device": dev_id,
                    "local": False, "verified": True, "peer": peer}
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
        # The console DOCUMENT is not gated on identity; every API route it
        # calls still is. A browser cannot put a header on a document request,
        # so gating this returned 404 to an operator opening the console on
        # the rig itself — and bought nothing, because the page ships no
        # session data. It is markup and script, identical for every viewer.
        #
        # The rebinding attack this service defends against is stopped one
        # layer up, by TrustedHostMiddleware: a page that rebinds its own
        # hostname to 127.0.0.1 still sends `Host: attacker.example`, which is
        # refused with a 400 before any handler runs. Requiring a credential
        # here as well was belt-and-braces; requiring it on /api/chat/* is the
        # actual control, and that is unchanged.
        #
        # The audit row still records who asked, so an unauthenticated console
        # load is visible in .run/chat-audit.jsonl.
        with audited("GET /", request=request) as ctx:
            ctx["principal"] = principal_or_none(request)
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

    # A real manifest ROUTE and PNG icons (F-C3 / review chat-browser-13).
    # Ungated like / and /icon.svg: identical markup for every viewer, no
    # session data.
    @app.get("/manifest.webmanifest")
    def manifest():
        return Response(json.dumps(MANIFEST), media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache",
                                 "X-Content-Type-Options": "nosniff"})

    def _png(size: int):
        return Response(icon_png(size), media_type="image/png",
                        headers={"Cache-Control": "max-age=86400",
                                 "X-Content-Type-Options": "nosniff"})

    @app.get("/icon-180.png")
    def icon_180():
        return _png(180)

    @app.get("/icon-192.png")
    def icon_192():
        return _png(192)

    @app.get("/icon-512.png")
    def icon_512():
        return _png(512)

    @app.get("/sw.js")
    def service_worker():
        return Response(SERVICE_WORKER_JS, media_type="application/javascript",
                        headers={"Cache-Control": "no-cache",
                                 "Service-Worker-Allowed": "/",
                                 "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy":
                                     "default-src 'none'; connect-src 'self'"})

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
        resumed = raw_from not in (None, "")
        if raw_from in (None, ""):
            raw_from = request.query_params.get("from")
        try:
            start = int(raw_from) if raw_from not in (None, "") else 0
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="'from' must be an integer")
        if start < 0:
            raise HTTPException(status_code=400, detail="'from' must be >= 0")
        # `tail=N`: start near the END — the last ~N bytes, realigned to a
        # line boundary so the first frame is a whole event. A fresh open used
        # to replay the whole transcript from byte 0, one frame per line, into
        # a view that keeps only the last ~2,400 nodes: a 1 MB campaign log
        # froze a phone for 80 s to show what it then threw away (review
        # chat-lifecycle-console-scroll-thrash / chat-browser-11). A reconnect
        # (Last-Event-ID) and an explicit from= both beat it.
        raw_tail = request.query_params.get("tail")
        tail_skipped = 0
        if raw_tail not in (None, "") and not resumed and \
                request.query_params.get("from") in (None, ""):
            try:
                tail = int(raw_tail)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400,
                                    detail="'tail' must be an integer")
            if tail < 0:
                raise HTTPException(status_code=400,
                                    detail="'tail' must be >= 0")
            start = tail_start(rec.get("transcript") or "", tail)
            tail_skipped = start

        audit(principal, "GET /events", session_id, "stream_open",
              int((time.monotonic() - t0) * 1000), {"from": start})
        with metrics_lock:
            counters[("GET /events", "stream_open")] += 1
            gauges["sse_open"] += 1

        return StreamingResponse(
            _stream(request, rec, start, principal, tail_skipped),
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
                      principal: dict, tail_skipped: int = 0):
        session_id = record["id"]
        kind = record.get("kind") or "agent"
        path = record.get("transcript") or ""
        offset = start
        seq = 0
        closing_at = None
        ident = _file_ident(path)
        last_auth = time.monotonic()
        opened = last_auth

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
                # >0 when tail= started us past the beginning: the console
                # says "showing the end — replay for everything".
                "skipped": tail_skipped if offset == tail_skipped else 0,
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

                # Off the event loop: the read is bounded now, but it is
                # still a blocking syscall inside an async generator, and
                # every OTHER attached stream (plus /api/chat/health, which
                # start.sh and healthcheck.sh probe) waits behind it.
                lines, offset = await asyncio.to_thread(
                    read_lines_from, path, offset)
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
            # What a reader actually pulled, and for how long — the open row
            # alone cannot say whether a revoked or hostile reader drained a
            # whole transcript (review chat-security-8).
            audit(principal, "GET /events", session_id, "stream_close",
                  int((time.monotonic() - opened) * 1000),
                  {"from": start, "offset": offset,
                   "bytes": max(0, offset - start)})

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
                # The runner strips surrounding whitespace before clipping, so
                # the stripped length is what must fit.
                if (len(blob) > MAX_MESSAGE_BYTES
                        or len(text.strip()) > MAX_MESSAGE_CHARS):
                    raise HTTPException(
                        status_code=413,
                        detail=(f"message too long — an agent receives at "
                                f"most {MAX_MESSAGE_CHARS} characters per "
                                f"message (this one is {len(text.strip())}); "
                                f"split it into several"))
                rec = load_session(session_id)
                if rec.get("state") in TERMINAL_STATES:
                    raise HTTPException(
                        status_code=409,
                        detail=f"session is {rec.get('state')} — nothing is "
                               f"listening on its inbox")
                # A JOB has no turn boundary and no inbox reader. Only
                # agents/runner.py reads an inbox — job.sh's supervisor never
                # opens one — so a message sent to a job was appended to a
                # file nothing would ever read, and answered
                # {"queued": true, "detail": "queued — lands at the next
                # turn"}. A silently dropped operator instruction with a
                # positive acknowledgement is the worst of both. The console
                # enabled its composer for jobs too, so this was reachable
                # from the documented phone UI, not just curl.
                if (rec.get("kind") or "agent") != "agent":
                    raise HTTPException(
                        status_code=409,
                        detail="job sessions have no inbox — stop is the "
                               "only action")
                op_id = uuid.uuid4().hex[:12]
                # [48] append_op used to return None whether the op landed or
                # was dropped on a full disk, and this route answered
                # {"queued": true, "detail": "queued — lands at the next
                # turn"} either way. A silently dropped operator instruction
                # with a positive acknowledgement is the shape of bug this
                # route already got fixed for once (jobs, below).
                if not sessions.append_op(session_id,
                                          steer_op("say", op_id, principal,
                                                   text=text)):
                    raise HTTPException(
                        status_code=503,
                        detail="could not write to the session inbox — the "
                               "message was NOT queued (check disk space)")
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
                    # Return value deliberately ignored, unlike /send above:
                    # stop does NOT depend on the inbox. start_escalation
                    # signals the process group on a timer whether or not the
                    # cooperative op was ever read, so a failed write costs a
                    # clean shutdown, not the shutdown. Answering 503 here
                    # would refuse a stop we can still deliver.
                    sessions.append_op(session_id,
                                       steer_op("stop", op_id, principal))
                    note_stopper(session_id, principal)
                    start_escalation(session_id, term_after, kill_after,
                                     poll=min(1.0, max(0.05, poll)),
                                     on_event=escalation_audit(principal,
                                                               session_id))
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
                note_stopper(session_id, principal)
                sent = signal_session(rec, signal.SIGTERM)
                start_escalation(session_id, 0.0,
                                 max(1.0, kill_after - term_after),
                                 poll=min(1.0, max(0.05, poll)),
                                 already_signalled=bool(sent),
                                 on_event=escalation_audit(principal,
                                                           session_id))
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

    def resolve_preset(body: dict) -> dict:
        """`preset: <name>` -> the operator's own cmd/workdir/title for it.

        Resolved HERE, from .run/chat-presets.json, so the phone sends a
        name rather than a shell command; a body naming a preset may not
        also carry its own cmd.
        """
        name = _body_str(body, "preset").strip()
        if not name:
            return {k: v for k, v in body.items() if k != "_preset_title"}
        if _body_str(body, "cmd") or _body_str(body, "command"):
            raise HTTPException(status_code=400,
                                detail="send either 'preset' or 'cmd', not both")
        rows, problem = load_presets(presets_path)
        row = next((r for r in rows if r["name"] == name), None)
        if row is None:
            raise HTTPException(
                status_code=400,
                detail=f"no preset named {name!r}" + (f" ({problem})" if problem else ""))
        out = dict(body)
        out["kind"] = "job"
        out["cmd"] = row["cmd"]
        if row.get("workdir") and not _body_str(body, "workdir"):
            out["workdir"] = row["workdir"]
        out["_preset_title"] = row.get("title") or name
        return out

    def plan_session(body: dict) -> dict:
        """Validate a create request and work out EXACTLY what would run.

        Shared by the real spawn and by `dry_run`, so the argv the console's
        confirm dialog echoes is the argv that executes, byte for byte.
        """
        body = resolve_preset(body)
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
        # Transcripts live in agents/logs/, NOT under SESSIONS_DIR. Two
        # reasons, both load-bearing: sessions.prune() deletes a session's
        # whole directory, so a transcript stored there would be destroyed
        # with the index that points at it; and mcp_server.start_agent
        # already writes agent-<id>.jsonl here, so check_agent/tail_agent and
        # this console read the same files instead of two divergent archives.
        if kind == "agent":
            transcript = os.path.join(_log_dir(), f"agent-{session_id}.jsonl")
        else:
            transcript = os.path.join(_log_dir(), f"job-{session_id}.log")

        given_title = _body_str(body, "title").strip()
        if kind == "agent":
            task = _body_str(body, "task").strip()
            if not task:
                raise HTTPException(status_code=400,
                                    detail="agent sessions need a task")
            # The runner titles its own record task[:200]; say the same
            # thing in the 201 unless the caller named it (then the reaper
            # merges that name into the ledger — review chat-browser-9).
            title = given_title or task[:200]
            model = _body_str(body, "model")
            max_iter = _body_int(body, "max_iter", 200, lo=1, hi=1000)
            cmd = [sys.executable, RUNNER_PATH,
                   "--log-file", transcript,
                   "--workdir", workdir,
                   "--max-iter", str(max_iter),
                   # The steering opt-in is EXPLICIT ARGV and nothing else
                   # (the env opt-in is gone, and it leaked into measured eval
                   # units through inherited environments). --session-id
                   # also pins the id the runner registers ITSELF under,
                   # which is what keeps this server from owning that record.
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
            # `--` ends the runner's options. The task is a nargs='*'
            # positional, so without it a task that starts with '-' was
            # parsed as a FLAG: '--help' printed usage and exited before
            # registering, '--task-file=/etc/x' ran a file as the task —
            # and the API had already answered 201 (review chat-security-6).
            cmd += ["--", task]
        else:
            shell_cmd = (_body_str(body, "cmd")
                         or _body_str(body, "command")).strip()
            if not shell_cmd:
                raise HTTPException(status_code=400,
                                    detail="job sessions need a cmd")
            title = given_title or body.get("_preset_title") or shell_cmd[:80]
            model = ""
            max_iter = None
            # Equivalent in power to the stack's existing `bash` tool, and
            # gated by the same class of credential (an enrolled device key,
            # or proof of being on the box).
            cmd = ["/bin/bash", "-lc", shell_cmd]
        # The WHOLE argv (clipped by the writers below), not the first six
        # words: the flags that decide what an agent may do — --session-id,
        # --steer, --max-iter, --model — all sort after the sixth token.
        display = (" ".join(shlex.quote(c) for c in cmd) if kind == "agent"
                   else cmd[2])
        return {"kind": kind, "session_id": session_id, "workdir": workdir,
                "transcript": transcript, "title": title,
                "given_title": given_title, "model": model or None,
                "max_iter": max_iter, "cmd": cmd, "argv": list(cmd),
                "display": display, "meta_in": meta_in}

    @app.post("/api/chat/sessions")
    async def create_session(request: Request):
        with audited("POST /api/chat/sessions", request=request) as ctx:
            with _inflight(metrics_lock, gauges):
                principal = read_gate(request)
                ctx["principal"] = principal
                principal = write_gate(request, principal)
                ctx["principal"] = principal
                body = await _json_body(request)
                plan = plan_session(body)
                if body.get("dry_run") is True:
                    # What WOULD run — argv, workdir, title — and nothing
                    # spawned. Same gate as the real thing, so the confirm
                    # dialog cannot be used to learn more than a start could.
                    ctx["extra"] = {"dry_run": True, "kind": plan["kind"]}
                    return {"dry_run": True, "kind": plan["kind"],
                            "argv": plan["argv"], "display": plan["display"],
                            "workdir": plan["workdir"], "title": plan["title"],
                            "wrapper": ("scripts/job.sh __supervise"
                                        if plan["kind"] == "job" else None)}
                kind = plan["kind"]
                session_id = plan["session_id"]
                workdir = plan["workdir"]
                transcript = plan["transcript"]
                title = plan["title"]
                model = plan["model"]
                cmd = plan["cmd"]
                display = plan["display"]
                meta_in = plan["meta_in"]
                # 0700 when we are the one creating it (an existing
                # directory's mode is the operator's call, not ours).
                os.makedirs(_log_dir(), mode=0o700, exist_ok=True)

                # The session's own id, for anything it runs (an artifact it
                # publishes can link back here — F-C5), and a scrubbed env.
                env_extra = {"OPENBEAST_SESSION_ID": session_id}
                if kind == "job":
                    # [review chat-lifecycle-api-job-restart-lost] An API job
                    # runs under job.sh's SUPERVISOR, not bare. Bare, this
                    # server was its only ledger writer — the Popen and the
                    # reaper live in memory — so a job that outlived a
                    # chat_server restart (the watchdog, update.sh, a crash)
                    # finished with exit 0 and was filed `lost`, and a stop's
                    # pending SIGKILL died with the escalation thread. The
                    # supervisor registers the job itself, writes
                    # done/failed/stopped whether or not we are alive, and
                    # does its own TERM-then-KILL on a stop. `display` and
                    # the audit row keep the command as asked.
                    # Through /bin/bash rather than the exec bit, which a
                    # copied or re-cloned tree can lose.
                    run = ["/bin/bash", JOB_SH_PATH, "__supervise", session_id,
                           transcript, title, workdir, "--"] + cmd
                else:
                    run = cmd
                try:
                    # Fresh session => pgid == pid, so stop/escalation can
                    # signal this session's group — the supervisor and every
                    # child that has not detached into its OWN session —
                    # rather than orphaning them. Not literally "the whole
                    # tree": a descendant that calls setsid (evals/run_eval.py
                    # does, deliberately, so a task timeout can kill one agent
                    # group) is outside this group and must reap itself on
                    # SIGTERM. Out of the stack's unit (see scope_prefix).
                    # to_thread: the FIRST call probes systemd-run (up to
                    # 10 s on a box with a broken user bus), and this handler
                    # runs ON the event loop — blocking here stalls every SSE
                    # stream and /api/chat/health, which the watchdog reads
                    # as "down". main() also warms it at start.
                    run = await asyncio.to_thread(scope_prefix) + run
                    # 0600 FIRST: the runner and the supervisor both append
                    # with the umask, and a transcript holds tool output, file
                    # contents and fetched pages. An append-open keeps it.
                    _create_private(transcript)
                    if kind == "agent":
                        # The runner needs its inference key; nothing else
                        # secret. It scrubs its own tools' env again.
                        env = child_env(env_extra,
                                        keep=("OPENBEAST_API_KEY",
                                              "OPENAI_API_KEY"))
                    else:
                        env = child_env(env_extra)
                    proc = subprocess.Popen(
                        run, cwd=workdir, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True, env=env)
                except Exception as e:
                    raise HTTPException(status_code=500,
                                        detail=f"spawn failed: {e}")

                # RESERVED KEYS ARE STRIPPED, not merged. `meta` is a
                # free-form caller field that lands in the ledger's meta
                # namespace — where `pid_start` is the process-identity PROOF
                # that stops a recycled pid from making a dead session look
                # alive, and `cursor` is the steering inbox position.
                # sessions.register() only *setdefault*s pid_start, so a
                # caller-supplied `{"meta": {"pid_start": 1}}` won, _alive()
                # then compared 1 against the real /proc start time, and the
                # record reconciled to `lost` while the command ran on:
                # /stop answered "already finished" and never signalled,
                # /send 409'd, and the SSE stream closed — for the whole
                # duration of a 19-hour job. Review of v1.4.0.
                meta = {k: v for k, v in (meta_in or {}).items()
                        if k not in RESERVED_META}
                meta.update({"started_by": principal.get("login"),
                             "device": principal.get("device"),
                             "command": display[:500]})

                # E4 — ONE WRITER PER RECORD. The child registers this id
                # itself: the runner (we passed --session-id) or the job
                # supervisor. register()ing it here too is a full overwrite
                # racing a full overwrite, which loses started_by/device/
                # command at random AND resets a runner's consumed-message
                # cursor. Wait for its record, then merge ours in.
                fallback = {"kind": kind, "title": title, "workdir": workdir,
                            "model": model, "transcript": transcript,
                            "meta": meta}
                start_reaper(session_id, proc, annotate=meta,
                             fields={"title": plan["given_title"]},
                             fallback=fallback)
                # Answer once the child's own record exists (bounded), so a
                # console that routes straight to #/s/<id> finds it — the
                # same promise `job.sh run` makes before it prints an id.
                deadline = time.monotonic() + REGISTER_WAIT_S
                while (time.monotonic() < deadline
                       and read_record_raw(session_id) is None
                       and proc.poll() is None):
                    await asyncio.sleep(0.05)
                rec = sessions.get(session_id) or {
                    "id": session_id, "kind": kind, "title": title,
                    "pid": proc.pid, "pgid": proc.pid, "state": "running",
                    "workdir": workdir, "model": model,
                    "transcript": transcript,
                    "inbox": _inbox_path(session_id), "meta": meta,
                }
                ctx["session"] = session_id
                # The command is the one action the scope system gates, so it
                # is the one thing this row must carry. Hash + workdir too:
                # the hash survives truncation and proves the exact bytes.
                ctx["extra"] = {
                    "kind": kind, "pid": proc.pid, "workdir": workdir,
                    "preset": _body_str(body, "preset") or None,
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

    # -- pause / resume (feature F-C2) -------------------------------------

    async def flow_op(request: Request, session_id: str, op: str):
        route = f"POST /{op}"
        with audited(route, session_id, request=request) as ctx:
            with _inflight(metrics_lock, gauges):
                principal = read_gate(request)
                ctx["principal"] = principal
                principal = write_gate(request, principal)
                ctx["principal"] = principal
                rec = load_session(session_id)
                if rec.get("state") in TERMINAL_STATES:
                    raise HTTPException(
                        status_code=409,
                        detail=f"session is {rec.get('state')} — there is "
                               f"nothing left to {op}")
                if (rec.get("kind") or "agent") != "agent":
                    # Same reason /send refuses: only agents/runner.py reads
                    # an inbox. A job's shell has no turn boundary to pause at.
                    raise HTTPException(
                        status_code=409,
                        detail=f"jobs cannot {op} — only an agent reads an "
                               f"inbox; Stop is the only action for a job")
                op_id = uuid.uuid4().hex[:12]
                # agents/runner.py _apply_steer_ops understands exactly these
                # op names; while paused it keeps polling the inbox, which is
                # how a resume reaches it.
                if not sessions.append_op(session_id,
                                          steer_op(op, op_id, principal)):
                    raise HTTPException(
                        status_code=503,
                        detail=f"could not write to the session inbox — the "
                               f"{op} was NOT queued (check disk space)")
                ctx["extra"] = {"op": op, "op_id": op_id}
                with metrics_lock:
                    counters[("ops", op)] += 1
                return {"queued": True, "op": op, "op_id": op_id,
                        "session": session_id, "delivery": "next_turn",
                        "detail": (f"{op} queued — takes effect at the "
                                   f"agent's next turn boundary")}

    @app.post("/api/chat/sessions/{session_id}/pause")
    async def pause(request: Request, session_id: str):
        return await flow_op(request, session_id, "pause")

    @app.post("/api/chat/sessions/{session_id}/resume")
    async def resume(request: Request, session_id: str):
        return await flow_op(request, session_id, "resume")

    # -- export as an artifact (feature F-C6) ------------------------------

    @app.post("/api/chat/sessions/{session_id}/export")
    async def export(request: Request, session_id: str):
        with audited("POST /export", session_id, request=request) as ctx:
            with _inflight(metrics_lock, gauges):
                principal = read_gate(request)
                ctx["principal"] = principal
                principal = write_gate(request, principal)
                ctx["principal"] = principal
                rec = load_session(session_id)
                try:
                    out = await asyncio.to_thread(
                        publish_export, rec,
                        export_owner_login(principal, operators.allows))
                except ExportError as e:
                    raise HTTPException(status_code=e.status, detail=e.detail)
                ctx["extra"] = {"artifact": out.get("id"),
                                "version": out.get("version"),
                                "bytes": out.get("bytes_html")}
                return {"url": out.get("url"), "id": out.get("id"),
                        "version": out.get("version"), "visibility": "private",
                        "session": session_id}

    # -- new-session sheet helpers (feature F-C1) --------------------------

    @app.get("/api/chat/presets")
    def presets(request: Request):
        with audited("GET /api/chat/presets", request=request) as ctx:
            ctx["principal"] = read_gate(request)
            rows, problem = load_presets(presets_path)
            return {"presets": rows, "path": presets_path,
                    **({"problem": problem} if problem else {})}

    @app.get("/api/chat/models")
    async def models(request: Request):
        with audited("GET /api/chat/models", request=request) as ctx:
            ctx["principal"] = read_gate(request)
            doc = await asyncio.to_thread(fetch_slot)
            model = (doc.get("model") or {}) if isinstance(doc, dict) else {}
            mid = model.get("id") if isinstance(model, dict) else None
            return {"models": [mid] if mid else [], "default": mid,
                    "source": "beast-slot" if doc else None}

    def fetch_slot() -> dict:
        import urllib.request
        url = (os.environ.get("OPENBEAST_CHAT_SLOT_URL") or "").strip() or \
            f"http://127.0.0.1:{_int_or_zero(os.environ.get('DASHBOARD_PORT')) or 3002}/api/slot"
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                doc = json.loads(r.read(256 * 1024) or b"{}")
                return doc if isinstance(doc, dict) else {}
        except Exception:
            return {}

    # -- rig status strip (feature F-C7) -----------------------------------

    rig_cache = {"at": 0.0, "llama": None}

    @app.get("/api/chat/rig")
    async def rig(request: Request):
        with audited("GET /api/chat/rig", request=request) as ctx:
            ctx["principal"] = read_gate(request)
            now = time.monotonic()
            base = inference_base_url()
            if rig_cache["llama"] is None or now - rig_cache["at"] > 5.0:
                rig_cache["llama"] = await asyncio.to_thread(
                    probe_http, base + "/health")
                rig_cache["at"] = now
            try:
                running = len(sessions.list_sessions(state="running",
                                                     limit=1000))
            except Exception:
                running = -1
            host = base.split("//", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1]
            return {"gpu": gpu_lease_status(lease_path),
                    "llama": {"up": bool(rig_cache["llama"]), "host": host},
                    "running": running, "notify": notifier is not None}

    # -- notifications (feature F-C4) --------------------------------------

    @app.post("/api/chat/notify/test")
    async def notify_test(request: Request):
        with audited("POST /notify/test", request=request) as ctx:
            with _inflight(metrics_lock, gauges):
                principal = read_gate(request)
                ctx["principal"] = principal
                principal = write_gate(request, principal)
                ctx["principal"] = principal
                if notifier is None:
                    raise HTTPException(
                        status_code=409,
                        detail="notifications are off — set CHAT_NOTIFY_URL "
                               "(an ntfy topic URL) and restart")
                ok = await asyncio.to_thread(
                    notifier.send, title="beast-chat: test notification",
                    body="If you can read this, session alerts will reach you.",
                    click=notifier.public_url + "/#/" if notifier.public_url else "",
                    tags="bell")
                ctx["extra"] = {"sent": ok}
                if not ok:
                    raise HTTPException(status_code=502,
                                        detail="the notification service did "
                                               "not accept it — check "
                                               "CHAT_NOTIFY_URL and the token")
                return {"sent": True}

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
            "notify": notifier is not None,
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


def _create_private(path: str) -> None:
    """Create `path` empty and 0600 if absent; tighten it to 0600 if not."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    finally:
        os.close(fd)


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


# How long uvicorn waits for open connections on SIGTERM before cancelling
# them. Without a bound the wait is FOREVER, and an SSE stream is a connection
# that never finishes on its own: measured, a server with one phone attached
# ignored stop.sh's SIGTERM indefinitely (it had already closed its listener,
# so the next start bound the port and the old process lingered, still
# streaming to that phone from a stack that was "stopped"). Cancelling the
# stream ends the response; the console's EventSource reconnects by itself and
# resumes from its last offset against whatever is serving then.
GRACEFUL_SHUTDOWN_S = 5


def _prune_ledger_soon(days: int = 30) -> None:
    """sessions.prune() existed and nothing ever called it, so the ledger grew
    by one record per agent or job forever — and every console list request
    reads and reconciles every record. Once per start, off the startup path,
    and never allowed to matter if it fails."""
    def run():
        with contextlib.suppress(Exception):
            # keep_logs: a scripts/job.sh job's <id>.log is its ONLY output.
            # An automatic sweep may forget the index entry; it may not
            # destroy the work.
            sessions.prune(days, keep_logs=True)
    threading.Thread(target=run, name="chat-prune", daemon=True).start()


def _uvicorn_config(app, host: str, port: int):
    """The uvicorn.Config main() serves with — separate so tests load it.

    proxy_headers=False is load-bearing. uvicorn's default trusts
    X-Forwarded-For from 127.0.0.1 and rewrites request.client to it, and
    `tailscale serve` — which dials us from 127.0.0.1 — always sends
    X-Forwarded-For: <tailnet IP>. The app would then see a 100.x peer,
    _peer_is_loopback() would drop Tailscale-User-Login, and every phone on
    the tailnet would get 404 from the console. The auth peer must be the
    real socket peer; nothing here reads the forwarded address.
    """
    import uvicorn
    return uvicorn.Config(app, host=host, port=port, log_level="warning",
                          timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
                          proxy_headers=False, forwarded_allow_ips="")


def main() -> None:
    """Bind the port FIRST, then build the app (which mints the token).

    create_app() rewrites .run/chat-local.token. Built before the bind, a
    second start that was about to fail on a busy port had already replaced
    the LIVE server's token with one nobody honours — the same defect
    agents/artifact_server.py fixed as D18, left open here.
    """
    import socket
    import uvicorn
    host = os.environ.get("OPENBEAST_CHAT_BIND", "127.0.0.1")
    port = int(os.environ.get("OPENBEAST_CHAT_PORT") or DEFAULT_PORT)
    if not _bind_is_loopback(host):
        # Not refused: an enrolled device key is a real credential from any
        # peer. But the published path is `tailscale serve` → 127.0.0.1, and
        # off-box callers can no longer read with a login header alone.
        print(f"WARNING: OPENBEAST_CHAT_BIND={host} is not loopback. "
              f"Tailscale-User-Login is honoured only from 127.0.0.1 (the "
              f"`tailscale serve` path); callers on {host} need an enrolled "
              f"chat-scoped device key.", file=sys.stderr)
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    # IPv4 FIRST. For a NAME, [0] is whatever the resolver lists first —
    # `localhost` gives ::1 on most boxes — and every health probe in this
    # repo speaks to 127.0.0.1, so a v6-only listener is a restart loop.
    infos.sort(key=lambda i: i[0] != socket.AF_INET)
    family, stype, proto, _, addr = infos[0]
    sock = socket.socket(family, stype, proto)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(addr)
        sock.listen(128)
    except OSError as e:
        sock.close()
        print(f"ERROR: cannot bind {host}:{port} ({e}) — another beast-chat "
              f"is probably already running. Its locality token has been "
              f"left alone.", file=sys.stderr)
        raise SystemExit(1)
    sockets = [sock]
    unix_path = (os.environ.get("OPENBEAST_CHAT_SOCKET") or "").strip()
    if unix_path:
        try:
            sockets.append(_unix_listener(unix_path))
        except OSError as e:
            sock.close()
            print(f"ERROR: cannot bind the login socket {unix_path} ({e})",
                  file=sys.stderr)
            raise SystemExit(1)
    if login_source() == "unix" and not unix_path:
        print("WARNING: OPENBEAST_CHAT_LOGIN_FROM=unix but OPENBEAST_CHAT_SOCKET "
              "is unset — no connection can present a tailnet login; only "
              "device keys and the locality token will work.", file=sys.stderr)
    app = create_app()
    _prune_ledger_soon()
    if app.state.notifier is not None:
        try:
            period = max(1.0, float(os.environ.get("OPENBEAST_CHAT_NOTIFY_PERIOD_S")
                                    or 5))
        except ValueError:
            period = 5.0
        threading.Thread(target=app.state.notifier.run_forever,
                         args=(period, threading.Event()),
                         name="chat-notify", daemon=True).start()
    # PNG icons are rasterised once, off the request path.
    threading.Thread(target=lambda: [icon_png(n) for n in _ICON_SIZES],
                     name="chat-icons", daemon=True).start()
    # Warm the systemd-scope probe off the request path (see scope_prefix).
    threading.Thread(target=scope_prefix, name="chat-scope-probe",
                     daemon=True).start()
    print(f"OpenBeast beast-chat on {host}:{port} "
          f"(sessions: {sessions.SESSIONS_DIR})")
    uvicorn.Server(_uvicorn_config(app, host, port)).run(sockets=sockets)


if __name__ == "__main__":
    main()
