# READY-TO-POST confirmation comment for llama.cpp #23575 (fix PR
# thread for issue #23476 — imatrix blind on MTP/NextN tensors)
# Prepared 2026-08-11 from the 08-03 draft + recon ammo. Post from
# Max's account after a personal read-through; adjust voice freely.
# One comment, the ACTIVE 23-comment thread — not a new issue.
# (Per CONTRIBUTING: evidence attached, AI-assistance disclosed.)

---

Confirming this issue with measurements from an independent setup, plus
two data points from elsewhere that may help scope the fix.

**Reproduction (master `0ef6e55ed`, CUDA, Qwen3.6-27B MTP-preserved,
64 layers + NextN):** a 100-chunk wikitext-2 `llama-imatrix` run
produces 496 entries and **zero** entries for `blk.64.*` — the NextN
block is never executed by the calibration graph. Consequences we hit:

1. Silent: k-quant requantization proceeds with the draft layer
   quantized unweighted — the one part of the model whose quality the
   speculative acceptance rate depends on is the one part calibration
   never sees. No warning.
2. Hard: IQ2-class quantization fails outright — `Missing importance
   matrix for tensor blk.64.attn_k.weight in a very low-bit
   quantization`. Stock tooling cannot produce sub-2.5-bpw quants of
   any MTP-preserved GGUF.

**Validated workaround** we've been running for a week:
`--tensor-type "blk\.64\.=q5_k"` — dodges the hard failure, and
pinning the draft head high is arguably the right default anyway (one
layer, negligible bytes, protects acceptance rate).

Two independent corroborations that draft-head quantization
sensitivity is real, not theoretical:

- The winning entries of the ICML AdaptFM "Efficient Qwen" efficiency
  competition this June are informative here: the rank-2 entry
  quantized MLPs aggressively but deliberately kept the MTP module in
  FP16, and the rank-3 entry (arXiv 2607.04244) showed a drafter
  co-calibrated against the *quantized* target preserves acceptance
  rates. Practitioners are already paying bytes to protect exactly the
  tensors this issue leaves uncalibrated.
- #26903 (MTP export / lm-head quant scales) looks like the same
  blind-spot family surfacing from the export side.

On fix shape, from our experience: (1) quantize-side fallback
(higher-k-quant + warning for imatrix-less NextN tensors, like the
existing token-embd special-casing) plus (2) an imatrix end-of-run
warning listing tensors that received zero activations would have
caught this for us immediately, and (2) is useful beyond MTP — it
catches any never-executed branch. The fuller fix — running the MTP
graph during calibration, as the abandoned #23258 prototyped — gets
real statistics for the draft tensors; we're experimenting with that
mechanism locally for activation-covariance capture and can report
numbers if useful.

Happy to contribute a PR for (1)+(2) if there's agreement on the
approach. (Disclosure: measurements and text prepared with AI
assistance; reproduced and reviewed by hand.)
