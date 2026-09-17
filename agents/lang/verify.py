#!/usr/bin/env python3
"""Verify language claims against the toolchains installed on THIS machine.

A claim is the unit beast-lang is built out of:

    {"id": "arraylist-unmanaged", "lang": "zig", "topic": "ArrayList",
     "old": ["var l = std.ArrayList(u8).init(gpa);"],     # must FAIL
     "new": ["var l: std.ArrayList(u8) = .empty;"],        # must COMPILE
     "doc_must_contain": ["append(gpa, x)"]}

and the verdicts are deliberately more than pass/fail, because the
interesting outcomes are the ones that say the CLAIM is wrong rather than the
code:

  VERIFIED      every new form compiles and every old form fails. Safe to
                auto-inject.
  NEW_FAILS     a new form does not compile. The claim is WRONG, and this is
                the dangerous case: an authoritative-looking line that would
                teach a model something false.
  NOT_A_BREAK   an old form still compiles. The migration is real only if the
                old way stopped working; otherwise presenting it as removed
                is misleading. (The zig pack already handles one of these by
                hand: the 4-arg `format` method still compiles, so it is NOT
                listed as a break.)
  BACKWARDS     the old form compiles and the new one does not. The worst
                outcome available: a pack built from this would teach the
                REVERSE of the truth, confidently. Separated from NEW_FAILS
                because "nobody's code compiles" and "we have it exactly
                inverted" call for different reactions.
  FIXTURE_BROKEN  both halves fail — the fixture, not the language, is wrong.
  UNVERIFIABLE  no toolchain for this language on this machine. NEVER treated
                as a pass; on this rig that is Swift.

Doc linkage (`doc_must_contain`) closes the other direction: a verified claim
that no pack mentions is knowledge we proved and then failed to deliver.

usage:
  python3 agents/lang/verify.py --claims agents/lang/claims [--lang zig]
                               [--pack agents/packs/zig-0.16.md] [--json]
exit 0 only when every claim is VERIFIED (UNVERIFIABLE is reported and, with
--strict, also fails).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

from lang import drivers as D  # noqa: E402

VERIFIED = "VERIFIED"
NEW_FAILS = "NEW_FAILS"
BACKWARDS = "BACKWARDS"
NOT_A_BREAK = "NOT_A_BREAK"
FIXTURE_BROKEN = "FIXTURE_BROKEN"
UNVERIFIABLE = "UNVERIFIABLE"
GOOD = (VERIFIED,)


class Claim:
    """One checkable assertion about a language.

    Two axes show up in practice and the model has to carry both, which the
    SECOND language made obvious:

    * STALENESS (zig): one toolchain, two idioms. `old` must fail and `new`
      must compile on the installed compiler.
    * AVAILABILITY (C/C++/Rust): one idiom, two language versions. The same
      code compiles under `new_variant` and fails under `old_variant` —
      "this needs C++20", "this needs edition 2021".

    So `old_variant`/`new_variant` may differ, and when `old` is empty while
    `old_variant` is set, the `new` snippets are re-compiled under the old
    variant instead of being duplicated in the JSON. A duplicated snippet is
    one that drifts.
    """

    __slots__ = ("id", "lang", "topic", "old", "new", "variant", "old_variant",
                 "new_variant", "doc_must_contain", "note", "summary", "source")

    def __init__(self, raw: dict, source: str, base: str):
        self.source = source
        self.id = raw.get("id") or raw.get("entry") or "<unnamed>"
        self.lang = raw["lang"]
        self.topic = raw.get("topic", "")
        self.variant = raw.get("variant")
        self.old_variant = raw.get("old_variant", self.variant)
        self.new_variant = raw.get("new_variant", self.variant)
        self.note = raw.get("note", "")
        #: the ONE LINE that would have prevented the mistake. A pack is a
        #: token budget, and a claim's fixtures are whole programs — the
        #: summary is what actually ships. A claim without one can still be
        #: verified; it just cannot be delivered.
        self.summary = raw.get("summary", "")
        self.doc_must_contain = list(raw.get("doc_must_contain") or
                                     raw.get("pack_must_contain") or [])
        self.old = [_read(s, base) for s in (raw.get("old") or [])]
        self.new = [_read(s, base) for s in (raw.get("new") or [])]

    def __repr__(self) -> str:
        return f"Claim({self.lang}:{self.id})"


def _read(snippet: str, base: str) -> str:
    """A claim carries code inline, or names a fixture file relative to the
    claim set. Referencing files keeps 75 existing zig fixtures usable without
    copying them into JSON, where they would immediately start to drift."""
    if "\n" in snippet or not snippet.endswith(
            (".zig", ".c", ".cpp", ".rs", ".go", ".py")):
        return snippet
    path = snippet if os.path.isabs(snippet) else os.path.join(base, snippet)
    with open(path) as fh:
        return fh.read()


def load_claims(path: str, lang: str | None = None,
                problems: list[str] | None = None) -> list[Claim]:
    """Every claim that can be LOADED; what could not be is reported.

    This runs on the serving path (allow_list, pack_for, cards_for), and it
    used to raise: one corrupt JSON file, or one claim naming a fixture that
    is not on disk, took down every language at once. The zig set reads
    ../../../tests/fixtures/zig016/*, so an install without tests/ lost its
    cpp and python packs to a missing ZIG file. Now the damage stays where it
    is — a bad file loses its own claims, a bad claim loses itself — and the
    reason lands in `problems` when the caller passes a list. The CLI below
    does, and FAILS on it: tolerance is for serving, never for the gate.
    """
    files = []
    try:
        if os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                if name.endswith(".json"):
                    files.append(os.path.join(path, name))
        else:
            files.append(path)
    except OSError as e:
        files = []
        if problems is not None:
            problems.append(f"{path}: {e}")
    out: list[Claim] = []
    for f in files:
        try:
            with open(f) as fh:
                doc = json.load(fh)
            if not isinstance(doc, dict):
                raise ValueError("not a JSON object")
            base = doc.get("fixture_dir") or os.path.dirname(os.path.abspath(f))
            if not os.path.isabs(base):
                base = os.path.join(os.path.dirname(os.path.abspath(f)), base)
            raws = list(doc.get("claims") or [])
        except (OSError, ValueError, TypeError, AttributeError) as e:
            if problems is not None:
                problems.append(f"{os.path.basename(f)}: unreadable claim set "
                                f"({e.__class__.__name__}: {e})")
            continue
        for raw in raws:
            try:
                raw.setdefault("lang", doc.get("lang"))
                raw.setdefault("variant", doc.get("variant"))
                c = Claim(raw, os.path.basename(f), base)
            except (OSError, ValueError, TypeError, KeyError,
                    AttributeError) as e:
                if problems is not None:
                    rid = raw.get("id", "?") if isinstance(raw, dict) else "?"
                    problems.append(f"{os.path.basename(f)}: claim {rid!r} "
                                    f"dropped ({e.__class__.__name__}: {e})")
                continue
            if lang and c.lang != lang:
                continue
            out.append(c)
    return out


def old_snippets(claim: Claim) -> tuple[list[str], str | None]:
    """(the snippets that must FAIL, the variant to compile them under).

    An AVAILABILITY claim carries no explicit `old` code — the old form IS the
    new code compiled under the older language version. Both verify() and the
    escalation index need that rule, and two copies of it would drift, so it
    lives here.
    """
    if claim.old:
        return claim.old, claim.old_variant
    if claim.old_variant and claim.old_variant != claim.new_variant:
        return claim.new, claim.old_variant
    return [], claim.old_variant


#: (toolchain version, the claim's CODE) -> (verdict, detail). A verdict is a
#: pure function of those two, and packs.render() asked for it again on every
#: call: ~0.5 s per pack_for("cpp"), and 74 zig compiles per call the day the
#: hand-written pack stops matching. Keyed on the snippet TEXT rather than the
#: claims file's mtime, so an edited fixture file is seen too. Only the
#: verdict is remembered — summary/note/topic are read off the claim each
#: time — and a verdict that is not the toolchain's own is never stored.
_VERDICTS: dict = {}


def verify(claim: Claim) -> dict:
    d = D.driver_for(claim.lang)
    if d is None or not d.available():
        why = ("no driver" if d is None else f"{d.exe} not installed")
        return {"claim": claim.id, "lang": claim.lang, "topic": claim.topic,
                "verdict": UNVERIFIABLE, "detail": why, "source": claim.source}

    old_snips, old_variant = old_snippets(claim)
    version = d.version()
    key = (claim.lang, version, tuple(claim.new), claim.new_variant,
           tuple(old_snips), old_variant, claim.old_variant)
    if version and key in _VERDICTS:
        verdict, detail = _VERDICTS[key]
    else:
        verdict, detail, judged = _judge(claim, d, old_snips, old_variant)
        if version and judged:
            _VERDICTS[key] = (verdict, detail)
    return {"claim": claim.id, "lang": claim.lang, "topic": claim.topic,
            "verdict": verdict, "detail": detail, "source": claim.source,
            "variant": (f"{claim.old_variant}->{claim.new_variant}"
                        if claim.old_variant != claim.new_variant
                        else claim.variant),
            "doc_must_contain": claim.doc_must_contain,
            "summary": claim.summary, "note": claim.note}


def _not_judged(r) -> bool:
    """A timeout, a program that would not start, or a REFUSAL: none of them
    is the toolchain's opinion of the snippet. The refusal case is the subtle
    one — it returns ok=False like a compile error does, and a synthetic claim
    whose OLD form was valid C carrying the comment
    `/* never #include "/etc/passwd" */` came back VERIFIED on the strength of
    a refusal."""
    return bool(getattr(r, "transient", False) or getattr(r, "refused", False))


def _judge(claim: Claim, d, old_snips, old_variant) -> tuple[str, str, bool]:
    """(verdict, detail, judged). `judged` is False when a compile never
    produced the toolchain's verdict (timeout, could not start): that is
    UNVERIFIABLE — an OLD form "failing" because the compiler was killed is
    not evidence of a break — and it is not remembered."""
    bad_new, still_ok_old = [], []
    for i, snip in enumerate(claim.new, 1):
        r = d.compile_source(d.wrap(snip), claim.new_variant)
        if _not_judged(r):
            return UNVERIFIABLE, f"new[{i}] was never judged: {r.detail}", False
        if not r:
            bad_new.append((i, r.detail.splitlines()[0] if r.detail else "?"))
    # An AVAILABILITY claim gives no `old` code — the old form IS the new
    # code, compiled under the older language version. Re-use it rather than
    # duplicating the snippet, which would drift.
    for i, snip in enumerate(old_snips, 1):
        r = d.compile_source(d.wrap(snip), old_variant)
        if _not_judged(r):
            return UNVERIFIABLE, f"old[{i}] was never judged: {r.detail}", False
        if r:
            still_ok_old.append(i)

    if bad_new and old_snips and still_ok_old:
        # Inverted: the form we call stale is the one that works.
        verdict, detail = BACKWARDS, (
            f"old[{still_ok_old[0]}] compiles but new[{bad_new[0][0]}] does "
            f"NOT — the claim is inverted: {bad_new[0][1]}")
    elif bad_new and old_snips:
        verdict, detail = FIXTURE_BROKEN, (
            f"new[{bad_new[0][0]}] fails AND every old form fails: {bad_new[0][1]}")
    elif bad_new:
        verdict, detail = NEW_FAILS, (
            f"new[{bad_new[0][0]}] does not compile: {bad_new[0][1]}")
    elif still_ok_old:
        where = (f" under {claim.old_variant}" if claim.old_variant else "")
        verdict, detail = NOT_A_BREAK, (
            f"old[{still_ok_old[0]}] still compiles{where} — not a removal")
    else:
        verdict, detail = VERIFIED, (
            f"{len(claim.new)} new compile"
            + (f" ({claim.new_variant})" if claim.new_variant else "")
            + (f", {len(old_snips)} old fail"
               + (f" ({claim.old_variant})" if claim.old_variant else "")
               if old_snips else ", no old form given"))
    return verdict, detail, True


def check_doc_linkage(results: list[dict], pack_path: str) -> list[str]:
    """A verified claim the pack never mentions is knowledge we proved and
    then failed to deliver. Reported, not fatal."""
    text = open(pack_path).read()
    missing = []
    for r in results:
        if r["verdict"] != VERIFIED:
            continue
        for needle in r.get("doc_must_contain") or []:
            if needle not in text:
                missing.append(f"{r['claim']}: pack is missing {needle!r}")
    return missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--claims", default=os.path.join(_HERE, "claims"))
    ap.add_argument("--lang")
    ap.add_argument("--pack", help="pack/summary file to check doc linkage against")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="UNVERIFIABLE also fails (use where a toolchain is expected)")
    a = ap.parse_args(argv)

    problems: list[str] = []
    claims = load_claims(a.claims, a.lang, problems)
    for msg in problems:
        print(f"CLAIM SET PROBLEM: {msg}", file=sys.stderr)
    if not claims:
        print(f"no claims found in {a.claims}"
              + (f" for lang={a.lang}" if a.lang else ""), file=sys.stderr)
        return 1
    results = [verify(c) for c in claims]

    if a.json:
        print(json.dumps({"results": results}, indent=2))
    else:
        print(f"\n{'verdict':14} {'lang':7} {'variant':8} claim")
        print("-" * 78)
        for r in results:
            print(f"{r['verdict']:14} {r['lang']:7} {str(r.get('variant') or '-'):8} "
                  f"{r['claim']}")
            if r["verdict"] not in GOOD:
                print(f"{'':14} └─ {r['detail'][:120]}")
        counts: dict[str, int] = {}
        for r in results:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        for lang, drv in sorted(D.DRIVERS.items()):
            if any(r["lang"] == lang for r in results):
                print(f"  {lang}: {drv.version() or 'ABSENT'}")
        for lang in D.NO_TOOLCHAIN:
            if any(r["lang"] == lang for r in results):
                print(f"  {lang}: no toolchain on this machine — claims stay CURATED")

    if a.pack:
        missing = check_doc_linkage(results, a.pack)
        if missing:
            print("\nDOC LINKAGE — verified knowledge the pack does not deliver:")
            for m in missing:
                print(f"  {m}")
        else:
            print(f"\ndoc linkage: every verified claim appears in {os.path.basename(a.pack)}")

    # UNVERIFIABLE is excused only when it means "no toolchain here". A
    # compile that was killed or never started is a failure of this run.
    bad = [r for r in results
           if r["verdict"] not in GOOD
           and (a.strict or r["verdict"] != UNVERIFIABLE
                or "never judged" in r["detail"])]
    # A claim that could not even be LOADED is not a pass. load_claims
    # tolerates it so that serving degrades per-language; the gate does not.
    return 1 if (bad or problems) else 0


if __name__ == "__main__":
    sys.exit(main())
