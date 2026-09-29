# IQ-artifact stability repro — 2026-09-15 06:30:46
engine: version: 0.4.0-dev (build 10865, commit d4389a4dd); GPU: NVIDIA GeForce RTX 5090, 610.57.04

## A-udiq3s-np6 — /home/max/Documents/openbeast/weights/research-staging/Qwen3.8-27B-UD-IQ3_S.gguf, -np 6, env: 
- SUMMARY requests=438 tokens=1794048 errors=0 elapsed=6010s first_error_at=None first_error=None
- crash line: NONE within 100 min

## B-udiq3s-np6-nograph — /home/max/Documents/openbeast/weights/research-staging/Qwen3.8-27B-UD-IQ3_S.gguf, -np 6, env: GGML_CUDA_DISABLE_GRAPHS=1
- SUMMARY requests=432 tokens=1769472 errors=0 elapsed=6010s first_error_at=None first_error=None
- crash line: NONE within 100 min
