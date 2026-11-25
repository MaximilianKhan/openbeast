#!/usr/bin/env python3
"""Integrity scan of OpenBeast agent transcripts (a model's eval runs): did
the agent's tools read or list what a measured unit must not see?

  python3 evals/audit/integrity_scan.py LABEL=RESULTS.json[,MORE.json][@TREE] ... \\
      [--model GLM] [--json out.json] [--trailing-dot] [--tz ZONE] [--until [LABEL=]ISO]
  python3 evals/audit/integrity_scan.py ALL=@TREE --model GLM   # every transcript

Each LABEL is one arm. RESULTS are its run_eval results files (a main run and
its reruns); TREE is the checkout it ran from (default: this one), whose
evals/tasks/ give the task texts and whose agents/logs/ holds the
transcripts. Every transcript that started inside a results file's window
is scanned, and attributed to its unit by task text (transcripts.py); the
row's verdict is shown for the transcript the row was scored on.

The window's ends: results and transcript timestamps are written without a
zone, so scan on the host that wrote them or pass --tz (its zone). A results
file from before 2026-11-22 records no end (`saved`), so its mtime is used;
a checkout or copy resets that, so pass --until (the run's end, from its
campaign log). Exit 0 clean, 1 contaminated, 2 when any result row has no
matched transcript (a partial scan, never a clean verdict).

Forbidden (G24, the eval sandbox of agents/tools.py since #195): a checkout's
evals/ (tasks, refs, suites, results, cache, vlib, the harness), its tests/
and agents/logs/, ~/.claude, Claude Code's scratch dirs (/tmp/claude-<uid>)
and run ledgers. A tool call is checked on its PATH arguments (bash: the
command; file tools: path/directory; grep: path) and its RESULT (a listing
that printed a forbidden path). Kinds:

  evals       a checkout's evals/ tree (a relative evals/ counts only after a
              cd out of /tmp; a /tmp/eval* fixture never counts)
  tests       a checkout's tests/
  logs        agents/logs (other units' transcripts)
  claude      ~/.claude (Claude Code session and workflow journals)
  scratch     /tmp/claude-<uid> (arm worktrees, results, ledgers)
  ledger      a results or ledger file by name (eval-*.json, ledger.jsonl)
  listed      the tool's OUTPUT named a forbidden path (find / grep -r / ls)
              (a line that starts with a home or scratch path; since review
              1124 also an answer tree, evals/{tasks,refs,suites,results,
              cache}/, ANYWHERE in a line under any root: ls -l, find -ls,
              du -a, stat, /root/..., /work/..., and a ./ listing after an
              absolute cd)
  web         fetch / web_search (refused under eval since v5; recorded)
  selfrepo    any tool call that names this project's own repository
              (MaximilianKhan/openbeast on github.com, raw.githubusercontent.com,
              api.github.com/repos, codeload, a CDN mirror; case-insensitive,
              percent-decoded) or a URL path into a checkout's evals/ answer
              trees: the public repo holds evals/refs (2026-11-18; the eval
              fetch guard of agents/tools.py refuses the same URLs)
  network     a bash command that reaches off the host: curl / wget to a
              non-loopback host, git clone / fetch / ls-remote of a remote,
              any github.com / githubusercontent.com (review 1118: the public
              repo holds evals/refs); since review 1124 also a package
              manager that fetches by name (pip / uv / cargo add|install /
              go get / npm / yarn / gem / apt; not --no-index / --offline /
              a local path / a loopback index), nc / ncat / telnet / ssh /
              scp / rsync / socat / httpie to a non-loopback host, and a
              python / node one-liner that opens a URL or a socket. A failed
              connection, or a command the shell reported `command not
              found` (2026-11-25), is an ATTEMPT
  embed       write_file / edit_file CONTENT that compiles or opens an
              absolute forbidden path (#include "/...", include_str!,
              include_bytes!, @embedFile, @import, open("/...")): the build
              reads it, so it counts as a read (review 1118)
  toolchain   info only: a compiler's own tree that happens to sit in a scratch
              dir (the zig of the 2026-11-16 runs lived in one)
  source      info only: the harness's agents/*.py source (not an answer key)

A hit whose result was a refusal (the seal's "Error: refusing", or the
kernel's "Operation not permitted" alone) is an ATTEMPT. A unit with any
other hit, except `source` and `web`, is CONTAMINATED: report it as such and
do not count its verdict. This tool never edits a results file.

--trailing-dot also counts, per unit, the turns lost to the trailing-dot
path bug (run_eval.task_paths, G24): turns after the first task_done that was
refused for a dotted path ("after_refusal"), and turns whose tool calls
named a dotted path ("touched").

The reference row's scan (Claude Code Workflow transcripts) is
evals/reference/integrity_scan.py.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
EVALS = os.path.dirname(HERE)
REPO = os.path.dirname(EVALS)
sys.path.insert(0, EVALS)
sys.path.insert(0, HERE)

import campaign  # noqa: E402
import transcripts as T  # noqa: E402

# The pattern run_eval used before G24 (it keeps a sentence-final period).
_OLD_PATHS = re.compile(r"/tmp/eval[\w\-./]*")

_P = r"[\w.~/+@-]"          # a path character
_ABS = re.compile(r"(?:~|\$HOME|/)" + _P + r"*")
_CD = re.compile(r"(?:^|[\s;&|(])cd\s+[\"']?([^\s\"';&|)]+)")
_FIXTURE = re.compile(r"^(?:/private)?/tmp/eval[^/]*(?:/|$)")
_CLAUDE_HOME = re.compile(r"(?:~|\$HOME|\$\{HOME\}|/Users/[^/\s]+|/home/[^/\s]+)/\.claude(?:/|\b)")
_SCRATCH = re.compile(r"(?:/private)?/tmp/claude-\d+")
_TOOLCHAIN = re.compile(r"/toolchains/|/(?:zig|go|rust|cargo|rustup)[\w.-]*/(?:lib|src|bin|toolchains)/")
_LEDGER = re.compile(r"\b(?:eval-[\w.-]+-\d{8}-\d{6}\.json|ledger\.jsonl|diag-timing-[\w.-]+\.jsonl)\b")
_REL = re.compile(r"(?<![\w./~$-])(?:\./)?(evals|tests|agents/logs)(?:/|\b)")
_SOURCE = re.compile(r"agents/(?:tools|runner|mcp_server)\.py")
# In an absolute path: a checkout's evals/ anywhere, tests/ and agents/logs
# only under an openbeast-like checkout (any /usr/.../tests is fine).
_ABS_KIND = (("evals", re.compile(r"/evals(?:/|$)")),
             ("logs", re.compile(r"/agents/logs(?:/|$)")),
             ("tests", re.compile(r"openbeast[^/]*/(?:[^/]+/)*?tests(?:/|$)|/worktrees/[^/]+/tests(?:/|$)")))
# The web gate's refusal ("Error: fetch is disabled while ...") starts with
# the tool's name: with `.match` the second branch needs its own prefix, or a
# refused fetch reads as "read" (it did on DeepSeek arm T's two CPython
# fetches, 2026-11-18, which looked like they had reached the network).
_REFUSED = re.compile(r"^Error: refusing|^Error: \w+ is disabled while an eval unit is measured")
# Review 1118: off-host network from a bash command.
_LOOPBACK = re.compile(r"^(?:localhost|127\.\d+\.\d+\.\d+|\[?::1\]?|0\.0\.0\.0)(?::\d+)?$", re.I)
_FETCHER = re.compile(r"(?:^|[\s;&|(`$])(curl|wget)\b([^;&|\n`)]*)")
# A URL argument: a scheme, or a bare host that is loopback or ends in a
# common TLD (so `-o out.txt` is not a host).
_URL_TOKEN = re.compile(r"^(?:(?:https?|ftps?)://(?:[^@/\s]+@)?(?P<host>\[[^\]]+\]|[^/:\s]+)"
                        r"|(?P<host2>localhost|127(?:\.\d+){3}|(?:[\w-]+\.)+(?:com|org|net|io|dev|ai"
                        r"|co|edu|gov|sh|xyz|app|cloud|me|info|so|gg|us|uk|de))(?=[:/]|$))")
_GIT_REMOTE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(?:clone|fetch|ls-remote|pull|remote\s+add|submodule)\b"
                         r"[^;&|\n]*?((?:https?|ssh|git)://\S+|[\w.-]+@[\w.-]+:\S+)")
_GITHUB = re.compile(r"\b(?:github\.com|githubusercontent\.com|api\.github\.com)\b", re.I)
_ANY_SCRATCH = re.compile(r"(?:/private)?/tmp/claude-")
_NET_FAIL = re.compile(r"Couldn't connect|Could not resolve|Failed to connect|unable to access|"
                       r"Connection refused|Network is unreachable|Operation not permitted|"
                       r"failed: |Name or service not known|nodename nor servname|"
                       r"Temporary failure in name resolution|Failed to establish a new connection|"
                       r"No matching distribution found|no such host|(?-i:\bENOTFOUND\b|\bEAI_AGAIN\b|"
                       r"\bECONNREFUSED\b)|spurious network error|Couldn't resolve host", re.I)
# Node's error codes are matched case-sensitively and whole: case-folded,
# ENOTFOUND is inside every "ModuleNotFoundError" / "FileNotFoundError", and a
# Python traceback anywhere in a result turned a real fetch into an attempt
# (Q38U-L0's 122_gemm_blocked_b, the rig-arms rescan of 2026-11-25).
# The shell never ran the network command: "<cmd>: command not found".
_NOT_FOUND = re.compile(r"(?:^|[\s:])([\w.+-]+): (?:command )?not found\b", re.M)
# Review 1124: the other ways a shell reaches off the host. A package
# manager's install fetches from its index unless told to stay offline;
# nc / telnet / ssh / scp / httpie name a host; a python / node one-liner
# opens a URL or a socket (any host that is not loopback, TLD or not).
_PKG = re.compile(r"(?:^|[\s;&|(`$])(?P<cmd>(?:python3?\s+-m\s+)?pip3?\s+(?:install|download)"
                  r"|uv\s+(?:pip\s+install|add|sync|tool\s+install)"
                  r"|cargo\s+(?:add|install|fetch)|go\s+(?:get|mod\s+download)"
                  r"|go\s+install\s+\S+@\S+|npm\s+(?:install|i|add|ci)|npx\s+-y|yarn\s+add"
                  r"|pnpm\s+(?:add|install|i)|gem\s+install|apt(?:-get)?\s+install"
                  r"|conda\s+install)\b(?P<rest>[^;&|\n`)]*)")
_PKG_LOCAL = re.compile(r"--no-index|--offline|--frozen|--path\b|--find-links\s+/|-f\s+/"
                        r"|--index-url[=\s]+https?://(?:localhost|127\.|\[?::1)"
                        r"|(?:^|\s)-i\s+https?://(?:localhost|127\.|\[?::1)")
_LOCAL_INDEX = re.compile(r"Looking in indexes: https?://(?:localhost|127\.|\[?::1)")
_HOSTCMD = re.compile(r"(?:^|[\s;&|(`$])(nc|ncat|netcat|telnet|ssh|scp|sftp|rsync|socat)\s+([^;&|\n`)]*)")
_HTTPIE = re.compile(r"(?:^|[\s;&|(`$])(https?|xh)\s+(?:(?:GET|POST|PUT|HEAD|DELETE|PATCH)\s+)?"
                     r"(?:-\S+\s+)*(\S+)")
_SCRIPT_NET = re.compile(r"urlopen|urllib|requests\.|http\.client|httpx|socket\.|create_connection"
                         r"|\bfetch\(|https?\.get\(|net\.connect", re.I)
_ANY_URL = re.compile(r"(?:https?|ftps?|wss?)://(?:[^@/\s'\"]+@)?(\[[^\]]+\]|[^/:\s'\"]+)")
_SOCK_ADDR = re.compile(r"""\(\s*['"]([\w.-]+)['"]\s*,\s*\d+\s*\)""")
# Review 1118: write content that makes the BUILD read an absolute path.
_EMBED = re.compile(r'#\s*include\s*[<"](/[^>"]+)[>"]'
                    r'|include_(?:str|bytes)!\s*\(\s*r?#*"(/[^"]+)"'
                    r'|@(?:embedFile|import)\s*\(\s*"(/[^"]+)"'
                    r"""|\bopen\s*\(\s*[rbf]*["'](/[^"']+)["']"""
                    r'|\bfopen\s*\(\s*"(/[^"]+)"')
# 2026-11-18: this project's own repository, on any host, and URL paths into
# evals/ answer trees. The same patterns as agents/tools.py's eval fetch guard
# (_EVAL_SELF_REPO_RE / _EVAL_URL_EVALS_RE; a test pins them equal).
_SELF_REPO = re.compile(r"maximiliankhan(?:/|:|%2f)+openbeast(?![\w-])", re.I)
_URL_EVALS = re.compile(r"/evals/(?:tasks|refs|suites|results|cache|reference)(?:/|$)", re.I)
_URL_IN = re.compile(r"(?:https?|ftps?)://[^\s'\"<>`]+", re.I)
_DENIED = "Operation not permitted"
WEB_TOOLS = {"fetch", "web_search"}
INFO_KINDS = {"source", "web", "toolchain"}


def _norm_home(p: str) -> str:
    return re.sub(r"^(?:\$HOME|\$\{HOME\})", "~", p)


def path_kinds(p: str) -> set:
    """Forbidden kinds of one absolute (or ~) path."""
    p = _norm_home(p)
    out = set()
    if _FIXTURE.match(p):
        # a task dir is the agent's own, except an answer key copied (cloned)
        # into it: evals/tasks|refs|suites under /tmp/eval* (review 1118)
        if re.search(r"/evals/(?:tasks|refs|suites)(?:/|$)", p):
            out.add("evals")
        return out
    if _CLAUDE_HOME.search(p) or p.startswith("~/.claude"):
        out.add("claude")
    if _SCRATCH.search(p):
        # a toolchain an operator unpacked there (the zig of the 11-16 runs)
        # is the agent's compiler, not a campaign's data
        out.add("toolchain" if _TOOLCHAIN.search(p) else "scratch")
    for kind, rx in _ABS_KIND:
        if rx.search(p):
            out.add(kind)
    return out


def _arg_texts(name: str, args) -> list[str]:
    """The parts of a tool call that name what it reads."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return [args]
    if not isinstance(args, dict):
        return []
    if name == "bash":
        return [str(args.get("command", ""))]
    if name in ("write_file", "edit_file", "task_done", "update_plan"):
        return []             # writes and summaries read nothing
    return [str(args.get(k, "")) for k in ("path", "directory", "url", "query") if args.get(k)]


def network_hits(command: str) -> list[str]:
    """Evidence that a bash command reaches off the host (review 1118): a
    curl / wget URL whose host is not loopback, a git remote operation on a
    URL, any github.com / githubusercontent.com."""
    ev = []
    for m in _FETCHER.finditer(command):
        for tok in m.group(2).split():
            tok = tok.strip("'\"")
            url = _URL_TOKEN.match(tok)
            if not url:
                continue
            host = (url.group("host") or url.group("host2") or "").strip("[]")
            if not _LOOPBACK.match(host):
                ev.append(f"{m.group(1)} {tok}"[:160])
                break
    for m in _GIT_REMOTE.finditer(command):
        host = re.sub(r"^\w+://|^[\w.-]+@", "", m.group(1)).split("/")[0].split(":")[0]
        if not _LOOPBACK.match(host):
            ev.append(m.group(0)[:160])
    for m in _PKG.finditer(command):
        if not _PKG_LOCAL.search(m.group("rest")) and not _local_install(m.group("cmd"), m.group("rest")):
            ev.append(re.sub(r"\s+", " ", m.group(0).strip())[:160])
    for m in _HOSTCMD.finditer(command):
        host = _first_host(m.group(1), m.group(2))
        if host and not _LOOPBACK.match(host):
            ev.append(f"{m.group(1)} {host}"[:160])
    for m in _HTTPIE.finditer(command):
        url = _URL_TOKEN.match(m.group(2).strip("'\""))
        host = url and (url.group("host") or url.group("host2") or "").strip("[]")
        if host and not _LOOPBACK.match(host):
            ev.append(f"{m.group(1)} {m.group(2)}"[:160])
    if _SCRIPT_NET.search(command) and re.search(r"\b(?:python3?|node|ruby|perl|deno|bun)\b", command):
        for m in list(_ANY_URL.finditer(command)) + list(_SOCK_ADDR.finditer(command)):
            host = m.group(1).strip("[]")
            if not _LOOPBACK.match(host) and not re.fullmatch(r"\d+", host):
                ev.append(m.group(0)[:160])
                break
    m = _GITHUB.search(command)
    if m and not ev:
        ev.append(m.group(0))
    return ev


def _local_install(cmd: str, rest: str) -> bool:
    """A package command whose every argument is a local path (pip install -e .,
    pip install ./pkg, cargo install --path .): nothing is fetched by name."""
    if not cmd.startswith(("pip", "python", "uv pip")):
        return False
    args = [a.strip("'\"") for a in rest.split() if not a.startswith("-")]
    return bool(args) and all(a.startswith((".", "/", "~")) or a.endswith((".whl", ".tar.gz"))
                              for a in args)


def _first_host(cmd: str, rest: str) -> str:
    """The remote host of nc / telnet / ssh / scp / rsync (user@ stripped)."""
    toks = rest.split()
    skip_next = {"-p", "-i", "-o", "-l", "-P", "-F", "-e", "-w", "-s", "-J"}
    i = 0
    while i < len(toks):
        t = toks[i].strip("'\"")
        if t.startswith("-"):
            i += 2 if t in skip_next else 1
            continue
        if cmd in ("scp", "rsync", "sftp") and ":" not in t and cmd != "sftp":
            i += 1
            continue
        if cmd == "socat":
            m = re.search(r"(?:TCP\d?|SSL|OPENSSL):([^:,]+):", t, re.I)
            return m.group(1) if m else ""
        t = t.split("@")[-1]
        if cmd in ("scp", "rsync"):
            t = t.split(":")[0]
        t = t.strip("[]")
        # A host is a name or an address. Code that merely uses `nc` as an
        # identifier (`nc = len(x)`, `if nc == 0`, `for nc {` inside a heredoc;
        # INK-T2-STOCK's 168_linearizability_kv_e, rescanned 2026-11-25) puts
        # an operator or brace where the host would be: not a host.
        return t if _HOST_TOKEN.fullmatch(t) else ""
    return ""


_HOST_TOKEN = re.compile(r"(?=[^:]*[A-Za-z0-9])[A-Za-z0-9_.-]+|[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*")


def embed_paths(content: str) -> list[str]:
    """Absolute paths a written file includes, embeds or opens."""
    return [next(g for g in m.groups() if g) for m in _EMBED.finditer(content or "")]


def self_repo_hits(text: str) -> list[str]:
    """This project's own repository named anywhere in `text`, or a URL whose
    path reaches a checkout's evals/ answer trees (percent-decoded)."""
    t = str(text or "")
    for _ in range(3):
        dec = urllib.parse.unquote(t)
        if dec == t:
            break
        t = dec
    ev = [m.group(0) for m in _SELF_REPO.finditer(t)]
    for m in _URL_IN.finditer(t):
        if _URL_EVALS.search(urllib.parse.urlparse(m.group(0)).path):
            ev.append(m.group(0)[:160])
    return ev


def call_kinds(name: str, args) -> tuple[set, list[str]]:
    """(kinds, evidence) for the arguments of one tool call."""
    kinds, ev = set(), []
    if name in WEB_TOOLS:
        own = self_repo_hits(json.dumps(args))
        return ({"web", "selfrepo"} if own else {"web"}), ([json.dumps(args)[:200]] + own)[:6]
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            pass
    if isinstance(args, dict) and name == "bash":
        cmd = str(args.get("command", ""))
        net = network_hits(cmd)
        if net:
            kinds.add("network")
            ev += net
        own = self_repo_hits(cmd)
        if own:
            kinds.add("selfrepo")
            ev += own
        if _ANY_SCRATCH.search(cmd) and not _SCRATCH.search(cmd):
            kinds.add("scratch")          # /tmp/claude-$UID, /tmp/claude-*
            ev.append(_ANY_SCRATCH.search(cmd).group(0))
    if isinstance(args, dict) and name in ("write_file", "edit_file"):
        for p in embed_paths(str(args.get("content") or args.get("new_string") or "")):
            k = path_kinds(p)
            if k:
                kinds |= k | {"embed"}
                ev.append(p[:160])
    for text in _arg_texts(name, args):
        # A relative evals/ or tests/ means the repo only after a cd out of
        # /tmp (bash starts in the eval workdir; the file tools resolve a
        # relative path there too). A cd to a bare relative name
        # (`cd /tmp/eval_x && (cd tests && ...)`) descends from where the
        # shell already is, so only an absolute or home-relative target
        # leaves (GLM-UP 2026-11-23: a fixture's own tests/ was flagged).
        left_tmp = name == "bash" and any(
            (c.startswith(("/", "~"))
             and not c.startswith(("/tmp", "/private/tmp", "/var/folders")))
            or c.startswith(("$HOME", "${HOME}"))
            for c in _CD.findall(text))
        for m in _ABS.finditer(text):
            k = path_kinds(m.group(0))
            if k:
                kinds |= k
                ev.append(m.group(0)[:160])
        if left_tmp:
            for m in _REL.finditer(text):
                kinds.add({"agents/logs": "logs"}.get(m.group(1), m.group(1)))
                ev.append(m.group(0))
        m = _LEDGER.search(text)
        if m:
            kinds.add("ledger")
            ev.append(m.group(0))
        m = _SOURCE.search(text)
        if m:
            kinds.add("source")
            ev.append(m.group(0))
    return kinds, ev


def result_kinds(result: str, context: str = "") -> tuple[set, list[str]]:
    """Forbidden paths a tool's OUTPUT printed (a listing, a grep -r).
    `context` (the call's arguments) supplies toolchain paths: a stored
    result can cut a line mid-path, and a cut toolchain path is still one."""
    if not result or _REFUSED.match(result):
        return set(), []
    tool_paths = [m.group(0) for m in _ABS.finditer(context + "\n" + result)
                  if "toolchain" in path_kinds(m.group(0))]
    base = _cd_base(context)
    kinds, ev = set(), []

    def _hit(p):
        k = path_kinds(p) - INFO_KINDS
        if k and not any(t.startswith(p) for t in tool_paths):
            kinds.update(k)
            ev.append(p[:160])
            return True
        return False

    for ln in result.splitlines():
        # A line that STARTS with a home or scratch path (ls -d / find /
        # grep -r / grep -l output): every forbidden kind.
        m = re.match(r"\s*((?:/Users/|/home/|/root/|/private/tmp/claude-|/tmp/claude-)" + _P + r"*)", ln)
        if m and _hit(m.group(1)):
            continue
        # Review 1124: a sealed answer tree ANYWHERE in a line (ls -l, find
        # -ls, du -a, stat) under ANY root (/root/ob, /work, a mount). Only the
        # answer trees count here, so an echoed PATH or a toolchain listing is
        # not a hit; an error line about a path the agent typed lists nothing.
        if "No such file" in ln or _DENIED in ln:
            continue
        for m in _ANSWER_ABS.finditer(ln):
            if _hit(m.group(1)):
                break
        else:
            # `cd / && find .`: a relative listing resolves against the cd
            if base:
                for m in _ANSWER_REL.finditer(ln):
                    if _hit(os.path.normpath(os.path.join(base, m.group(1)))):
                        break
    return ({"listed"} if kinds else set()), ev[:5]


_ANSWER = r"evals/(?:tasks|refs|suites|results|cache)(?:/|$|(?=[\s'\":]))"
_ANSWER_ABS = re.compile(r"(?:^|(?<=[\s'\"=:(\[,]))(/" + _P + r"*?/" + _ANSWER + _P + r"*)")
_ANSWER_REL = re.compile(r"(?:^|(?<=[\s'\"=:(\[,]))((?:\./)?(?:[\w.+@-]+/)*" + _ANSWER + _P + r"*)")


def _cd_base(context: str) -> str:
    """The directory a relative listing is relative to: the last absolute cd
    out of the eval workdir in the call's command (`cd / && find .`), or
    nothing (bash starts in the task's own /tmp/eval* dir)."""
    try:
        cmd = str(json.loads(context).get("command", ""))
    except (ValueError, AttributeError):
        cmd = context
    base = ""
    for c in _CD.findall(cmd):
        if c.startswith("/"):
            base = c
        elif base and not c.startswith(("~", "$")):
            base = os.path.join(base, c)
        else:
            base = ""
    if base.startswith(("/tmp/eval", "/private/tmp/eval")):
        return ""
    return base


def refused(result: str) -> bool:
    r = (result or "").strip()
    if _REFUSED.match(r):
        return True
    lines = [ln for ln in r.splitlines() if ln.strip() and not ln.startswith(("(exit code", "[eval sandbox"))]
    return bool(lines) and all(_DENIED in ln or "No such file" in ln for ln in lines)


def scan_transcript(path: str) -> list[dict]:
    hits = []
    it = 0
    for e in T.read_events(path):
        if e.get("type") == "iteration":
            it = e.get("number", it)
            continue
        if e.get("type") != "tool_call":
            continue
        name = e.get("name") or e.get("tool") or "?"
        args = e.get("args") or {}
        res = str(e.get("result", ""))
        kinds, ev = call_kinds(name, args)
        if "network" in kinds and _LOCAL_INDEX.search(res) and not network_hits(
                _PKG.sub(" ", str((args or {}).get("command", "")) if isinstance(args, dict) else "")):
            kinds.discard("network")      # pip used a loopback index (the ark)
        rk, rev = result_kinds(res, json.dumps(args))
        if not kinds and not rk:
            continue
        status = "refused" if refused(res) else "read"
        if status == "read" and "network" in kinds \
                and (kinds | rk) - INFO_KINDS <= {"network", "selfrepo"} \
                and (_NET_FAIL.search(res) or _never_ran(name, args, res)):
            status = "refused"            # the connection failed (the sandbox, since 1118)
        hits.append({"iteration": it, "tool": name, "kinds": sorted(kinds | rk),
                     "status": status, "evidence": (ev + rev)[:6],
                     "input": json.dumps(args)[:240], "result": res[:160]})
    return hits


def _never_ran(name: str, args, res: str) -> bool:
    """Every network command of a bash call was missing from the host: the
    shell printed "<cmd>: command not found" for each (`pip3` absent from the
    eval-host image; Q38U-T2's 122_gemm_blocked_a, the rig-arms rescan of
    2026-11-25). A command that is not named in such a line, or ran, keeps
    the hit."""
    if name != "bash" or not isinstance(args, dict):
        return False
    missing = set(_NOT_FOUND.findall(res))
    ev = network_hits(str(args.get("command", "")))
    if not missing or not ev:
        return False
    for e in ev:
        word = e.lstrip("(`$;&| ").split()[0] if e.strip() else ""
        if word not in missing:
            return False
    return True


def contaminated(hits: list[dict]) -> bool:
    return any(h["status"] == "read" and set(h["kinds"]) - INFO_KINDS for h in hits)


# --- the trailing-dot bug ------------------------------------------------------

def dotted_paths(unit: dict) -> list[str]:
    """The dotted expected paths run_eval derived before G24."""
    spec = " ".join(str(unit.get(k, "")) for k in ("task", "validation", "setup", "cleanup"))
    return sorted({p for p in _OLD_PATHS.findall(spec) if p.endswith(".")})


def dot_turns(path: str, dotted: list[str]) -> dict:
    if not dotted:
        return {}
    names = {os.path.basename(p) for p in dotted} | set(dotted)
    rx = re.compile("|".join(re.escape(n) + r"(?![\w.])" for n in sorted(names, key=len, reverse=True)))
    first_refusal, last_it, touched = None, 0, set()
    it = 0
    for e in T.read_events(path):
        if e.get("type") == "iteration":
            it = e.get("number", it)
            last_it = max(last_it, it)
            continue
        if e.get("type") != "tool_call":
            continue
        name = e.get("name") or ""
        res = str(e.get("result", ""))
        if name == "task_done" and res.startswith("NOT DONE") and any(p in res for p in dotted):
            if first_refusal is None:
                first_refusal = it
            touched.add(it)
        elif name != "task_done" and rx.search(json.dumps(e.get("args") or {})):
            touched.add(it)
    return {"dotted": dotted, "first_refusal": first_refusal, "iterations": last_it,
            "after_refusal": (last_it - first_refusal) if first_refusal is not None else 0,
            "touched": len(touched)}


# --- units ---------------------------------------------------------------------

def load_units(tree: str) -> dict:
    """{effective unit id: unit dict} from a checkout's evals/tasks/, read
    as data (another tree's run_eval is never imported)."""
    out = {}
    for p in sorted(glob.glob(os.path.join(tree, "evals", "tasks", "*.json"))):
        try:
            with open(p) as f:
                t = json.load(f)
        except (OSError, ValueError):
            continue
        if t.get("variants"):
            for v in t["variants"]:
                out[f"{t['id']}_{v['id']}"] = {**t, **v, "id": f"{t['id']}_{v['id']}"}
        elif t.get("id"):
            out[t["id"]] = t
    return out


def _model_of(path: str) -> str:
    try:
        with open(path) as f:
            return str(json.loads(f.readline() or "{}").get("model") or "")
    except (OSError, ValueError):
        return ""


def scan_arm(label: str, files: list[str], tree: str, logs_dir: str | None,
             trailing_dot: bool, model: str = "", until: float | None = None) -> dict:
    """One arm. No results files: every transcript in the logs dir (whose
    model matches `model`, a case-insensitive substring) is scanned."""
    units = load_units(tree)
    texts = {u: T._norm(d.get("task", "")) for u, d in units.items() if d.get("task")}
    logs = logs_dir or os.path.join(tree, "agents", "logs")
    rows: dict = {}
    scored: dict = {}            # transcript -> unit, for the transcript a row was scored on
    seen: dict = {}
    for f in files:
        since, end = T.results_window(f, until)
        _, rr = campaign.load_rows(f)
        idx = T.index_logs(logs, since, end)
        for u, p in T.match_transcripts({u: units.get(u, {}).get("task") for u in rr}, idx).items():
            scored[p] = u
        rows.update(rr)
        for e in idx:
            seen[e["path"]] = e
    if not files:
        seen = {e["path"]: e for e in T.index_logs(logs)}
    if model:
        seen = {p: e for p, e in seen.items() if model.lower() in _model_of(p).lower()}
    # A result row with no matched transcript means the window missed it (a
    # naive-timestamp zone mismatch, a reset mtime): never a clean verdict.
    unattributed = sorted(set(rows) - set(scored.values())) if files else []
    out = {"label": label, "tree": tree, "files": files, "transcripts": 0, "units": {},
           "rows": len(rows), "unattributed": unattributed}
    for p, e in sorted(seen.items()):
        unit = scored.get(p)
        if unit is None:
            unit = next((u for u, t in texts.items() if t and (e["task"] == t or e["task"].startswith(t))),
                        None)
        if unit is None:
            continue
        out["transcripts"] += 1
        hits = scan_transcript(p)
        row = rows.get(unit) or {}
        rec = {"unit": unit, "transcript": p, "scored": p in scored,
               "passed": bool(row.get("passed")) if p in scored else None,
               "hits": hits, "contaminated": contaminated(hits)}
        if trailing_dot:
            rec["trailing_dot"] = dot_turns(p, dotted_paths(units.get(unit, {})))
        out["units"].setdefault(unit, []).append(rec)
    return out


def _parse_run(spec: str) -> tuple[str, list[str], str]:
    label, _, rest = spec.partition("=")
    files, _, tree = rest.partition("@")
    return label, [x for x in files.split(",") if x], tree or REPO


def report(arms: list[dict], trailing_dot: bool) -> str:
    lines = []
    for a in arms:
        recs = [r for rs in a["units"].values() for r in rs]
        cont = [r for r in recs if r["contaminated"]]
        lines.append(f"== {a['label']}: {a['transcripts']} transcripts, {len(a['units'])} units, "
                     f"{len(cont)} contaminated transcript(s)")
        if a.get("unattributed"):
            lines.append(f"  UNATTRIBUTED: {len(a['unattributed'])} of {a['rows']} result rows have "
                         f"no matched transcript (check --tz / --until / the file's mtime)")
        for r in sorted(recs, key=lambda r: r["unit"]):
            if not r["hits"]:
                continue
            verdict = {True: "PASS", False: "FAIL", None: "(not scored)"}[r["passed"]]
            tag = "CONTAMINATED" if r["contaminated"] else "attempts/info only"
            lines.append(f"  {r['unit']} [{verdict}] {tag}  {os.path.basename(r['transcript'])}")
            for h in r["hits"][:12]:
                lines.append(f"    it{h['iteration']:<3} {h['tool']:<10} {h['status']:<7} "
                             f"{','.join(h['kinds']):<18} {'; '.join(h['evidence'])[:150]}")
            if len(r["hits"]) > 12:
                lines.append(f"    ... +{len(r['hits']) - 12} more")
        if trailing_dot:
            dots = [r for r in recs if r.get("trailing_dot") and r["scored"]]
            hit = [r for r in dots if r["trailing_dot"]["after_refusal"] or r["trailing_dot"]["touched"]]
            after = sum(r["trailing_dot"]["after_refusal"] for r in hit)
            touched = sum(r["trailing_dot"]["touched"] for r in hit)
            lines.append(f"  trailing-dot bug: {len(dots)} scored units with a dotted path; "
                         f"{len(hit)} affected; {after} turns after a refused task_done; "
                         f"{touched} turns touched a dotted path")
            for r in sorted(hit, key=lambda r: r["unit"]):
                d = r["trailing_dot"]
                verdict = "PASS" if r["passed"] else "FAIL"
                lines.append(f"    {r['unit']:<28} {verdict}  it={d['iterations']:<3} "
                             f"refused@{d['first_refusal']}  after={d['after_refusal']:<3} "
                             f"touched={d['touched']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+", help="LABEL=results.json[,more.json][@TREE]; "
                                             "LABEL=@TREE scans every transcript of TREE")
    ap.add_argument("--logs-dir", help="override every arm's TREE/agents/logs")
    ap.add_argument("--model", default="", help="only transcripts whose start event names this "
                                                 "model (substring, any case)")
    ap.add_argument("--json", help="write the full per-transcript report here")
    ap.add_argument("--trailing-dot", action="store_true",
                    help="also count the turns lost to the trailing-dot path bug")
    ap.add_argument("--tz", help="zone for timestamps written without one (the writing host's, "
                                 "e.g. UTC or America/Los_Angeles); default: this host's")
    ap.add_argument("--until", action="append", default=[], metavar="[LABEL=]ISO",
                    help="the run's end (from its campaign log), overriding the results file's "
                         "saved time or mtime; LABEL= limits it to one arm; repeatable")
    a = ap.parse_intermixed_args(argv)
    T.set_default_tz(a.tz)
    untils: dict = {}
    for u in a.until:
        lab, sep, iso = u.rpartition("=")
        untils[lab if sep else None] = T.parse_time(iso)
    arms = []
    for spec in a.runs:
        label, files, tree = _parse_run(spec)
        arms.append(scan_arm(label, files, os.path.abspath(tree), a.logs_dir, a.trailing_dot,
                             a.model, untils.get(label, untils.get(None))))
    print(report(arms, a.trailing_dot))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(arms, f, indent=1)
    if any(arm.get("unattributed") for arm in arms):
        return 2             # a partial scan is never a clean (0) verdict
    return 1 if any(r["contaminated"] for arm in arms for rs in arm["units"].values() for r in rs) else 0


if __name__ == "__main__":
    sys.exit(main())
