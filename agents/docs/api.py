"""What a reader gets: search, read, live, status — as text with provenance.

Every answer names its source URL and when it was fetched, and says so when
the mirror is older than the source's refresh window. That is the whole
contract of beast-docs toward a model: a documentation line is only as good
as the date on it, and the model has to be able to say "per Apple's page as
of 2026-11-15" rather than "per my training data".

Trust tier, in beast-lang's terms (docs/BEAST_LANG_PLAN.md §3): everything
here is a DOCUMENT — what the publisher says, not what a toolchain confirmed.
It is served on request with its provenance and is never injected unasked.
"""
from __future__ import annotations

import urllib.error
from typing import Optional

from . import config as C
from . import net
from . import sync as S
from .store import IndexUnavailable, Store, age_days

DEFAULT_MAX_CHARS = 12000


def _day(iso: str) -> str:
    return (iso or "?")[:10]


def freshness(rec: dict, src: Optional[C.Source]) -> str:
    fetched = rec.get("fetched_at", "")
    checked = rec.get("checked_at") or fetched
    age = age_days(checked)
    s = f"fetched {_day(fetched)}"
    if checked and checked != fetched:
        s += f", confirmed unchanged {_day(checked)}"
    if age is not None:
        s += f", {age:.0f}d ago"
        if src and age > src.refresh_days:
            s += (f" — STALE: older than the {src.refresh_days}d refresh window; "
                  f"`docs.sh sync {src.name}` when online")
    return s


def _sources() -> dict:
    try:
        return C.load_sources()
    except C.ConfigError:
        return {}


def _clip(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars - 120)
    cut = cut if cut > 0 else max_chars - 120
    return text[:cut] + f"\n\n[truncated: {len(text) - cut} more characters — pass a larger max_chars]\n"


def search(query: str, source: str = "", limit: int = 8, store: Optional[Store] = None) -> list:
    store = store or Store()
    on_disk = store.sources_on_disk()
    want = [s.strip() for s in (source or "").split(",") if s.strip()]
    names = [s for s in want if s in on_disk] if want else on_disk
    if not names:
        return []
    idx = store.search_index()
    try:
        rows = idx.search(query, names, limit)
    finally:
        idx.close()
    srcs = _sources()
    for r in rows:
        rec = store.peek(r["source"])["pages"].get(r["url"], {})     # parsed once per source
        r["fetched_at"] = rec.get("fetched_at", r["fetched_at"])
        r["checked_at"] = rec.get("checked_at", "")
        r["freshness"] = freshness(rec or {"fetched_at": r["fetched_at"]}, srcs.get(r["source"]))
        r["deprecated"] = bool(rec.get("deprecated"))
    return rows


def search_text(query: str, source: str = "", limit: int = 8, store: Optional[Store] = None) -> str:
    store = store or Store()
    if not store.sources_on_disk():
        return (f"No documentation mirror at {store.root}. Build one when online: "
                f"`scripts/docs.sh sync <source> --max-pages N` (`scripts/docs.sh sources` "
                f"lists them), or carry one in with `scripts/docs.sh unpack`.\n")
    try:
        rows = search(query, source, limit, store)
    except IndexUnavailable as e:
        return f"The documentation search index is unavailable: {e}\n"
    if not rows:
        scope = f" in {source}" if source else ""
        return (f"No mirrored documentation matches {query!r}{scope}. The mirror holds: "
                f"{', '.join(store.sources_on_disk())}. Try other words, or `docs.sh live` "
                f"when online.\n")
    out = [f"=== platform docs: {len(rows)} result(s) for {query!r} (mirror {store.root}) ==="]
    for n, r in enumerate(rows, 1):
        dep = "  [DEPRECATED]" if r.get("deprecated") else ""
        out.append(f"[{n}] {r['title']}{dep} — {r['doc_id']}")
        out.append(f"    {r['url']}  ({r['freshness']})")
        if r["snippet"]:
            out.append(f"    {r['snippet']}")
    out.append("Read one: docs.sh read <doc_id>. This is the publisher's documentation, "
               "not compiler-verified; cite the URL and its date.")
    return "\n".join(out) + "\n"


def read_text(ref: str, max_chars: int = DEFAULT_MAX_CHARS, store: Optional[Store] = None) -> Optional[str]:
    store = store or Store()
    hit = store.find(ref)
    if not hit:
        return None
    name, url, rec = hit
    try:
        md = store.read_page(name, rec["file"])
    except (OSError, ValueError, KeyError):
        return None
    body = md.split("\n---\n", 1)[1] if md.startswith("---\n") and "\n---\n" in md else md
    src = _sources().get(name)
    head = (f"Source: {url}\nMirror: {rec.get('doc_id')} · {freshness(rec, src)}\n"
            + (f"License: {src.license}\n" if src and src.license else "") + "\n")
    return _clip(head + body.lstrip("\n"), max_chars)


def _wanted(source: str, srcs: dict) -> list:
    """`source` is one name or a comma-separated list, as in search()."""
    want = [s.strip() for s in (source or "").split(",") if s.strip()]
    unknown = [w for w in want if w not in srcs]
    if unknown:
        raise LookupError(f"unknown source(s) {', '.join(unknown)} (`docs.sh sources` lists them)")
    return [srcs[w] for w in want]


def resolve_live_all(ref: str, source: str = "", store: Optional[Store] = None) -> list:
    """[(Source, page URL)] to try IN ORDER for a URL or a symbol name;
    raises LookupError. A URL, or a symbol whose exact title is in the
    mirror, gives one candidate. A bare symbol otherwise gives one GUESSED
    URL per DocC source in scope (`source`, else every DocC source) — the
    caller moves to the next only on a 404, so `UIView` is not reported
    "unavailable" just because it is not under /swiftui/."""
    srcs = _sources()
    want = _wanted(source, srcs)
    ref = ref.strip()
    if ref.startswith(("http://", "https://")):
        src = S.source_for_url({s.name: s for s in (want or srcs.values())}, ref)
        if not src:
            where = f"--source {source}" if want else "any configured source (agents/docs/sources.toml)"
            raise LookupError(f"{ref} is not inside {where} — live mode only fetches those")
        return [(src, ref)]
    store = store or Store()
    try:
        hits = search(ref, source, 3, store) if store.sources_on_disk() else []
    except IndexUnavailable:
        hits = []
    for h in hits:
        if h["score"] >= 1000 and h["source"] in srcs:        # exact title in the mirror
            return [(srcs[h["source"]], h["url"])]
    docc = [s for s in (want or srcs.values()) if s.adapter == "apple-docc-json"]
    if not docc:
        raise LookupError(f"cannot turn {ref!r} into a page URL: pass a URL, or --source with a "
                          f"DocC source (symbol names are only guessable for Apple)")
    return [(s, S.guess_docc_url(s, ref)) for s in docc]


def resolve_live(ref: str, source: str = "", store: Optional[Store] = None) -> tuple:
    """(Source, page URL): the first candidate of resolve_live_all."""
    return resolve_live_all(ref, source, store)[0]


def live_text(ref: str, source: str = "", save: bool = False, max_chars: int = DEFAULT_MAX_CHARS,
              store: Optional[Store] = None, fetcher=None, accept_terms: bool = False) -> str:
    """The live page — or, offline / on failure, the mirror's copy with its
    age, and never silently: the first line says which one this is.
    `save` writes it into the mirror, behind the same --accept-terms gate as
    sync (checked BEFORE the request, so a refusal costs no fetch)."""
    store = store or Store()
    try:
        cands = resolve_live_all(ref, source, store)
    except LookupError as e:
        return f"Error: {e}\n"
    out, err, tried = None, None, []
    for src, page in cands:
        if save:
            try:
                S.terms_gate(src, dict(store.load(src.name)), accept_terms)
            except S.SyncError as e:
                return f"Error: not saved — {e}\n"
        try:
            out = S.live(src, page, fetcher=fetcher)
            break
        except (S.SyncError, net.FetchError, net.Refused, urllib.error.HTTPError) as e:
            err = e
            tried.append(page)
            if not (isinstance(e, urllib.error.HTTPError) and e.code == 404):
                break                  # only a wrong GUESS moves on; an outage does not
    if out is None:
        e = err
        why = f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else str(e)
        if len(tried) > 1:
            why += f" for every guessed URL: {', '.join(tried)} — pass the page URL or --source"
        mirrored = read_text(page, max_chars, store)
        if mirrored:
            return (f"LIVE FETCH UNAVAILABLE ({why}). Serving the MIRROR copy instead — "
                    f"check its date below.\n\n" + mirrored)
        hits = search_text(ref, source or (src.name if len(cands) == 1 else ""), 5, store)
        return f"LIVE FETCH UNAVAILABLE ({why}), and {page} is not in the mirror.\n\n{hits}"
    if save:
        S.save_live(src, store, out, accept_terms)
    head = (f"Source: {out['url']}\nLIVE · fetched {out['fetched_at']} from {out['fetched_from']}"
            + (" · saved to the mirror" if save else "") + "\n"
            + (f"License: {src.license}\n" if src.license else "") + "\n")
    return _clip(head + out["markdown"], max_chars)


def status_rows(store: Optional[Store] = None) -> list:
    store = store or Store()
    srcs = _sources()
    rows = []
    for name in list(srcs) + [n for n in store.sources_on_disk() if n not in srcs]:
        m = store.load(name)
        pages = m["pages"]
        src = srcs.get(name)
        if not pages and not m.get("last_sync_at"):
            rows.append({"source": name, "pages": 0, "state": "not mirrored",
                         "title": src.title if src else "(not in sources.toml)"})
            continue
        checked = sorted(p.get("checked_at") or p.get("fetched_at", "") for p in pages.values())
        oldest = age_days(checked[0]) if checked else None
        state = "complete" if m.get("sync_complete") else f"partial ({len(m.get('frontier') or [])} queued)"
        stale = bool(src and oldest is not None and oldest > src.refresh_days)
        rows.append({"source": name, "title": src.title if src else "(not in sources.toml)",
                     "pages": len(pages), "state": state, "stale": stale,
                     "last_sync_at": m.get("last_sync_at", ""),
                     "oldest_check": checked[0] if checked else "",
                     "oldest_age_days": None if oldest is None else round(oldest, 1),
                     "refresh_days": src.refresh_days if src else None,
                     "license": src.license if src else "",
                     "terms_accepted_at": m.get("terms_accepted_at", "")})
    return rows


def status_text(store: Optional[Store] = None) -> str:
    store = store or Store()
    rows = status_rows(store)
    off = C.offline_reason()
    out = [f"beast-docs mirror: {store.root}" + (f"   [{off}]" if off else "")]
    for r in rows:
        if r["pages"] == 0 and r["state"] == "not mirrored":
            out.append(f"  {r['source']:<20} not mirrored        {r['title']}")
            continue
        flag = "STALE" if r.get("stale") else "ok"
        age = r.get("oldest_age_days")
        out.append(f"  {r['source']:<20} {r['pages']:>5} pages  {flag:<5} {r['state']:<22} "
                   f"last sync {_day(r['last_sync_at'])}, oldest check "
                   f"{'?' if age is None else f'{age:.0f}d'} (refresh {r['refresh_days']}d)")
    return "\n".join(out) + "\n"
