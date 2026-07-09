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
5. agent context hash — sha256 of system-prompt.md, system-prompt-tools.md,
   opencode.json (which pins the available tools/transport), the agent
   runtime itself (agents/runner.py, agents/tools.py), and the eval-suite
   marker (evals/SUITE_VERSION). Changes to the agent's runtime context →
   cache miss.

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
CONTEXT_FILES = [
    REPO_ROOT / "system-prompt.md",
    REPO_ROOT / "system-prompt-tools.md",
    REPO_ROOT / "opencode.json",
    # The agent runtime itself: a change to the loop or the tool
    # implementations changes what a "cached result" means.
    REPO_ROOT / "agents" / "runner.py",
    REPO_ROOT / "agents" / "tools.py",
    # The suite marker: a suite bump means the task set was redefined.
    EVALS_DIR / "SUITE_VERSION",
]


def _short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def context_hash() -> str:
    """Hash the agent runtime context. Cached because reading three files
    every cache lookup is wasteful — but the cache is invalidated on file
    mtime change so it stays correct."""
    h = hashlib.sha256()
    for p in CONTEXT_FILES:
        if p.exists():
            h.update(p.read_bytes())
        h.update(b"\x00")  # separator so concat ambiguity can't collide
    return h.hexdigest()[:16]


# Lazy memoization — recomputed once per process.
_context_cache: dict[str, str] = {}


def _context_hash_cached() -> str:
    if "h" not in _context_cache:
        _context_cache["h"] = context_hash()
    return _context_cache["h"]


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
              max_iter: int | None = None) -> str:
    """Build the cache key for a (task, model) pair under the current
    agent runtime context.

    max_iter: the EFFECTIVE iteration budget the agent runs with (task
    default or CLI override). When provided it becomes part of the key —
    a run capped at 5 iterations is not the same experiment as one allowed
    15. None (the default) omits the segment, preserving legacy key shape
    for callers that don't thread a budget."""
    mi = f".mi{max_iter}" if max_iter is not None else ""
    return f"{model_slug}.{task['id']}.{task_hash(task)}{mi}.{_context_hash_cached()}"


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
    except Exception:
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
    return n
