"""Escalation: a compiler error selects the card that fixes it.

This is the layer the whole beast-lang delivery argument rests on. The repo
measured that local models do not call optional tools, so *asking* a model to
look something up does not work — but a COMPILE ERROR is not a request, it is
proof the model already holds a stale belief, at the moment it holds it.

The matching index is GENERATED, not hand-written: every claim already ships
OLD fixtures that must fail, so compiling them yields the real diagnostic a
model will actually see, and the index maps those signatures to claims. Nobody
guesses what zig says about `std.io`; zig is asked.

Two accuracy measurements matter and they are not the same thing:

  ROUND-TRIP — every fixture's own diagnostic must select its own card. This
  is近 tautological (the index was built from these very diagnostics) and its
  only real job is to catch an index that has gone stale or a scoring change
  that breaks lookup.

  HELD-OUT — stale snippets written for this test, whose diagnostics the index
  has NEVER seen. This is the number that means something, and it is the one
  with a floor.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

from lang import drivers as D        # noqa: E402
from lang import escalate as E       # noqa: E402
from lang import verify as V         # noqa: E402

CLAIMS = os.path.join(ROOT, "agents", "lang", "claims")
ZIG = D.driver_for("zig")
PY_D = D.driver_for("python")
have_zig = bool(ZIG and ZIG.available())

# Stale code written HERE, deliberately not copied from tests/fixtures/zig016,
# so the index has never seen these diagnostics.
HELD_OUT = [
    ("zig", "arraylist", 'const gpa = std.heap.page_allocator;\n'
                         '    var xs = std.ArrayList(u32).init(gpa);\n'
                         '    defer xs.deinit();\n    try xs.append(7);\n    _ = xs;'),
    ("zig", "math", 'const v = std.math.max(3, 9);\n    _ = v;'),
    ("zig", "mem", 'const parts = std.mem.split(u8, "a,b", ",");\n    _ = parts;'),
    ("zig", "ascii", "const b = std.ascii.isSpace(' ');\n    _ = b;"),
    ("zig", "casts", 'const f = @intToFloat(f64, @as(i32, 3));\n    _ = f;'),
    ("zig", "alloc_exit", 'var g = std.heap.GeneralPurposeAllocator(.{}){};\n'
                          '    _ = g.allocator();'),
    ("zig", "args", 'const a = try std.process.argsAlloc(std.heap.page_allocator);\n'
                    '    _ = a;'),
    ("zig", "time_random", 'const t = std.time.milliTimestamp();\n    _ = t;'),
    ("zig", "fmt", 'var b: [8]u8 = undefined;\n'
                   '    const n = std.fmt.formatIntBuf(&b, 42, 10, .lower, .{});\n'
                   '    _ = n;'),
    ("python", "imp-module-gone", 'import imp\nm = imp.find_module("os")\n'),
    ("python", "string-maketrans-gone", 'import string\ntbl = string.maketrans("ab", "xy")\n'),
]


def _diagnostic(lang, src):
    drv = D.driver_for(lang)
    res = drv.compile_source(drv.wrap(src))
    assert not res, f"the snippet COMPILED — it is not stale on this toolchain:\n{src}"
    return res.detail


# --- the index ------------------------------------------------------------

def test_the_index_is_present_and_stamped_with_a_toolchain():
    idx = E.load_index()
    assert idx.get("langs"), "no escalation index — run escalate.py --rebuild"
    for lang, entry in idx["langs"].items():
        assert entry.get("toolchain"), f"{lang} entry has no toolchain stamp"
        assert entry.get("signatures"), f"{lang} entry has no signatures"


@pytest.mark.skipif(not have_zig, reason="zig absent")
def test_the_committed_index_matches_a_rebuild():
    """A hand-edited or stale index would select cards from signatures the
    installed compiler no longer produces."""
    assert E.main(["--check", "--lang", "zig"]) == 0


def test_a_stale_index_is_refused_rather_than_used(monkeypatch):
    """Diagnostics move between releases. An index built by another toolchain
    version must produce NOTHING — the same drift discipline packs have."""
    idx = E.load_index()
    if "zig" not in (idx.get("langs") or {}):
        pytest.skip("no zig index here")
    poisoned = {"langs": {"zig": dict(idx["langs"]["zig"], toolchain="0.11.0")}}
    got = E.cards_for("zig", "error: root source file struct 'std' has no member "
                             "named 'io'", index=poisoned)
    assert got == [], "a card was served from an index built by another toolchain"


# --- accuracy -------------------------------------------------------------

@pytest.mark.skipif(not have_zig, reason="zig absent")
def test_round_trip_every_fixture_selects_its_own_card():
    idx = E.load_index()
    tot = hit = 0
    for c in V.load_claims(CLAIMS):
        drv = D.driver_for(c.lang)
        if not drv or not drv.available():
            continue
        snips, variant = V.old_snippets(c)
        for src in snips:
            res = drv.compile_source(drv.wrap(src), variant)
            if res:
                continue
            tot += 1
            ids = [x["claim"] for x in E.cards_for(c.lang, res.detail,
                                                   max_cards=3, index=idx)]
            hit += c.id in ids
    assert tot >= 40, f"only {tot} diagnostics — the claim sets shrank?"
    assert hit == tot, f"round-trip {hit}/{tot}: the index no longer maps its own evidence"


def test_held_out_accuracy_has_a_floor():
    """The number that means something: code the index has never seen.

    Measured 14/14 in top-3 and 13/14 at #1 when this was written. The floor
    is deliberately below that — a regression should fail, ordinary drift in
    one snippet should not."""
    idx = E.load_index()
    cases = [(l, e, s) for l, e, s in HELD_OUT
             if (D.driver_for(l) and D.driver_for(l).available())]
    if len(cases) < 5:
        pytest.skip("too few toolchains here to measure")
    hits = firsts = 0
    misses = []
    for lang, expect, src in cases:
        ids = [x["claim"] for x in E.cards_for(lang, _diagnostic(lang, src),
                                               max_cards=3, index=idx)]
        if expect in ids:
            hits += 1
            firsts += ids[0] == expect
        else:
            misses.append((expect, ids))
    assert hits == len(cases), f"held-out misses: {misses}"
    assert firsts >= len(cases) - 2, f"only {firsts}/{len(cases)} ranked #1"


def test_the_shipped_default_card_limit_still_delivers():
    """MAX_CARDS defaults to 2, and the one case that does not rank #1 is the
    `std.io` ambiguity where stdin and stdout are both true. Two cards must
    therefore still contain the right one."""
    idx = E.load_index()
    if not have_zig:
        pytest.skip("zig absent")
    diag = _diagnostic("zig", 'const out = std.io.getStdOut().writer();\n'
                              '    try out.print("{d}\\n", .{1});')
    ids = [x["claim"] for x in E.cards_for("zig", diag, index=idx)]
    assert len(ids) <= 2
    assert "stdout" in ids or "stdin" in ids


# --- what it refuses to say -----------------------------------------------

def test_nothing_is_attached_for_an_unrecognised_error():
    idx = E.load_index()
    assert E.cards_for("zig", "error: this is not a thing zig ever said",
                       index=idx) == []
    assert E.render_escalation("zig", "totally unrelated text") == ""


def test_a_language_with_no_index_gets_nothing():
    assert E.cards_for("cobol", "error: something", index=E.load_index()) == []


def test_a_claim_with_no_summary_is_never_attached(monkeypatch):
    """Verified but undeliverable. This is not hypothetical: EVERY zig claim
    was in that state at first (the set is generated from the fixture manifest,
    which carried no summaries), so escalation silently matched nothing for the
    one language it matters most for."""
    real = V.load_claims

    def stripped(path):
        cs = real(path)
        for c in cs:
            c.summary = ""
        return cs
    monkeypatch.setattr(V, "load_claims", stripped)
    assert E.cards_for("zig", "error: root source file struct 'std' has no "
                              "member named 'io'", index=E.load_index()) == []


def test_the_rendered_block_says_where_its_facts_came_from():
    if not have_zig:
        pytest.skip("zig absent")
    diag = _diagnostic("zig", 'const v = std.math.max(3, 9);\n    _ = v;')
    out = E.render_escalation("zig", diag)
    assert out, "nothing rendered for a known-stale idiom"
    assert "beast-lang" in out
    assert "installed on this machine" in out
    assert "@min" in out or "@max" in out
