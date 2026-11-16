"""beast-apps catalog: load, overlay, validate (docs/BEAST_APPS.md).

The catalog is data (catalog.toml next to this file). A local overlay
directory — $OPENBEAST_APPS_CATALOG_D, else <state dir>/catalog.d — may add
or override entries: each *.toml there is merged entry by entry, field by
field, over the repo catalog, in file-name order. That is how a user adds a
server the repo does not ship yet, or a newer MCP release, without editing
code or the checkout.

Stdlib only (tomllib, Python 3.11+): this ships to client-mode machines.
"""
from __future__ import annotations

import glob
import os
import re
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_CATALOG = os.path.join(HERE, "catalog.toml")

SCHEMA = 1
TABLES = ("capability", "server", "probe")
PROBE_KINDS = ("command", "path", "env", "tcp", "any")
TRANSPORTS = ("stdio", "http")
REGISTRIES = ("npm", "pypi", "github", "app")
PLATFORMS = ("darwin", "linux")
OFF_PATH = ("ok", "warn", "fail")
# An exact release version, and nothing else: digits and dots, then an
# optional semver / PEP 440 suffix (1.2.3-beta.1, 2.3.0rc1, 1.0.0.post1).
# A version is substituted into `{package}@{version}`, so anything wider is a
# different install spec, not a version: `npm:other-pkg@9` is an npm alias
# (another package under this name), `file:../x` and `github:o/r` install
# local or repository code. ':', '/', '@' and whitespace can never pass.
VERSION_RE = re.compile(r"\d+(?:\.\d+){0,3}(?:[-.+]?[0-9A-Za-z]+(?:[-.+][0-9A-Za-z]+)*)?")


def valid_version(v) -> bool:
    return isinstance(v, str) and len(v) <= 64 and VERSION_RE.fullmatch(v) is not None


class CatalogError(Exception):
    pass


def state_dir(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    d = env.get("OPENBEAST_APPS_STATE")
    if d:
        return os.path.expanduser(d)
    return os.path.join(os.path.expanduser("~"), ".openbeast-client", "apps")


def overlay_dir(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    return env.get("OPENBEAST_APPS_CATALOG_D") or os.path.join(state_dir(env), "catalog.d")


def platform_id(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    forced = env.get("OPENBEAST_APPS_PLATFORM")
    if forced:
        return forced
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def _read(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise CatalogError(f"{path}: not valid TOML: {e}") from None
    except OSError as e:
        raise CatalogError(f"{path}: cannot read: {e}") from None


def load(path: str | None = None, env: dict | None = None, overlays: bool = True) -> dict:
    """The effective catalog: repo file (or `path`) plus local overlays."""
    path = path or (os.environ if env is None else env).get("OPENBEAST_APPS_CATALOG") or REPO_CATALOG
    cat = _read(path)
    cat.setdefault("schema", SCHEMA)
    for t in TABLES:
        cat.setdefault(t, {})
    cat["_sources"] = [path]
    if overlays:
        for extra in sorted(glob.glob(os.path.join(overlay_dir(env), "*.toml"))):
            over = _read(extra)
            for t in TABLES:
                for key, entry in (over.get(t) or {}).items():
                    if not isinstance(entry, dict):
                        raise CatalogError(f"{extra}: [{t}.{key}] is not a table")
                    merged = dict(cat[t].get(key, {}))
                    merged.update(entry)
                    cat[t][key] = merged
            cat["_sources"].append(extra)
    errors = validate(cat)
    if errors:
        raise CatalogError("catalog is invalid:\n  " + "\n  ".join(errors))
    return cat


def _strs(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def validate(cat: dict) -> list[str]:
    """Every problem in the catalog, as human-readable lines (empty = valid)."""
    errs: list[str] = []
    if cat.get("schema") != SCHEMA:
        errs.append(f"schema must be {SCHEMA}, got {cat.get('schema')!r}")
    caps, servers, probes = cat.get("capability", {}), cat.get("server", {}), cat.get("probe", {})
    for cid, c in caps.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", cid):
            errs.append(f"capability.{cid}: id must be lower-case kebab")
        if not isinstance(c.get("title"), str):
            errs.append(f"capability.{cid}: missing title")
        if not _strs(c.get("servers")) or not c.get("servers"):
            errs.append(f"capability.{cid}: servers must be a non-empty list of server ids")
        for s in c.get("servers") or []:
            if s not in servers:
                errs.append(f"capability.{cid}: unknown server {s!r}")
        if "keywords" in c and not _strs(c["keywords"]):
            errs.append(f"capability.{cid}: keywords must be a list of strings")
    for sid, s in servers.items():
        p = f"server.{sid}"
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", sid):
            errs.append(f"{p}: id must be lower-case kebab (it becomes the opencode key)")
        for req in ("title", "source", "checked", "registry", "package", "version", "transport"):
            if not isinstance(s.get(req), str) or not s.get(req):
                errs.append(f"{p}: missing {req}")
        if s.get("registry") not in REGISTRIES:
            errs.append(f"{p}: registry must be one of {REGISTRIES}")
        if s.get("registry") == "github" and not s.get("repo"):
            errs.append(f"{p}: registry github needs repo = 'owner/name'")
        if s.get("checked") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(s.get("checked"))):
            errs.append(f"{p}: checked must be YYYY-MM-DD")
        if s.get("registry") != "app" and s.get("version") in (None, "", "latest"):
            errs.append(f"{p}: version must be an exact pin, not latest")
        elif s.get("registry") != "app" and not valid_version(s.get("version")):
            errs.append(f"{p}: version {s.get('version')!r} is not an exact release version")
        t = s.get("transport")
        if t not in TRANSPORTS:
            errs.append(f"{p}: transport must be one of {TRANSPORTS}")
        if t == "stdio" and (not _strs(s.get("command")) or not s.get("command")):
            errs.append(f"{p}: stdio transport needs command = [argv...]")
        if t == "stdio" and _strs(s.get("command")) and s.get("registry") in ("npm", "pypi") \
                and not any("{version}" in a for a in s["command"]):
            errs.append(f"{p}: command must pin {{version}} (an unpinned npx/uvx runs whatever is latest)")
        if t == "http" and not isinstance(s.get("url"), str):
            errs.append(f"{p}: http transport needs url")
        plats = s.get("platforms")
        if not _strs(plats) or not plats or any(x not in PLATFORMS for x in plats):
            errs.append(f"{p}: platforms must be a non-empty subset of {PLATFORMS}")
        for lst in ("probes", "ask_tools", "deny_tools", "risk_patterns", "env_passthrough", "secret_env"):
            if lst in s and not _strs(s[lst]):
                errs.append(f"{p}: {lst} must be a list of strings")
        for pr in s.get("probes") or []:
            if pr not in probes:
                errs.append(f"{p}: unknown probe {pr!r}")
        if "env" in s and not (isinstance(s["env"], dict) and all(isinstance(v, str) for v in s["env"].values())):
            errs.append(f"{p}: env must be a table of strings")
        if s.get("default_enabled") is False and not s.get("risk"):
            errs.append(f"{p}: default_enabled = false needs a risk = '...' explanation")
    for pid, pr in probes.items():
        p = f"probe.{pid}"
        k = pr.get("kind")
        if k not in PROBE_KINDS:
            errs.append(f"{p}: kind must be one of {PROBE_KINDS}")
        if not isinstance(pr.get("what"), str):
            errs.append(f"{p}: missing what")
        if not isinstance(pr.get("fix"), str):
            errs.append(f"{p}: missing fix (the remediation is the point)")
        if k == "command" and (not _strs(pr.get("command")) or not pr.get("command")):
            errs.append(f"{p}: command probe needs command = [argv...]")
        if k == "path" and (not _strs(pr.get("paths")) or not pr.get("paths")):
            errs.append(f"{p}: path probe needs paths")
        if k == "env" and not isinstance(pr.get("var"), str):
            errs.append(f"{p}: env probe needs var")
        if k == "tcp" and not (isinstance(pr.get("host"), str) and isinstance(pr.get("port"), str)):
            errs.append(f"{p}: tcp probe needs host and port (strings; ${{VAR:-default}} allowed)")
        if k == "any":
            if not _strs(pr.get("of")) or not pr.get("of"):
                errs.append(f"{p}: any probe needs of = [probe ids]")
            for sub in pr.get("of") or []:
                if sub not in probes or sub == pid:
                    errs.append(f"{p}: bad sub-probe {sub!r}")
        if pr.get("off_path", "warn") not in OFF_PATH:
            errs.append(f"{p}: off_path must be one of {OFF_PATH}")
        for rx in ("version_regex", "match", "reject"):
            if rx in pr:
                try:
                    re.compile(pr[rx])
                except re.error as e:
                    errs.append(f"{p}: {rx} does not compile: {e}")
    return errs


def capability_servers(cat: dict, cid: str) -> list[str]:
    return list(cat["capability"][cid]["servers"])


def capabilities_of(cat: dict, sid: str) -> list[str]:
    return [cid for cid, c in cat["capability"].items() if sid in c["servers"]]


def find(cat: dict, query: str) -> list[tuple[str, int]]:
    """Capabilities matching a free-text query, best first: [(id, score)]."""
    words = [w for w in re.findall(r"[a-z0-9+]+", query.lower()) if len(w) > 1]
    out = []
    for cid, c in cat["capability"].items():
        hay = set(c.get("keywords", [])) | set(cid.split("-"))
        title = c.get("title", "").lower()
        score = 0
        for w in words:
            if w == cid:
                score += 10
            if w in hay:
                score += 3
            elif any(h.startswith(w) or w.startswith(h) for h in hay if len(h) > 2 and len(w) > 2):
                score += 1
            elif w in title:
                score += 1
        if score:
            out.append((cid, score))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out
