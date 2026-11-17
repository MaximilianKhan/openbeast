"""Eval result cache — skip re-running tasks whose inputs haven't changed.

Hash key components:
1. model_slug — the model under test (results vary by model)
2. task["id"] — the task+variant identifier
3. task spec hash — sha256 of canonical-encoded task dict (setup, task,
   validation, cleanup, max_iter, etc.). Any spec change → cache miss.
   Keys starting with "_" (e.g. the runtime-injected `_path`) are stripped
   before hashing so keys are independent of repo location.
4. effective max_iter (optional) — the iteration budget the agent actually
   ran with (task default or --max-iter override). A different budget can
   change the outcome, so it is part of the key when supplied.
4b. era components (each omitted when off, so legacy keys stay reachable):
   diag (push-diagnostics toolchain fingerprint), rb (finite reasoning
   budget), greedy (low-churn decode), pack (awareness pack sha —
   task-scoped to the pack's language), env (opt-in environment
   fingerprint: weights, llama.cpp build, KV flags, toolchains), agent
   (agent-side env switches: thinking, seed, verify-done, diag edit-hint;
   built by run_eval.agent_switches).
5. agent context hash (the era) — sha256 of what an eval unit consumes:
   system-prompt.md, the agent runtime itself (agents/runner.py,
   agents/tools.py), the eval-suite marker (evals/SUITE_VERSION) and the
   harness marker (evals/HARNESS_VERSION, pinned to run_eval's
   agent-shaping code). Changes to any of them → cache miss.

Cached payload mirrors the per-task entry written to results JSON: passed,
elapsed_seconds, agent_exit_code, validation_output, token counts, and any
variant metadata. We add `from_cache: True` and `cached_at` on retrieval so
post-hoc analysis can distinguish live runs from replays.

Cache files live at evals/cache/{key}.json. Files are tiny (<1 KB each); a
full sweep at 5 models × 200 entries = 1 000 files, well under any FS limit.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
CACHE_DIR = EVALS_DIR / "cache"

# Files that pin the agent's runtime context. Changing any of these
# invalidates all cached results — that's intentional (it's the whole point
# of the cache key).
#
# ONLY what an eval unit consumes. system-prompt-tools.md and opencode.json
# were hashed until 2026-11 although the eval agent reads neither (its prompt
# is system-prompt.md, its tools tools.TOOL_SCHEMAS): every OpenCode catalog
# edit cold-started the cache for every model. Conversely run_eval.py — which
# builds the agent's argv, env (OPENBEAST_TASK_PATHS, OPENBEAST_EVAL) and
# wall/validation budgets — was in no era at all; HARNESS_VERSION now carries
# it, pinned to harness_fingerprint() by tests/test_cache.py (review finding
# campaign-evals: era hashes the wrong files).
CONTEXT_FILES = [
    REPO_ROOT / "system-prompt.md",
    # The agent runtime itself: a change to the loop or the tool
    # implementations changes what a "cached result" means.
    REPO_ROOT / "agents" / "runner.py",
    REPO_ROOT / "agents" / "tools.py",
    # The suite marker: a suite bump means the task set was redefined.
    EVALS_DIR / "SUITE_VERSION",
    # The harness marker: run_eval's agent-shaping code (see
    # HARNESS_FUNCTIONS). Bumped, deliberately, when that code changes.
    EVALS_DIR / "HARNESS_VERSION",
    # The validator's standard library (v5). run_eval injects it into every
    # validator from source, so a change to it changes how units are judged.
    EVALS_DIR / "vlib.py",
]

# The parts of run_eval.py that decide what an agent sees and how a unit is
# judged: its argv/env/wall budget (and the --jobs contention multiplier on
# it), fixture setup and re-assertion, the validator and its timeout. harness_fingerprint() hashes their AST (comments
# and docstrings excluded, so prose edits move nothing).
HARNESS_FUNCTIONS = ("run_agent", "_run_reaped", "run_setup", "run_pre_validate",
                     "run_validation", "run_cleanup", "contention_scale",
                     # v5: the agent's wall cap, the validator's env and
                     # private build cache, the fixture cwd, and the sweep of
                     # everything the agent left running before validation.
                     "agent_wall_s", "_validator_env", "build_cache_env",
                     "_run_fixture_cmd", "_kill_recorded_groups",
                     "sweep_agent_leftovers", "AgentWatch",
                     # review 1116: the toolchain pinned before any agent runs
                     # and checked before every validation.
                     "toolchain_pins", "_toolchain_problems")
HARNESS_CONSTANTS = ("VALIDATION_TIMEOUT_S", "BUILD_CACHE_ENV", "_AGENT_POLL_S", "_PY_BOOT",
                     "_SWEEP_ROUNDS")


def _ast_canon(node) -> str:
    """Python-version-stable AST serialization: ast.dump's format moves
    between releases (3.13 hides empty fields; new versions add fields such
    as type_params), so emit only NON-empty fields, with no positions."""
    import ast
    if isinstance(node, ast.AST):
        fields = []
        for name in node._fields:
            val = getattr(node, name, None)
            if val is None or val == []:
                continue
            fields.append(f"{name}={_ast_canon(val)}")
        return f"{type(node).__name__}({','.join(fields)})"
    if isinstance(node, list):
        return "[" + ",".join(_ast_canon(v) for v in node) + "]"
    return repr(node)


def harness_fingerprint(path: Path | None = None) -> str:
    """sha256[:16] over the AST of run_eval's agent-shaping code. Read by
    parsing, never importing, so cache.py stays import-light."""
    import ast
    src = (path or EVALS_DIR / "run_eval.py").read_text()
    tree = ast.parse(src)
    parts: dict[str, str] = {}
    for node in tree.body:
        if (isinstance(node, (ast.FunctionDef, ast.ClassDef))
                and node.name in HARNESS_FUNCTIONS):
            # Docstrings are prose: strip them from the def and from every
            # def nested in it (a class's methods), so only code counts.
            for sub in ast.walk(node):
                if not isinstance(sub, (ast.FunctionDef, ast.ClassDef)):
                    continue
                body = sub.body
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(getattr(body[0], "value", None), ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    sub.body = body[1:] or [ast.Pass()]
            parts[node.name] = _ast_canon(node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in HARNESS_CONSTANTS:
                    parts[t.id] = _ast_canon(node.value)
    missing = set(HARNESS_FUNCTIONS + HARNESS_CONSTANTS) - set(parts)
    if missing:
        raise ValueError(f"run_eval.py lacks harness parts {sorted(missing)}")
    blob = "\x00".join(f"{k}={parts[k]}" for k in sorted(parts))
    return _short_hash(blob.encode())


def pinned_harness() -> tuple[str, str]:
    """(version, fingerprint) recorded in evals/HARNESS_VERSION."""
    for line in (EVALS_DIR / "HARNESS_VERSION").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ver, fp = line.split()
            return ver, fp
    raise ValueError("evals/HARNESS_VERSION has no `<version> <fingerprint>` line")


def _short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def context_hash() -> str:
    """Hash the agent runtime context (uncached; see _context_hash_cached)."""
    h = hashlib.sha256()
    for p in CONTEXT_FILES:
        if p.exists():
            h.update(p.read_bytes())
        h.update(b"\x00")  # separator so concat ambiguity can't collide
    return h.hexdigest()[:16]


# Memo, SELF-VALIDATING: keyed on each context file's stat signature, so a
# pull or an edit mid-sweep (benchmark_all keeps one process — and one memo
# — across every model of a multi-day run) is seen at the next lookup. A
# once-per-process memo banked every later unit — run by a fresh runner
# reading the NEW files — under the OLD era's key (review finding
# campaign-evals: era memo never invalidated).
_context_cache: dict[str, object] = {}


def _context_signature() -> tuple:
    sig = []
    for p in CONTEXT_FILES:
        try:
            st = os.stat(p)
            sig.append((str(p), st.st_ino, st.st_size, st.st_mtime_ns,
                        st.st_ctime_ns))
        except OSError:
            sig.append((str(p), None))
    return tuple(sig)


def _context_hash_cached() -> str:
    sig = _context_signature()
    if _context_cache.get("sig") != sig or "h" not in _context_cache:
        _context_cache["h"] = context_hash()
        _context_cache["sig"] = sig
    return _context_cache["h"]  # type: ignore[return-value]


def current_era() -> str:
    """The era the cache keys are built with RIGHT NOW. Stamp results with
    this, never a separately computed hash, so stamp and keys agree."""
    return _context_hash_cached()


def task_hash(task: dict[str, Any]) -> str:
    """Hash the task spec. Variant metadata (base_id, variant_id, language,
    variant_count) is INCLUDED — different variants of the same task hash
    differently because their specs differ. ALL keys starting with "_"
    (e.g. the runtime-injected `_path`, which embeds the absolute repo
    location) are stripped before hashing so cache keys survive a repo
    move/clone."""
    clean = {k: v for k, v in task.items() if not k.startswith("_")}
    payload = json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()
    return _short_hash(payload)


def cache_key(task: dict[str, Any], model_slug: str,
              max_iter: int | None = None,
              diag: str | None = None,
              rb: str | None = None,
              greedy: bool = False,
              pack: str | None = None,
              env: str | None = None,
              agent: str | None = None) -> str:
    """Build the cache key for a (task, model) pair under the current
    agent runtime context.

    max_iter: the EFFECTIVE iteration budget the agent runs with (task
    default or CLI override). When provided it becomes part of the key —
    a run capped at 5 iterations is not the same experiment as one allowed
    15. None (the default) omits the segment, preserving legacy key shape
    for callers that don't thread a budget."""
    mi = f".mi{max_iter}" if max_iter is not None else ""
    # Push-diagnostics era component (docs/LANG_AWARENESS_PLAN.md §3.4).
    # OMITTED when diagnostics are off so legacy keys — every row banked
    # before the feature existed — stay reachable. When on, the component
    # carries a toolchain fingerprint, not a boolean: zig 0.16.0 vs 0.16.1
    # emit different diagnostics → different model trajectories.
    dg = f".{diag}" if diag else ""
    # Reasoning-budget era component (2026-09-09). A row generated under a
    # thinking cap is a different experiment from an uncapped one — the
    # 2026-09-09 A/B relaunch nearly replayed uncapped rows into a capped
    # run because server flags were absent from the key. Same convention as
    # diag: OMITTED when uncapped (None / -1), so every legacy row stays
    # reachable; a finite budget stamps its value.
    rbc = f".rb{rb}" if rb else ""
    # Greedy-decode era (2026-09-10): greedy and sampled rows are different
    # experiments; omitted when off so every legacy row stays reachable.
    gr = ".greedy" if greedy else ""
    # Awareness-pack era (Tier 3, 2026-09-11): `pack1-<sha8 of the pack
    # bytes>`. TASK-SCOPED by the caller — only units whose language has a
    # pack receive the component, because a non-zig request is
    # byte-identical with packs on or off (no --context-file is passed) and
    # so legitimately shares the un-packed era's rows. Omitted when None.
    pk = f".{pack}" if pack else ""
    # Environment era (2026-09-29, opt-in via OPENBEAST_EVAL_ENV_ERA=1):
    # `env1-<sha8>` over the weights' identity, the llama.cpp build, the
    # KV/context serve flags and the validator toolchains — none of which
    # the rest of the key can see (run_eval.env_fingerprint). Omitted when
    # None, so every existing key stays reachable.
    en = f".{env}" if env else ""
    # Agent-switch era (2026-11-16): `ag-<parts>` for the env switches that
    # change what the AGENT does and that nothing else in the key can see:
    # OPENBEAST_AGENT_THINKING, OPENBEAST_AGENT_SEED,
    # OPENBEAST_AGENT_VERIFY_DONE, OPENBEAST_DIAG_EDIT_HINT
    # (run_eval.agent_switches builds it). Without it, a thinking-on or
    # seed-2 arm run without --no-cache replayed, and banked into, the
    # default arm's rows. OMITTED when every switch is unset, so every legacy
    # key stays reachable.
    ag = f".{agent}" if agent else ""
    return (f"{model_slug}.{task['id']}.{task_hash(task)}{mi}{dg}{rbc}{gr}{pk}{en}{ag}."
            f"{_context_hash_cached()}")


def cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def cache_get(key: str) -> dict[str, Any] | None:
    """Return cached result dict for `key`, or None if not cached or unreadable."""
    p = cache_path(key)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            cached = json.load(f)
        return cached
    except (json.JSONDecodeError, OSError) as e:
        # Corrupt or unreadable entry = cache miss, but say so — a silent
        # None here makes real disk problems look like cold caches.
        print(f"  [cache] unreadable entry {p.name}: {e} — treating as miss")
        return None


def cache_put(key: str, result: dict[str, Any]) -> None:
    """Persist `result` under `key`. Adds a `cached_at` ISO timestamp for
    post-hoc inspection. Idempotent overwrites are fine — the key is stable
    by construction."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(result)
    payload["cached_at"] = datetime.now().isoformat()
    tmp = cache_path(key).with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, cache_path(key))


# env_error strikes: how many times a key's unit failed on resource
# exhaustion (run_eval._ENV_ERROR_RE). Kept beside the cache, one small file
# per key, outside the *.json glob every other helper walks.
STRIKES_DIR = CACHE_DIR / "env-strikes"


def env_error_strike(key: str) -> int:
    """Record one more env_error for `key`; return the running count.
    Unreadable state counts as zero — a lost strike costs one extra rerun."""
    STRIKES_DIR.mkdir(parents=True, exist_ok=True)
    p = STRIKES_DIR / f"{key}.json"
    try:
        n = int(json.loads(p.read_text()).get("strikes", 0))
    except (OSError, ValueError, AttributeError):
        n = 0
    n += 1
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"strikes": n, "last": datetime.now().isoformat()}))
    os.replace(tmp, p)
    return n


def env_error_strikes_clear(key: str) -> None:
    """Forget `key`'s strikes (a real verdict was banked for it)."""
    try:
        (STRIKES_DIR / f"{key}.json").unlink()
    except OSError:
        pass


def cache_stats() -> dict[str, Any]:
    """Quick stats for the CLI."""
    if not CACHE_DIR.exists():
        return {"entries": 0, "size_bytes": 0}
    total = 0
    size = 0
    for p in CACHE_DIR.glob("*.json"):
        total += 1
        size += p.stat().st_size
    return {"entries": total, "size_bytes": size}


def cache_invalidate_model(model_slug: str) -> int:
    """Drop all cache entries for one model. Returns count removed."""
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for p in CACHE_DIR.glob(f"{model_slug}.*.json"):
        p.unlink()
        n += 1
    return n


def cache_clear() -> int:
    """Drop all cache entries. Returns count removed."""
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for p in CACHE_DIR.glob("*.json"):
        p.unlink()
        n += 1
    for p in STRIKES_DIR.glob("*.json") if STRIKES_DIR.exists() else []:
        p.unlink()
    return n
