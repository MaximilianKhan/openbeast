"""openapi_tools.main() startup guards (review identity-rbac-4, round 2).

1. A keyless tool server (bash included, identity headers taken as sent) on
   a non-loopback BIND_HOST is remote code execution for that network. main()
   must refuse to start unless ALLOW_OPEN_TOOLS=true acknowledges it.
2. main() must make the process non-dumpable BEFORE serving: it holds the
   RBAC keys / JWT secret in its environ and is an ancestor of every
   model-authored shell.

Each case runs main() in a child interpreter with a stub `uvicorn` module
(records the call, binds nothing) and a hand-built env, so no host state or
real key leaks in.

Run: python3 -m pytest tests/test_tool_server_startup.py -q
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))

import openapi_tools  # noqa: E402

_PROBE = r'''
import ctypes, sys, types
sys.path.insert(0, sys.argv[1])
fake = types.ModuleType("uvicorn")
def run(app, host, port, **kw):
    dumpable = -1
    if sys.platform.startswith("linux"):
        dumpable = ctypes.CDLL(None).prctl(3, 0, 0, 0, 0)   # PR_GET_DUMPABLE
    print(f"UVICORN_RUN host={host} dumpable={dumpable}")
fake.run = run
sys.modules["uvicorn"] = fake
import openapi_tools
openapi_tools.main()
'''


def _run_main(tmp_path, **env_extra):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        # Real HOME only so a `pip --user` fastapi/jwt stays importable.
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "OPENBEAST_FILES_DIR": str(tmp_path / "files"),
        "OPENBEAST_TOOL_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
        "OPENBEAST_RUN_DIR": str(tmp_path / "run"),
    }
    if "PYTHONPATH" in os.environ:
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", _PROBE, str(ROOT / "agents")],
                          env=env, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("host", ["192.168.1.20", "100.64.0.7", "0.0.0.0",
                                  "::", "rig.local", ""])
def test_keyless_non_loopback_bind_refuses(tmp_path, host):
    r = _run_main(tmp_path, OPENBEAST_BIND=host)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "UVICORN_RUN" not in r.stdout          # never served
    assert "refusing to serve" in r.stderr
    assert "setup-mcpo-keys.sh" in r.stderr and "ALLOW_OPEN_TOOLS" in r.stderr


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "[::1]",
                                  "localhost"])
def test_keyless_loopback_bind_serves(tmp_path, host):
    # Negative control: Phase-1 parity on loopback is unchanged.
    r = _run_main(tmp_path, OPENBEAST_BIND=host)
    assert r.returncode == 0, r.stderr
    assert f"UVICORN_RUN host={host}" in r.stdout


def test_default_bind_serves(tmp_path):
    r = _run_main(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "UVICORN_RUN host=127.0.0.1" in r.stdout


@pytest.mark.parametrize("keys", [
    {"OPENBEAST_MCPO_ADMIN_KEY": "a" * 32},
    {"OPENBEAST_MCPO_GUEST_KEY": "g" * 32},
    {"OPENBEAST_MCPO_ADMIN_KEY": "a" * 32, "OPENBEAST_MCPO_GUEST_KEY": "g" * 32},
])
def test_keyed_non_loopback_bind_serves(tmp_path, keys):
    r = _run_main(tmp_path, OPENBEAST_BIND="192.168.1.20", **keys)
    assert r.returncode == 0, r.stderr
    assert "UVICORN_RUN host=192.168.1.20" in r.stdout


def test_whitespace_keys_do_not_count(tmp_path):
    r = _run_main(tmp_path, OPENBEAST_BIND="192.168.1.20",
                  OPENBEAST_MCPO_ADMIN_KEY="   ")
    assert r.returncode == 2
    assert "UVICORN_RUN" not in r.stdout


@pytest.mark.parametrize("val,serves", [("true", True), ("1", True),
                                        ("false", False), ("", False)])
def test_allow_open_tools_acknowledgement(tmp_path, val, serves):
    r = _run_main(tmp_path, OPENBEAST_BIND="0.0.0.0",
                  OPENBEAST_ALLOW_OPEN_TOOLS=val)
    assert ("UVICORN_RUN" in r.stdout) is serves, (r.stdout, r.stderr)
    assert r.returncode == (0 if serves else 2)


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="prctl is Linux-only")
def test_main_hardens_before_serving(tmp_path):
    r = _run_main(tmp_path)
    assert "UVICORN_RUN host=127.0.0.1 dumpable=0" in r.stdout, (r.stdout, r.stderr)


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="prctl is Linux-only")
def test_main_keep_dumpable_opt_out(tmp_path):
    # Negative control: the probe does see a dumpable process when opted out.
    r = _run_main(tmp_path, OPENBEAST_KEEP_DUMPABLE="1")
    assert "dumpable=1" in r.stdout, (r.stdout, r.stderr)


def test_bind_is_loopback_mirrors_conf_sh():
    for h in ("127.0.0.1", "127.9.9.9", "::1", "[::1]", "localhost", "LOCALHOST"):
        assert openapi_tools.bind_is_loopback(h), h
    for h in ("0.0.0.0", "::", "192.168.1.20", "100.100.1.1", "rig", "", "128.0.0.1"):
        assert not openapi_tools.bind_is_loopback(h), h
