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
import atexit
import json
import os
import platform
import re
import signal
import sys
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path

from openai import OpenAI

import tools as _tools
from tools import (TOOL_SCHEMAS, TOOL_HANDLERS, coerce_args, get_plan, plan_block,
                   reset_plan, update_plan)

# beast-chat session ledger. Optional on purpose: a runner whose checkout
# predates sessions.py, or whose import fails for any reason, must still run.
try:
    import sessions as _sessions
except Exception:                                      # pragma: no cover
    _sessions = None

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:8080/v1"
# llama.cpp ignores the id, but the API requires one. vLLM / TensorFold serve a
# named model: conf.sh exports OPENBEAST_INFERENCE_MODEL for those backends
# (INFERENCE_MODEL, set by scripts/backends/use-model.sh).
DEFAULT_MODEL = os.environ.get("OPENBEAST_INFERENCE_MODEL") or "qwen-27b-q5"
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
   first, then call it again only when a step changes status (exactly one step
   in_progress at a time; the plan is re-shown every turn, never re-send it unchanged).
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


def platform_line(system: str | None = None, shell: str | None = None) -> str:
    """One line naming the tool host's OS and the bash tool's shell.

    Runner v2 (audit of GLM-5.3-Flash's v5 failures, 2026-11-16). Tools run
    where THIS process runs (AGENTS.md), which for the eval harness and Mac
    clients is macOS: GNU habits (`sed -i 's/..'`, `timeout`, `cat -A`)
    failed there and cost turns, because nothing said the host was BSD. The
    text is built here, never in system-prompt.md (owner's rule)."""
    system = system or platform.system() or "unknown"
    shell = shell or _tools.BASH_SHELL
    host = system
    if system == "Darwin":
        host = ("Darwin (macOS, BSD userland: `sed -i ''` not `sed -i`, no GNU "
                "`timeout`, no `cat -A`"
                + ("; /bin/bash is bash 3.2" if shell == "/bin/bash" else "") + ")")
    return f"Tool host: {host}. The bash tool runs each command with `{shell} -c`.\n"


def budget_line(max_iter: int) -> str:
    """The turn budget, stated up front (runner v2: most failing v5 units
    ran out of turns while still exploring, never told how many they had)."""
    return (f"Turn budget: you have {max_iter} tool turns (model replies) for this "
            f"task. Spend them on progress: explore briefly, then edit, run the "
            f"check, and call task_done well before they run out.\n")


def build_system_prompt(context: str = "", context_budget: int = 0,
                        max_iter: int = 0) -> str:
    """Assemble the full system prompt with optional context and budget info."""
    parts = []
    if _SOUL_PROMPT:
        parts.append(_SOUL_PROMPT)
    parts.append(_AGENT_INSTRUCTIONS)
    parts.append(platform_line())
    if max_iter > 0:
        parts.append(budget_line(max_iter))
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

def _rebuild_messages_from_log(log_path: str, system_prompt: str,
                               steering: bool = False) -> list[dict]:
    """Reconstruct the conversation from a JSONL log for resumption.

    `steering` is THE EVAL GUARD, carried in (E2). This function replayed
    `steer` events into the message list ~26 lines before `run_agent` ever
    computed the gate, so a --resume of a transcript that contains operator
    messages re-injected every one of them with NEITHER lock applied — the
    guard protected the live inbox and left the recorded history wide open.
    The caller now computes the gate first and passes it here; with steering
    off, a `steer` event is inert transcript, exactly like `paused`.
    """
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
            elif etype == "steer":
                # beast-chat operator messages are real history (unlike the
                # transient plan block), so --resume must put them back in the
                # position they were spoken — between the turns they separated.
                # Under the eval guard they are not replayed at all.
                if steering and event.get("op") == "say":
                    text = event.get("text") or ""
                    if text:
                        messages.append({"role": "user",
                                         "content": _STEER_PREFIX + text})
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
    if fmt and isinstance(args, dict):
        # Called outside the handler's try: a field of the wrong type
        # ({"summary": null}, {"command": 5}) must not kill the run here.
        try:
            return str(fmt(args))
        except Exception:
            pass
    return str(args)[:100]


def _print_token_summary(tokens_prompt: int, tokens_completion: int, tokens_total: int,
                         compactions: int = 0, api_errors: int = 0) -> None:
    """Print the stable-key token line that the eval harness parses.

    API_ERRORS counts model calls that failed for a reason other than a
    context overflow (connection refused, 5xx, timeout). A server that dies
    mid-task leaves the loop burning its remaining iterations on errors and
    exiting 0 with the tokens it had, which looks like a model FAIL; the
    eval harness reads this line to refuse caching such a unit."""
    print(f"TOKENS: prompt={tokens_prompt} completion={tokens_completion} total={tokens_total}")
    print(f"COMPACTIONS: {compactions}")
    print(f"API_ERRORS: {api_errors}")


# ---------------------------------------------------------------------------
# Context-window management (2026-09-11 harness agentics, SOTA review #8).
#
# Messages grew unboundedly, and on a context overflow the loop retried the
# IDENTICAL oversized payload every 5 s until max_iter — e.g. 160 × 5 s of
# guaranteed failures. Now: (a) the server's overflow error is recognised
# and the OLDEST tool results are replaced by one-line stubs before the
# retry; (b) with --context-budget set, the same eviction runs proactively
# past ~70% of the budget (4 chars/token estimate), freeing down to ~50% so
# one compaction buys many turns of stable prefix. The system prompt, the
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
#
# vLLM (vllm/renderers/params.py): HTTP 400 "This model's maximum context
# length is M tokens. However, you requested K output tokens and your prompt
# contains [at least ]N input tokens, for a total of ...". Older releases
# said "...you requested T tokens (N in the messages, K in the completion)".
# TensorFold CUDA (src/tensorfold/cuda/server.py check): "the rendered prompt
# has N tokens and leaves no room for a reply in the server's M-token context
# window" / "...requests K reply tokens, exceeding the server's M-token ...";
# its MLX server (src/tensorfold/server/app.py) says "This server's maximum
# context length is M tokens, but the rendered prompt has N tokens ..." or
# "the rendered prompt has N tokens and requests K reply tokens; this
# server's context window is M." — MLX formats M and N with thousands commas.
# ---------------------------------------------------------------------------
_CHARS_PER_TOKEN = 4
_COMPACT_FRACTION = 0.70          # proactive trigger as a fraction of the budget
#: Proactive compaction frees down to this fraction, not just back under the
#: trigger (hysteresis). Stubbing an old result rewrites the history near its
#: START, which invalidates llama-server's cached prefix from that point on —
#: on the hybrid qwen35 arch everything after the nearest checkpoint is
#: re-prefilled (~50K tokens at an 85K budget). Freeing only (est - trigger)
#: meant the next tool result crossed the line again, so ~1 turn in 1.7
#: re-prefilled (review efficiency-2, 2026-09-29); a 20-point gap buys many
#: turns of stable prefix per compaction.
_COMPACT_LOW_WATER = 0.50
_STUB_MIN_CHARS = 200             # results shorter than this aren't worth stubbing
_STUB_PREFIX = "[tool result elided:"
#: An operator message becomes evictable after this many turns (E12). It is
#: real history, so it outlives ordinary tool results — but it is not
#: IMMORTAL: a single oversized `say` used to be unevictable, so the context
#: could never come back under the window and the run died at the overflow
#: with nothing left to compact. Note the stub does NOT carry _STEER_PREFIX,
#: so a stubbed message is never re-counted as a live directive.
_STEER_STUB_AFTER_TURNS = 3
_STEER_STUB_PREFIX = "[operator message elided:"
_CTX_OVERFLOW_RE = re.compile(
    r"exceed_context_size|exceeds the available context size|"
    r"larger than the max context size|context size has been exceeded|"
    r"context shift is disabled|"
    r"maximum context length is \d|"                          # vLLM, TensorFold MLX
    r"-token (?:context window|safe cache capacity)|"          # TensorFold CUDA
    r"server's context window is \d",                         # TensorFold MLX, reply length
    re.IGNORECASE)
_CTX_FIELDS_RE = re.compile(r"n_prompt_tokens['\"]?\s*[:=]\s*(\d+).*?n_ctx['\"]?\s*[:=]\s*(\d+)", re.S)
_CTX_MSG_RE = re.compile(r"\((\d+) tokens\).*?\((\d+) tokens\)", re.S)
# vLLM states the window FIRST, then the prompt: (n_ctx, n_prompt).
_CTX_VLLM_RE = re.compile(
    r"maximum context length is (\d+) tokens.*?"
    r"(?:contains (?:at least )?(\d+) input tokens|\((\d+) in the messages)", re.S)
# TensorFold, prompt first: CUDA "...has N tokens ... the server's M-token
# ..." and MLX "...has N tokens and requests K reply tokens; this server's
# context window is M". MLX's other form states the window first.
_CTX_TF_RE = re.compile(
    r"rendered prompt has ([\d,]+) tokens.*?"
    r"(?:server's ([\d,]+)-token|context window is ([\d,]+))", re.S)
_CTX_TF_MLX_RE = re.compile(
    r"maximum context length is ([\d,]+) tokens.*?rendered prompt has ([\d,]+) tokens", re.S)
_SCHEMA_CHARS = len(json.dumps(TOOL_SCHEMAS))

# ---------------------------------------------------------------------------
# Per-turn generation bound (review efficiency-5, 2026-09-29).
#
# --reasoning-budget caps THINKING only; a repetition loop in the answer or a
# tool's arguments ran until the client's 600 s read timeout, and the openai
# client then silently retried the identical request twice — ~30 min of the
# single -np 1 slot for one turn. Agent requests now carry max_tokens: the
# deployment's thinking budget + a 12K content allowance. The budget is the
# operator's REASONING_BUDGET when set (env, else openbeast.conf — the value
# serve.sh passes as --reasoning-budget; -1 = unlimited means no cap here
# either), else the shipped serve scripts' 20480. OPENBEAST_AGENT_MAX_TOKENS
# overrides it all (0 = uncapped).
#
# Eval runs were uncapped (run_eval bounds each task by wall time). Runner v2
# caps them too (G11 in docs/HARNESS_NOTES.md): on the 2026-11-16 v5 L0,
# GLM-5.3-Flash's first code-writing turn on 168 ran past the client's 600 s
# read timeout in 5 of 5 attempts, was retried identically, and the unit spent
# its whole 1,800 s wall without writing a file. A capped turn instead ends
# with finish_reason "length" and the truncation recovery below hands the head
# back to the model with a nudge. The eval cap is fixed, the same for every
# model: 8K tokens with thinking off (a v5 solution file is a few thousand
# tokens; the cap only bites on a runaway), 16K with OPENBEAST_AGENT_THINKING=on
# (room for a long think plus the same answer). The client's read timeout is
# sized to the cap at a pessimistic 8 tok/s, so a capped turn on a busy
# 4-stream server is never cut off by the client first: the first sizing (16K
# at 20 tok/s) was not pessimistic enough. Measured on the runner v2 arm, a
# long-context turn under 4-way load decoded at ~12 tok/s (6,818 tokens in
# 580 s), so the client's 819 s timeout fired before the 16K cap and its
# retry lost the turn twice (168_c, 1,638 s). Eval runs also never retry a
# timed-out turn (_EVAL_CLIENT_MAX_RETRIES): the loop owns recovery.
#
# The 16K thinking cap was re-measured on arm C (2026-11-17, 71 units, 4
# jobs) and kept. The 78 capped turns decoded at 15.1-25.8 tok/s (median
# 19.1), so 8 tok/s still leaves the client 2x margin. Turns that ended on
# their own: p50 ~0.4K, p90 ~2.3K, p99 ~12.4K, max ~15.1K tokens
# (estimated from turn time and each unit's completion total). A higher cap
# was rejected: what a capped think would have needed is censored, the same
# 163/168 units capped 2-4 turns in a row, and every extra 8K tokens is
# ~7 more minutes per stalled turn on a 3,600 s wall. The fix is the
# recovery instead: a capped think keeps its tail (_capped_think_nudge).
# ---------------------------------------------------------------------------
_DEFAULT_REASONING_BUDGET = 20480   # --reasoning-budget in the shipped serve scripts
_CONTENT_ALLOWANCE_TOKENS = 12288
_DEFAULT_MAX_COMPLETION_TOKENS = _DEFAULT_REASONING_BUDGET + _CONTENT_ALLOWANCE_TOKENS
_CLIENT_MAX_RETRIES = 1        # one quick retry for a blip; the loop owns the rest
_EVAL_CLIENT_MAX_RETRIES = 0   # eval: a retried timeout re-spends the whole turn
_EVAL_TURN_TOKENS = 8192            # eval cap, thinking off/unset (G11)
_EVAL_TURN_TOKENS_THINKING = 16384  # eval cap, OPENBEAST_AGENT_THINKING=on
_CLIENT_TIMEOUT_FLOOR_S = 600.0     # the openai client's own default
_PESSIMISTIC_TOK_S = 8.0            # per-stream decode under --jobs load
# A 429 is admission control (beast-gate's per-device in-flight / rate caps,
# beast-chat's slot cap), not a failed turn: wait the server's Retry-After and
# re-send the SAME turn, without spending an iteration or counting an API
# error. Bounded so a device wedged at 429 still ends instead of hanging.
_RATE_LIMIT_MAX_WAIT_S = 600
_RATE_LIMIT_DEFAULT_S = 5.0


def _retry_after_s(exc: Exception) -> float | None:
    """Seconds to wait when `exc` is an HTTP 429, else None."""
    if getattr(exc, "status_code", None) != 429:
        return None
    resp = getattr(exc, "response", None)
    raw = None
    try:
        raw = resp.headers.get("retry-after") if resp is not None else None
    except Exception:
        raw = None
    try:
        wait = float(raw) if raw is not None else _RATE_LIMIT_DEFAULT_S
    except (TypeError, ValueError):
        wait = _RATE_LIMIT_DEFAULT_S
    return min(max(wait, 0.5), 60.0)
_TRUNCATED_KEEP_CHARS = 2000   # head of a capped turn kept in the history

# ---------------------------------------------------------------------------
# A thinking turn cut off by the cap (2026-11-17, docs/HARNESS_NOTES.md
# "Thinking stall"). On arm C (GLM-5.3-Flash, thinking on, 16K eval cap) all
# 78 capped turns were pure reasoning: no content, no tool call. The
# recovery above kept nothing and the next turn re-derived the plan from
# scratch, and after a cap on a code-writing turn that re-derivation was
# capped again or killed by the wall 33 times in 47 (it wrote the file
# straight away 8 times). Those turns cost 1,136 minutes and 13 of C's 17
# failures. So when a capped turn has no content and no tool call but does
# have reasoning, the nudge carries the reasoning's last
# _CAPPED_REASONING_TAIL_CHARS (~1.5K tokens) and says: conclude and act.
# Bounded per cap, so a chain of caps cannot grow the history without
# limit. Every other turn keeps the default: reasoning is never fed back.
# ---------------------------------------------------------------------------
_CAPPED_REASONING_TAIL_CHARS = 6000
_CONF_PATH = Path(__file__).resolve().parent.parent / "openbeast.conf"


def _reasoning_text(message) -> str:
    """The turn's reasoning as the server returned it: `reasoning_content`
    (llama.cpp, TensorFold, vLLM) or `reasoning` (some OpenAI-compatible
    servers); the openai SDK keeps unknown fields as attributes or in
    model_extra. "" when there is none."""
    for attr in ("reasoning_content", "reasoning"):
        v = getattr(message, attr, None)
        if isinstance(v, str) and v.strip():
            return v
    extra = getattr(message, "model_extra", None)
    if isinstance(extra, dict):
        for attr in ("reasoning_content", "reasoning"):
            v = extra.get(attr)
            if isinstance(v, str) and v.strip():
                return v
    return ""


def _reasoning_tail(reasoning: str, limit: int = _CAPPED_REASONING_TAIL_CHARS) -> str:
    """The last `limit` chars of `reasoning`, started at a line boundary
    when one falls in the first fifth (a tail that opens mid-word reads
    like noise)."""
    text = reasoning.strip()
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    nl = tail.find("\n", 0, limit // 5)
    return tail[nl + 1:] if nl >= 0 else tail


def _capped_think_nudge(max_tokens: int, tail: str) -> str:
    return (f"Your last turn hit the {max_tokens:,}-token output cap while you were "
            "still thinking: no answer and no tool call came out. The end of that "
            "reasoning, so you do not start over:\n"
            "<<<\n" + tail + "\n>>>\n"
            "You were cut off mid-reasoning. Do not re-derive what is above: conclude "
            "from it and act now. Make the tool call it was leading to (write the "
            "file, run the test) or call task_done, and keep any further thinking short.")


def _conf_value(key: str) -> str:
    """Last `key=value` in openbeast.conf, read (never sourced) the way
    scripts/lib/conf.sh reads it; "" when absent or unreadable."""
    val = ""
    try:
        with open(_CONF_PATH) as fh:
            for line in fh:
                k, sep, v = line.strip().partition("=")
                if sep and not k.startswith("#") and k.strip() == key:
                    val = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return val


def _max_completion_tokens() -> int:
    """max_tokens for an agent request; 0 means send none (uncapped)."""
    raw = os.environ.get("OPENBEAST_AGENT_MAX_TOKENS", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    if os.environ.get(_EVAL_MARKER):
        thinking = os.environ.get("OPENBEAST_AGENT_THINKING", "").strip().lower() == "on"
        return _EVAL_TURN_TOKENS_THINKING if thinking else _EVAL_TURN_TOKENS
    # AGENT_MAX_TOKENS in openbeast.conf: the serving host's own turn cap for
    # its agents (never eval runs, decided above). REASONING_BUDGET below is
    # a llama.cpp knob (serve.sh --reasoning-budget); a TensorFold or vLLM
    # host has no such flag, so the cap is set here instead. spark-1 sets
    # 16384, arm C's cap (docs/SPARK_HYDRA.md, "Recommended serving
    # configuration").
    raw = _conf_value("AGENT_MAX_TOKENS").split("#", 1)[0].strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    budget = (os.environ.get("OPENBEAST_REASONING_BUDGET", "").strip()
              or os.environ.get("REASONING_BUDGET", "").strip()
              or _conf_value("REASONING_BUDGET"))
    if not budget:
        return _DEFAULT_MAX_COMPLETION_TOKENS
    try:
        n = int(budget)
    except ValueError:
        return _DEFAULT_MAX_COMPLETION_TOKENS
    if n < 0:
        return 0   # unlimited thinking: a token cap would cut it mid-thought
    return n + _CONTENT_ALLOWANCE_TOKENS


def _client_timeout_s(max_tokens: int) -> float:
    """Read timeout for the model client: the openai default, raised so a
    turn that runs to `max_tokens` at a pessimistic per-stream rate is ended
    by the cap (recoverable), not by the client (a lost, retried turn)."""
    if max_tokens <= 0:
        return _CLIENT_TIMEOUT_FLOOR_S
    return max(_CLIENT_TIMEOUT_FLOOR_S, max_tokens / _PESSIMISTIC_TOK_S)


def _thinking_kwargs() -> dict:
    """Per-request thinking toggle (runner v2), OPENBEAST_AGENT_THINKING:
    on / off send chat_template_kwargs.enable_thinking true / false (the
    switch for a "thinking on" ablation arm; checked live on the Spark GLM
    TensorFold server, where `true` returns reasoning_content and the
    default none). Unset, or any other value, sends nothing: the request is
    byte-identical to before. run_eval copies the parent env into the
    runner, so `OPENBEAST_AGENT_THINKING=on python evals/run_eval.py` reaches
    here. reasoning_content is not put back in the history: only
    message.content and tool_calls are kept (see the assistant turn below).
    The one exception is a turn the output cap cut off while it was still
    thinking (no content, no tool call): a bounded tail of its reasoning
    goes back in the nudge, so the next turn continues instead of starting
    over (_capped_think_nudge)."""
    v = os.environ.get("OPENBEAST_AGENT_THINKING", "").strip().lower()
    if v not in ("on", "off"):
        return {}
    return {"extra_body": {"chat_template_kwargs": {"enable_thinking": v == "on"}}}


#: Request header beast-gate reads (agents/edge.py, GATE_DEFAULTS_HEADER):
#: "off" = forward this request with no gate-side defaults (today: the
#: INFERENCE_THINKING injection). Every eval-mode request carries it, so an
#: eval run that names no thinking mode reaches the server exactly as it
#: always did and gets the SERVER's default, on every host: thinking on for
#: the llama.cpp Qwen templates, off on TensorFold GLM. Sending an explicit
#: enable_thinking=false instead would have kept the GLM baselines and
#: silently turned thinking off for every llama.cpp eval row.
GATE_DEFAULTS_HEADER = "X-OpenBeast-Gate-Defaults"


def _inference_thinking() -> str:
    """The serving host's thinking default, INFERENCE_THINKING (env
    OPENBEAST_INFERENCE_THINKING, else openbeast.conf): "on", "off" or ""
    (unset, or any other value)."""
    v = (os.environ.get("OPENBEAST_INFERENCE_THINKING", "").strip()
         or _conf_value("INFERENCE_THINKING")).split("#", 1)[0].strip().lower()
    return v if v in ("on", "off") else ""


def _agent_thinking_kwargs() -> dict:
    """What the run sends for thinking. OPENBEAST_AGENT_THINKING on/off
    always wins (_thinking_kwargs). Unset: an eval run sends nothing, as
    before (the era's baselines are "the server's default"); any other run
    follows the host's INFERENCE_THINKING, so OpenBeast's own agents think
    by default where the operator turned that on (spark-1: arm C, the best
    v5 configuration). Neither set: nothing, and the server (or beast-gate,
    for a client-mode agent) decides."""
    kw = _thinking_kwargs()
    if kw or os.environ.get(_EVAL_MARKER):
        return kw
    v = _inference_thinking()
    if not v:
        return {}
    return {"extra_body": {"chat_template_kwargs": {"enable_thinking": v == "on"}}}


def _seed_kwargs() -> dict:
    """Per-run sampling seed (runner v2), OPENBEAST_AGENT_SEED=<int>: sent as
    `seed` on every request. The Spark TensorFold server falls back to a FIXED
    seed when none is sent (probed 2026-11-16: three temperature-0.6 requests
    gave byte-identical text; seeds 1/2/3 gave three texts, seed 1 twice gave
    one), so every run there is the same single draw and a 23-byte prompt
    change flips units. Repeating an arm under different seeds is how its
    effect is told from that chaos (G12). Unset sends nothing."""
    raw = os.environ.get("OPENBEAST_AGENT_SEED", "").strip()
    try:
        return {"seed": int(raw)} if raw else {}
    except ValueError:
        return {}


def _is_context_overflow(err: str) -> bool:
    return bool(_CTX_OVERFLOW_RE.search(err or ""))


def _overflow_tokens(err: str) -> tuple[int, int] | None:
    """(n_prompt_tokens, n_ctx) parsed from the error text, or None."""
    m = _CTX_FIELDS_RE.search(err or "") or _CTX_MSG_RE.search(err or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _CTX_VLLM_RE.search(err or "")
    if m:
        return int(m.group(2) or m.group(3)), int(m.group(1))

    def num(s: str) -> int:
        return int(s.replace(",", ""))
    m = _CTX_TF_RE.search(err or "")
    if m:
        return num(m.group(1)), num(m.group(2) or m.group(3))
    m = _CTX_TF_MLX_RE.search(err or "")
    if m:
        return num(m.group(2)), num(m.group(1))
    return None


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


def _steer_stub(content: str) -> str:
    return f"{_STEER_STUB_PREFIX} {len(content)} chars]"


#: One of the two bars a tool result must clear to count as oversized on
#: the proactive path: this many times the median of the other live results.
_OVERSIZED_VS_MEDIAN = 4


_STUB_SIZE_RE = re.compile(re.escape(_STUB_PREFIX) + r" (\d+) chars")


def _oversized(messages: list[dict], tool_results: list[int], gain,
               gap: int) -> int | None:
    """The largest live tool result if it is OVERSIZED, else None.

    Oversized = it frees at least half the hysteresis gap (10% of the
    context budget on the proactive path) AND at least _OVERSIZED_VS_MEDIAN
    times the median size of every other tool result this run has seen —
    stubbed ones counted at their original size, so the bar does not sink
    as compaction thins the live history. Both bars matter: the gap share
    alone would call an ordinary 12 KB read oversized under a tiny budget,
    and the median alone would do the same in a history of `ls` one-liners.
    An ordinary result never qualifies, so ordinary compaction stays
    oldest-first down to the low-water mark (efficiency-2)."""
    if not tool_results or gap <= 0:
        return None
    big = max(tool_results, key=gain)
    g = gain(big)
    sizes = []
    for i, m in enumerate(messages):
        if i <= 1 or i == big or m.get("role") != "tool":
            continue
        c = str(m.get("content") or "")
        hit = _STUB_SIZE_RE.match(c)
        sizes.append(int(hit.group(1)) if hit else len(c))
    sizes.sort()
    median = sizes[len(sizes) // 2] if sizes else 0
    if 2 * g >= gap and g >= _OVERSIZED_VS_MEDIAN * median:
        return big
    return None


def compact_messages(messages: list[dict], chars_to_free: int,
                     call_index: dict[int, int] | None = None,
                     steer_eligible=None,
                     must_free: int | None = None) -> tuple[int, int]:
    """Replace the OLDEST tool results with one-line stubs until at least
    `chars_to_free` characters are freed (or nothing evictable remains).

    Never touches: messages[0] (system prompt), the first user message (the
    task), assistant turns, user nudges, or results already stubbed. Oldest
    first and stop as soon as enough is freed, so the newest results — the
    ones the model is about to act on — survive unless the older ones can't
    cover the ask (being stuck beats keeping them). One exception: when the
    walk would reach a tool result that frees more than every older one it
    plans to stub, that one is stubbed first, sparing older results the ask
    no longer needs. Returns
    (results_evicted, chars_freed). `call_index` maps message position ->
    tool-call ordinal for the stub text.

    `steer_eligible` (E12) is the set of message positions holding operator
    messages old enough to evict (see _STEER_STUB_AFTER_TURNS). They are
    considered only after every tool result has been stubbed. It is EMPTY
    whenever steering is off, which is what keeps this function byte-for-byte
    identical to its pre-beast-chat behaviour on every eval unit.

    `must_free` (proactive path) is the part of the ask that is REQUIRED —
    back under the trigger; the rest of `chars_to_free` is the hysteresis
    gap down to the low-water mark, which is wanted but not worth history.
    When the largest live tool result ANYWHERE in the history is oversized
    (see _oversized: at least half the hysteresis gap chars_to_free -
    must_free, and several times the run's median result), it is stubbed first
    and the walk then stops at `must_free`: older results are spent only if
    the trigger itself needs them, never for the gap (review efficiency-3,
    rounds 2-3). None = the whole ask is required (the overflow path, and
    every pre-existing caller).
    """
    call_index = call_index or {}
    steer_eligible = steer_eligible or ()
    tool_results: list[int] = []
    aged_steers: list[int] = []
    for i, m in enumerate(messages):
        if i <= 1:
            continue
        content = str(m.get("content") or "")
        if len(content) <= _STUB_MIN_CHARS:
            continue
        if m.get("role") == "tool":
            if not content.startswith(_STUB_PREFIX):
                tool_results.append(i)
        elif (i in steer_eligible and m.get("role") == "user"
                and content.startswith(_STEER_PREFIX)):
            aged_steers.append(i)
    # Tool results first (oldest first), aged operator messages only as a LAST
    # resort: a directive outranks a transcript of `ls`, but it stops short of
    # being unevictable, which is the failure E12 describes.
    candidates = tool_results + aged_steers
    ask = max(chars_to_free, 1)
    must = ask if must_free is None else max(min(must_free, ask), 1)

    def _stub_for(i: int) -> str:
        c = messages[i]["content"]
        return (_steer_stub(c) if messages[i].get("role") != "tool"
                else _stub(c, call_index.get(i, i)))

    def _gain(i: int) -> int:
        return len(messages[i]["content"]) - len(_stub_for(i))

    # The oldest-first walk, planned before anything is touched. If it would
    # reach a tool result that frees more than every older planned result
    # combined, stub THAT one first and only then continue oldest-first for
    # whatever is left of the ask: an oversized result (a fetch alone can be
    # 200K chars) used to strip EVERY older result first, so one bad call
    # cost the agent its whole working memory (review efficiency-3). On that
    # overflow path only the order changes: the walk still stops once the
    # ask is met, so it never stubs a result the plain walk would have kept.
    # The proactive path first looks for an oversized result in the WHOLE
    # history (below), since the plain walk to low water rarely reaches it.
    plan, planned = [], 0
    for i in candidates:
        if planned >= ask:
            break
        plan.append(i)
        planned += _gain(i)
    big = _oversized(messages, tool_results, _gain, ask - must) if must < ask else None
    if big is not None:
        # Proactive path, one oversized result anywhere in the history (not
        # only inside the oldest-first plan: a 50K-200K result is usually
        # smaller than the older history, so the plan met the ask before
        # ever reaching it and spent the history instead — review
        # efficiency-3, round 3). Stub it first, then walk oldest-first only
        # as far as the TRIGGER needs (usually nowhere): older history is
        # never spent on reaching the low-water mark.
        candidates = [big] + [c for c in candidates if c != big]
        ask = must
    else:
        tools_in_plan = [i for i in plan if messages[i].get("role") == "tool"]
        if len(plan) > 1 and tools_in_plan:
            big = max(tools_in_plan, key=_gain)
            older = sum(_gain(i) for i in plan[:plan.index(big)])
            if older and _gain(big) > older:
                candidates = [big] + [c for c in plan if c != big]
    evicted = freed = 0
    for i in candidates:
        if freed >= ask:
            break
        content = messages[i]["content"]
        stub = _stub_for(i)
        messages[i]["content"] = stub
        freed += len(content) - len(stub)
        evicted += 1
    return evicted, freed


def proactive_asks(est: int, context_budget: int) -> tuple[int, int] | None:
    """(must_free, chars_to_free) for the proactive --context-budget path, or
    None below the trigger. must_free gets the estimate back under the
    trigger; chars_to_free goes on to the low-water mark (hysteresis)."""
    target = int(context_budget * _COMPACT_FRACTION)
    if context_budget <= 0 or est <= target:
        return None
    low = int(context_budget * _COMPACT_LOW_WATER)
    return ((est - target) * _CHARS_PER_TOKEN, (est - low) * _CHARS_PER_TOKEN)


def _unsurrogate(v):
    """A lone UTF-16 surrogate (a model's "\\ud800" JSON escape) cannot be
    encoded as UTF-8: it crashes print() and the next request's JSON body.
    Strings come back with each one as '?'; dicts/lists are rebuilt."""
    if isinstance(v, str):
        return v.encode("utf-8", "replace").decode("utf-8")
    if isinstance(v, dict):
        return {k: _unsurrogate(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_unsurrogate(x) for x in v]
    return v


def _scrub_surrogates(messages: list[dict]) -> int:
    """In place, on the history: how many message fields changed."""
    n = 0
    for m in messages:
        for k, v in m.items():
            clean = _unsurrogate(v)
            if clean != v:
                m[k] = clean
                n += 1
    return n


def _scrub_plan_surrogates() -> int:
    """The plan is the other half of every request (_with_plan re-injects
    plan_block() each turn) and lives outside `messages`, in tools'
    ContextVar: update_plan keeps a step's text exactly as json.loads gave
    it. Scrubbing only the history left a surrogate in a plan step failing
    every later request. 1 if the plan was rewritten, else 0."""
    steps = get_plan()
    if not steps:
        return 0
    clean = _unsurrogate(steps)
    if clean == steps:
        return 0
    update_plan(clean)   # the steps already passed validation once
    return 1


def _safe_stdio():
    """print() of model/tool text must never be what kills the run."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


#: The remaining-turns notice appears when this many turns or fewer are left
#: (the current one included).
_TURNS_LOW = 3


def turns_notice(iteration: int, max_iter: int) -> str:
    """A short notice for the last _TURNS_LOW turns, else ''. Rides in the
    transient block with the plan (never stored in the history)."""
    left = max_iter - iteration + 1
    if left > _TURNS_LOW or left < 1:
        return ""
    if left == 1:
        return ("[turn budget — this is your LAST turn: call task_done now "
                "if the work is done]")
    return (f"[turn budget — {left} turns left, this one included: stop "
            f"exploring; finish, verify, and call task_done]")


# ---------------------------------------------------------------------------
# G23 — wall-clock warnings (opt-in, OPENBEAST_AGENT_WALL_WARN=1)
#
# The runner knew its turn budget but not the unit's wall budget, so a slow
# model was killed by run_eval's timeout with no warning. With the switch on
# and a wall known (--wall-s, or OPENBEAST_AGENT_WALL_S, which run_eval's
# run_agent sets to the scaled wall it kills at), the first turn starting
# past 50% and the first past 80% get one transient notice each, next to the
# turns notice (never stored in the history). Prior art: Droid shows elapsed
# time only past thresholds; LangChain deepagents' time-budget warnings.
# ---------------------------------------------------------------------------

#: Fractions of the wall at which a notice fires, once each.
_WALL_MARKS = (0.5, 0.8)

#: The runner's clock (monotonic; a module attribute so tests can fake it).
_clock = time.monotonic


def wall_warn_enabled() -> bool:
    return os.environ.get("OPENBEAST_AGENT_WALL_WARN", "").strip() == "1"


def wall_budget_s(explicit: float | None = None) -> float:
    """The unit's wall budget: the explicit value (--wall-s) if positive,
    else OPENBEAST_AGENT_WALL_S, else 0 (unknown: no notices)."""
    if explicit and explicit > 0:
        return float(explicit)
    try:
        v = float(os.environ.get("OPENBEAST_AGENT_WALL_S", "").strip() or 0)
    except ValueError:
        return 0.0
    return v if v > 0 else 0.0


def wall_notice(elapsed: float, wall_s: float, fired: set) -> str:
    """The notice for the first turn starting past a mark not yet fired, else
    ''. A turn that jumps past several marks fires once, for the highest, and
    retires the lower ones. Mutates `fired`."""
    if wall_s <= 0:
        return ""
    crossed = [m for m in _WALL_MARKS if elapsed >= m * wall_s and m not in fired]
    if not crossed:
        return ""
    fired.update(m for m in _WALL_MARKS if m <= max(crossed))
    return (f"[wall clock — {int(elapsed)} s of your {int(wall_s)} s wall used; "
            f"finish and verify the destination now]")


# ---------------------------------------------------------------------------
# G22 — first-turn environment block (opt-in, OPENBEAST_AGENT_ENV_BLOCK=1)
#
# Most failing GLM v5 units ended on the turn cap after spending early turns
# on `which`, `--version` and `ls`. Prior art: Droid's startup environment
# block, shown as shell command output ("helped the agent avoid reissuing
# similar commands"); Meta-Harness's sandbox snapshot "saves 2-5 early
# exploration turns". This block is about THIS run (versions, workdir, the
# task's paths); host userland quirks belong to platform_line and the packs.
#
# Placement: appended to the first user message (the task), formatted as
# the output of a probe. Not a synthetic tool result: that needs a fabricated
# assistant tool_call the model never made, which chat templates validate
# and the transcript would attribute to the model. Stored in the history: it
# is a fact that never changes, so it costs one prefix, not one per turn.
#
# Never prints environment values or anything outside the task's
# /tmp/eval_* directories.
# ---------------------------------------------------------------------------

#: Hard size cap (~300 tokens at 4 chars/token).
_ENV_BLOCK_MAX_CHARS = 1200
_ENV_PROBE_TIMEOUT_S = 3.0
_ENV_LIST_MAX = 30
_ENV_PATHS_MAX = 12
_ENV_PATHS_CHARS = 360
#: Same commands as evals/run_eval.py toolchain_versions(), plus python3 and
#: the C compilers the task may call by any of their names.
_ENV_TOOLCHAINS = (("python3", ["python3", "--version"]),
                   ("go", ["go", "version"]),
                   ("rustc", ["rustc", "--version"]),
                   ("zig", ["zig", "version"]),
                   ("cc", ["cc", "--version"]),
                   ("clang", ["clang", "--version"]),
                   ("gcc", ["gcc", "--version"]))
_ENV_CLI = ("git", "make", "cmake", "timeout", "gtimeout", "perl", "jq")


def env_block_enabled() -> bool:
    return os.environ.get("OPENBEAST_AGENT_ENV_BLOCK", "").strip() == "1"


def _probe_version(cmd: list[str]) -> str | None:
    """First line of a version command, None when the tool is absent."""
    import shutil
    import subprocess
    # The bash tool's PATH (G24: under eval, python3 is the validators').
    exe = shutil.which(cmd[0], path=_tools.shell_path())
    if not exe:
        return None
    try:
        out = subprocess.run([exe] + list(cmd[1:]), capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=_ENV_PROBE_TIMEOUT_S)
        text = (out.stdout or out.stderr or "").strip()
        return text.splitlines()[0][:50] if text else "?"
    except Exception:
        return "?"


def _human_size(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n}B"  # pragma: no cover


def _eval_roots(expected: list[str]) -> list[str]:
    """The task's /tmp/eval_* top-level directories (only those)."""
    roots = []
    for p in expected:
        m = re.match(r"^/tmp/(eval[\w\-.]*)", p)
        if m:
            root = "/tmp/" + m.group(1).rstrip(".")
            if root not in roots:
                roots.append(root)
    return roots


def _list_eval_dir(root: str, budget: list[int]) -> list[str]:
    """Depth <= 2 listing of one eval dir, sharing the entry budget. Symlinks
    are shown, never followed."""
    lines: list[str] = []

    def walk(d: str, depth: int, indent: str):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return
        for name in names:
            if budget[0] <= 0:
                budget[1] += 1
                continue
            full = os.path.join(d, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            import stat as _stat
            budget[0] -= 1
            if _stat.S_ISLNK(st.st_mode):
                lines.append(f"{indent}{name} -> (symlink)")
            elif _stat.S_ISDIR(st.st_mode):
                lines.append(f"{indent}{name}/")
                if depth < 2:
                    walk(full, depth + 1, indent + "  ")
            else:
                lines.append(f"{indent}{name} {_human_size(st.st_size)}")

    walk(root, 1, "    ")
    return lines


def env_block(workdir: str | None = None, expected: list[str] | None = None,
              system: str | None = None, machine: str | None = None) -> str:
    """The first-turn environment block (G22). Bounded by
    _ENV_BLOCK_MAX_CHARS; never includes an environment value."""
    from concurrent.futures import ThreadPoolExecutor
    expected = _tools._task_expected_paths() if expected is None else expected
    # Only the eval dirs: run_eval extracts exactly /tmp/eval* paths, and
    # anything else (a home path in a hand-set variable) is not echoed.
    expected = [p for p in expected if p.startswith("/tmp/eval")]
    system = system or platform.system() or "unknown"
    machine = machine or platform.machine() or "unknown"
    with ThreadPoolExecutor(max_workers=len(_ENV_TOOLCHAINS)) as ex:
        found = list(zip([n for n, _ in _ENV_TOOLCHAINS],
                         ex.map(lambda nc: _probe_version(nc[1]), _ENV_TOOLCHAINS)))
    # cc/clang/gcc are often one compiler: group identical answers.
    groups: dict[str, list[str]] = {}
    for name, ver in found:
        if ver is not None:
            groups.setdefault(ver, []).append(name)
    tc = [f"{'/'.join(names)}: {ver}" for ver, names in groups.items()]
    absent = [n for n, v in found if v is None]
    import shutil
    have = [t for t in _ENV_CLI if shutil.which(t)]
    lacking = [t for t in _ENV_CLI if t not in have]

    head = ["[environment — probed by the harness before your first turn; "
            "this is command output, no need to re-run it]",
            f"os: {system} {machine}",
            "toolchains: " + ("; ".join(tc) or "none found")
            + (f" (not installed: {', '.join(absent)})" if absent else ""),
            "cli: " + (" ".join(have) or "none")
            + (f" (missing: {' '.join(lacking)})" if lacking else ""),
            f"workdir: {workdir or os.getcwd()}"]
    if expected:
        # Bounded on its own (<= _ENV_PATHS_MAX names, <= _ENV_PATHS_CHARS),
        # so a long path list can never crowd out the rest of the block.
        line = "expected task paths: "
        shown = 0
        for p in expected[:_ENV_PATHS_MAX]:
            mark = f"{p} ({'exists' if os.path.lexists(p) else 'missing'})"
            if shown and len(line) + len(mark) + 2 > _ENV_PATHS_CHARS:
                break
            line += (", " if shown else "") + mark
            shown += 1
        more = len(expected) - shown
        head.append(line + (f", +{more} more" if more > 0 else ""))
    listing: list[str] = []
    budget = [_ENV_LIST_MAX, 0]           # [entries left, entries dropped]
    for root in _eval_roots(expected):
        if os.path.isdir(root) and not os.path.islink(root):
            listing.append(f"  {root}/")
            listing.extend(_list_eval_dir(root, budget))
    if listing:
        head.append("task dirs (depth <= 2):")
    if budget[1]:
        listing.append(f"    ... +{budget[1]} more entries")
    # Whole lines only: the head always fits (each part is bounded), the
    # listing is cut at the line that would cross the cap.
    out = "\n".join(head)[:_ENV_BLOCK_MAX_CHARS]
    tail = "\n    ... (truncated)"
    for i, ln in enumerate(listing):
        if len(out) + 1 + len(ln) + (len(tail) if i < len(listing) - 1 else 0) \
                > _ENV_BLOCK_MAX_CHARS:
            out += tail
            break
        out += "\n" + ln
    return out


#: Consecutive identical tool calls (same name, args AND result) after which
#: the result gets _repeat_nudge appended.
_REPEAT_AFTER = 3


def _repeat_nudge(n: int) -> str:
    return (f"\n\n[You have seen this exact output {n} times; act on it "
            f"(edit, run the tests, or call task_done)]")


def _call_key(name: str, args) -> str:
    """Canonical form of a tool call, so {"a":1,"b":2} == {"b":2,"a":1}."""
    try:
        return name + "\0" + json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return name + "\0" + repr(args)


def _with_plan(messages: list[dict], plan: str) -> list[dict]:
    """Request payload = history + the current plan block (transient: the
    block is never stored, so it can't bloat or be evicted). Folded into a
    trailing user message when one exists so roles keep alternating.

    E14: never fold into a message carrying _STEER_PREFIX. An operator
    message is a directive the model must act on NOW; gluing a step ladder
    onto its tail dilutes it and — worse — makes the boundary between what
    the operator said and what the harness appended invisible. In that one
    case the plan goes in its own message.
    """
    if not plan:
        return messages
    last = messages[-1] if messages else None
    if (last and last.get("role") == "user" and last is not messages[1]
            and not str(last.get("content") or "").startswith(_STEER_PREFIX)):
        merged = dict(last)
        merged["content"] = f"{last.get('content') or ''}\n\n{plan}"
        return messages[:-1] + [merged]
    return messages + [{"role": "user", "content": plan}]


# ---------------------------------------------------------------------------
# beast-chat: session ledger + steering inbox
#
# INERT BY DEFAULT. With beast-chat unconfigured, every path below is skipped
# and the runner emits the same events, builds the same message list, prints
# the same stdout and returns the same exit codes as before this feature.
# ---------------------------------------------------------------------------

#: Marks an operator message in both `messages` and the transcript. Unlike the
#: plan block this IS stored in history — the model should remember being told.
_STEER_PREFIX = "[operator message] "

#: Bounded poll while paused. Never a busy loop; a paused agent costs nothing.
_PAUSE_POLL_S = 1.0

#: Hard ceiling on an operator message, applied here as well as in
#: sessions.read_new_ops (E12) — the runner must bound its own context even
#: if it is handed ops by something other than that reader.
_SAY_MAX_CHARS = 4000

#: Set unconditionally by evals/run_eval.py in every child environment. It is
#: the ONLY lock that does not depend on how a task happens to be worded or on
#: how the operator's shell happens to be configured.
_EVAL_MARKER = "OPENBEAST_EVAL"


def _steering_enabled(explicit: bool = False) -> bool:
    """The single gate for the ledger AND the steering inbox.

    ===================== HARD EVAL GUARD (E1) =====================
    An eval unit is a scientific measurement, half of a paired A/B row. If an
    operator message — or a stale, pre-planted inbox file from an earlier
    interactive run — reaches one, the row is silently corrupted and the
    cache-key era does not show it. A reviewer drove a real operator message
    into a measured unit through the genuine spawn path, so this gate is now
    THREE locks and the first of them is unconditional:

      L1  OPENBEAST_EVAL — evals/run_eval.py sets it in EVERY child env, and
          pops OPENBEAST_BEAST_CHAT from that env at the same time. It does
          not depend on the task text, the shell, or openbeast.conf. This is
          the lock that actually holds.
      L2  OPENBEAST_TASK_PATHS — kept, checked next. It used to be the last
          line of defence, which was luck: it is derived from whether a task
          SPEC happens to mention a /tmp/eval path. All 291 current variants
          do; that is a property of wording, not of being a measurement.
      L3  EXPLICIT ARGV ONLY — `--steer` or `--session-id`. The environment
          opt-in is GONE. scripts/lib/conf.sh exports OPENBEAST_BEAST_CHAT
          unconditionally and run_eval copied the whole environment into the
          child, so "opt-in" was already open in any configured shell. A
          spawner that wants steering must now say so on the command line;
          agents/chat_server.py passes `--session-id <id> --steer`.

    Under eval mode the inbox is never opened, never created, and never even
    stat()ed: this returns False FIRST, before any other consideration, and
    run_agent caches the result for the whole run.
    ================================================================
    """
    if os.environ.get(_EVAL_MARKER):
        return False
    if os.environ.get("OPENBEAST_TASK_PATHS"):
        return False
    if _sessions is None:
        return False
    return bool(explicit)


def _session_id_from_log(log_path: str) -> str | None:
    """Recover the MCP agent id from its log filename (`agent-<id>.jsonl`).

    mcp_server.start_agent already names the transcript after the agent id, so
    reusing it keeps one identity across the tool server, the ledger and the
    console instead of minting a second name for the same run.
    """
    m = re.fullmatch(r"agent-(.+)\.jsonl", os.path.basename(log_path or ""))
    return m.group(1) if m else None


def _apply_steer_ops(ops: list[dict], messages: list[dict], log_event,
                     paused: bool = False) -> dict:
    """Fold a batch of inbox ops into the conversation.

    Returns {"stop", "paused", "said"}. Ops apply in file order, so the last
    pause/resume in a batch wins. Unknown ops are ignored but logged — a
    silently dropped operator action is worse than a noisy one.
    """
    stop = False
    said = 0
    for op in ops:
        if not isinstance(op, dict):
            log_event({"type": "steer", "op": "?", "ignored": "not an object"})
            continue
        name = str(op.get("op") or "").strip().lower()
        sender = str(op.get("from") or "")
        if name == "say":
            text = str(op.get("text") or "").strip()[:_SAY_MAX_CHARS]
            if not text:
                log_event({"type": "steer", "op": "say", "ignored": "empty text"})
                continue
            messages.append({"role": "user", "content": _STEER_PREFIX + text})
            said += 1
            log_event({"type": "steer", "op": "say", "text": text, "from": sender})
        elif name == "stop":
            stop = True
            paused = False
            log_event({"type": "steer", "op": "stop", "from": sender})
        elif name == "pause":
            paused = True
            log_event({"type": "steer", "op": "pause", "from": sender})
        elif name == "resume":
            paused = False
            log_event({"type": "steer", "op": "resume", "from": sender})
        else:
            log_event({"type": "steer", "op": name or "?", "ignored": "unknown op"})
    return {"stop": stop, "paused": paused, "said": said}


_LOOPBACK_NAMES = ("localhost", "127.0.0.1", "::1")


def _origin_of(url: str) -> tuple[str, str, int] | None:
    """(scheme, host, port) with the default port filled in and loopback
    aliases folded to one name; None for anything that is not http(s)."""
    try:
        u = urllib.parse.urlsplit((url or "").strip())
        scheme = u.scheme.lower()
        host = (u.hostname or "").lower()
        port = u.port or {"http": 80, "https": 443}.get(scheme)
    except ValueError:
        return None
    if scheme not in ("http", "https") or not host or port is None:
        return None
    if host in _LOOPBACK_NAMES:
        host = "localhost"
    return scheme, host, port


def _key_endpoint_trusted(base_url: str) -> bool:
    """True when the ambient key may be sent to `base_url`.

    start_agent(base_url=...) is a model-callable MCP parameter with no host
    allowlist, so an injected prompt can name an arbitrary endpoint. The
    ambient key therefore travels ONLY to an endpoint this install was
    configured for — the runner's local default, OPENBEAST_AGENT_INFERENCE_URL,
    OPENBEAST_INFERENCE_URL or OPENBEAST_CONSUMER_BASE (hydra) — matched by
    ORIGIN (scheme + host + port), so another loopback listener or another
    port/scheme on the worker host gets the keyless sentinel. An explicit
    --api-key is operator intent and always honored.
    """
    origin = _origin_of(base_url)
    if origin is None:
        return False
    trusted = {_origin_of(DEFAULT_BASE_URL)}
    for name in ("OPENBEAST_AGENT_INFERENCE_URL", "OPENBEAST_INFERENCE_URL",
                 "OPENBEAST_CONSUMER_BASE"):
        configured = os.environ.get(name, "").strip()
        if configured:
            trusted.add(_origin_of(configured))
    return origin in trusted


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
    session_id: str | None = None,
    steer: bool = False,
    wall_s: float | None = None,
) -> str:
    """Run the agent loop. Returns the final summary or last model message."""
    _safe_stdio()
    # G23: the wall clock starts with the run (run_eval's kill timer started a
    # moment earlier, at spawn). Notices only with the switch on and a wall.
    wall_t0 = _clock()
    wall = wall_budget_s(wall_s) if wall_warn_enabled() else 0.0
    wall_fired: set = set()

    if workdir:
        workdir = os.path.expanduser(workdir)
        os.environ["AGENT_WORKDIR"] = workdir

    # Build system prompt: explicit override > dynamic build > default
    if system_prompt is None:
        system_prompt = build_system_prompt(context=context, context_budget=context_budget,
                                            max_iter=max_iter)

    # Resolved once per run; {} (send nothing) when set to 0 or unlimited.
    max_tokens = _max_completion_tokens()
    client_kwargs = {}
    if os.environ.get(_EVAL_MARKER):
        # beast-gate leaves an eval request exactly as sent (no thinking
        # default injected): see GATE_DEFAULTS_HEADER.
        client_kwargs["default_headers"] = {GATE_DEFAULTS_HEADER: "off"}
    client = OpenAI(base_url=base_url, api_key=resolve_api_key(api_key, base_url),
                    max_retries=(_EVAL_CLIENT_MAX_RETRIES if os.environ.get(_EVAL_MARKER)
                                 else _CLIENT_MAX_RETRIES),
                    timeout=_client_timeout_s(max_tokens), **client_kwargs)
    cap_kwargs = {"max_tokens": max_tokens} if max_tokens else {}
    # {} unless OPENBEAST_AGENT_THINKING, or (not eval) INFERENCE_THINKING
    think_kwargs = _agent_thinking_kwargs()
    think_kwargs.update(_seed_kwargs())  # {} unless OPENBEAST_AGENT_SEED

    # --- THE EVAL GUARD, resolved FIRST (E1/E2) ---------------------------
    # Resolved once, here, and cached in a local for the whole run: the guard
    # is evaluated exactly one time, cannot be re-decided mid-loop, and — the
    # E2 fix — is known BEFORE the resume rebuild, which replays recorded
    # operator messages and used to run ~26 lines ahead of this line.
    steering = _steering_enabled(steer or bool(session_id))

    # Resume from existing log or start fresh
    if resume_from and os.path.isfile(resume_from):
        messages = _rebuild_messages_from_log(resume_from, system_prompt,
                                              steering=steering)
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
    # Transcripts hold full tool output: dirs we create are 0700, files 0600
    # (the stack's own agents/logs/ is tightened even if it already exists).
    if log_file:
        log_path = log_file
        _tools.private_log_dir(os.path.dirname(log_path) or ".")
    else:
        _tools.private_log_dir(
            log_dir, tighten=os.path.abspath(log_dir) == os.path.abspath(DEFAULT_LOG_DIR))
        # E18: the same shape sessions.new_id() uses. A bare 1-second
        # timestamp collided under `run_eval.py --jobs N` — two units of the
        # same measurement appended to ONE transcript, and the derived
        # session id collided with it.
        stamp = (datetime.now().strftime("%Y%m%d-%H%M%S") + "-"
                 + uuid.uuid4().hex[:8])
        log_path = os.path.join(log_dir, f"agent-{stamp}.jsonl")

    # --- beast-chat (inert unless configured; see _steering_enabled) --------
    ses_id = ""
    steer_cursor = 0
    steer_paused = False
    #: message position -> the iteration at which that operator message
    #: arrived, so compact_messages knows which ones have aged out (E12).
    steer_turn: dict[int, int] = {}
    if steering:
        # Operator messages restored by --resume are already old history:
        # evictable from the first turn if the context needs the room.
        for _i, _m in enumerate(messages):
            if (_m.get("role") == "user"
                    and str(_m.get("content") or "").startswith(_STEER_PREFIX)):
                steer_turn[_i] = 1 - _STEER_STUB_AFTER_TURNS
        ses_id = session_id or _session_id_from_log(log_path) or _sessions.new_id("agent")
        # Carry a previous cursor forward so --resume never replays ops the
        # earlier process already consumed.
        prior = _sessions.get(ses_id) or {}
        prior_meta = prior.get("meta") if isinstance(prior.get("meta"), dict) else {}
        try:
            steer_cursor = max(int(prior_meta.get("cursor") or 0), 0)
        except (TypeError, ValueError):
            steer_cursor = 0
        _sessions.register(
            ses_id, kind="agent", title=task[:200], pid=os.getpid(),
            workdir=workdir, model=model, transcript=log_path,
            meta={"cursor": steer_cursor, "base_url": base_url,
                  "max_iter": max_iter},
        )
        _finalized = {"done": False}

        def _finalize_once(state: str, summary: str | None = None):
            if not _finalized["done"]:
                _finalized["done"] = True
                _sessions.finalize(ses_id, state, summary=summary)

        # An unhandled exception prints its traceback and THEN runs atexit, so
        # a crashed agent lands as `failed` instead of a record stuck on
        # `running`. A SIGKILL skips this — that is exactly what `lost` is for.
        atexit.register(
            _finalize_once, "failed", "process exited without a terminal event")
        _last_touch = [0.0]

    def log_event(event: dict):
        event["timestamp"] = datetime.now().isoformat()
        with _tools.open_private_append(log_path) as f:
            f.write(json.dumps(event) + "\n")
        if steering:
            # Throttled: `updated_at` is a liveness heartbeat for the console,
            # not an audit trail, and the transcript is the real record. Keeps
            # the ledger off the hot path during tool-call bursts.
            now = time.time()
            if now - _last_touch[0] >= 0.5:
                _last_touch[0] = now
                _sessions.touch(ses_id, last_event=event.get("type") or "")

    print(f"Agent started — task: {task[:100]}{'...' if len(task) > 100 else ''}")
    print(f"Server: {base_url}")
    print(f"Log: {log_path}")
    if workdir:
        print(f"Workdir: {workdir}")
    print(f"Max iterations: {max_iter}")
    print("-" * 60)

    log_event({"type": "start", "task": task, "model": model, "workdir": workdir})

    # G22: the environment block rides on the task message, once, before the
    # first model call (a fresh run only: a resumed history starts elsewhere).
    if env_block_enabled() and not (resume_from and os.path.isfile(resume_from)):
        block = env_block(workdir)
        messages[1] = {**messages[1],
                       "content": f"{messages[1].get('content') or ''}\n\n{block}"}
        log_event({"type": "env_block", "chars": len(block), "text": block})

    final_summary = ""
    # Token usage accumulated across every API call. Servers that don't return
    # a `usage` field (e.g. some older llama.cpp builds) leave these at 0.
    tokens_prompt = 0
    tokens_completion = 0
    tokens_total = 0
    # Context-window management state (see compact_messages).
    compactions = 0
    api_errors = 0      # non-overflow model-call failures (API_ERRORS: n)
    stop_reason = ""
    call_seq = 0
    call_index: dict[int, int] = {}   # message position -> tool-call ordinal
    # Repeat guard (runner v2): the v5 audit saw the same `cat` 15x and the
    # same bash command 19x in a row, each with the same output.
    last_call: tuple[str, str] | None = None   # (call key, result)
    repeats = 0
    if not (resume_from and os.path.isfile(resume_from)):
        reset_plan()
    # G9's write/run ledger is per run (a no-op unless VERIFY_DONE is on).
    _tools.reset_verify_done()

    def compact(reason: str, chars_to_free: int, detail: str = "",
                must_free: int | None = None) -> int:
        nonlocal compactions
        # Empty unless steering is on, which is what keeps compaction
        # byte-identical to the pre-beast-chat behaviour under the guard.
        eligible = {i for i, turn in steer_turn.items()
                    if iteration - turn >= _STEER_STUB_AFTER_TURNS}
        n, freed = compact_messages(messages, chars_to_free, call_index,
                                    steer_eligible=eligible,
                                    must_free=must_free)
        if n:
            compactions += 1
            print(f"[compaction] {reason}: stubbed {n} oldest tool result(s), "
                  f"freed {freed:,} chars{detail}", file=sys.stderr)
            log_event({"type": "compaction", "reason": reason, "evicted": n,
                       "chars_freed": freed, "iteration": iteration})
        return n

    for iteration in range(1, max_iter + 1):
        if steering:
            # THE TURN BOUNDARY — the only point at which operator ops apply.
            # A message sent during a 60 s tool call lands here, when the call
            # returns; the console tells the operator so. Reading before the
            # `iteration` event means a stop never leaves a phantom turn in
            # the transcript.
            paused_logged = False
            while True:
                ops, steer_cursor = _sessions.read_new_ops(ses_id, steer_cursor)
                if ops:
                    # Persist the cursor before acting: a crash mid-turn must
                    # not replay an op the model has already been told.
                    _sessions.touch(ses_id, meta={"cursor": steer_cursor})
                    _before = len(messages)
                    act = _apply_steer_ops(ops, messages, log_event, steer_paused)
                    for _i in range(_before, len(messages)):
                        if str(messages[_i].get("content") or "").startswith(
                                _STEER_PREFIX):
                            steer_turn[_i] = iteration
                    steer_paused = act["paused"]
                    if act["stop"]:
                        print("\nStopped by operator.")
                        _print_token_summary(tokens_prompt, tokens_completion,
                                             tokens_total, compactions, api_errors)
                        log_event({
                            # COMPLETED turns, so counter-1 — ops are consumed
                            # at the TOP of iteration N, before that turn runs
                            # and before its `iteration` event is logged, so
                            # N-1 turns have finished. It also matches the last
                            # `iteration` event in the transcript, which is the
                            # previous pass's. (An adversarial review claimed
                            # this was an off-by-one and a spec item propagated
                            # the claim; both were wrong. A stop at the first
                            # boundary takes no turn at all and must record 0.)
                            "type": "done", "summary": "stopped by operator",
                            "iterations": iteration - 1,
                            "tokens_prompt": tokens_prompt,
                            "tokens_completion": tokens_completion,
                            "tokens_total": tokens_total,
                            "compactions": compactions,
                        })
                        _finalize_once("stopped", "stopped by operator")
                        return final_summary or "stopped by operator"
                if not steer_paused:
                    break
                if not paused_logged:
                    paused_logged = True
                    log_event({"type": "paused", "iteration": iteration})
                    print("[paused] waiting for resume or stop", file=sys.stderr)
                time.sleep(_PAUSE_POLL_S)   # bounded: never a spin

        print(f"\n[iter {iteration}/{max_iter}]")
        log_event({"type": "iteration", "number": iteration})

        # The transient block: the plan plus, near the end, the turns left.
        # G23: and, with OPENBEAST_AGENT_WALL_WARN=1, the wall-clock notice.
        wnote = wall_notice(_clock() - wall_t0, wall, wall_fired)
        if wnote:
            log_event({"type": "wall_notice", "iteration": iteration,
                       "elapsed_s": round(_clock() - wall_t0, 1), "wall_s": wall})
        plan = "\n\n".join(b for b in (plan_block(), turns_notice(iteration, max_iter),
                                      wnote)
                           if b)
        if context_budget > 0:
            # Proactive: past ~70% of the declared budget, evict before the
            # server has to tell us (4 chars/token estimate).
            # Once over the trigger, free down to the low-water mark so the
            # rewritten prefix stays stable for many turns (see
            # _COMPACT_LOW_WATER).
            est = estimate_tokens(messages, len(plan))
            asks = proactive_asks(est, context_budget)
            if asks:
                target = int(context_budget * _COMPACT_FRACTION)
                low = int(context_budget * _COMPACT_LOW_WATER)
                compact("budget", asks[1],
                        f" (est {est:,} > {target:,} tokens, to {low:,})",
                        must_free=asks[0])

        try:
            waited = 0.0
            while True:
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=_with_plan(messages, plan),
                        tools=TOOL_SCHEMAS,
                        # OPENBEAST_EVAL_GREEDY=1 (low-churn eval mode,
                        # 2026-09-10): unseeded temperature-0.6 sampling was
                        # the measured ±5-14 task-flip churn floor's primary
                        # engine. Greedy decoding is an EXPERIMENT mode — the
                        # 0.6 default is serving reality and stays for
                        # leaderboard rows.
                        temperature=(0.0 if os.environ.get(
                            "OPENBEAST_EVAL_GREEDY", "") == "1" else 0.6),
                        **cap_kwargs,
                        **think_kwargs,
                    )
                    break
                except Exception as rl:
                    wait = _retry_after_s(rl)
                    if wait is None or waited + wait > _RATE_LIMIT_MAX_WAIT_S:
                        raise
                    waited += wait
                    log_event({"type": "rate_limited", "wait_s": wait})
                    time.sleep(wait)
        except Exception as e:
            if isinstance(e, UnicodeEncodeError) and (
                    _scrub_surrogates(messages) + _scrub_plan_surrogates()):
                # Not an API error: the payload could not be encoded, and an
                # identical retry would fail the same way every 5 s. Both
                # halves of the payload are scrubbed (no short-circuit): the
                # history AND the plan block re-injected from tools.
                log_event({"type": "surrogates_scrubbed"})
                continue
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
            api_errors += 1
            time.sleep(5)
            continue

        usage = getattr(response, "usage", None)
        if usage is not None:
            tokens_prompt += getattr(usage, "prompt_tokens", 0) or 0
            tokens_completion += getattr(usage, "completion_tokens", 0) or 0
            tokens_total += getattr(usage, "total_tokens", 0) or 0

        choice = response.choices[0]
        message = choice.message
        # Hit our own output cap: keep only the head of the runaway text —
        # assistant turns are never compacted, so a 30K-token repetition loop
        # would otherwise ride along on every later request.
        truncated = bool(cap_kwargs) and choice.finish_reason == "length"

        # Append assistant message to history
        content = message.content or ""
        if truncated and len(content) > _TRUNCATED_KEEP_CHARS:
            content = (content[:_TRUNCATED_KEEP_CHARS]
                       + f"\n[... truncated: {len(message.content):,} chars, output cap reached]")
        msg_dict = {"role": "assistant", "content": content}
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
            if truncated:
                print(f"  (reply hit the {max_tokens:,}-token output cap)")
                # Cut off mid-think: the reasoning is all the turn produced.
                reasoning = ("" if (message.content or "").strip()
                             else _reasoning_text(message))
                cap_event = {"type": "output_cap", "iteration": iteration,
                             "max_tokens": max_tokens}
                if usage is not None and getattr(usage, "completion_tokens", None):
                    cap_event["completion_tokens"] = usage.completion_tokens
                if reasoning:
                    tail = _reasoning_tail(reasoning)
                    cap_event.update(reasoning_chars=len(reasoning),
                                     reasoning_tail_chars=len(tail))
                    nudge = _capped_think_nudge(max_tokens, tail)
                else:
                    nudge = (f"Your last reply hit the {max_tokens:,}-token output cap "
                             "and was cut off. Do not repeat it: be concise, and make "
                             "progress with a tool call or call task_done.")
                log_event(cap_event)
                messages.append({"role": "user", "content": nudge})
            elif choice.finish_reason == "stop":
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
            call_ok = False  # the handler ran and returned (no error turn)
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
                    result = handler(**coerce_args(fn_name, fn_args))
                    call_ok = True
                except Exception as e:
                    result = f"Error: bad tool call to {fn_name}: {e}"

            # task_done is exempt: a refused one is answered by its own text.
            if fn_name != "task_done":
                key = (_call_key(fn_name, fn_args if not parse_err
                                 else tc.function.arguments), result)
                repeats = repeats + 1 if key == last_call else 1
                last_call = key
                if repeats >= _REPEAT_AFTER:
                    result += _repeat_nudge(repeats)
                    log_event({"type": "repeat_guard", "name": fn_name,
                               "repeats": repeats, "iteration": iteration})
            else:
                last_call, repeats = None, 0

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

            # Check for task completion. Only an ACCEPTED task_done ends the
            # run: a bad call (invalid JSON, non-object args, missing summary)
            # and the R1 path guard's "NOT DONE — ..." refusal are tool turns
            # the model must get to act on.
            if (fn_name == "task_done" and call_ok
                    and str(result).startswith("TASK_DONE")):
                final_summary = fn_args.get("summary")
                if not isinstance(final_summary, str):
                    # {"summary": null|5|{...}}: callers slice the summary.
                    final_summary = (str(result) if final_summary is None
                                     else str(final_summary))
                print(f"\n{'=' * 60}")
                print(f"Task complete (iteration {iteration})")
                print(f"Summary: {final_summary}")
                print(f"Log: {log_path}")
                _print_token_summary(tokens_prompt, tokens_completion, tokens_total,
                                     compactions, api_errors)
                print(f"{'=' * 60}")
                log_event({
                    "type": "done", "summary": final_summary, "iterations": iteration,
                    "tokens_prompt": tokens_prompt, "tokens_completion": tokens_completion,
                    "tokens_total": tokens_total, "compactions": compactions,
                })
                if steering:
                    _finalize_once("done", final_summary)
                return final_summary

    # Max iterations reached (or an unrecoverable stop)
    if stop_reason:
        print(f"\nStopped without task_done: {stop_reason}.")
    else:
        print(f"\nMax iterations ({max_iter}) reached without task_done.")
    _print_token_summary(tokens_prompt, tokens_completion, tokens_total, compactions,
                         api_errors)
    log_event({
        "type": "max_iterations", "iterations": max_iter,
        "tokens_prompt": tokens_prompt, "tokens_completion": tokens_completion,
        "tokens_total": tokens_total, "compactions": compactions,
    })
    if steering:
        # The process exited cleanly, so this is `done`, not `failed`; the
        # summary is what tells the console it ran out of road.
        _finalize_once("done", final_summary or (
            stop_reason or f"max iterations ({max_iter}) reached without task_done"))
    return final_summary or "(max iterations reached)"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

#: Seconds a tool command gets to exit on SIGTERM before its group is
#: SIGKILLed, when the runner itself is told to stop.
_STOP_GRACE_S = 2.0


def _on_stop_signal(signum, frame):
    """Kill the in-flight tool command(s), then die the way the signal says.

    Every bash-tool command runs in its OWN session (tools.run_reaped), so
    `job.sh stop`, MCP stop_agent and the console's escalation — which all
    signal the runner or its group — never reached it, and once the runner
    was dead nothing enforced its timeout: the command ran on as an orphan
    (review chat-sessions-orphaned-tool-children-on-stop). SIGTERM/SIGHUP
    then re-raise with the default action, so the exit status (-15/-1) and
    the skipped atexit hooks are exactly what they were; SIGINT keeps its
    KeyboardInterrupt."""
    try:
        _tools.kill_live_children(_STOP_GRACE_S)
    finally:
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)


def install_stop_handlers() -> None:
    """Hook the stop signals — but never one the launcher chose to IGNORE.

    `nohup ./agent.sh ... &` ignores SIGHUP and a backgrounded `cmd &` in a
    non-interactive shell ignores SIGINT; that inherited SIG_IGN is how the
    run survives a closed terminal. Overriding it turned a hangup into a
    dead agent (rc 129) — Python itself keeps an inherited SIG_IGN too."""
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        if signal.getsignal(sig) is signal.SIG_IGN:
            continue
        signal.signal(sig, _on_stop_signal)


def main():
    # This process is $PPID of every model-authored shell and holds the API
    # key (and whatever the launcher exported) in its environ: non-dumpable
    # before the first model call, not lazily at the first tool spawn.
    _tools.harden_process()
    install_stop_handlers()
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
    parser.add_argument("--session-id", help="beast-chat: pin the session-ledger id (default: derived from the log filename, else generated). Implies --steer.")
    parser.add_argument("--steer", action="store_true", help="beast-chat: register in the session ledger and read the steering inbox at each turn boundary. OFF unless this flag or --session-id is passed (there is no environment opt-in); ALWAYS off under run_eval.py (OPENBEAST_EVAL / OPENBEAST_TASK_PATHS).")
    parser.add_argument("--wall-s", type=float, default=0, help="The unit's wall budget in seconds (default: OPENBEAST_AGENT_WALL_S, which run_eval sets). Used only with OPENBEAST_AGENT_WALL_WARN=1: one transient notice at the first turn past 50%% and past 80%% of it")
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
        session_id=args.session_id,
        steer=args.steer,
        wall_s=args.wall_s,
    )


if __name__ == "__main__":
    main()
