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
import os
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


def test_escalate_flag_stamps_the_index_and_needs_diagnostics(monkeypatch, tmp_path):
    idx = tmp_path / "escalate-index.json"
    idx.write_text('{"langs": {}}')
    monkeypatch.setattr(run_eval, "ESCALATE_INDEX", str(idx))
    monkeypatch.setenv("BEAST_ESCALATE", "1")
    on, comp, meta = run_eval.escalate_flag(True)
    assert on and comp == f"esc1-{meta['index_sha']}" and len(meta["index_sha"]) == 8
    # the index IS the treatment: a rebuilt index is a different era
    idx.write_text('{"langs": {"zig": {}}}')
    assert run_eval.escalate_flag(True)[1] != comp
    # an --escalate arm with the checker off would measure nothing, under a
    # name that says it measured something
    with pytest.raises(SystemExit) as e:
        run_eval.escalate_flag(False)
    assert "BEAST_ASSIST" in str(e.value)


def test_the_real_facade_is_silent_in_a_measured_unit_unless_the_arm_opens_it(monkeypatch):
    """The second lock, with the REAL package: BEAST_ESCALATE=1 leaking into an
    ordinary eval child (an ambient rig-wide setting) must change nothing."""
    sys.modules.pop("lang", None)
    import lang                                   # the real one
    seen = []
    import lang.escalate as E
    monkeypatch.setattr(E, "render_escalation", lambda *a, **k: seen.append(a) or CARD)
    monkeypatch.setenv("BEAST_ESCALATE", "1")
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.delenv("OPENBEAST_LANG_IN_EVAL", raising=False)
    assert tools._escalation_for("zig", 1, ZIG_ERR) == ""
    assert seen == [], "the work was done under eval and only the result hidden"
    monkeypatch.setenv("OPENBEAST_LANG_IN_EVAL", "1")     # what --escalate sets
    assert "known cause" in tools._escalation_for("zig", 1, ZIG_ERR)
