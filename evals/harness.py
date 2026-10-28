"""The eval harness contract: everything run_eval.py decides that can change
what a model sees or how its work is judged.

This file is one of evals/cache.py's CONTEXT_FILES, so editing it rolls the
eval era (review 2026-10-27, R6-5). run_eval.py itself stays out of the hash
— it is mostly bookkeeping (caching, results files, leases, parallelism) and
a change there must not cost the board. What used to live in run_eval.py
and DID shape trajectories or verdicts lives here instead:

- the runner's argv (working directory, where the transcript goes),
- the per-iteration wall budget the unit and each model request get,
- the regex that pulls the expected paths out of the task text (the path
  guard reads them back to the model),
- the paths an eval agent's file tools refuse (earlier transcripts, the
  cache, the task specs and reference solutions),
- the validator timeout and the Python-validator completion wrapper.

Before this, editing any of them left every cache key identical, so old rows
replayed as if measured under the new harness (the 2026-08-20 timeout_scale
change and the v4.1 path-guard change were both of that kind).

Keep this module small and dependency-free: it is hashed byte for byte.
"""

from __future__ import annotations

import os
import re

# --------------------------------------------------------------------------
# The agent's run
# --------------------------------------------------------------------------

#: The runner's --workdir: relative paths the model writes land here.
RUNNER_WORKDIR = "/tmp"

#: Iterations a task gets when its spec does not say.
DEFAULT_MAX_ITER = 15

#: Wall budget per iteration at single-stream decode, in seconds. The unit
#: gets max_iter * this * timeout_scale, and the runner bounds each model
#: request by what is left of it (OPENBEAST_EVAL_WALL_S).
WALL_SECONDS_PER_ITER = 60


def wall_budget(max_iter: int, timeout_scale: float = 1.0) -> int:
    """Seconds an eval unit may run before the harness kills it."""
    return int(max_iter * WALL_SECONDS_PER_ITER * timeout_scale)


def runner_argv(python: str, runner_path: str, base_url: str, max_iter: int,
                task_text: str, log_dir: str,
                context_file: str | None = None) -> list[str]:
    """The command line run_eval starts the agent runner with."""
    cmd = [python, runner_path,
           "--base-url", base_url,
           "--max-iter", str(max_iter),
           "--workdir", RUNNER_WORKDIR,
           "--log-dir", log_dir]
    if context_file:
        cmd += ["--context-file", context_file]
    cmd.append(task_text)
    return cmd


#: /tmp/eval* paths named in the task text. The path guard warns on a write
#: to the right basename in the wrong place and refuses task_done while one
#: is missing; both messages reach the model.
TASK_PATH_RE = re.compile(r"/tmp/eval[\w\-./]*")


def task_expected_paths(task: dict) -> list[str]:
    """The /tmp/eval* paths the TASK TEXT names, for the agent's path guard.

    From the text the model reads and nothing else. Until suite v4.1 the
    list was scraped from setup, validation and cleanup as well, so it held
    files only the validator writes (`/tmp/eval_ntt/out.txt`), and the
    pattern kept a sentence's full stop (`/tmp/eval_gemm/gemm.zig.`, a file
    that can never exist). Trailing sentence punctuation is stripped."""
    found = {m.rstrip(".,;:)") for m in TASK_PATH_RE.findall(str(task.get("task", "")))}
    return sorted(p for p in found if p)


# --------------------------------------------------------------------------
# What an eval agent's file tools may not open (review 2026-10-27, R6-11)
# --------------------------------------------------------------------------

#: Below every checkout of this repo: earlier agents' transcripts (full
#: write_file arguments, i.e. passing solutions), the result cache, the
#: task specs with their hidden cases, and the reference solutions.
DENIED_REPO_SUBDIRS = ("agents/logs", "evals")


def default_eval_log_dir() -> str:
    """Where eval transcripts go: outside the repo, so an agent's `grep -r`
    over the checkout does not find the last model's solution.
    OPENBEAST_EVAL_LOG_DIR overrides it."""
    explicit = os.environ.get("OPENBEAST_EVAL_LOG_DIR", "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    state = (os.environ.get("XDG_STATE_HOME", "").strip()
             or os.path.join(os.path.expanduser("~"), ".local", "state"))
    return os.path.join(state, "openbeast", "eval-logs")


def _git_common_dir(repo_root: str) -> str | None:
    """The repository's shared .git directory, read from disk (no git
    subprocess: tests stub subprocess.Popen around run_agent)."""
    dot = os.path.join(repo_root, ".git")
    if os.path.isdir(dot):
        return dot
    try:
        with open(dot) as f:
            line = f.read().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = os.path.join(repo_root, line[len("gitdir:"):].strip())
    try:
        with open(os.path.join(gitdir, "commondir")) as f:
            return os.path.normpath(os.path.join(gitdir, f.read().strip()))
    except OSError:
        return gitdir


def _checkouts(repo_root: str) -> list[str]:
    """This checkout plus every git worktree of the same repository (the
    main checkout's agents/logs is as readable from a worktree as its own)."""
    roots = [repo_root]
    common = _git_common_dir(repo_root)
    if not common:
        return roots
    roots.append(os.path.dirname(os.path.normpath(common)))
    wt = os.path.join(common, "worktrees")
    try:
        names = os.listdir(wt)
    except OSError:
        names = []
    for name in names:
        try:
            with open(os.path.join(wt, name, "gitdir")) as f:
                roots.append(os.path.dirname(f.read().strip()))
        except OSError:
            continue
    return roots


def denied_roots(repo_root: str, log_dir: str) -> list[str]:
    """Realpath'd directories an eval agent's file tools refuse
    (OPENBEAST_EVAL_DENY_ROOTS). The eval agent's `bash` can still reach
    them: closing that needs the unit sandboxed away from the repo."""
    roots = {os.path.realpath(log_dir)}
    for checkout in _checkouts(repo_root):
        for sub in DENIED_REPO_SUBDIRS:
            roots.add(os.path.realpath(os.path.join(checkout, sub)))
    return sorted(roots)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

VALIDATION_TIMEOUT_S = 30

# Python validators must FINISH to pass (suite v4.1, review R6-1).
#
# A Python validator loads the solution in its own process
# (`spec.loader.exec_module`), and its verdict used to be the exit code
# alone. A solution that exits at import time (`sys.exit(0)`, `os._exit(0)`,
# or a script-style `sys.exit(main())` with no __main__ guard) ended the
# validator with rc 0 before one assert ran: 96 of 99 Python validators
# passed `import sys; sys.exit(0)`.
#
# So the validator runs inside this wrapper. The harness hands it a fresh
# random nonce on stdin (never in argv or the environment, which the
# solution could read from /proc/self), the wrapper execs the validator in
# __main__'s namespace, and prints the nonce only after the validator's
# last statement ran. A PASS needs rc 0 AND the nonce. A SystemExit with
# status 0/None raised while the validator runs becomes a failure, because
# no validator exits 0 on purpose (they end with print('OK')); a non-zero
# SystemExit (a validator's own `raise SystemExit('msg')`) keeps its meaning.
#
# What this does not stop: a solution written to defeat it (walking the
# interpreter's frames to find the nonce). It closes the accidental and the
# cheap cases; deliberate attacks on the harness need sandboxing.
VALIDATOR_SENTINEL = "OPENBEAST-VALIDATOR-COMPLETED:"
VALIDATOR_UNFINISHED = ("[harness] the validator exited with status 0 before "
                        "its checks finished (did the solution call sys.exit "
                        "or os._exit when it was imported?)")
PY_VALIDATOR_WRAPPER = r"""
import os, sys
def _openbeast_validate(src, nonce):
    # The validator's globals are __main__'s, as under a plain `python -c`
    # (multiprocessing pickles functions by reference to __main__).
    g = sys.modules["__main__"].__dict__
    try:
        exec(compile(src, "<validator>", "exec"), g)
    except SystemExit as e:
        if e.code is None or e.code == 0:
            sys.stdout.flush()
            sys.stderr.write("\n" + %(unfinished)r + "\n")
            sys.stderr.flush()
            os._exit(3)
        raise
    sys.stdout.flush()
    sys.stderr.flush()
    sys.stdout.write("\n" + %(sentinel)r + nonce + "\n")
    sys.stdout.flush()
_n = sys.stdin.readline().strip()
_fd = os.open(os.devnull, os.O_RDONLY); os.dup2(_fd, 0); os.close(_fd)
_s = sys.argv[1]
del sys.argv[1:]
_openbeast_validate(_s, _n)
""" % {"sentinel": VALIDATOR_SENTINEL, "unfinished": VALIDATOR_UNFINISHED}
