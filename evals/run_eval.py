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
  python evals/run_eval.py --jobs 4                 # 4 parallel workers (server must run -np >= 4)
  python evals/run_eval.py --base-url http://...:8080/v1  # different server

Requires: llama.cpp server running on port 8080.
"""

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = os.path.join(EVALS_DIR, "tasks")
RESULTS_DIR = os.path.join(EVALS_DIR, "results")
RUNNER_PATH = os.path.join(EVALS_DIR, "..", "agents", "runner.py")

# Shared process-reaping from agents/tools.py — single source of truth for
# SIGKILL-the-group + rlimit + output-cap logic.
sys.path.insert(0, os.path.join(EVALS_DIR, "..", "agents"))
from tools import run_reaped  # noqa: E402
sys.path.insert(0, EVALS_DIR)
import suite_manifest  # noqa: E402

# ---------------------------------------------------------------------------
# Agent process groups we own, so a SIGTERM to US does not orphan THEM.
#
# Every agent is spawned with start_new_session=True, which is load-bearing:
# it is what lets the per-task wall-timeout SIGKILL the runner's whole group
# instead of just the runner. The cost, found by the v1.4.0 review, is that
# the same isolation puts the agent OUTSIDE any group signalled at us — so
# `./scripts/job.sh stop`, a Stop from the beast-chat console, or a plain
# Ctrl-C killed run_eval and left the agent running: still holding a
# llama-server slot, still writing the task's shared /tmp/eval_* fixtures,
# against a relaunch that necessarily re-runs the unbanked unit. A corrupted
# row from that re-run does not look corrupted; it looks like a result.
#
# A SET, not one global: --jobs N runs N agents concurrently in a
# ThreadPoolExecutor, so tracking a single Popen would orphan N-1 of them.
_live_pgids: set[int] = set()
_live_pgid_lock = threading.Lock()


def _descendant_pids(root: int) -> list[int]:
    """Every live descendant of `root`, from /proc (Linux). [] where there
    is no /proc — the kill then degrades to the runner's own group."""
    children: dict[int, list[int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for d in entries:
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                stat = f.read()
            # comm may contain spaces/parens: fields resume after the LAST ')'.
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
        children.setdefault(ppid, []).append(int(d))
    out, stack = [], [root]
    while stack:
        for c in children.get(stack.pop(), ()):
            out.append(c)
            stack.append(c)
    return out


def _kill_agent_tree(pgid: int) -> None:
    """SIGKILL an agent's process group AND every group its descendants lead.

    Killing only the runner's group (start_new_session) is not enough:
    tools.run_reaped starts every bash-tool child in its OWN session, so a
    command in flight at a wall timeout (a hung `./ts < input.txt`) outlived
    the runner and kept running — only its own run_reaped timeout could
    stop it, and that died with the runner. The runner group is frozen
    first so it cannot spawn another tool child during the /proc walk."""
    try:
        os.killpg(pgid, signal.SIGSTOP)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    groups = {pgid}
    for pid in _descendant_pids(pgid):
        try:
            groups.add(os.getpgid(pid))
        except (ProcessLookupError, OSError):
            pass
    groups.discard(os.getpgrp())        # never our own group
    for g in groups:
        try:
            os.killpg(g, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _reap_live_agents() -> None:
    """SIGKILL every agent group we own. Best-effort, last-resort."""
    got = _live_pgid_lock.acquire(blocking=False)
    try:
        pgids = tuple(_live_pgids)
    except RuntimeError:        # mutated mid-snapshot; a partial sweep beats none
        pgids = ()
    finally:
        if got:
            _live_pgid_lock.release()
    for pgid in pgids:
        _kill_agent_tree(pgid)


def _install_signal_reaper() -> None:
    """Reap our agent groups before dying, for SIGTERM and SIGINT alike."""
    def _handler(signum, _frame):
        _reap_live_agents()
        # os._exit skips the interpreter's own flush, and under nohup or a
        # log file stdout is BLOCK-buffered: a stopped campaign lost the last
        # screenful of its log — exactly the lines that say where it stopped.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        # os._exit: no atexit, no threads to join, no chance of a worker
        # spawning a fresh agent on the way out.
        os._exit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Not the main thread, or a platform without it. The timeout path
            # still reaps; only the signalled path degrades.
            pass


def detect_model(base_url: str) -> str:
    """Query /v1/models on the llama.cpp server. Returns the alias the model
    was launched with (set via the -a flag in our serve scripts)."""
    try:
        url = base_url.rstrip("/") + "/models"
        # /v1/models is key-protected on a keyed server (LLAMA_API_KEY);
        # completions inherit the key via the runner's env resolution.
        key = os.environ.get("OPENBEAST_API_KEY") or os.environ.get("OPENAI_API_KEY")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("data", [])
        if models:
            return models[0].get("id", "unknown")
    except Exception:
        pass
    return "unknown"


def _fetch_server_slots(base_url: str) -> int | None:
    """Number of parallel slots (-np) on the llama.cpp server, from /props.

    /props lives at the SERVER ROOT, not under /v1 — and beast-gate's path
    allowlist blocks it on gated installs, so this is best-effort. Returns
    None when unreadable."""
    try:
        root = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
        key = os.environ.get("OPENBEAST_API_KEY") or os.environ.get("OPENAI_API_KEY")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        req = urllib.request.Request(root + "/props", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        n = data.get("total_slots")
        return int(n) if n else None
    except Exception:
        pass
    # Not llama.cpp (vLLM, TensorFold, the hydra proxy): /props is absent.
    # Some OpenAI-compatible servers publish their batch limit on the model
    # card; use it when present. Otherwise the caller needs --slots.
    try:
        key = os.environ.get("OPENBEAST_API_KEY") or os.environ.get("OPENAI_API_KEY")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        req = urllib.request.Request(base_url.rstrip("/") + "/models", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for m in data.get("data", [])[:1]:
            n = m.get("max_num_seqs") or (m.get("meta") or {}).get("max_num_seqs")
            if n:
                return int(n)
    except Exception:
        pass
    return None


def capture_remote_server_info(base_url: str, declared_rb: str | None = None) -> dict:
    """Provenance for a server capture_server_config cannot see: a remote
    endpoint, or a local non-llama engine (vLLM, TensorFold). Reads the
    OpenAI model card (/v1/models) for the engine (`owned_by`) and context
    length, and takes the reasoning budget from what the operator DECLARED
    (--reasoning-budget / OPENBEAST_EVAL_REASONING_BUDGET) — never from
    `pgrep llama-server`, which describes some other process on this host.
    "0" means thinking off. An undeclared budget is recorded as unknown, and
    the cache era falls back to uncapped, as before."""
    info: dict = {"source": "/v1/models"}
    rb = declared_rb if declared_rb not in (None, "") else \
        os.environ.get("OPENBEAST_EVAL_REASONING_BUDGET", "").strip()
    if rb:
        info["reasoning_budget"] = str(rb).strip()
        info["reasoning_budget_source"] = "declared"
    try:
        key = os.environ.get("OPENBEAST_API_KEY") or os.environ.get("OPENAI_API_KEY")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        req = urllib.request.Request(base_url.rstrip("/") + "/models", headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        m = (data.get("data") or [{}])[0]
        info["model_id"] = m.get("id")
        if m.get("owned_by"):
            info["engine"] = m["owned_by"]
        for k in ("max_model_len", "max_num_seqs", "root"):
            if m.get(k) is not None:
                info[k] = m[k]
    except Exception as e:  # noqa: BLE001 — provenance is best-effort
        info["error"] = type(e).__name__
    return info


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


def parse_engine_version(out: str) -> dict:
    """Parse llama-server --version output. Pure, so it can be tested.

    TWO upstream formats, and the second one broke us silently:
      old: "version: 8893 (6217b4958)"
      new: "version: 0.4.0-dev (build 10865, commit d4389a4dd)"
    The regex only knew the old one, so from the day llama.cpp changed the
    line, `build` and `commit` were absent from EVERY row — and because the
    parse failure was a quiet `if m:`, nothing said so. The row kept
    `source_head`, which is the SOURCE TREE's HEAD and is NOT what the binary
    was built from (today: tree 8e126574, binary build 10865 / d4389a4dd). So
    the field recording what actually produced a measurement was empty while
    the populated one named something else.

    An unrecognised third format is reported, not dropped.
    """
    info: dict = {}
    m = re.search(r"^version:\s*\S*\s*\(build\s+(\d+),\s*commit\s+([0-9a-f]+)\)",
                  out, re.MULTILINE)
    if not m:
        m = re.search(r"^version:\s+(\S+)\s+\(([0-9a-f]+)\)\s*$", out, re.MULTILINE)
    if m:
        info["build"], info["commit"] = m.group(1), m.group(2)
    else:
        first = next((ln for ln in out.splitlines() if ln.startswith("version:")), "")
        info["version_raw"] = first.strip() or "(no version line)"
        info["version_parse"] = "UNRECOGNISED — update parse_engine_version in run_eval.py"
    m = re.search(r"^built with\s+(.+?)\s+for\s+(.+?)\s*$", out, re.MULTILINE)
    if m:
        info["compiler"], info["target"] = m.group(1), m.group(2)
    return info


def capture_inference_engine_info() -> dict:
    """Snapshot the llama.cpp binary's version + build commit + compiler.

    Reads from `<repo>/llama.cpp/build/bin/llama-server --version`. Upstream
    has used two formats — `version: 8893 (6217b4958)` and the current
    `version: 0.4.0-dev (build 10865, commit d4389a4dd)` — and both are
    parsed. An unrecognised third one is recorded as `version_raw` +
    `version_parse: UNRECOGNISED` rather than dropped, because the previous
    silent miss meant no row carried the binary's identity at all.
    `CUDA_VISIBLE_DEVICES=""` disables ggml's CUDA init so this call doesn't
    contend for VRAM with a running server. Also captures the llama.cpp
    source-tree HEAD via `git rev-parse HEAD`, which may differ from the
    binary's embedded commit if the source was updated since the last build.

    Returns empty dict on any failure (binary missing, llama.cpp tree gone,
    parse failure)."""
    binary = Path(__file__).resolve().parent.parent / "llama.cpp" / "build" / "bin" / "llama-server"
    info: dict = {"name": "llama.cpp", "binary": str(binary)}
    try:
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        out = (result.stdout or "") + (result.stderr or "")
        info.update(parse_engine_version(out))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ["git", "-C", str(binary.parent.parent.parent), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            info["source_head"] = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return info


def _parse_server_flags(cmdline: str) -> dict:
    """Pull the provenance-relevant flags out of a llama-server command line.

    Server flags ARE experiment config — reasoning budget, slot count, KV
    quant — assembled at launch from serve.sh + openbeast.conf + env, and
    otherwise invisible to results: the 2026-08-20 Qwen3.8 rows ran under a
    gitignored local REASONING_BUDGET=4096 that no file recorded, while the
    Qwen3.6 champion ran uncapped.

    LAST occurrence wins, as it does in llama-server (common/arg.cpp
    assigns in argv order): serve.sh appends the global REASONING_BUDGET
    override AFTER a serve script's baked --reasoning-budget on purpose, so
    reading the first one stamped the baked value into the cache era while
    the server decoded under the override. `--flag=value` is honoured too."""
    info: dict = {"cmdline": cmdline}
    flags = {"--reasoning-budget": "reasoning_budget",
             "--reasoning": "reasoning",
             "-np": "parallel_slots",
             "-c": "context",
             "-ctk": "kv_cache_type",
             "-ctv": "kv_cache_type_v",
             "-m": "model_path",
             "--model": "model_path",
             "--port": "port"}
    toks = cmdline.split()
    for i, tok in enumerate(toks):
        name, eq, val = tok.partition("=")
        if name not in flags:
            continue
        if eq:
            info[flags[name]] = val
        elif i + 1 < len(toks):
            info[flags[name]] = toks[i + 1]
    return info


PROC_ROOT = "/proc"
LLAMA_DEFAULT_PORT = 8080     # llama-server's own default for --port


def _server_port(info: dict) -> int | None:
    """The port a parsed llama-server command line listens on (its default
    8080 when no --port is given), or None if the value is not a number."""
    try:
        return int(info.get("port", LLAMA_DEFAULT_PORT))
    except (TypeError, ValueError):
        return None


def _listening_inodes(port: int) -> set[str]:
    """Socket inodes LISTENing on `port` (any address), from /proc/net."""
    inodes: set[str] = set()
    for name in ("tcp", "tcp6"):
        try:
            with open(os.path.join(PROC_ROOT, "net", name)) as fh:
                next(fh, None)                       # header
                for line in fh:
                    f = line.split()
                    if len(f) < 10 or f[3] != "0A":  # 0A = TCP_LISTEN
                        continue
                    if int(f[1].rsplit(":", 1)[1], 16) == port:
                        inodes.add(f[9])
        except (OSError, ValueError, IndexError):
            continue
    return inodes


def _pid_holds_socket(pid: str, inodes: set[str]) -> bool:
    fd_dir = os.path.join(PROC_ROOT, pid, "fd")
    try:
        fds = os.listdir(fd_dir)
    except OSError:                                  # another uid's process
        return False
    want = {f"socket:[{i}]" for i in inodes}
    for fd in fds:
        try:
            if os.readlink(os.path.join(fd_dir, fd)) in want:
                return True
        except OSError:
            continue
    return False


def _is_local_host(host: str | None) -> bool:
    """Whether `host` names this machine: an address we can bind to."""
    if not host:
        return True
    if host in ("localhost", "0.0.0.0", "::"):
        return True
    import socket
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for family, _, _, _, addr in infos:
        s = socket.socket(family, socket.SOCK_STREAM)
        try:
            s.bind((addr[0], 0))
            return True
        except OSError:
            continue
        finally:
            s.close()
    return False


def capture_server_config(base_url: str = "http://localhost:8080/v1") -> dict:
    """Best-effort snapshot of the llama-server invocation that serves
    `base_url` (local host only — empty when the server runs elsewhere, isn't
    up, or can't be told apart from another one).

    It must be THAT server: the reasoning budget read here becomes the cache
    era (.rbN) and the flags feed the env fingerprint. Taking the first line
    of `pgrep` described whichever llama-server the kernel listed first — a
    ChunkHound sidecar on :8081, or a sibling worktree's measurement server
    — so the run banked its verdicts under the other process's era. The
    listener is resolved by the socket that LISTENs on the port
    (/proc/net/tcp inode -> /proc/<pid>/fd), falling back to the --port on
    the command line (llama-server's default 8080 when absent) for a server
    whose fds this uid cannot read."""
    from urllib.parse import urlparse
    try:
        u = urlparse(base_url)
        host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return {}
    if not _is_local_host(host):
        return {}
    try:
        out = subprocess.run(["pgrep", "-ax", "llama-server"],
                             capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    lines = out.stdout.strip().splitlines() if out.returncode == 0 else []
    procs = []
    for line in lines:
        pid, _, cmdline = line.strip().partition(" ")
        if pid.isdigit():
            procs.append((pid, _parse_server_flags(cmdline.strip())))
    if not procs:
        return {}
    inodes = _listening_inodes(port)
    if inodes:
        owners = [info for pid, info in procs if _pid_holds_socket(pid, inodes)]
        if len(owners) == 1:
            return owners[0]
    by_flag = [info for _, info in procs if _server_port(info) == port]
    if len(by_flag) == 1:
        return by_flag[0]
    # llama-server IS running here, but none can be tied to this port — most
    # often a proxy (beast-gate) in front of it. Say so: an empty record makes
    # the cache era fall back to uncapped, which is wrong for a capped server.
    print(f"WARNING: {len(procs)} local llama-server(s) running, but none can be "
          f"identified as the one serving port {port} (a proxy such as beast-gate "
          f"in front of it, or two candidates). Server config not recorded; the "
          f"cache era falls back to UNCAPPED. Point --base-url at llama-server's "
          f"own port to record it.", file=sys.stderr)
    return {}


def capture_suite_version() -> str:
    """The eval-suite version from evals/SUITE_VERSION (e.g. 'v4').

    Records WHICH benchmark a row ran against so v3.5 and v4 scores are never
    confused on the leaderboard. Returns 'unknown' if the marker is absent."""
    marker = Path(__file__).resolve().parent / "SUITE_VERSION"
    try:
        return marker.read_text().strip() or "unknown"
    except Exception:
        return "unknown"


def capture_runtime_info() -> dict:
    """Agent-runtime provenance: the Python + client-library versions that
    drove the model, plus OpenBeast's own commit. The agent harness
    (agents/runner.py, agents/tools.py, prompts) drives the model and drifts
    independently of the inference engine — without the repo SHA no
    leaderboard row can say which harness it ran under. Each field
    best-effort; empty on failure."""
    import platform
    info = {"python": platform.python_version(), "platform": platform.platform()}
    for pkg in ("openai", "mcp"):
        try:
            from importlib.metadata import version
            info[pkg] = version(pkg)
        except Exception:
            pass
    repo = Path(__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            info["openbeast_commit"] = result.stdout.strip()
        result = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            # True = the run used uncommitted local changes; the SHA alone
            # can't reproduce that harness.
            info["openbeast_dirty"] = bool(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return info


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


def diagnostics_flag(env=None) -> tuple[bool, str | None, dict]:
    """Derive the push-diagnostics state ONCE per run (docs/
    LANG_AWARENESS_PLAN.md §3.4): the cache-key component and the agent
    subprocess env must come from the same read or they can disagree.

    Returns (enabled, cache_component, toolchain_versions). The component
    is a toolchain fingerprint, not a boolean — different checker
    toolchains emit different diagnostics."""
    env = os.environ if env is None else env
    enabled = (env.get("OPENBEAST_DIAGNOSTICS", "").strip() == "1"
               or env.get("BEAST_ASSIST", "").strip() == "1")
    if not enabled:
        return False, None, {}
    import hashlib
    versions = dict(toolchain_versions())
    fp = hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()[:8]
    return True, f"diag2-{fp}", versions


_TOOLCHAINS: dict | None = None


def toolchain_versions() -> dict:
    """First line of each validator/checker toolchain's version output
    (zig, rustc, go, gcc, shellcheck), probed once per process. Absent
    tools are omitted, a probe that fails reads "?"."""
    global _TOOLCHAINS
    if _TOOLCHAINS is None:
        import shutil as _sh
        versions = {}
        for name, cmd in (("zig", ["zig", "version"]),
                          ("rustc", ["rustc", "--version"]),
                          ("go", ["go", "version"]),
                          ("gcc", ["gcc", "-dumpfullversion"]),
                          ("shellcheck", ["shellcheck", "--version"])):
            if _sh.which(cmd[0]):
                try:
                    out = subprocess.run(cmd, capture_output=True, text=True,
                                         timeout=10).stdout.strip()
                    versions[name] = out.splitlines()[0][:60] if out else "?"
                except Exception:
                    versions[name] = "?"
        _TOOLCHAINS = versions
    return dict(_TOOLCHAINS)


WEIGHTS_REGISTRY = os.path.join(EVALS_DIR, "..", "scripts", "weights.registry")


def weight_identity(model_path: str | None) -> dict:
    """Cheap, deterministic identity of the GGUF a server loaded. A pinned
    weight (scripts/weights.registry row whose byte size matches) is its
    registry sha256; anything else — research quants regenerated under the
    same alias — is size + mtime, so a re-emitted file reads as different.
    Never hashes the file itself (minutes per 20 GB)."""
    if not model_path:
        return {}
    ident: dict = {"file": os.path.basename(model_path)}
    try:
        st = os.stat(model_path)
    except OSError:
        ident["missing"] = True
        return ident
    ident["bytes"] = st.st_size
    try:
        with open(WEIGHTS_REGISTRY) as f:
            for line in f:
                cols = line.rstrip("\n").split("\t")
                if (not line.startswith("#") and len(cols) >= 3
                        and cols[2] == ident["file"] and cols[1] == str(st.st_size)):
                    ident["sha256"] = cols[0]
                    return ident
    except OSError:
        pass
    ident["mtime_ns"] = st.st_mtime_ns
    return ident


def env_fingerprint(server_info: dict | None, engine_info: dict | None) -> tuple[str, dict]:
    """What the cache key does NOT otherwise see (review eval-harness-7):
    the weights' identity, the llama.cpp build, the KV-cache/context serve
    flags, and the validator toolchains + Python. Returns
    (`env1-<sha8>`, the dict it hashes)."""
    import hashlib
    import platform
    server_info = server_info or {}
    engine_info = engine_info or {}
    env = {
        "weights": weight_identity(server_info.get("model_path")),
        "engine": {k: engine_info[k] for k in ("build", "commit") if engine_info.get(k)},
        "serve": {k: server_info[k] for k in ("context", "kv_cache_type", "kv_cache_type_v")
                  if server_info.get(k)},
        "toolchains": {**toolchain_versions(), "python": platform.python_version()},
    }
    fp = hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()[:8]
    return f"env1-{fp}", env


def env_era_enabled() -> bool:
    """OPENBEAST_EVAL_ENV_ERA=1 puts env_fingerprint into the cache key.
    Opt-in because turning it on forks every key into a new era; campaigns
    that pair arms across days should set it."""
    return os.environ.get("OPENBEAST_EVAL_ENV_ERA", "").strip() == "1"


PACKS_DIR = os.path.join(EVALS_DIR, "..", "agents", "packs")
# Language → pack file, and the drift check, live in agents/lang/pack_context
# so the harness and PRODUCTION agents (which get the same pack since the
# Tier-3 SHIP) cannot disagree about which file a pack is or when it is stale.
# Tier 3 (docs/LANG_AWARENESS_PLAN.md §5): only zig has a pack today; every
# other language gets nothing — its agent command line is byte-identical with
# packs on or off.
from lang import pack_context as _pack_context  # noqa: E402
PACK_FILES = _pack_context.PACK_FILES


def _verify_pack(lang: str, path: str, data: bytes) -> None:
    """Drift-abort at run start (roadmap R2): the generated section's
    stamped sha must match its bytes, and the pack's zig version must
    match the installed compiler — a pack generated against another
    stdlib is a different experiment and must never run under this era.
    The check itself is pack_context.pack_problem (shared with production)."""
    problem = _pack_context.pack_problem(lang, path, data)
    if problem:
        raise SystemExit(problem)


def packs_flag(env=None) -> tuple[bool, str | None, dict]:
    """Derive the awareness-pack state ONCE per run (same rule as
    diagnostics_flag: the cache-key component and what the agent sees must
    come from one read). Enabled by BEAST_PACKS=1 (user-facing) or
    OPENBEAST_PACKS=1 (internal), or run_eval/benchmark_all --packs.

    Returns (enabled, cache_component, meta) where cache_component is
    `pack1-<sha8 of the pack file bytes>` (packs concatenated in language
    order when there is more than one) and meta = {"sha": {lang: sha8},
    "paths": {lang: abs_path}}."""
    env = os.environ if env is None else env
    enabled = (env.get("BEAST_PACKS", "").strip() == "1"
               or env.get("OPENBEAST_PACKS", "").strip() == "1")
    if not enabled:
        return False, None, {}
    import hashlib
    shas, paths = {}, {}
    h = hashlib.sha256()
    for lang, fn in sorted(PACK_FILES.items()):
        p = os.path.abspath(os.path.join(PACKS_DIR, fn))
        with open(p, "rb") as f:
            data = f.read()
        _verify_pack(lang, p, data)
        shas[lang] = hashlib.sha256(data).hexdigest()[:8]
        paths[lang] = p
        h.update(data)
    return True, f"pack1-{h.hexdigest()[:8]}", {"sha": shas, "paths": paths}


ESCALATE_LANG_DIR = os.path.join(EVALS_DIR, "..", "agents", "lang")
# What decides the card text a measured unit sees, relative to
# ESCALATE_LANG_DIR. The index says which errors select which claim ids; the
# claim files carry the `summary` sentence that is actually delivered (and are
# what escalate.namespace_wildcards reads); escalate.py is the selector
# (_select, the tie-break, the fan-out limits, render_escalation's header);
# verify.py turns a claim file into the summary that is served; __init__.py is
# the facade that decides whether anything is said under eval at all.
# Only TOP-LEVEL claims/*.json count, matching verify.load_claims: staging/ is
# never served, so editing it must not split the era.
ESCALATE_TREATMENT_FILES = ("escalate-index.json", "escalate.py", "verify.py",
                            "__init__.py")
ESCALATE_KNOBS = ("OPENBEAST_LANG_MAX_CARDS",)
# escalate.py's `from lang import X` modules that are NOT hashed as source,
# and why that is safe: what they decide reaches the component as an OUTCOME
# (_escalate_gate) instead. drivers/packs decide whether an index entry is
# served on this machine (the toolchain-version gate); _proc parses the
# MAX_CARDS knob. Hashing their source would split the era on every driver
# hardening edit; hashing the outcome splits it exactly when what the model
# sees changes. A new import must be added here or to the hashed files — a
# test reads escalate.py's imports and holds this list to it.
ESCALATE_OUTCOME_COVERED = ("_proc", "drivers", "packs")


def _escalate_gate(index_bytes: bytes) -> dict:
    """What this machine would actually serve, from the REAL selector:
    {"gate": escalate.gate_state(index), "max_cards": parsed knob}. The index
    is the one being stamped (so a test tree stays self-consistent); the gate
    and the knob parser are the real ones cards_for uses. Any failure refuses
    the arm: a treatment whose delivery cannot be determined cannot be named."""
    try:
        index = json.loads(index_bytes.decode("utf-8"))
        agents = os.path.abspath(os.path.join(EVALS_DIR, "..", "agents"))
        if agents not in sys.path:
            sys.path.insert(0, agents)
        from lang import escalate as E            # noqa: PLC0415
        return {"gate": E.gate_state(index), "max_cards": E.max_cards_knob()}
    except SystemExit:
        raise
    except Exception as e:                        # noqa: BLE001
        raise SystemExit(f"--escalate: cannot determine what the escalation index "
                         f"serves on this machine ({type(e).__name__}: {e})")


def _escalate_treatment() -> list[tuple[str, bytes]]:
    """(relative path, bytes) for every file that shapes a delivered card,
    in a fixed order. A missing file is a refusal: an arm whose treatment
    cannot be read cannot be named."""
    base = os.path.abspath(ESCALATE_LANG_DIR)
    rels = list(ESCALATE_TREATMENT_FILES)
    claims = os.path.join(base, "claims")
    try:
        rels += sorted(f"claims/{n}" for n in os.listdir(claims)
                       if n.endswith(".json") and os.path.isfile(os.path.join(claims, n)))
    except OSError as e:
        raise SystemExit(f"--escalate: cannot list {claims} ({e})")
    out = []
    for rel in rels:
        path = os.path.join(base, rel)
        try:
            with open(path, "rb") as f:
                out.append((rel, f.read()))
        except OSError as e:
            raise SystemExit(f"--escalate: cannot read {path} ({e})")
    return out


def escalate_flag(diag_on: bool, env=None) -> tuple[bool, str | None, dict]:
    """Derive the beast-lang escalation state ONCE per run (the same rule as
    diagnostics_flag / packs_flag: the cache-key component and what the agent
    sees come from one read). Enabled by BEAST_ESCALATE=1 (user-facing),
    OPENBEAST_ESCALATE=1, or run_eval/benchmark_all --escalate.

    Escalation rides INSIDE the push-diagnostics block (agents/tools.py), so
    it REQUIRES diagnostics: an --escalate arm with the checker off would be
    a measured row that silently did nothing, under a name that says it did.

    Returns (enabled, cache_component, meta). The component is
    `esc1-<sha8>` over the treatment AS DELIVERED: the index, every served
    claim file (the card sentences), the selector and facade source, the
    knobs that change how many cards are shown, and the per-language gate
    outcome on this machine (is the index served, against which toolchain).
    Hashing the index alone let an edited summary or a retuned selector
    replay rows measured under the old text, silently mixing two treatments in one A/B."""
    env = os.environ if env is None else env
    enabled = (env.get("BEAST_ESCALATE", "").strip() == "1"
               or env.get("OPENBEAST_ESCALATE", "").strip() == "1")
    if not enabled:
        return False, None, {}
    if not diag_on:
        raise SystemExit("--escalate / BEAST_ESCALATE=1 needs push-diagnostics on "
                         "(BEAST_ASSIST=1): the card is attached to the checker's "
                         "verdict, so without the checker this arm would measure nothing.")
    import hashlib
    h = hashlib.sha256()
    shas = {}
    files = _escalate_treatment()
    for rel, data in files:
        shas[rel] = hashlib.sha256(data).hexdigest()[:8]
        h.update(f"{rel}\0{len(data)}\0".encode())
        h.update(data)
    # The RAW knob value, not the parsed one: an unset knob and an explicit
    # default then read as two eras, which costs a rerun, never a mixed row.
    knobs = {k: env.get(k, "").strip() for k in ESCALATE_KNOBS}
    for k, v in knobs.items():
        h.update(f"{k}={v}\0".encode())
    # The served OUTCOME of the code not hashed as source (see
    # ESCALATE_OUTCOME_COVERED): per language, the index's toolchain stamp,
    # the installed version as the gate compares it, and whether cards are
    # served at all; plus the parsed card cap. Without it, a zig upgrade or a
    # packs._short_version edit silenced every card under the same esc1 key.
    gate = _escalate_gate(dict(files)["escalate-index.json"])
    h.update(b"gate\0" + json.dumps(gate, sort_keys=True).encode())
    sha8 = h.hexdigest()[:8]
    return True, f"esc1-{sha8}", {"treatment_sha": sha8,
                                  "index_sha": shas["escalate-index.json"],
                                  "files": shas, "knobs": knobs, "gate": gate}


_ITER_LINE = re.compile(r"^\[iter (\d+)/(\d+)\]\s*$", re.MULTILINE)
_DONE_LINE = re.compile(r"^Task complete \(iteration (\d+)\)\s*$", re.MULTILINE)


def _parse_iterations(stdout: str) -> int | None:
    """Iterations the agent actually used (co-primary Tier-3 readout:
    iterations-to-fix). Prefers the runner's `Task complete (iteration N)`
    line; falls back to the last `[iter N/M]` marker. None when neither is
    in the retained stdout tail."""
    m = _DONE_LINE.search(stdout)
    if m:
        return int(m.group(1))
    its = _ITER_LINE.findall(stdout)
    return int(its[-1][0]) if its else None


# Environmental resource exhaustion in validation/setup output. None of these
# is a verdict on the model's code: RLIMIT_NPROC is checked against every
# task the real uid owns machine-wide, so a busy desktop makes a trivial
# `cd dir && ./x` fail to fork; a disk filled by a research quantize makes a
# zig build fail to write its cache. The 2026-09-10..17 rows banked 19 such
# "failures" (ud-iq3s 115_fft_b / 136_gf256_e, 10 of 30 champion C1 zig
# rows), and they replayed into paired verdicts as model regressions.
_ENV_ERROR_RE = re.compile(
    r"Resource temporarily unavailable"
    r"|\bEAGAIN\b"
    r"|\bSystemResources\b"
    r"|thread constructor failed"
    r"|can't start new thread"
    r"|pthread_create\b[^\n]*fail"
    r"|No space left on device"
    r"|\bNoSpaceLeft\b"
    r"|\bENOSPC\b"
    r"|\[Errno 28\]"
    r"|Disk quota exceeded"
    r"|\bDiskQuota\b",
    re.IGNORECASE)


def env_error_signature(text: str | None) -> str | None:
    """The first line of `text` showing environmental resource exhaustion
    (fork/thread EAGAIN, ENOSPC), or None. Used both to keep the evidence in
    a truncated validation_output and to refuse caching the row."""
    if not text:
        return None
    m = _ENV_ERROR_RE.search(text)
    if not m:
        return None
    start = text.rfind("\n", 0, m.start()) + 1
    end = text.find("\n", m.end())
    return text[start:end if end != -1 else len(text)].strip()[:200]


# Row reasons recorded BEFORE the agent runs: the unit put no load on the
# model server.
NO_AGENT_REASONS = frozenset({"low_disk", "server_unhealthy", "setup_failed"})


def low_disk(min_free_gb: float | None = None) -> str | None:
    """A message when a filesystem the eval writes to (the cache/results
    tree, $HOME for compiler caches, /tmp for fixtures) is below the floor,
    else None. Floor: OPENBEAST_EVAL_MIN_FREE_GB (default 5; 0 disables).
    /home is shared with the weights and with research quantizes that write
    12-55 GB each; a unit that starts on a nearly-full disk dies in its
    build with ENOSPC, not on its merits."""
    import shutil as _sh
    if min_free_gb is None:
        try:
            min_free_gb = float(os.environ.get("OPENBEAST_EVAL_MIN_FREE_GB", "5"))
        except ValueError:
            min_free_gb = 5.0
    if min_free_gb <= 0:
        return None
    for path in (EVALS_DIR, os.path.expanduser("~"), "/tmp"):
        try:
            free_gb = _sh.disk_usage(path).free / 1e9
        except OSError:
            continue
        if free_gb < min_free_gb:
            return (f"only {free_gb:.1f} GB free on the filesystem holding {path} "
                    f"(floor {min_free_gb:g} GB, OPENBEAST_EVAL_MIN_FREE_GB)")
    return None


def cacheable_result(result: dict) -> bool:
    """A result row may enter the cache only if it is a genuine verdict.
    Environmental deaths must retry clean on the next run:
      exit < 0 — the agent died to a signal (-1 harness wall-timeout,
        -15 a Ctrl-C'd sweep, -9 the OOM reaper). The 2026-08-22 interrupt
        banked four SIGTERM'd in-flight units as 0-token "failures" that
        silently replayed in the 2026-09-08 Phase A' rerun.
      0 completion tokens + failed — the model never produced anything
        (server crash/restart window, dead endpoint); a capability verdict
        requires the model to have actually spoken.
      failed + reason server_error / api_errors >= 1 — the server died or
        errored AFTER the model had spoken. The runner burns its remaining
        iterations on connection errors and exits 0 with tokens > 0; the
        2026-09-15 greedy P0 run banked 7 such zig "FAILs" that the Tier-3
        verdict then replayed.
      failed + reason env_error / exhaustion text in validation_output —
        fork/thread EAGAIN or a full disk killed the VALIDATOR, not the
        model's code (see _ENV_ERROR_RE).
        Unless it repeated env_error_bank_after() times for the key
        (env_error_repeats): then the model's own program is the cause.
      failed + reason validator_timeout / run_validation's timeout message
        — the validator ran out of time, which a contended host does to a
        correct solution (its own compile is most of the budget). 17 such
        rows were banked by 2026-10-09 and replayed as model regressions.
        Unless it repeated for the key (validator_timeout_repeats): then the
        solution itself hangs.
    A PASS is always a genuine verdict: infrastructure trouble can only
    make a unit fail, never make it pass."""
    if (result.get("agent_exit_code") or 0) < 0:
        return False
    if result.get("passed"):
        return True
    if not result.get("tokens_completion"):
        return False
    if result.get("reason") in ("server_error", "env_error", "validator_timeout"):
        return False
    if (result.get("api_errors") or 0) > 0:
        return False
    if (env_error_signature(result.get("validation_output"))
            and not result.get("env_error_repeats")):
        return False
    if (validator_timed_out(result.get("validation_output"))
            and not result.get("validator_timeout_repeats")):
        return False
    return True


def env_error_bank_after() -> int:
    """How many env_error verdicts in a row for one cache key make it a
    genuine FAIL (OPENBEAST_EVAL_ENV_ERROR_BANK_AFTER, default 3). Machine
    contention is transient; the same exhaustion three runs running is the
    model's program exhausting the machine."""
    try:
        return max(1, int(os.environ.get("OPENBEAST_EVAL_ENV_ERROR_BANK_AFTER", "3")))
    except ValueError:
        return 3


SUITES_DIR = os.path.join(EVALS_DIR, "suites")


# Renamed at the v5 era break: "v5-fast" was a v4 subset, and v5 is now a
# real suite. The old name still loads, with a note, for scripts in scratch/.
SUITE_ALIASES = {"v5-fast": "v4-fast"}


def suite_universe(suite: dict, tasks: list[dict]) -> set[str]:
    """The unit set a pin must partition. A pin built from v4 runs covers
    the v4 task files only (base ids 01..162), so the task files v5 adds
    never read as drift of a frozen v4 pin."""
    if suite.get("base_suite_version") == "v4":
        return {t["id"] for t in tasks
                if (suite_manifest.task_number(t.get("base_id") or t["id"]) or 0)
                <= suite_manifest.V4_MAX_TASK}
    return {t["id"] for t in tasks}


def default_suite() -> str | None:
    """The manifest a bare run uses: evals/suites/<SUITE_VERSION>.json when
    it is a v5-format manifest, else None (every task file, the v4 rule)."""
    ver = capture_suite_version()
    path = os.path.join(SUITES_DIR, f"{ver}.json")
    try:
        with open(path) as f:
            return ver if suite_manifest.is_manifest(json.load(f)) else None
    except (OSError, ValueError):
        return None


def load_suite(name: str) -> dict:
    """Load a pinned suite subset from evals/suites/{name}.json and validate
    it against the task specs: units + assumed_passed + assumed_failed must
    exactly cover the effective unit set, or the pin has drifted from
    evals/tasks/ and running it would silently mis-score.

    A v5-format manifest (evals/suites/v5.json) loads through
    suite_manifest.load_manifest instead: every unit resolved against
    evals/tasks/, and refused if the coverage floor leaves a cell empty.
    A pin that declares a coverage_floor is refused the same way."""
    if name in SUITE_ALIASES:
        print(f"NOTE: suite {name!r} was renamed {SUITE_ALIASES[name]!r} at the v5 era break")
        name = SUITE_ALIASES[name]
    path = os.path.join(SUITES_DIR, f"{name}.json")
    if not os.path.isfile(path):
        avail = sorted(os.path.splitext(f)[0] for f in os.listdir(SUITES_DIR)
                       if f.endswith(".json")) if os.path.isdir(SUITES_DIR) else []
        raise SystemExit(f"unknown suite {name!r} (available: {', '.join(avail) or 'none'})")
    with open(path) as f:
        suite = json.load(f)
    if suite_manifest.is_manifest(suite):
        man = suite_manifest.load_manifest(name, tasks=load_tasks(None), strict=True)
        man["_manifest"] = True
        return man
    pinned = set(suite["units"]) | set(suite.get("assumed_passed", [])) | set(suite.get("assumed_failed", []))
    all_tasks = load_tasks(None)
    actual = suite_universe(suite, all_tasks)
    if pinned != actual:
        missing = sorted(actual - pinned)[:5]
        stale = sorted(pinned - actual)[:5]
        raise SystemExit(
            f"suite {name!r} has drifted from evals/tasks/ "
            f"({len(actual - pinned)} unpinned units e.g. {missing}; "
            f"{len(pinned - actual)} stale pins e.g. {stale}). "
            f"Regenerate with evals/make_fast_suite.py --generate (deliberate act).")
    if suite.get("coverage_floor"):
        import make_fast_suite  # noqa: PLC0415
        gaps = make_fast_suite.pin_coverage_gaps(suite, suite["coverage_floor"], all_tasks)
        if gaps:
            raise SystemExit(f"suite {name!r} refused: {len(gaps)} cell(s) under its coverage "
                             f"floor {suite['coverage_floor']} (e.g. {gaps[:3]})")
    suite["suite"] = suite.get("suite") or name
    return suite


def _run_reaped(cmd, timeout: int, shell: bool = False) -> tuple[int, str]:
    """Thin wrapper around tools.run_reaped for eval-harness callers.

    tools.run_reaped only supports shell=True (it manages the process group
    and rlimit itself). For non-shell commands we shell-quote them into a
    single string so the same safety guarantees apply.

    Returns (returncode, merged stdout+stderr). Raises TimeoutExpired.
    """
    if isinstance(cmd, list):
        shell_cmd = " ".join(shlex.quote(c) for c in cmd)
    else:
        shell_cmd = cmd
    return run_reaped(shell_cmd, timeout)


def run_setup(task: dict, log=print) -> bool:
    """Run task setup command. Returns True on success. Timeouts and
    unexpected errors are recorded as failures, not crashes — a single bad
    fixture must not abort the whole sweep (mirrors run_validation)."""
    setup = task.get("setup")
    if not setup:
        return True
    try:
        returncode, output = _run_reaped(setup, timeout=30, shell=True)
    except subprocess.TimeoutExpired:
        log("  Setup timed out")
        return False
    except Exception as e:
        log(f"  Setup error: {e}")
        return False
    if returncode != 0:
        log(f"  Setup failed: {output[:200]}")
        return False
    return True


_TOKEN_LINE = re.compile(r"^TOKENS:\s+prompt=(\d+)\s+completion=(\d+)\s+total=(\d+)\s*$", re.MULTILINE)
_COMPACT_LINE = re.compile(r"^COMPACTIONS:\s+(\d+)\s*$", re.MULTILINE)
_API_ERRORS_LINE = re.compile(r"^API_ERRORS:\s+(\d+)\s*$", re.MULTILINE)
_API_ERROR_EVENT = re.compile(r"^\s*API error: ", re.MULTILINE)


def _parse_api_errors(stdout: str) -> int:
    """Failed model calls the runner reported. Prefers the stable
    `API_ERRORS: n` line; a runner that died before printing it (or an
    older runner) is counted from its `API error:` event lines instead."""
    matches = list(_API_ERRORS_LINE.finditer(stdout))
    if matches:
        return int(matches[-1].group(1))
    return len(_API_ERROR_EVENT.findall(stdout))


def _parse_compactions(stdout: str) -> int:
    """Context-compaction events the runner reported (0 when absent)."""
    matches = list(_COMPACT_LINE.finditer(stdout))
    return int(matches[-1].group(1)) if matches else 0


def _parse_tokens(stdout: str) -> dict:
    """Pull the last TOKENS: prompt=… completion=… total=… line from runner stdout."""
    matches = list(_TOKEN_LINE.finditer(stdout))
    if not matches:
        return {"prompt": 0, "completion": 0, "total": 0}
    m = matches[-1]
    return {"prompt": int(m.group(1)), "completion": int(m.group(2)), "total": int(m.group(3))}


def agent_wall_s(task: dict, max_iter: int, cap_override: float | None = None) -> float:
    """The agent's wall budget before timeout_scale. A unit that declares a
    wall cap (task field `max_wall_s`, or `_max_wall_s` from a v5 manifest)
    gets exactly that: v5 replaces `max_iter*60`, which reached 80 minutes
    for a 40-iteration unit at --jobs 4. Units with no cap keep the v4 rule.
    `cap_override` (--unit-wall-cap) can only tighten."""
    cap = task.get("max_wall_s") or task.get("_max_wall_s")
    wall = float(cap) if cap else float(max_iter * 60)
    if cap_override:
        wall = min(wall, float(cap_override))
    return wall


def run_agent(task: dict, base_url: str, max_iter_override: int | None = None,
              timeout_scale: float = 1.0, env_overlay: dict | None = None,
              wall_s: float | None = None) -> dict:
    """Run the agent against a task. Returns timing, iteration, and token info.

    timeout_scale: multiplier on the per-iteration wall budget. Under --jobs
    parallelism, per-stream decode drops (streams share GPU bandwidth), so the
    single-stream-calibrated 60 s/iteration budget silently shrinks in
    effective tokens — slow-but-progressing units get killed as "timeouts"
    (measured 2026-08-20: 3 spurious fails in the first 134 units of a
    --jobs 4 run). Scaling the budget restores sequential-equivalent
    semantics; the timeout exists to catch hung agents, not slow ones."""
    max_iter = max_iter_override or task.get("max_iter", 15)
    start_time = time.time()

    cmd = [
        sys.executable, RUNNER_PATH,
        "--base-url", base_url,
        "--max-iter", str(max_iter),
        "--workdir", "/tmp",
    ]
    # Tier-3 awareness pack (docs/LANG_AWARENESS_PLAN.md §5): run_eval
    # annotates units whose language has a pack with `_context_file`
    # (underscore key ⇒ excluded from the task hash; the pack1-<sha8> era
    # component carries the pack identity instead). The runner puts the
    # file into the system prompt as a delimited "Background context"
    # block — zero runner.py edits, so the context hash is untouched.
    if task.get("_context_file"):
        cmd += ["--context-file", task["_context_file"]]
    cmd.append(task["task"])

    # R1 path guard (2026-09-10): 25% of the diagnostics-A/B campaign's
    # zig failures were FileNotFound at validation — right code, wrong
    # place. Extract the task's /tmp/eval* paths and hand them to the
    # agent's tool layer (OPENBEAST_TASK_PATHS), which warns on
    # wrong-place writes and refuses task_done while expected files are
    # missing. Passed via env= (NOT os.environ mutation) so --jobs
    # parallel tasks cannot race each other's path sets.
    spec_text = " ".join(str(task.get(k, ""))
                         for k in ("task", "validation", "setup", "cleanup"))
    expected = sorted(set(re.findall(r"/tmp/eval[\w\-./]*", spec_text)))
    child_env = dict(os.environ)
    # Per-arm treatment env (diag / escalate / packs, both spellings) for
    # THIS unit: an arm scheduler runs several treatments in one process, so
    # the flags travel per child, never through os.environ. None = unset.
    for k, v in (env_overlay or {}).items():
        if v is None:
            child_env.pop(k, None)
        else:
            child_env[k] = v

    # === THE EVAL MARKER — beast-chat lock L1 (E1) ========================
    # This is the ONE fact about a child of this function that is always
    # true: it is a measured evaluation unit. Everything else the old guard
    # relied on was circumstantial. OPENBEAST_TASK_PATHS (below) is derived
    # from whether the task SPEC happens to name a /tmp/eval path — today all
    # 291 variants do, which is luck, not a guarantee. And the "opt-in",
    # OPENBEAST_BEAST_CHAT, is exported UNCONDITIONALLY by
    # scripts/lib/conf.sh, so `dict(os.environ)` on the line above handed it
    # straight to every child in any configured shell: a reviewer drove a
    # real operator message into a measured unit through this exact path.
    #
    # So: set the marker always, and strip the flag that was never a lock.
    # agents/runner.py:_steering_enabled() checks OPENBEAST_EVAL first, and
    # its remaining opt-in is explicit argv only.
    #
    # This file is deliberately NOT in evals/cache.py CONTEXT_FILES, so this
    # costs no cache era — verified before and after the change.
    child_env["OPENBEAST_EVAL"] = "1"
    child_env.pop("OPENBEAST_BEAST_CHAT", None)
    # ======================================================================

    if expected:
        child_env["OPENBEAST_TASK_PATHS"] = json.dumps(expected)
    else:
        child_env.pop("OPENBEAST_TASK_PATHS", None)
    # Low-churn eval mode rides the same env read the runner uses.
    if os.environ.get("OPENBEAST_EVAL_GREEDY", "") == "1":
        child_env["OPENBEAST_EVAL_GREEDY"] = "1"

    # start_new_session so an agent timeout SIGKILLs the runner's whole
    # process group, not just the runner. Its bash-tool children run in
    # their OWN sessions (agents/tools.py run_reaped), so the timeout path
    # walks the tree and kills those groups too (_kill_agent_tree).
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=child_env,
    )
    with _live_pgid_lock:
        _live_pgids.add(proc.pid)          # == pgid: start_new_session above
    try:
        # Rough budget: 1 min per iteration at single-stream decode, scaled up
        # under parallel contention (see timeout_scale in the docstring).
        budget = wall_s if wall_s is not None else max_iter * 60
        stdout, stderr = proc.communicate(timeout=int(budget * timeout_scale))
        elapsed = time.time() - start_time
        tokens = _parse_tokens(stdout)
        return {
            "exit_code": proc.returncode,
            "elapsed_seconds": round(elapsed, 1),
            "stdout": stdout[-2000:],  # last 2K of output
            "stderr": stderr[-1000:],
            "tokens": tokens,
            "iterations": _parse_iterations(stdout),
            "compactions": _parse_compactions(stdout),
            "api_errors": _parse_api_errors(stdout),
        }
    except subprocess.TimeoutExpired:
        _kill_agent_tree(proc.pid)
        proc.kill()
        # What the runner had flushed before the kill. It prints its TOKENS
        # line only on a normal exit, so today this is almost always
        # without one and the row keeps tokens 0 (scoring counts such rows
        # per board row). Parsed anyway: a recorded 0 for a unit that spent
        # 20 minutes of decode understates TOKENS, and any count beats none.
        partial = ""
        try:
            partial = proc.communicate(timeout=5)[0] or ""
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        elapsed = time.time() - start_time
        return {
            "exit_code": -1,
            "elapsed_seconds": round(elapsed, 1),
            "stdout": "(timed out)",
            "stderr": "",
            "tokens": _parse_tokens(partial),
            "iterations": None,
        }
    finally:
        # Whatever happened, this group is no longer ours to reap.
        with _live_pgid_lock:
            _live_pgids.discard(proc.pid)


def run_pre_validate(task: dict, log=print):
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
    try:
        _run_reaped(cmd, timeout=30, shell=True)
    except subprocess.TimeoutExpired:
        log("  (pre_validate timed out — continuing to validation)")
    except Exception as e:
        log(f"  (pre_validate error: {e} — continuing to validation)")


VALIDATION_TIMEOUT_S = 30
# What run_validation returns when the VALIDATOR ran out of time. process()
# and cacheable_result match on it: a validator timeout is not a verdict.
VALIDATION_TIMED_OUT = "Validation timed out"


def validator_timed_out(text: str | None) -> bool:
    """Whether a validation_output is run_validation's own timeout message."""
    return bool(text) and text.startswith(VALIDATION_TIMED_OUT)


def run_validation(task: dict, timeout_scale: float = 1.0) -> tuple[bool, str]:
    """Run validation. Returns (passed, output).

    timeout_scale: the same contention multiplier run_agent's wall budget
    gets. The validator compiles the solution itself (`zig build-exe -O
    ReleaseFast` is 7-12 s warm on an idle host), so under --jobs the fixed
    30 s was mostly the compiler's, and a correct solution timed out."""
    validation = task.get("validation", {})
    vtype = validation.get("type", "bash")
    script = validation.get("script", "")

    if not script:
        return False, "No validation script defined"

    timeout = max(1, int(VALIDATION_TIMEOUT_S * timeout_scale))
    try:
        if vtype == "python":
            returncode, output = _run_reaped(
                [sys.executable, "-c", script], timeout=timeout,
            )
        else:
            returncode, output = _run_reaped(script, timeout=timeout, shell=True)
        passed = returncode == 0
        kept = output.strip()[:500]
        # The 500-char cut must never drop the evidence that the VALIDATOR
        # (not the model's code) died to fork/thread EAGAIN or a full disk:
        # cacheable_result reads validation_output to refuse banking it.
        sig = None if passed else env_error_signature(output)
        if sig and not env_error_signature(kept):
            kept = kept[:480] + "\n[...] " + sig
        return passed, kept
    except subprocess.TimeoutExpired:
        return False, f"{VALIDATION_TIMED_OUT} ({timeout}s)"
    except Exception as e:
        return False, f"Validation error: {e}"


def run_cleanup(task: dict, log=print):
    """Run cleanup command. Failures are logged, never fatal — leftover /tmp
    fixtures are annoying, a crashed sweep is worse."""
    cleanup = task.get("cleanup")
    if not cleanup:
        return
    try:
        _run_reaped(cleanup, timeout=30, shell=True)
    except subprocess.TimeoutExpired:
        log("  (cleanup timed out)")
    except Exception as e:
        log(f"  (cleanup error: {e})")


def _last_live_results(model_slug: str) -> tuple[str, dict] | None:
    """(file name, data) of the newest results file for this model that came
    from a LIVE run (it recorded the server's flags), or None."""
    try:
        names = sorted((n for n in os.listdir(RESULTS_DIR)
                        if n.startswith(f"eval-{model_slug}-") and n.endswith(".json")),
                       reverse=True)
    except OSError:
        names = []
    for name in names:
        try:
            with open(os.path.join(RESULTS_DIR, name)) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        # Slug prefixes collide (qwen-27b-q5 vs qwen-27b-q5-k-xl): match exactly.
        if data.get("model_slug") == model_slug and (data.get("server") or {}).get("cmdline"):
            return name, data
    return None


def _cache_only_rb(model_slug: str, explicit: str | None) -> tuple[str, str]:
    """The reasoning-budget era a --cache-only replay should look up, and
    where it came from. An explicit value wins; otherwise the newest LIVE
    results file for this model. Returns ("", reason) when nothing says —
    the legacy uncapped era."""
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip(), "--reasoning-budget"
    last = _last_live_results(model_slug)
    if last:
        name, data = last
        return str(data["server"].get("reasoning_budget", "")).strip(), \
            f"from the last live run, {name}"
    return "", "no live run on record; pass --reasoning-budget to choose"


def _write_results(results_path: str, results: dict) -> None:
    """Atomically persist the results dict (tmp + os.replace). Called after
    every task so a crash mid-sweep leaves a readable partial results file
    instead of nothing."""
    tmp = results_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(results, f, indent=2)
    os.replace(tmp, results_path)


# ---------------------------------------------------------------------------
# Arms (v5, brief §6.4): several treatments inside ONE process.
#
# An arm is a named treatment: {packs, diag, escalate}. Each (unit, arm) is a
# job. A base group (variants share /tmp/eval_* fixtures) stays on one worker
# ACROSS arms, so two arms can never overwrite each other's working dirs —
# the reason arms could not run as two processes (risk R10). The arms of one
# unit run next to each other in time, so server and thermal drift hit both.
# ---------------------------------------------------------------------------

BUILTIN_ARMS = dict(suite_manifest.DEFAULT_ARMS)
BOARD_ARM = "L0"
# Languages whose units get push-diagnostics in a diag arm (the "assist
# scope"). The checker can substantively help only compiled languages and
# shell; Python's py_compile is syntax-only, so a Python unit in a diag arm
# would be a near-copy of its L0 row under a different key. Units outside
# the scope run with diagnostics OFF and share the L0 row's cache key.
# OPENBEAST_DIAG_LANGS overrides (comma list of unit languages, or "all").
DEFAULT_DIAG_SCOPE = frozenset({"go", "c", "cpp", "rust", "zig", "shell"})
# unit language -> the checker names agents/tools.py uses
_CHECKER_LANG = {"go": "go", "c": "c", "cpp": "c++", "rust": "rust", "zig": "zig",
                 "shell": "shell", "python": "python"}
# Row reasons for (unit, arm) pairs that were deliberately not measured in
# that arm: never a failure, never scored (scoring.NOT_SCORED_REASONS has
# budget_skip; ablation reads the rest).
NOT_IN_ARM = "not_in_arm"
BUDGET_SKIP = "budget_skip"
_TIER_FALLBACK_S = {"easy": 14, "medium": 35, "hard": 82, "xl": 330}


def diag_scope(env=None) -> frozenset:
    env = os.environ if env is None else env
    raw = env.get("OPENBEAST_DIAG_LANGS", "").strip()
    if not raw:
        return DEFAULT_DIAG_SCOPE
    if raw.lower() == "all":
        return frozenset(_CHECKER_LANG)
    return frozenset(suite_manifest.norm_lang(x) for x in raw.split(",") if x.strip())


def unit_lang(task: dict) -> str:
    return suite_manifest.norm_lang(task.get("language"))


class ArmCtx:
    """One treatment's derived state: the cache components and the env the
    agent subprocess gets, computed from ONE read (the §3.4 rule), plus the
    arm's own results file and counters."""

    def __init__(self, name: str | None, env, results_dir: str, stamp: str):
        self.name = name
        self.diag_on, self.diag_base, self.diag_toolchains = diagnostics_flag(env)
        self.esc_on, self.esc_component, self.esc_meta = escalate_flag(self.diag_on, env)
        self.diag_component = (f"{self.diag_base}+{self.esc_component}"
                               if self.esc_on else self.diag_base)
        self.packs_on, self.packs_component, self.packs_meta = packs_flag(env)
        self.scope = diag_scope(env)
        suffix = f"-{name}" if name else ""
        self.timing_log = (os.path.join(results_dir, f"diag-timing-{stamp}{suffix}.jsonl")
                           if self.diag_on else None)

    # -- per unit ----------------------------------------------------------
    def in_diag_scope(self, task: dict) -> bool:
        return self.diag_on and unit_lang(task) in self.scope

    def diag_for(self, task: dict) -> str | None:
        """diag2[+esc1] cache component, only for units in assist scope:
        keyed PER UNIT like the pack, so out-of-scope units share rows."""
        return self.diag_component if self.in_diag_scope(task) else None

    def pack_path(self, task: dict) -> str | None:
        if not self.packs_on:
            return None
        return self.packs_meta["paths"].get(task.get("language"))

    def pack_for(self, task: dict) -> str | None:
        return self.packs_component if self.pack_path(task) else None

    def env_overlay(self, task: dict) -> dict:
        on = self.in_diag_scope(task)
        esc = on and self.esc_on
        d, e = ("1" if on else "0"), ("1" if esc else "0")
        p = "1" if self.packs_on else "0"
        ov = {"OPENBEAST_DIAGNOSTICS": d, "BEAST_ASSIST": d,
              "BEAST_ESCALATE": e, "OPENBEAST_ESCALATE": e,
              "BEAST_PACKS": p, "OPENBEAST_PACKS": p,
              "OPENBEAST_LANG_IN_EVAL": "1" if esc else None,
              "OPENBEAST_DIAG_TIMING_LOG": self.timing_log if on else None,
              "OPENBEAST_DIAG_LANGS": (",".join(sorted(_CHECKER_LANG[x] for x in self.scope
                                                       if x in _CHECKER_LANG))
                                       if on else None),
              "OPENBEAST_EVAL_UNIT": task["id"],
              "OPENBEAST_EVAL_ARM": self.name}
        return ov

    def label(self) -> str:
        bits = [b for b, on in (("packs", self.packs_on), ("diag", self.diag_on),
                                ("escalate", self.esc_on)) if on]
        return "+".join(bits) or "bare"


def arm_env(spec: dict, base=None) -> dict:
    """The env an arm's flags are derived from: the ambient env (knobs such
    as OPENBEAST_LANG_MAX_CARDS) with the arm's three switches pinned in
    both spellings, so an ambient BEAST_ASSIST=1 can never leak into L0."""
    env = dict(os.environ if base is None else base)
    for keys, on in ((("OPENBEAST_DIAGNOSTICS", "BEAST_ASSIST"), spec.get("diag")),
                     (("BEAST_ESCALATE", "OPENBEAST_ESCALATE"), spec.get("escalate")),
                     (("BEAST_PACKS", "OPENBEAST_PACKS"), spec.get("packs"))):
        for k in keys:
            env[k] = "1" if on else "0"
    return env


def unit_est_s(task: dict, prior: dict | None = None) -> float:
    """Planning seconds for a unit: manifest/task est_s, else the unit's
    prior measured elapsed_seconds (cache), else its tier rate."""
    for k in ("_est_s", "est_s"):
        if task.get(k):
            return float(task[k])
    if prior and prior.get(task["id"]):
        return float(prior[task["id"]])
    return float(_TIER_FALLBACK_S.get(suite_manifest.tier_of(task.get("difficulty")), 35))


def order_groups_longest_first(groups: list[list], weight) -> list[list]:
    """Groups sorted by total planned seconds, longest first (stable on
    ties). One base group runs on one worker across all arms, so the
    largest group sets the tail; starting it last is the classic LPT miss."""
    return sorted(groups, key=lambda g: -sum(weight(j) for j in g))


def _summarize_diag_log(path: str | None) -> tuple[dict | None, dict | None]:
    """(latency percentiles, exposure) from an arm's diag JSONL. Exposure
    answers "did the mechanism fire at all" per unit, so a null result can
    be told apart from a mechanism that never ran (risk R9)."""
    if not path or not os.path.exists(path):
        return None, None
    rows = []
    with open(path) as f:
        for ln in f:
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
    ms = sorted(r["ms"] for r in rows if "ms" in r)
    lat = None
    if ms:
        def _pct(q: float) -> float:
            return ms[min(len(ms) - 1, int(q * len(ms)))]
        lat = {"n": len(ms), "p50": _pct(.5), "p95": _pct(.95), "p99": _pct(.99), "max": ms[-1]}
    per: dict = {}
    for r in rows:
        u = r.get("unit")
        if not u:
            continue
        e = per.setdefault(u, {"writes": 0, "errors": 0, "failed_checks": 0,
                               "hint_fired": False, "card_fired": False})
        e["writes"] += 1
        e["errors"] += int(r.get("errors") or 0)
        e["failed_checks"] += int((r.get("rc") or 0) != 0)
        e["hint_fired"] = e["hint_fired"] or bool(r.get("hint_fired"))
        e["card_fired"] = e["card_fired"] or bool(r.get("card_fired"))
    exposure = {"units_exposed": len(per),
                "units_with_failed_check": sum(1 for e in per.values() if e["failed_checks"]),
                "units_hint_fired": sum(1 for e in per.values() if e["hint_fired"]),
                "units_card_fired": sum(1 for e in per.values() if e["card_fired"]),
                "per_unit": per}
    return lat, exposure


def run_eval(
    task_filter: list[str] | None = None,
    base_url: str = "http://localhost:8080/v1",
    max_iter_override: int | None = None,
    model_name: str | None = None,
    use_cache: bool = True,
    cache_only: bool = False,
    health_check=None,
    recover_cb=None,
    jobs: int = 1,
    suite: str | None = None,
    reasoning_budget: str | None = None,
    arms: list[str] | None = None,
    arm_units: dict | None = None,
    arm_meta: dict | None = None,
    deadline: float | None = None,
    slots: int | None = None,
    unit_wall_cap: float | None = None,
    pair_id: str | None = None,
) -> dict:
    """Run the full eval suite. Returns results dict.

    reasoning_budget: cache_only — the reasoning-budget era to replay
    (e.g. "20480"; "-1" = uncapped). A live run reads it from the server;
    cache_only has no server, so without this it is inferred from the
    newest LIVE results file for the model (see _cache_only_rb). On a LIVE
    run against a server this host cannot inspect (remote, vLLM,
    TensorFold) it is the DECLARED budget, recorded as such.

    cache_only: when True, never invoke the agent. Cache hits replay; cache
    misses are recorded as 'skipped_cache_miss' with passed=False. Used for
    replays of prior runs; never seated (no live host — scoring.py --rebuild
    rescores banked runs).

    health_check / recover_cb: optional pair injected by benchmark_all.py.
    Before each live task, `health_check()` probes /health; if it returns
    False, `recover_cb()` is invoked to kill+restart the serve script. If
    recovery fails the task is recorded as `server_unhealthy` (not cached)
    and the eval aborts — silently grinding through hundreds of dead-server
    tasks is what produced the v3.5 Gemma 24% phantom result.

    jobs: parallel workers. Fixture isolation is by SCHEDULING, not path
    rewriting: every /tmp/eval_* fixture dir belongs to exactly one task
    file (enforced by tests/test_eval_jobs.py), and variants of the same
    base task share theirs — so units are grouped by base task, each group
    runs sequentially on one worker (across every arm), and distinct groups
    run in parallel, longest group first. Prompts, validations, and cache
    keys are untouched, so parallel scores are directly comparable to every
    sequential run ever recorded. Clamped to the server's /props
    total_slots when readable, and to `slots` (--slots) when given — the
    only clamp a non-llama server has.

    arms: e.g. ["L0", "L1", "L2"] — run every unit under each named
    treatment (the manifest's `arms`, else suite_manifest.DEFAULT_ARMS) in
    this one process. Returns {"arms": {arm: results}, "pair_id", "paths"}.
    A (unit, arm) whose request is byte-identical to the board arm's (same
    cache key) is SHARED: the board arm's row is copied, marked
    `shared_from`. arm_units {arm: set(ids)} restricts what an arm
    measures (the profile's stratified/cut plan); the rest are recorded as
    `not_in_arm` rows, never scored. arm_meta {arm: dict} is stamped into
    that arm's harness block (e.g. stratified sampling probabilities).

    deadline: epoch seconds. A unit that would START live after it is
    recorded as `budget_skip` — never run, never cached, never scored.

    unit_wall_cap: global ceiling on any unit's agent wall budget (seconds,
    before timeout_scale); see agent_wall_s."""
    import cache  # local import — keeps run_eval importable in environments without cache.py
    if cache_only and not use_cache:
        raise ValueError("cache_only requires use_cache=True (cache_only is meaningless without cache lookup)")
    suite_pin = None
    manifest = None
    if suite:
        if task_filter is not None:
            raise ValueError("--suite and --tasks are mutually exclusive")
        loaded = load_suite(suite)
        if loaded.get("_manifest"):
            manifest = loaded
            task_filter = sorted(manifest["by_task"])
        else:
            suite_pin = loaded
            suite = loaded.get("suite", suite)
            task_filter = list(suite_pin["units"])
    suite_version = capture_suite_version()

    # --- arms -----------------------------------------------------------
    arm_specs = dict((manifest or {}).get("arms") or BUILTIN_ARMS)
    if arms:
        unknown = [a for a in arms if a not in arm_specs]
        if unknown:
            raise SystemExit(f"unknown arm(s) {unknown}; known: {sorted(arm_specs)}")
        if len(set(arms)) != len(arms):
            raise SystemExit(f"--arms lists an arm twice: {arms}")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if arms:
        ctxs = [ArmCtx(a, arm_env(arm_specs[a]), RESULTS_DIR, timestamp) for a in arms]
        board = (manifest or {}).get("board_arm", BOARD_ARM)
        # The board arm's context, even when this process does not RUN it
        # (a profile's L1+L2 pass after its L0 pass): shared rows and key
        # equality are decided against it.
        base_ctx = next((c for c in ctxs if c.name == board), None) or \
            ArmCtx(board, arm_env(arm_specs.get(board, {})), RESULTS_DIR, timestamp)
    else:
        # Legacy single run: the treatment comes from the ambient env, and
        # is pinned there in BOTH spellings so an ambient rig-wide
        # BEAST_ASSIST=1 / BEAST_PACKS=1 cannot leak into an OFF run.
        ctx = ArmCtx(None, os.environ, RESULTS_DIR, timestamp)
        os.environ["OPENBEAST_DIAGNOSTICS"] = "1" if ctx.diag_on else "0"
        os.environ["BEAST_ASSIST"] = "1" if ctx.diag_on else "0"
        os.environ["BEAST_ESCALATE"] = "1" if ctx.esc_on else "0"
        os.environ["OPENBEAST_ESCALATE"] = "1" if ctx.esc_on else "0"
        os.environ["BEAST_PACKS"] = "1" if ctx.packs_on else "0"
        os.environ["OPENBEAST_PACKS"] = "1" if ctx.packs_on else "0"
        if ctx.esc_on:
            os.environ["OPENBEAST_LANG_IN_EVAL"] = "1"
        else:
            os.environ.pop("OPENBEAST_LANG_IN_EVAL", None)
        if ctx.timing_log:
            os.environ["OPENBEAST_DIAG_TIMING_LOG"] = ctx.timing_log
        else:
            os.environ.pop("OPENBEAST_DIAG_TIMING_LOG", None)
        ctxs, base_ctx = [ctx], ctx

    tasks = load_tasks(task_filter)
    if manifest:
        ceiling = manifest.get("max_iter_ceiling")
        for t in tasks:
            u = manifest["by_task"].get(t["id"])
            if not u:
                continue
            # `_` keys: manifest scheduling metadata never enters task_hash.
            t["_est_s"] = u.get("est_s")
            t["_max_wall_s"] = u.get("max_wall_s") or suite_manifest.default_wall_s(
                t.get("difficulty"))
            t["_role"] = u.get("role", "scored")
            t["_stratum"] = u.get("stratum")
            t["_arms"] = list(u.get("arms") or [])
            t["_manifest_id"] = u["id"]
            if ceiling and not max_iter_override and (t.get("max_iter") or 15) > ceiling:
                t["_max_iter_ceiling"] = ceiling
    if suite_pin and len(tasks) != suite_pin["counts"]["units"]:
        raise SystemExit(f"suite {suite!r} resolved to {len(tasks)} units, "
                         f"pin declares {suite_pin['counts']['units']} — pin/tasks drift")
    if not tasks:
        print("No tasks found.")
        return {}

    if not model_name:
        if cache_only:
            # Cache-only doesn't talk to the server — there might be no
            # server running. Require the user to name the model so the
            # cache key resolves correctly.
            raise ValueError("--cache-only requires --model-name (no live server query in cache-only mode)")
        model_name = detect_model(base_url)
    model_slug = slugify(model_name)
    gpu_info = capture_gpu_info() if not cache_only else None
    engine_info = capture_inference_engine_info() if not cache_only else None
    server_info = capture_server_config(base_url) if not cache_only else None
    if not cache_only and not server_info:
        # Not a local llama-server: record what the endpoint says about
        # itself, and the DECLARED reasoning budget (vLLM / TensorFold /
        # remote rows used to fall back to "uncapped" silently).
        server_info = capture_remote_server_info(base_url, reasoning_budget)
    # Reasoning-budget cache-era component (see cache.cache_key): a finite
    # --reasoning-budget on the live server stamps its value into every
    # cache key so capped and uncapped rows can never replay across eras.
    # Uncapped (absent or -1) omits the component — legacy keys unchanged.
    # cache_only mode has no live server to ask: the era comes from
    # reasoning_budget or the model's newest live results file. Guessing
    # "uncapped" missed every current-era (.rb20480) key and recorded the
    # whole suite as skipped_cache_miss.
    # Low-churn eval mode (2026-09-10): greedy decoding, own cache era,
    # stamped in provenance. Set via env OPENBEAST_EVAL_GREEDY=1 or the
    # --greedy CLI flag (benchmark_all passes it through as env).
    greedy_mode = os.environ.get("OPENBEAST_EVAL_GREEDY", "") == "1"
    rb_component = None
    _rb = ""
    if server_info:
        _rb = str(server_info.get("reasoning_budget", "")).strip()
    elif cache_only:
        _rb, _rb_src = _cache_only_rb(model_slug, reasoning_budget)
        print(f"Cache-only reasoning-budget era: {_rb or 'uncapped'} ({_rb_src})")
    if _rb and _rb != "-1":
        rb_component = _rb
    # Environment identity (weights / engine / serve KV flags / toolchains).
    # ALWAYS stamped on live rows and in provenance, so a replay banked
    # under a different environment is counted and disclosed; it enters
    # the cache key only with OPENBEAST_EVAL_ENV_ERA=1 (see env_era_enabled).
    env_component, env_info = (None, None)
    if not cache_only:
        env_component, env_info = env_fingerprint(server_info, engine_info)
    else:
        _last = _last_live_results(model_slug)
        if _last:
            env_component = (_last[1].get("harness") or {}).get("env_component")
            env_info = (_last[1].get("harness") or {}).get("env")
    env_key = env_component if env_era_enabled() else None
    if env_era_enabled() and not env_component:
        raise SystemExit("OPENBEAST_EVAL_ENV_ERA=1 in --cache-only mode needs a live results "
                         "file for this model that recorded its env_component")

    jobs = max(1, int(jobs))
    if cache_only and jobs > 1:
        print(f"--jobs {jobs} ignored in --cache-only mode (replay is disk-bound)")
        jobs = 1
    if jobs > 1:
        probed = _fetch_server_slots(base_url)
        limit = min(x for x in (probed, slots) if x) if (probed or slots) else None
        if limit is not None and jobs > limit:
            src = "--slots" if slots and limit == slots and (not probed or slots <= probed) \
                else "the server"
            print(f"--jobs {jobs} clamped to {limit}: {src} exposes {limit} slot(s) (-np); "
                  f"excess workers would queue server-side and the wait would count "
                  f"against per-task timeouts (MTP configs force -np 1)")
            jobs = limit
        elif limit is None:
            print(f"WARNING: could not read /props total_slots (gated install, or not "
                  f"llama.cpp?) — running --jobs {jobs} unclamped; pass --slots N for a "
                  f"vLLM/TensorFold server, and against an -np 1 (MTP) server this queues "
                  f"and trips per-task timeouts")

    # Per-iteration wall budget scales with contention: N concurrent streams
    # share GPU bandwidth, so per-stream decode slows with N (measured ~1.6×
    # at jobs=4). Linear ramp with margin — jobs=4 → 2.0× — restores
    # sequential-equivalent budgets; see run_agent's docstring.
    timeout_scale = 1.0 if jobs == 1 else 1.0 + (jobs - 1) / 3.0

    if arms and not pair_id:
        pair_id = f"{model_slug}-{timestamp}"

    def _results_path(c: ArmCtx) -> str:
        if c.name:
            return os.path.join(RESULTS_DIR, f"eval-{model_slug}-{c.name}-{timestamp}.json")
        return os.path.join(RESULTS_DIR, f"eval-{model_slug}-{timestamp}.json")

    print(f"Eval started — {len(tasks)} tasks" + (f" × arms {','.join(arms)}" if arms else ""))
    print(f"Model:  {model_name}")
    print(f"Server: {base_url}")
    if jobs > 1:
        print(f"Jobs:   {jobs} parallel workers (same-base variants stay sequential across "
              f"arms; longest group first; per-iteration timeout ×{timeout_scale:.2f} for contention)")
    if gpu_info:
        print(f"GPU:    {gpu_info.get('name', '?')} ({gpu_info.get('memory_total_mib', '?')} MiB, driver {gpu_info.get('driver_version', '?')})")
    if engine_info and engine_info.get("build"):
        head = (engine_info.get("source_head") or "")[:9]
        head_note = f"; source HEAD {head}" if head and head != engine_info.get("commit", "") else ""
        print(f"Engine: llama.cpp {engine_info.get('build', '?')} ({engine_info.get('commit', '?')}){head_note}")
    if server_info and server_info.get("cmdline"):
        rb = server_info.get("reasoning_budget", "unlimited (default)")
        print(f"Serve:  -np {server_info.get('parallel_slots', '?')} -c {server_info.get('context', '?')} "
              f"kv {server_info.get('kv_cache_type', '?')} reasoning-budget {rb}")
    elif server_info:
        print(f"Serve:  {server_info.get('engine', 'unknown engine')} "
              f"(via {server_info.get('source')}) reasoning-budget "
              f"{server_info.get('reasoning_budget', 'undeclared')}")
    if greedy_mode:
        print("Decode: GREEDY (low-churn eval mode — leaderboard-ineligible era)")
    for c in ctxs:
        tag = f"[{c.name}] " if c.name else ""
        if c.diag_on:
            print(f"Diag:   {tag}push-diagnostics ON for {','.join(sorted(c.scope))} units "
                  f"({', '.join(c.diag_toolchains) or 'no toolchains?'}) — cache era {c.diag_base}")
        if c.esc_on:
            print(f"Escal:  {tag}beast-lang escalation ON (treatment {c.esc_meta['treatment_sha']}, "
                  f"index {c.esc_meta['index_sha']}) — "
                  f"cache era {c.esc_component} (leaderboard-ineligible experiment rows)")
        if c.packs_on:
            packed = sum(1 for t in tasks if c.pack_path(t))
            print(f"Packs:  {tag}awareness packs ON ({', '.join(f'{k}={v}' for k, v in c.packs_meta['sha'].items())}) — "
                  f"cache era {c.packs_component} on {packed}/{len(tasks)} units "
                  f"(leaderboard-ineligible experiment rows)")
    if suite_pin:
        c = suite_pin["counts"]
        print(f"Suite:  {suite} ({c['units']} pinned units = {c['discriminating']} discriminating "
              f"+ {c['tripwires']} tripwires; {c['assumed_passed'] + c['assumed_failed']} imputed; "
              f"leaderboard-ineligible partial of {suite_pin.get('base_suite_version', 'v4')})")
    if manifest:
        n_sc = sum(1 for t in tasks if t.get("_role") == "scored")
        print(f"Suite:  {manifest['suite']} manifest ({n_sc} scored + {len(tasks) - n_sc} probe "
              f"units; no imputation)")
    if deadline:
        print(f"Budget: units starting after {datetime.fromtimestamp(deadline):%H:%M:%S} "
              f"are recorded as budget_skip (never scored)")
    for c in ctxs:
        print(f"Results{' [' + c.name + ']' if c.name else ''}: {_results_path(c)}")
    print("=" * 60)

    def _harness(c: ArmCtx) -> dict:
        h = {"diagnostics": c.diag_on,
             # The eval era (scripts/eval-era.sh): rows either side of
             # a change to it are not comparable, and until this was
             # stamped the board could only infer it from cache keys.
             "era": cache.context_hash(),
             "greedy": greedy_mode,
             "packs": dict(c.packs_meta.get("sha", {})) if c.packs_on else {},
             **({"packs_component": c.packs_component} if c.packs_on else {}),
             **({"escalate": c.esc_meta, "escalate_component": c.esc_component}
                if c.esc_on else {}),
             **({"toolchains": c.diag_toolchains,
                 "diag_scope": sorted(c.scope)} if c.diag_on else {}),
             **({"rb_component": rb_component} if rb_component else {}),
             **({"env_component": env_component, "env": env_info,
                 "env_in_cache_key": bool(env_key)} if env_component else {}),
             **({"unit_wall_cap": unit_wall_cap} if unit_wall_cap else {})}
        if c.name:
            h.update({"arm": c.name, "arm_label": c.label(), "pair_id": pair_id,
                      "arms_in_process": list(arms), "board_arm": base_ctx.name,
                      **({"arm_meta": arm_meta[c.name]} if arm_meta and c.name in arm_meta else {})})
        if deadline:
            h["deadline"] = deadline
        return h

    states: dict[str | None, dict] = {}
    for c in ctxs:
        states[c.name] = {
            "ctx": c,
            "path": _results_path(c),
            "results": {
                "timestamp": datetime.now().isoformat(),
                "model": model_name,
                "model_slug": model_slug,
                "base_url": base_url,
                "suite_version": suite_version,
                "gpu": gpu_info,
                "inference_engine": engine_info,
                "server": server_info,
                "runtime": capture_runtime_info(),
                "jobs": jobs,
                "suite_selection": suite if suite_pin else None,
                **({"suite_manifest": manifest["suite"]} if manifest else {}),
                "cache_only": cache_only,
                "harness": _harness(c),
                "tasks": [],
                "summary": {"total": len(tasks), "passed": 0, "failed": 0},
            },
            "indexed": {},
            "counters": {"cache_hits": 0, "cache_misses_skipped": 0, "aborted_skips": 0,
                         "env_drift_replays": 0, "shared": 0, "not_in_arm": 0,
                         "budget_skips": 0},
        }

    state_lock = threading.Lock()   # results dicts + counters + results files
    print_lock = threading.Lock()   # whole-task output blocks in parallel mode
    health_lock = threading.Lock()  # one recovery attempt at a time
    abort = threading.Event()       # set on failed recovery — stop starting units
    n_arms = len(ctxs)

    def effective_max_iter_of(task: dict) -> int:
        mi = max_iter_override or task.get("max_iter", 15)
        if task.get("_max_iter_ceiling"):
            mi = min(mi, task["_max_iter_ceiling"])
        return mi

    def key_for(task: dict, c: ArmCtx) -> str:
        return cache.cache_key(task, model_slug, max_iter=effective_max_iter_of(task),
                               diag=c.diag_for(task), rb=rb_component,
                               greedy=greedy_mode, pack=c.pack_for(task), env=env_key)

    def process(idx: int, task: dict, log, c: ArmCtx) -> dict | None:
        """One (unit, arm) end-to-end: cache check → health → setup → agent
        → validate → record → cache → cleanup. Returns the recorded result,
        or None when skipped because the run aborted. `log` collects output
        lines in parallel mode so each unit's block prints contiguously."""
        st = states[c.name]
        counters = st["counters"]
        if abort.is_set():
            with state_lock:
                counters["aborted_skips"] += 1
            return None

        task_id = task["id"]
        task_name = task.get("name", task_id)
        difficulty = task.get("difficulty", "?")
        arm_tag = f" [{c.name}]" if c.name else ""

        log(f"\n[{idx + 1}/{len(tasks)}]{arm_tag} {task_name} ({difficulty})")
        log(f"  Task: {task['task'][:80]}...")

        def record(result: dict) -> dict:
            # Variant metadata, only present for tasks that declared `variants`
            if "base_id" in task:
                result.setdefault("base_id", task["base_id"])
                result.setdefault("variant_id", task["variant_id"])
                result.setdefault("language", task["language"])
                result.setdefault("variant_count", task["variant_count"])
            # role/stratum: the manifest's, else the task JSON's own
            # optional fields (a task may declare itself a probe).
            role = task.get("_role") or task.get("role")
            if role:
                result["role"] = role
            stratum = task.get("_stratum") or task.get("stratum")
            if stratum:
                result["stratum"] = stratum
            if c.name:
                result["arm"] = c.name
            with state_lock:
                st["indexed"][idx] = result
                if result.get("reason") in (NOT_IN_ARM, BUDGET_SKIP):
                    pass                                  # not an outcome
                elif result.get("passed"):
                    st["results"]["summary"]["passed"] += 1
                else:
                    st["results"]["summary"]["failed"] += 1
                # Keep results in task order regardless of completion order,
                # and persist incrementally — a crash mid-sweep must not lose
                # the tasks already completed.
                st["results"]["tasks"] = [st["indexed"][k] for k in sorted(st["indexed"])]
                _write_results(st["path"], st["results"])
            return result

        effective_max_iter = effective_max_iter_of(task)

        # Arms: a pair whose request is byte-identical to the board arm's
        # (same key) is the SAME experiment — copy the board arm's row
        # rather than re-roll it (a re-roll would read as a discordant pair
        # caused by nothing). A pair the plan excludes is not measured.
        if c.name and c is not base_ctx:
            if key_for(task, c) == key_for(task, base_ctx):
                base_row = None
                if base_ctx.name in states:
                    base_row = states[base_ctx.name]["indexed"].get(idx)
                if base_row is None and use_cache:
                    base_row = cache.cache_get(key_for(task, base_ctx))
                if base_row is not None:
                    row = dict(base_row)
                    row["shared_from"] = base_ctx.name
                    row.pop("arm", None)
                    with state_lock:
                        counters["shared"] += 1
                    log(f"  SHARED from {base_ctx.name} (identical request)")
                    return record(row)
            allowed = (arm_units or {}).get(c.name)
            planned = task.get("_arms")
            if (allowed is not None and task_id not in allowed) or \
                    (allowed is None and planned is not None and c.name not in planned):
                with state_lock:
                    counters["not_in_arm"] += 1
                log(f"  NOT IN ARM {c.name} (plan)")
                return record({"id": task_id, "name": task_name, "difficulty": difficulty,
                               "model": model_name, "passed": False, "reason": NOT_IN_ARM,
                               "elapsed_seconds": 0})

        # Cache check — same task spec + same model + same max_iter + same
        # agent context = same answer. Saves the agent run and the validation
        # step. Setup/cleanup still run on the live path so /tmp fixtures
        # land for any downstream inspection.
        ck = None
        if use_cache:
            ck = key_for(task, c)
            cached = cache.cache_get(ck)
            if cached is not None:
                cached = dict(cached)
                cached["from_cache"] = True
                with state_lock:
                    counters["cache_hits"] += 1
                    if env_component and cached.get("env_fp") != env_component:
                        counters["env_drift_replays"] += 1
                tag = "PASS" if cached.get("passed") else "FAIL"
                log(f"  CACHED ({cached.get('elapsed_seconds', 0)}s, {tag}) — skipping live run")
                return record(cached)

        # cache_only: never invoke the agent. Record a skipped placeholder.
        if cache_only:
            with state_lock:
                counters["cache_misses_skipped"] += 1
            log("  SKIPPED (cache miss; --cache-only mode)")
            return record({
                "id": task_id, "name": task_name, "difficulty": difficulty,
                "model": model_name, "passed": False,
                "reason": "skipped_cache_miss",
                "elapsed_seconds": 0,
                "from_cache": False,
            })

        # Global deadline (profile --budget-h): a unit that has not started
        # is not a failure, it is unmeasured.
        if deadline is not None and time.time() >= deadline:
            with state_lock:
                counters["budget_skips"] += 1
            log("  BUDGET SKIP (deadline reached before this unit started)")
            return record({"id": task_id, "name": task_name, "difficulty": difficulty,
                           "model": model_name, "passed": False, "reason": BUDGET_SKIP,
                           "elapsed_seconds": 0})

        # Free-space floor before each live unit: a disk filled mid-sweep
        # (a research quantize) turns every following unit into an ENOSPC
        # "failure". Stop starting units instead; a relaunch resumes from
        # the cache once space is back.
        disk_msg = low_disk()
        if disk_msg:
            abort.set()
            log(f"  ABORT: {disk_msg}; skipping all remaining tasks")
            return record({
                "id": task_id, "name": task_name, "difficulty": difficulty,
                "model": model_name, "passed": False, "reason": "low_disk",
                "elapsed_seconds": 0,
            })

        # Health check + recovery before each live task. If the server is
        # gone, restart it once (one worker at a time); if it still won't
        # come back, abort the whole run.
        if health_check is not None:
            with health_lock:
                if abort.is_set():
                    with state_lock:
                        counters["aborted_skips"] += 1
                    return None
                if not health_check():
                    log("  Server unhealthy before task — attempting recovery...")
                    if not (recover_cb and recover_cb()):
                        abort.set()
                        log("  ABORT: server unreachable; skipping all remaining tasks")
                        return record({
                            "id": task_id, "name": task_name, "difficulty": difficulty,
                            "model": model_name, "passed": False,
                            "reason": "server_unhealthy",
                            "elapsed_seconds": 0,
                            "tokens_prompt": 0, "tokens_completion": 0, "tokens_total": 0,
                        })

        # Cleanup BEFORE setup: every task's setup is `mkdir -p`, so whatever
        # a killed run left in the fixture dir (the signal handler exits
        # without cleaning up) was inherited by the next unit to use it —
        # another model, or the other arm of an A/B. If that agent then wrote
        # nothing, the leftover solution validated and a PASS was banked.
        run_cleanup(task, log=log)

        # Setup
        if not run_setup(task, log=log):
            log("  FAIL (setup failed)")
            return record({
                "id": task_id, "name": task_name, "difficulty": difficulty,
                "passed": False, "reason": "setup_failed",
                "elapsed_seconds": 0,
            })

        # Run agent (the arm's pack, if this unit's language has one)
        log("  Running agent...")
        run_task = dict(task)
        pack_path = c.pack_path(task)
        if pack_path:
            run_task["_context_file"] = pack_path
        else:
            run_task.pop("_context_file", None)
        agent_result = run_agent(run_task, base_url,
                                 effective_max_iter,
                                 timeout_scale=timeout_scale,
                                 env_overlay=c.env_overlay(task),
                                 wall_s=agent_wall_s(task, effective_max_iter, unit_wall_cap))
        log(f"  Agent finished in {agent_result['elapsed_seconds']}s")

        # Re-assert fixtures (no-op unless task declares pre_validate).
        run_pre_validate(task, log=log)

        # Validate
        passed, validation_output = run_validation(task, timeout_scale=timeout_scale)

        # Infrastructure verdicts (not model verdicts) for a FAILED unit.
        # server_error: the runner saw failed model calls, or the server is
        # gone right after the agent finished — the health check above only
        # runs BEFORE a task, so a server that died mid-task was invisible.
        # env_error: the validator died to fork/thread EAGAIN or a full disk.
        # validator_timeout: the validator ran out of its own time budget.
        api_errors = agent_result.get("api_errors", 0) or 0
        infra_reason = None
        env_strikes = 0
        vt_strikes = 0
        if not passed:
            if api_errors > 0:
                infra_reason = "server_error"
            elif (health_check is not None and agent_result["exit_code"] >= 0
                  and not health_check()):
                infra_reason = "server_error"
            elif env_error_signature(validation_output):
                infra_reason = "env_error"
                # Exhaustion the model's OWN program causes (a fork loop,
                # thousands of threads) matches the same text and would
                # rerun live forever, keeping the model off the board. An
                # env_error that keeps coming back for the same key is a
                # verdict: bank it as a plain FAIL.
                if ck is not None:
                    try:
                        strikes = cache.env_error_strike(ck)
                    except OSError:
                        strikes = 0
                    if strikes >= env_error_bank_after():
                        infra_reason = None
                        env_strikes = strikes
            elif validator_timed_out(validation_output):
                infra_reason = "validator_timeout"
                # A solution that really hangs times out every run, and
                # would rerun live forever. Same strike counter and
                # threshold as env_error: repeated for the same key, it is
                # the model's program, and banks as a plain FAIL.
                if ck is not None:
                    try:
                        strikes = cache.env_error_strike(ck)
                    except OSError:
                        strikes = 0
                    if strikes >= env_error_bank_after():
                        infra_reason = None
                        vt_strikes = strikes

        # Record result
        tokens = agent_result.get("tokens") or {"prompt": 0, "completion": 0, "total": 0}
        result = record({
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
            "iterations": agent_result.get("iterations"),
            "compactions": agent_result.get("compactions", 0),
            "api_errors": api_errors,
            **({"reason": infra_reason} if infra_reason else {}),
            **({"env_error_repeats": env_strikes} if env_strikes else {}),
            **({"validator_timeout_repeats": vt_strikes} if vt_strikes else {}),
            **({"env_fp": env_component} if env_component else {}),
        })

        if passed:
            log(f"  PASS ({agent_result['elapsed_seconds']}s)")
        elif infra_reason:
            log(f"  FAIL ({infra_reason}, not cached — retries on the next run): "
                f"{validation_output[:100]}")
        else:
            log(f"  FAIL: {validation_output[:100]}")

        # Cache the result for future reruns. We cache both PASS and
        # deterministic FAIL — replaying a known fail is replay-safe.
        # We do NOT cache environmental deaths — they must retry clean:
        #   exit == -1: harness wall-timeout.
        #   exit < 0 generally: the agent died to a SIGNAL (-15 = the user
        #     Ctrl-C'ing a sweep; -9 = OOM reaper). The 2026-08-22 interrupt
        #     banked four SIGTERM'd in-flight units as 0-token "failures"
        #     that silently replayed in the 2026-09-08 Phase A' rerun.
        #   0 completion tokens + failed: the model never produced anything
        #     (server crash/restart window, dead endpoint) — a capability
        #     verdict needs the model to have actually spoken.
        #   server_error / env_error / validator_timeout: see the
        #     infra_reason block above and cacheable_result.
        # The row is cached WITHOUT the arm label: the arm is not part of
        # the key (two arms with one key are one experiment).
        if use_cache and cacheable_result(result):
            try:
                cache.cache_put(ck, {k: v for k, v in result.items() if k != "arm"})
                cache.env_error_strikes_clear(ck)
            except Exception as e:
                log(f"  (cache write failed: {e})")

        # Cleanup
        run_cleanup(task, log=log)
        return result

    # --- scheduling ---------------------------------------------------------
    # Jobs = (unit, arm). Grouped by base task (variants share a /tmp/eval_*
    # fixture dir): a group serializes on one worker ACROSS ARMS, with the
    # arms of each unit adjacent; distinct groups parallelize.
    grouped: dict[str, list[tuple[int, dict, ArmCtx]]] = {}
    for idx, task in enumerate(tasks):
        g = grouped.setdefault(task.get("base_id", task["id"]), [])
        for c in ctxs:
            g.append((idx, task, c))
    groups = list(grouped.values())

    prior: dict = {}
    if jobs > 1 and use_cache:
        # Prior measured time for units with no est_s (the v4 suite): the
        # board-arm key's banked elapsed_seconds.
        for idx, task in enumerate(tasks):
            if task.get("_est_s") or task.get("est_s"):
                continue
            try:
                row = cache.cache_get(key_for(task, base_ctx))
            except Exception:  # noqa: BLE001
                row = None
            if row and row.get("elapsed_seconds"):
                prior[task["id"]] = row["elapsed_seconds"]

    def _job_weight(job) -> float:
        _idx, task, c = job
        planned = task.get("_arms")
        if c.name and planned is not None and c.name not in planned:
            return 0.0
        return unit_est_s(task, prior)

    if jobs == 1:
        for g in groups:
            for idx, task, c in g:
                process(idx, task, log=print, c=c)
                if abort.is_set():
                    break
            if abort.is_set():
                remaining = len(tasks) * n_arms - sum(len(s["indexed"]) for s in states.values())
                if remaining:
                    print(f"  ({remaining} remaining tasks skipped)")
                break
    else:
        groups = order_groups_longest_first(groups, _job_weight)

        def run_group(units: list[tuple[int, dict, ArmCtx]]) -> None:
            for idx, task, c in units:
                lines: list[str] = []
                process(idx, task, log=lines.append, c=c)
                if lines:
                    with print_lock:
                        print("\n".join(lines))

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for fut in [pool.submit(run_group, g) for g in groups]:
                fut.result()

    # --- per-arm finalization --------------------------------------------------
    out_by_arm = {}
    for c in ctxs:
        st = states[c.name]
        results, counters, indexed = st["results"], st["counters"], st["indexed"]
        results_path = st["path"]
        # beast-assist latency + exposure (ship-rule clause: p95 per-write
        # <= 1s; exposure tells "never fired" from "no effect").
        try:
            lat, exposure = _summarize_diag_log(c.timing_log)
        except OSError as e:
            lat, exposure = None, None
            print(f"Diag latency summary unavailable: {e}")
        if lat:
            results["harness"]["diag_latency_ms"] = lat
            print(f"Diag latency{' [' + c.name + ']' if c.name else ''}: n={lat['n']} "
                  f"p50={lat['p50']:.0f}ms p95={lat['p95']:.0f}ms p99={lat['p99']:.0f}ms "
                  f"max={lat['max']:.0f}ms")
        if exposure is not None:
            results["harness"]["diag_exposure"] = exposure

        # How many units actually ran the model (benchmark_all skips its
        # thermal cool-off when this is 0, i.e. a full cache replay).
        results["summary"]["cache_hits"] = counters["cache_hits"]
        if c.name:
            results["summary"].update({"shared": counters["shared"],
                                       "not_in_arm": counters["not_in_arm"]})
        if counters["budget_skips"]:
            results["summary"]["budget_skips"] = counters["budget_skips"]
        # Rows recorded without invoking the agent (a disk-floor abort, a
        # failed recovery, a fixture setup failure) put no load on the GPU.
        no_agent = sum(1 for r in indexed.values()
                       if r.get("reason") in NO_AGENT_REASONS | {NOT_IN_ARM, BUDGET_SKIP}
                       or r.get("shared_from"))
        results["summary"]["live_units"] = (len(indexed) - counters["cache_hits"]
                                            - counters["cache_misses_skipped"] - no_agent)
        _write_results(results_path, results)

        # Print summary
        s = results["summary"]
        print(f"\n{'=' * 60}")
        print(f"Results{' [' + c.name + ' ' + c.label() + ']' if c.name else ''}: "
              f"{s['passed']}/{s['total']} passed, {s['failed']} failed")
        cache_hits = counters["cache_hits"]
        if use_cache and cache_hits:
            print(f"Cache hits: {cache_hits}/{s['total']} ({100*cache_hits//max(1,s['total'])}% replay)")
        if c.name and (counters["shared"] or counters["not_in_arm"]):
            print(f"Shared from {base_ctx.name}: {counters['shared']}; "
                  f"not measured in this arm: {counters['not_in_arm']}")
        if counters["env_drift_replays"]:
            results["summary"]["env_drift_replays"] = counters["env_drift_replays"]
            _write_results(results_path, results)
            print(f"ENV DRIFT: {counters['env_drift_replays']} replayed row(s) were banked under a "
                  f"different (or unrecorded) weights/engine/toolchain environment than {env_component}. "
                  f"Set OPENBEAST_EVAL_ENV_ERA=1 to key the cache on it.")
        if cache_only and counters["cache_misses_skipped"]:
            print(f"Cache misses skipped: {counters['cache_misses_skipped']}/{s['total']} "
                  f"({100*counters['cache_misses_skipped']//max(1,s['total'])}%)")
        if counters["aborted_skips"]:
            print(f"Aborted: {counters['aborted_skips']} tasks skipped after server loss")
        if counters["budget_skips"]:
            print(f"Budget: {counters['budget_skips']} unit(s) never started (budget_skip, not scored)")
        import scoring  # local import, like cache — keeps import surface lazy
        if (suite_pin and not counters["aborted_skips"] and not counters["cache_misses_skipped"]
                and scoring.imputation_allowed(suite_version, c.name)):
            trip_failed = [t["id"] for t in results["tasks"]
                           if not t.get("passed") and t["id"] in set(suite_pin["tripwires"])]
            imputed = scoring.impute_suite_tasks(results["tasks"], suite_pin)
            solve, lang, cap = scoring.compute_solve_breadth(imputed)
            results["fast_suite"] = {
                "suite": suite,
                "capability_imputed": cap,
                "problem_solving_imputed": solve,
                "language_breadth_imputed": lang,
                "imputed_units": len(imputed) - len(results["tasks"]),
                "tripwire_failures": trip_failed,
            }
            _write_results(results_path, results)
            print(f"Imputed capability (v4-leaderboard scale): {cap:.2f} "
                  f"(solve {solve:.2f} / breadth {lang:.2f}; "
                  f"{results['fast_suite']['imputed_units']} saturated units imputed)")
            if trip_failed:
                print(f"TRIPWIRE FAILURES ({len(trip_failed)}): {', '.join(trip_failed)}")
                print("  This model breaks the suite's saturation assumption — the imputed "
                      "score above is NOT trustworthy. Run the full v4 suite.")
        elif suite_pin:
            print(f"No imputed score: imputation is retired for suite {suite_version}"
                  f"{' and for every arm' if c.name else ''} (measured units only)")
        print(f"Saved to: {results_path}")
        print(f"{'=' * 60}")
        out_by_arm[c.name] = results

    if not arms:
        return out_by_arm[None]
    return {"arms": out_by_arm, "pair_id": pair_id,
            "paths": {c.name: states[c.name]["path"] for c in ctxs}}


def main():
    # Before anything spawns: a SIGTERM or Ctrl-C must take our agents with us.
    # Without this, `job.sh stop` / a Stop from the phone / Ctrl-C killed this
    # process and left the agent running in its own session — holding a server
    # slot and writing the task's fixtures while the next launch re-ran it.
    _install_signal_reaper()
    parser = argparse.ArgumentParser(description="Run eval tasks against local LLM")
    parser.add_argument("--tasks", help="Comma-separated task IDs to run (default: the current "
                        "suite's manifest, evals/suites/<SUITE_VERSION>.json)")
    parser.add_argument("--all-tasks", action="store_true",
                        help="Run every task file in evals/tasks/ (the v4 rule), not the "
                             "current suite's manifest")
    parser.add_argument("--base-url", default="http://localhost:8080/v1", help="API base URL")
    parser.add_argument("--api-key-env",
                        help="Name of an env var holding the endpoint's bearer key (e.g. a "
                             "remote vLLM). Exported to the agent as OPENBEAST_API_KEY, and "
                             "--base-url is marked as this install's inference endpoint so "
                             "the runner sends it there.")
    parser.add_argument("--max-iter", type=int, help="Override max iterations for all tasks")
    parser.add_argument("--model-name", help="Override model name (default: auto-detect from /v1/models)")
    parser.add_argument("--list", action="store_true", help="List available tasks and exit")
    parser.add_argument("--no-cache", action="store_true", help="Disable result cache (force live run)")
    parser.add_argument("--cache-only", action="store_true",
                        help="Replay cache only — skip live runs entirely. Cache misses recorded as 'skipped_cache_miss'.")
    parser.add_argument("--suite", help="Run a suite from evals/suites/: a v5 manifest (v5) or a "
                        "pinned v4 subset (v4-fast; imputation-scored, v4 only). Mutually "
                        "exclusive with --tasks.")
    parser.add_argument("--reasoning-budget",
                        help="--cache-only: the reasoning-budget era to replay (e.g. 20480; "
                             "-1 = uncapped). Default: the model's last live run. On a LIVE run "
                             "against a non-llama server (vLLM, TensorFold, remote): the "
                             "budget the server runs with, declared (0 = thinking off).")
    parser.add_argument("--jobs", type=int, default=1,
                        help="Parallel eval workers (default 1). Needs a server with -np >= N "
                             "(clamped to /props total_slots when readable; MTP configs are -np 1). "
                             "Variants of the same base task always run sequentially — they share "
                             "/tmp fixtures — so scores stay comparable to sequential runs.")
    parser.add_argument("--slots", type=int,
                        help="The server's concurrent-request limit when /props cannot say "
                             "(vLLM max_num_seqs, TensorFold): --jobs is clamped to it.")
    parser.add_argument("--arms",
                        help="Comma-separated arms to run in THIS process (e.g. L0,L1,L2; "
                             "defined by the suite manifest, else L0 bare / L1 packs / "
                             "L2 packs+diag+escalate). Results go to eval-<slug>-<arm>-<ts>.json "
                             "with harness.arm and a shared harness.pair_id.")
    parser.add_argument("--unit-wall-cap", type=float,
                        help="Ceiling (s, before the --jobs timeout scale) on any unit's agent "
                             "wall budget; tightens per-unit max_wall_s, never loosens it.")
    parser.add_argument("--deadline-h", type=float,
                        help="Units that would start live after this many hours from now are "
                             "recorded as budget_skip (never scored). evals/profile.py sets it.")
    parser.add_argument("--packs", action="store_true",
                        help="Tier-3 language awareness packs (docs/LANG_AWARENESS_PLAN.md §5): "
                             "inject agents/packs/<lang>.md into the agent system prompt for units "
                             "whose language has a pack (zig only today). Own pack1-<sha8> cache "
                             "era on those units; leaderboard-ineligible. Same as BEAST_PACKS=1.")
    parser.add_argument("--diag", action="store_true",
                        help="beast-assist push diagnostics for units in assist scope "
                             "(compiled languages + shell; OPENBEAST_DIAG_LANGS overrides). "
                             "Own diag2-<sha8> cache era on those units; leaderboard-ineligible. "
                             "Same as BEAST_ASSIST=1.")
    parser.add_argument("--escalate", action="store_true",
                        help="beast-lang escalation (docs/BEAST_LANG_PLAN.md §7 P4): when the "
                             "push-diagnostics checker reports an error with a toolchain-CONFIRMED "
                             "known cause, attach the one-line fix to the same tool result. Needs "
                             "--diag / BEAST_ASSIST=1. Own esc1-<sha8> cache era; leaderboard-ineligible. "
                             "Same as BEAST_ESCALATE=1.")
    args = parser.parse_args()

    if args.jobs < 1:
        parser.error("--jobs must be >= 1")
    arms = [a.strip() for a in args.arms.split(",") if a.strip()] if args.arms else None
    if arms and (args.packs or args.diag or args.escalate):
        parser.error("--arms defines each arm's treatment; drop --packs/--diag/--escalate")
    if args.packs:
        os.environ["BEAST_PACKS"] = "1"
    if args.diag:
        os.environ["BEAST_ASSIST"] = "1"
    if args.escalate:
        os.environ["BEAST_ESCALATE"] = "1"
    if args.api_key_env:
        key = os.environ.get(args.api_key_env, "")
        if not key:
            parser.error(f"--api-key-env {args.api_key_env}: that variable is empty")
        os.environ["OPENBEAST_API_KEY"] = key
        os.environ["OPENBEAST_AGENT_INFERENCE_URL"] = args.base_url

    if args.list:
        tasks = load_tasks()
        print(f"Available tasks ({len(tasks)}):\n")
        for t in tasks:
            print(f"  {t['id']}  [{t.get('difficulty', '?'):>6}]  {t.get('name', t['id'])}")
        return

    task_filter = args.tasks.split(",") if args.tasks else None
    if args.suite and args.tasks:
        print("--suite and --tasks are mutually exclusive.", file=sys.stderr)
        sys.exit(2)
    if args.cache_only and args.no_cache:
        print("--cache-only and --no-cache are mutually exclusive.", file=sys.stderr)
        sys.exit(2)
    suite = args.suite
    if not suite and not task_filter and not args.all_tasks:
        suite = default_suite()
        if suite:
            print(f"Suite {suite}: running the manifest (evals/suites/{suite}.json); "
                  f"--all-tasks runs every task file")
    results = run_eval(
        task_filter=task_filter,
        base_url=args.base_url,
        max_iter_override=args.max_iter,
        model_name=args.model_name,
        use_cache=not args.no_cache,
        cache_only=args.cache_only,
        jobs=args.jobs,
        suite=suite,
        reasoning_budget=args.reasoning_budget,
        arms=arms,
        slots=args.slots,
        unit_wall_cap=args.unit_wall_cap,
        deadline=(time.time() + args.deadline_h * 3600) if args.deadline_h else None,
    )

    # Exit with failure if any task failed
    runs = list((results or {}).get("arms", {}).values()) if arms else [results]
    if any(r and r["summary"]["failed"] > 0 for r in runs):
        sys.exit(1)


if __name__ == "__main__":
    main()
