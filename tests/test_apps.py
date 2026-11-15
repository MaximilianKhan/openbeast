#!/usr/bin/env python3
"""beast-apps (scripts/apps.sh, agents/apps/) — hermetic tests.

Nothing here touches the real machine: every probe sees stub executables in
a throwaway PATH and a throwaway HOME, the catalog's absolute paths are
re-rooted into that HOME, the opencode config is a temp file, and the MCP
"servers" are tests/fixtures/apps/stub_mcp_server.py behind a stub npx. The
npm registry is a local HTTP server; "offline" is an island probe aimed at a
closed loopback port.

Run: python3 -m pytest tests/test_apps.py -q
"""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "agents" / "apps" / "cli.py"
CATALOG = REPO / "agents" / "apps" / "catalog.toml"
STUB = REPO / "tests" / "fixtures" / "apps" / "stub_mcp_server.py"
WRAPPER = REPO / "scripts" / "apps.sh"


def _py311() -> str | None:
    if sys.version_info >= (3, 11):
        return sys.executable
    for c in ("python3.14", "python3.13", "python3.12", "python3.11"):
        p = shutil.which(c)
        if p:
            return p
    return None


PY = _py311()
pytestmark = pytest.mark.skipif(PY is None, reason="beast-apps needs Python 3.11+ (tomllib)")


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class Box:
    """A throwaway machine: HOME, PATH of stubs, state dir, opencode config."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "home"
        self.bin = tmp / "bin"
        self.state = tmp / "state"
        self.overlay = tmp / "overlay"
        self.config = self.home / ".config" / "opencode" / "opencode.json"
        for d in (self.home, self.bin, self.state, self.overlay):
            d.mkdir(parents=True, exist_ok=True)
        self.argv_log = tmp / "npx-argv.log"
        # The real catalog, with every absolute machine path re-rooted into
        # HOME: /Applications/Xcode.app on the box running the tests must not
        # leak into a "Xcode is not installed" assertion.
        text = CATALOG.read_text()
        for root in ("/Applications", "/opt/homebrew", "/usr/local/bin", "/snap/bin", '"/usr/bin"'):
            if root.startswith('"'):
                text = text.replace(root, f'"{self.home}/usr/bin"')
            else:
                text = text.replace(f'"{root}', f'"{self.home}{root}')
        self.catalog = tmp / "catalog.toml"
        self.catalog.write_text(text)
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "OPENBEAST_APPS_STATE": str(self.state),
            "OPENBEAST_APPS_CATALOG": str(self.catalog),
            "OPENBEAST_APPS_CATALOG_D": str(self.overlay),
            "OPENBEAST_OPENCODE_CONFIG": str(self.config),
            "OPENBEAST_OPENCODE_SCHEMA": "v2",
            "OPENBEAST_APPS_PLATFORM": "darwin",
            "OPENBEAST_ISLAND_PROBES": f"127.0.0.1:{_closed_port()}",
            "OPENBEAST_APPS_PROBE_TIMEOUT": "0.5",
            "OPENBEAST_APPS_TIMEOUT": "20",
            "PY": PY,
            "STUB": str(STUB),
            "ARGV_LOG": str(self.argv_log),
        }
        # base toolchain every server needs
        _exe(self.bin / "node", 'echo v22.1.0\n')
        _exe(self.bin / "npx", '''
            if [ "$1" = "--version" ]; then echo 10.9.0; exit 0; fi
            echo "$*" >> "$ARGV_LOG"
            exec "$PY" "$STUB" "$@"
        ''')
        _exe(self.bin / "uvx", '''
            if [ "$1" = "--version" ]; then echo "uv 0.9.0"; exit 0; fi
            exec "$PY" "$STUB" "$@"
        ''')
        _exe(self.bin / "sw_vers", 'echo 15.1\n')

    # ── machine shapes ──
    def clt_only(self):
        _exe(self.bin / "xcodebuild", '''
            echo "xcode-select: error: tool 'xcodebuild' requires Xcode, but active developer directory '/Library/Developer/CommandLineTools' is a command line tools instance" >&2
            exit 1
        ''')
        _exe(self.bin / "xcrun", '''
            echo 'xcrun: error: unable to find utility "simctl", not a developer tool or in PATH' >&2
            exit 72
        ''')

    def full_xcode(self):
        (self.home / "Applications" / "Xcode.app" / "Contents").mkdir(parents=True, exist_ok=True)
        _exe(self.bin / "xcodebuild", 'printf "Xcode 16.2\\nBuild version 16C5032a\\n"\n')
        _exe(self.bin / "xcrun", 'echo "== Devices =="; echo "-- iOS 18.2 --"\n')

    def android_sdk_off_path(self):
        sdk = self.home / "Library" / "Android" / "sdk"
        _exe(sdk / "platform-tools" / "adb", 'echo "Android Debug Bridge version 1.0.41"\n')
        _exe(self.bin / "java", 'echo \'openjdk version "17.0.9" 2023-10-17\' >&2\n')
        return sdk

    def overlay_toml(self, name: str, text: str):
        (self.overlay / name).write_text(textwrap.dedent(text))

    def stub_server(self, risk=("shell",), ask=("danger*",), version="1.0.0"):
        self.overlay_toml("stub.toml", f'''
            [capability.stub-cap]
            title = "A stub capability served by the test MCP server"
            keywords = ["stub", "widget"]
            servers = ["stubsrv"]

            [server.stubsrv]
            title = "stub MCP server"
            source = "https://example.invalid/stub"
            checked = "2026-11-15"
            registry = "npm"
            package = "stubpkg"
            version = "{version}"
            transport = "stdio"
            command = ["npx", "-y", "{{package}}@{{version}}"]
            platforms = ["darwin", "linux"]
            probes = ["npx"]
            risk_patterns = {json.dumps(list(risk))}
            ask_tools = {json.dumps(list(ask))}
        ''')

    # ── running ──
    def run(self, *args, extra_env=None, check=None):
        env = dict(self.env)
        env.update(extra_env or {})
        cp = subprocess.run([PY, str(CLI), *args], capture_output=True, text=True, env=env, timeout=120)
        if check is not None:
            assert cp.returncode == check, f"rc={cp.returncode}\nstdout:\n{cp.stdout}\nstderr:\n{cp.stderr}"
        return cp

    def json(self, *args, **kw):
        cp = self.run(*args, "--json", **kw)
        return cp.returncode, json.loads(cp.stdout)

    def read_config(self):
        return json.loads(self.config.read_text())

    def write_config(self, obj):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps(obj, indent=2) + "\n")

    def backups(self):
        return sorted(self.config.parent.glob("opencode.json.bak-beast-apps-*"))


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


USER_CFG = {
    "$schema": "https://opencode.ai/config.json",
    "model": "openbeast-rig/glm",
    "provider": {"openbeast-rig": {"npm": "@ai-sdk/openai-compatible", "options": {"baseURL": "https://rig/v1"}}},
    "mcp": {"openbeast-tools": {"type": "local", "command": ["/venv/bin/python3", "/repo/agents/mcp_server.py"],
                                "enabled": True}},
    "permissions": [{"action": "bash", "resource": "rm *", "effect": "deny"}],
}


# ── catalog ─────────────────────────────────────────────────────────────────
def test_real_catalog_is_valid_and_every_server_is_pinned_and_sourced(box):
    cp = subprocess.run([PY, str(CLI), "validate"], capture_output=True, text=True,
                        env={**box.env, "OPENBEAST_APPS_CATALOG": str(CATALOG)})
    assert cp.returncode == 0, cp.stderr
    # parsed by the 3.11+ interpreter: the one running pytest may be older
    cat = json.loads(subprocess.run(
        [PY, "-c", "import json,sys,tomllib; print(json.dumps(tomllib.load(open(sys.argv[1], 'rb'))))",
         str(CATALOG)], capture_output=True, text=True, check=True).stdout)
    for sid, s in cat["server"].items():
        assert s["source"].startswith("https://"), sid
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["checked"]), sid
        if s["registry"] != "app":
            assert re.fullmatch(r"\d+\.\d+\.\d+", s["version"]), f"{sid}: not an exact pin"
            assert any("{version}" in a for a in s["command"]), sid
    # the 3D printer server has no print confirmation upstream: off by default
    assert cat["server"]["mcp-3d-printer-server"]["default_enabled"] is False
    # the seeded capabilities Max asked for
    for cid in ("ios-build", "android-build", "blender", "figma", "3d-print", "sim-ros"):
        assert cid in cat["capability"]


def test_validation_names_each_problem(box):
    box.overlay_toml("bad.toml", '''
        [server.broken]
        title = "x"
        source = "https://x"
        checked = "2026-11-15"
        registry = "npm"
        package = "x"
        version = "1.0.0"
        transport = "stdio"
        command = ["npx", "-y", "x@latest"]
        platforms = ["darwin"]
        probes = ["no-such-probe"]

        [probe.nofix]
        kind = "path"
        what = "something"
        paths = ["/nope"]
    ''')
    cp = box.run("list")
    assert cp.returncode == 1
    assert "must pin {version}" in cp.stderr
    assert "unknown probe 'no-such-probe'" in cp.stderr
    assert "probe.nofix: missing fix" in cp.stderr


def test_overlay_adds_a_capability_without_code_changes(box):
    box.stub_server()
    rc, out = box.json("list")
    assert rc == 0
    caps = {c["capability"]: c for c in out["capabilities"]}
    assert caps["stub-cap"]["status"] == "READY"
    assert caps["stub-cap"]["server"] == "stubsrv"


def test_find_maps_a_request_to_capabilities(box):
    rc, out = box.json("find", "mobile", "build", "on", "the", "mac", "for", "ios")
    assert rc == 0
    assert out["matches"][0]["capability"] == "ios-build"
    rc, out = box.json("find", "android", "apk")
    assert out["matches"][0]["capability"] == "android-build"
    rc, out = box.json("find", "quantum", "teleporter")
    assert rc == 2 and out["matches"] == []


# ── check: ready / missing / unsupported ────────────────────────────────────
def test_check_ios_clt_only_is_missing_with_exact_remediation(box):
    box.clt_only()
    cp = box.run("check", "ios-build")
    assert cp.returncode == 3, cp.stdout
    out = cp.stdout
    assert "status: MISSING" in out
    assert "Xcode.app is not installed: only the Command Line Tools are present" in out
    assert "xcodes install --latest" in out
    assert "sudo xcode-select -s /Applications/Xcode.app" in out
    assert "is a command line tools instance" in out          # the real error is shown
    assert 'unable to find utility "simctl"' in out
    rc, rep = box.json("check", "ios-build")
    assert rc == 3 and rep["status"] == "MISSING" and rep["next"] is None
    failing = {p["id"] for s in rep["servers"] for p in s["probes"] if p["status"] == "fail"}
    assert failing == {"xcode-app", "xcodebuild-full", "simctl"}
    assert len(rep["remediation"]) == 3


def test_check_ios_full_xcode_is_ready(box):
    box.full_xcode()
    rc, rep = box.json("check", "ios-build")
    assert rc == 0, rep
    assert rep["status"] == "READY" and rep["server"] == "mobilebuildmcp"
    assert rep["next"] == "scripts/apps.sh enable ios-build"


def test_check_ios_old_xcode_reports_the_version(box):
    box.full_xcode()
    _exe(box.bin / "xcodebuild", 'printf "Xcode 15.4\\nBuild version 15F31d\\n"\n')
    rc, rep = box.json("check", "ios-build")
    assert rc == 3
    probe = next(p for p in rep["servers"][0]["probes"] if p["id"] == "xcodebuild-full")
    assert probe["found"] == "15.4" and "< 16.0" in probe["detail"]


def test_check_ios_on_linux_is_unsupported(box):
    rc, rep = box.json("check", "ios-build", extra_env={"OPENBEAST_APPS_PLATFORM": "linux"})
    assert rc == 4
    assert rep["status"] == "UNSUPPORTED"
    assert "darwin only; this machine is linux" in rep["remediation"][0]


def test_check_android_adb_off_path_is_ready_with_advice(box):
    sdk = box.android_sdk_off_path()
    rc, rep = box.json("check", "android-build")
    assert rc == 0, rep
    assert any(f"adb exists at {sdk}/platform-tools/adb but is not on PATH" in w for w in rep["warnings"])
    srv = rep["servers"][0]
    assert srv["export_env"]["ANDROID_HOME"] == str(sdk)


def test_check_android_without_adb_names_what_is_missing(box):
    _exe(box.bin / "java", 'echo \'openjdk version "17.0.9"\' >&2\n')
    rc, rep = box.json("check", "android-build")
    assert rc == 3
    assert any("adb was not found on PATH or in the default SDK locations" in r for r in rep["remediation"])


def test_old_java_on_path_but_a_newer_jdk_elsewhere_is_used(box):
    box.android_sdk_off_path()
    _exe(box.bin / "java", 'echo \'openjdk version "11.0.26"\' >&2\n')
    jbr = box.home / "Applications" / "Android Studio.app" / "Contents" / "jbr" / "Contents" / "Home" / "bin"
    _exe(jbr / "java", 'echo \'openjdk version "21.0.6"\' >&2\n')
    rc, rep = box.json("check", "android-build")
    assert rc == 0, rep
    java = next(p for p in rep["servers"][0]["probes"] if p["id"] == "java-17")
    assert java["status"] == "warn" and java["found"] == "21.0.6"
    assert rep["servers"][0]["export_env"]["JAVA_HOME"] == str(jbr.parent)


def test_unknown_capability_suggests(box):
    cp = box.run("check", "andriod-build-apk")
    assert cp.returncode == 2
    assert "unknown capability" in cp.stderr


# ── enable / disable ────────────────────────────────────────────────────────
def test_enable_v2_merges_backs_up_and_disable_restores(box):
    sdk = box.android_sdk_off_path()
    box.write_config(USER_CFG)
    before = box.config.read_text()
    rc, out = box.json("enable", "android-build")
    assert rc == 0, out
    assert out["schema"] == "v2" and out["key"] == "replicant-mcp"
    bks = box.backups()
    assert len(bks) == 1 and bks[0].read_text() == before
    assert oct(bks[0].stat().st_mode & 0o777) == "0o600"
    cfg = box.read_config()
    # everything of the user's survives
    for k in ("$schema", "model", "provider"):
        assert cfg[k] == USER_CFG[k]
    assert cfg["mcp"]["openbeast-tools"] == USER_CFG["mcp"]["openbeast-tools"]
    e = cfg["mcp"]["servers"]["replicant-mcp"]
    assert e["type"] == "local" and e["codemode"] is False and "enabled" not in e
    assert e["command"] == [str(box.bin / "npx"), "-y", "replicant-mcp@1.6.7"]
    assert e["environment"]["ANDROID_HOME"] == str(sdk)
    assert e["environment"]["PATH"].split(":")[0] == str(sdk / "platform-tools")
    assert cfg["permissions"][0] == USER_CFG["permissions"][0]
    assert {"action": "replicant-mcp_adb-shell", "resource": "*", "effect": "ask"} in cfg["permissions"]
    # list/check now see it as enabled
    rc, rep = box.json("check", "android-build")
    assert rep["enabled"]["key"] == "replicant-mcp"

    rc, out = box.json("disable", "android-build")
    assert rc == 0, out
    assert box.read_config() == USER_CFG                 # exactly what the user had
    assert len(box.backups()) == 2


def test_enable_v1_schema_uses_legacy_keys(box):
    box.android_sdk_off_path()
    box.write_config({"mcp": {"mine": {"type": "local", "command": ["x"], "enabled": True}}})
    rc, out = box.json("enable", "android-build", extra_env={"OPENBEAST_OPENCODE_SCHEMA": "v1"})
    assert rc == 0, out
    cfg = box.read_config()
    assert cfg["mcp"]["mine"] == {"type": "local", "command": ["x"], "enabled": True}
    e = cfg["mcp"]["replicant-mcp"]
    assert e["enabled"] is True and "codemode" not in e
    assert cfg["permission"] == {"replicant-mcp_adb-shell": "ask"}
    box.run("disable", "replicant-mcp", check=0)
    assert box.read_config() == {"mcp": {"mine": {"type": "local", "command": ["x"], "enabled": True}}}


def test_schema_detection_from_installed_opencode(box):
    box.android_sdk_off_path()
    _exe(box.bin / "opencode", 'echo "opencode v2.0.25"\n')
    env = {"OPENBEAST_OPENCODE_SCHEMA": ""}
    rc, out = box.json("enable", "android-build", "--dry-run", extra_env=env)
    assert out["schema"] == "v2"
    _exe(box.bin / "opencode", 'echo "1.18.3"\n')
    rc, out = box.json("enable", "android-build", "--dry-run", extra_env=env)
    assert out["schema"] == "v1"
    assert not box.config.exists()                       # dry run wrote nothing


def test_enable_refuses_when_check_fails_and_writes_nothing(box):
    box.clt_only()
    box.write_config(USER_CFG)
    before = box.config.read_text()
    cp = box.run("enable", "ios-build")
    assert cp.returncode == 3
    assert "Refusing to enable ios-build" in cp.stdout
    assert "Xcode.app is not installed" in cp.stdout
    assert box.config.read_text() == before and box.backups() == []
    assert not (box.state / "enabled.json").exists()


def test_enable_refuses_unsupported_platform(box):
    cp = box.run("enable", "figma", extra_env={"OPENBEAST_APPS_PLATFORM": "linux"})
    assert cp.returncode == 4


def test_risky_server_needs_accept_risk_and_secrets_make_the_file_private(box):
    env = {"PRINTER_TYPE": "octoprint", "PRINTER_HOST": "10.0.0.5", "API_KEY": "s3cret"}
    cp = box.run("enable", "3d-print", extra_env=env)
    assert cp.returncode == 6
    assert "OFF by default" in cp.stdout and "no confirmation prompt" in cp.stdout
    assert not box.config.exists()
    rc, out = box.json("enable", "3d-print", "--accept-risk", extra_env=env)
    assert rc == 0, out
    cfg = box.read_config()
    e = cfg["mcp"]["servers"]["mcp-3d-printer-server"]
    assert e["environment"]["PRINTER_TYPE"] == "octoprint" and e["environment"]["API_KEY"] == "s3cret"
    assert oct(box.config.stat().st_mode & 0o777) == "0o600"
    gated = {r["action"] for r in cfg["permissions"] if r["effect"] == "ask"}
    assert "mcp-3d-printer-server_start_print" in gated and "mcp-3d-printer-server_print_3mf" in gated


def test_printer_without_env_is_missing(box):
    rc, rep = box.json("check", "3d-print")
    assert rc == 3
    assert any("PRINTER_TYPE is not set" in r for r in rep["remediation"])


def test_enable_never_overwrites_a_user_entry(box):
    box.android_sdk_off_path()
    cfg = {"mcp": {"servers": {"replicant-mcp": {"type": "local", "command": ["my", "own"]}}}}
    box.write_config(cfg)
    cp = box.run("enable", "android-build")
    assert cp.returncode == 7 and "did not write" in cp.stdout
    assert box.read_config() == cfg
    # same package under another name: no duplicate either, unless asked
    cfg = {"mcp": {"droid": {"type": "local", "command": ["npx", "-y", "replicant-mcp"], "enabled": True}}}
    box.write_config(cfg)
    cp = box.run("enable", "android-build")
    assert cp.returncode == 7 and "droid" in cp.stdout
    box.run("enable", "android-build", "--allow-duplicate", check=0)
    # and disable never removes the user's entry
    cp = box.run("disable", "droid")
    assert cp.returncode == 2 and "did not write it" in cp.stdout
    assert box.read_config()["mcp"]["droid"] == cfg["mcp"]["droid"]


def test_jsonc_config_is_refused_untouched(box):
    box.android_sdk_off_path()
    box.config.parent.mkdir(parents=True, exist_ok=True)
    box.config.write_text('{\n  // my comment\n  "model": "x"\n}\n')
    cp = box.run("enable", "android-build")
    assert cp.returncode == 1 and "not plain JSON" in cp.stderr
    assert "// my comment" in box.config.read_text()


def test_remote_server_entry(box):
    (box.home / "Applications" / "Figma.app").mkdir(parents=True)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    box.overlay_toml("figma-port.toml", f'''
        [probe.figma-mcp-port]
        port = "{port}"
    ''')
    try:
        rc, out = box.json("enable", "figma")
    finally:
        srv.close()
    assert rc == 0, out
    assert out["entry"] == {"type": "remote", "url": "http://127.0.0.1:3845/mcp", "codemode": False}


# ── refresh / upgrade ───────────────────────────────────────────────────────
class Registry:
    """A local npm registry: GET /<pkg>/latest → {"version": ...}."""

    def __init__(self, version: str):
        self.version = version
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"name": "stubpkg", "version": outer.version}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def env(self):
        return {"OPENBEAST_APPS_NPM_REGISTRY": f"http://127.0.0.1:{self.port}",
                "OPENBEAST_ISLAND_PROBES": f"127.0.0.1:{self.port}"}   # "online"

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def registry():
    r = Registry("1.0.0")
    yield r
    r.close()


def test_refresh_records_manifest_and_detects_an_added_tool(box, registry):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    env = {**registry.env(), "STUB_TOOLS": "alpha,beta"}
    rc, out = box.json("refresh", extra_env=env)
    assert rc == 0, out
    row = out["servers"][0]
    assert out["offline"] is False and row["latest"] == "1.0.0" and not row["update_available"]
    assert row["tool_count"] == 2 and row["diff"]["first"]
    man = json.loads((box.state / "manifests" / "stubsrv" / "1.0.0.json").read_text())
    assert [t["name"] for t in man["tools"]] == ["alpha", "beta"]
    assert man["server_info"]["version"] == "1.0.0"

    # upstream "updates" the server in place: a new exec-style tool appears
    env["STUB_TOOLS"] = "alpha,beta,run_shell,danger_zone"
    cp = box.run("refresh", extra_env=env, check=0)
    assert "+ new: danger_zone, run_shell" in cp.stdout
    assert "new tool run_shell matches risk pattern(s) shell and is NOT gated" in cp.stdout
    rc, out = box.json("refresh", extra_env={**env, "STUB_TOOLS": "alpha,run_shell,danger_zone"})
    d = out["servers"][0]["diff"]
    assert d["removed"] == ["beta"] and d["added"] == []
    # the model can read the manifest back
    rc, man = box.json("tools", "stubsrv")
    assert {t["name"] for t in man["tools"]} == {"alpha", "run_shell", "danger_zone"}


def test_refresh_detects_schema_change(box, tmp_path):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps([{"name": "alpha", "description": "a", "inputSchema": {"type": "object"}}]))
    box.run("refresh", "--no-introspect", check=0)
    box.run("refresh", extra_env={"STUB_TOOLS_FILE": str(tools)}, check=0)
    tools.write_text(json.dumps([{"name": "alpha", "description": "a",
                                  "inputSchema": {"type": "object", "required": ["path"]}}]))
    cp = box.run("refresh", extra_env={"STUB_TOOLS_FILE": str(tools)}, check=0)
    assert "~ alpha: inputSchema changed" in cp.stdout


def test_refresh_reports_an_update_but_never_moves_the_pin(box, registry):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    registry.version = "1.2.0"
    cp = box.run("refresh", extra_env=registry.env(), check=0)
    assert "UPDATE AVAILABLE: 1.0.0 → 1.2.0" in cp.stdout
    assert "scripts/apps.sh upgrade stubsrv" in cp.stdout
    e = box.read_config()["mcp"]["servers"]["stubsrv"]
    assert e["command"][-1] == "stubpkg@1.0.0"
    assert not (box.state / "pins.json").exists()


def test_offline_refresh_degrades_cleanly(box):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    rc, out = box.json("refresh")                       # island probe → closed port
    assert rc == 0, out
    assert out["offline"] is True and "no internet route" in out["offline_reason"]
    assert out["servers"][0]["latest"] is None
    cp = box.run("refresh")
    assert "beast-island: OFFLINE" in cp.stdout and "Pins are kept exactly as they are" in cp.stdout
    assert "latest: not checked (offline) — keeping 1.0.0" in cp.stdout
    # introspection asked npx for the cache only
    assert box.argv_log.read_text().splitlines()[-1].startswith("--offline -y stubpkg@1.0.0")
    # OFFLINE=true (the install-level switch) is honoured too, without probing
    rc, out = box.json("refresh", "--no-introspect",
                       extra_env={"OFFLINE": "true", "OPENBEAST_ISLAND_PROBES": "1.1.1.1:443"})
    assert out["offline_reason"].startswith("OFFLINE=true")


def test_upgrade_is_explicit_reprobes_and_rewrites_the_entry(box, registry):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    box.run("refresh", extra_env={**registry.env(), "STUB_TOOLS": "alpha"}, check=0)
    # offline without --to: refuses, says why
    cp = box.run("upgrade", "stubsrv")
    assert cp.returncode == 5 and "beast-island: OFFLINE" in cp.stdout
    registry.version = "1.1.0"
    rc, out = box.json("upgrade", "stubsrv", extra_env={**registry.env(), "STUB_TOOLS": "alpha,gamma"})
    assert rc == 0, out
    assert out["from"] == "1.0.0" and out["to"] == "1.1.0" and out["config_updated"]
    assert out["diff"]["added"] == ["gamma"]
    assert json.loads((box.state / "pins.json").read_text())["stubsrv"]["version"] == "1.1.0"
    assert box.read_config()["mcp"]["servers"]["stubsrv"]["command"][-1] == "stubpkg@1.1.0"
    assert (box.state / "manifests" / "stubsrv" / "1.1.0.json").exists()
    assert (box.state / "manifests" / "stubsrv" / "1.0.0.json").exists()   # history kept
    rc, out = box.json("refresh", extra_env=registry.env())
    assert out["servers"][0]["pinned"] == "1.1.0" and not out["servers"][0]["update_available"]


def test_upgrade_aborts_when_the_new_version_does_not_answer(box):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    cp = box.run("upgrade", "stubsrv", "--to", "2.0.0",
                 extra_env={"STUB_HANG": "1", "OPENBEAST_APPS_TIMEOUT": "2"})
    assert cp.returncode == 1 and "Pin stays 1.0.0" in cp.stdout and "timed out" in cp.stdout
    assert not (box.state / "pins.json").exists()
    assert box.read_config()["mcp"]["servers"]["stubsrv"]["command"][-1] == "stubpkg@1.0.0"


def test_upgrade_refuses_when_prerequisites_are_gone(box):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    (box.bin / "npx").unlink()
    cp = box.run("upgrade", "stubsrv", "--to", "1.1.0")
    assert cp.returncode == 3 and "Refusing to upgrade" in cp.stdout


# ── the MCP client itself ───────────────────────────────────────────────────
def test_mcp_client_pages_and_tolerates_banners(box):
    sys.path.insert(0, str(REPO / "agents" / "apps"))
    import mcp_client
    env = {**os.environ, "STUB_TOOLS": ",".join(f"t{i}" for i in range(7)), "STUB_PAGE_SIZE": "3",
           "STUB_BANNER": "1"}
    info, tools = mcp_client.list_tools_stdio([PY, str(STUB), "x@9.9.9"], env, timeout=20)
    assert [t["name"] for t in tools] == [f"t{i}" for i in range(7)]
    assert info["serverInfo"]["version"] == "9.9.9"


def test_mcp_client_times_out(box):
    sys.path.insert(0, str(REPO / "agents" / "apps"))
    import mcp_client
    with pytest.raises(mcp_client.MCPError, match="timed out"):
        mcp_client.list_tools_stdio([PY, str(STUB)], {**os.environ, "STUB_HANG": "1"}, timeout=1.5)


# ── wrapper, skill, docs ────────────────────────────────────────────────────
def test_wrapper_runs_validate(box):
    cp = subprocess.run(["bash", str(WRAPPER), "validate"], capture_output=True, text=True,
                        env={**box.env, "OPENBEAST_PYTHON": PY, "OPENBEAST_APPS_CATALOG": str(CATALOG)})
    assert cp.returncode == 0, cp.stderr
    assert "catalog OK" in cp.stdout


def test_skill_is_registered_and_off_the_always_on_menu():
    skill = (REPO / "skills" / "app-control" / "SKILL.md").read_text()
    fm = skill.split("---")[1]
    assert re.search(r"^name: app-control$", fm, re.M)
    assert re.search(r"^prompt_index: false$", fm, re.M)
    assert "scripts/apps.sh check" in skill and "--json" in skill
    names = (REPO / "skills" / "IN_HOUSE_SKILLS.txt").read_text().split()
    assert "app-control" in names
    assert "app-control" not in (REPO / "system-prompt-tools.md").read_text()
    assert (REPO / "docs" / "BEAST_APPS.md").exists()


# ── safety gates against the real pinned tool lists ─────────────────────────
# tests/fixtures/apps/<server>-<version>.tools.json are the names and
# descriptions the pinned upstream releases answer tools/list with (read off
# the real packages, 2026-11-15). A gate that names no real tool, or a tool
# that says it runs code with no gate, fails here instead of on a user's box.
FIX = REPO / "tests" / "fixtures" / "apps"


def _apps():
    sys.path.insert(0, str(REPO / "agents" / "apps"))
    import catalog as C
    import cli
    return C, cli


def _real(sid: str):
    C, cli = _apps()
    cat = C.load(str(CATALOG), env={}, overlays=False)
    s = cat["server"][sid]
    tools = json.loads((FIX / f"{sid}-{s['version']}.tools.json").read_text())
    man = cli.make_manifest(sid, s["version"], "stdio", {}, tools)
    return s, {t["name"] for t in tools}, cli.diff_manifests(None, man, s)


def _gated(s: dict, name: str) -> bool:
    import fnmatch
    return any(fnmatch.fnmatchcase(name, g) for g in s["ask_tools"] + s["deny_tools"])


@pytest.mark.parametrize("sid", ["mcp-3d-printer-server", "replicant-mcp", "mobilebuildmcp"])
def test_every_gate_names_a_real_tool(sid):
    import fnmatch
    s, names, _ = _real(sid)
    for g in s["ask_tools"] + s["deny_tools"]:
        assert any(fnmatch.fnmatchcase(n, g) for n in names), f"{sid}: gate {g!r} matches no tool"


def test_3d_print_gates_its_code_and_install_bridges():
    s, names, d = _real("mcp-3d-printer-server")
    # blender_mcp_call reaches execute_blender_code; fusion_mcp_call reaches
    # execute_code; fusion_mcp_setup installs an add-in + a LaunchAgent.
    for t in ("blender_mcp_call", "blender_mcp_setup", "blender_mcp_edit_model", "fusion_mcp_call",
              "fusion_mcp_setup", "cad_run", "cad_setup", "cancel_print", "fulu_bambu_network_rpc",
              "start_print", "print_3mf", "set_printer_temperature"):
        assert t in names and _gated(s, t), t
    assert not _gated(s, "pause_print")                   # the stop button stays one call away
    loud = [f["name"] for f in d["flagged"] if f["description"] and not f["gated"]]
    assert loud == [], f"tools that say they run code, ungated: {loud}"
    for t in ("blender_mcp_call", "fusion_mcp_call", "fusion_mcp_setup"):
        assert next(f for f in d["flagged"] if f["name"] == t)["description"]


def test_3d_print_bridge_programs_come_from_the_shell_never_a_project_env(box):
    # upstream dotenv.config() reads the cwd's .env but never overrides a
    # variable that is already set: pinning them empty closes that path.
    env = {"PRINTER_TYPE": "octoprint", "PRINTER_HOST": "10.0.0.5"}
    rc, out = box.json("enable", "3d-print", "--accept-risk", "--dry-run", extra_env=env)
    assert rc == 0, out
    e = out["entry"]["environment"]
    for v in ("BLENDER_MCP_COMMAND", "BLENDER_MCP_ARGS", "FUSION_MCP_COMMAND", "FULU_BAMBU_BRIDGE_COMMAND",
              "SLICER_PATH"):
        assert e[v] == "", v
    rc, out = box.json("enable", "3d-print", "--accept-risk", "--dry-run",
                       extra_env={**env, "BLENDER_MCP_COMMAND": "/usr/bin/uvx"})
    assert out["entry"]["environment"]["BLENDER_MCP_COMMAND"] == "/usr/bin/uvx"


def test_first_refresh_flags_an_ungated_tool_that_says_it_runs_code(box, tmp_path):
    box.stub_server(risk=("shell",), ask=("danger*",))
    box.run("enable", "stub-cap", check=0)
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps([
        {"name": "alpha", "description": "read a widget", "inputSchema": {"type": "object"}},
        {"name": "bridge_call", "description": "Call a tool on the bridge; execute_code accepts Python code.",
         "inputSchema": {"type": "object"}},
        {"name": "danger_exec", "description": "Runs arbitrary code.", "inputSchema": {"type": "object"}},
    ]))
    cp = box.run("refresh", extra_env={"STUB_TOOLS_FILE": str(tools)}, check=0)
    tail = cp.stdout.split("first manifest: 3 tools")[1]
    assert "tool bridge_call describes itself as running code" in tail and "NOT gated" in tail
    assert "danger_exec" not in tail and "alpha" not in tail           # gated / harmless: quiet


def test_enable_refuses_when_v1_permission_is_a_string(box):
    box.android_sdk_off_path()
    box.write_config({"permission": "allow", "mcp": {}})
    before = box.config.read_text()
    v1 = {"OPENBEAST_OPENCODE_SCHEMA": "v1"}
    cp = box.run("enable", "android-build", extra_env=v1)
    assert cp.returncode == 6, cp.stdout + cp.stderr
    assert "Refusing to enable replicant-mcp" in cp.stdout and "--ungated" in cp.stdout
    assert "gated:" not in cp.stdout
    assert box.config.read_text() == before and box.backups() == []
    assert not (box.state / "enabled.json").exists()
    # explicit opt-out: the entry goes in, and no gate is claimed or recorded
    rc, out = box.json("enable", "android-build", "--ungated", extra_env=v1)
    assert rc == 0, out
    assert out["rules"] == [] and out["ungated"] and any("NOT gated" in w for w in out["warnings"])
    cfg = box.read_config()
    assert cfg["permission"] == "allow" and "replicant-mcp" in cfg["mcp"]
    assert json.loads((box.state / "enabled.json").read_text())["replicant-mcp"]["rules"] == []
    cp = box.run("enable", "android-build", "--ungated", extra_env=v1)
    assert cp.returncode == 0 and "  gated: " not in cp.stdout
