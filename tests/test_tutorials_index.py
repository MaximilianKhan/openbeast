"""docs/TUTORIALS.md: the index table at the top links every walkthrough.

A walkthrough missing from the table (beast-island was, for weeks) cannot be
found by a reader navigating from it.
"""
import re
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent / "docs" / "TUTORIALS.md"


def _anchor(heading: str) -> str:
    # GitHub's slug: lowercase, drop punctuation except - and space, space -> -
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def test_every_walkthrough_is_in_the_index_table():
    text = DOC.read_text()
    sections = [h for h in re.findall(r"^## (.+)$", text, re.MULTILINE)]
    links = set(re.findall(r"\]\(#([^)]+)\)", text.split("\n## ", 1)[0]))
    assert sections, "no walkthrough sections found"
    missing = [h for h in sections if _anchor(h) not in links]
    assert not missing, f"walkthroughs absent from the index table: {missing}"
