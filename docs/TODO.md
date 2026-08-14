# TODO

## 🆕 BENCHMARK Qwen3.8-27B — staged 2026-08-14, sweep not yet run

Four configs are integrated, VRAM/speed-measured, and registered in
`evals/benchmark_all.py` with **deliberately empty leaderboard entries**.
Weights are pinned in `scripts/weights.registry`. Full profile + architecture
notes: [`MODELS.md`](MODELS.md#qwen38-27b-qwen--unsloth--added--profiled-2026-08-14--not-yet-benchmarked).

Run the sweep (~3 h at ~45 min/model):

```bash
python evals/benchmark_all.py --models qwen38-27b-q5,qwen38-27b-mtp-q5,qwen38-27b-q6,qwen38-27b-mtp-q6
```

Open questions the sweep should settle:

1. **`--reasoning-preserve`.** llama-server reports this model's chat template
   supports it and Qwen defaults `preserve_thinking` *on*; our scripts leave it
   off to match the rest of the lineup. This is the highest-value A/B — Qwen's
   own headline numbers are multi-turn agentic benchmarks, exactly where
   retained reasoning context would matter. Measure both ways before picking a
   default.
2. **Does it displace the default?** Qwen claims SWE-bench Pro 61.7 /
   OSWorld-Verified 84.3 vs Opus 4.6 Max's 53.4 / 72.7 (vendor-reported, on
   benchmarks our suite does not run). Our v4 board is the arbiter.
3. **Q5 vs Q6 accuracy-per-byte.** Q6 costs 2.7 GB and ~9% decode for whatever
   fidelity it buys; the equal-byte findings from the beast-rank campaign make
   this the interesting rung.
4. ✅ **Vision — DONE 2026-08-14.** `mmproj-F16.gguf` downloaded, pinned, and
   verified reading a synthetic test image; two vision serve scripts shipped.
   Loads under the existing `PROJECTOR_TYPE_QWEN3VL` graph, no llama.cpp change.
   Details in [`MODELS.md`](MODELS.md). Two follow-ups left: **video** input is
   untested (the chat template renders `<|video_pad|>`), and the vision configs
   are not in `benchmark_all.py` (our eval suite is text-only — adding vision
   tasks is its own project).

## 🔁 RETEST — is the MTP + `--mmproj` ban actually lifted? (2026-08-14)

Every MTP serve script we ship asserts "`--mmproj` is not yet supported with
MTP", inherited from a 2026-05-22 upstream limitation. On 2026-08-14 that was
**disproven for arch `qwen35`**: no such guard exists in the current llama.cpp
source, and Qwen3.8 + `--mmproj` + `--spec-type draft-mtp` ran correctly with
the draft path live at 64.6% acceptance.

The Qwen3.6-era scripts (`serve-qwen-27b-mtp-q5.sh`, the Fable-Fusion and
Heretic v2 MTP scripts, etc.) still carry the old claim in their headers and
were **not** retested. If it is lifted for them too, several models gain vision
they are currently documented as not having. Cheap to settle: load one Qwen3.6
MTP script with a matching `mmproj` and send an image. Fix the headers either
way — right now they state as fact something we know is false for at least one
architecture.

## ✅ DEFAULT MODEL SWAP — DONE 2026-08-14

Applied: `openbeast.conf` now sets `SERVE_SCRIPT=serve-heretic-v2-27b-mtp-q5.sh`.
Stack restarted and verified serving `Heretic v2 27B MTP Q5` at `n_ctx 262144`,
29,282 MiB used / 3,325 MiB free, all three services healthy.

**"Maximum context" was measured, not assumed.** 262,144 is both the GGUF's
declared ceiling and the practical VRAM limit for this config:

| Context | VRAM at load | Free | Verdict |
|---------|--------------|------|---------|
| **262,144** | 29,282 MiB | **3,325 MiB** | shipped — 3,307 MiB free even under load |
| 294,912 | 30,214 MiB | 2,393 MiB | **rejected** — climbs to 30,717 MiB under load, leaving 1,890 MiB, below the Gemma crash point |
| 327,680 | 31,146 MiB | 1,461 MiB | rejected outright |

288K also measured **no faster** (87.3 vs 89.0 tok/s), so it bought 1.4 GB of
risk for nothing. Going beyond 262,144 safely would need rope extension *and*
freeing VRAM — see the note in `MODELS.md` about Qwen3.6-class scripts running
past their declared `context_length`. Not attempted; unvalidated on this
fine-tune.

Original analysis retained below.

## 🎛️ DEFAULT MODEL SWAP — Heretic v2 Q6 -> Q5 (Max, 2026-08-14)

Max's call: the current default (`serve-heretic-v2-27b-mtp-q6.sh`, `-c 212992`
= 208K) is excellent but its context is too small for his typical use. Swap the
default to the **Q5_K_M** sibling, conditional on the context gain being more
than marginal.

**Condition met — the gain is real, and Q5 is already maxed out:**

| | Context | VRAM used / free | Decode |
|---|---|---|---|
| Q6_K (current default) | 208K (`-c 212992`) | 30.4 GB / 2.25 GB | ~139 tok/s (n4) |
| **Q5_K_M (proposed)** | **256K (`-c 262144`)** | 29.6 GB / 2.97 GB | ~136 tok/s (n8) |

**+49,152 tokens (+23%)**, and it also buys ~0.7 GB more headroom for ~2% less
decode. Note "push the context as far as we can take it" is already satisfied:
262144 is Qwen3.6's **native ceiling**, and `serve-heretic-v2-27b-mtp-q5.sh`
already ships exactly there — going higher needs YaRN rope scaling, which is
untested on this fine-tune and risks silent quality loss past ~128K. So this is
a one-line config change, not a script change:

```
# openbeast.conf line 26
SERVE_SCRIPT=serve-heretic-v2-27b-mtp-q5.sh
```

Not applied yet — `openbeast.conf` is gitignored local config and the running
stack would need a restart to pick it up. Also update the "default is …"
sentences in `README.md`, `docs/MODELS.md`, and `docs/FEATURES.md`, which still
name Qwen3.6-27B Uncensored Q5_K_P and are already stale against the current Q6
default.

## 🐍 MIGRATE agents/ TO mcp 2.0 — deferred 2026-08-14

`mcp 2.0.0` removes the `FastMCP` class that `agents/mcp_server.py:65` is built
on (it is `MCPServer` in `mcp.server.mcpserver` now). `update.sh --python`
installed it on 2026-08-14 and the next `./start.sh` died on
`ModuleNotFoundError`, taking the stack down.

Held at `mcp==1.28.1`, and `update.sh` now refuses to bump past it (the import
gate added in `4bc72e6` rolls back and exits 1). **That is a hold, not a fix** —
we are pinned to a superseded major version and will drift further.

Same story for `openai` 2.46.0 → 3.0.0, which the same run tried to install.
Both migrations are gated behind the import check, so neither can land silently;
they need doing deliberately:

1. Port `agents/mcp_server.py` from `FastMCP` to the 2.0 `MCPServer` API —
   note `agents/openapi_tools.py` imports it and re-uses the decorated
   callables, so both surfaces move together (that shared-import design is what
   keeps them from drifting — don't break it).
2. Check the `openai` 3.0 client surface used by `agents/runner.py` and the
   evals.
3. Re-run `./scripts/update.sh --python`; it rewrites the pins only once
   `import mcp_server, openapi_tools` succeeds against the new versions.

## ⬆️ PROMOTE THE REBASED llama.cpp BUILD — staged 2026-08-14

The beast-rank research kernels were uncommitted in the llama.cpp working tree
and blocking `git pull` (14 modified files + `tools/gradmatrix/`, 1,367 lines,
never saved anywhere — `llama.cpp/` is gitignored, so no OpenBeast commit ever
captured them). Now saved twice: local branch `beast-rank-kernels` and a patch
vendored at [`research/lowrank/kernels/`](../research/lowrank/kernels/).

**Rebased b10254 → b10434 (180 commits) and it works.** 3 files / 4 hunks
conflicted, all resolved; every CUDA kernel auto-merged. Builds clean.
Qwen3.8 + MTP n4 measures 121.8 tok/s vs 123.7 pre-rebase with identical draft
counts — noise, no #25489 regression. Full detail in that README.

**Still to do before the new build goes live:**

1. **tok/s check on the DEFAULT model.** Only Qwen3.8 (`qwen35`) was measured.
   The default is Heretic v2 27B MTP Q6 (`qwen3.6`), and #25489 targets MTP —
   measure it against the ~139 tok/s baseline in [`MODELS.md`](MODELS.md).
2. **Promote the build.** The rebased tree is in `llama.cpp/build-rebase/`;
   `serve.sh` still points at `llama.cpp/build/` (2026-08-04). Swap only after
   step 1 passes. Keep the old `build/` until the new one has served a real
   session — rollback pin is b10066 (`86a9c79f8`) per `LLAMACPP_WATCH.md`.
3. **Re-verify the research kernels themselves.** The rebase proved the *serving*
   path is unchanged; it did NOT re-measure the +79% 0.6B kernel trilogy or the
   gradmatrix extraction. Re-run those before trusting the rebased tree for new
   campaign work.
4. **`git checkout master` in llama.cpp is now safe** — it is clean, and the
   research work is on `beast-rank-kernels`. Future upstream pulls should be
   `git pull` on master, then `git rebase master beast-rank-kernels`.

## 🔭 UPSTREAM WATCH — llama.cpp (recon 2026-08-03)

Full brief: [`LLAMACPP_WATCH.md`](LLAMACPP_WATCH.md). Short version: default
port 8080→9931 is announced upstream (we pass `--port` everywhere — docs-only
change when it lands, recommendation is to keep 8080); llama-server is heading
toward daemon/router mode (re-evaluate the deferred beast-slot fleet router
against it before building ours); an OPEN 15-20% MTP throughput regression
(#25489) makes a tok/s baseline check mandatory after every rebuild. Rollback
pin recorded there: b10066 (`86a9c79f8`).

## 🧪 EXPERIMENT — serve with vLLM instead of llama.cpp (Max, 2026-08-04)

Evaluate vLLM as the serving engine for our weights. Motivation: vLLM
has shipped TurboQuant KV-cache quantization (fused Triton backend,
extended to hybrid-attention Qwen3.5-class models via PR #39931) while
llama.cpp has only an open feature request (#20977); its issue traffic
shows active Qwen3.6-27B + NVFP4 + Blackwell usage — exactly our
territory. Test matrix: (a) serve the production 27B (and the beast-rank
corrected artifacts — vLLM's GGUF support is partial; may need HF/
safetensors weights + separate LoRA loading for our corrections.
Post-E27 scope note (2026-08-04, research/lowrank coherence-audit
P3.8): at ≥2.5 bpw the equal-byte winners are PURE-QUANT GGUFs
(mixed-TYPE control, byte-compatible re-rounded files) — no LoRA
needed, which simplifies this matrix; adapter plumbing only matters
for the sub-2.5-bpw lane);
(b) single-stream tok/s + VRAM vs our llama.cpp numbers (llama.cpp wins
today on MTP speculative decoding — check vLLM's spec-decode support
for this model family); (c) multi-client throughput (vLLM's home turf —
relevant to beast-slot); (d) integration cost: OpenAI-compat surface,
beast-gate/identity-server compatibility, streaming, the /api/slot
contract. Outcome: a measured recommendation, not a migration — the
stack stays llama.cpp unless vLLM wins on our actual workload.
Recon addendum (2026-08-11): vLLM v0.27 ships QuTLASS FP4 + a fused
one-graph AR speculator, and FlashInfer v0.6.16+ has SM120 (RTX
5090-class) NVFP4 dense GEMM + fused FP4 MoE — desktop-Blackwell FP4
is no longer second-class outside llama.cpp, which strengthens the
case for running this experiment and gives the beast-rank NVFP4 work
a second serving substrate. Details:
research/lowrank/prior-art/recon-2026-08-11.md (CONTEXT section).

## 🔌 ODS ABSORPTION — decided 2026-07-17 (Max)

Reviewed [Osmantic/ODS](https://github.com/Osmantic/ODS) (a kitchen-sink
one-command local-AI installer) for features worth absorbing. ODS and OpenBeast
are opposite philosophies — ODS bundles everything; OpenBeast maximizes the one
biggest brain, opinionated, nothing leaves the machine. Max approved absorbing
these FIVE (build them the OpenBeast way; each stays on-ethos):

1. ✅ **Bootstrap fast-first-chat (SHIPPED 2026-07-17, 2450d78).** Opt-in
   FAST_BOOT: serve-bootstrap.sh runs Qwen3-0.6B on :8080 for instant chat, the
   model-agnostic stack comes up, then start.sh warms the real weights and
   hot-swaps behind :8080. Default off. Verified end-to-end.
2. ✅ **Model load-failure rollback (SHIPPED 2026-07-17, 2450d78).**
   launch_and_wait() records last-good (.run/last-good-serve-script) and reverts
   on OOM/missing-weight at initial boot, fast-boot swap, and supervisor
   relaunch. On by default (MODEL_ROLLBACK). Verified with a broken serve script.
3. ✅ **Extension architecture (SHIPPED 2026-07-17, 940fa28).** extensions/<name>/
   with manifest + compose.yaml (KIND=compose) or run.sh (KIND=process);
   scripts/ext.sh enable/disable/list; start.sh/stop.sh merge fragments + launch/
   reap process extensions. EXTENSIONS conf key. Verified via the dashboard.
4. **Hardware auto-tiering (multi-vendor)** (M) — extend `lib/hardware.sh` beyond
   the CUDA reference: AMD Strix Halo (unified memory), Apple Silicon (Metal),
   Intel Arc (SYCL) tier detection + config. Overlaps the existing "CI build
   matrix" + "macOS/Metal" items — mine ODS's tier logic when tackling those.
   ⏳ REMAINING — the one ODS-absorbed item not yet built (needs the non-CUDA
   hardware / CI matrix to validate against).
5. ✅ **Status dashboard (SHIPPED 2026-07-17, 93f6463).** extensions/dashboard/ —
   stdlib-Python read-only GPU/model/services page + /api/status on :3002 (the
   first extension). Enable: ./scripts/ext.sh enable dashboard.

Also shipped alongside: **per-model reasoning config** (646bc32) — serve.sh
REASONING / REASONING_BUDGET; the DavidAU NEO scripts default to
--reasoning-budget 4096 to tame the 25k-token over-reasoning runaways.

REJECTED from ODS (against the grain — noted so we don't relitigate): voice I/O
(Whisper/Kokoro), LiteLLM cloud/hybrid fallback (violates "nothing leaves your
machine"), n8n workflow automation, ComfyUI/image generation, Perplexica, the
Qdrant RAG pipeline (WebUI's built-in RAG suffices), PII-scrub proxy, and the
magic-link remote proxy (Tailscale is stronger).

## 🎯 NEXT CAMPAIGNS — ranked (2026-07-09 full-repo audit: docs, code, security, deployability)

What the four-front audit surfaced that is NOT yet done, ranked by
impact-to-effort for the "comprehensive, configurable, deployable" mandate.
Quick wins from the same audit already shipped (compose cap_drop/NNP,
secrets off the systemd unit env, BIND_HOST=0.0.0.0 warning, .kube/.docker
write guard, pinned pip deps + update.sh pin-bump path, Ubuntu CUDA paths,
disk-space healthcheck guard, scripts/openbeast.service boot unit).
PR-quality CI (ShellCheck+Ruff+pip-audit), dependabot, CODEOWNERS,
SECURITY.md, CODE_OF_CONDUCT.md, and a VRAM-headroom healthcheck warning
shipped 2026-07-09.
Also shipped 2026-07-09: PR-quality CI (ShellCheck + Ruff + pip-audit),
dependabot, CODEOWNERS, SECURITY.md, CODE_OF_CONDUCT.md, and a
VRAM-headroom (<2 GB) healthcheck warning that names the graphics
processes eating the free space.

1. ✅ **Identity tool server (DONE 2026-07-09).** agents/openapi_tools.py
   replaced mcpo for the WebUI connection: per-user workspace sharding
   (FILES_SHARDING=user default, per-chat optional), both RBAC keys
   enforced in one process (admin=all, guest=web-only-404-elsewhere),
   per-call audit trail (.run/tool-audit.jsonl, digests not contents).
   Live-verified; tests/test_identity_server.py (10). mcp_server.py stays
   for OpenCode/MCP. mcpo dropped from requirements (fastapi+uvicorn in).
2. ✅ **Hardware profiles Phase 2 (DONE 2026-07-09).** serve.sh auto-scales
   -c to the detected card's KV budget (ob_scale_context); reference cards
   unchanged, sub-32GB scaled down, weights-don't-fit → smaller-quant warn.
   The sub-32GB OOM blocker is closed. REMAINING refinements: Intel Arc VRAM
   detection (S), community measured per-card profiles, multi-GPU
   --tensor-split (L).
3. **CI build matrix** — GitHub Actions job building llama.cpp cuda/hip/
   sycl/cpu so the AMD/Intel paths stop being faith-based. (`pip-audit` is
   ✅ DONE — pr-quality.yml; caught 4 PyJWT CVEs on day one → 2.12.1→2.13.0.)
4. ✅ **Weight checksum verification (DONE 2026-07-17).** `scripts/weights.registry`
   sha256+size-pins every shipped GGUF (17 models); bootstrap verifies the
   default after download; `scripts/verify-weights.sh` (--deep / --file) checks
   any download; doctor runs the quick size check; test_scripts §12 blocks an
   unpinned serve-script weight. Remaining upgrade: model REGISTRY enforcement —
   serve.sh refusing an unlisted/mismatched weight unless overridden (see Model
   governance below), + the eval quality gate.
5. **macOS/Metal + Docker Desktop** (M) — Darwin branch in bootstrap
   (system_profiler GPU detect, Metal build flags); compose bridge-network
   variant (host networking doesn't exist on Docker Desktop). WSL2: likely
   works, needs a documented test pass.
   *(Distinct from client mode below — this ports the SERVER to Mac; that runs
   NO model on the Mac.)*
5b. ✅ **OpenBeast client mode (DONE 2026-07-17) → EXTENDED to beast-slot
   (2026-07-30).** [`docs/BEAST_SLOT.md`](BEAST_SLOT.md) — first-class
   client/server: generalized `scripts/setup-client.sh` (macOS+Linux,
   `--api-key`, `--local-search` bridge compose), `scripts/client.sh` CLI,
   `/api/slot` discovery contract published via
   `setup-tailscale.sh --publish-slot` (:8444), LLAMA_API_KEY loop closed
   stack-wide, macOS prlimit blocker fixed, fetch CGNAT policy pinned.
   Re-verified under an isolated HOME against the live stack (install →
   status → uninstall w/ foreign-key survival); test_scripts §13 (rewritten)
   + §15, tests/test_beast_slot.py. Remaining: the live two-machine
   three-localities run from the actual laptop (checklist now in
   BEAST_SLOT.md, incl. keyed variant). Multi-slot serving profile + fleet
   router are designed-for-deferred BEHIND the /api/slot contract — clients
   need no change when they land.
6. ✅ **fetch() DNS-rebinding pin (DONE 2026-07-09)** — _resolve_vetted +
   _PinnedHTTP(S)Connection dial the vetted IP; TLS keeps real-host SNI/cert.
   Proven: zero connect-time re-resolution; connect-flip to loopback refused.
7. ✅ **read_file hazard mounts (DONE 2026-07-09)** — refuses /proc,/sys,
   /dev (post-realpath, so symlinks too) + bounds the read regardless of
   stat'd size.
8. ✅ **Docker image digest pinning (DONE 2026-07-09)** — open-webui +
   searxng pinned by @sha256; update.sh --images bumps the digests.
9. **WebUI volume backup** (S) — log rotation ✅ DONE (logrotate-openbeast
   .conf); REMAINING: automate `docker volume` backup for chat data (folds
   into the enterprise Backup/restore CLI).
9b. ✅ **SSD/NVMe wear tracking (DONE 2026-07-30).** `scripts/ssd-wear.sh`
   (report / `--json` / `--snapshot` / `--doctor`) + a doctor row. Reads SMART
   READ-ONLY via `smartctl -j -a`, resolves the drives actually backing the
   weights and repo mounts, and tracks GB-written/day + projected days to 100%
   wear from a bounded history in `.run/ssd-wear.json` (0600).
   Design notes worth keeping:
   - **Device discovery walks `lsblk --inverse`, not string-munging.** This box
     is LUKS — `df` returns `/dev/mapper/root`, and the obvious
     strip-the-partition-suffix approach yields `/dev/mapper/roo`. Encrypted
     and LVM layouts are the common case, so name-munging is the fallback only.
   - **1 NVMe data unit = 512,000 bytes** (not 512) — the classic error.
   - `percentage_used` is a whole integer that barely moves, so a pure delta
     projection reports nothing for months; a lifetime-average endurance
     fallback fills the gap and both methods are labeled in the output.
   - History prunes from the MIDDLE and keeps the oldest point as a permanent
     anchor, so frequent doctor runs don't collapse the measurement window;
     snapshots coalesce to one per 5 min.
   - smartctl's exit status is a BITMASK (8 = failing drive) with valid JSON,
     so the script never gates on it — it parses and lets the data speak.
   - Serials are never printed or persisted, only `sha256(serial)[:12]` to keep
     history attached across device renumbering.
   Advisory by design: always exits 0, degrades to one warn row when smartctl
   is absent or unprivileged. **It warns rather than staying silent when it
   cannot read** — a green row for health nobody measured is the worst
   outcome this tool could produce.
   REMAINING (needs root, Max): add the sudoers line the script prints, then
   `sudo ./scripts/ssd-wear.sh` for the first real datapoint. Until then the
   doctor row is a warning, and this rig's actual wear is UNKNOWN.

10. **mTLS / cert-based auth: assessed, deferred** — Tailscale already
    provides identity + encrypted transport equivalent to mTLS for the
    home-lab threat model. Revisit only if OpenBeast ever fronts a network
    Tailscale doesn't own end-to-end.

Also enforced 2026-07-09: **the 11 GB VRAM floor** (Max's call) —
bootstrap hard-fails on detected GPUs under the 1080 Ti / 2080 Ti class
(`ob_vram_floor_check`, `OPENBEAST_FORCE_VRAM=1` escape hatch, tiers in
docs/HARDWARE_PROFILES.md).

## 🏢 ENTERPRISE HORIZON — brainstorm 2026-07-09 (Max: "real enterprise-grade possibility")

Everything below turns OpenBeast from a hardened home rig into something a
small org could run with a straight face. Grouped by pillar, each item
S/M/L effort. The identity tool server (shipped) is the foundation — it is
the choke point where identity, quotas, audit, and metering all attach.

### Identity & access
- ✅ **Signed identity (DONE 2026-07-09).** `setup-mcpo-keys.sh --with-jwt`
  generates IDENTITY_JWT_SECRET; docker-compose hands it to WebUI
  (FORWARD_USER_INFO_HEADER_JWT_SECRET) which then mints an HS256 JWT per
  tool call; openapi_tools.py verifies (sub drives sharding, plain headers
  ignored, forged/expired → 401, absent → anonymous). Live-verified;
  tests/test_identity_jwt.py. PyJWT pinned.
- **SSO / OIDC recipe (M).** Open WebUI supports OAuth (Google / Entra /
  Keycloak / Authentik). Document + script the wiring so org logins map to
  WebUI roles → our RBAC tiers. Zero code on our side, pure recipe.
- **Scoped, expiring tool keys (M).** setup-mcpo-keys.sh v2: per-user keys
  with TTL + tool-scope, revocation list the server hot-reloads. Replaces
  the two static profile keys when >2 trust tiers exist.
- **mTLS (deferred, above)** — becomes relevant here only off-tailnet.

### Auditability & compliance
- **Tamper-evident audit v2 (M).** Hash-chain the tool-audit entries
  (each row carries prev-row hash) + logrotate policy + retention knob.
  Today's audit is append-only JSONL; chaining makes deletion detectable.
  NOTE 2026-07-30: there are now TWO audit streams — tool calls
  (`.run/tool-audit.jsonl`) and inference (`.run/inference-audit.jsonl`,
  from beast-gate). Chain both, and add the new one to
  `scripts/logrotate-openbeast.conf`.
- ✅ **SECURITY.md + threat model + disclosure policy (DONE 2026-07-09).**
  Private-disclosure policy, scoped attack surfaces, brief threat model.
- **SBOM + scanning in CI (S/M).** ✅ pip-audit DONE (pr-quality.yml).
  REMAINING: syft SBOM artifact per release; trivy image scan. Pairs with
  the digest-pinning item.
- **Data governance (M).** Per-user workspace export + delete (the shards
  make this trivial now); FILES_RETENTION_DAYS GC; document what data
  lives where (WebUI volume, shards, audit, eval results).

### Reliability & operations
- ✅ **/metrics (SHIPPED 2026-07-09, phase 1).** Prometheus text endpoint
  on openapi_tools: per-tool call counts by profile/outcome + cumulative
  latency (no user labels — cardinality/privacy; per-user detail is the
  audit log). REMAINING: llama-server metrics scrape config + Grafana
  dashboard JSON.
- ✅ **Rotation (DONE 2026-07-09):** scripts/logrotate-openbeast.conf
  covers stack.log / tool-audit.jsonl / sweep logs (weekly or 50M, 8 kept).
  REMAINING: structured JSON log option for the tool server.
- **Backup/restore CLI (M).** scripts/backup.sh: WebUI volume + conf +
  workspaces + leaderboard → one tarball; restore path TESTED (an
  untested backup is a wish, not a backup).
- ✅ **Watchdog timer (DONE 2026-07-09):** scripts/openbeast-watchdog
  .timer/.service — healthcheck --restart every 5 min; install steps in
  the timer's header.
- **HA / fleet (L, "Mark of the Beast").** Active-passive on two boxes
  over tailnet (weights rsync + conf sync + WebUI volume replication),
  then the clustered-nodes idea. Distributed agents Phase 1 already
  points spawned-agent inference at a worker box.

### Multi-tenancy & fairness
- ✅ **beast-gate — identity-aware inference edge (DONE 2026-07-30).**
  `agents/edge.py` + `scripts/clients.sh`. The inference path had NO identity
  (`:8443` → llama-server, which has zero tenancy primitives), so every
  device collapsed into one anonymous caller with the run of the whole route
  table. The gate adds per-device keys (hot-reloaded → revoke without
  restarting llama-server), a path allowlist (404 for `/lora-adapters`,
  `/slots`, `/v1/stream`, …), client `id_slot` stripping + server-side
  affinity injection, per-device conversation-id namespacing, token bucket +
  in-flight cap, and an inference audit with token usage + `/gate/metrics`.
  Opt-in (`EDGE_GATE`), fails closed. docs/BEAST_SLOT.md.
- **Per-user rate limits + disk quotas (M).** ⏳ PARTIAL: per-DEVICE rate
  limits + in-flight caps shipped in beast-gate. REMAINING: per-user (not
  per-device) limits in openapi_tools, and du-based shard quotas.
- **Usage metering (M).** ⏳ PARTIAL: per-device prompt/completion tokens now
  recorded per completion (`.run/inference-audit.jsonl`) and exported at
  `/gate/metrics`. REMAINING: roll-up reporting (monthly per-user/per-device
  usage report) and joining gate rows to WebUI user identity.
- ✅ **Slot fairness — downgraded from L and largely DONE.** The assumption
  that this needed a scheduler was wrong: llama-server already honors a
  client `id_slot` and prefers slot-matching deferred tasks. beast-gate does
  the affinity server-side (`--slot N` at enroll). REMAINING: measure it
  under a real multi-slot profile, and note that under `--kv-unified` pinning
  reserves a slot, not KV (pool exhaustion purges the lowest-numbered idle
  slot's cache — serve `--no-kv-unified` for hard isolation).

### Deployment & fleet provisioning
- ✅ **`openbeast doctor` (DONE 2026-07-09).** ./start.sh doctor —
  validates conf/secret hygiene, file modes, pinned-dep drift, digest pins,
  GPU floor + headroom, docker, and per-service health with fix hints
  (exit 1 on FAIL). scripts/doctor.sh.
- **Air-gapped bundle (M/L).** Offline installer: weights + images +
  wheels + repo tarball with checksums; enterprise networks often can't
  pull from PyPI/DockerHub/HF.
- **Signed releases (M).** cosign-signed tags + CHANGELOG discipline +
  stable/edge channels once public.
- **Ansible/Terraform recipe (M).** Fleet-provision N boxes from bare
  metal to healthy stack; builds on bootstrap's idempotency.

### Model governance
- ✅ **Model registry + verification (DONE 2026-07-30).** `scripts/weights.registry`
  pins sha256 + bytes for every shipped GGUF; `serve.sh` now checks the weight
  it is about to load and honors `WEIGHT_ENFORCE` (`warn` default / `strict`
  refuses / `off`). Default is warn ON PURPOSE — a hard refusal on the critical
  start path would brick an upgrade whose weights predate a re-pin; `doctor`
  reports when `strict` is safe to enable. Size-only per launch (a 20 GB sha256
  every start costs minutes); `verify-weights.sh --deep` is the hash check.
  test_scripts asserts the default AND that every shipped serve script targets
  a pinned weight.
- ✅ **Eval quality gate (DONE 2026-07-30).** `doctor` resolves the default
  serve script's alias, slugifies it the way `scoring.py` does, and WARNS when
  it has no leaderboard row on this host — promotion by evidence, surfaced
  where an operator already looks instead of inventing a new workflow. It is a
  warning, not a refusal: the operator may knowingly run an unevaluated model
  (and today does — the 6 community models added 2026-07-17, including the
  current default, have never been benchmarked here).

## 🔴 REGRESSION RISK — FAST_BOOT re-creates the WebUI toolless-model bug (2026-07-31)

`e85536d` fixed WebUI tool calls by making `configure-webui.sh` create the
served model's WebUI row (with `meta.toolIds`). **That fix is defeated by
`FAST_BOOT`, and by model-load rollback.**

`start.sh:545` backgrounds `configure-webui.sh`, which polls `/v1/models` for
up to 30s. Under `FAST_BOOT=true` the stack is serving `serve-bootstrap.sh`
(alias `OpenBeast Bootstrap (loading full model…)`) at that moment and only
hot-swaps to the real model afterwards. So the config run wins the race and
writes a permanent WebUI model row for the **bridge**; the real model never
gets one → no `toolIds` → **no tools attached to any chat**. Identical symptom
to the bug just fixed, and it repeats every fast boot.

Same defect on two other paths: model-load rollback (`start.sh:319-331`)
switches to a different serve script with a different alias long after
`configure-webui.sh` exited, and the supervisor's relaunch loop can too.

FAST_BOOT is opt-in but advertised in `docs/FEATURES.md` and the README.

**Fix:** re-run the model-config pass after the fast-boot swap and after any
rollback, and have `configure-webui.sh` deactivate rows for models no longer
served (they accumulate — 11 active rows on the dev rig, 10 of them dead, and
llama.cpp ignores the `model` field so selecting one silently answers with the
loaded model under the wrong name and context).

## 🔴 LAUNCH GAP — bootstrap.sh has no client-mode off-ramp (2026-07-31)

The README now invites macOS and GPU-less users ("macOS or Linux. No GPU"), but
`bootstrap.sh` has **zero** `Darwin`/`uname -s` branches and never mentions
`scripts/setup-client.sh`. A Mac user running the headline `./bootstrap.sh`
hard-fails on a missing C toolchain with a Linux package hint. A GPU-less Linux
user fares worse: no VRAM floor rejection fires, so it builds llama.cpp and
downloads ~20 GB of weights for a CPU-only install.

**Fix:** detect macOS or no-GPU early and offer the one-line client install.

## 🐛 KNOWN ISSUE — GPU RC watchdog aborts llama-server (Xid 8) (2026-07-31)

**Corrected 2026-07-31.** An earlier version of this entry blamed the NVIDIA
*display* watchdog and recommended moving the desktop to the iGPU. **That was
wrong** — the kernel log names a different, display-independent mechanism, and
going headless would not have prevented the crash.

llama-server took SIGABRT (exit 134). Userspace signature:

```
CUDA error: the launch timed out and was terminated
ggml-cuda.cu:2485  cudaStreamSynchronize(cuda_ctx->stream())
  → common_speculative_impl_draft_mtp::process
```

The kernel log, same second, same pid:

```
NVRM: krcWatchdog_IMPL: RC watchdog: GPU is probably locked!  Notify Timeout Seconds: 7
NVRM: Xid (PCI:0000:01:00): 8, pid=463898, name=llama-server, channel 0x00000007
```

**Mechanism.** This is the driver's **Robust Channel watchdog**, which pings
its own GPFIFO channel on a timer and RC-resets the offending channel if the
ping goes unserviced for `RmWatchDogTimeOut` (default 7s) → Xid 8 →
`cudaErrorLaunchTimeout` surfaces at the next `cudaStreamSynchronize`. It is
armed by RM default on a stock `nvidia-open` install, on every GPU, headless or
not. Verified on this rig: `KERNEL_EXEC_TIMEOUT=1`, `RegistryDwords: ""`, and
**zero** "another client's request" lines in the boot log — no display client
ever pinned it. Under Wayland there is no X server on the GPU, so the
`Interactive` xorg.conf knob the display-watchdog folklore rests on does not
exist in this stack. Xid 8 is classified HW/driver/user-app — explicitly *not*
thermal, bus, or FB corruption, matching clean telemetry (zero ECC, zero
retired pages, no thermal/power brake).

**What actually correlates.** The crashing task decoded **99,574 tokens in one
unbroken 10.5-minute generation** — 7.3× the next-longest in the log's history,
and 8.7% of every token this rig has ever produced. Rate: **1 abort in 2,323
tasks / ~1.14M tokens / 14 days.** A healthy driver should have time-sliced the
watchdog's probe channel regardless, so the runaway generation is the
**trigger**, not a sufficient cause — it exposed a preemption/GSP hiccup.
Corroborated upstream: `open-gpu-kernel-modules#1063` and `#1080` (RTX 5090,
Xid 8, open module only), `llama.cpp#22060` (identical signature, closed
without root cause).

**Why the MTP frame appears — and the earlier explanation was backwards.** MTP
is *faster* per token, so it holds the GPU *less*. `common/speculative.cpp:1405`
calls `llama_get_embeddings_nextn(ctx_tgt)` — the **target** 27B context,
synchronized before any draft work. It's a sync barrier where deferred async
errors surface, which is positive evidence the hung kernel was in the main
model's forward pass. Still not a tool-calling bug.

**Recovery** is ~7.6s (5s supervisor sleep + 2.55s model load), not the ~3s
previously claimed. One turn is lost.

### To do, highest leverage first

1. **`NVreg_RegistryDwords="RmWatchDogTimeOut=60"`** in `/etc/modprobe.d/` —
   the source-verified knob (default 7s, max 60s), then regenerate initramfs
   and reboot. Targets the actual mechanism, keeps a real hang detector, costs
   no capability. `RmRcWatchdog=0` disables it entirely (last resort). Note
   `RmDisableRcWatchdog` does not exist, and `nvidia-smi -c EXCLUSIVE_PROCESS`
   / persistence mode have **no** effect on this watchdog.
2. **DECIDED 2026-07-31 (Max): do NOT cap generation length.** A reviewer
   proposed adding `-n`/`--n-predict` to `serve.sh` because the crash
   correlated with a 99,574-token unbounded decode. Rejected, for three
   reasons. It isn't enforceable — `-n` is only a *default* applied when the
   request omits `n_predict` (`server-context.cpp:388-398`), so any client
   sending `max_tokens` overrides it; it would constrain our own WebUI turns
   while leaving the real surface open. The rate (1 abort / 2,323 tasks /
   ~1.14M tokens / 14 days, self-healing in ~8s) does not justify losing the
   capability. And the defect is upstream in `nvidia-open`, not here.
   Unbounded generation is deliberate — it's the "no compromise" ethos, and a
   27B model reasoning for 100K tokens is a feature. Revisit only if the rate
   climbs materially or a multi-tenant profile makes one client's runaway
   everyone's problem — and then bound it per-device at beast-gate, where
   identity exists, not globally at the serve layer.
3. **A/B test MTP off** — `llama.cpp#23268`/`#23210` report speculative-decode
   timeouts on Qwen3.6-27B + MTP. Cheap hypothesis test.
4. **`doctor`: do NOT warn on `display_active: Enabled`.** It fires on
   essentially every single-GPU desktop — our whole audience — most users can't
   act on it, and it points at the wrong cause. Warning fatigue for no signal.
   Add a **forensic** check instead: count `NVRM: Xid` in `journalctl -k -b` and
   `exited unexpectedly` in `.run/stack.log`; silent when clean, degrades to a
   silent pass when the journal is unreadable.
5. **Troubleshooting entry** in `docs/INSTALL.md` keyed on the `launch timed
   out` string, pointing at `journalctl -k -b | grep NVRM` to confirm Xid 8.
6. **Supervisor gaps.** On restart-budget exhaustion `cleanup` kills the
   processes but never the Docker containers, leaving a WebUI up with no model
   behind it. And `agents/router.py:250-252` streams the body outside its
   `try/except`, so an upstream death mid-stream just truncates the SSE with no
   error object — the `Server disconnected` message we quoted actually comes
   from a *later* request landing in the outage window, not the killed turn.

**Doc conflict to reconcile:** `scripts/healthcheck.sh:240,247`,
`docs/MODELS.md`, and `docs/REFERENCE.md` assert a "sustained-load crash zone"
near 2,080–2,132 MiB free. This crash happened at 2,247 MiB free. The repo now
carries two competing folk explanations for llama-server dying under load,
neither with a verified mechanism.

## 🌐 EGRESS PRIVACY — tabled 2026-07-31 (Max: design only, do not implement)

User-requested feature: route OpenBeast's outbound traffic through a commercial
VPN. Shipped so far is only the *documentation* of the supported baseline (a
Tailscale exit node) in [EGRESS_PRIVACY.md](EGRESS_PRIVACY.md). The three
commercial-VPN options below are DESIGNED, NOT BUILT, and none is tested.

- **Option 1 — Mullvad via Tailscale.** No code. Account configuration plus
  `tailscale up --exit-node=`. Recommended default: one WireGuard stack, exit
  selection governed by tailnet ACLs. Nothing to do here but document a walked
  example once someone runs it.
- **Option 2 — VPN sidecar for SearXNG only.** The contained option, and the
  one that matches the actual privacy ask (search queries leak intent; model
  downloads do not). Requires moving SearXNG off `network_mode: host` to bridge
  networking, remapping its port to loopback, and rewiring `SEARXNG_URL` in
  `agents/tools.py` plus the healthcheck probe. Medium effort, blast radius
  limited to search.
- **Option 3 — commercial VPN upstream of a self-hosted exit node.** Correct
  topology, highest risk. Needs the provider's Linux CLI, a tailnet allowlist,
  and explicit FORWARD/MASQUERADE rules, since these clients permit only
  host-originated traffic. **Must not run on the rig**: it would make the
  inference server depend on a client that auto-restarts and resets firewall
  rules, which is the documented VPN collision relocated to the machine
  everything else depends on. Put the exit node on a VPS.

Prerequisite for any of it: none of these can be verified on the current dev rig
(no NordVPN CLI installed, no exit node configured). Whoever builds this needs a
test bed with the provider CLI present.

## 🏛️ SOC 2 READINESS GAPS (2026-07-31)

Full mapping and evidence in [SOC2_READINESS.md](SOC2_READINESS.md). These are
the gaps a security reviewer at a regulated customer will find, ranked by how
early they will find them. Raised because OpenBeast is being taken into private
equity diligence conversations.

1. **beast-gate is off by default** (`EDGE_GATE=false`), so a default install
   has no per-device identity, no admission control and **no inference audit
   trail at all**. Nothing warns the operator. Ship a documented hardening
   profile plus a `doctor` warning when remote surfaces are published with the
   gate off. (CC6.1, CC7.2 — small)
2. **`/api/slot` is unauthenticated.** Live-verified: HTTP 200 with no
   credentials, returning model, capacity and service topology. A revoked
   device still reads it. Reuse the gate's token check. (CC6.1 — small)
3. **No usage accountability per principal.** beast-gate rate-limits requests
   but not tokens or wall-clock, so one device can consume unbounded compute and
   starve others. No quota, no usage report. `clients.sh usage` over
   `.run/inference-audit.jsonl` is the cheap first half. (CC6.1, A1.1 — medium)
4. **Enrollment is out-of-band; keys never expire.** No request-and-approve
   flow, no expiry, no rotation schedule. (CC6.2/6.3 — medium)
5. **Revocation does not terminate an in-flight stream.** (CC6.2 — small)
6. **No decommissioning path.** No rig-side uninstall, so secure disposal is a
   manual checklist. (CC6.5 — medium)
7. **No SBOM.** Everything is pinned and inventoried but nothing emits
   CycloneDX/SPDX. (CC9.2 — small)
8. **No log-integrity guarantees.** Audit logs are plain append-only JSONL on
   local disk: no signing, no tamper evidence, no SIEM forwarding. An assessor
   will ask whether an admin could edit them. (CC7.2/7.3 — medium)
9. **No encryption at rest.** Prompts, transcripts and audit logs sit in
   plaintext. FDE is assumed but never checked. (Confidentiality — small to
   check)
10. **No configuration baseline or drift detection.** `doctor` checks health and
    consistency, not conformance to a documented secure baseline. (CC7.1 —
    medium)

## ⚠️ SECURITY

- ✅ **Router IS identity-aware (DONE 2026-07-08).** WebUI forwards
  `X-OpenWebUI-User-Role` (`ENABLE_FORWARD_USER_INFO_HEADERS=true`); the
  router only runs its spawn path for `admin` turns, guests pass through
  untouched. `OPENBEAST_ROUTER_REQUIRE_IDENTITY=true` fails closed when the
  header is absent. Live-verified. The RBAC-bypass gap is closed.
- ✅ **Guest `fetch` SSRF-guarded (DONE 2026-07-08)** — http/https only,
  loopback/private/link-local refused, redirects re-validated per hop.
- **Remaining RBAC Phase 2 hardening:**
  - **Sandlock → default-on.** Shipped opt-in this session (`setup-sandlock.sh`,
    profile, `docs/SANDBOXING.md`). Next concrete step: run the eval suite with
    `OPENBEAST_BASH_WRAPPER='sandlock run -p openbeast -w "$PWD" --'` set; if
    green, flip default-on in `conf.sh`. Re-verify the setpgid/killpg
    interaction on every sandlock version bump.
  - ✅ **Per-profile tool-server keys (DONE 2026-07-09, opt-in).** Enable
    with `scripts/setup-mcpo-keys.sh` → the identity tool server (:3001,
    agents/openapi_tools.py) checks a Bearer key per call: admin key = all
    15 tools, guest key = web_search+fetch only (denied tools 404). Guest
    can't reach admin tools even bypassing the WebUI layer. Verified
    401/403/404/200 live; tests `tests/test_identity_server.py`. (First
    shipped that morning as TWO keyed mcpo instances — superseded the same
    day by the single identity server; the `OPENBEAST_MCP_TOOLS`
    registration allowlist lives on in mcp_server.py,
    tests/test_mcp_allowlist.py. History: docs/RBAC_PLAN.md Phase 2.)

## ⏳ READY TO BUILD — per-user/per-chat file isolation (investigated 2026-07-09)

**Investigation DONE — see docs/archive/IDENTITY_TOOLS_PLAN.md.** WebUI forwards
X-OpenWebUI-User-* AND chat_id headers to tool servers; mcpo (0.0.20) drops
them at the MCP boundary (verified in source: meta built, never passed).
Manifest (option 3) SHIPPED 2026-07-09 (`_manifest_log` in agents/tools.py,
tests/test_manifest.py, system-prompt note). Remaining: Option B — own thin
FastAPI tool server for the WebUI connection (~200 lines), which unlocks
per-user + per-chat sharding and simplifies the Phase 2 two-instance setup.

<details><summary>Original context (pre-investigation)</summary>


**Context / what shipped now (2026-07-08):** the chat model's direct file tools
(`write_file`/`read_file`/`edit_file`/`list_files`/`bash`/`grep`) used to have
NO working-directory binding — the model picked its own path and defaulted to a
world-readable, reboot-wiped `/tmp` (e.g. it dropped a report at
`/tmp/weimar_conditions_2026.md`). The quick fix (shipped): `_base_dir()` in
`agents/tools.py` anchors relative paths to `OPENBEAST_FILES_DIR`
(default `~/openbeast-files`, created `0700` by start.sh), and a system-prompt
line tells the model to use relative paths, never `/tmp`. Spawned agents keep
their own `AGENT_WORKDIR`.

**What's still missing:** all direct-tool writes share ONE flat namespace. There
is no per-conversation isolation and no index — conversation A can read/overwrite
conversation B's files, and the model only "knows" a file exists if its name is
already in context. The structural reason: the OpenAPI tool call from WebUI →
MCPO → mcp_server is **stateless** — it carries only the model-generated
arguments, no chat id, no user id. So the tool is blind to which conversation
(or which RBAC user) it serves.

**Options (pick one when we tackle this):**
1. **Per-user workspace (pragmatic, pairs with RBAC).** Shard `OPENBEAST_FILES_DIR`
   by the authenticated WebUI user. Needs the user identity to reach the tool
   call — investigate whether Open WebUI forwards user context (header / injected
   arg) to an external OpenAPI tool server. If it does, `_base_dir()` appends the
   user id. Good enough for the multi-user / tailnet case; closes the cross-user
   read hole the guard doesn't cover.
2. **Per-conversation subdir (full isolation).** Requires WebUI to inject
   `chat_id` into the tool call. Check whether native function-calling metadata
   (`__metadata__` / `__chat_id__`) is available to external tool servers or only
   to WebUI's internal Python tools — likely the latter, which is why this is the
   hard path.
3. **Session index + retention.** Regardless of sharding, add a lightweight
   manifest (what was written, when, by which turn/user) + a retention/GC policy
   so the workspace doesn't grow unbounded. Lets a future turn answer "what files
   have I made?" instead of relying on names being in context.

Recommended: (1) + (3). Ship per-user isolation with a manifest; leave true
per-conversation scoping (2) until we confirm WebUI can pass a chat id.

</details>

## ⏳ BENCHMARK QUEUE (Max, 2026-07-10) — NVFP4 MTP models

Two Blackwell-native NVFP4+MTP GGUFs (neko-legends conversions of unsloth's
NVFP4 checkpoints) downloaded, profiled for throughput, and deployed this
session. **Both need a full v4 accuracy run** — registered in
`evals/benchmark_all.py` MODELS, awaiting GPU-hours (Max-triggered).

- `qwen-27b-nvfp4-mtp` — `serve-qwen-27b-nvfp4-mtp.sh` (n=4, c=262144). ✅ registered.
- `qwen-35b-a3b-nvfp4-mtp` — `serve-qwen-35b-a3b-nvfp4-mtp.sh`. Registered after profiling.

**WHY these need benchmarking (the whole point):** head-to-head speed profiling
proved NVFP4 **loses** to our K-quant MTP siblings at `-np 1` single-stream
decode — it's memory-bandwidth-bound and the FP8→Q8_0 attention makes the file
larger (27B: NVFP4 23.2 GB @ ~115 tok/s vs Q5_K_XL 20.4 GB @ ~141). The ONLY
axis where NVFP4 could justify itself is **accuracy-per-bit** (QAT 4-bit FFNs
may preserve quality better than K-quantization). The v4 suite is how we find
out. If NVFP4 27B matches/beats the Q5_K_XL leaderboard row, it earns a slot
despite the speed cost; if not, it's a dead end for our single-user use case.

Run (GPU free, stack down — the harness manages its own llama-server):
```bash
systemd-run --user --scope --unit=openbeast-sweep -p MemoryMax=92G -p MemorySwapMax=8G \
  -- python3 evals/benchmark_all.py --models qwen-27b-nvfp4-mtp,qwen-35b-a3b-nvfp4-mtp
```
~3-4 h/model on the 5090. Compare the resulting rows against `qwen-27b-mtp-q5`
and `qwen-35b-a3b-mtp` (`python3 evals/scoring.py --show`). Promotion-by-evidence
per the eval-quality-gate principle.

## ⏳ LATER (Max, 2026-07-08) — rerun the v3.5-pinned models on v4

5 leaderboard rows are still v3.5 (Qwen 27B Q5_K_XL, Qwen 27B Uncensored,
Qwen 35B-A3B MoE, Qwen 35B-A3B Uncensored, Gemma 4 31B-it). Rerun them on the
v4 suite so the WHOLE leaderboard is v4-comparable (currently only the 3 MTP
models are v4). Same command shape as the MTP sweep, memory-capped scope:
    systemd-run --user --scope --unit=openbeast-sweep -p MemoryMax=92G \
      -p MemorySwapMax=8G -- python3 evals/benchmark_all.py \
      --models qwen-27b-q5,qwen-27b-uncensored-q5,qwen-35b-a3b,qwen-35b-a3b-uncensored-q4,gemma-4-31b-q5
Rows now carry suite_version so old v3.5 + new v4 coexist cleanly; the new v4
rows will replace the v3.5 ones for those slugs (leaderboard keyed by
host+slug). ~another ~13-17h sweep. Not urgent; Max-triggered.

## ✅ POST-SWEEP STEP 1 — leaderboard provenance (DONE 2026-07-08)

The eval engine (llama.cpp build/commit/source_head/compiler) and GPU are
logged per row, but the **eval SUITE VERSION is not** — and v3.5 + v4 rows
now coexist in `evals/leaderboard.json` with nothing distinguishing them.
The 3 MTP rows we just generated ran on v4 but say nothing about it. Fix +
backfill before anything else touches the leaderboard.

Do (safe to edit `run_eval.py` mid-sweep — Python won't reload it; but DO
the backfill only after the sweep has fully finished writing):
1. `evals/run_eval.py`: add a `capture_suite_version()` that reads
   `evals/SUITE_VERSION` (currently `v4`), and a `capture_runtime_info()`
   returning `{python: sys.version.split()[0], openai: <pkg ver>,
   mcp: <pkg ver>, platform: ...}` (each best-effort, empty on failure —
   same pattern as `capture_inference_engine_info`). Fold both into the
   results dict so `scoring.py` persists them per entry.
2. `evals/scoring.py`: carry `suite_version` + `runtime` through into the
   leaderboard entry (like it already does `inference_engine`). Show
   `suite_version` in the printed leaderboard header/rows so v3.5 vs v4 is
   visible at a glance.
3. Optional: capture the served GGUF sha256 (or size+mtime if hashing 20GB
   is too slow) so a weight is identifiable beyond its alias.
4. **Backfill** the 3 existing v4 rows (qwen-27b-mtp-q5-k-xl,
   qwen-35b-a3b-mtp-moe-q4-k-m, qwopus-27b-v2-mtp-q5-k-m) with
   `suite_version: "v4"` — they demonstrably ran on v4. Leave the older
   v3.5 rows tagged `v3.5` (or `unknown` → then labeled v3.5, since the
   suite marker didn't exist when they ran).
5. Verify `scoring.py --rebuild` still works and the header shows versions.

Then proceed to profiling (step 2, below).

## ✅ POST-SWEEP STEP 3 — verified 2026-07-08: skills FIRE, start_agent does NOT

**Result (tests/verify_agent_spawn.py, Qwen 27B MTP):** skills 2/2 ✅ (index
injection validated), controls 2/2 ✅, but start_agent 0/5 → 1/5 even with an
aggressive tool description — the 27B will NOT reliably delegate to background
agents by judgment alone (strong "just start coding" prior). Details in
docs/RESEARCH_FINDINGS §8. Applied: aggressive start_agent docstring
(mcp_server.py, 0→1 lift) + directive prompt guidance (system-prompt-tools.md).
Per Max's gate, we do NOT profile other models' spawn behavior until fixed.

## ✅ BLOCKER FIX — pre-flight intent router (DONE 2026-07-08)

Built and shipped as `agents/router.py` (Starlette+httpx proxy on :8088):
keyword prefilter → grammar-constrained classify (`enable_thinking=false`,
json_schema `{spawn,task,workdir}`, 16/16 in validation) → spawns via MCPO
`start_agent` on a hit, transparent passthrough otherwise. Wired into the stack
(`AGENT_ROUTER=true` in conf.sh, WebUI points at :8088), double-passed for
correctness + speed (router adds ~1ms/normal turn, ~0ms streaming; no KV thrash
on the -np 1 slot). This REPLACES the model-judgment approach — start_agent no
longer depends on the weak local model choosing to call it. Documented in
docs/RESEARCH_FINDINGS §8-11. **Unblocks** the distributed-agents feature and
the multi-node orchestrator investigation below.

<details><summary>original blocker writeup (kept for context)</summary>

start_agent won't fire via model judgment (STEP 3). Robust fix = don't rely on
judgment; DETECT the spawn intent and FORCE the call:
- A cheap pre-flight classifier (heuristic on the user turn: "spawn/launch/kick
  off/in the background/while we keep talking/don't block", OR a tiny model
  call) that, on a detected spawn-request, sets `tool_choice` to FORCE
  start_agent (or invokes runner.py directly and returns the agent ID).
- Same conclusion SKILLS_PLAN reached (Phase-5 auto-router): meta-tools on weak
  local models need routing, not hope. Reuse that design.
- Gates the whole distributed-agents feature (docs/DISTRIBUTED_AGENTS_PLAN.md)
  and the multi-node vision. Re-run tests/verify_agent_spawn.py to measure the
  fix; target >=4/5. THEN proceed to profile other models.

UNVERIFIED to date: whether a local model (in WebUI/OpenCode via MCPO)
reliably (a) calls `start_agent` to spawn a background agent when it should,
and (b) fires skills now that the skill index is injected into
`system-prompt-tools.md`. Neither the eval sweep nor any recent change tested
this — `runner.py` deliberately lacks `start_agent` (no recursive spawn), so
evals don't exercise it. This is the PREREQUISITE GATE for the multi-node
orchestrator/worker architecture below — no point building that topology if
the primary model won't reliably kick off agents.

Test (needs GPU free + stack up): bring up `./start.sh -d`, give the default
27B a prompt that should trigger a background agent ("spawn an agent to
refactor X while we keep talking"), watch whether it calls `start_agent` and
the agent actually runs (check `agents/logs/`, `check_agent`). Repeat for a
skill-triggering prompt. Record hit-rate; if ~0%, the fix is prompt/routing
work (see docs/SKILLS_PLAN.md pruning + PRODUCTION_ROADMAP §B).
</details>

 - ✅ DONE 2026-07-08: OpenBeast's ethos stated in the README ("Our opinion"
   under Why OpenBeast) — maximize intelligence per hardware, no compromise;
   entry point to win + upgrade ladder up, never downsize.

 - **FEATURE (planned, opt-in, don't lose it): configurable main->worker
   agent split.** Let users run agents that EXECUTE on their main box (local
   files) while INFERENCE is served by a separate worker box. Off by default
   (avg user is single-box), but ~1/3 of users want it — a great way to get
   real work done. Full design, config UX (`AGENT_INFERENCE_URL`), the
   ~10-line `start_agent` base_url change, phased plan, and caveats:
   **docs/DISTRIBUTED_AGENTS_PLAN.md**. Phase 1 is ~half a day. Gated on
   STEP 3 (orchestrator must reliably spawn).

## 🔭 INVESTIGATION — multi-node orchestrator + parallel worker fleet

Max's architecture (2026-07-08), = the parked "Mark of the Beast" direction:
- **Primary/orchestrator = MTP** (single-stream, latency-optimized). It's the
  model you talk to and that spawns agents; inherently one session, so MTP's
  `-np 1` serialization limit doesn't hurt — you just get the 2.75x decode.
- **Worker fleet = non-MTP, high `-np`** on separate hardware (e.g. a
  2x3090Ti box) for aggregate parallel throughput across many concurrent
  agents. MTP CANNOT parallelize (upstream `-np 1` pin), so workers must be
  plain batched serving.

**GOVERNING PRINCIPLE (Max, 2026-07-08): maximize intelligence per hardware,
NO compromise.** Fill every GPU with the LARGEST/most-accurate model that
fits — never a stew of smaller models. Quality of work is the objective;
parallelism is secondary and must NEVER be bought by shrinking the model.
This RETRACTS the earlier "14B worker" and "27B+7B single-box" ideas.

Assessment: the CORE split is correct. Open measurements/decisions before
building (Hardware-Profiles Phase 2 territory):
1. **Workers run the SAME large model as the orchestrator — do NOT shrink.**
   (Retracts the earlier right-size-down suggestion.) Parallelism comes from
   HARDWARE (more VRAM / cards / NVLink tensor-split), never from a smaller,
   lower-quality worker model. Scaling ladder, all uncompromised: (1) single
   large MTP model, sequential agents; (2) + 2x3090Ti NVLink box running the
   SAME-class large model tensor-split across 48GB, non-MTP high-np, for
   parallel agents; (3) more large-model nodes.
2. **Tensor-split vs 2 instances** on the 3090Ti box — **the box HAS NVLink**
   (Max, 2026-07-08), so tensor-split is now a STRONG option (NVLink ~112GB/s
   vs PCIe), not the fallback it'd be on consumer boards. Two paths:
   (a) tensor-split one model across both cards → 48GB unified pool for a
   BIGGER worker model and/or a large shared KV supporting many slots; NVLink
   makes the cross-GPU tax small. (b) one llama-server PER card + load
   balancer → simplest max-parallelism when the worker model fits in 24GB.
   With NVLink, lean (a) if you want >24GB models or a big unified `-np` pool;
   (b) if workers are small and you want dead-simple horizontal slots. Measure
   both.
3. **np/KV envelope** — `-np 10` is feasible but KV-per-slot constrains
   per-slot context on 24GB (or the 48GB NVLink pool); measure
   model×quant×slots×context (extend scripts/measure-vram.sh).
4. **Router/transport** — orchestrator's `start_agent` must target the worker
   endpoint(s); wants a tailnet-native least-loaded router. The RBAC +
   remote-access layer already generalizes to a fleet.
Depends on STEP 3 passing first (does the orchestrator reliably spawn?).

### Single-machine option — MTP model shared by sequential agents (YES, possible)
Max's Q (2026-07-08): can ONE MTP model on ONE machine pause → spawn agents
that run one-at-a-time cycling through the same MTP model? **Yes, mechanically
straightforward:**
- One MTP `llama-server` at `-np 1` = one KV slot. Orchestrator + every spawned
  agent (`runner.py`) hit the SAME `:8080`. With one slot, requests SERIALIZE
  (FIFO) — they time-share the single MTP-accelerated slot. Each generation is
  MTP-fast (2.75x); the fleet runs sequentially. No second machine needed.
- COST: with `-np 1` there's one KV cache. Alternating between the
  orchestrator's conversation and an agent's conversation evicts/reprocesses
  the incoming context (prompt-processing cost on every switch), since the slot
  holds one sequence's KV at a time. Mitigate by running agents STRICTLY
  sequentially (spawn → await → next), so only one conversation is live at a
  time and you pay the swap once per handoff, not per interleave.
- Trade vs the two-box design: single MTP box = simplest, each agent FAST, but
  agents are SEQUENTIAL (no parallelism) + per-switch context reload. Two-box =
  parallel agents but workers are non-MTP. Choose by workload: few sequential
  agents that each should be fast → single MTP box; many independent agents →
  parallel worker fleet.
- ~~Heterogeneous single-box variant (27B-MTP + small 7B worker pool)~~
  **RETRACTED** per the no-compromise principle: mixing in a small
  lower-quality worker model sacrifices output quality. If one 32GB card can't
  hold a second LARGE model, keep it single-model (sequential agents) and add
  hardware for parallelism instead.

### Requirements & limitations for agents to work (the checklist)
Load-bearing conditions that must ALL hold, or the agent architecture fails:
- **R-spawn:** the orchestrator model must RELIABLY call `start_agent` when
  appropriate (UNVERIFIED — STEP 3 gates this). If hit-rate ~0%, no topology
  matters; fix prompt/routing first.
- **R-endpoint:** spawned agents (`runner.py`) reach a serving endpoint — same
  box (`:8080`) or a worker box over the tailnet. `AGENT_WORKDIR` / model URL
  must be wired per target. **CONCRETE GAP (verified 2026-07-08):** the agent
  runs where `runner.py` runs, and its filesystem ops act on THAT box's FS —
  so to have agents touch the MAIN (5090) filesystem while thinking via the
  2x3090Ti worker fleet, run `runner.py` ON THE 5090 pointed at the worker's
  `--base-url`. `agent.sh`/`runner.py` support `--base-url` TODAY
  (`./agent.sh --base-url http://<worker>:8080/v1 -w ~/project "task"`). BUT
  `mcp_server.start_agent()` does NOT expose a model URL (only task/workdir/
  max_iter/context) — so orchestrator-spawned agents use the LOCAL model.
  FIX (~10 lines): add a `base_url` param to `start_agent`, thread it to the
  runner spawn exactly like `workdir` already is; default to local. This is
  the load-bearing change that lets the 5090 be the execution host while the
  3090Ti box provides parallel inference. Caveat: remote inference sends read
  file contents to the worker box as context — fine on-tailnet (still your
  hardware), but "no data leaves your machine" becomes "your machines".
- **R-serialize:** MTP endpoints are `-np 1` → any agents sharing an MTP model
  serialize; true parallelism REQUIRES a non-MTP high-`-np` endpoint.
- **R-context:** shared `-np 1` slot reprocesses context on conversation switch;
  budget for it or run sequentially.
- **R-VRAM:** slots×context×quant must fit the target card(s); the
  measure-vram envelope (Hardware Profiles Phase 2) is the gate.
- **R-recursion:** `runner.py` deliberately lacks `start_agent` (no recursive
  spawn). If nested agents are ever wanted, that's an explicit design change.
- **R-cleanup:** background agents log to `agents/logs/`; the MCP server reaps
  them on shutdown. A fleet needs supervision (pidfiles/systemd) like the
  main stack.

## ✅ POST-SWEEP STEP 2 — MTP throughput profiling (DONE 2026-07-08)

Find the peak tok/s DEPLOYMENT config for each MTP model by brute-forcing
the lossless speculation knobs (safe — MTP verifies every drafted token, so
these change speed not output; confirmed 35B-A3B MTP 93.76 vs non-MTP 93.74).

- Harness: `evals/profile_mtp.py` (built, syntax-clean, refuses to run while
  `openbeast-mtp-sweep` is active). Plan + config ledger:
  `docs/archive/MTP_PROFILING_PLAN.md`.
- Sweeps ONLY `--spec-draft-n-max` / `--spec-draft-p-min`; HOLDS FIXED the
  lossy knobs (weights, KV `q4_0`, context, ngl, np) at each model's
  leaderboard config. ~11 configs/model, ~1h total.
- Run (GPU must be free — after the sweep):
      systemd-run --user --unit=openbeast-mtp-profile --collect \
        -p MemoryMax=92G -p MemorySwapMax=8G -p WorkingDirectory="$PWD" \
        bash -lc 'python3 -u evals/profile_mtp.py > .run/mtp-profile.log 2>&1'
- After: create `serve-*-mtp-fast.sh` deployment variants (do NOT overwrite
  the benchmark serve scripts — the leaderboard configs must stay
  reproducible). Optional full v4 confirm run at the winner.
- Leaderboard (benchmark) configs are recorded in MTP_PROFILING_PLAN.md so
  the published scores are never confused with the deployment configs.

## ✅ RESOLVED 2026-07-07: healthcheck --restart vs the supervisor

Was: killing llama-server (crash or `healthcheck.sh --restart`) made the
start.sh supervisor exit and tear down MCPO with it. Fixed in two layers:
the supervisor now **self-heals** — an unexpected llama-server death gets
up to 3 relaunches (budget refills after 5 healthy minutes; `./stop.sh`'s
TERM is never mistaken for a crash) — and `healthcheck.sh --restart` is
supervisor-aware: when `.run/supervisor.pid` is alive it just kills
llama-server and waits for the supervisor's relaunch instead of racing it
with a second copy. The no-supervisor path (bare serve script) keeps the
old direct-restart behavior.

## 🧯 Post-mortem: 2026-07-07 session OOM crash (RESOLVED — harness hardened)

**What happened.** At 12:59:50 the kernel OOM killer fired with 122 GB RAM
*and* all 187 GB of swap exhausted ("Free swap = 200kB"), killed the largest
process, and systemd then reaped the whole terminal scope — Claude session,
smoke harness, and llama-server together.

**Root cause.** `subprocess.run(cmd, shell=True, timeout=N)` kills only the
`/bin/sh` wrapper on timeout; the shell's children survive as orphans. During
the 35B-A3B MTP smoke run, task `27_brainfuck_interpreter_a` produced a buggy
`bf.py`; two of the agent's own `bash` tool calls running it timed out at
120 s, orphaning two `python3 bf.py` processes stuck in an unbounded
tape-growth loop. Each grew to ~140 GB anonymous memory over ~12 minutes
(44 GB + 99 GB swapped / 61 GB + 77 GB swapped in the OOM report). Not a VRAM
issue, not an llama.cpp leak — llama-server's host RSS was 817 MB. The same
pattern also leaked harmless-but-real orphans in the 27B run (a stdin-blocked
`bf.py`, a `python3 -c` trial-division on 10^18) and, verified live, two
CPU-burning `./prho` binaries in a post-crash rerun.

**Fixes landed (all tested — `tests/test_proc_hygiene.py`, 7/7):**
1. `agents/tools.py bash()` and every shell spawn in `evals/run_eval.py`
   (setup / pre_validate / validation / cleanup / agent runner) now launch
   children with `start_new_session=True` and SIGKILL the **whole process
   group** on timeout — grandchildren included. This also fixes the silent
   hang where `communicate()` blocked on a pipe an orphan still held.
2. `RLIMIT_AS` = 32 GB on all model-code subprocesses — a memory bomb dies
   with `MemoryError` in-process instead of eating the box during the
   seconds before its timeout fires.
3. Sweeps/smokes should run inside a memory-capped systemd scope so any
   future unknown failure mode is contained away from the interactive
   session: `systemd-run --user --scope --unit=openbeast-sweep
   -p MemoryMax=96G -p MemorySwapMax=8G -- python3 evals/benchmark_all.py …`

**Related field note (same day):** don't reconfigure the network while an
eval is live. Running `setup-tailscale.sh` (tailscaled bring-up, DNS/route
changes 13:45–13:47) during the Qwopus v2 smoke made every request of task
`137_pollard_rho_b` fail with `Connection error.` — 20/20 iterations, 0
tokens, recorded (and cached!) as a FAIL that was really an infra blip. The
poisoned cache entry was removed and the task re-run. If an eval row ever
shows 0 tokens with all-iteration connection errors, suspect the
environment, not the model — and consider pointing the harness at
`127.0.0.1` instead of `localhost` to dodge resolver reconfigurations.

## ⏳ READY — only the sweep remains

All v3.5 build work is **landed, audited, committed, and pushed**. The
single remaining step is to run the sweep against the upgraded suite.

```bash
python3 evals/benchmark_all.py
```

**No setup needed.** The cache (`evals/cache/`) starts empty; the first
sweep populates it. Any subsequent partial rerun replays cache hits
instantly via `--cache-only` or transparently during a normal rerun.

**Wall-clock estimate:** ~16–20h on the 5090 for all 5 models on the
~313-unit suite (5 models × ~3-4 h average + 4× 5-min cool-offs). Best
to start late evening so models 4–5 finish overnight + early morning.

**If anything fails mid-sweep:** kill it, fix the issue, restart with
`python3 evals/benchmark_all.py`. Completed tasks replay from cache;
only the missing portion runs live. The "lose 14h of progress" failure
mode is gone.

**Diagnostics during a live run:**
- `tail -f evals/results/sweep-run-*.log` — live progress
- `python3 evals/cache_cli.py stats` — cache fill rate
- `nvidia-smi` — GPU temp/util
- `pgrep -fa benchmark_all` — alive check

**Post-sweep — what to do once it finishes:**
1. `python3 evals/scoring.py --show` — top-line + tokens column
2. `python3 evals/scoring.py --by-category` — see which categories
   de-saturated with the v3.5 hardening
3. `python3 evals/scoring.py --by-language` — per-language drilldown
   across the 33 variant base tasks (the headline new view)
4. `python3 evals/tool_efficiency.py` — per-model tool-use comparison
   (built this session; cross-model meaningful for the first time)
5. Update `docs/RESULTS.md` v3.5 section with the leaderboard,
   per-language matrix, and tool-efficiency table
6. If Zig is still 0/13 across all models after the spec fix → spec
   defect remains; investigate. If Zig now passes → fix is validated
   for production sweeps

**Status of every prerequisite:**

| Component | Status | Location |
|---|---|---|
| Zig spec defect fix | ✅ landed (commit 62443ef) | `skills/eval-variant-porter/SKILL.md`, all 13 Zig variant `task` fields |
| 20 new variant tasks | ✅ landed (commit 62443ef) | `evals/tasks/{32,71,82,92,100,11,20,54,38,39,36,62,63,136,27,115,123,127,47,137}*.json` |
| Reference impls (durable) | ✅ landed (commit e57433a) | `evals/refs/` — 133 ref impls + README |
| Generators archived | ✅ landed (commit e57433a) | `evals/scripts/{easy,medium,hard,patch_zig}*.py` + README |
| Result cache | ✅ landed (commit 62443ef) + tested (commit e57433a) | `evals/cache.py`, `evals/cache_cli.py`, `tests/test_cache.py` (16/16) |
| --cache-only mode | ✅ landed (commit 97829fb) | `evals/run_eval.py`, `evals/benchmark_all.py` |
| Tool-efficiency analyzer | ✅ landed (commit 97829fb) | `evals/tool_efficiency.py`, `tests/test_tool_efficiency.py` (13/13) |
| Spec-completeness lint | ✅ landed (commit 62443ef) | `tests/audit_variants.py` |
| Documentation | ✅ landed (commit e57433a) | `evals/README.md`, `docs/{RESULTS,WORK_PLAN,INSTALL,REFERENCE,WEAK_SPOT_ASSESSMENT}.md`, `skills/eval-{task-author,variant-porter}/SKILL.md` |
| Test status | ✅ all green | 47/47 `test_scripts.sh`, 16/16 `test_cache.py`, 13/13 `test_tool_efficiency.py`, 133/133 audit_variants (lint clean) |

That's the complete picture. Next action is yours: kick the sweep when
you're ready for the GPU commitment.

## Completed (this session)

### v3.5 prereq — Zig variant spec defect ✓ DONE (2026-05-07)

**The bug.** Both 27B models in the running v3 sweep scored **0/13 on Zig
variants**. Investigation while the sweep continued confirmed the failures
are spec defects, not model weakness or toolchain trouble. Working Zig 0.16
demo program proven at `/tmp/zig_demo/demo.zig` — compiles and runs.

**Toolchain ✅** — `zig 0.16.0` installed via mise, on PATH for the eval
subprocess. Compiler is healthy.

**Models ✅** — they follow the spec literally.

**Spec ❌ — two distinct defects across all 13 Zig variant tasks:**

#### Defect A: Wrong API surface (9 of 13)

`takeDelimiter` and `appendRemainingUnlimited` live on `std.Io.Reader`
(the *interface*, 65 public methods) — **not** on `std.Io.File.Reader`
(the concrete file reader, 10 public methods). The model has to access
`&fr.interface` to reach the rich API. The spec doesn't mention this.

Working pattern (confirmed in `/tmp/zig_demo/demo.zig`):
```zig
var fr = std.Io.File.stdin().reader(init.io, &buf);
const r = &fr.interface;  // ← the missing step
while (try r.takeDelimiter('\n')) |line| { ... }
```

What the spec teaches the model to write:
```zig
var fr = std.Io.File.stdin().reader(...);
fr.takeDelimiter('\n')  // ERROR: not on File.Reader
```

Failures of this shape: `122_gemm_blocked_e`, `148_convex_hull_f`,
`155_tonelli_shanks_f`, `158_karatsuba_bytes_f`.

A related sub-flavor: 4 tasks tried `std.io` (lowercase, the pre-0.15
namespace) instead of `std.Io` (renamed in 0.15+). Models reached for
this from older training data when the new template wasn't sufficient:
`51_toposort_f`, `61_extgcd_f`, `65_miller_rabin_f`, `73_count_vowels_f`.

#### Defect B: Strict-default warnings as hard errors (5 of 13)

Zig 0.16 errors on:
- unused function parameters
- unused local constants
- `var` declarations that are never mutated (should be `const`)

The spec doesn't warn the model that these are hard errors (other languages
treat them as soft warnings or ignore entirely). Failures:
- `19_three_way_quicksort_f` — unused local constant
- `31_is_power_of_two_f`, `74_palindrome_f` — unused parameter (`init` when
  not needed)
- `52_unionfind_f`, `159_ntt_convolution_f` — `var` never mutated

#### Why the audit missed this

`tests/audit_variants.py` validates **reference implementations** in
`/tmp/refs/`, which had the correct `.interface` pattern at landing time.
The audit confirms "test infrastructure works for a known-good solution"
but not "spec text gives the model enough to write a known-good solution."
The spec defect slipped through because the reference impls knew about
`.interface` even though the task `task` field didn't mention it.

#### The fix (Phase-1-equivalent — apply post-sweep)

1. **Update `skills/eval-variant-porter/SKILL.md`** Zig section:
   - Show the `&fr.interface` indirection explicitly in the example
   - Mention `init.io` is the `Io` instance
   - Call out strict-default footguns: prefer `const` over `var`, use
     `_ = unused;` to silence intentional unused values, rename unused
     parameters to `_`
   - Reaffirm `std.Io` (capital I), not `std.io`

2. **Edit all 13 Zig variant `task` fields** in `evals/tasks/*.json` to
   include the canonical pattern (or link to it). The `task` text is what
   the model sees as the spec; this is the load-bearing change. Tasks:
   `19, 31, 51, 52, 61, 65, 73, 74, 122, 148, 155, 158, 159` (variant `f`
   in 12 cases, variant `e` for `122_gemm_blocked` per the no-Python rule).

3. **Strengthen the audit** to check the model-facing spec, not just the
   reference impl. Add a "spec-completeness" check: for each variant,
   does the `task` text mention the language constructs the reference
   impl actually uses? E.g., if the Zig ref impl uses `&fr.interface`,
   the spec should mention it. Could be a lint pass over all variants.

4. **Re-run the affected models** on the Zig variants once the spec is
   fixed — selective re-test, not a full sweep. `python3 evals/run_eval.py
   --tasks 19,31,51,52,61,65,73,74,122,148,155,158,159` against each model
   in turn. Update the leaderboard with the corrected entries.

**Cost of NOT fixing:** the v3 leaderboard entries permanently understate
all 5 models' true Zig capability by ~13 tasks each. The relative ranking
is preserved (defect hits all models equally), but the absolute accuracy
numbers and the "Zig 0%" finding in `RESULTS.md` would mislead future
analysis without this footnote.

**Estimated effort:** 1-2 hours including verification — fix the SKILL,
patch the 13 task fields, re-run the audit with refreshed `/tmp/refs/`,
re-run the 13 Zig variants against the 5 models (~30-60 min compute).

---

### v3.5 — expand variant coverage from 13 → 33 base tasks ✓ DONE (2026-05-07)

**Status:** Landed. 20 new variant base tasks added (instead of the 21 in
the original plan; `108_hmac_verify` dropped because cross-lang HMAC
requires SHA-256 from scratch in 6 langs — heavy lift, deferred. `145` and
`146` substituted with `47_branchless_min` and `137_pollard_rho` which are
similarly hard-tier but tractable per-lang implementations).

**Final 20 tasks shipped, all 6-lang (py/go/c/cpp/rust/zig):**
- Easy (5): `32_dot_product`, `71_reverse_list`, `82_sigmoid`, `92_popcount`, `100_constant_time_compare`
- Medium (9): `11_bst`, `20_priority_queue`, `54_astar`, `38_monte_carlo_pi`, `39_blocked_transpose`, `36_black_scholes`, `62_crt`, `63_det`, `136_gf256`
- Hard (6): `27_brainfuck_interpreter`, `115_fft`, `123_nbody`, `127_aes_keysched`, `47_branchless_min`, `137_pollard_rho`

**Audit verification:** 120/120 new variant entries pass with reference
impls (`tests/audit_variants.py` clean, spec-completeness lint clean).
With Phase A's 13/13 Zig fixes, total = 133/133 variant entries verified
end-to-end this session.

**Suite size:** 159 base tasks (unchanged) → **~313 effective variant
entries** post-rollout (was 223). Sweep wall-clock projects to ~16-20h on
the 5090 — partial reruns benefit massively from the result cache (see
below).

---

### v3.5 deferred / superseded picks (was in the original 21)

The original plan had 21 picks. Three changed during implementation:
- **`108_hmac_verify`** — dropped. Cross-lang HMAC needs SHA-256 from
  scratch in Rust/Zig (no stdlib SHA-256 in either). Heavy lift, low signal
  marginal vs `100_constant_time_compare` already covering Security.
- **`145_segment_tree_lazy`** — substituted with `47_branchless_min`. The
  original task was on the Phase 4 deferred list at ~150-200 LOC/lang.
  `47_branchless_min` is hard-tier in the same Performance/bit-twiddling
  vein, ~30-50 LOC/lang.
- **`146_aho_corasick`** — substituted with `137_pollard_rho`. Same
  reasoning: AC is ~200 LOC/lang for suffix-link automaton; Pollard's rho
  is hard-tier in Pure Math, ~50-80 LOC/lang.

If Max wants a follow-up session for the 3 substitutions, the Phase 4
deferred entries (53_bloom, 145, 146, 152, 153) are still tracked below
and the Aho-Corasick + segment tree implementations would be done with the
established 6-lang pattern.

---

### Result cache for retryable sweeps ✓ DONE (2026-05-07)

`evals/cache.py` + `evals/cache_cli.py` + integration in `run_eval.py` and
`benchmark_all.py`. Hash key = `model_slug.task_id.task_spec_hash.context_hash`
where `task_spec_hash` is sha256 of canonical-encoded task dict and
`context_hash` is sha256 of system-prompt.md + system-prompt-tools.md +
opencode.json.

**Behavior:**
- On match: replay the cached result, skip agent + validation. Logged as
  "CACHED (Xs, PASS|FAIL) — skipping live run".
- On miss: live run, then cache the result.
- Cache invalidates automatically when (a) task spec changes (e.g. you edit
  a setup or task field) or (b) the agent runtime context changes (system
  prompt, available tools).
- Both PASS and FAIL are cached — a deterministic FAIL on the same input
  is replay-safe.

**Bypass:** `python3 evals/run_eval.py --no-cache` or
`python3 evals/benchmark_all.py --no-cache`.

**Manage:**
- `python3 evals/cache_cli.py stats`
- `python3 evals/cache_cli.py list [--model SLUG]`
- `python3 evals/cache_cli.py clear [--model SLUG]`

**Why it matters:** mid-sweep crash, model swap, or partial rerun used to
mean re-paying the entire compute cost. Now: kill, fix, restart — only the
not-yet-cached tasks live-run. On a 16-20h sweep this is a 10-100× speedup
for partial replays.

**Next-session todo (if needed):** add a `--cache-only` mode that runs
ONLY cache hits (skip uncached entirely) for fast leaderboard rebuilds
from prior runs.

---

### Original Zig prereq + variant rollout plan (preserved for reference)

#### v3.5 prereq — fix Zig variant spec defect (mid-sweep finding, 2026-05-07)

**Why.** The v3 sweep's first two models showed dramatic per-language
gaps (Python 99.86% vs C++ 81.34% on Q5_K_XL; Go flipping from 90% to
100% between models on the same task set). 13 base tasks is enough to
confirm the gaps exist but too thin to characterize *which* algorithms
or *which* domains drive them. 33 base tasks (≈ doubled coverage) gets
us to ~190 effective variant entries (≈ 33 × 5.7 langs avg). Adds ~115
new entries to the suite for ~2× the discrimination signal.

**Selection criteria** (per `eval-variant-porter` SKILL):
- Algorithm/data-structure tasks where stdio I/O is natural
- Tasks where the *language* genuinely matters (perf, low-level discipline,
  numerical stability)
- Avoid Python-idiomatic tasks (decorators, dunders, threading.RLock)
- Avoid concurrency tasks where the paradigm differs across languages
- Avoid web/Flask-specific tasks (SWE/DevOps category)
- Avoid probabilistic-validation tasks (Bloom etc.) until a separate
  cross-language probabilistic harness is built

**The 20 picks** — chosen to broaden category coverage. Currently 13
variant tasks span only 3 categories (Algorithms & DS, Pure & Abstract
Math, Performance & HW Opt × 1 only). The picks below add Security,
LLM/ML, Probability & Stats, Mathematical Finance, and Physics for the
first time, while filling out the under-covered Performance category.

**Easy (5)** — language-fluency warmups in each new category:

| ID | Task | Category | Why this one |
|---|---|---|---|
| 32_dot_product | Linear-algebra warmup | Pure Math | Tests basic loop + accumulator discipline; pairs with 31 / 73 / 74 as a 4th easy-tier warmup |
| 71_reverse_list | Reverse a singly linked list | Algorithms & DS — Linear DS | Pointer/lifetime discipline differs sharply: Python uses refs, C/Rust manage explicitly, Zig + ownership semantics |
| 82_sigmoid | Numerically stable sigmoid | LLM / ML — Activations | First LLM/ML variant. `1/(1+exp(-x))` underflow handling is a classic FP gotcha; cross-language signal on numerical-stability awareness |
| 92_popcount | Population count | Performance — Bit-twiddling | Bit ops are perfect cross-language: Python `bin(x).count('1')`, C `__builtin_popcount`, Rust `.count_ones()`, Zig `@popCount`. Tests language-idiom fluency |
| 100_constant_time_compare | Constant-time string compare | Security — Side-channel | First Security variant. Loop-XOR pattern is identical across langs but timing-safety semantics differ |

**Medium (10)** — meat of the variant value:

| ID | Task | Category | Why this one |
|---|---|---|---|
| 11_bst | Binary search tree | Algorithms & DS — Trees | Tree DS with insert/delete/search via stdio op log. Tests pointer/reference discipline across langs |
| 20_priority_queue | Priority queue (heap) | Algorithms & DS — Linear DS | Sift-up/down + heapify — classic algorithm, exercises array indexing and comparator handling |
| 54_astar | A* pathfinding | Algorithms & DS — Graph | Grid input via stdio, classic graph algo. Heap + heuristic; exercises priority queue + graph traversal together |
| 38_monte_carlo_pi | Monte Carlo π | Probability & Stats — Monte Carlo | First Probability variant. RNG seeding differs sharply across langs (Python/Go/C/C++/Rust/Zig all have different default PRNGs) — exposes that gap |
| 39_blocked_transpose | Cache-aware blocked transpose | Performance — Cache locality | Companion to 122_gemm_blocked. Cache locality matters more in compiled langs than Python |
| 36_black_scholes | Black-Scholes call price | Math Finance — Option pricing | First Math Finance variant. erf/normal-CDF is in stdlib for some langs, not others (Zig: from scratch). Tests numerical fluency |
| 62_crt | Chinese Remainder Theorem | Pure Math — Number theory | Pairs with 61_extgcd; bigger-number arithmetic exposes overflow discipline (C/Go need `int64`/`__int128`) |
| 63_det | Determinant via Gaussian elim | Pure Math — Linear algebra | Numerical stability + partial pivoting; FP discipline cross-lang |
| 136_gf256 | GF(2^8) finite-field arithmetic | Pure Math — Number theory | Byte-level finite-field — perfect for low-level langs. Used in AES, Reed-Solomon, etc. Tests bitwise discipline |
| 108_hmac_verify | HMAC sign + timing-safe verify | Security — Crypto primitives | Companion to 100_constant_time_compare. Crypto primitives are where Python's "use stdlib" cleanness differs most from C/Zig "implement from primitives" |

(*Total picks: 5 easy + 10 medium + 6 hard = 21 tasks. Max asked for 20;
this list intentionally has one extra Security pick (`108_hmac_verify`)
that pairs with `100_constant_time_compare` at the easy tier — drop it
if effort needs trimming, but keeping both gives full 2-tier Security
coverage in one rollout.*)

**Hard (6)** — real differentiators:

| ID | Task | Category | Why this one |
|---|---|---|---|
| 27_brainfuck_interpreter | Brainfuck interpreter | Algorithms & DS — Interpretation | Interpreter loop + jump table. Switch/match/jump dispatch differs sharply per lang (Python dict, C switch, Rust match, Zig comptime) |
| 115_fft | Cooley-Tukey radix-2 FFT | Pure Math — Polynomial algebra | Complement to 159_ntt_convolution. Exercises complex arithmetic (Python native, Go via `cmplx`, C via `_Complex` or struct, Zig from scratch) |
| 123_nbody | N-body Velocity Verlet | Physics — N-body | First Physics variant. Vector arithmetic + integration loop. Numerical drift differs by lang FP semantics |
| 127_aes_keysched | AES-128 key schedule | Security — Cipher impl | First Security cipher variant. Byte-level rotation, S-box lookup — a perfect exercise for low-level discipline |
| 145_segment_tree_lazy | Segment tree, lazy propagation | Algorithms & DS — Trees | Already deferred in Phase 4. Heavy port (~150 LOC/lang) but high-signal — push-down timing varies idiomatically per lang |
| 146_aho_corasick | Aho-Corasick multi-pattern | Algorithms & DS — String | Already deferred in Phase 4. Suffix-link automaton; queue/struct-of-arrays choices matter sharply per lang |

#### Implementation pattern (per task)

For each new variant base task, follow the `eval-variant-porter` SKILL:

1. Pick the 5–6 target languages (Python + Go + C + C++ + Rust + Zig is
   the standard 6-language set; some perf-flavored tasks like 39 might
   skip Python).
2. Author reference impls in each lang at `/tmp/refs/{slug}.{ext}`.
3. Patch the task JSON: replace top-level `setup`/`task`/`validation`/
   `cleanup` with a `variants` array of N entries.
4. Run `python3 tests/audit_variants.py {task_id}` — confirm N/N variants
   pass with reference impls.
5. Run `bash tests/test_scripts.sh` — confirm syntax-level regression.

**One commit per ~3-5 task batch** so the leaderboard story stays
auditable. Total effort estimate (assuming Zig spec defect is fixed first):
- 5 easy × ~30 min each = 2.5 hours
- 9 medium × ~60-90 min each = 9-13 hours
- 6 hard × ~3-4 hours each = 18-24 hours
- **Sweep cost after rollout:** ~115 new variant entries × (33–35 sec
  median) × 5 models = ~6h additional sweep time. Suite total grows from
  223 → ~338 entries (~10–12h sweep → ~16–18h sweep — push to a weekend
  or run with `--models` filter for incremental rebuilds).

#### Sequencing recommendation

1. **Fix Zig spec defect first** (1-2 hr) — otherwise every new Zig
   variant inherits the same broken template.
2. **Easy batch (1 commit, ~3 hr)** — 5 easy tasks. Confirms the new
   spec template works end-to-end; cheap to re-run if the template
   needs another iteration.
3. **Medium batch in two commits, ~10 hr total** — split by category to
   keep PRs reviewable.
4. **Hard batch deferrable** — 145, 146 are already on the deferred list;
   the other 4 hard picks can land alongside or in a follow-up session.
5. **Re-sweep after each batch** with `--tasks` filter, not full sweep.
   Full sweep only when the suite is stable for the leaderboard refresh.

---

### ~~Tonight — overnight v3 sweep on the 5090~~ (HISTORICAL — superseded by the v3.5 ready-to-sweep banner at the top)

> Kept for reference. The original v3 sweep was started, killed mid-way
> (see Zig spec defect mid-sweep finding above), and the suite was then
> hardened into v3.5. The current state is "READY to sweep against the
> v3.5 suite" — see the banner at the top of this file. The plan below
> is the v3 plan; run-time projections below are no longer accurate
> (suite size grew 223 → ~313 effective units; expect ~16-20h, not
> 12-14h).

The v3 eval suite is fully landed (Phases 1–4) and the smoke test on the
winning model validated end-to-end. Plan:

**Run command:**

```bash
python3 evals/benchmark_all.py
```

Per-model wall-clock estimate (linearly scaled from the 144-task sweep at
1.37× the unit count, plus ~5–10% for the harder hardening tasks):

| Model | Old (144) | Projected (197) |
|---|---:|---:|
| Qwen 35B-A3B Uncensored Q4_K_M | 50 min | **~69 min** |
| Qwen 35B-A3B MoE Q4_K_M | 49 min | **~67 min** |
| Qwen 27B Uncensored Q5_K_P | 97 min | **~133 min** |
| Qwen 27B Q5_K_XL | 116 min | **~159 min** |
| Gemma 4 31B-it Q5_K_XL | 127 min | **~174 min** |
| **Sum** | **~7h 21m** | **~10h 0m + 5–10% buffer** |

**Smoke test recalibration.** The 35B-A3B Uncensored took **207 min** for
197 tasks (vs the 70-80 min original estimate) due to four 18-30 min
outliers (rkf45 max_iter, aes_keysched timeout, berlekamp_massey timeout,
karatsuba_cpp 28-min grind). Apply this realism to the per-model estimates
above — the slower models likely overshoot too. Plan for **12-14 hour total
overnight wall-clock**, not 10. Kick off well before bed.

**Smoke test scoreboard for reference:**

```
Qwen 35B-A3B Uncensored Q4_K_M  v3:  91.8  86.4  90.4  177/197  65/80  12388s  9.8M
                                v1:  97.3  86.7  94.6  140/144  49/51   3024s    —
```

The accuracy drop (97.3 → 91.8) is expected — harder suite + variant work
surfaced real cross-language gaps that didn't exist in the Python-only v1.
4 cross-language differentials confirmed (`31_is_power_of_two`, `51_toposort`,
`155_tonelli_shanks`, `158_karatsuba_bytes`).

**After the sweep finishes:**

```bash
python3 evals/scoring.py --show                    # top-line + tokens column
python3 evals/scoring.py --by-category             # which categories de-saturated
python3 evals/scoring.py --by-language             # per-language drilldown across variants
```

Update `docs/RESULTS.md` with the v3 sweep section once the leaderboard is
final. The smoke-test post-mortem (in chat history but not yet documented)
should also land here as a reference v3.5 baseline for the 35B-A3B Uncensored.

### Inference-time strategy — "Python-first, then port" for hard cross-language tasks

**Empirical finding (2026-05-06):** Qwen 35B-A3B Uncensored shows a stark
language asymmetry — **Python 93.21 %**, C++ 74.02 %, C 69.29 %, **Go 66.93 %**
on the v3 variant matrix. Same algorithm, same model, ~26-point delta across
languages. The Tonelli-Shanks Go failure made the mechanism concrete: the
model knew the math (Python ✓, C ✓, C++ ✓) but kept thrashing on Go's typed-int
edges around `(*big.Int).Lsh(n, uint(j))` for negative `j`. Five debug cycles,
239K tokens, max_iter exhaustion — the algorithmic knowledge transferred
across three languages; the *language-specific* edge case did not.

**Hypothesis on the cause:** Qwen's training mix is Python-heavy. Algorithmic
content (LeetCode, competitive-programming archives, library implementations,
StackOverflow Q&A) is overwhelmingly in Python. For hard / niche algorithms
(NTT, Tonelli-Shanks, Karatsuba on byte arrays), the Go / Rust / Zig training
density is *much* thinner — the model has fewer examples of the algorithm
expressed in those languages, so it has to compose from first principles
while *also* navigating language-specific gotchas (Go's `uint` cast wrap, C's
manual lifetimes, Zig's explicit error unions). That double cognitive load
is where it cracks.

**Strategy for hard-tier cross-language work — both at eval time and in
real coding sessions:**

1. **Solve in Python first.** Get a working, tested reference. The model
   is fluent in Python — it'll converge fast, with high accuracy.
2. **Then port.** Hand the model the working Python and ask for a port to
   the target language (Go / Rust / Zig / etc.). Porting is a *narrower*
   cognitive task — the algorithm structure is fixed, only the syntax and
   language-idiom translation remain. The model's port-from-Python success
   rate is plausibly higher than its from-scratch success rate in the target
   language, because it isolates the failure mode (typed-int handling,
   memory management) from the algorithmic part.
3. **Reserve for advanced / extreme circumstances.** This isn't worth the
   round-trip overhead on easy tasks — Qwen handles `is_power_of_two` in
   any language without help. Apply when (a) the task is hard-tier *and*
   (b) the target language is non-Python *and* (c) the model has already
   failed once or shows signs of language-specific thrashing.

**How we could test the hypothesis empirically (eval framework changes):**

- Add a `port-from-reference` variant flavor: same task spec, but the
  agent's prompt includes the Python reference impl as context, and the
  ask is "port this to Go" (or Rust / Zig). Compare pass rate vs. the
  current "write from scratch" Go variant on the same base task.
- If the port-from-reference rate is materially higher (say ≥10 points)
  on hard tasks, the strategy is empirically validated. Could become a
  documented best practice in `AGENTS.md` and a candidate skill
  (`python-first-then-port`).

**Why this isn't a substitute for testing the model directly.** The eval
suite measures unaided capability — what the model can do without us
front-loading the algorithm. Both views matter: unaided capability tells
us the *honest* gap; the port-from-reference view tells us how to
*operate around* that gap in production.

**Action items:**
- [ ] Document the strategy in `AGENTS.md` so OpenCode users see it
- [ ] Consider authoring a `python-first-then-port` skill (Tier 3 — not
      universal, but high-value for the niche it covers)
- [ ] Add 1-2 port-from-reference variant pairs to the eval suite to test
      the hypothesis quantitatively (pick from the hard-tier 4 confirmed
      differentials: 31 / 51 / 155 / 158)

### Smaller queued items (work to do *after* the v3.5 sweep, while we assess results)

> **Note:** Originally three items here. The third (`145`/`146` Phase 4
> deferred) was de-prioritized when v3.5 substituted in `47_branchless_min`
> and `137_pollard_rho` as cross-language hard picks at tractable size.
> Items 1 and 2 below remain queued; revisit `145`/`146` only if v3.5
> data shows the substitutes don't differentiate.

1. **Author a `python-first-then-port` skill (Tier 3, situational).**
   Encodes the inference-time strategy from the section above into a
   reusable skill. Operational value the moment it lands — OpenCode users
   can invoke it on hard cross-language tasks without us re-writing the
   strategy each session. Estimated effort: ~30 min. Description focused
   on the activation criteria (hard-tier task × non-Python target × prior
   thrashing or expected language-specific gotchas).

2. **Add 1-2 port-from-reference variant pairs to the eval suite.**
   Wires the Python-first hypothesis into the eval framework so the *next*
   sweep can quantitatively test it. Pick 1-2 of the 4 confirmed
   differentials from the v3 smoke test: `31_is_power_of_two`,
   `51_toposort`, `155_tonelli_shanks`, `158_karatsuba_bytes`. Each pair
   adds two new variant entries to a base task: a "port from this Python
   reference" Go variant and a "port from this Python reference" Rust
   variant. The agent's prompt includes the working Python impl as context
   instead of a from-scratch ask. Compare pass rate vs. the existing
   from-scratch Go/Rust variants. Estimated effort: ~1 hour for 1 pair,
   ~2 hours for both.

3. **Phase 4 deferred — pick up 145 (segment tree lazy) or 146 (Aho-Corasick).**
   Both are heavyweight algorithmic ports (~150–200 lines per language) but
   they already have Python reference impls in `/tmp/refs/`. The 6-language
   pattern from Phase 4.5 transfers cleanly. 145 is more naturally
   stdio-friendly; 146 is heavier but exercises suffix-link automaton
   discipline that's idiom-different across languages. Estimated effort:
   ~3-4 hours per task for full 6-language rollout including reference
   impls + JSON variants + audit verification.

These three are intentionally pickable in any order, and any one of them can
be a single focused session. None of them block the sweep; the sweep
doesn't block any of them.

### ~~Complete Rust + Zig variant rollout~~ ✓ DONE (2026-05-06)

**Shipped.** All 13 variant base tasks now cover all 6 languages
(python/go/c/cpp/rust/zig). 77 variant entries total, all verified end-to-end
via `python3 tests/audit_variants.py`. See "Completed" section below for the
landing entry.

Toolchain prerequisites added to `docs/INSTALL.md`:
- Rust 1.95.0 (system pacman install)
- Zig 0.16.0 (mise-installed; new Zig 0.16 I/O API uses
  `pub fn main(init: std.process.Init)` with `std.Io.File.stdin()/stdout()`
  and `takeDelimiter('\n')` for line reads)

Effective test units rose from **197 → 223** (+26 = +13 Rust + +13 Zig).

**Trade-off worth noting:** Zig builds take ~8s per task (cold compile).
For the audit pass that's 13 × 8 = ~100s overhead; for the full overnight
sweep, Zig variants will add ~13 × 8 × 5 models = ~9 minutes of pure
compile time across the run. Acceptable; documented for awareness.

### Skills don't fire spontaneously — Phase 5 (auto-routing) priority increase

Smoke test signal: **0 of 197 task agents called `list_skills` /
`load_skill` / `start_skill_agent`**. Despite AGENTS.md being at the
project root, despite the skills system being fully wired, despite the
system-prompt-tools.md mentioning skills as a tool group — the model
doesn't invoke skills proactively on eval-style tasks.

Why this is OK in the short term: eval task descriptions are too directive
("Create /tmp/eval_X/file.py with…") for a skill description to feel
relevant. We saw `list_skills` fire ONCE in OpenCode interactive use
(per `opencode stats`), which is the more realistic case.

Why this matters longer term: the skills are valuable specifically when
the model's task is open-ended ("review this code", "audit this for bugs",
"write a new eval task"). For those, AGENTS.md task→skill mapping is the
nudge; we want to confirm it's working in practice over the next few
weeks of normal use.

**Action:** if real OpenCode usage over the next ~2 weeks shows skill-fire
rate stays near-zero, escalate Phase 5 (auto-routing layer from
`docs/SKILLS_PLAN.md`). The pre-flight classifier becomes worth the
engineering cost. If skills do fire on real conversational work, hold.

### Selectively pull skills from browse.sh

Pull curated skills from <https://browse.sh/> into our local `skills/`
catalog to expand what the model can invoke without writing every skill
from scratch.

**Why "selectively" is non-negotiable.** A skill is authoritative
context — `start_skill_agent` loads the SKILL.md body straight into the
sub-agent's system prompt. A poisoned skill is a prompt-injection /
context-poisoning vector that the runner has no defense against: a
malicious skill could exfiltrate files via `bash`/`fetch`, plant
backdoors via `edit_file`, or silently bias every downstream task. One
bad skill ruins the whole catalog because once skills auto-route
(Phase 5 in `docs/SKILLS_PLAN.md`), the model picks which to load —
not us.

**Selection gate (do not skip any step):**
1. **Per-skill review, not bulk import.** Each candidate gets read end
   to end by Max before it lands in `skills/`. No directory sync, no
   "pull everything matching tag X."
2. **Pin a content hash.** Record the SHA-256 of the SKILL.md body at
   import time in a `skills/REMOTE_PROVENANCE.md` ledger (source URL +
   hash + import date + Max's initials). Upstream edits don't silently
   reach us.
3. **Sandbox-read first.** Before importing, load the skill into a
   throwaway session and probe: does it instruct the model to run
   `bash` against unfamiliar paths? Touch `~/.ssh`, `~/.config`,
   git remotes, credential files? Fetch arbitrary URLs? Any of those
   → reject or rewrite.
4. **Strip + rewrite, don't mirror.** Even "good" skills usually carry
   author-specific assumptions (paths, tool names, project structure).
   Adapt them to our MCP tool surface (`edit_file`, `web_search`,
   `start_agent`, etc.) and re-attribute in frontmatter.
5. **Re-review on update.** If we ever refresh an imported skill,
   treat it as a fresh import — diff the SKILL.md, re-run the sandbox
   probe, update the hash in the ledger.

**Out of scope (explicitly):** any automated "pull latest" job, any
MCP tool that fetches skills at runtime from a remote source, any
trust-on-first-use scheme. The whole catalog's integrity depends on
the human-in-the-loop gate.

**First candidates to evaluate** (none imported yet — placeholder for
the first review session): start with skills that augment categories
we know are weak from `evals/scoring.py --by-language` (Zig idioms,
Rust borrow patterns) and skills that complement Tier 1/2 in
`skills/README.md` without overlap.

### Expand multi-language variant coverage (driven by Tonelli-Shanks finding)

**The Tonelli-Shanks Go failure during the 2026-05-06 smoke test was the
first piece of evidence that variant testing surfaces information the
Python-only suite cannot.** That alone reshapes the priority of variant
expansion. Documenting here so we don't lose the thread.

**What happened.** Task `155_tonelli_shanks` is a hard-tier number-theory
task (modular square root). The 35B-A3B Uncensored solved it cleanly in
Python ✅, C ✅, and C++ ✅. The Go variant **failed at max_iter (25)** after
**239K tokens** and 5 visible debugging cycles. The model knew the
algorithm — it had just written it correctly in three other languages.

**Root cause from the agent log:** the model kept tripping on Go's strict
typed-int arithmetic around the bit-shift step in the Tonelli-Shanks
inner loop. Specifically `j = M - i - 1` going negative and underflowing
when used with `(*big.Int).Lsh(n, uint(j))` — Go's `uint` cast on a
negative number wraps to a huge value, where Python's `<<` would raise
ValueError or just compute. The five recorded fix attempts all
re-encountered the same edge case from different angles.

**Why this is important.** This is exactly the kind of cross-language gap
the variant system was built to surface, and which a Python-only test
literally could not. The algorithmic knowledge transfers; the
language-specific handling of edge cases (typed int overflow, big-int API
ergonomics, memory management discipline) does not. Whether a model can
write correct Go is a separate capability from whether it knows
Tonelli-Shanks.

**Implication for the suite.**

If the smoke test (and tonight's full sweep) produces more findings of
this shape — Python passes, one or two compiled languages fail — the case
for expanding variant coverage gets much stronger. The original Phase 4
plan deferred 5 tasks (53_bloom, 145, 146, 152, 153) as "too heavy this
session." After the Tonelli finding, **at least 145 (segment tree) and
146 (Aho-Corasick) jump in priority** — both are algorithmically dense
in ways that exercise language-specific data-structure idioms (Go's
slice-of-slice aliasing, C's manual lifetime management, C++'s
RAII-vs-raw-pointer choices). They're exactly the right shape to produce
more cross-language differentials.

**Concrete next-step plan, gated on tonight's full sweep results:**

1. **After the 5-model overnight sweep**, audit per-language pass rates
   across all 77 variant entries (post-Phase 4.5). Count how many tasks show
   the "Python ✓ / one-or-more compiled-lang ✗" pattern. With Rust + Zig
   added, expect *new* failure modes (Rust borrow checker, Zig explicit
   error unions) that the original 4-language matrix could not surface.
2. **If ≥3 tasks show that pattern**, escalate variant expansion. Phase 4
   deferred items move up the priority list. Consider adding variants to
   currently single-variant tasks where they'd be most informative
   (likely candidates: `27_brainfuck_interpreter`, `54_astar`,
   `45_kv_cache`, `108_hmac_verify`, `137_pollard_rho`).
3. **If only 1-2 tasks show the pattern** (Tonelli-Shanks alone +
   maybe one more), variants are still valuable but the marginal value of
   expanding from 13 → 20 base tasks is lower. Prioritize other axes
   (skills routing, agentic tasks, speculative decoding).
4. **Also useful:** look at WHICH language fails most often. If Go is
   consistently the weak point, that's a different signal than "C is the
   weak point." Could inform skill creation (e.g., a `go-bigint` skill
   encoding the typed-int gotchas).

**Don't forget:** add this story to `docs/RESULTS.md` v3 sweep section
once the post-mortem is written. The Tonelli-Shanks Go anecdote is the
single most concrete piece of evidence for why we did Phase 4.

### Token usage aggregator (`scripts/token-stats.py`)

Production-mode telemetry — pull from existing sources, no new service, no
runtime overhead. Built once, run on demand.

**The pitch.** Tokens are tracked everywhere already, but fragmented across
three stores:

| Source | What it has |
|---|---|
| `opencode stats` (sqlite under `~/.local/share/opencode/`) | Per-session input/output/cache tokens, tool usage |
| `agents/logs/agent-*.jsonl` | Per-task tokens for autonomous agents + MCP-spawned sub-agents (already added) |
| Open WebUI sqlite (Docker volume) | Per-conversation tokens |

A small aggregator reads all three and prints a unified report. **Zero
overhead at runtime** — only runs when you ask for a snapshot. Local model
is free, so this is for awareness/efficiency analysis, not billing.

**Output shape (target):**

```
$ ./scripts/token-stats.py --last-7d

LAST 7 DAYS
================================================================
Source          Sessions    In tokens    Out tokens    Cache rd
opencode             18         1.2M          187K        21M
agents/logs/         34         580K           47K          —
open-webui            5         220K           29K       4.5M
================================================================
TOTAL                57         2.0M          263K       25.5M

Equivalent Sonnet 4.6 spend: ~$13.85
Equivalent Opus 4.7 spend:   ~$45.20
```

**Design notes (work out details when we build it):**
- Read-only against each source — never modifies anything
- Optional flags: `--last-Nd`, `--by-client`, `--by-model`, `--cost-as=sonnet|opus|gpt5`
- Cache reads are billed at ~10% of input rate at frontier APIs — model that into the cost-translation
- Only blind spot: interactive `run.sh` chat (no API path; no usage data emitted). Acceptable — marginal use.
- Optional flag `--watch` for periodic refresh, but not the default

**Why not the proxy idea (`token_proxy.py` on :8090):**
Investigated and rejected. OpenCode's existing telemetry is comprehensive
enough that a proxy would be ~80% redundant, add 1-2 ms to every request,
and require streaming-SSE handling. The aggregator hits the same data with
zero overhead. Revisit only if a real-time use case appears (none yet).

**Implementation:** ~150 lines of Python, one new file
`scripts/token-stats.py`. Estimated 1-2 hours including the cost-translation
table and a couple of useful flags.

**Trigger:** build after the v3 sweep lands and the post-mortem is written.

### Tailscale remote access — ✅ implemented 2026-07-07, awaiting first run
Superseded by `docs/REMOTE_ACCESS_PLAN.md` (full design) and
`scripts/setup-tailscale.sh` (implementation, incl. tailnet-only HTTPS via
`tailscale serve` + the 127.0.0.1 bind-surface shrink via `BIND_HOST`).
Remaining: run `./scripts/setup-tailscale.sh` (needs sudo + browser SSO),
create the WebUI admin account, mirror creds into `openbeast.conf`.
Original sketch below kept for reference.

Set up Tailscale so OpenBeast can be accessed from the work laptop (or
any device) over a private encrypted mesh — no port forwarding or static IP needed.

**Steps:**
1. Install Tailscale on the home machine (Arch): `sudo pacman -S tailscale`
2. Enable and start: `sudo systemctl enable --now tailscaled && sudo tailscale up`
3. Install Tailscale on the work laptop
4. Note the home machine's Tailscale IP (e.g., `100.64.x.x`) from `tailscale status`
5. On the work laptop, create `~/.config/opencode/opencode.json` with the provider
   pointing to `http://<tailscale-ip>:8080/v1`
6. Verify: `curl http://<tailscale-ip>:8080/health` from the work laptop
7. Open WebUI is also accessible at `http://<tailscale-ip>:3000`

**Why Tailscale:** Zero config networking. WireGuard-encrypted, NAT-traversing,
works from any network. Free for personal use (up to 100 devices). No router
config, no dynamic DNS, no exposed ports.

### Speculative decoding — MTP variants scaffolded 2026-05-22, benchmarked 2026-07-08

**Benchmarked 2026-07-08** on the v4 suite at the measured context ceilings
(27B MTP 288K / 35B-A3B MTP 512K / Qwopus MTP 336K) — results in
`docs/RESEARCH_FINDINGS.md` §3 and `evals/leaderboard.json`. The narrative
below is the original scaffolding/tuning history.

**Status:** the separate-draft-model approach (originally planned with a small
0.6B Qwen) is **superseded** by MTP (Multi-Token Prediction). Unsloth shipped
MTP-enabled GGUFs and llama.cpp (HEAD `0f3cb3fc8`, PR #22673 + follow-ups)
now supports `--spec-type draft-mtp` natively. The MTP heads share the base
model's representations — strictly better than bolting on an external draft
model.

**What's landed:**
- `scripts/{serve,run}-qwen-27b-mtp-q5.sh` — Qwen3.6-27B Q5_K_XL MTP build
- `scripts/{serve,run}-qwen-35b-a3b-mtp.sh` — Qwen3.6-35B-A3B Q4_K_M MTP build
- Both registered in `opencode.json`, `tests/test_scripts.sh` expects them
- Weights downloading from `unsloth/Qwen3.6-{27B,35B-A3B}-MTP-GGUF`
- Conservative starting contexts (256K / 384K) pending VRAM measurement
- See `docs/REFERENCE.md` "Qwen3.6 MTP variants" section for the full notes

**Constraints to respect when benchmarking:**
- MTP pins `-np 1` (upstream limitation) — so the MTP variants can only serve
  one request at a time. Eval sweeps need to account for that vs. the 6-slot
  non-MTP runs (no concurrency benefit during the sweep, only per-request
  speedup).
- `--mmproj` not supported with MTP — no vision input.

**Next steps:**
1. ✅ **Done 2026-07-07** — clean launches verified and VRAM measured via
   `scripts/measure-vram.sh` (required rebuilding llama.cpp first: the old
   binary predated MTP support and carried a dead RUNPATH from the repo's
   pre-OpenBeast location). Contexts raised to measured ceilings: 27B MTP
   256K→320K, 35B-A3B MTP 384K→512K, Qwopus v2 350K→416K, Qwopus v2 MTP
   256K→336K. Numbers in README "## Models" and REFERENCE "MTP variants".
2. ✅ **Done 2026-07-07** — spec parameters tuned *empirically* per model
   (superseding the originally requested n-max 4 / p-min 0.75, which measured
   slower on every model): 27B MTP `n8/p0.0` (184 tok/s, 2.75×), 35B-A3B MTP
   `n4/p0.0` (379 tok/s, 1.46×), Qwopus MTP `n4/p0.0` (147 tok/s, 2.14×).
   p-min gating never won — drafts are verified by the target model, so
   p-min affects speed only, and unconditional drafting is faster. The 27B's
   n8 draft buffers cost ~600 MiB, so its context backed off 320K→288K.
   Full tuning table in REFERENCE.md "MTP variants".
3. ✅ **Done 2026-07-07** — all four rows added to `evals/benchmark_all.py`
   AND smoke-validated end-to-end on the 13-task stratified subset (results
   quarantined in `evals/results/smoke/`, leaderboard untouched via the new
   `--no-leaderboard` flag):
   - qwen-27b-mtp-q5: 13/13 · qwen-35b-a3b-mtp: 13/13 (100.0 acc)
   - qwopus-27b-v2-mtp-q5: 13/13 · qwopus-27b-v2-q5: 12/13 after infra-blip
     rerun of 137 (the honest FAIL: `65_miller_rabin_e` — 20 min of CoT
     without ever writing mr.rs; the MTP sibling passed the same task, its
     ~2× token speed letting the long reasoning finish inside the timeout)
   - Notable: on sequential eval work, single-slot MTP beat 6-slot non-MTP
     on wall-clock (Qwopus MTP 1502s vs non-MTP 1679s, same 13 tasks) —
     the sweep runs tasks sequentially, so `-np 1` costs nothing there.

   Original step preserved below:
   Add `qwen-27b-mtp-q5`, `qwen-35b-a3b-mtp`, and `qwopus-27b-v2-mtp-q5`
   to `evals/benchmark_all.py` as additional models in the next sweep
   (plus the non-MTP `qwopus-27b-v2-q5` as a regular row alongside our other
   27B variants).
   - **Sweep cannot parallelize MTP runs.** MTP forces `-np 1`, so the
     per-task concurrency benefit our non-MTP runs get from 6 unified slots
     is *gone* for all MTP models. Eval throughput on the MTP rows will
     be bounded by single-request speed × task count. Plan wall-clock
     accordingly — expect each MTP model to take noticeably longer than
     its non-MTP sibling on the same task count, even with the MTP
     per-token speedup, because tasks can no longer overlap.
   - This is purely a sweep-orchestration note; the MTP per-request
     speedup is what we're actually measuring.
   - **Qwopus-specific check first:** before sweeping, verify outputs are
     coherent past 128K. Jackrong's README cites 32K/128K native context
     and the YaRN extension we rely on in the unsloth Qwen3.6 GGUFs may
     or may not be intact in their conversion. If long-context outputs
     degrade, drop the Qwopus serve scripts' contexts before benchmarking.
4. Compare apples-to-apples against the non-MTP siblings on the v3.5 suite:
   accuracy should be identical, speed-per-request should be 1.5–2× per
   unsloth's claim. If speed gain is smaller after the step-2 tuning,
   revisit `--spec-draft-n-max` and `--spec-draft-p-min` together (tighter
   p-min trades draft acceptance rate for quality; deeper n-max amplifies
   both wins and losses).
5. If results look good, consider promoting the MTP MoE to the auto-launch
   default (currently Qwen 27B Uncensored Q5_K_P). Tradeoff: lose 6-slot
   parallelism for single-request speed.

### Phase 4 follow-up — variant the 5 deferred tasks
Phase 4 shipped 13 of 18 originally-planned tasks with multi-language variants
(see docs/archive/WORK_PLAN.md "Phase 4 deferred items" for full breakdown). Five
tasks remain: 53_bloom (probabilistic test), 145_segment_tree_lazy, 146_aho_corasick,
152_chase_lev_deque, 153_coroutine_scheduler. Each has a specific reason it
was held back. Pick up in a focused follow-up session — the audit pattern from
the 13 completed tasks transfers cleanly.

### Eval harness — agentic + tool-selection tasks
Next axis after variants: agentic tasks (require `web_search` / `start_agent`)
and tool-selection tasks (does the model prefer `edit_file` over `write_file`?
Does it know when to spawn a subagent?). These don't fit the current
deterministic-validation pattern and need a separate harness path.

## Future Horizon

### Production roadmap → community pillar (2026-07-07)
Full review + prioritized plan in [PRODUCTION_ROADMAP.md](archive/PRODUCTION_ROADMAP.md).
Highest impact-to-effort, in order: **(1) add LICENSE** (absent — legal
blocker; Max picks MIT/Apache-2.0), **(2) `bootstrap.sh` one-command install**,
**(3) "Tier 0: just chat" minimal path**, (4) README hook + "vs Ollama/LM
Studio" value prop, (5) reconcile contradictory eval numbers, (6) `.github/`
CI + templates, (7) publish repo + v1.0 tag. Fixed this session: WEBUI_AUTH
fresh-install regression (now defaults false, tailscale flips true), stale
`/home/max/Documents/models` rename paths, README CUDA-PATH/arch trap.
Also: **process-supervision gap** — llama-server + mcpo run bare; add systemd
units so they restart on crash/boot like the docker services do.

### RESEARCH EXPERIMENT — does preemptive skill-chaining break the ~0% barrier? (2026-07-07)
**Hypothesis (Max):** skills fire ~0% on small local models because the
model must *blindly* discover them (`list_skills` → `load_skill` chain it
never initiates). If we PREEMPTIVELY inject the chain — i.e. surface a skill
index up front and/or auto-invoke `list_skills` at the start of an
open-ended turn — small models may finally route to skills. This matters
because OpenBeast must serve BOTH small and large models: large models chain
proactively on their own; small ones need the scaffold.

**Experiment to run:**
1. Baseline: current skill-fire rate across model sizes on open-ended
   (non-directive) tasks — instrument the agent runner to log
   list_skills/load_skill/start_skill_agent calls per task. Confirm the ~0%.
2. Arm A — **index injection**: put a compact skill menu (name + trigger)
   in system-prompt-tools.md; measure fire rate + correct-skill-selection.
3. Arm B — **preemptive list_skills**: runner auto-calls list_skills on
   turn 1 of open-ended tasks and feeds the result back; measure whether the
   model then loads the right skill.
4. Arm C — both. Compare across a small (Gemma/27B) and large model on a
   held-out set of open-ended tasks (review/audit/debug/design — the shapes
   where skills add value).
**Metric:** skill-fire rate AND skill-selection accuracy AND end-task
quality delta (does firing the skill actually improve the output?). A skill
that fires but doesn't help is noise.
**Payoff:** if index-injection alone lifts small-model fire rate materially,
it becomes the default and validates the SKILLS_PLAN Phase-5 auto-router
direction. Pairs with the surface-simplification work below.

### Skills ↔ tools surface simplification (2026-07-07)
Max flagged confusion; analysis confirms it's structural — 9 of 17 tools (now 7 of 15 post-collapse) are
meta-machinery (5 agent + 4 skills), and skills fire ~0% on local models due
to blind `list_skills`→`load_skill` indirection. Plan (PRODUCTION_ROADMAP §B):
inject a compact skill *index* into system-prompt-tools.md (1 `load_skill`
tool + visible menu, not 4 blind-discovery tools); use the RBAC connections
to expose a lean core tool profile by default + advanced behind a second
connection; prune/merge overlapping skills (14→leaner); collapse the 5
agent-mgmt tools to fewer verbs; sharpen the tool-vs-skill framing in the
prompt. Endgame = the deferred SKILLS_PLAN Phase-5 auto-router.

### RBAC — multi-user tool lockdown (Phase 0+1 DONE 2026-07-07)
Full design + UX in [RBAC_PLAN.md](RBAC_PLAN.md). **Live and verified against
the real Open WebUI access-control code**: `user`-role (guest/family) = only
`web_search`; `admin` = all 17. Two connections on one MCPO (id1 privileged
`!web_search` admin-only, id2 `web_search` public), baked into
`configure-webui.sh`. Assigning a tier = the WebUI role dropdown, nothing
else. **Remaining — Phase 2 hardening:** below-app enforcement (per-profile
MCPO `--api-key`s + Sandlock sandbox so a bypassed UI or a second frontend
can't reach the dangerous tools), then guest `fetch` once SSRF/scheme-
filtered, then per-tool-call audit log. Converges with Arsenal Phase 1a.

### Arsenal — KICK OFF Phase 1 (recon done, both GO — 2026-07-07)

**Sandlock SHIPPED opt-in (2026-07-08):** security review passed (pinned
v0.8.4), installed to ~/.local/bin, validated (FS/net deny, exit codes,
killpg compat, ~1.2ms overhead); `scripts/setup-sandlock.sh` +
`scripts/sandlock-profile-openbeast.toml` + `docs/SANDBOXING.md`; conf key
BASH_WRAPPER forwards to the bash tool. **Next: run the eval suite with
OPENBEAST_BASH_WRAPPER set (profile `openbeast`); if green, flip to
default-on in conf.sh. Until then: opt-in only.** Re-verify the
setpgid/killpg interaction on every sandlock version bump (child leaves the
wrapper's group; teardown still catches it today — see docs/SANDBOXING.md).

Recon prototypes built and verified on this box (see
[TOOL_ARSENAL_RESEARCH.md](archive/TOOL_ARSENAL_RESEARCH.md) "Prototype recon"):
**Sandlock** GO (builds, 8/8 isolation tests held on Landlock ABI 8) and
**ChunkHound** GO (working local index, CPU embeddings, zero VRAM). Phase 1
implementation, ~1 week:
1. **File-tool path allowlist** in `agents/tools.py` (realpath under
   workdir/`/tmp/eval_*`, never `~/.ssh`/`~/.config`/credentials) — the
   real credential-exfil fix, Sandlock-independent, do FIRST. Also the
   in-process gap Sandlock can't cover.
2. **Sandlock wrap of `bash()`** (argv via `sandlock run … -- /bin/bash
   -c`), composed under `run_reaped` (Landlock/seccomp = what; RLIMIT_AS +
   killpg = how much/how long). Pin commit `5c9bafe9`, vendor source, keep
   MITM/OCI features off.
3. **guest/admin Sandlock TOML profiles** — the RBAC Phase 2 enforcement layer.
4. **ChunkHound**: CPU llama.cpp embedding sidecar on :8081 (nomic-embed,
   ≥2048 ctx) wired into start.sh/stop.sh; `.chunkhound.json` with local
   base_url (never leak to api.openai.com); reindex script; opencode.json
   stdio entry; tight-output tool guidance (pin page_size + max_response_tokens).
   OPS RULE: never `pkill -f llama-server` (kills prod) — target by PID.

### Arsenal expansion — original research verdicts (2026-07-07)
Deep-research verdicts in [TOOL_ARSENAL_RESEARCH.md](archive/TOOL_ARSENAL_RESEARCH.md)
(9 findings, 25 claims 3-vote-verified). Headlines: ADOPT ChunkHound
(semantic code search, MIT, llama.cpp embeddings) + Sandlock (unprivileged
Landlock/seccomp sandboxing — composes with our rlimit layer); wrap
Playwright CLI as a *skill* (the 68-tool Playwright MCP server is
token-hostile to Qwen-class models); BUILD our own memory tool
(sqlite-vec/LanceDB + llama.cpp embeddings — Mem0/Letta default to cloud
and assume frontier models) and canned-query SQLite tool. Phase 1 ≈ 1 week.

### Multi-model routing
Run two models simultaneously on different ports (e.g., 35B-A3B on :8080 for fast
agent work, 27B Q5 on :8081 for deep reasoning). Build a lightweight router that
picks the right model based on task type or explicit preference.

### RAG pipeline for local codebases
Embed local codebases into a vector store and give agents semantic search beyond
grep. Use llama.cpp's embedding endpoint with ChromaDB/LanceDB for local vector
storage. New MCP tool: `semantic_search(query, codebase_path)`.

---

## Completed

- [x] Debug Open WebUI MCP connection — MCPO proxy, native function calling
- [x] Verify OpenCode MCP stdio transport
- [x] Test agent.sh end-to-end (3 iterations)
- [x] Validate 35B-A3B KV cache (~6.3 KB/token measured)
- [x] Open WebUI persistence confirmed
- [x] Git init + version control
- [x] Long-running agent management via MCP (start/check/tail/list/stop)
- [x] Claude Code-caliber tool suite (edit_file, fetch, web_search)
- [x] 6-slot parallel serving with unified KV cache
- [x] Script refactor (scripts/ directory, 3 root entry points)
- [x] Test suite (79 tests — structure + tools + MCP)
- [x] Fixed grep repr() quoting bug (shlex.quote)
- [x] Context-aware agent spawning with context briefing
- [x] Local web search via SearXNG
- [x] Agent log tailing (tail_agent)
- [x] Model-aware context budgeting (~85K per slot)
- [x] Agent resumption from JSONL logs
- [x] System prompt split (soul file + tools addendum)
- [x] OpenCode global config for models from any directory
- [x] Eval harness — 10 tasks, 10/10 pass rate
- [x] Smoke test (end-to-end stack validation)
- [x] Health monitor with auto-restart
- [x] Default model documented (27B Uncensored Q5_K_P)
- [x] Gemma 4 31B-it integrated as 5th model (220K context, validated)
- [x] Context lengths re-tuned to measured VRAM ceilings (Qwen + Gemma)
- [x] SearXNG fixed: granian port collision + JSON format gate
- [x] Eval harness expanded: 10 → 30 → 50 → 70 → 128 → 133 → 144 tasks (40 easy / 53 medium / 51 hard)
- [x] Model-tagged eval results with GPU snapshot via nvidia-smi
- [x] Multi-model benchmark runner (`evals/benchmark_all.py`)
- [x] Accuracy-primary leaderboard with composite (0.75×accuracy + 0.25×speed) shown
- [x] 12-category taxonomy with subcategory drilldown (`scoring.py --by-category`)
- [x] All 144 tasks verified end-to-end against canonical solutions
- [x] 4 task bugs fixed (23 SQLi tripwire, 27 BF typo, 33 daily-compounding ref, 36 BS ref)
- [x] Qwen 35B-A3B Uncensored Q4_K_M added as 5th active model
- [x] Removed redundant Qwen 27B Q4_K_M (Q5 variant supersedes)
- [x] VRAM measurements re-calibrated (35B-A3B: 23.1 → 27.8 GB at 512K)
- [x] Multi-host leaderboard schema (host_id keying, `--compare-hosts`, `--host` filter)
- [x] Full 144-task × 5-model sweep on RTX 5090 (7h 21m, all 5 succeeded — see RESULTS.md)
- [x] Post-sweep post-mortem: identified 4 spec/harness defects (42 numpy lint, 85 type ambiguity, 121 return contract, 17 fixture corruption)
- [x] Phase 1 — 4 spec/harness fixes landed; `pre_validate` field added to harness for opt-in fixture re-assertion
- [x] Phase 2 — 15 hardening tasks added (145–159) across the three saturated categories; suite is now 40 easy / 53 medium / 66 hard = 159 total
- [x] Cheat-resistance perf gates added to 150 + 152 (catches list.pop(0) impls)
- [x] Phase 3 — multi-language variant architecture in `run_eval.py` + `scoring.py` + result schema; backward-compat regression bit-identical
- [x] Token tracking through runner → eval → scoring → leaderboard (separate column, not part of rank)
- [x] `evals/README.md` with full distribution table, schema, scoring, and pitfall-lessons-learned section
- [x] Phase 4 (initial) — 51 variant entries across 13 tasks (Python / Go / C / C++); reference impls verified end-to-end. 5 base tasks deferred (see "Up Next"). [Superseded by Phase 4.5: Rust + Zig added across all 13 → 77 entries — see entry below.]
- [x] Default model swap to Qwen 35B-A3B Uncensored Q4_K_M (top of leaderboard); start.sh, healthcheck.sh, opencode.json reordering, README/INSTALL/REFERENCE all updated
- [x] Docs reorganized: INSTALL/REFERENCE/RESULTS/WORK_PLAN/TODO moved to `docs/`; README and system-prompt files stay at base
- [x] RESULTS.md eval distribution section (categories × difficulty + subcategory drilldown + variant matrix)
- [x] **Skills system landed** — Phases 1-4 complete. `list_skills` / `load_skill` / `start_skill_agent` / `reload_skills` MCP tools; 6 starter skills (code-review, security-audit, debugging-methodology, deep-counsel, eval-task-author, eval-variant-porter); repo + global discovery with repo-wins-on-collision; `scripts/install-skills.sh` for global symlinks. Phase 5 (auto-routing layer) deferred. See [docs/SKILLS_PLAN.md](SKILLS_PLAN.md).
- [x] **8 more skills authored** (Tier 1 + Tier 2 from frontier-model behaviors) — `codebase-onboarding`, `spec-extraction`, `git-discipline`, `long-context-synthesis` (Tier 1, universal); `test-driven-development`, `architecture-proposal`, `performance-optimization`, `api-design` (Tier 2, situational). Total: 14 skills.
- [x] **AGENTS.md at project root** — auto-loaded by OpenCode; nudges the model toward skills with a task→skill mapping. Pairs with the MCP tools to make skills first-class for any OpenCode session in this repo.
- [x] **Rust + Zig variant rollout complete** (2026-05-06) — all 13 variant base tasks now cover 6 languages (python/go/c/cpp/rust/zig); 77 variant entries audited end-to-end with reference impls. Zig 0.16.0 installed via mise; Rust 1.95.0 from pacman. Effective test units: 197 → 223. The cross-language matrix can now surface failure modes specific to ownership/borrow-checking (Rust) and explicit error unions / compile-time checks (Zig). Audit script persisted at `tests/audit_variants.py`.
