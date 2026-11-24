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
6. [Qwen3.8 stays the default after I_T2_s1 and I_T2_stock](#6-qwen38-stays-the-default-after-i_t2_s1-and-i_t2_stock)
7. [A seed replicate is listed and never ranked (`replicate_of`)](#7-a-seed-replicate-is-listed-and-never-ranked-replicate_of)
8. [The stock pack is its own board row; the next Inkling arm runs on it](#8-the-stock-pack-is-its-own-board-row-the-next-inkling-arm-runs-on-it)
9. [Read the kept Basalt log with a committed tool, and replay both dlsym stops](#9-read-the-kept-basalt-log-with-a-committed-tool-and-replay-both-dlsym-stops)

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

---

## 6. Qwen3.8 stays the default after I_T2_s1 and I_T2_stock

**Decision.** Qwen3.8 27B Uncensored MTP Q5_K_M with `AGENT_PROFILE=qwen38-best`
stays the RTX 5090 default. Nothing on the rig changes. Revisit after the
improved Inkling arm: Basalt's stock pack plus upstream PR #632 as a local
patch (an answer block opened at the thinking budget, the fix for #588),
I_T2's switches, and ideally a seed repeat of it.

**When / who.** Max's brief for this wrap-up (2026-11-24): "Qwen3.8 stays
the 5090 default until the improved arm (stock + the Basalt PR #632 local
patch, being designed now) and ideally a seed repeat confirm." Applied in
the wrap-up of I_T2_s1 and I_T2_stock, 2026-11-24 ~11:00 PST.

**Reason and evidence**
([V5_INKLING2_MINI_RTX5090-2026-11-24.md](V5_INKLING2_MINI_RTX5090-2026-11-24.md) §10):

- **Parity, not a lead.** INK-T2-STOCK 53/71 scored (SCORE 78.2) against
  Q38U-T2 52/71 (75.6). Over the 65 units both measured cleanly: Qwen3.8
  52, stock Inkling 50 (2 gained, 4 lost, −2, McNemar p 0.69). The board's
  view (68 units, Q38U-T2's excluded passes as fails): level, 4 each way.
  The one-pass lead on scored passes comes from units Q38U-T2 never
  measured (3 lost to llama-server's 500, 3 excluded).
- **The gaps are inside the measured noise.** The seed repeat (I_T2_s1)
  flipped 2 of 71 units, and moved SCORE by 1.8 on LANG alone; DeepSeek's
  seed pair moved it by 2.8 with no net unit. 2.6 SCORE points (stock
  against Q38U-T2) is that size. The stock arm is one draw.
- **By the letter of §1's rule** ("unless Inkling's scored row beats
  Q38U-T2"), 53 against 52 is a beat. We do not switch on it: the rule
  was written for a scored lead that means something, and this one is one
  unit of units the other row did not measure, against a paired −2. Max's
  brief for this wrap-up sets the stronger bar above.
- **The engine.** #588 is still in the pin: 105 of the stock arm's turns
  ended empty at the cap (203 and 167 in the two Derisked arms). Of the
  Strata review's §15.8 items, the DNS fix is now proven on three live
  starts and the queue keeps the server log; the #588 fix (re-pin or the
  #632 patch), the pinned Python side and an engine adapter remain.

**Switch rule for the improved arm** (set now, before it runs): Inkling
becomes the 5090 default only if its scored row beats Q38U-T2's **and** the
paired net over the clean units is positive, a seed repeat does not reverse
either, and the §15.8 items are closed. Otherwise Qwen3.8 stays.

**Alternatives considered.** *Switch now on the stock row's 53 and SCORE
78.2*: rejected for the reasons above. *Wait for I_UP8k before deciding*:
not needed for "stay"; I_UP8k is on the Derisked pack and a different
harness, and its row is recorded when it ends.

---

## 7. A seed replicate is listed and never ranked (`replicate_of`)

**Decision.** A board run that repeats another run's configuration under a
new seed names it with `"replicate_of"` in `board.json`. It is scored and
listed with every run (docs/RESULTS.md, the token table, the website's
compare page) and never chosen as its (model, weights)'s best row.
INK-T2-S1 is `replicate_of: INK-T2`; DS-Ts1 is marked `replicate_of: DS-T`
for consistency (no change: DS-T2 was and is DeepSeek's row).

**Reason.** INK-T2-S1 (78.3) scores above INK-T2 (76.5). Under the old rule
it would have become the Derisked pack's row, ranked above INK-T2-STOCK
(78.2): the board would then rank the better of two draws of one setup,
a selection effect no single-seed row gets, and the 5090's top row would
have been chosen by which seed came out better. The pre-planned draw (the
first) is the one that ranks. Max's brief: "probably not a ranked best row,
but recorded".

**The code.** `evals/v5_board.py`: `best_per_model` skips a run with
`replicate_of`; `weights_problems` requires it to name another run of the
same model and weights that is not itself a replicate; the run table's
status says "seed replicate of …: never the ranked row". Tested
(`tests/test_v5_board.py`).

**Alternatives considered.** *Rank the mean of the seeds*: better
statistics, but the board's rows are single runs with their files, and only
two models have a second seed; revisit when seed repeats are routine.
*Keep the old rule (best of everything)*: rejected, above. *Leave the
replicate off the board*: rejected; every measured run is recorded.

---

## 8. The stock pack is its own board row; the next Inkling arm runs on it

**Decision.** `INK-T2-STOCK` is a board row of its own model and weights
("Inkling 2 Mini (Basalt NVFP4-MX8)", `inkling2-mini-basalt-nvfp4-mx8-stock`),
ranked by SCORE like every row: #4, above INK-T2 (#5) and Q38U-T2 (#6), so
it is now the RTX 5090's top board row and the website's per-rig pick
(§3's rule, unchanged), still not the default. The improved arm (§6) runs on
the stock pack.

**The weights entry.** On the Hub: `jesdga/Inkling-2-Mini-Basalt` @
`c2afac3a…`, file `Inkling-2-Mini-NVFP4-MX8.basalt` pinned by sha256
`28ec7072…` (`file-sha256` with its repo and commit, as Qwen3.8's GGUF).
The pack and its README do not record the Thinking Machines release
revision or the converter commit they were built from: both `unverified`,
never guessed. The engine is Basalt `400b411c`, whose own docs name this
pack and sum. Re-hashed on the rig after the arm.

**Why the stock pack for the next arm.** The measured direction favours it:
+4 and +2 against the two Derisked draws (3 units in both views), about
half the empty capped turns, half the divergence from the reference
(KLD 0.0128 against 0.0263). None of it is significant, so the
uncensored-preferred policy is not overturned in general; it is a
tie-breaker, and here the evidence leans the other way. The Derisked pack
stays measured (INK-T2, INK-T2-S1, I_UP8k).

**Alternatives considered.** *One "Inkling 2 Mini" row with the better
pack*: rejected, a row is a model and its exact weights (`weights.json`).
*Keep the improved arm on Derisked for the uncensored policy*: rejected for
this arm; it would test the #632 patch on the pack with more empty turns
and the weaker paired record.

---

## 9. Read the kept Basalt log with a committed tool, and replay both dlsym stops

**Decision.** (a) Read the saved Basalt server logs of the ablation arms
with a new committed tool, `evals/audit/basalt_log.py`, over each arm's
campaign window, and report the live decode rate and DSpark's draft
acceptance from it. (b) Replay both arms' `122_gemm_blocked_d` dlsym stops
now, from a new directory on the rig (`~/ob-cal/.claude/gemm-replay-ink-ab/`).

**Reason.** (a) The I_T2 wrap-up asked for the log to be kept (§5's
follow-up); the queue's log saver did, and the log is the only record of
per-reply decode speed and acceptance. A committed tool with a test makes
the numbers reproducible. It also cross-checks `capped_turns.py`: on both
arms the log's request count, cap endings, "no answer" warnings and token
sums equal the transcripts' and the runner's exactly. (b) As §4: a verdict
beats a footnote. Neither final `gemm.rs` was a single heredoc this time
(a `write_file`, then Python edit snippets run from bash), so a new
`rebuild.py` re-runs those snippets exactly and checks every printed
output; #303's grading step is unchanged. Done while I_UP8k ran: each step
a `--rm` eval-host container, `nice -n 19`, `--cpus 8`, about 3 s each,
with its own `/tmp` (no fixture shared with the running arm); the queue
checks for eval-host containers only before launching an arm, and I_UP8k
was its last. Result: control FAIL as recorded, proposed rule PASS 3 of 3,
in both.
