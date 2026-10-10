"""Network-facing review 2026-10-09, S2: the tool server pins Host.

The tool server — the one with /bash — was the only OpenBeast HTTP server
that did not check the Host header, so a DNS name re-pointed at 127.0.0.1
was same-origin with it and could POST JSON to /bash. Also covers the Host
parsing in agents/hostpolicy.py that it and the agent router now share.

In-process only: the app under an ASGI test client, /bash replaced by a stub
that records its calls. No port is opened and nothing is executed.

Run: python3 -m pytest tests/test_toolserver_host_review1009.py -q
"""
import importlib
import os
import sys

import pytest
from starlette.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

import hostpolicy  # noqa: E402
import openapi_tools  # noqa: E402
import tools as _tools  # noqa: E402


# ── hostpolicy: the parsing both servers now share ──────────────────────────

ALLOWED = hostpolicy.trusted_hosts("rig.lan")


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.0.0.1:3001", "localhost:8088", "LOCALHOST:8088",
    "[::1]:3001", "[::1]", "[0:0:0:0:0:0:0:1]:3001",
    "beast.tail1234.ts.net", "beast.tail1234.ts.net:443", "rig.lan:3001",
])
def test_host_allowed_accepts_our_own_names(host):
    assert hostpolicy.host_allowed(host, ALLOWED)


@pytest.mark.parametrize("host", [
    "evil.example", "evil.example:3001", "127.0.0.1.evil.example",
    "localhost.evil.example:3001", "ts.net", "evilts.net", "", "[", "[::1",
    "[not-an-ip]:3001", "localhost@evil.example", "*", "*.ts.net",
])
def test_host_allowed_refuses_everything_else(host):
    assert not hostpolicy.host_allowed(host, ALLOWED)


def test_a_bare_star_in_config_does_not_switch_pinning_off():
    """Starlette reads "*" as allow-any; a config value must not be able to
    do that here."""
    assert not hostpolicy.host_allowed("evil.example", hostpolicy.trusted_hosts("*"))
    assert hostpolicy.host_allowed("localhost", hostpolicy.trusted_hosts("*"))


def test_ip_literal_is_an_address_never_a_name():
    for ok in ("192.168.1.50:3001", "100.64.0.7", "[fd7a:115c::5]:3001", "127.0.0.2"):
        assert hostpolicy.is_ip_literal(ok), ok
    # Things that LOOK numeric but that a resolver, not the kernel, decides.
    for bad in ("evil.example", "2130706433", "0x7f.1", "127.0.0.1.evil.example", ""):
        assert not hostpolicy.is_ip_literal(bad), bad


# ── S2: the tool server pins Host ───────────────────────────────────────────

@pytest.fixture()
def tool_app(tmp_path, monkeypatch):
    """A keyless tool server whose /bash records instead of running."""
    ws = tmp_path / "files"
    ws.mkdir()
    monkeypatch.setenv("OPENBEAST_FILES_DIR", str(ws))
    for var in ("OPENBEAST_MCPO_ADMIN_KEY", "OPENBEAST_MCPO_GUEST_KEY",
                "OPENBEAST_IDENTITY_JWT_SECRET", "OPENBEAST_TOOLS_ALLOWED_HOSTS",
                "AGENT_WORKDIR"):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(_tools)
    ran = []

    def bash(command: str, timeout: int = 60) -> str:
        ran.append(command)
        return "ran"
    monkeypatch.setattr(openapi_tools.impl, "bash", bash)
    return openapi_tools.create_app, ran


def _bash(create_app, host):
    # The Host header itself, not a base_url: the test client cannot dial a
    # bracketed IPv6 literal, and the header is what the server reads.
    c = TestClient(create_app(), base_url="http://127.0.0.1:3001")
    return c.post("/bash", json={"command": "id"}, headers={"Host": host})


def test_rebound_host_cannot_run_bash(tool_app):
    """netsec S2 as reported: Host: evil.example:3001, JSON body, keyless."""
    create_app, ran = tool_app
    r = _bash(create_app, "evil.example:3001")
    assert r.status_code == 400
    assert "Invalid host header" in r.text
    assert "OPENBEAST_TOOLS_ALLOWED_HOSTS" in r.text      # names the fix
    assert ran == []
    # Negative control: the same request, dialled the way WebUI dials it.
    assert _bash(create_app, "localhost:3001").status_code == 200
    assert ran == ["id"]


@pytest.mark.parametrize("host", [
    "127.0.0.1:3001", "localhost:3001", "[::1]:3001",
    "192.168.1.50:3001",        # a LAN BIND_HOST, dialled as itself
    "100.64.0.7:3001",          # a tailnet BIND_HOST
    "beast.tail1234.ts.net",    # behind `tailscale serve`
])
def test_legitimate_callers_still_reach_the_tool_server(tool_app, host):
    create_app, ran = tool_app
    assert _bash(create_app, host).status_code == 200
    assert ran == ["id"]


@pytest.mark.parametrize("host", [
    "evil.example", "localhost.evil.example:3001", "127.0.0.1.nip.io:3001",
    "2130706433:3001",
])
def test_hostile_names_are_refused_on_every_route(tool_app, host):
    create_app, ran = tool_app
    c = TestClient(create_app(), base_url=f"http://{host}")
    assert c.post("/bash", json={"command": "id"}).status_code == 400
    assert c.get("/health").status_code == 400
    assert c.get("/openapi.json").status_code == 400
    assert ran == []


def test_extra_tool_hosts_come_from_the_environment(tool_app, monkeypatch):
    create_app, ran = tool_app
    assert _bash(create_app, "rig.lan:3001").status_code == 400
    monkeypatch.setenv("OPENBEAST_TOOLS_ALLOWED_HOSTS", "rig.lan, other.lan")
    assert _bash(create_app, "rig.lan:3001").status_code == 200
    assert _bash(create_app, "evil.example:3001").status_code == 400


def test_host_pinning_does_not_stand_in_for_the_key(tool_app, monkeypatch):
    """Pinning is in front of the key check, not instead of it."""
    create_app, ran = tool_app
    monkeypatch.setenv("OPENBEAST_MCPO_ADMIN_KEY", "k-admin")
    monkeypatch.setenv("OPENBEAST_MCPO_GUEST_KEY", "k-guest")
    assert _bash(create_app, "localhost:3001").status_code == 401
    assert ran == []
