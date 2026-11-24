#!/usr/bin/env python3
"""How the #588 probe requests in this directory were made (OpenBeast, 2026-11-24). Reads only.

probe-143-turn2.json, probe-163go-turn2.json: the harness9 runner (git archive f38a29c7, agents/runner.py) was run on
the Mac with T2's env (OPENBEAST_EVAL=1, OPENBEAST_AGENT_THINKING=on, OPENBEAST_AGENT_MAX_TOKENS=8192, ACT_NUDGE,
VERIFY_DONE, DIAG_EDIT_HINT, ENV_BLOCK, WALL_WARN = 1) against a local mock server that answered turn 1 with the
transcript's own first tool call and recorded the turn-2 request. Then: the env block and the tool result were put back
from the I_T2 transcript (agent-20261124-050412-48a9b514, 143_uov; agent-20261124-060650-d1f5f63d, 163_bigint_divmod_b
Go), the system prompt's host line was set to the Linux one ("Tool host: Linux. ...") and its turn budget to 60, and
the model to inkling-2-mini. Not included: the host pack (run_eval's context file).

The four deep probes are built by this script from those turn-2 requests' system prompt and tools plus the transcript
history up to the turn that went EMPTY at the 8,192 cap on the unpatched server, choosing turns with no earlier capped
turn, act nudge or other runner message (so the history is the assistant calls and tool results only):

  probe-163rs-it39.json   I_T2    agent-20261124-063655-dd8f7e2d.jsonl turn 39 (163_bigint_divmod_e, Rust)
  probe-163py-it15.json   I_T2_s1 agent-20261124-103615-7cb406ff.jsonl turn 15 (163_bigint_divmod_a)
  probe-163cpp-it21.json  I_T2_s1 agent-20261124-105739-89147a3a.jsonl turn 21 (163_bigint_divmod_d, C++)
  probe-168cpp-it15.json  I_T2_s1 agent-20261124-120848-9f4c0d37.jsonl turn 15 (168_linearizability_kv_d, C++)

Approximations: tool results are as logged; the runner does not send reasoning back, so none is included.

  python3 -I scripts/engines/probes/rebuild_deep_probes.py BASE_TURN2.json TRANSCRIPT.jsonl TURN OUT.json
"""
import json, sys


def build(base_path: str, transcript: str, turn: int, out: str) -> None:
    base = json.load(open(base_path))
    ev = [json.loads(l) for l in open(transcript)]
    task = ev[0]["task"]
    envb = [e for e in ev if e["type"] == "env_block"][0]["text"]
    msgs = [base["messages"][0], {"role": "user", "content": task + "\n\n" + envb}]
    cur, n = None, 0
    for e in ev:
        t = e["type"]
        if t == "iteration":
            if cur:
                msgs.append(cur["a"]); msgs.extend(cur["t"])
            cur = None
            if e["number"] >= turn:
                break
            cur = {"a": {"role": "assistant", "content": ""}, "t": []}
        elif t == "assistant" and cur:
            cur["a"]["content"] = e["content"]
        elif t == "tool_call" and cur:
            n += 1
            cid = f"call_{n}"
            cur["a"].setdefault("tool_calls", []).append(
                {"id": cid, "type": "function", "function": {"name": e["name"], "arguments": json.dumps(e["args"])}})
            cur["t"].append({"role": "tool", "tool_call_id": cid, "content": str(e["result"])})
    req = dict(base)
    req["messages"] = msgs
    json.dump(req, open(out, "w"))


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4])
