#!/usr/bin/env python3
"""Regenerate the skill index block in system-prompt-tools.md.

The index makes every skill visible to the model UPFRONT (name + when to
use it) so discovery costs zero tool round-trips — a 27B local model will
not spontaneously browse a skill library, but it will follow a menu it can
see (docs/PRODUCTION_ROADMAP.md §B, recommendation 2). The single `skill`
MCP tool backs the menu: skill() re-lists (with a fresh disk scan),
skill(name) pulls one body on demand.

Reads skills/*/SKILL.md frontmatter (name, description), rewrites the text
between the SKILL_INDEX markers in system-prompt-tools.md.

Usage:
  python3 scripts/generate-skill-index.py            # rewrite in place
  python3 scripts/generate-skill-index.py --check    # exit 1 if stale
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROMPT = REPO / "system-prompt-tools.md"
SKILLS = REPO / "skills"
START = "<!-- SKILL_INDEX_START (generated — edit skills/*/SKILL.md, then run scripts/generate-skill-index.py) -->"
END = "<!-- SKILL_INDEX_END -->"


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    fields = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fields[k.strip()] = v.strip()
    return fields


def build_index() -> str:
    rows = []
    for skill_md in sorted(SKILLS.glob("*/SKILL.md")):
        fm = frontmatter(skill_md)
        name = fm.get("name", skill_md.parent.name)
        desc = fm.get("description", "(no description)")
        rows.append(f"- **`{name}`** — {desc}")
    return "\n".join(rows)


def main() -> int:
    check = "--check" in sys.argv
    prompt = PROMPT.read_text(encoding="utf-8")
    if START not in prompt or END not in prompt:
        print(f"Error: markers not found in {PROMPT}", file=sys.stderr)
        return 2
    head, rest = prompt.split(START, 1)
    _, tail = rest.split(END, 1)
    new = f"{head}{START}\n{build_index()}\n{END}{tail}"
    if new == prompt:
        print("Skill index up to date.")
        return 0
    if check:
        print("Skill index is STALE — run scripts/generate-skill-index.py",
              file=sys.stderr)
        return 1
    PROMPT.write_text(new, encoding="utf-8")
    print(f"Skill index regenerated ({len(list(SKILLS.glob('*/SKILL.md')))} skills).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
