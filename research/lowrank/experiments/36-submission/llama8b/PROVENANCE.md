# E36-L provenance — Llama-3.1-8B base

| item | value |
|---|---|
| HF repo | `NousResearch/Meta-Llama-3.1-8B` (ungated mirror of `meta-llama/Llama-3.1-8B`) |
| HF revision | `1f47e50cdbe801ad8a5174156ec3a0655108fb9f` (main, fetched 2026-10-15 16:08–16:11 PDT, `hf` CLI 2.0.0) |
| files fetched | `*.safetensors` (4 shards) + `*.json` (config, generation_config, tokenizer, tokenizer_config, special_tokens_map, index, original/params.json) |
| converter | `llama.cpp/convert_hf_to_gguf.py --outtype bf16`, llama.cpp tree @ `8e126574f` (branch beast-rank-kernels = b10865 `d4389a4dd` + research patch), its own `gguf-py`; torch 2.11.0+cpu, transformers 4.57.6, numpy 2.5.4, Python 3.14.7, in a throwaway venv (deleted) |
| BF16 GGUF | `weights/research-staging/llama31-8b/Meta-Llama-3.1-8B-BF16.gguf` |
| GGUF bytes | 16,068,891,040 (292 tensors, 8,030,261,312 params) |
| GGUF sha256 | `a49f6a496573d6a70f0a4998944d366ec49914c9db8b551a009a581232a21238` |
| conversion log | `plan-logs/convert.log` (tokenizer pre = `llama-bpe`; `rope_freqs.weight` F32 {64} written, the Llama-3.1 rope-scaling factors) |

Safetensors sha256 (verified at download; the shards were then **deleted** —
disk was tight: the concurrent 27B campaign was writing ~60 GB of Grams and
files and this pipeline peaks at ~85 GB. Re-fetch with
`hf download NousResearch/Meta-Llama-3.1-8B --revision 1f47e50cdbe801ad8a5174156ec3a0655108fb9f`):

```
f8b9704ab09cdeb097aa4a0a24bca96f906eec36bad63ab495bc21475058601b  model-00001-of-00004.safetensors
c28b25e7541751056ee126627e007f8d4288319733285e9f7b17b9ff6eb313f0  model-00002-of-00004.safetensors
d8e9504dd4e4a146d484c52a97584ec14dac92237c46b064934af67a85e7d383  model-00003-of-00004.safetensors
e4486f35c040f683f7d790354f66c169c109eb9fa0954a4a35d7c458a108405d  model-00004-of-00004.safetensors
54acfad3cffe057640904ca8a1e83525e6551c70c7a04c641f5a9eda0bbf64bd  config.json
76e48799b099d43365bd24ccd8ecc5aedac831718da780552f03b0a6eb4412aa  tokenizer.json
```

The JSON files (config, tokenizer) are kept in `weights/research-staging/llama31-8b/hf/`.
`pipeline.sh` re-checks the GGUF sha256 before its first GPU step.
