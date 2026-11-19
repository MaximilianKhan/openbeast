# openbeast.ai update, 2026-11-19: handoff for the website agents

**Status: plan, ready to implement.** Written 2026-11-19 for the agents that
maintain openbeast.ai (the private repo `MaximilianKhan/openbeast-website`).
This page is self-contained: it quotes what the live site says, gives the
replacement copy ready to paste, and names the source of truth for each
claim. The master plan is [`WEBSITE_PLAN.md`](WEBSITE_PLAN.md). Its stale
claims S53–S57 ([Stale since the third revision](WEBSITE_PLAN.md#stale-since-the-third-revision-2026-11-19))
are the short form of this page, and its accuracy rules
([§3](WEBSITE_PLAN.md#3-accuracy-rules)) still apply. Where the two differ,
this page is newer.

**How this page was made.** The live text was read on 2026-11-19 with
`curl -sL` from every page in the sitemap, plus `/llms.txt`. The live build
is website commit `e72790c` ("Refine typography and geometry across
OpenBeast.ai"), Next.js 16.4.5, `last-modified: Wed, 18 Nov 2026 01:37:35 GMT`.
File and line references (`src/app/page.tsx:39`) are to that commit. The
numbers come from `evals/v5_board.py --json` on OpenBeast `main` at
`55a6a8f7` (#242). Serving facts come from
[`SPARK_HYDRA.md`](SPARK_HYDRA.md), [`MODELS.md`](MODELS.md),
[`ARCHITECTURE.md`](ARCHITECTURE.md) and the README.

**Labels.** **MEASURED**: run on our hardware, with a record in the repo.
**SHIPPED (upstream main)**: merged on OpenBeast `main` after the v1.9.0 tag
(the site already calls this "UPSTREAM MAIN · after v1.9"). **PLANNED**:
designed, and maybe tested against fakes, but not run on hardware. Every new
claim on the site carries one of these.

| Section | What it gets you |
|---|---|
| [1. Summary](#1-summary) | what changed and why the site must change |
| [2. Rules for this update](#2-rules-for-this-update) | the naming, policy and eval-safety rules |
| [3. Page-by-page changes](#3-page-by-page-changes) | the live quote, the replacement, the source of truth |
| [4. Data updates](#4-data-updates) | the board rows, the serving facts, the code in `results.ts` |
| [5. New sections to add](#5-new-sections-to-add) | proposed copy, each labelled |
| [6. SEO and metadata](#6-seo-and-metadata) | meta descriptions, `llms.txt`, social cards |
| [7. Acceptance checklist](#7-acceptance-checklist) | what to verify after deploy, and how |
| [8. Out of scope, and open questions for Max](#8-out-of-scope-and-open-questions-for-max) | what is not in this update |

---

## 1. Summary

The site was last deployed on the evening of 2026-11-17. The following
changed after that deploy:

1. **The Spark pair's default model changed.** Since 2026-11-18 the two DGX
   Sparks serve **DeepSeek-V4.1-Flash (Mia EXL3)** by default. It runs with
   TensorFold TP 2 (one rank per Spark), a 524,288-token context per
   conversation, four streams and thinking on. Max chose it after hands-on
   testing. **GLM-5.3-Flash** is the staged alternative. One command,
   `beast-spark`, switches between the two. The live site still says
   TensorFold serves GLM-5.3-Flash on the pair, with a 1,048,576-token window.
2. **DeepSeek leads the v5 board.** Its arm T2 (run `DS-T2`) scored
   **65/71, SCORE 94.9**, which is 94.9% of the gold standard's SCORE. Its
   arm T (run `DS-T`) scored 61/71, SCORE 88.5. GLM-5.3-Flash's best is
   51/71 (SCORE 76.7). The Claude Opus 5.5 reference passed 71/71: it is the
   gold standard, not a ranked peer, because it used a different scaffold.
   The live site still shows GLM's 51/71 as the pair's result and says
   "DeepSeek v4.1 Flash is next".
3. **DeepSeek serves stock weights.** It is not abliterated and not
   uncensored (`ABLIT=0`). The live site says "The default weights on the
   reference systems are abliterated" and carries a risk row titled
   "Uncensored-only fleet policy". Neither is true now.
4. **The model policy is decided: uncensored-preferred.** Max decided this on
   2026-11-19. Stock models are allowed. Where an uncensored variant of
   comparable quality exists, OpenBeast prefers it, "to enable researchers,
   lawyers, and professionals to get the most honest and neutral answers when
   weighing critical business decisions". The site states this as decided
   policy. A separate PR is bringing the hydra router's family policy in line
   with it; the site does not need to wait for that PR.
5. **New capabilities are on `main`,** and some are worth a line on the site:
   model switching with `beast-spark`, the `deepseek-best` agent preset,
   opencode on the Sparks with no network, client autosync, offline git and
   backup and restore, the EXL3 intake playbook, and eval integrity
   hardening. [Section 5](#5-new-sections-to-add) gives the copy and the
   label for each.
6. **The site publishes a checker detail it should not publish.** The
   GLM-E footnote on `/benchmarks/` and `/research/harness/` quotes the
   pattern a validator lint matched in one unit. A resync of the board as
   it stands would add more lint text, and a DeepSeek run note that names
   what an agent read while solving a unit. [Rule E](#e-eval-safety-score-tables-only)
   and [§4.3](#43-code-changes-in-the-website-repo) say how to keep that off
   the site.

The site has to change because its headline claims are about the pair: what
the pair serves, its context window, its best v5 score and what runs next.
All four are now wrong. The site also states a model policy that no longer
holds.

---

## 2. Rules for this update

These add to [WEBSITE_PLAN.md §3](WEBSITE_PLAN.md#3-accuracy-rules). Put
rules A to E in the website repo's `AGENTS.md`, under "OpenBeast evidence and
publication".

### A. Model names

- The default is **"DeepSeek-V4.1-Flash (Mia EXL3)"**, written exactly like
  that, every time. The pack's folder and cache id carry a version suffix.
  Never print that suffix, and never write "DeepSeek v4.1 Flash" or
  "DeepSeek-V4.1-Flash-1017" in body copy. The served id
  `DeepSeek-V4.1-Flash-1017` may appear only in a code sample that shows
  what a client sees.
- The alternative is **"GLM-5.3-Flash"**. Say "abliterated" where its
  weights matter (the hardware plate, security copy). The GLM ladder rows
  keep their run ids `GLM-L0` … `GLM-E`.
- Name a configuration by its run id (`DS-T2`, `DS-T`, `GLM-C`) and its
  campaign arm (T2, T, C). Don't call arm T2 "the default model". T2 is the
  default **agent setup** for that model (`AGENT_PROFILE=deepseek-best`).

### B. Model policy

- Policy wording: **"OpenBeast's model policy is uncensored-preferred."** The
  explanation, ready to paste: "Stock models are allowed. Where an
  uncensored variant of comparable quality exists, OpenBeast prefers it, to
  enable researchers, lawyers, and professionals to get the most honest and
  neutral answers when weighing critical business decisions."
- Never call the Spark default uncensored or abliterated. Of the models the
  site names, the RTX 5090 rig's default (Qwen3.8 27B Uncensored MTP) and the
  pair's GLM-5.3-Flash alternative are abliterated. DeepSeek-V4.1-Flash
  (Mia EXL3) is stock.
- Keep the existing rule: refusal behaviour (a model property) is separate
  from RBAC, file scopes, sandboxing, device keys and audit (platform
  controls).

### C. Numbers

- Every v5 number says **single seed**. DeepSeek's seed arm (`T_s1`) is
  running. Until it lands, no difference between two v5 rows is called
  significant, and no row is called "better by N". The ranking is the
  board's: "#1 on the v5 board".
- Every v5 number says what v5 measures: **agentic coding with thinking on**,
  71 units across six languages and one shell unit. It is not a chat or
  general-knowledge score. Say this once per page that shows a v5 number,
  near the number.
- Both DeepSeek rows and GLM's best row are **experiment arms**: wall-scaled
  turns, a 2× wall, and (for DeepSeek) diagnostics and awareness packs. The
  board says so in the status column. Keep that label.
- The gold-standard fraction is the SCORE divided by Opus's SCORE (100.0),
  shown next to the absolute pass count. Opus is never ranked.
- Spark serving numbers name the model they were measured on. The
  2026-11-13 table is **GLM's**. DeepSeek's numbers come from its own
  2026-11-17 bench ([§4.2](#42-serving-and-hardware-facts)).

### D. Status

- `beast-spark`, the `deepseek-best` preset, client autosync and the eval
  hardening are on `main` after v1.9.0. Label them "UPSTREAM MAIN · after
  v1.9", the site's existing chip. v1.9.0 is still the latest tag (checked
  2026-11-19), so the hero's "v1.9.0 SHIPPED" stays.
- Backup and restore is **PLANNED**. The tools exist and are tested against
  fakes. Neither has written a real backup, image or restore yet
  ([SPARK_BACKUP_RESTORE.md](SPARK_BACKUP_RESTORE.md)).

### E. Eval safety: score tables only

The v5 units are a live benchmark. Nothing on the site may help an agent or
a person solve one.

- **Publish:** pass counts, SCORE, SOLVE, LANG, per-language and
  per-category counts, run ids, dates, mean time per unit, the configuration
  label, the experiment-arm label, and footnote **counts** ("3 units
  withheld after an integrity scan").
- **Never publish:** unit solutions or approaches, reference algorithms,
  what a validator or lint checks or matches, the files or libraries an
  agent read to solve a unit, transcripts, or unit-by-unit pass and fail
  lists. Unit ids are not needed for any claim on the site, so leave them out.
- The live footnote "122_gemm_blocked_c is a validator false FAIL: the
  pre-#195 forbidden-API lint matched 'dlopen' inside a comment …" (on
  `/benchmarks/` and `/research/harness/`) breaks this rule. Replace it
  ([§3.2](#32-benchmarks-srcappbenchmarkspagetsx-srccomponentsarmladdertsx)).
- This tightens the website `AGENTS.md` line "Preserve exclusions and
  validator notes". Keep the fact that a note exists, and how many units it
  covers. Drop what the note says about the checker. Link to the repo for
  anyone auditing the run.

---

## 3. Page-by-page changes

Each row gives the **live** text (quoted from the page on 2026-11-19), the
**replacement** (ready to paste), and the **source** of truth. The Playwright
suite (`e2e/site.spec.ts`) pins several of these strings. Change the copy and
the test together ([§3.10](#310-e2esitespects)).

### 3.1 Home `/` (`src/app/page.tsx`)

| # | Section | Live (exact) | Replacement | Source |
|---|---|---|---|---|
| H1 | 01 "Two reference systems", the **MEASURED** card "spark-hydra · two DGX Sparks" (`page.tsx:167`) | "TensorFold serves GLM-5.3-Flash across two GB10s. A conversation can use a 1,048,576-token window. Four streams share one 1,986,560-token KV pool." | "TensorFold serves DeepSeek-V4.1-Flash (Mia EXL3) across two GB10s, with thinking on. A conversation can use a 524,288-token window, and four streams run at once. GLM-5.3-Flash is staged as the alternative, one command away." | [SPARK_HYDRA.md top](SPARK_HYDRA.md#spark-hydra-the-dgx-spark-launch-runbook) and [DeepSeek deployment](SPARK_HYDRA.md#deepseek-deployment); README "spark-hydra" row |
| H2 | 03 "Evidence", the teaser heading and paragraph | "GLM-5.3-Flash: 26/71 baseline, 51/71 scored." / "The November 16 serving configuration passed 26/71. With thinking and a revised harness, 51/71 scored reached 76.7% of the gold standard's SCORE. Claude Opus 5.5 passed 71/71 in its own scaffold. Single seed." | Heading: "DeepSeek-V4.1-Flash (Mia EXL3): 65/71, #1 on the v5 board." Paragraph: "The Spark pair's default model passed 65/71 with its recommended agent setup, a SCORE of 94.9, or 94.9% of the gold standard's. Claude Opus 5.5 passed 71/71 in its own scaffold. GLM-5.3-Flash, the alternative, reached 51/71. Single seed. v5 measures agentic coding with thinking on, not general chat." | Board JSON `best[0]`, `best[1]`, `best[2]` ([§4.1](#41-the-v5-board)) |
| H3 | 03 "Evidence", the ladder chips | "L0 · 26/71 SCORE 48.2%" / "A · 31/71 SCORE 56.9%" / "B · 36/71 SCORE 60.4%" / "C · 51/71 SCORE 76.7%" / "E · 50/71 SCORE 74.7%" / "Gold · 71/71 SCORE 100%" | The teaser shows one bar per model at its best, not GLM's ladder: "DeepSeek-V4.1-Flash (Mia EXL3) · T2 · 65/71 · 94.9%", "GLM-5.3-Flash · C · 51/71 · 76.7%", "Gold · 71/71 · 100%". GLM's ladder moves to `/benchmarks/` and `/research/harness/` only, where it already is | Board JSON `best` |
| H4 | 03 "Evidence", the closing line | "The RTX 5090 default awaits a v5 run." | Keep. Still true (`not_yet_run` in the board JSON) | Board JSON `not_yet_run` |
| H5 | 04 "Scale out", the fleet row (`page.tsx:39`) | `["LIVE", "2× DGX Spark", "TensorFold · TP=2", "GLM-5.3-Flash · four streams sharing one KV pool"]` | `["LIVE", "2× DGX Spark", "TensorFold · TP=2", "DeepSeek-V4.1-Flash (Mia EXL3) · four streams"]` | SPARK_HYDRA.md top |
| H6 | 02 "What ships", card ENGINES / 08 | "Since v1.9 two DGX Sparks serve one model tensor-parallel with TensorFold: spark-hydra, live with its own command center." | Keep. Add a new card after "beast-apps" ([§5 N1](#n1-home-card-two-models-one-command)) | — |
| H7 | Hero eyebrow | "v1.9.0 SHIPPED · SPARK-HYDRA" | Keep (v1.9.0 is still the latest tag) | `git tag` |

### 3.2 `/benchmarks/` (`src/app/benchmarks/page.tsx`, `src/components/ArmLadder.tsx`)

| # | Section | Live (exact) | Replacement | Source |
|---|---|---|---|---|
| B1 | Hero lede | "The DGX Spark pair scored 26/71 with its November 16 serving configuration and 51/71 with thinking and a revised harness. That best configuration achieved 76.7% of the gold standard's SCORE. Claude Opus 5.5 passed 71/71 in its own scaffold. Single seed." | "On the DGX Spark pair, DeepSeek-V4.1-Flash (Mia EXL3) passed 65/71 with its recommended agent setup, 94.9% of the gold standard's SCORE. It is #1 on the v5 board. GLM-5.3-Flash, the pair's alternative, went from 26/71 to 51/71 as its harness improved. Claude Opus 5.5 passed 71/71 in its own scaffold. Single seed. v5 measures agentic coding with thinking on, not general chat." | Board JSON |
| B2 | Hero stat tiles | "GLM-5.3-Flash, GLM-C" / "51/71 scored · single seed · integrity footnote" / "Of the gold standard" / "76.7% SCORE 76.7 of 100.0 · single seed" | "DeepSeek-V4.1-Flash (Mia EXL3), DS-T2" / "65/71 · single seed · integrity scan clean" / "Of the gold standard" / "94.9% · SCORE 94.9 of 100.0 · single seed". The Opus tile ("71/71 its own scaffold · 68/71 at the 1× wall · single seed") and the coverage tile stay | Board JSON `best[0]`; integrity: `DS-T2` note "0 contaminated after review" (state only the count) |
| B3 | Ladder section heading and intro | "The same weights across five harness configurations" / "GLM-5.3-Flash Ablit ran on the Spark pair with TensorFold, EXL3 4-bit weights and speculative decoding. Each arm covers the entire suite. … Arms D and F were never run." | Keep it as **GLM's** ladder, retitled "GLM-5.3-Flash: the same weights across five harness configurations". Add a second, shorter ladder above it: "DeepSeek-V4.1-Flash (Mia EXL3): two thinking-first arms". Intro: "DeepSeek-V4.1-Flash (Mia EXL3) ran on the Spark pair with TensorFold and stock EXL3 weights at 2.9 bits per weight. Arm T combined the changes that helped GLM, with thinking on. Arm T2 added a per-turn thinking cap and a nudge to act. Each arm covers the entire suite. Single seed each." Rungs: "T · 61/71 · 88.5%", "T2 · 65/71 · 94.9%", gold at 100% | Board JSON runs `DS-T`, `DS-T2`; [SPARK_HYDRA.md E2b](SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2) |
| B4 | The v5 board table | Rank 1 "GLM-5.3-Flash … 51/71* … 76.7", then the Opus reference row, then two "No v5 result yet" rows | The table in [§4.1](#41-the-v5-board), rank 1 DeepSeek, rank 2 GLM, then the reference, then the two not-yet-run rows | Board JSON `best`, `not_yet_run` |
| B5 | "Passes by language" | "GLM-C · scored · single seed" with Python 24/32 75%, Go 6/8 75%, C 6/8 75%, C++ 6/8 75%, Rust 5/8 63%, Zig 3/6 50%, Shell 1/1 100% | Lead with DS-T2 and keep GLM-C as a second column: the table in [§4.1](#per-language-share-of-the-gold-standard) | Board JSON `by_lang` |
| B6 | "Passes by language", the closing note | "The reference passed every unit in every language. Of arm C's 17 raw failures, 13 were process failures, three were algorithmic and one was a Zig knowledge failure." | Keep, prefixed with "For GLM-5.3-Flash:" | `HARNESS_NOTES.md` (unchanged) |
| B7 | "What runs next" | "DeepSeek v4.1 Flash is next on the Spark pair." / "A different seed will help estimate how much of the observed difference is run-to-run variation." / "The RTX 5090 and its Qwen3.8 default still have no v5 result." | "DeepSeek-V4.1-Flash (Mia EXL3) ran two arms and leads the board. Its seed arm is running now: it repeats arm T with a different seed, to measure run-to-run variation. A shorter thinking cap (arm T3) runs after it." / "The RTX 5090 and its Qwen3.8 default still have no v5 result." / keep the paper line | [MODEL_CAMPAIGN.md](MODEL_CAMPAIGN.md) "arm T_s1 is running" (2026-11-19) |
| B8 | Ladder footnotes (`ArmLadder.tsx:61-62`) | "* 54 raw; 3 units (122_gemm_blocked_b, 122_gemm_blocked_d, 122_gemm_blocked_e) excluded contaminated: the agent read sealed paths (G25 integrity scan, evals/CHANGELOG.md)." / "** 122_gemm_blocked_c is a validator false FAIL: the pre-#195 forbidden-API lint matched 'dlopen' inside a comment, and the solution passes the fixed validator (replayed, #195); not adjusted." | "* 54 raw. Three units of one task family were withheld after an integrity scan found the agent had read sealed harness files." / "** One unit was failed by a validator bug that was later fixed. The score is not adjusted." Keep the "Integrity record" and "Validator record" links. Write both as fixed strings, not from the board's notes ([§4.3](#43-code-changes-in-the-website-repo)) | Rule E; [WEBSITE_PLAN.md §13 Q8](WEBSITE_PLAN.md#13-questions-for-max) |
| B9 | "Measurement limits" | "The web is disabled for measured agents. The integrity scan found a gap in file isolation and three arm C passes were withheld. A distinct-seed run and held-out checks are still needed." | "The web is disabled for measured agents. After an integrity scan withheld three of GLM arm C's passes, the eval sandbox was hardened: sealed files are denied at the kernel boundary on macOS, outbound network is denied except loopback, and every published run is scanned for contamination. Both DeepSeek arms ran under the hardened sandbox, and their scans found no contaminated passes. A distinct-seed run and held-out checks are still needed." | [`evals/CHANGELOG.md`](../evals/CHANGELOG.md) "review 1118: sandbox hardening (#224)"; board JSON notes for `DS-T`, `DS-T2` (counts only) |

### 3.3 `/hardware/` (`src/app/hardware/page.tsx`)

| # | Section | Live (exact) | Replacement | Source |
|---|---|---|---|---|
| W1 | "spark-hydra · two NVIDIA DGX Sparks", first paragraph (`page.tsx:23`) | "Each GB10 has 128 GB of unified memory. TensorFold serves GLM-5.3-Flash Ablit in EXL3 4-bit with tensor parallelism across two ConnectX-7 rails, speculative decoding and FP8 KV. The lead unit runs the command center and starts the peer rank under the same service lifecycle." | "Each GB10 has 128 GB of unified memory. By default TensorFold serves DeepSeek-V4.1-Flash (Mia EXL3): stock weights in EXL3 at 2.9 bits per weight, with one tensor-parallel rank on each Spark across two ConnectX-7 rails. GLM-5.3-Flash, abliterated, in EXL3 4-bit with speculative decoding and FP8 KV, is staged as the alternative. One model serves at a time, and one command switches between them. The lead unit runs the command center and starts the peer rank under the same service lifecycle." | [SPARK_HYDRA.md DeepSeek deployment](SPARK_HYDRA.md#deepseek-deployment), [D1](SPARK_HYDRA.md#d1-the-glm-recipe-the-alternative-deployment); board host string "Mia EXL3 (2.9 bpw)" |
| W2 | Tile "1,048,576-token window" (`page.tsx:27`) | Title "1,048,576-token window"; body "One conversation can use up to this context window. Four streams draw from one shared 1,986,560-token KV pool." | Title "524,288-token window"; body "The default serves up to 524,288 tokens per conversation, with four streams at once. The model itself supports 1,048,576. With GLM selected, a conversation can use 1,048,576 tokens, and four streams share one 1,986,560-token KV pool." | [MODELS.md](MODELS.md), the "DGX Spark pair (spark-hydra)" paragraph; SPARK_HYDRA.md E2b "Unchanged" (`CONTEXT=524288`, `PARALLEL=4`) |
| W3 | Tile "Thinking is on by default" | "Since November 17 the gate, Open WebUI and OpenBeast agents default to thinking. A caller can override it." | Keep. Change the link target from E2a to [E2b](SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2), which describes the default model's setup | SPARK_HYDRA.md E2b (`THINKING=1`) |
| W4 | Trust line below the tiles (`page.tsx:31`) | "The operator controls the choice of abliterated weights. Device keys, human roles, scoped files, tool checks and audit remain in effect when configured." | "The default, DeepSeek-V4.1-Flash (Mia EXL3), runs stock weights. The GLM-5.3-Flash alternative is abliterated. OpenBeast's model policy is uncensored-preferred: stock models are allowed, and an uncensored variant of comparable quality is preferred. Device keys, human roles, scoped files, tool checks and audit apply to both." | Rule B; SPARK_HYDRA.md top |
| W5 | "Serving throughput" chart and table caption | "Spark pair · November 13, 2026 · short API prompts · thinking off · MTU 1500" (Prose 43–44 / 145 tok/s, Code 68–70 / 224 tok/s) | Retitle that table "GLM-5.3-Flash (the alternative) · November 13, 2026 · short API prompts · thinking off · MTU 1500" and keep its numbers. Add the DeepSeek table from [§4.2](#42-serving-and-hardware-facts) above it, as the default's | SPARK_HYDRA.md [Serving configuration](SPARK_HYDRA.md#serving-configuration-measured-2026-11-13) (GLM); [DeepSeek packs](SPARK_HYDRA.md#deepseek-packs-spark-deepseek-pack) run record (DeepSeek) |
| W6 | Paragraph after the table | "In a separate single-stream test from a Mac on November 17, the first streamed token still arrived in about 0.2 s. The first answer token moved from about 0.2 s to a 5.0 s median, with a 1.9–38.7 s range. Decode stayed around 75 tok/s on that sample. In the full single-seed v5 arms, the mean per unit was 29.0 min for C and 11.7 min for B." | Prefix "For GLM-5.3-Flash:", and keep the rest | `WEBSITE_PLAN.md` S48 (GLM's thinking test) |
| W7 | "Measured capability on the Spark pair" | "GLM-5.3-Flash passed 26/71 in its November 16 configuration and 51/71 scored with thinking and a revised harness. Single seed." | "DeepSeek-V4.1-Flash (Mia EXL3) passed 65/71 (SCORE 94.9, #1 on the v5 board) with its recommended agent setup, and 61/71 with the arm before it. GLM-5.3-Flash passed 26/71 in its November 16 configuration and 51/71 scored with thinking and a revised harness. Single seed. v5 measures agentic coding with thinking on." | Board JSON |

### 3.4 `/security/` (`src/app/security/page.tsx`)

| # | Section | Live (exact) | Replacement | Source |
|---|---|---|---|---|
| X1 | Below the role and file scope diagram (`page.tsx:17`) | "Uncensored (abliterated) weights reduce refusals at the model layer. The operator is responsible for their use." | "OpenBeast's model policy is uncensored-preferred. Stock models are allowed. Where an uncensored variant of comparable quality exists, OpenBeast prefers it, to enable researchers, lawyers, and professionals to get the most honest and neutral answers when weighing critical business decisions. Uncensored (abliterated) weights reduce refusals at the model layer. The RTX 5090 rig's default and the Spark pair's GLM alternative are abliterated. The Spark pair's default, DeepSeek-V4.1-Flash (Mia EXL3), is stock. The operator is responsible for how the models are used." (The sentence that follows, "Device authentication, human roles, … act at separate layers.", stays.) | Rule B |

### 3.5 `/investors/investment-case/` (`src/app/investors/investment-case/page.tsx`)

| # | Section | Live (exact) | Replacement | Source |
|---|---|---|---|---|
| I1 | "Risks and the evidence that would reduce them", risk row (`page.tsx:43`) | Title "Uncensored-only fleet policy"; body "Model refusal behavior and platform permissions are separate controls. The guest WebUI tool tier has search, fetch and weather. Regulated deployments require policy, defaults and external evidence appropriate to the workload." | Title "Uncensored-preferred model policy"; body "OpenBeast prefers uncensored variants where one of comparable quality exists, and allows stock models: the Spark pair's default is stock. Model refusal behavior and platform permissions are separate controls. The guest WebUI tool tier has search, fetch and weather. Regulated deployments require policy, defaults and external evidence appropriate to the workload." | Rule B; [WEBSITE_PLAN.md §13 Q11](WEBSITE_PLAN.md#13-questions-for-max), answered by the policy decision |
| I2 | Evidence row (v5) | "On v5, GLM-5.3-Flash passed 26/71 in the November 16 baseline and 51/71 scored with a revised harness and thinking. That is 76.7% of the gold standard's SCORE. Claude Opus 5.5 used its own scaffold. Single seed." | "On v5, DeepSeek-V4.1-Flash (Mia EXL3), the Spark pair's default, passed 65/71, 94.9% of the gold standard's SCORE. GLM-5.3-Flash passed 26/71 in its November 16 baseline and 51/71 scored with a revised harness and thinking. Claude Opus 5.5 used its own scaffold. Single seed. v5 measures agentic coding." | Board JSON |
| I3 | Release record row (`page.tsx:33`) | "v5 runs for Qwen3.8 and DeepSeek remain open." | "v5 runs for Qwen3.8 remain open. DeepSeek-V4.1-Flash (Mia EXL3) has two single-seed rows, and its seed arm is running." | Board JSON `not_yet_run`; MODEL_CAMPAIGN.md |

### 3.6 `/investors/` (`src/app/investors/page.tsx`)

| # | Section | Live (exact) | Replacement | Source |
|---|---|---|---|---|
| V1 | Capability status block | "First rows measured on the DGX Spark pair for one model, single seed." | "Rows measured on the DGX Spark pair for two models, single seed. The default, DeepSeek-V4.1-Flash (Mia EXL3), is #1 at 65/71." | Board JSON |
| V2 | Commercial path | "A supported Spark offer requires a commercial licence review of the weights and serving recipe, and a replacement for the research-only speculative component." | "A supported Spark offer requires a commercial licence review of the weights and serving recipe for each model it ships (DeepSeek-V4.1-Flash (Mia EXL3) by default, GLM-5.3-Flash as the alternative), and a replacement for the research-only speculative component that the GLM recipe uses." | [§8 Q-A](#8-out-of-scope-and-open-questions-for-max) |

### 3.7 `/research/`, `/research/harness/`, `/research/campaign/`

| # | Page, section | Live (exact) | Replacement | Source |
|---|---|---|---|---|
| R1 | `/research/` (`research/page.tsx:192`), harness finding | "On the same Spark system and weights, GLM-5.3-Flash moved from 26/71 to 51/71 scored with a revised harness and thinking. Single seed. The cross-model paper is in progress." | "On the same Spark system and weights, GLM-5.3-Flash moved from 26/71 to 51/71 scored with a revised harness and thinking. A second model, DeepSeek-V4.1-Flash (Mia EXL3), reached 65/71 with a thinking-first version of that harness. Single seed. The cross-model paper is in progress." | Board JSON |
| R2 | `/research/harness/` (`harness/page.tsx:31`), "Per-model or unified?" | "One local model has been measured. Arms D and F did not run." | "Two local models have been measured: GLM-5.3-Flash (arms L0 to E) and DeepSeek-V4.1-Flash (Mia EXL3) (arms T and T2). GLM's arms D and F did not run." | Board JSON |
| R3 | `/research/harness/` (`harness/page.tsx:33`) | "DeepSeek's own harness supplies hypotheses for the next campaign." | "DeepSeek's own harness supplied hypotheses for its thinking-first campaign." | [HARNESS_NOTES.md](HARNESS_NOTES.md) |
| R4 | `/research/harness/` footnotes | the same two footnotes as B8 | the B8 replacements | Rule E |
| R5 | `/research/campaign/` (`campaign/page.tsx:27`) | "Next: DeepSeek v4.1 Flash. First verify its per-request thinking switch through the actual serving path." | "Ran: DeepSeek-V4.1-Flash (Mia EXL3). Arm T scored 61/71, and arm T2 (a per-turn thinking cap and a nudge to act) scored 65/71, #1 on the v5 board. Running now: the seed arm, then arm T3 (a shorter cap). Single seed so far." | Board JSON; MODEL_CAMPAIGN.md |

### 3.8 `/clients/` and `/offline/`

| # | Page, section | Live (exact) | Replacement | Source |
|---|---|---|---|---|
| C1 | `/clients/`, multi-rig paragraph | "The upstream multi-rig client reads model, context and capacity from each host's /api/slot. … Switching the active rig moves the local tooling …" | Keep, and append: "When a rig switches models, `openbeast-client autosync` (installed by default) updates the local opencode configuration within 30 seconds." Label UPSTREAM MAIN | [BEAST_SLOT.md](BEAST_SLOT.md) (`autosync install`: "every 30 s"); SPARK_HYDRA.md "Switching on this pair" |
| O1 | `/offline/`, recorded Spark run | "In one recorded Spark run, GLM-5.3-Flash used OpenBeast's own tools to make a correct two-file repair …" | Keep (a dated GLM record). Add the new offline blocks in [§5 N3, N4](#n3-offline-develop-with-no-network) | — |

### 3.9 `/llms.txt` (`public/llms.txt`)

| Line | Live (exact) | Replacement |
|---|---|---|
| 8 | "On the Spark pair, TensorFold serves GLM-5.3-Flash Ablit, EXL3 4-bit, with tensor parallelism and speculative decoding. The 1,048,576-token context window is per conversation. Four streams share one 1,986,560-token KV pool. Thinking defaults on since November 17." | "On the Spark pair, TensorFold serves DeepSeek-V4.1-Flash (Mia EXL3) by default since November 18: stock EXL3 weights at 2.9 bits per weight, tensor parallel across both Sparks, a 524,288-token context per conversation, four streams, thinking on. GLM-5.3-Flash (abliterated, 1,048,576-token context) is the staged alternative; one command switches between them." |
| 15 | "GLM-5.3-Flash on the Spark pair passed 26/71 in the November 16 baseline. Arm C, … passed 51/71 scored (54 raw; three units withheld after the integrity scan), SCORE 76.7. Arm E passed 50/71, SCORE 74.7, …" | "DeepSeek-V4.1-Flash (Mia EXL3) on the Spark pair passed 65/71, SCORE 94.9 (arm T2, its recommended agent setup), and 61/71, SCORE 88.5 (arm T). It is #1 on the v5 board. GLM-5.3-Flash passed 26/71 in the November 16 baseline and 51/71 scored with thinking and a revised harness (SCORE 76.7). Single seed. v5 measures agentic coding with thinking on." |
| 16 | "… GLM-C achieved 76.7% of its SCORE. …" | "Claude Opus 5.5 passed 71/71 in its own Claude Code scaffold and serves as the unranked gold standard. DeepSeek's best row reached 94.9% of its SCORE, GLM's 76.7%. The different scaffolds and single seed bound the comparison." |
| 17 | "DeepSeek v4.1 Flash is next on the Spark pair. The paper asking whether a unified harness generalizes across models is in progress." | "DeepSeek's seed-noise arm is running. The paper asking whether a unified harness generalizes across models is in progress." |
| 22 | "The default weights on the reference systems are abliterated. …" | "OpenBeast's model policy is uncensored-preferred: stock models are allowed, and uncensored variants of comparable quality are preferred. The RTX 5090 rig's default and the Spark pair's GLM alternative are abliterated; the Spark pair's default is stock. …" (the rest of the line stays) |
| Pinned project sources | links pinned to `58c437e0…` | Re-pin to the OpenBeast commit chosen in [§4.3](#43-code-changes-in-the-website-repo) step 1. Add a line `- [DeepSeek best configuration](https://github.com/MaximilianKhan/openbeast/blob/<revision>/docs/SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2)` |

### 3.10 `e2e/site.spec.ts`

These assertions pin copy that changes. Update them in the same PR:

| Line | Pins today | Change to |
|---|---|---|
| 77 | `["26/71", "51/71", "50/71", "76.7% of the gold standard", "Single seed"]` (home evidence) | `["65/71", "94.9", "51/71", "Single seed", "DeepSeek-V4.1-Flash (Mia EXL3)"]` |
| 190 | the GLM ladder counts | keep, and add `["61/71", "65/71"]` for the DeepSeek ladder |
| 254 | row `/Uncensored-only fleet policy/` | row `/Uncensored-preferred model policy/` |
| 398 | `llms.txt` facts incl. `"1,986,560-token KV pool"`, `"51/71 scored"`, `"DeepSeek v4.1 Flash"` | `"DeepSeek-V4.1-Flash (Mia EXL3)"`, `"524,288-token context"`, `"65/71"`, `"uncensored-preferred"`, `"Single seed"` |
| 465 | the five GLM run rows | keep, and add `["DS-T", "61/71", "88.5"], ["DS-T2", "65/71", "94.9"]` |
| 484 | `/DeepSeek v4.1 Flash is next/` | `/seed arm is running/` |
| 490 | `/hardware/` facts incl. `"1,986,560-token KV pool"`, `"224 tok/s"` | keep both (they stay, labelled GLM), and add `"524,288-token window"` |
| 496 | `/research/campaign/` `"DeepSeek v4.1 Flash"` | `"DeepSeek-V4.1-Flash (Mia EXL3)"` |

Add one negative test over every page and `llms.txt`: none of the stale
strings in [§7](#7-acceptance-checklist) step 3 appears.

---

## 4. Data updates

### 4.1 The v5 board

Generated from `evals/v5_board.py --json` (OpenBeast `main` at `55a6a8f7`).
SCORE = 0.75·SOLVE + 0.25·LANG. The gold-standard fraction is SCORE ÷ 100.0
(Opus). Mean / unit is the agent's wall time per unit.

**The board: each model at its best configuration** (the rows exactly as the
site shows them):

| Rank | Model | Run | Host | Passed | SCORE | SOLVE / LANG | Of gold standard | Py | Go | C | C++ | Rust | Zig | Sh | Mean / unit | Date | Status |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 1 | DeepSeek-V4.1-Flash (Mia EXL3) | DS-T2 | DGX Spark pair, TensorFold, Mia EXL3 (2.9 bpw) | 65/71 | 94.9 | 96.9 / 88.9 | 94.9% | 30/32 | 7/8 | 7/8 | 7/8 | 8/8 | 5/6 | 1/1 | 20.7 min | 2026-11-19 | experiment arm (beast-assist diagnostics, awareness packs, wall-scaled turns, wall ×2) |
| 2 | GLM-5.3-Flash | GLM-C | DGX Spark pair, TensorFold, EXL3 | 51/71\* | 76.7 | 75.4 / 80.5 | 76.7% | 24/32 | 6/8 | 6/8 | 6/8 | 5/8 | 3/6 | 1/1 | 29.0 min | 2026-11-16 | experiment arm (wall-scaled turns, wall ×2) |
| gold standard | Claude Opus 5.5 | OPUS-REF | Anthropic API | 71/71 | 100.0 | 100.0 / 100.0 | 100.0% | 32/32 | 8/8 | 8/8 | 8/8 | 8/8 | 6/6 | 1/1 | 3.7 min | 2026-11-16 | reference ceiling, not ranked: a different scaffold (68/71 at the 1× wall) |
| — | Qwen3.8 27B Uncensored MTP (the shipped default) | — | | — | — | — | — | | | | | | | | | | No v5 result yet |
| — | Qwen3.6 27B (v4 #1) | — | | — | — | — | — | | | | | | | | | | No v5 result yet |

\* 54 raw. Three units of one task family were withheld after an integrity
scan found the agent had read sealed harness files.

**Configuration column** (the board's label, verbatim; the site may shorten
it to the first clause plus "see the run table"):

- DS-T2: "thinking on, campaign arm T2: T + 8192-token per-turn thinking cap + act nudge; harness9 (review 1118 sandbox + partial stats + eval fetch guard)"
- GLM-C: "thinking on, runner v2, wall-scaled turns, wall ×2"
- OPUS-REF: "Claude Code tools, no turn cap, wall ×2 (68/71 at the 1× wall); solver never saw validator output"

**Every v5 configuration measured** (the run table and both ladders):

| Model | Run | Arm | Passed | SCORE | SOLVE | LANG | Of gold | Py | Go | C | C++ | Rust | Zig | Sh | Mean / unit | Date | Status |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| DeepSeek-V4.1-Flash (Mia EXL3) | DS-T | v5-thinking-first T | 61/71 | 88.5 | 84.6 | 100.0 | 88.5% | 27/32 | 7/8 | 7/8 | 7/8 | 7/8 | 5/6 | 1/1 | 22.5 min | 2026-11-18 | experiment arm |
| DeepSeek-V4.1-Flash (Mia EXL3) | DS-T2 | v5-thinking-first T2 | 65/71 | 94.9 | 96.9 | 88.9 | 94.9% | 30/32 | 7/8 | 7/8 | 7/8 | 8/8 | 5/6 | 1/1 | 20.7 min | 2026-11-19 | experiment arm |
| GLM-5.3-Flash | GLM-L0 | (the L0 profile) | 26/71 | 48.2 | 50.8 | 40.5 | 48.2% | 16/32 | 3/8 | 1/8 | 3/8 | 3/8 | 0/6 | 0/1 | 6.8 min | 2026-11-16 | board protocol (L0) |
| GLM-5.3-Flash | GLM-A | v5-harness A | 31/71 | 56.9 | 60.0 | 47.7 | 56.9% | 19/32 | 2/8 | 3/8 | 3/8 | 2/8 | 1/6 | 1/1 | 6.7 min | 2026-11-16 | board protocol (L0) |
| GLM-5.3-Flash | GLM-B | v5-harness B | 36/71 | 60.4 | 63.1 | 52.3 | 60.4% | 19/32 | 4/8 | 3/8 | 4/8 | 5/8 | 0/6 | 1/1 | 11.7 min | 2026-11-16 | experiment arm |
| GLM-5.3-Flash | GLM-C | v5-harness C | 51/71\* | 76.7 | 75.4 | 80.5 | 76.7% | 24/32 | 6/8 | 6/8 | 6/8 | 5/8 | 3/6 | 1/1 | 29.0 min | 2026-11-16 | experiment arm |
| GLM-5.3-Flash | GLM-E | v5-harness E | 50/71\*\* | 74.7 | 72.3 | 81.7 | 74.7% | 22/32 | 6/8 | 5/8 | 6/8 | 6/8 | 4/6 | 1/1 | 28.5 min | 2026-11-17 | experiment arm |
| Claude Opus 5.5 | OPUS-REF | reference | 71/71 | 100.0 | 100.0 | 100.0 | 100.0% | 32/32 | 8/8 | 8/8 | 8/8 | 8/8 | 6/6 | 1/1 | 3.7 min | 2026-11-16 | reference, not ranked |

\* As above. \*\* One unit was failed by a validator bug that was later
fixed. The score is not adjusted.

Every row ran **one seed**. The board also footnotes, on every row
including the reference, four units judged by an older validator version,
not adjusted. The site does not need that footnote: it applies to every row
alike and changes no ranking. If the site shows it, use the count only:
"Four units on every row were judged by an older validator version; not
adjusted."

#### Per-language share of the gold standard

Integer percentages, rounded half up (the live page's convention:
5/8 = 63%).

| Language | DS-T2 passed | Share | GLM-C passed | Share | Gold standard |
|---|---:|---:|---:|---:|---:|
| Python | 30/32 | 94% | 24/32 | 75% | 32/32 |
| Go | 7/8 | 88% | 6/8 | 75% | 8/8 |
| C | 7/8 | 88% | 6/8 | 75% | 8/8 |
| C++ | 7/8 | 88% | 6/8 | 75% | 8/8 |
| Rust | 8/8 | 100% | 5/8 | 63% | 8/8 |
| Zig | 5/6 | 83% | 3/6 | 50% | 6/6 |
| Shell | 1/1 | 100% | 1/1 | 100% | 1/1 |

**Do not hand-type these into the site.** They come from
`results-build.json` through `scripts/sync-results.mjs`
([§4.3](#43-code-changes-in-the-website-repo)). These tables are what the
build must produce. Check the rendered page against them.

### 4.2 Serving and hardware facts

**The default: DeepSeek-V4.1-Flash (Mia EXL3).** MEASURED or configured on
the pair; sources in the last column.

| Fact | Value | Source |
|---|---|---|
| Default since | 2026-11-18 (Max's decision after hands-on testing) | SPARK_HYDRA.md top |
| Weights | Mia EXL3, 2.9 bits per weight, **stock** (not abliterated, not uncensored; `ABLIT=0`) | SPARK_HYDRA.md [DeepSeek deployment](SPARK_HYDRA.md#deepseek-deployment), [DeepSeek packs](SPARK_HYDRA.md#deepseek-packs-spark-deepseek-pack) |
| Engine and parallelism | TensorFold, tensor parallel 2: one rank per Spark over two RoCE rails (ConnectX-7) | SPARK_HYDRA.md DeepSeek deployment |
| Context per conversation | 524,288 tokens (`CONTEXT=524288`); the model supports 1,048,576 | MODELS.md, SPARK_HYDRA.md E2b "Unchanged" |
| Streams | 4 (`PARALLEL=4`) | same |
| Thinking | on by default (`THINKING=1`); a caller can turn it off per request | same; SPARK_HYDRA.md "Local interactive baseline" |
| Recommended agent setup | `AGENT_PROFILE=deepseek-best` (v5 arm T2) | [SPARK_HYDRA.md E2b](SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2) |

Do **not** carry GLM's serving details over to DeepSeek: the speculative
decoder, FP8 KV and the 1,986,560-token shared pool are recorded for GLM
only. No DeepSeek source records any of them.

**DeepSeek throughput, MEASURED 2026-11-17 on spark-1** (the served pack,
thinking off, short prompts, through the recipe's bench; each cell is two
runs). Source: the run record in
[SPARK_HYDRA.md DeepSeek packs](SPARK_HYDRA.md#deepseek-packs-spark-deepseek-pack).

| Workload | Value |
|---|---|
| Decode, 1 stream | 60.6 / 62.3 tok/s |
| Decode, 2 streams, aggregate | 90.2 / 90.8 tok/s |
| Decode, 4 streams, aggregate | 118.4 / 120.5 tok/s |
| Prefill, 8K prompt | 2,384 / 2,376 tok/s |
| Prefill, 32K prompt | 2,434 / 2,436 tok/s |
| Errors | 0 |

Caption, ready to paste: "DeepSeek-V4.1-Flash (Mia EXL3) on the Spark
pair · November 17, 2026 · bench on the lead unit · thinking off · two runs
per cell."

**From a client, MEASURED 2026-11-17** (from a Mac through the tailnet gate,
six short temperature-zero prompts with 256 completion tokens each).
Measured on Mia's earlier EXL3 pack. The served pack matches it within
run-to-run noise (same run record). Source:
[SPARK_HYDRA.md DeepSeek deployment](SPARK_HYDRA.md#deepseek-deployment),
"Local interactive baseline".

- Thinking on: single-stream decode 70.1–71.7 tok/s; median first streamed
  token 232 ms.
- Thinking off: single-stream decode 53.7–63.2 tok/s (median 60.7); median
  first streamed token 156 ms.
- Four simultaneous thinking-off requests: 117.1 tok/s aggregate, end to end.
- Copy limit: "This short-prompt test does not establish long-context
  performance."

**The alternative: GLM-5.3-Flash.** Abliterated, EXL3 4-bit, speculative
decoding, FP8 KV, a 1,048,576-token window per conversation, four streams
sharing a 1,986,560-token KV pool. The 2026-11-13 throughput table on
`/hardware/` is GLM's and stays, labelled as GLM's. Sources: SPARK_HYDRA.md
[D1](SPARK_HYDRA.md#d1-the-glm-recipe-the-alternative-deployment) and
[Serving configuration](SPARK_HYDRA.md#serving-configuration-measured-2026-11-13).

**Unchanged:** two GB10s with 128 GB unified memory each; the fabric at MTU
9000 since 2026-11-17 (its speed effect is still unmeasured); the command
center and beast-gate run on the lead unit only; the RTX 5090 rig's facts
(Qwen3.8 27B Uncensored MTP Q5_K_M, 262K native context, 140.2 tok/s with
its MTP profile).

### 4.3 Code changes in the website repo

The board data path is already automated. It needs these changes:

1. **Resync.** In the website repo, run
   `node scripts/sync-results.mjs <openbeast checkout> <commit>`. For
   `<commit>`, use the merge commit of the OpenBeast PR that adds this page,
   or anything later on `main`. That revision has both DeepSeek rows and
   SPARK_HYDRA.md E2b. The same revision becomes `results.revision`, which
   pins every repo link on the site. Commit the regenerated
   `src/app/benchmarks/results-build.json`.
2. **`results.ts`: the best row.** Today `daily = run("GLM-L0")`, and `best`
   is GLM's ranked row. Change `best` to the board's rank-1 row
   (`board.find(r => r.rank === "1")`), which is `DS-T2`. Keep a separate
   `glmBest` (rank 2) and `glmDaily` (`GLM-L0`) for the GLM ladder.
3. **`results.ts`: a second ladder.** Add
   `deepseekLadder = [["T", "Thinking-first composite"], ["T2", "T plus a per-turn thinking cap and a nudge to act"]]`,
   mapped to `run("DS-T")` and `run("DS-T2")`. Rename the existing `ladder`
   to `glmLadder`.
4. **Footnotes become fixed strings.** `withheldFootnote` and
   `validatorFootnote` are read today from `notes[0]` of the GLM-C and GLM-E
   runs. After the resync, every run has notes (the four lint notes, sorted
   first), so `notes[0]` changes meaning and leaks checker wording. Replace
   both with the fixed strings in [B8](#32-benchmarks-srcappbenchmarkspagetsx-srccomponentsarmladdertsx).
   In `ArmLadder.tsx`, show a footnote mark only on GLM-C (`*`) and GLM-E
   (`**`), keyed by run id, not by `notes.length > 0`. Otherwise every rung
   gets a mark.
5. **Do not render the raw status column.** `benchmarks/page.tsx:45` renders
   `row.status`. The resynced status for `DS-T` and `DS-T2` carries integrity
   and wall-time notes, and `DS-T2`'s note names what an agent read while
   solving a unit (Rule E). Render only the status label before the first
   `;` (e.g. "experiment arm (beast-assist diagnostics, awareness packs,
   wall-scaled turns, wall ×2)"), and give the integrity result as a count:
   "integrity scan: 0 contaminated".
6. **The hardware serving data** (`serving` in `hardware/page.tsx`) gets a
   second series for DeepSeek from [§4.2](#42-serving-and-hardware-facts),
   with its own caption. The chart's bar scale (`/ 2.4`) assumes GLM's
   224 tok/s maximum; DeepSeek's 4-stream aggregate is about 120 tok/s, so
   the existing scale still fits.
7. **`AGENTS.md`:** add rules A–E from [section 2](#2-rules-for-this-update).

---

## 5. New sections to add

Each block gives its page, its label and its copy. Every link goes to the
pinned OpenBeast revision from [§4.3](#43-code-changes-in-the-website-repo)
step 1.

### N1. Home card: two models, one command

Page `/`, section 02 "What ships", a new card after "beast-apps". Label
**UPSTREAM MAIN · after v1.9**.

> **SPARK · UPSTREAM MAIN · after v1.9 · beast-spark**
> **Two models, one command.**
> The Spark pair keeps two models ready and serves one at a time:
> DeepSeek-V4.1-Flash (Mia EXL3) by default, GLM-5.3-Flash as the
> alternative. One command switches the pair, checks the new model before it
> takes traffic, and falls back if the check fails. Clients follow on their
> own.

Sources: [SPARK_HYDRA.md Quickstart](SPARK_HYDRA.md#quickstart) (the
`beast-spark` table: `beastds` runs a canary and conformance, and restores
GLM if anything fails), "Switching on this pair" in
[DeepSeek deployment](SPARK_HYDRA.md#deepseek-deployment).

### N2. `/hardware/`: the recommended setup for the default

Page `/hardware/`, under the Spark section. Label: score **MEASURED**,
preset **UPSTREAM MAIN · after v1.9**.

> **One line for the best measured setup.** DeepSeek-V4.1-Flash (Mia EXL3)
> scored 65/71 on v5 with a per-turn thinking cap, a nudge to act, and
> compiler diagnostics after each edit. One line in `openbeast.conf`,
> `AGENT_PROFILE=deepseek-best`, applies that setup to the stack's own
> agents. The serving recipe does not change. Single seed.

Source: [SPARK_HYDRA.md E2b](SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2).
Don't say the live pair runs it yet: E2b says it "goes live with the 'Apply
on spark-1' steps … after the current eval arm". Check E2b's status line
before deploying. If it still says "not yet in spark-1's `openbeast.conf`",
keep the copy above, which describes the preset, not the live pair.

### N3. `/offline/`: develop with no network

Page `/offline/`, a new block. Label **MEASURED** (opencode, 2026-11-18) and
**UPSTREAM MAIN** (the offline git hub).

> **Develop on the Sparks with the internet unplugged.** opencode runs on
> both Sparks and talks to the pair's own model over loopback: no network,
> no model-provider key. Its installer and checksum are kept on the Sparks
> for an offline reinstall. When GitHub is unreachable, the Sparks and the
> Mac exchange branches through a git hub on the lead unit, or by drive with
> git bundles, and sync back once the connection returns.

Sources: [SELF_DEVELOPMENT.md "opencode on the Sparks"](SELF_DEVELOPMENT.md#opencode-on-the-sparks),
[SELF_DEVELOPMENT.md §11](SELF_DEVELOPMENT.md#11-developing-with-github-unreachable),
[COMMANDS.md §10](COMMANDS.md#10-git-offline). Don't print the opencode
version, host names or paths.

### N4. `/offline/`: backup and restore, fully offline

Page `/offline/`, a new block. Label **PLANNED** (designed and tested
against fakes; not run on hardware).

> **One backup, any number of Sparks (planned).** The design: a golden image
> of DGX OS that skips NVIDIA's first boot, each unit's identity restored
> from its own small bundle, and the OpenBeast overlay (code, secrets,
> services, weights, images) on one encrypted 4 TB SSD. Anything that can
> be derived, such as rank files and compiled kernels, is rebuilt offline
> instead of copied. Estimated recovery from bare metal: about two to two
> and a half hours for the pair. The tools are written and tested against
> fakes; the first hardware drills are next.

Source: [SPARK_BACKUP_RESTORE.md §0](SPARK_BACKUP_RESTORE.md#0-the-verdict).
The recovery time is an **estimate** ("Recovery time for the pair (estimate,
SSD)"), and every number in this block stays PLANNED until drill G has run
([§5.1](SPARK_BACKUP_RESTORE.md#51-the-drills)).

### N5. `/hardware/`: bring another EXL3 model

Page `/hardware/`, after "Choose the engine for the host". Label
**MEASURED** for DeepSeek packs, **PLANNED** for the rest.

> **Bring a model on a drive.** A read-only preflight checks a model folder
> before it touches the pair: architecture, fit per rank, disk. Another
> EXL3 quantization of DeepSeek-V4.1-Flash drops in with one command that
> verifies the swap, benchmarks it and rolls back on failure. This is
> measured on the pair. GLM packs and new architectures have a documented
> manual path today.

Sources: [EXL3_PLAYBOOK.md](EXL3_PLAYBOOK.md#the-answer-in-short) (cases
a1, a2, b, c), [SPARK_HYDRA.md DeepSeek packs](SPARK_HYDRA.md#deepseek-packs-spark-deepseek-pack)
(the round trip passed twice, 2026-11-17).

### N6. `/benchmarks/`: how the numbers are protected

Page `/benchmarks/`, next to "Measurement limits". Label **SHIPPED
(eval harness, upstream main)**.

> **How the numbers are protected.** Measured agents run in a sealed
> sandbox. They cannot read the harness's sealed files, reach the network
> beyond loopback, or write into the evaluation's own code. Every published
> run gets an integrity scan of what the agent did. A pass that touched
> sealed material is withheld and footnoted, never quietly kept. The run
> also checks that the server still serves the model under test, so a model
> switch stops a run instead of mixing two models in one row.

Source: [`evals/CHANGELOG.md`](../evals/CHANGELOG.md), "review 1118: sandbox
hardening (#224, era roll)" and "G24-G27 harness integrity fixes". Say
**what** is protected. Never say **how** a validator decides (Rule E).

### N7. `/security/`: model policy

Page `/security/`. Use the X1 copy ([§3.4](#34-security-srcappsecuritypagetsx))
as its own short section titled "Model policy: uncensored-preferred",
directly above the trust-model paragraph. Label: none needed (a decided
policy).

### N8. `/clients/`: autosync

Covered by C1 ([§3.8](#38-clients-and-offline)).

---

## 6. SEO and metadata

| Where | Live (exact) | Replacement |
|---|---|---|
| `/benchmarks/` `<meta name="description">` and `og:description` (`benchmarks/page.tsx:9`) | "Reproducible OpenBeast v5 results across 71 proven units, with each local configuration measured against a Claude Opus 5.5 reference." | "OpenBeast v5 agentic-coding results on 71 proven units. DeepSeek-V4.1-Flash (Mia EXL3) on two DGX Sparks: 65/71, 94.9% of the Claude Opus 5.5 gold standard." (156 characters) |
| `/hardware/` description and `og:description` | "Measured serving on two NVIDIA DGX Sparks with TensorFold and on an RTX 5090 with llama.cpp. See throughput, context and setup guides." | "DeepSeek-V4.1-Flash (Mia EXL3) on two NVIDIA DGX Sparks with TensorFold, and Qwen3.8 on an RTX 5090 with llama.cpp. Measured throughput, context and setup." (155 characters) |
| `/` description (`layout.tsx:6`) | "Turn hardware you own into a private AI workstation, then send its intelligence into any device, tool, or workflow without cloud inference." | Keep (no model claim) |
| `/research/campaign/` description | (check it in the source) | If it names "DeepSeek v4.1 Flash" as next, change it to "DeepSeek-V4.1-Flash (Mia EXL3)" as run |
| JSON-LD `article.headline` on `/hardware/` | "OpenBeast reference hardware and measured serving" | Keep |
| `/llms.txt` | lines 8, 15, 16, 17, 22 | [§3.9](#39-llmstxt-publicllmstxt) |
| Social cards (`scripts/generate-social-cards.mjs`, `public/openbeast-social*.png`) | the generator has no model names or scores (checked) | Open each PNG and confirm it shows no GLM number. Regenerate only if one does |
| `sitemap.ts` | 15 URLs | Unchanged: this update adds sections, not pages |

Search terms the new copy now carries naturally (don't stuff them): "DGX
Spark", "DeepSeek-V4.1-Flash", "local AI coding benchmark", "uncensored
local model", "offline AI workstation".

---

## 7. Acceptance checklist

Run after `deploy.sh prod` on the Mini. Cloudflare does not cache HTML
(`cf-cache-status: DYNAMIC`), so the check sees the new build at once.

1. **Every page answers 200 with its CSP.**

   ```sh
   for p in / /benchmarks/ /hardware/ /offline/ /clients/ /security/ /releases/ \
            /research/ /research/harness/ /research/campaign/ /about/ /investors/ \
            /investors/investment-case/ /llms.txt; do
     printf '%s ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' "https://openbeast.ai$p"
   done
   curl -sI https://openbeast.ai/ | grep -ci content-security-policy   # 1
   ```

   On 2026-11-19 one early request for `/` answered a Cloudflare 502. A
   retry answered 200. Retry once before you call a failure.

2. **Fetch everything into one directory** (later steps grep it):

   ```sh
   d=$(mktemp -d); for p in "" benchmarks hardware offline clients security releases research \
     research/harness research/campaign about investors investors/investment-case; do
     curl -sL "https://openbeast.ai/$p/" -o "$d/$(echo "${p:-home}" | tr / _).html"; done
   curl -sL https://openbeast.ai/llms.txt -o "$d/llms.txt"
   ```

3. **No stale string survives** (each command prints nothing):

   ```sh
   grep -l -E 'GLM-5\.3-Flash Ablit in EXL3|TensorFold serves GLM|GLM serves|GLM-5\.3-Flash · four streams' "$d"/*
   grep -l -iE 'DeepSeek v4\.1 Flash is next|Next: DeepSeek|DeepSeek remain open|One local model has been measured' "$d"/*
   grep -l -iE 'uncensored-only|Uncensored-only fleet|default weights on the reference systems are abliterated|choice of abliterated weights' "$d"/*
   grep -l -E 'DeepSeek-V4\.1-Flash-v2|bpw-v2|EAQ-v1\.1' "$d"/*
   grep -l -E 'dlopen|forbidden-API|comments/docstrings|taskgroups|[0-9]{2,3}_[a-z]+_[a-z_]+' "$d"/*
   grep -l -E 'tail4109f9|100\.66\.|100\.110\.|10\.100\.21[67]\.' "$d"/*
   ```

   The fifth line is Rule E: no checker wording, no solution hint, no unit
   ids. The sixth keeps private addresses off the site.

4. **Old GLM numbers appear only where they are labelled GLM's.** `51/71`
   and `50/71` are true GLM rows. They may stay in GLM's ladder and run
   table, but must not appear in a headline:

   ```sh
   for f in home hardware llms.txt; do printf '%s ' "$f"; grep -o -E '50/71' "$d/$f"* | wc -l; done   # 0 each
   grep -o 'GLM-5.3-Flash: 26/71 baseline, 51/71 scored' "$d"/home.html | wc -l                         # 0
   grep -c '1,048,576-token window' "$d"/hardware.html     # 0 as a tile title; the number may appear in the GLM sentence
   ```

5. **The new facts are present:**

   ```sh
   for s in 'DeepSeek-V4.1-Flash (Mia EXL3)' '65/71' '94.9' 'single seed|Single seed'; do
     for f in home benchmarks hardware; do printf '%-36s %-11s ' "$s" "$f"; grep -c -E "$s" "$d/$f.html"; done; done   # >= 1 each
   grep -c '524,288' "$d"/hardware.html "$d"/home.html "$d"/llms.txt     # >= 1 each
   grep -ci 'uncensored-preferred' "$d"/security.html "$d"/investors_investment-case.html "$d"/llms.txt   # >= 1 each
   grep -c '61/71' "$d"/benchmarks.html                                   # >= 1 (the DS-T rung)
   grep -ci 'agentic coding' "$d"/benchmarks.html "$d"/home.html          # >= 1 each
   ```

6. **The board matches the generator.** In the website repo:
   `node scripts/sync-results.mjs --check <openbeast checkout> <revision>`
   passes. Then open `/benchmarks/` and compare the board and both ladders
   with [§4.1](#41-the-v5-board), row by row: rank, pass count, SCORE,
   gold-standard fraction and the seven language cells.
7. **The repo links resolve.** Every `github.com/MaximilianKhan/openbeast/blob/<revision>/…#anchor`
   on the site points at the revision from §4.3 step 1. Check that the
   anchors exist there, especially `SPARK_HYDRA.md#e2b-deepseek-best-configuration-v5-t2`
   and `RESULTS.md#v5-leaderboard--best-configuration-per-model`.
8. **The website's own gates pass.** `npm run lint`, `npm run build`, and
   `npx playwright test` on Chromium, Firefox and WebKit, with the
   §3.10 updates and the new negative test.
9. **Record the deploy.** Note the website commit and the date in
   [WEBSITE_PLAN.md](WEBSITE_PLAN.md), at the top and in "How it deploys".
   Tick the TODO item "The website names the pair's new default" in
   [TODO.md](TODO.md), and close S53–S57 in the plan's stale-claim table.

---

## 8. Out of scope, and open questions for Max

**Out of scope for this update:**

- The rest of WEBSITE_PLAN.md's backlog (S30–S52 and the P1/P2 pages). This
  update fixes what the model switch made wrong and adds the capabilities
  above. Nothing else.
- Any new v5 number. When `T_s1` or `T3` lands, run `v5_board.py --add`
  upstream, then resync the site (§4.3 step 1). If T3 beats T2, the board's
  rank-1 row changes and the site follows it automatically. The copy in
  §3 that names "T2" and "65/71" then needs one edit pass.
- The harness10 arms (OpenBeast PR #234, open). Don't mention them until
  they have rows.
- The hydra family-policy code change (a separate PR). The site states the
  policy as decided. It does not describe hydra's enforcement.
- Photos and video (WEBSITE_PLAN.md's shot list).

**Open questions for Max:**

- **Q-A. The licence of Mia's DeepSeek EXL3 pack** (WEBSITE_PLAN.md
  [Q4](WEBSITE_PLAN.md#13-questions-for-max), B6). The pair's default now
  ships these weights. May the investor pages describe a supported Spark
  offer built on them, and under which licence terms? Until you answer, V2
  above says only that a licence review is required for each model.
- **Q-B. Name Mia's lab on the site?** The recipe and the pack are Mia's
  (MiaAI-Lab). Credit the lab by name on `/hardware/` (fair attribution,
  and it explains "Mia EXL3"), or keep "Mia EXL3" as a bare label?
- **Q-C. The per-model ladder framing.** This plan shows DeepSeek's two arms
  as a second ladder beside GLM's five. The alternative is one combined
  "campaign" chart across both models. The paired-test rule (A5) argues for
  two separate ladders. Confirm.
- **Q-D. The GLM-E footnote's history.** Rule E removes the checker wording
  from a footnote that has been public since 2026-11-17. Should the site say
  that it was reworded, or is a silent rewording fine? (Recommendation:
  silent rewording. The repo keeps the full record.)
- **Q-E. "Experiment arm" in headlines.** Every headline number is an
  experiment arm (wall-scaled turns, a 2× wall). The board says so, and the
  site keeps the label on the board. Do you also want it in the hero
  sentence, or is the board enough? (Recommendation: the board, plus
  "with its recommended agent setup" in the hero.)
