"""beast-lang claim verifier — the properties that must not regress.

Two of these exist because building this feature produced two wrong claims,
and both would have filled the corpus with confident falsehoods:

  * gcc accepts its GNU extensions at every -std level, so an availability
    claim ("this needs C++20") compiled without -pedantic-errors CANNOT FAIL.
    Three of the first six C++ claims were reported NOT_A_BREAK for that
    reason alone.
  * the Python driver resolved dotted attribute chains but never checked a
    bare `import X`, so `import imp` — a module genuinely removed in 3.12 —
    was reported as "still compiles".

The third exists because the fix to the second one introduced a hazard:
importing a module EXECUTES its top level, and these claims are going to be
model-drafted in phase 3.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

from lang import drivers as D      # noqa: E402
from lang import verify as V       # noqa: E402

CPP = D.driver_for("cpp")
PY_D = D.driver_for("python")

pytestmark = pytest.mark.filterwarnings("ignore")


def _claim(**kw):
    raw = {"id": "t", "lang": kw.pop("lang", "python")}
    raw.update(kw)
    return V.Claim(raw, "test", ROOT)


# --- gap 1: -std must be binding -------------------------------------------

@pytest.mark.skipif(not CPP.available(), reason="g++ absent")
def test_pedantic_errors_makes_the_std_level_binding():
    """Designated initializers are C++20. gcc accepts them under bare
    -std=c++17 as an extension; with -pedantic-errors it refuses. If this
    ever passes under c++17 again, every C++ availability claim in the corpus
    silently becomes unfalsifiable."""
    src = ("struct P { int x; int y; };\n"
           "int main(){ P p{.x = 1, .y = 2}; return p.x; }\n")
    assert CPP.compile_source(src, "c++20"), "the new form must compile"
    r = CPP.compile_source(src, "c++17")
    assert not r, "c++17 accepted a C++20 feature — is -pedantic-errors still there?"
    assert "c++20" in r.detail.lower() or "designated" in r.detail.lower()


@pytest.mark.skipif(not CPP.available(), reason="g++ absent")
def test_an_availability_claim_reuses_the_new_snippet_for_the_old_variant():
    """The DRY path: with no `old` code and an `old_variant`, the SAME snippet
    is recompiled under the older standard. Duplicating it in JSON is how the
    two halves drift apart."""
    c = _claim(lang="cpp", old_variant="c++17", new_variant="c++20",
               new=["#include <compare>\nstruct P { int x; "
                    "auto operator<=>(const P&) const = default; };\n"
                    "int main(){ P a{1}, b{2}; return (a < b) ? 0 : 1; }\n"])
    assert not c.old, "the fixture should carry no explicit old form"
    r = V.verify(c)
    assert r["verdict"] == V.VERIFIED, r
    assert r["variant"] == "c++17->c++20"


# --- gap 2: a bare import must be checked ----------------------------------

def test_a_removed_module_fails_even_with_no_attribute_access():
    """`import imp` is the whole snippet — no attribute chain to resolve. The
    module is gone in 3.12+, so this must FAIL."""
    r = PY_D.compile_source("import imp\n")
    assert not r, "a bare import of a removed module was accepted"
    assert "imp" in r.detail


def test_a_present_module_with_no_attribute_access_passes():
    assert PY_D.compile_source("import importlib\n")


def test_from_import_of_a_missing_name_fails():
    r = PY_D.compile_source("from unittest import TestCase, NoSuchThing\n")
    assert not r
    assert "NoSuchThing" in r.detail


# --- the hazard the gap-2 fix introduced -----------------------------------

def test_a_non_stdlib_import_is_refused_and_never_executed(tmp_path, monkeypatch):
    """Importing a module RUNS its top level. Phase 3 has a local model
    drafting these claims, so a claim naming a module that happens to sit on
    sys.path must be REFUSED, not imported. Proven by planting a module that
    writes a file if it ever executes."""
    canary = tmp_path / "executed.flag"
    mod = tmp_path / "beastlang_canary.py"
    mod.write_text(textwrap.dedent(f"""
        open({str(canary)!r}, 'w').write('executed')
    """))
    monkeypatch.syspath_prepend(str(tmp_path))
    r = PY_D.compile_source("import beastlang_canary\n")
    assert not r, "a non-stdlib module was accepted"
    assert "not a stdlib module" in r.detail
    assert not canary.exists(), "THE MODULE WAS EXECUTED — no-execution rule broken"


def test_the_snippet_itself_is_never_executed(tmp_path):
    """The snippet is parsed, not run. A snippet whose only effect is a side
    effect must leave no trace."""
    canary = tmp_path / "side.flag"
    src = f"import pathlib\npathlib.Path({str(canary)!r}).write_text('x')\n"
    assert PY_D.compile_source(src), "valid snippet should verify"
    assert not canary.exists(), "the snippet ran"


# --- verdicts --------------------------------------------------------------

def test_old_that_still_compiles_is_not_a_break():
    c = _claim(old=["import importlib\n"], new=["import json\n"])
    assert V.verify(c)["verdict"] == V.NOT_A_BREAK


def test_new_that_fails_with_no_old_form_is_the_claim_being_wrong():
    c = _claim(new=["import alsogone_xyz\n"])
    assert V.verify(c)["verdict"] == V.NEW_FAILS


def test_both_failing_is_a_broken_fixture_not_a_verified_claim():
    """Neither half works, so the fixture is what is wrong — distinct from a
    claim that is merely mistaken about which form is current."""
    c = _claim(old=["import imp\n"], new=["import imp2_gone\n"])
    assert V.verify(c)["verdict"] == V.FIXTURE_BROKEN


def test_an_inverted_claim_is_called_out_as_backwards():
    """The worst available outcome: the form we call stale compiles and the
    one we call current does not. A pack built from this would teach the
    reverse of the truth, so it must not hide inside NEW_FAILS."""
    c = _claim(old=["import importlib\n"], new=["import imp\n"])
    r = V.verify(c)
    assert r["verdict"] == V.BACKWARDS, r
    assert "inverted" in r["detail"]


def test_a_language_with_no_toolchain_is_unverifiable_never_verified():
    """Swift on this rig. An absent toolchain must NEVER read as a pass —
    that is how an unverifiable claim would reach auto-injection."""
    assert "swift" in D.NO_TOOLCHAIN
    assert D.driver_for("swift") is None
    c = _claim(lang="swift", new=["let x = 1\n"])
    r = V.verify(c)
    assert r["verdict"] == V.UNVERIFIABLE
    assert r["verdict"] != V.VERIFIED


# --- the shipped claim sets ------------------------------------------------

def test_the_zig_claim_set_matches_the_fixture_manifest():
    """The claim set is a PROJECTION of tests/fixtures/zig016/MANIFEST.json.
    If someone edits one side only, the verifier reports VERIFIED about
    something nobody checked."""
    r = subprocess.run([sys.executable,
                        os.path.join(ROOT, "agents", "lang", "claims", "regen_zig.py"),
                        "--check"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_shipped_claim_set_is_loadable_and_declares_a_language():
    d = os.path.join(ROOT, "agents", "lang", "claims")
    sets = [f for f in os.listdir(d) if f.endswith(".json")]
    assert sets, "no claim sets shipped"
    for name in sets:
        doc = json.load(open(os.path.join(d, name)))
        assert doc.get("lang"), f"{name} declares no lang"
        assert doc.get("claims"), f"{name} has no claims"
        assert doc.get("_comment"), f"{name} has no provenance comment"
    claims = V.load_claims(d)
    assert len(claims) >= 25, f"expected the shipped sets, got {len(claims)}"


def test_doc_linkage_reports_verified_knowledge_the_pack_omits(tmp_path):
    """The other direction: a claim we proved and then failed to deliver."""
    pack = tmp_path / "pack.md"
    pack.write_text("mentions append(gpa, x) and nothing else\n")
    results = [{"claim": "a", "verdict": V.VERIFIED,
                "doc_must_contain": ["append(gpa, x)"]},
               {"claim": "b", "verdict": V.VERIFIED,
                "doc_must_contain": ["takeDelimiter"]},
               {"claim": "c", "verdict": V.NOT_A_BREAK,
                "doc_must_contain": ["never checked"]}]
    missing = V.check_doc_linkage(results, str(pack))
    assert len(missing) == 1 and "takeDelimiter" in missing[0]
