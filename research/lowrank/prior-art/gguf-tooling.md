# GGUF Tooling Recon — llama.cpp @ 0ef6e55ed (fresh master)

Recon for the low-rank research pipeline: (a) read + dequantize GGUF tensors in numpy, (b) requantize to aggressive low-bit types, (c) write new GGUF files with arbitrary computed tensors (per-layer low-rank A/B factors). All paths absolute; verified against the live tree on 2026-08-03.

Tree: `/home/max/Documents/openbeast/llama.cpp` (gguf-py at `gguf-py/gguf/`).
Lab rat verified: `/home/max/Documents/openbeast/weights/Qwen3-0.6B-Q8_0.gguf` (see §5).

---

## 1. gguf-py capabilities

### 1.1 Reading — `GGUFReader`

`gguf-py/gguf/gguf_reader.py`:

- `GGUFReader` class: line 111. Constructor mmaps the file, builds `self.fields: OrderedDict[str, ReaderField]` (line 160) and `self.tensors: list[ReaderTensor]` (line 161).
- `ReaderField` (line 39) — KV metadata entry; `field.contents()` (line 57) decodes strings/arrays/scalars cleanly. Use this, not raw `parts`/`data` indexing.
- `ReaderTensor` (line 100) — NamedTuple with `name`, `tensor_type` (GGMLQuantizationType), `shape`, `n_elements`, `n_bytes`, `data` (mmapped uint8 view of the raw quantized bytes — zero-copy).
- `get_field(key)` line 190, `get_tensor(idx)` line 194.

Important: `ReaderTensor.shape` is in GGUF `ne[]` order — **fastest-varying (input/columns) dimension first**, i.e. reversed vs. torch. `blk.0.attn_q.weight` reads as `[1024, 2048]` = (n_embd_in, n_head*head_dim_out); the torch tensor was `(2048, 1024)`. `dequantize()` returns row-major numpy of shape `(shape[-1], ..., shape[0])` reversed back — for 2D you get `(out, in)` = torch convention (verified §5: ffn_down `[3072, 1024]` dequantized to `(1024, 3072)`... note: dequant returns `t.shape[::-1]`).

### 1.2 Dequantize / quantize — `gguf.quants`

`gguf-py/gguf/quants.py`. Public API:

- `dequantize(data: np.ndarray, qtype) -> np.ndarray` — line 68. F32/F16 handled inline; everything else dispatched to `_type_traits`; raises `NotImplementedError` otherwise (line 76).
- `quantize(data: np.ndarray, qtype) -> np.ndarray` — line 57; raises `NotImplementedError` at line 65 for unimplemented types.
- `quant_shape_to_byte_shape` / `quant_shape_from_byte_shape` — lines 14/21 (needed when calling `GGUFWriter.add_tensor(..., raw_dtype=...)` with pre-quantized bytes).

**Pure-numpy DEQUANTIZE support (class : line):**

| Type | Line | Type | Line |
|---|---|---|---|
| BF16 | 205 | TQ1_0 / TQ2_0 | 576 / 624 |
| Q4_0 / Q4_1 | 221 / 255 | MXFP4 / NVFP4 | 657 / 708 |
| Q5_0 / Q5_1 | 292 / 334 | IQ2_XXS | 767 |
| **Q8_0** | 379 | **IQ2_XS** | 832 |
| **Q2_K** | 405 | **IQ2_S** | 902 |
| Q3_K | 432 | IQ3_XXS / IQ3_S | 1006 / 1058 |
| **Q4_K** | 476 | IQ1_S / IQ1_M | 1129 / 1292 |
| Q5_K | 526 | IQ4_NL / IQ4_XS | 1331 / 1352 |
| **Q6_K** | 553 | F16/F32 | inline in `dequantize()` |

Everything we care about dequantizes: **Q8_0, Q6_K, Q4_K, Q2_K, and the full IQ2 family (IQ2_XXS/XS/S)**. Note "IQ2_M" is NOT a tensor type — it's a file type (ftype) whose tensors are stored as `GGML_TYPE_IQ2_S` (+ some IQ3_S/Q2_K mixing; see §3). So an IQ2_M file dequantizes fine tensor-by-tensor.

**Pure-numpy QUANTIZE support** (only classes that implement `quantize_blocks`): F16, F32, **BF16, Q4_0, Q4_1, Q5_0, Q5_1, Q8_0, TQ1_0, TQ2_0, MXFP4**.

**GAPS (cannot quantize in Python):** all K-quants (Q2_K…Q6_K), all IQ types, NVFP4. Verified empirically: `quantize(x, Q2_K)` and `quantize(x, IQ2_XS)` both raise `NotImplementedError`. Consequence for the pipeline: **any Q2_K/IQ2 requant must go through the C++ `llama-quantize` tool** (§3). Python-side we can write F32/F16/BF16/Q8_0 tensors directly, which is fine for A/B factors (keep them F32 or F16 — llama.cpp's adapter path expects small dense tensors anyway).

`QuantError` (line 51) is raised by `__Quant.quantize` (line 191) when the trailing dim isn't a multiple of the block size (e.g. 256 for K-quants, 32 for Q8_0). Rank dims of A/B don't need block alignment if stored F16/F32.

## 2. Writing GGUF files

### 2.1 `GGUFWriter` — `gguf-py/gguf/gguf_writer.py`

Core calls:
- ctor `GGUFWriter(path, arch_str)` — sets `general.architecture` automatically.
- `add_tensor(name, np_array, raw_shape=None, raw_dtype=None)` — line 375. Plain float/int numpy is typed automatically (F16/F32/F64/I8/I16/I32/I64 only — line 342-356); to store pre-quantized bytes pass `raw_dtype=GGMLQuantizationType.Q8_0` etc. and the byte-shaped uint8 array (shape fixup at line 359 via `quant_shape_from_byte_shape`).
- `add_tensor_info` — line 330 (streaming variant, pair with `write_tensor_data` line 407).
- Serialize (in this order): `write_header_to_file()` line 214 → `write_kv_data_to_file()` line 237 → `write_tensors_to_file(progress=True)` line 438 → `close()`.
- Generic KV: `add_key_value(key, val, vtype, sub_type)` line 277.
- Notable typed setters: `add_type` 496, `add_architecture` 499, `add_quantization_version` 502, `add_file_type` 511, `add_name` 550, `add_context_length` 682, `add_embedding_length` 685, `add_block_count` 709, `add_feed_forward_length` 721, `add_head_count` 757 (~300 `add_*` helpers total).

### 2.2 Metadata for a SERVABLE model file

Ground truth = full KV dump of the working Qwen3-0.6B file (§5). A loadable qwen3 model carries exactly:

```
general.architecture='qwen3'  general.type='model'  general.name  general.quantization_version=2  general.file_type
qwen3.block_count  qwen3.context_length  qwen3.embedding_length  qwen3.feed_forward_length
qwen3.attention.head_count  qwen3.attention.head_count_kv  qwen3.attention.key_length  qwen3.attention.value_length
qwen3.rope.freq_base  qwen3.attention.layer_norm_rms_epsilon
tokenizer.ggml.model='gpt2'  tokenizer.ggml.pre='qwen2'  tokenizer.ggml.tokens  tokenizer.ggml.token_type
tokenizer.ggml.merges  tokenizer.ggml.bos/eos/padding_token_id  tokenizer.ggml.add_bos_token  tokenizer.chat_template
```

Practical rule: don't hand-assemble this. Open the source model with `GGUFReader`, copy every KV field through, and swap only the tensors you changed (recipe R4). The tokenizer arrays are the painful part and copy-through sidesteps them.

### 2.3 Metadata for a LoRA ADAPTER file + `convert_lora_to_gguf.py` conventions

Adapter KV (from `convert_lora_to_gguf.py` and `gguf-py/gguf/constants.py:310-314`):

- `general.type = "adapter"` — via `gguf_writer.add_type(gguf.GGUFType.ADAPTER)`; `convert_lora_to_gguf.py:424`
- `adapter.type = "lora"` — `Keys.Adapter.TYPE`, `convert_lora_to_gguf.py:425`
- `adapter.lora.alpha` (float32) — `Keys.Adapter.LORA_ALPHA`, `convert_lora_to_gguf.py:428`; read by C++ at `src/llama-adapter.cpp:218`. Runtime scale applied is `alpha / rank` (rank inferred from tensor shapes); if alpha KV is absent the adapter is used with scale from `--lora-scaled` only.
- plus `general.architecture` of the BASE model (the converter subclasses the base model's converter class, so arch + name mapping match the base).
- Optional: `adapter.lora.task_name`, `adapter.lora.prompt_prefix`, `adapter.alora.invocation_tokens` (constants.py:312-314).

**Tensor naming**: base llama.cpp tensor name + `.lora_a` / `.lora_b` suffixes — emitted at `convert_lora_to_gguf.py:525-527` (`yield (dest_name + ".lora_a", lora_a)`); parsed back by C++ at `src/llama-adapter.cpp:273-283` (`str_endswith(name, ".lora_a")`). Names use llama.cpp convention (`blk.N.attn_q.weight.lora_a`), NOT HF names — the converter maps HF→llama.cpp via the base model class's `modify_tensors`.

**Shape/transposition convention** (the load-bearing detail), enforced at `src/llama-adapter.cpp:355-368`:

- For a base tensor W with GGUF dims `ne = [n_in, n_out]` (torch `(n_out, n_in)`):
  - `lora_a` must have GGUF `ne = [n_in, rank]` → **store numpy/torch A as `(rank, n_in)`** — i.e. standard PEFT `lora_A.weight` layout, unchanged.
  - `lora_b` must have GGUF `ne = [rank, n_out]` → **store numpy/torch B as `(n_out, rank)`** — standard PEFT `lora_B.weight` layout, unchanged.
  - Checks: `model_tensor->ne[0] == a->ne[0]`, `model_tensor->ne[1] == b->ne[1]`, `a->ne[1] == b->ne[0]` (else "lora_a tensor is not transposed").
  - So ΔW = B·A (torch), scaled by alpha/rank. PEFT tensors pass through untransposed.
- **Exception — `token_embd.weight`**: A and B are flipped and A is transposed (`lora_a = lora_a.T` at `convert_lora_to_gguf.py:523`; C++ check at llama-adapter.cpp:357 expects B non-transposed, "A and B flipped; see llm_build_inp_embd()").
- `lm_head`/`output.weight` adapters on tied-embedding models (Qwen3-0.6B is tied — no `output.weight` tensor, see §5): rejected with an error (`convert_lora_to_gguf.py:507-509`, issue #9065). Don't emit low-rank factors for output on this model.
- Norm vectors (1D) may ride along in the adapter unpaired (mergekit-extract-lora style) — `convert_lora_to_gguf.py:512-515`.
- Tensor dtype: converter defaults to F16 for A/B (F32 also loads fine).

`--lora` merging into base weights at load-time for GPU inference is handled by llama.cpp itself; alternatively `llama-export-lora` (tools/export-lora) bakes an adapter into a standalone GGUF.

## 3. `llama-quantize` — low-bit requants

`tools/quantize/quantize.cpp`. Usage (line 123):
`llama-quantize [--allow-requantize] [--imatrix file] [--leave-output-tensor] [--pure] [--output-tensor-type t] [--token-embedding-type t] [--tensor-type name=t] [--dry-run] model-in.gguf [model-out.gguf] TYPE [nthreads]`

- Requanting from an already-quantized input (our Q8_0 lab rat) requires `--allow-requantize` (line 127). Q8_0 source is nearly lossless, so quality cost is negligible vs F16 source.
- Type names: `Q2_K`, `Q2_K_S`, `IQ2_XXS`, `IQ2_XS` (2.31 bpw, line 43), `IQ2_S`, `IQ2_M` (2.7 bpw, line 45), `IQ1_S/M`…
- **imatrix requirement** — `src/llama-quant.cpp:780` `tensor_requires_imatrix()`: types **IQ3_XXS, IQ2_XXS, IQ2_XS, IQ2_S, IQ1_M, IQ1_S REQUIRE an imatrix** (hard error without one); token_embd/output.weight are exempt (line 781). **Q2_K requires an imatrix ONLY for the Q2_K_S ftype** (line 793-796: "k-type quantizations don't require imatrix data"); plain `Q2_K` works without, but quality improves with one — provide it anyway.
- IQ2_M / IQ2_S / IQ2_XS are ftype *mixes*: e.g. under IQ2_M, ffn_down of early layers and attn_v get bumped to IQ3_S (llama-quant.cpp:500-521). `--pure` disables mixing.
- imatrix provenance is recorded in the output (`quantize.imatrix.file/dataset/entries_count/chunks_count`, quantize.cpp:77-80).
- Build note: only `llama-server` exists in `/home/max/Documents/openbeast/llama.cpp/build/bin`. Build the tools first (recipe R5).

### imatrix generation — `llama-imatrix`

`tools/imatrix/` (README.md + imatrix.cpp). `-m model.gguf -f calib.txt` mandatory; output defaults to `imatrix.gguf` (GGUF format since PR #9400). Key flags: `-ngl 99` (GPU offload), `--chunks N` (max chunks; default -1 = all), `--no-ppl`, `--parse-special`, `--in-file` (merge runs), `--show-statistics`, `-o out.gguf`.

- Calibration text: any plain-text file; convention is wikitext-2 `wiki.train.raw` or a general-purpose calibration mix (e.g. bartowski/calibration_datav3 style). ~50-300K tokens (≈100-600 chunks at the default 512-token chunk) is the community norm for good IQ2 quality.
- Prefer generating the imatrix on the highest-precision GGUF you have (F16/BF16; Q8_0 acceptable — it's what we have for the 0.6B).
- Cost = pure prefill over the calibration tokens, single pass, no grad. Rough single-GPU (Blackwell-class) estimates for ~250K tokens: **0.6B → ~1-3 minutes** (prefill tens of K tok/s); **27B dense → ~15-45 minutes** at Q8/F16-ish prefill of a few K tok/s (VRAM permitting; partial offload pushes toward the hour+). Reduce with `--chunks 200` for a first pass.

## 4. `llama-perplexity`

`tools/perplexity/README.md`:

- Convention: **Wikitext-2 test set**, fetched by `scripts/get-wikitext-2.sh` (downloads `https://huggingface.co/datasets/ggml-org/ci/resolve/main/wikitext-2-raw-v1.zip`, extracts `wikitext-2-raw/wiki.test.raw`).
- Invocation: `llama-perplexity -m model.gguf -f wikitext-2-raw/wiki.test.raw -ngl 99` (defaults otherwise; README: "all command line arguments... left at their defaults"). Output = mean PPL ± uncertainty per ~512-token chunk windows.
- For quant-vs-base comparison the stronger metric is KL divergence: run once on the reference model with `--kl-divergence-base logits.kld` to record logits (large: 10s of GiB for big vocab), then on the quant with `--kl-divergence --kl-divergence-base logits.kld`.
- **`--lora` IS supported**: `common/arg.cpp:2864-2873` registers `--lora FNAME` (and `--lora-scaled FNAME:SCALE`, line 2874) with `LLAMA_EXAMPLE_COMMON` scope → available to every common-args tool including llama-perplexity. So "base minus rank-r correction" PPL is directly measurable: `llama-perplexity -m base.gguf --lora adapter.gguf -f wiki.test.raw`.

## 5. Lab rat verified — Qwen3-0.6B-Q8_0.gguf

Opened with `gguf-py` from this tree (sys.path insert of `/home/max/Documents/openbeast/llama.cpp/gguf-py`). Results:

- **arch `qwen3`**, general.name "Qwen3 0.6B Instruct", file_type 7 (Q8_0), quantization_version 2.
- hparams: 28 blocks, n_embd 1024, n_ff 3072, heads 16 / kv-heads 8, head dim (key_length) 128, ctx 40960, rope base 1e6, rms_eps 1e-6.
- **310 tensors**. Representative (GGUF ne-order shapes, i.e. [in, out]):

| tensor | shape | type |
|---|---|---|
| token_embd.weight | [1024, 151936] | Q8_0 |
| output.weight | **ABSENT — tied embeddings** | — |
| output_norm.weight | [1024] | F32 |
| blk.0.attn_q.weight | [1024, 2048] | Q8_0 |
| blk.0.attn_k.weight | [1024, 1024] | Q8_0 |
| blk.0.attn_v.weight | [1024, 1024] | Q8_0 |
| blk.0.attn_output.weight | [2048, 1024] | Q8_0 |
| blk.0.ffn_gate.weight | [1024, 3072] | Q8_0 |
| blk.0.ffn_up.weight | [1024, 3072] | Q8_0 |
| blk.0.ffn_down.weight | [3072, 1024] | Q8_0 |
| blk.0.attn_norm.weight | [1024] | F32 |
| blk.0.attn_q_norm.weight / attn_k_norm.weight | [128] | F32 |
| blk.13.attn_q.weight | [1024, 2048] | Q8_0 |
| blk.27.ffn_down.weight | [3072, 1024] | Q8_0 |

(Note attn_q out-dim 2048 = 16 heads × 128 head_dim ≠ n_embd 1024 — Qwen3 uses head_dim 128 with q/k per-head RMSNorm.)

- **Q8_0 dequant confirmed**: `blk.0.ffn_down.weight` → numpy `(1024, 3072)` float32, std 0.026241, mean 3.4e-6. (dequant returns reversed = torch-order shape.)
- Round-trip sanity: quantize→dequantize Q8_0 on random N(0,1) gives max abs err 0.0124 (≈ 1/127 scale, as expected).
- Confirmed `NotImplementedError` for Python-side Q2_K and IQ2_XS quantization.

---

## Recipes

R1-R3 were executed live against the lab rat on 2026-08-03: R3 copy-through reproduced all 310 tensors byte-identical with intact KV; R2 produced an adapter whose GGUF ne-shapes (`lora_a ne=[1024, 8]`, `lora_b ne=[8, 3072]` for ffn_up rank-8) satisfy every check at `src/llama-adapter.cpp:355-368`.

### R1 — Read + dequantize any tensor to numpy float32

```python
import sys
sys.path.insert(0, "/home/max/Documents/openbeast/llama.cpp/gguf-py")
from gguf import GGUFReader
from gguf.quants import dequantize

r = GGUFReader("/home/max/Documents/openbeast/weights/Qwen3-0.6B-Q8_0.gguf")
t = next(t for t in r.tensors if t.name == "blk.0.ffn_down.weight")
W = dequantize(t.data, t.tensor_type)   # float32, torch-order shape (out_in reversed from t.shape)
print(t.name, list(t.shape), "->", W.shape, W.dtype)
arch = r.get_field("general.architecture").contents()   # 'qwen3'
```

### R2 — Write a LoRA-style adapter GGUF with computed A/B factors

```python
import sys; sys.path.insert(0, "/home/max/Documents/openbeast/llama.cpp/gguf-py")
import numpy as np, gguf

w = gguf.GGUFWriter("qwen3-lowrank-adapter.gguf", "qwen3")   # arch must match base
w.add_type(gguf.GGUFType.ADAPTER)                             # general.type = "adapter"
w.add_string(gguf.Keys.Adapter.TYPE, "lora")                  # adapter.type
w.add_float32(gguf.Keys.Adapter.LORA_ALPHA, float(rank))      # alpha=rank -> effective scale 1.0

for base_name, (A, B) in factors.items():   # e.g. "blk.0.ffn_down.weight"
    # W (torch) is (n_out, n_in); A: (rank, n_in); B: (n_out, rank); dW = B @ A * alpha/rank
    w.add_tensor(base_name + ".lora_a", A.astype(np.float32))
    w.add_tensor(base_name + ".lora_b", B.astype(np.float32))
    # EXCEPTION: for token_embd.weight store A.T (flip convention, llama-adapter.cpp:357)

w.write_header_to_file(); w.write_kv_data_to_file(); w.write_tensors_to_file(progress=True); w.close()
```
Do NOT emit factors for `output.weight` on tied-embedding models (absent tensor → error path). Use at runtime: `--lora qwen3-lowrank-adapter.gguf` or `--lora-scaled f.gguf:0.5` on any llama tool/server.

### R3 — Write arbitrary tensors into a NEW full model GGUF (copy-through)

```python
import sys; sys.path.insert(0, "/home/max/Documents/openbeast/llama.cpp/gguf-py")
import gguf
from gguf import GGUFReader, GGUFWriter, GGUFValueType
from gguf.quants import dequantize, quantize

src = GGUFReader(SRC); arch = src.get_field("general.architecture").contents()
out = GGUFWriter(DST, arch)
for key, f in src.fields.items():
    if key.startswith("GGUF.") or key == "general.architecture": continue   # writer emits these
    sub = f.types[-1] if f.types[0] == GGUFValueType.ARRAY else None
    out.add_key_value(key, f.contents(), f.types[0], sub_type=sub)
for t in src.tensors:
    if t.name in replacements:                        # our computed tensor, numpy (out,in) float32
        out.add_tensor(t.name, quantize(replacements[t.name], gguf.GGMLQuantizationType.Q8_0),
                       raw_dtype=gguf.GGMLQuantizationType.Q8_0)   # or plain F16/F32: add_tensor(name, x)
    else:
        out.add_tensor(t.name, t.data, raw_dtype=t.tensor_type)  # zero-copy passthrough; do NOT pass raw_shape —
        # ReaderTensor.data already has the right numpy shape (byte-shape for quant types, gguf_reader.py:356-359),
        # and add_tensor_info reconstructs element shape from it (gguf_writer.py:358-359)
out.write_header_to_file(); out.write_kv_data_to_file(); out.write_tensors_to_file(progress=True); out.close()
```
Python can only (re)quantize to Q8_0/Q4_0/Q5_0/BF16/etc. — K/IQ targets must go through R5.

### R4 — Fetch wikitext-2 + perplexity (with and without adapter)

```bash
cd /home/max/Documents/openbeast/llama.cpp
sh scripts/get-wikitext-2.sh          # -> wikitext-2-raw/wiki.test.raw (and prints usage)
./build/bin/llama-perplexity -m /home/max/Documents/openbeast/weights/Qwen3-0.6B-Q8_0.gguf \
    -f wikitext-2-raw/wiki.test.raw -ngl 99
# base + low-rank correction:
./build/bin/llama-perplexity -m base.gguf --lora adapter.gguf -f wikitext-2-raw/wiki.test.raw -ngl 99
```

### R5 — imatrix + aggressive requant (Q2_K, IQ2_XS, IQ2_M)

```bash
cd /home/max/Documents/openbeast/llama.cpp
# tools are NOT built yet in this tree (only llama-server):
cmake --build build --target llama-quantize llama-imatrix llama-perplexity -j

# calibration text: reuse wikitext train, or any ~100-300K-token general text file
curl -L -o wikitext-2-raw-v1.zip https://huggingface.co/datasets/ggml-org/ci/resolve/main/wikitext-2-raw-v1.zip
unzip -o wikitext-2-raw-v1.zip     # wikitext-2-raw/wiki.train.raw

# imatrix (single pass prefill; ~minutes for 0.6B, ~15-45 min for 27B on one GPU)
./build/bin/llama-imatrix -m /home/max/Documents/openbeast/weights/Qwen3-0.6B-Q8_0.gguf \
    -f wikitext-2-raw/wiki.train.raw -o imatrix-qwen3-0.6b.gguf -ngl 99 --chunks 300

# requants from the Q8_0 source (--allow-requantize is mandatory for quantized input)
Q=./build/bin/llama-quantize; M=/home/max/Documents/openbeast/weights/Qwen3-0.6B-Q8_0.gguf; IM=imatrix-qwen3-0.6b.gguf
$Q --allow-requantize --imatrix $IM $M qwen3-0.6b-Q2_K.gguf   Q2_K    8   # imatrix optional for Q2_K, use it anyway
$Q --allow-requantize --imatrix $IM $M qwen3-0.6b-IQ2_XS.gguf IQ2_XS  8   # imatrix REQUIRED
$Q --allow-requantize --imatrix $IM $M qwen3-0.6b-IQ2_M.gguf  IQ2_M   8   # imatrix REQUIRED (tensors = IQ2_S mix)
# add --pure to disable per-tensor type mixing if you need uniform tensor types for analysis
```

## Gaps / gotchas summary

1. Python cannot produce K-quant or IQ tensors — C++ `llama-quantize` is the only requant path for Q2_K/IQ2 targets.
2. IQ2_XS/IQ2_S/IQ2_M/IQ2_XXS/IQ1_*/IQ3_XXS hard-require an imatrix (`src/llama-quant.cpp:780-799`); plain Q2_K does not.
3. IQ2_M is an ftype mix (mostly IQ2_S tensors, some IQ3_S/Q2_K); use `--pure` for uniformity.
4. GGUF shapes are ne-order (reversed vs torch); `dequantize` hands back torch-order.
5. Qwen3-0.6B has tied embeddings — no `output.weight`; never target lm_head/output with adapter factors.
6. `.lora_a`/`.lora_b` store PEFT-layout A `(rank, n_in)` / B `(n_out, rank)` untransposed, except token_embd (A transposed, A/B flipped).
7. Adapter scale at runtime = `adapter.lora.alpha / rank`; set alpha = rank for scale 1.0.
8. Build the tool binaries first — this tree only has `llama-server` built.
