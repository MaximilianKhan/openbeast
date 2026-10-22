#!/usr/bin/env python3
"""E36-F capability analysis, exactly as registered in PLAN.md §F.

usage: f_analysis.py [--allow-partial] [--md]
  --md prints the two tables of paper section 5.5 in the paper's format.
  reads rows-f.txt (one line per completed row, written by f_capability.sh)
  and each row's result JSON. Only rows listed there count: an interrupted
  run's JSON on disk (GSQ r2 of 2026-10-17) is never read.

Primary: per-unit pass rate over the runs, paired by unit (112 units), per
arm pair (GSQ-UD, OURS-UD, OURS-GSQ): mean difference, Wilcoxon signed-rank,
sign test. Secondary: run-level pass counts, mean +- SD, Welch t.
Rule: "differs" if Wilcoxon p < 0.05 AND the run-level ranges do not overlap;
"consistent with no difference" if p >= 0.05; otherwise "unresolved".

Failure classes (2026-10-17 note): validator_timeout and wall timeout (agent
exit -1) are reported per row, and a sensitivity rerun of the primary drops
every unit that hit a validator_timeout in any row of either arm.

Refuses to run unless every arm has the same number of runs (rounds are
balanced by design); --allow-partial uses the completed rounds only and
labels the output INTERIM.
"""
import json
import os
import re
import sys

import numpy as np
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
ARMS = ("GSQ", "UD", "OURS")
NAMES = {"GSQ": "GSQ-RCO IQ3_S", "UD": "UD-IQ3_S", "OURS": "UD-IQ3_S + correction"}
PAIRS = (("GSQ", "UD"), ("OURS", "UD"), ("OURS", "GSQ"))


def rows():
    out = {a: [] for a in ARMS}
    for line in open(os.path.join(HERE, "rows-f.txt")):
        m = re.match(r"e36f-(\w+)-IQ3S-r(\d+) .*result=(\S+)", line)
        if m:
            out[m.group(1)].append((int(m.group(2)), os.path.join(REPO, m.group(3))))
    return {a: [p for _, p in sorted(v)] for a, v in out.items()}


def load(path):
    tasks = json.load(open(path))["tasks"]
    res = {}
    for t in tasks:
        v = str(t.get("validation_output", ""))
        res[t["id"]] = {
            "pass": bool(t["passed"]),
            "vt": not t["passed"] and "Validation timed out" in v,
            "wall": t.get("agent_exit_code") == -1,
        }
    return res


def primary(rates, a, b, units):
    d = np.array([rates[a][u] - rates[b][u] for u in units])
    nz = d[d != 0]
    w = stats.wilcoxon(nz).pvalue if len(nz) else 1.0
    pos, neg = int((nz > 0).sum()), int((nz < 0).sum())
    s = stats.binomtest(pos, pos + neg).pvalue if pos + neg else 1.0
    return d.mean(), w, s, pos, neg


def main():
    partial = "--allow-partial" in sys.argv
    r = rows()
    n = min(len(v) for v in r.values())
    if len({len(v) for v in r.values()}) != 1 and not partial:
        sys.exit(f"unbalanced arms {({a: len(v) for a, v in r.items()})}; "
                 "wait for the round to finish or pass --allow-partial")
    runs = {a: [load(p) for p in r[a][:n]] for a in ARMS}
    # Every row must share the eval era, llama.cpp build, serve flags and
    # validator toolchains; only the weights (and OURS's --lora) may differ.
    envs = set()
    for a in ARMS:
        for path in r[a][:n]:
            h = json.load(open(path))["harness"]
            e = {k: v for k, v in h["env"].items() if k != "weights"}
            envs.add(json.dumps({"era": h["era"], **e}, sort_keys=True))
    assert len(envs) == 1, f"rows differ in era/build/serve/toolchains: {envs}"
    units = sorted(runs["UD"][0])
    for a in ARMS:
        for x in runs[a]:
            assert sorted(x) == units, f"{a}: unit set differs"
    tag = "INTERIM " if partial and n < 3 else ""
    md_out = "--md" in sys.argv

    counts, vts, walls = {}, {}, {}
    for a in ARMS:
        counts[a] = [sum(x[u]["pass"] for u in units) for x in runs[a]]
        vts[a] = [sum(x[u]["vt"] for u in units) for x in runs[a]]
        walls[a] = [sum(x[u]["wall"] for u in units) for x in runs[a]]
    rates = {a: {u: np.mean([x[u]["pass"] for x in runs[a]]) for u in units} for a in ARMS}
    vt_units = {u for a in ARMS for x in runs[a] for u in units if x[u]["vt"]}
    sets = (("all units", units),
            (f"without the {len(vt_units)} units with a validator timeout in any run",
             [u for u in units if u not in vt_units]))
    tests = []
    for label, us in sets:
        for a, b in PAIRS:
            md, w, s, pos, neg = primary(rates, a, b, us)
            ca = [sum(x[u]["pass"] for u in us) for x in runs[a]]
            cb = [sum(x[u]["pass"] for u in us) for x in runs[b]]
            overlap = not (min(ca) > max(cb) or min(cb) > max(ca))
            verdict = ("consistent with no difference" if w >= 0.05 else
                       "differs" if not overlap else "unresolved")
            welch = (stats.ttest_ind(ca, cb, equal_var=False).pvalue
                     if n > 1 else float("nan"))
            tests.append((label, len(us), a, b, md * len(us), w, pos, neg, s, welch, overlap, verdict))

    def sd(a):
        return np.std(counts[a], ddof=1) if n > 1 else float("nan")

    if md_out:
        print(f"<!-- f_analysis.py --md {tag}— {n} run(s) per arm -->\n")
        print(f"| Arm (IQ3 class) | passed, {n} runs of 112 | mean ± SD |"
              " validator timeouts | wall timeouts |")
        print("|---|---|---|---|---|")
        for a in ARMS:
            print(f"| {NAMES[a]} | {', '.join(map(str, counts[a]))} |"
                  f" {np.mean(counts[a]):.1f} ± {sd(a):.1f} |"
                  f" {', '.join(map(str, vts[a]))} | {', '.join(map(str, walls[a]))} |")
        print("\n| Pair, units | Δ passed per run | Wilcoxon p | units better / worse (sign p)"
              " | run ranges | registered verdict |")
        print("|---|---|---|---|---|---|")
        for label, nu, a, b, d, w, pos, neg, s, _, overlap, verdict in tests:
            print(f"| {a} − {b}, {label} (n = {nu}) | {d:+.1f} | {w:.3f} |"
                  f" {pos} / {neg} ({s:.3f}) | {'overlap' if overlap else 'disjoint'} | {verdict} |")
        return

    print(f"E36-F {tag}— {n} run(s) per arm, {len(units)} units\n")
    print("Run level (passed / 112)   validator_timeout   wall timeout (exit -1)")
    for a in ARMS:
        print(f"  {a:5} {counts[a]}  mean {np.mean(counts[a]):.1f} ± {sd(a):.1f}"
              f"   vt {vts[a]}   wall {walls[a]}")
    last = None
    for label, nu, a, b, d, w, pos, neg, s, welch, overlap, verdict in tests:
        if label != last:
            print(f"\n{label}: per-unit pass rate, paired by unit (n = {nu})")
            last = label
        print(f"  {a:4} − {b:4}  Δ {d:+.2f} units/run   Wilcoxon p {w:.4f}"
              f"   sign {pos}+/{neg}− p {s:.4f}   Welch p {welch:.3f}"
              f"   ranges {'overlap' if overlap else 'disjoint'}  → {verdict}")

if __name__ == "__main__":
    main()
