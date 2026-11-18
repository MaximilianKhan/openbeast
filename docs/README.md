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
2. On spark-1: install the Spark tools from the updated checkout:
   `install -m 755 ~/openbeast/scripts/backends/spark-beast.sh ~/.local/bin/beast-spark`
   (or `beast-spark install` once available), and copy
   `scripts/backends/spark-deepseek-pack.sh` and
   `scripts/backends/spark-deepseek-prepare-local.sh` into
   `~/openbeast-recipes/deepseek-prep/`. `beast-spark glm|ds` (aliases
   `beastglm` / `beastds`) is the only supported switch between the pair's
   two models.
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
| weights, rank caches, `~/.config/openbeast/` keys | [SPARK_HYDRA.md §0](SPARK_HYDRA.md#0-the-pair-at-a-glance) lists each with its path |

Their recovery path is a backup:
[SPARK_BACKUP_RESTORE.md](SPARK_BACKUP_RESTORE.md) (what one Spark holds,
backing it up, restoring it after an OS reinstall).

## Operating

| Doc | What's in it |
|---|---|
| [SPARK_HYDRA.md](SPARK_HYDRA.md) | The DGX Spark pair: the DeepSeek deployment and its packs, a quickstart, bare boxes to a serving pair, the command center, the Mac client, day-2 operations, troubleshooting, fully offline operation |
| [OPENBEAST-SERVING.md](OPENBEAST-SERVING.md) | Short serving note for the pair: check, start, stop, switch models and packs |
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
| [LANG_AWARENESS_PLAN.md](LANG_AWARENESS_PLAN.md) | beast-assist: push-diagnostics and awareness packs (*plan* with results) |
| [BEAST_LANG_PLAN.md](BEAST_LANG_PLAN.md) | beast-lang: the offline language library (*plan* with status) |
| [BEAST_CAMPAIGN_PLAN.md](BEAST_CAMPAIGN_PLAN.md) | beast-campaign: the GPU lease and the eval era (*plan*) |

## Models and evals

| Doc | What's in it |
|---|---|
| [MODELS.md](MODELS.md) | The model lineup with measured VRAM, context and speed |
| [RESULTS.md](RESULTS.md) | Eval leaderboards (v5, v4, v3.5) |
| [MODEL_CAMPAIGN.md](MODEL_CAMPAIGN.md) | A new model on the Sparks, from onboarding to the final report |
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
[SOC2_READINESS.md](SOC2_READINESS.md) (self-assessment)

## Releases and reviews

Release notes: [v1.1.0](RELEASE_NOTES_v1.1.0.md) · [v1.3.0](RELEASE_NOTES_v1.3.0.md) ·
[v1.4.0](RELEASE_NOTES_v1.4.0.md) · [v1.5.0](RELEASE_NOTES_v1.5.0.md) ·
[v1.6.0](RELEASE_NOTES_v1.6.0.md) · [v1.7.0](RELEASE_NOTES_v1.7.0.md) ·
[v1.8.0](RELEASE_NOTES_v1.8.0.md) · [v1.9.0](RELEASE_NOTES_v1.9.0.md).
Dated reviews and research notes are in [`reviews/`](reviews/); superseded
plans are in [`archive/`](archive/).

## Outside `docs/`

| Where | What |
|---|---|
| [`../skills/README.md`](../skills/README.md), `skills/<name>/SKILL.md` | The skills, and how to add one |
| [`../extensions/README.md`](../extensions/README.md) | Optional services |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Contribution rules, the local checks while CI is off |
| [`../SECURITY.md`](../SECURITY.md) | Reporting vulnerabilities |
| `../system-prompt.md`, `../system-prompt-tools.md` | The model's persona and tool guide. Never edited without Max |
| every script's `--help` | Kept current; the first thing to read before running a script |
