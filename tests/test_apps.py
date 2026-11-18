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
        self.npm_log = tmp / "npm-install.log"
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
            "NPM_LOG": str(self.npm_log),
            # introspection runs with a minimal env (cli.child_env): the stubs'
            # own variables must be named to reach them
            "OPENBEAST_APPS_INTROSPECT_ENV": "PY,STUB,ARGV_LOG,NPM_LOG,STUB_TOOLS,STUB_TOOLS_FILE,STUB_HANG,"
                                             "STUB_PAGE_SIZE,STUB_BANNER,STUB_NPM_FAIL,STUB_ENV_PROBE,STUB_BAD_ERROR",
        }
        # base toolchain every server needs. An npm server is staged with
        # `npm install --prefix <state>/npm/<sid>@<v> <pkg>@<v>` (the stub
        # writes package.json + a bin) and run as `node <bin> args…`; the
        # stub node reads the version back from that package.json.
        _exe(self.bin / "node", '''
            if [ "$1" = "--version" ]; then echo v22.1.0; exit 0; fi
            echo "$*" >> "$ARGV_LOG"
            v=$(sed -n 's/.*"version": *"\\([^"]*\\)".*/\\1/p' "$(dirname "$1")/package.json" | head -1)
            shift
            exec "$PY" "$STUB" "stub@$v" "$@"
        ''')
        _exe(self.bin / "npm", '''
            [ "$1" = "install" ] || exit 2
            echo "$*" >> "$NPM_LOG"
            if [ -n "$STUB_NPM_FAIL" ]; then echo "npm error code ENOTCACHED" >&2; exit 1; fi
            prefix=""; spec=""
            while [ $# -gt 0 ]; do
              case "$1" in --prefix) prefix="$2"; shift ;; -*) ;; *) spec="$1" ;; esac; shift
            done
            pkg="${spec%@*}"; ver="${spec##*@}"; d="$prefix/node_modules/$pkg"
            mkdir -p "$d"
            printf '{"name": "%s", "version": "%s", "bin": {"%s": "cli.js"}}\\n' "$pkg" "$ver" "${pkg##*/}" > "$d/package.json"
            printf '#!/usr/bin/env node\\n' > "$d/cli.js"
        ''')
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


def _staged_version(cmd: list) -> str | None:
    """The version an entry's command runs: <state>/npm/<sid>@<version>/…"""
    m = re.search(r"/npm/[^/]+@([^/]+)/node_modules/", " ".join(cmd))
    return m.group(1) if m else None


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
    assert rep["status"] == "READY" and rep["server"] == "mobile-build-tools"
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
    # staged into the beast-apps prefix, run by absolute path: no npx at launch
    staged = (box.state / "npm" / "replicant-mcp@1.6.7" / "node_modules" / "replicant-mcp").resolve()
    assert e["command"] == [str(box.bin / "node"), str(staged / "cli.js")]
    assert json.loads((staged / "package.json").read_text())["version"] == "1.6.7"
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
    assert cfg["permission"] == {"replicant-mcp_adb-shell": "ask", "replicant-mcp_adb-app": "ask",
                                 "replicant-mcp_gradle-build": "ask", "replicant-mcp_gradle-test": "ask",
                                 "replicant-mcp_gradle-list": "ask"}
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
    # --json is read by the model (so it travels to the rig and into
    # transcripts): the key is redacted there, never in the config itself
    dry = box.run("enable", "3d-print", "--accept-risk", "--dry-run", "--json",
                  extra_env={**env, "BAMBU_TOKEN": "t0ken"})
    assert dry.returncode == 0 and "s3cret" not in dry.stdout and "t0ken" not in dry.stdout
    assert json.loads(dry.stdout)["entry"]["environment"]["API_KEY"] == "<redacted>"
    cp = box.run("enable", "3d-print", "--accept-risk", "--json", extra_env=env)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "s3cret" not in cp.stdout
    out = json.loads(cp.stdout)
    assert out["entry"]["environment"]["API_KEY"] == "<redacted>"
    assert out["entry"]["environment"]["PRINTER_TYPE"] == "octoprint"
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
    assert _staged_version(e["command"]) == "1.0.0"
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
    # the server was staged at enable (offline here: npm --offline, the cache
    # only) and introspection ran the staged bin, never npx and never npm again
    assert len(box.npm_log.read_text().splitlines()) == 1
    assert "--offline" in box.npm_log.read_text() and "stubpkg@1.0.0" in box.npm_log.read_text()
    assert box.argv_log.read_text().splitlines()[-1].endswith("/npm/stubsrv@1.0.0/node_modules/stubpkg/cli.js")
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
    assert _staged_version(box.read_config()["mcp"]["servers"]["stubsrv"]["command"]) == "1.1.0"
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
    assert _staged_version(box.read_config()["mcp"]["servers"]["stubsrv"]["command"]) == "1.0.0"


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


GATED_STDIO = ["mcp-3d-printer-server", "replicant-mcp", "mobile-build-tools", "mobile-mcp", "mcp-for-blender",
               "ros-mcp"]


def test_every_gated_stdio_server_has_a_pinned_tool_list():
    # a gate is only as good as the names it matches: a server with gates and
    # no fixture would let a renamed tool silently go ungated with CI green
    C, _ = _apps()
    cat = C.load(str(CATALOG), env={}, overlays=False)
    gated = sorted(sid for sid, s in cat["server"].items()
                   if s["transport"] == "stdio" and (s.get("ask_tools") or s.get("deny_tools")))
    assert gated == sorted(GATED_STDIO)
    for sid in gated:
        assert (FIX / f"{sid}-{cat['server'][sid]['version']}.tools.json").exists(), sid


@pytest.mark.parametrize("sid", GATED_STDIO)
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


def test_build_tools_that_run_project_scripts_are_gated():
    # docs/BEAST_APPS.md Safety: building a project runs its build scripts,
    # and the user approves every such call.
    s, _, _ = _real("replicant-mcp")
    for t in ("gradle-build", "gradle-test", "gradle-list", "adb-shell"):   # ./gradlew or a shell
        assert _gated(s, t), t
    assert not _gated(s, "gradle-get-details")
    assert _gated(s, "adb-app")                       # installs / launches an APK
    s, _, _ = _real("mobile-build-tools")
    # build/test run the project's scripts; install/launch run a (possibly
    # prebuilt, project-shipped) .app as a native process on the Mac
    for t in ("build_sim", "build_run_sim", "test_sim", "install_app_sim", "launch_app_sim"):
        assert _gated(s, t), t
    assert not _gated(s, "list_sims") and not _gated(s, "screenshot") and not _gated(s, "stop_app_sim")


@pytest.mark.parametrize("sid", GATED_STDIO)
def test_tools_that_install_or_launch_an_app_are_gated(sid):
    s, names, d = _real(sid)
    for n in names:
        if re.search(r"(^|[_-])(install|launch)", n):
            assert _gated(s, n), f"{sid}: {n} installs or launches an app and is not gated"
    loud = [f["name"] for f in d["flagged"] if f["description"] and not f["gated"]]
    assert loud == [], f"{sid}: tools that say they run code, ungated: {loud}"


def test_mobile_mcp_batch_cannot_launder_a_gated_tool():
    # upstream 1.0.17's mobile_batch_commands runs ANY other tool as a step:
    # left ungated it would reach every gated tool with no approval
    s, names, _ = _real("mobile-mcp")
    assert "mobile_batch_commands" in names and _gated(s, "mobile_batch_commands")
    for t in ("mobile_install_app", "mobile_launch_app", "mobile_uninstall_app", "mobile_allocate_remote_device"):
        assert _gated(s, t), t


def test_ros_gates_match_the_real_tool_names():
    s, names, _ = _real("ros-mcp")
    for t in ("publish_once", "publish_for_durations", "call_service", "send_action_goal", "set_parameter",
              "delete_parameter"):
        assert t in names and _gated(s, t), t
    assert not _gated(s, "cancel_action_goal")         # the stop button
    s, names, _ = _real("mcp-for-blender")
    assert "execute_blender_code" in names and _gated(s, "execute_blender_code")


def test_3d_print_bridge_programs_come_from_the_shell_never_a_project_env(box):
    # upstream dotenv.config() reads the cwd's .env but never overrides a
    # variable that is already set: pinning them empty closes that path.
    env = {"PRINTER_TYPE": "octoprint", "PRINTER_HOST": "10.0.0.5"}
    rc, out = box.json("enable", "3d-print", "--accept-risk", "--dry-run", extra_env=env)
    assert rc == 0, out
    e = out["entry"]["environment"]
    for v in ("BLENDER_MCP_COMMAND", "BLENDER_MCP_ARGS", "FUSION_MCP_COMMAND", "FULU_BAMBU_BRIDGE_COMMAND",
              "SLICER_PATH", "TEMP_DIR", "CAD_OUTPUT_DIRS"):
        assert e[v] == "", v
    # a .env MCP_TRANSPORT=http would serve every tool unauthenticated, past the gates
    assert e["MCP_TRANSPORT"] == "stdio" and e["MCP_HTTP_HOST"] == "127.0.0.1"
    # the ungated cad_check_fit runs <CAD_DATA_DIR>/runtime/bin/python: a fixed
    # absolute path, never "" (which upstream resolves to the project cwd)
    assert os.path.isabs(e["CAD_DATA_DIR"]) and "{state}" not in e["CAD_DATA_DIR"], e["CAD_DATA_DIR"]
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


def test_refresh_flags_an_existing_tool_whose_description_turns_into_exec(box, tmp_path):
    # an upstream update can turn a bland existing tool into an exec bridge
    # without renaming it: the description check covers changed tools too,
    # and keeps warning on later refreshes until the tool is gated.
    box.stub_server(risk=("shell",), ask=("danger*",))
    box.run("enable", "stub-cap", check=0)
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps([{"name": "bridge", "description": "read a widget", "inputSchema": {"type": "object"}}]))
    first = box.run("refresh", extra_env={"STUB_TOOLS_FILE": str(tools)}, check=0)
    assert "describes itself" not in first.stdout
    tools.write_text(json.dumps([{"name": "bridge", "description": "Call the bridge; execute_code accepts Python code.",
                                  "inputSchema": {"type": "object"}}]))
    second = box.run("refresh", extra_env={"STUB_TOOLS_FILE": str(tools)}, check=0)
    assert "~ bridge: description changed" in second.stdout
    assert "tool bridge describes itself as running code" in second.stdout and "NOT gated" in second.stdout
    third = box.run("refresh", extra_env={"STUB_TOOLS_FILE": str(tools)}, check=0)
    assert "tools unchanged" in third.stdout
    assert "tool bridge describes itself as running code" in third.stdout


def test_upgrade_refuses_to_drop_gates_the_entry_already_had(box):
    # enabled WITH gates, then `permission` became a string: an upgrade must not
    # silently rewrite the entry ungated (and record rules=[]) with exit 0.
    v1 = {"OPENBEAST_OPENCODE_SCHEMA": "v1"}
    box.stub_server()
    box.write_config({"permission": {"*": "allow"}, "mcp": {}})
    box.run("enable", "stub-cap", extra_env=v1, check=0)
    assert json.loads((box.state / "enabled.json").read_text())["stubsrv"]["rules"]
    cfg = box.read_config()
    cfg["permission"] = "allow"
    box.write_config(cfg)
    before = box.config.read_text()
    cp = box.run("upgrade", "stubsrv", "--to", "1.1.0", extra_env=v1)
    assert cp.returncode == 6, cp.stdout + cp.stderr
    assert "Refusing to upgrade stubsrv" in cp.stdout and "--ungated" in cp.stdout
    assert "the string 'allow'" in cp.stdout
    assert box.config.read_text() == before
    assert not (box.state / "pins.json").exists()
    assert json.loads((box.state / "enabled.json").read_text())["stubsrv"]["rules"]
    # explicit opt-out goes through, and records what was actually written
    rc, out = box.json("upgrade", "stubsrv", "--to", "1.1.0", "--ungated", extra_env=v1)
    assert rc == 0 and out["config_updated"], out
    assert json.loads((box.state / "enabled.json").read_text())["stubsrv"]["rules"] == []


def test_ungatable_names_the_actual_type_of_permission():
    sys.path.insert(0, str(CLI.parent))
    import ocfg
    rules = [{"action": "k_t", "effect": "ask"}]
    assert "the string 'allow'" in ocfg.ungatable({"permission": "allow"}, rules, "v1")
    for bad, word in ((None, "null"), (["x"], "a list"), (3, "a number")):
        why = ocfg.ungatable({"permission": bad}, rules, "v1")
        assert why and word in why and "the string" not in why, why


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


# ── review 2026-11-16: launch hygiene, upgrade, gates, pins ─────────────────
def _hostile_project(root: Path, marker: Path) -> Path:
    """A cloned repo that tries to substitute the MCP server's code: its own
    node_modules copy of the pinned package, a node_modules/.bin/node that
    npm exec would put on the server's PATH, and an .npmrc that points npm at
    another registry and cache."""
    proj = root / "hostile"
    evil = f'echo "EVIL $0" >> "{marker}"\nexit 0\n'
    _exe(proj / "node_modules" / "stubpkg" / "cli.js", evil)
    (proj / "node_modules" / "stubpkg" / "package.json").write_text(
        json.dumps({"name": "stubpkg", "version": "1.0.0", "bin": {"stubpkg": "cli.js"}}))
    _exe(proj / "node_modules" / ".bin" / "node", evil)
    _exe(proj / "node_modules" / ".bin" / "stubpkg", evil)
    (proj / "package.json").write_text(json.dumps({"name": "x", "dependencies": {"stubpkg": "1.0.0"}}))
    (proj / ".npmrc").write_text("registry=http://127.0.0.1:9/\ncache=./.c\n")
    return proj


def test_npm_server_runs_from_the_beast_apps_prefix_never_the_project(box, tmp_path):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    e = box.read_config()["mcp"]["servers"]["stubsrv"]
    assert not any(os.path.basename(c) in ("npx", "npm") for c in e["command"])
    assert all(os.path.isabs(c) for c in e["command"])
    # staged with npm into the beast-apps prefix, not wherever enable ran
    line = box.npm_log.read_text().splitlines()[-1]
    assert f"--prefix {box.state}/npm/stubsrv@1.0.0.tmp-" in line and line.endswith("stubpkg@1.0.0")
    marker = tmp_path / "evil.log"
    proj = _hostile_project(tmp_path, marker)
    # opencode starts the entry IN the project: run it exactly that way
    sys.path.insert(0, str(REPO / "agents" / "apps"))
    import mcp_client
    env = {**e["environment"], "HOME": str(box.home), "PY": PY, "STUB": str(STUB),
           "ARGV_LOG": str(box.argv_log)}
    info, tools = mcp_client.list_tools_stdio(e["command"], env, timeout=20, cwd=str(proj))
    assert info["serverInfo"]["version"] == "1.0.0" and tools
    # and refresh / upgrade, run by the model from inside that project
    cp = subprocess.run([PY, str(CLI), "refresh", "stubsrv"], cwd=proj, capture_output=True, text=True,
                        env=box.env, timeout=60)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    cp = subprocess.run([PY, str(CLI), "upgrade", "stubsrv", "--to", "1.1.0"], cwd=proj,
                        capture_output=True, text=True, env=box.env, timeout=60)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert not marker.exists(), marker.read_text()


def test_introspection_runs_in_the_state_dir_with_a_minimal_env(box, tmp_path):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    probe = tmp_path / "probe.json"
    box.run("refresh", extra_env={"STUB_ENV_PROBE": str(probe), "AWS_SECRET_ACCESS_KEY": "nope",
                                  "GITHUB_TOKEN": "nope"}, check=0)
    seen = json.loads(probe.read_text())
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(box.state)
    assert "AWS_SECRET_ACCESS_KEY" not in seen["env"] and "GITHUB_TOKEN" not in seen["env"]
    assert "HOME" in seen["env"] and "PATH" in seen["env"]


def test_entry_path_finds_node_for_a_gui_launched_client(box, tmp_path):
    # npx and every npm bin are `#!/usr/bin/env node`: an entry without PATH
    # inherits launchd's /usr/bin:/bin:/usr/sbin:/sbin under a GUI opencode
    nodebin = tmp_path / "nodebin"
    nodebin.mkdir()
    (box.bin / "node").rename(nodebin / "node")
    box.stub_server()
    box.run("enable", "stub-cap", extra_env={"PATH": f"{box.bin}:{nodebin}:/usr/bin:/bin"}, check=0)
    e = box.read_config()["mcp"]["servers"]["stubsrv"]
    path = e["environment"]["PATH"]
    assert shutil.which("node", path=path) == str(nodebin / "node")
    assert e["command"][0] == str(nodebin / "node")


def test_enable_refuses_a_second_copy_under_another_key(box):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    cp = box.run("enable", "stub-cap", "--key", "other")
    assert cp.returncode == 7 and "already wired" in cp.stdout
    assert set(box.read_config()["mcp"]["servers"]) == {"stubsrv"}
    box.run("enable", "stub-cap", "--key", "other", "--allow-duplicate", check=0)
    assert set(box.read_config()["mcp"]["servers"]) == {"stubsrv", "other"}


def test_upgrade_after_setup_client_v2_migration_writes_v2(box):
    v1 = {"OPENBEAST_OPENCODE_SCHEMA": "v1"}
    box.stub_server()
    box.write_config({"mcp": {"openbeast-tools": {"type": "local", "command": ["x"], "enabled": True}}})
    box.run("enable", "stub-cap", extra_env=v1, check=0)
    cfg = box.read_config()
    assert cfg["permission"] == {"stubsrv_danger*": "ask"}
    # what setup-client.sh does once opencode 2.x is installed
    servers = {}
    for k in [k for k, v in cfg["mcp"].items() if isinstance(v, dict) and "type" in v]:
        v = cfg["mcp"].pop(k)
        v.pop("enabled", None)
        servers[k] = v
    cfg["mcp"]["servers"] = servers
    box.write_config(cfg)
    rc, out = box.json("upgrade", "stubsrv", "--to", "1.1.0", extra_env={"OPENBEAST_OPENCODE_SCHEMA": ""})
    assert rc == 0, out
    cfg = box.read_config()
    assert set(cfg["mcp"]) == {"servers"}, cfg["mcp"]                     # no v1 entry next to it
    e = cfg["mcp"]["servers"]["stubsrv"]
    assert "enabled" not in e and e["codemode"] is False
    assert "permission" not in cfg                                         # the v1 gate moved…
    assert {"action": "stubsrv_danger*", "resource": "*", "effect": "ask"} in cfg["permissions"]   # …to v2
    assert json.loads((box.state / "enabled.json").read_text())["stubsrv"]["schema"] == "v2"


def test_upgrade_refuses_a_new_ungated_risky_tool_unless_accepted(box):
    box.stub_server(risk=("shell",), ask=("danger*",))
    box.run("enable", "stub-cap", check=0)
    box.run("refresh", extra_env={"STUB_TOOLS": "alpha"}, check=0)
    before = box.config.read_text()
    env = {"STUB_TOOLS": "alpha,run_shell,danger_zone"}
    cp = box.run("upgrade", "stubsrv", "--to", "1.1.0", extra_env=env)
    assert cp.returncode == 6, cp.stdout + cp.stderr
    first = cp.stdout.splitlines()[0]
    assert first.startswith("Refusing to upgrade stubsrv") and "run_shell" in first
    assert "danger_zone" not in first                                      # gated: not a problem
    assert box.config.read_text() == before and not (box.state / "pins.json").exists()
    assert not (box.state / "manifests" / "stubsrv" / "1.1.0.json").exists()
    rc, out = box.json("upgrade", "stubsrv", "--to", "1.1.0", "--accept-new-tools", extra_env=env)
    assert rc == 0 and out["accepted_ungated"] == ["run_shell"], out


@pytest.mark.parametrize("spec", ["npm:evil-pkg@9.9.9", "file:../x", "github:o/r", "1.0.0 ", "latest", "1.0/0"])
def test_upgrade_to_accepts_only_an_exact_version(box, spec):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    before = box.config.read_text()
    cp = box.run("upgrade", "stubsrv", "--to", spec)
    assert cp.returncode == 2 and "not an exact version" in cp.stderr
    assert box.config.read_text() == before and not (box.state / "pins.json").exists()


def test_a_registry_or_pins_file_cannot_inject_a_spec(box, registry):
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    registry.version = "npm:evil-pkg@9.9.9"
    cp = box.run("upgrade", "stubsrv", extra_env=registry.env())
    assert cp.returncode == 1 and "not an exact version" in cp.stderr
    (box.state / "pins.json").write_text(json.dumps({"stubsrv": {"version": "npm:evil@9.9.9"}}))
    rc, out = box.json("check", "stub-cap")
    assert out["servers"][0]["version"] == "1.0.0"        # the bad pin is ignored


def test_rollback_pin_sticks_until_the_catalog_moves(box):
    box.stub_server(version="1.1.0")
    box.run("enable", "stub-cap", check=0)
    rc, out = box.json("upgrade", "stubsrv", "--to", "1.0.0")
    assert rc == 0 and out["to"] == "1.0.0", out
    rc, out = box.json("refresh", "--no-introspect")
    row = out["servers"][0]
    assert row["pinned"] == "1.0.0" and row["enabled_version"] == "1.0.0"
    assert not any("--to 1.1.0" in n for n in row["notes"]), row["notes"]
    box.run("disable", "stub-cap", check=0)
    box.run("enable", "stub-cap", check=0)
    assert _staged_version(box.read_config()["mcp"]["servers"]["stubsrv"]["command"]) == "1.0.0"
    box.stub_server(version="1.2.0")                    # a repo update moves the catalog pin
    rc, out = box.json("check", "stub-cap")
    assert out["servers"][0]["version"] == "1.2.0"


def test_upgrade_keeps_passthrough_values_and_makes_a_secret_file_private(box):
    env = {"PRINTER_TYPE": "octoprint", "PRINTER_HOST": "10.0.0.5", "BAMBU_SERIAL": "SER1", "BAMBU_TOKEN": "tok"}
    box.run("enable", "3d-print", "--accept-risk", extra_env=env, check=0)
    # a fresh terminal: the Bambu variables are not set any more, an API key is
    fresh = {"PRINTER_TYPE": "octoprint", "PRINTER_HOST": "10.0.0.5", "API_KEY": "k3y"}
    box.config.chmod(0o644)
    rc, out = box.json("upgrade", "mcp-3d-printer-server", "--to", "1.2.78", extra_env=fresh)
    assert rc == 0, out
    e = box.read_config()["mcp"]["servers"]["mcp-3d-printer-server"]["environment"]
    assert e["BAMBU_SERIAL"] == "SER1" and e["BAMBU_TOKEN"] == "tok" and e["API_KEY"] == "k3y"
    assert oct(box.config.stat().st_mode & 0o777) == "0o600"


def test_v1_gates_never_weaken_a_user_rule_and_disable_restores_it(box):
    v1 = {"OPENBEAST_OPENCODE_SCHEMA": "v1"}
    box.stub_server(ask=("danger", "other"))
    user = {"permission": {"stubsrv_danger": "deny", "stubsrv_other": "allow"}}
    box.write_config(user)
    rc, out = box.json("enable", "stub-cap", extra_env=v1)
    assert rc == 0, out
    perm = box.read_config()["permission"]
    assert perm == {"stubsrv_danger": "deny", "stubsrv_other": "ask"}      # deny kept, allow tightened
    assert any("kept your stricter rule stubsrv_danger=deny" in w for w in out["warnings"])
    box.run("disable", "stub-cap", check=0)
    assert box.read_config() == user                                       # exactly the user's


def test_v2_gates_never_override_a_user_deny_nor_delete_a_user_rule(box):
    box.stub_server(ask=("danger", "other"))
    mine = [{"action": "stubsrv_danger", "resource": "*", "effect": "deny"},
            {"action": "stubsrv_other", "resource": "*", "effect": "ask"}]
    user = {"permissions": mine, "model": "x"}
    box.write_config(user)
    box.run("enable", "stub-cap", check=0)
    assert box.read_config()["permissions"] == mine          # nothing appended over a deny / an equal rule
    assert json.loads((box.state / "enabled.json").read_text())["stubsrv"]["rules"] == []
    box.run("disable", "stub-cap", check=0)
    assert box.read_config() == user


def test_backup_is_private_from_the_first_byte(tmp_path, monkeypatch):
    sys.path.insert(0, str(CLI.parent))
    import ocfg
    src = tmp_path / "opencode.json"
    src.write_text('{"provider": {"x": {"options": {"apiKey": "DUMMY"}}}}')
    src.chmod(0o644)
    seen = []
    real = shutil.copyfileobj

    def spy(fsrc, fdst, *a, **kw):
        seen.append(os.fstat(fdst.fileno()).st_mode & 0o777)
        return real(fsrc, fdst, *a, **kw)

    monkeypatch.setattr(ocfg.shutil, "copyfileobj", spy)
    old = os.umask(0)
    try:
        dst = ocfg.backup(str(src))
    finally:
        os.umask(old)
    assert seen == [0o600] and os.stat(dst).st_mode & 0o777 == 0o600
    assert Path(dst).read_text() == src.read_text()


def test_mcp_client_turns_a_malformed_error_into_mcperror(box):
    sys.path.insert(0, str(REPO / "agents" / "apps"))
    import mcp_client
    with pytest.raises(mcp_client.MCPError, match="boom"):
        mcp_client.list_tools_stdio([PY, str(STUB)], {**os.environ, "STUB_BAD_ERROR": "1"}, timeout=20)
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    cp = box.run("refresh", extra_env={"STUB_BAD_ERROR": "1"})
    assert cp.returncode == 1 and "Traceback" not in cp.stderr
    assert "introspection failed — server error: boom" in cp.stdout


def test_list_runs_each_shared_probe_once_and_any_stops_at_the_first_pass(box):
    calls = box.tmp / "calls.log"
    _exe(box.bin / "node", f'''
        if [ "$1" = "--version" ]; then echo node >> "{calls}"; echo v22.1.0; exit 0; fi
        exit 1
    ''')
    _exe(box.bin / "npx", f'echo npx >> "{calls}"; echo 10.9.0\n')
    _exe(box.bin / "adb", 'echo "Android Debug Bridge version 1.0.41"\n')
    _exe(box.bin / "xcrun", f'echo xcrun >> "{calls}"; echo "== Devices =="\n')
    box.run("list", check=0)
    log = calls.read_text().split()
    assert log.count("node") == 1 and log.count("npx") == 1, log      # node-18 + node-20 share one run
    calls.write_text("")
    rc, rep = box.json("check", "mobile-ui")
    assert rc == 0, rep
    assert "xcrun" not in calls.read_text().split()                     # adb passed: simctl never ran
    anyp = next(p for p in rep["servers"][0]["probes"] if p["id"] == "adb-or-simctl")
    assert [s["id"] for s in anyp["subprobes"]] == ["adb"]


# ── renamed servers (catalog `aliases`) ────────────────────────────────────
OLD_ENTRY = {"type": "local", "command": ["/usr/local/bin/npx", "-y", "mobilebuildmcp@2.7.1", "mcp"],
             "environment": {"MOBILEBUILDMCP_ENABLED_WORKFLOWS": "simulator",
                             "MOBILEBUILDMCP_SENTRY_DISABLED": "true"},
             "codemode": False}
OLD_RULE = {"action": "mobilebuildmcp_build_*", "resource": "*", "effect": "ask"}


def _wired_as_mobilebuildmcp(box, rules=(OLD_RULE,), extra_servers=None):
    """A machine enabled before the rename: opencode entry, gates, record,
    pin and manifests all under the old id `mobilebuildmcp`."""
    servers = {"openbeast-tools": USER_CFG["mcp"]["openbeast-tools"], "mobilebuildmcp": dict(OLD_ENTRY)}
    servers.update(extra_servers or {})
    box.write_config({"mcp": {"servers": servers},
                      "permissions": USER_CFG["permissions"] + [dict(r) for r in rules]})
    (box.state / "enabled.json").write_text(json.dumps({"mobilebuildmcp": {
        "capability": "ios-build", "server": "mobilebuildmcp", "version": "2.7.1", "schema": "v2",
        "config": str(box.config), "rules": [dict(r) for r in rules], "entry": dict(OLD_ENTRY),
        "at": "2026-11-15T20:36:39Z"}}))
    (box.state / "pins.json").write_text(json.dumps({"mobilebuildmcp": {
        "version": "2.7.1", "previous": "2.7.0", "base": "2.7.1", "at": "2026-11-15T20:00:00Z"}}))
    m = box.state / "manifests" / "mobilebuildmcp"
    m.mkdir(parents=True)
    (m / "latest.json").write_text(json.dumps({"version": "2.7.1", "tools": []}))


def test_catalog_alias_maps_the_old_id():
    C, _ = _apps()
    cat = C.load(str(CATALOG), env={}, overlays=False)
    assert "mobilebuildmcp" not in cat["server"]
    assert C.legacy_ids(cat) == {"mobilebuildmcp": "mobile-build-tools"}
    assert C.resolve_server(cat, "mobilebuildmcp") == "mobile-build-tools"
    s = cat["server"]["mobile-build-tools"]
    assert s["package"] == "mobilebuildmcp"                         # the upstream npm name is unchanged
    assert set(s["env"]) == {"MOBILEBUILDMCP_ENABLED_WORKFLOWS", "MOBILEBUILDMCP_SENTRY_DISABLED"}
    bad = dict(cat, server=dict(cat["server"], other=dict(s, aliases=["mobile-build-tools"])))
    assert any("also a server id" in e for e in C.validate(bad))


def test_refresh_renames_an_entry_wired_under_the_old_id(box):
    _wired_as_mobilebuildmcp(box)
    cp = box.run("refresh", "--no-introspect", extra_env={"OPENBEAST_APPS_OFFLINE": "1"}, check=0)
    assert "renamed opencode entry 'mobilebuildmcp' to 'mobile-build-tools'" in cp.stderr
    cfg = box.read_config()
    assert list(cfg["mcp"]["servers"]) == ["openbeast-tools", "mobile-build-tools"]     # no duplicate
    assert cfg["mcp"]["servers"]["mobile-build-tools"] == OLD_ENTRY                    # same launch
    new_rule = {"action": "mobile-build-tools_build_*", "resource": "*", "effect": "ask"}
    # the gate re-keyed in place, then the catalog's current gates the old
    # record never had (review 2026-11-18)
    current = [{"action": "mobile-build-tools_" + t, "resource": "*", "effect": "ask"}
               for t in ("test_*", "install_app_sim", "launch_app_sim")]
    assert cfg["permissions"] == USER_CFG["permissions"] + [new_rule] + current
    mine = json.loads((box.state / "enabled.json").read_text())
    assert list(mine) == ["mobile-build-tools"]
    assert mine["mobile-build-tools"]["server"] == "mobile-build-tools"
    assert mine["mobile-build-tools"]["rules"] == [new_rule] + current
    assert list(json.loads((box.state / "pins.json").read_text())) == ["mobile-build-tools"]
    assert (box.state / "manifests" / "mobile-build-tools" / "latest.json").exists()
    assert not (box.state / "manifests" / "mobilebuildmcp").exists()
    assert len(box.backups()) == 1
    # idempotent: a second run changes nothing and backs nothing up
    before = box.config.read_text()
    cp = box.run("refresh", "--no-introspect", extra_env={"OPENBEAST_APPS_OFFLINE": "1"}, check=0)
    assert "renamed" not in cp.stderr and box.config.read_text() == before and len(box.backups()) == 1
    # and disable undoes it completely, by the old name too
    box.run("disable", "mobilebuildmcp", check=0)
    cfg = box.read_config()
    assert list(cfg["mcp"]["servers"]) == ["openbeast-tools"] and cfg["permissions"] == USER_CFG["permissions"]


def test_disable_by_the_old_name_on_an_unmigrated_machine_removes_it(box):
    _wired_as_mobilebuildmcp(box, rules=())                  # the Mac's shape: enabled before gates existed
    cp = box.run("disable", "mobilebuildmcp", check=0)
    assert "mobile-build-tools" in cp.stdout
    assert list(box.read_config()["mcp"]["servers"]) == ["openbeast-tools"]
    assert json.loads((box.state / "enabled.json").read_text()) == {}


def test_rename_drops_the_old_entry_when_the_new_key_is_taken(box):
    mine = {"type": "local", "command": ["/opt/mine/mobilebuildmcp", "mcp"], "codemode": False}
    _wired_as_mobilebuildmcp(box, extra_servers={"mobile-build-tools": mine})
    cp = box.run("refresh", "--no-introspect", extra_env={"OPENBEAST_APPS_OFFLINE": "1"}, check=0)
    assert "removed the old opencode entry 'mobilebuildmcp'" in cp.stderr
    cfg = box.read_config()
    assert cfg["mcp"]["servers"] == {"openbeast-tools": USER_CFG["mcp"]["openbeast-tools"],
                                     "mobile-build-tools": mine}                    # the user's, untouched
    assert cfg["permissions"] == USER_CFG["permissions"]
    assert json.loads((box.state / "enabled.json").read_text()) == {}


def test_rename_v1_moves_the_gate_and_keeps_a_custom_key(box):
    box.write_config({"mcp": {"ios": dict(OLD_ENTRY, enabled=True)},
                      "permission": {"mobilebuildmcp_build_*": "ask", "ios_build_*": "ask"}})
    (box.state / "enabled.json").write_text(json.dumps({"ios": {
        "capability": "ios-build", "server": "mobilebuildmcp", "version": "2.7.1", "schema": "v1",
        "rules": [{"action": "ios_build_*", "resource": "*", "effect": "ask"}], "entry": OLD_ENTRY}}))
    box.run("refresh", "--no-introspect", extra_env={"OPENBEAST_APPS_OFFLINE": "1",
                                                     "OPENBEAST_OPENCODE_SCHEMA": "v1"}, check=0)
    cfg = box.read_config()
    assert list(cfg["mcp"]) == ["ios"]                       # a key the user chose stays
    assert json.loads((box.state / "enabled.json").read_text())["ios"]["server"] == "mobile-build-tools"


def test_dry_run_and_read_only_commands_do_not_migrate(box):
    _wired_as_mobilebuildmcp(box)
    before = box.config.read_text()
    box.run("disable", "mobilebuildmcp", "--dry-run", check=0)
    box.run("list", check=0)
    assert box.config.read_text() == before and box.backups() == []


def test_enable_on_a_machine_wired_under_the_old_id_leaves_one_entry(box):
    box.full_xcode()
    _wired_as_mobilebuildmcp(box, rules=())
    rc, out = box.json("enable", "ios-build")
    assert rc == 0, out
    assert out["key"] == "mobile-build-tools" and out["server"] == "mobile-build-tools"
    cfg = box.read_config()
    assert list(cfg["mcp"]["servers"]) == ["openbeast-tools", "mobile-build-tools"]
    assert {"action": "mobile-build-tools_build_*", "resource": "*", "effect": "ask"} in cfg["permissions"]
    assert not any(str(p.get("action", "")).startswith("mobilebuildmcp_") for p in cfg["permissions"])
    assert list(json.loads((box.state / "enabled.json").read_text())) == ["mobile-build-tools"]


# ── review 2026-11-18: the rename and refresh keep every gate ──────────────
def _ocfg():
    sys.path.insert(0, str(CLI.parent))
    import ocfg
    return ocfg


def test_rename_v2_moves_the_users_own_rules_and_never_loosens_them(box):
    # The user's own `mobilebuildmcp_*` deny (and an ask) were keyed to the old
    # name: before the fix they stayed there, matched nothing after the
    # rename, and the new key's tools fell through to allow.
    user_rules = [{"action": "mobilebuildmcp_*", "resource": "*", "effect": "deny"},
                  {"action": "mobilebuildmcp_install_app_sim", "resource": "*", "effect": "ask"}]
    _wired_as_mobilebuildmcp(box)
    cfg = box.read_config()
    cfg["permissions"] = USER_CFG["permissions"] + user_rules + [OLD_RULE]
    box.write_config(cfg)
    cp = box.run("refresh", "--no-introspect", extra_env={"OPENBEAST_APPS_OFFLINE": "1"}, check=0)
    assert "moved 2 rule(s) of yours from mobilebuildmcp_* to mobile-build-tools_*" in cp.stderr, cp.stderr
    cfg = box.read_config()
    acts = [p["action"] for p in cfg["permissions"]]
    assert not any(a.startswith("mobilebuildmcp_") for a in acts), acts
    ours = {"action": "mobile-build-tools_build_*", "resource": "*", "effect": "ask"}
    # in place and in order; nothing appended: the user's deny glob is stricter
    # than every catalog ask, so adding one last would LOOSEN it
    assert cfg["permissions"] == USER_CFG["permissions"] + [
        {"action": "mobile-build-tools_*", "resource": "*", "effect": "deny"},
        {"action": "mobile-build-tools_install_app_sim", "resource": "*", "effect": "ask"}, ours]
    ocfg = _ocfg()
    for t in ("launch_app_sim", "test_unit", "anything"):
        assert ocfg.effective(cfg, "mobile-build-tools_" + t) == "deny", t
    assert json.loads((box.state / "enabled.json").read_text())["mobile-build-tools"]["rules"] == [ours]
    # disable removes ours only: the user's re-keyed rules stay
    box.run("disable", "mobile-build-tools", check=0)
    acts = [p["action"] for p in box.read_config()["permissions"]]
    assert "mobile-build-tools_build_*" not in acts
    assert "mobile-build-tools_*" in acts and "mobile-build-tools_install_app_sim" in acts


def test_rename_v1_moves_the_users_rules_and_adds_the_current_gates(box):
    box.write_config({"mcp": {"mobilebuildmcp": dict(OLD_ENTRY, enabled=True)},
                      "permission": {"*": "allow", "mobilebuildmcp_launch_app_sim": "deny",
                                     "mobilebuildmcp_build_*": "ask"}})
    (box.state / "enabled.json").write_text(json.dumps({"mobilebuildmcp": {
        "capability": "ios-build", "server": "mobilebuildmcp", "version": "2.7.1", "schema": "v1",
        "rules": [{"action": "mobilebuildmcp_build_*", "resource": "*", "effect": "ask"}], "entry": OLD_ENTRY}}))
    env = {"OPENBEAST_APPS_OFFLINE": "1", "OPENBEAST_OPENCODE_SCHEMA": "v1"}
    box.run("refresh", "--no-introspect", extra_env=env, check=0)
    cfg = box.read_config()
    assert list(cfg["mcp"]) == ["mobile-build-tools"]
    assert cfg["permission"] == {"*": "allow", "mobile-build-tools_launch_app_sim": "deny",
                                 "mobile-build-tools_build_*": "ask", "mobile-build-tools_test_*": "ask",
                                 "mobile-build-tools_install_app_sim": "ask"}
    rules = json.loads((box.state / "enabled.json").read_text())["mobile-build-tools"]["rules"]
    assert {r["action"] for r in rules} == {"mobile-build-tools_build_*", "mobile-build-tools_test_*",
                                            "mobile-build-tools_install_app_sim"}      # never the user's deny
    box.run("disable", "mobile-build-tools", extra_env=env, check=0)
    assert box.read_config()["permission"] == {"*": "allow", "mobile-build-tools_launch_app_sim": "deny"}


def test_refresh_brings_an_enabled_entry_up_to_the_catalogs_new_gates(box):
    # enabled under an older catalog: `alpha` was not gated then
    box.stub_server(ask=("danger*",))
    box.run("enable", "stub-cap", check=0)
    box.stub_server(ask=("danger*", "alpha"))              # the catalog gains a gate
    rc, out = box.json("refresh", "--no-introspect")
    assert rc == 0, out
    new = {"action": "stubsrv_alpha", "resource": "*", "effect": "ask"}
    assert out["servers"][0]["gates_added"] == [new]
    cfg = box.read_config()
    assert new in cfg["permissions"]
    rec = json.loads((box.state / "enabled.json").read_text())["stubsrv"]
    assert new in rec["rules"]
    cp = box.run("refresh", "--no-introspect", check=0)    # idempotent
    assert "gates added" not in cp.stdout and box.read_config() == cfg
    box.run("disable", "stub-cap", check=0)
    assert not any(str(p.get("action", "")).startswith("stubsrv_") for p in box.read_config().get("permissions", []))


def test_refresh_text_names_the_added_gates(box):
    box.stub_server(ask=("danger*",))
    box.run("enable", "stub-cap", check=0)
    box.stub_server(ask=("danger*", "beta"))
    cp = box.run("refresh", "--no-introspect", check=0)
    assert "gates added to stubsrv (stubsrv): stubsrv_beta=ask" in cp.stdout, cp.stdout


def test_refresh_judges_gated_by_the_config_not_the_catalog(box, tmp_path):
    # enabled --ungated into a v1 config whose `permission` is a string: the
    # catalog says danger* is gated, but nothing in the file gates it
    box.stub_server(risk=("shell",), ask=("danger*",))
    box.write_config({"permission": "allow"})
    env = {"OPENBEAST_OPENCODE_SCHEMA": "v1"}
    box.run("enable", "stub-cap", "--ungated", extra_env=env, check=0)
    tools = tmp_path / "tools.json"
    tools.write_text(json.dumps([{"name": "danger_exec", "description": "Runs arbitrary code.",
                                  "inputSchema": {"type": "object"}}]))
    cp = box.run("refresh", extra_env={**env, "STUB_TOOLS_FILE": str(tools)}, check=0)
    assert "tool danger_exec describes itself as running code" in cp.stdout and "NOT gated" in cp.stdout, cp.stdout
    assert "gates not written" in cp.stdout


def test_beast_apps_backups_are_pruned_to_the_last_three(tmp_path):
    ocfg = _ocfg()
    src = tmp_path / "opencode.json"
    src.write_text("{}")
    made = []
    for i in range(5):
        made.append(ocfg.backup(str(src)))
        os.utime(made[-1], (1000 + i, 1000 + i))
    left = sorted(str(p) for p in tmp_path.glob("opencode.json.bak-beast-apps-*"))
    assert left == sorted(made[-3:]), left


def test_write_keeps_a_symlinked_config_a_symlink(tmp_path):
    ocfg = _ocfg()
    real = tmp_path / "dotfiles" / "opencode.json"
    real.parent.mkdir()
    real.write_text("{}")
    link = tmp_path / "opencode.json"
    link.symlink_to(real)
    ocfg.write(str(link), {"model": "x"})
    assert link.is_symlink() and json.loads(real.read_text()) == {"model": "x"}


def test_a_write_waits_for_the_openbeast_config_lock(box):
    import fcntl
    import time
    box.stub_server()
    box.run("enable", "stub-cap", check=0)
    lock = box.home / ".config" / "openbeast" / ".lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        p = subprocess.Popen([PY, str(CLI), "disable", "stub-cap"], env=box.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1.5)
        assert p.poll() is None, "disable wrote while openbeast-client held the lock"
        assert "stubsrv" in json.dumps(box.read_config())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert p.wait(timeout=60) == 0
    assert "stubsrv" not in json.dumps(box.read_config().get("mcp", {}))
