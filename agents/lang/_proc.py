"""The one way beast-lang starts a process — reaped, capped and bounded.

Plain `subprocess.run(timeout=)` kills only the DIRECT child. Every toolchain
here is a driver that forks the real worker (gcc -> cc1plus, go -> compile,
zig -> its own jobs), so a timeout left the worker running as an orphan:
reproduced with OPENBEAST_LANG_COMPILE_TIMEOUT=0.15, where compile_source
returned "timed out" while `pgrep cc1plus` still showed it alive. This repo
once lost the whole machine to exactly that class (the 2026-07-07 OOM: two
orphans grew to ~140 GB each), and agents/tools.py::run_reaped is the house
answer. This is the same pattern, kept LOCAL on purpose: agents/lang must stay
importable without dragging the tool server's module in behind it.

Three guarantees, each of which the old call lacked:

  1. NO ORPHANS. The child gets its own session/process group and a timeout
     SIGKILLs the GROUP, then reaps.
  2. AN ADDRESS-SPACE CAP (RLIMIT_AS). Default 8 GiB, env-overridable with
     OPENBEAST_LANG_AS_LIMIT_MB (0 disables). MEASURED on this rig before
     picking the number, because the folklore is that Go cannot start under
     RLIMIT_AS at all: go 1.26 (`version`, `mod init`, `build`, `list std`,
     cold GOCACHE included), rustc and g++ all run under a 1 GiB cap; zig
     0.16 `build-exe -fno-emit-bin` reports `error: OutOfMemory` at 2 GiB and
     succeeds at 4 GiB. So nothing is exempted, and the default sits at 2x the
     smallest value zig was seen to pass under: a cap that is too tight does
     not crash, it makes a VALID snippet "fail to compile", and a false
     failure here becomes a false VERIFIED for an OLD fixture.
  3. BOUNDED OUTPUT. A reader thread keeps at most MAX_CAPTURE_BYTES and
     drains the rest, so a compiler in a template-error spiral cannot make
     the PARENT buffer gigabytes (and cannot deadlock on a full pipe).

stderr is folded into stdout: every caller here concatenated the two anyway.
"""
from __future__ import annotations

import os
import resource
import signal
import subprocess
import threading


def env_number(name: str, default, cast=float, minimum=None):
    """An env knob parsed WITHOUT the ability to take the process down.

    `float(os.environ.get(...))` at import time meant an empty or mistyped
    value (`OPENBEAST_LANG_COMPILE_TIMEOUT=`, `OPENBEAST_LANG_MAX_CARDS=two`)
    raised ValueError while the module was being imported — which, for a tool
    server that imports this package, is a crash at startup over a knob. A bad
    value is ignored in favour of the default.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        val = cast(raw.strip())
    except (TypeError, ValueError):
        return default
    if val != val:                                   # NaN compares unequal
        return default
    if minimum is not None and val < minimum:
        return default
    return val


#: Kept per stream-merged call. A diagnostic worth matching is in the first
#: few KB; 256 KB is already far more than any caller reads.
MAX_CAPTURE_BYTES = 256 * 1024
#: See the module docstring for where 8 GiB comes from. 0 = no cap.
AS_LIMIT_BYTES = env_number("OPENBEAST_LANG_AS_LIMIT_MB", 8192, int, 0) * 1024 * 1024


def _killpg(proc: subprocess.Popen) -> None:
    """SIGKILL the child's whole group, then reap the child."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)          # pgid == pid (new session)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run(argv: list[str], timeout: float, cwd: str | None = None,
        env: dict | None = None, stdin: str | None = None,
        as_limit: int | None = None) -> tuple[int, str]:
    """(returncode, merged output). Raises FileNotFoundError when the program
    is absent and subprocess.TimeoutExpired — AFTER the group is dead and
    reaped — on timeout. `env` replaces the child's environment; os.environ is
    never touched."""
    limit = AS_LIMIT_BYTES if as_limit is None else as_limit
    kw: dict = {}
    if limit and not hasattr(resource, "prlimit"):
        # prlimit(2) is Linux-only. preexec_fn is the fallback, with its
        # documented fork-safety caveat accepted only where there is no
        # alternative — same trade as tools.run_reaped.
        def _cap():
            try:
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            except (OSError, ValueError):
                pass
        kw["preexec_fn"] = _cap
    proc = subprocess.Popen(
        argv, cwd=cwd, env=env,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True, **kw)
    if limit and hasattr(resource, "prlimit"):
        # From the parent, post-spawn: safe in a threaded server, and the
        # driver's own children (cc1plus, compile, …) inherit it at fork.
        try:
            resource.prlimit(proc.pid, resource.RLIMIT_AS, (limit, limit))
        except (OSError, ValueError):
            pass

    kept = bytearray()
    total = [0]

    def _drain():
        while True:
            try:
                chunk = proc.stdout.read(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            total[0] += len(chunk)
            room = MAX_CAPTURE_BYTES - len(kept)
            if room > 0:
                kept.extend(chunk[:room])

    def _feed():
        try:
            proc.stdin.write(stdin.encode("utf-8"))
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    if stdin is not None:
        threading.Thread(target=_feed, daemon=True).start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _killpg(proc)
        reader.join(timeout=2)
        raise subprocess.TimeoutExpired(
            argv, timeout, output=kept.decode("utf-8", errors="replace")) from None
    finally:
        reader.join(timeout=5)
        if reader.is_alive():
            # The child exited but something it backgrounded still holds the
            # pipe. Nothing outlives the call: kill the group, which also
            # unblocks the reader.
            _killpg(proc)
            reader.join(timeout=2)
        if reader.is_alive():
            # It left the group (setsid). Force EOF on the raw fd.
            try:
                os.close(proc.stdout.fileno())
            except OSError:
                pass
            reader.join(timeout=2)
        try:
            proc.stdout.close()
        except Exception:                             # noqa: BLE001
            pass
    out = kept.decode("utf-8", errors="replace")
    if total[0] > len(kept):
        out += (f"\n[output truncated — {total[0]} bytes produced, "
                f"kept {len(kept)}]")
    return proc.returncode, out
