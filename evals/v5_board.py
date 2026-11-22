#!/usr/bin/env python3
"""
The v5 leaderboard: each model's BEST configuration on the v5.0 suite.

Inputs are committed, not the live evals/results/ (which is not tracked):
evals/boards/v5/board.json lists every measured configuration (a model, a
short config label, and its result files: a main run plus any repair runs),
and the files themselves sit next to it as verbatim copies. A reference
ledger (evals/reference_attempt.py grade rows, .jsonl) is a run too, marked
"reference": it is shown as a ceiling, never ranked.

Rows are merged the way evals/campaign.py defines a lost unit: a later file's
row for the same unit supersedes an earlier row only when the earlier one is
missing or an infrastructure row (campaign.infra_reason: the eval interpreter
died, a validator timeout, a server error...; plus a validator whose own
interpreter failed to start). A repair run therefore fills
in lost units and never re-rolls a real verdict. Units still lost after the
merge count as not passed (out of the manifest's 71).

SCORE is the v5 capability (evals/README.md "Scoring (v5)"):
0.75 x SOLVE + 0.25 x LANG, LANG over multi-language bases only, computed by
scoring.compute_solve_breadth on rows enriched from the suite manifest.

WEIGHTS. Every run names its exact weights with "weights_id", an entry of
evals/boards/v5/weights.json (repo + revision, format, bpw, variant, stock or
abliterated, engine, drafter, KV cache, hosts and the evidence). A board row is
a model AND its exact weights: the best configuration is chosen per (model,
weights), so a second quantization of a model (another bpw, another pack) is
its own row and never replaces or merges with the first.

The board is generated between markers in README.md and docs/RESULTS.md
(<!-- v5-board:start --> / <!-- v5-board:end -->); docs/RESULTS.md also holds
every run of every model (<!-- v5-runs:start --> / <!-- v5-runs:end -->) and
the weights behind them (<!-- v5-weights:start --> / <!-- v5-weights:end -->).

Usage:
  python3 evals/v5_board.py              # print the board (markdown)
  python3 evals/v5_board.py --json       # per-model and per-run numbers
  python3 evals/v5_board.py --write      # regenerate the blocks in the docs
  python3 evals/v5_board.py --check      # exit 1 if a doc's block is stale (CI)

  # a new configuration (e.g. a campaign arm that just finished): copies the
  # files into evals/boards/v5/, records the run in board.json, rewrites docs
  # --weights-id names its weights.json entry (add the entry first for new weights)
  python3 evals/v5_board.py --add GLM-E --model GLM-5.3-Flash \\
      --weights-id glm53f-mia-exl3-4bpw-tf-ablit \\
      --config "thinking on, runner v3, ..." [--campaign-arm "v5-harness E"] \\
      evals/results/eval-<slug>-<ts>.json [repair files...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(EVALS_DIR)
sys.path.insert(0, EVALS_DIR)

import campaign  # noqa: E402  (infra_reason / load_rows / load_ledger: one definition of "lost")
import scoring   # noqa: E402  (compute_solve_breadth, ineligibility_reasons)

BOARD_DIR = os.path.join(EVALS_DIR, "boards", "v5")
BOARD_FILE = os.path.join(BOARD_DIR, "board.json")
# Units withheld from a run's score (an integrity review, a contaminated
# run) and validator notes shown without adjusting anything. Data, not code:
# an integrity scan's verdicts are applied by editing this file + --write.
EXCLUSIONS_FILE = os.path.join(BOARD_DIR, "exclusions.json")
# The exact weights behind each run (board.json "weights_id" -> an entry here).
WEIGHTS_FILE = os.path.join(BOARD_DIR, "weights.json")
MANIFEST = os.path.join(EVALS_DIR, "suites", "v5.json")

# Docs that hold a generated block, and the path prefix from that doc to the
# repo root (for links to the result files).
DOCS = {"README.md": "", os.path.join("docs", "RESULTS.md"): "../"}
BLOCKS = {"v5-board": "board", "v5-runs": "runs", "v5-weights": "weights"}
# Where a weights cell links when the weights have no Hub revision (the
# generated "Weights used" block), relative to the repo root.
WEIGHTS_ANCHOR = "docs/RESULTS.md#weights-used"

LANGS = (("python", "Py"), ("go", "Go"), ("c", "C"), ("cpp", "C++"),
         ("rust", "Rust"), ("zig", "Zig"), ("shell", "Sh"))


# --- inputs ---------------------------------------------------------------------------

def load_manifest(path: str = MANIFEST) -> dict:
    """{unit id: {base, lang, difficulty, k}} for the scored units of v5."""
    with open(path) as f:
        man = json.load(f)
    units = [u for u in man["units"] if u.get("role", "scored") == "scored"]
    k = Counter(u["task"] for u in units)
    # run_eval records the one xl unit as "hard"; the scorer's weights have no xl.
    return {u["id"]: {"base": f"task-{u['task']}", "lang": u["lang"],
                      "difficulty": "hard" if u["tier"] == "xl" else u["tier"],
                      "k": k[u["task"]]} for u in units}


def load_board(path: str = BOARD_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --- weights ---------------------------------------------------------------------------

WEIGHTS_REQUIRED = ("model", "label", "repo", "revision", "revision_kind", "format", "bpw",
                    "variant", "abliterated", "engine", "engine_version", "drafter",
                    "kv_dtype", "hosts", "evidence")
REVISION_KINDS = ("hub", "local", "hosted", "file-sha256")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def load_weights(path: str = WEIGHTS_FILE) -> dict:
    """{weights id: entry}; an absent file is an empty registry (every run then fails
    validation, which names the missing file's entries)."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("weights") or {}


def weights_problems(board: dict, weights: dict) -> list[str]:
    """Why the registry or a run's weights_id is not usable ([] = fine)."""
    bad = []
    for wid, w in weights.items():
        missing = [k for k in WEIGHTS_REQUIRED if k not in w]
        if missing:
            bad.append(f"weights {wid}: missing {', '.join(missing)}")
            continue
        kind = w["revision_kind"]
        if kind not in REVISION_KINDS:
            bad.append(f"weights {wid}: revision_kind {kind!r} is not one of {', '.join(REVISION_KINDS)}")
        if kind == "hub" and not (isinstance(w["repo"], str) and "/" in w["repo"]
                                  and _SHA.match(str(w["revision"]))):
            bad.append(f"weights {wid}: a hub entry needs a repo id and a full 40-hex revision")
        if kind == "local" and not (w.get("local_id") and w["repo"] is None
                                    and _SHA.match(str(w["revision"]))):
            bad.append(f"weights {wid}: a local pack needs repo null, a local_id and its 40-hex local revision")
        if kind == "file-sha256" and not re.match(r"^[0-9a-f]{64}$", str(w.get("file_sha256"))):
            bad.append(f"weights {wid}: a file-pinned entry needs file and file_sha256")
        if not isinstance(w["abliterated"], bool):
            bad.append(f"weights {wid}: abliterated must be true or false")
        if w["bpw"] is not None and not isinstance(w["bpw"], (int, float)):
            bad.append(f"weights {wid}: bpw must be a number or null")
    labels: dict = {}
    for wid, w in weights.items():
        key = (w.get("model"), w.get("label"))
        if key in labels:
            bad.append(f"weights {wid} and {labels[key]}: one model, one label: two variants need two labels")
        labels[key] = wid
    for r in board.get("runs", []):
        wid = r.get("weights_id")
        if not wid:
            bad.append(f"run {r['id']}: no weights_id (name its weights.json entry)")
        elif wid not in weights:
            bad.append(f"run {r['id']}: weights_id {wid!r} is not in weights.json")
        elif weights[wid].get("model") != r["model"]:
            bad.append(f"run {r['id']}: weights {wid} belong to {weights[wid].get('model')!r}, not {r['model']!r}")
    return bad


def hub_url(w: dict) -> str | None:
    """The Hub page of the exact revision, for weights that are on the Hub."""
    if w.get("revision_kind") == "hub":
        return f"https://huggingface.co/{w['repo']}/tree/{w['revision']}"
    return None


def infra_reason(row: dict) -> str | None:
    """campaign.infra_reason, plus one case it misses: a failed row whose
    VALIDATOR could not start its interpreter ("Could not find platform
    independent libraries" in validation_output) after the agent ran. The
    agent's work was never judged, so the unit is lost, not failed (2026-11-16,
    arm B: 36_black_scholes_e and 173_taskgroup_cancel)."""
    why = campaign.infra_reason(row)
    if why or row.get("passed"):
        return why
    if campaign.INTERPRETER_STARTUP.search(row.get("validation_output") or ""):
        return "validator_interpreter"
    return None


def load_exclusions(path: str = EXCLUSIONS_FILE) -> dict:
    """{"exclusions": [{unit, run | file, reason}], "validator_notes":
    [{unit, run | file, note}]}; an absent file excludes nothing."""
    if not os.path.exists(path):
        return {"exclusions": [], "validator_notes": []}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return {"exclusions": d.get("exclusions") or [],
            "validator_notes": d.get("validator_notes") or []}


def applies(entry: dict, run: dict) -> bool:
    """An entry names its run by board.json id ("run") or by one of its
    files ("file"); an entry naming neither applies to every run."""
    if entry.get("run") is not None and entry["run"] != run["id"]:
        return False
    if entry.get("file") is not None and entry["file"] not in run["files"]:
        return False
    return True


def merge_runs(paths: list[str]) -> tuple[dict, dict]:
    """(header of the first file, {unit: row}) over a main run and its repair
    runs: a later row replaces an earlier one only if the earlier is an
    infrastructure row (or the unit was missing)."""
    header: dict = {}
    merged: dict = {}
    for p in paths:
        h, rows = campaign.load_rows(p)
        header = header or h
        for uid, r in rows.items():
            old = merged.get(uid)
            if old is None or (infra_reason(old) and not infra_reason(r)):
                merged[uid] = r
    return header, merged


def load_reference(path: str) -> tuple[dict, dict]:
    """A reference ledger as (header, {unit: row}) in run_eval's row shape."""
    led = campaign.load_ledger(path)
    rows = {u: {"id": u, "passed": bool(r.get("passed")) and not r.get("indeterminate"),
                "elapsed_seconds": r.get("elapsed_s"),
                "over_budget": bool(r.get("over_budget"))} for u, r in led.items()}
    graded = [r.get("graded_at") for r in led.values() if r.get("graded_at")]
    ts = (datetime.fromtimestamp(max(graded), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
          if graded else None)
    return {"timestamp": ts, "model": next(iter(led.values()), {}).get("model")}, rows


# --- scoring --------------------------------------------------------------------------

def experiment_reasons(header: dict, rows: dict) -> list[str]:
    """Why this configuration is an experiment arm rather than the v5 board
    protocol (L0 bare, the manifest's own budget). [] = on protocol."""
    reasons = scoring.ineligibility_reasons({**header, "tasks": list(rows.values())})
    if header.get("turns_per_wall_s"):
        reasons.append("wall-scaled turns")
    ws = header.get("wall_scale")
    if ws not in (None, 1, 1.0):
        reasons.append(f"wall ×{ws:g}")
    return reasons


def score_run(run: dict, manifest: dict, base_dir: str = BOARD_DIR,
              exclusions: dict | None = None) -> dict:
    """A run's numbers. An excluded unit counts as NOT passed (the
    conservative figure); `passed_raw` keeps the unadjusted count."""
    exclusions = exclusions or {}
    excluded = {e["unit"]: e.get("reason", "excluded")
                for e in exclusions.get("exclusions", []) if applies(e, run)}
    vnotes = {e["unit"]: e.get("note", "") for e in exclusions.get("validator_notes", [])
              if applies(e, run)}
    paths = [os.path.join(base_dir, f) for f in run["files"]]
    if run.get("reference"):
        header, rows = load_reference(paths[0])
    else:
        header, rows = merge_runs(paths)
    scored, lost, secs, raw = [], {}, [], 0
    by_lang = {lang: [0, 0] for lang, _ in LANGS}
    for uid, meta in manifest.items():
        r = rows.get(uid)
        why = "missing" if r is None else (None if run.get("reference")
                                           else infra_reason(r))
        passed = bool(r and not why and r.get("passed"))
        raw += passed
        if uid in excluded:
            passed = False
        if why:
            lost[uid] = why
        elif isinstance(r.get("elapsed_seconds"), (int, float)):
            secs.append(float(r["elapsed_seconds"]))
        by_lang[meta["lang"]][1] += 1
        by_lang[meta["lang"]][0] += passed
        scored.append({"id": uid, "base_id": meta["base"], "variant_count": meta["k"],
                       "difficulty": meta["difficulty"], "passed": passed})
    solve, lang, cap = scoring.compute_solve_breadth(scored, lang_multi_only=True)
    ts = header.get("timestamp") or ""
    out = {
        "id": run["id"], "model": run["model"], "config": run["config"],
        "host": run.get("host"), "campaign_arm": run.get("campaign_arm"),
        "reference": bool(run.get("reference")), "note": run.get("note"),
        "files": run["files"], "timestamp": ts, "date": ts[:10],
        "passed": sum(1 for t in scored if t["passed"]), "units": len(scored),
        "solve": solve, "lang": lang, "score": cap,
        "by_lang": {k: tuple(v) for k, v in by_lang.items()},
        "mean_s": round(sum(secs) / len(secs), 1) if secs else None,
        "lost": lost,
        "passed_raw": raw,
        "excluded": {u: why for u, why in excluded.items() if u in manifest},
        "validator_notes": {u: n for u, n in vnotes.items() if u in manifest},
        "experiment": [] if run.get("reference") else experiment_reasons(header, rows),
    }
    if run.get("reference"):
        out["passed_strict_wall"] = sum(1 for u in manifest if rows.get(u, {}).get("passed")
                                        and not rows[u].get("over_budget"))
    return out


def best_per_model(results: list[dict]) -> list[dict]:
    """Each (model, weights)'s best configuration (SCORE, then passes, then the
    earlier date), ranked; reference runs last and unranked. Two quantizations
    of one model are two entries: a 3.5 bpw build never replaces or merges with
    the 2.9 bpw one."""
    best: dict = {}
    for r in results:
        key = (r["model"], r.get("weights_id"))
        cur = best.get(key)
        if cur is None or (r["score"], r["passed"], _neg_date(r)) > (
                cur["score"], cur["passed"], _neg_date(cur)):
            best[key] = r
    rows = sorted(best.values(), key=lambda r: (r["reference"], -r["score"], -r["passed"],
                                                r["model"], r.get("weights_id") or ""))
    return rows


def _neg_date(r: dict) -> tuple:
    return tuple(-int(x) for x in re.findall(r"\d+", r.get("date") or "")) or (0,)


def build(board: dict | None = None, base_dir: str = BOARD_DIR,
          manifest: dict | None = None, exclusions: dict | None = None,
          weights: dict | None = None) -> dict:
    board = board if board is not None else load_board()
    manifest = manifest if manifest is not None else load_manifest()
    exclusions = exclusions if exclusions is not None else load_exclusions()
    weights = weights if weights is not None else load_weights()
    problems = weights_problems(board, weights)
    if problems:
        raise ValueError("weights.json / board.json: " + "; ".join(problems))
    runs = []
    for r in board["runs"]:
        s = score_run(r, manifest, base_dir, exclusions)
        s["weights_id"] = r["weights_id"]
        s["weights"] = {**weights[r["weights_id"]], "id": r["weights_id"],
                        "hub_url": hub_url(weights[r["weights_id"]])}
        runs.append(s)
    used = {r["weights_id"] for r in runs}
    return {"units": len(manifest), "bases": len({m["base"] for m in manifest.values()}),
            "runs": runs, "best": best_per_model(runs),
            "weights": {wid: {**w, "id": wid, "hub_url": hub_url(w),
                              "runs": [r["id"] for r in runs if r["weights_id"] == wid]}
                        for wid, w in weights.items() if wid in used},
            "not_yet_run": board.get("not_yet_run", [])}


# --- rendering ------------------------------------------------------------------------

def _mins(s: float | None) -> str:
    return "—" if s is None else f"{s / 60:.1f} min"


def _files(r: dict, prefix: str) -> str:
    links = [f"[{f}]({prefix}evals/boards/v5/{f})" for f in r["files"]]
    return links[0] + (f" + {len(links) - 1} repair" if len(links) > 1 else "")


def _weights_cell(r: dict, prefix: str) -> str:
    """The exact weights in one cell: the label, then the revision (linked on the Hub;
    a local pack links to the "Weights used" block, which names what it is)."""
    w = r["weights"]
    if w.get("hub_url"):
        return f"{w['label']} · [{w['repo']}@{w['revision'][:8]}]({w['hub_url']})"
    if w["revision_kind"] == "local":
        return f"{w['label']} · [local pack @{w['revision'][:8]}]({prefix}{WEIGHTS_ANCHOR})"
    return w["label"]


def _langs(r: dict) -> list[str]:
    return [f"{p}/{n}" for p, n in (r["by_lang"][lang] for lang, _ in LANGS)]


def _status(r: dict) -> str:
    if r["reference"]:
        return "reference ceiling, not ranked: a different scaffold"
    bits = []
    if r["experiment"]:
        bits.append("experiment arm (" + ", ".join(r["experiment"]) + ")")
    else:
        bits.append("board protocol (L0)")
    if r["lost"]:
        bits.append(f"{len(r['lost'])} units lost to infrastructure, counted as fails")
    return "; ".join(bits)


def _footnotes(rows: list[dict]) -> tuple[dict, list[str]]:
    """({run id: mark}, footnote lines) for the rows with exclusions or
    validator notes. Marks are escaped asterisks: *, **, ..."""
    marks, lines = {}, []
    for r in rows:
        if not (r.get("excluded") or r.get("validator_notes")):
            continue
        mark = "\\*" * (len(marks) + 1)
        marks[r["id"]] = mark
        bits = []
        if r["excluded"]:
            n = len(r["excluded"])
            bits.append(f"{r['passed_raw']}/{r['units']} raw; {n} unit{'s' * (n != 1)} ("
                        + ", ".join(sorted(r["excluded"])) + ") excluded "
                        + "; ".join(sorted(set(r["excluded"].values()))))
        for u, note in sorted(r["validator_notes"].items()):
            bits.append(f"{u} is {note}; not adjusted")
        lines.append(f"{mark} {r['model']}, {r['id']}: " + ". ".join(bits) + ".")
    return marks, lines


def _with_notes(lines: list[str], notes: list[str]) -> str:
    if notes:
        lines = lines + [""] + [n + ("  " if i < len(notes) - 1 else "")
                                for i, n in enumerate(notes)]
    return "\n".join(lines)


def render_board(b: dict, prefix: str = "") -> str:
    head = ("| # | Model | Weights | Host | Best configuration | Passed | SCORE | "
            + " | ".join(lbl for _, lbl in LANGS) + " | Mean / unit | Date | Result file | Status |")
    lines = ["<!-- generated by evals/v5_board.py: do not hand-edit -->", "",
             head, "|" + "---:|---|---|---|---|---:|---:|" + "---:|" * len(LANGS) + "---:|---|---|---|"]
    rank = 0
    marks, notes = _footnotes(b["best"])
    for r in b["best"]:
        m = marks.get(r["id"], "")
        if r["reference"]:
            num, model = "ref", f"*{r['model']}* (reference)"
            passed = f"*{r['passed']}/{r['units']}*"
            score = f"*{r['score']:.1f}*"
        else:
            rank += 1
            num, model = str(rank), f"**{r['model']}**"
            passed, score = f"**{r['passed']}/{r['units']}**{m}", f"**{r['score']:.1f}**"
        lines.append(f"| {num} | {model} | {_weights_cell(r, prefix)} | {r['host'] or '—'} | {r['config']} | {passed} | {score} | "
                     + " | ".join(_langs(r)) + f" | {_mins(r['mean_s'])} | {r['date']} | "
                     f"{_files(r, prefix)} | {_status(r)} |")
    for name in b["not_yet_run"]:
        cells = ["—", name, "", "", "not yet run on v5"] + [""] * (2 + len(LANGS) + 4)
        lines.append("| " + " | ".join(cells).rstrip() + " |")
    return _with_notes(lines, notes)


def render_runs(b: dict, prefix: str = "") -> str:
    head = ("| Model | Weights | Host | Run | Configuration | Passed | SCORE | SOLVE | LANG | "
            + " | ".join(lbl for _, lbl in LANGS) + " | Mean / unit | Date | Result files | Status |")
    lines = ["<!-- generated by evals/v5_board.py: do not hand-edit -->", "",
             head, "|---|---|---|---|---|---:|---:|---:|---:|" + "---:|" * len(LANGS) + "---:|---|---|---|"]
    ordered = sorted(b["runs"], key=lambda r: (r["reference"], r["model"], r["weights_id"],
                                               r["timestamp"], r["id"]))
    marks, notes = _footnotes(ordered)
    for r in ordered:
        m = marks.get(r["id"], "")
        run = r["id"] + (f" ({r['campaign_arm']})" if r["campaign_arm"] else "")
        files = ", ".join(f"[{f}]({prefix}evals/boards/v5/{f})" for f in r["files"])
        status = _status(r) + (f"; {r['note']}" if r.get("note") else "")
        if r["reference"]:
            status += f"; {r['passed_strict_wall']}/{r['units']} at the 1× wall"
        lines.append(f"| {r['model']} | {_weights_cell(r, prefix)} | {r['host'] or '—'} | {run} | {r['config']} | {r['passed']}/{r['units']}{m} | "
                     f"{r['score']:.1f} | {r['solve']:.1f} | {r['lang']:.1f} | "
                     + " | ".join(_langs(r)) + f" | {_mins(r['mean_s'])} | {r['date']} | "
                     f"{files} | {status} |")
    return _with_notes(lines, notes)


def _rev(repo: str | None, rev: str | None) -> str:
    if not repo:
        return "—"
    if rev and _SHA.match(rev):
        return f"[`{repo}` @ `{rev}`](https://huggingface.co/{repo}/tree/{rev})"
    return f"`{repo}`" + (f" @ `{rev}`" if rev else "")


def render_weights(b: dict, prefix: str = "") -> str:
    """One table per weights entry a board run uses, in board order: exactly which
    file set, engine and serving setup each row measured."""
    order = []
    for r in b["best"] + b["runs"]:
        if r["weights_id"] not in order:
            order.append(r["weights_id"])
    lines = ["<!-- generated by evals/v5_board.py from evals/boards/v5/weights.json: do not hand-edit -->"]
    for wid in order:
        w = b["weights"][wid]
        if w["revision_kind"] == "hub":
            weights = _rev(w["repo"], w["revision"])
        elif w["revision_kind"] == "local":
            weights = (f"local pack `{w['local_id']}`, local revision `{w['revision']}` "
                       "(OpenBeast's content id: not a Hub commit"
                       + (f"; fingerprint `{w['fingerprint'][:16]}…`" if w.get("fingerprint") else "") + ")")
        elif w["revision_kind"] == "file-sha256":
            weights = f"`{w['repo']}`, file `{w['file']}` (sha256 `{w['file_sha256']}`)"
        else:
            weights = "not public (hosted)"
        rows = [("Weights", weights)]
        if w.get("derived_from"):
            d = w["derived_from"]
            rows.append(("Derived from", _rev(d["repo"], d.get("revision")) + f": {d['relation']}"))
        if w.get("base_model"):
            rows.append(("Base model", _rev(w["base_model"]["repo"], w["base_model"].get("revision"))))
        bpw = f"{w['bpw']:g} bpw" if w["bpw"] is not None else "—"
        rows += [("Format", f"{w['format']}, {bpw}" + (f" ({w['quant_detail']})" if w.get("quant_detail") else "")),
                 ("Variant", w["variant"]),
                 ("Stock or abliterated", "abliterated (changes refusals only)" if w["abliterated"] else "stock"),
                 ("Engine", f"{w['engine']}: {w['engine_version']}")]
        for key, name in (("image", "Image"), ("recipe", "Recipe")):
            if w.get(key):
                rows.append((name, w[key]))
        ctx = f"{w['context']:,} tokens" if w.get("context") else None
        rows += [("Speculative drafter", w["drafter"] or "none"),
                 ("KV cache", w["kv_dtype"] or "—"),
                 ("Context, streams", ", ".join(x for x in (ctx, f"{w['streams']} streams" if w.get("streams") else None) if x) or "—"),
                 ("Hosts", w["hosts"]),
                 ("Board runs", ", ".join(w["runs"])),
                 ("Evidence", w["evidence"])]
        lines += ["", f"**{w['model']} · {w['label']}** (`{wid}`)", "", "| | |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(lines)


RENDER = {"board": render_board, "runs": render_runs, "weights": render_weights}


def splice(text: str, name: str, body: str) -> str | None:
    """text with the block `name` replaced by body; None if the markers are absent."""
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    i, j = text.find(start), text.find(end)
    if i < 0 or j < i:
        return None
    return text[:i + len(start)] + "\n" + body + "\n" + text[j:]


def update_docs(b: dict, write: bool, root: str = REPO_ROOT) -> list[str]:
    """Regenerate every block present in DOCS. Returns the stale (doc, block)
    names; with write=True they are rewritten. A doc with no block at all is
    drift too: README.md and docs/RESULTS.md must both carry the board."""
    stale = []
    for rel, prefix in DOCS.items():
        path = os.path.join(root, rel)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        new, found = text, 0
        for name, kind in BLOCKS.items():
            out = splice(new, name, RENDER[kind](b, prefix))
            if out is not None:
                found += 1
                new = out
        if "<!-- v5-board:start -->" not in text:
            stale.append(f"{rel}: no v5-board block")
            continue
        if rel == os.path.join("docs", "RESULTS.md") and "<!-- v5-weights:start -->" not in text:
            stale.append(f"{rel}: no v5-weights block (the weights behind every row)")
        if new != text:
            stale.append(rel)
            if write:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new)
    return stale


# --- --add ----------------------------------------------------------------------------

def add_run(board: dict, run_id: str, model: str, config: str, files: list[str],
            campaign_arm: str | None = None, note: str | None = None,
            reference: bool = False, host: str | None = None,
            board_dir: str = BOARD_DIR, weights_id: str | None = None) -> dict:
    """Copy files into board_dir and record the run (replacing one with the same id).
    weights_id names the run's weights.json entry; build() refuses a run without one."""
    names = []
    for src in files:
        dst = os.path.join(board_dir, os.path.basename(src))
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copyfile(src, dst)
        names.append(os.path.basename(src))
    run = {"id": run_id, "model": model, "config": config, "files": names}
    if weights_id:
        run["weights_id"] = weights_id
    for k, v in (("host", host), ("campaign_arm", campaign_arm), ("note", note)):
        if v:
            run[k] = v
    if reference:
        run["reference"] = True
    board["runs"] = [r for r in board["runs"] if r["id"] != run_id] + [run]
    return board


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true", help="rewrite the blocks in README.md and docs/RESULTS.md")
    ap.add_argument("--check", action="store_true", help="exit 1 if a doc block is stale")
    ap.add_argument("--add", metavar="RUN_ID", help="record a new run from FILES (then --write)")
    ap.add_argument("--model")
    ap.add_argument("--weights-id", help="the run's entry in evals/boards/v5/weights.json (required with --add)")
    ap.add_argument("--config")
    ap.add_argument("--host")
    ap.add_argument("--campaign-arm")
    ap.add_argument("--note")
    ap.add_argument("--reference", action="store_true", help="the run is a reference ledger (.jsonl)")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()

    if a.add:
        if not (a.model and a.config and a.weights_id and a.files):
            ap.error("--add needs --model, --weights-id, --config and at least one result file")
        board = add_run(load_board(), a.add, a.model, a.config, a.files, a.campaign_arm,
                        a.note, a.reference, a.host, weights_id=a.weights_id)
        build(board)  # refuse to record a run that does not load
        with open(BOARD_FILE, "w", encoding="utf-8") as f:
            json.dump(board, f, indent=2, ensure_ascii=False)
            f.write("\n")
        a.write = True

    b = build()
    if a.check:
        stale = update_docs(b, write=False)
        for s in stale:
            print(f"  DRIFT {s}: run `python3 evals/v5_board.py --write`")
        if not stale:
            print("  OK   v5 board blocks in " + ", ".join(DOCS))
        return 1 if stale else 0
    if a.write:
        for s in update_docs(b, write=True):
            print(f"  wrote {s}")
        return 0
    if a.json:
        print(json.dumps(b, indent=2))
    else:
        print(render_board(b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
