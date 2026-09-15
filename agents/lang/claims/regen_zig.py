#!/usr/bin/env python3
"""Regenerate agents/lang/claims/zig-0.16.json from the zig fixture manifest.

The fixtures under tests/fixtures/zig016/ are the single source of truth for
what the zig pack claims. This script projects them into the generic
beast-lang claim format so the cross-language verifier and the zig-specific
test suite can never disagree about what was claimed — a second hand-written
copy of 15 entries would drift within a week, and a drifted claim set is one
that reports VERIFIED about something nobody checked.

  python3 agents/lang/claims/regen_zig.py [--check]

--check exits non-zero if the committed JSON differs from a regeneration,
which is how CI notices someone edited one side only.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
MANIFEST = os.path.join(ROOT, "tests", "fixtures", "zig016", "MANIFEST.json")
OUT = os.path.join(HERE, "zig-0.16.json")

COMMENT = ("GENERATED from tests/fixtures/zig016/MANIFEST.json by "
           "agents/lang/claims/regen_zig.py — do not hand-edit. The fixtures stay "
           "the single source of truth; this file is the beast-lang view of them, "
           "so the generic verifier and the zig-specific test suite can never "
           "disagree about what was claimed.")


def build() -> dict:
    man = json.load(open(MANIFEST))
    return {
        "_comment": COMMENT,
        "lang": "zig",
        "toolchain": man["zig"],
        "verified": man["verified"],
        "fixture_dir": "../../../tests/fixtures/zig016",
        "rule": man["rule"],
        "claims": [
            {"id": e["entry"], "topic": e["entry"],
             "old": e.get("old") or [], "new": e.get("new") or [],
             # The one line that would have prevented the mistake. A claim
             # without one is verifiable but UNDELIVERABLE — escalation
             # refuses to attach a card it cannot state in a sentence, which
             # is how every zig claim silently matched nothing at first.
             "summary": e.get("summary", ""),
             "doc_must_contain": e.get("pack_must_contain") or []}
            for e in man["entries"]
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    fresh = build()
    if a.check:
        if not os.path.exists(OUT):
            print(f"{OUT} is missing", file=sys.stderr)
            return 1
        if json.load(open(OUT)) != fresh:
            print(f"{os.path.basename(OUT)} is stale — regenerate it "
                  f"(python3 agents/lang/claims/regen_zig.py)", file=sys.stderr)
            return 1
        print(f"{os.path.basename(OUT)} matches the fixture manifest")
        return 0
    with open(OUT, "w") as fh:
        json.dump(fresh, fh, indent=2)
    print(f"wrote {len(fresh['claims'])} claims to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
