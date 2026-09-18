#!/usr/bin/env python3
"""Topic lookup — the PULL half of delivery (docs/BEAST_LANG_PLAN.md §7 P5).

Read the plan's §2.1 before extending this: local models do not call optional
tools, so nothing here is how a 27B learns a language — that is the pack
(push) and the compile-error card (escalate). Pull exists for the caller that
DOES stop to look something up: a cloud model working in this repo, a human
at the WebUI, a future local model strong enough to ask.

THE ONE RULE: this is a window onto the verified corpus, and it never
improvises. Every line it returns is one of the two tiers a pack may carry —

  VERIFIED   a claim whose NEW form compiled and whose OLD form failed on the
             installed toolchain, re-checked at the moment of asking (the same
             `verify.verify` a pack goes through, behind the same verdict
             cache), and stated in the claim's own one-line summary;
  GENERATED  an answer the installed toolchain gave when ASKED — "is `io` one
             of the top-level names of THIS std", "at which -std level does
             THIS g++ first define __cpp_lib_format" — labelled as such.

There is deliberately no third tier, no fuzzy "did you mean" and no prose. A
lookup that pads a miss with something plausible is the exact failure this
package exists to end: wrong, with the toolchain's authority behind it. A miss
returns "" and the caller says so.
"""
from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENTS = os.path.dirname(_HERE)
if _AGENTS not in sys.path:
    sys.path.insert(0, _AGENTS)

from lang import packs as P       # noqa: E402
from lang import verify as V      # noqa: E402

#: How many VERIFIED cards one topic may return. A topic that drags in a dozen
#: has stopped being a lookup and become a second pack — ask for the pack.
MAX_TOPIC_CARDS = 8
#: Characters, not lines (the pack budget was once counted in lines, and a
#: twelve-line cap was a 5,500 character pack).
MAX_TOPIC_CHARS = 3000

_BACKTICKED = re.compile(r"`([^`\n]{1,120})`")
#: A dotted / `::` / `<header>` name: the things a claim is ABOUT.
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::)[A-Za-z_][A-Za-z0-9_]*)+"
                   r"|<[a-z_]+>|__\w+|[A-Za-z_][A-Za-z0-9_]{2,}")


def identifiers(claim) -> set[str]:
    """The names a claim is about, lower-cased.

    Harvested from where the claim itself names them — the backticked spans
    of its summary and its `doc_must_contain` strings — never from the
    fixtures: those are whole programs, and `main`, `int` and `return` are in
    all of them. Shared with synthesize.py's duplicate check, because "the
    same topic about the same names" has to mean one thing in both places.
    """
    spans = _BACKTICKED.findall(getattr(claim, "summary", "") or "")
    spans += list(getattr(claim, "doc_must_contain", None) or [])
    out: set[str] = set()
    for span in spans:
        for name in _NAME.findall(span):
            out.add(name.lower())
    return out


def _score(claim, phrase: str, terms: list[str]) -> int:
    """How specifically `claim` answers the topic; 0 = it does not.

      3  the phrase IS the claim's topic, id, or one of its identifiers
      2  the phrase appears inside one of those
      1  the phrase appears in the summary, or every term of it appears
         somewhere in the claim's own words
    """
    idents = identifiers(claim)
    labels = {(claim.topic or "").lower(), (claim.id or "").lower()} - {""}
    if phrase in labels or phrase in idents:
        return 3
    if any(phrase in x for x in labels | idents):
        return 2
    summary = (claim.summary or "").lower()
    if phrase in summary:
        return 1
    hay = " ".join([summary, *labels, *idents])
    if terms and all(t in hay for t in terms):
        return 1
    return 0


def verified_cards(lang: str, topic: str,
                   max_cards: int = MAX_TOPIC_CARDS) -> list[dict]:
    """[{claim, topic, summary, score}], most specific first.

    Only claims that are VERIFIED **now** and carry a summary — the same two
    conditions packs.render() applies, for the same reason: a line handed to
    a model says "confirmed on this machine", and that has to be true at the
    moment it is said, not on the day the claim was written.
    """
    phrase = " ".join((topic or "").lower().split())
    if not phrase:
        return []
    terms = [t for t in re.split(r"[^\w.:+#<>]+", phrase) if len(t) >= 3]
    hits = []
    for c in V.load_claims(P.CLAIMS_DIR):
        if c.lang != lang or not c.summary:
            continue
        sc = _score(c, phrase, terms)
        if sc:
            hits.append((sc, c))
    # Most specific first; among equals the shorter card (it is the one about
    # less), then the id so the order is stable across runs.
    hits.sort(key=lambda h: (-h[0], len(h[1].summary), h[1].id))
    out = []
    for sc, c in hits:
        if V.verify(c)["verdict"] != V.VERIFIED:
            continue                       # not confirmed HERE: not said
        out.append({"claim": c.id, "topic": c.topic, "summary": c.summary,
                    "score": sc})
        if len(out) >= max_cards:
            break
    return out


def _single_name(topic: str) -> tuple[str, bool] | None:
    """(the name, was it QUALIFIED) if the topic is ONE name and nothing else.

    Prose gets no generated line at all: for "reading files in python" an
    absence line would announce that `reading` is not a module — true, and
    noise. And a BARE name only ever gets a presence line (or its case twin):
    `maketrans` is a function, so "`import maketrans` does NOT exist" is true,
    useless, and reads like a verdict on the thing the caller asked about. An
    ABSENCE is stated only when the caller said which namespace they meant —
    `import imp`, `std.io`, `"io/ioutil"`.
    """
    t = (topic or "").strip().strip("`")
    m = re.fullmatch(r"(?:import|from|#include|use)\s+[\"<]?([A-Za-z_][\w./:]*)[\">]?;?", t)
    if m:
        return m.group(1), True
    t = t.strip("\"")
    if not re.fullmatch(r"[A-Za-z_][\w./:]*", t):
        return None
    return t, bool(re.match(r"std(\.|::)\w", t) or "/" in t)


def generated_lines(lang: str, topic: str) -> list[str]:
    """What the installed toolchain says about the NAME in `topic`, or [].

    Mechanical only. A name is looked up in the list the toolchain reported
    (introspect.facts — asked live, so it cannot describe another compiler);
    the one "suggestion" ever made is the SAME name in a different case,
    because `std.io` vs `std.Io` is a lookup, not a guess. No edit distance.
    """
    try:
        from lang import introspect as I         # noqa: PLC0415
    except ImportError:                           # pragma: no cover
        return []
    rec = I.facts(lang)
    if not rec:
        return []
    facts = rec.get("facts") or {}
    tc = P._short_version(rec.get("toolchain") or "") or "installed"
    asked = _single_name(topic)
    if not asked:
        return []
    name, qualified = asked
    out: list[str] = []

    def lookup(pool: list[str], key: str, what: str, show) -> None:
        if key in pool:
            out.append(f"{show(key)} exists: it is one of the {len(pool)} "
                       f"{what} of the {lang} {tc} installed here")
            return
        twins = [p for p in pool if p.lower() == key.lower()]
        if not twins and not qualified:
            return                         # see _single_name: no bare absences
        out.append(f"{show(key)} does NOT exist: it is not one of the "
                   f"{len(pool)} {what} of the {lang} {tc} installed here"
                   + (f" — the installed spelling is {show(twins[0])}"
                      if twins else ""))

    if lang == "zig":
        # `std.io.getStdOut` -> the top-level name is `io`.
        parts = name.split(".")
        if parts[0] == "std" and len(parts) > 1:
            parts = parts[1:]
        lookup(list(facts.get("top_level") or []), parts[0],
               "top-level names in std", lambda k: f"`std.{k}`")
    elif lang == "python":
        lookup(list(facts.get("modules") or []), name.split(".")[0],
               "stdlib modules", lambda k: f"`import {k}`")
    elif lang == "go":
        lookup(list(facts.get("packages") or []), name.strip("/"),
               "std packages", lambda k: f'`import "{k}"`')
    elif lang in ("cpp", "c"):
        # PRESENCE ONLY. A feature-test macro that is absent does not prove
        # the feature is: plenty of library features have no macro at all.
        bare = re.sub(r"^std::", "", name).replace("::", "_").lower()
        wanted = {name, f"__cpp_{bare}", f"__cpp_lib_{bare}"}
        for level in facts.get("levels_supported") or []:
            for macro in (facts.get("added_at") or {}).get(level, []):
                if macro in wanted:
                    out.append(f"`{macro}` is first defined at -std={level} "
                               f"by the {lang} compiler installed here ({tc}) "
                               f"— test it with `#if defined({macro})`")
    return out


def topic_reference(lang: str, topic: str,
                    budget_chars: int = MAX_TOPIC_CHARS) -> str:
    """The text for one topic, or "" when nothing verified answers it.

    "" for the same reasons pack_for() returns None — not allow-listed, no
    toolchain — plus the one that is this function's own: nothing matched.
    """
    _, allowed = P.allow_list()
    if lang not in allowed:
        return ""
    version = P.installed_version(lang)
    if not version:
        return ""
    cards = verified_cards(lang, topic)
    gen = generated_lines(lang, topic)
    if not cards and not gen:
        return ""
    head = (f"=== {lang} {P._short_version(version)}: verified reference for "
            f"{' '.join(topic.split())!r} (beast-lang) ===\n")
    lines = []
    if cards:
        lines.append("CONFIRMED by compiling on this machine (the old form "
                     "was checked to fail, the new form to compile):")
        lines += [f"- {c['summary']}" for c in cards]
    if gen:
        lines.append("GENERATED by asking the installed toolchain:")
        lines += [f"- {g}" for g in gen]
    # Trim from the END, whole lines only, and SAY so: generated lines go
    # first, exactly as in a pack, because VERIFIED means a fixture proved it.
    kept, total = [], len(head)
    for ln in lines:
        if total + len(ln) + 1 > budget_chars:
            break
        kept.append(ln)
        total += len(ln) + 1
    dropped = len(lines) - len(kept)
    # A tier label whose facts were all trimmed announces a tier the text
    # does not contain — the same overstated-provenance bug the pack header
    # once had. Labels are only kept in front of something.
    while kept and not kept[-1].startswith("- "):
        kept.pop()
    if dropped:
        kept.append(f"[{dropped} more line(s) not shown — narrow the topic]")
    if not any(ln.startswith("- ") for ln in kept):
        return ""                          # a header with no fact is padding
    return head + "\n".join(kept) + "\n"


def served_languages() -> list[str]:
    """Languages this rig will actually answer for: allow-listed AND the
    toolchain is installed (the requested-vs-served split packs.main makes)."""
    _, allowed = P.allow_list()
    return [lang for lang in allowed if P._toolchain_ok(lang)]


def main(argv: list[str] | None = None) -> int:
    import argparse                                # noqa: PLC0415
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("lang")
    ap.add_argument("topic", nargs="+")
    a = ap.parse_args(argv)
    out = topic_reference(a.lang, " ".join(a.topic))
    print(out or f"(no verified reference for {' '.join(a.topic)!r} in {a.lang})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
