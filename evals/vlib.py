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
      the kernel enforces that through sandbox-exec, which also denies the
      solution network, signals to and process-info on other processes, confines a
      compiled unit's exec to its own binary (no exec into python3), and
      denies reads of any evals/{refs,tasks,suites} tree.

  private_build_dir(files), build_env(), tool_path(name),
  scan_toolchain_overrides(dir), is_native_executable(path), sanitizer_flags(k)
      Build a solution from a PRIVATE copy of only its source, with a clean
      private HOME and the toolchain pinned in the environment, so a planted
      rust-toolchain.toml / go env / .cargo config cannot capture the compiler,
      then require the product to be a native executable.

  run_build(argv, cwd=build_dir, env=build_env(), timeout=t(20)),
  lint_source_includes(src, lang)
      Compile under the same read-deny as a solution (macOS sandbox-exec),
      and refuse source that names a file outside its build dir (#include of
      an absolute or `..` path, include!, .incbin, @embedFile): the compiler
      runs with the validator's rights and would read the sealed reference.

  run_solution(..., confine=[workdir], read_only=python_runtime(exe, mods))
      The solution may read only its runtime and its work dir, write only
      its work dir, and has no network, on macOS (sandbox-exec) and Linux
      (landlock plus a seccomp socket filter, else user + mount + net
      namespace). Fails closed: verdict
      "sandbox" when confinement is unavailable. Use it whenever the
      solution could otherwise read the oracle (the repo, its .git, other
      worktrees or clones) or site-packages.

  python_cmd(path)
      The argv to run a Python solution with: `-I -S`, so neither its own
      dir's siblings nor site-packages (numpy, torch) resolve unless the
      validator puts them there on purpose.

  processes()
      The live process table (pid, ppid, pgid, cpu seconds). It reads /proc
      on Linux and libproc on macOS (in-process, no fork; our uid's
      processes), and falls back to `ps` elsewhere.

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
import re
import secrets
import signal
import subprocess
import sys
import threading
import time

__all__ = ["scale", "t", "run_solution", "Result", "python_cmd", "python_runtime",
           "processes", "lint_python", "clean_env", "tool_path", "scan_toolchain_overrides",
           "private_build_dir", "build_env", "is_native_executable",
           "assert_native_executable", "sanitizer_flags", "SANITIZER_FLAGS",
           "run_build", "lint_source_includes", "read_source", "build_private"]

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
             "XDG_CACHE_HOME", "PKG_CONFIG_PATH", "OPENBEAST_TIMEOUT_SCALE",
             "CARGO_TARGET_DIR", "OPENBEAST_BUILD_CACHE", "CCACHE_DISABLE",
             "SCCACHE_DISABLE")


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


# macOS: libproc in-process. `ps -A` forks, costs ~25 ms wall and ~20 ms CPU
# per table on a desktop (1100 processes), and the sampler called it every
# 50 ms for the whole run, so a 1 s solution burned ~0.4 s of harness CPU
# and 20 forks/s of pid churn (review 1116). proc_listallpids +
# proc_pidinfo gives the same table in ~1.5 ms with no fork.
_LIBPROC = None
_PROC_PIDTBSDINFO, _PROC_PIDTASKINFO, _SZOMB = 3, 4, 5


def _libproc():
    """(lib, BsdInfo, TaskInfo, ticks->seconds) or False when unusable."""
    global _LIBPROC
    if _LIBPROC is None:
        _LIBPROC = False
        if _DARWIN:
            try:
                import ctypes
                lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
                u32, u64 = ctypes.c_uint32, ctypes.c_uint64

                class BsdInfo(ctypes.Structure):
                    _fields_ = [("flags", u32), ("status", u32), ("xstatus", u32),
                                ("pid", u32), ("ppid", u32), ("uid", u32), ("gid", u32),
                                ("ruid", u32), ("rgid", u32), ("svuid", u32), ("svgid", u32),
                                ("rfu_1", u32), ("comm", ctypes.c_char * 16),
                                ("name", ctypes.c_char * 32), ("nfiles", u32), ("pgid", u32),
                                ("pjobc", u32), ("e_tdev", u32), ("e_tpgid", u32),
                                ("nice", ctypes.c_int32), ("start_tvsec", u64),
                                ("start_tvusec", u64)]

                class TaskInfo(ctypes.Structure):
                    _fields_ = [("virtual_size", u64), ("resident_size", u64),
                                ("total_user", u64), ("total_system", u64),
                                ("threads_user", u64), ("threads_system", u64)] + \
                               [(n, ctypes.c_int32) for n in (
                                   "policy", "faults", "pageins", "cow_faults", "msgs_sent",
                                   "msgs_received", "syscalls_mach", "syscalls_unix", "csw",
                                   "threadnum", "numrunning", "priority")]

                class Timebase(ctypes.Structure):
                    _fields_ = [("numer", u32), ("denom", u32)]
                tb = Timebase()
                if ctypes.CDLL(None).mach_timebase_info(ctypes.byref(tb)) != 0 or not tb.denom:
                    return _LIBPROC
                lib.proc_listallpids.restype = ctypes.c_int
                lib.proc_pidinfo.restype = ctypes.c_int
                lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                             ctypes.c_void_p, ctypes.c_int]
                # pti_total_* are mach absolute-time units (ns on Intel,
                # 125/3 ns on Apple silicon)
                _LIBPROC = (lib, BsdInfo, TaskInfo, tb.numer / tb.denom / 1e9)
            except (OSError, AttributeError):
                _LIBPROC = False
    return _LIBPROC


def darwin_table(cpu: bool = True) -> dict | None:
    """macOS: {pid: (ppid, pgid, uid, cpu_seconds, start)} of every live,
    non-zombie process this uid may inspect (our own uid's; root's and
    other users' are not listed, unlike `ps`), through libproc. `start` is (tv_sec, tv_usec), an
    exact identity across pid reuse. cpu_seconds is 0.0 for a process we
    may not inspect (another uid), or with cpu=False. None when libproc is
    unusable (the caller falls back to ps)."""
    lp = _libproc()
    if not lp:
        return None
    import ctypes
    lib, BsdInfo, TaskInfo, tick_s = lp
    n = lib.proc_listallpids(None, 0)
    if n <= 0:
        return None
    for _ in range(4):
        cap = n + 256
        buf = (ctypes.c_int * cap)()
        n = lib.proc_listallpids(buf, ctypes.sizeof(buf))
        if n <= 0:
            return None
        if n < cap:
            break
    bi, ti = BsdInfo(), TaskInfo()
    bsz, tsz = ctypes.sizeof(bi), ctypes.sizeof(ti)
    out = {}
    for i in range(min(n, cap)):
        pid = buf[i]
        if pid <= 0:
            continue
        if lib.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(bi), bsz) != bsz:
            continue                       # gone, or not inspectable at all
        if bi.status == _SZOMB:
            continue
        c = 0.0
        if cpu and lib.proc_pidinfo(pid, _PROC_PIDTASKINFO, 0, ctypes.byref(ti), tsz) == tsz:
            c = (ti.total_user + ti.total_system) * tick_s
        out[pid] = (bi.ppid, bi.pgid, bi.uid, c, (bi.start_tvsec, bi.start_tvusec))
    return out


def processes() -> dict:
    """{pid: (ppid, pgid, cpu_seconds)} of every live, non-zombie process
    (on macOS through libproc: those of our uid, which is all a solution
    can start)."""
    if _LINUX and os.path.isdir("/proc/self"):
        tab = _proc_table()
        if tab is not None:
            return tab
    if _DARWIN:
        tab = darwin_table()
        if tab is not None:
            return {pid: (row[0], row[1], row[3]) for pid, row in tab.items()}
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

# Any path that lies inside an evals/{refs,tasks,suites} directory, in ANY
# checkout — not only this run's sealed paths. A solution that globs
# /Users/*/.../worktrees/*/evals/refs/<unit>/*.py to read the reference
# (reported in U4) is denied by construction, wherever the copy lives.
_EVAL_SECRET_RE = r"/evals/(refs|tasks|suites)(/|$)"


# kill(2) of any process outside the solution's own sandbox. (allow default)
# otherwise lets the solution SIGKILL/SIGSTOP every process of our uid: its
# validator, sibling units under --jobs, the harness itself, kill(-1) the
# user's whole session (review 1116). Itself, its own children and its own
# group stay signalable (they share the sandbox). Linux gets the same from
# landlock's scoped signals (ABI >= 6).
_SIGNAL_RULE = "(deny signal (target others))"


def _sbpl_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _sandbox_prefix(allow_fork: bool, deny_read: list[str], *,
                    deny_net: bool = True, deny_procinfo: bool = True,
                    exec_only: str | None = None,
                    allow_write: list[str] | None = None,
                    always: bool = False) -> list[str]:
    """sandbox-exec argv prefix (macOS), or [] when sandboxing is unavailable.

    The confined solution gets `(allow default)` minus the holes a cheat uses:
      * signals  — to any process outside its own sandbox (_SIGNAL_RULE);
      * network  — no fetch of a pushed reference, no phone-home (future risk
        reported on 164);
      * process-info  — proc_pidinfo/proc_pidpath on other processes (their
        executable path, cwd, open files). NOT their argv and environment:
        sandbox-exec does not mediate sysctl kern.procargs2 for a same-uid
        process (measured on Darwin 25: readable under (deny process-info*),
        under (deny sysctl-read) with a name allowlist, and under a name deny
        of kern.procargs2). So a validator must keep every secret out of its
        own argv and env (run_eval feeds it on stdin with a scrubbed env), and
        the seed in memory; tests/test_eval_190_confinement.py pins that;
      * process-exec  — when exec_only is set (a compiled unit passes its built
        binary), the solution may exec only that one image, so a C/Go/Rust/zig
        program cannot `execvp("python3", ...)` into an unlinted interpreter
        (reported on 166 and U4); a native binary does not re-exec itself, so
        this does not disturb an honest solution;
      * reads of any evals/{refs,tasks,suites} tree, plus the caller's
        deny_read paths.
    Later SBPL rules win, so the allow for exec_only follows the deny.

    allow_write: when given (a list, possibly empty), every file write is
    denied except under these paths and /dev. Graded code runs as our uid
    and can otherwise write anywhere we can, e.g. plant a module in the cwd
    of an ancestor found through libproc (176)."""
    global _SANDBOX_OK
    want = (always or (not allow_fork) or deny_read or deny_net or deny_procinfo or exec_only
            or allow_write is not None)
    if not _DARWIN or not want:
        return []
    rules = ["(version 1)", "(allow default)", _SIGNAL_RULE]
    if deny_net:
        rules.append("(deny network*)")
    if deny_procinfo:
        # Deny reading OTHER processes' info (the validator's argv/env,
        # reported on 190) while allowing a process to query ITSELF, which a
        # framework Python needs when it re-execs into its app bundle.
        rules.append("(deny process-info* (with no-report))")
        rules.append("(allow process-info-pidinfo (target self))")
        rules.append("(allow process-info-codesignature (target self))")
    if not allow_fork:
        rules.append("(deny process-fork)")
    rules.append(f'(deny file-read* (regex #"{_EVAL_SECRET_RE}"))')
    for p in deny_read:
        rules.append(f'(deny file-read* (subpath "{_sbpl_str(os.path.realpath(p))}"))')
    if exec_only:
        rules.append("(deny process-exec*)")
        for img in _exec_images(exec_only):
            rules.append(f'(allow process-exec (literal "{_sbpl_str(img)}"))')
    if allow_write is not None:
        rules.append("(deny file-write*)")
        for p in ["/dev"] + list(allow_write):
            rules.append(f'(allow file-write* (subpath "{_sbpl_str(os.path.realpath(p))}"))')
    if not _sandbox_ok():
        return []          # run_solution refuses the run (verdict "sandbox")
    return ["/usr/bin/sandbox-exec", "-p", "".join(rules)]


def _sealed_paths() -> list[str]:
    raw = os.environ.get("OPENBEAST_VALIDATOR_SEAL", "")
    return [p for p in raw.split(os.pathsep) if p and os.path.exists(p)]


# --- confinement (read allowlist, both platforms) ----------------------------
#
# A lint cannot close off Python introspection, and a deny-list (the seal)
# misses .git, other worktrees, other clones and site-packages. `confine`
# turns it around: the solution may READ only the interpreter's runtime
# (stdlib minus site-packages, the shared libraries it maps), the system
# library dirs, a few /dev nodes and the dirs the validator names; it may
# WRITE only the confine dirs; it gets no network. macOS: sandbox-exec with
# a deny-by-default file-read profile. Linux: landlock (kernel >= 5.13),
# else an unprivileged user + mount namespace with a fresh root that holds
# only the allowed paths (bind mounts, read-only). When neither works the
# run is refused (verdict "sandbox"): it fails closed, never unconfined.

_PKG_DIRS = ("site-packages", "dist-packages")
_RUNTIME_CACHE: dict = {}

_PROBE = r"""
import json, os, sys, sysconfig
mods = json.loads(sys.argv[1])
for m in mods:
    try:
        __import__(m)
    except Exception:
        pass
paths = set()
images = []
if sys.platform.startswith('linux'):
    try:
        for line in open('/proc/self/maps'):
            parts = line.split(None, 5)
            if len(parts) == 6 and parts[5].startswith('/'):
                images.append(parts[5].strip())
    except OSError:
        pass
elif sys.platform == 'darwin':
    try:
        import ctypes
        lib = ctypes.CDLL(None)
        lib._dyld_get_image_name.restype = ctypes.c_char_p
        for i in range(lib._dyld_image_count()):
            n = lib._dyld_get_image_name(i)
            if n:
                images.append(n.decode())
    except Exception:
        pass
exe = [sys.executable, getattr(sys, '_base_executable', sys.executable)]
cfg = []
d = os.path.dirname(os.path.abspath(sys.executable))
for c in (os.path.join(d, 'pyvenv.cfg'), os.path.join(os.path.dirname(d), 'pyvenv.cfg')):
    if os.path.exists(c):
        cfg.append(c)
libs = sorted({os.path.realpath(sysconfig.get_path(k)) for k in ('stdlib', 'platstdlib')
               if sysconfig.get_path(k)})
print(json.dumps({'exe': exe, 'cfg': cfg, 'stdlib': libs, 'images': images}))
"""


def _entries(d: str, skip=lambda n: False) -> list[str]:
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    return [os.path.join(d, n) for n in names if not skip(n)]


def python_runtime(executable: str | None = None, modules=()) -> list[str]:
    """The paths `executable -I -S` must read to start and import `modules`,
    WITHOUT any site-packages / dist-packages: the interpreter (and its
    symlink chain), pyvenv.cfg, the stdlib dirs and the shared libraries the
    process maps. A dir that holds a site-packages / dist-packages entry is
    granted by every confiner as "list it, read everything in it except the
    package dirs". Pass the result as
    run_solution(read_only=...) together with confine=[...]."""
    exe = executable or sys.executable
    key = (exe, tuple(sorted(modules)))
    if key in _RUNTIME_CACHE:
        return list(_RUNTIME_CACHE[key])
    import json
    r = subprocess.run([exe, "-I", "-S", "-c", _PROBE, json.dumps(sorted(modules))],
                       capture_output=True, text=True, timeout=60, env=clean_env(),
                       cwd="/")
    if r.returncode != 0:
        raise RuntimeError(f"python_runtime probe failed: {r.stderr.strip()[-300:]}")
    info = json.loads(r.stdout)
    out = []
    for p in info["exe"] + info["cfg"]:
        out.append(os.path.abspath(p))
        out.append(os.path.realpath(p))
    out += info["stdlib"]     # the confiners never grant a package dir under these
    for img in info["images"]:
        rp = os.path.realpath(img)
        if not any(seg in _PKG_DIRS for seg in rp.split(os.sep)):
            out.append(rp)
    seen, uniq = set(), []
    for p in out:
        if p not in seen and os.path.exists(p):
            seen.add(p)
            uniq.append(p)
    _RUNTIME_CACHE[key] = uniq
    return list(uniq)


def _sbpl(p: str) -> str:
    return os.path.realpath(p).replace("\\", "\\\\").replace('"', '\\"')


def _confine_profile(allow_fork: bool, ro: list[str], rw: list[str], *,
                     exec_only: str | None = None, deny_read: list[str] = ()) -> str:
    """macOS sandbox profile: reads only from the allowlist, writes only to rw,
    no network, no signal to another sandbox's process, no process-info on other processes (but argv/env stay
    readable through sysctl kern.procargs2: see _sandbox_prefix), and, with
    exec_only, no exec of any image but that one."""
    rules = ["(version 1)", "(allow default)", _SIGNAL_RULE, "(deny network*)",
             "(deny process-info* (with no-report))",
             "(allow process-info-pidinfo (target self))",
             "(allow process-info-codesignature (target self))"]
    if not allow_fork:
        rules.append("(deny process-fork)")
    rules.append("(deny file-read*)")
    rules.append("(allow file-read-metadata)")
    allow = ['(subpath "/System")', '(subpath "/usr/lib")', '(subpath "/private/var/db/dyld")',
             '(subpath "/dev")', '(literal "/")',
             # Rosetta, for an x86_64 interpreter on Apple silicon
             '(subpath "/Library/Apple/usr/libexec/oah")', '(subpath "/private/var/db/oah")']
    for p in ro:
        allow.append(f'(literal "{_sbpl(p)}")' if not os.path.isdir(p)
                     else f'(subpath "{_sbpl(p)}")')
    for p in rw:
        allow.append(f'(subpath "{_sbpl(p)}")')
    rules.append("(allow file-read* " + " ".join(allow) + ")")
    # an allowed dir may still hold a package dir (stdlib/site-packages)
    pk = []
    for p in ro:
        if os.path.isdir(p):
            for n in _PKG_DIRS:
                q = os.path.join(p, n)
                if os.path.lexists(q):
                    pk.append(f'(subpath "{_sbpl(os.path.join(os.path.realpath(p), n))}")')
                    if os.path.exists(q):
                        pk.append(f'(subpath "{_sbpl(q)}")')
    if pk:
        rules.append("(deny file-read* " + " ".join(pk) + ")")
    rules.append("(deny file-write*)")
    wr = ['(literal "/dev/null")', '(literal "/dev/zero")', '(literal "/dev/tty")',
          '(regex #"^/dev/fd/")'] + [f'(subpath "{_sbpl(p)}")' for p in rw]
    rules.append("(allow file-write* " + " ".join(wr) + ")")
    # after the read allowlist, so these denies win
    rules.append(f'(deny file-read* (regex #"{_EVAL_SECRET_RE}"))')
    for p in deny_read:
        rules.append(f'(deny file-read* (subpath "{_sbpl(os.path.realpath(p))}"))')
    if exec_only:
        rules.append("(deny process-exec*)")
        for img in _exec_images(exec_only):
            rules.append(f'(allow process-exec (literal "{_sbpl(img)}"))')
    return "".join(rules)


def _sandbox_ok() -> bool:
    """Whether sandbox-exec works here. Only a success is cached: a probe
    that failed under load (EAGAIN, the 10 s timeout) is retried by the
    next run instead of turning the sandbox off for the whole validator."""
    global _SANDBOX_OK
    if not _SANDBOX_OK:
        try:
            _SANDBOX_OK = subprocess.run(
                ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
                capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _SANDBOX_OK = False
    return _SANDBOX_OK


# Linux ------------------------------------------------------------------------

_LINUX_SYS = ("/lib", "/lib64", "/lib32", "/libx32", "/usr/lib", "/usr/lib64", "/usr/lib32",
              "/usr/libx32")
_LINUX_FILES = ("/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/localtime",
                "/dev/null", "/dev/zero", "/dev/urandom", "/dev/random")


def _linux_ro_plan(ro: list[str]) -> list[tuple[str, bool]]:
    """Linux read allowlist as (path, tree): entries of the system lib dirs
    (skipping python* dirs, which hold dist-packages), the loader files, and
    `ro`. A dir in `ro` that directly holds a package dir becomes
    (dir, False) -- listable only -- plus (entry, True) for its other
    entries. tree=True grants the path and everything below it."""
    out = []
    seen_real = set()
    for d in _LINUX_SYS:
        if not os.path.isdir(d):
            continue
        if os.path.islink(d):
            out.append(d)                  # /lib -> usr/lib: the loader path goes through it
        rd = os.path.realpath(d)
        if rd in seen_real:
            continue
        seen_real.add(rd)
        out += _entries(rd, lambda n: n.startswith("python") or n in _PKG_DIRS)
    out += [p for p in _LINUX_FILES if os.path.exists(p)]
    plan = [(p, True) for p in out]
    for p in ro:
        if os.path.isdir(p) and any(os.path.lexists(os.path.join(p, n)) for n in _PKG_DIRS):
            rp = os.path.realpath(p)
            plan.append((rp, False))
            plan += [(e, True) for e in _entries(rp, lambda n: n in _PKG_DIRS)]
        else:
            plan.append((p, True))
    return plan


_LL = {"create": 444, "add": 445, "restrict": 446}  # same on every arch
_LL_FS = {"exec": 1 << 0, "write": 1 << 1, "read": 1 << 2, "readdir": 1 << 3,
          "rmdir": 1 << 4, "rmfile": 1 << 5, "mkchar": 1 << 6, "mkdir": 1 << 7,
          "mkreg": 1 << 8, "mksock": 1 << 9, "mkfifo": 1 << 10, "mkblock": 1 << 11,
          "mksym": 1 << 12, "refer": 1 << 13, "truncate": 1 << 14, "ioctl": 1 << 15}
_LL_FILE_BITS = _LL_FS["exec"] | _LL_FS["write"] | _LL_FS["read"] | _LL_FS["truncate"] | _LL_FS["ioctl"]
_LL_ABI = None


def _landlock_abi() -> int:
    global _LL_ABI
    if _LL_ABI is None:
        _LL_ABI = 0
        if _LINUX:
            try:
                import ctypes
                libc = ctypes.CDLL(None, use_errno=True)
                v = libc.syscall(_LL["create"], None, ctypes.c_size_t(0), ctypes.c_uint32(1))
                _LL_ABI = v if v > 0 else 0
            except (OSError, AttributeError):
                _LL_ABI = 0
    return _LL_ABI


# Landlock has no UDP right at all, and TCP only from ABI 4 (kernel 6.7), so
# a landlock-confined solution could still send datagrams (DNS exfiltration,
# an oracle over UDP), and below 6.7 open any connection, under a confine
# documented as "no network" (review 1116). A seccomp filter in the same
# child closes it: socket() may create only AF_UNIX and AF_NETLINK sockets,
# io_uring (whose IORING_OP_SOCKET bypasses the socket() filter) is refused,
# and any foreign-ABI syscall (i386/x32 on x86_64) is refused outright.
# (audit arch, socket nr, io_uring_setup nr, refuse x32 range)
_SECCOMP_ARCH = {"x86_64": (0xC000003E, 41, 425, True),
                 "amd64": (0xC000003E, 41, 425, True),
                 "aarch64": (0xC00000B7, 198, 425, False),
                 "arm64": (0xC00000B7, 198, 425, False)}
_SOCK_ALLOWED = (1, 16)                        # AF_UNIX, AF_NETLINK


def _seccomp_net_prog():
    """A built `struct sock_fprog` (and the filter array it points to) that
    refuses every network socket, or raise OSError on an unknown arch."""
    import ctypes
    import platform
    spec = _SECCOMP_ARCH.get(platform.machine())
    if spec is None:
        raise OSError(f"no seccomp syscall table for {platform.machine()}")
    arch, nr_socket, nr_uring, x32 = spec
    ld, jeq, jge, ret = 0x20, 0x15, 0x35, 0x06
    allow, eperm, eacces = 0x7FFF0000, 0x00050000 | 1, 0x00050000 | 13
    # Layout: arch check, load nr, the body, then ALLOW, then DENY. BPF
    # jumps are relative, so each body jump counts the instructions left.
    tail = [(ret, 0, 0, allow), (ret, 0, 0, eacces)]      # ALLOW, DENY
    ins = []
    ins.append((ld, 0, 0, 4))                              # arch
    ins.append((jeq, 1, 0, arch))
    ins.append((ret, 0, 0, eperm))                         # foreign ABI
    ins.append((ld, 0, 0, 0))                              # syscall nr
    body = []
    if x32:
        body.append(("jge", 0x40000000, "deny"))
    body.append(("jeq", nr_uring, "deny"))
    body.append(("jeq_else_allow", nr_socket, None))
    body.append(("ld_arg0", None, None))
    for af in _SOCK_ALLOWED:
        body.append(("jeq_allow", af, None))
    body.append(("ja_deny", None, None))
    # each body entry is one instruction; ALLOW sits right after the body,
    # DENY one after that
    n = len(body)
    for i, (kind, k, _t) in enumerate(body):
        to_allow = n - i - 1               # instructions to skip to reach ALLOW
        to_deny = to_allow + 1
        if kind == "jge":
            ins.append((jge, to_deny, 0, k))
        elif kind == "jeq":
            ins.append((jeq, to_deny, 0, k))
        elif kind == "jeq_else_allow":
            ins.append((jeq, 0, to_allow, k))
        elif kind == "ld_arg0":
            ins.append((ld, 0, 0, 16))                     # args[0], low 32 bits
        elif kind == "jeq_allow":
            ins.append((jeq, to_allow, 0, k))
        elif kind == "ja_deny":
            ins.append((0x05, 0, 0, to_deny))              # BPF_JMP|BPF_JA
    ins += tail

    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8),
                    ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]

    class Prog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]
    arr = (Filter * len(ins))(*[Filter(*x) for x in ins])
    return Prog(len(ins), ctypes.cast(arr, ctypes.POINTER(Filter))), arr


def _landlock_prep(ro: list[str], rw: list[str]):
    """Everything the child needs, built before fork (no allocation games in
    the child beyond os.open and the syscalls)."""
    import ctypes
    abi = _landlock_abi()
    libc = ctypes.CDLL(None, use_errno=True)
    handled = 0
    for k in ("write", "read", "readdir", "rmdir", "rmfile", "mkchar", "mkdir", "mkreg",
              "mksock", "mkfifo", "mkblock", "mksym"):
        handled |= _LL_FS[k]
    if abi >= 2:
        handled |= _LL_FS["refer"]
    if abi >= 3:
        handled |= _LL_FS["truncate"]
    if abi >= 5:
        handled |= _LL_FS["ioctl"]

    class Attr(ctypes.Structure):
        _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64),
                    ("scoped", ctypes.c_uint64)]

    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed", ctypes.c_uint64), ("fd", ctypes.c_int32)]

    attr = Attr(handled, 0, 0)
    size = 8
    if abi >= 4:
        attr.net = 0b11          # BIND_TCP | CONNECT_TCP, with no rule: no TCP at all
        size = 16
    if abi >= 6:
        attr.scoped = 0b11       # abstract unix sockets, signals outside the domain
        size = 24
    rules = []
    rd = _LL_FS["read"] | _LL_FS["readdir"]
    for p, tree in _linux_ro_plan(ro):
        if os.path.islink(p) and p not in ro:
            continue                       # its target is covered (or not allowed)
        if not os.path.exists(p):
            continue
        isdir = os.path.isdir(p)
        # list-only: READ_DIR beneath lets it list subdirs, never read a file
        acc = (rd if tree else _LL_FS["readdir"]) if isdir else _LL_FS["read"]
        rules.append((os.fsencode(os.path.realpath(p)), acc))
    for p in ("/dev/null", "/dev/zero"):
        if os.path.exists(p):
            rules.append((os.fsencode(p), (_LL_FS["read"] | _LL_FS["write"]
                                           | (_LL_FS["truncate"] if abi >= 3 else 0))))
    for p in rw:
        rules.append((os.fsencode(os.path.realpath(p)), handled))
    beneath = Beneath(0, -1)
    seccomp = _seccomp_net_prog()          # raises on an unknown arch: fail closed
    return libc, attr, size, rules, beneath, handled, seccomp


def _landlock_child(prep):
    import ctypes
    libc, attr, size, rules, beneath, handled, (prog, _arr) = prep
    rfd = libc.syscall(_LL["create"], ctypes.byref(attr), ctypes.c_size_t(size),
                       ctypes.c_uint32(0))
    if rfd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")
    o_path = getattr(os, "O_PATH", 0o10000000)
    for path, acc in rules:
        try:
            fd = os.open(path, o_path | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            isdir = os.path.isdir(path)
            beneath.allowed = (acc & handled) if isdir else (acc & handled & _LL_FILE_BITS)
            beneath.fd = fd
            if libc.syscall(_LL["add"], ctypes.c_int(rfd), ctypes.c_int(1),
                            ctypes.byref(beneath), ctypes.c_uint32(0)) != 0:
                raise OSError(ctypes.get_errno(), f"landlock_add_rule {path!r}")
        finally:
            os.close(fd)
    if libc.prctl(38, 1, 0, 0, 0) != 0:                 # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "no_new_privs")
    if libc.syscall(_LL["restrict"], ctypes.c_int(rfd), ctypes.c_uint32(0)) != 0:
        raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    os.close(rfd)
    # PR_SET_SECCOMP, SECCOMP_MODE_FILTER: no network sockets (see _seccomp_net_prog)
    if libc.prctl(22, 2, ctypes.byref(prog), 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "seccomp network filter")


# mount namespace fallback
_MS = {"rdonly": 1, "nosuid": 2, "nodev": 4, "noexec": 8, "remount": 32, "bind": 4096,
       "rec": 16384, "private": 1 << 18, "noatime": 1024, "nodiratime": 2048,
       "relatime": 1 << 21}
_PIVOT_ROOT = {"x86_64": 155, "aarch64": 41, "arm64": 41}


def _locked_flags(p: str) -> int:
    """Mount flags a remount inside a user namespace must keep (EPERM otherwise)."""
    try:
        f = os.statvfs(p).f_flag
    except OSError:
        return 0
    out = 0
    for st, ms in ((2, "nosuid"), (4, "nodev"), (8, "noexec"), (1024, "noatime"),
                   (2048, "nodiratime"), (4096, "relatime")):
        if f & st:
            out |= _MS[ms]
    return out


def _chain(p: str):
    """[(symlink path, its target)...] met while resolving p, and the real path."""
    links = []
    parts = [x for x in os.path.abspath(p).split("/") if x]
    cur = "/"
    hops = 0
    while parts:
        nxt = os.path.join(cur, parts.pop(0))
        if os.path.islink(nxt):
            hops += 1
            if hops > 40:
                break
            tgt = os.readlink(nxt)
            links.append((nxt, tgt))
            base = tgt if tgt.startswith("/") else os.path.join(cur, tgt)
            parts = [x for x in os.path.normpath(base).split("/") if x] + parts
            cur = "/"
        else:
            cur = nxt
    return links, os.path.normpath(cur)


def _mountns_prep(ro: list[str], rw: list[str], cwd: str | None):
    import ctypes
    import platform
    import tempfile
    nr = _PIVOT_ROOT.get(platform.machine())
    if nr is None:
        raise OSError(f"no pivot_root number for {platform.machine()}")
    libc = ctypes.CDLL(None, use_errno=True)
    root = tempfile.mkdtemp(prefix="vlib-root-")
    links, binds, done = [], [], set()

    def add(p, writable, tree=True):
        lk, real = _chain(p)
        for l in lk:
            if l[0] not in done:
                done.add(l[0])
                links.append(l)
        if real in done or not os.path.exists(real):
            return
        done.add(real)
        if not tree:                      # list-only: an empty dir the entries are bound into
            dirs.append(real)
            return
        binds.append((real, os.path.isdir(real), writable, _locked_flags(real)))

    dirs = []
    for p, tree in _linux_ro_plan(ro):
        if os.path.islink(p) and p not in ro:
            lk, _real = _chain(p)
            for l in lk:
                if l[0] not in done:
                    done.add(l[0])
                    links.append(l)
            continue
        add(p, False, tree)
    for p in rw:
        add(p, True)
    binds.sort(key=lambda b: b[0].count("/"))
    return libc, nr, root, links, binds, dirs, os.getuid(), os.getgid(), cwd or os.getcwd()


def _mountns_child(prep):
    import ctypes
    libc, nr, root, links, binds, dirs, uid, gid, cwd = prep
    m = _MS

    def ck(rc, what):
        if rc != 0:
            raise OSError(ctypes.get_errno(), what)

    # The harness makes the validator non-dumpable, which leaves /proc/self/*
    # owned by root and the id maps unwritable. This child holds nothing the
    # solution could use (it has not run yet, and exec replaces the image).
    libc.prctl(4, 1, 0, 0, 0)                                          # PR_SET_DUMPABLE
    ck(libc.unshare(0x10000000 | 0x00020000 | 0x40000000), "unshare")  # USER|NS|NET
    for f, v in (("/proc/self/setgroups", "deny"), ("/proc/self/uid_map", f"{uid} {uid} 1"),
                 ("/proc/self/gid_map", f"{gid} {gid} 1")):
        fd = os.open(f, os.O_WRONLY)
        try:
            os.write(fd, v.encode())
        finally:
            os.close(fd)
    ck(libc.mount(b"none", b"/", None, m["rec"] | m["private"], None), "make-private")
    ck(libc.mount(b"tmpfs", root.encode(), b"tmpfs", m["nosuid"] | m["nodev"], b"mode=0755"),
       "tmpfs")
    R = root.rstrip("/")
    for d in dirs:
        os.makedirs(R + d, exist_ok=True)
    for path, isdir, writable, locked in binds:
        tgt = R + path
        if isdir:
            os.makedirs(tgt, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(tgt), exist_ok=True)
            if not os.path.lexists(tgt):
                os.close(os.open(tgt, os.O_CREAT | os.O_WRONLY, 0o644))
        ck(libc.mount(path.encode(), tgt.encode(), None, m["bind"] | m["rec"], None),
           f"bind {path}")
        if not writable:
            ck(libc.mount(None, tgt.encode(), None,
                          m["remount"] | m["bind"] | m["rdonly"] | locked, None),
               f"ro {path}")
    for lp, target in links:
        dst = R + lp
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.lexists(dst):
            os.symlink(target, dst)
    os.chdir(root)
    ck(libc.syscall(nr, b".", b"."), "pivot_root")
    ck(libc.umount2(b".", 2), "detach old root")               # MNT_DETACH
    ck(libc.mount(None, b"/", None, m["remount"] | m["bind"] | m["rdonly"]
                  | m["nosuid"] | m["nodev"], None), "ro root")
    os.chdir("/")
    os.chdir(cwd)


def _confine_linux(ro: list[str], rw: list[str], cwd: str | None):
    """(child function, cleanup) for Linux confinement, or raise OSError."""
    if _landlock_abi() >= 1:
        prep = _landlock_prep(ro, rw)
        return (lambda: _landlock_child(prep)), (lambda: None), f"landlock abi {_landlock_abi()}"
    prep = _mountns_prep(ro, rw, cwd)
    root = prep[2]

    def cleanup():
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    return (lambda: _mountns_child(prep)), cleanup, "mount namespace"


# --- run_solution ----------------------------------------------------------------

class Result:
    """What run_solution saw.

    verdict: "ok" (exited by itself within every budget; check returncode
    yourself), "cpu" (over the CPU budget), "wall" (over the wall budget),
    "daemon" (a descendant was orphaned or outlived the solution), "forked"
    (allow_fork=False and it started a child), "exec" (it replaced its image
    with another binary under confine_exec), "output" (wrote more than
    max_output bytes), "sandbox" (confine was asked for and could not be
    set up, or on macOS sandbox-exec is unusable: the solution never ran)."""

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


def _exec_images(exec_only) -> list[str]:
    """exec_only as a list of realpaths (one image, or a launcher + its target)."""
    imgs = [exec_only] if isinstance(exec_only, str) else list(exec_only)
    return [os.path.realpath(i) for i in imgs if i]


def _env_target(cmd: list) -> str | None:
    """The program `/usr/bin/env [-i] [NAME=VALUE]... PROG ...` will exec, or
    None. A validator uses env to set DYLD_* after sandbox-exec (which is
    SIP-protected and drops them), so the confined images are env AND PROG."""
    for a in cmd[1:]:
        if a.startswith("-") or "=" in a:
            continue
        return a
    return None


def _resolve_exec(prog: str, cwd: str | None, env: dict) -> str:
    """The realpath the kernel will exec for argv[0], as the CHILD sees it:
    a relative path (`./divmod`) against the solution's cwd, not ours, and a
    bare name through the child's PATH. The exec_only rule names this path
    literally, so resolving `./divmod` against the validator's cwd denied
    the solution its own image (sandbox-exec exits 71)."""
    import shutil
    if os.sep in prog:
        return os.path.realpath(os.path.join(cwd or os.getcwd(), prog))
    found = shutil.which(prog, path=env.get("PATH"))
    return os.path.realpath(found) if found else prog


def _exit_waiter(pid: int):
    """(wait(timeout) -> bool, close()): wait() returns True as soon as
    `pid` (our child) has exited, WITHOUT reaping it, so the final wait4
    still gets its rusage. The sampler used to time.sleep(poll_s) whatever
    happened, so every run paid up to a full poll after the solution was
    done (review 1116). kqueue NOTE_EXIT on macOS, a pidfd on Linux, a
    plain sleep elsewhere."""
    import select
    done = [False]
    if hasattr(select, "kqueue"):
        try:
            kq = select.kqueue()
        except OSError:
            kq = None
        if kq is not None:
            try:
                kq.control([select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                          flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                          fflags=select.KQ_NOTE_EXIT)], 0, 0)
            except OSError:
                done[0] = True          # already exited (ESRCH)

            def wait(timeout):
                if not done[0] and kq.control(None, 1, timeout):
                    done[0] = True
                return done[0]
            return wait, kq.close
    if hasattr(os, "pidfd_open"):
        try:
            fd = os.pidfd_open(pid)
        except OSError:
            fd = None
        if fd is not None:
            pl = select.poll()
            pl.register(fd, select.POLLIN)

            def wait(timeout):
                if not done[0] and pl.poll(int(timeout * 1000)):
                    done[0] = True
                return done[0]
            return wait, (lambda: os.close(fd))

    def wait(timeout):
        time.sleep(timeout)
        return False
    return wait, (lambda: None)


def run_solution(cmd, input=None, *, cpu_s: float, wall_s: float | None = None,
                 cwd: str | None = None, env: dict | None = None, text: bool = True,
                 allow_fork: bool = False, scale_cpu: bool = True,
                 max_output: int = 64 * 1024 * 1024, poll_s: float = 0.05,
                 deny_read: list[str] | None = None, confine: list[str] | None = None,
                 read_only: list[str] = (), confine_exec: bool | None = None) -> Result:
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
    the harness's sealed eval paths, OPENBEAST_VALIDATOR_SEAL). Reads of any
    evals/{refs,tasks,suites} tree are denied unconditionally. A deny-list:
    prefer `confine`.
    confine: dirs the solution may read AND write (its private work dir).
    With it set the solution is confined on both platforms: it may read
    only `read_only` (use python_runtime() for a Python solution), the system
    library dirs and a few /dev nodes, write only the confine dirs, and use
    no network and no process-info on other processes (macOS sandbox-exec;
    Linux landlock, else a user + mount namespace). If confinement cannot be
    set up the solution is NOT run and the verdict is "sandbox".
    confine_exec: restrict the exec of child/replaced images to the
    solution's own binary (macOS). None (default) decides automatically:
    confine when cmd[0] is a native program (a compiled unit's built binary),
    leave open when cmd[0] is this Python interpreter (it re-execs itself into
    its framework on some hosts, and a Python solution that execs Python gains
    nothing a deny-read on evals/ does not already stop). Pass True to force
    it, False to disable. A compiled unit should keep the default so the
    solution cannot exec into python3 or any other interpreter.
    """
    if isinstance(cmd, str):
        raise TypeError("run_solution takes an argv list, not a shell string")
    sc = scale()
    cpu_budget = float(cpu_s) * (sc if scale_cpu else 1.0)
    wall_budget = (float(wall_s) if wall_s is not None else 3 * float(cpu_s) + 5) * sc
    env = clean_env() if env is None else dict(env)
    seal = _sealed_paths() if deny_read is None else list(deny_read)
    exec0 = _resolve_exec(cmd[0], cwd, env) if cmd else ""
    interp = {os.path.realpath(sys.executable), os.path.realpath(python_cmd("x")[0])}
    if confine_exec is None:
        # Auto: confine a single compiled binary (allow_fork False, not the
        # harness interpreter). A shell/multiprocess solution (allow_fork
        # True) legitimately execs coreutils, so leave its exec open unless a
        # validator asks otherwise.
        confine_exec = (not allow_fork) and bool(exec0) and exec0 not in interp
    exec_only = exec0 if confine_exec else None
    if exec_only and exec0 == "/usr/bin/env":
        # A launcher: allow it and the one program it starts, nothing else.
        target = _env_target(list(cmd))
        exec_only = [exec0] + ([_resolve_exec(target, cwd, env)] if target else [])
    prefix = _sandbox_prefix(allow_fork, seal, exec_only=exec_only)
    if _DARWIN and confine is None and not prefix:
        # The deny-list is the only thing between the solution and fork,
        # exec into python3, the network and evals/refs on macOS. It used to
        # fail OPEN here (an empty prefix, verdict "ok"); fail closed like
        # confine= does (review 1116).
        return Result(None, "", "", 0.0, 0.0, "sandbox", "sandbox-exec unavailable")
    argv = prefix + list(cmd)
    confine_child, confine_cleanup = None, (lambda: None)
    if confine is not None:
        rw = [os.path.realpath(p) for p in confine]
        ro = [os.path.abspath(p) for p in read_only]
        if _DARWIN:
            if not _sandbox_ok():
                return Result(None, "", "", 0.0, 0.0, "sandbox", "sandbox-exec unavailable")
            argv = ["/usr/bin/sandbox-exec", "-p",
                    _confine_profile(allow_fork, ro, rw, exec_only=exec_only, deny_read=seal)
                    ] + list(cmd)
        elif _LINUX:
            try:
                confine_child, confine_cleanup, _how = _confine_linux(ro, rw, cwd)
            except (OSError, ImportError) as e:
                return Result(None, "", "", 0.0, 0.0, "sandbox", f"no confinement: {e}")
        else:
            return Result(None, "", "", 0.0, 0.0, "sandbox", f"no confinement on {sys.platform}")
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
        if confine_child is not None:
            try:
                confine_child()  # an exception here fails the Popen: never unconfined
            except BaseException as e:
                try:
                    os.write(err_w, f"{type(e).__name__}: {e}"[:400].encode("utf-8", "replace"))
                except OSError:
                    pass
                raise

    data = None
    if input is not None:
        data = input.encode() if isinstance(input, str) else bytes(input)
    t0 = time.monotonic()
    err_r, err_w = os.pipe() if confine_child is not None else (-1, -1)
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env,
                                start_new_session=True, preexec_fn=_pre, close_fds=True)
    except subprocess.SubprocessError as e:
        confine_cleanup()
        if confine_child is not None:
            os.close(err_w)
            why = os.read(err_r, 512).decode("utf-8", "replace") or str(e)
            os.close(err_r)
            return Result(None, "", "", 0.0, 0.0, "sandbox", f"confinement failed: {why}")
        raise
    if confine_child is not None:
        os.close(err_w)
        os.close(err_r)
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
    reaped = [False]
    exit_wait, exit_close = _exit_waiter(proc.pid)

    def _reap_nohang():
        """The solution's exit status once it has exited, else None. Reaps
        it through wait4 and KEEPS its rusage: Popen.poll() reaped it with
        waitpid, the final wait4 then hit ChildProcessError, and the rusage
        was dropped, so the CPU it used after the last sample was never
        counted and a fixed-CPU gate passed over-budget solutions about 1 run
        in 12 (review 1116)."""
        nonlocal rusage_cpu
        if reaped[0]:
            return proc.returncode
        try:
            pid_, wst, ru = os.wait4(proc.pid, os.WNOHANG)
        except ChildProcessError:
            return None
        if pid_ == 0:
            return None
        reaped[0] = True
        rusage_cpu = ru.ru_utime + ru.ru_stime
        proc.returncode = (-os.WTERMSIG(wst) if os.WIFSIGNALED(wst)
                           else os.WEXITSTATUS(wst))
        return proc.returncode

    def _exe(pid: int) -> str | None:
        """The realpath of a process's running image, Linux only."""
        if not _LINUX:
            return None
        try:
            return os.readlink(f"/proc/{pid}/exe")
        except OSError:
            return None

    # The image the solution must stay in. On Linux (where the macOS exec
    # sandbox does not apply) a compiled solution that execs into python3 or
    # `zig run <ref>` (reported on 166 and U4) replaces /proc/<pid>/exe; if it
    # changes while confine_exec is on, the run is refused as "exec".
    exe0 = _exe(proc.pid) if confine_exec else None

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
            r = _reap_nohang()
            live = _tree()
            for pid, (ppid, _pg, cpu) in live.items():
                known.add(pid)
                cpu_seen[pid] = max(cpu_seen.get(pid, 0.0), cpu)
            if r is not None:
                status = r
                break
            if exe0 is not None:
                now = _exe(proc.pid)
                if now is not None and now != exe0:
                    verdict, reason = "exec", f"solution exec'd into another image ({now})"
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
            exit_wait(poll_s)
        # The solution itself exited (or we are about to kill it): anything of
        # its tree still alive outlived it.
        if verdict == "ok":
            survivors = [p for p in _tree() if p != proc.pid]
            if survivors:
                verdict, reason = "daemon", f"{len(survivors)} process(es) outlived the solution"
    finally:
        _kill_all()
        if not reaped[0]:
            try:
                _pid, wst, ru = os.wait4(proc.pid, 0)
                proc.returncode = (-os.WTERMSIG(wst) if os.WIFSIGNALED(wst)
                                   else os.WEXITSTATUS(wst))
                rusage_cpu = ru.ru_utime + ru.ru_stime
            except ChildProcessError:
                rusage_cpu = 0.0
        exit_close()
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
        confine_cleanup()
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


# --- builds ----------------------------------------------------------------------
#
# A build is where the reference and the wrong solutions were beaten by
# toolchain capture (reported on 166 and 169): a planted rust-toolchain.toml,
# a $HOME/.../go/env with GOFLAGS=-ldflags=-s, a .cargo/config.toml in an
# ancestor of the build dir, each replacing the compiler or stripping the
# symbol table the validator inspects. The rule is: build a PRIVATE COPY of
# only the source, in a dir whose ancestors the agent cannot have planted, with
# a clean private HOME and the toolchain pinned in the environment, then require
# the product to be a native executable.

_CONFIG_NAMES = ("rust-toolchain", "rust-toolchain.toml", "go.work", "go.env",
                 ".go-version", "go.env.local")
_CONFIG_DIRS = (".cargo", ".config")  # .cargo/config{,.toml}, .config/go/env


def tool_path(name: str) -> str:
    """Absolute path of a toolchain binary, so a planted `rustc`/`cc` earlier
    on PATH in the build dir cannot shadow it. Falls back to the bare name."""
    import shutil
    return shutil.which(name) or name


def scan_toolchain_overrides(start: str, stop: str = "/") -> list[str]:
    """Toolchain-config files planted in `start` or any ancestor up to `stop`:
    rust-toolchain(.toml), .cargo/config(.toml), go.work, a go env file. The
    rustup and cargo proxies honour these from any ancestor of the build cwd,
    so a validator must build somewhere none exist (see private_build_dir)."""
    found, cur = [], os.path.realpath(start)
    stop = os.path.realpath(stop)
    while True:
        for n in _CONFIG_NAMES:
            p = os.path.join(cur, n)
            if os.path.exists(p):
                found.append(p)
        for d in _CONFIG_DIRS:
            for n in ("config", "config.toml", os.path.join("go", "env")):
                p = os.path.join(cur, d, n)
                if os.path.exists(p):
                    found.append(p)
        if cur == stop or cur == os.path.dirname(cur):
            break
        cur = os.path.dirname(cur)
    return found


def private_build_dir(files: dict, *, base: str | None = None) -> str:
    """A fresh 0700 dir under a harness-owned base that holds ONLY the given
    source — {relpath: contents|bytes} — and no toolchain-config sibling or
    ancestor the solution could have planted. Build in the returned dir.

    `base` defaults to a private dir created under TMPDIR; its one child is
    this build dir, so `scan_toolchain_overrides` up to `base` finds nothing
    unless the compile itself writes one. Returns the build dir path."""
    import tempfile
    if base:
        os.makedirs(base, exist_ok=True)
    else:
        base = tempfile.mkdtemp(prefix="ob-build-")
    os.chmod(base, 0o700)
    d = os.path.realpath(tempfile.mkdtemp(prefix="src-", dir=base))
    os.chmod(d, 0o700)
    for rel, content in files.items():
        dst = os.path.join(d, rel)
        if os.path.relpath(os.path.realpath(dst), d).startswith(".."):
            raise ValueError(f"source path escapes the build dir: {rel!r}")
        os.makedirs(os.path.dirname(dst) or d, exist_ok=True)
        mode = "wb" if isinstance(content, (bytes, bytearray)) else "w"
        with open(dst, mode) as f:
            f.write(content)
    return d


def build_env(extra: dict | None = None, *, home: str | None = None) -> dict:
    """Environment for a compiler so a planted config file cannot redirect it.

    HOME is a fresh private dir (no $HOME/Library/Application Support/go/env,
    reported on 166); GOENV=off and GOFLAGS cleared (no -ldflags=-s to strip
    the symbol table); GOTOOLCHAIN=local (no network toolchain download);
    RUSTUP_TOOLCHAIN is kept from the env if pinned there. Pass the build dir
    nowhere on PATH; call tool_path() for an absolute compiler path."""
    import tempfile
    env = clean_env(base={k: v for k, v in os.environ.items()})
    env["HOME"] = home or tempfile.mkdtemp(prefix="ob-home-")
    env["GOENV"] = "off"
    env["GOFLAGS"] = ""
    env["GOTOOLCHAIN"] = env.get("GOTOOLCHAIN") or "local"
    # Compiler caches: the harness puts them in the validator's private dir
    # (run_eval.build_cache_env, deleted after the unit). Outside the harness
    # they go under this private HOME, never ~/.cache, where a compiled
    # reference would stay readable to the next agent.
    cache = env.get("OPENBEAST_BUILD_CACHE") or os.path.join(env["HOME"], "cache")
    for k, sub in (("GOCACHE", "go-build"), ("GOMODCACHE", "go-mod"),
                   ("ZIG_GLOBAL_CACHE_DIR", "zig-global"),
                   ("ZIG_LOCAL_CACHE_DIR", "zig-local"),
                   ("CARGO_TARGET_DIR", "cargo-target")):
        if not env.get("OPENBEAST_BUILD_CACHE") or not env.get(k):
            env[k] = os.path.join(cache, sub)
    env["CCACHE_DISABLE"] = "1"
    env["SCCACHE_DISABLE"] = "1"
    env.pop("RUSTC_WRAPPER", None)
    if extra:
        env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


def is_native_executable(path: str) -> bool:
    """True iff `path` begins with ELF or Mach-O magic. A build that produced
    a script (a `#!/usr/bin/env python3` shim behind a planted rustc, reported
    on 166/169) is NOT a native executable, so a validator that requires one
    refuses it even when nm/otool print nothing for the non-object file."""
    try:
        with open(path, "rb") as f:
            m = f.read(4)
    except OSError:
        return False
    return m[:4] in (b"\x7fELF",                      # ELF
                     b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe",  # Mach-O 64/32 LE
                     b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",  # Mach-O BE
                     b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")  # fat


def assert_native_executable(path: str) -> None:
    """Raise AssertionError unless `path` is an ELF/Mach-O executable."""
    assert is_native_executable(path), f"{path} is not a native executable"


# --- the compiler is graded code's first reader ------------------------------------
#
# A validator compiles agent-written source with its own, unsandboxed rights.
# `#include "<repo>/evals/refs/<unit>/ref.c"` (or Rust `include!`, an
# `.incbin`, a quoted header symlinked to the reference) makes the COMPILER
# read the sealed reference the agent itself could never open, and the binary
# is then the reference (reported on 163/167/168). Two walls:
#   * run_build(): the compiler runs under the same file-read deny as a
#     solution (sandbox-exec on macOS: every evals/{refs,tasks,suites} tree
#     and OPENBEAST_VALIDATOR_SEAL), from a private_build_dir that holds only
#     the source, so no planted symlink sits next to it;
#   * lint_source_includes(): refuses, on every platform, the source-level
#     routes to a file outside the build dir: an include/import/embed
#     directive naming an absolute or `..` path or computed by a macro,
#     `.incbin`, Rust include!/include_str!/include_bytes!, zig @embedFile
#     and @cImport. A single-file solution needs none of them.
# On Linux run_build has no kernel read-deny (the deny-list form of
# run_solution has none either; see "Residual gaps" in evals/README.md), so
# the lint is the wall there.

_C_TOKEN = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?\*/', re.DOTALL)
_C_DIRECTIVE = re.compile(r"^[ \t]*(?:#|%:)[ \t]*(include_next|include|import|embed)\b[ \t]*(.*)$", re.MULTILINE)
_C_HEADER = re.compile(r'^(<[^<>"\n]+>|"[^"\n]+")\s*$')


def _strip_c_comments(src: str) -> str:
    # translation phases 1-3: trigraph `??/` is a backslash under -std=c11,
    # then line splices, then comments (each one becomes a space)
    src = src.replace("??/", "\\")
    src = re.sub(r"\\[ \t]*\r?\n", "", src)
    return _C_TOKEN.sub(lambda m: m.group(0) if m.group(0)[0] in "\"'" else " ", src)


def _escapes_dir(name: str) -> bool:
    name = name.strip()[1:-1].strip()
    return name.startswith(("/", "\\", "~")) or ".." in name or ":" in name


def lint_source_includes(src: str, lang: str) -> str | None:
    """None, or why `src` (C, C++, Rust, Zig or Go) could make the compiler
    read a file outside its build dir. See the block comment above."""
    lang = {"c++": "cpp", "rs": "rust"}.get(lang, lang)
    if lang in ("c", "cpp", "go"):
        code = _strip_c_comments(src)
        if lang == "go":
            # cgo preamble (needs import "C"; CGO_ENABLED=0 refuses it too)
            # and //go:embed (which the go tool already keeps in-module)
            if re.search(r'^\s*import\s*(\(\s*)?"C"', code, re.MULTILINE) or "go:embed" in src:
                return "cgo or go:embed"
            return None
        for m in _C_DIRECTIVE.finditer(code):
            arg = m.group(2).strip()
            if not _C_HEADER.match(arg):
                return f"computed #{m.group(1)} ({arg[:40]!r})"
            if _escapes_dir(arg):
                return f"#{m.group(1)} of a path outside the build dir ({arg[:60]})"
        if re.search(r"\bincbin\b", code, re.IGNORECASE):
            return ".incbin"
        return None
    if lang == "rust":
        code = re.sub(r"//[^\n]*|/\*.*?\*/", " ", src, flags=re.DOTALL)
        m = re.search(r"\binclude(_str|_bytes)?\b", code)
        if m:
            return f"{m.group(0)}! (a single-file solution includes nothing)"
        if re.search(r"\bincbin\b", code, re.IGNORECASE):
            return ".incbin"
        return None
    if lang == "zig":
        code = re.sub(r"//[^\n]*", " ", src)
        m = re.search(r"@(embedFile|cImport|cInclude)\b", code)
        if m:
            return f"@{m.group(1)} (a single-file solution includes nothing)"
        return None
    return None


def read_source(path: str, cap: int = 4 << 20) -> bytes | None:
    """The bytes of the solution's source file, or None unless `path` is a
    regular file (not a symlink, FIFO or device) of at most `cap` bytes. The
    validator reads with its own unsandboxed rights, so a source symlinked
    to the reference must never be followed into a private build dir."""
    import stat
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        out = bytearray()
        while len(out) <= cap:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            out += chunk
        return bytes(out) if len(out) <= cap else None
    finally:
        os.close(fd)


def build_private(files: dict, cmd, *, lang: str, timeout: float,
                  extra_env: dict | None = None):
    """Lint, then compile `files` ({relpath: bytes}) with `cmd` in a fresh
    private_build_dir under build_env() and run_build(). Returns
    (build_dir, None) or (None, why-it-failed). The dir's parent is removed
    when the validator exits."""
    import atexit
    import shutil
    import tempfile
    for rel, content in files.items():
        text = content.decode("utf-8", "replace") if isinstance(content, (bytes, bytearray)) else content
        bad = lint_source_includes(text, lang)
        if bad:
            return None, f"forbidden in {rel}: {bad}"
    base = tempfile.mkdtemp(prefix="ob-build-")
    atexit.register(shutil.rmtree, base, True)
    d = private_build_dir(files, base=base)
    home = os.path.join(base, "home")
    os.mkdir(home, 0o700)
    env = build_env(extra_env, home=home)
    try:
        b = run_build(cmd, cwd=d, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "build timed out"
    if b.returncode:
        return None, "build failed: " + (b.stderr or b.stdout)[-300:]
    return d, None


def run_build(cmd, *, cwd: str, env: dict | None = None, timeout: float,
              deny_read: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run a compiler (an argv list) like subprocess.run(capture_output=True,
    text=True, stdin=/dev/null), on macOS under sandbox-exec with reads of
    every evals/{refs,tasks,suites} tree and the harness seal denied. The
    compiler may fork and use the network (toolchains probe both); only the
    read-deny is added. Raises subprocess.TimeoutExpired like run()."""
    if isinstance(cmd, str):
        raise TypeError("run_build takes an argv list, not a shell string")
    seal = _sealed_paths() if deny_read is None else list(deny_read)
    prefix = _sandbox_prefix(True, seal, deny_net=False, deny_procinfo=False, always=True)
    return subprocess.run(prefix + list(cmd), cwd=cwd, env=clean_env() if env is None else env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          errors="replace", timeout=timeout)


# --- sanitizer builds ------------------------------------------------------------
#
# 170 is detectable at compile time: a build with -fsanitize=thread changes
# observable builtins (link_libcpp, pic) so the solution can branch on the
# build (reported on 170). The defence is twofold: (1) the zig/C lint bans the
# fields that differ, and (2) a validator should also validate BEHAVIOUR in
# the plain build, where no sanitizer flag is present at all. These helpers
# give both builds from ONE source so neither is a different program.

SANITIZER_FLAGS = {
    "thread": ("-fsanitize=thread",),
    "address": ("-fsanitize=address", "-fno-omit-frame-pointer"),
    "undefined": ("-fsanitize=undefined", "-fno-sanitize-recover=all"),
}


def sanitizer_flags(kind: str) -> tuple:
    """Compiler flags for a sanitizer build (`thread`/`address`/`undefined`).
    A validator builds the SAME source with and without these. Where the build
    can be told apart at compile or run time (zig's @import("builtin") exposes
    link_libcpp/pic under TSan), the validator must also check behaviour in the
    PLAIN build — the sanitizer is a stress amplifier, not the only judge."""
    return SANITIZER_FLAGS[kind]


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
