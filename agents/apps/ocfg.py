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

import contextlib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

try:
    import fcntl
except ImportError:          # not a POSIX host: no lock, as before
    fcntl = None


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


def flavor_for(cfg: dict, key: str, env: dict, recorded: str | None = None) -> str:
    """The schema to REWRITE an existing entry in: what the file is now.

    The schema recorded at enable time can be stale: setup-client.sh moves
    every v1 mcp.<name> entry into mcp.servers once opencode 2.x is there,
    beast-apps' own included. Order: OPENBEAST_OPENCODE_SCHEMA; a file with
    v2 markers (mcp.servers / a `permissions` list) is v2; where the entry
    sits now; the recorded schema; detection."""
    forced = (env.get("OPENBEAST_OPENCODE_SCHEMA") or "").lower()
    if forced in ("v1", "v2"):
        return forced
    mcp = cfg.get("mcp")
    if (isinstance(mcp, dict) and isinstance(mcp.get("servers"), dict)) or isinstance(cfg.get("permissions"), list):
        return "v2"
    hit = find_entry(cfg, key)
    if hit:
        return hit[0]
    return recorded if recorded in ("v1", "v2") else flavor(cfg, env)


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


def ungatable(cfg: dict, rules: list[dict], fl: str) -> str | None:
    """Why `rules` cannot be written into cfg, or None when they can.

    opencode 1.x accepts `"permission": "allow"` (a string for every tool);
    there is then nowhere to put a per-tool ask rule. Callers must refuse
    rather than wire in a server whose only safety is that gate."""
    if not rules:
        return None
    if fl == "v2":
        return None if isinstance(cfg.get("permissions", []), list) else \
            "`permissions` is not a list, so the per-tool gates cannot be written"
    perm = cfg.get("permission", {})
    if isinstance(perm, dict):
        return None
    if isinstance(perm, str):
        what = f"the string {perm!r}"
    elif perm is None:
        what = "null"
    elif isinstance(perm, list):
        what = "a list"
    elif isinstance(perm, bool):
        what = f"the boolean {str(perm).lower()}"
    elif isinstance(perm, (int, float)):
        what = "a number"
    else:
        what = f"a {type(perm).__name__}"
    return (f"`permission` is {what}, not an object, so the per-tool gates cannot be "
            "written: " + ", ".join(f"{r['action']}={r['effect']}" for r in rules))


# How strict an effect is. A gate we add must never LOOSEN a rule the user
# already has for the same action: their "deny" stays a deny.
STRICTNESS = {"allow": 0, "ask": 1, "deny": 2}


def _stricter_or_equal(have, ours: str) -> bool:
    return isinstance(have, str) and STRICTNESS.get(have, -1) >= STRICTNESS[ours]


def _same_rule(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("action", "resource", "effect"))


def effective(cfg: dict, action: str) -> str | None:
    """The effect opencode applies to `action` today, or None when no rule
    names it. Both schemas, last match wins, globs included (a user's
    `mobile-build-tools_*: deny` covers `mobile-build-tools_build_*`)."""
    eff = None
    perm = cfg.get("permission")
    if isinstance(perm, str):
        eff = perm
    elif isinstance(perm, dict):
        if isinstance(perm.get(action), str):
            eff = perm[action]
        else:
            for k, v in perm.items():
                if isinstance(v, str) and fnmatch.fnmatchcase(action, k):
                    eff = v
    perms = cfg.get("permissions")
    if isinstance(perms, list):
        for p in perms:
            if (isinstance(p, dict) and isinstance(p.get("action"), str) and p.get("resource", "*") == "*"
                    and isinstance(p.get("effect"), str) and fnmatch.fnmatchcase(action, p["action"])):
                eff = p["effect"]
    return eff


def is_gated(cfg: dict, key: str, tool: str) -> bool:
    """Whether opencode asks (or denies) before the model runs `tool` of the
    entry `key`, by the rules actually in the file."""
    return STRICTNESS.get(effective(cfg, f"{_safe(key)}_{_safe(tool)}") or "", 0) >= STRICTNESS["ask"]


def add_gates(cfg: dict, rules: list[dict], fl: str) -> tuple[list[dict], list[str]]:
    """Write the gates in `rules` that the file does not already hold, and
    only ever TIGHTEN: a rule of the user's that is as strict for that exact
    action is left alone (not recorded), and so is one that is stricter by a
    glob (appending our weaker rule last would override it). Returns
    (written, warnings); `written` is what uninstall() must undo:
      - v1: a weaker user value (allow) is replaced by ours and recorded with
        "prev", so uninstall puts it back;
      - v2: our rule is appended (last match wins)."""
    warns: list[str] = []
    written: list[dict] = []
    if not rules:
        return written, warns
    if fl == "v2":
        perms = cfg.setdefault("permissions", [])
        if not isinstance(perms, list):
            raise ConfigError("`permissions` is not a list; refusing to touch it")
        for r in rules:
            last = next((p for p in reversed(perms) if isinstance(p, dict)
                         and p.get("action") == r["action"] and p.get("resource", "*") == r["resource"]), None)
            if last is not None and _stricter_or_equal(last.get("effect"), r["effect"]):
                if last.get("effect") != r["effect"]:
                    warns.append(f"kept your stricter rule {r['action']}={last.get('effect')}")
                continue
            eff = effective(cfg, r["action"])
            if STRICTNESS.get(eff or "", -1) > STRICTNESS[r["effect"]]:
                warns.append(f"kept your stricter rule for {r['action']} ({eff})")
                continue
            perms.append(dict(r))      # last match wins: ours go last
            written.append(dict(r))
        if not perms:
            del cfg["permissions"]
        return written, warns
    perm = cfg.setdefault("permission", {})
    if not isinstance(perm, dict):
        warns.append(f"`permission` is the string {perm!r}, not an object, so the per-tool "
                     "gates were NOT written: " + ", ".join(f"{r['action']}={r['effect']}" for r in rules))
        return written, warns
    for r in rules:
        have = perm.get(r["action"])
        if have is not None and _stricter_or_equal(have, r["effect"]):
            if have != r["effect"]:
                warns.append(f"kept your stricter rule {r['action']}={have}")
            continue
        if have is None:
            eff = effective(cfg, r["action"])
            if STRICTNESS.get(eff or "", -1) > STRICTNESS[r["effect"]]:
                warns.append(f"kept your stricter rule for {r['action']} ({eff})")
                continue
        rec = dict(r)
        if have is not None:
            rec["prev"] = have          # restored on uninstall
        perm[r["action"]] = r["effect"]
        written.append(rec)
    if not perm:
        del cfg["permission"]
    return written, warns


def install(cfg: dict, key: str, entry: dict, rules: list[dict], fl: str) -> tuple[list[dict], list[str]]:
    """Merge one server + its gates into cfg in place.

    Returns (written, warnings). `written` is exactly what this call changed,
    and is what the caller must record so uninstall() undoes only that (see
    add_gates: a rule the user already has at the same or a stricter effect
    is left alone and not recorded)."""
    mcp = cfg.setdefault("mcp", {})
    if not isinstance(mcp, dict):
        raise ConfigError("the config's `mcp` is not an object; refusing to touch it")
    if fl == "v2":
        servers = mcp.setdefault("servers", {})
        if not isinstance(servers, dict):
            raise ConfigError("mcp.servers is not an object; refusing to touch it")
        servers[key] = entry
    else:
        mcp[key] = entry
    return add_gates(cfg, rules, fl)


def uninstall(cfg: dict, key: str, rules: list[dict]) -> list[str]:
    """Remove one server and exactly the gates install() recorded (`rules`),
    putting back any value a v1 gate replaced. Returns what went."""
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
        # One list entry per recorded rule, the LAST matching one (ours were
        # appended), so a user rule identical to ours earlier in the list stays.
        keep = list(perms)
        for r in rules:
            for i in range(len(keep) - 1, -1, -1):
                if isinstance(keep[i], dict) and _same_rule(keep[i], r):
                    del keep[i]
                    break
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
                if "prev" in r:
                    perm[r["action"]] = r["prev"]
                else:
                    del perm[r["action"]]
                n += 1
        if n:
            gone.append(f"{n} permission gate(s)")
        if not perm:
            del cfg["permission"]
    return gone


def _rekey(action, po: str, pn: str):
    return pn + action[len(po):] if isinstance(action, str) and action.startswith(po) else action


def rekey_rules(cfg: dict, old: str, new: str) -> int:
    """Re-key EVERY permission rule in the file named after `old`'s tools
    (old_<tool>, globs included) to `new`, in place and in order, the user's
    own rules as well as ours: opencode names a tool's permission after the
    entry's key, so a `mobilebuildmcp_* = deny` the user wrote stops matching
    the moment the entry is renamed and those tools fall through to allow.
    v1: when the new action is already there the stricter value stays.
    Returns how many rules moved."""
    po, pn = _safe(old) + "_", _safe(new) + "_"
    n = 0
    perms = cfg.get("permissions")
    if isinstance(perms, list):
        for p in perms:
            if isinstance(p, dict) and _rekey(p.get("action"), po, pn) != p.get("action"):
                p["action"] = _rekey(p["action"], po, pn)
                n += 1
    perm = cfg.get("permission")
    if isinstance(perm, dict) and any(_rekey(k, po, pn) != k for k in perm):
        out: dict = {}
        for k, v in perm.items():
            nk = _rekey(k, po, pn)
            n += nk != k
            if nk in out and not (isinstance(v, str) and STRICTNESS.get(v, -1) > STRICTNESS.get(out[nk], -1)):
                continue
            out[nk] = v
        cfg["permission"] = out
    return n


def rename(cfg: dict, old: str, new: str, rules: list[dict], fl: str,
           server: dict | None = None) -> tuple[list[dict], list[str]] | None:
    """Move the entry beast-apps wrote under `old` to `new` in place. Every
    rule in the file keyed old_<tool> moves to new_<tool> (rekey_rules: the
    user's own deny/ask rules included), then the catalog's CURRENT gates
    for `new` (rules_for(new, server)) are applied, tightening only. `rules`
    are the gates install() recorded for `old`. Returns (written, warnings)
    for `new`: our re-keyed records plus any gate added now, or None when
    there is no `old` entry in the file."""
    hit = find_entry(cfg, old)
    if not hit:
        return None
    entry = hit[1]
    uninstall(cfg, old, [])                  # the entry only; the rules move below
    n = rekey_rules(cfg, old, new)
    ours = [dict(r, action=_rekey(r["action"], _safe(old) + "_", _safe(new) + "_"))
            for r in rules if isinstance(r.get("action"), str)]
    users = n - len(ours)
    want = rules_for(new, server) if server else []
    if want and ungatable(cfg, want, fl):
        want = []
    written, warns = install(cfg, new, entry, want, fl)
    if users > 0:
        warns.insert(0, f"moved {users} rule(s) of yours from {_safe(old)}_* to {_safe(new)}_*")
    return ours + written, warns


def backup(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.bak-beast-apps-{stamp}"
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = f"{path}.bak-beast-apps-{stamp}-{n}"
    # The config may hold an API key: the copy is CREATED 0600 (O_EXCL, never
    # an existing file). shutil.copy2 created it with the umask (0644) and
    # wrote the key before a chmod, so another local user could open it in
    # between and keep the fd.
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
            shutil.copyfileobj(src, out)
    except BaseException:
        try:
            os.unlink(dst)
        except OSError:
            pass
        raise
    prune_backups(path)
    return dst


KEEP_BACKUPS = 3


def prune_backups(path: str, keep: int = KEEP_BACKUPS) -> list[str]:
    """Keep the newest `keep` opencode.json.bak-beast-apps-* (each may hold
    an API key); delete the rest. Returns what went."""
    d, base = os.path.dirname(path) or ".", os.path.basename(path) + ".bak-beast-apps-"
    try:
        olds = [os.path.join(d, n) for n in os.listdir(d) if n.startswith(base)]
    except OSError:
        return []
    olds.sort(key=lambda p: (os.path.getmtime(p), p))
    gone = []
    for p in olds[:-keep] if keep > 0 else olds:
        try:
            os.unlink(p)
            gone.append(p)
        except OSError:
            pass
    return gone


def lock_path(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "openbeast", ".lock")


_held = 0


@contextlib.contextmanager
def config_lock(env: dict | None = None):
    """flock ~/.config/openbeast/.lock — the lock scripts/lib/client_rigs.py
    holds from load to save of opencode.json, so a beast-apps write and an
    openbeast-client sync (autosync, every 30 s) never interleave and drop
    each other's change. Re-entrant within one process."""
    global _held
    if fcntl is None or _held:
        _held += 1
        try:
            yield
        finally:
            _held -= 1
        return
    p = lock_path(env)
    fd = None
    try:
        os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        if fd is not None:
            os.close(fd)
        fd = None                       # unlockable (read-only home): carry on unlocked
    _held += 1
    try:
        yield
    finally:
        _held -= 1
        if fd is not None:
            os.close(fd)


def write(path: str, cfg: dict, secret: bool = False) -> None:
    # Through the symlink: a symlinked opencode.json (dotfiles repo) stays a
    # symlink, its target is what gets replaced.
    with config_lock():
        real = os.path.realpath(path)
        d = os.path.dirname(real) or "."
        os.makedirs(d, exist_ok=True)
        mode = 0o600 if secret else (os.stat(real).st_mode & 0o777 if os.path.exists(real) else 0o644)
        fd, tmp = tempfile.mkstemp(prefix=".opencode.json.", dir=d)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cfg, f, indent=2)
                f.write("\n")
            os.chmod(tmp, mode)
            os.replace(tmp, real)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
