#!/usr/bin/env python3
"""Pick the WG14 C working draft out of the document index.

Resolving this is genuinely awkward and the obvious heuristic is WRONG. The
index lists every WG14 *paper*, not just drafts, and carries no titles in its
HTML — so "highest N-numbered PDF" selected n3962, a two-page note titled
"clarify H.11.4 encoding conversion requirements". Shipping that as "the C
standard" would be worse than shipping nothing: a corpus that is confidently
wrong is one an agent will cite.

What actually separates a draft from a paper is SIZE: a C working draft runs
~700 pages and several MB; a paper is a few KB. So HEAD the newest candidates,
take the largest, and REFUSE anything under the floor rather than accept a
paper. Exit 1 on no qualifying candidate, so the caller fails loudly.

usage: wg14_pick.py <index.html> [--newest N] [--floor-bytes N]
prints: <url>\t<bytes>
"""
import argparse
import re
import sys
import urllib.request

BASE = "https://www.open-std.org/jtc1/sc22/wg14/www/docs/"
UA = "openbeast-lang/1.0 (+https://github.com/MaximilianKhan/openbeast)"


def head_size(url: str, timeout: float = 20.0) -> int:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def candidates(html: str, newest: int) -> list[int]:
    ns = {int(m) for m in re.findall(r"n(\d{4})\.pdf", html, re.I)}
    return sorted(ns, reverse=True)[:newest]


def pick(html: str, newest: int, floor: int) -> tuple[str, int] | None:
    best = (0, "")
    for n in candidates(html, newest):
        url = f"{BASE}n{n}.pdf"
        size = head_size(url)
        if size > best[0]:
            best = (size, url)
    if best[1] and best[0] >= floor:
        return best[1], best[0]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("index")
    ap.add_argument("--newest", type=int, default=40)
    ap.add_argument("--floor-bytes", type=int, default=1_000_000)
    a = ap.parse_args()
    html = open(a.index, errors="ignore").read()
    got = pick(html, a.newest, a.floor_bytes)
    if not got:
        print(f"no WG14 candidate >= {a.floor_bytes} bytes in the newest "
              f"{a.newest} documents", file=sys.stderr)
        return 1
    print(f"{got[0]}\t{got[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
