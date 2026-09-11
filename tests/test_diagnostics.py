"""Push-diagnostics (docs/LANG_AWARENESS_PLAN.md §3) tests.

The pins that matter:
  1. Default OFF — no env opt-in, no behavior change, byte-identical tool
     results (the current eval era must be unaffected).
  2. The zig checker sees the STALE-STDLIB class (the whole point — plain
     `ast-check` passes stale std code clean; the review proved a
     syntax-only fixture would not have caught that).
  3. Shell-injection probe: a hostile filename must not execute.
  4. A broken/missing/slow checker never fails the write.
  5. Cache keys: legacy shape when off; distinct era component when on.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "evals"))

import tools  # noqa: E402


@pytest.fixture
def diag_on(monkeypatch):
    monkeypatch.setenv("OPENBEAST_DIAGNOSTICS", "1")


@pytest.fixture
def diag_off(monkeypatch):
    monkeypatch.delenv("OPENBEAST_DIAGNOSTICS", raising=False)


def _write(tmp_path, name, content):
    p = tmp_path / name
    return tools.write_file(str(p), content)


# --- 1. gating -------------------------------------------------------------

def test_default_off_appends_nothing(tmp_path, diag_off):
    out = _write(tmp_path, "x.py", "print('hi')\n")
    assert out.startswith("Wrote ") and "diagnostics" not in out


def test_off_even_for_broken_code(tmp_path, diag_off):
    out = _write(tmp_path, "x.py", "def broken(:\n")
    assert "diagnostics" not in out


def test_non_source_file_appends_nothing(tmp_path, diag_on):
    out = _write(tmp_path, "notes.txt", "hello")
    assert "diagnostics" not in out


# --- 2. python checker (always available) ----------------------------------

def test_python_clean_ok(tmp_path, diag_on):
    out = _write(tmp_path, "ok.py", "x = 1\n")
    assert "diagnostics: OK (python)" in out


def test_python_syntax_error_reported(tmp_path, diag_on):
    out = _write(tmp_path, "bad.py", "def broken(:\n")
    assert "── diagnostics (python) ──" in out and "SyntaxError" in out


def test_edit_file_also_pushes(tmp_path, diag_on):
    p = tmp_path / "e.py"
    p.write_text("x = 1\n")
    out = tools.edit_file(str(p), "x = 1", "def broken(:")
    assert "── diagnostics (python) ──" in out


# --- 3. shell-injection probe ----------------------------------------------

def test_hostile_filename_does_not_execute(tmp_path, diag_on):
    marker = tmp_path / "pwned"
    hostile = tmp_path / f"a b$(touch {marker}).py"
    out = tools.write_file(str(hostile), "x = 1\n")
    assert not marker.exists(), "checker command executed injected shell"
    assert "Wrote" in out  # the write itself succeeded


# --- 4. robustness ---------------------------------------------------------

def test_missing_checker_appends_nothing(tmp_path, diag_on, monkeypatch):
    monkeypatch.setattr(tools.shutil, "which", lambda _: None)
    out = _write(tmp_path, "x.zig", "pub fn main() void {}\n")
    assert "diagnostics" not in out and out.startswith("Wrote ")


def test_checker_timeout_never_fails_write(tmp_path, diag_on, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(tools, "run_reaped", boom)
    out = _write(tmp_path, "x.py", "x = 1\n")
    assert out.startswith("Wrote ") and "unavailable (timeout)" in out


def test_checker_crash_never_fails_write(tmp_path, diag_on, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("checker exploded")
    monkeypatch.setattr(tools, "run_reaped", boom)
    out = _write(tmp_path, "x.py", "x = 1\n")
    assert out.startswith("Wrote ") and "unavailable (RuntimeError)" in out


def test_output_capped(tmp_path, diag_on):
    many = "\n".join(f"def broken{i}(:" for i in range(1)) + "\n" + \
           "\n".join("x" + "(" * 50 for _ in range(100))
    out = _write(tmp_path, "big.py", many)
    tail = out.split("── diagnostics", 1)[-1]
    assert len(tail) < 4000  # 2KB block + framing headroom


# --- 5. the load-bearing zig tests -----------------------------------------

zig = pytest.mark.skipif(shutil.which("zig") is None, reason="zig not installed")


@zig
def test_zig_stale_stdlib_is_caught(tmp_path, diag_on):
    """THE test: stale-std code (the measured 3.8 failure class) must
    produce a diagnostic. `zig ast-check` passes this file clean — if this
    test fails, the checker regressed to AstGen-only checking."""
    stale = (
        "const std = @import(\"std\");\n"
        "pub fn main() !void {\n"
        "    const out = std.io.getStdOut();\n"   # moved in zig 0.16
        "    _ = out;\n"
        "}\n"
    )
    out = _write(tmp_path, "stale.zig", stale)
    assert "── diagnostics (zig) ──" in out, f"stale std sailed through: {out}"


@zig
def test_zig_clean_ok(tmp_path, diag_on):
    out = _write(tmp_path, "ok.zig",
                 "pub fn main() void {}\n")
    assert "diagnostics: OK (zig)" in out, out


@zig
def test_zig_mainless_no_bogus_main_error(tmp_path, diag_on):
    out = _write(tmp_path, "lib.zig",
                 "pub fn add(a: i32, b: i32) i32 { return a + b; }\n")
    assert "diagnostics: OK (zig)" in out, out


# --- 6. other toolchains (skip where absent) --------------------------------

rustc = pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc not installed")
godep = pytest.mark.skipif(shutil.which("go") is None, reason="go not installed")


@pytest.fixture(scope="session", autouse=True)
def _warm_toolchains(tmp_path_factory):
    """Cold-runner guard: the first `go vet` on a fresh CI machine compiles
    vet's export data and can exceed the production 10s checker timeout —
    the tool then (correctly) degrades to 'unavailable (timeout)' and the
    behavior assertions flake. Warm the slow toolchains once, generously,
    outside the timeout-bounded production path."""
    d = tmp_path_factory.mktemp("warm")
    warmups = []
    if shutil.which("go"):
        (d / "w.go").write_text("package main\nfunc main() {}\n")
        warmups.append(["go", "vet", "./w.go"])
    if shutil.which("rustc"):
        (d / "w.rs").write_text("fn main() {}\n")
        warmups.append(["rustc", "--emit=metadata", "--out-dir", str(d), str(d / "w.rs")])
    for cmd in warmups:
        try:
            subprocess.run(cmd, cwd=d, capture_output=True, timeout=180)
        except Exception:
            pass  # warmup is best-effort; the real tests will tell the story
gcc = pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not installed")
sck = pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")


@rustc
def test_rust_clean_ok_and_no_devnull_bug(tmp_path, diag_on):
    out = _write(tmp_path, "ok.rs", "fn main() { println!(\"hi\"); }\n")
    assert "diagnostics: OK (rust)" in out, out  # -o /dev/null variant errors here


@rustc
def test_rust_type_error_reported(tmp_path, diag_on):
    out = _write(tmp_path, "bad.rs", "fn main() { let x: u8 = \"s\"; }\n")
    assert "── diagnostics (rust) ──" in out


@godep
def test_go_undefined_reported_moduleless(tmp_path, diag_on):
    out = _write(tmp_path, "bad.go",
                 "package main\nfunc main() { missing() }\n")
    assert "── diagnostics (go) ──" in out, out


@gcc
def test_c_error_reported(tmp_path, diag_on):
    out = _write(tmp_path, "bad.c", "int main(void){ return x; }\n")
    assert "── diagnostics (c) ──" in out


@sck
def test_sh_warning_reported(tmp_path, diag_on):
    out = _write(tmp_path, "bad.sh", "#!/bin/bash\necho $undefined_var_here\n")
    assert "diagnostics" in out


# --- 7. cache-key era -------------------------------------------------------

def test_cache_key_legacy_shape_when_off():
    import cache
    t = {"id": "01_x", "task": "t"}
    k = cache.cache_key(t, "slug", max_iter=10, diag=None)
    assert ".diag" not in k and ".mi10." in k


def test_cache_key_era_component_when_on():
    import cache
    t = {"id": "01_x", "task": "t"}
    k_off = cache.cache_key(t, "slug", max_iter=10)
    k_on = cache.cache_key(t, "slug", max_iter=10, diag="diag1-abcd1234")
    assert k_on != k_off and ".diag1-abcd1234." in k_on


def test_diagnostics_flag_off_is_none(monkeypatch):
    monkeypatch.delenv("OPENBEAST_DIAGNOSTICS", raising=False)
    import run_eval
    on, comp, tc = run_eval.diagnostics_flag()
    assert (on, comp, tc) == (False, None, {})


def test_diagnostics_flag_on_fingerprints_toolchains(monkeypatch):
    monkeypatch.setenv("OPENBEAST_DIAGNOSTICS", "1")
    import run_eval
    on, comp, tc = run_eval.diagnostics_flag()
    assert on and comp.startswith("diag2-") and len(comp) == len("diag2-") + 8


# --- beast-assist: alias + latency telemetry (2026-09-10) ------------------

def test_beast_assist_alias_enables(tmp_path, monkeypatch):
    # BEAST_ASSIST=1 is the user-facing name; must enable without the
    # internal OPENBEAST_DIAGNOSTICS spelling.
    monkeypatch.delenv("OPENBEAST_DIAGNOSTICS", raising=False)
    monkeypatch.setenv("BEAST_ASSIST", "1")
    out = _write(tmp_path, "alias.py", "x = 1\n")
    assert "diagnostics: OK (python)" in out


def test_beast_assist_alias_off_when_neither_set(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENBEAST_DIAGNOSTICS", raising=False)
    monkeypatch.delenv("BEAST_ASSIST", raising=False)
    out = _write(tmp_path, "noalias.py", "def broken(:\n")
    assert "diagnostics" not in out


def test_timing_log_written_when_pointed(tmp_path, diag_on, monkeypatch):
    import json as _json
    log = tmp_path / "diag-timing.jsonl"
    monkeypatch.setenv("OPENBEAST_DIAG_TIMING_LOG", str(log))
    _write(tmp_path, "timed.py", "x = 1\n")
    rows = [_json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    assert rows and rows[0]["lang"] == "python" and rows[0]["status"] == "ok"
    assert rows[0]["ms"] >= 0


def test_timing_log_absent_when_unset(tmp_path, diag_on, monkeypatch):
    monkeypatch.delenv("OPENBEAST_DIAG_TIMING_LOG", raising=False)
    _write(tmp_path, "untimed.py", "x = 1\n")  # must not raise


def test_eval_arm_pins_both_spellings(monkeypatch):
    # A rig-wide BEAST_ASSIST=1 must not leak into a diag-OFF eval arm:
    # run_eval pins both env spellings to the arm's own state.
    monkeypatch.setenv("BEAST_ASSIST", "1")
    monkeypatch.delenv("OPENBEAST_DIAGNOSTICS", raising=False)
    src = (ROOT / "evals" / "run_eval.py").read_text()
    assert 'os.environ["BEAST_ASSIST"] = "1" if diag_on else "0"' in src


# ===========================================================================
# diag2 bundle (2026-09-11) — roadmap R3. Pure-parser tests run without
# zig; the fixture-driven integration tests run only when zig is installed.
# Captured output below is VERBATIM zig 0.16.0 output for
# tests/fixtures/zig/*.zig (paths shortened).
# ===========================================================================

FIXTURES = ROOT / "tests" / "fixtures" / "zig"
STD = "/opt/zig/lib/std"

ZIG_ARITY_OUT = f"""\
stale_arraylist_append_arity.zig:11:13: error: member function expected 2 argument(s), found 1
    try list.append(42);
        ~~~~^~~~~~~
{STD}/array_list.zig:903:13: note: function declared here
        pub fn append(self: *Self, gpa: Allocator, item: T) Allocator.Error!void {{
        ~~~~^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
referenced by:
    callMain [inlined]: {STD}/start.zig:698:59
    callMainWithArgs [inlined]: {STD}/start.zig:638:20
    posixCallMainAndExit: {STD}/start.zig:590:38
    2 reference(s) hidden; use '-freference-trace=5' to see all references
"""

ZIG_MEMBER_OUT = f"""\
stale_removed_trimright.zig:6:22: error: root source file struct 'mem' has no member named 'trimRight'
    const t = std.mem.trimRight(u8, "abc  ", " ");
              ~~~~~~~^~~~~~~~~~
{STD}/mem.zig:1:1: note: struct declared here
const std = @import("std.zig");
^~~~~
referenced by:
    callMain [inlined]: {STD}/start.zig:698:59
    callMainWithArgs [inlined]: {STD}/start.zig:638:20
    posixCallMainAndExit: {STD}/start.zig:590:38
    2 reference(s) hidden; use '-freference-trace=5' to see all references
"""

ZIG_IO_OUT = f"""\
stale_io_writer.zig:8:24: error: root source file struct 'std' has no member named 'io'
    const stdout = std.io.getStdOut().writer();
                       ^~
{STD}/std.zig:1:1: note: struct declared here
pub const AutoHashMap = hash_map.AutoHashMap;
^~~
referenced by:
    callMain [inlined]: {STD}/start.zig:698:59
    2 reference(s) hidden; use '-freference-trace=5' to see all references
"""


# --- item 1: trace strip ---------------------------------------------------

def test_zig_compact_drops_reference_trace():
    c = tools._zig_compact(ZIG_ARITY_OUT)
    assert "referenced by:" not in c
    assert "start.zig" not in c
    assert "reference(s) hidden" not in c


def test_zig_compact_keeps_declared_here_note_and_signature():
    """The `note: function declared here` + signature line is the single
    most useful thing zig prints for the stale-API class — must survive."""
    c = tools._zig_compact(ZIG_ARITY_OUT)
    assert "array_list.zig:903:13: note: function declared here" in c
    assert "pub fn append(self: *Self, gpa: Allocator, item: T)" in c
    # error line + its own snippet + caret intact
    assert c.splitlines()[0].startswith("stale_arraylist_append_arity.zig:11:13: error:")
    assert "    try list.append(42);" in c


def test_zig_compact_keeps_module_note_but_drops_its_snippet():
    c = tools._zig_compact(ZIG_MEMBER_OUT)
    assert "mem.zig:1:1: note: struct declared here" in c
    assert 'const std = @import("std.zig");' not in c  # module line-1 noise
    assert "referenced by:" not in c


def test_zig_compact_measured_reduction():
    """The roadmap's 56% figure, pinned as a floor on the captured output."""
    for raw in (ZIG_ARITY_OUT, ZIG_MEMBER_OUT, ZIG_IO_OUT):
        c = tools._zig_compact(raw)
        assert len(c) <= 0.65 * len(raw), (len(c), len(raw))  # short synthetic paths; real-zig test pins 0.55


def test_zig_compact_non_zig_text_untouched():
    txt = "plain line\nanother: not a location\n"
    assert tools._zig_compact(txt) == txt.strip()


def test_zig_compact_keeps_user_notes_with_snippets():
    out = ("u.zig:9:6: error: no field or member function named 'incremnt' in 'u.Counter'\n"
           "    c.incremnt();\n"
           "    ~^~~~~~~~~\n"
           "u.zig:2:17: note: struct declared here\n"
           "const Counter = struct {\n"
           "                ^~~~~~\n"
           "referenced by:\n"
           "    callMain [inlined]: /x/std/start.zig:698:59\n")
    c = tools._zig_compact(out)
    assert "u.zig:2:17: note: struct declared here\nconst Counter = struct {" in c
    assert "referenced by" not in c


# --- item 2: did-you-mean --------------------------------------------------

MEM_NAMES = ["trim", "trimStart", "trimEnd", "eql", "indexOf", "copyForwards",
             "sliceTo", "tokenizeAny", "splitScalar", "zeroes"]


def test_did_you_mean_trimright_to_trimend():
    """difflib alone misses this one (get_close_matches ranks noise above
    trimEnd); the camelCase-stem tier is what makes it work."""
    got = tools._did_you_mean("trimRight", MEM_NAMES)
    assert "trimEnd" in got and len(got) <= 3
    assert set(got) <= {"trim", "trimStart", "trimEnd"}


def test_did_you_mean_exact_case_insensitive_first():
    got = tools._did_you_mean("io", ["Io", "os", "fs", "log"])
    assert got == ["Io"]


def test_did_you_mean_prefix_before_substring():
    got = tools._did_you_mean("init", ["empty", "initCapacity", "initBuffer", "deinit", "items"])
    assert got[:2] == ["initBuffer", "initCapacity"] and "deinit" in got


def test_did_you_mean_confident_typo_via_difflib():
    got = tools._did_you_mean("incremnt", ["increment", "reset", "n"])
    assert got == ["increment"]


def test_did_you_mean_precision_gate_suppresses_noise():
    # absInt has no stdlib home (it became @abs) — token-overlap noise like
    # maxInt/minInt or the 3-char substring `sin` must NOT be offered.
    got = tools._did_you_mean("absInt", ["sin", "cos", "maxInt", "minInt", "sqrt"])
    assert got == []


def test_did_you_mean_never_suggests_itself_and_caps_at_three():
    got = tools._did_you_mean("trim", ["trim", "trimStart", "trimEnd", "trimLeft", "trimRight"])
    assert "trim" not in got and len(got) == 3


def test_zig_decl_names_region_and_fallback(tmp_path):
    f = tmp_path / "array_list.zig"
    f.write_text(
        "const std = @import(\"std.zig\");\n"
        "pub fn Managed(comptime T: type) type {\n"
        "    return struct {\n"
        "        pub fn init(gpa: Allocator) Self {}\n"
        "        pub fn append(self: *Self, item: T) void {}\n"
        "    };\n"
        "}\n"
        "pub fn Aligned(comptime T: type, comptime a: ?u29) type {\n"
        "    return struct {\n"
        "        pub const empty: Self = .{};\n"
        "        pub fn initCapacity(gpa: Allocator, n: usize) !Self {}\n"
        "        fn private(self: *Self) void {}\n"
        "        pub fn deinit(self: *Self, gpa: Allocator) void {}\n"
        "    };\n"
        "}\n"
        "pub const Top = 1;\n")
    mt = f.stat().st_mtime
    region = tools._zig_decl_names(str(f), "Aligned", True, mt)
    assert set(region) == {"empty", "initCapacity", "deinit"}  # not Managed's, not private
    whole = tools._zig_decl_names(str(f), None, True, mt)
    assert "Top" in whole and "Managed" in whole and "private" not in whole
    missing = tools._zig_decl_names(str(f), "Nope", True, mt)
    assert missing == whole  # region not found → whole file
    anyn = tools._zig_decl_names(str(f), "Aligned", False, mt)
    assert "private" in anyn


def _fake_std(tmp_path):
    std = tmp_path / "std"
    (std / "Io").mkdir(parents=True)
    (std / "std.zig").write_text("pub const Io = @import(\"Io.zig\");\npub const os = 1;\npub const fs = 2;\n")
    (std / "mem.zig").write_text("pub fn trim() void {}\npub fn trimStart() void {}\npub fn trimEnd() void {}\npub fn eql() void {}\n")
    (std / "Io" / "Writer.zig").write_text("pub fn writeAll() void {}\npub fn print() void {}\npub fn flush() void {}\n")
    (std / "array_list.zig").write_text(
        "pub fn Aligned(comptime T: type) type {\n    return struct {\n"
        "        pub const empty = 0;\n        pub fn initCapacity() void {}\n"
        "        pub fn deinit() void {}\n    };\n}\n")
    return str(std)


def test_zig_resolve_type_shapes(tmp_path):
    std = _fake_std(tmp_path)
    user = str(tmp_path / "probe_user.zig")
    assert tools._zig_resolve_type("mem", std, user)[:2] == ("std.mem", f"{std}/mem.zig")
    assert tools._zig_resolve_type("std", std, user)[:2] == ("std", f"{std}/std.zig")
    d, f, region, pub = tools._zig_resolve_type("array_list.Aligned(i32,null)", std, user)
    assert (d, f, region, pub) == ("std.array_list.Aligned", f"{std}/array_list.zig", "Aligned", True)
    assert tools._zig_resolve_type("Io.Writer", std, user)[1] == f"{std}/Io/Writer.zig"
    d, f, region, pub = tools._zig_resolve_type("probe_user.Counter", std, user)
    assert (d, f, region, pub) == ("probe_user.Counter", user, "Counter", False)
    assert tools._zig_resolve_type("nonexistent_mod.X", std, user) is None
    assert tools._zig_resolve_type("mem", None, user) is None  # no std → nothing


def test_zig_extras_suggestions_from_installed_std_only(tmp_path):
    std = _fake_std(tmp_path)
    ex = tools._zig_extras(tools._zig_compact(ZIG_MEMBER_OUT), "x.zig", std)
    hint = [e for e in ex if e.startswith("hint:")]
    assert len(hint) == 1 and "'trimRight' is not in std.mem" in hint[0] and "trimEnd" in hint[0]
    ex = tools._zig_extras(tools._zig_compact(ZIG_IO_OUT), "x.zig", std)
    assert any("'io' is not in std — did you mean Io?" in e for e in ex)
    # std missing entirely → no suggestions, no crash
    assert not [e for e in tools._zig_extras(ZIG_MEMBER_OUT, "x.zig", str(tmp_path / "nope"))
                if e.startswith("hint:")]


def test_zig_extras_user_struct_resolves_to_checked_file(tmp_path):
    std = _fake_std(tmp_path)
    user = tmp_path / "probe_user.zig"
    user.write_text("const Counter = struct {\n    pub fn increment() void {}\n    pub fn reset() void {}\n};\n")
    out = (f"{user}:9:6: error: no field or member function named 'incremnt' in 'probe_user.Counter'\n"
           "    c.incremnt();\n    ~^~~~~~~~~\n")
    ex = tools._zig_extras(out, str(user), std)
    assert ex and "did you mean increment?" in ex[0]


def test_zig_extras_only_error_lines_and_capped(tmp_path):
    std = _fake_std(tmp_path)
    errs = "\n".join(f"a.zig:{i}:1: error: root source file struct 'mem' has no member named 'trim{w}'"
                     for i, w in enumerate(["Right", "Left", "Both", "All", "More"]))
    ex = tools._zig_extras(errs, "a.zig", std)
    assert len([e for e in ex if e.startswith("hint:")]) == tools._DIAG_MAX_SUGGEST
    note_only = "a.zig:1:1: note: root source file struct 'mem' has no member named 'trimRight'"
    assert tools._zig_extras(note_only, "a.zig", std) == []


# --- item 3: curated fix hints --------------------------------------------

def test_hint_table_is_curated_and_bounded():
    assert 1 <= len(tools._ZIG_FIX_HINTS) <= 5
    src = (ROOT / "agents" / "tools.py").read_text()
    assert "CURATED — verified 2026-" in src
    for pat, text in tools._ZIG_FIX_HINTS:
        assert len(text) < 400  # rename map, not documentation


def test_hint_arraylist_init_fires():
    out = "s.zig:8:34: error: struct 'array_list.Aligned(i32,null)' has no member named 'init'\n"
    ex = tools._zig_extras(out, "s.zig", None)
    fixes = [e for e in ex if e.startswith("fix:")]
    assert len(fixes) == 1 and ".empty" in fixes[0] and "append(allocator, item)" in fixes[0]


def test_hint_arraylist_arity_fires_on_declared_here_pair():
    ex = tools._zig_extras(tools._zig_compact(ZIG_ARITY_OUT), "s.zig", None)
    fixes = [e for e in ex if e.startswith("fix:")]
    assert len(fixes) == 1 and "allocator first" in fixes[0]


def test_hint_io_fires_and_is_capped_at_two():
    ex = tools._zig_extras(tools._zig_compact(ZIG_IO_OUT), "s.zig", None)
    fixes = [e for e in ex if e.startswith("fix:")]
    assert fixes and "std.Io.File.stdout().writer(init.io, &buf)" in fixes[0]
    both = ("s.zig:1:1: error: root source file struct 'std' has no member named 'io'\n"
            "    const in = std.io.getStdIn().reader();\n    ^~\n"
            "s.zig:2:1: error: struct 'array_list.Aligned(u8,null)' has no member named 'init'\n"
            "s.zig:3:1: error: member function expected 2 argument(s), found 1\n"
            "/x/std/array_list.zig:903:13: note: function declared here\n")
    ex = tools._zig_extras(both, "s.zig", None)
    assert len([e for e in ex if e.startswith("fix:")]) == tools._DIAG_MAX_HINTS


def test_hint_none_on_unrelated_error():
    out = "s.zig:3:5: error: expected type 'u8', found 'i32'\n    x = y;\n    ^\n"
    assert tools._zig_extras(out, "s.zig", None) == []


# --- item 4: anchored error count + footer ---------------------------------

def test_count_errors_anchored_zig():
    txt = ("a.zig:1:2: error: first\n"
           "    // this line mentions error: but is a snippet\n"
           "a.zig:1:2: note: error: inside a note\n"
           "  referenced: /x/error/start.zig:1:1\n"
           "a.zig:9:9: error: second\n")
    assert tools._diag_count_errors("zig", txt) == 2
    assert tools._diag_count_errors("c", "x.c:1:24: error: 'x' undeclared\n") == 1


def test_count_errors_other_languages_have_anchors():
    rust = "error[E0308]: mismatched types\n --> bad.rs:1:25\nerror: aborting due to 1 previous error\n"
    assert tools._diag_count_errors("rust", rust) == 1
    assert tools._diag_count_errors("python", '  File "x.py", line 1\nSyntaxError: invalid syntax\n') == 1
    assert tools._diag_count_errors("go", "./bad.go:2:15: undefined: missing\n") == 1
    assert tools._diag_count_errors("shell", "  ^-- SC1234 (error): oops\n  ^-- SC2154 (warning): x\n") == 1
    assert tools._diag_count_errors("nolang", "error: x\n") == 0


def test_footer_plain_count():
    out = tools._diag_format("zig", 1, ZIG_ARITY_OUT, "s.zig")
    assert out.rstrip().endswith("── 1 error ──")


def test_footer_shows_truncation():
    many = "\n".join(f"a.zig:{i}:1: error: e{i}\n    x\n    ^" for i in range(1, 41))
    out = tools._diag_format("zig", 1, many, "a.zig")
    assert "── 40 errors (" in out and "shown) ──" in out
    shown = int(out.rsplit("(", 1)[1].split(" ")[0])
    assert 0 < shown < 40


def test_footer_warnings_when_rc0_with_output():
    out = tools._diag_format("shell", 0, "  ^-- SC2154 (warning): x\n")
    assert out.rstrip().endswith("── warnings ──")


def test_footer_rc_nonzero_without_parsable_errors():
    out = tools._diag_format("zig", 1, "error: unable to open 'x.zig': FileNotFound\n")
    assert out.rstrip().endswith("── errors (rc=1) ──")


def test_format_ok_when_clean():
    assert tools._diag_format("zig", 0, "") == "\ndiagnostics: OK (zig)"


def test_format_block_bounded_with_extras(tmp_path):
    """Hints are budgeted first — they survive truncation, and the whole
    appended block (body + extras) stays within the existing caps."""
    std = _fake_std(tmp_path)
    tools._zig_std_dir.cache_clear()
    monkey = tools._zig_std_dir
    try:
        tools._zig_std_dir = lambda: std
        many = "\n".join(
            f"a.zig:{i}:1: error: root source file struct 'mem' has no member named 'trimRight'\n"
            "    const t = std.mem.trimRight(u8, s, \" \");\n    ^" for i in range(1, 60))
        out = tools._diag_format("zig", 1, many, "a.zig")
    finally:
        tools._zig_std_dir = monkey
    body = out.split("── diagnostics (zig) ──\n", 1)[1].rsplit("\n── ", 1)[0]
    assert len(body) <= tools._DIAG_MAX_BYTES
    assert len(body.splitlines()) <= tools._DIAG_MAX_LINES
    assert "did you mean" in body and "trimEnd" in body
    assert "── 59 errors (" in out


def test_non_zig_languages_unchanged_except_footer():
    py = '  File "x.py", line 1\n    def broken(:\n               ^\nSyntaxError: invalid syntax\n'
    out = tools._diag_format("python", 1, py)
    assert py.strip() in out and out.rstrip().endswith("── 1 error ──")
    assert "hint:" not in out and "fix:" not in out


# --- integration: real zig on the fixtures -----------------------------------

def _fixture_names(prefix):
    return sorted(p.name for p in FIXTURES.glob(f"{prefix}*.zig"))


def test_fixture_dir_has_both_halves():
    assert len(_fixture_names("stale_")) >= 6 and len(_fixture_names("fixed_")) >= 4


@zig
@pytest.mark.parametrize("name", [n for n in sorted(
    p.name for p in (Path(__file__).resolve().parent / "fixtures" / "zig").glob("fixed_*.zig"))])
def test_zig_hint_table_verified(tmp_path, diag_on, name):
    """Every hinted form in _ZIG_FIX_HINTS must COMPILE on the installed
    zig — the hint table's verification, re-run on every test pass."""
    out = _write(tmp_path, name, (FIXTURES / name).read_text())
    assert "diagnostics: OK (zig)" in out, out


@zig
@pytest.mark.parametrize("name", [n for n in sorted(
    p.name for p in (Path(__file__).resolve().parent / "fixtures" / "zig").glob("stale_*.zig"))])
def test_zig_stale_fixtures_fail_compact(tmp_path, diag_on, name):
    """Every stale form must FAIL, and the pushed block must be trace-free
    with the anchored footer."""
    out = _write(tmp_path, name, (FIXTURES / name).read_text())
    assert "── diagnostics (zig) ──" in out, out
    assert "referenced by:" not in out and "start.zig" not in out
    assert out.rstrip().endswith("── 1 error ──"), out


@zig
def test_zig_real_arity_keeps_signature_and_hints(tmp_path, diag_on):
    out = _write(tmp_path, "arity.zig", (FIXTURES / "stale_arraylist_append_arity.zig").read_text())
    assert "note: function declared here" in out
    assert "pub fn append(self: *Self, gpa: Allocator, item: T)" in out
    assert "fix: zig 0.16 ArrayList methods take the allocator first" in out


@zig
def test_zig_real_did_you_mean_from_installed_std(tmp_path, diag_on):
    out = _write(tmp_path, "tr.zig", (FIXTURES / "stale_removed_trimright.zig").read_text())
    assert "'trimRight' is not in std.mem" in out and "trimEnd" in out
    out = _write(tmp_path, "io.zig", (FIXTURES / "stale_io_writer.zig").read_text())
    assert "did you mean Io?" in out and "fix: zig 0.16 has no std.io" in out


@zig
def test_zig_real_payload_reduction(tmp_path):
    """Measured on this box's zig: compaction must remove ≥45% of the
    failing-fixture payload (roadmap figure: 56%)."""
    scratch = tmp_path / "zc"
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(scratch), ZIG_LOCAL_CACHE_DIR=str(scratch))
    raw_total = comp_total = 0
    for name in _fixture_names("stale_"):
        r = subprocess.run(["zig", "build-exe", "-fno-emit-bin", name], cwd=FIXTURES,
                           capture_output=True, text=True, env=env, timeout=60)
        raw = (r.stdout + r.stderr).strip()
        assert r.returncode != 0 and raw, name
        raw_total += len(raw)
        comp_total += len(tools._zig_compact(raw))
    assert comp_total <= 0.55 * raw_total, (raw_total, comp_total)
