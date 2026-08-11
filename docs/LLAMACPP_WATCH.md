# llama.cpp watch list — upstream changes we must plan for

Findings from the 2026-08-03 upstream recon (window 2026-07-01 → 2026-08-03,
upgraded from b10066 to master the same day). This is the forward-looking
half: announced or in-flight upstream changes that WILL require OpenBeast
work when they land, plus the tripwires to re-check on every upgrade. The
how-to-update mechanics live in [`UPDATING.md`](UPDATING.md).

## Coming changes that will need OpenBeast work

### 1. Default port moves 8080 → 9931 (announced, not yet flipped)

Upstream merged a notice (#26508) that llama-server's default port will change
from 8080 to 9931. **We are safe at runtime today**: every launcher goes
through `scripts/serve.sh`, which always passes `--port` explicitly
(default `PORT=8080`), and profile/measure scripts do the same. Nothing in the
stack relies on llama-server's built-in default.

When the flip lands, the residual work is **documentation and comments only**:
~16 serve-script header comments, `healthcheck.sh`'s `LLAMA_URL` default,
README/docs that say ":8080 is llama-server's default". Grep to find them:

```bash
grep -rn "8080" --include="*.sh" --include="*.md" scripts/ docs/ README.md
```

Decision to make at that point: keep 8080 as OpenBeast's opinionated port
(clients, tailscale serve :8443→:8080, WebUI wiring all assume it — churn for
zero gain), or follow upstream. **Recommendation: keep 8080.** We set the port
explicitly everywhere; upstream's default is irrelevant to us.

### 2. llama-server → daemon/router mode (direction, issue #26116)

Upstream plans to evolve llama-server into a daemon that resolves model tags,
auto-spawns/reuses backends (`llama serve -hf ...` UX). This overlaps with
what beast-slot's deferred "multi-slot serving profile + fleet router" would
build (see `BEAST_SLOT.md`). When it materializes:

- Re-evaluate the deferred fleet-router design BEFORE building it ourselves —
  upstream may hand us model-tag routing for free behind the `/api/slot`
  contract.
- Watch for changes to process lifecycle (our supervisor, pidfiles in `.run/`,
  and `launch_and_wait()` rollback assume one long-lived llama-server
  process we exec directly).

### 3. `--load-mode` flag consolidation (#20834, landed Jul 23)

`--mlock`, `--no-mmap`, `--direct-io` are deprecated in favor of
`--load-mode {none|mmap|mlock|dio}` (`-lm`). **We pass none of the deprecated
flags anywhere** — nothing to migrate. Rule going forward: any new serve
script that wants locking/IO behavior uses `--load-mode`, never the old flags.

### 4. Server built-in tool surface is churning

The server's built-in agentic tools are in flux: `apply_diff` was removed
(#25498), new tools (`get_info`, `datetime` format arg) and an `x-tool-cwd`
header appeared, and experimental external-MCP-server connection support
landed. OpenBeast runs its OWN tool layer (identity tool server +
`mcp_server.py`) and does not use llama-server's built-in tools — keep it that
way. If we ever consider the built-ins, note they have no per-user identity or
RBAC and would bypass beast-gate's model of "tools execute on the client".

### 5. Chat-parsing shape: `thinking` aliased to `reasoning_content` (#25695)

Reasoning models may now surface thinking under either field name depending on
template/version. Anything of ours that parses reasoning out of completions
(evals, benchmark harnesses, WebUI wiring) should accept both. Also upstream
now supports **separate sampling params for thinking vs non-thinking
sections** (#25709) — a candidate lever for the still-open NEO over-reasoning
config question (memory: reasoning-budget 4096 is the current tame).

### 6. Stricter tool-call validation (#25583)

Malformed tool-call arguments now get HTTP 400 instead of a 5xx/crash. Good
change, but clients that previously "got away" with sloppy args will start
seeing 400s. If agent-router or OpenCode tool calls start failing after an
upgrade, check for this before blaming our stack.

## Tripwires — re-check on EVERY llama.cpp upgrade

- **MTP throughput regression (#25489, OPEN as of 2026-08-03).** Upstream has
  an unresolved 15–20% MTP decode regression report since ~b9235. We are an
  MTP-default stack. After every rebuild, compare live tok/s against the
  profiled baselines (heretic-v2 Q6 MTP ≈ 139 t/s single-stream; see
  `docs/RESULTS.md`) before declaring the upgrade good. If throughput craters:
  pin back (see rollback below) and note the build range here.
  - **MEASURED 2026-08-03 on `0ef6e55ed` (b10066+188): 120.3 t/s** warm,
    greedy, 512-tok gen, draft-mtp confirmed engaged (n-max 4, ~49%
    acceptance). That is **−13.4% vs the 139 t/s baseline** — we ARE hit.
    Decision: staying on the new build (we develop against master, and the
    prefill-disaggregation + CUDA race fixes are worth it); pin back with the
    rollback below if serving speed matters before upstream fixes #25489.
    Re-measure on the next pull.
- **`--fit`/NextN layer miscount (#26177, fixed in b10152).** `--fit`
  undercounted the MTP/NextN block, so layer 0 could land on CPU and the
  fused GDN kernel was silently disabled (~10% decode on MTP models). Our
  pinned `0ef6e55ed` PREDATES the fix — the −13.4% measurement above may be
  part #25489, part #26177. Before quoting ANY MTP throughput number (this
  includes the research campaign's speed columns): check whether the serve/
  measure invocation passes `--fit`, then re-measure on ≥b10152. Related
  merges spotted in the 2026-08-11 recon: #26861 (imatrix collection −30%
  MoE / −13.5% dense, no math change) and #26903 (MTP-export lm-head quant
  scales — upstream now feels the MTP-calibration pain we reported in
  #23476/#23575; the comment window is open). Also worth a code inspection:
  the MTP speculative path for the recurrent-state rollback-on-rejected-
  drafts hazard reported on hybrid linear-attention rigs (AdaptFM rank-6
  finding, `research/lowrank/prior-art/recon-2026-08-11.md` §T8).
- **`--kv-unified` semantics.** Our tenancy ground truth (prefix-based slots,
  unbounded queue, kv-unified purges lowest slot) was re-verified against the
  July window — no upstream changes. Any upgrade note mentioning kv-cache or
  slot management should trigger a re-read of the beast-gate tenancy
  assumptions before the new build serves multi-client traffic.
- **Default behavior changes hiding in flag renames.** Deprecated flags warn
  today and disappear later. After a pull, run one serve script and scan the
  first 50 log lines for `deprecated` warnings.

## Opportunities parked for later

- **Disaggregated prompt-prefill workers (#25675, opt-in).** Prefill stops
  blocking decode slots — directly improves beast-slot multi-client latency
  (one client's giant prompt no longer stalls another's generation). Evaluate
  enabling once we run the multi-slot serving profile.
- **NVFP4 W4A4 + per-channel scaling (#25730).** May shift the July finding
  that K-quant MTP siblings beat NVFP4 at single-stream (-np1). Re-profile the
  neko-legends NVFP4 pair on the new build before citing the old numbers.
- **DSpark speculative decoding (#25173).** `--spec-type draft-dspark`;
  Qwen3 4B/8B/14B only so far — not our 27B lineup yet. Watch for larger-model
  support. Update 2026-08-11: DeepSeek-V4 support + DSpark for it merged
  Aug 2 (#24162/#25784, ~1.8–2.0× measured) — the larger-model lane is
  opening; note the drafter tensors join the imatrix blind-spot family
  (same class as MTP/NextN — check coverage before quantizing).
- **Slot-similarity trace logging (#26218/#26271).** Debugging aid for exactly
  our prefix-based slot-reuse behavior — useful next time slot assignment
  needs forensics.

## Rollback pin

Last known-good build before the 2026-08-03 upgrade: **b10066
(`86a9c79f8`, 2026-07-17)** — the build that served the v1.1.0 launch and the
07-31 two-machine run. To pin back:

```bash
git -C llama.cpp fetch --depth 1 origin 86a9c79f8 && git -C llama.cpp checkout 86a9c79f8
./scripts/update.sh --llama    # detached HEAD → rebuilds as-is, no pull
```
