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
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENTS = os.path.dirname(_HERE)
_REPO = os.path.dirname(_AGENTS)
if _AGENTS not in sys.path:
    sys.path.insert(0, _AGENTS)

from lang import drivers as D        # noqa: E402
from lang import verify as V         # noqa: E402

CLAIMS_DIR = os.path.join(_HERE, "claims")
HANDWRITTEN_DIR = os.path.join(_AGENTS, "packs")
#: 4 chars/token is the estimate the zig pack already budgets against.
CHARS_PER_TOKEN = 4
DEFAULT_BUDGET_TOKENS = int(os.environ.get("OPENBEAST_LANG_PACK_BUDGET", "2000"))


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
        return raw, [lang for lang in sorted(_claim_langs()) if _toolchain_ok(lang)]
    wanted = [x.strip().lower() for x in raw.split(",") if x.strip()]
    return raw, [w for w in wanted if w in _claim_langs()]


def _claim_langs() -> set[str]:
    return {c.lang for c in V.load_claims(CLAIMS_DIR)}


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
        # A pack for 0.16 serves 0.16.0; a pack for 0.15 does NOT serve 0.16.
        if short.startswith(stamped):
            path = os.path.join(HANDWRITTEN_DIR, name)
            return path, open(path).read()
    return None


def _short_version(version: str) -> str:
    """'zig 0.16.0' / 'rustc 1.98.1 (…)' / '0.16.0' -> a comparable prefix."""
    for tok in version.replace("(", " ").split():
        if tok and tok[0].isdigit():
            return tok
    return version.strip()


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
    return [p for p in (pack_for(lang, budget_tokens) for lang in allowed) if p]


# --------------------------------------------------------------------------
# 3. the renderer
# --------------------------------------------------------------------------

def render(lang: str, version: str, budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> Pack | None:
    """Render a pack from the VERIFIED claims for `lang`.

    Only VERIFIED survives. A claim that came back NOT_A_BREAK, BACKWARDS,
    NEW_FAILS, FIXTURE_BROKEN or UNVERIFIABLE is excluded by construction —
    the pack is a statement about what the installed toolchain confirmed, and
    anything else in it would be a confident falsehood.
    """
    claims = [c for c in V.load_claims(CLAIMS_DIR) if c.lang == lang]
    if not claims:
        return None
    lines, skipped, n = [], [], 0
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
    if not lines:
        return None

    head = (f"=== Language notes: {lang} {_short_version(version)} "
            f"(beast-lang, generated) ===\n"
            f"Toolchain on this machine: {version}. Every line below was "
            f"CONFIRMED by compiling against it — the old form was checked to "
            f"fail and the new form to compile. Nothing here is from a web "
            f"search or from model priors.\n\n")
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
        body = "\n".join(kept) + "\n"
        n = len(kept)
    return Pack(lang, version, "generated", head + body, claims=n, skipped=skipped)


# --------------------------------------------------------------------------
# delivery: which packs a given task should receive
# --------------------------------------------------------------------------

#: Substrings that identify a task's language. Deliberately dumb and explicit:
#: a clever classifier that guesses wrong injects the wrong language's notes.
LANG_HINTS = {
    "zig": (".zig", " zig ", "zig:"),
    "cpp": (".cpp", ".hpp", ".cc", "c++", "cpp:"),
    "c": (".c ", ".h ", " c ", "c:"),
    "rust": (".rs", " rust ", "rust:", "cargo"),
    "go": (".go", " go ", "go:", "golang"),
    "python": (".py", " python ", "python:"),
}


def languages_in(text: str) -> list[str]:
    """Languages a task text mentions, most specific first.

    Used where the language IS known (an eval unit names its file). For an
    interactive session it is NOT known up front, and the answer there is the
    escalation path — inject the pack when the compiler reports an error in
    that language — not a guess made before the model writes anything.
    """
    low = f" {text.lower()} "
    hits = []
    for lang, needles in LANG_HINTS.items():
        if any(nd in low for nd in needles):
            hits.append(lang)
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
    print(f"LANG_PACKS={raw!r} -> active: {', '.join(allowed) or '(none)'}")
    langs = [a.lang] if a.lang else allowed
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
