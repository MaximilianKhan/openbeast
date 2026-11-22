# openbeast.ai update, 2026-11-21: handoff for the website agents

**Status: plan, ready to implement.** Written 2026-11-21 for the agents that
maintain openbeast.ai (the private repo `MaximilianKhan/openbeast-website`).
It **extends** [`WEBSITE_UPDATE_2026-11-20.md`](WEBSITE_UPDATE_2026-11-20.md),
which extends [`WEBSITE_UPDATE_2026-11-19.md`](WEBSITE_UPDATE_2026-11-19.md).
**Neither earlier handoff has been applied** ([§1](#1-status-whats-live)).
Apply all three in one website PR and one deploy, in order: 11-19, then
11-20, then the deltas here. Where they differ, the newest page wins. The
master plan is [`WEBSITE_PLAN.md`](WEBSITE_PLAN.md); its accuracy rules
([§3](WEBSITE_PLAN.md#3-accuracy-rules)) still apply, and so do 11-19's
rules A to E and 11-20's amendments C1 to C4, E1 to E3 and F.

**What this page adds.** DeepSeek-V4.1-Flash (Mia EXL3) ran four more
campaign arms (U, UP, UPA, U_8k), so its ladder grows from four rungs to
eight, crosses a second harness version, and has something to teach: the
per-turn thinking cap matters most, short planning guidance helped, an extra
procedure pack hurt, and a forced action turn did not beat the nudge. T2
stays the headline. This page gives the copy for that, a chart spec for
it, the rules for phrasing small differences, and the gate that keeps runs
without a board row off the site.

**The board gate for this page: satisfied.** The four new arms' board rows
(`DS-U`, `DS-UP`, `DS-UPA`, `DS-U8k`) landed on OpenBeast `main` in #254
(`bfd605eb`, 2026-11-21) while this page was being written, and the numbers
here are the board's ([§5.0](#50-board-state)). So the eight-rung ladder may
ship. The gate still applies to everything after them: **UP_8k and UPA_8k
have no board rows and do not appear on the site in any form**
([§2.2](#22-rule-amendments) C7). The website agents take every number from
the resync (`results-build.json`), never from this page, and the build
refuses a rung whose row is missing ([§3.8](#38-code-changes-amends-11-20-38)).

**How this page was made.** On 2026-11-21 (about 18:30 UTC) every sitemap
page and `/llms.txt` were fetched with `curl -sL`. The website repo was
cloned read-only to `/tmp` at `e72790c`. The board numbers come from
`evals/v5_board.py --json` on OpenBeast `main` at `bfd605eb` (#254); the
data blocks in [§5](#5-data) were generated from that JSON by a small script
(its source is in [§5.4](#54-how-the-blocks-were-generated)). File and line
references are to website commit `e72790c`.

**Labels** are 11-19's and 11-20's: **MEASURED**, **SHIPPED (upstream
main)** / "UPSTREAM MAIN · after v1.9", **PLANNED**, **PROPOSED** (a website
change Max has not approved). This page adds two:

- **PENDING BOARD MERGE**: copy that is ready but may ship only once the
  named board rows exist on OpenBeast `main`. On this page it marks only
  what waits for UP_8k and UPA_8k; the four arms U to U_8k are on the board.
- **UNVERIFIED-for-site**: a number reported from a finished run that has
  no board row yet. It never goes on the site. (Nothing on this page
  carries it any more: the four new arms' counts were checked against
  their board rows.)

| Section | What it gets you |
|---|---|
| [1. Status: what's live](#1-status-whats-live) | re-checked: nothing from 11-19 or 11-20 is live |
| [2. What changed since 11-20, and the rule amendments](#2-what-changed-since-11-20-and-the-rule-amendments) | the new arms, what they taught, the phrasing rules |
| [3. Delta changes, page by page](#3-delta-changes-page-by-page) | live text, replacement, source, on top of 11-20 |
| [4. Updated proposals](#4-updated-proposals-p1p4-u1u6) | P1 with the eight-rung ladder and a chart spec; P2 to P4, U1 to U6 as amended |
| [5. Data](#5-data) | the board state, the generated blocks, the gated rows |
| [6. llms.txt](#6-llmstxt-delta-on-11-20-62) | the Evaluation lines that change |
| [7. Acceptance checklist](#7-acceptance-checklist) | 11-19's and 11-20's steps plus the new greps |
| [8. Open questions for Max](#8-open-questions-for-max) | Q-A to Q-L carried over, Q-M to Q-S new |

---

## 1. Status: what's live

**Nothing from the 11-19 or the 11-20 handoff is live.** Evidence,
2026-11-21:

- The live build is unchanged: `last-modified: Wed, 18 Nov 2026 01:37:35 GMT`
  on `/`, the header both earlier handoffs recorded.
- The website repo's `main` is still `e72790c`. Its only branches are
  Dependabot's and the two merged v5 branches; its open PRs are Dependabot's
  #15 and #19. There is no branch or PR for the update.
- Every stale string the earlier handoffs target is still served:

| Stale string | Live on (2026-11-21) | Fixed by |
|---|---|---|
| "TensorFold serves GLM-5.3-Flash …" | `/`, `/hardware/`, `/llms.txt` | 11-19 H1, W1, llms 8 |
| "GLM-5.3-Flash: 26/71 baseline, 51/71 scored." | `/` | 11-19 H2, 11-20 H2 |
| "DeepSeek v4.1 Flash" (wrong name, and "is next") | `/benchmarks/`, `/research/harness/`, `/research/campaign/`, `/llms.txt` | 11-19 B7, R2, R5, llms 17; 11-20 B7, R2, R5 |
| "One local model has been measured." | `/research/harness/` | 11-20 R2 |
| "Uncensored-only fleet policy" | `/investors/investment-case/` | 11-19 I1 |
| "The default weights on the reference systems are abliterated." | `/llms.txt` | 11-19 llms 22 |
| "The operator controls the choice of abliterated weights." | `/hardware/` | 11-19 W4 |
| "First rows measured on the DGX Spark pair for one model, single seed." | `/investors/` | 11-19 V1 |
| the GLM-E footnote quoting the lint's match ("dlopen") | `/benchmarks/`, `/research/harness/` | 11-19 B8, R4 (Rule E) |

- No page carries "DeepSeek-V4.1-Flash (Mia EXL3)", `65/71`, `T_s1`, `T3`,
  "uncensored-preferred", `harness10`, `59/71`, `UP_8k` or "Hy4".

**So 11-19 §3 to §6 and 11-20 §3 to §6 are all pending**, and so is the
website `AGENTS.md` update (rules A to F). The Rule E leak (the GLM-E
footnote) has now been public for four days. It is still the first thing to
fix, and it does not need to wait for anything on this page: if the full
update can't ship soon, ship 11-19 B8 and R4 alone.

**The cold start got worse.** The first requests for `/` and `/benchmarks/`
answered Cloudflare **502** again, and this time they kept answering 502
through four retries spaced 8 s apart (about 40 s); `/` answered 200 a
minute later, and every other page answered 200 first time. A `HEAD /`
answered 200 while a `GET /` answered 502, so the tunnel was up and the
origin was slow to render or serve. That is three days running (11-19,
11-20, 11-21), and a visitor who arrives first after idle now sees an error
page. This is 11-20 Q-L, escalated ([§8](#8-open-questions-for-max) Q-L).

---

## 2. What changed since 11-20, and the rule amendments

### 2.1 The new facts

1. **Four more DeepSeek arms are on the board** (harness 10, single seed
   each; board rows added in #254):

   | Arm | Run id | Built from | One change | Cap | Passed | SCORE | Integrity scan |
   |---|---|---|---|---:|---:|---:|---|
   | U | DS-U | T2 | a forced action turn replaces the nudge, with a stricter done check and no web tools offered; the cap goes back to 16,384 | 16,384 | 59/71 | 85.5 | 0 contaminated |
   | UP | DS-UP | U | short planning guidance in the system prompt | 16,384 | 62/71 | 88.0 | 0 contaminated |
   | UPA | DS-UPA | UP | an extra procedure pack | 16,384 | 59/71 | 83.8 | 0 contaminated |
   | U_8k | DS-U8k | U | U at T2's 8,192-token cap | 8,192 | 63/71 | 88.5 | 0 contaminated |

   The rung label and the run id differ for U_8k (`DS-U8k`, no underscore),
   as they do for T_s1 (`DS-Ts1`). The board still ranks `DS-T2` first.
   Source: board JSON; [MODEL_CAMPAIGN.md §5b](MODEL_CAMPAIGN.md#5b-the-arms),
   [the interim campaign report §3.2](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md#32-the-arms).
2. **What the arms taught** (the comparisons, each within one harness
   version unless marked):

   | Question | Comparison | Delta | Reading |
   |---|---|---:|---|
   | Does the per-turn thinking cap matter? | U → U_8k (16K → 8K, same era) | +4 | **yes, the biggest lever.** It repeats T → T2's +4 (16K → 8K, which crossed an era) |
   | Is a shorter cap better still? | T2 → T3 (8K → 4K, same era) | 0 | no: three units in, three out, lower SCORE; 8K stays |
   | Do planning lines help? | U → UP (same era, 16K) | +3 | **yes**, outside the ±1 noise |
   | Does an extra pack help on top? | UP → UPA (same era, 16K) | −3 | **no, it hurt** |
   | Does a forced action turn beat the nudge? | T2 → U_8k (same cap, crosses harness 9 → 10, and U also changed two smaller switches) | −2 | **no**: it did not beat the nudge. −2 is inside twice the noise, so "not better", never "worse" |
   | Is T2 still the best? | every arm against T2 | — | **yes**: no finished arm with a board row passes more than 65/71 |

   Source: the deltas are board pass counts (§5.2); the readings match [HARNESS_NOTES.md](HARNESS_NOTES.md) and the interim
   report's [§6.2](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md#62-the-cap-trade-off)
   and [§6.3](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md#63-what-u-taught)
   for the arms it covers.
3. **Status update (2026-11-21, after this page was written): UP_8k and
   UPA_8k finished.** UP_8k 65/71 (SCORE 91.4), UPA_8k 62/71 (SCORE 88.0);
   integrity scans clean; board rows DS-UP8k and DS-UPA8k land on OpenBeast
   `main` with the branch `board/ds-8k-series`. Until that merge is on
   `main`, C7 holds: **neither may appear on the site in any form**
   ([§2.2](#22-rule-amendments) C7). Once it is, they ship together (Q-Q)
   and T2 stays recommended (Q-O: UP_8k ties T2 on passes).
4. **Max's call (2026-11-21): T2 is the best-performing configuration so
   far, and UP_8k may deliver comparable results.** T2 stays the headline,
   the default agent setup (`AGENT_PROFILE=deepseek-best`) and the board's
   rank-1 row. If UP_8k lands level with T2, the board's SCORE ranking
   decides "#1 on the board", but the recommended setup changes only on
   Max's word ([§8](#8-open-questions-for-max) Q-O).
5. **Eras: the ladder now spans three harness versions.** An era is a hash
   of every file that shapes the agent's environment; a harness version
   names the eras that changed the agent-shaping code
   ([report §3.4](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md#34-eras-and-harness-versions)).

   | Harness | What it added (as switches, off by default) | DeepSeek arms |
   |---|---|---|
   | 8 (with the review-1118 sandbox) | the hardened sandbox | T |
   | 9 | the per-turn cap and the act nudge as switches, partial stats on a wall kill, an eval fetch guard | T_s1, T2, T3 |
   | 10 | the forced action turn, a stricter done check, the planning guidance, the procedure pack, web tools not offered | U, UP, UPA, U_8k (and UP_8k, UPA_8k) |

   The clean comparisons are inside one version: T2 → T3 (harness 9);
   U → UP → UPA and U → U_8k (harness 10). Two steps cross a version and
   say so: T → T2 (8 → 9; T_s1, T's setup on harness 9, scored the same
   61, so the new version did not move T) and T2 → U (9 → 10, several
   changes at once; U_8k is the clean cap test).
6. **Hy4-flash (NVFP4, vLLM TP2) is staged to run after the DeepSeek arms**,
   as a public-interest measurement (arm `H_T2`, T2 exactly). No result.
   11-20 C3 and Q-I stand.
7. **Mia's new GLM recipe (1104, on TensorFold 1.3.0) is to be tested
   last**, after Hy4-flash. No result, no doc. 11-20 C3 and Q-J stand.
8. **`deepseek-best` is live on the pair.** Since #255 (merged
   2026-11-21), [SPARK_HYDRA.md E2b](SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2)
   records that `AGENT_PROFILE=deepseek-best` has been in spark-1's
   `openbeast.conf` since 2026-11-19 (about 14:50), applied in the gap
   after arm T_s1; [MODEL_CAMPAIGN.md §5b](MODEL_CAMPAIGN.md#5b-the-arms)
   agrees. 11-19 N2's caveat ("don't say the live pair runs it yet") is
   lifted: see [§3.3](#33-hardware-srcapphardwarepagetsx) N2.
   The same PR scoped the board's four-unit older-validator footnote to
   the runs judged before the fix (the GLM rows, the reference and DS-T);
   if the site shows that footnote (11-19 §4.1 says it needn't), it goes on
   those rows only, as a count.
9. **Unchanged since 11-20:** the default model and its serving facts,
   v1.9.0 as the latest tag, backup and restore still PLANNED, native
   TensorFold 1.2.10 installed and not serving, the RTX 5090 facts, the
   uncensored-preferred policy.

### 2.2 Rule amendments

11-19's rules A to E and 11-20's amendments stand. These amend them.

**C. Numbers** (adds to 11-20 C1 to C4):

- **C5. Phrasing by size.** Every v5 row is one seed, and the one measured
  repeat moved by about one unit. So:

  | Difference in passed units | Say | Never say |
  |---|---|---|
  | 0 or 1 | "the same within noise", "tied", "no measurable change" | better, worse, improved, regressed |
  | 2 | "did not beat", "did not improve on", "slightly ahead/behind, within noise" | better, worse, beat, lost to |
  | 3 or more, same harness version | "helped", "hurt", "gained N", "lost N", always with "single seed" nearby | significant, proves, conclusive, statistically |
  | any, across harness versions | the same words as above, plus "across a harness version" (or the ladder's era column) | a clean one-variable claim |

  A SCORE difference without a units difference is not a finding: T and
  T_s1 differ by 2.8 SCORE on the same 61/71. Don't cite SCORE deltas in
  prose at all; the ladder shows them.
- **C6. Headline and best.** "#1 on the v5 board" and "recommended setup"
  name **T2** (65/71, SCORE 94.9). No other arm is called best, leading,
  or close to the best in a headline, until it has a board row and Max says
  so.
- **C7. Board rows only, strictly.** 11-20 C2 stands and is now enforced in
  the build ([§3.8](#38-code-changes-amends-11-20-38)). **UP_8k and UPA_8k
  do not appear anywhere on the site, in any form, until they have board
  rows** (no name, no "running" line naming them, no partial count, no
  "tracking"). The generic line "further harness arms are running" stays
  allowed.
- **C8. Arms that teach together ship together.** U, UP, UPA and U_8k
  teach together (the cap result needs U_8k; the pack result needs UP).
  All four rows landed in #254, so this is satisfied for them. It applies
  again to UP_8k and UPA_8k: ship them together, with their own one-change
  text and lesson, unless Max says otherwise.

**E. Eval safety** (extends 11-20 E3):

- **E4. Name the switch, never its contents.** The site may say "short
  planning guidance in the system prompt" and "an extra procedure pack".
  It may not quote, paraphrase, summarise or hint at what either says,
  how many lines the guidance has, what kind of problems the pack covers,
  its size, its file name or its hash. "Planning lines" and "algorithmic
  procedure pack" are the repo's internal names; on the site, use the
  wording above (the 11-20 N3 grep already bans the internal names).
- **E5. Why something helped or hurt stays general.** The site may say the
  cap helped and the pack hurt. It may not say which kind of unit gained or
  lost, or why (that is the solution-path detail the DS-T3 note already
  carries, 11-20 E1).

- **E6. The board's own labels for the new rows leak.** The board notes
  of `DS-U`, `DS-UP`, `DS-UPA` and `DS-U8k` list failed units by id, and the
  configuration labels of `DS-UP` and `DS-UPA` use the internal names
  ([§5.0](#50-board-state)). Render neither verbatim: the status label
  before the first `;`, the integrity result as a count, and the
  configuration in this page's one-change wording (`deepseekLadder`'s
  `change`, [§3.8](#38-code-changes-amends-11-20-38)).

**G. Eras on the site (new, PROPOSED with P1).** The site may show the
harness version as a plain label ("harness 9", "harness 10") in the ladder's
era column and in one explanatory sentence. It still never prints era
hashes or internal version strings (`harness9`, `06c82c43…`). This amends
11-20 P1's implementation note, which banned version strings outright,
because the ladder now crosses two versions and a reader needs to see where
([§8](#8-open-questions-for-max) Q-M).

---

## 3. Delta changes, page by page

Each row amends an 11-20 row (its id in the first column) or is **new**
(an id starting with `E`). "Live" is the text served on 2026-11-21; where it
is the same as 11-19 or 11-20 quoted, the row says so. The four new board
rows landed in #254 (`bfd605eb`), so every row here is ready to ship.

### 3.1 Home `/` (`src/app/page.tsx`)

| # | Section | Live (exact) | Replacement (ready to paste) | Source |
|---|---|---|---|---|
| H2 | 03 "Evidence" | as 11-19 H2 (re-verified) | **11-20 H2 unchanged.** It names T2's 65/71 and the about-one-unit noise, which stay true. Don't add the new arms here: the home page shows each model at its best only | Board JSON `best`; C6 |
| H3 | ladder chips | as 11-19 H3 | 11-19 H3 unchanged (DS-T2, GLM-C, gold) | Board JSON `best` |

### 3.2 `/benchmarks/` (`src/app/benchmarks/page.tsx`, `src/components/ArmLadder.tsx`)

| # | Section | Live (exact) | Replacement (ready to paste) | Source |
|---|---|---|---|---|
| B3 (amend 11-20 B3) | DeepSeek ladder | (none; GLM's ladder only) | Heading: "DeepSeek-V4.1-Flash (Mia EXL3): eight thinking-first arms". Intro: "DeepSeek-V4.1-Flash (Mia EXL3) ran on the Spark pair with TensorFold and stock EXL3 weights at 2.9 bits per weight. Each arm covers the entire suite and changes one thing from the arm it builds on. T combined the changes that helped GLM, with thinking on. T_s1 repeated T with a different seed, to measure noise. T2 capped each turn's thinking at 8,192 tokens and added a nudge to act. T3 halved the cap. The next four arms ran on a newer harness version: U replaced the nudge with a forced action turn, at the original 16,384-token cap; UP added short planning guidance; UPA added an extra procedure pack; U_8k ran U at T2's 8,192-token cap. Single seed per run; run-to-run noise is about one unit." Rungs from `deepseekLadder` ([§3.8](#38-code-changes-amends-11-20-38)), in run order, each "arm · passed · fraction of gold", with T2 marked "(recommended setup)" and T_s1 "(seed repeat of T)". The era column or chip per rung (G). Gold at 100% | Board JSON runs `DS-T` … `DS-U8k`; [MODEL_CAMPAIGN.md §5b](MODEL_CAMPAIGN.md#5b-the-arms) |
| E1 (new) | "What the DeepSeek ladder taught us", directly under the ladder | (none) | Four items: "**The thinking cap matters most.** Capping each turn's thinking at 8,192 tokens instead of 16,384 gained four units, twice: once for T2 and again for U_8k on the newer harness. Halving it again to 4,096 traded units without a net gain." / "**Planning guidance helped.** Adding short planning guidance to the system prompt gained three units at the 16,384-token cap." / "**More guidance hurt.** Adding an extra procedure pack on top lost those three units again." / "**A forced action turn did not beat the nudge.** At the same cap, forcing the model to act after long thinking turns finished two units behind T2, within noise. T2 remains the recommended setup." Closing line: "Single seed per run; a difference of one unit is noise. We name what each arm changed, not what the guidance says: the suite is a live benchmark." | §2.1 item 2; C5, E4, E5 |
| B7 (amend 11-20 B7) | "What runs next" | as 11-19 B7 (re-verified) | Three items: "DeepSeek-V4.1-Flash (Mia EXL3) ran eight arms. Arm T2 still leads the board and remains the recommended setup. Further harness arms are running; they appear here when they reach the board." / "Tencent's Hy4-flash is staged to run on the same harness after the current arms, as a public-interest measurement. No result yet." / "The RTX 5090 and its Qwen3.8 default still have no v5 result." Keep the paper line | Board JSON; [TODO.md](TODO.md) "Run `H_T2`"; C3, C7 |
| B9 (amend 11-20 B9) | "Measurement limits" | as 11-19 B9 | 11-20 B9's text, with "All four DeepSeek arms" replaced by "Every DeepSeek arm on the board". Ship that only after step M6 ([§7](#7-acceptance-checklist)) confirms each DeepSeek row's board note says 0 contaminated. Add, after it: "The DeepSeek ladder spans three harness versions. Steps inside one version change one thing; the two steps that cross a version are marked." | Board JSON notes (counts only); §2.1 item 5 |
| D1 (amend 11-20 D1) | Run table rows | (GLM rows only) | Add `DS-U`, `DS-UP`, `DS-UPA`, `DS-U8k` after 11-20's four, from the resync. Status column: the label before the first `;`, plus "integrity scan: 0 contaminated" only when the row's note says so | Board JSON |
| E2 (new) | The "How we evaluate" link | (none) | Under "Measurement limits": "How we evaluate: one change per run, a measured noise margin, and the full DeepSeek ladder →" linking `/research/campaign/` (P1) | P1 |

### 3.3 `/hardware/` (`src/app/hardware/page.tsx`)

| # | Section | Live (exact) | Replacement (ready to paste) | Source |
|---|---|---|---|---|
| W7 (amend 11-20 W7) | "Measured capability on the Spark pair" | as 11-19 W7 | 11-20 W7's text with "across four measured arms from 61/71 up" replaced by "across its measured arms" (no count of arms, so the line needn't change when more rows land). The rest unchanged | Board JSON; C6 |
| N2 (amend 11-19 N2) | the `deepseek-best` block | (none) | 11-19 N2's copy, with one sentence inserted before "The serving recipe does not change.": "The Spark pair's own agents have run this setup since November 19." | [SPARK_HYDRA.md E2b](SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2) status (#255) |

### 3.4 `/investors/investment-case/` and `/investors/`

| # | Section | Live (exact) | Replacement (ready to paste) | Source |
|---|---|---|---|---|
| I3 (amend 11-20 I3) | Release record (`investment-case/page.tsx:33`) | "v5 runs for Qwen3.8 and DeepSeek remain open." | "v5 runs for Qwen3.8 remain open. DeepSeek-V4.1-Flash (Mia EXL3) has single-seed rows on the board for each arm of its campaign, including a seed repeat." (No arm count: it changes with every row.) | Board JSON |
| D3 (11-20) | Evidence row "Two DGX Sparks" | as 11-20 D3 | 11-20 D3 unchanged (it says 65/71 and "one seed repeat", both still true) | Board JSON |

### 3.5 `/research/`, `/research/harness/`, `/research/campaign/`

| # | Page, section | Live (exact) | Replacement (ready to paste) | Source |
|---|---|---|---|---|
| R1 | `/research/` | as 11-19 R1 | 11-20 R1 unchanged | Board JSON |
| R2 (amend 11-20 R2) | `/research/harness/` (`harness/page.tsx:31`) | as 11-20 R2 (re-verified) | 11-20 R2's text, with "(arms T, T_s1, T2 and T3)" replaced by "(eight arms across three harness versions)" and "DeepSeek gained from a per-turn thinking cap that GLM never ran" replaced by "DeepSeek gained most from a per-turn thinking cap, a setting GLM never ran; whether the cap transfers to GLM is still open" | Board JSON; [HARNESS_NOTES.md](HARNESS_NOTES.md) (the per-model knob) |
| R5 (amend 11-20 R5) | `/research/campaign/` (`campaign/page.tsx:27`) | "Next: DeepSeek v4.1 Flash. First verify its per-request thinking switch through the actual serving path." | "**Ran: DeepSeek-V4.1-Flash (Mia EXL3), eight arms.** Arm T2 (a per-turn thinking cap and a nudge to act) scored 65/71, #1 on the v5 board, and is the model's recommended setup. A seed repeat puts run-to-run noise at about one unit. Four arms on a newer harness version taught the rest: the cap matters most, short planning guidance helped, an extra procedure pack hurt, and a forced action turn did not beat the nudge. Further arms are running." Links as 11-20 R5. If P1 ships, this paragraph is replaced by P1's copy | Board JSON; §2.1 item 2 |

### 3.6 `/llms.txt`

See [§6](#6-llmstxt-delta-on-11-20-62): two lines of 11-20's full replacement
change.

### 3.7 `e2e/site.spec.ts` (amends 11-20 §3.7)

| Line | 11-20 said | Change to |
|---|---|---|
| 190 | `["T_s1", "61/71", "65/71", "91.5%"]` | also `"U_8k"` and its count (`63/71` at `bfd605eb`). Generate the expected values from `results-build.json` in the test, not as literals, so the test can't drift from the board |
| 465 | the four DeepSeek run rows | also `DS-U`, `DS-UP`, `DS-UPA`, `DS-U8k`, values read from `results-build.json` |
| new | — | `/benchmarks/` and `/research/campaign/` contain "The thinking cap matters most" (once E1 ships) |
| new | — | **Negative, always:** no page and not `llms.txt` contains `UP_8k`, `UPA_8k` or `H_T2` (C7). Lift this test only in the PR that adds their rows |
| new | — | **Negative, always:** the E4 strings of [§7](#7-acceptance-checklist) step M3 appear nowhere |

### 3.8 Code changes (amends 11-20 §3.8)

1. **Resync** at OpenBeast `bfd605eb` (#254, the first commit with all
   four new rows) or later, e.g. the merge commit of the PR that adds this
   page. Pin `results.revision` to it.
2. **`results.ts`: the eight-rung ladder.** Replaces 11-20 step 2. Each rung
   carries its cap and harness version, so the ladder and the chart
   ([§4.1](#41-p1-how-we-evaluate-now-with-the-eight-rung-ladder)) read one
   array:

   ```ts
   // One change per rung, public wording only (Rule E4). Cap in tokens; harness = plain label (Rule G).
   export const deepseekLadder = [
     ["T",    "DS-T",   16384, 8,  "Thinking-first composite"],
     ["T_s1", "DS-Ts1", 16384, 9,  "T repeated with a different seed (noise)"],
     ["T2",   "DS-T2",  8192,  9,  "T plus an 8,192-token thinking cap and a nudge to act"],
     ["T3",   "DS-T3",  4096,  9,  "T2 with the cap halved to 4,096"],
     ["U",    "DS-U",   16384, 10, "A forced action turn instead of the nudge, at 16,384"],
     ["UP",   "DS-UP",  16384, 10, "U plus short planning guidance"],
     ["UPA",  "DS-UPA", 16384, 10, "UP plus an extra procedure pack"],
     ["U_8k", "DS-U8k", 8192,  10, "U at T2's 8,192-token cap"],
   ].map(([rung, id, cap, harness, change]) => ({ ...run(id), rung, cap, harness, change }));
   ```

   `run(id)` already throws when the id is missing, so **the build fails
   until the resync has all eight rows**: that is the board-row gate, in
   code. Never add
   `UP_8k` or `UPA_8k` to this array before their rows exist; when they
   land, they go in with their own one-change text, and the C7 negative
   test is lifted in the same PR.
3. **`best` stays the board's rank-1 row, and the copy is pinned to T2.**
   Tighten 11-20 step 3's assertion from "a DeepSeek row" to
   `if (best.id !== "DS-T2") throw new Error("rank 1 changed: re-read the
   headline copy (WEBSITE_UPDATE_2026-11-21 C6)")`. UP_8k may tie or pass
   T2 (§2.1 items 3 and 4); if a resync changes rank 1, the build stops
   instead of silently putting T2's name on another row's numbers.
4. **Status and notes:** 11-19 steps 4 and 5 and 11-20 step 4, now for
   eight DeepSeek rows. Render the status label before the first `;` and
   the fixed "integrity scan: 0 contaminated" string only for rows whose
   note contains "0 contaminated".
5. **`updated`** in `src/lib/project.ts`: the deploy date (or U5).

---

## 4. Updated proposals (P1–P4, U1–U6)

All **PROPOSED** until Max answers 11-20 Q-G (or says "ship the
proposals"). Only the parts that change are given; everything else in 11-20
§4 stands.

### 4.1 P1. "How we evaluate", now with the eight-rung ladder

**Where:** unchanged (upgrade `/research/campaign/` in place, retitled).

**Copy, ready to paste** (replaces the 11-20 P1 block from "The
DeepSeek-V4.1-Flash (Mia EXL3) ladder" down to "How the numbers stay
honest"; the rest of 11-20 P1 stays). The numbers in the table are what
the resync produces at `bfd605eb`, shown so the agents can check the
render; the page builds them from `deepseekLadder`, never typed.

> **The DeepSeek-V4.1-Flash (Mia EXL3) ladder** (MEASURED, single seed per
> run):
>
> | Step | Built on | One change | Thinking cap | Harness | Passed | Of the gold standard |
> |---|---|---|---:|---|---:|---:|
> | T | — | the thinking-first composite: everything that helped GLM, with thinking on | 16,384 | 8 | 61/71 | 88.5% |
> | T_s1 | T | the same as T, with a different seed | 16,384 | 9 | 61/71 | 85.7% |
> | T2 | T | an 8,192-token cap on each turn's thinking and a nudge to act | 8,192 | 9 | **65/71** | **94.9%** |
> | T3 | T2 | the cap halved to 4,096 | 4,096 | 9 | 65/71 | 91.5% |
> | U | T2 | a forced action turn instead of the nudge, back at 16,384 | 16,384 | 10 | 59/71 | 85.5% |
> | UP | U | short planning guidance in the system prompt | 16,384 | 10 | 62/71 | 88.0% |
> | UPA | UP | an extra procedure pack | 16,384 | 10 | 59/71 | 83.8% |
> | U_8k | U | U at T2's cap | 8,192 | 10 | 63/71 | 88.5% |
>
> **What it taught us.**
>
> - **Noise first.** T_s1 is the control. The same setup, run with a
>   different seed, passed the same count with one unit going each way. So
>   a difference of one unit is noise, and we don't report it as a result.
> - **The thinking cap matters most.** Capping each turn's thinking at
>   8,192 tokens instead of 16,384 gained four units, and it did so twice:
>   T to T2, and U to U_8k on the newer harness. Halving the cap again
>   (T3) traded three units for three others: shorter turns are not better
>   without limit.
> - **Planning guidance helped; more guidance hurt.** Short planning
>   guidance in the system prompt gained three units (U to UP). An extra
>   procedure pack on top of it lost them again (UP to UPA). More
>   instruction is not automatically better instruction.
> - **Forcing action did not beat asking for it.** A turn that makes the
>   model act after long thinking did what it was built to do, but at the
>   same cap it finished two units behind T2's simple nudge: within noise,
>   and not an improvement.
> - **T2 is still the best.** It is the model's recommended setup, and one
>   line in `openbeast.conf` applies it to the stack's own agents. Further
>   arms are running and join the ladder when they reach the board.
>
> **Eras.** The harness is versioned. A change to the code that shapes the
> agent starts a new version, and the board records each row's version.
> Steps inside one version change one thing. Two steps cross a version, and
> the table shows it: T to T2 (and T_s1 shows that the new version alone did
> not move T's score), and T2 to U, where the newer harness changed more
> than one thing at once. That is why U_8k exists: it isolates the cap on
> the newer harness.
>
> **We publish what each arm changed, never what its guidance says.** The
> suite is a live benchmark. Describing the planning guidance or the pack
> would hand the next model the answers.

**The chart: score per arm, grouped by cap, with a noise band.** Below the
table, above "What it taught us".

- **Form:** a horizontal dot plot (one dot per arm, not bars: the axis does
  not start at zero, and bars with a truncated axis exaggerate). Three row
  groups, by cap, in this order: **8,192** (T2, U_8k), **16,384** (T, T_s1,
  U, UP, UPA), **4,096** (T3). Inside a group, run order. A group heading
  per cap ("8,192-token cap"), and the arm label plus a small harness chip
  ("h9", "h10"; Rule G) at each row's left.
- **Axis:** passed units, 55 to 71, ticks every 2, labelled "/71". A thin
  vertical line at 71 labelled "gold standard (71/71, own scaffold)".
  Units are the axis because the noise was measured in units; SCORE goes
  as a text label right of each dot ("SCORE 94.9"), not on the axis.
- **Noise band:** a shaded vertical band from 64 to 66 (T2 ± 1 unit),
  labelled "T2 ± noise (about one unit)". Also a ±1 whisker on every dot.
  A dot inside the band is "the same as T2 within noise"; the caption says
  so.
- **Emphasis:** T2's dot filled and labelled "recommended setup"; every
  other dot outlined. No colour carries meaning by itself (the chip and the
  label say the harness version); the band uses the page's muted surface
  token, so it works in light and dark.
- **Caption, ready to paste:** "Passed units per arm, grouped by thinking
  cap. Single seed per run. The band is T2 plus or minus the measured
  run-to-run noise of about one unit. h9, h10: harness version."
- **Data:** `deepseekLadder` (passed, score, cap, harness). Never typed.
  The group order and membership come from `cap`, so a new rung lands in
  its group automatically.
- **Build:** HTML/CSS like `/hardware/`'s serving chart (no chart library,
  no client JavaScript, CSP untouched). Positions are `left: calc((passed −
  55) / 16 * 100%)`. The table above is the accessible equivalent; give the
  chart `role="img"` and an `aria-label` that reads the eight values, or
  `aria-hidden="true"` with the table as the source.
- **Guard:** an e2e test that the chart has exactly as many dots as
  `deepseekLadder` has rungs, and none labelled `UP_8k` or `UPA_8k` (C7).

**Data sources** (adds to 11-20 P1): board runs `DS-U`, `DS-UP`, `DS-UPA`,
`DS-U8k`; [report §3.4](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md#34-eras-and-harness-versions)
(eras); [MODEL_CAMPAIGN.md §5b](MODEL_CAMPAIGN.md#5b-the-arms) (what each
arm changes, internal wording: translate it per E4).

### 4.2 P2. "Local models we've measured" (amend)

One cell changes: DeepSeek's "Best v5 result" becomes "65/71 (94.9% of the
gold standard), arm T2" (drop "four arms": the arm count changes with every
row). Tencent Hy4-flash's "Status" becomes "**PLANNED.** Staged to run after
the current DeepSeek arms; a public-interest measurement, not a default
candidate". GLM-5.3-Flash's row is unchanged, and the Mia GLM recipe on
TensorFold 1.3.0 stays off the page (C3). 11-20 Q-H and Q-I still gate it.

### 4.3 P3. "Own your stack" (unchanged)

11-20 P3 stands as written.

### 4.4 P4. The model policy statement (unchanged)

11-20 P4 stands as written; Q-F still gates the quote.

### 4.5 Upgrades U1 to U6 (amend)

- **U1. Interactive board:** add two filters, "thinking cap" and "harness
  version", and make "DeepSeek ladder" a preset view (filtered to the
  model, sorted by run order). The era filter shows plain labels (Rule G).
  Still no unit-level view (Rule E).
- **U2. Per-language charts:** unchanged: each model at its best (DS-T2,
  GLM-C). Don't chart the eight DeepSeek arms per language: per-language
  differences between arms are one or two units, all inside noise (C5).
- **U3. Changelog feed:** seed entries added: "2026-11-20/21 DeepSeek arms
  U, UP, UPA and U_8k" (one entry, dated 2026-11-21, when their rows
  landed) and "the thinking cap matters most" as that entry's one
  sentence. No entry for UP_8k, UPA_8k, Hy4-flash or the GLM recipe until
  each has a board row or a published result (C7, C3).
- **U4. llms.txt:** [§6](#6-llmstxt-delta-on-11-20-62); `llms-full.txt` as 11-20.
- **U5. Generated dates and facts:** more urgent now: the arm count is the
  fact most likely to go stale, which is why this page's copy avoids it.
  Generate any count from `deepseekLadder.length`.
- **U6. Structured data:** unchanged.

---

## 5. Data

### 5.0 Board state

OpenBeast `main` at `bfd605eb` (#254, 2026-11-21): `evals/boards/v5/board.json`
has fourteen runs: `GLM-L0`, `GLM-A`, `GLM-B`, `GLM-C`, `GLM-E`, `OPUS-REF`,
and eight DeepSeek rows, `DS-T`, `DS-Ts1`, `DS-T2`, `DS-T3`, `DS-U`, `DS-UP`,
`DS-UPA`, `DS-U8k`. The rank-1 row is still `DS-T2`. When this page was
started, `main` was `c4d7a8f0` (#253) and had only the first four DeepSeek
rows; #254 merged about 25 minutes later, and its pass counts matched the
counts in the brief exactly (59, 62, 59, 63).

**The new rows' board notes are not for the site.** Each of the four notes
lists the failed units by id, and the configuration labels of `DS-UP` and
`DS-UPA` use the internal names ("planning lines", "the frozen algorithmic
procedure pack"). Render the status label before the first `;` only (11-19
§4.3 step 5), and print the configuration as this page's one-change
wording, never the board's label (E4, E6). 11-19's fifth grep and M3 catch
both.

### 5.1 The board: each model at its best

Unchanged from 11-20 §5.1 (rank 1 DS-T2 65/71, SCORE 94.9; rank 2 GLM-C
51/71\*, 76.7; the gold standard 71/71; two "No v5 result yet" rows).
Regenerated at `bfd605eb` and again at `c619eaee` (#255): identical.

### 5.2 The DeepSeek ladder (generated)

Generated from `v5_board.py --json` at `bfd605eb`. Mean / unit is the
agent's wall time per unit. Every row: one seed, integrity scan 0
contaminated, 0 units lost.

| Step | Run id | Built from | One change | Cap | Era | Passed | SCORE | Of gold | Mean / unit | Date |
|---|---|---|---|---:|---|---:|---:|---:|---:|---|
| T | DS-T | — | the thinking-first composite | 16,384 | harness8 + review 1118 | 61/71 | 88.5 | 88.5% | 22.5 min | 2026-11-18 |
| T_s1 | DS-Ts1 | T | T with a different seed (noise control) | 16,384 | harness9 | 61/71 | 85.7 | 85.7% | 22.5 min | 2026-11-19 |
| T2 | DS-T2 | T | an 8,192-token per-turn thinking cap and a nudge to act | 8,192 | harness9 | 65/71 | 94.9 | 94.9% | 20.7 min | 2026-11-19 |
| T3 | DS-T3 | T2 | the cap halved to 4,096 tokens | 4,096 | harness9 | 65/71 | 91.5 | 91.5% | 18.2 min | 2026-11-19 |
| U | DS-U | T2 | a forced action turn instead of the nudge, at a 16,384-token cap | 16,384 | harness10 | 59/71 | 85.5 | 85.5% | 22.1 min | 2026-11-19 |
| UP | DS-UP | U | short planning guidance in the system prompt | 16,384 | harness10 | 62/71 | 88.0 | 88.0% | 22.4 min | 2026-11-20 |
| UPA | DS-UPA | UP | an extra problem-solving procedure pack | 16,384 | harness10 | 59/71 | 83.8 | 83.8% | 25.2 min | 2026-11-20 |
| U_8k | DS-U8k | U | U at T2's 8,192-token cap | 8,192 | harness10 | 63/71 | 88.5 | 88.5% | 20.0 min | 2026-11-21 |

The era column is the repo's internal label; the site prints "harness 8",
"harness 9", "harness 10" (Rule G).

### 5.3 Per-language passes (generated)

Count · share of the gold standard's count, rounded half up. The site's
"Passes by language" still leads with DS-T2 and GLM-C (11-19 B5, U2):
per-language differences between DeepSeek arms are one or two units, inside
noise.

| Language | Gold | DS-T | DS-Ts1 | DS-T2 | DS-T3 | DS-U | DS-UP | DS-UPA | DS-U8k | GLM-C |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| python | 32 | 27 · 84% | 27 · 84% | 30 · 94% | 28 · 88% | 26 · 81% | 28 · 88% | 25 · 78% | 28 · 88% | 24 · 75% |
| go | 8 | 7 · 88% | 7 · 88% | 7 · 88% | 7 · 88% | 7 · 88% | 7 · 88% | 7 · 88% | 7 · 88% | 6 · 75% |
| c | 8 | 7 · 88% | 7 · 88% | 7 · 88% | 8 · 100% | 7 · 88% | 7 · 88% | 7 · 88% | 8 · 100% | 6 · 75% |
| cpp | 8 | 7 · 88% | 7 · 88% | 7 · 88% | 8 · 100% | 6 · 75% | 7 · 88% | 7 · 88% | 7 · 88% | 6 · 75% |
| rust | 8 | 7 · 88% | 7 · 88% | 8 · 100% | 8 · 100% | 7 · 88% | 7 · 88% | 7 · 88% | 8 · 100% | 5 · 63% |
| zig | 6 | 5 · 83% | 5 · 83% | 5 · 83% | 5 · 83% | 5 · 83% | 5 · 83% | 5 · 83% | 4 · 67% | 3 · 50% |
| shell | 1 | 1 · 100% | 1 · 100% | 1 · 100% | 1 · 100% | 1 · 100% | 1 · 100% | 1 · 100% | 1 · 100% | 1 · 100% |

The chart data, as the P1 chart reads it (generated; one object per rung
with a board row):

```json
{"best": "DS-T2", "noise_units": 1, "arms": [
 {"arm": "T", "run": "DS-T", "cap": 16384, "era": "harness8 + review 1118", "passed": 61, "score": 88.5},
 {"arm": "T_s1", "run": "DS-Ts1", "cap": 16384, "era": "harness9", "passed": 61, "score": 85.7},
 {"arm": "T2", "run": "DS-T2", "cap": 8192, "era": "harness9", "passed": 65, "score": 94.9},
 {"arm": "T3", "run": "DS-T3", "cap": 4096, "era": "harness9", "passed": 65, "score": 91.5},
 {"arm": "U", "run": "DS-U", "cap": 16384, "era": "harness10", "passed": 59, "score": 85.5},
 {"arm": "UP", "run": "DS-UP", "cap": 16384, "era": "harness10", "passed": 62, "score": 88.0},
 {"arm": "UPA", "run": "DS-UPA", "cap": 16384, "era": "harness10", "passed": 59, "score": 83.8},
 {"arm": "U_8k", "run": "DS-U8k", "cap": 8192, "era": "harness10", "passed": 63, "score": 88.5}
]}
```

**Not on the board, not for the site** (C7; recorded so the website agents
don't go looking): UP_8k and UPA_8k (finished 2026-11-21 after this page;
board rows DS-UP8k and DS-UPA8k via `board/ds-8k-series`), `H_T2` (Hy4-flash,
staged after the DeepSeek arms), the Mia GLM recipe 1104 on TensorFold
1.3.0 (tested last). When UP_8k and UPA_8k get rows, add them to `ARMS`
below and to `deepseekLadder` (§3.8), and rerun.

### 5.4 How the blocks were generated

The tables in §5.2 and §5.3 and the chart JSON came from this script, run
in a scratch directory on the board JSON:

```sh
~/.local/opt/openbeast-eval/venv/bin/python evals/v5_board.py --json > /tmp/board.json
python3 -I gen_blocks.py /tmp/board.json
```

It is kept here rather than in `evals/` because it exists only for this
handoff. The arm metadata (cap, era, the public one-change wording) is
hand-kept from [MODEL_CAMPAIGN.md §5b](MODEL_CAMPAIGN.md#5b-the-arms),
translated per E4. The gold-standard fraction divides by the reference's
SCORE (100.0).

```python
"""Generate the data blocks of WEBSITE_UPDATE_2026-11-21.md from v5_board.py --json.

Usage: python gen_blocks.py board.json > blocks.md
Arm metadata (cap, era, one change) is hand-kept from MODEL_CAMPAIGN.md §5b.
"""
import json
import sys

ARMS = [  # arm, cap, era, built from, one change (public wording, Rule E4)
    ("T", 16384, "harness8 + review 1118", "—", "the thinking-first composite"),
    ("T_s1", 16384, "harness9", "T", "T with a different seed (noise control)"),
    ("T2", 8192, "harness9", "T", "an 8,192-token per-turn thinking cap and a nudge to act"),
    ("T3", 4096, "harness9", "T2", "the cap halved to 4,096 tokens"),
    ("U", 16384, "harness10", "T2", "a forced action turn instead of the nudge, at a 16,384-token cap"),
    ("UP", 16384, "harness10", "U", "short planning guidance in the system prompt"),
    ("UPA", 16384, "harness10", "UP", "an extra problem-solving procedure pack"),
    ("U_8k", 8192, "harness10", "U", "U at T2's 8,192-token cap"),
]
LANGS = ["python", "go", "c", "cpp", "rust", "zig", "shell"]
GOLD = 100.0


def pct(a, b):
    return int(a * 100 / b + 0.5)


def arm_of(run):
    ca = run.get("campaign_arm") or ""
    return ca.split()[-1] if ca.startswith("v5-thinking-first") else None


def main():
    d = json.load(open(sys.argv[1]))
    ds = {arm_of(r): r for r in d["runs"] if r["model"].startswith("DeepSeek")}
    glm_c = next(r for r in d["runs"] if r["id"] == "GLM-C")
    out = []
    out.append("| Step | Run id | Built from | One change | Cap | Era | Passed | SCORE | Of gold | Mean / unit | Date |")
    out.append("|---|---|---|---|---:|---|---:|---:|---:|---:|---|")
    missing = []
    for arm, cap, era, base, change in ARMS:
        r = ds.get(arm)
        if r is None:
            missing.append(arm)
            out.append(f"| {arm} | (no board row) | {base} | {change} | {cap:,} | {era} | — | — | — | — | pending board merge |")
            continue
        out.append(
            f"| {arm} | {r['id']} | {base} | {change} | {cap:,} | {era} | {r['passed']}/{r['units']} | "
            f"{r['score']:.1f} | {r['score'] / GOLD * 100:.1f}% | {r['mean_s'] / 60:.1f} min | {r['date']} |"
        )
    out.append("")
    out.append("Per-language passes (count · share, rounded half up):")
    out.append("")
    cols = [a for a, *_ in ARMS if a in ds]
    out.append("| Language | Gold | " + " | ".join(ds[a]["id"] for a in cols) + " | GLM-C |")
    out.append("|---|---:|" + "---:|" * (len(cols) + 1))
    for lang in LANGS:
        n = glm_c["by_lang"][lang][1]
        cells = [f"{ds[a]['by_lang'][lang][0]} · {pct(ds[a]['by_lang'][lang][0], n)}%" for a in cols]
        g = glm_c["by_lang"][lang][0]
        out.append(f"| {lang} | {n} | " + " | ".join(cells) + f" | {g} · {pct(g, n)}% |")
    out.append("")
    chart = [
        {"arm": a, "run": ds[a]["id"], "cap": cap, "era": era, "passed": ds[a]["passed"],
         "score": round(ds[a]["score"], 1)}
        for a, cap, era, *_ in ARMS if a in ds
    ]
    out.append("```json")
    out.append(f'{{"best": "{d["best"][0]["id"]}", "noise_units": 1, "arms": [')
    out.append(",\n".join(" " + json.dumps(c) for c in chart))
    out.append("]}")
    out.append("```")
    out.append("")
    out.append(f"missing arms (no board row): {', '.join(missing) or 'none'}")
    print("\n".join(out))


main()
```

---

## 6. `llms.txt`: delta on 11-20 §6.2

Start from 11-20's full replacement ([§6.2](WEBSITE_UPDATE_2026-11-20.md#62-llmstxt-the-full-replacement)).
Two lines in "Evaluation" change; everything else stands.

| 11-20 line | Replace with |
|---|---|
| "DeepSeek-V4.1-Flash (Mia EXL3) on the Spark pair passed 65/71, SCORE 94.9, with arm T2, its recommended agent setup. It is #1 on the v5 board. Its four arms: T 61/71, T_s1 (T with a different seed) 61/71, T2 65/71, T3 (half T2's thinking cap) 65/71 with a lower SCORE." | "DeepSeek-V4.1-Flash (Mia EXL3) on the Spark pair passed 65/71, SCORE 94.9, with arm T2, its recommended agent setup (an 8,192-token per-turn thinking cap and a nudge to act). It is #1 on the v5 board. Its campaign ran eight single-seed arms across three harness versions; the full ladder is on the How we evaluate page." |
| "Tencent's Hy4-flash is staged for the same harness as a public-interest measurement; no result yet. Further harness arms are running …" | "What the DeepSeek campaign found: the per-turn thinking cap matters most; short planning guidance helped; an extra procedure pack hurt; a forced action turn did not beat the nudge. Tencent's Hy4-flash is staged to run on the same harness next, as a public-interest measurement; no result yet. Further harness arms are running and are published when they reach the board. The paper asking whether a unified harness generalizes across models is in progress." |

Per 11-20 U5, prefer
generating the first line's numbers from `results-build.json`.

---

## 7. Acceptance checklist

Run 11-19's [§7](WEBSITE_UPDATE_2026-11-19.md#7-acceptance-checklist) steps
1 to 9, then 11-20's [§7](WEBSITE_UPDATE_2026-11-20.md#7-acceptance-checklist)
N1 to N7, with these changes, then the new steps M1 to M8. Use the same
fetched directory `$d` (11-19 step 2; add `models` and `changes` if P2 and
U3 shipped). `$board` is the OpenBeast board at the pinned revision:
`git -C <openbeast checkout> show <revision>:evals/boards/v5/board.json > /tmp/board.json`.

**Changes to 11-20's steps.**

- **N4, first line:** 11-20 banned `59/71` and `U_8k` outright. Replace it
  with M2 below (the ban now depends on the board). Keep `H_T2` banned (M1).
- **N3:** extended by M3.

**M1. Never before their board rows** (each prints nothing; lift a line only
in the PR that adds that row):

```sh
grep -l -E '\bUP_8k\b|\bUPA_8k\b|\bUP-8k\b|UPA-8k|\bH_T2\b' "$d"/*
grep -l -iE 'tracking (even|level|above)|on (its|their) completed units|partial (count|result)' "$d"/*
```

**M2. The board-row gate for U, UP, UPA, U_8k** (prints nothing). For each
arm named on the site, its board row must exist:

```sh
for pair in 'U:DS-U' 'UP:DS-UP' 'UPA:DS-UPA' 'U_8k:DS-U8k'; do
  arm=${pair%%:*}; id=${pair#*:}
  if grep -q -E "(^|[^A-Za-z0-9_])${arm}([^A-Za-z0-9_]|$)" "$d"/benchmarks.html "$d"/research_campaign.html "$d"/llms.txt 2>/dev/null \
     && ! grep -q "\"id\": \"${id}\"" /tmp/board.json; then echo "FAIL: ${arm} on the site without board row ${id}"; fi
done
```

(`U` alone is a short token; the check looks only at the three pages that
carry the ladder. If it false-positives on unrelated prose, tighten it to
the ladder's markup, e.g. `data-rung="U"`.) At `bfd605eb` all four rows
exist, so this passes; it is here for the next arms (swap in UP_8k:DS-UP8k
and UPA_8k:DS-UPA8k, or whatever ids their rows get).

**M3. Rule E4: no content of the guidance or the pack** (each prints
nothing):

```sh
grep -l -iE 'planning lines|algorithmic (procedure )?pack|algo-v1|6c532400|ALGO_LINES|ALGO_PACK|FORCE_ACT|DONE_VERIFIED' "$d"/*
grep -l -iE 'bound first|brute[- ]force|redesign on|5x miss|>5x|doubtful split' "$d"/*
grep -l -iE '(three|3) (general )?planning|[0-9,]+-token (procedure )?pack|~?400 tokens' "$d"/*
grep -l -iE 'iterate-and-test|long-derivation|design-choice failure|168_|192_pike' "$d"/*
```

**M4. Phrasing (C5, C6)** (each prints nothing):

```sh
grep -l -iE 'statistically|significant(ly)?|proves?|conclusive' "$d"/benchmarks.html "$d"/research_campaign.html "$d"/research_harness.html "$d"/llms.txt
grep -l -iE '(U_8k|UP|UPA|\bU\b)[^<.]{0,60}(best|leads|#1|recommended)' "$d"/*
grep -l -iE 'forced action[^<.]{0,80}(worse|lost to|regress)' "$d"/*
```

**M5. T2 stays the headline** (each `>= 1`):

```sh
grep -c -E 'T2' "$d"/benchmarks.html "$d"/research_campaign.html
grep -c 'recommended setup' "$d"/benchmarks.html
grep -c '65/71' "$d"/home.html "$d"/benchmarks.html "$d"/llms.txt
```

And the build's rank-1 assertion ([§3.8](#38-code-changes-amends-11-20-38)
step 3) is present: `grep -n 'DS-T2' src/app/benchmarks/results.ts` in the
website repo shows the `throw`.

**M6. Integrity counts, row by row.** Before shipping B9's "Every DeepSeek
arm on the board", each DeepSeek row's note says 0 contaminated:

```sh
python3 -c "import json,sys; b=json.load(open('/tmp/board.json')); bad=[r['id'] for r in b['runs'] if r['id'].startswith('DS-') and '0 contaminated' not in r.get('note','')]; print(bad or 'ok')"
```

(The committed `board.json` keeps the note on each run; if the layout
differs, use `evals/v5_board.py --json` at the pinned revision instead.)

**M7. The new copy is present** (each `>= 1`):

```sh
grep -c 'The thinking cap matters most' "$d"/benchmarks.html "$d"/research_campaign.html
grep -c -E 'U_8k' "$d"/benchmarks.html "$d"/research_campaign.html
grep -ci 'harness 10' "$d"/research_campaign.html          # if Rule G is approved (Q-M)
grep -ci 'about one unit' "$d"/benchmarks.html "$d"/research_campaign.html "$d"/llms.txt
grep -c 'procedure pack' "$d"/research_campaign.html       # the public wording, E4
```

And the chart (if P1 ships) has one dot per `deepseekLadder` rung, none
for UP_8k or UPA_8k (its e2e test, §4.1 "Guard").

**M8. The cold start** (Q-L): after deploy and again after 30 minutes idle,
`curl -s -o /dev/null -w '%{http_code}\n' https://openbeast.ai/` answers
200 first time. If it answers 502, record it in the deploy note; don't call
the deploy failed for it alone.

**Record the deploy.** As 11-20 N7, and also tick this page's TODO line,
write "applied in website `<commit>`" at the top of this page, and say
whether the eight-rung ladder shipped.

---

## 8. Open questions for Max

**Decisions (Max, 2026-11-21).** These answers settle §8. The questions
below are kept as asked, for the record.

- **Q-R: yes.** Ship the Rule E fix now, on its own (a one-line website PR,
  reworded silently per Q-D). This is in hand separately from this handoff.
- **Q-O: yes.** T2 stays the recommended setup if UP_8k ties. A gap of one
  unit is noise; UP_8k needs a lead of two or more units.
- **Q-P: yes.** Publish the misses ("an extra procedure pack hurt", "a
  forced action turn did not beat the nudge"). The goal is a full ablation
  study, negative results included.
- **Q-K, Q-M, Q-N: yes.**
  - Name harness 10's arms at the high level given in E4.
  - Print "harness 8 / 9 / 10" in the ladder's era column.
  - Say the pair's agents run `deepseek-best`.
- **Q-A to Q-L (carried over): accepted as recommended.** This settles:
  - Q-D: silent rewording.
  - Q-E: "experiment arm" stays on the board.
  - Q-F: publish the full policy quote.
  - Q-G: approve the proposals.
  - Q-H: `/models/` is a new URL.
  - Q-I: hold Hy4-flash until `H_T2` has a row.
  - Q-L: fix the cold-start 502 in the same deploy.

  For Q-B, Q-C and Q-J, the default this handoff assumes applies:
  - Q-B: the bare label "Mia EXL3".
  - Q-C: per-model ladders.
  - Q-J: the decision is made when the 1104 A/B result lands.

  **Q-A needs a fact, not a recommendation:** state the licence of Mia's
  pack before any page offers it as a supported Spark setup. Until then, no
  page makes that offer.
- **Q-Q and Q-S** were not answered separately; the recommendations stand:
  - Q-Q: ship UP_8k and UPA_8k together.
  - Q-S: "What we learned" goes on both pages.

**Carried over (all unanswered as of 2026-11-21):**

- **Q-A.** The licence of Mia's DeepSeek EXL3 pack for a supported Spark
  offer (11-19 Q-A).
- **Q-B.** Name Mia's lab (MiaAI-Lab) on the site, or keep "Mia EXL3" as a
  bare label? (11-19 Q-B)
- **Q-C.** Per-model ladders or one campaign chart? (11-19 Q-C, 11-20 Q-C.)
  This page keeps per-model ladders; the P1 chart is DeepSeek's alone.
- **Q-D.** The GLM-E footnote's history: silent rewording? (Recommendation:
  silent.)
- **Q-E.** "Experiment arm" in headlines, or on the board only?
  (Recommendation: the board.)
- **Q-F.** Publish your full policy quote, with the parenthetical and your
  name? (Recommendation: yes.)
- **Q-G.** Approve the proposals (P1 to P4, U1 to U6)? Recommendation
  unchanged, with P1 now the most valuable: the eight-rung ladder is the
  clearest public evidence of how we work.
- **Q-H.** `/models/` as a new URL, or a section on `/benchmarks/`?
  (Recommendation: the URL.)
- **Q-I.** Announce Hy4-flash before it runs? It is now scheduled right
  after the DeepSeek arms, so the gap between "staged" and a result is
  shorter: holding it back until `H_T2` has a row costs little.
- **Q-J.** The Mia GLM recipe (1104, TensorFold 1.3.0): if it beats today's
  GLM recipe, does GLM-5.3-Flash stay "the alternative" with an upgraded
  line, or get a changelog entry?
- **Q-K.** Name harness 10's arms on the site? **This handoff assumes yes**,
  at the level "short planning guidance" and "an extra procedure pack"
  (E4), because the 2026-11-21 brief asked for the ladder to span U to
  U_8k. Confirm, or say the arms stay off the public ladder.
- **Q-L (escalated).** The cold-start 502 lasted about a minute on two
  pages today, three days running. Have the website agents fix the Mini's
  origin wake-up (a keep-alive or health ping, and a look at the launchd
  service)? (Recommendation: yes, in the same deploy.)

**New:**

- **Q-M. Harness versions on the site (Rule G)?** Print "harness 8 / 9 /
  10" in the ladder's era column, with one explanatory sentence, or hide
  versions and only mark "crosses a harness version"? (Recommendation:
  print them; the ladder is unreadable without them.)
- **Q-N. Say on the site that the pair's agents run `deepseek-best`?**
  (Resolved in the repo by #255: live since 2026-11-19.) The amended N2
  says so. Confirm you want the live state public, or keep N2 describing
  the preset only. (Recommendation: say it; it is true and it is the point
  of the preset.)
- **Q-O. If UP_8k lands level with T2, or one unit ahead,** what does the
  site say? Options: T2 stays "recommended setup" and the board's SCORE
  rank decides "#1" (the build assertion then stops the deploy for a copy
  pass); or T2 stays both until a seed repeat separates them.
  (Recommendation: a difference of one unit is noise by our own rule, so T2
  stays "recommended" until UP_8k is ahead by two or more, or you decide
  otherwise; if UP_8k takes rank 1 on SCORE, the board says so and the
  headline sentence says "tied on passes".)
- **Q-P. Publish the misses as findings?** "An extra procedure pack hurt"
  and "a forced action turn did not beat the nudge" are negative results.
  (Recommendation: yes. "Publish the misses" is P1's own headline.)
- **Q-Q. Ship UP_8k and UPA_8k only together (C8)?** Or each as its row
  lands? (Recommendation: together, as U to U_8k did; each lesson needs
  both.)
- **Q-R. Ship the Rule E fix now, alone?** The GLM-E footnote leak is four
  days public and blocked behind a large update. A one-line website PR (11-19
  B8, R4) could go today. (Recommendation: yes.)
- **Q-S. "What we learned" on `/benchmarks/` (E1), or only on How we
  evaluate (P1)?** (Recommendation: both, the short version on
  `/benchmarks/`.)

**Out of scope** (as 11-20 §8, plus): any number for UP_8k, UPA_8k or
`H_T2`; any number for U, UP, UPA or U_8k before its board row; the
content of the planning guidance or the pack (E4); which units an arm
gained or lost (E5); the GLM recipe 1104 on TensorFold 1.3.0; the CI
setting (Rule F).
