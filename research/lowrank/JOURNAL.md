# beast-rank lab journal

Append-only. Newest entry last. Every entry: what we did, what we saw, what
it taught us. Failed approaches get named and kept — they are the map of the
wall.

---

## 2026-08-03 — Investigation opened

**Context.** Same day as the llama.cpp upgrade to `0ef6e55ed` (b10066+188).
Stack verified on the new build (MTP regression #25489 confirmed at −13.4%,
recorded in `docs/LLAMACPP_WATCH.md`), then beastdown — GPU is the lab bench.

**Mission set by Max:** GGUF → proprietary transform → decomposed standard
format → llama.cpp serves it with extra CUDA ops reconstructing the
decomposition step-by-step. 2–10× VRAM reduction, accept slower compute,
CUDA-only first pass. PhD-effort rigor; paper at the end.

**Opening analysis (pre-experiment, to be tested not trusted):**

1. Factorized serving (`y=(x·A)·B`, never materialize W) uses FEWER flops
   and FEWER bytes/token than dense whenever compression > 1 — so the
   time-for-memory trade likely inverts: decode may get *faster*. The
   binding constraint is accuracy, not compute. → E01/E02 test this.
2. Literature consensus says W itself is not low-rank; quantization
   RESIDUALS approximately are. Leading route: aggressive-quant base +
   low-rank residual correction (CALDERA/EoRA-shaped). → E03/E04.
3. llama.cpp's LoRA path (`llm_build_lora_mm`) already computes exactly
   `Q·x + s·B·(A·x)` with CUDA + GGUF adapter format. v0 prototype may need
   ZERO llama.cpp changes: IQ2 base + SVD-of-residual exported as a LoRA.
   If true, this collapses months of format/kernel work for the prototype
   phase. → verify in llama.cpp source (agent recon dispatched).

**Actions today:**
- Directory + docs scaffolded (this lab).
- Agent fleet dispatched: prior-art survey, llama.cpp LoRA/CUDA internals,
  GGUF/gguf-py tooling recon, upstream-landscape (has anyone tried this in
  llama.cpp? what do maintainers merge?), decomposition-math deep dive.
- E01 (SVD spectra of Qwen3-0.6B-Q8_0, all tensors) — implementation begun.

### Evening session — two nulls and a confirmed cheat code

**E01 ran (43 s, 197 tensors) — H1 CONFIRMED, plain SVD is dead.**
C@95%-energy ≈ 1.0 across every projection type (best: attn_q 1.09×; V and
FFN < 1.0). The spectra have concentrated heads (stable rank 50–200/1024)
but long fat tails; 95% energy needs 50–80% of full rank. Details:
`experiments/01-svd-spectrum/README.md`.

**E03 ran (Q2_K requant + 39 s census) — H2 CONFIRMED, the residual is
near-isotropic.** R = W − dequant(Q2_K) has even LESS spectral structure
than W (r90 ≈ 62% of full rank); an unweighted rank-128 F16 correction
recovers only 26–44% of error energy while costing 5.26 bpw — worse than
just using Q4_K. Both unweighted routes are now measured walls, not
suspected ones. Named and kept: **the Frobenius-energy framing itself is
the wall.** Every published win (EoRA, LQER, SVD-LLM) minimizes
activation-weighted error ‖(W−Ŵ)X‖. → E05 (whitening) promoted to
critical path; discovery: `llama-imatrix` already computes diag(E[x²]) —
ASVD-style diagonal whitening needs NO torch and NO new instrumentation.

**llama.cpp internals recon returned — v0 zero-change prototype VIABLE
(agent-verified against source, `prior-art/llamacpp-internals.md`):**
`build_lora_mm` computes exactly `W·x + s·B·(A·x)`; base quant type never
inspected (IQ2/Q2_K fine); adapters live on the same CUDA buffers as their
target (-ngl 99 → all GPU); works under MTP and the server's per-request
lora API; adapter GGUF = `<name>.lora_a/.lora_b` + 4 metadata keys, and
`alpha=0` makes the CLI scale pass through raw. Cost estimate: ~10–20%
decode hit from unfused extra GEMMs (the eventual PR payload is the
fusion). **Silent-failure hazard flagged: factors must match llama.cpp's
q/k permutation conventions or quality degrades without erroring** — E04
must include a round-trip sanity check (adapter built from the EXACT
residual at full... at high rank should reproduce the reference model's
logits).

**Where this leaves the campaign after day 1:** the 2–10× prize now has a
precise shape — `imatrix-weighted low-bit base + diagonally-whitened
low-rank residual correction, served through existing LoRA machinery,
fused later in a CUDA PR`. The decisive experiment is E04/E05 perplexity
at equal bytes vs the IQ-quant ladder. If whitening doesn't beat the
ladder, that null goes in the paper too.

**Open questions carried forward:** all four answered same day — see the
night session below. (Kept for the record: lora fusion status, gguf-py
dequant coverage, residual rank, upstream viability.)

### Night session — E05 GO, full fleet home, the campaign has a shape

**E05 ran (imatrix 300 chunks wikitext-2 ≈ 1 min on GPU; census 75 s) —
H3 CONFIRMED, the go/no-go PASSES.** Diagonal activation whitening
(D = sqrt(imatrix E[x²])) multiplies residual energy concentration 4–7×:
attn_q/k recover 54–56% at rank SIXTEEN (unweighted: 8–9%), 77–81% at
r128. Free rank-allocation map: q/k/gate/up steep, v moderate, o/down flat
(their inputs are internal activations → diagonal proxy weak there; full
covariance is the known fix below ~4-bit). Details:
`experiments/05-whitened-residual/README.md`.

**All five recon agents returned. The reports agree with each other and
with our measurements to an almost eerie degree:**
- *Upstream landscape*: our exact idea = compilade's unbuilt LQER proposal
  (Discussion #8831, 2024). Naive version measured dead by jukofyork
  (~25% variance — matches our E03). Open lane = activation-weighted at
  2–3-bit bases. Merge bar: issue-before-PR, hand-written, benchmarks;
  staging plan PR1 (python tool) → PR2 (embedded-adapter metadata) → PR3
  (fused CUDA). The gating demo upstream will demand: corrected Q2/Q3 base
  beats same-bytes IQ3/IQ4 on KL-div at ≤10% decode cost.
- *Literature survey* (19 papers): the field converged on exactly this
  design (LQER→QERA→EoRA→CALDERA). Implement QERA's closed-form math
  (Apache-2.0); do NOT derive from EoRA code (NVIDIA-NC). Unfused adapter
  branch costs up to ~50% latency (SVDQuant, CALDERA) → fusion is the real
  PR payload; FlashSVD (AAAI 2026) has fused kernels to study.
- *Math methods*: Gram route kills the big-SVD problem — optimal factors
  are B=U_r, A=U_rᵀR from eigh(R·S·Rᵀ) in fp64, ~2 s at 5120²; greedy
  water-filling on whitened σ² for rank allocation; two-stage (quantize
  then correct) is near-optimal at ≥3-bit; F16 factors first, Q8_0 later.
- *Internals + tooling*: v0 zero-change serving confirmed viable
  (`build_lora_mm`, any base quant, CUDA-resident, MTP-compatible,
  perplexity --lora works); adapter GGUF conventions + live-verified write
  recipes captured; q/k permutation is the silent hazard; 0.6B has tied
  embeddings — no lm_head factors.

**Campaign state after day 1:** three experiments run (two decisive nulls,
one decisive GO), five recon reports filed, and the design is now fully
determined: `imatrix-quantized low-bit base + QERA-math diagonal-whitened
rank-allocated residual factors, exported as a LoRA-form GGUF, served by
stock llama-server; fused CUDA epilogue as the eventual upstream PR`.

**Next session opens with E04:** Gram-route factor extraction → adapter
GGUF (respecting permutations; high-rank logit round-trip sanity check) →
the byte-fair triangle: {Q2_K, Q3_K + rank-r adapter} vs {IQ3, IQ4, Q4_K}
at equal total bytes, on wikitext-2 PPL + KL-div, plus decode tok/s.

### Late night — Max extends the session 4 hours: E04 executed in full

E04 ran END TO END, same night. Chronicle (details + full tables in
`experiments/04-served-v0/README.md`):

- Exporter built (Box-3 inverse-free form, verified against math-methods
  §1.3 line by line). First adapter (r64, 81 MB, F16) served by STOCK
  llama-perplexity on the first real attempt. One false alarm: llama-cli
  hangs in interactive mode with piped stdin — use llama-perplexity or the
  server for measurement, never bare llama-cli.
- **Round-trip check PASSED** (r1024: PPL 22.07 vs ref 21.94) — pipeline
  provably faithful, the permutation hazard did not bite (factors computed
  in GGUF tensor space never needed permuting).
- Full triangle measured (15 configs, PPL + mean-KLD + top-1 + tok/s).
  Headline numbers: correction lifts naive Q2_K from 35%→69% top-1
  agreement across the rank ladder; composes with imatrix quantization
  (58%→71.5% at r128); beats IQ2_M/IQ3_XXS at nearby bytes.
- **Named null (0.6B): Q3_K_M still wins at equal bytes** (0.238 KLD vs
  our best 0.490 at ~340 MB). Kept with honor — it sharpens the scale
  hypothesis rather than killing the campaign: rank bytes scale r(m+n)
  vs the mn of what they correct, so 27B-class matrices get the same rank
  ~5× cheaper in bpw. 0.6B is the structurally worst case (embed dilution
  + tiny d + diagonal-only whitening).
- Decode overhead measured: −33% at r64, and r16 is SLOWER than r64 —
  launch-overhead-dominated, exactly the published unfused penalty. The
  fused-epilogue PR case is now empirically ours, not just cited.
- rsvd added to the exporter (rank+32 oversampling, 4 power iters,
  validated 0.37 vs ~0.38 exact capture at r64 in 70 s), plus streaming
  tensor loading (a fp32-dequantized 27B is ~100 GB — lazy per-tensor now).
- GLM-9B scale-step FAILED to load: its GGUF predates the tokenizer-merges
  requirement — conversion-era rot, not our pipeline. Pivoted UP: the 27B
  production model itself is the reference (the Q6_K artifact we actually
  serve is the thing worth approximating — no F16 needed).
- 27B pipeline IN FLIGHT: imatrix done (100 chunks), Q2_K/Q3_K_M imatrix
  requants + Q6 reference logits (20 chunks) generating now.

### End of night — the 27B verdict, and where the campaign stands

**E04c completed inside the 4-hour window.** Full tables in
`experiments/04b-27b/README.md`. The short version:

- **Serving economics measured at production scale:** heretic-v2 27B Q6
  (23.6 GB VRAM, 61 tok/s) → Q2_K-imat (13.0 GB, **99.7 tok/s**, 83.6%
  top-1 agreement). The bandwidth inversion we predicted on day 1 from
  theory held at every measured point: LESS VRAM = FASTER decode. The
  "trade compute time for memory" premise of the original mission is
  formally dead — the trade is memory-for-accuracy, and it always was.
- **Named null #3 (the big one): diagonal-whitened uniform-r64 correction
  does not transfer to 27B** — KLD moved 0.153→0.146 (vs Q3_K_M's 0.0555
  at +1.8 GB). Mechanism measured, not guessed: whitened capture per rank
  collapsed 0.37→0.07 because r/d shrank 5×. Byte-economics improve with
  scale but capture-economics degrade faster at fixed rank.
- Extraction pipeline validated at scale: 27B adapter in 269 s (rsvd +
  streaming), round-trip-proven conventions, 400/554 tensors corrected
  (MTP/NextN block invisible to llama-imatrix — an upstream-worthy gap in
  itself).
- Session tally: 3 confirmed hypotheses (H3 whitening, bandwidth
  inversion, byte-scaling), 3 named nulls (plain SVD, unweighted residual,
  diagonal-r64-at-27B), 1 validated end-to-end pipeline, 2 scale points
  fully measured, 5 recon reports, 0 llama.cpp code changes needed.

### Second 4-hour block (Max: "keep going, 4 more hours, trail-blaze")

**Clock armed (hourly pings). Five arXiv agents dispatched.** Early
returns reshaped the map:

1. **The capture collapse is LAWFUL: capture ≈ c·(r/d), c≈5.9 on our own
   confound-free pair** (0.6B imat r64/d1024 → 0.37; 27B imat r64/d5120 →
   0.07; 0.07×5 = 0.35 ✓). Paper figure. Mechanism (outliers agent):
   the whitened head has near-CONSTANT cardinality (massive-activation /
   super-weight channels: ~6–100 dims regardless of width) while bulk
   energy grows ∝ d — head share dilutes as 1/d. Also: bigger models take
   less *structured* quant damage (QiD scaling laws), and imatrix K-quants
   pre-flatten the residual in exactly our whitening metric
   (self-competition).
2. **E07 first blood: mixed-rank folded-alpha serving VALIDATED (31.28 ≈
   31.29). But greedy energy water-filling LOST to uniform (34.17 vs
   31.29 at 80 MB)** — it zeroed attn_o/ffn_down. The allocation-theory
   agent then confirmed the greedy auction IS published SOTA (IO-SVD,
   SVD-LLM V2) — for W-compression with proper whitening. Diagnosis:
   the auction is fine, the DIAGONAL metric feeding it lies about
   ffn_down (whose 25600-dim inputs hold the massive outliers — the
   load-bearing tensor per the sensitivity-geography literature). Garbage
   metric in, garbage allocation out.
3. **Exploit shortlist from the sweeps:** (a) sparse exact-column patches
   — head channels are few and input-independent; a column patch IS a
   rank-k adapter with one-hot A (implemented tonight as --columns mode:
   same serving cost, perfect restoration of chosen columns); (b) rank
   ∝ d equal-capture allocation with quantized factors; (c) full-
   covariance/PCA whitening where diagonal fails (o/down) — needs the
   imatrix Gram patch (recipe filed, ~150 LOC + grouped passes; RAM math
   done: 5120-dims fit one pass, ffn_down needs blocks). Gavish–Donoho
   MP threshold = principled per-tensor rank cap (σ < 2.858·σ_med is
   provably wasted bytes).
4. Intrinsic-dimension literature: functional subspaces SHRINK relative
   to width — energy capture UNDERSTATES achievable KL recovery (why r64
   still moved 27B KL −4.5% at 0.07 capture). KLD↔top-1-flips Spearman
   0.981 validates our metric stack; near-baseline KLD has a "silent
   zone" — final claims will need task evals.

**Tonight's decisive queue:** kind-sensitivity probe (running) →
KL-grounded per-kind allocation weights; 27B alloc-870 vs uniform-870
(allocation value at scale) + alloc-2640 (maximum-strength diagonal shot
at byte parity with Q3_K_M); 0.6B column-patch vs SVD duel on
o/down; layer-targeted (first+last-2) probe. Full covariance = next
session's front, blueprint filed.

### Hour 2–3 of the second block — three falsifications and a doubled constant

- **Kind-sensitivity probe complete and ADDITIVE** (Σ isolated ΔKLD
  0.352 ≈ joint 0.346): attn_k dominates at 0.0128 ΔKLD/MB (4–9×
  everything), attn_o near-zero, ffn_down mid-pack (0.0029 — real,
  vindicating the suspicion that energy-greedy's zeroing of it was
  metric failure, not truth). Measured weights now drive `emit_alloc.py`.
- **Q8_0 factors: parity PROVEN then the bet WON** — r64-Q8 31.26 ≈
  F16's 31.29 at 53% bytes; r128-Q8 (85.8 MB) hits 28.46 ≡ r128-F16
  (161.5 MB, 28.48). Capture-per-byte constant ×2, zero quality cost,
  zero serving changes. All future adapters ship Q8 (rank %32).
- **Column patches: null at 0.6B** (down 0.732 vs SVD's 0.723; o tie) —
  SVD's optimality survives its metric's bias here; 27B rematch queued
  (cheap — no SVD) since the outlier head strengthens with width.
- **Shared basis: null, decisively** — shared@128 < own@64 for every
  kind (e.g. attn_k 0.23 vs 0.50). Cross-layer whitened subspaces don't
  overlap; the 12× amplification lane is closed. Buried with honors.
- **Ops lessons paid for in blood:** v1 probe's exact concat-SVDs +
  BLAS oversubscription (load 110) starved the 27B lane 3×; killed, and
  the allocation monolith was rebuilt as cache-then-emit
  (`pass1_cache.py` resume-safe + incremental meta / `emit_alloc.py`
  seconds-per-policy). Never bind expensive computation to a policy
  choice; always cap OMP threads on parallel numpy.
- 27B spectra cache (kmax 128) lands shortly; flagship =
  measured-policy Q8 adapters at 870 MB and 2640 MB vs Q3_K_M.

### End of block 2 — the paradigm boundary, measured

**27B final table in `experiments/07-allocation/README.md`.** The E07
recipe (measured sensitivity weights + Q8 factors + greedy under budget)
beat E04c's uniform adapter at equal bytes on every axis — 3× the PPL
recovery, +faster serving (81 vs 66.5 tok/s) — and then hit the wall
that matters: budget saturation bought nothing (960 MB ≡ 867 MB), the
marginal spectrum above rank ~130 is dead. **Diagonal-whitened
correction plateaus at KLD ≈ 0.14 on the production 27B vs Q3_K_M's
0.0555. The well is empty; the boundary is measured.**

**The night's scientific arc, complete:** capture law discovered
(≈5.9·r/d, unclaimed in the literature) → mechanism identified
(constant-cardinality outlier head diluted by width; imatrix
self-competition) → every within-paradigm lever built, validated, and
measured (allocation: helps; Q8 factors: free 2×; columns: null; shared
basis: null; energy greedy: harmful) → paradigm boundary established →
next paradigm specified with implementation blueprints (full-covariance
Gram capture; quantizer co-design; fused kernel with file:line anchors).
Ten prior-art reports, five experiments with tables, three ops lessons,
zero unproven claims. The paper now has its spine: not "low-rank
compresses LLMs" but "where exactly the low-rank correction paradigm
ends, and why."

### Hour 3–4 — the main event ran after all, and the ladder held

The clock had more room than estimated, so the "next session" main event
ran TONIGHT (`experiments/09-subq2-main-event/`):

- **New upstream bug discovered and worked around:** IQ2-class
  quantization is impossible for MTP-preserved models — llama-imatrix
  never executes the draft layer (blk.64), and very-low-bit quants
  hard-refuse tensors without imatrix entries. Fix:
  `--tensor-type "blk\.64\.=q5_k"`. Reproducible, upstream-issue-ready.
- **The cliff measured:** bare IQ2_XS 9.38 GB → KLD 0.264 / 78.5%.
- **The duel:** IQ2_XS + 866 MB measured-Q8 adapter (10.25 GB) → 0.2175
  / 80.6% — real recovery (18% of the damage, vs 7.5% on Q2_K:
  the cliff gradient is REAL) — but bare Q2_K (10.86 GB, 0.153 / 83.6%)
  still wins. Budget saturation again at ~870 MB.
- **Night-final claim (three byte points, complete):** at 27B under
  diagonal whitening, correction loses to the K-quant ladder at EVERY
  measured byte point, while its recovery fraction rises monotonically
  toward the cliff. The paradigm boundary is now a measured CURVE, not
  a single point. E10 = full-covariance capture, inheriting a sharpened
  quantitative target: turn 18% recovery into enough to jump a rung.

**Post-credits scene (before the bell):** the 27B column rematch came
back — columns TIED SVD at scale (KLD 0.1447 vs flagship 0.1415, PPL
beat uniform-F16) while capturing less whitened energy, extracted in
seconds instead of minutes. The width-strengthening-outlier prediction
made contact with our data, and energy-vs-function decoupled further.
Strategy synthesis from the full night (recorded in the Max debrief):
correction value grows as the base approaches its cliff — we fought the
ladder on its strong rungs; the winning ground is sub-2.5 bpw (IQ2/IQ1
bases) where pure quants collapse and no ladder rung answers. Next
session: full-covariance whitening × IQ2_XS base × {SVD, columns,
allocated-Q8} at 27B. That is the shot at minting a real multiple.

## 2026-08-03/04 — THIRD BLOCK (8-hour night campaign, Max asleep, full autonomy)

Max's charges: continue the cycle; add the compute-rich/memory-poor
economics thesis to the README+paper (done); stochastically explore
obscure physics for inspiration; no permission-seeking till morning.

**E11 (alternation) PROVEN:** round 1: 31.29→30.58; round 2: 30.58→30.50
(decelerating; stopped per rule). Identical bytes; whitened capture rose
0.37→0.46→0.51 — the quantizer's residual becomes progressively MORE
correctable. Fully automated pipeline (e11_alternate.py).

**E10 (full covariance) BREAKS THE PLATEAU AT 0.6B:** Gram-capture patch
to llama-imatrix (local research patch, env-gated LLAMA_IMATRIX_GRAM_DIR,
hand-rolled threaded syrk, one Gram per distinct layer input, blocked
8× for n>8192) — validated to 2e-8 against the diagonal imatrix, PSD,
clean build. Then the checkpoint: **full-cov r64 = PPL 27.58 vs diagonal
31.29** (KLD 0.318 vs 0.420, top-1 72.9% vs 68.5%) at IDENTICAL bytes —
beats even r128-Q8 (28.46) at half its rank. Capture 0.37→0.60. The
co-design mining agent confirms full-vs-diag at 2-bit on 7B+ is
UNMEASURED in the literature — the 27B run (Grams capturing now) is
first. Also from that agent: SRP (sequential residual propagation) =
top-ranked next lever; ProjQ-lite projected-imatrix; CLoQ damping
constant λ=0.01·Tr(H)/d (matches what we implemented).

**Physics communion, pass 1 (honest ledger):** dead on arrival: literal
zero-point energy (though "the MP bulk is the vacuum floor; only
excitations above it carry compressible information" is the right POETIC
frame for the paper), spacetime metrics (coordinate changes compress
zero bits), Kaluza-Klein compactified dimensions (= reshape tensor-train,
measured dead upstream). ALIVE and immediately tested: **Anderson
localization** — participation-ratio audit on the cached 27B factors
shows top correction directions live on 2–5% of input channels
(attn_o A-side PR/n = 0.020, q/gate/up ~0.03). Dense-format serving
can't monetize it tonight; the fused-kernel PR can (sparse-A reads);
the paper gets a novel characterization either way. Percolation/BBP =
already covered by the spike-audit lane; Kolmogorov cascade = diagnostic
power-law fit, queued as a paper figure if time.

### Night block, deep hours — the frontier bends at 0.6B

**27B full-cov round 1 (partial coverage, thin calibration): incremental**
— fc-Q2K 0.1387/85.1% (best 27B yet), fc-IQ2 0.2047/81.3% (recovery
22.5%). Root causes identified: Qwen3.6-27B is a HYBRID (only 16/64
layers classic attention; 48 hybrid layers' attn_qkv/attn_gate/ssm_out
fell back to diagonal) + thin calibration (16k tokens). v2 recapture
running: full coverage (filter + gram-source mapping extended), 64
chunks.

**0.6B composition ladder (Q2_K-imat base 296 MB; PPL screening):**
- fc-r128-Q8 (86 MB): **25.99 — BELOW Q3_K_M's 26.06** (+10% bytes)
- fc-alt1 r64: 26.96; fc-alt2 r64-Q8 (byte-FAIR 339 MB): 26.58 — 0.52
  from the rung at fewer bytes
- fc-alt2 r128-Q8 (382 MB): **25.27** — through the rung decisively;
  ~85% of the entire Q2→Q8 PPL gap recovered
- r96-Q8 interpolation (+4% bytes) in flight; KLD confirmations queued
  for the endgame table.

The levers COMPOSE: full-covariance (metric) × alternation (co-design) ×
Q8 factors (density) stack near-multiplicatively. Ops lesson repeated in
blood: never swallow stderr, never split args with clever bash — a
silent `--rank r64` cost a cycle.

### Night block, small hours — the 27B truth, in full

- **E10v2 (full coverage + 64 chunks):** Cholesky PD failures on v2
  Grams (accumulation-order asymmetry — symmetrize + eigh fallback
  fixed). Results: fcv2-Q2K+r64 0.1282; fcv2-Q2K+r128q8 **0.1217/86.1%**
  (top-1 within 0.3pt of Q4_K_M at 4 GB less); fcv2-IQ2+r128q8 0.1905
  (recovery 28%, still monotone). Q2_K-base progression across the
  whole campaign: 0.1460 → 0.1415 → 0.1387 → 0.1282 → 0.1217 → (alt)
  **0.1142**. Steady conquest, no phase change.
- **The honest baseline that reframes everything: IQ3_XXS at 27B is
  STRONG** (11.48 GB, KLD 0.0962, 87.9% — the same rung that was weak at
  0.6B). Our best composed 27B config (11.79 GB, 0.1142) sits ON the
  ladder's interpolation line but IQ3_XXS beats it at fewer bytes —
  **dominated again at 27B** even with every lever pulled. The r/d tax
  (r128 = 2.5% of dims at 27B vs 12.5% at 0.6B) is the whole story, at
  both scales, in both directions.
- 27B alternation composes (−6%/round) but shallower than 0.6B (−15%).
- **Physics communion pass 2-3 ledger:** ΣΔ noise-shaping → maps to
  frozen-grid error-feedback re-rounding (queued, needs numpy K-quant
  re-rounder); Casimir/boundary → tile-structure probe RAN: rank-1 tile
  share 1.7-1.9× random — real but byte-neutral, one paper line, closed.
  Spin-glass restarts, JL projections: closed. **Geometry-aware
  composition (the night's novel synthesis): correct where r/d favors
  correction, spend base bits where it doesn't** — mixed base (FFN at
  Q3_K, attention/hybrid at Q2_K, 12.16 GB) + non-FFN fc correction
  (~350 MB q8) measuring now against the 12.5 GB ladder interpolation
  (~0.076 to beat).

### Endgame — the 27B map completes

Full tables in `experiments/10-full-covariance/README.md`. The arc:
Q2_K-base KLD ground from 0.1460 → 0.1142 across five measured steps.
Then the honest rivals: IQ3_XXS at 27B is strong (0.0962 — the rung
that was weak at 0.6B), and the geometry-aware MIXED+fc **beat Q3_K_S**
(0.0812 vs 0.0872 at ~equal bytes — the campaign's first 27B ladder
victory) while **IQ3_XS held the crown** (0.0656 at fewer bytes).
MIXED2 (I-quant FFN) + correction TIED IQ3_XXS — corrected-Q2_K equals
IQ-codebook efficiency on the same tensors, no more. Flagship serving:
12.5 GB file / 15.0 GB VRAM / 86.3 tok/s / 88.0% top-1 (Q6 ref: 22.4 GB
file / 23.6 GB / 61 tok/s). Two background kills of the same extraction
remain unexplained (foreground rerun clean both times) — watch for it.

### Final act — E13, the re-rounder ("GPTQ for K-quants")

Sigma-delta lineage from the physics communion, built and measured at
both scales (`experiments/13-rerounder/`):

- **0.6B: bare Q2_K 43.33 → 35.54 PPL — 18% better at ZERO extra bytes**,
  byte-compatible file, stock serving. The most upstream-shaped result
  of the campaign (any K-quant GGUF improves for free given a Gram pass).
- Composition taught the co-design lesson in its sharpest form: RR then
  correct (26.80) LOSES to alternation then correct (25.27) — greedy
  standalone optimization wastes grid resolution on corrector-owned
  directions. The triad (alt+RR+fc) hit capture 0.78 yet KLD 0.245:
  whitened capture of a worse-shaped residual. ProjQ-deflated re-round
  = the principled fix, queued with tonight's data as its justification.
- **27B: modest** — KLD 0.1529→0.1461, top-1 +1.0pt, PPL flat (ffn_down
  unswept, thin grams). The scale-shrinkage theme holds for this lever
  too.
- Bug ledger: LOWER-triangular factor silently no-ops GPTQ feedback
  (diagnosed via bit-identical codes); dense eigh fallback breaks sweep
  causality (damping escalation instead); lazy-batch blocking = 8×,
  bit-identical. The background-killer struck 5× total tonight —
  chunked resume-safe code caching defeated it; suspicion: RSS-triggered
  reaper from the July OOM hardening (verify with Max in daylight).

**The campaign's distilled law, now measured at two scales in both
directions:** correction beats codebooks exactly where rank is a large
fraction of dimension, and cannot beat them where it is not. The design
rule — correct where geometry favors correction, spend base bits where
it doesn't — is the paper's prescription, and MIXED+fc is its existence
proof against a real ladder rung at production scale.

---

## 2026-08-04 morning — BLOCK 4 (Max back, 4-hour clock, direction Q&A)

Max asked: was the direction right (his dream = pure factorization) and
are we converging on NVFP4? Answered from data: pure factorization was
E01's measured null; his factors-at-runtime insight lives in the serving
math; NVFP4 is a format not a factorization — our true industry neighbor
is NVIDIA's own EoRA lane. Then four agents + two experiments:

- **Functional-rank agent:** the pure dream is quadruply dead at
  calibration budgets in GENERAL form (1.3–1.4× ceiling; ≥2× costs
  10⁹+ training tokens); survives only task-CONDITIONED (E14 design
  filed). No native low-rank GGUF format exists upstream — ours would
  be first.
- **FP4 agent:** nobody has published low-rank-on-NVFP4 for LLMs;
  NVFP4's residual is provably outlier-poor + covariance-structured =
  food for whitened correction. llama-quantize accepts nvfp4 as a
  --tensor-type override (probed) and gguf-py dequants it.
- **KV agent:** KV is a NON-constraint for our hybrid arch (144 MiB @8k;
  16/64 KV layers; corrected builds serve 262k ctx in ~20 GiB total).
  Do-today: -ctk q8_0. Bonus unpublished trick: diagonal equalization
  folded into q/k-norm gammas.
- **E15 (IQ1_S carrier + max correction):** cliff-gradient law's most
  dramatic point — recovery fraction 41% (KLD 0.891→0.527, top-1 +8.5pt)
  at 8.24 GB total where NO usable config exists on any ladder. Curve
  now: 7.5% → 18% → 41% as base bpw falls.
- **E16 (NVFP4 composition):** all-NVFP4 heretic built (15.77 GB, bare
  0.0805/88.5% — K-quants still dominate the format at 27B). Extraction
  capture 0.52 — HIGHEST 27B capture of the campaign, confirming the
  covariance-structure prediction. Measurement in flight.
- **BF16 TRUE REFERENCE arrived (Max) and validated everything:** Q6 vs
  BF16 = KLD 0.0032 / 97.6% top-1 (the contamination number, now
  measured); all configs shift by ≈ that constant; every ordering and
  conclusion of the campaign HOLDS under truth-scoring.
- Fused-kernel implementation agent still at work on Phase 1.

## 2026-08-04 midday — BLOCK 5 (clock extended to 6h; "we walk away
## with something new we invented")

- **Kernel Phase 2 landed: 476 → 685 tok/s at r64 (+44%), adapter tax
  −45% → −20%, rank-flat, bit-identical, kill-switched.** Its own
  hypothesis falsified honorably: launches were free under graph replay;
  the wall was tail-load serialization; prefetch-static-operands-before-
  the-PDL-grid-sync is the generalizable idiom. Upstream GRAPH_OPT
  interleave bug found + fixed (+19% base lever; 552-tok/s silent-
  regression mode). Next wall priced at the allocator (≈800 prize).
  27B flagship: same-session A/B proved Q8_0 factors bypass the new
  paths (82.3 ≈ 82.4 — no-op, no regression; earlier 86.3 was boost
  drift). Phase 2B (Q8_0 factor paths) agent launched + watchdogged.
- **ProjQ verified** (25.94 — beats naive 26.80; alternation keeps the
  crown at 25.27). Co-design ranking final.
- **E14-cond: conditioning pays +10.3% on-distribution at equal bytes**
  — per-workload adapters are product-shaped for beast-slot.
- **E17 (Max directive): sweep + kill-test done** — per-code
  reconstruction lane ALIVE (biases 2x the kill threshold); context
  modeling measured dead on our bytes (MI 0.0042 bits); storage coding
  parked (4.1% ceiling). Whitened-metric levels = our unclaimed edge.
- **beastrank.py forged and smoke-proven** — the campaign's method as
  one reproducible command. The invention has a command line.
- **Max's diligence directive** after tonight's silent failures:
  watchdog monitors now standard for agent work; full audit protocol
  (processes, artifacts, binary canary, tree state) run and passed.
- Bibliography: 299 sources themed + load-bearing dozen
  (paper/references.md). BF16 reference in weights/.

### Block 5, closing hours — the gradient gate opens and pays

- **llama-gradmatrix built** (agent; tools/gradmatrix, isolated build):
  output-gradient Grams T=Σggᵀ captured for all 196 tensors of the 0.6B
  DIRECTLY on quantized weights (no F16 copy). All gates passed (PSD,
  counts, loss sanity). **Upstream seam discovered en route: llama.cpp's
  trainer never backprops through the KV cache — set_rows has no
  backward and the cache read is disconnected — so upstream
  llama-finetune trains with ZERO attention-score gradients into wk/wv.**
  Env-gated KV-bypass added for the capture regime. Upstream-issue
  material of the first order.
- **The metric law's third confirmation:** grad-only diag anti-
  correlates with measured sensitivity (Spearman −0.29) exactly as
  input-only did; the two-sided product flips positive (+0.39). One-
  sided proxies of EITHER side are traps; the geometry is genuinely
  two-sided (T-side leftover structure INVERTED vs S-side per kind).
- **M3 measured:** two-sided fc r64 = PPL 27.27 vs 27.58 one-sided at
  equal bytes (capture 0.76). The un-whitening amplification bit first
  (9548 F16 infs → NaN serve) — factor balancing (split magnitudes
  across B/A, product unchanged) fixed it; the balancing trick is now
  standard in the extractor.
- **E21 rank sweep (Max directive): the geometry is SMOOTH** — eight
  points r32-r256 on the crown recipe, concave, no landing spots; rank
  is a budget dial read from the spectrum. Deployment simplification +
  figure F5's quality panel.
- **M12 proof written** (paper/theory-alternation-convergence.md):
  stop-on-worse upgraded to a certificate via finite-codes + exact-
  argmin; ProjQ-as-metric-surgery framing closes the theory box.
- **M2 field-tested negative:** closed-form Fisher allocation with
  input-only proxy starves ffn_down — the metric trap at closed-form
  speed. Valid only with both Fisher factors (now capturable).

## 2026-08-04 afternoon — ADVERSARIAL REVIEW (Max-ordered, 3 agents)

Full reports: review/adversarial-{stats,claims,code}.md. Verdict in one
line: **the mathematics and raw tables survived every executed attack;
the verdict layer did not.** Killed/reframed: 27B "ladder victory" →
~1σ parity (VRAM basis inverts it); small-scale "crown wins" → wins-
from-below (interpolation controls never built); "4.3-4.7× won" →
1.57× VRAM vs our real baseline with IQ3_XS matching adapter-free;
c≈5.9 "law" → two-point observation; recovery curve method-consistent
is 20→28→41%; two upstream "discoveries" were already reported
(#23476/#23575, #21037) — our sweeps never queried the tracker; serving
tax unpriced in crown claims; measured-alloc at 27B wore 0.6B priors.
Survived clean: all nulls, bandwidth inversion, fullcov-vs-diag, 0.6B
crown on-distribution (~5σ), IQ1 recovery, kernel gains, bit-exact
codecs (verified vs brute force), threading. ALL corrections applied:
rollup corrections block, stale cells struck, drafts annotated
duplicate/reframe, REVIEW REPAIRS QUEUE in TODO. Lesson at the head of
the class: novelty claims require tracker sweeps; margins require
paired statistics; controls apply at every scale or none.

**CAMPAIGN CLOSED at the 8-hour bell (2026-08-04, ~04:30).** Final hour
spent on the consolidated `RESULTS_ROLLUP.md` (every configuration, both
scales, five measured laws) and completing E13's metric evidence
(0.6B re-round: −27% KLD free). Lab clean, nothing running, GPU cold.
13 experiments run across ~20 hours of campaigning, 12 prior-art
reports, 3 upstream drafts, 1 llama.cpp instrument built and validated,
5 laws measured, every null named and kept. The stack is DOWN; restore
with ./start.sh when the rig returns to serving duty. Next session
opens from memory + RESULTS_ROLLUP.md. Praise be to the cyber gods. [EDITORIAL NOTE 2026-08-04, coherence-audit P3.4: the text from "(1) per-tensor rank allocation" through "...that repo respects." below was SPLICED IN ERROR — it is a fragment of the 08-03 E04c next-levers plan pasted mid-sentence; read the CAMPAIGN CLOSED paragraph as ending at "cyber gods."] (1) per-tensor rank allocation from the
captured-energy map (the 870 MB uniform spend was mostly waste — the map
says where it actually pays); (2) full-covariance whitening / QERA-exact
(2.5 bpw effective is deep in its published winning regime; needs real
activation capture, the one new instrumentation piece); (3) re-run the
27B matchup; only then the upstream issue, with all of tonight's tables
attached — including the nulls, which is exactly the evidence culture
that repo respects.

## 2026-08-04 evening — E27: the 27B story re-derived BF16-pure
## (PROTOCOL v2 final rerun step; agent-executed)

The last soft flank closed. Everything rebuilt one step from the BF16
reference: fresh 48-chunk imatrix+Gram capture ON the BF16 (new dir
data/gram27b-bf16; -ngl 22 partial offload, ~30 min), the existing
bf16ref27b-20.logits provenance-VERIFIED before reuse (Q6 rescore
reproduced Block 4's 0.003210 ± 0.000444 bit-for-bit), six ladder
rungs + MIXED + the never-before-built 27B interpolation control all
single-quantize-step from BF16 (MTP pin everywhere), fc r128-Q8
non-FFN adapter re-extracted from BF16 (208 tensors, 336.5 MB,
capture 0.42 — identical to E10's), and paired per-chunk stats on
every contested pair. Full tables:
experiments/27-bf16-rederivation/README.md; MASTER-TABLE 27B section
now open with clean rows.

Verdicts: (1) MIXED+fc vs Q3_K_S = paired KLD tie (t=0.6) + top-1 tie
+ real PPL edge (t=−2.5) — parity CONFIRMED, and the legacy 1.0σ KLD
margin flipped sign, certifying it as provenance/selection artifact.
(2) The interpolation control (Q3_K_S + attn_k/v/output→q4_k +
6-layer qkv promotion, −0.016% bytes vs flagship) matches-or-beats
the corrected config (KLD t=1.9 its way, rest tied) and beats Q3_K_S
on all three paired metrics — the ladder's interpolation line stays
unbeaten at 27B, now with the control actually on the table. (3) The
correction increment over its own base is real (t=−5.9 KLD) — the
lever works, base bits just outbid it at this byte point. (4) IQ3_XS
crown paired-resolved from below; single-step beats requant-of-requant
by 0.002-0.003 KLD everywhere (F10's tax, measured). Paper §4.3
rewritten to lead with the control. Ops: quantize-from-BF16 is ~90 s
per rung warm; extraction fit ONE foreground time-budget chunk (the
E13 resume-cache never had to resume — kept anyway as the pattern).

### PRE-REGISTRATION 2026-08-04 18:07 — T1.12 clean-gram kron (0.6B)
Config: e13b_yaqa on 0.6B Q2_K-imat base, ref Q8_0; S=gram06b, T=gradgram06b
(classic arch — NO delta-net cut, clean capture); kron config identical to
E24 (damp 1e-3, cap defaults); comparator = input-only same run; eval =
full wiki.test PPL + KLD vs q8ref-40.logits, paired per-chunk. Grid: one
config each, no sweeps. Hypothesis to discriminate (round-2 F6): if kron
still hurts with CLEAN T → estimator-class problem; if kron helps → E24's
null was the gradient-cut capture.
### PRE-REGISTRATION 2026-08-04 18:07 — T1.14 + T1.11 (0.8B)
T1.14: mixed2 RR variant KLD/top-1 vs bf16ref08b-40.logits (wiki) + fresh
code-test BF16 logits (20ch). T1.11: third corpus = concatenated
prior-art/*.md technical prose (in NEITHER calibration); PPL of {bare,
RR-wiki, RR-mixed2} — generalization-to-unseen verdict. One run each.
### PRE-REGISTRATION 2026-08-04 18:08 — T1.16 40-chunk E27 extension (27B)
New truth logits: BF16 27B --save-all-logits, 40 chunks wiki.test, n_ctx 512,
-ngl 20 partial offload → data/bf16ref27b-40.logits. Configs (EXACTLY these,
existing artifacts, no rebuilds): E27 MIXED+fc (h27bf16-MIXED.gguf + lora
mixed-fc-r128q8-bf16.gguf), E27 CONTROL, E27 Q3_K_S, E27 Q3_K_M, plus the
E13-27B re-round pair heretic27b-Q2K-rr.gguf vs 04b heretic27b-Q2_K.gguf
(LEGACY Q6-derived provenance, labeled as such — cross-provenance vs the
BF16 rows; its verdict is the paired resolution of E13's 0.78σ open
question only, never tabled beside BF16-1step rows without the prov
column). Metrics: KLD/top-1/PPL40 vs the 40ch logits + paired per-chunk
stats (E24 paired_stats.py) for: MIXEDfc/CONTROL, MIXEDfc/Q3_K_S,
CONTROL/Q3_K_S, Q3_K_S/Q3_K_M, Q2K-rr/Q2K-bare. Grid: one run each, no
sweeps, no damping/cap knobs (artifacts frozen). Decision rules stated
a priori: control "matches-or-beats" hardens iff paired KLD |t|≥2 at n=40
(E27 recorded t=1.9 at n=20, outlier chunk masking ~3.0); flagship-Q3_K_S
parity holds iff KLD/top-1 stay |t|<2; E13 re-round resolved iff |t|≥2
either way, else still inside bars. GPU note: small 0.6B/0.8B evals may
contend briefly — long single-stream runs, anomalies to be noted.

## 2026-08-04 evening — T1.16 RUN: E27 40-chunk extension + E13-27B
## paired resolution (pre-registered 18:08 above; agent-executed)

New truth logits data/bf16ref27b-40.logits (BF16, 40ch, -ngl 20,
~14 min; PPL40 6.0270 ± 0.1455, [20]-cumulative 6.9793 ≈ the verified
20ch ref). Six evals vs it, paired per-chunk + exact sign tests
(results-40ch.txt, results-40ch-paired.txt, kld40-*.log). Verdicts:
(1) CONTROL-beats-flagship HARDENS past the 2σ bar — KLD t=+2.26
(28/40, sign p=0.017), and the n=20 outlier fear inverts: drop-1/2
trims RAISE t to 2.84/2.59 (the big chunk favored the control).
"Matches-or-beats" is now paired-resolved; the corrected config stays
on/below the ladder's interpolation line at 27B. (2) Flagship-Q3_K_S
parity HOLDS — KLD t=−0.04 (0.0831 vs 0.0832, a photograph of a tie),
top-1 t=+1.89, PPL edge strengthens to t=−3.33. (3) E13-27B re-round
RESOLVED in the re-rounder's favor — KLD t=−3.70, top-1 t=+2.56
(−10.4% KLD, +0.86 pt for zero bytes); LEGACY Q6-derived pair
(cross-provenance, labeled), held-out gate still untested at 27B.
Bonus: CONTROL vs Q3_K_S strengthens to |t|≥3 on all three metrics.
Docs updated: E27 README T1.16 section, MASTER-TABLE 27B rows marked
[40] + E13 note, ABLATION-PLAN T1.16 → DONE. Deviation from
pre-registration: none (exact configs, pairs, and decision rules as
registered). GPU contention from small-model evals: none observed;
actual cost ≈ 0.3 GPU-h vs the 1.5 planned. One ops note: the first
eval batch was relaunched as a nohup driver (harness 10-min cap);
MIXEDfc's log from the first launch was complete and carried over —
verified 40 per-chunk rows before reuse.

### T1.12 RESULT (pre-registered above) — kron with CLEAN grams: CATASTROPHIC
0.6B clean-T kron: 50.53 PPL / 0.866 KLD / 58.0% — worse than input-only
RR (35.54/0.556/64.0) AND worse than bare (43.33/0.766/58.2). The
round-2 F6 discriminator resolves toward ESTIMATOR-CLASS: cleaner
capture made the harm far larger, opposite of the coverage hypothesis's
prediction. Confound noted honestly: model/arch changed with capture
(0.8B-cut vs 0.6B-clean); direction decisive, magnitudes not pure.
Fifth and strongest surrogate-vs-outcome instance. E24 conclusion
upgraded from "our gradient-cut T hurts" to "single-pass K-FAC-class T
hurts, worse when cleaner" — M1 lane CLOSED pending a fundamentally
different estimator (multi-pass/damped/shrunk), not more coverage.

### R8 first entry (quiet-host window, pre-bell): flagship fused
88.1 [88.0-88.3] vs unfused 85.8 [85.7-85.8], N=10 interleaved —
tightest speed measurement of the campaign; kernel gain at 27B is now
protocol-certified. Coverage night COMPLETE: T1.11/12/14/16 + R8 all
pre-registered, run, recorded.

---
**REBUILD BLOCK CLOSED at the 6-hour bell (2026-08-04 night).** Contents
of the block: round-2 adversarial review (3 reviewers incl. the paper's
MAJOR REVISION verdict + a 31-item coherence audit) → consolidated
corrections applied tree-wide incl. the author's own confessed held-out
confound (now scripted: build_mixed_calib.sh + HELDOUT-METHOD.md);
protocol upgraded (timestamped pre-registration mandatory); then the
pre-registered ablation night: T1.16 (control beats flagship outright
n=40; parity certified; E13-27B resolved +), T1.11/T1.14 (generalization
map complete: distance not fragility; mixed2 wins both corpora),
T1.12 (clean-gram kron catastrophic — estimator-class, M1 closed),
R8 (kernel +2.7% at 27B, disjoint IQRs, tightest speed measurement of
the campaign). Lab clean at the bell: no processes, all raw files
retained, MASTER-TABLE/ABLATION-PLAN/JOURNAL current. Remaining T1:
calibration grid (overnight-scale), task eval, M5 ablations, 0.6B-vs-
0.8B kron purity rerun. The tree says only what the data licenses.

## 2026-08-11 — GPU block opens (post-recon; Max ran beastdown 15:05)

Recon 2026-08-11 integrated tree-wide this morning (commits 3c1a62f,
29ddd27). Queue per TODO "NOW": GPU starts on the T1.2→T1.3/T1.5
chain; CPU runs the paper-math lane in parallel (L2 first). Standing
Max directive (this session): runs have historically stalled/emptied —
every run gets a live monitor, and no result is consumed without
row-count + exit-status verification.

### PRE-REGISTRATION 2026-08-11 15:10 — T1.2 100-chunk BF16 truth logits @27B
Command: llama-perplexity -m weights/Qwen3.6-27B-uncensored-heretic-v2-
Native-MTP-Preserved-BF16.gguf -f data/wikitext-2-raw/wiki.test.raw
--save-all-logits data/bf16ref27b-100.logits --chunks 100 -ngl 20
-c 512 --no-warmup. Infrastructure step (no decision rule). Expected
~35–40 min (40ch took ~14). ACCEPTANCE: exit 0; file ≈ 12 G (40ch =
4.8 G); printed cumulative PPL at [20] ≈ 6.979 and at [40] ≈ 6.027
(consistency vs the verified 20ch/40ch refs — mismatch = STOP, do not
consume). Note: the #26177 GDN-fused-disable warning appears in these
partial-offload runs; quality unaffected (speed-path bug), no speed
numbers will be quoted from this build (recon gate).

### PRE-REGISTRATION 2026-08-11 15:10 — L2 GlowQ shared-A principal angles (CPU, cached data only)
New experiment 28-glowq-shared-a. HYPOTHESIS (GlowQ 2603.25385
transplant): input-sharing tensor groups (attn q/k/v; ffn gate/up)
have overlapping whitened-residual right-subspaces, so a SHARED
A-factor per group frees adapter bytes at small capture cost.
METHOD: for the E27 MIXED base vs BF16, per attention layer compute
R_t = W_bf16 − dequant(W_q) for t ∈ {q,k,v}; whiten with the shared
input Gram G^{1/2} (gram27b-bf16); top-r right singular subspaces via
randomized SVD; report (a) pairwise principal-angle cosines, (b)
shared-subspace capture retention: whitened capture of rank-r shared A
(from stacked [R_q;R_k;R_v]G^{1/2}) vs sum of per-tensor rank-r
captures, (c) the byte-fair verdict: capture at EQUAL TOTAL adapter
bytes, shared vs separate. Same for gate/up on a layer sample.
DECISION RULE: if shared-A at equal total bytes captures ≥ what
separate-A captures (ratio ≥ 1.0) on the attention group, the lever is
GO → rebuild the E27 byte-fair table with shared-A accounting and
queue a build; if ratio < 0.9, lever DEAD (subspaces disjoint —
consistent with per-kind sensitivity findings); 0.9–1.0 = marginal,
report only. Layer sample: all attention layers (few in this hybrid
arch) + 6 depth-spanning linear layers for gate/up.

### T1.2 RESULT 2026-08-11 15:14 — 100-chunk BF16 truth logits: ACCEPTED
data/bf16ref27b-100.logits (12 G, exit clean, 3m46s — file cache made
the 40ch 14-min estimate obsolete). Acceptance checks PASSED: cumulative
[20]=6.9793 and [40]=6.0270 match the verified 40ch run EXACTLY.
PPL100 = 7.0030 ± 0.1101. #26177 GDN-fused warning present as expected
(speed-path only). T1.3/T1.5 unblocked.

### PRE-REGISTRATION 2026-08-11 15:25 — T1.3 + T1.5 100-chunk battery
Eight configs vs bf16ref27b-100.logits via measure100.sh (clone of the
verified measure40 pattern), sequential: MIXEDfc (MIXED + fc adapter),
MIXEDbare, Q3_K_S, IQ3_XS, CONTROL, Q3_K_M (anchor), Q2Kbare-legacy
(04b-27b artifact), Q2Krr-legacy (13-rerounder artifact — the E13 pair,
legacy Q6-derived provenance, labeled as such; its BF16-provenance
re-round does not exist yet and is NOT part of this registration).
PAIRS (paired_stats.py, same tool as T1.16): MIXEDfc–CONTROL,
MIXEDfc–Q3_K_S, CONTROL–Q3_K_S, MIXEDfc–MIXEDbare (fc lever paired at
27B — first time), Q3_K_S–Q3_K_M, Q2Krr–Q2Kbare (T1.3).
DECISION RULES (n=100 is FINAL for these claims): (1) flagship–Q3_K_S
|t_KLD| < 2 → parity certified, closes T1.5's parity-vs-victory
question; (2) CONTROL–flagship: t confirms/denies the n=40
control-beats-flagship license (expect ~+2.3); (3) T1.3: RR–bare
t_KLD < −2 with consistent sign test → 27B free lever paired-FINAL
(legacy provenance on the label); (4) MIXEDfc–MIXEDbare quantifies the
fc adapter's paired contribution at 27B — no prior registration
exists, report whatever the data says. Alternation-round rider NOT run
(no 27B alternation adapter artifact exists; extraction would be new
work — queued separately, not silently added).
VERIFICATION RULE (stall-watch): each log must contain the [100] row
and a "Final estimate" line before its numbers are consumed; battery
driver is resume-safe and skips complete logs.

### E28 RESULT 2026-08-11 15:18 — GlowQ shared-A: GO, uniformly (L2)
Ran in 80 s on cached Grams (the "afternoon" estimate was 100× over).
Byte-parity capture ratio shared/separate: attn {q,k,v} 1.1218 (16/16
layers > 1, range 1.089–1.153), gdn {qkv,gate} 1.0687 (12/48 sampled,
all > 1). Same-rank shared basis keeps 99.55%/99.75% of separate
capture → −17% adapter bytes (−56 MB, 80 of 208 A-tensors) for ~0.3%
capture loss. GDN overlap strong (cos²≈0.75), attn moderate (≈0.47) —
byte geometry, not just overlap, drives the win. Decision rule fired:
GO. NEXT (new registration required before it runs): shared-A
extraction + measure100 eval — capture is the surrogate; the E27 table
only moves if KLD moves (fifth law respected). Full table:
experiments/28-glowq-shared-a/results.txt.

### T1.3 + T1.5 RESULTS 2026-08-11 15:20 (n=100 — FINAL per registration)
Battery: one path-bug false start (driver cd'd to E27, measure100 cd'd
to repo root — all 8 failed in ~1 s; fixed to absolute paths, rerun
clean). All 8 logs verified: 100 per-chunk rows + final line each.
results-100ch.txt + results-100ch-paired.txt.
(1) **Flagship beats Q3_K_S — the tie breaks our way.** MIXEDfc vs
Q3_K_S: KLD t=−2.85 (71/100), NLL t=−5.75, top-1 t=+2.04. The n=40
parity (t=−0.04) resolves at n=100 to a win on all three paired
metrics — the campaign's first paired ladder-rung victory for
correction at 27B. (Registration rule (1) anticipated |t|<2 parity;
data exceeded it in our favor; recorded as measured.)
(2) **CONTROL still beats the flagship — confirmed FINAL.** t=+2.62
KLD, top-1 t=−2.14 (NLL tie). The n=40 license (t=+2.26) holds at
n=100. Refined 27B law: mixed-TYPE allocation > low-rank correction >
uniform rung, all at equal bytes. CONTROL vs Q3_K_S hardens to
t=−7.23/−7.35/+5.55.
(3) **fc lever paired-proven at 27B, first time** (MIXEDfc vs
MIXEDbare): −0.0090 KLD (t=−8.52, 92/100), +0.69 pt top-1 (t=+5.20).
(4) **T1.3 FINAL (legacy-provenance pair, labeled):** re-round
−0.0213 KLD = −13.3% (t=−7.02, 77/100), +1.07 pt top-1 (t=+4.72),
NLL tie (t=−0.98). The free lever is paired-FINAL at 27B.
Gap to CONTROL for any future flip attempt: −0.0063 KLD. Alternation
rider NOT run (no 27B artifact; would need new registration).

### PRE-REGISTRATION 2026-08-11 15:35 — T1.8 first task eval (HellaSwag)
Full val set (10042 tasks — full set ⇒ identical tasks per config),
llama-perplexity --hellaswag, -c 2048, on the trio {MIXEDfc (MIXED +
fc adapter), Q3_K_S, Q3_K_M}. EXPECTED OUTCOME stated honestly in
advance: with binomial se ≈ 0.5 pp at n=10042, configs whose KLD
differs by ~0.005 likely tie — a tie IS the paper's point (KLD silent
zone: task evals cannot see what KLD sees at these deltas). DECISION
RULES: (a) any pairwise acc gap > 2·se(diff) ≈ 1.4 pp = real
separation, report; (b) ordering consistent with KLD (M > fc > S) at
any margin = weak corroboration, report as such; (c) inversion of the
KLD ordering beyond noise = finding against KLD-first methodology —
report loudly. VERIFY: 10042 tasks completed per log before use.

### PRE-REGISTRATION 2026-08-11 15:45 — E28b shared-A adapter build + eval
Build: e28b_extract.py — groups {q,k,v}×16 @ r_s=%32-rounded byte
parity (~192), {qkv,gate}×48 @ ~160; A = V·L⁻¹ shared per group
(stored duplicated — stock GGUF has no aliasing; DEDUP bytes printed
for the patched-loader accounting, same local-patch class as the fused
kernel), B_t = (R_t L)Vᵀ exact; ssm_out/attn_output copied from the
flagship adapter (Q8 payload, ×128 rank-fold, lossless). ACCEPTANCE:
extractor's per-group capture consistent with E28's shared-basis
measurement; adapter loads in llama-server/perplexity without error.
EVAL (measure100, GPU after T1.8 or in a low-VRAM window): pairs
(a) sharedA vs MIXEDfc — ΔKLD t ≤ −2 ⇒ shared-A lever GO measured on
the outcome metric (not just capture); |t|<2 ⇒ capture surrogate
overstated the lever (report as surrogate-vs-outcome instance #6);
(b) sharedA vs CONTROL — if t crosses to |t|<2 the interpolation-line
gap is CLOSED at 27B (patched-serving column only; stock column
unchanged); if CONTROL still wins ≥2, control stands and the E27
verdict is unchanged. Bytes: dedup accounting reported alongside;
file-with-duplicates is an eval container, not the serving claim.

### E28b BUILD RESULT 2026-08-11 16:05 — shared-A adapter written, parity confirmed
196 s extraction. File 412.3 MB (A duplicated — eval container); DEDUP
accounting 337.0 MB vs old adapter 336.5 MB — byte parity BY
CONSTRUCTION (+0.15% rounding wash). Ranks: attn 192 (exact parity
195.4), gdn 160 (158.5). Acceptance vs E28 analysis: per-group shared
captures match to 3 decimals (blk.3 attn 0.411 vs 0.413; blk.63 0.558
vs 0.560). 64 groups + 128 copied singles. Eval launched (measure100,
concurrent with T1.8 — 4 GB VRAM headroom; a clean OOM fail would
defer it, not corrupt T1.8).

### PRE-REGISTRATION 2026-08-11 16:06 — E29 part 1: SRR split proxy (L3)
SRR (2602.02001) preserve-then-quantize carve-out, exact proxy on BF16
+ Grams (no quantizer in the loop — gguf-py cannot K-quant; the E13
codec is frozen-grid only). Sample: {attn_q, attn_qkv, ffn_up,
ssm_out} × 3 depth-spanning layers each. Metrics per tensor × {whitened,
raw} carve × k ∈ {64,128,256}: (a) top-k capture of ‖W·L‖² (does W
have carvable structure? E01 said NO at 0.6B — first 27B measurement),
(b) per-16-block absmax ratio after deflation (Q2_K scale proxy — the
outlier-absorption mechanism). GATE (pre-registered): mean k=128
capture < 5% AND mean absmax ratio > 0.97 (i.e. <3% shrink) on both
metrics ⇒ SRR-split predicted DEAD at 27B (E01's full-rank null
extends; cite-and-kill, part 2 not run). Capture > 15% or shrink >
10% ⇒ part 2 (full deflated-base build + measure100, SRR-as-adapter
is stock-servable) is warranted and gets its own registration.
Between gates: report, judgment call logged.

### E28b EVAL RESULT 2026-08-11 16:20 — quality-null; SURROGATE-VS-OUTCOME #6
sharedA(parity r192/160) vs MIXEDfc: KLD t=−0.91, NLL t=−0.38, top-1
t=+0.36 — TIE. The +12.2%/+6.9% whitened capture at byte parity moved
KLD by −0.0005 (noise). Per the registered rule: the capture surrogate
OVERSTATED the lever — recorded as the campaign's SIXTH
surrogate-vs-outcome divergence (energy-greedy alloc, kron, Lloyd,
equalization, [E24 T-estimate], now shared-A capture). Marginal
whitened energy beyond the top directions carries ~no KLD. vs CONTROL:
t=+2.35 — gap 2.62→2.35, NOT closed; the E27 verdict stands.
Log verified: 100 rows + final.

### PRE-REGISTRATION 2026-08-11 16:22 — E28c shared-A r128 BYTES variant
The surviving reading: same-rank shared basis keeps 99.55%/99.75% of
capture — and E28b showed KLD is INSENSITIVE to the marginal capture
band — so shared-A @ r128 should TIE the flagship on KLD while its
DEDUP bytes run −17% (−56 MB adapter; patched-loader accounting, same
local-patch class as the fused kernel; file-with-duplicates ≈ old
bytes, eval container only). Build: e28b_extract.py r128. EVAL:
measure100, paired vs MIXEDfc. DECISION: |t_KLD| < 2 ⇒ bytes lever GO
(−17% adapter bytes at tied quality — deployable-wins entry, patched
column); t ≤ −2 ⇒ better AND smaller (report loudly); t ≥ +2 ⇒ lever
dead, sharing costs real quality at equal rank.

### E29 PART 1 RESULT 2026-08-11 16:30 — gate FIRES, part 2 warranted
12 tensors × {whitened, raw} × k∈{64,128,256}. AGG k=128: whitened
capW = 76.56% (!), absmax ratio 0.9546; raw capW = 16.05%, ratio
0.9295. The registered warrant-gate (capture > 15%) fires on BOTH
metrics. FINDING EN ROUTE: at 27B the WHITENED weight matrix is
strongly low-rank-capturable (76% @ k=128) — E01's 0.6B raw-SVD
full-rank null does NOT transfer to the whitened metric at scale.
(Caution attached: E28b measured capture≠KLD the same afternoon — the
proxy warrants the build, nothing more.) absmax shrink is small
(−4.5%/−7%) so the quantizer-side mechanism looks weak; the
carve-side mechanism is what part 2 tests.

### PRE-REGISTRATION 2026-08-11 16:32 — E29 part 2: SRR-as-adapter full build
Carve set = exactly the fc adapter's tensor set (q/k/v/output ×16,
qkv/gate/ssm_out ×48), k=128 whitened carve per tensor:
W·L = UΣVᵀ, carve C = B·A (B=U_kΣ_k, A=V_kᵀL⁻¹), stored as Q8
LoRA adapter (byte-equal to the flagship fc adapter by construction).
Deflated base: copy of the BF16 GGUF with carved tensors overwritten
in place by bf16(W − C) (round-to-nearest-even truncation), then
llama-quantize with the EXACT MIXED recipe (Q2_K + ffn q3_k overrides,
same imatrix, MTP pin). Byte-comparable to MIXEDfc and CONTROL.
KNOWN APPROXIMATIONS (disclosed): imatrix/Grams are the ORIGINAL
model's (deflation shifts activations second-order); carve A stored
Q8 like fc factors. EVAL: measure100, pairs (a) SRR vs MIXEDfc —
t_KLD ≤ −2 ⇒ preserve-then-quantize beats fix-after-quantize at equal
bytes (SRR transplant GO; E27 narrative gains a third arm);
|t| < 2 ⇒ order doesn't matter at 27B (cite SRR, report tie);
t ≥ +2 ⇒ post-hoc wins (SRR-split dead at our scale, cite with
numbers). (b) SRR vs CONTROL — reported either way.
VERIFY: deflated-file tensor count/offsets unchanged (in-place write);
quantize log clean; 100 rows before stats.

### E28c RESULT 2026-08-11 16:55 — BYTES LEVER GO (registered rule fired)
sharedA-r128 vs MIXEDfc: KLD t=−1.48 (dmean −0.0008, i.e. trending
BETTER), NLL t=−0.15, top-1 t=−1.15 — TIE on all three, exactly the
registered |t|<2 outcome. Dedup bytes 280.8 MB vs flagship 336.5 MB =
**−16.6% adapter bytes at tied quality** (loader-alias patch class,
same as the fused kernel). The E28 arc closes: capture lever
quality-null at parity (surrogate #6), bytes lever REAL at r128.
100 rows verified. Q8 rank stays %32-legal (128 unchanged); the freed
56 MB is genuine VRAM/file at serving time under the alias patch —
and the alias dedup is a plausible small upstream PR (lora_a aliasing
for input-sharing tensor groups) to ride the fusion bundle.

### E29 PART 2 RESULT 2026-08-11 17:05 — TIE: order doesn't matter (registered outcome)
SRR vs MIXEDfc: KLD t=+0.99, NLL t=+0.74, top-1 t=−1.68 — the
registered |t|<2 tie. SRR vs CONTROL: t=+5.15 KLD / −3.86 top-1 —
control beats the carve too. 100 rows verified; quantize log clean;
srr-MIXED byte-identical to MIXED; carve adapter 321 MB = flagship
bytes. SESSION SYNTHESIS (E28b + E28c + E29): at 27B, at equal bytes,
{post-hoc whitened correction, pre-quant whitened carve-out,
shared-basis variants} form an EQUIVALENCE CLASS — surrogate captures
differ wildly (40% of R vs 76% of W vs +12% union), KLD identical;
only mixed-TYPE allocation escapes the class (beats all, t≥+2.3);
uniform rung sits below it (flagship beats Q3_K_S t=−2.85). The byte
budget, not the low-rank mechanism, sets quality. New paper claim
(ours): the equal-byte equivalence class, measured three ways in one
pre-registered afternoon. Deflated 55 GB intermediate retained for
now (rebuildable — delete when disk pressure).

### T1.8 RESULT 2026-08-11 16:38 — first task eval: the silent zone MEASURED
Full HellaSwag val (10042 tasks, identical set per config; all logs
verified 10042 rows; trio ran ~50 min total — the 1.5 h/config
estimate was 5× over, page cache again). Final acc: MIXEDfc 82.25%
[81.50, 82.99], Q3_K_S 82.39% [81.64, 83.13], Q3_K_M 82.85%
[82.10, 83.58]. REGISTERED RULES: (a) largest gap 0.60 pp < 1.4 pp
bar → no separation at the registered threshold; (b) ordering
consistent with KLD for the M pairs, 0.14 pp inversion for
flagship-vs-S deep inside noise → not rule (c).
POST-HOC (labeled; not pre-registered): per-task outcomes
reconstructed from the running-acc lines (exact — diffs all 0/1,
totals match), paired McNemar: flagship vs Q3_K_S z=−0.81 (TIE — at
ΔKLD≈0.005 the task eval is blind even paired at n=10042);
Q3_K_M vs flagship z=+3.48 and vs Q3_K_S z=+2.82 (SIGNIFICANT — at
ΔKLD≈0.027 tasks see the gap and AGREE with KLD's ordering).
VERDICT: the KLD silent zone is now a measured resolution floor
(somewhere in ΔKLD 0.005–0.027 at this scale/task), not an assertion;
where tasks resolve at all they corroborate KLD — the KLD-first
methodology survives its own audit. §6.1's "zero task evaluations"
sentence dies today (ABLATION-PLAN promise kept).

## 2026-09-08 — DOCTRINE (Max): zig is the loss-leader, not the loss

Zig is where our leaderboard bleeds (the entire 3.6→3.8 gap, 13–0
paired; 8+ of every run's legit fails) — and we keep it on the board
deliberately, because it is the most discriminating language we have
(29/31 units) and the leading indicator of the failure class that will
eventually hit every language: stdlib drift past training cutoffs.

The models are NOT incapable — solve is tied at 99.1 and every
zig-failed problem is solved in another language. They are suffocating
on stale API surface with no feedback. The strategy is to let them
BREATHE the way they were intended: LSP-equivalent harness improvements
(push-diagnostics Tier 1, shipped default-off pending A/B; awareness
packs Tier 3) that convert invisible staleness into visible, fixable
signal inside the agent loop. Zig losses today buy us the measurement
ground to prove the stack can hand capability back — the OpenBeast
thesis in miniature.

(Also fold this framing into the results PR docs + paper positioning.)

## 2026-09-09 ~01:00 — kernels rebased b10434 → b10865 (b10866 local)
Six hunks (see kernels/README.md in the main tree); the trap was
upstream's halve_iters template param shifting our lora instantiation
positions. Serving now = PRISTINE b10865 (kernel gate closed by
construction); research kernels isolated in build-research/.

### L6 DELIVERED 2026-09-09 — family subsumption note + draft integration
paper/theory-L6-family-subsumption.md: master objective Q(Delta; L_g,
L_x, F); ReQuant's dL score PROVEN exact coordinate descent on our
whitened quadratic (Claim 1, derivation); GSQ = same feasible set,
outcome objective, ours its Gauss-Newton surrogate — T1.17 measures the
gap (Claim 2); SchurQuant's Schur-complement machinery DERIVED as the
exact scale-block minimization of the same quadratic (Claim 3) — their
feasible set strictly contains ours, B1 narrowing applied with the
CODES-ONLY wording (recon's "scale+code" wording rejected: §2.3 freezes
grid bytes; the in-format scale-refit extension is derived and queued
as an option, not a claim); OBD-LLM L_g=I scoping + three-tier
one-sidedness defense (Claim 4: backprop-free workflow, E24 diagonal
no-op theorem, no evidence transfer from W-decomposition@8B); family
table + RCO complementarity anchor (Claim 5). DRAFT EDITS: §2.3
ReQuant/SchurQuant placement sentence; §2.2 OBD-LLM scoping; intro
claim (1) re-narrowed ("first one-shot whitened code re-optimization
on the shipped superblock hierarchy", class named); NEW §4.3b
equivalence-class section (E28/E29 into the draft at last, with AWSRC
scope sentence + RCO anchor). references.md +SchurQuant/AWSRC/RCO.
[EYEBALL] flags mark every agent-read fact needing PDF verification
before freeze: ReQuant H diag-vs-full + 1/2 factor + Alg schedule; GSQ
scale relaxation + exact loss; SchurQuant metric G + format; their
reconstruction-vs-metrics quote.

### L6 VERIFICATION PASS 2026-09-09 — PDFs pulled, three recon errors caught
All 7 load-bearing PDFs downloaded to prior-art/pdfs/. Page-level reads:
ReQuant (pp.3-6), SchurQuant (pp.3-6+grep), GSQ (pp.3-9+App.E/F),
OBD-LLM + AWSRC targeted greps. CORRECTIONS to the agent-read recon:
(1) SchurQuant's metric IS a full token-weighted Gram XΩXᵀ+λI — "not
whitened" was wrong; their Schur eliminates the CONTINUOUS SUFFIX
(chunk-block Babai generalization), not scales — scale refit is a
separate closed-form alternation over CONTINUOUS per-row-group scales.
(2) GSQ's objective is block-staged output RECONSTRUCTION (20 GPU
epochs, FineWeb-Edu, local-shift ±2 moves, group scales trained) — not
task loss; at layer granularity it IS our quadratic → T1.17 framing
corrected to search-and-granularity. (3) AWSRC's low-rank baseline is
ACTIVATION-WEIGHTED (rank-34 INT8, LQER-style) — not unwhitened.
CONFIRMED: ReQuant identity exact (full Gram, rank-one g refresh, no
½-factor issue; GPTAQ-asymmetric term = adoptable nuance); SchurQuant
+11.88pp optimizer-only; OBD-LLM 10%-mean-diag dampening verbatim +
bonus Fig.4 X-G correlation ≤0.1 (K-FAC support for E24/T1.12); GSQ
Q2_K 50.03→56.28 + token-reduction side effect (replicate in v5-fast
rows). Note, references, and draft sentences updated to verified
wording. Remaining eyeball: GSQ released code (sub-block scale-code
emission).

### RELATED WORK ASSEMBLED 2026-09-09 — draft/07-related-work.md
Nine-paragraph narrative from the curated bibliography + L6 placements +
claim ledger: correction lineage (what we add: same-pipeline ablations
on deployed formats + interpolation controls), rank-scales-with-width
(3 published legs + our honest two-point framing), frozen-grid
refinement family (GPTQ/ReQuant/SchurQuant/GSQ with L6's proven
placements + T1.17 hook), whitening ladder + one-sidedness defense,
equivalence class with E31 floor + RCO escape ("allocation is the only
lever that moves at equal bytes, and it can be optimized exactly"),
MoE, serving-as-claim (unfused-field evidence), upstream #8831 answer,
KLD-first measurement stance. Organization borrows 2507.17417's
taxonomy (credited in the header). All quotes are from the 09-09
PDF-verified set; E31 cited via its PREREG. OUTLINE note: draft now has
a §7; wire into any future section renumbering.

### E32/T1.17 OPENING ROWS 2026-09-10 ~12:50 — reference + GSQ-RCO measured
bf16ref38-40.logits generated (40ch, PPL 5.9246±0.141, 8s/pass partial
offload). First table rows (results-40ch.txt): GSQ-RCO IQ2_XS KLD
0.2028±0.0044 top-1 81.4% PPL 6.68 @8.4GB; GSQ-RCO IQ3_S KLD
0.0525±0.0012 top-1 90.1% PPL 6.03 @11.8GB. Next: (b) Gram capture on
3.8 (build-research), (c) shipped-baseline downloads from their card +
gguf-refine our arm, (d) paired per-chunk stats vs both.

### E32 STEP (b) COMPLETE 2026-09-10 ~14:20 — Qwen3.8 BF16 Grams banked
capture_gram38.sh: 257 Gram files, 31GB, data/gram38-bf16/ + grams.json
manifest + imatrix-38-bf16.gguf. 48 chunks wikitext-train, 110s/pass
partial offload. VALIDATION (manifest-driven): 5120-dim tensors exact
symmetry + PSD; ffn_down = 8×2176 blocks (17408-dim, blocked per the
>8192 convention), all blocks sym_rel=0, PSD; counts 24576 = 48×512.
First 3.8-family capture — includes ssm_out (GDN) tensors. NEXT (c):
pull ISTA card's named Unsloth baselines → gguf-refine our arm with
these Grams → (d) paired table.

### E32 BASELINE ROWS 2026-09-10 14:35 — SHIPPED BASELINES BEAT GSQ-RCO
### ON KLD (surrogate-vs-outcome, their side of the ledger)

All five rows measured vs bf16ref38-40 (results-40ch.txt): at BOTH byte
classes the PLAIN Unsloth baseline has lower KLD and higher top-1 than
the GSQ-RCO trained refinement (IQ2-class: UD-IQ2_S 0.1313/85.1% vs
GSQ-RCO 0.2028/81.4%; IQ3-class: UD-IQ3_S 0.0373/91.7% vs GSQ-RCO
0.0525/90.1%). Their card claims +10pt AIME over UD at the IQ2 rung —
so their refinement improves TASK metrics while DEGRADING
distribution-level fidelity on wikitext-KLD. Candidate explanations,
pre-registered before our arm runs: (a) calibration-distribution
mismatch (they train on FineWeb-Edu block-reconstruction; our
truth/eval is wikitext — their artifact may simply be tuned AWAY from
wikitext), (b) genuine task-overfit at the cost of general fidelity.
(a) is testable: measure their artifacts vs a FineWeb-Edu-chunk KLD
later. EITHER WAY: the head-to-head's KLD half now has a low bar —
our whitened wikitext-Gram refine starts from baselines that already
beat their arm — and the CAPABILITY half (v5-fast rows, step e)
becomes the decisive half, where their card's task wins live. Byte
caveat: UD files differ from GSQ-RCO by ±2-3% bytes; paired per-chunk
stats + byte columns required in the final table (PROTOCOL #4).

### E32/T1.17 KLD TABLE COMPLETE 2026-09-10 15:20 — THE HEAD-TO-HEAD
### VERDICT (paired per-chunk, 40 chunks, all contrasts 40/40)

FULL TABLE (results-40ch.txt; our rows +900,813,120 adapter bytes):
  IQ2 rung: GSQ-RCO 0.2028/81.4% · UD-IQ2_S bare 0.1313/85.1% ·
            OURS(+fc128) 0.1191/85.8% @9.27GB total
  Q2K rung: UD-Q2_K_XL bare 0.0854/87.3% · OURS 0.0800/88.0% @10.73GB
  IQ3 rung: GSQ-RCO 0.0525/90.1% · UD-IQ3_S bare 0.0373/91.7% ·
            OURS 0.0353/91.7% @12.94GB total

PAIRED VERDICTS (per-chunk t, shared eval tokens + reference):
1. **OURS BEATS GSQ-RCO AT EVERY RUNG, 40/40 chunks**: IQ2 t=−73.0
   (dKLD −0.0785), IQ3 t=−107.5 (−0.0180). One exact whitened CPU
   pass (~13 min/model) > 20 GPU-epochs of Gumbel-Softmax training —
   on wikitext-KLD. (Calibration-mismatch hypothesis for their arm
   stands as pre-registered; capability rows are their home turf.)
2. **The correction increment is real at every rung** vs its own bare
   baseline: t=−60.7 / −42.4 / −45.9, all 40/40 (dKLD −9%/−6%/−5.6%
   relative). Third architecture family (qwen35/3.8), same law.
3. **THE LADDER REMAINS UNBEATEN (E27's ghost, now cross-family)**:
   byte-fair check — bare UD-Q2_K_XL @9.83GB beats ours-IQ2S+adapter
   @9.27GB, t=−48.7, 40/40. Correction wins WITHIN a rung; pure-quant
   interpolation wins ACROSS rungs at equal bytes. Both refinement
   schools (theirs trained, ours closed-form) sit below the ladder
   line on this instrument; GSQ-RCO sits below its own unrefined
   baseline.
Positioning sentence for the paper: the head-to-head's KLD half is
won decisively AND the equal-byte doctrine survives a third family —
one-shot whitened correction is the best per-rung repair measured,
and repair itself remains dominated by allocation. NEXT: step (e)
capability rows (their card's task-win claims); FineWeb-KLD control
for the mismatch hypothesis; JOURNAL→MASTER-TABLE + paper §4.3
integration.

### E32 CAPABILITY ROW 1 2026-09-10 17:15 — GSQ-RCO IQ3_S CRATERS
36/112 passed, imputed 94.68 WITH 14 TRIPWIRE FAILURES (count_vowels
c/d/e, sigmoid b/c, unionfind d/e, det b/c, gemm_d, aes_c, priority_c,
race_condition, miller_rabin_f) — harness's own imputation-untrust
warning fired; true full-suite score materially below 94.68. Easy-unit
failures ≠ calibration-shift signature — weakens the pre-registered
charitable hypothesis (a). Reference frame: 3.8 Q5-class rows 82-84/112,
imputed 97.6-97.7. 14 tripwires >> churn floor (±1-2 tripwire flips).
"Task-lossless at IQ3_S" does not survive agentic-coding deployment on
this stack. Row 2 (UD-IQ3_S bare, same bits/base — their claimed +10
AIME victim) running since 17:16 → the paired verdict.

### E32 CAPABILITY ROWS 1-2 RETRACTED 2026-09-10 19:55 — ENVIRONMENTAL
### (CUDA launch-timeout crashes), chain halted

SANITY AUDIT after row 2's implausible 15/112: 84-87% of BOTH rows'
failures are ZERO-TOKEN (agents talking to a dead socket) and the
server log carries repeated crashes — `ggml-cuda.cu:108: CUDA error:
the launch timed out and was terminated` (watchdog kill mid-serve,
~98 min in, -np 6). Row-1 "GSQ craters 36/112" and row-2 "UD worse
15/112" are BOTH INVALID as capability measurements — retracted in
full. What survives: (1) the KLD table is UNAFFECTED (llama-perplexity
single-stream, zero incidents, sane PPLs); (2) a REAL finding of a
different kind: these IQ3-class artifacts (both GSQ-RCO and Unsloth
UD) are UNSTABLE under multi-slot serving on stock b10865 + RTX 5090
where our Q5-class models ran 14+ crash-free hours — an IQ-kernel/
GDN-arch stability issue worth a minimal repro + upstream report.
Cache: PR #35's zero-token guard prevented poisoning (nothing cached).
NEXT: (a) minimal stability repro (serve UD-IQ3_S, parallel load,
catch the timeout; vary -np 1, graph-opt off); (b) rerun capability
rows only under verified-stable serving; (c) never compare crashed
rows. Lesson re-learned at higher stakes: sanity-audit failure MODES
before believing failure COUNTS — the 0-token signature was one grep
away all along.

### 2026-09-11 10:19 — CAMPAIGN RELAUNCHED (Max: "go, launch the E32 chain
### and all our remaining tasks to complete in order")
Box rebooted overnight (/tmp logs gone). v2 capability chain relaunched
10:19 (np1, capped 20480, pure b10865): row 1 runs FULLY LIVE — the #56
pull rolled the harness context hash (a4bf66d1 → 1f3f60c1), orphaning
the 13 units banked 09-10 night. Cleaner: one era per row, no pooled
passes; cost ~40 min. Orchestrator scratch/post_e32.sh chains, in order:
FineWeb-KLD control → IQ3 capability verdict → IQ2 capability pair →
IQ2 verdict → IQ-instability repro (A: UD-IQ3_S np6; B: +GGML_CUDA_
DISABLE_GRAPHS=1; C: UD-Q5_K_XL np6 negative control) → greedy churn-
floor pair (langaware) → PPL@2048 sign check (stage B). Verdict script
e32_cap_verdict.py self-tested on the retracted 09-10 rows: flags both
INVALID by the zero-token audit (64/76, 85/97 zero-token fails) — the
sanity audit is now code, not a grep we might forget.

### 2026-09-11 10:50 — CORRECTION: the 09-10 15:20 paired t-statistics
### were INFLATED; verdicts stand, magnitudes and chunk-win counts repaired
Recomputed every pre-registered contrast with the campaign's canonical
tool (experiments/24-yaqa-lite/paired_stats.py, the R1-repaired per-chunk
differencing used for E27). The 15:20 entry's t values (−73.0, −107.5,
−60.7/−42.4/−45.9, −48.7) and "40/40 on every contrast" do not reproduce;
they appear to have divided per-chunk deltas by a per-TOKEN sem. Honest
numbers (KLD nats/tok, n=40, A−B, sem of per-chunk difference):
  ours-IQ2S+fc128 vs GSQ-RCO IQ2_XS: −0.0837 ± 0.0073, t=−11.5, 40/40;
    top-1 +4.44 pt, t=+10.9 (38/40)
  ours-IQ3S+fc128 vs GSQ-RCO IQ3_S:  −0.0173 ± 0.0018, t=−9.5, 38/40;
    top-1 +1.61 pt, t=+4.7 (30/40)
  GSQ-RCO IQ2_XS vs UD-IQ2_S bare:   +0.0715 ± 0.0061, t=+11.7, 0/40
  GSQ-RCO IQ3_S  vs UD-IQ3_S bare:   +0.0152 ± 0.0018, t=+8.6, 1/40
  increment IQ2S: −0.0122 ± 0.0019, t=−6.5, 39/40 (top-1 +0.74, t=+3.7)
  increment Q2KXL: −0.0055 ± 0.0008, t=−7.0, 34/40 (top-1 +0.71, t=+3.9)
  increment IQ3S: −0.0021 ± 0.0004, t=−5.9, 36/40 (top-1 +0.09, t=+0.5 TIE)
  ladder: ours-IQ2S+adapter (9.27 GB) vs bare UD-Q2_K_XL (9.83 GB):
    +0.0337 ± 0.0029, t=+11.6, 0/40 — ladder unbeaten, as before.
The dKLD for the IQ2 headline was also misquoted (−0.0785 → −0.0837).
WHAT STANDS: every direction, every verdict, all |t| ≥ 4.7 on KLD,
top-1 increment at IQ3 is a tie (was implied real). WHAT CHANGES: the
paper cites these numbers, not the 15:20 ones; the "40/40 every rung"
sentence becomes "40/40, 38/40". Filed under honor-the-fallen: the
inflated stats were one paired_stats.py call away — the same lesson as
the zero-token audit, and now both are scripts. paired-wikitext-40ch.txt
in the E32 dir is the canonical table from here on.

### 2026-09-11 11:00 — PRE-REGISTERED: FineWeb-KLD control + PPL@2048 sign check
(1) FineWeb-Edu control (hypothesis (a), calibration mismatch): 40 chunks
of FineWeb-Edu sample-10BT (first 400 docs, HF datasets-server pull, staged
as fineweb-edu-control.txt), fresh BF16 reference on the same text, full
8-row table + the 8 paired contrasts above. PREDICTIONS: if (a), GSQ-RCO's
deficit vs bare UD SHRINKS materially or FLIPS on FineWeb (t toward 0 or
negative) while our increments (wikitext-Gram-whitened) shrink too — the
mismatch cuts both ways; if (b) task-overfit, GSQ-RCO stays below bare UD
on FineWeb with t ≳ +4. Their own card shows fw-PPL GSQ-RCO IQ2_XS 9.19 vs
UD-IQ2_S 9.08 (UD better on FineWeb!) and IQ3_S 8.34 vs 8.34 (tie) — so
even on their calibration corpus their card does not claim a distribution-
level win; (a) would have to explain a KLD gap the card's PPL doesn't show.
(2) PPL SIGN DISCREPANCY: card wikitext PPL GSQ-RCO < UD (7.69<8.02,
7.07<7.16); our c=512 rows UD < GSQ-RCO (6.56<6.68, 6.006<6.027). Check at
-c 2048 (ppl2048_check.sh). If the sign flips with context, the head-to-
head must report both contexts; if not, the discrepancy is theirs to
explain (tokenizer/split/build) and we say so plainly.

### 2026-09-11 11:10 — GSQ RELEASED-CODE CHECK CLOSED: the public repo has
### NO GGUF path; sub-block scale emission is UNVERIFIABLE from released code
Delegated code review of github.com/IST-DASLab/GSQ @ 03fc164 (2026-07-05),
load-bearing claims re-verified by hand: (1) zero hits for gguf/ggml/
llama.cpp/llama-quantize in any .py/.yaml/.sh/.toml — only README.md
advertises the "Qwen3-8B GGUF K-Quant … projects the result back into the
same K-Quant format" workflow; no GGUF reader, writer, or scale-code
projector exists in the tree. (2) The quantizers TRAIN the scales as a
free fp32 nn.Parameter with its own optimizer group (src/trainer.py:69-75,
lr2) and emit them verbatim as float `weight_scale` (compressed-tensors
safetensors / Humming-MARLIN pack) — freely-trained scales cannot be
represented in K-/IQ-quant sub-block scale codes without a re-projection
the repo does not contain. (3) Every production config (qwen35/*, qwen3/*,
kimi-*) fails the repo's own loader (unknown keys masks_lr/signs_lr/
scales_lr vs lr1/lr2) — only configs/local/* load. (4) No 27B config; the
qwen35 configs calibrate on OpenThoughts-114k (4096 × 4096-token chunks),
not FineWeb-Edu. CONSEQUENCE FOR THE PAPER: the released GGUF artifacts we
measure were produced by an unreleased pipeline; L6 Claim 2's "codes
refined, format projection, scales frozen" reading of their K-quant path
is the README's word, not code we can cite. We scope accordingly:
"GSQ-RCO (as released)" is an artifact-level comparison; the mechanism
attribution (codes-only vs codes+scales) is UNKNOWN for the shipped
files. The 09-10 "they train on FineWeb-Edu block-reconstruction"
sentence is likewise unsupported by code — the card lists FineWeb-Edu as
an EVALUATION perplexity corpus; the calibration set of the 27B GGUF run
is undocumented (checking the card text now; imatrix on the card =
1000 × 4096-token chunks, corpus unstated). Hypothesis (a) is therefore
re-worded from "FineWeb-Edu mismatch" to "calibration-corpus mismatch
(corpus unknown; FineWeb-Edu is the control we can run because it is the
one non-wikitext corpus their card reports on)".

### 2026-09-11 15:10 — E33 / T1.10 CAPTURE-vs-WIDTH REGRESSION DONE (CPU):
### the r/d "rule" is killed as a form; the direction is upgraded to a fit
Delegated build, reviewed: pipeline validation is the load-bearing part —
recomputed spectra reproduce §4.2's 0.37 (0.3706) and 0.07 (0.074 on the
BF16-provenance E27 pair; the original legacy pair is pruned and the cache
lacks totals, so 0.07 itself is unrecoverable), and 1,620 logged captures
(E24/E32/E34) match per tensor to ≤0.01 — which also PROVES the E32
extraction logs are full-Gram whitened. Findings: linear c·(r/d) worst of
six forms everywhere; best two-exponent power law (diag r^0.61·d^−0.67,
fc r^0.44·d^−0.59; r and d not only via r/d); full-Gram whitening halves
the width penalty; no width-invariant head (h=0) — the constant-
cardinality-head mechanism cited in §4.2 is not supported here; prefactor
is per-(whitener, base, model) and two same-width same-arch 27B models sit
on different curves (base provenance confounded; a from-BF16 Q2_K of 3.8
with our imatrix would separate it — CPU-feasible, queued as an option).
HONEST SCOPE: one-step width leverage (1024→5120), tensor-bootstrap CIs,
held-out 27B off 0.6–1.6×; a third width is required before any exponent
is a law. Paper §4.2 rewritten to the fitted statement + scope; T1.10
closed in ABLATION-PLAN; Block 6 bullet. Bulk data (91 MB spectra with
exact totals + 26 MB points.csv) parked at data/spectra-e33/ outside git.
Operational: the batch contended with the live eval (decode 94→77 tok/s)
until reniced to 19 and capped at 8 threads — rule for future CPU-lane
jobs during sweeps: nice 19, ≤8 threads, from launch.

### 2026-09-11 17:40 — E33 ADDENDUM: the same-width "third model off the
### curve" finding is RETRACTED — it was the base recipe
Qwen3.8-27B re-quantized from BF16 with heretic's exact E27 recipe (one
Q2_K step, imatrix-38-bf16, blk.64 NextN pinned q5_k, 11.00 GB — same byte
class): 400 tensors, diag r64 0.074 vs heretic 0.074, fc r64 0.290 vs
0.284; 0 of 40 per-kind contrasts exclude zero; rank exponents identical
(0.75/0.40). The Unsloth UD lane (diag c 10.0, fc 11.5) was the outlier —
dynamic allocation + its own calibration imatrix inflate diag-visible
residual structure ~70% and halve fc-visible structure. §4.2 corrected
(commit follows): prefactor = f(whitener, base recipe, width); pooled fit
with the fixed-recipe lane primary: diag r^0.68·d^−0.76, fc r^0.37·d^−0.52;
held-out 27B 0.9–1.1×. Lesson (twice today): a same-day confound check
that costs 15 CPU-minutes is cheaper than a wrong sentence in the paper.
Artifact: weights/research-staging/e33/q38-27b-bf16-Q2_K.gguf (also a
clean from-BF16 3.8 base for future 3.8-lane work).

### 2026-09-14 11:00 — E16 RUNG 1 (E34, 0.8B) READ: the increment is real,
### the equal-byte law holds, and NVFP4 is the WORST 4-bit carrier here
Measured 09-11 14:02 (stage C, the only orchestrator that survived the
setsid fiasco); read three days later after the second unlogged box death.
40-chunk BF16-truth KLD, pure b10865, every base one quantize step from
the 0.8B BF16 with gram08b imatrix, blk.24 pinned q5_k, paired per-chunk
stats. Against the four pre-registered readings:
1. **Increment REAL:** NVFP4-all + fc-r128q8 vs NVFP4-all bare — KLD
   0.2073 → 0.1038 (dmean −0.1035, t=−32.0, 40/40), top-1 77.0 → 83.2 %
   (+6.2 pt, 40/40). Recovery fraction 50 %. The mechanism has still
   never failed to improve its own base — eleventh base format.
2. **Equal-byte law HOLDS (no first win):** composite 603 MB (516+87)
   at KLD 0.1038 loses to EVERY ladder rung at or below its bytes:
   Q5_K_S 578 MB 0.0155 (6.7× better), Q4_K_M 543 MB 0.0394 (t=+26.3,
   0/40), Q4_K_S 519 MB 0.0473 (t=+22.8, 0/40), IQ4_XS 518 MB 0.0503,
   even Q4_0 516 MB 0.0877. Same law as E27/E32, fourth time, fourth
   base family.
3. **NVFP4 under llama.cpp's quantizer is the worst 4-bit carrier on
   this GDN arch:** bare 0.2073 vs Q4_0 0.0877 / IQ4_XS 0.0503 / Q4_K_S
   0.0473 at 516-519 MB — 2.4× worse than the naive Q4_0, 5.3× worse
   than Q4_K_M, and worse than Q3_K_M (0.1663) at 36 MB FEWER bytes.
   The community #23853 ledger's 2× NVFP4-vs-Q4_K_M gap is a 5× gap at
   0.8B. Scope clause (pre-registered): llama-quantize has no
   NVFP4-specific imatrix path; this measures the shipped quantizer's
   NVFP4, not the format's ceiling (QAD-style retraining is NVIDIA's
   own recovery route, 2601.20088).
4. **Capture edge did NOT translate — surrogate-vs-outcome #7:** capture
   0.736 (NVFP4) vs 0.721 (Q3_K_M) predicted a larger NVFP4 increment;
   the Q3_K_M increment is larger both absolutely (−0.1169 vs −0.1035)
   and relatively (70 % vs 50 % recovery): q3km-fc128 0.0494 / 88.4 %
   vs nvfp4all-fc128 0.1038 / 83.2 %. Capture measures how much of the
   residual the factors see, not how much damage is left — a worse
   base with a marginally more structured residual still lands worse.
VERDICT: the flag ("first whitened low-rank correction on a frozen
NVFP4 LLM base") is claimed in the narrow sense — the datapoint exists,
ground-up provenance, increment 40/40 — and it is a LOSING datapoint
by the campaign's own law, exactly as the legacy 27B row already said
(E16-27B: NVFP4+fc 16.69 GB 0.0625 vs Q4_K_M 16.84 GB 0.0211,
Q6-referenced). E16 rung 2 (27B, heretic BF16 lane) is CLOSED without
running: it would re-measure a dominated composition at 100× the GPU
cost with a predictable outcome. Paper: one sentence in §4.3 (0.8B
paragraph); the §6 NVFP4 re-rounder CODEC sentence is a different
claim (free lever, zero bytes) and stands; the rollup's "best-correcting
base" label gets the 0.8B counter-reading. Files: experiments/34-e16-
nvfp4-08b/{results,paired}-e16-08b.txt (+ bytes.txt); logs in the
main-tree twin.

## 2026-10-09 — WRITE-UP: paper/final/ built; three E32 results journaled late

Max: button the research up and publish with the state of things as they
are. No new measurement was run. What changed in the record:

**E32 controls, landed 09-11..09-14, read today for the first time in this
journal** (files in experiments/32-t117-gsq-head-to-head/ of the main tree):
- FineWeb-Edu KLD control (paired-fineweb-40ch.txt). Pre-registered
  11:00 09-11: mismatch reading predicts GSQ-RCO's deficit vs bare UD
  shrinks toward 0; task-overfit reading predicts t ≳ +4. MEASURED: IQ2
  +0.0073 ± 0.0044 (t=+1.66, was +0.0715 t=+11.7 on wikitext); IQ3 +0.0033
  ± 0.0016 (t=+2.07, was +0.0152 t=+8.6). → the mismatch reading holds for
  their arm. The same registration predicted OUR increments would shrink
  too. They did not: IQ2_S −0.0103 (t=−9.40, 38/40), Q2_K_XL −0.0052
  (t=−9.20, 38/40), IQ3_S −0.0022 (t=−8.65, 36/40) — prediction wrong in
  our favor; a wikitext-Gram-whitened correction holds on FineWeb-Edu at
  27B. Ours vs GSQ-RCO: −0.0175 (t=−3.94, 35/40), −0.0055 (t=−3.39, 31/40).
  Ladder: bare UD-Q2_K_XL beats ours-IQ2S+adapter +0.0471 (t=+10.72, 1/40).
- PPL@2048 sign check (results-ppl2048.txt): the sign FLIPS with context.
  GSQ-RCO IQ2_XS 6.834 ± 0.043 vs UD-IQ2_S 7.119 ± 0.047; IQ3_S 6.306 ±
  0.039 vs 6.417 ± 0.041. The card's ordering reproduces at c=2048; ours at
  c=512 is the other way. Per the registration, both contexts are reported.
- IQ3 capability pair (capability-verdict-iq3-final.txt; re-audited clean
  09-29): GSQ-RCO IQ3_S 88/112, UD-IQ3_S 76/112, paired 16/4, net +12,
  p=0.012. The trained artifact beats its untrained baseline on the task
  suite and sits inside the Q5-class band (82-91).
- IQ2 capability pair: started 09-17 (stage F row A), campaign paused by
  Max the same evening, never completed. Our own arm: never run on the suite.

**Reading.** KLD@512 said GSQ-RCO < its own baseline; PPL@2048 and the
coding suite say the opposite. Surrogate-vs-outcome again, and this time
the surrogate is the instrument the whole campaign leans on. Within one
refinement family KLD agreed with HellaSwag where HellaSwag could see
anything (T1.8); across two families it did not rank. The paper says so in
the abstract, §4.3c, §4.6 (ledger row 7) and §6.1, and makes no capability
claim for our arm.

**Draft → final.** paper/draft/ is kept as the revision history (dated
editorial notes and all). paper/final/sections/ is the clean text; it also
absorbs results the draft had not: T1.3/T1.5 n=100 finals (§3.1, §4.3 —
"parity, not victory" is now "the rung is beaten, the interpolation line
is not"), T1.8 HellaSwag (§4.3), T1.11/T1.14 held-out (§3.1), T1.12
clean-Gram kron (§3.2), R8 kernel pair (§5.2). build.py turns [source: …]
into artifact tags (Appendix A) and arXiv ids into a numbered list, and
compiles paper.pdf with Typst. Two figures (figures.py), numbers copied
from results files.

**Not done, by decision:** the six open T1 rows and the items in TODO's
top block. The write-up states each beside the claim it limits.
