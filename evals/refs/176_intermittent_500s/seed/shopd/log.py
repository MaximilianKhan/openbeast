"""Structured line logging.

One line per event::

    2026-03-02T10:15:00.123Z INFO  w2 r000123 http.access method=GET path=/x status=200

The worker name comes from the current thread's name (the server names its
worker threads ``w1``..``wN``); the request id from the per-thread context
that the request-id middleware fills in. Multi-line payloads (tracebacks)
are written as continuation lines indented by two spaces.
"""
from __future__ import annotations

import threading

from .clock import iso

_ctx = threading.local()

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")


def set_request_id(rid: str | None) -> None:
    _ctx.request_id = rid


def current_request_id() -> str | None:
    return getattr(_ctx, "request_id", None)


def _fmt_value(v) -> str:
    s = str(v)
    if s == "" or any(c in s for c in ' "='):
        s = '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


class Logger:
    """Writes formatted lines to a sink: any object with ``write(str)``,
    or a list (lines are appended)."""

    def __init__(self, clock, sink=None, level: str = "INFO", default_worker: str = "w0"):
        self.clock = clock
        self.sink = sink if sink is not None else []
        self.level = LEVELS.index(level)
        self.default_worker = default_worker
        self._lock = threading.Lock()

    def _worker(self) -> str:
        name = threading.current_thread().name
        return name if name.startswith("w") and name[1:].isdigit() else self.default_worker

    def log(self, level: str, event: str, detail: str | None = None, **fields) -> None:
        if LEVELS.index(level) < self.level:
            return
        rid = current_request_id() or "-"
        head = "%s %-5s %s %s %s" % (iso(self.clock.now_ms()), level, self._worker(), rid, event)
        if fields:
            head += " " + " ".join("%s=%s" % (k, _fmt_value(v)) for k, v in fields.items())
        lines = [head]
        if detail:
            lines.extend("  " + ln for ln in detail.rstrip("\n").split("\n"))
        with self._lock:
            for ln in lines:
                if isinstance(self.sink, list):
                    self.sink.append(ln)
                else:
                    self.sink.write(ln + "\n")

    def debug(self, event, detail=None, **f):
        self.log("DEBUG", event, detail, **f)

    def info(self, event, detail=None, **f):
        self.log("INFO", event, detail, **f)

    def warn(self, event, detail=None, **f):
        self.log("WARN", event, detail, **f)

    def error(self, event, detail=None, **f):
        self.log("ERROR", event, detail, **f)
