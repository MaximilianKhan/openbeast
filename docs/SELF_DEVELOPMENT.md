# Self-development: OpenBeast's own model working on OpenBeast

OpenBeast serves a capable coding model, GLM-5.3-Flash on spark-hydra. That
model, with OpenBeast's own tools, can maintain the repo that runs it. This
page shows how to do that well, including on a network that is cut off from the
internet. Everything here was written for, and checked against, the
`feat/spark-scale-1-8` branch and the live pair on 2026-11-15. "Proof" below
records a real run.

The launch and operations runbook for the pair is
[`SPARK_HYDRA.md`](SPARK_HYDRA.md). This page assumes the pair is up
(`healthcheck.sh`: "All 9 services healthy").

| Section | |
|---|---|
| [1. Setup](#1-setup) | a clone, which client, which skills |
| [2. What works with the network off](#2-what-works-with-the-network-off) | tools, refusals, mirrors, docs |
| [3. Tests: which to run for which change](#3-tests-which-to-run-for-which-change) | file area to test map, GNU-only suites, the right python |
| [4. The eval-era rule](#4-the-eval-era-rule) | six hashed files, and why touching them matters |
| [5. Git discipline](#5-git-discipline) | branches, atomic commits, messages |
| [6. Safety](#6-safety) | secrets, overwrites, live boxes, remote quoting |
| [7. How Claude works on this repo](#7-how-claude-works-on-this-repo) | the loop, as copy-pasteable recipes |
| [8. Worked example sessions](#8-worked-example-sessions) | ten prompts, the commands, what "done" looks like |
| [9. Proof: a real run on spark-1](#9-proof-a-real-run-on-spark-1-2026-11-15) | what happened, and the lessons folded in above |

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
| **`./agent.sh` on spark-1** (measured) | spark-1, in `-w <clone>` | GLM on `127.0.0.1:8000`, read from the **live** checkout's `openbeast.conf` | unattended tasks with a clear end state; works with the internet off, since only the LAN or tailnet ssh is needed to start it |
| **opencode on the Mac, client mode** (measured as the daily setup) | the Mac, in the clone | GLM through beast-gate `https://spark-1.tail4109f9.ts.net:8443/v1` with the Mac's device key | interactive work; `skill()`, `language_reference`, `platform_docs` and the other MCP tools are all there |
| **`start_agent` from a WebUI or opencode chat** | where that MCP server runs | the configured `OPENBEAST_AGENT_INFERENCE_URL` | delegating a long sub-task while you keep talking |
| opencode **on spark-1** | — | — | **not set up**: opencode is not installed on spark-1, and the repo's `opencode.json` points at `localhost:8080` (llama.cpp), not `:8000`. Use `agent.sh` there |

**`agent.sh`: run the live checkout's script, point it at the clone.** The
clone's own `agent.sh` would find no `openbeast.conf` and dial the llama.cpp
default `http://localhost:8080/v1`, where nothing serves on a Spark. The live
`~/openbeast/agent.sh` sources the live conf. That gives it
`OPENBEAST_AGENT_INFERENCE_URL=http://127.0.0.1:8000/v1` and
`OPENBEAST_INFERENCE_MODEL=GLM-5.3-Flash-EXL3`. All file and shell work then
happens in `-w`:

```bash
ssh spark-1.tail4109f9.ts.net
cd ~/openbeast && ./agent.sh -w ~/ob-work --max-iter 40 \
  --log-file ~/agent-logs/$(date +%Y%m%d-%H%M)-topic.jsonl "<task>"
```

`--log-file` outside both trees keeps the transcript (JSONL: `start`,
`iteration`, `tool_call`, `assistant`, `max_iterations` / done events) out of
`git status`.

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
| `scripts/backends/spark-cluster.sh`, `tensorfold/spark-node.sh`, `vllm/` | `tests/test_spark_cluster.sh`, `tests/test_backends.sh` |
| `scripts/backends/spark-guard.sh` | `tests/test_spark_guard.sh` |
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
| a live surface (only read-only, only on the live box) | `scripts/healthcheck.sh`, `scripts/backends/conformance.sh --model GLM-5.3-Flash-EXL3`, `tests/test_smoke.sh` (needs a live stack) |

---

## 4. The eval-era rule

`evals/cache.py` hashes six files into the **eval era**. Every cached and
reported eval result is keyed by it:

```
system-prompt.md   system-prompt-tools.md   opencode.json
agents/runner.py   agents/tools.py          evals/SUITE_VERSION
```

`./scripts/eval-era.sh` prints the era, `--files` shows each file's hash, and
`--check <hash>` exits 1 if it moved.

- **Why it matters.** Rows measured on either side of a change to any of these
  files are **not comparable**. A one-character edit to `tools.py` invalidates
  every cached result and splits a campaign mid-way
  (BEAST_CAMPAIGN_PLAN §1). This is why the island guard is in
  `mcp_server.py`, why `app-control` and `platform-docs` are off the always-on
  menu (`prompt_index: false`: adding them rewrites `system-prompt-tools.md`),
  and why fixes to the runner's web tools wait for "the next deliberate era
  roll".
- **The rule.** Do not edit any of the six without Max. **`system-prompt.md`
  and `system-prompt-tools.md` are never edited without Max, full stop.** If a
  fix needs one of them, stop. Write the change up (the file, the diff, the
  reason), and put it in the branch's notes or `docs/TODO.md` instead.
- **Check before committing:**

  ```bash
  git diff --name-only main... | grep -E '^(system-prompt(-tools)?\.md|opencode\.json|agents/(runner|tools)\.py|evals/SUITE_VERSION)$' \
    && echo "ERA FILE TOUCHED — stop and ask Max"
  ```

- **v5 is being built** (`evals/suites/v5-fast.json` exists; the full v5 suite
  is in progress on parallel branches). `SUITE_VERSION` moves only when Max
  rolls the suite.

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
  `--render` on `spark-guard.sh`; `--preflight` on `bootstrap.sh`;
  `setup-tailscale.sh --status`.
- **Live boxes are read-only.** On spark-1 / spark-2 outside a clone, only
  probes: `healthcheck.sh`, `curl …/health`, `systemctl --user status`,
  `journalctl`, `docker ps`, `git status`, `spark-guard.sh --status`. Do not
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
> answer a model would give>. Write a reference solution in /tmp/ref, run the
> task's setup + validation against it (must print OK), then write the most
> plausible WRONG solution and confirm the validation rejects it. Run
> `bash tests/test_scripts.sh` and `python3 evals/suite_stats.py --check`. Do not
> edit evals/SUITE_VERSION.

```bash
ls evals/tasks | sort -n | tail -3                    # next free id
write_file evals/tasks/<id>_<slug>.json
python3 - <<'EOF'   # run setup, drop the reference in, run the validation
import json, subprocess; t = json.load(open("evals/tasks/<id>_<slug>.json"))
subprocess.run(t["setup"], shell=True, check=True)
# … copy /tmp/ref/* into the task's dir …
v = t["validation"]; cmd = ["python3", "-c", v["script"]] if v["type"] == "python" else ["bash", "-c", v["script"]]
print(subprocess.run(cmd, capture_output=True, text=True))
EOF
# repeat with the WRONG solution: must fail
bash tests/test_scripts.sh 2>&1 | tail -3
python3 evals/suite_stats.py --check
```

**Done:** the reference passes and the wrong solution fails (both outputs
shown). `test_scripts.sh` parses it, and `suite_stats --check` either passes or
names the doc counts that now need updating, which go in the same change.
Coordinate the id with the v5 branches: other agents are adding tasks in
parallel, so re-check `ls evals/tasks` after merging.

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
