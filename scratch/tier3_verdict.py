#!/usr/bin/env python3
"""Tier-3 zig-0.16 awareness pack — pre-registered verdict for the zig-only
mini-A/B run by scratch/tier3_zig_ab.sh.

PRE-REGISTERED READOUTS (fixed 2026-09-11, before any cell ran):
  R1 PRIMARY  pooled-replicate paired McNemar, P1 (packs ON) vs P0 (OFF), on
              the zig units. Pairs are (P0a,P1a) and (P0b,P1b); b = units
              passing in P1 but not P0 (rescues), c = the reverse
              (regressions). Exact two-sided binomial p on (b, c). Net = b-c.
  R2 CO-PRIMARY iterations-to-fix and completion-tokens-to-fix: paired
              differences (P1-P0) on units that PASSED in both arms of a
              pair (fix cost), plus all-unit means (total spend). Sign-test p.
              Iterations come from the results rows' `iterations` field
              (added with this harness; rows lacking it are excluded and
              counted).
  R3 GUARD    champion C1 (packs ON) vs C0 (packs OFF) McNemar on the same
              units. Clean = p > 0.05 or net >= 0. No C0 ⇒ guard NOT
              evaluated ⇒ cannot ship.
  R4 AUDIT    every unit passing under P1 but not P0 (per pair) is listed for
              the per-unit pack-parroting review (§6.3 / §7 item 7).
  SHIP RULE (Clause 1): net rescues >= 7 AND p < 0.05 AND guard clean.
  Overhead: prompt-token delta per unit (the pack costs ~2k tokens/request).

Usage:
  python3 scratch/tier3_verdict.py --manifest scratch/tier3_cells-<stamp>.txt
  python3 scratch/tier3_verdict.py --p0 A.json B.json --p1 C.json D.json [--c0 X.json --c1 Y.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

SHIP_MIN_NET = 7
SHIP_ALPHA = 0.05


def load_cell(path: str, language: str = "zig") -> dict:
    r = json.loads(Path(path).read_text())
    rows = {}
    for t in r.get("tasks", []):
        if language and t.get("language") not in (language, None):
            continue
        rows[t["id"]] = {
            "passed": bool(t.get("passed")),
            "tokens": t.get("tokens_completion"),
            "prompt": t.get("tokens_prompt"),
            "iters": t.get("iterations"),
            "cached": bool(t.get("from_cache")),
        }
    return {"path": path, "model": r.get("model"), "harness": r.get("harness", {}), "rows": rows}


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided binomial test on the discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def sign_test(diffs: list[float]) -> float:
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    return mcnemar_exact(pos, neg)


def paired(p0: dict, p1: dict) -> dict:
    ids = sorted(set(p0["rows"]) & set(p1["rows"]))
    b = [i for i in ids if p1["rows"][i]["passed"] and not p0["rows"][i]["passed"]]
    c = [i for i in ids if p0["rows"][i]["passed"] and not p1["rows"][i]["passed"]]
    both = [i for i in ids if p0["rows"][i]["passed"] and p1["rows"][i]["passed"]]
    d_tok = [p1["rows"][i]["tokens"] - p0["rows"][i]["tokens"] for i in both
             if p0["rows"][i]["tokens"] is not None and p1["rows"][i]["tokens"] is not None]
    d_it = [p1["rows"][i]["iters"] - p0["rows"][i]["iters"] for i in both
            if p0["rows"][i]["iters"] is not None and p1["rows"][i]["iters"] is not None]
    d_tok_all = [p1["rows"][i]["tokens"] - p0["rows"][i]["tokens"] for i in ids
                 if p0["rows"][i]["tokens"] is not None and p1["rows"][i]["tokens"] is not None]
    d_prompt = [p1["rows"][i]["prompt"] - p0["rows"][i]["prompt"] for i in ids
                if p0["rows"][i]["prompt"] is not None and p1["rows"][i]["prompt"] is not None]
    return {"ids": ids, "b": b, "c": c, "both": both, "d_tok": d_tok, "d_it": d_it,
            "d_tok_all": d_tok_all, "d_prompt": d_prompt,
            "pass0": sum(p0["rows"][i]["passed"] for i in ids),
            "pass1": sum(p1["rows"][i]["passed"] for i in ids),
            "missing_iters": sum(1 for i in both if p0["rows"][i]["iters"] is None or p1["rows"][i]["iters"] is None)}


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def parse_manifest(path: str) -> dict:
    cells = {}
    for ln in Path(path).read_text().splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        name, file = ln.split(None, 1)
        cells[name] = file.strip()
    return cells


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", help="cell manifest written by tier3_zig_ab.sh")
    ap.add_argument("--p0", nargs="*", default=[], help="packs-OFF results files (replicates, in order)")
    ap.add_argument("--p1", nargs="*", default=[], help="packs-ON results files (replicates, in order)")
    ap.add_argument("--c0", help="champion packs-OFF results file")
    ap.add_argument("--c1", help="champion packs-ON results file")
    ap.add_argument("--language", default="zig")
    a = ap.parse_args()
    p0, p1, c0, c1 = list(a.p0), list(a.p1), a.c0, a.c1
    if a.manifest:
        cells = parse_manifest(a.manifest)
        p0 = [cells[k] for k in sorted(cells) if k.startswith("P0")]
        p1 = [cells[k] for k in sorted(cells) if k.startswith("P1")]
        c0 = cells.get("C0", c0)
        c1 = cells.get("C1", c1)
    if not p0 or not p1 or len(p0) != len(p1):
        print("need equal numbers of P0 and P1 replicate files", file=sys.stderr)
        return 2

    P0 = [load_cell(p, a.language) for p in p0]
    P1 = [load_cell(p, a.language) for p in p1]
    for cell, want in [(x, {}) for x in P0] + [(x, "on") for x in P1]:
        packs = cell["harness"].get("packs", {})
        if want == "on" and not packs:
            print(f"WARNING: {cell['path']} has no harness.packs — is this really a packs-ON cell?")
        if want == {} and packs:
            print(f"WARNING: {cell['path']} has harness.packs={packs} — is this really a packs-OFF cell?")

    print("=" * 72)
    print("Tier-3 zig-0.16 awareness pack — zig-only mini-A/B verdict")
    print("=" * 72)
    print(f"treated model: {P1[0]['model']}   replicates: {len(P0)}   pack: {P1[0]['harness'].get('packs')}")

    # R1 primary — pooled replicates
    B = C = 0
    rescued_by_pair, regressed_by_pair = [], []
    d_tok, d_it, d_tok_all, d_prompt = [], [], [], []
    missing_iters = 0
    for k, (x0, x1) in enumerate(zip(P0, P1)):
        pr = paired(x0, x1)
        B += len(pr["b"]); C += len(pr["c"])
        rescued_by_pair.append(pr["b"]); regressed_by_pair.append(pr["c"])
        d_tok += pr["d_tok"]; d_it += pr["d_it"]; d_tok_all += pr["d_tok_all"]; d_prompt += pr["d_prompt"]
        missing_iters += pr["missing_iters"]
        print(f"  pair {k}: units={len(pr['ids'])} P0 pass={pr['pass0']} P1 pass={pr['pass1']} "
              f"rescues={len(pr['b'])} regressions={len(pr['c'])} "
              f"(cached rows: P0 {sum(r['cached'] for r in x0['rows'].values())}, "
              f"P1 {sum(r['cached'] for r in x1['rows'].values())})")
    net = B - C
    p = mcnemar_exact(B, C)
    print(f"\nR1 PRIMARY  pooled McNemar: rescues b={B} regressions c={C} net={net:+d} p={p:.4f}")

    # R2 co-primary
    print(f"R2 CO-PRIMARY (units passed in both arms, n={len(d_tok)}):")
    print(f"    completion tokens-to-fix  mean Δ(P1-P0)={mean(d_tok):+.0f}  sign-test p={sign_test(d_tok):.3f}")
    if d_it:
        print(f"    iterations-to-fix         mean Δ(P1-P0)={mean(d_it):+.2f}  sign-test p={sign_test(d_it):.3f}"
              + (f"  ({missing_iters} pairs lacked iterations)" if missing_iters else ""))
    else:
        print(f"    iterations-to-fix         unavailable (rows carry no `iterations`; {missing_iters} pairs)")
    print(f"    all-unit completion tokens mean Δ={mean(d_tok_all):+.0f} (n={len(d_tok_all)}); "
          f"prompt-token overhead mean Δ={mean(d_prompt):+.0f}/unit")

    # R3 guard
    guard_clean = None
    if c0 and c1:
        G0, G1 = load_cell(c0, a.language), load_cell(c1, a.language)
        g = paired(G0, G1)
        gp = mcnemar_exact(len(g["b"]), len(g["c"]))
        gnet = len(g["b"]) - len(g["c"])
        guard_clean = gp > 0.05 or gnet >= 0
        print(f"R3 GUARD    champion {G1['model']}: C0 pass={g['pass0']} C1 pass={g['pass1']} "
              f"rescues={len(g['b'])} regressions={len(g['c'])} net={gnet:+d} p={gp:.3f} → "
              f"{'CLEAN' if guard_clean else 'REGRESSION'}")
    elif c1:
        G1 = load_cell(c1, a.language)
        print(f"R3 GUARD    champion C1 pass={sum(r['passed'] for r in G1['rows'].values())}/{len(G1['rows'])} "
              f"— NO C0 reference: guard NOT evaluated (pass --c0)")
    else:
        print("R3 GUARD    NOT evaluated (no champion cells)")

    # R4 audit list
    print("R4 AUDIT    P1-only passes per pair (review for pack-parroting):")
    for k, ids in enumerate(rescued_by_pair):
        print(f"    pair {k}: {', '.join(ids) or '(none)'}")
    consistent = set(rescued_by_pair[0]).intersection(*rescued_by_pair[1:]) if rescued_by_pair else set()
    print(f"    rescued in EVERY replicate: {', '.join(sorted(consistent)) or '(none)'}")
    for k, ids in enumerate(regressed_by_pair):
        if ids:
            print(f"    pair {k} regressions: {', '.join(ids)}")

    # Ship rule
    reasons = []
    if net < SHIP_MIN_NET:
        reasons.append(f"net {net:+d} < {SHIP_MIN_NET}")
    if p >= SHIP_ALPHA:
        reasons.append(f"p {p:.3f} >= {SHIP_ALPHA}")
    if guard_clean is None:
        reasons.append("champion guard not evaluated")
    elif not guard_clean:
        reasons.append("champion guard regression")
    verdict = "SHIP" if not reasons else "NO-SHIP"
    print("\n" + "-" * 72)
    print(f"VERDICT: {verdict} — Clause 1 (net>={SHIP_MIN_NET}, p<{SHIP_ALPHA}, guard clean): "
          f"net={net:+d} p={p:.4f} guard={'clean' if guard_clean else ('regression' if guard_clean is False else 'n/a')}"
          + (f" — {'; '.join(reasons)}" if reasons else ""))
    print("Clause 2: this was the last arm on this suite — STOP regardless (roadmap §5 stopping rule).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
