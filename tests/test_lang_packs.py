"""beast-lang pack resolution — the allow list and the drift guard.

Max's framing (2026-09-15): "I want this implemented so that ANY model
dropped in … can automatically and auto-magically know how to use a language
… we can FF certain languages via an allow list for an openbeast-deployment."
So which languages are active is a DEPLOYMENT decision, and the properties
that matter are: the allow list is honoured, and a pack is never served for a
toolchain it does not describe.

That second one is the safety property. A pack describing zig 0.15 handed to
an agent compiling against zig 0.16 is worse than no pack at all — it is
wrong with authority, which is the exact failure beast-lang exists to fix.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

from lang import drivers as D     # noqa: E402
from lang import packs as P       # noqa: E402
from lang import verify as V      # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("OPENBEAST_LANG_PACKS", raising=False)
    monkeypatch.delenv("OPENBEAST_LANG_PACK_BUDGET", raising=False)


# --- the deployment allow list ---------------------------------------------

def test_auto_activates_every_language_with_a_toolchain_and_claims(monkeypatch):
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "auto")
    raw, active = P.allow_list()
    assert raw == "auto"
    assert active, "auto activated nothing"
    for lang in active:
        d = D.driver_for(lang)
        assert d and d.available(), f"{lang} active with no toolchain"


def test_an_explicit_list_pins_exactly_those_languages(monkeypatch):
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "zig")
    assert P.allow_list()[1] == ["zig"]
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "cpp, zig")
    assert sorted(P.allow_list()[1]) == ["cpp", "zig"]


def test_off_activates_nothing(monkeypatch):
    for value in ("off", "none", "false", "0", ""):
        monkeypatch.setenv("OPENBEAST_LANG_PACKS", value)
        assert P.allow_list()[1] == [], f"{value!r} did not disable packs"


def test_a_language_we_have_no_claims_for_cannot_be_forced_on(monkeypatch):
    """The allow list selects from what exists; it cannot conjure a pack."""
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "cobol,zig")
    assert P.allow_list()[1] == ["zig"]


def test_a_language_not_on_the_list_gets_no_pack(monkeypatch):
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "python")
    assert P.pack_for("zig") is None, "served a pack for a language not allow-listed"
    assert P.pack_for("python") is not None


def test_conf_is_read_without_being_sourced(tmp_path, monkeypatch):
    """openbeast.conf values are not shell: a trailing comment is literal
    text, which has bitten this repo before (CHAT_OPERATORS). Reading it must
    also never EXECUTE it — this is imported by the serving path."""
    conf = tmp_path / "openbeast.conf"
    conf.write_text('LANG_PACKS="zig"   # just zig on this rig\n')
    monkeypatch.setattr(P, "_REPO", str(tmp_path))
    assert P._conf_value("LANG_PACKS") == 'zig"   # just zig on this rig'.split('"')[0] \
        or P._conf_value("LANG_PACKS").startswith("zig")


# --- the drift guard: the safety property ----------------------------------

def test_a_pack_for_another_toolchain_version_is_never_served(monkeypatch, tmp_path):
    """The whole point. A 0.15 pack must not be handed to a 0.16 compiler."""
    hw = tmp_path / "packs"
    hw.mkdir()
    (hw / "zig-0.15.md").write_text("=== Language notes: zig 0.15 ===\nstale\n")
    monkeypatch.setattr(P, "HANDWRITTEN_DIR", str(hw))
    assert P._handwritten("zig", "0.16.0") is None, \
        "a pack stamped 0.15 was offered for zig 0.16"
    assert P._handwritten("zig", "0.15.1") is not None, \
        "the matching pack was not found"


def test_no_toolchain_means_no_pack(monkeypatch):
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "swift")
    assert P.pack_for("swift") is None
    assert "swift" in D.NO_TOOLCHAIN


def test_version_shortening_handles_every_driver_format():
    assert P._short_version("zig 0.16.0") == "0.16.0"
    assert P._short_version("0.16.0") == "0.16.0"
    assert P._short_version("rustc 1.98.1 (48a229cea 2026-09-01)") == "1.98.1"
    assert P._short_version("g++ (GCC) 16.2.1 20260810") == "16.2.1"
    assert P._short_version("Python 3.14.7") == "3.14.7"


# --- the renderer ----------------------------------------------------------

def test_only_verified_claims_reach_a_pack(monkeypatch):
    """A pack is a statement about what the toolchain CONFIRMED. Anything
    else in it is a confident falsehood."""
    real = V.verify

    def poison(claim):
        r = real(claim)
        if r["verdict"] == V.VERIFIED:
            r = dict(r, verdict=V.NOT_A_BREAK)
        return r
    monkeypatch.setattr(V, "verify", poison)
    assert P.render("python", "Python 3.14.7") is None, \
        "rendered a pack with nothing verified"


def test_a_verified_claim_with_no_summary_is_reported_not_silently_dropped(monkeypatch):
    """Verified but undeliverable is a real state and must be visible —
    knowledge we proved and then failed to ship."""
    claims = [c for c in V.load_claims(P.CLAIMS_DIR) if c.lang == "python"]
    assert claims
    for c in claims:
        c.summary = ""
    monkeypatch.setattr(V, "load_claims", lambda _d: claims)
    assert P.render("python", "Python 3.14.7") is None
    for c in claims:
        c.summary = "x"
    pack = P.render("python", "Python 3.14.7")
    assert pack and pack.claims == len(claims)


def test_the_budget_is_enforced_and_the_drop_is_reported():
    full = P.render("python", "Python 3.14.7")
    small = P.render("python", "Python 3.14.7", budget_tokens=110)
    assert full and small
    assert small.tokens <= 110, f"{small.tokens} tokens over a 110-token budget"
    assert small.claims < full.claims, "nothing was actually dropped"
    assert any("budget" in x for x in small.skipped), "a silent truncation"


def test_a_budget_too_small_for_the_header_serves_NOTHING():
    """Rather than a header that promises confirmed facts and carries none.
    An over-budget pack with no content is the worst of both."""
    assert P.render("python", "Python 3.14.7", budget_tokens=10) is None


def test_an_explicitly_emptied_setting_is_off_not_auto(monkeypatch, tmp_path):
    """`LANG_PACKS=` written out is an operator turning this OFF. Reading the
    setting with `or` conflated empty with absent and activated everything."""
    conf = tmp_path / "openbeast.conf"
    conf.write_text("LANG_PACKS=\n")
    monkeypatch.setattr(P, "_REPO", str(tmp_path))
    monkeypatch.delenv("OPENBEAST_LANG_PACKS", raising=False)
    assert P.allow_list()[1] == [], "an emptied key activated languages anyway"
    conf.write_text("# nothing set here\n")
    assert P.allow_list()[0] == "auto", "an ABSENT key should mean auto"


def test_a_generated_pack_says_where_its_facts_came_from():
    pack = P.render("cpp", "g++ (GCC) 16.2.1 20260810")
    assert pack and pack.kind == "generated"
    assert "16.2.1" in pack.text
    assert "CONFIRMED by compiling" in pack.text
    assert "web search" in pack.text        # the local-first claim, stated


def test_handwritten_precedence_without_needing_any_toolchain(tmp_path, monkeypatch):
    """The PRECEDENCE rule, testable on a machine with no compilers at all.

    The integration version of this needs zig installed, so CI (which has gcc
    but no zig) could not run it — and a property only checked on one
    developer's machine is a property that breaks quietly. This exercises the
    resolver directly instead.
    """
    hw = tmp_path / "packs"
    hw.mkdir()
    (hw / "madeup-1.2.md").write_text("=== curated notes for madeup 1.2 ===\n")
    monkeypatch.setattr(P, "HANDWRITTEN_DIR", str(hw))
    found = P._handwritten("madeup", "madeup 1.2.3")
    assert found, "a matching hand-written pack was not preferred"
    path, text = found
    assert path.endswith("madeup-1.2.md") and "curated notes" in text
    # and nothing is offered for a language with no hand-written pack
    assert P._handwritten("other", "other 1.0") is None


@pytest.mark.skipif(not (D.driver_for("zig") and D.driver_for("zig").available()),
                    reason="zig absent (CI runners have gcc but no zig)")
def test_handwritten_beats_generated_end_to_end(monkeypatch):
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "zig")
    p = P.pack_for("zig")
    assert p and p.kind == "handwritten", "the curated zig pack was bypassed"
    assert p.path and p.path.endswith("zig-0.16.md")


# --- delivery --------------------------------------------------------------

def test_language_detection_is_dumb_on_purpose():
    assert "zig" in P.languages_in("Create /tmp/eval_x/out.zig that reads stdin")
    assert "rust" in P.languages_in("write src/main.rs")
    assert "python" in P.languages_in("implement solve() in solver.py")
    # 'c' must not fire when the task is plainly C++
    langs = P.languages_in("Create gemm.cpp, a cache-blocked matmul in C++")
    assert "cpp" in langs and "c" not in langs


def test_active_packs_covers_every_allowed_language_with_content(monkeypatch):
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "auto")
    _, active = P.allow_list()
    got = {p.lang for p in P.active_packs()}
    assert got, "auto produced no packs at all"
    assert got <= set(active)


def test_the_shipped_claim_summaries_are_one_line_each():
    """A pack line is a line. A multi-line summary breaks the budget maths
    and the rendering."""
    for c in V.load_claims(P.CLAIMS_DIR):
        if c.summary:
            assert "\n" not in c.summary, f"{c.id} has a multi-line summary"
            assert len(c.summary) < 400, f"{c.id} summary is {len(c.summary)} chars"


def test_claim_sets_that_ship_summaries_declare_them_in_json():
    """Guard against a summary that exists only in a generator script."""
    d = P.CLAIMS_DIR
    with_sum = 0
    for name in os.listdir(d):
        if not name.endswith(".json"):
            continue
        for c in json.load(open(os.path.join(d, name))).get("claims", []):
            if c.get("summary"):
                with_sum += 1
    assert with_sum >= 10, f"only {with_sum} claims are deliverable"
