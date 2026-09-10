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
    assert on and comp.startswith("diag1-") and len(comp) == len("diag1-") + 8


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
