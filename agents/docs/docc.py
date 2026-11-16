"""Apple DocC JSON -> clean markdown.

developer.apple.com/documentation/<path> is a JavaScript app; what it renders
is fetched from developer.apple.com/tutorials/data/documentation/<path>.json
(measured 2026-11-15: 200, application/json, ETag + Last-Modified). That JSON
is the DocC "RenderNode" format (schemaVersion 0.3.0):

  metadata            title, roleHeading, symbolKind, platforms[] (introducedAt,
                      deprecatedAt, message, beta), modules[]
  abstract            inline content
  deprecationSummary  block content, present when the symbol is deprecated
  primaryContentSections
                      declarations (tokens), parameters, content (the
                      discussion: headings, paragraphs, codeListing, aside,
                      lists, tables), mentions, properties, possibleValues
  topicSections / seeAlsoSections / relationshipsSections
                      groups of identifiers into `references`
  references          id -> {title, url, abstract, kind, role, type}

Pure functions only — no I/O — so the golden tests pin the rendering.
"""
from __future__ import annotations

import urllib.parse

from .htmlmd import list_item

HOST = "https://developer.apple.com"
DATA_PREFIX = "/tutorials/data"
_SAFE = "/:()!$&'*+,;=@-._~%"


def json_url(page_url: str) -> str:
    """https://developer.apple.com/documentation/swiftui/navigationstack ->
    https://developer.apple.com/tutorials/data/documentation/swiftui/navigationstack.json"""
    p = urllib.parse.urlsplit(page_url)
    path = urllib.parse.unquote(p.path.rstrip("/")).lower()
    # The page URL was normalized before it was scope-checked; a dot segment
    # here could only come from a double-encoded one (`%252e%252e`). Refuse it
    # rather than let `..` climb out of /tutorials/data/documentation/.
    if any(seg in (".", "..") for seg in path.split("/")):
        raise ValueError(f"{page_url}: dot segment in a DocC path")
    # Re-quote with '%' NOT safe: after unquote a '%' is a literal percent.
    path = urllib.parse.quote(path, safe=_SAFE.replace("%", ""))
    return f"{p.scheme}://{p.netloc}{DATA_PREFIX}{path}.json"


def page_url(base: str, path: str) -> str:
    """A reference's site-relative url -> the human-facing page URL."""
    p = urllib.parse.urlsplit(base)
    path = path.split("#", 1)[0]
    return f"{p.scheme}://{p.netloc}{urllib.parse.quote(urllib.parse.unquote(path), safe=_SAFE)}"


def _d(x) -> dict:
    """x when it is a dict, else {} — the RenderNode schema is not a
    contract (schemaVersion drifts), so every container is checked, not
    assumed: an unexpected shape renders as nothing, never as a crash."""
    return x if isinstance(x, dict) else {}


def _l(x) -> list:
    return x if isinstance(x, list) else []


def _dicts(x) -> list:
    return [i for i in _l(x) if isinstance(i, dict)]


def _level(v) -> int:
    try:
        return min(6, max(2, int(v or 2)))
    except (TypeError, ValueError):
        return 2


class _R:
    def __init__(self, doc: dict, base: str):
        self.refs = _d(doc.get("references"))
        self.base = base

    # inline ---------------------------------------------------------------
    def ref_title(self, ident) -> str:
        ident = ident if isinstance(ident, str) else ""
        r = _d(self.refs.get(ident))
        t = r.get("title")
        if not t:
            t = ident.rsplit("/", 1)[-1]
        if r.get("kind") == "symbol" or r.get("role") == "symbol":
            return f"`{t}`"
        return t

    def inline(self, items) -> str:
        out = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            t = it.get("type")
            if t == "text":
                out.append(it.get("text", ""))
            elif t == "codeVoice":
                out.append(f"`{it.get('code', '')}`")
            elif t == "reference":
                ov = it.get("overridingTitle")
                out.append(ov if ov else self.ref_title(it.get("identifier", "")))
            elif t in ("emphasis", "newTerm"):
                out.append(f"*{self.inline(it.get('inlineContent'))}*")
            elif t in ("strong", "inlineHead"):
                out.append(f"**{self.inline(it.get('inlineContent'))}**")
            elif t == "link":
                title = it.get("title") or it.get("destination", "")
                out.append(f"[{title}]({it.get('destination', '')})")
            elif t == "image":
                r = _d(self.refs.get(it.get("identifier", "")))
                if r.get("alt"):
                    out.append(f"[image: {r['alt']}]")
            elif "inlineContent" in it:
                out.append(self.inline(it.get("inlineContent")))
        return "".join(out)

    # blocks ---------------------------------------------------------------
    def blocks(self, items, depth: int = 0) -> list:
        lines: list = []
        for b in _dicts(items):
            t = b.get("type")
            if t == "heading":
                lvl = _level(b.get("level"))
                lines += [f"{'#' * lvl} {b.get('text', '')}", ""]
            elif t == "paragraph":
                txt = self.inline(b.get("inlineContent")).strip()
                if txt:
                    lines += [txt, ""]
            elif t == "codeListing":
                code = _l(b.get("code"))
                lines += [f"```{b.get('syntax') or ''}", *[str(c) for c in code], "```", ""]
            elif t == "aside":
                name = b.get("name") or (b.get("style") or "note").capitalize()
                inner = self.blocks(b.get("content"), depth)
                body = [ln for ln in inner if ln.strip()]
                if body:
                    lines.append(f"> **{name}:** {body[0]}")
                    lines += [f"> {ln}" for ln in body[1:]]
                    lines.append("")
            elif t in ("unorderedList", "orderedList"):
                for n, item in enumerate(_dicts(b.get("items")), 1):
                    bullet = f"{n}." if t == "orderedList" else "-"
                    lines += list_item(bullet, self.blocks(item.get("content"), depth + 1))
                lines.append("")
            elif t == "termList":
                for item in _dicts(b.get("items")):
                    term = self.inline(_d(item.get("term")).get("inlineContent"))
                    defn = " ".join(ln for ln in self.blocks(_d(item.get("definition")).get("content"))
                                    if ln.strip())
                    lines.append(f"- **{term}** — {defn}")
                lines.append("")
            elif t == "table":
                rows = [r for r in _l(b.get("rows")) if isinstance(r, list)]
                cells = [[" ".join(ln for ln in self.blocks(c) if ln.strip()).replace("|", "\\|")
                          for c in row] for row in rows]
                if cells:
                    width = max(len(r) for r in cells)
                    cells = [r + [""] * (width - len(r)) for r in cells]
                    lines.append("| " + " | ".join(cells[0]) + " |")
                    lines.append("|" + "---|" * width)
                    lines += ["| " + " | ".join(r) + " |" for r in cells[1:]]
                    lines.append("")
            elif t == "tabNavigator":
                for tab in _dicts(b.get("tabs")):
                    lines += [f"**{tab.get('title', '')}**", ""]
                    lines += self.blocks(tab.get("content"), depth)
            elif t == "links":
                for ident in _l(b.get("items")):
                    lines.append(f"- {self.ref_title(ident)}")
                lines.append("")
            elif t in ("row",):
                for col in _dicts(b.get("columns")):
                    lines += self.blocks(col.get("content"), depth)
            elif "content" in b and isinstance(b["content"], list):
                lines += self.blocks(b["content"], depth)
            elif "inlineContent" in b:
                txt = self.inline(b["inlineContent"]).strip()
                if txt:
                    lines += [txt, ""]
        return lines

    def ref_line(self, ident) -> str:
        r = _d(self.refs.get(ident if isinstance(ident, str) else ""))
        title = self.ref_title(ident)
        if r.get("deprecated"):
            title += " (deprecated)"
        line = f"- {title}"
        ab = self.inline(r.get("abstract")).strip()
        if ab:
            line += f" — {ab}"
        if r.get("url"):
            line += f" <{page_url(self.base, r['url'])}>"
        return line


def _availability(platforms) -> tuple:
    avail, depr = [], []
    for p in _l(platforms):
        if not isinstance(p, dict) or not p.get("name"):
            continue
        name = p["name"]
        if p.get("unavailable"):
            avail.append(f"{name} unavailable")
            continue
        s = f"{name} {p['introducedAt']}+" if p.get("introducedAt") else name
        if p.get("beta"):
            s += " (beta)"
        avail.append(s)
        if p.get("deprecatedAt") or p.get("deprecated"):
            d = f"{name} {p.get('deprecatedAt', '')}".strip()
            if p.get("message"):
                d += f" ({p['message']})"
            depr.append(d)
    return avail, depr


def to_markdown(doc: dict, base: str = HOST) -> tuple:
    """(title, markdown) for one DocC RenderNode. Raises ValueError for a
    body that is not a RenderNode at all (a JSON list, null, a string)."""
    if not isinstance(doc, dict):
        raise ValueError(f"not a DocC RenderNode (a JSON {type(doc).__name__})")
    r = _R(doc, base)
    meta = _d(doc.get("metadata"))
    title = str(meta.get("title") or "(untitled)")
    lines = [f"# {title}"]
    kind = meta.get("roleHeading") or meta.get("role") or ""
    modules = ", ".join(str(m.get("name", "")) for m in _dicts(meta.get("modules")))
    sub = " · ".join(x for x in (kind, modules) if x)
    if sub:
        lines.append(f"*{sub}*")
    lines.append("")
    ab = r.inline(doc.get("abstract")).strip()
    if ab:
        lines += [ab, ""]
    avail, depr = _availability(meta.get("platforms"))
    if avail:
        lines += ["**Availability:** " + " · ".join(avail), ""]
    if depr or doc.get("deprecationSummary"):
        # One line per distinct message, not one per platform: seven copies
        # of "use NavigationStack instead" are noise.
        groups: dict = {}
        for d in depr:
            plat, _, msg = d.partition(" (")
            groups.setdefault(msg.rstrip(")"), []).append(plat)
        text = "; ".join(", ".join(p) + (f" — {m}" if m else "") for m, p in groups.items())
        lines.append("**DEPRECATED:** " + (text or "see below"))
        summary = [ln for ln in r.blocks(doc.get("deprecationSummary")) if ln.strip()]
        lines += [f"> {ln}" for ln in summary]
        lines.append("")
    for sec in _dicts(doc.get("primaryContentSections")):
        k = sec.get("kind")
        if k == "declarations":
            for d in _dicts(sec.get("declarations")):
                code = "".join(str(t.get("text", "")) for t in _dicts(d.get("tokens")))
                lang = (_l(d.get("languages")) or ["swift"])[0]
                lines += ["## Declaration", "", f"```{lang}", code, "```", ""]
        elif k == "parameters":
            lines += ["## Parameters", ""]
            for prm in _dicts(sec.get("parameters")):
                body = " ".join(ln for ln in r.blocks(prm.get("content")) if ln.strip())
                lines.append(f"- `{prm.get('name', '')}` — {body}")
            lines.append("")
        elif k in ("properties", "possibleValues", "restParameters", "restResponses"):
            lines += [f"## {sec.get('title') or k[0].upper() + k[1:]}", ""]
            for item in _dicts(sec.get("items")) or _dicts(sec.get("values")):
                body = " ".join(ln for ln in r.blocks(item.get("content")) if ln.strip())
                lines.append(f"- `{item.get('name') or item.get('status', '')}` — {body}".rstrip(" —"))
            lines.append("")
        elif k == "content":
            lines += r.blocks(sec.get("content"))
        elif k == "mentions":
            ments = _l(sec.get("mentions"))
            if ments:
                lines += ["## Mentioned in", ""] + [f"- {r.ref_title(m)}" for m in ments] + [""]
    for heading, key in (("Topics", "topicSections"), ("Relationships", "relationshipsSections"),
                         ("See Also", "seeAlsoSections")):
        secs = _dicts(doc.get(key))
        if not secs:
            continue
        lines += [f"## {heading}", ""]
        for s in secs:
            if s.get("title"):
                lines += [f"### {s['title']}", ""]
            lines += [r.ref_line(i) for i in _l(s.get("identifiers"))]
            lines.append("")
    # Collapse runs of blank lines (outside code fences); strip trailing spaces.
    out, blank, fence = [], False, False
    for ln in lines:
        ln = ln.rstrip()
        if ln.lstrip().startswith("```"):
            fence = not fence
        if not ln and not fence:
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(ln)
    return title, "\n".join(out).strip() + "\n"


def links(doc: dict, base: str = HOST) -> list:
    """Page URLs this node points at, hierarchy first (topics), then the rest."""
    if not isinstance(doc, dict):
        return []
    refs = _d(doc.get("references"))
    order: list = []
    for key in ("topicSections", "seeAlsoSections", "relationshipsSections"):
        for s in _dicts(doc.get(key)):
            order += [i for i in _l(s.get("identifiers")) if isinstance(i, str)]
    order += list(refs)
    seen, out = set(), []
    for ident in order:
        r = _d(refs.get(ident))
        u = r.get("url")
        if r.get("type") != "topic" or not isinstance(u, str) or not u.startswith("/"):
            continue
        full = page_url(base, u)
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def summary(doc: dict) -> dict:
    """Small facts the index and `read` header carry."""
    meta = _d(_d(doc).get("metadata"))
    avail, depr = _availability(meta.get("platforms"))
    return {"kind": meta.get("roleHeading") or meta.get("role") or "",
            "deprecated": bool(depr or _d(doc).get("deprecationSummary")),
            "availability": avail}
