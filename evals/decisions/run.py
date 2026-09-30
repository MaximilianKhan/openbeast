#!/usr/bin/env python3
"""Decision-quality harness: evaluate, calibrate, gate, fit `linear` (plan §5.8).

    python3 evals/decisions/run.py --decision D --engine E [--split test]
                                   [--fit-linear] [--calibrate] [--gate]
                                   [--load-report loadgen.json] [--compare rules,linear]

Namespace rule: this lives in evals/decisions/, never touches SUITE_VERSION or
the v4 cache hash, and evals/run_eval.py never imports it (boundary-tested).

Datasets: evals/decisions/<decision>/{train,calib,test,ood,adversarial}.jsonl
+ MANIFEST.toml. Rows: {"id","input":{…},"items"?,"label","source","group",
"labeller","added_at","note"}. Enforced here, not by convention:
  * test and ood never contain synthetic rows;
  * a `group` never appears in two splits (split by source group, not by row);
  * MANIFEST sha256 pins, when present, must match the files.
Outputs: .run/instinct/eval/<decision>/<ts>/{report.json,report.txt,samples.jsonl}.
Records: evals/decisions/<decision>/calib/<hash16>.json (--calibrate) and
gates/<hash16>.json (--gate). A gate record is the ONLY way a decision reaches
enforce, and it only counts once committed (lifecycle.git_committed).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "agents"))
sys.path.insert(0, str(HERE))

import metrics as M  # noqa: E402
from instinct import calibrate as C  # noqa: E402
from instinct import core  # noqa: E402
from instinct.config import LLM_ADAPTERS, load_config  # noqa: E402
from instinct.engines import EngineError, ScoreReq, build_engine  # noqa: E402
from instinct.engines import linear as L  # noqa: E402
from instinct.engines.rules import mechanical_label  # noqa: E402
from instinct.render import InputError, validate_inputs  # noqa: E402
from instinct.spec import DecisionSpec, decision_hash, load_spec  # noqa: E402

SPLITS = ("train", "calib", "test", "ood", "adversarial")
FROZEN_NO_SYNTHETIC = ("test", "ood")


class DatasetError(ValueError):
    pass


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_dataset(ddir: Path, spec: DecisionSpec) -> tuple[dict[str, list[dict]], dict]:
    manifest = {}
    mp = ddir / "MANIFEST.toml"
    if mp.exists():
        with open(mp, "rb") as fh:
            manifest = tomllib.load(fh)
    pins = (manifest.get("files") or {})
    data: dict[str, list[dict]] = {}
    groups: dict[str, str] = {}
    for split in SPLITS:
        p = ddir / f"{split}.jsonl"
        if not p.exists():
            data[split] = []
            continue
        if split in pins and pins[split] != sha256_file(p):
            raise DatasetError(f"{p.name}: sha256 does not match MANIFEST.toml")
        rows = []
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                raise DatasetError(f"{p.name}:{i}: not JSON") from None
            for k in ("id", "input", "label", "source"):
                if k not in r:
                    raise DatasetError(f"{p.name}:{i}: missing {k}")
            if r["label"] not in spec.label_names:
                raise DatasetError(f"{p.name}:{i}: label {r['label']!r} not in the spec")
            try:
                r["input"] = validate_inputs(spec, r["input"])
            except InputError as exc:
                raise DatasetError(f"{p.name}:{i}: {exc}") from None
            if split in FROZEN_NO_SYNTHETIC and r["source"] == "synthetic":
                raise DatasetError(f"{p.name}:{i}: synthetic rows never go in {split}")
            g = r.get("group") or r["id"]
            if g in groups and groups[g] != split:
                raise DatasetError(f"{p.name}:{i}: group {g!r} is also in {groups[g]} "
                                   "(split by source group, never by row)")
            groups[g] = split
            rows.append(r)
        data[split] = rows
    ids = [r["id"] for rows in data.values() for r in rows]
    if len(ids) != len(set(ids)):
        raise DatasetError("duplicate row id across the dataset")
    return data, manifest


def git_sha() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


class Scorer:
    """One engine, driven exactly as the service drives it: every row has the
    mechanical labels zeroed with the same core.mask_mechanical the service
    uses, so T, thresholds and gate metrics are fitted on the population that
    is served. Rows whose facts FORCE a mechanical label are removed from the
    dataset before any scorer sees them (judged_rows): the service never asks
    a model about them."""

    def __init__(self, cfg, spec: DecisionSpec, name: str, *, transport=None):
        if name not in cfg.engines:
            raise SystemExit(f"engine {name!r} not configured "
                             f"({cfg.engine_errors.get(name, 'unknown')})")
        self.name, self.spec, self.cfg = name, spec, cfg
        ctx = {"records_dir": cfg.records_dir}
        if transport is not None:
            ctx["transport"] = transport
        self.engine = build_engine(cfg.engines[name], **ctx)
        self.lock = None
        self.probe = None

    @property
    def adapter(self) -> str:
        return self.engine.adapter

    async def attach(self, probe: bool = True) -> None:
        if self.adapter in LLM_ADAPTERS and probe:
            self.probe = await self.engine.probe([self.spec])
            if not self.probe.ok:
                raise SystemExit(f"{self.name}: conformance probe failed: {self.probe.reason}")
            if self.probe.exec_forced:
                self.engine.exec_forced = self.probe.exec_forced
        locks = await self.engine.attach([self.spec])
        self.lock = locks.get(self.spec.id)

    def dhash(self) -> str:
        ids = self.lock.ids if (self.lock and self.adapter in LLM_ADAPTERS) else None
        if self.adapter in LLM_ADAPTERS and not ids:
            raise SystemExit(f"{self.name}: label lock failed: "
                             f"{self.lock.reason if self.lock else 'none'}")
        return decision_hash(self.spec, self.engine.hash_identity(), ids)

    async def score_rows(self, rows: list[dict]) -> list[dict]:
        out = []
        for r in rows:
            if self.adapter == "linear" and (self.lock is None or not self.lock.ok):
                raise SystemExit(f"linear: no model for {self.spec.id} — run --fit-linear")
            items = [it["text"] if isinstance(it, dict) else it for it in r.get("items") or []] \
                or None
            t0 = time.perf_counter()
            try:
                res = await self.engine.score(ScoreReq(
                    spec=self.spec, inputs=r["input"], items=items,
                    label_ids=self.lock.ids if self.lock else None,
                    deadline_s=self.engine.binding.timeout_ms / 1000))
                row = res.rows[0]
                q, logits = core.mask_mechanical(self.spec, row.q, row.logits, row.mechanical)
                if logits is not None:
                    q = core.softmax(logits)
                if q is not None and not any(v > 0 for v in q.values()):
                    raise EngineError("engine returned an all-zero label distribution")
                out.append({"id": r["id"], "y": r["label"], "source": r["source"],
                            "q": q, "label_mass": row.label_mass,
                            "truncated": list(row.truncated), "ood": row.ood,
                            "defer": row.defer, "ms": (time.perf_counter() - t0) * 1000,
                            "error": None})
            except (EngineError, asyncio.TimeoutError, ValueError) as exc:
                out.append({"id": r["id"], "y": r["label"], "source": r["source"], "q": None,
                            "label_mass": None, "truncated": [], "ood": False, "defer": False,
                            "ms": (time.perf_counter() - t0) * 1000, "error": str(exc)[:200]})
        return out


def judged_rows(spec: DecisionSpec, data: dict[str, list[dict]]
                ) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """Drop rows whose facts force a mechanical label (vision, long_context):
    the service answers those with `rules` only and never judges them, so
    they are not part of the population any engine is calibrated or gated
    on. One filter for every scorer keeps paired comparisons paired."""
    if not spec.mechanical:
        return data, {}
    out, dropped = {}, {}
    for split, rows in data.items():
        keep = [r for r in rows if mechanical_label(spec, r["input"]) is None]
        out[split] = keep
        if len(keep) != len(rows):
            dropped[split] = len(rows) - len(keep)
    return out, dropped


def gate_integrity(spec: DecisionSpec, manifest: dict, subj, a) -> list[dict]:
    """What a PASSING gate record needs besides good numbers: a frozen,
    human-labelled dataset (MANIFEST status "gated"), every split a
    criterion reads pinned by sha256 in MANIFEST [files] (load_dataset
    already refuses a mismatch; this refuses an ABSENT pin), and — for an
    LLM engine — a conformance probe on the engine that was gated."""
    pins = manifest.get("files") or {}
    used = sorted({p for c in spec.gate.criteria for p in c.split.split("+") if p in SPLITS})
    out = [{"check": "dataset status is gated", "ok": manifest.get("status") == "gated",
            "detail": manifest.get("status", "no MANIFEST")},
           {"check": "gate splits pinned in MANIFEST", "ok": all(p in pins for p in used),
            "detail": [p for p in used if p not in pins]}]
    if subj.adapter in LLM_ADAPTERS:
        out.append({"check": "conformance probe ran", "ok": not a.no_probe and bool(
            subj.probe and subj.probe.ok), "detail": "--no-probe" if a.no_probe else None})
    return out


def apply_T(samples: list[dict], T: float | None) -> list[dict]:
    out = []
    for s in samples:
        p = None
        if s["q"]:
            try:
                p = core.apply_temperature(s["q"], T or 1.0)
            except ValueError:
                p = None
        out.append({**s, "p": p})
    return out


def split_metrics(spec: DecisionSpec, rows: list[dict], thresholds: dict | None,
                  calibrated: bool) -> dict:
    labels = spec.label_names
    n = len(rows)
    if n == 0:
        return {"n": 0}
    k = sum(M.correct(rows))
    e = M.ece(rows, labels)
    res = {
        "n": n,
        "accuracy": k / n, "accuracy_ci95": list(M.wilson(k, n)),
        "macro_f1": M.macro_f1(rows, labels),
        "macro_f1_ci95": list(M.bootstrap_ci(rows, lambda s: M.macro_f1(s, labels),
                                             n_boot=500)),
        "nll": M.nll(rows, labels), "brier": M.brier(rows, labels),
        "ece": e["ece"], "ece_bins": e["bins"], "ece_indicative": e["indicative"],
        "ece_ci95": list(M.bootstrap_ci(rows, lambda s: M.ece(s, labels)["ece"], n_boot=300)),
        "reliability": e["table"], "aurc": M.aurc(rows, labels),
        "truncated_rate": sum(1 for r in rows if r.get("truncated")) / n,
        "errors": sum(1 for r in rows if r.get("error")),
        "label_mass": _pcts([r["label_mass"] for r in rows
                             if isinstance(r.get("label_mass"), (int, float))]),
    }
    act = {}
    abstain = 0
    for r in rows:
        if not any(C._row_would_act(spec, r, lb, (thresholds or spec.policy.act).get(lb))
                   for lb in spec.policy.act) or not calibrated:
            abstain += 1
    for lb in spec.policy.act:
        thr = core.effective_threshold(spec, lb, thresholds)
        st = C.act_stats(spec, rows, lb, thr) if calibrated else {
            "acts": 0, "act_errors": 0, "act_coverage": 0.0, "act_precision": 1.0}
        n_true = sum(1 for r in rows if r["y"] == lb)
        correct_acts = st["acts"] - st["act_errors"]
        st["threshold"] = thr
        st["act_coverage_ci95"] = list(M.wilson(correct_acts, n_true))
        act[lb] = st
    res["act"] = act
    res["abstain_rate"] = abstain / n
    # per-source slices (battery / adversarial / truncation / synthetic ...)
    slices = {}
    for src in sorted({r.get("source", "?") for r in rows}):
        sub = [r for r in rows if r.get("source") == src]
        slices[src] = {"n": len(sub), "accuracy": M.accuracy(sub)}
    res["slices"] = slices
    return res


def _pcts(xs: list[float]) -> dict:
    if not xs:
        return {}
    return {"p05": M.percentile(xs, 0.05), "p50": M.percentile(xs, 0.5),
            "p95": M.percentile(xs, 0.95), "n": len(xs)}


def concat(by_split: dict[str, list[dict]], split_expr: str) -> list[dict]:
    out = []
    for s in split_expr.split("+"):
        out.extend(by_split.get(s, []))
    return out


def metric_value(spec: DecisionSpec, crit, rows: list[dict], thresholds, calibrated,
                 comparisons: dict, load: dict | None, subject_adapter: str):
    """(value, ci95 or None, note)."""
    if crit.metric == "latency_p95_ms":
        if not load or "p95_ms" not in load:
            return None, None, "no loadgen report"
        return float(load["p95_ms"]), None, None
    if crit.metric == "mcnemar_p_vs":
        if subject_adapter not in LLM_ADAPTERS:
            return 0.0, None, "not_applicable (only LLM engines must beat tier-0)"
        cmp = comparisons.get(crit.label, {}).get(crit.split)
        if cmp is None:
            return None, None, f"no paired comparison vs {crit.label}"
        # A significant LOSS must never pass: the subject has to be the winner.
        p = cmp["mcnemar"]["p"] if cmp["mcnemar"]["b"] > cmp["mcnemar"]["c"] else 1.0
        return p, None, None
    if not rows:
        return None, None, "no rows"
    sm = split_metrics(spec, rows, thresholds, calibrated)
    if crit.metric in ("act_errors", "act_coverage", "act_precision"):
        st = sm["act"].get(crit.label, {})
        ci = st.get("act_coverage_ci95") if crit.metric == "act_coverage" else None
        return st.get(crit.metric), ci, None
    if crit.metric == "aurc":
        return sm["aurc"]["aurc"], None, None
    ci = sm.get(f"{crit.metric}_ci95")
    return sm.get(crit.metric), ci, None


async def main_async(a) -> int:
    cfg = load_config(a.config)
    if a.records_dir:
        cfg.records_dir = Path(a.records_dir)
    spec = load_spec(Path(cfg.decisions_dir) / f"{a.decision}.toml")
    data_dir = Path(a.data_dir) / a.decision
    data, manifest = load_dataset(data_dir, spec)
    data, mech_dropped = judged_rows(spec, data)
    subj = Scorer(cfg, spec, a.engine)
    report: dict = {"decision": spec.id, "engine": a.engine, "adapter": subj.adapter,
                    "dataset_version": manifest.get("dataset_version", "unversioned"),
                    "dataset_status": manifest.get("status", "unknown"),
                    "git_sha": git_sha(),
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    if a.fit_linear:
        if subj.adapter != "linear":
            raise SystemExit("--fit-linear needs a linear engine")
        if not data["train"]:
            raise SystemExit("no train rows")
        model = L.fit(spec, data["train"])
        h = decision_hash(spec, subj.engine.hash_identity(), None)
        mpath = C.linear_model_path(cfg.records_dir, spec.id, h)
        C.write_record(mpath, model)
        report["linear_model"] = {"path": str(mpath), "n_train": model["n_train"],
                                  "sha256": C.file_sha256(mpath)}
    await subj.attach(probe=not a.no_probe)
    dhash = subj.dhash()
    report["decision_hash"] = dhash
    if subj.probe is not None:
        report["probe"] = {"ok": subj.probe.ok, "nondeterministic": subj.probe.nondeterministic,
                           "exec_forced": subj.probe.exec_forced}

    eval_splits = [a.split] if a.split else [s for s in ("calib", "test", "ood", "adversarial")
                                              if data[s]]
    need = set(eval_splits) | ({"calib"} if a.calibrate else set())
    samples = {s: await subj.score_rows(data[s]) for s in sorted(need)}
    all_samples = [dict(s, split=sp) for sp, rows in samples.items() for s in rows]
    samples_blob = "\n".join(json.dumps(s, sort_keys=True) for s in all_samples) + "\n"
    samples_sha = hashlib.sha256(samples_blob.encode()).hexdigest()

    crec_path = C.calib_path(cfg.records_dir, spec.id, dhash)
    if a.calibrate:
        if not subj.engine.caps.probs:
            raise SystemExit(f"{a.engine} has no probabilities — it can never be calibrated (I6)")
        cal = [s for s in samples["calib"] if s["q"]]
        if not cal:
            raise SystemExit("no scoreable calib rows")
        T = C.fit_temperature([s["q"] for s in cal], [s["y"] for s in cal])
        pre = apply_T(samples["calib"], 1.0)
        post = apply_T(samples["calib"], T)
        thresholds, detail = C.fit_thresholds(spec, [dict(r, p=r["p"]) for r in post if r["p"]])
        e_post = M.ece(post, spec.label_names)
        rec = {
            "decision": spec.id, "decision_hash": dhash, "engine": a.engine,
            "adapter": subj.adapter, "T": T, "thresholds": thresholds,
            "threshold_fit": detail, "n_calib": len(cal),
            "dataset_version": report["dataset_version"],
            "pre": {"nll": M.nll(pre, spec.label_names), "brier": M.brier(pre, spec.label_names),
                    "ece": M.ece(pre, spec.label_names)["ece"]},
            "post": {"nll": M.nll(post, spec.label_names),
                     "brier": M.brier(post, spec.label_names), "ece": e_post["ece"]},
            "reliability": e_post["table"],
            "label_mass_ref": _pcts([s["label_mass"] for s in cal
                                     if isinstance(s["label_mass"], (int, float))]) or None,
            "git_sha": report["git_sha"], "samples_sha256": samples_sha,
            "created_at": report["created_at"],
        }
        if subj.adapter == "linear":
            rec["model_file_sha256"] = subj.engine.model_sha.get(spec.id)
        C.write_record(crec_path, rec)
        report["calibration"] = {"path": str(crec_path), "T": T, "thresholds": thresholds}

    crec = C.load_record(crec_path, dhash)
    calibrated = crec is not None and subj.engine.caps.probs
    if crec is not None and subj.adapter == "linear" and crec.get(
            "model_file_sha256") != subj.engine.model_sha.get(spec.id):
        calibrated, crec = False, None
        report["calibration_note"] = "linear model changed since calibration: uncalibrated"
    T = float(crec["T"]) if calibrated else 1.0
    thresholds = (crec or {}).get("thresholds") if calibrated else None
    scaled = {s: apply_T(rows, T) for s, rows in samples.items()}
    report["calibrated"] = calibrated
    report["splits"] = {s: split_metrics(spec, scaled[s], thresholds, calibrated)
                        for s in eval_splits}

    # Paired comparisons against the incumbent (rules) and tier-0 (linear).
    comparisons: dict = {}
    for other in [x for x in (a.compare or "").split(",") if x and x != a.engine]:
        try:
            osc = Scorer(cfg, spec, other)
            await osc.attach(probe=False)
            if osc.adapter == "linear" and (osc.lock is None or not osc.lock.ok):
                comparisons[other] = {"skipped": "no linear model"}
                continue
            ohash = osc.dhash()
            orec = C.load_record(C.calib_path(cfg.records_dir, spec.id, ohash), ohash)
            oT = float(orec["T"]) if orec and osc.engine.caps.probs else 1.0
        except SystemExit as exc:
            comparisons[other] = {"skipped": str(exc)}
            continue
        comparisons[other] = {}
        for s in eval_splits:
            orows = apply_T(await osc.score_rows(data[s]), oT)
            a_ok, b_ok = M.correct(scaled[s]), M.correct(orows)
            comparisons[other][s] = {
                "mcnemar": M.mcnemar_exact(a_ok, b_ok),
                "delta_nll": M.paired_bootstrap_delta(
                    [M.row_nll(r, spec.label_names) for r in scaled[s]],
                    [M.row_nll(r, spec.label_names) for r in orows], n_boot=500),
                "other_accuracy": M.accuracy(orows)}
        # composite splits the gate might ask for
        for crit in spec.gate.criteria:
            if crit.metric == "mcnemar_p_vs" and crit.label == other and "+" in crit.split:
                mine = concat(scaled, crit.split)
                theirs = []
                for s in crit.split.split("+"):
                    theirs.extend(apply_T(await osc.score_rows(data.get(s, [])), oT))
                comparisons[other][crit.split] = {
                    "mcnemar": M.mcnemar_exact(M.correct(mine), M.correct(theirs))}
        await osc.engine.aclose()
    report["comparisons"] = comparisons
    if mech_dropped:
        report["mechanical_excluded"] = mech_dropped

    if a.gate:
        if not calibrated:
            raise SystemExit("no calibration record for this decision_hash — run --calibrate")
        load = None
        load_meta = None
        if a.load_report:
            lp = Path(a.load_report)
            load = json.loads(lp.read_text())
            load_meta = {"path": str(lp), "sha256": sha256_file(lp)}
        # score splits the gate needs that were not evaluated above
        for crit in spec.gate.criteria:
            for s in crit.split.split("+"):
                if s in SPLITS and s not in scaled:
                    scaled[s] = apply_T(await subj.score_rows(data[s]), T)
        crits, passed = [], True
        for crit in spec.gate.criteria:
            rows = concat(scaled, crit.split) if crit.split != "load" else []
            val, ci, note = metric_value(spec, crit, rows, thresholds, calibrated, comparisons,
                                         load, subj.adapter)
            ok = (note or "").startswith("not_applicable") or (
                val is not None and not (isinstance(val, float) and math.isnan(val))
                and C.compare(val, crit.op, crit.value))
            empty = [p for p in crit.split.split("+") if p in SPLITS and not scaled.get(p)]
            if empty and crit.split != "load":
                # "test+ood+adversarial" with no ood rows must not quietly
                # become "test+adversarial".
                ok, note = False, f"empty split(s): {','.join(empty)}"
            passed = passed and ok
            crits.append({"metric": crit.metric, "label": crit.label, "split": crit.split,
                          "value": val, "op": crit.op, "threshold": crit.value, "ci95": ci,
                          "pass": ok, "note": note})
        min_n = {s: {"need": n, "have": len(data.get(s, [])) if s != "load" else None}
                 for s, n in spec.gate.min_n.items()}
        min_n_met = all(v["have"] is None or v["have"] >= v["need"] for v in min_n.values())
        passed = passed and min_n_met
        integrity = gate_integrity(spec, manifest, subj, a)
        passed = passed and all(i["ok"] for i in integrity)
        calib_sha = C.file_sha256(crec_path)
        grec = {"decision": spec.id, "decision_hash": dhash, "engine": a.engine,
                "passed": passed, "criteria": crits, "min_n": min_n, "min_n_met": min_n_met,
                "calib_sha256": calib_sha, "calib_hash": calib_sha,
                "samples_sha256": samples_sha, "git_sha": report["git_sha"],
                "dataset_version": report["dataset_version"],
                "dataset_status": report["dataset_status"],
                "integrity": integrity, "load_report": load_meta,
                "created_at": report["created_at"]}
        gpath = C.gate_path(cfg.records_dir, spec.id, dhash)
        C.write_record(gpath, grec)
        report["gate"] = {"path": str(gpath), "passed": passed, "min_n_met": min_n_met,
                          "criteria": crits, "integrity": integrity}

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(a.out_dir) / spec.id / ts
    out.mkdir(parents=True, exist_ok=True)
    (out / "samples.jsonl").write_text(samples_blob)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True,
                                                default=str) + "\n")
    txt = render_text(report)
    (out / "report.txt").write_text(txt)
    report["run_dir"] = str(out)
    await subj.engine.aclose()
    if a.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(txt)
        print(f"run dir: {out}")
    return 0


def _f(v, nd=3):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def render_text(r: dict) -> str:
    lines = [f"decision {r['decision']}  engine {r['engine']} ({r['adapter']})  "
             f"hash {str(r.get('decision_hash'))[:16]}",
             f"dataset {r['dataset_version']} [{r['dataset_status']}]  "
             f"calibrated={r.get('calibrated')}  git {r['git_sha'][:12]}"]
    if r.get("dataset_status") != "gated":
        lines.append("NOTE: dataset is not a frozen, human-labelled gate set — numbers are "
                     "plumbing/seed-level only.")
    for s, m in (r.get("splits") or {}).items():
        if not m.get("n"):
            lines.append(f"  {s:12s} n=0")
            continue
        lo, hi = m["accuracy_ci95"]
        lines.append(f"  {s:12s} n={m['n']:<4d} acc={_f(m['accuracy'])} [{_f(lo)},{_f(hi)}] "
                     f"F1={_f(m['macro_f1'])} NLL={_f(m['nll'])} Brier={_f(m['brier'])} "
                     f"ECE={_f(m['ece'])}{'*' if m['ece_indicative'] else ''} "
                     f"AURC={_f(m['aurc']['aurc'])} abstain={_f(m['abstain_rate'])}")
        for lb, st in m["act"].items():
            lines.append(f"      act[{lb}] thr={st['threshold']} acts={st['acts']} "
                         f"errors={st['act_errors']} coverage={_f(st['act_coverage'])} "
                         f"precision={_f(st['act_precision'])}")
    for other, cmp in (r.get("comparisons") or {}).items():
        if "skipped" in cmp:
            lines.append(f"  vs {other}: skipped ({cmp['skipped']})")
            continue
        for s, c in cmp.items():
            if "mcnemar" in c:
                mc = c["mcnemar"]
                lines.append(f"  vs {other} [{s}]: b={mc['b']} c={mc['c']} p={_f(mc['p'])} "
                             f"other_acc={_f(c.get('other_accuracy'))}")
    if "gate" in r:
        g = r["gate"]
        lines.append(f"GATE passed={g['passed']} min_n_met={g['min_n_met']}")
        for i in g.get("integrity") or []:
            lines.append(f"  {'PASS' if i['ok'] else 'FAIL'} {i['check']}  ({i['detail']})")
        for c in g["criteria"]:
            lines.append(f"  {'PASS' if c['pass'] else 'FAIL'} {c['metric']}"
                         f"{'[' + c['label'] + ']' if c['label'] else ''}@{c['split']} "
                         f"{_f(c['value'])} {c['op']} {c['threshold']}"
                         f"{'  (' + c['note'] + ')' if c['note'] else ''}")
    lines.append("  * = ECE indicative (n < 150, 5 bins)")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="beast-instinct decision-quality harness")
    ap.add_argument("--decision", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--split", choices=("calib", "test", "ood", "adversarial"))
    ap.add_argument("--fit-linear", action="store_true")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--load-report")
    ap.add_argument("--compare", default="rules,linear")
    ap.add_argument("--config", default=None)
    ap.add_argument("--data-dir", default=str(HERE))
    ap.add_argument("--records-dir", default=None)
    ap.add_argument("--out-dir", default=str(REPO / ".run" / "instinct" / "eval"))
    ap.add_argument("--no-probe", action="store_true", help="skip the LLM conformance probe")
    ap.add_argument("--json", action="store_true")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return asyncio.run(main_async(a))
    except DatasetError as exc:
        print(f"dataset error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
