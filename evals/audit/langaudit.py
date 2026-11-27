#!/usr/bin/env python3
"""Per-language failure taxonomy: how many of a run's failed units hit a
KNOWLEDGE error (an API the model's training does not match the installed
toolchain), a host-userland error, or spent turns researching the toolchain.

  python3 evals/audit/langaudit.py L0=RESULTS.json [B=a.json,b.json ...] \\
      [--repo TREE] [--logs-dir TREE/agents/logs] [--out taxonomy.json] [--errors]

Each LABEL=FILES is one run (several files merge: a main run and its
reruns). For every failed, scorable unit the transcript is found
(transcripts.py) and every tool result scanned:

  K  knowledge     zig 0.16 std API drift, rust/c/go API misuse a pack or a
                   language reference would answer
  L  lint          zig's compile-time lint errors (unused local, never mutated)
  B  borrow        rust ownership / lifetime errors (reasoning, not knowledge)
  H  host          the BSD/macOS userland (no nproc, no timeout, BSD sed/cat)
  R  research      turns that read the toolchain's own sources or docs

The decision rule this feeds (docs/MODEL_CAMPAIGN.md §5, arm E): a language
pack is worth its arm only where K failures are a real share of that
language's failures. 2026-11-16 GLM-5.3-Flash: zig yes (most zig failures
were 0.16 API drift); rust partly (edition / API); go, c, python no. A host
pack is the answer to H, whatever the language.

--errors also prints the first distinct compiler / runtime errors of each
transcript (the raw material for new K patterns).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVALS = os.path.dirname(HERE)
sys.path.insert(0, EVALS)
sys.path.insert(0, HERE)

import campaign  # noqa: E402
import transcripts as T  # noqa: E402

# family -> (class, language or None for any, regex). From the 2026-11-16 GLM taxonomy.
PATTERNS = {
    "zig": ("K", "zig",
            r"has no member named '(cwd|File|GeneralPurposeAllocator|io|ResetEvent|write|args|init"
            r"|getStdOut|getStdIn)'|no field or member function named '\w+' in '(Io|fs|array_list)"
            r"|member function expected \d argument|Thread\.SpawnConfig"
            r"|expected '\(', found 'an identifier'|expected a tuple, found|@std\b"),
    "zlint": ("L", "zig", r"unused local|never mutated|pointless discard|shadows primitive"
                          r"|unused function parameter"),
    "rust": ("K", "rust", r"no method named `try_into`|cannot write into `String`|E0133"
                          r"|unresolved import|static_mut_refs"),
    "rborrow": ("B", "rust", r"E0499|E0382|E0596|E0597|E0502|E0505|E0506"
                             r"|cannot be sent between threads|does not live long enough"),
    "c": ("K", None, r"pthread_barrier|bits/stdc\+\+\.h|sysctlbyname"
                     r"|call to undeclared function 'sysconf'"),
    "go": ("K", "go", r"no new variables on left side"),
    "host": ("H", None, r"nproc: command not found|timeout: command not found|sed: \d+: |sed: -e"
                        r"|illegal option|invalid option -- P"),
}
RESEARCH = re.compile(r"lib/std|zig env|zig std|language_reference|\.rustup|go doc|GOROOT"
                      r"|/usr/include/pthread")
ERRORS = re.compile(
    r"(error(\[E\d+\])?:.*|undefined: .*|.*no new variables.*|.*declared and not used.*"
    r"|.*cannot use .*|.*not enough arguments.*|.*has no member named.*|.*no member named.*"
    r"|.*use of undeclared identifier.*|.*implicit declaration.*|.*no matching function.*"
    r"|.*Killed: 9.*|.*Segmentation fault.*|.*panicked at.*|.*command not found.*|sed: .*"
    r"|.*invalid option.*|.*unknown type name.*)")
COMPILED = {k: (c, lang, re.compile(p)) for k, (c, lang, p) in PATTERNS.items()}


def norm_lang(lang: str | None) -> str:
    lang = (lang or "python").lower()
    return {"c++": "cpp", "golang": "go", "rs": "rust"}.get(lang, lang)


def scan(path: str, lang: str) -> dict:
    """{family: [iterations]} plus research turns and the run's end."""
    hits = collections.defaultdict(set)
    ex = {}
    errs = collections.OrderedDict()
    it = 0
    end = None
    for e in T.read_events(path):
        typ = e.get("type")
        end = typ or end
        if typ == "iteration":
            try:
                it = int(e.get("number") or it)
            except (TypeError, ValueError):
                pass
        if typ != "tool_call":
            continue
        res = str(e.get("result", ""))
        args = str(e.get("args", ""))
        for fam, (_cls, flang, rx) in COMPILED.items():
            if flang and flang != lang:
                continue
            m = rx.search(res)
            if m:
                hits[fam].add(it)
                ex.setdefault(fam, res[max(0, m.start() - 60):m.end() + 40].replace("\n", " "))
        if RESEARCH.search(args):
            hits["research"].add(it)
        for m in ERRORS.finditer(res):
            k = re.sub(r"^\S*?:\d+:\d+: ", "", m.group(0).strip())[:110]
            errs.setdefault(k, []).append(it)
    return {"hits": {k: sorted(v) for k, v in hits.items()}, "examples": ex,
            "iterations": it, "end": end, "errors": list(errs.items())[:14]}


def classes(hits: dict) -> set:
    out = {COMPILED[f][0] for f in hits if f in COMPILED}
    if hits.get("research"):
        out.add("R")
    return out


def run_label(label: str, files: list[str], repo: str, logs_dir: str, tasks: dict) -> list[dict]:
    rows = campaign.merge_rows(files)
    paths: dict = {}
    for rp in files:
        since, until = T.results_window(rp)
        _, rr = campaign.load_rows(rp)
        paths.update(T.match_transcripts({u: (tasks.get(u) or {}).get("task") for u in rr},
                                         T.index_logs(logs_dir, since, until)))
    out = []
    for uid, r in sorted(campaign.scorable(rows).items()):
        if r.get("passed"):
            continue
        lang = norm_lang(r.get("language") or (tasks.get(uid) or {}).get("language"))
        p = paths.get(uid)
        s = scan(p, lang) if p else {"hits": {}, "examples": {}, "iterations": None,
                                     "end": None, "errors": []}
        out.append({"run": label, "unit": uid, "lang": lang, "transcript": p,
                    "classes": sorted(classes(s["hits"])), **s})
    return out


def summary(units: list[dict], k_share: float) -> tuple[str, dict]:
    by = collections.defaultdict(lambda: collections.Counter())
    for u in units:
        c = by[(u["run"], u["lang"])]
        c["failed"] += 1
        c["no_transcript"] += 0 if u["transcript"] else 1
        for cls in u["classes"]:
            c[cls] += 1
    lines = ["| Run | Language | Failed | K knowledge | L lint | B borrow | H host | R research | No transcript |",
             "|---|---|---|---|---|---|---|---|---|"]
    verdict: dict = {}
    for (run, lang), c in sorted(by.items()):
        lines.append(f"| {run} | {lang} | {c['failed']} | {c['K']} | {c['L']} | {c['B']} | {c['H']} "
                     f"| {c['R']} | {c['no_transcript']} |")
        v = verdict.setdefault(lang, {"k": 0, "failed": 0, "h": 0})
        v["k"] += c["K"]
        v["failed"] += c["failed"]
        v["h"] += c["H"]
    pack_langs = sorted(lang for lang, v in verdict.items()
                        if v["failed"] and v["k"] / v["failed"] >= k_share)
    host = sum(v["h"] for v in verdict.values())
    text = "\n".join(lines) + "\n\n" + \
        f"Language packs indicated (K >= {k_share:.0%} of that language's failures): " \
        f"{', '.join(pack_langs) or 'none'}.\n" + \
        (f"Host-userland errors in {host} failed units: a host pack (or the env block) is indicated.\n"
         if host else "")
    return text, {"pack_langs": pack_langs, "host_hits": host, "by_lang": verdict}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+", help="LABEL=results.json[,more.json]")
    ap.add_argument("--repo", default=os.path.dirname(EVALS))
    ap.add_argument("--logs-dir", help="default <repo>/agents/logs")
    ap.add_argument("--out", help="per-unit taxonomy JSON")
    ap.add_argument("--k-share", type=float, default=0.3)
    ap.add_argument("--errors", action="store_true", help="print each transcript's first errors")
    a = ap.parse_args(argv)
    repo = os.path.abspath(a.repo)
    tasks = T.load_task_texts(repo)
    logs = a.logs_dir or os.path.join(repo, "agents", "logs")
    units = []
    for spec in a.runs:
        label, _, files = spec.partition("=")
        if not files:
            ap.error(f"{spec}: expected LABEL=results.json")
        units += run_label(label, [os.path.abspath(f) for f in files.split(",")], repo, logs, tasks)
    text, verdict = summary(units, a.k_share)
    if a.errors:
        for u in units:
            print(f"### {u['run']}:{u['unit']} ({u['lang']}) end={u['end']} iters={u['iterations']} "
                  f"{os.path.basename(u['transcript'] or '-')}")
            for k, its in u["errors"]:
                print(f"   it{its[:8]} {k}")
        print()
    print(text)
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"units": units, "verdict": verdict}, f, indent=1)
        print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
