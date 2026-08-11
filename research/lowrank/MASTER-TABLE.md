# beast-rank MASTER COMPARISON TABLE
# METRIC STANDARD (PROTOCOL §0): compression = targeted-reference bytes
# / artifact bytes (×BF16 column is context, never headline); every
# corrected row reads against its same-size quant comparator rows.
# (methodology v2; BF16-referenced; grows as reruns land. This file is
# the paper's Table 1 in waiting. ± are printed stderr; * = paired
# stats pending; serving = stock llama.cpp unless [fused] noted.)

## Qwen3.5-0.8B (BF16 = 1557.7 MB, PPL 16.61, logits 40ch)

| config | MB | bpw | ×BF16 | PPL | KLD | top-1 % | prov |
|---|---|---|---|---|---|---|---|
| BF16 reference | 1558 | 16.0 | 1.00× | 16.61 | 0 | 100 | — |
| vendor UD-Q4_K_XL | 573 | 5.9 | 2.72× | 18.94 | 0.0272 | 90.7 | vendor |
| Q4_K_M | 543 | 5.6 | 2.87× | 19.79 | 0.0396 | 89.4 | BF16-1step |
| Q3_K_M | 480 | 4.9 | 3.24× | 23.28 | 0.1668 | 79.2 | BF16-1step |
| IQ3_XXS | 412 | 4.2 | 3.78× | 23.95 | 0.2573 | 74.3 | BF16-1step |
| Q2_K bare | 436 | 4.5 | 3.57× | 33.09 | 0.4902 | 66.4 | BF16-1step |
| Q2_K + fc r64 | 518 | 5.3 | 3.01× | 24.61 | 0.2227 | 77.0 | BF16-1step |
| Q2_K + fc-2side r64 | 518 | 5.3 | 3.01× | 24.16 | 0.2066 | 78.1 | BF16-1step |
| Q2_K + fc r128q8 | 523 | 5.4 | 2.98× | 23.03 | 0.1685 | 79.4 | BF16-1step |
| + alternation ×1 | 523 | 5.4 | 2.98× | 22.79* | 0.1467* | 81.1* | BF16-1step |
| Q2_K + E13 re-round (free) | 436 | 4.5 | 3.57× | 28.09† | 0.323† | 72.7† | BF16-1step |
| RR-base + fc r128q8 | 523 | 5.4 | 2.98× | 23.90† | 0.192† | 78.7† | BF16-1step |

† paired per-chunk stats (E24): RR-vs-bare resolved at t=30-45; the
RR+fc row confirms (3rd time, paired) that RR-then-correct loses to
alternation-with-correction — RR is the zero-byte serving lever, not
the pre-corrector step. YAQA-lite Kronecker variant: anti-helpful with
current T estimates (E24 "Named results" 2) — not tabled. ["E24 §6"
was a dead cite; fixed per adversarial-round2-experiments F11.]

⚠ RR row-note (added 2026-08-04 per adversarial-round2-experiments
F11): THIS wiki-calibrated artifact measured WORSE than bare on the
held-out code corpus (PPL 3.470 vs 3.083 — R7 first pass); the numbers
above are on-calibration-distribution. The mixed-calibration variant
(RR-mixed2: wiki 29.84 / code 2.785) rescues both corpora but has
PPL only — its KLD/top-1 are queued (ABLATION-PLAN T1.14) and it is
not tabled until then. "Zero-byte serving lever" holds only with
broad calibration + a held-out gate (see DEPLOYABLE-WINS).

Pending rows (rerun matrix R1-R7; YAQA-lite dropped — decided-not-
tabled per E24, coherence-audit P2.9): interpolation control @523 MB;
alternation ×2 paired; rank sweep; serving columns (tok/s stock +
[fused], VRAM); held-out corpus (PARTIAL — R7 first pass done for the
re-rounder, see row-note; crown configs + task eval still open).

Notes: bpw = file bytes ×8 / param count (0.8B ≈ 0.78e9); ×BF16 =
BF16 MB / total MB. The between-rungs window claim: corrected configs
compete between Q3_K_M and Q4-tier; Q4-tier dominates above ~540 MB.

## Qwen3.6-27B heretic-v2 (BF16 = 54.66 GB, PPL20 6.9742 ± 0.2455,
## logits 20ch verified; E27 re-derivation — every row ONE quantize
## step from BF16, new 48-chunk BF16 imatrix+Grams, MTP pin
## blk.64=q5_k; † = paired per-chunk stats in
## experiments/27-bf16-rederivation/results-paired.txt; rows marked
## [40] carry n=40 values from the T1.16 extension (truth logits
## bf16ref27b-40.logits, BF16 PPL40 6.027 ± 0.145; their PPL column
## is PPL40, not PPL20 — raw: results-40ch{,-paired}.txt))

| config | GB | bpw | ×BF16 | PPL20 | KLD | top-1 % | prov |
|---|---|---|---|---|---|---|---|
| BF16 reference | 54.66 | 16.0 | 1.00× | 6.974 ± 0.246 | 0 | 100 | — |
| Q2_K | 11.00 | 3.22 | 4.97× | 7.724 ± 0.279 | 0.1576 ± 0.0069 | 83.9 ± 0.5 | BF16-1step |
| IQ3_XXS | 11.48 | 3.36 | 4.76× | 7.562 ± 0.270 | 0.0983 ± 0.0041 | 87.3 ± 0.5 | BF16-1step |
| MIXED bare (ffn q3_k) | 12.16 | 3.56 | 4.49× | 7.248 ± 0.256 | 0.0944 ± 0.0061 | 88.0 ± 0.5 | BF16-1step |
| IQ3_XS | 12.26 | 3.59 | 4.46× | 7.363 ± 0.259 | 0.0666 ± 0.0025† | 89.4 ± 0.4 | BF16-1step |
| Q3_K_S [40] | 12.37 | 3.62 | 4.42× | 6.352 ± 0.156 | 0.0832 ± 0.0029† | 88.1 ± 0.3† | BF16-1step |
| MIXED + fc r128q8 [40] | 12.50 | 3.66 | 4.37× | 6.221 ± 0.151† | 0.0831 ± 0.0035† | 88.6 ± 0.3† | BF16-1step |
| interpolation CONTROL [40] | 12.50 | 3.66 | 4.37× | 6.213 ± 0.151† | 0.0739 ± 0.0025† | 89.0 ± 0.3† | BF16-1step |
| Q3_K_M [40] | 13.59 | 3.98 | 4.02× | 6.153 ± 0.150 | 0.0551 ± 0.0019 | 90.4 ± 0.3 | BF16-1step |
| Q4_K_M | 16.84 | 4.93 | 3.25× | 7.078 ± 0.251 | 0.0186 ± 0.0019 | 94.3 ± 0.3 | BF16-1step |

† E27 paired verdicts at the 12.5 GB point, UPGRADED to n=40 (T1.16,
2026-08-04 evening; pre-registered): MIXED+fc vs Q3_K_S = KLD dead tie
(t=−0.04, point estimates 0.0831 vs 0.0832) / top-1 t=+1.89 (under the
bar) / PPL edge t=−3.33 — parity CONFIRMED at n=40; the legacy 1.0σ
"victory" stays dead. The CONTROL (pure-quant mix at the same bytes:
Q3_K_S + attn_k/v/output→q4_k + 6-layer attn_qkv→q4_k) now BEATS the
flagship on KLD past the 2σ bar (t=+2.26, 28/40 chunks, sign p=0.017;
outlier trims raise it to t≈2.8; was t=1.9 at n=20), PPL/top-1 tied —
"matches-or-beats" is paired-RESOLVED, and the corrected config does
not rise above the ladder's interpolation line. CONTROL vs Q3_K_S
strengthens to |t|≥3 on all three (KLD −3.04 / NLL −4.29 / top-1
+4.86). The correction increment over its own base is real (KLD
t=−5.9, top-1 t=+2.5, n=20). IQ3_XS holds the KLD crown from below
(vs Q3_K_S t=−2.8, n=20). Single-step rebuild vs legacy Q6-requant:
−0.002…−0.003 KLD on every rung checked (the F10 provenance tax,
measured). Serving columns (tok/s, VRAM) pending locked-clock
protocol (R8).

E13-27B re-round pair, n=40 (LEGACY Q6-requant provenance — NOT
tabled above per the provenance rule; both sides same provenance so
the DELTA is clean): re-rounded Q2_K 0.1374 ± 0.0042 KLD / 85.06%
top-1 vs bare 0.1533 ± 0.0047 / 84.20% — paired KLD t=−3.70 (26/40),
top-1 t=+2.56, NLL tie. The E13 0.78σ open question is RESOLVED in
the re-rounder's favor: −10.4% KLD + 0.86 pt top-1 for zero bytes at
27B. Wiki-calibrated/wiki-scored; the 0.8B R7 held-out
anti-generalization gate is untested at 27B — no deployment language
until it runs.

## Qwen3.6-35B-A3B MoE (E23 COMPLETE — capture shipped with gates
## green, REPORT-capture.md; review-compliant ladder + paired stats
## below. "Capture in flight" deleted 2026-08-04 per coherence-audit
## P1.6.)

TARGETED reference = vendor UD-Q4_K_M (22.135 GB) — no BF16 exists on
the rig; every row is ONE llama-quantize step from it, so all rows
share requant-of-Q4_K_M provenance (uniform, disclosed; PROTOCOL §0
targeted-reference rule). KLD/top-1 vs Q4_K_M logits, 20 chunks.

| config | GB | C (vs Q4_K_M) | PPL20 | KLD | top-1 % | prov |
|---|---|---|---|---|---|---|
| reference UD-Q4_K_M | 22.135 | 1.00 | 6.627 ± 0.228 | 0 | 100 | vendor |
| base Q2_K (exps pinned) | 11.849 | 1.87 | 7.677 ± 0.277 | 0.2104 ± 0.0059 | 80.5 ± 0.6 | Q4KM-1step |
| base + SH r32 adapter | 13.458 | 1.64 | 7.630 ± 0.275 | 0.2045 ± 0.0056 | 81.0 ± 0.6 | Q4KM-1step |
| control (down_exps→q4_K) | 14.366 | 1.54 | 7.469 ± 0.270 | 0.1666 ± 0.0046 | 82.9 ± 0.5 | Q4KM-1step |
| base + PE r32 adapter | 14.523 | 1.52 | 7.619 ± 0.275 | 0.1923 ± 0.0055 | 81.3 ± 0.6 | Q4KM-1step |
| rival Q3_K_S | 15.182 | 1.46 | 7.270 ± 0.260 | 0.1150 ± 0.0034 | 85.3 ± 0.5 | Q4KM-1step |

Paired verdicts (E23, n=20): both corrections real (PE vs bare 4.6σ,
18/20; SH vs bare 2.7σ), both LOSE the ladder — PE loses the trivial
promotion CONTROL at **6.9σ paired, 0/20 chunks** (+0.0257 ± 0.0038
KLD); control loses Q3_K_S at 8.2σ. Shared-basis capture sits barely
above the random-basis floor (0.086–0.090 vs 0.0625 up/gate; 0.022 vs
0.0156 down) — no fat shared subspace. Full tables + capture gates:
experiments/23-moe/README.md.

## R8 serving columns (first locked-protocol entry, 2026-08-04 late —
## quiet host, interleaved 2-block A/B, N=10, median [IQR]; clocks
## unlocked, sudo unavailable — drift controlled by interleaving)
27B flagship (MIXED + fc r128q8, Phase-3 kernels): fused 88.1
[88.0-88.3] vs unfused 85.8 [85.7-85.8] tok/s — +2.7%, disjoint IQRs;
Phase-3's same-session +2.6% estimate confirmed under protocol.
