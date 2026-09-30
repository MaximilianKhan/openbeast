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

VALIDITY (added 2026-09-29, after the fact — review eval-harness-1 /
tools-mcp-security-1): a row that llama-server died under (API/connection
errors in its agent log), whose validation died on fork/thread EAGAIN, or that
the harness never ran is not a sample of the model. Such a row drops its unit
from THAT pair (both arms), the same row classifier as scratch/row_validity.py.
The 09-17 cells read SHIP (+13, p=0.019) only with those rows counted; see
scratch/tier3-verdict-reaudit-2026-09-29.txt. --raw reproduces the
as-registered read; --keep CELL:UNIT keeps one flagged row (sensitivity).

WALL TIMEOUTS (added 2026-09-30, review A-campaign-1): a row that hit the
harness wall timeout (exit -1) keeps its pass/fail — a timed-out PASS
validated the files the agent left behind — but run_eval records its tokens
as 0 and its iterations as None, so it is excluded from EVERY token and
iteration statistic (a recorded 0 used to enter R2 as a real value). Every
timeout is listed under VALIDITY, and R1/R3 print a sensitivity read with
the timed-out units dropped. The R2 prompt figure is the all-unit TOTAL
prompt tokens per unit (dominated by iteration count), not the pack's
per-request overhead. PROVENANCE warns when the repo commit or the engine
build differs across cells, or the weights differ within P*/C* cells.
--heldout UNITS reads held-out units on their own (one-sided sign test,
LANG_AWARENESS_PLAN §5) and takes them out of R1-R4 instead of pooling.

Usage:
  python3 scratch/tier3_verdict.py --manifest scratch/tier3_cells-<stamp>.txt
  python3 scratch/tier3_verdict.py --p0 A.json B.json --p1 C.json D.json [--c0 X.json --c1 Y.json]
  [--agent-logs DIR|none] [--raw] [--keep P0a:62_crt_f ...] [--heldout U1,U2|suite.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import row_validity  # noqa: E402  (the one row classifier; see its docstring)

SHIP_MIN_NET = 7
SHIP_ALPHA = 0.05


def load_cell(path: str, language: str = "zig", log_idx=None) -> dict:
    r = json.loads(Path(path).read_text())
    rows = {}
    for t in r.get("tasks", []):
        if language and t.get("language") not in (language, None):
            continue
        # A wall timeout (exit -1) or a zero-token row carries tokens 0 /
        # iterations None because run_eval never measured them — not because
        # the agent spent nothing. Blank them so no token statistic reads a
        # recorded 0 as a real value (2026-09-30: one such row moved the
        # published prompt Δ from -2163 to -5189).
        unrec = row_validity.tokens_unrecorded(t)
        rows[t["id"]] = {
            "passed": bool(t.get("passed")),
            "tokens": None if unrec else t.get("tokens_completion"),
            "prompt": None if unrec else t.get("tokens_prompt"),
            "iters": None if unrec else t.get("iterations"),
            "cached": bool(t.get("from_cache")),
            "unrecorded": unrec,
        }
    bad = row_validity.contaminated_ids(r, log_idx)
    rt, eng = r.get("runtime") or {}, r.get("inference_engine") or {}
    weights = ((r.get("harness") or {}).get("env") or {}).get("weights") or {}
    prov = {"commit": rt.get("openbeast_commit"), "dirty": rt.get("openbeast_dirty"),
            "engine": (f"{eng.get('build')}/{eng.get('commit')}" if eng else None),
            "weights": weights.get("sha256")}
    return {"path": path, "model": r.get("model"), "harness": r.get("harness", {}), "rows": rows,
            "contaminated": {k: v for k, v in bad.items() if k in rows},
            "timeouts": {k: v for k, v in row_validity.wall_timeouts(r).items() if k in rows},
            "prov": prov}


def drop_timeouts(cell: dict) -> dict:
    """The cell without its wall-timeout rows (the sensitivity read)."""
    return {**cell, "rows": {u: v for u, v in cell["rows"].items() if u not in cell["timeouts"]}}


def restrict(cell: dict, units, exclude: bool = False) -> dict:
    """The cell keeping only (or, with exclude, dropping) the given units."""
    units = set(units)
    return {**cell, "rows": {u: v for u, v in cell["rows"].items() if (u in units) != exclude}}


def sign_test_greater(b: int, c: int) -> float:
    """Exact one-sided binomial p for b > c on the discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    return sum(math.comb(n, i) for i in range(b, n + 1)) / 2 ** n


def provenance_warnings(named_cells) -> list[str]:
    """Pairing across a code, engine or weights change is not a paired
    comparison. The repo commit and the llama.cpp build must match across
    every cell; the weights must match within the treated (P*) and within the
    champion (C*) cells. Unknown (absent) fields are not compared."""
    out = []

    def differs(field, cells):
        vals = {n: c["prov"][field] for n, c in cells if c["prov"][field] is not None}
        return vals if len(set(vals.values())) > 1 else None

    for field in ("commit", "engine"):
        v = differs(field, named_cells)
        if v:
            out.append(f"{field} differs across cells: " + ", ".join(f"{n}={str(x)[:12]}" for n, x in v.items()))
    for group in ("P", "C"):
        v = differs("weights", [(n, c) for n, c in named_cells if n.startswith(group)])
        if v:
            out.append(f"weights differ within {group}* cells: "
                       + ", ".join(f"{n}={str(x)[:12]}" for n, x in v.items()))
    dirty = [n for n, c in named_cells if c["prov"]["dirty"]]
    if dirty:
        out.append(f"dirty repo tree at run time in {', '.join(dirty)} "
                   "(the uncommitted diff is not recoverable from the results)")
    return out


def drop_contaminated(cell: dict, name: str, keep: set) -> dict:
    """The cell with its contaminated rows removed (paired() then drops the
    unit from this pair), except rows named in keep as CELL:UNIT."""
    drop = {u for u in cell["contaminated"] if f"{name}:{u}" not in keep}
    return {**cell, "rows": {u: v for u, v in cell["rows"].items() if u not in drop},
            "dropped": sorted(drop)}


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
            "missing_iters": sum(1 for i in both if p0["rows"][i]["iters"] is None or p1["rows"][i]["iters"] is None),
            "unrecorded": sum(1 for i in ids if p0["rows"][i].get("unrecorded") or p1["rows"][i].get("unrecorded"))}


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def fmt_p(p: float) -> str:
    """4 decimals, but never round a real p to 0.0000."""
    return f"{p:.4f}" if p >= 1e-4 else f"{p:.2g}"


def median(xs):
    if not xs:
        return float("nan")
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


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
    ap.add_argument("--agent-logs", default=str(Path(__file__).resolve().parent.parent / "agents" / "logs"),
                    help="agents/logs dir for the API-error check, or 'none'")
    ap.add_argument("--raw", action="store_true",
                    help="count every row, contaminated or not (the as-registered read)")
    ap.add_argument("--keep", action="append", default=[], metavar="CELL:UNIT",
                    help="keep one flagged row anyway (sensitivity), e.g. P0a:62_crt_f")
    ap.add_argument("--heldout", metavar="UNITS|SUITE.json",
                    help="held-out units (comma list, or a suite json with 'units'): taken OUT of "
                         "R1-R4 and read on their own with the pre-registered one-sided sign test "
                         "(LANG_AWARENESS_PLAN §5), never pooled with the in-sample units")
    a = ap.parse_args()
    heldout = set()
    if a.heldout:
        hp = Path(a.heldout)
        if a.heldout.endswith(".json"):
            # never fall through to the comma split: a missing suite file would
            # become one bogus "unit id", R1 would stay fully pooled under a
            # banner saying it is in-sample only, and exit 0
            if not hp.is_file():
                print(f"--heldout {a.heldout!r}: no such suite file (cwd {Path.cwd()})", file=sys.stderr)
                return 2
            try:
                units = json.loads(hp.read_text()).get("units")
            except (ValueError, AttributeError) as e:
                print(f"--heldout {a.heldout!r}: not a suite json object ({e})", file=sys.stderr)
                return 2
            if not isinstance(units, list):
                print(f"--heldout {a.heldout!r} has no 'units' list", file=sys.stderr)
                return 2
            heldout = {str(u) for u in units}
        else:
            heldout = {u.strip() for u in a.heldout.split(",") if u.strip()}
        if not heldout:
            print(f"--heldout {a.heldout!r} names no units", file=sys.stderr)
            return 2
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

    idx = None if a.agent_logs == "none" else row_validity.load_log_index(a.agent_logs)
    keep = set(a.keep)
    P0 = [load_cell(p, a.language, idx) for p in p0]
    P1 = [load_cell(p, a.language, idx) for p in p1]
    names0 = [f"P0{chr(97 + k)}" for k in range(len(P0))]
    names1 = [f"P1{chr(97 + k)}" for k in range(len(P1))]
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
    print("VALIDITY    " + ("--raw: contaminated rows COUNTED (as-registered read)" if a.raw else
                          "contaminated rows dropped from their pair"
                          + ("" if idx is not None else " (agent logs not read: API axis unchecked)")))
    for nm, cell in zip(names0 + names1, P0 + P1):
        if cell["contaminated"]:
            print(f"    {nm}: " + "; ".join(f"{u} [{', '.join(w)}]" + (" KEPT" if f"{nm}:{u}" in keep else "")
                                        for u, w in sorted(cell["contaminated"].items())))

    G0 = load_cell(c0, a.language, idx) if c0 else None
    G1 = load_cell(c1, a.language, idx) if c1 else None
    named = list(zip(names0 + names1, P0 + P1)) + [(n, c) for n, c in (("C0", G0), ("C1", G1)) if c]
    n_to = sum(len(c["timeouts"]) for _, c in named)
    print(f"    wall timeouts (exit -1; pass/fail counted, tokens NOT recorded → out of every token "
          f"statistic): {n_to}" + (": " + "; ".join(
              f"{nm} {u} [{v}]" for nm, c in named for u, v in sorted(c["timeouts"].items())) if n_to else ""))
    warns = provenance_warnings(named)
    for w in warns:
        print(f"PROVENANCE  WARNING: {w}")
    if not warns:
        print("PROVENANCE  repo commit, engine build and weights consistent across cells")

    if not a.raw:
        P0 = [drop_contaminated(c, n, keep) for c, n in zip(P0, names0)]
        P1 = [drop_contaminated(c, n, keep) for c, n in zip(P1, names1)]
        if G0 is not None:
            G0 = drop_contaminated(G0, "C0", keep)
        if G1 is not None:
            G1 = drop_contaminated(G1, "C1", keep)
    H0 = H1 = None
    if heldout:
        present = set().union(*(c["rows"] for c in P0 + P1))
        if not heldout & present:
            print(f"--heldout names {len(heldout)} unit(s), none of which appear in any P cell "
                  f"({', '.join(sorted(heldout)[:5])}{' …' if len(heldout) > 5 else ''}) — "
                  "refusing to print an in-sample-only verdict that excluded nothing", file=sys.stderr)
            return 2
        absent = sorted(heldout - present)
        if absent:
            print(f"WARNING: --heldout units with no rows in any P cell: {', '.join(absent)}", file=sys.stderr)
        H0 = [restrict(c, heldout) for c in P0]
        H1 = [restrict(c, heldout) for c in P1]
        P0 = [restrict(c, heldout, exclude=True) for c in P0]
        P1 = [restrict(c, heldout, exclude=True) for c in P1]
        G0 = restrict(G0, heldout, exclude=True) if G0 is not None else None
        G1 = restrict(G1, heldout, exclude=True) if G1 is not None else None
        print(f"HELD-OUT    {len(heldout & present)} unit(s) read separately below"
              + (f" ({len(absent)} named but absent)" if absent else "")
              + "; R1-R4 are in-sample only")

    # R1 primary — pooled replicates
    B = C = 0
    rescued_by_pair, regressed_by_pair = [], []
    d_tok, d_it, d_tok_all, d_prompt = [], [], [], []
    missing_iters = unrecorded = 0
    for k, (x0, x1) in enumerate(zip(P0, P1)):
        pr = paired(x0, x1)
        B += len(pr["b"]); C += len(pr["c"])
        rescued_by_pair.append(pr["b"]); regressed_by_pair.append(pr["c"])
        d_tok += pr["d_tok"]; d_it += pr["d_it"]; d_tok_all += pr["d_tok_all"]; d_prompt += pr["d_prompt"]
        missing_iters += pr["missing_iters"]; unrecorded += pr["unrecorded"]
        print(f"  pair {k}: units={len(pr['ids'])} P0 pass={pr['pass0']} P1 pass={pr['pass1']} "
              f"rescues={len(pr['b'])} regressions={len(pr['c'])} "
              f"(cached rows: P0 {sum(r['cached'] for r in x0['rows'].values())}, "
              f"P1 {sum(r['cached'] for r in x1['rows'].values())})")
    net = B - C
    p = mcnemar_exact(B, C)
    print(f"\nR1 PRIMARY  pooled McNemar: rescues b={B} regressions c={C} net={net:+d} p={fmt_p(p)}")
    if any(c["timeouts"] for c in P0 + P1):
        sb = sc = 0
        for x0, x1 in zip(P0, P1):
            s = paired(drop_timeouts(x0), drop_timeouts(x1))
            sb += len(s["b"]); sc += len(s["c"])
        print(f"    sensitivity, wall-timeout units dropped from their pair: b={sb} c={sc} "
              f"net={sb - sc:+d} p={mcnemar_exact(sb, sc):.2g}")

    # R2 co-primary
    print(f"R2 CO-PRIMARY (units passed in both arms, n={len(d_tok)}"
          + (f"; {unrecorded} pair(s) excluded — a side's tokens were not recorded" if unrecorded else "")
          + "):")
    print(f"    completion tokens-to-fix  mean Δ(P1-P0)={mean(d_tok):+.0f}  sign-test p={sign_test(d_tok):.3f}")
    if d_it:
        print(f"    iterations-to-fix         mean Δ(P1-P0)={mean(d_it):+.2f}  sign-test p={sign_test(d_it):.3f}"
              + (f"  ({missing_iters} pairs lacked iterations)" if missing_iters else ""))
    else:
        print(f"    iterations-to-fix         unavailable (rows carry no `iterations`; {missing_iters} pairs)")
    # Prompt tokens are a total over every request of a unit, so this is
    # dominated by how many iterations each arm ran — not the pack's own
    # per-request cost (~2k tokens), which only a per-request count measures.
    print(f"    all-unit completion tokens mean Δ={mean(d_tok_all):+.0f} (n={len(d_tok_all)}); "
          f"all-unit prompt tokens (total per unit, not per-request overhead) "
          f"mean Δ={mean(d_prompt):+.0f} median Δ={median(d_prompt):+.0f} (n={len(d_prompt)})")

    # R3 guard
    guard_clean = None
    if G0 is not None and G1 is not None:
        for nm, cell in (("C0", G0), ("C1", G1)):
            if cell["contaminated"]:
                print(f"    {nm} contaminated: " + "; ".join(
                    f"{u} [{', '.join(w)}]" + (" KEPT" if f"{nm}:{u}" in keep else "")
                    for u, w in sorted(cell["contaminated"].items())))
        g = paired(G0, G1)
        gp = mcnemar_exact(len(g["b"]), len(g["c"]))
        gnet = len(g["b"]) - len(g["c"])
        guard_clean = gp > 0.05 or gnet >= 0
        print(f"R3 GUARD    champion {G1['model']}: units={len(g['ids'])} C0 pass={g['pass0']} C1 pass={g['pass1']} "
              f"rescues={len(g['b'])} regressions={len(g['c'])} net={gnet:+d} p={gp:.3f} → "
              f"{'CLEAN' if guard_clean else 'REGRESSION'}")
        if G0["timeouts"] or G1["timeouts"]:
            s = paired(drop_timeouts(G0), drop_timeouts(G1))
            snet = len(s["b"]) - len(s["c"])
            print(f"    sensitivity, wall-timeout units dropped: b={len(s['b'])} c={len(s['c'])} "
                  f"net={snet:+d} p={mcnemar_exact(len(s['b']), len(s['c'])):.3f}")
    elif G1 is not None:
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

    # Held-out readout (LANG_AWARENESS_PLAN §5 item 4): its own test, never
    # pooled — 30 in-sample units at +24 would swamp any held-out signal.
    if heldout:
        hb = hc = 0
        hunits = set()
        for k, (x0, x1) in enumerate(zip(H0, H1)):
            h = paired(x0, x1)
            hb += len(h["b"]); hc += len(h["c"]); hunits |= set(h["ids"])
            print(f"HELD-OUT    pair {k}: units={len(h['ids'])} P0 pass={h['pass0']} P1 pass={h['pass1']} "
                  f"rescues={', '.join(h['b']) or '-'} regressions={', '.join(h['c']) or '-'}")
        missing = sorted(heldout - hunits)
        hp_ = sign_test_greater(hb, hc)
        print(f"HELD-OUT    pooled one-sided exact sign test P1>P0: b={hb} c={hc} net={hb - hc:+d} "
              f"p={hp_:.4f} → {'P1 > P0 at α=0.05' if hp_ < 0.05 else 'not shown at α=0.05'}"
              + (f"  (no rows for: {', '.join(missing)})" if missing else ""))

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
          f"net={net:+d} p={fmt_p(p)} guard={'clean' if guard_clean else ('regression' if guard_clean is False else 'n/a')}"
          + (f" — {'; '.join(reasons)}" if reasons else ""))
    print("Clause 2: this was the last arm on this suite — STOP regardless (roadmap §5 stopping rule).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
