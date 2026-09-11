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
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from tools import TOOL_SCHEMAS, TOOL_HANDLERS, plan_block, reset_plan, update_plan

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
  bash         — run shell commands (compile, run scripts, test, install); stdout+stderr
                 merged, fresh shell per call (no persistent cd/exports), background
                 processes are killed when the call returns
  read_file    — read file contents with offset/limit (offset is 1-based, like grep -n)
  write_file   — create new files (overwrites if exists)
  edit_file    — surgical string-replace in existing files (PREFERRED for edits); on a
                 miss it quotes the nearest file text, on success it shows the edited region
  list_files   — list files matching a glob (use this to explore)
  grep         — regex search across files (use this to navigate code)
  fetch        — pull text from a PUBLIC URL (docs, API references, gists); localhost/
                 LAN/tailnet addresses are blocked; use bash + curl for local servers
  web_search   — search the web via local SearXNG (when stuck or need references)
  update_plan  — keep a step ladder for multi-step tasks; it is re-shown to you every
                 turn, so it survives when older tool output is dropped

USE THE TOOLS. A working professional engineer:
  - Runs the code they wrote. Hand-tracing math is not a substitute for running the test
    that comes with the task. Use bash to execute the validation when you can.
  - Looks things up. If a formula or API signature is fuzzy, use web_search/fetch to find
    a reference — guessing wastes iterations.
  - Tests intermediate state. Don't deliver a 100-line solution untested; print interim
    values, run unit checks, verify each piece before composing them.

Workflow:
1. Understand the task — read relevant files, explore the codebase with list_files / grep.
2. Plan your approach. For any task with 3+ steps call update_plan with the steps
   first, then keep it current as you go (exactly one step in_progress at a time).
   For hard tasks (parsers, algorithms with subtle invariants, numerical code),
   think through the invariants before coding.
3. Execute. Use edit_file for changes to existing files; write_file only for brand-new files.
4. Verify by running. Use bash to invoke python/the test/the validation when one exists.
   If stuck, use web_search or fetch for references — don't keep guessing.
5. Call task_done with a summary when finished.

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


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def _rebuild_messages_from_log(log_path: str, system_prompt: str) -> list[dict]:
    """Reconstruct the conversation from a JSONL log for resumption."""
    messages = [{"role": "system", "content": system_prompt}]
    # Track recent assistant contents in a set for O(1) dedup instead of O(n²) scan.
    seen_assistant: set[str] = set()
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
                content = event.get("content", "")
                if content and content not in seen_assistant:
                    seen_assistant.add(content)
                    messages.append({"role": "assistant", "content": content})
            elif etype == "tool_call":
                name = event.get("name", "")
                result = event.get("result", "")
                if name == "update_plan" and isinstance(event.get("args"), dict):
                    # Restore the step ladder (pure in-process state) so the
                    # re-injected plan block is right from the first turn.
                    try:
                        update_plan(**event["args"])
                    except Exception:
                        pass
                if name and result:
                    messages.append({
                        "role": "user",
                        "content": f"[Previous tool call: {name}] Result:\n{result[:2000]}",
                    })

    return messages


def _tool_summary(name: str, args: dict) -> str:
    """One-line summary for printing a tool call to the console."""
    summaries = {
        "bash": lambda a: a.get("command", "")[:100],
        "read_file": lambda a: a.get("path", ""),
        "write_file": lambda a: a.get("path", ""),
        "edit_file": lambda a: a.get("path", ""),
        "grep": lambda a: f"'{a.get('pattern', '')}' in {a.get('path', '.')}",
        "list_files": lambda a: f"{a.get('directory', '.')} {a.get('pattern', '')}",
        "fetch": lambda a: a.get("url", "")[:80],
        "update_plan": lambda a: (
            f"{len(a.get('steps') or [])} steps"
            + (f" — {a['explanation'][:60]}" if a.get("explanation") else "")),
        "task_done": lambda a: a.get("summary", "")[:100],
    }
    fmt = summaries.get(name)
    if fmt:
        return fmt(args)
    return str(args)[:100]


def _print_token_summary(tokens_prompt: int, tokens_completion: int, tokens_total: int,
                         compactions: int = 0) -> None:
    """Print the stable-key token line that the eval harness parses."""
    print(f"TOKENS: prompt={tokens_prompt} completion={tokens_completion} total={tokens_total}")
    print(f"COMPACTIONS: {compactions}")


# ---------------------------------------------------------------------------
# Context-window management (2026-09-11 harness agentics, SOTA review #8).
#
# Messages grew unboundedly, and on a context overflow the loop retried the
# IDENTICAL oversized payload every 5 s until max_iter — e.g. 160 × 5 s of
# guaranteed failures. Now: (a) the server's overflow error is recognised
# and the OLDEST tool results are replaced by one-line stubs before the
# retry; (b) with --context-budget set, the same eviction runs proactively
# past ~70% of the budget (4 chars/token estimate). The system prompt, the
# original task, assistant turns and the (transiently injected) plan block
# are never evicted. Every event is logged to stderr + the JSONL log and
# counted in the done/max_iterations telemetry (COMPACTIONS: n on stdout).
#
# Error shapes (llama-server, tools/server): HTTP 400 body
#   {"error": {"code": 400, "message": "request (N tokens) exceeds the
#    available context size (M tokens), try increasing it",
#    "type": "exceed_context_size_error", "n_prompt_tokens": N, "n_ctx": M}}
# or "input (N tokens) is larger than the max context size (M tokens)";
# older builds/other paths: "Context size has been exceeded.",
# "context shift is disabled". The openai client raises BadRequestError
# whose str() embeds that body.
# ---------------------------------------------------------------------------
_CHARS_PER_TOKEN = 4
_COMPACT_FRACTION = 0.70          # proactive target as a fraction of the budget
_STUB_MIN_CHARS = 200             # results shorter than this aren't worth stubbing
_STUB_PREFIX = "[tool result elided:"
_CTX_OVERFLOW_RE = re.compile(
    r"exceed_context_size|exceeds the available context size|"
    r"larger than the max context size|context size has been exceeded|"
    r"context shift is disabled", re.IGNORECASE)
_CTX_FIELDS_RE = re.compile(r"n_prompt_tokens['\"]?\s*[:=]\s*(\d+).*?n_ctx['\"]?\s*[:=]\s*(\d+)", re.S)
_CTX_MSG_RE = re.compile(r"\((\d+) tokens\).*?\((\d+) tokens\)", re.S)
_SCHEMA_CHARS = len(json.dumps(TOOL_SCHEMAS))


def _is_context_overflow(err: str) -> bool:
    return bool(_CTX_OVERFLOW_RE.search(err or ""))


def _overflow_tokens(err: str) -> tuple[int, int] | None:
    """(n_prompt_tokens, n_ctx) parsed from the error text, or None."""
    m = _CTX_FIELDS_RE.search(err or "") or _CTX_MSG_RE.search(err or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _message_chars(m: dict) -> int:
    n = len(m.get("content") or "")
    if m.get("tool_calls"):
        n += len(json.dumps(m["tool_calls"]))
    return n


def estimate_tokens(messages: list[dict], extra_chars: int = 0) -> int:
    """Rough prompt size: 4 chars/token over every message + the tool
    schemas (sent on every request) + a small per-message framing cost."""
    chars = sum(_message_chars(m) for m in messages) + _SCHEMA_CHARS + extra_chars
    return chars // _CHARS_PER_TOKEN + 4 * len(messages)


def _stub(content: str, call_no: int) -> str:
    return f"{_STUB_PREFIX} {len(content)} chars, call #{call_no}]"


def compact_messages(messages: list[dict], chars_to_free: int,
                     call_index: dict[int, int] | None = None) -> tuple[int, int]:
    """Replace the OLDEST tool results with one-line stubs until at least
    `chars_to_free` characters are freed (or nothing evictable remains).

    Never touches: messages[0] (system prompt), the first user message (the
    task), assistant turns, user nudges, or results already stubbed. Oldest
    first and stop as soon as enough is freed, so the newest results — the
    ones the model is about to act on — survive unless the older ones can't
    cover the ask (being stuck beats keeping them). Returns
    (results_evicted, chars_freed). `call_index` maps message position ->
    tool-call ordinal for the stub text.
    """
    call_index = call_index or {}
    candidates = [
        i for i, m in enumerate(messages)
        if i > 1 and m.get("role") == "tool"
        and not str(m.get("content") or "").startswith(_STUB_PREFIX)
        and len(m.get("content") or "") > _STUB_MIN_CHARS
    ]
    evicted = freed = 0
    for i in candidates:
        if freed >= max(chars_to_free, 1):
            break
        content = messages[i]["content"]
        stub = _stub(content, call_index.get(i, i))
        messages[i]["content"] = stub
        freed += len(content) - len(stub)
        evicted += 1
    return evicted, freed


def _with_plan(messages: list[dict], plan: str) -> list[dict]:
    """Request payload = history + the current plan block (transient: the
    block is never stored, so it can't bloat or be evicted). Folded into a
    trailing user message when one exists so roles keep alternating."""
    if not plan:
        return messages
    last = messages[-1] if messages else None
    if last and last.get("role") == "user" and last is not messages[1]:
        merged = dict(last)
        merged["content"] = f"{last.get('content') or ''}\n\n{plan}"
        return messages[:-1] + [merged]
    return messages + [{"role": "user", "content": plan}]


def _host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def _key_endpoint_trusted(base_url: str) -> bool:
    """True when the ambient key may be sent to `base_url`.

    start_agent(base_url=...) is a model-callable MCP parameter with no host
    allowlist, so an injected prompt can name an arbitrary endpoint. The
    ambient key therefore travels ONLY to the endpoint this install was
    configured for (OPENBEAST_AGENT_INFERENCE_URL) or to the local server;
    anywhere else gets the keyless sentinel. An explicit --api-key is operator
    intent and always honored.
    """
    host = _host_of(base_url)
    if not host or host in ("localhost", "127.0.0.1", "::1"):
        return True
    configured = os.environ.get("OPENBEAST_AGENT_INFERENCE_URL", "")
    return bool(configured) and host == _host_of(configured)


def resolve_api_key(explicit: str | None = None,
                    base_url: str = DEFAULT_BASE_URL) -> str:
    """Bearer key for the serving endpoint: flag > OPENBEAST_API_KEY > OPENAI_API_KEY.

    llama-server without --api-key ignores the Authorization header, so the
    "not-needed" fallback keeps keyless endpoints working unchanged. Prefer the
    env path in our own wiring — argv is visible in `ps`; the flag exists for
    ad-hoc use against foreign endpoints. Env-sourced keys are withheld from
    endpoints this install wasn't configured for (see _key_endpoint_trusted).
    """
    if explicit:
        return explicit
    if not _key_endpoint_trusted(base_url):
        return "not-needed"
    return (
        os.environ.get("OPENBEAST_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or "not-needed"
    )


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
    api_key: str | None = None,
) -> str:
    """Run the agent loop. Returns the final summary or last model message."""

    if workdir:
        workdir = os.path.expanduser(workdir)
        os.environ["AGENT_WORKDIR"] = workdir

    # Build system prompt: explicit override > dynamic build > default
    if system_prompt is None:
        system_prompt = build_system_prompt(context=context, context_budget=context_budget)

    client = OpenAI(base_url=base_url, api_key=resolve_api_key(api_key, base_url))

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
    # Token usage accumulated across every API call. Servers that don't return
    # a `usage` field (e.g. some older llama.cpp builds) leave these at 0.
    tokens_prompt = 0
    tokens_completion = 0
    tokens_total = 0
    # Context-window management state (see compact_messages).
    compactions = 0
    stop_reason = ""
    call_seq = 0
    call_index: dict[int, int] = {}   # message position -> tool-call ordinal
    if not (resume_from and os.path.isfile(resume_from)):
        reset_plan()

    def compact(reason: str, chars_to_free: int, detail: str = "") -> int:
        nonlocal compactions
        n, freed = compact_messages(messages, chars_to_free, call_index)
        if n:
            compactions += 1
            print(f"[compaction] {reason}: stubbed {n} oldest tool result(s), "
                  f"freed {freed:,} chars{detail}", file=sys.stderr)
            log_event({"type": "compaction", "reason": reason, "evicted": n,
                       "chars_freed": freed, "iteration": iteration})
        return n

    for iteration in range(1, max_iter + 1):
        print(f"\n[iter {iteration}/{max_iter}]")
        log_event({"type": "iteration", "number": iteration})

        plan = plan_block()
        if context_budget > 0:
            # Proactive: past ~70% of the declared budget, evict before the
            # server has to tell us (4 chars/token estimate).
            est = estimate_tokens(messages, len(plan))
            target = int(context_budget * _COMPACT_FRACTION)
            if est > target:
                compact("budget", (est - target) * _CHARS_PER_TOKEN,
                        f" (est {est:,} > {target:,} tokens)")

        try:
            response = client.chat.completions.create(
                model=model,
                messages=_with_plan(messages, plan),
                tools=TOOL_SCHEMAS,
                # OPENBEAST_EVAL_GREEDY=1 (low-churn eval mode, 2026-09-10):
                # unseeded temperature-0.6 sampling was the measured ±5-14
                # task-flip churn floor's primary engine. Greedy decoding is
                # an EXPERIMENT mode — the 0.6 default is serving reality
                # and stays for leaderboard rows.
                temperature=(0.0 if os.environ.get(
                    "OPENBEAST_EVAL_GREEDY", "") == "1" else 0.6),
            )
        except Exception as e:
            err = str(e)
            print(f"  API error: {err}")
            log_event({"type": "error", "error": err})
            if _is_context_overflow(err):
                nums = _overflow_tokens(err)
                if nums:
                    n_prompt, n_ctx = nums
                    need = (n_prompt - int(n_ctx * _COMPACT_FRACTION)) * _CHARS_PER_TOKEN
                    detail = f" (server: {n_prompt:,} > {n_ctx:,} tokens)"
                else:
                    # No numbers in the error: free a quarter of the history.
                    need = sum(_message_chars(m) for m in messages) // 4
                    detail = ""
                if compact("overflow", max(need, 1), detail):
                    continue  # retry immediately with the compacted history
                # Nothing left to evict: the identical payload can only fail
                # again, so stop instead of burning the remaining iterations.
                print("  context overflow with nothing left to compact — stopping",
                      file=sys.stderr)
                log_event({"type": "context_overflow_unrecoverable",
                           "iterations": iteration, "compactions": compactions})
                stop_reason = "context overflow (nothing left to compact)"
                break
            time.sleep(5)
            continue

        usage = getattr(response, "usage", None)
        if usage is not None:
            tokens_prompt += getattr(usage, "prompt_tokens", 0) or 0
            tokens_completion += getattr(usage, "completion_tokens", 0) or 0
            tokens_total += getattr(usage, "total_tokens", 0) or 0

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
            parse_err = None
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError as e:
                # 2026-09-10 hardening: silently substituting {} produced a
                # "missing argument" error that taught the model to fix the
                # WRONG thing — the #1 local-model tool-call failure mode.
                # Teach the actual failure: the JSON error + the raw text.
                fn_args = {}
                raw = (tc.function.arguments or "")[:300]
                parse_err = (f"Error: tool arguments were not valid JSON "
                             f"({e}). Raw arguments received: {raw!r}. "
                             f"Re-issue the call with valid JSON.")

            handler = TOOL_HANDLERS.get(fn_name)
            if parse_err:
                result = parse_err
            elif not handler:
                result = (f"Error: unknown tool '{fn_name}'. Available tools: "
                          f"{', '.join(sorted(TOOL_HANDLERS))}")
            else:
                print(f"  > {fn_name}: {_tool_summary(fn_name, fn_args)}")
                # Local models routinely emit imperfect tool calls (missing
                # required args, hallucinated kwargs, non-dict arguments). A
                # bad call must become a correctable error TURN, not kill the
                # whole run (stderr is DEVNULL under mcp_server — the run
                # would die silently with no done event).
                try:
                    if not isinstance(fn_args, dict):
                        raise TypeError(
                            f"arguments must be an object, got {type(fn_args).__name__}")
                    result = handler(**fn_args)
                except Exception as e:
                    result = f"Error: bad tool call to {fn_name}: {e}"

            log_event({
                "type": "tool_call",
                "name": fn_name,
                "args": fn_args,
                "result": result[:2000],
            })

            call_seq += 1
            call_index[len(messages)] = call_seq
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
                _print_token_summary(tokens_prompt, tokens_completion, tokens_total,
                                     compactions)
                print(f"{'=' * 60}")
                log_event({
                    "type": "done", "summary": final_summary, "iterations": iteration,
                    "tokens_prompt": tokens_prompt, "tokens_completion": tokens_completion,
                    "tokens_total": tokens_total, "compactions": compactions,
                })
                return final_summary

    # Max iterations reached (or an unrecoverable stop)
    if stop_reason:
        print(f"\nStopped without task_done: {stop_reason}.")
    else:
        print(f"\nMax iterations ({max_iter}) reached without task_done.")
    _print_token_summary(tokens_prompt, tokens_completion, tokens_total, compactions)
    log_event({
        "type": "max_iterations", "iterations": max_iter,
        "tokens_prompt": tokens_prompt, "tokens_completion": tokens_completion,
        "tokens_total": tokens_total, "compactions": compactions,
    })
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
    parser.add_argument("--api-key", help="Bearer key for the endpoint (default: OPENBEAST_API_KEY or OPENAI_API_KEY env; keyless endpoints need none)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model name (default: {DEFAULT_MODEL})")
    parser.add_argument("--max-iter", type=int, default=DEFAULT_MAX_ITER, help=f"Max iterations (default: {DEFAULT_MAX_ITER})")
    parser.add_argument("--workdir", "-w", help="Working directory for file/shell operations")
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR, help=f"Log directory (default: {DEFAULT_LOG_DIR})")
    parser.add_argument("--log-file", help="Specific log file path (overrides auto-generated name)")
    parser.add_argument("--context", help="Background context to include in the system prompt")
    parser.add_argument("--context-file", help="Read background context from a file")
    parser.add_argument("--context-budget", type=int, default=0, help="Approximate context token budget: told to the agent, and past ~70%% of it the runner stubs the oldest tool results (see compact_messages)")
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
        api_key=args.api_key,
    )


if __name__ == "__main__":
    main()
