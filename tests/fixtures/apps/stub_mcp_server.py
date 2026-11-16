#!/usr/bin/env python3
"""A tiny stdio MCP server for tests/test_apps.py: initialize + tools/list.

Tools come from $STUB_TOOLS_FILE (a JSON list of tool objects) or
$STUB_TOOLS (comma-separated names). The first CLI argument that looks like
`pkg@VERSION` sets the reported server version, so a fake `npx -y pkg@1.2.3`
launcher can exec this script with its own argv. $STUB_PAGE_SIZE > 0 pages
the list with nextCursor; $STUB_BANNER prints a non-JSON line first (servers
that log to stdout must not break the client); $STUB_HANG never answers.
"""
import json
import os
import sys
import time


def tools():
    f = os.environ.get("STUB_TOOLS_FILE")
    if f:
        with open(f) as fh:
            return json.load(fh)
    names = [n for n in os.environ.get("STUB_TOOLS", "alpha,beta").split(",") if n]
    return [{"name": n, "description": f"stub tool {n}",
             "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}}} for n in names]


def main():
    version = "0.0.0"
    for a in sys.argv[1:]:
        if "@" in a and not a.startswith("@"):
            version = a.rsplit("@", 1)[1]
        elif a.startswith("@") and a.count("@") == 2:
            version = a.rsplit("@", 1)[1]
    if os.environ.get("STUB_BANNER"):
        print("stub server starting (not JSON)", flush=True)
    if os.environ.get("STUB_HANG"):
        time.sleep(3600)
    page = int(os.environ.get("STUB_PAGE_SIZE", "0"))
    all_tools = tools()
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:
            continue
        if method == "initialize":
            res = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                   "serverInfo": {"name": "stub", "version": version}}
            # exercise the client's handling of a server->client request
            print(json.dumps({"jsonrpc": "2.0", "id": "srv-1", "method": "roots/list"}), flush=True)
        elif method == "tools/list":
            start = int((msg.get("params") or {}).get("cursor") or 0)
            chunk = all_tools[start:start + page] if page else all_tools
            res = {"tools": chunk}
            if page and start + page < len(all_tools):
                res["nextCursor"] = str(start + page)
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": mid,
                              "error": {"code": -32601, "message": "nope"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}), flush=True)


if __name__ == "__main__":
    main()
