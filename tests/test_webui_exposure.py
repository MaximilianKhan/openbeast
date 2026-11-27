#!/usr/bin/env python3
"""Who can reach Open WebUI's API, and when a login is enforced.

Review findings (webui-searxng, 2026-11):

* CORS. docker-compose never set CORS_ALLOW_ORIGIN, so upstream's '*' applied
  with allow_credentials=True, and Starlette echoed back any Origin. On an
  auth-off WebUI (the default) any web page the operator opened could POST the
  sign-in, read the admin token and run code; with auth on, any page served by
  another tailnet host (same-site: ts.net is a public suffix) could read the
  admin's API. scripts/lib/conf.sh now computes the WebUI's real origins and
  compose passes them as CORS_ALLOW_ORIGIN (Socket.IO uses the same list, so
  it must be complete: loopback, the bind address, the tailnet name).
* WEBUI_AUTH=false is honoured only while the WebUI is reachable from this
  machine alone: a non-loopback BIND_HOST, or a :443 `tailscale serve` mount
  still live, keeps login on unless ALLOW_OPEN_WEBUI=true acknowledges it.

Each test sources the real conf.sh in a scratch REPO_DIR with stub
`tailscale` / `hostname` binaries, and reads the resolved variables.

Run: pytest tests/test_webui_exposure.py
"""

import json
import os
import re
import shutil
import subprocess
import textwrap
from urllib.parse import urlparse

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TS_STUB = textwrap.dedent(r'''
    #!/usr/bin/env python3
    import json, os, sys
    a = sys.argv[1:]
    if a[:2] == ["status", "--json"] and os.environ.get("TS_DNS"):
        print(json.dumps({"Self": {"DNSName": os.environ["TS_DNS"]}}))
    elif a[:3] == ["serve", "status", "--json"] and os.environ.get("TS_SERVE_SEQ"):
        # One answer per call, in order: a tailscaled whose answer changes.
        seq = os.environ["TS_SERVE_SEQ"]
        lines = open(seq).read().splitlines()
        open(seq, "w").write("\n".join(lines[1:]))
        if not lines or lines[0] == "NONE":
            sys.exit(1)
        print(lines[0])
    elif a[:3] == ["serve", "status", "--json"] and os.environ.get("TS_SERVE"):
        print(os.environ["TS_SERVE"])
    else:
        sys.exit(1)
''').lstrip()

HOSTNAME_STUB = textwrap.dedent(r'''
    #!/bin/sh
    if [ "$1" = "-I" ]; then echo "192.168.1.20 fd00::5 "; else echo "Beast"; fi
''').lstrip()


def _serve(proxy, port=443):
    return json.dumps({"TCP": {str(port): {"HTTPS": True}},
                       "Web": {f"beast.example.ts.net:{port}":
                               {"Handlers": {"/": {"Proxy": proxy}}}}})


@pytest.fixture()
def conf(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(os.path.join(REPO, "scripts", "lib"), root / "scripts" / "lib")
    (root / "openbeast.conf").write_text("SEARXNG_SECRET=stub\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def run(conf_text="", ts=True, **env_extra):
        (root / "openbeast.conf").write_text("SEARXNG_SECRET=stub\n" + conf_text)
        # ts=False: a tailscale that never answers. PATH keeps /usr/bin, so
        # leaving the stub out would run a real tailscale on a box with one.
        for name, body in (("tailscale", TS_STUB if ts else "#!/bin/sh\nexit 1\n"),
                           ("hostname", HOSTNAME_STUB)):
            p = bin_dir / name
            if p.exists():
                p.unlink()
            if body:
                p.write_text(body)
                p.chmod(0o755)
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path),
               "REPO_DIR": str(root), "OB_CONF_READONLY": "1", "OB_CONF_LINTED": "1",
               "OPENBEAST_RUN_DIR": str(tmp_path / "run"), "OB_WEBUI_443_RECHECK_S": "0"}
        env.update(env_extra)
        r = subprocess.run(
            ["bash", "-c", 'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null; '
                           'printf "%s\\n%s\\n%s\\n" "$OPENBEAST_WEBUI_AUTH" "$OPENBEAST_WEBUI_CORS" '
                           '"$OPENBEAST_WEBUI_HOSTS"'],
            env=env, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        auth, cors, hosts = r.stdout.splitlines()[-3:]
        r.auth, r.cors, r.hosts = auth, cors, hosts
        r.origins = cors.split(";")
        return r
    return run


# --- CORS --------------------------------------------------------------------

def test_compose_never_falls_back_to_the_wildcard():
    text = open(os.path.join(REPO, "docker-compose.yml")).read()
    m = re.search(r"^\s*-\s*CORS_ALLOW_ORIGIN=(.*)$", text, re.M)
    assert m, "open-webui has no CORS_ALLOW_ORIGIN: upstream's '*' + credentials applies"
    val = m.group(1)
    assert val.startswith("${OPENBEAST_WEBUI_CORS:-") and "*" not in val, val


def test_default_install_allows_only_loopback(conf):
    r = conf(ts=False)
    assert r.origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_the_tailnet_name_is_allowed(conf):
    """Tailscale Serve publishes the WebUI as https://<fqdn>: without it the
    browser's own origin fails Socket.IO's check and the tailnet UI breaks."""
    r = conf(TS_DNS="Beast.Example.ts.net.")
    assert "https://beast.example.ts.net" in r.origins, r.cors
    assert not [o for o in r.origins if o.endswith(".ts.net:8446")]


def test_the_tailnet_name_survives_a_status_outage(conf):
    """`tailscale status` is transient (boot race with tailscaled, logged out,
    >3 s): the persisted :443 mount still names the host, so a start then
    must not drop https://<fqdn> and break Socket.IO on the tailnet UI."""
    r = conf("WEBUI_AUTH=true\n", TS_SERVE=_serve("http://127.0.0.1:3000"))
    assert "https://beast.example.ts.net" in r.origins, r.cors


def test_the_tailnet_name_is_remembered(conf, tmp_path):
    """tailscaled wholly unreachable: the last name seen is reused, so the
    container env (and CORS) does not flip between runs."""
    conf(TS_DNS="beast.example.ts.net.", OB_CONF_READONLY="")
    assert (tmp_path / "run" / "webui-tailnet-name").read_text().strip() == "beast.example.ts.net"
    r = conf()   # stub tailscale answers nothing
    assert "https://beast.example.ts.net" in r.origins, r.cors
    r = conf(ts=False)
    assert "https://beast.example.ts.net" in r.origins, r.cors


def test_a_readonly_source_writes_no_cache(conf, tmp_path):
    conf(TS_DNS="beast.example.ts.net.")
    assert not (tmp_path / "run" / "webui-tailnet-name").exists()


def test_a_junk_cache_is_ignored(conf, tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "webui-tailnet-name").write_text("evil.example;http://x\n")
    r = conf(ts=False)
    assert r.origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_a_specific_bind_is_allowed(conf):
    r = conf(ts=False, OPENBEAST_BIND="192.168.1.20", ALLOW_OPEN_WEBUI="true")
    assert "http://192.168.1.20:3000" in r.origins and "http://beast:3000" in r.origins


@pytest.mark.parametrize("bind,origin,host", [
    ("127.0.0.2", "http://127.0.0.2:3000", "127.0.0.2"),
    ("::1", "http://[::1]:3000", "[::1]"),
])
def test_a_non_default_loopback_bind_is_an_origin(conf, bind, origin, host):
    """The WebUI binds only BIND_HOST, so it is opened at that address. The
    Host list allowed it but CORS (= Socket.IO's origin list) did not, so
    the browser's handshake was refused and chat streaming broke."""
    r = conf(ts=False, OPENBEAST_BIND=bind)
    assert r.auth == "false", r.stderr          # still loopback: no login forced
    assert origin in r.origins, r.cors
    assert host in r.hosts.split(","), r.hosts
    for o in r.origins:
        u = urlparse(o)
        assert u.scheme in ("http", "https") and u.netloc and not u.path, o


def test_a_wildcard_bind_lists_the_box_addresses(conf):
    r = conf(ts=False, OPENBEAST_BIND="0.0.0.0")
    assert {"http://192.168.1.20:3000", "http://[fd00::5]:3000"} <= set(r.origins), r.cors


def test_operator_origins_are_added(conf):
    r = conf("WEBUI_ORIGINS=https://chat.example.org, https://x.example.org;http://y:81\n", ts=False)
    assert {"https://chat.example.org", "https://x.example.org", "http://y:81"} <= set(r.origins)


def test_operator_wildcard_is_the_whole_list(conf):
    """Upstream rejects '*' mixed with origins (validate_cors_origin raises),
    so an explicit '*' must be the whole value."""
    assert conf("WEBUI_ORIGINS=*\n", ts=False).cors == "*"


@pytest.mark.parametrize("kw", [dict(ts=False), dict(TS_DNS="beast.example.ts.net."),
                                dict(ts=False, OPENBEAST_BIND="0.0.0.0"),
                                dict(ts=False, OPENBEAST_BIND="fd00::7",
                                     OPENBEAST_ALLOW_OPEN_WEBUI="true")])
def test_every_origin_passes_upstream_validation(conf, kw):
    """config.py validate_cors_origin: http/https scheme and a netloc, or the
    WebUI refuses to boot."""
    for o in conf(**kw).origins:
        u = urlparse(o)
        assert u.scheme in ("http", "https") and u.netloc and not u.path, o


# --- WEBUI_AUTH=false only where nothing else can reach the WebUI -------------

def test_auth_off_on_loopback_is_honoured(conf):
    r = conf(ts=False)
    assert r.auth == "false"


def test_a_network_bind_keeps_login_on(conf):
    """BIND_HOST=<LAN IP> + setup-mcpo-keys.sh cleared every refusal and left
    a no-login admin WebUI (bash included) on the LAN."""
    r = conf(ts=False, OPENBEAST_BIND="192.168.1.20",
             OPENBEAST_MCPO_ADMIN_KEY="a", OPENBEAST_MCPO_GUEST_KEY="g")
    assert r.auth == "true"
    assert "WEBUI_AUTH=false ignored" in r.stderr and "BIND_HOST=192.168.1.20" in r.stderr


def test_an_acknowledged_network_bind_stays_open(conf):
    r = conf("ALLOW_OPEN_WEBUI=true\n", ts=False, OPENBEAST_BIND="192.168.1.20")
    assert r.auth == "false"
    assert "ignored" not in r.stderr


def test_a_live_443_mount_keeps_login_on(conf):
    """setup-tailscale's :443 mount persists. Turning auth back off and
    restarting recreated an auth-off WebUI behind it, unacknowledged."""
    r = conf("WEBUI_AUTH=false\n", TS_SERVE=_serve("http://127.0.0.1:3000"))
    assert r.auth == "true"
    assert ":443" in r.stderr
    r = conf(TS_SERVE=_serve("http://127.0.0.1:3000"), OPENBEAST_WEBUI_AUTH="false")
    assert r.auth == "true"


def test_a_live_443_mount_with_the_acknowledgement_stays_open(conf):
    r = conf("WEBUI_AUTH=false\nALLOW_OPEN_WEBUI=true\n",
             TS_SERVE=_serve("http://127.0.0.1:3000"))
    assert r.auth == "false"


def test_a_mount_seen_before_keeps_login_on_while_tailscale_is_silent(conf, tmp_path):
    """tailscaled re-applies a persisted :443 mount when it comes up: a start
    that raced it at boot (no answer) must not recreate an auth-off WebUI."""
    conf("WEBUI_AUTH=false\n", TS_SERVE=_serve("http://127.0.0.1:3000"),
         OB_CONF_READONLY="")
    r = conf("WEBUI_AUTH=false\n")          # tailscale answers nothing now
    assert r.auth == "true", r.stderr
    r = conf("WEBUI_AUTH=false\n", ts=False)
    assert r.auth == "true", r.stderr
    # a real "no mount" answer clears it
    conf("WEBUI_AUTH=false\n", TS_SERVE=_serve("http://127.0.0.1:3004"), OB_CONF_READONLY="")
    assert conf("WEBUI_AUTH=false\n").auth == "false"


def test_one_no_mount_answer_after_a_mount_is_rechecked(conf, tmp_path):
    """tailscaled at boot can answer "no serve config" before it re-applies
    the persisted :443 mount. That single "no" used to be cached and
    believed, so the start recreated an auth-off WebUI behind the mount."""
    mounted = _serve("http://127.0.0.1:3000")
    empty = json.dumps({})
    conf("WEBUI_AUTH=false\n", TS_SERVE=mounted, OB_CONF_READONLY="")
    seq = tmp_path / "serve-seq"
    seq.write_text(f"{empty}\n{mounted}\n")      # boot: "no", then the mount
    r = conf("WEBUI_AUTH=false\n", TS_SERVE_SEQ=str(seq), OB_CONF_READONLY="")
    assert r.auth == "true", r.stderr
    seq.write_text(f"{empty}\nNONE\n")           # "no", then no answer at all
    r = conf("WEBUI_AUTH=false\n", TS_SERVE_SEQ=str(seq), OB_CONF_READONLY="")
    assert r.auth == "true", r.stderr
    assert conf("WEBUI_AUTH=false\n").auth == "true"   # the "no" was not cached
    seq.write_text(f"{empty}\n{empty}\n")         # confirmed: really unmounted
    r = conf("WEBUI_AUTH=false\n", TS_SERVE_SEQ=str(seq), OB_CONF_READONLY="")
    assert r.auth == "false", r.stderr
    assert conf("WEBUI_AUTH=false\n").auth == "false"


def test_a_readonly_source_clears_a_stale_mounted_cache(conf, tmp_path):
    """After :443 is unmounted, read-only sources (doctor, engine.sh,
    start.sh --status) could not clear the cached "mounted", so every one
    of them slept the 2 s recheck on every source until a start ran."""
    conf("WEBUI_AUTH=false\n", TS_SERVE=_serve("http://127.0.0.1:3000"),
         OB_CONF_READONLY="")
    cache = tmp_path / "run" / "webui-on-443"
    assert cache.read_text().strip() == "0"
    empty = json.dumps({})
    seq = tmp_path / "serve-seq"
    seq.write_text(f"{empty}\n{empty}\n")          # confirmed: unmounted
    r = conf("WEBUI_AUTH=false\n", TS_SERVE_SEQ=str(seq))   # read-only source
    assert r.auth == "false", r.stderr
    assert cache.read_text().strip() == "1"
    # the next read-only source believes one "no" at once (no recheck)
    seq.write_text(f"{empty}\n")
    assert conf("WEBUI_AUTH=false\n", TS_SERVE_SEQ=str(seq)).auth == "false"
    assert seq.read_text() == "", "a second probe ran: the recheck was not skipped"


def test_a_readonly_source_still_creates_no_mount_cache(conf, tmp_path):
    conf("WEBUI_AUTH=false\n", TS_SERVE=_serve("http://127.0.0.1:3000"))
    assert not (tmp_path / "run" / "webui-on-443").exists()


@pytest.mark.parametrize("serve", [_serve("http://127.0.0.1:3004"),
                                   _serve("http://127.0.0.1:3000", port=8446), "", "not json"])
def test_other_mounts_do_not_force_login(conf, serve):
    r = conf("WEBUI_AUTH=false\n", TS_SERVE=serve)
    assert r.auth == "false"


# --- Host allowlist (DNS rebinding) ------------------------------------------

def test_auth_off_answers_loopback_hosts_only(conf):
    r = conf(ts=False)
    assert set(r.hosts.split(",")) == {"localhost", "127.0.0.1", "::1"}, r.hosts
    r = conf(ts=False, OPENBEAST_BIND="127.0.0.2")
    assert "127.0.0.2" in r.hosts.split(","), r.hosts


def test_auth_off_adds_operator_origin_hosts(conf):
    r = conf("WEBUI_ORIGINS=http://webui.lan:81;http://[fd00::9]:80\n", ts=False)
    assert {"webui.lan", "[fd00::9]", "localhost"} <= set(r.hosts.split(",")), r.hosts


@pytest.mark.parametrize("kw", [dict(OPENBEAST_WEBUI_AUTH="true"),
                                dict(OPENBEAST_WEBUI_ORIGINS="*"),
                                dict(OPENBEAST_BIND="192.168.1.20")])   # forces login on
def test_no_host_check_when_login_is_on_or_open_is_acknowledged(conf, kw):
    assert conf(ts=False, **kw).hosts == "*"


def test_an_acknowledged_open_webui_still_pins_host(conf):
    """ALLOW_OPEN_WEBUI=true lets the network in with login off; it used to
    drop the Host check too, so a page the operator opened could rebind its
    name to the WebUI's address and drive it as admin."""
    r = conf("ALLOW_OPEN_WEBUI=true\n", ts=False)
    assert r.auth == "false" and r.hosts != "*", r.hosts
    assert set(r.hosts.split(",")) == {"localhost", "127.0.0.1", "::1"}, r.hosts
    r = conf("ALLOW_OPEN_WEBUI=true\n", TS_DNS="beast.example.ts.net.",
             OPENBEAST_BIND="192.168.1.20")
    assert r.auth == "false", r.stderr
    hosts = set(r.hosts.split(","))
    assert {"192.168.1.20", "beast", "beast.example.ts.net", "localhost"} <= hosts, r.hosts
    assert "evil.example" not in hosts
    r = conf("ALLOW_OPEN_WEBUI=true\nWEBUI_ORIGINS=http://webui.lan:81\n", ts=False)
    assert "webui.lan" in r.hosts.split(","), r.hosts


def test_compose_wires_the_host_guard():
    text = open(os.path.join(REPO, "docker-compose.yml")).read()
    assert "- OB_WEBUI_HOSTS=${OPENBEAST_WEBUI_HOSTS:-localhost,127.0.0.1,::1}" in text
    assert "- PYTHONPATH=/opt/openbeast-webui" in text
    assert "./scripts/webui/sitecustomize.py:/opt/openbeast-webui/sitecustomize.py:ro" in text
    assert "./agents/hostpolicy.py:/opt/openbeast-webui/hostpolicy.py:ro" in text


GUARD_APP = textwrap.dedent(r'''
    import sys, json
    import sitecustomize  # site already ran it, from PYTHONPATH, as in the container
    assert sitecustomize.__file__.startswith(sys.argv[1]), sitecustomize.__file__
    from fastapi import FastAPI, WebSocket
    from fastapi.testclient import TestClient
    app = FastAPI()
    sub = FastAPI()
    @app.post("/api/v1/auths/signin")
    def signin():
        return {"token": "admin-token"}
    @sub.get("/x")
    def x():
        return {"ok": 1}
    app.mount("/sub", sub)
    @app.websocket("/ws")
    async def ws(w: WebSocket):
        await w.accept(); await w.send_text("hi"); await w.close()
    out = {}
    c = TestClient(app)
    for host in ["localhost:3000", "127.0.0.1:3000", "[::1]:3000", "evil.example:3000",
                 "EVIL.example", "localhost.evil.example:3000", "beast.example.ts.net",
                 "rig.lan:3000"]:
        out[host] = c.post("/api/v1/auths/signin", headers={"host": host}).status_code
    out["sub-evil"] = c.get("/sub/x", headers={"host": "evil.example"}).status_code
    out["sub-ok"] = c.get("/sub/x", headers={"host": "localhost"}).status_code
    def wsok(host):
        try:
            with c.websocket_connect("/ws", headers={"host": host}) as w:
                return w.receive_text() == "hi"
        except Exception:
            return False
    out["ws-ok"], out["ws-evil"] = wsok("localhost:3000"), wsok("evil.example:3000")
    print(json.dumps(out))
''')


def _guard(tmp_path, hosts):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    script = tmp_path / "guard_app.py"
    script.write_text(GUARD_APP)
    import sys as _sys
    # PYTHONPATH, exactly as docker-compose.yml hands it to the image's python
    env = dict(os.environ, OB_WEBUI_HOSTS=hosts, PYTHONPATH=os.pathsep.join([os.path.join(REPO, "scripts", "webui"),
                                            os.path.join(REPO, "agents")]))
    r = subprocess.run([_sys.executable, str(script), os.path.join(REPO, "scripts", "webui")],
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_host_guard_refuses_a_rebound_page(tmp_path):
    """DNS rebinding: evil.example re-resolves to 127.0.0.1, so the browser
    sends the auth-off sign-in as same-origin — no CORS involved — but its
    Host header still says evil.example."""
    out = _guard(tmp_path, "localhost,127.0.0.1,::1,rig.lan")
    assert out["localhost:3000"] == out["127.0.0.1:3000"] == out["[::1]:3000"] == 200, out
    assert out["evil.example:3000"] == out["EVIL.example"] == 400, out
    assert out["localhost.evil.example:3000"] == 400, out
    assert out["beast.example.ts.net"] == 200, out      # hostpolicy: *.ts.net
    assert out["rig.lan:3000"] == 200, out              # an OB_WEBUI_HOSTS extra
    assert out["sub-evil"] == 400 and out["sub-ok"] == 200, out
    assert out["ws-ok"] is True and out["ws-evil"] is False, out


def test_host_guard_wildcard_is_off(tmp_path):
    out = _guard(tmp_path, "*")
    assert out["evil.example:3000"] == 200 and out["ws-evil"] is True, out
