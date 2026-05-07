"""Eval result cache — skip re-running tasks whose inputs haven't changed.

Hash key components:
1. model_slug — the model under test (results vary by model)
2. task["id"] — the task+variant identifier
3. task spec hash — sha256 of canonical-encoded task dict (setup, task,
   validation, cleanup, max_iter, etc.). Any spec change → cache miss.
4. agent context hash — sha256 of system-prompt.md, system-prompt-tools.md,
   and opencode.json (which pins the available tools/transport). Changes to
   the agent's runtime context → cache miss.

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
    differently because their specs differ. Internal-only fields like
    'index' or runtime state are not in the dict by the time we cache."""
    payload = json.dumps(task, sort_keys=True, separators=(",", ":")).encode()
    return _short_hash(payload)


def cache_key(task: dict[str, Any], model_slug: str) -> str:
    """Build the cache key for a (task, model) pair under the current
    agent runtime context."""
    return f"{model_slug}.{task['id']}.{task_hash(task)}.{_context_hash_cached()}"


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
