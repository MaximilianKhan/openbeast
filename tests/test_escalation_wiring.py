"""beast-lang escalation wiring (docs/BEAST_LANG_PLAN.md §7 P4) — the ~5 lines
in agents/tools.py that attach a toolchain-CONFIRMED fix to the diagnostics
block, and the eval arm that measures it.

What has to hold, and is pinned here:
  * OFF IS BYTE-IDENTICAL. agents/tools.py is hashed into the eval cache era;
    with the flag off its output must be exactly what it was before.
  * EVIDENCE-TRIGGERED. A clean check never gets a card; an error does.
  * NEVER COSTS A WRITE. The lookup cannot raise into a tool result.
  * TWO LOCKS UNDER EVAL. The env flag alone does nothing inside a measured
    unit; only `run_eval --escalate` opens the facade, and it stamps its own
    cache component when it does.

Every case is BUILT: the beast-lang facade is a stub that records its calls,
so nothing here depends on which compilers this box has.
"""
import json
import os
import re
import sys
import types

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
for sub in ("agents", "evals"):
    p = os.path.abspath(os.path.join(ROOT, sub))
    if p not in sys.path:
        sys.path.insert(0, p)

import tools            # noqa: E402
import run_eval         # noqa: E402

_real_gate = run_eval._escalate_gate   # before any fixture stubs it

ZIG_ERR = "/tmp/x/main.zig:3:20: error: root source file struct 'std' has no member named 'io'"
CARD = ("=== zig: this error has a known cause (beast-lang) ===\n"
        "Confirmed against the zig toolchain installed on this machine:\n"
        "- std.io is std.Io in 0.16\n")


@pytest.fixture()
def facade(monkeypatch):
    """A stub `lang` package: records (lang, diagnostic), returns `reply`."""
    calls = []
    stub = types.ModuleType("lang")
    stub.reply = CARD

    def safe_escalation(name, diagnostic):
        calls.append((name, diagnostic))
        if isinstance(stub.reply, Exception):
            raise stub.reply
        return stub.reply

    stub.safe_escalation = safe_escalation
    monkeypatch.setitem(sys.modules, "lang", stub)
    for v in ("BEAST_ESCALATE", "OPENBEAST_ESCALATE"):
        monkeypatch.delenv(v, raising=False)
    return stub, calls


def test_off_is_byte_identical_and_the_facade_is_never_asked(facade):
    stub, calls = facade
    assert tools._escalation_for("zig", 1, ZIG_ERR) == ""
    assert calls == [], "the lookup ran with the flag off"
    # and the formatter, handed nothing, is the old formatter
    for args in (("zig", 1, ZIG_ERR, "/tmp/x/main.zig"), ("c", 1, "a.c:1:1: error: x"),
                 ("python", 0, ""), ("rust", 1, "error[E0425]: nope")):
        assert tools._diag_format(*args) == tools._diag_format(*args, escalation="")


@pytest.mark.parametrize("flag", ["BEAST_ESCALATE", "OPENBEAST_ESCALATE"])
def test_a_failed_check_gets_its_card_inside_the_block(facade, monkeypatch, flag):
    stub, calls = facade
    monkeypatch.setenv(flag, "1")
    esc = tools._escalation_for("zig", 1, ZIG_ERR)
    assert calls == [("zig", ZIG_ERR)]
    block = tools._diag_format("zig", 1, ZIG_ERR, "/tmp/x/main.zig", escalation=esc)
    assert "std.io is std.Io in 0.16" in block
    assert block.index("error:") < block.index("known cause")     # after the evidence
    assert block.rstrip().endswith("──"), "the footer still closes the block"
    assert len(block.encode()) <= tools._DIAG_MAX_BYTES + 200


def test_a_clean_check_never_gets_a_card(facade, monkeypatch):
    """Evidence-triggered: rc=0 (or no output) is no evidence."""
    stub, calls = facade
    monkeypatch.setenv("BEAST_ESCALATE", "1")
    assert tools._escalation_for("zig", 0, "") == ""
    assert tools._escalation_for("zig", 0, "warning: unused") == ""
    assert tools._escalation_for("zig", 1, "   ") == ""
    assert calls == []


def test_language_names_are_translated_and_unknown_ones_are_skipped(facade, monkeypatch):
    stub, calls = facade
    monkeypatch.setenv("BEAST_ESCALATE", "1")
    tools._escalation_for("c++", 1, "x.cpp:1:1: error: y")
    assert calls[-1][0] == "cpp", "the diagnostics layer says c++, beast-lang says cpp"
    n = len(calls)
    assert tools._escalation_for("shell", 1, "SC2086 (error): quote it") == ""
    assert len(calls) == n, "a language beast-lang has no driver for is never asked"


def test_the_lookup_can_never_cost_a_write(facade, monkeypatch):
    stub, calls = facade
    monkeypatch.setenv("BEAST_ESCALATE", "1")
    for bad in (RuntimeError("index corrupt"), KeyError("x"), MemoryError()):
        stub.reply = bad
        assert tools._escalation_for("zig", 1, ZIG_ERR) == ""
    stub.reply = None
    assert tools._escalation_for("zig", 1, ZIG_ERR) == ""
    # and a package that will not even import
    monkeypatch.setitem(sys.modules, "lang", None)
    assert tools._escalation_for("zig", 1, ZIG_ERR) == ""


def test_the_card_is_capped_on_a_line_boundary_and_budgeted_first(facade, monkeypatch):
    """Half a fix is a different fix; and the remedy is never the part that
    truncation eats (the banked NTT failure of the 2026-09-10 A/B)."""
    stub, calls = facade
    monkeypatch.setenv("BEAST_ESCALATE", "1")
    stub.reply = "=== head ===\n" + "".join(f"- fix number {i} " + "x" * 60 + "\n" for i in range(40))
    esc = tools._escalation_for("zig", 1, ZIG_ERR)
    assert 0 < len(esc) <= tools._ESC_MAX_BYTES
    assert all(ln.startswith(("=== head", "- fix number")) and ln.endswith(("===", "x"))
               for ln in esc.splitlines()), "cut mid-line"
    huge = "\n".join(f"/tmp/x/main.zig:{i}:1: error: e{i}" for i in range(400))
    block = tools._diag_format("zig", 1, huge, "/tmp/x/main.zig", escalation=esc)
    assert esc in block, "the body was truncated, the card was not"
    assert len(block.encode()) <= tools._DIAG_MAX_BYTES + 200


def test_run_diagnostics_passes_the_checkers_own_output_to_the_lookup(facade, monkeypatch, tmp_path):
    """End to end through _run_diagnostics, with a stub CHECKER: the card is
    selected from what the checker printed, not from the file."""
    stub, calls = facade
    src = tmp_path / "main.zig"
    src.write_text("const std = @import(\"std\");\n")
    monkeypatch.setenv("BEAST_ASSIST", "1")
    monkeypatch.setenv("BEAST_ESCALATE", "1")
    monkeypatch.setattr(tools, "_diag_checker", lambda p: ("zig", "true", {}, []))
    monkeypatch.setattr(tools, "run_reaped", lambda *a, **k: (1, ZIG_ERR))
    out = tools._run_diagnostics(str(src))
    assert calls and calls[-1] == ("zig", ZIG_ERR)
    assert "std.io is std.Io in 0.16" in out
    # control: same run, flag off -> the block without the card, facade silent
    monkeypatch.setenv("BEAST_ESCALATE", "0")
    n = len(calls)
    out_off = tools._run_diagnostics(str(src))
    assert "known cause" not in out_off and len(calls) == n
    assert out_off == tools._diag_format("zig", 1, ZIG_ERR, str(src))


# ---- the eval arm -----------------------------------------------------------

def test_escalate_flag_off_is_none(monkeypatch):
    for v in ("BEAST_ESCALATE", "OPENBEAST_ESCALATE"):
        monkeypatch.delenv(v, raising=False)
    assert run_eval.escalate_flag(True) == (False, None, {})
    assert run_eval.escalate_flag(False) == (False, None, {})


def _lang_tree(root):
    """A minimal copy of what the esc1 component reads: its own case, so the
    test never depends on (or edits) the real agents/lang/."""
    (root / "claims" / "staging").mkdir(parents=True)
    (root / "escalate-index.json").write_text('{"langs": {}}')
    (root / "escalate.py").write_text("MAX_IDENT_FANOUT = 3\n")
    (root / "verify.py").write_text("# loader\n")
    (root / "__init__.py").write_text("# facade\n")
    (root / "claims" / "zig-0.16.json").write_text(
        '[{"id": "stdout", "summary": "std.io is std.Io in 0.16"}]')
    (root / "claims" / "staging" / "zig-draft.json").write_text("[]")
    return root


@pytest.fixture()
def esc_tree(monkeypatch, tmp_path):
    root = _lang_tree(tmp_path / "lang")
    monkeypatch.setattr(run_eval, "ESCALATE_LANG_DIR", str(root))
    monkeypatch.setenv("BEAST_ESCALATE", "1")
    monkeypatch.delenv("OPENBEAST_LANG_MAX_CARDS", raising=False)
    # The gate outcome is stubbed here (its own tests below use the real
    # selector), so these cases never depend on which compilers this box has.
    gate = {"gate": {"zig": {"toolchain": "0.16.0", "installed": "0.16.0",
                             "served": True}}, "max_cards": 2}
    monkeypatch.setattr(run_eval, "_escalate_gate", lambda _b: gate)
    monkeypatch.setattr(run_eval, "_TEST_GATE", gate, raising=False)
    return root


def test_escalate_flag_stamps_the_treatment_and_needs_diagnostics(esc_tree):
    on, comp, meta = run_eval.escalate_flag(True)
    assert on and comp == f"esc1-{meta['treatment_sha']}" and len(meta["treatment_sha"]) == 8
    assert len(meta["index_sha"]) == 8
    assert "claims/zig-0.16.json" in meta["files"]
    # negative control: nothing changed, nothing moves (the era is stable)
    assert run_eval.escalate_flag(True)[1] == comp
    # an --escalate arm with the checker off would measure nothing, under a
    # name that says it measured something
    with pytest.raises(SystemExit) as e:
        run_eval.escalate_flag(False)
    assert "BEAST_ASSIST" in str(e.value)


@pytest.mark.parametrize("edit", [
    # the index: which errors select which cards
    lambda r, mp: (r / "escalate-index.json").write_text('{"langs": {"zig": {}}}'),
    # a claim SUMMARY: the sentence the model actually reads (review open-prs-1:
    # this edit used to leave the component at the same esc1-<sha>)
    lambda r, mp: (r / "claims" / "zig-0.16.json").write_text(
        '[{"id": "stdout", "summary": "EDITED: use std.debug.print for all output"}]'),
    # a new served claim file
    lambda r, mp: (r / "claims" / "cpp-std.json").write_text("[]"),
    # the selector (_select, the tie-break, render_escalation's header)
    lambda r, mp: (r / "escalate.py").write_text("MAX_IDENT_FANOUT = 4\n"),
    # how a claim file becomes a served summary; the eval facade
    lambda r, mp: (r / "verify.py").write_text("# loader v2\n"),
    lambda r, mp: (r / "__init__.py").write_text("# facade v2\n"),
    # how many cards are shown
    lambda r, mp: mp.setenv("OPENBEAST_LANG_MAX_CARDS", "3"),
    # review r2: the toolchain gate (drivers/packs) turned every card OFF —
    # the esc1 key used to stay put while the model stopped seeing cards
    lambda r, mp: run_eval._TEST_GATE["gate"]["zig"].update(served=False),
    # the installed toolchain moved under the same index
    lambda r, mp: run_eval._TEST_GATE["gate"]["zig"].update(installed="0.17.0"),
    # the knob parser changed what a raw value means
    lambda r, mp: run_eval._TEST_GATE.update(max_cards=1),
], ids=["index", "claim-summary", "new-claim-file", "selector", "loader",
        "facade", "max-cards", "gate-off", "toolchain-moved", "parsed-cap"])
def test_everything_that_reaches_the_model_moves_the_era(esc_tree, monkeypatch, edit):
    before = run_eval.escalate_flag(True)[1]
    edit(esc_tree, monkeypatch)
    assert run_eval.escalate_flag(True)[1] != before


def test_unserved_staging_claims_do_not_split_the_era(esc_tree):
    """verify.load_claims never recurses, so staging/ is not served; a draft
    being reviewed there must not invalidate a measured arm."""
    before = run_eval.escalate_flag(True)[1]
    (esc_tree / "claims" / "staging" / "zig-draft.json").write_text('[{"id": "x"}]')
    assert run_eval.escalate_flag(True)[1] == before


def test_an_unreadable_treatment_refuses_the_arm(esc_tree):
    (esc_tree / "escalate.py").unlink()
    with pytest.raises(SystemExit) as e:
        run_eval.escalate_flag(True)
    assert "escalate.py" in str(e.value)


class _FakeDriver:
    def __init__(self, version):
        self._v = version

    def available(self):
        return self._v is not None

    def version(self):
        return self._v


@pytest.fixture()
def real_escalate(monkeypatch):
    """The REAL lang.escalate, with only the toolchain probe stubbed."""
    monkeypatch.delitem(sys.modules, "lang", raising=False)
    monkeypatch.delitem(sys.modules, "lang.escalate", raising=False)
    import lang.escalate as E
    installed = {"zig": "0.16.0"}
    monkeypatch.setattr(E.D, "driver_for", lambda lang: _FakeDriver(installed.get(lang)))
    return E, installed


def test_the_real_gate_moves_the_era_when_cards_stop_being_served(esc_tree, monkeypatch,
                                                                  real_escalate):
    """Review r2: packs._short_version / drivers.version() decide whether ANY
    card is served, and neither file was in the component. Breaking that gate
    silenced every card under an unchanged esc1 key."""
    E, installed = real_escalate
    monkeypatch.setattr(run_eval, "_escalate_gate", _real_gate)
    (esc_tree / "escalate-index.json").write_text(
        '{"langs": {"zig": {"toolchain": "zig 0.16.0", "generic": [], "signatures": {}}}}')
    on, comp, meta = run_eval.escalate_flag(True)
    assert meta["gate"]["gate"]["zig"] == {"toolchain": "0.16.0", "installed": "0.16.0",
                                           "served": True}
    assert run_eval.escalate_flag(True)[1] == comp          # negative control
    # a _short_version that never matches: cards_for now serves nothing ...
    monkeypatch.setattr(E.P, "_short_version", lambda v: f"never-{v}")
    assert E.cards_for("zig", ZIG_ERR, index=json.loads(
        (esc_tree / "escalate-index.json").read_text())) == []
    # ... and the era says so
    off = run_eval.escalate_flag(True)
    assert off[2]["gate"]["gate"]["zig"]["served"] is False and off[1] != comp


def test_the_real_gate_follows_the_installed_toolchain(esc_tree, monkeypatch, real_escalate):
    E, installed = real_escalate
    monkeypatch.setattr(run_eval, "_escalate_gate", _real_gate)
    (esc_tree / "escalate-index.json").write_text(
        '{"langs": {"zig": {"toolchain": "zig 0.16.0", "generic": []}}}')
    served = run_eval.escalate_flag(True)
    installed["zig"] = None                                  # no compiler: nothing served
    gone = run_eval.escalate_flag(True)
    assert gone[2]["gate"]["gate"]["zig"]["served"] is False and gone[1] != served[1]


def test_an_undeterminable_gate_refuses_the_arm(esc_tree, monkeypatch):
    monkeypatch.setattr(run_eval, "_escalate_gate", _real_gate)
    (esc_tree / "escalate-index.json").write_text("{not json")
    with pytest.raises(SystemExit) as e:
        run_eval.escalate_flag(True)
    assert "serves on this machine" in str(e.value)


def test_escalate_imports_are_hashed_or_covered_by_the_gate_outcome():
    """Every `from lang import X` in the real selector is either hashed as
    source or listed as covered by the gate outcome — a new import cannot
    slip into the treatment unstamped."""
    src = open(os.path.join(run_eval.ESCALATE_LANG_DIR, "escalate.py")).read()
    imported = set(re.findall(r"^from lang import (\w+)", src, re.M))
    assert imported, "the guard found no imports: the regex no longer matches"
    hashed = {f[:-3] for f in run_eval.ESCALATE_TREATMENT_FILES if f.endswith(".py")}
    uncovered = imported - hashed - set(run_eval.ESCALATE_OUTCOME_COVERED)
    assert not uncovered, f"escalate.py imports {sorted(uncovered)} unstamped"


def test_the_real_tree_names_every_file_the_selector_loads():
    """Guard against the list drifting from the package: every claim file
    escalate.cards_for would load is part of the real component."""
    real = [rel for rel, _ in run_eval._escalate_treatment()]
    lang_dir = os.path.abspath(run_eval.ESCALATE_LANG_DIR)
    served = sorted(n for n in os.listdir(os.path.join(lang_dir, "claims"))
                    if n.endswith(".json"))
    assert served and all(f"claims/{n}" in real for n in served)
    for rel in ("escalate-index.json", "escalate.py", "verify.py", "__init__.py"):
        assert rel in real


def test_the_real_facade_is_silent_in_a_measured_unit_unless_the_arm_opens_it(monkeypatch):
    """The second lock, with the REAL package: BEAST_ESCALATE=1 leaking into an
    ordinary eval child (an ambient rig-wide setting) must change nothing."""
    # Restored at teardown: another module's `import lang` object must stay
    # the one sys.modules hands tools.py, or its monkeypatches stop landing.
    monkeypatch.delitem(sys.modules, "lang", raising=False)
    seen = []
    import lang.escalate as E                     # the real package
    monkeypatch.setattr(E, "render_escalation", lambda *a, **k: seen.append(a) or CARD)
    monkeypatch.setenv("BEAST_ESCALATE", "1")
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.delenv("OPENBEAST_LANG_IN_EVAL", raising=False)
    assert tools._escalation_for("zig", 1, ZIG_ERR) == ""
    assert seen == [], "the work was done under eval and only the result hidden"
    monkeypatch.setenv("OPENBEAST_LANG_IN_EVAL", "1")     # what --escalate sets
    assert "known cause" in tools._escalation_for("zig", 1, ZIG_ERR)


def test_benchmark_all_names_the_escalate_arm_as_leaderboard_ineligible():
    """--escalate is an experiment arm like --packs/--greedy: benchmark_all
    must name it when it drops the leaderboard, not only via BEAST_ASSIST."""
    import importlib
    ba = importlib.import_module("benchmark_all")
    assert "--escalate" in ba.experiment_arms({"BEAST_ESCALATE": "1", "BEAST_ASSIST": "1"})
    assert "--escalate" in ba.experiment_arms({"OPENBEAST_ESCALATE": "1"})
    # negative control: off (or explicitly 0, as run_eval pins it) is no arm
    assert "--escalate" not in ba.experiment_arms({"BEAST_ESCALATE": "0", "BEAST_ASSIST": "1"})
    assert ba.experiment_arms({}) == []
