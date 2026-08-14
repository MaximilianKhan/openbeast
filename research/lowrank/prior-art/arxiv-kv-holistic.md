# KV cache + total-VRAM budget — prior art and our architecture's actual math

Sweep date: 2026-08-04. Question: our weight compression takes the 27B from
23.6 GB to ~15 GB served at 8k — but the end-goal story ("frontier model in
consumer VRAM") needs the whole budget: weights + KV + compute buffers, with
KV growing in context. Is KV compression a beast-rank front?

**Verdict up front: NO front. Our architecture already ate the KV problem —
16 of 64 layers carry KV (4× structural), GQA 24:4 (6× vs MHA), and q4_0
cache (3.6× vs f16) compose to ~85× vs a dense-MHA-f16 counterfactual.
Measured: full native 262k context costs 4.6 GiB of KV. Weights remain the
binding constraint at every context length we can serve. Two cheap moves
worth taking, one paper-grade idea worth one experiment — see the end.**

---

## (b) first, because it grounds everything: OUR architecture's KV math (measured)

### Architecture ground truth (GGUF metadata, heretic-v2 27B, `qwen35` arch)

```
block_count 65 (64 + 1 nextn/MTP)     full_attention_interval 4
  → 16 full-attention layers, 48 linear-attention (GatedDeltaNet-class)
full-attn: n_head 24, n_kv_heads 4 (GQA 6:1), head_dim (K=V) 256
  partial RoPE: rope.dimension_count 64 of 256 (M-RoPE sections [11,11,10,0])
  → 64 roped dims + 192 NoPE dims per head   (matters for (d))
  per-head q/k RMS-norms (attn_q_norm/attn_k_norm, one 256-dim gamma
  shared across heads) + sigmoid output gate  (matters for (d))
linear layers: ssm inner 6144, state 128, groups 16, conv 4
```

### Per-token KV cost (16 layers × 2(K,V) × 4 heads × 256 = 32,768 elems/token)

| cache type | bytes/token | 8k | 32k | 128k | 212992 (serve ceiling) | 262144 (native max) |
|---|---|---|---|---|---|---|
| f16 | 64 KiB | 512 MiB | 2.0 GiB | **8.0 GiB** | 13.0 GiB | 16.0 GiB |
| q8_0 | 34 KiB | 272 MiB | 1.06 GiB | 4.25 GiB | 6.9 GiB | 8.5 GiB |
| q8_0 K + q4_0 V | 26 KiB | 208 MiB | 832 MiB | 3.25 GiB | 5.3 GiB | 6.5 GiB |
| q4_0 (current default) | 18 KiB | 144 MiB | 576 MiB | 2.25 GiB | 3.7 GiB | 4.5 GiB |

### Measured on the 5090 (llama-cli b10254, MIXED 27B, -fa on, 2026-08-04)

llama.cpp's own allocator lines, single sequence — they match the analytic
table to the MiB:

| ctx | -ctk/-ctv | KV buffer | compute buffer | recurrent (RS) buffer |
|---|---|---|---|---|
| 8192 | q4_0 | 144.00 MiB (K 72 + V 72) | 130.28 MiB | 149.62 MiB |
| 32768 | q4_0 | 576.00 MiB | 240.28 MiB | 149.62 MiB |
| 131072 | q4_0 | 2,304.00 MiB | 720.28 MiB | 149.62 MiB |
| 131072 | q8_0 | 4,352.00 MiB | 720.28 MiB | 149.62 MiB |
| 131072 | f16 | 8,192.00 MiB | 250.02 MiB | 149.62 MiB |
| 262144 | q4_0 | 4,608.00 MiB | 1,360.28 MiB | 149.62 MiB |
| 262144 | q8_0 | 8,704.00 MiB | 1,360.28 MiB | 149.62 MiB |

- **Recurrent state is constant and tiny**: 149.62 MiB f32 at ANY context
  (48 layers × [conv 3×(6144+2·16·128) + state 128×6144] × 4 B). The 48
  linear layers' "KV" never grows. Confirmed in source:
  `llama.cpp/src/llama-hparams.cpp` `n_embd_r()/n_embd_s()`; hybrid split in
  `llama-memory-hybrid` (KV allocated for the 16 attn layers only — the
  allocator line says "16 layers" explicitly).
- **Compute buffer scales ~linearly**: ≈ 90 MiB + 4.9 MiB per 1k ctx with
  quantized KV (dequant scratch; the f16 path is smaller). 1.36 GiB at 262k
  — second-order, never binding.
- MTP draft adds one more attention layer's KV (1/16 extra: 288 MiB at 262k
  q4_0) plus its own compute buffer — the reason the Q6 serve ceiling is
  212992 rather than 262144.

### Is KV binding for us? No.

Counterfactual: a dense 64-layer version of this model (same GQA) would need
4× — 9 GiB q4_0 / 32 GiB f16 at 128k; Qwen3-32B (64L, 8 kv-heads × 128)
needs 32 GiB f16 at 128k — the whole 5090 for cache alone. Ours needs
**2.25 GiB q4_0 at 128k**. The hybrid architecture IS the KV compression;
everything else is a rounding correction.

---

## (a) Low-rank / latent KV methods — and why they lose here

Full method roster with numbers (agents' sweep, arXiv 2024-2026):

| method | ratio | quality | arXiv | llama.cpp |
|---|---|---|---|---|
| Palu (SVD of KV projections, latent cached) | 50% KV; 11.4× w/ quant | ~maintained PPL (Llama-2-7B 5.62) | 2407.21118 | no |
| ReCalKV / STAR-KV / OjaKV / DynaCalKV (2025-26 Palu successors) | ≥Palu at equal ratio | — | 2505.24357 etc. | no |
| MLA (DeepSeek V2/V3): 576-elem latent vs 32,768 MHA | ~57×/layer, "93.3% reduction" | ≥ MHA in ablations | 2405.04434 | **yes, native-MLA archs only** (PR #12801 latent cache, #13306 CUDA FA for 576/512 heads) |
| TransMLA (GQA→MLA conversion) | 93% KV cut | parity after **~6B tokens** fine-tune | 2502.07864 | no (unless exported as DeepSeek-arch GGUF) |
| MHA2MLA | −92% KV | −0.5% LongBench, 0.3-0.6% pretrain data | 2502.14837 | no |
| X-EcoMLA (distilled upcycling) | 6.4× | 100% LM-Harness kept, 3.4B tokens | 2503.11132 | no |
| xKV (cross-layer SVD) | up to 6.8× over inter-layer SOTA; +3× even on MLA | +2.7% RULER | 2503.18893 | no |
| EigenAttention / LoRC / MatryoshkaKV | 40-75% | degrade below ~60% budget / need distillation | 2408.05646, 2410.03111, 2410.14731 | no |

**The decisive negative result:** "Quantization Dominates Rank Reduction for
KV-Cache Compression" (arXiv 2604.11501) — at EQUAL bytes, INT4 beats rank
reduction by 4-364 PPL; rank-32 collapses LAMBADA to 0.4% where INT4 costs
+0.23 PPL; per-direction projection damage ≈ 3·2^(2b) × quantization damage.
Low-rank KV is a losing trade at the byte counts we care about, before even
counting that llama.cpp has **zero** low-rank/latent path for non-MLA models
(nothing merged, nothing in progress; only architecture-native MLA).

MLA-conversion (TransMLA/X-EcoMLA class) is an *architecture* decision
requiring billions of fine-tune tokens and a non-servable output format for
our arch. And our 16 KV layers are exactly the layers carrying all
long-range recall — vLLM's hybrid-model FP8-KV guidance (Apr 2026) points
the same way: treat the few full-attention layers *conservatively*, don't
squeeze them hardest.

### K/V quant ablations vs what we run

- llama.cpp measured deltas (JohannesGaessler, discussions #5932, #20969,
  #23470): q8_0 KV ≈ +0.002-0.05 PPL (noise); q4_0 ≈ +0.2-0.25 PPL; **K is
  markedly more sensitive than V** — q8_0-K + q4_0-V ≈ +0.4% relative PPL,
  "more precise than q6_K weights."
- Literature is unanimous on the asymmetry (KIVI 2402.02750: K per-channel
  outliers, V per-token tolerant; KVQuant 2401.18079: pre-RoPE per-channel K
  worth 0.82 PPL at 3-bit).
- Caveat from field reports: q4_0 KV long-context *retrieval* degrades much
  faster than short-context PPL suggests (one report −36.8% at ~110k), and
  q4_0 KV dequant can cost decode speed at very long context. Our KV is so
  small that q8_0-K is affordable everywhere — see verdict.
- Hybrid-ratio science: 3:1 linear:full (ours) is inside the measured sweet
  spot (arXiv 2507.06457, 72-model sweep: recall collapses only above ~6:1);
  Kimi Linear (2510.26692) and MiniMax-01 (2501.08313) push 3:1-7:1 with the
  same conclusion.

---

## (c) Total-VRAM planning: where a corrected 27B lands on the 5090

Published budget decompositions all use weights + KV + activations
(PagedAttention 2309.06180: 65/30/5 on A100-13B; LLM-Viewer 2402.16363;
eLLM 2506.15155: KV 2.5-5× weights at datacenter batch sizes; vLLM profiles
activation peak empirically). The datacenter picture (KV-dominated) does
NOT transfer to us: at batch 1 on a hybrid arch, weights dominate at every
context. No academic "32 GB 5090 long-context" budget exists — a genuine
gap our paper can fill with measured numbers.

Our measured budget model (single stream, MiB):
`total ≈ weights_device + KV(ctx, type) + 150 (RS) + 90 + 4.9·ctx/1k
(compute) + ~600-900 (CUDA context) [+ MTP: +KV/16 + draft compute]`

5090 = 32,116 MiB device, ~600 MiB desktop. Landing table at **full native
262,144 context**, q8_0-K + q4_0-V cache (6.5 GiB), no MTP:

| weights | device GiB | total @262k | headroom | note |
|---|---|---|---|---|
| MIXED + fc (12.5 GB file) | 10.9 | ~20.1 GiB | **~11 GiB** | our corrected build |
| IQ3_XS (12.26 GB) | 10.7 | ~19.9 GiB | ~11 GiB | 27B crown |
| Q3_K_M (13.5 GB) | 11.8 | ~21.0 GiB | ~10 GiB | |
| Q6_K reference (22.4 GB) | 20.5 | ~29.7 GiB | ~1.5 GiB | why the serve ceiling is 212992 w/ MTP |

Story numbers: the compression campaign freed ~9.6 GiB of weights
(Q6→MIXED); max-native-context cache costs 4.5-6.5 GiB. **The weight
savings fund the entire long-context budget twice over** — a corrected 27B
serves 262k with ~11 GiB to spare (room for a second parallel 262k stream
via `--kv-unified`/multi-seq, or a 0.6B router + embedder + headroom).
On the 24 GB cards (4090/3090) the same corrected build + 262k q4_0 cache
totals ~18.5 GiB — the frontier-in-consumer-VRAM claim extends down a tier.

---

## (d) Could our full-covariance Gram instrumentation drive better KV quant?

The "imatrix-for-KV" concept exists and works — but nothing shaped like it
ships in any stock engine:

| method | statistics | bits | fusable into projections? |
|---|---|---|---|
| KVQuant (2401.18079) | Fisher-weighted NUQ, per-channel pre-RoPE K | 3-bit <0.1 PPL | no (NUQ tables, RoPE-on-dequant) |
| QuaRot (2404.00456) | Hadamard | KV4 ≤0.47 PPL | V-side yes / K-side no (post-RoPE) |
| SpinQuant (2405.16406) | **learned** Cayley rotations on calibration | 4-bit | R2 (v_proj↔o_proj) **fully offline** — the key precedent |
| OSCAR (2605.17757, Together) | **attention-aware Gram matrices**: C_K=QᵀQ, C_S=VᵀSᵀSV → eigenrotations + clipping | 2.28-bit, −0.02 pts Qwen3-32B @32k | V yes (absorbed), K no (Triton kernel) |
| RotateKV (2501.16383) | calibrated channel reorder + grouped FWHT | 2-bit | partial, not stock |
| Block-GTQ (2606.24033) | per-RoPE-frequency-block energy → bit allocation | 2-3 bit K | no (custom format) |
| SVDq / KVLinC / CQ / AQUA-KV | K-covariance eigenbasis / correction adapters / VQ codebooks / inter-layer predictors | 1.25-2.5 bit | no |
| llama.cpp today | **none** — imatrix is weights-only; RTN block formats for KV; issue #21385 (calibrated per-head KV) closed unplanned; TurboQuant (#20969) unmerged | — | — |

**Arch-specific fusability analysis (ours, from the qwen35 graph —
`llama.cpp/src/models/qwen35.cpp`):** the literature's fusion recipes
assume vanilla attention; our graph has (i) per-head q/k RMS-norms between
projection and RoPE, and (ii) a sigmoid **output gate** elementwise-applied
between attention output and o_proj. Consequences:

- **Rotations (SpinQuant-R2/OSCAR-style) do NOT fuse here** — on the V
  side the elementwise gate breaks R⁻¹-into-wo (g ⊙ Rx ≠ R(g ⊙ x)); on the
  K side the post-projection RMS-norm gamma breaks R-into-wk.
- **Diagonals DO fuse, at exactly the right places**: elementwise ops
  commute with diagonal scaling.
  - **K equalization**: fold per-channel D into the `attn_k_norm` gamma and
    D⁻¹ into `attn_q_norm` gamma (both are per-head-dim vectors already in
    the GGUF — a tensor edit, zero kernel changes). Constraint: D must be
    constant within each RoPE 2-dim pair on the 64 roped dims; **free
    per-channel on the 192 NoPE dims** (75% of the head). Attention scores
    exactly preserved; cache stores variance-flattened K.
  - **V equalization**: scale wv rows by D_v, fold D_v⁻¹ into wo columns
    (through the GQA head mapping); the sigmoid gate commutes with D_v.
    Full per-channel granularity.
- Our Gram diagonals for k_proj/v_proj *outputs* are precisely the
  statistics needed to set D (KVQuant showed per-channel K outliers are the
  dominant 4-bit error source; q4_0's 32-element blocks span channels, so
  flattening within-block variance is the mechanism). This
  "**KV-imatrix as a pure GGUF tensor edit**" is unpublished as a package —
  every component is validated separately in the literature (SpinQuant
  fusion, KVQuant channel analysis, Block-GTQ pair-energy allocation), and
  it is deployable on unmodified llama.cpp. One experiment: measure KLD at
  -ctk q4_0 with/without equalization at 32k+; if q4_0-K+equalization ≈
  q8_0-K, it saves 2 GiB at 262k and is a tidy paper section. If not, flip
  K to q8_0 and move on.

---

## Verdict

1. **KV compression is NOT a beast-rank front.** The architecture already
   did it (16/64 layers, GQA, constant 150 MiB linear state); measured KV
   at full native 262k is 4.5 GiB q4_0 — weights are the binding constraint
   at every context, and stay so after our compression.
2. **Do today (config, 5 minutes):** serve with `-ctk q8_0 -ctv q4_0`
   (asymmetric is supported with FA). Costs +2 GiB only at extreme context,
   nothing at 8-32k; removes the documented q4_0-K long-context-retrieval
   risk; literature and llama.cpp ablations unanimously say K is the
   fragile side. Bump serve ceilings accordingly (the corrected builds can
   take `-c 262144` outright).
3. **Maybe (one experiment, paper-grade if it works):** Gram-diagonal K/V
   equalization fused into attn_k_norm/attn_q_norm gammas and wv/wo — the
   only calibration-driven KV mechanism that runs on stock llama.cpp.
4. **Don't:** Palu/xKV-style low-rank KV (loses to quant at equal bytes —
   2604.11501 — and has no runtime), MLA conversion (billions of tokens,
   non-servable format, solves a problem we don't have).

Sources: arXiv 2407.21118 (Palu), 2405.04434 (MLA), 2502.07864 (TransMLA),
2502.14837 (MHA2MLA), 2503.11132 (X-EcoMLA), 2503.18893 (xKV), 2604.11501
(quant-dominates-rank), 2401.18079 (KVQuant), 2402.02750 (KIVI),
2404.00456 (QuaRot), 2405.16406 (SpinQuant), 2605.17757 (OSCAR),
2501.16383 (RotateKV), 2606.24033 (Block-GTQ), 2507.06457 (hybrid-ratio
sweep), 2510.26692 (Kimi Linear), 2309.06180 (PagedAttention), 2402.16363
(LLM-Viewer), 2506.15155 (eLLM); llama.cpp PRs #12801, #13306, #16095,
#26185; discussions #5932, #20969, #23470; issue #21385. Local
measurements: llama-cli b10254 allocator logs, 2026-08-04 (this file's
tables); GGUF metadata via gguf-py; graph analysis from
src/models/qwen35.cpp and src/llama-hparams.cpp.
