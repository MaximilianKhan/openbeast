# What we can contribute — plan (2026-09-08, Max's directive)

Four contribution lanes, ordered by readiness. Each names the artifact,
the audience, and the gate.

## C1 — llama.cpp PR: imatrix blind-spot fixes (READY TO BUILD)
The offer is already public (our #23575 comment, posted today):
(1) quantize-side fallback — higher-k-quant + warning for
imatrix-less NextN/draft tensors, mirroring the existing token-embd
special-casing; (2) an imatrix end-of-run warning listing tensors that
received zero activations (catches ANY never-executed branch, not just
MTP). Small, evidenced, maintainers already hit the pain (#26903).
**Gate:** build on the clean post-b10829 serving build (see cleanup
plan) so the PR is from pristine upstream, not our kernel branch.
Effort ~1 day incl. tests. Highest goodwill-per-hour on the board.

## C2 — Independent low-bit benchmark rows (UNIQUE INSTRUMENT)
Nobody publishes behavioral capability for shipped low-bit artifacts
(industry guide: "NVFP4-vs-Q4_K_M is unsettled — no independent
benchmark has run it"). We have v5-fast: 291-unit-suite capability,
imputation-exact on the v4 scale, ~0.85h/row on consumer hardware.
Contribute: a measured table of {Unsloth-Dynamic, GSQ-RCO, NVFP4,
our-refined} Qwen3.8-27B artifacts — KLD + capability + tok/s on a
5090. Publish in docs/RESULTS.md + a public-facing writeup.
**Gate:** T1.17 run (post-sweep GPU). This doubles as the paper's
benchmark section AND an OpenBeast credibility artifact.

## C3 — gguf-refine tool release (PRODUCT-GRADE, GATED)
If T1.17 shows our one-shot whitened refine dominates the
deployed-practice baseline (scale-only community tools) and holds
against GSQ-RCO at consumer cost: release as an OpenBeast tool —
"improve the GGUF you already downloaded, on the card you serve it
from." **Gate:** T1.17 verdict + the B1 claim rewording. This is the
consumer-hardware asymmetry made into a product.

## C4 — The paper (THE LONG GAME)
Remaining evidence gaps: T1.17 table, E16 rungs, L6 subsumption math,
RCO (2605.00649) + DAM + OBD-LLM reads folded into related work.
E30/E31 slot in as the Muon negative-control and the seeded-basis
rebuttal. Positioning: the spare-memory meta framing + the
consumer-hardware asymmetry, both still unclaimed.

## Sequencing
C1 immediately post-rebase (independent of everything).
C2 = T1.17 (next GPU day). C3 gated on C2. C4 rolls continuously.
