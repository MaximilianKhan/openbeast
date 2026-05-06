#!/usr/bin/env python3
"""
Local agent runner — loops an LLM with tool use against a task until completion.

Connects to a local llama.cpp server (OpenAI-compatible API) and iterates:
  1. Send task + conversation history to the model
  2. If the model calls tools, execute them and feed results back
  3. Repeat until the model calls task_done or max iterations are reached

Usage:
  python runner.py "refactor the logging module to use structured output"
  python runner.py --task-file task.md
  python runner.py --max-iter 50 --workdir ~/projects/myapp "add unit tests for auth"

The server must be running (e.g. ./serve-qwen-27b-q5.sh) before launching.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from tools import TOOL_SCHEMAS, TOOL_HANDLERS

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:8080/v1"
DEFAULT_MODEL = "qwen-27b-q5"  # llama.cpp ignores this, but it's required by the API
DEFAULT_MAX_ITER = 200
DEFAULT_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")

# Load soul file (system-prompt.md) from repo root, fall back to inline default
_SOUL_FILE = os.path.join(os.path.dirname(__file__), "..", "system-prompt.md")
_SOUL_PROMPT = ""
if os.path.exists(_SOUL_FILE):
    with open(_SOUL_FILE) as f:
        _SOUL_PROMPT = f.read().strip() + "\n\n"

_AGENT_INSTRUCTIONS = """You are a capable autonomous agent running on a local machine
with a FULL production-grade toolset. Use it.

Your toolset:
  bash         — run shell commands (compile, run scripts, test, install)
  read_file    — read file contents with offset/limit
  write_file   — create new files (overwrites if exists)
  edit_file    — surgical string-replace in existing files (PREFERRED for edits)
  list_files   — list files matching a glob (use this to explore)
  grep         — regex search across files (use this to navigate code)
  fetch        — pull text from a URL (docs, API references, gists)
  web_search   — search the web via local SearXNG (when stuck or need references)
  start_agent  — spawn a sub-agent for a self-contained subtask
  check_agent  — poll the sub-agent for status
  tail_agent   — read raw logs of a running sub-agent
  list_agents  — see what sub-agents you've spawned
  stop_agent   — kill a sub-agent

USE THE TOOLS. A working professional engineer:
  - Runs the code they wrote. Hand-tracing math is not a substitute for running the test
    that comes with the task. Use bash to execute the validation when you can.
  - Looks things up. If a formula or API signature is fuzzy, use web_search/fetch to find
    a reference — guessing wastes iterations.
  - Tests intermediate state. Don't deliver a 100-line solution untested; print interim
    values, run unit checks, verify each piece before composing them.
  - Decomposes hard problems. If a task has independent subproblems, spawn sub-agents
    via start_agent to work on them in parallel — then check_agent to gather results.
    This is the same pattern as a senior engineer delegating to teammates.

Workflow:
1. Understand the task — read relevant files, explore the codebase with list_files / grep.
2. Plan your approach. For hard tasks (parsers, algorithms with subtle invariants,
   numerical code), write a brief plan to yourself before coding.
3. For decomposable hard tasks: consider start_agent for one or more isolated subproblems.
4. Execute. Use edit_file for changes to existing files; write_file only for brand-new files.
5. Verify by running. Use bash to invoke python/the test/the validation when one exists.
   If stuck, use web_search or fetch for references — don't keep guessing.
6. Call task_done with a summary when finished.

Guidelines:
- Be thorough but efficient. Don't repeat failed approaches without changing something —
  if your second attempt fails the same way, your mental model is wrong; investigate.
- If a command fails, read the error in full and adapt.
- Prefer edit_file over write_file when modifying existing code — safer and more precise.
- Prefer running the actual code over reasoning about what it should do. Local execution
  is cheap; iterations on broken reasoning are not.
- When the task is complete, call the task_done tool. Do not just say you're done — call
  the tool.
"""


def build_system_prompt(context: str = "", context_budget: int = 0) -> str:
    """Assemble the full system prompt with optional context and budget info."""
    parts = []
    if _SOUL_PROMPT:
        parts.append(_SOUL_PROMPT)
    parts.append(_AGENT_INSTRUCTIONS)
    if context_budget > 0:
        parts.append(
            f"Context budget: you have approximately {context_budget:,} tokens of context. "
            f"Be mindful of this limit — avoid reading very large files in full when "
            f"offset/limit or grep can target what you need.\n"
        )
    if context:
        parts.append(
            f"Background context from the caller:\n"
            f"---\n{context}\n---\n"
        )
    return "\n".join(parts)


SYSTEM_PROMPT = build_system_prompt()

# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def _rebuild_messages_from_log(log_path: str, system_prompt: str) -> list[dict]:
    """Reconstruct the conversation from a JSONL log for resumption."""
    messages = [{"role": "system", "content": system_prompt}]
    with open(log_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            if etype == "start":
                messages.append({"role": "user", "content": event.get("task", "")})
            elif etype == "assistant":
                # Only add standalone assistant messages (no tool calls)
                # Tool-calling assistant messages are logged as part of tool_call events
                content = event.get("content", "")
                if content and not any(
                    m.get("role") == "assistant" and m.get("content") == content
                    for m in messages[-3:]
                ):
                    messages.append({"role": "assistant", "content": content})
            elif etype == "tool_call":
                # We can't fully reconstruct tool_call_id-linked messages without
                # more log detail, but we can add the tool result as context
                name = event.get("name", "")
                result = event.get("result", "")
                if name and result:
                    messages.append({
                        "role": "user",
                        "content": f"[Previous tool call: {name}] Result:\n{result[:2000]}",
                    })

    return messages


def run_agent(
    task: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    max_iter: int = DEFAULT_MAX_ITER,
    workdir: str | None = None,
    log_dir: str = DEFAULT_LOG_DIR,
    log_file: str | None = None,
    system_prompt: str | None = None,
    context: str = "",
    context_budget: int = 0,
    resume_from: str | None = None,
) -> str:
    """Run the agent loop. Returns the final summary or last model message."""

    if workdir:
        workdir = os.path.expanduser(workdir)
        os.environ["AGENT_WORKDIR"] = workdir

    # Build system prompt: explicit override > dynamic build > default
    if system_prompt is None:
        system_prompt = build_system_prompt(context=context, context_budget=context_budget)

    client = OpenAI(base_url=base_url, api_key="not-needed")

    # Resume from existing log or start fresh
    if resume_from and os.path.isfile(resume_from):
        messages = _rebuild_messages_from_log(resume_from, system_prompt)
        messages.append({
            "role": "user",
            "content": "You are resuming a previous run that was interrupted. "
                       "Review the context above and continue working on the task. "
                       "If the task is already complete, call task_done.",
        })
        print(f"Resuming from {resume_from} ({len(messages)} messages reconstructed)")
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]

    # Set up logging
    if log_file:
        log_path = log_file
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    else:
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = os.path.join(log_dir, f"agent-{timestamp}.jsonl")

    def log_event(event: dict):
        event["timestamp"] = datetime.now().isoformat()
        with open(log_path, "a") as f:
            f.write(json.dumps(event) + "\n")

    print(f"Agent started — task: {task[:100]}{'...' if len(task) > 100 else ''}")
    print(f"Server: {base_url}")
    print(f"Log: {log_path}")
    if workdir:
        print(f"Workdir: {workdir}")
    print(f"Max iterations: {max_iter}")
    print("-" * 60)

    log_event({"type": "start", "task": task, "model": model, "workdir": workdir})

    final_summary = ""

    for iteration in range(1, max_iter + 1):
        print(f"\n[iter {iteration}/{max_iter}]")
        log_event({"type": "iteration", "number": iteration})

        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                temperature=0.6,
            )
        except Exception as e:
            print(f"  API error: {e}")
            log_event({"type": "error", "error": str(e)})
            time.sleep(5)
            continue

        choice = response.choices[0]
        message = choice.message

        # Append assistant message to history
        msg_dict = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        messages.append(msg_dict)

        # If the model has text output, print it
        if message.content:
            print(f"  Model: {message.content[:200]}{'...' if len(message.content or '') > 200 else ''}")
            log_event({"type": "assistant", "content": message.content})

        # If no tool calls, the model is just talking — check if it's done
        if not message.tool_calls:
            if choice.finish_reason == "stop":
                print("  (model stopped without calling task_done)")
                # Nudge it to either continue or call task_done
                messages.append({
                    "role": "user",
                    "content": "If the task is complete, call the task_done tool with a summary. If not, continue working.",
                })
            continue

        # Execute tool calls
        for tc in message.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            handler = TOOL_HANDLERS.get(fn_name)
            if not handler:
                result = f"Error: unknown tool '{fn_name}'"
            else:
                if fn_name == "bash":
                    print(f"  > bash: {fn_args.get('command', '')[:100]}")
                elif fn_name == "read_file":
                    print(f"  > read: {fn_args.get('path', '')}")
                elif fn_name == "write_file":
                    print(f"  > write: {fn_args.get('path', '')}")
                elif fn_name == "edit_file":
                    print(f"  > edit: {fn_args.get('path', '')}")
                elif fn_name == "grep":
                    print(f"  > grep: {fn_args.get('pattern', '')} in {fn_args.get('path', '.')}")
                elif fn_name == "list_files":
                    print(f"  > ls: {fn_args.get('directory', '.')} {fn_args.get('pattern', '')}")
                elif fn_name == "fetch":
                    print(f"  > fetch: {fn_args.get('url', '')[:80]}")
                elif fn_name == "task_done":
                    print(f"  > task_done")

                result = handler(fn_args)

            log_event({
                "type": "tool_call",
                "name": fn_name,
                "args": fn_args,
                "result": result[:2000],
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

            # Check for task completion
            if fn_name == "task_done":
                final_summary = fn_args.get("summary", result)
                print(f"\n{'=' * 60}")
                print(f"Task complete (iteration {iteration})")
                print(f"Summary: {final_summary}")
                print(f"Log: {log_path}")
                print(f"{'=' * 60}")
                log_event({"type": "done", "summary": final_summary, "iterations": iteration})
                return final_summary

    # Max iterations reached
    print(f"\nMax iterations ({max_iter}) reached without task_done.")
    log_event({"type": "max_iterations", "iterations": max_iter})
    return final_summary or "(max iterations reached)"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Run a local AI agent against a task",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("task", nargs="*", help="Task description (or use --task-file)")
    parser.add_argument("--task-file", "-f", help="Read task from a file")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"API base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model name (default: {DEFAULT_MODEL})")
    parser.add_argument("--max-iter", type=int, default=DEFAULT_MAX_ITER, help=f"Max iterations (default: {DEFAULT_MAX_ITER})")
    parser.add_argument("--workdir", "-w", help="Working directory for file/shell operations")
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR, help=f"Log directory (default: {DEFAULT_LOG_DIR})")
    parser.add_argument("--log-file", help="Specific log file path (overrides auto-generated name)")
    parser.add_argument("--context", help="Background context to include in the system prompt")
    parser.add_argument("--context-file", help="Read background context from a file")
    parser.add_argument("--context-budget", type=int, default=0, help="Approximate context token budget (informs the agent to be mindful of context)")
    parser.add_argument("--resume", help="Resume from a previous agent log file (JSONL path)")
    parser.add_argument("--system-prompt", help="Override the system prompt (disables context/budget injection)")
    parser.add_argument("--system-prompt-file", help="Read system prompt from a file (disables context/budget injection)")

    args = parser.parse_args()

    # Resolve task
    if args.task_file:
        task = Path(args.task_file).read_text()
    elif args.task:
        task = " ".join(args.task)
    else:
        parser.error("Provide a task as arguments or via --task-file")

    # Resolve system prompt
    system_prompt = None  # None = use dynamic build_system_prompt()
    if args.system_prompt:
        system_prompt = args.system_prompt
    elif args.system_prompt_file:
        system_prompt = Path(args.system_prompt_file).read_text()

    # Resolve context
    context = args.context or ""
    if args.context_file:
        context = Path(args.context_file).read_text()

    run_agent(
        task=task,
        base_url=args.base_url,
        model=args.model,
        max_iter=args.max_iter,
        workdir=args.workdir,
        log_dir=args.log_dir,
        log_file=args.log_file,
        system_prompt=system_prompt,
        context=context,
        context_budget=args.context_budget,
        resume_from=args.resume,
    )


if __name__ == "__main__":
    main()
