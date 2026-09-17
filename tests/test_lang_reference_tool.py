"""language_reference — beast-lang's PULL surface (docs/BEAST_LANG_PLAN.md P5).

Three promises, each with its negative control:

  A WINDOW, NOT AN AUTHOR   every line is VERIFIED or GENERATED. A claim that
                            is NOT_A_BREAK here never appears, a miss says so
                            instead of padding, and nothing is fuzzy-matched.
  NEVER A TRACEBACK         a facade that returns "", a facade that raises, a
                            package that will not import, a language nobody
                            serves: all of them are a string.
  ON THIS SURFACE ONLY      registered on the MCP/WebUI surface, admin profile
                            only, and NOT in the runner's registry — asserted
                            by READING agents/tools.py, which is era-locked.

Every case builds its own input: the facade is stubbed (and the stubs record
their calls), or a claim set is written into tmp_path. Nothing reads the
machine's zig/gcc — the one real toolchain used is the python driver, which is
the interpreter running this file and therefore present everywhere.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import lang                          # noqa: E402
import mcp_server                    # noqa: E402
from lang import packs as P          # noqa: E402
from lang import reference as R      # noqa: E402

ERA_LOCKED = ("system-prompt.md", "system-prompt-tools.md", "opencode.json",
              "agents/runner.py", "agents/tools.py")


class Facade:
    """Stub of the four facade calls. Records every call it receives."""

    def __init__(self, monkeypatch, pack="PACK-TEXT\n", cards="CARD-TEXT\n",
                 ref="REF-TEXT\n", langs=("python", "zig"), boom=False):
        self.calls = []
        self.boom = boom
        for name, value in (("safe_pack", pack), ("safe_escalation", cards),
                            ("safe_reference", ref), ("safe_languages", list(langs))):
            monkeypatch.setattr(lang, name, self._make(name, value))

    def _make(self, name, value):
        def stub(*args):
            self.calls.append((name, *args))
            if self.boom and name != "safe_languages":
                raise RuntimeError("the facade broke its promise")
            return value
        return stub

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture(autouse=True)
def _not_under_eval(monkeypatch):
    monkeypatch.delenv("OPENBEAST_EVAL", raising=False)
    monkeypatch.delenv("OPENBEAST_LANG_IN_EVAL", raising=False)


# --- routing: which facade call answers which question -----------------------

def test_error_returns_the_escalation_cards_and_nothing_else(monkeypatch):
    f = Facade(monkeypatch)
    out = mcp_server.language_reference("zig", error="error: no member named 'io'")
    assert "CARD-TEXT" in out
    assert ("safe_escalation", "zig", "error: no member named 'io'") in f.calls
    # negative control: an error lookup must not drag the whole pack in
    assert "safe_pack" not in f.names() and "PACK-TEXT" not in out


def test_topic_returns_the_filtered_reference(monkeypatch):
    f = Facade(monkeypatch)
    out = mcp_server.language_reference("zig", topic="  ArrayList  ")
    assert "REF-TEXT" in out
    assert ("safe_reference", "zig", "ArrayList") in f.calls
    assert "safe_pack" not in f.names() and "safe_escalation" not in f.names()


def test_neither_returns_the_pack(monkeypatch):
    f = Facade(monkeypatch)
    out = mcp_server.language_reference("python")
    assert out.strip() == "PACK-TEXT"
    assert ("safe_pack", "python") in f.calls
    assert "safe_reference" not in f.names() and "safe_escalation" not in f.names()


def test_error_and_topic_answers_both(monkeypatch):
    Facade(monkeypatch)
    out = mcp_server.language_reference("zig", topic="std.io", error="error: x")
    assert out.index("CARD-TEXT") < out.index("REF-TEXT")


def test_a_spelling_alias_is_the_same_language(monkeypatch):
    f = Facade(monkeypatch, langs=("cpp",))
    assert "PACK-TEXT" in mcp_server.language_reference(" C++ ")
    assert ("safe_pack", "cpp") in f.calls


# --- never a traceback, never padding ----------------------------------------

@pytest.mark.parametrize("language", ["cobol", "", "swift", "../../etc/passwd"])
def test_an_unserved_language_lists_what_this_rig_can_serve(monkeypatch, language):
    f = Facade(monkeypatch, langs=("python", "zig"))
    out = mcp_server.language_reference(language)
    assert out.startswith("Error:") and "python, zig" in out
    assert "Traceback" not in out
    # and nothing was looked up for a language that is not served
    assert f.names() == ["safe_languages"]


def test_no_active_language_says_so_rather_than_listing_nothing(monkeypatch):
    Facade(monkeypatch, langs=())
    out = mcp_server.language_reference("zig")
    assert out.startswith("Error:") and "LANG_PACKS" in out


@pytest.mark.parametrize("kw,needle", [
    ({"error": "error: something nobody has a claim about"}, "No verified card"),
    ({"topic": "frobnicate"}, "No verified reference for 'frobnicate'"),
    ({}, "No verified reference for zig"),
])
def test_an_empty_facade_answer_is_said_plainly(monkeypatch, kw, needle):
    """The facade's "" means "nothing verified to say". The tool must say
    exactly that — not fall back to a different tier to look helpful."""
    f = Facade(monkeypatch, pack="", cards="", ref="")
    out = mcp_server.language_reference("zig", **kw)
    assert needle in out and not out.startswith("Error:")
    if kw:                        # a miss is not answered with the pack
        assert "safe_pack" not in f.names()


def test_a_facade_that_raises_is_still_a_string(monkeypatch):
    Facade(monkeypatch, boom=True)
    for kw in ({}, {"topic": "x"}, {"error": "e"}):
        out = mcp_server.language_reference("zig", **kw)
        assert isinstance(out, str) and out.startswith("Error:")
        assert "Traceback" not in out


def test_a_package_that_will_not_import_costs_one_tool_an_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "lang", None)     # `import lang` -> ImportError
    out = mcp_server.language_reference("zig")
    assert out.startswith("Error:") and "beast-lang is unavailable" in out


def test_non_string_arguments_do_not_raise(monkeypatch):
    Facade(monkeypatch)
    assert isinstance(mcp_server.language_reference(None), str)
    assert isinstance(mcp_server.language_reference("zig", topic=None, error=None), str)


def test_output_is_bounded_and_says_when_it_was_cut(monkeypatch):
    big = "".join(f"- fact number {i} about the language\n" for i in range(5000))
    Facade(monkeypatch, pack=big)
    out = mcp_server.language_reference("zig")
    assert len(out) <= mcp_server._LANG_REF_MAX_CHARS
    assert "[truncated:" in out.splitlines()[-1]
    # cut on a LINE boundary: every kept line is a whole fact
    assert all(ln.endswith("about the language") for ln in out.splitlines()[:-1])
    # negative control: a pack that fits is returned untouched, no notice
    Facade(monkeypatch, pack="- one fact\n")
    assert mcp_server.language_reference("zig") == "- one fact\n"


def test_a_huge_pasted_error_is_clipped_before_matching(monkeypatch):
    f = Facade(monkeypatch)
    mcp_server.language_reference("zig", error="e" * 1_000_000)
    sent = [c for c in f.calls if c[0] == "safe_escalation"][0][2]
    assert len(sent) == mcp_server._LANG_REF_MAX_ERROR_CHARS


def test_the_docstring_tells_a_model_when_to_call_it():
    doc = mcp_server.language_reference.__doc__
    for needle in ("compile error", "INSTALLED", "CALL IT WHEN", "never guesses"):
        assert needle in doc, needle


# --- the surface it is on, and the ones it is not ----------------------------

def test_it_is_not_in_the_runner_registry():
    """READ the era-locked registry; never edit it. The positive control
    (`bash` IS there) is what makes the negative one mean something."""
    import tools as runner_tools
    names = {s["function"]["name"] for s in runner_tools.TOOL_SCHEMAS}
    assert "bash" in names and len(names) == 10, names
    assert "language_reference" not in names


def test_no_era_locked_file_mentions_it():
    for rel in ERA_LOCKED:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            assert "language_reference" not in fh.read(), rel


def test_it_is_on_the_webui_surface_for_admin_only(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import openapi_tools
    assert "language_reference" in openapi_tools.TOOL_NAMES
    # the guest profile is web-only ON PURPOSE — harmless is not the test
    assert openapi_tools.GUEST_TOOLS == {"web_search", "fetch"}
    f = Facade(monkeypatch)
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(tmp_path))
    monkeypatch.setenv("OPENBEAST_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("OPENBEAST_MCPO_ADMIN_KEY", "k-admin")
    monkeypatch.setenv("OPENBEAST_MCPO_GUEST_KEY", "k-guest")
    c = TestClient(openapi_tools.create_app())
    body = {"language": "zig", "topic": "ArrayList"}
    guest = c.post("/language_reference", json=body,
                   headers={"Authorization": "Bearer k-guest"})
    assert guest.status_code == 404 and f.calls == []       # never reached it
    admin = c.post("/language_reference", json=body,
                   headers={"Authorization": "Bearer k-admin"})
    assert admin.status_code == 200 and "REF-TEXT" in admin.text
    # `language` is the one required argument; the rest are optional
    spec = c.get("/openapi.json").json()
    assert "/language_reference" in spec["paths"]
    assert c.post("/language_reference", json={},
                  headers={"Authorization": "Bearer k-admin"}).status_code == 422


# --- the real lookup, against a claim set this test writes -------------------

def _claims(tmp_path, monkeypatch, claims):
    d = tmp_path / "claims"
    d.mkdir()
    (d / "python-test.json").write_text(json.dumps({"lang": "python", "claims": claims}))
    monkeypatch.setattr(P, "CLAIMS_DIR", str(d))
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "python")
    return d


MAKETRANS = {
    "id": "string-maketrans-gone", "topic": "str translation",
    "old": ["import string\nt = string.maketrans('a', 'b')\n"],
    "new": ["t = str.maketrans('a', 'b')\n"],
    "summary": "`string.maketrans` is gone — use `str.maketrans(...)`"}
GETCWD = {
    "id": "os-getcwd", "topic": "working directory",
    "old": ["import os\nf = os.beastlang_no_such_attr\n"],
    "new": ["import os\nf = os.getcwd\n"],
    "summary": "the working directory is `os.getcwd()`"}
#: the OLD form still resolves, so this is NOT_A_BREAK on every interpreter
NOT_A_BREAK = {
    "id": "os-path-join-moved", "topic": "str translation",
    "old": ["import os\nf = os.path.join\n"],
    "new": ["import os\nf = os.path.join\n"],
    "summary": "`os.path.join` moved — UNTRUE, and maketrans is mentioned here"}
NO_SUMMARY = {
    "id": "undeliverable", "topic": "maketrans",
    "old": ["import string\nt = string.maketrans\n"],
    "new": ["t = str.maketrans\n"]}


def test_topic_lookup_returns_only_verified_matching_claims(tmp_path, monkeypatch):
    _claims(tmp_path, monkeypatch, [GETCWD, NOT_A_BREAK, NO_SUMMARY, MAKETRANS])
    out = R.topic_reference("python", "MakeTrans")             # case-insensitive
    assert "`string.maketrans` is gone" in out
    assert "CONFIRMED by compiling" in out
    # negative controls: the unrelated claim, the one the toolchain did NOT
    # confirm (it mentions the topic — matching is not the gate, verifying
    # is), and nothing at all from a claim with no summary
    assert "getcwd" not in out and "UNTRUE" not in out
    assert out.count("\n- ") == 1


def test_most_specific_match_comes_first(tmp_path, monkeypatch):
    broad = dict(MAKETRANS, id="broad", topic="strings",
                 summary="for text use `str` methods; os.getcwd is unrelated")
    _claims(tmp_path, monkeypatch, [broad, GETCWD])
    cards = R.verified_cards("python", "os.getcwd")
    assert [c["claim"] for c in cards] == ["os-getcwd", "broad"]
    assert cards[0]["score"] > cards[1]["score"]


def test_a_miss_is_empty_not_padded(tmp_path, monkeypatch):
    _claims(tmp_path, monkeypatch, [MAKETRANS, GETCWD])
    assert R.topic_reference("python", "how do I frobnicate a widget") == ""
    assert lang.safe_reference("python", "how do I frobnicate a widget") == ""
    out = mcp_server.language_reference("python", topic="how do I frobnicate a widget")
    assert out.startswith("No verified reference for")


def test_generated_lines_answer_for_one_name_and_only_one_name(tmp_path, monkeypatch):
    _claims(tmp_path, monkeypatch, [])
    out = R.topic_reference("python", "import beastlang_no_such_module")
    assert "GENERATED by asking" in out and "does NOT exist" in out
    # a BARE unknown name gets no absence line: `maketrans` is a function, and
    # "`import maketrans` does NOT exist" would read as a verdict on it
    assert R.generated_lines("python", "beastlang_no_such_module") == []
    assert "CONFIRMED by compiling" not in out      # a tier it does not carry
    assert "`import json` exists" in R.topic_reference("python", "json.dumps")
    # the ONE suggestion ever made is the same name in another case
    assert "the installed spelling is `import json`" in R.topic_reference("python", "JSON")
    # negative control: prose is not a name — no "`reading` is not a module"
    assert R.generated_lines("python", "reading files") == []


def test_absent_toolchain_or_allow_list_means_nothing_is_said(tmp_path, monkeypatch):
    _claims(tmp_path, monkeypatch, [MAKETRANS])
    assert R.topic_reference("python", "maketrans")             # control: it answers
    with monkeypatch.context() as m:
        m.setattr(P, "installed_version", lambda lang: None)
        assert R.topic_reference("python", "maketrans") == ""
    assert R.topic_reference("python", "maketrans")             # and it is back
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "off")
    assert R.topic_reference("python", "maketrans") == ""
    assert lang.safe_languages() == []


def test_the_pull_surface_is_silent_under_eval(tmp_path, monkeypatch):
    _claims(tmp_path, monkeypatch, [MAKETRANS])
    assert "python" in lang.safe_languages()
    assert lang.safe_reference("python", "maketrans")
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    assert lang.safe_reference("python", "maketrans") == ""
    assert lang.safe_languages() == []
    monkeypatch.setenv("OPENBEAST_LANG_IN_EVAL", "1")
    assert lang.safe_reference("python", "maketrans")


def test_the_trim_never_leaves_a_label_with_no_fact_under_it(tmp_path, monkeypatch):
    many = [dict(MAKETRANS, id=f"m{i}", summary=f"`string.maketrans` is gone — {i} " + "x" * 80)
            for i in range(6)]
    _claims(tmp_path, monkeypatch, many)
    out = R.topic_reference("python", "string.maketrans", budget_chars=500)
    assert len(out) <= 500 + 80                     # + the notice line
    assert "more line(s) not shown" in out
    body = out.splitlines()[1:]
    for i, ln in enumerate(body):
        if ln.endswith(":"):                        # a tier label
            assert body[i + 1].startswith("- "), out


# --- the skill (P5's other half: cloud models working in this repo) ----------

def _generator():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "generate_skill_index", os.path.join(ROOT, "scripts", "generate-skill-index.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_skill_loads_and_names_the_hard_rules():
    mcp_server._discover_skills(force=True)
    out = mcp_server.skill("beast-lang")
    for needle in ("No driver executes a snippet", "-pedantic-errors",
                   "Never hardcode an artifact URL", "summary", "language_reference",
                   "escalate.py --rebuild", "verify.py"):
        assert needle in out, needle
    rec = mcp_server._resolve_skill("beast-lang")
    assert rec["frontmatter"]["name"] == "beast-lang"
    assert rec["frontmatter"]["prompt_index"] is False


def test_prompt_index_false_keeps_a_skill_out_of_the_menu(tmp_path, monkeypatch):
    """The menu lives in system-prompt-tools.md, which is hashed into the eval
    cache era. A cloud-model skill must be able to exist without rolling it."""
    gen = _generator()
    for name, extra in (("shown", ""), ("hidden", "prompt_index: false\n")):
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: the {name} one\n{extra}---\n\n# {name}\n")
    monkeypatch.setattr(gen, "SKILLS", tmp_path)
    index = gen.build_index()
    assert "`shown`" in index and "hidden" not in index


def test_the_committed_menu_is_fresh_and_does_not_list_the_skill(monkeypatch):
    gen = _generator()
    monkeypatch.setattr(sys, "argv", ["generate-skill-index.py", "--check"])
    assert gen.main() == 0                      # --check: reads, never writes
    assert "`beast-lang`" not in gen.build_index()
    assert "`code-review`" in gen.build_index()  # control: the menu is not empty
