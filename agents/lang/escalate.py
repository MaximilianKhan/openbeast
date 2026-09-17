#!/usr/bin/env python3
"""Escalation: a compiler error picks the card that fixes it.

This is Tier 1.5 of docs/LANG_AWARENESS_PLAN.md and phase 4 of
docs/BEAST_LANG_PLAN.md, and it is the layer the whole push/escalate/pull
argument rests on. The repo measured that local models do not call optional
tools, so *asking* the model to look something up does not work. But a
COMPILE ERROR is not a request — it is proof that the model already holds a
stale belief, at the exact moment it holds it. Attaching the right card then
is the only delivery that is both targeted and unsolicited.

HOW THE MATCHING IS DERIVED, and why it is not a pile of regexes. Every claim
already ships OLD fixtures that MUST fail (that is what VERIFIED means), so
compiling them yields the real diagnostic a model will actually see. The index
is built from those compilations: error text -> signature -> claim. Nobody
guesses what zig says about `std.io`; zig is asked.

Measured on the shipped zig claim set: 39/39 OLD fixtures produce a diagnostic,
in 8 distinct message shapes, and 28 of them are
`root source file struct 'std' has no member named 'io'` — where the quoted
identifiers ARE the signal.

DRIFT. Diagnostics change between toolchain versions, so the index is stamped
with the version that produced it and is refused when that does not match the
installed one. A card selected by a stale error signature is the same class of
mistake as a pack written for the wrong compiler.

AMBIGUITY IS NOT AN ERROR. `'std' + 'io'` legitimately selects both the stdout
and stdin claims — `std.io` is gone and both cards are relevant. Matches are
returned ranked by signature overlap, not narrowed to one by a tiebreak nobody
can justify.

GENERIC IS NOT EVIDENCE. The block this renders says "this error has a known
cause … Confirmed", so a card on the WRONG error is a false statement with the
toolchain's authority behind it — worse than silence. The first matcher let
any single signature select a card, and the commonest errors there are selected
one: a zig type mismatch got the ArrayList card (`shape:expected type 'X',
found 'X'`), a typo'd `std.foobar` got the std.io cards (`ident:std`), EVERY
python syntax error got the asyncio.async() card, a missing `;` in C++ got
structured bindings. Two rules now decide what may SELECT (see cards_for), and
one of them is measured the same way the index is: DECOYS — snippets broken in
ways no claim is about — are compiled at build time, and whatever signature
they produce is recorded as `generic`. Nobody guesses which messages are
boilerplate; the compiler is asked. When in doubt the answer is nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENTS = os.path.dirname(_HERE)
if _AGENTS not in sys.path:
    sys.path.insert(0, _AGENTS)

from lang import _proc            # noqa: E402
from lang import drivers as D     # noqa: E402
from lang import packs as P       # noqa: E402
from lang import verify as V      # noqa: E402

INDEX_PATH = os.path.join(_HERE, "escalate-index.json")
#: How many cards one escalation may attach. A compile error that drags in six
#: cards has stopped being targeted delivery and become a second pack.
# Parsed with a fallback — `OPENBEAST_LANG_MAX_CARDS=two` used to be a
# ValueError at import, i.e. a crash in whatever server imported this.
MAX_CARDS = _proc.env_number("OPENBEAST_LANG_MAX_CARDS", 2, int, 1)
#: An identifier naming more claims than this is a namespace, not a symptom.
MAX_IDENT_FANOUT = 3
#: gcc quotes with \u2018…\u2019, zig and python with '…'. Matching only the
#: ASCII pair left every gcc identifier INSIDE the shape, so a cpp card fired
#: only for the fixture's own variable names (`v`, `sum`) and never for a
#: model's (`vec`, `total`).
_QUOTED = re.compile("'([^']{1,64})'|\u2018([^\u2019]{1,64})\u2019")
#: Noise that differs per machine or per interpreter release and is not part of
#: the message: python's "(/usr/lib/python3.14/string.py)" and its
#: ". Did you mean: 'assertEqual'?" hint.
_TAIL_NOISE = re.compile(r"\s*\(/[^()]*\)\s*$|\. Did you mean:.*$")

#: Code that is broken in a way NO CLAIM IS ABOUT. Each entry is a false
#: positive somebody actually got a card for; compiling them is how the index
#: learns which of its signatures are compiler boilerplate. Every one must
#: FAIL to compile (the build reports any that do not), and a whole-file decoy
#: is used where the message quotes a type name that has to match.
DECOYS: dict[str, list[str]] = {
    "zig": [
        "const x = std.beastlang_no_such_member;\n_ = x;",        # ident:std
        "const a: u16 = 300;\nconst b: u8 = a;\n_ = b;",
        "const a: ?i32 = null;\nconst b: i32 = a;\n_ = b;",       # type mismatch
        "const S = struct { a: u8 };\nvar s = S{ .a = 1 };\ns.writer();",
        "const S = struct { a: u8 };\n_ = S.init;",
        "const S = struct { items: u8, n: u8 };\nconst s = S{ .n = 1 };\n_ = s;",
        "const n = @sizeOf(u8, u8);\n_ = n;",                     # arg count
    ],
    "python": [
        "def f(:\n    pass\n",                                   # any SyntaxError
    ],
    "cpp": [
        "int k = 0; k++ { }",                                     # missing ;
        "struct S { int x };",
        "auto t = std::pair{1, 2}; (void)t;",                     # CTAD, not bindings
        "int > x;",
        "struct P { int a; };\nint main() { P a{1}, b{2}; return a < b; }\n",
        "int n = 3; static_assert(n == 3, \"n\");",
        # The two that look like a claim's OWN error and are not. `std::ranges`
        # is "not declared" at c++23 too when nothing that declares it is
        # included, and a stray `operator<=` needs no spaceship to be wrong:
        # the text is identical, so the text cannot be evidence.
        "#include <cstddef>\nint main() { int a[2] = {2, 1}; "
        "std::ranges::sort(a); return 0; }\n",
        "int operator<=;\nint main() { return 0; }\n",
    ],
}


def _line_signatures(text: str) -> list[tuple[set[str], list[str]]]:
    """(signature set, quoted identifiers IN ORDER) per diagnostic line — see
    extract_signatures for what is in the set. Selection needs the line as a
    unit, and needs the order: in "struct 'A' has no member named 'b'" the
    first quoted name is the CONTAINER, and `'sort'` missing from the user's
    own file is not `'sort'` missing from std.mem."""
    lines = [ln for ln in text.splitlines() if "error:" in ln]
    if not lines:
        # Not every toolchain prefixes "error:". The python driver reports its
        # OWN static-resolution failures ("import imp: ModuleNotFoundError",
        # "unittest.TestCase.assertEquals does not exist"), and requiring the
        # prefix meant python produced 4 diagnostics and 0 signatures — an
        # index that silently covered one language.
        lines = [ln for ln in text.splitlines() if ln.strip()]
    out: list[tuple[set[str], list[str]]] = []
    for line in lines:
        sigs: set[str] = set()
        msg = line.split("error:", 1)[1].strip() if "error:" in line else line.strip()
        msg = _TAIL_NOISE.sub("", msg)
        # Dotted API paths are the signal where nothing is quoted
        # (`unittest.TestCase.assertEquals does not exist`), so harvest them
        # too — the last segment is the name that moved.
        for dotted in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b", msg):
            parts = dotted.split(".")
            sigs.add(f"ident:{parts[-1]}")
            sigs.add(f"ident:{dotted}")
        quoted: list[str] = []
        for pair in _QUOTED.findall(msg):
            ident = pair[0] or pair[1]
            # Skip paths and anything that is plainly not an identifier.
            if "/" in ident or ident.count(".") > 2 or len(ident) > 48:
                continue
            quoted.append(ident)
            sigs.add(f"ident:{ident}")
        shape = _QUOTED.sub("'X'", msg)
        shape = re.sub(r"\d+", "N", shape)[:80]
        sigs.add(f"shape:{shape}")
        out.append((sigs, quoted))
    return out


def extract_signatures(text: str) -> set[str]:
    """Matchable tokens from a compiler diagnostic.

    Two kinds, both cheap and both derived from the shapes actually observed:

      ident:<name>   every quoted identifier. This is the workhorse — the
                     missing member's name is quoted in 30 of the 39 zig
                     diagnostics, and it is the most specific thing present.
      shape:<form>   the message with quoted spans blanked, so
                     "invalid builtin function: 'X'" can match even when the
                     identifier differs from the fixture's.

    Deliberately NOT a parse of the compiler's grammar: that would be a second
    implementation of somebody else's error format, wrong on the next release.
    """
    sigs: set[str] = set()
    for line_sigs, _ in _line_signatures(text):
        sigs |= line_sigs
    return sigs


def _container_key(ident: str) -> str:
    """`array_list.Aligned(u32,null)` -> `array_list.Aligned`: the container's
    NAME, without the type arguments a model's code will not share with the
    fixture's."""
    return re.sub(r"\(.*\)\s*$", "", ident)


def _select(table: dict, generic: set, diagnostic: str,
            open_shapes: frozenset | set = frozenset()) -> tuple[set[str], dict]:
    """(claims this diagnostic is EVIDENCE for, {claim: overlap score}).

    The score ranks; it never selects. A claim is selected only by a line that
    could not plausibly have come from an unrelated mistake, which means ALL
    of the following, per line:

      * the sentence form maps to the claim. Form alone never selects:
        `expected type 'X', found 'X'` is every type mismatch ever written.
      * EVERY identifier on the line that the index knows maps to the claim.
        One matching name was the old rule, and it handed the std.mem card to
        `std.math.split` (the index knows 'split'; it also knows 'math', and
        'math' says otherwise) — 9 wrong cards from 9 real diagnostics.
      * at least one of those is not generic (decoy-observed) and not a
        namespace (MAX_IDENT_FANOUT).
      * on a two-name form — "struct 'A' has no member named 'b'" — the FIRST
        name is known and maps to the claim too. `const S = @This(); S.sort`
        reads "root source file struct 'claim' has no member named 'sort'":
        the member is the index's, the container is the user's own file, and
        an unknown container is not std.mem. The exception is a form the
        index itself declares open (python's "'T' object has no attribute
        'x'", where the first name is the USER's class by construction).
      * a line that quotes nothing has only its form, so the form must be
        non-generic and must not be a blanked template.
    """
    eligible: set[str] = set()
    score: dict[str, int] = {}
    for line_sigs, quoted in _line_signatures(diagnostic):
        shape = next((x for x in line_sigs if x.startswith("shape:")), "")
        by_shape = set(table.get(shape, []))
        idents = {x for x in line_sigs if x.startswith("ident:")}
        idents |= {f"ident:{_container_key(q)}" for q in quoted}
        known = [x for x in sorted(idents) if x in table]
        if known:
            agreed = set(by_shape)
            for ident in known:
                agreed &= set(table[ident])
            specific = any(x not in generic and len(table[x]) <= MAX_IDENT_FANOUT
                           for x in known)
            if len(quoted) == 2 and shape not in open_shapes:
                first = f"ident:{_container_key(quoted[0])}"
                if first not in table and f"ident:{quoted[0]}" not in table:
                    agreed = set()
            if specific:
                eligible |= agreed
        elif not idents and shape not in generic and "'X'" not in shape:
            eligible |= by_shape
        for sig in line_sigs:
            for cid in table.get(sig, []):
                # A PLAIN COUNT of matching signatures. The first version
                # weighted `ident:` matches 4x over `shape:` ones, on the
                # reasoning that an identifier is more specific than a
                # sentence form — plausible, and measurably worthless: a
                # mutation that removed the weight broke no test, so it was
                # measured properly at weights 1, 2, 4 and 8 against the
                # held-out set. All four give identical results (#1 in 11/12,
                # right card within the shipped 2-card limit in 12/12). An
                # unmeasured knob carrying a confident comment is exactly the
                # shape of thing this repo keeps getting burned by, so it is
                # gone. If a future case needs ranking help, measure first
                # and put the number here.
                score[cid] = score.get(cid, 0) + 1
    return eligible, score


#: python's own wording. The index used to hold only what OUR DRIVER says
#: ("imp is not a stdlib module (refused, not imported)"), which no model ever
#: sees: 0 of 4 real tracebacks selected a card. The interpreter's sentence is
#: DERIVED from the driver's finding rather than produced by running the OLD
#: form — running it is the one thing this package does not do (drivers.py,
#: SECURITY), and the wording is fixed by CPython, not by the snippet.
_PY_OPEN_SHAPE = "shape:AttributeError: 'X' object has no attribute 'X'"


def _python_real_wording(detail: str) -> list[str]:
    lines: list[str] = []
    for part in detail.split("; "):
        m = (re.match(r"([\w.]+) is not a stdlib module", part)
             or re.match(r"import ([\w.]+): ModuleNotFoundError", part))
        if m:
            lines.append(f"ModuleNotFoundError: No module named '{m.group(1)}'")
            continue
        m = re.match(r"([\w.]+) does not exist$", part)
        if not m:
            continue
        *owner, attr = m.group(1).split(".")
        mod = ".".join(owner)
        lines.append(f"AttributeError: module '{mod}' has no attribute '{attr}'")
        lines.append(f"ImportError: cannot import name '{attr}' from '{mod}'")
        if len(owner) > 1:                  # the owner is a class in a module
            lines.append(f"AttributeError: type object '{owner[-1]}' has no "
                         f"attribute '{attr}'")
            lines.append(f"AttributeError: '{owner[-1]}' object has no "
                         f"attribute '{attr}'")
    return lines


def build_index(langs: list[str] | None = None) -> dict:
    """Compile every claim's OLD fixtures and record what the toolchain says."""
    claims = V.load_claims(os.path.join(_HERE, "claims"))
    by_lang: dict[str, list] = {}
    for c in claims:
        by_lang.setdefault(c.lang, []).append(c)
    index: dict = {"_comment": (
        "GENERATED by agents/lang/escalate.py --rebuild. error signature -> "
        "claim ids, derived by COMPILING each claim's OLD fixtures and reading "
        "what the toolchain actually reports (python: plus the interpreter's "
        "own sentence for each finding, derived without running the OLD form). "
        "`generic` = signatures that DECOYS — code broken in ways no claim is "
        "about — produce too; they never select a card. Do not hand-edit: a "
        "hand-written signature is a guess about somebody else's error "
        "format."), "langs": {}}
    for lang, cs in sorted(by_lang.items()):
        if langs and lang not in langs:
            continue
        drv = D.driver_for(lang)
        if not drv or not drv.available():
            continue
        version = drv.version() or "?"
        sig_map: dict[str, list[str]] = {}
        compiled = failed = 0
        seen: list[tuple[str, int, str]] = []       # (claim, old[n], diagnostic)
        open_shapes: set[str] = set()
        variants: set = {None}
        for c in cs:
            snips, variant = V.old_snippets(c)
            variants.update((variant, c.new_variant))
            for n, src in enumerate(snips, 1):
                res = drv.compile_source(drv.wrap(src), variant)
                if res:                      # the OLD form COMPILED — not a break
                    failed += 1
                    continue
                if res.transient or res.refused:
                    # Not the toolchain's words: a timeout, or OUR refusal
                    # text. Neither may become a signature.
                    continue
                compiled += 1
                seen.append((c.id, n, res.detail))
                detail = res.detail
                if lang == "python":
                    real = _python_real_wording(res.detail)
                    detail = "\n".join([res.detail] + real)
                    if any("object has no attribute" in ln for ln in real):
                        open_shapes.add(_PY_OPEN_SHAPE)
                for sig in extract_signatures(detail):
                    sig_map.setdefault(sig, [])
                    if c.id not in sig_map[sig]:
                        sig_map[sig].append(c.id)
        # DECOYS, under every language level a claim is compiled at: the same
        # mistake reads differently at c++14 and c++23, and a model can be at
        # either. Only signatures the index actually holds are worth keeping.
        generic: set[str] = set()
        decoys_ok = 0
        for src in DECOYS.get(lang, []):
            broke = False
            for variant in sorted(variants, key=str):
                res = drv.compile_source(drv.wrap(src), variant)
                if not res:
                    broke = True
                    generic |= extract_signatures(res.detail) & set(sig_map)
            decoys_ok += not broke           # compiled everywhere: measured nothing
        # A claim whose every diagnostic is generic cannot be reached by an
        # error message at all (python's asyncio.async() is a bare "invalid
        # syntax"). That is the honest outcome, and it is recorded rather than
        # discovered later: its card still ships in the pack.
        silent = [(cid, n) for cid, n, diag in seen
                  if cid not in _select(sig_map, generic, diag, open_shapes)[0]]
        reached = {cid for cid, n, _ in seen if (cid, n) not in silent}
        index["langs"][lang] = {
            "toolchain": version,
            "fixtures_with_diagnostics": compiled,
            "fixtures_that_compiled_anyway": failed,
            "decoys_that_compiled_anyway": decoys_ok,
            "signatures": {k: v for k, v in sorted(sig_map.items())},
            "generic": sorted(generic),
            "open_shapes": sorted(open_shapes),
            # PER FIXTURE as well as per claim: a claim with three OLD forms,
            # one of which fails with nothing but boilerplate, is reachable —
            # and that one fixture's error still selects nothing. Declared, so
            # it is a known property and not a surprise.
            "unreachable": {
                "claims": sorted({cid for cid, _, _ in seen} - reached),
                "fixtures": sorted(f"{cid}:old[{n}]" for cid, n in silent),
            },
        }
    return index


def load_index() -> dict:
    try:
        return json.load(open(INDEX_PATH))
    except (OSError, ValueError):
        return {"langs": {}}


def cards_for(lang: str, diagnostic: str, max_cards: int = MAX_CARDS,
              index: dict | None = None) -> list[dict]:
    """[{claim, summary, score}] for a diagnostic, best first, or [].

    Empty when: the language has no index, the index was built by a DIFFERENT
    toolchain version than the one installed, nothing matches, or the matching
    claims carry no deliverable summary. Every one of those is a reason to say
    nothing rather than to attach a card we cannot stand behind.
    """
    idx = index if index is not None else load_index()
    entry = (idx.get("langs") or {}).get(lang)
    if not entry:
        return []
    # The index must be CONFIRMED to describe this machine. Two ways to fail.
    installed = D.driver_for(lang)
    installed_v = installed.version() if installed and installed.available() else None
    if not installed_v:
        # No toolchain, so the match cannot be confirmed at all. This branch
        # was a FAIL-OPEN: the version comparison was guarded on
        # `if installed_v and ...`, so a box with no compiler served cards
        # from ANY index, including one stamped for a different release. CI
        # caught it — the runner has no zig, and a deliberately poisoned
        # index handed it a card anyway. Unverifiable is never a pass here;
        # that is the same rule keeping Swift claims out of every pack.
        return []
    if P._short_version(entry.get("toolchain", "")) != P._short_version(installed_v):
        # Diagnostics move between releases; a card chosen from a stale
        # signature is the same mistake as a pack for the wrong compiler.
        return []
    if "generic" not in entry:
        # Built before decoys existed: it cannot say which of its signatures
        # are boilerplate, and that is the knowledge selection depends on.
        return []
    table = entry.get("signatures") or {}
    eligible, score = _select(table, set(entry.get("generic") or []), diagnostic,
                              set(entry.get("open_shapes") or []))
    score = {cid: sc for cid, sc in score.items() if cid in eligible}
    if not score:
        return []
    summaries = {c.id: c.summary for c in V.load_claims(os.path.join(_HERE, "claims"))
                 if c.lang == lang}
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    out = []
    for cid, sc in ranked:
        summary = summaries.get(cid) or ""
        if not summary:
            continue                       # verified but undeliverable
        out.append({"claim": cid, "summary": summary, "score": sc})
        if len(out) >= max_cards:
            break
    return out


def render_escalation(lang: str, diagnostic: str, **kw) -> str:
    """The text to attach to the next turn, or "" when there is nothing to say."""
    cards = cards_for(lang, diagnostic, **kw)
    if not cards:
        return ""
    head = (f"=== {lang}: this error has a known cause (beast-lang) ===\n"
            f"Confirmed against the {lang} toolchain installed on this machine:\n")
    body = "".join(f"- {c['summary']}\n" for c in cards)
    return head + body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed index differs from a rebuild")
    ap.add_argument("--lang")
    ap.add_argument("--diagnostic", help="text to match (use - for stdin)")
    a = ap.parse_args(argv)

    if a.rebuild or a.check:
        fresh = build_index([a.lang] if a.lang else None)
        if a.check:
            have = load_index()
            # Compare only the languages we could actually rebuild, so a box
            # without zig does not "fail" an index it cannot regenerate.
            for lang, entry in fresh["langs"].items():
                if have.get("langs", {}).get(lang) != entry:
                    print(f"escalate-index.json is stale for {lang} — rebuild it",
                          file=sys.stderr)
                    return 1
            print(f"escalate-index.json matches ({', '.join(fresh['langs']) or 'nothing rebuildable here'})")
            return 0
        with open(INDEX_PATH, "w") as fh:
            json.dump(fresh, fh, indent=1)
        for lang, e in fresh["langs"].items():
            print(f"{lang}: {len(e['signatures'])} signatures from "
                  f"{e['fixtures_with_diagnostics']} diagnostics "
                  f"(toolchain {e['toolchain']})")
            if e["fixtures_that_compiled_anyway"]:
                print(f"  ! {e['fixtures_that_compiled_anyway']} OLD fixture(s) "
                      f"COMPILED — those claims are not breaks any more")
            if e["decoys_that_compiled_anyway"]:
                print(f"  ! {e['decoys_that_compiled_anyway']} decoy(s) COMPILED "
                      f"at every level — they no longer measure anything")
            print(f"  {len(e['generic'])} signature(s) are generic (decoys "
                  f"produce them too) and select nothing on their own")
            if e["unreachable"]["claims"]:
                print(f"  ! no error message can select: "
                      f"{', '.join(e['unreachable']['claims'])} (pack-only)")
            if e["unreachable"]["fixtures"]:
                print(f"    silent fixtures (boilerplate only): "
                      f"{', '.join(e['unreachable']['fixtures'])}")
        return 0

    if a.diagnostic:
        text = sys.stdin.read() if a.diagnostic == "-" else a.diagnostic
        lang = a.lang or ""
        if not lang:
            print("--lang is required with --diagnostic", file=sys.stderr)
            return 2
        out = render_escalation(lang, text)
        print(out or "(no card matches this diagnostic)")
        return 0

    idx = load_index()
    for lang, e in (idx.get("langs") or {}).items():
        print(f"{lang}: {len(e.get('signatures', {}))} signatures, "
              f"toolchain {e.get('toolchain')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
