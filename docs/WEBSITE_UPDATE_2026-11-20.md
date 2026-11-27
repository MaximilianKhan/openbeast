# openbeast.ai update and upgrade, 2026-11-20: handoff for the website agents

**Extended by [WEBSITE_UPDATE_2026-11-21.md](WEBSITE_UPDATE_2026-11-21.md)** (2026-11-21): still not applied; apply 11-19, this page, then 11-21's deltas, and where they differ the 11-21 page wins.

**Status: plan, ready to implement.** Written 2026-11-20 for the agents that
maintain openbeast.ai (the private repo `MaximilianKhan/openbeast-website`).
It **extends** yesterday's handoff,
[`WEBSITE_UPDATE_2026-11-19.md`](WEBSITE_UPDATE_2026-11-19.md), which is
still the base and has **not been applied** ([§1](#1-status-of-the-11-19-handoff)).
Apply both in one website PR and one deploy: 11-19 first, then the deltas
here. Where the two differ, this page wins. The master plan is
[`WEBSITE_PLAN.md`](WEBSITE_PLAN.md), whose accuracy rules
([§3](WEBSITE_PLAN.md#3-accuracy-rules)) still apply.

This page does two things:

- **Update** ([§3](#3-delta-changes-page-by-page)): it corrects the 11-19
  replacement copy where the facts moved on (DeepSeek now has four board
  rows, the seed-noise arm landed, arm T3 landed), and adds live stale text
  that 11-19 missed.
- **Upgrade** ([§4](#4-new-content-proposals-proposed)): new pages and
  features, each labelled **PROPOSED**, with copy, data sources and an
  implementation note against the website repo's structure.

**How this page was made.** On 2026-11-20 (17:28 UTC) every sitemap page and
`/llms.txt` were fetched with `curl -sL` and their text extracted. The
website repo was cloned read-only to `/tmp` at `e72790c`. The numbers come
from `evals/v5_board.py --json` on OpenBeast `main` at `06bd875c` (#250),
and the website's own `scripts/sync-results.mjs` was dry-run against that
commit in a scratch copy (it passes: "Checked 10 v5 runs against
06bd875c…"). File and line references (`src/app/page.tsx:39`) are to
website commit `e72790c`.

**Labels** are 11-19's: **MEASURED** (run on our hardware, with a record in
the repo), **SHIPPED (upstream main)** / "UPSTREAM MAIN · after v1.9" (merged
on OpenBeast `main` after the v1.9.0 tag), **PLANNED** (designed, maybe
tested against fakes, not run on hardware). This page adds **PROPOSED**: a
website change that Max has not approved yet. A PROPOSED item ships only
after Max answers its question in [§8](#8-open-questions-for-max), or says
"ship the proposals".

| Section | What it gets you |
|---|---|
| [1. Status of the 11-19 handoff](#1-status-of-the-11-19-handoff) | what is live, what is pending (nothing is live) |
| [2. What changed since 11-19, and the rule amendments](#2-what-changed-since-11-19-and-the-rule-amendments) | the new facts, and the rules they change |
| [3. Delta changes, page by page](#3-delta-changes-page-by-page) | live quote, replacement, source, on top of 11-19 |
| [4. New content proposals (PROPOSED)](#4-new-content-proposals-proposed) | How we evaluate, Models we've measured, Own your stack, the policy statement, upgrades |
| [5. Data](#5-data) | the board rows exactly as the site should show them |
| [6. SEO, metadata and llms.txt](#6-seo-metadata-and-llmstxt) | descriptions, structured data, the full new `llms.txt` |
| [7. Acceptance checklist](#7-acceptance-checklist) | 11-19's steps plus the new greps |
| [8. Open questions for Max](#8-open-questions-for-max) | Q-A to Q-E carried over, Q-F to Q-L new |

---

## 1. Status of the 11-19 handoff

**Nothing from the 11-19 handoff is live.** Evidence, 2026-11-20:

- The live build is unchanged: `last-modified: Wed, 18 Nov 2026 01:37:35 GMT`
  on `/`, the same header 11-19 recorded.
- The website repo's `main` is still `e72790c` ("Refine typography and
  geometry across OpenBeast.ai"). There is no branch or PR for the update:
  the only open PRs are Dependabot's #15 and #19.
- Every stale string 11-19 targets is still served:

| Stale string (11-19 target) | Where it is live today | 11-19 row |
|---|---|---|
| "TensorFold serves GLM-5.3-Flash across two GB10s …" | `/`, `/hardware/` ("TensorFold serves GLM-5.3-Flash Ablit in EXL3 4-bit …"), `/llms.txt` line 8 | H1, W1, llms 8 |
| "GLM-5.3-Flash: 26/71 baseline, 51/71 scored." | `/` | H2 |
| "DeepSeek v4.1 Flash is next on the Spark pair." | `/benchmarks/`, `/llms.txt` line 17 | B7, llms 17 |
| "Next: DeepSeek v4.1 Flash." | `/research/campaign/` | R5 |
| "One local model has been measured." | `/research/harness/` | R2 |
| "Uncensored-only fleet policy" | `/investors/investment-case/` | I1 |
| "The default weights on the reference systems are abliterated." | `/llms.txt` line 22 | llms 22 |
| "The operator controls the choice of abliterated weights." | `/hardware/` | W4 |
| the GLM-E footnote quoting the lint's match ("dlopen") | `/benchmarks/`, `/research/harness/` | B8, R4 (Rule E) |
| "First rows measured on the DGX Spark pair for one model, single seed." | `/investors/` | V1 |

- No DeepSeek-V4.1-Flash (Mia EXL3) text, no `65/71` and no
  "uncensored-preferred" appear on any page.

**So all of 11-19 §3 to §6 is pending,** and so is the website `AGENTS.md`
update (rules A to E). The Rule E leak (the GLM-E footnote) has now been
public for three days. It is the first thing to fix.

**One operational note.** As on 11-19, the very first request for `/`
answered a Cloudflare **502**; the retry answered 200, and every other page
answered 200 first time. Two days running suggests the origin is slow to
wake after idle, not a fluke. Check the Mini's launchd service and tunnel
logs (`deploy/README.md`) while deploying. This is a website-ops item, not
copy.

---

## 2. What changed since 11-19, and the rule amendments

### 2.1 The new facts

1. **DeepSeek has four board rows.** Arms T (61/71, SCORE 88.5), T_s1 (the
   seed-noise repeat of T: 61/71, SCORE 85.7), T2 (65/71, SCORE 94.9) and T3
   (65/71, SCORE 91.5). **T2 is still the board's best row** for the model:
   T3 tied it on passes with a lower SCORE. Run ids: `DS-T`, `DS-Ts1`,
   `DS-T2`, `DS-T3` (the board's spelling: `DS-Ts1`, no underscore).
2. **Run-to-run noise is measured: about ±1 unit.** T_s1 repeated arm T
   with a different seed. It passed the same count, 61/71, with one unit
   gained and one lost. T2's +4 over T is well outside that margin; T3 vs
   T2 (0 net) is inside it. Source: the board note on `DS-Ts1`.
3. **T3 is a trade-off, not an improvement.** A 4,096-token per-turn
   thinking cap (half of T2's 8,192) gained three units and lost three,
   finishing faster (18.2 vs 20.7 min per unit). T2 stays the recommended
   setup. Source: the board note on `DS-T3` (do not publish its wording,
   [§2.2](#22-rule-amendments)).
4. **`deepseek-best` is OpenBeast's default agent setup for DeepSeek** (Max,
   2026-11-19): `AGENT_PROFILE=deepseek-best` is arm T2 for daily agents, and
   the campaign's `composite_by_model` makes T2 the head of every DeepSeek
   chain. [SPARK_HYDRA.md E2b](SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2)
   still says the profile is **not yet in spark-1's `openbeast.conf`** (it
   goes live after the eval series). Keep 11-19 N2's caveat.
5. **More arms are running and are not on the board.** Arm U (harness10, a
   forced-act turn) finished at 59/71 on 2026-11-20 but has **no board row**.
   UP is running (partial). UP_8k and UPA_8k were added (#250). None of
   these goes on the site until it has a board row
   ([§2.2](#22-rule-amendments) C2).
6. **The model policy is enforced in code.** The hydra router's
   `family_policy = "uncensored-preferred"` is the default since #245
   ([BEAST_HYDRA.md "Family policy"](BEAST_HYDRA.md#family-policy)). Max's
   full rationale is quoted there ([§4.4](#44-p4-the-model-policy-statement)).
7. **Hy4-flash is staged as a public-interest measurement.** Tencent's
   Hy4-flash is set up to be measured (arm `H_T2` = T2 exactly), not to
   become a default. The EXL3 pack we hold **cannot span two Sparks**; the
   NVFP4 build on vLLM TP2 is staged in code, and nothing is on the Sparks
   yet ([review](reviews/HY4_FLASH_ENGINES-2026-11-19.md),
   [SPARK_HYDRA.md](SPARK_HYDRA.md#hy4-flash-nvfp4-vllm-tp2-measured)).
8. **Native TensorFold 1.2.10 is installed on both Sparks** (signed,
   `~/.local/bin/tensorfold`), not serving: it does not yet support the
   default model's architecture (`deepseek_v41`) or Hy4-flash's, so the pair
   serves through the recipe's engine build
   ([EXL3_PLAYBOOK.md §1.2](EXL3_PLAYBOOK.md#12-the-engine-builds)).
9. **A new Mia GLM recipe on TensorFold 1.3.0 is being tested.** Pending:
   no result, no doc, nothing for the site ([§2.2](#22-rule-amendments) C3).
10. **Unchanged since 11-19:** the default model and its serving facts
    (11-19 §4.2), v1.9.0 as the latest tag, backup and restore still
    **PLANNED** (no real backup, image or restore yet:
    [SPARK_BACKUP_RESTORE.md](SPARK_BACKUP_RESTORE.md)), the RTX 5090 facts.

### 2.2 Rule amendments

11-19's rules A to E stand. These amend them.

**C. Numbers** (amends 11-19 Rule C):

- **C1. Seed noise is now a number.** Replace 11-19's "until it lands, no
  difference … is called significant" with: every v5 row is still **one
  seed**, and the site may now say "a repeat of arm T with a different seed
  passed the same 61/71, one unit swapped each way: run-to-run noise is
  about one unit". It may say T2's gain over T (+4) is larger than that
  noise. It may **not** call T3 better or worse than T2, or call any
  one-unit difference real. The website `AGENTS.md` line "Use a single-seed
  qualifier on v5 results and deltas until distinct-seed repeats exist"
  stays: one arm's repeat is not a repeat of every row.
- **C2. Board rows only.** A run appears on the site only when
  `v5_board.py` has a row for it. Arm U's 59/71, UP's partial count and any
  8K arm stay off the site until then. The site may say "further harness
  arms are running" without naming a count.
- **C3. No pending engine claims.** The Mia GLM recipe on TensorFold 1.3.0
  and the Hy4-flash NVFP4 serving path are untested on our hardware. No
  throughput, fit or score for either, and no third-party or vendor
  benchmark numbers for Hy4-flash (the website `AGENTS.md`: "Leave unrun or
  unreleased figures out of production").
- **C4. Ties are ties.** T2 and T3 both passed 65/71. The board ranks T2
  first by SCORE. Say "65/71" for the model; don't say "65/71 twice" as
  evidence of stability (the two arms differ in one variable).

**E. Eval safety** (extends 11-19 Rule E; amended 2026-11-25 there: prompts and per-unit results are public, solutions, validators and sealed notes never):

- **E1. The new board notes leak more.** After a resync, `DS-T3`'s status
  says which *kind* of unit the 4,096 cap gained and lost, and `DS-T2`'s says
  what the integrity review found. 11-19 §4.3 step 5 (render only the
  status label before the first `;`) covers both. Add the strings to the
  negative test ([§7](#7-acceptance-checklist) step N3).
- **E2. Integrity results as counts only:** "integrity scan: 0
  contaminated" for each DeepSeek row. Don't publish the scan's flags, the
  sealed review notes, or what an agent attempted.
- **E3. Harness switches, not harness content.** The site may name a
  campaign arm's one changed variable ("a per-turn thinking cap of 4,096
  tokens"). It may not describe the contents of the planning lines or the
  algorithmic procedure pack (arms UP, UPA) even after they land: those are
  problem-solving guidance written against failures on live units.

**F. Internal process stays internal (new).**

- GitHub CI is turned off on the OpenBeast repo for cost; local tests are
  the merge gate. This is not a feature and not a trust claim. Don't
  publish it, and don't write "CI-tested" or "every PR runs CI" anywhere.
- Host names, tailnet names, private addresses and paths stay off the site
  (11-19 step 3, sixth grep).

---

## 3. Delta changes, page by page

Each row is either an **amendment** to an 11-19 row (its id in the first
column) or **new** (an id starting with `D`). "Live" is the exact text
served on 2026-11-20. Where the live text is the same as 11-19 quoted, the
row says so instead of repeating it.

### 3.1 Home `/` (`src/app/page.tsx`)

| # | Section | Live (exact) | Replacement (ready to paste) | Source |
|---|---|---|---|---|
| H2 (amend) | 03 "Evidence", paragraph | as 11-19 H2 | Heading unchanged: "DeepSeek-V4.1-Flash (Mia EXL3): 65/71, #1 on the v5 board." Paragraph: "The Spark pair's default model passed 65/71 with its recommended agent setup, a SCORE of 94.9, or 94.9% of the gold standard's. Claude Opus 5.5 passed 71/71 in its own scaffold. GLM-5.3-Flash, the alternative, reached 51/71. Single seed per run; repeating one run with a new seed moved it by about one unit. v5 measures agentic coding with thinking on, not general chat." | Board JSON `best`; `DS-Ts1` note |
| H3 | ladder chips | as 11-19 H3 | 11-19 H3 unchanged (one bar per model at its best: DS-T2, GLM-C, gold) | Board JSON `best` |

### 3.2 `/benchmarks/` (`src/app/benchmarks/page.tsx`, `src/components/ArmLadder.tsx`)

| # | Section | Live (exact) | Replacement (ready to paste) | Source |
|---|---|---|---|---|
| B1 (amend) | Hero lede | as 11-19 B1 | 11-19 B1's text, with its "Single seed." replaced by: "Single seed per run. A repeat of one run with a different seed passed the same count, so run-to-run noise is about one unit." | `DS-Ts1` note |
| B3 (amend) | DeepSeek ladder (new, above GLM's) | (none) | Heading: "DeepSeek-V4.1-Flash (Mia EXL3): four thinking-first arms". Intro: "DeepSeek-V4.1-Flash (Mia EXL3) ran on the Spark pair with TensorFold and stock EXL3 weights at 2.9 bits per weight. Arm T combined the changes that helped GLM, with thinking on. T_s1 repeated T with a different seed, to measure noise. T2 added a per-turn thinking cap of 8,192 tokens and a nudge to act. T3 halved the cap to 4,096. Each arm covers the entire suite. One variable changes per step." Rungs, in this order: "T · 61/71 · 88.5%", "T_s1 · 61/71 · 85.7% (seed repeat of T)", "T2 · 65/71 · 94.9% (recommended setup)", "T3 · 65/71 · 91.5% (shorter cap)", gold at 100% | Board JSON runs `DS-T`, `DS-Ts1`, `DS-T2`, `DS-T3`; [MODEL_CAMPAIGN.md §5b](MODEL_CAMPAIGN.md#5b-the-arms) |
| B7 (amend) | "What runs next" | as 11-19 B7 ("DeepSeek v4.1 Flash is next on the Spark pair." / "A different seed will help estimate how much of the observed difference is run-to-run variation." / "The RTX 5090 and its Qwen3.8 default still have no v5 result.") | Three items: "DeepSeek-V4.1-Flash (Mia EXL3) ran four arms and leads the board with arm T2. A seed repeat put run-to-run noise at about one unit, and a shorter thinking cap (T3) tied T2 without beating it. Further harness arms are running; they appear here when they reach the board." / "Tencent's Hy4-flash is staged for the same harness as a public-interest measurement. No result yet." / "The RTX 5090 and its Qwen3.8 default still have no v5 result." Keep the paper line | Board JSON; [TODO.md "Run `H_T2`"](TODO.md); C2, C3 |
| B9 (amend) | "Measurement limits" | as 11-19 B9 | 11-19 B9's text with "Both DeepSeek arms ran under the hardened sandbox, and their scans found no contaminated passes. A distinct-seed run and held-out checks are still needed." replaced by: "All four DeepSeek arms ran under the hardened sandbox, and their scans found no contaminated passes. One arm has a distinct-seed repeat; the others do not, and held-out checks are still needed." | Board JSON notes (counts only) |
| D1 (new) | Run table rows | (GLM rows only) | Add `DS-T`, `DS-Ts1`, `DS-T2`, `DS-T3` from the resync ([§5.2](#52-every-v5-configuration-measured)). Status column: the label before the first `;` plus "integrity scan: 0 contaminated" (11-19 §4.3 step 5) | Board JSON |

### 3.3 `/hardware/` (`src/app/hardware/page.tsx`)

| # | Section | Live (exact) | Replacement (ready to paste) | Source |
|---|---|---|---|---|
| W7 (amend) | "Measured capability on the Spark pair" | as 11-19 W7 | "DeepSeek-V4.1-Flash (Mia EXL3) passed 65/71 (SCORE 94.9, #1 on the v5 board) with its recommended agent setup, across four measured arms from 61/71 up. GLM-5.3-Flash passed 26/71 in its November 16 configuration and 51/71 scored with thinking and a revised harness. Single seed per run; run-to-run noise is about one unit. v5 measures agentic coding with thinking on." | Board JSON |
| D2 (new) | "Open validation" (`page.tsx:89`) | "Open validation includes hydra routing between hosts, cluster sizes beyond two, eight concurrent Spark streams, native TensorFold versus the reference recipe, the MTU throughput A/B and a complete offline rebuild." | "Open validation includes hydra routing between hosts, cluster sizes beyond two, eight concurrent Spark streams, the MTU throughput A/B and a complete offline rebuild. Native TensorFold 1.2.10 is installed on both Sparks but does not yet support the default model's architecture, so the pair serves through the recipe's engine build; comparing the two waits on native support." | [EXL3_PLAYBOOK.md §1.2](EXL3_PLAYBOOK.md#12-the-engine-builds); [SPARK_HYDRA.md "Not done yet"](SPARK_HYDRA.md#not-done-yet) |

### 3.4 `/investors/investment-case/` (`src/app/investors/investment-case/page.tsx`)

| # | Section | Live (exact) | Replacement (ready to paste) | Source |
|---|---|---|---|---|
| I3 (amend) | Release record (`page.tsx:33`) | "v5 runs for Qwen3.8 and DeepSeek remain open." | "v5 runs for Qwen3.8 remain open. DeepSeek-V4.1-Flash (Mia EXL3) has four single-seed rows on the board, including a seed repeat." | Board JSON |
| D3 (new) | Evidence table, row "Two DGX Sparks" (`page.tsx:26`) | "TensorFold tensor-parallel serving plus the first local v5 profiles, for one model at one seed." | "TensorFold tensor-parallel serving, and v5 profiles for two models: DeepSeek-V4.1-Flash (Mia EXL3), the default, at 65/71, and GLM-5.3-Flash at 51/71. Single seed per run, with one seed repeat." | Board JSON |
| D4 (new) | Next gates, row "Capability and portability" (`page.tsx:40`) | "Publish fresh-seed, cross-model and per-platform results. Validate professional Blackwell and sustained recovery on real systems." | "Extend the first seed repeat to more rows, and publish per-platform results (the RTX 5090 default has no v5 row yet). Validate professional Blackwell and sustained recovery on real systems." | Board JSON `not_yet_run` |
| D5 (new) | Next gates, row "Serving stack licences" (`page.tsx:41`) | "Review the Ablit weights and serving recipe for commercial use. A supported Spark stack needs a replacement for the research-only speculative component." | "Review the weights and serving recipe of each Spark model for commercial use: DeepSeek-V4.1-Flash (Mia EXL3), the default, and GLM-5.3-Flash, the alternative. The GLM recipe's research-only speculative component needs a replacement in a supported stack." | 11-19 V2 and Q-A (same wording basis) |

### 3.5 `/research/`, `/research/harness/`, `/research/campaign/`

| # | Page, section | Live (exact) | Replacement (ready to paste) | Source |
|---|---|---|---|---|
| R1 (amend) | `/research/` (`research/page.tsx:192`) | as 11-19 R1 | "On the same Spark system and weights, GLM-5.3-Flash moved from 26/71 to 51/71 scored with a revised harness and thinking. A second model, DeepSeek-V4.1-Flash (Mia EXL3), reached 65/71 with a thinking-first version of that harness. A seed repeat puts run-to-run noise at about one unit. The cross-model paper is in progress." | Board JSON |
| R2 (amend) | `/research/harness/` (`harness/page.tsx:31`), the whole paragraph | "Does each model need its own harness, or can one harness with a few per-model settings serve GLM-5.3-Flash, Qwen3.8 and DeepSeek v4.1 Flash? One local model has been measured. Arms D and F did not run. A different seed is required to estimate run-to-run variation. The Opus reference used a different scaffold, and the validator note on E remains in the record." | "Does each model need its own harness, or can one harness with a few per-model settings serve GLM-5.3-Flash, Qwen3.8 and DeepSeek-V4.1-Flash (Mia EXL3)? Two local models have been measured: GLM-5.3-Flash (arms L0 to E) and DeepSeek-V4.1-Flash (Mia EXL3) (arms T, T_s1, T2 and T3). GLM's arms D and F did not run. Repeating DeepSeek's arm T with a different seed moved it by about one unit. The best setup differs by model so far: DeepSeek gained from a per-turn thinking cap that GLM never ran. The Opus reference used a different scaffold, and the validator note on E remains in the record." | Board JSON; [MODEL_CAMPAIGN.md §5b](MODEL_CAMPAIGN.md#5b-the-arms) |
| R5 (amend) | `/research/campaign/` (`campaign/page.tsx:27`) | "Next: DeepSeek v4.1 Flash. First verify its per-request thinking switch through the actual serving path." | "**Ran: DeepSeek-V4.1-Flash (Mia EXL3).** Arm T scored 61/71. Its seed repeat, T_s1, also scored 61/71, which puts run-to-run noise at about one unit. Arm T2 (a per-turn thinking cap and a nudge to act) scored 65/71, #1 on the v5 board, and became the model's recommended setup. Arm T3 (half the cap) tied at 65/71 without beating it. Further arms are running." Links: keep "Next campaign plan" but point it at `#9-next-deepseek-v41-flash-1017-thinking-first` (unchanged anchor), and add "How we evaluate" ([§4.1](#41-p1-how-we-evaluate-the-campaign-page)) | Board JSON; MODEL_CAMPAIGN.md §9 |

### 3.6 `/llms.txt` (`public/llms.txt`)

The full replacement file is in [§6.2](#62-llmstxt-the-full-replacement).
It supersedes 11-19 §3.9 line by line.

### 3.7 `e2e/site.spec.ts` (amends 11-19 §3.10)

| Line | 11-19 said | Change to |
|---|---|---|
| 190 | add `["61/71", "65/71"]` for the DeepSeek ladder | add `["T_s1", "61/71", "65/71", "91.5%"]` (the four-rung ladder) |
| 465 | add `["DS-T", "61/71", "88.5"], ["DS-T2", "65/71", "94.9"]` | also `["DS-Ts1", "61/71", "85.7"], ["DS-T3", "65/71", "91.5"]` |
| 484 | `/seed arm is running/` | `/run-to-run noise is about one unit/i` |
| 398 | `llms.txt` facts | `"DeepSeek-V4.1-Flash (Mia EXL3)"`, `"524,288-token context"`, `"65/71"`, `"uncensored-preferred"`, `"Single seed"`, `"about one unit"` |
| new | — | The negative test (11-19 §3.10 last paragraph) also covers [§7](#7-acceptance-checklist) steps N3 and N4 |

### 3.8 Code changes (amends 11-19 §4.3)

1. **Resync** (11-19 step 1): use OpenBeast `main` at the merge commit of
   the PR that adds this page, or later. `06bd875c` already passes the
   sync (dry run, 2026-11-20). Pin `results.revision` to the commit you
   sync: every repo link on the site follows it.
2. **`results.ts`: the DeepSeek ladder** (replaces 11-19 step 3):

   ```ts
   export const deepseekLadder = [
     ["T", "DS-T", "Thinking-first composite"],
     ["T_s1", "DS-Ts1", "T repeated with a different seed (noise)"],
     ["T2", "DS-T2", "T plus a per-turn thinking cap and a nudge to act"],
     ["T3", "DS-T3", "T2 with half the thinking cap"],
   ].map(([rung, id, change]) => ({ ...run(id), rung, change }));
   ```

   The rung label and the run id differ for T_s1 (`DS-Ts1`). Don't derive
   one from the other.
3. **`best` stays the board's rank-1 row** (11-19 step 2), which is still
   `DS-T2`. Add a build-time assertion that `best.id` is a DeepSeek row, so
   a resync that changes rank 1 fails the build instead of silently
   changing the headline copy (the copy names T2).
4. **Status and notes** (11-19 steps 4 and 5): unchanged, and now required
   for four more rows. `notes[0]` for `GLM-E` is a lint note after the
   resync (checked in the dry run), so the fixed footnote strings are
   mandatory, not optional.
5. **`updated` in `src/lib/project.ts`** is hand-set to `"2026-11-17"`; it
   drives the sitemap's `lastModified` and the JSON-LD dates. Set it to the
   deploy date (or see [§4.5 U5](#u5-generated-dates-and-facts)).

---

## 4. New content proposals (PROPOSED)

Each proposal gives its place in the site, its copy, its data sources and
an implementation note. All copy obeys rules A to F. Everything in this
section is **PROPOSED** until Max approves it ([§8](#8-open-questions-for-max)).

**The site's structure, for orientation** (website `e72790c`): a Next.js 16
static export (`output: "export"`, `trailingSlash: true`), served by the Mac
Mini behind a Cloudflare tunnel. Pages are `src/app/<route>/page.tsx`.
Technical pages use `TechnicalPage`, `TechnicalSection`, `FeatureGrid`,
`DataTable` and `FlowDiagram` from `src/components/TechnicalPage.tsx`. Board
data is `src/app/benchmarks/results-build.json`, generated by
`scripts/sync-results.mjs` from OpenBeast's `docs/RESULTS.md`, and read
through `src/app/benchmarks/results.ts`. Navigation is the `links` array in
`src/components/SiteHeader.tsx`; the sitemap is the `paths` array in
`src/app/sitemap.ts`; per-page metadata is `siteMetadata()` in
`src/lib/project.ts`; social cards come from
`scripts/generate-social-cards.mjs`. The CSP is generated per build
(`scripts/generate-headers.mjs`): `script-src 'self'` plus hashes,
`connect-src 'self'`. Client components already exist (`ArchitectureMap`,
`PaperReader`), so a client component is allowed; it may fetch only
same-origin files.

### 4.1 P1. "How we evaluate": the campaign page

**Where:** upgrade `/research/campaign/` in place (the URL is already in the
sitemap, `llms.txt` and inbound links). Retitle it "How we evaluate", and
link it from `/benchmarks/` next to "Measurement limits" and from the board
caption. Don't add a new URL.

**Copy, ready to paste:**

> **[ RESEARCH / HOW WE EVALUATE ]**
> **Change one thing. Measure everything. Publish the misses.**
>
> Every number on the v5 board comes from a campaign: a chain of runs on
> the same model, weights and hardware, where each run changes one thing
> from the run before it. Every run covers the whole suite: 71 scored units
> across six languages and one shell unit. v5 measures agentic coding with
> thinking on: the model plans, edits, compiles and tests in a sandbox until
> a validator accepts its work or the clock runs out.
>
> **The DeepSeek-V4.1-Flash (Mia EXL3) ladder** (MEASURED, single seed per
> run):
>
> | Step | One change | Passed | Of the gold standard |
> |---|---|---:|---:|
> | T | the thinking-first composite: everything that helped GLM, with thinking on | 61/71 | 88.5% |
> | T_s1 | the same as T, with a different seed | 61/71 | 85.7% |
> | T2 | T plus an 8,192-token cap on each turn's thinking and a nudge to act | 65/71 | 94.9% |
> | T3 | T2 with the cap halved to 4,096 tokens | 65/71 | 91.5% |
>
> T_s1 is the control. Run twice with different seeds, the same setup
> passed the same count, with one unit going each way. So a difference of
> one unit between two runs is noise, and T2's gain of four over T is not.
> T3 traded three units for three others and finished faster; it did not
> beat T2. T2 is the model's recommended setup, and one line in
> `openbeast.conf` applies it to the stack's own agents. Further arms are
> running and join the ladder when they reach the board.
>
> **How the numbers stay honest.**
>
> - **One variable per step.** A step's difference is attributed to its one
>   change, and only when it is larger than the measured noise.
> - **Seeds.** Every row is one seed. A seed repeat measures the noise, and
>   the board says which rows have one.
> - **Eras.** The harness is versioned. A change to the code that shapes
>   the agent starts a new era, and the board records each row's era. Rows
>   are compared within a model and an era, never across them by default.
> - **Sealed evaluation.** Measured agents can't read the suite's sealed
>   files, can't reach the network beyond the model server, and can't write
>   into the evaluation's own code. The run checks around every unit that the
>   server still serves the model under test, so a model switch stops a run
>   instead of mixing two models in one row.
> - **Integrity scans.** Every published run is scanned for what the agent
>   did. A pass that touched sealed material is withheld and footnoted,
>   never quietly kept. The review notes are sealed in the repository, not
>   published, so they can't become hints.
> - **A gold standard, not a rival.** Claude Opus 5.5 passed every unit in
>   its own scaffold. Scores are shown as a fraction of its SCORE, and it is
>   never ranked.
> - **The units stay unpublished.** The site shows scores and pass counts,
>   never solutions, checker details or which unit passed or failed. The
>   suite is a live benchmark.

Keep the existing six-step `FeatureGrid` ("Onboard" … "Run and compare") and
the dry-run command below this copy, as "Run a campaign yourself".

**Data sources:** board JSON runs `DS-T`, `DS-Ts1`, `DS-T2`, `DS-T3`
(through `deepseekLadder`, [§3.8](#38-code-changes-amends-11-19-43));
[MODEL_CAMPAIGN.md §5b](MODEL_CAMPAIGN.md#5b-the-arms) (what each arm
changes), [§7](MODEL_CAMPAIGN.md#7-the-rules-that-keep-rows-comparable)
(eras, comparability); [`evals/CHANGELOG.md`](../evals/CHANGELOG.md) "review
1118: sandbox hardening" (the sandbox bullets); the `DS-T2` note "review
notes sealed in evals/audit/reviews/" (the sealed audit, stated as a fact
only); [SPARK_HYDRA.md E2b](SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2)
(the one-line preset).

**Implementation note:** make `ArmLadder` take its ladder as a prop
(`<ArmLadder ladder={deepseekLadder} footnotes={…} />`) so the same component
renders GLM's ladder on `/benchmarks/` and DeepSeek's here. The four-row
table above is a `DataTable` built from `deepseekLadder`; never type its
numbers. The "One change" texts live in `results.ts` next to the ladder.
Update the page's `siteMetadata` ([§6.1](#61-page-metadata)). The "eras"
bullet must not print era hashes or harness version strings (they mean
nothing to a reader and churn every era roll).

### 4.2 P2. "Local models we've measured"

**Where:** a new page `/models/`, linked from `/benchmarks/` ("Every model
we've measured") and `/hardware/`, added to the sitemap and `llms.txt`. Not
in the top navigation (six items is the header's limit on phones). If Max
prefers no new URL, the same content is a section at the end of
`/benchmarks/` with `id="models"` (Q-H).

**Copy, ready to paste:**

> **[ MODELS ]**
> **Every local model we've measured, and the ones we haven't yet.**
>
> We publish what ran on our hardware, under the same harness and the same
> rules, including the models that lost. A model is on this page once it
> serves on one of our reference systems. It gets a v5 number only when a
> full run reaches the board.

| Model | Runs on | Weights | Status | Best v5 result |
|---|---|---|---|---|
| DeepSeek-V4.1-Flash (Mia EXL3) | DGX Spark pair, TensorFold, tensor parallel 2 | stock, EXL3 2.9 bpw | **Default on the Spark pair.** MEASURED | 65/71 (94.9% of the gold standard), four arms |
| GLM-5.3-Flash | DGX Spark pair, TensorFold, tensor parallel 2 | abliterated, EXL3 4-bit | Staged alternative, one command away. MEASURED | 51/71 (76.7%), five arms |
| Qwen3.8 27B Uncensored MTP | RTX 5090, llama.cpp | abliterated, Q5_K_M | Default on the RTX 5090. MEASURED serving; no v5 run yet | — (v4 archive: 97.6) |
| Qwen3.6 27B | RTX 5090, llama.cpp | stock | v4's #1. No v5 run yet | — (v4 archive: 98.7) |
| Tencent Hy4-flash | DGX Spark pair, vLLM, tensor parallel 2 (staged) | stock, NVFP4 | **PLANNED.** A public-interest measurement, not a default candidate | pending |
| Claude Opus 5.5 (reference) | Anthropic API, its own scaffold | — | Gold standard, not ranked | 71/71 |

> **Why we're measuring Hy4-flash.** Tencent's Hy4-flash is a new open model
> that people will want to run on a pair of DGX Sparks. We're measuring it
> because the result is useful to them, not because we expect it to replace
> our default: it runs through the exact setup that gave our default its best
> score, and we'll publish the number whichever way it lands.
>
> **Why not the EXL3 build we already have.** We hold an EXL3 4-bit build of
> Hy4-flash, and it can't serve across two Sparks. The engine that reads it,
> ExLlamaV3, runs its tensor-parallel workers as local processes on one
> machine, so it can't split the model over the cable between the Sparks.
> On one Spark the build fits only by streaming part of the model from disk,
> through a reference code path with no server. The pair's own engine,
> TensorFold, does not support the architecture yet. So the measurement uses
> a different build of the same model: NVFP4 weights on vLLM, split across
> both Sparks. It is set up in our code and has not run on the Sparks yet.

**Data sources:** board JSON (`best`, `not_yet_run`, runs); 11-19 §4.2
(serving facts); `v4Archive` in `results.ts` (the v4 numbers, already on the
site); [HY4_FLASH_ENGINES-2026-11-19.md](reviews/HY4_FLASH_ENGINES-2026-11-19.md)
"Verdict" (the EXL3 limits, VERIFIED in code); [EXL3_PLAYBOOK.md case
(c)](EXL3_PLAYBOOK.md#c-an-architecture-no-build-supports);
[SPARK_HYDRA.md "Hy4-flash (NVFP4, vLLM TP2): measured"](SPARK_HYDRA.md#hy4-flash-nvfp4-vllm-tp2-measured)
(state, arm `H_T2`, "a public-interest measurement");
[TODO.md "Run `H_T2`"](TODO.md) ("compared with DS-T2 under the same
harness").

**Do not publish** (C3): the review's CLAIMED decode and prefill speeds,
Tencent's launch-table scores, the per-Spark memory figures, or the review's
own UNVERIFIED estimate of Hy4-flash's v5 score. The Hy4 row says "pending"
until its board row exists.

**Implementation note:** a `TechnicalPage` with one `DataTable`. Build the
measured rows from `board` and `runs` (pass count, fraction, number of arms
= runs per model); keep the "Runs on", "Weights" and "Status" cells in a
small typed array in a new `src/app/models/models.ts`, since the board
doesn't carry them. The Hy4 row is static text until a resync brings a run.
Add `/models/` to `sitemap.ts`, `llms.txt`, a social card (`models`), and
an e2e test that the Hy4 row contains "pending" and no `/\d+\/71/`.

### 4.3 P3. "Own your stack": upgrade `/offline/`

**Where:** `/offline/`, retitled in its hero; keep the URL and the header
label "Offline". This merges 11-19's N3 and N4 with two new blocks into one
section, "Own your stack", above the existing content.

**Copy, ready to paste** (each block's label is in brackets; print it as
the existing chips do):

> **Own your stack.** The models, the engines, the code and the way back
> from a dead disk all live on hardware you hold. Nothing on this page
> needs a cloud account.
>
> **Two models, one command.** [UPSTREAM MAIN · after v1.9] The Spark pair
> keeps its models ready on disk and serves one at a time. One command
> switches the pair, checks the new model before it takes traffic, and falls
> back if the check fails. Clients pick up the change on their own.
>
> **Develop with the internet unplugged.** [MEASURED · opencode, 2026-11-18;
> UPSTREAM MAIN · offline git hub] opencode runs on both Sparks against the
> pair's own model over loopback: no network, no model-provider key. When
> GitHub is unreachable, the Sparks and a laptop exchange branches through a
> git hub on the lead unit, or by drive with git bundles, review and merge
> there, and sync back when the connection returns.
>
> **Bring a model on a drive.** [MEASURED for DeepSeek packs; PLANNED for the
> rest] A read-only preflight checks a model folder before it touches the
> pair: architecture, fit, disk. A pack the pair already understands drops
> in with one command that verifies, benchmarks and rolls back on failure.
> The preflight also says plainly when a build can't serve on the pair, and
> why.
>
> **One backup, any number of Sparks.** [PLANNED] A golden image of the
> operating system that skips the vendor's online first boot, each unit's
> identity restored from its own small bundle, and the OpenBeast layer
> (code, secrets, services, weights, images) on one encrypted 4 TB SSD.
> Anything derivable is rebuilt offline instead of copied. The tools are
> written and tested against fakes; the first hardware drills are next.

Drop 11-19 N4's recovery-time estimate from the copy: it stays PLANNED
until drill G runs, and the website `AGENTS.md` keeps unrun figures out of
production.

**Data sources:** [SPARK_HYDRA.md Quickstart](SPARK_HYDRA.md#quickstart)
(`beast-spark`, the canary and fallback), [BEAST_SLOT.md](BEAST_SLOT.md)
(client autosync); [SELF_DEVELOPMENT.md "opencode on the Sparks"](SELF_DEVELOPMENT.md#opencode-on-the-sparks),
[§11](SELF_DEVELOPMENT.md#11-developing-with-github-unreachable),
[COMMANDS.md §10](COMMANDS.md#10-git-offline);
[EXL3_PLAYBOOK.md "The answer in short"](EXL3_PLAYBOOK.md#the-answer-in-short);
[SPARK_BACKUP_RESTORE.md §0](SPARK_BACKUP_RESTORE.md#0-the-verdict) and
[§3.2](SPARK_BACKUP_RESTORE.md#32-the-golden-base-image).

**Implementation note:** one `TechnicalSection id="own-your-stack"` with a
`FeatureGrid` of four items, each with `status` set to its label (the
`FeatureGrid` items already take `status`). Link each block to its doc at
the pinned revision. Don't print host names, paths, versions of opencode,
or the `beast-spark` verbs (they are operator detail and change). Update
the page title and description ([§6.1](#61-page-metadata)).

### 4.4 P4. The model policy statement

**Where:** `/security/` as its own section "Model policy:
uncensored-preferred" (this is 11-19 N7, now with the quote), and one line on
`/about/` under the founder block linking to it.

**Copy, ready to paste:**

> **Model policy: uncensored-preferred.**
>
> Stock models are allowed. Where an uncensored variant of comparable
> quality exists, OpenBeast prefers it.
>
> > "It's uncensored-preferred, as we want to enable researchers, lawyers,
> > and professionals to get the most honest and neutral answers when
> > weighing critical business decisions (some of these models have
> > alignment and post-training that lead to wrong decisions)."
> > (Max Khan, founder, November 19, 2026)
>
> The policy is built into the router [UPSTREAM MAIN · after v1.9]: when
> more than one deployment can serve a request, an uncensored model goes
> first among equals. It never overrides health, capacity, context fit or an
> operator's explicit choice, and a stock model always serves when the
> uncensored one can't. The Spark pair's default, DeepSeek-V4.1-Flash (Mia
> EXL3), is stock; the RTX 5090 rig's default and the pair's GLM-5.3-Flash
> alternative are abliterated.
>
> Refusal behaviour is a property of the model. Device keys, human roles,
> scoped files, tool checks, sandboxing and audit are platform controls, and
> they apply to every model alike. The operator is responsible for how the
> models are used.

**Data sources:** [BEAST_HYDRA.md "Family policy"](BEAST_HYDRA.md#family-policy)
(the decision, the verbatim quote, the router behaviour in the
`uncensored-preferred` row); 11-19 Rule B and X1.

**Implementation note:** a `TechnicalSection id="model-policy"` above the
trust-model paragraph, replacing 11-19 X1's inline paragraph (don't ship
both). The quote is a `<blockquote>` with a `<cite>`. Whether to print the
parenthetical and the attribution is Q-F; if Max declines, use the
shortened sentence from 11-19 Rule B with no quote marks. Don't say the
Sparks run the router: hydra routing to the Sparks is still open
(`/hardware/` "Open validation").

### 4.5 P5. Upgrades

Each is independent; ship any subset.

#### U1. An interactive board

`/benchmarks/` today renders static tables. Make the run table a client
component (`src/components/BoardExplorer.tsx`, `"use client"`): filter by
model, toggle "best per model" / "every run", sort by SCORE, pass count or
mean time, and show the per-language cells as a row of small bars. Data:
import `results-build.json` at build (no fetch needed, so the CSP stays
untouched). Render the full static table first and enhance it, so the page
works with JavaScript off and the e2e text assertions still hold. Labels
(single seed, experiment arm, the footnote marks) must survive every
filter state. **Never** add a unit-level view: the board has no unit
column, and the explorer must not grow one (Rule E).

#### U2. Per-language charts

A small-multiples chart under "Passes by language": one row per language,
one bar per model at its best (DS-T2, GLM-C) against the gold standard,
values from `breakdown`. Draw it with HTML/CSS bars like `/hardware/`'s
serving chart (no chart library). Percentages rounded half up (11-19 §4.1
convention). Caption: "Single seed per run. Units per language: Python 32,
Go 8, C 8, C++ 8, Rust 8, Zig 6, Shell 1." Small languages (Zig 6, Shell 1)
must show their counts next to the bar, since one unit is 17 or 100 points.

#### U3. A changelog feed

A `/changes/` page and `/changes.xml` (Atom), generated at build from a
hand-kept `src/app/changes/changes.json` in the website repo: one entry per
board addition, release and site deploy, each with a date, a title, one
sentence, and a link to its pinned source. Seed it with: 2026-11-13 v1.9.0;
2026-11-16 GLM ladder; 2026-11-18 DeepSeek-V4.1-Flash (Mia EXL3) becomes
the Spark default; 2026-11-18/19 arms T and T2; 2026-11-19
uncensored-preferred policy; 2026-11-19/20 arms T_s1 and T3; and this
deploy. Write the XML from a build script (`scripts/generate-feed.mjs`) next
to `generate-headers.mjs`, and add `<link rel="alternate"
type="application/atom+xml">` in `layout.tsx`. Entries obey C2: no entry
for a run without a board row.

#### U4. `llms.txt` refresh, and `llms-full.txt`

Replace `llms.txt` with [§6.2](#62-llmstxt-the-full-replacement). Then
consider an `llms-full.txt`: the text of every page, generated from the
build output by a script, so agents that read it get the same facts as the
pages without a crawl. Generated, never hand-written.

#### U5. Generated dates and facts

Three hand-kept values have drifted already: `updated` in `project.ts`
(still "2026-11-17"), the numbers in `llms.txt` (still GLM's), and the
model names in prose. Two cheap fixes:

- Set `updated` at build from the deploy date, or from the newest board
  run's date.
- Generate the `llms.txt` evaluation lines from `results-build.json` in a
  build step (a template with `{{best.passed}}` etc.), and add an e2e test
  that every number in `llms.txt` appears in `results-build.json`.

#### U6. Structured data for the board

Add a schema.org `Dataset` JSON-LD block to `/benchmarks/` (name "OpenBeast
v5 agentic coding results", `dateModified`, `license` Apache-2.0,
`isBasedOn` the pinned `RESULTS.md` URL, `variableMeasured` passed units,
SCORE). It goes through `TechnicalPage`'s existing JSON-LD path, and its
inline script gets a CSP hash like the others.

---

## 5. Data

Generated from `evals/v5_board.py --json` on OpenBeast `main` at `06bd875c`
(2026-11-20). SCORE = 0.75·SOLVE + 0.25·LANG. The gold-standard fraction is
SCORE ÷ 100.0 (Opus). Mean / unit is the agent's wall time per unit. **Do
not hand-type these into the site**: the resync produces them. Check the
rendered pages against them.

### 5.1 The board: each model at its best configuration

Unchanged from 11-19 §4.1. T3 did not displace T2: both passed 65/71, and
the board ranks by SCORE (T2 94.9, T3 91.5).

| Rank | Model | Run | Passed | SCORE | SOLVE / LANG | Of gold | Py | Go | C | C++ | Rust | Zig | Sh | Mean / unit | Date |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | DeepSeek-V4.1-Flash (Mia EXL3) | DS-T2 | 65/71 | 94.9 | 96.9 / 88.9 | 94.9% | 30/32 | 7/8 | 7/8 | 7/8 | 8/8 | 5/6 | 1/1 | 20.7 min | 2026-11-19 |
| 2 | GLM-5.3-Flash | GLM-C | 51/71\* | 76.7 | 75.4 / 80.5 | 76.7% | 24/32 | 6/8 | 6/8 | 6/8 | 5/8 | 3/6 | 1/1 | 29.0 min | 2026-11-16 |
| gold standard | Claude Opus 5.5 | OPUS-REF | 71/71 | 100.0 | 100.0 / 100.0 | 100.0% | 32/32 | 8/8 | 8/8 | 8/8 | 8/8 | 6/6 | 1/1 | 3.7 min | 2026-11-16 |
| — | Qwen3.8 27B Uncensored MTP (the shipped default) | — | — | — | — | — | | | | | | | | | No v5 result yet |
| — | Qwen3.6 27B (v4 #1) | — | — | — | — | — | | | | | | | | | No v5 result yet |

\* 54 raw. Three units of one task family were withheld after an integrity
scan found the agent had read sealed harness files.

Host, status and configuration columns: as 11-19 §4.1.

### 5.2 Every v5 configuration measured

| Model | Run | Arm | Passed | SCORE | SOLVE | LANG | Of gold | Py | Go | C | C++ | Rust | Zig | Sh | Mean / unit | Date | Status (label only) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| DeepSeek-V4.1-Flash (Mia EXL3) | DS-T | T | 61/71 | 88.5 | 84.6 | 100.0 | 88.5% | 27/32 | 7/8 | 7/8 | 7/8 | 7/8 | 5/6 | 1/1 | 22.5 min | 2026-11-18 | experiment arm · integrity scan: 0 contaminated |
| DeepSeek-V4.1-Flash (Mia EXL3) | DS-Ts1 | T_s1 | 61/71 | 85.7 | 84.6 | 88.9 | 85.7% | 27/32 | 7/8 | 7/8 | 7/8 | 7/8 | 5/6 | 1/1 | 22.5 min | 2026-11-19 | experiment arm (seed repeat of T) · integrity scan: 0 contaminated |
| DeepSeek-V4.1-Flash (Mia EXL3) | DS-T2 | T2 | 65/71 | 94.9 | 96.9 | 88.9 | 94.9% | 30/32 | 7/8 | 7/8 | 7/8 | 8/8 | 5/6 | 1/1 | 20.7 min | 2026-11-19 | experiment arm · integrity scan: 0 contaminated |
| DeepSeek-V4.1-Flash (Mia EXL3) | DS-T3 | T3 | 65/71 | 91.5 | 90.8 | 93.9 | 91.5% | 28/32 | 7/8 | 8/8 | 8/8 | 8/8 | 5/6 | 1/1 | 18.2 min | 2026-11-19 | experiment arm · integrity scan: 0 contaminated |
| GLM-5.3-Flash | GLM-L0 | L0 | 26/71 | 48.2 | 50.8 | 40.5 | 48.2% | 16/32 | 3/8 | 1/8 | 3/8 | 3/8 | 0/6 | 0/1 | 6.8 min | 2026-11-16 | board protocol (L0) |
| GLM-5.3-Flash | GLM-A | A | 31/71 | 56.9 | 60.0 | 47.7 | 56.9% | 19/32 | 2/8 | 3/8 | 3/8 | 2/8 | 1/6 | 1/1 | 6.7 min | 2026-11-16 | board protocol (L0) |
| GLM-5.3-Flash | GLM-B | B | 36/71 | 60.4 | 63.1 | 52.3 | 60.4% | 19/32 | 4/8 | 3/8 | 4/8 | 5/8 | 0/6 | 1/1 | 11.7 min | 2026-11-16 | experiment arm |
| GLM-5.3-Flash | GLM-C | C | 51/71\* | 76.7 | 75.4 | 80.5 | 76.7% | 24/32 | 6/8 | 6/8 | 6/8 | 5/8 | 3/6 | 1/1 | 29.0 min | 2026-11-16 | experiment arm |
| GLM-5.3-Flash | GLM-E | E | 50/71\*\* | 74.7 | 72.3 | 81.7 | 74.7% | 22/32 | 6/8 | 5/8 | 6/8 | 6/8 | 4/6 | 1/1 | 28.5 min | 2026-11-17 | experiment arm |
| Claude Opus 5.5 | OPUS-REF | reference | 71/71 | 100.0 | 100.0 | 100.0 | 100.0% | 32/32 | 8/8 | 8/8 | 8/8 | 8/8 | 6/6 | 1/1 | 3.7 min | 2026-11-16 | reference, not ranked |

\* As in §5.1. \*\* One unit was failed by a validator bug that was later
fixed. The score is not adjusted.

Every DeepSeek arm carries the label "experiment arm (beast-assist
diagnostics, awareness packs, wall-scaled turns, wall ×2)"; the table
shortens it. Every row ran one seed; `DS-Ts1` is the only seed repeat
(of `DS-T`). The four-unit older-validator footnote that the board puts on
every row is as 11-19 §4.1 says (optional, count only).

**The configuration column for the two new rows** (the board's label,
verbatim; the site may shorten it to the first clause):

- DS-Ts1: "seed-noise arm T_s1: arm T under --seed 1 (harness9); sets the
  noise margin for T vs T2"
- DS-T3: "campaign arm T3: T2 with a 4096-token per-turn thinking cap
  (harness9); trades 3 units each way against T2"

**Not on the board, not for the site** (C2; recorded here so the website
agents don't go looking): arm U (harness10) 59/71, finished 2026-11-20
(result file in the eval checkout, no board row); UP running; UP_8k and
UPA_8k defined (#250); `H_T2` (Hy4-flash) staged. When any of them gets a
board row, resync and add it to the ladder in [§4.1](#41-p1-how-we-evaluate-the-campaign-page)
with its one change; the copy that says "further arms are running" then
needs one edit pass.

### 5.3 Per-language share of the gold standard

Integer percentages, rounded half up (5/8 = 63%).

| Language | Gold | DS-T | DS-Ts1 | DS-T2 | DS-T3 | GLM-C |
|---|---:|---:|---:|---:|---:|---:|
| Python | 32 | 27 · 84% | 27 · 84% | 30 · 94% | 28 · 88% | 24 · 75% |
| Go | 8 | 7 · 88% | 7 · 88% | 7 · 88% | 7 · 88% | 6 · 75% |
| C | 8 | 7 · 88% | 7 · 88% | 7 · 88% | 8 · 100% | 6 · 75% |
| C++ | 8 | 7 · 88% | 7 · 88% | 7 · 88% | 8 · 100% | 6 · 75% |
| Rust | 8 | 7 · 88% | 7 · 88% | 8 · 100% | 8 · 100% | 5 · 63% |
| Zig | 6 | 5 · 83% | 5 · 83% | 5 · 83% | 5 · 83% | 3 · 50% |
| Shell | 1 | 1 · 100% | 1 · 100% | 1 · 100% | 1 · 100% | 1 · 100% |

The site's "Passes by language" leads with DS-T2 and GLM-C (11-19 B5). The
other columns are for the U2 chart and the explorer (U1).

### 5.4 Serving and hardware facts

Unchanged from 11-19 §4.2. One addition, MEASURED (read-only, both Sparks,
2026-11-19): native TensorFold 1.2.10 is installed and does not support
`deepseek_v41` or `hy_v4_flash`; the default serves through the recipe's
engine build ([EXL3_PLAYBOOK.md §1.2](EXL3_PLAYBOOK.md#12-the-engine-builds)).
The site needs only the sentence in D2.

---

## 6. SEO, metadata and llms.txt

### 6.1 Page metadata

11-19 §6 stands (the `/benchmarks/` and `/hardware/` descriptions). Add:

| Where | Live (exact) | Replacement |
|---|---|---|
| `/research/campaign/` title and description (`campaign/page.tsx:7`) | title "Model campaign"; "Onboard a model on DGX Spark, profile a v5 baseline, audit failures and run the thinking-first harness campaign from a pinned eval host." | title "How we evaluate"; "How OpenBeast measures local models: one change per run, a measured seed-noise margin, sealed evaluation and integrity scans. The DeepSeek-V4.1-Flash (Mia EXL3) ladder." (153 characters) |
| `/models/` (new, P2) | — | title "Models we've measured"; "Every local model OpenBeast has measured on its own hardware, with v5 agentic-coding results, plus the ones still pending, such as Tencent's Hy4-flash." (153 characters) |
| `/offline/` title and description (P3) | (keep the title "Offline") | description: "Own your stack: switch models with one command, develop with the internet unplugged, bring a model on a drive, and restore from an offline backup (planned)." (157 characters) |
| `/security/` description | (check it in the source) | If it mentions abliterated defaults, end it with "Model policy: uncensored-preferred." |
| `article.headline` JSON-LD on `/research/campaign/` | (if set) | "How OpenBeast evaluates local models" |
| `sitemap.ts` | 13 paths + papers | add `/models/` (P2) and `/changes/` (U3) if shipped |
| Social cards | `openbeast-social-campaign.png` | Regenerate `campaign` with the new title; add `models` and `changes` cards. The generator has no numbers (11-19 checked), so the cards can't go stale on scores |

Search terms the new copy carries naturally (don't stuff): "DGX Spark",
"DeepSeek-V4.1-Flash", "Hy4-flash DGX Spark", "local AI coding benchmark",
"uncensored local model", "offline AI workstation", "how to evaluate local
LLMs".

### 6.2 `llms.txt`: the full replacement

Replace `public/llms.txt` with this text. `<revision>` is the pinned
OpenBeast commit from [§3.8](#38-code-changes-amends-11-19-43) step 1.
Lines marked `(P2)` and `(U3)` go in only if those proposals ship. The
lines under "Website" keep the order of the header navigation.

```text
# OpenBeast

> OpenBeast is an Apache 2.0 private AI workstation for hardware you own. It combines local inference, a 19-tool MCP agent surface, Mac and Linux clients, browser and phone interfaces, offline operation and reproducible evaluation.

## Product and hardware

- v1.9.0 (2026-11-13) is the latest tagged release. It brought up spark-hydra, a reference system with two NVIDIA DGX Sparks on DGX OS. Later work on the public main branch is labelled upstream development.
- On the Spark pair, TensorFold serves DeepSeek-V4.1-Flash (Mia EXL3) by default since November 18: stock EXL3 weights at 2.9 bits per weight, tensor parallel across both Sparks, a 524,288-token context per conversation, four streams, thinking on. GLM-5.3-Flash (abliterated, 1,048,576-token context) is the staged alternative; one command switches between them.
- The recommended agent setup for the default model is one line in openbeast.conf, AGENT_PROFILE=deepseek-best (v5 arm T2).
- The other reference system uses an RTX 5090, a Ryzen 9950X3D and 128 GB DDR5. llama.cpp serves the default Qwen3.8 27B Uncensored MTP Q5_K_M profile at its 262K native context.
- The Spark pair and the RTX 5090 currently serve independently. Hydra cross-host routing and failover require physical validation. The Spark fabric runs at MTU 9000; its speed effect has yet to be measured.

## Evaluation

- v5.0 includes 71 scored units from 34 base tasks, spanning 12 technical categories and six programming languages, plus one shell unit. v5 measures agentic coding with thinking on, not general chat. The v4 board is a frozen archive with a different methodology.
- DeepSeek-V4.1-Flash (Mia EXL3) on the Spark pair passed 65/71, SCORE 94.9, with arm T2, its recommended agent setup. It is #1 on the v5 board. Its four arms: T 61/71, T_s1 (T with a different seed) 61/71, T2 65/71, T3 (half T2's thinking cap) 65/71 with a lower SCORE.
- Single seed per run. The seed repeat of arm T passed the same count with one unit swapped each way: run-to-run noise is about one unit.
- GLM-5.3-Flash passed 26/71 in the November 16 baseline and 51/71 scored with thinking and a revised harness (SCORE 76.7; three units withheld after an integrity scan).
- Claude Opus 5.5 passed 71/71 in its own Claude Code scaffold and serves as the unranked gold standard. DeepSeek's best row reached 94.9% of its SCORE, GLM's 76.7%. The different scaffolds and single seeds bound the comparison.
- Tencent's Hy4-flash is staged for the same harness as a public-interest measurement; no result yet. Further harness arms are running and are published when they reach the board. The paper asking whether a unified harness generalizes across models is in progress.

## Operation and trust

- Clients run tools next to their own files. Selected content can travel to the rig as model context. beast-gate authenticates devices and scopes remote inference. (Keep the rest of the live line 21 unchanged.)
- OpenBeast's model policy is uncensored-preferred: stock models are allowed, and uncensored variants of comparable quality are preferred. The RTX 5090 rig's default and the Spark pair's GLM alternative are abliterated; the Spark pair's default is stock. Tool access policy, optional sandboxing and audit remain separate controls. The Spark host-network boundary and proposed keyed native engine are documented on the security page.
- beast-island lets local work continue after losing the internet route. Signed install bundles, staged engines, mirrored platform docs and cold-storage packages support disconnected operation. opencode runs on the Sparks against the local model with no network. The full offline rebuild of the Spark pair, from a golden image and an encrypted backup drive, is planned and not yet drilled on hardware.
- The investment pages describe a commercial hypothesis. Licences for the Spark weights and recipes, longer-duration operation, physical fleet recovery and repeat external deployments remain diligence items.

## Website

- [Home and install](https://openbeast.ai/)
- [Benchmarks and frozen v4 archive](https://openbeast.ai/benchmarks/)
- [Models we've measured](https://openbeast.ai/models/)   (P2)
- [Reference hardware and serving measurements](https://openbeast.ai/hardware/)
- [Offline operation and owning your stack](https://openbeast.ai/offline/)
- [Desktop, browser and phone clients](https://openbeast.ai/clients/)
- [Security, model policy and published review record](https://openbeast.ai/security/)
- [Tagged releases](https://openbeast.ai/releases/)
- [Changes](https://openbeast.ai/changes/)   (U3)
- [Research](https://openbeast.ai/research/)
- [Post-training repair paper](https://openbeast.ai/research/post-training-repair-llama-cpp/)
- [Harness engineering](https://openbeast.ai/research/harness/)
- [How we evaluate](https://openbeast.ai/research/campaign/)
- [About](https://openbeast.ai/about/)
- [Investors](https://openbeast.ai/investors/)
- [Public investment case](https://openbeast.ai/investors/investment-case/)

## Pinned project sources

- [Architecture](https://github.com/MaximilianKhan/openbeast/blob/<revision>/docs/ARCHITECTURE.md)
- [v5 score record](https://github.com/MaximilianKhan/openbeast/blob/<revision>/docs/RESULTS.md#v5-leaderboard--best-configuration-per-model)
- [Spark serving conditions](https://github.com/MaximilianKhan/openbeast/blob/<revision>/docs/SPARK_HYDRA.md#deepseek-deployment)
- [DeepSeek best configuration](https://github.com/MaximilianKhan/openbeast/blob/<revision>/docs/SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2)
- [Model campaign](https://github.com/MaximilianKhan/openbeast/blob/<revision>/docs/MODEL_CAMPAIGN.md)
- [Model policy](https://github.com/MaximilianKhan/openbeast/blob/<revision>/docs/BEAST_HYDRA.md#family-policy)
- [v1.9.0 release notes](https://github.com/MaximilianKhan/openbeast/blob/1b06a907041ffd04875f520eb53179b621168280/docs/RELEASE_NOTES_v1.9.0.md)
```

Two notes on that text. Line 7 of the live file lists the November
upstream components in its second sentence; keep that sentence if the
website agents prefer it to "Later work … is labelled upstream
development". And the first "Operation and trust" line keeps the live
line 21 verbatim after its third sentence (it's unchanged and long).
Remove the `(P2)`/`(U3)` markers before shipping.

---

## 7. Acceptance checklist

Run 11-19's [§7](WEBSITE_UPDATE_2026-11-19.md#7-acceptance-checklist) steps
1 to 9 first, with these changes: step 5's `61/71` check passes on the
four-rung ladder; step 9 also ticks this page's TODO line. Then run these,
on the same fetched directory `$d` (11-19 step 2; add `models` and
`changes` to its page list if P2 and U3 shipped).

**N1. Status of 11-19 resolved** (each prints `0`):

```sh
grep -c 'last-modified: Wed, 18 Nov 2026 01:37:35 GMT' <(curl -sI https://openbeast.ai/)
grep -l -E 'TensorFold serves GLM|DeepSeek v4\.1 Flash|Uncensored-only fleet|One local model has been measured' "$d"/* | wc -l
```

**N2. The new facts are present** (each `>= 1`):

```sh
grep -c -E 'T_s1|seed repeat' "$d"/benchmarks.html "$d"/research_campaign.html
grep -c '85\.7' "$d"/benchmarks.html                      # the T_s1 rung
grep -c '91\.5' "$d"/benchmarks.html                      # the T3 rung
grep -ci 'about one unit' "$d"/benchmarks.html "$d"/home.html "$d"/llms.txt
grep -c 'How we evaluate' "$d"/research_campaign.html "$d"/llms.txt
grep -c 'Native TensorFold 1\.2\.10' "$d"/hardware.html
```

**N3. Rule E: nothing new leaks** (each prints nothing). This extends
11-19's fifth grep with the strings the new board notes would bring in:

```sh
grep -l -iE 'iterate-and-test|long-derivation|flagged network attempt|evals/audit/reviews|swapped three' "$d"/*
grep -l -iE 'algo-v1|algorithmic procedure pack|planning lines|bound first|brute force' "$d"/*
grep -l -E '[0-9]{2,3}_[a-z]+_[a-z_]+|dlopen|forbidden-API|comments/docstrings|pre-#223' "$d"/*
```

**N4. No unboarded numbers, no internal process** (each prints nothing):

```sh
grep -l -E '59/71|\bU_8k\b|\bUPA?_8k\b|\bH_T2\b' "$d"/*
grep -l -iE 'Hy4-flash[^<]{0,200}[0-9]+/71|[0-9.]+ tok/s[^<]{0,80}Hy4|Hy4[^<]{0,80}[0-9.]+ tok/s' "$d"/*
grep -l -iE 'TensorFold (v)?1\.3|Mia GLM recipe' "$d"/*
grep -l -iE 'GitHub Actions|CI is (off|disabled)|continuous integration' "$d"/*
grep -l -E 'tail4109f9|100\.66\.|100\.110\.|10\.100\.21[67]\.|~/\.local/bin' "$d"/*
```

(The third line deliberately does not match a bare `1.3.0`: `/releases/`
lists OpenBeast's own v1.3.0. All five lines print nothing on the live site
of 2026-11-20.)

**N5. Labels.** Every block from [§4.3](#43-p3-own-your-stack-upgrade-offline)
shows its label chip; the backup block says PLANNED; the Hy4 row on
`/models/` says PLANNED and "pending":

```sh
grep -c 'PLANNED' "$d"/offline.html                         # >= 1
grep -c -E 'pending' "$d"/models.html                        # >= 1 (if P2 shipped)
```

**N6. The quote is verbatim** (if Q-F is answered yes):

```sh
grep -c 'some of these models have alignment and post-training that lead to wrong decisions' "$d"/security.html   # 1
```

**N7. Record the deploy.** As 11-19 step 9, and also tick the TODO line for
this page and write "applied in website `<commit>`" at the top of this
page.

---

## 8. Open questions for Max

**Carried over from 11-19 (all unanswered as of 2026-11-20):**

- **Q-A. The licence of Mia's DeepSeek EXL3 pack** for a supported Spark
  offer (11-19 Q-A). D5 says only that a review is required per model.
- **Q-B. Name Mia's lab on the site?** Credit MiaAI-Lab on `/hardware/` and
  `/models/`, or keep "Mia EXL3" as a bare label? (11-19 Q-B)
- **Q-C. Per-model ladders or one campaign chart?** (11-19 Q-C.) This page
  keeps two ladders (DeepSeek's four arms, GLM's five) and puts DeepSeek's
  on "How we evaluate". Confirm.
- **Q-D. The GLM-E footnote's history:** silent rewording, or say it was
  reworded? (11-19 Q-D; recommendation unchanged: silent.)
- **Q-E. "Experiment arm" in headlines,** or on the board only? (11-19 Q-E;
  recommendation unchanged: the board, plus "with its recommended agent
  setup" in the hero.)

**New:**

- **Q-F. Publish your full quote, with attribution?** P4 quotes you
  verbatim from BEAST_HYDRA.md, including the parenthetical "(some of these
  models have alignment and post-training that lead to wrong decisions)",
  signed "Max Khan, founder". The parenthetical is the sharpest part and
  the one a reader will push back on. Options: full quote and name
  (recommended: it's your decision and your reason); the sentence without
  the parenthetical; or the policy with no quote.
- **Q-G. Approve the proposals?** P1 (How we evaluate), P2 (Models page),
  P3 (Own your stack), P4 (policy section), and U1 to U6. Recommendation:
  ship P1, P3, P4, U5 and U6 with the 11-19 update; P2 once Hy4's status is
  confirmed for publication (Q-I); U1 to U4 as a second website PR.
- **Q-H. A new `/models/` URL, or a section on `/benchmarks/`?**
  Recommendation: the new URL. It's the page people will link to when they
  ask "what runs on two Sparks".
- **Q-I. Announce Hy4-flash before it runs?** P2 and B7 say it is staged
  as a public-interest measurement with no result. That commits us
  publicly to publishing the number "whichever way it lands". Yes, or keep
  Hy4 off the site until `H_T2` has a board row?
- **Q-J. The Mia GLM recipe on TensorFold 1.3.0.** Nothing about it goes
  on the site now (C3). If it lands and beats today's GLM recipe, does the
  site keep GLM-5.3-Flash as "the alternative", or does a GLM upgrade get
  its own changelog entry and hardware line?
- **Q-K. Name harness10's arms when they land?** E3 lets the site name a
  run's one change but not the contents of the planning lines or the
  algorithmic pack. If UP or UPA becomes a recommended setup, is "a short
  planning checklist" / "a procedure pack" enough description for you, or
  should those arms stay off the public ladder altogether?
- **Q-L. The cold-start 502.** The first request to `/` answered a
  Cloudflare 502 on both 11-19 and 11-20. Do you want the website agents to
  fix the Mini's origin wake-up (a keep-alive or a health ping), or is it
  acceptable?

**Out of scope** (as 11-19 §8, plus): any number from arms U, UP, UP_8k,
UPA, UPA_8k or `H_T2` until it has a board row; the Mia GLM recipe on
TensorFold 1.3.0; the CI setting (Rule F); a recovery time for backup and
restore before drill G.
