# Hy4-flash on two DGX Sparks: which engines can serve it (2026-11-19)

Read-only research for Max: what can serve `tencent/Hy4-flash`
(`hy_v4_flash`, HYV4FlashForCausalLM, 512 experts / 16 active, 220B total
including 64B of Engram tables, ~12B active, 1,048,576-token context,
built-in 0.85B DFly2 drafter) on the Spark pair. Labels: **VERIFIED** (read
code or registry), **CLAIMED** (cited page), **UNVERIFIED**.

**Staged (2026-11-19, later the same day).** The recommended two-Spark path
below is built into the repo: `scripts/backends/spark-hy4-stage.sh` (weights
at 30f201ac verified against the Hub's LFS sha256, the base image by digest,
the recipe's image from the vendored files, the tar for offline restore),
`scripts/backends/hy4-vllm/` (start, stop, memguard, the unit), `beast-spark
hy4`, campaign arm `H_T2`, and the intake family `hy4-vllm`. Nothing is on the
Sparks yet; staging and the first start follow the current eval series. The
EXL3 pack stays on both Sparks on purpose. Runbook:
[SPARK_HYDRA.md "Hy4-flash (NVFP4, vLLM TP2): staged"](../SPARK_HYDRA.md#hy4-flash-nvfp4-vllm-tp2-staged).

## Verdict

- The pack we downloaded, `LibertAIDAI/Hy4-flash-EXL3-4bpw` (218 GB, on both
  Sparks' `~/Documents`), **cannot span two Sparks**. ExLlamaV3's TP workers are
  local processes on one host (VERIFIED, `model_tp.py`). The pack fits ONE
  Spark only: an 84.87 GiB trunk with 118 GiB of Engram streamed from disk.
  Its runtime there is pure-PyTorch reference attention with no server
  (VERIFIED in the patch). Not a serving path.
- **Best two-Spark path today: vLLM TP2 with atakann's NVFP4 build**
  (`atakans/Hy4-flash-NVFP4`, 165 GB). About 82 GiB per Spark at a 262K
  context. Decode is about 15 tok/s plain and 17-38 tok/s with DFly; prefill
  is 1,110-1,461 tok/s (CLAIMED). It needs the `vllm/vllm-openai:hy4-flash`
  image plus patches for sm_121 (FlashInfer SM120 sparse-MLA,
  `fp8_ds_mla` KV).
- **Single-Spark alternative: llama.cpp** with PR #32517 (unmerged) and
  AngelSlim's GGUF SPARK-4Bit-Fast (149 GiB; about 87 GiB resident, Engram on
  SSD). Decode is 32-40 tok/s with MTP (CLAIMED).
- **Quality reference: vLLM FP8 (ZackSample recipe, 287 GB),** on Tencent's
  unmodified weights, Engram on disk.
- **Not paths yet:** SGLang (no GB10 report), TensorRT-LLM and
  ktransformers (no support), ExLlamaV3 upstream (fork only),
  **TensorFold** (#3797 open; #3801 is a two-Spark native family in slices,
  weeks out; the installed 1.2.10 lacks it).

## Against our default, DeepSeek-V4.1-Flash (Mia EXL3)

On Tencent's own launch table (CLAIMED), Hy4-flash trails DeepSeek-V4.1-Flash
on agentic and coding rows (Terminal-Bench 4.0 19.2 vs 40.0, DeepSWE v1.1
64.6 vs 73.8, ProgramBench 16.0 vs 29.0) and is close on OSWorld 2.1 (68.1 vs
68.4) and HLE with tools (55.2 vs 57.1). Tencent positions full Hy4 for
long-horizon SWE, and Flash for cost. It decodes about 1.5-4x slower than
DeepSeek's ~60 tok/s on the pair. Its upsides are about 82 vs 99 GiB per Spark
and native vision. Estimate (UNVERIFIED): 40s to low 50s of 71 on our v5 board.

## Fit (about 100 GiB of weights per Spark)

| Build | Total | Per Spark | Two Sparks? |
|---|---|---|---|
| BF16 | 410 GiB | ~146 GiB even with Engram on disk | No (4 Sparks) |
| FP8 | ~267 GiB | 77 GB with Engram on disk | Yes, Engram on disk only |
| NVFP4 (atakans) | 153 GiB | ~82 GiB | Yes, comfortably |
| GGUF SPARK-4Bit-Fast | 149 GiB | ~87 GiB on ONE Spark | One Spark |
| EXL3 4bpw (ours) | 203 GiB | 85 GiB trunk on ONE Spark | One Spark only |

## Offline staging (when we try it)

- **vLLM NVFP4:** pull the base image by digest, build the recipe image
  (six .py files on top), `docker save | ssh spark-2 docker load`, and
  `hf download atakans/Hy4-flash-NVFP4 --revision 30f201ac…` on both Sparks.
  Keep the first-start `./cache` (Triton/FlashInfer autotune; about 10 min
  otherwise). Pin it via `scripts/engine.sh stage/install vllm --from`.
- **llama.cpp:** clone upstream at 2cbca389 plus the SHA-pinned AngelSlim
  patch (sxuff recipe), and download `AngelSlim/Hy4-flash-GGUF` at
  `ecd803c8…`, SPARK-4Bit-Fast.
- **Serving it on the pair means stopping DeepSeek:** one model at a time.
  Run it between eval arms, as a new v5 arm on the board.

## License (Tencent Hy License 1.0; CLAIMED, not legal advice)

Royalty-free and worldwide. Quantized builds are derivative works: pass the
license on. §10: a separate Tencent license is needed only for third-party
model-as-a-service above US$50M revenue or 100M MAU, and internal use is
exempt (§10.3). Local use on the Sparks is unrestricted.

## Sources

[tencent/Hy4-flash](https://huggingface.co/tencent/Hy4-flash) ·
[vLLM recipe](https://recipes.vllm.ai/tencent/Hy4-flash) ·
[vllm#68380](https://github.com/vllm-project/vllm/issues/68380) ·
[atakans/Hy4-flash-NVFP4](https://huggingface.co/atakans/Hy4-flash-NVFP4) ·
[atakann/dgx-spark-recipes](https://github.com/atakann/dgx-spark-recipes) ·
[ZackSample/hy4-flash-dgx-sparks](https://github.com/ZackSample/hy4-flash-dgx-sparks) ·
[sxuff/Hy4-Flash-Spark-recipe](https://github.com/sxuff/Hy4-Flash-Spark-recipe) ·
[AngelSlim/Hy4-flash-GGUF](https://huggingface.co/AngelSlim/Hy4-flash-GGUF) ·
[llama.cpp#32517](https://github.com/ggml-org/llama.cpp/pull/32517) ·
[SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Tencent/Hy4-flash) ·
[TensorFold#3797](https://github.com/ashhart/TensorFold/issues/3797) ·
[TensorFold#3801](https://github.com/ashhart/TensorFold/issues/3801) ·
[benchmarks via atomicagent](https://atomicagent.io/blog/tencent-hy4-flash/) ·
[LICENSE](https://huggingface.co/tencent/Hy4-flash/blob/main/LICENSE)
