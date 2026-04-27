#!/usr/bin/env python3
"""
MCP server exposing local tools to any MCP-compatible client
(OpenCode, Open WebUI, etc).

Tools:
  - bash: run shell commands
  - read_file: read file contents with line numbers
  - write_file: create/overwrite files
  - list_files: glob-based file discovery
  - grep: regex search across files

Transports:
  stdio:           opencode local MCP (default)
  streamable-http: Open WebUI and remote clients (--transport http --port 3001)

Usage:
  python mcp_server.py                              # stdio (for opencode)
  python mcp_server.py --transport http --port 3001  # HTTP (for Open WebUI)
"""

import argparse
import os
import subprocess
import glob as glob_module

from mcp.server.fastmcp import FastMCP

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
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        if not output.strip():
            output = f"(exit code {result.returncode})"
        return output[:50_000]
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    """Read lines from a file. Returns numbered lines."""
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


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates directories if needed. Overwrites existing files."""
    try:
        path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def list_files(directory: str = ".", pattern: str = "**/*") -> str:
    """List files matching a glob pattern in a directory."""
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


@mcp.tool()
def grep(pattern: str, path: str = ".", file_glob: str = "") -> str:
    """Search file contents for a regex pattern. Returns matching lines with
    file paths and line numbers."""
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
        )
        output = result.stdout or "(no matches)"
        return output[:50_000]
    except subprocess.TimeoutExpired:
        return "Error: grep timed out"
    except Exception as e:
        return f"Error: {e}"


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
