> ⚠️ REVIEW 2026-08-04: DUPLICATE — this issue already exists upstream as #23476 (+ fix PR #23575). Convert to a confirmation comment there; do NOT post as new.

# DRAFT upstream issue #1 — llama-imatrix never calibrates MTP/NextN
# tensors, which silently degrades and partially blocks quantization of
# MTP-preserved models

*Status: DRAFT — not posted. Post from Max's account after review.
Written 2026-08-03 against master `0ef6e55ed`. Per CONTRIBUTING: issue
first, hand-written, evidence attached, AI-assistance disclosed.*

## Summary

`llama-imatrix` computes importance matrices from the ordinary decode
graph. The MTP/NextN draft layer (e.g. `blk.64.*` on a 64-layer
Qwen3.6-27B MTP-preserved model) is never executed during calibration,
so its tensors get **no imatrix entries**. Two consequences:

1. **Degraded (silent):** k-quant requantization of MTP models proceeds
   with the draft layer quantized WITHOUT importance weighting — the
   speculative head, whose acceptance rate throughput depends on, is the
   one part of the model calibration never sees. No warning is emitted
   for the k-quant case.
2. **Blocked (hard):** IQ2-class ("very low-bit") quantization refuses
   outright: `Missing importance matrix for tensor blk.64.attn_k.weight
   in a very low-bit quantization`. Stock tooling therefore cannot
   produce sub-2.5-bpw quants of ANY MTP-preserved GGUF.

## Reproduction

```
llama-imatrix -m <mtp-model>.gguf -f wiki.train.raw --chunks 100 -o imat.gguf
# imat.gguf: 496 entries; zero entries for blk.64.* (the NextN block)
llama-quantize --imatrix imat.gguf <mtp-model>.gguf out.gguf IQ2_XS
# -> llama_model_quantize: failed to quantize: Missing importance matrix
#    for tensor blk.64.attn_k.weight in a very low-bit quantization
```

Observed on Qwen3.6-27B MTP-preserved (arch qwen3, 64 layers + NextN);
100-chunk wikitext-2 imatrix; master `0ef6e55ed` CUDA build.

## Workaround (validated)

```
llama-quantize --imatrix imat.gguf \
  --tensor-type "blk\.64\.=q5_k" <mtp>.gguf out.gguf IQ2_XS
```

Pinning the draft layer at q5_k both dodges the hard failure and is
arguably the right default (a sharp draft head preserves acceptance
rate at negligible size cost — the NextN block is one layer).

## Possible fixes (in ascending effort)

1. `llama-quantize`: when an MTP/NextN tensor lacks imatrix data, fall
   back to a higher k-quant for that tensor with a warning, instead of
   failing the whole run (matches the spirit of the existing
   token-embd/output special-casing).
2. `llama-imatrix`: emit a summary warning listing tensors that
   received zero activations (useful beyond MTP — catches any
   never-executed branch).
3. `llama-imatrix`: optionally run the speculative/MTP graph during
   calibration so draft tensors get real statistics.

Happy to submit a PR for (1)+(2) if maintainers agree on the approach.
