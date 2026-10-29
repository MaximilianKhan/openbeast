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
                          (start.sh --offline, the beastup-offline alias)
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

A consumer trusts the file only while its heartbeat is fresh (`stale_after`,
written into the file itself). A watcher that died, or a stack that stopped
without cleaning up, therefore reads as "not islanded" — a stale file must
never refuse web tools on a box whose network is fine.

Probing is TCP connects to IP literals (no DNS: a dead resolver would turn
every probe into a multi-second stall), in parallel, any success = online.
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
  island.py watch --mode M          the watcher loop (start.sh supervises it)
  island.py clear                   remove the state file (stop.sh)
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODES = ("auto", "force", "off")
DEFAULT_PROBES = "1.1.1.1:443 9.9.9.9:443 8.8.8.8:53"
DEFAULT_GRACE = 90       # seconds without a route before islanding
DEFAULT_INTERVAL = 15    # seconds between probes
DEFAULT_TIMEOUT = 2.0    # seconds per probe connect
REJOIN_OK = 2            # consecutive good probes before leaving island mode

EXIT_ONLINE = 0
EXIT_ISLAND = 3


def run_dir() -> str:
    d = os.environ.get("OPENBEAST_RUN_DIR", "").strip()
    return d or os.path.join(REPO_DIR, ".run")


def state_path() -> str:
    return os.path.join(run_dir(), "island.json")


def _now() -> float:
    return time.time()


def _stamp(t: float | None = None) -> str:
    return datetime.fromtimestamp(t if t is not None else _now()).strftime("%Y-%m-%dT%H:%M:%S")


# ── probing ─────────────────────────────────────────────────────────────────

def parse_probes(spec: str | None, fallback: bool = True) -> list[tuple[str, int]]:
    """'1.1.1.1:443 [2606:4700::1111]:443' → [(host, port), ...]. Bad
    entries are dropped; an empty result falls back to DEFAULT_PROBES unless
    fallback=False (doctor asks that way to flag an unusable setting)."""
    out: list[tuple[str, int]] = []
    for tok in (spec or "").replace(",", " ").split():
        host, sep, port = tok.rpartition(":")
        if not sep or not port.isdigit():
            continue
        host = host.strip("[]")
        if host and 0 < int(port) < 65536:
            out.append((host, int(port)))
    if not out and fallback:
        return parse_probes(DEFAULT_PROBES, fallback=False)
    return out


def probe(targets: list[tuple[str, int]], timeout: float = DEFAULT_TIMEOUT) -> bool:
    """True when ANY target accepts a TCP connection within `timeout`.
    Parallel, so the worst case is one timeout, not one per target."""
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
    deadline = _now() + timeout + 0.5
    for t in threads:
        t.join(max(0.0, deadline - _now()))
        if ok.is_set():
            return True
    return ok.is_set()


# ── the state file ──────────────────────────────────────────────────────────

def write_state(state: dict, path: str | None = None) -> None:
    """Atomic replace: a reader never sees half a file."""
    path = path or state_path()
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".island.", dir=d)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=1, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_state(path: str | None = None) -> dict | None:
    """The state file's contents when its heartbeat is FRESH, else None.
    None means "nobody is watching" — consumers must treat it as online."""
    path = path or state_path()
    try:
        with open(path) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(st, dict):
        return None
    try:
        age = _now() - float(st.get("updated_at", 0))
        stale_after = float(st.get("stale_after", 0))
    except (TypeError, ValueError):
        return None
    if stale_after <= 0 or age > stale_after or age < -60:
        return None
    return st


def islanded(path: str | None = None) -> bool:
    st = read_state(path)
    return bool(st and st.get("state") == "island")


def web_refusal(tool: str) -> str | None:
    """The answer a web tool gives in island mode, or None to proceed.

    Worded for a model: it says what is gone, that retrying will not help,
    and what still works — a bare "network error" gets retried in a loop."""
    st = read_state()
    if not st or st.get("state") != "island":
        return None
    since = st.get("since_iso") or "?"
    why = st.get("reason") or "no internet route"
    return (
        f"{tool} is unavailable: this rig is in ISLAND MODE (offline since {since}: "
        f"{why}). There is no internet connection, so web search and fetching "
        "URLs cannot work — do not retry. Everything local still works: files, "
        "bash, code, agents, skills and your own knowledge. Answer from those, "
        "and tell the user when something would need the web."
    )


# ── the state machine ───────────────────────────────────────────────────────

class Watcher:
    """Hysteresis around the probe. `probe_fn` and `clock` are injectable so
    the tests drive it without sockets or sleeps."""

    def __init__(self, mode: str, grace: float, interval: float,
                 probe_fn, clock=_now, path: str | None = None,
                 log=lambda msg: print(msg, flush=True)):
        if mode not in ("auto", "force"):
            raise ValueError(f"watcher mode must be auto or force, not {mode!r}")
        self.mode = mode
        self.grace = max(0.0, float(grace))
        self.interval = max(1.0, float(interval))
        self.probe_fn = probe_fn
        self.clock = clock
        self.path = path
        self.log = log
        self.state = "online"
        self.reason = ""
        self.since = clock()
        self.first_fail: float | None = None
        self.ok_streak = 0
        self.net_up: bool | None = None
        self.launched_islanded = False

    def stale_after(self) -> float:
        # Generous: a probe tick can take a timeout plus scheduling slack,
        # and a consumer reading "stale" means it stops refusing.
        return self.interval * 4 + 30

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
        write_state(snap, self.path)
        return snap

    def init(self) -> str:
        """The launch decision. Forced → island. Auto → one probe; no route
        right now means islanded from the start (no grace: at launch the
        cost of guessing wrong is a WebUI that stalls on downloads)."""
        if self.mode == "force":
            self.net_up = None
            self._enter("island", "forced at launch (start.sh --offline / ISLAND=force)")
            self.state = "island"
        else:
            self.net_up = bool(self.probe_fn())
            if self.net_up:
                self.state, self.reason = "online", "internet reachable at launch"
            else:
                self._enter("island", "no internet route at launch")
                self.first_fail = self.clock()
        self.launched_islanded = self.state == "island"
        self.since = self.clock()
        self.save()
        return self.state

    def resume(self, prior: dict | None) -> None:
        """Pick up where `init` left off (it ran in another process)."""
        if not prior:
            return
        if prior.get("state") in ("online", "island"):
            self.state = prior["state"]
        self.reason = str(prior.get("reason") or "")
        try:
            self.since = float(prior.get("since", self.since))
        except (TypeError, ValueError):
            pass
        self.launched_islanded = bool(prior.get("launched_islanded"))
        self.net_up = prior.get("net_up")
        if self.state == "island" and self.net_up is False:
            self.first_fail = self.since

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
                         "Rejoin with: beastdown && beastup")
            elif changed:
                self.log(f"{_stamp(now)} beast-island: the internet is unreachable (forced island mode)")
            return self.save()
        if up:
            self.first_fail = None
            self.ok_streak += 1
            if self.state == "island" and self.ok_streak >= REJOIN_OK:
                note = "internet reachable again"
                if self.launched_islanded:
                    note += (" (Open WebUI was started in offline mode and stays that way "
                             "until the next beastdown && beastup)")
                self._enter("online", note)
        else:
            self.ok_streak = 0
            if self.first_fail is None:
                self.first_fail = now
                if self.state == "online":
                    self.log(f"{_stamp(now)} beast-island: internet probe failed — "
                             f"islanding in {int(self.grace)}s unless it comes back")
            if self.state == "online" and now - self.first_fail >= self.grace:
                self._enter("island", f"no internet route for {int(now - self.first_fail)}s")
        return self.save()

    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        self.save()
        while not stop.wait(self.interval):
            try:
                self.tick()
            except OSError as e:   # a full disk must not kill the watcher
                self.log(f"{_stamp()} beast-island: could not write state: {e}")


# ── CLI ─────────────────────────────────────────────────────────────────────

def _num(env: str, default: float) -> float:
    try:
        v = float(os.environ.get(env, "").strip() or default)
        return v if v >= 0 else default
    except ValueError:
        return default


def _watcher_from(args) -> Watcher:
    targets = parse_probes(args.probes)
    return Watcher(args.mode, args.grace, args.interval,
                   probe_fn=lambda: probe(targets, args.timeout))


def describe(st: dict | None) -> str:
    if not st:
        return "not watching (no fresh island state — stack down, or ISLAND=off)"
    if st.get("state") == "island":
        line = f"ISLAND MODE since {st.get('since_iso')} — {st.get('reason')}"
        if st.get("mode") == "force":
            line += " [forced]"
    else:
        line = f"online (watching: islands after {int(st.get('grace', 0))}s without a route)"
    net = st.get("net_up")
    if net is not None:
        line += f"; internet {'reachable' if net else 'UNREACHABLE'} at last probe"
    return line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="island.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, mode=True):
        if mode:
            p.add_argument("--mode", choices=("auto", "force"),
                           default=os.environ.get("OPENBEAST_ISLAND", "auto")
                           if os.environ.get("OPENBEAST_ISLAND") in ("auto", "force") else "auto")
        p.add_argument("--grace", type=float, default=_num("OPENBEAST_ISLAND_GRACE", DEFAULT_GRACE))
        p.add_argument("--interval", type=float, default=_num("OPENBEAST_ISLAND_INTERVAL", DEFAULT_INTERVAL))
        p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
        p.add_argument("--probes", default=os.environ.get("OPENBEAST_ISLAND_PROBES") or DEFAULT_PROBES)

    p = sub.add_parser("status"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("probe"); common(p, mode=False)
    p = sub.add_parser("init"); common(p)
    p = sub.add_parser("watch"); common(p)
    sub.add_parser("clear")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        st = read_state()
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
    try:
        with open(state_path()) as f:
            w.resume(json.load(f))
    except (OSError, ValueError):
        pass
    print(f"{_stamp()} beast-island watcher up (mode {w.mode}, grace {int(w.grace)}s, "
          f"every {int(w.interval)}s, probes {args.probes}) — {describe(w.snapshot())}",
          flush=True)
    import signal
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    w.run(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
