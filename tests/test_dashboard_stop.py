#!/usr/bin/env python3
"""The dashboard's SIGTERM handler: one run, bounded, never re-entered.

Found 2026-11-23 on spark-1 (GLM serving window): a stop delivers SIGTERM to
the dashboard twice within milliseconds (the supervisor's trap reaps the
extension, then systemd TERMs what is left of openbeast-stack). The second
signal re-entered the handler inside the first, and the nested save deadlocked
on the HistoryStore lock its own outer call held. The process ignored SIGTERM
until systemd's SIGKILL 90 s later, `systemctl --user stop openbeast` timed out,
and the command-center restart of a model switch failed.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

from test_dashboard_v2 import _PATH, load


def test_a_reentered_handler_returns_at_once(monkeypatch):
    """The deadlock, in process: a second SIGTERM arriving while the first
    call holds the HistoryStore lock (inside save_history) must return, not
    wait for that lock."""
    d = load(monkeypatch)
    exits = []
    term = d._term_handler([], exit_=exits.append)
    nested = []

    def save_history_reentered(*a, **kw):
        with d._store._lock:                       # what the outer dump holds
            t0 = time.monotonic()
            term(signal.SIGTERM, None)             # the second signal, same thread
            nested.append(time.monotonic() - t0)
        return 0

    monkeypatch.setattr(d, "save_history", save_history_reentered)
    th = threading.Thread(target=term, args=(signal.SIGTERM, None), daemon=True)
    th.start()
    th.join(5)
    assert not th.is_alive(), "the re-entered SIGTERM handler deadlocked on the history lock"
    assert nested and nested[0] < 0.5
    assert exits == [0], exits                     # the outer call exits once


def test_a_stuck_save_cannot_hold_the_process(monkeypatch):
    """Whatever a save blocks on, the deadline timer exits the process."""
    d = load(monkeypatch)
    monkeypatch.setattr(d, "TERM_SAVE_DEADLINE_S", 0.3)
    exits = []
    block = threading.Event()
    monkeypatch.setattr(d, "save_log", lambda *a, **kw: block.wait(10))
    term = d._term_handler([], exit_=exits.append)
    th = threading.Thread(target=term, args=(signal.SIGTERM, None), daemon=True)
    t0 = time.monotonic()
    th.start()
    while not exits and time.monotonic() - t0 < 5:
        time.sleep(0.02)
    block.set()
    th.join(5)
    assert exits and exits[0] == 0
    assert time.monotonic() - t0 < 3


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed_history(monkeypatch, run):
    """A metric history the size of spark-1's (52 series, ~3 MB): the dump
    then holds the store lock long enough for the second signal to land in it."""
    d = load(monkeypatch)
    store = d.HistoryStore()
    now = time.time()
    for m in range(52):
        name = f"node:spark-1:m{m}"
        for i in range(720):
            store.add(name, (m * 7 + i) % 97 + 0.123456, t=now - 3600 + i * 5)
        for i in range(1440):
            store.add(name, (m + i) % 89 + 0.654321, t=now - 86400 + i * 60)
    assert d.save_history(store, str(run / d.HIST_FILE), now=now) > 1_000_000


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_two_sigterms_stop_the_real_process_promptly(monkeypatch, tmp_path):
    run = tmp_path / ".run"
    run.mkdir()
    _seed_history(monkeypatch, run)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENBEAST_", "DASHBOARD_"))}
    for attempt in range(3):
        port = _free_port()
        env.update(OPENBEAST_REPO_DIR=str(tmp_path), DASHBOARD_PORT=str(port), DASHBOARD_PEERS="",
                   OPENBEAST_BIND="127.0.0.1")
        p = subprocess.Popen([sys.executable, _PATH], env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                with socket.socket() as s:
                    if s.connect_ex(("127.0.0.1", port)) == 0:
                        break
                time.sleep(0.1)
            else:
                pytest.fail("the dashboard did not listen")
            time.sleep(0.5)
            p.send_signal(signal.SIGTERM)          # the supervisor's trap
            time.sleep(0.05)
            p.send_signal(signal.SIGTERM)          # systemd, for the rest of openbeast-stack
            t0 = time.monotonic()
            try:
                rc = p.wait(15)
            except subprocess.TimeoutExpired:
                pytest.fail(f"attempt {attempt + 1}: still running 15 s after two SIGTERMs (the re-entry deadlock)")
            assert rc == 0, rc
            assert time.monotonic() - t0 < 10
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
    # the saves completed: no half-written temp file, and the history still parses
    assert not [f for f in os.listdir(run) if ".tmp." in f], os.listdir(run)
    json.load(open(run / "dashboard-history.json"))


def test_startup_sweeps_temp_files_of_dead_writers(monkeypatch, tmp_path):
    """A writer killed mid-save leaves <state>.json.tmp.<pid>.<thread>; the next start removes those of
    dead pids and leaves a live pid's (its write may be in flight) and every other file alone."""
    d = load(monkeypatch)
    run = tmp_path / ".run"
    run.mkdir()
    dead, live = 999_999_991, os.getpid()
    names = [f"dashboard-power.json.tmp.{dead}.1", f"dashboard-history.json.tmp.{dead}.22",
             f"dashboard-log.json.tmp.{dead}.3", f"dashboard-power.json.tmp.{live}.4",
             "dashboard-power.json", "other.json.tmp.1.2", f"dashboard-power.json.tmp.{dead}.x"]
    for n in names:
        (run / n).write_text("{}")
    assert d.sweep_stale_tmp(str(run), alive=lambda pid: pid == live) == 3
    assert sorted(os.listdir(run)) == sorted(names[3:])
    assert d.sweep_stale_tmp(str(tmp_path / "missing")) == 0       # no .run: nothing, no error


def test_the_handler_waits_for_a_save_in_flight(monkeypatch):
    """The 30 s saver may hold _save_lock when SIGTERM lands: the handler waits (bounded) for it to
    finish before exiting, so os._exit never cuts that write short."""
    d = load(monkeypatch)
    for f in ("save_power", "save_log", "save_history"):
        monkeypatch.setattr(d, f, lambda *a, **kw: 0)
    exits = []
    term = d._term_handler([], exit_=exits.append)
    released = []
    d._save_lock.acquire()                                   # the saver, mid-write

    def finish_write():
        time.sleep(0.3)
        released.append(time.monotonic())
        d._save_lock.release()

    threading.Thread(target=finish_write, daemon=True).start()
    term(signal.SIGTERM, None)
    t_exit = time.monotonic()
    assert exits == [0] and released and t_exit >= released[0]
    assert d._save_lock.acquire(timeout=1)                   # given back after a returning exit_
    d._save_lock.release()


def test_a_save_lock_never_released_does_not_hold_the_exit(monkeypatch):
    d = load(monkeypatch)
    for f in ("save_power", "save_log", "save_history"):
        monkeypatch.setattr(d, f, lambda *a, **kw: 0)
    monkeypatch.setattr(d, "TERM_LOCK_WAIT_S", 0.2)
    exits = []
    d._save_lock.acquire()
    try:
        t0 = time.monotonic()
        d._term_handler([], exit_=exits.append)(signal.SIGTERM, None)
        assert exits == [0] and time.monotonic() - t0 < 1.5
    finally:
        d._save_lock.release()
