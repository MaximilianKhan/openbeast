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


class _R:
    def __init__(self, doc: dict, base: str):
        self.refs = doc.get("references") or {}
        self.base = base

    # inline ---------------------------------------------------------------
    def ref_title(self, ident: str) -> str:
        r = self.refs.get(ident) or {}
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
                r = self.refs.get(it.get("identifier", "")) or {}
                if r.get("alt"):
                    out.append(f"[image: {r['alt']}]")
            elif "inlineContent" in it:
                out.append(self.inline(it.get("inlineContent")))
        return "".join(out)

    # blocks ---------------------------------------------------------------
    def blocks(self, items, depth: int = 0) -> list:
        lines: list = []
        for b in items or []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "heading":
                lvl = min(6, max(2, int(b.get("level") or 2)))
                lines += [f"{'#' * lvl} {b.get('text', '')}", ""]
            elif t == "paragraph":
                txt = self.inline(b.get("inlineContent")).strip()
                if txt:
                    lines += [txt, ""]
            elif t == "codeListing":
                code = b.get("code") or []
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
                for n, item in enumerate(b.get("items") or [], 1):
                    inner = [ln for ln in self.blocks(item.get("content"), depth + 1) if ln.strip()]
                    bullet = f"{n}." if t == "orderedList" else "-"
                    if inner:
                        lines.append(f"{'  ' * depth}{bullet} {inner[0]}")
                        lines += [f"{'  ' * (depth + 1)}{ln}" for ln in inner[1:]]
                lines.append("")
            elif t == "termList":
                for item in b.get("items") or []:
                    term = self.inline((item.get("term") or {}).get("inlineContent"))
                    defn = " ".join(ln for ln in self.blocks((item.get("definition") or {}).get("content"))
                                    if ln.strip())
                    lines.append(f"- **{term}** — {defn}")
                lines.append("")
            elif t == "table":
                rows = b.get("rows") or []
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
                for tab in b.get("tabs") or []:
                    lines += [f"**{tab.get('title', '')}**", ""]
                    lines += self.blocks(tab.get("content"), depth)
            elif t == "links":
                for ident in b.get("items") or []:
                    lines.append(f"- {self.ref_title(ident)}")
                lines.append("")
            elif t in ("row",):
                for col in b.get("columns") or []:
                    lines += self.blocks(col.get("content"), depth)
            elif "content" in b and isinstance(b["content"], list):
                lines += self.blocks(b["content"], depth)
            elif "inlineContent" in b:
                txt = self.inline(b["inlineContent"]).strip()
                if txt:
                    lines += [txt, ""]
        return lines

    def ref_line(self, ident: str) -> str:
        r = self.refs.get(ident) or {}
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
    for p in platforms or []:
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
    """(title, markdown) for one DocC RenderNode."""
    r = _R(doc, base)
    meta = doc.get("metadata") or {}
    title = meta.get("title") or "(untitled)"
    lines = [f"# {title}"]
    kind = meta.get("roleHeading") or meta.get("role") or ""
    modules = ", ".join(m.get("name", "") for m in meta.get("modules") or [] if isinstance(m, dict))
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
    for sec in doc.get("primaryContentSections") or []:
        k = sec.get("kind")
        if k == "declarations":
            for d in sec.get("declarations") or []:
                code = "".join(t.get("text", "") for t in d.get("tokens") or [])
                lang = (d.get("languages") or ["swift"])[0]
                lines += ["## Declaration", "", f"```{lang}", code, "```", ""]
        elif k == "parameters":
            lines += ["## Parameters", ""]
            for prm in sec.get("parameters") or []:
                body = " ".join(ln for ln in r.blocks(prm.get("content")) if ln.strip())
                lines.append(f"- `{prm.get('name', '')}` — {body}")
            lines.append("")
        elif k in ("properties", "possibleValues", "restParameters", "restResponses"):
            lines += [f"## {sec.get('title') or k[0].upper() + k[1:]}", ""]
            for item in sec.get("items") or sec.get("values") or []:
                body = " ".join(ln for ln in r.blocks(item.get("content")) if ln.strip())
                lines.append(f"- `{item.get('name') or item.get('status', '')}` — {body}".rstrip(" —"))
            lines.append("")
        elif k == "content":
            lines += r.blocks(sec.get("content"))
        elif k == "mentions":
            ments = sec.get("mentions") or []
            if ments:
                lines += ["## Mentioned in", ""] + [f"- {r.ref_title(m)}" for m in ments] + [""]
    for heading, key in (("Topics", "topicSections"), ("Relationships", "relationshipsSections"),
                         ("See Also", "seeAlsoSections")):
        secs = doc.get(key) or []
        if not secs:
            continue
        lines += [f"## {heading}", ""]
        for s in secs:
            if s.get("title"):
                lines += [f"### {s['title']}", ""]
            lines += [r.ref_line(i) for i in s.get("identifiers") or []]
            lines.append("")
    # Collapse runs of blank lines; strip trailing spaces.
    out, blank = [], False
    for ln in lines:
        ln = ln.rstrip()
        if not ln:
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(ln)
    return title, "\n".join(out).strip() + "\n"


def links(doc: dict, base: str = HOST) -> list:
    """Page URLs this node points at, hierarchy first (topics), then the rest."""
    refs = doc.get("references") or {}
    order: list = []
    for key in ("topicSections", "seeAlsoSections", "relationshipsSections"):
        for s in doc.get(key) or []:
            order += s.get("identifiers") or []
    order += list(refs)
    seen, out = set(), []
    for ident in order:
        r = refs.get(ident) or {}
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
    meta = doc.get("metadata") or {}
    avail, depr = _availability(meta.get("platforms"))
    return {"kind": meta.get("roleHeading") or meta.get("role") or "",
            "deprecated": bool(depr or doc.get("deprecationSummary")),
            "availability": avail}
