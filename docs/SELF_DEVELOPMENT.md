# Self-development: OpenBeast's own model working on OpenBeast

The Spark pair serves stock DeepSeek-V4.1-Flash-1017. OpenCode on the Mac reached it through beast-gate on November 17. The self-development exercises and offline task transcripts below were measured with GLM-5.3-Flash on the pair on November 15. They document the workflow and the results of that deployment.

The launch and operations runbook for the pair is
[`SPARK_HYDRA.md`](SPARK_HYDRA.md). This page assumes the pair is up
(`healthcheck.sh`: "All 9 services healthy").

| Section | |
|---|---|
| [1. Setup](#1-setup) | a clone, which client, which skills |
| [2. What works with the network off](#2-what-works-with-the-network-off) | tools, refusals, mirrors, docs |
| [3. Tests: which to run for which change](#3-tests-which-to-run-for-which-change) | file area to test map, GNU-only suites, the right python |
| [4. The eval-era rule](#4-the-eval-era-rule) | the hashed files, and why touching them matters |
| [5. Git discipline](#5-git-discipline) | branches, atomic commits, messages |
| [6. Safety](#6-safety) | secrets, overwrites, live boxes, remote quoting |
| [7. How Claude works on this repo](#7-how-claude-works-on-this-repo) | the loop, as copy-pasteable recipes |
| [8. Worked example sessions](#8-worked-example-sessions) | ten prompts, the commands, what "done" looks like |
| [9. Proof: a real run on spark-1](#9-proof-a-real-run-on-spark-1-2026-11-15) | what happened, and the lessons folded in above |
| [10. The v5 eval suite, offline](#10-the-v5-eval-suite-offline-running-it-and-authoring-for-it) | running a profile, reading a failure, authoring and proving a task |

---

## 1. Setup

### 1.1 Work in a clone, never in a live checkout

`~/openbeast` on spark-1 **is the running command center**. `start.sh`, the
units and the watchdog run from it, and its `openbeast.conf` and `.run/` hold
live secrets and state. Never let a model edit it. Give the model a scratch
clone:

```bash
# on spark-1 (or spark-2, which runs no OpenBeast services)
git clone ~/openbeast ~/ob-work          # a local clone: no network needed
cd ~/ob-work && git switch -c fix/<topic>
```

```bash
# on the Mac
git clone ~/Documents/projects/openbeast ~/ob-work && cd ~/ob-work && git switch -c fix/<topic>
```

A local clone has no `openbeast.conf` (it is gitignored), so it cannot
accidentally start a stack or read keys. Push from the clone to the real remote
when there is network, or bring the branch home with `git bundle` when there is
not ([5](#5-git-discipline)).

### 1.2 Which client

| Client | Where tools run | Model | Best for |
|---|---|---|---|
| **`./agent.sh` on spark-1** (measured) | spark-1, in `-w <clone>` | The active Spark model on `127.0.0.1:8000`, read from the **live** checkout's `openbeast.conf` | unattended tasks with a clear end state; works with the internet off, since only the LAN or tailnet ssh is needed to start it |
| **opencode on the Mac, client mode** (measured as the daily setup) | the Mac, in the clone | The active Spark model through beast-gate `https://spark-1.tail4109f9.ts.net:8443/v1` with the Mac's device key | interactive work; `skill()`, `language_reference`, `platform_docs` and the other MCP tools are all there |
| **`start_agent` from a WebUI or opencode chat** | where that MCP server runs | the configured `OPENBEAST_AGENT_INFERENCE_URL` | delegating a long sub-task while you keep talking |
| opencode **on spark-1** | — | — | **not set up**: opencode is not installed on spark-1, and the repo's `opencode.json` points at `localhost:8080` (llama.cpp), not `:8000`. Use `agent.sh` there |

**`agent.sh`: run the live checkout's script, point it at the clone.** The
clone's own `agent.sh` would find no `openbeast.conf` and dial the llama.cpp
default `http://localhost:8080/v1`, where nothing serves on a Spark. The live
`~/openbeast/agent.sh` sources the live conf. That gives it
`OPENBEAST_AGENT_INFERENCE_URL=http://127.0.0.1:8000/v1` and
`OPENBEAST_INFERENCE_MODEL=DeepSeek-V4.1-Flash-1017`. All file and shell work then
happens in `-w`:

```bash
ssh spark-1.tail4109f9.ts.net
cd ~/openbeast && ./agent.sh -w ~/ob-work --max-iter 40 \
  --log-file ~/agent-logs/$(date +%Y%m%d-%H%M)-topic.jsonl "<task>"
```

`--log-file` outside both trees keeps the transcript (JSONL: `start`,
`iteration`, `tool_call`, `assistant`, `max_iterations` / done events) out of
`git status`.

A lone UTF-16 surrogate in model output (a `\ud800` JSON escape with no
partner) shows as `?`: the runner prints with `errors="replace"`, and when the
history cannot be encoded for the next request it replaces each surrogate
once, logs a `surrogates_scrubbed` event and retries at once. It does not
count as an API error. The router does the same for the task it classified
and for its own replies (`tests/test_runner_surrogates.py`).

**opencode on the Mac** ([SPARK_HYDRA.md F](SPARK_HYDRA.md#f-the-mac-client)):
run `opencode` in the clone and pick an `openbeast-rig/…` model. The repo's own
`opencode.json` is era-hashed: never edit it to point somewhere else. The
global config (`~/.config/opencode/opencode.json`, opencode v2) holds the rig
provider and `mcp.servers.openbeast-tools`.

### 1.3 Which skills to load

In opencode (or WebUI), the model calls `skill()` for the index and
`skill("<name>")` to load one. The runner behind `agent.sh` has no `skill`
tool (its tools are `bash`, `read_file`, `write_file`, `edit_file`,
`list_files`, `grep`, `web_search`, `fetch`, `update_plan`, `task_done`). So
hand it the skill text as context:

```bash
cat skills/codebase-onboarding/SKILL.md skills/git-discipline/SKILL.md > /tmp/ctx.md   # one --context-file only
./agent.sh -w ~/ob-work --context-file /tmp/ctx.md --max-iter 40 "<task>"
```

| Skill | Load it when |
|---|---|
| `codebase-onboarding` | the first task of a session in this repo |
| `git-discipline` | any task that ends in a commit |
| `test-driven-development` | a new behaviour, or a bug that needs a regression test |
| `debugging-methodology` | the obvious fix did not work; a flaky or environment-dependent failure |
| `code-review` | reviewing a branch before merge, or after merging parallel branches |
| `beast-lang` | touching `agents/lang/`, packs, claims, or unsure whether a zig 0.16 / C++ / Python API exists |
| `platform-docs` | writing against Apple / Android APIs (backed by `platform_docs` / `scripts/docs.sh`) |
| `app-control` | the task needs Xcode, Gradle/adb, Blender, a printer or ROS (`scripts/apps.sh check <cap>` first) |
| `eval-task-author`, `eval-variant-porter` | adding eval tasks or language variants |
| `verification-before-completion` (imported) | before claiming "done" |

---

## 2. What works with the network off

| Works offline | Notes |
|---|---|
| the model, through every client above | inference is local to the pair |
| `bash`, `read_file`, `write_file`, `edit_file`, `list_files`, `grep`, `update_plan`, `task_done` | all local |
| `skill()` / `skill(name)` | the skills are in the repo |
| `language_reference` | the installed toolchain's verified claims. On a Spark only gcc/g++ are installed today (no zig, rust or go) |
| `platform_docs`, `scripts/docs.sh search/read` | only what is mirrored. **spark-1 has no mirror yet**; carry one ([SPARK_HYDRA.md H8](SPARK_HYDRA.md#h8-beast-docs-mirror)) |
| the docs in the repo | `docs/*.md`, `AGENTS.md`, `skills/*/SKILL.md`, `evals/README.md`, every script's `--help` |
| beast-ark: `scripts/ark.sh list / plan / lint / verify / restore / serve` | the catalog is in the repo. A filled ark drive restores toolchains (go, zig, rust, node, cpython, …), images and source with no network ([BEAST_ARK.md](BEAST_ARK.md) §8 and §13). `fetch`, `lock` and `update` exit 3 offline, which is correct. Never invent a pin: add an entry as `verified = "unverified"` and leave `lock` to a connected session |
| git (local) | commit, branch, diff, log, `git bundle` |
| the hermetic test suites | stubs, no network ([3](#3-tests-which-to-run-for-which-change)) |

| Does not work offline | What the model sees |
|---|---|
| `web_search`, `fetch` through the **MCP** server (opencode, WebUI `:3001`) | the island refusal at once, *"… ISLAND MODE … do not retry"*, once the island watcher has islanded (`ISLAND=auto`, after 90 s) or the stack was started `--offline` |
| `web_search`, `fetch` in **`agent.sh` / `start_agent`** | **no island guard.** `agents/tools.py` is era-hashed, so the guard lives only in `mcp_server.py` (BEAST_ISLAND "Known limits"). `web_search` fails after up to 15 s, and `fetch` after its DNS timeout plus 45 s. Tell the model in the prompt not to use them, and disable them for the process ([9](#9-proof-a-real-run-on-spark-1-2026-11-15)) |
| `pip install` from an index, `npm`, `apt`, `docker pull`, Hub downloads, `git pull` from GitHub | errors. Under `OFFLINE=true` the OpenBeast scripts refuse up front and say what to stage |

**Disabling web tools for one `agent.sh` run** without touching the live stack's
island state. These are process-local environment variables; the live services
never see them:

```bash
cd ~/openbeast && env OPENBEAST_OFFLINE=true \
  SEARXNG_URL=http://127.0.0.1:9 \
  http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 \
  no_proxy=127.0.0.1,localhost NO_PROXY=127.0.0.1,localhost \
  ./agent.sh -w ~/ob-work --max-iter 40 "<task — and say: the network is off, do not call web_search or fetch>"
```

- `SEARXNG_URL` points `web_search` at a closed port.
- `fetch` honours the proxy environment (`_build_fetch_opener`), so it fails
  fast at a closed local proxy.
- `no_proxy` keeps the model's own `127.0.0.1:8000` direct.
- `OPENBEAST_OFFLINE=true` makes `conf.sh` treat the box as offline for install
  steps.

To island the **whole stack** instead (a real network-off test), use
`./start.sh restart --offline` on spark-1. That is a live-service change, so
make it Max's call.

---

## 3. Tests: which to run for which change

### 3.1 The right python

pytest is **not** in `agents/requirements.lock`. On 2026-11-15 neither
spark-1's `/usr/bin/python3` (3.12.3) nor the Mac's default `python3` (an
Anaconda 3.9, too old for `run_eval.py`) had it. Use a test venv with both
hash-pinned closures. This was verified on the Mac with python 3.12, where 95
tests passed:

```bash
python3.12 -m venv ~/.venvs/openbeast-test                        # on a Spark: /usr/bin/python3 -m venv …
~/.venvs/openbeast-test/bin/python -m pip install -q --require-hashes -r agents/requirements.lock
~/.venvs/openbeast-test/bin/python -m pip install -q --require-hashes --no-deps -r .github/ci-requirements/test.txt
source ~/.venvs/openbeast-test/bin/activate                       # shell suites call `python3`: it must be this one
python3 -m pytest tests/test_island.py -q
```

Offline, build the venv while online and keep it, or carry a wheelhouse
([SPARK_HYDRA.md H7](SPARK_HYDRA.md#h7-test-and-language-toolchains)).
`tests/run_tests.sh` refuses to report success without pytest. It sets
`OPENBEAST_SKIP_NETWORK_TESTS=1` and points the tool audit log at a temp file.

### 3.2 GNU-only suites run on a Spark

The shell suites are written for GNU coreutils / Linux. `tests/test_engines.sh`
says so in its header (it also needs OpenSSL ≥ 3). On the Mac on 2026-11-15,
`tests/test_skill_import.sh` showed 5 BSD-only failures that do not happen on
spark-1. Run shell suites on a Spark, in a clone, niced. spark-2 is the better
host: it runs no OpenBeast services and had ~11 GiB available, against spark-1's
~9.7.

```bash
ssh spark-2.tail4109f9.ts.net 'git clone -q ~/openbeast ~/ob-test && cd ~/ob-test && nice -n 19 bash tests/test_spark_guard.sh | tail -3'
ssh spark-2.tail4109f9.ts.net 'rm -rf ~/ob-test'          # clean up
```

Measured on spark-2 on 2026-11-15, at this branch's head, in a clone: see
[9.3](#93-gnu-suites-on-spark-2).

### 3.3 File area to tests

Run the narrow suites while iterating, then the full set before committing:
`tests/run_tests.sh` (everything, as CI does) or at least
`bash tests/test_scripts.sh` + `python3 -m pytest tests/ -q --ignore=tests/test_apps.py`.

| You changed | Run |
|---|---|
| any `scripts/*.sh`, `start.sh`, `stop.sh`, `bootstrap.sh` | `tests/test_scripts.sh` (syntax, path references, ShellCheck-level checks; also runs `test_lifecycle.sh`, `test_uninstall.sh`) |
| `start.sh` / `stop.sh` supervision, scopes, watchdog | `tests/test_lifecycle.sh`, `tests/test_health_recovery.py`, `tests/test_proc_hygiene.py` |
| `scripts/lib/conf.sh`, `openbeast.conf` keys | `tests/test_conf_secrets.sh`, `tests/test_escalate_conf.sh`, `tests/test_shell_ops.sh` |
| `scripts/healthcheck.sh`, `scripts/doctor.sh`, `setup-tailscale.sh` | `tests/test_shell_ops.sh`, `tests/test_operator_scripts.sh`, `tests/test_backends.sh` |
| `scripts/engine.sh`, `scripts/engines/*`, `scripts/lib/engine.sh`, `engine-serve.sh` | `tests/test_engines.sh` (GNU), `tests/test_lifecycle.sh` |
| `scripts/serve-*.sh` flags, `scripts/llama.cpp.ref`, `scripts/lib/memscope.sh`, `scripts/engine-gate.sh`, `scripts/tokps.py`, `scripts/engines/maya*` | `tests/test_weight_streaming.sh` (portable; every serve-script flag against the pinned llama.cpp's option list), `tests/test_tokps.py`, `tests/test_engines.sh` (GNU, Maya install/stage/gate), `tests/test_lifecycle.sh` (the big-mode scope through `start.sh -d`). Runbook: [WEIGHT_STREAMING.md](WEIGHT_STREAMING.md) |
| `scripts/backends/spark-cluster.sh`, `tensorfold/spark-node.sh`, `vllm/` | `tests/test_spark_cluster.sh`, `tests/test_backends.sh` |
| `scripts/backends/spark-guard.sh` | `tests/test_spark_guard.sh` |
| `scripts/backends/spark-deepseek-pack.sh` | `tests/test_spark_deepseek_pack.sh` |
| `scripts/backends/spark-backup.sh`, `spark-restore.sh`, `pylib/spark_backup.py`, `data/spark-backup.items` | `tests/test_spark_backup.sh` (the copy steps need GNU rsync >= 3: Linux, or `brew install rsync` on a Mac; without it they are skipped) |
| `scripts/backends/spark-model-intake.sh`, `pylib/model_intake.py`, `data/spark-families.json` | `tests/test_spark_model_intake.sh` (portable; the local-revision parity check needs GNU stat), `tests/test_model_inspect.py` |
| `scripts/backends/spark-beast.sh` | `tests/test_spark_beast.sh` |
| `scripts/backends/spark-fabric-mtu.sh` (fabric MTU 9000, netplan drop-in) | `tests/test_spark_fabric_mtu.sh` (portable; on Ubuntu it also checks the merge against the real `netplan generate --root-dir`) |
| `spark-root-setup.sh`, `tensorfold/install-native.sh` | `tests/test_spark_supply.sh` |
| `scripts/backends/model-*.sh`, `pylib/`, profiles, locks | `tests/test_model_profiles.py`, `test_model_inspect.py`, `test_model_fetch.py`, `test_conformance.py`, `test_use_model.py` |
| `scripts/clients.sh`, `agents/edge.py` (beast-gate) | `tests/test_clients.sh`, `tests/test_edge.py`, `tests/test_edge_review1009.py` |
| `scripts/setup-client.sh`, `scripts/client.sh` | `tests/test_setup_client.sh` |
| `scripts/bundle.sh`, `pydeps.sh`, `fetch-weight.sh`, locks | `tests/test_offline_fixes.sh`, `tests/test_supply_chain.sh`, `tests/test_bundle_manifest.py`, `tests/test_pydeps_lock.py` |
| `agents/mcp_server.py` | `tests/test_mcp_allowlist.py`, `test_tool_server_startup.py`, `test_island.py`, `test_platform_docs_tool.py`, `test_artifact_mcp_tools.py`, `test_lang_reference_tool.py` |
| `agents/island.py` | `tests/test_island.py` |
| `agents/openapi_tools.py` (the `:3001` identity tool server) | `tests/test_identity_server.py`, `test_identity_jwt.py`, `test_toolserver_host_review1009.py` |
| `agents/tools.py`, `agents/runner.py` (**era-hashed**, [4](#4-the-eval-era-rule)) | `tests/test_tools.py`, `test_runner_*.py`, `test_tool_*.py`, `test_fetch_guards.py`, `test_path_guard.py` |
| `agents/apps/`, `scripts/apps.sh` (beast-apps) | `tests/test_apps.py` (its own step; the full run ignores it) |
| `agents/docs/`, `scripts/docs.sh` (beast-docs) | `tests/test_beast_docs.py`, `tests/test_platform_docs_tool.py` |
| `agents/ark/`, `agents/ark/catalog.toml`, `scripts/ark.sh` (beast-ark) | `tests/test_ark.py` (it lints the real catalog, checks repo-pin parity and runs the fake-server round trips), `tests/test_ark.sh` (CLI exit codes, the bare-box `./ark` wrapper) |
| `agents/lang/`, `agents/packs/`, `lang-*.sh` | `tests/test_lang_*.py`, `test_pack_context.py`, `test_zig_pack.py`, `test_escalation_wiring.py` |
| `agents/chat_server.py`, `sessions.py`, `job.sh` | `tests/test_chat_*.py`, `test_sessions.py`, `test_steering.py`, `test_job_sh.sh`, `test_ops_chat_artifact.sh` |
| `agents/artifact*.py`, `scripts/artifact.sh` | `tests/test_artifact_*.py`, `tests/test_artifact_cli.sh` |
| `agents/hydra*.py`, `scripts/hydra*.sh` | `tests/test_hydra_*.py`, `test_hydra_*.sh` |
| `agents/instinct/`, `agents/router.py` | `tests/test_instinct_*.py`, `test_router*.py` |
| `agents/weather.py` | `tests/test_weather.py` |
| `extensions/dashboard` (`/api/slot`) | `tests/test_beast_slot.py`, `tests/test_dashboard_review1009.py` |
| `skills/`, `scripts/skill-import.sh`, `IN_HOUSE_SKILLS.txt` | `tests/test_skill_import.sh` (GNU) |
| `evals/` | `tests/test_scoring.py`, `test_suite_stats.py`, `test_fast_suite.py`, `test_eval_*.py`, `test_cache.py`, `test_benchmark_lifecycle.py`, `test_board_honesty.py`, `test_leaderboard_eligibility.py`, plus `python3 evals/suite_stats.py --check` |
| `.github/workflows/*` or a new named test step | `tests/test_ci_workflow.py` |
| docs that cite eval counts | `python3 evals/suite_stats.py --check` (exit 1 on drift) |
| a live surface (only read-only, only on the live box) | `scripts/healthcheck.sh`, `scripts/backends/conformance.sh --model DeepSeek-V4.1-Flash-1017`, `tests/test_smoke.sh` (needs a live stack) |

---

## 4. The eval-era rule

`evals/cache.py` hashes these files into the **eval era**. Every cached and
reported eval result is keyed by it:

```
system-prompt.md   agents/runner.py   agents/tools.py
evals/SUITE_VERSION   evals/HARNESS_VERSION   evals/vlib.py
```

`evals/HARNESS_VERSION` is a pin, not code: `harness7 <fingerprint>`, where
the fingerprint is the AST of run_eval's agent-shaping and judging code
(`cache.HARNESS_FUNCTIONS` / `HARNESS_CONSTANTS`: the agent's argv, env and
wall cap, fixture setup, the validator, its bootstrap and private build
cache, the leftover sweep, and the toolchain pins). `tests/test_cache.py` fails when the code
and the pin disagree. `evals/vlib.py` is hashed whole, because run_eval
injects it into every validator.

`./scripts/eval-era.sh` prints the era, `--files` shows each file's hash, and
`--check <hash>` exits 1 if it moved.

- **Why it matters.** Rows measured on either side of a change to any of these
  files are **not comparable**. A one-character edit to `tools.py` invalidates
  every cached result and splits a campaign mid-way
  (BEAST_CAMPAIGN_PLAN §1). This is why the island guard is in
  `mcp_server.py`, and why fixes to the runner's web tools wait for "the next
  deliberate era roll".
- **The rule.** Do not edit any of them without Max. **`system-prompt.md`
  and `system-prompt-tools.md` are never edited without Max, full stop.** If a
  fix needs one of them, stop. Write the change up (the file, the diff, the
  reason), and put it in the branch's notes or `docs/TODO.md` instead. A
  change to run_eval's fingerprinted functions needs a new `HARNESS_VERSION`
  pin, which is the same decision.
- **Check before committing:**

  ```bash
  git diff --name-only main... | grep -E '^(system-prompt(-tools)?\.md|agents/(runner|tools)\.py|evals/(SUITE_VERSION|HARNESS_VERSION|vlib\.py))$' \
    && echo "ERA FILE TOUCHED — stop and ask Max"
  ```

- **v5.0 is the current suite** (`evals/SUITE_VERSION` = `v5`, harness
  `harness7`, the manifest `evals/suites/v5.json`). `SUITE_VERSION` and the
  manifest move only when Max rolls the suite; v5.1 is a backlog of twelve
  families, not something to add units to ([10](#10-the-v5-eval-suite-offline-running-it-and-authoring-for-it)).

---

## 5. Git discipline

The `git-discipline` skill is the long form. In short:

- **Branch first.** `git switch -c fix/<topic>` (or `docs/`, `feat/`). Never
  commit to `main` or to a shared integration branch such as
  `feat/spark-scale-1-8` directly. Integration happens by merge.
- **Atomic commits.** One logical change per commit: the fix, its test and its
  doc line together. Two unrelated fixes are two commits.
- **Stage deliberately.** `git add <paths>`, never `git add .` / `-A`. Read
  `git diff --cached` before every commit.
- **Messages.** The subject is imperative and ≤ 72 chars ("Make X do Y", "docs:
  …", "<component>: …"). The body says **why**: what was wrong, what it broke,
  why this fix is at the root. End it with the attribution line the session
  asks for.
- **Never** amend, rebase or force-push a commit that is already pushed to a
  shared branch. Never `--no-verify`.
- **No network?** Bring a branch home as a bundle:
  `git bundle create /tmp/topic.bundle main..fix/<topic>`, then
  `git fetch /tmp/topic.bundle fix/<topic>:fix/<topic>` where the remote is
  reachable.
- **Never share the stash.** Other sessions share it. Use a WIP commit on your
  branch instead of `git stash`.

---

## 6. Safety

- **Never print secrets.** Do not `cat openbeast.conf`, key files,
  `~/.config/openbeast/*`, `.run/*.token` or `clients.json`. To show config,
  print key names or filter: `grep -vE 'KEY|TOKEN|PASS|SECRET' openbeast.conf`.
  Never put a secret on argv (it shows in `ps`); scripts take `--api-key-stdin`
  or a `*_FILE`.
- **Read before overwriting.** `read_file` (or `sed -n`) before `write_file`
  on an existing file. Prefer `edit_file` for changes.
- **Back up configs before editing them**:
  `cp X X.bak-$(date +%Y%m%d%H%M)`. This covers `openbeast.conf`, `local.sh`,
  `opencode.json` and `spark.env`. Keep the backup 0600 if the file is.
- **Dry-run first.** `--dry-run` exists on `engine.sh use/install/update/rollback`,
  `spark-cluster.sh`, `apps.sh enable/disable`; `--print` on `spark-node.sh`;
  `--render` on `spark-guard.sh`; `--dry-run` on `spark-fabric-mtu.sh`;
  `--preflight` on `bootstrap.sh`;
  `setup-tailscale.sh --status`.
- **Live boxes are read-only.** On spark-1 / spark-2 outside a clone, only
  probes: `healthcheck.sh`, `curl …/health`, `systemctl --user status`,
  `journalctl`, `docker ps`, `git status`, `spark-guard.sh --status`,
  `spark-fabric-mtu.sh --status`. Do not
  restart, stop, `docker` anything, rewrite a unit, or change island state
  unless that is the task and Max said so. The pair has ~10 GiB of headroom on
  spark-1: no big builds there.
- **Quote remote ssh commands.** In `ssh host cat ~/x`, the `~` expands
  **locally**. Write `ssh host 'cat ~/x'`. The same goes for `$VAR`, globs,
  `&&` and pipes meant for the remote side.
- **Scope the task.** Name the files the model may touch, and say "do not
  commit / push" when you will review first.

---

## 7. How Claude works on this repo

These are the habits that make changes land cleanly here, written as recipes
the local model (or you) can paste.

**1. Scout before touching anything.** Read the doc for the component first.
Most components have one (`docs/<NAME>.md`), and scripts have a `--help`
that is kept current.

```bash
git log --oneline -15 -- path/to/area          # what changed recently, and why (read the bodies: git show <sha>)
ls path/to/area; grep -rn "SYMBOL_OR_KEY" scripts agents tests docs | head -30
scripts/<thing>.sh --help 2>&1 | head -40
grep -n "^#" docs/<COMPONENT>.md               # the doc's outline, then read the relevant section
```

**2. Reproduce before fixing.** Get a failing test, or a command that shows the
bug, before changing code. If none exists, write the test first
(`test-driven-development`).

```bash
bash tests/test_<area>.sh 2>&1 | grep -E 'FAIL|Results'
python3 -m pytest tests/test_<area>.py -q -x 2>&1 | tail -20
```

**3. Find the root cause, and make the minimal fix there.** Ask why the wrong
value got there, not only where it shows up. Fix it at the source, in the
fewest lines. Do not weaken a test to make it pass unless the test's
expectation is what went stale (then say so in the commit body).

**4. Run the component's tests, then the wide ones.** The narrow suites from the
[3.3 map](#33-file-area-to-tests) first, then `tests/test_scripts.sh` and the
full pytest run. Shell suites go on a Spark.

**5. Verify on the live box, read-only.** For anything operational, show it on
the real system without changing it:

```bash
ssh spark-1.tail4109f9.ts.net '~/openbeast/scripts/healthcheck.sh | tail -4'
ssh spark-1.tail4109f9.ts.net 'cd ~/ob-work && scripts/backends/spark-guard.sh --render | head'   # the CLONE's script, render only
```

**6. Write the commit body as WHY.** For example: "the in-house list gained
platform-docs in 24bc0c1 but the test still counted 16, so the suite failed on
every GNU box".

**7. Update the docs in the same change.** If behaviour, a flag, a count or a
path changed, fix the doc that states it, in the same commit or the next one.
`grep -rn` for the old wording across `docs/`, `README.md`, `AGENTS.md` and
`skills/README.md`. `python3 evals/suite_stats.py --check` covers eval counts.

**8. Check for collisions after merges.** Parallel branches merge into
`feat/spark-scale-1-8` often. After a merge, re-run every suite either side
touched, not just yours. Two green branches can merge into a red one: a count
both bumped, a shared helper both changed. Run `git log --merges -5` and
`git diff HEAD~1 --stat` to see what came in.

**9. Say what you did not verify.** "Tested against stubs; VERIFY ON
HARDWARE." This is a line in the doc and in the report, never silence.

---

## 8. Worked example sessions

Each one gives a prompt to hand the local model (via `agent.sh` or opencode),
the command sequence it should run, and what "done" looks like. Give
`--max-iter 40` for anything with a test loop ([9](#9-proof-a-real-run-on-spark-1-2026-11-15)).

### 8.1 Fix a failing shell test on a Spark

> `tests/test_<x>.sh` fails on this Spark with "<the FAIL line>". The network is
> off; do not use web_search or fetch. Reproduce it, find the root cause, make
> the minimal fix (code or a stale expectation, whichever is actually wrong),
> re-run until 0 failed, then show `git diff`. Touch only the files the fix
> needs. Do not commit. Do not spend iterations on git archaeology unless the
> cause is unclear from the code.

```bash
bash tests/test_<x>.sh 2>&1 | grep -E 'FAIL|Results'     # reproduce
grep -n '<string from the FAIL line>' tests/test_<x>.sh   # find the assertion
read_file …                                               # the code under test
git log --oneline -5 -- <file>                            # only if the "why" is unclear
edit_file …                                               # minimal fix
bash tests/test_<x>.sh 2>&1 | grep -E 'FAIL|Results'     # 0 failed
git diff --stat && git diff
```

**Done:** the suite reports `0 failed`, the diff is limited to the cause, and a
WHY-style summary is printed. [9](#9-proof-a-real-run-on-spark-1-2026-11-15) is
this exact session, run for real.

### 8.2 Add a capability to beast-apps' catalog

> Add a capability `<cap>` to agents/apps/catalog.toml for the MCP server
> `<pkg>` (`source = <its repo>`). Use only what the server's README in
> `<a local copy path>` says. Pin an exact version. Write probes with
> actionable `fix` text, gate tools that execute code or touch hardware with
> `ask_tools`/`risk_patterns`, run `scripts/apps.sh validate` and
> `python3 -m pytest tests/test_apps.py -q`, and update the "Seeded entries"
> table in docs/BEAST_APPS.md. Load the app-control skill first.

```bash
sed -n '/^\[capability.android-build\]/,/^$/p' agents/apps/catalog.toml   # copy the shape of a neighbour
edit_file agents/apps/catalog.toml                                       # [capability.X], [server.Y], [probe.Z]
scripts/apps.sh validate
scripts/apps.sh check <cap>        # on the machine that has the app; MISSING with a usable fix is fine
python3 -m pytest tests/test_apps.py -q
edit_file docs/BEAST_APPS.md       # the seeded-entries row, with the date checked
```

**Done:** `validate` passes, `test_apps.py` is green, `check` prints a status
with a fix that names the exact command, and the doc row exists. Offline:
the version pin must come from a local source (a vendored README, an npm cache
`package.json`). If none is available, the model says so instead of guessing.

### 8.3 Add a beast-docs source

> Add a source `<name>` to agents/docs/sources.toml for `<site>` using the
> `<adapter>` adapter (one `[[source]]` block; the key list is at the top of the
> file). Follow docs/BEAST_DOCS.md §3.1 and §2.3: set `requires_ack = true` if
> the site's terms restrict copying (the first sync then needs --accept-terms).
> Add a golden test
> fixture like the existing ones in tests/fixtures/beast_docs/, run
> `python3 -m pytest tests/test_beast_docs.py tests/test_platform_docs_tool.py -q`,
> and add the source to the table in docs/BEAST_DOCS.md.

```bash
grep -n -A3 '^\[\[source\]\]' agents/docs/sources.toml   # existing sources and their shape
ls tests/fixtures/beast_docs/                   # golden inputs/outputs
edit_file agents/docs/sources.toml
write_file tests/fixtures/beast_docs/<name>/…   # a synthetic page in the site's shape
python3 -m pytest tests/test_beast_docs.py tests/test_platform_docs_tool.py -q
./scripts/docs.sh sources | grep <name>
```

**Done:** both suites are green and `docs.sh sources` lists the source. A real
`sync` waits for the network. When there is one, `docs.sh sync <name>
--max-pages 20` on a connected box, then `pack` → `unpack` on the Spark.

### 8.4 Author a v5 eval task and prove it

> Load the eval-task-author skill. Write evals/tasks/<next id>_<slug>.json
> (category <C>, difficulty <D>) whose validation discriminates <the wrong
> answer a model would give>, with "ref_stem" and "dest" declared. Put the
> reference, two plausible wrong solutions, a leak probe and prove.json in
> evals/refs/<id>_<slug>/. Prove it three times and at --scale 2.0, run the
> eval tests and `python3 evals/suite_stats.py --check`. Do not edit
> evals/SUITE_VERSION or evals/suites/v5.json.

```bash
ls evals/tasks | sort -n | tail -3                       # next free id
write_file evals/tasks/<id>_<slug>.json                   # ref_stem + dest declared
write_file evals/refs/<id>_<slug>/<stem>.py               # the reference
write_file evals/refs/<id>_<slug>/wrong_<bug>.py          # x2 at least, one bug each
write_file evals/refs/<id>_<slug>/prove.json              # {"expect": {"wrong_<bug>.py": "<regex>"}}
python3 tests/prove_task.py --unit <id>_<slug>           # run 3 times
python3 tests/prove_task.py --unit <id>_<slug> --scale 2.0
python3 -m pytest tests/test_prove_task.py tests/test_eval_validator_isolation.py -q
python3 evals/suite_stats.py --check
```

**Done:** `PROVEN` on three runs and at scale 2.0, with every arm shown
(ref, wrongs with their failure classes, empty, leak, slow if timed). The
details, and what the prover cannot check for you, are in
[10.4](#104-authoring-a-v5-task-offline). Coordinate the id with any other
author: re-check `ls evals/tasks` after merging.

### 8.5 Update an engine pin offline

> On this closed Spark, install native TensorFold <V> from the staged folder
> /media/usb/tf (made with `engine.sh stage tensorfold --version <V> --platform
> linux-aarch64`). Dry-run first, then install, then show `engine.sh list` and
> the spark.env change. Do NOT restart any rank; print the exact restart steps
> for Max instead.

```bash
scripts/engine.sh install tensorfold --from /media/usb/tf --dry-run
scripts/engine.sh install tensorfold --from /media/usb/tf      # verifies signature + SHA256SUMS offline
scripts/engine.sh list && scripts/engine.sh history tensorfold
git diff scripts/backends/spark.env 2>/dev/null || grep TENSORFOLD_VERSION scripts/backends/spark.env
tensorfold capabilities --json | head
```

**Done:** the new version is installed and pinned, `history` records it, and
`spark.env` names it. The restart plan reads: every rank, the same build, via
`spark-cluster.sh stop/start` (D2), or no restart at all if the recipe (D1) is
what serves. A failed verify leaves the old pin, and the model reports that
rather than retrying unverified. VERIFY ON HARDWARE: `engine.sh` is not yet on
the live checkouts.

### 8.6 Diagnose a DOWN service from healthcheck

> `scripts/healthcheck.sh` on spark-1 shows `<service>` DOWN. Diagnose it
> read-only: do not restart anything. Report the cause with evidence (log
> lines), the fix, and the exact command for Max to run.

```bash
~/openbeast/scripts/healthcheck.sh
tail -80 ~/openbeast/.run/stack.log
journalctl --user -u openbeast --since '-30 min' --no-pager | tail -40
docker ps -a --format '{{.Names}} {{.Status}}' | head
systemd-run --user --wait --pipe docker info >/dev/null && echo docker-ok   # the docker-group case
free -m | sed -n 2p; ss -ltn | grep -E ':(3000|3001|8888|8090) '
```

Then the model matches the evidence against the troubleshooting table
([SPARK_HYDRA.md G8](SPARK_HYDRA.md#g8-when-x-fails-troubleshooting)).
**Done:** one cause backed by log lines, one fix, and one command. Usually that
is `./scripts/healthcheck.sh --restart`, or `systemctl --user restart openbeast`
for the command center. Nothing was changed.

### 8.7 Fix a doc/code drift

> docs/<X>.md says `<claim>`. Check it against the code (`--help`, grep). If the
> doc is wrong, fix the doc. If the code is wrong, do not change code: write a
> one-paragraph issue in docs/TODO.md with file:line evidence.

```bash
scripts/<tool>.sh --help 2>&1 | grep -n -- '<flag>'
grep -n '<flag>' scripts/<tool>.sh
grep -rn '<old wording>' docs README.md AGENTS.md skills/README.md
```

**Done:** the doc matches `--help`, every other mention is fixed too, and the
change is one commit.

### 8.8 Add a regression test for a reported bug (TDD)

> Bug: <symptom>. Load test-driven-development. Write the smallest test in
> tests/test_<area>.* that fails on the current code for this reason, show it
> failing, make the fix, show it passing, run the area's other suites.

**Done:** the commit contains the red-then-green test and the fix. The body
explains the root cause.

### 8.9 Review a merged branch for collisions

> Load code-review. `git log --merges -3` on feat/spark-scale-1-8: for the last
> merge, list the files both parents changed (`git diff <p1>...<p2> --stat`),
> run the suites for those areas (docs/SELF_DEVELOPMENT.md §3.3), and report
> failures with the commit that introduced each.

**Done:** a list of suites run with their results, and for each failure, the
introducing commit and the conflicting assumption.

### 8.10 Stage an offline artifact and prove it verifies

> On this online Spark, stage the libnccl2 and tailscale debs for an offline
> Spark into ~/stage/debs and prove them with `spark-root-setup.sh --check-debs`
> (no root). Then stage the beast-docs mirror (`docs.sh pack`) and show the
> sha256 file. Do not install anything.

**Done:** `OK: … holds the pinned .debs` and a `.sha256` next to the tarball.

### 8.11 Restore a toolchain from an ark drive, offline

> The ark drive is mounted at /media/max/ARK. Restore zig and go into
> ~/ob-work/opt (not ~/.local), verify the drive first, and show
> `zig version` and `go version`. The network is off.

```bash
/media/max/ARK/ark verify /media/max/ARK --quick        # fast presence check; drop --quick to re-hash
/media/max/ARK/ark restore zig --from /media/max/ARK --prefix ~/ob-work/opt --bin ~/ob-work/bin --kinds file
/media/max/ARK/ark restore go  --from /media/max/ARK --prefix ~/ob-work/opt --bin ~/ob-work/bin --kinds file
~/ob-work/bin/zig version && ~/ob-work/bin/go version
```

**Done:** `RESULT: OK`, then `0.16.0` and `go version go1.27.4 linux/arm64`.
If a file is corrupt, restore refuses with exit 1 and names it. Report it,
and do not work around it ([BEAST_ARK.md](BEAST_ARK.md) §11).

---

## 9. Proof: a real run on spark-1 (2026-11-15)

### 9.1 The task

A real, small defect. `tests/test_skill_import.sh` failed on spark-1 (GNU) with
`FAIL: in-house list: 17 names, problems:`. `skills/IN_HOUSE_SKILLS.txt` had
gained `platform-docs` (commit `24bc0c1`), but the test still expected 16, and
`skills/README.md` still said "Currently shipped (16 skills)".

Setup, exactly as recommended above:

- A scratch clone, `git clone ~/openbeast ~/selfdev-scratch`, at `ecf50a2`.
- The live checkout's `agent.sh` with `-w ~/selfdev-scratch --max-iter 25`.
- `--log-file` outside both trees.
- Web tools disabled for the process only (§2's environment: closed
  `SEARXNG_URL`, closed proxy, `OPENBEAST_OFFLINE=true`). The live stack's
  island state was untouched.

The prompt named the reproduce command, the three places to compare, the
constraint "do not change the list unless a skill directory is missing", the
end state ("0 failed"), and "do not commit, do not touch any other file".

### 9.2 What happened

| | |
|---|---|
| Wall time | **68 s** (1m7.7s) |
| Iterations | 25 of 25: **hit the cap before calling `task_done`** |
| Tool calls | 33: `bash` 26, `edit_file` 4, `read_file` 1, `update_plan` 2; **0 `web_search` / `fetch`** |
| Tokens | prompt 266,957, completion 2,070 (prefix cache hits make the prompt side cheap); 0 compactions, 0 API errors |
| Result | **correct.** 4 edits in 2 files: the test's count `16 → 17` (condition, pass message, comment) and `skills/README.md` "Currently shipped (17 skills)", plus a stale "15 in-house descriptions" line in the same file. `Results: 75 passed, 0 failed`, re-checked independently after the run |

The trajectory:

- **Iterations 1-7:** reproduce, read the test, list the skills, read the
  README. These were efficient.
- **Iterations 8-21:** git archaeology. `git log -S`, and `git show` of the
  app-control and platform-docs commits, to learn when the count last moved.
  This was correct reasoning but unnecessary: the cause was already plain from
  the code.
- **Iteration 22:** `update_plan`.
- **Iteration 23:** all four edits.
- **Iteration 24:** test green.
- **Iteration 25:** `git diff`. The budget ran out before `task_done`.

The scratch clone, the log and the run script were deleted afterwards.

**Not in scope, so not fixed by the run:** `AGENTS.md` also said "16 curated
skills". The prompt's "touch no other file" kept the model from fixing it. A
human (or a follow-up task) fixed it in the same docs change as this page.

### 9.3 GNU suites on spark-2

Run in a scratch clone of this branch, niced, on 2026-11-15:

| Suite | Result | Time |
|---|---|---|
| `test_spark_guard.sh` | 32 passed, 0 failed, 1 skipped | 1 s |
| `test_spark_deepseek_pack.sh` | 4 passed, 0 failed, 1 skipped (pack_rev needs GNU stat; 5 of 5 on a Spark) | 1 s |
| `test_spark_beast.sh` | 9 passed, 0 failed | 1 s |
| `test_spark_cluster.sh` | 31 passed, 0 failed | 4 s |
| `test_spark_supply.sh` | 22 passed, 0 failed | <1 s |
| `test_engines.sh` | 61 passed, 0 failed | 64 s |
| `test_backends.sh` | 190 passed, 0 failed | 127 s |
| `test_skill_import.sh` | 74 passed, **1 failed** (`in-house list: 17 names`, the 9.1 defect; the test's fix is code, not landed here) | 4 s |
| `test_clients.sh` | 96 passed, 0 failed | 5 s |
| `test_conf_secrets.sh` | 45 passed, 0 failed | 9 s |
| `test_shell_ops.sh` | 63 passed, 0 failed | 21 s |
| `test_setup_client.sh` | 15 passed, 0 failed | 1 s |

The whole set takes about 4 minutes niced, with the pair serving throughout.

### 9.4 Lessons (folded into §§1-8)

1. **The model fixes real defects in this repo, fast, with no network.** The
   loop of reproduce, compare, minimal edit, re-run, diff is what it does
   naturally when the prompt names the reproduce command and the end state.
2. **Budget 40 iterations for a test loop, not 25.** Or say "skip git history
   unless the code does not explain it". Most of the budget went to archaeology
   (§8.1's prompt now says this).
3. **Run the live checkout's `agent.sh` with `-w` on the clone.** The clone has
   no `openbeast.conf`, so its own `agent.sh` would dial `:8080` (§1.2).
4. **`agent.sh`'s web tools ignore island mode** (era-hashed `tools.py`). Tell
   the model the network is off, and disable them for the process (§2). With
   both done, it made zero web calls.
5. **Scope by file list, and widen it deliberately.** "Touch no other file" was
   safe but left one stale count in `AGENTS.md`. Name the doc files that state
   the same fact (§7 step 7), or allow "and any doc that states the same count".
6. **Verify independently.** Re-run the test and read the diff yourself. "Max
   iterations reached" is not failure, and "task_done" would not have been proof
   either.
7. **The test map matters.** This suite fails for BSD reasons on the Mac and
   passes on a Spark, so the run had to happen on a Spark to be meaningful
   (§3.2).

---

## 10. The v5 eval suite, offline: running it and authoring for it

This section is for the local model on spark-hydra running
and extending the eval suite with the network off. The suite itself is
described in [`evals/README.md`, v5](../evals/README.md#v5--the-current-suite);
this is the operator's and author's path through it.

### 10.1 What you need on the box, and how to get it there offline

The **harness host** is where `run_eval.py` runs. Tools and validators
execute there, not where the model runs, so the harness host needs every
validator toolchain, while the model can be anywhere `--base-url` reaches.

| Need | Why | Offline source |
|---|---|---|
| python >= 3.10 with the test closure | `run_eval.py`, the prover, pytest | the test venv of [3.1](#31-the-right-python), built while online, or a wheelhouse ([SPARK_HYDRA.md H7](SPARK_HYDRA.md#h7-test-and-language-toolchains)) |
| zig **0.16.0** exactly | the six zig units (163_f, 167_f, 168_f, 36_f, 123_f, 122_e) and the pack's pin; `profile.py` preflight refuses any other version | the release tarball, staged with its sha256 (CI pins the same file) |
| go, rustc, gcc/g++ (or clang) | the compiled units | distro packages staged as debs, or rustup's offline installer |
| git | 179 (bisect), and `validator_seal()` reads `git worktree list` | already on every box |
| `sandbox-exec` (macOS) or Landlock / user namespaces (Linux) | solution confinement; without it a confining validator returns `verdict="sandbox"` and fails the unit, never runs it unconfined | the OS |

On 2026-11-15 the Sparks had gcc/g++ only (no zig, go or rust). A Spark is
fine as the harness host for python, C and C++ units; the rest of the suite
needs the toolchains staged first. Check with:

```bash
zig version; go version; rustc --version; cc --version | head -1
python3 evals/profile.py --help    # the preflight lists what it checks
```

### 10.2 Running a profile against the local model

```bash
# GLM on the pair, thinking off, four streams, inside four hours:
python3 evals/profile.py --model glm-5.3-flash --budget-h 4 --jobs 4
# one arm, a few units, no leaderboard (a smoke run):
python3 evals/run_eval.py --base-url http://127.0.0.1:8000/v1 --tasks 163,190 --no-cache
```

- A profile runs **L0** (bare) on every unit, measures how fast this server
  really is, then fits **L1** (packs, zig only) and **L2** (packs +
  diagnostics + escalation, compiled and shell units) into what is left of
  the budget, cutting L2 in a fixed order if it must. L0 and L1 are never cut.
- Web tools are **off** under `OPENBEAST_EVAL=1`, so an offline run measures
  the same thing as an online one. Do not set `OPENBEAST_EVAL_WEB=record`
  for a board run.
- Results land in `evals/results/eval-<slug>-<arm>-<ts>.json`. The board takes
  only full-suite L0 rows of one era; `scoring.py --rebuild` rescores.

### 10.3 What the harness does to every unit (so you can read a failure)

In order, per unit:

1. **Fixture check and setup.** `setup` runs in a fresh 0700 cwd with
   `PYTHONSAFEPATH=1`. A `/tmp/eval_*` root owned by another uid, a
   symlink, or world-writable is refused.
2. **The agent.** It runs with `OPENBEAST_EVAL=1` (sealed answer key: no
   reads of `evals/tasks`, `evals/refs`, `evals/suites`, in the kernel on
   macOS; web tools off), a per-agent tag in its environment, and on macOS
   a per-agent tag file that its bash sandbox denies.
3. **The sweep.** When the runner exits (or hits its wall cap), every
   process the agent started is frozen and killed, including daemons that
   double-forked into `setsid`, and the sweep then checks that none
   survived. A unit with survivors is **not validated** and fails with
   `Not validated: the agent left processes running that could not be killed`.
   The result row carries `leftovers_killed`.
4. **Validation.** The script reaches the validator on stdin from an
   unlinked file; the validator runs in an empty private cwd with a scrubbed
   environment and a **private build cache** (zig, Go, cargo, XDG; ccache
   off) that is deleted afterwards. A python validator passes only if it
   ran to its end: a solution that exits the validator's process early with
   status 0 fails with `the validator did not run to its end`.
5. **Cleanup**, and every solution process group the validator recorded is
   SIGKILLed.

Failures you will meet, and what they mean:

| Output | Meaning |
|---|---|
| `solution exited 71` | the macOS sandbox refused to start the solution (fixed for relative argv and env launchers; a new one means the validator runs an image the exec rule does not name) |
| `verdict="sandbox"` / `no confinement` | the host cannot confine (no Landlock, no user namespaces); the unit cannot be judged here |
| `Validation timed out (Ns)` | the harness's own validator timeout (30 s x scale), not the validator's limit: an infrastructure result, retried, never banked on the first strike |
| `Not validated: the agent left processes running ...` | an agent process could not be killed (another uid, D state); look at the listed pids |

### 10.4 Authoring a v5 task offline

Load the `eval-task-author` skill (and `eval-variant-porter` for language
variants). The gate is the prover, which needs no model and no network:

```bash
ls evals/tasks | sort -n | tail -3                       # the next free id
# write evals/tasks/<id>_<slug>.json with "ref_stem" and "dest" declared,
# and evals/refs/<id>_<slug>/: the ref, >= 2 wrong_<name>.<ext>, leak.<ext>,
# slow.<ext> if the validator has its own time limit, and prove.json:
#   {"expect": {"wrong_<name>.<ext>": "<regex for the failure class>"},
#    "slow_expect": "<regex>", "timed": true|false}
python3 tests/prove_task.py --unit <id>_<slug>           # every arm, through run_eval's lifecycle
python3 tests/prove_task.py --unit <id>_<slug> --scale 2.0
python3 -m pytest tests/test_prove_task.py tests/test_eval_validator_isolation.py -q
python3 evals/suite_stats.py --check
```

**Done** is `PROVEN` on three separate runs (determinism) and at
`--scale 2.0`: the reference passes 3/3 (5/5 when timed) inside the 15 s
validator budget at scale 1, cold compile included; every wrong solution
fails with its declared failure class (never a compile or import error);
an empty submission fails; no oracle-like file is in the fixture; the leak
probe fails; a slow solution fails by the validator's own limit. Families
whose solutions are directories, shell scripts run in the fixture, or
whose wrongs sit in `bad/` keep their own `evals/refs/<dir>/prove.py`, and
`prove_task.py` delegates to it.

Rules that the prover cannot check, and the verifiers found broken:

- Run the solution with `vlib.run_solution` in a **separate process** from
  anything that holds answers. Never import it into the validator: it can
  rebind your checks (U4's verifiers did, on 152 and 153).
- Hold the oracle and the seed **in memory**; write only inputs to disk.
- Build compiled solutions with `vlib.start_build({SRC: source}, BUILD, EXE)`
  (a private copy, the compiler the harness pinned before the agent ran, a
  clean env, a native executable required) and run `b.product`, never a
  binary in the fixture dir; do not set a cache dir of your own (the harness
  gives you a private one).
- Run a Python solution with `confine=[work], read_only=vlib.python_runtime(...)`
  from a private copy: the deny-list form has no read-deny on Linux and
  misses a stray copy of a reference on macOS.
- Scale every fixed limit with `vlib.t()`; start no `python -c SRC` child.
- Zig prompts state the contract only (Zig 0.16, entry point, compile
  command). API tutoring (`&fr.interface`, `init.io`, `takeDelimiter`) is
  banned in v5 and the prover rejects it.

A task is not shipped when it is proven: it ships when an **adversarial
verifier** (a second session that tries to pass every unit without solving
it, and re-tests the previous round's fixes) finds nothing open. That is
what kept 12 families out of v5.0 (`evals/README.md`, "v5.1 backlog"). Do
not add a unit to `evals/suites/v5.json` yourself: changing the manifest
changes what the board measures, and that is Max's call.

### 10.5 Prove a shipped unit after a harness change

Any change to `evals/run_eval.py`'s fingerprinted functions, to
`evals/vlib.py`, or to `agents/tools.py` / `runner.py` moves the era
([4](#4-the-eval-era-rule)). Re-prove the suite before claiming nothing
broke:

```bash
python3 tests/prove_task.py --suite v5 --json /tmp/proof.json      # ~1 h on a loaded Mac
```

Run one family at a time when you parallelise by hand: a family's units
share one `/tmp/eval_*` dir, and two provers of the same family at once
corrupt each other (186's and 190's verifiers both hit this).
