"""Every in-repo Markdown link with a #section anchor under docs/ points at a
heading that exists (review 1124: two links named section numbers that had
moved, HARNESS_NOTES -> V5_INKLING2 §11 and STRATA -> DECISIONS §6).

GitHub's slug rule: lower-case, drop backticks and punctuation other than
'-' and '_', spaces to '-', a repeated heading gets -1, -2, ...
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\]\(([^)\s#]*\.md)?#([^)\s]+)\)")


def _slug(h: str) -> str:
    h = re.sub(r"`", "", h.strip().lower())
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", h)
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def _anchors(path: Path, cache: dict) -> set:
    if path not in cache:
        out, seen, fence = set(), {}, False
        for ln in path.read_text(errors="ignore").splitlines():
            if ln.startswith("```"):
                fence = not fence
            if fence:
                continue
            m = re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", ln)
            if m:
                s = _slug(m.group(1))
                n = seen.get(s, 0)
                seen[s] = n + 1
                out.add(s if n == 0 else f"{s}-{n}")
            out.update(re.findall(r'<a (?:name|id)="([^"]+)"', ln))
        cache[path] = out
    return cache[path]


def test_slug_rule():
    assert _slug("12. The improved arm I_T3 (2026-11-24)") == "12-the-improved-arm-i_t3-2026-11-24"
    assert _slug("11. Basalt PR #632 as our local patch for Inkling") == \
        "11-basalt-pr-632-as-our-local-patch-for-inkling"


def test_docs_section_links_resolve():
    cache, bad = {}, []
    for p in sorted((ROOT / "docs").rglob("*.md")):
        for m in LINK.finditer(p.read_text(errors="ignore")):
            tgt = (p.parent / m.group(1)).resolve() if m.group(1) else p
            if not tgt.exists():
                continue          # missing files are another check's business
            if m.group(2) not in _anchors(tgt, cache):
                bad.append(f"{p.relative_to(ROOT)} -> {m.group(1) or ''}#{m.group(2)}")
    assert not bad, "broken section links:\n" + "\n".join(bad)
