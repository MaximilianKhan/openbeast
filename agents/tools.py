"""
Built-in tools for the agent runner.

Each tool is defined as:
  - A schema (OpenAI function-calling format)
  - A handler function that executes the tool and returns a string result
"""

import html as html_module
import json
import os
import re
import shlex
import subprocess
import glob as glob_module
import urllib.error
import urllib.parse
import urllib.request

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
        cmd = f"grep -rn --include='*' -E {shlex.quote(pattern)} {shlex.quote(path)}"
        if file_glob:
            cmd = f"grep -rn --include={shlex.quote(file_glob)} -E {shlex.quote(pattern)} {shlex.quote(path)}"
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


def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Replace an exact string in a file with new content."""
    try:
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            return f"Error: file not found: {path}"

        with open(path, "r") as f:
            content = f.read()

        if not old_string:
            return "Error: old_string must not be empty"

        if old_string == new_string:
            return "Error: old_string and new_string are identical — nothing to change"

        count = content.count(old_string)
        if count == 0:
            lines = old_string.split("\n")
            if len(lines) > 1 and content.find(lines[0]) != -1:
                return (
                    f"Error: exact match not found in {path}. "
                    f"The first line was found but the full multi-line string didn't match. "
                    f"Check whitespace and indentation."
                )
            return f"Error: old_string not found in {path}"

        if count > 1 and not replace_all:
            return (
                f"Error: old_string appears {count} times in {path}. "
                f"Include more surrounding context to make it unique, "
                f"or set replace_all=true to replace all occurrences."
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        with open(path, "w") as f:
            f.write(new_content)

        change_line = content[:content.index(old_string)].count("\n") + 1
        old_lines = old_string.count("\n") + 1
        new_lines = new_string.count("\n") + 1

        if replace_all and count > 1:
            return f"Replaced {count} occurrences in {path} ({len(old_string)} → {len(new_string)} chars each)"
        else:
            return (
                f"Edited {path} at line {change_line}: "
                f"replaced {old_lines} line{'s' if old_lines != 1 else ''} "
                f"with {new_lines} line{'s' if new_lines != 1 else ''}"
            )
    except Exception as e:
        return f"Error: {e}"


def fetch(url: str, max_length: int = 50_000) -> str:
    """Fetch content from a URL and return as text."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; local-agent/1.0)",
                "Accept": "text/html,application/json,text/plain,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()
            raw_bytes = resp.read(max_length * 4)
            text = raw_bytes.decode(charset, errors="replace")

        if "html" in content_type.lower() or text.strip()[:100].lower().startswith(("<!doctype", "<html")):
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<(br|hr|/p|/div|/h[1-6]|/li|/tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = html_module.unescape(text)
            text = re.sub(r"[^\S\n]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text.strip()

        if len(text) > max_length:
            text = text[:max_length] + f"\n\n[truncated at {max_length} chars — {len(raw_bytes)} bytes fetched]"

        return text if text else "(empty response)"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(2000).decode("utf-8", errors="replace")
        except Exception:
            pass
        return f"HTTP {e.code} {e.reason}" + (f"\n{body}" if body else "")
    except urllib.error.URLError as e:
        return f"URL error: {e.reason}"
    except Exception as e:
        return f"Error: {e}"


def web_search(query: str, max_results: int = 10) -> str:
    """Search the web using the local SearXNG instance."""
    searxng_url = os.environ.get("SEARXNG_URL", "http://localhost:8888")
    try:
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "categories": "general",
        })
        url = f"{searxng_url}/search?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "local-agent/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError:
        return (
            "Error: SearXNG is not running. Start it with:\n"
            "  docker run -d -p 8888:8080 -e SEARXNG_BASE_URL=http://localhost:8888/ searxng/searxng\n"
            "Or set SEARXNG_URL env var if running on a different port."
        )
    except Exception as e:
        return f"Error: {e}"

    results = data.get("results", [])[:max_results]
    if not results:
        return f"No results for: {query}"

    lines = [f"Web search: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        url = r.get("url", "")
        snippet = r.get("content", "")[:200]
        lines.append(f"{i}. {title}")
        lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")
    return "\n".join(lines)


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
            "name": "edit_file",
            "description": "Replace an exact string in a file with new content. Use this instead of write_file when modifying existing files — it's safer and more precise. The old_string must appear exactly once unless replace_all is true. To insert text, include surrounding context in old_string and add new text within that context in new_string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to edit"},
                    "old_string": {"type": "string", "description": "The exact text to find (must be unique in the file)"},
                    "new_string": {"type": "string", "description": "The replacement text"},
                    "replace_all": {"type": "boolean", "description": "Replace all occurrences instead of requiring uniqueness", "default": False},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch",
            "description": "Fetch content from a URL and return it as text. HTML pages are cleaned (scripts/styles removed, tags stripped) to return readable text. JSON and plain text are returned as-is.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch (http or https)"},
                    "max_length": {"type": "integer", "description": "Maximum characters to return (default 50000)", "default": 50000},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using the local SearXNG instance. Returns titles, URLs, and snippets. Requires SearXNG running on port 8888.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string"},
                    "max_results": {"type": "integer", "description": "Maximum results to return (default 10)", "default": 10},
                },
                "required": ["query"],
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
    "edit_file": lambda args: edit_file(args["path"], args["old_string"], args["new_string"], args.get("replace_all", False)),
    "list_files": lambda args: list_files(args.get("directory", "."), args.get("pattern", "**/*")),
    "grep": lambda args: grep(args["pattern"], args.get("path", "."), args.get("file_glob", "")),
    "fetch": lambda args: fetch(args["url"], args.get("max_length", 50_000)),
    "web_search": lambda args: web_search(args["query"], args.get("max_results", 10)),
    "task_done": lambda args: task_done(args["summary"]),
}
