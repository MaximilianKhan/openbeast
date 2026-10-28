#!/usr/bin/env python3
"""MTP throughput profiler — find the peak tokens/sec deployment config.

WHY THIS IS SAFE WITHOUT RE-VALIDATING ACCURACY
-----------------------------------------------
MTP / speculative decoding is *lossless*: the main model verifies every
drafted token, so the accepted sequence is distributed identically to the
model running alone (empirically confirmed on the leaderboard: 35B-A3B MTP
93.76 vs non-MTP 93.74). Therefore the speculation knobs swept here —
`--spec-draft-n-max` (how many tokens to draft), `--spec-draft-n-min`,
`--spec-draft-p-min` (acceptance floor) — change ONLY throughput, never the
output. We can brute-force them for tok/s with zero accuracy risk and no
eval suite.

WHAT IS HELD FIXED (and must NOT be swept here)
-----------------------------------------------
Weights (quant), KV-cache quant (`q4_0`), context length, `-ngl` — these are
LOSSY. Changing them trades accuracy for speed/VRAM and is NOT covered by the
lossless guarantee; any change needs a full v4 eval sweep to trust. This
profiler pins them to each model's leaderboard config.

OUTPUT
------
Peak-tok/s config per model + a table vs the leaderboard baseline, written to
evals/results/mtp_profile_<stamp>.json. The winning configs are the OPTIMAL
DEPLOYMENT configs (what to actually serve with); the leaderboard configs are
what produced the published scores. Keep both distinct — see
docs/archive/MTP_PROFILING_PLAN.md.

Run AFTER the benchmark sweep finishes (it needs the GPU exclusively):
    python3 evals/profile_mtp.py                 # all 3 MTP models
    python3 evals/profile_mtp.py --models qwopus-27b-v2-mtp-q5
"""
from __future__ import annotations
import argparse, json, os, signal, subprocess, sys, time, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVE = REPO / "scripts" / "serve.sh"
WEIGHTS = os.environ.get("OPENBEAST_WEIGHTS_DIR") or str(REPO / "weights")
RESULTS = REPO / "evals" / "results"
PORT = 8080
HEALTH = f"http://127.0.0.1:{PORT}/health"
CHAT = f"http://127.0.0.1:{PORT}/v1/chat/completions"

# Server ownership and the GPU lease are benchmark_all's: the same "target
# by PID" stop (never `pkill -f llama-server`, which also killed the stack's
# model, a sibling worktree's measurement server, the instinct scorer on
# :8082 and any `tail -f ...llama-server.log`) and the same lease rules.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_all as _ba  # noqa: E402

GpuLeaseHeld = _ba.GpuLeaseHeld
PortBusy = _ba.PortBusy

# Leaderboard configs — the EXACT flags that produced the published v4
# scores. The profiler pins the lossy/quality knobs (context, KV quant,
# ngl, np) to these and sweeps only the lossless spec-* knobs.
MODELS = {
    "qwen-27b-mtp-q5": {
        "weight": "Qwen3.6-27B-MTP-UD-Q5_K_XL.gguf",
        "context": 294912, "bench_n_max": 8, "bench_p_min": 0.0,
    },
    "qwen-35b-a3b-mtp": {
        "weight": "Qwen3.6-35B-A3B-MTP-UD-Q4_K_M.gguf",
        "context": 524288, "bench_n_max": 4, "bench_p_min": 0.0,
    },
    "qwopus-27b-v2-mtp-q5": {
        "weight": "Qwopus3.6-27B-v2-MTP-Q5_K_M.gguf",
        "context": 344064, "bench_n_max": 4, "bench_p_min": 0.0,
    },
}

# Fixed workload — representative coding generations, temperature 0 for
# determinism so tok/s is the only thing that varies between configs.
PROMPTS = [
    "Write a Python function that merges two sorted lists into one sorted list without using sorted(). Include a docstring and a couple of examples.",
    "Implement a least-recently-used (LRU) cache class in Python with O(1) get and put. Explain the data structures you chose.",
    "Write a Go function that computes the SHA-256 hash of a file, streaming it in 64KB chunks. Include error handling.",
    "Explain, then implement in C, the Karatsuba multiplication algorithm for two non-negative integers given as byte arrays.",
]
MAX_TOKENS = 512

# Sweep grid. Phase 1 finds the best draft depth; phase 2 refines the
# acceptance floor at that depth. Keeps it "light": ~11 configs/model.
N_MAX_GRID = [2, 4, 6, 8, 12, 16]
P_MIN_GRID = [0.0, 0.1, 0.25, 0.5]


def stop_server():
    """Stop the server THIS profiler started (its process group), nothing
    else. A no-op when we started none."""
    _ba.stop_llama_server()


def start_server(weight, context, n_max, p_min, n_min=0):
    path = os.path.join(WEIGHTS, weight)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    # Asked before every load: a lease taken mid-profile (acquire --force)
    # must not get a second model loaded into its window.
    _ba.require_gpu_lease()
    # Our previous server is stopped by now, so a bound port is someone
    # else's (the stack, another worktree). Refuse — never kill it, and
    # never measure it as ours.
    if _ba._port_in_use(PORT):
        raise PortBusy(f"port {PORT} is already serving and this profiler did not "
                       f"start it. Stop it by PID (or ./stop.sh for the stack), then "
                       f"rerun.\n{_ba._foreign_servers()}")
    args = [str(SERVE), "-m", path, "-c", str(context), "-np", "1",
            "-ctk", "q4_0", "-ctv", "q4_0",
            "--spec-type", "draft-mtp",
            "--spec-draft-n-max", str(n_max),
            "--spec-draft-p-min", str(p_min)]
    if n_min:
        args += ["--spec-draft-n-min", str(n_min)]
    with open(RESULTS / "mtp_profile_server.log", "a") as log:
        proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
    _ba._own_server["proc"] = proc
    return proc


def ensure_gpu_lease(argv, exec_fn=os.execvpe):
    """Refuse someone else's lease; run on under ours; when the card is
    FREE, re-exec this profiler under `gpu-lease.sh run` so the whole sweep
    holds the card (benchmark_all.ensure_gpu_lease, for this script)."""
    rc = _ba.require_gpu_lease()
    if rc == _ba.LEASE_OURS:
        return
    env = _ba._lease_env()
    if env.get(_ba._REEXEC_MARK):
        raise GpuLeaseHeld("re-exec under `gpu-lease.sh run` did not take the lease "
                           "(check still says FREE) — refusing to run unleased.")
    env[_ba._REEXEC_MARK] = "1"
    label = ("profile_mtp " + " ".join(argv)).strip()
    print(f"GPU lease is free — taking it for this profile: {label}")
    sys.stdout.flush()
    exec_fn("bash", ["bash", _ba.GPU_LEASE_SH, "run", label, "--",
                     sys.executable, os.path.abspath(__file__), *argv], env)


def wait_health(proc, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            raise RuntimeError("llama-server exited during load")
        try:
            with urllib.request.urlopen(HEALTH, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError("server did not become healthy")


def measure(warmup=1, runs=2):
    """Return mean generation tok/s over the fixed workload."""
    # warmup (also primes any caches)
    for _ in range(warmup):
        for p in PROMPTS[:1]:
            _one(p)
    samples = []
    for _ in range(runs):
        for p in PROMPTS:
            t = _one(p)
            if t:
                samples.append(t)
    if not samples:
        return None
    return {
        "gen_tok_s_mean": round(sum(s["gen"] for s in samples) / len(samples), 2),
        "gen_tok_s_max": round(max(s["gen"] for s in samples), 2),
        "prompt_tok_s_mean": round(sum(s["prompt"] for s in samples) / len(samples), 2),
        "n_samples": len(samples),
    }


def _one(prompt):
    # enable_thinking=false so the measured tokens are actual answer decode, not
    # a reasoning block — a cleaner, more representative tok/s for the workload.
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0, "max_tokens": MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request(CHAT, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read())
    except Exception as e:
        print(f"    request failed: {e}", file=sys.stderr)
        return None
    tm = d.get("timings", {})
    return {"gen": tm.get("predicted_per_second", 0.0),
            "prompt": tm.get("prompt_per_second", 0.0),
            "n": tm.get("predicted_n", 0)}


def run_config(m, weight, context, n_max, p_min):
    print(f"  config n_max={n_max} p_min={p_min} ... ", end="", flush=True)
    stop_server()
    res = None
    try:
        proc = start_server(weight, context, n_max, p_min)
        try:
            wait_health(proc)
            res = measure()
        finally:
            stop_server()
    except (GpuLeaseHeld, PortBusy):
        raise                       # not a bad config: stop the whole run
    except Exception as e:
        # A single bad config (e.g. n16 draft buffers OOM at a tight context)
        # must NOT kill the whole run — record it as skipped and continue.
        print(f"skipped ({type(e).__name__}: {e})")
        return {"n_max": n_max, "p_min": p_min, "skipped": str(e)}
    if res:
        print(f"{res['gen_tok_s_mean']} tok/s (gen)")
    else:
        print("no data")
    return {"n_max": n_max, "p_min": p_min, **(res or {})}


def profile_model(slug, cfg):
    print(f"\n=== {slug} (leaderboard: n_max={cfg['bench_n_max']} "
          f"p_min={cfg['bench_p_min']}, c={cfg['context']}, KV q4_0) ===")
    w, c = cfg["weight"], cfg["context"]
    results = []
    # Phase 1: draft depth at p_min=0.0
    for n in N_MAX_GRID:
        results.append(run_config(slug, w, c, n, 0.0))
    best_n = max((r for r in results if r.get("gen_tok_s_mean")),
                 key=lambda r: r["gen_tok_s_mean"], default={"n_max": cfg["bench_n_max"]})["n_max"]
    # Phase 2: acceptance floor at the best depth
    for p in P_MIN_GRID:
        if p == 0.0:
            continue  # already measured in phase 1
        results.append(run_config(slug, w, c, best_n, p))
    scored = [r for r in results if r.get("gen_tok_s_mean")]
    best = max(scored, key=lambda r: r["gen_tok_s_mean"]) if scored else None
    baseline = next((r for r in results if r["n_max"] == cfg["bench_n_max"]
                     and r["p_min"] == cfg["bench_p_min"]), None)
    return {"slug": slug, "leaderboard_config": {"n_max": cfg["bench_n_max"],
            "p_min": cfg["bench_p_min"], "context": c, "kv_quant": "q4_0"},
            "baseline_measured": baseline, "optimal": best, "all_configs": results}


def main():
    ap = argparse.ArgumentParser(description="MTP throughput profiler")
    ap.add_argument("--models", help="comma-separated slugs (default: all 3)")
    args = ap.parse_args()

    # Preflight: never contend with a running sweep for the GPU.
    active = subprocess.run(["systemctl", "--user", "is-active",
                             "openbeast-mtp-sweep"], capture_output=True,
                            text=True).stdout.strip()
    if active == "active":
        sys.exit("REFUSING: openbeast-mtp-sweep is still running — it owns the "
                 "GPU. Wait for it to finish, then re-run this profiler.")
    try:
        ensure_gpu_lease(sys.argv[1:])
    except GpuLeaseHeld as e:
        sys.exit(f"REFUSING: {e}")
    if _ba._port_in_use(PORT):
        sys.exit(f"REFUSING: port {PORT} is already serving (the stack?). Stop it by "
                 f"PID or ./stop.sh, then rerun.\n{_ba._foreign_servers()}")

    def _on_signal(signum, _frame):
        stop_server()                       # ours only; it runs in its own session
        os._exit(128 + signum)
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    RESULTS.mkdir(exist_ok=True)
    slugs = args.models.split(",") if args.models else list(MODELS)
    # Wall-clock stamp so re-runs don't overwrite prior profiling results
    # (the old getmtime(__file__) was constant per file version).
    stamp = os.environ.get("OPENBEAST_STAMP", str(int(time.time())))
    path = RESULTS / f"mtp_profile_{stamp}.json"
    out = []
    for slug in slugs:
        if slug not in MODELS:
            print(f"unknown model {slug}", file=sys.stderr); continue
        try:
            out.append(profile_model(slug, MODELS[slug]))
        except (GpuLeaseHeld, PortBusy) as e:
            stop_server()
            sys.exit(f"REFUSING to continue: {e}")
        except Exception as e:
            print(f"  {slug}: profiling aborted ({type(e).__name__}: {e})", file=sys.stderr)
        # Persist after EACH model so a late failure never loses earlier data.
        # tmp + os.replace: never leave a torn JSON if killed mid-write.
        tmp = Path(str(path) + ".tmp")
        with open(tmp, "w") as f:
            json.dump(out, f, indent=2)
        os.replace(tmp, path)

    print("\n" + "=" * 66)
    print(f"{'model':<24}{'baseline':>12}{'optimal':>12}{'best cfg':>16}")
    print("-" * 66)
    for r in out:
        b = (r.get("baseline_measured") or {}).get("gen_tok_s_mean", "?")
        o = r["optimal"]
        oc = f"n{o['n_max']}/p{o['p_min']}" if o else "?"
        ov = o["gen_tok_s_mean"] if o else "?"
        # {:+.0f} prints an explicit sign either way — a regression shows as
        # (-12%), not the old mangled (+-12%).
        gain = f"({(ov-b)/b*100:+.0f}%)" if isinstance(b,(int,float)) and isinstance(ov,(int,float)) and b else ""
        print(f"{r['slug']:<24}{str(b):>12}{str(ov):>12}{oc+' '+gain:>16}")
    print("=" * 66)
    print(f"\nFull results: {path}")
    print("These 'optimal' configs are for DEPLOYMENT; the leaderboard scores "
          "used the baseline configs. Update serve scripts only after eyeballing "
          "the table — see docs/archive/MTP_PROFILING_PLAN.md.")


if __name__ == "__main__":
    main()
