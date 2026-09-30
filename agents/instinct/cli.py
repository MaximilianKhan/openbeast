"""Offline helpers behind scripts/instinct.sh: config, stats, label, promote, demote.

    python3 -m instinct.cli cfg                      # shell-safe KEY=VALUE lines
    python3 -m instinct.cli stats [--decision D] [--since 7d]
    python3 -m instinct.cli label D [--n 20]         # interactive labelling TUI
    python3 -m instinct.cli promote D --engine E     # CHECKS only, never edits
    python3 -m instinct.cli demote D [--reason R]    # runtime override (+ SIGHUP by caller)
    python3 -m instinct.cli undemote D

Promotion needs (plan §5.7): a calibration record and a gate record for the
exact decision_hash — the gate computed against THAT calibration, passed,
committed; the gate.shadow soak (min_decisions ledger rows scored by this
engine+hash over at least min_days); a conformance probe that passes; no
demotion; the spec edited to mode="enforce"; and a SIGHUP/restart. `promote`
applies the SAME rules the service does (lifecycle.gate_record_ok) and prints
the diff — it never edits a file. `demote` needs no commit: going DOWN is
always allowed, but it refuses a decision id the registry does not know
(a typo in an emergency must not report success); --force overrides.
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
from .lifecycle import _read_map, gate_record_ok, git_committed


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
    st["_demotions"] = _read_map(Path(cfg.state_dir) / "demoted.json")
    # auto-demotions persist too (the service writes them): show them
    st["_auto_demotions"] = _read_map(Path(cfg.state_dir) / "auto-demoted.json")
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


def shadow_soak(cfg, decision: str, engine: str, h16: str | None) -> tuple[int, float]:
    """(rows, days spanned) of ledger rows in which `engine` actually SCORED
    this decision under hash `h16` — the gate.shadow soak a promotion needs."""
    ts = []
    for r in read_rows(cfg.ledger_dir, 0.0, decision):
        if r.get("kind", "decide") not in ("decide", "route"):
            continue
        for c in r.get("cascade") or []:
            if (c.get("engine") == engine and c.get("hash") == h16
                    and c.get("action") not in ("skipped", "fallback")):
                if isinstance(r.get("ts"), (int, float)):
                    ts.append(float(r["ts"]))
                break
    return len(ts), ((max(ts) - min(ts)) / 86400.0 if ts else 0.0)


async def _promote_checks(cfg, decision: str, engine: str, repo: Path) -> tuple[bool, list]:
    from .service import Instinct
    inst = Instinct(cfg, repo_root=repo)
    try:
        await inst.reload()
        spec = inst.specs.get(decision)
        if spec is None:
            return False, [("decision loads", False, inst.spec_errors.get(decision, "unknown"))]
        checks = []
        h = inst.hashes.get((decision, engine))
        checks.append(("engine in chain + label lock", h is not None,
                       f"hash {h[:16] if h else None}"))
        crec = inst.calib.get((decision, engine))
        cpath = C.calib_path(cfg.records_dir, decision, h) if h else None
        checks.append(("calibration record for this hash", crec is not None, str(cpath)))
        gpath = C.gate_path(cfg.records_dir, decision, h) if h else None
        grec = C.load_record(gpath, h) if h else None
        # The service's own rule: passed, this hash, computed against THIS
        # calibration (a recalibration after the gate voids it).
        checks.append(("gate record passed, for this hash AND this calibration",
                       bool(h and gate_record_ok(grec, h, crec)), str(gpath)))
        checks.append(("gate record committed", bool(gpath and git_committed(gpath, repo)),
                       "git ls-files + clean"))
        need_n, need_d = spec.gate.shadow_min_decisions, spec.gate.shadow_min_days
        n, days = shadow_soak(cfg, decision, engine, h[:16] if h else None)
        checks.append((f"shadow soak >= {need_n} decisions over >= {need_d} days",
                       n >= need_n and days >= need_d, f"{n} decisions over {days:.1f} days"))
        await inst.probe_all()
        st = inst.states.get(engine)
        probe = st.probe if st else None
        checks.append(("engine conformance probe passes",
                       bool(st and (st.healthy if probe is None else probe.ok)),
                       (probe.reason or "ok") if probe else "in-process"))
        dem = inst.demotions.reason(decision)
        checks.append(("not demoted", dem is None, dem or "-"))
        checks.append(("spec mode == enforce", spec.policy.mode == "enforce", spec.policy.mode))
        return all(ok for _, ok, _ in checks), checks
    finally:
        await inst.aclose()


def promote_check(cfg, decision: str, engine: str, repo: Path = REPO_ROOT) -> tuple[bool, list]:
    """(ok, [(check, ok, detail)]). Pure checks; edits nothing. ONE event loop
    for reload, probe and close: an engine's HTTP client is bound to the loop
    that made it (two asyncio.run calls crashed on any live LLM engine)."""
    return asyncio.run(_promote_checks(cfg, decision, engine, Path(repo)))


def spec_diff(cfg, decision: str, repo: Path = REPO_ROOT) -> str:
    p = Path(cfg.decisions_dir) / f"{decision}.toml"
    return subprocess.run(["git", "-C", str(repo), "diff", "HEAD", "--", str(p)],
                          capture_output=True, text=True).stdout.strip()


def cmd_promote(a) -> int:
    cfg = load_config(a.config)
    ok, checks = promote_check(cfg, a.decision, a.engine)
    for name, good, detail in checks:
        print(f"{'OK  ' if good else 'FAIL'} {name}: {detail}")
    print("spec diff (review it in the PR):\n" + (spec_diff(cfg, a.decision) or "(committed)"))
    print("promotion READY: commit, then SIGHUP/restart the service" if ok
          else "promotion NOT ready — nothing was changed")
    return 0 if ok else 1


def known_decisions(cfg) -> set[str]:
    """Every decision id the registry names — valid or not (a broken spec can
    still be demoted: it is the one you are likely firefighting)."""
    from .spec import load_registry
    specs, errors = load_registry(cfg.decisions_dir)
    return set(specs) | {k for k in errors if "#" not in k and not k.startswith("<")}


def cmd_demote(a, undo: bool = False) -> int:
    cfg = load_config(a.config)
    if not undo and not getattr(a, "force", False) and a.decision not in known_decisions(cfg):
        print(f"instinct: unknown decision {a.decision!r} — nothing demoted "
              f"(known: {', '.join(sorted(known_decisions(cfg))) or 'none'}; --force to "
              "record it anyway)", file=sys.stderr)
        return 2
    if undo:
        # the service re-reads this on SIGHUP; it is the only other writer
        auto = Path(cfg.state_dir) / "auto-demoted.json"
        data = _read_map(auto)
        if data.pop(a.decision, None) is not None:
            tmp = auto.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, auto)
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
    s.add_argument("--force", action="store_true", help="record an id the registry lacks")
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
