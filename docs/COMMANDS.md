# Commands cheat sheet

The commands people and agents run when working on OpenBeast, grouped by
where they run. Every command here was taken from a script's `--help`, its
source, or a runbook that was checked against the source on 2026-11-18. When
a command changes state on a live box, the line says so. When a flag is not
listed here, run the script's `--help`; it is kept current.

The machines, by name (details in [SPARK_HYDRA.md §0](SPARK_HYDRA.md#0-the-pair-at-a-glance)):

| Name | What it is | Repo checkout |
|---|---|---|
| **Mac** | Max's MacBook, a client (`openbeast-client`, opencode) and the dev box with GitHub access | `~/Documents/projects/openbeast.nosync` (any clone works) |
| **spark-1** | DGX Spark, head of the pair: rank 0, the command center, beast-gate | `~/openbeast` is the **live command center**: read it, never edit it |
| **spark-2** | DGX Spark, worker: rank 1 only, no OpenBeast services | `~/openbeast` (reference copy); scratch clones are safe here |
| **beast** | the RTX 5090 llama.cpp rig (offline on the tailnet on 2026-11-18) | `~/Documents/openbeast` (the path BEAST_ISLAND.md's aliases use; not re-checked) |

Conventions used below:

- `ssh spark-1 '…'` from the Mac. **Quote the remote command**: unquoted, the
  Mac's shell expands `~`, `$VAR` and globs before ssh sends them.
- From spark-1, `ssh spark-2` reaches the worker over the fabric
  (10.100.216.2, the NVIDIA Sync alias). From spark-2, spark-1 is
  `max@10.100.216.1`.
- `PY=~/.local/opt/openbeast-eval-venv/bin/python` on the Mac. On spark-2 the
  test venv is `~/obvenv` (pytest 9.0.3, Python 3.12.3). **spark-1 has no
  pytest** (checked 2026-11-18).
- **Live box rule.** On spark-1 and spark-2, outside a scratch clone, run only
  read-only probes unless the task is an operation and Max asked for it. Every
  line marked **CHANGES STATE** stops or restarts something people may be using.

Contents:
[1. Mac client](#1-mac-client) ·
[2. spark-1: status and logs](#2-spark-1-status-and-logs-read-only) ·
[3. spark-1: start, stop, switch](#3-spark-1-start-stop-switch-changes-state) ·
[4. spark-1: DeepSeek packs](#4-spark-1-deepseek-packs) ·
[5. spark-1: the command center](#5-spark-1-the-command-center) ·
[6. spark-2](#6-spark-2) ·
[7. The local model as a developer](#7-the-local-model-as-a-developer) ·
[8. Tests and lint](#8-tests-and-lint) ·
[9. Git and PRs, online](#9-git-and-prs-online) ·
[10. Git, offline](#10-git-offline) ·
[11. Evals and campaigns](#11-evals-and-campaigns) ·
[12. Benches](#12-benches) ·
[13. Docs, mirrors and offline material](#13-docs-mirrors-and-offline-material) ·
[14. Emergency recovery](#14-emergency-recovery)

---

## 1. Mac client

Client mode is installed by `scripts/setup-client.sh`; the CLI is
`~/.local/bin/openbeast-client` (a symlink to `scripts/client.sh`).
Details: [SPARK_HYDRA.md §F2](SPARK_HYDRA.md#f2-client-mode-one-or-several-rigs), [BEAST_SLOT.md](BEAST_SLOT.md).

```bash
openbeast-client status                  # client doctor: env, venv, tailnet, rig endpoints, beast-slot view, opencode wiring
openbeast-client rigs                    # every registered rig, reachability and health; * = active
openbeast-client use spark-hydra         # switch opencode, tools and search to the Spark pair (refuses a down rig)
openbeast-client use beast               # switch to the 5090 rig
openbeast-client refresh-config          # re-sync the opencode model catalog only
openbeast-client update --no-pull        # refresh deps + catalog after updating the checkout yourself (offline: from a bundle)
openbeast-client agent "<task>"          # a CLI agent HERE against the rig's model (files and shell act on the Mac)
openbeast-client search up|down          # the local SearXNG container
scripts/client-e2e.sh                    # end-to-end check of client mode against the active rig (step d switches rigs and back)
```

```bash
cd <project> && opencode                 # pick an openbeast-rig/<model> (the active rig) or openbeast-<rig>/<model>
curl -fsS https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -m json.tool | head -30   # what the pair serves
```

Raw API through beast-gate (the device key lives in a 0600 file; it is never
put on argv by the scripts, and the `$(cat …)` here keeps it out of history):

```bash
curl -s https://spark-1.tail4109f9.ts.net:8443/v1/chat/completions \
  -H "Authorization: Bearer $(cat ~/.config/openbeast/spark-hydra-gate.key)" \
  -H 'Content-Type: application/json' \
  -d '{"model": "DeepSeek-V4.1-Flash-1017", "messages": [{"role": "user", "content": "hello"}]}'
```

Per request, `"chat_template_kwargs": {"enable_thinking": false}` turns
thinking off; the gate's default is on (`INFERENCE_THINKING=on`).

---

## 2. spark-1: status and logs (read-only)

```bash
~/openbeast/scripts/healthcheck.sh                      # expect "All 9 services healthy" + the unified-memory line
~/openbeast/start.sh status                             # what is running (pids)
~/openbeast/scripts/doctor.sh                           # config / security / supply-chain / health fix-list
python3 ~/openbeast/agents/island.py status             # online or islanded, and why
systemctl --user is-active deepseek-v41-inference spark-hydra-inference openbeast
systemctl --user is-enabled deepseek-v41-inference spark-hydra-inference   # "enabled" = the model selected for boot
systemctl --user list-unit-files --no-pager | grep -E 'openbeast|deepseek|spark|hydra'
curl -fsS --max-time 3 http://127.0.0.1:8000/health    # backend readiness (no key needed for /health)
curl -s -H "Authorization: Bearer $(cat ~/.config/openbeast/tensorfold-api-key)" http://127.0.0.1:8000/v1/models
docker ps --format '{{.Names}} {{.Image}} {{.Status}}'  # dsv41-1017-exl3-tf (rank 0), open-webui, searxng
tailscale serve status                                  # what is published on the tailnet (:443, :8443-:8446, :8889)
ss -ltn | grep -E ':(3000|3001|3002|3003|3004|8000|8090|8443|8888) '
free -m | sed -n 2p                                     # unified memory: watch "available"
```

If PR #212 is installed (`~/.local/bin/beast-spark` exists on spark-1 since
2026-11-17), `beaststatus` (= `beast-spark status`) shows the selected model
and pack, the three units, and what `:8000` serves in one go.

Logs:

```bash
journalctl --user -fu deepseek-v41-inference            # rank 0 + the recipe's start.sh (Ctrl-C exits)
journalctl --user -u spark-hydra-inference --since today   # GLM's unit, when GLM is selected
docker logs -f dsv41-1017-exl3-tf                       # rank 0 container
ssh spark-2 'docker logs -f dsv41-1017-exl3-tf'         # rank 1 container (quote it)
tail -f ~/openbeast/.run/stack.log                      # the command center's supervisor
journalctl --user -u openbeast --since today
tail -20 ~/openbeast/.run/supervisor-heal.log           # what the supervisor restarted by itself
```

Firewall and fabric (read-only modes):

```bash
sudo ~/openbeast/scripts/backends/spark-guard.sh --status       # nftables table; without sudo it prints "loaded: unknown"
~/openbeast/scripts/backends/spark-fabric-mtu.sh --status       # MTU per fabric port (9000 since 2026-11-17)
~/openbeast/scripts/setup-tailscale.sh --status                 # read-only mount table
```

Never print secrets. To show configuration, filter:
`grep -vE 'KEY|TOKEN|PASS|SECRET' ~/openbeast/openbeast.conf`.

---

## 3. spark-1: start, stop, switch (CHANGES STATE)

The pair serves exactly two models, one at a time: **DeepSeek-V4.1-Flash-1017**
(unit `deepseek-v41-inference`, default since 2026-11-17, on Mia's EAQ v1.1
"-v2" pack) and **GLM-5.3-Flash** (unit `spark-hydra-inference`). "Selected"
means the enabled unit. Finish or warn about in-flight requests first:
a switch or restart drops every generation in progress.

With the beast-spark shortcuts (PR #212; the aliases are in spark-1's
`~/.bashrc` and need an interactive shell):

```bash
beastup        # beast-spark up:   start the selected model, wait for /v1/models, then the command center
beastdown      # beast-spark down: stop the command center and both inference units (boot selection kept)
beastds        # beast-spark ds:   switch to DeepSeek on the -v2 pack (canary + conformance)
beastglm       # beast-spark glm:  switch to GLM-5.3-Flash
beaststatus    # beast-spark status
```

From a non-interactive shell (ssh one-liner, a script, an agent), call
`~/.local/bin/beast-spark <verb>` directly; aliases are not expanded there.

Without the shortcuts, the underlying commands
([OPENBEAST-SERVING.md](OPENBEAST-SERVING.md)):

```bash
systemctl --user restart deepseek-v41-inference         # both ranks (rank 1 is started over ssh by the recipe)
systemctl --user stop deepseek-v41-inference
systemctl --user start deepseek-v41-inference
bash ~/openbeast-recipes/deepseek-prep/rollback/restore-glm.sh            # DeepSeek -> GLM
systemctl --user stop spark-hydra-inference && \
  bash ~/openbeast-recipes/deepseek-prep/activate-stock.sh                # GLM -> DeepSeek (whatever pack local.sh selects)
```

After any switch, on each client: `openbeast-client use spark-hydra`.

**Never** `docker stop` a rank container, or run the recipe's `stop.sh` or
`~/openbeast/stop.sh` by hand while the matching unit is enabled: systemd
restarts behind you (`Restart=on-failure`, after 60 s).

---

## 4. spark-1: DeepSeek packs

`spark-deepseek-pack.sh` is installed in `~/openbeast-recipes/deepseek-prep/`
(copied from `scripts/backends/`). [SPARK_HYDRA.md "DeepSeek packs"](SPARK_HYDRA.md#deepseek-packs-spark-deepseek-pack).

```bash
cd ~/openbeast-recipes/deepseek-prep
./spark-deepseek-pack.sh list                                       # serving pack, staged packs, previous (read-only)
./spark-deepseek-pack.sh use ~/Documents/<pack-folder> --dry-run    # checks + plan, changes nothing
./spark-deepseek-pack.sh use ~/Documents/<pack-folder> --bench      # CHANGES STATE: swap, verify, A/B bench
./spark-deepseek-pack.sh use ~/Documents/<pack-folder> --id ORG/NAME   # name the HF-cache ID yourself
./spark-deepseek-pack.sh use stock                                  # CHANGES STATE: back to Mia EAQ v1 @ 45ee5d0f
```

The verification steps it runs, which you can also run by hand (read-only):

```bash
python3 ~/openbeast-recipes/deepseek-prep/verify-chat.py http://127.0.0.1:8000 ~/.config/openbeast/tensorfold-api-key   # expects 391
cd ~/openbeast && scripts/backends/conformance.sh --url http://127.0.0.1:8000 --model DeepSeek-V4.1-Flash-1017 \
  --backend tensorfold --key-file ~/.config/openbeast/tensorfold-api-key --out /tmp/conf-$(date +%H%M)
# last line: "VERDICT: PASS"
```

To install an updated helper from a checkout (on the Mac, or from a clone on
spark-1): `scp scripts/backends/spark-deepseek-pack.sh scripts/backends/spark-deepseek-prepare-local.sh spark-1:openbeast-recipes/deepseek-prep/`.

---

## 5. spark-1: the command center

```bash
systemctl --user restart openbeast                      # CHANGES STATE: WebUI, tools, gate, chat, artifact; drops gate streams
~/openbeast/scripts/healthcheck.sh --restart            # CHANGES STATE: restart only what is DOWN (the watchdog does this every 5 min)
cd ~/openbeast && ./scripts/clients.sh list             # enrolled devices (no keys shown)
cd ~/openbeast && ./scripts/clients.sh enroll <id> --label "text"   # prints a new key ONCE: write it straight to a 0600 file on the device
cd ~/openbeast && ./scripts/clients.sh revoke <id>      # hot-reloaded by the gate, no restart
cd ~/openbeast && ./scripts/clients.sh rotate <id>      # new key for the same device
~/openbeast/scripts/job.sh list                         # beast-chat tracked jobs
~/openbeast/scripts/artifact.sh --help                  # beast-artifact CLI
```

Updating the command center to a newer commit is
[SPARK_HYDRA.md §G6](SPARK_HYDRA.md#g6-updating). Short form, at a quiet
moment: `cd ~/openbeast && git status --short` (must show nothing you would
lose), `git pull --ff-only`, then `systemctl --user restart openbeast`.
Offline, pull from the local hub instead of GitHub ([§10](#10-git-offline)).

---

## 6. spark-2

spark-2 runs rank 1 only. Its `~/openbeast` is not a live command center,
but keep it on the same commit as spark-1 (`spark-cluster.sh check` enforces
"the same repo on every node").

```bash
ssh spark-2 'docker ps --format "{{.Names}} {{.Status}}"'    # from spark-1: rank 1 container
ssh spark-2 'free -m | sed -n 2p; df -h / | tail -1'
ssh spark-2 'cd ~/openbeast && git log --oneline -1 && git status --short'
```

spark-2 is where Linux-only test suites run, in a scratch clone ([§8](#8-tests-and-lint)).

---

## 7. The local model as a developer

Full guide: [SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md). Always work in a
scratch clone, never in `~/openbeast` on spark-1.

```bash
# headless agent on spark-1: the LIVE checkout's agent.sh (it sources the live conf
# and its key), all file and shell work in the clone given by -w
cd ~/openbeast && ./agent.sh -w ~/ob-work --max-iter 40 \
  --log-file ~/agent-logs/$(date +%Y%m%d-%H%M)-topic.jsonl "<task>"
# hand it skills as context (the runner has no skill() tool)
cat skills/codebase-onboarding/SKILL.md skills/git-discipline/SKILL.md > /tmp/ctx.md
cd ~/openbeast && ./agent.sh -w ~/ob-work --context-file /tmp/ctx.md --max-iter 40 "<task>"
```

opencode v2.3.2 on either Spark (since 2026-11-18; provider
`spark-hydra-local` at `127.0.0.1:8000`, default model DeepSeek; no OpenBeast
MCP tools yet). Details: [SELF_DEVELOPMENT.md "opencode on the Sparks"](SELF_DEVELOPMENT.md#opencode-on-the-sparks).

```bash
cd ~/ob-<topic> && opencode                                   # interactive, in a clone
opencode run "What is 17 times 23? Reply with only the number."   # smoke test: 391
opencode --version                                            # v2.3.2
systemctl --user status opencode-model-link --no-pager        # on spark-2: the ssh tunnel to spark-1's :8000
cat ~/Documents/opencode/INSTALL.md                           # offline reinstall from the kept tarball
```

---

## 8. Tests and lint

CI is off (since 2026-11-17). The author runs the checks before merging
([CONTRIBUTING.md "CI is off"](../CONTRIBUTING.md#ci-is-off-check-locally-before-merging)).
Which suites cover which files: [SELF_DEVELOPMENT.md §3.3](SELF_DEVELOPMENT.md#33-file-area-to-tests).

On the Mac:

```bash
PY=~/.local/opt/openbeast-eval-venv/bin/python
$PY -m pytest tests/ -q                                  # the whole Python suite
$PY -m pytest -q tests/test_beast_docs.py tests/test_platform_docs_tool.py   # docs-checking tests
$PY -m pytest -q tests/test_suite_stats.py tests/test_beast_slot_ports_doc.py tests/test_v5_board.py tests/test_tutorials_index.py
$PY evals/suite_stats.py --check                         # docs cite the generated eval counts (exit 1 on drift)
$PY evals/v5_board.py --check                            # README / RESULTS v5 board blocks are current
shellcheck -S error scripts/<file>.sh                    # ShellCheck at CI's severity
ruff check agents evals tests scripts extensions --select E9,F
bash tests/test_<area>.sh                                # portable shell suites (some are GNU-only: run those on spark-2)
grep -n 'bash "\$REPO_DIR/tests/' tests/run_tests.sh     # the shell suites run_tests.sh runs
```

On spark-2, in a scratch clone (GNU userland, no services disturbed):

```bash
ssh spark-2 'set -e; d=$(mktemp -d); trap "rm -rf $d" EXIT
  git clone -q ~/openbeast "$d/ob"            # or a branch from the hub: git clone -q -b <branch> max@10.100.216.1:git/openbeast.git "$d/ob"
  cd "$d/ob" && source ~/obvenv/bin/activate && nice -n 19 ./tests/run_tests.sh 2>&1 | tail -30'
```

`source ~/obvenv/bin/activate` matters: `run_tests.sh` and the shell suites
call `python3`, and spark-2's system `python3` has no pytest.
ShellCheck is **not installed** on either Spark (checked 2026-11-18): lint on
the Mac, or stage a shellcheck binary for the Sparks.

The era check before committing ([SELF_DEVELOPMENT.md §4](SELF_DEVELOPMENT.md#4-the-eval-era-rule)):

```bash
scripts/eval-era.sh --files
git diff --name-only main... | grep -E '^(system-prompt(-tools)?\.md|agents/(runner|tools)\.py|evals/(SUITE_VERSION|HARNESS_VERSION|vlib\.py))$' \
  && echo "ERA FILE TOUCHED — stop and ask Max"
```

---

## 9. Git and PRs, online

From the Mac (spark-1 has no `gh` and no GitHub credentials, checked
2026-11-18).

```bash
git fetch origin && git switch -c docs/<topic> origin/main     # branch first; never commit to main
git add <paths> && git diff --cached                            # stage deliberately, read the diff
git commit                                                      # imperative subject <= 72 chars, body says WHY
git push -u origin docs/<topic>
gh pr create --base main --title "<subject>" --body-file /tmp/pr.md   # body: what changed, the local test results
gh pr view <N> --json state,mergeable
gh pr merge <N> --merge --admin                                 # only after the local checks pass (CI is off)
```

Worktrees for parallel sessions:
`git worktree add -b <branch> .claude/worktrees/<name> origin/main`, and
`git worktree remove .claude/worktrees/<name>` when done. Never use a bare
`git stash`: the stash is shared between worktrees and sessions.

---

## 10. Git, offline

When GitHub is unreachable, the Sparks and the Mac exchange branches through
a bare repository on spark-1, `~/git/openbeast.git` (the "hub"), and an
offline trunk branch, `offline/main`. **The hub does not exist yet**
(2026-11-18): it is created once, ideally while GitHub is still reachable. The full procedure, including the
one-time setup and the sync back to GitHub, is
[SELF_DEVELOPMENT.md §11](SELF_DEVELOPMENT.md#11-developing-with-github-unreachable).

```bash
# a work clone (spark-1; from spark-2 use max@10.100.216.1:git/openbeast.git; from the Mac spark-1:git/openbeast.git)
git clone ~/git/openbeast.git ~/ob-work && cd ~/ob-work
git config user.name "spark-1 local model" && git config user.email "<address Max chose>"
git switch -c fix/<topic> origin/offline/main
# ... edit, test, commit ...
git push origin fix/<topic>                                     # share the branch through the hub
git switch offline/main && git pull --ff-only && git merge --no-ff fix/<topic> && git push origin offline/main
```

Carry work by drive instead of the network:

```bash
git bundle create /media/usb/ob-$(date +%Y%m%d).bundle offline/main fix/<topic>   # on the source
git -C ~/ob-work bundle verify /media/usb/ob-<date>.bundle                         # on the target (needs a repo)
git -C ~/ob-work fetch /media/usb/ob-<date>.bundle 'refs/heads/*:refs/remotes/usb/*'
```

---

## 11. Evals and campaigns

Runbooks: [evals/README.md](../evals/README.md), [MODEL_CAMPAIGN.md](MODEL_CAMPAIGN.md),
[SELF_DEVELOPMENT.md §10](SELF_DEVELOPMENT.md#10-the-v5-eval-suite-offline-running-it-and-authoring-for-it).
Evals measure: never start one against the pair while Max is using it, and
take the GPU lease on the rig (`scripts/gpu-lease.sh status`).

```bash
. ~/.config/openbeast/eval-env.sh                         # PATH, toolchains, OPENBEAST_EVAL_PY (written by eval-host.sh env)
./scripts/eval-host.sh check                              # the harness host's toolchains vs evals/toolchains.lock
python3 evals/run_eval.py --list                          # every task file
python3 evals/run_eval.py --base-url http://127.0.0.1:8000/v1 --tasks 163,190 --no-cache   # a smoke run, no board
python3 evals/profile.py --model-name "<Model>" --base-url https://spark-1.tail4109f9.ts.net:8443/v1 \
    --api-key-env SPARK_KEY --slots 4 --jobs 4 --budget-h 4 --plan-only           # the plan, nothing run
python3 evals/campaign.py run --campaign v5-thinking-first --model-name "<Model>" \
    --base-url https://spark-1.tail4109f9.ts.net:8443/v1 --api-key-env OPENBEAST_SPARK_API_KEY \
    --jobs 4 --slots 4 --dry-run                                                   # every arm's command and ETA
python3 evals/campaign.py status --campaign v5-thinking-first --model-name "<Model>"   # arm table
python3 evals/scoring.py --show                           # the leaderboard
python3 evals/scoring.py --rebuild                        # rescore every results/eval-*.json
python3 tests/prove_task.py --unit <id>_<slug>            # model-free proof of one eval unit
python3 tests/prove_task.py --suite v5 --json /tmp/proof.json   # the whole suite (~1 h)
scripts/eval-era.sh                                       # the current era hash
```

`benchmark_all.py` has a MODELS entry for GLM (`glm-5.3-flash`) but **none
for DeepSeek yet** (2026-11-18), so `--model deepseek-v4.1-flash` fails until
one is added ([MODEL_CAMPAIGN.md §0](MODEL_CAMPAIGN.md#0-onboard-the-model), step 4); use
`--model-name` with `--base-url` meanwhile.

---

## 12. Benches

**`tools/bench.py`** is the DeepSeek recipe's own benchmark, in
`~/openbeast-recipes/deepseek-prep/runtime/tools/` on spark-1 (it is not in
this repo). Stdlib only. It reads the key from `API_KEY` or `API_KEY_FILE`.

```bash
cd ~/openbeast-recipes/deepseek-prep/runtime
python3 -I tools/bench.py --help
API_KEY_FILE=~/.config/openbeast/tensorfold-api-key python3 -I tools/bench.py \
  --url http://127.0.0.1:8000 --model DeepSeek-V4.1-Flash-1017 --suite decode,multi --streams 1,2,4 --reps 2 \
  --label "<what>" --out runs/<name>.json                     # loads the pair: not while Max is working
python3 -I tools/bench.py --compare runs/packs/45ee5d0f.json runs/packs/04c13f12.json   # no server needed
```

Suites: `prefill`, `decode`, `multi`, `needle` (default all four). Thinking is
off unless `--thinking`. `--salt <s>` repeats the same prompts; the default
salt is random so the prefix cache is not read.

In this repo:

```bash
python3 scripts/tokps.py measure --help                       # decode / prefill tok/s of an endpoint (subcommands: measure, check, gate)
scripts/backends/conformance.sh --url <URL> --model <id> --backend tensorfold --key-file <F> [--concurrency 4] [--heavy]
```

---

## 13. Docs, mirrors and offline material

```bash
scripts/docs.sh status                                    # beast-docs: which sources are mirrored here
scripts/docs.sh search --source <name> "<query>"          # search the offline mirror
scripts/docs.sh read <ref>                                # read one page
scripts/docs.sh pack <out.tar>  /  scripts/docs.sh unpack --sha256 <hex> <tarball>
scripts/ark.sh list                                       # beast-ark catalog
scripts/ark.sh verify <drive-dir> --quick                 # an ark drive, presence check
scripts/ark.sh restore <name> --from <drive-dir> --prefix ~/ob-work/opt --bin ~/ob-work/bin
scripts/pydeps.sh verify                                  # the Python lock is sane and current (offline)
scripts/pydeps.sh install --from <wheelhouse>             # hash-pinned install with no index
scripts/bundle.sh verify <dir>                            # an offline bundle, re-hashed
python3 scripts/generate-skill-index.py --check           # exit 1 if the skill menu in system-prompt-tools.md is stale
```

Generated content: the eval counts (`evals/suite_stats.py --markdown`,
checked by `--check`), the v5 board blocks in README and RESULTS
(`evals/v5_board.py`, checked by `--check`), and the skill menu between the
`SKILL_INDEX` markers in `system-prompt-tools.md`
(`scripts/generate-skill-index.py`). Run that last one **without** `--check`
only with Max's agreement: it rewrites `system-prompt-tools.md`, which is
never changed without him, and it has no `--help` (any other argument runs
it). Everything else is hand-written; update it in the same change as the
code.

---

## 14. Emergency recovery

Read [SPARK_HYDRA.md §G8](SPARK_HYDRA.md#g8-when-x-fails-troubleshooting) first.
Each of these is a state change: say what you are doing before you do it.

| Situation | Commands (spark-1 unless noted) |
|---|---|
| The model does not answer, `/health` fails | `systemctl --user status deepseek-v41-inference --no-pager`; `journalctl --user -u deepseek-v41-inference -n 100 --no-pager`; then `systemctl --user restart deepseek-v41-inference` (both ranks; never one rank) |
| The unit hit its start limit (3 failures in 30 min) | `systemctl --user reset-failed deepseek-v41-inference && systemctl --user start deepseek-v41-inference` |
| Answers mid-stream stop, `/health` still ok | restart the unit (a stalled collective; restart all ranks) |
| A service in `healthcheck.sh` is DOWN | `~/openbeast/scripts/healthcheck.sh --restart`; if that fails, `systemctl --user restart openbeast` |
| WebUI / SearXNG DOWN after a reboot, "docker daemon not ready" | the user manager started before the docker group applied: reboot once; `systemd-run --user --wait --pipe docker info` proves it |
| A pack swap failed | the script rolls back by itself; read `runtime/runs/packs/` and `journalctl --user -u deepseek-v41-inference`; `./spark-deepseek-pack.sh list` shows what serves |
| DeepSeek will not come back at all | switch to GLM: `bash ~/openbeast-recipes/deepseek-prep/rollback/restore-glm.sh` (or `beastglm`), then `openbeast-client use spark-hydra` on clients |
| The box freezes or ssh hangs | unified memory exhausted (GB10 freezes rather than OOMs): power-cycle; afterwards check the `Unified memory` line and what was added |
| Tailnet down, LAN up | ssh by LAN IP (`ssh -o HostName=192.168.40.153 spark-1` from the Mac); reach the gate through an ssh tunnel ([TAILSCALE_OFFLINE.md](TAILSCALE_OFFLINE.md)) |
| The internet is down | nothing to do: the island watcher islands the stack after 90 s ([BEAST_ISLAND.md](BEAST_ISLAND.md#on-the-spark-pair)); work continues locally |
| A live-checkout edit went wrong on spark-1 | `cd ~/openbeast && git status --short && git diff`; restore the file with `git checkout -- <file>` only after saving anything Max may want (`git diff > ~/live-diff-$(date +%s).patch`) |
| The fabric hangs after a reboot (MTU mismatch) | `scripts/backends/spark-fabric-mtu.sh --status` on both Sparks; [SPARK_HYDRA.md §B4](SPARK_HYDRA.md#b4-fabric-mtu-9000-spark-fabric-mtu) |
