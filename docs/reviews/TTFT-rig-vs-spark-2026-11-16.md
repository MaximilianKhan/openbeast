# TTFT: 5090 rig (Qwen, llama.cpp) vs spark-hydra (GLM, TensorFold), 2026-11-16

Rig offline: rig-side causes are inferred from configs/docs; Spark side measured live.

> **Note, 2026-11-18.** File:line references below are as of 2026-11-16 and
> have moved. On `main` on 2026-11-18: the prefill table is
> `docs/REFERENCE.md:873` (was :868); the budget is the `--reasoning-budget 20480`
> line, `serve-qwen38-27b-uncensored-mtp-q5.sh:79` (was :76); the 15.1 s turn
> is `docs/ROUTER_SIDECAR_PLAN.md:17-18`. `probe.py`, named under "Measure
> when the rig is back", was never committed: it is not in this directory or
> anywhere in the repository. Write a fresh probe (or use `scripts/tokps.py`
> for the speed side) when the rig is back.

## Spark probes (idle pair, streamed through beast-gate)
| probe | TTFT | cached tokens |
|---|---:|---:|
| short prompt | ~160 ms | 0 |
| ~9.2K cold | ~5.3 s (~1,740 tok/s prefill) | 0 |
| same 9.2K again | ~290 ms | 9,152 |
| next turn on top | ~390 ms | 9,152 |
Rig prefill (docs/REFERENCE.md:868) ~2,100 tok/s below 40K: the 5090 prefills FASTER.
Spark /health: 89% of prompt tokens served from cache. No reasoning tokens (thinking off).
Under eval load, audit median short-reply duration 2.3-2.8 s.

## Root causes (ranked)
1. Thinking before every visible token (high). serve-qwen38-27b-uncensored-mtp-q5.sh:76 budget 20480, nothing turns it off; WebUI collapses "Thinking…". 111-133 tok/s: 500 tok = ~4 s, 1,500 = ~11 s, 20K cap = 2.5-3 min. Only rig datum: a WebUI turn at 15.1 s (docs/ROUTER_SIDECAR_PLAN.md:17).
2. Single slot (-np 1 forced by MTP) -> head-of-line queueing behind agents/evals/clients/WebUI title+tag tasks; AGENT_ROUTER classify uses the same slot (+2.7-30 s measured). Sparks: 4 streams.
3. Cache misses on long contexts (medium). 80% of rig requests >= 60K tokens where prefill is 585-1,049 tok/s -> 30-95 s per miss. Qwen 27B hybrid limits prefix reuse; newer llama.cpp context checkpoints help; b8893 unverified.
4. MTP prefill overhead (low, small). Weight streaming not involved for dense 27B.
No per-request prefix mutation found in OpenBeast (no timestamp; stable tool list). System prompt + schemas ~7-10K tokens (matters only on a miss).

## Fixes (ranked, NOT applied)
1. WebUI-only thinking off: Admin > Models > Qwen > Advanced Params: chat_template_kwargs {"enable_thinking": false} (or reasoning_budget_tokens 2048). Saves ~4-150 s/turn. Rig-wide REASONING=off moves the eval era.
2. Keep the rig slot free for chat: route agents/evals/clients to the Sparks, or the no-MTP script with -np 6 (~half per-stream tok/s); AGENT_ROUTER off or CPU sidecar.
3. Rebuild llama.cpp + --ctx-checkpoints ~32; first count `forcing full prompt re-processing` in .run/stack.log.
4. -ub 2048 -b 2048 if VRAM headroom (4.76 GB) allows; trim fixed prompt/schemas.

## Measure when the rig is back
- llama-server --version; ps -o args= -C llama-server
- probe.py (this dir) at :8080: default / thinking off / budget 1024
- grep -c 'forcing full prompt re-processing' vs 'restored context checkpoint' .run/stack.log
- /slots and /metrics requests_deferred during normal use
- AGENT_ROUTER, INSTINCT, REASONING in the rig's openbeast.conf
