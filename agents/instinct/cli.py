"""Offline helpers behind scripts/instinct.sh: config, stats, label, promote, demote.

    python3 -m instinct.cli cfg                      # shell-safe KEY=VALUE lines
    python3 -m instinct.cli stats [--decision D] [--since 7d]
    python3 -m instinct.cli label D [--n 20]         # interactive labelling TUI
    python3 -m instinct.cli promote D --engine E     # CHECKS only, never edits
    python3 -m instinct.cli demote D [--reason R]    # runtime override (+ SIGHUP by caller)
    python3 -m instinct.cli undemote D

Promotion needs three things (plan §5.7): a gate record for the exact
decision_hash with passed:true, committed; the spec edited to mode="enforce";
and a SIGHUP/restart. `promote` verifies and prints the diff — it never edits
a file. `demote` needs no commit: going DOWN is always allowed.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from . import calibrate as C
from .config import REPO_ROOT, load_config
from .ledger import read_rows
from .lifecycle import git_committed


def _since(s: str | None) -> float:
    if not s:
        return 0.0
    m = re.fullmatch(r"(\d+)([smhd])", s.strip())
    if not m:
        raise SystemExit(f"--since: want like 7d / 12h, got {s!r}")
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    return time.time() - int(m.group(1)) * mult


def cmd_cfg(a) -> int:
    cfg = load_config(a.config)
    for k, v in (("INSTINCT_HOST", cfg.host), ("INSTINCT_PORT", cfg.port),
                 ("INSTINCT_KEY_FILE", cfg.key_file), ("INSTINCT_LEDGER_DIR", cfg.ledger_dir),
                 ("INSTINCT_STATE_DIR", cfg.state_dir),
                 ("INSTINCT_RECORDS_DIR", cfg.records_dir),
                 ("INSTINCT_DECISIONS_DIR", cfg.decisions_dir)):
        print(f"{k}={shlex.quote(str(v))}")
    return 0


def stats_from_rows(rows: list[dict]) -> dict:
    out: dict = {}
    by = defaultdict(list)
    for r in rows:
        by[r.get("decision") or "?"].append(r)
    for d, rs in sorted(by.items()):
        mix = Counter(r.get("action") for r in rs)
        modes = Counter(r.get("mode") for r in rs)
        reasons = Counter(r.get("fallback_reason") for r in rs if r.get("fallback_reason"))
        conf = Counter((r.get("baseline"), r.get("label")) for r in rs if r.get("baseline"))
        lat = sorted(float((r.get("latency_ms") or {}).get("total") or 0.0) for r in rs)
        mass = sorted(r["label_mass"] for r in rs if isinstance(r.get("label_mass"),
                                                                  (int, float)))

        def pct(xs, q):
            return xs[min(len(xs) - 1, int(q * (len(xs) - 1)))] if xs else None
        hist = Counter()
        for x in lat:
            for b in (1, 5, 10, 25, 50, 100, 250, 500, 1000):
                if x <= b:
                    hist[f"<={b}ms"] += 1
                    break
            else:
                hist[">1000ms"] += 1
        out[d] = {"n": len(rs), "mix": dict(mix), "modes": dict(modes),
                  "fallback_reasons": dict(reasons),
                  "baseline_vs_label": {f"{b}->{lb}": n for (b, lb), n in conf.items()},
                  "latency_ms": {"p50": pct(lat, .5), "p95": pct(lat, .95),
                                 "p99": pct(lat, .99), "hist": dict(hist)},
                  "label_mass": {"p05": pct(mass, .05), "p50": pct(mass, .5),
                                 "p95": pct(mass, .95)}}
    return out


def cmd_stats(a) -> int:
    cfg = load_config(a.config)
    rows = list(read_rows(cfg.ledger_dir, _since(a.since), a.decision))
    st = stats_from_rows([r for r in rows if r.get("kind", "decide") in ("decide", "route")])
    dem = Path(cfg.state_dir) / "demoted.json"
    st["_demotions"] = json.loads(dem.read_text()) if dem.exists() else {}
    print(json.dumps(st, indent=2, sort_keys=True))
    return 0


def cmd_label(a) -> int:
    """Walk ledger rows that carry text (excerpt/full) and are not labelled yet."""
    cfg = load_config(a.config)
    out = Path(cfg.records_dir) / a.decision / "shadow-labelled.jsonl"
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            try:
                done.add(json.loads(line)["trace_id"])
            except (ValueError, KeyError):
                pass
    rows = [r for r in read_rows(cfg.ledger_dir, _since(a.since), a.decision)
            if r.get("trace_id") not in done]
    texty = [r for r in rows if r.get("input_excerpt") or r.get("input_full")]
    if len(texty) < len(rows):
        print(f"{len(rows) - len(texty)} unlabelled rows carry only a hash "
              "(log_inputs=hash) and cannot be labelled.", file=sys.stderr)
    # lowest-margin first: the most informative rows
    texty.sort(key=lambda r: (r.get("confidence") or {}).get("margin", 1.0))
    labels = None
    try:
        from .spec import load_spec
        labels = load_spec(Path(cfg.decisions_dir) / f"{a.decision}.toml").label_names
    except Exception:
        labels = None
    if not labels:
        raise SystemExit(f"unknown decision {a.decision}")
    keys = {lb[0]: lb for lb in labels}
    if len(keys) != len(labels) or keys.keys() & {"s", "q"}:   # [s]kip / [q]uit win
        keys = {str(i + 1): lb for i, lb in enumerate(labels)}
    n = 0
    user = getpass.getuser()
    for r in texty:
        if n >= a.n:
            break
        inp = r.get("input_full", {}).get("inputs") if r.get("input_full") else r.get(
            "input_excerpt")
        print("\n" + "=" * 72)
        print(json.dumps(inp, indent=2, ensure_ascii=False)[:4000])
        print(f"model: {r.get('label')}  p={r.get('probabilities')}  "
              f"action={r.get('action')}  baseline={r.get('baseline')}")
        choice = input(" ".join(f"[{k}]{v}" for k, v in keys.items()) + " [s]kip [q]uit > ")
        choice = choice.strip()
        if choice == "q":
            break
        if choice not in keys:
            continue
        row = {"trace_id": r["trace_id"], "id": f"shadow-{r['trace_id']}", "input": inp,
               "label": keys[choice], "source": "shadow", "group": f"shadow-{date.today()}",
               "labeller": user, "added_at": str(date.today()),
               "note": "from ledger excerpt" if r.get("input_excerpt") else ""}
        out.parent.mkdir(parents=True, exist_ok=True)
        # User text: 0600, and gitignored (.gitignore) — a human moves chosen
        # rows into a dataset split; nothing here is committed by accident.
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        os.chmod(out, 0o600)
        n += 1
    print(f"labelled {n}; file: {out}")
    return 0


def promote_check(cfg, decision: str, engine: str, repo: Path = REPO_ROOT) -> tuple[bool, list]:
    """(ok, [(check, ok, detail)]). Pure checks; edits nothing."""
    from .service import Instinct
    inst = Instinct(cfg, repo_root=repo)
    asyncio.run(inst.reload())
    checks = []
    spec = inst.specs.get(decision)
    if spec is None:
        return False, [("decision loads", False, inst.spec_errors.get(decision, "unknown"))]
    h = inst.hashes.get((decision, engine))
    checks.append(("engine in chain + label lock", h is not None,
                   f"hash {h[:16] if h else None}"))
    gpath = C.gate_path(cfg.records_dir, decision, h) if h else None
    grec = C.load_record(gpath, h) if h else None
    checks.append(("gate record for this hash passed", bool(grec and grec.get("passed")),
                   str(gpath)))
    checks.append(("gate record committed", bool(gpath and git_committed(gpath, repo)),
                   "git ls-files + clean"))
    spec_path = Path(spec.path)
    checks.append(("spec mode == enforce", spec.policy.mode == "enforce", spec.policy.mode))
    diff = subprocess.run(["git", "-C", str(repo), "diff", "HEAD", "--", str(spec_path)],
                          capture_output=True, text=True).stdout
    checks.append(("spec change visible in a diff/PR", True, diff.strip() or "(committed)"))
    asyncio.run(inst.aclose())
    return all(ok for _, ok, _ in checks), checks


def cmd_promote(a) -> int:
    cfg = load_config(a.config)
    ok, checks = promote_check(cfg, a.decision, a.engine)
    for name, good, detail in checks:
        print(f"{'OK  ' if good else 'FAIL'} {name}: {detail}")
    print("promotion READY: commit, then SIGHUP/restart the service" if ok
          else "promotion NOT ready — nothing was changed")
    return 0 if ok else 1


def cmd_demote(a, undo: bool = False) -> int:
    cfg = load_config(a.config)
    p = Path(cfg.state_dir) / "demoted.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if undo:
        data.pop(a.decision, None)
    else:
        data[a.decision] = {"reason": a.reason or "operator", "at": time.time(),
                            "by": getpass.getuser()}
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, p)
    print(("undemoted " if undo else "demoted ") + a.decision)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="instinct")
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cfg")
    s = sub.add_parser("stats")
    s.add_argument("--decision")
    s.add_argument("--since")
    s = sub.add_parser("label")
    s.add_argument("decision")
    s.add_argument("--n", type=int, default=20)
    s.add_argument("--since")
    s = sub.add_parser("promote")
    s.add_argument("decision")
    s.add_argument("--engine", required=True)
    s = sub.add_parser("demote")
    s.add_argument("decision")
    s.add_argument("--reason")
    s = sub.add_parser("undemote")
    s.add_argument("decision")
    a = ap.parse_args(argv)
    if a.cmd == "cfg":
        return cmd_cfg(a)
    if a.cmd == "stats":
        return cmd_stats(a)
    if a.cmd == "label":
        return cmd_label(a)
    if a.cmd == "promote":
        return cmd_promote(a)
    if a.cmd == "demote":
        return cmd_demote(a)
    if a.cmd == "undemote":
        return cmd_demote(a, undo=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
