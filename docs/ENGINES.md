# Engines — choose, swap, update and roll back the inference engine

OpenBeast talks OpenAI-compatible HTTP to one inference server. That server, the
**inference plane**, can be one of four engines (Basalt is the RTX 5090's
default since 2026-11-24, [below](#basalt-the-rtx-5090-default)), plus one
that is opt-in and gated (Project Maya: admitted only after it measures 1.5x
llama.cpp on the rig, [WEIGHT_STREAMING.md](WEIGHT_STREAMING.md)). Each has defaults per host
class, and you can override them. `scripts/engine.sh` is the one command for
all of it:

```bash
scripts/engine.sh list                         # what this host can run, what is installed / pinned / active
scripts/engine.sh status                       # the active engine: version, pin, endpoint readiness, model served
scripts/engine.sh use vllm --profile NAME      # switch (stops a running stack, rewrites the conf, starts it)
scripts/engine.sh use llama                    # llama.cpp: the default on rig GPUs other than an RTX 5090, the 5090's fallback
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
| **llama** (llama.cpp `llama-server`) | **default on every rig GPU but the RTX 5090, the 5090's fallback, and what an unset conf launches** — every `scripts/serve-*.sh`, measured on the 5090 | builds (CUDA arm64), not the Spark default | **GGUF** only | yes, through `scripts/serve-*.sh` (unchanged) |
| **vllm** (`vllm/vllm-openai`, container) | yes — one GPU, TP 1, managed | yes — TP 1 managed, or TP 1..8 across Sparks (unmanaged, `spark-node.sh`) | safetensors: BF16 / FP8 / **NVFP4** / AWQ / GPTQ. GGUF only through the out-of-tree `vllm-gguf-plugin` ("highly experimental") | TP 1 on this box: yes (`engine-serve.sh`). Otherwise unmanaged |
| **maya** (Project Maya, built from a reviewed commit; opt-in, **gated**) | opt-in only after `scripts/engine-gate.sh` measured **≥ 1.5×** llama.cpp's decode tok/s on the same weights ([WEIGHT_STREAMING.md](WEIGHT_STREAMING.md)) | no | GGUF (GLM-5.3-Flash through `--gguf-dir`, Maya packs), EXL3. The adapter pins v1.1.65 (GLM big mode, never run). Inkling 2 Mini (`maya-ik2`, v1.1.72+) was measured outside the adapter and **not adopted** ([below](#evaluated-not-adopted)) | TP 1 on this box: yes, once the gate passes |
| **basalt** (Basalt, built from a reviewed commit in a pinned env image; [below](#basalt-the-rtx-5090-default)) | **default on an RTX 5090** since 2026-11-24 (Inkling 2 Mini, stock pack; `use default` picks it there). sm_120 only | no | Basalt packs (`.basalt`): Inkling 2 Mini NVFP4-MX8 | TP 1 on this box: yes, a hardened container (`engine-serve.sh`) |
| **tensorfold** (native ≥ 1.0 release binary) | yes — sm_120 kernels ship. Single-GPU fits such as Qwen3.8-27B MLX-4bit are plausible, **not qualified on a 5090** | **default** — TP 1 managed, or TP 1..8 across Sparks (unmanaged, `spark-node.sh`) | **MLX-affine 4/6/8-bit** safetensors, NVFP4 (ModelOpt), EXL3 (DeepSeek-V4.1-Flash (Mia EXL3), GLM-5.3-Flash). **No GGUF on CUDA** | TP 1 on this box: yes. Otherwise unmanaged |

**The live Spark pair** serves **DeepSeek-V4.1-Flash (Mia EXL3)** by default
(unit `deepseek-v41-inference`) and GLM-5.3-Flash as the staged alternative
(unit `spark-hydra-inference`), one at a time, each with TensorFold TP 2 from
its own recipe's container image, unmanaged from this plane (the command
center reaches it at `http://127.0.0.1:8000`). Switch between them only with
`beast-spark` on spark-1 (`beastds` / `beastglm`), never with `engine.sh use`:
[SPARK_HYDRA.md](SPARK_HYDRA.md) (the live deployment note at its top,
[DeepSeek deployment](SPARK_HYDRA.md#deepseek-deployment) and
[D1](SPARK_HYDRA.md#d1-the-glm-recipe-the-alternative-deployment)).

**Which engine serves a given model on the Sparks** (and at how many ranks)
is answered by beast-recipe, not by this plane: `scripts/beast-recipe.sh plan
<recipe>` evaluates every engine in `scripts/backends/data/recipe-engines.json`
(native TensorFold releases, the dsv41 fork image, Mia's GLM image, the Hy4
vLLM kit, NGC vLLM, ExLlamaV3) for the checkpoint's model_type, format, EXL3
layout and rank count, with sources, and picks the best path with a verdict.
Its `engine <recipe>` step checks each Spark's engine and prints the
`engine.sh stage` / `install --from` / `rollback` commands above when a release
must be staged ([BEAST_RECIPE.md](BEAST_RECIPE.md#the-engine-and-family-matrix)).
Native TensorFold, 2026-11-22: 1.2.10 is installed on both Sparks; the latest
upstream release is **1.3.4** (published 2026-11-22 20:53Z; it was the README's
install pin before it had a release, and the matrix's `unreleased` status
covers that case). The Qwen3.8 recipe pins 1.3.4; the Hy4 TensorFold recipe
stays on 1.3.3 (1.3.4 adds no Hy4 path). `install-native.sh`'s default and
the ark catalog pin **1.3.4** since 2026-11-23 (was 1.2.7; each archive's
sha256 is the release's signed `.sha256`, re-hashed and signature-checked
when pinned). The default only decides a bare `install-native.sh`: every
recipe names its own release (`TENSORFOLD_REQUIRES`, `spark.env`'s
`TENSORFOLD_VERSION`), and the live DeepSeek serve is Mia's
`tensorfold-dsv41` image, not this binary. `~/.local/bin/tensorfold` is one link
per Spark: a recipe on another release needs `engine.sh install` (and
`rollback`) on every Spark before its switch.

**Host class** comes from `scripts/lib/hardware.sh`. A GPU that reports
`memory.total` as `[N/A]` (`OB_GPU_UNIFIED=1`) makes this host a `spark`; macOS
is `mac` (llama or tensorfold `macos-arm64`); everything else is a `rig`.
`OB_ENGINE_HOST_CLASS` overrides the detection (`engine.sh`, `engine-serve.sh`
and `engine-gate.sh` also set it themselves after detecting once, so one
command runs `nvidia-smi` once, not once per question; the same detection
pins the GPU's name in `OB_ENGINE_GPU_NAME`, which makes an RTX 5090 rig's
default `basalt`), and `ENGINE_DEFAULT` in `openbeast.conf` overrides the
default engine. The runtime fallback in
`conf.sh` stays `llama` when `INFERENCE_BACKEND` is unset. The host-class
default only decides what `engine.sh use default` picks and what `list` and
`doctor` call the default, so an unset conf never changes what a box launches.

### Weights format is the coupling

The engine decides which model files you can serve, so swapping engines nearly
always means a different checkpoint:

- **OpenBeast's llama.cpp models are GGUF** (`weights.registry`; the
  llama.cpp default is the uncensored Qwen3.8-27B MTP Q5_K_M). Only llama.cpp
  serves them natively. The RTX 5090's default is a Basalt pack
  (`Inkling-2-Mini-NVFP4-MX8.basalt`, also pinned in `weights.registry`),
  which only Basalt serves.
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
- The fleet policy is uncensored-preferred (Max, 2026-11-19; DGX_SPARK_PLAN
  §15): a profile may name a stock checkpoint (the Spark pair's
  DeepSeek-V4.1-Flash (Mia EXL3) default and Hy4-flash (NVFP4) are stock),
  and an uncensored variant of comparable quality is preferred when both
  could serve. `family_policy = "uncensored-only"` restores the strict rule
  ([BEAST_HYDRA.md](BEAST_HYDRA.md#family-policy)).

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
| basalt | `ENGINE_PIN_BASALT=<tag>@<40-hex commit>`, only for a version reviewed in `scripts/engines/basalt.sh` (v0.5.7 = `400b411c`; `--digest` on the command line for another) | The commit id; the build record `OPENBEAST_BUILD` in each build dir (commit, applied patches with their sha256, the env image id, `bin/basalt` sha256). The env image's server packages are checked against exact versions after every build or load | Relink `~/.local/opt/basalt-<tag>[+<patch tag>]` whose env image is still on the box (offline) |
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
scripts/engine.sh stage basalt     --version v0.5.7 /media/usb/basalt     # bare repo of the commit + the env image (docker save) + MANIFEST
# closed box
scripts/engine.sh install llama      --from /media/usb/llama
scripts/engine.sh install vllm       --from /media/usb/vllm
scripts/engine.sh install tensorfold --from /media/usb/tf
scripts/engine.sh install maya       --from /media/usb/maya     # both commits re-verified; wheels with --no-index
scripts/engine.sh install basalt     --from /media/usb/basalt   # commit, tarball sha256, image id, package pins re-checked; compile --network none
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

## Basalt, the RTX 5090 default

**Basalt** (`github.com/jesdga95/basalt`, MIT; v0.5.7 = `400b411c58031d064812ea21a259b2381acdaa3e`)
serves the RTX 5090's default model since 2026-11-24, Inkling 2 Mini on
Basalt's stock NVFP4-MX8 pack (Max's decision,
[DECISIONS-2026-11-24 §10](reports/DECISIONS-2026-11-24.md#10-stock-inkling-2-mini-on-basalt-is-the-rtx-5090-default);
serve args and agent setup of board row `INK-T2-STOCK`, 53/71 scored, SCORE
78.2, single seed). **The default build is not that row's build:** it is the
pin plus the PR #632 patch (§11), which `INK-T2-STOCK` ran without, and no
board row has measured pin + #632 at these args yet (the profile's
`PATCHES` key names the build, and the launch refuses any other; I_T3,
2026-11-25, ran the patch inside a composite with a 16,384 cap and
`--answer-room 8192`: 0 empty capped turns in 3,204 replies, 52/71, level
with `INK-T2-STOCK`, so the default's answer room is still open,
[TODO.md](TODO.md)). It was
chosen 2026-11-23 for the Inkling v5 runs as the one engine that drives T2
on Inkling (thinking with `reasoning_content` and tool calls, streamed and
not; conformance PASS) and the fastest single stream (403 / 356 tok/s code /
prose with the Derisked pack, 376 / 307 stock, against vLLM 269 / 161 and
Strata 201 / 131) and prefill (18-19K tok/s)
([report §11](reports/STRATA_INKLING_5090-2026-11-23.md#11-pending-native-sq-packs-and-other-engines),
security review [§15](reports/STRATA_INKLING_5090-2026-11-23.md#15-basalt-security-and-supply-chain-review)).
The adapter is `scripts/engines/basalt.sh`; the profile
`scripts/engines/basalt/profiles/inkling2-mini-stock.env`; switch and
rollback: [MODELS.md](MODELS.md#switching-the-rtx-5090-to-its-default-and-back);
a fresh install: [INSTALL.md](INSTALL.md#rtx-5090-the-default-model-inkling-2-mini-on-basalt).

**The review's "before default" items (§15.8) and how the adapter closes them.**
"Tested" means `tests/test_engine_basalt.py` (a fake docker records every
argv; no GPU); "live" means proven on the rig by the eval serves; "at the
switch" means first checked when the rig switches ([TODO.md](TODO.md)).

| Item | What the adapter does | Verified |
|---|---|---|
| Source at a reviewed pin, offline | Only a version in the reviewed table builds (v0.5.7 = `400b411c`); the commit id is checked after the fetch and the tree must be clean. The compile runs in the env image with `--network none`, unprivileged, `-DBASALT_MODELS=inkling2 -DCMAKE_CUDA_ARCHITECTURES=120` (the measured build, `build-basalt.sh` on the rig). Offline: `stage` / `install --from` (bare one-commit repo, the env image as a docker-save tarball saved by id, a MANIFEST with sha256, image id and Dockerfile sha256). `install --from` checks the commit against the pin, the MANIFEST's Dockerfile sha256 against this checkout's Dockerfile, the tarball's sha256 and image id against the MANIFEST, refuses a tarball that names images (`docker load` would retag them), and tags the loaded image `openbeast-basalt-env:<hash>` so a `docker image prune` cannot remove it. The tarball's hash and id prove the transfer is intact, not where the image came from: a folder can only be trusted as far as the box that staged it. beast-ark holds the source and the CUDA base image by digest | Tested (an offline install end to end against a fake docker; each MANIFEST mismatch and a tagged tarball refused; the swap of an existing build, the refusal while it serves); the measured build is live (`bin/basalt` `5dc8a63c…`); a build through the adapter: at the switch |
| The #588 fix (upstream PR #632) | A hook: every `scripts/engines/patches/basalt-pr632-*.patch` in the checkout must be a row of the adapter's reviewed patch table (file name, the commit it is for, the sha256 of its diff body; an unlisted or edited file stops the build), is applied after `git apply --check` and recorded with its sha256 (build dir `basalt-v0.5.7+pr632-<12 hex>`); none present = the pin exactly, which is what INK-T2-STOCK measured. A rollback picks, of two builds at one commit, the one whose patch tag the history names (else this checkout's, else the newest). `ENGINE_BASALT_PATCHES=none` forces the pin. **From 2026-11-24 (Max) the patch is on `main`**: `scripts/engines/patches/basalt-pr632-55f9be17.patch` (PR head `55f9be17`, body sha256 `af15ee07…`; Python server only), so a build through the adapter is the pin plus it; the rig's eval serves use the same patch in a copy of the measured build tree (`scripts/engines/basalt-apply-pr632.sh`, engine binaries unchanged). Review: [Strata report §16](reports/STRATA_INKLING_5090-2026-11-23.md#16-openbeasts-local-patch-for-upstream-588-pr-632) | Tested (apply, record, refusal of an unreviewed, edited, other-commit or non-applying patch; the shipped file hashes as pinned); **live**: the patched tree served on the rig 2026-11-24, #588 probe 0 empty of 16 where the pin gave 5, conformance PASS c=1 and c=4 (Strata report §16.6) |
| Python side pinned | No venv and no PyPI: the server runs the env image's distro `python3`. `scripts/engines/basalt/Dockerfile` pins the base by digest, every Ubuntu package by one archive snapshot (`20261124T013100Z`, when the measured image was built), and the six server packages to the exact versions the measured rows ran on (python3 3.12.3-0ubuntu2.1, jinja2 3.1.2-1ubuntu1.3, markupsafe 2.1.5-1build2, regex 0.1.20221031-2build1, psutil 5.9.8-2build2, pil 10.2.0-1ubuntu1.5); the adapter re-reads them from the built or loaded image and refuses a mismatch | Tested (the check); the Dockerfile build itself: at the switch (the measured image was built from the live archive with the same package list) |
| No DNS, no route out | A user bridge `openbeast-basalt-net` on the interface `ob-basalt0` with `enable_ip_masquerade=false` (no NAT) and `--dns 127.0.0.1` (Docker's resolver gets no upstream). **Checked at every launch** by a probe container with the server's exact sandbox flags, which must: fail to resolve example.com, huggingface.co and github.com; fail to connect to 1.1.1.1:443, 8.8.8.8:53 and the bridge gateway's port 22; and get no answer to a raw UDP DNS query sent straight to the gateway, to 172.17.0.1 (docker0) and to 1.1.1.1 on port 53. Otherwise the server is not started. That is all the probe tests: other ports on the host are reachable unless the host firewall denies the bridge, so the rig denies `ob-basalt0` inbound (`sudo ufw insert 1 deny in on ob-basalt0`; [TODO.md](TODO.md)). An existing network of that name with NAT, or on another interface, is refused. **2026-11-24 review:** the rig's ufw accepts UDP 53 to 172.17.0.1 from all of 172.16.0.0/12 and 192.168.0.0/16 (every Docker default address pool), where its host resolver answers, so the raw-DNS check is expected to refuse the launch there until that deny rule is in place | Live: the same bridge + `--dns 127.0.0.1` made the name and TCP checks fail on three eval serves (V5 Inkling report §10.1); the raw-DNS checks were added after them and have not run live (from the host, a raw query to 172.17.0.1:53 was answered); tested: each check gates the launch |
| Port on 127.0.0.1 only | `-p 127.0.0.1:<port>:<port>`; any other `INFERENCE_URL` host refuses (remote clients go through beast-gate) | Live (the eval serves); tested |
| Read-only root, tree, weights, key | `--read-only`; the build dir, the pack file and the key file are mounted `:ro` (only those three) | Live; tested |
| No host home | `HOME=/tmp`; no mount of `$HOME` or a parent of it | Live; tested |
| `--cap-drop ALL`, `no-new-privileges` | Both, on the server and the probe | Live; tested |
| Non-root | `--user <uid>:<gid>` of the operator; uid 0 refuses | Live (uid 1000); tested |
| PID and memory limits | `--pids-limit 256`, `--memory 32g --memory-swap 32g` (no swap). The measured serve's steady state was 16 PIDs, 2.84 GiB (`docker stats`, 2026-11-24, during I_UP8k); the load peak was not measured. `ENGINE_BASALT_PIDS` / `_MEMORY` raise them; a value that is not a limit refuses | Tested; **unverified on a live serve: at the switch** |
| `/tmp` noexec | `--tmpfs /tmp:rw,noexec,nosuid,nodev,size=8g` (the eval serves had `exec`). By the source at `400b411c`, the serve path writes no executable to `/tmp` (`temp_dir()` is used by the probe and bench programs only; the server's temp files are vision dirs, off here); CUDA's JIT cache under `HOME=/tmp` is data | Tested; **unverified on a live serve: at the switch** (if Basalt fails to start, that is the first suspect) |
| Behind the gate, key like llama's | The engine binds `INFERENCE_URL`'s port on loopback; beast-gate and the router forward to it as to llama-server. `LLAMA_API_KEY` reaches Basalt as a 0600 file under `.run/`, mounted read-only for `--api-key-file` (never argv); no key = keyless on loopback, as llama-server | Tested (the value never appears in any argv) |
| GPU | `--gpus device=0` (the NVIDIA Container Toolkit; `ENGINE_BASALT_GPUS`); the launch refuses a GPU whose compute capability is not 12.0 | Tested; the toolkit path: at the switch (the eval serves passed the devices by hand) |

Readiness is Basalt's `/health`: 200 with `status ok`, `loaded true` and
`service basalt` (`serve/handler.py:415-421` at the pin). Still open, not
part of §15.8: the image is a full build image (compilers, git), more than a
serve needs; a slim serve image is a follow-up. Basalt's whole `serve/tests`
has not been run against the distro package set (conformance and the
thinking probes passed on it, §11.2); its Inkling dialect and thinking-budget
tests have (28 OK in the measured image, with the #632 patch; Strata report §16.6).

**Thinking off is a hint on Inkling, and it drops the answer room.** At the
pin, `enable_thinking: false` becomes `reasoning_effort: "none"`
(`serve/models/inkling2.py:527-529`), which the template writes into a
system message; the model opens its own blocks, so it can still think. Such
a request also gets no thinking budget and no answer room
(`serve/service.py:1148`, 1370, 1511), so its thinking runs free to the
output cap. Short prompts hide it (a 117-character block on "What is
17*23?"); in the I_UP8k arm (2026-11-24), 67 of 68 thinking-off requests
deep in agent work still thought, 56 ran to the 8,192 cap, and each one and
the request after it missed the prompt cache (the effort level changes the
rendered system message). The default setup (`inkling-best`, T2) never
sends thinking off. A client that needs a short reply should send a small
`reasoning_budget_tokens` instead (honoured, `service.py:226-247`)
([report §10.9.3](reports/V5_INKLING2_MINI_RTX5090-2026-11-24.md#1093-the-forced-act-turns-did-not-run-with-thinking-off)).

**History.** I_T2 (2026-11-24) did not justify switching (49/71 against
Qwen3.8's 52/71); the ablations brought the stock pack to 53/71, level with
Qwen3.8 paired (−2 over 65 clean units), and kept the server logs (DSpark
accepted 88.7% of drafted tokens on the stock arm; decode 444 tok/s over
all replies). The wrap-up kept Qwen3.8 pending an improved arm
(DECISIONS-2026-11-24 §6); Max made the stock Inkling the default the same
day (§10). [Report](reports/V5_INKLING2_MINI_RTX5090-2026-11-24.md).
## Evaluated, not adopted

Engines measured on our hardware and deliberately left out of the matrix.
Each line says why, so the question is not reopened without new evidence.

| Engine | Status | Why not | Evidence |
|---|---|---|---|
| **Strata** (`Niko1221/Strata`, MIT; `strata-inkling` built from source at `6a6db7ff`, binary sha256 `11df1066…`) | **Evaluated 2026-11-23 on the RTX 5090 with Inkling 2 Mini (unsloth UD-Q4_K_XL GGUF); not adopted** | (1) It forces Inkling's thinking off (`HAS_THINKING = False`; every effort normalizes to `"none"`), and our best agent setup is thinking-first (T2), so it cannot run arm `I_T2`. (2) Its DSpark drafter dies at startup on CUDA with a GGUF drafter (`dspark.layers.N` vs `layers.N`; upstream #6762, a one-line fix not merged at the pin). (3) Alpha: open 5090 hangs at high concurrency and long prefill, reasoning-channel bugs, one author. (4) Its release binaries, images and `curl \| sh` installer have no provenance; only a reviewed source build in a locked-down container is acceptable. What it does well: 1.43x llama.cpp single-stream on the same tensors (138.6 vs 96.9 tok/s), 1.5-2.0x prefill, conformance PASS. The "4,087 tok/s" claim is its own 64-request aggregate (4,089.7), not single-stream | [reports/STRATA_INKLING_5090-2026-11-23.md](reports/STRATA_INKLING_5090-2026-11-23.md); earlier, for weight streaming: [WEIGHT_STREAMING.md](WEIGHT_STREAMING.md#why-maya-and-not-strata) |
| **Project Maya** for Inkling (`mw00/project-maya`, MIT; v1.1.76 = `8c618a5994e25932ff4977498d6195f8b80f91bb`, `maya-ik2` built from source offline, sha256 `61598619…`) | **Evaluated 2026-11-24 on the RTX 5090 with Inkling 2 Mini (stock, unsloth Old_Support UD-Q4_K_XL GGUF); not adopted** | (1) Slower than Basalt on the same probes: single stream 329 / 204 tok/s code / prose (Basalt stock 376 / 307), 4-stream aggregate 350 / 354 (702 / 624; drafting turns off under `--parallel`), prefill ~6K (18-19K). (2) It cannot load the eval's Derisked model: it reads only `inkling2`-keyed GGUFs with NVFP4 / Q8_0 experts, which among our files is just the Old_Support UD-Q4_K_XL. (3) "Experimental preview", Blackwell untested upstream. What it does well: KLD 0.0137 / top-1 94.81% against Q8 (llama.cpp 0.0162 on the same tensors), byte-identical greedy output with drafting, conformance PASS at c=1 and c=4, thinking and tool calls separated. "Similar numbers to Basalt" holds only for single code prompts (one reached 513) | [reports/MAYA_INKLING_5090-2026-11-23.md](reports/MAYA_INKLING_5090-2026-11-23.md) |

Revisit Strata only if (a) a thinking mode for Inkling lands, (b) the DSpark
fix is in a reviewed commit, and (c) its own SQ packs beat Basalt (the report's §11: SQ-20 201 / 131 tok/s
against Basalt's 403 / 356, and ~1,400 at 64 streams, not 4,089.7). There is no `scripts/engines/strata.sh`
adapter, and none is planned.

Revisit Maya for Inkling only if (a) a reviewed release reads `inkling2_loop`
GGUFs or the Derisked checkpoint, (b) its prose drafting stops running below its
plain decode and `--parallel` keeps drafting, and (c) its prefill moves off ~6K tok/s
([the Maya report §11](reports/MAYA_INKLING_5090-2026-11-23.md#11-verdict)). The GLM
adapter (`scripts/engines/maya.sh`, v1.1.65) is unaffected.

## Research notes (primary sources, read 2026-11-15)

- **The pin moved to v1.0.0** (`b1b770d9…`, 2026-11-15) for the MoE expert
  cache and `glm5-next`. The serve-script flag audit against v1.0.0, and the
  DNS-rebinding Host guard serve.sh had to accommodate (#32098), are in
  [WEIGHT_STREAMING.md](WEIGHT_STREAMING.md) "The llama.cpp v1.0.0 pin".
- **Project Maya**: the adapter's reviewed version is still v1.1.65 (GLM
  only; Inkling arrived in v1.1.72). The Inkling evaluation reviewed v1.1.76
  (`8c618a59`, 2026-11-23 23:08Z): [reports/MAYA_INKLING_5090-2026-11-23.md](reports/MAYA_INKLING_5090-2026-11-23.md).
  Earlier note: v1.1.65 (lightweight tag on `c81fab6c…`, 2026-11-16 UTC,
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

None of this has run on the 5090 yet. The container prerequisite is met
since 2026-11-23: the rig has the NVIDIA Container Toolkit, so vLLM's
`--gpus all` works there ([INSTALL.md](INSTALL.md#gpu-in-docker-the-nvidia-container-toolkit)).
To measure the A/B you need:

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
