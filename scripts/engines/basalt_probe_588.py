#!/usr/bin/env python3
"""Upstream #588 probe for Basalt on Inkling 2 Mini (OpenBeast, 2026-11-24).

  python3 -I scripts/engines/basalt_probe_588.py --url http://127.0.0.1:8099/v1 --key-file KEY --label pr632 --n 6 --out DIR [--issue] [--max-tokens N] REQ.json [REQ.json ...]

Each REQ.json (scripts/engines/probes/probe-*-turn2.json) is a full chat request rebuilt from an I_T2 transcript turn that ended EMPTY at the 8,192 cap on the
unpatched server: turn 2 of 143_uov (agent-20261124-050412-48a9b514.jsonl, one empty capped turn) and of
163_bigint_divmod_b, Go (agent-20261124-060650-d1f5f63d.jsonl, turns 2 and 3 empty). Rebuilt by capturing the harness9 runner's own request against a mock server, then putting the rig's
env block and tool result from the transcript back in; the host pack context file is not included). It is sent N times
as the runner sends it (non-streaming, temperature 0.6, max_tokens 8192, enable_thinking true; the server's answer room
is what limits the thinking, 6,144 at --answer-room auto). One more probe, `issue588`, is the issue's own shape: a
reasoning_budget_tokens of 400 at max_tokens 3000 (--issue; meaningful at --answer-room auto only: a fixed
answer room of 3,000 or more leaves it one thinking token). --max-tokens overrides the rebuilt requests' 8,192
(I_T3 serves --answer-room 8192, so its gate sends 16,384, the arm's cap).

Per reply it records finish_reason, whether content and tool calls came back, completion tokens and time. An EMPTY reply
is finish 'length' with no content and no tool call: the #588 signature. Writes DIR/probe-<label>.json and prints a
summary. Reads only; the server log's 'wrapping up' / 'no answer' lines are the cross-check (the caller greps them).
"""
import argparse, json, os, sys, time, urllib.request

ISSUE = {"messages": [{"role": "user", "content":
         "Prove that there are infinitely many primes of the form 4k+3, then write a Python function that lists the first "
         "20 of them. Think it through carefully before answering."}],
         "max_tokens": 3000, "reasoning_budget_tokens": 400, "temperature": 0.6,
         "chat_template_kwargs": {"enable_thinking": True}}


def post(url, key, body, timeout=600):
    req = urllib.request.Request(url + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return d, time.time() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True); ap.add_argument("--key-file", required=True)
    ap.add_argument("--label", required=True); ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--out", required=True); ap.add_argument("--issue", action="store_true")
    ap.add_argument("--max-tokens", type=int); ap.add_argument("reqs", nargs="+")
    a = ap.parse_args()
    key = open(a.key_file).read().strip()
    probes = [(os.path.basename(p), json.load(open(p))) for p in a.reqs]
    if a.max_tokens:
        probes = [(n, dict(b, max_tokens=a.max_tokens)) for n, b in probes]
    if a.issue:
        probes.append(("issue588", dict(ISSUE, model="inkling-2-mini")))
    rows = []
    for name, body in probes:
        for i in range(a.n):
            d, dt = post(a.url, key, body)
            ch = d["choices"][0]; m = ch["message"]
            row = {"probe": name, "i": i, "finish": ch.get("finish_reason"),
                   "content_chars": len((m.get("content") or "").strip()),
                   "tool_calls": [c["function"]["name"] for c in (m.get("tool_calls") or [])],
                   "reasoning_chars": len(m.get("reasoning_content") or ""),
                   "completion_tokens": d.get("usage", {}).get("completion_tokens"), "seconds": round(dt, 1)}
            row["empty"] = row["finish"] == "length" and not row["content_chars"] and not row["tool_calls"]
            rows.append(row); print(json.dumps(row), flush=True)
    os.makedirs(a.out, exist_ok=True)
    json.dump({"label": a.label, "url": a.url, "n": a.n, "max_tokens": a.max_tokens, "issue": a.issue, "rows": rows}, open(f"{a.out}/probe-{a.label}.json", "w"), indent=1)
    for name, _ in probes:
        r = [x for x in rows if x["probe"] == name]
        print(f"SUMMARY {a.label} {name}: n={len(r)} empty={sum(x['empty'] for x in r)} "
              f"tool_call={sum(bool(x['tool_calls']) for x in r)} text_only={sum(bool(x['content_chars']) and not x['tool_calls'] for x in r)} "
              f"length={sum(x['finish'] == 'length' for x in r)}")
    print(f"SUMMARY {a.label} all: empty={sum(x['empty'] for x in rows)} of {len(rows)}")


if __name__ == "__main__":
    sys.exit(main())
