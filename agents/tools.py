"""Built-in tools for the agent runner.

Each tool is defined as:
  - A schema (OpenAI function-calling format)
  - A handler function that executes the tool and returns a string result
"""

from __future__ import annotations

import codecs
import difflib
import functools
import glob
import html
import http.client
import ipaddress
import json
import os
import re
import resource
import shlex
import signal
import socket
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextvars import ContextVar
from datetime import datetime
from typing import Any

# Largest slice of a child's output we retain in the PARENT process. The
# RLIMIT_AS below caps the child's own memory, but `cat /dev/zero` streams
# unboundedly *into the parent's* pipe buffer — which has no rlimit — so we
# must cap what the parent keeps too, or the box OOMs anyway. We keep
# draining past this (to avoid a pipe-full deadlock) but discard the excess.
_MAX_CAPTURE_BYTES = 4 * 1024 * 1024
# Largest file read_file will slurp; also refuses non-regular files so a
# read of /dev/zero / a FIFO can't hang or OOM the in-process runner.
_MAX_READ_BYTES = 64 * 1024 * 1024

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

# Per-process address-space cap for model-written code. subprocess timeouts
# with shell=True kill only the sh wrapper — before the 2026-07-07 fix below,
# the grandchild survived as an orphan and two runaway eval programs grew to
# ~140 GB each, exhausting RAM + 187 GB swap and OOM-killing the whole
# session. The killpg reaps the tree at timeout; this rlimit bounds how much
# a memory bomb can grab in the seconds before the timeout fires.
_CHILD_AS_LIMIT = 32 * 1024**3


def _killpg(proc):
    """SIGKILL the whole process group, then reap the leader."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.kill()  # belt-and-suspenders if the leader left its group
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_reaped(command, timeout, as_limit=None, **popen_kw):
    """subprocess.run(shell=True)-alike that (a) kills the WHOLE process group
    on timeout and (b) bounds how much output the PARENT buffers.

    Plain subprocess.run kills only the direct child (/bin/sh) on timeout,
    orphaning whatever the shell spawned; and communicate() buffers the
    child's entire stdout in the parent, so `cat /dev/zero` OOMs the runner
    even though the child is rlimited. start_new_session puts the tree in its
    own group for a group SIGKILL, and a reader thread drains the pipe while
    retaining at most _MAX_CAPTURE_BYTES (discarding the rest to avoid a
    pipe-full deadlock). Output is decoded errors="replace" so binary bytes
    don't crash the call.

    Returns (returncode, output_str) — stderr folded into stdout.
    Raises subprocess.TimeoutExpired after reaping on timeout.
    """
    # Cap the child's address space. prlimit (from the parent, post-spawn) is
    # safe when the caller is a threaded server (FastMCP); /bin/sh's own
    # children inherit the limit when it forks them. prlimit(2) is Linux-only —
    # on Darwin fall back to preexec_fn/setrlimit (the documented fork-safety
    # caveat is accepted only on the prlimit-less platform, and RLIMIT_AS is
    # best-effort there anyway).
    _as_limit = as_limit or _CHILD_AS_LIMIT
    if not hasattr(resource, "prlimit"):
        def _cap_as():
            try:
                resource.setrlimit(resource.RLIMIT_AS,
                                   (_as_limit, _as_limit))
            except (OSError, ValueError):
                pass
        popen_kw = dict(popen_kw, preexec_fn=_cap_as)
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        **popen_kw,
    )
    if hasattr(resource, "prlimit"):
        # NPROC/FSIZE/CPU joined AS in the 2026-09-10 hardening: fork bombs,
        # disk-fill, and pure-CPU spins previously ran free until the wall
        # timeout. Generous ceilings — real builds fork hundreds of procs and
        # write big artifacts; these stop bombs, not work.
        _limits = ((resource.RLIMIT_AS, _as_limit),
                   (resource.RLIMIT_NPROC, 2048),
                   (resource.RLIMIT_FSIZE, 8 * 1024**3),
                   (resource.RLIMIT_CPU, 1800))
        for _res, _cap in _limits:
            try:
                resource.prlimit(proc.pid, _res, (_cap, _cap))
            except (OSError, ProcessLookupError, AttributeError, ValueError):
                pass
    # Head+tail capture (2026-09-10 hardening): the FINAL lines of long
    # output are where verdicts live (pytest summaries, linker errors) —
    # keeping only the head silently discarded exactly what the model
    # needed. Head gets half the budget, tail a ring over the other half.
    _HEAD = _MAX_CAPTURE_BYTES // 2
    _TAIL = _MAX_CAPTURE_BYTES - _HEAD
    head = bytearray()
    tail = bytearray()
    total = [0]

    def _drain():
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            total[0] += len(chunk)
            if len(head) < _HEAD:
                take = min(_HEAD - len(head), len(chunk))
                head.extend(chunk[:take])
                chunk = chunk[take:]
            if chunk:
                tail.extend(chunk)
                if len(tail) > _TAIL:
                    del tail[: len(tail) - _TAIL]

    def _assemble() -> str:
        if not tail:
            return head.decode("utf-8", errors="replace")
        elided = total[0] - len(head) - len(tail)
        marker = (f"\n[... {elided} bytes elided — head and tail kept ...]\n"
                  if elided > 0 else "")
        return (head.decode("utf-8", errors="replace") + marker
                + tail.decode("utf-8", errors="replace"))

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _killpg(proc)
        # Hand the partial output to the caller — "which test hung?" is
        # answerable only from what was printed before the kill.
        reader.join(timeout=2)
        raise subprocess.TimeoutExpired(command, timeout,
                                        output=_assemble()) from None
    finally:
        reader.join(timeout=5)
        if reader.is_alive():
            # The shell exited but a backgrounded grandchild still holds the
            # stdout pipe — the reader would block forever (and so would we).
            # Killing the group unblocks it AND enforces the no-orphans
            # policy: nothing outlives the tool call. Long-lived daemons must
            # detach properly (setsid + redirect away from the pipe).
            _killpg(proc)
            reader.join(timeout=2)
        if reader.is_alive():
            # Grandchild escaped the group (setsid): force EOF on the raw fd.
            try:
                os.close(proc.stdout.fileno())
            except OSError:
                pass
            reader.join(timeout=2)
        try:
            proc.stdout.close()
        except Exception:
            pass
    out = _assemble()
    kept_bytes = len(head) + len(tail)
    if total[0] > kept_bytes:
        out += f"\n[output truncated — {total[0]} bytes produced, kept {kept_bytes}]"
    return proc.returncode, out


# Per-request workspace override. The identity tool server
# (agents/openapi_tools.py) serves many users from ONE process, so an env
# var can't carry the per-user shard — a ContextVar can: it is scoped to
# the request's thread/task, set before the tool call and reset after.
_BASE_DIR_OVERRIDE: ContextVar = ContextVar("openbeast_base_dir", default=None)


def set_base_dir_override(path: str):
    """Point relative tool paths (and the manifest) at `path` for the
    current context. Returns a token for reset_base_dir_override()."""
    return _BASE_DIR_OVERRIDE.set(path)


def reset_base_dir_override(token) -> None:
    _BASE_DIR_OVERRIDE.reset(token)


def _base_dir() -> str:
    """Directory that relative, model-supplied paths resolve against.

    The identity server sets a per-request override (the caller's workspace
    shard). A spawned background agent gets AGENT_WORKDIR (its task's
    working dir). A direct tool call from a chat turn gets
    OPENBEAST_FILES_DIR — a persistent, private (0700) workspace — so
    generated files (reports, charts) land somewhere durable and NOT
    world-readable in /tmp, and every conversation shares one predictable
    home instead of the model's ad-hoc default. Falls back to the process
    cwd only if none is set (e.g. bare `python tools.py`).

    NOT a confinement boundary: `..` and absolute paths leave it freely by
    design (agents do legitimate work anywhere the denylist allows). Writes
    are protected only by _guard_write_path's denylist; kernel-level
    confinement is Arsenal Phase 1 (Sandlock).
    """
    return (_BASE_DIR_OVERRIDE.get()
            or os.environ.get("AGENT_WORKDIR")
            or os.environ.get("OPENBEAST_FILES_DIR")
            or os.getcwd())


def _resolve(path: str) -> str:
    """Expand ~, anchor a relative path to _base_dir(), then realpath it.
    Absolute paths the model supplies are honored as-given (post-realpath)."""
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(_base_dir(), p)
    return os.path.realpath(p)


def _scrubbed_env() -> dict:
    """Copy of the process env minus the stack's secrets.

    The bash tool runs MODEL-authored commands: a prompt-injected `env`
    must not hand over the RBAC profile keys, the identity-JWT signing
    secret (with which the model could mint admin identities), or the
    WebUI admin password that the server process was launched with.
    Mirrors start.sh's systemd-setenv secret filter — stack-prefixed
    names containing KEY/SECRET/PASSWORD are dropped; the user's own
    unrelated env vars are left alone."""
    env = dict(os.environ)
    # Exact-name denylist (2026-09-10 hardening): OPENAI_API_KEY is the very
    # credential runner.py's _key_endpoint_trusted guards against
    # exfiltration — yet the stack-prefix filter above left it in the env of
    # every model-authored command. TOKEN joins the secret-shaped substrings.
    _DENY_EXACT = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN",
                   "GITHUB_TOKEN", "GH_TOKEN"}
    for name in list(env):
        up = name.upper()
        if up in _DENY_EXACT:
            env.pop(name)
            continue
        if (up.startswith(("OPENBEAST_", "WEBUI_", "LLAMA_", "SEARXNG_"))
                and any(t in up for t in ("KEY", "SECRET", "PASSWORD", "TOKEN"))):
            env.pop(name)
    return env


def bash(command: str, timeout: int = 120) -> str:
    """Run a shell command and return stdout + stderr.

    Enforced safety: child process group is SIGKILLed on timeout,
    RLIMIT_AS caps the child's address space, and output is capped.
    """
    try:
        # Arsenal Phase 1 hook: OPENBEAST_BASH_WRAPPER is a command prefix
        # (e.g. "sandlock --profile openbeast --") that wraps every model
        # shell command in a kernel-level sandbox. Unset (the default and
        # the eval-validated configuration) runs the command directly.
        # Read per-call so a server doesn't need a restart to toggle it.
        wrapper = os.environ.get("OPENBEAST_BASH_WRAPPER", "").strip()
        if wrapper:
            command = f"{wrapper} /bin/sh -c {shlex.quote(command)}"
        returncode, output = run_reaped(
            command,
            timeout,
            cwd=_base_dir(),
            env=_scrubbed_env(),
        )
        if not output.strip():
            output = f"(exit code {returncode})"
        elif returncode != 0:
            # A nonzero exit was invisible whenever the command printed
            # anything — the model's one machine-stable failure token.
            output += f"\n(exit code {returncode})"
        if len(output) > 50_000:
            # Head+tail at the string layer too: the tail holds the verdict,
            # and a plain [:50_000] slice also destroyed run_reaped's own
            # truncation marker.
            output = (output[:25_000]
                      + f"\n[... {len(output) - 50_000} chars elided — head and tail kept; "
                      f"re-run with | tail / | grep to narrow ...]\n"
                      + output[-25_000:])
        return output
    except subprocess.TimeoutExpired as e:
        partial = (e.output or "")
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        tail_note = ""
        if partial.strip():
            tail_note = ("\n--- partial output before the kill (tail) ---\n"
                         + partial[-10_000:])
        return f"Error: command timed out after {timeout}s{tail_note}"
    except Exception as e:
        return f"Error: {e}"


# Credential / persistence locations model-written file ops must never touch.
# Realpath-resolved (so symlinks can't dodge it) and scoped to the real HOME —
# a project-local .npmrc or a dotfiles repo's copy of .bashrc is legitimate
# coding work; only the live copies under ~ are persistence/exfil targets.
# Defense-in-depth: the authoritative sandbox is Arsenal Phase 1 (Sandlock).
_PROTECTED_DIRS = (".ssh", ".gnupg", ".aws", ".kube", ".docker")
_PROTECTED_BASENAMES = {
    ".netrc", ".git-credentials", ".npmrc", ".pypirc",
    ".bashrc", ".bash_profile", ".zshrc", ".profile",
}

# Pseudo-filesystems that read_file refuses: regular-file-shaped but can be
# infinite (/proc/kcore), streaming (a 0-size /proc file), or side-effecting.
_HAZARD_PREFIXES = ("/proc", "/sys", "/dev")


def _hazard_path(rp: str) -> bool:
    """True if the (already-realpath'd) path lives in a pseudo-filesystem."""
    return any(rp == p or rp.startswith(p + os.sep) for p in _HAZARD_PREFIXES)


def _guard_write_path(path: str):
    """Return an error string if `path` is a protected credential/persistence
    target, else None. Applied to write_file / edit_file only — reads stay
    open (an agent may legitimately read config)."""
    rp = os.path.realpath(os.path.expanduser(path))
    home = os.path.realpath(os.path.expanduser("~"))
    rel = os.path.relpath(rp, home)
    inside_home = rel != os.pardir and not rel.startswith(os.pardir + os.sep)
    if inside_home:
        if rel.split(os.sep)[0] in _PROTECTED_DIRS:
            return f"Error: refusing to write inside a credential store ({rp})"
        if rel in _PROTECTED_BASENAMES:
            return f"Error: refusing to write protected file {rp}"
    if inside_home:
        # Persistence/execution targets (2026-09-10 hardening): a hook fires
        # on the next git command, autostart/systemd-user on next login, and
        # ~/.local/bin shadows real binaries on PATH.
        _persist = (os.path.join(".config", "systemd", "user"),
                    os.path.join(".config", "autostart"),
                    os.path.join(".local", "bin"))
        for pfx in _persist:
            if rel == pfx or rel.startswith(pfx + os.sep):
                return f"Error: refusing to write a persistence target ({rp})"
    if rp == "/etc" or rp.startswith("/etc/"):
        return f"Error: refusing to write under /etc ({rp})"
    if rp.endswith(f"{os.sep}.git{os.sep}config"):
        return f"Error: refusing to write a git config ({rp})"
    if f"{os.sep}.git{os.sep}hooks{os.sep}" in rp or rp.endswith(f"{os.sep}.git{os.sep}hooks"):
        # A hook is arbitrary code execution on the next git invocation —
        # strictly worse than .git/config, which was already blocked.
        return f"Error: refusing to write a git hook ({rp})"
    return None


def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    """Read lines from a file."""
    try:
        path = _resolve(path)
        # Pseudo-filesystems (procfs/sysfs/devfs) present as regular files but
        # can be infinite, blocking, or side-effecting to read — e.g. a 0-size
        # /proc file that streams unbounded content, or /dev/zero. _resolve
        # already realpath'd, so a symlink into them is caught here too.
        if _hazard_path(path):
            return (f"Error: refusing to read {path} — pseudo-filesystem paths "
                    f"(/proc, /sys, /dev) can be infinite or side-effecting")
        # O_NONBLOCK so opening a FIFO can't hang; fstat (not stat) so the
        # regular-file check and the read see the same inode.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            return f"Error: not a regular file (refusing to read {path})"
        if st.st_size > _MAX_READ_BYTES:
            os.close(fd)
            return (f"Error: file too large ({st.st_size} bytes > "
                    f"{_MAX_READ_BYTES}); read a slice with bash instead")
        os.set_blocking(fd, True)
        with os.fdopen(fd, "r", errors="replace") as f:
            # Bound the read regardless of the stat'd size: a pseudo-file (or a
            # file growing under us) can report size 0 yet stream forever. One
            # byte past the cap tells us it was truncated.
            data = f.read(_MAX_READ_BYTES + 1)
        truncated = len(data) > _MAX_READ_BYTES
        lines = data[:_MAX_READ_BYTES].splitlines(keepends=True)
        total = len(lines)
        if offset >= total and total > 0:
            # An offset past EOF used to return an empty SUCCESS ("lines
            # 801-800 of 600") — teach instead of confusing.
            return (f"Error: offset {offset} is past the end of {path} "
                    f"({total} lines) — last page: offset={max(0, total - limit)}")
        selected = lines[offset : offset + limit]
        # Context-bomb caps (2026-09-10 hardening): the file-size cap bounded
        # DISK reads, but the RETURNED STRING was unbounded — one minified
        # line could dump megabytes into a 27B's context. Clamp per-line and
        # per-call, and always say how to resume.
        _LINE_CLAMP, _CALL_CAP = 2000, 50_000
        numbered, used, shown = [], 0, 0
        for i, line in enumerate(selected):
            if len(line) > _LINE_CLAMP:
                line = line[:_LINE_CLAMP] + f"…[line truncated, {len(line)} chars total]\n"
            row = f"{i + offset + 1}\t{line}"
            if used + len(row) > _CALL_CAP:
                break
            numbered.append(row)
            used += len(row)
            shown += 1
        note = f" (+ more; read capped at {_MAX_READ_BYTES} bytes)" if truncated else ""
        header = f"[{path}] lines {offset + 1}-{offset + shown} of {total}{note}\n"
        resume = ""
        if offset + shown < total:
            resume = (f"\n[{total - offset - shown} more lines — continue with "
                      f"read_file(path, offset={offset + shown}, limit={limit})]")
        return header + "".join(numbered) + resume
    except Exception as e:
        return f"Error: {e}"


def _manifest_log(action: str, path: str, nbytes: int) -> None:
    """Append a write record to the workspace manifest (.manifest.jsonl).

    The chat workspace (OPENBEAST_FILES_DIR) is a flat namespace shared by
    every conversation; the model only "knows" a file exists if its name is
    in context. The manifest is the durable index — a later turn (or user)
    can answer "what files have I made?" by reading it. Only writes that
    LAND inside the workspace are recorded (agent workdirs and absolute
    paths elsewhere are not workspace artifacts). Fail-soft by contract:
    a manifest problem must never break the write it describes.
    """
    try:
        # Shard-aware: under the identity server, the manifest lives at the
        # caller's shard root (each user indexes only their own files).
        base = _BASE_DIR_OVERRIDE.get() or os.environ.get("OPENBEAST_FILES_DIR")
        if not base:
            return
        base = os.path.realpath(os.path.expanduser(base))
        real = os.path.realpath(path)
        if os.path.commonpath([base, real]) != base:
            return  # not a workspace file
        if os.path.basename(real) == ".manifest.jsonl":
            return  # never index the index
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "action": action,
            "path": os.path.relpath(real, base),
            "bytes": nbytes,
        }
        with open(os.path.join(base, ".manifest.jsonl"), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Push-diagnostics (docs/LANG_AWARENESS_PLAN.md §3) — experiment-gated.
# Default OFF; OPENBEAST_DIAGNOSTICS=1 opts in (set by the A/B's on-arms).
# Every write_file/edit_file of a source file gets its language's checker
# verdict appended to the tool result — pushed, never model-initiated.
# Security spec (§3.3): paths shlex-quoted (run_reaped is shell=True),
# scrubbed env, OPENBEAST_BASH_WRAPPER honored, tight AS rlimit, output cap,
# and env hardening that closes go's toolchain-download/network channels.
# ---------------------------------------------------------------------------

_DIAG_TIMEOUT = 10
_DIAG_MAX_LINES = 30
_DIAG_MAX_BYTES = 2048
_DIAG_AS_LIMIT = 4 * 1024**3   # tighter than _CHILD_AS_LIMIT (§3.3)
_DIAG_SLOTS = threading.BoundedSemaphore(2)  # N-jobs × checkers RAM cap


def _diag_checker(path: str):
    """(lang, command, extra_env, scratch_dirs) for a source path, or None.

    Commands mirror the eval validators' flags (docs/LANG_AWARENESS_PLAN.md
    §3.2 — measured on the rig). A missing toolchain returns None: the
    feature silently no-ops rather than degrading the write."""
    ext = os.path.splitext(path)[1]
    q = shlex.quote(path)
    qdir = shlex.quote(os.path.dirname(path) or ".")
    if ext == ".zig":
        if not shutil.which("zig"):
            return None
        scratch = tempfile.mkdtemp(prefix="diagzig")
        env = {"ZIG_GLOBAL_CACHE_DIR": scratch, "ZIG_LOCAL_CACHE_DIR": scratch}
        # build-exe -fno-emit-bin runs full Sema (ast-check is AstGen-only
        # and passes stale-std code CLEAN — measured; build-obj skips
        # unreferenced fns). Main-less files fall back to ast-check to avoid
        # a bogus missing-main error.
        try:
            with open(path, errors="replace") as f:
                # \b + pub fn main( — the bare substring false-positived on
                # `fn mainLoop`/comments, selecting build-exe on library code
                # and FABRICATING "no member named main" errors (2026-09-10
                # review; attenuated the diagnostics A/B treatment).
                has_main = re.search(r"\bpub\s+fn\s+main\s*\(",
                                     f.read(_DIAG_MAX_BYTES * 64)) is not None
        except OSError:
            has_main = True
        cmd = (f"zig build-exe -fno-emit-bin {q}" if has_main
               else f"zig ast-check {q}")
        return ("zig", cmd, env, [scratch])
    if ext == ".rs":
        if not shutil.which("rustc"):
            return None
        scratch = tempfile.mkdtemp(prefix="diagrs")
        # NOT -o /dev/null (rustc can't create temp files there — measured
        # failing on CLEAN code). No --edition pin: mirrors the validator.
        return ("rust", f"rustc --emit=metadata --out-dir {shlex.quote(scratch)} {q}",
                {}, [scratch])
    if ext == ".go":
        if not shutil.which("go"):
            return None
        # File mode works module-less; vet all siblings to avoid false
        # `undefined:` on multi-file packages. Env closes the toolchain-
        # download (GOTOOLCHAIN), network (GOPROXY) and cgo channels.
        env = {"GOTOOLCHAIN": "local", "GOPROXY": "off",
               "GOFLAGS": "-mod=readonly", "CGO_ENABLED": "0"}
        return ("go", f"cd {qdir} && go vet ./*.go", env, [])
    if ext == ".c":
        if not shutil.which("gcc"):
            return None
        return ("c", f"gcc -fsyntax-only -std=c11 -Wall -Wextra -I{qdir} {q}", {}, [])
    if ext in (".cpp", ".cc", ".cxx"):
        if not shutil.which("g++"):
            return None
        return ("c++", f"g++ -fsyntax-only -std=c++17 -Wall -Wextra -I{qdir} {q}", {}, [])
    if ext == ".py":
        # -I (isolated) is a SECURITY requirement: bare python executes
        # model-writable .pth files from user site-packages at startup.
        return ("python", f"{shlex.quote(sys.executable)} -I -m py_compile {q}", {}, [])
    if ext == ".sh":
        if not shutil.which("shellcheck"):
            return None
        return ("shell", f"shellcheck -S warning {q}", {}, [])
    return None


def diagnostics_enabled() -> bool:
    """Read per-call so a server toggle needs no restart (mirrors the
    OPENBEAST_BASH_WRAPPER pattern). BEAST_ASSIST is the user-facing
    name (locked 2026-09-09); OPENBEAST_DIAGNOSTICS remains the
    internal/mechanism spelling — either enables the feature."""
    return (os.environ.get("OPENBEAST_DIAGNOSTICS", "").strip() == "1"
            or os.environ.get("BEAST_ASSIST", "").strip() == "1")


def _diag_log_timing(lang: str, ms: float, status: str) -> None:
    """Append one JSONL timing row when OPENBEAST_DIAG_TIMING_LOG is set.
    The per-write latency clause of the beast-assist ship rule was
    unmeasurable in the first A/B rounds — this closes that gap. Must
    never raise: timing is telemetry, not behavior."""
    log = os.environ.get("OPENBEAST_DIAG_TIMING_LOG", "").strip()
    if not log:
        return
    try:
        with open(log, "a") as f:
            f.write(json.dumps({"ts": time.time(), "lang": lang,
                                "ms": round(ms, 1), "status": status}) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# diag2 (2026-09-11) — diagnostics-quality bundle. One cache era
# (run_eval.diagnostics_flag → `diag2-<fp>`), four fixes shipped together:
#   1. zig reference-trace strip (_zig_compact) — the `referenced by:` block
#      and std/start.zig shim frames were 56% of the payload; `note:` lines
#      that carry declared-here / parameter / signature info are KEPT.
#   2. "did you mean" for zig unknown-member errors (_zig_extras), sourced
#      from the INSTALLED stdlib only, hybrid prefix+difflib matcher.
#   3. curated fix-hint table for the ArrayList-arity / std.Io idioms
#      (_ZIG_FIX_HINTS), every row compile-verified on zig 0.16.
#   4. anchored error count in the footer (_diag_count_errors).
# All parsing is pure (no zig needed) — see tests/test_diagnostics.py.
# The total appended block stays inside _DIAG_MAX_LINES/_DIAG_MAX_BYTES:
# extras (hints) are budgeted FIRST so the remedy is never the part that
# truncation eats (the banked NTT failure of the 2026-09-10 A/B).
# ---------------------------------------------------------------------------

_DIAG_EXTRA_BYTES = 800          # hints share the 2 KB block, never exceed it
_DIAG_MAX_HINTS = 2              # fix-hint rows per block (table order)
_DIAG_MAX_SUGGEST = 3            # unknown-member errors that get suggestions
_DIAG_LOC_RE = re.compile(r"^(\S+?):(\d+):(\d+):\s+(error|note|warning):\s?(.*)$")
_DIAG_CARET_RE = re.compile(r"^\s*[~^]+\s*$")
# Frames from these std files are compiler plumbing, never the user's bug.
_ZIG_SHIM_PATHS = ("/std/start.zig", "/compiler_rt/", "/std/std.zig")

# Per-language anchored error-line shapes. The naive `"error" in line`
# over-counted (rustc's "aborting due to N previous errors", zig's
# "N reference(s) hidden" — no; but `error` inside source snippets/notes —
# yes). zig/gcc emit `file:line:col: error:`; the others have their own
# anchored forms because they never print that shape at all (an anchored
# zig regex alone would have relabelled every rustc/python failure
# "warnings").
_DIAG_ERROR_RES = {
    "zig": re.compile(r"^\S+:\d+:\d+:\s+error:", re.M),
    "c": re.compile(r"^\S+:\d+:\d+:\s+(?:fatal )?error:", re.M),
    "c++": re.compile(r"^\S+:\d+:\d+:\s+(?:fatal )?error:", re.M),
    "rust": re.compile(r"^error(?:\[E\d+\])?:(?! aborting due to)", re.M),
    "go": re.compile(r"^\S+:\d+:\d+: ", re.M),
    "python": re.compile(r"^\w*Error:", re.M),
    "shell": re.compile(r"\(error\):", re.M),
}

# CURATED — verified 2026-09-11 on zig 0.16.0 (this box): for every row the
# OLD form fails and the HINTED form compiles under
# `zig build-exe -fno-emit-bin`. Fixtures: tests/fixtures/zig/stale_*.zig
# (old, must fail) and tests/fixtures/zig/fixed_*.zig (hinted, must pass);
# tests/test_diagnostics.py::test_zig_hint_table_verified runs them when
# zig is installed. Re-verify + re-date on any zig upgrade. ≤5 rows —
# this is a rename map, not documentation (roadmap R3).
_ZIG_FIX_HINTS: tuple[tuple[str, str], ...] = (
    # 1. ArrayList managed → unmanaged (fixed_arraylist_unmanaged.zig)
    (r"'array_list\.[^']*' has no member named 'init'",
     "std.ArrayList is unmanaged in zig 0.16: `var list: std.ArrayList(T) = .empty; "
     "defer list.deinit(allocator); try list.append(allocator, item);` "
     "(there is no .init(allocator) / .deinit())"),
    # 2. ArrayList method arity (fixed_arraylist_unmanaged.zig)
    (r"expected \d+ argument\(s\), found \d+\n(?:[^\n]*\n){0,2}[^\n]*/std/array_list\.zig:\d+:\d+: note: function declared here",
     "zig 0.16 ArrayList methods take the allocator first: `append(allocator, item)`, "
     "`appendSlice(allocator, items)`, `deinit(allocator)`, `toOwnedSlice(allocator)`"),
    # 3. std.io / std.fs.File are gone (fixed_io_writer_init.zig)
    (r"struct 'std' has no member named 'io'|getStdOut|getStdErr|struct 'fs' has no member named 'File'",
     "zig 0.16 has no std.io / std.fs.File: declare `pub fn main(init: std.process.Init) !void`, then "
     "`var buf: [1024]u8 = undefined; var w = std.Io.File.stdout().writer(init.io, &buf); "
     "const out = &w.interface; try out.print(\"..\", .{..}); try out.flush();`"),
    # 4. File.writer/reader arity (fixed_io_writer_init.zig, fixed_io_threaded.zig)
    (r"expected \d+ argument\(s\), found \d+\n(?:[^\n]*\n){0,2}[^\n]*/std/Io/File\.zig:\d+:\d+: note: function declared here",
     "zig 0.16 File.writer/File.reader take (io, buffer): `.writer(init.io, &buf)` and use "
     "`&w.interface`; without an Init param: `var t: std.Io.Threaded = .init_single_threaded; "
     "const io = t.io();`"),
    # 5. stdin line reading (fixed_io_reader_init.zig)
    (r"getStdIn|readUntilDelimiter|streamUntilDelimiter|named '\w+' in 'Io\.Reader'",
     "zig 0.16 stdin lines: `var r = std.Io.File.stdin().reader(init.io, &rbuf); const in = &r.interface; "
     "while (in.takeDelimiterExclusive('\\n')) |line| { .. } else |err| switch (err) "
     "{ error.EndOfStream => {}, else => return err }`"),
)

_ZIG_MEMBER_RES = (
    # root source file struct 'mem' has no member named 'trimRight'
    # struct 'array_list.Aligned(i32,null)' has no member named 'init'
    re.compile(r"(?:root source file )?(?:struct|enum|union|opaque|type) '(?P<t>[^']+)' "
               r"has no member named '(?P<m>\w+)'"),
    # no field or member function named 'writeAllz' in 'Io.Writer'
    re.compile(r"no (?:field or member function|member function|member|field) named "
               r"'(?P<m>\w+)' in '(?P<t>[^']+)'"),
)
_ZIG_DECL_PUB_RE = re.compile(
    r"^\s*pub\s+(?:(?:inline|extern|export|threadlocal)\s+)*(?:fn|const|var)\s+(\w+)", re.M)
_ZIG_DECL_ANY_RE = re.compile(
    r"^\s*(?:pub\s+)?(?:(?:inline|extern|export|threadlocal)\s+)*(?:fn|const|var)\s+(\w+)", re.M)
_ZIG_READ_CAP = 4 * 1024 * 1024


def _diag_count_errors(lang: str, text: str) -> int:
    """Anchored error count for the footer (diag2 item 4)."""
    rx = _DIAG_ERROR_RES.get(lang)
    return len(rx.findall(text)) if rx and text else 0


def _zig_compact(out: str) -> str:
    """diag2 item 1 — drop zig reference-trace noise, keep signal.

    Dropped: `referenced by:` blocks (their indented frames + the
    "N reference(s) hidden" line); `note:` frames located in std shim
    files (start.zig, compiler_rt, std.zig snippet); the source+caret
    companion lines of a module-level `:1:1: note: struct declared here`
    (that snippet is just the module's first line). Kept verbatim: every
    error line with its snippet+caret, and every other note — "function
    declared here" + its signature line is the single most useful thing
    zig prints for the stale-API class."""
    lines = out.splitlines()
    res: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip() == "referenced by:":
            i += 1
            while i < len(lines) and lines[i][:1] in (" ", "\t"):
                i += 1
            continue
        m = _DIAG_LOC_RE.match(ln)
        if not m:
            res.append(ln)
            i += 1
            continue
        path, row, col, kind, _msg = m.groups()
        # companions = source snippet + caret (exactly when the 2nd is a caret)
        comp: list[str] = []
        j = i + 1
        while (j < len(lines) and len(comp) < 2
               and not _DIAG_LOC_RE.match(lines[j])
               and lines[j].strip() != "referenced by:"):
            comp.append(lines[j])
            j += 1
        if not (len(comp) == 2 and _DIAG_CARET_RE.match(comp[1])):
            comp = []
        shim = kind == "note" and any(s in path for s in _ZIG_SHIM_PATHS)
        module_decl = kind == "note" and row == "1" and col == "1"
        if shim and "declared here" not in _msg:
            pass  # plumbing frame: drop line + companions
        elif shim or module_decl:
            res.append(ln)  # keep the note, drop the useless snippet
        else:
            res.append(ln)
            res.extend(comp)
        i += 1 + len(comp)
    while res and not res[-1].strip():
        res.pop()
    return "\n".join(res)


@functools.lru_cache(maxsize=1)
def _zig_std_dir() -> str | None:
    """`zig env` std_dir, resolved once per process (scrubbed env, bounded)."""
    zig = shutil.which("zig")
    if not zig:
        return None
    try:
        out = subprocess.run([zig, "env"], capture_output=True, text=True,
                             timeout=10, env=_scrubbed_env()).stdout
    except Exception:
        return None
    m = re.search(r'std_dir"?\s*[=:]\s*"([^"]+)"', out)
    if m and os.path.isdir(m.group(1)):
        return m.group(1)
    m = re.search(r'lib_dir"?\s*[=:]\s*"([^"]+)"', out)
    if m and os.path.isdir(os.path.join(m.group(1), "std")):
        return os.path.join(m.group(1), "std")
    return None


@functools.lru_cache(maxsize=64)
def _zig_decl_names(file_path: str, region: str | None, pub_only: bool,
                    _mtime: float) -> tuple[str, ...]:
    """Declared names in a zig file (or inside one container decl).

    `region` narrows to the body of `pub fn NAME(` / `const NAME = struct`
    — the block from that line to the first `}` at the same indent — so
    `array_list.Aligned(...)` suggests initCapacity, not the whole module.
    Falls back to the whole file when the region isn't found or is empty.
    Cached per (path, mtime); reads are capped at _ZIG_READ_CAP."""
    try:
        with open(file_path, errors="replace") as f:
            src = f.read(_ZIG_READ_CAP)
    except OSError:
        return ()
    rx = _ZIG_DECL_PUB_RE if pub_only else _ZIG_DECL_ANY_RE
    if region:
        start = re.search(
            r"^([ \t]*)(?:pub\s+)?(?:fn\s+%s\s*\(|const\s+%s\s*=)" % (re.escape(region), re.escape(region)),
            src, re.M)
        if start:
            indent = start.group(1)
            end = re.compile(r"^%s\}" % re.escape(indent), re.M).search(src, start.end())
            body = src[start.end():end.start() if end else len(src)]
            names = tuple(dict.fromkeys(rx.findall(body)))
            if names:
                return names
    return tuple(dict.fromkeys(rx.findall(src)))


def _zig_resolve_type(type_str: str, std_dir: str | None,
                      checked_path: str) -> tuple[str, str, str | None, bool] | None:
    """Map a zig type spelling from an error message to
    (display_name, file, region, pub_only) — the file to mine for
    candidates. Stdlib types resolve under std_dir only (INSTALLED std,
    never the internet); the checked file itself when the type's root
    segment is that file's module name."""
    base = type_str.split("(", 1)[0].strip()
    segs = [s for s in base.split(".") if s]
    if not segs:
        return None
    stem = os.path.splitext(os.path.basename(checked_path))[0]
    if checked_path and segs[0] == stem:
        return (".".join(segs), checked_path, segs[1] if len(segs) > 1 else None, False)
    if not std_dir:
        return None
    if segs == ["std"]:
        return ("std", os.path.join(std_dir, "std.zig"), None, True)
    # longest file path first: Io.File → std/Io/File.zig; array_list.Aligned
    # → std/array_list.zig + region Aligned.
    for k in range(len(segs), 0, -1):
        cand = os.path.join(std_dir, *segs[:k]) + ".zig"
        if os.path.isfile(cand):
            region = segs[k] if k < len(segs) else None
            return ("std." + ".".join(segs), cand, region, True)
    return None


def _did_you_mean(name: str, candidates, n: int = _DIAG_MAX_SUGGEST) -> list[str]:
    """diag2 item 2 — hybrid matcher. Tiers: exact (case-insensitive) >
    camelCase-stem prefix > substring > shared camel/snake token > difflib
    close match. difflib alone misses trimRight→trimEnd (ratio 0.62 but
    outranked by noise); the stem tier is what makes that case work."""
    low = name.lower()
    stem_m = re.match(r"[a-z]+|[A-Z][a-z]+|[A-Z]+", name)
    stem = stem_m.group(0).lower() if stem_m else low
    toks = {t.lower() for t in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name) if len(t) >= 3}
    scored: dict[str, float] = {}
    for c in set(candidates):
        if c == name:
            continue
        cl = c.lower()
        r = difflib.SequenceMatcher(None, low, cl).ratio()
        if cl == low:
            score = 4.0
        elif len(stem) >= 3 and cl.startswith(stem):
            score = 3.0 + r
        elif len(low) >= 4 and len(cl) >= 4 and (low in cl or cl in low):
            score = 2.0 + r
        else:
            ctoks = {t.lower() for t in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", c) if len(t) >= 3}
            shared = toks & ctoks
            if shared:
                score = 1.0 + len(shared) / len(toks | ctoks) + 0.5 * r
            elif r >= 0.6:
                score = r
            else:
                continue
        scored[c] = score
    if not scored:
        return []
    best = max(scored.values())
    # Precision gate: a wrong suggestion sends the model chasing. Emit only
    # on a strong tier (exact/prefix/substring) or a confident typo
    # (difflib ≥ 0.75, e.g. incremnt→increment). Token-overlap and weak
    # difflib hits only ever FILL positions behind a strong lead.
    if best < 2.0 and not (0.75 <= best < 1.0):
        return []
    return sorted(scored, key=lambda c: (-scored[c], len(c), c))[:n]


def _zig_extras(body: str, checked_path: str = "",
                std_dir: str | None = None) -> list[str]:
    """diag2 items 2+3 — suggestion lines + curated fix hints for a
    (compacted) zig diagnostic text. Pure given std_dir; resolves the
    installed std lazily when not supplied."""
    extras: list[str] = []
    seen: set[tuple[str, str]] = set()
    for ln in body.splitlines():
        if len(seen) >= _DIAG_MAX_SUGGEST:
            break
        m = _DIAG_LOC_RE.match(ln)
        if not m or m.group(4) != "error":
            continue
        for rx in _ZIG_MEMBER_RES:
            mm = rx.search(m.group(5))
            if not mm:
                continue
            key = (mm.group("t"), mm.group("m"))
            if key in seen:
                break
            seen.add(key)
            if std_dir is None:
                std_dir = _zig_std_dir()
            res = _zig_resolve_type(key[0], std_dir, checked_path)
            if not res:
                break
            display, fpath, region, pub_only = res
            try:
                mtime = os.stat(fpath).st_mtime
            except OSError:
                break
            cands = _did_you_mean(key[1], _zig_decl_names(fpath, region, pub_only, mtime))
            if cands:
                extras.append(f"hint: '{key[1]}' is not in {display} — did you mean "
                              f"{', '.join(cands)}?")
            break
    n_hints = 0
    for pat, text in _ZIG_FIX_HINTS:
        if n_hints >= _DIAG_MAX_HINTS:
            break
        if re.search(pat, body):
            extras.append("fix: " + text)
            n_hints += 1
    return extras


def _diag_format(lang: str, rc: int, out: str | None, path: str = "") -> str:
    """Render one checker verdict — pure, testable without any toolchain.
    Extras (did-you-mean + fix hints, zig only) are budgeted before the
    body so truncation can never eat the remedy; the whole block stays
    within _DIAG_MAX_LINES / _DIAG_MAX_BYTES."""
    out = (out or "").strip()
    if rc == 0 and not out:
        return f"\ndiagnostics: OK ({lang})"
    body = out
    extras: list[str] = []
    if lang == "zig":
        body = _zig_compact(out)
        extras = _zig_extras(body, path)
    extra_txt = "\n".join(extras)[:_DIAG_EXTRA_BYTES]
    budget_lines = max(_DIAG_MAX_LINES - len(extras), 5)
    budget_bytes = max(_DIAG_MAX_BYTES - len(extra_txt), 512)
    block = "\n".join(body.splitlines()[:budget_lines])[:budget_bytes]
    n = _diag_count_errors(lang, out)
    shown = _diag_count_errors(lang, block)
    if n:
        label = f"{n} error{'s' if n != 1 else ''}"
        if shown < n:
            label += f" ({shown} shown)"
    elif rc == 0:
        label = "warnings"
    else:
        label = f"errors (rc={rc})"
    if extra_txt:
        block += "\n" + extra_txt
    return f"\n── diagnostics ({lang}) ──\n{block}\n── {label} ──"


def _run_diagnostics(path: str) -> str:
    """Checker verdict for `path`, formatted for appending to a tool result.
    Returns "" when diagnostics are off, no checker applies, or the checker
    itself breaks — a broken checker must never fail a write."""
    if not diagnostics_enabled():
        return ""
    checker = None
    t0 = time.monotonic()
    lang = "?"
    try:
        checker = _diag_checker(path)
        if checker is None:
            return ""
        lang, cmd, extra_env, scratches = checker
        wrapper = os.environ.get("OPENBEAST_BASH_WRAPPER", "").strip()
        if wrapper:
            cmd = f"{wrapper} /bin/sh -c {shlex.quote(cmd)}"
        env = _scrubbed_env()
        env.update(extra_env)
        if not _DIAG_SLOTS.acquire(timeout=5):
            _diag_log_timing(lang, (time.monotonic() - t0) * 1000, "busy")
            return "\ndiagnostics: unavailable (busy)"
        try:
            rc, out = run_reaped(cmd, _DIAG_TIMEOUT, as_limit=_DIAG_AS_LIMIT, env=env)
        finally:
            _DIAG_SLOTS.release()
        _diag_log_timing(lang, (time.monotonic() - t0) * 1000, "ok")
        return _diag_format(lang, rc, out, path)
    except subprocess.TimeoutExpired:
        _diag_log_timing(lang, (time.monotonic() - t0) * 1000, "timeout")
        return "\ndiagnostics: unavailable (timeout)"
    except Exception as e:
        _diag_log_timing(lang, (time.monotonic() - t0) * 1000, type(e).__name__)
        return f"\ndiagnostics: unavailable ({type(e).__name__})"
    finally:
        if checker:
            for d in checker[3]:
                shutil.rmtree(d, ignore_errors=True)


def write_file(path: str, content: str) -> str:
    """Write content to a file, creating directories if needed."""
    try:
        # Resolve first (anchors relative paths to the private workspace), then
        # guard and write the SAME path — a symlink swapped in after the check
        # can't redirect the write.
        path = _resolve(path)
        blocked = _guard_write_path(path)
        if blocked:
            return blocked
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        _manifest_log("write", path, len(content))
        return (f"Wrote {len(content)} bytes to {path}"
                + _path_guard_note(path) + _run_diagnostics(path))
    except Exception as e:
        return f"Error: {e}"


def list_files(directory: str = ".", pattern: str = "**/*") -> str:
    """List files matching a glob pattern."""
    try:
        directory = _resolve(directory)
        matches = sorted(glob.glob(os.path.join(directory, pattern), recursive=True))
        files = [m for m in matches if os.path.isfile(m)]
        if not files:
            return f"No files matching '{pattern}' in {directory}"
        result = "\n".join(files[:200])
        if len(files) > 200:
            result += f"\n... and {len(files) - 200} more"
        return result
    except Exception as e:
        return f"Error: {e}"


def grep(pattern: str, path: str = ".", file_glob: str = "",
         context_lines: int = 0, ignore_case: bool = False,
         max_results: int = 500) -> str:
    """Search file contents for a regex pattern (POSIX ERE).

    2026-09-10 hardening: the pattern rides behind `-e ... --` so a
    leading-dash pattern can never be parsed as a grep option (live-
    verified: a `-v` pattern flipped invert-match and recursed the whole
    tree); binary files and VCS/build dirs are skipped; the search path
    goes through _resolve() so `~` works; and the sandbox wrapper +
    scrubbed env apply — this was the one exec path that bypassed both."""
    try:
        path = _resolve(path)
        flags = ["-rn", "-I", "-H",
                 "--exclude-dir=.git", "--exclude-dir=node_modules",
                 "--exclude-dir=__pycache__", "--exclude-dir=build",
                 "--exclude-dir=.cache"]
        if ignore_case:
            flags.append("-i")
        if context_lines:
            flags.append(f"-C{max(0, min(int(context_lines), 10))}")
        if file_glob:
            flags.append(f"--include={shlex.quote(file_glob)}")
        cmd = (f"grep {' '.join(flags)} -E -e {shlex.quote(pattern)} -- "
               f"{shlex.quote(path)}")
        wrapper = os.environ.get("OPENBEAST_BASH_WRAPPER", "").strip()
        if wrapper:
            cmd = f"{wrapper} /bin/sh -c {shlex.quote(cmd)}"
        _, output = run_reaped(cmd, 30, cwd=_base_dir(), env=_scrubbed_env())
        # "No matches" means no path:lineno: rows — grep may still have
        # printed warnings (e.g. "stray \ before d"), which we keep visible.
        has_match = re.search(r"^[^\n:]+:\d+:", output or "", re.M)
        if not has_match:
            hint = ""
            if re.search(r"\\d|\\w|\\s|\(\?", pattern):
                hint = ("\n(note: this tool speaks POSIX ERE — \\d/\\w/\\s and "
                        "lookarounds are PCRE and match literally; use [0-9], "
                        "[A-Za-z0-9_], [[:space:]])")
            warn = ("\n" + output.strip()) if output.strip() else ""
            return "(no matches)" + warn + hint
        lines = output.splitlines()
        cap = max(1, min(int(max_results), 2000))
        if len(lines) > cap:
            output = "\n".join(lines[:cap]) + (
                f"\n[... {len(lines) - cap} more matching lines elided — "
                f"narrow the pattern, path, or file_glob ...]")
        return output[:50_000]
    except subprocess.TimeoutExpired:
        return ("Error: grep timed out after 30s — narrow the path or add "
                "file_glob (VCS/build dirs are already skipped)")
    except Exception as e:
        return f"Error: {e}"


def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Replace an exact string in a file with new content."""
    try:
        # Resolve first, then guard/operate on the SAME path (see write_file).
        path = _resolve(path)
        blocked = _guard_write_path(path)
        if blocked:
            return blocked
        if not os.path.isfile(path):
            return f"Error: file not found: {path}"

        with open(path, "r") as f:
            content = f.read()

        if not old_string:
            return "Error: old_string must not be empty"

        if old_string == new_string:
            return "Error: old_string and new_string are identical — nothing to change"

        count = content.count(old_string)
        if count == 0:
            lines = old_string.split("\n")
            if len(lines) > 1 and content.find(lines[0]) != -1:
                return (
                    f"Error: exact match not found in {path}. "
                    f"The first line was found but the full multi-line string didn't match. "
                    f"Check whitespace and indentation."
                )
            return f"Error: old_string not found in {path}"

        if count > 1 and not replace_all:
            return (
                f"Error: old_string appears {count} times in {path}. "
                f"Include more surrounding context to make it unique, "
                f"or set replace_all=true to replace all occurrences."
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        with open(path, "w") as f:
            f.write(new_content)
        _manifest_log("edit", path, len(new_content))

        change_line = content[:content.index(old_string)].count("\n") + 1
        old_lines = old_string.count("\n") + 1
        new_lines = new_string.count("\n") + 1

        if replace_all and count > 1:
            return (f"Replaced {count} occurrences in {path} "
                    f"({len(old_string)} → {len(new_string)} chars each)"
                    + _path_guard_note(path) + _run_diagnostics(path))
        else:
            return (
                f"Edited {path} at line {change_line}: "
                f"replaced {old_lines} line{'s' if old_lines != 1 else ''} "
                f"with {new_lines} line{'s' if new_lines != 1 else ''}"
                + _path_guard_note(path) + _run_diagnostics(path)
            )
    except Exception as e:
        return f"Error: {e}"


# Tailscale addresses: CGNAT v4 (100.64.0.0/10) and the default v6 ULA range
# (fd7a:115c:a1e0::/48). CPython's is_private classification of the CGNAT range
# changed in 3.12.4/3.11.9 (gh-113171), so we pin the semantics explicitly
# instead of inheriting stdlib drift: blocked by default on every interpreter,
# opt-in via OPENBEAST_FETCH_ALLOW_TAILNET for clients that fetch from tailnet
# hosts — and the opt-in must cover BOTH families, since MagicDNS returns A and
# AAAA. web_search deliberately bypasses this guard entirely (SEARXNG_URL may
# legitimately be a tailnet/loopback service).
_TS_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_TS_ULA = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def _unwrap_v6(ip):
    """Return the embedded IPv4 address for v4-in-v6 forms, else `ip`.

    ::ffff:100.64.1.2 and 2002::/16 / 2001::/32 tunnels otherwise sail past a
    v4-only range check while still dialing the v4 target.
    """
    if ip.version == 6:
        for attr in ("ipv4_mapped", "sixtofour", "teredo"):
            embedded = getattr(ip, attr, None)
            if embedded is not None:
                # teredo yields (server, client) — the client is the endpoint.
                return embedded[1] if isinstance(embedded, tuple) else embedded
    return ip


def _vet_addr(addr: str) -> str | None:
    """Refusal reason if `addr` is a non-public IP, else None."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])  # strip v6 zone id
    except ValueError:
        return f"unparseable address '{addr}'"
    ip = _unwrap_v6(ip)
    is_tailnet = ((ip.version == 4 and ip in _TS_CGNAT)
                  or (ip.version == 6 and ip in _TS_ULA))
    if is_tailnet:
        if os.environ.get("OPENBEAST_FETCH_ALLOW_TAILNET", "").lower() in ("1", "true", "yes"):
            return None
        return f"tailnet address {ip} (set OPENBEAST_FETCH_ALLOW_TAILNET=1 to allow)"
    if (ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_unspecified or ip.is_multicast or ip.is_reserved):
        return f"non-public address {ip}"
    return None


def _resolve_vetted(host: str, port, scheme: str):
    """Resolve `host` and vet EVERY result. Returns (vetted_ips, None) when
    all resolved addresses are public, else (None, reason). Returning the
    exact IPs it validated is what lets the caller CONNECT to one of them —
    closing the DNS-rebinding window where a second resolution could hand
    back 127.0.0.1 after the guard saw a public address."""
    try:
        infos = socket.getaddrinfo(
            host, port or (443 if scheme == "https" else 80),
            proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return None, f"could not resolve host '{host}': {e}"
    if not infos:
        return None, f"host '{host}' resolved to no addresses"
    ips = []
    for info in infos:
        addr = info[4][0]
        reason = _vet_addr(addr)
        if reason:
            return None, f"host '{host}' resolves to {reason}"
        ips.append(addr)
    return ips, None


def _fetch_url_blocked(url: str) -> str | None:
    """SSRF guard for fetch(): return a human-readable refusal reason, or
    None if the URL is safe. Scheme allowlist (http/https only) + resolve
    the hostname and refuse if ANY address is loopback/private/link-local/
    reserved. Blocks http://127.0.0.1:3001, http://169.254.169.254, and
    public names that resolve privately. The authoritative check is repeated
    atomically at connect time (see _PinnedHTTP*Handler) so a DNS flip
    between this call and the socket can't sneak through — this is the
    early, friendly-error copy."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        return f"scheme '{parsed.scheme}' not allowed (http/https only)"
    if not parsed.hostname:
        return "URL has no hostname"
    _, reason = _resolve_vetted(parsed.hostname, parsed.port, parsed.scheme.lower())
    return reason


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that dials a PRE-VETTED IP instead of re-resolving the
    hostname — the IP the SSRF guard approved is the exact IP we connect to."""
    def __init__(self, *a, pinned_ip=None, **kw):
        super().__init__(*a, **kw)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Same pin for TLS. Crucially keeps server_hostname = the ORIGINAL host
    so SNI and certificate validation still check the real name, not the IP."""
    def __init__(self, *a, pinned_ip=None, **kw):
        super().__init__(*a, **kw)
        self._pinned_ip = pinned_ip

    def connect(self):
        sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            sock = self.sock
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _pinned_open(handler, req, conn_class, scheme):
    """Resolve + vet + pin, atomically, for THIS request (initial or any
    redirect hop — urllib re-enters the handler per hop, so every hop is
    independently vetted against the address it actually dials)."""
    parsed = urllib.parse.urlparse(req.full_url)
    ips, reason = _resolve_vetted(parsed.hostname, parsed.port, scheme)
    if reason:
        raise urllib.error.URLError(f"fetch blocked: {reason}")
    return handler.do_open(conn_class, req, pinned_ip=ips[0])


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return _pinned_open(self, req, _PinnedHTTPConnection, "http")


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return _pinned_open(self, req, _PinnedHTTPSConnection, "https")


class _FetchRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject a bad redirect target EARLY with a clear message (defense in
    depth + friendly error). The atomic resolve-vet-pin still runs in the
    pinned handlers when urllib re-opens the redirected request, so this
    early check being one resolution ahead can't be exploited."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = _fetch_url_blocked(newurl)
        if reason:
            raise urllib.error.URLError(
                f"fetch blocked: redirect to {newurl}: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Default HTTP/HTTPS handlers replaced by the pinned variants (build_opener
# swaps same-type handlers), so no request path re-resolves post-guard.
_fetch_opener = urllib.request.build_opener(
    _PinnedHTTPHandler(), _PinnedHTTPSHandler(), _FetchRedirectHandler)


def fetch(url: str, max_length: int = 50_000) -> str:
    """Fetch content from a URL and return as text.

    Deliberately refuses local/private targets for EVERYONE (admin included —
    defense in depth, see docs/RBAC_PLAN.md): http/https schemes only, and any
    hostname resolving to loopback/private/link-local/reserved space is
    blocked, at request time and again on every redirect hop.
    """
    reason = _fetch_url_blocked(url)
    if reason:
        return f"Error: fetch blocked: {reason}"
    # max_length is model-controlled; without a ceiling, max_length*4 below
    # becomes an attempted multi-GB read into memory.
    max_length = max(1, min(int(max_length), 2_000_000))
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; local-agent/1.0)",
                "Accept": "text/html,application/json,text/plain,*/*",
            },
        )
        with _fetch_opener.open(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()
                # The header is server-controlled; a bogus charset name would
                # raise LookupError and fail the whole fetch.
                try:
                    codecs.lookup(charset)
                except LookupError:
                    charset = "utf-8"
            raw_bytes = resp.read(max_length * 4)
            text = raw_bytes.decode(charset, errors="replace")

        if "html" in content_type.lower() or text.strip()[:100].lower().startswith(("<!doctype", "<html")):
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<(br|hr|/p|/div|/h[1-6]|/li|/tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = html.unescape(text)
            text = re.sub(r"[^\S\n]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text.strip()

        if len(text) > max_length:
            text = text[:max_length] + f"\n\n[truncated at {max_length} chars — {len(raw_bytes)} bytes fetched]"

        return text if text else "(empty response)"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(2000).decode("utf-8", errors="replace")
        except Exception:
            pass
        return f"HTTP {e.code} {e.reason}" + (f"\n{body}" if body else "")
    except urllib.error.URLError as e:
        return f"URL error: {e.reason}"
    except Exception as e:
        return f"Error: {e}"


def web_search(query: str, max_results: int = 10) -> str:
    """Search the web using the local SearXNG instance."""
    searxng_url = os.environ.get("SEARXNG_URL", "http://localhost:8888")
    try:
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "categories": "general",
        })
        url = f"{searxng_url}/search?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "local-agent/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError:
        return (
            "Error: SearXNG is not running. Start it with:\n"
            "  docker run -d -p 8888:8080 -e SEARXNG_BASE_URL=http://localhost:8888/ searxng/searxng\n"
            "Or set SEARXNG_URL env var if running on a different port."
        )
    except Exception as e:
        return f"Error: {e}"

    results = data.get("results", [])[:max_results]
    if not results:
        return f"No results for: {query}"

    lines = [f"Web search: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        url = r.get("url", "")
        snippet = r.get("content", "")[:200]
        lines.append(f"{i}. {title}")
        lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")
    return "\n".join(lines)


def _task_expected_paths() -> list[str]:
    """Expected file paths for the current task (R1 path guard, 2026-09-10).

    The eval harness extracts /tmp/eval* paths from the task spec and
    passes them via OPENBEAST_TASK_PATHS (JSON list). Empty/absent = the
    guard is inert (production agents, non-eval use). Measured basis: 25%
    of the diagnostics-A/B campaign's zig failures were FileNotFound at
    validation — the model wrote correct code somewhere validation never
    looks — identical with diagnostics on or off."""
    raw = os.environ.get("OPENBEAST_TASK_PATHS", "").strip()
    if not raw:
        return []
    try:
        paths = json.loads(raw)
        return [p for p in paths if isinstance(p, str)]
    except json.JSONDecodeError:
        return []


def _path_guard_note(written: str) -> str:
    """Warning to append when a write lands at the wrong place: same
    basename as an expected task file, different location."""
    expected = _task_expected_paths()
    if not expected:
        return ""
    wp = os.path.realpath(written)
    if any(wp == os.path.realpath(e) for e in expected):
        return ""
    base = os.path.basename(wp)
    hits = [e for e in expected if os.path.basename(e) == base]
    if hits:
        return (f"\n⚠ path check: the task expects this file at {hits[0]} "
                f"— you wrote {written}. Validation will only look at the "
                f"expected path.")
    return ""


def task_done(summary: str) -> str:
    """Signal that the task is complete."""
    # R1 path guard: refuse completion while expected task files (with a
    # file extension — extensionless paths are usually build artifacts the
    # validator compiles itself) do not exist. Catches both never-wrote
    # and wrote-elsewhere with iterations left to fix it.
    expected = _task_expected_paths()
    missing = [e for e in expected
               if "." in os.path.basename(e) and not os.path.exists(e)]
    if missing:
        return ("NOT DONE — the task expects these files, which do not "
                "exist yet: " + ", ".join(sorted(missing)) +
                ". Create them at exactly these paths, then call task_done "
                "again.")
    return f"TASK_DONE: {summary}"


# ---------------------------------------------------------------------------
# Tool registry — single source of truth
# ---------------------------------------------------------------------------

# Each entry: (handler_fn, openai_schema_dict).
# TOOL_SCHEMAS and TOOL_HANDLERS are derived from this list, so they can never
# drift out of sync.
_TOOL_REGISTRY: list[tuple[Any, dict]] = [
    (
        bash,
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run a shell command. Use for building, testing, git operations, installing packages, or any system task.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The shell command to execute"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)", "default": 120},
                    },
                    "required": ["command"],
                },
            },
        },
    ),
    (
        read_file,
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read lines from a file. Returns numbered lines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file"},
                        "offset": {"type": "integer", "description": "Line offset to start from (0-indexed)", "default": 0},
                        "limit": {"type": "integer", "description": "Max lines to read", "default": 500},
                    },
                    "required": ["path"],
                },
            },
        },
    ),
    (
        write_file,
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file. Creates directories if needed. Overwrites existing files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to write to"},
                        "content": {"type": "string", "description": "File content"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    ),
    (
        list_files,
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files matching a glob pattern in a directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "Directory to search", "default": "."},
                        "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')", "default": "**/*"},
                    },
                },
            },
        },
    ),
    (
        grep,
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": "Search file contents for a regex pattern (POSIX ERE — use [0-9] not \\d, no lookaheads). Returns matching lines as path:lineno:text (line numbers are 1-based, same as read_file display). Skips binary files and .git/build dirs.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "POSIX ERE regex to search for"},
                        "path": {"type": "string", "description": "File or directory to search in", "default": "."},
                        "file_glob": {"type": "string", "description": "Filter files by glob (e.g. '*.py')"},
                        "context_lines": {"type": "integer", "description": "Lines of context around each match (0-10)", "default": 0},
                        "ignore_case": {"type": "boolean", "description": "Case-insensitive search", "default": False},
                        "max_results": {"type": "integer", "description": "Cap on matching lines returned (default 500)", "default": 500},
                    },
                    "required": ["pattern"],
                },
            },
        },
    ),
    (
        edit_file,
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Replace an exact string in a file with new content. Use this instead of write_file when modifying existing files — it's safer and more precise. The old_string must appear exactly once unless replace_all is true. To insert text, include surrounding context in old_string and add new text within that context in new_string.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file to edit"},
                        "old_string": {"type": "string", "description": "The exact text to find (must be unique in the file)"},
                        "new_string": {"type": "string", "description": "The replacement text"},
                        "replace_all": {"type": "boolean", "description": "Replace all occurrences instead of requiring uniqueness", "default": False},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
    ),
    (
        fetch,
        {
            "type": "function",
            "function": {
                "name": "fetch",
                "description": "Fetch content from a URL and return it as text. HTML pages are cleaned (scripts/styles removed, tags stripped) to return readable text. JSON and plain text are returned as-is.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The URL to fetch (http or https)"},
                        "max_length": {"type": "integer", "description": "Maximum characters to return (default 50000)", "default": 50000},
                    },
                    "required": ["url"],
                },
            },
        },
    ),
    (
        web_search,
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web using the local SearXNG instance. Returns titles, URLs, and snippets. Requires SearXNG running on port 8888.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query string"},
                        "max_results": {"type": "integer", "description": "Maximum results to return (default 10)", "default": 10},
                    },
                    "required": ["query"],
                },
            },
        },
    ),
    (
        task_done,
        {
            "type": "function",
            "function": {
                "name": "task_done",
                "description": "Call this when the task is fully complete. Provide a summary of what was accomplished.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Summary of what was accomplished"},
                    },
                    "required": ["summary"],
                },
            },
        },
    ),
]

# Derived exports — always in sync with _TOOL_REGISTRY.
TOOL_SCHEMAS = [schema for _, schema in _TOOL_REGISTRY]

TOOL_HANDLERS: dict[str, Any] = {}
for _fn, _schema in _TOOL_REGISTRY:
    _name = _schema["function"]["name"]
    TOOL_HANDLERS[_name] = _fn  # runner calls handler(args_dict) directly
