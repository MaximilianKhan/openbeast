"""The mirror on disk: manifests, pages, the search index, portable bundles.

    <root>/                          OPENBEAST_DOCS_DIR, default ~/.openbeast/docs
      index.sqlite                   search index over every source (rebuildable)
      <source>/manifest.json         URL -> {doc_id, file, fetched_at, checked_at,
                                     etag, last_modified, sha256, title, links}
      <source>/pages/<slug>.md       one page, markdown, provenance header on top

The manifest is the source of truth, like beast-lang's library manifests: a
page is described only after its bytes are on disk and hashed, so a manifest
never names something that is not there. The index is derived and can always
be rebuilt from the pages (`docs.sh reindex`).

A bundle (`pack`) is a tar.gz of the chosen sources plus BUNDLE.json listing
every file's sha256, and a `<tarball>.sha256` beside it. `unpack` checks the
tarball hash, refuses unsafe members (absolute, `..`, links, devices),
re-hashes every file against BUNDLE.json, and only then swaps each source in
— the air-gap bundle's rule (scripts/bundle.sh): nothing is trusted because
it arrived.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
from collections import OrderedDict
import urllib.parse
from typing import Optional

from . import config as C

SCHEMA = 1
BUNDLE_NAME = "BUNDLE.json"

# Parsed manifests for READERS (search, read, live's fallback), keyed on the
# file's identity: path + inode + mtime_ns + size. A save replaces the file
# (new inode, new mtime), so a stale entry is never served. Small: a big
# source's manifest parses to tens of MB.
_PEEK_MAX = 4
_PEEK: "OrderedDict" = OrderedDict()
_PEEK_LOCK = threading.Lock()


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> Optional[float]:
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError, TypeError):
        return None


def age_days(iso: str, now: Optional[float] = None) -> Optional[float]:
    t = parse_iso(iso)
    if t is None:
        return None
    return max(0.0, ((now or time.time()) - t) / 86400.0)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def slug(url: str) -> str:
    """A filesystem-safe, readable, collision-proof file name for a URL."""
    from urllib.parse import urlsplit  # noqa: PLC0415
    path = urlsplit(url).path.strip("/") or "index"
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", path).strip("._")[:120] or "page"
    return f"{s}-{hashlib.sha1(url.encode()).hexdigest()[:8]}.md"


def doc_id(source: str, url: str) -> str:
    from urllib.parse import urlsplit  # noqa: PLC0415
    return f"{source}:{urlsplit(url).path or '/'}"


class Store:
    def __init__(self, root: Optional[str] = None):
        self.root = root or C.store_root()

    # manifests -------------------------------------------------------------
    def source_dir(self, name: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9_-]{0,47}$", name or ""):
            raise ValueError(f"bad source name {name!r}")
        return os.path.join(self.root, name)

    def manifest_path(self, name: str) -> str:
        return os.path.join(self.source_dir(name), "manifest.json")

    def load(self, name: str) -> dict:
        try:
            with open(self.manifest_path(name), encoding="utf-8") as fh:
                m = json.load(fh)
            if isinstance(m, dict) and isinstance(m.get("pages"), dict):
                return m
        except (OSError, ValueError):
            pass
        return {"schema": SCHEMA, "source": name, "pages": {}, "frontier": [],
                "created_at": now_iso()}

    def peek(self, name: str) -> dict:
        """The manifest for READING ONLY — shared, cached, never mutate it
        (load() returns a private copy for writers). Re-parsed only when the
        file changed, so a search with eight hits parses it at most once
        instead of eight times."""
        return self._peek(name)[0]

    def _peek(self, name: str) -> tuple:
        """(manifest, {doc_id.lower(): url}, {file basename: url})."""
        path = self.manifest_path(name)
        try:
            st = os.stat(path)
            key = (path, st.st_ino, st.st_mtime_ns, st.st_size)
        except OSError:
            return self.load(name), {}, {}
        with _PEEK_LOCK:
            hit = _PEEK.get(path)
            if hit and hit[0] == key:
                _PEEK.move_to_end(path)
                return hit[1]
        m = self.load(name)
        by_id, by_file = {}, {}
        for u, rec in m["pages"].items():
            if isinstance(rec, dict):
                by_id.setdefault(str(rec.get("doc_id", "")).lower(), u)
                by_file.setdefault(os.path.basename(str(rec.get("file", ""))), u)
        val = (m, by_id, by_file)
        with _PEEK_LOCK:
            _PEEK[path] = (key, val)
            _PEEK.move_to_end(path)
            while len(_PEEK) > _PEEK_MAX:
                _PEEK.popitem(last=False)
        return val

    def save(self, name: str, m: dict) -> None:
        _atomic_write(self.manifest_path(name),
                      json.dumps(m, indent=1, sort_keys=True).encode())

    def sources_on_disk(self) -> list:
        try:
            names = sorted(os.listdir(self.root))
        except OSError:
            return []
        return [n for n in names if os.path.isfile(os.path.join(self.root, n, "manifest.json"))]

    # pages -----------------------------------------------------------------
    def write_page(self, source: str, url: str, title: str, body_md: str, meta: dict) -> tuple:
        """(file_rel, sha256, changed). The header carries the provenance a
        reader needs without the manifest: where, when, under what licence."""
        rel = os.path.join("pages", slug(url))
        path = os.path.join(self.source_dir(source), rel)
        sha = sha256_bytes(body_md.encode())
        try:
            with open(path, encoding="utf-8") as fh:
                old = fh.read()
            if f"\nsha256: {sha}\n" in old.split("\n---\n", 1)[0] + "\n":
                return rel, sha, False
        except OSError:
            pass
        head = ["---", f"title: {json.dumps(title, ensure_ascii=False)}", f"source: {source}",
                f"doc_id: {doc_id(source, url)}", f"url: {url}"]
        for k in ("fetched_from", "fetched_at", "last_modified", "etag", "page_updated",
                  "kind", "deprecated", "license"):
            v = meta.get(k)
            if v not in (None, "", False):
                head.append(f"{k}: {json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v}")
        head += [f"sha256: {sha}", "---", ""]
        _atomic_write(path, ("\n".join(head) + body_md).encode())
        return rel, sha, True

    def read_page(self, source: str, rel: str) -> str:
        path = os.path.normpath(os.path.join(self.source_dir(source), rel))
        if not path.startswith(self.source_dir(source) + os.sep):
            raise ValueError("page path escapes the source directory")
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def find(self, ref: str, sources=None) -> Optional[tuple]:
        """(source, url, record) for a doc id, a URL, or a page file name.
        The record is the cached manifest's (read-only). A doc id names its
        source (`swiftui:/documentation/...`), so only that manifest is
        consulted for one; every lookup is a dict hit, not a page scan."""
        ref = (ref or "").strip()
        from .htmlmd import canonical  # noqa: PLC0415
        names = list(sources or self.sources_on_disk())
        m = re.match(r"^([a-z0-9][a-z0-9_-]{0,47}):/", ref)
        if m and m.group(1) in names:
            names = [m.group(1)]           # doc ids carry their source's name; no other can hold it
        cands = [u for u in (ref, canonical(ref), canonical(ref, ".md.txt"), ref.rstrip("/")) if u]
        for name in names:
            man, by_id, by_file = self._peek(name)
            pages = man["pages"]
            for u in cands:
                if u in pages:
                    return name, u, pages[u]
            u = by_id.get(ref.lower()) or by_file.get(ref)
            if u and u in pages:
                return name, u, pages[u]
        return None

    # the index -------------------------------------------------------------
    def index_path(self) -> str:
        return os.path.join(self.root, "index.sqlite")

    def index(self) -> "Index":
        """The index, read-write. One that was just (re)created — new, or
        built by the other search mode or an older schema — is refilled from
        the pages on disk before it is handed out: a mode flip never leaves
        an empty index behind."""
        idx = Index(self.index_path())
        if idx.rebuilt:
            try:
                _fill(self, idx, self.sources_on_disk())
            except BaseException:
                idx.close()
                raise
        return idx

    def writable(self) -> bool:
        p = self.index_path()
        return os.access(self.root, os.W_OK) and (not os.path.exists(p) or os.access(p, os.W_OK))

    def search_index(self) -> "Index":
        """The index for a SEARCH: opened read-only, so searching writes
        nothing. When it cannot be used as it is (missing, the other mode, an
        older schema) it is rebuilt if this store is writable; otherwise
        IndexUnavailable says what to run."""
        try:
            return Index(self.index_path(), readonly=True)
        except IndexUnavailable as e:
            if not self.writable():
                raise IndexUnavailable(
                    f"{e}, and {self.root} is read-only here — run `docs.sh reindex` as the "
                    f"store's owner (with this python), or unpack the mirror somewhere writable. "
                    f"Reading a page by doc id still works.") from None
        self.index().close()                       # rebuilds and refills
        return Index(self.index_path(), readonly=True)


def fts5_available() -> bool:
    if os.environ.get("OPENBEAST_DOCS_NO_FTS") == "1":
        return False
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
        c.close()
        return True
    except sqlite3.Error:
        return False


def _camel_split(s: str) -> str:
    """NavigationStack -> "Navigation Stack", lazyColumn -> "lazy Column": so
    "navigation stack" finds the symbol and "NavigationStack" still does."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", s)


def _body_text(md: str) -> str:
    return md.split("\n---\n", 1)[1] if md.startswith("---\n") and "\n---\n" in md else md


def _headings(md: str) -> str:
    return " ".join(re.findall(r"^#{2,6}\s+(.+)$", md, re.M))


INDEX_SCHEMA = "2"          # 2: the `ids` side table (doc_id -> rowid, compact title)


class IndexUnavailable(Exception):
    """The search index cannot be used as it is and cannot be rebuilt here."""


def _compact(s: str) -> str:
    """The exact-title key, the SAME on both sides of the comparison:
    `navigationDestination(for:destination:)`, "navigation destination for
    destination" and `NavigationDestination` all compact alike."""
    return re.sub(r"\W+", "", s or "").lower()


class Index:
    """Search over every mirrored page. SQLite FTS5 with bm25 when the
    interpreter's sqlite has it (checked, not assumed: OPENBEAST_DOCS_NO_FTS=1
    forces the fallback), else a plain table scanned and scored in Python —
    fine for the size of a local mirror, and the same answers in the same
    order for the cases the tests pin.

    Beside `docs` sits an ordinary table, `ids` (doc_id PRIMARY KEY -> the
    docs rowid, source, compact title, indexed). FTS5 cannot index an
    UNINDEXED column or an expression, so "replace this doc" and "the page
    titled exactly X" were full scans of every row; through `ids` both are
    B-tree lookups, and reindex/unpack are linear instead of quadratic.

    `readonly=True` (searches) opens the file `mode=ro` and writes NOTHING,
    so a mirror on a read-only mount, or owned by another user, searches
    fine. It raises IndexUnavailable when the file was built by the other
    search mode or an older schema: Store.search_index() rebuilds it when it
    can and explains when it cannot. A read-write open whose meta does not
    match recreates the tables EMPTY and sets `rebuilt`; Store.index()
    refills them from the pages on disk."""

    def __init__(self, path: str, readonly: bool = False):
        self.path = path
        self.fts = fts5_available()
        self.mode = "fts5" if self.fts else "plain"
        self.rebuilt = False
        if readonly:
            if not os.path.isfile(path):
                raise IndexUnavailable(f"no search index at {path}")
            uri = "file:" + urllib.parse.quote(os.path.abspath(path)) + "?mode=ro"
            self.db = sqlite3.connect(uri, uri=True)
            built = self._meta()
            if built != (self.mode, INDEX_SCHEMA):
                self.db.close()
                raise IndexUnavailable(
                    f"the index was built as {built[0] or '?'} (schema {built[1] or '?'}); this "
                    f"python searches with {self.mode} (schema {INDEX_SCHEMA})")
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        if self._meta() != (self.mode, INDEX_SCHEMA):
            # New, built by the other mode, or an older schema: recreate empty.
            self.db.execute("DROP TABLE IF EXISTS docs")
            self.db.execute("DROP TABLE IF EXISTS ids")
            if self.fts:
                self.db.execute(
                    "CREATE VIRTUAL TABLE docs USING fts5("
                    "doc_id UNINDEXED, source UNINDEXED, url UNINDEXED, fetched_at UNINDEXED,"
                    "title, keywords, headings, body, tokenize = \"unicode61 tokenchars '_'\")")
            else:
                self.db.execute(
                    "CREATE TABLE docs (doc_id TEXT, source TEXT, url TEXT,"
                    " fetched_at TEXT, title TEXT, keywords TEXT, headings TEXT, body TEXT)")
            self.db.execute("CREATE TABLE ids (doc_id TEXT PRIMARY KEY, rid INTEGER NOT NULL,"
                            " source TEXT NOT NULL, compact TEXT NOT NULL)")
            self.db.execute("CREATE INDEX ids_compact ON ids (compact)")
            self.db.execute("CREATE INDEX ids_source ON ids (source)")
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('mode', ?)", (self.mode,))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (INDEX_SCHEMA,))
            self.db.commit()
            self.rebuilt = True

    def _meta(self) -> tuple:
        try:
            kv = dict(self.db.execute("SELECT k, v FROM meta WHERE k IN ('mode', 'schema')"))
        except sqlite3.Error:
            return (None, None)
        return (kv.get("mode"), kv.get("schema"))

    def close(self):
        self.db.close()

    def upsert(self, source, url, did, title, fetched_at, md, commit=True):
        row = self.db.execute("SELECT rid FROM ids WHERE doc_id = ?", (did,)).fetchone()
        if row:
            self.db.execute("DELETE FROM docs WHERE rowid = ?", (row[0],))
        body = _body_text(md)
        cur = self.db.execute("INSERT INTO docs (doc_id, source, url, fetched_at, title, keywords,"
                              " headings, body) VALUES (?,?,?,?,?,?,?,?)",
                              (did, source, url, fetched_at, title, _camel_split(title),
                               _headings(body), body))
        self.db.execute("INSERT OR REPLACE INTO ids VALUES (?,?,?,?)",
                        (did, cur.lastrowid, source, _compact(title)))
        if commit:
            self.db.commit()

    def drop_source(self, source, commit=True):
        self.db.execute("DELETE FROM docs WHERE source = ?", (source,))
        self.db.execute("DELETE FROM ids WHERE source = ?", (source,))
        if commit:
            self.db.commit()

    def count(self, source=None) -> int:
        if source:
            return self.db.execute("SELECT count(*) FROM ids WHERE source=?", (source,)).fetchone()[0]
        return self.db.execute("SELECT count(*) FROM ids").fetchone()[0]

    @staticmethod
    def terms(query: str) -> list:
        """[(token, parts)]: each word of the query as typed (lower-cased),
        and its camelCase parts when it has more than one — so
        `rememberSaveable` matches the body token AND "remember saveable"
        matches the split title in `keywords`."""
        out = []
        for w in re.findall(r"\w+", query)[:12]:
            parts = [p.lower() for p in _camel_split(w).split()]
            out.append((w.lower(), parts if len(parts) > 1 else []))
        return out

    @staticmethod
    def _fts_term(tok, parts) -> str:
        if not parts:
            return f'"{tok}"'
        return f'("{tok}" OR (' + " AND ".join(f'"{p}"' for p in parts) + "))"

    def search(self, query: str, sources=None, limit: int = 8) -> list:
        """[{doc_id, source, url, fetched_at, title, snippet, score}], best first.

        Ranking: an exact title match first (asking for `NavigationStack`
        means the NavigationStack page, not the twenty pages that mention
        it), then bm25 with title >> keywords > headings > body."""
        q = " ".join(query.split())
        terms = self.terms(q)
        if not terms:
            return []
        rows = self._fts(q, terms, sources, limit) if self.fts else self._scan(terms, sources, limit, q)
        return rows[:limit]

    def _filter(self, sources, col="source"):
        if not sources:
            return "", []
        return f" AND {col} IN ({','.join('?' * len(sources))})", list(sources)

    def _exact(self, q, sources, limit) -> list:
        """Pages whose compacted title equals the compacted query: an
        indexed lookup in `ids`, then the rows by rowid."""
        where, args = self._filter(sources, "source")
        rids = [r[0] for r in self.db.execute(
            "SELECT rid FROM ids WHERE compact = ?" + where + " ORDER BY doc_id LIMIT ?",
            [_compact(q)] + args + [limit])]
        rows = []
        for rid in rids:
            r = self.db.execute("SELECT doc_id, source, url, fetched_at, title, substr(body, 1, 400) "
                                "FROM docs WHERE rowid = ?", (rid,)).fetchone()
            if r:
                rows.append(r)
        return rows

    def _fts(self, q, terms, sources, limit):
        where, args = self._filter(sources)
        out, seen = [], set()
        # 1. exact title (case/space/punctuation-insensitive)
        for r in self._exact(q, sources, limit):
            seen.add(r[0])
            out.append(self._row(r, _first_para(r[5]), 1000.0))
        # 2. bm25: all terms, then any term
        for joiner in (" AND ", " OR "):
            match = joiner.join(self._fts_term(t, p) for t, p in terms)
            try:
                cur = self.db.execute(
                    "SELECT doc_id, source, url, fetched_at, title, "
                    "snippet(docs, 7, '[', ']', ' … ', 14), bm25(docs, 0, 0, 0, 0, 12.0, 6.0, 3.0, 1.0) "
                    "FROM docs WHERE docs MATCH ?" + where + " ORDER BY 7 LIMIT ?",
                    [match] + args + [limit * 3])
                for r in cur:
                    if r[0] not in seen:
                        seen.add(r[0])
                        out.append(self._row(r, r[5], -float(r[6])))
            except sqlite3.Error:
                continue
            if len(out) >= limit:
                break
        return out

    def _scan(self, terms, sources, limit, q):
        where, args = self._filter(sources)
        compact = _compact(q)
        scored = []
        for r in self.db.execute("SELECT doc_id, source, url, fetched_at, title, keywords, "
                                 "headings, body FROM docs WHERE 1=1" + where, args):
            title, kw, hd, body = (x.lower() for x in r[4:8])
            if _compact(title) == compact:
                s = 1000.0
            else:
                fields = ((title, 12.0), (kw, 6.0), (hd, 3.0), (body, 1.0))
                def count(t, parts):
                    n = sum(len(re.findall(rf"\b{re.escape(t)}\b", f)) * w for f, w in fields)
                    if not n and parts:
                        n = min(sum(len(re.findall(rf"\b{re.escape(p)}\b", f)) * w for f, w in fields)
                                for p in parts)
                    return n
                hits = [count(t, p) for t, p in terms]
                if not any(hits):
                    continue
                s = sum(min(h, 40.0) for h in hits) * (2.0 if all(hits) else 1.0)
            scored.append((s, r))
        scored.sort(key=lambda x: (-x[0], x[1][0]))
        return [self._row(r[:5], _scan_snippet(r[7], terms), s) for s, r in scored[:limit]]

    @staticmethod
    def _row(r, snippet, score):
        return {"doc_id": r[0], "source": r[1], "url": r[2], "fetched_at": r[3],
                "title": r[4], "snippet": " ".join((snippet or "").split())[:320],
                "score": round(score, 3)}


def _first_para(body: str) -> str:
    for para in body.split("\n\n"):
        p = para.strip()
        if p and not p.startswith("#") and not p.startswith("*"):
            return p.lstrip("> ")
    return body[:200]


def _scan_snippet(body: str, terms) -> str:
    low = body.lower()
    for t, _p in terms:
        i = low.find(t)
        if i >= 0:
            a, b = max(0, i - 120), min(len(body), i + 180)
            return ("… " if a else "") + body[a:b] + (" …" if b < len(body) else "")
    return body[:200]


# ── reindex ──────────────────────────────────────────────────────────────

def _fill(store: Store, idx: Index, sources) -> int:
    """(Re)index `sources` from their pages on disk, in one transaction."""
    n = 0
    for name in sources:
        idx.drop_source(name, commit=False)
        m = store.load(name)
        for url, rec in m["pages"].items():
            try:
                md = store.read_page(name, rec["file"])
            except (OSError, ValueError, KeyError):
                continue
            idx.upsert(name, url, rec.get("doc_id") or doc_id(name, url), rec.get("title", ""),
                       rec.get("fetched_at", ""), md, commit=False)
            n += 1
    idx.db.commit()
    return n


def reindex(store: Store, sources=None) -> int:
    idx = Index(store.index_path())
    try:
        # A just-(re)created index holds nothing: fill ALL of it, not only `sources`.
        return _fill(store, idx, store.sources_on_disk() if idx.rebuilt or not sources else sources)
    finally:
        idx.close()


# ── bundles ──────────────────────────────────────────────────────────────

class BundleError(Exception):
    pass


def pack(store: Store, out_path: str, sources=None) -> dict:
    names = list(sources or store.sources_on_disk())
    if not names:
        raise BundleError("nothing to pack: no mirrored sources")
    files = {}
    for name in names:
        d = store.source_dir(name)
        if not os.path.isfile(os.path.join(d, "manifest.json")):
            raise BundleError(f"source {name!r} has no mirror here")
        for dp, _dn, fns in os.walk(d):
            for fn in sorted(fns):
                if fn.startswith(".tmp-"):
                    continue
                full = os.path.join(dp, fn)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                files[os.path.relpath(full, store.root).replace(os.sep, "/")] = sha256_file(full)
    bundle = {"schema": SCHEMA, "kind": "openbeast-docs-bundle", "created_at": now_iso(),
              "sources": names, "files": files}
    tmp = out_path + ".part"
    with tarfile.open(tmp, "w:gz") as tf:
        data = json.dumps(bundle, indent=1, sort_keys=True).encode()
        ti = tarfile.TarInfo(BUNDLE_NAME)
        ti.size, ti.mtime, ti.mode = len(data), int(time.time()), 0o644
        tf.addfile(ti, io.BytesIO(data))
        for rel in sorted(files):
            tf.add(os.path.join(store.root, rel), arcname=rel, recursive=False)
    os.replace(tmp, out_path)
    digest = sha256_file(out_path)
    with open(out_path + ".sha256", "w") as fh:
        fh.write(f"{digest}  {os.path.basename(out_path)}\n")
    return {"path": out_path, "sha256": digest, "files": len(files), "sources": names}


def unpack(store: Store, tar_path: str, expect_sha256: str = "") -> dict:
    digest = sha256_file(tar_path)
    want = expect_sha256.strip().lower()
    side = tar_path + ".sha256"
    if not want and os.path.isfile(side):
        with open(side) as fh:
            want = (fh.read().split() or [""])[0].lower()
    if not want:
        raise BundleError(f"no expected hash: pass --sha256 or keep {os.path.basename(side)} beside the tarball")
    if digest != want:
        raise BundleError(f"tarball sha256 {digest} does not match the expected {want}")
    os.makedirs(store.root, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".unpack-", dir=store.root)
    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            members = tf.getmembers()
            for mem in members:
                n = mem.name
                if (n.startswith("/") or ".." in n.split("/") or "\\" in n
                        or not (mem.isfile() or mem.isdir())):
                    raise BundleError(f"refusing unsafe member {n!r}")
            for mem in members:
                if mem.isdir():
                    continue
                dest = os.path.join(stage, *mem.name.split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                src = tf.extractfile(mem)
                with open(dest, "wb") as fh:
                    shutil.copyfileobj(src, fh)
        try:
            with open(os.path.join(stage, BUNDLE_NAME), encoding="utf-8") as fh:
                bundle = json.load(fh)
        except (OSError, ValueError) as e:
            raise BundleError(f"no readable {BUNDLE_NAME} in the bundle: {e}") from None
        files = bundle.get("files") or {}
        present = set()
        for dp, _dn, fns in os.walk(stage):
            for fn in fns:
                rel = os.path.relpath(os.path.join(dp, fn), stage).replace(os.sep, "/")
                if rel != BUNDLE_NAME:
                    present.add(rel)
        if present != set(files):
            raise BundleError(f"bundle contents differ from {BUNDLE_NAME}: "
                              f"extra {sorted(present - set(files))[:3]}, "
                              f"missing {sorted(set(files) - present)[:3]}")
        for rel, sha in files.items():
            if sha256_file(os.path.join(stage, rel)) != sha:
                raise BundleError(f"{rel}: sha256 mismatch")
        names = bundle.get("sources") or []
        for name in names:
            store.source_dir(name)                        # validates the name
            if not os.path.isfile(os.path.join(stage, name, "manifest.json")):
                raise BundleError(f"source {name!r} has no manifest in the bundle")
        for name in names:
            dest = store.source_dir(name)
            old = dest + ".old-unpack"
            shutil.rmtree(old, ignore_errors=True)
            if os.path.exists(dest):
                os.replace(dest, old)
            os.replace(os.path.join(stage, name), dest)
            shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    n = reindex(store, names)
    return {"sha256": digest, "sources": names, "files": len(files), "indexed": n,
            "created_at": bundle.get("created_at", "")}
