"""Static HTML -> clean markdown (main content only), and the markdown
adapter's cleanup. Pure functions; stdlib html.parser only.

The HTML path builds a small, forgiving tree, picks the content root with a
tiny selector language (`tag`, `.class`, `#id`, `tag.class`, `[attr=value]`),
removes chrome (scripts, nav, buttons, anything aria-hidden or listed in the
source's strip_selectors), and renders headings, paragraphs, lists, code
blocks (with their language), tables and asides. Links are kept as their
text: a page full of URLs is noise to a model, and link DISCOVERY is a
separate pass over the whole page.
"""
from __future__ import annotations

import re
import urllib.parse
from html.parser import HTMLParser

from .urls import normalize_url

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "param", "source", "track", "wbr"}
_DROP = {"script", "style", "noscript", "svg", "template", "iframe", "button", "form",
         "nav", "header", "footer", "select", "option", "canvas", "video", "audio",
         "object", "dialog", "menu"}
_BLOCK = {"p", "div", "section", "article", "main", "aside", "blockquote", "ul", "ol",
          "li", "pre", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "dl", "dt",
          "dd", "figure", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
          "details", "summary"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, dict(attrs or {}), [], parent

    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def text(self) -> str:
        out = []
        for c in self.children:
            out.append(c if isinstance(c, str) else c.text())
        return "".join(out)

    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, [(k, v or "") for k, v in attrs], self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, [(k, v or "") for k, v in attrs], self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:   # unmatched end tags are ignored
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(html: str) -> Node:
    b = _Builder()
    b.feed(html)
    b.close()
    return b.root


# ── selectors ────────────────────────────────────────────────────────────

_SEL = re.compile(r"^(?P<tag>[a-zA-Z][\w-]*)?(?:\.(?P<cls>[\w-]+))?(?:#(?P<id>[\w-]+))?"
                  r"(?:\[(?P<attr>[\w-]+)=(?P<val>[^\]]+)\])?$")


def matches(node: Node, selector: str) -> bool:
    m = _SEL.match(selector.strip())
    if not m or not any(m.groupdict().values()):
        return False
    g = m.groupdict()
    if g["tag"] and node.tag != g["tag"].lower():
        return False
    if g["cls"] and g["cls"] not in node.classes():
        return False
    if g["id"] and node.attrs.get("id") != g["id"]:
        return False
    if g["attr"] and node.attrs.get(g["attr"]) != g["val"].strip("\"'"):
        return False
    return True


def select(root: Node, selector: str):
    for n in root.walk():
        if isinstance(n, Node) and matches(n, selector):
            return n
    return None


# ── rendering ────────────────────────────────────────────────────────────

def _code_lang(node: Node) -> str:
    for n in (node, *(c for c in node.children if isinstance(c, Node))):
        for k in ("syntax", "data-lang", "lang"):
            if n.attrs.get(k):
                return n.attrs[k].lower()
        for c in n.classes():
            m = re.match(r"(?:language|lang|highlight)-([\w+#-]+)$", c)
            if m:
                return m.group(1).lower()
    return ""


_ASIDE_LABEL = {"note": "Note", "warning": "Warning", "caution": "Caution", "tip": "Tip",
                "important": "Important", "key-term": "Key term", "special": "Note",
                "success": "Tip", "objective": "Objective"}


def _has_block(n: Node) -> bool:
    """A custom element (<devsite-code>, <x-tab>) wrapping real blocks is a
    block container, or its <pre> would be flattened into a paragraph."""
    return any(isinstance(d, Node) and d is not n and d.tag in _BLOCK for d in n.walk())


class _Render:
    def __init__(self, strip):
        self.strip = list(strip)

    def dropped(self, n: Node) -> bool:
        if n.tag in _DROP or n.attrs.get("aria-hidden") == "true" or "hidden" in n.attrs:
            return True
        return any(matches(n, s) for s in self.strip)

    def raw(self, n) -> str:
        """Text with whitespace preserved (code), chrome still removed."""
        if isinstance(n, str):
            return n
        if self.dropped(n):
            return ""
        if n.tag == "br":
            return "\n"
        return "".join(self.raw(c) for c in n.children)

    def inline(self, n) -> str:
        if isinstance(n, str):
            return re.sub(r"\s+", " ", n)
        if self.dropped(n):
            return ""
        if n.tag == "br":
            return "\n"
        if n.tag == "img":
            return ""
        inner = "".join(self.inline(c) for c in n.children)
        if n.tag == "code":
            t = n.text().strip()
            return f"`{t}`" if t else ""
        if n.tag in ("strong", "b") and inner.strip():
            return f"**{inner.strip()}**"
        if n.tag in ("em", "i") and inner.strip():
            return f"*{inner.strip()}*"
        return inner

    def blocks(self, n: Node, depth: int = 0) -> list:
        """Lines for the children of n. Inline runs are joined into paragraphs."""
        lines: list = []
        run: list = []

        def flush():
            txt = "".join(run).strip()
            run.clear()
            if txt:
                lines.extend([re.sub(r"[ \t]+", " ", ln).strip() for ln in txt.split("\n")])
                lines.append("")

        for c in n.children:
            if isinstance(c, str):
                run.append(self.inline(c))
                continue
            if self.dropped(c):
                continue
            if c.tag not in _BLOCK and not _has_block(c):
                run.append(self.inline(c))
                continue
            flush()
            lines += self.block(c, depth)
        flush()
        return lines

    def block(self, c: Node, depth: int) -> list:
        t = c.tag
        if t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            txt = re.sub(r"\s+", " ", "".join(self.inline(x) for x in c.children)).strip()
            return [f"{'#' * int(t[1])} {txt}", ""] if txt else []
        if t == "pre":
            code = self.raw(c).strip("\n")
            return [f"```{_code_lang(c)}", code, "```", ""] if code.strip() else []
        if t in ("ul", "ol"):
            out, n = [], 0
            for li in c.children:
                if not isinstance(li, Node) or li.tag != "li" or self.dropped(li):
                    continue
                n += 1
                inner = [ln for ln in self.blocks(li, depth + 1) if ln.strip()]
                if not inner:
                    continue
                bullet = f"{n}." if t == "ol" else "-"
                out.append(f"{'  ' * depth}{bullet} {inner[0].lstrip()}")
                out += [ln if ln.startswith("  ") else f"{'  ' * (depth + 1)}{ln}" for ln in inner[1:]]
            return out + [""] if out else []
        if t == "table":
            rows = [r for r in c.walk() if isinstance(r, Node) and r.tag == "tr"]
            cells = []
            for r in rows:
                cs = [x for x in r.children if isinstance(x, Node) and x.tag in ("td", "th")]
                cells.append([re.sub(r"\s+", " ", " ".join(ln for ln in self.blocks(x) if ln.strip()))
                              .replace("|", "\\|") for x in cs])
            cells = [r for r in cells if any(x.strip() for x in r)]
            if not cells:
                return []
            w = max(len(r) for r in cells)
            cells = [r + [""] * (w - len(r)) for r in cells]
            return (["| " + " | ".join(cells[0]) + " |", "|" + "---|" * w]
                    + ["| " + " | ".join(r) + " |" for r in cells[1:]] + [""])
        if t in ("aside", "blockquote"):
            inner = [ln for ln in self.blocks(c, depth) if ln.strip()]
            if not inner:
                return []
            label = next((v for k, v in _ASIDE_LABEL.items() if k in c.classes()), "")
            first = inner[0]
            if label and not first.lower().lstrip("*").startswith(label.lower()):
                first = f"**{label}:** {first}"
            return [f"> {first}"] + [f"> {ln}" for ln in inner[1:]] + [""]
        if t == "dt":
            txt = "".join(self.inline(x) for x in c.children).strip()
            return [f"**{txt}**"] if txt else []
        if t == "dd":
            return [f": {ln}" if ln.strip() else ln for ln in self.blocks(c, depth)]
        if t == "hr":
            return ["---", ""]
        return self.blocks(c, depth)


def _collapse(lines) -> str:
    out, blank, fence = [], False, False
    for ln in lines:
        if ln.startswith("```"):
            fence = not fence
        if not fence:
            ln = ln.rstrip()
        if not ln.strip() and not fence:
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(ln)
    return "\n".join(out).strip() + "\n"


_SUFFIXES = re.compile(r"\s*[|\-–—]\s*(Android Developers|Apple Developer.*|Documentation)\s*$")


def html_to_markdown(html: str, content_selectors=(), strip_selectors=()) -> dict:
    """{title, markdown, description, updated} for one HTML page."""
    root = parse(html)
    title = ""
    h1 = select(root, "h1")
    if h1 is not None:
        # Rendered, not .text(): chrome nested in the heading (bookmark
        # buttons, tooltips, hidden widgets) is dropped the same way as in
        # the body.
        r = _Render(strip_selectors)
        title = re.sub(r"\s+", " ", "".join(r.inline(c) for c in h1.children)).strip()
    if not title:
        for n in root.walk():
            if n.tag == "meta" and n.attrs.get("property") == "og:title":
                title = n.attrs.get("content", "").strip()
                break
    if not title:
        t = select(root, "title")
        title = _SUFFIXES.sub("", re.sub(r"\s+", " ", t.text()).strip()) if t else ""
    desc = ""
    for n in root.walk():
        if n.tag == "meta" and n.attrs.get("name") == "description":
            desc = n.attrs.get("content", "").strip()
            break
    m = re.search(r"Last updated (\d{4}-\d{2}-\d{2})", html)
    updated = m.group(1) if m else ""
    content = None
    for sel in (*content_selectors, "main", "article", "body"):
        content = select(root, sel)
        if content is not None:
            break
    content = content or root
    lines = _Render(strip_selectors).blocks(content)
    # The page title is printed once, at the top; drop the content's own copy.
    while lines and (not lines[0].strip() or lines[0].lstrip("# ").strip() == title):
        lines.pop(0)
    head = [f"# {title or '(untitled)'}", ""]
    if desc:
        head += [f"> {desc}", ""]
    return {"title": title or "(untitled)", "markdown": _collapse(head + lines),
            "description": desc, "updated": updated}


def html_links(html: str, base: str) -> list:
    """Every <a href> in the page, absolute, fragment- and query-free, in order."""
    out, seen = [], set()
    for n in parse(html).walk():
        if n.tag != "a" or not n.attrs.get("href"):
            continue
        u = canonical(urllib.parse.urljoin(base, n.attrs["href"]))
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def canonical(url: str, markdown_suffix: str = "") -> str:
    """Normalize (agents/docs/urls.py: dot segments, %2e, default port,
    userinfo refused), drop fragment and query (?hl=, ?authuser=) and, for
    the markdown adapter, the .md.txt suffix — one page, one key. "" when
    the URL has no safe spelling."""
    n = normalize_url(url, drop_query=True)
    if not n:
        return ""
    p = urllib.parse.urlsplit(n)
    path = p.path or "/"
    if markdown_suffix and path.endswith(markdown_suffix):
        path = path[: -len(markdown_suffix)]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, "", ""))


# ── the markdown adapter ─────────────────────────────────────────────────

# Link text may wrap onto the next line (Android's notes do: "[`RecyclerView`\n> widget](...)").
_MD_LINK = re.compile(r"(!?)\[([^\]]{0,300})\]\(((?:[^()\s]|\([^()\s]*\))+)\)")


def markdown_links(md: str, base: str, markdown_suffix: str = ".md.txt") -> list:
    out, seen = [], set()
    for m in _MD_LINK.finditer(md):
        if m.group(1):
            continue
        u = canonical(urllib.parse.urljoin(base, m.group(3)), markdown_suffix)
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def clean_markdown(md: str, title_hint: str = "", description: str = "") -> dict:
    """{title, markdown}: the site's own markdown, with links reduced to their
    text, raw <br /> and index pointers removed, and a title on top."""
    lines = []
    for ln in md.replace("\r\n", "\n").split("\n"):
        if re.match(r"^>\s*Documentation index:", ln):
            continue
        if ln.strip() in ("<br />", "<br>", "<br/>"):
            continue
        lines.append(ln)
    body = "\n".join(lines)
    fence_parts = re.split(r"(^```.*?^```)", body, flags=re.S | re.M)
    for i in range(0, len(fence_parts), 2):          # outside code fences only
        fence_parts[i] = _MD_LINK.sub(lambda m: "" if m.group(1) else m.group(2), fence_parts[i])
    body = "".join(fence_parts)
    # The page's own H1 is the authority; the index's title is the fallback
    # for pages that have none (Android's .md.txt files usually do not).
    m = re.search(r"^#\s+(.+)$", body, re.M)
    title = m.group(1).strip() if m else title_hint
    if not re.match(r"^\s*#\s", body):
        head = f"# {title or '(untitled)'}\n\n" + (f"> {description}\n\n" if description else "")
        body = head + body.lstrip("\n")
    return {"title": title or "(untitled)", "markdown": _collapse(body.split("\n"))}


def llms_index(text: str, base: str, markdown_suffix: str = ".md.txt") -> list:
    """[(page_url, title, description)] from an llms.txt index."""
    out = []
    for m in re.finditer(r"^\s*-\s*\[([^\]]+)\]\(([^)\s]+)\)\s*:?\s*(.*)$", text, re.M):
        u = canonical(urllib.parse.urljoin(base, m.group(2)), markdown_suffix)
        if u:
            out.append((u, m.group(1).strip(), m.group(3).strip()))
    return out


def sitemap_locs(xml: str) -> list:
    """(<loc>s, is_index) from a sitemap or sitemap index. Regex, not a DOM:
    an Android child sitemap is tens of MB."""
    is_index = "<sitemapindex" in xml[:2000]
    return [html_unescape(x.strip()) for x in re.findall(r"<loc>([^<]+)</loc>", xml)], is_index


def html_unescape(s: str) -> str:
    import html as _h  # noqa: PLC0415
    return _h.unescape(s)
