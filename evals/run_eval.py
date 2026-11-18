#!/usr/bin/env python3
"""
Eval harness — run coding tasks against the local LLM and measure performance.

Each task is defined as a JSON file in evals/tasks/ with:
  - task: what the agent should do
  - setup: optional shell command to create test fixtures
  - validation: python or bash script that checks the result
  - cleanup: shell command to remove test artifacts
  - max_iter: max agent iterations for this task

The eval runner:
  1. Runs setup (if any)
  2. Launches the agent runner against the task
  3. Runs validation to check success/failure
  4. Records results to evals/results/{timestamp}.json
  5. Runs cleanup

Usage:
  python evals/run_eval.py                          # run all tasks
  python evals/run_eval.py --tasks 01,02,03         # run specific tasks
  python evals/run_eval.py --max-iter 5             # override max iterations
  python evals/run_eval.py --jobs 4                 # 4 parallel workers (server must run -np >= 4)
  python evals/run_eval.py --base-url http://...:8080/v1  # different server

Requires: llama.cpp server running on port 8080.
"""

import argparse
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = os.path.join(EVALS_DIR, "tasks")
RESULTS_DIR = os.path.join(EVALS_DIR, "results")
RUNNER_PATH = os.path.join(EVALS_DIR, "..", "agents", "runner.py")

# Shared process-reaping from agents/tools.py — single source of truth for
# SIGKILL-the-group + rlimit + output-cap logic.
sys.path.insert(0, os.path.join(EVALS_DIR, "..", "agents"))
from tools import run_reaped  # noqa: E402
sys.path.insert(0, EVALS_DIR)
import suite_manifest  # noqa: E402

# ---------------------------------------------------------------------------
# Agent process groups we own, so a SIGTERM to US does not orphan THEM.
#
# Every agent is spawned with start_new_session=True, which is load-bearing:
# it is what lets the per-task wall-timeout SIGKILL the runner's whole group
# instead of just the runner. The cost, found by the v1.4.0 review, is that
# the same isolation puts the agent OUTSIDE any group signalled at us — so
# `./scripts/job.sh stop`, a Stop from the beast-chat console, or a plain
# Ctrl-C killed run_eval and left the agent running: still holding a
# llama-server slot, still writing the task's shared /tmp/eval_* fixtures,
# against a relaunch that necessarily re-runs the unbanked unit. A corrupted
# row from that re-run does not look corrupted; it looks like a result.
#
# A SET, not one global: --jobs N runs N agents concurrently in a
# ThreadPoolExecutor, so tracking a single Popen would orphan N-1 of them.
_live_pgids: set[int] = set()
_live_pgid_lock = threading.Lock()

# Set, once, by a signal handler that is about to os._exit. Reaping the
# live set is not enough under --jobs N: the handler may block (benchmark_
# all stops llama-server first, up to ~15 s) while worker threads keep
# running, and a worker whose agent was just SIGKILLed validates, cleans up
# and Popens the NEXT agent, which os._exit then orphans (verifier finding
# on campaign-evals: signal handler still orphans agents under --jobs).
# run_agent refuses to spawn once it is set, and kills its own agent if the
# flag appeared between the Popen and the registration.
_stopping = threading.Event()


# Held across "check _stopping -> Popen -> register" (_spawn_agent). Without
# it a child forked while its thread was still inside Popen was in no set
# yet: begin_stop's snapshot missed it and os._exit orphaned it (review
# finding campaign-evals: an in-flight spawn escapes the reap). A handler
# waits on it, so every child forked before the flag is registered first.
_spawn_lock = threading.Lock()
_spawn_owner: int | None = None     # thread ident inside _spawn_agent
_deferred_signal: int | None = None  # arrived on that thread, mid-spawn


def stopping() -> bool:
    """Whether a signal handler has begun tearing this process down."""
    return _stopping.is_set()


def begin_stop(signum: int | None = None) -> bool:
    """Forbid new agents, wait out a spawn in flight, then reap every live
    one. Call from a handler before anything slow; call _reap_live_agents()
    again just before os._exit.

    Returns False when the signal interrupted THIS thread's own spawn (the
    handler runs on the main thread, which may be the one inside Popen): it
    cannot wait for itself, so the caller must return at once, and
    _spawn_agent re-raises `signum` the moment its child is registered."""
    global _deferred_signal
    _stopping.set()
    if _spawn_owner == threading.get_ident():
        _deferred_signal = signum
        return False
    if _spawn_lock.acquire(timeout=30):   # Popen is milliseconds; never hang
        _spawn_lock.release()
    _reap_live_agents()
    return True


def _spawn_agent(cmd: list, env: dict) -> subprocess.Popen | None:
    """Popen an agent in its own session and register its group, as one
    step no reap can fall between. None once a stop has begun."""
    global _spawn_owner, _deferred_signal
    try:
        with _spawn_lock:
            _spawn_owner = threading.get_ident()
            try:
                if _stopping.is_set():
                    return None
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True,
                                        start_new_session=True, env=env)
                with _live_pgid_lock:
                    _live_pgids.add(proc.pid)   # == pgid: start_new_session
                return proc
            finally:
                _spawn_owner = None
    finally:
        if _deferred_signal is not None:
            signum, _deferred_signal = _deferred_signal, None
            if signum is not None:
                signal.raise_signal(signum)     # the handler, able to reap now


def _descendant_pids(root: int) -> list[int]:
    """Every live descendant of `root`: from /proc on Linux, else from
    _proc_snapshot (libproc on macOS). Without the fallback the macOS
    timeout kill reached only the runner's own group, and a bash-tool child
    in its own session ran on (review 1116)."""
    children: dict[int, list[int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        snap = _proc_snapshot()
        return sorted(_subtree(snap, {root}) - {root})
    for d in entries:
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                stat = f.read()
            # comm may contain spaces/parens: fields resume after the LAST ')'.
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
        children.setdefault(ppid, []).append(int(d))
    out, stack = [], [root]
    while stack:
        for c in children.get(stack.pop(), ()):
            out.append(c)
            stack.append(c)
    return out


def _kill_agent_tree(pgid: int) -> None:
    """SIGKILL an agent's process group AND every group its descendants lead.

    Killing only the runner's group (start_new_session) is not enough:
    tools.run_reaped starts every bash-tool child in its OWN session, so a
    command in flight at a wall timeout (a hung `./ts < input.txt`) outlived
    the runner and kept running — only its own run_reaped timeout could
    stop it, and that died with the runner. The runner group is frozen
    first so it cannot spawn another tool child during the /proc walk."""
    try:
        os.killpg(pgid, signal.SIGSTOP)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    groups = {pgid}
    for pid in _descendant_pids(pgid):
        try:
            groups.add(os.getpgid(pid))
        except (ProcessLookupError, OSError):
            pass
    groups.discard(os.getpgrp())        # never our own group
    for g in groups:
        try:
            os.killpg(g, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _reap_live_agents() -> None:
    """SIGKILL every agent group we own. Best-effort, last-resort."""
    got = _live_pgid_lock.acquire(blocking=False)
    try:
        pgids = tuple(_live_pgids)
    except RuntimeError:        # mutated mid-snapshot; a partial sweep beats none
        pgids = ()
    finally:
        if got:
            _live_pgid_lock.release()
    for pgid in pgids:
        _kill_agent_tree(pgid)


# --- Agent leftovers: nothing the agent started may outlive it (v5) -----------
#
# _kill_agent_tree only walks the tree that still hangs off the runner. A
# daemon that detaches properly (double fork + setsid, stdio off the pipe)
# is reparented to init the moment its parent exits, so it is in no tree,
# no group and no session of ours, and the bash tool documents that such a
# process outlives the tool call. It runs as our uid, so during validation
# it could rewrite the solution file after the lint, read the validator's
# work dir, or burn the CPU a budgeted solution is measured against (190's
# verifier, "harness-level, needs a decision before v5 ships").
#
# So every agent is watched while it runs and swept before validation:
#   tag      OPENBEAST_AGENT_TAG, a per-agent random value in its env. Every
#            child inherits it; the sweep reads each same-uid process's
#            environment (/proc/<pid>/environ, or KERN_PROCARGS2 on macOS).
#            On macOS this sees only NON-platform binaries: for Apple-signed
#            ones (/bin/sh, bash, sleep, nohup...) KERN_PROCARGS2 returns
#            argv without the environment (Darwin 25, review 1116). There
#            the sandbox tag below is the real mechanism, and the sweep
#            warns when it is unusable.
#   sandbox  macOS: the agent's bash runs under sandbox-exec in eval mode
#            (tools._eval_read_deny_prefix), and that profile also denies a
#            per-agent tag FILE (OPENBEAST_AGENT_TAG_FILE). A sandbox cannot
#            be left or replaced, so sandbox_check(pid, that file) marks every
#            process the shell started, detached and env-scrubbed included.
#   watch    a thread polls the agent's process tree every _AGENT_POLL_S and
#            records each (pid, start time) it ever saw, so a daemon that
#            scrubbed its env (env -i) but lived one poll is still known.
#            On Linux the runner is made a child subreaper before it execs,
#            so an orphan stays in its tree (and in view) while it lives.
#   groups   every process group and session the watched processes led,
#            with the identity of its leader. Each bash-tool call is a new
#            group, so a long unit records hundreds; on macOS (pid max
#            99999, sequential) those ids are reused within minutes. A group
#            is matched or killed only while it is provably the same group:
#            its leader alive with the recorded identity, or leaderless and
#            never seen empty since (an id cannot be reused while its group
#            has a member). A reused id used to be SIGKILLed whole, taking a
#            sibling unit's agent or validator, or a user app (review 1116).
#   cgroup   on Linux with a delegated cgroup v2, the runner is moved into a
#            child cgroup of ours, which nothing can leave; it is killed
#            whole (cgroup.kill) and must be empty.
# The candidates are frozen (SIGSTOP), then SIGKILLed with their whole
# subtree, for several rounds; then the sweep asserts none survive. A unit
# whose leftovers survive is not validated.

AGENT_TAG_ENV = "OPENBEAST_AGENT_TAG"
_AGENT_POLL_S = 0.5
_SWEEP_ROUNDS = 6


_BOOT_TIME: float | None = None


def _boot_time() -> float:
    global _BOOT_TIME
    if _BOOT_TIME is None:
        _BOOT_TIME = 0.0
        try:
            with open("/proc/stat") as f:
                for ln in f:
                    if ln.startswith("btime "):
                        _BOOT_TIME = float(ln.split()[1])
                        break
        except (OSError, ValueError):
            pass
    return _BOOT_TIME


def _proc_snapshot() -> dict:
    """{pid: (ppid, pgid, sid, start, uid, started_at)} of every live,
    non-zombie process. `start` identifies the process across pid reuse,
    `started_at` is its start as epoch seconds (1 s resolution on macOS),
    and `sid` is None where the platform does not report it in bulk (macOS:
    ask os.getsid)."""
    out = {}
    if os.path.isdir("/proc/self"):
        try:
            entries = os.listdir("/proc")
            tick = os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError):
            return out
        boot = _boot_time()
        for d in entries:
            if not d.isdigit():
                continue
            try:
                with open(f"/proc/{d}/stat") as f:
                    rest = f.read().rsplit(")", 1)[1].split()
                uid = os.stat(f"/proc/{d}").st_uid
            except (OSError, IndexError):
                continue
            if rest[0] in ("Z", "X"):
                continue
            try:
                out[int(d)] = (int(rest[1]), int(rest[2]), int(rest[3]), rest[19], uid,
                               boot + int(rest[19]) / tick)
            except (IndexError, ValueError):
                continue
        return out
    tab = _darwin_table()
    if tab is not None:
        # libproc, in-process: ~2-5 ms with no fork, against ~45 ms for the
        # `ps -A ... lstart=` below plus a strptime per row, twice a second
        # per agent (review 1116). Our uid's processes only, which is all the
        # sweep may match (another uid's row has no start). The start
        # identity is exact (sec, usec).
        for pid, (ppid, pgid, uid, _cpu, start) in tab.items():
            if start is None:
                continue
            sec, usec = start
            out[pid] = (ppid, pgid, None, f"{sec}.{usec:06d}", uid, sec + usec / 1e6)
        return out
    try:
        r = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid=,uid=,stat=,lstart="],
                           capture_output=True, text=True, timeout=10,
                           stdin=subprocess.DEVNULL, env={**os.environ, "LC_ALL": "C"})
    except Exception:  # noqa: BLE001 — a failed snapshot must never fail the unit
        return out
    for line in r.stdout.splitlines():
        p = line.split(None, 5)
        if len(p) < 6 or p[4].startswith("Z"):
            continue
        try:
            lstart = p[5].strip()
            started = time.mktime(time.strptime(lstart, "%a %b %d %H:%M:%S %Y"))
            out[int(p[0])] = (int(p[1]), int(p[2]), None, lstart, int(p[3]), started)
        except ValueError:
            continue
    return out


_HOST_VLIB = None


def _darwin_table():
    """vlib.darwin_table() (libproc), loaded from VLIB_PATH for the harness's
    own use, or None off macOS / when libproc is unusable."""
    global _HOST_VLIB
    if sys.platform != "darwin":
        return None
    try:
        if _HOST_VLIB is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_ob_host_vlib", VLIB_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _HOST_VLIB = mod
        return _HOST_VLIB.darwin_table(cpu=False)
    except Exception:  # noqa: BLE001 — fall back to ps
        return None


def _proc_env_blob(pid: int) -> bytes:
    """The process's initial environment block (and argv), as raw bytes.
    b"" when it cannot be read (another uid, gone, unsupported)."""
    if os.path.isdir("/proc/self"):
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                return f.read()
        except OSError:
            return b""
    if sys.platform != "darwin":
        return b""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        argmax = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(argmax))
        mib = (ctypes.c_int * 2)(1, 8)                    # CTL_KERN, KERN_ARGMAX
        if libc.sysctl(mib, 2, ctypes.byref(argmax), ctypes.byref(size), None, 0) != 0:
            return b""
        buf = ctypes.create_string_buffer(argmax.value)
        size = ctypes.c_size_t(argmax.value)
        mib = (ctypes.c_int * 3)(1, 49, pid)              # KERN_PROCARGS2
        if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
            return b""
        return buf.raw[:size.value]
    except (OSError, AttributeError, ValueError):
        return b""


_SANDBOX_CHECK = None


def _sandbox_tagged(pid: int, tag_file: str) -> int:
    """macOS: 1 when `pid` runs under a sandbox that denies reading
    `tag_file`, 0 when not, -1 when it cannot be asked."""
    global _SANDBOX_CHECK
    if sys.platform != "darwin":
        return -1
    try:
        import ctypes
        if _SANDBOX_CHECK is None:
            fn = ctypes.CDLL("/usr/lib/libSystem.B.dylib").sandbox_check
            fn.restype = ctypes.c_int
            # Only the fixed arguments: ctypes then passes the path as a
            # variadic one, which arm64 Darwin puts on the stack.
            fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
            _SANDBOX_CHECK = fn
        # SANDBOX_FILTER_PATH | SANDBOX_CHECK_NO_REPORT
        return int(_SANDBOX_CHECK(int(pid), b"file-read-data", 1 | 0x40000000,
                                  ctypes.c_char_p(tag_file.encode())))
    except (OSError, AttributeError, ValueError):
        return -1


# Run as the runner's argv prefix on Linux: become a child subreaper, then
# exec the runner (the attribute survives execve), so orphans of the agent's
# tools reparent to the runner instead of init and stay in its tree.
_SUBREAPER_EXEC = ("import ctypes, os, sys; ctypes.CDLL(None).prctl(36, 1, 0, 0, 0); "
                   "os.execv(sys.executable, [sys.executable] + sys.argv[1:])")


def _subtree(snap: dict, roots: set) -> set:
    children: dict = {}
    for pid, row in snap.items():
        children.setdefault(row[0], []).append(pid)
    out, stack = set(), [r for r in roots if r in snap]
    out.update(stack)
    while stack:
        for c in children.get(stack.pop(), ()):
            if c not in out:
                out.add(c)
                stack.append(c)
    return out


class _AgentCgroup:
    """A child cgroup (v2) of the harness's own, when the host delegates it.
    Linux only; .path is None everywhere else and every method is a no-op."""

    def __init__(self):
        self.path = None
        if not sys.platform.startswith("linux"):
            return
        try:
            with open("/proc/self/cgroup") as f:
                rel = next((ln.split("::", 1)[1].strip() for ln in f
                            if ln.startswith("0::")), None)
            if rel is None:
                return
            base = os.path.join("/sys/fs/cgroup", rel.lstrip("/"))
            if not os.access(os.path.join(base, "cgroup.procs"), os.W_OK):
                return
            import secrets
            p = os.path.join(base, f"ob-agent-{os.getpid()}-{secrets.token_hex(4)}")
            os.mkdir(p)
            self.path = p
        except (OSError, StopIteration):
            self.path = None

    def adopt(self, pid: int) -> bool:
        if not self.path:
            return False
        try:
            with open(os.path.join(self.path, "cgroup.procs"), "w") as f:
                f.write(str(pid))
            return True
        except OSError:
            return False

    def pids(self) -> set:
        if not self.path:
            return set()
        try:
            with open(os.path.join(self.path, "cgroup.procs")) as f:
                return {int(x) for x in f.read().split()}
        except (OSError, ValueError):
            return set()

    def kill(self):
        if not self.path:
            return
        try:
            with open(os.path.join(self.path, "cgroup.kill"), "w") as f:
                f.write("1")
        except OSError:
            pass
        for pid in self.pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def remove(self):
        if not self.path:
            return
        for _ in range(100):
            try:
                os.rmdir(self.path)
                break
            except OSError:
                self.kill()
                time.sleep(0.02)
        self.path = None


class AgentWatch:
    """Track everything one agent starts, from spawn to the sweep."""

    def __init__(self, tag: str):
        self.tag = tag
        self.tag_file: str | None = None
        self.control_file: str | None = None
        self._tag_dir: str | None = None
        if sys.platform == "darwin":
            import tempfile
            try:
                self._tag_dir = os.path.realpath(tempfile.mkdtemp(prefix="ob-agent-"))
                self.tag_file = os.path.join(self._tag_dir, "tag")
                self.control_file = os.path.join(self._tag_dir, "control")
                for f in (self.tag_file, self.control_file):
                    with open(f, "w"):
                        pass
            except OSError:
                self.tag_file = None
        self.root: int | None = None
        # Nothing that started before the agent can be the agent's. (-2 s:
        # macOS reports start times to the second.)
        self.t0 = time.time() - 2.0
        self.seen: dict = {}            # pid -> start identity
        # pgid/sid -> its leader's start identity when recorded (None: the
        # leader was already gone). See "groups" in the block comment.
        self.groups: dict = {}
        self.cgroup = _AgentCgroup()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, root: int):
        self.root = root
        self.cgroup.adopt(root)
        self.poll()
        self.groups.setdefault(root, self.seen.get(root))  # pgid == sid == pid
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"agent-watch-{root}")
        self._thread.start()

    def _loop(self):
        while not self._stop.wait(_AGENT_POLL_S):
            self.poll()

    def poll(self, snap: dict | None = None):
        if self.root is None:
            return
        snap = snap if snap is not None else _proc_snapshot()
        alive = {p for p, s in self.seen.items() if p in snap and snap[p][3] == s}
        mine = os.getpgrp()
        for pid in _subtree(snap, {self.root} | alive):
            row = snap[pid]
            self.seen.setdefault(pid, row[3])
            g = row[1]
            if g not in (mine, 0, 1) and g not in self.groups:
                self.groups[g] = snap[g][3] if g in snap else None
        # A group seen empty is over: its id may be handed out again.
        live = {row[1] for row in snap.values()} | {row[2] for row in snap.values()}
        for g in [g for g in self.groups if g not in live]:
            del self.groups[g]

    def owned_groups(self, snap: dict) -> set:
        """The recorded groups that are still provably the agent's: the
        leader alive with the identity recorded for it, or no live process
        with that pid at all (the group outlived its leader, and an id
        cannot be reused while its group has a member). A live pid with
        another identity is a reused id, never ours."""
        out = set()
        for g, ident in self.groups.items():
            lead = snap.get(g)
            if lead is None or (ident is not None and lead[3] == ident):
                out.add(g)
        return out

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _sandbox_tag_usable(self) -> bool:
        # The tag only means something while both files exist and THIS
        # process, which runs under no such sandbox, may read them.
        return bool(self.tag_file and os.path.isfile(self.tag_file)
                    and os.path.isfile(self.control_file)
                    and _sandbox_tagged(os.getpid(), self.tag_file) == 0
                    and _sandbox_tagged(os.getpid(), self.control_file) == 0)

    def _sandbox_marked(self, pid: int) -> bool:
        # Differential: denied the tag but allowed its sibling. An app
        # sandbox (most macOS agents and XPC services) denies both.
        return (_sandbox_tagged(pid, self.tag_file) == 1
                and _sandbox_tagged(pid, self.control_file) == 0)

    def candidates(self) -> set:
        snap = _proc_snapshot()
        protect = {0, 1, os.getpid(), os.getppid()}
        mine = os.getpgrp()
        uid = os.getuid()
        tag = f"{AGENT_TAG_ENV}={self.tag}".encode()
        sb = self._sandbox_tag_usable()
        groups = self.owned_groups(snap)
        found = set(self.cgroup.pids())
        for pid, (ppid, pgid, sid, start, puid, started) in snap.items():
            if puid != uid or pid in protect or pgid == mine:
                continue
            if self.seen.get(pid) == start:
                found.add(pid)
                continue
            if started < self.t0:
                continue
            if pgid in groups:
                found.add(pid)
                continue
            if sid is None:
                try:
                    sid = os.getsid(pid)
                except OSError:
                    sid = None
            if sid in groups:
                found.add(pid)
                continue
            if sb and self._sandbox_marked(pid):
                found.add(pid)
                continue
            if tag in _proc_env_blob(pid):
                found.add(pid)
        found = {p for p in _subtree(snap, found)
                 if p in snap and p not in protect and snap[p][1] != mine
                 and snap[p][4] == uid
                 and (self.seen.get(p) == snap[p][3] or snap[p][5] >= self.t0)}
        return found

    def kill_now(self):
        """One immediate freeze-and-kill of everything that matches, for the
        timeout path: it runs BEFORE the harness drains the runner's pipes
        for up to 5 s, so an orphan holding them cannot keep running (or
        writing the fixtures) through that wait. The full sweep follows."""
        self.poll()
        cands = self.candidates()
        for sig in (signal.SIGSTOP, signal.SIGKILL):
            for pid in cands:
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass


_WARNED_NO_SANDBOX_TAG = False


def sweep_agent_leftovers(watch: "AgentWatch") -> tuple[list, list]:
    """Kill every process the agent left running; return (killed, survivors).

    Called after the runner exited (or was killed) and BEFORE any fixture
    re-assertion or validation. Survivors are processes that still match
    after _SWEEP_ROUNDS of freeze-and-kill (only an unkillable process, or
    one this uid may not signal)."""
    global _WARNED_NO_SANDBOX_TAG
    watch.stop()
    # One last look: the watch thread's view can be up to a poll stale.
    watch.poll()
    if (sys.platform == "darwin" and not _WARNED_NO_SANDBOX_TAG
            and not watch._sandbox_tag_usable()):
        _WARNED_NO_SANDBOX_TAG = True
        print("WARNING: the macOS sandbox tag is unusable (no sandbox-exec, or the harness "
              "itself runs sandboxed); a detached /bin/sh daemon that left the agent's "
              "tree between two polls cannot be found by its env tag on this OS",
              file=sys.stderr)
    killed: set = set()
    try:
        for _ in range(_SWEEP_ROUNDS):
            watch.cgroup.kill()
            cands = watch.candidates()
            if not cands:
                return sorted(killed), []
            for sig in (signal.SIGSTOP, signal.SIGKILL):
                for pid in cands:
                    try:
                        os.kill(pid, sig)
                    except OSError:
                        pass
            for g in watch.owned_groups(_proc_snapshot()):
                if g not in (0, 1, os.getpgrp()):
                    try:
                        os.killpg(g, signal.SIGKILL)
                    except OSError:
                        pass
            killed |= cands
            # A SIGKILLed orphan is reaped by init within milliseconds.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                snap = _proc_snapshot()
                if not any(p in snap and snap[p][3] == watch.seen.get(p, snap[p][3])
                           for p in cands):
                    break
                time.sleep(0.05)
        return sorted(killed), sorted(watch.candidates())
    finally:
        watch.cgroup.remove()
        if watch._tag_dir:
            import shutil
            shutil.rmtree(watch._tag_dir, ignore_errors=True)


def _install_signal_reaper() -> None:
    """Reap our agent groups before dying, for SIGTERM and SIGINT alike."""
    def _handler(signum, _frame):
        if not begin_stop(signum):
            return      # mid-spawn on this thread: re-raised once registered
        # os._exit skips the interpreter's own flush, and under nohup or a
        # log file stdout is BLOCK-buffered: a stopped campaign lost the last
        # screenful of its log — exactly the lines that say where it stopped.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        # os._exit: no atexit, no threads to join, no chance of a worker
        # spawning a fresh agent on the way out.
        os._exit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Not the main thread, or a platform without it. The timeout path
            # still reaps; only the signalled path degrades.
            pass


def _auth_headers(base_url: str) -> dict:
    """Bearer header for a probe of `base_url`, under the RUNNER's key policy
    (runner.resolve_api_key / _key_endpoint_trusted): the ambient key goes
    only to loopback or OPENBEAST_AGENT_INFERENCE_URL. The probes used to
    attach OPENBEAST_API_KEY / OPENAI_API_KEY to ANY --base-url — in
    cleartext over http — while the runner child went keyless against the
    same host (review finding campaign-evals: harness leaks the ambient
    key). One function decides for both, so they cannot disagree. EVERY
    probe must build its headers here: two later probes (the /v1/models
    fallback in _fetch_server_slots and capture_remote_server_info) read
    os.environ directly and reopened the leak (review 1116);
    tests/test_eval_key_policy.py now drives every probe."""
    try:
        from runner import resolve_api_key
    except Exception:
        return {}          # no runner, no agent: fail closed, send nothing
    key = resolve_api_key(None, base_url)
    return {} if key == "not-needed" else {"Authorization": f"Bearer {key}"}


def detect_model(base_url: str) -> str:
    """Query /v1/models on the llama.cpp server. Returns the alias the model
    was launched with (set via the -a flag in our serve scripts)."""
    try:
        url = base_url.rstrip("/") + "/models"
        # /v1/models is key-protected on a keyed server (LLAMA_API_KEY);
        # completions inherit the key via the runner's env resolution.
        headers = _auth_headers(base_url)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("data", [])
        if models:
            return models[0].get("id", "unknown")
    except Exception:
        pass
    return "unknown"


def _fetch_server_slots(base_url: str) -> int | None:
    """Number of parallel slots (-np) on the llama.cpp server, from /props.

    /props lives at the SERVER ROOT, not under /v1 — and beast-gate's path
    allowlist blocks it on gated installs, so this is best-effort. Returns
    None when unreadable."""
    try:
        root = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
        headers = _auth_headers(base_url)
        req = urllib.request.Request(root + "/props", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        n = data.get("total_slots")
        return int(n) if n else None
    except Exception:
        pass
    # Not llama.cpp (vLLM, TensorFold, the hydra proxy): /props is absent.
    # Some OpenAI-compatible servers publish their batch limit on the model
    # card; use it when present. Otherwise the caller needs --slots.
    try:
        headers = _auth_headers(base_url)     # never the raw ambient key
        req = urllib.request.Request(base_url.rstrip("/") + "/models", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for m in data.get("data", [])[:1]:
            n = m.get("max_num_seqs") or (m.get("meta") or {}).get("max_num_seqs")
            if n:
                return int(n)
    except Exception:
        pass
    return None


_GATE_PROBE_N = 1024


def _fetch_gate_inflight_cap(base_url: str) -> int | None:
    """beast-gate's per-device in-flight cap (EDGE_MAX_INFLIGHT), or None
    when the endpoint is not a gate or the cap cannot be read.

    Every agent of a run shares ONE device key, so the gate admits at most
    that many concurrent generations: --jobs 4 against spark-1's default
    of 2 got 429 on half its generations, each one burning an agent
    iteration and turning failing units into unbanked server_errors
    (review 1116). Nothing generates here: a gate is recognised by the
    X-OpenBeast-Request-Id header on /v1/models, and only then asked for a
    chat completion with n=1024, which it refuses with a 400 naming its
    cap before anything is forwarded (agents/edge.py, the fan-out check).
    A non-gate endpoint never gets the POST."""
    import urllib.error
    headers = _auth_headers(base_url)
    root = base_url.rstrip("/")
    try:
        req = urllib.request.Request(root + "/models", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            if not resp.headers.get("X-OpenBeast-Request-Id"):
                return None
            data = json.loads(resp.read().decode("utf-8"))
        model = ((data.get("data") or [{}])[0]).get("id") or "probe"
    except Exception:  # noqa: BLE001 — best-effort, like every probe here
        return None
    body = json.dumps({"model": model, "max_tokens": 1, "n": _GATE_PROBE_N,
                       "messages": [{"role": "user", "content": "."}]}).encode()
    req = urllib.request.Request(root + "/chat/completions", data=body, method="POST",
                                 headers={**headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10):
            return None          # admitted: a cap >= 1024 is no constraint
    except urllib.error.HTTPError as e:
        try:
            msg = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return None
        m = re.search(r"may run at most (\d+) at once", msg)
        return int(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def capture_remote_server_info(base_url: str, declared_rb: str | None = None) -> dict:
    """Provenance for a server capture_server_config cannot see: a remote
    endpoint, or a local non-llama engine (vLLM, TensorFold). Reads the
    OpenAI model card (/v1/models) for the engine (`owned_by`) and context
    length, and takes the reasoning budget from what the operator DECLARED
    (--reasoning-budget / OPENBEAST_EVAL_REASONING_BUDGET) — never from
    `pgrep llama-server`, which describes some other process on this host.
    "0" means thinking off. An undeclared budget is recorded as unknown, and
    the cache era falls back to uncapped, as before."""
    info: dict = {"source": "/v1/models"}
    rb = declared_rb if declared_rb not in (None, "") else \
        os.environ.get("OPENBEAST_EVAL_REASONING_BUDGET", "").strip()
    if rb:
        info["reasoning_budget"] = str(rb).strip()
        info["reasoning_budget_source"] = "declared"
    try:
        headers = _auth_headers(base_url)     # never the raw ambient key
        req = urllib.request.Request(base_url.rstrip("/") + "/models", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        m = (data.get("data") or [{}])[0]
        info["model_id"] = m.get("id")
        if m.get("owned_by"):
            info["engine"] = m["owned_by"]
        for k in ("max_model_len", "max_num_seqs", "root"):
            if m.get(k) is not None:
                info[k] = m[k]
    except Exception as e:  # noqa: BLE001 — provenance is best-effort
        info["error"] = type(e).__name__
    return info


def slugify(name: str) -> str:
    """Convert a model name to a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown"


def capture_gpu_info() -> dict:
    """Snapshot the GPU configuration via nvidia-smi.

    Captures every GPU on the host (not just GPU 0) and derives a `host_id`
    string of the form "RTX 5090 ×1" or "RTX 3090 Ti ×2 + RTX 4090 ×1" so
    that benchmark results from different machines can co-exist in the same
    leaderboard. Backward-compatible top-level fields (name, driver_version,
    memory_total_mib, compute_capability) describe GPU 0.

    Returns empty dict if nvidia-smi isn't available."""
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,driver_version,memory.total,compute_cap",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {}
        lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
        gpus = []
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                gpus.append({
                    "name": parts[0],
                    "driver_version": parts[1],
                    "memory_total_mib": int(parts[2]),
                    "compute_capability": parts[3],
                })
        if not gpus:
            return {}
        from collections import Counter
        counts = Counter(g["name"] for g in gpus)
        host_id = " + ".join(f"{name} ×{n}" for name, n in sorted(counts.items()))
        primary = gpus[0]
        return {
            "name": primary["name"],
            "driver_version": primary["driver_version"],
            "memory_total_mib": primary["memory_total_mib"],
            "compute_capability": primary["compute_capability"],
            "host_id": host_id,
            "gpu_count": len(gpus),
            "gpus": gpus,
        }
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return {}


def parse_engine_version(out: str) -> dict:
    """Parse llama-server --version output. Pure, so it can be tested.

    TWO upstream formats, and the second one broke us silently:
      old: "version: 8893 (6217b4958)"
      new: "version: 0.4.0-dev (build 10865, commit d4389a4dd)"
    The regex only knew the old one, so from the day llama.cpp changed the
    line, `build` and `commit` were absent from EVERY row — and because the
    parse failure was a quiet `if m:`, nothing said so. The row kept
    `source_head`, which is the SOURCE TREE's HEAD and is NOT what the binary
    was built from (today: tree 8e126574, binary build 10865 / d4389a4dd). So
    the field recording what actually produced a measurement was empty while
    the populated one named something else.

    An unrecognised third format is reported, not dropped.
    """
    info: dict = {}
    m = re.search(r"^version:\s*\S*\s*\(build\s+(\d+),\s*commit\s+([0-9a-f]+)\)",
                  out, re.MULTILINE)
    if not m:
        m = re.search(r"^version:\s+(\S+)\s+\(([0-9a-f]+)\)\s*$", out, re.MULTILINE)
    if m:
        info["build"], info["commit"] = m.group(1), m.group(2)
    else:
        first = next((ln for ln in out.splitlines() if ln.startswith("version:")), "")
        info["version_raw"] = first.strip() or "(no version line)"
        info["version_parse"] = "UNRECOGNISED — update parse_engine_version in run_eval.py"
    m = re.search(r"^built with\s+(.+?)\s+for\s+(.+?)\s*$", out, re.MULTILINE)
    if m:
        info["compiler"], info["target"] = m.group(1), m.group(2)
    return info


def capture_inference_engine_info() -> dict:
    """Snapshot the llama.cpp binary's version + build commit + compiler.

    Reads from `<repo>/llama.cpp/build/bin/llama-server --version`. Upstream
    has used two formats — `version: 8893 (6217b4958)` and the current
    `version: 0.4.0-dev (build 10865, commit d4389a4dd)` — and both are
    parsed. An unrecognised third one is recorded as `version_raw` +
    `version_parse: UNRECOGNISED` rather than dropped, because the previous
    silent miss meant no row carried the binary's identity at all.
    `CUDA_VISIBLE_DEVICES=""` disables ggml's CUDA init so this call doesn't
    contend for VRAM with a running server. Also captures the llama.cpp
    source-tree HEAD via `git rev-parse HEAD`, which may differ from the
    binary's embedded commit if the source was updated since the last build.

    Returns empty dict on any failure (binary missing, llama.cpp tree gone,
    parse failure)."""
    binary = Path(__file__).resolve().parent.parent / "llama.cpp" / "build" / "bin" / "llama-server"
    info: dict = {"name": "llama.cpp", "binary": str(binary)}
    try:
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        out = (result.stdout or "") + (result.stderr or "")
        info.update(parse_engine_version(out))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ["git", "-C", str(binary.parent.parent.parent), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            info["source_head"] = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return info


def _parse_server_flags(cmdline: str) -> dict:
    """Pull the provenance-relevant flags out of a llama-server command line.

    Server flags ARE experiment config — reasoning budget, slot count, KV
    quant — assembled at launch from serve.sh + openbeast.conf + env, and
    otherwise invisible to results: the 2026-08-20 Qwen3.8 rows ran under a
    gitignored local REASONING_BUDGET=4096 that no file recorded, while the
    Qwen3.6 champion ran uncapped.

    LAST occurrence wins, as it does in llama-server (common/arg.cpp
    assigns in argv order): serve.sh appends the global REASONING_BUDGET
    override AFTER a serve script's baked --reasoning-budget on purpose, so
    reading the first one stamped the baked value into the cache era while
    the server decoded under the override. `--flag=value` is honoured too."""
    info: dict = {"cmdline": cmdline}
    flags = {"--reasoning-budget": "reasoning_budget",
             "--reasoning": "reasoning",
             "-np": "parallel_slots",
             "-c": "context",
             "-ctk": "kv_cache_type",
             "-ctv": "kv_cache_type_v",
             "-m": "model_path",
             "--model": "model_path",
             "--port": "port"}
    toks = cmdline.split()
    for i, tok in enumerate(toks):
        name, eq, val = tok.partition("=")
        if name not in flags:
            continue
        if eq:
            info[flags[name]] = val
        elif i + 1 < len(toks):
            info[flags[name]] = toks[i + 1]
    return info


PROC_ROOT = "/proc"
LLAMA_DEFAULT_PORT = 8080     # llama-server's own default for --port


def _server_port(info: dict) -> int | None:
    """The port a parsed llama-server command line listens on (its default
    8080 when no --port is given), or None if the value is not a number."""
    try:
        return int(info.get("port", LLAMA_DEFAULT_PORT))
    except (TypeError, ValueError):
        return None


def _listening_inodes(port: int) -> set[str]:
    """Socket inodes LISTENing on `port` (any address), from /proc/net."""
    inodes: set[str] = set()
    for name in ("tcp", "tcp6"):
        try:
            with open(os.path.join(PROC_ROOT, "net", name)) as fh:
                next(fh, None)                       # header
                for line in fh:
                    f = line.split()
                    if len(f) < 10 or f[3] != "0A":  # 0A = TCP_LISTEN
                        continue
                    if int(f[1].rsplit(":", 1)[1], 16) == port:
                        inodes.add(f[9])
        except (OSError, ValueError, IndexError):
            continue
    return inodes


def _pid_holds_socket(pid: str, inodes: set[str]) -> bool:
    fd_dir = os.path.join(PROC_ROOT, pid, "fd")
    try:
        fds = os.listdir(fd_dir)
    except OSError:                                  # another uid's process
        return False
    want = {f"socket:[{i}]" for i in inodes}
    for fd in fds:
        try:
            if os.readlink(os.path.join(fd_dir, fd)) in want:
                return True
        except OSError:
            continue
    return False


def _is_local_host(host: str | None) -> bool:
    """Whether `host` names this machine: an address we can bind to."""
    if not host:
        return True
    if host in ("localhost", "0.0.0.0", "::"):
        return True
    import socket
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for family, _, _, _, addr in infos:
        s = socket.socket(family, socket.SOCK_STREAM)
        try:
            s.bind((addr[0], 0))
            return True
        except OSError:
            continue
        finally:
            s.close()
    return False


def capture_server_config(base_url: str = "http://localhost:8080/v1") -> dict:
    """Best-effort snapshot of the llama-server invocation that serves
    `base_url` (local host only — empty when the server runs elsewhere, isn't
    up, or can't be told apart from another one).

    It must be THAT server: the reasoning budget read here becomes the cache
    era (.rbN) and the flags feed the env fingerprint. Taking the first line
    of `pgrep` described whichever llama-server the kernel listed first — a
    ChunkHound sidecar on :8081, or a sibling worktree's measurement server
    — so the run banked its verdicts under the other process's era. The
    listener is resolved by the socket that LISTENs on the port
    (/proc/net/tcp inode -> /proc/<pid>/fd), falling back to the --port on
    the command line (llama-server's default 8080 when absent) for a server
    whose fds this uid cannot read."""
    from urllib.parse import urlparse
    try:
        u = urlparse(base_url)
        host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return {}
    if not _is_local_host(host):
        return {}
    try:
        out = subprocess.run(["pgrep", "-ax", "llama-server"],
                             capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    lines = out.stdout.strip().splitlines() if out.returncode == 0 else []
    procs = []
    for line in lines:
        pid, _, cmdline = line.strip().partition(" ")
        if pid.isdigit():
            procs.append((pid, _parse_server_flags(cmdline.strip())))
    if not procs:
        return {}
    inodes = _listening_inodes(port)
    if inodes:
        owners = [info for pid, info in procs if _pid_holds_socket(pid, inodes)]
        if len(owners) == 1:
            return owners[0]
    by_flag = [info for _, info in procs if _server_port(info) == port]
    if len(by_flag) == 1:
        return by_flag[0]
    # llama-server IS running here, but none can be tied to this port — most
    # often a proxy (beast-gate) in front of it. Say so: an empty record makes
    # the cache era fall back to uncapped, which is wrong for a capped server.
    print(f"WARNING: {len(procs)} local llama-server(s) running, but none can be "
          f"identified as the one serving port {port} (a proxy such as beast-gate "
          f"in front of it, or two candidates). Server config not recorded; the "
          f"cache era falls back to UNCAPPED. Point --base-url at llama-server's "
          f"own port to record it.", file=sys.stderr)
    return {}


def capture_suite_version() -> str:
    """The eval-suite version from evals/SUITE_VERSION (e.g. 'v4').

    Records WHICH benchmark a row ran against so v3.5 and v4 scores are never
    confused on the leaderboard. Returns 'unknown' if the marker is absent."""
    marker = Path(__file__).resolve().parent / "SUITE_VERSION"
    try:
        return marker.read_text().strip() or "unknown"
    except Exception:
        return "unknown"


def capture_runtime_info() -> dict:
    """Agent-runtime provenance: the Python + client-library versions that
    drove the model, plus OpenBeast's own commit. The agent harness
    (agents/runner.py, agents/tools.py, prompts) drives the model and drifts
    independently of the inference engine — without the repo SHA no
    leaderboard row can say which harness it ran under. Each field
    best-effort; empty on failure."""
    import platform
    info = {"python": platform.python_version(), "platform": platform.platform()}
    for pkg in ("openai", "mcp"):
        try:
            from importlib.metadata import version
            info[pkg] = version(pkg)
        except Exception:
            pass
    repo = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            info["openbeast_commit"] = result.stdout.strip()
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            # True = the run used uncommitted local changes; the SHA alone
            # can't reproduce that harness.
            info["openbeast_dirty"] = bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return info


def load_tasks(task_filter: list[str] | None = None) -> list[dict]:
    """Load task definitions from JSON files.

    A task JSON may declare a `variants` array. Each variant becomes its own
    flattened task entry with effective id `{base_id}_{variant_id}`, plus
    `base_id`, `variant_id`, `language`, and `variant_count` fields. Variant
    fields (setup, task, validation, cleanup, language, name, ...) override
    the corresponding top-level fields. Tasks without a `variants` array are
    treated as a single Python variant — `variant_count` = 1, no base_id.

    Filter matching: a filter entry that matches the base_id selects ALL of
    that task's variants; a filter entry matching the effective id selects
    just that variant.
    """
    tasks = []
    for path in sorted(Path(TASKS_DIR).glob("*.json")):
        with open(path) as f:
            task = json.load(f)
        task["_path"] = str(path)

        variants = task.get("variants")
        if not variants:
            if task_filter is None or task["id"] in task_filter:
                tasks.append(task)
            continue

        base_id = task["id"]
        base_name = task.get("name", base_id)
        for variant in variants:
            variant_id = variant["id"]
            effective_id = f"{base_id}_{variant_id}"
            if task_filter is not None and base_id not in task_filter and effective_id not in task_filter:
                continue
            language = variant.get("language", "python")
            t = dict(task)
            t.pop("variants", None)
            for k, v in variant.items():
                if k != "id":
                    t[k] = v
            t["id"] = effective_id
            t["base_id"] = base_id
            t["variant_id"] = variant_id
            t["language"] = language
            t["variant_count"] = len(variants)
            # Preserve the base task's name unless the variant overrides it
            t["name"] = variant.get("name", f"{base_name} [{language}]")
            tasks.append(t)
    return tasks


def diagnostics_flag(env=None) -> tuple[bool, str | None, dict]:
    """Derive the push-diagnostics state ONCE per run (docs/
    LANG_AWARENESS_PLAN.md §3.4): the cache-key component and the agent
    subprocess env must come from the same read or they can disagree.

    Returns (enabled, cache_component, toolchain_versions). The component
    is a toolchain fingerprint, not a boolean — different checker
    toolchains emit different diagnostics."""
    env = os.environ if env is None else env
    enabled = (env.get("OPENBEAST_DIAGNOSTICS", "").strip() == "1"
               or env.get("BEAST_ASSIST", "").strip() == "1")
    if not enabled:
        return False, None, {}
    import hashlib
    versions = dict(toolchain_versions())
    fp = hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()[:8]
    return True, f"diag2-{fp}", versions


def agent_switches(env=None) -> tuple[str | None, dict]:
    """The agent-side env switches that change what a unit's agent does but
    that no other cache-key component can see, read ONCE per run with the
    agent's OWN parsing rules (agents/runner.py _thinking_kwargs /
    _seed_kwargs, agents/tools.py verify_done_enabled / diag_edit_hint_enabled),
    so the key and the request can never disagree:

      OPENBEAST_AGENT_THINKING on|off -> think-on / think-off (anything else
                                         sends nothing, so it is unset here)
      OPENBEAST_AGENT_SEED <int>      -> seed<N>
      OPENBEAST_AGENT_VERIFY_DONE=1   -> vdone   (G9, "test before done")
      OPENBEAST_DIAG_EDIT_HINT=1      -> ehint   (G13, "edit, don't rewrite")
      OPENBEAST_AGENT_ENV_BLOCK=1     -> envb    (G22, first-turn env block)
      OPENBEAST_AGENT_WALL_WARN=1     -> wwarn   (G23, wall-clock notices)

    Returns (component, meta): component `ag-<part>-<part>...` for the cache
    key, None when every switch is unset (legacy keys stay reachable); meta
    is stamped in the results header and on every row. Every caller
    (--seed / --thinking, benchmark_all, profile, a plain env prefix) goes
    through the environment, so this one read covers them all."""
    env = os.environ if env is None else env
    think = env.get("OPENBEAST_AGENT_THINKING", "").strip().lower()
    think = think if think in ("on", "off") else None
    raw_seed = env.get("OPENBEAST_AGENT_SEED", "").strip()
    try:
        seed = int(raw_seed) if raw_seed else None
    except ValueError:
        seed = None
    vdone = env.get("OPENBEAST_AGENT_VERIFY_DONE", "").strip() == "1"
    ehint = env.get("OPENBEAST_DIAG_EDIT_HINT", "").strip() == "1"
    envb = env.get("OPENBEAST_AGENT_ENV_BLOCK", "").strip() == "1"
    wwarn = env.get("OPENBEAST_AGENT_WALL_WARN", "").strip() == "1"
    meta = {"thinking": think, "seed": seed, "verify_done": vdone,
            "diag_edit_hint": ehint, "env_block": envb, "wall_warn": wwarn}
    parts = ([f"think-{think}"] if think else []) + \
        ([f"seed{seed}"] if seed is not None else []) + \
        (["vdone"] if vdone else []) + (["ehint"] if ehint else []) + \
        (["envb"] if envb else []) + (["wwarn"] if wwarn else [])
    return (f"ag-{'-'.join(parts)}" if parts else None), meta


_TOOLCHAINS: dict | None = None


def toolchain_versions() -> dict:
    """First line of each validator/checker toolchain's version output
    (zig, rustc, go, gcc, shellcheck), probed once per process. Absent
    tools are omitted, a probe that fails reads "?"."""
    global _TOOLCHAINS
    if _TOOLCHAINS is None:
        import shutil as _sh
        versions = {}
        for name, cmd in (("zig", ["zig", "version"]),
                          ("rustc", ["rustc", "--version"]),
                          ("go", ["go", "version"]),
                          ("gcc", ["gcc", "-dumpfullversion"]),
                          ("shellcheck", ["shellcheck", "--version"])):
            if _sh.which(cmd[0]):
                try:
                    out = subprocess.run(cmd, capture_output=True, text=True,
                                         timeout=10).stdout.strip()
                    versions[name] = out.splitlines()[0][:60] if out else "?"
                except Exception:
                    versions[name] = "?"
        _TOOLCHAINS = versions
    return dict(_TOOLCHAINS)


WEIGHTS_REGISTRY = os.path.join(EVALS_DIR, "..", "scripts", "weights.registry")


def weight_identity(model_path: str | None) -> dict:
    """Cheap, deterministic identity of the GGUF a server loaded. A pinned
    weight (scripts/weights.registry row whose byte size matches) is its
    registry sha256; anything else — research quants regenerated under the
    same alias — is size + mtime, so a re-emitted file reads as different.
    Never hashes the file itself (minutes per 20 GB)."""
    if not model_path:
        return {}
    ident: dict = {"file": os.path.basename(model_path)}
    try:
        st = os.stat(model_path)
    except OSError:
        ident["missing"] = True
        return ident
    ident["bytes"] = st.st_size
    try:
        with open(WEIGHTS_REGISTRY) as f:
            for line in f:
                cols = line.rstrip("\n").split("\t")
                if (not line.startswith("#") and len(cols) >= 3
                        and cols[2] == ident["file"] and cols[1] == str(st.st_size)):
                    ident["sha256"] = cols[0]
                    return ident
    except OSError:
        pass
    ident["mtime_ns"] = st.st_mtime_ns
    return ident


def env_fingerprint(server_info: dict | None, engine_info: dict | None) -> tuple[str, dict]:
    """What the cache key does NOT otherwise see (review eval-harness-7):
    the weights' identity, the llama.cpp build, the KV-cache/context serve
    flags, and the validator toolchains + Python. Returns
    (`env1-<sha8>`, the dict it hashes)."""
    import hashlib
    import platform
    server_info = server_info or {}
    engine_info = engine_info or {}
    env = {
        "weights": weight_identity(server_info.get("model_path")),
        "engine": {k: engine_info[k] for k in ("build", "commit") if engine_info.get(k)},
        "serve": {k: server_info[k] for k in ("context", "kv_cache_type", "kv_cache_type_v")
                  if server_info.get(k)},
        "toolchains": {**toolchain_versions(), "python": platform.python_version()},
    }
    fp = hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()[:8]
    return f"env1-{fp}", env


def env_era_enabled() -> bool:
    """OPENBEAST_EVAL_ENV_ERA=1 puts env_fingerprint into the cache key.
    Opt-in because turning it on forks every key into a new era; campaigns
    that pair arms across days should set it."""
    return os.environ.get("OPENBEAST_EVAL_ENV_ERA", "").strip() == "1"


PACKS_DIR = os.path.join(EVALS_DIR, "..", "agents", "packs")
# Language → pack file, and the drift check, live in agents/lang/pack_context
# so the harness and PRODUCTION agents (which get the same pack since the
# Tier-3 SHIP) cannot disagree about which file a pack is or when it is stale.
# Tier 3 (docs/LANG_AWARENESS_PLAN.md §5): only zig has a language pack
# today. Since 2026-11-16 packs-on also hands EVERY unit the host pack
# (agents/lang/host_pack.py: this host's probed userland/libc gaps), so a
# non-zig unit's command line differs with packs on unless the host pack is
# empty; with packs OFF every command line is byte-identical to before.
from lang import pack_context as _pack_context  # noqa: E402
from lang import host_pack as _host_pack  # noqa: E402
PACK_FILES = _pack_context.PACK_FILES
#: Where packs_flag writes the probed host pack (and a language pack joined
#: with it) as content-addressed files the runner reads via --context-file.
#: None = ~/.cache/openbeast/host-pack.
HOST_PACK_DIR = os.environ.get("OPENBEAST_HOST_PACK_DIR") or None


def _verify_pack(lang: str, path: str, data: bytes) -> None:
    """Drift-abort at run start (roadmap R2): the generated section's
    stamped sha must match its bytes, and the pack's zig version must
    match the installed compiler — a pack generated against another
    stdlib is a different experiment and must never run under this era.
    The check itself is pack_context.pack_problem (shared with production)."""
    problem = _pack_context.pack_problem(lang, path, data)
    if problem:
        raise SystemExit(problem)


def packs_flag(env=None) -> tuple[bool, str | None, dict]:
    """Derive the awareness-pack state ONCE per run (same rule as
    diagnostics_flag: the cache-key component and what the agent sees must
    come from one read). Enabled by BEAST_PACKS=1 (user-facing) or
    OPENBEAST_PACKS=1 (internal), or run_eval/benchmark_all --packs.

    Returns (enabled, cache_component, meta) where cache_component is
    `pack1-<sha8>` over the language pack file bytes (concatenated in
    language order) followed by the HOST pack bytes, and meta = {"sha":
    {lang: sha8, "host": sha8}, "paths": {lang: abs_path}, "host": {...},
    "unit_paths": {lang: the file a unit of that language gets}}.

    The host pack (agents/lang/host_pack.py, 2026-11-16) is probed from THIS
    host when the flag is read and goes to EVERY unit, all languages: a zig
    unit gets one file holding the zig pack then the host pack, any other
    unit the host pack alone. A host with no probed gaps renders nothing and
    the component is the language packs' alone."""
    env = os.environ if env is None else env
    enabled = (env.get("BEAST_PACKS", "").strip() == "1"
               or env.get("OPENBEAST_PACKS", "").strip() == "1")
    if not enabled:
        return False, None, {}
    import hashlib
    # Which language-pack bytes this run measures: OPENBEAST_PACK_VARIANT =
    # v1 (default: PACK_FILES, the measured pack production serves) | v2 (the
    # 2026-11-16 pack) | v3 (v2 + the 2026-11-17 thinking-arm claims). The
    # file's bytes go into pack1-<sha8>, so rows of
    # two variants can never share a key. An unknown name aborts: a typo
    # must not silently measure the wrong treatment.
    variant = (env.get("OPENBEAST_PACK_VARIANT") or "").strip().lower() \
        or _pack_context.DEFAULT_PACK_VARIANT
    if variant not in _pack_context.PACK_VARIANTS:
        raise SystemExit(f"OPENBEAST_PACK_VARIANT={variant!r}: not one of "
                         f"{sorted(_pack_context.PACK_VARIANTS)}")
    files = (PACK_FILES if variant == _pack_context.DEFAULT_PACK_VARIANT
             else _pack_context.PACK_VARIANTS[variant])
    shas, paths = {}, {}
    h = hashlib.sha256()
    for lang, fn in sorted(files.items()):
        p = os.path.abspath(os.path.join(PACKS_DIR, fn))
        with open(p, "rb") as f:
            data = f.read()
        _verify_pack(lang, p, data)
        shas[lang] = hashlib.sha256(data).hexdigest()[:8]
        paths[lang] = p
        h.update(data)
    host = _host_pack.build()
    meta = {"sha": shas, "paths": paths, "host": {}, "unit_paths": dict(paths),
            "variant": variant}
    if host.text:
        h.update(host.data)
        shas["host"] = host.sha8
        meta["host"] = {"path": _host_pack.write(host, HOST_PACK_DIR), "system": host.system,
                        "tokens": host.tokens,
                        "facts": [f.key for f in host.facts
                                  if f.line and not f.covered_by_platform_line]}
        for lang, p in paths.items():
            with open(p, "rb") as f:
                both = f.read().rstrip(b"\n") + b"\n\n" + host.data
            meta["unit_paths"][lang] = _host_pack.write(both, HOST_PACK_DIR)
    return True, f"pack1-{h.hexdigest()[:8]}", meta


ESCALATE_LANG_DIR = os.path.join(EVALS_DIR, "..", "agents", "lang")
# What decides the card text a measured unit sees, relative to
# ESCALATE_LANG_DIR. The index says which errors select which claim ids; the
# claim files carry the `summary` sentence that is actually delivered (and are
# what escalate.namespace_wildcards reads); escalate.py is the selector
# (_select, the tie-break, the fan-out limits, render_escalation's header);
# verify.py turns a claim file into the summary that is served; __init__.py is
# the facade that decides whether anything is said under eval at all.
# Only TOP-LEVEL claims/*.json count, matching verify.load_claims: staging/ is
# never served, so editing it must not split the era.
ESCALATE_TREATMENT_FILES = ("escalate-index.json", "escalate.py", "verify.py",
                            "__init__.py")
ESCALATE_KNOBS = ("OPENBEAST_LANG_MAX_CARDS",)
# escalate.py's `from lang import X` modules that are NOT hashed as source,
# and why that is safe: what they decide reaches the component as an OUTCOME
# (_escalate_gate) instead. drivers/packs decide whether an index entry is
# served on this machine (the toolchain-version gate); _proc parses the
# MAX_CARDS knob. Hashing their source would split the era on every driver
# hardening edit; hashing the outcome splits it exactly when what the model
# sees changes. A new import must be added here or to the hashed files — a
# test reads escalate.py's imports and holds this list to it.
ESCALATE_OUTCOME_COVERED = ("_proc", "drivers", "packs")


def _escalate_gate(index_bytes: bytes) -> dict:
    """What this machine would actually serve, from the REAL selector:
    {"gate": escalate.gate_state(index), "max_cards": parsed knob}. The index
    is the one being stamped (so a test tree stays self-consistent); the gate
    and the knob parser are the real ones cards_for uses. Any failure refuses
    the arm: a treatment whose delivery cannot be determined cannot be named."""
    try:
        index = json.loads(index_bytes.decode("utf-8"))
        agents = os.path.abspath(os.path.join(EVALS_DIR, "..", "agents"))
        if agents not in sys.path:
            sys.path.insert(0, agents)
        from lang import escalate as E            # noqa: PLC0415
        return {"gate": E.gate_state(index), "max_cards": E.max_cards_knob()}
    except SystemExit:
        raise
    except Exception as e:                        # noqa: BLE001
        raise SystemExit(f"--escalate: cannot determine what the escalation index "
                         f"serves on this machine ({type(e).__name__}: {e})")


def _escalate_treatment() -> list[tuple[str, bytes]]:
    """(relative path, bytes) for every file that shapes a delivered card,
    in a fixed order. A missing file is a refusal: an arm whose treatment
    cannot be read cannot be named."""
    base = os.path.abspath(ESCALATE_LANG_DIR)
    rels = list(ESCALATE_TREATMENT_FILES)
    claims = os.path.join(base, "claims")
    try:
        rels += sorted(f"claims/{n}" for n in os.listdir(claims)
                       if n.endswith(".json") and os.path.isfile(os.path.join(claims, n)))
    except OSError as e:
        raise SystemExit(f"--escalate: cannot list {claims} ({e})")
    out = []
    for rel in rels:
        path = os.path.join(base, rel)
        try:
            with open(path, "rb") as f:
                out.append((rel, f.read()))
        except OSError as e:
            raise SystemExit(f"--escalate: cannot read {path} ({e})")
    return out


def escalate_flag(diag_on: bool, env=None) -> tuple[bool, str | None, dict]:
    """Derive the beast-lang escalation state ONCE per run (the same rule as
    diagnostics_flag / packs_flag: the cache-key component and what the agent
    sees come from one read). Enabled by BEAST_ESCALATE=1 (user-facing),
    OPENBEAST_ESCALATE=1, or run_eval/benchmark_all --escalate.

    Escalation rides INSIDE the push-diagnostics block (agents/tools.py), so
    it REQUIRES diagnostics: an --escalate arm with the checker off would be
    a measured row that silently did nothing, under a name that says it did.

    Returns (enabled, cache_component, meta). The component is
    `esc1-<sha8>` over the treatment AS DELIVERED: the index, every served
    claim file (the card sentences), the selector and facade source, the
    knobs that change how many cards are shown, and the per-language gate
    outcome on this machine (is the index served, against which toolchain).
    Hashing the index alone let an edited summary or a retuned selector
    replay rows measured under the old text, silently mixing two treatments in one A/B."""
    env = os.environ if env is None else env
    enabled = (env.get("BEAST_ESCALATE", "").strip() == "1"
               or env.get("OPENBEAST_ESCALATE", "").strip() == "1")
    if not enabled:
        return False, None, {}
    if not diag_on:
        raise SystemExit("--escalate / BEAST_ESCALATE=1 needs push-diagnostics on "
                         "(BEAST_ASSIST=1): the card is attached to the checker's "
                         "verdict, so without the checker this arm would measure nothing.")
    import hashlib
    h = hashlib.sha256()
    shas = {}
    files = _escalate_treatment()
    for rel, data in files:
        shas[rel] = hashlib.sha256(data).hexdigest()[:8]
        h.update(f"{rel}\0{len(data)}\0".encode())
        h.update(data)
    # The RAW knob value, not the parsed one: an unset knob and an explicit
    # default then read as two eras, which costs a rerun, never a mixed row.
    knobs = {k: env.get(k, "").strip() for k in ESCALATE_KNOBS}
    for k, v in knobs.items():
        h.update(f"{k}={v}\0".encode())
    # The served OUTCOME of the code not hashed as source (see
    # ESCALATE_OUTCOME_COVERED): per language, the index's toolchain stamp,
    # the installed version as the gate compares it, and whether cards are
    # served at all; plus the parsed card cap. Without it, a zig upgrade or a
    # packs._short_version edit silenced every card under the same esc1 key.
    gate = _escalate_gate(dict(files)["escalate-index.json"])
    h.update(b"gate\0" + json.dumps(gate, sort_keys=True).encode())
    sha8 = h.hexdigest()[:8]
    return True, f"esc1-{sha8}", {"treatment_sha": sha8,
                                  "index_sha": shas["escalate-index.json"],
                                  "files": shas, "knobs": knobs, "gate": gate}


_ITER_LINE = re.compile(r"^\[iter (\d+)/(\d+)\]\s*$", re.MULTILINE)
_DONE_LINE = re.compile(r"^Task complete \(iteration (\d+)\)\s*$", re.MULTILINE)


def _parse_iterations(stdout: str) -> int | None:
    """Iterations the agent actually used (co-primary Tier-3 readout:
    iterations-to-fix). Prefers the runner's `Task complete (iteration N)`
    line; falls back to the last `[iter N/M]` marker. None when neither is
    in the retained stdout tail."""
    m = _DONE_LINE.search(stdout)
    if m:
        return int(m.group(1))
    its = _ITER_LINE.findall(stdout)
    return int(its[-1][0]) if its else None


# Environmental resource exhaustion in validation/setup output. None of these
# is a verdict on the model's code: RLIMIT_NPROC is checked against every
# task the real uid owns machine-wide, so a busy desktop makes a trivial
# `cd dir && ./x` fail to fork; a disk filled by a research quantize makes a
# zig build fail to write its cache. The 2026-09-10..17 rows banked 19 such
# "failures" (ud-iq3s 115_fft_b / 136_gf256_e, 10 of 30 champion C1 zig
# rows), and they replayed into paired verdicts as model regressions.
_ENV_ERROR_RE = re.compile(
    r"Resource temporarily unavailable"
    r"|\bEAGAIN\b"
    r"|\bSystemResources\b"
    r"|thread constructor failed"
    r"|can't start new thread"
    r"|pthread_create\b[^\n]*fail"
    r"|No space left on device"
    r"|\bNoSpaceLeft\b"
    r"|\bENOSPC\b"
    r"|\[Errno 28\]"
    r"|Disk quota exceeded"
    r"|\bDiskQuota\b"
    # run_validation refused: a pinned compiler moved or changed on disk.
    r"|Toolchain changed since the harness started",
    re.IGNORECASE)


def env_error_signature(text: str | None) -> str | None:
    """The first line of `text` showing environmental resource exhaustion
    (fork/thread EAGAIN, ENOSPC), or None. Used both to keep the evidence in
    a truncated validation_output and to refuse caching the row."""
    if not text:
        return None
    m = _ENV_ERROR_RE.search(text)
    if not m:
        return None
    start = text.rfind("\n", 0, m.start()) + 1
    end = text.find("\n", m.end())
    return text[start:end if end != -1 else len(text)].strip()[:200]


# Row reasons recorded BEFORE the agent runs: the unit put no load on the
# model server.
NO_AGENT_REASONS = frozenset({"low_disk", "server_unhealthy", "setup_failed"})


def low_disk(min_free_gb: float | None = None) -> str | None:
    """A message when a filesystem the eval writes to (the cache/results
    tree, $HOME for compiler caches, /tmp for fixtures) is below the floor,
    else None. Floor: OPENBEAST_EVAL_MIN_FREE_GB (default 5; 0 disables).
    /home is shared with the weights and with research quantizes that write
    12-55 GB each; a unit that starts on a nearly-full disk dies in its
    build with ENOSPC, not on its merits."""
    import shutil as _sh
    if min_free_gb is None:
        try:
            min_free_gb = float(os.environ.get("OPENBEAST_EVAL_MIN_FREE_GB", "5"))
        except ValueError:
            min_free_gb = 5.0
    if min_free_gb <= 0:
        return None
    for path in (EVALS_DIR, os.path.expanduser("~"), "/tmp"):
        try:
            free_gb = _sh.disk_usage(path).free / 1e9
        except OSError:
            continue
        if free_gb < min_free_gb:
            return (f"only {free_gb:.1f} GB free on the filesystem holding {path} "
                    f"(floor {min_free_gb:g} GB, OPENBEAST_EVAL_MIN_FREE_GB)")
    return None


def cacheable_result(result: dict) -> bool:
    """A result row may enter the cache only if it is a genuine verdict.
    Environmental deaths must retry clean on the next run:
      exit < 0 — the agent died to a signal (-1 harness wall-timeout,
        -15 a Ctrl-C'd sweep, -9 the OOM reaper). The 2026-08-22 interrupt
        banked four SIGTERM'd in-flight units as 0-token "failures" that
        silently replayed in the 2026-09-08 Phase A' rerun.
      0 completion tokens + failed — the model never produced anything
        (server crash/restart window, dead endpoint); a capability verdict
        requires the model to have actually spoken.
      failed + reason interpreter_error — the agent's python died at
        startup (eval_host.interpreter_startup_failure): the venv broke under
        the harness, as on 2026-11-16 (22 units).
      failed + reason server_error / api_errors >= 1 that ENDED the run
        (api_errors_tail > 0, review 1118; a row without the field keeps the
        old any-error rule) — the server died or errored AFTER the model had
        spoken. An error the run recovered from is a verdict either way, as
        a pass with one always was. The runner burns its remaining
        iterations on connection errors and exits 0 with tokens > 0; the
        2026-09-15 greedy P0 run banked 7 such zig "FAILs" that the Tier-3
        verdict then replayed.
      failed + reason env_error / exhaustion text in validation_output —
        fork/thread EAGAIN or a full disk killed the VALIDATOR, not the
        model's code (see _ENV_ERROR_RE).
        Unless it repeated env_error_bank_after() times for the key
        (env_error_repeats): then the model's own program is the cause.
      failed + reason validator_timeout / run_validation's timeout message
        — the validator ran out of time, which a contended host does to a
        correct solution (its own compile is most of the budget). 17 such
        rows were banked by 2026-10-09 and replayed as model regressions.
        Unless it repeated for the key (validator_timeout_repeats): then the
        solution itself hangs.
    A PASS is always a genuine verdict: infrastructure trouble can only
    make a unit fail, never make it pass."""
    if (result.get("agent_exit_code") or 0) < 0:
        return False
    if result.get("passed"):
        return True
    if not result.get("tokens_completion"):
        return False
    if result.get("reason") in ("server_error", "env_error", "validator_timeout",
                                "interpreter_error", SERVED_MODEL_CHANGED):
        return False
    if (result.get("api_errors") or 0) > 0 and (result.get("api_errors_tail") is None
                                                 or result["api_errors_tail"] > 0):
        return False
    if (env_error_signature(result.get("validation_output"))
            and not result.get("env_error_repeats")):
        return False
    if (validator_timed_out(result.get("validation_output"))
            and not result.get("validator_timeout_repeats")):
        return False
    return True


def env_error_bank_after() -> int:
    """How many env_error verdicts in a row for one cache key make it a
    genuine FAIL (OPENBEAST_EVAL_ENV_ERROR_BANK_AFTER, default 3). Machine
    contention is transient; the same exhaustion three runs running is the
    model's program exhausting the machine."""
    try:
        return max(1, int(os.environ.get("OPENBEAST_EVAL_ENV_ERROR_BANK_AFTER", "3")))
    except ValueError:
        return 3


SUITES_DIR = os.path.join(EVALS_DIR, "suites")


# Renamed at the v5 era break: "v5-fast" was a v4 subset, and v5 is now a
# real suite. The old name still loads, with a note, for scripts in scratch/.
SUITE_ALIASES = {"v5-fast": "v4-fast"}


def suite_universe(suite: dict, tasks: list[dict]) -> set[str]:
    """The unit set a pin must partition. A pin built from v4 runs covers
    the v4 task files only (base ids 01..162), so the task files v5 adds
    never read as drift of a frozen v4 pin."""
    if suite.get("base_suite_version") == "v4":
        return {t["id"] for t in tasks
                if (suite_manifest.task_number(t.get("base_id") or t["id"]) or 0)
                <= suite_manifest.V4_MAX_TASK}
    return {t["id"] for t in tasks}


def default_suite() -> str | None:
    """The manifest a bare run uses: evals/suites/<SUITE_VERSION>.json when
    it is a v5-format manifest, else None (every task file, the v4 rule)."""
    ver = capture_suite_version()
    path = os.path.join(SUITES_DIR, f"{ver}.json")
    try:
        with open(path) as f:
            return ver if suite_manifest.is_manifest(json.load(f)) else None
    except (OSError, ValueError):
        return None


def load_suite(name: str) -> dict:
    """Load a pinned suite subset from evals/suites/{name}.json and validate
    it against the task specs: units + assumed_passed + assumed_failed must
    exactly cover the effective unit set, or the pin has drifted from
    evals/tasks/ and running it would silently mis-score.

    A v5-format manifest (evals/suites/v5.json) loads through
    suite_manifest.load_manifest instead: every unit resolved against
    evals/tasks/, and refused if the coverage floor leaves a cell empty.
    A pin that declares a coverage_floor is refused the same way."""
    if name in SUITE_ALIASES:
        print(f"NOTE: suite {name!r} was renamed {SUITE_ALIASES[name]!r} at the v5 era break")
        name = SUITE_ALIASES[name]
    path = os.path.join(SUITES_DIR, f"{name}.json")
    if not os.path.isfile(path):
        avail = sorted(os.path.splitext(f)[0] for f in os.listdir(SUITES_DIR)
                       if f.endswith(".json")) if os.path.isdir(SUITES_DIR) else []
        raise SystemExit(f"unknown suite {name!r} (available: {', '.join(avail) or 'none'})")
    with open(path) as f:
        suite = json.load(f)
    if suite_manifest.is_manifest(suite):
        man = suite_manifest.load_manifest(name, tasks=load_tasks(None), strict=True)
        man["_manifest"] = True
        return man
    pinned = set(suite["units"]) | set(suite.get("assumed_passed", [])) | set(suite.get("assumed_failed", []))
    all_tasks = load_tasks(None)
    actual = suite_universe(suite, all_tasks)
    if pinned != actual:
        missing = sorted(actual - pinned)[:5]
        stale = sorted(pinned - actual)[:5]
        raise SystemExit(
            f"suite {name!r} has drifted from evals/tasks/ "
            f"({len(actual - pinned)} unpinned units e.g. {missing}; "
            f"{len(pinned - actual)} stale pins e.g. {stale}). "
            f"Regenerate with evals/make_fast_suite.py --generate (deliberate act).")
    if suite.get("coverage_floor"):
        import make_fast_suite  # noqa: PLC0415
        gaps = make_fast_suite.pin_coverage_gaps(suite, suite["coverage_floor"], all_tasks)
        if gaps:
            raise SystemExit(f"suite {name!r} refused: {len(gaps)} cell(s) under its coverage "
                             f"floor {suite['coverage_floor']} (e.g. {gaps[:3]})")
    suite["suite"] = suite.get("suite") or name
    return suite


def _run_reaped(cmd, timeout: int, shell: bool = False, **popen_kw) -> tuple[int, str]:
    """Thin wrapper around tools.run_reaped for eval-harness callers.

    tools.run_reaped only supports shell=True (it manages the process group
    and rlimit itself). For non-shell commands we shell-quote them into a
    single string so the same safety guarantees apply. The string starts with
    `exec`, so the shell replaces itself with the command. Without it, dash
    (Debian's and Ubuntu's /bin/sh) stays alive as the command's parent: a
    normal, dumpable process that holds the same stdin. For a validator that
    stdin is the unlinked script, and any same-uid process could read it
    back through /proc/<sh pid>/fd/0.

    Returns (returncode, merged stdout+stderr). Raises TimeoutExpired.
    """
    if isinstance(cmd, list):
        shell_cmd = "exec " + " ".join(shlex.quote(c) for c in cmd)
    else:
        shell_cmd = cmd
    return run_reaped(shell_cmd, timeout, **popen_kw)


_FIXTURE_PATH = re.compile(r"/tmp/eval[\w\-./]*")


def task_paths(text: str) -> list[str]:
    """Every /tmp/eval* path named in `text`, sorted, without the sentence
    punctuation the pattern swallows.

    _FIXTURE_PATH takes a sentence-final period with the path: "Create
    /tmp/eval_gemm/gemm.c." gave `/tmp/eval_gemm/gemm.c.`. From the R1 path
    guard (2026-09-10) until G24 (2026-11-17), the agent's done-check and
    path guard, and G22's environment block, demanded that dotted name: 27
    of 320 units named one, and GLM-5.3-Flash spent 85 turns in arm C
    copying its solution to `gemm.c.` and compiling `gemm.rs.`. No real
    path here ends in a dot, so trailing dots are always punctuation."""
    return sorted({p.rstrip(".") for p in _FIXTURE_PATH.findall(text)})


def fixture_roots(task: dict) -> list[str]:
    """The task's /tmp/eval* fixture roots (the first path component under
    /tmp of every fixture path its setup, task, validation, cleanup and
    pre_validate name), sorted."""
    spec = " ".join(str(task.get(k, "")) for k in ("setup", "task", "validation",
                                                   "cleanup", "pre_validate"))
    return sorted({"/tmp/" + p[len("/tmp/"):].split("/", 1)[0]
                   for p in task_paths(spec)})


# --- one harness per fixture root, across processes ------------------------------
#
# Every unit uses fixed, global /tmp/eval_* dirs, and setup/cleanup `rm -rf`
# them. Inside one run_eval process the variants of a family are serialized
# on one worker, but nothing stopped a SECOND harness on the host (a prover
# during a sweep, two reviewers' provers, a comparison sweep) from deleting
# the fixture under a live agent or validator: both sides were then scored
# on missing or foreign files, silently, and the FAILs were banked as model
# failures (review 1116: a 173 prove run failed 10 arms with "missing
# /tmp/eval_tg173/tg.py" while a GLM sweep was in /tmp/eval_linz; the
# reference of 167_q15_biquad_a/_c failed 8 runs of 12 side by side; a
# correct 188 reference failed on "missing /tmp/eval_gmm/gmm.py"). Every
# harness now holds an exclusive advisory flock per fixture root for a whole
# unit: a sweep waits for it (and says so at once), a prover refuses at once
# with the holder's pid. flock is per open file description, so threads of
# one process are serialized by a per-root RLock in front of it, and roots
# are taken in sorted order so two units can never deadlock. The lock files
# live in a private per-user dir (not world-writable /tmp, where another uid
# could pre-create them); when that dir is unusable the unit runs unlocked,
# with a warning. The kernel drops a flock when its holder dies, so a killed
# harness never leaves a stale lock.

class FixtureBusy(RuntimeError):
    """A fixture root is held by another harness process."""


_FIXTURE_LOCKS: dict = {}          # root -> [threading.RLock, depth, fd or None]
_FIXTURE_LOCKS_MU = threading.Lock()


def fixture_lock_dir() -> str:
    """Where the lock files live: $OPENBEAST_FIXTURE_LOCK_DIR, else
    ~/.cache/openbeast/fixture-locks."""
    return (os.environ.get("OPENBEAST_FIXTURE_LOCK_DIR")
            or os.path.join(os.path.expanduser("~"), ".cache", "openbeast", "fixture-locks"))


def _fixture_lock_dir() -> str:
    """fixture_lock_dir(), created 0700, refused (OSError) unless it is a
    real dir of ours that nobody else can write."""
    d = fixture_lock_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    st = os.lstat(d)
    if (stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode)
            or st.st_uid != os.getuid() or st.st_mode & 0o077):
        raise OSError(f"{d} is not a private dir of ours; remove it")
    return d


def _lock_path(lock_dir: str, root: str) -> str:
    return os.path.join(lock_dir, os.path.basename(root.rstrip("/")) + ".lock")


def fixture_lock_holder(root: str) -> str:
    """The pid recorded by whoever holds (or last held) the root's lock."""
    try:
        with open(_lock_path(fixture_lock_dir(), root)) as f:
            return f.read(32).strip() or "?"
    except OSError:
        return "?"


def _acquire_root(root: str, lock_dir: str | None, wait: bool, log) -> None:
    import fcntl
    with _FIXTURE_LOCKS_MU:
        ent = _FIXTURE_LOCKS.setdefault(root, [threading.RLock(), 0, None])
    if not ent[0].acquire(blocking=wait):
        raise FixtureBusy(f"{root} is in use by another unit in this process")
    try:
        if ent[1] == 0 and lock_dir:          # first holder in this process: the flock
            fd = os.open(_lock_path(lock_dir, root),
                         os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            told = False
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    holder = fixture_lock_holder(root)
                    if not wait:
                        os.close(fd)
                        raise FixtureBusy(f"{root} is in use by another harness (pid {holder}); "
                                          f"rerun when it finishes") from None
                    if not told:
                        log(f"  Waiting for fixture {root} (held by pid {holder}: another "
                            f"eval or prover on this host is using it)...")
                        told = True
                    time.sleep(0.5)
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
            ent[2] = fd
        ent[1] += 1
    except BaseException:
        ent[0].release()
        raise


def _release_root(root: str) -> None:
    ent = _FIXTURE_LOCKS[root]
    ent[1] -= 1
    if ent[1] == 0 and ent[2] is not None:
        os.close(ent[2])                      # drops the flock
        ent[2] = None
    ent[0].release()


class fixture_lock:
    """A context manager, fixture_lock(task, wait=..., log=...), that holds
    every fixture root of the task for one unit (cleanup -> setup -> agent
    -> validation -> cleanup). Roots are taken in sorted order, re-entrant per thread.
    wait=True blocks and logs the wait at once (the sweep); wait=False
    raises FixtureBusy at once (a prover)."""

    def __init__(self, task: dict, *, wait: bool = True, log=print):
        self.task = task
        self.roots = fixture_roots(task)
        self.wait, self.log, self.held = wait, log, []

    def __enter__(self):
        lock_dir = None
        if self.roots:
            try:
                lock_dir = _fixture_lock_dir()
            except OSError as e:
                self.log(f"  WARNING: no fixture lock for {self.task.get('id')} ({e}); "
                         f"run only one harness on this host")
        try:
            for root in self.roots:
                _acquire_root(root, lock_dir, self.wait, self.log)
                self.held.append(root)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        while self.held:
            _release_root(self.held.pop())
        return False


def unsafe_fixture_roots(task: dict) -> list[str]:
    """The task's /tmp/eval* fixture roots that are NOT safely ours.

    Every fixture is a fixed, global name directly under world-writable
    /tmp, and setup's `mkdir -p` silently accepts a directory that already
    exists — whoever made it. Another local uid could pre-create
    /tmp/eval_debug 0777 with symlinks named like fixture files (setup's
    `printf > …` then overwrites the operator's files), read the model's
    solution, or rewrite it before validation to flip the verdict
    (review finding campaign-evals: /tmp fixture squatting). fs.protected_*
    guard only entries directly in sticky /tmp, not inside such a dir.

    A root is unsafe when it is a symlink, is owned by another uid, or is
    world-writable (or group-writable by a group other than ours). A root
    that does not exist yet is fine."""
    bad = []
    me = os.getuid()
    for root in fixture_roots(task):
        try:
            st = os.lstat(root)
        except FileNotFoundError:
            continue
        except OSError as e:
            bad.append(f"{root} ({e.__class__.__name__})")
            continue
        if stat.S_ISLNK(st.st_mode):
            bad.append(f"{root} (a symlink)")
        elif st.st_uid != me:
            bad.append(f"{root} (owned by uid {st.st_uid}, not {me})")
        elif st.st_mode & 0o002 or (st.st_mode & 0o020 and st.st_gid != os.getgid()):
            # Group-writable is fine under our OWN primary group: Ubuntu's
            # default umask 0002 (user-private groups, the Sparks) makes
            # every fixture dir 0775, and refusing that refused every unit.
            bad.append(f"{root} (writable by others, mode {stat.S_IMODE(st.st_mode):o})")
    return bad


def _refuse_unsafe_fixtures(task: dict, log=print) -> bool:
    bad = unsafe_fixture_roots(task)
    if bad:
        log(f"  Refusing fixtures not safely ours: {', '.join(bad)} — remove them "
            f"(another uid may have planted them) and rerun")
    return bool(bad)


def _run_fixture_cmd(cmd: str, timeout: int) -> tuple[int, str]:
    """Run a task's setup / pre_validate / cleanup shell command in a fresh,
    empty 0700 cwd with PYTHONSAFEPATH=1, removed afterwards.

    Graded code runs as our uid and can find the harness cwd through the
    ppid chain (libproc / /proc) and write there. A fixture command that ran
    in that inherited cwd (`python3 - <<EOF` puts '' on sys.path) imported a
    planted copy.py through tarfile, printed a forged OK and never built the
    next unit's fixture (176). No task command depends on its cwd."""
    import shutil
    import tempfile
    private = tempfile.mkdtemp(prefix="ob-fixture-")
    os.chmod(private, 0o700)
    env = dict(os.environ)
    env["PYTHONSAFEPATH"] = "1"
    try:
        return _run_reaped(cmd, timeout=timeout, shell=True, cwd=private, env=env)
    finally:
        shutil.rmtree(private, ignore_errors=True)


def run_setup(task: dict, log=print) -> bool:
    """Run task setup command. Returns True on success. Timeouts and
    unexpected errors are recorded as failures, not crashes — a single bad
    fixture must not abort the whole sweep (mirrors run_validation)."""
    setup = task.get("setup")
    if not setup:
        return True
    try:
        returncode, output = _run_fixture_cmd(setup, timeout=30)
    except subprocess.TimeoutExpired:
        log("  Setup timed out")
        return False
    except Exception as e:
        log(f"  Setup error: {e}")
        return False
    if returncode != 0:
        log(f"  Setup failed: {output[:200]}")
        return False
    return True


_TOKEN_LINE = re.compile(r"^TOKENS:\s+prompt=(\d+)\s+completion=(\d+)\s+total=(\d+)\s*$", re.MULTILINE)
_COMPACT_LINE = re.compile(r"^COMPACTIONS:\s+(\d+)\s*$", re.MULTILINE)
_API_ERRORS_LINE = re.compile(r"^API_ERRORS:\s+(\d+)\s*$", re.MULTILINE)
_API_ERROR_EVENT = re.compile(r"^\s*API error: ", re.MULTILINE)


def _parse_api_errors(stdout: str) -> int:
    """Failed model calls the runner reported. Prefers the stable
    `API_ERRORS: n` line; a runner that died before printing it (or an
    older runner) is counted from its `API error:` event lines instead."""
    matches = list(_API_ERRORS_LINE.finditer(stdout))
    if matches:
        return int(matches[-1].group(1))
    return len(_API_ERROR_EVENT.findall(stdout))


_API_TAIL_LINE = re.compile(r"^API_ERRORS_TAIL:\s+(\d+)\s*$", re.MULTILINE)
_REASONING_TURNS_LINE = re.compile(r"^REASONING_TURNS:\s+(\d+)\s*$", re.MULTILINE)
_RESPONSE_MODELS_LINE = re.compile(r"^RESPONSE_MODELS:\s+(\{.*\})\s*$", re.MULTILINE)


def _parse_api_errors_tail(stdout: str) -> int | None:
    """Failed model calls AFTER the last successful one (review 1118): > 0
    means API errors ENDED the run. The runner's `API_ERRORS_TAIL: n` line;
    for a runner killed before printing it, whether the last `[iter` block
    in the (tail of the) output holds an `API error:` (1) or not (0); None
    when the output says neither (the caller keeps the old any-error rule)."""
    matches = list(_API_TAIL_LINE.finditer(stdout or ""))
    if matches:
        return int(matches[-1].group(1))
    last_iter = (stdout or "").rfind("\n[iter ")
    if last_iter < 0:
        return None
    return 1 if _API_ERROR_EVENT.search(stdout[last_iter:]) else 0


def _parse_reasoning_turns(stdout: str) -> int | None:
    """Responses that carried reasoning (the EFFECTIVE thinking), or None."""
    matches = list(_REASONING_TURNS_LINE.finditer(stdout or ""))
    return int(matches[-1].group(1)) if matches else None


def _parse_response_models(stdout: str) -> dict | None:
    """{model id: responses} the server named in its responses, or None."""
    matches = list(_RESPONSE_MODELS_LINE.finditer(stdout or ""))
    if not matches:
        return None
    try:
        d = json.loads(matches[-1].group(1))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def served_model_ids(base_url: str) -> list[str] | None:
    """Every id /v1/models lists, or None when it cannot be read."""
    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/models",
                                     headers=_auth_headers(base_url))
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [str(m.get("id")) for m in data.get("data") or [] if m.get("id")]
    except Exception:  # noqa: BLE001 — unreadable is its own answer
        return None


def served_model_problem(base_url: str, want: str) -> str | None:
    """None when /v1/models lists `want`, else why not."""
    ids = served_model_ids(base_url)
    if ids is None:
        return f"{base_url}/models could not be read"
    if want not in ids:
        return f"{base_url}/models lists {ids}, not the served model {want!r}"
    return None


#: Review 1118: how long a run waits for the served model to come back
#: before it stops as an infra stop (OPENBEAST_EVAL_MODEL_WAIT_S), and how
#: often it re-reads /v1/models meanwhile.
SERVED_MODEL_WAIT_S = 600.0
#: The row reason (an infra reason: never cached, never scored) of a unit
#: run while the server was not serving the requested model.
SERVED_MODEL_CHANGED = "served_model_changed"
SERVED_MODEL_POLL_S = 30.0


class ServedModelGuard:
    """The served model, re-checked before and after every unit (one
    /v1/models read, memoised for a few seconds across workers). On a
    mismatch it waits up to wait_s for the model to come back; past that,
    the caller stops the run as an infra stop."""

    def __init__(self, base_url: str, want: str, wait_s: float | None = None,
                 poll_s: float = SERVED_MODEL_POLL_S, memo_s: float = 5.0,
                 probe=None, sleep=time.sleep, clock=time.monotonic):
        self.base_url, self.want = base_url, want
        env_wait = os.environ.get("OPENBEAST_EVAL_MODEL_WAIT_S", "").strip()
        self.wait_s = float(wait_s if wait_s is not None else (env_wait or SERVED_MODEL_WAIT_S))
        self.poll_s, self.memo_s = poll_s, memo_s
        self.probe = probe or (lambda: served_model_problem(base_url, want))
        self.sleep, self.clock = sleep, clock
        self._lock = threading.Lock()
        self._last: tuple[float, str | None] | None = None

    def problem(self, force: bool = False) -> str | None:
        with self._lock:
            now = self.clock()
            if force or self._last is None or now - self._last[0] >= self.memo_s:
                self._last = (now, self.probe())
            return self._last[1]

    def wait_ok(self, log=print, force: bool = False) -> str | None:
        """None once the model is served; the last problem after wait_s.
        force: read /v1/models now, not a memo (after a unit: the swap may
        have happened while it ran)."""
        why = self.problem(force)
        if not why:
            return None
        start = self.clock()
        log(f"  SERVED MODEL: {why}; waiting up to {self.wait_s:.0f}s for it to come back")
        while self.clock() - start < self.wait_s:
            self.sleep(min(self.poll_s, max(0.0, self.wait_s - (self.clock() - start))))
            why = self.problem(force=True)
            if not why:
                log("  SERVED MODEL: back")
                return None
        return why


def _parse_compactions(stdout: str) -> int:
    """Context-compaction events the runner reported (0 when absent)."""
    matches = list(_COMPACT_LINE.finditer(stdout))
    return int(matches[-1].group(1)) if matches else 0


def _parse_tokens(stdout: str) -> dict:
    """Pull the last TOKENS: prompt=… completion=… total=… line from runner stdout."""
    matches = list(_TOKEN_LINE.finditer(stdout))
    if not matches:
        return {"prompt": 0, "completion": 0, "total": 0}
    m = matches[-1]
    return {"prompt": int(m.group(1)), "completion": int(m.group(2)), "total": int(m.group(3))}


def agent_wall_s(task: dict, max_iter: int, cap_override: float | None = None) -> float:
    """The agent's wall budget before timeout_scale. A unit that declares a
    wall cap (task field `max_wall_s`, or `_max_wall_s` from a v5 manifest)
    gets exactly that: v5 replaces `max_iter*60`, which reached 80 minutes
    for a 40-iteration unit at --jobs 4. Units with no cap keep the v4 rule.
    `cap_override` (--unit-wall-cap) can only tighten."""
    cap = task.get("max_wall_s") or task.get("_max_wall_s")
    wall = float(cap) if cap else float(max_iter * 60)
    if cap_override:
        wall = min(wall, float(cap_override))
    return wall


def wall_scaled_turns(task: dict, turns_per_wall_s: float) -> int:
    """Turn budget scaled to the unit's own wall budget (before contention
    scaling): one turn per `turns_per_wall_s` seconds, never fewer than the
    task's declared max_iter. Units without a wall cap keep max_iter."""
    base = int(task.get("max_iter") or 15)
    cap = task.get("max_wall_s") or task.get("_max_wall_s")
    if not cap or turns_per_wall_s <= 0:
        return base
    return max(base, int(float(cap) // turns_per_wall_s))


def run_agent(task: dict, base_url: str, max_iter_override: int | None = None,
              timeout_scale: float = 1.0, env_overlay: dict | None = None,
              wall_s: float | None = None) -> dict:
    """Run the agent against a task. Returns timing, iteration, and token info.

    timeout_scale: multiplier on the per-iteration wall budget. Under --jobs
    parallelism, per-stream decode drops (streams share GPU bandwidth), so the
    single-stream-calibrated 60 s/iteration budget silently shrinks in
    effective tokens — slow-but-progressing units get killed as "timeouts"
    (measured 2026-08-20: 3 spurious fails in the first 134 units of a
    --jobs 4 run). Scaling the budget restores sequential-equivalent
    semantics; the timeout exists to catch hung agents, not slow ones."""
    max_iter = max_iter_override or task.get("max_iter", 15)
    start_time = time.time()

    cmd = [
        sys.executable, RUNNER_PATH,
        "--base-url", base_url,
        "--max-iter", str(max_iter),
        "--workdir", "/tmp",
    ]
    # Tier-3 awareness pack (docs/LANG_AWARENESS_PLAN.md §5): run_eval
    # annotates units whose language has a pack with `_context_file`
    # (underscore key ⇒ excluded from the task hash; the pack1-<sha8> era
    # component carries the pack identity instead). The runner puts the
    # file into the system prompt as a delimited "Background context"
    # block — zero runner.py edits, so the context hash is untouched.
    if task.get("_context_file"):
        cmd += ["--context-file", task["_context_file"]]
    cmd.append(task["task"])

    # R1 path guard (2026-09-10): 25% of the diagnostics-A/B campaign's
    # zig failures were FileNotFound at validation — right code, wrong
    # place. Extract the task's /tmp/eval* paths and hand them to the
    # agent's tool layer (OPENBEAST_TASK_PATHS), which warns on
    # wrong-place writes and refuses task_done while expected files are
    # missing. Passed via env= (NOT os.environ mutation) so --jobs
    # parallel tasks cannot race each other's path sets.
    spec_text = " ".join(str(task.get(k, ""))
                         for k in ("task", "validation", "setup", "cleanup"))
    expected = task_paths(spec_text)
    child_env = dict(os.environ)
    # Per-arm treatment env (diag / escalate / packs, both spellings) for
    # THIS unit: an arm scheduler runs several treatments in one process, so
    # the flags travel per child, never through os.environ. None = unset.
    for k, v in (env_overlay or {}).items():
        if v is None:
            child_env.pop(k, None)
        else:
            child_env[k] = v

    # === THE EVAL MARKER — beast-chat lock L1 (E1) ========================
    # This is the ONE fact about a child of this function that is always
    # true: it is a measured evaluation unit. Everything else the old guard
    # relied on was circumstantial. OPENBEAST_TASK_PATHS (below) is derived
    # from whether the task SPEC happens to name a /tmp/eval path — today all
    # 291 variants do, which is luck, not a guarantee. And the "opt-in",
    # OPENBEAST_BEAST_CHAT, is exported UNCONDITIONALLY by
    # scripts/lib/conf.sh, so `dict(os.environ)` on the line above handed it
    # straight to every child in any configured shell: a reviewer drove a
    # real operator message into a measured unit through this exact path.
    #
    # So: set the marker always, and strip the flag that was never a lock.
    # agents/runner.py:_steering_enabled() checks OPENBEAST_EVAL first, and
    # its remaining opt-in is explicit argv only.
    #
    # This function's code IS in the era, via evals/HARNESS_VERSION (pinned
    # to cache.harness_fingerprint()): a change here that moves argv, env or
    # budgets must bump it — tests/test_cache.py refuses a stale pin.
    child_env["OPENBEAST_EVAL"] = "1"
    child_env.pop("OPENBEAST_BEAST_CHAT", None)
    # Every process this agent starts inherits the tag, so the sweep before
    # validation can find one that detached from the tree (AgentWatch).
    import secrets
    watch = AgentWatch(secrets.token_hex(12))
    child_env[AGENT_TAG_ENV] = watch.tag
    if watch.tag_file:
        child_env["OPENBEAST_AGENT_TAG_FILE"] = watch.tag_file
    if sys.platform.startswith("linux"):
        cmd = [sys.executable, "-c", _SUBREAPER_EXEC] + cmd[1:]
    # ======================================================================

    if expected:
        child_env["OPENBEAST_TASK_PATHS"] = json.dumps(expected)
    else:
        child_env.pop("OPENBEAST_TASK_PATHS", None)
    # Rough budget: 1 min per iteration at single-stream decode, scaled up
    # under parallel contention (see timeout_scale in the docstring).
    budget = wall_s if wall_s is not None else max_iter * 60
    kill_after_s = int(budget * timeout_scale)
    # G23: the runner learns the wall it is killed at (the same number as
    # the communicate() timeout below). Through env, not argv: argv stays
    # as it was. The runner reads it only with OPENBEAST_AGENT_WALL_WARN=1.
    child_env["OPENBEAST_AGENT_WALL_S"] = str(kill_after_s)
    # Low-churn eval mode rides the same env read the runner uses.
    if os.environ.get("OPENBEAST_EVAL_GREEDY", "") == "1":
        child_env["OPENBEAST_EVAL_GREEDY"] = "1"

    # start_new_session so an agent timeout SIGKILLs the runner's whole
    # process group, not just the runner. Its bash-tool children run in
    # their OWN sessions (agents/tools.py run_reaped), so the timeout path
    # walks the tree and kills those groups too (_kill_agent_tree).
    stopped = {"exit_code": -1, "elapsed_seconds": 0.0, "stdout": "(stopped)",
               "stderr": "", "tokens": _parse_tokens(""), "iterations": None}
    proc = _spawn_agent(cmd, child_env)    # registered, or None: stopping
    if proc is None:
        return stopped
    watch.start(proc.pid)
    # Registered BEFORE this check: a begin_stop() that snapshotted the set
    # without us has already set the flag, so one side always kills it.
    if _stopping.is_set():
        _kill_agent_tree(proc.pid)
        proc.kill()
        sweep_agent_leftovers(watch)
        with _live_pgid_lock:
            _live_pgids.discard(proc.pid)
        return stopped
    result: dict = {}
    try:
        stdout, stderr = proc.communicate(timeout=kill_after_s)
        elapsed = time.time() - start_time
        tokens = _parse_tokens(stdout)
        result = {
            "exit_code": proc.returncode,
            "elapsed_seconds": round(elapsed, 1),
            "stdout": stdout[-2000:],  # last 2K of output
            "stderr": stderr[-1000:],
            "tokens": tokens,
            "iterations": _parse_iterations(stdout),
            "compactions": _parse_compactions(stdout),
            "api_errors": _parse_api_errors(stdout),
        }
        return result
    except subprocess.TimeoutExpired:
        watch.poll()                    # the tree as it stands, before any kill
        _kill_agent_tree(proc.pid)
        proc.kill()
        # Before the 5 s drain below: a tool child in its own session that
        # inherited the runner's stdout kept the pipe open, so the drain
        # waited it out while it ran on (and the sweep came too late).
        watch.kill_now()
        # What the runner had flushed before the kill. It prints its TOKENS
        # line only on a normal exit, so today this is almost always
        # without one and the row keeps tokens 0 (scoring counts such rows
        # per board row). Parsed anyway: a recorded 0 for a unit that spent
        # 20 minutes of decode understates TOKENS, and any count beats none.
        partial = ""
        try:
            partial = proc.communicate(timeout=5)[0] or ""
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        elapsed = time.time() - start_time
        result = {
            "exit_code": -1,
            "elapsed_seconds": round(elapsed, 1),
            "stdout": "(timed out)",
            "stderr": "",
            "tokens": _parse_tokens(partial),
            "iterations": None,
        }
        return result
    finally:
        # Nothing the agent started may still run when validation starts:
        # sweep the detached leftovers too (AgentWatch), then assert none
        # survived. `result` is the dict being returned, so the counts and
        # any survivors travel with it to the unit's verdict.
        killed, survivors = sweep_agent_leftovers(watch)
        result["leftovers_killed"] = len(killed)
        if survivors:
            result["leftover_survivors"] = survivors
        # Whatever happened, this group is no longer ours to reap.
        with _live_pgid_lock:
            _live_pgids.discard(proc.pid)


def run_pre_validate(task: dict, log=print):
    """Re-assert harness fixtures right before validation.

    Agents have full bash access and can corrupt fixture files during their
    own testing (e.g. overwriting a healthcheck.sh with `exit 0`). Tasks
    declare `pre_validate` as an optional shell command that re-creates
    *only* the harness fixtures (not any file the agent is supposed to
    write). If absent, this is a no-op.
    """
    cmd = task.get("pre_validate")
    if not cmd:
        return
    try:
        _run_fixture_cmd(cmd, timeout=30)
    except subprocess.TimeoutExpired:
        log("  (pre_validate timed out — continuing to validation)")
    except Exception as e:
        log(f"  (pre_validate error: {e} — continuing to validation)")


VALIDATION_TIMEOUT_S = 30
# What run_validation returns when the VALIDATOR ran out of time. process()
# and cacheable_result match on it: a validator timeout is not a verdict.
VALIDATION_TIMED_OUT = "Validation timed out"


def validator_timed_out(text: str | None) -> bool:
    """Whether a validation_output is run_validation's own timeout message."""
    return bool(text) and text.startswith(VALIDATION_TIMED_OUT)


def contention_scale(jobs: int) -> float:
    """The --jobs multiplier on every agent's wall budget and validator
    timeout. N concurrent streams share GPU bandwidth, so per-stream decode
    slows with N (measured ~1.6x at jobs=4). Linear ramp with margin
    (jobs=4 -> 2.0x) restores sequential-equivalent budgets; see run_agent's
    docstring. A function, not an inline expression, so it is in
    cache.HARNESS_FUNCTIONS and a change to it moves the era."""
    return 1.0 if jobs == 1 else 1.0 + (jobs - 1) / 3.0


# --- Validator isolation (v5) ----------------------------------------------
#
# The solution under test is a child (or grandchild) of the validator and
# runs as the same uid, so everything the validator exposes to a same-uid
# process is exposed to the model's code:
#
#   argv     `ps -ww -o command= -p $PPID` (macOS) or /proc/PPID/cmdline
#            (Linux) is world-readable. `python -c <script>` put the whole
#            validator there, oracle and seed included. Proven cheats passed
#            163, 164, 168, 173, 181, 183, 188, 20, 40 and 121 that way.
#            The script now arrives on STDIN, from an already-unlinked file.
#   cwd      `python -c` puts '' (the harness cwd) on sys.path, so a planted
#            urllib/parse.py there ran inside the validator on a lazy
#            import (176), and stayed for every later unit. Validators now run
#            as `python -I -S -` in a fresh, empty 0700 cwd, removed after the
#            unit.
#   environ  readable with `ps eww` on macOS. The validator gets a scrubbed
#            environment (_validator_env).
#   memory   the bootstrap makes the validator non-dumpable on Linux.
#   orphans  vlib records every solution process group it starts in the
#            private dir, and the harness SIGKILLs them after the validator
#            exits, so a validator killed on timeout cannot leave a solution
#            running into the next unit.

VLIB_PATH = os.path.join(EVALS_DIR, "vlib.py")
# Eval data a solution has no business reading (vlib denies it on macOS).
VALIDATOR_SEAL = tuple(os.path.join(EVALS_DIR, d) for d in ("tasks", "refs", "suites"))
_SEAL_CACHE: tuple | None = None


def validator_seal() -> tuple:
    """VALIDATOR_SEAL plus the same eval data of every OTHER checkout of this
    repository, and the shared git directory itself.

    Sealing only this checkout's evals/ left the rest in reach: a solution
    read evals/tasks/*.json from the main checkout while the eval ran in a
    worktree, and the common .git object store holds every branch's
    committed task JSONs, validators and refs. No solution needs either."""
    global _SEAL_CACHE
    if _SEAL_CACHE is not None:
        return _SEAL_CACHE
    roots, extra = {os.path.dirname(EVALS_DIR)}, []

    def git(*args):
        try:
            r = subprocess.run(["git", "-C", EVALS_DIR, *args], capture_output=True,
                               text=True, timeout=10, stdin=subprocess.DEVNULL)
            return r.stdout if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    common = git("rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    if common:
        extra.append(common)
        roots.add(os.path.dirname(common.rstrip(os.sep)))
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            roots.add(line[len("worktree "):])
    seal = list(VALIDATOR_SEAL)
    for root in sorted(roots):
        for d in ("tasks", "refs", "suites"):
            p = os.path.join(root, "evals", d)
            if p not in seal:
                seal.append(p)
    _SEAL_CACHE = tuple(seal + extra)
    return _SEAL_CACHE

# --- Toolchain pins (review 1116) ---------------------------------------------
#
# A validator runs a compiler as our uid, unsandboxed, and the agent before it
# could write any dir on our PATH (/opt/homebrew/bin, ~/.local/bin, a conda
# bin) or a Go/Rust tree it owns. A `gcc` planted there that compiled the
# REFERENCE passed 36_c with an empty main. So the harness resolves and
# fingerprints the toolchain ONCE, before any agent runs (vlib.pin_toolchain),
# refuses a validation when a name now resolves elsewhere, appeared, or a
# pinned tree changed (vlib.check_toolchain), and hands the pins to the
# validator, whose vlib.tool_path / start_build call the pinned binary.
_TOOL_PINS: dict | None = None
_TOOL_PINS_LOCK = threading.Lock()
TOOLCHAIN_CHANGED = "Toolchain changed since the harness started"


def toolchain_pins() -> dict:
    """The validator toolchain, pinned on first use (run_eval calls it before
    the first agent; prove_task before its first arm)."""
    global _TOOL_PINS
    with _TOOL_PINS_LOCK:
        if _TOOL_PINS is None:
            import vlib  # noqa: PLC0415  (the harness's own copy; validators get it injected)
            _TOOL_PINS = vlib.pin_toolchain(
                vlib.TOOLCHAIN, path=os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin")
        return _TOOL_PINS


def _toolchain_problems(path: str) -> list[str]:
    import vlib  # noqa: PLC0415
    return vlib.check_toolchain(toolchain_pins(), path=path)


_VALIDATOR_ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TZ",
                       "TMPDIR", "TERM", "SDKROOT", "DEVELOPER_DIR",
                       "MACOSX_DEPLOYMENT_TARGET", "CC", "CXX", "GOROOT", "GOPATH",
                       "GOCACHE", "GOMODCACHE", "GOFLAGS", "GOTOOLCHAIN", "GOPROXY",
                       "GOSUMDB", "CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN",
                       "ZIG_GLOBAL_CACHE_DIR", "ZIG_LOCAL_CACHE_DIR", "XDG_CACHE_HOME",
                       "PKG_CONFIG_PATH")

# Python bootstrap: the only program on the validator's argv is `-I -S -`.
# It reads nothing more from stdin, hardens itself, keeps cwd off sys.path,
# injects vlib from source, and runs the validator as __main__.
_PY_BOOT = r"""
def _ob_boot(src, vlib_src, group_file, tool_pins):
    import os, sys, types, linecache
    try:
        fd = os.open(os.devnull, os.O_RDONLY); os.dup2(fd, 0); os.close(fd)
    except OSError:
        pass
    if sys.platform.startswith("linux") and os.environ.get("OPENBEAST_KEEP_DUMPABLE") != "1":
        try:
            import ctypes
            ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE 0
        except Exception:
            pass
    cwd = os.getcwd()
    sys.path[:] = [p for p in sys.path if p and os.path.abspath(p) != cwd]
    m = types.ModuleType("vlib")
    m.__file__ = "<vlib>"
    sys.modules["vlib"] = m
    exec(compile(vlib_src, "<vlib>", "exec", dont_inherit=True), m.__dict__)
    m._GROUP_FILE = group_file
    m._TOOL_PINS = tool_pins
    sys.argv[:] = ["<validator>"]
    linecache.cache["<validator>"] = (len(src), None, src.splitlines(True), "<validator>")
    del globals()["_ob_boot"]
    return compile(src, "<validator>", "exec", dont_inherit=True)


def _ob_done(nonce, exc=None):
    # A clean SystemExit counts as the validator finishing only when the
    # VALIDATOR raised it. One raised by the solution's code (a v4 validator
    # imports the solution in-process) or an os._exit() never gets here with
    # an origin of "<validator>", so the harness sees no completion line.
    if exc is not None:
        if exc.code not in (0, None):
            raise exc
        tb = exc.__traceback__
        while tb is not None and tb.tb_next is not None:
            tb = tb.tb_next
        if tb is None or tb.tb_frame.f_code.co_filename != "<validator>":
            raise exc
    import sys as _s
    _s.stdout.flush()
    _s.stdout.write("\n" + nonce + "\n")
    _s.stdout.flush()
    raise SystemExit(0)


def _ob_run(c, n, done):
    # The completion line lives only in this frame: no module global names
    # it or the function that prints it, so `__main__._ob_n` (a one-line
    # forgery by an in-process solution, review 1116) finds nothing. Frame
    # introspection still can: see "Residual gaps" in evals/README.md.
    g = globals()
    for k in ("_ob_run", "_ob_done"):
        g.pop(k, None)
    try:
        exec(c, g)
    except SystemExit as e:
        done(n, e)
    done(n)


_ob_run(_ob_boot(%r, %r, %r, %r), %r, _ob_done)
"""

# The line a python validator prints when it ran to its end (see _ob_done).
_DONE_PREFIX = "@@ob-validator-done-"

# Shell validators: read the whole script from stdin, give stdin up, run it.
# The script never appears in any argv.
_SH_BOOT = '__ob_v=$(cat) && exec </dev/null && eval "$__ob_v"'


def _validator_env(timeout_scale: float) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k in _VALIDATOR_ENV_KEEP or k.startswith("LC_")}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["OPENBEAST_TIMEOUT_SCALE"] = f"{float(timeout_scale):g}"
    env["OPENBEAST_EVAL"] = "1"
    env["OPENBEAST_VALIDATOR_SEAL"] = os.pathsep.join(validator_seal())
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Every validator, v4 ones too: no go env file (a planted
    # GOTOOLCHAIN=path / GOFLAGS), no toolchain switch to a `go1.99` on PATH,
    # and rustup's proxy pinned to the toolchain the harness fingerprinted
    # (a planted rust-toolchain.toml in a build dir does not override it).
    env["GOENV"] = "off"
    env["GOTOOLCHAIN"] = "local"
    rt = (toolchain_pins().get("rustc") or {}).get("rustup_toolchain")
    if rt and not env.get("RUSTUP_TOOLCHAIN"):
        env["RUSTUP_TOOLCHAIN"] = rt
    return env


# Every validation builds into a private, throw-away cache. A compiled
# reference (or any correct solution) used to stay in a SHARED cache after its
# validator exited: `zig build-exe` outside a build system leaves the whole
# compilation unit in ~/.cache/zig/tmp/<hash>/*.o, and the measured agent's
# bash keeps ~/.cache read-write, so a later agent linked a cached object
# and passed without solving (164's verifier: 40 of 40 cached objects
# linked). The caches below live in the validator's private 0700 dir and are
# deleted with it. GOMODCACHE is safe to move: no unit has a module
# dependency, and GOPROXY=off keeps a build from fetching one.
BUILD_CACHE_ENV = {
    "ZIG_GLOBAL_CACHE_DIR": "zig-global",
    "ZIG_LOCAL_CACHE_DIR": "zig-local",
    "GOCACHE": "go-build",
    "GOMODCACHE": "go-mod",
    "CARGO_TARGET_DIR": "cargo-target",
    "XDG_CACHE_HOME": "xdg-cache",
}


def build_cache_env(root: str) -> dict:
    """Env overrides that put every compiler cache under `root` (created
    0700) and turn shared compiler caches off."""
    os.makedirs(root, mode=0o700, exist_ok=True)
    env = {k: os.path.join(root, sub) for k, sub in BUILD_CACHE_ENV.items()}
    for d in env.values():
        os.makedirs(d, mode=0o700, exist_ok=True)
    env.update({"OPENBEAST_BUILD_CACHE": root,
                "CCACHE_DISABLE": "1",          # ccache: off, never a shared dir
                "SCCACHE_DISABLE": "1",
                "RUSTC_WRAPPER": "",            # no sccache/ccache wrapper either
                "GOPROXY": "off",
                "GOFLAGS": "-modcacherw"})      # so the cache can be deleted
    return env


def _rmtree_force(path: str):
    """shutil.rmtree that also removes read-only entries (a Go module cache
    is 0444/0555 unless -modcacherw held)."""
    import shutil

    def onerr(func, p, _exc):
        try:
            os.chmod(os.path.dirname(p), 0o700)
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    try:
        shutil.rmtree(path, onexc=onerr)        # 3.12+
    except TypeError:
        shutil.rmtree(path, onerror=onerr)
    except FileNotFoundError:
        pass


def _vlib_source() -> str:
    with open(VLIB_PATH) as f:
        return f.read()


def _kill_recorded_groups(group_file: str, since: float | None = None):
    """SIGKILL every solution group the validator recorded in `group_file`.

    The file sits in the validator's 0700 dir, but under --jobs other
    units' agents run as the same uid and could append any pgid to it
    (review 1116). With `since` (the validation's start, epoch s), a group
    is killed only while it has a live member of our uid that started
    after it: everything a validator starts does, while a process that
    was already running (a sibling's agent, a user app) never qualifies."""
    try:
        with open(group_file) as f:
            pgids = {int(x) for x in f.read().split() if x.strip().isdigit()}
    except OSError:
        return
    mine = os.getpgrp()
    pgids = {pg for pg in pgids if pg > 1 and pg != mine}
    if since is not None and pgids:
        uid = os.getuid()
        snap = _proc_snapshot()
        # macOS start times are whole seconds: allow one second of slack
        pgids = {row[1] for row in snap.values()
                 if row[1] in pgids and row[4] == uid and row[5] >= since - 1.0}
    for pg in pgids:
        try:
            os.killpg(pg, signal.SIGKILL)
        except OSError:
            pass


def run_validation(task: dict, timeout_scale: float = 1.0) -> tuple[bool, str]:
    """Run validation. Returns (passed, output).

    timeout_scale: the same contention multiplier run_agent's wall budget
    gets. The validator compiles the solution itself (`zig build-exe -O
    ReleaseFast` is 7-12 s warm on an idle host), so under --jobs the fixed
    30 s was mostly the compiler's, and a correct solution timed out. It is
    also exported to the validator as OPENBEAST_TIMEOUT_SCALE (vlib.t()).

    Isolation (see the block comment above): the script is fed on stdin from
    an unlinked file in a private 0700 dir, the validator runs in a fresh
    empty cwd with a scrubbed environment, python validators run as
    `python -I -S -` with vlib injected, and the private dir is removed
    afterwards."""
    validation = task.get("validation", {})
    vtype = validation.get("type", "bash")
    script = validation.get("script", "")

    if not script:
        return False, "No validation script defined"

    timeout = max(1, int(VALIDATION_TIMEOUT_S * timeout_scale))
    changed = _toolchain_problems(_validator_env(timeout_scale).get("PATH", ""))
    if changed:
        # Not validated: the compiler the validator would call is not the one
        # pinned before the agent ran. An env-error verdict (not banked).
        return False, f"{TOOLCHAIN_CHANGED}: " + "; ".join(changed[:4])
    import tempfile
    private = tempfile.mkdtemp(prefix="ob-validator-")  # 0700
    os.chmod(private, 0o700)
    vcwd = os.path.join(private, "cwd")
    os.mkdir(vcwd, 0o700)
    group_file = os.path.join(private, "pgids")
    started = time.time()
    try:
        if vtype == "python":
            import secrets
            done = f"{_DONE_PREFIX}{secrets.token_hex(16)}@@"
            payload = _PY_BOOT % (script, _vlib_source(), group_file, toolchain_pins(), done)
            cmd = [sys.executable, "-I", "-S", "-"]
        else:
            payload = script
            cmd = _SH_BOOT
        # The script reaches the validator through an UNLINKED file on its
        # stdin: no path to find, no argv to read.
        sfd, spath = tempfile.mkstemp(dir=private, prefix="s-")
        with os.fdopen(sfd, "w+") as sf:
            os.unlink(spath)
            sf.write(payload)
            sf.flush()
            sf.seek(0)
            venv = _validator_env(timeout_scale)
            venv.update(build_cache_env(os.path.join(private, "build-cache")))
            # The validator's temp files (tempfile.mkdtemp, its children's)
            # live in the private dir too, so a validator SIGKILLed at the
            # timeout -- its `finally: rmtree` never runs -- leaves nothing in
            # the shared TMPDIR (176: a copy of a planted /dev/zero link).
            vtmp = os.path.join(private, "tmp")
            os.mkdir(vtmp, 0o700)
            venv["TMPDIR"] = vtmp
            returncode, output = _run_reaped(
                cmd, timeout=timeout, shell=not isinstance(cmd, list),
                stdin=sf, cwd=vcwd, env=venv)
        passed = returncode == 0
        if vtype == "python":
            # Exit 0 is a pass only if the validator itself ran to its end:
            # a solution imported in-process that calls os._exit(0) or
            # sys.exit(0) at import used to pass every v4 python unit.
            completed = done in output
            output = output.replace("\n" + done + "\n", "\n").replace(done, "")
            if passed and not completed:
                passed = False
                output = (output.rstrip() + "\nFAIL: the validator did not run to its end "
                          "(something exited its process early with status 0)").lstrip()
        kept = output.strip()[:500]
        # The 500-char cut must never drop the evidence that the VALIDATOR
        # (not the model's code) died to fork/thread EAGAIN or a full disk:
        # cacheable_result reads validation_output to refuse banking it.
        sig = None if passed else env_error_signature(output)
        if sig and not env_error_signature(kept):
            kept = kept[:480] + "\n[...] " + sig
        return passed, kept
    except subprocess.TimeoutExpired:
        return False, f"{VALIDATION_TIMED_OUT} ({timeout}s)"
    except Exception as e:
        return False, f"Validation error: {e}"
    finally:
        _kill_recorded_groups(group_file, since=started)
        _rmtree_force(private)


def run_cleanup(task: dict, log=print):
    """Run cleanup command. Failures are logged, never fatal — leftover /tmp
    fixtures are annoying, a crashed sweep is worse."""
    cleanup = task.get("cleanup")
    if not cleanup:
        return
    try:
        _run_fixture_cmd(cleanup, timeout=30)
    except subprocess.TimeoutExpired:
        log("  (cleanup timed out)")
    except Exception as e:
        log(f"  (cleanup error: {e})")


def _last_live_results(model_slug: str) -> tuple[str, dict] | None:
    """(file name, data) of the newest results file for this model that came
    from a LIVE run (it recorded the server's flags), or None."""
    try:
        names = sorted((n for n in os.listdir(RESULTS_DIR)
                        if n.startswith(f"eval-{model_slug}-") and n.endswith(".json")),
                       reverse=True)
    except OSError:
        names = []
    for name in names:
        try:
            with open(os.path.join(RESULTS_DIR, name)) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        # Slug prefixes collide (qwen-27b-q5 vs qwen-27b-q5-k-xl): match exactly.
        if data.get("model_slug") == model_slug and (data.get("server") or {}).get("cmdline"):
            return name, data
    return None


def _cache_only_rb(model_slug: str, explicit: str | None) -> tuple[str, str]:
    """The reasoning-budget era a --cache-only replay should look up, and
    where it came from. An explicit value wins; otherwise the newest LIVE
    results file for this model. Returns ("", reason) when nothing says —
    the legacy uncapped era."""
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip(), "--reasoning-budget"
    last = _last_live_results(model_slug)
    if last:
        name, data = last
        return str(data["server"].get("reasoning_budget", "")).strip(), \
            f"from the last live run, {name}"
    return "", "no live run on record; pass --reasoning-budget to choose"


def _write_results(results_path: str, results: dict) -> None:
    """Atomically persist the results dict (tmp + os.replace). Called after
    every task so a crash mid-sweep leaves a readable partial results file
    instead of nothing."""
    tmp = results_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(results, f, indent=2)
    os.replace(tmp, results_path)


# ---------------------------------------------------------------------------
# Arms (v5, brief §6.4): several treatments inside ONE process.
#
# An arm is a named treatment: {packs, diag, escalate}. Each (unit, arm) is a
# job. A base group (variants share /tmp/eval_* fixtures) stays on one worker
# ACROSS arms, so two arms can never overwrite each other's working dirs —
# the reason arms could not run as two processes (risk R10). The arms of one
# unit run next to each other in time, so server and thermal drift hit both.
# ---------------------------------------------------------------------------

BUILTIN_ARMS = dict(suite_manifest.DEFAULT_ARMS)
BOARD_ARM = "L0"
# Languages whose units get push-diagnostics in a diag arm (the "assist
# scope"). The checker can substantively help only compiled languages and
# shell; Python's py_compile is syntax-only, so a Python unit in a diag arm
# would be a near-copy of its L0 row under a different key. Units outside
# the scope run with diagnostics OFF and share the L0 row's cache key.
# OPENBEAST_DIAG_LANGS overrides (comma list of unit languages, or "all").
DEFAULT_DIAG_SCOPE = frozenset({"go", "c", "cpp", "rust", "zig", "shell"})
# unit language -> the checker names agents/tools.py uses
_CHECKER_LANG = {"go": "go", "c": "c", "cpp": "c++", "rust": "rust", "zig": "zig",
                 "shell": "shell", "python": "python"}
# Row reasons for (unit, arm) pairs that were deliberately not measured in
# that arm: never a failure, never scored (scoring.NOT_SCORED_REASONS has
# budget_skip; ablation reads the rest).
NOT_IN_ARM = "not_in_arm"
BUDGET_SKIP = "budget_skip"
_TIER_FALLBACK_S = {"easy": 14, "medium": 35, "hard": 82, "xl": 330}


def diag_scope(env=None) -> frozenset:
    env = os.environ if env is None else env
    raw = env.get("OPENBEAST_DIAG_LANGS", "").strip()
    if not raw:
        return DEFAULT_DIAG_SCOPE
    if raw.lower() == "all":
        return frozenset(_CHECKER_LANG)
    return frozenset(suite_manifest.norm_lang(x) for x in raw.split(",") if x.strip())


def unit_lang(task: dict) -> str:
    return suite_manifest.norm_lang(task.get("language"))


# The suite languages a beast-lang card can be attached to: agents/tools.py
# _ESC_LANG's values (python's checker never prints a card-selecting shape,
# shell has no index), in suite_manifest.norm_lang spelling.
CARD_LANGS = frozenset({"zig", "c", "cpp", "rust", "go"})


def card_eligible_langs(gate: dict | None) -> list[str]:
    """Suite languages whose escalation index is SERVED on this host, from
    escalate.gate_state()'s {lang: {"served": bool, ...}} (G16). An arm named
    "packs+diag+escalate" on a host with only a zig index escalates zig only;
    this is what makes that visible in the header and the ablation report."""
    out = []
    for lang, g in (gate or {}).items():
        name = suite_manifest.norm_lang(lang)
        if name in CARD_LANGS and isinstance(g, dict) and g.get("served"):
            out.append(name)
    return sorted(set(out))


class ArmCtx:
    """One treatment's derived state: the cache components and the env the
    agent subprocess gets, computed from ONE read (the §3.4 rule), plus the
    arm's own results file and counters."""

    def __init__(self, name: str | None, env, results_dir: str, stamp: str):
        self.name = name
        self.diag_on, self.diag_base, self.diag_toolchains = diagnostics_flag(env)
        self.esc_on, self.esc_component, self.esc_meta = escalate_flag(self.diag_on, env)
        self.diag_component = (f"{self.diag_base}+{self.esc_component}"
                               if self.esc_on else self.diag_base)
        self.card_langs = (card_eligible_langs((self.esc_meta.get("gate") or {}).get("gate"))
                           if self.esc_on else [])
        self.packs_on, self.packs_component, self.packs_meta = packs_flag(env)
        self.scope = diag_scope(env)
        suffix = f"-{name}" if name else ""
        self.timing_log = (os.path.join(results_dir, f"diag-timing-{stamp}{suffix}.jsonl")
                           if self.diag_on else None)

    # -- per unit ----------------------------------------------------------
    def in_diag_scope(self, task: dict) -> bool:
        return self.diag_on and unit_lang(task) in self.scope

    def diag_for(self, task: dict) -> str | None:
        """diag2[+esc1] cache component, only for units in assist scope:
        keyed PER UNIT like the pack, so out-of-scope units share rows."""
        return self.diag_component if self.in_diag_scope(task) else None

    def card_eligible(self, task: dict) -> bool:
        """Could an escalation card reach this unit at all on this host:
        escalation on, the unit in diagnostics scope, and its language's
        index served here (G16)."""
        return self.esc_on and self.in_diag_scope(task) and unit_lang(task) in self.card_langs

    def pack_path(self, task: dict) -> str | None:
        """The --context-file a unit gets: its language pack joined with the
        host pack, or the host pack alone (every language), or None."""
        if not self.packs_on:
            return None
        lang_path = self.packs_meta["unit_paths"].get(task.get("language"))
        return lang_path or (self.packs_meta.get("host") or {}).get("path")

    def pack_for(self, task: dict) -> str | None:
        return self.packs_component if self.pack_path(task) else None

    def env_overlay(self, task: dict) -> dict:
        on = self.in_diag_scope(task)
        esc = on and self.esc_on
        d, e = ("1" if on else "0"), ("1" if esc else "0")
        p = "1" if self.packs_on else "0"
        ov = {"OPENBEAST_DIAGNOSTICS": d, "BEAST_ASSIST": d,
              "BEAST_ESCALATE": e, "OPENBEAST_ESCALATE": e,
              "BEAST_PACKS": p, "OPENBEAST_PACKS": p,
              "OPENBEAST_LANG_IN_EVAL": "1" if esc else None,
              "OPENBEAST_DIAG_TIMING_LOG": self.timing_log if on else None,
              "OPENBEAST_DIAG_LANGS": (",".join(sorted(_CHECKER_LANG[x] for x in self.scope
                                                       if x in _CHECKER_LANG))
                                       if on else None),
              "OPENBEAST_EVAL_UNIT": task["id"],
              "OPENBEAST_EVAL_ARM": self.name}
        return ov

    def label(self) -> str:
        bits = [b for b, on in (("packs", self.packs_on), ("diag", self.diag_on),
                                ("escalate", self.esc_on)) if on]
        return "+".join(bits) or "bare"


def arm_env(spec: dict, base=None) -> dict:
    """The env an arm's flags are derived from: the ambient env (knobs such
    as OPENBEAST_LANG_MAX_CARDS) with the arm's three switches pinned in
    both spellings, so an ambient BEAST_ASSIST=1 can never leak into L0."""
    env = dict(os.environ if base is None else base)
    for keys, on in ((("OPENBEAST_DIAGNOSTICS", "BEAST_ASSIST"), spec.get("diag")),
                     (("BEAST_ESCALATE", "OPENBEAST_ESCALATE"), spec.get("escalate")),
                     (("BEAST_PACKS", "OPENBEAST_PACKS"), spec.get("packs"))):
        for k in keys:
            env[k] = "1" if on else "0"
    return env


def unit_est_s(task: dict, prior: dict | None = None) -> float:
    """Planning seconds for a unit: manifest/task est_s, else the unit's
    prior measured elapsed_seconds (cache), else its tier rate."""
    for k in ("_est_s", "est_s"):
        if task.get(k):
            return float(task[k])
    if prior and prior.get(task["id"]):
        return float(prior[task["id"]])
    return float(_TIER_FALLBACK_S.get(suite_manifest.tier_of(task.get("difficulty")), 35))


def order_groups_longest_first(groups: list[list], weight) -> list[list]:
    """Groups sorted by total planned seconds, longest first (stable on
    ties). One base group runs on one worker across all arms, so the
    largest group sets the tail; starting it last is the classic LPT miss."""
    return sorted(groups, key=lambda g: -sum(weight(j) for j in g))


def _summarize_diag_log(path: str | None) -> tuple[dict | None, dict | None]:
    """(latency percentiles, exposure) from an arm's diag JSONL. Exposure
    answers "did the mechanism fire at all" per unit, so a null result can
    be told apart from a mechanism that never ran (risk R9)."""
    if not path or not os.path.exists(path):
        return None, None
    rows = []
    with open(path) as f:
        for ln in f:
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
    ms = sorted(r["ms"] for r in rows if "ms" in r)
    lat = None
    if ms:
        def _pct(q: float) -> float:
            return ms[min(len(ms) - 1, int(q * len(ms)))]
        lat = {"n": len(ms), "p50": _pct(.5), "p95": _pct(.95), "p99": _pct(.99), "max": ms[-1]}
    per: dict = {}
    for r in rows:
        u = r.get("unit")
        if not u:
            continue
        e = per.setdefault(u, {"writes": 0, "errors": 0, "failed_checks": 0,
                               "hint_fired": False, "card_fired": False})
        e["writes"] += 1
        e["errors"] += int(r.get("errors") or 0)
        e["failed_checks"] += int((r.get("rc") or 0) != 0)
        e["hint_fired"] = e["hint_fired"] or bool(r.get("hint_fired"))
        e["card_fired"] = e["card_fired"] or bool(r.get("card_fired"))
    exposure = {"units_exposed": len(per),
                "units_with_failed_check": sum(1 for e in per.values() if e["failed_checks"]),
                "units_hint_fired": sum(1 for e in per.values() if e["hint_fired"]),
                "units_card_fired": sum(1 for e in per.values() if e["card_fired"]),
                "per_unit": per}
    return lat, exposure


def run_eval(
    task_filter: list[str] | None = None,
    base_url: str = "http://localhost:8080/v1",
    max_iter_override: int | None = None,
    model_name: str | None = None,
    use_cache: bool = True,
    cache_only: bool = False,
    health_check=None,
    recover_cb=None,
    jobs: int = 1,
    suite: str | None = None,
    reasoning_budget: str | None = None,
    arms: list[str] | None = None,
    arm_units: dict | None = None,
    arm_meta: dict | None = None,
    deadline: float | None = None,
    slots: int | None = None,
    unit_wall_cap: float | None = None,
    pair_id: str | None = None,
    turns_per_wall_s: float | None = None,
    wall_scale: float = 1.0,
) -> dict:
    """Run the full eval suite. Returns results dict.

    turns_per_wall_s: wall-scaled turn budget (2026-11-16). Each unit gets
    max(its own max_iter, wall_s / turns_per_wall_s) turns, ignoring the
    manifest's max_iter_ceiling, so the unit's wall budget (which v5 already
    sizes per problem: 600/900/1500 s by tier) is the binding limit and the
    turn cap only stops a loop. The GLM-5.3-Flash v5 L0 audit found most
    failing units ended on max_iterations after 150-400 s of a 1,200-1,800 s
    wall. The effective budget is in the cache key (.mi<N>), so these rows
    never replay into, or from, default-budget rows.

    wall_scale: multiplies every unit's agent wall (after its own or the
    manifest's max_wall_s, before contention scaling). The turn budget from
    turns_per_wall_s is still computed from the BASE wall, so an arm can keep
    another arm's turns while getting more time: a thinking arm, whose turns
    are slower, measured at the same turns as its no-thinking twin
    (2026-11-16 arm C). Recorded in the results header and on each row.

    reasoning_budget: cache_only — the reasoning-budget era to replay
    (e.g. "20480"; "-1" = uncapped). A live run reads it from the server;
    cache_only has no server, so without this it is inferred from the
    newest LIVE results file for the model (see _cache_only_rb). On a LIVE
    run against a server this host cannot inspect (remote, vLLM,
    TensorFold) it is the DECLARED budget, recorded as such.

    cache_only: when True, never invoke the agent. Cache hits replay; cache
    misses are recorded as 'skipped_cache_miss' with passed=False. Used for
    replays of prior runs; never seated (no live host — scoring.py --rebuild
    rescores banked runs).

    health_check / recover_cb: optional pair injected by benchmark_all.py.
    Before each live task, `health_check()` probes /health; if it returns
    False, `recover_cb()` is invoked to kill+restart the serve script. If
    recovery fails the task is recorded as `server_unhealthy` (not cached)
    and the eval aborts — silently grinding through hundreds of dead-server
    tasks is what produced the v3.5 Gemma 24% phantom result.

    jobs: parallel workers. Fixture isolation is by SCHEDULING, not path
    rewriting: every /tmp/eval_* fixture dir belongs to exactly one task
    file (enforced by tests/test_eval_jobs.py), and variants of the same
    base task share theirs — so units are grouped by base task, each group
    runs sequentially on one worker (across every arm), and distinct groups
    run in parallel, longest group first. Prompts, validations, and cache
    keys are untouched, so parallel scores are directly comparable to every
    sequential run ever recorded. Clamped to the server's /props
    total_slots when readable, and to `slots` (--slots) when given — the
    only clamp a non-llama server has.

    arms: e.g. ["L0", "L1", "L2"] — run every unit under each named
    treatment (the manifest's `arms`, else suite_manifest.DEFAULT_ARMS) in
    this one process. Returns {"arms": {arm: results}, "pair_id", "paths"}.
    A (unit, arm) whose request is byte-identical to the board arm's (same
    cache key) is SHARED: the board arm's row is copied, marked
    `shared_from`. arm_units {arm: set(ids)} restricts what an arm
    measures (the profile's stratified/cut plan); the rest are recorded as
    `not_in_arm` rows, never scored. arm_meta {arm: dict} is stamped into
    that arm's harness block (e.g. stratified sampling probabilities).

    deadline: epoch seconds. A unit that would START live after it is
    recorded as `budget_skip` — never run, never cached, never scored.

    unit_wall_cap: global ceiling on any unit's agent wall budget (seconds,
    before timeout_scale); see agent_wall_s."""
    import cache  # local import — keeps run_eval importable in environments without cache.py
    if cache_only and not use_cache:
        raise ValueError("cache_only requires use_cache=True (cache_only is meaningless without cache lookup)")
    suite_pin = None
    manifest = None
    if suite:
        if task_filter is not None:
            raise ValueError("--suite and --tasks are mutually exclusive")
        loaded = load_suite(suite)
        if loaded.get("_manifest"):
            manifest = loaded
            task_filter = sorted(manifest["by_task"])
        else:
            suite_pin = loaded
            suite = loaded.get("suite", suite)
            task_filter = list(suite_pin["units"])
    suite_version = capture_suite_version()
    if not cache_only:
        # Before ANY agent runs: can this interpreter start one at all? Every
        # agent is `sys.executable runner.py`; a broken venv used to turn each
        # unit into a scored FAIL (2026-11-16, docs/HARNESS_NOTES.md).
        import eval_host  # noqa: PLC0415
        bad_interp = eval_host.interpreter_problem()
        if bad_interp:
            raise SystemExit(f"REFUSED: {bad_interp}")
        # Before ANY agent runs: what a validator may call a compiler.
        pinned = toolchain_pins()
        print("Toolchain pinned for validators: " + (", ".join(
            f"{n}={p['path']}" for n, p in sorted(pinned.items()) if p.get("path")) or "none"))

    # --- arms -----------------------------------------------------------
    arm_specs = dict((manifest or {}).get("arms") or BUILTIN_ARMS)
    if arms:
        unknown = [a for a in arms if a not in arm_specs]
        if unknown:
            raise SystemExit(f"unknown arm(s) {unknown}; known: {sorted(arm_specs)}")
        if len(set(arms)) != len(arms):
            raise SystemExit(f"--arms lists an arm twice: {arms}")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if arms:
        ctxs = [ArmCtx(a, arm_env(arm_specs[a]), RESULTS_DIR, timestamp) for a in arms]
        board = (manifest or {}).get("board_arm", BOARD_ARM)
        # The board arm's context, even when this process does not RUN it
        # (a profile's L1+L2 pass after its L0 pass): shared rows and key
        # equality are decided against it.
        base_ctx = next((c for c in ctxs if c.name == board), None) or \
            ArmCtx(board, arm_env(arm_specs.get(board, {})), RESULTS_DIR, timestamp)
    else:
        # Legacy single run: the treatment comes from the ambient env, and
        # is pinned there in BOTH spellings so an ambient rig-wide
        # BEAST_ASSIST=1 / BEAST_PACKS=1 cannot leak into an OFF run.
        ctx = ArmCtx(None, os.environ, RESULTS_DIR, timestamp)
        os.environ["OPENBEAST_DIAGNOSTICS"] = "1" if ctx.diag_on else "0"
        os.environ["BEAST_ASSIST"] = "1" if ctx.diag_on else "0"
        os.environ["BEAST_ESCALATE"] = "1" if ctx.esc_on else "0"
        os.environ["OPENBEAST_ESCALATE"] = "1" if ctx.esc_on else "0"
        os.environ["BEAST_PACKS"] = "1" if ctx.packs_on else "0"
        os.environ["OPENBEAST_PACKS"] = "1" if ctx.packs_on else "0"
        if ctx.esc_on:
            os.environ["OPENBEAST_LANG_IN_EVAL"] = "1"
        else:
            os.environ.pop("OPENBEAST_LANG_IN_EVAL", None)
        if ctx.timing_log:
            os.environ["OPENBEAST_DIAG_TIMING_LOG"] = ctx.timing_log
        else:
            os.environ.pop("OPENBEAST_DIAG_TIMING_LOG", None)
        ctxs, base_ctx = [ctx], ctx

    tasks = load_tasks(task_filter)
    if manifest:
        ceiling = manifest.get("max_iter_ceiling")
        for t in tasks:
            u = manifest["by_task"].get(t["id"])
            if not u:
                continue
            # `_` keys: manifest scheduling metadata never enters task_hash.
            t["_est_s"] = u.get("est_s")
            t["_max_wall_s"] = u.get("max_wall_s") or suite_manifest.default_wall_s(
                t.get("difficulty"))
            t["_role"] = u.get("role", "scored")
            t["_stratum"] = u.get("stratum")
            t["_arms"] = list(u.get("arms") or [])
            t["_manifest_id"] = u["id"]
            if ceiling and not max_iter_override and (t.get("max_iter") or 15) > ceiling:
                t["_max_iter_ceiling"] = ceiling
    if suite_pin and len(tasks) != suite_pin["counts"]["units"]:
        raise SystemExit(f"suite {suite!r} resolved to {len(tasks)} units, "
                         f"pin declares {suite_pin['counts']['units']} — pin/tasks drift")
    if not tasks:
        print("No tasks found.")
        return {}

    if not model_name:
        if cache_only:
            # Cache-only doesn't talk to the server — there might be no
            # server running. Require the user to name the model so the
            # cache key resolves correctly.
            raise ValueError("--cache-only requires --model-name (no live server query in cache-only mode)")
        model_name = detect_model(base_url)
    model_slug = slugify(model_name)
    gpu_info = capture_gpu_info() if not cache_only else None
    engine_info = capture_inference_engine_info() if not cache_only else None
    server_info = capture_server_config(base_url) if not cache_only else None
    if not cache_only and not server_info:
        # Not a local llama-server: record what the endpoint says about
        # itself, and the DECLARED reasoning budget (vLLM / TensorFold /
        # remote rows used to fall back to "uncapped" silently).
        server_info = capture_remote_server_info(base_url, reasoning_budget)
    # Reasoning-budget cache-era component (see cache.cache_key): a finite
    # --reasoning-budget on the live server stamps its value into every
    # cache key so capped and uncapped rows can never replay across eras.
    # Uncapped (absent or -1) omits the component — legacy keys unchanged.
    # cache_only mode has no live server to ask: the era comes from
    # reasoning_budget or the model's newest live results file. Guessing
    # "uncapped" missed every current-era (.rb20480) key and recorded the
    # whole suite as skipped_cache_miss.
    # Review 1118: the served model. The runner requests
    # OPENBEAST_INFERENCE_MODEL (campaign.py / --served-model set it); a live
    # run refuses to start unless /v1/models lists it, and re-checks before
    # and after every unit (ServedModelGuard). Unset, the guard pins the
    # model the server lists first now.
    model_guard = None
    served_model = None
    if not cache_only:
        served_model = os.environ.get("OPENBEAST_INFERENCE_MODEL", "").strip() or None
        if served_model:
            why = served_model_problem(base_url, served_model)
            if why:
                raise SystemExit(f"REFUSED: {why}. The run would measure some other model "
                                 f"under {model_name!r} (--served-model / "
                                 f"OPENBEAST_INFERENCE_MODEL names the id the server serves)")
        else:
            ids = served_model_ids(base_url)
            served_model = ids[0] if ids else None
        if served_model:
            model_guard = ServedModelGuard(base_url, served_model)
            if isinstance(server_info, dict):
                server_info["served_model"] = served_model
    # Review 1118: thinking is always SENT explicitly on a run whose budget
    # is declared (a remote server: TensorFold, vLLM): DeepSeek's template
    # thinks by default, so "nothing sent" under a declared 0 would be a
    # thinking run labelled thinking-off. A declared 0 sends
    # enable_thinking=false, a finite budget true; --thinking wins.
    thinking_source = "requested"
    # A copy, never os.environ: an in-process caller (benchmark_all runs one
    # model after another) must not inherit this run's derived value.
    switch_env = dict(os.environ)
    if switch_env.get("OPENBEAST_AGENT_THINKING", "").strip().lower() not in ("on", "off"):
        thinking_source = "server default"
        rb_decl = str((server_info or {}).get("reasoning_budget", "")).strip()
        if (not cache_only and (server_info or {}).get("reasoning_budget_source") == "declared"
                and rb_decl not in ("", "-1")):
            switch_env["OPENBEAST_AGENT_THINKING"] = "off" if rb_decl == "0" else "on"
            thinking_source = f"from the declared reasoning budget {rb_decl}"
            print(f"Thinking: none requested; sending enable_thinking="
                  f"{switch_env['OPENBEAST_AGENT_THINKING'] == 'on'} explicitly "
                  f"({thinking_source})")
    # Low-churn eval mode (2026-09-10): greedy decoding, own cache era,
    # stamped in provenance. Set via env OPENBEAST_EVAL_GREEDY=1 or the
    # --greedy CLI flag (benchmark_all passes it through as env).
    greedy_mode = os.environ.get("OPENBEAST_EVAL_GREEDY", "") == "1"
    # Agent switches (thinking / seed / verify-done / edit-hint / env-block /
    # wall-warn): one read,
    # used for the key, the header and every row (see agent_switches).
    agent_component, agent_meta = agent_switches(switch_env)
    # ...and the agent gets exactly what was keyed: the normalized values are
    # pinned into every unit's env (None = removed), so an unparseable or
    # mid-run-changed ambient value can never reach a child the key missed.
    agent_env_pin = {
        "OPENBEAST_AGENT_THINKING": agent_meta["thinking"],
        "OPENBEAST_AGENT_SEED": (str(agent_meta["seed"])
                                 if agent_meta["seed"] is not None else None),
        "OPENBEAST_AGENT_VERIFY_DONE": "1" if agent_meta["verify_done"] else None,
        "OPENBEAST_DIAG_EDIT_HINT": "1" if agent_meta["diag_edit_hint"] else None,
        "OPENBEAST_AGENT_ENV_BLOCK": "1" if agent_meta["env_block"] else None,
        "OPENBEAST_AGENT_WALL_WARN": "1" if agent_meta["wall_warn"] else None,
    }
    rb_component = None
    _rb = ""
    if server_info:
        _rb = str(server_info.get("reasoning_budget", "")).strip()
    elif cache_only:
        _rb, _rb_src = _cache_only_rb(model_slug, reasoning_budget)
        print(f"Cache-only reasoning-budget era: {_rb or 'uncapped'} ({_rb_src})")
    if _rb and _rb != "-1":
        rb_component = _rb
    # Environment identity (weights / engine / serve KV flags / toolchains).
    # ALWAYS stamped on live rows and in provenance, so a replay banked
    # under a different environment is counted and disclosed; it enters
    # the cache key only with OPENBEAST_EVAL_ENV_ERA=1 (see env_era_enabled).
    env_component, env_info = (None, None)
    if not cache_only:
        env_component, env_info = env_fingerprint(server_info, engine_info)
    else:
        _last = _last_live_results(model_slug)
        if _last:
            env_component = (_last[1].get("harness") or {}).get("env_component")
            env_info = (_last[1].get("harness") or {}).get("env")
    env_key = env_component if env_era_enabled() else None
    if env_era_enabled() and not env_component:
        raise SystemExit("OPENBEAST_EVAL_ENV_ERA=1 in --cache-only mode needs a live results "
                         "file for this model that recorded its env_component")

    jobs = max(1, int(jobs))
    if cache_only and jobs > 1:
        print(f"--jobs {jobs} ignored in --cache-only mode (replay is disk-bound)")
        jobs = 1
    if jobs > 1:
        probed = _fetch_server_slots(base_url)
        gate_cap = _fetch_gate_inflight_cap(base_url)
        if gate_cap is not None and jobs > gate_cap:
            print(f"--jobs {jobs} clamped to {gate_cap}: beast-gate admits {gate_cap} "
                  f"concurrent generation(s) per device (EDGE_MAX_INFLIGHT) and every "
                  f"agent shares one key; the rest would get 429 and burn iterations. "
                  f"Raise EDGE_MAX_INFLIGHT in the gate host's openbeast.conf and "
                  f"restart the gate to use more (docs/SPARK_HYDRA.md)")
            jobs = gate_cap
        limit = min(x for x in (probed, slots) if x) if (probed or slots) else None
        if limit is not None and jobs > limit:
            src = "--slots" if slots and limit == slots and (not probed or slots <= probed) \
                else "the server"
            print(f"--jobs {jobs} clamped to {limit}: {src} exposes {limit} slot(s) (-np); "
                  f"excess workers would queue server-side and the wait would count "
                  f"against per-task timeouts (MTP configs force -np 1)")
            jobs = limit
        elif limit is None:
            print(f"WARNING: could not read /props total_slots (gated install, or not "
                  f"llama.cpp?) — running --jobs {jobs} unclamped; pass --slots N for a "
                  f"vLLM/TensorFold server, and against an -np 1 (MTP) server this queues "
                  f"and trips per-task timeouts")

    timeout_scale = contention_scale(jobs)

    if arms and not pair_id:
        pair_id = f"{model_slug}-{timestamp}"

    def _results_path(c: ArmCtx) -> str:
        if c.name:
            return os.path.join(RESULTS_DIR, f"eval-{model_slug}-{c.name}-{timestamp}.json")
        return os.path.join(RESULTS_DIR, f"eval-{model_slug}-{timestamp}.json")

    print(f"Eval started — {len(tasks)} tasks" + (f" × arms {','.join(arms)}" if arms else ""))
    print(f"Model:  {model_name}")
    print(f"Server: {base_url}")
    if jobs > 1:
        print(f"Jobs:   {jobs} parallel workers (same-base variants stay sequential across "
              f"arms; longest group first; per-iteration timeout ×{timeout_scale:.2f} for contention)")
    if gpu_info:
        print(f"GPU:    {gpu_info.get('name', '?')} ({gpu_info.get('memory_total_mib', '?')} MiB, driver {gpu_info.get('driver_version', '?')})")
    if engine_info and engine_info.get("build"):
        head = (engine_info.get("source_head") or "")[:9]
        head_note = f"; source HEAD {head}" if head and head != engine_info.get("commit", "") else ""
        print(f"Engine: llama.cpp {engine_info.get('build', '?')} ({engine_info.get('commit', '?')}){head_note}")
    if server_info and server_info.get("cmdline"):
        rb = server_info.get("reasoning_budget", "unlimited (default)")
        print(f"Serve:  -np {server_info.get('parallel_slots', '?')} -c {server_info.get('context', '?')} "
              f"kv {server_info.get('kv_cache_type', '?')} reasoning-budget {rb}")
    elif server_info:
        print(f"Serve:  {server_info.get('engine', 'unknown engine')} "
              f"(via {server_info.get('source')}) reasoning-budget "
              f"{server_info.get('reasoning_budget', 'undeclared')}")
    if greedy_mode:
        print("Decode: GREEDY (low-churn eval mode — leaderboard-ineligible era)")
    if agent_component:
        print(f"Agent:  switches {', '.join(f'{k}={v}' for k, v in agent_meta.items() if v is not None and v is not False)}"
              f" — cache era {agent_component}")
    for c in ctxs:
        tag = f"[{c.name}] " if c.name else ""
        if c.diag_on:
            print(f"Diag:   {tag}push-diagnostics ON for {','.join(sorted(c.scope))} units "
                  f"({', '.join(c.diag_toolchains) or 'no toolchains?'}) — cache era {c.diag_base}")
        if c.esc_on:
            print(f"Escal:  {tag}beast-lang escalation ON (treatment {c.esc_meta['treatment_sha']}, "
                  f"index {c.esc_meta['index_sha']}) — "
                  f"cache era {c.esc_component} (leaderboard-ineligible experiment rows)")
            n_card = sum(1 for t in tasks if c.card_eligible(t))
            print(f"Escal:  {tag}card-eligible languages on this host: "
                  f"{', '.join(c.card_langs) or 'NONE'}; card-eligible units: {n_card}/{len(tasks)}")
        if c.packs_on:
            packed = sum(1 for t in tasks if c.pack_path(t))
            print(f"Packs:  {tag}awareness packs ON, variant {c.packs_meta.get('variant')} "
                  f"({', '.join(f'{k}={v}' for k, v in c.packs_meta['sha'].items())}) — "
                  f"cache era {c.packs_component} on {packed}/{len(tasks)} units "
                  f"(leaderboard-ineligible experiment rows)")
            hp = c.packs_meta.get("host") or {}
            print(f"Packs:  {tag}host pack " + (
                f"{hp['system']} {hp['tokens']}tok, probed gaps: {', '.join(hp['facts'])} — every unit"
                if hp else "empty (no host gaps probed here)"))
    if suite_pin:
        c = suite_pin["counts"]
        print(f"Suite:  {suite} ({c['units']} pinned units = {c['discriminating']} discriminating "
              f"+ {c['tripwires']} tripwires; {c['assumed_passed'] + c['assumed_failed']} imputed; "
              f"leaderboard-ineligible partial of {suite_pin.get('base_suite_version', 'v4')})")
    if manifest:
        n_sc = sum(1 for t in tasks if t.get("_role") == "scored")
        print(f"Suite:  {manifest['suite']} manifest ({n_sc} scored + {len(tasks) - n_sc} probe "
              f"units; no imputation)")
    if deadline:
        print(f"Budget: units starting after {datetime.fromtimestamp(deadline):%H:%M:%S} "
              f"are recorded as budget_skip (never scored)")
    for c in ctxs:
        print(f"Results{' [' + c.name + ']' if c.name else ''}: {_results_path(c)}")
    print("=" * 60)

    def _harness(c: ArmCtx) -> dict:
        h = {"diagnostics": c.diag_on,
             # The eval era (scripts/eval-era.sh): rows either side of
             # a change to it are not comparable, and until this was
             # stamped the board could only infer it from cache keys.
             "era": cache.current_era(),
             "greedy": greedy_mode,
             # Always present (all-unset included), so a file says what the
             # agent ran with instead of leaving "default" to inference.
             "agent_switches": dict(agent_meta),
             # Review 1118: where the thinking value came from ("requested",
             # derived from the declared budget, or the server's default:
             # nothing sent), and the model id checked around every unit.
             "thinking_source": thinking_source,
             **({"served_model": served_model} if served_model else {}),
             **({"agent_component": agent_component} if agent_component else {}),
             "packs": dict(c.packs_meta.get("sha", {})) if c.packs_on else {},
             **({"packs_component": c.packs_component,
                 "pack_variant": c.packs_meta.get("variant")} if c.packs_on else {}),
             **({"host_pack": {k: v for k, v in (c.packs_meta.get("host") or {}).items()
                               if k != "path"}} if c.packs_on else {}),
             **({"escalate": c.esc_meta, "escalate_component": c.esc_component,
                 # G16: which languages can receive a card on this host, and
                 # how many of the run's units that covers (before any arm
                 # plan; ablation.py recounts over the rows actually measured).
                 "escalation_eligible_langs": list(c.card_langs),
                 "card_eligible_units": sum(1 for t in tasks if c.card_eligible(t)),
                 "card_units_total": len(tasks)}
                if c.esc_on else {}),
             **({"toolchains": c.diag_toolchains,
                 "diag_scope": sorted(c.scope)} if c.diag_on else {}),
             **({"rb_component": rb_component} if rb_component else {}),
             **({"env_component": env_component, "env": env_info,
                 "env_in_cache_key": bool(env_key)} if env_component else {}),
             **({"unit_wall_cap": unit_wall_cap} if unit_wall_cap else {})}
        if c.name:
            h.update({"arm": c.name, "arm_label": c.label(), "pair_id": pair_id,
                      "arms_in_process": list(arms), "board_arm": base_ctx.name,
                      **({"arm_meta": arm_meta[c.name]} if arm_meta and c.name in arm_meta else {})})
        if deadline:
            h["deadline"] = deadline
        return h

    states: dict[str | None, dict] = {}
    for c in ctxs:
        states[c.name] = {
            "ctx": c,
            "path": _results_path(c),
            "results": {
                "timestamp": datetime.now().isoformat(),
                "model": model_name,
                "model_slug": model_slug,
                "base_url": base_url,
                "suite_version": suite_version,
                "gpu": gpu_info,
                "inference_engine": engine_info,
                "server": server_info,
                "runtime": capture_runtime_info(),
                "jobs": jobs,
                **({"turns_per_wall_s": turns_per_wall_s} if turns_per_wall_s else {}),
                **({"wall_scale": wall_scale} if wall_scale != 1.0 else {}),
                "suite_selection": suite if suite_pin else None,
                **({"suite_manifest": manifest["suite"]} if manifest else {}),
                "cache_only": cache_only,
                "harness": _harness(c),
                "tasks": [],
                "summary": {"total": len(tasks), "passed": 0, "failed": 0},
            },
            "indexed": {},
            "counters": {"cache_hits": 0, "cache_misses_skipped": 0, "aborted_skips": 0,
                         "env_drift_replays": 0, "shared": 0, "not_in_arm": 0,
                         "budget_skips": 0},
        }

    state_lock = threading.Lock()   # results dicts + counters + results files
    print_lock = threading.Lock()   # whole-task output blocks in parallel mode
    health_lock = threading.Lock()  # one recovery attempt at a time
    abort = threading.Event()       # set on failed recovery — stop starting units
    n_arms = len(ctxs)

    def effective_max_iter_of(task: dict) -> int:
        if turns_per_wall_s and not max_iter_override:
            return wall_scaled_turns(task, turns_per_wall_s)
        mi = max_iter_override or task.get("max_iter", 15)
        if task.get("_max_iter_ceiling"):
            mi = min(mi, task["_max_iter_ceiling"])
        return mi

    def key_for(task: dict, c: ArmCtx) -> str:
        return cache.cache_key(task, model_slug, max_iter=effective_max_iter_of(task),
                               diag=c.diag_for(task), rb=rb_component,
                               greedy=greedy_mode, pack=c.pack_for(task), env=env_key,
                               agent=agent_component)

    def process(idx: int, task: dict, log, c: ArmCtx) -> dict | None:
        """One (unit, arm) end-to-end: cache check → health → setup → agent
        → validate → record → cache → cleanup. Returns the recorded result,
        or None when skipped because the run aborted. `log` collects output
        lines in parallel mode so each unit's block prints contiguously."""
        st = states[c.name]
        counters = st["counters"]
        if abort.is_set():
            with state_lock:
                counters["aborted_skips"] += 1
            return None

        task_id = task["id"]
        task_name = task.get("name", task_id)
        difficulty = task.get("difficulty", "?")
        arm_tag = f" [{c.name}]" if c.name else ""

        log(f"\n[{idx + 1}/{len(tasks)}]{arm_tag} {task_name} ({difficulty})")
        log(f"  Task: {task['task'][:80]}...")

        def record(result: dict) -> dict:
            # Variant metadata, only present for tasks that declared `variants`
            if "base_id" in task:
                result.setdefault("base_id", task["base_id"])
                result.setdefault("variant_id", task["variant_id"])
                result.setdefault("language", task["language"])
                result.setdefault("variant_count", task["variant_count"])
            # Every row names its language, variant or not: evals/ablation.py
            # buckets a row without one as python, so the shell unit 179 sat
            # in the python control contrast (L2 audit, 2026-11-16).
            result.setdefault("language", unit_lang(task))
            # The agent switches this row ran under. A replay comes from a
            # key that carries the same switches (agent_switches), so the
            # current run's values are the row's; legacy rows never had it.
            result.setdefault("agent_thinking", agent_meta["thinking"])
            result.setdefault("agent_seed", agent_meta["seed"])
            if agent_meta["verify_done"]:
                result.setdefault("agent_verify_done", True)
            if agent_meta["diag_edit_hint"]:
                result.setdefault("diag_edit_hint", True)
            if agent_meta["env_block"]:
                result.setdefault("agent_env_block", True)
            if agent_meta["wall_warn"]:
                result.setdefault("agent_wall_warn", True)
            # role/stratum: the manifest's, else the task JSON's own
            # optional fields (a task may declare itself a probe).
            role = task.get("_role") or task.get("role")
            if role:
                result["role"] = role
            stratum = task.get("_stratum") or task.get("stratum")
            if stratum:
                result["stratum"] = stratum
            if c.name:
                result["arm"] = c.name
            with state_lock:
                st["indexed"][idx] = result
                if result.get("reason") in (NOT_IN_ARM, BUDGET_SKIP):
                    pass                                  # not an outcome
                elif result.get("passed"):
                    st["results"]["summary"]["passed"] += 1
                else:
                    st["results"]["summary"]["failed"] += 1
                # Keep results in task order regardless of completion order,
                # and persist incrementally — a crash mid-sweep must not lose
                # the tasks already completed.
                st["results"]["tasks"] = [st["indexed"][k] for k in sorted(st["indexed"])]
                _write_results(st["path"], st["results"])
            return result

        effective_max_iter = effective_max_iter_of(task)

        # Arms: a pair whose request is byte-identical to the board arm's
        # (same key) is the SAME experiment — copy the board arm's row
        # rather than re-roll it (a re-roll would read as a discordant pair
        # caused by nothing). A pair the plan excludes is not measured.
        if c.name and c is not base_ctx:
            if key_for(task, c) == key_for(task, base_ctx):
                base_row = None
                if base_ctx.name in states:
                    base_row = states[base_ctx.name]["indexed"].get(idx)
                if base_row is None and use_cache:
                    base_row = cache.cache_get(key_for(task, base_ctx))
                if base_row is not None:
                    row = dict(base_row)
                    row["shared_from"] = base_ctx.name
                    row.pop("arm", None)
                    with state_lock:
                        counters["shared"] += 1
                    log(f"  SHARED from {base_ctx.name} (identical request)")
                    return record(row)
            allowed = (arm_units or {}).get(c.name)
            planned = task.get("_arms")
            if (allowed is not None and task_id not in allowed) or \
                    (allowed is None and planned is not None and c.name not in planned):
                with state_lock:
                    counters["not_in_arm"] += 1
                log(f"  NOT IN ARM {c.name} (plan)")
                return record({"id": task_id, "name": task_name, "difficulty": difficulty,
                               "model": model_name, "passed": False, "reason": NOT_IN_ARM,
                               "elapsed_seconds": 0})

        # Cache check — same task spec + same model + same max_iter + same
        # agent context = same answer. Saves the agent run and the validation
        # step. Setup/cleanup still run on the live path so /tmp fixtures
        # land for any downstream inspection.
        ck = None
        if use_cache:
            ck = key_for(task, c)
            cached = cache.cache_get(ck)
            if cached is not None:
                cached = dict(cached)
                cached["from_cache"] = True
                with state_lock:
                    counters["cache_hits"] += 1
                    if env_component and cached.get("env_fp") != env_component:
                        counters["env_drift_replays"] += 1
                tag = "PASS" if cached.get("passed") else "FAIL"
                log(f"  CACHED ({cached.get('elapsed_seconds', 0)}s, {tag}) — skipping live run")
                return record(cached)

        # cache_only: never invoke the agent. Record a skipped placeholder.
        if cache_only:
            with state_lock:
                counters["cache_misses_skipped"] += 1
            log("  SKIPPED (cache miss; --cache-only mode)")
            return record({
                "id": task_id, "name": task_name, "difficulty": difficulty,
                "model": model_name, "passed": False,
                "reason": "skipped_cache_miss",
                "elapsed_seconds": 0,
                "from_cache": False,
            })

        # Global deadline (profile --budget-h): a unit that has not started
        # is not a failure, it is unmeasured.
        if deadline is not None and time.time() >= deadline:
            with state_lock:
                counters["budget_skips"] += 1
            log("  BUDGET SKIP (deadline reached before this unit started)")
            return record({"id": task_id, "name": task_name, "difficulty": difficulty,
                           "model": model_name, "passed": False, "reason": BUDGET_SKIP,
                           "elapsed_seconds": 0})

        # Free-space floor before each live unit: a disk filled mid-sweep
        # (a research quantize) turns every following unit into an ENOSPC
        # "failure". Stop starting units instead; a relaunch resumes from
        # the cache once space is back.
        disk_msg = low_disk()
        if disk_msg:
            abort.set()
            log(f"  ABORT: {disk_msg}; skipping all remaining tasks")
            return record({
                "id": task_id, "name": task_name, "difficulty": difficulty,
                "model": model_name, "passed": False, "reason": "low_disk",
                "elapsed_seconds": 0,
            })

        # Health check + recovery before each live task. If the server is
        # gone, restart it once (one worker at a time); if it still won't
        # come back, abort the whole run.
        if health_check is not None:
            with health_lock:
                if abort.is_set():
                    with state_lock:
                        counters["aborted_skips"] += 1
                    return None
                if not health_check():
                    log("  Server unhealthy before task — attempting recovery...")
                    if not (recover_cb and recover_cb()):
                        abort.set()
                        log("  ABORT: server unreachable; skipping all remaining tasks")
                        return record({
                            "id": task_id, "name": task_name, "difficulty": difficulty,
                            "model": model_name, "passed": False,
                            "reason": "server_unhealthy",
                            "elapsed_seconds": 0,
                            "tokens_prompt": 0, "tokens_completion": 0, "tokens_total": 0,
                        })

        # Review 1118: the server still serves the model this run measures
        # (a model swap mid-run would bank another model's rows under this
        # one's name). A mismatch waits for the model to come back, then
        # stops the run as an infra stop.
        if model_guard is not None:
            why = model_guard.wait_ok(log)
            if why:
                abort.set()
                log(f"  ABORT: {why}; stopping the run (infra stop, nothing banked)")
                return record({"id": task_id, "name": task_name, "difficulty": difficulty,
                               "model": model_name, "passed": False,
                               "reason": SERVED_MODEL_CHANGED, "served_model_problem": why,
                               "elapsed_seconds": 0, "tokens_prompt": 0,
                               "tokens_completion": 0, "tokens_total": 0})

        # Cleanup BEFORE setup: every task's setup is `mkdir -p`, so whatever
        # a killed run left in the fixture dir (the signal handler exits
        # without cleaning up) was inherited by the next unit to use it —
        # another model, or the other arm of an A/B. If that agent then wrote
        # nothing, the leftover solution validated and a PASS was banked.
        run_cleanup(task, log=log)

        # Setup, between two checks that every /tmp/eval* fixture root is
        # OURS (see unsafe_fixture_roots): before, so setup never writes
        # through a directory another uid planted; after, for one created in
        # the window between the check and setup's `mkdir -p`.
        if (_refuse_unsafe_fixtures(task, log) or not run_setup(task, log=log)
                or _refuse_unsafe_fixtures(task, log)):
            log("  FAIL (setup failed)")
            return record({
                "id": task_id, "name": task_name, "difficulty": difficulty,
                "passed": False, "reason": "setup_failed",
                "elapsed_seconds": 0,
            })

        # Run agent (the arm's pack, if this unit's language has one)
        log("  Running agent...")
        run_task = dict(task)
        pack_path = c.pack_path(task)
        if pack_path:
            run_task["_context_file"] = pack_path
        else:
            run_task.pop("_context_file", None)
        agent_result = run_agent(run_task, base_url,
                                 effective_max_iter,
                                 timeout_scale=timeout_scale,
                                 env_overlay={**c.env_overlay(task), **agent_env_pin},
                                 wall_s=agent_wall_s(task, effective_max_iter, unit_wall_cap) * wall_scale)
        log(f"  Agent finished in {agent_result['elapsed_seconds']}s")

        # Re-assert fixtures (no-op unless task declares pre_validate).
        run_pre_validate(task, log=log)

        # Validate, unless something the agent started is still running: it
        # could rewrite the solution or the fixture under the validator.
        if agent_result.get("leftover_survivors"):
            passed = False
            validation_output = (f"Not validated: the agent left processes running that "
                                 f"could not be killed: {agent_result['leftover_survivors'][:10]}")
        else:
            passed, validation_output = run_validation(task, timeout_scale=timeout_scale)

        # Infrastructure verdicts (not model verdicts) for a FAILED unit.
        # server_error: the runner saw failed model calls, or the server is
        # gone right after the agent finished — the health check above only
        # runs BEFORE a task, so a server that died mid-task was invisible.
        # env_error: the validator died to fork/thread EAGAIN or a full disk.
        # validator_timeout: the validator ran out of its own time budget.
        api_errors = agent_result.get("api_errors", 0) or 0
        agent_stdout = agent_result.get("stdout") or ""
        # Review 1118: only errors that ENDED the run make a failed row infra
        # (None: the output cannot say, so any error counts, as before).
        api_tail = _parse_api_errors_tail(agent_stdout) if api_errors else 0
        reasoning_turns = _parse_reasoning_turns(agent_stdout)
        response_models = _parse_response_models(agent_stdout)
        model_after = None
        validated_pass = False
        if model_guard is not None:
            model_after = model_guard.wait_ok(log, force=True)
        infra_reason = None
        env_strikes = 0
        vt_strikes = 0
        # interpreter_error: the agent's python died at startup (no model
        # call was made). The environment's fault, never the model's.
        import eval_host  # noqa: PLC0415
        startup_err = None if passed else eval_host.interpreter_startup_failure(agent_result)
        if model_after:
            # The model was not served when the unit ended: its turns may
            # have gone to another model, so neither verdict is this model's.
            infra_reason = SERVED_MODEL_CHANGED
            validated_pass = passed
            passed = False      # a pass of another model is not this model's
            abort.set()
            log(f"  ABORT: {model_after}; stopping the run (infra stop, nothing banked)")
        elif not passed:
            if startup_err:
                infra_reason = "interpreter_error"
            elif api_errors > 0 and (api_tail is None or api_tail > 0):
                infra_reason = "server_error"
            elif (health_check is not None and agent_result["exit_code"] >= 0
                  and not health_check()):
                infra_reason = "server_error"
            elif env_error_signature(validation_output):
                infra_reason = "env_error"
                # Exhaustion the model's OWN program causes (a fork loop,
                # thousands of threads) matches the same text and would
                # rerun live forever, keeping the model off the board. An
                # env_error that keeps coming back for the same key is a
                # verdict: bank it as a plain FAIL.
                if ck is not None:
                    try:
                        strikes = cache.env_error_strike(ck)
                    except OSError:
                        strikes = 0
                    if strikes >= env_error_bank_after():
                        infra_reason = None
                        env_strikes = strikes
            elif validator_timed_out(validation_output):
                infra_reason = "validator_timeout"
                # A solution that really hangs times out every run, and
                # would rerun live forever. Same strike counter and
                # threshold as env_error: repeated for the same key, it is
                # the model's program, and banks as a plain FAIL.
                if ck is not None:
                    try:
                        strikes = cache.env_error_strike(ck)
                    except OSError:
                        strikes = 0
                    if strikes >= env_error_bank_after():
                        infra_reason = None
                        vt_strikes = strikes

        # Record result
        tokens = agent_result.get("tokens") or {"prompt": 0, "completion": 0, "total": 0}
        result = record({
            "id": task_id,
            "name": task_name,
            "difficulty": difficulty,
            "model": model_name,
            "passed": passed,
            "elapsed_seconds": agent_result["elapsed_seconds"],
            "agent_exit_code": agent_result["exit_code"],
            "validation_output": validation_output,
            "tokens_prompt": tokens["prompt"],
            "tokens_completion": tokens["completion"],
            "tokens_total": tokens["total"],
            "iterations": agent_result.get("iterations"),
            "max_iter": effective_max_iter,
            "compactions": agent_result.get("compactions", 0),
            "api_errors": api_errors,
            **({"api_errors_tail": api_tail} if api_errors and api_tail is not None else {}),
            **({"reasoning_turns": reasoning_turns} if reasoning_turns is not None else {}),
            **({"response_models": response_models} if response_models else {}),
            **({"served_model_problem": model_after,
                "validated_pass": validated_pass} if model_after else {}),
            **({"reason": infra_reason} if infra_reason else {}),
            **({"env_error_repeats": env_strikes} if env_strikes else {}),
            **({"validator_timeout_repeats": vt_strikes} if vt_strikes else {}),
            **({"env_fp": env_component} if env_component else {}),
            **({"interpreter_error": startup_err} if startup_err else {}),
        })

        if passed:
            log(f"  PASS ({agent_result['elapsed_seconds']}s)")
        elif infra_reason:
            log(f"  FAIL ({infra_reason}, not cached — retries on the next run): "
                f"{validation_output[:100]}")
        else:
            log(f"  FAIL: {validation_output[:100]}")
        if startup_err:
            # One agent's startup death may be a blip; a broken interpreter
            # would kill every unit after it. Re-check now; stop if broken.
            bad_interp = eval_host.interpreter_problem(force=True)
            if bad_interp:
                abort.set()
                log(f"  ABORT: {bad_interp}")

        # Cache the result for future reruns. We cache both PASS and
        # deterministic FAIL — replaying a known fail is replay-safe.
        # We do NOT cache environmental deaths — they must retry clean:
        #   exit == -1: harness wall-timeout.
        #   exit < 0 generally: the agent died to a SIGNAL (-15 = the user
        #     Ctrl-C'ing a sweep; -9 = OOM reaper). The 2026-08-22 interrupt
        #     banked four SIGTERM'd in-flight units as 0-token "failures"
        #     that silently replayed in the 2026-09-08 Phase A' rerun.
        #   0 completion tokens + failed: the model never produced anything
        #     (server crash/restart window, dead endpoint) — a capability
        #     verdict needs the model to have actually spoken.
        #   server_error / env_error / validator_timeout: see the
        #     infra_reason block above and cacheable_result.
        # The row is cached WITHOUT the arm label: the arm is not part of
        # the key (two arms with one key are one experiment).
        if use_cache and cacheable_result(result):
            try:
                cache.cache_put(ck, {k: v for k, v in result.items() if k != "arm"})
                cache.env_error_strikes_clear(ck)
            except Exception as e:
                log(f"  (cache write failed: {e})")

        # Cleanup
        run_cleanup(task, log=log)
        return result

    # --- scheduling ---------------------------------------------------------
    # Jobs = (unit, arm). Grouped by base task (variants share a /tmp/eval_*
    # fixture dir): a group serializes on one worker ACROSS ARMS, with the
    # arms of each unit adjacent; distinct groups parallelize.
    grouped: dict[str, list[tuple[int, dict, ArmCtx]]] = {}
    for idx, task in enumerate(tasks):
        g = grouped.setdefault(task.get("base_id", task["id"]), [])
        for c in ctxs:
            g.append((idx, task, c))
    groups = list(grouped.values())

    prior: dict = {}
    if jobs > 1 and use_cache:
        # Prior measured time for units with no est_s (the v4 suite): the
        # board-arm key's banked elapsed_seconds.
        for idx, task in enumerate(tasks):
            if task.get("_est_s") or task.get("est_s"):
                continue
            try:
                row = cache.cache_get(key_for(task, base_ctx))
            except Exception:  # noqa: BLE001
                row = None
            if row and row.get("elapsed_seconds"):
                prior[task["id"]] = row["elapsed_seconds"]

    def _job_weight(job) -> float:
        _idx, task, c = job
        planned = task.get("_arms")
        if c.name and planned is not None and c.name not in planned:
            return 0.0
        return unit_est_s(task, prior)

    if jobs == 1:
        for g in groups:
            for idx, task, c in g:
                with fixture_lock(task, wait=True, log=print):
                    process(idx, task, log=print, c=c)
                if abort.is_set():
                    break
            if abort.is_set():
                remaining = len(tasks) * n_arms - sum(len(s["indexed"]) for s in states.values())
                if remaining:
                    print(f"  ({remaining} remaining tasks skipped)")
                break
    else:
        groups = order_groups_longest_first(groups, _job_weight)

        def say_now(msg: str) -> None:
            with print_lock:
                print(msg)

        def run_group(units: list[tuple[int, dict, ArmCtx]]) -> None:
            for idx, task, c in units:
                lines: list[str] = []
                # one unit at a time per fixture root, across threads AND
                # other harness processes on this host (fixture_lock); a
                # wait is printed at once, not with the unit's buffered lines
                with fixture_lock(task, wait=True, log=say_now):
                    process(idx, task, log=lines.append, c=c)
                if lines:
                    with print_lock:
                        print("\n".join(lines))

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for fut in [pool.submit(run_group, g) for g in groups]:
                fut.result()

    # --- per-arm finalization --------------------------------------------------
    out_by_arm = {}
    for c in ctxs:
        st = states[c.name]
        results, counters, indexed = st["results"], st["counters"], st["indexed"]
        results_path = st["path"]
        # beast-assist latency + exposure (ship-rule clause: p95 per-write
        # <= 1s; exposure tells "never fired" from "no effect").
        try:
            lat, exposure = _summarize_diag_log(c.timing_log)
        except OSError as e:
            lat, exposure = None, None
            print(f"Diag latency summary unavailable: {e}")
        if lat:
            results["harness"]["diag_latency_ms"] = lat
            print(f"Diag latency{' [' + c.name + ']' if c.name else ''}: n={lat['n']} "
                  f"p50={lat['p50']:.0f}ms p95={lat['p95']:.0f}ms p99={lat['p99']:.0f}ms "
                  f"max={lat['max']:.0f}ms")
        if exposure is not None:
            results["harness"]["diag_exposure"] = exposure

        # How many units actually ran the model (benchmark_all skips its
        # thermal cool-off when this is 0, i.e. a full cache replay).
        results["summary"]["cache_hits"] = counters["cache_hits"]
        if c.name:
            results["summary"].update({"shared": counters["shared"],
                                       "not_in_arm": counters["not_in_arm"]})
        if counters["budget_skips"]:
            results["summary"]["budget_skips"] = counters["budget_skips"]
        # Rows recorded without invoking the agent (a disk-floor abort, a
        # failed recovery, a fixture setup failure) put no load on the GPU.
        no_agent = sum(1 for r in indexed.values()
                       if r.get("reason") in NO_AGENT_REASONS | {NOT_IN_ARM, BUDGET_SKIP}
                       or r.get("shared_from"))
        results["summary"]["live_units"] = (len(indexed) - counters["cache_hits"]
                                            - counters["cache_misses_skipped"] - no_agent)
        # The era is re-read per unit (cache._context_hash_cached validates on
        # stat), so a tree change mid-run keys later rows under the NEW era while
        # this file is stamped with the start era. Say so rather than hide it.
        era_now = cache.current_era()
        if era_now != results["harness"].get("era"):
            results["harness"]["era_moved_to"] = era_now
            print(f"ERA MOVED DURING THE RUN: {results['harness'].get('era')} -> "
                  f"{era_now}. Rows either side are not comparable; rerun this model.",
                  file=sys.stderr)
        _write_results(results_path, results)

        # Print summary
        s = results["summary"]
        print(f"\n{'=' * 60}")
        print(f"Results{' [' + c.name + ' ' + c.label() + ']' if c.name else ''}: "
              f"{s['passed']}/{s['total']} passed, {s['failed']} failed")
        cache_hits = counters["cache_hits"]
        if use_cache and cache_hits:
            print(f"Cache hits: {cache_hits}/{s['total']} ({100*cache_hits//max(1,s['total'])}% replay)")
        if c.name and (counters["shared"] or counters["not_in_arm"]):
            print(f"Shared from {base_ctx.name}: {counters['shared']}; "
                  f"not measured in this arm: {counters['not_in_arm']}")
        if counters["env_drift_replays"]:
            results["summary"]["env_drift_replays"] = counters["env_drift_replays"]
            _write_results(results_path, results)
            print(f"ENV DRIFT: {counters['env_drift_replays']} replayed row(s) were banked under a "
                  f"different (or unrecorded) weights/engine/toolchain environment than {env_component}. "
                  f"Set OPENBEAST_EVAL_ENV_ERA=1 to key the cache on it.")
        if cache_only and counters["cache_misses_skipped"]:
            print(f"Cache misses skipped: {counters['cache_misses_skipped']}/{s['total']} "
                  f"({100*counters['cache_misses_skipped']//max(1,s['total'])}%)")
        if counters["aborted_skips"]:
            print(f"Aborted: {counters['aborted_skips']} tasks skipped after server loss")
        if counters["budget_skips"]:
            print(f"Budget: {counters['budget_skips']} unit(s) never started (budget_skip, not scored)")
        import scoring  # local import, like cache — keeps import surface lazy
        if (suite_pin and not counters["aborted_skips"] and not counters["cache_misses_skipped"]
                and scoring.imputation_allowed(suite_version, c.name)):
            trip_failed = [t["id"] for t in results["tasks"]
                           if not t.get("passed") and t["id"] in set(suite_pin["tripwires"])]
            imputed = scoring.impute_suite_tasks(results["tasks"], suite_pin)
            solve, lang, cap = scoring.compute_solve_breadth(imputed)
            results["fast_suite"] = {
                "suite": suite,
                "capability_imputed": cap,
                "problem_solving_imputed": solve,
                "language_breadth_imputed": lang,
                "imputed_units": len(imputed) - len(results["tasks"]),
                "tripwire_failures": trip_failed,
            }
            _write_results(results_path, results)
            print(f"Imputed capability (v4-leaderboard scale): {cap:.2f} "
                  f"(solve {solve:.2f} / breadth {lang:.2f}; "
                  f"{results['fast_suite']['imputed_units']} saturated units imputed)")
            if trip_failed:
                print(f"TRIPWIRE FAILURES ({len(trip_failed)}): {', '.join(trip_failed)}")
                print("  This model breaks the suite's saturation assumption — the imputed "
                      "score above is NOT trustworthy. Run the full v4 suite.")
        elif suite_pin:
            print(f"No imputed score: imputation is retired for suite {suite_version}"
                  f"{' and for every arm' if c.name else ''} (measured units only)")
        print(f"Saved to: {results_path}")
        print(f"{'=' * 60}")
        out_by_arm[c.name] = results

    if not arms:
        return out_by_arm[None]
    return {"arms": out_by_arm, "pair_id": pair_id,
            "paths": {c.name: states[c.name]["path"] for c in ctxs}}


def main():
    # Before anything spawns: a SIGTERM or Ctrl-C must take our agents with us.
    # Without this, `job.sh stop` / a Stop from the phone / Ctrl-C killed this
    # process and left the agent running in its own session — holding a server
    # slot and writing the task's fixtures while the next launch re-ran it.
    _install_signal_reaper()
    parser = argparse.ArgumentParser(description="Run eval tasks against local LLM")
    parser.add_argument("--tasks", help="Comma-separated task IDs to run (default: the current "
                        "suite's manifest, evals/suites/<SUITE_VERSION>.json)")
    parser.add_argument("--all-tasks", action="store_true",
                        help="Run every task file in evals/tasks/ (the v4 rule), not the "
                             "current suite's manifest")
    parser.add_argument("--base-url", default="http://localhost:8080/v1", help="API base URL")
    parser.add_argument("--api-key-env",
                        help="Name of an env var holding the endpoint's bearer key (e.g. a "
                             "remote vLLM). Exported to the agent as OPENBEAST_API_KEY, and "
                             "--base-url is marked as this install's inference endpoint so "
                             "the runner sends it there.")
    parser.add_argument("--max-iter", type=int, help="Override max iterations for all tasks")
    parser.add_argument("--wall-scale", type=float, default=1.0,
                        help="Multiply every unit's agent wall (turn budgets from "
                             "--turns-per-wall-s still use the base wall), e.g. 2 for a "
                             "thinking arm kept at its twin's turns. On the results header.")
    parser.add_argument("--turns-per-wall-s", type=float,
                        help="Wall-scaled turn budget: each unit gets max(its max_iter, "
                             "wall_s / N) turns (e.g. 15 -> medium 40, hard 60, xl 100), "
                             "ignoring the manifest's max_iter_ceiling. In the cache key.")
    parser.add_argument("--model-name", help="Override model name (default: auto-detect from /v1/models)")
    parser.add_argument("--list", action="store_true", help="List available tasks and exit")
    parser.add_argument("--no-cache", action="store_true", help="Disable result cache (force live run)")
    parser.add_argument("--cache-only", action="store_true",
                        help="Replay cache only — skip live runs entirely. Cache misses recorded as 'skipped_cache_miss'.")
    parser.add_argument("--suite", help="Run a suite from evals/suites/: a v5 manifest (v5) or a "
                        "pinned v4 subset (v4-fast; imputation-scored, v4 only). Mutually "
                        "exclusive with --tasks.")
    parser.add_argument("--reasoning-budget",
                        help="--cache-only: the reasoning-budget era to replay (e.g. 20480; "
                             "-1 = uncapped). Default: the model's last live run. On a LIVE run "
                             "against a non-llama server (vLLM, TensorFold, remote): the "
                             "budget the server runs with, declared (0 = thinking off).")
    parser.add_argument("--jobs", type=int, default=1,
                        help="Parallel eval workers (default 1). Needs a server with -np >= N "
                             "(clamped to /props total_slots when readable; MTP configs are -np 1). "
                             "Variants of the same base task always run sequentially — they share "
                             "/tmp fixtures — so scores stay comparable to sequential runs.")
    parser.add_argument("--slots", type=int,
                        help="The server's concurrent-request limit when /props cannot say "
                             "(vLLM max_num_seqs, TensorFold): --jobs is clamped to it.")
    parser.add_argument("--arms",
                        help="Comma-separated arms to run in THIS process (e.g. L0,L1,L2; "
                             "defined by the suite manifest, else L0 bare / L1 packs / "
                             "L2 packs+diag+escalate). Results go to eval-<slug>-<arm>-<ts>.json "
                             "with harness.arm and a shared harness.pair_id.")
    parser.add_argument("--unit-wall-cap", type=float,
                        help="Ceiling (s, before the --jobs timeout scale) on any unit's agent "
                             "wall budget; tightens per-unit max_wall_s, never loosens it.")
    parser.add_argument("--deadline-h", type=float,
                        help="Units that would start live after this many hours from now are "
                             "recorded as budget_skip (never scored). evals/profile.py sets it.")
    parser.add_argument("--packs", action="store_true",
                        help="Tier-3 language awareness packs (docs/LANG_AWARENESS_PLAN.md §5): "
                             "inject agents/packs/<lang>.md into the agent system prompt for units "
                             "whose language has a pack (zig only today), plus the host pack "
                             "probed from this machine (agents/lang/host_pack.py) for EVERY unit. "
                             "Own pack1-<sha8> cache era on those units; leaderboard-ineligible. "
                             "Same as BEAST_PACKS=1.")
    parser.add_argument("--diag", action="store_true",
                        help="beast-assist push diagnostics for units in assist scope "
                             "(compiled languages + shell; OPENBEAST_DIAG_LANGS overrides). "
                             "Own diag2-<sha8> cache era on those units; leaderboard-ineligible. "
                             "Same as BEAST_ASSIST=1.")
    parser.add_argument("--escalate", action="store_true",
                        help="beast-lang escalation (docs/BEAST_LANG_PLAN.md §7 P4): when the "
                             "push-diagnostics checker reports an error with a toolchain-CONFIRMED "
                             "known cause, attach the one-line fix to the same tool result. Needs "
                             "--diag / BEAST_ASSIST=1. Own esc1-<sha8> cache era; leaderboard-ineligible. "
                             "Same as BEAST_ESCALATE=1.")
    parser.add_argument("--seed", type=int,
                        help="Sampling seed sent on every agent request (sets "
                             "OPENBEAST_AGENT_SEED). In the cache key (ag-seed<N>), so a "
                             "k-seed repeat (G12) is just --seed 1, --seed 2, --seed 3 runs.")
    parser.add_argument("--thinking", choices=("on", "off"),
                        help="Per-request thinking toggle for the agent (sets "
                             "OPENBEAST_AGENT_THINKING). In the cache key (ag-think-on/off).")
    parser.add_argument("--served-model",
                        help="The model id the server serves (sets OPENBEAST_INFERENCE_MODEL, "
                             "which the agent requests). A live run refuses to start unless "
                             "/v1/models lists it, and stops (infra) if it disappears mid-run.")
    parser.add_argument("--no-provision", action="store_true",
                        help="Do not install missing eval toolchains (scripts/eval-host.sh "
                             "setup) or re-exec under the eval venv; only report what is "
                             "missing. Same as OPENBEAST_EVAL_NO_PROVISION=1.")
    args = parser.parse_args()

    if args.jobs < 1:
        parser.error("--jobs must be >= 1")
    if not args.list and not args.cache_only:
        # The toolchains (evals/toolchains.lock) before anything is pinned:
        # setup when missing, the prefix first on PATH, then re-exec under the
        # eval venv's python (docs/SPARK_HYDRA.md F6).
        import eval_host  # noqa: PLC0415
        host_problems = eval_host.ensure_host(
            argv=[os.path.abspath(__file__)] + sys.argv[1:], no_provision=args.no_provision)
        if host_problems and not eval_host.no_provision_requested(args.no_provision):
            print("REFUSED: the eval host is not ready (scripts/eval-host.sh check):",
                  file=sys.stderr)
            for p in host_problems:
                print(f"  - {p}", file=sys.stderr)
            sys.exit(2)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()] if args.arms else None
    if arms and (args.packs or args.diag or args.escalate):
        parser.error("--arms defines each arm's treatment; drop --packs/--diag/--escalate")
    if args.packs:
        os.environ["BEAST_PACKS"] = "1"
    if args.diag:
        os.environ["BEAST_ASSIST"] = "1"
    if args.escalate:
        os.environ["BEAST_ESCALATE"] = "1"
    # Through the environment like the switches above: agent_switches()
    # reads it once for the key, the header and the agent's env.
    if args.seed is not None:
        os.environ["OPENBEAST_AGENT_SEED"] = str(args.seed)
    if args.thinking:
        os.environ["OPENBEAST_AGENT_THINKING"] = args.thinking
    if args.served_model:
        os.environ["OPENBEAST_INFERENCE_MODEL"] = args.served_model
    if args.api_key_env:
        key = os.environ.get(args.api_key_env, "")
        if not key:
            parser.error(f"--api-key-env {args.api_key_env}: that variable is empty")
        os.environ["OPENBEAST_API_KEY"] = key
        os.environ["OPENBEAST_AGENT_INFERENCE_URL"] = args.base_url

    if args.list:
        tasks = load_tasks()
        print(f"Available tasks ({len(tasks)}):\n")
        for t in tasks:
            print(f"  {t['id']}  [{t.get('difficulty', '?'):>6}]  {t.get('name', t['id'])}")
        return

    task_filter = args.tasks.split(",") if args.tasks else None
    if args.suite and args.tasks:
        print("--suite and --tasks are mutually exclusive.", file=sys.stderr)
        sys.exit(2)
    if args.cache_only and args.no_cache:
        print("--cache-only and --no-cache are mutually exclusive.", file=sys.stderr)
        sys.exit(2)
    suite = args.suite
    if not suite and not task_filter and not args.all_tasks:
        suite = default_suite()
        if suite:
            print(f"Suite {suite}: running the manifest (evals/suites/{suite}.json); "
                  f"--all-tasks runs every task file")
    results = run_eval(
        task_filter=task_filter,
        base_url=args.base_url,
        max_iter_override=args.max_iter,
        turns_per_wall_s=args.turns_per_wall_s,
        wall_scale=args.wall_scale,
        model_name=args.model_name,
        use_cache=not args.no_cache,
        cache_only=args.cache_only,
        jobs=args.jobs,
        suite=suite,
        reasoning_budget=args.reasoning_budget,
        arms=arms,
        slots=args.slots,
        unit_wall_cap=args.unit_wall_cap,
        deadline=(time.time() + args.deadline_h * 3600) if args.deadline_h else None,
    )

    # Exit with failure if any task failed
    runs = list((results or {}).get("arms", {}).values()) if arms else [results]
    if any(r and r["summary"]["failed"] > 0 for r in runs):
        sys.exit(1)


if __name__ == "__main__":
    main()
