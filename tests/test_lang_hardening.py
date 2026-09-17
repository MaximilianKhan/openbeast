"""beast-lang hardening — what the 2026-09-17 review found by RUNNING it.

Four properties, none of which the feature tests could see because every one
of them is about what happens when something goes wrong:

  PROCESS HYGIENE   a compile that times out leaves NOTHING behind. Plain
                    subprocess.run(timeout=) kills the driver and orphans the
                    worker it forked (gcc -> cc1plus); this repo lost a whole
                    machine to that class once (tests/test_proc_hygiene.py).
  THE FACADE        safe_pack / safe_escalation never raise and say nothing
                    under OPENBEAST_EVAL; one corrupt claim set costs its own
                    language, not all of them.
  NO AMBIENT STATE  the Go driver passes env= to the child and never writes
                    os.environ; the Python driver imports nothing in-process.
  NO HOST FILES     a snippet that names a file outside its temp dir is
                    refused before the toolchain can quote it back.

Doctrine (tests/…-build-their-own-case): every test builds its case — a stub
"compiler" that records what it was given, a secret file written here — and
asserts the negative control next to the property.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agents"))

import lang                           # noqa: E402
from lang import _proc                # noqa: E402
from lang import drivers as D         # noqa: E402
from lang import escalate as E        # noqa: E402
from lang import introspect as I      # noqa: E402
from lang import packs as P           # noqa: E402
from lang import verify as V          # noqa: E402

CLAIMS = os.path.join(ROOT, "agents", "lang", "claims")
SECRET = "BEASTLANG_SECRET=hunter2-do-not-leak"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie still answers kill(0); it is dead for our purposes
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def _script(path, body: str) -> str:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _hanging_compiler(tmp_path, name="stubcc"):
    """A 'compiler' shaped like the real ones: the driver forks a WORKER and
    waits on it. The worker records its pid, so the test can ask afterwards
    whether it outlived the timeout."""
    pidfile = tmp_path / f"{name}.worker.pid"
    exe = _script(tmp_path / name,
                  "#!/bin/sh\n"
                  f"sleep 300 &\necho $! > '{pidfile}'\n"
                  "echo 'stub: compiling'\nwait\n")
    return exe, pidfile


def _worker_pid(pidfile) -> int:
    for _ in range(50):
        if pidfile.exists() and pidfile.read_text().strip():
            return int(pidfile.read_text().strip())
        time.sleep(0.05)
    raise AssertionError("the stub never started its worker — the case was not built")


def _gone(pid: int) -> bool:
    for _ in range(40):
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


# --------------------------------------------------------------------------
# 2. process hygiene
# --------------------------------------------------------------------------

def test_a_timeout_kills_the_workers_the_compiler_forked(tmp_path):
    exe, pidfile = _hanging_compiler(tmp_path)
    t0 = time.time()
    with pytest.raises(subprocess.TimeoutExpired) as ei:
        _proc.run([exe], timeout=1.0)
    assert time.time() - t0 < 10, "the call hung on the orphan's pipe"
    assert "stub: compiling" in (ei.value.output or ""), \
        "partial output was lost — it is the only clue to WHAT hung"
    pid = _worker_pid(pidfile)
    assert _gone(pid), f"worker {pid} outlived the timeout (orphaned)"


def test_the_old_call_really_did_orphan_the_worker(tmp_path):
    """The control that says the stub is a fair model: under plain
    subprocess.run(timeout=) — what drivers._run used to be — the SAME stub's
    worker survives. Without this, the test above could pass because the stub
    never produced an orphan in the first place."""
    exe, pidfile = _hanging_compiler(tmp_path, "oldcc")
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=1.0)
    pid = _worker_pid(pidfile)
    try:
        assert _alive(pid), "the stub does not orphan anything — it proves nothing"
    finally:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


def test_a_fast_compiler_still_gets_its_output_and_exit_code_back(tmp_path):
    ok = _script(tmp_path / "okcc", "#!/bin/sh\necho out; echo err >&2; exit 0\n")
    bad = _script(tmp_path / "badcc", "#!/bin/sh\necho 'x.c:1: error: nope' >&2; exit 3\n")
    rc, out = _proc.run([ok], timeout=10)
    assert rc == 0 and "out" in out and "err" in out
    rc, out = _proc.run([bad], timeout=10)
    assert rc == 3 and "error: nope" in out
    with pytest.raises(FileNotFoundError):
        _proc.run([str(tmp_path / "no-such-compiler")], timeout=10)


def test_stdin_reaches_the_child(tmp_path):
    rc, out = _proc.run(["cat"], timeout=10, stdin="#include <version>\n")
    assert rc == 0 and out == "#include <version>\n"


def test_the_c_driver_end_to_end_leaves_no_orphan(tmp_path, monkeypatch):
    """Through the real driver, with a stub `gcc` first on PATH — the path the
    reviewer reproduced (compile_source said "timed out", pgrep still showed
    cc1plus)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _, pidfile = _hanging_compiler(bindir, "gcc")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(D, "TIMEOUT_S", 1.0)
    r = D.driver_for("c").compile_source("int main(void){return 0;}\n")
    assert not r and "timed out" in r.detail and r.transient
    assert _gone(_worker_pid(pidfile)), "the driver's timeout orphaned the worker"


def test_the_go_mod_init_call_is_guarded_too(tmp_path, monkeypatch):
    """It was a bare subprocess.run inside the go driver: a hung `go` raised
    TimeoutExpired OUT of compile_source, and an absent one FileNotFoundError."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _, pidfile = _hanging_compiler(bindir, "go")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(D, "TIMEOUT_S", 0.5)
    r = D.driver_for("go").compile_source("package main\nfunc main() {}\n")
    assert not r and "timed out" in r.detail
    assert _gone(_worker_pid(pidfile))


def test_an_introspection_probe_is_guarded_too(tmp_path, monkeypatch):
    exe, pidfile = _hanging_compiler(tmp_path, "probecc")
    monkeypatch.setattr(I, "TIMEOUT_S", 1.0)
    with pytest.raises(I.ProbeError, match="timed out"):
        I._run([exe])
    assert _gone(_worker_pid(pidfile))
    # negative control: a probe that answers is passed through untouched
    assert I._run(["cat"], stdin="x\n") == (0, "x\n")


def test_output_is_bounded_in_the_parent(tmp_path):
    spew = _script(tmp_path / "spew",
                   f"#!{sys.executable}\nimport sys\n"
                   "sys.stdout.write('HEAD\\n' + 'x' * (3 * 1024 * 1024))\n")
    rc, out = _proc.run([spew], timeout=30)
    assert rc == 0, "the child must be DRAINED, not blocked on a full pipe"
    assert out.startswith("HEAD")
    assert len(out) < _proc.MAX_CAPTURE_BYTES + 200
    assert "truncated" in out and str(3 * 1024 * 1024 + 5) in out


def test_the_address_space_cap_stops_a_memory_bomb(tmp_path):
    bomb = [sys.executable, "-c", "b = bytearray(1 << 30); print('allocated', len(b))"]
    rc, out = _proc.run(bomb, timeout=60, as_limit=512 * 1024 * 1024)
    assert rc != 0 and "MemoryError" in out, (rc, out[-200:])
    # negative control: the same program is fine when the cap allows it
    rc, out = _proc.run(bomb, timeout=60, as_limit=0)
    assert rc == 0 and "allocated" in out


@pytest.mark.parametrize("lang,src", [
    ("zig", 'std.debug.print("x", .{});'),
    ("go", 'package main\nimport "fmt"\nfunc main() { fmt.Println(1) }\n'),
    ("rust", 'println!("x");'),
    ("cpp", "std::vector<int> v{1}; (void)v;"),
])
def test_the_default_cap_does_not_break_a_real_toolchain(lang, src):
    """The cap's failure mode is quiet: too tight and a VALID snippet "does
    not compile". Go is the folklore case (it reserves large virtual ranges)
    and zig the measured one (OutOfMemory at 2 GiB, fine at 4)."""
    drv = D.driver_for(lang)
    if not drv.available():
        pytest.skip(f"{drv.exe} absent")
    assert _proc.AS_LIMIT_BYTES, "no cap is configured — this measures nothing"
    r = drv.compile_source(drv.wrap(src))
    assert r, f"{lang} cannot compile hello-world under the default cap: {r.detail[:300]}"


# --------------------------------------------------------------------------
# 3. the facade, and degrading per language
# --------------------------------------------------------------------------

@pytest.fixture()
def damaged_claims(tmp_path, monkeypatch):
    """A claims dir with one CORRUPT set, one set whose fixture files are
    missing (what the zig set looks like on an install without tests/), and
    one healthy set — python, because its driver needs no toolchain."""
    d = tmp_path / "claims"
    d.mkdir()
    (d / "a-corrupt.json").write_text('{"lang": "cpp", "claims": [')
    (d / "b-no-fixtures.json").write_text(json.dumps({
        "lang": "zig", "fixture_dir": "../no/such/fixtures",
        "claims": [{"id": "gone", "old": ["old/x.zig"], "new": ["new/x.zig"],
                    "summary": "unreachable"}]}))
    shutil.copy(os.path.join(CLAIMS, "python-3.14.json"), d / "python-3.14.json")
    monkeypatch.setattr(P, "CLAIMS_DIR", str(d))
    monkeypatch.setattr(E, "_HERE", str(tmp_path))
    monkeypatch.delenv("OPENBEAST_EVAL", raising=False)
    return d


def test_a_damaged_claim_set_costs_only_itself(damaged_claims, monkeypatch):
    problems: list = []
    claims = V.load_claims(str(damaged_claims), problems=problems)
    assert {c.lang for c in claims} == {"python"}, "the healthy set was lost too"
    assert len(claims) >= 3
    assert any("a-corrupt.json" in p for p in problems)
    assert any("'gone'" in p for p in problems), problems
    # allow_list() used to raise here — for EVERY language
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "auto")
    _, langs = P.allow_list()
    assert "python" in langs
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "python,zig")
    assert "python" in P.allow_list()[1]


def test_the_facade_still_serves_the_healthy_language(damaged_claims, monkeypatch):
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "python")
    text = lang.safe_pack("python")
    summaries = [c.summary for c in V.load_claims(str(damaged_claims)) if c.summary]
    assert summaries and any(s in text for s in summaries), \
        "python's verified claims did not reach its pack"


def test_the_gate_does_NOT_tolerate_what_serving_tolerates(damaged_claims, capsys):
    """Tolerance is for the serving path. `verify` is the gate every line has
    to pass, and a claim that could not be loaded has not passed it."""
    assert V.main(["--claims", str(damaged_claims), "--lang", "python"]) == 1
    assert "CLAIM SET PROBLEM" in capsys.readouterr().err
    # negative control: the same healthy set alone passes
    solo = damaged_claims.parent / "solo"
    solo.mkdir()
    shutil.copy(damaged_claims / "python-3.14.json", solo / "python-3.14.json")
    assert V.main(["--claims", str(solo)]) == 0


def test_the_facade_never_raises(monkeypatch):
    monkeypatch.delenv("OPENBEAST_EVAL", raising=False)
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "python")
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise subprocess.TimeoutExpired("go", 90)
    # negative control FIRST: with nothing broken the facade does answer, so
    # the "" below is the exception being absorbed and not a dead feature
    assert lang.safe_pack("python").startswith("===")
    monkeypatch.setattr(P, "pack_for", boom)
    monkeypatch.setattr(E, "render_escalation", boom)
    assert lang.safe_pack("python") == ""
    assert lang.safe_escalation("zig", "error: x") == ""
    assert len(calls) == 2, "the stubs were never reached — nothing was tested"
    for exc in (OSError("gone"), ValueError("bad json"), FileNotFoundError("go")):
        monkeypatch.setattr(P, "pack_for", lambda *a, _e=exc, **k: (_ for _ in ()).throw(_e))
        assert lang.safe_pack("python") == ""


def test_the_facade_is_silent_under_eval(monkeypatch):
    """An eval unit is a measurement. Text that shows up in its context
    because a toolchain happened to be installed moves the number without
    moving the cache key."""
    calls = []
    monkeypatch.setattr(P, "pack_for",
                        lambda lg, *a, **k: calls.append(lg) or P.Pack(lg, "1", "generated", "PACK"))
    monkeypatch.setattr(E, "render_escalation",
                        lambda lg, d, **k: calls.append(lg) or "CARD")
    monkeypatch.setenv("OPENBEAST_EVAL", "1")
    monkeypatch.delenv("OPENBEAST_LANG_IN_EVAL", raising=False)
    assert lang.safe_pack("zig") == "" and lang.safe_escalation("zig", "e") == ""
    assert calls == [], "the work was DONE under eval and only the result hidden"
    # the explicit opt-in, for a run that is measuring beast-lang itself
    monkeypatch.setenv("OPENBEAST_LANG_IN_EVAL", "1")
    assert lang.safe_pack("zig") == "PACK" and lang.safe_escalation("zig", "e") == "CARD"
    # and outside eval it simply works
    monkeypatch.delenv("OPENBEAST_EVAL")
    monkeypatch.delenv("OPENBEAST_LANG_IN_EVAL")
    assert lang.safe_pack("zig") == "PACK"


def test_importing_the_package_costs_nothing():
    code = ("import sys, lang\n"
            "loaded = sorted(m for m in sys.modules if m.startswith('lang.'))\n"
            "assert not loaded, loaded\n"
            "assert 'subprocess' not in sys.modules\n"
            "assert callable(lang.safe_pack) and callable(lang.safe_escalation)\n")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=os.path.join(ROOT, "agents"))
    assert p.returncode == 0, p.stderr[-400:]


def test_one_language_failing_does_not_empty_active_packs(monkeypatch):
    monkeypatch.setenv("OPENBEAST_LANG_PACKS", "python,cpp")
    real = P.pack_for

    def flaky(lg, *a, **k):
        if lg == "cpp":
            raise OSError("cpp's fixtures went missing")
        return real(lg, *a, **k)
    monkeypatch.setattr(P, "pack_for", flaky)
    assert [p.lang for p in P.active_packs()] == ["python"]


# --------------------------------------------------------------------------
# 4. the go driver: env= to the child, os.environ untouched
# --------------------------------------------------------------------------

def test_go_is_run_offline_and_os_environ_is_never_written(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    seen = tmp_path / "go.calls"
    _script(bindir / "go",
            "#!/bin/sh\n"
            f"echo \"$1|$GOFLAGS|$GOPROXY|$GOTOOLCHAIN|$CGO_ENABLED\" >> '{seen}'\n")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GOFLAGS", "-operator-sentinel")
    during = []
    real = _proc.run

    def spy(argv, *a, **k):
        during.append(os.environ.get("GOFLAGS"))
        return real(argv, *a, **k)
    monkeypatch.setattr(_proc, "run", spy)

    D.driver_for("go").compile_source("package main\nfunc main() {}\n")

    calls = [ln.split("|") for ln in seen.read_text().splitlines()]
    assert [c[0] for c in calls] == ["mod", "build"], calls
    for sub, goflags, goproxy, gotoolchain, cgo in calls:
        assert goflags == "-mod=readonly", f"go {sub} ran with GOFLAGS={goflags!r}"
        assert goproxy == "off", f"go {sub} could reach a module proxy"
        assert gotoolchain == "local", f"go {sub} could download a toolchain"
    assert calls[1][4] == "0", "cgo was left on for the build"
    # the parent: untouched DURING the call (another thread's subprocess
    # inherits whatever is there at that instant) and after it
    assert during and set(during) == {"-operator-sentinel"}, during
    assert os.environ["GOFLAGS"] == "-operator-sentinel"


@pytest.mark.skipif(not shutil.which("go"), reason="go absent")
def test_a_real_go_snippet_still_compiles_offline():
    go = D.driver_for("go")
    assert go.compile_source('package main\nimport "fmt"\nfunc main() { fmt.Println(1) }\n')
    r = go.compile_source('package main\nimport "example.invalid/nope"\n'
                          'func main() { nope.X() }\n')
    assert not r, "an unresolvable module import compiled?"
    # go SAYS when it declined to look: that sentence is the evidence, and a
    # proxy attempt reads "dial tcp" / "proxy.golang.org" instead.
    assert "disabled by -mod=readonly" in r.detail, r.detail[:300]
    assert "dial" not in r.detail and "proxy.golang.org" not in r.detail


# --------------------------------------------------------------------------
# 8. the python driver imports nothing in-process
# --------------------------------------------------------------------------

def test_a_side_effect_module_is_refused_and_nothing_is_printed(capfd):
    py = D.driver_for("python")
    for mod in ("this", "antigravity", "__hello__", "idlelib.idle", "turtledemo"):
        sys.modules.pop(mod.split(".")[0], None)
        r = py.compile_source(f"import {mod}\n")
        assert not r and "refused, not imported" in r.detail, (mod, r.detail)
        assert mod.split(".")[0] not in sys.modules, f"{mod} was imported in-process"
    out, err = capfd.readouterr()
    assert out == "" and err == "", "an import wrote to the server's stdio"


def test_resolution_happens_in_a_child_whose_stdout_is_not_ours(capfd, monkeypatch):
    """The denylist is the belt; THIS is the fix. With the denylist emptied,
    `import this` is resolved — in a separate interpreter. The Zen of Python
    is printed there, into /dev/null, and the parent never imports a thing."""
    py = D.driver_for("python")
    monkeypatch.setattr(D.PythonDriver, "SIDE_EFFECT_MODULES", frozenset())
    sys.modules.pop("this", None)
    r = py.compile_source("import this\nthis.s\n")
    assert r, f"a module that exists did not resolve: {r.detail}"
    assert "this" not in sys.modules, "the module was imported in the PARENT"
    out, _ = capfd.readouterr()
    assert "Zen" not in out and out == "", "an import printed on our stdout"
    # negative control: the child still tells the truth about what is missing
    r = py.compile_source("import this\nthis.no_such_attribute\n")
    assert not r and "this.no_such_attribute does not exist" in r.detail


def test_the_python_verdicts_did_not_change():
    py = D.driver_for("python")
    assert py.compile_source("import json\njson.dumps({})\nfrom os import path\n")
    assert py.compile_source("from xml import etree\n"), "a submodule is importable"
    assert "string.maketrans does not exist" in \
        py.compile_source("import string\nstring.maketrans('a', 'b')\n").detail
    assert "os.nope does not exist" in py.compile_source("from os import nope\n").detail
    assert "not a stdlib module" in py.compile_source("import imp\n").detail


# --------------------------------------------------------------------------
# 9. a snippet may not read host files
# --------------------------------------------------------------------------

@pytest.fixture()
def secret(tmp_path):
    p = tmp_path / "dot.env"
    p.write_text(SECRET + "\n")
    return str(p)


def _c_leaks(path):
    return [
        f'#include "{path}"\nint main(void){{return 0;}}\n',
        f"#include <{path}>\nint main(void){{return 0;}}\n",
        f'#  include   "{path}"\nint main(void){{return 0;}}\n',
        f'# /* hidden */ include "{path}"\nint main(void){{return 0;}}\n',
        f'??=include "{path}"\nint main(void){{return 0;}}\n',
        f'%:include "{path}"\nint main(void){{return 0;}}\n',
        f'#inc\\\nlude "{path}"\nint main(void){{return 0;}}\n',
        f'#define F "{path}"\n#include F\nint main(void){{return 0;}}\n',
        f'const char *s = "/*";\n# /**/ include "{path}"\nconst char *t = "*/";\n'
        "int main(void){return 0;}\n",
        '#include "../../../../etc/hostname"\nint main(void){return 0;}\n',
        f'int main(void){{ static const unsigned char b[] = {{\n#embed "{path}"\n}}; '
        "return b[0]; }\n",
    ]


@pytest.mark.parametrize("lang", ["c", "cpp"])
def test_c_family_snippets_that_read_a_host_file_are_refused(lang, secret, monkeypatch):
    ran = []
    real = D._run
    monkeypatch.setattr(D, "_run", lambda argv, *a, **k: ran.append(argv) or real(argv, *a, **k))
    drv = D.driver_for(lang)
    for src in _c_leaks(secret):
        r = drv.compile_source(src)
        assert not r and r.detail.startswith("refused, not compiled"), (src, r.detail)
        assert "hunter2" not in r.detail
    assert ran == [], f"the toolchain was started on a refused snippet: {ran}"


@pytest.mark.skipif(not shutil.which("gcc"), reason="gcc absent")
def test_the_leak_was_real_and_ordinary_includes_are_untouched(secret, tmp_path, monkeypatch):
    c = D.driver_for("c")
    # what the refusal prevents, demonstrated with the refusal switched off
    monkeypatch.setattr(D.CDriver, "refusal", lambda self, s: None)
    leaked = c.compile_source(f'#include "{secret}"\nint main(void){{return 0;}}\n')
    assert "hunter2" in leaked.detail, "gcc no longer echoes the file — revisit this test"
    monkeypatch.undo()
    # negative controls: system and local includes are not the refusal's business
    ok = c.compile_source('#include <stdio.h>\n#include <sys/types.h>\n'
                          'int main(void){ puts("x"); return 0; }\n')
    assert ok, ok.detail
    local = c.compile_source('#include "local.h"\nint main(void){return 0;}\n')
    assert "refused" not in local.detail and "local.h" in local.detail
    assert c.refusal('#include "sub/dir/local.h"\n') is None
    assert c.refusal("int include = 1, embed = 2; /* #include <stdio.h> */\n") is None


def test_rust_snippets_that_read_the_host_are_refused(secret):
    rs = D.driver_for("rust")
    for src in (f'const S: &str = include_str!("{secret}");',
                f'const B: &[u8] = include_bytes!("{secret}");',
                f'include!("{secret}");',
                'const S: &str = include_str!("../../../etc/hostname");',
                f'const S: &str = include_str!(concat!("{secret[:5]}", "{secret[5:]}"));',
                f'const S: &str = include_str /* c */ ! ( "{secret}" );',
                'compile_error!(env!("HOME"));',
                'const K: Option<&str> = option_env!("OPENAI_API_KEY");',
                f'#[path = "{secret}"]\nmod leaked;'):
        r = rs.compile_source(rs.wrap(src))
        assert not r and r.detail.startswith("refused, not compiled"), (src, r.detail)
    # negative controls
    for src in ('const S: &str = include_str!("notes.txt");',
                'let path = "/etc/passwd"; let _ = path;',
                'let env = 1; if env != 2 { }',
                'let v = std::env::var("HOME"); let _ = v;'):
        assert rs.refusal(rs.wrap(src)) is None, src


def test_zig_snippets_that_read_a_host_file_are_refused(secret):
    z = D.driver_for("zig")
    for src in (f'const s = @embedFile("{secret}");\n_ = s;',
                'const s = @embedFile("../../../etc/hostname");\n_ = s;',
                f'const p = "{secret}";\nconst s = @embedFile(p);\n_ = s;',
                f'const s = @embedFile // why\n ("{secret}");\n_ = s;',
                f'const m = @import("{secret}");\n_ = m;',
                f'const c = @cImport(@cInclude("{secret}"));\n_ = c;'):
        r = z.compile_source(z.wrap(src))
        assert not r and r.detail.startswith("refused, not compiled"), (src, r.detail)
    for src in ('const s = @embedFile("data.txt");\n_ = s;',
                'const b = @import("builtin");\n_ = b;'):
        assert z.refusal(z.wrap(src)) is None, src


@pytest.mark.skipif(not (shutil.which("go") and shutil.which("gcc")),
                    reason="needs go and gcc (cgo)")
def test_cgo_cannot_be_used_to_include_a_host_file(secret):
    r = D.driver_for("go").compile_source(
        f'package main\n\n// #include "{secret}"\nimport "C"\n\nfunc main() {{}}\n')
    assert "hunter2" not in r.detail, "cgo handed the preamble to gcc"


def test_no_shipped_fixture_is_refused():
    """The refusal must cost the claim sets nothing. Checked statically, so it
    runs (and can fail) on a box with no compilers at all."""
    n = 0
    for c in V.load_claims(CLAIMS):
        drv = D.driver_for(c.lang)
        for snip in list(c.new) + list(c.old):
            n += 1
            assert drv.refusal(drv.wrap(snip)) is None, (c.id, drv.refusal(drv.wrap(snip)))
    assert n >= 40, f"only {n} snippets checked — the claim sets did not load"
