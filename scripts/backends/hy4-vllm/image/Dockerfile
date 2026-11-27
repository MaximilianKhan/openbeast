# vLLM for Hy4-flash NVFP4 on DGX Spark: the official vllm/vllm-openai:hy4-flash arm64 image (vLLM 77766db with the
# Hy4-flash pull request, FlashInfer 0.7.4), pinned by digest, plus six Python files. The image's Hy4-flash code
# takes FP8 only and needs Hopper or data-center Blackwell for attention; the files add:
#   sm120.py            Hy4's per-head learnable attention sink through FlashInfer's SM120 sparse MLA kernel
#   attention.py        Hy4-flash layers pick that backend on compute capability 12 (sparse and sliding-window)
#   hy_v4_attention.py  the same choice for the DFly drafter's layers
#   mla_attention.py    fp8_ds_mla KV cache on sliding-window MLA (as vllm-project/vllm#68365)
#   model.py            ModelOpt NVFP4 experts: weight, block scale, global scale and input scale for each expert
#   dfly_config.py      DFly accepts a ModelOpt NVFP4 target (the drafter's own layers stay BF16)
# No weights inside.
FROM vllm/vllm-openai@sha256:5e2c1badbca60595d6591c60feb4b8547f59da96e7a91710d40239250e721aba
ARG V=/usr/local/lib/python3.12/dist-packages/vllm
COPY sm120.py attention.py model.py $V/models/hy_v4_flash/nvidia/
COPY hy_v4_attention.py $V/models/hy_v4/nvidia/attention.py
COPY dfly_config.py $V/models/hy_v4/dfly_config.py
COPY mla_attention.py $V/model_executor/layers/attention/mla_attention.py
ENV CUTE_DSL_ARCH=sm_121a VLLM_WORKER_MULTIPROC_METHOD=spawn
# vLLM, Triton and FlashInfer keep their compile caches under $HOME/.cache; with "cache: true"
# that is ./cache/<recipe>, so only the first start compiles
ENV HOME=/cache
ENTRYPOINT ["vllm", "serve"]
