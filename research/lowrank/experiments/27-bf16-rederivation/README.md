# E27 — the 27B story, re-derived BF16-pure (PROTOCOL v2 rerun)

**Hypothesis.** The legacy 27B ladder and the flagship result (MIXED+fc
~1σ parity with Q3_K_S; review F1/F8/F10) were measured with mixed
provenance: every base was a REQUANT of the served Q6_K artifact while
Q4_K_M came from BF16, and Q3_K_S was never truth-rescored. Under clean
provenance — everything ONE quantization step from the BF16 reference,
captures on the BF16, paired per-chunk statistics — the orderings and
the parity verdict should hold (F10 measured the reference shift at
~0.003 KLD, same order as the contested margins, so this is a real
test, not a formality).

**What would falsify it.** MIXED+fc losing to Q3_K_S beyond paired
noise, or the interpolation control (a pure-quant mixed-type base at
the flagship's byte point — the control the campaign never built at
27B, review F1c/E10) matching the corrected config: either would kill
the flagship row as anything but a provenance artifact.

## Method (all binding choices from PROTOCOL.md)

- Reference: `weights/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-
  Preserved-BF16.gguf` (54.66 GB) — the specifically TARGETED weights.
- Truth logits: `data/bf16ref27b-20.logits` (20 chunks, wiki.test,
  n_ctx 512). Provenance VERIFIED before reuse: rescoring the served
  Q6_K against it reproduces the Block-4 record bit-for-bit
  (KLD 0.003210 ± 0.000444, top-1 97.588 ± 0.215 — see
  `verify-q6-vs-bf16logits.log`). BF16 PPL20 = 6.9742 ± 0.2455.
- Capture: Grams + imatrix on the BF16 itself, 48 chunks wiki.train,
  `LLAMA_IMATRIX_GRAM_DIR=data/gram27b-bf16` (NEW dir; gram27b-v2 is
  the legacy Q6-derived capture, untouched), -ngl 22 partial offload.
  Log: `capture-imatrix-gram.log`.
- Bases (`build_bases.sh`): Q2_K, Q3_K_S, Q3_K_M, IQ3_XXS, IQ3_XS,
  Q4_K_M, MIXED — each ONE `llama-quantize` step from BF16 with the
  new imatrix and the MTP pin `--tensor-type 'blk\.64\.=q5_k'` (E09
  discovery; also what makes IQ quants possible at all here).
- MIXED recipe (byte-identical geometry to E10's): Q2_K base type +
  `ffn_gate/ffn_up/ffn_down=q3_k` overrides + MTP pin. (Q2_K defaults
  already promote attn_v/attn_qkv→Q4_K, attn_output→Q3_K — verified
  against the E10 artifact's tensor map.)
- Correction (`e27_extract.py`, resume-safe E13-pattern wrapper around
  the E04/E10 extractor): full-covariance r128, Q8_0 factors, non-FFN
  only (`--rank-map ffn_gate=0,ffn_up=0,ffn_down=0`), whitened with
  `--gram-dir data/gram27b-bf16`, BF16 as ref. Target set replicates
  the E10 adapter: attn_q/k/v/output + attn_gate/attn_qkv/ssm_out
  (208 tensors; MTP block has no imatrix entries and is skipped — the
  known blindspot, pinned to q5_k in the base instead).
- Interpolation control: pure-quantization mixed-type base built at
  the flagship's byte point (recipe + exact bytes below) — the
  "ladder's own interpolation" comparator PROTOCOL §0/§6 requires.
- Scoring (`measure.sh`): one `llama-perplexity --kl-divergence` run
  per config vs the BF16 logits → PPL20/KLD/top-1 with printed stderr
  plus per-chunk rows; paired per-chunk stats via E24's
  `paired_stats.py` for every comparison within 2σ unpaired.

## Results (KLD/top-1 vs BF16 truth logits; PPL over the same 20 chunks;
## every row single-quantization-step from BF16; raw: results.txt, kld-*.log)

| config | GB | bpw | ×BF16 | PPL20 | KLD | top-1 % |
|---|---|---|---|---|---|---|
| BF16 reference | 54.658 | 16.0 | 1.00× | 6.9742 ± 0.2455 | 0 | 100 |
| Q2_K | 11.004 | 3.22 | 4.97× | 7.7243 ± 0.2789 | 0.1576 ± 0.0069 | 83.88 ± 0.52 |
| IQ3_XXS | 11.478 | 3.36 | 4.76× | 7.5619 ± 0.2700 | 0.0983 ± 0.0041 | 87.29 ± 0.47 |
| MIXED bare | 12.162 | 3.56 | 4.49× | 7.2479 ± 0.2564 | 0.0944 ± 0.0061 | 88.00 ± 0.46 |
| IQ3_XS | 12.259 | 3.59 | 4.46× | 7.3626 ± 0.2586 | 0.0666 ± 0.0025 | 89.35 ± 0.43 |
| Q3_K_S | 12.366 | 3.62 | 4.42× | 7.4272 ± 0.2673 | 0.0845 ± 0.0044 | 88.24 ± 0.45 |
| MIXED + fc r128q8 | 12.499 | 3.66 | 4.37× | 7.2330 ± 0.2572 | 0.0863 ± 0.0054 | 88.65 ± 0.44 |
| CONTROL (interp.) | 12.497 | 3.66 | 4.37× | 7.2431 ± 0.2579 | 0.0723 ± 0.0026 | 88.82 ± 0.44 |
| Q3_K_M | 13.594 | 3.98 | 4.02× | 7.1186 ± 0.2532 | 0.0540 ± 0.0023 | 90.31 ± 0.41 |
| Q4_K_M | 16.839 | 4.93 | 3.25× | 7.0784 ± 0.2509 | 0.0186 ± 0.0019 | 94.28 ± 0.33 |

MIXED+fc = 12.162 GB base + 336.5 MB adapter (208 corrected tensors,
mean whitened-energy capture 0.42 — identical to the legacy E10 run).
bpw = bytes×8 / 27.329e9 params; ×BF16 = compression vs the TARGETED
reference (PROTOCOL §0 — BF16 *is* the target here).

Rebuild vs legacy, measured: every rebuilt rung scores 0.002–0.003 KLD
better than its legacy Q6-requant sibling under the same BF16 logits
(Q3_K_M 0.0540 vs 0.0570 rescored; IQ3_XS 0.0666 vs 0.0689; Q4_K_M
0.0186 vs 0.0211-class) — a **rebuild tax (provenance + calibration)**
of the same order as the old flagship margin. Attribution caveat
(added 2026-08-04 per adversarial-round2-experiments F2): the rebuild
changed TWO things per rung — provenance AND the imatrix (new 48-chunk
BF16 capture) — and legacy Q4_K_M was ALREADY BF16-single-step yet
improved by the same ~0.0025, so the uniform shift is consistent with
"the new imatrix is worth ~0.0025 everywhere" and a provenance tax of
~zero. The isolating run (requant one rung from Q6 WITH the new
imatrix) is the queued three-way split; until then do not attribute
the 0.002–0.003 to provenance alone.

## Paired per-chunk statistics (results-paired.txt; n=20; A-vs-B, KLD
## dmean < 0 = A better; † = paired-resolved |t|≥2)

Bare ladder, adjacent rungs — all KLD-resolved†:
Q2_K→IQ3_XXS t=5.7, IQ3_XXS→IQ3_XS t=7.6, Q3_K_S→Q3_K_M t=7.3,
Q3_K_M→Q4_K_M t=12.9; and IQ3_XS BEATS Q3_K_S from below (KLD t=−2.8,
19/20 chunks, top-1 t=+2.0)† — the IQ3_XS crown is now paired-resolved,
not a point estimate. PPL saturates early: Q3_K_M→Q4_K_M NLL t=0.6.

The flagship triangle at ~12.5 GB:
- MIXED+fc vs Q3_K_S: KLD t=+0.6 (10/20 chunks — a dead tie), top-1
  t=+1.0 (tie), NLL t=−2.5 (flagship better)†. Parity, with a real PPL
  edge; the legacy "1.0σ KLD victory" is GONE — its point estimate
  FLIPPED SIGN (−0.0061 → +0.0018) under clean provenance.
- MIXED+fc vs CONTROL: KLD dmean +0.0140 ± 0.0075 (t=+1.9, control
  better on 16/20 chunks); NLL t=−0.2, top-1 t=−0.4 (ties). The control
  matches-or-beats the corrected config at equal bytes.
- CONTROL vs Q3_K_S: better on ALL THREE (KLD t=−2.5, NLL t=−2.6,
  top-1 t=+2.9)† — the control is a genuine ladder point, not a straw.
- MIXED+fc vs MIXED bare: KLD dmean −0.0081 ± 0.0014 (t=−5.9, 19/20)†,
  top-1 +0.65 pt (t=+2.5)†, NLL tie — the correction's increment over
  its own base is real and paired-resolved.
- MIXED bare vs Q3_K_S: KLD t=+3.6 (rung better)† — the base mix alone
  does NOT tie the rung under clean provenance (refines review F1a).
- MIXED+fc vs IQ3_XS: KLD t=+2.3 (IQ3_XS better)†, NLL t=−2.0
  (flagship better), top-1 t=−1.5 — IQ3_XS keeps the byte-class KLD
  crown at fewer bytes.

## Verdicts

1. **Parity with Q3_K_S survives clean provenance; victory stays dead.**
   KLD and top-1 are paired ties; the flagship's only resolved edge over
   the rung is PPL (t=−2.5). The legacy 1.0σ KLD margin was a
   provenance-plus-selection artifact — its sign did not survive the
   rebuild. The honest sentence is unchanged from the review's repair:
   statistically indistinguishable from Q3_K_S at ~equal bytes, from a
   corrected 2-bit-geometry base.
2. **The control verdict is NEW and it is negative for the correction
   paradigm at 27B:** the **designed same-byte mixed-quant control**
   (renamed from "interpolation control" 2026-08-04 per
   adversarial-round2-experiments F3 — it is ONE designed point built
   with our own measured kind-sensitivities, not a vendor ladder
   property; which SHARPENS verdict 3: base mixing is the winning
   move) at the flagship's exact byte point (recipe above, −0.016%
   bytes) is ≥ the corrected config — KLD 0.0723 vs 0.0863 (paired
   t=1.9 in the control's favor; 16/20 chunks), PPL and top-1 tied —
   and it beats Q3_K_S on all three metrics. Statistics honesty (F3):
   t=1.9 on n=20 is below the campaign's own 2σ bar over a
   heavy-tailed per-chunk distribution (max 3.82 vs median 0.029);
   the sign test on 16/20 resolves at p≈0.012 and dropping the one
   outlier chunk gives t≈+3.0 — direction very likely real, but
   "matches-or-beats" rides the sign/robust evidence pending the
   40-chunk rerun (ABLATION-PLAN T1.16). Cherry-pick audit clean:
   exactly one control was designed (bytes predicted a priori) and
   measured once; deliberate strength is legitimate for an
   existence-proof negative. At 27B the corrected flagship does NOT
   create a point above the ladder's own interpolation line; it sits
   ON it for PPL/top-1 and below it for KLD. This closes the last
   open question from E10 in the ladder's favor and is CONSISTENT
   WITH the 0.6B/0.8B inference (no small-scale control was ever
   BUILT — the "controls beat crowns" reading there comes from
   Q4-tier domination, not a measured control; scoping fixed per
   round-2 F5).
3. **The correction lever itself is real** (−0.0081 KLD, +0.65 pt top-1
   over its own base, paired 5.9σ/2.5σ) — the machinery works; it is
   simply outcompeted at this byte point by spending the same 336 MB
   as base bits. The design rule tightens: at 27B, geometry-aware BASE
   MIXING is the winning move, and the correction budget only pays
   where no ladder/mix point exists (sub-2.5 bpw, per E15).
4. **Ladder shape, BF16-pure:** IQ3_XS > Q3_K_S (paired, from below) —
   the I-quant crown is confirmed; and one-step quantization from BF16
   is worth 0.002–0.003 KLD over requant-of-requant everywhere it was
   checked (never share a table without a provenance column).

Falsification check (from the header): the control matching the
corrected config at equal bytes DID happen — so the flagship row's
correct reading is "existence proof that a corrected 2-bit-geometry
base reaches the rung", not "the correction beats the ladder". The
paper's 27B section should lead with the control.

Caveats carried: 20-chunk KLD (PROTOCOL asks ≥40 for new headline
claims — these rows exist to settle ORDERINGS, all load-bearing ones
paired-resolved or honest ties; the round-2 review notes the control
triangle's heavy tail is exactly why the ≥40 rule exists — T1.16,
**now RUN — see the 40-chunk extension below; the contested pairs
carry n=40 numbers and the ≥40 rule is satisfied for them**);
wikitext-calibrated, wikitext-scored (F4 silent-zone caveat applies to
any parity+ language); serving tax of the adapter path unpriced here
(stock llama.cpp: bare rungs don't pay it — see E10/E14 numbers);
VRAM columns absent (PROTOCOL §0 wants file AND VRAM; the adapter
path inflates VRAM ~0.7 GB, so at equal VRAM the control/rungs beat
the flagship by MORE — direction-safe for the negatives).

⚠ TOOL HAZARD (latent — adversarial-round2-experiments F4, recorded
2026-08-04): `e27_extract.py` caches per-tensor factors in
`--cache-dir` keyed by tensor name ONLY — no provenance fingerprint
(the exact B8 defect e13b/e26 already fixed). No published number is
affected (the cache was created fresh for this run), but a RESUMED
extraction against a different base/ref/gram silently reuses stale
factors. Do not resume without adding e13b's 12-line fingerprint
block; repair queued in TODO.md.

## T1.16 — the 40-chunk extension (2026-08-04 evening; pre-registered
## JOURNAL 18:08; raw: results-40ch.txt, results-40ch-paired.txt,
## kld40-*.log; truth logits data/bf16ref27b-40.logits, BF16 PPL40
## 6.0270 ± 0.1455, [20]-cumulative 6.9793 ≈ the verified 20-chunk ref)

Re-measured at n=40: MIXED+fc, CONTROL, Q3_K_S, Q3_K_M, plus the
E13-27B re-round pair (LEGACY Q6-derived provenance — cross-provenance,
never tabled beside the BF16-1step rows without the label).

| config | KLD (40ch) | top-1 % | PPL40 | prov |
|---|---|---|---|---|
| MIXED + fc r128q8 | 0.0831 ± 0.0035 | 88.62 ± 0.31 | 6.2208 ± 0.1510 | BF16-1step |
| CONTROL | 0.0739 ± 0.0025 | 88.99 ± 0.31 | 6.2127 ± 0.1512 | BF16-1step |
| Q3_K_S | 0.0832 ± 0.0029 | 88.12 ± 0.32 | 6.3520 ± 0.1562 | BF16-1step |
| Q3_K_M | 0.0551 ± 0.0019 | 90.44 ± 0.29 | 6.1528 ± 0.1500 | BF16-1step |
| E13 Q2_K re-round | 0.1374 ± 0.0042 | 85.06 ± 0.35 | 6.6925 ± 0.1662 | LEGACY Q6-requant |
| E13 Q2_K bare | 0.1533 ± 0.0047 | 84.20 ± 0.36 | 6.7136 ± 0.1679 | LEGACY Q6-requant |

Paired per-chunk, n=40 (sign test = exact binomial two-sided):

1. **CONTROL vs MIXED+fc: "matches-or-beats" HARDENS.** KLD t=+2.26
   (control better, 28/40 chunks, sign p=0.017) — now past the
   campaign's 2σ bar that n=20's t=1.9 sat under; drop-1/drop-2
   outlier trims RAISE it (t=+2.84/+2.59 — the one big chunk was in
   the CONTROL's favor). NLL t=+0.30 and top-1 t=−1.40 stay ties.
   The n=20 hedge ("rides the sign/robust evidence") is retired: at
   equal bytes the designed mixed-quant control beats the corrected
   config on KLD outright and ties the rest. Verdict 2's paradigm
   reading is now paired-resolved, not provisional.
2. **MIXED+fc vs Q3_K_S: parity holds, PPL edge strengthens.** KLD
   t=−0.04 (point estimates land on top of each other, 0.0831 vs
   0.0832), top-1 t=+1.89 (still under the bar), NLL t=−3.33
   (flagship better, 29/40, sign p=0.006; was t=−2.5 at n=20). The
   honest sentence is unchanged: statistically indistinguishable on
   KLD/top-1, real PPL edge.
3. **CONTROL vs Q3_K_S strengthens across the board:** KLD t=−3.04,
   NLL t=−4.29, top-1 t=+4.86 (was 2.5/2.6/2.9) — a genuine ladder
   point, now beyond argument. Q3_K_S→Q3_K_M stays resolved
   (KLD t=+9.26).
4. **E13-27B re-round: RESOLVED, in the re-rounder's favor.** The
   0.78σ open question (E13 README "27B result", review 2026-08-04)
   closes: KLD t=−3.70 (26/40, sign p=0.081 — the t carries it),
   top-1 t=+2.56 (29/40, sign p=0.006), NLL tie (t=−0.37). The
   zero-byte re-round gain at 27B is real: −0.0159 KLD (−10.4%),
   +0.86 pt top-1. Caveats: LEGACY provenance (both sides Q6-derived
   with the old imatrix — the delta is internally consistent but not
   a BF16-1step row); wiki-calibrated and wiki-scored — the 0.8B R7
   anti-generalization finding (RR worse on held-out code) is
   untested at 27B, so "free win" still needs the held-out gate
   before deployment language.

Interpretation deltas vs n=20: verdict 2 (control ≥ flagship) is
upgraded from "direction very likely real" to paired-resolved†;
everything else confirms. The paper's 27B section needs no rewrite,
only the statistics upgrade: the control's KLD win now carries
t=2.26/p=0.017 at n=40 with outlier-robust trims strengthening it.

Ops note: 40-chunk BF16 logits generation took ~14 min at -ngl 20
(5.07 GB file); each eval ~10-15 s warm at -ngl 99. Brief 0.6B/0.8B
eval contention was possible per the task note but no anomaly was
observed — all six evals ran clean, single-stream.
