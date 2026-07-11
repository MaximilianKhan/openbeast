#!/usr/bin/env python3
"""
Multi-model benchmark — run the full eval suite against every model and
produce a ranked leaderboard.

For each model:
  1. Stop any running llama-server
  2. Start the model's serve script in the background
  3. Wait for /health to return ok
  4. Run the full eval suite (results tagged with model name + GPU info)
  5. Score the run and update leaderboard.json
  6. Stop the server cleanly
  7. Cool-off, then move to the next model

If a model crashes (won't start, dies mid-run, or all tasks fail), it is
SKIPPED and flagged — the sweep continues to the next model.

Total runtime estimate: ~30 tasks × ~90s avg × N models ≈ 45 min/model.
A full 5-model sweep takes ~3-4 hours; plan to run it overnight.

Usage:
  python evals/benchmark_all.py                        # all configured models
  python evals/benchmark_all.py --models qwen-27b-q5,gemma-4-31b-q5
  python evals/benchmark_all.py --tasks 21,22,23       # subset of tasks
  python evals/benchmark_all.py --max-iter 30          # cap iterations per task
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(EVALS_DIR)
RESULTS_DIR = os.path.join(EVALS_DIR, "results")

sys.path.insert(0, EVALS_DIR)
import run_eval  # noqa: E402
import scoring   # noqa: E402

# ---------------------------------------------------------------------------
# Models to benchmark — slug, display name, serve script
# ---------------------------------------------------------------------------

MODELS = [
    {"slug": "qwen-27b-q5",
     "name": "Qwen 27B Q5_K_XL",
     "serve": "scripts/serve-qwen-27b-q5.sh"},
    {"slug": "qwen-27b-uncensored-q5",
     "name": "Qwen 27B Uncensored Q5_K_P",
     "serve": "scripts/serve-qwen-27b-uncensored-q5.sh"},
    {"slug": "qwen-35b-a3b",
     "name": "Qwen 35B-A3B MoE Q4_K_M",
     "serve": "scripts/serve-qwen-35b-a3b.sh"},
    {"slug": "qwen-35b-a3b-uncensored-q4",
     "name": "Qwen 35B-A3B Uncensored Q4_K_M",
     "serve": "scripts/serve-qwen-35b-a3b-uncensored-q4.sh"},
    {"slug": "gemma-4-31b-q5",
     "name": "Gemma 4 31B-it Q5_K_XL",
     "serve": "scripts/serve-gemma-4-31b-q5.sh"},
    # MTP + Qwopus rows added 2026-07-07. MTP models pin -np 1 (no parallel
    # slots) — irrelevant for the sweep, which runs tasks sequentially anyway,
    # and the tuned speculative decode makes them faster per token.
    {"slug": "qwen-27b-mtp-q5",
     "name": "Qwen 27B MTP Q5_K_XL",
     "serve": "scripts/serve-qwen-27b-mtp-q5.sh"},
    {"slug": "qwen-35b-a3b-mtp",
     "name": "Qwen 35B-A3B MTP MoE Q4_K_M",
     "serve": "scripts/serve-qwen-35b-a3b-mtp.sh"},
    {"slug": "qwopus-27b-v2-q5",
     "name": "Qwopus 27B v2 Q5_K_M",
     "serve": "scripts/serve-qwopus-27b-v2-q5.sh"},
    {"slug": "qwopus-27b-v2-mtp-q5",
     "name": "Qwopus 27B v2 MTP Q5_K_M",
     "serve": "scripts/serve-qwopus-27b-v2-mtp-q5.sh"},
    # NVFP4 + MTP rows added 2026-07-10 (neko-legends native llama.cpp
    # conversions of unsloth's NVFP4 checkpoints; Blackwell-native FP4 FFNs,
    # FP8->Q8_0 attention, bundled MTP). Configs tuned on the 5090 — see the
    # serve scripts. Speed loses to Q5 MTP at -np 1 (bandwidth-bound); the open
    # question these rows answer is accuracy-per-bit vs the K-quant siblings.
    {"slug": "qwen-27b-nvfp4-mtp",
     "name": "Qwen 27B NVFP4 MTP",
     "serve": "scripts/serve-qwen-27b-nvfp4-mtp.sh"},
    {"slug": "qwen-35b-a3b-nvfp4-mtp",
     "name": "Qwen 35B-A3B NVFP4 MTP",
     "serve": "scripts/serve-qwen-35b-a3b-nvfp4-mtp.sh"},
]

LLAMA_HEALTH_URL = "http://localhost:8080/health"
LLAMA_PORT = 8080
HEALTH_TIMEOUT = 180   # seconds to wait for model load
COOLOFF_SECONDS = 600  # 10-min thermal break between models


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def stop_llama_server():
    """Kill any running llama-server. Tolerant — pkill returns 1 if no match."""
    subprocess.run(["pkill", "-TERM", "-f", "llama-server"], check=False, timeout=5)
    # Wait briefly for graceful shutdown
    for _ in range(10):
        if not _port_in_use(LLAMA_PORT):
            return
        time.sleep(0.5)
    # Escalate
    subprocess.run(["pkill", "-KILL", "-f", "llama-server"], check=False, timeout=5)
    time.sleep(1)


def _port_in_use(port: int) -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        result = s.connect_ex(("127.0.0.1", port))
        return result == 0
    finally:
        s.close()


def _server_log_path(slug: str) -> str:
    """Per-restart server log path. Distinct timestamp on every call so a
    mid-sweep restart leaves a separate file from the original boot."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(RESULTS_DIR, f"server-{slug}-{ts}.log")


def start_model(serve_script: str, slug: str = "model") -> tuple[subprocess.Popen, str]:
    """Launch a serve script in the background. Returns (Popen, log_path).

    llama-server stdout+stderr are tee'd to evals/results/server-{slug}-{ts}.log
    so a silent crash mid-sweep leaves evidence (the v3.5 Gemma run had its
    server die at task 64 and 256 subsequent tasks logged 0 tokens with no
    crash trace anywhere — DEVNULL ate the cause)."""
    full_path = os.path.join(REPO_DIR, serve_script)
    if not os.path.isfile(full_path):
        raise FileNotFoundError(f"Serve script not found: {full_path}")
    log_path = _server_log_path(slug)
    log_fp = open(log_path, "wb", buffering=0)
    print(f"  Server log: {log_path}")
    try:
        proc = subprocess.Popen(
            ["bash", full_path],
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            cwd=REPO_DIR,
            start_new_session=True,
        )
    finally:
        # The child inherited the fd via Popen (when it succeeded); the
        # parent's copy is not needed either way and would otherwise leak
        # one fd per (re)start — including when Popen itself raises.
        log_fp.close()
    return proc, log_path


def wait_for_health(timeout: int = HEALTH_TIMEOUT) -> bool:
    """Poll /health until ok or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(LLAMA_HEALTH_URL, timeout=2) as resp:
                if b"ok" in resp.read():
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def ping_health(timeout: float = 2.0, retries: int = 2, gap: float = 3.0) -> bool:
    """Health probe with cheap retry to absorb transient blips (a request
    saturating slots can briefly delay /health). Returns True if any attempt
    sees 'ok'; only declares the server unhealthy after `retries` consecutive
    misses."""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(LLAMA_HEALTH_URL, timeout=timeout) as resp:
                if b"ok" in resp.read():
                    return True
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(gap)
    return False


def restart_server(serve_script: str, slug: str,
                   health_timeout: int = HEALTH_TIMEOUT) -> bool:
    """Kill llama-server, restart it, wait for /health. Returns True on
    success. Used as the per-task recovery callback when a sweep encounters
    an unhealthy server mid-run."""
    print("  Server unhealthy — restarting...")
    stop_llama_server()
    try:
        start_model(serve_script, slug)
    except FileNotFoundError as e:
        print(f"  Restart failed: {e}")
        return False
    print(f"  Waiting for /health (up to {health_timeout}s)...")
    if not wait_for_health(timeout=health_timeout):
        print("  Server failed to come back healthy.")
        return False
    print("  Server recovered.")
    return True


# ---------------------------------------------------------------------------
# Per-model benchmark
# ---------------------------------------------------------------------------

def benchmark_model(model: dict, task_filter: list[str] | None,
                    max_iter_override: int | None,
                    use_cache: bool = True,
                    cache_only: bool = False) -> dict:
    """Run the full eval suite against one model. Returns a dict with either
    'results' (success) or 'error' (skipped)."""
    print(f"\n{'#' * 60}")
    print(f"# Benchmarking: {model['name']} ({model['slug']})")
    print(f"# Serve: {model['serve']}")
    print(f"{'#' * 60}\n")

    if cache_only:
        # Cache-only mode never talks to the server. Skip start/health/stop.
        print("Cache-only: skipping server start; replaying cache hits…\n")
        try:
            results = run_eval.run_eval(
                task_filter=task_filter,
                max_iter_override=max_iter_override,
                model_name=model["name"],
                use_cache=True,
                cache_only=True,
            )
        except Exception as e:
            return {"slug": model["slug"], "name": model["name"],
                    "error": f"cache-only eval crashed: {e}"}
        if not results:
            return {"slug": model["slug"], "name": model["name"],
                    "error": "eval produced no results"}
        return {"slug": model["slug"], "name": model["name"], "results": results}

    print("Stopping any running llama-server...")
    stop_llama_server()

    print(f"Starting {model['serve']}...")
    try:
        start_model(model["serve"], model["slug"])
    except FileNotFoundError as e:
        return {"slug": model["slug"], "name": model["name"], "error": str(e)}

    print(f"Waiting for /health (up to {HEALTH_TIMEOUT}s)...")
    if not wait_for_health():
        stop_llama_server()
        return {"slug": model["slug"], "name": model["name"],
                "error": "model failed to become healthy within timeout"}

    # Per-task recovery: if /health stops responding mid-sweep, kill+restart
    # the serve script before the next task instead of letting the agent burn
    # 35 connection-error iterations × 5s for the rest of the run.
    def _recover() -> bool:
        return restart_server(model["serve"], model["slug"])

    print("Model healthy. Running eval suite...\n")
    try:
        results = run_eval.run_eval(
            task_filter=task_filter,
            max_iter_override=max_iter_override,
            model_name=model["name"],
            use_cache=use_cache,
            health_check=ping_health,
            recover_cb=_recover,
        )
    except Exception as e:
        stop_llama_server()
        return {"slug": model["slug"], "name": model["name"],
                "error": f"eval crashed: {e}"}
    finally:
        print(f"\nStopping {model['name']}...")
        stop_llama_server()

    if not results:
        return {"slug": model["slug"], "name": model["name"],
                "error": "eval produced no results"}

    return {"slug": model["slug"], "name": model["name"], "results": results}


# ---------------------------------------------------------------------------
# Sweep orchestration
# ---------------------------------------------------------------------------

def run_sweep(models: list[dict], task_filter: list[str] | None,
              max_iter_override: int | None,
              use_cache: bool = True,
              cache_only: bool = False,
              update_leaderboard: bool = True) -> dict:
    sweep_start = datetime.now()
    sweep_summary = {
        "started_at": sweep_start.isoformat(),
        "models_attempted": len(models),
        "models_succeeded": 0,
        "models_skipped": 0,
        "scores": [],
        "skipped": [],
    }

    for i, model in enumerate(models, 1):
        print(f"\n[{i}/{len(models)}] Starting model")
        outcome = benchmark_model(model, task_filter, max_iter_override,
                                   use_cache=use_cache, cache_only=cache_only)

        if "error" in outcome:
            print(f"\n>>> SKIPPED {model['name']}: {outcome['error']}")
            sweep_summary["models_skipped"] += 1
            sweep_summary["skipped"].append({
                "slug": model["slug"], "name": model["name"],
                "reason": outcome["error"],
            })
        else:
            results = outcome["results"]
            entry = scoring.score_run(results)
            # Partial-run guard: an aborted run (e.g. server died mid-sweep)
            # has fewer recorded tasks than the suite total, and its accuracy
            # is computed only over the tasks that ran — inflated garbage.
            # Keep the results file, keep the sweep score for visibility, but
            # NEVER let it into the leaderboard.
            n_recorded = len(results.get("tasks", []))
            n_expected = (results.get("summary") or {}).get("total", 0)
            if n_recorded < n_expected:
                print(f"\n>>> ERROR: {model['name']} run is PARTIAL "
                      f"({n_recorded}/{n_expected} tasks recorded) — leaderboard "
                      f"NOT updated. Results file kept for inspection; its "
                      f"accuracy covers only the tasks that completed.")
                entry["partial_run"] = True
            elif update_leaderboard:
                scoring.update_leaderboard(entry)
            else:
                print("  (--no-leaderboard: score not recorded in leaderboard.json)")
            sweep_summary["scores"].append(entry)
            sweep_summary["models_succeeded"] += 1
            print(f"\n>>> {model['name']}: capability {entry.get('capability')} "
                  f"(solve {entry.get('problem_solving')} / lang {entry.get('language_breadth')}) "
                  f"| accuracy {entry['accuracy']} speed {entry['speed']}")

        if i < len(models) and not cache_only:
            # No thermal load in cache-only mode — skip the cool-off.
            print(f"\nCool-off for {COOLOFF_SECONDS}s before next model...")
            time.sleep(COOLOFF_SECONDS)

    sweep_summary["finished_at"] = datetime.now().isoformat()
    sweep_summary["elapsed_seconds"] = round(
        (datetime.now() - sweep_start).total_seconds(), 1)
    return sweep_summary


def save_sweep_summary(summary: dict) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(RESULTS_DIR, f"sweep-{timestamp}.json")
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Benchmark all local models")
    parser.add_argument("--models", help="Comma-separated model slugs to benchmark "
                        "(default: all configured)")
    parser.add_argument("--tasks", help="Comma-separated task IDs (default: all)")
    parser.add_argument("--max-iter", type=int, help="Override max iterations per task")
    parser.add_argument("--list", action="store_true", help="List configured models and exit")
    parser.add_argument("--no-cache", action="store_true", help="Disable result cache (force live runs)")
    parser.add_argument("--no-leaderboard", action="store_true",
                        help="Do not record scores in leaderboard.json. REQUIRED for "
                             "smoke tests / partial-suite runs: the leaderboard must only "
                             "ever contain full-suite sweeps, or its accuracy numbers "
                             "become incomparable (a 13-task 100%% is not a 323-task 97%%).")
    parser.add_argument("--cache-only", action="store_true",
                        help="Replay cache only — never start a server, never call the model. Cache misses recorded as 'skipped_cache_miss'.")
    args = parser.parse_args()

    if args.list:
        print(f"Configured models ({len(MODELS)}):\n")
        for m in MODELS:
            print(f"  {m['slug']:<32}  {m['name']}")
            print(f"  {'':<32}  serve: {m['serve']}")
        return

    if args.models:
        slugs = {s.strip() for s in args.models.split(",")}
        models = [m for m in MODELS if m["slug"] in slugs]
        if not models:
            print(f"No models matched {slugs}. Use --list to see available.")
            sys.exit(1)
    else:
        models = MODELS

    task_filter = args.tasks.split(",") if args.tasks else None

    # Cleanup handler — make sure we don't leave llama-server running
    def _on_signal(signum, frame):
        print(f"\n\nReceived signal {signum} — stopping llama-server and exiting...")
        stop_llama_server()
        sys.exit(130)
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    if args.cache_only and args.no_cache:
        print("--cache-only and --no-cache are mutually exclusive.", file=sys.stderr)
        sys.exit(2)
    if args.tasks and not args.no_leaderboard:
        print("NOTE: --tasks given without --no-leaderboard. Partial-suite scores "
              "will be recorded in leaderboard.json and mix incomparably with "
              "full-suite rows. Use --no-leaderboard for smoke/partial runs.",
              file=sys.stderr)
    summary = run_sweep(models, task_filter, args.max_iter,
                         use_cache=not args.no_cache,
                         cache_only=args.cache_only,
                         update_leaderboard=not args.no_leaderboard)
    summary_path = save_sweep_summary(summary)

    # Final report
    print(f"\n{'=' * 60}")
    print(f"SWEEP COMPLETE — {summary['elapsed_seconds']:.0f}s total")
    print(f"  Succeeded: {summary['models_succeeded']}/{summary['models_attempted']}")
    if summary["skipped"]:
        print(f"  Skipped: {summary['models_skipped']}")
        for s in summary["skipped"]:
            print(f"    - {s['name']}: {s['reason']}")
    print(f"  Sweep summary: {summary_path}")
    print(f"{'=' * 60}\n")

    leaderboard = scoring.load_leaderboard()
    print("LEADERBOARD")
    print(scoring.format_leaderboard(leaderboard))
    print()


if __name__ == "__main__":
    main()
