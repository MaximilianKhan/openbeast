#!/usr/bin/env python3
"""beast-island — keep the rig's intelligence running when the internet dies.

Named for "islanding" in power systems: when the grid fails, a microgrid with
its own battery disconnects and keeps serving its local load. That is the
situation this is for — a power outage takes the router (and so the internet)
down while the rig rides its UPS. Everything OpenBeast does is local except
the web tools, so the rig stays useful as long as nothing on it WAITS on the
internet. This module decides when "the internet is gone" is true and tells
the parts that would otherwise wait.

Two related settings, deliberately separate:

  OFFLINE=true   (scripts/lib/conf.sh, 2026-09-15) — "this box has no route
                 and never will": install/update steps that cannot succeed
                 are refused up front. A property of the INSTALL.
  ISLAND         (this module) — "the route is gone right now": a RUNTIME
                 state, entered and left while the stack runs.
                   auto   (default) probe the internet; enter island mode
                          after ISLAND_GRACE seconds without a route, leave it
                          once the route has been back for two probes in a row
                   force  island mode from launch until the stack stops
                          (./start.sh -d --offline)
                   off    no watcher, never islanded

The state lives in ONE file, <run dir>/island.json, written atomically by the
watcher on every tick (a heartbeat) and read by every consumer:

  * the web tools (agents/mcp_server.py fetch / web_search, which the :3001
    tool server serves too) answer at once with a clear refusal instead of
    stalling on connect timeouts the model then retries;
  * start.sh, at launch, boots islanded: `compose up --pull never` and Open
    WebUI's OFFLINE_MODE (no Hugging Face downloads or update checks, which
    otherwise stall its startup with no network);
  * start.sh --status, healthcheck.sh and doctor.sh report it.

A consumer trusts the file only while (a) its heartbeat is fresh
(`stale_after`, written by the watcher and capped at MAX_STALE on read) and
(b) the watcher pid it names is alive and really is an island.py process. A
watcher that died, a stack that stopped without cleaning up, or a file
planted by hand therefore reads as "not islanded" — a stale file must never
refuse web tools on a box whose network is fine. The file is opened
non-blocking, no-follow, regular-file-only and size-capped: a FIFO or a
/dev/zero symlink in its place must not hang or bloat the tool server.

The watcher exits when its parent (the supervisor) does, so a SIGKILLed
supervisor cannot leave it heartbeating forever, and it removes its own file
on the way out. It is started and reaped by start.sh, not restarted: if it
dies, the file goes stale and the rig reads as online.

Probing is TCP connects to IP literals (validated with ipaddress, so no
hostname can bring DNS back in: a dead resolver would turn every probe into a
multi-second stall), in parallel, any success = online. Port 443, not 53: a
Pi-hole-style router that redirects port 53 to itself would answer with the
uplink dead.
With no default route a connect fails at once (ENETUNREACH); with a router
but no uplink it costs the per-probe timeout.

Stdlib only: mcp_server.py imports this, and mcp_server.py ships to clients.
On a client there is no watcher and no state file, so nothing is islanded.

CLI:
  island.py status [--json]         what the state file says (exit 0 online,
                                    3 islanded)
  island.py probe [--timeout S]     one connectivity probe (exit 0 / 1)
  island.py init  --mode M          launch decision: write the first state
                                    (exit 0 online, 3 islanded)
  island.py watch --mode M          the watcher loop (start.sh starts and reaps it)
  island.py clear                   remove the state file (stop.sh)
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODES = ("auto", "force", "off")
# 443 everywhere: a router that hijacks port 53 for its own resolver accepts
# a TCP connect to 8.8.8.8:53 with the uplink dead, which reads as "online".
DEFAULT_PROBES = "1.1.1.1:443 9.9.9.9:443 8.8.8.8:443"
DEFAULT_GRACE = 90       # seconds without a route before islanding
DEFAULT_INTERVAL = 15    # seconds between probes
DEFAULT_TIMEOUT = 2.0    # seconds per probe connect
REJOIN_OK = 2            # consecutive good probes before leaving island mode
MAX_GRACE = 7 * 86400    # a finite ceiling: `--grace inf` must not crash int()
MAX_STALE = 600          # a reader never trusts a heartbeat older than this
MAX_STATE_BYTES = 64 * 1024
REASON_MAX = 160

EXIT_ONLINE = 0
EXIT_ISLAND = 3


def _git_main_run_dir() -> str | None:
    """The MAIN checkout's .run when this file lives in a git worktree.

    OpenCode starts mcp_server.py from whatever checkout it was opened in; the
    stack writes its state in the checkout it runs from. A worktree asking its
    own empty .run would never see island mode (same trap as the GPU lease,
    evals/benchmark_all.py _lease_env)."""
    try:
        r = subprocess.run(["git", "-C", REPO_DIR, "rev-parse", "--git-common-dir"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    common = r.stdout.strip()
    if r.returncode != 0 or not common:
        return None
    common = os.path.abspath(os.path.join(REPO_DIR, common))
    if os.path.basename(common) != ".git":
        return None
    main = os.path.dirname(common)
    return None if os.path.samefile(main, REPO_DIR) else os.path.join(main, ".run")


_MAIN_RUN_DIR: list = []   # memo: [] = not computed yet, [None] / [path]


def run_dir() -> str:
    d = os.environ.get("OPENBEAST_RUN_DIR", "").strip()
    return d or os.path.join(REPO_DIR, ".run")


def state_path() -> str:
    return os.path.join(run_dir(), "island.json")


def _read_candidates() -> list[str]:
    """Where a READER looks: the explicit run dir alone when one is set (the
    stack always sets it), else this checkout's .run, then the main
    checkout's when this is a worktree."""
    if os.environ.get("OPENBEAST_RUN_DIR", "").strip():
        return [state_path()]
    if not _MAIN_RUN_DIR:
        _MAIN_RUN_DIR.append(_git_main_run_dir())
    out = [state_path()]
    if _MAIN_RUN_DIR[0]:
        out.append(os.path.join(_MAIN_RUN_DIR[0], "island.json"))
    return out


def _now() -> float:
    return time.time()


def _stamp(t: float | None = None) -> str:
    return datetime.fromtimestamp(t if t is not None else _now()).strftime("%Y-%m-%dT%H:%M:%S")


# ── probing ─────────────────────────────────────────────────────────────────

def parse_probes(spec: str | None, fallback: bool = True) -> list[tuple[str, int]]:
    """'1.1.1.1:443 [2606:4700::1111]:443' → [(host, port), ...].

    Hosts must be IP LITERALS (a hostname needs DNS, which is what dies first)
    and an IPv6 literal must be bracketed (a bare one is ambiguous: the last
    group would be read as the port). Bad entries are dropped; an empty
    result falls back to DEFAULT_PROBES unless fallback=False (doctor asks
    that way to flag an unusable setting)."""
    out: list[tuple[str, int]] = []
    for tok in (spec or "").replace(",", " ").split():
        if tok.startswith("["):
            host, sep, port = tok[1:].partition("]:")
        else:
            host, sep, port = tok.rpartition(":")
            if ":" in host:          # bare IPv6: refuse rather than guess
                continue
        if not sep or not port.isdigit() or not 0 < int(port) < 65536:
            continue
        try:
            ipaddress.ip_address(host)
        except ValueError:
            continue
        out.append((host, int(port)))
    if not out and fallback:
        return parse_probes(DEFAULT_PROBES, fallback=False)
    return out


def probe(targets: list[tuple[str, int]], timeout: float = DEFAULT_TIMEOUT) -> bool:
    """True when ANY target accepts a TCP connection within `timeout`.
    Parallel, and returns on the FIRST success: a slow target listed before a
    live one must not delay the answer."""
    ok = threading.Event()

    def one(host: str, port: int) -> None:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                ok.set()
        except OSError:
            pass

    threads = [threading.Thread(target=one, args=t, daemon=True) for t in targets]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout + 0.5
    while not ok.is_set() and time.monotonic() < deadline:
        if not any(t.is_alive() for t in threads):
            break
        ok.wait(0.05)
    return ok.is_set()


# ── the state file ──────────────────────────────────────────────────────────

def write_state(state: dict, path: str | None = None) -> None:
    """Atomic replace: a reader never sees half a file. 0600 like the rest of
    .run (the readers all run as this user)."""
    path = path or state_path()
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".island.", dir=d)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load(path: str) -> dict | None:
    """The file as a dict, or None. Never blocks and never reads unbounded:
    O_NONBLOCK + O_NOFOLLOW, regular files only, size-capped (a FIFO would
    hang a tool-server worker forever; /dev/zero would read until OOM)."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_STATE_BYTES:
            return None
        data = os.read(fd, MAX_STATE_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        obj = json.loads(data)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _watcher_alive(pid) -> bool:
    """The pid the file names is alive and is an island.py process. Where
    there is no /proc (macOS) liveness alone is the check."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass                      # alive, another user's: cmdline decides
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read(8192).split(b"\0")
        # An argv element that IS island.py — not one that merely contains
        # it (tests/test_island.py would pass a substring check).
        return any(os.path.basename(a) == b"island.py" for a in argv)
    except FileNotFoundError:
        return not os.path.isdir("/proc/self")
    except OSError:
        return False


def read_state(path: str | None = None) -> dict | None:
    """The state when it is TRUSTWORTHY, else None. None means "nobody is
    watching" — every consumer must treat it as online."""
    for p in ([path] if path else _read_candidates()):
        st = _load(p)
        if st is None:
            continue
        try:
            age = _now() - float(st.get("updated_at", 0))
            stale_after = min(float(st.get("stale_after", 0)), MAX_STALE)
        except (TypeError, ValueError):
            continue
        if stale_after <= 0 or age > stale_after or age < -60:
            continue
        if not _watcher_alive(st.get("pid")):
            continue
        return st
    return None


def islanded(path: str | None = None) -> bool:
    st = read_state(path)
    return bool(st and st.get("state") == "island")


# Only reasons this module writes are echoed to the model; anything else in
# the file (hand-edited, planted) is replaced, so the file is not a channel
# into every user's tool results.
_REASON_OK = re.compile(r"^(forced at launch \(start\.sh --offline / ISLAND=force\)"
                        r"|no internet route at launch"
                        r"|no internet route for \d{1,9}s)$")


def _safe_reason(st: dict) -> str:
    r = st.get("reason")
    return r if isinstance(r, str) and _REASON_OK.match(r) else "no internet route"


def web_refusal(tool: str) -> str | None:
    """The answer a web tool gives in island mode, or None to proceed.

    Worded for a model: it says what is gone, that retrying will not help,
    and that the rest is local — a bare "network error" gets retried in a
    loop. Profile-neutral: a guest caller has only the web tools."""
    st = read_state()
    if not st or st.get("state") != "island":
        return None
    since = st.get("since_iso")
    since = since if isinstance(since, str) and re.fullmatch(r"[0-9T:-]{1,25}", since) else "?"
    return (
        f"{tool} is unavailable: this rig is in ISLAND MODE (offline since {since}: "
        f"{_safe_reason(st)}). There is no internet connection, so web search and "
        "fetching URLs cannot work — do not retry. Everything else this rig "
        "provides is local and still works; answer from it and from your own "
        "knowledge, and tell the user when something would need the web."
    )


# ── the state machine ───────────────────────────────────────────────────────

class Watcher:
    """Hysteresis around the probe. `probe_fn`, `clock` (wall time, for the
    file) and `mono` (monotonic, for the grace) are injectable so the tests
    drive it without sockets or sleeps."""

    def __init__(self, mode: str, grace: float, interval: float,
                 probe_fn, clock=None, path: str | None = None,
                 log=lambda msg: print(msg, flush=True), timeout: float = DEFAULT_TIMEOUT,
                 mono=None):
        if mode not in ("auto", "force"):
            raise ValueError(f"watcher mode must be auto or force, not {mode!r}")
        self.mode = mode
        g = float(grace)
        self.grace = min(max(0.0, g), MAX_GRACE) if g == g else DEFAULT_GRACE
        self.interval = max(1.0, float(interval))
        self.timeout = max(0.1, float(timeout))
        self.probe_fn = probe_fn
        self.clock = clock or (lambda: _now())
        # The grace is measured on a monotonic clock: an NTP step backwards
        # mid-outage must not postpone islanding (or a step forward hasten it).
        self.mono = mono or (clock if clock else time.monotonic)
        self.path = path
        self.log = log
        self.state = "online"
        self.reason = ""
        self.since = self.clock()
        self.first_fail: float | None = None   # on self.mono
        self.ok_streak = 0
        self.net_up: bool | None = None
        self.launched_islanded = False
        self.stopping = threading.Event()

    def stale_after(self) -> float:
        # Four missed ticks, each a full interval plus a probe that ran to its
        # timeout, plus slack. Readers cap it at MAX_STALE.
        return min(4 * (self.interval + self.timeout + 0.5) + 30, MAX_STALE)

    def _enter(self, state: str, reason: str) -> None:
        if state != self.state:
            self.log(f"{_stamp(self.clock())} beast-island: "
                     + ("ISLAND MODE — " if state == "island" else "back ONLINE — ")
                     + reason)
        self.state, self.reason, self.since = state, reason, self.clock()

    def snapshot(self) -> dict:
        now = self.clock()
        return {
            "state": self.state,
            "mode": self.mode,
            "reason": self.reason,
            "since": self.since,
            "since_iso": _stamp(self.since),
            "net_up": self.net_up,
            "grace": self.grace,
            "interval": self.interval,
            "launched_islanded": self.launched_islanded,
            "updated_at": now,
            "updated_iso": _stamp(now),
            "stale_after": self.stale_after(),
            "pid": os.getpid(),
        }

    def save(self) -> dict:
        snap = self.snapshot()
        # Never after a stop was asked for: a tick that was mid-probe when
        # SIGTERM arrived would otherwise re-create the file stop.sh just
        # removed.
        if not self.stopping.is_set():
            write_state(snap, self.path)
        return snap

    def init(self) -> str:
        """The launch decision. Forced → island. Auto → one probe; no route
        right now means islanded from the start (no grace: at launch the
        cost of guessing wrong is a WebUI that stalls on downloads). The
        decision is returned even when the file cannot be written — start.sh
        acts on the exit code, and an unwritable .run must not turn "no
        route" into "online"."""
        if self.mode == "force":
            self.net_up = None
            self._enter("island", "forced at launch (start.sh --offline / ISLAND=force)")
        else:
            self.net_up = bool(self.probe_fn())
            if self.net_up:
                self.state, self.reason = "online", "internet reachable at launch"
            else:
                self._enter("island", "no internet route at launch")
                self.first_fail = self.mono()
        self.launched_islanded = self.state == "island"
        self.since = self.clock()
        try:
            self.save()
        except OSError as e:
            self.log(f"{_stamp()} beast-island: could not write state: {e}")
        return self.state

    def resume(self, prior: dict | None) -> None:
        """Pick up where `init` left off (it ran in another process). Only a
        file of THIS launch counts — init wrote it moments ago, so it is
        fresh; anything older is a previous run's and is ignored."""
        if not prior:
            return
        try:
            if self.clock() - float(prior.get("updated_at", 0)) > MAX_STALE:
                return
        except (TypeError, ValueError):
            return
        if prior.get("mode") not in (None, self.mode):
            return
        if prior.get("state") in ("online", "island"):
            self.state = prior["state"]
        self.reason = str(prior.get("reason") or "")
        try:
            self.since = float(prior.get("since", self.since))
        except (TypeError, ValueError):
            pass
        self.launched_islanded = bool(prior.get("launched_islanded"))
        net = prior.get("net_up")
        self.net_up = net if isinstance(net, bool) else None
        if self.state == "island" and self.net_up is False:
            self.first_fail = self.mono()

    def tick(self) -> dict:
        up = bool(self.probe_fn())
        first = self.net_up is None
        changed = up != self.net_up
        self.net_up = up
        now = self.clock()
        if self.mode == "force":
            if changed and up:
                self.log(f"{_stamp(now)} beast-island: the internet is reachable"
                         + ("" if first else " again") + " — "
                         "staying islanded because island mode was forced. "
                         "Rejoin with: ./stop.sh && ./start.sh -d")
            elif changed:
                self.log(f"{_stamp(now)} beast-island: the internet is unreachable (forced island mode)")
            return self.save()
        m = self.mono()
        if up:
            self.first_fail = None
            self.ok_streak += 1
            if self.state == "island" and self.ok_streak >= REJOIN_OK:
                note = "internet reachable again"
                if self.launched_islanded:
                    note += (" (Open WebUI was started in offline mode and stays that way "
                             "until the next ./stop.sh && ./start.sh -d)")
                self._enter("online", note)
        else:
            self.ok_streak = 0
            if self.first_fail is None:
                self.first_fail = m
                if self.state == "online":
                    self.log(f"{_stamp(now)} beast-island: internet probe failed — "
                             f"islanding in {int(self.grace)}s unless it comes back")
            if self.state == "online" and m - self.first_fail >= self.grace:
                self._enter("island", f"no internet route for {int(m - self.first_fail)}s")
        return self.save()

    def ensure_forced(self) -> None:
        """Forced means islanded, whatever init managed to write: a failed
        init (unwritable .run, bad args) must not leave --offline online."""
        if self.mode == "force" and self.state != "island":
            self._enter("island", "forced at launch (start.sh --offline / ISLAND=force)")
            self.launched_islanded = True

    def _orphaned(self, parent: int) -> bool:
        return parent > 1 and os.getppid() != parent

    def run(self, stop: threading.Event | None = None, parent: int | None = None) -> None:
        """Tick until stopped or orphaned, then remove our own file. Any
        exception in a tick is logged and the loop carries on: a watcher that
        dies mid-outage silently turns island mode off."""
        if stop is not None:
            self.stopping = stop
        self.ensure_forced()
        parent = parent or os.getppid()
        first = True
        while True:
            if not first and self.stopping.wait(self.interval):
                break
            if self._orphaned(parent):
                self.log(f"{_stamp()} beast-island: supervisor gone (parent {parent}) — exiting")
                break
            try:
                if first:
                    self.save()
                else:
                    self.tick()
            except Exception as e:   # noqa: BLE001 — keep watching, say why
                self.log(f"{_stamp()} beast-island: tick failed: {type(e).__name__}: {e}")
            first = False
        self.stopping.set()
        self._remove_own()

    def _remove_own(self) -> None:
        p = self.path or state_path()
        st = _load(p)
        if st and st.get("pid") == os.getpid():
            try:
                os.unlink(p)
            except OSError:
                pass


# ── CLI ─────────────────────────────────────────────────────────────────────

def _num(env: str, default: float) -> float:
    try:
        v = float(os.environ.get(env, "").strip() or default)
        return v if 0 <= v < float("inf") else default
    except ValueError:
        return default


def _watcher_from(args) -> Watcher:
    targets = parse_probes(args.probes)
    return Watcher(args.mode, args.grace, args.interval,
                   probe_fn=lambda: probe(targets, args.timeout), timeout=args.timeout)


def describe(st: dict | None) -> str:
    if not st:
        return "not watching (no live island watcher — stack down, or ISLAND=off)"
    if st.get("state") == "island":
        line = f"ISLAND MODE since {st.get('since_iso')} — {_safe_reason(st)}"
        if st.get("mode") == "force":
            line += " [forced]"
    else:
        try:
            g = int(float(st.get("grace", 0)))
        except (TypeError, ValueError, OverflowError):
            g = 0
        line = f"online (watching: islands after {g}s without a route)"
    net = st.get("net_up")
    if isinstance(net, bool):
        line += f"; internet {'reachable' if net else 'UNREACHABLE'} at last probe"
    return line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="island.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, mode=True):
        if mode:
            env_mode = os.environ.get("OPENBEAST_ISLAND")
            p.add_argument("--mode", choices=("auto", "force"),
                           default=env_mode if env_mode in ("auto", "force") else "auto")
        p.add_argument("--grace", type=float, default=_num("OPENBEAST_ISLAND_GRACE", DEFAULT_GRACE))
        p.add_argument("--interval", type=float, default=_num("OPENBEAST_ISLAND_INTERVAL", DEFAULT_INTERVAL))
        p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
        p.add_argument("--probes", default=os.environ.get("OPENBEAST_ISLAND_PROBES") or DEFAULT_PROBES)

    p = sub.add_parser("status"); p.add_argument("--json", action="store_true")
    p.add_argument("--net", action="store_true",
                   help="print only up / down / unknown (the last probe's answer)")
    p.add_argument("--webui-offline", action="store_true",
                   help="print true/false: should Open WebUI run in OFFLINE_MODE now")
    p = sub.add_parser("probe"); common(p, mode=False)
    p = sub.add_parser("init"); common(p)
    p = sub.add_parser("watch"); common(p)
    p.add_argument("--parent", type=int, default=0,
                   help="exit when this pid is no longer our parent (start.sh passes $$)")
    sub.add_parser("clear")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        st = read_state()
        if args.net:
            net = (st or {}).get("net_up")
            print("up" if net is True else "down" if net is False else "unknown")
        elif args.webui_offline:
            # Fresh state only: islanded now, or launched islanded (the
            # container was created that way; keep it until a restart).
            on = bool(st and (st.get("state") == "island" or st.get("launched_islanded")))
            print("true" if on else "false")
        else:
            print(json.dumps(st, indent=1, sort_keys=True) if args.json else describe(st))
        return EXIT_ISLAND if st and st.get("state") == "island" else EXIT_ONLINE
    if args.cmd == "probe":
        up = probe(parse_probes(args.probes), args.timeout)
        print("internet reachable" if up else "internet UNREACHABLE")
        return 0 if up else 1
    if args.cmd == "clear":
        try:
            os.unlink(state_path())
        except FileNotFoundError:
            pass
        return 0
    w = _watcher_from(args)
    if args.cmd == "init":
        return EXIT_ISLAND if w.init() == "island" else EXIT_ONLINE
    # watch
    w.resume(_load(state_path()))
    w.ensure_forced()
    print(f"{_stamp()} beast-island watcher up (mode {w.mode}, grace {int(w.grace)}s, "
          f"every {int(w.interval)}s, probes {args.probes}) — {describe(w.snapshot())}",
          flush=True)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: w.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: w.stopping.set())
    w.run(parent=args.parent or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
