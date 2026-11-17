#!/usr/bin/env python3
"""Integrity scan of OpenBeast agent transcripts (a model's eval runs): did
the agent's tools read or list what a measured unit must not see?

  python3 evals/audit/integrity_scan.py LABEL=RESULTS.json[,MORE.json][@TREE] ... \\
      [--model GLM] [--json out.json] [--trailing-dot]
  python3 evals/audit/integrity_scan.py ALL=@TREE --model GLM   # every transcript

Each LABEL is one arm. RESULTS are its run_eval results files (a main run and
its reruns); TREE is the checkout it ran from (default: this one), whose
evals/tasks/ give the task texts and whose agents/logs/ holds the
transcripts. Every transcript that started inside a results file's window
is scanned, and attributed to its unit by task text (transcripts.py); the
row's verdict is shown for the transcript the row was scored on.

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
  web         fetch / web_search (refused under eval since v5; recorded)
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
_REFUSED = re.compile(r"^Error: refusing|disabled while an eval unit is measured")
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


def call_kinds(name: str, args) -> tuple[set, list[str]]:
    """(kinds, evidence) for the arguments of one tool call."""
    kinds, ev = set(), []
    if name in WEB_TOOLS:
        return {"web"}, [json.dumps(args)[:200]]
    for text in _arg_texts(name, args):
        # A relative evals/ or tests/ means the repo only after a cd out of
        # /tmp (bash starts in the eval workdir; the file tools resolve a
        # relative path there too).
        left_tmp = name == "bash" and any(
            not c.startswith(("$", "/tmp", "/private/tmp", "/var/folders", "."))
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
    kinds, ev = set(), []
    # Only lines that START with a path (ls -d / find / grep -r / grep -l
    # output), so an echoed PATH or an error message is not a listing.
    for ln in result.splitlines():
        m = re.match(r"\s*((?:/Users/|/home/|/private/tmp/claude-|/tmp/claude-)" + _P + r"*)", ln)
        if m:
            k = path_kinds(m.group(1)) - INFO_KINDS
            if k and not any(t.startswith(m.group(1)) for t in tool_paths):
                kinds |= k
                ev.append(m.group(1)[:160])
    return ({"listed"} if kinds else set()), ev[:5]


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
        rk, rev = result_kinds(res, json.dumps(args))
        if not kinds and not rk:
            continue
        status = "refused" if refused(res) else "read"
        hits.append({"iteration": it, "tool": name, "kinds": sorted(kinds | rk),
                     "status": status, "evidence": (ev + rev)[:6],
                     "input": json.dumps(args)[:240], "result": res[:160]})
    return hits


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
             trailing_dot: bool, model: str = "") -> dict:
    """One arm. No results files: every transcript in the logs dir (whose
    model matches `model`, a case-insensitive substring) is scanned."""
    units = load_units(tree)
    texts = {u: T._norm(d.get("task", "")) for u, d in units.items() if d.get("task")}
    logs = logs_dir or os.path.join(tree, "agents", "logs")
    rows: dict = {}
    scored: dict = {}            # transcript -> unit, for the transcript a row was scored on
    seen: dict = {}
    for f in files:
        since, until = T.results_window(f)
        _, rr = campaign.load_rows(f)
        idx = T.index_logs(logs, since, until)
        for u, p in T.match_transcripts({u: units.get(u, {}).get("task") for u in rr}, idx).items():
            scored[p] = u
        rows.update(rr)
        for e in idx:
            seen[e["path"]] = e
    if not files:
        seen = {e["path"]: e for e in T.index_logs(logs)}
    if model:
        seen = {p: e for p, e in seen.items() if model.lower() in _model_of(p).lower()}
    out = {"label": label, "tree": tree, "files": files, "transcripts": 0, "units": {}}
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
    a = ap.parse_args(argv)
    arms = []
    for spec in a.runs:
        label, files, tree = _parse_run(spec)
        arms.append(scan_arm(label, files, os.path.abspath(tree), a.logs_dir, a.trailing_dot,
                             a.model))
    print(report(arms, a.trailing_dot))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(arms, f, indent=1)
    return 1 if any(r["contaminated"] for arm in arms for rs in arm["units"].values() for r in rs) else 0


if __name__ == "__main__":
    sys.exit(main())
