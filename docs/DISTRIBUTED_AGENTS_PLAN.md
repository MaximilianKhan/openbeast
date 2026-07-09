# Distributed agents — main box executes, worker box thinks

**Status: the agent-spawn ROUTER is BUILT + wired (2026-07-08, opt-in `AGENT_ROUTER`); the main->worker DISTRIBUTED split remains planned.**

> Router shipped: `agents/router.py` + start.sh integration + conf toggle. It solves the STEP-3 blocker (models won't self-spawn). The remaining work below is the multi-node main-executes/worker-thinks split.

**The feature (Max, 2026-07-08):** let a user run OpenBeast's agents so that
the **filesystem work happens on their MAIN machine** (where their code lives,
e.g. the 5090 box) while the **model inference is served by a separate worker
machine** (e.g. a 2x3090Ti box running non-MTP models with high `-np` for
parallel agents). Opt-in and off by default — the average user is single-box —
but a meaningful minority (~1/3) will want this main→worker split, and it's a
genuinely powerful way to get real work done: local files, remote brains.

## Why this works (the load-bearing fact)

The **model and the agent are separate**. The model is an inference API; the
agent (`agents/runner.py` + `agents/tools.py`) is the process that executes the
tool calls. Filesystem ops happen **where `runner.py` runs**, not where the
model runs (`tools.py` uses local `subprocess`/`open()` with
`cwd=AGENT_WORKDIR`). So: run the agent on the main box (local files), point its
inference at the worker box. The interface between machines is a single
OpenAI-compatible HTTP call — no NFS, no SSHFS, no shared drives.

```
   MAIN box (files here)                 WORKER box
   runner.py agents  ── HTTP (tailnet) ─▶ non-MTP model, high -np
   bash/read/write act on MAIN FS  ◀───── tokens only; never touches files
```

Consistent with the no-compromise principle ([[max-intelligence-no-compromise]]):
the worker fleet runs the SAME large model class, just replicated onto more
hardware for parallelism. This is the execution half of the "Mark of the Beast"
multi-node vision.

## What already works vs. what's missing (verified 2026-07-08)

- ✅ `runner.py --base-url <url>` and `--workdir` exist. Scripted agents can do
  this TODAY: `./agent.sh --base-url http://<worker>:8080/v1 -w ~/project "task"`.
- ✅ Remote-access layer already publishes llama-server over the tailnet
  (`setup-tailscale.sh` → `https://<host>.<tailnet>.ts.net:8443/v1`).
- ❌ `mcp_server.start_agent()` does NOT expose a model URL (only task/workdir/
  max_iter/context), so agents spawned from the WebUI/chat use the LOCAL model.
- ❌ No config knob to declare a worker endpoint once and have all spawned
  agents use it.

## Config UX (the opt-in)

One new key, resolved by `scripts/lib/conf.sh` the same way as the others
(env → `openbeast.conf` → default). Empty/default = local model (today's
behavior, single-box users unaffected):

```ini
# openbeast.conf
# Route spawned agents' INFERENCE to a worker box while they execute on THIS
# machine's filesystem. Empty = use the local model server (default).
# AGENT_INFERENCE_URL=https://worker.tailnet.ts.net:8443/v1
```
Env override: `OPENBEAST_AGENT_INFERENCE_URL`.

## Implementation plan

### Phase 1 — single worker endpoint (~½ day)
1. `scripts/lib/conf.sh`: resolve `AGENT_INFERENCE_URL` (env →
   `openbeast.conf` → empty). Export `OPENBEAST_AGENT_INFERENCE_URL`.
2. `agents/mcp_server.py` `start_agent()` / `start_skill_agent()`: add a
   `base_url` param (default from `OPENBEAST_AGENT_INFERENCE_URL`, else local);
   thread it into the `runner.py` spawn exactly like `workdir` already is
   (add `--base-url` to the arg list when set). ~10 lines.
3. `agent.sh`: default `--base-url` from the same env var so headless
   `./agent.sh "task"` also honors the configured worker (still overridable).
4. `start.sh`: nothing extra beyond sourcing conf.sh (which now exports it).
5. Docs: note in `docs/REFERENCE.md` (config options) + `openbeast.conf.example`
   + a short section in `README` remote-access; update `docs/TOOLS.md`
   `start_agent` signature.
6. Verify: worker box serving a model on the tailnet; `start_agent` from the
   5090 chat spawns an agent that reads/writes 5090 files but whose tokens come
   from the worker (check `agents/logs/` shows the remote base_url).

### Phase 2 — worker fleet + load balancing (later)
- Accept multiple worker endpoints; a small least-loaded/round-robin router in
  front (the orchestrator's `start_agent` picks a slot). Ties into the
  Hardware-Profiles Phase 2 fleet work and the tailnet-native router noted in
  the multi-node INVESTIGATION (docs/TODO.md).
- On the 2x3090Ti+NVLink box: tensor-split one large model across 48GB with
  high `-np`, OR one instance per card + the router. Measure the np/KV envelope.

### Phase 3 — per-role routing (horizon)
- Route by agent type/weight (heavy agents → big worker, quick edits → local),
  once real workloads justify it.

## Prerequisites & caveats

- **GATE: ✅ SATISFIED (2026-07-08).** The orchestrator must reliably spawn
  agents in the first place — solved by the shipped agent-spawn router
  (`agents/router.py`, opt-in `AGENT_ROUTER=true`), which detects spawn
  intent deterministically instead of trusting model tool-choice (see
  docs/RESEARCH_FINDINGS.md §8–11 and the header above).
- **Data flow:** remote inference sends the file contents the agent READS to the
  worker box as context (the model must see the data to reason about it). On the
  user's own tailnet this is still their hardware, but the promise shifts from
  "no data leaves your machine" to "no data leaves your machines / your
  tailnet." State this plainly wherever the feature is documented.
- **Security:** worker endpoint should be tailnet-only (never public); reuse the
  existing `LLAMA_API_KEY` boundary if the worker is exposed more broadly.
- **Supervision:** a worker fleet needs the same pidfile/systemd discipline as
  the main stack (see start.sh daemon mode).
