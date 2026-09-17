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
  3. BOUNDED OUTPUT. At most MAX_CAPTURE_BYTES are kept and the rest is
     drained, so a compiler in a template-error spiral cannot make the PARENT
     buffer gigabytes (and cannot deadlock on a full pipe).
  4. A SCRUBBED ENVIRONMENT, for callers that ask (scrubbed_env): the child
     sees where its toolchain lives and nothing else, so no diagnostic can
     carry an API key out of os.environ.

stderr is folded into stdout: every caller here concatenated the two anyway.
"""
from __future__ import annotations

import os
import resource
import selectors
import signal
import subprocess
import time


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


#: What a child may inherit. A compiler can be made to PRINT its environment
#: (rust's env!(), a C `#error` built from a macro the driver defines from
#: one…), and Result.detail is attached to a model's next turn, so the child
#: gets an allow list, not os.environ. Names, then prefixes. Everything here
#: is "where is the toolchain / where may it cache", which is what each one
#: was MEASURED to need on this rig (zig: HOME or XDG_CACHE_HOME for its
#: global cache; go: HOME/GOPATH/GOCACHE; rustc and gcc: PATH alone) plus the
#: locator variables of the ways toolchains get installed elsewhere (rustup,
#: mise/asdf shims, nix wrappers, macOS SDKs, a non-system gcc).
_ENV_NAMES = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "TMPDIR", "TMP",
    "TEMP", "TERM", "NO_COLOR", "SOURCE_DATE_EPOCH",
    "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
    "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH",
    "GCC_EXEC_PREFIX", "COMPILER_PATH", "SDKROOT", "DEVELOPER_DIR",
    "MACOSX_DEPLOYMENT_TARGET", "CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN",
    "SYSTEMROOT",
})
_ENV_PREFIXES = ("LC_", "XDG_", "ZIG_", "MISE_", "ASDF_", "NIX_")
#: Go reads its configuration from GO* variables; only the go driver asks for
#: them (the operator's GOPATH/GOCACHE/GOROOT are locations, not secrets —
#: GOFLAGS/GOPROXY/GOTOOLCHAIN are then overridden by the driver anyway).
GO_ENV_PREFIXES = ("GO", "CGO_")


def scrubbed_env(extra: dict | None = None, prefixes: tuple = ()) -> dict:
    """A fresh dict for `env=`: the allow list above, then `extra` on top."""
    keep = _ENV_PREFIXES + tuple(prefixes)
    env = {k: v for k, v in os.environ.items()
           if k in _ENV_NAMES or k.startswith(keep)}
    env.update(extra or {})
    return env


def run(argv: list[str], timeout: float, cwd: str | None = None,
        env: dict | None = None, stdin: str | None = None,
        as_limit: int | None = None) -> tuple[int | None, str]:
    """(returncode, merged output).

    A program that could not be STARTED (absent, not executable) is
    `(None, why)` — a result, not an exception, so no caller can forget the
    case. A timeout raises subprocess.TimeoutExpired, AFTER the group is dead
    and reaped, carrying the partial output. `env` replaces the child's
    environment; os.environ is never touched.

    NO READER THREAD, and that is the point of how this is written. The first
    version drained the pipe from a thread blocked in a buffered read(), and
    "unblocked" it on the escape path by closing the fd underneath it. Closing
    an fd does not wake a blocked read(); the later stdout.close() then waited
    on the reader's buffer lock until the ESCAPED grandchild exited (measured:
    a 1 s timeout returning after 41 s), and the fd was closed twice — in a
    threaded server the second close lands on whatever file another thread
    opened in between (its write failed with EBADF). So: we own both pipe
    ends' parent side as raw fds, they are NON-BLOCKING, one loop polls them
    against the deadline, and each is closed exactly once, here, by the only
    code that ever touches it. A grandchild that setsid()s away and keeps the
    pipe open is simply ABANDONED: nothing waits for it.
    """
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

    out_r, out_w = os.pipe()                 # non-inheritable (PEP 446)
    in_r = in_w = None
    if stdin is not None:
        in_r, in_w = os.pipe()
    try:
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, env=env,
                stdin=in_r if in_r is not None else subprocess.DEVNULL,
                stdout=out_w, stderr=subprocess.STDOUT,
                start_new_session=True, **kw)
        finally:
            os.close(out_w)                  # the child has its own copy now
            if in_r is not None:
                os.close(in_r)
    except OSError as e:
        os.close(out_r)
        if in_w is not None:
            os.close(in_w)
        why = ("not installed" if isinstance(e, FileNotFoundError)
               else f"could not be started ({e.strerror or e})")
        return None, f"{argv[0]}: {why}"
    if limit and hasattr(resource, "prlimit"):
        # From the parent, post-spawn: safe in a threaded server, and the
        # driver's own children (cc1plus, compile, …) inherit it at fork.
        try:
            resource.prlimit(proc.pid, resource.RLIMIT_AS, (limit, limit))
        except (OSError, ValueError):
            pass

    kept = bytearray()
    total = 0
    pending = memoryview((stdin or "").encode("utf-8"))
    deadline = time.monotonic() + timeout
    sel = selectors.DefaultSelector()
    os.set_blocking(out_r, False)
    sel.register(out_r, selectors.EVENT_READ)
    if in_w is not None:
        os.set_blocking(in_w, False)
        sel.register(in_w, selectors.EVENT_WRITE)

    def _close_stdin():
        nonlocal in_w
        if in_w is not None:
            sel.unregister(in_w)
            os.close(in_w)
            in_w = None

    def _pump(wait: float) -> bool:
        """One poll. False once the output pipe has reached EOF."""
        nonlocal total, pending
        for key, _ in sel.select(max(0.0, wait)):
            if key.fd == out_r:
                try:
                    chunk = os.read(out_r, 65536)
                except BlockingIOError:
                    continue
                except OSError:
                    return False
                if not chunk:
                    return False
                total += len(chunk)
                room = MAX_CAPTURE_BYTES - len(kept)
                if room > 0:
                    kept.extend(chunk[:room])
            else:
                try:
                    pending = pending[os.write(in_w, pending[:65536]):]
                except BlockingIOError:
                    continue
                except OSError:              # EPIPE: the child stopped reading
                    pending = pending[:0]
                if not pending:
                    _close_stdin()
        return True

    def _drain_briefly() -> None:
        """What a just-killed group had already written — and no longer."""
        grace = time.monotonic() + 0.2
        while time.monotonic() < grace and _pump(0.05):
            pass

    def _text() -> str:
        return kept.decode("utf-8", errors="replace")

    timed_out = False
    try:
        if in_w is not None and not pending:
            _close_stdin()
        open_ = True
        while open_:
            left = deadline - time.monotonic()
            if left <= 0:
                timed_out = True
                break
            open_ = _pump(min(left, 0.2))
            if open_ and proc.poll() is not None:
                # The child is gone but the pipe is not at EOF: something it
                # started still holds it. Nothing outlives the call — kill the
                # group, take what is already in the pipe, and stop. Whatever
                # left the group (setsid) keeps its end; we do not wait for it.
                _killpg(proc)
                _drain_briefly()
                break
        if not timed_out:
            try:
                # EOF with the child still running (it closed stdout and went
                # on working) is not an exit: keep the deadline.
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
        if timed_out:
            _killpg(proc)
            _drain_briefly()
            raise subprocess.TimeoutExpired(argv, timeout, output=_text())
    finally:
        if proc.poll() is None:              # an exception on the way out
            _killpg(proc)
        sel.close()
        os.close(out_r)                      # exactly once, and only here
        if in_w is not None:
            os.close(in_w)
    out = _text()
    if total > len(kept):
        out += (f"\n[output truncated — {total} bytes produced, "
                f"kept {len(kept)}]")
    return proc.returncode, out
