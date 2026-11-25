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

CONTEXT OVERFLOW IS A MODEL LOSS (Max's rule, 2026-11-25). A unit that ends
because the model's session outgrew the context window is a counted fail, never
an infrastructure loss: the session is the model's, and scoring it as lost would
shrink nothing here (lost units count as fails) but would let a repair run
re-roll it and would drop it from ablation.py's pairs. run_eval rows written
before the rule call such a unit server_error (the runner's overflow pattern
missed the engine's wording, e.g. Basalt's "exceeds the context window"), so
evals/boards/v5/overflow.json records a verdict, with its evidence, for every
server_error row of every committed result file: "overflow" rows are relabelled
reason context_overflow (scoring.MODEL_LOSS_REASONS) before the merge, so they
count as fails and no repair supersedes them; ablation.py applies the same
ledger. --check fails while a server_error row has no verdict, an entry is
stale, or a row's own text shows an overflow its verdict does not
(overflow_problems). evals/audit/overflow_audit.py reads the transcripts and
proposes the entries.

SCORE is the v5 capability (evals/README.md "Scoring (v5)"):
0.75 x SOLVE + 0.25 x LANG, LANG over multi-language bases only, computed by
scoring.compute_solve_breadth on rows enriched from the suite manifest.

WEIGHTS. Every run names its exact weights with "weights_id", an entry of
evals/boards/v5/weights.json (repo + revision, format, bpw, variant, stock or
abliterated, engine, drafter, KV cache, hosts and the evidence). A board row is
a model AND its exact weights: the best configuration is chosen per (model,
weights), so a second quantization of a model (another bpw, another pack) is
its own row and never replaces or merges with the first.

SEED REPLICATES. A run that repeats another run's configuration under a new
seed (a noise draw: DS-Ts1, INK-T2-S1) names that run with "replicate_of".
It is listed and scored with every run, and never chosen as the best row:
otherwise the board would rank the better of two draws of one setup, a
selection effect that a single-seed row of another model does not get. The
run it replicates is the one that ranks (the first, pre-planned draw).

SHIPPED DEFAULTS. The board ranks by SCORE; which row a platform SHIPS as its
default is a separate, human decision, recorded in board.json "defaults": one
entry per platform naming the run whose model, weights and agent setup that
platform serves by default, since when, the decision that made it so, and the
previous defaults ("previous", newest first). It is the one source of truth the
docs and the website read for "the rig's default" (never the rank, never a
hand-kept name). defaults_problems() checks every entry names a ranked board
run (not a reference, not a seed replicate), once per platform, with ISO dates
in order; render_defaults() writes the <!-- v5-defaults --> block of
docs/RESULTS.md.

The board is generated between markers in README.md and docs/RESULTS.md
(<!-- v5-board:start --> / <!-- v5-board:end -->); docs/RESULTS.md also holds
every run of every model (<!-- v5-runs:start --> / <!-- v5-runs:end -->),
each run's token usage (<!-- v5-tokens:start --> / <!-- v5-tokens:end -->) and
the weights behind them (<!-- v5-weights:start --> / <!-- v5-weights:end -->).

TOKENS. Each result row carries the runner's usage sums (tokens_prompt,
tokens_completion): the server's `usage` added up over every request of the
unit (agents/runner.py). A run's totals are summed over the board's merged rows
(a superseded infrastructure attempt is not counted); a row with no usage
(wall-killed before the partial-stats sidecar of 2026-11-18, or a reference
ledger) is counted as missing, and a wall-killed row with the sidecar
(stats_partial) is counted to its last reply. See token_stats().

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
# Context overflow verdicts for the server_error rows of every committed result
# file (module docstring, CONTEXT OVERFLOW IS A MODEL LOSS).
OVERFLOW_FILE = os.path.join(BOARD_DIR, "overflow.json")
DIAGNOSTICS_DIR = os.path.join(BOARD_DIR, "diagnostics")
OVERFLOW_REASON = "context_overflow"
OVERFLOW_VERDICTS = ("overflow", "not_overflow", "undetermined")
# Infra labels that can hide a session that ended on an overflow: the runner
# counts an overflow it does not recognise as an API error, and run_eval turns
# a failed row whose run ended on API errors into server_error.
OVERFLOW_AUDITED = frozenset({"server_error"})
# Every serving engine's wording for a request that does not fit the window,
# plus the runner's own stop. agents/runner.py's _CTX_OVERFLOW_RE (era-hashed)
# lacks Basalt's; this one is the audit's and the board's.
OVERFLOW_RE = re.compile(
    r"exceed_context_size|exceeds the available context size|"            # llama.cpp
    r"larger than the max context size|context size has been exceeded|"   # llama.cpp, older
    r"context shift is disabled|"
    r"maximum context length is \d|"                                      # vLLM, TensorFold MLX
    r"-token (?:context window|safe cache capacity)|"                     # TensorFold CUDA
    r"server's context window is \d|"                                     # TensorFold MLX
    r"exceeds the context window|"                                        # Basalt
    r"context_overflow_unrecoverable|context overflow \(nothing left to compact\)",  # the runner
    re.IGNORECASE)
# Row fields that may carry an error's text (none do before the next era; the
# proposal in docs/HARNESS_NOTES.md adds them).
OVERFLOW_TEXT_FIELDS = ("stop_reason", "last_api_error", "api_error_sample")
# The exact weights behind each run (board.json "weights_id" -> an entry here).
WEIGHTS_FILE = os.path.join(BOARD_DIR, "weights.json")
MANIFEST = os.path.join(EVALS_DIR, "suites", "v5.json")

# Docs that hold a generated block, and the path prefix from that doc to the
# repo root (for links to the result files).
DOCS = {"README.md": "", os.path.join("docs", "RESULTS.md"): "../"}
BLOCKS = {"v5-board": "board", "v5-runs": "runs", "v5-tokens": "tokens", "v5-weights": "weights",
          "v5-defaults": "defaults"}
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
        if kind == "file-sha256" and not w["repo"] and not (w.get("derived_from") or {}).get("repo"):
            bad.append(f"weights {wid}: a locally built file (repo null) needs derived_from: what it was built from")
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
    ids = {r["id"]: r for r in board.get("runs", [])}
    for r in board.get("runs", []):
        rep = r.get("replicate_of")
        if rep is None:
            continue
        base = ids.get(rep)
        if base is None or rep == r["id"]:
            bad.append(f"run {r['id']}: replicate_of {rep!r} is not another board run")
        elif (base["model"], base.get("weights_id")) != (r["model"], r.get("weights_id")):
            bad.append(f"run {r['id']}: a seed replicate needs the same model and weights as {rep}")
        elif base.get("replicate_of"):
            bad.append(f"run {r['id']}: replicate_of names {rep}, itself a replicate: name the original")
    return bad


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_REQUIRED = ("platform", "label", "run", "agent_profile", "since", "decision")


def defaults_problems(board: dict) -> list[str]:
    """Why board.json "defaults" is not usable ([] = fine). Module docstring, SHIPPED DEFAULTS."""
    bad = []
    ids = {r["id"]: r for r in board.get("runs", [])}
    seen = set()

    def usable(where: str, rid) -> None:
        r = ids.get(rid)
        if r is None:
            bad.append(f"{where}: run {rid!r} is not a board run")
        elif r.get("reference"):
            bad.append(f"{where}: run {rid} is a reference ledger, never a shipped default")
        elif r.get("replicate_of"):
            bad.append(f"{where}: run {rid} is a seed replicate; name the run it replicates ({r['replicate_of']})")

    for i, d in enumerate(board.get("defaults", [])):
        where = f"defaults[{i}]"
        missing = [k for k in DEFAULT_REQUIRED if not d.get(k)]
        if missing:
            bad.append(f"{where}: missing {', '.join(missing)}")
            continue
        where = f"default {d['platform']}"
        if d["platform"] in seen:
            bad.append(f"{where}: two defaults for one platform")
        seen.add(d["platform"])
        usable(where, d["run"])
        if not _DATE.match(str(d["since"])):
            bad.append(f"{where}: since {d['since']!r} is not YYYY-MM-DD")
        for j, p in enumerate(d.get("previous", [])):
            pw = f"{where} previous[{j}]"
            usable(pw, p.get("run"))
            if p.get("run") == d["run"]:
                bad.append(f"{pw}: the previous default is the current one")
            if not (_DATE.match(str(p.get("from", ""))) and _DATE.match(str(p.get("until", "")))):
                bad.append(f"{pw}: from / until must be YYYY-MM-DD")
            elif not p["from"] <= p["until"] <= d["since"]:
                bad.append(f"{pw}: dates out of order (from {p['from']}, until {p['until']}, current since {d['since']})")
    return bad


def hub_url(w: dict) -> str | None:
    """The Hub page of the exact revision, for weights that are on the Hub."""
    if w.get("revision_kind") == "hub":
        return f"https://huggingface.co/{w['repo']}/tree/{w['revision']}"
    if w.get("revision_kind") == "file-sha256" and _SHA.match(str(w.get("revision"))):
        # A file pinned by sha256 that also names the Hub commit it was checked at.
        return f"https://huggingface.co/{w['repo']}/blob/{w['revision']}/{w['file']}"
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


def load_overflow(path: str = OVERFLOW_FILE) -> list[dict]:
    """overflow.json's "audit": [{file, unit, label, verdict, cause, evidence}];
    an absent file is an empty ledger."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("audit") or []


def overflow_verdicts(audit: list[dict] | None, file: str) -> dict:
    """{unit: entry} of the ledger's "overflow" verdicts for one result file (by basename)."""
    base = os.path.basename(file)
    return {e["unit"]: e for e in audit or []
            if e.get("file") == base and e.get("verdict") == "overflow"}


def reclassify_overflows(rows: dict, file: str, audit: list[dict] | None) -> dict:
    """rows with every ledger-confirmed overflow relabelled: reason context_overflow
    (a model fail: campaign.infra_reason and ablation.invalid_reason pass it),
    the original label kept in reason_before_overflow. A pass is left alone."""
    hits = overflow_verdicts(audit, file)
    out = dict(rows)
    for uid in hits:
        r = rows.get(uid)
        if r is None or r.get("passed") or r.get("reason") == OVERFLOW_REASON:
            continue
        out[uid] = {**r, "reason": OVERFLOW_REASON, "reason_before_overflow": r.get("reason")}
    return out


def row_overflow_text(row: dict) -> str | None:
    """The first overflow wording found in a row's own error fields, or None."""
    for k in OVERFLOW_TEXT_FIELDS:
        m = OVERFLOW_RE.search(str(row.get(k) or ""))
        if m:
            return f"{k}: {m.group(0)}"
    return None


def overflow_files(board: dict, base_dir: str = BOARD_DIR) -> dict:
    """{basename: path} of every committed run_eval result file: each board run's
    files and evals/boards/v5/diagnostics/eval-*.json (reference ledgers excluded)."""
    out = {}
    for r in board.get("runs", []):
        if r.get("reference"):
            continue
        for f in r["files"]:
            out[f] = os.path.join(base_dir, f)
    diag = os.path.join(base_dir, "diagnostics")
    if os.path.isdir(diag):
        for f in sorted(os.listdir(diag)):
            if f.startswith("eval-") and f.endswith(".json"):
                out.setdefault(f, os.path.join(diag, f))
    return out


def overflow_problems(board: dict, audit: list[dict], base_dir: str = BOARD_DIR) -> list[str]:
    """Why the overflow ledger does not cover the committed files ([] = fine):
    a server_error row with no verdict; an entry without a known verdict or
    evidence, naming no committed file or no infrastructure row; a row whose
    own text shows an overflow its verdict does not."""
    bad = []
    files = overflow_files(board, base_dir)
    seen: dict = {}
    for i, e in enumerate(audit):
        key = (e.get("file"), e.get("unit"))
        where = f"overflow.json audit[{i}] {e.get('file')} {e.get('unit')}"
        if key in seen:
            bad.append(f"{where}: a second entry for the same row")
        seen[key] = e
        if e.get("verdict") not in OVERFLOW_VERDICTS:
            bad.append(f"{where}: verdict {e.get('verdict')!r} is not one of {', '.join(OVERFLOW_VERDICTS)}")
        if not (e.get("evidence") or "").strip():
            bad.append(f"{where}: no evidence")
        if e.get("file") not in files:
            bad.append(f"{where}: not a committed result file (a board run's or diagnostics/)")
    for name, path in files.items():
        _, rows = campaign.load_rows(path)
        for uid, r in rows.items():
            label = infra_reason(r)
            e = seen.get((name, uid))
            if e is None:
                if label in OVERFLOW_AUDITED:
                    bad.append(f"{name} {uid}: a {label} row with no overflow.json verdict "
                               "(a context overflow is a model loss, 2026-11-25: audit it with "
                               "evals/audit/overflow_audit.py)")
                continue
            if not label:
                bad.append(f"{name} {uid}: overflow.json has a verdict but the row is not an "
                           "infrastructure row (stale entry)")
            elif e.get("label") and e["label"] != label:
                bad.append(f"{name} {uid}: overflow.json says label {e['label']!r}, the row is {label!r}")
            text = row_overflow_text(r)
            if text and e.get("verdict") != "overflow":
                bad.append(f"{name} {uid}: the row shows a context overflow ({text}) but its verdict "
                           f"is {e.get('verdict')!r}: reclassify it (a model loss)")
    for (name, uid), e in seen.items():
        if name in files and uid not in campaign.load_rows(files[name])[1]:
            bad.append(f"overflow.json {name} {uid}: no such unit in the file")
    return bad


def merge_runs(paths: list[str], overflow: list[dict] | None = None) -> tuple[dict, dict]:
    """(header of the first file, {unit: row}) over a main run and its repair
    runs: a later row replaces an earlier one only if the earlier is an
    infrastructure row (or the unit was missing). Each file's ledger-confirmed
    overflows are relabelled first (reclassify_overflows): a model fail, which
    no repair row supersedes. overflow None reads the committed ledger
    (every caller agrees: the board, v5_report.py, ablation.py); [] applies none."""
    overflow = load_overflow() if overflow is None else overflow
    header: dict = {}
    merged: dict = {}
    for p in paths:
        h, rows = campaign.load_rows(p)
        rows = reclassify_overflows(rows, p, overflow)
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


# --- tokens ---------------------------------------------------------------------------

def token_stats(rows: dict, manifest: dict, passed: int, reference: bool = False) -> dict:
    """A run's token usage over the manifest's units. prompt / completion are the
    sums of the rows' tokens_prompt / tokens_completion (each the server's usage
    summed over every request of the unit, so the prompt re-counts the whole
    context every turn). A row with neither is `missing` (no usage recorded);
    `partial` rows were wall-killed and count to their last reply (a lower bound).
    per_unit is over the units with data; per_pass is the total over the scored
    passes (the tokens spent for each pass). A reference ledger has no usage."""
    prompt = completion = with_data = partial = 0
    for uid in manifest:
        r = None if reference else rows.get(uid)
        tp = (r or {}).get("tokens_prompt") or 0
        tc = (r or {}).get("tokens_completion") or 0
        if not (isinstance(tp, int) and isinstance(tc, int)) or tp + tc <= 0:
            continue
        prompt += tp
        completion += tc
        with_data += 1
        partial += bool(r.get("stats_partial"))
    total = prompt + completion
    return {"prompt": prompt, "completion": completion, "total": total,
            "units_with_data": with_data, "missing": len(manifest) - with_data,
            "partial": partial,
            # round half up (as the website's Math.round), not Python's half-to-even
            "per_unit": _round_half_up(total, with_data) if with_data else None,
            "per_pass": _round_half_up(total, passed) if with_data and passed else None}


def _round_half_up(num: int, den: int) -> int:
    """num / den rounded half up, exact for non-negative integers (JS Math.round)."""
    return (2 * num + den) // (2 * den)


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
              exclusions: dict | None = None, overflow: list[dict] | None = None) -> dict:
    """A run's numbers. An excluded unit counts as NOT passed (the
    conservative figure); `passed_raw` keeps the unadjusted count. A unit
    that ended on a context overflow (overflow.json) is a counted fail and is
    listed in `overflow` with its original label."""
    exclusions = exclusions or {}
    excluded = {e["unit"]: e.get("reason", "excluded")
                for e in exclusions.get("exclusions", []) if applies(e, run)}
    vnotes = {e["unit"]: e.get("note", "") for e in exclusions.get("validator_notes", [])
              if applies(e, run)}
    paths = [os.path.join(base_dir, f) for f in run["files"]]
    if run.get("reference"):
        header, rows = load_reference(paths[0])
    else:
        header, rows = merge_runs(paths, overflow)
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
        "replicate_of": run.get("replicate_of"),
        "files": run["files"], "timestamp": ts, "date": ts[:10],
        "passed": sum(1 for t in scored if t["passed"]), "units": len(scored),
        "solve": solve, "lang": lang, "score": cap,
        "by_lang": {k: tuple(v) for k, v in by_lang.items()},
        "mean_s": round(sum(secs) / len(secs), 1) if secs else None,
        "lost": lost,
        "overflow": {u: rows[u].get("reason_before_overflow") for u in manifest
                     if (rows.get(u) or {}).get("reason") == OVERFLOW_REASON},
        "passed_raw": raw,
        "excluded": {u: why for u, why in excluded.items() if u in manifest},
        "validator_notes": {u: n for u, n in vnotes.items() if u in manifest},
        "experiment": [] if run.get("reference") else experiment_reasons(header, rows),
    }
    out["tokens"] = token_stats(rows, manifest, out["passed"], bool(run.get("reference")))
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
        if r.get("replicate_of"):
            continue   # a seed replicate never ranks (module docstring)
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
          weights: dict | None = None, overflow: list[dict] | None = None) -> dict:
    board = board if board is not None else load_board()
    manifest = manifest if manifest is not None else load_manifest()
    exclusions = exclusions if exclusions is not None else load_exclusions()
    overflow = overflow if overflow is not None else load_overflow()
    weights = weights if weights is not None else load_weights()
    problems = weights_problems(board, weights) + defaults_problems(board)
    if problems:
        raise ValueError("weights.json / board.json: " + "; ".join(problems))
    runs = []
    for r in board["runs"]:
        s = score_run(r, manifest, base_dir, exclusions, overflow)
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
            "not_yet_run": board.get("not_yet_run", []),
            "defaults": board.get("defaults", [])}


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
    if w["revision_kind"] == "file-sha256" and not w["repo"]:
        # a file converted or built locally, pinned by its sha256 (not on the Hub)
        return f"{w['label']} · [local file sha256 {w['file_sha256'][:8]}]({prefix}{WEIGHTS_ANCHOR})"
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
    if r.get("overflow"):
        n = len(r["overflow"])
        bits.append(f"{n} unit{'s' * (n != 1)} ended on context overflow: model fail{'s' * (n != 1)} "
                    "(overflow.json)")
    if r.get("replicate_of"):
        bits.append(f"seed replicate of {r['replicate_of']}: never the ranked row")
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
            passed = f"*{r['passed']}/{r['units']}*{m}"
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
        elif w["revision_kind"] == "file-sha256" and not w["repo"]:
            weights = (f"local file `{w['file']}` (sha256 `{w['file_sha256']}`; built here, not on the Hub: "
                       "see Derived from)")
        elif w["revision_kind"] == "file-sha256":
            weights = f"`{w['repo']}`, file `{w['file']}` (sha256 `{w['file_sha256']}`)"
            if _SHA.match(str(w.get("revision"))):
                weights += f", checked at Hub revision `{w['revision']}`"
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
                 ("Context, streams", ", ".join(x for x in (ctx, f"{w['streams']} stream{'' if w['streams'] == 1 else 's'}" if w.get("streams") else None) if x) or "—"),
                 ("Hosts", w["hosts"]),
                 ("Board runs", ", ".join(w["runs"])),
                 ("Evidence", w["evidence"])]
        lines += ["", f"**{w['model']} · {w['label']}** (`{wid}`)", "", "| | |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in rows]
    return "\n".join(lines)


def _n(x: int | None) -> str:
    return "—" if x is None else f"{x:,}"


TOKENS_NOTE = """\
How to read it. **Prompt** and **completion** are what the serving engine reported
in each response's `usage`, added up over every request of every unit
(`agents/runner.py`; each result row's `tokens_prompt` and `tokens_completion`).
The agent re-sends its whole conversation every turn, so **prompt tokens re-count
the context on every turn**: they measure what the server was asked to read, not
unique text. A prompt-cache hit is still counted: every run's per-turn prompt is
full-context sized (medians 7.5K to 15K, never under ~4K, the system prompt and
task), and the runner keeps no cached count. **Completion includes thinking**: every turn in the kept agent logs that
hit the output cap with reasoning and no answer text reports exactly the cap in
`completion_tokens` (DeepSeek on TensorFold, Hy4-flash on vLLM, and Qwen3.8 on llama.cpp), so the
reasoning is in the count. GLM's thinking arms predate that log field: same
engine, unverified. **Per unit** is the total over the units with usage;
**per pass** is the total over the scored passes (tokens spent for each solved
unit, lower is more efficient). **No usage**: units whose row recorded none
(wall-killed before the runner's partial-stats file of 2026-11-18; the reference
ledger records none at all), so that run's totals are an undercount. **Partial**:
wall-killed units counted to their last reply (the reply in flight is lost).
Only the board's merged rows count: a superseded infrastructure attempt is not
in the total. Each model counts with its own tokenizer and each engine reports its own
`usage`, so totals compare cleanly across runs of one model on one engine and
only roughly across models or engines. Single seed."""


def render_tokens(b: dict, prefix: str = "") -> str:
    """Every run's token usage, in the runs table's order."""
    lines = ["<!-- generated by evals/v5_board.py: do not hand-edit -->", "",
             "| Model | Run | Prompt | Completion | Total | Units with usage | Per unit | Passed | Per pass | No usage | Partial |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    ordered = sorted(b["runs"], key=lambda r: (r["reference"], r["model"], r["weights_id"],
                                               r["timestamp"], r["id"]))
    for r in ordered:
        t = r["tokens"]
        run = r["id"] + (f" ({r['campaign_arm']})" if r["campaign_arm"] else "")
        if not t["units_with_data"]:
            lines.append(f"| {r['model']} | {run} | — | — | — | 0/{r['units']} | — | "
                         f"{r['passed']}/{r['units']} | — | {t['missing']} | 0 |")
            continue
        lines.append(f"| {r['model']} | {run} | {_n(t['prompt'])} | {_n(t['completion'])} | "
                     f"{_n(t['total'])} | {t['units_with_data']}/{r['units']} | {_n(t['per_unit'])} | "
                     f"{r['passed']}/{r['units']} | {_n(t['per_pass'])} | {t['missing']} | {t['partial']} |")
    return "\n".join(lines + ["", TOKENS_NOTE])


def render_defaults(b: dict, prefix: str = "") -> str:
    """The shipped default per platform (board.json "defaults"), with its measured row."""
    runs = {r["id"]: r for r in b["runs"]}
    lines = ["<!-- generated by evals/v5_board.py: do not hand-edit -->", "",
             "| Platform | Default model | Weights | Row | Passed | SCORE | Agent setup | Since | Decision | Previous default |",
             "|---|---|---|---|---:|---:|---|---|---|---|"]
    for d in b.get("defaults", []):
        r = runs[d["run"]]
        prev = "; ".join(f"{runs[p['run']]['model']} (`{p['run']}`, `AGENT_PROFILE={p['agent_profile']}`), "
                         f"{p['from']} to {p['until']}" + (f", now the {p['role']}" if p.get("role") else "")
                         for p in d.get("previous", [])) or "—"
        lines.append(f"| {d['label']} | **{r['model']}** | {_weights_cell(r, prefix)} | `{r['id']}` | "
                     f"{r['passed']}/{r['units']} | {r['score']:.1f} | `AGENT_PROFILE={d['agent_profile']}` | "
                     f"{d['since']} | [record]({prefix}{d['decision']}) | {prev} |")
    return "\n".join(lines)


RENDER = {"board": render_board, "runs": render_runs, "tokens": render_tokens,
          "weights": render_weights, "defaults": render_defaults}


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
        if rel == os.path.join("docs", "RESULTS.md") and "<!-- v5-tokens:start -->" not in text:
            stale.append(f"{rel}: no v5-tokens block (the token usage of every run)")
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
    unaudited = overflow_problems(load_board(), load_overflow())
    for p in unaudited:
        print(f"  OVERFLOW {p}", file=sys.stderr)
    if a.check:
        stale = update_docs(b, write=False)
        for s in stale:
            print(f"  DRIFT {s}: run `python3 evals/v5_board.py --write`")
        if not stale:
            print("  OK   v5 board blocks in " + ", ".join(DOCS))
        if not unaudited:
            print("  OK   every server_error row has a context-overflow verdict (overflow.json)")
        return 1 if stale or unaudited else 0
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
