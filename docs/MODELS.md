# Models

OpenBeast ships **25 pre-configured models**, every one VRAM/context-measured on
the RTX 5090 reference card. Swap any of them in with one argument to
`start.sh` (e.g. `./start.sh serve-qwen-27b-q5.sh`) or set your `beastup`
default via `SERVE_SCRIPT` in `openbeast.conf`. Capability rankings (where a
model has been benchmarked) are in the [eval leaderboard](RESULTS.md) and
[`evals/README.md`](../evals/README.md).

The default is **Qwen3.8 27B Uncensored MTP Q5_K_M** at the full native 262K
context (changed 2026-08-19 from Heretic v2 27B MTP Q5_K_M, which it beats on
speed *and* headroom — see the Qwen3.8-27B-Uncensored section below); the dense
**Qwen3.6-27B Q5_K_XL** tops the capability board, and the **35B-A3B MoE**
variants trade a little accuracy for 30–50% more speed per token.

## Core lineup (v4-benchmarked / v3.5-legacy)

| Model | Quant | Weights | Context | VRAM (measured) | Notes |
|-------|-------|---------|---------|-----------------|-------|
| **Qwen3.6-27B** | **Q5_K_XL** | **19 GB** | **350K** | **~29.5 GB** | **Top accuracy**: 97.85% on v3.5, **96.62% on v4** (271/291, landed 2026-07-09) — a statistical tie with its MTP twin (see leaderboard). Slower per-token than the MoEs. |
| Qwen3.6-27B Uncensored | Q5_K_P | 21 GB | 350K | ~30.0 GB | Uncensored fine-tune (HauhauCS Aggressive); 96.16% on v3.5 (benchmarked at 380K) |
| Qwen3.6-35B-A3B (MoE) | Q4_K_M | 20 GB | 512K | 27.8 GB | Fast MoE (3B active); 93.74% on v3.5; ~4.3 GB headroom (measured) |
| Qwen3.6-35B-A3B Uncensored | Q4_K_M | 20 GB | 512K | 27.1 GB | Fastest of the lineup but trails on accuracy (90.33% on v3.5) |
| Gemma 4 31B-it | Q5_K_XL | 20 GB | 192K | ~28.5 GB | Different family; KV cost rises with context (20→25 KB/token); reduced from 220K on 2026-05-08 after a sustained-load crash at the tight 2,080 MiB headroom |
| Qwen3.6-27B **MTP** | Q5_K_XL | 20.4 GB | 288K | 29.4 GB | MTP draft heads baked in; tuned `n-max 8 / p-min 0.0` measures **184 tok/s vs 66.8 baseline (2.75×)**. Forces `-np 1` (no parallel slots); the old "no `--mmproj`" claim is unverified — see the retest item in TODO.md. 2.5 GB headroom at the tuned config. **95.63% on v4** (273/291) — a statistical tie with the non-MTP Qwen 27B (96.62%) at **2.75× the token throughput**; lossless speedup, exactly as MTP promises. |
| Qwen3.6-35B-A3B **MTP** (MoE) | Q4_K_M | 22.7 GB | 512K | 28.8 GB | Same as above for the MoE; tuned `n-max 4 / p-min 0.0` measures **379 tok/s vs 259 baseline (1.46×)**. Same `-np 1` constraint; matches the non-MTP MoE's 512K ceiling (3.1 GB headroom). 93.76% on v4 (254/291). |
| Qwopus3.6-27B-v2 | Q5_K_M | 19.2 GB | 416K | 29.3 GB | Jackrong SFT fine-tune of Qwen3.6-27B (Trace Inversion from Claude Opus 4.6/4.7); reasoning-enhanced. 2.6 GB headroom measured. YaRN config in this GGUF unverified — back off context if outputs degrade past ~128K. |
| Qwopus3.6-27B-v2 **MTP** | Q5_K_M | 19.5 GB | 336K | 29.3 GB | Same fine-tune with MTP heads; tuned `n-max 4 / p-min 0.0` measures **147 tok/s vs 68.5 baseline (2.14×)**. Same `-np 1` MTP constraint (the old no-`mmproj` claim is unverified — see TODO.md). 2.5 GB headroom (352K lands at 2,132 MiB — the known sustained-load crash zone). 93.00% on v4 (260/291). |
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

## Qwen3.8-27B (Qwen / unsloth) — added + profiled 2026-08-14 ⚠️ NOT YET BENCHMARKED

[Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) ·
[unsloth/Qwen3.8-27B-GGUF](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF) —
Qwen's newest 27B. The GGUF declares `general.architecture = qwen35`: a hybrid
Gated-DeltaNet stack of 64 trunk layers laid out as 16 × (3 × DeltaNet → 1 ×
full attention), i.e. `full_attention_interval = 4`. Only **16 of the 64 layers
carry a real KV cache**; the other 48 hold a constant-size recurrent state.
Native context is **262,144** (Qwen documents YaRN extension to 1M — we do not
enable it).

> **Correction (2026-08-14, same day).** This section originally called that
> "a new architecture, not a Qwen3.6 respin," and credited it for Qwen3.8
> holding more context than our older models. **That was wrong.** Qwen3.6-27B
> reports the *same* `qwen35` architecture with the *same* 64 trunk layers,
> `full_attention_interval = 4`, 16 full-attention layers, and identical
> `head_count_kv=4 / key_length=256 / value_length=256`. Both cost **~18
> KB/token** at `q4_0` — the "~28 KB/token" figure previously quoted for
> Qwen3.6 was carried over from older docs and is not what the GGUF describes.
> Qwen3.8 is an architecturally identical, newly-trained model. Every
> *measurement* below stands (they were taken empirically); only the
> architectural-novelty explanation was wrong. The one genuine structural
> difference is the MTP head — see below.

**Requires a llama.cpp with `LLM_ARCH_QWEN35`.** Ours already has it (build
2026-08-04, `b10066-188-g0ef6e55ed`) — **no upgrade and no vLLM needed**.

**Measured on the RTX 5090** (q4_0 KV, greedy 300-token decode, 2026-08-14):

| Model | Quant | Context | VRAM used / free | Decode (greedy) | Serve script |
|-------|-------|---------|------------------|-----------------|--------------|
| Qwen3.8 27B | UD-Q5_K_XL | **262K** (native) | 26.1 GB / **5.77 GB** | 67.6 tok/s | `serve-qwen38-27b-q5.sh` |
| Qwen3.8 27B **MTP** | UD-Q5_K_XL | **262K** (native) | 28.2 GB / 3.67 GB | **123.7 tok/s** (n4, 48% acc) | `serve-qwen38-27b-mtp-q5.sh` |
| Qwen3.8 27B | Q6_K | **262K** (native) | 28.2 GB / 3.65 GB | 61.5 tok/s | `serve-qwen38-27b-q6.sh` |
| Qwen3.8 27B **MTP** | Q6_K | 192K | 28.9 GB / 2.90 GB | **113.6 tok/s** (n4, 47% acc) | `serve-qwen38-27b-mtp-q6.sh` |

Three of the four configs hold the model's full native 262K, and the Q5 non-MTP
config leaves **5.8 GB free — the roomiest headroom in the lineup**. Only
Q6+MTP gives anything up: it loads at the full 262K (113.9 tok/s) but at 1,559
MiB free, inside the sustained-load crash zone that cost us Gemma at 220K and
Qwopus at 352K, so it ships one notch down at 192K.

Do **not** read that as an architectural win over the Qwen3.6 lineup — the KV
cost is the same ~18 KB/token on both (see the correction above). The context
differences across our 27B configs come from **file size and draft buffers**,
not architecture: a Q6 weighs ~2.7 GB more than a Q5, and MTP draft buffers
cost another ~0.7-1.7 GB, so each config lands where the 32 GB card allows.
Where Qwen3.6-class scripts run *past* 262,144 (the stock Qwen3.6-27B Q5 ships
`-c 358400`), they are exceeding the GGUF's declared `context_length` via rope
extension — a lever available to Qwen3.8 too, and one we have not exercised
or validated here.

**MTP heads ship inside the standard GGUF** (`qwen35.nextn_predict_layers = 1`,
tensors at `blk.64.nextn.*`; `block_count` is 65 = 64 trunk + 1 nextn). There is
**no separate `-MTP-GGUF` repo to download**, unlike Qwen3.6 — the MTP serve
scripts load the *same file* as their non-MTP twins and differ only in launch
flags. Served without `--spec-type`, those tensors log `unused tensor blk.64.*`
at startup; that is expected and harmless.

**Tuned draft depth** (measured sweep, both quants):

| n-max | Q5 decode | Q5 acceptance | Q6 decode | Q6 acceptance |
|-------|-----------|---------------|-----------|---------------|
| 2 | 116.5 tok/s | 63% | 112.4 tok/s | 67% |
| **4** | **123.7 tok/s** | 48% | **113.6 tok/s** | 47% |
| 6 | 107.7 tok/s | 36% | — | — |
| 8 | 106.6 tok/s | 28% | 111.0 tok/s | 32% |

Both ship **`n-max 4`** — a 1.83× (Q5) / 1.85× (Q6) lossless speedup. Note this
peaks **earlier than Qwen3.6-27B's n8**: Qwen3.8's acceptance falls off faster
with depth, so don't copy the 3.6 config across. The Q6 curve is essentially
flat (111–114 tok/s across the whole grid) — at 6-bit the model is
bandwidth-bound and draft depth barely matters. As with every MTP build, `-np 1`
is forced (no parallel slots) and `p-min 0.0` does not affect output quality.

**Recommended samplers** (client-side, per Qwen's card): thinking `temp 1.0 /
top_p 0.95 / top_k 20 / min_p 0.0 / presence_penalty 0.0`; non-thinking `temp
0.7 / top_p 0.80 / top_k 20 / presence_penalty 1.5`. Thinking is **on by
default** and the model exposes a `reasoning_effort` control (`xhigh` default /
`medium` / `low`) plus `preserve_thinking`. One open item we have **not** resolved:
llama-server reports the chat template supports `--reasoning-preserve`, which
Qwen defaults *on* — our scripts leave it off to match the rest of the lineup,
and it is the first thing to A/B when benchmarking, since it plausibly matters
most on the multi-turn agentic tasks Qwen's own numbers lean on.

### Vision — WORKING, verified 2026-08-14

Qwen3.8 is natively multimodal, but the language GGUF holds **no vision
tower**: it carries multimodal rope (`rope.dimension_sections = [11, 11, 10,
0]`) and an image/video-aware chat template, while the tower itself ships as a
separate `mmproj-F16.gguf` (928 MB, pinned in the registry, downloaded
2026-08-14). Pass it with `--mmproj` alongside the normal weight file.

**How it fits together.** The projector is a 461M-param ViT — 27 blocks,
1152-dim, patch 16, `spatial_merge_size 2` — whose output is mapped into the
LLM's 5120-dim embedding space. The chat template emits
`<|vision_start|><|image_pad|>…<|vision_end|>`; `libmtmd` swaps those pad
placeholders for real projected patch embeddings before the forward pass, so
the LLM attends over one uniform embedding sequence and cannot tell which
vectors came from pixels. It declares `clip.projector_type = qwen3vl_merger`,
which maps to our build's existing `PROJECTOR_TYPE_QWEN3VL` — **no llama.cpp
change needed.** Image cost is one token per 32×32 px (patch 16 with 2×2
merge): a 768×512 image is 384 tokens, 1024×1024 is 1,024 tokens. Verified by
reading a rendered code string and naming three shapes and colours in correct
left-to-right order from a synthetic test image.

| Model | Config | Context | VRAM used / free | Serve script |
|-------|--------|---------|------------------|--------------|
| Qwen3.8 27B **Vision** | Q5 + mmproj | **262K** (native) | 27.1 GB / 4.75 GB | `serve-qwen38-27b-vision-q5.sh` |
| Qwen3.8 27B **Vision + MTP** | Q5 + mmproj, n4 | 224K | 28.4 GB / 3.41 GB | `serve-qwen38-27b-vision-mtp-q5.sh` |

The vision tower costs **~979 MiB**, cheap enough that the full native 262K
survives intact without MTP. Vision **+** MTP together do not fit at 262K
(31,555 MiB / 1,052 MiB free — loads and answers, but deep in the crash zone),
so that config ships at 224K; 192K is available as an extra-margin fallback at
4,388 MiB free.

⚠️ **This overturns a standing constraint.** Every MTP script we ship carries
the note "`--mmproj` is not yet supported with MTP", inherited from a
2026-05-22 upstream limitation. **No such guard exists in the current
llama.cpp source**, and the combination was measured working — correct image
readback with the MTP draft path live at 64.6% acceptance on the same request.
Scope: proven for arch `qwen35` on our build. The Qwen3.6-era MTP scripts still
carry the old claim and have **not** been retested — don't assume it's lifted
for them without measuring.

Untested: **video** input (the template renders `<|video_pad|>` blocks), and
the `mmproj-BF16.gguf` variant.

**Benchmarks are deliberately empty.** All four rows are registered in
`evals/benchmark_all.py` with no leaderboard entry. Run them with
`python evals/benchmark_all.py --models qwen38-27b-q5,qwen38-27b-mtp-q5,qwen38-27b-q6,qwen38-27b-mtp-q6`.
Qwen's card claims SWE-bench Pro 61.7 and OSWorld-Verified 84.3 against Opus 4.6
Max's 53.4 / 72.7 (losing Terminal Bench 2.1, 73.0 vs 78.2) — vendor-reported,
on benchmarks our suite does not run. Treat as unverified until our own sweep
lands.

## Qwen3.8-27B-Uncensored (JonathanColetti) — added + profiled 2026-08-19 ⭐ THE DEFAULT

[JonathanColetti/Qwen3.8-27B-Uncensored-GGUF](https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF)
— Qwen3.8-27B with the refusal direction abliterated out of `self_attn.o_proj`
and `mlp.down_proj`, then imatrix-quantized (496 entries / 200 chunks). Same
`qwen35` hybrid Gated-DeltaNet architecture as the stock Qwen3.8 rows above;
abliteration edits weight *values*, not shapes, so nothing about the KV math
changes. **Measured on the RTX 5090** (q4_0 KV, greedy, seed 42, 2026-08-19):

| Model | Quant | Context | VRAM used / free | Decode (greedy) | Serve script |
|-------|-------|---------|------------------|-----------------|--------------|
| Qwen3.8 27B Uncensored | Q5_K_M | **262K** (native) | 25.6 GB / **6.25 GB** | 69.9 tok/s | `serve-qwen38-27b-uncensored-q5.sh` |
| **Qwen3.8 27B Uncensored MTP** ⭐ | Q5_K_M | **262K** (native) | 27.1 GB / **4.76 GB** | **140.2 tok/s** (n4, 56% acc) | `serve-qwen38-27b-uncensored-mtp-q5.sh` |

**Why this replaced Heretic v2 as the default (2026-08-19).** It is the first
swap that cost nothing on any axis:

| | Heretic v2 MTP Q5 (old) | Qwen3.8 Uncensored MTP Q5 (new) |
|---|---|---|
| Decode | ~136 tok/s | **140.2 tok/s** |
| Context | 262K native | 262K native (tie) |
| VRAM free | 2.97 GB | **4.76 GB** |
| Base model | Qwen3.6-27B | Qwen3.8-27B (newer) |

The headroom is the part that matters most. Heretic v2 shipped at 2.97 GB free,
which sits inside the sustained-load crash zone that forced Gemma 4 31B down
from 220K to 192K at 2,080 MiB (see the core lineup notes above). This config
leaves nearly 5 GB — the roomiest default we have ever shipped.

**MTP tuning.** The head ships *inside* the standard GGUF
(`qwen35.nextn_predict_layers = 1`, tensors at `blk.64.nextn.*`, block_count 65
= 64 trunk + 1 nextn), so the MTP and non-MTP rows are the **same weight file**
and differ only in launch flags. Full sweep at the native 262K
(`scripts/profile-qwen38-uncensored-mtp.sh`, results in
`.run/qwen38-uncensored-mtp-results.txt`):

| `n-max` | Decode | Draft acceptance | Mean accepted len | VRAM |
|---------|--------|------------------|-------------------|------|
| off | 69.9 tok/s | — | — | 25,476 MiB |
| 1 | 111.8 tok/s | 87.9% | 1.88 | 27,282 MiB |
| 2 | 132.4 tok/s | 71.4% | 2.43 | 27,432 MiB |
| **4** | **140.2 tok/s** | **56.1%** | **3.24** | **27,732 MiB** |
| 6 | 128.3 tok/s | 45.0% | 3.70 | 28,030 MiB |
| 8 | 120.9 tok/s | 31.9% | 3.54 | 28,330 MiB |
| 10 | 112.1 tok/s | 25.5% | 3.54 | 28,629 MiB |

**Abliteration moved the acceptance curve up but not sideways.** The peak sits
at **n4**, exactly where stock Qwen3.8-27B peaks — but at 56% acceptance and
140 tok/s versus stock's 48% and 123.7. This was worth measuring rather than
inheriting: the nextn head drafts out of the residual stream that abliteration
edits, so a *degraded* draft path was the live hypothesis going in. It got
better instead. (Do not copy the Qwen3.6-era `n8` config here — that optimum
belongs to a different base model, not to the architecture.)

The upstream repo also ships `-noMTP-` twins with the head stripped
(block_count 64). We deliberately do not use them: the MTP file is a strict
superset — served without `--spec-type` the extra tensors load and are ignored,
which is exactly what `serve-qwen38-27b-uncensored-q5.sh` does. Both files are
pinned in `scripts/weights.registry`, and both sha256 values were cross-checked
against the published HF LFS oids and match byte-for-byte.

Standard MTP constraints apply: `-np 1` is forced (concurrent requests
serialize — use the non-MTP script for a multi-user rig), temperature ≤ 1.0,
`repetition_penalty = 1.0`. Acceptance at 56% is comfortably above the ~50%
floor below which a non-MTP quant is the better trade.

⚠️ **Not yet benchmarked.** Both rows are registered in
`evals/benchmark_all.py` with no leaderboard entry. This matters more than
usual: abliteration is the one edit that plausibly costs capability, and this
is now what every fresh install serves. Run it with
`python evals/benchmark_all.py --models qwen38-27b-uncensored-q5,qwen38-27b-uncensored-mtp-q5`.

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
