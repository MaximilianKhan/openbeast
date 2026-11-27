# Credits and licenses: the Hy4-flash vLLM image

Vendored verbatim from [atakann/dgx-spark-recipes](https://github.com/atakann/dgx-spark-recipes)
at commit `e764efa3efed3e21fe543c3fffc927eb6a00bff8` (2026-11-18, "Hy4-flash NVFP4 on two
Sparks"), directory `images/vllm-hy4-flash/`. Fetched 2026-11-19 through the GitHub contents
API; no file was changed.

| File | sha256 | License |
|---|---|---|
| `Dockerfile` | `b8958dfaf8311079e3ec39ea6162e65e314ee16df114753bc8240078ae572016` | MIT, (c) 2026 atakann (`LICENSE.dgx-spark-recipes`) |
| `attention.py` | `b67afe0ff16b384261dec87338c559ae089843ad8bcc094f26671661c119f8b2` | Apache-2.0, contributors to the vLLM project (SPDX header in the file) |
| `dfly_config.py` | `9513efde31a50279fe892ba01d27a546dc15b494fcd7b628639e8f82b26dafe8` | Apache-2.0, vLLM project |
| `hy_v4_attention.py` | `269d390eeaec8a6cf5f85c014542ce4426f4d62e904c82499d6d34633c1938dd` | Apache-2.0, vLLM project |
| `mla_attention.py` | `9540ec2427fe3d747944165a2d416812f2a8e64c74c062e24c9e1b44e96aec67` | Apache-2.0, vLLM project |
| `model.py` | `69865a097d847d446fdcdfb81672249a426a8abfea4bc8da278efb2e79fa2716` | Apache-2.0, vLLM project |
| `sm120.py` | `b260fc169c926b5f026081a993650bfe43cce6cd53b8b80fdcabbc6e4ec0f5df` | Apache-2.0, vLLM project |
| `LICENSE.dgx-spark-recipes` | `89f39b03954d0d8396b9eee86e302207ad46d931290360a50116cb566780e064` | the recipe repository's MIT license, verbatim |

What the files do (the recipe's own description): `sm120.py` routes Hy4's per-head learnable
attention sink through FlashInfer 0.7.4's SM120 sparse-MLA kernel; `attention.py` and
`hy_v4_attention.py` pick that backend on compute capability 12 for the model's and the DFly
drafter's layers; `mla_attention.py` adds the `fp8_ds_mla` KV cache on sliding-window MLA (as
vllm-project/vllm#68365); `model.py` loads ModelOpt NVFP4 experts; `dfly_config.py` lets DFly
take a ModelOpt NVFP4 target. The same changes are proposed upstream in
[vllm-project/vllm#68380](https://github.com/vllm-project/vllm/issues/68380) (open, 2026-11-19).

The base image `vllm/vllm-openai@sha256:5e2c1bad…` (the arm64 image of the `hy4-flash` tag) is
vLLM's own (Apache-2.0). The weights `atakans/Hy4-flash-NVFP4` are a derivative of
`tencent/Hy4-flash` under the Tencent Hy License 1.0, which a quantized build passes on
(docs/reviews/HY4_FLASH_ENGINES-2026-11-19.md, "License"). No weights are in this directory.

Updating: fetch the new commit's `images/vllm-hy4-flash/`, replace these files, update this
table, then set `HY4_IMAGE_HASH` in `../config.sh` to `hy4_image_hash` of this directory
(`tests/test_spark_hy4.sh` fails until they agree) and bump `HY4_RECIPE_COMMIT`.
