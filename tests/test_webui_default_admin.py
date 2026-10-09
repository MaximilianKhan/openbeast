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
        if a in ("-X", "-d", "-H", "-m", "-o", "-w", "--max-time", "--config", "-K"):
            v = args[i + 1]; i += 2
            if a == "-X": method = v
            elif a == "-d": data = sys.stdin.read() if v == "@-" else v
            elif a == "-H":
                k, _, val = v.partition(":"); headers[k.strip().lower()] = val.strip()
            elif a in ("--config", "-K"):
                # lib/curl_auth.sh hands credential headers over as curl config
                # lines on an fd (`header = "K: v"`), never on argv.
                for ln in open(v).read().splitlines():
                    ln = ln.strip()
                    if ln.startswith("header") and "=" in ln:
                        hv = ln.split("=", 1)[1].strip()
                        if hv.startswith('"') and hv.endswith('"'):
                            hv = hv[1:-1].replace('\\"', '"').replace('\\\\', '\\')
                        k, _, val = hv.partition(":"); headers[k.strip().lower()] = val.strip()
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
                            "auth": headers.get("authorization"),
                            "url": url,
                            # a credential on argv is world-readable in /proc
                            "argv_bearer": any("Bearer" in a for a in args)}) + "\n")
    def out(o):
        sys.stdout.write(json.dumps(o)); sys.exit(0)
    def save():
        json.dump(st, open(os.environ["STUB_STATE"], "w"))
    if st.get("down"):
        sys.exit(7)
    if st.get("down_calls", 0) > 0:      # booting: the first N calls fail
        st["down_calls"] -= 1; save(); sys.exit(7)
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
    if path in ("/api/models", "/v1/models"): out({"data": [{"id": "m1"}]})
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
                "scripts/lib/conf.sh", "scripts/lib/net.sh",
                "scripts/lib/curl_auth.sh"):
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


def _mounts_8443(rig):
    """Upstreams `tailscale serve` was asked to mount on :8443, in order."""
    return [a[-1] for k, a in _events(rig)
            if k == "ts" and "serve" in a and "--https=8443" in a and "off" not in a]


def _mounted_8443(rig):
    return bool(_mounts_8443(rig))


def _unmounted_8443(rig):
    return any(k == "ts" and "--https=8443" in a and "off" in a
               for k, a in _events(rig))


# --- inference (:8443): never publish a keyless, ungated llama-server --------
# 2026-10-09 review, netsec S11. The WebUI needed --i-accept-open-webui to go
# out open; raw llama-server needed nothing and was the default path.

def test_tailscale_refuses_keyless_ungated_inference(ts_rig):
    ts_rig.set_state(auth=True, admin_pw="rotated-already")
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert not _mounted_8443(ts_rig)
    assert _unmounted_8443(ts_rig), "an earlier raw mount must be taken down"
    assert "NOT publishing inference (:8443)" in p.stderr
    # All three ways forward are named.
    for way in ("EDGE_GATE=true", "LLAMA_API_KEY=", "--i-accept-open-inference"):
        assert way in p.stderr, way
    assert "https://beast.example.ts.net:8443/v1" not in p.stdout
    assert "API (OpenAI-compat): NOT published" in p.stdout
    assert "ALLOW_OPEN_INFERENCE" not in ts_rig.conf.read_text()
    # Negative control for over-blocking: the WebUI still goes out.
    assert _mounted_443(ts_rig)


def test_tailscale_publishes_inference_behind_the_gate(ts_rig):
    ts_rig.set_state(auth=True, admin_pw="rotated-already")
    ts_rig.conf.write_text("SEARXNG_SECRET=stub\nEDGE_GATE=true\n")
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert _mounts_8443(ts_rig) == ["http://127.0.0.1:8090"]
    assert "NOT publishing inference" not in p.stderr
    assert "ALLOW_OPEN_INFERENCE" not in ts_rig.conf.read_text()


def test_tailscale_publishes_inference_behind_a_shared_key(ts_rig):
    ts_rig.set_state(auth=True, admin_pw="rotated-already")
    ts_rig.conf.write_text("SEARXNG_SECRET=stub\nLLAMA_API_KEY=sekrit\n")
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert _mounts_8443(ts_rig) == ["http://127.0.0.1:8080"]
    assert "NOT publishing inference" not in p.stderr
    assert "sekrit" not in p.stdout + p.stderr


def test_tailscale_empty_key_is_not_a_key(ts_rig):
    ts_rig.set_state(auth=True, admin_pw="rotated-already")
    ts_rig.conf.write_text("SEARXNG_SECRET=stub\nLLAMA_API_KEY=\nEDGE_GATE=false\n")
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert not _mounted_8443(ts_rig)
    assert "NOT publishing inference" in p.stderr


def test_tailscale_open_inference_flag_publishes_and_is_persisted(ts_rig):
    """Mirrors --i-accept-open-webui / ALLOW_OPEN_WEBUI: the flag publishes,
    records one assignment in a 0600 conf, and a re-run without it honours
    the record."""
    ts_rig.set_state(auth=True, admin_pw="rotated-already")
    ts_rig.conf.write_text("SEARXNG_SECRET=stub\nALLOW_OPEN_INFERENCE=false\n")
    ts_rig.conf.chmod(0o600)
    p = ts_rig.run("setup-tailscale.sh", "--i-accept-open-inference")
    assert p.returncode == 0, p.stderr
    assert _mounts_8443(ts_rig) == ["http://127.0.0.1:8080"]
    assert "NO key" in p.stdout
    text = ts_rig.conf.read_text()
    assert ts_rig.conf_values()["ALLOW_OPEN_INFERENCE"] == "true"
    assert text.count("ALLOW_OPEN_INFERENCE=") == 1, text
    assert "SEARXNG_SECRET=stub" in text
    assert (ts_rig.conf.stat().st_mode & 0o777) == 0o600
    assert not list(ts_rig.conf.parent.glob(ts_rig.conf.name + ".*")), "temp file left"
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert "NOT publishing inference" not in p.stderr
    assert len(_mounts_8443(ts_rig)) == 2
    assert ts_rig.conf.read_text().count("ALLOW_OPEN_INFERENCE=") == 1


def test_tailscale_does_not_persist_inference_ack_when_gated(ts_rig):
    """Control: with the gate on the flag is moot — nothing is recorded."""
    ts_rig.set_state(auth=True, admin_pw="rotated-already")
    ts_rig.conf.write_text("SEARXNG_SECRET=stub\nEDGE_GATE=true\n")
    p = ts_rig.run("setup-tailscale.sh", "--i-accept-open-inference")
    assert p.returncode == 0, p.stderr
    assert _mounts_8443(ts_rig) == ["http://127.0.0.1:8090"]
    assert "ALLOW_OPEN_INFERENCE" not in ts_rig.conf.read_text()


@pytest.mark.parametrize("pm,hint", [("apt-get", "sudo apt-get install tailscale"),
                                     ("dnf", "sudo dnf install tailscale"),
                                     (None, "https://tailscale.com/download")])
def test_tailscale_missing_prints_steps_and_never_pipes_an_installer(rig, pm, hint):
    """2026-10-09 review, supply S9: on apt/dnf systems the script ran
    `curl https://tailscale.com/install.sh | sh` — an unverified remote
    script, as root. It now prints the signed-repo steps and stops.

    PATH holds ONLY stubs (no /usr/bin): this box may have a real tailscale
    or pacman, and neither may be reached. Everything the script does before
    the install step is a bash builtin."""
    only = rig.tmp / "onlybin"
    only.mkdir()
    rec = rig.tmp / "ran.log"
    stub = f'#!/bin/bash\necho "$(basename "$0") $*" >> "{rec}"\nexit 0\n'
    for name in ("curl", "sudo", "sh", "systemctl") + ((pm,) if pm else ()):
        _write_exec(only / name, stub)
    p = subprocess.run(["/bin/bash", str(rig.root / "scripts" / "setup-tailscale.sh")],
                       env={"PATH": str(only), "HOME": str(rig.tmp)},
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "does not pipe a" in p.stderr and hint in p.stderr
    assert "re-run this script" in p.stderr
    assert not rec.exists(), f"something was executed: {rec.read_text()}"


def test_tailscale_help_prints_the_whole_header(ts_rig):
    p = ts_rig.run("setup-tailscale.sh", "--help")
    assert p.returncode == 0
    assert "--i-accept-open-inference" in p.stdout
    assert "docs/REMOTE_ACCESS_PLAN.md" in p.stdout      # the header's last line
    assert "set -euo pipefail" not in p.stdout
    assert _events(ts_rig) == []


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
    # Gated, so inference is publishable — it is the control below.
    p = ts_rig.run("setup-tailscale.sh", env_extra={"OPENBEAST_EDGE_GATE": "true"})
    assert p.returncode == 0, p.stderr
    assert not _mounted_443(ts_rig)
    assert "NOT publishing the WebUI" in p.stderr and "Restart the stack" in p.stderr
    assert ts_rig.conf_values()["WEBUI_AUTH"] == "true"      # persisted for the restart
    # Everything else still publishes (negative control for over-blocking).
    assert _mounted_8443(ts_rig)
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


def test_tailscale_persists_the_open_webui_acknowledgement(ts_rig):
    """review r2: --i-accept-open-webui had no persisted form, so doctor FAILed
    a deliberately open :443 on every run, forever. The flag now records
    ALLOW_OPEN_WEBUI=true (0600 conf, other lines kept, one assignment), and a
    re-run without the flag honours it."""
    ts_rig.set_state(auth=False, admin_pw="admin")
    ts_rig.conf.write_text("SEARXNG_SECRET=stub\nWEBUI_AUTH=false\n"
                           "ALLOW_OPEN_WEBUI=false\n")
    ts_rig.conf.chmod(0o600)
    p = ts_rig.run("setup-tailscale.sh", "--i-accept-open-webui")
    assert p.returncode == 0, p.stderr
    assert _mounted_443(ts_rig)
    text = ts_rig.conf.read_text()
    assert ts_rig.conf_values()["ALLOW_OPEN_WEBUI"] == "true"
    assert text.count("ALLOW_OPEN_WEBUI=") == 1, text
    assert "SEARXNG_SECRET=stub" in text and "WEBUI_AUTH=false" in text
    assert (ts_rig.conf.stat().st_mode & 0o777) == 0o600
    assert not list(ts_rig.conf.parent.glob(ts_rig.conf.name + ".*")), "temp file left"
    # Re-run WITHOUT the flag: the recorded acknowledgement counts as given.
    mounts_before = sum(1 for k, a in _events(ts_rig)
                        if k == "ts" and "--https=443" in a and "off" not in a)
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert "NOT publishing the WebUI" not in p.stderr
    assert sum(1 for k, a in _events(ts_rig)
               if k == "ts" and "--https=443" in a and "off" not in a) == mounts_before + 1
    assert ts_rig.conf.read_text().count("ALLOW_OPEN_WEBUI=") == 1


def test_tailscale_does_not_persist_acknowledgement_when_auth_is_on(ts_rig):
    """Control: with login enforced the flag is moot — nothing is recorded."""
    ts_rig.set_state(auth=True, admin_pw="admin")
    p = ts_rig.run("setup-tailscale.sh", "--i-accept-open-webui")
    assert p.returncode == 0, p.stderr
    assert _mounted_443(ts_rig)
    assert "ALLOW_OPEN_WEBUI" not in ts_rig.conf.read_text()


def _docker(rig, inspect=None, err="Error: No such object: open-webui"):
    """docker stub for `docker inspect open-webui`: prints INSPECT (container
    exists) or fails with ERR (no container / daemon trouble)."""
    if inspect is not None:
        body = "#!/bin/sh\ncat <<'X'\n%s\nX\n" % inspect
    else:
        body = "#!/bin/sh\necho '%s' >&2\nexit 1\n" % err
    _write_exec(rig.bin / "docker", body)


def test_tailscale_publishes_when_webui_not_running(ts_rig):
    """WebUI down and no container: it will start from the conf (auth on) and
    start.sh's configure-webui.sh retires the default password then."""
    _docker(ts_rig)
    ts_rig.set_state(auth=True, admin_pw="admin", down=True)
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert _mounted_443(ts_rig)
    assert ts_rig.conf_values()["WEBUI_AUTH"] == "true"


@pytest.mark.parametrize("status", ["running", "created", "exited"])
def test_tailscale_refuses_443_for_unanswering_auth_off_container(ts_rig, status):
    """The race the review found: /api/config does not answer yet (first boot,
    or start.sh just returned) but the container exists with WEBUI_AUTH=false.
    It would finish booting auth-off behind a live :443 — every tailnet
    device admin, with bash — and keep that env across daemon restarts."""
    _docker(ts_rig, inspect=f"{status} PATH=/usr/bin WEBUI_AUTH=false PORT=8080")
    ts_rig.set_state(auth=True, admin_pw="admin", down=True)
    p = ts_rig.run("setup-tailscale.sh", env_extra={"WEBUI_WAIT_S": "0"})
    assert p.returncode == 0, p.stderr
    assert not _mounted_443(ts_rig)
    assert "NOT publishing the WebUI" in p.stderr and "auth OFF" in p.stderr
    assert any(k == "ts" and "--https=8443" in a for k, a in _events(ts_rig))


def test_tailscale_publishes_for_stopped_auth_on_container(ts_rig):
    """Negative control: a stopped container created auth-on restarts auth-on."""
    _docker(ts_rig, inspect="exited PATH=/usr/bin WEBUI_AUTH=true")
    ts_rig.set_state(auth=True, admin_pw="admin", down=True)
    p = ts_rig.run("setup-tailscale.sh", env_extra={"WEBUI_WAIT_S": "0"})
    assert p.returncode == 0, p.stderr
    assert _mounted_443(ts_rig), p.stdout + p.stderr


def test_tailscale_refuses_443_when_docker_cannot_be_asked(ts_rig):
    """Can't tell 'nothing running' from 'auth-off container booting' → block."""
    _docker(ts_rig, err="permission denied while trying to connect to the Docker daemon")
    ts_rig.set_state(auth=True, admin_pw="admin", down=True)
    p = ts_rig.run("setup-tailscale.sh")
    assert p.returncode == 0, p.stderr
    assert not _mounted_443(ts_rig)
    assert "docker could not be asked" in p.stderr


def test_tailscale_waits_for_a_booting_container_then_rotates(ts_rig):
    """A running auth-on container that answers after a moment is waited for,
    and its default admin is retired before :443 goes up."""
    _docker(ts_rig, inspect="running WEBUI_AUTH=true")
    ts_rig.set_state(auth=True, admin_pw="admin", down_calls=1)
    p = ts_rig.run("setup-tailscale.sh", env_extra={"WEBUI_WAIT_S": "20"})
    assert p.returncode == 0, p.stderr
    assert _mounted_443(ts_rig)
    assert ts_rig.get_state()["accounts"]["admin@localhost"] != "admin"


def test_tailscale_refuses_443_when_running_container_never_answers(ts_rig):
    _docker(ts_rig, inspect="running WEBUI_AUTH=true")
    ts_rig.set_state(auth=True, admin_pw="admin", down=True)
    p = ts_rig.run("setup-tailscale.sh", env_extra={"WEBUI_WAIT_S": "0"})
    assert p.returncode == 0, p.stderr
    assert not _mounted_443(ts_rig)
    assert "never answered" in p.stderr


# --- 2026-09-29 round 2: secrets off argv, probe host, doctor probe, --status

def test_admin_jwt_never_rides_curl_argv(rig):
    """secrets-crypto-1 / network-exposure-4: the admin JWT (bash-equivalent)
    and the rotation call's token went to curl as `-H "Authorization: Bearer
    …"`, readable by every local uid in /proc/<pid>/cmdline. They now travel
    as curl --config lines on an fd — and still arrive (negative control)."""
    rig.set_state(auth=True, admin_pw="admin")
    p = rig.run("configure-webui.sh")
    assert p.returncode == 0, p.stderr
    calls = rig.calls()
    assert calls and not [c for c in calls if c["argv_bearer"]], \
        [c["path"] for c in calls if c["argv_bearer"]]
    # The header is still delivered: rotation and reconciliation both worked.
    assert rig.get_state()["accounts"]["admin@localhost"] != "admin"
    assert _tool_server_post(rig)["auth"] == "Bearer tok-admin@localhost-1"


def test_llama_key_fallback_probe_stays_off_argv(rig):
    """No admin token → model ids come from the inference endpoint, with
    LLAMA_API_KEY. That key went on argv too."""
    rig.set_state(auth=True, admin_pw="operator-chose-this")
    p = rig.run("configure-webui.sh",
                env_extra={"OPENBEAST_API_KEY": "llama-sekrit"})
    assert p.returncode == 0, p.stderr
    probes = [c for c in rig.calls() if c["path"].endswith("/v1/models")]
    assert probes, "the no-token fallback never asked the model endpoint"
    assert all(c["auth"] == "Bearer llama-sekrit" and not c["argv_bearer"]
               for c in probes), probes


@pytest.mark.parametrize("bind,host", [("192.168.1.50", "192.168.1.50"),
                                       ("127.0.0.1", "localhost"),
                                       ("0.0.0.0", "localhost")])
def test_configure_webui_dials_the_bind_host(rig, bind, host):
    """lifecycle-6: WebUI, the tool server and SearXNG bind BIND_HOST, and a
    socket bound to a LAN address refuses localhost. Both the script's own
    calls and the tool-server URL it stores in WebUI must use the probe host;
    loopback/wildcard binds keep the historical `localhost` spelling."""
    rig.set_state(auth=False, admin_pw="admin")
    p = rig.run("configure-webui.sh", env_extra={"OPENBEAST_BIND": bind})
    assert p.returncode == 0, p.stderr
    urls = {c["url"] for c in rig.calls()}
    assert f"http://{host}:3000/api/version" in urls, urls
    conns = _tool_server_post(rig)["body"]["TOOL_SERVER_CONNECTIONS"]
    assert {c["url"] for c in conns} == {f"http://{host}:3001"}


@pytest.mark.parametrize("state,rc", [
    (dict(auth=True, admin_pw="admin"), 1),                 # the finding
    (dict(auth=True, admin_pw="operator-chose-this"), 0),   # rotated
    (dict(auth=False, admin_pw="admin"), 4),                # auth off: not probed
    (dict(auth=None, admin_pw="admin"), 4),                 # auth unreported: not probed
    (dict(auth=True, admin_pw="admin", down=True), 3),      # unreachable
])
def test_check_default_admin_is_a_read_only_probe(rig, state, rc):
    """doctor.sh's row (network-exposure-1 follow-up) reuses this probe. It
    must answer, and must never change the password it is testing. Login off
    or unreported is 4 ("not probed"), not 0: doctor turned 0 into a green
    "does not accept the default password" that nothing had checked."""
    rig.set_state(**state)
    before = rig.conf.read_text()
    p = rig.run("configure-webui.sh", "--check-default-admin")
    assert p.returncode == rc, (p.stdout, p.stderr)
    assert rig.get_state()["accounts"]["admin@localhost"] == state["admin_pw"]
    assert not [c for c in rig.calls() if c["path"].endswith("/update/password")]
    assert rig.conf.read_text() == before


SERVE_STATUS_STUB = TAILSCALE_STUB.replace(
    'if args[:2] == ["status", "--json"]:',
    'if args[:2] == ["serve", "status"]:\n'
    '    print("https://beast.example.ts.net (tailnet only)\\n'
    '|-- / proxy http://127.0.0.1:3000\\n\\n'
    'https://beast.example.ts.net:8446 (tailnet only)\\n'
    '|-- / proxy http://127.0.0.1:3004")\n'
    'if args[:2] == ["status", "--json"]:')


def test_tailscale_status_flag_is_read_only(ts_rig):
    """docs-drift-setup-tailscale-status: BEAST_ARTIFACT.md documented
    `--status`; the script rejected it (exit 2), and the obvious workaround —
    a full run — reconfigures serve with sudo and writes WEBUI_AUTH."""
    _write_exec(ts_rig.bin / "tailscale", SERVE_STATUS_STUB)
    ts_rig.set_state(auth=False, admin_pw="admin")
    before = ts_rig.conf.read_text()
    p = ts_rig.run("setup-tailscale.sh", "--status")
    assert p.returncode == 0, p.stderr
    rows = {ln.split()[0]: ln.split()[-1] for ln in p.stdout.splitlines()
            if ln.strip() and ln.split()[0].isdigit()}
    assert rows["443"] == "published" and rows["8446"] == "published", p.stdout
    assert rows["8443"] == "-" and rows["8889"] == "-"
    # Nothing was changed: only `tailscale serve status` ran, no WebUI
    # traffic, conf untouched.
    ts_calls = [a for k, a in _events(ts_rig) if k == "ts"]
    assert ts_calls and all(a[:2] == ["serve", "status"] for a in ts_calls), ts_calls
    assert not [e for e in _events(ts_rig) if e[0] == "http"]
    assert ts_rig.conf.read_text() == before


@pytest.mark.parametrize("bind,host", [("192.168.1.50", "192.168.1.50"),
                                       ("127.0.0.1", "127.0.0.1"),
                                       ("0.0.0.0", "127.0.0.1"),
                                       ("::", "[::1]")])
def test_tailscale_mounts_follow_the_bind_host(ts_rig, bind, host):
    """artifact-3: every mount hard-coded 127.0.0.1, so on a rig bound to a
    specific address :443/:8443/:8446 were 502s. Mounts now dial where the
    service binds; the chat mount follows OPENBEAST_CHAT_BIND (loopback)."""
    ts_rig.set_state(auth=True, admin_pw="operator-chose-this")
    # A shared key, so the raw :8443 -> :8080 mount under test is allowed.
    p = ts_rig.run("setup-tailscale.sh", "--publish-artifact", "--publish-chat",
                   env_extra={"OPENBEAST_BIND": bind, "OPENBEAST_API_KEY": "k"})
    assert p.returncode == 0, p.stderr
    mounts = {}
    for k, a in _events(ts_rig):
        if k == "ts" and "serve" in a and "--bg" in a:
            port = next(x for x in a if x.startswith("--https=")).split("=")[1]
            mounts[port] = a[-1]
    assert mounts["443"] == f"http://{host}:3000", mounts
    assert mounts["8443"] == f"http://{host}:8080"
    assert mounts["8446"] == f"http://{host}:3004"
    assert mounts["8445"] == "http://127.0.0.1:3003"      # chat: its own bind
    # The WebUI auth probe dials the same host.
    assert f"http://{host}:3000/api/config" in {c.get("url") for c in ts_rig.calls()}
    lan = host not in ("127.0.0.1", "[::1]")
    # A LAN bind is honoured through :8446 (the peer is the bind address, on
    # this host), and there is no 'public' visibility to fall back on.
    assert "logins are NOT" not in p.stdout
    assert "public" not in p.stdout
    assert ("honoured through :8446 from this host"
            in " ".join(p.stdout.split())) == lan


def test_tailscale_publish_artifact_warns_without_operators(ts_rig):
    """integration-ops-1: publishing :8446 with neither ARTIFACT_OPERATORS nor
    CHAT_OPERATORS set warns that private pages (owner 'rig') open for nobody
    from a phone; an allowlist or ARTIFACT_ADMINS changes that (controls)."""
    ts_rig.set_state(auth=True, admin_pw="operator-chose-this")
    base = {"OPENBEAST_ARTIFACT_OPERATORS": "", "OPENBEAST_CHAT_OPERATORS": "",
            "OPENBEAST_ARTIFACT_ADMINS": ""}
    p = ts_rig.run("setup-tailscale.sh", "--publish-artifact", env_extra=base)
    assert p.returncode == 0, p.stderr
    out = " ".join(p.stdout.split())
    assert "neither ARTIFACT_OPERATORS nor CHAT_OPERATORS is set" in out
    assert "PRIVATE pages (the default) open for NOBODY" in out
    assert "owner 'local'" not in out
    p = ts_rig.run("setup-tailscale.sh", "--publish-artifact",
                   env_extra=dict(base, OPENBEAST_ARTIFACT_ADMINS="me@example.com"))
    out = " ".join(p.stdout.split())
    assert "neither ARTIFACT_OPERATORS" in out and "open for NOBODY" not in out
    p = ts_rig.run("setup-tailscale.sh", "--publish-artifact",
                   env_extra=dict(base, OPENBEAST_CHAT_OPERATORS="me@example.com"))
    out = " ".join(p.stdout.split())
    assert "neither ARTIFACT_OPERATORS" not in out
    assert "Reads are gated on the tailnet login" in out


def _serve_mounts(rig):
    mounts = {}
    for k, a in _events(rig):
        if k == "ts" and "serve" in a and "--bg" in a:
            port = next(x for x in a if x.startswith("--https=")).split("=")[1]
            mounts[port] = a[-1]
    return mounts


def test_tailscale_publish_ntfy_mounts_loopback(ts_rig):
    """F-O1: --publish-ntfy mounts :8447 at the ntfy extension, which binds
    127.0.0.1 whatever BIND_HOST says (its compose fragment) — so the mount
    dials loopback even on a LAN-bound rig, and it warns while the extension
    is off or CHAT_NOTIFY_URL is empty. --unpublish-ntfy takes it down."""
    ts_rig.set_state(auth=True, admin_pw="operator-chose-this")
    p = ts_rig.run("setup-tailscale.sh", "--publish-ntfy",
                   env_extra={"OPENBEAST_BIND": "192.168.1.50",
                              "OPENBEAST_NTFY_PORT": "3999"})
    assert p.returncode == 0, p.stderr
    mounts = _serve_mounts(ts_rig)
    assert mounts["8447"] == "http://127.0.0.1:3999", mounts
    assert mounts["443"] == "http://192.168.1.50:3000"     # the rest follow BIND_HOST
    assert "ntfy extension is not in EXTENSIONS" in p.stdout
    assert "CHAT_NOTIFY_URL is empty" in p.stdout
    # Control: enabled + configured → neither warning.
    p = ts_rig.run("setup-tailscale.sh", "--publish-ntfy",
                   env_extra={"OPENBEAST_EXTENSIONS": "ntfy",
                              "OPENBEAST_CHAT_NOTIFY_URL": "http://127.0.0.1:3005/t"})
    assert p.returncode == 0, p.stderr
    assert "not in EXTENSIONS" not in p.stdout
    assert "CHAT_NOTIFY_URL is empty" not in p.stdout
    p = ts_rig.run("setup-tailscale.sh", "--unpublish-ntfy")
    assert p.returncode == 0, p.stderr
    assert any(k == "ts" and a[:3] == ["serve", "--https=8447", "off"]
               for k, a in _events(ts_rig))


def test_tailscale_status_lists_the_ntfy_row(ts_rig):
    stub = TAILSCALE_STUB.replace(
        'if args[:2] == ["status", "--json"]:',
        'if args[:2] == ["serve", "status"]:\n'
        '    print("https://beast.example.ts.net:8447 (tailnet only)\\n'
        '|-- / proxy http://127.0.0.1:3005")\n'
        'if args[:2] == ["status", "--json"]:')
    _write_exec(ts_rig.bin / "tailscale", stub)
    p = ts_rig.run("setup-tailscale.sh", "--status")
    assert p.returncode == 0, p.stderr
    rows = {ln.split()[0]: ln.split()[-1] for ln in p.stdout.splitlines()
            if ln.strip() and ln.split()[0].isdigit()}
    assert rows["8447"] == "published" and rows["8446"] == "-", p.stdout


# --- beast-hydra: never a raw inference port under HYDRA=true --------------
# docs/BEAST_HYDRA_PLAN.md §6.7 / §10 decision 4. hydra holds the fleet's node
# keys and trusts identity only through beast-gate.

def _mounts(rig, port):
    return [a for k, a in _events(rig)
            if k == "ts" and f"--https={port}" in a and "off" not in a]


def test_tailscale_refuses_raw_inference_under_hydra(ts_rig):
    ts_rig.set_state(auth=True, admin_pw="admin")
    p = ts_rig.run("setup-tailscale.sh", env_extra={"OPENBEAST_HYDRA": "true"})
    assert p.returncode != 0
    assert "hydra holds node keys: enable EDGE_GATE=true to publish inference" in p.stderr
    # refused BEFORE anything was mounted — not even the WebUI
    assert not _mounts(ts_rig, 8443) and not _mounted_443(ts_rig)


def test_tailscale_publishes_the_gate_under_hydra(ts_rig):
    """Negative control: with the gate on, :8443 goes to the gate (whose
    upstream is hydra), never to hydra's own port or raw :8080."""
    ts_rig.set_state(auth=True, admin_pw="admin")
    p = ts_rig.run("setup-tailscale.sh", env_extra={"OPENBEAST_HYDRA": "true",
                                                    "OPENBEAST_EDGE_GATE": "true"})
    assert p.returncode == 0, p.stderr
    m = _mounts(ts_rig, 8443)
    assert m and all(a[-1].endswith(":8090") for a in m), m
    assert not [a for k, a in _events(ts_rig) if k == "ts" and any(":8095" in x for x in a)]


def test_tailscale_without_hydra_still_publishes_raw(ts_rig):
    """HYDRA off: the raw :8443 -> :8080 path is still there (behind the
    shared key it now needs — see the keyless-inference tests above)."""
    ts_rig.set_state(auth=True, admin_pw="admin")
    p = ts_rig.run("setup-tailscale.sh", env_extra={"OPENBEAST_API_KEY": "k"})
    assert p.returncode == 0, p.stderr
    m = _mounts(ts_rig, 8443)
    assert m and m[-1][-1].endswith(":8080"), m
