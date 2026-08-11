> ⚠️ REVIEW 2026-08-04: the launch-overhead mechanism asserted below was FALSIFIED by our own Phase-2 kernel work (the wall is tail-load serialization + register pressure). Rewrite measurements section from REPORT-PHASE2/2B/3 before posting.

# DRAFT upstream issue #2 — LoRA adapter decode overhead at batch 1 is
# launch-bound (rank-independent), and the mmvq fusion path could absorb it

*Status: DRAFT — not posted. Post from Max's account after review.
Written 2026-08-03 against master `0ef6e55ed`. Companion measurement
data for a future fused-epilogue PR (design notes in our lab:
`prior-art/arxiv-kernels.md`).*

## Summary

Serving a quantized base + LoRA adapter at batch 1 costs far more decode
throughput than the adapter's FLOPs/bytes justify, and the cost is
**rank-independent** — the signature of per-op launch overhead from the
extra `mul_mat` + `add` nodes per adapted tensor, not of the arithmetic.

## Measurements (RTX 5090, CUDA, -ngl 99, greedy 512-tok decode)

Qwen3-0.6B Q2_K base, 196 adapted tensors, F16 factors:

| config | tok/s | Δ |
|---|---|---|
| base | 667 | — |
| + rank-64 adapter | 444 | −33% |
| + rank-16 adapter | 407 | −39% (!) |

Rank 16 is SLOWER than rank 64 — cost is launches, not math. On a 27B
(Qwen3.6 MTP-preserved, Q2_K base, 400 adapted tensors): 99.7 → 66.5
tok/s (−33%) with rank-64 F16 factors; **81.0 tok/s (−19%) with Q8_0
factors** (halved factor reads), still far above the ~1–2% the extra
bytes/FLOPs predict.

CUDA graphs are NOT the culprit on current master: `graphs reused`
counts are identical with and without the adapter (284/284 in our
server logs) — the overhead is real kernel work inside the captured
graph.

## Why this is worth fixing

The adapter path is the natural serving vehicle for quantization-error
correction (quantized base + low-rank correction, LQER/EoRA-style — cf.
compilade's Discussion #8831). Published fused implementations put the
achievable overhead at 5–10% (SVDQuant/Nunchaku; NVIDIA EoRA kernels).
The mmvq batch-1 path already has an epilogue-fusion mechanism
(`has_fusion` template; backend pattern fusion via `ggml_cuda_try_fuse`)
that bias/gate fusions use — a `B·(A·x)` epilogue rides the same
machinery: fuse `Aᵀx` into `quantize_q8_1` (shares the x read), fuse
`B·t + scale` into the mmvq output write.

We are preparing (a) a benchmark harness for adapter-decode overhead
and (b) a draft fused-epilogue implementation for the batch-1 mmvq
path, and would welcome maintainer guidance on scoping before a PR.
