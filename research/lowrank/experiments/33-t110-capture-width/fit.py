#!/usr/bin/env python3
"""E33 / T1.10 — fit capture(r, d) forms to the per-tensor points from
collect_points.py, with cluster (per-tensor) bootstrap CIs, AIC/BIC, and
held-out-SCALE prediction error.

Forms (capture c, rank r, whitened input width d = d_in unless --dvar):
  (a) linear     c = k · (r/d)                       [the §4.2 "r/d rule"]
  (b) power      c = a · (r/d)^b
  (c) saturating c = r / (r + k·d)                   [1/c = 1 + k·d/r]
  (c') sat+int   1/c = α + k·(d/r)
  (d) two-exp    log c = α + b_r·log r + b_d·log d   [r/d ⇔ b_r = −b_d]
  (e) head+bulk  c = h + (1−h)·(r/d)^b               [constant-cardinality
                                                       outlier head + bulk]
Likelihood for AIC/BIC: Gaussian on log(capture) (multiplicative errors),
same observation set for every form.  Fits are least squares on log c for
(b),(d),(e) and on c for (a),(c),(c') (each reported with its RMSE on c AND
its AIC on log c, so the comparison is on one scale).

Usage: fit.py [--points points.csv] [--boot 1000] [--dvar d_in|d_out|d_min|d_geo]
              [--primary-only]
Outputs: fit_summary.txt, fit_results.json, fig_*.png
"""
import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

HERE = Path(__file__).resolve().parent
# "Primary" lanes: one Q2-class from-reference base per model, so that the
# width axis is not confounded by base choice. Everything else is the
# base/whitener axis (§4.2 caveat).
PRIMARY = {"q3-06b-Q2K-imat": "0.6B", "q35-08b-Q2K": "0.8B",
           "h27b-Q2K": "27B-heretic", "q38-27b-UD-Q2KXL": "27B-Qwen3.8"}
SCALE_OF = {"q3-06b": "0.6B", "q35-08b": "0.8B", "h27b": "27B-heretic",
            "q38-27b": "27B-Qwen3.8"}


def load(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            for k in ("d_model", "layer", "d_in", "d_out", "r"):
                r[k] = int(r[k])
            r["capture"] = float(r["capture"])
            r["scale_id"] = next(v for k, v in SCALE_OF.items()
                                 if r["config"].startswith(k))
            # collapse blocked-fc into fc but keep the flag
            r["wh"] = "fc" if r["whitener"].startswith("fc") else r["whitener"]
            rows.append(r)
    return rows


def dval(r, dvar):
    if dvar == "d_in":
        return r["d_in"]
    if dvar == "d_out":
        return r["d_out"]
    if dvar == "d_min":
        return min(r["d_in"], r["d_out"])
    return math.sqrt(r["d_in"] * r["d_out"])


# ----------------------------------------------------------------- forms
def fit_linear(x, c):            # c = k x  (LS on c)
    k = float((x * c).sum() / (x * x).sum())
    return dict(k=k), k * x


def fit_power(x, c):             # log c = log a + b log x
    A = np.c_[np.ones_like(x), np.log(x)]
    coef, *_ = np.linalg.lstsq(A, np.log(c), rcond=None)
    return dict(a=float(np.exp(coef[0])), b=float(coef[1])), np.exp(A @ coef)


def fit_sat(x, c):               # 1/c = 1 + k/x   (LS on c)
    def res(p):
        return (x / (x + p[0])) - c
    p = least_squares(res, [0.5], bounds=(1e-9, np.inf)).x
    return dict(k=float(p[0])), x / (x + p[0])


def fit_sat_int(x, c):           # 1/c = α + k/x  (LS on c)
    def res(p):
        return 1.0 / (p[0] + p[1] / x) - c
    p = least_squares(res, [1.0, 0.5], bounds=([1e-6, 1e-9], [np.inf, np.inf])).x
    return dict(alpha=float(p[0]), k=float(p[1])), 1.0 / (p[0] + p[1] / x)


def fit_twoexp(r, d, c):         # log c = α + b_r log r + b_d log d
    A = np.c_[np.ones_like(r), np.log(r), np.log(d)]
    coef, *_ = np.linalg.lstsq(A, np.log(c), rcond=None)
    return dict(alpha=float(coef[0]), b_r=float(coef[1]),
                b_d=float(coef[2])), np.exp(A @ coef)


def fit_headbulk(x, c):          # c = h + (1-h) x^b   (LS on log c)
    def res(p):
        h, b = p
        return np.log(np.clip(h + (1 - h) * x ** b, 1e-9, None)) - np.log(c)
    p = least_squares(res, [0.05, 1.0], bounds=([0, 0.05], [0.95, 3])).x
    return dict(h=float(p[0]), b=float(p[1])), p[0] + (1 - p[0]) * x ** p[1]


def ic(c, pred, k):
    """AIC/BIC with Gaussian errors on log c; plus RMSE on c."""
    e = np.log(c) - np.log(np.clip(pred, 1e-12, None))
    n = len(e); s2 = float((e ** 2).mean())
    ll = -0.5 * n * (math.log(2 * math.pi * s2) + 1)
    return dict(aic=2 * k - 2 * ll, bic=k * math.log(n) - 2 * ll,
                rmse_c=float(np.sqrt(((pred - c) ** 2).mean())),
                rmse_logc=float(np.sqrt(s2)), n=n)


FORMS = {
    "a_linear_r/d": (lambda r, d, c: fit_linear(r / d, c), 1),
    "b_power_(r/d)^b": (lambda r, d, c: fit_power(r / d, c), 2),
    "c_sat_r/(r+kd)": (lambda r, d, c: fit_sat(r / d, c), 1),
    "c2_sat_intercept": (lambda r, d, c: fit_sat_int(r / d, c), 2),
    "d_two_exponent": (lambda r, d, c: fit_twoexp(r, d, c), 3),
    "e_head+bulk": (lambda r, d, c: fit_headbulk(r / d, c), 2),
}


def fit_all(r, d, c):
    out = {}
    for name, (fn, k) in FORMS.items():
        try:
            par, pred = fn(r, d, c)
            out[name] = dict(params=par, **ic(c, pred, k))
        except Exception as e:  # pragma: no cover
            out[name] = dict(error=str(e))
    return out


def cluster_boot(rows, nboot, rng, stat_fn):
    """Resample TENSORS (all ranks of a tensor move together), stratified by
    scale_id so each model keeps its tensor count."""
    by_scale = defaultdict(lambda: defaultdict(list))
    for i, x in enumerate(rows):
        by_scale[x["scale_id"]][x["tensor"]].append(i)
    groups = {s: list(t.values()) for s, t in by_scale.items()}
    stats = []
    for _ in range(nboot):
        idx = []
        for s, tl in groups.items():
            pick = rng.integers(0, len(tl), len(tl))
            for j in pick:
                idx.extend(tl[j])
        stats.append(stat_fn([rows[i] for i in idx]))
    return stats


def arrays(rows, dvar):
    r = np.array([x["r"] for x in rows], float)
    d = np.array([dval(x, dvar) for x in rows], float)
    c = np.array([x["capture"] for x in rows], float)
    return r, d, c


def ci(vals, q=(2.5, 97.5)):
    v = np.array([x for x in vals if x is not None and np.isfinite(x)])
    return [float(np.percentile(v, q[0])), float(np.percentile(v, q[1]))] if len(v) else [None, None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", default="/home/max/Documents/openbeast/research/lowrank/data/spectra-e33/points.csv")
    ap.add_argument("--boot", type=int, default=1000)
    ap.add_argument("--dvar", default="d_in",
                    choices=["d_in", "d_out", "d_min", "d_geo"])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--qwen38-lane", default="q38-27b-UD-Q2KXL",
                    help="which Qwen3.8 config is the primary lane "
                         "(addendum: q38-27b-Q2K-ours = same recipe as heretic)")
    ap.add_argument("--tag", default="", help="suffix for output files")
    args = ap.parse_args()
    PRIMARY.pop("q38-27b-UD-Q2KXL", None)
    PRIMARY[args.qwen38_lane] = "27B-Qwen3.8"
    rng = np.random.default_rng(args.seed)
    rows = load(args.points)
    rows = [x for x in rows if x["capture"] > 0]
    prim = [x for x in rows if x["config"] in PRIMARY]
    out = {"dvar": args.dvar, "n_rows": len(rows), "n_primary_rows": len(prim),
           "scales_present": sorted(set(x["scale_id"] for x in prim)),
           "d_values_primary": {}, "fits": {}, "boot": {}, "heldout": {},
           "c_table": {}, "same_d": {}, "per_kind": {}, "dvar_compare": {}}
    lines = []
    P = lines.append
    P(f"# T1.10 capture-vs-width fit — dvar={args.dvar}, boot={args.boot}")
    P(f"rows={len(rows)} primary rows={len(prim)} "
      f"scales={out['scales_present']}")
    for s in out["scales_present"]:
        ds = sorted(set(dval(x, args.dvar) for x in prim if x["scale_id"] == s))
        nt = len(set(x["tensor"] for x in prim if x["scale_id"] == s))
        out["d_values_primary"][s] = dict(d=ds, n_tensors=nt)
        P(f"  {s}: {nt} tensors, {args.dvar} ∈ {ds}")
    P("NOTE: width leverage: 0.6B/0.8B (d_model 1024) vs the two 27B models "
      "(d_model 5120 — SAME d, so they test architecture/base, not width). "
      "Within a model, d varies by tensor kind (ffn_down / attn_o / ssm_out "
      "inputs are wider), which adds d-leverage but is confounded with kind.")

    # ---------------- 1. pooled fits per whitener (primary lanes) -----------
    for wh in ("diag", "fc", "none"):
        sub = [x for x in prim if x["wh"] == wh]
        if not sub:
            continue
        r, d, c = arrays(sub, args.dvar)
        fits = fit_all(r, d, c)
        out["fits"][wh] = fits
        P(f"\n## whitener={wh}: n={len(sub)} points, "
          f"{len(set(x['tensor']+x['config'] for x in sub))} tensors")
        best = min((v["aic"], k) for k, v in fits.items() if "aic" in v)[1]
        for k, v in fits.items():
            if "aic" in v:
                P(f"  {k:20s} params={json.dumps({a: round(b, 4) for a, b in v['params'].items()})} "
                  f"rmse_c={v['rmse_c']:.4f} rmse_logc={v['rmse_logc']:.3f} "
                  f"AIC={v['aic']:.0f} BIC={v['bic']:.0f}"
                  + ("  <-- best AIC" if k == best else ""))
        # bootstrap the key parameters
        def stat(rs):
            rr, dd, cc = arrays(rs, args.dvar)
            f = fit_all(rr, dd, cc)
            return dict(k_lin=f["a_linear_r/d"]["params"]["k"],
                        b_pow=f["b_power_(r/d)^b"]["params"]["b"],
                        a_pow=f["b_power_(r/d)^b"]["params"]["a"],
                        b_r=f["d_two_exponent"]["params"]["b_r"],
                        b_d=f["d_two_exponent"]["params"]["b_d"],
                        h=f["e_head+bulk"]["params"]["h"],
                        b_hb=f["e_head+bulk"]["params"]["b"],
                        k_sat=f["c_sat_r/(r+kd)"]["params"]["k"])
        bs = cluster_boot(sub, args.boot, rng, stat)
        out["boot"][wh] = {k: ci([b[k] for b in bs]) for k in bs[0]}
        P("  bootstrap 95% CI (cluster=tensor, stratified by model): "
          + ", ".join(f"{k}=[{v[0]:.3f},{v[1]:.3f}]"
                      for k, v in out["boot"][wh].items()))
        b_r, b_d = fits["d_two_exponent"]["params"]["b_r"], fits["d_two_exponent"]["params"]["b_d"]
        sums = [b["b_r"] + b["b_d"] for b in bs]
        out["boot"][wh]["b_r+b_d"] = ci(sums)
        P(f"  r/d test: b_r={b_r:.3f} b_d={b_d:.3f}; b_r+b_d={b_r+b_d:.3f} "
          f"CI=[{out['boot'][wh]['b_r+b_d'][0]:.3f},{out['boot'][wh]['b_r+b_d'][1]:.3f}] "
          f"(0 ⇔ capture depends on r and d only through r/d)")

        # ---------------- 2. held-out scale ---------------------------------
        scales = sorted(set(x["scale_id"] for x in sub))
        ho = {}
        for held in scales:
            tr = [x for x in sub if x["scale_id"] != held]
            te = [x for x in sub if x["scale_id"] == held]
            if len(set(x["scale_id"] for x in tr)) < 2:
                continue
            rt, dt, ct = arrays(tr, args.dvar)
            re_, de, ce = arrays(te, args.dvar)
            row = {}
            for name, (fn, k) in FORMS.items():
                par, _ = fn(rt, dt, ct)
                # re-evaluate on test with the fitted params
                if name == "a_linear_r/d":
                    pred = par["k"] * re_ / de
                elif name == "b_power_(r/d)^b":
                    pred = par["a"] * (re_ / de) ** par["b"]
                elif name == "c_sat_r/(r+kd)":
                    pred = re_ / (re_ + par["k"] * de)
                elif name == "c2_sat_intercept":
                    pred = 1.0 / (par["alpha"] + par["k"] * de / re_)
                elif name == "d_two_exponent":
                    pred = np.exp(par["alpha"] + par["b_r"] * np.log(re_) + par["b_d"] * np.log(de))
                else:
                    pred = par["h"] + (1 - par["h"]) * (re_ / de) ** par["b"]
                row[name] = dict(rmse_c=float(np.sqrt(((pred - ce) ** 2).mean())),
                                 rmse_logc=float(np.sqrt((np.log(pred.clip(1e-12)) - np.log(ce)) ** 2).mean()),
                                 mean_pred=float(pred.mean()), mean_obs=float(ce.mean()),
                                 ratio_obs_over_pred=float(ce.mean() / pred.mean()),
                                 trained_on=[s for s in scales if s != held])
            ho[held] = row
        out["heldout"][wh] = ho
        P("  held-out scale (fit on the others, predict this one) — "
          "mean obs/pred and RMSE(log c):")
        for held, row in ho.items():
            P(f"    hold {held:12s}: " + "  ".join(
                f"{k.split('_')[0]}: {v['ratio_obs_over_pred']:.2f}×/{v['rmse_logc']:.2f}"
                for k, v in row.items()))

    # ---------------- 3. implied c = capture·d/r per (model, base, whitener)
    P("\n## implied c = capture·d/r (the §4.2 'constant'), mean over tensors, "
      "r=64 and r=128; d=" + args.dvar)
    tab = defaultdict(list)
    for x in rows:
        if x["r"] in (64, 128):
            tab[(x["scale_id"], x["config"], x["base"], x["wh"], x["r"])].append(
                x["capture"] * dval(x, args.dvar) / x["r"])
    for key in sorted(tab):
        v = np.array(tab[key]); bs = [rng.choice(v, len(v)).mean() for _ in range(400)]
        lo, hi = np.percentile(bs, [2.5, 97.5])
        out["c_table"]["|".join(map(str, key))] = dict(c_mean=float(v.mean()), ci=[float(lo), float(hi)],
                                                        n=len(v), capture_mean=float(np.mean([
                                                            x["capture"] for x in rows if (x["scale_id"], x["config"], x["base"], x["wh"], x["r"]) == key])))
        P(f"  {key[0]:12s} {key[2]:12s} {key[3]:5s} r={key[4]:3d}: c={v.mean():6.2f} "
          f"[{lo:.2f},{hi:.2f}] (capture {out['c_table']['|'.join(map(str, key))]['capture_mean']:.3f}, n={len(v)})")

    # ---------------- 4. per kind, primary lanes ----------------------------
    P("\n## per tensor-kind power-law exponent b in capture=a(r/d)^b (primary lanes, pooled across models)")
    for wh in ("diag", "fc"):
        kinds = sorted(set(x["kind"] for x in prim if x["wh"] == wh))
        for kd in kinds:
            sub = [x for x in prim if x["wh"] == wh and x["kind"] == kd]
            sc = sorted(set(x["scale_id"] for x in sub))
            if len(sub) < 20:
                continue
            r, d, c = arrays(sub, args.dvar)
            f = fit_all(r, d, c)
            bs = cluster_boot(sub, min(args.boot, 300), rng,
                              lambda rs: fit_all(*arrays(rs, args.dvar))["d_two_exponent"]["params"])
            out["per_kind"][f"{wh}|{kd}"] = dict(
                n=len(sub), scales=sc, d_values=sorted(set(d.tolist())),
                power=f["b_power_(r/d)^b"]["params"], twoexp=f["d_two_exponent"]["params"],
                b_r_ci=ci([b["b_r"] for b in bs]), b_d_ci=ci([b["b_d"] for b in bs]))
            P(f"  {wh:4s} {kd:9s} n={len(sub):4d} d∈{sorted(set(int(v) for v in d))} "
              f"a={f['b_power_(r/d)^b']['params']['a']:.2f} b={f['b_power_(r/d)^b']['params']['b']:.2f} | "
              f"b_r={f['d_two_exponent']['params']['b_r']:.2f}{out['per_kind'][f'{wh}|{kd}']['b_r_ci']} "
              f"b_d={f['d_two_exponent']['params']['b_d']:.2f}{out['per_kind'][f'{wh}|{kd}']['b_d_ci']}")

    # ---------------- 5. same-d comparison: heretic vs Qwen3.8 --------------
    P("\n## same-width test: heretic-27B vs Qwen3.8-27B (both qwen35, d_model 5120), primary Q2 lanes, per kind, r=128")
    for wh in ("diag", "fc"):
        for kd in sorted(set(x["kind"] for x in prim if x["wh"] == wh)):
            a = [x["capture"] for x in prim if x["wh"] == wh and x["kind"] == kd and x["r"] == 128 and x["scale_id"] == "27B-heretic"]
            b = [x["capture"] for x in prim if x["wh"] == wh and x["kind"] == kd and x["r"] == 128 and x["scale_id"] == "27B-Qwen3.8"]
            if a and b:
                a, b = np.array(a), np.array(b)
                diff = [rng.choice(b, len(b)).mean() - rng.choice(a, len(a)).mean() for _ in range(400)]
                lo, hi = np.percentile(diff, [2.5, 97.5])
                out["same_d"][f"{wh}|{kd}"] = dict(heretic=float(a.mean()), qwen38=float(b.mean()),
                                                   diff_ci=[float(lo), float(hi)], n=[len(a), len(b)])
                P(f"  {wh:4s} {kd:9s}: heretic {a.mean():.3f} (n={len(a)})  Qwen3.8 {b.mean():.3f} (n={len(b)})  "
                  f"Δ=[{lo:+.3f},{hi:+.3f}]")

    # ---------------- 6. which d? --------------------------------------------
    P("\n## which width? AIC of the power law (fc/diag, primary) under d_in / d_out / d_min / d_geo")
    for wh in ("diag", "fc"):
        sub = [x for x in prim if x["wh"] == wh]
        if not sub:
            continue
        res = {}
        for dv in ("d_in", "d_out", "d_min", "d_geo"):
            r, d, c = arrays(sub, dv)
            f = fit_all(r, d, c)
            res[dv] = dict(aic=f["b_power_(r/d)^b"]["aic"], b=f["b_power_(r/d)^b"]["params"]["b"],
                           aic_2exp=f["d_two_exponent"]["aic"], b_d=f["d_two_exponent"]["params"]["b_d"])
        out["dvar_compare"][wh] = res
        P(f"  {wh}: " + "  ".join(f"{dv}: AIC={v['aic']:.0f} b={v['b']:.2f} (2exp b_d={v['b_d']:.2f})" for dv, v in res.items()))

    # ---------------- 7. §4.2 re-derived: mean capture per model at fixed (whitener, r)
    P("\n## §4.2 numbers re-derived (primary Q2-class lanes): mean capture over tensors, and the ratio to 0.6B")
    out["s42"] = {}
    for wh in ("diag", "fc"):
        for r in (64, 128):
            row = {}
            for s in out["scales_present"]:
                v = [x["capture"] for x in prim if x["wh"] == wh and x["r"] == r and x["scale_id"] == s]
                if v:
                    row[s] = float(np.mean(v))
            out["s42"][f"{wh}|{r}"] = row
            base = row.get("0.6B")
            P(f"  {wh:4s} r={r:3d}: " + "  ".join(f"{s}={m:.3f}" + (f" (×{m/base:.2f})" if base else "") for s, m in row.items()))
    P("  (§4.2: diag r64 0.37 @0.6B vs 0.07 @27B legacy Q6-ref pair — fivefold fall for fivefold d_model)")
    P("  c_model = mean capture · d_model / r  (the exact §4.2 arithmetic; §4.2 quotes c≈5.9):")
    out["c_dmodel"] = {}
    for wh in ("diag", "fc"):
        for r in (64, 128):
            row = {}
            for s in out["scales_present"]:
                v = [x for x in prim if x["wh"] == wh and x["r"] == r and x["scale_id"] == s]
                if v:
                    row[s] = float(np.mean([x["capture"] for x in v]) * v[0]["d_model"] / r)
            out["c_dmodel"][f"{wh}|{r}"] = row
            P(f"    {wh:4s} r={r:3d}: " + "  ".join(f"{s}: c={c:.1f}" for s, c in row.items()))

    # ---------------- 8. per-kind across-MODEL width slope at fixed r --------
    P("\n## width slope at FIXED kind and rank: b_d = dlog(mean capture)/dlog(d_in) across models (r/d ⇒ −1)")
    P("   (d-leverage here is ONLY the model widths: 1024 → 5120 at d_model kinds; ffn_down 3072/3584 → 17408)")
    out["kind_width_slope"] = {}
    for wh in ("diag", "fc"):
        for kd in sorted(set(x["kind"] for x in prim if x["wh"] == wh)):
            for r in (64, 128):
                sub = [x for x in prim if x["wh"] == wh and x["kind"] == kd and x["r"] == r]
                sc = sorted(set(x["scale_id"] for x in sub))
                if len(sc) < 2:
                    continue
                def slope(rs):
                    ms, ds = [], []
                    for s in sc:
                        v = [x for x in rs if x["scale_id"] == s]
                        if v:
                            ms.append(np.log(np.mean([x["capture"] for x in v])))
                            ds.append(np.log(np.mean([dval(x, args.dvar) for x in v])))
                    if len(set(np.round(ds, 6))) < 2:
                        return None
                    return float(np.polyfit(ds, ms, 1)[0])
                b = slope(sub)
                if b is None:
                    continue
                bs = cluster_boot(sub, min(args.boot, 300), rng, slope)
                cci = ci(bs)
                out["kind_width_slope"][f"{wh}|{kd}|{r}"] = dict(b_d=b, ci=cci, models=sc,
                    means={s: float(np.mean([x["capture"] for x in sub if x["scale_id"] == s])) for s in sc})
                P(f"  {wh:4s} {kd:9s} r={r:3d}: b_d={b:+.2f} [{cci[0]:+.2f},{cci[1]:+.2f}]  " +
                  "  ".join(f"{s}:{out['kind_width_slope'][f'{wh}|{kd}|{r}']['means'][s]:.3f}" for s in sc))

    # ---------------- 9. rank exponent WITHIN each model (r only) -----------
    P("\n## rank exponent within each model (all kinds pooled; log capture vs log r per tensor, averaged): capture ∝ r^b_r")
    out["within_model_br"] = {}
    for wh in ("diag", "fc"):
        for s in out["scales_present"]:
            sub = [x for x in prim if x["wh"] == wh and x["scale_id"] == s and 32 <= x["r"] <= 256]
            if not sub:
                continue
            byt = defaultdict(list)
            for x in sub:
                byt[x["tensor"]].append((math.log(x["r"]), math.log(x["capture"])))
            sl = [np.polyfit(*zip(*v), 1)[0] for v in byt.values() if len(v) >= 4]
            out["within_model_br"][f"{wh}|{s}"] = dict(mean=float(np.mean(sl)), sd=float(np.std(sl)), n=len(sl))
            P(f"  {wh:4s} {s:12s}: b_r = {np.mean(sl):.2f} ± {np.std(sl):.2f} (per-tensor slopes, n={len(sl)}, r∈[32,256])")

    (HERE / f"fit_summary{args.tag}.txt").write_text("\n".join(lines) + "\n")
    (HERE / f"fit_results{args.tag}.json").write_text(json.dumps(out, indent=1))
    print("\n".join(lines))
    if args.tag:
        return
    try:
        plots(rows, prim, out, args.dvar)
    except Exception as e:
        print("plots skipped:", e)


def plots(rows, prim, out, dvar):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cols = {"0.6B": "#1b9e77", "0.8B": "#d95f02", "27B-heretic": "#7570b3", "27B-Qwen3.8": "#e7298a"}
    # fig 1: capture vs r/d per whitener, primary lanes, per-tensor points + fits
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), sharey=True)
    for ax, wh in zip(axes, ("none", "diag", "fc")):
        sub = [x for x in prim if x["wh"] == wh]
        for s in cols:
            ss = [x for x in sub if x["scale_id"] == s]
            if not ss:
                continue
            ax.scatter([x["r"] / dval(x, dvar) for x in ss], [x["capture"] for x in ss],
                       s=4, alpha=0.25, color=cols[s], label=f"{s} (n_t={len(set(x['tensor'] for x in ss))})")
        xs = np.logspace(-3.3, -0.3, 100)
        f = out["fits"].get(wh, {})
        if "a_linear_r/d" in f:
            ax.plot(xs, f["a_linear_r/d"]["params"]["k"] * xs, "k--", lw=1, label=f"k·r/d, k={f['a_linear_r/d']['params']['k']:.1f}")
            p = f["b_power_(r/d)^b"]["params"]
            ax.plot(xs, p["a"] * xs ** p["b"], "k-", lw=1.2, label=f"a(r/d)^b, b={p['b']:.2f}")
            p = f["e_head+bulk"]["params"]
            ax.plot(xs, p["h"] + (1 - p["h"]) * xs ** p["b"], "k:", lw=1.2, label=f"h+(1-h)(r/d)^b, h={p['h']:.2f}")
        ax.set_xscale("log"); ax.set_yscale("log"); ax.set_ylim(3e-3, 1.05)
        ax.set_xlabel(f"r / {dvar}"); ax.set_title(f"whitener = {wh}")
        ax.legend(fontsize=7)
    axes[0].set_ylabel("whitened-energy capture")
    fig.suptitle("T1.10 capture vs r/d — per-tensor points, primary Q2-class lanes, 4 models")
    fig.tight_layout(); fig.savefig(HERE / "fig_capture_vs_rd.png", dpi=130)
    # fig 2: mean capture vs r per model per whitener
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for ax, wh in zip(axes, ("diag", "fc")):
        for s in cols:
            ss = [x for x in prim if x["wh"] == wh and x["scale_id"] == s]
            if not ss:
                continue
            rs = sorted(set(x["r"] for x in ss))
            m = [np.mean([x["capture"] for x in ss if x["r"] == r]) for r in rs]
            ax.plot(rs, m, "o-", color=cols[s], label=s, ms=4)
        ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("rank r"); ax.set_title(f"mean capture vs r — {wh}")
        ax.legend(fontsize=8); ax.grid(alpha=0.3, which="both")
    axes[0].set_ylabel("mean capture over tensors")
    fig.tight_layout(); fig.savefig(HERE / "fig_capture_vs_r.png", dpi=130)
    # fig 3: implied c at r=64 per (model, base, whitener), one panel per whitener
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), sharey=False)
    for ax, wh in zip(axes, ("diag", "fc", "none")):
        keys = [k for k in out["c_table"] if k.endswith("|64") and k.split("|")[3] == wh]
        keys.sort(key=lambda k: (list(cols).index(k.split("|")[0]), k.split("|")[2]))
        ax.bar(range(len(keys)), [out["c_table"][k]["c_mean"] for k in keys],
               yerr=[[out["c_table"][k]["c_mean"] - out["c_table"][k]["ci"][0] for k in keys],
                     [out["c_table"][k]["ci"][1] - out["c_table"][k]["c_mean"] for k in keys]],
               color=[cols[k.split("|")[0]] for k in keys], capsize=2)
        ax.set_xticks(range(len(keys)))
        ax.set_xticklabels([f"{k.split('|')[0]} {k.split('|')[2]}" for k in keys], fontsize=7, rotation=90)
        ax.set_title(f"whitener = {wh}"); ax.grid(axis="y", alpha=0.3)
    axes[0].set_ylabel(f"c = capture·{dvar}/r at r=64 (mean ± 95% CI over tensors)")
    fig.suptitle("the §4.2 'constant' c across (whitener, model, base)")
    fig.tight_layout(); fig.savefig(HERE / "fig_c_by_lane.png", dpi=130)
    print("wrote fig_capture_vs_rd.png fig_capture_vs_r.png fig_c_by_lane.png")


if __name__ == "__main__":
    main()
