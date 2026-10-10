# OpenBeast v1.7.0 — beast-hydra 🐉, beast-instinct 🧿, and a measured pack in production

**The rig learns to route across machines, gets a decision plane that runs on
a full 27B, and ships its first research result that survived a clean
rerun.**

v1.6.0 made the rig distrust its own measurements. v1.7.0 is the first release
where a measurement reached production. The zig awareness pack was re-run
fresh, in one era, with every cell clean. It rescued 26 units and regressed 2
(net +24, p < 0.0001), and the champion model did not get worse. It is now
handed to every agent that works on a zig task. The same day, a second review
caught a false line in that verdict's own record. The record is corrected here,
in public, and the verdict still holds.

Two opt-in subsystems land alongside it:

- **beast-hydra** routes inference across the 5090 rig, two DGX Sparks and a
  3090 Ti box, over llama.cpp, vLLM and TensorFold. It enforces one hard rule:
  **never answer from a stock model.**
- **beast-instinct** is a typed, calibrated decision service. Its routing
  decisions run on a **full 27B**: the rig's own 27B today, and
  Open-Jev-27B-v1.1 on its own GPU later.

Both are off by default. With them off, the stack is byte-identical to one
without them, and a CI suite proves it.

v1.6.0 → v1.7.0: 15 merged PRs (#111 – #125), 245 commits (208 non-merge),
205 files changed, +43,914 / −1,263 lines. The pytest suite grew from 1,873
to 3,127 tests.

---

## ⚠️ Read before upgrading

Restart the stack after upgrading (`./stop.sh && ./start.sh -d`). Most of what
follows is new and opt-in, but a few defaults change.

1. **The eval era does NOT roll.** It stays `b5596c660b5ab819`, the same as
   v1.6.0: none of the six hashed files changed (`system-prompt.md`,
   `system-prompt-tools.md`, `opencode.json`, `agents/runner.py`,
   `agents/tools.py`, `evals/SUITE_VERSION`). **Rows measured on v1.6.0 and
   v1.7.0 pair legitimately.** One caveat: an eval-harness run never gets
   the production pack (see 2). A zig row from the harness means what it
   meant in v1.6.0.
2. **The zig awareness pack is ON for agents on zig tasks, both models.**
   After a restart, an agent started from `start_agent`, the beast-chat
   console, `agent.sh` or `client.sh agent` whose task names zig gets
   `agents/packs/zig-0.16.md` as its `--context-file`. That is the exact
   delivery the A/B measured. It needs a zig word in the task, or zig files
   in the workdir when the task names no other language.
   - It is **only** served while the pack's sha256 matches the measured one
     (`MEASURED_SHA256` in `agents/lang/pack_context.py`). An edited pack
     is a different treatment and is not served until it is re-measured.
   - It is **never** served under the eval harness (`OPENBEAST_EVAL` or
     `OPENBEAST_TASK_PATHS` turns it off unconditionally).
   - It is **not** injected into Open WebUI chats or opencode, because it
     was never measured there.
   - **Turn it off with `LANG_PACK_CONTEXT=off`** in `openbeast.conf`, or
     `OPENBEAST_LANG_PACK_CONTEXT=off`. An unrecognised value also means off,
     with a warning. `LANG_PACKS=off` or a list without zig disables it too.
   - `doctor` shows a row for it (served / off / NOT SERVED and why).
3. **beast-artifact and beast-chat changed behaviour.** Everything in
   [UPDATING.md § Upgrading past v1.6.0](UPDATING.md#upgrading-past-v160-beast-artifact-and-beast-chat)
   applies. The ones that can surprise a working rig:
   - Pages owned by `local` are re-owned to a new `rig` principal on the
     first start. This is idempotent and audited.
   - With `ARTIFACT_ADMINS` unset, **the first operator becomes the artifact
     admin** and can read, re-share and delete other operators' private
     pages. A multi-operator rig warns at every start until you set it.
   - Console jobs run under `job.sh`'s supervisor and **no longer inherit
     the stack's secret environment** (`HF_TOKEN`, `GH_TOKEN`, stack keys).
   - `healthcheck.sh` can exit non-zero with a `FOREIGN` row when a process
     the stack did not start answers on `:3003` or `:3004`.
   - The daily logrotate sweeps the session ledger (terminal records older
     than 30 days) even with `BEAST_CHAT=false`.
4. **Transcript exports redact more, and more precisely.** The scrubber now
   runs in linear time; an export used to be able to freeze the chat server
   until its watchdog restarted it. It catches more secret shapes, including
   the server's own locality token and the ntfy topic URL. Notify settings
   are redacted by value only where they are secrets: the notify URL (and
   its topic) and the token. `CHAT_NOTIFY_ON=failed` no longer redacts the
   word "failed".
5. **MCP `start_agent` ends the runner's argv with `--` before the task.** A
   task that starts with `-` is now the task, not runner flags.
6. **hydra refuses a mixed-family fleet that doesn't declare its families.**
   If your routes can answer from more than one model family and
   `hydra.allowed_families` is unset, `hydra.py --check`, reload and startup
   now fail with an error, not a warning. The `hydra.toml.example` that
   shipped during this cycle would have spilled `beast` to the stock NVFP4
   and the 35B-A3B. Copy the new example; it is uncensored-only. Hydra is off
   by default, so a rig without `HYDRA=true` is unaffected.
   `scripts/hydra.sh reload` and `drain` now exit non-zero when hydra refuses
   the change.
7. **Every beast-instinct `decision_hash` changed.** The hash now covers the
   whole engine identity (score query, model revision, image digest, SGLang
   commit, MIS delimiter, the Open-Jev pins). No calibration or gate record
   was committed, so nothing in the repo is invalidated. A calibration you
   made locally on a pre-release build must be redone. Auto-demotions now
   persist across restarts (`.run/instinct/auto-demoted.json`);
   `instinct.sh undemote` clears one.
8. **Turning `ROUTER_INSTINCT` on has a real cost.** It is `off` by default.
   In `shadow`, every hinted router turn makes **one extra prefill on the
   primary 27B**, and the turn waits for it (up to the spec's 600 ms
   deadline), because it shares the primary's single slot with the router's
   classify. The 0.6B and linear fallbacks now finish in the background, so
   they no longer hold the turn. Under `enforce`, the extra call remains on
   every spawn, abstain or low-confidence verdict; only a confident "inline"
   actually saves the classify. See
   [beast-instinct](#beast-instinct---a-decision-plane-on-a-full-27b-opt-in).
9. **Conf re-sourcing is fixed.** A shell that had already sourced `conf.sh`
   used to keep its own `OPENBEAST_HYDRA=true` / `OPENBEAST_INSTINCT=true`
   exports, so later setting `HYDRA=false` in `openbeast.conf` did nothing
   in that shell. Now `openbeast.conf` wins. With hydra flipped off, an
   operator's own model id comes back instead of `beast`.
10. **`doctor`'s OFFLINE extension-image check is stricter.** An image passes
    only if `compose up --pull never` will actually resolve it (by digest),
    not on a `repo:tag` match.
11. **New log files are rotated.** `.run/hydra.log`, `.run/instinct*.log` and
    `.run/openjev*.log` are in `logrotate-openbeast.conf`. instinct no longer
    logs every engine request at INFO.

---

## The zig awareness pack, in production 📚

The pack is a short OLD → NEW map of zig 0.16 APIs that the models learned
wrong. v1.5.0 shipped it with a SHIP verdict. v1.6.0 withdrew that verdict
because part of it rested on rows the model never produced. It was rerun from
scratch, in era `b5596c660b5ab819`, under the GPU lease, with no cache and a
harness that refuses to bank infrastructure failures (#111, #116):

| | P0 (no pack) | P1 (pack) |
|---|---|---|
| replicate a | 10 / 30 | 24 / 30 |
| replicate b | 11 / 30 | 21 / 30 |
| champion guard (Qwen3.6-27B) | 23 / 30 | 28 / 30 |

- **R1 (primary):** pooled McNemar, 26 rescues vs 2 regressions, **net +24,
  p = 3e-6**. Pre-registered Clause 1 (net ≥ 7, p < 0.05, guard clean): met.
- **R3 (guard):** the champion went 7 rescues vs 2 regressions, net +5,
  p = 0.18. That is **clean**: no significant harm, and a small improvement.
- **R4 (pack-parroting audit):** all 180 agent logs were reconstructed. The
  longest verbatim run of pack text in any solution is 46 characters or
  less, in every cell with or without the pack. What changed is idiom
  adoption: `= .empty` ArrayList appears in 14 units per pack cell against
  3 without.

Seven units were rescued in *both* replicates: `115_fft_f`, `11_bst_f`,
`123_nbody_f`, `148_convex_hull_f`, `20_priority_queue_f`, `61_extgcd_f` and
`92_popcount_f`.

**Production wiring (#117)** reuses the measured mechanism and nothing more:
the same file (pinned by sha256), the same channel (`--context-file`), the
same population (agents on zig tasks). It is fail-soft, and a spawn never
fails because of a pack. The drift check is shared with `run_eval.py`, so the
harness and production run one implementation, not two.

**Still in-sample.** The pack was written against these units' failures. The
held-out check (`LANG_AWARENESS_PLAN.md` §5) has not run. Read it with
`tier3_verdict.py --heldout`, never pooled into R1.

---

## Research integrity — the record we corrected

The 09-30 double pass (below) found that the verdict record we had just
published was wrong in two places. The verdict survives both corrections.

1. **"0 timeouts" was false.** Two rows hit the 2400 s wall timeout (exit
   −1, 0 tokens) *after* their solutions were already on disk and
   validated:
   - `P1a 65_miller_rabin_f`, a pair-0 rescue;
   - `C1 27_brainfuck_interpreter_f`, which also passed in C0, so the guard
     is unaffected.
   Both passes are real: the model's own check had passed, and then one
   request hung for exactly 20 minutes. The cause of that hang is still
   **unverified**.
   Dropping the timed-out rescue gives 25 vs 2, **net +23, p = 5.6e-6**.
   **SHIP stands.**
2. **The prompt-token figure was biased.** A timeout's tokens are recorded
   as 0, and the readout took that 0 as real. On the 59 pairs with recorded
   tokens, the published "−5,189 prompt tokens per unit" is really
   **−2,163**, and its median is +23,399. It is not a saving. The completion
   tokens-to-fix (−4,681, p = 0.064) and iterations-to-fix figures do not
   change.

Why we missed it: the check looked for `reason == "timeout"`, and
`row_validity.py` only inspected *failed* rows, so a timed-out PASS never
appeared. The fix (#119):

- the verdict script lists every wall timeout;
- zero-token rows stay out of every token statistic;
- it warns when cells ran on different commits (it flags the contaminated
  09-17 run);
- `--heldout` refuses a suite file that doesn't exist or names nothing, where
  before it printed a false "in-sample only" banner and exited 0.

The original record is kept verbatim above the correction in
`scratch/tier3-verdict-fresh-20260930.txt`.

---

## beast-hydra 🐉 — one router in front of every engine (opt-in)

`HYDRA=true` runs `agents/hydra.py` on `127.0.0.1:8095` in front of the rig's
llama-server and any engines on the tailnet: llama.cpp, vLLM or TensorFold.
Every consumer (Open WebUI, beast-gate, agents) goes through it (#115).

- **A pure decision core** (`agents/hydra_core.py`). Resolution order: pin →
  deployment → route or alias → default. Then rules, filters (health,
  capability, context fit, drain), priority groups with spill, and
  least-loaded with session affinity. Config validation fails closed.
- **Failover only before the first body byte.** A mid-stream failure ends
  with an SSE error event and no `[DONE]`, never a silent replay. 4xx bodies
  pass through byte for byte.
- **Health** is UNKNOWN / LOADING / READY / DOWN / AUTH_FAILED / MISMATCH,
  with hysteresis and a circuit breaker. A stale `/v1/models` "ok" can no
  longer clear a newer AUTH_FAILED (#118).
- **Provenance:** `X-Hydra-*` headers and a 0600 audit log with no prompt
  text.
- **Operations:**
  - GPU-lease drain, conformance admission, and hot reload that keeps the
    last good config;
  - `scripts/hydra.sh` (check, status, explain, reload, drain, undrain,
    add-node, conformance, tail, pin-smoke);
  - `scripts/hydra-sim.sh`, with a fake engine that has llama.cpp, vLLM and
    TensorFold personalities plus fault injection.
  - Operator doc: [`BEAST_HYDRA.md`](BEAST_HYDRA.md); plan and prior art:
    [`BEAST_HYDRA_PLAN.md`](BEAST_HYDRA_PLAN.md).

**Uncensored-only, enforced (#122).** Every model OpenBeast serves is
uncensored, so hydra must never route or spill to a stock one. The double
pass found that the first cut broke that rule in three ways:

- `same_family` defaulted to false;
- rules could rewrite `beast` into a route that put a stock model first;
- anchoring by list order could lock a route onto the stock family.

Now `hydra.allowed_families` is a fleet-wide policy, enforced on every
non-strict path: spill, anchor, affinity and the context last resort. A
mixed-family config without it is refused. The example fleet
(`hydra.toml.example`) is uncensored-only:

- the rig's Qwen3.8-27B-Uncensored;
- the 3090 Ti box's Qwen3.8-27B-Uncensored, which is the classifier's first
  target, keeping it off the 5090's single slot;
- **GLM-5.3-Flash-Uncensored** on the two Sparks.

**GLM-5.3-Flash on the Sparks (#120).** GLM-5.3-Flash is 320B total with 18B
active, hybrid KDA + sparse MLA, and a 1M context.

- orcarouter's uncensored FP8 does not fit two Sparks, and vLLM cannot load
  EXL3.
- So the profile is `neko-legends/GLM-5.3-Flash-Uncensored-EXL3` on
  **TensorFold, tensor-parallel 2**
  (`scripts/backends/models/glm53-flash-unc-exl3-tensorfold.env`).
- The vendored TensorFold list is re-synced to upstream v0.5.0 and pinned.
- `model-inspect` refuses the EXL3 layouts TensorFold cannot read. It labels
  the locally staged Mia mirror as **stock**, so that mirror is unusable
  under the uncensored rule.
- The fallback quant is named in `DGX_SPARK_PLAN.md`:
  `vcruz305/GLM-5.3-Flash-Uncensored-EXL3-K2`, built from orcarouter's
  uncensored FP8.

Hydra has been tested against simulated fleets, **not yet on the Sparks, and
not yet enabled on the 5090**.

---

## beast-instinct 🧿 — a decision plane on a full 27B (opt-in)

`INSTINCT=true` runs a loopback decision service on `:8094` (#115, #123).
beast-instinct absorbs the LMSYS "decision models" line
([2026-09-25](https://www.lmsys.org/blog/2026-09-25-sglang-decision-models)).
A routing choice is a *typed* question (yes_no / choice / score / rank),
answered from the model's label probabilities, calibrated, and allowed to act
only after an eval gate promotes it.

- **The lifecycle.** A decision moves off → shadow → canary → enforce, and
  gate records alone promote it. Instinct can never *grant* anything.
  `router.spawn_intent` is skip-only: instinct can make the router skip a
  classify call, never cause a spawn. The eight invariants (I1–I8) are
  pinned by tests.
- **Engines:** `rules`, `linear`, `llamacpp_logprobs`, `sglang_score`
  (the exact `/v1/score` schema) and now `openjev`.
- **Decisions run on a full model.** The LMSYS post's point is that with
  multi-item scoring, model size is nearly free, and a small model is no
  substitute for a good decision model.
  - `router.spawn_intent` is now scored by **the rig's own 27B** through
    answer-boundary logprobs (`policy.primary_use = "substitute"`), in place
    of the router's generative classify on that same model, and only on
    hinted turns.
  - A busy slot drops to the **Qwen3-0.6B CPU scorer, now a fallback only**,
    in about 1 ms.
- **Open-Jev-27B-v1.1 is the target engine.** It is Qwen3.8-27B + a LoRA +
  a scalar decision head, run **locally** on a dedicated GPU, never through
  TypeSafe's hosted API. `scripts/serve-openjev.sh`:
  - serves the **uncensored** base by default;
  - allows the stock base only with `--validation-only` on loopback;
  - mounts weights read-only with `HF_HUB_OFFLINE=1`;
  - refuses any container image not pinned by digest.
  Its binding stays commented out in `instinct.toml` until the host exists.
- **The honest cost.** In shadow, the shipped mode, every hinted turn pays
  one extra primary prefill (see upgrade note 8). An earlier draft of the
  docs claimed "adds no primary load"; review showed that was false, and the
  docs now state the real cost.
- **Operational fixes from review:**
  - `promote` checks what the service actually enforces: the shadow soak,
    and the gate computed against the current calibration;
  - `promote` no longer crashes with "Event loop is closed";
  - `demote` refuses an unknown decision id;
  - an uncalibrated rank always abstains;
  - SIGHUP during in-flight calls no longer returns 500;
  - shadow work has its own semaphore and can't starve enforce calls.
- **Measurement:** `evals/decisions/` scores decision quality (accuracy,
  macro-F1, NLL, Brier, ECE, AURC, Wilson, McNemar, bootstrap). A latency
  gate now refuses a load report that isn't this gate's subject, or whose
  error rate is over 1%.

Operator doc: [`BEAST_INSTINCT.md`](BEAST_INSTINCT.md). Plan and research:
[`BEAST_INSTINCT_PLAN.md`](BEAST_INSTINCT_PLAN.md).

---

## beast-chat 📱 + beast-artifact 🎨 — reviewed, repaired, upgraded

A 09-29 review of both subsystems found 67 findings, none refuted: 3 high,
19 medium, 45 low (#113). They were fixed with tests, including real-browser
ones in phone-sized Chromium. Then the upgrades landed:

- **beast-artifact:**
  - one `rig` owner for everything the rig publishes, plus admins
    (`ARTIFACT_ADMINS`);
  - pin, tag, share and delete from a phone (new `artifact` device scope);
  - per-version delete and `prune --keep N`;
  - opt-in retention (`ARTIFACT_RETAIN_DAYS`);
  - a paged, searchable gallery;
  - links back to the session that made a page.
- **beast-chat:**
  - a new-session sheet with operator presets; the confirmation shows the
    exact argv;
  - pause/resume;
  - an installable PWA;
  - opt-in push notifications through a self-hosted ntfy extension
    (`--publish-ntfy`, `CHAT_NOTIFY_*`), carrying titles and links only,
    never transcript text;
  - transcript export to a private, scrubbed artifact;
  - a rig status strip.
- **Verdicts as artifacts:** `scripts/publish-verdict.sh` and
  `evals/scoring.py --html`.

The 09-30 double pass then fixed 16 more (#121):

- a missed end-of-session alert is retried;
- the export scrubber is linear-time;
- an OOM in a capped session kills only its offender, so the job's own
  verdict still gets written;
- a second principal's export gets its own page;
- the session list previews an agent's last event text;
- artifact session provenance comes from the version, not the page;
- an `ARTIFACT_ADMINS` login no longer needs to be listed as an operator
  too.

---

## The double pass 🔎 — 75 findings on everything since v1.6.0

Before this release, every PR since v1.6.0 was re-read by blind reviewers,
and each finding was checked by an independent verifier: **98 raw → 75
unique survivors (6 high, 22 medium, 47 low)**.

- The highs were two direction errors:
  - instinct was hard-wired to a 0.6B and refused the 27B (four findings);
  - hydra could spill to a stock model (two findings).
- Six tracks fixed them (#119 – #124), and #125 closed the harness half. Each was built, adversarially
  reviewed, and fixed up again.
- The reviews caught five more real defects **in the fixes themselves**,
  all fixed before merge:
  - hydra's uncensored rule was still opt-in;
  - shadow mode had become blocking;
  - the export scrubber redacted `CHAT_NOTIFY_ON`;
  - `--heldout` failed open;
  - a `test_scripts.sh` pipeline aborted the whole suite silently under
    `pipefail`.

The full list, with the status of each finding, is in the
[appendix](#appendix--the-09-30-double-pass-75-findings).

---

## Also in this release

- **A resumable Tier-3 campaign harness (#111).** `scratch/tier3_zig_ab.sh`
  gains:
  - a MANIFEST that records each finished cell and refuses to resume across
    an era change;
  - a cell-boundary stop file (`.run/tier3.stop`);
  - `DRY_RUN`.

  The double pass's harness findings are closed in #125:
  - a dry run works on a throwaway copy and never writes the real manifest;
  - a stop file left over from a previous run is cleared with a notice
    instead of stopping the next run at once;
  - a lock refuses a second run on the same manifest, and a results file
    must name this cell's model and exactly this run's units;
  - the era and the llama-server build are re-checked before every cell;
  - a new `# runtime` header records the engine, both weight pins and
    `SKIP_C0`. Older manifests still resume, with a warning.
  `tests/test_tier3_harness.sh` covers each fix (14 checks, all stubbed).
- **CI and tests (#114, #124, #125):**
  - hydra's prober, concurrency and body-cap tests poll for their condition
    instead of sleeping;
  - one Chrome finder for every browser suite (it prefers google-chrome
    over the snap `chromium`; `--no-sandbox` only under CI);
  - the I8 era-lock guard and the wiring byte-identity proof get a real base
    in CI, and suites no longer run twice;
  - instinct's out-of-distribution guard (OOD inputs can never act) and
    `min_margin` are pinned by tests;
  - the hydra classify route is pinned to uncensored dense 27B targets;
  - the instinct stub scorer's noise source is seeded, which removes a flaky
    failure in the nondeterminism-probe test;
  - `test_scripts.sh` no longer aborts silently when a `grep | head | sed`
    finds nothing under `pipefail`.
- **`scoring.py --html`** now runs after `--rebuild`, and a bad path fails
  in one line instead of a traceback.
- **`serve-openjev.sh`** resolves its checkpoint and HF cache under
  `WEIGHTS_DIR` like every other launcher. It is on the opencode and
  benchmark exclusion lists, because it is not a chat model.
- **instinct engine ports:** an engine may not sit on beast-gate's local
  port (`EDGE_PORT`), or on a moved `EDGE_PORT` or `ROUTER_PORT`.
- **Docs:**
  - new: `BEAST_HYDRA.md`, `BEAST_INSTINCT.md`, both plans with
    reconciliation sections, `docs/reviews/HYDRA_PRIOR_ART-2026-09-30.md`
    and `docs/reviews/INSTINCT_RESEARCH-2026-09-30.md`;
  - `DGX_SPARK_PLAN.md` gains the GLM-5.3-Flash day-one runbook;
  - ARCHITECTURE and README describe hydra.

---

## Known open items

These are stated plainly, as in every release:

- **Needs hardware:** hydra has not run on the Sparks or the 3090 Ti, and
  `HYDRA` is not enabled on the 5090. The example's instinct consult
  deadline is 800 ms, sized for a 27B (the code default is still 25 ms).
  The real prefill latency, and the added latency of the 27B scorer on the
  1-slot MTP primary, still need measuring.
- **Open-Jev-27B:**
  - its container is pinned by package versions only;
  - `BASE_IMAGE` and the image digest are placeholders to fill on the build
    host (the launcher refuses to run until they are);
  - it needs a GPU of its own.
- **GLM-5.3-Flash:**
  - `neko-legends/GLM-5.3-Flash-Uncensored-EXL3` is gated, and the profile
    rests on indirect evidence until access is granted;
  - `model-inspect` undercounts EXL3 parameters and prints "KV size unknown"
    for its hybrid attention;
  - the served model name and context are marked VERIFY.
- **Research, parked by choice:** the held-out zig check, the escalation
  A/B, and the single-slot floor.
- **Also open:**
  - the 20-minute request hangs behind the two timeouts are unexplained;
  - an agent stopped by `stop_agent` still reads `lost` (the fix is in
    era-locked `runner.py`);
  - the theme-toggle browser test's rare flake could not be reproduced.

---

## By the numbers

| | |
|---|---|
| Merged PRs | 15 (#111 – #125) |
| Commits | 245 (208 non-merge) |
| Files changed | 205 (+43,914 / −1,263) |
| New files | 122 |
| pytest tests | 1,873 → 3,127 |
| beast-hydra (core + proxy) | 3,405 lines of Python |
| beast-instinct | 5,440 lines of Python |
| Eval era | `b5596c660b5ab819` (unchanged) |
| Tier-3 FRESH | net +24, p = 3e-6 (net +23 without the timed-out rescue); guard clean |
| 09-29 chat/artifact review | 67 findings, all fixed |
| 09-30 double pass | 98 raw → 75 unique (6 high / 22 medium / 47 low) |

---

## Appendix — the 09-30 double pass (75 findings)

Status: ✅ fixed · ◐ partly fixed · — not fixed (reason given) · ○ no
change needed.

### hydra (9)
- ✅ A-hydra-1 [high] The example and the code defaults let every route spill to a stock model.
- ✅ A-hydra-2 [high] `same_family = true` could anchor a route to the stock family.
- ✅ B-hydra-2 [high] Rules rewrote `beast` into routes that put stock first, even with same_family.
- ✅ A-hydra-3 [medium] With same_family set, the ctx last resort returned 503 instead of the engine's 400.
- ◐ B-hydra-4 [medium] Sticky affinity kept a chat on stock. *Fixed; two chats with the same opening text and no `X-Conversation-Id` still share an entry. Under the policy that can only land on an allowed family.*
- ✅ B-hydra-5 [medium] Listing the 27B as an instinct engine made hydra refuse the rig node. *The example deadline is now 800 ms; the latency still needs measuring on hardware.*
- ✅ A-hydra-5 [low] The shipped example failed its own lint; the phone rule downgraded beast:max.
- ✅ B-hydra-6 [low] `beast:fast`, classify and bulk-to-fast routed to the ~3B-active MoE.
- ✅ B-hydra-7 [low] `hydra.sh reload`/`drain` exited 0 when hydra refused the change.

### instinct (16)
- ✅ B-instinct-01 [high] Instinct could not route into a full 27B; the code pinned the 0.6B.
- ✅ A-instinct-2 [medium] In shadow, a calibrated `linear` hid the LLM scorer from the shadow data.
- ✅ A-instinct-3 [medium] `decision_hash` ignored `score_query`.
- ✅ A-instinct-4 [medium] `gate.shadow` {min_decisions, min_days} was never enforced.
- ✅ B-instinct-02 [medium] `instinct.sh promote` crashed with "Event loop is closed".
- ✅ B-instinct-04 [medium] The latency gate passed on a foreign or mostly-failing load report.
- ✅ A-instinct-5 [low] `promote_check` said READY for a gate the service rejects.
- ✅ A-instinct-6 [low] Shadow work queued on the shared semaphore.
- ✅ A-instinct-7 [low] Router shadow recorded no incumbent verdict.
- ✅ A-instinct-9 [low] `exec=mis` did not require `mis_delimiter`.
- ✅ B-instinct-05 [low] A SIGHUP during in-flight calls returned 500 with no ledger row.
- ✅ B-instinct-06 [low] Auto-demotion was memory-only.
- ✅ B-instinct-07 [low] `demote` accepted an unknown decision id.
- ✅ B-instinct-08 [low] An uncalibrated rank reported `act`.
- ✅ B-instinct-09 [low] `instinct.log` grew without bound.
- ✅ B-instinct-10 [low] The docs and scorer header overstated some guarantees.

### wiring (4)
- ✅ B-wiring-1 [high] Every instinct engine was a 0.6B; the 27B primary and hydra were refused. *Open: a dedicated Qwen3.8 prompt format stays hardware-flagged.*
- ✅ B-wiring-2 [low] The classify route sent the spawn classifier to a ~3B-active MoE first.
- ✅ B-wiring-3 [low] The engine port blocklist named the gate as `:8443`, but it listens on `EDGE_PORT` 8090.
- ◐ B-wiring-4 [low] A re-sourced shell's own HYDRA/INSTINCT exports pinned them. *Fixed for both; BEAST_CHAT has the same pattern and is left as it is.*

### docs (5)
- ✅ B-docs-01 [high] instinct's docs, config and loader contradicted the full-27B direction.
- ✅ A-docs-2 [medium] BEAST_INSTINCT.md said "built, not wired". *Fixed there, and the `docs/TODO.md` heading in this release PR.*
- ✅ A-docs-3 [low] The seed table reported acts at 0.705, not the effective 0.90.
- ✅ A-docs-4 [low] hydra had no operator doc and was missing from ARCHITECTURE and README.
- ✅ B-docs-05 [low] On a real rig both hydra decisions run with no LLM engine, and the doc didn't say so.

### ops (5)
- ✅ A-ops-1 [medium] The instinct scorer was hard-wired to Qwen3-0.6B.
- ✅ B-ops-1 [medium] doctor's OFFLINE image check passed on a `repo:tag` compose can't use.
- ✅ A-ops-2 [low] The new hydra/instinct logs weren't rotated.
- ✅ A-ops-3 [low] `scoring.py --html` short-circuited before `--rebuild`.
- ✅ B-ops-2 [low] `publish-verdict` printed a spurious visibility WARNING on every republish.

### campaign (9)
- ✅ A-campaign-1 [medium] The record said "0 timeouts"; two cells hit the wall timeout, which skewed R2 2.4×.
- ✅ A-campaign-2 [medium] `DRY_RUN=1` against a real manifest appends fake rows. *Fixed in #125: a dry run uses a throwaway copy.*
- ✅ B-campaign-1 [medium] A stop file touched during the last cell is never consumed. *Fixed in #125.*
- ✅ A-campaign-4 [low] Era, commit and engine aren't compared across cells. *The verdict warns (#119); the harness re-checks before every cell (#125).*
- ✅ A-campaign-5 [low] The R4 pack-parroting audit had not been done. *Done: no parroting.*
- ✅ A-campaign-6 [low] Docs drift: "fresh rerun queued" / "effect unmeasured". *FEATURES fixed; LANG_AWARENESS_PLAN in this release PR.*
- ✅ B-campaign-3 [low] Two instances on one manifest run every cell twice. *Fixed in #125: a manifest lock.*
- ✅ B-campaign-5 [low] TODO/FEATURES still said "unresolved". *The NEXT section, then the Decisions section in this release PR.*
- ✅ B-campaign-6 [low] Held-out units were pooled into R1.

### chat (7)
- ✅ B-chat-1 [medium] The `scrub_secrets` regexes were quadratic; one export could freeze the server.
- ✅ B-chat-2 [medium] An OOM in a capped session stopped the whole scope.
- ✅ A-chat-2 [low] A notification that failed to send was dropped for good.
- ✅ B-chat-3 [low] The export scrubber missed common secret shapes.
- ✅ B-chat-4 [low] Re-exporting as a different principal failed with an opaque 404.
- ✅ B-chat-5 [low] MCP `start_agent` passed the task without `--`.
- ✅ B-chat-6 [low] The session-list preview showed the raw JSONL line.

### artifact (9)
- ✅ A-artifact-1 [medium] Session provenance kept only the latest session.
- ✅ B-artifact-1 [medium] Unpinning an old page under retention deleted it at the next sweep, with no warning.
- ✅ A-artifact-2 [low] An `ARTIFACT_ADMINS` login that wasn't also an operator got 404 everywhere.
- ✅ A-artifact-3 [low] Per-version DELETE told a keyed caller whether an id exists.
- ✅ A-artifact-4 [low] The CLI test's default-port case dialled the real `:3004`.
- ✅ B-artifact-2 [low] Per-version DELETE leaked a page's existence to a device key.
- ✅ B-artifact-3 [low] `ARTIFACT_ADMINS` and `ARTIFACT_RETAIN_DAYS` were missing from the conf example.
- ✅ B-artifact-4 [low] A bad `?page=` showed the owner a raw 422.
- ✅ B-artifact-5 [low] The CDP helper failed when the process had few open fds.

### tests & CI (11)
- ○ A-tests-ci-1 [medium] The first AUTH_FAILED fix didn't work. *Fixed properly by #118.*
- ✅ A-tests-ci-2 [medium] The same probe-flip race was in the MISMATCH and loading-503 failover tests.
- ✅ A-tests-ci-3 [medium] The caller-leaves test read the audit before hydra wrote it.
- ✅ B-tests-ci-8 [medium] Tests pinned the invariants the full-27B direction contradicts.
- ✅ A-tests-ci-4 [low] The I8 era-lock guard never checked the branch diff in CI.
- ✅ A-tests-ci-5 [low] linear's OOD guard was untested (a mutant survived).
- ✅ A-tests-ci-7 [low] CI ran about 2 minutes of suites twice.
- ✅ A-tests-ci-8 [low] The wiring byte-identity proof would never run in CI again.
- — B-tests-ci-3 [low] The theme-toggle browser test flakes about 1 in 7 under load. *Not reproduced here (0/12; the verifier got 0/29).*
- ✅ B-tests-ci-5 [low] Hydra's streamed body cap and feedback backpressure were never exercised.
- ✅ B-tests-ci-7 [low] The browser helpers read different env vars to choose Chrome.
