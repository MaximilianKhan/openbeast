"""Review 2026-10-27 (R7 tests/CI): helpers the suite relies on.

R7-09: tests/_cdp_pipe.py left `ob-cdp-*` profile dirs (with a SingletonLock)
and chrome's own `com.google.Chrome.*` scratch dirs in TMPDIR, and its fixed
30 s CDP ceiling timed out under load. Driven with a fake "chrome" that makes
both kinds of litter and never answers — no browser needed.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

FAKE_CHROME = r"""#!/bin/bash
# Litter like chrome does: a scratch dir in $TMPDIR and a SingletonLock in
# the profile dir. Then never answer a CDP command.
mkdir -p "${TMPDIR:-/tmp}/com.google.Chrome.fake$$"
for a in "$@"; do
  case "$a" in --user-data-dir=*) touch "${a#--user-data-dir=}/SingletonLock" ;; esac
done
exec sleep 60
"""


def _driver(tmp_path, body: str):
    fake = tmp_path / "fake-chrome"
    fake.write_text(FAKE_CHROME)
    fake.chmod(0o755)
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(TESTS)!r})
        import _cdp_pipe
        _cdp_pipe.Chrome.CLOSE_WAIT = 0.5
        _cdp_pipe.Chrome.send = lambda self, *a, **k: (_ for _ in ()).throw(TimeoutError("fake"))
        c = _cdp_pipe.Chrome({str(fake)!r})
        for _ in range(100):                     # let the fake make its litter
            if (__import__("pathlib").Path(c.udd) / "SingletonLock").exists():
                break
            time.sleep(0.05)
    """) + textwrap.dedent(body)
    env = {**os.environ, "TMPDIR": str(tmpdir)}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=60, env=env, stdin=subprocess.DEVNULL), tmpdir


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX")
def test_close_leaves_nothing_in_tmpdir(tmp_path):
    r, tmpdir = _driver(tmp_path, "c.close()\nprint('closed')\n")
    assert r.returncode == 0 and "closed" in r.stdout, r.stderr
    assert sorted(p.name for p in tmpdir.iterdir()) == [], "close() left litter in TMPDIR"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX")
def test_exit_without_close_still_cleans_up(tmp_path):
    """A fixture that dies before close(): the atexit hook kills the browser
    and removes its profile dir."""
    r, tmpdir = _driver(tmp_path, "print(c.proc.pid)\n")
    assert r.returncode == 0, r.stderr
    pid = int(r.stdout.split()[-1])
    assert sorted(p.name for p in tmpdir.iterdir()) == [], "exit without close() left litter"
    try:
        os.kill(pid, 0)
        alive = Path(f"/proc/{pid}/stat").exists() and Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except ProcessLookupError:
        alive = False
    assert not alive, "the browser outlived the interpreter"


def test_cdp_timeout_scales_with_load(monkeypatch):
    sys.path.insert(0, str(TESTS))
    import _cdp_pipe
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    monkeypatch.setattr(os, "getloadavg", lambda: (1.0, 0, 0))
    assert _cdp_pipe.default_timeout() == _cdp_pipe.BASE_TIMEOUT       # idle: unchanged
    monkeypatch.setattr(os, "getloadavg", lambda: (8.0, 0, 0))
    assert _cdp_pipe.default_timeout() == 2 * _cdp_pipe.BASE_TIMEOUT
    monkeypatch.setattr(os, "getloadavg", lambda: (400.0, 0, 0))
    assert _cdp_pipe.default_timeout() == 3 * _cdp_pipe.BASE_TIMEOUT   # capped


# ---------------------------------------------------------------------------
# R7-04: under CI=true a skip outside the allowlist fails the pytest session.
# ---------------------------------------------------------------------------

def _gate_run(tmp_path, reason: str, **env):
    """A scratch pytest session using the REAL tests/conftest.py, with one
    test that skips for `reason`."""
    d = tmp_path / "gate"
    d.mkdir(exist_ok=True)
    (d / "conftest.py").write_text((TESTS / "conftest.py").read_text())
    (d / "test_one.py").write_text(
        "import pytest\n\ndef test_x():\n    pytest.skip(%r)\n\ndef test_ok():\n    pass\n" % reason)
    base = {k: v for k, v in os.environ.items()
            if k not in ("CI", "GITHUB_ACTIONS", "OPENBEAST_SKIP_GATE", "PYTEST_ADDOPTS")}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(d)],
                          capture_output=True, text=True, timeout=120, cwd=d, env={**base, **env})


def test_ci_fails_a_skip_outside_the_allowlist(tmp_path):
    r = _gate_run(tmp_path, "no chromium/chrome binary", CI="true")
    assert r.returncode == 1, r.stdout
    assert "skips outside the CI allowlist" in r.stdout and "no chromium/chrome binary" in r.stdout


def test_ci_accepts_an_allowlisted_skip(tmp_path):
    r = _gate_run(tmp_path, "network tests disabled (OPENBEAST_SKIP_NETWORK_TESTS=1)", CI="true")
    assert r.returncode == 0, r.stdout
    assert "outside the CI allowlist" not in r.stdout


def test_skip_gate_is_off_locally_and_can_be_overridden(tmp_path):
    assert _gate_run(tmp_path, "no chromium/chrome binary").returncode == 0           # no CI
    w = _gate_run(tmp_path, "no chromium/chrome binary", CI="true", OPENBEAST_SKIP_GATE="warn")
    assert w.returncode == 0 and "outside the CI allowlist" in w.stdout
    s = _gate_run(tmp_path, "no chromium/chrome binary", OPENBEAST_SKIP_GATE="strict")
    assert s.returncode == 1
