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

SYSTEM_PROMPT = """You are a capable autonomous agent running on a local machine.
You have tools to run shell commands, read/write files, search code, and list directories.

Your job is to complete the task given to you. Work step by step:
1. Understand the task — read relevant files, explore the codebase if needed.
2. Plan your approach.
3. Execute — make changes, run tests, iterate until it works.
4. Verify — confirm the result is correct.
5. Call task_done with a summary when finished.

Guidelines:
- Be thorough but efficient. Don't repeat failed approaches without changing something.
- If a command fails, read the error and adapt.
- If you're unsure about the codebase structure, explore it with list_files and grep.
- Test your changes when possible (run tests, build, etc).
- When the task is complete, call the task_done tool. Do not just say you're done — call the tool.
"""

# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(
    task: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    max_iter: int = DEFAULT_MAX_ITER,
    workdir: str | None = None,
    log_dir: str = DEFAULT_LOG_DIR,
    system_prompt: str = SYSTEM_PROMPT,
) -> str:
    """Run the agent loop. Returns the final summary or last model message."""

    if workdir:
        workdir = os.path.expanduser(workdir)
        os.environ["AGENT_WORKDIR"] = workdir

    client = OpenAI(base_url=base_url, api_key="not-needed")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    # Set up logging
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
                elif fn_name == "grep":
                    print(f"  > grep: {fn_args.get('pattern', '')} in {fn_args.get('path', '.')}")
                elif fn_name == "list_files":
                    print(f"  > ls: {fn_args.get('directory', '.')} {fn_args.get('pattern', '')}")
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
    parser.add_argument("--system-prompt", help="Override the system prompt")
    parser.add_argument("--system-prompt-file", help="Read system prompt from a file")

    args = parser.parse_args()

    # Resolve task
    if args.task_file:
        task = Path(args.task_file).read_text()
    elif args.task:
        task = " ".join(args.task)
    else:
        parser.error("Provide a task as arguments or via --task-file")

    # Resolve system prompt
    system_prompt = SYSTEM_PROMPT
    if args.system_prompt:
        system_prompt = args.system_prompt
    elif args.system_prompt_file:
        system_prompt = Path(args.system_prompt_file).read_text()

    run_agent(
        task=task,
        base_url=args.base_url,
        model=args.model,
        max_iter=args.max_iter,
        workdir=args.workdir,
        log_dir=args.log_dir,
        system_prompt=system_prompt,
    )


if __name__ == "__main__":
    main()
