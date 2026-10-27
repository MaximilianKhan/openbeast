#!/usr/bin/env python3
"""Review 2026-10-27, R2-6: `setup-tailscale.sh --publish-chat` (and
--publish-artifact) must mount the Unix socket when one is configured, not
silently replace the documented unix: hardening with a TCP mount.

Uses the stubbed rig from test_webui_default_admin (stub tailscale, sudo,
curl, docker on a temp PATH); nothing is really mounted or bound.

Run: python3 -m pytest tests/test_review1027_tailscale.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_webui_default_admin import _events, rig, ts_rig  # noqa: E402,F401


def _mounts(r):
    out = {}
    for k, a in _events(r):
        if k == "ts" and "serve" in a and "--bg" in a:
            port = next(x for x in a if x.startswith("--https=")).split("=")[1]
            out[port] = a[-1]
    return out


def _run(r, **env):
    r.set_state(auth=True, admin_pw="operator-chose-this")
    p = r.run("setup-tailscale.sh", "--publish-chat", "--publish-artifact",
              env_extra={"OPENBEAST_API_KEY": "k", **env})
    assert p.returncode == 0, p.stderr
    return p, _mounts(r)


def test_chat_and_artifact_sockets_are_mounted(ts_rig, tmp_path):
    chat, art = str(tmp_path / "chat.sock"), str(tmp_path / "art.sock")
    p, m = _run(ts_rig, OPENBEAST_CHAT_SOCKET=chat,
                OPENBEAST_CHAT_LOGIN_FROM="unix",
                OPENBEAST_ARTIFACT_SOCKET=art,
                OPENBEAST_ARTIFACT_LOGIN_FROM="unix")
    assert m["8445"] == f"unix:{chat}"
    assert m["8446"] == f"unix:{art}"
    assert "WARNING: OPENBEAST_CHAT_LOGIN_FROM=unix" not in p.stdout
    assert "WARNING: OPENBEAST_ARTIFACT_LOGIN_FROM=unix" not in p.stdout


def test_default_is_still_tcp(ts_rig):
    # Negative control: nothing set, nothing changes.
    p, m = _run(ts_rig)
    assert m["8445"] == "http://127.0.0.1:3003"
    assert m["8446"] == "http://127.0.0.1:3004"
    assert "LOGIN_FROM=unix" not in p.stdout


def test_login_from_unix_without_a_socket_warns(ts_rig):
    p, m = _run(ts_rig, OPENBEAST_CHAT_LOGIN_FROM="unix",
                OPENBEAST_ARTIFACT_LOGIN_FROM="UNIX")
    assert m["8445"] == "http://127.0.0.1:3003"
    assert "WARNING: OPENBEAST_CHAT_LOGIN_FROM=unix" in p.stdout
    assert "WARNING: OPENBEAST_ARTIFACT_LOGIN_FROM=unix" in p.stdout
