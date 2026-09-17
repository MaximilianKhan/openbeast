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
    # "could not be started" is a RESULT, not an exception a direct caller can
    # forget to catch — and the driver turns it into a transient Result.
    rc, out = _proc.run([str(tmp_path / "no-such-compiler")], timeout=10)
    assert rc is None and "not installed" in out
    r = D._run([str(tmp_path / "no-such-compiler")])
    assert not r and r.transient and "not installed" in r.detail


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
        assert r.refused, "declining to import is not a verdict on the snippet"
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
        assert r.refused and "hunter2" not in r.detail
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


# ==========================================================================
# round 2 — an adversarial reviewer ran round 1 against real toolchains
# ==========================================================================
import threading      # noqa: E402


# --- _proc: a grandchild that leaves the group must not hold the call -------

def _escaping_compiler(tmp_path, name, hang: bool, hold_s: int = 30):
    """A 'compiler' whose worker calls setsid() — so killpg cannot reach it —
    and keeps the inherited stdout pipe open for `hold_s` seconds."""
    pidfile = tmp_path / f"{name}.escaped.pid"
    exe = _script(tmp_path / name,
                  f"#!{sys.executable}\n"
                  "import os, sys, time\n"
                  "if os.fork() == 0:\n"
                  "    os.setsid()\n"
                  f"    open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
                  f"    time.sleep({hold_s})\n"
                  "    os._exit(0)\n"
                  "print('stub: started', flush=True)\n"
                  + ("time.sleep(300)\n" if hang else ""))
    return exe, pidfile


def _kill(pidfile):
    try:
        os.kill(_worker_pid(pidfile), 9)
    except (ProcessLookupError, AssertionError):
        pass


def test_a_timeout_does_not_wait_for_a_grandchild_that_left_the_group(tmp_path):
    """Measured on round 1: a 1 s timeout returned after 41 s / 20 s / 16 s —
    each the escaped grandchild's sleep. Closing an fd does not wake a thread
    blocked in read(), and stdout.close() then waited on that thread's lock."""
    exe, pidfile = _escaping_compiler(tmp_path, "escapecc", hang=True)
    try:
        t0 = time.time()
        with pytest.raises(subprocess.TimeoutExpired) as ei:
            _proc.run([exe], timeout=1.0)
        took = time.time() - t0
        assert took < 3.0, f"a 1 s timeout took {took:.1f} s"
        assert "stub: started" in (ei.value.output or "")
        # the case was built: the grandchild DID escape and IS still alive
        assert _alive(_worker_pid(pidfile)), "nothing escaped — this proved nothing"
    finally:
        _kill(pidfile)


def test_a_clean_exit_does_not_wait_for_it_either(tmp_path):
    exe, pidfile = _escaping_compiler(tmp_path, "daemoncc", hang=False)
    try:
        t0 = time.time()
        rc, out = _proc.run([exe], timeout=20.0)
        took = time.time() - t0
        assert rc == 0 and "stub: started" in out
        assert took < 3.0, f"the call waited {took:.1f} s on a pipe held by a stranger"
    finally:
        _kill(pidfile)


def test_timeouts_never_close_somebody_elses_fd(tmp_path):
    """The double close: round 1 closed the pipe's fd under the reader and
    then closed the file object over it. In between, another thread's open()
    could be handed that fd number — and lose it to the second close (its
    write failed with EBADF). 50 timeouts through the escape path, while four
    threads open/write/close as fast as they can."""
    exe, pidfile = _escaping_compiler(tmp_path, "hammercc", hang=True, hold_s=20)
    stop, errors, writes = threading.Event(), [], [0]

    def hammer(k):
        path = tmp_path / f"hammer-{k}.txt"
        while not stop.is_set():
            try:
                with open(path, "w") as fh:
                    fh.write("x" * 64)
                    fh.flush()
                    os.fsync(fh.fileno())
                writes[0] += 1
            except OSError as e:
                errors.append(e)

    threads = [threading.Thread(target=hammer, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    pids = []
    try:
        for _ in range(50):
            pidfile.unlink(missing_ok=True)
            with pytest.raises(subprocess.TimeoutExpired):
                _proc.run([exe], timeout=0.15)
            if pidfile.exists() and pidfile.read_text().strip():
                pids.append(int(pidfile.read_text()))
    finally:
        stop.set()
        for t in threads:
            t.join()
        for pid in pids:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
    assert writes[0] > 100, "the hammer barely ran — this measured nothing"
    assert errors == [], f"{len(errors)} file operations failed: {errors[:3]}"


# --- the python driver must not run code -------------------------------------

@pytest.fixture()
def booby_trapped_cwd(tmp_path, monkeypatch):
    """A cwd holding a test_*.py that writes a marker when EXECUTED — what
    unittest's discovery does to every such file it finds."""
    marker = tmp_path / "PWNED"
    (tmp_path / "test_pwn.py").write_text(
        f"open({str(marker)!r}, 'w').write('ran')\n"
        "import unittest\n\nclass T(unittest.TestCase):\n"
        "    def test_x(self):\n        pass\n")
    monkeypatch.chdir(tmp_path)
    return marker


@pytest.mark.parametrize("src", [
    "import unittest.__main__\n",
    "from unittest import __main__\n",
    "import unittest.__main__ as m\n",
    "import venv.__main__\n",
    "import tkinter.__main__\n",
    "import json.__init__.__main__\n",
])
def test_a_dunder_module_is_never_imported(src, booby_trapped_cwd):
    """`unittest.__main__` passes the stdlib gate (its ROOT is unittest) and
    has no __name__ guard: importing it ran discovery in the cwd."""
    r = D.driver_for("python").compile_source(src)
    assert not r and r.refused and "dunder" in r.detail, r.detail
    assert not booby_trapped_cwd.exists(), "the claim EXECUTED code from the cwd"


def test_the_resolver_child_does_not_even_stand_in_our_cwd(booby_trapped_cwd, monkeypatch):
    """The belt under the refusal, tested with the refusal REMOVED: the child
    runs in a fresh empty directory, so discovery finds nothing of ours. The
    control proves the trap is live — the same import from this cwd fires it."""
    py = D.driver_for("python")
    monkeypatch.setattr(D.PythonDriver, "_unsafe", classmethod(lambda cls, m, a=None: None))
    py.compile_source("import unittest.__main__\n")
    assert not booby_trapped_cwd.exists(), "the child ran discovery in OUR cwd"
    subprocess.run([sys.executable, "-c", "import unittest.__main__"],
                   capture_output=True, timeout=60)
    assert booby_trapped_cwd.exists(), "the trap never fires — the test above is empty"


def test_an_attribute_walk_cannot_be_turned_into_a_dunder_import(booby_trapped_cwd):
    """`unittest.__main__` as an ATTRIBUTE: getattr fails, and the child's
    submodule fallback must not answer by importing it."""
    r = D.driver_for("python").compile_source("import unittest\nunittest.__main__\n")
    assert not r and "does not exist" in r.detail
    assert not booby_trapped_cwd.exists()


def test_ordinary_python_still_resolves():
    py = D.driver_for("python")
    for src in ("import unittest\nunittest.TestCase.assertEqual\n",
                "from xml import etree\n",
                "from __future__ import annotations\nimport json\n",
                "import os\nos.path.__name__\n"):
        assert py.compile_source(src), src


# --- nothing of the parent's environment reaches a diagnostic ----------------

ENV_SECRET = "sk-beastlang-round2-do-not-leak"


def test_the_scrubbed_env_keeps_locations_and_drops_everything_else(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", ENV_SECRET)
    monkeypatch.setenv("LLAMA_API_KEY", ENV_SECRET)
    monkeypatch.setenv("GOFLAGS", "-x")
    monkeypatch.setenv("ZIG_GLOBAL_CACHE_DIR", "/tmp/zc")
    env = _proc.scrubbed_env()
    assert ENV_SECRET not in env.values()
    assert env["PATH"] == os.environ["PATH"] and env.get("HOME") == os.environ.get("HOME")
    assert env["ZIG_GLOBAL_CACHE_DIR"] == "/tmp/zc"
    assert "GOFLAGS" not in env, "GO* is for the go driver only"
    go = D.GoDriver.env(CGO_ENABLED="0")
    assert ENV_SECRET not in go.values()
    assert go["GOFLAGS"] == "-mod=readonly" and go["GOPROXY"] == "off"


@pytest.mark.parametrize("lang,exe", [("c", "gcc"), ("cpp", "g++"), ("rust", "rustc"),
                                      ("zig", "zig"), ("go", "go")])
def test_a_compiler_that_dumps_its_environment_has_nothing_to_dump(
        lang, exe, tmp_path, monkeypatch):
    """Toolchain-independent: the 'compiler' is a stub whose whole diagnostic
    IS its environment. Whatever a real one can be tricked into printing, it
    cannot print what it was never given."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _script(bindir / exe, "#!/bin/sh\nenv\nexit 1\n")
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("BEASTLANG_TEST_SECRET", ENV_SECRET)
    drv = D.driver_for(lang)
    r = drv.compile_source(drv.wrap("x"))
    assert "PATH=" in r.detail, f"the stub was not what ran: {r.detail[:200]}"
    assert ENV_SECRET not in r.detail and "BEASTLANG_TEST_SECRET" not in r.detail


@pytest.mark.skipif(not shutil.which("rustc"), reason="rustc absent")
def test_rustc_cannot_print_a_parent_env_var_even_with_the_refusal_off(monkeypatch):
    """The ROOT fix for env!(): with the scan disabled the macro runs — and
    finds nothing, because the variable was never in rustc's environment."""
    monkeypatch.setenv("BEASTLANG_TEST_SECRET", ENV_SECRET)
    monkeypatch.setattr(D.RustDriver, "refusal", lambda self, s: None)
    r = D.driver_for("rust").compile_source(
        'fn main() { compile_error!(env!("BEASTLANG_TEST_SECRET")); }\n')
    assert not r and ENV_SECRET not in r.detail, r.detail[:300]
    assert "not defined" in r.detail, "rustc did not evaluate env!() — control is dead"


def test_a_macro_cannot_be_smuggled_in_under_another_name(secret):
    rs = D.driver_for("rust")
    for src in (f'use std::include as inc;\nfn main() {{ inc!("{secret}"); }}\n',
                f'use std::include_str as inc;\nfn main() {{ let _ = inc!("{secret}"); }}\n',
                f'use core::include_bytes as b;\nfn main() {{ let _ = b!("{secret}"); }}\n',
                'use std::env as e;\nfn main() { compile_error!(e!("HOME")); }\n',
                'use std::{option_env as oe, fmt};\nfn main() { let _ = oe!("HOME"); }\n',
                f'pub use std::{{include_str as s}};\nfn main() {{ let _ = s!("{secret}"); }}\n'):
        r = rs.compile_source(src)
        assert not r and r.refused, (src, r.detail)
        assert "hunter2" not in r.detail
    # negative controls: the MODULE std::env, imported the way real code does
    for src in ('use std::env;\nfn main() { let _ = env::args(); }\n',
                'use std::fmt as f;\nuse std::env;\nfn main() { let _ = env::var("X"); }\n',
                'use std::collections::HashMap as Map;\nfn main() { let _: Map<u8, u8> = Map::new(); }\n'):
        assert rs.refusal(src) is None, src


# --- a refusal is not a verdict, and a comment is not a directive -------------

def test_a_refused_old_form_does_not_make_a_claim_verified(monkeypatch):
    """Round 1 returned refusals as plain ok=False: an OLD form of VALID C
    carrying `/* never #include "/etc/passwd" */` was refused, "failed", and
    the claim came back VERIFIED. Rebuilt here with a stub driver so the
    verdict logic is what is under test, on any machine."""
    class Stub(D.Driver):
        lang, exe = "stublang", sys.executable

        def available(self):
            return True

        def version(self):
            return "stub 1.0.0"

        def refusal(self, source):
            return "it looks dangerous" if "DANGER" in source else None

        def compile_source(self, source, variant=None):
            return self._in_tmp(source, "claim.txt", lambda p, d: ["true"])

    monkeypatch.setitem(D.DRIVERS, "stublang", Stub())
    monkeypatch.setattr(V, "_VERDICTS", {})
    claim = V.Claim({"id": "k", "lang": "stublang", "old": ["DANGER but valid"],
                     "new": ["fine"]}, "inline", ".")
    r = V.verify(claim)
    assert r["verdict"] == V.UNVERIFIABLE and "never judged" in r["detail"], r
    assert V._VERDICTS == {}, "a refusal was remembered as a verdict"
    # negative control: the same claim with nothing to refuse is judged
    # (old compiles -> NOT_A_BREAK), so UNVERIFIABLE above is the refusal
    claim.old = ["harmless and valid"]
    assert V.verify(claim)["verdict"] == V.NOT_A_BREAK


@pytest.mark.skipif(not shutil.which("gcc"), reason="gcc absent")
def test_the_reviewers_synthetic_c_claim_is_not_a_break():
    claim = V.Claim({"id": "synthetic", "lang": "c", "variant": "c17",
                     "old": ['/* never #include "/etc/passwd" */\nint main(void){return 0;}\n'],
                     "new": ["int main(void){return 0;}\n"]}, "inline", ".")
    assert V.verify(claim)["verdict"] == V.NOT_A_BREAK


C_NOT_DIRECTIVES = [
    '#include <stdio.h>\nint main(void){ printf("#include \\"/etc/passwd\\"\\n"); return 0; }\n',
    '/* never #include "/etc/passwd" */\nint main(void){return 0;}\n',
    '// #include "/etc/passwd"\nint main(void){return 0;}\n',
    'int main(void){ const char *s = "# include </etc/shadow>"; return !s; }\n',
    'int main(void){ const char *s = "line one\\\n#include \\"/etc/passwd\\""; return !s; }\n',
    '#if __has_include(<stdio.h>)\n#include <stdio.h>\n#endif\nint main(void){return 0;}\n',
]


@pytest.mark.parametrize("src", C_NOT_DIRECTIVES)
def test_c_text_that_only_MENTIONS_an_include_is_not_refused(src):
    assert D.driver_for("c").refusal(src) is None, src
    if shutil.which("gcc"):
        assert D.driver_for("c").compile_source(src), "and it is valid C"


def test_c_lexing_tricks_still_refuse(secret):
    c = D.driver_for("c")
    for src in (
            # an unbalanced quote on the line before makes the directive LOOK
            # like the inside of a string. It is not; cpp reads it.
            f'const char *s = "oops;\n#include "{secret}"\nint main(void){{return 0;}}\n',
            f"char q = '\"';\n#include \"{secret}\"\nint main(void){{return 0;}}\n",
            f'int c = L\'"\' + sizeof("/*");\n#include "{secret}"\nconst char *z = "*/";\n',
            f'/* a comment that ends */ #include "{secret}"\n',
            f'/* a comment\n   that ends here */ #include "{secret}"\n',
            f'\f#include "{secret}"\n',
            f'int x;\r#include "{secret}"\r',
            f'#if __has_include("{secret}")\n#error present\n#endif\n',
            f'#if 1 && __has_include_next(<{secret}>)\n#endif\n',
            '#if __has_include("../../../../etc/shadow")\n#endif\n',
            f'#define H __has_include("{secret}")\n#if H\n#endif\n',
            f'#define P "{secret}"\n#if __has_include(P)\n#endif\n'):
        assert c.refusal(src), f"NOT refused: {src!r}"


def test_rust_and_zig_scans_ignore_comments_and_strings(secret):
    rs, z = D.driver_for("rust"), D.driver_for("zig")
    for src in ('/// like include_str!("/etc/passwd") but safe\nfn main() {}\n',
                '/* include!("/etc/passwd") /* nested */ env!("HOME") */\nfn main() {}\n',
                'fn main() { let s = "env!(HOME)"; let _ = s; }\n',
                'fn main() { let s = r#"include_str!("/etc/passwd")"#; let _ = s; }\n',
                "fn f<'a>(x: &'a str) -> &'a str { x } // option_env!(\"X\")\nfn main() {}\n"):
        assert rs.refusal(src) is None, src
    for src in ('// @embedFile("/etc/passwd")\nconst x = 1;\n',
                'const s = "@import(x)";\n',
                'const s =\n    \\\\@embedFile("/etc/passwd")\n;\n'):
        assert z.refusal(src) is None, src
    # …and stripping them opened no bypass: a quote char that would desync a
    # naive lexer, a comment between the macro and its `!`, doubt -> refuse
    for src in (f"fn main() {{ let q = '\"'; let _ = include_str!(\"{secret}\"); let r = '\"'; }}\n",
                f'fn main() {{ let _ = include_str /* " */ ! ("{secret}"); }}\n',
                f'fn main() {{ let _ = "unterminated; let _ = include_str!("{secret}");\n'):
        assert rs.refusal(src), f"NOT refused: {src!r}"
    for src in (f"const q = '\"';\nconst s = @embedFile(\"{secret}\");\nconst r = '\"';\n",
                f'const a = "oops;\nconst s = @embedFile // x\n ("{secret}");\n'):
        assert z.refusal(src), f"NOT refused: {src!r}"
