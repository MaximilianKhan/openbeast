#!/usr/bin/env python3
"""openbeast-client's rig registry: several named rigs, one active.

Driven by scripts/client.sh (`rigs`, `use`, `rig …`, `refresh-config`) and by
scripts/setup-client.sh (`adopt-env`). docs/SPARK_HYDRA.md § F2 and
docs/BEAST_SLOT.md § Several rigs describe the user side.

  registry   ${XDG_CONFIG_HOME:-~/.config}/openbeast/rigs.json   (0600)
             names, hosts, URLs, the PATH of each rig's key file, the pinned
             default model and the last beast-slot answer. Never a key.
  keys       one 0600 file per rig. An existing file is referenced where it
             is (e.g. ~/.config/openbeast/spark-hydra-gate.key); keys given
             to `rig add --api-key-stdin` go to …/openbeast/rigs/<name>.key.
  active     ~/.openbeast-client.env (sourced by client.sh) and the
             openbeast-tools MCP entry in opencode.json follow the ACTIVE rig:
             sub-agents and web_search use it.
  opencode   one provider per rig, `openbeast-<name>`, so a session can pick
             any rig's model, plus `openbeast-rig`, an alias of the active
             rig (the id every earlier install and doc uses).
  soul       opencode's `instructions` list gets the same system prompt Open
             WebUI uses on the rigs (system-prompt.md + system-prompt-tools.md,
             scripts/configure-webui.sh) plus a short client note, COPIED to
             ~/.openbeast-client/prompts/ so a moved checkout cannot break it.
             Entries pointing elsewhere are the user's and never touched.

Keys never travel on argv and are never printed: they are read from their
files (or stdin) and written only into 0600 files, each replaced atomically.
Python 3.9-compatible (a Mac's fallback python3 may be old); stdlib only.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import getpass
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
CFG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
OB_DIR = os.path.join(CFG_HOME, "openbeast")
REGISTRY = os.path.join(OB_DIR, "rigs.json")
KEY_DIR = os.path.join(OB_DIR, "rigs")
ENV_FILE = os.path.join(HOME, ".openbeast-client.env")
CLIENT_DIR = os.path.join(HOME, ".openbeast-client")
SOUL_DIR = os.path.join(CLIENT_DIR, "prompts")
OC_CONFIG = os.path.join(CFG_HOME, "opencode", "opencode.json")
REPO = os.environ.get("OB_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROBE_T = float(os.environ.get("OB_PROBE_TIMEOUT") or 4)
ALIAS = "openbeast-rig"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
# Keys this module owns in the env file; anything else there is kept as is.
ENV_OWNED = ("OPENBEAST_RIG", "OPENBEAST_AGENT_INFERENCE_URL", "OPENBEAST_SLOT_URL",
             "SEARXNG_URL", "OPENBEAST_API_KEY", "OPENAI_API_KEY")


def say(msg=""):
    print(msg, flush=True)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tilde(p):
    return ("~" + p[len(HOME):]) if p and HOME and p.startswith(HOME + os.sep) else p


# ---- files ------------------------------------------------------------------

def _write_private(path, text, mode=0o600):
    """Atomic replace, private from the first byte; a symlinked target stays one."""
    real = os.path.realpath(path)
    os.makedirs(os.path.dirname(real), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(real) + ".", dir=os.path.dirname(real))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _mkdir_private(d):
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass


def read_env(path=ENV_FILE):
    """KEY='value' lines as written by setup-client.sh (POSIX single quotes)."""
    out, order = {}, []
    if not os.path.exists(path):
        return out, order
    for line in open(path):
        s = line.rstrip("\n")
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", s)
        if not m:
            order.append((None, s))
            continue
        k, v = m.group(1), m.group(2)
        try:
            parts = shlex.split(v) if v else [""]
            val = parts[0] if parts else ""
        except ValueError:
            val = v
        out[k] = val
        order.append((k, s))
    return out, order


def _q(v):
    return "'" + v.replace("'", "'\\''") + "'"


def load_registry():
    if not os.path.exists(REGISTRY):
        return None
    with open(REGISTRY) as f:
        reg = json.load(f)
    reg.setdefault("version", 1)
    reg.setdefault("rigs", {})
    return reg


def save_registry(reg):
    _mkdir_private(OB_DIR)
    _write_private(REGISTRY, json.dumps(reg, indent=2, sort_keys=True) + "\n")


def read_key(rig):
    if rig.get("_key"):                     # legacy sync only: in memory, never saved
        return rig["_key"]
    p = rig.get("key_file") or ""
    if not p:
        return ""
    try:
        with open(os.path.expanduser(p)) as f:
            return f.read().strip()
    except OSError:
        return ""


def _key_files():
    out = []
    for d in (OB_DIR, KEY_DIR):
        try:
            for n in sorted(os.listdir(d)):
                if n.endswith(".key"):
                    out.append(os.path.join(d, n))
        except OSError:
            pass
    return out


def _find_key_file(key):
    for p in _key_files():
        try:
            if open(p).read().strip() == key:
                return p
        except OSError:
            pass
    return ""


def store_key(name, key):
    """Write a key to …/openbeast/rigs/<name>.key (0600). Returns the path."""
    _mkdir_private(OB_DIR)
    _mkdir_private(KEY_DIR)
    path = os.path.join(KEY_DIR, name + ".key")
    _write_private(path, key + "\n")
    return path


# ---- naming / urls -----------------------------------------------------------

def _sanitize(s):
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")[:32]
    return s or "rig"


def host_of(url):
    m = re.match(r"^https?://\[?([^\]/:]+)", url or "")
    return m.group(1) if m else ""


def name_for(host, key_file=""):
    """spark-hydra-gate.key -> 'spark-hydra'; else the host's first label."""
    if key_file:
        stem = os.path.basename(key_file)[:-4]
        stem = re.sub(r"-(gate|device|key)$", "", stem)
        if stem:
            return _sanitize(stem)
    if re.match(r"^\d+(\.\d+){3}$", host or ""):
        return _sanitize(host)
    return _sanitize((host or "rig").split(".")[0])


def urls_for(host, search):
    u = {"inference_url": "https://%s:8443/v1" % host, "slot_url": "https://%s:8444/api/slot" % host}
    if search == "rig":
        u["search_url"] = "https://%s:8889" % host
    elif search == "local":
        u["search_url"] = "http://127.0.0.1:8888"
    else:
        u["search_url"] = ""
    return u


def _unique_name(reg, base):
    n, i = base, 2
    while n in reg["rigs"] or n == "rig":
        n = "%s-%d" % (base, i)
        i += 1
    return n


def rig_from_env(env):
    url = env.get("OPENBEAST_AGENT_INFERENCE_URL", "")
    host = host_of(url)
    sx = env.get("SEARXNG_URL", "")
    if not sx:
        search = "none"
    elif re.match(r"^https?://(127\.0\.0\.1|localhost)[:/]", sx):
        search = "local"
    else:
        search = "rig"
    return {"host": host, "inference_url": url, "slot_url": env.get("OPENBEAST_SLOT_URL", ""),
            "search": search, "search_url": sx, "key_file": "", "default_model": "",
            "model_pinned": False, "added": _now()}


# ---- tailnet ------------------------------------------------------------------

def tailnet_beast(ts_bin):
    """FQDN of a peer named 'beast' on OUR tailnet owned by THIS user, else ''.
    Same rule as setup-client.sh's auto-detect: another user's or a tagged
    node is never registered unasked."""
    if not ts_bin:
        return ""
    try:
        r = subprocess.run([ts_bin, "status", "--json"], capture_output=True, text=True, timeout=8)
        d = json.loads(r.stdout)
    except Exception:
        return ""
    self_ = d.get("Self") or {}
    me = (self_.get("DNSName") or "").rstrip(".")
    suffix = me.partition(".")[2] or (d.get("MagicDNSSuffix") or "").strip(".")
    for p in (d.get("Peer") or {}).values():
        dns = (p.get("DNSName") or "").rstrip(".")
        name, _, rest = dns.partition(".")
        if (name == "beast" and suffix and rest == suffix
                and self_.get("UserID") is not None and p.get("UserID") == self_.get("UserID")):
            return dns
    return ""


# ---- probing --------------------------------------------------------------------

def _get(url, key="", timeout=None):
    """(status, parsed-json-or-None). Status 0 = nothing answered."""
    if not url:
        return 0, None
    req = urllib.request.Request(url)
    if key and key != "not-needed":
        req.add_header("Authorization", "Bearer " + key)
    try:
        r = urllib.request.urlopen(req, timeout=timeout or PROBE_T)
        code, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, b""
    except Exception:
        return 0, None
    try:
        return code, json.loads(raw)
    except Exception:
        return code, None


def _ctx(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def probe(rig, key):
    """What the rig says right now: beast-slot (public) + /v1/models (keyed)."""
    with ThreadPoolExecutor(max_workers=2) as ex:
        fs = ex.submit(_get, rig.get("slot_url"))
        fm = ex.submit(_get, (rig.get("inference_url") or "").rstrip("/") + "/models", key)
        slot_code, slot = fs.result()
        models_code, models = fm.result()
    if not (isinstance(slot, dict) and "beast_slot" in slot):
        slot = None
    ids, first = [], {}
    if models_code == 200 and isinstance(models, dict):
        data = [m for m in (models.get("data") or []) if isinstance(m, dict) and m.get("id")]
        ids = [m["id"] for m in data]
        first = data[0] if data else {}
    backend = (slot.get("backend") or "llama") if slot else None
    sm = (slot or {}).get("model") or {}
    if not ids and isinstance(sm.get("id"), str) and sm["id"]:
        ids = [sm["id"]]
    if backend in (None, "llama") and ids:
        ids = ids[:1]          # llama-server serves one model, whatever the id
    n_ctx = _ctx(first.get("max_model_len")) or _ctx(sm.get("ctx"))
    if not n_ctx and models_code == 200:
        base = (rig.get("inference_url") or "").rstrip("/")
        root = base[:-3] if base.endswith("/v1") else base
        for path, pick in (("/props", lambda d: d["default_generation_settings"]["n_ctx"]),
                           ("/health", lambda d: d["context_length"])):
            try:
                n_ctx = _ctx(pick(_get(root + path, key)[1]))
            except Exception:
                n_ctx = None
            if n_ctx:
                break
    s = (slot or {}).get("slots") or {}
    return {
        "reachable": bool(slot_code or models_code),
        "slot_code": slot_code, "models_code": models_code,
        "slot": slot, "ids": ids, "ctx": n_ctx, "backend": backend,
        "healthy": (slot.get("healthy") if slot else (models_code == 200)),
        "busy": s.get("busy"), "total": s.get("total"),
        "auth": (slot or {}).get("auth"),
    }


def remember(rig, p):
    """Cache what a successful probe learned; move the default with the rig
    unless the user pinned one."""
    if not p["ids"]:
        return
    rig["last"] = {"at": _now(), "model": p["ids"][0], "ids": p["ids"], "ctx": p["ctx"],
                   "backend": p["backend"], "slots": p["total"], "auth": p["auth"]}
    if not rig.get("model_pinned"):
        rig["default_model"] = p["ids"][0]


def state_of(p):
    if not p["reachable"]:
        return "offline"
    if p["models_code"] in (401, 403):
        return "key-rejected"
    if p["models_code"] in (502, 504):
        return "down"
    if p["models_code"] == 503:
        return "loading"
    if p["slot"] is not None and not p["slot"].get("healthy"):
        return "unhealthy"
    if p["models_code"] == 200:
        return "healthy"
    return "http-%s" % (p["models_code"] or p["slot_code"])


# ---- opencode ----------------------------------------------------------------------

def static_catalog():
    try:
        with open(os.path.join(REPO, "opencode.json")) as f:
            m = json.load(f)["provider"]["llama-cpp"]["models"]
        return dict(m) if m else {}
    except Exception:
        return {}


def default_model(rig, static):
    if rig.get("default_model"):
        return rig["default_model"]
    last = rig.get("last") or {}
    if last.get("model"):
        return last["model"]
    return next(iter(static), "default")


def catalog(name, rig, static, live_now):
    """Model rows for one rig. A llama rig serves any id (llama-server ignores
    it), so it carries the checkout's catalog plus a live row. Any other
    backend (TensorFold, vLLM, hydra) gets exactly the ids it serves: a
    llama alias there is a 404 or silently the same model."""
    last = rig.get("last") or {}
    backend = last.get("backend")
    rows = copy.deepcopy(static) if backend in (None, "llama") else {}
    ids = last.get("ids") or ([last["model"]] if last.get("model") else [])
    for i in ids:
        row = dict(rows.get(i) or {})
        row["name"] = "%s  [%s %s]" % (i, "live on" if live_now else "last seen on", name)
        if last.get("ctx") and i == last.get("model"):
            row["limit"] = limit_for(rig, last["ctx"])
        rows[i] = row
    dm = default_model(rig, static)
    if dm not in rows:
        rows[dm] = {"name": "%s  [%s]" % (dm, name)}
    return rows


def limit_for(rig, ctx):
    """opencode's `limit: {context, output}` (both required by its schema).
    context is the per-conversation window the rig reports (beast-slot
    model.ctx — NOT capacity.ctx_total, which is one KV pool shared by every
    slot). output defaults to 32768: thinking is on by default on the rigs and
    opencode has no reasoning-tail recovery, so it gets more headroom than the
    runner's AGENT_MAX_TOKENS. Per rig: `rig add <name> --output-limit N`."""
    out = rig.get("output_limit")
    if not (isinstance(out, int) and out > 0):
        out = 32768
    return {"context": ctx, "output": min(out, ctx)}


def _mcp_entry(cfg):
    mcp = cfg.get("mcp") or {}
    servers = mcp.get("servers")
    if isinstance(servers, dict) and isinstance(servers.get("openbeast-tools"), dict):
        return servers["openbeast-tools"]
    if isinstance(mcp.get("openbeast-tools"), dict):
        return mcp["openbeast-tools"]
    return None


# ---- soul: the WebUI system prompt, for opencode -----------------------------------
#
# Open WebUI on the rigs runs with system-prompt.md (the soul) followed by
# system-prompt-tools.md (the tool conventions) — scripts/configure-webui.sh.
# opencode gets the same two files through its `instructions` setting (an
# array of paths, URLs or globs; absolute and ~/ paths verified on opencode
# 2.2.0 and 2.3.0), followed by CLIENT_NOTE, which corrects the two things the
# tools file says that are WebUI-only. Both source files are owner-maintained
# and only ever COPIED here, never edited.
#
# Copied, not referenced in the checkout: an in-place install's checkout can
# be moved or deleted, and opencode silently skips a missing instruction file.
# The copies live under ~/.openbeast-client/ (removed by --uninstall) and are
# refreshed by every sync (setup, use, rig add/remove, refresh-config, update).

SOUL_SOURCES = ("system-prompt.md", "system-prompt-tools.md")
CLIENT_NOTE_NAME = "opencode-client.md"
CLIENT_NOTE = """\
## OpenBeast client mode (opencode)

You are running in opencode on the user's own machine. The model runs on an
OpenBeast rig; every tool runs HERE, on this machine.

- The arsenal described above is served by the `openbeast-tools` MCP server,
  so its tools carry that prefix in this session: `openbeast-tools_read_file`,
  `openbeast-tools_edit_file`, `openbeast-tools_write_file`,
  `openbeast-tools_bash`, `openbeast-tools_grep`, `openbeast-tools_web_search`,
  `openbeast-tools_fetch`, `openbeast-tools_start_agent`,
  `openbeast-tools_skill`, `openbeast-tools_start_skill_agent`, and the rest.
  opencode's own built-in tools are also available.
- "Where files go" above describes the Open WebUI chat workspace. It does not
  exist here: there is no `.manifest.jsonl`, and a relative path is resolved
  against the tool server's working directory, which need not be the project
  you are working in. Pass absolute paths; the working directory is in the
  environment block of this prompt.
- The OpenBeast skills come from `openbeast-tools_skill`. opencode's own
  `skill` tool, if present, is a different catalog.
"""

# opencode releases that accept `instructions` in the schema but never load
# it (2.0.25: its instruction plugin reads AGENTS.md only; measured
# 2026-11-17 against a stub provider). 2.2.0 and 2.3.0 load it; 1.x does.
_INSTRUCTIONS_IGNORED = ((2, 0), (2, 1))


def _is_ours(entry):
    if not isinstance(entry, str):
        return False
    p = os.path.realpath(os.path.expanduser(entry))
    return p.startswith(os.path.realpath(SOUL_DIR) + os.sep)


def soul_files(repo=None):
    """Copy the soul (and the tool guide + client note) into SOUL_DIR; return
    the paths opencode should load, in order. A source missing from this
    checkout keeps the copy an earlier sync made; with neither, it is left out.
    Files are rewritten only when their content changed (opencode watches
    instruction files and announces every change to a running session)."""
    repo = repo or REPO
    out = []
    items = [(n, os.path.join(repo, n)) for n in SOUL_SOURCES] + [(CLIENT_NOTE_NAME, None)]
    for name, src in items:
        dest = os.path.join(SOUL_DIR, name)
        if src is None:
            text = CLIENT_NOTE
        else:
            try:
                with open(src) as f:
                    text = f.read()
            except OSError:
                text = None
        if text is not None:
            try:
                with open(dest) as f:
                    same = f.read() == text
            except OSError:
                same = False
            if not same:
                _mkdir_private(CLIENT_DIR)
                _mkdir_private(SOUL_DIR)
                _write_private(dest, text, 0o644)
        if os.path.isfile(dest):
            out.append(dest)
    # The soul is the point; a tool guide or note on its own is not.
    if not out or os.path.basename(out[0]) != SOUL_SOURCES[0]:
        return []
    return out


def apply_soul(cfg, files):
    """Put `files` at the front of cfg["instructions"]; every entry that is
    not ours stays, in its order. Returns 'unchanged', 'updated' or a reason
    the file was left alone."""
    cur = cfg.get("instructions")
    if cur is not None and not isinstance(cur, list):
        return "skipped: 'instructions' in opencode.json is not a list"
    cur = list(cur or [])
    want = list(files) + [e for e in cur if not _is_ours(e)]
    if want == cur:
        return "unchanged"
    if want:
        cfg["instructions"] = want
    else:
        cfg.pop("instructions", None)
    return "updated"


def remove_soul(cfg):
    """Drop our entries only. Returns how many were removed."""
    cur = cfg.get("instructions")
    if not isinstance(cur, list):
        return 0
    keep = [e for e in cur if not _is_ours(e)]
    n = len(cur) - len(keep)
    if n:
        if keep:
            cfg["instructions"] = keep
        else:
            del cfg["instructions"]
    return n


def opencode_version(binary="opencode"):
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
    return tuple(int(x) for x in m.groups()) if m else None


def soul_warning(version):
    if version and version[:2] in _INSTRUCTIONS_IGNORED:
        return ("opencode %s ignores the `instructions` setting, so sessions do NOT load the soul yet"
                " (loaded from 2.2.0). Upgrade: brew upgrade opencode (or opencode upgrade),"
                " then opencode service restart" % ".".join(map(str, version)))
    return ""


def _save_oc(path, cfg):
    real = os.path.realpath(path)
    _write_private(real, json.dumps(cfg, indent=2) + "\n", os.stat(real).st_mode & 0o777)


def sync_opencode(reg, live, switched=False, path=None, legacy=False):
    """Rewrite our providers, the alias, the MCP entry's env and the default
    model. Everything else in the file is the user's and left alone."""
    path = path or OC_CONFIG
    if not os.path.exists(path):
        say("  ! no opencode config at %s — run scripts/setup-client.sh" % path)
        return None
    with open(path) as f:
        cfg = json.load(f)
    static = static_catalog()
    provs = cfg.setdefault("provider", {})
    active = reg.get("active")
    want_ids, notes, any_key = {ALIAS}, [], False
    for name in sorted(reg["rigs"]):
        rig = reg["rigs"][name]
        key = read_key(rig)
        rows = catalog(name, rig, static, name in live)
        opts = {"baseURL": rig.get("inference_url", ""), "apiKey": key or "not-needed"}
        any_key = any_key or bool(key)
        if name == active:
            p = dict(provs.get(ALIAS) or {})
            p.update({"npm": "@ai-sdk/openai-compatible",
                      "name": "OpenBeast rig (active: %s, %s)" % (name, rig.get("host", "")),
                      "options": dict(p.get("options") or {}, **opts), "models": rows})
            provs[ALIAS] = p
        if legacy:
            continue
        if not key and (rig.get("last") or {}).get("auth") not in ("open", "none"):
            notes.append(name)
            continue
        pid = "openbeast-" + name
        p = dict(provs.get(pid) or {})
        p.update({"npm": "@ai-sdk/openai-compatible",
                  "name": "OpenBeast %s (%s)" % (name, rig.get("host", "")),
                  "options": dict(p.get("options") or {}, **opts), "models": rows})
        provs[pid] = p
        want_ids.add(pid)
    for pid in ([] if legacy else list(provs)):
        if (pid.startswith("openbeast-") and pid not in want_ids
                and str((provs[pid] or {}).get("name", "")).startswith("OpenBeast ")):
            del provs[pid]
    # The tool arsenal follows the active rig (sub-agents, web_search).
    arig = reg["rigs"].get(active) or {}
    akey = read_key(arig)
    entry = None if legacy else _mcp_entry(cfg)
    if entry is not None and arig:
        env = entry.setdefault("environment", {})
        env["OPENBEAST_AGENT_INFERENCE_URL"] = arig.get("inference_url", "")
        if arig.get("search_url"):
            env["SEARXNG_URL"] = arig["search_url"]
        else:
            env.pop("SEARXNG_URL", None)
        if akey:
            env["OPENBEAST_API_KEY"] = akey
        else:
            env.pop("OPENBEAST_API_KEY", None)
    elif arig and not legacy:
        say("  ! opencode.json has no openbeast-tools MCP entry — run scripts/setup-client.sh")
    # Default model: ours follows the active rig; a user's own choice stays.
    amodels = (provs.get(ALIAS) or {}).get("models") or {}
    want = ALIAS + "/" + default_model(arig, static) if arig else None
    for k in ("model", "small_model"):
        cur = cfg.get(k)
        if not want or (cur is not None and not isinstance(cur, str)):
            continue
        cur = cur or ""
        if not cur:
            cfg[k] = want
        elif cur.startswith(ALIAS + "/"):
            # the alias follows the live rig; a row that no longer exists is fixed
            if switched or active in live or cur.split("/", 1)[1] not in amodels:
                cfg[k] = want
        elif cur.startswith("openbeast-") and switched:
            cfg[k] = want                   # `use` means: default to that rig
    # The soul rides along with every sync (setup, use, rig add/remove,
    # refresh-config, update), so a pulled checkout's prompt reaches opencode.
    soul, souls = None, []
    if not legacy:
        souls = soul_files()
        soul = apply_soul(cfg, souls)
    real = os.path.realpath(path)
    mode = 0o600 if (any_key or akey) else (os.stat(real).st_mode & 0o777)
    try:
        prev = open(real).read()
        _write_private(real + ".openbeast-prev", prev)       # one rolling backup
    except OSError:
        pass
    _write_private(real, json.dumps(cfg, indent=2) + "\n", mode)
    return {"providers": sorted(want_ids), "nokey": notes, "default": cfg.get("model"),
            "rows": len(amodels), "soul": soul, "souls": souls}


# ---- env ----------------------------------------------------------------------------

def write_env(reg):
    rig = reg["rigs"][reg["active"]]
    old, order = read_env()
    key = read_key(rig)
    lines = ["# OpenBeast client mode — written by setup-client.sh / `openbeast-client use`.",
             "# Sourced by scripts/client.sh; OpenCode gets its copy via the",
             "# environment block in opencode.json. The ACTIVE rig is below; every",
             "# registered rig is in %s." % _tilde(REGISTRY),
             "OPENBEAST_RIG=" + _q(reg["active"]),
             "OPENBEAST_AGENT_INFERENCE_URL=" + _q(rig.get("inference_url", "")),
             "OPENBEAST_SLOT_URL=" + _q(rig.get("slot_url", ""))]
    if rig.get("search_url"):
        lines.append("SEARXNG_URL=" + _q(rig["search_url"]))
    if key:
        lines += ["OPENBEAST_API_KEY=" + _q(key), "OPENAI_API_KEY=" + _q(key)]
    else:
        lines.append("# No device key for this rig: openbeast-client rig add %s --api-key-stdin" % reg["active"])
    for k, raw in order:
        if k and k not in ENV_OWNED:
            lines.append(raw)               # e.g. OPENBEAST_SEARXNG_SECRET, user additions
    _write_private(ENV_FILE, "\n".join(lines) + "\n")


# ---- registry lifecycle ------------------------------------------------------------

def migrate(ts_bin="", quiet=False):
    """First run: turn today's single-rig env file into a registry of one,
    and register this user's own tailnet peer 'beast' (keyless) if seen."""
    reg = load_registry()
    if reg is not None:
        return reg
    env, _ = read_env()
    if not env.get("OPENBEAST_AGENT_INFERENCE_URL"):
        return None
    reg = {"version": 1, "rigs": {}, "active": None}
    rig = rig_from_env(env)
    key = env.get("OPENBEAST_API_KEY", "")
    kf = _find_key_file(key) if key else ""
    name = _unique_name(reg, env.get("OPENBEAST_RIG") or name_for(rig["host"], kf))
    if key and not kf:
        kf = store_key(name, key)
    rig["key_file"] = kf
    reg["rigs"][name] = rig
    reg["active"] = name
    _detect_beast(reg, ts_bin)
    save_registry(reg)
    if not quiet:
        say("  ok migrated the single-rig install into the rig registry: '%s' (%s)%s" % (
            name, rig["host"], ", key " + _tilde(kf) if kf else ""))
        if "beast" in reg["rigs"] and name != "beast":
            say("  ok registered tailnet peer 'beast' (%s) — no key yet: "
                "openbeast-client rig add beast --api-key-stdin" % reg["rigs"]["beast"]["host"])
    return reg


def _detect_beast(reg, ts_bin):
    fqdn = tailnet_beast(ts_bin)
    if not fqdn or "beast" in reg["rigs"]:
        return False
    if any(r.get("host", "").lower() == fqdn.lower() for r in reg["rigs"].values()):
        return False
    rig = {"host": fqdn, "search": "rig", "key_file": "", "default_model": "",
           "model_pinned": False, "added": _now(), "detected": "tailnet peer 'beast'"}
    rig.update(urls_for(fqdn, "rig"))
    reg["rigs"]["beast"] = rig
    return True


def ensure(ts_bin=""):
    reg = migrate(ts_bin)
    if reg is None:
        say("  x no rig registered and no ~/.openbeast-client.env — install first:")
        say("      scripts/setup-client.sh --host <rig-fqdn> --api-key-stdin < <key file>")
        sys.exit(1)
    return reg


def _read_stdin_key():
    if sys.stdin.isatty():
        k = getpass.getpass("Rig API key (input hidden): ")
    else:
        k = sys.stdin.readline()
    k = k.strip()
    if any(ord(c) < 32 for c in k):
        raise SystemExit("  x the key contains a control character — refusing")
    return k


# ---- commands ----------------------------------------------------------------------

def cmd_migrate(a):
    reg = migrate(a.ts_bin, quiet=a.quiet)
    return 0 if reg is not None or not os.path.exists(ENV_FILE) else 1


def cmd_adopt_env(a):
    """setup-client.sh just wrote the env file for one rig: make that rig the
    active one in the registry (adding it, or updating the same host's)."""
    env, _ = read_env()
    if not env.get("OPENBEAST_AGENT_INFERENCE_URL"):
        return 0
    reg = load_registry() or {"version": 1, "rigs": {}, "active": None}
    new = rig_from_env(env)
    key = env.get("OPENBEAST_API_KEY", "")
    name = a.name
    if not name:
        same = [n for n, r in reg["rigs"].items() if r.get("host", "").lower() == new["host"].lower()]
        name = same[0] if same else None
    kf = _find_key_file(key) if key else ""
    if not name:
        name = _unique_name(reg, name_for(new["host"], kf))
    if not NAME_RE.match(name) or name == "rig":
        say("  x invalid rig name '%s' (a-z 0-9 -, not 'rig')" % name)
        return 2
    old = reg["rigs"].get(name) or {}
    if key and not kf:
        kf = store_key(name, key)
    new["key_file"] = kf
    for k in ("default_model", "model_pinned", "last", "added", "detected"):
        if k in old:
            new[k] = old[k]
    reg["rigs"][name] = new
    reg["active"] = name
    _detect_beast(reg, a.ts_bin)
    save_registry(reg)
    write_env(reg)
    say("  ok rig registry: '%s' (%s) is the active rig%s" % (
        name, new["host"], " — %d registered" % len(reg["rigs"]) if len(reg["rigs"]) > 1 else ""))
    return 0


def _probe_all(reg, only=None):
    names = [n for n in sorted(reg["rigs"]) if only is None or n in only]
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as ex:
        futs = {n: ex.submit(probe, reg["rigs"][n], read_key(reg["rigs"][n])) for n in names}
        return {n: f.result() for n, f in futs.items()}


def cmd_list(a):
    reg = ensure(a.ts_bin)
    probes = {} if a.no_probe else _probe_all(reg)
    for n, p in probes.items():
        if p["reachable"]:
            remember(reg["rigs"][n], p)
    save_registry(reg)
    if a.json:
        out = {"active": reg["active"], "rigs": {}}
        for n, r in reg["rigs"].items():
            p = probes.get(n)
            out["rigs"][n] = {"host": r.get("host"), "key": bool(read_key(r)),
                              "key_file": r.get("key_file") or None,
                              "default_model": default_model(r, {}),
                              "state": state_of(p) if p else None,
                              "slots": [p["busy"], p["total"]] if p and p["total"] else None,
                              "backend": (r.get("last") or {}).get("backend")}
        print(json.dumps(out, indent=2))
        return 0
    say("  %-1s %-14s %-30s %-12s %-24s %-6s %s" % ("", "RIG", "HOST", "STATE", "MODEL", "SLOTS", "KEY"))
    for n in sorted(reg["rigs"]):
        r = reg["rigs"][n]
        p = probes.get(n)
        st = state_of(p) if p else "-"
        slots = "%s/%s" % (p["busy"] if p["busy"] is not None else "?", p["total"]) if p and p["total"] else "-"
        key = _tilde(r.get("key_file")) if read_key(r) else (
            "open rig" if (r.get("last") or {}).get("auth") in ("open", "none") else "NONE")
        model = default_model(r, {}) if (r.get("default_model") or r.get("last")) else "?"
        say("  %-1s %-14s %-30s %-12s %-24s %-6s %s" % (
            "*" if n == reg["active"] else "", n, r.get("host", ""), st, model, slots, key))
    missing = [n for n in reg["rigs"] if not read_key(reg["rigs"][n])
               and (reg["rigs"][n].get("last") or {}).get("auth") not in ("open", "none")]
    say("")
    say("  * = active (env file, tools, opencode's openbeast-rig/…). Switch: openbeast-client use <rig>")
    for n in missing:
        say("  ! %s has no device key: openbeast-client rig add %s --api-key-stdin < <key file>" % (n, n))
    return 0


def cmd_active(a):
    reg = migrate(a.ts_bin, quiet=True)
    if not reg or not reg.get("active"):
        return 1
    r = reg["rigs"].get(reg["active"]) or {}
    print("%s|%s|%d" % (reg["active"], r.get("host", ""), len(reg["rigs"])))
    env, _ = read_env()
    url = env.get("OPENBEAST_AGENT_INFERENCE_URL", "")
    if url and r.get("inference_url") and url.rstrip("/") != r["inference_url"].rstrip("/"):
        print("! the env file points at %s, the registry's active rig at %s — "
              "run: openbeast-client use %s" % (url, r["inference_url"], reg["active"]))
    return 0


def _report_soul(state, files):
    if state is None:
        return
    if state.startswith("skipped"):
        say("  ! soul not wired — %s" % state[len("skipped: "):])
    elif files:
        say("  ok opencode instructions: %s (%s)" % (
            ", ".join(os.path.basename(f) for f in files), _tilde(SOUL_DIR)))
    else:
        say("  ! soul not wired — no system-prompt.md in %s" % REPO)


def _report_sync(res):
    if not res:
        return
    say("  ok opencode: providers %s; default %s" % (", ".join(res["providers"]), res["default"]))
    _report_soul(res.get("soul"), res.get("souls") or [])
    for n in res["nokey"]:
        say("  - no provider for '%s' (no device key yet): openbeast-client rig add %s --api-key-stdin" % (n, n))


def cmd_use(a):
    reg = ensure(a.ts_bin)
    name = a.rig
    if name not in reg["rigs"]:
        say("  x no rig named '%s'. Registered: %s" % (name, ", ".join(sorted(reg["rigs"])) or "none"))
        say("    add one: openbeast-client rig add %s --host <fqdn> --api-key-stdin" % name)
        return 2
    rig = reg["rigs"][name]
    prev = reg.get("active")
    key = read_key(rig)
    # Every rig at once (parallel): the target decides, the others refresh
    # their own providers' rows while we are at it.
    probes = _probe_all(reg)
    p = probes[name]
    st = state_of(p)
    if st != "healthy" and not a.force:
        if st == "offline":
            say("  x '%s' is not reachable: nothing answers at %s or %s" % (
                name, rig.get("inference_url"), rig.get("slot_url")))
            say("    (rig powered off, off the tailnet, or the stack not published)")
        elif st == "key-rejected":
            say("  x '%s' %s the device key (HTTP %s from /v1/models)" % (
                name, "requires" if not key else "rejected", p["models_code"]))
            say("    %s: openbeast-client rig add %s --api-key-stdin < <key file>" % (
                "add one" if not key else "rotated or revoked? enroll again on the rig, then", name))
        else:
            say("  x '%s' answers but is not serving (%s)" % (name, st))
        say("  active rig unchanged: %s. To switch anyway: openbeast-client use %s --force" % (prev, name))
        return 1
    live = set()
    for n, q in probes.items():
        if q["reachable"] and q["ids"]:
            remember(reg["rigs"][n], q)
            live.add(n)
    reg["active"] = name
    res = sync_opencode(reg, live, switched=True)
    write_env(reg)
    save_registry(reg)
    say("  ok active rig: %s (%s)%s" % (name, rig.get("host"), "" if prev == name else " — was %s" % prev))
    if p["reachable"] and p["ids"]:
        say("  ok serving %s%s, slots %s/%s" % (
            p["ids"][0], " at %d ctx" % p["ctx"] if p["ctx"] else "",
            p["busy"] if p["busy"] is not None else "?", p["total"] or "?"))
    elif st != "healthy":
        say("  ! switched with --force while '%s' is %s: requests fail until it is up." % (name, st))
        if prev and prev != name:
            say("    back: openbeast-client use %s" % prev)
    if not key:
        say("  ! no device key registered for '%s': openbeast-client rig add %s --api-key-stdin" % (name, name))
    _report_sync(res)
    say("  ok env file %s (0600). A running opencode picks the change up from its config"
        " dir; if a session does not: opencode reload" % _tilde(ENV_FILE))
    return 0


def cmd_sync(a):
    reg = ensure(a.ts_bin)
    probes = _probe_all(reg)
    live = set()
    for n, p in probes.items():
        if p["reachable"] and p["ids"]:
            remember(reg["rigs"][n], p)
            live.add(n)
    res = sync_opencode(reg, live)
    save_registry(reg)
    if res is None:
        return 0
    _report_live(reg, live, res)
    _report_sync(res)
    return 0


def _report_live(reg, live, res):
    say("  ok opencode catalog refreshed: %d models" % res["rows"])
    one = len(reg["rigs"]) == 1
    for n in sorted(reg["rigs"]):
        r = reg["rigs"][n]
        label = "rig" if one else "rig %s%s" % (n, " (active)" if n == reg["active"] else "")
        if n in live:
            last = r.get("last") or {}
            say("  ok %s is serving '%s'%s — pinned as the default" % (
                label, last.get("model"), " at %d ctx" % last["ctx"] if last.get("ctx") else ""))
        else:
            say("  ! %s unreachable — catalog kept from %s" % (
                label, "its last answer" if r.get("last") else "the checkout"))


def cmd_sync_legacy(a):
    """refresh-config for a config with no registry behind it: only the
    openbeast-rig provider's rows and the default, from that provider's own
    baseURL/key. Reads and writes nothing but the opencode file."""
    path = a.oc or OC_CONFIG
    if not os.path.exists(path):
        say("  ! no opencode config at %s — run scripts/setup-client.sh" % path)
        return 0
    if not static_catalog():
        say("  ! could not read the checkout's opencode.json — catalog unchanged")
        return 0
    prov = (json.load(open(path)).get("provider") or {}).get(ALIAS)
    if prov is None:
        say("  ! opencode.json has no 'openbeast-rig' provider — run scripts/setup-client.sh")
        return 0
    opts = prov.get("options") or {}
    base = opts.get("baseURL", "")
    rig = {"host": host_of(base), "inference_url": base,
           "slot_url": os.environ.get("OPENBEAST_SLOT_URL", ""), "_key": opts.get("apiKey") or ""}
    reg = {"rigs": {"rig": rig}, "active": "rig"}
    p = probe(rig, read_key(rig)) if base else None
    live = set()
    if p and p["reachable"] and p["ids"]:
        remember(rig, p)
        live.add("rig")
    res = sync_opencode(reg, live, path=path, legacy=True)
    if res:
        _report_live(reg, live, res)
    return 0


def cmd_soul(a):
    """setup-client.sh step 5 (and `status --check`): wire, check or remove
    the soul in opencode.json without touching anything else in it."""
    path = a.oc or OC_CONFIG
    if not os.path.exists(path):
        say("  ! no opencode config at %s — run scripts/setup-client.sh" % path)
        return 1
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        say("  ! could not parse %s (%s) — left untouched" % (path, e))
        return 1
    if a.check:
        cur = cfg.get("instructions")
        ours = [e for e in cur if _is_ours(e)] if isinstance(cur, list) else []
        if not ours or not all(os.path.isfile(os.path.expanduser(e)) for e in ours):
            say("! opencode does not load the OpenBeast soul — run: openbeast-client refresh-config")
            return 1
        warn = soul_warning(opencode_version())
        if warn:
            say("! " + warn)
            return 3
        say("opencode loads the OpenBeast soul (%s)" % ", ".join(os.path.basename(e) for e in ours))
        return 0
    if a.remove:
        n = remove_soul(cfg)
        if n:
            _save_oc(path, cfg)
        say("  ok opencode instructions: removed %d of ours" % n)
        return 0
    files = soul_files()
    state = apply_soul(cfg, files)
    if state == "updated":
        _save_oc(path, cfg)
    _report_soul(state, files)
    warn = soul_warning(opencode_version()) if files else ""
    if warn:
        say("  ! " + warn)
    return 0


def cmd_add(a):
    reg = load_registry() or migrate(a.ts_bin, quiet=True) or {"version": 1, "rigs": {}, "active": None}
    name = a.name
    if not NAME_RE.match(name) or name == "rig":
        say("  x invalid rig name '%s' (lowercase a-z 0-9 -, max 32, not 'rig')" % name)
        return 2
    rig = reg["rigs"].get(name)
    if rig is None:
        if not a.host:
            say("  x new rig '%s' needs --host <tailnet fqdn>" % name)
            return 2
        rig = {"added": _now(), "key_file": "", "default_model": "", "model_pinned": False}
    if a.host or a.search:
        host = a.host or rig.get("host")
        search = a.search or rig.get("search") or "rig"
        rig.update({"host": host, "search": search})
        rig.update(urls_for(host, search))
    if a.key_file:
        p = os.path.abspath(os.path.expanduser(a.key_file))
        if not os.path.isfile(p):
            say("  x no key file at %s" % p)
            return 2
        if os.stat(p).st_mode & 0o077:
            os.chmod(p, 0o600)
            say("  ok tightened %s to 0600" % _tilde(p))
        rig["key_file"] = p
    elif a.api_key_stdin:
        k = _read_stdin_key()
        if k:
            rig["key_file"] = store_key(name, k)
        else:
            rig["key_file"] = ""
    if a.output_limit is not None:
        if a.output_limit > 0:
            rig["output_limit"] = a.output_limit
        else:
            rig.pop("output_limit", None)          # 0 = back to the default
    if a.model:
        rig["default_model"], rig["model_pinned"] = a.model, True
    elif a.unpin_model:
        rig["model_pinned"] = False
    reg["rigs"][name] = rig
    if not reg.get("active"):
        reg["active"] = name
    p = None if a.no_probe else probe(rig, read_key(rig))
    if p and p["reachable"]:
        remember(rig, p)
    save_registry(reg)
    say("  ok rig '%s' (%s)%s; key %s" % (
        name, rig.get("host"), " — active" if reg["active"] == name else "",
        _tilde(rig["key_file"]) + " (0600)" if read_key(rig) else "none"))
    if p:
        say("  %s %s: %s" % ("ok" if state_of(p) == "healthy" else "!", name, state_of(p)))
    if os.path.exists(OC_CONFIG):
        live = {name} if p and p["reachable"] else set()
        _report_sync(sync_opencode(reg, live))
    if reg["active"] == name and os.path.exists(ENV_FILE):
        write_env(reg)
    if reg["active"] != name:
        say("  switch to it: openbeast-client use %s" % name)
    return 0


def cmd_remove(a):
    reg = ensure(a.ts_bin)
    if a.name not in reg["rigs"]:
        say("  x no rig named '%s'" % a.name)
        return 2
    if a.name == reg["active"]:
        say("  x '%s' is the active rig — switch first: openbeast-client use <other>" % a.name)
        return 1
    r = reg["rigs"].pop(a.name)
    save_registry(reg)
    if os.path.exists(OC_CONFIG):
        _report_sync(sync_opencode(reg, set()))
    say("  ok removed rig '%s'%s" % (a.name, (" — its key file stays at %s (delete it yourself)"
                                             % _tilde(r["key_file"])) if r.get("key_file") else ""))
    return 0


def cmd_detect(a):
    reg = ensure(a.ts_bin)
    if _detect_beast(reg, a.ts_bin):
        save_registry(reg)
        say("  ok registered tailnet peer 'beast' (%s) — no key yet: "
            "openbeast-client rig add beast --api-key-stdin" % reg["rigs"]["beast"]["host"])
    else:
        say("  - nothing new: no unregistered peer named 'beast' owned by you on this tailnet")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="openbeast-client", description=__doc__.split("\n")[0])
    ap.add_argument("--ts-bin", default=os.environ.get("OB_TS_BIN", ""))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("migrate")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_migrate)
    s = sub.add_parser("adopt-env")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_adopt_env)
    s = sub.add_parser("list")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-probe", action="store_true")
    s.set_defaults(fn=cmd_list)
    sub.add_parser("active").set_defaults(fn=cmd_active)
    s = sub.add_parser("use")
    s.add_argument("rig")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_use)
    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    s = sub.add_parser("sync-legacy")
    s.add_argument("--oc")
    s.set_defaults(fn=cmd_sync_legacy)
    s = sub.add_parser("soul")
    s.add_argument("--oc")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--remove", action="store_true")
    g.add_argument("--check", action="store_true")
    s.set_defaults(fn=cmd_soul)
    s = sub.add_parser("add")
    s.add_argument("name")
    s.add_argument("--host")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--api-key-stdin", action="store_true")
    g.add_argument("--key-file")
    s.add_argument("--search", choices=("rig", "local", "none"))
    g2 = s.add_mutually_exclusive_group()
    g2.add_argument("--model")
    g2.add_argument("--unpin-model", action="store_true")
    s.add_argument("--output-limit", type=int, help="opencode limit.output for this rig's models")
    s.add_argument("--no-probe", action="store_true")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("remove")
    s.add_argument("name")
    s.set_defaults(fn=cmd_remove)
    sub.add_parser("detect").set_defaults(fn=cmd_detect)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
