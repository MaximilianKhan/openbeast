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
    (base_url routes its inference to a worker box for multi-box setups)
  - check_agent: monitor progress, view recent activity and results
  - tail_agent: raw log tail for detailed debugging
  - list_agents: see all tracked agents and their status
  - stop_agent: terminate a running agent

Artifacts (a durable URL for anything the model renders):
  - publish_artifact: publish an HTML file as a versioned page on the rig
  - list_artifacts: list published pages (title, URL, versions, visibility)

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
import stat
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

# Must match runner.py's DEFAULT_BASE_URL — when the resolved endpoint equals
# this, we omit --base-url so local spawns stay byte-identical to before the
# distributed-agents feature existed.
_DEFAULT_AGENT_BASE_URL = "http://localhost:8080/v1"


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
                base_url: str = "") -> str:
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
        f"Log: {log_path}\n"
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


@_tool()
def list_agents() -> str:
    """List all agents spawned during this server session with their current status."""
    if not _agents:
        return "No agents tracked in this session."

    lines = [f"Agents ({len(_agents)}):"]
    lines.append(f"{'ID':<36}  {'STATUS':<22}  {'ITER':>6}  {'RUNTIME':>9}  TASK")
    lines.append("-" * 110)

    # Snapshot: MCPServer (mcp 2.x) serves tools on worker threads, so a concurrent
    # start_agent can mutate _agents mid-iteration (same guard as _cleanup_agents).
    for agent_id, record in list(_agents.items()):
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
# Artifacts — a durable URL for anything the model renders
# ---------------------------------------------------------------------------
# The store (agents/artifact.py) is imported INSIDE each function, never at
# module scope: beast-artifact is opt-in (BEAST_ARTIFACT), and a missing or
# broken module must degrade to one tool returning "Error: ..." rather than
# taking the other 16 tools down with an ImportError at registration time.
#
# Both tools call the store IN PROCESS. They do not speak HTTP to
# agents/artifact_server.py: that server exists to *serve* the pages (and is
# what scripts/artifact.sh talks to over loopback with the locality token).
# Two consequences worth knowing: the tool path writes no row to
# .run/artifact-audit.jsonl (a publish still appends the store's own
# index.jsonl ledger), and it needs the opt-in flag checked here rather than
# discovering the service is off by failing to connect.


def _artifact_enabled() -> bool:
    """Is beast-artifact turned on for this rig?

    start.sh sources scripts/lib/conf.sh, which exports BEAST_ARTIFACT into
    every child — including the tool server this module runs inside. Without
    this check `publish_artifact` happily writes into the store and hands the
    model a confident URL that nothing is serving (reg#3).
    """
    val = (os.environ.get("OPENBEAST_BEAST_ARTIFACT")
           or os.environ.get("BEAST_ARTIFACT") or "").strip().lower()
    return val in ("1", "true", "yes", "on")


_ARTIFACT_OFF = ("Error: beast-artifact is not enabled on this rig. Enable it "
                 "with BEAST_ARTIFACT=true in openbeast.conf and restart the "
                 "stack (./stop.sh && ./start.sh).")


def _is_store_path(resolved: str) -> bool:
    """Is this path the artifact store, or inside it (D26)?

    `artifact.is_store_path()` is the store's own helper for the question —
    it realpaths both sides, so a symlink on either path still lands on the
    same real directory. Imported lazily like everything else here (the store
    is opt-in), and a store that will not import cannot be published into
    anyway.
    """
    try:
        import artifact as _artifact
        return bool(_artifact.is_store_path(resolved))
    except Exception:
        return False


def _read_artifact_page(path: str, cap: int):
    """Read `path` as the page to publish. Returns (html, None) or (None, err).

    The MODEL picks this path and publishing mints a durable, shareable URL,
    so a plain open().read() here is an arbitrary-file-read exfiltration
    channel: a review published /etc/passwd and /proc/self/maps through it,
    wedged a worker thread forever on a named pipe, and could have exhausted
    memory on a character device long before any size cap applied. Reuse the
    four guards tools.read_file earned in the 2026-09-10 hardening — refuse
    pseudo-filesystems, open O_NONBLOCK, fstat + S_ISREG, and check the size
    BEFORE reading — and return a string for every failure, including the
    ValueError a NUL byte in the path raises (which otherwise leaves the tool
    server as a 500, breaking the "tools never raise" contract).

    Those four guards are necessary and not sufficient: /etc/passwd is a
    regular file comfortably under the cap, and it published cleanly with all
    four in place. read_file may leave reads open ("an agent may legitimately
    read config") because its output lands in one model's context; THIS tool
    mints a durable, shareable URL, which is a different blast radius. So the
    page must come from the caller's own workspace — the directory write_file
    puts it in — and NOT from the store's own tree inside it (D26). Publishing
    an arbitrary path on the rig stays available to the human through
    scripts/artifact.sh, which is loopback- and token-gated.
    """
    fd = -1
    try:
        resolved = _tools._resolve(path)
        if _tools._hazard_path(resolved):
            return None, (f"Error: refusing to read {path} — pseudo-filesystem "
                          f"paths (/proc, /sys, /dev) can be infinite or "
                          f"side-effecting")
        base = os.path.realpath(_tools._base_dir())
        rel = os.path.relpath(resolved, base)
        if rel == os.pardir or rel.startswith(os.pardir + os.sep):
            return None, (f"Error: refusing to publish {path} — it is outside "
                          f"your workspace ({base}), and publishing mints a "
                          f"durable URL anyone with the link can open. Write "
                          f"the page there first (write_file) and publish that "
                          f"path.")
        if _is_store_path(resolved):
            # D26. "Inside the workspace" is NOT the same as "yours": the
            # store is $OPENBEAST_FILES_DIR/artifacts and the workspace is
            # $OPENBEAST_FILES_DIR itself whenever no per-user shard is in
            # play (FILES_SHARDING=off, and every MCP stdio caller). A
            # reviewer republished another user's PRIVATE page by naming the
            # store's own internal path — the bytes are read as a file, so
            # ownership never enters into it — and then re-shared it as
            # tailnet. The store is addressed by artifact_id, never by path.
            return None, (f"Error: refusing to publish {path} — that is the "
                          f"artifact store's own storage. To add a version to "
                          f"an existing page use "
                          f"publish_artifact(path, artifact_id=\"<id>\"); the "
                          f"id is the last part of its URL.")
        # O_NONBLOCK so opening a FIFO can't hang; fstat (not stat) so the
        # regular-file check and the read see the same inode.
        fd = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, (f"Error: not a regular file (refusing to publish "
                          f"{path}) — publish_artifact takes an HTML file")
        if st.st_size > cap:
            return None, (f"Error: {path} is {st.st_size} bytes, over the "
                          f"{cap} byte page cap")
        os.set_blocking(fd, True)
        with os.fdopen(fd, "rb") as fh:
            fd = -1  # the file object owns it now
            # Bound the read regardless of the stat'd size: a file growing
            # under us can report one size and stream another.
            raw = fh.read(cap + 1)
        if len(raw) > cap:
            return None, (f"Error: {path} is over the {cap} byte page cap")
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as e:
        return None, (f"Error: {path} is not UTF-8 text ({e}) — "
                      f"publish_artifact takes an HTML file, not a binary.")
    except (OSError, ValueError) as e:
        return None, f"Error: cannot read {path}: {e}"
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


@_tool()
def publish_artifact(path: str, title: str = "", description: str = "",
                     favicon: str = "", artifact_id: str = "",
                     label: str = "", visibility: str = "private") -> str:
    """Publish an HTML file as a page with a durable, shareable URL on the rig,
    and return that URL. Use it whenever the answer is worth more as a page
    than as chat text — a report, a table, a dashboard, a chart, a checklist,
    a small interactive tool — or whenever the user asks for something they
    will reopen, send to someone, or read on their phone. Write the file first
    (write_file), then publish it. Republishing with the same artifact_id
    keeps the URL and adds a version, so prefer updating over publishing a
    second page.

    HOW TO WRITE THE FILE — the page is served inside a locked-down sandbox,
    so these are hard rules, not style advice:

      1. Write ONLY a <title>, a <style>, and the body content. No <!doctype>,
         <html>, <head> or <body> tags — a skeleton (doctype, charset,
         viewport, small reset) is added at serve time. The <title> names the
         page in the tab and the gallery: a short noun phrase, not a sentence.
      2. Inline all CSS and JS in the file. External <script> may ONLY load
         from https://cdnjs.cloudflare.com, https://cdn.jsdelivr.net/npm/,
         https://cdn.tailwindcss.com or https://code.jquery.com (pin an exact
         version, UMD build); external stylesheets ONLY from
         https://fonts.googleapis.com. Every other host is blocked silently.
      3. Embed images, fonts and any other asset as data: URIs.
      4. fetch(), XMLHttpRequest and WebSocket are blocked — the page cannot
         call home, so compute from data you write into the file itself.
      5. localStorage THROWS in the sandbox. Wrap every read and write in
         try/catch and render correctly when there is no stored value.
      6. Support both themes: define colors as CSS custom properties on
         :root, then redefine those properties inside
         @media (prefers-color-scheme: dark). Never give a color its only
         definition inside the media block.
      7. Set an explicit background (and color) on body — a transparent body
         borrows the host page's theme and can end up unreadable.
      8. Wide content — tables, pre/code, diagrams — goes in a container with
         overflow-x: auto so the page itself never scrolls sideways on a
         phone.

    Args:
        path: Path to the .html file to publish. It must be in your workspace
              — the directory write_file writes to — because publishing turns
              it into a durable URL. Write the page first, then publish it.
        title: Page title; falls back to the file's own <title>, then the
               filename. Keep it stable across updates.
        description: One sentence shown as the subtitle in the gallery.
        favicon: One or two emoji (e.g. "📊") used as the tab icon. Set it on
                 the FIRST publish and never change it — people find the page
                 by its icon. Omit when updating.
        artifact_id: Publish into an EXISTING artifact: same URL, new version.
                     Omit to create a new one (the returned id is what you
                     pass back later).
        label: Short name for this version (e.g. "with Q3 numbers"), shown in
               the version picker.
        visibility: "private" (default, only you) or "tailnet" (any device
                    signed in to the tailnet can open the link).

    Returns:
        A line naming the page and its URL, or a string starting with "Error:".
    """
    # The opt-in check comes FIRST and is its own branch: the old guard below
    # only fired on an ImportError, so on a rig with BEAST_ARTIFACT unset the
    # import succeeded, the store wrote a version, and the model got a URL
    # that nothing serves.
    if not _artifact_enabled():
        return _ARTIFACT_OFF
    try:
        import artifact as _artifact  # lazy: see the note above
    except Exception as e:
        return (f"Error: artifact store unavailable ({e}). Enable it with "
                f"BEAST_ARTIFACT=true in openbeast.conf and restart the stack.")
    html, err = _read_artifact_page(path, _artifact.CAPS["page_bytes"])
    if err:
        return err
    if not html.strip():
        return f"Error: {path} is empty — nothing to publish."
    try:
        meta = _artifact.publish(
            html,
            title=title.strip() or None,
            description=description.strip() or None,
            favicon=favicon.strip() or None,
            artifact_id=artifact_id.strip() or None,
            label=label.strip() or None,
            visibility=(visibility.strip() or "private"),
        )
    except Exception as e:  # ArtifactError and anything else: tool contract
        return f"Error: publish failed: {e}"
    name = meta.get("title") or os.path.basename(path)
    return (f'Published "{name}" → {meta.get("url")} '
            f'(v{meta.get("version")}, id {meta.get("id")})')


@_tool()
def list_artifacts(limit: int = 25) -> str:
    """List the published artifact pages you can see, newest first: title, URL,
    version count, visibility and when it was last updated. Use it to find the
    id of a page you want to update with publish_artifact(artifact_id=...), or
    to hand the user a link to something published earlier.

    Args:
        limit: Maximum number of artifacts to list (default 25).
    """
    try:
        import artifact as _artifact  # lazy: see the note above
    except Exception as e:
        return (f"Error: artifact store unavailable ({e}). Enable it with "
                f"BEAST_ARTIFACT=true in openbeast.conf and restart the stack.")
    try:
        # viewer= is not optional: without it the gallery enumerates EVERY
        # operator's private artifacts to whoever called the tool. The identity
        # server sets the ContextVar that default_owner() reads.
        rows = _artifact.list_artifacts(viewer=_artifact.default_owner(),
                                        limit=max(1, int(limit)))
    except Exception as e:
        return f"Error: could not list artifacts: {e}"
    if not rows:
        return ("No artifacts published yet. Write an HTML file and call "
                "publish_artifact(path) to make one.")
    lines = [f"{len(rows)} artifact(s):", ""]
    for r in rows:
        versions = r.get("versions")
        if isinstance(versions, list):
            versions = len(versions)
        updated = str(r.get("updated_at") or r.get("created_at") or "")[:16]
        updated = updated.replace("T", " ")
        lines.append(
            f"  {str(r.get('title') or '(untitled)')[:40]:40s}  "
            f"{r.get('url', '')}  "
            f"v{versions or 1}  {r.get('visibility', '?')}  {updated}"
        )
    lines.append("")
    lines.append('Update one in place: publish_artifact(path, '
                 'artifact_id="<id>") — the id is the last part of the URL.')
    return "\n".join(lines)


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
