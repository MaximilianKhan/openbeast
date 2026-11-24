#!/usr/bin/env python3
"""Per-unit counts of the agent turns that ran to the output cap, from the
transcripts an integrity scan attributed (evals/audit/integrity_scan.py --json).

  python3 evals/audit/capped_turns.py SCAN.json [--label I_T2] [--logs-dir DIR] [--json OUT]

Why: on a server whose thinking-budget close does not force an answer
(Basalt at 400b411c on Inkling 2 Mini, upstream issue #588), a turn can think
again after the close and end at the cap with no answer and no tool call.
The server log names those replies, but it goes with the container; the
transcript keeps enough to count them (2026-11-24, arm I_T2).

What a transcript records (agents/runner.py, harness9 and later):

  response_model  finish_reason of every reply ("length" = it hit the cap)
  output_cap      a reply that hit the cap with NO tool call; it carries
                  reasoning_chars only when the reply's content was empty
                  (runner: `reasoning = "" if content.strip() else ...`)
  tool_call       one per call the server returned for that turn

So, per unit:

  turns           replies (iteration events)
  length          replies that ended at the cap
  empty           capped replies with no answer and no call (output_cap with
                  reasoning_chars): the #588 signature on Basalt. On a server
                  whose thinking can run to the whole cap (llama.cpp with a
                  reasoning budget >= the cap) the same count is simply a turn
                  that thought to the cap
  capped_text     capped replies with no call but some answer text
  length_call     capped replies that still carried a tool call. On Basalt a
                  call whose JSON is whole is emitted even when the cap cut
                  its end marker (serve/models/inkling2.py finish/_whole), one
                  of its lenient parsing paths; the transcript cannot say which
                  path produced a call
  rate_8192       end-to-end tokens/s of each reply that hit an 8,192-token cap
                  exactly (8,192 / the time from the turn's start to its
                  response): prefill and request overhead included, so a lower
                  bound on decode speed

And, on a harness10 arm with the forced-act turn (OPENBEAST_AGENT_FORCE_ACT=1:
after N capped thinking turns in a row the next request goes with
enable_thinking=false and must act), from its `force_act_result` events
(tool_calls, content_chars, reasoning_chars, finish_reason of that reply):

  forced          forced-act turns
  forced_thought  of them, replies that still carried reasoning (the server
                  did not stop the model thinking: on Basalt, Inkling writes a
                  thinking block at reasoning_effort "none", with no budget)
  forced_length   of them, replies that ran to the cap
  forced_call     of them, replies with a tool call (the turn did its job)
  forced_empty    of them, replies with no call and no content (each is also
                  one of the `empty` turns above)

Only the units the scan marked `scored` are counted (a transcript inside the
window's start slack that belongs to another file is skipped). Reads only.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime

CAP = 8192


def count(path: str) -> dict:
    """The counts for one transcript (see the module docstring)."""
    turns = length = empty = capped_text = length_call = 0
    forced = {"forced": 0, "forced_thought": 0, "forced_length": 0, "forced_call": 0, "forced_empty": 0}
    forced_chars: list[int] = []
    rates: list[float] = []
    started: dict[int, datetime] = {}
    cur, finish, calls = None, None, 0

    def close():
        nonlocal length_call
        if cur is not None and finish == "length" and calls:
            length_call += 1

    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get("type")
            if t == "iteration":
                close()
                cur, finish, calls = e.get("number"), None, 0
                turns += 1
                if e.get("timestamp"):
                    started[cur] = datetime.fromisoformat(e["timestamp"])
            elif t == "response_model":
                finish = e.get("finish_reason")
                length += finish == "length"
            elif t == "tool_call":
                calls += 1
            elif t == "force_act_result":
                rc, calls_n = e.get("reasoning_chars") or 0, e.get("tool_calls") or 0
                forced["forced"] += 1
                forced["forced_thought"] += rc > 0
                forced["forced_length"] += e.get("finish_reason") == "length"
                forced["forced_call"] += calls_n > 0
                forced["forced_empty"] += not calls_n and not e.get("content_chars")
                forced_chars.append(rc)
            elif t == "output_cap":
                if "reasoning_chars" in e:
                    empty += 1
                else:
                    capped_text += 1
                t0 = started.get(e.get("iteration"))
                if e.get("completion_tokens") == CAP and t0 and e.get("timestamp"):
                    dt = (datetime.fromisoformat(e["timestamp"]) - t0).total_seconds()
                    if dt > 0:
                        rates.append(CAP / dt)
    close()
    return {"turns": turns, "length": length, "empty": empty, "capped_text": capped_text,
            "length_call": length_call, "rate_8192": rates, **forced, "forced_chars": forced_chars}


def from_scan(scan: list, label: str | None, logs_dir: str | None) -> dict:
    """{unit: counts + passed + transcript} for the scored units of one scanned arm."""
    arms = [a for a in scan if label is None or a.get("label") == label]
    if len(arms) != 1:
        raise SystemExit(f"pick one arm with --label (have: {', '.join(a.get('label', '?') for a in scan)})")
    out = {}
    for unit, entries in sorted(arms[0]["units"].items()):
        for ent in entries:
            if not ent.get("scored"):
                continue
            path = ent["transcript"]
            if logs_dir:
                path = os.path.join(logs_dir, os.path.basename(path))
            out[unit] = {**count(path), "passed": bool(ent.get("passed")),
                         "transcript": os.path.basename(path)}
    return out


def summary(units: dict) -> dict:
    rates = sorted(r for u in units.values() for r in u["rate_8192"])
    fails = {u: v for u, v in units.items() if not v["passed"]}

    def pct(p):
        return round(rates[int(p * (len(rates) - 1))]) if rates else None
    return {
        "units": len(units), "turns": sum(v["turns"] for v in units.values()),
        "length": sum(v["length"] for v in units.values()),
        "empty": sum(v["empty"] for v in units.values()),
        "empty_units": sorted(u for u, v in units.items() if v["empty"]),
        "empty_in_fails": sum(v["empty"] for v in fails.values()),
        "failed_units_with_empty": sorted(u for u, v in fails.items() if v["empty"]),
        "failed_units_without_empty": sorted(u for u, v in fails.items() if not v["empty"]),
        "capped_text": sum(v["capped_text"] for v in units.values()),
        "length_call": sum(v["length_call"] for v in units.values()),
        "rate_8192": {"n": len(rates), "p10": pct(0.1),
                      "median": round(statistics.median(rates)) if rates else None, "p90": pct(0.9)},
        **{k: sum(v.get(k, 0) for v in units.values())
           for k in ("forced", "forced_thought", "forced_length", "forced_call", "forced_empty")},
        "forced_units": sorted(u for u, v in units.items() if v.get("forced")),
        "forced_reasoning_chars_median": (round(statistics.median(chars)) if (chars := [
            c for v in units.values() for c in v.get("forced_chars", [])]) else None),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("scan", help="an integrity_scan.py --json report")
    ap.add_argument("--label", help="the arm's label in that report (needed when it has several)")
    ap.add_argument("--logs-dir", help="read the transcripts from here (a copy) instead of the scan's paths")
    ap.add_argument("--json", help="write {summary, units} here")
    a = ap.parse_args(argv)
    with open(a.scan, encoding="utf-8") as f:
        units = from_scan(json.load(f), a.label, a.logs_dir)
    s = summary(units)
    print(f"{s['units']} units, {s['turns']} turns, {s['length']} ended at the cap: "
          f"{s['empty']} empty (no answer, no call) in {len(s['empty_units'])} units, "
          f"{s['capped_text']} with text only, {s['length_call']} still carrying a tool call")
    print(f"empty capped turns in failed units: {s['empty_in_fails']}, in "
          f"{len(s['failed_units_with_empty'])} of {len(s['failed_units_with_empty']) + len(s['failed_units_without_empty'])} fails")
    r = s["rate_8192"]
    if r["n"]:
        print(f"replies at exactly {CAP:,} tokens: {r['n']}, end-to-end tok/s p10 {r['p10']}, "
              f"median {r['median']}, p90 {r['p90']} (prefill included)")
    if s["forced"]:
        print(f"forced-act turns: {s['forced']} in {len(s['forced_units'])} units; {s['forced_thought']} still "
              f"thought (median {s['forced_reasoning_chars_median']:,} reasoning chars), {s['forced_length']} ran "
              f"to the cap, {s['forced_empty']} empty, {s['forced_call']} made a tool call")
    print("unit | verdict | turns | at the cap | empty | text only | with a call")
    for u, v in units.items():
        if v["length"]:
            print(f"{u} | {'PASS' if v['passed'] else 'FAIL'} | {v['turns']} | {v['length']} | "
                  f"{v['empty']} | {v['capped_text']} | {v['length_call']}")
    if s["forced"]:
        print("unit | verdict | forced | still thought | at the cap | empty | with a call")
        for u, v in units.items():
            if v["forced"]:
                print(f"{u} | {'PASS' if v['passed'] else 'FAIL'} | {v['forced']} | {v['forced_thought']} | "
                      f"{v['forced_length']} | {v['forced_empty']} | {v['forced_call']}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"summary": s, "units": {u: {k: x for k, x in v.items() if k not in ("rate_8192", "forced_chars")}
                                               for u, v in units.items()}}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
