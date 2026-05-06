#!/usr/bin/env python3
"""
Eval harness — run coding tasks against the local LLM and measure performance.

Each task is defined as a JSON file in evals/tasks/ with:
  - task: what the agent should do
  - setup: optional shell command to create test fixtures
  - validation: python or bash script that checks the result
  - cleanup: shell command to remove test artifacts
  - max_iter: max agent iterations for this task

The eval runner:
  1. Runs setup (if any)
  2. Launches the agent runner against the task
  3. Runs validation to check success/failure
  4. Records results to evals/results/{timestamp}.json
  5. Runs cleanup

Usage:
  python evals/run_eval.py                          # run all tasks
  python evals/run_eval.py --tasks 01,02,03         # run specific tasks
  python evals/run_eval.py --max-iter 5             # override max iterations
  python evals/run_eval.py --base-url http://...:8080/v1  # different server

Requires: llama.cpp server running on port 8080.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = os.path.join(EVALS_DIR, "tasks")
RESULTS_DIR = os.path.join(EVALS_DIR, "results")
RUNNER_PATH = os.path.join(EVALS_DIR, "..", "agents", "runner.py")


def detect_model(base_url: str) -> str:
    """Query /v1/models on the llama.cpp server. Returns the alias the model
    was launched with (set via the -a flag in our serve scripts)."""
    try:
        url = base_url.rstrip("/") + "/models"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("data", [])
        if models:
            return models[0].get("id", "unknown")
    except Exception:
        pass
    return "unknown"


def slugify(name: str) -> str:
    """Convert a model name to a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown"


def capture_gpu_info() -> dict:
    """Snapshot the GPU configuration via nvidia-smi.

    Captures every GPU on the host (not just GPU 0) and derives a `host_id`
    string of the form "RTX 5090 ×1" or "RTX 3090 Ti ×2 + RTX 4090 ×1" so
    that benchmark results from different machines can co-exist in the same
    leaderboard. Backward-compatible top-level fields (name, driver_version,
    memory_total_mib, compute_capability) describe GPU 0.

    Returns empty dict if nvidia-smi isn't available."""
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,driver_version,memory.total,compute_cap",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {}
        lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
        gpus = []
        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                gpus.append({
                    "name": parts[0],
                    "driver_version": parts[1],
                    "memory_total_mib": int(parts[2]),
                    "compute_capability": parts[3],
                })
        if not gpus:
            return {}
        from collections import Counter
        counts = Counter(g["name"] for g in gpus)
        host_id = " + ".join(f"{name} ×{n}" for name, n in sorted(counts.items()))
        primary = gpus[0]
        return {
            "name": primary["name"],
            "driver_version": primary["driver_version"],
            "memory_total_mib": primary["memory_total_mib"],
            "compute_capability": primary["compute_capability"],
            "host_id": host_id,
            "gpu_count": len(gpus),
            "gpus": gpus,
        }
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return {}


def load_tasks(task_filter: list[str] | None = None) -> list[dict]:
    """Load task definitions from JSON files.

    A task JSON may declare a `variants` array. Each variant becomes its own
    flattened task entry with effective id `{base_id}_{variant_id}`, plus
    `base_id`, `variant_id`, `language`, and `variant_count` fields. Variant
    fields (setup, task, validation, cleanup, language, name, ...) override
    the corresponding top-level fields. Tasks without a `variants` array are
    treated as a single Python variant — `variant_count` = 1, no base_id.

    Filter matching: a filter entry that matches the base_id selects ALL of
    that task's variants; a filter entry matching the effective id selects
    just that variant.
    """
    tasks = []
    for path in sorted(Path(TASKS_DIR).glob("*.json")):
        with open(path) as f:
            task = json.load(f)
        task["_path"] = str(path)

        variants = task.get("variants")
        if not variants:
            if task_filter is None or task["id"] in task_filter:
                tasks.append(task)
            continue

        base_id = task["id"]
        base_name = task.get("name", base_id)
        for variant in variants:
            variant_id = variant["id"]
            effective_id = f"{base_id}_{variant_id}"
            if task_filter is not None and base_id not in task_filter and effective_id not in task_filter:
                continue
            language = variant.get("language", "python")
            t = dict(task)
            t.pop("variants", None)
            for k, v in variant.items():
                if k != "id":
                    t[k] = v
            t["id"] = effective_id
            t["base_id"] = base_id
            t["variant_id"] = variant_id
            t["language"] = language
            t["variant_count"] = len(variants)
            # Preserve the base task's name unless the variant overrides it
            t["name"] = variant.get("name", f"{base_name} [{language}]")
            tasks.append(t)
    return tasks


def run_setup(task: dict) -> bool:
    """Run task setup command. Returns True on success."""
    setup = task.get("setup")
    if not setup:
        return True
    result = subprocess.run(setup, shell=True, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"  Setup failed: {result.stderr[:200]}")
        return False
    return True


_TOKEN_LINE = re.compile(r"^TOKENS:\s+prompt=(\d+)\s+completion=(\d+)\s+total=(\d+)\s*$", re.MULTILINE)


def _parse_tokens(stdout: str) -> dict:
    """Pull the last TOKENS: prompt=… completion=… total=… line from runner stdout."""
    matches = list(_TOKEN_LINE.finditer(stdout))
    if not matches:
        return {"prompt": 0, "completion": 0, "total": 0}
    m = matches[-1]
    return {"prompt": int(m.group(1)), "completion": int(m.group(2)), "total": int(m.group(3))}


def run_agent(task: dict, base_url: str, max_iter_override: int | None = None) -> dict:
    """Run the agent against a task. Returns timing, iteration, and token info."""
    max_iter = max_iter_override or task.get("max_iter", 15)
    start_time = time.time()

    cmd = [
        sys.executable, RUNNER_PATH,
        "--base-url", base_url,
        "--max-iter", str(max_iter),
        "--workdir", "/tmp",
        task["task"],
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max_iter * 60,  # rough timeout: 1 min per iteration max
        )
        elapsed = time.time() - start_time
        tokens = _parse_tokens(result.stdout)
        return {
            "exit_code": result.returncode,
            "elapsed_seconds": round(elapsed, 1),
            "stdout": result.stdout[-2000:],  # last 2K of output
            "stderr": result.stderr[-1000:],
            "tokens": tokens,
        }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_time
        return {
            "exit_code": -1,
            "elapsed_seconds": round(elapsed, 1),
            "stdout": "(timed out)",
            "stderr": "",
            "tokens": {"prompt": 0, "completion": 0, "total": 0},
        }


def run_pre_validate(task: dict):
    """Re-assert harness fixtures right before validation.

    Agents have full bash access and can corrupt fixture files during their
    own testing (e.g. overwriting a healthcheck.sh with `exit 0`). Tasks
    declare `pre_validate` as an optional shell command that re-creates
    *only* the harness fixtures (not any file the agent is supposed to
    write). If absent, this is a no-op.
    """
    cmd = task.get("pre_validate")
    if not cmd:
        return
    subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)


def run_validation(task: dict) -> tuple[bool, str]:
    """Run validation. Returns (passed, output)."""
    validation = task.get("validation", {})
    vtype = validation.get("type", "bash")
    script = validation.get("script", "")

    if not script:
        return False, "No validation script defined"

    try:
        if vtype == "python":
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=30,
            )
        else:
            result = subprocess.run(
                script, shell=True,
                capture_output=True, text=True, timeout=30,
            )
        passed = result.returncode == 0
        output = (result.stdout + result.stderr).strip()[:500]
        return passed, output
    except subprocess.TimeoutExpired:
        return False, "Validation timed out"
    except Exception as e:
        return False, f"Validation error: {e}"


def run_cleanup(task: dict):
    """Run cleanup command."""
    cleanup = task.get("cleanup")
    if cleanup:
        subprocess.run(cleanup, shell=True, capture_output=True, timeout=30)


def run_eval(
    task_filter: list[str] | None = None,
    base_url: str = "http://localhost:8080/v1",
    max_iter_override: int | None = None,
    model_name: str | None = None,
) -> dict:
    """Run the full eval suite. Returns results dict."""
    tasks = load_tasks(task_filter)
    if not tasks:
        print("No tasks found.")
        return {}

    if not model_name:
        model_name = detect_model(base_url)
    model_slug = slugify(model_name)
    gpu_info = capture_gpu_info()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results_path = os.path.join(RESULTS_DIR, f"eval-{model_slug}-{timestamp}.json")

    print(f"Eval started — {len(tasks)} tasks")
    print(f"Model:  {model_name}")
    print(f"Server: {base_url}")
    if gpu_info:
        print(f"GPU:    {gpu_info.get('name', '?')} ({gpu_info.get('memory_total_mib', '?')} MiB, driver {gpu_info.get('driver_version', '?')})")
    print(f"Results: {results_path}")
    print("=" * 60)

    results = {
        "timestamp": datetime.now().isoformat(),
        "model": model_name,
        "model_slug": model_slug,
        "base_url": base_url,
        "gpu": gpu_info,
        "tasks": [],
        "summary": {"total": len(tasks), "passed": 0, "failed": 0},
    }

    for i, task in enumerate(tasks, 1):
        task_id = task["id"]
        task_name = task.get("name", task_id)
        difficulty = task.get("difficulty", "?")

        print(f"\n[{i}/{len(tasks)}] {task_name} ({difficulty})")
        print(f"  Task: {task['task'][:80]}...")

        # Setup
        if not run_setup(task):
            result = {
                "id": task_id, "name": task_name, "difficulty": difficulty,
                "passed": False, "reason": "setup_failed",
                "elapsed_seconds": 0,
            }
            if "base_id" in task:
                result["base_id"] = task["base_id"]
                result["variant_id"] = task["variant_id"]
                result["language"] = task["language"]
                result["variant_count"] = task["variant_count"]
            results["tasks"].append(result)
            results["summary"]["failed"] += 1
            print(f"  FAIL (setup failed)")
            continue

        # Run agent
        print(f"  Running agent...")
        agent_result = run_agent(task, base_url, max_iter_override)
        print(f"  Agent finished in {agent_result['elapsed_seconds']}s")

        # Re-assert fixtures (no-op unless task declares pre_validate).
        run_pre_validate(task)

        # Validate
        passed, validation_output = run_validation(task)

        # Record result
        tokens = agent_result.get("tokens") or {"prompt": 0, "completion": 0, "total": 0}
        result = {
            "id": task_id,
            "name": task_name,
            "difficulty": difficulty,
            "model": model_name,
            "passed": passed,
            "elapsed_seconds": agent_result["elapsed_seconds"],
            "agent_exit_code": agent_result["exit_code"],
            "validation_output": validation_output,
            "tokens_prompt": tokens["prompt"],
            "tokens_completion": tokens["completion"],
            "tokens_total": tokens["total"],
        }
        # Variant metadata, only present for tasks that declared `variants`
        if "base_id" in task:
            result["base_id"] = task["base_id"]
            result["variant_id"] = task["variant_id"]
            result["language"] = task["language"]
            result["variant_count"] = task["variant_count"]
        results["tasks"].append(result)

        if passed:
            results["summary"]["passed"] += 1
            print(f"  PASS ({agent_result['elapsed_seconds']}s)")
        else:
            results["summary"]["failed"] += 1
            print(f"  FAIL: {validation_output[:100]}")

        # Cleanup
        run_cleanup(task)

    # Save results
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

    # Print summary
    s = results["summary"]
    print(f"\n{'=' * 60}")
    print(f"Results: {s['passed']}/{s['total']} passed, {s['failed']} failed")
    print(f"Saved to: {results_path}")
    print(f"{'=' * 60}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Run eval tasks against local LLM")
    parser.add_argument("--tasks", help="Comma-separated task IDs to run (default: all)")
    parser.add_argument("--base-url", default="http://localhost:8080/v1", help="API base URL")
    parser.add_argument("--max-iter", type=int, help="Override max iterations for all tasks")
    parser.add_argument("--model-name", help="Override model name (default: auto-detect from /v1/models)")
    parser.add_argument("--list", action="store_true", help="List available tasks and exit")
    args = parser.parse_args()

    if args.list:
        tasks = load_tasks()
        print(f"Available tasks ({len(tasks)}):\n")
        for t in tasks:
            print(f"  {t['id']}  [{t.get('difficulty', '?'):>6}]  {t.get('name', t['id'])}")
        return

    task_filter = args.tasks.split(",") if args.tasks else None
    results = run_eval(
        task_filter=task_filter,
        base_url=args.base_url,
        max_iter_override=args.max_iter,
        model_name=args.model_name,
    )

    # Exit with failure if any task failed
    if results and results["summary"]["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
