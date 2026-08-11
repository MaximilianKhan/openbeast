# llama.cpp LoRA internals recon — can it serve y = Q·x + s·B·(A·x) unchanged?

Source: `/home/max/Documents/openbeast/llama.cpp` @ `0ef6e55ed` (master, 2026-08-03). All line numbers against this commit. Everything below was read from the actual source, not recalled.

## 1. The LoRA adapter path end-to-end

### 1.1 GGUF adapter file format

Metadata keys (declared `src/llama-arch.cpp:364-368`, mirrored `gguf-py/gguf/constants.py:309-314`):

| key | type | required | enforced at |
|---|---|---|---|
| `general.type` | str, must be `"adapter"` | yes | `src/llama-adapter.cpp:202-205` |
| `general.architecture` | str, must equal base model arch | yes | `src/llama-adapter.cpp:207-211` |
| `adapter.type` | str, must be `"lora"` | yes | `src/llama-adapter.cpp:213-216` |
| `adapter.lora.alpha` | f32, defaults to 0.0 if absent | no | `src/llama-adapter.cpp:218` |
| `adapter.alora.invocation_tokens` | u32 array (activated-LoRA) | no | `src/llama-adapter.cpp:220-238` |
| `adapter.lora.task_name`, `adapter.lora.prompt_prefix` | str, informational | no | read via generic kv dump |

Tensor naming (`src/llama-adapter.cpp:271-294`): every tensor must be named
`<base-model-tensor-name>.lora_a` or `<base-model-tensor-name>.lora_b`, e.g.
`blk.7.attn_q.weight.lora_a`. A trailing `_norm.weight` is silently skipped (line 287-290);
**any other suffix throws** (line 292). The base name must resolve via
`model.get_tensor(name)` or loading fails with "does not exist in base model" (line 330-333).

Shape contract (`src/llama-adapter.cpp:355-368`), in ggml `ne[]` order (ne[0] = row length):

- normal weight `w` with `w->ne = [n_in, n_out]`:
  - `a->ne = [n_in, rank]` (i.e. A stored "transposed": rows are input-dim)
  - `b->ne = [rank, n_out]`
  - checks: `w->ne[0]==a->ne[0]`, `w->ne[1]==b->ne[1]`, `a->ne[1]==b->ne[0]` (line 362-367)
- `token_embd.weight` is special-cased flipped/non-transposed (line 356-360) because the graph
  uses `get_rows` on A instead of a matmul (`src/llama-graph.cpp:2303-2306`); the converter
  transposes A for token_embd only (`convert_lora_to_gguf.py:520-525`).

Dtypes: **the loader imposes no dtype restriction whatsoever.** Tensors are duplicated as-is
(`ggml_dup_tensor`, `src/llama-adapter.cpp:371-375`) and raw-copied from the file
(line 394-412). `convert_lora_to_gguf.py:287-288` offers `f32 / f16 / bf16 / q8_0`. At runtime
the only constraint is that ggml's `mul_mat` accepts the type as src0 — F32/F16/BF16/Q8_0 all
work on CPU and CUDA. (The offline **merge** tool is stricter: `tools/export-lora/export-lora.cpp:304-306`
rejects quantized A/B — but that tool is irrelevant to the runtime path.)

Rank: no limit anywhere; rank is simply inferred as `b->ne[0]` (`src/llama-adapter.h:54`).
If A/B are stored Q8_0, ggml block size forces row length % 32 == 0, i.e. **rank must be a
multiple of 32 for B** (B's rows have length rank). F16 has no such constraint.

### 1.2 Loading

`llama_adapter_lora_init` -> `llama_adapter_lora_init_impl` (`src/llama-adapter.cpp:149-418`):
parses gguf, pairs `.lora_a`/`.lora_b`, and — the key fact for GPU execution — allocates each
pair **in the same backend buffer type as the base tensor it targets**:
`ggml_backend_buffer_get_type(model_tensor->buffer)` (line 335). With `-ngl 99` the base weights
live in CUDA buffers, so A/B land on the GPU too. The only fallback to CPU is for "extra"
repacked CPU bufts (line 337-350). Adapters are model-scoped objects, registered in
`model.loras` (line 415), and activated per-context with per-adapter scales via
`llama_set_adapters_lora` (`src/llama-context.cpp:1262-1304`, public API at line 3844).
`llama_adapter_loras` is literally `unordered_map<llama_adapter_lora*, float>` (`src/llama-adapter.h:90`)
— multiple simultaneous adapters, each with its own runtime scale, hot-swappable without reload.

### 1.3 Graph build site — the exact math

`llm_graph_context::build_lora_mm` (`src/llama-graph.cpp:1486-1515`):

```cpp
ggml_tensor * res = ggml_mul_mat(ctx0, w, cur);          // base: Q·x  (w may be any quant type)
...
for (const auto & lora : *loras) {
    llama_adapter_lora_weight * lw = lora.first->get_weight(w);   // lookup by tensor NAME (llama-adapter.cpp:138-147)
    if (lw == nullptr) continue;
    const float scale = lw->get_scale(lora.first->alpha, lora.second);
    ggml_tensor * ab_cur = ggml_mul_mat(ctx0, lw->b, ggml_mul_mat(ctx0, lw->a, cur));  // B·(A·x), two GEMMs
    ab_cur = ggml_scale(ctx0, ab_cur, scale);
    res = ggml_add(ctx0, res, ab_cur);
}
```

This is **exactly** `y = Q·x + s·B·(A·x)`. The scale (`src/llama-adapter.h:53-57`):

```cpp
const float rank  = (float) b->ne[0];
const float scale = alpha ? adapter_scale * alpha / rank : adapter_scale;
```

So: if we write `adapter.lora.alpha = 0` into our gguf, the effective multiplier is exactly the
CLI/API `adapter_scale` (default 1.0 from `--lora`, arbitrary from `--lora-scaled f.gguf:S` —
`common/arg.cpp:2865-2887`). We can either bake `s` into B and use alpha=0/scale=1, or expose
`s` as the runtime scale knob — free live tuning of the correction strength per request.

MoE experts get the same treatment via `build_lora_mm_id` (`src/llama-graph.cpp:1517-1553`,
`ggml_mul_mat_id` twice + scale + add). Token embeddings via `build_inp_embd`
(`src/llama-graph.cpp:2293-2309`: `get_rows(a, tokens)` then `mul_mat(b, ·)`, scaled, added).

## 2. CUDA execution

- **Yes, adapter matmuls run on GPU with `-ngl 99`** — A/B are allocated on the base tensor's
  buft (section 1.2), and the graph nodes are plain `GGML_OP_MUL_MAT`, scheduled to the same
  backend as the base weight.
- **Separate GEMMs, not fused.** Per adapted tensor per ubatch: 2 mul_mat + 1 scale + 1 add =
  4 kernels. The CUDA fusion machinery (`ggml/src/ggml-cuda/ggml-cuda.cu:1662` `ggml_cuda_should_fuse_mul_mat`,
  fusion dispatch around lines 2958-2995) covers up/gate GLU mul_mat pairs, rope+set_rows,
  add chains, etc. — there is **no fusion pattern for mul_mat->mul_mat->scale->add**.
- Dispatch for the small A/B GEMMs (`ggml-cuda.cu:1812-1866` `ggml_cuda_mul_mat`): F16/BF16 A/B
  at decode batch sizes go through the `mmvf` vector kernel or cuBLAS; Q8_0 A/B through MMVQ/MMQ.
  FLOPs are negligible (2·r·(d_in+d_out) vs 2·d_in·d_out base).
- **Per-token overhead is kernel launches, not mallocs.** Graph node budget is pre-reserved:
  `llama_adapter_lora::get_n_nodes()` = 6 nodes per adapted tensor (`src/llama-adapter.h:85-87`)
  is added to `graph_max_nodes` (`src/llama-context.cpp:2362-2366`) — no compute-buffer
  reallocation. CUDA graph capture (`ggml-cuda.cu:2549` `ggml_cuda_graph_update_required`)
  includes the extra nodes; changing the active adapter set changes topology and triggers a
  one-time re-capture, then steady-state decode replays the captured graph. Expected cost for a
  full-coverage adapter (~7 weights x n_layers x 4 kernels): community-reported ~10-20% decode
  slowdown at bs=1; prompt processing impact is smaller (GEMMs amortize).
- Server note: requests with **different lora configs are not batched together**
  (`tools/server/README.md:539`) — irrelevant for us since we run one adapter always-on.

## 3. Constraints

**Which tensors can carry adapters:** anything whose matmul is routed through
`build_lora_mm` / `build_lora_mm_id` / `build_inp_embd`. From `src/llama-graph.cpp`: fused
`wqkv` (1605), `wq/wk/wv` (1625/1635/1645), `wo` (2697, 2802-2808), FFN `up/gate/down`
(1706-1847), MoE `gate_inp` (1946) and expert tensors (2092-2213), `token_embd` (2265+), and
the output head (each `src/models/*.cpp` calls `build_lora_mm(model.output, cur)`). Caveat:
a handful of arch-specific matmuls call raw `ggml_mul_mat` and would silently skip an adapter —
verify per target arch (grep the model's file under `src/models/`). Norm weights are ignored
(TODO in `src/llama-adapter.cpp:287-290`).

**Base quant types:** `build_lora_mm` calls `ggml_mul_mat(w, cur)` on the base tensor exactly
as without lora — **all quant types incl. IQ2_XS/IQ2_XXS/Q2_K work**, since the adapter path
never inspects `w->type`. One exception found: NVFP4 FFN tensors that use per-tensor scales
assert if a lora targets them (`src/llama-graph.cpp:1699-1704`). Irrelevant for IQ2/Q2_K bases.

**Multiple adapters:** yes — map with per-adapter scales (section 1.2); server supports
multiple `--lora`, per-request `{"id","scale"}` selection, `GET/POST /lora-adapters`, and
`--lora-init-without-apply` (`common/arg.cpp:3690`, `tools/server/README.md:95-96, 539, 1131-1166`).

**MTP / speculative decoding:** MTP graphs (`LLAMA_CONTEXT_TYPE_MTP` -> `LLM_GRAPH_TYPE_DECODER_MTP`,
`src/llama-context.cpp:29`, `src/llama-ext.h:98`) are built by the same `llm_graph_context`
with the same `loras` pointer (`src/llama-context.cpp:2446`), so adapters on base weights apply
inside MTP graphs too; MTP model files (`src/models/glm4-moe.cpp`, `deepseek2.cpp`, `mimo2.cpp`)
use `build_lora_mm` for their NextN blocks, so even MTP-layer tensors are adapter-capable.
Draft-model speculative decoding: adapters are per-context — target-ctx adapters don't leak to
the draft ctx (correct behavior; load separately if desired).

**Quantized KV cache:** fully orthogonal. The adapter modifies projection outputs before
K/V quantization; no interaction anywhere in `src/llama-kv-cache*`.

**Tied lm_head:** an adapter targeting `lm_head` when the base gguf ties embeddings errors at
convert time (`convert_lora_to_gguf.py:507-508`).

## 4. Conversion side — emitting arbitrary A/B

`convert_lora_to_gguf.py` expects a PEFT directory (`adapter_config.json` +
`adapter_model.safetensors`) with names `base_model.model.<hf_name>.lora_A.weight` /
`.lora_B.weight` (`convert_lora_to_gguf.py:269-276, 457-499`). It routes each pair through the
base arch's `modify_tensors` to get correct llama.cpp names, writes `general.type=adapter`,
`adapter.type=lora`, `adapter.lora.alpha` (lines 422-428), and emits `<name>.lora_a/.lora_b`
(lines 525-526). Nothing checks training provenance.

Two abuse routes, both trivial:

1. **Fake-PEFT**: dump our SVD/optimized factors as `adapter_model.safetensors` with PEFT names
   plus a minimal `adapter_config.json` (`base_model_name_or_path`, `lora_alpha`). The stock
   converter does the name mapping and any arch-specific tensor splitting (wqkv splits, permutes)
   for free. **This is the recommended route** — `modify_tensors` handles per-arch permutation
   of q/k weights (rope interleaving) that a naive script would get wrong. `LoraTorchTensor`
   (lines 40-266) propagates reshape/permute/split onto A/B factors correctly.
2. **Direct gguf-py script** (~50 lines): `GGUFWriter` + `add_type("adapter")` +
   `add_string("adapter.type","lora")` + `add_float32("adapter.lora.alpha", 0.0)` + arch string,
   then `add_tensor("blk.N.<x>.weight.lora_a", A)` with A shaped `(rank, n_in)` row-major
   (=> ggml `ne=[n_in, rank]`) and B `(n_out, rank)`. Only viable if our factors are computed
   directly on the *GGUF-space* tensors (post-permutation), which is actually what we want for
   quantization-residual correction — we'd SVD `W_f16_gguf - dequant(Q_gguf)` per tensor, so
   route 2 avoids the HF name dance entirely. Get base names from the base GGUF's tensor list.

Key trick again: **alpha=0 makes scale == adapter_scale**, so factors can be emitted unscaled.

## 5. Adding a new quant type later (fused-kernel PR map)

Recent, clean example pair (found via git log in this checkout):

- **Core type add**: `5eae9cb1d` "ggml : add NVFP4 quantization type support (#19769)" and
  `bec4772f6` "Add Q2_0 quantization: type definition and CPU backend (#24448)". Files:
  `ggml/include/ggml.h` (enum `ggml_type` + ftype), `ggml/src/ggml.c` (type_traits: blck_size,
  type_size, is_quantized, to_float), `ggml/src/ggml-common.h` (block struct),
  `ggml/src/ggml-quants.{c,h}` (reference quantize/dequantize), `ggml/src/ggml-impl.h`,
  `ggml/src/ggml-cpu/{ggml-cpu.c,quants.c,quants.h,arch-fallback.h,ops.cpp}` (CPU vec_dot +
  per-arch SIMD or fallback), `gguf-py/gguf/constants.py` (GGMLQuantizationType + QK sizes),
  `gguf-py/gguf/quants.py`, `include/llama.h` + `src/llama-quant.cpp` (LLAMA_FTYPE mapping),
  `tests/test-quantize-fns.cpp`, `tests/test-backend-ops.cpp`.
- **CUDA kernel registration**: `9b2a08881` "CUDA: add Q2_0 support (#25707)" — the exact
  20-file map: `ggml/src/ggml-cuda/common.cuh` (type traits qk/qr/qi), `convert.cu` +
  `dequantize.cuh` (dequant to F16/F32), `getrows.cu`, `ggml-cuda.cu` (supports_op),
  `vecdotq.cuh` (dp4a dot for MMVQ), `mmvq.cu` (case dispatch), `mmq.cu`/`mmq.cuh` +
  `mmq-load-tiles.cuh` + `mmq-config-{ampere,pascal,cdna,rdna*}.cuh` (MMQ tile configs),
  `template-instances/generate_cu_files.py` + new `mmq-instance-q2_0.cu`,
  `tests/test-backend-ops.cpp`.
- Project convention (visible in `5eae9cb1d`'s commit message): backend implementations are
  **shelved into separate per-backend PRs** so backend specialists review independently
  (NVFP4 CUDA came later: `112c78159` dp4a kernel #20644, `84f82e846` MMQ #21074; Metal
  `14d3ba45f` #25419; Vulkan `788e07dc9` #25430). Budget for the same split.
- Note `llama.cpp/AGENTS.md`: this repo requires human-owned design, no AI-written PR
  descriptions, and discussion in an issue before any PR. A fused "Q2-plus-low-rank" type would
  be an invasive new subsystem — expect a hard sell; the per-type PR map above is for a
  conventional new quant type.

## Verdict

**v0 zero-change prototype: VIABLE.**

The runtime already computes literally `y = Q·x + s·B·(A·x)` per adapted tensor
(`src/llama-graph.cpp:1486-1515`), on GPU, against any base quant including IQ2_XS/Q2_K, with a
runtime-tunable scalar `s`, hot-swap, and full server support. No llama.cpp changes needed.
Caveats to engineer around (none blocking):

1. **Decode overhead, unfused**: ~4 extra kernel launches per adapted weight per step
   (2 mul_mat + scale + add, no fusion pattern exists). Expect ~10-20% tok/s hit at bs=1 with
   full-coverage rank-32-64 adapters. This is the motivation for the later fused-kernel work,
   not a v0 blocker. Measure with `llama-bench` `--lora` early.
2. **Emit factors in GGUF tensor space** (post-permute), or go through the fake-PEFT route so
   `modify_tensors` handles arch permutations (q/k rope interleave, wqkv splits). Getting this
   wrong fails silently as quality loss, not an error.
3. **Set `adapter.lora.alpha = 0`** so effective scale == CLI scale; otherwise scale is
   `adapter_scale*alpha/rank` (`src/llama-adapter.h:53-57`).
4. **A/B dtype**: use F16 (BF16 fine). Q8_0 works but forces rank % 32 == 0 and adds dequant
   overhead on tiny tensors for negligible VRAM savings.
5. **Name coverage**: adapters attach by exact tensor-name match and only where the arch's
   graph builder routes through `build_lora_mm`; verify the target arch's `src/models/*.cpp`
   covers every tensor we correct, and note norm vectors are ignored.
6. **token_embd/lm_head** have special shape/tie rules (flipped A/B for embd; tied lm_head
   unsupported) — start with attn+FFN only, add embd/output later if the residual there matters.
7. NVFP4-with-tensor-scales cannot carry loras (`src/llama-graph.cpp:1699-1704`) — irrelevant
   for IQ2/Q2_K bases, but rules out an NVFP4-base variant of this scheme for now.
8. MTP and quantized-KV interop are clean; multi-config server batching splits by lora set
   (single always-on adapter avoids this entirely).
