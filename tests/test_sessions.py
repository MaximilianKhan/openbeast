#!/usr/bin/env python3
"""Session ledger (agents/sessions.py) — beast-chat Phase 0.

Pure unit tests: no llama-server, no GPU, no network. Every test runs against
a tmp_path ledger, so the real .run/sessions is never touched.

Covers the properties the rest of beast-chat leans on:
  * register/touch/finalize round trip, atomic writes, 0600/0700 modes
  * reconcile: a dead pid becomes `lost`; a RECYCLED pid with a different
    process start time is still `lost` (the whole reason we record field 22
    of /proc/<pid>/stat)
  * list_sessions filtering/ordering, filtering AFTER reconciliation
  * read_new_ops cursor advance, second call returns nothing, partial
    trailing line not consumed
  * corrupt record skipped, never raised (this module is on the hot path)
  * prune

Run: python3 -m pytest tests/test_sessions.py -q
"""

import json
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

import errno
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import sessions  # noqa: E402


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    """Point the module global at a throwaway directory for every test."""
    d = tmp_path / "sessions"
    monkeypatch.setattr(sessions, "SESSIONS_DIR", str(d))
    return str(d)


def _dead_pid() -> int:
    """A pid that is definitely not running: spawn/reap a trivial child."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_register_touch_finalize_round_trip(ledger):
    sid = sessions.new_id("agent")
    rec = sessions.register(sid, kind="agent", title="port the zig module",
                            pid=os.getpid(), workdir="/tmp/wd",
                            model="qwen-27b-q5", transcript="/tmp/log.jsonl")

    assert rec["id"] == sid
    assert rec["state"] == "running"
    assert rec["kind"] == "agent"
    assert rec["meta"]["pid_start"] == sessions.pid_start_time(os.getpid())
    assert rec["inbox"].endswith(f"{sid}/inbox.jsonl")

    got = sessions.get(sid)
    assert got["title"] == "port the zig module"
    assert got["model"] == "qwen-27b-q5"
    assert got["transcript"] == "/tmp/log.jsonl"
    assert got["pgid"] == os.getpgid(os.getpid())

    before = got["updated_at"]
    sessions.touch(sid, last_event="tool_call")
    after = sessions.get(sid)
    assert after["last_event"] == "tool_call"
    assert after["updated_at"] >= before
    assert after["started_at"] == got["started_at"]   # identity is immutable

    sessions.finalize(sid, "done", summary="all green")
    final = sessions.get(sid)
    assert final["state"] == "done"
    assert final["summary"] == "all green"
    assert final["ended_at"]


def test_new_id_shape_and_uniqueness():
    a, b = sessions.new_id("agent"), sessions.new_id("job")
    assert a != b
    stamp, _, suffix = a.rpartition("-")
    datetime.strptime(stamp, "%Y%m%d-%H%M%S")        # raises if the shape drifts
    assert len(suffix) == 8 and int(suffix, 16) >= 0


def test_file_and_dir_modes(ledger):
    sid = sessions.new_id("agent")
    sessions.register(sid)
    assert os.stat(ledger).st_mode & 0o777 == 0o700
    assert os.stat(sessions.record_path(sid)).st_mode & 0o777 == 0o600


def test_touch_ignores_unknown_session(ledger):
    sessions.touch("no-such-session", last_event="iteration")   # must not raise
    assert sessions.get("no-such-session") is None
    sessions.finalize("no-such-session", "done")                # also a no-op


def test_touch_merges_meta_rather_than_replacing(ledger):
    sid = sessions.new_id("agent")
    sessions.register(sid, meta={"cursor": 0, "base_url": "http://x/v1"})
    sessions.touch(sid, meta={"cursor": 512})
    meta = sessions.get(sid)["meta"]
    assert meta["cursor"] == 512
    assert meta["base_url"] == "http://x/v1"     # co-writer's field survives
    assert "pid_start" in meta


def test_concurrent_touches_do_not_lose_writes(ledger):
    """E5 — THE test the merge claim actually needs.

    The docstring on touch() has always promised that two writers cannot
    clobber each other; until the flock landed, nothing enforced it and the
    test backing the promise was single-threaded. Four real processes, each
    setting its own field 40 times: with an unsynchronised read-modify-write
    ~10% of those writes vanished.
    """
    sid = "20260914-130000-c0c0c0c0"
    sessions.register(sid, pid=os.getpid())

    def writer(tag):
        sessions.SESSIONS_DIR = ledger        # fresh process, re-point it
        for i in range(40):
            sessions.touch(sid, **{f"w_{tag}": i}, meta={f"m_{tag}": i})

    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=writer, args=(t,)) for t in range(4)]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join(30)
    assert all(pr.exitcode == 0 for pr in procs)

    rec = sessions.get(sid)
    for t in range(4):
        assert rec.get(f"w_{t}") == 39, f"writer {t} lost its last write"
        assert rec["meta"].get(f"m_{t}") == 39, f"writer {t} lost its meta"
    assert rec["meta"]["pid_start"] is not None, "register's field survived"


def test_a_racing_touch_cannot_resurrect_a_finalized_session(ledger):
    """E5 — a touch that read before a finalize and wrote after it reverted
    the terminal record to `running` in 29 of 30 trials."""
    sid = "20260914-130001-d0d0d0d0"
    for trial in range(30):
        sessions.register(sid, pid=os.getpid())
        barrier = threading.Barrier(2)

        def toucher():
            barrier.wait()
            sessions.touch(sid, last_event="tool_call")

        t = threading.Thread(target=toucher)
        t.start()
        barrier.wait()
        sessions.finalize(sid, "done", summary="finished")
        t.join(10)
        rec = sessions.get(sid)
        assert rec["state"] == "done", f"resurrected on trial {trial}: {rec['state']}"


def test_finalize_refuses_to_move_a_record_back_to_running(ledger):
    sid = sessions.new_id("agent")
    sessions.register(sid, pid=os.getpid())
    assert sessions.finalize(sid, "done", summary="first verdict") is True
    # The atexit safety net fires after a clean done — it must not win.
    assert sessions.finalize(sid, "failed", summary="late atexit") is False
    assert sessions.finalize(sid, "running") is False
    rec = sessions.get(sid)
    assert rec["state"] == "done" and rec["summary"] == "first verdict"
    # And an explicit touch(state="running") cannot undo it either.
    sessions.touch(sid, state="running")
    assert sessions.get(sid)["state"] == "done"
    assert sessions.finalize("no-such-session", "done") is False


def test_register_returns_none_when_the_write_failed(ledger, monkeypatch):
    """E19 — it used to report success against a read-only ledger dir."""
    sid = sessions.new_id("agent")
    assert sessions.register(sid, pid=os.getpid()) is not None

    monkeypatch.setattr(sessions, "_write_record", lambda rec: False)
    assert sessions.register("20260914-130002-e0e0e0e0") is None


def test_register_does_not_create_the_inbox(ledger):
    sid = sessions.new_id("agent")
    sessions.register(sid)
    assert not os.path.exists(os.path.join(ledger, sid))


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------

def test_reconcile_marks_lost_when_pid_is_gone(ledger):
    sid = sessions.new_id("agent")
    dead = _dead_pid()
    sessions.register(sid, pid=dead)
    # The child is reaped, so /proc/<pid> is gone and pid_start is None.
    assert sessions.get(sid)["state"] == "lost"


def test_recycled_pid_with_different_start_time_is_still_lost(ledger):
    """The core anti-footgun: our own live pid, but not our process."""
    sid = sessions.new_id("agent")
    sessions.register(sid, pid=os.getpid())
    assert sessions.get(sid)["state"] == "running"

    # Rewrite pid_start as if a *previous* process had held this pid.
    path = sessions.record_path(sid)
    rec = json.loads(open(path).read())
    real = rec["meta"]["pid_start"]
    rec["meta"]["pid_start"] = int(real) - 1000
    rec["state"] = "running"
    open(path, "w").write(json.dumps(rec))

    fixed = sessions.get(sid)
    assert fixed["state"] == "lost", "a recycled pid must not look alive"
    assert "terminal event" in (fixed["summary"] or "")


def test_reconcile_is_pure_and_leaves_terminal_states_alone():
    live = {"id": "x", "state": "running", "pid": os.getpid(),
            "meta": {"pid_start": sessions.pid_start_time(os.getpid())}}
    assert sessions.reconcile(live) is live

    done = {"id": "x", "state": "done", "pid": _dead_pid(), "meta": {}}
    assert sessions.reconcile(done)["state"] == "done"

    dead = {"id": "x", "state": "running", "pid": _dead_pid(),
            "meta": {"pid_start": 12345}}
    out = sessions.reconcile(dead)
    assert out["state"] == "lost"
    assert dead["state"] == "running", "reconcile must not mutate its input"


def test_lost_transition_is_persisted(ledger):
    sid = sessions.new_id("agent")
    sessions.register(sid, pid=_dead_pid())
    sessions.get(sid)
    on_disk = json.loads(open(sessions.record_path(sid)).read())
    assert on_disk["state"] == "lost"


def test_pid_start_time_handles_comm_with_spaces_and_parens(tmp_path, monkeypatch):
    """`comm` is attacker-shaped: rpartition(')') is the only correct split."""
    fake = tmp_path / "stat"
    fields = " ".join(str(i) for i in range(3, 53))   # fields 3..52
    fake.write_text(f"4242 (weird ) name (x)) {fields}\n")

    real_open = open

    def fake_open(path, *a, **kw):
        if str(path) == "/proc/4242/stat":
            return real_open(str(fake), *a, **kw)
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    assert sessions.pid_start_time(4242) == 22        # field 22 == value 22


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

def test_list_sessions_filtering_and_ordering(ledger):
    ids = []
    for i, (kind, state) in enumerate([("agent", "running"), ("job", "done"),
                                       ("agent", "done")]):
        sid = f"2026090{i + 1}-120000-aaaaaaa{i}"
        sessions.register(sid, kind=kind, pid=os.getpid(), title=f"s{i}")
        if state != "running":
            sessions.finalize(sid, state)
        ids.append(sid)

    everything = sessions.list_sessions()
    assert [r["id"] for r in everything] == list(reversed(ids)), "newest first"

    assert [r["id"] for r in sessions.list_sessions(state="running")] == [ids[0]]
    assert [r["id"] for r in sessions.list_sessions(kind="job")] == [ids[1]]
    assert sessions.list_sessions(state="done", kind="agent")[0]["id"] == ids[2]
    assert len(sessions.list_sessions(limit=2)) == 2
    assert sessions.list_sessions(state="failed") == []


def test_list_sessions_filters_after_reconciliation(ledger):
    """A crashed agent is findable as `lost`, not as the `running` it claims."""
    sid = sessions.new_id("agent")
    sessions.register(sid, pid=_dead_pid())
    assert sessions.list_sessions(state="running") == []
    assert [r["id"] for r in sessions.list_sessions(state="lost")] == [sid]


def test_empty_and_missing_dir_are_not_errors(ledger):
    assert sessions.list_sessions() == []
    assert not os.path.exists(ledger)


# ---------------------------------------------------------------------------
# Fail-soft
# ---------------------------------------------------------------------------

def test_corrupt_record_is_skipped_not_raised(ledger):
    good = sessions.new_id("agent")
    sessions.register(good, pid=os.getpid(), title="good one")

    os.makedirs(ledger, exist_ok=True)
    open(os.path.join(ledger, "truncated.json"), "w").write('{"id": "trunc"')
    open(os.path.join(ledger, "empty.json"), "w").write("")
    open(os.path.join(ledger, "notadict.json"), "w").write("[1, 2, 3]")
    open(os.path.join(ledger, "noid.json"), "w").write('{"state": "running"}')
    os.makedirs(os.path.join(ledger, "somedir.json"), exist_ok=True)

    listed = sessions.list_sessions()
    assert [r["id"] for r in listed] == [good]
    assert sessions.get("truncated") is None


def test_invalid_ids_never_escape_the_ledger_dir(ledger):
    for bad in ("../escape", "a/b", "", ".", "..", "x\x00y"):
        assert sessions.get(bad) is None
        sessions.touch(bad, last_event="x")       # no raise, no write
        sessions.append_op(bad, {"op": "say", "text": "hi"})
        assert sessions.read_new_ops(bad, 0) == ([], 0)
    assert sessions.list_sessions() == []


# ---------------------------------------------------------------------------
# Steering inbox
# ---------------------------------------------------------------------------

def test_read_new_ops_cursor_advances_and_second_call_is_empty(ledger):
    sid = sessions.new_id("agent")
    sessions.register(sid)

    assert sessions.read_new_ops(sid, 0) == ([], 0), "missing inbox is not an error"
    assert not os.path.exists(sessions.inbox_path(sid)), "reading must not create"

    sessions.append_op(sid, {"op": "say", "text": "check the tests", "from": "max"})
    sessions.append_op(sid, {"op": "pause"})

    ops, cursor = sessions.read_new_ops(sid, 0)
    assert [o["op"] for o in ops] == ["say", "pause"]
    assert ops[0]["text"] == "check the tests"
    assert ops[0]["from"] == "max"
    assert "ts" in ops[0]
    assert cursor == os.path.getsize(sessions.inbox_path(sid))

    assert sessions.read_new_ops(sid, cursor) == ([], cursor), "no replay"

    sessions.append_op(sid, {"op": "resume"})
    ops2, cursor2 = sessions.read_new_ops(sid, cursor)
    assert [o["op"] for o in ops2] == ["resume"]
    assert cursor2 > cursor


def test_read_new_ops_ignores_a_partial_trailing_line(ledger):
    sid = sessions.new_id("agent")
    sessions.register(sid)
    sessions.append_op(sid, {"op": "say", "text": "first"})
    path = sessions.inbox_path(sid)
    complete = os.path.getsize(path)
    with open(path, "a") as f:
        f.write('{"op": "say", "text": "half-writ')   # no newline yet

    ops, cursor = sessions.read_new_ops(sid, 0)
    assert [o["text"] for o in ops] == ["first"]
    assert cursor == complete, "cursor must stop at the last complete line"

    with open(path, "a") as f:                          # writer finishes
        f.write('ten"}\n')
    ops2, _ = sessions.read_new_ops(sid, cursor)
    assert [o["text"] for o in ops2] == ["half-written"]


def test_read_new_ops_skips_malformed_lines_and_resets_on_truncation(ledger):
    sid = sessions.new_id("agent")
    sessions.register(sid)
    path = sessions.inbox_path(sid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("not json\n")
        f.write("[1,2]\n")                              # valid JSON, not an op
        f.write('{"op": "stop"}\n')
    ops, cursor = sessions.read_new_ops(sid, 0)
    assert [o["op"] for o in ops] == ["stop"]

    with open(path, "w") as f:                          # truncated / replaced
        f.write('{"op": "resume"}\n')
    ops2, cursor2 = sessions.read_new_ops(sid, cursor)
    assert [o["op"] for o in ops2] == ["resume"]
    assert cursor2 == os.path.getsize(path)


def test_append_op_refuses_non_objects(ledger):
    sid = sessions.new_id("agent")
    for junk in (["a", "b"], "say", 42):
        sessions.append_op(sid, junk)             # must not raise
    assert not os.path.exists(sessions.inbox_path(sid))
    sessions.append_op(sid, {"op": "stop"})
    assert sessions.read_new_ops(sid, 0)[0][0]["op"] == "stop"


def test_inbox_mode_is_0600(ledger):
    sid = sessions.new_id("agent")
    sessions.append_op(sid, {"op": "say", "text": "hi"})
    assert os.stat(sessions.inbox_path(sid)).st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------

def _age(sid: str, days: int):
    path = sessions.record_path(sid)
    rec = json.loads(open(path).read())
    old = (datetime.now() - timedelta(days=days)).isoformat()
    rec["updated_at"] = rec["started_at"] = old
    open(path, "w").write(json.dumps(rec))


def test_prune_removes_only_old_terminal_records(ledger):
    old_done = sessions.new_id("agent")
    sessions.register(old_done, pid=os.getpid())
    sessions.append_op(old_done, {"op": "say", "text": "x"})
    sessions.finalize(old_done, "done")
    _age(old_done, 45)

    fresh_done = sessions.new_id("agent")
    sessions.register(fresh_done, pid=os.getpid())
    sessions.finalize(fresh_done, "done")

    old_running = sessions.new_id("agent")
    sessions.register(old_running, pid=os.getpid())
    _age(old_running, 45)

    assert sessions.prune(30) == 1
    assert sessions.get(old_done) is None
    assert not os.path.exists(os.path.join(ledger, old_done)), "inbox dir gone too"
    assert sessions.get(fresh_done) is not None
    assert sessions.get(old_running) is not None

    assert sessions.prune(30) == 0            # idempotent


def test_prune_on_missing_dir_is_zero(ledger):
    assert sessions.prune(30) == 0


# ---------------------------------------------------------------------------
# Liveness (E3) — the ledger must tell the truth about a dead session
# ---------------------------------------------------------------------------

def _zombie() -> int:
    """A child that has exited and has NOT been reaped. Caller must reap."""
    pid = os.fork()
    if pid == 0:                                  # child
        os._exit(0)
    for _ in range(200):                          # wait for state Z
        try:
            with open(f"/proc/{pid}/stat", "rb") as f:
                raw = f.read().decode("utf-8", "replace")
            if raw.rpartition(")")[2].split()[0] == "Z":
                return pid
        except OSError:
            break
        time.sleep(0.01)
    return pid


def test_a_zombie_is_not_alive(ledger):
    """E3 — the premise of the whole feature.

    An unreaped child keeps its /proc entry AND its start time, so the old
    check called it `running` forever: every console-started session reported
    as running for good, the SSE stream never ended, and /send queued into a
    corpse.
    """
    pid = _zombie()
    try:
        state = open(f"/proc/{pid}/stat").read().rpartition(")")[2].split()[0]
        assert state == "Z", "the fixture did not actually produce a zombie"
        start = sessions.pid_start_time(pid)
        assert start is not None, "a zombie still has a start time (that is the bug)"

        assert sessions._alive(pid, start) is False
        assert sessions._alive(pid, start, require_start=True) is False

        sid = "20260914-140000-20mb1e00"
        sessions.register(sid, pid=pid)
        assert sessions.get(sid)["state"] == "lost"
        assert sessions.is_alive(sessions.get(sid)) is False
    finally:
        os.waitpid(pid, 0)


def test_missing_start_time_is_not_alive_for_a_signalling_caller(ledger):
    """E3 — a reviewer rode the `no pid_start` fallback into SIGKILLing an
    unrelated live process group. A record that cannot prove the pid is ours
    is not alive for anything that signals."""
    rec = {"id": "x", "state": "running", "pid": os.getpid(), "meta": {}}

    # Reporting is allowed to be lenient...
    assert sessions._alive(os.getpid(), None) is True
    assert sessions.is_alive(rec, require_start=False) is True
    # ...signalling is not, and that is the DEFAULT.
    assert sessions._alive(os.getpid(), None, require_start=True) is False
    assert sessions.is_alive(rec) is False

    # The concrete attack: a live pid this record never owned.
    victim = subprocess.Popen([sys.executable, "-c",
                               "import time; time.sleep(30)"])
    try:
        stolen = {"id": "y", "state": "running", "pid": victim.pid,
                  "pgid": victim.pid, "meta": {}}
        assert sessions.is_alive(stolen) is False, \
            "a record with no pid_start must never authorise a signal"
        stolen["meta"] = {"pid_start": sessions.pid_start_time(victim.pid)}
        assert sessions.is_alive(stolen) is True, "a proven pid still signals"
    finally:
        victim.kill()
        victim.wait()


def test_is_alive_rejects_junk_and_dead_pids(ledger):
    assert sessions.is_alive(None) is False
    assert sessions.is_alive({}) is False
    assert sessions.is_alive({"pid": 0, "meta": {"pid_start": 1}}) is False
    assert sessions.is_alive({"pid": -1, "meta": {"pid_start": 1}}) is False
    assert sessions.is_alive({"pid": "abc", "meta": {"pid_start": 1}}) is False
    assert sessions.is_alive({"pid": _dead_pid(), "meta": {"pid_start": 1}}) is False


def test_proc_state_and_start_time_come_from_one_parse(tmp_path, monkeypatch):
    """`comm` is attacker-shaped: rpartition(')') is the only correct split,
    and the state char is field 3 == index 0 after it."""
    fake = tmp_path / "stat"
    fields = " ".join(str(i) for i in range(3, 53))
    fake.write_text(f"4242 (weird ) name (x)) {fields}\n")
    real_open = open

    def fake_open(path, *a, **kw):
        if str(path) == "/proc/4242/stat":
            return real_open(str(fake), *a, **kw)
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    assert sessions._proc_stat(4242) == ("3", 22)
    assert sessions.pid_start_time(4242) == 22


# ---------------------------------------------------------------------------
# Inbox caps + hostile inbox paths (E12, E13)
# ---------------------------------------------------------------------------

def test_read_new_ops_is_bounded_per_turn(ledger):
    """E12 — the read was unbounded: a 25 MB inbox allocated ~151 MB."""
    sid = sessions.new_id("agent")
    line = json.dumps({"op": "say", "text": "y" * 900, "ts": 1}) + "\n"
    path = sessions.inbox_path(sid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for _ in range(3000):                      # ~2.8 MB
            f.write(line)
    total = os.path.getsize(path)
    assert total > sessions.INBOX_MAX_READ

    ops, cursor = sessions.read_new_ops(sid, 0)
    assert len(ops) == sessions.INBOX_MAX_OPS
    assert 0 < cursor <= sessions.INBOX_MAX_READ, "at most one capped read"

    # The backlog drains across turns instead of arriving as one allocation.
    seen, guard = len(ops), 0
    while cursor < total and guard < 500:
        ops, cursor = sessions.read_new_ops(sid, cursor)
        assert len(ops) <= sessions.INBOX_MAX_OPS
        seen += len(ops)
        guard += 1
    assert cursor == total and seen == 3000


def test_an_oversized_say_is_clamped(ledger):
    sid = sessions.new_id("agent")
    sessions.append_op(sid, {"op": "say", "text": "q" * 100_000})
    ops, _ = sessions.read_new_ops(sid, 0)
    assert len(ops) == 1
    assert len(ops[0]["text"]) == sessions.OP_MAX_TEXT + len(sessions._TRUNC_MARK)
    assert ops[0]["text"].endswith(sessions._TRUNC_MARK)


def test_a_single_line_longer_than_the_cap_does_not_wedge_the_cursor(ledger):
    sid = sessions.new_id("agent")
    path = sessions.inbox_path(sid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("x" * (sessions.INBOX_MAX_READ * 2) + "\n")
        f.write(json.dumps({"op": "stop"}) + "\n")
    cursor, guard = 0, 0
    ops = []
    while not ops and guard < 20:
        ops, cursor = sessions.read_new_ops(sid, cursor)
        guard += 1
    assert [o["op"] for o in ops] == ["stop"], "the good op is still reachable"


def test_a_fifo_at_the_inbox_path_does_not_block_the_runner(ledger):
    """E13 — a named pipe there hung the runner forever inside open(), at
    EVERY turn boundary: the module's fail-soft contract inverted into a hard
    hang by anything that can write the ledger directory."""
    sid = sessions.new_id("agent")
    path = sessions.inbox_path(sid)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    os.mkfifo(path, 0o600)

    def _boom(signum, frame):
        raise AssertionError("read_new_ops blocked on the FIFO")

    old = signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, 3.0)
    try:
        assert sessions.read_new_ops(sid, 0) == ([], 0)
        assert sessions.read_new_ops(sid, 512) == ([], 512)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
        os.unlink(path)


def test_a_directory_at_the_inbox_path_is_not_an_inbox(ledger):
    sid = sessions.new_id("agent")
    os.makedirs(sessions.inbox_path(sid), exist_ok=True)
    assert sessions.read_new_ops(sid, 0) == ([], 0)


def test_append_op_refuses_to_follow_a_symlink(ledger, tmp_path):
    """E13 — otherwise 'append an operator message' becomes 'append
    attacker-chosen JSON to an arbitrary file'."""
    sid = sessions.new_id("agent")
    path = sessions.inbox_path(sid)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    target = tmp_path / "victim.txt"
    target.write_text("original\n")
    os.symlink(str(target), path)

    sessions.append_op(sid, {"op": "say", "text": "pwned"})       # must not raise
    assert target.read_text() == "original\n", "the symlink was followed"
    assert sessions.read_new_ops(sid, 0) == ([], 0)


# ---------------------------------------------------------------------------
# prune (E17)
# ---------------------------------------------------------------------------

def test_prune_removes_the_job_log_and_the_lock_sidecar(ledger):
    """E17 — scripts/job.sh streams stdout+stderr to <ledger>/<id>.log; a
    prune that removed only the record orphaned it forever."""
    sid = sessions.new_id("job")
    sessions.register(sid, kind="job", pid=os.getpid())
    sessions.touch(sid, last_event="x")            # creates the lock sidecar
    log = os.path.join(ledger, f"{sid}.log")
    open(log, "w").write("job output\n")
    lock = os.path.join(ledger, f".{sid}.lock")
    assert os.path.exists(lock)
    sessions.finalize(sid, "done")
    _age(sid, 45)

    assert sessions.prune(30) == 1
    assert not os.path.exists(log), "the job log was orphaned"
    assert not os.path.exists(lock)
    assert not os.path.exists(sessions.record_path(sid))


def test_prune_leaves_a_live_session_log_alone(ledger):
    sid = sessions.new_id("job")
    sessions.register(sid, kind="job", pid=os.getpid())
    log = os.path.join(ledger, f"{sid}.log")
    open(log, "w").write("still running\n")
    _age(sid, 45)
    assert sessions.prune(30) == 0
    assert os.path.exists(log)


# ---------------------------------------------------------------------------
# append_op reports whether the op actually landed (review [48])
# ---------------------------------------------------------------------------

def test_append_op_reports_a_failed_write(ledger, monkeypatch):
    """The write's return value was discarded and its OSError swallowed, so a
    /send on a full disk was dropped while the API answered
    {"queued": true}."""
    sid = "s-enospc"
    sessions.register(sid, pid=os.getpid())
    real = os.write

    def enospc(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "write", enospc)
    assert sessions.append_op(sid, {"op": "say", "text": "hi"}) is False
    monkeypatch.setattr(os, "write", real)
    # nothing to read: the op did not land
    assert sessions.read_new_ops(sid)[0] == []


def test_a_short_write_does_not_take_the_next_op_down_with_it(ledger,
                                                              monkeypatch):
    """A newline-less remnant used to swallow the FOLLOWING op too, because
    read_new_ops advances its cursor past a line before json.loads rejects
    it — two ops gone, no error anywhere. A partial line must be terminated
    so the loss stops at one."""
    sid = "s-short"
    sessions.register(sid, pid=os.getpid())
    real = os.write
    state = {"calls": 0}

    def short_then_full(fd, data):
        """ENOSPC *after* a partial transfer — the realistic shape. A short
        write the loop CAN continue is continued (that is the loop working);
        what has to be survivable is the one it cannot."""
        state["calls"] += 1
        if state["calls"] == 1:
            return real(fd, bytes(data)[:len(bytes(data)) // 3])   # partial
        if state["calls"] == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(fd, data)            # the terminating "\n", and later ops

    monkeypatch.setattr(os, "write", short_then_full)
    assert sessions.append_op(sid, {"op": "say", "text": "A" * 300}) is False
    monkeypatch.setattr(os, "write", real)
    # the SECOND op is intact and readable — the whole point
    assert sessions.append_op(sid, {"op": "say", "text": "second"}) is True
    ops, _ = sessions.read_new_ops(sid)
    texts = [o.get("text") for o in ops]
    assert "second" in texts, ops
    assert not any(t and t.startswith("AAA") and len(t) < 300 for t in texts)


def test_append_op_returns_true_on_the_happy_path(ledger):
    sid = "s-ok"
    sessions.register(sid, pid=os.getpid())
    assert sessions.append_op(sid, {"op": "say", "text": "hello"}) is True
    assert sessions.append_op(sid, "not-a-dict") is False
    ops, _ = sessions.read_new_ops(sid)
    assert [o.get("text") for o in ops] == ["hello"]


# ---------------------------------------------------------------------------
# a record from a previous boot (review [54])
# ---------------------------------------------------------------------------

def test_register_stamps_the_boot(ledger):
    sid = "s-boot"
    rec = sessions.register(sid, pid=os.getpid())
    if sessions._boot_id() is None:
        pytest.skip("kernel does not report a boot id")
    assert rec["meta"]["boot_id"] == sessions._boot_id()


def test_a_record_from_another_boot_is_never_alive(ledger, monkeypatch):
    """pid_start is ticks since BOOT, so across a reboot the comparison is
    two different clocks — the documented reboot guarantee was void by
    construction for exactly the event it names."""
    sid = "s-prev-boot"
    rec = sessions.register(sid, pid=os.getpid())     # genuinely alive NOW
    assert sessions.is_alive(rec) is True
    rec["meta"]["boot_id"] = "00000000-0000-0000-0000-000000000000"
    assert sessions.from_another_boot(rec) is True
    assert sessions.is_alive(rec) is False
    # ...and a running record reconciles to lost, saying why
    rec["state"] = "running"
    out = sessions.reconcile(rec)
    assert out["state"] == "lost"
    assert "boot" in out["summary"]


def test_a_record_with_no_boot_id_keeps_todays_behaviour(ledger):
    """LOAD-BEARING. Every record written before this change has no boot_id;
    treating absent as mismatched would flip every live session on the rig to
    `lost` the moment the change lands."""
    sid = "s-legacy"
    rec = sessions.register(sid, pid=os.getpid())
    rec["meta"].pop("boot_id", None)
    assert sessions.from_another_boot(rec) is False
    assert sessions.is_alive(rec) is True
    assert sessions.reconcile(dict(rec, state="running"))["state"] == "running"
    # an empty string is no information either
    rec["meta"]["boot_id"] = ""
    assert sessions.from_another_boot(rec) is False
    assert sessions.is_alive(rec) is True


def test_an_unknowable_boot_invalidates_nothing(ledger, monkeypatch):
    """A kernel that will not report a boot id must not invalidate records
    that DO carry one."""
    sid = "s-noproc"
    rec = sessions.register(sid, pid=os.getpid())
    rec["meta"]["boot_id"] = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(sessions, "_boot_id", lambda: None)
    assert sessions.from_another_boot(rec) is False
    assert sessions.is_alive(rec) is True
