"""Crawl one source into the mirror (sync), or fetch one page now (live).

Sync is incremental and resumable:

  * a page checked within `refresh_days` is not requested again — its stored
    links still feed the frontier, so repeated small syncs walk DEEPER instead
    of re-checking the same first N pages;
  * a page older than that is re-requested CONDITIONALLY (If-None-Match /
    If-Modified-Since from the manifest). 304, or a 200 whose rendered
    markdown hashes the same, rewrites nothing — only checked_at moves;
  * the frontier is saved in the manifest every few pages and on the way out,
    so an interrupted sync (^C, a dead network, --max-pages) resumes where it
    stopped. A sync that empties its frontier is `complete`.

Politeness: one request at a time, `delay` seconds apart per host (robots
Crawl-delay wins when larger), robots.txt checked for the URL we request AND
the human page it stands for, and our own User-Agent. Nothing outside the
source's hosts, ever.
"""
from __future__ import annotations

import json
import urllib.error
from typing import Callable, Optional

from . import config as C
from . import docc
from . import htmlmd as H
from . import net
from . import urls as U
from .store import Store, age_days, doc_id, now_iso

SAVE_EVERY = 10


class SyncError(Exception):
    pass


def fetch_url_for(src: C.Source, page: str) -> str:
    if src.adapter == "apple-docc-json":
        return docc.json_url(page)
    if src.adapter == "markdown":
        return page.rstrip("/") + src.markdown_suffix
    return page


def render(src: C.Source, page: str, body: bytes, hints: Optional[dict] = None) -> dict:
    """{title, markdown, links, meta} for one fetched body, by adapter."""
    hints = hints or {}
    text = body.decode("utf-8", "replace")
    if src.adapter == "apple-docc-json":
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise SyncError(f"{page}: not DocC JSON ({e})") from None
        title, md = docc.to_markdown(doc, page)
        s = docc.summary(doc)
        return {"title": title, "markdown": md, "links": docc.links(doc, page),
                "meta": {"kind": s["kind"], "deprecated": s["deprecated"]}}
    if src.adapter == "markdown":
        r = H.clean_markdown(text, hints.get("title", ""), hints.get("description", ""))
        return {"title": r["title"], "markdown": r["markdown"],
                "links": H.markdown_links(text, page, src.markdown_suffix), "meta": {}}
    r = H.html_to_markdown(text, src.content_selectors, src.strip_selectors)
    return {"title": r["title"], "markdown": r["markdown"], "links": H.html_links(text, page),
            "meta": {"page_updated": r["updated"]}}


def _discover(src, fetcher, log) -> tuple:
    """(seed URLs, {url: hints}) from llms.txt / sitemap discovery."""
    if src.discovery == "links":
        return [], {}
    try:
        r = fetcher.get(src.index_url)
    except (net.FetchError, urllib.error.HTTPError) as e:
        log(f"  ! discovery index {src.index_url}: {e} — falling back to links from the roots")
        return [], {}
    text = r.body.decode("utf-8", "replace")
    if src.discovery == "llms-txt":
        entries = H.llms_index(text, src.index_url, src.markdown_suffix)
        hints = {u: {"title": t, "description": d} for u, t, d in entries}
        seeds = [u for u, _t, _d in entries if src.in_scope(u)]
        log(f"  discovery: {len(seeds)} in-scope page(s) of {len(entries)} in {src.index_url}")
        return seeds, hints
    locs, is_index = H.sitemap_locs(text)
    seeds = []
    if is_index:
        for child in locs:
            if not U.host_allowed(src.hosts, child):
                continue
            try:
                c = fetcher.get(child, max_bytes=64 * 1024 * 1024)
            except (net.FetchError, urllib.error.HTTPError) as e:
                log(f"  ! sitemap {child}: {e}")
                continue
            seeds += [u for u in H.sitemap_locs(c.body.decode("utf-8", "replace"))[0]]
    else:
        seeds = locs
    seeds = [H.canonical(u) for u in seeds]
    seeds = [u for u in dict.fromkeys(seeds) if u and src.in_scope(u)]
    log(f"  discovery: {len(seeds)} in-scope page(s) from the sitemap")
    return seeds, {}


def sync(src: C.Source, store: Store, max_pages: Optional[int] = None,
         roots: Optional[list] = None, accept_terms: bool = False, force: bool = False,
         fetcher: Optional[net.Fetcher] = None, log: Callable = print) -> dict:
    """Crawl `src` into `store`. Returns counters; raises SyncError on refusal."""
    reason = C.offline_reason()
    if reason:
        raise SyncError(f"offline: {reason}. A sync needs the internet — build the mirror on "
                        f"a connected box and carry it here: docs.sh pack / docs.sh unpack.")
    m = store.load(src.name)
    if src.requires_ack and not m.get("terms_accepted_at"):
        if not accept_terms:
            raise SyncError(
                f"{src.title}: the publisher's terms restrict copying.\n  {src.license}\n"
                f"  Re-run with --accept-terms to mirror it for your own use on your own "
                f"machines (recorded in the manifest).")
        m["terms_accepted_at"] = now_iso()
    budget = src.max_pages if max_pages is None else max(0, int(max_pages))
    fetcher = fetcher or net.Fetcher(src.hosts, src.delay)
    for r in roots or []:
        if not src.in_scope(r):
            raise SyncError(f"root {r} is outside {src.name}'s scope ({src.allow_prefixes})")
    pages = m["pages"]
    # Resume an interrupted frontier; otherwise start from the roots.
    frontier = [tuple(x) for x in m.get("frontier") or [] if isinstance(x, list) and len(x) == 2]
    resumed = bool(frontier)
    hints: dict = {}
    if roots:
        # --root jumps the queue; it does not throw away a saved frontier.
        frontier = [(u, 0) for u in roots] + frontier
    elif not frontier:
        seeds, hints = _discover(src, fetcher, log)
        frontier = [(u, 0) for u in src.roots] + [(u, 1) for u in seeds]
    m["hints"] = {**(m.get("hints") or {}), **hints}
    hints = m["hints"]
    seen = set()
    stats = {"source": src.name, "fetched": 0, "new": 0, "updated": 0, "unchanged": 0,
             "not_modified": 0, "skipped_fresh": 0, "refused": 0, "errors": 0,
             "requests": 0, "resumed": resumed}
    m["last_sync_started_at"] = now_iso()
    m["sync_complete"] = False
    i = 0
    try:
        while frontier:
            url, depth = frontier.pop(0)
            url = H.canonical(url) if src.adapter != "apple-docc-json" else U.normalize_url(url)
            if not url or url in seen or not src.in_scope(url):
                continue
            seen.add(url)
            rec = pages.get(url)
            fresh = (rec and not force and rec.get("checked_at")
                     and (age_days(rec["checked_at"]) or 0) < src.refresh_days)
            if fresh:
                stats["skipped_fresh"] += 1
                if depth < src.max_depth:
                    frontier += [(u, depth + 1) for u in rec.get("links") or [] if u not in seen]
                continue
            if stats["fetched"] >= budget:
                frontier.insert(0, (url, depth))
                seen.discard(url)
                break
            try:
                target = fetch_url_for(src, url)
            except ValueError as e:
                stats["refused"] += 1
                log(f"  - refused  {url}: {e}")
                continue
            try:
                if not fetcher.robots_for(url).allowed(url):
                    raise net.Refused(f"robots.txt disallows the page {url}")
                stats["fetched"] += 1
                resp = fetcher.get(target, etag=(rec or {}).get("etag", ""),
                                   last_modified=(rec or {}).get("last_modified", ""))
            except net.Refused as e:
                stats["refused"] += 1
                log(f"  - refused  {url}: {e}")
                continue
            except urllib.error.HTTPError as e:
                stats["errors"] += 1
                log(f"  ! HTTP {e.code} {target}")
                continue
            except net.FetchError as e:
                stats["errors"] += 1
                log(f"  ! {e}")
                continue
            now = now_iso()
            if resp.status == 304 and rec:
                rec["checked_at"] = now
                stats["not_modified"] += 1
                log(f"  = 304      {url}")
            else:
                try:
                    out = render(src, url, resp.body, hints.get(url))
                except SyncError as e:
                    stats["errors"] += 1
                    log(f"  ! {e}")
                    continue
                prev_sha = (rec or {}).get("sha256")
                meta = {"fetched_from": target, "fetched_at": now, "etag": resp.etag,
                        "last_modified": resp.last_modified, "license": src.license, **out["meta"]}
                links = [u for u in out["links"] if src.in_scope(u)]
                if prev_sha and rec and rec.get("file"):
                    # Same rendered bytes = the same page: keep the file (and its
                    # fetched_at) untouched; only the validators and checked_at move.
                    from .store import sha256_bytes  # noqa: PLC0415
                    if sha256_bytes(out["markdown"].encode()) == prev_sha:
                        rec.update({"checked_at": now, "etag": resp.etag or rec.get("etag", ""),
                                    "last_modified": resp.last_modified or rec.get("last_modified", ""),
                                    "links": links})
                        stats["unchanged"] += 1
                        log(f"  = same     {url}")
                        if depth < src.max_depth:
                            frontier += [(u, depth + 1) for u in links if u not in seen]
                        continue
                rel, sha, _changed = store.write_page(src.name, url, out["title"], out["markdown"], meta)
                pages[url] = {"doc_id": doc_id(src.name, url), "file": rel, "title": out["title"],
                              "fetched_from": target, "fetched_at": now, "checked_at": now,
                              "etag": resp.etag, "last_modified": resp.last_modified,
                              "sha256": sha, "links": links, **out["meta"]}
                idx = store.index()
                try:
                    idx.upsert(src.name, url, pages[url]["doc_id"], out["title"], now,
                               store.read_page(src.name, rel))
                finally:
                    idx.close()
                stats["updated" if prev_sha else "new"] += 1
                log(f"  {'~ updated' if prev_sha else '+ new    '} {url}  ({out['title']})")
                rec = pages[url]
            if depth < src.max_depth:
                frontier += [(u, depth + 1) for u in (rec or {}).get("links") or [] if u not in seen]
            i += 1
            if i % SAVE_EVERY == 0:
                m["frontier"] = [list(x) for x in frontier]
                store.save(src.name, m)
    finally:
        # Drop what this run already visited; keep the rest for a resume.
        rest, dedup = [], set()
        for u, d in frontier:
            if u not in seen and u not in dedup and src.in_scope(u):
                dedup.add(u)
                rest.append([u, d])
        m["frontier"] = rest
        m["sync_complete"] = not rest
        m["last_sync_at"] = now_iso()
        stats["requests"] = fetcher.requests
        stats["frontier_left"] = len(rest)
        m["robots"] = {h: rb.note for h, rb in fetcher.robots.items()}
        m["last_sync"] = {k: v for k, v in stats.items() if k != "source"}
        store.save(src.name, m)
    stats["pages_total"] = len(pages)
    return stats


# ── live ─────────────────────────────────────────────────────────────────

def source_for_url(sources: dict, url: str) -> Optional[C.Source]:
    for s in sources.values():
        if s.in_scope(H.canonical(url, ".md.txt") or url):
            return s
    return None


def guess_docc_url(src: C.Source, symbol: str) -> str:
    """NavigationStack -> <root>/navigationstack; View.navigationTitle(_:) ->
    <root>/view/navigationtitle(_:)."""
    root = src.roots[0].rstrip("/")
    path = "/".join(p for p in symbol.strip().strip("`").split(".") if p)
    return f"{root}/{path.lower()}"


def live(src: C.Source, page: str, fetcher: Optional[net.Fetcher] = None) -> dict:
    """Fetch ONE page now through the source's adapter. Raises SyncError /
    net.Refused / net.FetchError / HTTPError; the caller decides the fallback."""
    reason = C.offline_reason()
    if reason:
        raise SyncError(f"offline: {reason}")
    raw = page
    page = (H.canonical(page, src.markdown_suffix) if src.adapter != "apple-docc-json"
            else U.normalize_url(page))
    # Scope, robots and the request all see this one normalized spelling.
    if not page or not src.in_scope(page):
        raise net.Refused(f"{page or raw} is outside {src.name}'s scope")
    fetcher = fetcher or net.Fetcher(src.hosts, 0.0)
    if not fetcher.robots_for(page).allowed(page):
        raise net.Refused(f"robots.txt disallows {page}")
    try:
        target = fetch_url_for(src, page)
    except ValueError as e:
        raise net.Refused(str(e)) from None
    resp = fetcher.get(target)
    out = render(src, page, resp.body)
    out.update({"url": page, "fetched_from": target, "fetched_at": now_iso(),
                "etag": resp.etag, "last_modified": resp.last_modified, "source": src.name})
    return out


def save_live(src: C.Source, store: Store, out: dict) -> str:
    """Write a live result into the mirror, as a sync would have."""
    m = store.load(src.name)
    meta = {"fetched_from": out["fetched_from"], "fetched_at": out["fetched_at"],
            "etag": out["etag"], "last_modified": out["last_modified"], "license": src.license,
            **out.get("meta", {})}
    rel, sha, _ = store.write_page(src.name, out["url"], out["title"], out["markdown"], meta)
    m["pages"][out["url"]] = {"doc_id": doc_id(src.name, out["url"]), "file": rel,
                              "title": out["title"], "fetched_from": out["fetched_from"],
                              "fetched_at": out["fetched_at"], "checked_at": out["fetched_at"],
                              "etag": out["etag"], "last_modified": out["last_modified"],
                              "sha256": sha, "links": [u for u in out["links"] if src.in_scope(u)],
                              **out.get("meta", {})}
    store.save(src.name, m)
    idx = store.index()
    try:
        idx.upsert(src.name, out["url"], doc_id(src.name, out["url"]), out["title"],
                   out["fetched_at"], store.read_page(src.name, rel))
    finally:
        idx.close()
    return rel
