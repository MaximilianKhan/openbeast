# Harness notes — what could make a model perform better in OpenBeast's agent loop

A running log, opened 2026-11-16, of harness additions and improvements suggested by
evidence from v5 runs and their audits. Every entry names its evidence; nothing here is
adopted until it is measured as its own arm (a runner or tools change moves the eval era,
so it is judged on a full re-run, never on a re-roll of failed units).

Open question this log feeds (see the research-paper TODO in `docs/TODO.md`): does each
model need its own harness, or can one unified harness serve GLM-5.3-Flash,
Qwen3.8, DeepSeek v4.1 Flash and the rest, with a few per-model knobs?

Status key: **IDEA** (noted) · **BUILDING** · **ARM** (being measured) · **ADOPTED** · **REJECTED**.

---

## Headroom — the gap the harness work is chasing (2026-11-16)

| Row | v5 (71 units) | Notes |
|---|---|---|
| Claude Opus 5.5 (reference ceiling) | **71/71** (68/71 at the strict 1x wall) | Claude Code's own tools, no turn cap; solver never saw validator output; integrity scan clean |
| GLM-5.3-Flash L0 (daily config, runner v1) | **26/71** | 43 of 45 fails audited as genuine model mistakes *under this harness*; most ended on the turn cap at 10-30% of their wall |
| GLM L1 (Zig packs) | Zig 0/6 -> 3/6 | packs fix the 0.16 API drift |
| GLM arm A (runner v2: G1-G4, G8, G11 v1) | **31/71** | +5 net vs L0 (11 rescued, 6 regressed; single seed). Rescues cluster on the audited frictions (36_b/c, 143, 146, 121, 179); same time and turns as L0 |
| GLM arms B/C (+wall-scaled turns, +thinking) | running | B from 16:06; C after |

Max's read, and the working hypothesis: GLM-5.3-Flash is a far stronger model than
26/71 suggests, and much of the gap is the harness around it, not the weights. Each arm
above tests one piece of that; whatever gap remains after them is the next experiment.
Future-experiment backlog (beyond A-C): k-seed repeats (G12), "test before done" (G9),
edit-not-rewrite diagnostics (G13), thinking budget sweeps, parallel tool calls, a
Qwen3.8 v5 row and DeepSeek v4.1 Flash on the same arms (the paper's cross-model axis).

## GLM-5.3-Flash (spark-hydra, thinking off) — from the 2026-11-16 v5 L0 audit

Source: the 45-failure audit (one auditor + one skeptic per task; 43 TRUE_FAIL, 2 harness).
Transcripts: `agents/logs/agent-20261116-0[6-8]*.jsonl`; L0 results
`evals/results/eval-glm-5-3-flash-L0-20261116-060847.json`.

| # | Observation (evidence) | Candidate change | Status |
|---|---|---|---|
| G1 | Iteration cap, not time, ended most failures: 20-30 turns spent in 150-400 s of a 1,200-1,800 s wall (122 a-e, 158, 163, 36). | Tell the agent its turn budget up front; a remaining-turns notice at <= 3. | ARM A: in runner v2, 31/71 vs 26/71 (#179) |
| G2 | No-op `update_plan` loops: up to 15 of 25 turns with identical args (196, 183, 173, 178, 190). The prompt's "keep it current" wording invites it. | Don't count / nudge an `update_plan` whose args equal the current plan. | IN RUNNER V2 (#179; arm A 31/71) |
| G3 | Identical tool call + identical result repeated: same `cat` x15 (195), same bash x19 (121), same rebuild x12 with no edit (163_b). | After 3 identical calls, append "you have seen this output N times; act on it". | IN RUNNER V2 (#179; arm A 31/71) |
| G4 | Tool host is macOS/BSD, model assumes GNU: `sed -i 's/…'`, `cat -A`, `timeout`, `<(...)` under `/bin/sh` each cost a turn (124, 122_e, 36_c, 167, 179). | Platform line in the runner prompt; consider `bash -c` for the bash tool. | IN RUNNER V2 (#179; arm A 31/71) |
| G5 | Thinking off: reasoning slips that thinking would likely catch — the missing `x/sqrt(2)` in N(x) debugged for 20 turns (36 b/c/d) despite the sample disagreeing; copied its own carry bug into a hand trace (163_a). | Per-request thinking (`chat_template_kwargs.enable_thinking`), verified live on TensorFold. | ARM (thinking on) |
| G6 | Zig 0.16 API drift: pre-0.15 `std.heap.GeneralPurposeAllocator`, `std.io.bufferedWriter`; whole budgets spent grepping lib/std (36_f, 123_f, 163_f, 122_e). | Language packs (L1) + diagnostics/escalation (L2) — the existing ablation arms. | ARM (L1/L2) |
| G7 | A non-streaming turn ran past the gate's 600 s read timeout and the OpenAI client retried it identically: 1,200 s lost, two units never wrote code (168_c, 168_f). | Gate EDGE_READ_TIMEOUT above unit wall (done for evals, 3600); retry policy: never re-send an identical request after a read timeout; consider streaming. | PARTLY ADOPTED |
| G8 | Hung pipelines reported as "[Errno 1] Operation not permitted" instead of a timeout (146). | Always name the timeout and its limit. | IN RUNNER V2 (#179; arm A 31/71) |
| G9 | Model stops short of verifying: several fails had a working fix in a scratch file but never wrote it back to the destination (36_c, 36_d, 143). | A pre-`task_done` check: "the destination file differs from your last passing scratch test" — or a generic "re-run your test against the destination before task_done" nudge. | IDEA |
| G11 | Runaway turns: on 168 (c, f) GLM's first code-writing turn generated for > 600 s, uncapped (eval mode sends no max_tokens), reproduced in 3 samples on 2026-11-16 L0 and again in the 09:00 rerun after the gate timeout was raised: now the OpenAI client's own 600 s timeout fires, retries once, and the unit burns its 1,800 s wall without writing a file. | Cap eval turns (e.g. 12-16K completion tokens with thinking off) so the runner's existing truncation recovery returns the head of the turn to the model; client `max_retries=0` on read timeouts. Must apply to every model (era move). | IDEA |
| G12 | **Decoding is effectively deterministic on the Spark server** although the runner sends temperature 0.6: L1/L2 transcripts are byte-identical until the first injected byte; a 23-byte `diagnostics: OK (zig)` flipped 36_f and 123_f, an `ls` mtime flipped 179 (L2 audit, 2026-11-16). Single-sample arm deltas on small cells are trajectory chaos, not effects. | Seed-varied k >= 3 samples per (unit, arm) for any rescue/regression claim; check whether TensorFold honours `temperature`/`seed`; strip volatile bytes (mtimes) from fixtures. | IDEA (methodology) |
| G13 | A failing diagnostic on a `write_file` result invites a full rewrite: 9/29 next mutations were rewrites vs 2/31 after a failed build without diagnostics; the rewrite re-rolls correctness (167_b, 163_d regressed in L2). | Failing diagnostics on write results end with "fix with edit_file at line N", or show only the first error with its line. | IDEA |
| G14 | Push diagnostics do not save turns: after a reported error the model rebuilds anyway (167_f it5, 163_f, 122_e). | Tell the agent (runner prompt) that write results already carry compiler output; measure as a sub-arm. | IDEA |
| G15 | shellcheck re-reports pre-existing warnings on a file the model only edited; cost 179 a turn. | For edit_file, report only diagnostics new relative to the pre-edit file. | IDEA |
| G16 | Escalation can only fire for zig on the Mac harness host: no go/c/rust index; the cpp index was built with g++ 16.2.1 and the host has clang 21 (index_gate served:false). The arm is labelled "packs+diag+escalate" anyway. | Stamp per-language card eligibility in the results header and the exposure line ("card-eligible units: 6/38"); build a clang cpp index. | IDEA |
| G17 | Telemetry: shell unit 179 bucketed as python (no `language` on non-variant rows, `ablation.py` defaults to python); `go vet` errors counted as 0 (regex misses the `vet: ` prefix). | `language` on every row; `^(?:vet: )?\S+:\d+:\d+: `. | BUILDING |
| G10 | Request `model` was the rig alias `qwen-27b-q5`, not the served id (cosmetic; audit labels wrong). | Send the served id. | ADOPTED (#177) |

## Cross-model ideas (not yet model-specific)

- Iteration budget scaled by task size (xl SWE tasks at 30 turns starve a one-tool-call-per-turn model; 176 note). Must apply to every model equally.
- A "test before done" discipline in the runner (G9) may help every model that patches without re-running.
- Whether parallel tool calls per turn (if the model supports them) change the iteration economics.

## Per-model vs unified — what to measure

For each candidate: run it as an arm on GLM-5.3-Flash and on at least one other model (Qwen3.8 27B on the rig; DeepSeek v4.1 Flash when it lands). A change that helps one and hurts another is the evidence for per-model knobs; one that helps all belongs in the unified harness.
