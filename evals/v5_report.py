#!/usr/bin/env python3
"""
The tables of a v5 campaign report, generated from result files.

A campaign report (docs/reports/) compares the arms of one model. Its numbers
come from here, not from memory: every committed board run of that model
(evals/boards/v5/board.json, scored exactly as evals/v5_board.py scores it,
exclusions included), plus any arm not on the board yet, given as files.

Per arm: its exact weights (the board run's weights.json entry), passed, SCORE / SOLVE / LANG, the seven languages, unit-hours, mean
minutes per unit, wall hits (rows the harness killed at the wall,
`agent_exit_code == -1`) and how many of those passed on the code left at the
kill, and the turn range of the wall-hit units. Per pair of arms: the units
gained and lost, by unit id. Last, every unit whose verdict differs across
the arms.

An arm whose file does not yet hold all 71 units is "in progress": it gets
"N of M done" and no SCORE (a partial run's missing units are not fails).

Unit ids and pass/fail only: this script prints nothing from a unit's task
text, validator output or solution, so its output may go into docs/.

Usage:
  python3 evals/v5_report.py                               # DeepSeek-V4.1-Flash (Mia EXL3), board runs
  python3 evals/v5_report.py --arm U=evals/results/eval-...-u-<ts>.json \\
      --arm UP=evals/results/eval-...-up-<ts>.json        # + arms not on the board yet
  python3 evals/v5_report.py --model "GLM-5.3-Flash" --pair A:B
  python3 evals/v5_report.py --json
  python3 evals/v5_report.py --weights-id dsv41f-mia-exl3-2.9bpw-eaq1.1-v2   # one quantization
  python3 evals/v5_report.py --model "Hy4-flash (NVFP4)" --context DS-T2,GLM-C,OPUS-REF \\
      --pair DS-T2:H_T2                                   # a context run by its board id
  python3 evals/v5_report.py --arm ... --write docs/reports/<report>.md
      # replace the block between <!-- v5-report:start --> and <!-- v5-report:end -->

It reads only; with --write it rewrites that one block of that one file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, EVALS_DIR)

import campaign  # noqa: E402
import v5_board  # noqa: E402

DEFAULT_MODEL = "DeepSeek-V4.1-Flash (Mia EXL3)"
# Context rows for the headline table (board run ids): the other model's best
# and the reference ceiling.
DEFAULT_CONTEXT = ("GLM-C", "OPUS-REF")
# Pairs read in the campaign (base:arm); a pair is shown when both arms exist.
DEFAULT_PAIRS = ("T:T_s1", "T:T2", "T2:T3", "T2:U", "U:UP", "UP:UPA",
                 "U:U_8k", "U_8k:UP_8k", "UP_8k:UPA_8k", "T2:UP_8k")


def arm_label(run: dict) -> str:
    """The campaign arm's short name ("v5-thinking-first T2" -> "T2"), else the run id."""
    arm = run.get("campaign_arm")
    return arm.split()[-1] if arm else run["id"]


def rows_of(run: dict, base_dir: str) -> dict:
    """{unit: row} as the board merges them (repairs fill infra rows only)."""
    paths = [os.path.join(base_dir, f) for f in run["files"]]
    if run.get("reference"):
        return v5_board.load_reference(paths[0])[1]
    return v5_board.merge_runs(paths)[1]


def process_stats(rows: dict, manifest: dict) -> dict:
    """Unit-hours, wall hits and the turn range of the wall-hit units."""
    secs, hits, hit_pass, turns = 0.0, 0, 0, []
    done = 0
    for uid in manifest:
        r = rows.get(uid)
        if r is None:
            continue
        done += 1
        if isinstance(r.get("elapsed_seconds"), (int, float)):
            secs += float(r["elapsed_seconds"])
        if r.get("agent_exit_code") == -1:
            hits += 1
            hit_pass += bool(r.get("passed"))
            if isinstance(r.get("iterations"), int):
                turns.append(r["iterations"])
    return {"done": done, "unit_hours": round(secs / 3600, 2),
            "mean_min": round(secs / 60 / done, 1) if done else None,
            "wall_hits": hits, "wall_hit_passed": hit_pass,
            "wall_hit_turns": [min(turns), max(turns)] if turns else None}


def arm_result(run: dict, manifest: dict, base_dir: str, exclusions: dict,
               weights: dict | None = None) -> dict:
    s = v5_board.score_run(run, manifest, base_dir, exclusions)
    rows = rows_of(run, base_dir)
    p = process_stats(rows, manifest)
    verdicts = {}
    for uid in manifest:
        r = rows.get(uid)
        if r is None:
            continue
        lost = (not run.get("reference")) and v5_board.infra_reason(r)
        verdicts[uid] = None if lost else bool(r.get("passed")) and uid not in s["excluded"]
    header = ({} if run.get("reference")
              else campaign.load_rows(os.path.join(base_dir, run["files"][0]))[0])
    harness = header.get("harness") or {}
    # The per-turn cap: the agent switch (harness9 on), else the reasoning
    # budget the run declared (arm T declared the runner's 16384).
    cap = ((harness.get("agent_switches") or {}).get("turn_cap")
           or int((header.get("server") or {}).get("reasoning_budget") or 0) or None)
    return {
        "label": run.get("label") or arm_label(run), "id": run["id"], "model": run["model"],
        "config": run.get("config", ""), "reference": bool(run.get("reference")),
        "on_board": bool(run.get("on_board", True)),
        "weights_id": run.get("weights_id"),
        "weights": ((weights or {}).get(run.get("weights_id")) or {}).get("label"),
        "era": harness.get("era"), "turn_cap": cap,
        "complete": p["done"] == len(manifest), "done": p["done"], "units": len(manifest),
        "passed": s["passed"], "passed_raw": s["passed_raw"],
        "score": s["score"], "solve": s["solve"], "lang": s["lang"],
        "by_lang": s["by_lang"], "excluded": sorted(s["excluded"]), "lost": s["lost"],
        **{k: p[k] for k in ("unit_hours", "mean_min", "wall_hits", "wall_hit_passed",
                             "wall_hit_turns")},
        "verdicts": verdicts,
    }


def pair_delta(a: dict, b: dict) -> dict:
    """Units b passed that a failed (gained) and the reverse (lost), over the
    units both arms have a verdict for."""
    both = [u for u in a["verdicts"] if u in b["verdicts"]
            and a["verdicts"][u] is not None and b["verdicts"][u] is not None]
    gained = sorted(u for u in both if b["verdicts"][u] and not a["verdicts"][u])
    lost = sorted(u for u in both if a["verdicts"][u] and not b["verdicts"][u])
    return {"base": a["label"], "arm": b["label"], "compared": len(both),
            "gained": gained, "lost": lost, "net": len(gained) - len(lost)}


def build(model: str = DEFAULT_MODEL, extra: list[tuple[str, list[str]]] | None = None,
          pairs: list[str] | None = None, context: tuple[str, ...] = DEFAULT_CONTEXT,
          board: dict | None = None, base_dir: str = v5_board.BOARD_DIR,
          manifest: dict | None = None, exclusions: dict | None = None, strict_pairs: bool = False,
          weights: dict | None = None, weights_id: str | None = None) -> dict:
    """weights_id keeps one quantization of the model (a report compares arms on
    the same weights; another bpw or pack is another report)."""
    board = board if board is not None else v5_board.load_board()
    weights = weights if weights is not None else v5_board.load_weights()
    manifest = manifest if manifest is not None else v5_board.load_manifest()
    exclusions = exclusions if exclusions is not None else v5_board.load_exclusions()
    runs = [r for r in board["runs"] if r["model"] == model
            and (weights_id is None or r.get("weights_id") == weights_id)]
    for label, files in extra or []:
        runs.append({"id": label, "label": label, "model": model, "on_board": False,
                     "config": "not on the board yet",
                     "files": [os.path.abspath(f) for f in files]})
    arms = [arm_result(r, manifest, base_dir, exclusions, weights) for r in runs]
    ctx = [arm_result(r, manifest, base_dir, exclusions, weights)
           for r in board["runs"] if r["id"] in context and r["model"] != model]
    # a context run (another model's) is named in a pair by its board id, so a
    # cross-model delta on one harness reads "DS-T2:H_T2" (2026-11-22, Hy4 vs DeepSeek)
    by_label = {**{c["id"]: c for c in ctx}, **{a["label"]: a for a in arms}}
    deltas = []
    for p in pairs if pairs is not None else DEFAULT_PAIRS:
        base, _, arm = p.partition(":")
        if strict_pairs and not (base in by_label and arm in by_label):
            raise SystemExit(f"--pair {p}: unknown " + ", ".join(s for s in (base, arm) if s not in by_label)
                             + f" (known: {', '.join(sorted(by_label))})")
        if base in by_label and arm in by_label:
            deltas.append({**pair_delta(by_label[base], by_label[arm]), "base": base, "arm": arm})
    differ = sorted(u for u in manifest
                    if len({a["verdicts"].get(u) for a in arms
                            if a["verdicts"].get(u) is not None}) > 1)
    return {"model": model, "units": len(manifest), "arms": arms, "context": ctx,
            "deltas": deltas, "differ": differ}


# --- rendering ------------------------------------------------------------------------

def _passed(a: dict) -> str:
    if not a["complete"]:
        return f"{a['passed']} of {a['done']} done (in progress)"
    raw = f" ({a['passed_raw']} raw)" if a["passed_raw"] != a["passed"] else ""
    return f"{a['passed']}/{a['units']}{raw}"


def _score(a: dict, key: str) -> str:
    return f"{a[key]:.1f}" if a["complete"] else "—"


def render(r: dict) -> str:
    langs = v5_board.LANGS
    out = [f"<!-- generated by evals/v5_report.py for {r['model']}: do not hand-edit -->", "",
           "**Arms**", "",
           "| Arm | Run | Weights | On the board | Era | Turn cap | Passed | SCORE | SOLVE | LANG |",
           "|---|---|---|---|---|---:|---:|---:|---:|---:|"]
    for a in r["arms"] + r["context"]:
        name = a["label"] if a["model"] == r["model"] else f"{a['model']}: {a['label']}"
        out.append(f"| {name} | {a['id']} | {a['weights'] or '—'} | {'yes' if a['on_board'] else 'not yet'} | "
                   f"{a['era'] or '—'} | {a['turn_cap'] or '—'} | {_passed(a)} | "
                   f"{_score(a, 'score')} | {_score(a, 'solve')} | {_score(a, 'lang')} |")
    out += ["", "**Per language** (passed / units of that language; an in-progress arm's passes are over the units done so far)", "",
            "| Arm | " + " | ".join(lbl for _, lbl in langs) + " |",
            "|---|" + "---:|" * len(langs)]
    for a in r["arms"] + r["context"]:
        name = a["label"] if a["model"] == r["model"] else f"{a['model']}: {a['label']}"
        out.append(f"| {name} | " + " | ".join(
            f"{a['by_lang'][k][0]}/{a['by_lang'][k][1]}" for k, _ in langs) + " |")
    out += ["", "**Time and the wall** (wall hit = the harness killed the agent at the unit's wall, "
            "`agent_exit_code == -1`; turns are counted only where the row carries them, "
            "from harness9 on)", "",
            "| Arm | Units done | Unit-hours | Mean min / unit | Wall hits | Passed at the wall | "
            "Failed at the wall | Turns of the wall-hit units |",
            "|---|---:|---:|---:|---:|---:|---:|---|"]
    for a in r["arms"]:
        t = a["wall_hit_turns"]
        out.append(f"| {a['label']} | {a['done']} | {a['unit_hours']:.1f} | {a['mean_min']} | "
                   f"{a['wall_hits']} | {a['wall_hit_passed']} | "
                   f"{a['wall_hits'] - a['wall_hit_passed']} | "
                   f"{f'{t[0]}-{t[1]}' if t else '—'} |")
    if r["deltas"]:
        out += ["", "**Units gained and lost** (arm against base, over the units both have a verdict for)", "",
                "| Base → arm | Compared | Net | Gained | Lost |", "|---|---:|---:|---|---|"]
        for d in r["deltas"]:
            out.append(f"| {d['base']} → {d['arm']} | {d['compared']} | {d['net']:+d} | "
                       f"{', '.join(d['gained']) or '—'} | {', '.join(d['lost']) or '—'} |")
    if r["differ"]:
        labels = [a["label"] for a in r["arms"]]
        out += ["", "**Units whose verdict differs across the arms** (P pass, F fail, "
                "· not run yet, L lost to infrastructure); every other unit has the same "
                "verdict in every arm", "",
                "| Unit | " + " | ".join(labels) + " |", "|---|" + ":---:|" * len(labels)]
        for u in r["differ"]:
            cells = []
            for a in r["arms"]:
                v = a["verdicts"].get(u, "missing")
                cells.append("·" if v == "missing" else "L" if v is None else "P" if v else "F")
            out.append(f"| {u} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="the board model (default: %(default)s)")
    ap.add_argument("--arm", action="append", default=[], metavar="LABEL=FILE[,FILE]",
                    help="an arm not on the board yet: its label and result file(s)")
    ap.add_argument("--pair", action="append", metavar="BASE:ARM",
                    help="a delta to show (default: the campaign's pairs that exist)")
    ap.add_argument("--context", default=",".join(DEFAULT_CONTEXT),
                    help="board run ids of other models for the headline table (default: %(default)s)")
    ap.add_argument("--weights-id", help="only the arms on this weights.json entry (one quantization)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", metavar="DOC",
                    help="splice the tables into DOC between the v5-report markers")
    a = ap.parse_args()
    extra = []
    for spec in a.arm:
        label, eq, files = spec.partition("=")
        if not eq or not files:
            ap.error(f"--arm wants LABEL=FILE[,FILE], got {spec!r}")
        extra.append((label, files.split(",")))
    # a --pair typed on the command line must resolve; the campaign's defaults skip what has not run
    r = build(a.model, extra, a.pair, tuple(c for c in a.context.split(",") if c),
              weights_id=a.weights_id, strict_pairs=bool(a.pair))
    if not r["arms"]:
        print(f"no board runs for model {a.model!r}", file=sys.stderr)
        return 1
    if a.write:
        return write_block(a.write, render(r))
    print(json.dumps(r, indent=2) if a.json else render(r))
    return 0


def write_block(path: str, body: str) -> int:
    """Replace the v5-report block of a doc; refuse a doc without one."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = v5_board.splice(text, "v5-report", body)
    if new is None:
        print(f"{path}: no <!-- v5-report:start --> / <!-- v5-report:end --> block", file=sys.stderr)
        return 1
    if new != text:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
