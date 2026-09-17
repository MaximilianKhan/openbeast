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


def test_facts_never_raises_and_caches(monkeypatch):
    """`facts` is the serving path: it must degrade to None, never explode.

    The toolchain is a STUB: this used to probe the real zig, so on a box
    without one (CI) the probe returned None — which is never cached, by
    design — and "cached once" could not be observed at all."""
    assert I.facts("cobol") is None          # unknown language
    calls = []

    class FakeDriver:
        v = "9.9.9"
        def available(self): return True
        def version(self): return self.v

    fake = FakeDriver()
    monkeypatch.setattr(I.drivers, "driver_for", lambda lang: fake)
    monkeypatch.setattr(I, "probe", lambda lang: calls.append(lang) or {"facts": {}})
    I._CACHE.clear()
    try:
        I.facts("zig"); I.facts("zig"); I.facts("zig")
        assert len(calls) == 1, "the live probe must be cached per process"
        fake.v = "10.0.0"                     # a toolchain upgrade: re-asked
        I.facts("zig")
        assert len(calls) == 2
        # control: a probe that cannot observe is NEVER remembered
        def boom(lang):
            calls.append(lang)
            raise I.ProbeError("nope")
        monkeypatch.setattr(I, "probe", boom)
        fake.v = "11.0.0"
        assert I.facts("zig") is None and I.facts("zig") is None
        assert len(calls) == 4
    finally:
        I._CACHE.clear()


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


def test_a_budget_too_small_yields_no_partial_level_list():
    """Silent truncation is the failure this rendering must never have. The
    summary is complete or it is not served: a budget too small must degrade
    to the one self-complete statement (where the full set lives), or to
    nothing — never to a partial list of levels that reads as the whole
    answer."""
    if not _installed("cpp"):
        pytest.skip("no g++ here")
    levels = I.facts("cpp")["facts"]["levels_supported"]
    for budget in (50, 120, 300, 600):
        lines = I.render("cpp", budget_chars=budget)
        assert sum(len(x) for x in lines) <= budget, budget
        named = [lv for lv in levels
                 if any(f"-std={lv}" in ln for ln in lines)]
        if named and len(named) != len(levels):
            raise AssertionError(
                f"budget {budget}: served {len(named)} of {len(levels)} "
                f"levels with no indication — {lines}")
    full = I.render("cpp")
    assert all(any(f"-std={lv}" in ln for ln in full) for lv in levels)


def test_the_cpp_rendering_spends_its_budget_on_availability():
    """Priority is the whole design here. "__cpp_constexpr is 202211L at
    c++23" is true, costs 40 characters and changes no decision; "concepts
    need c++20" is the answer a model gets wrong. The additions must come
    first — before this ordering existed, value churn ate the budget."""
    if not _installed("cpp"):
        pytest.skip("no g++ here")
    lines = I.render("cpp")
    assert lines, "g++ is installed but nothing rendered"
    assert lines[0].startswith("-std="), lines[0]
    # The budget must be spent on the availability question a model actually
    # has, so LIBRARY macros come first within a level. Asserting a specific
    # macro's presence at the DEFAULT budget was brittle: including <version>
    # tripled the fact count and pushed __cpp_concepts out, which is a
    # ranking question, not a correctness one.
    # The rendering SUMMARISES rather than slicing, because any mechanical
    # slice of 269 facts into ~30 is arbitrary (alphabetical put
    # std::adaptor_iterator_pair_constructor in and left std::format out) and
    # a non-arbitrary one would be CURATED, which the GENERATED tier may not
    # be. So the contract is: every supported level is named with its counts,
    # and the reader is told where the complete set is.
    joined = " ".join(lines)
    for lv in I.facts("cpp")["facts"]["levels_supported"]:
        assert f"-std={lv}" in joined, f"{lv} is not mentioned at all"
    assert "library" in joined and "language" in joined
    # The reader must always learn how to get the complete set...
    assert "lang-introspect.sh write" in joined, \
        "the summary must say how to produce the complete set"
    # ...but the pack must NOT cite a path that does not exist. The generated
    # directory is gitignored per-rig state, so on a fresh clone there is no
    # such file, and pointing a model at one would be a false claim in a pack
    # whose premise is that it carries only true ones.
    cited = "agents/lang/generated/cpp.json" in joined
    assert cited == os.path.exists(I.artifact_path("cpp")), (
        f"pack cites the artifact: {cited}, but it exists: "
        f"{os.path.exists(I.artifact_path('cpp'))}")
    # and when it DOES exist, the citation must appear
    I.write("cpp")
    joined2 = " ".join(I.render("cpp"))
    assert "agents/lang/generated/cpp.json" in joined2


def test_c_renders_something_at_all():
    """C adds no feature macros between c99 and c23 — only __STDC_VERSION__
    changes VALUE — so an additions-only map rendered C as having no facts,
    which is not the same as C having none."""
    if not _installed("c"):
        pytest.skip("no gcc here")
    lines = I.render("c")
    assert lines, "gcc is installed but C rendered nothing"
    # The FACT is the mapping across levels, not that the macro is mentioned
    # once. C adds no feature macros at all between c99 and c23 — only
    # __STDC_VERSION__ moves — so each level must still be named WITH its
    # value, which is what selects the -std flag.
    per_level = [ln for ln in lines if ln.startswith("-std=")]
    assert len(per_level) >= 3, per_level
    levels = {lv for lv in ("c99", "c11", "c17", "c23")
              if any(f"-std={lv}" in ln for ln in per_level)}
    assert len(levels) >= 3, (levels, per_level)
    assert all(re.search(r"\b\d{6}L\b", ln) for ln in per_level), per_level


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


# --------------------------------------------------------------------------
# what the adversarial review of 2026-09-15 caught (all three were SHIPPED)
# --------------------------------------------------------------------------

def test_the_zig_rendering_never_declares_a_real_name_nonexistent():
    """SHIPPED BUG. The rendered line claims "a name not in that list does not
    exist on this toolchain", and it was rendering `modules` — only the subset
    whose backing file is spelled the same way. So the pack ASSERTED that
    std.AutoHashMap and std.StringHashMap do not exist. They do. An
    authoritative falsehood in a model's context is worse than silence, and it
    is the exact failure this layer exists to prevent."""
    if not _installed("zig"):
        pytest.skip("no zig here")
    rec = I.facts("zig")
    top = set(rec["facts"]["top_level"])
    lines = I.render("zig")
    assert lines
    claim = [ln for ln in lines if "does not exist" in ln]
    assert claim, "the absence claim is gone — then this test is moot, re-read it"
    listed_line = [ln for ln in lines if "EXACT spelling" in ln]
    assert listed_line, lines
    listed = set(x.strip() for x in
                 listed_line[0].split(": ", 1)[1].split(","))
    missing = sorted(n for n in top if n not in listed)
    assert missing == [], (
        f"the pack claims these REAL top-level names do not exist: {missing}")


def test_the_cpp_probe_observes_library_feature_macros():
    """SHIPPED BUG. With empty stdin, `-dM -E` reports only the compiler's own
    predefined macros, so ZERO __cpp_lib_* were observed at any level — while
    the module docstring cited __cpp_lib_format as its headline example and
    _fmt_feature carried a __cpp_lib_ branch that could never fire. Library
    feature macros live in <version>."""
    if not _installed("cpp"):
        pytest.skip("no g++ here")
    facts = I.facts("cpp")["facts"]
    per = facts.get("features") or {}
    lib_counts = {lv: len([n for n in v if n.startswith("__cpp_lib_")])
                  for lv, v in per.items()}
    assert any(c > 0 for c in lib_counts.values()), (
        f"no library feature macro observed at any level: {lib_counts}")
    # and the specific claim the docstring makes must hold
    added = facts.get("added_at") or {}
    where = [lv for lv, v in added.items() if "__cpp_lib_format" in v]
    if where:
        assert where == ["c++20"], where
        c17 = per.get("c++17") or {}
        assert "__cpp_lib_format" not in c17, \
            "the docstring says format is absent at c++17"


def test_the_cpp_summary_is_complete_and_never_silently_truncated():
    """SHIPPED BUG (now designed away). The enumeration double-counted its own
    drops — the notice claimed 483 omitted when the truth was 239, because
    `dropped` was incremented in the per-name loop AND again after it. The
    rendering no longer enumerates at all, which removes the class: a summary
    of every level cannot be partially dropped. What must still hold is that
    it FITS, at every budget, and that a budget too small to hold the truth
    yields less output rather than a false claim."""
    if not _installed("cpp"):
        pytest.skip("no g++ here")
    levels = I.facts("cpp")["facts"]["levels_supported"]
    for budget in (100, 300, 600, 1200, 2000, 20000):
        lines = I.render("cpp", budget_chars=budget)
        size = sum(len(x) for x in lines)
        assert size <= budget, f"budget {budget}: rendered {size}"
        # no leftover enumeration vocabulary can reappear
        assert not any("further feature macros" in ln for ln in lines), lines
    # at the real budget every level is present
    full = " ".join(I.render("cpp"))
    for lv in levels:
        assert f"-std={lv}" in full, lv


def test_a_broken_compiler_is_not_reported_as_an_unsupported_standard():
    """SHIPPED BUG. _feature_map swallowed EVERY per-level failure as "this
    -std is unsupported", so an ICE, an OOM or a missing libstdc++ header
    would have been filed as a fact about the language and the map served
    afterwards would look confident and be wrong."""
    with pytest.raises(I.ProbeError) as e:
        # a "compiler" that fails for a reason that is not an unknown -std
        I._feature_map("/bin/false", "c++", ("c++17",))
    assert "no probed -std level was accepted" in str(e.value) \
        or "broken toolchain" in str(e.value), str(e.value)


def test_an_unknown_std_is_still_recorded_as_a_fact_not_an_error(monkeypatch):
    """The other half: a compiler that genuinely does not know a -std must
    yield a FACT (that level absent from levels_supported), not an exception —
    otherwise one old compiler makes the whole probe unavailable."""
    real = I._macros_at

    def fake(exe, lang_flag, std):
        if std == "c++23":
            raise I.ProbeError(f"{exe}: error: invalid value 'c++23' in '-std=c++23'")
        return {"__cplusplus": "201703L", "__cpp_if_constexpr": "201606L"}

    monkeypatch.setattr(I, "_macros_at", fake)
    try:
        out = I._feature_map("g++", "c++", ("c++17", "c++23"))
    finally:
        monkeypatch.setattr(I, "_macros_at", real)
    assert out["levels_supported"] == ["c++17"]
    assert out["levels_rejected"] == ["c++23"]


# --------------------------------------------------------------------------
# check()'s drift contract, WITHOUT a toolchain (adversarial review 2026-09-15)
# --------------------------------------------------------------------------
# The three tests above that exercise DRIFTED/STALE call _installed("zig") and
# skip when it is absent — so on CI, which has no zig, the entire drift
# contract was unverified and reverting the fix left CI fully green. That is
# the ambient-machine trap this suite is otherwise careful about, in the suite
# that warns about it. These run everywhere by stubbing the probe.

@pytest.fixture()
def _fake_probe(monkeypatch):
    """A deterministic 'toolchain' so check() can be exercised anywhere."""
    state = {"toolchain": "9.9.9", "facts": {"top_level": ["Io"], "modules": ["Io"]}}

    def probe(lang):
        if lang != "fakelang":
            raise I.ProbeError(f"no probe for {lang!r}")
        facts = dict(state["facts"])
        return {"lang": lang, "tier": "GENERATED",
                "toolchain": state["toolchain"], "commands": ["stub"],
                "facts": facts, "sha256": I._hash(facts)}

    monkeypatch.setitem(I.PROBES, "fakelang", lambda: dict(state["facts"],
                                                           commands=["stub"]))
    monkeypatch.setattr(I, "probe", probe)
    monkeypatch.setattr(I, "_CACHE", {})
    return state


def test_check_is_OK_on_a_freshly_written_artifact_without_a_toolchain(_fake_probe):
    assert I.write("fakelang")
    state, detail = I.check("fakelang")
    assert state == "OK", (state, detail)


def test_check_catches_a_hand_edit_without_a_toolchain(_fake_probe):
    """The bug this contract exists for: the stored sha256 FIELD is written by
    the same hand that edits the facts, so it must be recomputed."""
    I.write("fakelang")
    with open(I.artifact_path("fakelang")) as fh:
        rec = json.load(fh)
    rec["facts"]["modules"].append("Fake")          # field left stale
    with open(I.artifact_path("fakelang"), "w") as fh:
        json.dump(rec, fh)
    state, detail = I.check("fakelang")
    assert state == "DRIFTED", (state, detail)
    assert "its own content" in detail


def test_check_catches_a_self_consistent_forgery_without_a_toolchain(_fake_probe):
    I.write("fakelang")
    with open(I.artifact_path("fakelang")) as fh:
        rec = json.load(fh)
    rec["facts"]["modules"].append("Fake")
    rec["sha256"] = I._hash(rec["facts"])           # field updated too
    with open(I.artifact_path("fakelang"), "w") as fh:
        json.dump(rec, fh)
    state, detail = I.check("fakelang")
    assert state == "DRIFTED", (state, detail)
    assert "different facts" in detail


def test_check_calls_a_moved_toolchain_STALE_without_a_toolchain(_fake_probe):
    """STALE and DRIFTED must not be the same word: one means regenerate, the
    other means somebody edited a generated file."""
    I.write("fakelang")
    _fake_probe["toolchain"] = "9.9.10"             # the compiler moved
    state, detail = I.check("fakelang")
    assert state == "STALE", (state, detail)
    assert "9.9.9" in detail and "9.9.10" in detail


def test_the_prerelease_part_of_a_version_is_not_thrown_away():
    """SHIPPED BUG. `_short` matched only `\\d+(\\.\\d+){1,3}`, so
    `0.16.0-dev.412` and `0.16.0-dev.500` compared EQUAL — a zig dev-build
    move, which is exactly when std changes most, was invisible to the drift
    guard and stale facts would have been served as current."""
    assert I._short("0.16.0-dev.412") != I._short("0.16.0-dev.500")
    assert I._short("0.14.0-dev.1") != I._short("0.14.0")
    assert I._short("0.16.0") == "0.16.0"
    # and the two helpers that answer "which version is this" must still agree
    from lang import packs as P
    for v in ("0.16.0-dev.412", "g++ (GCC) 16.2.1 20260810",
              "go version go1.26.2 linux/amd64", "Python 3.14.7",
              "rustc 1.98.1 (48a229cea 2026-09-01)"):
        assert I._short(v) == P._short_version(v), v


def test_a_prerelease_move_is_reported_as_STALE(_fake_probe):
    """The consequence, end to end, with no toolchain needed."""
    _fake_probe["toolchain"] = "0.16.0-dev.412"
    I.write("fakelang")
    assert I.check("fakelang")[0] == "OK"
    _fake_probe["toolchain"] = "0.16.0-dev.500"
    state, detail = I.check("fakelang")
    assert state == "STALE", (state, detail)


# --- review 2026-09-17 ------------------------------------------------------

def test_python_render_never_cites_an_artifact_that_is_not_there():
    """#84 fixed this for cpp and c and left python behind: its line ALWAYS
    named agents/lang/generated/python.json, a gitignored path that does not
    exist on a fresh clone. GEN_DIR is a fresh tmp dir here (see _isolate), so
    the file is absent until this test writes it."""
    assert not os.path.exists(I.artifact_path("python"))
    joined = " ".join(I.render("python"))
    assert joined, "python rendered nothing"
    assert "generated/python.json" not in joined, \
        "the pack cites a file that does not exist"
    assert "lang-introspect.sh write python" in joined, \
        "and it must still say how to get the complete list"
    # negative control: once the artifact exists, it IS cited
    I.write("python")
    assert "agents/lang/generated/python.json" in " ".join(I.render("python"))


class _FakeDriver:
    exe = "fake"

    def __init__(self):
        self.v = "fake 1.0.0"

    def available(self):
        return True

    def version(self):
        return self.v


def test_facts_are_re_probed_when_the_toolchain_version_moves(monkeypatch):
    """The memo was keyed on the language alone, for the life of the process:
    a server kept serving the OLD compiler's facts after an upgrade."""
    drv, calls = _FakeDriver(), []

    def probe(lang):
        calls.append(drv.v)
        return {"lang": lang, "toolchain": drv.v, "facts": {"n": len(calls)}}
    monkeypatch.setattr(I, "probe", probe)
    monkeypatch.setattr(I.drivers, "driver_for", lambda lang: drv)
    assert I.facts("fakelang")["toolchain"] == "fake 1.0.0"
    # negative control: same version -> served from the memo, not re-probed
    assert I.facts("fakelang")["facts"] == {"n": 1}
    assert calls == ["fake 1.0.0"]
    drv.v = "fake 2.0.0"
    assert I.facts("fakelang")["toolchain"] == "fake 2.0.0", \
        "stale facts survived a toolchain upgrade"
    assert calls == ["fake 1.0.0", "fake 2.0.0"]


def test_a_transient_probe_failure_is_not_remembered(monkeypatch):
    """One ProbeError (a timeout under load) used to pin None for the life of
    the process."""
    drv, state = _FakeDriver(), {"fail": True, "calls": 0}

    def probe(lang):
        state["calls"] += 1
        if state["fail"]:
            raise I.ProbeError("timed out")
        return {"lang": lang, "toolchain": drv.v, "facts": {"ok": True}}
    monkeypatch.setattr(I, "probe", probe)
    monkeypatch.setattr(I.drivers, "driver_for", lambda lang: drv)
    assert I.facts("fakelang") is None
    state["fail"] = False
    rec = I.facts("fakelang")
    assert rec and rec["facts"] == {"ok": True}, "the failure was cached"
    assert state["calls"] == 2
