#!/usr/bin/env python3
"""Decode / prefill tok/s of an OpenAI-compatible endpoint, and the engine gate.

docs/WEIGHT_STREAMING.md. Stdlib only: it runs on a closed rig.

    tokps.py measure --url http://127.0.0.1:8080 [--runs 3] [--max-tokens 256]
                     [--prompt short|long] [--label NAME] [--record FILE] [--json]
    tokps.py check   --min 55 --url ...       measure, exit 1 below the floor
    tokps.py gate    --engine maya --candidate maya.json --baseline llama.json
                     [--ratio 1.5] [--bind KEY=VALUE ...] [--out FILE]
                     exit 0 when candidate / baseline >= ratio, else 1

How a run is measured: one streamed chat completion, temperature 0, a fixed
seed. Decode tok/s is llama.cpp's own `timings.predicted_per_second` when the
server reports it (the final chunk of a llama-server stream), else
(completion_tokens - 1) / (last token time - first token time) from the
stream; the stream rate is recorded either way. TTFT is excluded, so prefill
and decode are not mixed. Prefill tok/s is `timings.prompt_per_second` when
reported, else none. The first run warms the caches (the MoE cache fills on
use) and is reported but not counted; the result is the median of the rest.

No run replays another. Run i opens with its own header line and asks
question i of a fixed rotation (QUESTIONS), with "cache_prompt": false: an
identical request let llama-server's prompt cache (and Maya's conversation
cache) re-evaluate ~1 token, so the "prefill" of counted runs was cache
reuse, and greedy decoding replayed the warm-up's exact expert trace into
the MoE cache. The rotation is fixed, so two engines or two days still see
the same work. A run whose prompt_tokens shows a cache hit anyway (under half
the longest run's) keeps its decode rate but not its prefill.

The gate compares like with like: server timings when both records have
them, else both stream rates. It refuses records whose completion lengths
differ by more than 10%, and a candidate whose self-reported rate is more
than 25% above what the client clock saw.

The API key is read from the environment variable named by --api-key-env
(LLAMA_API_KEY by default) or from --api-key-file, never from argv.

Exit codes: 0 ok, 1 below the floor / gate failed, 2 usage or a bad record,
3 the endpoint failed.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import statistics
import sys
import time
import urllib.error
import urllib.request

# A fixed rotation of questions, so two engines or two days see the same
# work, while no counted run repeats the warm-up (or another run) byte for
# byte: run i asks QUESTIONS[i % len(QUESTIONS)].
QUESTIONS = (
    "Write a detailed, technical explanation of how a hash map handles collisions, "
    "covering separate chaining, open addressing, load factors and resizing. "
    "Use plain paragraphs.",
    "Explain in detail how a B-tree stays balanced on insert and delete, covering node "
    "splits, merges, borrowing from siblings and why disk-based databases use it. "
    "Use plain paragraphs.",
    "Describe step by step how TCP establishes, uses and tears down a connection, "
    "covering the handshake, sequence numbers, flow control, congestion control and "
    "the TIME_WAIT state. Use plain paragraphs.",
    "Explain how a garbage collector based on tri-colour marking works, covering roots, "
    "write barriers, incremental and concurrent collection, and compaction. "
    "Use plain paragraphs.",
)
_LONG_UNIT = ("The lighthouse keeper logged the wind, the tide and every ship that passed, "
              "and never once wrote a line he had not seen with his own eyes. ")
PROMPTS = ("long", "short")
SEED = 42


def prompt_for(kind: str, i: int) -> str:
    """Run i's prompt. The header comes FIRST, so the prompt differs from every
    other run's within its first tokens (no prefix-cache reuse); "long" puts
    ~8K tokens of context before the question: decode with a long KV, and a
    prefill large enough that a prompt batch streams every expert."""
    head = f"Measurement run {i}. Answer the question at the end.\n\n"
    q = QUESTIONS[i % len(QUESTIONS)]
    return head + ((_LONG_UNIT * 400) + "\n\n" + q if kind == "long" else q)


def _key(args) -> str:
    if args.api_key_file:
        with open(args.api_key_file, encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get(args.api_key_env or "LLAMA_API_KEY", "").strip()


def _req(url: str, key: str, body: dict | None = None, timeout: float = 600.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    return urllib.request.urlopen(req, timeout=timeout)


def served_model(base: str, key: str) -> str:
    try:
        with _req(base + "/v1/models", key, timeout=10) as r:
            d = json.load(r)
        return ((d.get("data") or [{}])[0] or {}).get("id") or ""
    except (urllib.error.URLError, OSError, ValueError):
        return ""


def one_run(base: str, key: str, model: str, prompt: str, max_tokens: int) -> dict:
    body = {
        "model": model or "default",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "seed": SEED,
        "cache_prompt": False,       # llama-server: evaluate the whole prompt every run
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    t0 = time.monotonic()
    first = last = None
    n_chunks = 0
    usage = {}
    timings = {}
    with _req(base + "/v1/chat/completions", key, body) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                ev = json.loads(payload)
            except ValueError:
                continue
            if ev.get("usage"):
                usage = ev["usage"]
            if ev.get("timings"):
                timings = ev["timings"]
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content") or d.get("reasoning_content") or d.get("reasoning"):
                    now = time.monotonic()
                    first = first if first is not None else now
                    last = now
                    n_chunks += 1
    t_end = time.monotonic()
    ntok = int(usage.get("completion_tokens") or timings.get("predicted_n") or n_chunks)
    out = {"completion_tokens": ntok, "ttft_s": None if first is None else round(first - t0, 3),
           "wall_s": round(t_end - t0, 3), "source": "stream"}
    # The client's own clock, always: the gate falls back to it when the two
    # engines do not both report server timings, and checks a self-report
    # against it.
    if first is not None and last is not None and last > first and ntok > 1:
        out["stream_tps"] = round((ntok - 1) / (last - first), 2)
    else:
        out["stream_tps"] = None
    if timings.get("predicted_per_second"):
        out["decode_tps"] = round(float(timings["predicted_per_second"]), 2)
        out["source"] = "server-timings"
    else:
        out["decode_tps"] = out["stream_tps"]
    pp = timings.get("prompt_per_second")
    out["prefill_tps"] = round(float(pp), 1) if pp else None
    if timings.get("prompt_n") is not None:
        out["prompt_tokens"] = int(timings["prompt_n"])
    elif usage.get("prompt_tokens") is not None:
        out["prompt_tokens"] = int(usage["prompt_tokens"])
    return out


def measure(args) -> dict:
    base = args.url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    key = _key(args)
    model = args.model or served_model(base, key)
    runs = []
    for i in range(args.runs + 1):        # run 0 is the warm-up
        try:
            runs.append(one_run(base, key, model, prompt_for(args.prompt, i), args.max_tokens))
        except urllib.error.HTTPError as e:
            raise SystemExit(_fail(3, f"{base}: HTTP {e.code} on /v1/chat/completions"
                                      f"{' (LLAMA_API_KEY?)' if e.code in (401, 403) else ''}"))
        except (urllib.error.URLError, OSError) as e:
            raise SystemExit(_fail(3, f"{base}: {e}"))
        if not args.json:
            r = runs[-1]
            print(f"  run {i}{' (warm-up, not counted)' if i == 0 else ''}: "
                  f"decode {r['decode_tps']} tok/s, prefill {r['prefill_tps']} tok/s "
                  f"over {r.get('prompt_tokens', '?')} prompt tokens, "
                  f"TTFT {r['ttft_s']} s, {r['completion_tokens']} tokens [{r['source']}]", flush=True)
    counted_runs = [r for r in runs[1:] if r["decode_tps"]]
    if not counted_runs:
        raise SystemExit(_fail(3, f"{base}: no run produced a decode rate (empty replies?)"))
    counted = [r["decode_tps"] for r in counted_runs]
    stream = [r["stream_tps"] for r in counted_runs if r.get("stream_tps")]
    sources = {r["source"] for r in counted_runs}
    # A prefill over a fraction of the prompt is a cache hit, not a prefill.
    longest = max((r.get("prompt_tokens") or 0) for r in runs[1:])
    pre = []
    for r in runs[1:]:
        if not r["prefill_tps"]:
            continue
        if longest and r.get("prompt_tokens") is not None and r["prompt_tokens"] * 2 < longest:
            r["prefill_cached"] = True
            continue
        pre.append(r["prefill_tps"])
    return {
        "schema": "openbeast-tokps/1",
        "label": args.label or "",
        "engine": args.engine or "",
        "profile": args.profile or "",
        "url": base,
        "model": model,
        "prompt": args.prompt,
        "max_tokens": args.max_tokens,
        "runs": runs,
        "decode_tps_median": round(statistics.median(counted), 2),
        "decode_source": sources.pop() if len(sources) == 1 else "mixed",
        "stream_tps_median": round(statistics.median(stream), 2) if stream else None,
        "completion_tokens_median": statistics.median(r["completion_tokens"] for r in counted_runs),
        "prefill_tps_median": round(statistics.median(pre), 1) if pre else None,
        "host": platform.node(),
        "time": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _fail(code: int, msg: str) -> int:
    print(f"tokps: {msg}", file=sys.stderr)
    return code


def _write(path: str, doc: dict) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError) as e:
        raise SystemExit(_fail(2, f"cannot read {path}: {e}"))
    if d.get("schema") != "openbeast-tokps/1" or not isinstance(d.get("decode_tps_median"), (int, float)):
        raise SystemExit(_fail(2, f"{path} is not a tokps measurement record"))
    return d


def gate(args) -> int:
    cand, base = _load(args.candidate), _load(args.baseline)
    problems = []
    if cand["prompt"] != base["prompt"] or cand["max_tokens"] != base["max_tokens"]:
        problems.append("the two runs used different prompts or lengths")
    if cand.get("host") != base.get("host"):
        problems.append(f"measured on different hosts ({cand.get('host')} vs {base.get('host')})")
    # Like with like. Server timings only when BOTH report them; otherwise
    # the client's stream clock for both (a record from before these fields
    # existed counts as its decode median, measured how it says).
    c_src, b_src = cand.get("decode_source"), base.get("decode_source")
    c_rate, b_rate, method = cand["decode_tps_median"], base["decode_tps_median"], c_src or "as recorded"
    if c_src != b_src or c_src == "mixed":
        c_rate, b_rate, method = cand.get("stream_tps_median"), base.get("stream_tps_median"), "stream"
        if not (c_rate and b_rate):
            problems.append(f"decode rates measured differently ({c_src} vs {b_src}) and no stream "
                            "rate on both sides to compare instead")
            c_rate = b_rate = None
    c_n, b_n = cand.get("completion_tokens_median"), base.get("completion_tokens_median")
    if c_n and b_n and abs(c_n - b_n) > 0.10 * max(c_n, b_n):
        problems.append(f"completion lengths differ by more than 10% ({c_n} vs {b_n} tokens): "
                        "a shorter reply decodes on a shorter KV")
    c_stream = cand.get("stream_tps_median")
    if c_src == "server-timings" and c_stream and cand["decode_tps_median"] > 1.25 * c_stream:
        problems.append(f"the candidate reports {cand['decode_tps_median']} tok/s but the client saw "
                        f"{c_stream}: its self-reported timings are not trusted")
    bound = {}
    for kv in args.bind:
        k, sep, v = kv.partition("=")
        if not sep or not k:
            raise SystemExit(_fail(2, f"--bind {kv!r} is not KEY=VALUE"))
        bound[k] = v
    ratio = c_rate / b_rate if c_rate and b_rate else 0.0
    ok = not problems and ratio >= args.ratio
    verdict = {
        "schema": "openbeast-engine-gate/1",
        "engine": args.engine,
        "pass": ok,
        "ratio": round(ratio, 3),
        "required": args.ratio,
        "candidate_tps": c_rate,
        "baseline_tps": b_rate,
        "compared_by": method,
        "candidate": {k: cand.get(k) for k in ("label", "engine", "profile", "model", "time", "url")},
        "baseline": {k: base.get(k) for k in ("label", "engine", "profile", "model", "time", "url")},
        "prompt": cand["prompt"],
        "host": cand.get("host"),
        # What this verdict admits (lib/engine.sh ob_engine_gate_ok compares
        # it with what is about to run): the engine-gate.sh binding.
        "bound": bound,
        "problems": problems,
        "time": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if args.out:
        _write(args.out, verdict)
    word = "PASS" if ok else "FAIL"
    print(f"engine gate {args.engine}: {word} — {c_rate} vs "
          f"{b_rate} tok/s ({method}) = {ratio:.2f}x (needs {args.ratio}x)"
          + (f"; {'; '.join(problems)}" if problems else ""))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("measure", "check"):
        p = sub.add_parser(name)
        p.add_argument("--url", required=True, help="base URL (http://127.0.0.1:8080)")
        p.add_argument("--model", default="", help="model id to send (default: the first /v1/models id)")
        p.add_argument("--runs", type=int, default=3, help="counted runs after one warm-up (default 3)")
        p.add_argument("--max-tokens", type=int, default=256)
        p.add_argument("--prompt", choices=PROMPTS, default="short")
        p.add_argument("--label", default="")
        p.add_argument("--engine", default="")
        p.add_argument("--profile", default="")
        p.add_argument("--record", default="", help="write the measurement JSON here")
        p.add_argument("--json", action="store_true", help="print the record as JSON")
        p.add_argument("--api-key-env", default="LLAMA_API_KEY")
        p.add_argument("--api-key-file", default="")
        if name == "check":
            p.add_argument("--min", type=float, required=True, help="decode tok/s floor")
    g = sub.add_parser("gate")
    g.add_argument("--engine", required=True)
    g.add_argument("--candidate", required=True)
    g.add_argument("--baseline", required=True)
    g.add_argument("--ratio", type=float, default=1.5)
    g.add_argument("--bind", action="append", default=[], metavar="KEY=VALUE",
                   help="record what this verdict admits (repeatable): engine-gate.sh passes "
                        "the adapter's binding (commit, profile, weights)")
    g.add_argument("--out", default="")
    args = ap.parse_args(argv)
    if args.cmd == "gate":
        return gate(args)
    if args.runs < 1 or args.max_tokens < 8:
        return _fail(2, "--runs must be >= 1 and --max-tokens >= 8")
    rec = measure(args)
    if args.record:
        _write(args.record, rec)
    if args.json:
        print(json.dumps(rec, indent=2, sort_keys=True))
    else:
        print(f"decode {rec['decode_tps_median']} tok/s (median of {args.runs}), "
              f"prefill {rec['prefill_tps_median']} tok/s — {rec['model'] or '?'} at {rec['url']}")
    if args.cmd == "check":
        if rec["decode_tps_median"] < args.min:
            print(f"BELOW THE FLOOR: {rec['decode_tps_median']} < {args.min} tok/s", file=sys.stderr)
            return 1
        print(f"ok: {rec['decode_tps_median']} >= {args.min} tok/s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
