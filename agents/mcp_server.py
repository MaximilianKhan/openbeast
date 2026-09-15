#!/usr/bin/env python3
"""
MCP server exposing local tools to any MCP-compatible client
(OpenCode, Open WebUI, etc).

Tools:
  - bash: run shell commands
  - read_file: read file contents with line numbers
  - write_file: create/overwrite files
  - edit_file: targeted string replacement in files
  - list_files: glob-based file discovery
  - grep: regex search across files
  - fetch: retrieve content from URLs (HTML → text, JSON, plain text)
  - web_search: search the web via local SearXNG instance

Agent management (long-running autonomous agents; backed by the durable
session ledger in agents/sessions.py, so agents outlive this process):
  - start_agent: spawn a background agent with optional context briefing
    (base_url routes its inference to a worker box for multi-box setups)
  - check_agent: monitor progress, view recent activity and results
  - tail_agent: raw log tail for detailed debugging
  - list_agents: see all tracked agents and their status
  - stop_agent: terminate a running agent

Skills (progressive disclosure):
  - skill: skill() returns the index of every skill; skill(name) loads one
  - start_skill_agent: spawn a background agent with a skill activated

Transports:
  stdio:           opencode local MCP (default)
  streamable-http: Open WebUI and remote clients (--transport http --port 3001)

Usage:
  python mcp_server.py                              # stdio (for opencode)
  python mcp_server.py --transport http --port 3001  # HTTP (for Open WebUI)
"""

import argparse
import atexit
import difflib
import json
import os
import signal
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime

from mcp.server.mcpserver import MCPServer

# Shared tool implementations (single source of truth, incl. process-group
# reaping, rlimits, output capping, and protected-path write guards).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tools as _tools

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

# HTTP-transport bind address. This server has NO authentication — it
# exposes bash/file/agent tools to whoever can reach the port — so it
# follows the stack-wide bind resolver (OPENBEAST_BIND, default loopback)
# instead of the old hardcoded 0.0.0.0. doctor.sh can only inspect the env
# var, not a hardcoded literal, so the safe value must be the default here.
# mcp 2.x: transport params (host/port) moved from the constructor to run().
# The bind stays resolved at module level so the doctrine above (env-var
# resolver, loopback default, doctor.sh-inspectable) is unchanged.
MCP_BIND = os.environ.get("OPENBEAST_BIND", "127.0.0.1").strip() or "127.0.0.1"
mcp = MCPServer("local-tools")

# RBAC Phase 2 (docs/RBAC_PLAN.md): OPENBEAST_MCP_TOOLS, when set, is a
# comma-separated allowlist — only the named tools REGISTER at all. The guest
# MCPO instance runs with OPENBEAST_MCP_TOOLS="web_search,fetch", so even a
# caller holding the guest API key cannot reach bash/file/agent tools: they
# don't exist on that server. Unset (the default) = every tool registers.
_ALLOWED_TOOLS = {
    t.strip() for t in os.environ.get("OPENBEAST_MCP_TOOLS", "").split(",") if t.strip()
}


def _tool(*args, **kwargs):
    """mcp.tool() that honors the OPENBEAST_MCP_TOOLS allowlist."""
    def decorate(fn):
        if _ALLOWED_TOOLS and fn.__name__ not in _ALLOWED_TOOLS:
            return fn  # not registered — invisible to this instance
        return mcp.tool(*args, **kwargs)(fn)
    return decorate

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@_tool()
def bash(command: str, timeout: int = 120) -> str:
    """Run a shell command (/bin/sh) and return its output. Use for building,
    testing, git operations, installing packages, or any system task.

    Facts: stdout and stderr are MERGED; each call is a FRESH shell — cd,
    exported variables and functions do not carry over (chain steps with &&
    or cd inside the same command); output over 50 KB keeps head and tail;
    a nonzero exit appends '(exit code N)'; background processes (servers,
    trailing &, nohup) are killed when the command returns, so start and
    test a server in the same command.

    Args:
        command: The shell command to execute.
        timeout: Seconds before the whole process group is killed (default 120).
    """
    return _tools.bash(command, timeout)


@_tool()
def read_file(path: str, offset: int = 1, limit: int = 500) -> str:
    """Read lines from a file as 'N<TAB>text' with 1-based line numbers — the
    same numbering grep reports, so a grep line number can be passed straight
    to offset. Output is capped per call and ends with a resume hint.

    Args:
        path: Path to the file.
        offset: First line to return, 1-based (default 1 = top of file).
        limit: Max lines to return (default 500).
    """
    return _tools.read_file(path, offset, limit)


@_tool()
def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates directories if needed. Overwrites existing files."""
    return _tools.write_file(path, content)


@_tool()
def list_files(directory: str = ".", pattern: str = "**/*") -> str:
    """List files matching a glob pattern in a directory."""
    return _tools.list_files(directory, pattern)


@_tool()
def grep(pattern: str, path: str = ".", file_glob: str = "") -> str:
    """Search file contents for a regex pattern. Returns matching lines with
    file paths and line numbers."""
    return _tools.grep(pattern, path, file_glob)


@_tool()
def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Replace an exact string in a file with new content.

    Use this instead of write_file when modifying an existing file — it's safer
    and more precise than rewriting the entire file. The old_string must appear
    exactly once in the file unless replace_all is True.

    To insert text at a location, include surrounding context in old_string and
    add the new text within that context in new_string.

    Args:
        path: Path to the file to edit.
        old_string: The exact text to find (must be unique in the file).
        new_string: The replacement text.
        replace_all: Replace all occurrences instead of requiring uniqueness.
    """
    return _tools.edit_file(path, old_string, new_string, replace_all)


@_tool()
def fetch(url: str, max_length: int = 50_000) -> str:
    """Fetch content from a PUBLIC URL and return it as text.

    For HTML pages, scripts and styles are removed and tags are stripped to
    return readable text. For JSON, plain text, and other formats, content is
    returned as-is. Blocked by the SSRF guard: localhost/127.0.0.1, private
    LAN (10.x, 192.168.x, 172.16-31.x), link-local and tailnet (100.64-127.x)
    addresses — to reach a local server such as http://localhost:8080 use
    bash with curl instead.

    Args:
        url: The URL to fetch (http or https, public hosts only).
        max_length: Maximum characters to return (default 50000).
    """
    return _tools.fetch(url, max_length)


# ---------------------------------------------------------------------------
# Agent management — long-running autonomous agents
# ---------------------------------------------------------------------------

_RUNNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner.py")
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# Ceiling on how many ledger records list_agents will render. Each row costs
# one transcript parse, so this bounds the tool's worst case on a rig with a
# long history rather than letting it grow with the ledger.
_LEDGER_LIST_LIMIT = 100

# Must match runner.py's DEFAULT_BASE_URL — when the resolved endpoint equals
# this, we omit --base-url so local spawns stay byte-identical to before the
# distributed-agents feature existed.
_DEFAULT_AGENT_BASE_URL = "http://localhost:8080/v1"

# --- Session ledger (agents/sessions.py) — read-only, lazy, fail-soft ------
#
# The ledger is the DURABLE index of every agent and job session on this rig
# (.run/sessions/<id>.json, docs/BEAST_CHAT.md). It exists because `_agents`
# below is in-memory: restart this server and every live agent vanished from
# `list_agents` even though its process was still running.
#
# Three rules this module holds itself to:
#   1. READ ONLY. The record is written by the session itself (runner.py for
#      agents, scripts/job.sh for jobs), never by the spawner — that is what
#      makes non-MCP spawn paths (agent.sh, the client CLI) appear too. Two
#      writers for one id would race, so this one does not write.
#   2. LAZY. Imported on first use, not at module import, so `import
#      mcp_server` keeps working on a checkout where the ledger is absent.
#   3. FAIL SOFT. Every helper here returns an empty/None result instead of
#      raising. With no ledger, every tool behaves exactly as it did before
#      the ledger existed — the old in-memory paths are still all there.
#
# NOT an excuse to scan agents/logs/: that directory holds thousands of
# historical transcripts and listing it is a filesystem walk on every call.
# The ledger is the index; transcripts are opened by path, one at a time.

_SESSIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.py")
_sessions_mod = None


def _sessions():
    """The sessions module, or None when the ledger is unavailable."""
    global _sessions_mod
    if _sessions_mod is not None:
        return _sessions_mod
    # Stat before importing: cheap, and it lets a rig that gains the module
    # later pick it up without a restart (an ImportError is not cached here).
    if not os.path.isfile(_SESSIONS_PATH):
        return None
    try:
        import sessions as _mod  # noqa: PLC0415 — deliberately lazy
    except Exception:
        return None
    _sessions_mod = _mod
    return _mod


def _ledger_reconcile(record: dict) -> dict:
    """reconcile() a record (running → lost when its pid is gone), best effort."""
    mod = _sessions()
    if mod is None or not isinstance(record, dict):
        return record if isinstance(record, dict) else {}
    try:
        out = mod.reconcile(record)
    except Exception:
        return record
    return out if isinstance(out, dict) else record


def _ledger_get(session_id: str) -> dict | None:
    """One reconciled ledger record, or None."""
    mod = _sessions()
    if mod is None:
        return None
    try:
        record = mod.get(session_id)
    except Exception:
        return None
    if not isinstance(record, dict):
        return None
    return _ledger_reconcile(record)


def _ledger_agents(limit: int = _LEDGER_LIST_LIMIT) -> list[dict]:
    """Reconciled agent-kind ledger records, newest first, bounded."""
    mod = _sessions()
    if mod is None:
        return []
    try:
        records = mod.list_sessions(kind="agent", limit=limit)
    except Exception:
        return []
    if not isinstance(records, list):
        return []
    return [_ledger_reconcile(r) for r in records if isinstance(r, dict)]


def _ledger_transcript(record: dict, session_id: str) -> str:
    """Transcript path for a ledger record, falling back to the log-dir
    convention so a record written before `transcript` was set still tails."""
    path = record.get("transcript") if isinstance(record, dict) else None
    if isinstance(path, str) and path:
        return path
    return os.path.join(_LOG_DIR, f"agent-{session_id}.jsonl")


def _resolve_agent_base_url(explicit: str = "") -> str:
    """Inference endpoint for a spawned agent: explicit arg →
    OPENBEAST_AGENT_INFERENCE_URL env (set by scripts/lib/conf.sh from
    openbeast.conf) → the runner's own local default."""
    return (
        explicit.strip()
        or os.environ.get("OPENBEAST_AGENT_INFERENCE_URL", "").strip()
        or _DEFAULT_AGENT_BASE_URL
    )


def _build_runner_cmd(task: str, log_path: str, max_iter: int, workdir: str,
                      context_budget: int, context: str = "",
                      base_url: str = "") -> list[str]:
    """Argv for a runner.py spawn. --base-url is appended only when the
    resolved URL differs from the runner's default (distributed agents:
    tokens come from a worker box, execution stays on this machine)."""
    cmd = [
        sys.executable, _RUNNER_PATH,
        "--log-file", log_path,
        "--max-iter", str(max_iter),
        "--workdir", workdir,
        "--context-budget", str(context_budget),
    ]
    if base_url and base_url != _DEFAULT_AGENT_BASE_URL:
        cmd.extend(["--base-url", base_url])
    if context:
        cmd.extend(["--context", context])
    cmd.append(task)
    return cmd


@dataclass
class _AgentRecord:
    """Tracks a spawned agent subprocess."""
    agent_id: str
    task: str
    pid: int
    process: subprocess.Popen
    log_path: str
    workdir: str
    max_iter: int
    started_at: datetime
    base_url: str = _DEFAULT_AGENT_BASE_URL
    detach: bool = False


# In-memory registry of agents spawned during this server session.
_agents: dict[str, _AgentRecord] = {}


def _cleanup_agents():
    """Terminate all running agents on server shutdown.

    DETACHED agents are deliberately spared. Tying an agent's life to the tool
    server's meant a routine restart of :3001 SIGTERMed every long-running
    agent on the box — fatal for beast-chat, whose whole premise is that a
    session outlives the process that happened to spawn it. Non-detached
    spawns keep the old contract exactly: close the server, they die.
    """
    for record in list(_agents.values()):
        if record.detach:
            continue
        if record.process.poll() is None:
            try:
                os.killpg(os.getpgid(record.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass


atexit.register(_cleanup_agents)


def _parse_agent_log(log_path: str, max_bytes: int = 10_000_000) -> list[dict]:
    """Read and parse a JSONL agent log file. Caps read at max_bytes."""
    events = []
    try:
        size = os.path.getsize(log_path)
        with open(log_path, "r") as f:
            # For very large logs, seek to the tail for recent events.
            # Always try to read the first line (start event) separately.
            if size > max_bytes:
                # Read the first line for the start event
                first_line = f.readline().strip()
                if first_line:
                    try:
                        events.append(json.loads(first_line))
                    except json.JSONDecodeError:
                        pass
                # Seek to tail for recent events
                f.seek(max(0, size - max_bytes))
                f.readline()  # skip partial line after seek
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        pass
    return events


def _format_elapsed(start: datetime) -> str:
    """Human-readable elapsed time."""
    seconds = int((datetime.now() - start).total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m {secs}s"


def _classify_agent_status(
    alive: bool, events: list[dict], returncode: int | None = None,
    orphaned: bool = False,
) -> str:
    """Determine agent status string from liveness and log events.

    Shared by _agent_status_report, _orphaned_log_report, and list_agents
    to avoid duplicating the classification logic.

    `orphaned` marks records we no longer track (server restarted): without a
    done/max_iterations event their fate is genuinely unknown — they may have
    been SIGKILLed mid-run — so we say so rather than claiming a clean "exited".
    """
    has_done = any(e.get("type") == "done" for e in events)
    has_max_iter = any(e.get("type") == "max_iterations" for e in events)
    if has_done:
        return "completed"
    if has_max_iter:
        return "max_iterations_reached"
    if orphaned:
        return "unknown (server restarted)"
    if not alive:
        return f"exited (code {returncode})" if returncode is not None else "exited"
    return "running"


def _ledger_status(record: dict, events: list[dict]) -> str:
    """Status string for a ledger record with no live process behind it.

    A terminal TRANSCRIPT event still wins — _classify_agent_status stays the
    single source of "completed" / "max_iterations_reached", and a server
    restart after the fact does not make a finished agent's outcome unknown.
    The ledger only answers for records whose transcript never got a terminal
    event, which is precisely the case the in-memory map could never describe:
    the agent crashed, or was SIGKILLed, and nothing wrote a `done` line.
    """
    state = str(record.get("state") or "").strip().lower()
    if state == "running":
        return _classify_agent_status(alive=True, events=events)
    classified = _classify_agent_status(alive=False, events=events, orphaned=True)
    if classified != "unknown (server restarted)":
        return classified
    if state == "lost":
        return "lost (process gone)"
    if state in ("failed", "stopped"):
        return state
    if state == "done":
        return "completed"
    return classified


def _parse_ts(value) -> datetime | None:
    """ISO-8601 (with or without a trailing Z) → datetime, else None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.rstrip("Z"))
    except ValueError:
        return None


def _ledger_task(record: dict, events: list[dict]) -> str:
    """Best available description of what a ledger session is doing."""
    title = record.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for kind in ("start", "spawn"):
        event = next((e for e in events if e.get("type") == kind), None)
        if event and event.get("task"):
            return str(event["task"])
    return "(no title)"


def _agent_status_report(record: _AgentRecord) -> str:
    """Build a structured status report for a tracked agent."""
    alive = record.process.poll() is None
    events = _parse_agent_log(record.log_path)
    status = _classify_agent_status(alive, events, record.process.returncode)

    done_event = next((e for e in events if e.get("type") == "done"), None)
    error_events = [e for e in events if e.get("type") == "error"]
    iteration_events = [e for e in events if e.get("type") == "iteration"]
    tool_events = [e for e in events if e.get("type") == "tool_call"]
    assistant_events = [e for e in events if e.get("type") == "assistant"]

    current_iter = len(iteration_events)
    elapsed = _format_elapsed(record.started_at)

    base_url = record.base_url or _DEFAULT_AGENT_BASE_URL
    inference = base_url + (
        " (REMOTE worker)" if base_url != _DEFAULT_AGENT_BASE_URL else " (local)")

    lines = [
        f"Agent: {record.agent_id}",
        f"Status: {status}",
        f"Task: {record.task[:300]}",
        f"Workdir: {record.workdir}",
        f"Inference: {inference}",
        f"Iteration: {current_iter}/{record.max_iter}",
        f"Runtime: {elapsed}",
        f"PID: {record.pid}",
    ]

    # Final summary
    if done_event:
        lines.append(f"\nSummary: {done_event.get('summary', '(none)')}")

    # Last model reasoning (helps caller understand agent's current thinking)
    if assistant_events:
        last_thought = assistant_events[-1].get("content", "")
        if last_thought:
            lines.append(f"\nLast model output:\n  {last_thought[:500]}")

    # Recent tool calls (last 15)
    if tool_events:
        recent = tool_events[-15:]
        lines.append(f"\nRecent activity ({len(tool_events)} total tool calls):")
        for e in recent:
            name = e.get("name", "?")
            args = e.get("args", {})
            if name == "bash":
                detail = args.get("command", "")[:100]
            elif name in ("read_file", "write_file"):
                detail = args.get("path", "")
            elif name == "grep":
                detail = f"'{args.get('pattern', '')}' in {args.get('path', '.')}"
            elif name == "list_files":
                detail = f"{args.get('directory', '.')} [{args.get('pattern', '*')}]"
            elif name == "task_done":
                detail = args.get("summary", "")[:100]
            elif name == "update_plan":
                detail = f"{len(args.get('steps') or [])} steps"
            else:
                detail = str(args)[:100]
            lines.append(f"  [{name}] {detail}")

    # Errors
    if error_events:
        lines.append(f"\nErrors ({len(error_events)}):")
        for e in error_events[-5:]:
            lines.append(f"  {e.get('error', '?')[:200]}")

    return "\n".join(lines)


def _orphaned_log_report(agent_id: str, log_path: str) -> str:
    """Report for an agent from a previous server session (no live process)."""
    events = _parse_agent_log(log_path)
    if not events:
        return f"Agent {agent_id}: log file exists but is empty.\nLog: {log_path}"

    start_event = next((e for e in events if e.get("type") == "start"), None)
    spawn_event = next((e for e in events if e.get("type") == "spawn"), None)
    done_event = next((e for e in events if e.get("type") == "done"), None)
    iteration_events = [e for e in events if e.get("type") == "iteration"]

    task = "?"
    for source in (start_event, spawn_event):
        if source and source.get("task"):
            task = source["task"][:300]
            break
    status = _classify_agent_status(alive=False, events=events, orphaned=True)

    lines = [
        f"Agent: {agent_id} (from previous session — no process control)",
        f"Task: {task}",
        f"Status: {status}",
        f"Iterations: {len(iteration_events)}",
    ]

    # Spawn metadata survives server restarts in the log itself — surface a
    # remote inference endpoint so distributed agents stay visibly remote.
    if spawn_event and spawn_event.get("base_url"):
        bu = spawn_event["base_url"]
        lines.append(f"Inference: {bu}" + (
            " (REMOTE worker)" if bu != _DEFAULT_AGENT_BASE_URL else " (local)"))

    if done_event:
        lines.append(f"Summary: {done_event.get('summary', '(none)')}")

    lines.append(f"Log: {log_path}")
    return "\n".join(lines)


def _ledger_report(record: dict) -> str:
    """Report for an agent known only through the session ledger.

    Richer than _orphaned_log_report because the ledger carries what the
    transcript never did — pid/pgid, workdir, and an authoritative terminal
    state written by the session itself, so a crashed agent reads as crashed
    instead of "unknown".
    """
    session_id = str(record.get("id") or "?")
    log_path = _ledger_transcript(record, session_id)
    events = _parse_agent_log(log_path)
    status = _ledger_status(record, events)

    iteration_events = [e for e in events if e.get("type") == "iteration"]
    tool_events = [e for e in events if e.get("type") == "tool_call"]
    error_events = [e for e in events if e.get("type") == "error"]
    done_event = next((e for e in events if e.get("type") == "done"), None)
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}

    lines = [
        f"Agent: {session_id} (from the session ledger — this server did not spawn it)",
        f"Status: {status}",
        f"Task: {_ledger_task(record, events)[:300]}",
    ]
    if record.get("workdir"):
        lines.append(f"Workdir: {record['workdir']}")
    base_url = meta.get("base_url") or record.get("model")
    if base_url:
        lines.append(f"Inference: {base_url}" + (
            " (REMOTE worker)" if base_url != _DEFAULT_AGENT_BASE_URL else " (local)"))
    max_iter = meta.get("max_iter")
    lines.append(f"Iteration: {len(iteration_events)}"
                 + (f"/{max_iter}" if max_iter else ""))
    started = _parse_ts(record.get("started_at"))
    if started:
        lines.append(f"Runtime: {_format_elapsed(started)}")
    if record.get("pid"):
        lines.append(f"PID: {record['pid']}"
                     + (f" (pgid {record['pgid']})" if record.get("pgid") else ""))

    summary = (done_event or {}).get("summary") or record.get("summary")
    if summary:
        lines.append(f"\nSummary: {summary}")
    if tool_events:
        lines.append(f"\nRecent activity ({len(tool_events)} total tool calls):")
        for event in tool_events[-10:]:
            lines.append(f"  [{event.get('name', '?')}] {str(event.get('args', {}))[:100]}")
    if error_events:
        lines.append(f"\nErrors ({len(error_events)}):")
        for event in error_events[-5:]:
            lines.append(f"  {str(event.get('error', '?'))[:200]}")
    lines.append(f"\nLog: {log_path}")
    return "\n".join(lines)


def tail_transcript(log_path: str, from_offset: int = 0,
                    max_bytes: int = 50_000) -> dict:
    """Byte-offset read of a transcript: {content, offset, size, reset}.

    The contract a poller needs and the last-N-lines view cannot give: pass
    back the `offset` you were handed and you get only what was appended
    since, never the same bytes twice. Cut from the HEAD of the new region
    (not the tail, the way the legacy view caps) so `offset` advances without
    skipping anything, and trimmed to the last complete line so JSONL
    consumers never see half an event.

    `start` is where the read actually began (it differs from `from_offset`
    only on a reset). `reset` is True when the file is now SHORTER than the
    offset asked for —
    a rotated or rewritten transcript — and the read restarted at 0 rather
    than returning nothing forever.
    """
    try:
        size = os.path.getsize(log_path)
    except OSError:
        return {"content": "", "start": 0, "offset": 0, "size": 0, "reset": False}

    start = max(0, int(from_offset))
    reset = False
    if start > size:
        start, reset = 0, True
    if start == size:
        return {"content": "", "start": start, "offset": size, "size": size,
                "reset": reset}

    try:
        with open(log_path, "rb") as fh:
            fh.seek(start)
            chunk = fh.read(max_bytes)
    except OSError as exc:
        return {"content": f"Error reading log: {exc}", "start": start,
                "offset": start, "size": size, "reset": reset}

    # Trim to the last newline so the caller always gets whole events. A single
    # line longer than the cap would otherwise never advance the offset, so in
    # that one case emit the partial chunk and move on.
    cut = chunk.rfind(b"\n")
    if cut != -1:
        chunk = chunk[:cut + 1]
    return {
        "content": chunk.decode("utf-8", errors="replace"),
        "start": start,
        "offset": start + len(chunk),
        "size": size,
        "reset": reset,
    }


# ---------------------------------------------------------------------------
# Skills — discovery + load (Pattern A: progressive disclosure via MCP)
# ---------------------------------------------------------------------------

_REPO_SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills")
_GLOBAL_SKILLS_DIR = os.path.expanduser("~/.local/share/local-llm-skills")

# {name: {"description": str, "frontmatter": dict, "body": str, "path": str, "source": "repo"|"global"}}
_SKILLS_CACHE: dict | None = None


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML-ish frontmatter parser. Returns ({}, full_text) if no
    frontmatter is present. Supports `key: value` and `key: [a, b, c]` only —
    no nesting, no quoting tricks. Keep skill frontmatter simple."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip()
    body = text[end + 3:].lstrip("\n")
    fm: dict = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        elif value.lower() in ("true", "false"):
            value = (value.lower() == "true")
        fm[key] = value
    return fm, body


def _discover_skills(force: bool = False) -> dict:
    """Walk repo and global skill directories. Repo wins on name collision.
    Cached after first call; pass force=True to re-scan."""
    global _SKILLS_CACHE
    if _SKILLS_CACHE is not None and not force:
        return _SKILLS_CACHE

    skills: dict = {}
    # Order matters: repo first so it wins ties
    for source, base in (("repo", _REPO_SKILLS_DIR), ("global", _GLOBAL_SKILLS_DIR)):
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            skill_path = os.path.join(base, entry)
            md_path = os.path.join(skill_path, "SKILL.md")
            if not os.path.isfile(md_path):
                continue
            try:
                text = open(md_path).read()
            except Exception:
                continue
            fm, body = _parse_frontmatter(text)
            name = fm.get("name") or entry
            if name in skills:
                continue  # repo already claimed this name
            skills[name] = {
                "description": fm.get("description", "(no description)"),
                "frontmatter": fm,
                "body": body,
                "path": md_path,
                "source": source,
            }
    _SKILLS_CACHE = skills
    return skills


def _resolve_skill(name: str) -> dict | None:
    """Return the cached skill record, or None if not found."""
    return _discover_skills().get(name)


@_tool()
def skill(name: str = "") -> str:
    """Skill library — browse the index, or load one skill's full instructions.

    A skill is a curated package of instructions for a specific kind of work
    (code review, security audit, eval-task authoring, deep counsel, etc).
    Call with NO name to get the index (name + description for every skill);
    call with a name to get that skill's full body, then apply those
    instructions to the work at hand. This keeps the system prompt small and
    only pays for the content you need.

    Index calls re-scan the skills directories, so a newly added or edited
    SKILL.md shows up without restarting the server.

    Args:
        name: Skill identifier from the index (e.g. 'code-review').
              Empty (the default) returns the index.

    Returns:
        The skill index, or one skill's instructional content (frontmatter
        stripped).
    """
    name = name.strip()
    if not name:
        skills = _discover_skills(force=True)
        if not skills:
            return (
                "No skills installed. Repo skills go in skills/ at the repo root; "
                "global skills go in ~/.local/share/local-llm-skills/. "
                "Each skill is a folder with a SKILL.md file."
            )
        lines = [f"{len(skills)} skill(s) available:", ""]
        for skill_name in sorted(skills):
            s = skills[skill_name]
            src_tag = f"[{s['source']}]"
            lines.append(f"  {skill_name:30s} {src_tag:>9}  {s['description']}")
        lines.append("")
        lines.append("Call skill(name) to read the full skill body.")
        lines.append("Call start_skill_agent(skill, task) to spawn a sub-agent with the skill activated.")
        return "\n".join(lines)

    record = _resolve_skill(name)
    if record is None:
        # The cache may predate a newly added skill — re-scan once before failing.
        record = _discover_skills(force=True).get(name)
    if record is None:
        available = sorted(_discover_skills().keys())
        close = difflib.get_close_matches(name, available, n=3, cutoff=0.5)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        listing = ", ".join(available) or "(none)"
        return f"Error: skill '{name}' not found.{hint} Available: {listing}"
    header = f"=== SKILL: {name} ({record['source']}) ===\n\n"
    return header + record["body"]


# --- Agent MCP tools ---


@_tool()
def start_agent(task: str, workdir: str = ".", max_iter: int = 200, context: str = "",
                base_url: str = "", detach: bool = False) -> str:
    """Delegate a task to a background agent that runs it autonomously and in parallel.

    MANDATORY USAGE: if the user asks to 'spawn', 'launch', 'kick off', or 'start'
    an agent, to run something 'in the background', to work 'while we keep talking',
    or to handle a large multi-step subtask (add tests across a module, refactor a
    whole layer, port a module, audit a repo, run a migration), you MUST call this
    tool and MUST NOT do the work yourself with read_file/grep/bash/edit_file. For
    those requests, calling any other tool is wrong — hand the ENTIRE task to this
    agent via the `task` argument and return its ID.

    The agent loops independently until the task is done or max_iter is reached; it
    does NOT block the current conversation. Use check_agent() to monitor progress.

    Args:
        task: What the agent should accomplish. Be specific and detailed.
        workdir: Working directory for file and shell operations.
        max_iter: Maximum iterations before the agent stops (default 200).
        context: Background context to brief the agent (what you know, what you've
                 tried, relevant files). Helps the agent work more effectively.
        base_url: OpenAI-compatible endpoint the agent's MODEL calls go to.
                  Advanced multi-box setups only: a worker machine on your
                  tailnet serves the tokens while the agent still executes
                  files/shell on THIS machine. Leave empty (default) for the
                  local model — empty falls back to the configured
                  OPENBEAST_AGENT_INFERENCE_URL, else http://localhost:8080/v1.
        detach: Keep the agent alive if THIS tool server restarts. Default
                false preserves the historical contract (shutting the server
                down SIGTERMs every agent it spawned). Pass true for work that
                should outlive a service restart — the beast-chat console
                starts agents this way (docs/BEAST_CHAT.md). Either way the
                agent already runs in its own process group, so stop_agent()
                still terminates it cleanly.

    Returns:
        Agent ID for use with check_agent, list_agents, and stop_agent.
    """
    if not os.path.isfile(_RUNNER_PATH):
        return f"Error: agent runner not found at {_RUNNER_PATH}"

    workdir = os.path.abspath(os.path.expanduser(workdir))
    if not os.path.isdir(workdir):
        return f"Error: workdir does not exist: {workdir}"

    agent_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    log_path = os.path.join(_LOG_DIR, f"agent-{agent_id}.jsonl")
    os.makedirs(_LOG_DIR, exist_ok=True)

    # A deliberately conservative context budget advertised to the spawned
    # agent so it self-manages. It is NOT a per-slot capacity: under
    # --kv-unified (our default) every slot advertises the FULL -c while all
    # slots share one pool, so "total / slots" is meaningless — see
    # docs/BEAST_SLOT.md. Kept fixed rather than read from the live server
    # because a spawn must not depend on a round-trip that may be rate-limited
    # or gated; if you raise it, raise it against the SMALLEST context any
    # shipped serve script uses, not the largest.
    context_budget = 85_000

    resolved_base_url = _resolve_agent_base_url(base_url)
    remote = resolved_base_url != _DEFAULT_AGENT_BASE_URL

    cmd = _build_runner_cmd(
        task=task, log_path=log_path, max_iter=max_iter, workdir=workdir,
        context_budget=context_budget, context=context,
        base_url=resolved_base_url,
    )

    # Record the spawn (incl. the inference endpoint) as the log's first
    # event — runner.py appends its own "start" event right after. This keeps
    # base_url visible in check_agent even across an MCP server restart.
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps({
                "type": "spawn",
                "agent_id": agent_id,
                "task": task,
                "workdir": workdir,
                "base_url": resolved_base_url,
                "timestamp": datetime.now().isoformat(),
            }) + "\n")
    except OSError:
        pass  # log dir problems surface via the Popen below

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # own process group for clean cleanup
        )
    except Exception as e:
        return f"Error starting agent: {e}"

    record = _AgentRecord(
        agent_id=agent_id,
        task=task,
        pid=process.pid,
        process=process,
        log_path=log_path,
        workdir=workdir,
        max_iter=max_iter,
        started_at=datetime.now(),
        base_url=resolved_base_url,
        detach=bool(detach),
    )
    _agents[agent_id] = record

    return (
        f"Agent started successfully.\n"
        f"Agent ID: {agent_id}\n"
        f"PID: {process.pid}\n"
        f"Task: {task[:300]}\n"
        f"Workdir: {workdir}\n"
        f"Inference: {resolved_base_url}"
        + (" (REMOTE worker — executes locally, thinks remotely)" if remote else " (local)") + "\n"
        f"Max iterations: {max_iter}\n"
        + ("Detached: survives a tool-server restart\n" if detach else "")
        + f"Log: {log_path}\n"
        f"\nUse check_agent('{agent_id}') to monitor progress."
    )


@_tool()
def start_skill_agent(skill: str, task: str, workdir: str = ".", max_iter: int = 200,
                      extra_context: str = "", base_url: str = "") -> str:
    """Spawn a long-running sub-agent with a specific skill activated.

    The skill's instructions are loaded and framed as the sub-agent's primary
    operating context. The sub-agent inherits the soul file + agent
    instructions + activated skill + task. Use this for specialized work where
    a skill encodes the right approach (code-review, security-audit,
    eval-task-author, deep-counsel, etc.).

    Args:
        skill: Name of the skill to activate (see the skill() index).
        task: What the sub-agent should accomplish, framed in terms of the skill.
        workdir: Working directory for file and shell operations.
        max_iter: Maximum iterations before the agent stops (default 200).
        extra_context: Additional context to brief the sub-agent (what you've
                       tried, relevant files, partial findings).
        base_url: OpenAI-compatible inference endpoint for advanced multi-box
                  setups (worker box serves tokens; the sub-agent executes on
                  THIS machine). Leave empty (default) for the local model.

    Returns:
        Agent ID for use with check_agent, list_agents, and stop_agent.
    """
    skill_record = _resolve_skill(skill)
    if skill_record is None:
        skill_record = _discover_skills(force=True).get(skill)
    if skill_record is None:
        available = ", ".join(sorted(_discover_skills().keys())) or "(none)"
        return f"Error: skill '{skill}' not found. Available: {available}"

    framed = (
        f"=== ACTIVATED SKILL: {skill} (source: {skill_record['source']}) ===\n\n"
        f"{skill_record['body']}\n\n"
        f"=== END SKILL ===\n\n"
        f"Apply the above skill's approach and conventions to the task. "
        f"Treat the skill body as authoritative guidance for HOW to work, not "
        f"just background reading."
    )
    if extra_context.strip():
        framed += f"\n\n=== EXTRA CONTEXT FROM CALLER ===\n{extra_context}\n=== END EXTRA CONTEXT ==="

    return start_agent(task=task, workdir=workdir, max_iter=max_iter, context=framed,
                       base_url=base_url)


@_tool()
def check_agent(agent_id: str) -> str:
    """Check the status of a running or completed agent.

    Returns iteration count, recent tool calls, last model reasoning, errors,
    and the final summary if the agent has finished. Works for agents this
    server never spawned (another spawn path, or a previous server process) —
    the session ledger is consulted first.

    Args:
        agent_id: The ID returned by start_agent.
    """
    # Ledger first, in-memory second, transcript last. Where BOTH the ledger
    # and the in-memory map know this agent, the in-memory report wins: it is
    # a strict superset (a live poll of the actual child process, its exit
    # code, its resolved inference endpoint), and the ledger would only tell
    # us what the live process already proves.
    ledger_record = _ledger_get(agent_id)
    record = _agents.get(agent_id)
    if record:
        report = _agent_status_report(record)
        if ledger_record and ledger_record.get("state"):
            report += f"\nSession: {ledger_record['state']} (ledger)"
        return report
    if ledger_record:
        return _ledger_report(ledger_record)

    # Fallback: check for orphaned log from a previous server session
    candidate = os.path.join(_LOG_DIR, f"agent-{agent_id}.jsonl")
    if os.path.exists(candidate):
        return _orphaned_log_report(agent_id, candidate)

    return f"Error: unknown agent '{agent_id}'. Use list_agents() to see tracked agents."


@_tool()
def list_agents() -> str:
    """List every agent known to this rig with its current status.

    Reads the durable session ledger FIRST and this server's in-memory map
    second, merged by id — so agents spawned by another process, or by a
    previous life of this one, are listed rather than silently missing.
    """
    # id → row, insertion-ordered. Ledger rows land first (newest first, the
    # order list_sessions returns), then in-memory rows OVERWRITE the matching
    # ledger row in place: a live process is the better witness of its own
    # status, and dict assignment keeps the merged row where the ledger put it.
    rows: dict[str, tuple] = {}

    for ledger_record in _ledger_agents():
        session_id = str(ledger_record.get("id") or "")
        if not session_id:
            continue
        events = _parse_agent_log(_ledger_transcript(ledger_record, session_id))
        meta = ledger_record.get("meta") if isinstance(ledger_record.get("meta"), dict) else {}
        started = _parse_ts(ledger_record.get("started_at"))
        rows[session_id] = (
            _ledger_status(ledger_record, events),
            sum(1 for e in events if e.get("type") == "iteration"),
            meta.get("max_iter") or "-",
            _format_elapsed(started) if started else "-",
            _ledger_task(ledger_record, events)[:50].replace("\n", " "),
        )

    # Snapshot: MCPServer (mcp 2.x) serves tools on worker threads, so a concurrent
    # start_agent can mutate _agents mid-iteration (same guard as _cleanup_agents).
    for agent_id, record in list(_agents.items()):
        alive = record.process.poll() is None
        events = _parse_agent_log(record.log_path)
        rows[agent_id] = (
            _classify_agent_status(alive, events, record.process.returncode),
            sum(1 for e in events if e.get("type") == "iteration"),
            record.max_iter,
            _format_elapsed(record.started_at),
            record.task[:50].replace("\n", " "),
        )

    if not rows:
        return "No agents tracked in this session."

    lines = [f"Agents ({len(rows)}):"]
    lines.append(f"{'ID':<36}  {'STATUS':<22}  {'ITER':>6}  {'RUNTIME':>9}  TASK")
    lines.append("-" * 110)
    for agent_id, (status, iters, max_iter, elapsed, task_preview) in rows.items():
        lines.append(
            f"{agent_id:<36}  {status:<22}  {iters:>4}/{str(max_iter):<4}  {elapsed:>9}  {task_preview}"
        )
    if len(rows) >= _LEDGER_LIST_LIMIT:
        lines.append(f"(capped at the {_LEDGER_LIST_LIMIT} most recent ledger sessions)")

    return "\n".join(lines)


@_tool()
def stop_agent(agent_id: str) -> str:
    """Stop a running agent. Sends SIGTERM for graceful shutdown, escalates to
    SIGKILL after 10 seconds if the process doesn't exit.

    Args:
        agent_id: The ID returned by start_agent.
    """
    record = _agents.get(agent_id)
    if not record:
        return f"Error: unknown agent '{agent_id}'"

    if record.process.poll() is not None:
        return (
            f"Agent {agent_id} is already stopped (exit code {record.process.returncode}).\n"
            f"Use check_agent('{agent_id}') to see final status."
        )

    try:
        pgid = os.getpgid(record.pid)
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return f"Agent {agent_id}: process already gone (PID {record.pid})"
    except PermissionError:
        return f"Error: permission denied stopping PID {record.pid}"

    try:
        record.process.wait(timeout=10)
        return f"Agent {agent_id} stopped gracefully (PID {record.pid})."
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
            record.process.wait(timeout=5)
            return f"Agent {agent_id} force-killed after timeout (PID {record.pid})."
        except Exception as e:
            return f"Agent {agent_id}: escalated to SIGKILL but cleanup failed: {e}"


@_tool()
def tail_agent(agent_id: str, lines: int = 30, from_offset: int = 0) -> str:
    """Stream the raw tail of an agent's log — recent events in JSONL format.

    More detailed than check_agent: returns full tool call results, complete model
    output, and raw event data. Useful for debugging or understanding exactly what
    an agent is doing.

    Two modes:
      from_offset = 0 (default) — the last `lines` events, as always.
      from_offset ≠ 0           — FOLLOW mode: only the bytes appended since
                                  that offset. The reply's first line ends
                                  `next from_offset=N`; pass N back on the next
                                  call and you never re-read the same bytes.
                                  Use -1 to follow from the start of the file.

    Args:
        agent_id: The ID returned by start_agent.
        lines: Number of recent log events to return (default 30).
        from_offset: Byte offset to resume from; 0 = last-N-lines view,
                     -1 = follow from the beginning of the transcript.
    """
    record = _agents.get(agent_id)
    if record:
        log_path = record.log_path
    else:
        # Not ours — the ledger knows where this session's transcript lives
        # (a client-spawned or pre-restart agent need not sit in agents/logs/).
        ledger_record = _ledger_get(agent_id)
        log_path = (_ledger_transcript(ledger_record, agent_id) if ledger_record
                    else os.path.join(_LOG_DIR, f"agent-{agent_id}.jsonl"))

    if not os.path.exists(log_path):
        return f"Error: no log file for agent '{agent_id}'"

    if from_offset != 0:
        chunk = tail_transcript(log_path, max(0, from_offset))
        header = (f"Agent {agent_id} — bytes {chunk['start']}-{chunk['offset']} "
                  f"of {chunk['size']}; next from_offset={chunk['offset']}")
        if chunk["reset"]:
            header += " (transcript shorter than the requested offset — restarted at 0)"
        if not chunk["content"]:
            return header + "\n\n(no new events)"
        return header + "\n\n" + chunk["content"]

    try:
        with open(log_path, "r") as f:
            all_lines = f.readlines()
    except Exception as e:
        return f"Error reading log: {e}"

    tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
    output = "".join(tail)

    # Cap output to avoid flooding context
    if len(output) > 50_000:
        output = output[-50_000:]
        output = "[...truncated...]\n" + output[output.index("\n") + 1:]

    header = f"Agent {agent_id} — last {len(tail)} of {len(all_lines)} events:\n\n"
    return header + output


@_tool()
def web_search(query: str, max_results: int = 10, pageno: int = 1,
               time_range: str = "") -> str:
    """Search the web via the stack's SearXNG instance (SEARXNG_URL, default
    http://localhost:8888).

    Returns titles, URLs, and snippets for the top results; follow up with
    fetch to read a result page.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (default 10).
        pageno: Result page, 1-based (default 1); use 2, 3, … for more results.
        time_range: Restrict to recent pages: 'day', 'month' or 'year' (default: none).
    """
    return _tools.web_search(query, max_results, pageno, time_range)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP tool server")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="Transport mode: stdio (opencode) or http (Open WebUI, port 3001)",
    )
    parser.add_argument("--port", type=int, default=3001, help="HTTP port (default: 3001)")
    args = parser.parse_args()

    if args.transport == "http":
        print(f"MCP server starting on http://{MCP_BIND}:{args.port}/mcp")
        if MCP_BIND not in ("127.0.0.1", "localhost", "::1"):
            print("WARNING: non-loopback bind and this transport has NO auth — "
                  "every tool (bash included) is open to that network. Prefer "
                  "the identity tool server (agents/openapi_tools.py) instead.",
                  file=sys.stderr)
        mcp.run(transport="streamable-http", host=MCP_BIND, port=args.port)
    else:
        mcp.run(transport="stdio")
