# Upstream Landscape: Low-Rank-Decomposed Weight Serving in llama.cpp

Research date: 2026-08-03. Scope: ggml-org/llama.cpp + ikawrakow/ik_llama.cpp prior art on
quantized-base + low-rank-residual serving (LQER/EoRA/CALDERA/SVDQuant family), the LoRA
inference surface we would build on, contribution-process ground truth, and upstream appetite
for VRAM reduction beyond plain quantization. All claims sourced from GitHub via `gh` API and
web fetch; links and dates inline.

---

## 1. Prior attempts at low-rank residual correction

### 1.1 The canonical thread: Discussion #8831 — "Using LQER to improve low-bit quants"

**Link:** https://github.com/ggml-org/llama.cpp/discussions/8831 (compilade, 2024-08-02)

This is *the* upstream prior-art thread. compilade (core maintainer, quant/gguf-py owner)
proposed implementing LQER / L²QER (arXiv:2402.02446) on top of ngxson's then-fresh LoRA
refactor (#8332): quantize the base, SVD the quantization error into a LoRA adapter, serve
base + adapter. His stated needs map exactly onto our project:

1. A script producing a LoRA from the difference of two GGUF files (full-precision minus
   quantized), needing Python dequant functions (he refactored `gguf-py/gguf/quants.py` for
   this) or a C++ SVD.
2. A way to **store the LoRA adapter inside the same GGUF as the dense weights**, plus
   metadata signaling "this model file carries a built-in corrective adapter" so the loader
   auto-applies it.
3. Observation that **L²QER's activation scaling ≈ what `imatrix` already collects** — the
   existing imatrix files could supply the activation statistics.

Maintainer reactions in-thread:

- **ngxson** (2024-08-02/03): engaged and constructive. Flagged concrete plumbing gaps:
  `llama_lora_adapter_init` did not mmap, LoRA did not work with sharded/split models, and
  suggested moving gguf-reader logic into ggml for reuse. Interested in C++ SVD (also wanted
  it for control vectors, which used PCA). Noted ggml has an old ARM-only `test-svd0.c`
  using LAPACK's `sgesvd_`.
- **ggerganov** (2024-08-04): "Interesting work, will be cool to try to implement this
  approach and see how the perplexity improves for different ranks." Also questioned the
  paper's Appendix B (L²QER appearing *worse* than LQER in the plot while the text claims
  the opposite). Signal: open to it, but expects perplexity-vs-rank curves.
- **jukofyork** (2024-08-03 and 2025-02-14/15): the empirical heavy. Pointed at his
  mergekit LoRA-extraction work (https://github.com/arcee-ai/mergekit/pull/333, based on
  thomasgauthier/LoRD) and warned that ggml's biggest gap is **the lack of a robust linear
  algebra library** — "you can knock up some gash SVD code in a few days" but it won't match
  GSL/LAPACK quality, and GSL is GPL-3 (dependency problem; llama.cpp forbids third-party
  deps).

**jukofyork's negative result (2025-02-14/15, in-thread, run during MLA work on
[PR #11446](https://github.com/ggml-org/llama.cpp/pull/11446)):** truncated-SVD of Q4_0
round-trip residuals on DeepSeek-V3 MoE expert tensors:

- Rank-256 (≈16% size overhead): only ~25% variance explained (late layers), ~26–39% (layer 3).
- Rank-64 (≈4% overhead): ~7% variance explained late, ~7.6–15% early.
- Verdict: *"just adding an extra bit to the quant would give far more improvement than LQER
  with all the extra overhead it adds… Overall LQER seems a waste of time (can't comment on
  L²QER though)."*
- One salvageable insight: early layers have far steeper singular-value decay → useful for
  choosing which layers to "bump" in dynamic quants.

Note carefully what was and wasn't tested: **plain SVD of the raw error** (LQER), on MoE
expert tensors of DeepSeek-V3, at 4-bit. **Not** activation-weighted variants (L²QER, EoRA's
eigenspace projection, CALDERA's calibrated alternation), **not** 2–3-bit bases where the
error is much larger and lower-rank-structured, and not attention tensors. He explicitly
withheld judgment on L²QER.

### 1.2 ikawrakow's verdict: ik_llama.cpp Discussion #15 — "Will LQER improve k- and i-quants?"

**Link:** https://github.com/ikawrakow/ik_llama.cpp/discussions/15 (ikawrakow, 2024-08-09)

ikawrakow (author of llama.cpp's k-quants and IQ quants) opened this in direct response to
#8831 with a public prediction: **"LQER/L²QER will not help to improve any of the k- or
i-quants in llama.cpp."** His evidence:

- Perplexity-delta table: LQER's published ΔPPL on LLaMA-v1-7B ≈ 0.220 vs IQ4_K ≈ 0.041 at
  comparable size — his quants are ~5x better than what the paper improved upon, i.e. the
  paper's baseline (round-to-nearest Q4) was weak.
- He had **already tried SVD-before-quantization** himself — an exploratory `ik/try_svd`
  branch — finding benefit only on specific tensors in early layers (consistent with
  jukofyork's later variance-decay observation), and "minimal benefit when quantization
  quality is already good."
- compilade acknowledged the skepticism but kept interest in variants (low-rank decompose
  *before* quantization with LoRA error recovery; asymmetric quant types possibly
  benefiting more). OpenELM-270M test showed limited improvement.
- Note: ikawrakow said (July 2025) he no longer actively uses llama.cpp; his current work is
  ik_llama.cpp only.

**ikawrakow's actual chosen direction for accuracy-per-bit:** trellis quants (QTIP-style,
`IQ2_KT`/`IQ3_KT`/`IQ4_KT`) and Hadamard transforms for KV cache
([ik PR #1033](https://github.com/ikawrakow/ik_llama.cpp/pull/1033)) — i.e. better
*codebooks/rotations*, not low-rank side-cars. Relevant recent datapoint:
[ik Discussion #2213](https://github.com/ikawrakow/ik_llama.cpp/discussions/2213)
(2026-07-30) where ikawrakow states **"the advantage of trellis quants decreases with
bits-per-weight"** — 2-bit is where fancy machinery pays; at 4-bit it's a wash. That scaling
logic applies to low-rank residuals too and tells us where to aim (2–3 bpw).

### 1.3 Other adjacent traces in ggml-org/llama.cpp

- [Issue #4611](https://github.com/ggml-org/llama.cpp/issues/4611) (kalomaze, 2023-12-23,
  closed): "Mixtral experts are initialized from Mistral 7b — low rank conversion
  possible?" — early musing about storing experts as base + low-rank deltas; never
  implemented.
- [Issue #4176](https://github.com/ggml-org/llama.cpp/issues/4176) (2023-11-22, closed):
  dynamic tile quantization + fine-tune vector approximation; no traction.
- [PR #8151](https://github.com/ggml-org/llama.cpp/pull/8151) (compilade, ternary
  TQ1_0/TQ2_0 for BitNet/TriLMs, merged 2024): keyword-adjacent (mentions LQER); shows what
  a successful new-quant-type PR looks like (see §3).
- Searches for **CALDERA, EoRA, SVDQuant** in ggml-org/llama.cpp issues/PRs/discussions:
  **zero hits** (2026-08-03). Nobody upstream has proposed the modern calibrated low-rank
  correction family by name. The field is empty — the only prior art is 2024-era plain LQER
  plus the negative experiments above.

### 1.4 Cautionary parallel: the TurboQuant affair (2026-03)

Not low-rank, but the closest recent "paper-to-llama.cpp compression feature" saga and a
maintainer-attitude X-ray:

- [Issue #20977](https://github.com/ggml-org/llama.cpp/issues/20977) (mudler, 2026-03-25,
  open): TurboQuant KV-cache compression feature request. Thread devolved into hype,
  third-party forks, and "is this a race?" noise; core maintainer engagement limited to
  am17an asking for a reference implementation.
- [Discussion #20969](https://github.com/ggml-org/llama.cpp/discussions/20969) (2026-03-25).
- [ik issue #1509](https://github.com/ikawrakow/ik_llama.cpp/issues/1509) (closed): an
  AI-generated "Working Implementation Ready for Review" that wasn't wired into the build.
  ikawrakow's response is the template for what he/llama.cpp maintainers demand: *"it needs
  to be integrated so one can compare quality and performance against the existing
  quantization types. RMSE is not a very [good metric]"* — and *"ik_llama.cpp can use
  Hadamard transforms for quantized KV cache… we need to see if this is better than that."*
  **Lesson: standalone kernels + MSE numbers = rejected. Integrated PPL/KL-div + tok/s vs
  the incumbent = the only currency.**

---

## 2. The LoRA surface upstream

### 2.1 Current state

The foundation is ngxson's **[PR #8332 "Refactor lora adapter support"](https://github.com/ggml-org/llama.cpp/pull/8332)**
(merged 2024-07-15):

- LoRA adapters are their own GGUF files; applied **at runtime as extra mul_mats**
  (`W·x + scale·B·(A·x)`) — the quantized base is *never* modified or merged. This is
  exactly the execution model low-rank residual serving needs.
- API (since renamed `llama_adapter_lora_*`): `init` (load, tied to model lifetime),
  `set(ctx, adapter, scale)`, `remove`, `free` → **hot-swappable per context, scalable
  per request**. llama-server exposes `/lora-adapters` and per-request `lora` fields.
- Covered target modules: q/k/v/o proj, gate/up/down (incl. MoE), lm_head, MoE router.
- `convert_lora_to_gguf.py` converts PEFT adapters.

### 2.2 Known limitations and live bugs (the terrain we'd inherit)

- **Prompt-cache contamination (open, serious):**
  [#26207](https://github.com/ggml-org/llama.cpp/issues/26207) (2026-07-28) — server reuses
  prompt cache across requests with different per-request `lora`; output silently
  contaminated by the previous adapter. (Less relevant for a *static* corrective adapter,
  but shows the per-request path is undertested.)
- **Multiple-adapter allocator crashes:** GGML_ASSERT failures with >1 adapter —
  [#18050](https://github.com/ggml-org/llama.cpp/issues/18050),
  [#18466](https://github.com/ggml-org/llama.cpp/issues/18466),
  [#18193](https://github.com/ggml-org/llama.cpp/issues/18193) (all closed/fixed Dec 2025);
  graph-size asserts [#16555](https://github.com/ggml-org/llama.cpp/issues/16555) (closed),
  [#16475](https://github.com/ggml-org/llama.cpp/issues/16475) (open, Mac).
- **Adapter-scale changes leaked memory / infinite graph rebuild:**
  [#19217](https://github.com/ggml-org/llama.cpp/issues/19217),
  [#21552](https://github.com/ggml-org/llama.cpp/issues/21552) (both closed 2026). Changing
  LoRA state forces graph rebuilds — a static built-in adapter avoids this class entirely.
- **Convert-script fragility:** a long tail of `convert_lora_to_gguf.py` breakages per new
  arch (GLM-OCR [#24101](https://github.com/ggml-org/llama.cpp/issues/24101), Gemma 4
  [#23047](https://github.com/ggml-org/llama.cpp/issues/23047), Qwen3.5
  [#21125](https://github.com/ggml-org/llama.cpp/issues/21125) open, GraniteMoe
  [#21864](https://github.com/ggml-org/llama.cpp/issues/21864), MLA loras broken for
  glm-4.7-flash [#20058](https://github.com/ggml-org/llama.cpp/issues/20058)). Note
  `--outtype` was historically ignored
  ([#10671](https://github.com/ggml-org/llama.cpp/issues/10671),
  [#17447](https://github.com/ggml-org/llama.cpp/issues/17447),
  [#15890](https://github.com/ggml-org/llama.cpp/issues/15890) by jukofyork) — adapters
  effectively ship F16/F32; quantized adapters are not a first-class path.
- **Rejected/expired extensions:** DoRA
  ([#6536](https://github.com/ggml-org/llama.cpp/issues/6536), closed unimplemented),
  Activated LoRA ([#15212](https://github.com/ggml-org/llama.cpp/issues/15212), closed),
  per-model LoRA config mapping ([#11031](https://github.com/ggml-org/llama.cpp/issues/11031),
  ngxson's own, closed).
- **Speculative decoding:** no explicit lora+speculative issue found; the surface is simply
  untested there (draft and target adapters are independent). Not a blocker for a
  correction adapter (draft model doesn't need it).
- **Open maintainer interest:** JohannesGaessler has an open "LoRA training example" issue
  ([#13485](https://github.com/ggml-org/llama.cpp/issues/13485), 2025-05-12) — he owns
  training + CUDA; the LoRA runtime path matters to him.
- **No open redesign plan** for the adapter subsystem was found; #8332's architecture still
  stands. compilade's #8831 item 2 (adapter embedded in the model GGUF + auto-apply
  metadata) remains **unbuilt and unclaimed**.

### 2.3 CUDA reality

LoRA mul_mats run as separate ops on whatever backend holds the tensors; there is no fused
"quantized-GEMM + low-rank epilogue" kernel upstream. For a rank-64 residual at batch-1
decode this is two skinny GEMVs per corrected tensor — cheap but not free (this cost is why
benchmarks will be demanded; cf. ancient [#956](https://github.com/ggml-org/llama.cpp/issues/956)
on tall-skinny GEMM being pathological). Fusion would be a follow-up CUDA PR, Johannes
Gaessler's turf (he actively lands MMQ/MMVQ fusion work, e.g.
[#24481](https://github.com/ggml-org/llama.cpp/pull/24481) NVFP4 MMVQ post-scale fusion).

---

## 3. What gets merged: process ground truth (2026)

### 3.1 CONTRIBUTING.md rules (verbatim-relevant)

**Link:** https://github.com/ggml-org/llama.cpp/blob/master/CONTRIBUTING.md and
https://github.com/ggml-org/llama.cpp/blob/master/AGENTS.md

- **"Features must begin with an issue, not a PR — let interest accumulate before writing
  code."** Duplicates closed without questions. Niche features may only land as an
  example/tool, or stay on a private fork.
- **New CLI flags / public API additions carry a higher bar** — must justify why existing
  mechanisms don't suffice.
- **CPU-first**: initial PR should be CPU-only; CUDA and other backends in follow-up PRs.
- **New ggml_type = disproportionate maintenance burden.** Minimum extra criteria to add a
  quant type: (1) convert a small model and upload to HuggingFace, (2) perplexity vs
  FP16/BF16 *and* vs similar-size types, (3) **KL-divergence data** vs native precision for
  the new type and similar-size types, (4) llama-bench performance vs similar-size types on
  pure CPU.
- Run full CI locally; `test-backend-ops` for any ggml operator changes (needs 2 backends);
  verify perplexity and performance unaffected.
- No third-party dependencies. Simple C++, no fancy STL, no templates. (This kills any
  GSL/LAPACK/Eigen SVD dependency — SVD must live in Python tooling or be hand-rolled.)
- New contributors: max 1 open PR, no trivial fixes.
- Maintainers may close any PR that "doesn't fit the existing architecture, or is too
  complex to justify its benefit." Squash-merge; "merge ready" label fast-path exists
  ([#26178](https://github.com/ggml-org/llama.cpp/pull/26178)).
- **AI policy (strict, enforced by bot):** AI-generated code allowed *only* with explicit
  disclosure in the PR template; you must be able to explain every line without AI help;
  **AI-written PR descriptions, commit messages, issues, and reviewer responses are
  prohibited** and undisclosed AI use can mean a permanent ban.

### 3.2 The failure mode, live

**[PR #25662 "Infcore"](https://github.com/ggml-org/llama.cpp/pull/25662)** (2026-07-14):
opened 14:02, closed 14:04 — **two minutes** — by `ggml-gh-bot`, an automated PR checker
that flagged: PR template not respected, AI-generated content detected, and "Large PR:
large changes require prior discussion." There is now a machine gate in front of human
review. The ik_llama.cpp TurboQuant issue #1509 (§1.4) is the same failure mode in the
fork: unintegrated code + no comparative benchmarks = closed.

### 3.3 Success patterns (how big features actually landed)

- **DeepSeek MLA — the model to copy.** fairydreaming's optimized implementation
  [PR #11446](https://github.com/ggml-org/llama.cpp/pull/11446) (2025-01-27) proved the
  approach with benchmarks but was superseded; jukofyork carried it to merge as
  [PR #12801](https://github.com/ggml-org/llama.cpp/pull/12801) (merged **2025-04-15**)
  after multiple iterations: backward compatibility with legacy GGUFs preserved, new GGUF
  metadata (`n_embd_head_k_mla`/`n_embd_head_v_mla`), context shifting kept working, and an
  explicit narrative of "whose work is what." Months of public iteration, two authors,
  benchmarks throughout. Notably this *is* low-rank weight serving (latent-space attention)
  and it merged because it came from the model architecture, with a huge KV-cache win.
- **Ternary quants** [PR #8151](https://github.com/ggml-org/llama.cpp/pull/8151)
  (compilade, 2024): new types justified by a model family (BitNet), shipped with HF
  uploads + perplexity tables — the template the quant-type criteria were codified from.
- **gpt-oss + MXFP4** [PR #15091](https://github.com/ggml-org/llama.cpp/pull/15091)
  (ggerganov, merged 2025-08-05): new type landed because a flagship model shipped in it.
- **NVFP4** (2026): same pattern — in-tree because NVIDIA ships NVFP4 checkpoints; now a
  continuous optimization stream by NVIDIA engineers (ORippler:
  [#24481](https://github.com/ggml-org/llama.cpp/pull/24481) merged 2026-07-07,
  [#25730](https://github.com/ggml-org/llama.cpp/pull/25730) merged 2026-07-22,
  [#26311](https://github.com/ggml-org/llama.cpp/pull/26311) open) plus community CPU ports
  ([#23961](https://github.com/ggml-org/llama.cpp/pull/23961) AVX2, merged 2026-07-01).
- **Qwen3Next** (pwilkin, 2025-09 → 2026): community-driven multi-month arch effort with
  maintainer follow-up optimization (ggerganov's
  [#19375](https://github.com/ggml-org/llama.cpp/pull/19375), merged 2026-02-05).

**Pattern:** big features land when (a) a real model/vendor demands them, or (b) a
long-lived contributor iterates publicly for months with benchmarks at every step and takes
maintenance ownership (CODEOWNERS entry). Paper-driven features with no model shipping in
that format have the worst record.

---

## 4. Upstream appetite for VRAM reduction beyond quantization

- **Weight side:** direction is **hardware-native FP formats** (MXFP4, NVFP4 W4A4/W4A8 on
  Blackwell — [#24364](https://github.com/ggml-org/llama.cpp/pull/24364),
  [#22112](https://github.com/ggml-org/llama.cpp/pull/22112) CUDA mxfp4 repack PoC) and
  **QAT checkpoints from vendors** (official Gemma QAT q4_0 GGUFs from Google, e.g.
  [#25739](https://github.com/ggml-org/llama.cpp/issues/25739) referencing
  `google/gemma-4-E2B-it-qat-q4_0-gguf`). Also imatrix-aware NVFP4 scale search
  ([#25153](https://github.com/ggml-org/llama.cpp/pull/25153), open). No "Q2_0"-style new
  low-bit codebook types recently in mainline; that energy lives in ik_llama.cpp's IQ*_K
  and trellis (KT) quants.
- **KV-cache side is where the memory-reduction hunger actually is:** TurboQuant
  ([#20977](https://github.com/ggml-org/llama.cpp/issues/20977) open), XQUANT
  ([Discussion #15400](https://github.com/ggml-org/llama.cpp/discussions/15400), 2025-08-18)
  where **ggerganov** replied: could be a new memory module, "**Doubt this would make it to
  `master`, but it could be interesting to play with**" — a precise calibration of his
  default posture toward paper-driven memory tricks: curious, sandbox-first, master-later.
- **Accuracy-vs-size positions on record:**
  - CONTRIBUTING's KL-divergence requirement = maintainers' official accuracy currency
    (PPL alone insufficient; RMSE dismissed outright by ikawrakow in ik #1509).
  - ikawrakow (ik #2213, 2026-07): sophistication pays at 2–3 bpw, washes out at 4 bpw.
  - jukofyork (#8831): at 4-bit, "adding an extra bit" beats a rank-256 side-car of equal
    cost. Any low-rank proposal must beat the *same-total-bytes* quant, not FP16.
  - AGENTS.md: "a simpler change that does 90% of the job is often preferable to a complex
    one that does 100%."

---

## 5. PR strategy recommendation

**Verdict: viable, but only on a narrow path. The naive pitch ("LQER for llama.cpp") was
pre-refuted in 2024–2025 by jukofyork's measurements and ikawrakow's prediction. The modern
pitch (EoRA/CALDERA-class, activation-aware, targeting 2–3-bit bases, served through the
existing adapter machinery) has an empty field, a sympathetic maintainer (compilade), and
requires zero new ggml types. That is a landable feature if staged correctly.**

1. **Frame it as reviving Discussion #8831, not as a new idea.** compilade asked for exactly
   this in Aug 2024; items on his list (GGUF-pair → corrective adapter script; adapter
   embedded in model GGUF with auto-apply metadata) are still unbuilt. Post the opening
   comment *in #8831* or open a linked issue referencing it, ik #15, and jukofyork's
   negative results — and answer them head-on: plain SVD at 4-bit is dead (agreed); the
   claim is activation-weighted low-rank (EoRA eigenspace / L²QER-with-imatrix) at **2–3
   bpw**, where both ikawrakow's trellis scaling law and the residual spectra favor it.
2. **Come with data, not code.** Before any C++: a Python tool
   (`gguf-py` dequant + torch SVD, imatrix as the activation weighting — compilade already
   noted imatrix ≈ L²QER's S matrix) producing corrective adapters, plus the exact evidence
   bundle CONTRIBUTING demands for quant types even though we add none: PPL + **KL-div** for
   (base_Q2/Q3 alone) vs (base + rank-r adapter) vs (same-total-bytes bigger quant, e.g.
   IQ3 vs Q2+adapter), plus llama-bench tok/s. The same-total-bytes comparison is the one
   jukofyork will run mentally; win it or don't post.
3. **Slice the scope into 3–4 PRs, CPU-first:**
   - PR 1: Python tooling only (`gguf-py`/scripts): extract activation-weighted low-rank
     residual adapter from a GGUF pair + imatrix. No inference changes at all — output is a
     standard LoRA GGUF served by the *existing* `--lora`. Zero-risk, immediately usable,
     builds credibility.
   - PR 2: GGUF metadata + loader support for an embedded corrective adapter that
     auto-applies (compilade's item 2). Small C++ surface, no new ops.
   - PR 3 (only after 1–2 prove demand): CUDA fusion of quantized GEMV + low-rank epilogue
     for decode. Benchmarks vs unfused; engage JohannesGaessler early (his MMVQ fusion work
     #24481 is the pattern; his open LoRA-training issue #13485 shows he touches this path).
   - Explicitly do **not** propose a new ggml_type — the residual stays a LoRA-format
     adapter (F16 or Q8_0 A/B matrices), dodging the highest merge bar entirely.
4. **Process hygiene is now machine-enforced:** fill the PR template completely, disclose AI
   assistance explicitly, write every issue/PR description and reviewer reply by hand (AI-
   written posts are banned), one open PR at a time as a new contributor, run local CI +
   `test-backend-ops`, and add a CODEOWNERS entry to signal maintenance commitment. Expect
   the ggml-gh-bot before any human.
5. **People map:** compilade (proposer of the idea, quant/gguf-py owner — primary reviewer),
   ngxson (adapter subsystem author — loader/server changes), jukofyork (will demand the
   same-bytes comparison; convert him with 2-bit data and he's the strongest possible ally,
   cf. MLA), JohannesGaessler (CUDA), ggerganov (final call; his XQUANT reply says the
   pitch must be "small module, big measured win" or it stays a toy branch). ikawrakow is
   out of mainline; ik_llama.cpp is a benchmark rival, not a venue.
6. **Kill criteria (honor the fallen early):** if Q2/Q3 + rank-64–128 adapter cannot beat
   the same-total-bytes IQ3/IQ4 quant on KL-div at ≤10% decode slowdown, upstream will not
   take it — publish the negative result in #8831 and stop. That exact experiment is the
   cheapest possible first milestone and needs no upstream buy-in to run.

### Cited sources (chronological)

| Date | Item |
|---|---|
| 2023-11-22 | [Issue #4176 — dynamic tile quant + fine-tune vector approximation](https://github.com/ggml-org/llama.cpp/issues/4176) |
| 2023-12-23 | [Issue #4611 — Mixtral low-rank conversion musing](https://github.com/ggml-org/llama.cpp/issues/4611) |
| 2024-04-08 | [Issue #6536 — DoRA support request (closed)](https://github.com/ggml-org/llama.cpp/issues/6536) |
| 2024-07-15 | [PR #8332 — LoRA refactor, runtime adapters on quantized base (merged)](https://github.com/ggml-org/llama.cpp/pull/8332) |
| 2024 | [PR #8151 — ternary quants TQ1_0/TQ2_0 (merged)](https://github.com/ggml-org/llama.cpp/pull/8151) |
| 2024-08-02 | [Discussion #8831 — Using LQER to improve low-bit quants](https://github.com/ggml-org/llama.cpp/discussions/8831) |
| 2024-08-09 | [ik Discussion #15 — Will LQER improve k- and i-quants?](https://github.com/ikawrakow/ik_llama.cpp/discussions/15) |
| 2025-01-27 | [PR #11446 — DeepSeek MLA (fairydreaming, superseded)](https://github.com/ggml-org/llama.cpp/pull/11446) |
| 2025-04-15 | [PR #12801 — DeepSeek MLA (jukofyork, merged)](https://github.com/ggml-org/llama.cpp/pull/12801) |
| 2025-05-12 | [Issue #13485 — LoRA training example (JohannesGaessler, open)](https://github.com/ggml-org/llama.cpp/issues/13485) |
| 2025-08-05 | [PR #15091 — gpt-oss + MXFP4 (merged)](https://github.com/ggml-org/llama.cpp/pull/15091) |
| 2025-08-18 | [Discussion #15400 — XQUANT; ggerganov's "doubt this makes master" reply](https://github.com/ggml-org/llama.cpp/discussions/15400) |
| 2025-12 | [Issues #18050](https://github.com/ggml-org/llama.cpp/issues/18050) / [#18466](https://github.com/ggml-org/llama.cpp/issues/18466) / [#18193](https://github.com/ggml-org/llama.cpp/issues/18193) — multi-adapter crashes (fixed) |
| 2026-01/04 | [Issue #19217](https://github.com/ggml-org/llama.cpp/issues/19217) / [#21552](https://github.com/ggml-org/llama.cpp/issues/21552) — LoRA graph-rebuild/memory leaks (fixed) |
| 2026-03-25 | [Issue #20977 — TurboQuant feature request (open)](https://github.com/ggml-org/llama.cpp/issues/20977); [Discussion #20969](https://github.com/ggml-org/llama.cpp/discussions/20969); [ik Issue #1509 (closed)](https://github.com/ikawrakow/ik_llama.cpp/issues/1509) |
| 2026-06/07 | NVFP4 stream: [#24481](https://github.com/ggml-org/llama.cpp/pull/24481), [#25730](https://github.com/ggml-org/llama.cpp/pull/25730), [#26311](https://github.com/ggml-org/llama.cpp/pull/26311), [#25153](https://github.com/ggml-org/llama.cpp/pull/25153), [#23961](https://github.com/ggml-org/llama.cpp/pull/23961) |
| 2026-07-14 | [PR #25662 — "Infcore", bot-closed in 2 minutes](https://github.com/ggml-org/llama.cpp/pull/25662) |
| 2026-07-28 | [Issue #26207 — per-request lora prompt-cache contamination (open)](https://github.com/ggml-org/llama.cpp/issues/26207) |
| 2026-07-30 | [ik Discussion #2213 — trellis-vs-IQ4_KS datapoint; ikawrakow on bpw scaling](https://github.com/ikawrakow/ik_llama.cpp/discussions/2213) |
| current | [CONTRIBUTING.md](https://github.com/ggml-org/llama.cpp/blob/master/CONTRIBUTING.md) · [AGENTS.md](https://github.com/ggml-org/llama.cpp/blob/master/AGENTS.md) · [PR #26178 — "merge ready" label](https://github.com/ggml-org/llama.cpp/pull/26178) |
