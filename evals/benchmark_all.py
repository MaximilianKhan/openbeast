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
    {"slug": "qwen-27b-q4",
     "name": "Qwen 27B Q4_K_M",
     "serve": "scripts/serve-qwen-27b-q4.sh"},
    {"slug": "qwen-27b-q5",
     "name": "Qwen 27B Q5_K_XL",
     "serve": "scripts/serve-qwen-27b-q5.sh"},
    {"slug": "qwen-27b-uncensored-q5",
     "name": "Qwen 27B Uncensored Q5_K_P",
     "serve": "scripts/serve-qwen-27b-uncensored-q5.sh"},
    {"slug": "qwen-35b-a3b",
     "name": "Qwen 35B-A3B MoE Q4_K_M",
     "serve": "scripts/serve-qwen-35b-a3b.sh"},
    {"slug": "gemma-4-31b-q5",
     "name": "Gemma 4 31B-it Q5_K_XL",
     "serve": "scripts/serve-gemma-4-31b-q5.sh"},
]

LLAMA_HEALTH_URL = "http://localhost:8080/health"
LLAMA_PORT = 8080
HEALTH_TIMEOUT = 180   # seconds to wait for model load
COOLOFF_SECONDS = 5    # between models


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def stop_llama_server():
    """Kill any running llama-server. Tolerant — pkill returns 1 if no match."""
    subprocess.run(["pkill", "-TERM", "-f", "llama-server"], check=False)
    # Wait briefly for graceful shutdown
    for _ in range(10):
        if not _port_in_use(LLAMA_PORT):
            return
        time.sleep(0.5)
    # Escalate
    subprocess.run(["pkill", "-KILL", "-f", "llama-server"], check=False)
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


def start_model(serve_script: str) -> subprocess.Popen:
    """Launch a serve script in the background. Returns the Popen handle."""
    full_path = os.path.join(REPO_DIR, serve_script)
    if not os.path.isfile(full_path):
        raise FileNotFoundError(f"Serve script not found: {full_path}")
    return subprocess.Popen(
        ["bash", full_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=REPO_DIR,
        start_new_session=True,
    )


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


# ---------------------------------------------------------------------------
# Per-model benchmark
# ---------------------------------------------------------------------------

def benchmark_model(model: dict, task_filter: list[str] | None,
                    max_iter_override: int | None) -> dict:
    """Run the full eval suite against one model. Returns a dict with either
    'results' (success) or 'error' (skipped)."""
    print(f"\n{'#' * 60}")
    print(f"# Benchmarking: {model['name']} ({model['slug']})")
    print(f"# Serve: {model['serve']}")
    print(f"{'#' * 60}\n")

    print("Stopping any running llama-server...")
    stop_llama_server()

    print(f"Starting {model['serve']}...")
    try:
        proc = start_model(model["serve"])
    except FileNotFoundError as e:
        return {"slug": model["slug"], "name": model["name"], "error": str(e)}

    print(f"Waiting for /health (up to {HEALTH_TIMEOUT}s)...")
    if not wait_for_health():
        stop_llama_server()
        return {"slug": model["slug"], "name": model["name"],
                "error": "model failed to become healthy within timeout"}

    print("Model healthy. Running eval suite...\n")
    try:
        results = run_eval.run_eval(
            task_filter=task_filter,
            max_iter_override=max_iter_override,
            model_name=model["name"],
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
              max_iter_override: int | None) -> dict:
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
        outcome = benchmark_model(model, task_filter, max_iter_override)

        if "error" in outcome:
            print(f"\n>>> SKIPPED {model['name']}: {outcome['error']}")
            sweep_summary["models_skipped"] += 1
            sweep_summary["skipped"].append({
                "slug": model["slug"], "name": model["name"],
                "reason": outcome["error"],
            })
        else:
            entry = scoring.score_run(outcome["results"])
            scoring.update_leaderboard(entry)
            sweep_summary["scores"].append(entry)
            sweep_summary["models_succeeded"] += 1
            print(f"\n>>> {model['name']}: composite {entry['composite']} "
                  f"(corr {entry['correctness']}, speed {entry['speed']})")

        if i < len(models):
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

    summary = run_sweep(models, task_filter, args.max_iter)
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
