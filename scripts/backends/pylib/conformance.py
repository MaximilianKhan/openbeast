#!/usr/bin/env python3
"""Black-box conformance probe: does THIS served model do what OpenBeast needs?

    conformance.sh [--url INFERENCE_URL] [--model NAME] [--key-file F] [--backend vllm|tensorfold|llama]
                   [--out DIR] [--heavy] [--concurrency N] [--timeout S] [--json]

Talks to any OpenAI-compatible server over /v1 and knows nothing about the
model in advance. Each probe is PASS / FAIL / WARN / INFO / UNKNOWN / SKIP:

  models        /v1/models lists the model (and max_model_len when exposed)      REQUIRED
  unknown_id    does an unknown request model id 404? (vLLM's default) — decides
                whether consumers must send the served name exactly
  chat          a plain non-streaming round-trip                                  REQUIRED
  stream        SSE streaming delivers deltas and ends                            REQUIRED
  reasoning     where thinking arrives: reasoning_content / reasoning / inline <think>
  tools         OpenBeast's own bash + read_file schemas, tool_choice auto: structured
                tool_calls whose arguments are a JSON STRING of an object          REQUIRED
  tool_result   the tool result goes back and a final answer comes out             REQUIRED
  parallel      two tool calls in one turn
  max_tokens    a tiny max_tokens is honoured (the runner's only reasoning bound on TensorFold CUDA)
  overflow      (--heavy, needs max_model_len) the error text matches agents/runner.py's detector
  concurrency   (--concurrency N) N small requests at once all succeed

Exit 0 only when every REQUIRED probe passes; 1 otherwise; 2 on bad usage.
The API key comes from LLAMA_API_KEY / OPENBEAST_API_KEY in the environment or
--key-file (0600) — never argv — and is never sent when --backend tensorfold.
"""
from __future__ import annotations

import ast
import json
import os
import re
import stat
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
from hfapi import SameOriginAuth  # noqa: E402  (a redirect never carries the key to another origin)

_OPENER = urllib.request.build_opener(SameOriginAuth)
MARKER_TEXT = "openbeast-conformance-7f3a"
UNKNOWN_ID = "openbeast-conformance-no-such-model"

FALLBACK_TOOLS = [
    {"type": "function", "function": {
        "name": "bash", "description": "Run a shell command and return its output.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "The shell command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)"}},
            "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a text file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path of the file"}}, "required": ["path"]}}},
]
TEXT_TOOL_MARKERS = ("<tool_call>", "<function=", "[TOOL_CALLS]", "<|python_tag|>", "<|tool_call_begin|>",
                     "<arg_key>", "<minimax:tool_call>", "<|channel|>", "functools[", "\"name\":")


def openbeast_tools() -> tuple[list[dict], str]:
    """bash + read_file exactly as agents/tools.py defines them (fallback: a faithful subset)."""
    agents = REPO / "agents"
    if (agents / "tools.py").is_file():
        sys.path.insert(0, str(agents))
        try:
            import tools  # noqa: PLC0415

            chosen = [s for s in tools.TOOL_SCHEMAS if s.get("function", {}).get("name") in ("bash", "read_file")]
            if len(chosen) == 2:
                return chosen, "agents/tools.py TOOL_SCHEMAS"
        except Exception as e:  # noqa: BLE001 - any import failure means: use the subset
            print(f"note: agents/tools.py not importable ({type(e).__name__}); using the built-in subset",
                  file=sys.stderr)
        finally:
            sys.path.remove(str(agents))
    return FALLBACK_TOOLS, "built-in subset"


def runner_overflow_re() -> re.Pattern | None:
    """agents/runner.py's _CTX_OVERFLOW_RE, read with ast (runner.py imports openai; we do not)."""
    src = REPO / "agents" / "runner.py"
    if not src.is_file():
        return None
    for node in ast.parse(src.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_CTX_OVERFLOW_RE"
                                                 for t in node.targets):
            call = node.value
            if isinstance(call, ast.Call) and call.args and isinstance(call.args[0], ast.Constant):
                flags = 0
                for a in call.args[1:]:
                    if isinstance(a, ast.Attribute) and a.attr in ("IGNORECASE", "I"):
                        flags |= re.IGNORECASE
                return re.compile(call.args[0].value, flags)
    return None


def read_key(key_file: str | None, backend: str) -> str | None:
    if backend == "tensorfold":
        return None
    if key_file:
        p = Path(os.path.expanduser(key_file))
        st = p.stat()
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SystemExit(f"--key-file {p} is mode {stat.S_IMODE(st.st_mode):o} — chmod 600 it")
        return p.read_text().strip() or None
    return (os.environ.get("LLAMA_API_KEY") or os.environ.get("OPENBEAST_API_KEY") or "").strip() or None


class Client:
    def __init__(self, base: str, key: str | None, timeout: float):
        self.base = base.rstrip("/")
        if self.base.endswith("/v1"):
            self.base = self.base[:-3]
        self.key, self.timeout = key, timeout

    def _req(self, method: str, path: str, body: dict | None = None, stream: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        return _OPENER.open(req, timeout=self.timeout)

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | None, str]:
        try:
            with self._req(method, path, body) as r:
                raw = r.read().decode(errors="replace")
                status = r.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            status = e.code
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return 0, None, f"{type(e).__name__}: {getattr(e, 'reason', e)}"
        try:
            return status, json.loads(raw), raw
        except ValueError:
            return status, None, raw

    def stream(self, body: dict) -> tuple[int, list[dict], bool, str]:
        chunks, done = [], False
        try:
            with self._req("POST", "/v1/chat/completions", body, stream=True) as r:
                status = r.status
                for line in r:
                    s = line.decode(errors="replace").strip()
                    if not s.startswith("data:"):
                        continue
                    payload = s[5:].strip()
                    if payload == "[DONE]":
                        done = True
                        break
                    try:
                        chunks.append(json.loads(payload))
                    except ValueError:
                        return status, chunks, done, f"a non-JSON SSE line: {payload[:120]}"
        except urllib.error.HTTPError as e:
            return e.code, chunks, done, e.read().decode(errors="replace")[:300]
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            return 0, chunks, done, f"{type(e).__name__}: {getattr(e, 'reason', e)}"
        return status, chunks, done, ""


class Report:
    def __init__(self):
        self.results: list[dict] = []
        self.facts: dict = {}
        self.recommend: list[str] = []

    def add(self, name: str, status: str, detail: str, required: bool = False, **data) -> dict:
        r = {"name": name, "status": status, "required": required, "detail": detail}
        if data:
            r["data"] = data
        self.results.append(r)
        return r

    @property
    def ok(self) -> bool:
        return all(r["status"] == "pass" for r in self.results if r["required"])


def _msg(resp: dict | None) -> dict:
    try:
        return resp["choices"][0].get("message") or {}
    except (TypeError, KeyError, IndexError):
        return {}


def _finish(resp: dict | None) -> str | None:
    try:
        return resp["choices"][0].get("finish_reason")
    except (TypeError, KeyError, IndexError):
        return None


def _err(status: int, raw: str) -> str:
    return f"HTTP {status}: {raw[:240]}" if status else raw[:240]


def reasoning_where(msg: dict) -> str | None:
    if (msg.get("reasoning_content") or "").strip():
        return "reasoning_content"
    if (msg.get("reasoning") or "").strip():
        return "reasoning"
    if re.search(r"<think>|</think>", msg.get("content") or ""):
        return "inline <think> in content"
    return None


def check_tool_calls(msg: dict, tools: list[dict]) -> tuple[str, str, dict]:
    """(status, detail, data) for the first assistant turn of the tool probe."""
    calls = msg.get("tool_calls") or []
    names = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
    if not calls:
        content = msg.get("content") or ""
        if any(m in content for m in TEXT_TOOL_MARKERS):
            return "fail", ("the model wrote the tool call as TEXT in content — the server is not parsing it "
                            "(vLLM: --enable-auto-tool-choice with the right TOOL_CALL_PARSER)"), \
                {"content": content[:300]}
        return "fail", "no tool_calls and no tool-call text: the model answered without the tool", \
            {"content": content[:300]}
    notes, data = [], {"count": len(calls), "calls": []}
    for c in calls:
        fn = c.get("function") or {}
        name, args = fn.get("name"), fn.get("arguments")
        data["calls"].append({"id": c.get("id"), "name": name, "arguments_type": type(args).__name__})
        if name not in names:
            return "fail", f"called unknown tool {name!r}", data
        if not isinstance(args, str):
            return "fail", (f"arguments is a JSON {type(args).__name__}, not a string — the OpenAI API (and "
                            "agents/runner.py's json.loads) expect a JSON-encoded string"), data
        try:
            parsed = json.loads(args)
        except ValueError as e:
            return "fail", f"arguments is not valid JSON ({e}): {args[:160]!r}", data
        if not isinstance(parsed, dict):
            return "fail", f"arguments decodes to a {type(parsed).__name__}, not an object", data
        props = names[name].get("properties", {})
        for k, v in parsed.items():
            want = (props.get(k) or {}).get("type")
            if want in ("integer", "number") and isinstance(v, str):
                notes.append(f"{name}.{k} arrived as the STRING {v!r} (schema says {want}): the server does "
                             "no schema coercion (handled on our side: tools.coerce_args)")
                data["string_values"] = True
        missing = [r for r in names[name].get("required", []) if r not in parsed]
        if missing:
            return "fail", f"{name} call is missing required {missing}", data
        if not c.get("id"):
            notes.append("tool call has no id (tool results are matched by id)")
    return ("warn" if notes else "pass"), ("; ".join(notes) or f"{len(calls)} structured call(s), JSON-string "
                                           "arguments of the right shape"), data


def run(cl: Client, model: str | None, backend: str, heavy: bool, concurrency: int, rep: Report) -> None:
    tools, tools_from = openbeast_tools()
    rep.facts["tool_schemas_from"] = tools_from

    # models ------------------------------------------------------------------
    st, js, raw = cl.call("GET", "/v1/models")
    ids, mml = [], None
    if st == 200 and isinstance(js, dict):
        data = js.get("data") or []
        ids = [d.get("id") for d in data if isinstance(d, dict) and d.get("id")]
        for d in data:
            if isinstance(d, dict) and (model is None or d.get("id") == model) and d.get("max_model_len"):
                mml = int(d["max_model_len"])
                break
    if st in (401, 403):
        rep.add("models", "fail", f"{_err(st, raw)} — the server wants a key: LLAMA_API_KEY or --key-file",
                required=True)
        return
    if not ids:
        rep.add("models", "fail", f"no model listed ({_err(st, raw)})", required=True)
        return
    if model and model not in ids:
        rep.add("models", "fail", f"{model!r} is not listed; the server lists {ids}", required=True, ids=ids)
        return
    model = model or ids[0]
    rep.facts.update({"model": model, "listed": ids, "max_model_len": mml})
    rep.add("models", "pass", f"lists {ids}" + (f"; max_model_len {mml}" if mml else "; no max_model_len exposed"),
            required=True, ids=ids, max_model_len=mml)

    # unknown id -------------------------------------------------------------
    st, js, raw = cl.call("POST", "/v1/chat/completions", {
        "model": UNKNOWN_ID, "messages": [{"role": "user", "content": "Say ok."}], "max_tokens": 1})
    if st == 404:
        rep.facts["strict_model_names"] = True
        rep.add("unknown_id", "info", "an unknown model id gets 404: every consumer must send the served name "
                f"{model!r} exactly (or start vLLM with VLLM_SKIP_MODEL_NAME_VALIDATION=1)")
    elif st == 200:
        rep.facts["strict_model_names"] = False
        rep.add("unknown_id", "info", "an unknown model id is served anyway (llama-server-like): any id works")
    else:
        rep.add("unknown_id", "unknown", f"unexpected answer to an unknown id: {_err(st, raw)}")

    base = {"model": model}
    # chat ------------------------------------------------------------------
    t0 = time.monotonic()
    st, js, raw = cl.call("POST", "/v1/chat/completions", dict(base, messages=[
        {"role": "user", "content": "What is 17 * 23? Think briefly if you need to, then answer with just the number."}],
        max_tokens=2048))
    msg = _msg(js)
    if st == 200 and (msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning")):
        rep.add("chat", "pass", f"round-trip in {time.monotonic() - t0:.1f}s, finish_reason={_finish(js)}"
                + ("" if "391" in (msg.get("content") or "") else " (answer did not contain 391 — quality is "
                   "not this probe's job)"), required=True, usage=(js or {}).get("usage"))
    else:
        rep.add("chat", "fail", f"no usable reply: {_err(st, raw)}", required=True)
    where = reasoning_where(msg) if st == 200 else None

    # stream ----------------------------------------------------------------
    st2, chunks, done, err = cl.stream(dict(base, stream=True, max_tokens=512, messages=[
        {"role": "user", "content": "Write one short sentence about the sea."}]))
    fields, text = set(), ""
    finish = None
    for c in chunks:
        for ch in c.get("choices") or []:
            d = ch.get("delta") or {}
            fields.update(k for k, v in d.items() if v not in (None, "", []))
            text += d.get("content") or ""
            finish = ch.get("finish_reason") or finish
    if st2 == 200 and chunks and (done or finish):
        rep.add("stream", "pass", f"{len(chunks)} SSE chunk(s), delta fields {sorted(fields)}, "
                f"{'[DONE]' if done else 'no [DONE] but finish_reason ' + str(finish)}", required=True)
    else:
        rep.add("stream", "fail", f"status {st2}, {len(chunks)} chunk(s), done={done} {err}", required=True)
    if not where:
        where = "reasoning_content" if "reasoning_content" in fields else "reasoning" if "reasoning" in fields \
            else ("inline <think> in content" if "<think>" in text else None)

    # reasoning --------------------------------------------------------------
    rep.facts["reasoning_field"] = where
    if where == "reasoning_content":
        rep.add("reasoning", "pass", "thinking arrives in reasoning_content (what llama-server sends; Open WebUI "
                "and the runner already handle it)")
    elif where == "reasoning":
        rep.add("reasoning", "warn", "thinking arrives in `reasoning` (vLLM's current field name), not "
                "reasoning_content — check Open WebUI renders it (docs/DGX_SPARK_PLAN.md acceptance list)")
    elif where:
        rep.add("reasoning", "warn", "thinking is INLINE in content (<think>…): no reasoning parser is active. "
                "vLLM: set REASONING_PARSER in the profile; otherwise the runner replays thinking into history")
    else:
        rep.add("reasoning", "unknown", "no reasoning seen on this prompt (not a thinking model, thinking off by "
                "default, or the parser drops it)")

    # tools -----------------------------------------------------------------
    user = (f"Use the bash tool to run exactly this command: echo {MARKER_TEXT}\n"
            "Do not answer until you have the tool's output.")
    req = dict(base, messages=[{"role": "user", "content": user}], tools=tools, tool_choice="auto",
               max_tokens=4096)
    st, js, raw = cl.call("POST", "/v1/chat/completions", req)
    if st != 200:
        rep.add("tools", "fail", f"a request with tools was refused: {_err(st, raw)} (vLLM needs "
                "--enable-auto-tool-choice --tool-call-parser …)", required=True)
        rep.add("tool_result", "skip", "no tool call to answer", required=True)
    else:
        m1 = _msg(js)
        status, detail, data = check_tool_calls(m1, tools)
        rep.facts["tool_arguments_string_values"] = bool(data.get("string_values"))
        # a caveat (string values, no id) is still a working protocol: pass, and say so
        rep.add("tools", "pass" if status == "warn" else status,
                ("with a caveat: " + detail) if status == "warn" else detail,
                required=True, finish_reason=_finish(js), **data)
        if status in ("pass", "warn"):
            call = (m1.get("tool_calls") or [])[0]
            follow = [{"role": "user", "content": user},
                      {"role": "assistant", "content": m1.get("content") or "", "tool_calls": m1.get("tool_calls")},
                      {"role": "tool", "tool_call_id": call.get("id") or "call_0",
                       "content": f"{MARKER_TEXT}\n"}]
            st, js2, raw = cl.call("POST", "/v1/chat/completions", dict(base, messages=follow, tools=tools,
                                                                          tool_choice="auto", max_tokens=4096))
            m2 = _msg(js2)
            if st == 200 and (m2.get("content") or "").strip():
                quoted = MARKER_TEXT in m2["content"]
                rep.add("tool_result", "pass", "final answer after the tool result"
                        + ("" if quoted else " (it did not quote the output — fine for the protocol)"),
                        required=True)
            elif st == 200 and m2.get("tool_calls"):
                rep.add("tool_result", "pass", "accepted the tool result and chose to call again (protocol OK)",
                        required=True)
            else:
                rep.add("tool_result", "fail", f"the tool result was not accepted: {_err(st, raw)}", required=True)
        else:
            rep.add("tool_result", "skip", "no structured tool call to answer", required=True)

    # parallel ----------------------------------------------------------------
    st, js, raw = cl.call("POST", "/v1/chat/completions", dict(base, messages=[{"role": "user", "content": (
        "Read BOTH files /etc/hostname and /etc/os-release with the read_file tool. Make both calls in this "
        "one turn, in parallel; do not answer yet.")}], tools=tools, tool_choice="auto", parallel_tool_calls=True,
        max_tokens=4096))
    n = len(_msg(js).get("tool_calls") or []) if st == 200 else 0
    rep.facts["parallel_tool_calls"] = n
    if n >= 2:
        rep.add("parallel", "pass", f"{n} tool calls in one turn")
    elif n == 1:
        rep.add("parallel", "warn", "one call per turn (serial): the runner copes; it is slower")
    else:
        rep.add("parallel", "unknown", f"no tool calls on the parallel prompt ({_err(st, raw) if st != 200 else 'answered in text'})")

    # max_tokens -----------------------------------------------------------------
    st, js, raw = cl.call("POST", "/v1/chat/completions", dict(base, max_tokens=16, messages=[
        {"role": "user", "content": "Count from 1 to 300, separated by spaces."}]))
    ct = ((js or {}).get("usage") or {}).get("completion_tokens")
    fr = _finish(js)
    if st == 200 and fr == "length" and (ct is None or ct <= 16):
        rep.add("max_tokens", "pass", f"stopped at the cap (finish_reason=length, completion_tokens={ct})")
    elif st == 200 and ct is not None and ct > 16:
        rep.add("max_tokens", "fail", f"max_tokens=16 produced {ct} tokens — the runner's per-turn bound "
                "(and TensorFold CUDA's only thinking bound) does not hold")
    else:
        rep.add("max_tokens", "unknown", f"status {st}, finish_reason={fr}, completion_tokens={ct}")

    # overflow --------------------------------------------------------------------
    if not heavy:
        rep.add("overflow", "skip", "needs --heavy (sends a prompt longer than max_model_len)")
    elif not mml:
        rep.add("overflow", "skip", "max_model_len is not exposed; cannot size an overflow prompt")
    else:
        pattern = runner_overflow_re()
        st, js, raw = cl.call("POST", "/v1/chat/completions", dict(base, max_tokens=16, messages=[
            {"role": "user", "content": "x " * (mml * 2 + 1024)}]))
        if st == 200:
            rep.add("overflow", "fail", f"a {mml * 2}-token prompt was ACCEPTED (silent truncation?)")
        elif pattern is None:
            rep.add("overflow", "unknown", f"HTTP {st}; agents/runner.py's pattern could not be read: {raw[:200]}")
        elif pattern.search(raw):
            rep.add("overflow", "pass", f"HTTP {st}; runner.py's _CTX_OVERFLOW_RE recognises it (reactive "
                    "compaction will fire)")
        else:
            rep.add("overflow", "fail", f"HTTP {st} text is NOT recognised by runner.py's _CTX_OVERFLOW_RE: "
                    f"{raw[:240]}")

    # concurrency -----------------------------------------------------------------
    if concurrency > 1:
        out: list[int] = []
        lock = threading.Lock()

        def one():
            s, _, _ = cl.call("POST", "/v1/chat/completions", dict(base, max_tokens=32, messages=[
                {"role": "user", "content": "Say hello."}]))
            with lock:
                out.append(s)

        t0 = time.monotonic()
        th = [threading.Thread(target=one) for _ in range(concurrency)]
        for t in th:
            t.start()
        for t in th:
            t.join()
        good = sum(1 for s in out if s == 200)
        rep.add("concurrency", "pass" if good == concurrency else "fail",
                f"{good}/{concurrency} parallel requests succeeded in {time.monotonic() - t0:.1f}s")
    else:
        rep.add("concurrency", "skip", "--concurrency N to run N requests at once")

    # recommendations -------------------------------------------------------------
    rep.facts["backend"] = backend
    rec = rep.recommend
    rec.append(f"INFERENCE_MODEL={model}   (scripts/backends/use-model.sh records it in openbeast.conf)")
    if rep.facts.get("strict_model_names"):
        rec.append("Strict model names: opencode.json / clients must send exactly this id (use-model.sh prints "
                   "the opencode provider snippet), or run vLLM with VLLM_SKIP_MODEL_NAME_VALIDATION=1 "
                   "(the Spark launcher does)")
    if mml:
        rec.append(f"Context window {mml}: opencode limit.context={mml}; keep the runner's compaction default")
    if rep.facts.get("tool_arguments_string_values"):
        rec.append("Tool argument values arrive as strings (the server does no schema coercion). Handled: "
                   "agents/tools.py coerce_args converts them by TOOL_SCHEMAS in the runner; the MCP server and "
                   "the :3001 tool server coerce through their pydantic argument models. Nothing to set")
    if where == "reasoning":
        rec.append("Reasoning is in `reasoning`: confirm Open WebUI shows the thinking block")
    if where and where.startswith("inline"):
        rec.append("Set REASONING_PARSER in the profile (model-inspect.sh suggests one) so thinking leaves content")
    rec.append("Open WebUI needs no change when /v1/models lists the model (it does)" if ids
               else "Open WebUI will not list a model until /v1/models does")


def render(rep: Report, url: str) -> str:
    L = [f"Conformance — {rep.facts.get('model', '?')} @ {url}  ({datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC})",
         f"tool schemas: {rep.facts.get('tool_schemas_from', '?')}", ""]
    for r in rep.results:
        L.append(f"  {r['status'].upper():8} {r['name']:12}{' *' if r['required'] else '  '} {r['detail']}")
    L += ["", "  * = required by OpenBeast (chat, stream, tools)", "",
          "VERDICT: " + ("PASS — OpenBeast can use this server" if rep.ok else
                         "FAIL — a required probe failed; see above"), "", "Recommended settings:"]
    L += [f"  - {x}" for x in rep.recommend]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("OPENBEAST_INFERENCE_URL") or os.environ.get("INFERENCE_URL"))
    ap.add_argument("--model")
    ap.add_argument("--backend", default=os.environ.get("OPENBEAST_INFERENCE_BACKEND")
                    or os.environ.get("INFERENCE_BACKEND") or "vllm", choices=("vllm", "tensorfold", "llama"))
    ap.add_argument("--key-file")
    ap.add_argument("--out", type=Path, default=REPO / ".run" / "conformance")
    ap.add_argument("--heavy", action="store_true")
    ap.add_argument("--concurrency", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--json", action="store_true", help="print the JSON report instead of the text one")
    a = ap.parse_args(argv)
    if not a.url or not re.match(r"^https?://", a.url):
        print("Error: --url http(s)://host:port (or INFERENCE_URL in openbeast.conf)", file=sys.stderr)
        return 2
    cl = Client(a.url, read_key(a.key_file, a.backend), a.timeout)
    rep = Report()
    run(cl, a.model, a.backend, a.heavy, a.concurrency, rep)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(rep.facts.get("model") or "unknown"))[:60]
    doc = {"url": cl.base, "when": stamp, "ok": rep.ok, "facts": rep.facts, "results": rep.results,
           "recommendations": rep.recommend}
    text = render(rep, cl.base)
    try:
        a.out.mkdir(parents=True, exist_ok=True)
        jp = a.out / f"conformance-{stamp}-{slug}.json"
        jp.write_text(json.dumps(doc, indent=1) + "\n")
        (a.out / f"conformance-{stamp}-{slug}.txt").write_text(text + "\n")
        (a.out / "latest.json").write_text(json.dumps(doc, indent=1) + "\n")
        where = f"\nReport: {jp}"
    except OSError as e:
        where = f"\n(could not write the report to {a.out}: {e})"
    print(json.dumps(doc, indent=1) if a.json else text + where)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
