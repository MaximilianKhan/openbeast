"""
Built-in tools for the agent runner.

Each tool is defined as:
  - A schema (OpenAI function-calling format)
  - A handler function that executes the tool and returns a string result
"""

import os
import subprocess
import glob as glob_module

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def bash(command: str, timeout: int = 120) -> str:
    """Run a shell command and return stdout + stderr."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.environ.get("AGENT_WORKDIR", os.getcwd()),
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        if not output.strip():
            output = f"(exit code {result.returncode})"
        return output[:50_000]  # cap output size
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    """Read lines from a file."""
    try:
        path = os.path.expanduser(path)
        with open(path, "r") as f:
            lines = f.readlines()
        total = len(lines)
        selected = lines[offset : offset + limit]
        numbered = [f"{i + offset + 1}\t{line}" for i, line in enumerate(selected)]
        header = f"[{path}] lines {offset + 1}-{offset + len(selected)} of {total}\n"
        return header + "".join(numbered)
    except Exception as e:
        return f"Error: {e}"


def write_file(path: str, content: str) -> str:
    """Write content to a file, creating directories if needed."""
    try:
        path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def list_files(directory: str = ".", pattern: str = "**/*") -> str:
    """List files matching a glob pattern."""
    try:
        directory = os.path.expanduser(directory)
        matches = sorted(glob_module.glob(os.path.join(directory, pattern), recursive=True))
        files = [m for m in matches if os.path.isfile(m)]
        if not files:
            return f"No files matching '{pattern}' in {directory}"
        result = "\n".join(files[:200])
        if len(files) > 200:
            result += f"\n... and {len(files) - 200} more"
        return result
    except Exception as e:
        return f"Error: {e}"


def grep(pattern: str, path: str = ".", file_glob: str = "") -> str:
    """Search file contents for a regex pattern."""
    try:
        cmd = f"grep -rn --include='*' -E {repr(pattern)} {repr(path)}"
        if file_glob:
            cmd = f"grep -rn --include={repr(file_glob)} -E {repr(pattern)} {repr(path)}"
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.environ.get("AGENT_WORKDIR", os.getcwd()),
        )
        output = result.stdout or "(no matches)"
        return output[:50_000]
    except subprocess.TimeoutExpired:
        return "Error: grep timed out"
    except Exception as e:
        return f"Error: {e}"


def task_done(summary: str) -> str:
    """Signal that the task is complete."""
    return f"TASK_DONE: {summary}"


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command. Use for building, testing, git operations, installing packages, or any system task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)", "default": 120},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read lines from a file. Returns numbered lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "offset": {"type": "integer", "description": "Line offset to start from (0-indexed)", "default": 0},
                    "limit": {"type": "integer", "description": "Max lines to read", "default": 500},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Creates directories if needed. Overwrites existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to write to"},
                    "content": {"type": "string", "description": "File content"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files matching a glob pattern in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to search", "default": "."},
                    "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')", "default": "**/*"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents for a regex pattern. Returns matching lines with file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "File or directory to search in", "default": "."},
                    "file_glob": {"type": "string", "description": "Filter files by glob (e.g. '*.py')"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_done",
            "description": "Call this when the task is fully complete. Provide a summary of what was accomplished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Summary of what was accomplished"},
                },
                "required": ["summary"],
            },
        },
    },
]

TOOL_HANDLERS = {
    "bash": lambda args: bash(args["command"], args.get("timeout", 120)),
    "read_file": lambda args: read_file(args["path"], args.get("offset", 0), args.get("limit", 500)),
    "write_file": lambda args: write_file(args["path"], args["content"]),
    "list_files": lambda args: list_files(args.get("directory", "."), args.get("pattern", "**/*")),
    "grep": lambda args: grep(args["pattern"], args.get("path", "."), args.get("file_glob", "")),
    "task_done": lambda args: task_done(args["summary"]),
}
