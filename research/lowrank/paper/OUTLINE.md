> ⚠ see review/ corrections 2026-08-04

# Paper outline (living document)

Working title (rev 2, post-2026-08-03): *The Empty Well: Measuring the
Boundary of Low-Rank Quantization-Error Correction at Scale* — the paper
is no longer "low-rank compresses LLMs" (it doesn't, and we can prove
exactly why and where); it is a capture-vs-width observation — NOT a
law (review 2026-08-04): ≈5.9·r/d fit on TWO diagonal r64 points
(0.6B/27B), and our own fc/NVFP4 captures put c at 9.6–20.8, so c is a
property of (whitener, base, arch); regression across the E21 sweep +
0.8B queued before any "law" wording — plus the
complete within-paradigm lever audit on a production 27B (allocation:
helps; Q8 factors: free 2×; columns/shared-basis/energy-greedy: nulls),
the paradigm-boundary result (diagonal-whitened correction plateaus at
KLD ≈ 0.14 vs same-bytes Q3_K_M 0.0555, budget-saturated), and the
serving-economics inversion (less VRAM = faster decode, measured at
every point). Contributions 2-4 ride on a stock-llama.cpp SERVING path
only — capture (Gram patch), grad Grams, and near-tax-free decode all
require our local patches; "zero code changes" was true of E04's
diagonal v0 and died the night E10 was built (review F16). Full-
covariance results (next session) decide whether the paper ends at the
boundary or crosses it.

Rev 4 (night campaign 08-03/04): title candidates now include *Correct
Where Geometry Favors It: the r/d Law and the Limits of Low-Rank
Quantization Correction* — the story is (1) the capture-vs-width
observation (two points, see above), (2) the full lever audit
(metric/co-design/density/allocation/structure, each with numbers),
(3) the geometry rule with its existence evidence — corrected per
review 2026-08-04: MIXED+fc reaches ~1σ PARITY with Q3_K_S at 27B on
file bytes (loses to Q3_K_M at equal VRAM; serving-tax unpriced on
stock llama.cpp), and the fc stack's 0.6B result is a win-from-below
at +4–10% bytes, under the ladder's own interpolation line (no
mixed-quant control built) — (4) the honest frontier: I-quant
codebooks ≈ corrected-Q2 at equal tensors — parity as a finding. New
figures: F8 capture-vs-r/d (two scales, two POINTS — fit the E21
sweep + 0.8B before drawing a line); F9 the 27B Pareto scatter
(ladder + our configs, interpolation line drawn); F10 per-kind
localization (participation ratios). Methods additions: Gram-capture
instrumentation, PD-safe whitening, alternation protocol, mixed-base
recipe.

Rev 5 (recon 2026-08-11, `../prior-art/recon-2026-08-11.md`) — the
claim ledger after the post-08-04 literature sweep; apply before
freezing any section:
1. **gguf-refine**: "first to improve shipped GGUFs in-format" is DEAD
   (GSQ 2604.18556, verified from PDF, public code; ReQuant
   2608.07019). Reposition as the cheap deterministic point on the
   GSQ curve — one-shot, backprop-free, imatrix-whitened,
   sub-block-scale-aware, paired-KLD at two scales — and add the GSQ
   head-to-head on the same Unsloth Qwen3-8B Q2_K artifact as a
   REQUIRED evaluation row (ABLATION-PLAN T1.17).
2. **§2 gains the 2026 wave**: GSQ/ReQuant (frozen-grid); OBD-LLM +
   KronQ (two-sided metric — M3 reframed as *independent
   confirmation, first in GGUF K-quant residual correction with
   byte-fair interpolation controls*); TwinQuant + SVDQuant/Nunchaku
   + LoRaQ (quant+low-rank compositions — E16 stated as *first LLM
   instance, whitened residual correction of a frozen NVFP4 base,
   stock adapter serving*); SRR (rank-budget split — must-cite);
   SERQ; BaKron; DuQuant++ (FP4+transform contrast). One-line roles
   in references.md "Recon 2026-08-11 additions".
3. **Theory section**: check DAM's (2607.20434) non-orthogonality
   theorem against the 27B parity data before writing; import
   Bid-Up's (2606.01412) monotone-descent bounds for the E11
   convergence note; KronQ Prop 1 (HG cancels in the column update)
   is published support for "estimator quality gates metric
   sophistication" (E24/M1).
4. **Capture-vs-width (§4.2/F8)**: cite TwinQuant Fig. 1 as the
   published unwhitened cousin; ours is the first systematic
   whitened measurement (verified unscooped).
5. **Kernel section**: must-cite TwinQuant's fused dual-branch
   epilogue and Nunchaku's branch-fusion trick; the register-neutral
   occupancy law remains unclaimed — state it as the contribution.
6. **Measurement hygiene**: no new speed number ships until the
   llama.cpp pin is checked against #26177 (--fit/NextN miscount);
   the −13.4% MTP figure may be two bugs, not one.

Rev 3 addition (E09): the boundary is a CURVE, not a point — at 27B the
correction's recovery fraction rises monotonically as the base
approaches its cliff (7.5% of KLD damage at Q2_K → 18% at IQ2_XS, both
diagonal-whitened; the method-consistent fc curve is 20% → 28% → 41% —
plot THAT as F7, review F5/F7) yet never jumps a ladder rung under
diagonal whitening. New figure F7: recovery fraction vs base bpw,
single-estimator series. Upstream findings, reframed (review F10/F11):
independently found, already reported upstream (MTP/NextN invisible to
llama-imatrix + IQ2-class hard-block: open PRs #23476/#23575; validated
workaround `--tensor-type "blk\.64\.=q5_k"` goes there as a comment).
Drafts in `../upstream/` are annotated accordingly — comments, not new
issues.

## Skeleton

1. **Introduction** — the compute-rich / memory-poor economics of consumer
   GPUs (Max's framing, README top): powerful processors (3090 Ti, 5090)
   strapped to undersized VRAM; if frontier models fit that VRAM, idle
   compute absorbs reconstruction overhead in a fused kernel. Thesis:
   memory-for-accuracy (not memory-for-time — see §3 inversion) trade with
   a standardized decomposed format servable by mainline llama.cpp; the
   bandwidth-bound decode measurement makes compute the free resource.
2. **Background & prior art** — SVD family (ASVD, SVD-LLM), quant+low-rank
   hybrids (CALDERA, EoRA, SVDQuant), llama.cpp quant ecosystem + LoRA
   machinery, plus the 2026 wave per rev 5 (GSQ/ReQuant frozen-grid,
   OBD-LLM/KronQ two-sided, TwinQuant/SRR/SERQ/BaKron). ← feeds from
   `prior-art/` (start: `recon-2026-08-11.md`).
3. **Analysis** — factorized-serving cost model: FLOPs/bytes vs rank; the
   premise inversion (decode is bandwidth-bound → factorization speeds it
   up); why accuracy is the binding constraint. Spectrum census results
   (E01) as the empirical foundation.
4. **Method** — the transform (quant base + weighted low-rank residual,
   per-layer rank allocation), the format, the CUDA serving path.
5. **Implementation** — v0 (adapter-path, zero-change), v1 (fused kernel),
   integration with GGUF; what upstreaming required.
6. **Evaluation** — VRAM/perplexity/throughput triangle across 0.6B→27B;
   ablations (plain SVD null, weighting on/off, rank allocation on/off);
   comparison against same-VRAM pure quants (the honest baseline: does
   IQ2+LoRA beat IQ3 at equal bytes? if not, the method is decoration).
7. **Negative results & limitations** — every wall we hit, named.
8. **Related repo work** — MTP interaction, serving-stack integration.

## Figures planned (reconciled 2026-08-04 per round-2 paper review
## §1.3: F4 was orphaned in the draft — now referenced in §4.3 as the
## mission-axis Pareto and MUST be drawn; §3.1's summary was
## double-booked as "F3" — renamed Table 2; §5.4's serving-tax ledger
## was mis-cited as [F5, F6] — now F11)
- F1: pipeline diagram (GGUF → transform → decomposed → CUDA serve).
- F2: singular-value spectra by projection type (E01).
- F3: residual-energy vs rank, base-quant sweep (E03) — used in §4.1.
- F4: bytes/VRAM vs quality Pareto — ladder + interpolation control +
  corrected configs (E27 data) — referenced in §4.3; REQUIRED.
- F5: tok/s vs rank (the bandwidth argument, measured).
- F6: fused-kernel schematic (E06 trilogy).
- F7: recovery fraction vs base bpw, fc-consistent series (rev 3).
- F8: capture vs r/d, two POINTS (rev 4).
- F9: 27B Pareto scatter with interpolation line (rev 4).
- F10: per-kind localization / participation ratios (rev 4).
- F11: serving-tax ledger (per-phase tax table, §5.4).
- Table 2: free-lever three-scale summary (§3.1; formerly mislabeled
  "F3 companion table").

## References
Accumulate in `references.md` with one-line "why cited" annotations as we
read, never retroactively.

## The bar for every claim
Reproducible command + pinned corpus + pinned build SHA in the experiment
README it came from.
