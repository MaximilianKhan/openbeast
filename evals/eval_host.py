"""The eval harness host's toolchains, seen from the harness (scripts/eval-host.sh).

Three jobs, all before a LIVE run puts load on a model:

  ensure_host()        run `eval-host.sh check`; when it fails, `setup` (unless
                       --no-provision / OPENBEAST_EVAL_NO_PROVISION=1); then put
                       the prefix's tools first on PATH, and re-exec under the
                       eval venv's python when this process is not already it.
                       run_eval.main, profile.main and benchmark_all.main call it,
                       BEFORE run_eval pins the validator toolchain
                       (vlib.pin_toolchain resolves on the PATH set here).
  interpreter_problem()  can this interpreter start an agent at all: the
                       stdlib, then the packages the runner imports. run_eval
                       refuses a live run on a broken one instead of scoring
                       every unit as a FAIL.
  interpreter_startup_failure()  an agent that died in its first seconds
                       with an interpreter-startup error: run_eval records the
                       row as `interpreter_error` (an infra error: never cached,
                       never scored) and re-checks the interpreter.

Why (docs/HARNESS_NOTES.md, 2026-11-16): the v5 harness ran from a venv and
toolchains hand-placed in a session scratch directory. Something rewrote the
venv's python symlink mid-run, and 22 units died at interpreter startup
("Could not find platform independent libraries") and were scored as model
failures until a human noticed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(EVALS_DIR)
SCRIPT = os.path.join(REPO, "scripts", "eval-host.sh")

NO_PROVISION_ENV = "OPENBEAST_EVAL_NO_PROVISION"
REEXEC_ENV = "OPENBEAST_EVAL_REEXEC"
STATES = ("ok", "missing", "wrong", "system", "blocked")
UNFIXABLE = ("system", "blocked",      # setup cannot help: no sudo, no system dirs
             "outside")                 # nor an operator's OPENBEAST_EVAL_PY (review 1118)


def _lock_path() -> str:
    return os.environ.get("OPENBEAST_EVAL_LOCK") or os.path.join(EVALS_DIR, "toolchains.lock")


def lock_directive(name: str) -> str:
    """The value of `@name` in the toolchains lock ('' when absent)."""
    try:
        with open(_lock_path()) as f:
            for line in f:
                parts = line.split(None, 1)
                if parts and parts[0] == name:
                    return parts[1].strip() if len(parts) > 1 else ""
    except OSError:
        pass
    return ""


def _script(cmd: str, *args: str, prefix: str | None = None) -> list[str]:
    return ["bash", SCRIPT, cmd] + (["--prefix", prefix] if prefix else []) + list(args)


def check(prefix: str | None = None) -> tuple[int, list[tuple[str, str, str]]]:
    """(exit status, [(state, tool, detail)]) of `eval-host.sh check`."""
    try:
        r = subprocess.run(_script("check", prefix=prefix), capture_output=True, text=True,
                           timeout=600, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, [("system", "eval-host", f"scripts/eval-host.sh check did not run: {e}")]
    rows = []
    for line in r.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0] in STATES:
            rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    if r.returncode != 0 and not any(s != "ok" for s, _, _ in rows):
        rows.append(("system", "eval-host", (r.stderr or r.stdout).strip()[-300:]))
    return r.returncode, rows


def problems_of(rows) -> list[str]:
    return [f"{tool}: {state}: {detail}" for state, tool, detail in rows if state != "ok"]


def check_problems(prefix: str | None = None) -> list[str]:
    """The check's problems as strings ([] = ready). No installing."""
    rc, rows = check(prefix)
    probs = problems_of(rows)
    return probs if rc != 0 else []


def setup(prefix: str | None = None, out=None) -> int:
    """`eval-host.sh setup`, its output streamed to `out` (default stderr)."""
    out = out or sys.stderr
    try:
        p = subprocess.Popen(_script("setup", prefix=prefix), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL)
    except OSError as e:
        print(f"eval-host: setup did not start: {e}", file=out)
        return 1
    assert p.stdout is not None
    for line in p.stdout:
        out.write(line)
        out.flush()
    return p.wait()


def env_exports(prefix: str | None = None) -> dict:
    """`eval-host.sh env --json`: {"prefix", "path_prepend": [...], "env": {...}}."""
    r = subprocess.run(_script("env", "--json", prefix=prefix), capture_output=True, text=True,
                       timeout=60, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        return {"path_prepend": [], "env": {}}
    return json.loads(r.stdout)


def apply_env(exp: dict, environ=None) -> None:
    """Put the prefix's dirs first on PATH (each once) and set its variables."""
    environ = os.environ if environ is None else environ
    front = [d for d in exp.get("path_prepend") or [] if d]
    rest = [d for d in (environ.get("PATH") or "").split(os.pathsep) if d and d not in front]
    environ["PATH"] = os.pathsep.join(front + rest)
    for k, v in (exp.get("env") or {}).items():
        environ[k] = v


def _running_under(py: str) -> bool:
    venv = os.path.dirname(os.path.dirname(py))
    return os.path.realpath(sys.prefix) == os.path.realpath(venv)


def maybe_reexec(argv: list[str] | None, exp: dict, log=None) -> None:
    """Replace this process with the same command under OPENBEAST_EVAL_PY,
    unless it already runs there (or argv is None: an in-process caller)."""
    py = (exp.get("env") or {}).get("OPENBEAST_EVAL_PY")
    if not argv or not py or not os.access(py, os.X_OK) or _running_under(py):
        return
    if os.environ.get(REEXEC_ENV) == "1":
        (log or _err)(f"eval-host: already re-executed once and still not under {py}; "
                      f"continuing with {sys.executable}")
        return
    os.environ[REEXEC_ENV] = "1"
    (log or _err)(f"eval-host: re-executing under the eval venv: {py}")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(py, [py] + list(argv))


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def no_provision_requested(flag: bool = False) -> bool:
    return bool(flag) or os.environ.get(NO_PROVISION_ENV, "") not in ("", "0")


def ensure_host(argv: list[str] | None = None, no_provision: bool = False,
                prefix: str | None = None, log=None) -> list[str]:
    """Make this host's eval toolchains ready; [] when they are.

    no_provision: only check and report (nothing installed, PATH and the
    interpreter left alone). Otherwise a failed check runs setup once, the
    prefix's exports are applied to os.environ, and, when `argv` is given,
    the process re-execs under the eval venv's python."""
    log = log or _err
    rc, rows = check(prefix)
    if no_provision_requested(no_provision):
        probs = problems_of(rows) if rc != 0 else []
        if probs:
            log("eval-host: not ready, and provisioning is off (--no-provision / "
                f"{NO_PROVISION_ENV}=1):")
            for p in probs:
                log(f"  - {p}")
        return probs
    if rc != 0:
        fixable = [r for r in rows if r[0] not in UNFIXABLE and r[0] != "ok"]
        if fixable:
            log("eval-host: provisioning "
                + ", ".join(t for _, t, _ in fixable)
                + " (scripts/eval-host.sh setup; --no-provision to stop this)")
            setup(prefix)
            rc, rows = check(prefix)
    exp = env_exports(prefix)
    apply_env(exp)
    if rc != 0:
        return problems_of(rows) or ["scripts/eval-host.sh check failed"]
    maybe_reexec(argv, exp, log)
    return []


# --- the interpreter the agents start with ---------------------------------------

INTERP_RECHECK_S = 600.0
_INTERP_OK: dict[str, float] = {}


def interpreter_problem(python: str | None = None, force: bool = False) -> str | None:
    """None when `python` (default: this interpreter, which spawns every
    agent) imports its stdlib and the runner's packages; else why not.
    A pass is remembered for INTERP_RECHECK_S unless `force`."""
    python = python or sys.executable
    if not force and time.time() - _INTERP_OK.get(python, 0.0) < INTERP_RECHECK_S:
        return None
    mods = lock_directive("@python-imports").split() or ["openai"]
    code = ("import sys, encodings, json, ssl, hashlib\n"
            f"import {', '.join(mods)}\n"
            "print('interp-ok')")
    try:
        r = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=120,
                           cwd="/", stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        out, ok = f"{type(e).__name__}: {e}", False
    else:
        ok = r.returncode == 0 and r.stdout.strip().endswith("interp-ok")
        lines = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
        out = " | ".join(dict.fromkeys(lines[:1] + lines[-1:])) or f"exit {r.returncode}"
    if ok:
        _INTERP_OK[python] = time.time()
        return None
    _INTERP_OK.pop(python, None)
    return (f"the eval interpreter {python} cannot start an agent ({out}). Every unit would "
            f"die at startup and read as a model FAIL. Rebuild it: ./scripts/eval-host.sh "
            f"setup, then run the harness with $OPENBEAST_EVAL_PY")


# An agent that died this fast, with no completion tokens, on one of these
# lines never reached the model: the interpreter (or its packages) is broken.
INTERP_STARTUP_S = 2.0
_INTERP_STARTUP_RE = re.compile(
    r"Could not find platform (?:in)?dependent libraries"
    r"|Fatal Python error: (?:init_fs_encoding|init_import_site|init_sys_streams"
    r"|_Py_InitializeMain|failed to get the Python codec|Py_Initialize)"
    r"|No module named '?encodings'?"
    r"|ModuleNotFoundError: No module named "
    r"|dyld(?:\[\d+\])?: Library not loaded"
    r"|error while loading shared libraries"
    r"|bad interpreter"
    r"|can't open file .*runner\.py")


def interpreter_startup_failure(agent_result: dict) -> str | None:
    """The startup-error line when an agent died at interpreter startup, else None."""
    code = agent_result.get("exit_code")
    if code is None or code <= 0:
        return None          # a clean exit, or a signal death (its own class)
    if float(agent_result.get("elapsed_seconds") or 0) > INTERP_STARTUP_S:
        return None
    if ((agent_result.get("tokens") or {}).get("completion") or 0) > 0:
        return None
    for stream in (agent_result.get("stderr") or "", agent_result.get("stdout") or ""):
        m = _INTERP_STARTUP_RE.search(stream)
        if m:
            start = stream.rfind("\n", 0, m.start()) + 1
            end = stream.find("\n", m.end())
            return stream[start:end if end != -1 else len(stream)].strip()[:200]
    return None
