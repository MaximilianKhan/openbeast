> ⚠️ REVIEW 2026-08-04: prior public report exists — #21037 Bug 2 (SET_ROWS missing backward, closed stale). Reframe as confirmation comment with our KV-bypass workaround + gradmatrix repro; "first-order discovery" claim retracted.

# DRAFT upstream issue #4 — training/finetune: no gradient flows through
# the KV cache, so attention-score gradients into wk/wv are silently zero

*Status: DRAFT — not posted. Post from Max's account after review and
independent reproduction. Found 2026-08-04 during gradient-statistics
instrumentation work against master `0ef6e55ed`. AI-assistance to be
disclosed per CONTRIBUTING.*

## Summary

In the training path (`llama_opt_epoch` / examples/training/finetune),
the backward graph does not propagate gradients through the KV cache:

1. `ggml_set_rows` (the cache write) has no backward implementation, and
2. the attention computation reads the cache via views of the cache
   LEAF tensor, which is disconnected from the current step's write —
   so even within a single ubatch, the path
   loss → attention scores → K/V → wk/wv
   contributes ZERO gradient. wk/wv still receive gradients through
   other routes (none through attention scores), and wq/wo train
   against a frozen-K/V attention.

Consequence: llama-finetune runs converge but optimize a subtly wrong
objective — attention key/value projections never learn from attention-
score error. This is easy to miss because losses still go down.

## Reproduction sketch

Instrument the backward graph (sched eval callback on OUT_PROD nodes)
and dump per-tensor gradient norms for one training window: wk/wv
attention-score gradient contributions are exactly zero for every layer
while wq/wo carry normal magnitudes. (Our instrumentation tool and the
callback pattern can be shared; alternatively assert on the absence of
any backward node whose src chain reaches the cache write.)

## Workaround used locally

For a capture/training regime of one ubatch per window (n_kv ==
n_tokens), bypassing the cache — attention consuming k_cur/v_cur
directly — restores the full gradient path and is exact for that
regime. Env-gated in our local patch (`src/llama-graph.cpp`,
build_attn). A general fix needs either a set_rows backward + view
reconnection, or the same bypass applied whenever the whole window is
in-step.

## Why it matters

Anyone using llama.cpp finetuning today trains attention with frozen
K/V score paths. For LoRA-style finetunes targeting wq/wv (common), the
wv updates learn only from the value-mixing path, and wk adapters learn
nothing meaningful at all.
