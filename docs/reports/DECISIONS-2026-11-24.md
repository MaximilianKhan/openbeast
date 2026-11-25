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
10. [Stock Inkling 2 Mini on Basalt is the RTX 5090 default](#10-stock-inkling-2-mini-on-basalt-is-the-rtx-5090-default)
11. [Basalt PR #632 as our local patch for Inkling](#11-basalt-pr-632-as-our-local-patch-for-inkling)
12. [The improved Inkling arm I_T3: turns, cap, patch](#12-the-improved-inkling-arm-i_t3-turns-cap-patch)
13. [I_UP8k: UP_8k's setup is not adopted for Inkling; no forced-act arm on Basalt](#13-i_up8k-up_8ks-setup-is-not-adopted-for-inkling-no-forced-act-arm-on-basalt)
14. [A listing of the answer tree is a contamination, even when nothing was read](#14-a-listing-of-the-answer-tree-is-a-contamination-even-when-nothing-was-read)

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

---

## 10. Stock Inkling 2 Mini on Basalt is the RTX 5090 default

**Decision.** Inkling 2 Mini, Basalt's stock NVFP4-MX8 pack
(`jesdga/Inkling-2-Mini-Basalt` @ `c2afac3a`, `Inkling-2-Mini-NVFP4-MX8.basalt`,
sha256 `28ec7072…1b99`), served by Basalt v0.5.7 (`400b411c`) with
`AGENT_PROFILE=inkling-best`, is the RTX 5090's default model and agent
setup: exactly board row `INK-T2-STOCK`. **It supersedes §6's switch rule**
(and §1's "Qwen3.8 stays"). Qwen3.8 27B Uncensored MTP Q5_K_M on llama.cpp
with `qwen38-best` becomes the fallback and the rollback path; it stays
measured and listed.

**When / who.** Max, 2026-11-24: "the stock inkling 2 mini should be the
default for the 5090", and the website's benchmarks page must reflect it
"along with its scoring inclusion and all the latest runs". The repo change
is the switch date: `main` ships it from this entry's PR. The reference rig
applies it when its GPU frees up (I_UP8k is running on Basalt, and the
improved #632 arm is being prepared); Max applies it by the steps in
[MODELS.md](../MODELS.md#switching-the-rtx-5090-to-its-default-and-back),
and [TODO.md](../TODO.md) holds the item with what to check.

**What the repo now does.**

- `scripts/engines/basalt.sh`: the engine adapter, closing the Strata report
  §15.8 items (a reviewed, offline source build; the #632 patch hook; the
  Python side pinned in a distro-package image; the hardened serve with a
  DNS probe at every launch). How each is verified:
  [ENGINES.md](../ENGINES.md#basalt-the-rtx-5090-default).
- Profile `scripts/engines/basalt/profiles/inkling2-mini-stock.env` (the
  I_T2_stock serve: `--parallel 1 --max-context 262144 --kv fp8`, DSpark at
  the pack default, answer room auto), the pack pinned in
  `scripts/weights.registry`.
- Preset `AGENT_PROFILE=inkling-best`: T2's shared switch table (I_T2_stock
  is T2 at one stream, tested against the campaign).
- The default rule: `evals/boards/v5/board.json` `"defaults"` names each
  platform's shipped row (validated by `evals/v5_board.py`, rendered in
  [RESULTS.md](../RESULTS.md#shipped-default-per-platform)); `engine.sh use
  default` on an RTX 5090 picks `basalt` and `inkling2-mini-stock`. An unset
  `openbeast.conf` still launches llama.cpp, so a fresh install has a model
  before Basalt is built.

**The case for** ([V5_INKLING2_MINI_RTX5090-2026-11-24.md](V5_INKLING2_MINI_RTX5090-2026-11-24.md) §10):

- **The top RTX 5090 row.** 53/71 scored, SCORE 78.2 (SOLVE 75.4, LANG
  86.7), #4 on the v5 board; Qwen3.8's Q38U-T2 is 52/71, SCORE 75.6 (#6).
- **About 3× faster.** The stock arm ran in 4 h 18 min (14:07:39 to 18:25:17
  UTC) against about 13.5 h for Q_T2 (11.2 h main + 2.3 h repair run). On
  replies of exactly 8,192 tokens the end-to-end rate was median 510 tok/s
  against 142 for Q_T2 on llama.cpp (I_T2's 520 in §3.3); Basalt's own log:
  444 tok/s decode over all replies, 88.7% of drafted tokens accepted
  (§10.6).
- **No unit lost, none excluded, 0 API errors**, where Q38U-T2 lost 3 units
  to llama-server's HTTP 500 on a truncated tool call and had 3 integrity
  exclusions.
- **Within noise of the previous default on the units both measured.** Over
  the 65 clean pairs: Qwen3.8 52, stock Inkling 50 (2 gained, 4 lost, −2,
  McNemar p 0.69, MDE 10); on the board's view (68 units) level, 4 each way
  (§10.4). The measured seed churn is 2 of 71 units (§10.2).

**The case against, recorded:**

- **Paired −2 on the 65 clean units.** The one-pass lead on scored passes is
  made of units Q38U-T2 never measured (its 3 lost and 3 excluded units, of
  which the stock arm passed 3). Not a measured gain in capability.
- **Single seed.** The stock arm is one draw; SCORE gaps under about 3
  points between single-seed rows are inside one seed's noise (§10.2), and
  78.2 against 75.6 is 2.6.
- **Basalt is young.** Created 2026-10-06, about 3,500 commits in 60 days,
  0.5.5 → 0.5.7 in two days, 98 open issues at review (Strata report §15.2).
  Upstream #588 is in the pin: 105 of the stock arm's 2,032 turns ended
  empty at the 8,192 cap (5.2%); the #632 patch that would fix it is not
  merged upstream and was not yet on `main` here when this was decided.
  *(Later the same day it landed on `main` as the local patch, §11: a
  build of the 5090 default is now the pin plus the patch, so it differs
  from the measured pin of the stock row.)* Its tool-call parsing is more
  lenient than llama.cpp's (83 capped turns still carried a call), which
  can only raise the call success rate. A model-plus-engine change: weights,
  engine, thinking room (6,144 against 8,192) and parser all differ from
  Q38U-T2 at once.
- **The adapter is new.** Its hardening is tested against a fake docker and
  the network fix is proven live, but `--pids-limit`, `--memory`, a noexec
  `/tmp`, the toolkit `--gpus` path and the pinned Dockerfile build have not
  run on the rig (ENGINES.md, the table).
- **The stock pack is not uncensored.** The fleet policy is
  uncensored-preferred as a tie-breaker; here the measured direction
  favoured the stock pack (§8), and Qwen3.8 Uncensored stays available as
  the fallback.

**Rollback.** Qwen3.8 on llama.cpp: `scripts/engine.sh use llama
--serve-script serve-qwen38-27b-uncensored-mtp-q5.sh` and
`AGENT_PROFILE=qwen38-best` (or the conf backup `engine.sh use` takes), offline
and immediate; the Basalt build, image and pack stay on disk.

**Alternatives considered.** *Keep §6's rule (wait for the improved #632
arm and a seed repeat)*: Max's call to override it; the improved arm and
its seed repeat still run and are recorded, and a patched build is one
reinstall through the adapter's hook. *The Derisked pack as the default*:
it measured below the stock pack in both draws (−4, −2) with about twice
the empty capped turns (§8). *Switch the reference rig now*: no, it is
running I_UP8k on Basalt; Max applies the switch when the GPU frees up.

---

## 11. Basalt PR #632 as our local patch for Inkling

**Decision.** Whenever we serve Inkling 2 Mini on Basalt, serve it from the
v0.5.7 tree with upstream PR #632 applied as our own patch
(`scripts/engines/patches/basalt-pr632-55f9be17.patch`, pinned to the PR
head `55f9be172f247e50b623295662e2a5babbfbeeaa`, body sha256
`af15ee07ecddababe54b57ae68b78b0d526602156332333ae50ba3f36748c0cd`),
until upstream merges #632 and we re-pin past it. The patched tree is made
offline by `scripts/engines/basalt-apply-pr632.sh` as a copy
(`build/basalt-tree-pr632`); the unpatched tree stays for controls
(`inkling-v5.sh start <pack> --patch none`). On `main` the same file is what
the adapter's hook applies (`scripts/engines/basalt.sh`, §10), so a build of
the 5090 default is the pin plus this patch from now on.

**When / who.** Max, 2026-11-24 (~09:15 PST, through the coordinator).
Timing rule with it: nothing the running ablation queue uses was changed
until `QUEUE DONE`, so `I_UP8k` ran on the same unpatched Basalt as
`I_T2`, `I_T2_s1` and `I_T2_stock`; the patched path became
`inkling-v5.sh`'s default only after that.

**Reason.** Upstream #588 is the largest single waste in the Inkling rows:
203 (`I_T2`) and 167 (`I_T2_s1`) empty capped turns, 10.5% and 8.7% of all
turns; in `I_T2_s1` the server log's "no answer" count equals the
transcripts' (167), and 167 of its 491 budget wraps ended that way. The
fix is two behavioural lines in the Python server, reviewed line by line
([Strata report §16](STRATA_INKLING_5090-2026-11-23.md#16-openbeasts-local-patch-for-upstream-588-pr-632)),
it applies to the pin exactly (the patched files equal the PR head's
blobs), it needs no engine rebuild (the server is mounted from the build
tree, not baked into the image), and Basalt's own `serve/tests` give the
same results with and without it apart from its one added test, which
passes. §15.8 item 2 asked for exactly this before Basalt can be a default; §10's
adapter was built with the hook waiting for this file.

**Alternatives considered.** *Re-pin to Basalt `main`*: rejected; #632 is
not merged there either, and `main` has moved 39 commits past v0.5.7 with
new Inkling kernels (INT8 KV attention, expert spill), an unreviewed engine
change far larger than the fix. *Build the PR head*: rejected; its base is
older than v0.5.7 (173 files differ). *`--answer-room 0`*: rejected; with
no reserve there is no wrap at all, so every long think runs to the cap,
the llama.cpp behaviour (Q_T2: 15% of turns empty at the cap), while the
wrap already turns 52% of long thinks into a tool call. *Wait for
upstream*: rejected by Max; the fix is small and the cost is measured.

**Risk accepted, then checked.** After a wrap the model now has to write
answer text before a call (Strata report §16.4). Checked live after the
queue (§16.6): on requests rebuilt from turns that went empty, the
unpatched server gave 5 empty replies of 16 on the one that reproduces
#588 and the patched 0 of 16 (p 0.043); every patched agent reply made a
tool call (48 of 48, unpatched 43); conformance PASS at c=1 and c=4. At
`--answer-room auto` the patched replies often ran out of room while
writing the call, which is why `I_T3` pairs the patch with
`--answer-room 8192` (§12).

---

## 12. The improved Inkling arm I_T3: turns, cap, patch

**Decision.** Stage arm `I_T3` (campaign `v5-thinking-first`): `I_T2_stock`
(T2's switches at one stream on Basalt's stock pack, the 5090 default's
row, §8, §10) with three changes: `--turns-per-wall-s 5` (180 turns for a
900 s unit; the wall unchanged at ×2), a 16,384-token per-turn cap, and
the serve's `--answer-room 8192` (thinking ≤ 8,192, answer ≥ 8,192), on
the PR #632-patched Basalt (§11). Harness9 tree, one stream. Launcher
`~/openbeast-runs/ink-I_T3-run.sh`, ready, **not launched**: Max launches.
*(Status, added after merge: **RUNNING** since 2026-11-24 16:29:10 PST,
launcher pid 7275, `PACK=stock`, gate passed 16:30:37, campaign ETA
~11.5 h; live paths, stop-safely and restore-by-hand steps in TODO.)*
Follow-ups defined and not queued: `I_T3_t15` (turns back to 15 s),
`I_T3_8k` (cap back to 8,192, answer room `auto`), and a seed repeat (§6's
rule asks for one).

**When / who.** Max asked for it 2026-11-24 ("look at all our ablations and
create an improved test run which will hopefully bring completion parity
or above to Qwen 3.8"; §6: "stock + the Basalt PR #632 local patch");
designed after the ablation queue ended (14:28 PST).

**Reason.** The four Inkling rows (`I_T2` 49, `I_T2_s1` 51, `I_T2_stock`
53, `I_UP8k` 49) share one failure shape: every failure but the `dlsym`
stop ran out of turns, at a median 23-30% of its wall, with no wall hit in
284 units, and passes that land on the last turn flip between draws. The
turn limit, not the model's time, is binding, against the design of
wall-scaled turns. Two server mechanisms waste the turns it has: empty
capped turns (#588: 105-226 per arm) and calls cut by the cap that the
server still returns and the runner executes (73-88 per arm, 51-82
truncated heredocs; a unit lost to a file it could never write whole).
Each change targets one of these, with its counts, in
[the Inkling report §12](V5_INKLING2_MINI_RTX5090-2026-11-24.md#12-the-improved-arm-i_t3-2026-11-24).

**The pack.** Stock, by §6 and §8 (Max's brief; the better measured draw,
half the empty capped turns). It also makes `I_T3` against `I_T2_stock`
a comparison of the three changes alone (same pack, harness, era). The
launcher's `PACK=derisked` runs it on the Derisked pack instead.

**Left out, and why.** UP_8k's switches: `I_UP8k` tied `I_T2` (49); its
forced-act turn made a call in 18 of 68 turns, because `enable_thinking:
false` is `reasoning_effort: none` on Inkling, not a hard switch (56 forced
turns thought to the cap). Effort `max`: unmeasured, and longer thinking
means more wraps.

**Alternatives considered.** *Only the turns* (the least change): rejected
as the whole arm because the cut-call damage and #588 also cost turns and
time and are measured; the follow-ups separate them afterwards. *A 16,384
cap at `--answer-room auto`* (thinking ≤ 12,288): rejected; the evidence is
about room for the answer, not more thinking, and more thinking means more
wraps. *Turns at 7.5 s* (120): a middle step; 5 s puts every failed unit of
the four rows (8-9 s a turn) on its wall, the design intent of wall-scaled
turns.

**Detectability.** `I_T2` → `I_T2_s1` flipped 2 units (churn ~3%), so a
single `I_T3` row is descriptive unless it moves ~7 units (the MDE at that
churn). Against `I_T2_stock` (53) and Q38U-T2 (52), §6's switch rule
applies as written.
## 13. I_UP8k: UP_8k's setup is not adopted for Inkling; no forced-act arm on Basalt

**Decision.** (a) UP_8k's switches are not adopted for Inkling 2 Mini;
its setup stays T2's (`AGENT_PROFILE=inkling-best`). (b) This confirms
§12's choice to leave them out of `I_T3`; `I_T3` sends no thinking-off
request, so the forced-act defect cannot reach it. (c) No forced-act
(U-type) arm runs on Basalt with Inkling until the forced turn can really
stop thinking.

**Who / when.** The wrap-up of `I_UP8k`, 2026-11-24 evening (it ended
14:28:05 PST, 49/71). Within the standing campaign plan (DECISIONS-2026-11-23
§12 set what I_UP8k was to answer); (a) to (c) change nothing on the rig. §12.6 of the report
(#315) read the arm first for `I_T3`; this entry records the wrap-up's
evidence.

**Evidence** ([report §10.9](V5_INKLING2_MINI_RTX5090-2026-11-24.md#109-i_up8k-deepseeks-up_8k-setup-on-inkling-2026-11-24)):

- **Level with T2 on the same pack.** INK-UP8K 49/71 (SCORE 75.1) against
  INK-T2 49/71 (76.5): over the 70 units both measured cleanly, 3 gained and
  3 lost (McNemar p 1.0, MDE 9.5); against the seed-1 draw (51) −2 (1 in, 3
  out, p 0.63). Every flipped unit is one the seed or pack ablations also
  flipped. Cost on the units both passed: no difference (p 0.88 on tokens).
  DeepSeek's DS-T2 → DS-UP8k is the same picture (65 and 65, 2 each way),
  and GLM's C → UP did not beat either (+2 paired, p 0.69).
- **The forced-act turn did not work on this engine.** Basalt turns
  `enable_thinking=false` into `reasoning_effort "none"` for Inkling (a hint
  in a system message; `serve/models/inkling2.py:527-529`) and gives such a
  request no thinking budget and no answer room (`serve/service.py:1148`,
  1370, 1511). 67 of 68 forced turns still thought, 56 ran to the 8,192
  cap, 50 ended empty, 18 acted (DeepSeek 24 of 24, GLM 22 of 22). Counted
  from the transcripts and, independently, from the server log (68 requests
  without a thinking-budget line; 0 in the T2 arms).
- **`I_T3` does not touch that path.** A T2-family arm never sends thinking
  off (0 such requests in I_T2_s1's and I_T2_stock's logs). #632's answer
  lead acts at the thinking-budget close, which a thinking-off request does
  not have.

**Reasoning.** (a) A bundle that measured level with T2 on passes and
lower on SCORE, on a third model, gives no reason to change a setup that is
measured and shipped. (b) Adding UP's switches to `I_T3` would confound
its three changes (§12) with a mechanism that does not run on this engine.
(c) A forced turn that thinks
unbudgeted is worse protected than an ordinary turn (82% at the cap against
13%); running it again would measure the engine's thinking-off semantics,
not the harness idea. The fix is a runner change (send a small explicit
`reasoning_budget_tokens` on the forced turn, which Basalt honours) or an
engine change (a hard stop at effort `none`), each its own arm; recorded as
an IDEA in [HARNESS_NOTES.md](../HARNESS_NOTES.md).

**Alternatives considered.** *Rerun UP_8k on the stock pack*: rejected, the
forced turn would fail the same way, and the T2 comparison it would add is
already level on the Derisked pack. *Read I_UP8k as evidence against the
forced act in general*: rejected, the turn did not run as designed here, so
the arm says nothing about it; DeepSeek's U_8k (63 against T2's 65, where it
did run) is the only measurement of it. *Rank INK-UP8K*: not available, the
board ranks one row per model and weights (INK-T2, 76.5, is the Derisked
pack's best).

---

## 14. A listing of the answer tree is a contamination, even when nothing was read

**Decision.** `163_bigint_divmod_c` in INK-UP8K is excluded as contaminated
(`exclusions.json`): the agent's `find /` printed the file names of the
unit's sealed reference directory; both reads of a file were refused.

**Who / when.** The I_UP8k wrap-up, 2026-11-24, applying the integrity
scan's standing rule (`evals/audit/integrity_scan.py`: a hit other than
`source` and `web` that is not a refusal makes the unit contaminated, "report
it as such and do not count its verdict").

**Evidence** (sealed review `evals/audit/reviews/ink-up8k-integrity-2026-11-24.md`):
the listing named five files, two of whose names describe failure modes;
the transcript never refers to them again, and the unit failed at its turn
limit. Score effect: none (49 raw, 49 scored). The same check over every
other rig arm's transcripts (I_T2, I_T2_s1, I_T2_stock, Q_T2, the
calibration, Q38U-L0; 503 transcripts) found no other result that printed a
sealed path; Q_T2's rerun ran `find /` twice (Rust sources) without one. Pair effect: the unit is
dropped from every paired test (`ablation.py` reads the same file).

**Reasoning.** The rule does not weigh what a leak was worth, so no
judgement about "only file names" enters a score; and two of the names
describe failure modes (named only in the sealed review), which is a hint. Excluding a fail costs nothing and keeps the
rule exact. The cause is the documented Linux residual gap (the agent's own
shell has no read-deny there; evals/README.md), now seen live on the rig for
the first time; its next-era fix is in HARNESS_NOTES.

**Alternatives considered.** *Keep the unit (nothing was read)*: rejected,
it would make the rule a judgement call. *Re-run the unit*: rejected, a
rerun only replaces infrastructure rows (`v5_board.py` merge rule), never a
real verdict.

