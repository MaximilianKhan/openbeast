#!/usr/bin/env python3
"""configure-webui.sh + setup-tailscale.sh — the WebUI login boundary.

Open WebUI's signin, with WEBUI_AUTH off, creates admin@localhost as ADMIN
with the hardcoded password "admin" (open_webui/routers/auths.py), and
configure-webui.sh signs in that way on every auth-off start. When
setup-tailscale.sh later turns auth on and publishes :443, the account
survived with the well-known password: any tailnet device could take admin
and, through the privileged tool connection, bash (review network-exposure-1).

Every test builds its own rig in tmp_path: a copy of the scripts, a private
openbeast.conf, and stub `curl`/`docker`/`tailscale`/`sudo`/`systemctl`
binaries. The curl stub emulates the handful of Open WebUI endpoints the
scripts use, from a JSON state file, and logs every call — so nothing here
touches a real WebUI, container or tailnet.

Run: pytest tests/test_webui_default_admin.py
"""

import json
import os
import shutil
import stat
import subprocess
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CURL_STUB = textwrap.dedent(r'''
    #!/usr/bin/env python3
    """Stub curl: a tiny Open WebUI, driven by $STUB_STATE, logging to $STUB_LOG."""
    import json, os, sys
    args = sys.argv[1:]
    url, method, data, headers = None, None, None, {}
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-X", "-d", "-H", "-m", "-o", "-w", "--max-time"):
            v = args[i + 1]; i += 2
            if a == "-X": method = v
            elif a == "-d": data = sys.stdin.read() if v == "@-" else v
            elif a == "-H":
                k, _, val = v.partition(":"); headers[k.strip().lower()] = val.strip()
            continue
        if a.startswith("http"): url = a
        i += 1
    method = method or ("POST" if data is not None else "GET")
    st = json.load(open(os.environ["STUB_STATE"]))
    path = url.split("://", 1)[1].split("/", 1)[1] if url else ""
    path = "/" + path
    body = json.loads(data) if data else None
    with open(os.environ["STUB_LOG"], "a") as f:
        f.write(json.dumps({"method": method, "path": path, "body": body,
                            "auth": headers.get("authorization")}) + "\n")
    def out(o):
        sys.stdout.write(json.dumps(o)); sys.exit(0)
    def save():
        json.dump(st, open(os.environ["STUB_STATE"], "w"))
    if st.get("down"):
        sys.exit(7)
    tok = lambda email: "tok-%s-%d" % (email, st["gen"])
    if path == "/api/version": out({"version": "stub"})
    if path == "/api/config": out({"features": {"auth": st["auth"]}})
    if path == "/api/v1/auths/signin":
        email, pw = body["email"], body["password"]
        if email == "admin@localhost" and not st["auth"]:
            pw = "admin"   # upstream's auth-off branch uses its own constant
        if st["accounts"].get(email) == pw:
            out({"token": tok(email)})
        out({"detail": "The email or password provided is incorrect."})
    if path == "/api/v1/auths/update/password":
        if st.get("fail_update"):
            out({"detail": "nope"})
        who = [e for e in st["accounts"] if headers.get("authorization") == "Bearer " + tok(e)]
        if not who or st["accounts"][who[0]] != body["password"]:
            out({"detail": "Incorrect password"})
        st["accounts"][who[0]] = body["new_password"]; st["gen"] += 1; save()
        out(True)
    if path == "/api/v1/configs/tool_servers":
        out({"TOOL_SERVER_CONNECTIONS": []} if method == "GET" else {})
    if path == "/api/models": out({"data": [{"id": "m1"}]})
    out({})
''').lstrip()

# docker: every exec "fails" (no container) — the scripts degrade on that.
DOCKER_STUB = "#!/bin/sh\nexit 1\n"


def _write_exec(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, 0o755)


@pytest.fixture()
def rig(tmp_path):
    root = tmp_path / "rig"
    (root / "scripts" / "lib").mkdir(parents=True)
    for rel in ("scripts/configure-webui.sh", "scripts/setup-tailscale.sh",
                "scripts/lib/conf.sh"):
        shutil.copy2(os.path.join(REPO, rel), root / rel)
    conf = root / "openbeast.conf"
    conf.write_text("SEARXNG_SECRET=stub\n")
    os.chmod(conf, 0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exec(bin_dir / "curl", CURL_STUB)
    _write_exec(bin_dir / "docker", DOCKER_STUB)
    state = tmp_path / "state.json"
    log = tmp_path / "calls.jsonl"
    log.write_text("")

    class Rig:
        pass

    r = Rig()
    r.root, r.conf, r.bin, r.state, r.log, r.tmp = root, conf, bin_dir, state, log, tmp_path

    def set_state(auth=True, admin_pw="admin", **extra):
        st = {"auth": auth, "gen": 0, "accounts": {"admin@localhost": admin_pw}}
        st.update(extra)
        state.write_text(json.dumps(st))
    r.set_state = set_state

    def get_state():
        return json.loads(state.read_text())
    r.get_state = get_state

    def calls():
        return [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    r.calls = calls

    def run(script, *args, env_extra=None):
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path),
               "STUB_STATE": str(state), "STUB_LOG": str(log)}
        env.update(env_extra or {})
        return subprocess.run(["bash", str(root / "scripts" / script), *args],
                              env=env, capture_output=True, text=True,
                              timeout=120)
    r.run = run

    def conf_values():
        out = {}
        for ln in conf.read_text().splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, _, v = ln.partition("=")
                out[k.strip()] = v.strip()
        return out
    r.conf_values = conf_values
    return r


# --- configure-webui.sh --secure-default-admin ------------------------------

def test_default_password_is_rotated_and_saved(rig):
    rig.set_state(auth=True, admin_pw="admin")
    p = rig.run("configure-webui.sh", "--secure-default-admin")
    assert p.returncode == 0, p.stderr
    new_pw = rig.get_state()["accounts"]["admin@localhost"]
    assert new_pw != "admin" and len(new_pw) >= 24
    conf = rig.conf_values()
    assert conf["WEBUI_ADMIN_EMAIL"] == "admin@localhost"
    assert conf["WEBUI_ADMIN_PASSWORD"] == new_pw
    assert stat.S_IMODE(os.stat(rig.conf).st_mode) == 0o600
    assert conf["SEARXNG_SECRET"] == "stub"          # the rest of the conf kept
    assert "SECURITY" in p.stderr
    # The new secret is never printed.
    assert new_pw not in p.stdout and new_pw not in p.stderr
    # ...and the default no longer signs in.
    assert rig.get_state()["gen"] == 1


def test_already_rotated_is_left_alone(rig):
    rig.set_state(auth=True, admin_pw="operator-chose-this")
    before = rig.conf.read_text()
    p = rig.run("configure-webui.sh", "--secure-default-admin")
    assert p.returncode == 0, p.stderr
    assert rig.get_state()["accounts"]["admin@localhost"] == "operator-chose-this"
    assert not [c for c in rig.calls() if c["path"].endswith("/update/password")]
    assert rig.conf.read_text() == before


def test_auth_off_live_never_rotates(rig):
    """Negative control: with the RUNNING WebUI auth-off, upstream checks the
    stored hash against its own default at every signin — rotating now would
    break the live UI. Nothing may be signed in or changed."""
    rig.set_state(auth=False, admin_pw="admin")
    p = rig.run("configure-webui.sh", "--secure-default-admin")
    assert p.returncode == 0, p.stderr
    assert rig.get_state()["accounts"]["admin@localhost"] == "admin"
    assert not [c for c in rig.calls() if "/auths/" in c["path"]]


def test_failed_rotation_is_loud_and_nonzero(rig):
    rig.set_state(auth=True, admin_pw="admin", fail_update=True)
    before = rig.conf.read_text()
    p = rig.run("configure-webui.sh", "--secure-default-admin")
    assert p.returncode == 1
    assert "SECURITY WARNING" in p.stderr
    assert rig.conf.read_text() == before


def test_a_different_configured_admin_is_not_clobbered(rig):
    rig.set_state(auth=True, admin_pw="admin")
    rig.conf.write_text("SEARXNG_SECRET=stub\nWEBUI_ADMIN_EMAIL=max@example.com\n"
                        "WEBUI_ADMIN_PASSWORD=maxs-own\n")
    p = rig.run("configure-webui.sh", "--secure-default-admin")
    assert p.returncode == 0, p.stderr
    conf = rig.conf_values()
    assert conf["WEBUI_ADMIN_EMAIL"] == "max@example.com"
    assert conf["WEBUI_ADMIN_PASSWORD"] == "maxs-own"
    assert conf["WEBUI_DEFAULT_ADMIN_PASSWORD"] == \
        rig.get_state()["accounts"]["admin@localhost"] != "admin"


# --- configure-webui.sh full run ---------------------------------------------

def _tool_server_post(rig):
    posts = [c for c in rig.calls()
             if c["path"] == "/api/v1/configs/tool_servers" and c["method"] == "POST"]
    assert posts, "tool-server reconciliation never ran"
    return posts[-1]


def test_full_run_with_auth_on_rotates_then_configures(rig):
    """The normal start: auth on, default password still live. The default is
    retired BEFORE anything else, and the rest of the run signs in with the
    rotated credentials (so the tool servers still get reconciled)."""
    rig.set_state(auth=True, admin_pw="admin")
    p = rig.run("configure-webui.sh")
    assert p.returncode == 0, p.stderr
    assert rig.get_state()["accounts"]["admin@localhost"] != "admin"
    post = _tool_server_post(rig)
    assert post["auth"] == "Bearer tok-admin@localhost-1"


def test_single_admin_key_still_reaches_admin_tools(rig):
    """identity-rbac-7: the server treats EITHER key as keyed; the WebUI
    connections required BOTH, so one key meant no key sent anywhere and
    every tool call 401'd. Each connection now carries its own key."""
    rig.set_state(auth=False, admin_pw="admin")
    p = rig.run("configure-webui.sh",
                env_extra={"OPENBEAST_MCPO_ADMIN_KEY": "adm-key"})
    assert p.returncode == 0, p.stderr
    conns = {c["info"]["id"]: c for c in
             _tool_server_post(rig)["body"]["TOOL_SERVER_CONNECTIONS"]}
    assert conns["1"]["auth_type"] == "bearer" and conns["1"]["key"] == "adm-key"
    assert conns["2"]["auth_type"] == "none" and conns["2"]["key"] == ""
    assert "ONE profile key" in p.stderr


def test_both_keys_bind_both_connections(rig):
    """Negative control for the above: the two-key shape is unchanged."""
    rig.set_state(auth=False, admin_pw="admin")
    p = rig.run("configure-webui.sh",
                env_extra={"OPENBEAST_MCPO_ADMIN_KEY": "adm-key",
                           "OPENBEAST_MCPO_GUEST_KEY": "gst-key"})
    assert p.returncode == 0, p.stderr
    conns = {c["info"]["id"]: c for c in
             _tool_server_post(rig)["body"]["TOOL_SERVER_CONNECTIONS"]}
    assert conns["1"]["key"] == "adm-key" and conns["2"]["key"] == "gst-key"
    assert conns["2"]["auth_type"] == "bearer"


def test_no_keys_leaves_connections_open(rig):
    rig.set_state(auth=False, admin_pw="admin")
    p = rig.run("configure-webui.sh")
    assert p.returncode == 0, p.stderr
    conns = _tool_server_post(rig)["body"]["TOOL_SERVER_CONNECTIONS"]
    assert all(c["auth_type"] == "none" and c["key"] == "" for c in conns)


# --- setup-tailscale.sh: never publish an open or default-password WebUI -----

TAILSCALE_STUB = textwrap.dedent(r'''
    #!/usr/bin/env python3
    import json, os, sys
    args = sys.argv[1:]
    with open(os.environ["STUB_LOG"], "a") as f:
        f.write(json.dumps({"tailscale": args}) + "\n")
    if args[:2] == ["status", "--json"]:
        print(json.dumps({"Self": {"DNSName": "beast.example.ts.net."},
                          "CertDomains": ["beast.example.ts.net"]}))
''').lstrip()


@pytest.fixture()
def ts_rig(rig):
    _write_exec(rig.bin / "tailscale", TAILSCALE_STUB)
    _write_exec(rig.bin / "sudo", '#!/bin/sh\nexec "$@"\n')
    _write_exec(rig.bin / "systemctl", "#!/bin/sh\nexit 0\n")
    return rig


def _events(rig):
    """Every stub call in order: ('ts', args) or ('http', path)."""
    out = []
    for c in rig.calls():
        if "tailscale" in c:
            out.append(("ts", c["tailscale"]))
        else:
            out.append(("http", c["path"]))
    return out


def _mounted_443(rig):
    return any(k == "ts" and "--https=443" in a and "off" not in a
               and "serve" in a for k, a in _events(rig))


def test_tailscale_rotates_default_admin_before_publishing(ts_rig):
    ts_rig.set_state(auth=True, admin_pw="admin")
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert ts_rig.conf_values()["WEBUI_AUTH"] == "true"
    assert ts_rig.get_state()["accounts"]["admin@localhost"] != "admin"
    ev = _events(ts_rig)
    rotate = next(i for i, e in enumerate(ev) if e == ("http", "/api/v1/auths/update/password"))
    mount = next(i for i, (k, a) in enumerate(ev)
                 if k == "ts" and "--https=443" in a and "off" not in a)
    assert rotate < mount, "the WebUI went tailnet-wide before its default admin was retired"


def test_tailscale_refuses_443_while_running_webui_has_auth_off(ts_rig):
    """The container predates this run: conf says auth on, the live WebUI does
    not. Publishing now = every tailnet device is admin until a restart."""
    ts_rig.set_state(auth=False, admin_pw="admin")
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert not _mounted_443(ts_rig)
    assert "NOT publishing the WebUI" in p.stderr and "Restart the stack" in p.stderr
    assert ts_rig.conf_values()["WEBUI_AUTH"] == "true"      # persisted for the restart
    # Everything else still publishes (negative control for over-blocking).
    assert any(k == "ts" and "--https=8443" in a for k, a in _events(ts_rig))
    assert ts_rig.get_state()["accounts"]["admin@localhost"] == "admin"


def test_tailscale_refuses_explicit_auth_off_without_the_flag(ts_rig):
    ts_rig.set_state(auth=False, admin_pw="admin")
    ts_rig.conf.write_text("SEARXNG_SECRET=stub\nWEBUI_AUTH=false\n")
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert not _mounted_443(ts_rig)
    assert "--i-accept-open-webui" in p.stderr
    # The explicit choice is left alone, not silently overridden.
    assert ts_rig.conf_values()["WEBUI_AUTH"] == "false"


def test_tailscale_explicit_auth_off_with_the_flag_publishes(ts_rig):
    ts_rig.set_state(auth=False, admin_pw="admin")
    ts_rig.conf.write_text("SEARXNG_SECRET=stub\nWEBUI_AUTH=false\n")
    p = ts_rig.run("setup-tailscale.sh", "--i-accept-open-webui")
    assert p.returncode == 0, p.stderr
    assert _mounted_443(ts_rig)
    assert "NO login" in p.stdout


def test_tailscale_publishes_when_webui_not_running(ts_rig):
    """WebUI down: it will start from the conf (auth on) and start.sh's
    configure-webui.sh retires the default password then."""
    ts_rig.set_state(auth=True, admin_pw="admin", down=True)
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert _mounted_443(ts_rig)
    assert ts_rig.conf_values()["WEBUI_AUTH"] == "true"
