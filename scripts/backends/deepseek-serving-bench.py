#!/usr/bin/env python3
"""DeepSeek serving bench (2026-11-25), stdlib only, through beast-gate by default.
  dsserve.py LABEL [--only a,b,c,d] [--url URL] [--key-file F] [--out FILE]
(a) 1 stream short decode: 3 runs (after 1 warm-up), max_tokens 512, T=0, thinking off
(b) 4 and 8 short streams at once: aggregate tok/s, per-stream median/min, TTFT
(c) long mix like Max's job: 6 x ~60K prompts arriving 1 s apart (S0, S2, S4 share a 40K corpus, in the system message by default; U1, U3, U5
    unique), no max_tokens (the server's 32768 reservation, as Max's job), plus one short probe stream decoding
    throughout (its inter-chunk gaps = what a generating stream sees during long prefills); then a follow-up S9
    sharing the same 40K prefix after the load (does the kept prefix survive?)
(d) a short request during a long prefill: 1 stream decoding, a cold 60K prompt, 3 s later a short request
Every prompt carries a per-run salt so nothing is cached from an earlier run, except what the run itself shares."""
import json, os, random, ssl, statistics as st, sys, threading, time, urllib.request

a = sys.argv[1:]
label = a[0]
def opt(n, d=None):
    return a[a.index(n) + 1] if n in a else d
URL = opt("--url", "https://spark-1.tail4109f9.ts.net:8443").rstrip("/")
KEY = open(os.path.expanduser(opt("--key-file", "~/.config/openbeast/spark-hydra-gate.key"))).read().strip()
OUT = opt("--out", "/tmp/dsopt/serve.jsonl")
ONLY = set(opt("--only", "a,b,c,d").split(","))
MODEL = "DeepSeek-V4.1-Flash-1017"
SALT = f"{label}-{int(time.time())}-{random.randrange(1 << 30)}"
CTX = ssl.create_default_context()

def stream(messages, max_tokens=None, events=None, tag=""):
    """One streamed chat; returns TTFT, chunk times, usage. events: list to append (t, tag) per content chunk."""
    body = {"model": MODEL, "messages": messages, "stream": True, "temperature": 0, "seed": 42,
            "stream_options": {"include_usage": True}, "chat_template_kwargs": {"enable_thinking": False}}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    req = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + KEY)
    req.add_header("X-OpenBeast-Gate-Defaults", "off")
    t0 = time.monotonic()
    first = None; times = []; usage = None; finish = None; err = None; text = []
    try:
        with urllib.request.urlopen(req, timeout=3600, context=CTX) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                d = line[5:].strip()
                if d == "[DONE]":
                    break
                j = json.loads(d)
                if j.get("usage"):
                    usage = j["usage"]
                for c in j.get("choices") or []:
                    delta = c.get("delta") or {}
                    piece = (delta.get("content") or "") + (delta.get("reasoning_content") or "")
                    if piece:
                        now = time.monotonic()
                        if first is None:
                            first = now
                        times.append(now)
                        text.append(piece)
                        if events is not None:
                            events.append((now, tag))
                    if c.get("finish_reason"):
                        finish = c["finish_reason"]
    except Exception as e:  # noqa: BLE001
        err = repr(e)
    t1 = time.monotonic()
    ct = (usage or {}).get("completion_tokens")
    pt = (usage or {}).get("prompt_tokens")
    cached = ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens")
    gaps = [y - x for x, y in zip(times, times[1:])]
    dec = (ct - 1) / (times[-1] - first) if ct and len(times) > 1 and times[-1] > first else None
    return {"tag": tag, "t0": t0, "ttft": None if first is None else round(first - t0, 3), "wall": round(t1 - t0, 3),
            "prompt_tokens": pt, "cached": cached, "completion_tokens": ct, "decode_tps": None if dec is None else round(dec, 2),
            "gap_max": round(max(gaps), 3) if gaps else None, "gap_p50": round(st.median(gaps), 3) if gaps else None,
            "finish": finish, "error": err, "text_head": "".join(text)[:60]}

def short(i, n=512):
    return [{"role": "user", "content": f"[{SALT} s{i}] Write a long, detailed story about a lighthouse keeper named Ada {i}. Keep going; do not stop early."}], n

WORDS = "amber basalt cedar delta ember fjord granite harbor indigo juniper kestrel lantern meridian nimbus onyx prairie quartz raven sierra tundra umber vesper willow xenon yarrow zephyr".split()
def doc(seed, n_records):
    r = random.Random(seed)
    return "\n".join(f"Record {seed}-{k}: the {r.choice(WORDS)} {r.choice(WORDS)} unit at station {r.randrange(10**5)} logged {r.randrange(10**6)} readings, flagged {r.choice(WORDS)}." for k in range(n_records))

# 29.3 tokens a record (/tokenize, 2026-11-25); each request also reports usage.prompt_tokens
REC = 29
SHARED_IN = opt("--shared-in", "system")  # system: the shared corpus before the first user message (the engine keeps cuts there)
def long_prompt(shared_seed, tail_seed, shared_tok=40000, total_tok=60000, q=0):
    sysmsg = "You are a careful analyst. Answer in one short sentence."
    ask = f"\n\nQuestion {q}: in one sentence, name the colour words of the first record of case file {tail_seed}."
    if shared_seed is None:
        body = f"Case file {tail_seed} ({SALT}):\n" + doc(f"{SALT}-U{tail_seed}", total_tok // REC)
        return [{"role": "system", "content": sysmsg}, {"role": "user", "content": body + ask}]
    corpus = f"Reference corpus {SALT}-shared:\n" + doc(f"{SALT}-P{shared_seed}", shared_tok // REC)
    tail = f"Case file {tail_seed}:\n" + doc(f"{SALT}-T{tail_seed}", (total_tok - shared_tok) // REC)
    if SHARED_IN == "system":
        return [{"role": "system", "content": sysmsg + "\n\n" + corpus}, {"role": "user", "content": tail + ask}]
    return [{"role": "system", "content": sysmsg}, {"role": "user", "content": corpus + "\n\n" + tail + ask}]

def conc(n, off=10):
    res = [None] * n
    def go(i):
        m, mt = short(off + i)
        res[i] = stream(m, mt, tag=f"b{i}")
    th = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    t0 = time.monotonic()
    for t in th: t.start()
    for t in th: t.join()
    wall = time.monotonic() - t0
    ok = [r for r in res if not r["error"]]
    per = [r["decode_tps"] for r in ok if r["decode_tps"]]
    return {"n": n, "agg": round(sum(r["completion_tokens"] or 0 for r in ok) / wall, 2),
            "per_med": round(st.median(per), 2) if per else None, "per_min": min(per) if per else None,
            "ttft_med": round(st.median([r["ttft"] for r in ok]), 3) if ok else None,
            "ttft_max": max(r["ttft"] for r in ok) if ok else None, "errors": [r["error"] for r in res if r["error"]]}

def probe_loop(stop, out):
    """A short-prompt stream decoding back to back until stop: its chunk gaps."""
    i = 0
    while not stop.is_set():
        m, mt = short(900 + i, 400)
        out.append(stream(m, mt, tag=f"probe{i}"))
        i += 1

out = {"label": label, "shared_in": SHARED_IN, "url": URL, "salt": SALT, "when": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
def save():
    with open(OUT, "a") as fh:
        fh.write(json.dumps(out) + "\n")

if "a" in ONLY:
    stream(*short(0, 64), tag="warm")
    runs = [stream(*short(1 + k), tag=f"a{k}") for k in range(3)]
    out["a"] = {"decode_med": st.median([r["decode_tps"] for r in runs if r["decode_tps"]]),
                "ttft_med": st.median([r["ttft"] for r in runs]), "runs": [r["decode_tps"] for r in runs]}
    print(label, "a", json.dumps(out["a"]), flush=True)
if "b" in ONLY:
    out["b"] = {n: conc(n) for n in (4, 8)}
    print(label, "b", json.dumps(out["b"]), flush=True)
if "c" in ONLY:
    stop = threading.Event(); probes = []
    pt = threading.Thread(target=probe_loop, args=(stop, probes)); pt.start()
    time.sleep(2.0)
    specs = [("S0", 1, 0), ("U1", None, 1), ("S2", 1, 2), ("U3", None, 3), ("S4", 1, 4), ("U5", None, 5)][:int(opt("--long-n", "6"))]
    res = {}
    def go(tag, sh, tl):
        res[tag] = stream(long_prompt(sh, tl, q=tl), None, tag=tag)
    th = []
    t0 = time.monotonic()
    for tag, sh, tl in specs:
        t = threading.Thread(target=go, args=(tag, sh, tl)); t.start(); th.append(t); time.sleep(1.0)
    for t in th: t.join()
    wall = time.monotonic() - t0
    stop.set(); pt.join()
    f = stream(long_prompt(1, 9, q=9), None, tag="S9-after")
    ptoks = sum((r["prompt_tokens"] or 0) for r in res.values())
    ctoks = sum((r["completion_tokens"] or 0) for r in res.values())
    pg = [g for p in probes for g in ([p["gap_max"]] if p["gap_max"] is not None else [])]
    out["c"] = {"wall": round(wall, 1), "prompt_tokens": ptoks, "prompt_tps_wall": round(ptoks / wall, 1),
                "cached": {k: r["cached"] for k, r in res.items()}, "ttft": {k: r["ttft"] for k, r in res.items()},
                "ptoks": {k: r["prompt_tokens"] for k, r in res.items()}, "ctoks": ctoks,
                "probe_runs": len(probes), "probe_tokens": sum(p["completion_tokens"] or 0 for p in probes),
                "probe_tps_med": st.median([p["decode_tps"] for p in probes if p["decode_tps"]]) if probes else None,
                "probe_gap_max": max(pg) if pg else None,
                "errors": {k: r["error"] for k, r in res.items() if r["error"]},
                "after": {"ttft": f["ttft"], "cached": f["cached"], "prompt_tokens": f["prompt_tokens"], "error": f["error"]},
                "detail": res, "probes": probes}
    print(label, "c", json.dumps({k: v for k, v in out["c"].items() if k not in ("detail", "probes")}), flush=True)
if "d" in ONLY:
    bg = {}
    def dec():
        m, mt = short(700, 1024); bg["r"] = stream(m, mt, tag="d-dec")
    def lng():
        bg["long"] = stream(long_prompt(None, 77, q=7), 32, tag="d-long")
    t1 = threading.Thread(target=dec); t1.start(); time.sleep(3.0)
    t2 = threading.Thread(target=lng); t2.start(); time.sleep(3.0)
    s = stream([{"role": "user", "content": f"[{SALT} d] Say hello in five words."}], 32, tag="d-short")
    t2.join(); t1.join()
    out["d"] = {"short_ttft": s["ttft"], "short_wall": s["wall"], "long_ttft": bg["long"]["ttft"],
                "long_prompt_tokens": bg["long"]["prompt_tokens"], "dec_gap_max": bg["r"]["gap_max"],
                "dec_tps": bg["r"]["decode_tps"], "errors": [x["error"] for x in (s, bg["long"], bg["r"]) if x["error"]]}
    print(label, "d", json.dumps(out["d"]), flush=True)
save()
