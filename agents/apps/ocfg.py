"""beast-apps ↔ opencode config (docs/BEAST_APPS.md §opencode).

Two schemas, both handled:

  v1 (opencode 1.x)  mcp.<name> = {type, command, environment, enabled}
                     tool gates: permission.<tool> = "ask"|"deny" (globs ok)
  v2 (opencode 2.x)  mcp.servers.<name> = {type, command, environment,
                     disabled?, codemode}; tool gates: top-level
                     permissions = [{action, resource, effect}], last match
                     wins. v2 still reads v1 entries (it migrates them), but
                     a migrated entry cannot carry codemode, so on v2 we
                     write the v2 form.

The v2 shape was read from opencode 2.0.25 itself (`opencode mcp add` writes
mcp.servers.<name>; the config schema has additionalProperties: false, so an
"enabled" key there would make the whole file invalid) and from
https://opencode.ai/config.json, checked 2026-11-15. An MCP tool's permission
action is "<server key>_<tool>" (opencode namespaces MCP tools by server).

Never removes an entry we did not write: ownership lives in the state dir
(enabled.json), not in the config file (v2 forbids extra keys). Every write
backs the old file up first and is atomic.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time


class ConfigError(Exception):
    pass


def config_path(explicit: str | None = None, env: dict | None = None) -> str:
    env = os.environ if env is None else env
    if explicit:
        return os.path.expanduser(explicit)
    if env.get("OPENBEAST_OPENCODE_CONFIG"):
        return os.path.expanduser(env["OPENBEAST_OPENCODE_CONFIG"])
    base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "opencode", "opencode.json")


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            text = f.read()
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from None
    if not text.strip():
        return {}
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"{path} is not plain JSON ({e.msg} at line {e.lineno}); it may hold comments "
            "(JSONC). Refusing to rewrite it — add the entry by hand, or remove the comments."
        ) from None
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path}: top level is not a JSON object")
    return cfg


def opencode_major(env: dict) -> int | None:
    bin_ = env.get("OPENBEAST_OPENCODE_BIN") or shutil.which("opencode", path=env.get("PATH", ""))
    if not bin_:
        return None
    try:
        cp = subprocess.run([bin_, "--version"], capture_output=True, text=True, timeout=10,
                            env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+)\.\d+", (cp.stdout or "") + (cp.stderr or ""))
    return int(m.group(1)) if m else None


def flavor(cfg: dict, env: dict) -> str:
    """'v2' or 'v1'. OPENBEAST_OPENCODE_SCHEMA wins; then the file itself
    (mcp.servers present → v2); then the installed opencode's major version;
    unknown → v1 (which v2 also reads)."""
    forced = (env.get("OPENBEAST_OPENCODE_SCHEMA") or "").lower()
    if forced in ("v1", "v2"):
        return forced
    mcp = cfg.get("mcp")
    if isinstance(mcp, dict) and isinstance(mcp.get("servers"), dict):
        return "v2"
    if isinstance(cfg.get("permissions"), list):
        return "v2"
    major = opencode_major(env)
    if major is not None and major >= 2:
        return "v2"
    return "v1"


def entries(cfg: dict) -> dict[str, dict]:
    """Every MCP server entry in the file, both schemas: {key: entry}."""
    out: dict[str, dict] = {}
    mcp = cfg.get("mcp")
    if not isinstance(mcp, dict):
        return out
    for k, v in mcp.items():
        if k in ("servers", "timeout"):
            continue
        if isinstance(v, dict) and ("command" in v or "url" in v):
            out[k] = v
    servers = mcp.get("servers")
    if isinstance(servers, dict):
        for k, v in servers.items():
            if isinstance(v, dict):
                out[k] = v
    return out


def find_entry(cfg: dict, key: str) -> tuple[str, dict] | None:
    mcp = cfg.get("mcp")
    if not isinstance(mcp, dict):
        return None
    if isinstance(mcp.get("servers"), dict) and isinstance(mcp["servers"].get(key), dict):
        return "v2", mcp["servers"][key]
    if key not in ("servers", "timeout") and isinstance(mcp.get(key), dict):
        return "v1", mcp[key]
    return None


def build_entry(server: dict, command: list[str] | None, environment: dict, fl: str) -> dict:
    if server["transport"] == "http":
        e = {"type": "remote", "url": server["url"]}
    else:
        e = {"type": "local", "command": command}
        if environment:
            e["environment"] = environment
    if fl == "v2":
        e["codemode"] = False      # plain tools: a local model does not write Code Mode
    else:
        e["enabled"] = True
    return e


def _safe(name: str) -> str:
    # opencode 2.0.25 names an MCP tool's permission action
    # `${s(server)}_${s(tool)}` with s = replace(/[^a-zA-Z0-9_-]/g, "_").
    # Keep * and ? — they are our glob, matched by opencode's wildcard rule.
    return re.sub(r"[^a-zA-Z0-9_*?-]", "_", name)


def rules_for(key: str, server: dict) -> list[dict]:
    rules = []
    for t in server.get("ask_tools", []):
        rules.append({"action": f"{_safe(key)}_{_safe(t)}", "resource": "*", "effect": "ask"})
    for t in server.get("deny_tools", []):
        rules.append({"action": f"{_safe(key)}_{_safe(t)}", "resource": "*", "effect": "deny"})
    return rules


def install(cfg: dict, key: str, entry: dict, rules: list[dict], fl: str) -> list[str]:
    """Merge one server + its gates into cfg in place. Returns warnings."""
    warns: list[str] = []
    mcp = cfg.setdefault("mcp", {})
    if not isinstance(mcp, dict):
        raise ConfigError("the config's `mcp` is not an object; refusing to touch it")
    if fl == "v2":
        servers = mcp.setdefault("servers", {})
        if not isinstance(servers, dict):
            raise ConfigError("mcp.servers is not an object; refusing to touch it")
        servers[key] = entry
        if rules:
            perms = cfg.setdefault("permissions", [])
            if not isinstance(perms, list):
                raise ConfigError("`permissions` is not a list; refusing to touch it")
            for r in rules:
                if r not in perms:
                    perms.append(r)        # last match wins: ours go last
    else:
        mcp[key] = entry
        if rules:
            perm = cfg.setdefault("permission", {})
            if isinstance(perm, dict):
                for r in rules:
                    perm[r["action"]] = r["effect"]
            else:
                warns.append(f"`permission` is the string {perm!r}, not an object, so the per-tool "
                             "gates were NOT written: " + ", ".join(f"{r['action']}={r['effect']}" for r in rules))
    return warns


def uninstall(cfg: dict, key: str, rules: list[dict]) -> list[str]:
    """Remove one server and exactly the gates we added. Returns what went."""
    gone: list[str] = []
    mcp = cfg.get("mcp")
    if isinstance(mcp, dict):
        servers = mcp.get("servers")
        if isinstance(servers, dict) and key in servers:
            del servers[key]
            gone.append(f"mcp.servers.{key}")
            if not servers:
                del mcp["servers"]
        if key not in ("servers", "timeout") and isinstance(mcp.get(key), dict):
            del mcp[key]
            gone.append(f"mcp.{key}")
        if not mcp:
            del cfg["mcp"]
    perms = cfg.get("permissions")
    if isinstance(perms, list):
        keep = [r for r in perms if r not in rules]
        if len(keep) != len(perms):
            gone.append(f"{len(perms) - len(keep)} permissions rule(s)")
            if keep:
                cfg["permissions"] = keep
            else:
                del cfg["permissions"]
    perm = cfg.get("permission")
    if isinstance(perm, dict):
        n = 0
        for r in rules:
            if perm.get(r["action"]) == r["effect"]:
                del perm[r["action"]]
                n += 1
        if n:
            gone.append(f"{n} permission gate(s)")
        if not perm:
            del cfg["permission"]
    return gone


def backup(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.bak-beast-apps-{stamp}"
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = f"{path}.bak-beast-apps-{stamp}-{n}"
    shutil.copy2(path, dst)
    os.chmod(dst, 0o600)          # the config may hold an API key
    return dst


def write(path: str, cfg: dict, secret: bool = False) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    mode = 0o600 if secret else (os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644)
    fd, tmp = tempfile.mkstemp(prefix=".opencode.json.", dir=d)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
