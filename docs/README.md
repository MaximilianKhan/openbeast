# The OpenBeast docs: what to read for what

Every document in `docs/`, grouped by what you are trying to do. The
repository's front page is [`../README.md`](../README.md) (the product, the
install, the boards). Agent instructions for this repo are
[`../AGENTS.md`](../AGENTS.md). This page is the map.

**Current or historical.** Pages marked *plan* are design records: they say
why something was built, and parts of them describe states that no longer
hold. Where a plan and its operating doc disagree, the operating doc is
current. Anything in [`archive/`](archive/) is historical. Dated "Live
deployment" or "State on" lines at the top of a page say when it was last
checked against the machines.

## Before going offline (a prerequisite)

The offline reading order below works only if the Sparks were brought up to
date **while GitHub was still reachable**. Neither live checkout updates
itself, and both were behind `main` on 2026-11-18 (check on each Spark with
`git -C ~/openbeast fetch origin && git -C ~/openbeast log --oneline HEAD..origin/main | wc -l`).
The steps, with the exact commands, are
[SELF_DEVELOPMENT.md §0](SELF_DEVELOPMENT.md#0-before-going-offline-bring-both-sparks-to-main):

1. On spark-1 and spark-2: set aside the local copies in `~/openbeast` (a
   patch of the modified files, the untracked ones moved), then
   `git pull --ff-only`. A plain pull refuses while they are there.
2. On spark-1: install the Spark tools from the updated checkout with
   `~/openbeast/scripts/backends/spark-beast.sh install`. It copies
   `beast-spark` to `~/.local/bin`, `spark-deepseek-pack.sh` and
   `spark-deepseek-prepare-local.sh` to `~/openbeast-recipes/deepseek-prep/`,
   and adds the `beast*` aliases to `~/.bashrc` once. `beast-spark glm|ds`
   (aliases `beastglm` / `beastds`) is the only supported switch between the
   pair's two models: DeepSeek-V4.1-Flash (Mia EXL3), the default, and
   GLM-5.3-Flash, the alternative.
3. On spark-1: create the git hub
   ([SELF_DEVELOPMENT.md §11.2](SELF_DEVELOPMENT.md#112-one-time-setup)).

## Start here

| If you want to… | Read |
|---|---|
| know what OpenBeast is and install it on a GPU box | [`../README.md`](../README.md), then [INSTALL.md](INSTALL.md) |
| find the command for a job, on any machine | [COMMANDS.md](COMMANDS.md) |
| understand how the pieces connect | [ARCHITECTURE.md](ARCHITECTURE.md) |
| operate the two DGX Sparks (spark-hydra) | [SPARK_HYDRA.md](SPARK_HYDRA.md) (runbook), [OPENBEAST-SERVING.md](OPENBEAST-SERVING.md) (one-page serving note, also kept in `~/Documents` on the Sparks) |
| develop OpenBeast with the local model, network off | [SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md) |
| know what works when the internet is gone | [BEAST_ISLAND.md](BEAST_ISLAND.md) ("On the Spark pair"), [SPARK_HYDRA.md §H](SPARK_HYDRA.md#h-fully-offline-operation) |
| use a rig from a laptop | [BEAST_SLOT.md](BEAST_SLOT.md), [SPARK_HYDRA.md §F](SPARK_HYDRA.md#f-the-mac-client) |
| contribute: tests, lint, PRs while CI is off | [`../CONTRIBUTING.md`](../CONTRIBUTING.md), [SELF_DEVELOPMENT.md §3](SELF_DEVELOPMENT.md#3-tests-which-to-run-for-which-change) |

## A local model on the Sparks, with no internet: the reading order

1. [`../AGENTS.md`](../AGENTS.md): conventions, the skills, the tool surface.
2. [SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md): the clone, the client, the
   tests per file area, the era rule, git discipline, safety, worked
   sessions, and §11, the offline git loop through the hub on spark-1.
3. [COMMANDS.md](COMMANDS.md): the exact commands, per machine.
4. [ARCHITECTURE.md](ARCHITECTURE.md) §3: which machine runs what.
5. The component's own page from the tables below, and the script's `--help`.
6. When something is broken: [SPARK_HYDRA.md §G8](SPARK_HYDRA.md#g8-when-x-fails-troubleshooting),
   then [COMMANDS.md §14](COMMANDS.md#14-emergency-recovery).

Every document in this list is in the repository. Not everything they tell
you to run is: some of it lives only on the Sparks' disks, and is not
recreated by a clone.

| Only on spark-1 (not in the repository) | What |
|---|---|
| `~/openbeast-recipes/deepseek-prep/activate-stock.sh` | Activates the selected DeepSeek pack (canary, conformance, `openbeast.conf`, unit selection); `beast-spark ds` calls it |
| `~/openbeast-recipes/deepseek-prep/rollback/restore-glm.sh` (with `openbeast.conf.glm`, `glm-config.sha256`) | Back to GLM; `beast-spark glm` calls it |
| `~/openbeast-recipes/deepseek-prep/verify-chat.py` | The DeepSeek chat check |
| `~/.config/systemd/user/deepseek-v41-inference.service` | The DeepSeek unit file (GLM's `spark-hydra-inference.service` has a template in `scripts/backends/`) |
| `~/openbeast-recipes/deepseek-prep/runtime/`, `~/openbeast-recipes/glm53/` | The recipe runtimes, including `runtime/tools/bench.py` |
| `~/openbeast/openbeast.conf` | spark-1's command-center settings (`INFERENCE_MODEL`, `INFERENCE_THINKING`, `AGENT_PROFILE`, keys); the repository has only `openbeast.conf.example` |
| weights, rank caches, `~/.config/openbeast/` keys | [SPARK_HYDRA.md §0](SPARK_HYDRA.md#0-the-pair-at-a-glance) lists each with its path |

Their recovery path is a backup:
[SPARK_BACKUP_RESTORE.md](SPARK_BACKUP_RESTORE.md) (what one Spark holds,
backing it up, restoring it after an OS reinstall).

## Operating

| Doc | What's in it |
|---|---|
| [SPARK_HYDRA.md](SPARK_HYDRA.md) | The DGX Spark pair: the DeepSeek deployment and its packs, a quickstart, bare boxes to a serving pair, the command center, the Mac client, day-2 operations, troubleshooting, fully offline operation |
| [OPENBEAST-SERVING.md](OPENBEAST-SERVING.md) | Short serving note for the pair: check, start, stop, switch models and packs |
| [WEIGHTS_DOWNLOAD.md](WEIGHTS_DOWNLOAD.md) | Downloading any model from a Hugging Face URL, on any machine: `scripts/weights-download.sh URL [DEST]`, default `~/Documents/weights/ORG/REPO`, verified and resumable |
| [EXL3_PLAYBOOK.md](EXL3_PLAYBOOK.md) | Hosting another EXL3 model on the pair, offline: which cases are drag and drop (a DeepSeek pack), which are by hand, which need engine work |
| [BEAST_RECIPE.md](BEAST_RECIPE.md) | beast-recipe: any Hugging Face model + a Spark count to a verdict (SERVABLE-NOW / WITH-WORK / NEEDS-MORE-HARDWARE / BLOCKED), the fit, the engine, the gaps with effort, the recipe, and the apply steps (`scripts/beast-recipe.sh init / plan / engine / stage / up / check / adopt / rollback`); the engine and family matrix |
| [SPARK_BACKUP_RESTORE.md](SPARK_BACKUP_RESTORE.md) | Backing up the Spark pair and restoring it fully offline: the golden DGX OS image, per-node identity, the overlay, the offline rank-file rebuild, the LUKS key policy |
| [COMMANDS.md](COMMANDS.md) | Commands cheat sheet, grouped by machine and task |
| [INSTALL.md](INSTALL.md) | Installing the rig, per-model downloads, the air-gap path, troubleshooting |
| [UPDATING.md](UPDATING.md) | Updating every pulled-in component (`update.sh`; a maintainer tool, not the Spark update) |
| [ENGINES.md](ENGINES.md) | llama.cpp, vLLM, TensorFold, Maya through `scripts/engine.sh`: install, pin, swap, roll back, offline |
| [WEIGHT_STREAMING.md](WEIGHT_STREAMING.md) | The 5090 rig: MoE experts streamed from RAM, big mode, the engine gate |
| [HARDWARE_PROFILES.md](HARDWARE_PROFILES.md) | GPU detection and per-tier configs |
| [REMOTE_ACCESS_PLAN.md](REMOTE_ACCESS_PLAN.md) | Tailscale: design, VPN coexistence, verification |
| [TAILSCALE_OFFLINE.md](TAILSCALE_OFFLINE.md) | Pointer to the Tailscale field guide (`skills/tailscale/references/guide.md`): reaching the gate when the tailnet or WAN is down |
| [PYTHON_OFFLINE.md](PYTHON_OFFLINE.md) | Python packages on a Spark: venvs, wheelhouses, offline installs |
| [EGRESS_PRIVACY.md](EGRESS_PRIVACY.md) | Outbound traffic through a Tailscale exit node |
| [SANDBOXING.md](SANDBOXING.md) | The optional kernel sandbox for the bash tool |
| [LLAMACPP_WATCH.md](LLAMACPP_WATCH.md) | Upstream llama.cpp changes to plan for |
| [RUNBOOK-2026-09-08.md](RUNBOOK-2026-09-08.md) | A dated runbook from the v4 sweep (historical) |

## Developing

| Doc | What's in it |
|---|---|
| [SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md) | The local model developing OpenBeast: clients, offline tools, test map, era rule, git, safety, worked examples, the v5 suite offline, the offline git hub |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Trust model, service map, the deployed machines, project layout |
| [TOOLS.md](TOOLS.md) | Every tool a model can call: inventory, provenance, hardening, RBAC |
| [REFERENCE.md](REFERENCE.md) | Config keys, VRAM tables, per-variant details |
| [HARNESS_NOTES.md](HARNESS_NOTES.md) | Ideas for the agent harness, and what was measured |
| [TODO.md](TODO.md) | Roadmap, completed work, review records |

## The beast family (one page per feature)

| Doc | Feature |
|---|---|
| [TUTORIALS.md](TUTORIALS.md) | One walkthrough per feature |
| [FEATURES.md](FEATURES.md) | Capability breakdown through v1.5 |
| [BEAST_SLOT.md](BEAST_SLOT.md) | Client mode, `/api/slot`, beast-gate, device enrollment |
| [BEAST_CHAT.md](BEAST_CHAT.md) | beast-chat: the session console |
| [BEAST_ARTIFACT.md](BEAST_ARTIFACT.md) | beast-artifact: published pages |
| [BEAST_HYDRA.md](BEAST_HYDRA.md) | beast-hydra: routing inference across engines |
| [BEAST_INSTINCT.md](BEAST_INSTINCT.md) | beast-instinct: calibrated routing decisions |
| [BEAST_ISLAND.md](BEAST_ISLAND.md) | beast-island: runtime offline mode |
| [BEAST_DOCS.md](BEAST_DOCS.md) | beast-docs: platform docs mirrored offline |
| [BEAST_APPS.md](BEAST_APPS.md) | beast-apps: external apps through MCP servers |
| [BEAST_ARK.md](BEAST_ARK.md) | beast-ark: pinned open-source software on a drive |
| [BEAST_VIDEO.md](BEAST_VIDEO.md) | beast-video and beast-image (experiment): video with sound, and pictures, from a prompt on the 5090, in place of the LLM |
| [LANG_AWARENESS_PLAN.md](LANG_AWARENESS_PLAN.md) | beast-assist: push-diagnostics and awareness packs (*plan* with results) |
| [BEAST_LANG_PLAN.md](BEAST_LANG_PLAN.md) | beast-lang: the offline language library (*plan* with status) |
| [BEAST_CAMPAIGN_PLAN.md](BEAST_CAMPAIGN_PLAN.md) | beast-campaign: the GPU lease and the eval era (*plan*) |

## Models and evals

| Doc | What's in it |
|---|---|
| [MODELS.md](MODELS.md) | The model lineup with measured VRAM, context and speed |
| [RESULTS.md](RESULTS.md) | Eval leaderboards (v5, v4, v3.5) |
| [MODEL_CAMPAIGN.md](MODEL_CAMPAIGN.md) | A new model on the Sparks or the RTX 5090 rig (the Inkling and Qwen3.8 arms), from onboarding to the final report |
| [reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md](reports/V5_DEEPSEEK_CAMPAIGN-2026-11-20.md) | The v5 campaign of DeepSeek-V4.1-Flash (Mia EXL3), final: ten arms T to UPA_8k, T2 stays #1 and the default; the cap trade-off, integrity (tables from `evals/v5_report.py`) |
| [reports/V5_HY4_FLASH-2026-11-22.md](reports/V5_HY4_FLASH-2026-11-22.md) | Hy4-flash (NVFP4) on the Spark pair, final: H_T2 60/71 and the H_C ablation 55/71 |
| [reports/V5_GLM_UP-2026-11-23.md](reports/V5_GLM_UP-2026-11-23.md) | The GLM UP control: DeepSeek's arm UP on GLM-5.3-Flash, 56/71 (GLM's best row, #3); +2 paired over GLM-C across eras, 6 behind DeepSeek's UP on the same harness |
| [reports/V5_QWEN38_RTX5090-2026-11-22.md](reports/V5_QWEN38_RTX5090-2026-11-22.md) | Qwen3.8 27B Uncensored MTP on the RTX 5090: arm Q_T2 (T2 at one stream) 52/71 scored, the model's board row; the L0 baseline 39/71 scored; the profile's L1/L2 diagnostics (complete, not board rows) |
| [reports/V5_EVAL_HOST_CALIBRATION-2026-11-23.md](reports/V5_EVAL_HOST_CALIBRATION-2026-11-23.md) | The rig's eval-host container against the Mac harness host: DeepSeek T2 rerun 60/71 vs 65/71, no host effect except the Linux-only Rust `dlsym` validator bug on `122_gemm_blocked` (diagnostics, no board row) |
| [reports/GLM_SERVING_WINDOW-2026-11-23.md](reports/GLM_SERVING_WINDOW-2026-11-23.md) | The 2026-11-23 GLM serving window on the pair: the 1104 A/B (v1.8 stays), the DFlash2 re-pin to Apache-2.0 `6cc6c481`, and D2's first native serve (native TensorFold 1.2.10, TP2) with its measured start lines; addendum: what landed since |
| [reports/STRATA_INKLING_5090-2026-11-23.md](reports/STRATA_INKLING_5090-2026-11-23.md) | Strata vs llama.cpp for Inkling 2 Mini on the RTX 5090: the pre-run security review, the pinned source build, the results (Strata 1.43x single-stream, 1.5-2x prefill), the DSpark CUDA bug, where "4,087 tok/s" comes from (a 64-request aggregate); evaluated, not adopted |
| [reports/V5_INKLING2_MINI_RTX5090-2026-11-24.md](reports/V5_INKLING2_MINI_RTX5090-2026-11-24.md) | Inkling 2 Mini on Basalt, RTX 5090, arm I_T2: 49/71 scored (SCORE 76.5), 6 behind Q38U-T2 paired; 4.5 h for the arm; the #588 empty capped turns and the lenient parser. §10, the ablations: seed 1 51/71 (2 flips: the noise floor), Basalt's stock pack 53/71 (SCORE 78.2, the top 5090 row, level with Q38U-T2 paired), live decode and draft acceptance from the kept server logs; §10.9, I_UP8k (DeepSeek's UP_8k, harness10): 49/71, level with I_T2, its forced-act turns kept thinking on Basalt; T2 stays Inkling's setup; §12, the per-unit failure taxonomy of the four arms and the improved arm I_T3 (stock pack, wall-bound turns, a 16,384 cap, the #632 patch; running since 2026-11-24 16:29 PST) |
| [reports/V5_INKLING2_MINI_I_T3-2026-11-25.md](reports/V5_INKLING2_MINI_I_T3-2026-11-25.md) | Inkling 2 Mini's improved arm I_T3 (stock pack + 180 turns + a 16,384 cap + the #632 patch): 52/71 (SCORE 77.4, row INK-T3, listed), level with INK-T2-STOCK; 4 context overflows counted as fails (the runner does not compact on Basalt's wording); #632 removed every empty capped turn; twice the time; the default stays stock |
| [reports/DECISIONS-2026-11-25.md](reports/DECISIONS-2026-11-25.md) | Decision log for 2026-11-25: a context overflow is a model loss (Max), the ledger that enforces it, and I_T3 does not beat INK-T2-STOCK (the default stays stock; its follow-ups not queued), the rig arms rescanned, and §6 SC117's abliterated pack level with stock (the default stays stock; SC117 recommended over Derisked if an abliterated pack is wanted), and §7 Max makes SC117's pack the RTX 5090 default (the uncensored-preferred policy; the stock pack the rollback) |
| [reports/DECISIONS-2026-11-24.md](reports/DECISIONS-2026-11-24.md) | Decision log for 2026-11-24: Qwen3.8 stays the 5090 default after I_T2 and again after its ablations (with the switch rule for the improved arm), then Max makes the stock Inkling 2 Mini on Basalt the 5090 default (§10, superseding that rule; Qwen3.8 the fallback), INK-T2 ranked by SCORE, the website follows the board, seed replicates never rank (`replicate_of`), the stock pack's row and the next arm on it, the dlsym replays, the capped-turn counter and the Basalt log reader; §11 Basalt PR #632 as our local patch, §12 the improved arm I_T3, §13 UP_8k not adopted for Inkling (no forced-act arm on Basalt), §14 a listing of the answer tree counts as contamination |
| [reports/SC117_INKLING_STRATA-2026-11-25.md](reports/SC117_INKLING_STRATA-2026-11-25.md) | SC117's abliterated Inkling 2 Mini Strata packs (SQ-20 the largest): provenance and checksums (byte diff: only 20 BF16 `attn.wo_ud` tensors differ from the official pack), Strata v0.2.21 now returns Inkling thinking (unmeasured), the claims against our numbers, and the Phase 2 plan: the edit transplanted into a Basalt pack, arm `I_T2_sc117` |
| [reports/V5_INKLING2_MINI_SC117-2026-11-25.md](reports/V5_INKLING2_MINI_SC117-2026-11-25.md) | SC117's abliterated Inkling 2 Mini on Basalt (Path B): the stock pack rebuilt byte for byte by Basalt's converter, SC117's 20 `attn.wo_ud` matrices transplanted into the release (pack `6eaa50d3…`, offline recipe), same-day speed (identical) and KLD (0.0354, above Derisked), a refusal sanity probe, and arm I_T2_sc117: 51/71 (SCORE 75.3, row INK-T2-SC117), level with INK-T2-STOCK and Derisked; Max then made it the RTX 5090 default (DECISIONS-2026-11-25 §7) |
| [reports/MAYA_INKLING_5090-2026-11-23.md](reports/MAYA_INKLING_5090-2026-11-23.md) | Project Maya (`maya-ik2`) for Inkling 2 Mini on the RTX 5090: the read-only security review, which of our weights it reads (file:line), the offline build, the `--dns 127.0.0.1` DNS fix, and the measurement against Basalt (not adopted) |
| [reports/DEEPSEEK_TUNING-2026-11-23.md](reports/DEEPSEEK_TUNING-2026-11-23.md) | The DeepSeek tuning session on the pair: `PARALLEL=8` in production (147.4 tok/s aggregate at 8 streams against 116.5 at 4; ~20 tok/s per stream at 8 against ~31 at 4), the 1M window verified but deferred (0.88 GiB per rank on spark-1), `KV_SHARD=1`, the WebUI plan, the rollback |
| [reports/DECISIONS-2026-11-23.md](reports/DECISIONS-2026-11-23.md) | Decision log for 2026-11-23: each decision with its reason, evidence and the alternatives rejected (calibration, HG_T2, GLM 1104, D2, `qwen38-best`, the review, GLM-UP, the website progressions, Strata, the NVIDIA toolkit, Basalt for Inkling, the ablation queue, Maya, and §14 the DeepSeek tuning: `PARALLEL=8` live, 1M deferred) |
| [`../evals/README.md`](../evals/README.md) | The eval suite: schema, scoring, CLI, the v5 suite |
| [RESEARCH_FINDINGS.md](RESEARCH_FINDINGS.md) | MTP, profiling, model comparisons |
| [EVAL_FAST_SUITE_PROPOSAL.md](EVAL_FAST_SUITE_PROPOSAL.md) | The v4 fast suite (*plan*, built) |

## Plans and design records

[DGX_SPARK_PLAN.md](DGX_SPARK_PLAN.md) (the Spark design; SPARK_HYDRA.md is current) ·
[BEAST_HYDRA_PLAN.md](BEAST_HYDRA_PLAN.md) ·
[BEAST_INSTINCT_PLAN.md](BEAST_INSTINCT_PLAN.md) ·
[BEAST_ARTIFACT_PLAN.md](BEAST_ARTIFACT_PLAN.md) ·
[BEAST_CHAT_PLAN.md](BEAST_CHAT_PLAN.md) ·
[DISTRIBUTED_AGENTS_PLAN.md](DISTRIBUTED_AGENTS_PLAN.md) ·
[RBAC_PLAN.md](RBAC_PLAN.md) ·
[ROUTER_SIDECAR_PLAN.md](ROUTER_SIDECAR_PLAN.md) ·
[SKILLS_PLAN.md](SKILLS_PLAN.md) ·
[EXTERNAL_SKILLS_PLAN.md](EXTERNAL_SKILLS_PLAN.md) ·
[MAC_CLIENT_PLAN.md](MAC_CLIENT_PLAN.md) (superseded by BEAST_SLOT.md) ·
[WEBSITE_PLAN.md](WEBSITE_PLAN.md) (openbeast.ai) ·
[WEBSITE_UPDATE_2026-11-19.md](WEBSITE_UPDATE_2026-11-19.md) (openbeast.ai handoff: DeepSeek default, v5 board, model policy) ·
[WEBSITE_UPDATE_2026-11-20.md](WEBSITE_UPDATE_2026-11-20.md) (openbeast.ai update and upgrade on top of 11-19: DeepSeek ladder, seed noise, new-page proposals, llms.txt) ·
[WEBSITE_UPDATE_2026-11-21.md](WEBSITE_UPDATE_2026-11-21.md) (openbeast.ai delta on top of 11-20: the eight-arm DeepSeek ladder, what it taught, phrasing rules, chart spec, board-row gate) ·
[SOC2_READINESS.md](SOC2_READINESS.md) (self-assessment)

## Releases and reviews

Release notes: [v1.1.0](RELEASE_NOTES_v1.1.0.md) · [v1.3.0](RELEASE_NOTES_v1.3.0.md) ·
[v1.4.0](RELEASE_NOTES_v1.4.0.md) · [v1.5.0](RELEASE_NOTES_v1.5.0.md) ·
[v1.6.0](RELEASE_NOTES_v1.6.0.md) · [v1.7.0](RELEASE_NOTES_v1.7.0.md) ·
[v1.8.0](RELEASE_NOTES_v1.8.0.md) · [v1.9.0](RELEASE_NOTES_v1.9.0.md).
Dated reviews and research notes are in [`reviews/`](reviews/), dated
campaign reports in [`reports/`](reports/); superseded
plans are in [`archive/`](archive/). Both are records: read them for why
something is the way it is, not for current state.

| Dated review or research note (`reviews/`) | What it is |
|---|---|
| [TOOLS_SOTA_REVIEW-2026-09-10.md](reviews/TOOLS_SOTA_REVIEW-2026-09-10.md) | The tool suite against the state of the art: a ranked list of fixes and new tools |
| [BEAST_ASSIST_ROADMAP-2026-09-10.md](reviews/BEAST_ASSIST_ROADMAP-2026-09-10.md) | beast-assist: what could still move the score, and the stopping rule |
| [DISK-PRUNE-PLAN-2026-09-11.md](reviews/DISK-PRUNE-PLAN-2026-09-11.md) | A disk audit of the rig: what could be deleted or offloaded (nothing was deleted) |
| [AIRGAP-REVIEW-2026-09-15.md](reviews/AIRGAP-REVIEW-2026-09-15.md) | Air-gap readiness: features ranked for a closed network |
| [REVIEW-v140-findings-2026-09-15.md](reviews/REVIEW-v140-findings-2026-09-15.md), [REVIEW-v140-TRIAGE-2026-09-15.md](reviews/REVIEW-v140-TRIAGE-2026-09-15.md) | The adversarial review of beast-artifact and beast-chat at v1.4.0, and its triage |
| [HYDRA_PRIOR_ART-2026-09-30.md](reviews/HYDRA_PRIOR_ART-2026-09-30.md) | beast-hydra prior art: how other systems route inference across machines and engines |
| [INSTINCT_RESEARCH-2026-09-30.md](reviews/INSTINCT_RESEARCH-2026-09-30.md) | beast-instinct research: decision models and scoring |
| [DEEPSEEK_HARNESS-2026-10-09.md](reviews/DEEPSEEK_HARNESS-2026-10-09.md) | deepseek-ai's agent harness, evaluated: not integrated, ideas listed |
| [HARNESS_PRIOR_ART-2026-11-16.md](reviews/HARNESS_PRIOR_ART-2026-11-16.md) | How Factory's Droid lifts many models through its harness |
| [TTFT-rig-vs-spark-2026-11-16.md](reviews/TTFT-rig-vs-spark-2026-11-16.md) | Time to first token: the 5090 rig against the Spark pair |
| [WEIGHT_STREAMING_0XSERO-2026-11-17.md](reviews/WEIGHT_STREAMING_0XSERO-2026-11-17.md) | 0xSero's weight streaming, revisited for the 5090: what to trial |

| Archived (`archive/`, index in [archive/README.md](archive/README.md)) | What it was |
|---|---|
| [EVAL_REVIEW_2026-07-07.md](archive/EVAL_REVIEW_2026-07-07.md) | The double-pass review of the v3.5 eval suite that drove v4 |
| [EVAL_V4_PLAN.md](archive/EVAL_V4_PLAN.md) | The plan that turned v3.5 into v4 |
| [WORK_PLAN.md](archive/WORK_PLAN.md) | The v3 / v3.5 suite build log |
| [WEAK_SPOT_ASSESSMENT.md](archive/WEAK_SPOT_ASSESSMENT.md) | v3-era research: which eval axes to add |
| [MTP_PROFILING_PLAN.md](archive/MTP_PROFILING_PLAN.md) | MTP speed profiling: plan and config ledger |
| [IDENTITY_TOOLS_PLAN.md](archive/IDENTITY_TOOLS_PLAN.md) | The design of the identity-aware tool server |
| [PRODUCTION_ROADMAP.md](archive/PRODUCTION_ROADMAP.md) | The 2026-07-07 production review and roadmap |
| [TOOL_ARSENAL_RESEARCH.md](archive/TOOL_ARSENAL_RESEARCH.md) | Adopt-or-build research for the tool arsenal |

## Outside `docs/`

| Where | What |
|---|---|
| [`../skills/README.md`](../skills/README.md), `skills/<name>/SKILL.md` | The skills, and how to add one |
| [`../extensions/README.md`](../extensions/README.md) | Optional services |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Contribution rules, the local checks while CI is off |
| [`../SECURITY.md`](../SECURITY.md) | Reporting vulnerabilities |
| `../system-prompt.md`, `../system-prompt-tools.md` | The model's persona and tool guide. Never edited without Max |
| every script's `--help` | Kept current; the first thing to read before running a script |
