# COHERENCE AUDIT — round 2 (2026-08-04, post-E24/E26/E27 update wave)

Scope: research/lowrank/ tree (core docs, paper/ incl. draft/, all
experiments/*/README+REPORT, prior-art/MANIFOLD-CANDIDATES.md,
upstream/, review/, beastrank.py) + docs/TODO.md vLLM entry.
Method: every number/verdict cross-checked against its newest source
(E27 README + results-paired.txt is the 27B ground truth; E24/E26 for
0.8B; E23 for MoE; DEPLOYABLE-WINS R7 arc for held-out).
NO files were changed; this is the fix list.

Priorities: **P1** = factual error a reader hits as current truth ·
**P2** = stale verdict / done-but-listed-open · **P3** = cosmetic.

---

## P1 — factual errors visible to a reader (8)

**P1.1 — RESULTS_ROLLUP.md, 27B section (lines 80–107): legacy
Q6-referenced table presented as current, no supersession marker.**
Current: table rows `Q3_K_M | 13.50 | … | 0.0555 | 90.1%`,
`IQ3_XS (the 27B crown) | 12.26 | … | 0.0656`, `Q4_K_M | 15.9†`.
MASTER-TABLE's E27 section (BF16-1step) has Q3_K_M 13.59 GB / 0.0540,
IQ3_XS 0.0666, Q4_K_M 16.84 GB. Both tables read as current; the pairs
0.0555/0.0540 and 0.0656/0.0666 are different provenances of the same
nominal measurement. Required: add a banner over the 27B section —
"LEGACY (requant-of-Q6, Q6-referenced logits) — SUPERSEDED by
MASTER-TABLE.md §27B / experiments/27-bf16-rederivation (E27,
BF16-pure). Kept for the provenance-tax comparison only." Same banner
class for Block 4/Block 5 rows that E27 rebuilt.

**P1.2 — RESULTS_ROLLUP.md lines 105–107: "27B verdicts: geometry-aware
MIXED+fc beats Q3_K_S (first ladder victory)".**
Contradicts the SAME FILE's corrections header item 1 (parity) and E27
(paired KLD t=0.6 tie; legacy margin FLIPPED SIGN; the interpolation
control matches-or-beats the flagship). Required: rewrite the verdict
line — "MIXED+fc = paired parity with Q3_K_S (E27); the 27B
interpolation control matches-or-beats it at equal bytes; IQ3_XS keeps
the crown (paired)."

**P1.3 — RESULTS_ROLLUP.md lines 160–164 ("The laws the campaign
measured", law 1): "Capture ≈ 5.9·(r/d) … Governs every result
above."**
Retracted by the same file's corrections item 5, by OUTLINE.md, and by
draft/04 §4.2 (two-point observation; c varies ~9.6–20.8 by
whitener/base). Required: retitle the section ("laws and
observations"), rewrite item 1 as the two-point diag-r64 observation
with the c-range caveat; drop "governs every result above".

**P1.4 — RESULTS_ROLLUP.md line 115 (Block 4): "IQ1_S + fc-mix …
41% recovery; frontier-by-default <10 GB".**
E15 README (review-corrected) says 8.24 GB is FILE bytes; projected
serving is ~10.3–10.5 GB VRAM — explicitly "NOT inside a 10 GB card",
and rivals IQ1_M/IQ2_XXS were never built. Required: strike
"frontier-by-default <10 GB"; replace with "8.24 GB file /
~10.3–10.5 GB VRAM projected; equal-byte rivals unbuilt".

**P1.5 — RESULTS_ROLLUP.md line 120 (Block 4): "Recovery-fraction
curve (F7): 7.5% @Q2_K → 18% @IQ2_XS → 41% @IQ1_S."**
The corrections header (item 6), E15 README, and draft/04 §4.4 all
retire this metric-mixed curve in favor of the method-consistent
20% → 28% → 41%. Presented un-struck 70 lines below its own
retraction. Required: strike or annotate "(mixed-whitener series,
retired — see correction 6; fc-consistent curve is 20/28/41)".

**P1.6 — MASTER-TABLE.md lines 72–73: "## Qwen3.6-35B-A3B MoE (WS1
capture in flight; table opens with its first review-compliant
ladder)".**
E23 is COMPLETE: capture shipped (REPORT-capture.md, gates green) and
the review-compliant ladder + paired stats exist (Q4_K_M ref, Q2_K
base, PE/SH adapters, control, Q3_K_S; PE loses control at 6.9σ
paired). The paper's "Table 1 in waiting" tells a reader the MoE rung
is unmeasured. Required: populate the section from E23's results table
(with its requant-of-Q4_K_M provenance column) or point to it
explicitly; delete "capture in flight".

**P1.7 — paper/draft/06-limitations-future.md §6.1 "References and
precision" (line 11): "Q3_K_S was never BF16-rescored".**
False since E27 (Q3_K_S rebuilt one step from BF16, scored 0.0845 ±
0.0044 vs BF16 truth; TODO marks the repair [x]/superseded). The same
paragraph's "several 27B levers … pending paired tests" is also
half-stale (the flagship triangle is now fully paired; allocation/
re-round/alternation at 27B remain unpaired). Required: rewrite the
sentence to name what E27 closed and what remains (27B VRAM columns,
alloc/RR/alt paired tests).

**P1.8 — MANIFOLD-CANDIDATES.md line 22 (M1 row): no outcome recorded
for a run-and-decided experiment.**
The row still reads "Expected: ~30% KL cut vs GPTQ-class in print |
One-day test: 0.6B Kronecker sketch + re-round, KLD vs E13" — but E24
RAN M1 on 0.8B: **anti-helpful** (gives back a third of the input-only
win at t=+8..+15 paired; solver verified exact, estimator at fault).
A reader plans an experiment the lab already falsified. Required: mark
M1 "✖ RUN (E24): anti-helpful with single-pass measured T; estimator
repair (T-shrinkage/bigger capture/0.6B rerun) is the follow-up."

## P2 — stale verdicts / done-but-open (14)

**P2.1 — MANIFOLD-CANDIDATES.md lines 25–26 (M4, M5 rows): E26
verdicts missing.** M4 (Fisher-Lloyd): NULL — no codes fixed point
exists (92/96 limit-cycle), 2.6% surrogate gain, zero end-to-end
effect (|t|≤1.3). M5 (gauge-fixing): on qwen35 only DIAGONAL folds;
full-strength equalization strongly anti-helpful (t=+28..+60, KLD
0/40 chunks) — importance double-counting vs the imatrix. Required:
annotate both rows RUN + verdict, pointing at
experiments/26-lloyd-gauge/README.md.

**P2.2 — experiments/22-qwen35-08b/README.md lines 17–19 and 57–59:
"the two-sided lane failed — finding 4" / "Two-sided on this arch is
BLOCKED pending gradmatrix support … author-agent is on it".**
Unblocked and RUN: E20 §"qwen35 hybrid capture" SHIPPED (132 Grams,
gradgram08b, fail-loud hardening added), E24 consumed the T Grams, and
MASTER-TABLE carries the 0.8B `Q2_K + fc-2side r64 | 24.16 | 0.2066 |
78.1` row (ABLATION-PLAN row "one-sided vs two-sided" cites it).
Required: annotate finding 4 "RESOLVED same day — gradmatrix hybrid
capture shipped (E20 §qwen35); two-sided r64 measured 24.16/0.2066 (vs
one-sided 24.61/0.2227), see MASTER-TABLE." Also stale: the trailer's
"MoE expert tensors are the untested frontier" + B10 trap warning —
E23 ran MoE with a correct pooled-Gram capture; annotate.

**P2.3 — beastrank.py (docstring lines 3–27; defaults lines 63–71):
the tool's default recipe builds the exact MIXED+fc flagship that
E27's interpolation control matched-or-beat at equal bytes,
adapter-free and serving-tax-free.** The docstring's caveat cites only
the pre-E27 rollup header ("~1sigma parity / wins-from-below").
Required: (a) docstring: add the E27 lesson — "at ≥2.5 bpw a
pure-quant mixed-TYPE base at the same bytes (Q3_K_S +
attn_k/v/output→q4_k + partial qkv promotion — the E27 control)
matches-or-beats this recipe with no adapter and no decode tax; the
corrected recipe pays only below ~2.5 bpw (E15 regime)"; (b) add a
control-style mode (e.g. `--recipe control` emitting the mixed-TYPE
quantize command, rank 0) or at least print the comparison in the
report step. Defaults may stay for the sub-2.5-bpw lane, but the
docstring must say when NOT to use them.

**P2.4 — RESULTS_ROLLUP.md corrections header item 1 (line 8):
"Paired per-chunk stats queued."** Done — E27 ran them; the legacy
margin flipped sign and the control verdict is new information beyond
"parity". Required: append "→ DONE (E27): KLD/top-1 paired ties, PPL
edge; control ≥ flagship at equal bytes."

**P2.5 — TODO.md "NOW — critical path" (lines 13–27): every item long
done.** E05a/E05b (imatrix + whitened census: 08-03 night, GO), E04
end-to-end (same night incl. round-trip check), corpus pinned, recon
absorbed. "LATER" (lines 29–37): E06 fused kernel = trilogy DONE;
scale ladder DONE (0.6B/0.8B/27B/35B). Required: move to a DONE block
(with pointers) so the file's "NOW" reflects the actual queue (repairs
queue + codec targets + 27B two-sided + T1.x from ABLATION-PLAN).

**P2.6 — TODO.md repairs queue (lines 116–118): "Held-out corpus PPL
pass (non-wikitext) + one task eval … [ ]" — half done.** R7 first
pass ran (DEPLOYABLE-WINS: held-out code corpus; RR anti-generalizes
narrow → mixed-calibration fix verified both-corpus; heldout-
results.txt in E22 dir). Task eval still zero. Required: split the
item — held-out PPL: first pass DONE for the free lever at 0.8B (crown
configs + 27B still open); task eval [ ].

**P2.7 — TODO.md repairs queue (lines 123–124): "Cache provenance
fingerprints (pass1_cache, e13 codes-dir) … [ ]" — half done.** E24's
e13b added the codes-dir fingerprint (B8 repair, fails loudly); E26
state dirs fingerprinted. pass1_cache/spectra caches still open (draft
06 §6.1 states exactly this split). Required: mark the codes-dir half
[x], leave spectra caches open.

**P2.8 — PROTOCOL.md rerun matrix (lines 52–60): statuses stale.**
R4 "(WS2, in flight, paired)" → DONE (E24, the §3 flagship
measurement). R7 → first held-out pass done for the re-rounder
(DEPLOYABLE-WINS), task eval open. Final line "Then: 27B-from-BF16
re-derivation of the frontier configs only." → DONE (E27). Required:
update the three statuses.

**P2.9 — MASTER-TABLE.md lines 32–34: pending-rows list contains
"YAQA-lite re-round (WS2)" while the † footnote 4 lines above already
reports its E24 verdict ("anti-helpful … not tabled").** Same list:
"held-out corpus" now partially done (R7). Required: drop YAQA-lite
from pending (it is decided-not-tabled), annotate held-out as
partial.

**P2.10 — paper/ABLATION-PLAN.md lines 41–42, 97, 205: "E27 is the
in-flight repair" / "T1.4 ◆ E27 completion (in flight)".** E27
COMPLETE (core: ladder + control + paired stats + fc adapter);
residual work is only T1.5's 100-chunk quartet + serving columns.
Required: mark T1.4 done-with-residuals; fix the two "(in flight)"
mentions in §1/§2. Header "Nothing in this file has been run" is now
false for T1.4. Also line 75's "E27's BF16 rerun fixes provenance"
can cite it as done.

**P2.11 — paper/draft/00-abstract.md + draft/01 §1.2/§1.3: pre-E27
framing of the 27B result.** Abstract: "corrected configurations
reach parity at 27B (~1 sigma, top-1 exactly tied)"; intro §1.2:
"parity … 1.0 sigma unpaired, top-1 *exactly* tied at 88.039%
[source: …/10-full-covariance/README.md]". E27 (which §4.3 already
leads with) upgrades this: paired tie t=0.6, sign flipped, and the
NEW headline negative — the interpolation control matches-or-beats
the corrected flagship, so the ladder's interpolation line is
unbeaten at 27B with the control finally on the table. The 88.039%
coincidence no longer exists in clean-provenance data. Required:
update abstract + §1.2 to the E27 paired verdicts and cite E27; keep
the legacy numbers only as the history §2.5/§4.3 already tell.

**P2.12 — paper/draft/03-results-free-lever.md §3.1: the R7
generalization caveat is absent from the free-lever chapter.**
DEPLOYABLE-WINS (held-out finding + resolution) shows wiki-calibrated
RR is WORSE than bare on held-out code (3.470 vs 3.083) and that
mixed 2:1 calibration rescues both corpora; the tool "ships with a
held-out gate or not at all". §3.1/§1.3/abstract state the zero-byte
win without the calibrate-broadly condition. Required: add the R7
arc (one paragraph + the both-corpus numbers) to §3.1 and a clause to
§1.3(1)/abstract: "given sufficiently broad calibration —
narrow-calibration re-rounding anti-generalizes (measured)."

**P2.13 — paper/draft/06 §6.2 (line 19): repairs-queue recap lists
"paired statistics for the two knife-edge comparisons; BF16-rescore of
Q3_K_S" as future work.** Both closed by E27 (TODO marks them [x]).
Required: prune to the still-open items (VRAM columns, locked-clock
speed pairs, 27B kind-probe, spectra-cache fingerprints, MoE gram
tooling, upstream comments).

**P2.14 — RESULTS_ROLLUP.md line 117 (Block 4): "NVFP4 + fc …
best-correcting base; unpublished composition".** E16's own corrected
header says the novelty sweep was budget-capped and SVDQuant/Nunchaku
ship low-rank+NVFP4 for diffusion — "treat the claim as unswept, not
cleared" (review F12); the vs-IQ3_XS ordering was withdrawn
(corrections item 9) but this row still sells "unpublished".
Required: soften to "no LLM-PTQ instance found (sweep incomplete)".

## P3 — cosmetic / hygiene (9)

**P3.1 — Experiment numbering collisions.** "E14" = fused kernel
(dir 14-fused-kernel) AND "E14-cond" = task conditioning (dir
17-task-conditioned); "E17/E18" = entropy reconstruction (dir
18-entropy-reconstruction) while dir 17 is the E14-cond experiment.
E27's caveat "see E10/E14 numbers" is ambiguous to a new reader.
Required: a 3-line NAMING note in README.md's experiments/ bullet (or
experiments/README) mapping id ↔ dir; optionally symlink-free rename
is NOT worth the churn.

**P3.2 — MASTER-TABLE.md line 70: "pending locked-clock protocol
(R8)" — PROTOCOL.md defines only R1–R7.** Dangling id. Required: add
R8 (serving columns, locked-clock, interleaved N=10 median±IQR) to
PROTOCOL's matrix or cite R6 instead.

**P3.3 — RESULTS_ROLLUP.md lines 179–180 (artifacts): "upstream/
(MTP-imatrix blindspot; IQ2-blocks-MTP; LoRA decode overhead …)".**
There is no separate IQ2-blocks-MTP draft (folded into the MTP
draft); the third file is the KV-backprop draft, unlisted; and per
correction 10 these are now confirmation-comments, not issue drafts.
Required: relist the three actual files + their comment-not-issue
status.

**P3.4 — JOURNAL.md line 584: the CAMPAIGN CLOSED paragraph is
corrupted** — "Praise be to the cyber gods. (1) per-tensor rank
allocation from the captured-energy map …" splices an orphan fragment
(E04c-era next-levers list, lines 584–591) mid-sentence. Append-only
does not require keeping a paste accident. Required: one bracketed
editorial note "[lines below spliced in error — fragment of the
08-03 E04c plan]" or excise the fragment.

**P3.5 — README.md lines 102–112: the day-1 plan table (E01–E06,
"Scale up 0.6B → 9B GLM → 27B") reads as current.** 9B died
(conversion rot), numbering diverged (E06 ≠ the shipped kernel
trilogy dirs). Required: one line above the table: "(opening plan,
2026-08-03 — superseded by experiments/ 01–27; kept for the record)".

**P3.6 — RESULTS_ROLLUP.md Block 4/5 kernel lines (123–124, 139,
146): "Phase 2 … in progress", "Phase 2B in flight".** Superseded by
the same section's Phase-3 bullet; chronology is readable but the
tense misleads skimmers. Required: past-tense the phase bullets or
add "(landed — see Phase 3 bullet)".

**P3.7 — experiments/08-structure-probes/README.md lines 62–63:
"measured-sensitivity allocation (27B verdict pending)".** The 27B
allocation row was measured (E07; review: 0.5σ unresolved) — pending
is the PAIRED verdict, not the run. Required: "(27B margin
unresolved, paired test queued — T2.9)".

**P3.8 — docs/TODO.md vLLM entry (lines 13–29): test-matrix item (a)
centers "the beast-rank corrected artifacts … may need HF/safetensors
+ separate LoRA loading".** Post-E27 the equal-byte winners at
≥2.5 bpw are pure-quant GGUFs (mixed-TYPE control, re-rounded
byte-compatible files) — no LoRA needed, which SIMPLIFIES the vLLM
matrix; the adapter path only matters for the sub-2.5-bpw lane.
Required: one clause noting the E27 outcome so the vLLM eval doesn't
over-scope adapter plumbing.

**P3.9 — review/adversarial-*.md: no forward pointers.** The reviews
correctly predate E27/E24/E26; F1/F5/F10-class items are now
resolved (some with sign flips the review predicted). Required: a
3-line "RESOLUTIONS (2026-08-04 evening)" note at the top of
adversarial-stats.md pointing to E27/E24/E26 — historical text
untouched.

---

## Counts

- **P1: 8** · **P2: 14** · **P3: 9** — 31 items.

## The six worst

1. **P1.2** RESULTS_ROLLUP still concludes "first ladder victory" for
   MIXED+fc — contradicted by its own header, and by E27 where the
   margin flipped sign and the control beat the flagship.
2. **P1.1** The rollup's whole 27B table (0.0555/0.0656/15.9 GB rows)
   stands unmarked next to MASTER-TABLE's E27 rows
   (0.0540/0.0666/16.84 GB) — two "current" truths for one
   measurement.
3. **P1.6** MASTER-TABLE says the MoE rung is "capture in flight"
   while E23 is complete with the campaign's most decisive paired
   negative (6.9σ loss to the trivial control).
4. **P1.3** "Capture ≈ 5.9·(r/d) … governs every result above" still
   headlines the rollup's laws section after being retracted to a
   two-point observation everywhere else.
5. **P2.3** beastrank.py's default recipe builds the exact
   configuration E27's pure-quant control just matched-or-beat at
   equal bytes — the tool ships the losing recipe with no warning.
6. **P1.8 + P2.1** MANIFOLD-CANDIDATES lists M1/M4/M5 as un-run
   one-day tests; all three were run and decided (E24 anti-helpful,
   E26 null, E26 strongly anti-helpful) — a reader would rebuild
   three falsified experiments.
