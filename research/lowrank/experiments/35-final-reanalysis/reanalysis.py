#!/usr/bin/env python3
"""E35 -- final CPU reanalysis for the paper revision (2026-10-09).

Reads existing llama-perplexity logs only. No model is loaded, no GPU is
used. Per-chunk values are recovered with the project's canonical parser
(experiments/24-yaqa-lite/paired_stats.py: parse_kld_rows, parse_ppl_stream,
per_chunk_from_cum), imported unchanged.

Sections written to RESULTS.md:
  A1  robust statistics for the load-bearing 27B pairs (n=100) and the eight
      GSQ contrasts on each corpus (n=40): paired mean, t, 95% bootstrap CI
      (10,000 resamples, seed 20261009), exact sign test, Wilcoxon signed-rank,
      block bootstrap over contiguous blocks of 10 chunks, block-means t,
      lag-1 autocorrelation; chunks 1-40 vs 41-100; check that the 40-chunk
      run is the prefix of the 100-chunk run.
  A2  90% CIs of the three low-rank placements against the corrected
      configuration, absolute and as a fraction of the correction increment.
  A3  re-rounded Q2_K against stock rungs (0.8B paired; 27B on shared chunks).
  A4  tensor types of ffn_*_exps in the MoE reference (from the quantize log,
      and from the GGUF header if the file is on disk).
  A5  paired 2048-token perplexity for the GSQ / Unsloth pairs.
  A6  small derived numbers quoted in the paper (unpaired sigma distances,
      per-GB exchange rates, percentages).

Usage: python3 reanalysis.py > RESULTS.md
"""
import math
import os
import re
import sys

import numpy as np
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(HERE)
MAIN_EXP = "/home/max/Documents/openbeast/research/lowrank/experiments"
sys.path.insert(0, os.path.join(EXP, "24-yaqa-lite"))
from paired_stats import (parse_kld_rows, parse_ppl_stream,  # noqa: E402
                          per_chunk_from_cum, FLOAT)

SEED = 20261009
NBOOT = 10_000
BLOCK = 10


def P(rel):
    """experiment-relative path; falls back to the main checkout."""
    p = os.path.join(EXP, rel)
    if os.path.exists(p):
        return p
    q = os.path.join(MAIN_EXP, rel)
    if os.path.exists(q):
        return q
    raise FileNotFoundError(rel)


_cache = {}


def kld(rel):
    if rel not in _cache:
        _cache[rel] = parse_kld_rows(P(rel))
    return _cache[rel]


def base_nll_per_chunk(rel):
    """per-chunk NLL of the REFERENCE, recovered from PPL(Q) and
    ln(PPL(Q)/PPL(base)); identical series <=> same reference + chunks."""
    rows = []
    for line in open(P(rel)):
        if "±" not in line:
            continue
        nums = re.findall(FLOAT, line.replace("%", ""))
        if len(nums) == 11 and re.match(r"\s*\d+\s", line):
            nums = [float(x) for x in nums]
            rows.append((int(nums[0]), math.log(nums[1]) - nums[3]))
    rows.sort()
    return np.array(per_chunk_from_cum([r[1] for r in rows]))


# ---------------------------------------------------------------- statistics
def boot_ci(d, level=0.95, rng=None):
    n = len(d)
    idx = rng.integers(0, n, size=(NBOOT, n))
    m = d[idx].mean(axis=1)
    a = (1 - level) / 2
    return float(np.quantile(m, a)), float(np.quantile(m, 1 - a))


def block_boot_ci(d, block=BLOCK, level=0.95, rng=None):
    """non-overlapping contiguous blocks, resampled with replacement."""
    n = len(d)
    nb = n // block
    blocks = d[:nb * block].reshape(nb, block)
    idx = rng.integers(0, nb, size=(NBOOT, nb))
    m = blocks[idx].mean(axis=(1, 2))
    a = (1 - level) / 2
    return float(np.quantile(m, a)), float(np.quantile(m, 1 - a)), nb


def block_means_t(d, block=BLOCK):
    n = len(d)
    nb = n // block
    bm = d[:nb * block].reshape(nb, block).mean(axis=1)
    t = bm.mean() / (bm.std(ddof=1) / math.sqrt(nb))
    p = 2 * stats.t.sf(abs(t), nb - 1)
    return float(t), float(p), nb


def lag1(d):
    x = d - d.mean()
    return float((x[:-1] * x[1:]).sum() / (x * x).sum())


def describe(a, b, higher_better=False):
    a, b = np.asarray(a), np.asarray(b)
    n = min(len(a), len(b))
    d = a[:n] - b[:n]
    rng = np.random.default_rng(SEED)
    mean = float(d.mean())
    sem = float(d.std(ddof=1) / math.sqrt(n))
    t = mean / sem
    p_t = float(2 * stats.t.sf(abs(t), n - 1))
    wins = int(((d > 0) if higher_better else (d < 0)).sum())
    ties = int((d == 0).sum())
    p_sign = float(stats.binomtest(wins, n - ties, 0.5).pvalue)
    p_wil = float(stats.wilcoxon(d).pvalue)
    lo, hi = boot_ci(d, rng=rng)
    blo, bhi, nb = block_boot_ci(d, rng=rng)
    bt, bp, _ = block_means_t(d)
    return dict(n=n, mean=mean, sem=sem, t=t, p_t=p_t, wins=wins, ties=ties,
                p_sign=p_sign, p_wil=p_wil, lo=lo, hi=hi, blo=blo, bhi=bhi,
                nb=nb, bt=bt, bp=bp, r1=lag1(d), d=d)


def fmt_p(p):
    return f"{p:.2g}" if p < 0.001 else f"{p:.4f}"


def row(label, s):
    return (f"| {label} | {s['n']} | {s['mean']:+.5f} ± {s['sem']:.5f} | "
            f"{s['t']:+.2f} | {fmt_p(s['p_t'])} | "
            f"[{s['lo']:+.5f}, {s['hi']:+.5f}] | {s['wins']}/{s['n']} | "
            f"{fmt_p(s['p_sign'])} | {fmt_p(s['p_wil'])} | "
            f"[{s['blo']:+.5f}, {s['bhi']:+.5f}] | "
            f"{s['bt']:+.2f} (df {s['nb'] - 1}, p {fmt_p(s['bp'])}) | "
            f"{s['r1']:+.2f} |")


HEAD = ("| pair (A − B), KLD | n | mean ± sem | t | p(t) | 95% bootstrap CI | "
        "A better | sign p | Wilcoxon p | 95% block-bootstrap CI (blocks of "
        f"{BLOCK}) | t on block means | lag-1 r |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|")

E27 = "27-bf16-rederivation/"
E32 = "32-t117-gsq-head-to-head/"

PAIRS_27B = [
    ("corrected (MIXEDfc) vs Q3_K_S", "kld100-MIXEDfc.log", "kld100-Q3_K_S.log"),
    ("corrected (MIXEDfc) vs control", "kld100-MIXEDfc.log", "kld100-CONTROL.log"),
    ("control vs Q3_K_S", "kld100-CONTROL.log", "kld100-Q3_K_S.log"),
    ("corrected vs mixed base (MIXEDbare)", "kld100-MIXEDfc.log", "kld100-MIXEDbare.log"),
    ("mixed base (MIXEDbare) vs Q3_K_S", "kld100-MIXEDbare.log", "kld100-Q3_K_S.log"),
    ("re-rounded Q2_K vs bare Q2_K (legacy pair)", "kld100-Q2Krr-legacy.log", "kld100-Q2Kbare-legacy.log"),
]

GSQ = [
    ("ours IQ2_S+corr vs GSQ-RCO IQ2_XS", "ours-iq2s-fc128", "gsq-rco-iq2xs"),
    ("ours IQ3_S+corr vs GSQ-RCO IQ3_S", "ours-iq3s-fc128", "gsq-rco-iq3s"),
    ("GSQ-RCO IQ2_XS vs UD-IQ2_S", "gsq-rco-iq2xs", "unsloth-ud-iq2s"),
    ("GSQ-RCO IQ3_S vs UD-IQ3_S", "gsq-rco-iq3s", "unsloth-ud-iq3s"),
    ("ours vs own base, IQ2_S", "ours-iq2s-fc128", "unsloth-ud-iq2s"),
    ("ours vs own base, Q2_K_XL", "ours-q2kxl-fc128", "unsloth-ud-q2kxl"),
    ("ours vs own base, IQ3_S", "ours-iq3s-fc128", "unsloth-ud-iq3s"),
    ("ours IQ2_S+corr vs UD-Q2_K_XL", "ours-iq2s-fc128", "unsloth-ud-q2kxl"),
]


def a1():
    print("## A1. Robust statistics\n")
    print("All differences are A − B of per-chunk mean KL divergence "
          "(nats/token); negative favours A. `p(t)` is two-sided with "
          "df = n − 1. Sign test is the exact two-sided binomial on non-tied "
          "chunks. Bootstrap: 10,000 resamples, seed "
          f"{SEED}, percentile interval. Block bootstrap resamples "
          f"non-overlapping contiguous blocks of {BLOCK} chunks; "
          "`t on block means` is a one-sample t on the block means "
          "(a check that does not assume independent chunks).\n")
    print("### A1.1 Qwen3.6-27B (heretic-v2), 100 chunks, wikitext-2 test\n")
    print(HEAD)
    res = {}
    for label, fa, fb in PAIRS_27B:
        s = describe(kld(E27 + fa)["kld"], kld(E27 + fb)["kld"])
        res[label] = s
        print(row(label, s))
    print("\nNLL and top-1 for the same pairs (t, with sign and Wilcoxon p):\n")
    print("| pair | ΔNLL mean ± sem | t | A better | sign p | Wilcoxon p | "
          "Δtop-1 (pt) | t | Wilcoxon p |")
    print("|---|---|---|---|---|---|---|---|---|")
    for label, fa, fb in PAIRS_27B:
        ra, rb = kld(E27 + fa), kld(E27 + fb)
        s = describe(ra["nll"], rb["nll"])
        u = describe(ra["top"], rb["top"], higher_better=True)
        print(f"| {label} | {s['mean']:+.5f} ± {s['sem']:.5f} | {s['t']:+.2f} "
              f"| {s['wins']}/{s['n']} | {fmt_p(s['p_sign'])} | "
              f"{fmt_p(s['p_wil'])} | {100*u['mean']:+.3f} | {u['t']:+.2f} | "
              f"{fmt_p(u['p_wil'])} |")

    # --- prefix check: is the 40-chunk run the first 40 of the 100?
    print("\n### A1.2 Is the 40-chunk run the prefix of the 100-chunk run?\n")
    print("Both scripts (measure40.sh, measure100.sh) read the same file, "
          "`wikitext-2-raw/wiki.test.raw`, at `-c 512`, against reference "
          "logits generated from the same BF16 model at 40 and 100 chunks. "
          "Numerical check, per configuration: maximum absolute difference "
          "between the per-chunk series of the 40-chunk log and the first 40 "
          "chunks of the 100-chunk log.\n")
    print("| config | max \\|ΔKLD\\| per chunk | max \\|Δ reference NLL\\| "
          "per chunk | mean KLD, 40-run | mean KLD, first 40 of 100-run |")
    print("|---|---|---|---|---|")
    for tag in ["MIXEDfc", "Q3_K_S", "CONTROL", "Q3_K_M", "Q2Krr-legacy",
                "Q2Kbare-legacy"]:
        k40 = np.array(kld(E27 + f"kld40-{tag}.log")["kld"])
        k100 = np.array(kld(E27 + f"kld100-{tag}.log")["kld"])[:40]
        b40 = base_nll_per_chunk(E27 + f"kld40-{tag}.log")
        b100 = base_nll_per_chunk(E27 + f"kld100-{tag}.log")[:40]
        print(f"| {tag} | {np.abs(k40 - k100).max():.5f} | "
              f"{np.abs(b40 - b100).max():.5f} | {k40.mean():.5f} | "
              f"{k100.mean():.5f} |")
    print("\nReading: the per-chunk series agree to the printed precision, "
          "so the first 40 chunks of the 100-chunk run are the same text, "
          "scored against the same reference distribution, as the 40-chunk "
          "run. The 100-chunk result contains the 40-chunk result as a "
          "prefix; it is not an independent sample.\n")

    print("### A1.3 Chunks 1–40 against chunks 41–100 (100-chunk logs)\n")
    print("| pair (A − B), KLD | chunks 1–40: mean ± sem (t; A better) | "
          "chunks 41–100: mean ± sem (t; A better) | Welch t between the two "
          "blocks (p) | chunks 1–20 mean (t) |")
    print("|---|---|---|---|---|")
    for label, fa, fb in PAIRS_27B:
        d = res[label]["d"]
        d1, d2, d0 = d[:40], d[40:], d[:20]

        def ms(x):
            m = x.mean()
            se = x.std(ddof=1) / math.sqrt(len(x))
            return m, se, m / se, int((x < 0).sum())
        m1, s1, t1, w1 = ms(d1)
        m2, s2, t2, w2 = ms(d2)
        m0, s0, t0, _ = ms(d0)
        w = stats.ttest_ind(d1, d2, equal_var=False)
        print(f"| {label} | {m1:+.5f} ± {s1:.5f} (t {t1:+.2f}; {w1}/40) | "
              f"{m2:+.5f} ± {s2:.5f} (t {t2:+.2f}; {w2}/60) | "
              f"{w.statistic:+.2f} ({fmt_p(w.pvalue)}) | "
              f"{m0:+.5f} ({t0:+.2f}) |")
    d = res[PAIRS_27B[0][0]]["d"]
    bm = d.reshape(10, 10).mean(axis=1)
    print("\nCorrected vs Q3_K_S, mean ΔKLD per block of 10 chunks "
          "(chunks 1–10, 11–20, …, 91–100): "
          + ", ".join(f"{x:+.4f}" for x in bm)
          + f". Blocks with A ahead: {(bm < 0).sum()}/10.")
    d = res[PAIRS_27B[1][0]]["d"]
    bm = d.reshape(10, 10).mean(axis=1)
    print("\nCorrected vs control, mean ΔKLD per block of 10 chunks: "
          + ", ".join(f"{x:+.4f}" for x in bm)
          + f". Blocks with the control ahead: {(bm > 0).sum()}/10.\n")

    # Bonferroni reading
    print("Multiplicity, for reference: a Bonferroni factor of 3 (three looks "
          "at n = 20, 40, 100) or 6 (the six pairs above) applied to the "
          "two-sided t p-values of the two contested pairs:\n")
    for label in (PAIRS_27B[0][0], PAIRS_27B[1][0]):
        s = res[label]
        print(f"- {label}: p(t) = {s['p_t']:.4f}; ×3 = "
              f"{min(1, 3*s['p_t']):.4f}; ×6 = {min(1, 6*s['p_t']):.4f}; "
              f"Wilcoxon p = {s['p_wil']:.4f} (×6 = "
              f"{min(1, 6*s['p_wil']):.4f}); sign p = {s['p_sign']:.4f}")
    print()

    for corpus, pre in (("wikitext-2 test", "kld40-"),
                        ("FineWeb-Edu", "kldfw40-")):
        print(f"### A1.4 GSQ-RCO head-to-head, Qwen3.8-27B, 40 chunks, "
              f"{corpus}\n")
        print("With n = 40 the block bootstrap has only four blocks; its "
              "interval and the block-means t (df 3) are coarse and are "
              "shown for completeness.\n")
        print(HEAD)
        for label, a, b in GSQ:
            s = describe(kld(E32 + pre + a + ".log")["kld"],
                         kld(E32 + pre + b + ".log")["kld"])
            res[(corpus, label)] = s
            print(row(label, s))
        print()
    return res


def a2(res):
    print("## A2. Bound on the difference between low-rank placements "
          "(27B, 100 chunks)\n")
    inc = -res["corrected vs mixed base (MIXEDbare)"]["mean"]
    print(f"Reference increment: the correction lowers its own mixed base by "
          f"{inc:.5f} KLD (A1.1, row 4). Each row is a placement minus the "
          "post-hoc corrected configuration (MIXEDfc), same bytes, same "
          "chunks, same reference. 90% intervals: Student t (df 99) and "
          "percentile bootstrap.\n")
    print("| placement vs corrected configuration | ΔKLD mean ± sem | t | "
          "90% CI (t) | 90% CI (bootstrap) | largest \\|bound\\| | as % of "
          "the increment | Δtop-1 pt (t) | ΔNLL (t) |")
    print("|---|---|---|---|---|---|---|---|---|")
    worst = 0.0
    base = kld(E27 + "kld100-MIXEDfc.log")
    for label, f in [("pre-quantization carve (SRR order)", "kld100-SRR.log"),
                     ("shared basis, byte parity", "kld100-MIXEDfc-sharedA.log"),
                     ("shared basis, rank 128", "kld100-MIXEDfc-sharedA-r128.log")]:
        r = kld(E27 + f)
        s = describe(r["kld"], base["kld"])
        tq = stats.t.ppf(0.95, s["n"] - 1)
        lo, hi = s["mean"] - tq * s["sem"], s["mean"] + tq * s["sem"]
        rng = np.random.default_rng(SEED)
        blo, bhi = boot_ci(s["d"], level=0.90, rng=rng)
        bound = max(abs(lo), abs(hi), abs(blo), abs(bhi))
        worst = max(worst, bound)
        u = describe(r["top"], base["top"], higher_better=True)
        v = describe(r["nll"], base["nll"])
        print(f"| {label} | {s['mean']:+.5f} ± {s['sem']:.5f} | {s['t']:+.2f} "
              f"| [{lo:+.5f}, {hi:+.5f}] | [{blo:+.5f}, {bhi:+.5f}] | "
              f"{bound:.5f} | {100*bound/inc:.0f}% | {100*u['mean']:+.3f} "
              f"({u['t']:+.2f}) | {v['mean']:+.5f} ({v['t']:+.2f}) |")
    print(f"\nAcross the three placements the largest 90% bound is "
          f"{worst:.4f} KLD, {100*worst/inc:.0f}% of the correction's own "
          f"increment ({inc:.4f}). The data therefore bound the difference "
          "between placements at that size; they do not show the placements "
          "are identical.\n")
    print("Each placement against the same-byte mixed-type control "
          "(placement − control):\n")
    print("| placement vs control | ΔKLD mean ± sem | t | Wilcoxon p |")
    print("|---|---|---|---|")
    ctl = kld(E27 + "kld100-CONTROL.log")
    for label, f in [("post-hoc correction", "kld100-MIXEDfc.log"),
                     ("pre-quantization carve", "kld100-SRR.log"),
                     ("shared basis, byte parity", "kld100-MIXEDfc-sharedA.log"),
                     ("shared basis, rank 128", "kld100-MIXEDfc-sharedA-r128.log")]:
        s = describe(kld(E27 + f)["kld"], ctl["kld"])
        print(f"| {label} | {s['mean']:+.5f} ± {s['sem']:.5f} | {s['t']:+.2f} "
              f"| {fmt_p(s['p_wil'])} |")
    print()


def final_line(rel):
    f = kld(rel)["final"]
    return f["ppl"], f["kld"], 100 * f["top"]


def a3():
    print("## A3. Re-rounded Q2_K against stock rungs\n")
    print("### A3.1 Qwen3.5-0.8B (40 chunks, BF16 reference)\n")
    rr = "24-yaqa-lite/kld-rr-input.log"
    bare = "24-yaqa-lite/kld-bare.log"
    e34 = "34-e16-nvfp4-08b/"
    b_rr = base_nll_per_chunk(rr)
    print("Reference/chunk identity check (per-chunk reference NLL recovered "
          "from each log; E24 logs are from 2026-08-04, E34 logs from "
          "2026-09-11, both scored against `bf16ref08b-40.logits`):\n")
    for tag in ["iq3_xxs", "iq3_xs", "iq3_s", "q3_k_m"]:
        b = base_nll_per_chunk(e34 + f"kld40-{tag}.log")
        print(f"- E24 re-round log vs E34 {tag} log: max |Δ reference NLL| "
              f"per chunk = {np.abs(b - b_rr).max():.5f} over "
              f"{min(len(b), len(b_rr))} chunks")
    print("\nSizes: re-rounded and bare Q2_K 436,408,832 bytes "
          "(22-qwen35-08b/results.txt); E34 rungs from "
          "34-e16-nvfp4-08b/results-e16-08b.txt.\n")
    sizes = {}
    for line in open(P(e34 + "results-e16-08b.txt")):
        m = re.match(r"(\S+) bytes=(\d+)", line)
        if m:
            sizes[m.group(1)] = int(m.group(2))
    print("| artifact | MB | PPL (40 chunks) | KLD | top-1 % |")
    print("|---|---|---|---|---|")
    for label, rel, mb in [("Q2_K bare", bare, 436408832),
                           ("Q2_K re-rounded", rr, 436408832),
                           ("IQ3_XXS (stock)", e34 + "kld40-iq3_xxs.log", sizes["iq3_xxs"]),
                           ("IQ3_XS (stock)", e34 + "kld40-iq3_xs.log", sizes["iq3_xs"]),
                           ("IQ3_S (stock)", e34 + "kld40-iq3_s.log", sizes["iq3_s"]),
                           ("Q3_K_M (stock)", e34 + "kld40-q3_k_m.log", sizes["q3_k_m"])]:
        ppl, k, top = final_line(rel)
        print(f"| {label} | {mb/1e6:.1f} | {ppl:.3f} | {k:.4f} | {top:.2f} |")
    print("\nPaired, re-rounded Q2_K (A) minus stock rung (B):\n")
    print(HEAD)
    out = {}
    for tag in ["iq3_xxs", "iq3_xs", "iq3_s", "q3_k_m"]:
        s = describe(kld(rr)["kld"], kld(e34 + f"kld40-{tag}.log")["kld"])
        out[tag] = s
        print(row(f"re-rounded Q2_K vs {tag.upper()} "
                  f"({sizes[tag]/1e6:.0f} MB)", s))
    print("\nNLL and top-1, re-rounded Q2_K minus IQ3_XXS:")
    ra, rb = kld(rr), kld(e34 + "kld40-iq3_xxs.log")
    s = describe(ra["nll"], rb["nll"])
    u = describe(ra["top"], rb["top"], higher_better=True)
    print(f"ΔNLL {s['mean']:+.4f} ± {s['sem']:.4f} (t {s['t']:+.2f}, A better "
          f"{s['wins']}/40); Δtop-1 {100*u['mean']:+.2f} pt (t {u['t']:+.2f}, "
          f"A better {u['wins']}/40).")
    print("\nFull-corpus perplexity (unpaired, from result files): "
          "re-rounded Q2_K 28.09 (24-yaqa-lite/README.md), IQ3_XXS 23.95 at "
          "412,082,176 bytes (22-qwen35-08b/results.txt). The E34 IQ3_XXS "
          "file is a rebuild from the same BF16 at the same byte count "
          "(KLD 0.2577 here against 0.2573 in E22).\n")

    print("### A3.2 Qwen3.6-27B\n")
    print("Sizes and provenance differ between the two sides: the re-rounded "
          "Q2_K is the legacy pair (a requantization of Q6_K, 10.86 GB "
          "nominal; scored at 40 and 100 chunks), and IQ3_XXS is a "
          "single-step BF16 quant (11,478,442,368 bytes; scored at 20 chunks "
          "only, 27-bf16-rederivation/results.txt). IQ3_XXS is the LARGER "
          "file, by about 0.6 GB; no stock rung at or below 10.86 GB was "
          "measured under the BF16 reference.\n")
    b20 = base_nll_per_chunk(E27 + "kld-IQ3_XXS.log")
    b100 = base_nll_per_chunk(E27 + "kld100-Q2Krr-legacy.log")[:20]
    q20 = np.array(kld(E27 + "kld-Q3_K_S.log")["kld"])
    q100 = np.array(kld(E27 + "kld100-Q3_K_S.log")["kld"])[:20]
    print(f"Chunk/reference identity for chunks 1–20: max |Δ reference NLL| "
          f"between the 20-chunk IQ3_XXS log and the first 20 chunks of the "
          f"100-chunk re-round log = {np.abs(b20 - b100).max():.5f}; the same "
          f"artifact (Q3_K_S) scored in both runs differs by at most "
          f"{np.abs(q20 - q100).max():.5f} KLD per chunk. The 20-chunk "
          "reference is therefore the same text and the same reference "
          "distribution as the first 20 chunks of the 100-chunk run, and the "
          "pair below is a valid pairing on chunks 1–20, with the provenance "
          "difference standing.\n")
    print(HEAD)
    a = np.array(kld(E27 + "kld100-Q2Krr-legacy.log")["kld"])[:20]
    b = np.array(kld(E27 + "kld-IQ3_XXS.log")["kld"])
    s = describe(a, b)
    out["27b"] = s
    print(row("re-rounded Q2_K (legacy, 10.86 GB) vs IQ3_XXS (11.48 GB), "
              "chunks 1–20", s))
    a2_ = np.array(kld(E27 + "kld100-Q2Kbare-legacy.log")["kld"])[:20]
    s2 = describe(a2_, b)
    print(row("bare Q2_K (legacy, 10.86 GB) vs IQ3_XXS, chunks 1–20", s2))
    print(f"\nMeans on chunks 1–20: re-rounded Q2_K {a.mean():.4f}, bare "
          f"legacy Q2_K {a2_.mean():.4f}, IQ3_XXS {b.mean():.4f}. "
          "With n = 20 the block statistics have two blocks and are not "
          "informative.\n")
    return out


def a4():
    print("## A4. MoE reference: tensor types of the expert stacks\n")
    log = P("23-moe/quantize-ctrl.log")
    print("Source 1: `23-moe/quantize-ctrl.log` (llama-quantize, input = the "
          "vendor UD-Q4_K_M reference, output = the promotion control). "
          "llama-quantize prints, per tensor, `type = <input type>, "
          "converting to <output type>`.\n")
    counts = {}
    for line in open(log, errors="replace"):
        m = re.search(r"(ffn_(?:down|up|gate)_exps)\.weight.*type =\s*(\w+).*"
                      r"converting to (\w+)", line)
        if m:
            key = (m.group(1), m.group(2), m.group(3))
            counts[key] = counts.get(key, 0) + 1
    print("| tensor kind | type in reference | type in control | layers |")
    print("|---|---|---|---|")
    for (kind, src, dst), c in sorted(counts.items()):
        print(f"| {kind} | {src} | {dst} | {c} |")
    hdr = [line.strip() for line in open(log, errors="replace")
           if re.search(r"llama_model_loader: - type", line)]
    print("\nReference file tensor-type census from the same log: "
          + "; ".join(h.split("- type")[1].strip() for h in hdr) + ".\n")
    # optional header read (no weights are loaded; GGUFReader memory-maps)
    ref = "/home/max/Documents/openbeast/weights/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
    try:
        sys.path.insert(0, "/home/max/Documents/openbeast/llama.cpp/gguf-py")
        from gguf import GGUFReader
        r = GGUFReader(ref)
        c2 = {}
        for t in r.tensors:
            m = re.search(r"(ffn_(?:down|up|gate)_exps)", t.name)
            if m:
                k = (m.group(1), t.tensor_type.name)
                c2[k] = c2.get(k, 0) + 1
        print(f"Source 2: GGUF header of `{ref}` read with gguf-py "
              "(memory-mapped; only the tensor-info table is touched, no "
              "weights are read and nothing is loaded on a GPU):\n")
        print("| tensor kind | type | count |")
        print("|---|---|---|")
        for (kind, ty), c in sorted(c2.items()):
            print(f"| {kind} | {ty} | {c} |")
        print()
    except Exception as e:  # noqa: BLE001
        print(f"Source 2 (GGUF header via gguf-py) not available: "
              f"{type(e).__name__}: {e}\n")
    print("Reading: in the reference, `ffn_down_exps` is Q5_K in 37 layers "
          "and Q6_K in 3; it is not Q4_K. The control's promotion to Q4_K is "
          "therefore a real requantization step from a higher-precision "
          "stored tensor, not a near-identity copy of the reference. The "
          "up/gate stacks are Q4_K in the reference and Q2_K in every row of "
          "the table. The reference-favours-the-control mechanism raised in "
          "review does not apply in the form stated; what remains is that "
          "the reference is itself a quantized model, so all rows measure "
          "distance to a Q4/Q5/Q6-mixed artifact, and n = 20.\n")


def a4b():
    print("### A4.1 MoE paired contrasts (n = 20, reference = vendor "
          "UD-Q4_K_M logits)\n")
    m = "23-moe/"
    print("| pair (A − B), KLD | mean ± sem | t | A better | sign p | "
          "Wilcoxon p |")
    print("|---|---|---|---|---|---|")
    for label, a, b in [
            ("per-expert correction vs Q2_K base", "ppl-pe.log", "ppl-base.log"),
            ("shared-basis correction vs Q2_K base", "ppl-sh.log", "ppl-base.log"),
            ("per-expert correction vs promotion control", "ppl-pe.log", "ppl-ctrl.log"),
            ("shared-basis correction vs promotion control", "ppl-sh.log", "ppl-ctrl.log"),
            ("promotion control vs Q3_K_S", "ppl-ctrl.log", "ppl-q3ks.log")]:
        s = describe(kld(m + a)["kld"], kld(m + b)["kld"])
        print(f"| {label} | {s['mean']:+.5f} ± {s['sem']:.5f} | {s['t']:+.2f} "
              f"| {s['wins']}/{s['n']} | {fmt_p(s['p_sign'])} | "
              f"{fmt_p(s['p_wil'])} |")
    print()


def a5():
    print("## A5. Paired 2048-token perplexity (GSQ-RCO vs its Unsloth "
          "baseline)\n")
    print("Per-chunk NLL recovered from the cumulative `[k]ppl` stream of "
          "the four `ppl2048-*.log` files (full wikitext-2 test at "
          "`-c 2048`).\n")
    print("| pair (A − B) | n | final PPL A | final PPL B | ΔNLL mean ± sem | "
          "t | 95% bootstrap CI | A better | sign p | Wilcoxon p | block-means "
          "t (blocks of 10) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for label, a, b in [("GSQ-RCO IQ2_XS vs UD-IQ2_S", "gsq-rco-iq2xs", "unsloth-ud-iq2s"),
                        ("GSQ-RCO IQ3_S vs UD-IQ3_S", "gsq-rco-iq3s", "unsloth-ud-iq3s")]:
        ca = parse_ppl_stream(P(E32 + f"ppl2048-{a}.log"))
        cb = parse_ppl_stream(P(E32 + f"ppl2048-{b}.log"))
        na = per_chunk_from_cum([math.log(v) for v in ca])
        nb = per_chunk_from_cum([math.log(v) for v in cb])
        s = describe(na, nb)
        print(f"| {label} | {s['n']} | {ca[-1]:.4f} | {cb[-1]:.4f} | "
              f"{s['mean']:+.5f} ± {s['sem']:.5f} | {s['t']:+.2f} | "
              f"[{s['lo']:+.5f}, {s['hi']:+.5f}] | {s['wins']}/{s['n']} | "
              f"{fmt_p(s['p_sign'])} | {fmt_p(s['p_wil'])} | "
              f"{s['bt']:+.2f} (df {s['nb']-1}, p {fmt_p(s['bp'])}) |")
    print("\n512-token NLL for the same pairs (from the 40-chunk KLD logs, "
          "wikitext):\n")
    print("| pair (A − B) | ΔNLL mean ± sem | t | A better |")
    print("|---|---|---|---|")
    for label, a, b in [("GSQ-RCO IQ2_XS vs UD-IQ2_S", "gsq-rco-iq2xs", "unsloth-ud-iq2s"),
                        ("GSQ-RCO IQ3_S vs UD-IQ3_S", "gsq-rco-iq3s", "unsloth-ud-iq3s")]:
        s = describe(kld(E32 + f"kld40-{a}.log")["nll"],
                     kld(E32 + f"kld40-{b}.log")["nll"])
        print(f"| {label} | {s['mean']:+.5f} ± {s['sem']:.5f} | {s['t']:+.2f} "
              f"| {s['wins']}/{s['n']} |")
    print()


def a6(res):
    print("## A6. Small derived numbers quoted in the paper\n")
    # 0.6B unpaired distance (13-rerounder/results.txt)
    a, sa, b, sb = 43.3339, 0.38435, 35.5441, 0.31856
    z = (a - b) / math.hypot(sa, sb)
    print(f"- 0.6B re-round, unpaired PPL distance: ({a} − {b}) / "
          f"sqrt({sa}² + {sb}²) = {z:.1f} standard errors "
          "(13-rerounder/results.txt). Percent PPL change "
          f"{100*(b-a)/a:.1f}%.")
    # GSQ byte totals
    ad = 900_813_120
    for name, by in [("UD-IQ2_S", 8371970048), ("UD-Q2_K_XL", 9828981664),
                     ("UD-IQ3_S", 12040883104)]:
        print(f"- {name} base {by/1e9:.2f} GB + adapter {ad/1e9:.2f} GB = "
              f"{(by+ad)/1e9:.2f} GB total.")
    print("- GSQ-RCO IQ2_XS 8.42 GB (8,422,841,472 bytes); GSQ-RCO IQ3_S "
          "11.77 GB (11,771,546,784 bytes); UD-Q2_K_XL 9.83 GB "
          "(32-t117-gsq-head-to-head/results-40ch.txt; adapter bytes from "
          "JOURNAL 2026-09-10 15:20).")
    w = res[("wikitext-2 test", "ours IQ2_S+corr vs GSQ-RCO IQ2_XS")]["mean"]
    g = res[("wikitext-2 test", "GSQ-RCO IQ2_XS vs UD-IQ2_S")]["mean"]
    o = res[("wikitext-2 test", "ours vs own base, IQ2_S")]["mean"]
    print(f"- Wikitext IQ2 gap decomposition: ours − GSQ = {w:+.4f} = "
          f"−(GSQ − baseline = {g:+.4f}) + (ours − baseline = {o:+.4f}); "
          f"the untrained baseline accounts for {100*g/-w:.0f}% of the gap, "
          f"the correction for {100*o/w:.0f}%.")
    w = res[("wikitext-2 test", "ours IQ3_S+corr vs GSQ-RCO IQ3_S")]["mean"]
    g = res[("wikitext-2 test", "GSQ-RCO IQ3_S vs UD-IQ3_S")]["mean"]
    o = res[("wikitext-2 test", "ours vs own base, IQ3_S")]["mean"]
    print(f"- Wikitext IQ3 gap decomposition: ours − GSQ = {w:+.4f}; "
          f"baseline already ahead by {g:.4f} ({100*g/-w:.0f}%), correction "
          f"adds {o:+.4f} ({100*o/w:.0f}%).")
    for rung in ["IQ2_S", "Q2_K_XL", "IQ3_S"]:
        a_ = res[("wikitext-2 test", f"ours vs own base, {rung}")]["mean"]
        b_ = res[("FineWeb-Edu", f"ours vs own base, {rung}")]["mean"]
        print(f"- Increment at {rung}: wikitext {a_:+.4f}, FineWeb-Edu "
              f"{b_:+.4f}; change in magnitude {100*(abs(b_)-abs(a_))/abs(a_):+.0f}%.")
    for rung, lab in [("IQ2", "GSQ-RCO IQ2_XS vs UD-IQ2_S"),
                      ("IQ3", "GSQ-RCO IQ3_S vs UD-IQ3_S")]:
        a_ = res[("wikitext-2 test", lab)]["mean"]
        b_ = res[("FineWeb-Edu", lab)]["mean"]
        print(f"- GSQ-RCO deficit vs its baseline at {rung}: wikitext "
              f"{a_:+.4f}, FineWeb-Edu {b_:+.4f} (ratio {a_/b_:.1f}×).")
    # IQ1 exchange rates (RESULTS_ROLLUP.md, legacy 27B tables)
    print(f"- Carrier-bit exchange rate at 27B (RESULTS_ROLLUP.md legacy "
          f"rows): IQ1_S (7.44 GB, 0.891) → Q2_K (10.86 GB, 0.153): "
          f"{(0.891-0.153)/(10.86-7.44):.3f} KLD/GB; IQ1_S → IQ2_XS "
          f"(9.38 GB, 0.2642): {(0.891-0.2642)/(9.38-7.44):.3f} KLD/GB. "
          f"Adapter: (0.527 − 0.431) / (9.96 − 8.24 GB) = "
          f"{(0.527-0.431)/(9.96-8.24):.3f} KLD/GB. Ratios "
          f"{((0.891-0.153)/(10.86-7.44))/((0.527-0.431)/(9.96-8.24)):.1f}× "
          f"and {((0.891-0.2642)/(9.38-7.44))/((0.527-0.431)/(9.96-8.24)):.1f}×.")
    print(f"- IQ1_S bits per weight from file bytes: 7.44e9 × 8 / 27.329e9 "
          f"parameters = {7.44e9*8/27.329e9:.2f} bpw (file convention); "
          "1.56 bpw is the nominal format figure.")
    print(f"- Recovery-curve adapter budgets (rollup GB columns): "
          f"11.79 − 10.86 = {11.79-10.86:.2f} GB; 10.31 − 9.38 = "
          f"{10.31-9.38:.2f} GB; 8.24 − 7.44 = {8.24-7.44:.2f} GB.")
    # MoE per-byte
    pe, ctl, q3 = 0.0181 / 2.674, 0.0438 / 2.517, 0.0954 / 3.333
    print(f"- MoE per-byte KLD reduction over the pinned Q2_K base "
          f"(23-moe/README.md): per-expert adapter 0.0181 / 2.674 GB = "
          f"{pe:.4f}/GB; promotion control (0.2104 − 0.1666 = 0.0438) / "
          f"(14.366 − 11.849 = 2.517 GB) = {ctl:.4f}/GB, {ctl/pe:.1f}× the "
          f"adapter; Q3_K_S (0.2104 − 0.1150 = 0.0954) / (15.182 − 11.849 = "
          f"3.333 GB) = {q3:.4f}/GB, {q3/pe:.1f}× the adapter.")
    print(f"- Kronecker give-back (24-yaqa-lite/README.md): NLL 0.0483 / "
          f"0.1637 = {100*0.0483/0.1637:.1f}%; KLD 0.0475 / 0.1668 = "
          f"{100*0.0475/0.1668:.1f}%.")
    print(f"- Adapter decode cost on stock llama.cpp at 27B "
          f"(RESULTS_ROLLUP.md): 99.7 → 66.5 tok/s = "
          f"{100*(66.5-99.7)/99.7:.0f}% (F16 rank-64 factors); 99.7 → 81.0 = "
          f"{100*(81.0-99.7)/99.7:.0f}% (Q8 factors).")
    print(f"- Allocator fix, two sessions (25-alloc-concurrency/REPORT.md): "
          f"763 → 849 = {100*(849-763)/763:+.1f}%; 794 → 950 = "
          f"{100*(950-794)/794:+.1f}%.")
    print(f"- Extraction wall time (extract-UD-*.log): 724 s, 727 s, 744 s = "
          f"{724/60:.1f}–{744/60:.1f} minutes.")
    print(f"- 2048-token PPL, unpaired distance at IQ3: (6.4173 − 6.3055) / "
          f"sqrt(0.04074² + 0.03942²) = "
          f"{(6.4173-6.3055)/math.hypot(0.04074, 0.03942):.2f} standard "
          "errors (the paired statistic is in A5).")
    print(f"- Capability, per language (capability-verdict-iq3-final.txt): "
          f"Zig 9/30 vs 2/30; non-Zig {88-9}/82 vs {76-2}/82. Net +12 = "
          f"Zig +7, non-Zig +5. Reference band 82, 84, 85, 91 "
          f"(range 9 units).")
    print()


def main():
    print("# E35 — final reanalysis: results\n")
    print("Generated by `reanalysis.py` (CPU only; existing logs only). "
          "Do not edit by hand; re-run the script.\n")
    res = a1()
    a2(res)
    a3()
    a4()
    a4b()
    a5()
    a6(res)


if __name__ == "__main__":
    main()
