"""vlib: the standard helper for eval validators (v5).

Validators `import vlib`. Nothing else should: the harness
(run_eval.run_validation) injects this module from its source into the
validator process. It is not on any import path and it is never a file in
the validator's cwd, so a solution cannot plant or replace it.

The threat model. The solution is written by the model under test, runs with
the same uid as the validator, and may try to pass without solving the task.
It may read its parent's argv, environment or cwd, plant modules where the
validator imports from, hide CPU time in orphaned children, or leave a daemon
running into the next unit. The harness already closes the channels it owns:
the validator's script arrives on stdin, not argv; it runs as
`python -I -S -` in a fresh empty cwd with a scrubbed environment; and on
Linux it is non-dumpable. This module covers what each validator has to do
itself:

  scale(), t(seconds)
      OPENBEAST_TIMEOUT_SCALE (the harness's contention multiplier, 2.0
      under --jobs 4). Every fixed limit in a validator is written at
      scale 1 and multiplied by it: `timeout=vlib.t(10)`.

  run_solution(cmd, input=..., cpu_s=..., wall_s=...)
      Run the solution in its OWN session and process group with a CPU
      budget that counts every descendant. The accounting is exact through a
      cgroup when the Linux host delegates one. Otherwise it combines
      RLIMIT_CPU per process, wait4 rusage, and a process-tree sampler that
      tracks the pgid and the ancestry, with a child subreaper on Linux.
      Orphaned or surviving descendants are refused as `daemon`. Every
      descendant is SIGKILLed on the way out, whatever happened.
      By default (allow_fork=False) any child process is refused; on macOS
      the kernel enforces that through sandbox-exec.

  python_cmd(path)
      The argv to run a Python solution with: `-I -S`, so neither its own
      dir's siblings nor site-packages (numpy, torch) resolve unless the
      validator puts them there on purpose.

  processes()
      The live process table (pid, ppid, pgid, cpu seconds). It reads /proc
      on Linux and falls back to `ps` elsewhere, so it also works on macOS.

  lint_python(src, ban_modules=..., ban_calls=...)
      An AST lint that handles the routes a substring or line regex misses:
      `import a, torch`, `from x import y`, `__import__`, importlib, exec,
      eval, compile, getattr with a computed or dunder name, dunder
      attribute walks, format-string field lookups such as "{0.__globals__}",
      and sys.modules or builtins access.

The rules every validator follows are listed in skills/eval-task-author/SKILL.md
under "Validator isolation".
"""

from __future__ import annotations

import ast
import os
import secrets
import signal
import subprocess
import sys
import threading
import time

__all__ = ["scale", "t", "run_solution", "Result", "python_cmd", "processes",
           "lint_python", "clean_env"]

_LINUX = sys.platform.startswith("linux")
_DARWIN = sys.platform == "darwin"


# --- scale ------------------------------------------------------------------

def scale() -> float:
    """The harness's timeout scale (OPENBEAST_TIMEOUT_SCALE), never below 1.0."""
    try:
        s = float(os.environ.get("OPENBEAST_TIMEOUT_SCALE", "") or 1.0)
    except ValueError:
        s = 1.0
    return max(1.0, s)


def t(seconds: float) -> float:
    """A limit written at scale 1, scaled for this run."""
    return float(seconds) * scale()


# --- environment ----------------------------------------------------------------

_ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TZ", "TMPDIR",
             "TERM", "SDKROOT", "DEVELOPER_DIR", "MACOSX_DEPLOYMENT_TARGET",
             "CC", "CXX", "GOROOT", "GOPATH", "GOCACHE", "GOMODCACHE", "GOFLAGS",
             "GOTOOLCHAIN", "GOPROXY", "GOSUMDB", "CARGO_HOME", "RUSTUP_HOME",
             "RUSTUP_TOOLCHAIN", "ZIG_GLOBAL_CACHE_DIR", "ZIG_LOCAL_CACHE_DIR",
             "XDG_CACHE_HOME", "PKG_CONFIG_PATH", "OPENBEAST_TIMEOUT_SCALE")


def clean_env(extra: dict | None = None, base: dict | None = None) -> dict:
    """A minimal environment: locale, PATH, HOME and the toolchain variables.
    Nothing from the validator's own environment beyond those keys."""
    src = os.environ if base is None else base
    env = {k: v for k, v in src.items()
           if k in _ENV_KEEP or k.startswith("LC_")}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


def python_cmd(path: str, *args: str) -> list[str]:
    """argv for a Python solution: isolated (-I) and without site-packages (-S)."""
    return [sys.executable, "-I", "-S", path, *args]


# --- process table (Linux /proc, else ps) ----------------------------------------

def _parse_cputime(s: str) -> float:
    """ps `time`: [DD-][HH:]MM:SS[.cc]."""
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d or 0)
    total = 0.0
    for part in s.split(":"):
        total = total * 60 + float(part or 0)
    return days * 86400 + total


def _proc_table() -> dict | None:
    try:
        tick = os.sysconf("SC_CLK_TCK")
        names = os.listdir("/proc")
    except (OSError, ValueError, AttributeError):
        return None
    out = {}
    for n in names:
        if not n.isdigit():
            continue
        try:
            with open(f"/proc/{n}/stat", "rb") as f:
                raw = f.read().decode("ascii", "replace")
        except OSError:
            continue
        rest = raw[raw.rfind(")") + 2:].split()
        try:
            if rest[0] == "Z":
                continue
            out[int(n)] = (int(rest[1]), int(rest[2]),
                           (int(rest[11]) + int(rest[12])) / tick)
        except (IndexError, ValueError):
            continue
    return out


def _ps_table() -> dict:
    try:
        r = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid=,stat=,time="],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for line in r.stdout.splitlines():
        p = line.split()
        if len(p) < 5 or p[3].startswith("Z"):
            continue
        try:
            out[int(p[0])] = (int(p[1]), int(p[2]), _parse_cputime(p[4]))
        except ValueError:
            continue
    return out


def processes() -> dict:
    """{pid: (ppid, pgid, cpu_seconds)} of every live, non-zombie process."""
    if _LINUX and os.path.isdir("/proc/self"):
        tab = _proc_table()
        if tab is not None:
            return tab
    return _ps_table()


# --- Linux helpers ---------------------------------------------------------------

_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36
_libc = None


def _prctl(opt: int, arg: int) -> bool:
    global _libc
    if not _LINUX:
        return False
    try:
        if _libc is None:
            import ctypes
            _libc = ctypes.CDLL(None, use_errno=True)
        return _libc.prctl(opt, arg, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


_SUBREAPER = None


def _ensure_subreaper() -> bool:
    """Orphans of the solution reparent to THIS process, not to init, so they
    stay visible and their rusage comes back through wait4."""
    global _SUBREAPER
    if _SUBREAPER is None:
        _SUBREAPER = _prctl(_PR_SET_CHILD_SUBREAPER, 1)
    return _SUBREAPER


class _Cgroup:
    """A child cgroup (v2) of our own, when the host delegates it."""

    def __init__(self):
        self.path = None
        if not _LINUX:
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
            p = os.path.join(base, f"vlib-{os.getpid()}-{secrets.token_hex(4)}")
            os.mkdir(p)
            self.path = p
        except (OSError, StopIteration):
            self.path = None

    def enter_child(self):  # runs in the child, before exec
        if self.path:
            try:
                with open(os.path.join(self.path, "cgroup.procs"), "w") as f:
                    f.write("0")
            except OSError:
                pass

    def pids(self) -> list[int]:
        if not self.path:
            return []
        try:
            with open(os.path.join(self.path, "cgroup.procs")) as f:
                return [int(x) for x in f.read().split()]
        except (OSError, ValueError):
            return []

    def cpu(self) -> float | None:
        if not self.path:
            return None
        try:
            with open(os.path.join(self.path, "cpu.stat")) as f:
                for ln in f:
                    k, _, v = ln.partition(" ")
                    if k == "usage_usec":
                        return int(v) / 1e6
        except (OSError, ValueError):
            return None
        return None

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
        for _ in range(50):
            try:
                os.rmdir(self.path)
                break
            except OSError:
                self.kill()
                time.sleep(0.02)
        self.path = None


# --- macOS sandbox ---------------------------------------------------------------

_SANDBOX_OK = None


def _sb_quote(p: str) -> str:
    return os.path.realpath(p).replace("\\", "\\\\").replace('"', '\\"')


def _sandbox_prefix(allow_fork: bool, deny_read: list[str],
                    allow_write: list[str] | None = None) -> list[str]:
    """sandbox-exec argv prefix (macOS), or [] when not needed or unavailable.

    allow_write: when given (a list, possibly empty), every file write is
    denied except under these paths and /dev. Graded code runs as our uid
    and can otherwise write anywhere we can, e.g. plant a module in the cwd
    of an ancestor found through libproc (176)."""
    global _SANDBOX_OK
    if not _DARWIN or (allow_fork and not deny_read and allow_write is None):
        return []
    rules = ["(version 1)", "(allow default)"]
    if not allow_fork:
        rules.append("(deny process-fork)")
    for p in deny_read:
        rules.append(f'(deny file-read* (subpath "{_sb_quote(p)}"))')
    if allow_write is not None:
        rules.append("(deny file-write*)")
        for p in ["/dev"] + list(allow_write):
            rules.append(f'(allow file-write* (subpath "{_sb_quote(p)}"))')
    if _SANDBOX_OK is None:
        try:
            _SANDBOX_OK = subprocess.run(
                ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
                capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _SANDBOX_OK = False
    if not _SANDBOX_OK:
        return []
    return ["/usr/bin/sandbox-exec", "-p", "".join(rules)]


def _sealed_paths() -> list[str]:
    raw = os.environ.get("OPENBEAST_VALIDATOR_SEAL", "")
    return [p for p in raw.split(os.pathsep) if p and os.path.exists(p)]


# --- run_solution ----------------------------------------------------------------

class Result:
    """What run_solution saw.

    verdict: "ok" (exited by itself within every budget; check returncode
    yourself), "cpu" (over the CPU budget), "wall" (over the wall budget),
    "daemon" (a descendant was orphaned or outlived the solution), "forked"
    (allow_fork=False and it started a child), "output" (wrote more than
    max_output bytes)."""

    def __init__(self, returncode, stdout, stderr, cpu_s, wall_s, verdict, reason):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.cpu_s = cpu_s
        self.wall_s = wall_s
        self.verdict = verdict
        self.reason = reason

    @property
    def ok(self) -> bool:
        """Within every budget AND exit status 0."""
        return self.verdict == "ok" and self.returncode == 0

    def __repr__(self):
        return (f"Result(verdict={self.verdict!r}, rc={self.returncode}, "
                f"cpu={self.cpu_s:.2f}s, wall={self.wall_s:.2f}s, reason={self.reason!r})")


def _reader(stream, buf: bytearray, cap: int, over: list):
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            room = cap - len(buf)
            if room > 0:
                buf.extend(chunk[:room])
            if len(chunk) > max(room, 0):
                over[0] = True
    except (OSError, ValueError):
        pass


def _writer(stream, data: bytes):
    try:
        stream.write(data)
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def run_solution(cmd, input=None, *, cpu_s: float, wall_s: float | None = None,
                 cwd: str | None = None, env: dict | None = None, text: bool = True,
                 allow_fork: bool = False, scale_cpu: bool = True,
                 max_output: int = 64 * 1024 * 1024, poll_s: float = 0.05,
                 deny_read: list[str] | None = None) -> Result:
    """Run the solution `cmd` (an argv list) under a CPU budget for the whole
    process tree.

    cpu_s, wall_s: budgets written at SCALE 1. They are multiplied by scale()
    here, so do not pre-scale them. wall_s defaults to 3*cpu_s + 5.
    scale_cpu=False keeps the CPU budget fixed, for a perf gate where CPU
    time is the measured quantity; the wall budget always scales.
    env: defaults to clean_env(); never pass the validator's os.environ.
    input: str or bytes for stdin (default: /dev/null).
    allow_fork: False (the default) refuses ANY child process: on macOS
    sandbox-exec makes fork() itself fail, and on Linux a sampled child gives
    verdict "forked". Pass True only when the task needs subprocesses (a
    shell solution, multiprocessing). On Linux the accounting is then still
    exact, because a child subreaper (or a cgroup) keeps every orphan in view.
    On macOS a double-fork plus setsid can still slip past the sampler, so a
    timed gate on macOS should keep allow_fork=False.
    deny_read: paths the solution may not read (macOS sandbox only; default:
    the harness's sealed eval paths, OPENBEAST_VALIDATOR_SEAL).
    """
    if isinstance(cmd, str):
        raise TypeError("run_solution takes an argv list, not a shell string")
    sc = scale()
    cpu_budget = float(cpu_s) * (sc if scale_cpu else 1.0)
    wall_budget = (float(wall_s) if wall_s is not None else 3 * float(cpu_s) + 5) * sc
    env = clean_env() if env is None else dict(env)
    seal = _sealed_paths() if deny_read is None else list(deny_read)
    argv = _sandbox_prefix(allow_fork, seal) + list(cmd)
    cg = _Cgroup()
    me = os.getpid()
    subreaper = _ensure_subreaper()
    rl = max(1, int(cpu_budget + 1.999))

    def _pre():
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (rl, rl + 2))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ImportError, OSError, ValueError):
            pass
        _prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
        cg.enter_child()

    data = None
    if input is not None:
        data = input.encode() if isinstance(input, str) else bytes(input)
    t0 = time.monotonic()
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
                            start_new_session=True, preexec_fn=_pre, close_fds=True)
    pgid = proc.pid
    _register_group(pgid)
    out, err, over = bytearray(), bytearray(), [False]
    threads = [threading.Thread(target=_reader, args=(proc.stdout, out, max_output, over), daemon=True),
               threading.Thread(target=_reader, args=(proc.stderr, err, max_output, over), daemon=True)]
    if data is not None:
        threads.append(threading.Thread(target=_writer, args=(proc.stdin, data), daemon=True))
    for th in threads:
        th.start()

    known: set[int] = {proc.pid}
    cpu_seen: dict[int, float] = {}
    verdict, reason = "ok", ""
    rusage_cpu = 0.0
    status = None

    def _tree():
        """Live members of the solution's tree: the pgid, the ancestry from
        known pids, and (Linux, subreaper) orphans reparented to us."""
        tab = processes()
        live = {}
        for pid, (ppid, pg, cpu) in tab.items():
            if pid == me:
                continue
            if pg == pgid or pid in known or ppid in known or (subreaper and ppid == me):
                live[pid] = (ppid, pg, cpu)
        grew = True
        while grew:  # descendants of descendants
            grew = False
            for pid, (ppid, pg, cpu) in tab.items():
                if pid not in live and pid != me and ppid in live:
                    live[pid] = (ppid, pg, cpu)
                    grew = True
        for pid in cg.pids():
            if pid not in live and pid in tab:
                live[pid] = tab[pid]
        return live

    def _sampled_total():
        return sum(cpu_seen.values())

    def _kill_all(live=None):
        cg.kill()
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
        for pid in set(known) | set(live or ()):
            if pid == me:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    try:
        while True:
            r = proc.poll()
            live = _tree()
            for pid, (ppid, _pg, cpu) in live.items():
                known.add(pid)
                cpu_seen[pid] = max(cpu_seen.get(pid, 0.0), cpu)
            if r is not None:
                status = r
                break
            others = [p for p in live if p != proc.pid]
            if not allow_fork and others:
                verdict, reason = "forked", f"solution started {len(others)} child process(es)"
                break
            orphans = [p for p, (pp, _g, _c) in live.items()
                       if p != proc.pid and (pp == 1 or (pp == me and subreaper)
                                             or (pp not in live and pp != me))]
            if orphans:
                verdict, reason = "daemon", f"solution orphaned {len(orphans)} process(es)"
                break
            used = max(cg.cpu() or 0.0, _sampled_total())
            if used > cpu_budget:
                verdict, reason = "cpu", f"CPU budget {cpu_budget:.1f}s exceeded ({used:.1f}s)"
                break
            if over[0]:
                verdict, reason = "output", f"output over {max_output} bytes"
                break
            if time.monotonic() - t0 > wall_budget:
                verdict, reason = "wall", f"wall budget {wall_budget:.1f}s exceeded"
                break
            time.sleep(poll_s)
        # The solution itself exited (or we are about to kill it): anything of
        # its tree still alive outlived it.
        if verdict == "ok":
            survivors = [p for p in _tree() if p != proc.pid]
            if survivors:
                verdict, reason = "daemon", f"{len(survivors)} process(es) outlived the solution"
    finally:
        _kill_all()
        try:
            _pid, wst, ru = os.wait4(proc.pid, 0)
            proc.returncode = (-os.WTERMSIG(wst) if os.WIFSIGNALED(wst)
                               else os.WEXITSTATUS(wst))
            rusage_cpu = ru.ru_utime + ru.ru_stime
        except ChildProcessError:
            rusage_cpu = 0.0
        if proc.returncode is None and status is not None:
            proc.returncode = status
        if subreaper:  # orphans reparented to us: reap them (they were SIGKILLed)
            pending = {p for p in known if p != proc.pid}
            deadline = time.monotonic() + 1.0
            while pending and time.monotonic() < deadline:
                for pid in list(pending):
                    try:
                        _p, _w, ru = os.wait4(pid, os.WNOHANG)
                    except (ChildProcessError, OSError):
                        pending.discard(pid)  # not our child
                        continue
                    if _p:
                        rusage_cpu += ru.ru_utime + ru.ru_stime
                        pending.discard(pid)
                if pending:
                    time.sleep(0.01)
        for th in threads:
            th.join(timeout=2)
        cg_cpu = cg.cpu()
        cg.remove()
        _unregister_group(pgid)
    wall = time.monotonic() - t0
    total = max(rusage_cpu, _sampled_total(), cg_cpu or 0.0)
    rc = proc.returncode
    if verdict == "ok":
        if total > cpu_budget or (rc is not None and rc < 0 and -rc in (signal.SIGXCPU,)):
            verdict, reason = "cpu", f"CPU budget {cpu_budget:.1f}s exceeded ({total:.1f}s)"
        elif over[0]:
            verdict, reason = "output", f"output over {max_output} bytes"
    o, e = bytes(out), bytes(err)
    if text:
        o, e = o.decode("utf-8", "replace"), e.decode("utf-8", "replace")
    return Result(rc, o, e, total, wall, verdict, reason)


# Process groups this validator started, recorded where the harness looks
# after the validator exits (it SIGKILLs them, so a validator that is itself
# killed on timeout cannot leave a solution running into the next unit).
_GROUP_FILE = None  # set by the harness bootstrap
_LIVE = set()


def _register_group(pgid: int):
    _LIVE.add(pgid)
    if _GROUP_FILE:
        try:
            with open(_GROUP_FILE, "a") as f:
                f.write(f"{pgid}\n")
        except OSError:
            pass


def _unregister_group(pgid: int):
    # Rewrite the record with only the groups still running, so the harness
    # never signals a pgid that has since been reused.
    _LIVE.discard(pgid)
    if _GROUP_FILE:
        try:
            with open(_GROUP_FILE, "w") as f:
                f.write("".join(f"{g}\n" for g in sorted(_LIVE)))
        except OSError:
            pass


# --- lint ------------------------------------------------------------------------

_DANGER_CALLS = {"__import__", "exec", "eval", "compile", "execfile", "globals",
                 "vars", "locals", "breakpoint"}
_DANGER_MODULES = {"importlib", "ctypes", "cffi", "gc", "inspect", "builtins",
                   "__builtin__", "imp", "pkgutil", "runpy", "zipimport", "marshal",
                   "pickle", "code", "codeop"}
_ATTR_CALLS = {"getattr", "setattr", "delattr", "hasattr"}
_INTERNALS = {"_getframe", "f_back", "f_globals", "f_locals", "f_builtins", "gi_frame",
              "cr_frame", "ag_frame", "tb_frame", "co_consts"}


def lint_python(src: str, ban_modules=(), ban_calls=(), *, allow_modules=(),
                strict: bool = True) -> list[str]:
    """AST lint for a Python solution. Returns a list of problems ([] = clean).

    ban_modules: top-level module names that must not be imported (e.g.
    "numpy", "torch", "re"). Every import form is caught: `import a, b`,
    `import a.b as c`, `from a import b`, `from a.b import *`, and string
    imports through __import__ or importlib.
    ban_calls: function or attribute names that must not appear as a call
    or attribute (e.g. "sorted", "divmod").
    strict: also refuse the generic escape hatches (exec, eval, compile,
    __import__, importlib, ctypes, gc, inspect, builtins, sys.modules,
    getattr with a non-literal or dunder name, dunder attribute access, and
    format strings that walk attributes). Set allow_modules for any of those
    modules the task legitimately needs.

    A lint is only a second line of defence. The first is that the solution
    runs in a separate process and only its OUTPUT is judged.
    """
    problems: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"syntax error: {e.msg} (line {e.lineno})"]
    banned = {m.split(".")[0] for m in ban_modules}
    danger = (_DANGER_MODULES - set(allow_modules)) if strict else set()
    bad_mods = banned | danger
    ban_calls = set(ban_calls)

    def mod_bad(name: str | None, line: int):
        if not name:
            return
        top = name.split(".")[0]
        if top in bad_mods:
            problems.append(f"line {line}: import of {name!r} is not allowed")

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for a in node.names:
                mod_bad(a.name, line)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                mod_bad(node.module, line)
            if node.module in ("sys", "os") and any(a.name in ("modules", "_getframe") for a in node.names):
                if strict:
                    problems.append(f"line {line}: from {node.module} import of internals")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
            if name in ban_calls:
                problems.append(f"line {line}: call to {name!r} is not allowed")
            if strict and isinstance(fn, ast.Name) and fn.id in _DANGER_CALLS:
                problems.append(f"line {line}: {fn.id}() is not allowed")
            if strict and name in ("import_module", "__import__", "find_spec", "spec_from_file_location"):
                problems.append(f"line {line}: dynamic import ({name}) is not allowed")
            if isinstance(fn, ast.Name) and fn.id in _ATTR_CALLS and len(node.args) >= 2:
                arg = node.args[1]
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    if strict:
                        problems.append(f"line {line}: {fn.id}() with a computed attribute name")
                else:
                    if strict and arg.value.startswith("__"):
                        problems.append(f"line {line}: {fn.id}() of dunder {arg.value!r}")
                    if arg.value in ban_calls:
                        problems.append(f"line {line}: {fn.id}() reaches banned {arg.value!r}")
            # str.format / format_map field walks: "{0.__globals__}".format(f)
            if (strict and isinstance(fn, ast.Attribute) and fn.attr in ("format", "format_map")
                    and isinstance(fn.value, ast.Constant) and isinstance(fn.value.value, str)
                    and _format_walks(fn.value.value)):
                problems.append(f"line {line}: format string walks attributes or items")
        elif isinstance(node, ast.Attribute):
            if node.attr in ban_calls:
                problems.append(f"line {line}: attribute {node.attr!r} is not allowed")
            if strict and node.attr.startswith("__") and node.attr.endswith("__") \
                    and node.attr not in ("__init__", "__name__", "__main__", "__len__",
                                          "__iter__", "__next__", "__eq__", "__lt__",
                                          "__hash__", "__repr__", "__str__", "__dict__",
                                          "__class__", "__slots__", "__doc__"):
                problems.append(f"line {line}: dunder attribute {node.attr!r}")
            if strict and node.attr in ("__dict__", "__class__") and isinstance(node.ctx, ast.Load) \
                    and isinstance(node.value, ast.Attribute):
                problems.append(f"line {line}: dunder walk via {node.attr!r}")
            if strict and (node.attr in _INTERNALS or (
                    node.attr == "modules" and isinstance(node.value, ast.Name)
                    and node.value.id == "sys")):
                problems.append(f"line {line}: access to interpreter internals ({node.attr})")
        elif isinstance(node, ast.Name):
            if node.id in ban_calls and isinstance(node.ctx, ast.Load):
                problems.append(f"line {line}: name {node.id!r} is not allowed")
            if strict and node.id in ("__builtins__", "__loader__", "__spec__"):
                problems.append(f"line {line}: name {node.id!r} is not allowed")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value
            for m in banned:
                if m and (low == m or low.startswith(m + ".")):
                    problems.append(f"line {line}: string names banned module {m!r}")
    # de-duplicate, keep order
    seen, uniq = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _format_walks(fmt: str) -> bool:
    """Whether a str.format template has a field that walks attributes or items."""
    import string
    try:
        for _lit, field, _spec, _conv in string.Formatter().parse(fmt):
            if field and ("." in field or "[" in field):
                return True
            if _spec and ("{" in _spec):
                return True
    except ValueError:
        return True
    return False
