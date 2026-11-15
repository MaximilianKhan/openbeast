#!/usr/bin/env python3
"""Paired-arm delta report for v5 profiles (brief §5.2-§5.4).

Promoted from scratch/tier3_verdict.py + scratch/row_validity.py. Input is
two results files of the SAME model, session and era — typically two arms
of one profile (`eval-<slug>-L0-<ts>.json`, `eval-<slug>-L1-<ts>.json`,
same harness.pair_id) — paired by unit id.

Per contrast and language it reports:
  n, passes in each arm, b (rescues: fail -> pass), c (regressions:
  pass -> fail), net = b - c, the exact two-sided McNemar p, and
  `within_churn` when |net| is below the minimum detectable net effect for
  that n and the language's untreated churn (MDE, §5.2) — a single profile
  is underpowered for anything smaller, so such deltas are descriptive;
  plus the rescued and regressed unit ids.

Validity. A pair is dropped (and listed) when either row is not a model
verdict: an infrastructure reason (setup_failed, server_error, env_error,
validator_timeout, skipped_cache_miss, low_disk, server_unhealthy), a
signal death (exit < -1), a zero-token failure with a normal exit (a dead
server), API errors, a fork/thread EAGAIN validator death, or a unit the
arm did not measure (not_in_arm, budget_skip).

Stratified L2 (the profile's first budget cut). Every L0 failure is rerun
and a seeded fraction f of L0 passes; the rest are `not_in_arm`. Rescues b
are exact (all L0 failures sampled). Regressions are estimated by
Horvitz-Thompson: c_hat = sum over sampled L0-passes that failed of 1/pi
(pi = f), with its variance, and the p-value is labelled `stratified`.

Co-primary readouts on units passed in BOTH arms: completion tokens-to-fix
and iterations-to-fix (sign test), diag latency p95 (ship clause <= 1 s),
and the diag exposure rate (units where the checker fired at all).

Scores per arm on measured units only: SOLVE / LANG (k>1 bases) / SCORE.
DeltaSCORE and DeltaLANG are provenance, never board columns.

Cross-model significance: `--pool A1 B1 A2 B2 ...` — a stratified sign test
over the pooled discordant pairs, one stratum per model pair.

usage:
  python3 evals/ablation.py A.json B.json [--json OUT]
  python3 evals/ablation.py --profile evals/results/profile-<slug>-<ts>.json
  python3 evals/ablation.py --pool A1.json B1.json A2.json B2.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, EVALS_DIR)

import scoring  # noqa: E402
import suite_manifest  # noqa: E402

ALPHA = 0.05
# Untreated churn per language (fraction of units that flip between two
# same-config runs): zig 30% measured at --jobs 4 even under greedy; the
# other compiled languages ~12%; all compiled ~15% (§5.2).
CHURN = {"zig": 0.30, "go": 0.12, "c": 0.12, "cpp": 0.12, "rust": 0.12,
         "python": 0.12, "shell": 0.15, "compiled": 0.15, "all": 0.15}
INVALID_REASONS = frozenset({"setup_failed", "server_unhealthy", "skipped_cache_miss",
                             "low_disk", "server_error", "env_error", "validator_timeout",
                             "not_in_arm", "budget_skip"})
EAGAIN_RE = re.compile(r"Resource temporarily unavailable|SystemResources|"
                       r"thread constructor failed|pthread_create failed|"
                       r"fork: retry|can't start new thread")
PROVENANCE_KEYS = (("harness", "era"), ("harness", "env_component"),
                   ("harness", "rb_component"), ("inference_engine", "build"),
                   ("harness", "greedy"))


# --- exact tests ---------------------------------------------------------------

def binom_cdf(k: int, n: int) -> float:
    """P(X <= k), X ~ Binomial(n, 1/2)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n


def mcnemar_p(b: int, c: int) -> float:
    """Exact two-sided McNemar (binomial sign test on discordant pairs)."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * binom_cdf(min(b, c), n))


def sign_test_p(wins: int, losses: int) -> float:
    return mcnemar_p(wins, losses)


def mde(n: int, q: float, r: int = 1, alpha: float = ALPHA, step: float = 0.5) -> float:
    """Minimum detectable NET effect per replicate (brief §5.2): the smallest
    total net d (in `step`s) at which the exact McNemar test on EXPECTED
    discordant counts rejects at `alpha` — about 50% power. Null discordant
    pairs D0 = q*n*r split evenly; the effect adds d rescues:
    b = round(D0/2 + d), c = round(D0/2). Returned per replicate (d / r)."""
    d0 = q * n * r
    c = round(d0 / 2)
    d = 0.0
    while d <= n * r:
        b = round(d0 / 2 + d)
        if mcnemar_p(b, c) < alpha:
            return round(d / r, 2)
        d += step
    return float("inf")


# --- row validity ---------------------------------------------------------------

def invalid_reason(row: dict | None) -> str | None:
    """Why a row is not a model verdict, or None (valid)."""
    if row is None:
        return "missing"
    if row.get("reason") in INVALID_REASONS:
        return row["reason"]
    rc = row.get("agent_exit_code")
    if rc is not None and rc < -1:
        return f"signal death (exit {rc})"
    if (not row.get("passed") and not row.get("from_cache") and not row.get("shared_from")
            and rc is not None and rc >= 0 and not (row.get("tokens_completion") or 0)):
        return "zero-token fail (dead server)"
    if (row.get("api_errors") or 0) > 0 and not row.get("passed"):
        return "api errors"
    if (not row.get("passed") and not row.get("env_error_repeats")
            and EAGAIN_RE.search(str(row.get("validation_output") or ""))):
        return "EAGAIN validator death"
    return None


def tokens_unrecorded(row: dict) -> bool:
    return row.get("agent_exit_code") == -1 or not (row.get("tokens_completion") or 0)


def lang_of(row: dict) -> str:
    return suite_manifest.norm_lang(row.get("language"))


# --- pairing ----------------------------------------------------------------------

def provenance_mismatches(a: dict, b: dict) -> list[str]:
    out = []
    for sec, key in PROVENANCE_KEYS:
        va, vb = (a.get(sec) or {}).get(key), (b.get(sec) or {}).get(key)
        if va != vb:
            out.append(f"{sec}.{key}: {va!r} vs {vb!r}")
    pa, pb = (a.get("harness") or {}).get("pair_id"), (b.get("harness") or {}).get("pair_id")
    if pa and pb and pa != pb:
        out.append(f"pair_id: {pa} vs {pb} (different profile sessions)")
    if a.get("model_slug") != b.get("model_slug"):
        out.append(f"model: {a.get('model_slug')} vs {b.get('model_slug')}")
    return out


def stratified_pi(results: dict) -> dict:
    """{unit id: inclusion probability} when the arm was stratified."""
    meta = ((results.get("harness") or {}).get("arm_meta") or {})
    if not meta.get("stratified"):
        return {}
    return {u: float(p) for u, p in (meta.get("pi") or {}).items()}


def pair(a: dict, b: dict) -> tuple[list, list, list]:
    """(pairs [(uid, row_a, row_b)], dropped [(uid, why)], unsampled [(uid, row_a)]).
    Probe rows pair like any other (their stratum is reported separately);
    rows of a stratified arm that were not sampled go to `unsampled`."""
    ra = {t["id"]: t for t in a.get("tasks", [])}
    rb = {t["id"]: t for t in b.get("tasks", [])}
    pi = stratified_pi(b)
    pairs, dropped, unsampled = [], [], []
    for uid in sorted(set(ra) | set(rb)):
        xa, xb = ra.get(uid), rb.get(uid)
        if pi and xb is not None and xb.get("reason") == "not_in_arm" \
                and invalid_reason(xa) is None:
            unsampled.append((uid, xa))
            continue
        why = invalid_reason(xa) or invalid_reason(xb)
        if why:
            dropped.append((uid, why))
            continue
        pairs.append((uid, xa, xb))
    return pairs, dropped, unsampled


# --- contrasts ------------------------------------------------------------------------

def contrast(pairs: list, label: str, q: float, r: int = 1,
             unsampled: list | None = None, pi: dict | None = None) -> dict:
    """One contrast over the given pairs (already filtered to its units)."""
    a_pass = sum(1 for _u, xa, _xb in pairs if xa.get("passed"))
    b_pass = sum(1 for _u, _xa, xb in pairs if xb.get("passed"))
    rescued = sorted(u for u, xa, xb in pairs if not xa.get("passed") and xb.get("passed"))
    regressed = sorted(u for u, xa, xb in pairs if xa.get("passed") and not xb.get("passed"))
    b, c = len(rescued), len(regressed)
    n = len(pairs) + len(unsampled or [])
    out = {"contrast": label, "n": n, "a_pass": a_pass, "b_pass": b_pass,
           "b": b, "c": c, "net": b - c, "rescued": rescued, "regressed": regressed,
           "churn_q": q}
    if pi:
        # Horvitz-Thompson: regressions can only come from L0 passes, which
        # were sampled with probability pi; rescues come from L0 failures,
        # all sampled (pi = 1), so b is exact.
        weights = [1.0 / pi.get(u, 1.0) for u in regressed]
        c_hat = sum(weights)
        var = sum((1 - pi.get(u, 1.0)) / pi.get(u, 1.0) ** 2 for u in regressed)
        out.update({"c_hat": round(c_hat, 2), "c_hat_se": round(math.sqrt(var), 2),
                    "net": round(b - c_hat, 2), "p": mcnemar_p(b, int(round(c_hat))),
                    "p_label": "stratified", "sampled": len(pairs),
                    "unsampled": len(unsampled or [])})
    else:
        out.update({"p": mcnemar_p(b, c), "p_label": "exact"})
    out["mde"] = mde(n, q, r) if n else float("inf")
    out["within_churn"] = abs(out["net"]) < out["mde"]
    return out


def cost_to_fix(pairs: list) -> dict:
    """Tokens and iterations on units passed in BOTH arms (sign test: does B
    fix with fewer?). Rows whose counts are not a measurement are skipped."""
    both = [(u, xa, xb) for u, xa, xb in pairs if xa.get("passed") and xb.get("passed")
            and not xb.get("shared_from")]
    res = {"both_passed_measured": len(both)}
    for key, unrec in (("tokens_completion", tokens_unrecorded),
                       ("iterations", lambda x: x.get("iterations") is None)):
        d = [(xb.get(key) or 0) - (xa.get(key) or 0) for _u, xa, xb in both
             if not unrec(xa) and not unrec(xb)]
        wins, losses = sum(x < 0 for x in d), sum(x > 0 for x in d)
        res[key] = {"n": len(d), "fewer_in_b": wins, "more_in_b": losses,
                    "median_delta": sorted(d)[len(d) // 2] if d else None,
                    "p": sign_test_p(wins, losses)}
    return res


def arm_scores(results: dict) -> dict:
    rows = [t for t in scoring.scored_rows(results.get("tasks", []))
            if invalid_reason(t) is None]
    solve, lang, cap = scoring.compute_solve_breadth(rows, lang_multi_only=True)
    return {"n": len(rows), "solve": solve, "lang": lang, "score": cap}


def exposure(results: dict) -> dict | None:
    h = results.get("harness") or {}
    exp = h.get("diag_exposure")
    if exp is None:
        return None
    ran = sum(1 for t in results.get("tasks", [])
              if not t.get("shared_from") and not t.get("from_cache")
              and t.get("reason") not in INVALID_REASONS
              and lang_of(t) in set(h.get("diag_scope") or ()))
    return {"units_exposed": exp.get("units_exposed", 0), "units_run_in_scope": ran,
            "rate": round(exp.get("units_exposed", 0) / ran, 3) if ran else None,
            "units_hint_fired": exp.get("units_hint_fired"),
            "units_card_fired": exp.get("units_card_fired")}


def report(a: dict, b: dict, r: int = 1) -> dict:
    """Every contrast the brief asks for between arm A and arm B."""
    pairs, dropped, unsampled = pair(a, b)
    pi = stratified_pi(b)
    by_lang = defaultdict(list)
    for p in pairs:
        by_lang[lang_of(p[1])].append(p)
    uns_lang = defaultdict(list)
    for u in unsampled:
        uns_lang[lang_of(u[1])].append(u)
    rows = []
    for lang in sorted(set(by_lang) | set(uns_lang)):
        rows.append(contrast(by_lang[lang], lang, CHURN.get(lang, CHURN["all"]), r,
                             uns_lang[lang], pi))
    compiled = [p for p in pairs if lang_of(p[1]) in suite_manifest.COMPILED_LANGS]
    if compiled:
        rows.append(contrast(compiled, "compiled", CHURN["compiled"], r,
                             [u for u in unsampled if lang_of(u[1]) in suite_manifest.COMPILED_LANGS],
                             pi))
    non_zig = [p for p in compiled if lang_of(p[1]) != "zig"]
    if non_zig:
        rows.append(contrast(non_zig, "go+c+cpp+rust", CHURN["go"], r,
                             [u for u in unsampled if lang_of(u[1]) in
                              suite_manifest.COMPILED_LANGS - {"zig"}], pi))
    # Pack-contrast strata (zig): held-out (new tasks the pack never saw)
    # vs in-sample (retained zig variants + probes).
    for stratum in ("heldout", "insample"):
        sp = [p for p in pairs if (p[1].get("stratum") or p[2].get("stratum")) == stratum]
        if sp:
            rows.append(contrast(sp, f"zig:{stratum}", CHURN["zig"], r,
                                 [u for u in unsampled if u[1].get("stratum") == stratum], pi))
    ha, hb = a.get("harness") or {}, b.get("harness") or {}
    sa, sb = arm_scores(a), arm_scores(b)
    return {
        "a": {"file_arm": ha.get("arm"), "label": ha.get("arm_label"), "scores": sa},
        "b": {"file_arm": hb.get("arm"), "label": hb.get("arm_label"), "scores": sb},
        "model": a.get("model_slug"),
        "provenance_mismatch": provenance_mismatches(a, b),
        "dropped": dropped,
        "contrasts": rows,
        "cost_to_fix": cost_to_fix(pairs),
        "diag_latency_ms": hb.get("diag_latency_ms"),
        "diag_latency_ship_ok": (hb.get("diag_latency_ms") or {}).get("p95", 0) <= 1000
        if hb.get("diag_latency_ms") else None,
        "exposure": exposure(b),
        "delta_provenance": {"score": round(sb["score"] - sa["score"], 2),
                             "lang": round(sb["lang"] - sa["lang"], 2)},
    }


def pool(pairs_of_files: list[tuple[dict, dict]], langs: set | None = None) -> dict:
    """Stratified sign test over pooled discordant pairs across models: each
    (A, B) is one stratum; the discordant pairs add up; one exact test."""
    strata = []
    for a, b in pairs_of_files:
        ps, _d, _u = pair(a, b)
        if langs:
            ps = [p for p in ps if lang_of(p[1]) in langs]
        bb = sum(1 for _u2, xa, xb in ps if not xa.get("passed") and xb.get("passed"))
        cc = sum(1 for _u2, xa, xb in ps if xa.get("passed") and not xb.get("passed"))
        strata.append({"model": a.get("model_slug"), "n": len(ps), "b": bb, "c": cc})
    b = sum(s["b"] for s in strata)
    c = sum(s["c"] for s in strata)
    return {"strata": strata, "b": b, "c": c, "net": b - c, "p": mcnemar_p(b, c),
            "p_label": "pooled sign test"}


# --- text ------------------------------------------------------------------------------

def render(rep: dict) -> str:
    a, b = rep["a"], rep["b"]
    lines = [f"ablation {rep['model']}: {a['file_arm'] or 'A'} ({a['label']}) -> "
             f"{b['file_arm'] or 'B'} ({b['label']})"]
    if rep["provenance_mismatch"]:
        lines.append("  NOT A CLEAN PAIR: " + "; ".join(rep["provenance_mismatch"]))
    lines.append(f"  {'contrast':<16} {'n':>4} {'A':>4} {'B':>4} {'b':>3} {'c':>6} "
                 f"{'net':>6} {'p':>7} {'MDE':>5}  flag")
    for r in rep["contrasts"]:
        c = f"{r['c_hat']:.1f}" if "c_hat" in r else str(r["c"])
        flag = "within_churn" if r["within_churn"] else ""
        if r["p_label"] == "stratified":
            flag = (flag + " stratified").strip()
        lines.append(f"  {r['contrast']:<16} {r['n']:>4} {r['a_pass']:>4} {r['b_pass']:>4} "
                     f"{r['b']:>3} {c:>6} {r['net']:>6} {r['p']:>7.4f} {r['mde']:>5}  {flag}")
        if r["rescued"]:
            lines.append(f"    rescued:   {', '.join(r['rescued'][:12])}")
        if r["regressed"]:
            lines.append(f"    regressed: {', '.join(r['regressed'][:12])}")
    ctf = rep["cost_to_fix"]
    for key in ("tokens_completion", "iterations"):
        x = ctf[key]
        lines.append(f"  {key}-to-fix (both passed, n={x['n']}): fewer in B {x['fewer_in_b']}, "
                     f"more {x['more_in_b']}, median delta {x['median_delta']}, p {x['p']:.3f}")
    if rep["diag_latency_ms"]:
        lat = rep["diag_latency_ms"]
        lines.append(f"  diag latency p95 {lat['p95']:.0f} ms "
                     f"({'ship clause OK' if rep['diag_latency_ship_ok'] else 'OVER 1 s'})")
    if rep["exposure"]:
        e = rep["exposure"]
        lines.append(f"  diag exposure: {e['units_exposed']}/{e['units_run_in_scope']} units "
                     f"(hint fired {e['units_hint_fired']}, card fired {e['units_card_fired']})")
    lines.append(f"  scores on measured units: A {a['scores']['score']} "
                 f"(solve {a['scores']['solve']}, lang {a['scores']['lang']}) | "
                 f"B {b['scores']['score']} (solve {b['scores']['solve']}, lang {b['scores']['lang']})"
                 f" — delta in provenance only, never a board column")
    if rep["dropped"]:
        lines.append(f"  dropped {len(rep['dropped'])} invalid pair(s): "
                     + ", ".join(f"{u} ({w})" for u, w in rep["dropped"][:8]))
    lines.append("  within_churn = |net| below the MDE for this n and churn: descriptive only")
    return "\n".join(lines)


def _load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--profile", help="a profile state file (evals/profile.py)")
    ap.add_argument("--pool", action="store_true",
                    help="files are A1 B1 A2 B2 ...: pooled stratified sign test")
    ap.add_argument("--langs", help="--pool: restrict to these languages (comma list)")
    ap.add_argument("--replicates", type=int, default=1)
    ap.add_argument("--json", help="write the report as JSON here")
    ap.add_argument("--force", action="store_true",
                    help="report even when the two files are not a clean pair")
    a = ap.parse_args(argv)

    if a.pool:
        if len(a.files) < 2 or len(a.files) % 2:
            ap.error("--pool needs pairs: A1 B1 A2 B2 ...")
        pairs = [(_load(a.files[i]), _load(a.files[i + 1])) for i in range(0, len(a.files), 2)]
        langs = {suite_manifest.norm_lang(x) for x in a.langs.split(",")} if a.langs else None
        res = pool(pairs, langs)
        for s in res["strata"]:
            print(f"  {s['model']:<34} n {s['n']:>4}  b {s['b']:>3}  c {s['c']:>3}")
        print(f"pooled: b {res['b']} c {res['c']} net {res['net']:+d} p {res['p']:.4f} "
              f"({res['p_label']})")
        if a.json:
            with open(a.json, "w") as f:
                json.dump(res, f, indent=1)
        return 0

    reports = []
    if a.profile:
        st = _load(a.profile)
        paths = st.get("results") or {}
        order = [x for x in ("L0", "L1", "L2") if x in paths]
        # The brief's contrasts: L1-L0 (pack), L2-L1 (diag+esc given the
        # pack), L2-L0 (go/c/cpp/rust diag; python guard).
        for x, y in (("L0", "L1"), ("L1", "L2"), ("L0", "L2")):
            if x in order and y in order:
                reports.append(report(_load(paths[x]), _load(paths[y]), a.replicates))
    else:
        if len(a.files) != 2:
            ap.error("give two results files (A B), --profile, or --pool")
        reports.append(report(_load(a.files[0]), _load(a.files[1]), a.replicates))
    bad = 0
    for rep in reports:
        print(render(rep))
        print()
        if rep["provenance_mismatch"] and not a.force:
            bad = 1
    if a.json:
        with open(a.json, "w") as f:
            json.dump(reports if len(reports) > 1 else reports[0], f, indent=1, default=str)
    if bad:
        print("At least one pair crosses an era/env/engine/session boundary: its deltas mix "
              "two experiments. Re-run both arms in one session, or pass --force.")
    return bad


if __name__ == "__main__":
    sys.exit(main())
