#!/usr/bin/env python3
"""CLI utilities for the eval result cache.

  python evals/cache_cli.py stats              # entries, total size, context hash
  python evals/cache_cli.py list               # one line per cached entry
  python evals/cache_cli.py list --model SLUG  # filter by model
  python evals/cache_cli.py clear              # remove all cached entries
  python evals/cache_cli.py clear --model SLUG # remove entries for one model

Cache invalidates automatically when (a) the task spec changes or (b) the
agent runtime context (system-prompt.md, system-prompt-tools.md,
opencode.json) changes — the hash key includes both. This CLI exists for
manual cleanup / inspection."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR))

import cache  # noqa: E402


def cmd_stats(args):
    s = cache.cache_stats()
    print(f"Cache dir:        {cache.CACHE_DIR}")
    print(f"Entries:          {s['entries']}")
    print(f"Total size:       {s['size_bytes']:,} bytes")
    print(f"Context hash:     {cache.context_hash()}")
    print(f"Context files:")
    for p in cache.CONTEXT_FILES:
        mark = "[ok]" if p.exists() else "[missing]"
        print(f"  {mark} {p}")


def cmd_list(args):
    if not cache.CACHE_DIR.exists():
        return
    paths = sorted(cache.CACHE_DIR.glob("*.json"))
    if args.model:
        paths = [p for p in paths if p.stem.startswith(f"{args.model}.")]
    for p in paths:
        try:
            with open(p) as f:
                e = json.load(f)
            tag = "PASS" if e.get("passed") else "FAIL"
            print(f"  {p.stem}  {tag}  {e.get('elapsed_seconds', 0):>6}s  {e.get('cached_at', '')}")
        except Exception as ex:
            print(f"  {p.stem}  (unreadable: {ex})")


def cmd_clear(args):
    if args.model:
        n = cache.cache_invalidate_model(args.model)
        print(f"Removed {n} entries for model {args.model!r}.")
    else:
        n = cache.cache_clear()
        print(f"Removed {n} entries.")


def main():
    parser = argparse.ArgumentParser(description="Eval cache CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats")

    p_list = sub.add_parser("list")
    p_list.add_argument("--model", help="Filter by model slug")

    p_clear = sub.add_parser("clear")
    p_clear.add_argument("--model", help="Only clear entries for this model slug")

    args = parser.parse_args()

    if args.cmd == "stats":
        cmd_stats(args)
    elif args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "clear":
        cmd_clear(args)


if __name__ == "__main__":
    main()
