# E36-L — a non-Qwen model: Llama-3.1-8B base (prepared 2026-10-15, NOT RUN)

Referee request: every model in the paper is Qwen-family. This is the
outside-the-family replication: **Llama-3.1-8B base** (NousResearch mirror,
the standard quantization-paper testbed). It goes through the 27B protocol
(E27) unchanged: single-step quants from BF16, Gram+imatrix capture on BF16,
the stock ladder, the MIXED base, a same-byte CONTROL designed by a rule
fixed in advance, the fc r128 Q8_0 adapter, the two re-round arms of E36-B,
and scoring on the three E36 corpora.

Everything here was done on CPU while the E36 campaign held the GPU lease.
Nothing on this page has been measured on the GPU yet.

## Files

| file | what |
|---|---|
| `PROVENANCE.md` | HF revision, converter versions, GGUF sha256 |
| `pipeline.sh` | the GPU run, resume-safe, refuses unless under our lease |
| `bytes.py` | exact tensor bytes from a `llama-quantize --dry-run` log |
| `control_plan.py` | the CONTROL rule as code → `--tensor-type` args + predicted bytes |
| `adapter_bytes.py` | exact adapter file size (writes a zero adapter with the extractor's writer calls) |
| `synth_imatrix.py`, `audit_tools.py` | CPU test helpers (synthetic imatrix / Grams; never measurement artifacts) |
| `plan-logs/` | dry-run logs, control plan, audit output, the CPU tool runs, convert log |

Weights: `weights/research-staging/llama31-8b/` (BF16 GGUF + `hf/*.json`;
the pipeline writes `llama8b-*.gguf` there). Logits go to
`research/lowrank/data/e36-llama8b-bf16ref-{wiki,fw,code}-100.logits`, Grams to
`data/e36-llama8b-gram-{wiki,mixed}/` (each with its `diag.imatrix.gguf`).

## Tool compatibility (llama tensor names) — verdict: no patch needed

Checked by reading the code and by running every tool on CPU against the
real 8B BF16 with synthetic Grams/imatrix for layers 0–1 (`plan-logs/`).

1. **Gram capture patch** (`llama.cpp/tools/imatrix/imatrix.cpp`,
   `gram_wanted`): it hooks the MUL_MATs whose weight name contains
   `attn_k.`, `attn_output.`, `ffn_up.`, `ffn_down.` (plus qwen-only
   `attn_qkv.`, `ssm_out.`, `*_exps.`). It writes `<weight name>.gram.bin`
   (f32, `/`→`_`) plus `grams.json {name: {n, blocks, count}}`; inputs wider
   than 8192 (`LLAMA_IMATRIX_GRAM_MAXDIM`) are stored block-diagonal ×8.
   On llama: 4 Grams per layer, 128 in all; attn_k/attn_output/ffn_up are
   full 4096², ffn_down (n_in 14336) is 8 blocks of 1792². ~9.7 GB per
   capture. The llama graph (`src/models/llama.cpp` → `build_qkv`, no
   fused `wqkv` in this GGUF) runs separate `wq/wk/wv` MUL_MATs on the
   same `attn_norm` output, and `ffn_gate/ffn_up` on the same `ffn_norm`
   output, so one attn_k Gram is exactly the input covariance of q, k and
   v, and one ffn_up Gram that of gate and up. Only `blk.*` weights are
   collected (no `--process-output`), so `output.weight` and
   `token_embd.weight` (a GET_ROWS, not a MUL_MAT) have neither Gram nor
   imatrix entries.
2. **Extractor** (`04-served-v0/extract_adapter.py` via
   `27-bf16-rederivation/e27_extract.py`): `kind_of` maps attn_q/k/v,
   attn_output (→`attn_o`), ffn_gate/up/down correctly; `--rank-map
   ffn_gate=0,ffn_up=0,ffn_down=0` leaves exactly attn_q/k/v/output × 32 =
   **128 targets**; `token_embd`/`output` drop out at the imatrix gate.
   `_GRAM_SOURCE` maps q,v→attn_k and gate→ffn_up, the other kinds to
   their own file; every target in the Gram layers got a whitener (`fc`
   mode), with full 4096 Cholesky factors. A full CPU run (61 s) wrote a
   128-tensor adapter of **exactly the predicted 115,885,248 bytes**.
   Ranks: attn_k/attn_v are 1024×4096, so r=128 is under min(n_out, n_in)
   and Q8_0's 32-divisibility holds for A (r×4096) and B (n_out×128).
3. **Re-rounder** (`13-rerounder/e13_reround.py`): its `_GRAM_SOURCE` is
   identical to the extractor's. In the llama Q2_K file the Q2_K-coded
   tensors are attn_q, attn_k, ffn_gate, ffn_up (and `token_embd`, which
   has no Gram and passes through untouched); attn_v is q4_K,
   attn_output and ffn_down q3_K (not touched, as at 27B). A CPU run on
   the 2 Gram layers re-rounded exactly the 8 expected tensors; every KV
   and every other tensor is byte-identical, and in the changed tensors
   the scale/d/dmin bytes are untouched (only the 2-bit codes changed).
   With the synthetic S, tr(E S Eᵀ) fell by 24% on attn_q, attn_k and
   ffn_gate. Cost: ~29 s per tensor → ~1 h per arm for 128 tensors.
4. **q/k row permutation.** `convert_hf_to_gguf.py` permutes the *rows*
   (output dim) of q_proj and k_proj for llama (`LlamaModel.permute`,
   conversion/llama.py:189–194). It makes no difference to any tool here:
   - Grams and the imatrix are statistics of the layer *input* (columns),
     which the permutation does not touch.
   - Re-rounding: GPTQ sweeps columns with every row independent, both
     files are in GGUF order, and Q2_K blocks run along a row, so a row
     permutation commutes with the whole procedure.
   - Adapters: R = W_bf16 − W_quant is formed in GGUF space and B's rows
     come out in the same permuted order llama.cpp applies them in
     (`build_lora_mm` on `wq/wk`). The one thing that would break is
     loading this adapter into HF/PEFT without un-permuting B's rows,
     which nobody here does.
5. **Latent hazard carried, not patched.** e27_extract's `--cache-dir` and
   e13's `--codes-dir` are keyed by tensor name only (E27 README "TOOL
   HAZARD"). `pipeline.sh` handles it in the caller: each cache gets a STAMP
   of its inputs (path:size:mtime) and is wiped on mismatch. The tools are
   unchanged. If you want it fixed in the tools, the diff is e13b's
   fingerprint block, as queued in TODO.md.

## Byte plan (CPU; exact)

Method: `llama-quantize --dry-run` per type, then `bytes.py` recomputes each
tensor exactly from the ggml block size. Checked against real CPU
quantizations, which matched to the byte: Q2_K and Q3_K_S without an
imatrix, and Q3_K_S, IQ3_XXS, MIXED and CONTROL with a synthetic imatrix.
Header = 7,835,808 B for every type (mostly tokenizer KVs). An imatrix adds
`quantize.imatrix.*` KVs: 224 B with the synthetic file, a few hundred B
with the real paths. The test GGUFs were then deleted.

**Imatrix dependence.** Tensor bytes do not depend on the imatrix's
*values*. For one type they do depend on its *presence*: without an
imatrix, IQ3_XXS's plan puts ffn_down at q3_K/q4_K (3,386,351,872 B); with
one, at iq3_xxs (3,267,076,352 B). The dry-run warns that IQ3_XXS/IQ3_XS/
IQ2_M need an imatrix, and real quantization refuses without one. Every
figure below therefore comes from a dry-run *with* an imatrix, matching
how the measured files will be built. For the other six types the two
plans are identical.

| config | tensor bytes | file bytes (+imatrix KVs ≈0.3 KB) | bpw | ×BF16 |
|---|---|---|---|---|
| BF16 reference | 16,061,055,232 | 16,068,891,040 | 16.0 | 1.00× |
| IQ2_M | 2,940,444,928 | 2,948,280,736 | 2.937 | 5.45× |
| Q2_K | 3,171,295,488 | 3,179,131,296 | 3.167 | 5.05× |
| IQ3_XXS | 3,267,076,352 | 3,274,912,160 | 3.263 | 4.91× |
| IQ3_XS | 3,510,911,232 | 3,518,747,040 | 3.505 | 4.57× |
| MIXED bare | 3,552,977,152 | 3,560,812,960 | 3.547 | 4.51× |
| Q3_K_S | 3,656,663,296 | 3,664,499,104 | 3.651 | 4.39× |
| **CONTROL** | 3,668,361,472 | 3,676,197,280 | 3.662 | 4.37× |
| **MIXED + fc r128 Q8_0** | 3,552,977,152 + adapter 115,885,248 | **3,676,698,208** | 3.663 | 4.37× |
| Q3_K_M | 4,011,081,984 | 4,018,917,792 | 4.004 | 4.00× |
| Q4_K_M | 4,912,898,304 | 4,920,734,112 | 4.902 | 3.27× |

bpw = file bytes × 8 / 8,030,261,312 params.

**Types per kind** (`plan-logs/dryim-*.log`). Q2_K: attn_q/attn_k/ffn_gate/
ffn_up/token_embd q2_K, attn_v q4_K, attn_output/ffn_down q3_K, output
q6_K. MIXED = Q2_K with `ffn_gate/ffn_up/ffn_down=q3_k`, as at 27B; the
Q2_K defaults already promote attn_v→q4_K and attn_output→q3_K. Q3_K_S:
everything q3_K, output q6_K.

**Adapter bytes.** The extractor stores A (r × n_in) and B (n_out × r) as
Q8_0 = 34 B per 32 elements. Calibrated on the 27B
`mixed-fc-r128q8-bf16.gguf`: 316,669,952 elements → 336,461,824 tensor
bytes, exactly ×34/32, plus a 28,736 B header for 416 tensors. Llama-8B,
per layer at r=128: q 128·(4096+4096), k 128·(4096+1024),
v 128·(4096+1024), o 128·(4096+4096) = 3,407,872 elements; ×32 layers =
109,051,904 → 115,867,648 tensor bytes, plus a 17,600 B header (256
tensors) = **115,885,248 B**. That figure was measured twice: once with a
zero adapter from the extractor's own writer calls, once from the real
extractor run on CPU.

**Corrected configuration** = MIXED file + adapter file = **3,676,698,208 B**
(27B convention: base file + adapter file). It is only 12.2 MB (+0.33%)
above Q3_K_S. At 27B the gap was +1.1%. MIXED is smaller here because the
llama Q2_K keeps token_embd at q2_K (Q3_K_S has it at q3_K, 53 MB) and
attn_q/attn_k at q2_K.

## CONTROL — rule fixed before measurement, and its recipe

Rule (fixed before any 8B measurement): start from Q3_K_S and promote kinds
to q4_k in the order attn_k, attn_v, attn_output, attn_q, until total bytes
reach the corrected configuration's bytes from below. A kind is promoted
whole while it fits. The first kind that does not fit whole is promoted
layer by layer at evenly spaced layers, the same as the rule's own attn_q
clause, and the fill stops there. The spacing is floor((i+0.5)·32/k),
i < k. Both sides carry identical quantize headers (same imatrix KVs), so
the fill is computed on tensor bytes.

**What the arithmetic forced.** The budget above Q3_K_S is 12,199,104 B.
Promoting all of attn_k costs 17,825,792 B (32 × 557,056), so the fill ends
inside the first kind. attn_v, attn_output and attn_q are never reached.

**Recipe:** `Q3_K_S` + attn_k → q4_k at 21 of 32 layers
{0, 2, 3, 5, 6, 8, 9, 11, 12, 14, 16, 17, 19, 20, 22, 23, 25, 26, 28, 29, 31}
(`--tensor-type 'blk\.N\.attn_k=q4_k'` for each; `pipeline.sh` carries the
list).
Predicted 3,668,361,472 tensor bytes vs the target 3,668,862,400:
**−500,928 B (−0.0137%)**. Confirmed by a real CPU quantization:
3,676,197,504 B file = prediction + header + 224 B of synthetic-imatrix
KVs. The regex does not catch blk.1x/2x/3x (the per-layer type list in
`plan-logs/` shows q4_K at exactly those 21 layers).

**Interpretation hazard (state it in the paper).** At 8B the same-byte
control is Q3_K_S plus 11.7 MB. C2 (CONTROL vs MIXEDfc) is therefore close
to a re-test of C1 (MIXEDfc vs Q3_K_S). At 27B the control spent 131 MB
over Q3_K_S across four kinds. This is a property of the 8B byte geometry,
not a design choice.

## Pipeline (`pipeline.sh`)

Run: `scripts/gpu-lease.sh run "E36-L llama8b" -- bash research/lowrank/experiments/36-submission/llama8b/pipeline.sh`.
It refuses outside our lease (exit 2, tested), re-checks the BF16 sha256,
and refuses with < 95 GB free on a fresh run. Every step is resume-safe.

1. BF16 reference logits on wiki/fw/code (100 × 512, `-ngl 99`), via
   lib.sh `gen_ref`.
2. Gram + imatrix capture (research build, 48 chunks × 512, `-ngl 99`) on
   wiki.train → `e36-llama8b-gram-wiki`, and on `mixed-calib2.txt` →
   `e36-llama8b-gram-mixed`. Each asserts 128 Gram files.
3. Nine quantizations from BF16 with the wiki imatrix (no MTP pin; llama has
   none): Q2_K, Q3_K_S, Q3_K_M, IQ3_XXS, IQ3_XS, IQ2_M, Q4_K_M, MIXED,
   CONTROL. Each file size is checked against the plan.
4. fc extraction (r128, Q8_0, non-FFN, wiki Grams, BF16 as reference), in
   an exit-3 resume loop, size-checked against 115,885,248 B.
5. Background CPU lane: re-round the Q2_K base with the wiki Grams, then
   with the mixed Grams. Code caches are stamped, then deleted after
   assembly.
6. Scoring with lib.sh `score` on all three corpora: Q2_K, IQ2_M, IQ3_XXS,
   IQ3_XS, MIXEDbare, Q3_K_S, CONTROL, MIXEDfc (`--lora`), Q3_K_M, Q4_K_M,
   then Q2Krr-wiki and Q2Krr-mixed once the lane finishes →
   `results.txt`, `logs/<corpus>-<label>.log`.
7. Paired stats (`../e36_stats.py`) for the PLAN §A/§B pairs per corpus →
   `paired-<corpus>.txt`.

Note: `score` records the base file's bytes for MIXEDfc. Use the table
above (base + adapter) for the byte column.

### Estimated wall time (scaled from the 27B E36-A run on this box)

| step | 27B measured | 8B estimate | where |
|---|---|---|---|
| ref logits ×3 | 3.6–4.5 min each (`-ngl 20`) | ~1–1.5 min each, ~4 min total | GPU |
| Gram+imatrix capture ×2 | ~35 min each (E27 log) | ~10–12 min each, ~25 min total (Gram FLOPs/token ≈ 0.3× the 27B) | GPU fwd + CPU Gram |
| 9 quantizations | — | ~3–5 min total | CPU |
| fc extraction | — | ~1–2 min (61 s measured on CPU) | CPU |
| scoring, 10 configs × 3 corpora | ~21 s each | ~12–15 s each, ~7 min total | GPU |
| re-round arms ×2 | — | ~1 h each, sequential (29 s/tensor measured) | CPU, overlaps scoring |
| re-round scoring, 2 × 3 | | ~1.5 min | GPU |

**GPU busy ≈ 40 min. Wall under the lease ≈ 2.7 h**, dominated by the two
CPU re-round sweeps, which hold the lease while they run. To free the card
sooner, run step 5 outside the lease and score the two arms later. Note
that the Gram patch's accumulator uses up to 32 threads
(`std::thread::hardware_concurrency`, capped at 32), not the 12 the rest of
the pipeline uses.

### Disk

Peak ≈ 85 GB from scratch: logits 3 × ~6.5 GB, Grams 2 × ~9.7 GB, nine
quants 32.8 GB, two re-round files 6.4 GB, a 4.4 GB code cache per arm
(transient), fc cache 0.4 GB. The safetensors were deleted to make room
(PROVENANCE.md).
