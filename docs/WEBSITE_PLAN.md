# openbeast.ai upgrade plan

**Status, 2026-11-16: plan only. Revised 2026-11-17** to the state after
PRs #175–#192 (see [What changed since the first draft](#what-changed-since-the-first-draft-2026-11-17)).
**Decided 2026-11-17:** Max answered Q5–Q10 ([section 13](#13-questions-for-max));
the `/benchmarks/` page ships now with the v5 ladder and is updated again
when arm E lands. Until then nothing in the website repo was changed,
committed or deployed. This page
says what openbeast.ai claims today, what is true today, and what to build so
the site matches OpenBeast at **v2.0.0**.

The site was last changed on 2026-10-26 (`81eabc2`, "Upgrade Next.js to
16.4.3"). Its only later pushes, through 2026-11-08, are Dependabot branches.
Since then the project shipped v1.8.0 and v1.9.0, put two DGX Sparks into
service, replaced the eval suite (v4 to v5), and landed 642 commits on `main`
after v1.9.0 that will ship as v2.0.0.

**How to read the labels.** **NOW** is what the site says today, quoted from
the file. **TRUE** is the current fact, with the doc that backs it.
**[V5-PENDING]** marks a number that does not exist yet. On 2026-11-16 that
was every v5 number. Since 2026-11-17 GLM-5.3-Flash on the Spark pair has four
finished configurations (runs `GLM-L0`, `GLM-A`, `GLM-B`, `GLM-C`) and the
Claude Opus 5.5 reference ceiling has its run (`OPUS-REF`), all in the v5 board
that `evals/v5_board.py` generates into [`docs/RESULTS.md`, "v5 leaderboard"](RESULTS.md#v5-leaderboard--best-configuration-per-model) and the
README (#193, merged 2026-11-17; "Every v5 configuration measured" lists each
run id). Still
[V5-PENDING]: GLM arm E onward, the seed-noise arm, every Qwen3.8 row and
DeepSeek v4.1 Flash.

| Section | What it gets you |
|---|---|
| [1. The site today](#1-the-site-today) | stack, deploy path, pages, sections |
| [2. Stale claims](#2-stale-claims) | every outdated claim, its file, the current fact |
| [3. Accuracy rules](#3-accuracy-rules) | what may be printed, and what may not |
| [What changed since the first draft](#what-changed-since-the-first-draft-2026-11-17) | the 2026-11-17 revision in one list |
| [4. New pages](#4-new-pages) | nine pages, and what each says; `/benchmarks/` carries the v5 story |
| [5. Updates to existing pages](#5-updates-to-existing-pages) | home, research, about, investors, investment case, shared files |
| [6. Rankings and benchmarks](#6-rankings-and-benchmarks) | how v5 is shown, the placeholder mechanism, what not to claim |
| [7. Visuals](#7-visuals) | photo shot list for Max, diagrams, screenshots, redaction |
| [8. SEO and metadata](#8-seo-and-metadata) | titles, social images, structured data, llms.txt |
| [9. Accessibility](#9-accessibility) | what is good already, what to add |
| [10. Performance](#10-performance) | budgets and the CSP constraints that shape them |
| [11. Upstream doc fixes first](#11-upstream-doc-fixes-first) | openbeast docs the site will link, which are stale themselves |
| [12. Checklist](#12-checklist) | P0 / P1 / P2 |
| [13. Questions for Max](#13-questions-for-max) | open, decided, new |

---

## What changed since the first draft (2026-11-17)

The first draft was written while the v5 run of the pair was still going.
Since then:

- **v5 has its first rows, and they are the story.** GLM-5.3-Flash on the
  Spark pair went from **26/71** in its daily config (run `GLM-L0`) to
  **53/71 scored, 54/71 raw** (run `GLM-C`) with three measured harness
  changes plus thinking, against a reference ceiling of **71/71** (Claude
  Opus 5.5 in its own scaffold, run `OPUS-REF`). Single seed throughout.
  `/benchmarks/` is rebuilt around this ([4.1](#41-benchmarks--the-v5-story-and-the-board-p0-the-centrepiece)).
- **The harness work is a research program** with a paper in progress (does
  each model need its own harness, or does one unified harness serve all?):
  a new `/research/harness/` page ([4.8](#48-researchharness--the-harness-engineering-story-p0)).
- **A model campaign anyone can replay** (`evals/campaign.py`, thinking-first
  by default, an eval host that provisions its own pinned toolchains, online
  or offline): a new `/research/campaign/` page ([4.9](#49-researchcampaign--bring-any-model-to-the-sparks-p1)).
  DeepSeek v4.1 Flash is next.
- **The Spark docs grew an entry point:** a one-screen quickstart, fresh-install
  and update-everything checklists, and a staged MTU 9000 change waiting on
  a restart ([4.2](#42-hardware--two-hosts-one-stack-p0)).
- **Product fixes worth a line:** Regenerate in Open WebUI now gives a new
  answer on the pair (a random seed per unseeded chat, #181), and the eval
  host installs offline from an ark drive (#189).
- **New accuracy rules** A10–A13 (single seed, the gold standard (A11, reworded by Q5), withheld
  units, harness gains are system claims), new stale claims S39–S46, two new
  diagrams (D9 the arm ladder, D10 the harness loop), and
  [questions for Max](#13-questions-for-max).

---

## 1. The site today

### Stack

| | |
|---|---|
| Repo | `MaximilianKhan/openbeast-website` (private), `main` at `81eabc2` |
| Framework | Next.js 16.4.3, React 19, Tailwind CSS 4, TypeScript. `output: "export"`, `trailingSlash: true`, `images.unoptimized` (`next.config.ts`) |
| Styling | One hand-written `src/app/globals.css` (1,089 lines). Dark, green-on-black console look. `prefers-reduced-motion` is honoured (line 1069) |
| Client JS | Two client components only: `ArchitectureMap.tsx` (lens buttons) and `PaperReader.tsx` (pdf.js, loaded on the paper page only) |
| Tests | Playwright, `e2e/site.spec.ts` (344 lines), on Chromium, Firefox and WebKit in CI (`.github/workflows/ci.yml`), plus `npm audit` and ESLint |
| Headers | `scripts/generate-headers.mjs` writes `out/_headers` after every build: a strict CSP with a sha256 per inline script, `img-src 'self'`, `connect-src 'self'`, no `media-src` (so `<video>` is blocked today) |
| Paper sync | `scripts/sync-paper.mjs <openbeast> <commit>` copies the beast-rank PDF and writes `src/app/research/paper-build.json`. It refuses while the paper carries `[PENDING]` markers, and the e2e suite fails on a preview |
| Open Dependabot branches | eslint 10.11 / 10.13, an npm minor-patch group, setup-node 7.1.0, two Docker base images |

### How it deploys

The static export runs on **the Mac Mini behind a Cloudflare Tunnel**
(`deploy/README.md`). `deploy/deploy.sh prod` builds `origin/main`, copies
`out/` into a new release, flips the `current` symlink, restarts the launchd
service `com.openbeast.website` (`bin/static-server.mjs` on `127.0.0.1:8120`),
health-checks for a 200 with a CSP header, and flips back on failure.
`deploy.sh rollback` restores the previous release. Deploys build from
`origin/main`, so **commit and push before deploying**.

The live site matches `main` (checked 2026-11-16): all six URLs answer 200,
the home page shows "v1.7.0 SHIPPED", `llms.txt` is byte-identical to
`public/llms.txt`, and the CSP and HSTS headers are present.

### Pages and sections

| URL | File | Sections, in order |
|---|---|---|
| `/` | `src/app/page.tsx` | Hero (release eyebrow, H1, startup terminal) · signal strip (4 numbers) · first audiences · 01 Architecture (`ArchitectureMap`, 4 lenses) + principles + reference-rig plate with photo · 02 What ships (9 capability cards) · 03 Evidence (reference default, v4 top-5 leaderboard, zig verdict trail, 12 domains, review strip) · 04 Scale out (fleet console, onboarding steps) · 05 Why OpenBeast (comparison table) · 06 Install (rig, client, preflight, air-gap, remote) · final CTA |
| `/research/` | `src/app/research/page.tsx` | Hero · paper card · six findings · protocol · two programs · open work · working rules · CTA |
| `/research/post-training-repair-llama-cpp/` | `src/app/research/[slug]/page.tsx` | Paper page: abstract now, in-page PDF reader once final. Old slug `frozen-grid-re-rounding` redirects |
| `/about/` | `src/app/about/page.tsx` | Founder hero + portrait · origin story · evidence of range · journey (6 steps) · principles · CTA |
| `/investors/` | `src/app/investors/page.tsx` | Thesis hero · proof console (4 metrics) · current position · thesis · commercial path · why now · go-to-market · founder fit · Blackwell reference lab (3 hardware tracks, 4 gates, Inception) · release cadence · next phase · contact |
| `/investors/investment-case/` | `src/app/investors/investment-case/page.tsx` | Dated 2026-10-06, sources pinned to `f246fd1`. Thesis · timing (market signals, memory chart) · evidence ladder · hardware table · workload result · proof record · market · capital · risks · diligence · source ledger (17) |
| Shared | `layout.tsx`, `SiteHeader.tsx`, `SiteFooter.tsx`, `public/llms.txt`, `sitemap.ts`, `robots.ts` | Metadata, JSON-LD (`WebSite`, `SoftwareSourceCode`, `Person`), nav (Evidence, Research, About, Investors, GitHub) |

Assets: `green-machine-{720,1280}.webp` (the 5090 rig), `founder-max-khan.webp`,
`openbeast-social.png` (one 777 KB social card shared by every page),
`favicon.svg`, and the v1 paper PDF.

---

## 2. Stale claims

Every claim below is live on openbeast.ai today. Fix all of them in the
**same commit** as the matching assertion in `e2e/site.spec.ts`: the test file
pins much of this copy (for example `"Not yet run on the 3090 Ti or DGX Spark
nodes"`, `"3,127"`, `"291"`, `"v1.7.0 SHIPPED"`, `"PLANNED · SOFTWARE PATH
TESTED"`), so a copy change without a test change fails CI.

### Release and project record

| # | File | NOW | TRUE |
|---|---|---|---|
| S1 | `src/app/page.tsx:8,106` | `"v1.7.0 SHIPPED · BEAST-HYDRA · BEAST-INSTINCT"`, linking `RELEASE_NOTES_v1.7.0.md` | v1.9.0 is the latest tag (2026-11-13, "spark-hydra"), after v1.8.0 ("beast-island", same day). v2.0.0 is next. `docs/RELEASE_NOTES_v1.9.0.md` |
| S2 | `src/app/investors/page.tsx:95` | `"v1.7.0 · 2026-09-30"` | v1.9.0 · 2026-11-13, then v2.0.0 |
| S3 | `investors/page.tsx:97`, `investment-case/page.tsx:219`, `public/llms.txt:73` | `"Eight tagged releases in twelve weeks"` / `"8 TAGGED RELEASES"` / `"eight tagged releases"` | Ten tags (v1.0 through v1.9.0), 2026-07-09 to 2026-11-13. Eleven with v2.0.0 |
| S4 | `src/app/about/page.tsx:62` | `"Eight tagged releases later, it spans remote clients, … and a router built to span a GPU fleet."` | Ten releases; it now also serves one model across two DGX Sparks and runs offline (`docs/SPARK_HYDRA.md`) |
| S5 | `investors/page.tsx:54-58` | Release list stops at `v1.7 2026-09-30` | Add v1.8 (2026-11-13: beast-island, skill import gate, the 10-09 review) and v1.9 (2026-11-13: spark-hydra live), then v2.0 |
| S6 | `investors/page.tsx:103`, `public/llms.txt:54` | v1.7 release notes linked as "current release" | `RELEASE_NOTES_v1.9.0.md` now; the v2.0.0 notes once written |
| S7 | `investors/page.tsx:98`, `investment-case/page.tsx:220`, `page.tsx:346`, `llms.txt:73` | `"3,127"` pytest tests at v1.7 | Stale. Recount on the rig or in CI at the v2.0.0 tag (`python3 -m pytest tests/ --collect-only -q`); a Mac without the rig's deps collects with 58 errors, so do not count there |
| S8 | `investors/page.tsx:100`, `investment-case/page.tsx:222`, `page.tsx:343-345` | `"260 FINDINGS CLOSED OR SCOPED"`, three reviews (118, 67, 75) | Two more reviews since: the 2026-10-09 review (about 170 findings closed, `RELEASE_NOTES_v1.8.0.md`) and the 2026-11-16 review (209 findings, 195 confirmed, all fixed). The 11-16 totals are not in any repo doc yet; see rule A6 |

### Hardware and the DGX Sparks

| # | File | NOW | TRUE |
|---|---|---|---|
| S9 | `page.tsx:26` | Multi-engine card: `"including a planned two-Spark tensor-parallel profile … physical Blackwell validation is still pending."` | The two-Spark profile is live: GLM-5.3-Flash Ablit, TensorFold TP 2 over ConnectX-7, since v1.9.0 (`SPARK_HYDRA.md`, "Status, 2026-11-15: live") |
| S10 | `page.tsx:63` | Fleet row `"PLANNED · 2× DGX Spark · TensorFold · TP=2 · GLM-5.3-Flash Uncensored · CTX TBD"` | LIVE. EXL3 4-bit Ablit weights, speculative decoding, 4 streams, 1M-token window, fp8 KV (`SPARK_HYDRA.md` Reference) |
| S11 | `page.tsx:380` | `"Not yet run on the 3090 Ti or DGX Spark nodes."` | Half true. The Sparks serve, but **beast-hydra does not route to them yet**: the rig's hydra node for the pair is in "Not done yet" (`SPARK_HYDRA.md`). The 3090 Ti host is still planned. New wording: "The Sparks serve on their own; routing the rig's hydra to them is next. The 3090 Ti host is still planned" |
| S12 | `page.tsx:219` | Rig plate: `"It is the reference rig behind the numbers on this page: one GPU box, serving every client around it."` | Two hosts now: `beast` (RTX 5090, llama.cpp) and `spark-hydra` (2× DGX Spark, TensorFold). Each is a full OpenBeast with its own command center |
| S13 | `page.tsx:116`, `layout.tsx:48`, `llms.txt:5` | `"Rig: Linux + supported NVIDIA GPU with 24 GB or more"` / `runtimePlatform: "Linux with a supported NVIDIA GPU"` | Also DGX Spark (GB10, aarch64, unified memory) on DGX OS, detected by `hardware.sh` since v1.9.0. Arch-based distros are not supported on the Spark (README Requirements) |
| S14 | `investors/page.tsx:117` | `"one measured Blackwell workstation"` | Two measured Blackwell systems: the RTX 5090 and the GB10 pair (serving throughput measured 2026-11-13; v5 capability [V5-PENDING]) |
| S15 | `investors/page.tsx:161` | `"Physical GB10 bring-up, model fit, context, throughput, stability, and fleet recovery remain explicit hardware-validation gates."` | Bring-up, model fit, context and throughput are measured on the pair. Still open: v5 quality [V5-PENDING], 24-hour stability, energy, hydra routing to the pair and fleet failover on real hardware |
| S16 | `investors/page.tsx:198` | `"OpenBeast is measured today on the founder's 32 GB RTX 5090."` | And on the two-Spark pair (`SPARK_HYDRA.md`) |
| S17 | `investors/page.tsx:211-215` | Track `"TARGET · MULTI-NODE"`: `"The software path exists; no Spark result is claimed yet."`, links `DGX_SPARK_PLAN.md` | Status becomes MEASURED (serving). Link `SPARK_HYDRA.md` as the runbook; keep `DGX_SPARK_PLAN.md` as the design record |
| S18 | `investors/page.tsx:67-72` | Four validation gates, all future | Gate 01 BRING-UP passed on the pair; gate 02 CAPACITY partly (throughput, TTFT, context measured; energy and sustained load not); gate 03 QUALITY running [V5-PENDING]; gate 04 FLEET not started on hardware. Show a status per gate |
| S19 | `investment-case/page.tsx:47,55-56` | Evidence ladder `"SIMULATED · The multi-engine control plane"`; hardware rows `"DGX Spark … PLANNED"` and `"2× 128 GB DGX Spark · PLANNED · SOFTWARE PATH TESTED"` | TensorFold on the pair: MEASURED (serving). hydra across hosts: still SIMULATED. Single-Spark row: still PLANNED as its own class |
| S20 | `investment-case/page.tsx:178-181` | Memory chart `"DGX SPARK · 128 GB TARGET / NODE"`, `"2× 128 GB SPARK · DISTRIBUTED TARGET"` | The pair is measured. Keep the caption that aggregate memory is not one pooled GPU |
| S21 | `investment-case/page.tsx:284` | Why wait: `"No physical DGX Spark result"` | Replace with: "No v5 capability result on the Spark pair yet" until the run lands, then drop it |
| S22 | `investment-case/page.tsx:126,199,301` | `"PUBLIC INVESTMENT CASE / 2026-10-06"`, `"AS OF 2026-10-06"`, sources pinned to `f246fd1` | Re-review every number, re-pin all project sources to the v2.0.0 tag commit, re-date |
| S23 | `investors/page.tsx:195` | `"The next bottleneck is access to the hardware the roadmap already targets."` | The Spark pair is in hand. The open hardware target is the RTX PRO 6000 class. Reword around what the pair proved and what the next class must prove |
| S24 | `llms.txt:75` | `"DGX Spark and RTX PRO 6000 results are not yet claimed."` | Spark serving results are claimed and documented; RTX PRO 6000 is not |

### Evaluation

| # | File | NOW | TRUE |
|---|---|---|---|
| S25 | `page.tsx:259` | `"Every model on the board earned its place in a reproducible v4 evaluation … 137 deterministic base tasks expanded to 291 effective units across 12 technical domains."` | The current suite is **v5.0: 71 scored units over 34 base tasks**, all 12 categories and all 6 languages, harness6 (`evals/README.md` "v5", `evals/CHANGELOG.md`). v4 is frozen. v4 and v5 scores are not comparable |
| S26 | `page.tsx:286`, `page.tsx:321` | `"V4 LEADERBOARD · RTX 5090 ×1 · 291 UNITS"`; `"STARTING EVALUATION COVERAGE · V4 · 12 DOMAINS · EXPANDING"` | Label the v4 board "frozen archive". Headline the v5 board [V5-PENDING] |
| S27 | `page.tsx:335` | `"Thirty-one tasks expand into variants across Python, Go, C, C++, Rust, and Zig."` | v5.0: 46 of 72 (category, language) cells covered; 32 Python, 8 each Go/C/C++/Rust, 6 Zig, 1 shell unit |
| S28 | `investors/page.tsx:99`, `investment-case/page.tsx:221`, `investors/page.tsx:70`, `llms.txt:11,73` | `"291 EVALUATION UNITS"`; gate 03 `"The full 291-unit evaluation runs on the remote engine"` | v5.0, 71 units, three arms (L0 bare, L1 packs, L2 packs + diagnostics + escalation), about 3.73 reference hours per full profile |
| S29 | `page.tsx:342-348` | Review strip ends at v1.7 | Add the 10-09 and 11-16 reviews, and the v5 suite's three adversarial rounds per new family |

### Tools, models and engines

| # | File | NOW | TRUE |
|---|---|---|---|
| S30 | `page.tsx:13,20,150`, `ArchitectureMap.tsx:14,68,100`, `llms.txt:3,15` | `"18 online"`, `"the 18 tools"`, `"18 LOCAL AGENT TOOLS"`, `"MCP + 18 tools"` | **19 MCP tools** (README, `docs/TOOLS.md`); 20 on the WebUI tool server for admins (the 19 plus `weather`), 3 for guests |
| S31 | `page.tsx:13` | Terminal line `"MCPO tools ......... 18 online · :3001"` | `:3001` is the identity tool server (`agents/openapi_tools.py`), not MCPO. Copy the real `./start.sh --status` output instead of inventing one |
| S32 | `page.tsx:152`, `investment-case/page.tsx:46,53,98`, `investors/page.tsx:207`, `llms.txt:7` | `"23 PRECONFIGURED MODELS"`, `"Twenty-three llama.cpp configurations"` | `docs/MODELS.md` still says 23, but `scripts/` now holds 28 serve scripts, including the bootstrap bridge, the instinct scorer, Open-Jev, and the opt-in weight-streaming profiles. Reconcile MODELS.md first (section 11), then print its number |
| S33 | `page.tsx:152` | `"3 INFERENCE ENGINES"` | Three managed engines (llama.cpp, vLLM, TensorFold) plus a fourth, Project Maya, that is opt-in and admitted only after it measures 1.5× llama.cpp (`docs/ENGINES.md`). Write "3 engines, 1 gated" |
| S34 | `page.tsx:281`, `llms.txt:7` | `"plus pinned onboarding and conformance tests for vLLM and TensorFold"` | Now also `scripts/engine.sh`: list, use, install, update, stage, rollback, with an offline path for each (`ENGINES.md`, unreleased until v2.0.0) |
| S35 | `ArchitectureMap.tsx:9,109` | Request lens: `"Client → Device gate → llama.cpp → GPU"` | True for `beast`. On `spark-hydra` the request goes Client → beast-gate `:8443` → TensorFold rank 0 on loopback → TP 2 across both Sparks |

### Research and other

| # | File | NOW | TRUE |
|---|---|---|---|
| S36 | `research/page.tsx:76,208` | `"Capability replication, three runs per arm on the coding suite (running)"`; `"The capability replication is on the GPU now."` | Not running. It is in the GPU backlog, parked 2026-10-09 ("C. Multi-day, each a separate decision", `docs/TODO.md`). The paper on `paper/submission-campaign` (`f3c8a0f7`) still carries 6 `[PENDING:F]` markers. Confirm with Max, then say "parked" |
| S37 | `research/page.tsx:103` | `"Two programs have run on the reference rig, a single RTX 5090."` | Still true for research. Add one line that eval work now also runs on the Spark pair, so readers do not infer one machine total |
| S38 | GitHub repo description (not the site, but it is what search and social previews show) | `"… a 15-tool agent …"` | 19 tools. Update with `gh repo edit MaximilianKhan/openbeast --description …` |

### Stale since the first draft (2026-11-17)

Claims in **this plan** (and two in openbeast docs) that the 2026-11-17 state
makes stale. Fix the plan's own copy before it reaches the site.

| # | Where | NOW | TRUE |
|---|---|---|---|
| S39 | S14, S15, S18, S21 rows above; §4.2 item 5; §5.4 | v5 quality on the pair is `[V5-PENDING]`; S21's replacement "No v5 capability result on the Spark pair yet" | One model has v5 rows on the pair: GLM-5.3-Flash, `GLM-L0` 26/71 to `GLM-C` 53/71 scored (single seed). Gate 03 QUALITY becomes MEASURED (one model, single seed); "Why wait" becomes "v5 rows for one model; Qwen3.8 and DeepSeek pending" |
| S40 | §6.4 last bullet; §4.2 item 3 | "GLM runs thinking off" | GLM's **daily serving config** is thinking off (and `GLM-L0` is that config). Its best v5 row, `GLM-C`, is **thinking on**, with wall-scaled turns at 2x the wall. Every row states both settings |
| S41 | §6.1 | Ranked by L0 SCORE, one row per system | The board shows **each model at its best configuration** (`evals/v5_board.py`), flags experiment arms (wall-scaled turns, a scaled wall, packs, diagnostics), and keeps reference ledgers unranked. L0, the daily config, stays a column so the two are never confused |
| S42 | §4.1 item 2 | "Spark pair first because it is the run in progress" | GLM's L0-C rows are finished; the run in progress is arm E |
| S43 | §5.1 Install; §4.2 | The Spark role links the whole `SPARK_HYDRA.md` | Link its **Quickstart** (one screen, #182) and the two checklists, "Install everything, fresh" and G6 "update everything" (#183) |
| S44 | §4.2 item 9 "Not done yet" | Silent on the fabric MTU | The fabric runs at MTU 1500 (RoCE `active_mtu` 1024). A reversible MTU 9000 change is scripted and waits for a restart of both Sparks; no speed gain is claimed until its A/B is measured |
| S45 | §11 rows | `ARCHITECTURE.md` "planned: two DGX Sparks"; README "Beyond one GPU" | `ARCHITECTURE.md` fixed in the 2026-11-17 notes PR; the README section became "Serving from DGX Sparks (spark-hydra)" in #182. `docs/RESULTS.md` "no model has a v5 row yet" is fixed by #193 |
| S46 | §5.2, S37 | Research has two programs | Three: the beast-rank paper, the v5 suite as measurement research, and **harness engineering** (a paper in progress, §4.8) |

Claims checked and still true: the founder story and dates, Apache 2.0, the
v4 numbers themselves (98.7 / 97.7 / 97.6 / 97.5), "140 tok/s" for the default
MTP profile, 262K native context, the NVFP4 workload split, the zig verdict
trail, the paper abstract and pending status, "does not represent itself as a
current [Inception] member".

---

## 3. Accuracy rules

These hold for every page, every caption, `llms.txt`, social cards and alt
text. Put them in the website repo's `AGENTS.md` so any agent editing the site
reads them.

### A. Numbers

- **A1. No performance claim without a `docs/RESULTS.md` row.** A capability
  score, ranking, pass rate or "beats" statement needs a row in
  [`docs/RESULTS.md`](RESULTS.md) at a pinned commit, and the site links that
  row at that commit. Serving numbers (tok/s, TTFT, streams, window) need a
  measured table in a doc (`SPARK_HYDRA.md` Reference, `MODELS.md`), stated
  with its conditions: date, engine, drafter, streams, prompt type.
- **A2. Pin every project link to a commit.** Use the v2.0.0 tag commit, not
  `main`, the way `investment-case/page.tsx` already pins `f246fd1`. A number
  must still match its source when someone clicks it months later.
- **A3. Never compare across suites or eras.** No v4 score beside a v5 score
  on one axis. No harness5 row beside a harness6 row. The v4 board is an
  archive with its own heading.
- **A4. Rows are systems, not models.** A v5 row is (host, engine, engine
  version, weights repo@rev, quant, drafter, decoding, arm). "GLM beats Qwen"
  is not a claim the data can make when they ran on different hosts and
  engines. Say "spark-hydra (GLM-5.3-Flash Ablit, TensorFold) scored X; beast
  (Qwen3.8 27B Uncensored, llama.cpp) scored Y".
- **A5. Small suites, honest gaps.** v5.0 has 71 units. Two rows within a few
  units are a tie unless `evals/ablation.py` resolves the paired difference.
  Show ties as ties.
- **A6. Review totals need a record.** "209 findings, 195 confirmed, all
  fixed" for the 2026-11-16 review is not written down in any repo doc yet.
  Add it to `docs/TODO.md` or the v2.0.0 release notes first, then cite it.
- **A7. Unmeasured stays labelled.** Weight-streaming targets (Flash-Next
  55–70 tok/s, big mode 20–30 tok/s) are targets, not results
  (`WEIGHT_STREAMING.md`: "No number in this file was measured on our rig
  unless it says MEASURED (us)"). The default MTP profile has no capability
  row of its own. Native TensorFold 1.2.7 is installed but not serving.
  `PARALLEL=8` is untried. Fully offline rebuild of the pair is not done (H9).
- **A8. Keep the investment-case status vocabulary site-wide:** SHIPPED,
  MEASURED, SIMULATED, PLANNED, HYPOTHESIS, PRIVATE, plus **PENDING** for a
  run in progress.
- **A9. Words to avoid:** "frontier-class" (the README hero uses it; the site
  must not without a cited comparison), "fastest", "best", "beats GPT-…",
  "secure" without a scope, "air-gapped" for anything the H9 offline rebuild
  has not proved, "production fleet".
- **A10. Single seed, said every time.** Every v5 harness delta so far is one
  run at one seed. TensorFold's decoding is deterministic at a fixed seed
  (G12), so re-running the same seed is not a replicate; the noise floor
  comes from a different seed (the campaign's seed arm), which has not run
  yet. Print "single seed" beside every arm number and delta, and no
  percentage-improvement headline ("+104%") until the seed arm lands.
- **A11. The gold standard, not a peer (Max, Q5, DECIDED 2026-11-17).**
  Claude Opus 5.5 ran in Claude Code's own scaffold: its own tools, no turn
  cap, wall x2 (68/71 at the 1x wall), the solver never saw validator output,
  integrity scan clean (`OPUS-REF`). It is the **gold standard** every local
  model is measured against: "how close local gets to frontier", and an
  indicator of what a frontier model can confidently delegate to local AI.
  Rules: label it "gold standard: Claude Opus 5.5, its own scaffold"; keep
  it **unranked** and visually apart (its own row style, never the same bar
  colour as a local row, never a rank number); print each local row's
  **fraction of the gold standard** as its SCORE divided by the gold
  standard's SCORE (100.0 today, so `GLM-C` is 78.2%), always beside the
  absolute numbers (53/71, 78.2) and "single seed" (A10); say "of the gold
  standard", never "GLM matches / approaches / is X% as smart as Opus", and
  never a delta between two fractions as a headline (A10 still bars
  "+104%"-style improvement claims). Any fraction against the 1x-wall run
  (68/71) is a different, unpublished number: do not mix them.
- **A12. Withheld units stay withheld.** `GLM-C` passed 122_gemm_blocked_b,
  but in that unit the agent read Claude Code workflow journals, the harness
  source and the eval cache, which the sandbox did not block. The board
  scores **53/71** and footnotes the raw 54/71 with the reason. The site
  prints 53/71 with the same footnote, never 54/71 alone, and syncs when the
  integrity review clears or voids the unit
  (`evals/boards/v5/exclusions.json`).
- **A13. Harness gains are system claims.** "GLM-5.3-Flash under OpenBeast's
  harness, arm C, scored 53/71", never "GLM got smarter" or "our harness
  doubles any model". Whether a change that helps GLM helps Qwen3.8 or
  DeepSeek is the open research question (§4.8), not a result.

### B. Uncensored models

The default on both hosts is uncensored: Qwen3.8 27B Uncensored on `beast`,
GLM-5.3-Flash **Ablit** on `spark-hydra`. Describe it the same way everywhere.

- **B1. Define it once, plainly.** "Uncensored (abliterated) weights: the
  model's refusal behaviour was removed from its weights. It answers instead
  of declining. The operator decides what it is used for."
- **B2. Separate model refusals from system guardrails.** The tool guardrails
  stay on whatever the model is: RBAC tiers, per-user shards, the per-call
  audit, path guards, SSRF-pinned fetch, the optional sandbox, beast-gate
  device keys. Say so next to every uncensored mention.
- **B3. Cost claims are per model.** "Abliteration measured at no capability
  cost" is true only for Qwen3.8 27B on v4 (97.6 vs 97.7 stock). There is no
  stock-versus-Ablit comparison for GLM-5.3-Flash. Do not extend the claim.
- **B4. Never:** "no limits", "no rules", "no guardrails", "jailbroken",
  "does anything you ask", screenshots of harmful output, or uncensored
  framing on the medical or legal audience cards.
- **B5. Keep the governance risk visible.** The investment case already lists
  the uncensored-only fleet policy as a risk for regulated use
  (`investment-case/page.tsx:93`). Keep it, and say that the guest RBAC tier
  limits a WebUI user to `web_search`, `fetch` and `weather`.
- **B6. DFlash2 is under the hood, never marketed (Max, 2026-11-16; DECIDED).** The
  pair's drafter, `incoai/GLM-5.3-Flash-DFlash2`, is CC BY-NC-ND (personal
  use, `SPARK_HYDRA.md` §0). Decision: the site never names DFlash2. It may
  say "speculative decoding" generically where a speed figure needs its
  conditions. Before any investor or "supported deployment" copy presents
  the Spark configuration as a product, still confirm the licences of the
  Ablit weights and Mia-AiLab's recipe, and say which parts a commercial
  deployment would swap out (the drafter among them).

### C. Security and privacy

- **C1. Nothing from the tailnet on the site.** No tailnet names
  (`*.ts.net`), IPs (tailnet, LAN, ConnectX-7 rails), hostnames
  (`spark-3dc6`, `spark-9f26`), device keys, enrollment QR codes or links,
  usernames or home-network detail. `SPARK_HYDRA.md` contains all of these;
  copy prose from it, never tables.
- **C2. Name the open issues you link to.** `SPARK_HYDRA.md` "Known issues"
  records that loopback is not isolation on spark-1 (host-network containers
  reach the keyless engine). Any security page that cites the pair must say
  so, with the planned fix (native TensorFold with `--api-key-file`).
- **C3. Private repos are not links.** `openbeast-ios` and
  `openbeast-android` are private. Describe them as "in development"; do not
  link them, and do not imply store availability until Max says so.

---

## 4. New pages

Nine pages. Each needs metadata, a social card, a sitemap entry, a nav or
footer link, and an e2e test. The header nav grows from four links to six:
**Benchmarks, Hardware, Offline, Clients, Research, About**, with Investors
and Security in the footer (the investor pages stay linked from About). The
two new research pages (4.8, 4.9) sit under Research.

### 4.1 `/benchmarks/` — the v5 story and the board (P0, the centrepiece)

Replaces the home page's v4 table as the home of rankings, and becomes the
page the site leads with. Full spec in [section 6](#6-rankings-and-benchmarks).
Every number below cites its v5 board run (rule A1); each is single seed
(A10).

1. **Hero:** "71 units. Every one proven without a model." One paragraph:
   v5.0 is 71 scored units over 34 base tasks, all 12 categories, all six
   languages (Python, Go, C, C++, Rust, Zig). Every unit is proven before any
   model sees it; the answer key and the web are sealed from the measured
   agent, and the solution runs confined (`evals/README.md`, v5).
2. **The story, as a ladder (diagram D9).** GLM-5.3-Flash Ablit on the Spark
   pair (TensorFold, EXL3 4-bit), same weights on every rung, one change per
   rung, each rung a full 71-unit re-run, never a re-roll of failures:

   | Rung | What changed | v5 passed | SCORE | Source |
   |---|---|---|---|---|
   | L0 | the daily config: runner v1, thinking off | **26/71** | 48.2 | `GLM-L0` |
   | A | runner v2: the frictions the L0 failure audit found | **31/71** | 56.9 | `GLM-A` |
   | B | + turns scaled to each unit's wall | **36/71** | 60.4 | `GLM-B` |
   | C | + thinking on (wall x2, B's turns) | **53/71** scored (54 raw, A12) | 78.2 | `GLM-C` |
   | E | + diagnostics, verify-done, packs, env block, wall warnings | [V5-PENDING] | [V5-PENDING] | — |
   | gold standard | Claude Opus 5.5, its own scaffold (A11) | **71/71** (68/71 at 1x wall) | 100.0 | `OPUS-REF` |

   Each local rung also prints its **fraction of the gold standard** (Q5,
   A11): L0 48.2%, A 56.9%, B 60.4%, C 78.2%.

   Source for every row: [`docs/RESULTS.md`, "v5 leaderboard"](RESULTS.md#v5-leaderboard--best-configuration-per-model), "Every v5 configuration
   measured", at a pinned commit.

   Caption: "Single seed. Each rung is a system: host, engine, weights and
   harness. The gold standard ran in its own scaffold: it is the yardstick,
   not a ranked peer." Link `/research/harness/` for how each rung was found
   and measured.
3. **Best-per-model board,** generated, never hand-copied: one row per model
   at its best configuration, from `evals/v5_board.py` (the marker block it
   writes into `docs/RESULTS.md`), synced by `scripts/sync-results.mjs`
   (6.3). Experiment-arm flags shown as chips (thinking, wall x2,
   wall-scaled turns, packs, diagnostics), and a "of gold standard" column.
   Opus as the unranked gold-standard row, set apart. Qwen3.8 27B Uncensored MTP (the rig's default) and Qwen3.6 27B
   (v4 #1): "not yet run on v5".
4. **Every configuration,** collapsed per model: GLM L0, A, B, C (E when it
   lands), each with its flags and run date, so the best row never hides the
   daily one.
5. **What v5 measures:** 71 units, 34 bases, the three groups (14 new
   families, 4 upgraded v4 tasks, 16 v4 tasks kept as defined), the coverage
   grid (46 of 72 cells), the three language-assistance arms.
6. **How a unit earns its place (diagram D7):** proven without a model
   (`ref`, `wrong`, `empty`, `leak`, `slow`, `scale2`, `lint`, `toolchain`
   arms), up to three adversarial rounds per new family, sealed validators
   and answer key, web off, toolchains pinned.
7. **What v5 cannot tell you yet:** every number is single seed (A10); one
   GLM pass is withheld pending an integrity review (A12); the pack effect
   needs replicates and a held-out check (`evals/README.md`, "How small an
   effect one profile can see"); 26 cells are empty until v5.1; kept-v4
   units did not get the adversarial review in their v4 form.
8. **v4 archive (frozen):** the nine-row table from the README, with its
   condition caveat (seven July rows sequential on an earlier era; two
   Qwen3.8 rows at `jobs 4`). Collapsed by default.
9. **The misses ship with the hits:** move the zig verdict trail here from
   the home page, and add v5's failure taxonomy in one table: of GLM-C's 17
   failures, 13 are process failures (almost all a thinking turn capped
   before the file was written), 3 algorithmic, 1 zig knowledge
   (`HARNESS_NOTES.md`, "Language-specific residue under thinking").
10. **Reproduce it:** the `evals/profile.py` and `evals/campaign.py`
    commands, linked at a pinned commit.

### 4.2 `/hardware/` — two hosts, one stack (P0)

1. **Hero:** "Two machines think. Each is a whole OpenBeast."
2. **beast:** the existing rig plate (RTX 5090 ×1, Ryzen 9950X3D, 128 GB
   DDR5, llama.cpp, default Qwen3.8 27B Uncensored MTP, 262K, 140 tok/s).
   New photo (section 7).
3. **spark-hydra:** 2× DGX Spark (GB10, 128 GB unified memory each), linked
   over ConnectX-7 on two rails. GLM-5.3-Flash Ablit, EXL3 4-bit, TensorFold
   TP 2, speculative decoding, 4 streams, 1M-token window, fp8 KV, thinking off by
   default for chat (the best v5 row turns it on per request, S40). Command
   center on spark-1, spark-2 runs rank 1 only: **one user unit on spark-1
   drives both ranks** (it starts rank 1 on spark-2 over ssh; spark-2 runs no
   OpenBeast services). Boots by itself with no network.
4. **Measured serving table** (2026-11-13, through the API on spark-1):
   prose 43–44 tok/s single, 145 aggregate across 4 (37 each); code 68–70
   single, 224 aggregate (57 each); TTFT about 160–260 ms. No drafter
   names (B6): at most "with speculative decoding". Caption with conditions, per rule A1.
5. **Capability on this hardware:** GLM-5.3-Flash, 26/71 in its daily
   config (`GLM-L0`) and 53/71 at its best (`GLM-C`, thinking on), single
   seed, linking `/benchmarks/`. Never beside the serving numbers on one
   chart (6.2).
6. **Run it yourself:** the Quickstart (health, start/stop, clients, the
   bare-box order), then the two checklists: "Install everything, fresh"
   (21 steps, each with its offline variant) and "update everything" (G6,
   with a per-PR table of operator steps). Link the anchors at the tag (S43).
7. **The engine plane:** the engines × host-class matrix from `ENGINES.md`
   (llama.cpp default on the rig, TensorFold default on the Spark, vLLM both,
   Maya gated at 1.5×), and the one-command swap (`scripts/engine.sh use`,
   `rollback`, `stage` / `install --from`).
8. **Bigger than the card: weight streaming.** MoE experts in host RAM, a
   VRAM expert cache (llama.cpp v1.0.0 `--moe-cache-mib`), per-profile memory
   scope, opt-in big mode for GLM on the 5090. Every number labelled TARGET
   until measured (rule A7).
9. **Scale 1 to 8 Sparks:** `spark-cluster.sh` and `spark-guard.sh` exist and
   are documented; anything beyond 2 is VERIFY ON HARDWARE.
10. **Not done yet:** hydra routing to the pair, switching the primary between
   hosts, `PARALLEL=8`, native TensorFold vs the recipe, a fully offline
   rebuild, a one-command model swap with rollback (planned, `TODO.md`), and
   **the fabric MTU**: the rails run at 1500 (RoCE 1024); a scripted,
   reversible move to 9000 waits for the next restart of both Sparks, and no
   gain is claimed before its A/B (S44).

### 4.3 `/offline/` — offline-first (P0)

Offline operation is a design rule of the project, and the site does not
mention it beyond one air-gap card.

1. **Hero:** "Pull the cable. It keeps thinking."
2. **The ladder,** one diagram (section 7, D4):
   - **beast-island** (v1.8): the rig notices the route is gone in about
     90–120 s, `web_search` / `fetch` / `weather` answer "offline" at once,
     everything else keeps serving, and it rejoins by itself.
     `./start.sh -d --offline` forces it.
   - **Air-gap install** (v1.5): signed bundle, hash-pinned wheelhouse,
     install from a USB stick.
   - **Engines offline:** `scripts/engine.sh stage` on a connected box,
     `install --from` on a closed one; native TensorFold verified by sha256,
     an ed25519 signature and `SHA256SUMS`.
   - **beast-docs:** Apple and Android developer docs mirrored offline, every
     answer stamped with URL and fetch date; `platform_docs` tool.
   - **beast-ark:** 112 open-source packages (350 downloads), each pinned to
     version, tag and commit with a hash per file, on a cold-storage drive;
     `verify`, `restore` and `serve` never use the network; `serve` publishes
     the drive to the tailnet as an OCI registry plus pip, npm and go mirrors.
   - **spark-hydra boots offline:** the boot unit never reaches the Hub or a
     registry.
   - **The eval host provisions itself offline:** `scripts/eval-host.sh`
     installs the pinned zig, rust, go and python the v5 units were proven
     with, sha256-checked, from the internet or `--from` an ark drive; a
     dead interpreter is recorded as lost, never scored as a model failure
     (#189).
3. **The model maintains its own repo, network off.** A short section from
   `SELF_DEVELOPMENT.md` §9: on spark-1, GLM-5.3-Flash with OpenBeast's own
   tools made a correct two-file fix with network tools disabled, test green,
   in 25 iterations / 68 s, and hit the iteration cap before `task_done`.
   Quote both halves.
4. **What is not proved yet:** the end-to-end offline rebuild of the pair
   (H9). Say it.

### 4.4 `/clients/` — intelligence on every device (P1)

1. **Mac and Linux client** (beast-slot, v1.1): OpenCode and the 19 tools run
   on the laptop against its own files; only inference crosses the tailnet;
   `openbeast-client status` reads `/api/slot`. The trust warning from the
   README: joining someone's rig is closer to giving them a shell.
2. **Remote access through beast-gate:** per-device keys
   (`scripts/clients.sh`), route allowlist, rate and in-flight caps (per-device
   cap now defaults to the host's slot count), inference audit. On the pair,
   `:8443` answers 401 without a key (checked from off-LAN). Two identity
   layers: `:3001` authenticates the human, beast-gate the device.
3. **Phone:** beast-chat console (PWA, push via ntfy, steer / pause / stop),
   tailnet login accepted only on a 0600 Unix socket when
   `CHAT_LOGIN_FROM=unix` (hardened in review 1116, trusted by its listener).
   beast-artifact pages, now with the same Unix-socket login option.
4. **Native apps, in development:** iOS (SwiftUI, iOS 17+: Chat over
   beast-gate, Agents over beast-chat, Rigs, Face ID lock, QR enrollment) and
   Android (Compose, minSdk 28: the same tabs, Keystore-sealed keys,
   encrypted conversations). No links (rule C3). Screenshots only with Max's
   approval.
5. **Weather from where you are:** the WebUI `weather` tool resolves the
   place near the caller, so "weather in Danville, CA" no longer lands in
   Quebec; no location means where the caller is now (`TOOLS.md`).
6. **Regenerate gives a new answer.** On the Spark pair, Regenerate in
   Open WebUI used to return the same text word for word (the engine falls
   back to a fixed seed). WebUI now gives each unseeded chat a random seed;
   an explicit seed stays reproducible (#181). Other clients get the same
   fix at beast-gate next (`TODO.md`); say so if the page mentions it.
7. **External apps (beast-apps):** a catalog of pinned MCP servers for Xcode
   and the iOS Simulator, Gradle and adb, mobile UI driving, Blender, Figma,
   3D printers and ROS. `scripts/apps.sh check` says READY or exactly what is
   missing; it runs on the client, next to the apps.

### 4.5 `/security/` — trust model and review record (P1)

1. The trust model in one diagram: tool plane loopback-only, inference plane
   the only thing on `:8443`, two identity layers.
2. Supply chain: weights sha256-locked, images digest-pinned, Python
   hash-pinned and CVE-audited in CI, 43 GitHub-hosted files pinned to release
   digests, signed native engine.
3. **The review record,** one table: v1.6 (118), v1.7 (67 + 75), 2026-10-09
   (about 170), 2026-11-16 (209 found, 195 confirmed, all fixed; rule A6).
   Method: each finding checked by a verifier told to refute it, then fixed
   with a regression test or scoped in writing.
4. **Known open issues,** linked at a pinned commit: loopback is not
   isolation on spark-1; stale spark-guard rules until the operator step;
   SOC 2 readiness is a gap list, not a certification (`SOC2_READINESS.md`).
5. Link `SECURITY.md` for reporting.

### 4.6 `/releases/` — the record (P1)

One row per tag, v1.0 through v2.0.0, each with date, headline and its notes
at the tag. The v2.0.0 row lists what lands after v1.9.0: the engine plane,
weight streaming, beast-docs, beast-apps, beast-ark, the weather tool, the v5
suite and harness6, client hardening, the 11-16 review. Replaces the
"four milestones" list on `/investors/`, which then links here.

### 4.7 `/v2/` — the launch page (P2, at the v2.0.0 tag)

A one-screen announcement that the hero eyebrow links to: two hosts, offline
first, v5 board, what changed for operators (the "Read before upgrading" items
from the v2.0.0 notes). Retire it into `/releases/` after a month.

### 4.8 `/research/harness/` — the harness-engineering story (P0)

The "how" behind the ladder on `/benchmarks/`. Sources:
[`HARNESS_NOTES.md`](HARNESS_NOTES.md),
[`MODEL_CAMPAIGN.md`](MODEL_CAMPAIGN.md),
[`reviews/HARNESS_PRIOR_ART-2026-11-16.md`](reviews/HARNESS_PRIOR_ART-2026-11-16.md).
Every number single seed (A10), every claim about one model only (A13).

1. **Hero:** "Same weights. A better loop around them." One paragraph: the
   v5 audit of GLM-5.3-Flash found 43 of its 45 L0 failures to be genuine
   mistakes *under that harness*, and most ended on the turn cap with
   10–30% of their wall used. The question became how much of the gap is
   the harness.
2. **The arm ladder (D9),** the same table as `/benchmarks/` item 2, with one
   paragraph per rung on what changed and why:
   - **Runner v2** (A): the audited frictions: no-op plan loops, repeated
     identical calls, BSD-vs-GNU userland surprises, a client timeout that
     retried a whole turn. +5 net (11 rescued, 6 regressed).
   - **Wall-scaled turns** (B): a turn budget scaled to each unit's wall
     instead of one cap for all. +5.
   - **Thinking** (C): per-request thinking at 2x wall with B's turns. +17
     scored (+18 raw); on the first 21 units, 14 of its 18 passes finished
     inside B's original wall, so the gain is the thinking, not the time.
     Its price is latency: about 2.3x B's time per unit.
   - **E** (diagnostics with an edit hint, verify-done, packs, an
     environment block, wall-clock warnings): [V5-PENDING].
3. **The harness loop (D10):** what the runner adds around each model turn
   (section 7.2).
4. **The method:** every failure read and classed (the audit) → a numbered
   entry with its evidence (`HARNESS_NOTES.md` G1–G23) → an opt-in switch
   keyed in the eval cache → a full 71-unit arm, never a re-roll of
   failures. **Deterministic-seed noise:** decoding is deterministic at a
   fixed seed, so a same-seed re-run is not a replicate; the noise floor
   comes from a different seed (pending). **Integrity scans:** the Opus
   reference was scanned clean; one GLM-C pass is withheld because the agent
   could read the eval cache and harness source (A12); the read-deny fix and
   a scan of every v5 row are open work. Say all three plainly: they are
   what makes the ladder believable.
5. **What is left after thinking:** language-specific failures nearly
   vanish; one zig knowledge failure remains (a thinking model routes around
   a removed API to the nearest primitive, which in zig 0.16 is gone too),
   and the largest failure is now thinking's own: a turn capped before the
   file is written. Both are open work, not results.
6. **The paper in progress:** working title "Per-model or unified? Agent
   harnesses for local models" (Q7). Question: does each model need its own
   harness, or does one harness with a few per-model knobs serve
   GLM-5.3-Flash, Qwen3.8 and DeepSeek v4.1 Flash? Status: one model
   measured; the cross-model arms are next. A status chip (IN PROGRESS), no
   conclusion yet. **Q7 decided:** the paper is public and will be posted on
   the site; give it a paper page like the beast-rank paper's
   (`/research/<slug>/`, synced by `sync-paper.mjs` at a pinned commit, the
   same pending guard) as soon as a build exists, and link it from
   `/benchmarks/` and `/research/`.
7. **Prior art, credited:** Factory's Droid, read from public sources only
   (nobody ran it): environment discovery at startup, tool design, short
   timeouts, plan reminders and a per-model adapter, with per-scenario text
   injected when an event fires. OpenBeast's env block (G22) and wall
   warnings (G23) came from that reading. DeepSeek's own agent harness
   (`dsh`, evaluated 2026-10-09, not integrated) supplies the starting
   hypotheses for the DeepSeek arm. Link both reviews at the tag.
8. **Caveats box:** single seed; one model; a reference in a different
   scaffold; E pending; one unit withheld.

### 4.9 `/research/campaign/` — bring any model to the Sparks (P1)

The replicable recipe from [`MODEL_CAMPAIGN.md`](MODEL_CAMPAIGN.md), as a
page an operator or a lab could follow.

1. **Hero:** "Bring a model. Leave with a measured config."
2. **The steps,** one card each, with the command at a pinned commit:
   onboard (inspect, pin, fetch to both Sparks, serve behind beast-gate,
   conformance) → prerequisites (the eval host, below) → the L0 profile →
   the failure audit → the reference ceiling (reuse the v5 Opus ledger) →
   the harness arms → the report and its decision rules.
3. **Thinking first:** the default campaign (`v5-thinking-first`) runs the
   composite of every change believed to help, with thinking on, then the
   composite minus one component at a time, then a seed arm, then the
   no-thinking counterparts. Why: on GLM, thinking moved more than every
   other change together (A10 applies).
4. **The eval host, offline-first:** `scripts/eval-host.sh` installs the
   pinned toolchains from the internet or an ark drive; tools and validators
   run on the harness host, never on the Sparks.
5. **Rows stay comparable:** pinned trees, distinct model names per arm,
   infra rows never scored, a pass always a verdict (MODEL_CAMPAIGN §7).
6. **Next: DeepSeek v4.1 Flash,** thinking first: [V5-PENDING]. Say "next",
   not a date. Verify its per-request thinking switch live before any arm
   (MODEL_CAMPAIGN §9).

---

## 5. Updates to existing pages

### 5.1 Home (`src/app/page.tsx`)

- **Hero eyebrow:** "v2.0.0 · TWO HOSTS · OFFLINE FIRST" linking `/v2/` once
  tagged. Until then: "v1.9.0 SHIPPED · SPARK-HYDRA" linking the v1.9.0 notes
  at the tag.
- **H1 and lede:** keep "Your hardware. Your AI workstation. Intelligence
  anywhere." Add one sentence to the lede: "Run it on one GPU box or a pair of
  DGX Sparks, and keep it running when the internet is gone."
- **Compatibility line (S13):** "Rig: Linux + NVIDIA GPU, 24 GB or more, or
  DGX Spark on DGX OS. Clients: Mac or Linux. Browsers and phones need no
  GPU."
- **Terminal (S31):** replace the invented lines with real
  `./start.sh --status` output from `beast`, redacted per rule C1.
- **Signal strip:** 19 agent tools · [model count from MODELS.md] · 2 hosts ·
  0 cloud inference dependencies.
- **Architecture:** update `ArchitectureMap` (see 5.7).
- **Rig plate (S12):** becomes two plates side by side, `beast` and
  `spark-hydra`, each linking `/hardware/`.
- **What ships:** keep the card grid. Update Multi-engine (S9). Add cards:
  beast-island (v1.8), spark-hydra (v1.9), engine plane, weight streaming,
  beast-docs, beast-apps, beast-ark (each "v2.0" once tagged). Twelve
  capability cards plus the rig card fit a 4-column grid.
- **Evidence:** shrink to the v5 story in one band: "71 proven units.
  GLM-5.3-Flash on the Spark pair: 26/71 daily config, 53/71 with a better
  harness and thinking: 78.2% of the gold standard (Claude Opus 5.5, 71/71,
  its own scaffold; Q5). Single
  seed." Each number links its board run (`GLM-L0`, `GLM-C`, `OPUS-REF`);
  the `beast` row stays [V5-PENDING] until Qwen3.8 runs v5. Link
  `/benchmarks/` and `/research/harness/`. Move the v4 table, the zig trail
  and the 12-domain grid to `/benchmarks/`.
- **Review strip (S29):** five entries through 2026-11-16, plus the test
  count recounted at the tag (S7).
- **Scale out (S10, S11):** Spark node LIVE with its real config; 3090 Ti
  stays PLANNED; the honest-status line rewritten as in S11.
- **New band "Offline first":** three tiles (island, ark, offline engines)
  linking `/offline/`.
- **Comparison table:** keep the columns. Before adding rows for new features
  (offline mode, package archive), re-check Ollama, Hermes Agent and ODS
  against their current docs and date-stamp the table; never mark a
  competitor "No" from memory.
- **Install:** add a third role, "The Spark pair", linking the
  `SPARK_HYDRA.md` Quickstart and the fresh-install checklist at the tag
  (S43); add `./start.sh -d --offline` and `scripts/engine.sh list` to the
  notes row.

### 5.2 Research (`src/app/research/page.tsx`)

- Fix S36 after confirming with Max: open work becomes "parked (GPU backlog,
  2026-10-09)", with the 60–90 h estimate from `TODO.md`.
- S37: one sentence that eval work now also runs on the Spark pair.
- Add a third program card: **the v5 eval suite** as measurement research
  (adversarial unit review, the prover, the arm design, the MDE table),
  linking `/benchmarks/`.
- Add a fourth program card: **harness engineering** (S46): the arm ladder
  in one line (single seed), the per-model vs unified paper IN PROGRESS,
  linking `/research/harness/` and `/research/campaign/`.
- Paper card unchanged until `sync-paper.mjs` accepts a pending-free build.

### 5.3 About (`src/app/about/page.tsx`)

- S4: "Ten tagged releases later", and extend the list: two DGX Sparks
  serving one model, offline operation, native mobile clients in development.
- Add a short 2026-11 journey entry if Max wants it ("Two Sparks, one
  model").
- Replace or add a founder photo with the hardware (shot P6 in section 7).

### 5.4 Investors (`src/app/investors/page.tsx`)

- Proof console: S2, S3, S7, S8, S28.
- "Current position": "two measured Blackwell systems" (S14).
- Why now: S15.
- Blackwell lab: S16, S17, S18, S23. The Spark track becomes MEASURED with a
  link to `/hardware/`; RTX PRO 6000 stays TARGET.
- Release cadence: S5, then link `/releases/`.
- Add a licence sentence to the commercial path per rule B6.

### 5.5 Investment case (`src/app/investors/investment-case/page.tsx`)

Re-issue it as a new dated edition rather than editing in place: keep the
2026-10-06 text reachable (for example at `/investors/investment-case/2026-10-06/`)
so anyone who read it can see what changed.

- Re-date, re-pin every project source to the v2.0.0 tag commit (S22).
- Evidence ladder and hardware table: S19, S20.
- Why wait: S21.
- Proof record: S3, S7, S8, S28.
- Risk register: add **licensing of the serving stack** (B6) and **host
  security on the pair** (C2). Keep "uncensored-only fleet policy".
- Sources: add `SPARK_HYDRA.md`, `ENGINES.md`, `evals/README.md` (v5),
  `RELEASE_NOTES_v1.8.0.md`, `RELEASE_NOTES_v1.9.0.md`, the v2.0.0 notes;
  replace the v1.6 / v1.7 notes as sources for current totals.

### 5.6 Shared files

- `public/llms.txt`: rewrite against the new pages; fixes S3, S6, S7, S13,
  S24, S25, S28, S30, S32, S34. Add the new pages to its "Website" list.
- `src/app/layout.tsx`: S13 in JSON-LD; see section 8.
- `src/app/sitemap.ts`: add the new pages; see section 8.
- `SiteHeader.tsx` / `SiteFooter.tsx`: the nav in section 4.
- `e2e/site.spec.ts`: rewrite each pinned stale string with the copy that
  replaces it, plus one test per new page, plus the placeholder guard in 6.3.

### 5.7 `ArchitectureMap.tsx`

- Every "18" becomes "19" (S30).
- Inference plane: show two engines side by side, "llama.cpp · beast" and
  "TensorFold TP 2 · spark-hydra", both behind beast-gate `:8443`.
- Request lens: a host toggle or a second step list for the Spark path (S35).
- Scale lens: say hydra routing to the pair is next (S11).
- Add a fifth lens, **Offline**: island watcher → web tools refuse at once →
  local services keep serving.

---

## 6. Rankings and benchmarks

### 6.1 What the board shows

One table per suite version. For v5.0 the table is the **best
configuration per model**, exactly as `evals/v5_board.py` renders it into
`docs/RESULTS.md` ([`docs/RESULTS.md`, "v5 leaderboard"](RESULTS.md#v5-leaderboard--best-configuration-per-model), #193): the script merges repair runs (a later row replaces
an earlier one only when the earlier one is an infrastructure row), computes
the v5 SCORE, keeps each model's best configuration, flags experiment arms,
and marks reference ledgers as an unranked ceiling. The site never
re-ranks or recomputes it. Columns, in this order:

| Column | Source |
|---|---|
| # | rank by SCORE of each model's best configuration; ties share a rank (rule A5); the reference row has no rank (A11) |
| System | host · engine + version · model · weights repo@rev · quant · "speculative decoding" when on (never the drafter's name, B6) |
| Config | the board's config string and arm flags (thinking, wall x2, wall-scaled turns, packs, diagnostics) as chips |
| SCORE | of the best configuration, `0.75 × SOLVE + 0.25 × LANG` |
| SOLVE · LANG | the same configuration; LANG over multi-language bases only (k > 1) in v5 |
| Daily | the same model's L0 (daily config) result beside its best, so a harness arm never passes for the shipped config |
| +assist | L2 next to L0 where a profile ran it, never ranked |
| Category-balanced | secondary readout, smaller type |
| Tokens · Σ wall | from the profile |
| Run | date, era (harness6), `--jobs` actually used, reasoning budget or "thinking off"; "single seed" until the seed arm (A10) |
| Source | the board run id (`GLM-C`, `OPUS-REF`, …) linked to the `docs/RESULTS.md` row at a pinned commit; withheld units footnoted (A12) |

Below the table: the scoring formula, difficulty weights (1 / 1.5 / 2),
"`budget_skip` units never score; a profile with skips is not seated", and the
MDE line from `evals/README.md`.

### 6.2 Charts

- **Board chart:** horizontal bars of L0 SCORE per system, with the
  `+assist` value as a tick on the same bar. One colour per host, not per
  model. Always paired with the table (charts are an illustration, the table
  is the record).
- **Coverage heatmap:** 12 categories × 7 languages, 71 units, empty cells
  visible. It shows honestly what v5.0 does and does not cover.
- **Per-category bars** per system, once there are at least two v5 rows.
- **The arm ladder (D9):** GLM's L0 → A → B → C → E as a step chart, the
  gold standard as a dashed line at 100% labelled "gold standard: Claude
  Opus 5.5, its own scaffold" (Q5). Each rung shows its count, SCORE and
  fraction of the gold standard; no trend line, no improvement percentages
  between rungs (A10).
- **Serving chart on `/hardware/` only:** single-stream vs 4-stream
  aggregate tok/s, prose vs code, for the pair. Never on the same chart as a
  capability score.

### 6.3 Placeholders while the run is in progress

- Mark every missing value in source as `[V5-PENDING: <what> — docs/RESULTS.md
  §<section>, row <slug>]`. Render it as a visible "PENDING" chip with that
  text in a tooltip and in screen-reader text.
- Mirror the paper guard: an e2e test that fails a **production** build if
  `V5-PENDING` appears in `out/`, and a `--allow-pending` preview mode for
  Max to review. Until the numbers land, ship the page with the chips only on
  a preview deploy (`deploy.sh prod <branch>` on the Mini, or a local build).
- Better than hand-copying: a `scripts/sync-results.mjs <openbeast>
  <commit>` that reads the v5 table from `docs/RESULTS.md` at a pinned commit
  and writes `src/app/benchmarks/results-build.json` with the revision, the
  same shape as `paper-build.json`. Every source link then uses that revision.

Placeholders needed today:

| Where | Placeholder | Lands in |
|---|---|---|
| `/benchmarks/` ladder, rung E | E's count and its flags | `docs/RESULTS.md` v5 board, after `v5_board.py --add GLM-E` |
| `/benchmarks/` board, beast rows | Qwen3.8 27B Uncensored MTP, Qwen3.6 27B | same |
| `/benchmarks/` board, DeepSeek v4.1 Flash | its best configuration | same, after its campaign |
| `/research/harness/` | the seed-noise floor; the integrity verdict on the withheld unit; the paper's status | `HARNESS_NOTES.md`, `exclusions.json`, then RESULTS |
| `/hardware/` §5, home evidence teaser | the `beast` host's v5 row | same |

Filled since the first draft (2026-11-17): the GLM rows `GLM-L0` to `GLM-C`
and `OPUS-REF`, so the Spark row, gate 03 for GLM and the investment-case
ladder can print them (S39).

### 6.4 What not to claim

- No v4-to-v5 deltas, no "improved from 98.7 to …".
- No model-versus-model sentence across hosts (rule A4).
- No ranking of rows the paired test cannot separate (rule A5).
- No "1M-token context" as a quality claim: it is the configured window, not
  a measured long-context result.
- No capability claim for the default MTP profile (only its non-MTP twin has
  a row) or for any weight-streaming profile until its row exists.
- No throughput comparison between `beast` and `spark-hydra`: different
  models, engines, drafters and stream counts.
- No "zero capability cost" for GLM Ablit (rule B3).
- Do not hide the run conditions: GLM's daily config runs thinking off and
  its best row thinking on at 2x wall (S40); Qwen runs a 20,480-token
  reasoning budget; a gate with the default in-flight cap clamps `--jobs`
  (say what was used).
- No "GLM matches/approaches Opus", no ranked Opus row (A11). The fraction
  of the gold standard is allowed (Q5) and only in A11's form: SCORE over the
  gold standard's SCORE, beside the absolute numbers, single seed.
- No "the harness doubles a model's score": one model, one seed (A10, A13).
- No 54/71 without its footnote (A12).
- No claim that thinking is free: about 2.3x B's time per unit on GLM.

---

## 7. Visuals

### 7.1 Photo shot list for Max

General: RAW, at least 4,000 px on the long edge, landscape 3:2 with a safe
1:1 crop in the middle. Match the existing green-machine look (dark room,
green accent light, no clutter). For each shot, check every screen, sticker
and reflection against rule C1 before sending.

| # | Shot | Angle and framing | Why | Used on |
|---|---|---|---|---|
| P1 | **The two Sparks, stacked or side by side** | Front three-quarter, low (lens at desk height), shallow depth of field, both power LEDs on | The hero image for spark-hydra; the site has no Spark photo | `/hardware/` hero, home plate, social card |
| P2 | **The ConnectX-7 link** | Macro on the back panel: both QSFP cables seated between the two units, cable labels readable | "Two boxes, one model" in one frame: the link is the story | `/hardware/` spark-hydra section |
| P3 | **The family shot** | Wide, eye level: the 5090 tower, the two Sparks, a MacBook, a phone, on one desk | Shows "two hosts plus clients" without a diagram | Home architecture section, `/clients/` |
| P4 | **The 5090 rig, refreshed** | Same angle as `green-machine` so it can replace it, side panel open | Current hardware; keeps continuity | Home plate, `/hardware/` beast |
| P5 | **Offline proof** | Router with its WAN cable unplugged and held in hand, rig or Sparks lit in the background, out of focus | The beast-island story in one image | `/offline/` hero |
| P6 | **Max with the hardware** | Waist up, seated at the desk, Sparks in frame, natural light from the side; a vertical 4:5 variant too | The About page has only a headshot | `/about/`, investors |
| P7 | **The ark drive** | Top-down on the desk: the cold-storage SSD with a handwritten "ARK" label, its cable coiled | beast-ark needs a physical object | `/offline/` |
| P8 | **A phone in hand** | Over-the-shoulder, the beast-chat console or the iOS app on screen with a benign session (blur nothing; choose a clean screen instead) | Clients are a main claim and have no image | `/clients/` |
| P9 | **The Sparks' top-down** | Straight overhead, both units square to the frame, on a plain dark surface | Clean cut-out for diagrams and the social card | Diagrams D1, D2, OG images |

Ask Max for P1, P2, P4, P5 first (P0 pages need them). P3, P6 to P9 can follow.
The new research pages (4.8, 4.9) need no photo: they lead with diagrams D9
and D10. P3 (the family shot) can carry the eval harness host (the Mac)
beside the Sparks for `/research/campaign/` if Max wants one image there.

### 7.2 Diagrams to make

All as static SVG in the repo (CSP allows `img-src 'self'` and inline
styles), with a text description beside each (section 9). Use the site's
existing palette; no real addresses (rule C1).

| # | Diagram | Shows | Page |
|---|---|---|---|
| D1 | **Two-host topology** | Clients (Mac, Linux, phone, browser) → tailnet → beast-gate `:8443` on each host → `beast` (llama.cpp, RTX 5090) and `spark-hydra` (TensorFold); tools on the client, inference on the host | Home, `/hardware/` |
| D2 | **Inside spark-hydra** | spark-1: rank 0, loopback API, command center; spark-2: rank 1; two CX-7 rails; speculative decoding; 4 streams into one 1M window | `/hardware/` |
| D3 | **A request through beast-gate** | device key → route allowlist → rate and in-flight caps → audit → engine; next to it, the human path `:3001` → RBAC → shard → audit | `/clients/`, `/security/` |
| D4 | **The offline ladder** | online → islanded (about 90–120 s) → air-gapped install → cold-storage ark; for each, what keeps working | `/offline/` |
| D5 | **Engine plane matrix** | engines × host classes, default marked, Maya gated at 1.5× | `/hardware/` |
| D6 | **Weight streaming** | experts in host RAM, hot experts in a VRAM cache, the 75% / 90% memory scopes | `/hardware/` |
| D7 | **How a v5 unit is proven** | task → prover arms (ref, wrong, empty, leak, slow, scale2, lint, toolchain) → adversarial rounds → shipped; then L0 / L1 / L2 arms | `/benchmarks/` |
| D8 | **v5 coverage heatmap** | 12 × 7 grid, 46 cells filled | `/benchmarks/` |
| D9 | **The arm ladder** | L0 → A → B → C → E as rungs, each labelled with its one change (runner v1 / runner v2 / wall-scaled turns / thinking / diagnostics + packs + env block + wall warnings) and its count (26, 31, 36, 53 with "54 raw" footnoted, E PENDING); the Opus gold standard as a dashed line at 71 (100%) labelled "gold standard, its own scaffold" (Q5), each rung with its fraction of it; "single seed" in the caption | `/benchmarks/`, `/research/harness/`, home teaser (compact) |
| D10 | **The harness loop** | one model turn in the middle; around it what the runner adds: turn 1 gets the task, the language packs and the environment block; each tool result comes back with diagnostics (and an edit hint); the runner's notices (turns left, wall clock at 50% / 80%, verify-done when the destination files are missing); the turn and wall budgets scaled to the unit; the sandbox boundary (task dirs in, harness source and eval cache out, the open integrity fix marked) | `/research/harness/` |

### 7.3 Screenshots to capture

Capture on a fresh browser profile, light content, 2× pixel density, then crop.
Redact per rule C1 by choosing clean screens, not by blurring.

| # | Screenshot | Where from | Page |
|---|---|---|---|
| C1 | Open WebUI chat with a tool call in flight (the README's own TODO: "the money shot") | `beast` or spark-1 WebUI | Home, `/clients/` |
| C2 | The `weather` tool answering "weather here" | WebUI | `/clients/` |
| C3 | OpenCode on a Mac, client mode, a tool call acting on a local file | Mac client | `/clients/` |
| C4 | beast-chat console on a phone: a live session with steer / pause / stop | phone | `/clients/` |
| C5 | iOS and Android apps, Chat and Agents tabs (only with Max's approval, rule C3) | simulators | `/clients/` |
| C6 | `healthcheck.sh` on spark-1: "All 9 services healthy" | terminal | `/hardware/` |
| C7 | `scripts/engine.sh list` and `status` | terminal | `/hardware/` |
| C8 | `python3 agents/island.py status` while islanded, and a `web_search` answering "ISLAND MODE" | terminal + WebUI | `/offline/` |
| C9 | `./scripts/ark.sh verify` on the drive | terminal | `/offline/` |
| C10 | `./scripts/docs.sh search NavigationStack` with the URL and fetch-date stamp | terminal | `/offline/` |
| C11 | `scripts/apps.sh check` showing READY and MISSING with its fix | terminal | `/clients/` |
| C12 | `openbeast-client status` (what `/api/slot` reports) | Mac | `/clients/` |
| C13 | `evals/profile.py` / `ablation.py` report for the Spark run, after it lands | terminal | `/benchmarks/` |
| C14 | beast-artifact gallery with a published page | browser | `/clients/` |
| C15 | `python3 evals/v5_board.py` output (the board as the script prints it) | terminal | `/benchmarks/` |
| C16 | `evals/campaign.py run … --dry-run` (commands and an ETA per arm) | terminal | `/research/campaign/` |
| C17 | Open WebUI Regenerate giving two different answers on the pair | WebUI | `/clients/` |

Terminal screenshots read better as styled text (like the current hero
terminal) than as images: copy the real output into a component, which is
also searchable and accessible. Use images only for GUIs (C1 to C5, C14).

A short screen recording (C1 as a 15-second loop) is a P2: it needs
`media-src 'self'` added to `scripts/generate-headers.mjs`, a poster image,
no autoplay with sound, and a reduced-motion fallback to the poster.

---

## 8. SEO and metadata

- **Titles and descriptions** per page, under 60 and 155 characters:
  - `/benchmarks/`: "Benchmarks | OpenBeast" — "Reproducible v5 results for OpenBeast systems: 71 proven units, three arms, every row tied to its host, engine and weights."
  - `/hardware/`: "Hardware | OpenBeast" — "An RTX 5090 rig and a two-DGX-Spark pair serving GLM-5.3-Flash with TensorFold: measured serving numbers and the engine plane."
  - `/offline/`: "Offline first | OpenBeast" — "Island mode, an air-gap install, offline engine updates, mirrored platform docs and a cold-storage package archive."
  - `/research/harness/`: "Harness engineering | OpenBeast" — "How a better agent loop and thinking took GLM-5.3-Flash from 26 to 53 of 71 v5 units on two DGX Sparks. Single seed; method and caveats."
  - `/research/campaign/`: "Model campaign | OpenBeast" — "Bring any model to the DGX Spark pair: onboard, profile, audit, then thinking-first harness arms, with an offline eval host."
  - `/clients/`, `/security/`, `/releases/` likewise.
- **Social cards:** one per page instead of one shared 777 KB PNG. Build
  them from SVG at 1200×630, under 200 KB each, with page-specific text
  (`openbeast-social-<page>.png`). Use P1 / P9 for hardware.
- **Structured data** (`layout.tsx`): add `softwareVersion` ("2.0.0" at the
  tag), `dateModified`, `operatingSystem` ("Linux; DGX OS"),
  `applicationCategory` ("DeveloperApplication"), and widen
  `runtimePlatform` (S13). Add `TechArticle` JSON-LD on `/hardware/`,
  `/offline/` and `/benchmarks/` with `datePublished` / `dateModified`.
  Do not add `AggregateRating` or `Review` markup.
- **Sitemap:** add the new URLs and a `lastModified` per entry.
- **`llms.txt`:** rewrite (section 5.6); keep it the plain, factual summary it
  is now. It is what assistants quote, so stale numbers there spread.
- **Search terms the pages should naturally use:** private AI workstation,
  local LLM, self-hosted AI, offline AI, DGX Spark, GLM-5.3-Flash, TensorFold,
  llama.cpp, tensor parallel, air-gapped install, uncensored local model.
  Write them into headings and first paragraphs; no keyword lists.
- **Canonicals** already exist per page with trailing slashes; keep the
  pattern for new pages.
- **GitHub repo description** (S38) and topics: add `dgx-spark`,
  `tensorfold`, `offline-first`.

---

## 9. Accessibility

Good already: skip link, one `<h1>` per page, `scope` on table headers, a
caption on the leaderboard, `aria-pressed` on the lens buttons, an
`aria-live` readout, a reduced-motion block, keyboard-scrollable table
regions with `tabIndex={0}`, and a lazy founder image with alt text.

To add:

- **Automated checks:** add `@axe-core/playwright` to the e2e suite and run
  it on every page in all three browsers; fail on serious and critical.
- **Status chips** (MEASURED, PENDING, …) already carry text; keep that, and
  never encode status in colour alone.
- **Every chart has its table** (section 6.2), and every SVG diagram is a
  `<figure>` with a `<figcaption>` and a text description (a `<details>`
  "Describe this diagram" under it).
- **Placeholders read aloud:** `[V5-PENDING]` chips include "result pending"
  in screen-reader text, not just a glyph.
- **Photos:** alt text says what is in the frame ("Two NVIDIA DGX Spark
  units side by side, linked by two cables at the back"), not marketing.
- **Contrast:** check the green-on-black small caps (`section-index`,
  `card-top`) against WCAG AA 4.5:1; several are 11–12 px.
- **Focus:** visible focus ring on the new nav items and the host toggle in
  `ArchitectureMap`.
- **Motion:** the architecture flow animation and terminal blink already stop
  under reduced motion; apply the same to any new animation.

---

## 10. Performance

Budgets per page, measured on a mid-range phone profile with Lighthouse in
CI (or a Playwright performance check): LCP under 2.0 s, CLS under 0.05,
total JS under 120 KB compressed outside `/research/<paper>`.

- **Images:** WebP at 720 and 1280 widths (the rig photo's pattern), with
  `width` / `height`, lazy below the fold, no preload (the e2e test already
  enforces this for the rig photo; extend it to new photos). An above-the-fold
  photo on `/hardware/` should be eager with `fetchpriority="high"`.
- **Social PNG:** 777 KB today; recompress (section 8).
- **Diagrams:** static SVG, optimized (SVGO), under 30 KB each.
- **Client JS:** keep the new pages server-rendered with no client component,
  except a host toggle if added to `ArchitectureMap`. Charts render as static
  SVG at build time, no chart library at runtime.
- **CSP shapes the choices:** `img-src 'self'` and `connect-src 'self'` mean
  no third-party embeds, fonts or analytics without changing
  `scripts/generate-headers.mjs` (its README section says how for Cloudflare
  Web Analytics). No `media-src` means no `<video>` until it is added.
- **Cache:** hashed `_next/static` assets are immutable; HTML is not cached
  by a "Cache Everything" rule (deploy README). Keep it that way so a fix to
  a stale number goes live on the next deploy.

---

## 11. Upstream doc fixes first

The site will link these openbeast docs at the v2.0.0 tag. They disagree with
the current state themselves; fix them in the openbeast repo before pinning:

| Doc | Line | Says | Should say |
|---|---|---|---|
| `docs/ARCHITECTURE.md` | 91 | "planned: two DGX Sparks, tensor-parallel" | **Fixed 2026-11-17** (notes PR): live since v1.9.0, links `SPARK_HYDRA.md` |
| `README.md` | 24–27, 410–444 | "Point the same stack at a vLLM or TensorFold server spanning two NVIDIA DGX Sparks" as a future; "Beyond one GPU" points at `DGX_SPARK_PLAN.md` | **Fixed in #182** for 410–444 ("Serving from DGX Sparks (spark-hydra)", Quickstart link); 24–27 reads as a capability, fine |
| `README.md` | 59 (beast-hydra row) | "Tested against simulated fleets, not yet on the Sparks" | true for hydra; add that the pair serves on its own |
| `README.md` | 6 | "frontier-class models" | rule A9 |
| `docs/MODELS.md` | 3 | "23 pre-configured models" | reconcile with the 28 serve scripts (which count, which are opt-in or internal) |
| `docs/RESULTS.md` | top | v5 "no model has a v5 row yet" | **Fixed in #193** (merged 2026-11-17): the v5 board generated by `evals/v5_board.py` (GLM L0-C, the Opus reference), drift-checked in CI |
| `README.md` | v5 paragraph | "v5 results are pending" | **Fixed in #193**: the README's "v5 leaderboard" section |
| `docs/MODEL_CAMPAIGN.md` | §8 | arm C "[PENDING]" | **Filled 2026-11-17** (notes PR): C 54/71 raw, 53/71 scored, the integrity caveat; D/E/F still [PENDING] |
| `docs/RESULTS.md` | v5 board | no rung E | `v5_board.py --add GLM-E …` when E finishes, then `--write` |
| `docs/TODO.md` or v2.0.0 notes | — | nothing on the 11-16 review totals | 209 found, 195 confirmed, all fixed (rule A6) |
| `docs/RELEASE_NOTES_v2.0.0.md` | — | does not exist | write it before the site's v2.0.0 copy, so the site can link it |

---

## 12. Checklist

### P0 — before or with the v2.0.0 tag

- [ ] Fix every stale claim S1–S38 (or remove the claim), each with its
      e2e assertion in the same commit.
- [ ] Upstream doc fixes in section 11, then pin all project links to the
      v2.0.0 tag commit.
- [ ] Record the 11-16 review totals in a repo doc (A6).
- [ ] Ask Max the open questions in [section 13](#13-questions-for-max)
      (S36 replication, S32 model count, C3 app names). Q5–Q10 are
      decided (2026-11-17).
- [ ] Add accuracy rules A (A1–A13), B, C to the website repo's `AGENTS.md`.
- [ ] Fix this plan's own stale copy S39–S46 before it reaches the site.
- [ ] Build `/benchmarks/` around the v5 story: the ladder (D9), the
      best-per-model board synced from `v5_board.py` through
      `scripts/sync-results.mjs`, every configuration per model,
      `[V5-PENDING]` chips for E and the Qwen rows, the placeholder guard
      test, and the v4 archive.
- [ ] Build `/research/harness/` (4.8) with D9 and D10 and the caveats box.
- [ ] Build `/hardware/` with the measured Spark serving table.
- [ ] Build `/offline/`.
- [ ] Update the home page: eyebrow, compatibility line, real terminal
      output, signal strip, two host plates, capability cards, evidence
      teaser, scale-out status, offline band, install roles.
- [ ] Update `ArchitectureMap` (19 tools, two engines, the Spark request
      path, the offline lens).
- [ ] Rewrite `public/llms.txt`; update JSON-LD and the sitemap.
- [ ] Photos P1, P2, P4, P5 from Max; diagrams D1, D2, D4, D9, D10.
- [ ] Update the GitHub repo description (S38).
- [ ] Deploy through `deploy.sh prod` after push; run the post-deploy `curl`
      checks from the website README.

### P1 — within two weeks of the tag

- [ ] Fill the remaining v5 numbers (GLM E, the seed arm, Qwen3.8,
      DeepSeek) from `docs/RESULTS.md` through `scripts/sync-results.mjs`;
      the guard test then passes in production.
- [ ] Re-sync the GLM-C footnote when the integrity review clears or voids
      the withheld unit (A12).
- [ ] Build `/research/campaign/` (4.9), `/clients/` (with the Regenerate
      fix), `/security/`, `/releases/`.
- [ ] Update `/investors/` (S2, S3, S5–S8, S14–S18, S23, S28, B6).
- [ ] Re-issue the investment case as a new dated edition; keep the
      2026-10-06 edition reachable.
- [ ] Research page: S36, S37, the v5 program card, the harness program
      card (S46).
- [ ] About page: S4, new journey entry, photo P6.
- [ ] Per-page social cards; recompress the shared one.
- [ ] Axe checks in e2e; contrast fixes for small labels.
- [ ] Diagrams D3, D5, D7, D8; screenshots C1–C4, C6–C12, C15–C17.
- [ ] Merge or close the open Dependabot branches.

### P2 — later

- [ ] `/v2/` launch page, retired into `/releases/` after a month.
- [ ] Re-checked, date-stamped comparison table with new rows.
- [ ] Diagram D6; photos P3, P7, P8, P9; screenshots C5, C13, C14.
- [ ] A 15-second screen recording of C1 (adds `media-src 'self'`).
- [ ] `TechArticle` JSON-LD on long pages; Lighthouse budget in CI.
- [ ] When hydra routes to the pair, update S11, the scale lens, and
      `/hardware/` "Not done yet".
- [ ] When the paper's last measurement lands, sync it (`sync-paper.mjs`
      refuses until the `[PENDING]` markers are gone).
- [ ] When the harness paper has a cross-model result, update
      `/research/harness/` §6 (Q7 decides whether a draft is shown).
- [ ] After the MTU 9000 restart and its A/B, update `/hardware/` "Not done
      yet" (S44) with the measured TTFT / tok/s, or drop the line.

---

## 13. Questions for Max

Open since the first draft:

- **Q1. Paper replication status (S36).** The research page says the
  capability replication is running; `TODO.md` has it parked in the GPU
  backlog since 2026-10-09. Parked, or back on?
- **Q2. Model count (S32).** `MODELS.md` says 23, `scripts/` holds 28 serve
  scripts. Which number does the site print, and which scripts count?
- **Q3. Native apps (C3).** May the iOS and Android apps be named "in
  development", and may screenshots appear?
- **Q4. Licence position for the Spark stack (B6).** Still open for investor
  or "supported deployment" copy: the Ablit weights and the recipe.

Decided:

- **DFlash2: DECIDED 2026-11-16.** Under the hood only; the site says
  "speculative decoding" and never names the drafter (B6).
- **Q5–Q10: DECIDED 2026-11-17** (below, each with what it changes).

Asked 2026-11-17, decided the same day:

- **Q5. The reference ceiling on the site. DECIDED (Max): the gold
  standard.** Claude Opus 5.5 is presented as the **gold standard** every
  local model is compared against: "how close local gets to frontier", and
  an indicator of what frontier can confidently delegate to local AI. It is
  still not ranked as a peer (different scaffold), but it is the yardstick:
  each local model's score is shown as a fraction of it.
  *Consequences:* rule A11 becomes "the gold standard, not a peer" and
  allows the fraction (SCORE over the gold standard's SCORE, beside the
  absolute numbers, single seed); §6.4 drops "no percentage of the
  ceiling"; `/benchmarks/` is framed around the gold standard (hero, ladder
  column, board column, per-language fractions, D9's dashed line at 100%);
  copy says "gold standard" where it said "reference ceiling". Numbers today
  (`docs/RESULTS.md` v5 board): L0 48.2%, A 56.9%, B 60.4%, C 78.2% of the
  gold standard's 100.0.
- **Q6. Ship the ladder now? DECIDED (Max): yes.** Ship L0 → C now with
  "single seed" on every number and E shown as pending (in words, not a
  `[V5-PENDING]` marker, so the production guard stays meaningful); update
  the page when arm E lands (`v5_board.py --add GLM-E`, then the site).
- **Q7. The harness paper in public. DECIDED (Max): very public, soon on
  the site.** The per-model vs unified harness paper gets its own paper page
  under `/research/` once a build exists (§4.8 item 6). Until then
  `/benchmarks/` and `/research/harness/` name the question and an IN
  PROGRESS chip, and say it will be published on the site.
- **Q8. 53 or 54. DECIDED: print 53/71 as the scored number**, with the
  footnote "54 raw; 1 unit withheld pending integrity review" (A12). Never
  54/71 alone.
- **Q9. Name DeepSeek v4.1 Flash as "next". DECIDED (Max): yes**, on
  `/benchmarks/` and `/research/campaign/`, as "next", never with a date or
  a predicted number.
- **Q10. The headline host. DECIDED: the DGX Spark pair (spark-hydra)
  leads.** It is live, it holds every v5 row, and DeepSeek v4.1 Flash goes
  there next. The 5090 rig (`beast`) stays a featured secondary host until
  it has v5 rows. *Consequences:* the home evidence teaser, `/benchmarks/`
  and `/hardware/` lead with spark-hydra; the home hero lede becomes "one
  GPU box or a pair of DGX Sparks" (§5.1); the rig plate stays, as the
  second host.
