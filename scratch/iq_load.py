#!/usr/bin/env python3
"""Concurrent load generator for the IQ-artifact stability repro.
N parallel streams of long chat completions against llama-server; stops
at --minutes or when /health fails. Prints one line per completed request
and a final summary (requests, tokens, time-to-first-failure)."""
import argparse, json, os, sys, time, threading, urllib.request, urllib.error
ap = argparse.ArgumentParser()
ap.add_argument('--url', default='http://127.0.0.1:8080'); ap.add_argument('--concurrency', type=int, default=6)
ap.add_argument('--minutes', type=float, default=120); ap.add_argument('--max-tokens', type=int, default=4096)
a = ap.parse_args()
KEY = os.environ.get('LLAMA_API_KEY', '')
PROMPTS = ["Write a complete, well-commented Rust implementation of a lock-free MPSC queue with tests.",
           "Implement a Zig program that parses a JSON subset and pretty-prints it; include exhaustive tests.",
           "Derive and prove the convergence rate of conjugate gradient on a symmetric positive definite system, step by step.",
           "Write a Go HTTP server with graceful shutdown, structured logging and a rate limiter; explain every design choice.",
           "Design a B+tree in C with insert/delete/range-scan and explain the invariants at length.",
           "Write a Python asyncio task scheduler with priorities, cancellation and backpressure, with a long walkthrough."]
stop = time.time() + a.minutes * 60
lock = threading.Lock(); stats = {'req': 0, 'tok': 0, 'err': 0, 'first_err': None, 'first_err_t': None}
t0 = time.time()
def health():
    try: return urllib.request.urlopen(a.url + '/health', timeout=5).status == 200
    except Exception: return False
def worker(i):
    k = i
    while time.time() < stop:
        body = json.dumps({'model': 'x', 'messages': [{'role': 'user', 'content': PROMPTS[k % len(PROMPTS)]}],
                           'max_tokens': a.max_tokens, 'temperature': 0.7}).encode()
        req = urllib.request.Request(a.url + '/v1/chat/completions', data=body, headers={'Content-Type': 'application/json', **({'Authorization': f'Bearer {KEY}'} if KEY else {})})
        t = time.time()
        try:
            r = json.load(urllib.request.urlopen(req, timeout=3600)); n = r.get('usage', {}).get('completion_tokens', 0)
            with lock: stats['req'] += 1; stats['tok'] += n
            print(f"[{time.time()-t0:7.0f}s] w{i} ok {n} tok in {time.time()-t:.0f}s", flush=True)
        except Exception as e:
            with lock:
                stats['err'] += 1
                if stats['first_err'] is None: stats['first_err'] = repr(e)[:200]; stats['first_err_t'] = time.time() - t0
            print(f"[{time.time()-t0:7.0f}s] w{i} ERROR {repr(e)[:160]}", flush=True)
            if not health(): print(f"[{time.time()-t0:7.0f}s] SERVER UNHEALTHY — stopping load", flush=True); return
            time.sleep(5)
        k += a.concurrency
ths = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(a.concurrency)]
[t.start() for t in ths]
while any(t.is_alive() for t in ths) and time.time() < stop + 5:
    time.sleep(10)
    if not health(): print(f"[{time.time()-t0:7.0f}s] SERVER UNHEALTHY (main loop)", flush=True); break
print(f"SUMMARY requests={stats['req']} tokens={stats['tok']} errors={stats['err']} elapsed={time.time()-t0:.0f}s first_error_at={stats['first_err_t']} first_error={stats['first_err']}", flush=True)
