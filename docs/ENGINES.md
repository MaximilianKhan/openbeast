# Engines — choose, swap, update and roll back the inference engine

OpenBeast talks OpenAI-compatible HTTP to one inference server. That server, the
**inference plane**, can be one of three engines, plus a fourth that is opt-in
and gated (Project Maya: admitted only after it measures 1.5x llama.cpp on the
rig, [WEIGHT_STREAMING.md](WEIGHT_STREAMING.md)). Each has defaults per host
class, and you can override them. `scripts/engine.sh` is the one command for
all of it:

```bash
scripts/engine.sh list                         # what this host can run, what is installed / pinned / active
scripts/engine.sh status                       # the active engine: version, pin, endpoint readiness, model served
scripts/engine.sh use vllm --profile NAME      # switch (stops a running stack, rewrites the conf, starts it)
scripts/engine.sh use llama                    # back to the default rig, byte for byte
scripts/engine.sh use tensorfold --unmanaged --url http://127.0.0.1:8000 --profile NAME   # a server someone else runs (a Spark pair's rank 0)
scripts/engine.sh update tensorfold            # latest release: download, verify, pin
scripts/engine.sh install vllm --version v0.32.0 [--digest sha256:…]
scripts/engine.sh rollback vllm                # the previous pin (offline when it is still on disk)
scripts/engine.sh stage llama --version v1.0.0 ./carry   # connected box: fetch + verify for a closed one
scripts/engine.sh install llama --from ./carry           # the closed box
scripts/engine.sh history                      # every install / update / rollback / use
```

`--dry-run` works on `use`, `install`, `update` and `rollback`. With `use` it
prints the conf changes and the exact launch command.

## The matrix

| Engine | `rig` (x86_64 + RTX 5090, CUDA sm_120) | `spark` (DGX Spark GB10, aarch64, sm_121, unified memory) | Weights it serves | Managed here? |
|---|---|---|---|---|
| **llama** (llama.cpp `llama-server`) | **default** — every `scripts/serve-*.sh`, measured on the 5090 | builds (CUDA arm64), not the Spark default | **GGUF** only | yes, through `scripts/serve-*.sh` (unchanged) |
| **vllm** (`vllm/vllm-openai`, container) | yes — one GPU, TP 1, managed | yes — TP 1 managed, or TP 1..8 across Sparks (unmanaged, `spark-node.sh`) | safetensors: BF16 / FP8 / **NVFP4** / AWQ / GPTQ. GGUF only through the out-of-tree `vllm-gguf-plugin` ("highly experimental") | TP 1 on this box: yes (`engine-serve.sh`). Otherwise unmanaged |
| **maya** (Project Maya, built from a reviewed commit; opt-in, **gated**) | opt-in only after `scripts/engine-gate.sh` measured **≥ 1.5×** llama.cpp's decode tok/s on the same weights ([WEIGHT_STREAMING.md](WEIGHT_STREAMING.md)) | no | GGUF (GLM-5.3-Flash through `--gguf-dir`, Maya packs), EXL3 | TP 1 on this box: yes, once the gate passes |
| **tensorfold** (native ≥ 1.0 release binary) | yes — sm_120 kernels ship. Single-GPU fits such as Qwen3.8-27B MLX-4bit are plausible, **not qualified on a 5090** | **default** — TP 1 managed, or TP 1..8 across Sparks (unmanaged, `spark-node.sh`) | **MLX-affine 4/6/8-bit** safetensors, NVFP4 (ModelOpt), EXL3 (GLM-5.3-Flash). **No GGUF on CUDA** | TP 1 on this box: yes. Otherwise unmanaged |

**Host class** comes from `scripts/lib/hardware.sh`. A GPU that reports
`memory.total` as `[N/A]` (`OB_GPU_UNIFIED=1`) makes this host a `spark`; macOS
is `mac` (llama or tensorfold `macos-arm64`); everything else is a `rig`.
`OB_ENGINE_HOST_CLASS` overrides the detection (`engine.sh`, `engine-serve.sh`
and `engine-gate.sh` also set it themselves after detecting once, so one
command runs `nvidia-smi` once, not once per question), and `ENGINE_DEFAULT` in
`openbeast.conf` overrides the default engine. The runtime fallback in
`conf.sh` stays `llama` when `INFERENCE_BACKEND` is unset. The host-class
default only decides what `engine.sh use default` picks and what `list` and
`doctor` call the default, so an unset conf never changes what a box launches.

### Weights format is the coupling

The engine decides which model files you can serve, so swapping engines nearly
always means a different checkpoint:

- **OpenBeast's default models are GGUF** (`weights.registry`; the default is
  the uncensored Qwen3.8-27B MTP Q5_K_M). Only llama.cpp serves them
  natively.
- **vLLM** wants safetensors. Use the NVFP4 build of a model on Blackwell:
  W4A4 NVFP4 has been the default on SM120/121 since v0.30.0 (#55170). Serving
  our own GGUF through `vllm-gguf-plugin` would be the only same-weights A/B
  between llama.cpp and vLLM, and the docs call that path experimental and
  under-optimized.
- **TensorFold** wants MLX-affine safetensors (Vontra conversions), NVFP4, or
  EXL3. It has no GGUF serving on CUDA (its CHANGELOG says "GGUF serving remains
  unavailable" for Qwen3.8-27B).
- On vllm and tensorfold the model comes **only** from a profile,
  `scripts/backends/models/<name>.env` (DGX_SPARK_PLAN §14), fetched and
  verified with `scripts/backends/model-fetch.sh`. A managed engine serves only
  a verified local copy. It never downloads from the Hub at start.
- Every OpenBeast model is uncensored (DGX_SPARK_PLAN §15), and that rule
  carries over to whatever checkpoint a profile names.

## Managed and unmanaged

| Mode | When | What the stack does |
|---|---|---|
| **managed** | `INFERENCE_MANAGED=true`, `INFERENCE_URL` on **this** box | `start.sh` launches the engine, waits for readiness, supervises it (3 relaunches, budget refilled after 5 min), and its trap stops it. `stop.sh` stops it. llama goes through `scripts/serve-*.sh` as always. vllm / tensorfold go through `scripts/engine-serve.sh` (TP 1 profiles only) |
| **unmanaged** | `INFERENCE_MANAGED=false`, the default for vllm / tensorfold, and for any URL on another machine. `engine.sh use <engine> --unmanaged --url URL` writes it for a loopback URL too (rank 0 of a Spark pair on the head), which would otherwise read as managed | Exactly as before (DGX_SPARK_PLAN §4): `start.sh` waits, never launches, kills or rolls back. `healthcheck` reports. The Sparks are run with `scripts/backends/spark-cluster.sh` / `*/spark-node.sh` |

`conf.sh` honours `INFERENCE_MANAGED=true` for a non-llama engine only when the
URL names this box **and** the engine's adapter has a launcher. Anything else
warns and is forced to false, which keeps today's rule for the Sparks.

What a managed vllm / tensorfold run gets, and does not get:

- **Readiness** is per engine (`lib/backend.sh`): vLLM returns 200 with an empty
  body, TensorFold returns 200 `{"ok": true}`. "Ready" also means "ours": a
  container is not a child of `start.sh`, so its adapter answers that question
  (the launcher is alive and the container with this checkout's name is
  running).
- **Container name** is `openbeast-engine-<engine>-<8 hex of the repo path>`,
  one per checkout. Two worktrees on one box never stop each other's engine,
  and a leftover from a SIGKILLed launcher is removed at the next launch.
- **The API key**: `LLAMA_API_KEY` reaches vLLM as `-e VLLM_API_KEY` (by name),
  TensorFold as a 0600 `--api-key-file` under `.run/` and Maya as an exported
  `MAYA_API_KEY`. It never goes on argv.
- **No keyless network bind.** The engine binds `INFERENCE_URL`'s host, and
  `0.0.0.0`, `::` and `BIND_HOST` all count as "local" (so they stay managed).
  With no `LLAMA_API_KEY` on a non-loopback host, `engine-serve.sh` refuses
  with exit 3, exactly as `serve.sh` does for llama-server, unless
  `ALLOW_OPEN_INFERENCE=true` acknowledges it; `engine.sh use` warns first.
- **Not applicable**: FAST_BOOT (the bridge is a GGUF) and MODEL_ROLLBACK
  (last-good records serve scripts). For an engine, rollback is
  `engine.sh rollback`.
- **healthcheck `--restart`** reports a down managed engine and restarts
  nothing. The supervisor relaunches an engine that exits. One that is up but
  not ready needs `./stop.sh && ./start.sh -d`.
- **vLLM CUDA graphs**: `ENGINE_VLLM_EAGER=auto` adds `--enforce-eager` on a
  GB10, but only for the sparse-MLA families (`glm5*`, `deepseek_v4*`,
  `deepseek_v32*`). Those families hit the CUDA-graph NaN of vllm#57156 (open,
  2026-11-15: capture batches poison the null KV block on SM120/121). Every
  other model keeps CUDA graphs. Set `true` or `false` to force it either way.

## Pins, verification, history

| Engine | Pin (what can never move) | Integrity check | Rollback |
|---|---|---|---|
| llama | `scripts/llama.cpp.ref` `LLAMA_CPP_REF=<40-hex commit>` (tracked: what a fresh `bootstrap.sh` builds; review and commit the change) | The commit id *is* the content hash. A tag (`v1.0.0`, `b12422`) is resolved to its commit, and a local tag is never moved | `git checkout` of the old commit and rebuild. The objects are already local, so this works offline |
| vllm | `ENGINE_PIN_VLLM=<tag>@sha256:<index digest>` after a pull; `<tag>@sha256:<image id>` after an offline install | The pull resolves the digest. `--digest` must match it, or the install is refused. Offline: every MANIFEST field must have its shape, the tarball's sha256 is checked against the MANIFEST, then the image id after `docker load`. The MANIFEST travels with the tarball, so it only proves the folder is self-consistent, and a `docker save` tarball cannot prove a registry digest at all: the pin is therefore the **image id** this box read back from docker, and the staged digest goes into the history as `staged-digest=… (unverified)`. With `--from`, `--digest` names that **image id** (`docker image inspect --format '{{.Id}}'` on the staging box) and is compared with the ID docker reports after the load; the MANIFEST's registry digest is refused there, because the folder wrote it itself | Re-pin an image still on the box (by digest, or by the image id kept in the history). No pull |
| maya | `ENGINE_PIN_MAYA=<tag>@<40-hex commit>`, only for a version reviewed in `scripts/engines/maya.sh` (`--digest <commit>` on the command line to name another; a staged folder's MANIFEST never stands in for it). A version is `v<N>.<N>[.<N>[.<N>]]`, nothing else: it names the install directory | The commit id. Maya's own setup would fetch llama.cpp as an unhashed archive zip and pip-install unpinned packages. Instead llama.cpp is checked out at the commit its `setup.py` pins (read and compared first), and the packages come from `scripts/engines/maya/requirements.lock` with `--require-hashes`. Reinstalling a version on disk is refused while a Maya server runs from it; otherwise the old directory is moved aside and the new one renamed in. `prepare.sh` resolves the `~/.local/opt/maya` link before looking for the config Maya wrote, and records that resolved path | Relink `~/.local/opt/maya-<tag>` (offline) |
| tensorfold | `ENGINE_PIN_TENSORFOLD=<version>@sha256:<archive sha256>` | `install-native.sh`: archive against `.sha256`, `.sha256` against the release's ed25519 signature (vendored key), every unpacked file against `SHA256SUMS`. It always stages into `.engines/cache/` first | Relink `~/.local/opt/tensorfold-<V>-<platform>` after `SHA256SUMS` is re-checked. Instant and offline |

- `update` = install the latest **release**. llama: the highest `vX.Y.Z`
  (stable). The `bNNNN` CI builds are pre-releases, available as
  `--version b12422` or `--version latest-build`. vLLM: the GitHub release
  *name*, because v0.33.0's release object carries the tag `v0.33.0rc3` (both
  name the same commit). TensorFold: `releases/latest`.
- A failed install never moves a pin. llama: `update.sh --llama --force` builds
  the checked-out commit with its own bin/ snapshot and restore. If that fails,
  `llama.cpp/` goes back to the previous commit too.
- History is `.engines/history.tsv` (untracked). Every `use` first copies the
  conf to `.engines/conf-backups/` (0600).
- **Rollback always has the build that ran before.** Before `install`,
  `update` or `use` changes anything, the build this box has now is recorded
  as an `adopt` entry if the history's latest entry is not it: a
  `llama-server` built by hand from a branch, outside engine.sh, used to be
  missing, and `rollback` then had nothing to return to (the rig, 2026-11-17).
  For llama the rollback rebuilds that commit, so it must still be in
  `llama.cpp/` (a branch's commit is); a binary from a commit this clone does
  not hold is reported and not recorded, so copy `llama.cpp/build/bin` by hand.
- **llama.cpp's build number.** Its cmake derives the build number from the
  commit count, and the UI target fetches the prebuilt assets of `b<number>`.
  A depth-1 clone counts 1, asks for `b1`, falls back to an incompatible
  `latest`, and cmake fails. So the online install fetches whole history
  (`--unshallow` on a shallow clone, once), `stage` writes `BUILD_NUMBER=` into
  the MANIFEST and tags the commit `b<N>` on the closed box, and every build
  (`update.sh`, `bootstrap.sh`) passes `-DLLAMA_BUILD_NUMBER` from
  `ob_llama_build_number` (`scripts/lib/hardware.sh`).
  `install --from` fetches the one-commit staged repo with `--depth 1`, which
  writes the commit into `.git/shallow` even when the clone holds its
  parents; the install then removes that false cut (every parent present),
  so the clone stays whole and counts the build number itself (a clone count
  that disagrees with the MANIFEST's `BUILD_NUMBER` wins, with a note).
  The prebuilt Web UI is not staged: its download is made by llama.cpp's own
  cmake, so on a closed box run the install with `OFFLINE=true`, which builds
  with `-DLLAMA_USE_PREBUILT_UI=OFF` instead of stalling ~11 minutes on the
  fetch and failing. `install --from` without it says so.
- **On a Spark**, install / update / rollback also rewrite
  `scripts/backends/spark.env` (`VLLM_IMAGE` / `VLLM_IMAGE_DIGEST` /
  `VLLM_IMAGE_ID`: a pulled pin writes the digest plus that image's local ID
  when known; an offline-installed image writes its ID and clears the digest;
  `TENSORFOLD_VERSION`), so `spark-node.sh` launches the pinned build.
  `--spark-env FILE` does the same anywhere. **Every rank must run the same
  build**: update each Spark, then restart all ranks.

### Offline (closed network, `OFFLINE=true`)

Every install has an offline path. Stage on a connected box (any
architecture), carry the folder over, install from it:

```bash
# connected box
scripts/engine.sh stage llama      --version v1.0.0 /media/usb/llama
scripts/engine.sh stage vllm       --version v0.33.0 /media/usb/vllm      # the box's own arch; docker save
scripts/engine.sh stage tensorfold --version 1.2.9 --platform linux-aarch64 /media/usb/tf
MAYA_STAGE_PYTHON=3.12 scripts/engine.sh stage maya --version v1.1.65 --platform manylinux_2_28_x86_64 /media/usb/maya
# closed box
scripts/engine.sh install llama      --from /media/usb/llama
scripts/engine.sh install vllm       --from /media/usb/vllm
scripts/engine.sh install tensorfold --from /media/usb/tf
scripts/engine.sh install maya       --from /media/usb/maya     # both commits re-verified; wheels with --no-index
```

Each folder is re-verified on the closed box: the llama commit id, the vLLM
tarball sha256 and image id, the Maya commits against the reviewed table,
and the TensorFold signature. A MANIFEST is parsed, never sourced, and a
field that is not its shape (a path, a line break) refuses the install
before it names a file or reaches `openbeast.conf`. Under
`OFFLINE=true`, `update` without `--from` refuses and says why.

## Adapter contract (adding an engine)

An engine is **one file**, `scripts/engines/<name>.sh` (`[a-z][a-z0-9-]*`),
sourced by `scripts/lib/engine.sh`. It defines `eng_<name>_*` functions (a
`-` in the name becomes `_`). Once that file exists, `<name>` is a valid
`INFERENCE_BACKEND` (`lib/backend.sh`), `engine.sh list` shows it, and `use` /
`install` / `rollback` drive it. Nothing else needs an edit.

| Function | Required | Contract |
|---|---|---|
| `label` | yes | Human name (`vLLM`) |
| `hosts` | yes | Host classes it runs on, space-separated (`rig spark mac`) |
| `formats` | yes | Weights formats, one line |
| `installed` | yes | Print the installed version and return 0; return 1 when absent. No network |
| `pin` | no | Print `version@ref`. Without it, the pin is `ENGINE_PIN_<NAME>` in openbeast.conf |
| `current` | no | Print `version<TAB>ref` of the build this box has now, even one installed outside engine.sh (llama: the commit the built `llama-server --version` reports). `install` / `update` / `use` record it as an `adopt` history entry when the history's latest entry is not it, so `rollback` can return to it. Without it, the pin is what is current |
| `latest` | yes | Print `version ref-or-dash` for the newest release (network) |
| `install <version> <from-dir> <digest>` | yes | Fetch or build and **verify**, then set `ENG_RESULT_VERSION`, `ENG_RESULT_REF` (immutable) and optionally `ENG_RESULT_NOTE`. Return non-zero without side effects on failure |
| `stage <version> <dir>` | yes | Write a self-verifying folder (with a `MANIFEST`) for `install --from` |
| `activate <version> <ref> <note>` | no | Rollback without the network. Return 1 when impossible, and engine.sh then reinstalls |
| `launch` | no | Single-host managed server, **foreground**. `ENG_PRINT=1` prints the command only. Reads `INFERENCE_URL`, `ENGINE_PROFILE`, `LLAMA_API_KEY` |
| `profile_check <profile>` | with `launch` | Can this profile run managed here (TP 1, right `BACKEND`)? |
| `stop` | with `launch` | Stop this checkout's managed instance (`stop.sh`). Idempotent |
| `owns_port <pid> <port>` | no | For servers that are not a descendant of the launcher (containers). Return 2 for "use the pid-tree check" |
| `ready <url>` | no | Readiness. The default for a new engine is HTTP 200 on `/health` |
| `spark_env <version> <ref>` | no | `KEY=VALUE` lines to keep `spark.env` in step |
| `hydra_kind` | no | What beast-hydra calls it (`llama` / `vllm` / `tensorfold` / `openai`) |
| `profile_locate <profile>` | no | An adapter with its own profile format (maya: GGUF weights from `weights.registry`) sets `SP_LOCATE_RC` (0 = every weight on this box). `engine.sh use` calls it in place of `model-fetch.sh --locate` |
| `scope_file <profile>` | no | The file whose `# openbeast-scope:` line sizes `start.sh -d`'s memory scope for this profile (`scripts/lib/memscope.sh`) |

What a 4th engine (SGLang, exllamav3 / TabbyAPI, Ollama) still needs outside
its file:

- beast-hydra: a node with `engine = "openai"` (hydra's own list is `llama
  vllm tensorfold openai`, `agents/hydra_core.py`).
- A profile `BACKEND` value, if it should take model profiles: `obprofile.py`
  accepts only `vllm | tensorfold` today.
- The dashboard's `/api/slot` reports a generic OpenAI server.

## Hooks in shared files

Every hook is delimited by `>>> engine-plane hook` / `<<< engine-plane hook`,
or ends in `# engine-plane hook`. For `INFERENCE_BACKEND=llama` each one is a
no-op: the variables they set stay empty, and every `${_OB_*:-…}` falls back
to the original word. `tests/test_lifecycle.sh` lifts the llama launch
functions out of `start.sh` verbatim and still passes, and
`tests/test_engines.sh` runs the llama path end to end.

- `start.sh`
  - one block after `MANAGED=` that switches to `engine-serve.sh`
  - `${_OB_READY_FN:-ob_llama_ready}` twice in `wait_llama_health`
  - an `_OB_OWNS_FN` guard at the top of `_llama_port_ours`
  - labels in three echo lines
  - `_pid_pattern llama` widened only when the backend is not llama
- `stop.sh`: an `elif` for a managed non-llama engine.
- `scripts/healthcheck.sh`: an `elif` before the llama-loading check.
- `scripts/lib/conf.sh`: the managed-non-llama rule (one host plus an adapter
  launcher).
- `scripts/doctor.sh`: an "Inference engine" section (`ob_engine_doctor_rows`).
- `scripts/lib/backend.sh` (not a shared hot spot): adapter-only engine names
  in `normalize`, `label` and `ready`.

## Research notes (primary sources, read 2026-11-15)

- **The pin moved to v1.0.0** (`b1b770d9…`, 2026-11-15) for the MoE expert
  cache and `glm5-next`. The serve-script flag audit against v1.0.0, and the
  DNS-rebinding Host guard serve.sh had to accommodate (#32098), are in
  [WEIGHT_STREAMING.md](WEIGHT_STREAMING.md) "The llama.cpp v1.0.0 pin".
- **Project Maya** v1.1.65 (lightweight tag on `c81fab6c…`, 2026-11-16 UTC,
  no release assets; `thomas9120/project-maya` is a fork). The facts the
  adapter depends on, and why Strata has no adapter, are in
  [WEIGHT_STREAMING.md](WEIGHT_STREAMING.md) "The engine gate".

- **llama.cpp**: GitHub's "latest" is the first semver release, **v1.0.0**
  (2026-11-13). It is an annotated tag that peels to commit `b1b770d9…`, the
  same commit as build b12359. The CI builds are still `bNNNN`, marked
  pre-release; the newest is **b12422** (2026-11-15, `a681a53b…`). Prebuilt
  `ubuntu-cuda-13.5-x64` and `-arm64` tarballs exist. OpenBeast builds from
  source at a pinned commit (`bootstrap.sh`, `update.sh --llama`). v1.0.0
  removed 35 deprecated options (incl. `--defrag-thold`, which no serve script
  uses), added an FP8 KV cache, and tightened `llama-server` security
  (`--allow-unauthenticated-tools`, `--media-allow-private-urls`, CORS / DNS
  rebinding / SSRF). **Read the serve scripts against v1.0.0 before moving the
  pin.**
  - `gh api repos/ggml-org/llama.cpp/releases/latest`, `…/git/ref/tags/v1.0.0`,
    `…/releases/tags/b12422`
- **vLLM**: **v0.33.0** (2026-11-02, commit `4ba787ec…`; the release object's
  tag is `v0.33.0rc3`). Previous: v0.32.0 (2026-10-18). Docker Hub
  `vllm/vllm-openai:v0.33.0` is a multi-arch index:
  `sha256:d825baf9ce4b1b6bfc5ecc7b79880c7e6748c65a72d697ce3d47607aafa84c6e`
  (amd64 `sha256:91ee972a…` = `v0.33.0-x86_64`, arm64 `sha256:e3481489…` =
  `v0.33.0-aarch64`). CUDA 13 is the default, and v0.33.0 dropped the cu129
  images (#62575). GLM-5.3-Flash (`Glm5Next*`) has been upstream since
  **v0.30.0** (PR #53906, merged 2026-09-03; absent in v0.29.0), which
  confirms DGX_SPARK_PLAN §15's "vLLM ≥ 0.30". GB10 / SM12x issues open
  today:
  - #57156: CUDA-graph NaN for sparse MLA; workaround `--enforce-eager`.
  - #50925: NVFP4 MoE falls back to Marlin on sm_121.
  - #61007: cuDNN FP8 GEMM hang on GB10.
  - #51920: FlashInferMLASparseSM120 crash.
  - #51884: FP8 block-scaled weights fail in DeepGEMM on an RTX 5090.

  NGC `nvcr.io/nvidia/vllm:26.10-py3` (manifest list `sha256:0a0de936…`) ships
  vLLM **0.31.0**. Its Spark notes advise `--gpu-memory-utilization 0.7`.
  - `gh api repos/vllm-project/vllm/releases`, `…/pulls/53906`,
    `…/issues/57156`
  - https://hub.docker.com/v2/repositories/vllm/vllm-openai/tags
  - https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-26-10.html
  - https://docs.vllm.ai/en/latest/features/quantization/gguf.html
- **TensorFold**: **v1.2.9** (2026-11-15; v1.2.8 2026-11-13).
  - Assets: `tensorfold-1.2.9-linux-{x86_64,aarch64}.tar.gz` plus `.sha256`
    and `.sha256.sig`, and `macos-arm64`.
  - Requirements: glibc ≥ 2.28 and driver R580+ (CUDA 13).
  - CUDA fatbins: **sm_80, sm_86, sm_120, sm_121** (no sm_90). v1.2.9 refuses,
    by name, a GPU it has no kernels for.
  - Signature scheme unchanged: ed25519 over
    `"tensorfold-native-checksum-v1\n" + .sha256`. The key is in
    `packaging/runtime-trust.json`, unchanged since `27b1b136` (2026-11-07),
    and it is the key `install-native.sh` vendors.
  - On a 5090: the CHANGELOG records Qwen3.8-27B MLX-4bit running on CUDA on an
    RTX 5090 (#1373, #1078). Qualified sm_120 runs are on RTX PRO 6000 / 4500
    only.
  - `gh api repos/ashhart/TensorFold/releases`, `packaging/README.md`,
    `packaging/runtime-trust.json`, `docs/evaluating.md`

## The rig A/B (llama.cpp vs vLLM), when the rig is back

None of this has run on the 5090 yet. To measure the A/B you need:

1. **A model that both engines can serve and that is allowed to be served.**
   The default is a GGUF of the uncensored Qwen3.8-27B.
   - vLLM needs either an uncensored **safetensors / NVFP4** build of the same
     model, or the GGUF through `vllm-gguf-plugin`. The plugin is
     experimental, but it is the only same-weights comparison.
   - Write a TP 1 profile with `GPU_MEMORY_UTILIZATION≈0.90` and a
     `MAX_MODEL_LEN` that fits 32 GB:
     `model-inspect.sh <repo@sha> --write-profile NAME`, then
     `model-fetch.sh --profile NAME`.
   - No such profile ships, because no reviewed uncensored NVFP4 repo is
     pinned yet.
2. `scripts/engine.sh install vllm` (pins v0.33.0 by digest), then
   `scripts/engine.sh use vllm --profile NAME`.
3. `scripts/backends/conformance.sh` (tools, reasoning split), then the eval
   suite against both engines at the same model. Pass `--models <slug>` on
   `evals/benchmark_all.py`, or both rows will not exist.
4. Record decode tok/s single-stream and at `MAX_NUM_SEQS`, time to first
   token, VRAM at idle, and the context vLLM admits. Then
   `scripts/engine.sh use llama` to go back.
5. The same for TensorFold (Qwen3.8-27B MLX-4bit, TP 1) is the third column.
   It is unqualified on a 5090, so treat the first run as a qualification.
