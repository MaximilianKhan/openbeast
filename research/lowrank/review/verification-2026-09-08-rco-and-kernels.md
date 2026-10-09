# Verification memo — RCO vs equivalence class; kernel-contamination audit
# (Max's directive, 2026-09-08)

## 1. RCO (arXiv 2605.00649) vs the equal-byte equivalence class: CLAIM HOLDS

Read: "Model Compression with Exact Budget Constraints via Riemannian
Manifolds" (Helcig & Alistarh — IST-DASLab, same lab as GSQ). RCO is a
general budget-constrained discrete optimizer (Riemannian logit-space
relaxation + Gumbel-ST + DP) applied to per-tensor TYPE assignment.
**It contains NO equal-byte comparison of correction mechanisms** (no
low-rank arm, no carving, no shared-basis, no residual sidecars) and
makes no mechanism-class-per-byte claims — it compares type-assignment
strategies against each other and uniform baselines.

**Verdict: our claim not only holds, it gains significance.** E28/E29
measured that among low-rank mechanisms at equal bytes (27B, K-quant),
correction ≈ carve ≈ shared-basis and ONLY type allocation escapes.
RCO is the method-side complement: a strong optimizer for exactly the
mechanism our controls identified as the escape. Cite as: "RCO
optimizes within the one axis our equal-byte controls show carries
leverage; neither work subsumes the other — theirs is the optimizer,
ours is the mechanism-class measurement." Same-lab GSQ+RCO artifacts
make T1.17 the natural meeting point.

## 2. Kernel-contamination audit of every benchmark row: THREE-TIER VERDICT

Question: did research kernels in the live serving build perturb our
benchmarks? Patch audited hunk-by-hunk
(kernels/0001-beast-rank-low-rank-*.patch, 1950 lines) + runtime
provenance.

**Tier 1 — the LoRA-TQ research FEATURES: provably DORMANT.**
- All lora compute paths gate on `fusion.lora_b != nullptr` /
  `fusion.lora_t_qs` — non-null only when LoRA adapter tensors are in
  the graph.
- The lora branch-merge in graph_optimize requires fan-out ==
  `2*max_fan_out` (every projection paired with a lora matvec) AND
  weight types in {F16,F32,Q8_0}; plain K-quant serving graphs take the
  upstream-identical path.
- Runtime: ZERO `--lora` occurrences across all eval server cmdlines
  and all serve scripts — no adapter has ever been loaded in a
  benchmark. Env toggles (GGML_CUDA_DISABLE_LORA_TQ /
  _MIN_ROWS) unset, and they only disable lora sub-paths anyway.

**Tier 2 — allocator deferred-frees: ACTIVE in plain serving, numerics
untouched.** FLAG_BRANCH marks ALL fork-join concurrency windows
(upstream QKV windows included); frees inside a window defer to the
join. This changes memory ADDRESSES/peak slightly — it is a
cross-stream WAR-hazard safety fix (arguably an upstream bug we
patched) — and changes no arithmetic.

**Tier 3 — one genuine behavioral deviation from stock upstream:**
`evaluate_and_capture` restores original node order whenever
concurrent events exist (upstream: only when launching them). This can
change which single-stream FUSIONS fire vs stock — numerically
equivalent-by-design kernels, not bit-identical. Scope: a fusion
micro-difference in some graphs.

**Materiality for the leaderboard: NONE within eras.** Every row since
2026-08-04 shares this exact build (paired and cross-model comparisons
internally consistent — including Phase A/A′). The July rows (champion
b9690) predate the patch — that cross-build seam ALREADY exists via
the GDN-norm issue (#28068) and llama.cpp version drift, and is
retired by the same remedy: the planned clean-upstream ≥b10829 serving
build + one-build-per-table rule. Behavioral pass/fail over full
completions is insensitive to fusion-level float ordering at any
plausible magnitude; no mechanism was found by which the research
FEATURES could alter a benchmark verdict.

Bottom line: **benchmarks were not running research features.** Two
infrastructure-level deviations from stock upstream existed (both
conservative/safety-oriented), shared identically by all post-08-04
rows; the clean rebuild closes even those by construction.
