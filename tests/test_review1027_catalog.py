"""opencode.json must not under-advertise or over-advertise a model's
context (review 2026-10-27, R9-07).

The serve scripts were raised to their measured ceilings on 2026-07-07 and
two catalog rows kept the old limits, so OpenCode compacted early. A row
may not claim more than the script's literal `-c` either: OpenCode would
then overflow the server.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CTX = re.compile(r"^\s*-c\s+(\d+)\b", re.M)


def _catalog():
    return json.loads((ROOT / "opencode.json").read_text())["provider"]["llama-cpp"]["models"]


def _script_ctx(slug):
    m = _CTX.search((ROOT / "scripts" / f"serve-{slug}.sh").read_text())
    return int(m.group(1)) if m else None


def test_catalog_context_matches_the_serve_script():
    checked = 0
    for slug, row in _catalog().items():
        ctx = (row.get("limit") or {}).get("context")
        c = _script_ctx(slug)
        if ctx is None or c is None:
            continue
        assert ctx == c, f"{slug}: opencode.json says {ctx}, serve-{slug}.sh -c {c}"
        checked += 1
    assert checked >= 10


def test_the_two_drifted_rows_are_fixed():
    cat = _catalog()
    assert cat["qwen-27b-mtp-q5"]["limit"]["context"] == _script_ctx("qwen-27b-mtp-q5") == 294912
    assert cat["qwen-35b-a3b-mtp"]["limit"]["context"] == _script_ctx("qwen-35b-a3b-mtp") == 524288
