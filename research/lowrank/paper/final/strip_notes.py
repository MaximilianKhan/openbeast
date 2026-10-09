#!/usr/bin/env python3
"""One-time: copy draft/*.md into final/sections/ with the dated editorial
notes removed. The draft keeps its revision history; the final does not."""
import re, pathlib
here = pathlib.Path(__file__).parent
NOTE = re.compile(r"\s*(?<!\*)\*\((?:(?!\)\*).)*?20\d\d-\d\d-\d\d(?:(?!\)\*).)*?\)\*", re.S)
DASH = re.compile(r"\s*\*—[^*]*?20\d\d-\d\d-\d\d[^*]*?\*")
for src in sorted((here.parent / "draft").glob("0[1-7]*.md")):
    t = src.read_text()
    t = NOTE.sub("", t)
    t = DASH.sub("", t)
    (here / "sections" / src.name).write_text(t)
    print(src.name, len(t.split()))
