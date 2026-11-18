#!/usr/bin/env python3
"""beast-apps — let the model drive external apps through MCP servers.

    apps.sh list                         capabilities + their status here
    apps.sh find <words...>              which capability covers a request
    apps.sh check <capability>           probes → READY / MISSING (+ the fix)
    apps.sh enable <capability>          wire the MCP server into opencode
    apps.sh disable <capability|server>  undo exactly what enable wrote
    apps.sh refresh [server...]          latest versions + live tool lists
    apps.sh upgrade <server> [--to V]    move a pin (explicit, never silent; down = rollback)
    apps.sh tools <server>               the recorded tool manifest
    apps.sh validate                     check the catalog itself

Exit codes: 0 ok/READY · 1 error · 2 usage / unknown name · 3 MISSING
prerequisite · 4 unsupported platform · 5 offline (upgrade needs the
network) · 6 risk (needs --accept-risk, --ungated or --accept-new-tools) ·
7 config key owned by you, or the server is already wired.

Design and catalog format: docs/BEAST_APPS.md. Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))          # agents/ (island.py)

import catalog as C       # noqa: E402
import mcp_client         # noqa: E402
import ocfg               # noqa: E402
import probes as P        # noqa: E402

EXIT_OK, EXIT_ERR, EXIT_USAGE, EXIT_MISSING, EXIT_UNSUPPORTED = 0, 1, 2, 3, 4
EXIT_OFFLINE, EXIT_RISK, EXIT_CONFLICT = 5, 6, 7
OK, WARN, FAIL = "✓", "!", "✗"
SYSTEM_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")


# ── small utilities ─────────────────────────────────────────────────────────
def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def emit(obj, as_json: bool, text: str) -> None:
    if as_json:
        print(json.dumps(obj, indent=2))
    else:
        print(text)


class State:
    """~/.openbeast-client/apps (or $OPENBEAST_APPS_STATE): what WE wrote."""

    def __init__(self, env: dict):
        self.dir = C.state_dir(env)
        self.enabled_path = os.path.join(self.dir, "enabled.json")
        self.pins_path = os.path.join(self.dir, "pins.json")

    def enabled(self) -> dict:
        return read_json(self.enabled_path, {})

    def save_enabled(self, d: dict) -> None:
        write_json(self.enabled_path, d)

    def pins(self) -> dict:
        return read_json(self.pins_path, {})

    def save_pins(self, d: dict) -> None:
        write_json(self.pins_path, d)

    def manifest_dir(self, sid: str) -> str:
        return os.path.join(self.dir, "manifests", sid)

    def last_manifest(self, sid: str) -> dict | None:
        return read_json(os.path.join(self.manifest_dir(sid), "latest.json"), None)

    def save_manifest(self, sid: str, man: dict) -> str:
        d = self.manifest_dir(sid)
        safe_v = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in man["version"])
        p = os.path.join(d, f"{safe_v}.json")
        write_json(p, man)
        write_json(os.path.join(d, "latest.json"), man)
        return p


def effective_version(cat: dict, sid: str, st: State) -> str:
    """The version to run: an `upgrade` pin in pins.json, else the catalog's.

    A pin records the catalog version it was set against (`base`). While the
    catalog still says that, the pin wins in EITHER direction, so
    `upgrade --to <older>` (a rollback) sticks. Once the catalog pin moves (a
    repo update), an older pin is stale and the catalog wins; a newer one
    still does. A pin that is not an exact version is ignored: it is
    substituted into `{package}@{version}`, where `npm:other@1` or `file:..`
    would install something else under the trusted name."""
    s = cat["server"][sid]
    base = s["version"]
    if s["registry"] == "app":
        return base
    p = st.pins().get(sid)
    pin = p.get("version") if isinstance(p, dict) else None
    if not C.valid_version(pin):
        return base
    if p.get("base") == base or P.version_ge(pin, base):
        return pin
    return base


# Variables an MCP child process started BY beast-apps (introspection, the
# npm install that stages a server) may inherit. Everything else in the
# user's shell (cloud credentials, tokens, session secrets) stays out: a new
# upstream release runs here before anyone has reviewed it. The server's own
# catalog env and env_passthrough values are added on top.
CHILD_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TMP", "TEMP", "LANG", "TERM", "TZ",
             "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
             "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy",
             "all_proxy", "DEVELOPER_DIR", "ANDROID_HOME", "ANDROID_SDK_ROOT", "JAVA_HOME")
CHILD_ENV_PREFIXES = ("LC_", "XDG_", "npm_config_", "NPM_CONFIG_", "UV_")


def child_env(env: dict, environment: dict | None = None) -> dict:
    extra = set((env.get("OPENBEAST_APPS_INTROSPECT_ENV") or "").replace(",", " ").split())
    out = {k: v for k, v in env.items()
           if k in CHILD_ENV or k in extra or k.startswith(CHILD_ENV_PREFIXES)}
    out.update(environment or {})
    return out


# Names whose VALUE is a credential. enable/upgrade --json output is read by
# the model (and so travels to the rig and into transcripts): those values
# are shown as "<redacted>" there. The config file itself keeps them (600).
SECRET_NAME = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")


def redact_entry(entry: dict, server: dict) -> dict:
    e = json.loads(json.dumps(entry))
    envd = e.get("environment")
    if isinstance(envd, dict):
        for k, v in envd.items():
            if v and (k in server.get("secret_env", []) or any(w in k.upper() for w in SECRET_NAME)):
                envd[k] = "<redacted>"
    return e


def private_config(server: dict, environment: dict, cfg_path: str) -> bool:
    """Whether the config must be written 0600: a secret_env value is in the
    entry, or the file is already private. One rule for enable and upgrade."""
    return any(v in environment for v in server.get("secret_env", [])) or (
        os.path.exists(cfg_path) and os.stat(cfg_path).st_mode & 0o077 == 0)


# ── evaluation ──────────────────────────────────────────────────────────────
def evaluate_server(cat: dict, sid: str, env: dict, st: State, cache: dict | None = None) -> dict:
    s = cat["server"][sid]
    plat = C.platform_id(env)
    res = {"id": sid, "title": s["title"], "version": effective_version(cat, sid, st),
           "catalog_version": s["version"], "source": s["source"], "checked": s["checked"],
           "transport": s["transport"], "platforms": s["platforms"], "probes": [],
           "missing": [], "warnings": [], "export_env": {}, "dirs": [],
           "default_enabled": s.get("default_enabled", True), "risk": s.get("risk"),
           "notes": s.get("notes", "")}
    if plat not in s["platforms"]:
        res["status"] = "UNSUPPORTED"
        res["missing"].append(f"{s['title']} runs on {', '.join(s['platforms'])} only; this machine is {plat}.")
        return res
    for pid in s.get("probes", []):
        r = P.run(cat, pid, env, cache=cache)
        res["probes"].append(r)
        if r["status"] == "fail":
            res["missing"].append(r["fix"])
        elif r["status"] == "warn" and r["fix"]:
            res["warnings"].append(r["fix"])
        res["export_env"].update(r.get("export_env") or {})
        for d in r.get("dirs") or []:
            if d not in res["dirs"]:
                res["dirs"].append(d)
    res["status"] = "MISSING" if res["missing"] else "READY"
    return res


def evaluate_capability(cat: dict, cid: str, env: dict, st: State, only: str | None = None,
                        cache: dict | None = None) -> dict:
    c = cat["capability"][cid]
    out = {"capability": cid, "title": c["title"], "stage": c.get("stage", ""),
           "platform": C.platform_id(env), "servers": [], "server": None}
    order = [only] if only else C.capability_servers(cat, cid)
    for sid in order:
        r = evaluate_server(cat, sid, env, st, cache)
        out["servers"].append(r)
        if r["status"] == "READY":
            out["server"] = sid
            break                    # preference order: first READY wins
    statuses = [r["status"] for r in out["servers"]]
    if "READY" in statuses:
        out["status"] = "READY"
    elif all(x == "UNSUPPORTED" for x in statuses):
        out["status"] = "UNSUPPORTED"
    else:
        out["status"] = "MISSING"
    first = next((r for r in out["servers"] if r["status"] != "UNSUPPORTED"), out["servers"][0])
    chosen = next((r for r in out["servers"] if r["id"] == out["server"]), first)
    out["remediation"] = [] if out["status"] == "READY" else list(first["missing"])
    out["warnings"] = list(chosen["warnings"])
    return out


def ownership(st: State, cfg: dict) -> tuple[dict, dict]:
    """(entries we own that are still in the config, every other entry)."""
    mine = st.enabled()
    present = ocfg.entries(cfg)
    owned = {k: v for k, v in mine.items() if k in present}
    others = {k: v for k, v in present.items() if k not in mine}
    return owned, others


def user_configured(server: dict, others: dict) -> list[str]:
    keys = []
    for k, e in others.items():
        cmd = " ".join(e.get("command") or []) if isinstance(e.get("command"), list) else ""
        if server.get("package") and server["registry"] != "app" and server["package"] in cmd:
            keys.append(k)
        elif server.get("url") and e.get("url", "").rstrip("/") == server["url"].rstrip("/"):
            keys.append(k)
    return keys


# ── offline + registries ────────────────────────────────────────────────────
def offline_reason(env: dict) -> str | None:
    if (env.get("OPENBEAST_APPS_OFFLINE") or "").lower() in ("1", "true", "yes"):
        return "OPENBEAST_APPS_OFFLINE is set"
    if (env.get("OFFLINE") or "").lower() in ("1", "true", "yes"):
        return "OFFLINE=true (this install has no internet route by design)"
    try:
        import island
        if island.islanded():
            return "the rig is in island mode"
        targets = island.parse_probes(env.get("OPENBEAST_ISLAND_PROBES"))
        if not island.probe(targets, timeout=float(env.get("OPENBEAST_APPS_PROBE_TIMEOUT", "2"))):
            return "no internet route (connect probes to " + ", ".join(f"{h}:{p}" for h, p in targets) + " failed)"
    except ImportError:
        pass
    return None


def island_banner(reason: str) -> str:
    return (f"beast-island: OFFLINE — {reason}. Pins are kept exactly as they are; latest "
            "versions were not checked. Catalog, probes, check and disable work offline; enable "
            "and upgrade stage npm servers from the local npm cache only, and introspection runs "
            "what is staged (or the local uvx cache). Re-run `apps.sh refresh` "
            "when the network is back.")


def _get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "openbeast-apps/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(16 * 1024 * 1024).decode())


def latest_version(server: dict, env: dict) -> tuple[str | None, str | None]:
    """(latest published version, error). A registry answer that is not an
    exact version is an error, never a spec to install."""
    v, err = _latest_raw(server, env)
    if v is not None and not C.valid_version(v):
        return None, f"{server['registry']} answered {v!r} as the latest version, which is not an exact version"
    return v, err


def _latest_raw(server: dict, env: dict) -> tuple[str | None, str | None]:
    reg = server["registry"]
    try:
        if reg == "npm":
            base = env.get("OPENBEAST_APPS_NPM_REGISTRY", "https://registry.npmjs.org").rstrip("/")
            d = _get_json(f"{base}/{urllib.parse.quote(server['package'], safe='@')}/latest")
            return d.get("version"), None
        if reg == "pypi":
            base = env.get("OPENBEAST_APPS_PYPI", "https://pypi.org").rstrip("/")
            d = _get_json(f"{base}/pypi/{urllib.parse.quote(server['package'])}/json")
            return d.get("info", {}).get("version"), None
        if reg == "github":
            base = env.get("OPENBEAST_APPS_GITHUB_API", "https://api.github.com").rstrip("/")
            d = _get_json(f"{base}/repos/{server['repo']}/releases/latest")
            return (d.get("tag_name") or "").lstrip("v") or None, None
        return None, None            # app-bundled: no registry, the manifest hash tracks it
    except (urllib.error.URLError, OSError, ValueError, AttributeError, TypeError) as e:
        return None, f"{reg} lookup failed: {getattr(e, 'reason', e)}"


# ── launching ───────────────────────────────────────────────────────────────
def npm_prefix(env: dict, sid: str, version: str) -> str:
    return os.path.join(C.state_dir(env), "npm", f"{sid}@{version}")


def _npm_bin(pkgdir: str, package: str) -> str | None:
    """The executable `npx <package>` would run (npm's own rule: a string
    `bin`, the only bin, or the one named after the unscoped package)."""
    man = read_json(os.path.join(pkgdir, "package.json"), None)
    b = man.get("bin") if isinstance(man, dict) else None
    if isinstance(b, str):
        return b
    if isinstance(b, dict) and b:
        if len(b) == 1:
            return next(iter(b.values()))
        return b.get(package.rsplit("/", 1)[-1])
    return None


def stage_npm(s: dict, sid: str, version: str, env: dict, npx: str, path: str,
              mode: str, offline: bool) -> tuple[list[str] | None, str | None]:
    """Install `package@version` into a beast-apps-owned prefix and return the
    argv that runs its bin directly: ([node, <abs bin>] or [<abs bin>], error).

    WHY. `npx -y pkg@ver` resolves against the directory it starts in, and
    opencode starts MCP servers in the project it has open: a project's
    node_modules/ holding pkg@ver runs instead of the registry copy, its
    .npmrc can point `registry`/`cache` at an attacker, and npm exec puts its
    node_modules/.bin on the server's PATH (a planted `node` or `xcodebuild`
    there wins). Exact-version pinning stops none of that, and it happens
    when opencode STARTS, before any ask gate. So the package is installed
    once (enable / upgrade) into <state>/npm/<server>@<version>, from a
    neutral cwd, with a lockfile, and the entry runs that file by absolute
    path: no npm, no project lookup at launch. mode "plan" (a dry run) only
    says what would happen."""
    prefix = npm_prefix(env, sid, version)
    pkg = s["package"]
    pkgdir = os.path.join(prefix, "node_modules", *pkg.split("/"))
    man = read_json(os.path.join(pkgdir, "package.json"), None)
    if not (isinstance(man, dict) and man.get("version") == version):
        if mode == "plan":
            return [os.path.join(pkgdir, "<bin, installed at enable>")], None
        npm = os.path.join(os.path.dirname(npx), "npm")
        if not os.access(npm, os.X_OK):
            npm = shutil.which("npm", path=path) or ""
        if not npm:
            return None, f"npm not found next to {npx} or on PATH (it ships with Node.js)"
        tmp = f"{prefix}.tmp-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, mode=0o700)
        cmd = [npm, "install", "--prefix", tmp, "--save-exact", "--no-audit", "--no-fund",
               "--no-update-notifier", "--loglevel=error",
               "--offline" if offline else "--prefer-offline", f"{pkg}@{version}"]
        try:
            cp = subprocess.run(cmd, cwd=tmp, env=child_env(env, {"PATH": path}), capture_output=True,
                                text=True, stdin=subprocess.DEVNULL,
                                timeout=float(env.get("OPENBEAST_APPS_INSTALL_TIMEOUT", "600")))
            rc, tail = cp.returncode, (cp.stderr or cp.stdout or "").strip().splitlines()[-3:]
        except (OSError, subprocess.TimeoutExpired) as e:
            rc, tail = 1, [str(e)]
        got = read_json(os.path.join(tmp, "node_modules", *pkg.split("/"), "package.json"), None)
        if rc != 0 or not (isinstance(got, dict) and got.get("version") == version):
            shutil.rmtree(tmp, ignore_errors=True)
            why = " | ".join(tail) or f"exit {rc}"
            return None, (f"could not install {pkg}@{version} into {prefix}: {why}"
                          + (" (offline: only the local npm cache was used)" if offline else ""))
        shutil.rmtree(prefix, ignore_errors=True)
        os.replace(tmp, prefix)
    rel = _npm_bin(pkgdir, pkg)
    if not rel:
        return None, f"{pkg}@{version} declares no single executable (package.json bin) to run"
    real_prefix = os.path.realpath(prefix)
    binp = os.path.realpath(os.path.join(pkgdir, rel))
    if not binp.startswith(real_prefix + os.sep) or not os.path.isfile(binp):
        return None, f"{pkg}@{version}: its bin {rel!r} is not a file inside {prefix}"
    try:
        with open(binp, "rb") as f:
            first = f.readline(200)
    except OSError as e:
        return None, f"cannot read {binp}: {e}"
    if first.startswith(b"#!") and b"node" not in first:
        return [binp], None                  # a non-node bin: run as is
    node = shutil.which("node", path=path)
    if not node:
        return None, f"node not found on {path}"
    return [os.path.abspath(node), binp], None


def render_launch(cat: dict, sid: str, version: str, env: dict, ev: dict, keep: dict | None = None,
                  stage: str = "install", offline: bool | None = None
                  ) -> tuple[list[str] | None, dict, str | None]:
    """(absolute argv, MCP environment, error).

    keep: the environment recorded at enable time. An env_passthrough value
    the current shell does not set is taken from it, so an upgrade run from
    a fresh terminal does not silently drop the printer's token.
    stage: "install" stages an npm server (see stage_npm), "plan" does not.
    offline: None = ask offline_reason() only if an install is needed."""
    s = cat["server"][sid]
    keep = keep or {}
    # {state} in a fixed env value is the beast-apps state dir: a location a
    # project cannot choose (a pinned path never comes from the opened dir).
    environment = {k: v.replace("{state}", C.state_dir(env)) for k, v in (s.get("env") or {}).items()}
    environment.update(ev.get("export_env") or {})
    for var in s.get("env_passthrough", []):
        if env.get(var):
            environment[var] = env[var]
        elif keep.get(var):
            environment[var] = keep[var]
    if s["transport"] != "stdio":
        return None, environment, None
    argv = [a.replace("{package}", s["package"]).replace("{version}", version) for a in s["command"]]
    search: list[str] = []
    for pid in s.get("probes", []):
        pr = cat["probe"][pid]
        if pr.get("kind") == "command" and pr["command"][0] == argv[0]:
            search += pr.get("search", [])
    exe, on_path = P.resolve([argv[0]], search, env)
    if not exe:
        return None, environment, f"{argv[0]} not found on PATH or in {', '.join(search) or 'any known dir'}"
    # ALWAYS a clean, deterministic PATH — never a copy of this shell's (which
    # carries session temp dirs and whatever happens to be active now), and
    # never nothing: a GUI-launched opencode gives its servers launchd's
    # /usr/bin:/bin:/usr/sbin:/sbin, where `#!/usr/bin/env node` (npx, every
    # npm bin) fails with "env: node: No such file or directory". So: the
    # off-PATH dirs the probes found, the launcher's dir, the dir of every
    # tool a probe found (node, adb, java, xcodebuild…), then the system dirs.
    dirs = list(ev.get("dirs") or [])
    found = [os.path.dirname(p["path"]) for p in ev.get("probes") or []
             if p.get("path") and p.get("kind") in ("command", "any") and p.get("status") in ("ok", "warn")]
    base = [os.path.dirname(exe)] + found + list(SYSTEM_PATH)
    npm =s["registry"] == "npm" and os.path.basename(argv[0]) == "npx"
    if npm:
        # the staged bin runs under node: make sure node's dir is on the
        # entry PATH even when no probe of this server looked for node
        node, _ = P.resolve(["node"], [os.path.dirname(exe)], env)
        if node:
            base.insert(1, os.path.dirname(node))
    environment["PATH"] = os.pathsep.join(dict.fromkeys(
        d for d in dirs + base if d and (d in dirs or os.path.isdir(d))))
    if npm:
        i = next(i for i, a in enumerate(s["command"]) if "{version}" in a)
        if offline is None and stage == "install" and not os.path.isfile(
                os.path.join(npm_prefix(env, sid, version), "node_modules", *s["package"].split("/"),
                             "package.json")):
            offline = bool(offline_reason(env))
        head, err = stage_npm(s, sid, version, env, exe, environment["PATH"], stage, bool(offline))
        if err:
            return None, environment, err
        return head + argv[i + 1:], environment, None
    return [exe] + argv[1:], environment, None


def introspect(cat: dict, sid: str, version: str, command: list[str] | None, environment: dict,
               env: dict, offline: bool) -> dict:
    """initialize + tools/list against the server. A stdio server is started
    the way opencode will start it, but from the beast-apps state dir (never
    the caller's project) and with child_env(): the user's credentials are
    not handed to a release nobody has reviewed yet."""
    s = cat["server"][sid]
    timeout = float(env.get("OPENBEAST_APPS_TIMEOUT", "90"))
    if s["transport"] == "http":
        info, tools = mcp_client.list_tools_http(s["url"], timeout=min(timeout, 30))
    else:
        cmd = list(command)
        if offline and os.path.basename(cmd[0]) in ("npx", "uvx"):
            cmd.insert(1, "--offline")    # cache only: no stall on a dead network
        cwd = C.state_dir(env)
        os.makedirs(cwd, exist_ok=True)
        info, tools = mcp_client.list_tools_stdio(cmd, child_env(env, environment), timeout=timeout, cwd=cwd)
    return make_manifest(sid, version, s["transport"], info, tools)


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def make_manifest(sid: str, version: str, transport: str, info: dict, tools: list[dict]) -> dict:
    rows = []
    for t in sorted(tools, key=lambda t: t["name"]):
        schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {}
        desc = t.get("description") if isinstance(t.get("description"), str) else ""
        rows.append({"name": t["name"], "description": desc.strip(),
                     "inputSchema": schema, "schema_sha256": _h(schema),
                     **({"annotations": t["annotations"]} if isinstance(t.get("annotations"), dict) else {})})
    core = [{"name": r["name"], "description": r["description"], "schema": r["schema_sha256"]} for r in rows]
    si = (info or {}).get("serverInfo") or {}
    return {"server": sid, "version": version, "transport": transport, "introspected_at": now_iso(),
            "server_info": {"name": si.get("name"), "version": si.get("version")},
            "protocol": (info or {}).get("protocolVersion"), "tool_count": len(rows),
            "hash": _h(core), "tools": rows}


# Phrases in a tool's DESCRIPTION that mean it runs code or installs
# something on this machine. A tool that says so and is not gated is
# flagged on every refresh (the first one included) until it is gated,
# whether the tool is new or an existing one whose description changed.
EXEC_MARKERS = ("execute_code", "execute_blender_code", "python code", "arbitrary code", "arbitrary python",
                "shell command", "run on startup", "login item", "launchagent", "runs a script")


def diff_manifests(old: dict | None, new: dict, server: dict) -> dict:
    gates = list(server.get("ask_tools", [])) + list(server.get("deny_tools", []))
    if not old:
        added, removed, changed = [t["name"] for t in new["tools"]], [], []
    else:
        o = {t["name"]: t for t in old["tools"]}
        n = {t["name"]: t for t in new["tools"]}
        added = sorted(set(n) - set(o))
        removed = sorted(set(o) - set(n))
        changed = []
        for name in sorted(set(n) & set(o)):
            what = []
            if n[name]["schema_sha256"] != o[name]["schema_sha256"]:
                what.append("inputSchema")
            if n[name]["description"] != o[name]["description"]:
                what.append("description")
            if what:
                changed.append({"name": name, "changed": what})
    flagged = []
    desc = {t["name"]: (t.get("description") or "").lower() for t in new["tools"]}
    names_here = {n.lower() for n in desc}
    new_names = set(added)
    for name in sorted(desc):
        # Name patterns are a review hint for NEW tools only.
        hits = [p for p in server.get("risk_patterns", []) if p.lower() in name.lower()] if name in new_names else []
        # Names alone miss a bridge ("blender_mcp_call") whose description
        # says it runs code or installs something: check what EVERY tool says
        # it does, on every manifest, so an upstream update that rewrites an
        # existing tool's description into an exec bridge is caught too. A
        # marker that is the name of ANOTHER tool of this same server is a
        # cross-reference ("read it with execute_blender_code"), not a bridge:
        # that tool is judged (and gated) on its own.
        said = [m for m in EXEC_MARKERS if m in desc[name] and not (m in names_here and m != name.lower())]
        if hits or said:
            gated = any(fnmatch.fnmatchcase(name, g) for g in gates)
            flagged.append({"name": name, "patterns": hits, "description": said, "gated": gated})
    return {"first": old is None, "previous_version": (old or {}).get("version"),
            "previous_hash": (old or {}).get("hash"), "hash": new["hash"],
            "same": bool(old) and old.get("hash") == new["hash"],
            "added": added, "removed": removed, "changed": changed, "flagged": flagged}


# ── text rendering ──────────────────────────────────────────────────────────
def _mark(status: str) -> str:
    return {"ok": OK, "warn": WARN}.get(status, FAIL)


def render_check(rep: dict, enabled: dict | None, user_keys: list[str]) -> str:
    L = [f"{rep['capability']} — {rep['title']}",
         f"  platform: {rep['platform']}    status: {rep['status']}"]
    if enabled:
        L.append(f"  enabled: opencode key {enabled['key']} ({enabled['server']} {enabled['version']})")
    elif user_keys:
        L.append(f"  configured by you (not beast-apps): {', '.join(user_keys)}")
    for s in rep["servers"]:
        L.append(f"  server {s['id']} {s['version']} — {s['title']}  [{s['status']}]")
        L.append(f"    source: {s['source']} (verified {s['checked']})")
        for p in s["probes"]:
            extra = p["found"] if p["status"] != "fail" else (p["detail"] or p["found"])
            opt = "" if p["required"] else " (optional)"
            L.append(f"    {_mark(p['status'])} {p['what']}{opt}" + (f" — {extra}" if extra else ""))
        if s["status"] == "UNSUPPORTED":
            L.append(f"    {FAIL} {s['missing'][0]}")
        if s.get("risk") and not s["default_enabled"]:
            L.append(f"    RISK (off by default): {s['risk']}")
    if rep["status"] == "READY":
        if rep["warnings"]:
            L.append("  Notes:")
            L += [f"    - {w}" for w in rep["warnings"]]
        if not enabled:
            L.append(f"  Next: scripts/apps.sh enable {rep['capability']}")
    else:
        L.append("  What to do:" if rep["status"] == "MISSING" else "  Not available here:")
        L += [f"    {i}. {fix}" for i, fix in enumerate(rep["remediation"], 1)]
        if rep["status"] == "MISSING":
            L.append(f"  Then re-run: scripts/apps.sh check {rep['capability']}")
    return "\n".join(L)


# ── renamed servers ─────────────────────────────────────────────────────────
MIGRATING = ("enable", "disable", "refresh", "upgrade")


def migrate_legacy(ctx: "Ctx") -> list[str]:
    """Move what beast-apps wired under a server's former id (its catalog
    `aliases`) to the current id: the opencode entry and its gates, the
    enabled.json record, the pin and the tool manifests. Runs before enable /
    disable / refresh / upgrade, so a machine wired as `mobilebuildmcp` ends
    up with `mobile-build-tools` only, never both. An entry under a key the
    user chose (`enable --key`) keeps that key; only its record moves. If the
    new key is already taken (wired again, or by hand), the old entry is
    removed instead. Returns what it did, one line each."""
    legacy = C.legacy_ids(ctx.cat)
    if not legacy:
        return []
    st, msgs = ctx.st, []
    pins = st.pins()
    if any(old in pins for old in legacy):
        for old, new in legacy.items():
            if old in pins:
                pins.setdefault(new, pins[old])
                del pins[old]
                msgs.append(f"pin of {old} moved to {new}")
        st.save_pins(pins)
    for old, new in legacy.items():
        od, nd = st.manifest_dir(old), st.manifest_dir(new)
        if os.path.isdir(od) and not os.path.exists(nd):
            os.replace(od, nd)
            msgs.append(f"tool manifests of {old} moved to {new}")
    mine = st.enabled()
    todo = [(k, r) for k, r in mine.items() if r.get("server") in legacy]
    if not todo:
        return msgs
    try:
        cfg = ctx.cfg()
    except ocfg.ConfigError as e:
        return msgs + [f"cannot rename the entries of {', '.join(k for k, _ in todo)} yet: {e}"]
    wrote = secret = False
    for key, rec in todo:
        sid = legacy[rec["server"]]
        s = ctx.cat["server"][sid]
        secret = secret or private_config(s, (rec.get("entry") or {}).get("environment") or {}, ctx.cfg_path)
        newkey = (s.get("config_key") or sid) if key == rec["server"] else key
        rec = dict(rec, server=sid)
        del mine[key]
        if newkey != key and (newkey in mine or ocfg.find_entry(cfg, newkey)):
            gone = ocfg.uninstall(cfg, key, rec.get("rules", []))
            wrote = wrote or bool(gone)
            msgs.append(f"removed the old opencode entry {key!r}: {sid} is already wired as {newkey!r}")
            continue
        if newkey != key:
            fl = ocfg.flavor_for(cfg, key, ctx.env, rec.get("schema"))
            res = ocfg.rename(cfg, key, newkey, rec.get("rules", []), fl)
            if res is not None:
                rec.update(rules=res[0], schema=fl, at=now_iso())
                msgs += [f"renamed opencode entry {key!r} to {newkey!r} ({sid})"] + [f"  ! {w}" for w in res[1]]
                wrote = True
        mine[newkey] = rec
    if wrote:
        b = ocfg.backup(ctx.cfg_path)
        ocfg.write(ctx.cfg_path, cfg, secret=secret)
        if b:
            msgs.append(f"  backup: {b}")
        msgs.append("  Restart opencode to load the new name.")
    st.save_enabled(mine)
    return msgs


# ── commands ────────────────────────────────────────────────────────────────
class Ctx:
    def __init__(self, args):
        self.args = args
        self.env = dict(os.environ)
        self.cat = C.load(env=self.env)
        self.st = State(self.env)
        self.cfg_path = ocfg.config_path(getattr(args, "config", None), self.env)
        self.probe_cache: dict = {}        # one process, one env: probes run once

    def cfg(self) -> dict:
        return ocfg.load(self.cfg_path)

    def cfg_or_empty(self) -> tuple[dict, str | None]:
        try:
            return self.cfg(), None
        except ocfg.ConfigError as e:
            return {}, str(e)


def resolve_capability(ctx: Ctx, name: str) -> str | None:
    if name in ctx.cat["capability"]:
        return name
    caps = C.capabilities_of(ctx.cat, C.resolve_server(ctx.cat, name) or name)
    return caps[0] if caps else None


def cmd_validate(ctx: Ctx) -> int:
    print(f"catalog OK: {len(ctx.cat['capability'])} capabilities, {len(ctx.cat['server'])} servers, "
          f"{len(ctx.cat['probe'])} probes ({', '.join(ctx.cat['_sources'])})")
    return EXIT_OK


def _enabled_for(ctx: Ctx, cid: str, owned: dict) -> dict | None:
    for k, v in owned.items():
        if v.get("capability") == cid:
            return dict(v, key=k)
    return None


def cmd_list(ctx: Ctx) -> int:
    cfg, cerr = ctx.cfg_or_empty()
    owned, others = ownership(ctx.st, cfg)
    rows = []
    for cid in ctx.cat["capability"]:
        rep = evaluate_capability(ctx.cat, cid, ctx.env, ctx.st, cache=ctx.probe_cache)
        en = _enabled_for(ctx, cid, owned)
        ukeys = sorted({k for sid in C.capability_servers(ctx.cat, cid)
                        for k in user_configured(ctx.cat["server"][sid], others)})
        rows.append({"capability": cid, "title": rep["title"], "stage": rep["stage"],
                     "status": rep["status"], "server": rep["server"] or rep["servers"][0]["id"],
                     "enabled": en, "configured_by_user": ukeys,
                     "default_enabled": all(ctx.cat["server"][s].get("default_enabled", True)
                                            for s in C.capability_servers(ctx.cat, cid)),
                     "missing": len(rep["remediation"])})
    out = {"platform": C.platform_id(ctx.env), "opencode_config": ctx.cfg_path,
           "config_error": cerr, "state_dir": ctx.st.dir, "capabilities": rows}
    L = [f"beast-apps on {out['platform']} — opencode config: {ctx.cfg_path}"]
    if cerr:
        L.append(f"  ! {cerr}")
    w = max(len(r["capability"]) for r in rows)
    for r in rows:
        tag = "enabled" if r["enabled"] else ("yours" if r["configured_by_user"] else "")
        risk = "" if r["default_enabled"] else "  (off by default: risk)"
        miss = f" ({r['missing']} missing)" if r["status"] == "MISSING" else ""
        L.append(f"  {r['capability']:<{w}}  {r['status']:<11} {r['server']:<22} {tag:<8} {r['title']}{miss}{risk}")
    L.append("Details and the fix for anything MISSING: scripts/apps.sh check <capability>")
    emit(out, ctx.args.json, "\n".join(L))
    return EXIT_OK


def cmd_find(ctx: Ctx) -> int:
    q = " ".join(ctx.args.query)
    hits = C.find(ctx.cat, q)[: ctx.args.limit]
    res = []
    for cid, score in hits:
        rep = evaluate_capability(ctx.cat, cid, ctx.env, ctx.st, cache=ctx.probe_cache)
        res.append({"capability": cid, "score": score, "status": rep["status"], "title": rep["title"],
                    "server": rep["server"], "remediation": rep["remediation"]})
    out = {"query": q, "matches": res}
    if not res:
        txt = (f"No catalog capability matches {q!r}. Nothing to enable: say so to the user. "
               "A server can be added to agents/apps/catalog.toml or an overlay in "
               f"{C.overlay_dir(ctx.env)} (docs/BEAST_APPS.md).")
    else:
        txt = "\n".join(f"  {r['capability']:<14} {r['status']:<11} {r['title']}" for r in res) + \
              "\nNext: scripts/apps.sh check <capability>"
    emit(out, ctx.args.json, txt)
    return EXIT_OK if res else EXIT_USAGE


def cmd_check(ctx: Ctx) -> int:
    cid = resolve_capability(ctx, ctx.args.capability)
    if not cid:
        return unknown(ctx, ctx.args.capability)
    only = ctx.args.server and (C.resolve_server(ctx.cat, ctx.args.server) or ctx.args.server)
    if only and only not in C.capability_servers(ctx.cat, cid):
        print(f"server {only!r} is not a candidate for {cid} "
              f"(candidates: {', '.join(C.capability_servers(ctx.cat, cid))})", file=sys.stderr)
        return EXIT_USAGE
    rep = evaluate_capability(ctx.cat, cid, ctx.env, ctx.st, only=only)
    cfg, cerr = ctx.cfg_or_empty()
    owned, others = ownership(ctx.st, cfg)
    en = _enabled_for(ctx, cid, owned)
    ukeys = sorted({k for sid in C.capability_servers(ctx.cat, cid)
                    for k in user_configured(ctx.cat["server"][sid], others)})
    rep["enabled"] = en
    rep["configured_by_user"] = ukeys
    rep["config_error"] = cerr
    rep["next"] = (None if rep["status"] != "READY" or en
                   else f"scripts/apps.sh enable {cid}" + (f" --server {rep['server']}" if only else ""))
    emit(rep, ctx.args.json, render_check(rep, en, ukeys))
    return {"READY": EXIT_OK, "MISSING": EXIT_MISSING, "UNSUPPORTED": EXIT_UNSUPPORTED}[rep["status"]]


def unknown(ctx: Ctx, name: str) -> int:
    hits = [c for c, _ in C.find(ctx.cat, name)[:3]]
    msg = f"unknown capability or server {name!r}. Known: {', '.join(ctx.cat['capability'])}"
    if hits:
        msg += f". Did you mean: {', '.join(hits)}?"
    if getattr(ctx.args, "json", False):
        print(json.dumps({"error": msg, "suggestions": hits}))
    else:
        print(msg, file=sys.stderr)
    return EXIT_USAGE


def cmd_enable(ctx: Ctx) -> int:
    a = ctx.args
    cid = resolve_capability(ctx, a.capability)
    if not cid:
        return unknown(ctx, a.capability)
    server = a.server and (C.resolve_server(ctx.cat, a.server) or a.server)
    if server and server not in C.capability_servers(ctx.cat, cid):
        print(f"server {a.server!r} is not a candidate for {cid}", file=sys.stderr)
        return EXIT_USAGE
    rep = evaluate_capability(ctx.cat, cid, ctx.env, ctx.st, only=server)
    if rep["status"] != "READY":
        print(render_check(rep, None, []))
        print(f"\nRefusing to enable {cid}: the check did not pass. Fix the items above, then re-run.")
        return EXIT_MISSING if rep["status"] == "MISSING" else EXIT_UNSUPPORTED
    sid = rep["server"]
    s = ctx.cat["server"][sid]
    ev = next(r for r in rep["servers"] if r["id"] == sid)
    if not s.get("default_enabled", True) and not a.accept_risk:
        print(f"{sid} is OFF by default.\n  Risk: {s['risk']}\n  Gated as ask once enabled: "
              f"{', '.join(s.get('ask_tools', [])) or 'nothing'}\n"
              f"Re-run with --accept-risk if you want it anyway.")
        return EXIT_RISK
    key = a.key or s.get("config_key") or sid
    try:
        cfg = ctx.cfg()
    except ocfg.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERR
    mine = ctx.st.enabled()
    hit = ocfg.find_entry(cfg, key)
    if hit and key not in mine:
        print(f"opencode already has an MCP entry named {key!r} that beast-apps did not write; "
              f"not touching it. Keep yours, or pass --key <other-name>.")
        return EXIT_CONFLICT
    owned, others = ownership(ctx.st, cfg)
    dup = user_configured(s, others)
    if dup and not a.allow_duplicate:
        print(f"You already run {s['package']} from your own opencode entry {', '.join(dup)}; "
              "beast-apps will not add a second copy (the model would see every tool twice). "
              "Keep yours, or remove it and re-run (or pass --allow-duplicate).")
        return EXIT_CONFLICT
    # The same guard for entries beast-apps wrote itself: `enable <cap> --key
    # other` must not wire a second copy next to the first.
    twin = sorted(k for k, v in owned.items() if k != key and (v.get("server") == sid or v.get("capability") == cid))
    if twin and not a.allow_duplicate:
        print(f"beast-apps already wired {cid} ({', '.join(owned[k]['server'] for k in twin)}) as opencode "
              f"entry {', '.join(twin)}; it will not add a second copy under {key!r} (the model would see "
              f"every tool twice). Re-run without --key to re-enable it, `scripts/apps.sh disable "
              f"{twin[0]}` first, or pass --allow-duplicate.")
        return EXIT_CONFLICT
    version = effective_version(ctx.cat, sid, ctx.st)
    command, environment, err = render_launch(ctx.cat, sid, version, ctx.env, ev,
                                              stage="plan" if a.dry_run else "install")
    if err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_MISSING
    fl = ocfg.flavor(cfg, ctx.env)
    entry = ocfg.build_entry(s, command, environment, fl)
    rules = ocfg.rules_for(key, s)
    if key in mine:                                    # re-enable: drop the old gates first
        ocfg.uninstall(cfg, key, mine[key].get("rules", []))
    why = ocfg.ungatable(cfg, rules, fl)
    ungated = []
    if why:
        if not a.ungated:
            print(f"Refusing to enable {sid}: {why}.\n  Those tools would run with no approval. Make "
                  f"`permission` an object in {ctx.cfg_path} (e.g. {{\"*\": \"allow\"}}) and re-run, or pass "
                  "--ungated to wire it in without the gates anyway.")
            return EXIT_RISK
        ungated, rules = rules, []                     # nothing written, nothing recorded
    # Only what install() actually changed is recorded (and later undone): a
    # rule of yours that is already as strict is kept and left alone.
    rules, warns = ocfg.install(cfg, key, entry, rules, fl)
    if ungated:
        warns.append("NOT gated (--ungated): " + ", ".join(f"{r['action']}={r['effect']}" for r in ungated))
    secret = private_config(s, environment, ctx.cfg_path)
    where = f"mcp.servers.{key}" if fl == "v2" else f"mcp.{key}"
    out = {"enabled": True, "capability": cid, "server": sid, "version": version, "key": key,
           "config": ctx.cfg_path, "schema": fl, "entry": redact_entry(entry, s), "rules": rules,
           "ungated": ungated, "warnings": warns + ev["warnings"], "dry_run": bool(a.dry_run), "backup": None}
    if not a.dry_run:
        out["backup"] = ocfg.backup(ctx.cfg_path)
        ocfg.write(ctx.cfg_path, cfg, secret=secret)
        mine[key] = {"capability": cid, "server": sid, "version": version, "schema": fl,
                     "config": ctx.cfg_path, "rules": rules, "entry": entry, "at": now_iso()}
        ctx.st.save_enabled(mine)
    L = [("[dry run] would write" if a.dry_run else "wrote") + f" {where} ({sid} {version}) into {ctx.cfg_path}"]
    if out["backup"]:
        L.append(f"  backup: {out['backup']}")
    if rules:
        L.append("  gated: " + ", ".join(f"{r['action']}={r['effect']}" for r in rules))
    if fl == "v2":
        L.append("  codemode: off (tools are exposed directly)")
    L += [f"  ! {w}" for w in out["warnings"]]
    L.append(f"  Restart opencode to load it. Record its tools: scripts/apps.sh refresh {sid}")
    if s.get("notes"):
        L.append(f"  Note: {s['notes']}")
    emit(out, a.json, "\n".join(L))
    return EXIT_OK


def cmd_disable(ctx: Ctx) -> int:
    a = ctx.args
    mine = ctx.st.enabled()
    name = C.resolve_server(ctx.cat, a.name) or a.name
    targets = [k for k, v in mine.items() if {a.name, name} & {k, v.get("server"), v.get("capability")}]
    try:
        cfg = ctx.cfg()
    except ocfg.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERR
    if not targets:
        hit = ocfg.find_entry(cfg, a.name)
        msg = (f"{a.name!r} is in your opencode config but beast-apps did not write it; not touching it."
               if hit else f"nothing enabled by beast-apps matches {a.name!r}.")
        emit({"disabled": [], "message": msg}, a.json, msg)
        return EXIT_USAGE
    gone = []
    for k in targets:
        gone += ocfg.uninstall(cfg, k, mine[k].get("rules", []))
    backup = None
    if not a.dry_run:
        backup = ocfg.backup(ctx.cfg_path)
        ocfg.write(ctx.cfg_path, cfg)
        for k in targets:
            del mine[k]
        ctx.st.save_enabled(mine)
    out = {"disabled": targets, "removed": gone, "backup": backup, "config": ctx.cfg_path,
           "dry_run": bool(a.dry_run)}
    emit(out, a.json, ("[dry run] would remove " if a.dry_run else "removed ") +
         (", ".join(gone) or "nothing (already gone from the file)") + f" from {ctx.cfg_path}" +
         (f"\n  backup: {backup}" if backup else "") + "\n  Restart opencode to drop it.")
    return EXIT_OK


def _targets(ctx: Ctx, names: list[str]) -> tuple[list[tuple[str, dict | None]], list[str]]:
    mine = ctx.st.enabled()
    out, bad = [], []
    if not names:
        return [(v["server"], dict(v, key=k)) for k, v in sorted(mine.items())], bad
    for n in names:
        n = C.resolve_server(ctx.cat, n) or n
        rec = next((dict(v, key=k) for k, v in mine.items() if n in (k, v.get("server"), v.get("capability"))), None)
        if rec:
            out.append((rec["server"], rec))
        elif n in ctx.cat["server"]:
            out.append((n, None))
        elif n in ctx.cat["capability"]:
            out.append((C.capability_servers(ctx.cat, n)[0], None))
        else:
            bad.append(n)
    return out, bad


def _launch_for(ctx: Ctx, sid: str, rec: dict | None, version: str):
    s = ctx.cat["server"][sid]
    if rec and rec.get("version") == version and s["transport"] == "stdio":
        e = rec.get("entry") or {}
        cmd = e.get("command") or []
        # the recorded command, unless a file it names has gone (a cleaned
        # state dir): then stage it again below.
        if cmd and all(os.path.exists(c) for c in cmd[:2] if os.path.isabs(c)):
            return cmd, e.get("environment") or {}, None
    ev = evaluate_server(ctx.cat, sid, ctx.env, ctx.st)
    if ev["status"] != "READY":
        return None, {}, "prerequisites missing: " + " | ".join(ev["missing"])
    return render_launch(ctx.cat, sid, version, ctx.env, ev, keep=((rec or {}).get("entry") or {}).get("environment"))


def _render_diff(sid: str, d: dict) -> list[str]:
    L = []
    if d["first"]:
        L.append(f"    first manifest: {len(d['added'])} tools")
    elif d["same"]:
        L.append("    tools unchanged (manifest hash matches)")
    else:
        if d["added"]:
            L.append("    + new: " + ", ".join(d["added"]))
        if d["removed"]:
            L.append("    - removed: " + ", ".join(d["removed"]))
        for c in d["changed"]:
            L.append(f"    ~ {c['name']}: {', '.join(c['changed'])} changed")
    for f in d["flagged"]:
        if f["gated"]:
            continue
        if f.get("description"):          # says it runs code / installs: always, first manifest too
            L.append(f"    ! tool {f['name']} describes itself as running code or installing something "
                     f"({', '.join(f['description'])}) and is NOT gated — add it to ask_tools in the "
                     f"catalog (or an overlay) to gate it")
        elif not d["first"]:
            L.append(f"    ! new tool {f['name']} matches risk pattern(s) {', '.join(f['patterns'])} and is NOT "
                     f"gated — review it; add it to ask_tools in the catalog (or an overlay) to gate it")
    return L


def cmd_refresh(ctx: Ctx) -> int:
    a = ctx.args
    targets, bad = _targets(ctx, a.servers)
    if bad:
        return unknown(ctx, bad[0])
    reason = offline_reason(ctx.env)
    out = {"offline": bool(reason), "offline_reason": reason, "servers": []}
    L = [island_banner(reason)] if reason else []
    if not targets:
        msg = "nothing enabled by beast-apps yet; name a server or capability: apps.sh refresh <name>"
        out["message"] = msg
        emit(out, a.json, "\n".join(L + [msg]))
        return EXIT_OK
    failures = 0
    for sid, rec in targets:
        s = ctx.cat["server"][sid]
        pinned = effective_version(ctx.cat, sid, ctx.st)
        row = {"server": sid, "pinned": pinned, "enabled_version": (rec or {}).get("version"),
               "latest": None, "update_available": False, "notes": []}
        L.append(f"{sid} (pinned {pinned}{', enabled as ' + rec['key'] if rec else ''})")
        if rec and rec.get("version") != pinned:
            n = (f"the pin is now {pinned} but your opencode entry runs {rec['version']}: "
                 f"scripts/apps.sh upgrade {sid} --to {pinned}")
            row["notes"].append(n)
            L.append(f"    ! {n}")
        rcmd = ((rec or {}).get("entry") or {}).get("command") or []
        if s["registry"] == "npm" and rcmd and os.path.basename(rcmd[0]) == "npx":
            n = (f"your opencode entry still launches through npx, which resolves the package in the "
                 f"project opencode opens (its node_modules/.npmrc can substitute the code): re-run "
                 f"scripts/apps.sh enable {rec['capability']} to run it from the beast-apps prefix")
            row["notes"].append(n)
            L.append(f"    ! {n}")
        if pinned != s["version"] and not P.version_ge(pinned, s["version"]):
            L.append(f"    pinned below the catalog's {s['version']} (an `upgrade --to` rollback); kept "
                     "until the catalog pin moves")
        if rec:
            pref = next((x for x in C.capability_servers(ctx.cat, rec["capability"])
                         if C.platform_id(ctx.env) in ctx.cat["server"][x]["platforms"]), None)
            if pref and pref != sid:
                n = (f"the catalog now prefers {pref} for {rec['capability']}; "
                     f"see scripts/apps.sh check {rec['capability']} --server {pref}")
                row["notes"].append(n)
                L.append(f"    ! {n}")
        if s["registry"] == "app":
            L.append("    version: follows the app (tracked by manifest hash)")
        elif reason:
            L.append(f"    latest: not checked (offline) — keeping {pinned}")
        else:
            latest, err = latest_version(s, ctx.env)
            row["latest"] = latest
            if err:
                row["notes"].append(err)
                L.append(f"    latest: unknown ({err}) — keeping {pinned}")
            elif latest and latest != pinned and P.version_ge(latest, pinned):
                row["update_available"] = True
                L.append(f"    UPDATE AVAILABLE: {pinned} → {latest} (explicit: scripts/apps.sh upgrade {sid})")
            else:
                L.append(f"    latest: {latest} (up to date)")
        if not a.no_introspect:
            cmd, environment, err = _launch_for(ctx, sid, rec, pinned)
            if err:
                row["introspection_error"] = err
                failures += 1
                L.append(f"    tools: could not start — {err}")
            else:
                try:
                    man = introspect(ctx.cat, sid, pinned, cmd, environment, ctx.env, bool(reason))
                except mcp_client.MCPError as e:
                    row["introspection_error"] = str(e)
                    failures += 1
                    L.append(f"    tools: introspection failed — {e}"
                             + (" (offline: the package is probably not in the local cache)" if reason else ""))
                else:
                    d = diff_manifests(ctx.st.last_manifest(sid), man, s)
                    path = ctx.st.save_manifest(sid, man)
                    row.update(tool_count=man["tool_count"], hash=man["hash"], manifest=path, diff=d)
                    L.append(f"    tools: {man['tool_count']} (hash {man['hash'][:12]}) → {path}")
                    L += _render_diff(sid, d)
        out["servers"].append(row)
    emit(out, a.json, "\n".join(L))
    return EXIT_ERR if failures and failures == len(targets) else EXIT_OK


def cmd_upgrade(ctx: Ctx) -> int:
    a = ctx.args
    targets, bad = _targets(ctx, [a.server])
    if bad:
        return unknown(ctx, bad[0])
    sid, rec = targets[0]
    s = ctx.cat["server"][sid]
    if s["registry"] == "app":
        print(f"{sid} is bundled with an app; upgrade the app, then run scripts/apps.sh refresh {sid}.")
        return EXIT_USAGE
    current = (rec or {}).get("version") or effective_version(ctx.cat, sid, ctx.st)
    if a.to is not None and not C.valid_version(a.to):
        # --to is substituted into `{package}@{version}`: an npm alias
        # (npm:other@1), file: or a repository spec would install a different
        # package under this server's name and gates.
        print(f"error: --to {a.to!r} is not an exact version (digits and dots, e.g. 1.2.3 or 2.3.0rc1)",
              file=sys.stderr)
        return EXIT_USAGE
    reason = offline_reason(ctx.env)
    target = a.to
    if not target:
        if reason:
            print(island_banner(reason) + f"\nupgrade needs the latest version from {s['registry']}; "
                  f"pass --to <version> to use a package already in the local cache.")
            return EXIT_OFFLINE
        target, err = latest_version(s, ctx.env)
        if not target:
            print(f"error: cannot find the latest version of {s['package']}: {err}", file=sys.stderr)
            return EXIT_ERR
    if target == current and not a.reintrospect:
        emit({"server": sid, "version": current, "changed": False}, a.json,
             f"{sid} is already at {current}. (--reintrospect to re-read its tools anyway)")
        return EXIT_OK
    ev = evaluate_server(ctx.cat, sid, ctx.env, ctx.st)
    if ev["status"] != "READY":
        print(f"Refusing to upgrade {sid}: prerequisites are missing on this machine:")
        for i, m in enumerate(ev["missing"], 1):
            print(f"  {i}. {m}")
        return EXIT_MISSING if ev["status"] == "MISSING" else EXIT_UNSUPPORTED
    if rec and rec.get("rules"):
        # The entry is gated today. If the config can no longer hold those
        # gates, rewriting it would drop them silently: refuse before any
        # pin, manifest or config is written, unless --ungated says so.
        try:
            cfg0 = ctx.cfg()
        except ocfg.ConfigError as e:
            print(f"error: {e}", file=sys.stderr)
            return EXIT_ERR
        fl0 = ocfg.flavor_for(cfg0, rec["key"], ctx.env, rec.get("schema"))
        why = ocfg.ungatable(cfg0, ocfg.rules_for(rec["key"], s), fl0)
        if why and not a.ungated:
            print(f"Refusing to upgrade {sid}: {why}.\n  The entry {rec['key']} is gated now and would lose "
                  f"its gates. Make `permission` an object in {ctx.cfg_path} (e.g. {{\"*\": \"allow\"}}) and "
                  "re-run, or pass --ungated to upgrade without the gates anyway.")
            return EXIT_RISK
    keep = ((rec or {}).get("entry") or {}).get("environment")
    cmd, environment, err = render_launch(ctx.cat, sid, target, ctx.env, ev, keep=keep, offline=bool(reason))
    if err:
        print(f"Upgrade aborted: {err}. Pin stays {current}.")
        return EXIT_MISSING if "not found" in err else EXIT_ERR
    try:
        man = introspect(ctx.cat, sid, target, cmd, environment, ctx.env, bool(reason))
    except mcp_client.MCPError as e:
        print(f"Upgrade aborted: {sid} {target} did not answer tools/list ({e}). Pin stays {current}.")
        return EXIT_ERR
    d = diff_manifests(ctx.st.last_manifest(sid), man, s)
    # The same rule _render_diff prints: a tool that says it runs code, or a
    # NEW tool matching risk_patterns, with no gate. refresh only reports it
    # (the running entry is unchanged); upgrade would WIRE it in, so it stops
    # here, before any pin, manifest or config is written.
    risky = [f["name"] for f in d["flagged"] if not f["gated"] and (f.get("description") or not d["first"])]
    if risky and not a.accept_new_tools:
        L = [f"Refusing to upgrade {sid} to {target}: it brings tool(s) flagged as risky and NOT gated: "
             f"{', '.join(risky)}."] + _render_diff(sid, d)
        L.append(f"  Pin stays {current}; nothing was written. Gate them (ask_tools in the catalog or an "
                 f"overlay, docs/BEAST_APPS.md), or pass --accept-new-tools to wire them in ungated.")
        emit({"server": sid, "from": current, "to": target, "refused": True, "flagged_ungated": risky,
              "diff": d}, a.json, "\n".join(L))
        return EXIT_RISK
    path = ctx.st.save_manifest(sid, man)
    pins = ctx.st.pins()
    # `base` = the catalog pin this choice was made against: while the catalog
    # still says that, this pin wins even when it is OLDER (a rollback).
    pins[sid] = {"version": target, "previous": current, "base": s["version"], "at": now_iso()}
    ctx.st.save_pins(pins)
    out = {"server": sid, "from": current, "to": target, "manifest": path, "diff": d,
           "config_updated": False, "backup": None, "accepted_ungated": risky}
    L = [f"{sid}: {current} → {target}", f"  tools: {man['tool_count']} (hash {man['hash'][:12]}) → {path}"]
    L += _render_diff(sid, d)
    if risky:
        L.append(f"  ! --accept-new-tools: wired in UNGATED: {', '.join(risky)}")
    if not P.version_ge(target, s["version"]):
        L.append(f"  rollback below the catalog's {s['version']}: this pin is kept until the catalog pin moves")
    if rec:
        try:
            cfg = ctx.cfg()
        except ocfg.ConfigError as e:
            print(f"error: {e}", file=sys.stderr)
            return EXIT_ERR
        key = rec["key"]
        # The layout the file has NOW, not the one recorded at enable time:
        # setup-client.sh moves v1 entries into mcp.servers once opencode 2.x
        # is installed, and writing a v1 mcp.<key> next to mcp.servers is the
        # mixed layout opencode 2 reads as "No MCP servers configured".
        fl = ocfg.flavor_for(cfg, key, ctx.env, rec.get("schema"))
        entry = ocfg.build_entry(s, cmd, environment, fl)
        ocfg.uninstall(cfg, key, rec.get("rules", []))
        rules = ocfg.rules_for(key, s)
        why = ocfg.ungatable(cfg, rules, fl)
        if why:          # the old entry had no gates either, or --ungated (checked above)
            rules = []
            L.append(f"  ! NOT gated{' (--ungated)' if a.ungated else ''}: {why}")
        rules, warns = ocfg.install(cfg, key, entry, rules, fl)
        L += [f"  ! {w}" for w in warns]
        out["backup"] = ocfg.backup(ctx.cfg_path)
        ocfg.write(ctx.cfg_path, cfg, secret=private_config(s, environment, ctx.cfg_path))
        mine = ctx.st.enabled()
        mine[key].update(version=target, entry=entry, rules=rules, schema=fl, at=now_iso())
        ctx.st.save_enabled(mine)
        out["config_updated"] = True
        out["schema"] = fl
        L.append(f"  opencode entry {key} now runs {target} (backup {out['backup']}). Restart opencode.")
    else:
        L.append("  pin recorded; it is used the next time you enable this server.")
    emit(out, a.json, "\n".join(L))
    return EXIT_OK


def cmd_tools(ctx: Ctx) -> int:
    a = ctx.args
    targets, bad = _targets(ctx, [a.server])
    if bad:
        return unknown(ctx, bad[0])
    sid = targets[0][0]
    man = ctx.st.last_manifest(sid)
    if not man:
        msg = f"no tool manifest for {sid} yet: run scripts/apps.sh refresh {sid}"
        emit({"server": sid, "manifest": None, "message": msg}, a.json, msg)
        return EXIT_USAGE
    if a.json:
        print(json.dumps(man, indent=2))
    else:
        print(f"{sid} {man['version']} — {man['tool_count']} tools (introspected {man['introspected_at']})")
        for t in man["tools"]:
            print(f"  {t['name']}: {t['description'].splitlines()[0][:110] if t['description'] else ''}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apps.sh", description="beast-apps: external apps through MCP servers "
                                "(docs/BEAST_APPS.md)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, json_=True, cfg=True):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        if json_:
            sp.add_argument("--json", action="store_true", help="machine-readable output")
        if cfg:
            sp.add_argument("--config", help="opencode.json to read/write (default "
                            "$OPENBEAST_OPENCODE_CONFIG or ~/.config/opencode/opencode.json)")
        return sp

    add("list", cmd_list, "capabilities and their status on this machine")
    sp = add("find", cmd_find, "which capability covers a request", cfg=False)
    sp.add_argument("query", nargs="+")
    sp.add_argument("--limit", type=int, default=3)
    sp = add("check", cmd_check, "run a capability's prerequisite probes")
    sp.add_argument("capability")
    sp.add_argument("--server", help="check one candidate server only")
    sp = add("enable", cmd_enable, "merge the MCP server into the opencode config")
    sp.add_argument("capability")
    sp.add_argument("--server", help="a specific candidate (default: the first READY one)")
    sp.add_argument("--key", help="opencode entry name (default: the server id)")
    sp.add_argument("--accept-risk", action="store_true", help="enable a server that is off by default")
    sp.add_argument("--dry-run", action="store_true", help="print what would be written")
    sp.add_argument("--ungated", action="store_true",
                    help="enable even when the config cannot hold its ask/deny gates")
    sp.add_argument("--allow-duplicate", action="store_true",
                    help="add it even though your own entry already runs the same package")
    sp = add("disable", cmd_disable, "remove what enable wrote")
    sp.add_argument("name", help="capability, server id or opencode key")
    sp.add_argument("--dry-run", action="store_true")
    sp = add("refresh", cmd_refresh, "check latest versions and re-read tool lists")
    sp.add_argument("servers", nargs="*")
    sp.add_argument("--no-introspect", action="store_true", help="registry check only")
    sp = add("upgrade", cmd_upgrade, "move a server's pin (re-probes, re-reads tools)")
    sp.add_argument("server")
    sp.add_argument("--to", help="exact version, e.g. 1.2.3; lower than now is a rollback "
                    "(default: latest published)")
    sp.add_argument("--reintrospect", action="store_true")
    sp.add_argument("--accept-new-tools", action="store_true",
                    help="upgrade even though the new version brings ungated tools flagged as risky")
    sp.add_argument("--ungated", action="store_true",
                    help="upgrade even when the config can no longer hold the entry's ask/deny gates")
    sp = add("tools", cmd_tools, "show the recorded tool manifest", cfg=False)
    sp.add_argument("server")
    add("validate", cmd_validate, "validate the catalog", json_=False, cfg=False)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = Ctx(args)
    except C.CatalogError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERR
    if args.cmd in MIGRATING and not getattr(args, "dry_run", False):
        for m in migrate_legacy(ctx):        # stderr: --json output stays one object
            print(m, file=sys.stderr)
    return args.fn(ctx)


if __name__ == "__main__":
    sys.exit(main())
