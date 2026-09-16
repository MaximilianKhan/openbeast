"""beast-lang L1 — the GENERATED tier.

What these tests are for: a fact this layer emits costs no fixture and no
review, so the ONLY thing standing between "mechanically extracted" and
"confidently wrong" is that the extraction really is mechanical. So the tests
here are mostly about the ways that can quietly stop being true —

  * a probe that observes nothing must RAISE, never return an empty fact set
    that renders as an authoritative-looking blank
  * `check` must catch a hand-edited artifact, including one whose editor also
    updated the recorded hash (it did not, until this suite)
  * the rendering budget must be spent on the availability answer, and a
    truncated rendering must SAY it was truncated — including when the notice
    itself is what does not fit
  * nothing here may depend on which toolchains this particular box has

That last one is why almost every test below either skips on an absent
toolchain or builds a synthetic record instead of reading the machine. CI has
no zig and a different gcc; a test that reads the ambient toolchain passes
here and proves nothing there.
"""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "agents"))

from lang import introspect as I     # noqa: E402
from lang import packs as P          # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Never write into the repo's own generated/ dir, and never let one
    test's live-probe cache leak into the next."""
    monkeypatch.setattr(I, "GEN_DIR", str(tmp_path / "generated"))
    monkeypatch.setattr(I, "_CACHE", {})
    return tmp_path


def _installed(lang: str) -> bool:
    try:
        return I.probe(lang) is not None
    except I.ProbeError:
        return False


# --------------------------------------------------------------------------
# probes observe, or say they could not
# --------------------------------------------------------------------------

def test_every_probe_either_observes_something_or_raises():
    """The failure mode this replaces: a probe that returns {} on a box
    without the toolchain, which then renders as a confident empty pack."""
    for lang in sorted(I.PROBES):
        try:
            rec = I.probe(lang)
        except I.ProbeError as e:
            assert str(e), f"{lang} raised with no explanation"
            continue
        assert rec["lang"] == lang
        assert rec["tier"] == "GENERATED"
        assert rec["facts"], f"{lang} reported success with no facts"
        assert rec["sha256"] and len(rec["sha256"]) == 16
        assert rec["commands"], f"{lang} recorded no way to reproduce itself"


def test_an_unknown_language_is_refused_not_invented():
    with pytest.raises(I.ProbeError) as e:
        I.probe("cobol")
    assert "cobol" in str(e.value)


def test_facts_never_raises_and_caches():
    """`facts` is the serving path: it must degrade to None, never explode."""
    calls = []
    monkey = I.PROBES.get("zig")
    assert I.facts("cobol") is None          # unknown language
    if monkey is None:                        # pragma: no cover
        return
    real = I.probe

    def counting(lang):
        calls.append(lang)
        return real(lang)

    I._CACHE.clear()
    I.probe = counting
    try:
        I.facts("zig"); I.facts("zig"); I.facts("zig")
    finally:
        I.probe = real
    assert len(calls) == 1, "the live probe must be cached per process"


# --------------------------------------------------------------------------
# the hash is over the facts, and only the facts
# --------------------------------------------------------------------------

def test_the_hash_ignores_the_toolchain_stamp():
    """Hashing the whole record would make every re-probe look like drift the
    moment a compiler's banner text changed without its behaviour changing."""
    facts = {"a": [1, 2], "b": {"c": "d"}}
    h = I._hash(facts)
    assert h == I._hash(dict(facts))
    assert h == I._hash({"b": {"c": "d"}, "a": [1, 2]}), "key order must not matter"
    assert h != I._hash({"a": [1, 2, 3], "b": {"c": "d"}})


def test_no_probe_puts_a_machine_specific_path_in_its_facts():
    """A home directory inside the hashed facts makes the artifact unique to
    one box: every other rig would read as DRIFTED, and the path would be
    committed for nothing. zig's std_dir was exactly that, and is now
    provenance instead of a fact."""
    home = os.path.expanduser("~")
    for lang in sorted(I.PROBES):
        try:
            rec = I.probe(lang)
        except I.ProbeError:
            continue
        blob = json.dumps(rec["facts"])
        assert home not in blob, f"{lang} facts embed {home}"
        assert "/home/" not in blob, f"{lang} facts embed an absolute home path"


# --------------------------------------------------------------------------
# check: the four states, and the forger
# --------------------------------------------------------------------------

def _synthetic(lang="zig", toolchain="9.9.9", facts=None):
    facts = facts or {"top_level": ["Io"], "modules": ["Io"]}
    return {"lang": lang, "tier": "GENERATED", "toolchain": toolchain,
            "commands": ["synthetic"], "facts": facts,
            "sha256": I._hash(facts)}


def _put(rec, lang="zig"):
    os.makedirs(I.GEN_DIR, exist_ok=True)
    with open(I.artifact_path(lang), "w") as fh:
        json.dump(rec, fh)


def test_check_reports_missing_without_failing():
    state, detail = I.check("zig")
    assert state == "MISSING"
    assert I.GEN_DIR in detail


def test_check_catches_an_edit_that_left_the_hash_field_alone():
    """The bug this test exists for: `check` compared the stored sha256 FIELD
    against a fresh probe, so appending a fake namespace to `facts` and
    leaving the field untouched reported OK. The field is written by the same
    hand that edits the facts — it proves nothing unless it is recomputed."""
    if not _installed("zig"):
        pytest.skip("no zig toolchain here")
    I.write("zig")
    with open(I.artifact_path("zig")) as fh:
        rec = json.load(fh)
    rec["facts"]["modules"].append("FakeNamespace")      # sha256 left stale
    _put(rec)
    state, detail = I.check("zig")
    assert state == "DRIFTED", (state, detail)
    assert "its own content" in detail


def test_check_catches_a_forger_who_updates_the_hash_too():
    if not _installed("zig"):
        pytest.skip("no zig toolchain here")
    I.write("zig")
    with open(I.artifact_path("zig")) as fh:
        rec = json.load(fh)
    rec["facts"]["modules"].append("FakeNamespace")
    rec["sha256"] = I._hash(rec["facts"])                # self-consistent lie
    _put(rec)
    state, detail = I.check("zig")
    assert state == "DRIFTED", (state, detail)
    assert "different facts" in detail


def test_check_calls_a_moved_toolchain_stale_not_drifted():
    """STALE and DRIFTED must not be the same word: one means regenerate, the
    other means somebody edited a generated file. Only the second should ever
    fail a pipeline."""
    if not _installed("zig"):
        pytest.skip("no zig toolchain here")
    I.write("zig")
    with open(I.artifact_path("zig")) as fh:
        rec = json.load(fh)
    rec["toolchain"] = "0.1.0"
    rec["sha256"] = I._hash(rec["facts"])
    _put(rec)
    state, detail = I.check("zig")
    assert state == "STALE", (state, detail)
    assert "0.1.0" in detail


def test_check_on_an_absent_toolchain_is_unavailable_not_a_verdict():
    _put(_synthetic("cobol"), "cobol")
    # cobol has no probe at all, which is the same shape as a missing compiler
    state, _ = I.check("cobol")
    assert state in ("UNAVAILABLE", "MISSING")


def test_a_written_artifact_round_trips():
    for lang in sorted(I.PROBES):
        if not _installed(lang):
            continue
        path = I.write(lang)
        assert os.path.exists(path)
        state, detail = I.check(lang)
        assert state == "OK", (lang, state, detail)


# --------------------------------------------------------------------------
# rendering: the budget, and what it is spent on
# --------------------------------------------------------------------------

def test_rendering_respects_the_character_budget():
    """A LINE cap is not a budget: one "-std=c++23 adds …" line is 460
    characters, so twelve of them are a pack, not a hint."""
    if not _installed("cpp"):
        pytest.skip("no g++ here")
    for budget in (2000, 900, 500, 300):
        lines = I.render("cpp", budget_chars=budget)
        assert sum(len(x) for x in lines) <= budget, (budget, lines)


def test_a_truncated_rendering_says_so_even_when_it_is_nearly_all_truncated():
    """Silent truncation is the failure this rendering must never have: a pack
    that drops 150 facts and does not mention it reads as a complete answer.
    The notice is what the tail reserve exists to protect, and it used to be
    squeezed out by the facts it was meant to qualify."""
    if not _installed("cpp"):
        pytest.skip("no g++ here")
    lines = I.render("cpp", budget_chars=400)
    assert any("omitted" in ln for ln in lines), lines
    full = I.render("cpp", budget_chars=100000)
    assert sum(len(x) for x in full) > sum(len(x) for x in lines)
    assert not any("omitted" in ln for ln in full), \
        "a rendering that fits must not claim anything was dropped"


def test_the_cpp_rendering_spends_its_budget_on_availability():
    """Priority is the whole design here. "__cpp_constexpr is 202211L at
    c++23" is true, costs 40 characters and changes no decision; "concepts
    need c++20" is the answer a model gets wrong. The additions must come
    first — before this ordering existed, value churn ate the budget."""
    if not _installed("cpp"):
        pytest.skip("no g++ here")
    lines = I.render("cpp")
    assert lines, "g++ is installed but nothing rendered"
    assert "adds" in lines[0], lines[0]
    # and the availability answer for the feature that motivated the design
    joined = " ".join(lines)
    assert "__cpp_concepts" in joined
    idx = joined.index("__cpp_concepts")
    assert "c++20" in joined[max(0, idx - 400):idx], \
        "concepts must be attributed to the level that adds them"


def test_c_renders_something_at_all():
    """C adds no feature macros between c99 and c23 — only __STDC_VERSION__
    changes VALUE — so an additions-only map rendered C as having no facts,
    which is not the same as C having none."""
    if not _installed("c"):
        pytest.skip("no gcc here")
    lines = I.render("c")
    assert lines, "gcc is installed but C rendered nothing"
    # The FACT is the mapping across levels, not that the macro is mentioned
    # once. Asserting mere presence was too weak: with value-tracking removed
    # the first-level fallback still emitted a single line, so the mutation
    # that blanks C's only signal passed.
    got = [ln for ln in lines if "__STDC_VERSION__" in ln]
    assert len(got) >= 3, got
    levels = {lv for lv in ("c99", "c11", "c17", "c23")
              if any(f"-std={lv}" in ln for ln in got)}
    assert len(levels) >= 3, (levels, got)
    # and each line must carry the VALUE, which is what selects the flag
    assert all(re.search(r"\b\d{6}L\b", ln) for ln in got), got


def test_zig_rendering_names_the_exact_spelling_trap():
    if not _installed("zig"):
        pytest.skip("no zig here")
    lines = I.render("zig")
    joined = " ".join(lines)
    assert "Io" in joined
    assert "std.io is not" in joined, \
        "the one trap this fact set exists for must be stated, not implied"


def test_render_says_nothing_for_a_language_it_cannot_see():
    assert I.render("cobol") == []


# --------------------------------------------------------------------------
# the pack path
# --------------------------------------------------------------------------

def test_generated_lines_reach_the_pack_and_are_labelled(monkeypatch):
    """The payoff: a language with GENERATED facts gets a pack even where the
    two tiers differ in how they earn belief, so the header must say which is
    which rather than calling everything "confirmed by compiling"."""
    if not _installed("cpp"):
        pytest.skip("no g++ here")
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "cpp")
    pack = P.pack_for("cpp")
    assert pack is not None
    assert "GENERATED by asking the toolchain" in pack.text
    assert "-std=" in pack.text


def test_a_generated_only_language_still_gets_a_pack(monkeypatch):
    """The whole point of L1: no hand-authored claim, still a pack. `go` has
    no claims file, so if this passes for go it passes for any language whose
    toolchain we can ask."""
    if not _installed("go"):
        pytest.skip("no go here")
    assert not [c for c in P.V.load_claims(P.CLAIMS_DIR) if c.lang == "go"], \
        "go has claims now — pick another claim-free language for this test"
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "go")
    pack = P.pack_for("go")
    assert pack is not None, "a language with facts but no claims got nothing"
    assert "GENERATED" in pack.text
    assert "CONFIRMED by compiling" not in pack.text, \
        "nothing was confirmed by compiling — the header must not claim it was"


def test_the_pack_drops_generated_before_verified_under_pressure(monkeypatch):
    """VERIFIED is the stronger tier — a fixture proved a migration — so when
    the budget bites, the generated lines are what goes."""
    if not _installed("cpp"):
        pytest.skip("no g++ here")
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "cpp")
    full = P.pack_for("cpp")
    tight = P.pack_for("cpp", budget_tokens=120)
    assert full is not None and tight is not None
    assert "-std=" in full.text
    assert "-std=" not in tight.text, \
        "the generated tier survived while verified claims were dropped"
    # At this budget only the first claim or two survive; what matters is that
    # what survived is VERIFIED, not which particular claim it was.
    assert "[c++" in tight.text, tight.text
