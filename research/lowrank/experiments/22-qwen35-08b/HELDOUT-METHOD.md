# HELDOUT-METHOD — exact construction of the R7 held-out / mixed-calibration arc

Written 2026-08-04 as the round-2 repair of adversarial-round2-experiments
F1b: "the resolution's method exists nowhere." This file documents the
mixed-corpus construction exactly, names what each artifact is, and states
what the record can and cannot verify. Raw numbers: `heldout-results.txt`
(unchanged). Companion caveat register: paper/DEPLOYABLE-WINS.md
§"MITIGATION INDICATED".

## Corpora

- **Calibration (wiki)**: `data/wikitext-2-raw/wiki.train.raw` — the
  campaign's standard calibration corpus.
- **Calibration (code)**: `experiments/17-task-conditioned/code-train.txt`
  (3.0 MB, drawn from our own repos — see E14-cond README).
- **Held-out eval (code)**: `experiments/17-task-conditioned/code-test.txt`
  (0.4 MB, DISJOINT from code-train by E14-cond's construction). This is
  the corpus on which wiki-calibrated RR scored 3.470 vs bare 3.083.
- **On-distribution eval**: wikitext-2-test (full corpus, 580×512).

## The three calibration runs, in order

1. **Wiki-only (the published free-lever row).** Grams `data/gram08b`
   (40 chunks, count = 20480 tokens). RR artifact: `q35-08b-Q2K-rr-input
   .gguf` (E24). Result: wiki 28.09, held-out code **3.470 — WORSE than
   bare 3.083**. The ⚠ held-out finding.

2. **Attempt 1 — "mixed", sequential concatenation: BUG.** The mixed
   corpus was built by concatenating wiki.train followed by code-train
   as one file. llama-imatrix consumes chunks sequentially from the
   front, so the 48 captured chunks never reached the appended code
   text — the "mixed" calibration was **100% wiki**. Artifact:
   `q35-08b-Q2K-rr-mixed.gguf`; result line 6 of heldout-results.txt
   (wiki 27.5384, code 3.4278). INVALID as a mixed-calibration test —
   but retained as the **accidental sampling-alone control**: same
   corpus, different chunking/budget moved wiki-RR by 0.55 PPL
   (27.54 vs 28.09), the campaign's only measurement of
   calibration-sampling variance (F12a axis; formalized as
   ABLATION-PLAN T1.15). Bug caught by the code-side number not
   moving (3.428 ≈ 3.470).

3. **Attempt 2 — TRUE interleave, 2:1 wiki:code.** Reconstructed as a
   script after the fact (F1b repair): `build_mixed_calib.sh` builds
   `data/mixed-calib2.txt` (3,000,000 bytes; sha256 28f156fe…) by
   interleaving 4000-byte wiki.train slices with 2000-byte code-train
   slices — 2 MB wiki + 1 MB code, so every capture chunk window mixes
   both corpora. Grams: `data/gram08b-mixed` (48 chunks, count = 24576
   tokens per grams.json). RR artifact: `q35-08b-Q2K-rr-mixed2.gguf`.
   Result: wiki 29.8420, code **2.7852** — beats bare on BOTH corpora
   (line 7 of heldout-results.txt).

## Held-out integrity

The calibration inputs are wiki-TRAIN and code-TRAIN only; the eval
corpus code-TEST is never read by the build script — the held-out
corpus provably stays held out of the run that mitigated the held-out
finding (this was the review's disqualifying "cannot be verified"
gap; the script closes it going forward).

**Honesty note on reconstruction**: the original attempt-2 corpus was
assembled unscripted in-session; `build_mixed_calib.sh` is the
after-the-fact scripted reconstruction of the same recipe (same
sources, same 2:1 interleave, byte count and gram08b-mixed's
count=24576 consistent). Byte-identity of the original unscripted
corpus to the script's output cannot be proven from the retained
record — a rebuild of gram08b-mixed + RR-mixed2 from the scripted
corpus is the cheap verification (~minutes at 0.8B) and rides
ABLATION-PLAN T1.14's KLD rerun.

## Known gaps (queued, not hidden)

- `data/gram08b-mixed/grams.json` carries counts but no provenance
  fingerprint (PROTOCOL rule 2 violated at capture time; noted in
  TODO repairs queue).
- Mixed-vs-wiki-only confounds corpus composition + budget (+20%) +
  chunk sampling (F1a). Equal-budget grid: ABLATION-PLAN T1.1.
- RR-mixed2 is PPL-only (no KLD/top-1): T1.14.
- Both eval corpora are in-coverage post-mix; third-corpus
  extrapolation test: T1.11.
