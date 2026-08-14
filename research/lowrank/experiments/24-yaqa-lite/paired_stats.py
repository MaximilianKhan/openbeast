#!/usr/bin/env python3
"""Paired per-chunk statistics from llama-perplexity logs (R1 repair).

llama-perplexity prints RUNNING (cumulative) means, not per-chunk rows.
Both are recoverable exactly by differencing the cumulative stream:
  per-chunk mean_k = k*cum_k - (k-1)*cum_{k-1}
(each chunk contributes the same token count, so the weights are equal;
the mean of the recovered per-chunk series telescopes back to the final
printed value exactly — parse noise only inflates the sem, never the
mean).

Modes:
  ppl  <logA> <logB>   plain-PPL logs ("[k]ppl," stream): paired per-
                       chunk mean-NLL deltas (A - B), plus PPL summary.
  kld  <logA> <logB>   --kl-divergence logs: paired per-chunk deltas of
                       KLD, mean-NLL, and same-top fraction.
"""
import math
import re
import sys

FLOAT = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"


def parse_ppl_stream(path):
    """-> list of cumulative PPL values, index k-1 = after chunk k."""
    text = open(path).read()
    vals = [float(m.group(2))
            for m in re.finditer(r"\[(\d+)\](" + FLOAT + ")", text)]
    if not vals:
        sys.exit(f"no [k]ppl stream found in {path}")
    return vals


def per_chunk_from_cum(cum):
    """cumulative means -> per-chunk means (equal weights)."""
    out = []
    prev = 0.0
    for k, c in enumerate(cum, 1):
        out.append(k * c - (k - 1) * prev)
        prev = c
    return out


def parse_kld_rows(path):
    """-> dict of per-chunk-recovered series: nll, kld, top."""
    rows = []
    for line in open(path):
        if "±" not in line:
            continue
        nums = re.findall(FLOAT, line.replace("%", ""))
        if len(nums) == 11 and re.match(r"\s*\d+\s", line):
            nums = [float(x) for x in nums]
            rows.append({"chunk": int(nums[0]), "ppl": nums[1],
                         "lnratio": nums[3], "kld": nums[5],
                         "top": nums[9] / 100.0})
    if not rows:
        sys.exit(f"no per-chunk KLD rows found in {path}")
    rows.sort(key=lambda r: r["chunk"])
    cum_nll = [math.log(r["ppl"]) for r in rows]
    cum_kld = [r["kld"] for r in rows]
    cum_top = [r["top"] for r in rows]
    return {"nll": per_chunk_from_cum(cum_nll),
            "kld": per_chunk_from_cum(cum_kld),
            "top": per_chunk_from_cum(cum_top),
            "final": rows[-1]}


def paired(a, b, label, unit="", higher_better=False):
    n = min(len(a), len(b))
    if len(a) != len(b):
        print(f"  WARNING: chunk counts differ ({len(a)} vs {len(b)}), "
              f"pairing first {n}")
    d = [x - y for x, y in zip(a[:n], b[:n])]
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / (n - 1)
    sem = math.sqrt(var / n)
    t = mean / sem if sem > 0 else float("inf")
    better = sum(1 for x in d if (x > 0 if higher_better else x < 0))
    print(f"  {label:24s} paired dmean = {mean:+.6f} ± {sem:.6f}{unit}  "
          f"(t = {t:+.2f}, n = {n}, A better on {better}/{n} chunks)")
    return mean, sem, t


def main():
    mode, fa, fb = sys.argv[1], sys.argv[2], sys.argv[3]
    print(f"paired per-chunk stats: A = {fa}\n" + " " * 24 + f"B = {fb}")
    if mode == "ppl":
        ca, cb = parse_ppl_stream(fa), parse_ppl_stream(fb)
        na, nb = per_chunk_from_cum([math.log(v) for v in ca]), \
            per_chunk_from_cum([math.log(v) for v in cb])
        print(f"  final PPL: A = {ca[-1]:.4f}  B = {cb[-1]:.4f}")
        paired(na, nb, "mean NLL (nats/tok)")
    elif mode == "kld":
        ra, rb = parse_kld_rows(fa), parse_kld_rows(fb)
        print(f"  final: A KLD {ra['final']['kld']:.5f} "
              f"top {100*ra['final']['top']:.3f}%  |  "
              f"B KLD {rb['final']['kld']:.5f} "
              f"top {100*rb['final']['top']:.3f}%")
        paired(ra["kld"], rb["kld"], "KLD (nats/tok)")
        paired(ra["nll"], rb["nll"], "mean NLL (nats/tok)")
        paired(ra["top"], rb["top"], "same-top fraction",
               higher_better=True)
    else:
        sys.exit("mode must be ppl|kld")


if __name__ == "__main__":
    main()
