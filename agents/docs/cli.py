#!/usr/bin/env python3
"""beast-docs CLI — scripts/docs.sh is the entry point (docs/BEAST_DOCS.md).

  sources                         the configured sources (agents/docs/sources.toml)
  sync <source> [--max-pages N] [--root URL ...] [--accept-terms] [--force]
  status [--json]                 every mirror: pages, age, STALE, partial/complete
  search <query> [--source S] [--limit N] [--json]
  read <doc-id|url> [--max-chars N]
  live <url|symbol> [--source S] [--save [--accept-terms]]
  pack <out.tar.gz> [--source S ...]
  unpack <tarball> [--sha256 H]
  reindex [--source S]

Exit codes: 0 ok · 1 nothing found · 2 usage / config · 3 offline refusal ·
4 sync refused (terms, scope) · 5 bundle verification failed ·
6 sync stopped by an outage (robots.txt unreachable, repeated transport
failures) — the queue was saved; re-run sync to resume.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "docs"  # noqa: A001

from docs import api                       # noqa: E402
from docs import config as C               # noqa: E402
from docs import store as ST               # noqa: E402
from docs import sync as S                 # noqa: E402


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="docs.sh", description="beast-docs: offline platform docs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sources")
    p = sub.add_parser("sync")
    p.add_argument("source")
    p.add_argument("--max-pages", type=int)
    p.add_argument("--root", action="append", default=[])
    p.add_argument("--accept-terms", action="store_true")
    p.add_argument("--force", action="store_true", help="re-check pages inside the refresh window")
    p.add_argument("--quiet", action="store_true")
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("search")
    p.add_argument("query", nargs="+")
    p.add_argument("--source", default="")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("read")
    p.add_argument("ref")
    p.add_argument("--max-chars", type=int, default=0)
    p = sub.add_parser("live")
    p.add_argument("ref", nargs="+")
    p.add_argument("--source", default="")
    p.add_argument("--save", action="store_true")
    p.add_argument("--accept-terms", action="store_true",
                   help="with --save: accept a requires_ack source's terms (recorded, as sync does)")
    p.add_argument("--max-chars", type=int, default=0)
    p = sub.add_parser("pack")
    p.add_argument("out")
    p.add_argument("--source", action="append", default=[])
    p = sub.add_parser("unpack")
    p.add_argument("tarball")
    p.add_argument("--sha256", default="")
    p = sub.add_parser("reindex")
    p.add_argument("--source", action="append", default=[])
    a = ap.parse_args(argv)
    store = ST.Store()

    if a.cmd == "sources":
        try:
            srcs = C.load_sources()
        except C.ConfigError as e:
            _err(str(e))
            return 2
        for s in srcs.values():
            ack = "  [needs --accept-terms]" if s.requires_ack else ""
            print(f"{s.name:<20} {s.adapter:<16} {s.title}{ack}\n{'':<20} {s.roots[0]}")
        return 0

    if a.cmd == "sync":
        try:
            src = C.load_sources()[a.source]
        except C.ConfigError as e:
            _err(str(e))
            return 2
        except KeyError:
            _err(f"unknown source {a.source!r} (docs.sh sources)")
            return 2
        log = (lambda *_x: None) if a.quiet else print
        print(f"==> sync {src.name} ({src.adapter}) into {store.source_dir(src.name)}")
        try:
            st = S.sync(src, store, a.max_pages, a.root or None, a.accept_terms, a.force, log=log)
        except S.SyncAborted as e:
            _err(str(e))
            return 6
        except S.SyncError as e:
            _err(str(e))
            return 3 if str(e).startswith("offline") else 4
        except KeyboardInterrupt:
            print("interrupted — the frontier was saved; re-run sync to resume")
            return 130
        print(f"    {st['fetched']} fetched: {st['new']} new, {st['updated']} updated, "
              f"{st['unchanged']} unchanged, {st['not_modified']} not modified (304); "
              f"{st['skipped_fresh']} fresh skipped, {st['refused']} refused, {st['errors']} errors; "
              f"{st['requests']} HTTP requests; {st['pages_total']} pages mirrored; "
              f"{st['frontier_left']} queued for the next sync")
        return 0

    if a.cmd == "status":
        if a.json:
            print(json.dumps(api.status_rows(store), indent=1))
        else:
            sys.stdout.write(api.status_text(store))
        return 0

    if a.cmd == "search":
        q = " ".join(a.query)
        if a.json:
            rows = api.search(q, a.source, a.limit, store)
            print(json.dumps(rows, indent=1))
            return 0 if rows else 1
        out = api.search_text(q, a.source, a.limit, store)
        sys.stdout.write(out)
        return 0 if out.startswith("===") else 1

    if a.cmd == "read":
        out = api.read_text(a.ref, a.max_chars, store)
        if out is None:
            _err(f"no mirrored page {a.ref!r} (search first; it prints doc ids)")
            return 1
        sys.stdout.write(out)
        return 0

    if a.cmd == "live":
        out = api.live_text(" ".join(a.ref), a.source, a.save, a.max_chars, store,
                            accept_terms=a.accept_terms)
        sys.stdout.write(out)
        if out.startswith("Error:"):
            return 2
        return 0

    if a.cmd == "pack":
        try:
            r = ST.pack(store, a.out, a.source or None)
        except (ST.BundleError, ValueError) as e:
            _err(str(e))
            return 5
        print(f"packed {', '.join(r['sources'])}: {r['files']} files -> {r['path']}\n"
              f"sha256 {r['sha256']}  (also in {r['path']}.sha256)")
        return 0

    if a.cmd == "unpack":
        try:
            r = ST.unpack(store, a.tarball, a.sha256)
        except (ST.BundleError, ValueError, OSError) as e:
            _err(f"bundle refused: {e}")
            return 5
        print(f"unpacked {', '.join(r['sources'])} (built {r['created_at']}): {r['files']} files "
              f"verified, {r['indexed']} pages indexed into {store.root}")
        return 0

    if a.cmd == "reindex":
        n = ST.reindex(store, a.source or None)
        print(f"indexed {n} pages ({'fts5' if ST.fts5_available() else 'plain scan'})")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
