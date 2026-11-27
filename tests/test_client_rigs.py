#!/usr/bin/env python3
"""The multi-rig client: scripts/lib/client_rigs.py through scripts/client.sh.

Everything runs for real with a throwaway HOME and XDG_CONFIG_HOME, against
stdlib HTTP servers on loopback that play a rig's model API (keyed) and its
beast-slot endpoint; `tailscale` is a stub printing canned `status --json`.

  - first run migrates a single-rig env file into the registry, names the rig
    after the key file that holds the same key, writes no key into the
    registry, and registers this user's own tailnet peer 'beast' (keyless)
    but never another user's
  - `use` switches env + opencode.json (one provider per rig, openbeast-rig =
    the active one, the MCP env follows), all 0600; a rig that is offline or
    rejects the key is refused with NOTHING changed unless --force
  - `rig add --api-key-stdin` stores the key in a 0600 file, never on argv
    or in any output
  - `rigs` lists every rig with its state; `status` names the active rig
  - no command ever prints a key

Run: python3 -m pytest tests/test_client_rigs.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(REPO, "scripts", "client.sh")

pytestmark = pytest.mark.skipif(not shutil.which("curl"), reason="curl not installed")

KEY_A = "keyA-" + "a" * 40
KEY_B = "keyB-" + "b" * 40


class FakeRig:
    """A keyed rig: /v1/models needs `key` (None = open), /api/slot is public."""

    def __init__(self, key, model="GLM-5.3-Flash-EXL3", backend="tensorfold", ctx=1048576):
        rig = self
        self.key = key
        self.slot = {"beast_slot": 2, "min_client": 1, "healthy": True, "backend": backend,
                     "model": {"id": model, "ctx": ctx}, "slots": {"total": 4, "busy": 1},
                     "capacity": {"ctx_shared": True}, "services": {},
                     "auth": "device" if key else "open"}
        self.model = model
        self.auth_seen = []
        self.slot_status = 200          # 503: beast-slot busy / restarting
        self.redirect_models = None     # a URL: /v1/models answers 302 there

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, st, body):
                data = json.dumps(body).encode()
                self.send_response(st)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                auth = self.headers.get("Authorization") or ""
                rig.auth_seen.append((path, auth))
                if path == "/api/slot":
                    if rig.slot_status != 200:
                        return self._send(rig.slot_status, {"error": "busy"})
                    return self._send(200, rig.slot)
                if path == "/v1/models" and rig.redirect_models:
                    self.send_response(302)
                    self.send_header("Location", rig.redirect_models)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path == "/health":
                    return self._send(200, {"status": "ok"})
                if rig.key and auth != "Bearer " + rig.key:
                    return self._send(401, {"error": "unauthorized"})
                if path == "/v1/models":
                    return self._send(200, {"data": [{"id": rig.model}]})
                return self._send(404, {})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _closed_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return "http://127.0.0.1:%d" % port


TS_JSON = {
    "Self": {"DNSName": "mac.tailnet.ts.net.", "UserID": 1},
    "MagicDNSSuffix": "tailnet.ts.net",
    "Peer": {"a": {"DNSName": "beast.tailnet.ts.net.", "UserID": 1},
             "b": {"DNSName": "spark-1.tailnet.ts.net.", "UserID": 1}},
}


class Env:
    def __init__(self, tmp_path, rig, ts_json=TS_JSON, key_file_name="spark-hydra-gate.key"):
        self.home = tmp_path / "home"
        self.xdg = self.home / ".config"
        (self.xdg / "opencode").mkdir(parents=True)
        venv = self.home / ".openbeast-client" / "venv" / "bin"
        venv.mkdir(parents=True)
        os.symlink(sys.executable, venv / "python3")
        ob = self.xdg / "openbeast"
        ob.mkdir(mode=0o700)
        if key_file_name:
            kf = ob / key_file_name
            kf.write_text(KEY_A + "\n")
            kf.chmod(0o600)
        self.env_file = self.home / ".openbeast-client.env"
        self.env_file.write_text(
            "OPENBEAST_AGENT_INFERENCE_URL='%s/v1'\nOPENBEAST_SLOT_URL='%s/api/slot'\n"
            "SEARXNG_URL='%s'\nOPENBEAST_API_KEY='%s'\nOPENAI_API_KEY='%s'\n"
            "OPENBEAST_SEARXNG_SECRET='keepme'\n" % (rig.url, rig.url, rig.url, KEY_A, KEY_A))
        self.env_file.chmod(0o600)
        self.oc = self.xdg / "opencode" / "opencode.json"
        self.oc.write_text(json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "mcp": {"servers": {
                "openbeast-tools": {"type": "local", "command": ["py", "mcp_server.py"],
                                    "environment": {"OPENBEAST_AGENT_INFERENCE_URL": rig.url + "/v1",
                                                    "OPENBEAST_API_KEY": KEY_A}, "codemode": False},
                "users-own": {"type": "local", "command": ["x"]}}},
            "provider": {"openbeast-rig": {"npm": "@ai-sdk/openai-compatible",
                                           "options": {"baseURL": rig.url + "/v1", "apiKey": KEY_A},
                                           "models": {"old": {}}},
                         "someone-else": {"options": {"baseURL": "http://x"}}},
            "model": "openbeast-rig/old", "small_model": "openbeast-rig/old"}))
        self.oc.chmod(0o600)
        self.stub = tmp_path / "bin"
        self.stub.mkdir()
        (tmp_path / "ts.json").write_text(json.dumps(ts_json))
        (self.stub / "tailscale").write_text(
            "#!/bin/sh\n[ \"$1\" = status ] && [ \"$2\" = --json ] && cat '%s'\nexit 0\n" % (tmp_path / "ts.json"))
        (self.stub / "tailscale").chmod(0o755)
        # `status` asks `autosync status`, which asks launchctl / systemctl:
        # never the real ones (they would read, or with install change, this
        # machine's own agents). Stubs log their argv and say "not loaded".
        self.svc_log = tmp_path / "svc.log"
        for tool in ("launchctl", "systemctl"):
            (self.stub / tool).write_text("#!/bin/sh\necho \"%s $*\" >> '%s'\nexit 1\n" % (tool, self.svc_log))
            (self.stub / tool).chmod(0o755)
        self.registry = ob / "rigs.json"

    def run(self, *args, stdin=None):
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.xdg), "OB_PROBE_TIMEOUT": "2",
               "PATH": "%s:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin" % self.stub}
        bash = shutil.which("bash", path="/bin:/usr/bin") or "bash"
        r = subprocess.run([bash, CLIENT, *args], capture_output=True, text=True, env=env,
                           input=stdin, timeout=90)
        out = r.stdout + r.stderr
        for k in (KEY_A, KEY_B):
            assert k not in out, "a device key was printed: %s" % out
        return r.returncode, out

    def reg(self):
        return json.loads(self.registry.read_text())

    def cfg(self):
        return json.loads(self.oc.read_text())


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.fixture
def rig_a():
    r = FakeRig(KEY_A)
    yield r
    r.close()


def test_first_run_migrates_the_single_rig_install(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    rc, out = e.run("rigs", "--no-probe")
    assert rc == 0, out
    reg = e.reg()
    assert reg["active"] == "spark-hydra", reg              # named after spark-hydra-gate.key
    sh = reg["rigs"]["spark-hydra"]
    assert sh["key_file"].endswith("openbeast/spark-hydra-gate.key")
    assert sh["inference_url"] == rig_a.url + "/v1"
    assert KEY_A not in e.registry.read_text()               # a path, never the key
    assert _mode(e.registry) == 0o600
    # this user's own tailnet peer 'beast' is registered, keyless
    assert reg["rigs"]["beast"]["host"] == "beast.tailnet.ts.net"
    assert reg["rigs"]["beast"]["key_file"] == ""
    assert "beast" in out and "NONE" in out and "rig add beast --api-key-stdin" in out, out


def test_another_users_beast_is_not_registered(tmp_path, rig_a):
    ts = json.loads(json.dumps(TS_JSON))
    ts["Peer"]["a"]["UserID"] = 2
    e = Env(tmp_path, rig_a, ts_json=ts)
    e.run("rigs", "--no-probe")
    assert "beast" not in e.reg()["rigs"]


def test_migration_without_a_key_file_stores_the_key_privately(tmp_path, rig_a):
    e = Env(tmp_path, rig_a, key_file_name=None)
    rc, out = e.run("rigs", "--no-probe")
    assert rc == 0, out
    reg = e.reg()
    name = reg["active"]
    assert name == "127-0-0-1", reg
    kf = reg["rigs"][name]["key_file"]
    assert kf.endswith("openbeast/rigs/%s.key" % name)
    assert open(kf).read().strip() == KEY_A and _mode(kf) == 0o600


def test_status_names_the_active_rig(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    rc, out = e.run("status")
    assert "✓ active rig: spark-hydra (127.0.0.1) — 2 registered" in out, out


def test_use_an_offline_rig_is_refused_and_changes_nothing(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    e.run("rigs", "--no-probe")
    reg = e.reg()
    reg["rigs"]["beast"]["inference_url"] = _closed_url() + "/v1"     # nothing answers
    reg["rigs"]["beast"]["slot_url"] = _closed_url() + "/api/slot"
    e.registry.write_text(json.dumps(reg))
    before = (e.env_file.read_text(), e.oc.read_text(), e.registry.read_text())
    rc, out = e.run("use", "beast")
    assert rc == 1, out
    assert "is not reachable" in out and "active rig unchanged: spark-hydra" in out, out
    assert (e.env_file.read_text(), e.oc.read_text(), e.registry.read_text()) == before

    # --force switches anyway; status then fails gracefully, naming the problem
    rc, out = e.run("use", "beast", "--force")
    assert rc == 0, out
    assert "switched with --force" in out and "back: openbeast-client use spark-hydra" in out, out
    assert e.reg()["active"] == "beast"
    env = e.env_file.read_text()
    assert "OPENBEAST_RIG='beast'" in env and KEY_A not in env.split("OPENBEAST_SEARXNG_SECRET")[0]
    rc, out = e.run("status")
    assert rc == 1 and "active rig: beast" in out and "no response" in out, out

    # and back
    rc, out = e.run("use", "spark-hydra")
    assert rc == 0, out
    assert "ok active rig: spark-hydra" in out and "was beast" in out, out
    env = e.env_file.read_text()
    assert "OPENBEAST_AGENT_INFERENCE_URL='%s/v1'" % rig_a.url in env
    assert "OPENBEAST_API_KEY='%s'" % KEY_A in env
    rc, out = e.run("status")
    assert "active rig: spark-hydra" in out and "✓ rig model API reachable" in out, out


def test_use_writes_env_and_opencode_for_the_target(tmp_path, rig_a):
    rig_b = FakeRig(KEY_B, model="qwen38-27b-uncensored-mtp-q5", backend="llama", ctx=262144)
    try:
        e = Env(tmp_path, rig_a)
        rc, out = e.run("rig", "add", "beast", "--api-key-stdin", stdin=KEY_B + "\n")
        assert rc == 0, out
        reg = e.reg()
        kf = reg["rigs"]["beast"]["key_file"]
        assert open(kf).read().strip() == KEY_B and _mode(kf) == 0o600
        reg["rigs"]["beast"]["inference_url"] = rig_b.url + "/v1"
        reg["rigs"]["beast"]["slot_url"] = rig_b.url + "/api/slot"
        reg["rigs"]["beast"]["search_url"] = rig_b.url
        e.registry.write_text(json.dumps(reg))

        rc, out = e.run("use", "beast")
        assert rc == 0, out
        assert "ok serving qwen38-27b-uncensored-mtp-q5 at 262144 ctx, slots 1/4" in out, out
        env = e.env_file.read_text()
        assert "OPENBEAST_RIG='beast'" in env
        assert "OPENBEAST_AGENT_INFERENCE_URL='%s/v1'" % rig_b.url in env
        assert "OPENBEAST_API_KEY='%s'" % KEY_B in env and KEY_A not in env
        assert "OPENBEAST_SEARXNG_SECRET='keepme'" in env            # not ours: kept
        assert _mode(e.env_file) == 0o600 and _mode(e.oc) == 0o600

        cfg = e.cfg()
        prov = cfg["provider"]
        assert prov["openbeast-rig"]["options"] == {"baseURL": rig_b.url + "/v1", "apiKey": KEY_B}
        assert "active: beast" in prov["openbeast-rig"]["name"]
        assert prov["openbeast-beast"]["options"]["apiKey"] == KEY_B
        assert prov["openbeast-spark-hydra"]["options"] == {"baseURL": rig_a.url + "/v1", "apiKey": KEY_A}
        # TensorFold rig: only the id it serves, with its window; llama rig:
        # the checkout catalog plus the live row
        sh_rows = prov["openbeast-spark-hydra"]["models"]
        assert list(sh_rows) == ["GLM-5.3-Flash-EXL3"], sh_rows
        assert sh_rows["GLM-5.3-Flash-EXL3"]["limit"]["context"] == 1048576
        b_rows = prov["openbeast-beast"]["models"]
        assert b_rows["qwen38-27b-uncensored-mtp-q5"]["limit"]["context"] == 262144
        assert len(b_rows) > 1
        assert prov["someone-else"] == {"options": {"baseURL": "http://x"}}   # untouched
        assert cfg["model"] == cfg["small_model"] == "openbeast-rig/qwen38-27b-uncensored-mtp-q5"
        mcp = cfg["mcp"]["servers"]
        ob_env = mcp["openbeast-tools"]["environment"]
        assert ob_env["OPENBEAST_AGENT_INFERENCE_URL"] == rig_b.url + "/v1"
        assert ob_env["OPENBEAST_API_KEY"] == KEY_B and ob_env["SEARXNG_URL"] == rig_b.url
        assert mcp["users-own"] == {"type": "local", "command": ["x"]}
        assert os.path.exists(str(e.oc) + ".openbeast-prev")

        rc, out = e.run("rigs")
        assert rc == 0, out
        lines = {ln.split()[1] if ln.startswith("  *") else ln.split()[0]: ln
                 for ln in out.splitlines() if ".ts.net" in ln or "127.0.0.1" in ln}
        assert lines["beast"].startswith("  *") and "healthy" in lines["beast"], out
        assert "healthy" in lines["spark-hydra"] and "1/4" in lines["spark-hydra"], out
        assert rig_b.auth_seen and all(a in ("", "Bearer " + KEY_B) for _, a in rig_b.auth_seen)
        assert all(a != "Bearer " + KEY_A for _, a in rig_b.auth_seen)   # no cross-rig key
    finally:
        rig_b.close()


def test_use_a_rig_that_rejects_the_key_is_refused(tmp_path, rig_a):
    rig_b = FakeRig(KEY_B, backend="llama")
    try:
        e = Env(tmp_path, rig_a)
        e.run("rigs", "--no-probe")
        reg = e.reg()
        reg["rigs"]["beast"]["inference_url"] = rig_b.url + "/v1"     # no key registered
        reg["rigs"]["beast"]["slot_url"] = rig_b.url + "/api/slot"
        e.registry.write_text(json.dumps(reg))
        before = e.env_file.read_text()
        rc, out = e.run("use", "beast")
        assert rc == 1, out
        assert "requires the device key" in out and "rig add beast --api-key-stdin" in out, out
        assert e.env_file.read_text() == before and e.reg()["active"] == "spark-hydra"
    finally:
        rig_b.close()


def test_refresh_config_keeps_every_rig_and_the_active_default(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    rc, out = e.run("refresh-config")
    assert rc == 0, out
    assert "rig spark-hydra (active) is serving 'GLM-5.3-Flash-EXL3' at 1048576 ctx" in out, out
    assert "no provider for 'beast'" in out, out
    cfg = e.cfg()
    assert cfg["model"] == "openbeast-rig/GLM-5.3-Flash-EXL3"
    assert "openbeast-beast" not in cfg["provider"]          # no key: no dead provider
    assert set(cfg["provider"]) == {"openbeast-rig", "openbeast-spark-hydra", "someone-else"}


def test_unknown_rig_and_active_removal(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    rc, out = e.run("use", "nope")
    assert rc == 2 and "no rig named 'nope'" in out and "spark-hydra" in out, out
    rc, out = e.run("rig", "remove", "spark-hydra")
    assert rc == 1 and "is the active rig" in out, out
    rc, out = e.run("rig", "remove", "beast")
    assert rc == 0 and "beast" not in e.reg()["rigs"], out
    rc, out = e.run("rig", "add", "rig", "--host", "x.ts.net")
    assert rc == 2 and "invalid rig name" in out, out


def test_key_file_is_referenced_and_tightened(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    kf = tmp_path / "beast.key"
    kf.write_text(KEY_B + "\n")
    kf.chmod(0o644)
    rc, out = e.run("rig", "add", "beast", "--key-file", str(kf), "--no-probe")
    assert rc == 0, out
    assert e.reg()["rigs"]["beast"]["key_file"] == str(kf) and _mode(kf) == 0o600


def test_setup_client_adopt_env_names_the_rig(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    env = {"HOME": str(e.home), "XDG_CONFIG_HOME": str(e.xdg), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "lib", "client_rigs.py"),
                        "adopt-env", "--name", "hydra"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    reg = e.reg()
    assert reg["active"] == "hydra" and reg["rigs"]["hydra"]["key_file"].endswith("spark-hydra-gate.key")
    assert "OPENBEAST_RIG='hydra'" in e.env_file.read_text()
    assert KEY_A not in r.stdout + r.stderr


def test_model_limits_come_from_the_slot_and_output_is_overridable(tmp_path, rig_a):
    # opencode's limit {context, output}: context = beast-slot model.ctx (the
    # per-conversation window, never ctx_total); output 32768 by default.
    e = Env(tmp_path, rig_a)
    e.run("refresh-config")
    row = e.cfg()["provider"]["openbeast-spark-hydra"]["models"]["GLM-5.3-Flash-EXL3"]
    assert row["limit"] == {"context": 1048576, "output": 32768}, row
    rc, out = e.run("rig", "add", "spark-hydra", "--output-limit", "16384")
    assert rc == 0, out
    for pid in ("openbeast-spark-hydra", "openbeast-rig"):
        row = e.cfg()["provider"][pid]["models"]["GLM-5.3-Flash-EXL3"]
        assert row["limit"] == {"context": 1048576, "output": 16384}, (pid, row)
    assert e.reg()["rigs"]["spark-hydra"]["output_limit"] == 16384


# ---- the soul: opencode `instructions` -------------------------------------------
# Open WebUI's system prompt (system-prompt.md + system-prompt-tools.md) reaches
# opencode through `instructions`: copies under ~/.openbeast-client/prompts/,
# in front of the user's own entries, which are never touched.

SOUL = ("system-prompt.md", "system-prompt-tools.md", "opencode-client.md")


def _soul_dir(e):
    return e.home / ".openbeast-client" / "prompts"


def test_sync_adds_the_soul_and_keeps_user_instructions(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    cfg = e.cfg()
    cfg["instructions"] = ["CONTRIBUTING.md", "~/rules/mine.md"]
    e.oc.write_text(json.dumps(cfg))
    rc, out = e.run("refresh-config")
    assert rc == 0, out
    d = _soul_dir(e)
    want = [str(d / n) for n in SOUL]
    ins = e.cfg()["instructions"]
    assert ins == want + ["CONTRIBUTING.md", "~/rules/mine.md"], ins
    # copies of the checkout's files, byte for byte (never edited)
    for n in SOUL[:2]:
        assert (d / n).read_text() == open(os.path.join(REPO, n)).read()
    note = (d / "opencode-client.md").read_text()
    assert "openbeast-tools_read_file" in note and ".manifest.jsonl" in note
    assert "ok opencode instructions: system-prompt.md, system-prompt-tools.md, opencode-client.md" in out, out
    assert _mode(e.oc) == 0o600                      # still holds the key


def test_soul_is_idempotent_and_follows_use(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    e.run("refresh-config")
    first = e.cfg()["instructions"]
    stamps = {n: os.stat(_soul_dir(e) / n).st_mtime_ns for n in SOUL}
    rc, out = e.run("refresh-config")
    assert rc == 0, out
    assert e.cfg()["instructions"] == first and len(first) == 3
    # unchanged content is not rewritten: opencode announces every change
    assert {n: os.stat(_soul_dir(e) / n).st_mtime_ns for n in SOUL} == stamps
    # a stale copy (an older checkout) is refreshed by the next sync, here `use`
    (_soul_dir(e) / "system-prompt.md").write_text("old soul\n")
    rc, out = e.run("use", "spark-hydra")
    assert rc == 0, out
    assert (_soul_dir(e) / "system-prompt.md").read_text() == open(os.path.join(REPO, "system-prompt.md")).read()
    assert e.cfg()["instructions"] == first


def test_soul_subcommand_check_and_remove_only_ours(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    cfg = e.cfg()
    cfg["instructions"] = ["docs/a.md"]
    e.oc.write_text(json.dumps(cfg))
    e.oc.chmod(0o600)
    env = {"HOME": str(e.home), "XDG_CONFIG_HOME": str(e.xdg), "PATH": str(e.stub) + ":/usr/bin:/bin"}
    rigs_py = os.path.join(REPO, "scripts", "lib", "client_rigs.py")

    def soul(*args):
        r = subprocess.run([sys.executable, rigs_py, "soul", *args], capture_output=True, text=True, env=env)
        return r.returncode, r.stdout + r.stderr

    rc, out = soul("--check")
    assert rc == 1 and "does not load the OpenBeast soul" in out, out
    rc, out = soul()
    assert rc == 0, out
    assert e.cfg()["instructions"][-1] == "docs/a.md" and len(e.cfg()["instructions"]) == 4
    assert _mode(e.oc) == 0o600                      # the mode is kept
    rc, out = soul()                                 # idempotent
    assert rc == 0 and len(e.cfg()["instructions"]) == 4, out
    # an opencode that ignores `instructions` (2.0.x) is named, with the fix
    (e.stub / "opencode").write_text("#!/bin/sh\necho 'opencode v2.0.25'\n")
    (e.stub / "opencode").chmod(0o755)
    rc, out = soul("--check")
    assert rc == 3 and "2.0.25 ignores the `instructions` setting" in out and "brew upgrade opencode" in out, out
    (e.stub / "opencode").write_text("#!/bin/sh\necho 'opencode v2.3.0'\n")
    rc, out = soul("--check")
    assert rc == 0 and "opencode loads the OpenBeast soul" in out, out
    rc, out = soul("--remove")
    assert rc == 0 and "removed 3 of ours" in out, out
    assert e.cfg()["instructions"] == ["docs/a.md"]
    rc, out = soul("--remove")
    assert rc == 0 and "removed 0 of ours" in out and e.cfg()["instructions"] == ["docs/a.md"], out


def test_soul_never_replaces_a_non_list_instructions(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    cfg = e.cfg()
    cfg["instructions"] = "mine.md"                  # not opencode's shape: not ours to fix
    e.oc.write_text(json.dumps(cfg))
    rc, out = e.run("refresh-config")
    assert rc == 0, out
    assert e.cfg()["instructions"] == "mine.md"
    assert "soul not wired — 'instructions' in opencode.json is not a list" in out, out


def test_autosync_follows_a_model_switch_and_is_quiet_otherwise(tmp_path, rig_a):
    """beastglm / beastds on the Sparks switch the served model under a running
    client: `autosync run` (the 30 s launchd/systemd job) must bring the name
    and context window opencode shows along, and write nothing when nothing
    changed or the rig cannot be reached."""
    e = Env(tmp_path, rig_a)
    rc, out = e.run("refresh-config")
    assert rc == 0, out
    rows = e.cfg()["provider"]["openbeast-rig"]["models"]
    assert set(rows) == {"GLM-5.3-Flash-EXL3"} and rows["GLM-5.3-Flash-EXL3"]["limit"]["context"] == 1048576
    before = e.oc.read_text()
    rc, out = e.run("autosync", "run")
    assert rc == 0 and out.strip() == "", out              # unchanged rig: silent, no write
    assert e.oc.read_text() == before
    # the rig switches to DeepSeek (another id, another window)
    rig_a.model = "DeepSeek-V4.1-Flash-1017"
    rig_a.slot["model"] = {"id": rig_a.model, "ctx": 524288}
    rc, out = e.run("autosync", "run")
    assert rc == 0 and "now serves DeepSeek-V4.1-Flash-1017 at 524288 ctx" in out, out
    cfg = e.cfg()
    rows = cfg["provider"]["openbeast-rig"]["models"]
    assert set(rows) == {"DeepSeek-V4.1-Flash-1017"}, rows
    assert rows["DeepSeek-V4.1-Flash-1017"]["limit"]["context"] == 524288
    assert cfg["model"] == "openbeast-rig/DeepSeek-V4.1-Flash-1017"
    # same model, new window (a restart with another CONTEXT) is a change too
    rig_a.slot["model"]["ctx"] = 262144
    rc, out = e.run("autosync", "run")
    assert "at 262144 ctx" in out, out
    assert e.cfg()["provider"]["openbeast-rig"]["models"]["DeepSeek-V4.1-Flash-1017"]["limit"]["context"] == 262144
    # an unreachable rig changes nothing
    before = e.oc.read_text()
    rig_a.close()
    rc, out = e.run("autosync", "run")
    assert rc == 0 and out.strip() == "", out
    assert e.oc.read_text() == before


# ---- review 2026-11-18 ---------------------------------------------------------

def test_an_unreadable_key_file_keeps_the_key_opencode_has(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    rc, out = e.run("refresh-config")
    assert rc == 0, out
    assert e.cfg()["provider"]["openbeast-spark-hydra"]["options"]["apiKey"] == KEY_A
    kf = e.xdg / "openbeast" / "spark-hydra-gate.key"
    kf.rename(kf.with_name("moved.away"))                # moved, unmounted, chmod'ed…
    rc, out = e.run("refresh-config")
    assert rc == 0, out
    assert "key file of rig 'spark-hydra'" in out and "cannot be read" in out, out
    cfg = e.cfg()
    # not "not-needed", not deleted, not stripped from the tools' env
    assert cfg["provider"]["openbeast-spark-hydra"]["options"]["apiKey"] == KEY_A
    assert cfg["provider"]["openbeast-rig"]["options"]["apiKey"] == KEY_A
    assert cfg["mcp"]["servers"]["openbeast-tools"]["environment"]["OPENBEAST_API_KEY"] == KEY_A
    rc, out = e.run("rigs", "--no-probe")
    assert "UNREADABLE" in out, out
    rc, out = e.run("use", "spark-hydra", "--force")
    assert "OPENBEAST_API_KEY='%s'" % KEY_A in e.env_file.read_text()      # env file keeps it too


def test_no_key_file_never_turns_a_real_key_into_not_needed(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    e.run("refresh-config")
    reg = e.reg()
    reg["rigs"]["spark-hydra"]["key_file"] = ""          # no key registered (e.g. an older registry)
    e.registry.write_text(json.dumps(reg))
    rc, out = e.run("refresh-config")
    assert rc == 0, out
    assert e.cfg()["provider"]["openbeast-rig"]["options"]["apiKey"] == KEY_A    # same rig: kept


def test_a_slot_that_does_not_answer_keeps_the_backend(tmp_path, rig_a):
    # TensorFold rig; beast-slot busy (503) while the model switches: before
    # the fix the backend became None (= llama) and the rig got the checkout's
    # 18 static llama rows.
    e = Env(tmp_path, rig_a)
    e.run("refresh-config")
    assert set(e.cfg()["provider"]["openbeast-rig"]["models"]) == {"GLM-5.3-Flash-EXL3"}
    rig_a.slot_status = 503
    rig_a.model = "DeepSeek-V4.1-Flash-1017"
    rc, out = e.run("refresh-config")
    assert rc == 0, out
    assert set(e.cfg()["provider"]["openbeast-rig"]["models"]) == {"DeepSeek-V4.1-Flash-1017"}
    assert e.reg()["rigs"]["spark-hydra"]["last"]["backend"] == "tensorfold"


def test_autosync_repairs_a_tensorfold_rig_left_with_llama_rows(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    e.run("refresh-config")
    reg = e.reg()
    reg["rigs"]["spark-hydra"]["last"]["backend"] = None              # the damage the old code did
    e.registry.write_text(json.dumps(reg))
    static = json.load(open(os.path.join(REPO, "opencode.json")))["provider"]["llama-cpp"]["models"]
    cfg = e.cfg()
    rows = cfg["provider"]["openbeast-rig"]["models"]
    cfg["provider"]["openbeast-rig"]["models"] = dict(static, **rows)
    e.oc.write_text(json.dumps(cfg))
    rc, out = e.run("autosync", "run")
    assert rc == 0 and "now serves GLM-5.3-Flash-EXL3" in out, out
    assert set(e.cfg()["provider"]["openbeast-rig"]["models"]) == {"GLM-5.3-Flash-EXL3"}
    assert e.reg()["rigs"]["spark-hydra"]["last"]["backend"] == "tensorfold"
    rc, out = e.run("autosync", "run")
    assert out.strip() == "", out                                     # and then quiet again


def test_probes_never_follow_a_redirect_with_the_key(tmp_path, rig_a):
    elsewhere = FakeRig(None)
    try:
        rig_a.redirect_models = elsewhere.url + "/v1/models"
        e = Env(tmp_path, rig_a)
        rc, out = e.run("rigs", "--json")
        assert rc == 0, out
        assert [p for p, _ in elsewhere.auth_seen] == [], elsewhere.auth_seen   # never dialed, key or not
        state = json.loads(out[out.index("{"):])["rigs"]["spark-hydra"]["state"]
        assert state != "healthy", state
    finally:
        elsewhere.close()


def test_autosync_is_quiet_about_a_missing_or_jsonc_config(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    e.run("refresh-config")
    reg0, m0 = e.registry.read_text(), os.stat(e.registry).st_mtime_ns
    text = e.oc.read_text()
    e.oc.write_text("// my comment\n" + text)
    rc, out = e.run("autosync", "run")
    assert rc == 0 and "Traceback" not in out, out
    assert len(out.strip().splitlines()) == 1 and "not plain JSON" in out, out
    rc, out = e.run("autosync", "run")
    assert rc == 0 and out.strip() == "", out                         # rate-limited
    e.oc.unlink()
    rc, out = e.run("autosync", "run")
    assert rc == 0 and len(out.strip().splitlines()) == 1 and "no opencode config" in out, out
    rc, out = e.run("autosync", "run")
    assert rc == 0 and out.strip() == "", out
    assert e.registry.read_text() == reg0 and os.stat(e.registry).st_mtime_ns == m0   # never rewritten
    # an interactive sync says so, with no traceback, and leaves the file alone
    e.oc.write_text("// my comment\n" + text)
    rc, out = e.run("refresh-config")
    assert "could not parse" in out and "Traceback" not in out, out
    assert e.oc.read_text().startswith("// my comment")


def test_autosync_caps_its_log(tmp_path, rig_a):
    e = Env(tmp_path, rig_a)
    e.run("refresh-config")
    log = e.home / ".openbeast-client" / "autosync.log"
    log.write_bytes(b"x" * 2000000 + b"\nthe last line\n")
    rc, out = e.run("autosync", "run")
    assert rc == 0, out
    data = log.read_bytes()
    assert len(data) < 1024 * 1024 and data.endswith(b"the last line\n"), len(data)


def test_autosync_skips_a_run_while_another_sync_holds_the_lock(tmp_path, rig_a):
    import fcntl
    e = Env(tmp_path, rig_a)
    e.run("refresh-config")
    before = e.oc.read_text()
    rig_a.model = "DeepSeek-V4.1-Flash-1017"
    rig_a.slot["model"] = {"id": rig_a.model, "ctx": 524288}
    fd = os.open(str(e.xdg / "openbeast" / ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        rc, out = e.run("autosync", "run")
        assert rc == 0 and out.strip() == "" and e.oc.read_text() == before, out
    finally:
        os.close(fd)
    rc, out = e.run("autosync", "run")
    assert "now serves DeepSeek-V4.1-Flash-1017" in out, out


def _svc_stubs(e, system):
    """uname says `system`; launchctl / systemctl are stubs that log argv and
    answer like an installed job. No test calls the real ones."""
    (e.stub / "uname").write_text("#!/bin/sh\necho %s\n" % system)
    (e.stub / "launchctl").write_text(
        "#!/bin/sh\necho \"launchctl $*\" >> '%s'\n"
        "if [ \"$1\" = print ]; then [ -f \"$HOME/Library/LaunchAgents/ai.openbeast.client-autosync.plist\" ]"
        " || exit 113; printf '\\tstate = not running\\n\\tlast exit code = 0\\n'; fi\nexit 0\n" % e.svc_log)
    (e.stub / "systemctl").write_text(
        "#!/bin/sh\necho \"systemctl $*\" >> '%s'\nu=\"${XDG_CONFIG_HOME}/systemd/user/openbeast-client-autosync\"\n"
        "case \"$*\" in\n  *is-active*) [ -f \"$u.timer\" ] || exit 3 ;;\n"
        "  *'-p Result'*) echo success ;;\n  *ExecMainStatus*) echo 0 ;;\nesac\nexit 0\n" % e.svc_log)
    for t in ("uname", "launchctl", "systemctl"):
        (e.stub / t).chmod(0o755)


def _assert_copy(e):
    snap = e.home / ".openbeast-client" / "autosync"
    for f in ("scripts/lib/client_rigs.py", "opencode.json", "system-prompt.md", "system-prompt-tools.md"):
        assert (snap / f).is_file(), f
    return snap


def test_autosync_install_on_macos_runs_the_copy_never_the_checkout(tmp_path, rig_a):
    import plistlib
    e = Env(tmp_path / "r&d <home>", rig_a)                 # a path the plist must escape
    _svc_stubs(e, "Darwin")
    e.run("refresh-config")
    rc, out = e.run("autosync", "install")
    assert rc == 0 and "launchd agent ai.openbeast.client-autosync" in out, out
    snap = _assert_copy(e)
    plist = e.home / "Library" / "LaunchAgents" / "ai.openbeast.client-autosync.plist"
    raw = plist.read_text()
    assert REPO not in raw, raw                             # TCC: never the ~/Documents checkout
    pl = plistlib.loads(raw.encode())                       # well-formed despite & and <
    assert pl["ProgramArguments"][1:] == [str(snap / "scripts/lib/client_rigs.py"), "sync", "--if-changed"]
    assert pl["EnvironmentVariables"]["OB_REPO"] == str(snap)
    assert pl["StandardOutPath"] == str(e.home / ".openbeast-client" / "autosync.log")
    assert "launchctl bootstrap" in e.svc_log.read_text()
    # the job, run exactly as launchd would: from the copy alone
    r = subprocess.run(pl["ProgramArguments"], capture_output=True, text=True, timeout=60,
                       env={"HOME": str(e.home), "XDG_CONFIG_HOME": str(e.xdg), "PATH": "/usr/bin:/bin",
                            "OB_PROBE_TIMEOUT": "2", **pl["EnvironmentVariables"]})
    assert r.returncode == 0, r.stdout + r.stderr
    rc, out = e.run("autosync", "status")
    assert "autosync: on" in out and "last run: exit 0" in out and "copy matches this checkout" in out, out
    (snap / "system-prompt.md").write_text("stale\n")
    rc, out = e.run("autosync", "status")
    assert "differs from this checkout: system-prompt.md" in out, out
    rc, out = e.run("autosync", "uninstall")
    assert rc == 0 and not plist.exists() and not snap.exists(), out
    assert "launchctl bootout" in e.svc_log.read_text()


def test_autosync_install_on_linux_quotes_the_unit(tmp_path, rig_a):
    e = Env(tmp_path / "r&d %h home", rig_a)               # a space and a systemd specifier
    _svc_stubs(e, "Linux")
    e.run("refresh-config")
    rc, out = e.run("autosync", "install")
    assert rc == 0 and "systemd user timer" in out, out
    snap = _assert_copy(e)
    udir = e.xdg / "systemd" / "user"
    unit = (udir / "openbeast-client-autosync.service").read_text()
    assert REPO not in unit, unit
    s = str(snap).replace("%", "%%")
    py = str(e.home / ".openbeast-client" / "venv" / "bin" / "python3").replace("%", "%%")
    assert 'Environment="OB_REPO=%s" "OB_TS_BIN=' % s in unit, unit
    assert 'ExecStart="%s" "%s/scripts/lib/client_rigs.py" sync --if-changed' % (py, s) in unit, unit
    assert (udir / "openbeast-client-autosync.timer").is_file()
    assert "systemctl --user enable --now openbeast-client-autosync.timer" in e.svc_log.read_text()
    rc, out = e.run("autosync", "status")
    assert "autosync: on" in out and "last run: success (exit 0)" in out and "copy matches" in out, out
    rc, out = e.run("autosync", "uninstall")
    assert rc == 0 and not snap.exists(), out
    assert not (udir / "openbeast-client-autosync.service").exists()
    assert not (udir / "openbeast-client-autosync.timer").exists()
