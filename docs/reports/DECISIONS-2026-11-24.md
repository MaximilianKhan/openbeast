# Decision log, 2026-11-24

> One entry per decision taken on 2026-11-24 (PST, UTC-8; `Z` marks UTC),
> in the format of [DECISIONS-2026-11-23.md](DECISIONS-2026-11-23.md): the
> decision, who took it, the reason, the evidence, the alternatives and why
> they lost. PRs are in `MaximilianKhan/openbeast` unless marked "website"
> (`MaximilianKhan/openbeast-website`).

## Contents

1. [Qwen3.8 stays the RTX 5090 default after I_T2](#1-qwen38-stays-the-rtx-5090-default-after-i_t2)
2. [INK-T2 on the board as its own row, ranked by SCORE](#2-ink-t2-on-the-board-as-its-own-row-ranked-by-score)
3. [The website follows the board: Inkling is the 5090's top-ranked model there](#3-the-website-follows-the-board-inkling-is-the-5090s-top-ranked-model-there)
4. [Replay the dlsym stop now, on the rig, in a new directory](#4-replay-the-dlsym-stop-now-on-the-rig-in-a-new-directory)
5. [Count capped turns from the transcripts, with a committed tool](#5-count-capped-turns-from-the-transcripts-with-a-committed-tool)

---

## 1. Qwen3.8 stays the RTX 5090 default after I_T2

**Decision.** Qwen3.8 27B Uncensored MTP Q5_K_M with `AGENT_PROFILE=qwen38-best`
stays the RTX 5090's default model and agent setup. Inkling 2 Mini
(Basalt, Derisked pack) is measured, not adopted. Nothing on the rig
changes.

**When / who.** The rule was set before the run (TODO, 2026-11-23: "Qwen3.8
with `qwen38-best` stays the default unless Max decides otherwise"; the
wrap-up brief, 2026-11-24: "unless Inkling's scored row beats Q38U-T2").
Applied in the I_T2 wrap-up, 2026-11-24 ~02:00 PST.

**Reason and evidence**
([V5_INKLING2_MINI_RTX5090-2026-11-24.md](V5_INKLING2_MINI_RTX5090-2026-11-24.md) §5, §7):

- **The scored row does not beat Q38U-T2.** INK-T2 49/71 scored, Q38U-T2
  52/71 scored. Over the 65 units both measured cleanly (Q38U-T2's 3 lost
  and 3 excluded units dropped): 46 against 52; Inkling gained 1 and lost
  7, net −6, McNemar p 0.07, within the minimum detectable effect (10).
  Single seed each.
- **The SCORE lead is breadth, and small.** INK-T2's SCORE is 76.5 against
  75.6: SOLVE is level (72.3 each); LANG is 89.1 against 85.6. Q38U-T2's
  SCORE is held down by its 3 lost units (llama-server's truncated-call
  500) and its 3 integrity exclusions (two of them passes). A 0.9 SCORE
  gap is under one unit's swing; DeepSeek's seed repeat moved by about one
  unit (T vs T_s1).
- **The engine is not ready.** Basalt v0.5.7 still carries upstream #588
  (203 of I_T2's turns ended empty at the cap), and the review's §15.8 lists
  the DNS channel, the Python pins and an adapter before a default
  ([STRATA_INKLING_5090-2026-11-23.md §15](STRATA_INKLING_5090-2026-11-23.md#15-basalt-security-and-supply-chain-review)).

**What Inkling wins, kept on record.** Time: the arm in 4.5 h against ~13.5
for Q_T2; median unit 47 s against 254 s; about 520 against 142 tok/s end
to end on full 8,192-token replies; on the 45 units both solved, fewer
turns (p 0.002) and fewer completion tokens (p 0.016). No Inkling unit hit
its wall, so the speed did not convert into passes on this suite.

**Alternatives considered.** *Switch to Inkling on SCORE*: rejected, the
paired evidence points the other way and the engine has open items.
*Switch for speed alone*: rejected; the default is chosen on measured
capability, and the faster model solved fewer units. *Hold the decision
until the ablations*: not needed for "stay"; the ablations (`I_T2_s1`,
`I_T2_stock`, `I_UP8k`) can reopen it if one of them beats Q38U-T2 on
scored passes beyond the noise margin `I_T2_s1` sets, and only after a
Basalt re-pin past #588.

---

## 2. INK-T2 on the board as its own row, ranked by SCORE

**Decision.** Add board row `INK-T2` (model "Inkling 2 Mini Derisked
(Basalt NVFP4-MX8)", weights `inkling2-mini-blackfrost-derisked-basalt-nvfp4-mx8`)
and let the board's existing rule rank it: by SCORE, #4, above Q38U-T2 (#5).

**Reason.** The board is "each model's best configuration, ranked by SCORE"
and was built that way before this run; changing the ranking rule for one
result would be choosing the rule after seeing the data. The row's own
text, the report and the docs prose say plainly that it has fewer passes
than Q38U-T2 and lost the paired test.

**The weights entry.** The pack was converted on the rig, so no Hub repo
holds it. It is pinned by its sha256 (`d2b01bcc…`, `file-sha256`, repo
null) with its source in `derived_from` (`Blackfrost-AI/Inkling-2-Mini-Derisked`
@ `31ec2edd…`) and the converter and engine at `400b411c`.
`evals/v5_board.py` gained that shape: a locally built file renders as
"local file sha256 …" and must name what it was built from (tested).
*Alternative:* revision_kind `local` with a 40-hex revision, rejected
because that field is a snapshot id, and the pack's identity is its file
hash.

---

## 3. The website follows the board: Inkling is the 5090's top-ranked model there

**Decision.** After the sync, the website's "best model on the RTX 5090"
is Inkling 2 Mini Derisked, because it is the board's top-ranked row on
that rig. Qwen3.8 keeps the "default" role and the shipped serve; the
Inkling entry says it is not the default, gives the paired reading
(6 behind Qwen3.8 on the units both measured, within noise) and the
engine's caveats.

**Reason.** Website #43 (Max, 2026-11-24) made the per-rig pick the board's
top-ranked row, "never a hand-picked name"; the build fails when that row
has no lineup entry. The brief allowed it ("Qwen3.8 stays the 5090's
headline unless Inkling scores higher"), and on the board's SCORE it does.
Overriding the pick for one model would bring back the hand-picking #43
removed.

**Alternatives considered.** *Pin Qwen3.8 as the 5090's pick*: rejected for
the reason above; flagged to Max as the one call he may want to reverse
(the change would be a rule in `bestPerRig`, e.g. the shipped default per
rig, not a name). *Hide Inkling from the site*: rejected; it has a board
row, and every row is public.

---

## 4. Replay the dlsym stop now, on the rig, in a new directory

**Decision.** Replay INK-T2's `122_gemm_blocked_d` under the proposed
Rust-only rule immediately, on the rig, from a new directory
(`~/ob-cal/.claude/gemm-replay-ink/`), rather than the rebuild-and-check
fallback.

**Reason.** The procedure prefers the replay (a verdict beats a footnote).
The rig's GPU was idle and the ablation queue was waiting on the Maya
benchmark, so a niced 10-second CPU job in a `--rm` eval-host container
disturbed nothing; the queue only waits while an eval-host container runs.
A new directory leaves PR #303's replay files unchanged. Result: control
FAIL as recorded, proposed rule PASS 3 of 3.

---

## 5. Count capped turns from the transcripts, with a committed tool

**Decision.** Count I_T2's empty capped turns (#588) and its capped turns
that still carried a tool call from the transcripts, with a new committed
tool, `evals/audit/capped_turns.py`, and use the same tool for the three
ablations.

**Reason.** The Basalt server log was gone (`docker rm -f` at the restore,
json-file logging), so the transcripts are the only record. The runner logs
`output_cap` with `reasoning_chars` only when the capped reply had no
content, which identifies an empty capped turn exactly. A committed tool
with a test makes the count reproducible and the same for every Basalt
arm; a scratch script would not.
