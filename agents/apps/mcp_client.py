"""A minimal MCP client: initialize + tools/list, nothing else.

beast-apps uses it to learn what an MCP server ACTUALLY offers at a given
version (`apps.sh refresh` / `upgrade`), so a release that adds, removes or
reshapes tools is seen instead of assumed. It never calls a tool.

Transports: stdio (newline-delimited JSON-RPC, the MCP stdio framing) and
streamable HTTP (POST, JSON or SSE response, Mcp-Session-Id carried).
Stdlib only; every wait is bounded by one overall deadline.
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"
CLIENT = {"name": "openbeast-apps", "version": "1"}
MAX_PAGES = 50


class MCPError(Exception):
    pass


def _error_text(err) -> str:
    """A JSON-RPC `error` as text, whatever shape a misbehaving server sends
    (the spec says an object with `message`; a bare string or number must
    still become an MCPError, never an AttributeError traceback)."""
    if isinstance(err, dict):
        return str(err.get("message", err))
    return str(err)


def _result(msg: dict) -> dict:
    if "error" in msg:
        raise MCPError(f"server error: {_error_text(msg['error'])}")
    res = msg.get("result")
    if res is None:
        return {}
    if not isinstance(res, dict):
        raise MCPError(f"malformed answer: result is a {type(res).__name__}, not an object")
    return res


def _tools_page(res: dict) -> tuple[list[dict], str | None]:
    tools = res.get("tools", [])
    if not isinstance(tools, list):
        raise MCPError(f"malformed tools/list answer: tools is a {type(tools).__name__}")
    cursor = res.get("nextCursor")
    if cursor is not None and not isinstance(cursor, str):
        raise MCPError("malformed tools/list answer: nextCursor is not a string")
    return [t for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)], cursor


def _init_params() -> dict:
    return {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT}


def list_tools_stdio(command: list[str], env: dict, timeout: float = 90.0,
                     cwd: str | None = None) -> tuple[dict, list[dict]]:
    deadline = time.monotonic() + timeout
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, cwd=cwd, text=True, bufsize=1)
    except OSError as e:
        raise MCPError(f"cannot start {command[0]}: {e}") from None
    lines: queue.Queue = queue.Queue()
    err_tail: list[str] = []

    def pump_out():
        for ln in proc.stdout:
            lines.put(ln)
        lines.put(None)

    def pump_err():
        for ln in proc.stderr:
            err_tail.append(ln.rstrip())
            del err_tail[:-20]

    threading.Thread(target=pump_out, daemon=True).start()
    threading.Thread(target=pump_err, daemon=True).start()

    def send(msg: dict) -> None:
        try:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise MCPError("server closed its stdin" + _tail(err_tail)) from None

    def wait_for(rid: int) -> dict:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise MCPError(f"timed out after {timeout:.0f}s waiting for the server" + _tail(err_tail))
            try:
                ln = lines.get(timeout=min(left, 0.5))
            except queue.Empty:
                continue
            if ln is None:
                raise MCPError(f"server exited (code {proc.poll()}) before answering" + _tail(err_tail))
            ln = ln.strip()
            if not ln.startswith("{"):
                continue                      # a banner on stdout: not ours to parse
            try:
                msg = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:   # a request FROM the server (roots/list, ping…)
                send({"jsonrpc": "2.0", "id": msg["id"],
                      "result": {}} if msg["method"] == "ping" else
                     {"jsonrpc": "2.0", "id": msg["id"],
                      "error": {"code": -32601, "message": "not supported by openbeast-apps"}})
                continue
            if msg.get("id") == rid:
                return _result(msg)

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _init_params()})
        init = wait_for(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools: list[dict] = []
        cursor, rid = None, 2
        for _ in range(MAX_PAGES):
            params = {"cursor": cursor} if cursor else {}
            send({"jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": params})
            page, cursor = _tools_page(wait_for(rid))
            tools.extend(page)
            rid += 1
            if not cursor:
                break
        return init, tools
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()


def _tail(lines: list[str]) -> str:
    t = [x for x in lines if x.strip()][-3:]
    return (" — stderr: " + " | ".join(t)[:400]) if t else ""


def _http(url: str, msg: dict, session: str | None, timeout: float) -> tuple[dict | None, str | None]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": PROTOCOL}
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, data=json.dumps(msg).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            sid = r.headers.get("Mcp-Session-Id") or session
            body = r.read(8 * 1024 * 1024).decode("utf-8", "replace")
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise MCPError(f"HTTP {e.code} from {url}") from None
    except (urllib.error.URLError, OSError) as e:
        raise MCPError(f"cannot reach {url}: {getattr(e, 'reason', e)}") from None
    if "id" not in msg:
        return None, sid
    cands = []
    if "text/event-stream" in ctype:
        for ln in body.splitlines():
            if ln.startswith("data:"):
                cands.append(ln[5:].strip())
    else:
        cands.append(body)
    for c in cands:
        try:
            m = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(m, dict) and m.get("id") == msg["id"]:
            return _result(m), sid
    raise MCPError(f"no JSON-RPC answer to {msg['method']} from {url}")


def list_tools_http(url: str, timeout: float = 30.0) -> tuple[dict, list[dict]]:
    init, sid = _http(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _init_params()},
                      None, timeout)
    _http(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid, timeout)
    tools: list[dict] = []
    cursor, rid = None, 2
    for _ in range(MAX_PAGES):
        res, sid = _http(url, {"jsonrpc": "2.0", "id": rid, "method": "tools/list",
                               "params": {"cursor": cursor} if cursor else {}}, sid, timeout)
        page, cursor = _tools_page(res or {})
        tools.extend(page)
        rid += 1
        if not cursor:
            break
    return init or {}, tools

