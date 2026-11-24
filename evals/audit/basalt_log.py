#!/usr/bin/env python3
"""Speed, draft acceptance and the #588 count of one arm, from a saved Basalt
server log (`docker logs --timestamps basalt-serve`, kept per container by the
ablation queue's log saver since 2026-11-24; arms I_T2_s1 and I_T2_stock).

What the server writes per request (Basalt v0.5.7, one line when it ends):

    <docker ts> [server] s1 done  pp=4734/4620c 0.1s [15755t/s]  tg=3630 447.8t/s
        acc=74% dl=3.1  ttft=0.1s  end=stop  hit=100.0%  pcie=0.0%

pp = prompt tokens / of them cached, the prefill time (and its rate when
there was uncached work to time); tg = generated tokens and the decode rate;
acc = the share of DSpark's drafted tokens accepted; dl = tokens emitted per
verify window (1 + accepted drafts); end = stop or length (the output cap).
A line without acc/dl had no draft (a very short reply). And, per reply that
ran to the cap while still thinking:

    [server] warning: max tokens reached while thinking, no answer ...

which is the server's own count of the empty capped turns (upstream #588)
that evals/audit/capped_turns.py counts from the transcripts.

Only lines whose docker timestamp (UTC) falls inside --since/--until are
counted, so the serve script's probe requests before the arm started are
left out: pass the arm's START and END lines from campaign.log (UTC). Those
lines are whole seconds, so a whole-second --until covers that second to its
end (the last reply can land a fraction of a second before the END line).

    python3 evals/audit/basalt_log.py LOG --since 2026-11-24T09:56:55 \\
        --until 2026-11-24T14:07:28 [--cap 8192] [--json out.json]

Rates are the server's own per-request figures. "Aggregate decode" is all
generated tokens over all decode time (tg / rate summed), so long replies
weigh by their length; the per-request percentiles weigh each reply once.
Acceptance is reported per request and weighted by generated tokens.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timezone

DONE = re.compile(
    r"^(?P<ts>\S+) \[server\] s\d+ done\s+pp=(?P<pp>\d+)/(?P<cached>\d+)c (?P<pps>[\d.]+)s"
    r"(?: (?P<pprate>[\d.]+)t/s)?\s+tg=(?P<tg>\d+) (?P<tgrate>[\d.]+)t/s"
    r"(?: acc=(?P<acc>\d+)% dl=(?P<dl>[\d.]+))?\s+ttft=(?P<ttft>[\d.]+)s\s+end=(?P<end>\w+)")
NOANSWER = re.compile(r"^(?P<ts>\S+) \[server\] warning: max tokens reached while thinking, no answer")


def _ts(s: str) -> float:
    """A docker --timestamps stamp (RFC 3339, nanoseconds, Z) or a naive ISO time read as UTC."""
    s = s.rstrip("Z")
    if "." in s:
        head, frac = s.split(".", 1)
        s = head + "." + frac[:6]
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


def parse(lines, since: float | None = None, until: float | None = None) -> dict:
    reqs, noanswer = [], 0
    for line in lines:
        m = DONE.match(line)
        n = None if m else NOANSWER.match(line)
        hit = m or n
        if not hit:
            continue
        t = _ts(hit.group("ts"))
        if (since is not None and t < since) or (until is not None and t > until):
            continue
        if n:
            noanswer += 1
            continue
        reqs.append({
            "pp": int(m["pp"]), "cached": int(m["cached"]), "pp_s": float(m["pps"]),
            "pp_rate": float(m["pprate"]) if m["pprate"] else None,
            "tg": int(m["tg"]), "tg_rate": float(m["tgrate"]),
            "acc": int(m["acc"]) if m["acc"] else None,
            "dl": float(m["dl"]) if m["dl"] else None,
            "end": m["end"]})
    return {"requests": reqs, "no_answer": noanswer}


def _pct(xs: list, p: float):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def _dist(xs: list) -> dict:
    return {"n": len(xs), "p10": _pct(xs, 0.1), "median": statistics.median(xs) if xs else None,
            "p90": _pct(xs, 0.9)}


def summary(parsed: dict, cap: int = 8192, min_tg: int = 256) -> dict:
    reqs = parsed["requests"]
    ends: dict = {}
    for r in reqs:
        ends[r["end"]] = ends.get(r["end"], 0) + 1
    timed = [r for r in reqs if r["tg"] > 0 and r["tg_rate"] > 0]
    tg_s = sum(r["tg"] / r["tg_rate"] for r in timed)
    tg_tok = sum(r["tg"] for r in timed)
    long_ = [r for r in timed if r["tg"] >= min_tg]
    full = [r for r in timed if r["tg"] == cap]
    drafted = [r for r in timed if r["acc"] is not None]
    w = sum(r["tg"] for r in drafted)
    prefill = [r for r in reqs if r["pp_rate"] and r["pp"] - r["cached"] >= 1000]
    return {
        "requests": len(reqs),
        "ends": ends,
        "no_answer_warnings": parsed["no_answer"],
        "generated_tokens": tg_tok,
        "decode_seconds": round(tg_s, 1),
        "prefill_seconds": round(sum(r["pp_s"] for r in reqs), 1),
        "aggregate_decode_tps": round(tg_tok / tg_s, 1) if tg_s else None,
        f"decode_tps_replies_ge_{min_tg}": _dist([r["tg_rate"] for r in long_]),
        f"decode_tps_replies_at_{cap}": _dist([r["tg_rate"] for r in full]),
        "acceptance_pct": {**_dist([r["acc"] for r in drafted]),
                           "tg_weighted_mean": round(sum(r["acc"] * r["tg"] for r in drafted) / w, 1)
                           if w else None},
        "tokens_per_window": {**_dist([r["dl"] for r in drafted]),
                              "tg_weighted_mean": round(sum(r["dl"] * r["tg"] for r in drafted) / w, 2)
                              if w else None},
        "requests_without_draft": len(timed) - len(drafted),
        "prompt_tokens": sum(r["pp"] for r in reqs),
        "cached_share": round(sum(r["cached"] for r in reqs) / max(1, sum(r["pp"] for r in reqs)), 3),
        "prefill_tps_uncached_ge_1000": _dist([r["pp_rate"] for r in prefill]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", help="a saved Basalt server log (docker logs --timestamps)")
    ap.add_argument("--since", help="the arm's START (campaign.log, UTC, ISO)")
    ap.add_argument("--until", help="the arm's END (campaign.log, UTC, ISO)")
    ap.add_argument("--cap", type=int, default=8192, help="the per-turn output cap (default 8192)")
    ap.add_argument("--json", help="write the summary here")
    a = ap.parse_args(argv)
    with open(a.log, errors="replace") as f:
        until = None
        if a.until:
            until = _ts(a.until) + (0 if "." in a.until else 0.999999)
        p = parse(f, _ts(a.since) if a.since else None, until)
    s = summary(p, a.cap)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(s, f, indent=1)
    json.dump(s, sys.stdout, indent=1)
    print()
    return 0 if s["requests"] else 1


if __name__ == "__main__":
    sys.exit(main())
