# Models

OpenBeast ships **17 pre-configured models**, every one VRAM/context-measured on
the RTX 5090 reference card. Swap any of them in with one argument to
`start.sh` (e.g. `./start.sh serve-qwen-27b-q5.sh`) or set your `beastup`
default via `SERVE_SCRIPT` in `openbeast.conf`. Capability rankings (where a
model has been benchmarked) are in the [eval leaderboard](RESULTS.md) and
[`evals/README.md`](../evals/README.md).

The default is **Qwen3.6-27B Uncensored Q5_K_P**; the dense **Qwen3.6-27B
Q5_K_XL** tops the capability board, and the **35B-A3B MoE** variants trade a
little accuracy for 30–50% more speed per token.

## Core lineup (v4-benchmarked / v3.5-legacy)

| Model | Quant | Weights | Context | VRAM (measured) | Notes |
|-------|-------|---------|---------|-----------------|-------|
| **Qwen3.6-27B** | **Q5_K_XL** | **19 GB** | **350K** | **~29.5 GB** | **Top accuracy**: 97.85% on v3.5, **96.62% on v4** (271/291, landed 2026-07-09) — a statistical tie with its MTP twin (see leaderboard). Slower per-token than the MoEs. |
| Qwen3.6-27B Uncensored | Q5_K_P | 21 GB | 350K | ~30.0 GB | Uncensored fine-tune (HauhauCS Aggressive); 96.16% on v3.5 (benchmarked at 380K) |
| Qwen3.6-35B-A3B (MoE) | Q4_K_M | 20 GB | 512K | 27.8 GB | Fast MoE (3B active); 93.74% on v3.5; ~4.3 GB headroom (measured) |
| Qwen3.6-35B-A3B Uncensored | Q4_K_M | 20 GB | 512K | 27.1 GB | Fastest of the lineup but trails on accuracy (90.33% on v3.5) |
| Gemma 4 31B-it | Q5_K_XL | 20 GB | 192K | ~28.5 GB | Different family; KV cost rises with context (20→25 KB/token); reduced from 220K on 2026-05-08 after a sustained-load crash at the tight 2,080 MiB headroom |
| Qwen3.6-27B **MTP** | Q5_K_XL | 20.4 GB | 288K | 29.4 GB | MTP draft heads baked in; tuned `n-max 8 / p-min 0.0` measures **184 tok/s vs 66.8 baseline (2.75×)**. Forces `-np 1` (no parallel slots, no `--mmproj`). 2.5 GB headroom at the tuned config. **95.63% on v4** (273/291) — a statistical tie with the non-MTP Qwen 27B (96.62%) at **2.75× the token throughput**; lossless speedup, exactly as MTP promises. |
| Qwen3.6-35B-A3B **MTP** (MoE) | Q4_K_M | 22.7 GB | 512K | 28.8 GB | Same as above for the MoE; tuned `n-max 4 / p-min 0.0` measures **379 tok/s vs 259 baseline (1.46×)**. Same `-np 1` constraint; matches the non-MTP MoE's 512K ceiling (3.1 GB headroom). 93.76% on v4 (254/291). |
| Qwopus3.6-27B-v2 | Q5_K_M | 19.2 GB | 416K | 29.3 GB | Jackrong SFT fine-tune of Qwen3.6-27B (Trace Inversion from Claude Opus 4.6/4.7); reasoning-enhanced. 2.6 GB headroom measured. YaRN config in this GGUF unverified — back off context if outputs degrade past ~128K. |
| Qwopus3.6-27B-v2 **MTP** | Q5_K_M | 19.5 GB | 336K | 29.3 GB | Same fine-tune with MTP heads; tuned `n-max 4 / p-min 0.0` measures **147 tok/s vs 68.5 baseline (2.14×)**. Same `-np 1` / no-`mmproj` MTP constraints. 2.5 GB headroom (352K lands at 2,132 MiB — the known sustained-load crash zone). 93.00% on v4 (260/291). |
| Qwen3.6-27B **NVFP4** MTP | NVFP4 | 21.6 GB | 262K | 30.0 GB | **Blackwell-only** (native FP4 tensor cores, sm_120+; needs a GGML_TYPE_NVFP4 build). Tuned `n-max 4` measures ~115 tok/s decode; 95.7 v4 Score. Slower single-stream than its Q5 K-quant sibling — NVFP4's win is batched `-np 8` serving (see leaderboard notes). |
| Qwen3.6-35B-A3B **NVFP4** MTP (MoE) | NVFP4 | 24.3 GB | 262K | 29.5 GB | Same Blackwell-only constraint. Tuned `n-max 2` measures ~317 tok/s decode; 96.3 v4 Score. Same story vs its Q4_K_M sibling: K-quant wins single-stream, NVFP4 wins batched worker-fleet serving. |

All eleven rows have their contexts and VRAM measured against the 2GB OS-headroom rule on a 32GB card (the four MTP/Qwopus rows measured 2026-07-07, the two NVFP4 rows 2026-07-10; VRAM column shows total GPU usage at max context, which includes ~1.3 GB of desktop baseline). See [`REFERENCE.md`](REFERENCE.md) for per-variant details and [`RESEARCH_FINDINGS.md`](RESEARCH_FINDINGS.md) §3 for the v4 MTP benchmark results.

## Fable-Fusion 711 (DavidAU) — added + profiled 2026-07-17

A community fine-tune family: DavidAU's [Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP](https://huggingface.co/DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-NEO-MAX-MTP-GGUF) — Qwen3.6-27B (dense, reasoning ON) with Heretic uncensoring and NEO imatrix quants (output tensor kept full 16-bit). All four variants **measured on the RTX 5090** (q4_0 KV, greedy decode, 2026-07-17):

| Model | Quant | Context | VRAM used / free | Decode (greedy) | Serve script |
|-------|-------|---------|------------------|-----------------|--------------|
| Fable-Fusion 27B | Q5_K_M | **262K** (native) | 28.3 GB / 4.35 GB | ~66 tok/s | `serve-fable-fusion-27b-q5.sh` |
| Fable-Fusion 27B **MTP** | Q5_K_M | **262K** (native) | 29.9 GB / 2.72 GB | **~108 tok/s** (n2, 65% acc) | `serve-fable-fusion-27b-mtp-q5.sh` |
| Fable-Fusion 27B | Q6_K | **240K** | 30.1 GB / 2.46 GB | ~57 tok/s | `serve-fable-fusion-27b-q6.sh` |
| Fable-Fusion 27B **MTP** | Q6_K | **176K** | 30.1 GB / 2.49 GB | **~103 tok/s** (n2, 67% acc) | `serve-fable-fusion-27b-mtp-q6.sh` |

MTP is a **1.6–1.8× lossless speedup** here; the sweet spot is **`--spec-draft-n-max 2`** for both MTP builds (this fine-tune's draft head accepts shallow drafts, not deep — profiled with `scripts/profile-fable-fusion-mtp.sh {q5,q6}`). Q6_K also loads at the full 262K but only ~2.1 GB free (crash-zone edge), so it ships one notch down at 240K; Q6_K MTP is the tightest (weights + draft buffers) at 176K. **DavidAU's MTP rules** (in the script headers): keep **temperature ≤ 1.0** and **repetition_penalty = 1.0**, or switch to the non-MTP quant if acceptance drops below 50%. These "MAX" tunes over-reason, so the serve scripts default to `--reasoning-budget 4096` (override via `REASONING_BUDGET`). Recommended samplers (client-side): thinking `temp 1.0 / top_p 0.95 / top_k 20`, coding `temp 0.6`. Not yet on the eval leaderboard.

## Heretic v2 (llmfan46) — added + profiled 2026-07-17

[llmfan46/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved](https://huggingface.co/llmfan46/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-GGUF) — Qwen3.6-27B (dense, reasoning ON), uncensored via Heretic v1.3.0 + MPOA (94% fewer refusals). Two MTP variants, both **Native-MTP-Preserved**: the 15 original Qwen3.6 MTP heads are kept intact (KL 0.0021 vs base, not retrained). **Measured on the RTX 5090** (q4_0 KV, greedy, 2026-07-17):

| Model | Quant | Context | VRAM used / free | Decode (greedy) | Serve script |
|-------|-------|---------|------------------|-----------------|--------------|
| Heretic v2 27B **MTP** | Q5_K_M | **262K** (native) | 29.6 GB / 2.97 GB | **~136 tok/s** (n8, 39% acc) | `serve-heretic-v2-27b-mtp-q5.sh` |
| Heretic v2 27B **MTP** | Q6_K | **208K** | 30.4 GB / 2.25 GB | **~139 tok/s** (n4, 60% acc) | `serve-heretic-v2-27b-mtp-q6.sh` |

**These are the fastest MTP builds in the lineup** — 136–139 tok/s vs the NEO models' 103–108 — because preserving the native draft heads gives much better acceptance at depth. The optimum draft depth differs by quant (Q5 a flat plateau topping at **n8**, Q6 a sharp peak at **n4** — profiled with `scripts/profile-heretic-v2-mtp.sh {q5,q6}`); the native-MTP hypothesis held (base unsloth 27B MTP also peaked at n8, unlike DavidAU's NEO head at n2). Same MTP rules (temp ≤ 1.0, rep_pen 1.0). Not yet on the eval leaderboard.

## Where model weights live

Weights are large (10s of GB each), so OpenBeast never requires you to store
them inside the repo. Every launch script resolves a weights directory through
`scripts/lib/weights.sh`, checking these in order (first match wins):

1. **`$OPENBEAST_WEIGHTS_DIR`**, environment variable, highest priority. Best
   for a one-off: `OPENBEAST_WEIGHTS_DIR=/mnt/nvme/gguf ./start.sh`.
2. **`WEIGHTS_DIR=` in `openbeast.conf`**, a repo-root config file for a
   persistent choice. Copy the template and edit it:
   ```bash
   cp openbeast.conf.example openbeast.conf
   # WEIGHTS_DIR=/mnt/nas/ai/weights   (NVMe, USB, NAS mount, ~ , or relative)
   ```
   `openbeast.conf` is gitignored, so your personal path is never committed.
3. **`./weights/`**, an in-repo folder, used automatically if it exists
   (this is what the Quick Start creates, and what long-time setups already use).
4. **`../weights/`**, the default for a fresh clone with no `./weights`: a
   sibling folder right next to the `openbeast` checkout.

Paths accept `~` and may be relative (resolved against the repo root). If the
resolved directory doesn't exist, the launch scripts print exactly how to point
OpenBeast at your weights instead of failing with a cryptic "model not found".

Every shipped weight is **sha256-pinned** in `scripts/weights.registry`;
`scripts/verify-weights.sh` checks a download against its pin (`--deep` for a
full hash). See [`INSTALL.md`](INSTALL.md) for per-model download commands.
