#!/usr/bin/env python3
"""STEP 3 verification: does the local orchestrator model actually reach for
the meta-tools (start_agent, skills) when it should?

Faithful to production: uses the real system prompt (system-prompt.md +
system-prompt-tools.md, incl. the injected skill index) and a realistic
OpenAI-format tool menu (base file/shell tools + start_agent/check_agent +
list_skills/load_skill), then checks the model's tool_calls on prompts that
SHOULD trigger a background agent, SHOULD trigger a skill, or should do NEITHER
(controls). Talks straight to llama-server /v1 — the same decision the model
makes behind MCPO/WebUI.

Run with the target model already serving on :8080.
"""
import json, sys, os, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))
from tools import TOOL_SCHEMAS  # the 8 base tools + task_done

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT = "http://127.0.0.1:8080/v1/chat/completions"


def _read(p):
    try:
        return open(os.path.join(REPO, p)).read()
    except Exception:
        return ""


SYSTEM = (_read("system-prompt.md") + "\n\n" + _read("system-prompt-tools.md")).strip()

# The MCP-only tools the model sees in production (not in runner's TOOL_SCHEMAS).
MCP_TOOLS = [
    {"type": "function", "function": {"name": "start_agent",
        "description": "Delegate a task to a background agent that runs it autonomously and in parallel. MANDATORY USAGE: if the user asks to 'spawn', 'launch', 'kick off', or 'start' an agent, to run something 'in the background', to work 'while we keep talking', or to handle a large multi-step subtask (add tests across a module, refactor a whole layer, port a module, audit a repo, run a migration), you MUST call this tool and MUST NOT do the work yourself with read_file/grep/bash/edit_file. For those requests, calling any other tool is wrong — hand the ENTIRE task to this agent via the `task` argument and return its ID.",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": "What the agent should accomplish."},
            "workdir": {"type": "string", "description": "Working directory."},
            "context": {"type": "string", "description": "Background briefing."}},
            "required": ["task"]}}},
    {"type": "function", "function": {"name": "check_agent",
        "description": "Check a background agent's progress by its ID.",
        "parameters": {"type": "object", "properties": {"agent_id": {"type": "string"}}, "required": ["agent_id"]}}},
    {"type": "function", "function": {"name": "list_skills",
        "description": "List curated expertise skills (name + description).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "load_skill",
        "description": "Load the full instructions for one skill by name, then follow them.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
]
# Base tools minus task_done (which is runner-internal, not a WebUI tool).
BASE = [t for t in TOOL_SCHEMAS if t["function"]["name"] != "task_done"]
TOOLS = BASE + MCP_TOOLS

# (prompt, expected-tool-family). "spawn" -> start_agent; "skill" -> a skill
# tool; "none" -> should NOT spawn/skill (control).
CASES = [
    ("Spawn a background agent to add comprehensive unit tests to /home/max/proj/auth.py while we keep discussing the architecture — don't block our chat.", "spawn"),
    ("Kick off an autonomous agent in the background to refactor the logging module across the whole codebase. I'll keep working with you meanwhile.", "spawn"),
    ("Start a long-running agent to migrate the database schema and run the test suite; report back when it's done rather than waiting here.", "spawn"),
    ("Launch an independent background agent that audits the entire repo for security issues. Keep chatting with me in the meantime.", "spawn"),
    ("In the background, have an agent port the whole utils/ module from Python to Go while we plan the next feature together.", "spawn"),
    ("Do a thorough security audit of the authentication code in this project.", "skill"),
    ("Help me author a brand-new task for the eval suite, following our conventions properly.", "skill"),
    ("What is 17 times 23?", "none"),
    ("Read /etc/hostname and tell me the machine name.", "none"),
]

SKILL_TOOLS = {"list_skills", "load_skill", "start_skill_agent"}


def ask(prompt):
    body = json.dumps({
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
        "tools": TOOLS, "tool_choice": "auto",
        "temperature": 0, "max_tokens": 700,
    }).encode()
    req = urllib.request.Request(CHAT, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    msg = d["choices"][0]["message"]
    calls = [c["function"]["name"] for c in (msg.get("tool_calls") or [])]
    return calls, (msg.get("content") or "")[:90]


def main():
    print(f"System prompt: {len(SYSTEM)} chars | tools offered: {len(TOOLS)} "
          f"(incl. start_agent, skills)\n")
    results = {"spawn": [0, 0], "skill": [0, 0], "none": [0, 0]}
    for prompt, kind in CASES:
        calls, preview = ask(prompt)
        if kind == "spawn":
            hit = "start_agent" in calls
        elif kind == "skill":
            hit = bool(set(calls) & SKILL_TOOLS)
        else:  # none: success = did NOT spawn or skill
            hit = not (("start_agent" in calls) or (set(calls) & SKILL_TOOLS))
        results[kind][0] += int(hit); results[kind][1] += 1
        mark = "✓" if hit else "✗"
        print(f"  [{mark}] {kind:5} | calls={calls or '(none)'} | {prompt[:58]}")
    print()
    for k in ("spawn", "skill", "none"):
        p, n = results[k]
        print(f"  {k:6}: {p}/{n}")
    sp = results["spawn"]
    print(f"\nSTART_AGENT hit-rate: {sp[0]}/{sp[1]} "
          f"({'WORKS' if sp[0] else 'DOES NOT FIRE'})")


if __name__ == "__main__":
    main()
