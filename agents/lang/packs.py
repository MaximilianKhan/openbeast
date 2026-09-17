#!/usr/bin/env python3
"""Pack resolution and rendering — what a model actually receives.

The point of beast-lang (docs/BEAST_LANG_PLAN.md) is that ANY model dropped
into this rig knows how to use a language, without asking, and **without the
network**. That last part is the whole local-first promise: a machine with no
internet can still write CURRENT code, because the facts came from the
toolchain sitting next to it rather than from a web search or from weights
that were frozen two versions ago.

Three things live here:

1. THE DEPLOYMENT ALLOW LIST. Which languages this install cares about is a
   deployment decision, not a property of our eval suite. `LANG_PACKS=auto`
   (the default) means every language with an installed toolchain AND
   verified claims; `LANG_PACKS=cpp,zig` pins it; `LANG_PACKS=off` disables
   the feature. One rig wants C++ and zig; another drops in a model that
   needs Rust and Go, and gets them by editing one line.

2. THE DRIFT GUARD. A pack is served only if its stamped toolchain version
   matches the INSTALLED one. A pack describing zig 0.15 handed to an agent
   running against zig 0.16 is worse than no pack — it is wrong with
   authority. This mirrors the abort the zig pack already performs at eval
   start, and it is the reason `installed_version` is re-read every time
   rather than cached.

3. THE RENDERER. A pack is a token budget, so what ships is one line per
   VERIFIED claim (`summary`), never the fixtures — those are whole programs.
   A claim with no summary is verified but undeliverable, and is reported as
   such rather than silently dropped. Nothing UNVERIFIED, NOT_A_BREAK,
   BACKWARDS or UNVERIFIABLE is ever rendered: the pack contains only what
   the installed toolchain confirmed.

A hand-written pack (agents/packs/<lang>-<ver>.md, as zig has) takes
precedence over a generated one — it is better prose, and it is already
fixture-backed. Generated packs are how a language gets covered before anyone
writes that prose.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENTS = os.path.dirname(_HERE)
_REPO = os.path.dirname(_AGENTS)
if _AGENTS not in sys.path:
    sys.path.insert(0, _AGENTS)

from lang import _proc               # noqa: E402
from lang import drivers as D        # noqa: E402
from lang import verify as V         # noqa: E402

CLAIMS_DIR = os.path.join(_HERE, "claims")
HANDWRITTEN_DIR = os.path.join(_AGENTS, "packs")
#: 4 chars/token is the estimate the zig pack already budgets against.
CHARS_PER_TOKEN = 4
# Parsed with a fallback — a mistyped value used to be a ValueError at import.
DEFAULT_BUDGET_TOKENS = _proc.env_number("OPENBEAST_LANG_PACK_BUDGET", 2000, int, 1)


# --------------------------------------------------------------------------
# 1. the deployment allow list
# --------------------------------------------------------------------------

def _conf_value(key: str, missing=""):
    """Read a key from openbeast.conf without sourcing it.

    Sourcing a shell file to read one value RUNS it, and this module is
    imported by the serving path. Same posture as the rest of the repo's
    python conf readers.

    `missing` is what to return when the key is absent — callers that need to
    distinguish "absent" from "present but empty" pass None.
    """
    path = os.path.join(_REPO, "openbeast.conf")
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    # openbeast.conf values are NOT shell — a trailing comment
                    # is literal text, which has bitten this repo before
                    # (CHAT_OPERATORS). Take the value verbatim, minus quotes.
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return missing


def allow_list() -> tuple[str, list[str]]:
    """(raw setting, resolved languages). Env beats conf; `auto` is default.

    PRESENCE, not truthiness. `LANG_PACKS=` written out explicitly means the
    operator turned this off; only an ABSENT setting means `auto`. Reading it
    with an `or` chain conflated the two and silently activated every language
    on a rig where someone had deliberately emptied the key.
    """
    if "OPENBEAST_LANG_PACKS" in os.environ:
        raw = os.environ["OPENBEAST_LANG_PACKS"].strip()
    else:
        conf = _conf_value("LANG_PACKS", missing=None)
        raw = "auto" if conf is None else conf.strip()
    if raw.lower() in ("off", "false", "none", "0", ""):
        return raw, []
    if raw.lower() == "auto":
        return raw, [lang for lang in sorted(_eligible_langs())
                     if _toolchain_ok(lang)]
    wanted = [x.strip().lower() for x in raw.split(",") if x.strip()]
    # NO toolchain filter here. An explicit list is the operator's REQUEST,
    # and availability is a different question — two pre-existing tests pin
    # that contract, and CI was right to fail when this filtered. The real
    # defect the review found was the REPORTING: doctor printed a green
    # "packs active for: cpp, zig, go, rust" on a box with none of them
    # installed. That belongs where the claim is made, not in the config
    # reader, so the CLI below distinguishes requested from actually served.
    return raw, [w for w in wanted if w in _eligible_langs()]


def _claim_langs() -> set[str]:
    # load_claims degrades PER FILE and per claim, so one corrupt claim set
    # (or the zig set's fixtures going missing with tests/) cannot take the
    # other languages off the allow list. The belt is for whatever it missed.
    try:
        return {c.lang for c in V.load_claims(CLAIMS_DIR) if c.lang}
    except Exception:                                 # noqa: BLE001
        return set()


def _eligible_langs() -> set[str]:
    """Languages this rig can say something TRUE about.

    Claims are no longer the gate, and that change is the point of L1: a
    language with a hand-authored claims file was the only thing that could
    ever be served, so `go` — whose toolchain is installed and can be asked
    directly what its std contains — was unreachable no matter what the
    operator put in LANG_PACKS. That defeats the requirement this layer exists
    for ("any model dropped in can know how to use a language, irrespective of
    what we happened to test"): a fact does not need a fixture to be true if
    the compiler is the one saying it.

    Breadth here is close to free at delivery time: a pack is injected only
    for the language a task is actually in (see `languages_in`), so activating
    every toolchain on the box costs nothing on a task that never touches it.
    """
    langs = set(_claim_langs())
    try:
        from lang import introspect as I         # noqa: PLC0415
    except ImportError:                           # pragma: no cover
        return langs
    return langs | set(I.PROBES)


def _toolchain_ok(lang: str) -> bool:
    d = D.driver_for(lang)
    return bool(d and d.available())


# --------------------------------------------------------------------------
# 2. resolution + the drift guard
# --------------------------------------------------------------------------

class Pack:
    __slots__ = ("lang", "version", "kind", "path", "text", "claims", "skipped")

    def __init__(self, lang, version, kind, text, path=None, claims=0, skipped=()):
        self.lang, self.version, self.kind = lang, version, kind
        self.text, self.path = text, path
        self.claims, self.skipped = claims, list(skipped)

    @property
    def tokens(self) -> int:
        return len(self.text) // CHARS_PER_TOKEN

    @property
    def sha8(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:8]

    def __repr__(self) -> str:
        return (f"Pack({self.lang} {self.version} {self.kind} "
                f"{self.tokens}tok sha={self.sha8})")


def installed_version(lang: str) -> str | None:
    d = D.driver_for(lang)
    return d.version() if d and d.available() else None


def _handwritten(lang: str, version: str | None) -> tuple[str, str] | None:
    """(path, text) for a hand-written pack whose stamped version matches."""
    if not os.path.isdir(HANDWRITTEN_DIR) or not version:
        return None
    short = _short_version(version)
    for name in sorted(os.listdir(HANDWRITTEN_DIR)):
        if not name.endswith(".md") or not name.startswith(f"{lang}-"):
            continue
        stamped = name[len(lang) + 1:-3]
        if _stamp_serves(stamped, short):
            path = os.path.join(HANDWRITTEN_DIR, name)
            try:
                with open(path) as fh:
                    return path, fh.read()
            except OSError:
                continue                   # unreadable is absent, not a crash
    return None


def _stamp_serves(stamped: str, short: str) -> bool:
    """A pack for 0.16 serves 0.16.0; a pack for 0.15 does NOT serve 0.16.

    Two things the bare `short.startswith(stamped)` got wrong:
      * it made a RELEASE-stamped pack serve a DEV build — 0.16 matched
        0.16.0-dev.412 — while every other drift check in this module treats
        the prerelease part as significant, because a dev build is exactly
        where std moves. A dev build is served only by a pack stamped for it.
      * it compared characters, not components: a pack for 0.1 served 0.16.
    """
    def pre(v: str) -> bool:
        return "-" in v or "+" in v
    if not stamped or pre(short) != pre(stamped):
        return False
    return short == stamped or short.startswith(stamped + ".")


def _short_version(version: str) -> str:
    """'zig 0.16.0' / 'rustc 1.98.1 (…)' / '0.16.0' -> a comparable prefix.

    Matches the NUMBER anywhere in the token, not only a token that starts
    with a digit: `go version go1.26.2 linux/amd64` failed the old rule
    entirely, so go's pack header read "go go version go1.26.2 linux/amd64"
    and every version comparison for go was a whole-banner string compare.
    It only surfaced when L1 made go eligible at all. Same rule as
    introspect._short, deliberately — two helpers answering "which version is
    this" differently is a drift guard waiting to disagree with itself.
    """
    # The prerelease suffix is part of the version: without it a zig
    # dev-build move (0.16.0-dev.412 -> 0.16.0-dev.500) compared EQUAL and the
    # drift guard served stale facts. Kept identical to introspect._short,
    # which a test pins — two answers to "which version is this" is a drift
    # guard waiting to disagree with itself.
    m = re.search(r"\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.]+)?", version or "")
    return m.group(0) if m else (version or "").strip()


def pack_for(lang: str, budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> Pack | None:
    """The pack this rig would hand a model for `lang`, or None.

    None means: not allow-listed, no toolchain, or nothing verified to say.
    Never a stale pack — that is the case this function exists to prevent.
    """
    _, allowed = allow_list()
    if lang not in allowed:
        return None
    version = installed_version(lang)
    if not version:
        return None
    hw = _handwritten(lang, version)
    if hw:
        path, text = hw
        return Pack(lang, version, "handwritten", text, path=path)
    return render(lang, version, budget_tokens)


def active_packs(budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> list[Pack]:
    _, allowed = allow_list()
    out = []
    for lang in allowed:
        try:                       # one language failing is ONE pack missing
            p = pack_for(lang, budget_tokens)
        except Exception:                             # noqa: BLE001
            p = None
        if p:
            out.append(p)
    return out


# --------------------------------------------------------------------------
# 3. the renderer
# --------------------------------------------------------------------------

def _generated_lines(lang: str, version: str) -> tuple[list[str], str]:
    """L1 facts for `lang`, or ([], why-not).

    THE SAME DRIFT GUARD AS EVERYTHING ELSE HERE. A generated artifact
    describes one toolchain; served against a different one it is a confident
    falsehood, which is the exact failure mode this module exists to prevent.
    `introspect.check()` distinguishes the two ways that goes wrong: STALE
    (the compiler moved — regenerate) and DRIFTED (same compiler, different
    facts — somebody edited a generated file). Neither is served.
    """
    # Absolute, like the D/V imports at the top of this file: packs.py is also
    # a CLI (`python3 agents/lang/packs.py`), and as __main__ it has no parent
    # package, so a relative import here failed at exactly the moment a human
    # was looking at the output — reporting "introspect unavailable" on a rig
    # where it was sitting right next to it.
    try:
        from lang import introspect as I         # noqa: PLC0415
    except ImportError as e:                      # pragma: no cover
        return [], f"generated: introspect unavailable ({e})"
    rec = I.facts(lang)
    if rec is None:
        return [], f"generated: no probe could observe {lang} on this machine"
    # The drift guard is STRUCTURAL here, not a check: I.facts() asks the
    # installed toolchain every call (~0.03s per language, measured), so the
    # facts cannot describe a compiler that is not the one present. The
    # stamped-vs-installed comparison every other source in this module needs
    # has nothing to compare — which is the point. `lang-introspect.sh check`
    # exists for the separate question of whether a COMMITTED artifact still
    # matches, for a rig that chooses to keep one for review.
    stamped = rec.get("toolchain") or ""
    if _short_version(stamped) != _short_version(version):
        # Belt: two different ways of asking the same machine for a version
        # disagreeing means one of them is wrong, and serving either would be
        # a guess.
        return [], (f"generated: introspect saw {stamped!r} but the pack path "
                    f"saw {version!r} — refusing to guess which is right")
    return I.render(lang), ""



def render(lang: str, version: str, budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> Pack | None:
    """Render a pack from the VERIFIED claims for `lang`.

    Only VERIFIED survives. A claim that came back NOT_A_BREAK, BACKWARDS,
    NEW_FAILS, FIXTURE_BROKEN or UNVERIFIABLE is excluded by construction —
    the pack is a statement about what the installed toolchain confirmed, and
    anything else in it would be a confident falsehood.
    """
    claims = [c for c in V.load_claims(CLAIMS_DIR) if c.lang == lang]
    gen_lines, gen_note = _generated_lines(lang, version)
    if not claims and not gen_lines:
        return None
    lines, skipped, n = [], [], 0
    if gen_note:
        skipped.append(gen_note)
    for c in claims:
        r = V.verify(c)
        if r["verdict"] != V.VERIFIED:
            skipped.append(f"{c.id}: {r['verdict']}")
            continue
        if not c.summary:
            skipped.append(f"{c.id}: VERIFIED but no summary (undeliverable)")
            continue
        variant = ""
        if c.old_variant and c.new_variant and c.old_variant != c.new_variant:
            variant = f" [{c.new_variant}]"
        lines.append(f"- {c.summary}{variant}")
        n += 1
    # GENERATED lines (L1) go in ALONGSIDE the VERIFIED ones, and they are
    # what makes this work for a language nobody wrote claims for: they cost
    # no fixture and no review because the toolchain is the author. They are
    # marked, because the two tiers earn belief differently — VERIFIED means a
    # fixture proved a migration, GENERATED means the compiler was asked what
    # it supports. Both are observations of THIS machine; neither is a prior.
    verified_n = len(lines)
    lines.extend(f"- {ln}" for ln in gen_lines)
    if not lines:
        return None

    # COUNTED AFTER THE TRIM, below. Building the header here from the
    # pre-trim counts meant the provenance line asserted facts the pack did
    # not contain: the budget loop drops from the END, so the generated lines
    # go first and the header still claimed them. A provenance header that
    # overstates is worse than no header — it is the one part a model has no
    # way to check.
    def _head(n_verified: int, n_generated: int) -> str:
        tiers = []
        if n_verified:
            tiers.append(f"{n_verified} CONFIRMED by compiling (the old form "
                         f"was checked to fail, the new form to compile)")
        if n_generated:
            tiers.append(f"{n_generated} GENERATED by asking the toolchain "
                         f"what it supports")
        return (f"=== Language notes: {lang} {_short_version(version)} "
                f"(beast-lang, generated) ===\n"
                f"Toolchain on this machine: {version}. " + "; ".join(tiers)
                + ". Nothing here is from a web search or from model priors.\n\n")

    head = _head(verified_n, len(gen_lines))
    body = "\n".join(lines) + "\n"
    budget_chars = budget_tokens * CHARS_PER_TOKEN
    if len(head) + len(body) > budget_chars:
        kept, total = [], len(head)
        for ln in lines:
            if total + len(ln) + 1 > budget_chars:
                skipped.append(f"budget: {len(lines) - len(kept)} lines dropped")
                break
            kept.append(ln)
            total += len(ln) + 1
        if not kept:
            # The provenance header alone does not fit. There is no honest
            # pack to serve: emitting the header anyway produced an
            # OVER-BUDGET pack that announced confirmed facts and carried
            # none. Serve nothing.
            return None
        # REBUILD THE HEADER for what SURVIVED. The trim drops from the end,
        # so the generated lines go first — and the old header, built before
        # the trim, kept claiming them. `lines` is verified-then-generated, so
        # a kept prefix of length k contains min(k, verified_n) verified lines
        # and the remainder generated.
        n_verified = min(len(kept), verified_n)
        head = _head(n_verified, len(kept) - n_verified)
        body = "\n".join(kept) + "\n"
        n = n_verified
        if len(head) + len(body) > budget_chars:
            # The rebuilt header can be shorter, never longer, than the one
            # measured above — but assert rather than assume, because serving
            # an over-budget pack is the failure this branch exists to avoid.
            return None
    return Pack(lang, version, "generated", head + body, claims=n, skipped=skipped)


# --------------------------------------------------------------------------
# delivery: which packs a given task should receive
# --------------------------------------------------------------------------

#: Patterns that identify a task's language. Deliberately dumb and explicit:
#: a clever classifier that guesses wrong injects the wrong language's notes.
#:
#: They were plain SUBSTRINGS, and that was too dumb in one specific way:
#: "Fix the basic: handler, then let us go to the next task" resolved to
#: ['c', 'go'] — "c:" sits inside "basic:" and "public:", "rust:" inside
#: "trust:", and " go " is an English verb. So names are matched as whole
#: words, and bare "go" is not a language at all without a stronger signal
#: next to it (.go, golang, go.mod, a go subcommand, "in go", "go code", …).
#: All matched against the lower-cased text.
_NOT_NAME = r"(?<![\w.+#/\\-])"           # not glued to a longer token
LANG_HINTS = {
    "zig": (r"\.zig\b", r"\bzig\b"),
    "cpp": (r"\.(?:cpp|hpp|cc|cxx|hh)\b", r"c\+\+", r"\bcpp\b"),
    # a lone "c" — not c++, c#, objective-c, a.c.b, or the C:\ drive
    "c": (r"\.[ch]\b(?!\.\w)", _NOT_NAME + r"c(?![\w+#/-]|:\\|\.\w)"),
    "rust": (r"\.rs\b", r"\brust\b", r"\bcargo\b", r"\brustc\b"),
    "go": (r"\.go\b", r"\bgolang\b", r"\bgo\.(?:mod|sum|work)\b",
           r"\bgo (?:build|run|test|vet|fmt|mod|get|install|generate)\b",
           r"\bin go\b", r"\bgoroutines?\b",
           r"\bgo (?:code|program|module|package|function|toolchain|compiler)\b"),
    "python": (r"\.py\b", r"\bpython[23]?\b"),
}
_LANG_RES = {lang: [re.compile(p) for p in pats]
             for lang, pats in LANG_HINTS.items()}
#: The one CASE-SENSITIVE signal: a capitalised "Go" in the middle of a
#: sentence ("write a Go HTTP server") is the proper noun. The verb is only
#: capitalised at the start of one, which the lookbehind excludes.
_GO_PROPER_NOUN = re.compile(r"(?<=[a-z,;:] )Go\b")


def languages_in(text: str) -> list[str]:
    """Languages a task text mentions, most specific first.

    Used where the language IS known (an eval unit names its file). For an
    interactive session it is NOT known up front, and the answer there is the
    escalation path — inject the pack when the compiler reports an error in
    that language — not a guess made before the model writes anything.
    """
    low = (text or "").lower()
    hits = [lang for lang, pats in _LANG_RES.items()
            if any(p.search(low) for p in pats)
            or (lang == "go" and _GO_PROPER_NOUN.search(text or ""))]
    # 'c' matches inside plenty of prose; only trust it if cpp did not hit.
    if "c" in hits and "cpp" in hits:
        hits.remove("c")
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Show what beast-lang would serve")
    ap.add_argument("lang", nargs="?")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET_TOKENS)
    ap.add_argument("--print", action="store_true", help="print the pack text")
    a = ap.parse_args(argv)

    raw, allowed = allow_list()
    # REQUESTED vs SERVED. doctor.sh greps this line, and reporting the
    # request as "active" printed a green row for languages the rig has no
    # toolchain for — a pack_for() of None each. Say both.
    served = [lang for lang in allowed if _toolchain_ok(lang)]
    unavailable = [lang for lang in allowed if lang not in served]
    print(f"LANG_PACKS={raw!r} -> active: {', '.join(served) or '(none)'}"
          + (f"  [requested but no toolchain here: {', '.join(unavailable)}]"
             if unavailable else ""))
    langs = [a.lang] if a.lang else served
    if not langs:
        print("\nnothing active. Set LANG_PACKS in openbeast.conf "
              "(auto | off | comma-separated languages).")
        return 0
    print(f"\n{'lang':8} {'kind':12} {'tok':>5}  {'sha':8} toolchain")
    print("-" * 74)
    rc = 0
    for lang in langs:
        p = pack_for(lang, a.budget)
        if not p:
            why = ("not allow-listed" if lang not in allowed
                   else "no toolchain" if not installed_version(lang)
                   else "nothing verified to say")
            print(f"{lang:8} {'—':12} {'—':>5}  {'—':8} {why}")
            continue
        print(f"{lang:8} {p.kind:12} {p.tokens:5}  {p.sha8:8} {p.version}")
        for s in p.skipped:
            print(f"{'':8} └─ skipped {s}")
        if p.tokens > a.budget:
            print(f"{'':8} └─ OVER BUDGET ({p.tokens} > {a.budget})")
            rc = 1
        if a.print:
            print("\n" + p.text)
    for lang in D.NO_TOOLCHAIN:
        print(f"{lang:8} {'—':12} {'—':>5}  {'—':8} "
              f"no toolchain here — claims stay CURATED, never auto-injected")
    return rc


if __name__ == "__main__":
    sys.exit(main())
