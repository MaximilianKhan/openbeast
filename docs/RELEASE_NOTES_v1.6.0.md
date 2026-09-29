# OpenBeast v1.6.0 — the review 🔬, multi-engine inference 🟩, and a harness that won't lie

**Every line re-read by an adversary, the rig learns to drive engines it
doesn't own, and the measurement stack stops scoring its own crashes as the
model's mistakes.**

v1.5.0 taught the rig to distrust its measurements. v1.6.0 turned that on
everything else. A whole-repository adversarial review read the code through
eighteen lenses (security, correctness, efficiency, storage, research validity,
CI, docs) and found **118 defects**. None of them was refuted. Two rounds of
fixes closed them, and every fix branch was itself reviewed for regressions
before it merged. One finding reached further than code: a published research
verdict was built partly on rows the model never produced. That verdict is
withdrawn here and re-audited in the open.

Alongside the repair: OpenBeast can now drive **vLLM** or **TensorFold**,
including tensor-parallel across two **NVIDIA DGX Sparks**, with tooling to
bring up checkpoints nobody has seen before. Also new: beast-lang's escalation
wired as a real switch, and a safe way to clear opencode sessions on any
device.

v1.5.0 → v1.6.0: 16 merged PRs (#94 – #109), 217 commits, 368 files changed.

---

## ⚠️ Read before upgrading

These are the behaviour changes. Most of them close a hole, and a few can
surprise a working rig.

1. **The eval era rolls.** `3b7c2adb8da7968d` (v1.5.0) becomes
   `b5596c660b5ab819`. `agents/runner.py` and `agents/tools.py` are both in the
   era hash. **Never pair a row measured before this release with one measured
   after it.** The Python client also moved (openai 3.13.0 → 3.22.0), so treat
   older rows as a different instrument in any case.
2. **The zig awareness pack's SHIP verdict is withdrawn.** On clean rows it is
   **unresolved** (see [Research integrity](#research-integrity--the-verdict-we-withdrew)).
   The pack stays unwired in production until a clean rerun decides it. The
   per-model gating plan that relied on the champion's "negative" guard is
   withdrawn too: that signal was an artifact.
3. **A keyless tool server on a network bind now refuses to start.** If
   `BIND_HOST` is not loopback and neither `MCPO_ADMIN_KEY` nor
   `MCPO_GUEST_KEY` is set, `:3001` (bash included) will not serve. Run
   `./scripts/setup-mcpo-keys.sh`, or set `ALLOW_OPEN_TOOLS=true` to accept the
   exposure explicitly. The default loopback bind is unaffected.
4. **Open WebUI's built-in admin password is rotated.** With `WEBUI_AUTH=false`
   (the default), Open WebUI creates `admin@localhost` with a password that is
   hardcoded upstream. Once login is enforced, `configure-webui.sh` and
   `setup-tailscale.sh` now sign in with that default; if it still works, they
   rotate it to a random password stored in the 0600 `openbeast.conf`
   (`WEBUI_ADMIN_PASSWORD`). `setup-tailscale.sh` will not publish the WebUI if
   the rotation fails, and `doctor` fails while the default still works.
5. **Boolean config keys parse strictly, and the same way everywhere.**
   `WEBUI_AUTH=true   # comment`, `EDGE_GATE=1` and `yes`/`on` were read as
   *false*: the login wall and the gate failed **open**. Every true/false key
   now goes through one parser: first token only, inline comments stripped,
   true/yes/1/on vs false/no/0/off, and anything else warns. **A rig that
   "worked" with a commented `WEBUI_AUTH=true` was running without a login;
   after upgrading, it enforces one.**
6. **A model counts as up only when it answers `{"status":"ok"}`.**
   llama-server replies `503 Loading model` from the moment it binds, and
   `curl -s` counted that as healthy. So model-load rollback never fired,
   last-good was overwritten by a model that then OOMed, KV warming never ran,
   and `-d` printed "Stack is up" mid-load. Readiness now waits for a real 200
   with a deadline (`OPENBEAST_LLAMA_LOAD_GRACE`, 900 s). **Expect `./start.sh
   -d` to return later than before, when the model is actually serving.**
7. **Secrets are off the command line.** llama-server gets its key through the
   `LLAMA_API_KEY` environment variable, which it reads natively, instead of
   `--api-key`. Every curl that carries a bearer or locality token reads the
   header from a config on a file descriptor (`scripts/lib/curl_auth.sh`).
   Keys no longer appear in `/proc/*/cmdline`, which every local user can
   read.
8. **Tool-running processes are non-dumpable.** The tool server, MCP server
   and runner set `PR_SET_DUMPABLE=0` before their first shell, so a model's
   shell can no longer read their secrets from `/proc/$PPID/environ`. To attach
   py-spy or gdb, set `OPENBEAST_KEEP_DUMPABLE=1`.
9. **`fetch` got limits.** A 45 s whole-request deadline covers connect, TLS,
   headers, redirects and body. Output is capped at 200 000 chars
   (`OPENBEAST_FETCH_MAX_CHARS`, up to the old 2 M). The HTML stripper now runs
   in linear time, and `http(s)_proxy` actually works.
10. **Agent turns carry `max_tokens`**: the reasoning budget plus 12 288 (`-1`
    means uncapped; `OPENBEAST_AGENT_MAX_TOKENS` overrides). The OpenAI client
    retries once, not twice. Eval runs stay uncapped.
11. **`update.sh --llama` refuses while someone else holds the GPU lease**, and
    a failed rebuild rolls `llama.cpp/build/bin` back from a reflinked snapshot.
    A half-built mix of new libraries and an old binary can no longer be served.
12. **Log rotation installs itself**, as a daily `systemd --user` timer
    (`LOGROTATE_AUTOINSTALL=false` opts out). It now covers the chat, artifact
    and extension logs too. New: `AGENT_LOG_RETENTION_DAYS` (default 0 = keep
    forever).
13. **The VRAM floor is the 24 GB class** (3090 / 4090 and up; the threshold is
    22 000 MiB, so a 3090's 24 564 passes). Bootstrap refuses smaller cards;
    `OPENBEAST_FORCE_VRAM=1` proceeds, unsupported.
14. **Dependencies.** openai 3.22.0, fastapi 0.142.0, uvicorn 0.54.0, PyJWT
    2.15.1 (hash-pinned lock regenerated). Run `./scripts/update.sh --python`
    (or `./bootstrap.sh`), then `./stop.sh && ./start.sh -d`.

**After upgrading:** run `./scripts/doctor.sh`. It now checks every one of the
above: bind scope, the default admin, `:443` publishing with login off, the
log-rotation timer, and `INFERENCE_*` consistency.

---

## The review 🔬 — 118 findings, 0 refuted

**Method.** Eighteen reviewers each owned one area and one lens: the tool
arsenal, identity/RBAC, beast-gate + `/api/slot`, beast-artifact, beast-chat,
beast-lang, process lifecycle, supply chain, network exposure, the eval
harness, research statistics, efficiency, storage, CI and test validity, docs
drift, secrets and crypto, extensions + client, and the open PRs. Every batch
went to an adversarial verifier instructed to **refute**, defaulting to refuted
when in doubt. Every high or critical finding that survived went to a third,
independent skeptic. Findings carried a failure scenario and evidence, most
with a reproduction on the rig itself.

**Result.** 118 findings, none refuted, many downgraded: **8 high, 43 medium,
67 low**. They were fixed in two rounds by 21 fixers working on disjoint files
in separate worktrees. Every fix branch was then reviewed adversarially for
regressions before it merged; that pass found real ones and sent them back.
Behavioural fixes carry regression tests, each checked against the old code
(where it must fail), and test runs no longer write to the rig's live audit
logs.

### The eight highs

| Finding | What was wrong | Now |
|---|---|---|
| `RLIMIT_NPROC=2048` was uid-global | Linux counts **every thread the user owns** against it. A desktop idles at ~1 400, so tool commands and eval validators died with fork/thread EAGAIN, and those deaths were **cached as model failures** | The cap is the uid's live task count + 4 096, measured per call before the spawn. It still stops a fork bomb |
| Server death cached as a real FAIL | The runner caught the API error, burned its remaining iterations and exited 0 with the tokens it had, so a dead llama-server became a permanent capability failure | The runner prints `API_ERRORS: n`; `run_eval` records `server_error` / `env_error` rows as infrastructure, never as a cached FAIL |
| The reasoning-budget era read the FIRST flag | llama-server obeys the **last** `--reasoning-budget`, and `serve.sh` appends the global override on purpose, so uncapped rows could be cached under a capped era | The last occurrence wins, in both `--flag v` and `--flag=v` forms |
| The row-validity guard waved harness deaths through | `setup_failed` / `server_unhealthy` rows (no exit code, 0 s) were classified as benign "cached" hits, which real cache hits never look like, so a contaminated or truncated row printed "row clean" | Classified from the row's own fields and counted against validity; a paired p-value is refused on an incomplete row. Both copies (repo and research) agree |
| `503 Loading model` counted as healthy | Model-load rollback could never fire, and a broken serve script overwrote last-good | Real readiness with a deadline (upgrade note 6) |
| beast-gate forwarded unparseable JSON verbatim | A body too deep for Python's parser skipped the slot pin, the `id_slot` strip and usage metering, and delivered a stack-overflow payload to the shared llama-server | Non-UTF-8, non-object, unparseable or over-deep bodies get a **400**, from a linear pre-scan before `json.loads` |
| Default `admin@localhost` survived auth-on | A known upstream password on a tailnet-published WebUI means admin, which means bash | Rotated; publishing is refused on failure; doctor checks it (upgrade note 4) |
| The Tier-3 SHIP rested on contaminated rows | See below | Withdrawn and re-audited |

### By area (the full list is in the [appendix](#appendix--all-118-findings))

- **Tool arsenal.**
  - The `/proc/$PPID/environ` secret read is closed.
  - `write_file` / `edit_file` are atomic: temp file, fsync, rename. They keep
    the target's mode and owner, still refuse read-only files, and a full disk
    can no longer truncate your file.
  - The write guard covers the code-execution targets it missed: git hooks and
    config under any `.git`, shell login files, and files a live shell rc
    sources.
  - `fetch` got its deadline, its proxy support and its linear stripper.
  - **Tool arguments are coerced to their schema types** (a `"30"` timeout
    becomes 30), for servers that send strings.
- **Identity / RBAC.**
  - The router's admin-only spawn gate no longer fails open in JWT mode.
  - Router-spawned agents are attributed and stay in the caller's shard.
  - A single configured RBAC key no longer breaks every WebUI tool call.
  - A non-ASCII bearer token is denied (403) and audited, not a 500 with a traceback.
  - Audit rows for denied calls no longer present forged identities as
    verified.
  - Tests write audit rows to a temp dir, never to the live `.run/`.
- **beast-gate / beast-slot.**
  - The in-flight cap counts upstream generations, so a prompt array or `n`
    can no longer take every slot.
  - Oversize JSON replies are metered.
  - Aborted streams are audited as `client_disconnect` (tokens null; a count
    would be invented) and mid-stream faults as `upstream_error`.
  - Unauthenticated denials are throttled in the log.
  - The locality token is minted only after the port binds.
  - `/api/slot` reports `auth=anon` only while no device is enrolled.
- **beast-artifact / beast-chat.**
  - Identity headers are trusted only from loopback peers (tailscale serve),
    so a LAN bind no longer turns a forged `Tailscale-User-Login` into a read.
  - Artifact audit rows are budgeted per identified login.
  - Stopping an agent kills its in-flight tool commands, on every stop path.
  - macOS clients no longer reconcile every live session to `lost`.
  - Caller metadata can't forge `boot_id`.
  - Transcripts are created 0600 in a 0700 directory.
  - `doctor` probes beast-chat where it actually binds.
- **beast-lang.** The sandbox refusals were hardened:
  - Rust macro indirection;
  - C/C++ `#line`, the GCC dependency pragma, `_Pragma` with token pasting,
    universal character names and aliased `__has_include`;
  - the environment allow list reduced to exact names.

  The verifier no longer reports false compile failures, which had produced
  false VERIFIED claims. Escalation cards are "Confirmed" only for members the
  index actually knows.
- **Lifecycle.**
  - One probe-host helper (`scripts/lib/net.sh`) makes every probe follow
    `BIND_HOST`, including `::` and specific addresses.
  - The watchdog respects a stack you stopped on purpose, and stops restarting
    after the supervisor gives up.
  - Extension pidfiles are identity-checked before any signal.
  - `ext.sh` validates extension names.
  - The supervisor identity check no longer matches any command line
    containing `start.sh`.
  - `gpu-lease.sh` won't delete a lease that someone else took over with
    `--force`, and it tracks leased descendants by an inherited token.
  - `measure-vram.sh`, the MTP profilers and `benchmark_all.py` kill only what
    they launched, never by bare name, and refuse under a foreign lease.
- **Supply chain.**
  - A weight that fails its sha256 pin is never left under its final name.
  - Client installs, `openbeast-client update` and `agent.sh` install from the
    hash-pinned lock.
  - The signed bundle manifest now covers the lock, so a USB stick can't carry
    its own trust root.
  - Bundle install verifies what it reads, not a re-read of the medium.
  - Every GitHub Action is pinned by commit SHA.
  - The relock workflow's resolve step can no longer plant a hook that runs
    with a write token.
  - `land-dependabot.sh` never approves runs from forks.
  - `verify-weights.sh` fails on a file not in the registry.
- **Destructive operations.** `uninstall.sh`:
  - resolves paths through the stack's own resolver, never the caller's
    working directory;
  - removes only this compose project's volumes;
  - keeps `.run/` (device registry, audit trails, keys) unless you pass
    `--purge-state`;
  - keeps a `llama.cpp/` that holds local-only work;
  - refuses `/`, system trees, `$HOME` and the checkout.
- **Storage.**
  - Log rotation is actually installed and covers every log.
  - Transcript retention is opt-in.
  - The prune script never takes the compose-pinned images, a live server's
    weight, or any shard of a split GGUF it keeps.
  - A full disk stops an eval sweep instead of banking FAILs.
- **Efficiency.**
  - Proactive compaction has hysteresis: it frees down to 50 % of the budget,
    so the prefix cache survives many turns.
  - One giant tool result is stubbed first instead of wiping the whole
    history.
  - Agent turns carry `max_tokens`.
  - `benchmark_all` stops waiting for a server that has already died, and
    skips the needless cool-off.
- **Eval harness.**
  - `--cache-only` is never seated on the leaderboard (rescore with
    `scoring.py --rebuild`).
  - Experiment arms (greedy, packs, escalate) are enforced as
    leaderboard-ineligible.
  - The cache key fingerprints weights, engine and toolchains, with an opt-in
    env era.
  - The server fingerprint comes from the process on the eval's port, not
    pgrep's first match.
  - A timeout kill reaps the agent's in-flight tool commands.
  - A recurring env error is eventually banked as a real FAIL
    (`OPENBEAST_EVAL_ENV_ERROR_BANK_AFTER`, 3).
- **CI.**
  - Every hermetic shell suite runs in CI; `extensions/` is inside the
    shellcheck, `bash -n` and ruff gates.
  - The bundle's signature-required and hash-verify gates have tests.
  - Tests that could not fail were fixed. Examples: `unittest.main()` sitting
    mid-file, and a rebuild test that depended on the host's GPU detection.

**Deliberately not closed**, each with its reason recorded in
[`docs/TODO.md`](TODO.md):

- **A same-uid shell can still read `openbeast.conf`.** Landlock is
  allow-list-only, so it can't hide one file from an otherwise free shell.
  Sandlock by default changes eval behaviour and needs a GPU measurement first;
  a separate uid needs system setup. Documented in
  [`RBAC_PLAN.md`](RBAC_PLAN.md).
- **`PR_SET_PDEATHSIG` for tool children.** It fires when the spawning
  *thread* exits, and the servers spawn from worker threads, so it would kill
  live commands. A stop handler covers the stop paths instead.
- **Aborted streams keep a null token count** rather than an invented one.

## Research integrity — the verdict we withdrew

Two of the highs above (the uid-global thread cap, and server deaths cached as
failures) were not hypothetical. They had already written rows into the eval
cache, and a published verdict paired against those rows.

**The Tier-3 zig awareness pack** (v1.5.0-era record: *SHIP, net +13, McNemar
p = 0.019*) was re-audited from the six results files, excluding every row
the model did not produce. Contaminated rows were matched to their agent logs
by token counts.

| Read | Rescues b | Regressions c | Net | Exact p | Pre-registered call |
|---|---:|---:|---:|---:|---|
| As registered | 20 | 7 | +13 | 0.0192 | SHIP |
| **Clean rows** | **17** | **7** | **+10** | **0.0639** | **no-ship** |
| Sensitivity: keep `62_crt_f` (lost 2 of 25 iterations) | 18 | 7 | +11 | 0.0433 | ship |

**The verdict is unresolved, not refuted.** The direction holds in both
replicates, and the iterations-to-fix gain still stands (−3.2, sign-test
p = 0.008). The champion-model guard's "negative direction" (−7), which
motivated gating the pack per model, was mostly thread-exhaustion artifact:
on clean rows it is +3 over 14 units, which tells us nothing. Two more
caveats are now written down:

- Greedy decoding at `--jobs 4` against `-np 6` churns about 30 % between
  identical runs, not "near zero".
- The +10 is an in-sample estimate. The pack was scoped to failures seen on
  the same units it was scored on, and a held-out design is in
  [`LANG_AWARENESS_PLAN.md`](LANG_AWARENESS_PLAN.md) §5.

**The IQ3 verdict** (GSQ-RCO-IQ3_S over UD-IQ3_S) is clean on both rows and
**unchanged**: net +12, p = 0.012.

**What was done about it:**

- **75 contaminated cache entries moved, not deleted,** into
  `evals/cache-quarantine-2026-09-29/`, with a manifest giving each one's
  reason.
- **`scratch/tier3_verdict.py` drops contaminated rows by default.** `--raw`
  reproduces the registered read, and `--keep` runs sensitivities.
- **`greedy_floor.sh` gained a `--single-slot` mode.** It refuses to measure a
  server it can't verify is in that regime.
- **The full re-audit is committed** at
  `scratch/tier3-verdict-reaudit-2026-09-29.txt`.

A fresh Tier-3 rerun takes about 7 GPU-hours in the new era, and the
single-slot floor about 20; both are ready to run and not yet scheduled.

## Multi-engine inference 🟩 — vLLM, TensorFold and DGX Spark (opt-in)

*(#105, #107.)* The rig's own inference stays llama.cpp, byte for byte. Set
`INFERENCE_BACKEND=vllm` or `tensorfold` and `INFERENCE_URL`, and the whole
stack talks to that server instead: WebUI, tool servers, agents, beast-gate,
`/api/slot` and doctor.

- **Hands off what it doesn't own.** A remote backend defaults to
  `INFERENCE_MANAGED=false`, so start, stop and the watchdog never launch,
  restart or kill it. Every llama-only step says "not applicable" instead of
  failing. If the server isn't up at boot, the stack still comes up, warns,
  and logs the moment it becomes ready.
- **Per-backend readiness.** llama needs `{"status":"ok"}` (503 = loading).
  vLLM accepts an empty 200. TensorFold needs `{"ok":true}`.
- **`/api/slot` for vLLM** reads `max_model_len` from `/v1/models`, and
  running, waiting and KV use from `/metrics`. The llama answer is unchanged;
  the contract stays v2.
- **Runner:** vLLM and TensorFold context-overflow errors are recognised,
  token counts included.

**Built for models nobody here has seen.** Nothing is hard-wired to a
checkpoint:

| Step | Tool | What it guarantees |
|---|---|---|
| Inspect | `scripts/backends/model-inspect.sh <repo@sha \| dir>` | Reads metadata only: architecture, quantization, weight bytes, context, MoE, KV per token, fit on one Spark vs two, parser suggestions from the chat template, and engine support checked against lists vendored from vLLM `a96ee59` and TensorFold `6b2e4c4`. `--write-profile` drafts a profile with `# VERIFY` markers; checkpoint text lands only in sanitised comments, and the draft is re-parsed before it is written |
| Profile | `scripts/backends/models/<name>.env` | Parsed as data, never sourced. A Hub revision must be a full SHA; `trust_remote_code` needs an ACK equal to that revision; extra flags go through a per-engine **allow list** (normalised spellings, prefix-resolved, short options refused, a never-list for secrets, pins and launcher-owned flags); a speculative draft model must be pinned too |
| Lock | `scripts/backends/model-fetch.sh --profile <name>` | Verifies every file against the Hub's LFS sha256 or git-blob sha1 at the pinned revision; for a local copy (models copied onto a Spark's own storage) it hashes in place with no network. **The lock is the pin:** a second Spark or a re-copy must reproduce it byte for byte. Staged download with a crash-safe publish, stdlib only, and the token never crosses origins |
| Launch | `scripts/backends/{vllm,tensorfold}/spark-node.sh --profile <name> --rank 0\|1` | `--print` dry run. Serves only a verified copy, mounted read-only with the hub offline. Refuses a wildcard bind, a key file others can read, an unpinned image, or a TensorFold ref that is not a commit SHA. The vLLM key goes through `VLLM_API_KEY`, never argv |
| Test | `scripts/backends/conformance.sh --model <id>` | A black-box probe: model listing, unknown-id 404, chat, streaming, which field reasoning uses, a real tool-call round trip with OpenBeast's own schemas, parallel calls, `max_tokens`, and overflow text against the runner's detector. Exit 0 only when everything OpenBeast needs passes |
| Use | `scripts/backends/use-model.sh --profile <name>` | Writes `INFERENCE_MODEL` only behind a passing report for that exact id and URL; the runner sends it, and doctor checks it matches |

**Honest status.** Every engine-specific detail comes from source, official
recipes and stub servers, not from a Spark. The list of what to verify on
hardware is §12 of [`DGX_SPARK_PLAN.md`](DGX_SPARK_PLAN.md), and the day-of
runbook is §14. Two findings from the research that shape the setup:

- **DGX OS stays on the Sparks.** Omarchy does not support the Spark's ARM64
  GB10 today, and NVIDIA supports only DGX OS.
- **The second Spark pays off for models too big for one box.** A 27B fits on
  one, and splitting it costs network latency on every token.

## beast-lang escalation, wired 📚

*(#103, superseding #90.)* A beast-assist compile error can now arrive with
the one-line fix the installed toolchain confirmed. Enable it with
`BEAST_ESCALATE=1` alongside `BEAST_ASSIST=1`; it is byte-identical when off.
Before it merged:

- `BEAST_ESCALATE` in `openbeast.conf` now actually reaches the stack.
- The escalation cache era keys on the treatment as delivered: the index, the
  claims that supply the card text, the selection logic, and the gate
  outcome.
- `--escalate` rows are leaderboard-ineligible, and that is enforced.
- The matcher gives "Confirmed" only for a member the index knows, and
  campaign scripts strip the switch.

Its A/B is still to run.

## Also in this release

- **`scripts/opencode-sessions.sh`** *(#108)* shows and safely clears this
  device's opencode sessions. The storage location is the same on the rig and
  on a Mac, `~/.local/share/opencode/opencode.db`: opencode uses the XDG
  directories on every OS, with no `~/Library` case, confirmed in its source.
  `clear` is a dry run until `--go`, and:
  - refuses while opencode is running;
  - backs up with `VACUUM INTO` (0600);
  - deletes through opencode's own cascade, verified to leave zero orphans;
  - scopes `--dir` by exact path prefix;
  - then reclaims the space. In WAL mode a bare VACUUM only lands in the
    `-wal` file, so a truncating checkpoint follows; a 2.4 GB database became
    244 KB on a test copy.
- **`scripts/uninstall.sh`** *(#96)*: the rig's decommissioning path. It is a
  dry run until `--go`, and keeps weights, config, chats and the workspace
  unless you pass the matching `--purge-*` flag. Hardened again by the review.
- **`update.sh --python` carries every pin forward** *(#94)*, comments
  included. Before, the first bump after `httpx` was pinned would have dropped
  it, and router.py and edge.py with it.
- **The healthcheck follows `BIND_HOST`** *(#97)*. A LAN-bound rig had its four
  healthy services restarted every five minutes.
- **`docs/TUTORIALS.md`** *(#98)*: a copy-pasteable walkthrough for every
  beast-* feature, checked against the scripts. The campaign tooling and
  review records moved into git (`scratch/`, `docs/reviews/`) *(#99)*.
- **Found by the post-merge smoke test** *(#104)*. The first real boot after
  #102 caught two false greens that no test could:
  - doctor passed a `:443` WebUI whose live login state it couldn't read;
  - `start.sh -d` listed Open WebUI as up before its container had started.

  Both are fixed and pinned by tests.
- **The README** was rewritten around everything above *(#109)*.

## By the numbers

| | v1.5.0 | v1.6.0 |
|---|---|---|
| Python tests (`pytest tests/`, measured at each tag) | 1 297 | **1 873** |
| Hermetic shell suite files | 6 | **15** |
| Shell suites CI runs as their own step | 2 | **13** (+2 run inside `test_scripts.sh`) |
| Largest shell suites | — | test_scripts 319 · test_backends 133 · test_offline_fixes 100 · test_clients 73 · test_artifact_cli 68 |
| Review findings open | — | 118 found → **all closed or deliberately scoped** (see appendix) |
| Eval era | `3b7c2adb8da7968d` | `b5596c660b5ab819` |
| Inference engines | llama.cpp | llama.cpp · vLLM · TensorFold |
| MCP / WebUI tools | 18 | 18 |

Verified on `main` at the release commit: every suite passes. The one local
exception is `test_artifact_cli`'s "port 3004 is free" check, which fails on
this rig only because the live artifact server holds that port; CI runs all
68 checks green. ruff (`E9,F`) and shellcheck (`-S error`) are clean.

**Real-stack smoke test.** Booting the actual rig after #102 gave `doctor` 29
ok and 0 failures, and it found the two false greens fixed in #104. The later
`conf.sh` changes in #105 and #107 were verified in sandboxes only: old vs new
`conf.sh` compared under `set -euo pipefail` across six configs, plus the
start, supervisor, healthcheck, stop and doctor flows run against recording
stubs. After upgrading, restart once from your own terminal (`./stop.sh &&
./start.sh -d`) and run `./scripts/doctor.sh`.

---

## Appendix — all 118 findings

Severity is the final one after verification. "Fixed" means a merged change
with a regression test, or for docs and data a merged correction. Anything
else says what it is.

### Tool arsenal (tools.py / MCP) (6)

| Sev | Defect | Status |
|---|---|---|
| high | RLIMIT_NPROC=2048 counts every thread the user owns, so bash and eval validation fail with fork EAGAIN, and those failures are cached as model failures | Fixed: NPROC cap = live uid task count + 4096, measured before spawn. Poisoned cache rows quarantined |
| medium | fetch has no total deadline: a slow-dripping URL holds a tool-server worker indefinitely, and guest users can call fetch | Fixed |
| medium | fetch breaks completely when http_proxy/https_proxy is set: it dials the target IP on the proxy's port | Fixed |
| low | The bash-tool env scrub is bypassed by reading the tool server's own /proc/<ppid>/environ (JWT secret, RBAC keys) | Fixed: `/proc/$PPID/environ` closed (non-dumpable). Same-uid `openbeast.conf` read remains, documented (RBAC_PLAN) |
| low | The write-file guard blocks .git/config and ~/.bashrc but misses equivalent code-execution targets, including ~/.bashrc_custom, which exists on this box | Fixed |
| low | test_fetch_guards.py places unittest.main() in the middle of the file, so the documented direct run skips the DNS-rebinding, /proc-hazard and tailnet tests | Fixed |

### Identity & RBAC (7)

| Sev | Defect | Status |
|---|---|---|
| medium | Agent router's admin-only spawn gate fails open in JWT mode, and this rig runs that exact config | Fixed |
| medium | The test suite writes thousands of synthetic rows (including 'prober' denials and '../../etc' identities) into the live security audit log | Fixed |
| medium | The tool server serves unauthenticated bash on a non-loopback BIND_HOST, with no refusal and no warning for specific IPs | Fixed: the tool server refuses a keyless non-loopback bind unless `ALLOW_OPEN_TOOLS=true` |
| low | The bash tool's secret scrub can be bypassed through /proc/$PPID/environ, so any admin-tier session (or prompt injection) gets the RBAC keys and the JWT signing secret | Fixed as above; same-uid conf read documented as an architectural limit |
| low | Audit rows for denied calls record forged identities as if verified, and a non-ASCII bearer token returns a 500 with no audit row | Fixed |
| low | Router-spawned agents are unattributed and bypass the caller's workspace shard | Fixed |
| low | A single configured RBAC key breaks every WebUI tool call, because configure-webui.sh and the server disagree on what 'keyed' means | Fixed |

### beast-gate & /api/slot (5)

| Sev | Defect | Status |
|---|---|---|
| high | Gate forwards bodies it cannot parse verbatim, so a deeply nested body keeps a client-chosen id_slot, skips include_usage, and delivers a stack-overflow payload to llama-server | Fixed |
| medium | In-flight cap counts HTTP requests, not upstream generations: prompt arrays and n/n_cmpl let one admitted request occupy every slot | Fixed |
| low | Audit metering drops token counts for non-streaming replies over 256 KB and for streams the client aborts, and records them as outcome=ok | Fixed: oversize JSON replies metered; aborted streams audited as `client_disconnect` (tokens null by design) |
| low | /api/slot advertises auth="anon" whenever EDGE_ALLOW_ANON=true, but the gate ignores ALLOW_ANON once any device is enrolled and 401s keyless callers | Fixed |
| low | Unauthenticated denials write an unthrottled line to stdout/.run/stack.log, the unbounded growth the audit file was deliberately protected from | Fixed |

### beast-artifact (3)

| Sev | Defect | Status |
|---|---|---|
| medium | The artifact server follows BIND_HOST, so on a LAN-bound rig anyone who can reach :3004 can read every private artifact by forging Tailscale-User-Login | Fixed |
| low | The shell sets no Cross-Origin-Opener-Policy, so any website Max visits can count frames to learn whether an artifact id/version exists and is visible to him | Fixed |
| low | scripts/artifact.sh always dials 127.0.0.1 and setup-tailscale proxies 127.0.0.1:3004, so on a rig bound to a specific LAN address the CLI and the tailnet mount both reach nothing | Fixed |

### beast-chat & sessions (7)

| Sev | Defect | Status |
|---|---|---|
| medium | Stopping an agent mid-tool-call leaves the tool's command running as an orphan with no wall-clock timeout | Fixed: every stop path kills in-flight tool process groups. PDEATHSIG deliberately not used (thread-scoped) |
| medium | On macOS clients (no /proc) every live session reconciles to 'lost' at once; job.sh stop refuses and the owner's own verdict is rejected | Fixed |
| medium | OPENBEAST_CHAT_BIND set to a non-loopback address turns a forged Tailscale-User-Login header into a full transcript read, with no guard or warning | Fixed |
| low | Caller meta 'boot_id' is not in RESERVED_META: an agent spawned through the API can be born 'lost' (the pid_start fix, incomplete after [54]) | Fixed |
| low | doctor.sh probes beast-chat on BIND_HOST instead of OPENBEAST_CHAT_BIND, so it reports a false FAIL on any rig with a specific-IP BIND_HOST | Fixed |
| low | Each console-started session gets its own 50%-of-RAM scope cap, with no aggregate bound, so two runaway jobs (or one plus the stack) can still OOM the box | Fixed |
| low | Agent transcripts that the console streams are created 0644 in a 0755 agents/logs/, while job transcripts are deliberately 0600 | Fixed |

### beast-lang (4)

| Sev | Defect | Status |
|---|---|---|
| medium | Rust host-file/env refusal can be evaded through macro indirection, and the file's contents reach the synthesis report | Fixed |
| low | Escalation matcher attaches a 'Confirmed' card for any unknown member of a namespace the index knows | Fixed |
| low | Python and zig drivers report spurious compile failures, which make any OLD form 'fail' and yield false VERIFIED claims | Fixed |
| low | C/C++ host-file refusal misses #line redirection, the GCC dependency pragma, and a macro-aliased __has_include | Fixed |

### Process lifecycle (11)

| Sev | Defect | Status |
|---|---|---|
| high | start.sh counts llama-server's '503 Loading model' as healthy, so MODEL_ROLLBACK never fires, last-good gets overwritten and KV warming never runs | Fixed |
| medium | Watchdog restarts a stack the operator stopped on purpose, and restarts without limit after the supervisor gives up | Fixed |
| medium | measure-vram.sh's EXIT trap pkills whatever llama-server is on :8080 (the stack's or a campaign's) and reports a port conflict as an OOM | Fixed |
| medium | benchmark_all.stop_llama_server() uses bare `pkill -f llama-server`, breaking the repo's own ops rule and killing sibling worktrees' and the stack's servers | Fixed |
| medium | Extension pidfiles are still signalled with a bare kill, so the 'stale pidfiles killed strangers' fix is incomplete | Fixed |
| medium | With BIND_HOST set to a specific address, every health probe reports green while WebUI, beast-gate, the router and the KV warmer cannot reach llama-server or the tool server | Fixed: one probe-host helper; MODEL_URL / SearXNG / WebUI URLs follow BIND_HOST (the compose SearXNG self-link stays localhost, cosmetic) |
| medium | BIND_HOST=:: (which conf.sh treats as legal) makes start.sh spin forever in wait_llama_health and doctor report every service down | Fixed |
| medium | uninstall.sh resolves WEIGHTS_DIR and FILES_DIR differently from the stack: relative paths become rm -rf relative to the caller's cwd, and the default sibling layout is silently not purged | Fixed |
| low | gpu-lease.sh run's EXIT trap deletes the lease file even after someone else --force took it over | Fixed |
| low | The lease's 'still in its group' guarantee misses the one process on the card: benchmark_all launches llama-server with start_new_session=True | Fixed |
| low | The supervisor identity check matches any command line containing 'start.sh', so after a reboot stop.sh can SIGTERM and then SIGKILL an unrelated process | Fixed |

### Supply chain & destructive ops (11)

| Sev | Defect | Status |
|---|---|---|
| medium | uninstall.sh re-implements WEIGHTS_DIR/FILES_DIR resolution: relative paths resolve against CWD (rm -rf out of scope), ~ is never expanded, and the default layout is wrong | Fixed |
| medium | --purge-data deletes every Docker volume named *_open-webui-data, including other compose projects' volumes | Fixed |
| medium | bootstrap leaves a weight that failed its sha256 pin under its final name, and the next bootstrap run accepts it as "already downloaded" | Fixed |
| medium | Air-gap wheelhouse path trusts a lock file carried on the same USB stick; pydeps verify accepts a lock whose hashes were replaced | Fixed |
| medium | Client installer (and agent.sh) install from requirements.txt without --require-hashes, bypassing the hash-pinned lock on the machines that run the tool arsenal | Fixed |
| low | After a bundle install, update.sh --images pulls new images but never repins compose, prints nothing about it, and reports the update as done | Fixed |
| low | bundle install re-reads the source tarball and image tarballs from the (untrusted) medium after verifying them | Fixed |
| low | dependabot-relock: code run by the resolve step can plant a git hook that runs in the push step with the contents:write token | Fixed |
| low | land-dependabot.sh approves every action_required run whose head branch name matches, including runs from fork PRs | Fixed |
| low | verify-weights.sh --file <name not in registry> exits 0 ('0 failures') | Fixed |
| low | uninstall --go (no purge flags) rm -rf's llama.cpp/ even when it holds local-only branches or uncommitted work | Fixed |

### Network exposure (6)

| Sev | Defect | Status |
|---|---|---|
| high | admin@localhost stays a WebUI admin with a known password after setup-tailscale.sh turns auth on; configure-webui.sh creates that account on every fresh install | Fixed: default admin rotated when auth is on; publish refused on failure; doctor FAIL; docs corrected |
| medium | WEBUI_AUTH (and EDGE_GATE) fail OPEN on an inline comment or on 1/yes; the OFFLINE fix was applied to one key only | Fixed |
| low | setup-tailscale.sh publishes the WebUI while the running container still has auth off, and silently keeps an explicit WEBUI_AUTH=false | Fixed |
| low | Bearer secrets are placed on process argv (world-readable /proc/*/cmdline): LLAMA_API_KEY for llama-server's whole lifetime, and the WebUI admin JWT and device keys on curl | Fixed |
| low | The client-mode SearXNG image pin drifted from the rig's pin and nothing bumps it | Fixed |
| low | The client install and update path installs Python deps without hashes, with transitive deps floating, while the rig path is hash-locked | Fixed |

### Eval harness (7)

| Sev | Defect | Status |
|---|---|---|
| high | Server death mid-task is saved to the cache as a real FAIL; the Tier-3 SHIP verdict rests on those rows | Fixed: `API_ERRORS: n` + `server_error` rows. 75 entries quarantined; Tier-3 re-audited |
| high | Reasoning-budget era takes the FIRST --reasoning-budget flag; llama-server obeys the LAST | Fixed |
| high | RLIMIT_NPROC=2048 is per-USER, not per-tree: validation/setup forks can fail from desktop load and are saved as model FAILs | Fixed: env exhaustion is an `env_error`, never a cached FAIL (with tools-mcp-security-1) |
| medium | Documented `benchmark_all.py --cache-only` leaderboard rebuild seats a 0% row; can never hit current-era keys | Fixed |
| medium | Cache key does not identify the model weights, the llama.cpp build or the validator toolchains, so stale replays go unnoticed | Fixed: weights / engine / toolchain fingerprint; opt-in env era |
| low | Greedy/packs/diag/capped experiment rows are called 'leaderboard-ineligible', but nothing enforces it | Fixed |
| low | Timeout kill leaves the agent's in-flight bash tool command running; it then races the next variant in the shared fixture dir | Fixed |

### Research statistics (5)

| Sev | Defect | Status |
|---|---|---|
| high | Row-validity guard labels setup failures and server aborts as benign 'cached' rows, so a contaminated or truncated capability row gets stamped 'row clean' | Fixed in both copies (repo + research); verdicts refuse incomplete rows |
| medium | The Tier-3 design's 'greedy = near-zero churn' premise is falsified by the Tier-3 data, and the pending greedy-floor stage cannot measure what it claims to | Fixed: churn claims corrected (~30 %), `--single-slot` floor mode that verifies its regime. The GPU measurement itself is not yet scheduled |
| low | The Tier-3 +13 is an in-sample estimate: the pack's curated section was scoped to failures observed on the same 30 zig units it was then scored on, and there is no held-out set | Documented: in-sample caveat + a held-out design (LANG_AWARENESS_PLAN §5) |
| low | patchup_replace.py rewrites task rows but leaves summary and fast_suite stale: the UD-IQ3 file says 73 passed / capability 97.42 while its rows say 76 / 97.53 | Fixed |
| low | The untracked in-repo copy of e32_cap_verdict.py is the stale pre-fix version and diverges from the canonical research-repo script | Fixed: the in-repo copy is the current classifier |

### Efficiency (5)

| Sev | Defect | Status |
|---|---|---|
| medium | fetch() HTML stripping regexes run in quadratic time and hold the GIL, so one hostile page can freeze the shared tool server for minutes to hours | Fixed |
| medium | Proactive compaction has no hysteresis: once an agent passes 70% of its budget, nearly every turn stubs one more old result and forces a near-full KV re-prefill | Fixed |
| low | One oversized tool result makes overflow compaction wipe the entire useful history first, then re-prefill from scratch | Fixed on both paths: an oversized result is stubbed first; older history is never spent on the low-water gap |
| low | benchmark_all waits the full 180 s for a server that already died, then sleeps 600 s of 'thermal' cool-off even when nothing ran on the GPU | Fixed |
| low | Agent completions send no max_tokens and use the OpenAI client's default 600 s timeout with 2 automatic retries, so one degenerate turn can burn about 30 minutes of GPU | Fixed |

### Storage (7)

| Sev | Defect | Status |
|---|---|---|
| medium | write_file/edit_file truncate the user's existing file in place, so a failed write (ENOSPC/EFBIG) destroys the original content | Fixed |
| medium | A disk-full failure during an eval unit is cached as a deterministic FAIL and replays forever in later campaigns | Fixed |
| low | uninstall.sh --go without purge flags deletes .run/, which holds the device registry, every audit log and the artifact raw-URL key | Fixed |
| low | Log rotation is marked DONE but is never installed, and the config omits chat-audit, artifact-audit and ext logs; artifact-audit has no bound for identified callers | Fixed: the timer auto-installs, covers every log, doctor checks it; opt-in `AGENT_LOG_RETENTION_DAYS` |
| low | prune script's docker image prune can delete the pinned open-webui/searxng images, contradicting its own 'NOT open-webui/searxng' guarantee | Fixed |
| low | 13 GB of byte-identical research GGUFs, including a 10.9 GB unreferenced E13 rr2 copy | Fixed: identical copies reflinked; the unreferenced 10.9 GB E13 `rr2` duplicate deleted |
| low | prune KEEP guard's 'live llama-server' leg scans /proc/<pid>/fd, but llama.cpp closes the model fd after load | Fixed |

### CI & test validity (8)

| Sev | Defect | Status |
|---|---|---|
| medium | uninstall.sh --purge-weights resolves a relative WEIGHTS_DIR against the caller's CWD, deletes an unrelated ../weights, and misses the real default ../weights | Fixed |
| medium | Relock job's persist-credentials:false mitigation is bypassable: sdist build code can plant a git hook that runs with GH_TOKEN (contents:write) in the push step | Fixed |
| low | The 'missing signature with --key must fail' rule in bundle.sh has no test: turning it into a silent pass leaves all suites green | Fixed |
| low | bundle.sh install's hash-verification gate is untested: removing it leaves every suite green | Fixed |
| low | Identity tool server (:3001) returns 500 with a traceback for any non-ASCII bearer token; the fix applied to edge/chat/artifact servers was never applied here | Fixed |
| low | Write-token relock job runs mutable-tag actions (checkout@v7, setup-python@v7) that receive github.token by default | Fixed |
| low | beast-gate's streaming include_usage injection (the inference-audit token metering) has no test | Fixed |
| low | CI never runs tests/test_ssd_wear.sh, and extensions/ is outside the shellcheck, bash -n and ruff gates | Fixed |

### Docs drift (5)

| Sev | Defect | Status |
|---|---|---|
| low | AGENTS.md says 15 MCP tools and leaves out language_reference, publish_artifact and list_artifacts; the code registers 18 | Fixed |
| low | ARCHITECTURE.md says the default model serves 350K context across six slots; the real default is 262K and -np 1 | Fixed |
| low | AGENTS.md says a full eval sweep covers 11 models; benchmark_all.py has 20, so the time estimate is badly off | Fixed |
| low | BEAST_ARTIFACT.md documents `setup-tailscale.sh --status`, but the script rejects that flag | Fixed: `setup-tailscale.sh --status` now exists (read-only mount table) |
| low | The air-gap tutorial checks the signature, then runs `bundle.sh install` without --key, so the install trusts hashes alone | Fixed |

### Open PRs (#90 → #103) (5)

| Sev | Defect | Status |
|---|---|---|
| medium | #90 esc1 cache era hashes only escalate-index.json; the card text and selection logic that reach the model are not in the key | Fixed in #103: the esc1 era keys the index, claims, selection logic and gate outcome |
| medium | 'Leaderboard-ineligible' is not enforced: a full-suite --escalate (or --greedy/--packs) run replaces the model's real leaderboard row | Fixed: experiment arms enforced leaderboard-ineligible |
| medium | Escalation selector gives a confident 'known cause … Confirmed' card for any missing member of a namespace the index knows (std.os.*, std.process.*, std.io.*) | Fixed: 'Confirmed' only for members the index knows |
| low | #90 is not merge-ready: the Ruff gate fails and docs/BEAST_LANG_PLAN.md conflicts with main | Fixed: #90 rebased, ruff green, merged as #103 |
| low | BEAST_ESCALATE is documented as the user-facing switch but setting it in openbeast.conf does nothing | Fixed: `BEAST_ESCALATE` forwarded from openbeast.conf as exactly 1/0 |

### Secrets & crypto (6)

| Sev | Defect | Status |
|---|---|---|
| medium | LLAMA_API_KEY (and edge locality token) broadcast on argv: llama-server --api-key plus curl -H in healthcheck/client scripts; the fix already applied to the artifact/chat tokens never reached these | Fixed |
| medium | Test suite appends fabricated rows into the rig's production tool-call audit trail (.run/tool-audit.jsonl) | Fixed |
| low | bash-tool secret scrub is bypassed in one command: model-authored shell reads the tool server's unscrubbed environment from /proc/$PPID/environ; SOC2 doc lists the scrub as a 'Provided' control | Fixed as above; same-uid conf read documented as an architectural limit |
| low | Tool server compares bearer keys as str with hmac.compare_digest: non-ASCII Authorization gives an unhandled 500, and the denial is never audited | Fixed |
| low | beast-gate mints .run/edge-local.token in lifespan startup, BEFORE uvicorn binds; a failed second start rotates the live gate's locality secret (the D18 bug fixed for artifact/chat but not edge) | Fixed |
| low | Tool-call audit and agent transcripts are created world-readable (0644 in 0755 dirs), unlike every sibling audit/ledger (0600) | Fixed |

### Extensions, client & misc scripts (10)

| Sev | Defect | Status |
|---|---|---|
| medium | measure-vram.sh reports a bogus 'OK' against whatever already listens on :8080, then pkills that server, with no lease check and an unanchored pattern | Fixed |
| medium | uninstall.sh --go (no purge flags) deletes .run/, which holds the session ledger, every audit log and the device registry, although the script and README say these are kept | Fixed |
| medium | uninstall.sh resolves WEIGHTS_DIR/FILES_DIR differently from lib/weights.sh: the default is wrong, ~ is not expanded, and relative paths resolve against the caller's CWD | Fixed |
| medium | ext.sh does not validate extension names: 'enable dashboard/' makes start.sh abort after the model loads; 'disable dashboard/' or 'disable .*' wipes every extension and reports success | Fixed |
| medium | doctor.sh (and start.sh) do not map BIND_HOST=:: to loopback, so every probe hits the invalid URL http://:::8080; the #97 fix covered only healthcheck.sh | Fixed |
| low | Extension pidfiles are killed with no identity check in stop.sh and start.sh cleanup; a recycled pid after a reboot or SIGKILL gets SIGTERMed | Fixed |
| low | MTP profile scripts profile the campaign's server when a GPU lease is held, because stop.sh now leaves it running and wait_health probes before checking liveness | Fixed |
| low | doctor.sh and start.sh pass beast-gate's locality token on the curl command line, visible to every local uid via ps | Fixed |
| low | Disabling a compose-kind extension and then running stop.sh (as ext.sh instructs) leaves its containers running and bound | Fixed |
| low | update.sh --llama rebuilds the live llama-server binary in place with no GPU-lease check; campaign cells launched afterwards run a different engine under the same eval era | Fixed differently: refuses under a foreign GPU lease; a failed rebuild rolls `build/bin` back |
