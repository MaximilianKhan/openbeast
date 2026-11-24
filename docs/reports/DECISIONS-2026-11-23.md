# Decision log, 2026-11-23

> One entry per decision taken on 2026-11-23 (PST, UTC-8; `Z` marks UTC).
> Each entry gives the decision, who took it, the reason, the evidence (a PR,
> a report or a file), the alternatives considered and why they lost, and any
> correction made afterwards. The reports linked here hold the detail; this
> page holds the reasoning, so a later reader (or model) can see why the
> repository looks the way it does without replaying the session.
>
> There was no decision-log convention in `docs/` before this page; dated
> reasoning lived in the reports and in `docs/TODO.md`. This is the first
> dated decision log. PRs are in `MaximilianKhan/openbeast` unless marked
> "website" (`MaximilianKhan/openbeast-website`). Merge times are from
> `gh pr view N --json mergedAt`.

## Contents

1. [The rig's eval-host calibration: rerun, redo, verdict](#1-the-rigs-eval-host-calibration-rerun-redo-verdict)
2. [Abandon HG_T2 and delete the Hy4-abliterated GGUF](#2-abandon-hg_t2-and-delete-the-hy4-abliterated-gguf)
3. [Reject Mia's GLM 1104 recipe; keep v1.8](#3-reject-mias-glm-1104-recipe-keep-v18)
4. [D2 (our native GLM recipe): the 4-bit-dense key, still the challenger](#4-d2-our-native-glm-recipe-the-4-bit-dense-key-still-the-challenger)
5. [Q_T2 becomes the 5090's default via `AGENT_PROFILE=qwen38-best`](#5-q_t2-becomes-the-5090s-default-via-agent_profileqwen38-best)
6. [The deep adversarial review](#6-the-deep-adversarial-review)
7. [GLM-UP on the board, with the honest reading of its +5](#7-glm-up-on-the-board-with-the-honest-reading-of-its-5)
8. [The website progressions](#8-the-website-progressions)
9. [Strata: review, build, benchmark, not adopted](#9-strata-review-build-benchmark-not-adopted)
10. [Install the NVIDIA Container Toolkit on the rig](#10-install-the-nvidia-container-toolkit-on-the-rig)

---

## 1. The rig's eval-host calibration: rerun, redo, verdict

**Decision.** The RTX 5090 rig's eval-host container is accepted as a v5
harness host, with two named host effects as caveats. The verdict was then
**toned down** by the review to what one arm supports: "no host effect
detected beyond run-to-run variation, from one calibration arm".

**When / who.** Calibration arm 2026-11-22 15:03-21:11; rerun of the flips
21:11-23:12; redo of every fail 23:12 → 2026-11-23 03:57. Report merged as
#282 (04:12); verdict corrected in #295 (16:10). Max judged the late fails
to be contention (he had loaded the rig by accident, and a 224 GB copy
shared the Sparks' fabric from 19:42 to 20:15 on 11-22); the redo of all 11
fails was chosen in reply (session record, 2026-11-23 06:42Z).

**Reason and evidence** ([V5_EVAL_HOST_CALIBRATION-2026-11-23.md](V5_EVAL_HOST_CALIBRATION-2026-11-23.md)):

- The method changes only the harness host: DeepSeek T2 (board row `DS-T2`,
  65/71 from the Mac) rerun from the rig's container, same tree `f38a29c7`,
  same command, same pair, same served model.
- Results: calibration **60/71**; rerun of the 7 units whose verdict differed
  **2/7**; redo of the 11 units the calibration failed **5/11**.
- A real host bug was found and reproduced: Rust's std on Linux/glibc imports
  `dlsym` for any thread, and the `122_gemm_blocked` validator rejects any
  binary importing `dlsym`, so a threaded Rust GEMM is an automatic stop on a
  Linux host (`122_gemm_blocked_d` in all three attempts). The second known
  host effect: the agent's shell has no network deny on Linux.
- Timing on the 58 units that finished under the wall on both hosts: the rig
  is slower, median per-unit ratio 1.16 (report §4; the first draft's "about
  the same" was dominated by wall-capped units).

**Alternatives considered.**

- *Take the 60/71 at face value* (a host 5 below the board): rejected; four
  of the fails ran inside the copy window, and the hour-long units flip
  between attempts on the rig itself.
- *Rerun only the flipped units* (r1): done first, then a redo of **all 11**
  fails, so the comparison with the board stays unit-for-unit.
  Caveat recorded at the time: a redo of fails only can only add passes, so
  r1/r2 are evidence of host parity, not a score. The 60/71 stays as recorded.
- *Keep the Mac as the only harness host*: rejected; the rig is needed for
  the 5090's own runs (Q_T2) and frees the Mac.

**Correction.** #282's verdict ("calibrated") overstated one arm; #295 (the
review, entry 6) restated it, rewrote the `dlsym` footnote procedure to
"verdict not measured", and the 5090 board host string now reads "calibrated
2026-11-23 (harness9, carried to harness10 by inference)".

## 2. Abandon HG_T2 and delete the Hy4-abliterated GGUF

**Decision.** Stop arm HG_T2 (Hy4-flash-abliterated, OrcaRouter Mix-256G GGUF,
llama.cpp RPC across the pair) and do not test that model. Then delete the
GGUF from both Sparks.

**When / who.** Max, 08:10 ("If HG_T2 is this bad and not performing, then
let's just kill the run and not test that specific model!"). Recorded in #284
(08:12). Deletion: Max, 08:19 ("yes, delete the GGUF from both sparks"); done
the same minute.

**Reason and evidence.** At the kill the arm had run 16/71 units, 8 passed,
10 of them at the 3,600 s wall and 5 with no solution file written. The serve
measured **8.5 tok/s** for one stream and **about 5.3 per stream at four**
(21.3 aggregate), against vLLM NVFP4's 31.4 / 77.8 for stock Hy4 (H_T2, 60/71).
At 5.3 tok/s an 8K-token thinking turn takes about 25 minutes, so the row
would measure the RPC serve, not the model. The arm stays in
`evals/campaigns/v5-thinking-first.json` with its ABANDONED note; its
`recommend.composite_by_model` entry is removed (#295);
`tests/test_campaign.py` pins both. Records: [TODO.md](../TODO.md),
[MODEL_CAMPAIGN.md](../MODEL_CAMPAIGN.md).

**Deletion.** 224 G freed on each Spark (spark-1 2.0T free, spark-2 2.1T
after, per the session record). Kept: the LibertAI Hy4 EXL3 4bpw and the Hy4
NVFP4 packs (memory rule: never delete the Hy4 EXL3 pack). Also removed: the
96 G RPC tensor cache on spark-2 (rebuildable). Kept on spark-1 in
`~/openbeast-runs/`: the patched fork (llama.cpp PR #32517 at `cf70454` plus
two local patches: RPC `supports_op` for indexer shapes, a 16-head indexer
case) and its serve scripts. Why delete: the GGUF is hash-verified against the
Hub and re-downloadable; keeping 224 G twice for an abandoned model buys
nothing.

**Alternatives considered.** *Let it finish* (ETA then 19:00-20:00, holding
DeepSeek off the Sparks all day) to get a complete but serve-bound row:
rejected, the row would be uninterpretable. *One stream at 8.5 tok/s*: still
too slow for 8K thinking turns. *Keep the GGUF for a later engine*: rejected,
re-download is cheap.

**Correction.** The session told Max "5.4 tok/s per stream" at launch; the
campaign note and TODO say 5.3 (21.3 / 4 = 5.33). 5.3 is the right figure.

## 3. Reject Mia's GLM 1104 recipe; keep v1.8

**Decision.** GLM keeps Mia's recipe v1.8 (`33b50fd`). The newer 1104 recipe
is not adopted.

**When / who.** The GLM serving window, 08:25-10:20, Max's approval for the
window; report merged as #292 (10:18).

**Reason and evidence** ([GLM_SERVING_WINDOW-2026-11-23.md](GLM_SERVING_WINDOW-2026-11-23.md), "Benches",
same pair, same day, same probe through beast-gate, thinking off):

| Recipe | 1 stream | 4 streams | Prefill 12K / 50K |
|---|---:|---:|---:|
| v1.8 (`DENSE=q4`) | **48.4** | **78.4** | **1,969 / 2,047** |
| 1104, Mia's settings (`DENSE=q8 HEAD=q8`) | 41.3 | 66.8 | 1,368 / 1,407 |
| 1104 at v1.8's `DENSE=q4` | 45.4 | 75.5 | 1,404 / 1,484 |

About 15% slower decode and about 30% slower prefill as shipped; at v1.8's
numerics it ties at one stream (thinking on) and stays behind elsewhere. No
functional regression either way.

**Alternatives considered.** *Adopt 1104 for its newer engine* (TensorFold
1.3.0-era plus 15 patches): rejected, slower on our pair. *Chase the gap to
Mia's published 47.8 / 87.3*: not done; three setup differences (reserve 17.5
vs 6.5 GiB, `PARALLEL=4` vs 8, the Ablit checkpoint) were noted, none
expected to cost decode, and the same-day same-probe comparison is what
decides.

**Correction (later, #296).** Mia's 1104 default reserve is 6.5 GiB, not 9.5
as `data/recipe-engines.json` had said.

## 4. D2 (our native GLM recipe): the 4-bit-dense key, still the challenger

**Decision.** Add a `DENSE_BITS` recipe key so D2 (`glm53-flash-exl3-native-tp2`,
native TensorFold 1.2.10, TP2, no Mia container) renders 4-bit dense layers,
a 524,288-token window and an 11.5 GiB reserve. D2 stays the **challenger**;
production GLM stays v1.8.

**When.** D2 first served in the window (09:42, after a failed 09:31 attempt
that the rollback caught). Key merged as #293 (15:32). Hardware re-check of
the committed render 18:53-18:59 (#300, 19:03).

**Reason and evidence.**

- As first rendered (bf16 dense, 245,760, 17.5 GiB) D2 served at 30.7 / 57.7
  tok/s; with `TF_GLM_DENSE_BITS=4` tuned, 41.8 / 64.7 (window report).
- #293: `DENSE_BITS` takes 4, 6, 8 or 16; the engine data says which engine
  and family read it (today only native 1.2.10 with GLM); recipes refuse it
  elsewhere, including GLM on 1.3.x, where no one has confirmed it is read. It
  reaches the ranks through an allow-listed profile key, so a recipe cannot
  inject `LD_PRELOAD` or NCCL settings.
- #300 re-check: drafter and pack hashes OK on both Sparks,
  `TF_GLM_DENSE_BITS=4` on both rank units, start line "model 84.33 GiB; 4
  streams … 3.66 GiB each at a 524288-token window … of 15.9 GiB left after a
  11.5 GiB reserve", conformance PASS, **44.65 / 67.95 tok/s** at 1 / 4
  streams. Rolled back to `beast-spark glm`, then DeepSeek.

**Alternatives considered.** *Switch production GLM to D2* (own launcher, no
third-party image): rejected while it trails v1.8's 48.4 / 78.4. *Expose the
raw env var instead of a recipe key*: rejected for the injection reason.

## 5. Q_T2 becomes the 5090's default via `AGENT_PROFILE=qwen38-best`

**Decision.** The RTX 5090's default agent setup is arm Q_T2's switches,
named `AGENT_PROFILE=qwen38-best`; applied on the rig.

**When / who.** Max's decision after the Q_T2 row (board #298, 17:39).
Preset merged as #299 (17:48). Applied on the rig 19:14-19:15, after the
Strata bench restored serving.

**Reason and evidence.** Q_T2 (T2's switches at one stream) scored **52/71**
(54 raw), SCORE 75.6, #4, against the L0 baseline's 39/71 (SCORE 63.3); on
the 61 units both runs scored, +12 / -1, p 0.003
([V5_QWEN38_RTX5090-2026-11-22.md](V5_QWEN38_RTX5090-2026-11-22.md),
[MODELS.md](../MODELS.md)). The serve did not change (the same Qwen3.8 27B
Uncensored MTP Q5_K_M on llama.cpp); the gain is the agent configuration.

**Design.** `qwen38-best` and `deepseek-best` share one switch table in
`scripts/lib/conf.sh` (Q_T2 is T2 at one stream); a test enforces the
identity. Precedence unchanged: env `OPENBEAST_<KEY>` > the key's own conf
line > the profile > the default, so the rig's existing `BEAST_ASSIST=1` does
not conflict.

**Apply record.** Backup `openbeast.conf.bak-20261123-preq38`; line appended
with a comment; conf lint clean, all nine switches resolved; `./stop.sh` and
`./start.sh -d` (the rig does not run the `openbeast` user unit); verified:
gate `/v1/models` 200, a chat answered "391" with `reasoning_content`, the
gate process environment carries the T2 switches, dashboard 200. Details and
rollback: [TODO.md](../TODO.md).

**Alternatives considered.** *Wait for an Inkling 2 Mini row before changing
the default*: rejected; the default should be the best measured setup now, and
Inkling has no row. *Apply during the Strata bench*: rejected; the restart
would have disturbed it. *A separate switch table for Qwen*: rejected; two
tables would drift.

**Caveats** (as published): single seed; L0 is harness10, Q_T2 harness9;
every switch changes at once, so no single switch is credited.

**Correction.** The planned steps said `~/openbeast`; the rig's checkout is
`~/Documents/openbeast`. Fixed in TODO.

## 6. The deep adversarial review

**Decision.** Review everything since 2026-11-22 adversarially before it
settles, in parallel by area, and count a finding only after an independent
re-check. Fixes off the live path merge at once; anything touching a live host
becomes a TODO for a quiet window.

**When / who.** Queued at 15:32 and launched at 15:52 (23:52Z), once the
three agents then working had reported: five read-only reviewers, A (eval
integrity), B (Spark ops), C (recipes and engines), D (docs), E (website).
The message that first asked for it is not in the session excerpt read for
this log, so who proposed it is **unverified**. Fix PRs: #295 (16:10), #296
(16:25), #297 (16:31), website #35 (17:15); reviewer D's 17 doc findings
were folded into those PRs.

**Scope and outcome.**

| Area | PR | Findings |
|---|---|---|
| Eval integrity (calibration claims, `dlsym` procedure, ablation exclusions, `composite_for`) | #295 | 16 found, 16 confirmed and fixed, none refuted. Ablation now honours `exclusions.json` (5090 diagnostic n's: Rust 7→6, compiled 36→35; no rescue, regression or p-value changed). `composite_for` takes the longest matching key. A sealed integrity review added |
| Recipes and engines | #296 | All confirmed and fixed except one refuted doc-link item: the D2 drafter's offline path, TensorFold 1.3.3 in the ark (signature and hashes re-verified), `d2_served`, Mia's 1104 reserve 9.5→6.5, duplicate JSON keys refused, cluster start refuses mismatched profiles |
| Spark ops | #297 | 9 of 9 fixed: `beast-spark ds` no longer rolls back a working switch, exit 3 = model serving but command center down, Hy4 start/stop races, dashboard temp files, `TimeoutStopSec=120` |
| Website (website #35) | #35 | 10 of 10 held. **High:** `e2e-remote.sh` with `E2E_REMOTE_DIR=.` would have wiped the rig user's home, `~/.ssh` included (shown with a fake `ssh`, nothing harmed); untracked secrets (`.npmrc`) could be archived; `E2E_REMOTE_DOCKER_ARGS` allowed remote command injection. Stale "not yet calibrated" copy replaced; evidence pin re-synced to `e54a8efd` |

**Alternatives considered.** *One reviewer over everything*: rejected, too
shallow per area. *Fix findings as reported*: rejected; every finding was
re-verified first (one refuted). *Deploy fixes to the Sparks and rig at once*:
rejected while GLM UP and Q_T2 ran; deploys waited (#300).

## 7. GLM-UP on the board, with the honest reading of its +5

**Decision.** GLM-UP (56/71, SCORE 81.9, #3) becomes GLM's best row, published
with the statement that its +5 over GLM-C is **not** a measured improvement
from the UP setup.

**When.** Arm finished ~18:50; board and report #301 (19:05); the minimum
detectable effect per contrast added in #302 (19:13); website #39 (19:20).

**Reason and evidence** ([V5_GLM_UP-2026-11-23.md](V5_GLM_UP-2026-11-23.md)):

- Board: 56 against GLM-C's 51. Three of the five are `122_gemm_blocked_b`,
  `_d` and `_e`: GLM-C's integrity scan had withheld those passes, and UP
  passed them clean.
- Paired over the 68 units both rows measured cleanly: +4 / -2, **net +2,
  McNemar p 0.69**, MDE 9.5 units (#302): the same within noise, single seed,
  across harness eras (`8eaa5e5f` → `9bcd5aa6`), every assist switched on at
  once.
- Integrity: 0 contaminated after a scanner fix (a relative `cd tests` under
  `/tmp` was misread as leaving `/tmp`; re-running all 1,196 past transcripts
  changed no earlier verdict).

**Alternatives considered.** *Report "+5 from UP"*: rejected as misleading.
*Withhold the row until a same-era pair exists*: rejected; it is a valid row,
only its attribution is limited, and the page says so.

**Correction.** Earlier in the session Max was told DeepSeek's UP scored
65/71. Wrong: **DS-UP is 62/71**; the 65s are T2 and UP_8k. The cross-model
reading on the same harness is 62 vs 56.

## 8. The website progressions

**Decision.** Show a model's runs as a progression (both rows, paired
statistics, caveats) rather than only its best row. Done for the 5090's
Qwen3.8 (website #37) and GLM C → UP (website #39).

**When / who.** Max pointed out three gaps on the 5090 page (no detail
progression, no overview indicator, runs tables showing one run). Website #36
(row, 17:48), #37 (progression, 18:07), #38 (numeral clipping fix, 18:15),
#39 (GLM, 19:20).

**Reason and evidence.** #37: baseline → T2 bars (39 → 52, SCORE 63.3 →
75.6), per-language bars, the paired tile (+12 / -1, net +11, p 0.003),
tokens per pass 484K → 1.2M, wall stops 15 → 9, a runs table naming each
run's harness version; "2 runs: 39 → 52" on the overview; ranking still by
the best run. Every figure is computed from the synced results with guards
that fail the build if the paired numbers stop adding up. #39: the same for
GLM (+4 / -2, p 0.69, wall stops 20 → 16). Both are labelled bundled and
cross-era, never as a single-switch ablation. #38: big gradient numerals lost
the right edge of their last digit (tight negative letter-spacing narrowed
the painted box); one CSS rule fixes every use.

**Deployed.** Max runs `deploy.sh prod`; at ~20:10 `curl -s
https://openbeast.ai/benchmarks/` contained "39 → 52", "2 runs", "GLM-UP",
"56/71" and "52/71", and no "not yet calibrated".

**Alternatives considered.** *Hy4-style ablation framing*: rejected; these
pairs cross harness versions and bundle switches. *Rank by latest run*:
rejected; the board ranks by the best run.

## 9. Strata: review, build, benchmark, not adopted

**Decision.** Review Strata before running it; build it from source at a pin
in a locked-down container; benchmark it against llama.cpp on the same
Inkling 2 Mini file; **do not adopt it**. Continue with Strata's own SQ packs
and other engines before choosing the engine for Inkling's v5 run.

**When / who.** Max asked to support Strata after the "4,087 tok/s" claim
(~15:28), then relayed "754 code / 440 prose" (~15:48). Review reported
15:35; bench 17:29-19:14; follow-up investigation from ~19:39.

**Reason and evidence** ([STRATA_INKLING_5090-2026-11-23.md](STRATA_INKLING_5090-2026-11-23.md)):

- Review: MIT, no malware found, alpha; release binaries, images and the
  `curl | sh` installer have no provenance (the installer verifies itself with
  the binary it downloads). Built from `6a6db7ff` offline; binary `11df1066…`.
- Same tensors, single stream, no drafter: Strata 138.6 / 138.7 tok/s (code /
  prose) vs llama.cpp 96.9 / 96.9; prefill 1.5-2.0x; conformance PASS both;
  top-1 agreement 96.76%.
- Not adopted because Strata forces Inkling's thinking off (T2 needs it on),
  its DSpark drafter fails on CUDA at the pin (upstream #6762), and it is
  alpha.
- The claims: "4,087" is Strata's own "4,089.7 tok/s total output across 64
  concurrent requests" (v0.2.19 notes); their single-stream figures are 250.3
  solo and 198.9 on SQ-20, which our 198.5 on code matches. 754 / 440 appears
  in no official source.

**Alternatives considered.** *Run the release binary or the installer*:
rejected (no provenance, circular verification). *Skip the review*: rejected
for new single-author code. *Adopt Strata for chat only, thinking off*:
rejected for now; one engine per model keeps the eval and the daily setup
comparable. *Report the DSpark bug as a new issue*: unnecessary, already
reported.

**Corrections.** Two wrong statements were made and corrected the same
evening: "4,087 has no source" (pre-run review) and "4,087 is a prefill
figure" (03:15Z); both are recorded in the report's §10.

## 10. Install the NVIDIA Container Toolkit on the rig

**Decision.** Install `nvidia-container-toolkit` on the 5090 rig so
containers get the GPU with `--gpus`.

**When / who.** Max, 19:56:48 (pacman log), Docker restarted 19:57:17.

**Reason and evidence.** Without it the Strata bench passed the GPU by hand
(`scripts/gpu-args.sh`: device nodes plus the driver's user-space libraries,
mounted read-only, tied to driver 615.78.08); vLLM's engine path
(`scripts/engines/vllm.sh`, `--gpus all`) could not run on the rig at all; and
the Inkling candidates (vLLM, Basalt) run in containers. Record and offline
reinstall: [INSTALL.md](../INSTALL.md#gpu-in-docker-the-nvidia-container-toolkit).

**Alternatives considered.** *Keep passing devices by hand*: rejected; it
breaks on every driver update and duplicates the toolkit badly. *CDI only,
no Docker runtime entry*: not chosen; `nvidia-ctk runtime configure` is the
documented path and keeps `runc` the default.

**What went wrong, and the lesson.** At ~19:39 the session advised running the
commands "now, before the Inkling run starts", when no container was using the
GPU. By ~19:47 the Inkling investigation had started containers, and at 19:56
the session changed the advice to "install now, hold the Docker restart". The
restart happened at 19:57:17 and cut the running bench container, which
resumed and re-ran the affected measurements. Lesson: before advising a
Docker restart, check `docker ps` at that moment, and say that the restart
stops every container.
