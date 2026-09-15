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
import os
import subprocess
import sys
from datetime import datetime, timedelta

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
