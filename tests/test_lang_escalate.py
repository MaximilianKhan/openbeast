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


DIAG_STD_IO = "error: root source file struct 'std' has no member named 'io'"


def test_a_stale_index_is_refused_rather_than_used():
    """Diagnostics move between releases. An index built by another toolchain
    version must produce NOTHING — the same drift discipline packs have."""
    idx = E.load_index()
    if "zig" not in (idx.get("langs") or {}):
        pytest.skip("no zig index here")
    poisoned = {"langs": {"zig": dict(idx["langs"]["zig"], toolchain="0.11.0")}}
    assert E.cards_for("zig", DIAG_STD_IO, index=poisoned) == [], \
        "a card was served from an index built by another toolchain"


def test_an_index_that_cannot_be_CONFIRMED_serves_nothing():
    """The fail-open CI caught. The version comparison was guarded on
    `if installed_v and ...`, so a box with NO toolchain skipped the check
    entirely and served cards from any index at all — including one stamped
    for a different release. Unverifiable is never a pass here; it is the same
    rule that keeps Swift claims out of every pack."""
    idx = E.load_index()
    if "zig" not in (idx.get("langs") or {}):
        pytest.skip("no zig index here")
    drv = D.driver_for("zig")
    original = drv.available
    try:
        drv.available = lambda: False          # a box with no zig
        assert E.cards_for("zig", DIAG_STD_IO, index=idx) == [], \
            "cards were served for a language whose toolchain is absent"
    finally:
        drv.available = original
    if original():                             # and it still works with zig
        assert E.cards_for("zig", DIAG_STD_IO, index=idx)


# --- accuracy -------------------------------------------------------------

@pytest.mark.skipif(not have_zig, reason="zig absent")
def test_round_trip_every_fixture_selects_its_own_card_or_nothing():
    """Was `hit == tot`, and that was the bug wearing a test's clothes: the
    only way EVERY fixture can select its card is if a generic message
    selects one too. Four fixtures fail with nothing but boilerplate —
    python's bare "invalid syntax", zig's "expected type 'i32', found '?i32'",
    "missing struct field: items" and "expected 1 argument, found 2" — and a
    card for those is a card for every type mismatch ever written. So: a
    fixture selects ITS OWN card or NO card, the silent ones are few, and a
    claim is left with no reachable fixture only if the index says so."""
    idx = E.load_index()
    tot = hit = 0
    silent, wrong, reached, seen = [], [], set(), set()
    per_lang: dict = {}
    for c in V.load_claims(CLAIMS):
        drv = D.driver_for(c.lang)
        if not drv or not drv.available():
            continue
        snips, variant = V.old_snippets(c)
        for n, src in enumerate(snips, 1):
            res = drv.compile_source(drv.wrap(src), variant)
            if res:
                continue
            tot += 1
            seen.add((c.lang, c.id))
            got = per_lang.setdefault(c.lang, [0, 0])
            got[1] += 1
            ids = [x["claim"] for x in E.cards_for(c.lang, res.detail,
                                                   max_cards=3, index=idx)]
            if c.id in ids:
                hit += 1
                got[0] += 1
                reached.add((c.lang, c.id))
            elif ids:
                wrong.append((c.id, ids))
            else:
                silent.append((c.lang, f"{c.id}:old[{n}]"))
    assert tot >= 40, f"only {tot} diagnostics — the claim sets shrank?"
    assert not wrong, f"a fixture selected somebody else's card: {wrong}"
    # EXACTLY the fixtures the index declares silent — no more (a regression)
    # and no fewer (a stale declaration). On this toolchain that is 36/39 zig.
    declared = {(lang, f) for lang, e in idx["langs"].items()
                for f in e["unreachable"]["fixtures"]}
    assert set(silent) == {d for d in declared if d[0] in per_lang}, \
        (sorted(silent), sorted(declared))
    assert per_lang["zig"] == [36, 39], per_lang
    declared_claims = {(lang, cid) for lang, e in idx["langs"].items()
                       for cid in e["unreachable"]["claims"]}
    assert seen - reached == {d for d in declared_claims if d[0] in per_lang}


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


# --- a generic error selects NOTHING ---------------------------------------
# Every one of these got a card, headed "this error has a known cause …
# Confirmed against the toolchain installed on this machine". They are the
# commonest errors there are, and none of them is any claim's error.
FALSE_POSITIVES = [
    ("zig", "claim.zig:3:20: error: expected type 'u8', found 'u16'"),
    ("zig", "claim.zig:2:18: error: struct 'std' has no member named 'foobar'"),
    ("zig", "claim.zig:4:6: error: no field or member function named "
            "'frobnicate' in 'MyStruct'"),
    ("python", "SyntaxError: invalid syntax (<unknown>, line 3)"),
    ("cpp", "claim.cpp:6:12: error: expected \u2018;\u2019 before \u2018{\u2019 token"),
    # same family, found while fixing the five above
    ("zig", "claim.zig:2:18: error: root source file struct 'std' has no "
            "member named 'foobar'"),
    ("zig", "claim.zig:4:6: error: no field or member function named 'sort' "
            "in 'MyStruct'"),
    ("zig", "claim.zig:3:20: error: expected type 'i32', found 'u8'"),
    ("zig", "claim.zig:3:9: error: expected 1 argument, found 2"),
]
# The NEGATIVE CONTROL for the list above: the real known-cause line of the
# same sentence form, which must keep selecting its card.
TRUE_POSITIVES = [
    ("zig", "claim.zig:2:18: error: root source file struct 'std' has no "
            "member named 'io'", "stdout"),
    ("zig", "claim.zig:5:9: error: no field or member function named 'writer' "
            "in 'array_list.Aligned(u8,null)'", "arraylist_writer"),
    ("zig", "claim.zig:3:9: error: struct 'array_list.Aligned(i32,null)' has "
            "no member named 'init'", "arraylist"),
    ("zig", "claim.zig:3:9: error: invalid builtin function: '@intToFloat'", "casts"),
    ("python", "string.maketrans does not exist", "string-maketrans-gone"),
    ("cpp", "claim.cpp:6:10: error: structured bindings only available with "
            "\u2018-std=c++17\u2019 or \u2018-std=gnu++17\u2019 [-Wc++17-extensions]",
     "structured-bindings"),
]


def _selected(lang, diag):
    """Selection against the COMMITTED index, with no toolchain in the loop —
    so this runs (and can fail) on a CI box that has neither zig nor g++,
    where cards_for() would return [] for the wrong reason."""
    entry = E.load_index()["langs"][lang]
    return E._select(entry["signatures"], set(entry["generic"]), diag,
                     set(entry["open_shapes"]))[0]


@pytest.mark.parametrize("lang,diag", FALSE_POSITIVES)
def test_a_generic_error_selects_no_card(lang, diag):
    assert _selected(lang, diag) == set(), \
        f"a card was attached to an error that is nobody's: {diag}"
    assert E.render_escalation(lang, diag) == ""


@pytest.mark.parametrize("lang,diag,expect", TRUE_POSITIVES)
def test_the_known_cause_error_of_the_same_form_still_selects(lang, diag, expect):
    assert expect in _selected(lang, diag), \
        f"the fix for false positives silenced a real one: {diag}"
    drv = D.driver_for(lang)
    if drv and drv.available() and E.P._short_version(drv.version()) == \
            E.P._short_version(E.load_index()["langs"][lang]["toolchain"]):
        assert expect in [c["claim"] for c in E.cards_for(lang, diag, max_cards=3)]


def test_an_identifier_alone_or_a_form_alone_is_not_evidence():
    """The rule itself, on a table built here so it cannot pass by accident
    of what the shipped index happens to contain."""
    table = {"ident:gone": ["a"], "ident:ns": ["a", "b", "c", "d"],
             "ident:boiler": ["a"],
             "shape:struct 'X' has no member named 'X'": ["a"],
             "shape:the frobnicator was removed": ["a"],
             "shape:invalid syntax": ["a"]}
    generic = {"ident:boiler", "shape:invalid syntax"}
    sel = lambda d: E._select(table, generic, d)[0]          # noqa: E731
    assert sel("error: struct 'T' has no member named 'gone'") == set(), \
        "the member is known but the CONTAINER 'T' is not"
    table["ident:T"] = ["a"]
    assert sel("error: struct 'T' has no member named 'gone'") == {"a"}
    assert sel("error: struct 'T(u8,null)' has no member named 'gone'") == {"a"}, \
        "type arguments are not part of the container's name"
    assert sel("error: struct 'T' has no member named 'other'") == {"a"}, \
        "a known container with an unseen member is the held-out case"
    assert sel("error: struct 'U' has no member named 'other'") == set(), "form alone"
    table["ident:elsewhere"] = ["b"]
    assert sel("error: struct 'T' has no member named 'elsewhere'") == set(), \
        "two known names that disagree about the claim select nothing"
    assert sel("error: cannot find 'gone' anywhere") == set(), "identifier alone"
    assert sel("error: struct 'ns' has no member named 'x'") == set(), "a namespace"
    assert sel("error: struct 'boiler' has no member named 'x'") == set(), "decoy-seen"
    assert sel("error: the frobnicator was removed") == {"a"}, "an unquoted, specific line"
    assert sel("error: invalid syntax") == set(), "an unquoted, generic line"


def test_an_index_built_before_decoys_is_refused():
    """It cannot say which signatures are boilerplate, so it cannot select."""
    idx = E.load_index()
    lang = next((lg for lg in idx["langs"]
                 if D.driver_for(lg) and D.driver_for(lg).available()), None)
    if not lang:
        pytest.skip("no indexed toolchain here")
    old = {"langs": {lang: {k: v for k, v in idx["langs"][lang].items()
                            if k != "generic"}}}
    diag = {d[0]: d[1] for d in TRUE_POSITIVES}[lang]
    assert E.cards_for(lang, diag, index=idx), "control: the full index selects"
    assert E.cards_for(lang, diag, index=old) == []


def test_every_decoy_still_fails_to_compile():
    """A decoy that compiles measures nothing, silently."""
    for lang, entry in E.load_index()["langs"].items():
        assert entry.get("decoys_that_compiled_anyway") == 0, lang
        assert entry.get("generic"), f"{lang}: the decoys marked nothing generic"


@pytest.mark.parametrize("var,val", [("OPENBEAST_LANG_MAX_CARDS", "two"),
                                     ("OPENBEAST_LANG_MAX_CARDS", ""),
                                     ("OPENBEAST_LANG_COMPILE_TIMEOUT", ""),
                                     ("OPENBEAST_LANG_COMPILE_TIMEOUT", "soon"),
                                     ("OPENBEAST_LANG_PACK_BUDGET", "lots"),
                                     ("OPENBEAST_LANG_AS_LIMIT_MB", "big")])
def test_a_mistyped_env_knob_cannot_crash_the_import(var, val):
    """These were float()/int() AT IMPORT: one bad value in the environment
    of a tool server that imports this package was a crash at startup."""
    import subprocess
    env = dict(os.environ, **{var: val})
    p = subprocess.run(
        [sys.executable, "-c",
         "import lang.escalate as E, lang.packs as P, lang.drivers as D;"
         "print(E.MAX_CARDS, D.TIMEOUT_S, P.DEFAULT_BUDGET_TOKENS)"],
        cwd=os.path.join(ROOT, "agents"), env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-400:]
    assert p.stdout.split() == ["2", "90.0", "2000"], p.stdout
    # negative control: a GOOD value is still honoured, not defaulted away
    good = dict(os.environ, OPENBEAST_LANG_MAX_CARDS="3",
                OPENBEAST_LANG_COMPILE_TIMEOUT="7.5")
    p = subprocess.run(
        [sys.executable, "-c",
         "import lang.escalate as E, lang.drivers as D; print(E.MAX_CARDS, D.TIMEOUT_S)"],
        cwd=os.path.join(ROOT, "agents"), env=good, capture_output=True, text=True)
    assert p.stdout.split() == ["3", "7.5"], p.stdout + p.stderr


# --- round 2: the container, the other names on the line, and real wording ---
_ROOT = "claim.zig:3:14: error: root source file struct '{}' has no member named '{}'"
WRONG_CARDS = [
    # `const S = @This(); _ = S.sort;` — the container is the USER'S OWN FILE
    ("zig", _ROOT.format("claim", "sort")),          # got the mem card
    ("zig", _ROOT.format("claim", "max")),           # got math
    ("zig", _ROOT.format("claim", "cwd")),           # got files
    # a real std container, and a member that belongs to a DIFFERENT claim
    ("zig", _ROOT.format("mem", "exit")),            # got alloc_exit + mem
    ("zig", _ROOT.format("ascii", "copy")),          # ascii + mem
    ("zig", _ROOT.format("heap", "sleep")),          # alloc_exit + time_random
    ("zig", _ROOT.format("process", "random")),      # args + time_random
    ("zig", _ROOT.format("fmt", "abs")),             # fmt + math
    ("zig", _ROOT.format("math", "split")),          # math + mem
    # cpp: the very text of two fixtures' only specific line, produced by code
    # that has nothing to do with the claim (c++23 with no header declaring
    # std::ranges; a stray `int operator<=;`). Identical text cannot be
    # evidence, so those two claims are declared unreachable instead.
    ("cpp", "claim.cpp:4:45: error: \u2018std::ranges\u2019 has not been declared"),
    ("cpp", "claim.cpp:1:5: error: declaration of \u2018operator<=\u2019 as non-function"),
]
RIGHT_CARDS = [
    ("zig", _ROOT.format("mem", "split"), "mem"),
    ("zig", _ROOT.format("math", "max"), "math"),
    ("zig", _ROOT.format("time", "milliTimestamp"), "time_random"),    # held-out member
    ("zig", "claim.zig:3:9: error: struct 'array_list.Aligned(u32,null)' has no "
            "member named 'init'", "arraylist"),                       # held-out type args
    ("zig", "claim.zig:5:9: error: no field or member function named 'writer' in "
            "'array_list.Aligned(u32,null)'", "arraylist_writer"),
    # cpp with the MODEL's identifiers, not the fixture's `v` and `sum`
    ("cpp", "claim.cpp:2:39: error: variable \u2018vec\u2019 of non-literal type "
            "\u2018std::vector<int>\u2019 in \u2018constexpr\u2019 function only available "
            "with \u2018-std=c++23\u2019 or \u2018-std=gnu++23\u2019 [-Wc++23-extensions]",
     "constexpr-vector"),
    ("cpp", "claim.cpp:9:3: error: init-statement in selection statements only "
            "available with \u2018-std=c++17\u2019 or \u2018-std=gnu++17\u2019 "
            "[-Wc++17-extensions]", "if-init-statement"),
    # python, in the INTERPRETER's words — 0 of these hit before
    ("python", "Traceback (most recent call last):\n  File \"x.py\", line 1, in "
               "<module>\n    import imp\nModuleNotFoundError: No module named 'imp'",
     "imp-module-gone"),
    ("python", "Traceback (most recent call last):\n  File \"x.py\", line 2, in "
               "<module>\n    t = string.maketrans('a', 'b')\nAttributeError: module "
               "'string' has no attribute 'maketrans'", "string-maketrans-gone"),
    ("python", "ImportError: cannot import name 'maketrans' from 'string' "
               "(/usr/lib/python3.14/string.py)", "string-maketrans-gone"),
    ("python", "AttributeError: 'T' object has no attribute 'assertEquals'. "
               "Did you mean: 'assertEqual'?", "unittest-aliases-removed"),
]
PY_NOT_OURS = [
    "ModuleNotFoundError: No module named 'requests'",
    "AttributeError: module 'os' has no attribute 'maketrans'",
    "AttributeError: 'T' object has no attribute 'frobnicate'",
    "AttributeError: 'str' object has no attribute 'maketrans'",
]


@pytest.mark.parametrize("lang,diag", WRONG_CARDS)
def test_a_real_diagnostic_with_no_known_cause_selects_no_card(lang, diag):
    assert _selected(lang, diag) == set(), diag
    assert E.render_escalation(lang, diag) == ""


@pytest.mark.parametrize("lang,diag,expect", RIGHT_CARDS)
def test_the_card_still_fires_on_what_a_model_actually_sees(lang, diag, expect):
    assert _selected(lang, diag) == {expect}, diag


@pytest.mark.parametrize("diag", PY_NOT_OURS)
def test_real_python_wording_does_not_widen_the_net(diag):
    assert _selected("python", diag) == set(), diag


def test_the_silent_fixtures_are_declared_not_discovered():
    """Per FIXTURE: arraylist is reachable, and two of its three OLD forms
    still fail with nothing but boilerplate."""
    e = E.load_index()["langs"]
    assert e["zig"]["unreachable"] == {
        "claims": [], "fixtures": ["arraylist:old[2]", "arraylist:old[3]",
                                   "casts:old[3]"]}
    assert e["python"]["unreachable"]["claims"] == ["asyncio-no-async-coroutine"]
    # and the cost of refusing identical text, stated rather than hidden
    assert e["cpp"]["unreachable"]["claims"] == ["ranges-sort", "three-way-comparison"]


def test_python_wording_is_derived_without_running_the_old_form():
    lines = E._python_real_wording(
        "imp is not a stdlib module (refused, not imported); "
        "unittest.TestCase.assertEquals does not exist")
    assert "ModuleNotFoundError: No module named 'imp'" in lines
    assert "AttributeError: 'TestCase' object has no attribute 'assertEquals'" in lines
    assert E._python_real_wording("SyntaxError: invalid syntax (<unknown>, line 2)") == []


def test_a_refusal_never_becomes_a_signature(monkeypatch):
    """OUR refusal text is not the toolchain's words. A fixture the driver
    refuses must leave no trace in the index."""
    py = D.driver_for("python")
    monkeypatch.setattr(type(py), "compile_source",
                        lambda self, src, variant=None: D.Result(
                            False, "refused, not compiled: it includes a host file",
                            "refused", refused=True))
    entry = E.build_index(["python"])["langs"]["python"]
    assert entry["fixtures_with_diagnostics"] == 0
    assert entry["signatures"] == {}
