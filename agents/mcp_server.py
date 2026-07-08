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

Agent management (long-running autonomous agents):
  - start_agent: spawn a background agent with optional context briefing
  - check_agent: monitor progress, view recent activity and results
  - tail_agent: raw log tail for detailed debugging
  - list_agents: see all tracked agents and their status
  - stop_agent: terminate a running agent

Transports:
  stdio:           opencode local MCP (default)
  streamable-http: Open WebUI and remote clients (--transport http --port 3001)

Usage:
  python mcp_server.py                              # stdio (for opencode)
  python mcp_server.py --transport http --port 3001  # HTTP (for Open WebUI)
"""

import argparse
import atexit
import json
import os
import signal
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime

from mcp.server.fastmcp import FastMCP

# Shared tool implementations (single source of truth, incl. process-group
# reaping, rlimits, output capping, and protected-path write guards).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tools as _tools

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "local-tools",
    host="0.0.0.0",
    port=3001,
)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def bash(command: str, timeout: int = 120) -> str:
    """Run a shell command and return stdout + stderr. Use for building, testing,
    git operations, installing packages, or any system task."""
    return _tools.bash(command, timeout)


@mcp.tool()
def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    """Read lines from a file. Returns numbered lines."""
    return _tools.read_file(path, offset, limit)


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates directories if needed. Overwrites existing files."""
    return _tools.write_file(path, content)


@mcp.tool()
def list_files(directory: str = ".", pattern: str = "**/*") -> str:
    """List files matching a glob pattern in a directory."""
    return _tools.list_files(directory, pattern)


@mcp.tool()
def grep(pattern: str, path: str = ".", file_glob: str = "") -> str:
    """Search file contents for a regex pattern. Returns matching lines with
    file paths and line numbers."""
    return _tools.grep(pattern, path, file_glob)


@mcp.tool()
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


@mcp.tool()
def fetch(url: str, max_length: int = 50_000) -> str:
    """Fetch content from a URL and return it as text.

    For HTML pages, scripts and styles are removed and tags are stripped to
    return readable text. For JSON, plain text, and other formats, content is
    returned as-is.

    Args:
        url: The URL to fetch (http or https).
        max_length: Maximum characters to return (default 50000).
    """
    return _tools.fetch(url, max_length)


# ---------------------------------------------------------------------------
# Agent management — long-running autonomous agents
# ---------------------------------------------------------------------------

_RUNNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner.py")
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


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


# In-memory registry of agents spawned during this server session.
_agents: dict[str, _AgentRecord] = {}


def _cleanup_agents():
    """Terminate all running agents on server shutdown."""
    for record in list(_agents.values()):
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

    lines = [
        f"Agent: {record.agent_id}",
        f"Status: {status}",
        f"Task: {record.task[:300]}",
        f"Workdir: {record.workdir}",
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
    done_event = next((e for e in events if e.get("type") == "done"), None)
    iteration_events = [e for e in events if e.get("type") == "iteration"]

    task = start_event.get("task", "?")[:300] if start_event else "?"
    status = _classify_agent_status(alive=False, events=events, orphaned=True)

    lines = [
        f"Agent: {agent_id} (from previous session — no process control)",
        f"Task: {task}",
        f"Status: {status}",
        f"Iterations: {len(iteration_events)}",
    ]

    if done_event:
        lines.append(f"Summary: {done_event.get('summary', '(none)')}")

    lines.append(f"Log: {log_path}")
    return "\n".join(lines)


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


@mcp.tool()
def list_skills() -> str:
    """List every available skill — name, description, source.

    A skill is a curated package of instructions for a specific kind of work
    (code review, security audit, eval-task authoring, deep counsel, etc).
    The full skill body is NOT returned here — call load_skill(name) when one
    matches the task at hand. This keeps the system prompt small and only
    pays for the content you need.
    """
    skills = _discover_skills()
    if not skills:
        return (
            "No skills installed. Repo skills go in /home/max/Documents/models/skills/; "
            "global skills go in ~/.local/share/local-llm-skills/. "
            "Each skill is a folder with a SKILL.md file."
        )
    lines = [f"{len(skills)} skill(s) available:", ""]
    for name in sorted(skills):
        s = skills[name]
        src_tag = f"[{s['source']}]"
        lines.append(f"  {name:30s} {src_tag:>9}  {s['description']}")
    lines.append("")
    lines.append("Call load_skill(name) to read the full skill body.")
    lines.append("Call start_skill_agent(skill, task) to spawn a sub-agent with the skill activated.")
    return "\n".join(lines)


@mcp.tool()
def load_skill(name: str) -> str:
    """Return the full body of a skill (markdown instructions, checklists,
    examples). Use after list_skills() identifies a relevant skill.

    Args:
        name: The skill identifier from list_skills() (e.g. 'code-review').

    Returns:
        The skill's instructional content (frontmatter stripped). Apply these
        instructions to the work at hand.
    """
    skill = _resolve_skill(name)
    if skill is None:
        available = ", ".join(sorted(_discover_skills().keys())) or "(none)"
        return f"Error: skill '{name}' not found. Available: {available}"
    header = f"=== SKILL: {name} ({skill['source']}) ===\n\n"
    return header + skill["body"]


@mcp.tool()
def reload_skills() -> str:
    """Re-scan the skills directories and refresh the cache. Use after adding
    or editing a skill file without restarting the MCP server."""
    skills = _discover_skills(force=True)
    return f"Reloaded {len(skills)} skill(s) from repo and global directories."


# --- Agent MCP tools ---


@mcp.tool()
def start_agent(task: str, workdir: str = ".", max_iter: int = 200, context: str = "") -> str:
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

    # Estimate per-slot context budget (~85K tokens at 512K/6 slots, rough)
    context_budget = 85_000

    cmd = [
        sys.executable, _RUNNER_PATH,
        "--log-file", log_path,
        "--max-iter", str(max_iter),
        "--workdir", workdir,
        "--context-budget", str(context_budget),
    ]
    if context:
        cmd.extend(["--context", context])
    cmd.append(task)

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
    )
    _agents[agent_id] = record

    return (
        f"Agent started successfully.\n"
        f"Agent ID: {agent_id}\n"
        f"PID: {process.pid}\n"
        f"Task: {task[:300]}\n"
        f"Workdir: {workdir}\n"
        f"Max iterations: {max_iter}\n"
        f"Log: {log_path}\n"
        f"\nUse check_agent('{agent_id}') to monitor progress."
    )


@mcp.tool()
def start_skill_agent(skill: str, task: str, workdir: str = ".", max_iter: int = 200, extra_context: str = "") -> str:
    """Spawn a long-running sub-agent with a specific skill activated.

    The skill's instructions are loaded and framed as the sub-agent's primary
    operating context. The sub-agent inherits the soul file + agent
    instructions + activated skill + task. Use this for specialized work where
    a skill encodes the right approach (code-review, security-audit,
    eval-task-author, deep-counsel, etc.).

    Args:
        skill: Name of the skill to activate (see list_skills()).
        task: What the sub-agent should accomplish, framed in terms of the skill.
        workdir: Working directory for file and shell operations.
        max_iter: Maximum iterations before the agent stops (default 200).
        extra_context: Additional context to brief the sub-agent (what you've
                       tried, relevant files, partial findings).

    Returns:
        Agent ID for use with check_agent, list_agents, and stop_agent.
    """
    skill_record = _resolve_skill(skill)
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

    return start_agent(task=task, workdir=workdir, max_iter=max_iter, context=framed)


@mcp.tool()
def check_agent(agent_id: str) -> str:
    """Check the status of a running or completed agent.

    Returns iteration count, recent tool calls, last model reasoning, errors,
    and the final summary if the agent has finished.

    Args:
        agent_id: The ID returned by start_agent.
    """
    record = _agents.get(agent_id)
    if record:
        return _agent_status_report(record)

    # Fallback: check for orphaned log from a previous server session
    candidate = os.path.join(_LOG_DIR, f"agent-{agent_id}.jsonl")
    if os.path.exists(candidate):
        return _orphaned_log_report(agent_id, candidate)

    return f"Error: unknown agent '{agent_id}'. Use list_agents() to see tracked agents."


@mcp.tool()
def list_agents() -> str:
    """List all agents spawned during this server session with their current status."""
    if not _agents:
        return "No agents tracked in this session."

    lines = [f"Agents ({len(_agents)}):"]
    lines.append(f"{'ID':<36}  {'STATUS':<22}  {'ITER':>6}  {'RUNTIME':>9}  TASK")
    lines.append("-" * 110)

    for agent_id, record in _agents.items():
        alive = record.process.poll() is None
        events = _parse_agent_log(record.log_path)
        iters = sum(1 for e in events if e.get("type") == "iteration")
        status = _classify_agent_status(alive, events, record.process.returncode)

        elapsed = _format_elapsed(record.started_at)
        task_preview = record.task[:50].replace("\n", " ")

        lines.append(
            f"{agent_id:<36}  {status:<22}  {iters:>4}/{record.max_iter:<4}  {elapsed:>9}  {task_preview}"
        )

    return "\n".join(lines)


@mcp.tool()
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


@mcp.tool()
def tail_agent(agent_id: str, lines: int = 30) -> str:
    """Stream the raw tail of an agent's log — recent events in JSONL format.

    More detailed than check_agent: returns full tool call results, complete model
    output, and raw event data. Useful for debugging or understanding exactly what
    an agent is doing.

    Args:
        agent_id: The ID returned by start_agent.
        lines: Number of recent log events to return (default 30).
    """
    record = _agents.get(agent_id)
    log_path = record.log_path if record else os.path.join(_LOG_DIR, f"agent-{agent_id}.jsonl")

    if not os.path.exists(log_path):
        return f"Error: no log file for agent '{agent_id}'"

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


@mcp.tool()
def web_search(query: str, max_results: int = 10) -> str:
    """Search the web using the local SearXNG instance.

    Returns titles, URLs, and snippets for the top results. Requires SearXNG
    to be running (docker compose service or standalone on port 8888).

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (default 10).
    """
    return _tools.web_search(query, max_results)


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
        mcp.settings.port = args.port
        print(f"MCP server starting on http://0.0.0.0:{args.port}/mcp")
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
