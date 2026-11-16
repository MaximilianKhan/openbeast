"""beast-apps prerequisite probes (docs/BEAST_APPS.md §Probes).

A probe answers one question about THIS machine — "is a full Xcode
selected?", "is adb anywhere?", "is Blender's addon socket up?" — and, when
the answer is no, carries the exact remediation the user (or the model)
should be shown. Probes never install, never write, never need the network.

Result statuses: ok | warn (passes, with advice: e.g. adb found only in the
SDK dir, not on PATH) | fail. A failing probe with required = false is
reported as warn: the server can start, one workflow will not work.
"""
from __future__ import annotations

import copy
import glob
import os
import re
import shutil
import socket
import subprocess

DEFAULT_TIMEOUT = 15.0
TCP_TIMEOUT = 1.5
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand(s: str, env: dict) -> tuple[str, bool]:
    """${VAR} / ${VAR:-default} and a leading ~. Returns (value, complete):
    complete is False when a ${VAR} without default was unset — a search dir
    like ${ANDROID_HOME}/platform-tools must then be skipped, not read as
    /platform-tools."""
    complete = True

    def sub(m):
        nonlocal complete
        v = env.get(m.group(1))
        if v:
            return v
        if m.group(2) is not None:
            return m.group(2)
        complete = False
        return ""

    out = _VAR.sub(sub, s)
    if out.startswith("~"):
        home = env.get("HOME") or os.path.expanduser("~")
        out = home + out[1:]
    return out, complete


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4]) or (0,)


def version_ge(have: str, want: str) -> bool:
    a, b = _vtuple(have), _vtuple(want)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) >= b + (0,) * (n - len(b))


def _first_line(text: str, limit: int = 200) -> str:
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if ln:
            return ln[:limit]
    return ""


def resolve(names: list[str], search: list[str], env: dict) -> tuple[str | None, bool]:
    """(absolute path, on_path) for the first of `names` found on PATH, else in
    the `search` dirs (expanded). (None, False) when nowhere."""
    path = env.get("PATH", "")
    for n in names:
        hit = shutil.which(n, path=path)
        if hit:
            return os.path.abspath(hit), True
    for d in search:
        d, complete = expand(d, env)
        if not complete or not d:
            continue
        for n in names:
            cand = os.path.join(d, n)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return os.path.abspath(cand), False
    return None, False


def _fmt(tpl: str, **kw) -> str:
    out = tpl
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _render_exports(pr: dict, path: str | None) -> dict:
    if not path:
        return {}
    d = os.path.dirname(path)
    return {k: os.path.normpath(_fmt(v, dir=d, path=path)) for k, v in (pr.get("export_env") or {}).items()}


def run(cat: dict, pid: str, env: dict, _depth: int = 0, cache: dict | None = None) -> dict:
    """Run probe `pid`. `cache` (a dict the caller owns for ONE process, with
    one env) memoizes results: `list`/`find` evaluate several servers that
    share probes (npx, node, adb, simctl), and each used to re-spawn the same
    subprocesses. Callers get a copy, never the cached object."""
    if cache is not None and ("probe", pid) in cache:
        return copy.deepcopy(cache[("probe", pid)])
    res = _run(cat, pid, env, _depth, cache)
    if cache is not None:
        cache[("probe", pid)] = copy.deepcopy(res)
    return res


def _run(cat: dict, pid: str, env: dict, _depth: int, cache: dict | None) -> dict:
    pr = cat["probe"][pid]
    kind = pr["kind"]
    required = pr.get("required", True)
    res = {"id": pid, "kind": kind, "what": pr["what"], "required": required,
           "status": "fail", "found": "", "path": None, "on_path": None,
           "detail": "", "fix": "", "export_env": {}, "dirs": []}
    try:
        ok, warn_fix = _dispatch(cat, pr, env, res, _depth, cache)
    except Exception as e:  # a probe must never crash the CLI
        ok, warn_fix = False, None
        res["detail"] = f"probe error: {type(e).__name__}: {e}"
    d = os.path.dirname(res["path"] or "")
    ctx = dict(found=res["found"] or "not found", path=res["path"] or "", dir=d,
               parent=os.path.dirname(d), output=res["detail"] or "no output")
    if ok and warn_fix:
        res["status"], res["fix"] = "warn", _fmt(warn_fix, **ctx)
    elif ok:
        res["status"] = "ok"
    else:
        res["status"] = "fail" if required else "warn"
        res["fix"] = _fmt(pr["fix"], **ctx)
    return res


def _good(sub: dict) -> bool:
    return sub["status"] in ("ok", "warn") and sub["required"] or sub["status"] == "ok"


def _dispatch(cat, pr, env, res, depth, cache=None):
    kind = pr["kind"]
    if kind == "command":
        return _command(pr, env, res, cache)
    if kind == "path":
        for p in pr["paths"]:
            p, complete = expand(p, env)
            if not complete:
                continue
            hits = sorted(glob.glob(p))
            if hits:
                res["path"] = res["found"] = hits[0]
                return True, None
        res["detail"] = "none of " + ", ".join(expand(p, env)[0] for p in pr["paths"]) + " exists"
        return False, None
    if kind == "env":
        v = env.get(pr["var"], "")
        if not v:
            res["detail"] = f"{pr['var']} is not set"
            return False, None
        if pr.get("match") and not re.search(pr["match"], v):
            res["detail"] = f"{pr['var']}={v!r} is not one of the accepted values"
            return False, None
        res["found"] = f"{pr['var']} set"
        return True, None
    if kind == "tcp":
        host, _ = expand(pr["host"], env)
        port_s, _ = expand(pr["port"], env)
        res["found"] = f"{host}:{port_s}"
        try:
            port = int(port_s)
            with socket.create_connection((host, port), timeout=float(pr.get("timeout", TCP_TIMEOUT))):
                pass
            return True, None
        except (OSError, ValueError) as e:
            res["detail"] = f"cannot connect to {host}:{port_s} ({e.__class__.__name__})"
            return False, None
    if kind == "any":
        if depth > 3:
            raise ValueError("any-probes nested too deep")
        # In order, stopping at the first that passes: the alternatives
        # after it cannot change the answer, and one of them (`xcrun simctl
        # list`, ~0.4 s) used to run only to be thrown away.
        subs, g = [], None
        for sid in pr["of"]:
            sub = run(cat, sid, env, depth + 1, cache)
            subs.append(sub)
            if _good(sub):
                g = sub
                break
        res["subprobes"] = subs
        if g:
            res.update(found=f"{g['id']}: {g['found'] or 'ok'}", path=g["path"],
                       export_env=g["export_env"], dirs=g["dirs"], on_path=g["on_path"])
            return True, (g["fix"] if g["status"] == "warn" else None)
        res["detail"] = "; ".join(f"{s['id']}: {s['detail'] or 'missing'}" for s in subs)
        return False, None
    raise ValueError(f"unknown probe kind {kind}")


def candidates(names: list[str], search: list[str], env: dict) -> list[tuple[str, bool]]:
    """Every executable for `names`: the PATH hit first, then each search dir.
    [(absolute path, on_path)], de-duplicated by real path."""
    out, seen = [], set()
    path = env.get("PATH", "")
    for n in names:
        hit = shutil.which(n, path=path)
        if hit and os.path.realpath(hit) not in seen:
            seen.add(os.path.realpath(hit))
            out.append((os.path.abspath(hit), True))
            break
    for d in search:
        d, complete = expand(d, env)
        if not complete or not d:
            continue
        for n in names:
            cand = os.path.join(d, n)
            if os.path.isfile(cand) and os.access(cand, os.X_OK) and os.path.realpath(cand) not in seen:
                seen.add(os.path.realpath(cand))
                out.append((os.path.abspath(cand), False))
    return out


def _exec(argv: list[str], env: dict, timeout: float, cache: dict | None):
    """(output, rc) or (error text, None). Memoized per argv in `cache`, so
    node-18 and node-20 share one `node --version`."""
    key = ("exec", tuple(argv), timeout)
    if cache is not None and key in cache:
        return cache[key]
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, env=env,
                            timeout=timeout, stdin=subprocess.DEVNULL)
        r = ((cp.stdout or "") + (cp.stderr or ""), cp.returncode)
    except subprocess.TimeoutExpired:
        r = (f"`{' '.join(argv)}` timed out", None)
    except OSError as e:
        r = (f"cannot run {argv[0]}: {e}", None)
    if cache is not None:
        cache[key] = r
    return r


def _try(pr, env, path, cache=None):
    """Run one candidate. (ok, found, detail)."""
    argv = pr["command"]
    out, rc = _exec([path] + argv[1:], env, float(pr.get("timeout", DEFAULT_TIMEOUT)), cache)
    if rc is None:
        return False, path, out
    detail = _first_line(out) or (f"exit {rc}" if rc else "")
    if rc != 0:
        return False, f"{path} (exit {rc})", detail
    if pr.get("reject") and re.search(pr["reject"], out):
        return False, path, detail
    if pr.get("match") and not re.search(pr["match"], out):
        return False, path, detail
    if pr.get("version_regex"):
        m = re.search(pr["version_regex"], out)
        if not m:
            return False, "unknown version", detail
        if pr.get("min_version") and not version_ge(m.group(1), pr["min_version"]):
            return False, m.group(1), f"version {m.group(1)} < {pr['min_version']} ({path})"
        return True, m.group(1), detail
    return True, path, detail


def _command(pr, env, res, cache=None):
    argv = pr["command"]
    names = [argv[0]] + [n for n in pr.get("binary_names", []) if n != argv[0]]
    cands = candidates(names, pr.get("search", []), env)
    if not cands:
        searched = [d for d in (expand(s, env)[0] for s in pr.get("search", []) if expand(s, env)[1]) if d]
        res["detail"] = f"{argv[0]} not found on PATH" + (" or in " + ", ".join(searched) if searched else "")
        res["found"] = "not installed"
        return False, None
    first_fail = None
    for path, on_path in cands:
        ok, found, detail = _try(pr, env, path, cache)
        if not ok:
            first_fail = first_fail or (path, on_path, found, detail)
            continue
        res.update(path=path, on_path=on_path, found=found, detail=detail)
        if on_path:
            return True, None
        res["export_env"] = _render_exports(pr, path)
        res["dirs"] = [os.path.dirname(path)]
        mode = pr.get("off_path", "warn")
        if mode == "fail":
            return False, None
        if mode == "warn":
            return True, pr.get("fix_off_path") or (
                "{path} works but its directory is not on PATH; beast-apps adds {dir} to the "
                "MCP server's PATH. For your shell: `export PATH=\"{dir}:$PATH\"`.")
        return True, None
    path, on_path, found, detail = first_fail
    others = len(cands) - 1
    res.update(path=path, on_path=on_path, found=found,
               detail=detail + (f"; {others} other candidate(s) failed too" if others else ""))
    return False, None
