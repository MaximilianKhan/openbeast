# Strata on the RTX 5090: Inkling 2 Mini against llama.cpp (2026-11-23)

> **Status: evaluated, not adopted (2026-11-23).** Speed measurement only,
> no eval arm, no board row. Raw data, scripts and logs stay on the rig in
> `~/openbeast-runs/strata-bench-2026-11-23/` (paths below are relative to
> it). Everything here was read back from those files, from the review clone
> of Strata at `6a6db7ff`, from `gh api` on 2026-11-23, or from the session
> record; where a figure could not be traced it says **unverified**. Times
> are PST (UTC-8) unless marked `Z`.
>
> A second investigation (Strata's own SQ packs, vLLM NVFP4 + MTP, a
> "Basalt" candidate) followed the same evening; its results and the engine
> choice (Basalt v0.5.7 with the Blackfrost Derisked pack, arm `I_T2`) are in
> [§11](#11-pending-native-sq-packs-and-other-engines).
>
> A third candidate, Project Maya (`maya-ik2`), was reviewed and measured on
> 2026-11-24 and not adopted: [MAYA_INKLING_5090-2026-11-23.md](MAYA_INKLING_5090-2026-11-23.md).
> Its §9.1 proves the fix for this report's §15.4 DNS gap (`--dns 127.0.0.1`).

## 1. Summary

- **Why.** Max asked us (2026-11-23 afternoon) to support Strata
  (`github.com/Niko1221/Strata`) on the 5090 after claims of "4,087 tok/s
  Inkling 2 Mini on a 5090", and later of "754 tok/s code / 440 tok/s prose".
- **Security verdict (before anything ran).** Genuine MIT open source with no
  malware found, alpha maturity. Do not use its release binaries, images or
  `curl | sh` installer. Build from source at a reviewed pin, offline, in a
  locked-down container. That is what we did ([§2](#2-the-pre-run-security-and-supply-chain-review), [§3](#3-the-build), [§4](#4-the-isolation-actually-used)).
- **Speed, same 4-bit file, single stream, no drafter:** Strata **138.6 /
  138.7 tok/s** (code / prose) against llama.cpp **96.9 / 96.9**, about 1.43x.
  Prefill 1.5x to 2.0x. Four streams: about 320 against 239 tok/s total. With
  a drafter: Strata **198.5 / 132.6**, llama.cpp 170.2 / 78.5 ([§5](#5-results)).
- **Correctness.** Both engines pass the 15K-token needle and our conformance
  suite. Strata's top-1 token matches llama.cpp's on 96.76% of 1,020 positions,
  every disagreement within Strata's top 4 ([§6](#6-correctness-needle-and-conformance)).
- **A Strata bug.** On CUDA every `--draft dspark` run with a GGUF drafter
  dies at startup: the CUDA path looks up `dspark.layers.N.*` while the GGUF
  loader stores `layers.N.*`. A one-line patch fixes it; our drafter rows use
  the patched build. **Already reported upstream** (#6762 and four others),
  with the same fix proposed there ([§7](#7-the-bug-dspark-on-cuda-cannot-find-its-tensors)).
- **The claims, corrected ([§9](#9-claim-provenance-where-4087-and-754--440-come-from)).** Strata's own v0.2.19 release notes say
  "**4,089.7 tok/s total output across 64 concurrent requests** on RTX 5090":
  the circulating "4,087" is a 64-request aggregate, not single-stream, and
  not prefill (we said both wrong things earlier today; [§10](#10-corrections-made-today)). Their
  single-stream figures are "up to 250.3 tok/s solo (198.9 tok/s on lossless
  SQ-20)", on Strata's own SQ packs with the built-in drafter. Our 198.5 on
  code with unsloth's GGUF matches their SQ-20 tier. No official source
  claims 400+ single-stream, or 754 / 440.
- **Physics ([§8](#8-the-bandwidth-arithmetic)).** About 4.14 GB is read per token from this file, so plain
  single-stream decode tops out near **433 tok/s** at the 5090's 1,792 GB/s.
- **Verdict ([§12](#12-verdict-and-open-questions)).** Strata is the fastest engine we have measured for
  Inkling 2 Mini on the 5090, but it is not adopted: it forces Inkling's
  thinking off (our T2 eval arm needs thinking on), its drafter is broken on
  CUDA at the pin, and it is alpha. The engine for the Inkling v5 run is
  Basalt v0.5.7 with the Derisked pack (§11).
- **Basalt review ([§15](#15-basalt-security-and-supply-chain-review)).** No
  malware, no telemetry, no network code in the engine; OK to keep serving
  the `I_T2` eval in its sandbox. Two findings: the sandbox bridge still
  resolves public DNS names (Docker's embedded resolver), and Basalt's
  thinking-budget close does not force an answer on Inkling (upstream #588,
  open): 3 of the first 18 budget wraps in the live run ended at the cap
  with no answer (05:11Z). Not yet fit to be the 5090's default.

## 2. The pre-run security and supply-chain review

**Why a review first.** Strata is new code from a personal GitHub account,
and the 4,087 claim was extraordinary. Nothing from it ran until a read-only
review (a subagent, 2026-11-23 ~15:28-15:35, reported 23:35Z) had read the
website, the repo and the code. The review clone is
`~/.claude/jobs/400403b4/tmp/strata-src` on the Mac (a shallow clone, 176
commits deep, at `6a6db7ff`); a full bundle of the same commit is on the rig
at `build/strata-6a6db7ff.bundle` (sha256 `c7801e6b6beca09c…`, 46,919,042
bytes).

Every line below was re-checked for this report, by the command given.

### 2.1 stratallm.org: unofficial, not authoritative

| Fact | Evidence (re-checked 2026-11-23) |
|---|---|
| Registered 2026-10-04 20:08:02Z through Porkbun | `whois stratallm.org`: `Creation Date: 2026-10-04T20:08:02Z`, `Registrar: Porkbun LLC` |
| Calls itself unofficial | its footer: "© 2026 stratallm.org • Unofficial Community Documentation & Quickstart Hub." |
| Umami tracker | `<script … src="…umamimy.vercel.app/script.js" data-website-id="75fe84eb-…">` on the home page |
| Google Analytics | reported by the pre-run review; **not re-observed** in the home page HTML fetched for this report (`curl -sL https://stratallm.org/`): **unverified** today |
| Specs contradicting the repo | reported by the pre-run review (e.g. its speed tables, "25 - 38 tok/s", do not match the release notes); not itemised: **unverified** in detail |

Consequence: nothing from stratallm.org is used as a source. The repo and its
release notes are.

### 2.2 The repository

| Fact | Evidence |
|---|---|
| MIT licence | `LICENSE` line 1, `gh api repos/Niko1221/Strata --jq .license.spdx_id` = `MIT` |
| Created 2026-09-24 16:40:20Z | `gh api repos/Niko1221/Strata --jq .created_at` |
| 8,487 commits at the pin | `gh api -i 'repos/Niko1221/Strata/commits?per_page=1&sha=6a6db7ff…'`, `Link: …page=8487>; rel="last"` |
| 7,242 by the owner | `gh api repos/Niko1221/Strata/contributors` (Niko1221 7242; next: 35, 29, 23, 20) |
| 74 releases | `gh api repos/Niko1221/Strata/releases --paginate --jq '.[].tag_name' \| wc -l` |
| Alpha stability on Inkling | open issues below |

Open Inkling issues at review time (all `[open]`, `gh api repos/Niko1221/Strata/issues/N`):

| # | Opened (Z) | Title (abridged) |
|---|---|---|
| 6779 | 11-23 06:40 | RTX 5090: 16 concurrent requests with `"parallel": 64` stall at 1-10 tokens forever |
| 6605 | 11-22 15:05 | CUDA, RTX 5090: prefill hangs for prompts above ~20-27k tokens |
| 6598 | 11-22 14:50 | serve via `server.py`: SIGSEGV on any prompt above ~40 tokens (`--bench` is fine) |
| 6588 | 11-22 13:48 | `./setup.sh --build` compiles locally but still downloads the prebuilt `strata-inkling` |
| 6612 | 11-22 15:57 | replies start with a thinking span that the server discards |
| 6682 | 11-22 22:07 | serve drops the reasoning channel: empty content when the budget ends inside it |
| 6734 | 11-23 02:38 | `reasoning_budget_tokens` never enforced |

We hit neither #6605 (our 29,208-token prefill ran, [§5.3](#53-prefill)) nor #6598
(our serve answered every prompt) at this pin. #6588 is a reason we built with
our own `cmake` call, not `setup.sh`.

### 2.3 The code

- **No checked-in executables, no obfuscation, no credential or SSH access,
  no networking in the engine** (pre-run review). The project states the
  last one itself: `docs/SAFETY.md`, "The engine itself has no network code.
  The part of Strata that runs the model (`src/`, `include/`, `sycl/`,
  `metal/`) contains no networking."
- **Documented, opt-in network uses** (`docs/SAFETY.md` table): MCP servers
  you add, a preview pane, share links, and building from source. The one
  default connection is a daily update-feed read by the launch monitor;
  `STRATA_UPDATE_CHECK=0` turns it off (`terminal/strata_control/engine_update.py:65`,
  `CHECK_ENV = "STRATA_UPDATE_CHECK"`). We set it in every container anyway,
  and every container also ran with `--network none` or on a bridge with no
  masquerade ([§4](#4-the-isolation-actually-used)).

### 2.4 Why not the release binaries, images or installer

- **Release assets have no provenance.** They are uploaded by the owner's
  account (`gh api repos/Niko1221/Strata/releases/tags/v0.2.20 --jq
  '[.assets[].uploader.login]|unique'` = `["Niko1221"]`), not built by a CI
  job we can read.
- **The installer verifies itself circularly.** `README.md:118` is `curl …
  /releases/latest/download/install.sh -o install.sh && sh install.sh`. In
  `tools/packaging/install.sh` the archive's sha256 is checked against
  `install-manifest.txt` from the same release folder (lines 136-146), and
  the "signature" step runs the binary it just unpacked: `"$dest.incoming/bin/strata"
  update --check --no-engine --feed "$FEED"` (line 169). A tampered release
  would vouch for itself.
- **`setup.sh` runs sudo.** `setup.sh:64`: `sudo pacman -S --noconfirm python python-pip`.

**Verdict:** build from source at a pinned, reviewed commit, offline, in a
locked-down container, never as root, with read-only weights.

## 3. The build

**Pin.** `6a6db7ff58c283cf28d2b913404b5fa31a767eaa` (2026-11-23 09:17:47Z,
"README: Linger first, Olares second in the sponsor row"). It was `main`'s
HEAD at review time and still was when checked for this report (`gh api
repos/Niko1221/Strata/commits/main`). It is 16 commits after the v0.2.20 tag
(`git describe --tags` = `v0.2.20-16-g6a6db7f`); the 16 (`git log --oneline
v0.2.20..6a6db7ff`) are five "Pass 2 verification and fixes" merges (engine,
docker, GUI, TUI/CLI, serve) with their fix commits, among them `759ccf6`
"fix(engine): harden … inkling2 Metal/HIP/SYCL/CUDA guards, GGUF layout" and
`6bd3485` (Inkling serve fixes for #6675/#6680/#6682/#6686), two test/tool
fixes, and four README/docs commits. None of them fixes the §7 bug.

**Image.** `ob-strata-env:2026-11-23` (image id `sha256:0c44ed984067fe0c…`,
created 17:31:36), from `build/env/Dockerfile`:

```dockerfile
FROM nvidia/cuda:13.0.2-devel-ubuntu24.04@sha256:5dc1bca23d05bd37b011be68ec470c03b403a5da07ec3a86e41af9470e9d0cc6
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake ninja-build git ca-certificates libgomp1 libatomic1 \
      python3 python3-jinja2 python3-regex python3-yaml python3-numpy python3-requests \
      python3-psutil python3-pil python3-tqdm python3-markupsafe libcurl4-openssl-dev libssl-dev
```

The network was used only for this image build (base image + apt). Every
compile ran with `--network none`, so CMake could fetch nothing.

**Strata** (`scripts/build-strata.sh`; log `logs/build-strata.log`):

```bash
test "$(git -C "$SRC" rev-parse HEAD)" = 6a6db7ff58c283cf28d2b913404b5fa31a767eaa
test -z "$(git -C "$SRC" status --porcelain)"
docker run --rm --network none --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
  -v "$SRC":/src:ro -v "$OUT":/out -e HOME=/tmp -w /out ob-strata-env:2026-11-23 bash -c '
    cmake -G Ninja -S /src/engine -B /out/build -DCMAKE_BUILD_TYPE=Release \
      -DSTRATA_ENABLE_CUDA=ON -DSTRATA_BUILD_TESTS=OFF -DSTRATA_BUILD_HY4=OFF \
      -DSTRATA_BUILD_INKLING=ON -DSTRATA_BUILD_INKLING2=ON -DSTRATA_NATIVE_EXPERTS=ON \
      -DSTRATA_PORTABLE=ON -DSTRATA_Q6K_EXPERTS=ON -DCMAKE_CUDA_ARCHITECTURES=120
    cmake --build /out/build --target strata-inkling -j 24'
```

nvcc 13.0.88 (`Build cuda_13.0.r13.0/compiler.36424714_0`), 40 ninja steps.

| Binary | sha256 | Built |
|---|---|---|
| `build/strata-out/build/src/inkling2/strata-inkling` (pin, unmodified) | `11df106607e0034604233c0de0df3ed03bb213fd540d4de52b2e31e533518825` | 17:32 (`results/strata-inkling.sha256`) |
| `build/strata-out-patched/…/strata-inkling` (pin + the [§7](#7-the-bug-dspark-on-cuda-cannot-find-its-tensors) patch) | `cddb89e00bb2d8d218931a7c0322617445f3ceb7a1b2bd742315bff2b77dcc4c` | 18:19 (`results/strata-inkling-patched.sha256`, `scripts/build-strata-patched.sh`) |

**llama.cpp baselines** (`scripts/build-llama.sh`, same image, `--network
none`, `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120a-real -DLLAMA_CURL=OFF
-DGGML_NATIVE=ON -DBUILD_SHARED_LIBS=OFF`):

| Tree | Commit | Why | `llama-server` sha256 |
|---|---|---|---|
| `llama-mainline` | `af5c58347fdbc2cd19e65b278e81c8f528f8a175` (2026-11-23 17:12 PST, `b12662-17-gaf5c58347`) | mainline with `inkling2_loop` (#32694, #32704 per the script's comment) | `68acd04d66268f39…` |
| `llama-dspark` | `e4b1cb5318f1ac55d148be228c6347c6a94cc4c6` (2026-11-22, "dspark : fix Inkling2 draft state width, reseeding and draw order") | the head of PR #32218 ("feat: Inkling 2 DSpark support", open) that unsloth's mainline-layout drafter targets | `6ee695f2fd009f32…` |

PR #32218's head has since moved to `9f57b445` (`gh api
repos/ggml-org/llama.cpp/pulls/32218`, checked for this report); our numbers
are for `e4b1cb53`.

## 4. The isolation actually used

All containers (`scripts/strata-run.sh`, `strata-serve.sh`, `llama-serve.sh`):

```text
--read-only --tmpfs /tmp:rw,size=8g --user 1000:1000 --cap-drop ALL
--security-opt no-new-privileges --ulimit memlock=-1
-e STRATA_UPDATE_CHECK=0 -e HOME=/tmp
weights and binaries mounted :ro
```

- `--bench` and `--dump-ref` runs: `--network none`.
- Servers: a dedicated bridge `strata-bench-net` created with
  `com.docker.network.bridge.enable_ip_masquerade=false` (no NAT: no TCP
  connection out, re-tested in [§15.4](#154-the-running-isolation); **but
  public DNS names still resolve** through Docker's embedded resolver, a
  one-way channel this report first claimed was closed),
  published only on `127.0.0.1:8097` (Strata) / `127.0.0.1:8098` (llama.cpp).
  Strata's `server.py` ran `--offline --api-key <scratch/strata.key>`.

**The GPU was passed by hand.** At the time the rig's Docker had no NVIDIA
runtime or CDI spec (`nvidia-container-toolkit` not installed; installing it
needs Max's sudo), so `--gpus` failed. `scripts/gpu-args.sh` did by hand what
the toolkit does for one GPU: the device nodes and the host driver's
user-space libraries, read-only:

```bash
DRV=615.78.08
GPU_ARGS=(--device /dev/nvidia0 --device /dev/nvidiactl --device /dev/nvidia-uvm --device /dev/nvidia-uvm-tools
  -v /usr/lib/libcuda.so.$DRV:/usr/local/nvidia/lib64/libcuda.so.1:ro
  -v /usr/lib/libcuda.so.$DRV:/usr/local/nvidia/lib64/libcuda.so:ro
  -v /usr/lib/libnvidia-ptxjitcompiler.so.$DRV:/usr/local/nvidia/lib64/libnvidia-ptxjitcompiler.so.1:ro
  -v /usr/lib/libnvidia-nvvm.so.$DRV:/usr/local/nvidia/lib64/libnvidia-nvvm.so.4:ro
  -v /usr/lib/libnvidia-gpucomp.so.$DRV:/usr/local/nvidia/lib64/libnvidia-gpucomp.so.$DRV:ro
  -v /usr/lib/libnvidia-ml.so.$DRV:/usr/local/nvidia/lib64/libnvidia-ml.so.1:ro
  -v /usr/bin/nvidia-smi:/usr/local/bin/nvidia-smi:ro
  -e LD_LIBRARY_PATH=/usr/local/nvidia/lib64:/usr/local/cuda/lib64 -e NVIDIA_VISIBLE_DEVICES=0)
```

The trade-off: this is tied to the exact driver version (a driver update
breaks the mounts), and it bypasses nothing the toolkit would add for
security; every other hardening flag above still applied. Max installed the
toolkit after this bench, at 19:56:48 (pacman log), and Docker restarted at
19:57:17; from then on `--gpus` works on the rig
([INSTALL.md](../INSTALL.md#gpu-in-docker-the-nvidia-container-toolkit)). The bench itself (17:29-19:14)
ran entirely on the hand-passed devices.

**The rig's stack during the bench.** The default Qwen3.8 serve held 26.5 GB
of the GPU (`logs/pre-state.txt`, 17:35), so the stack was stopped
(`./stop.sh`, 17:46, `logs/stop.log`) and restored at the end (`./start.sh
-d`, 19:13:57, `logs/restore-start.log`; verified 19:14:03 in
`logs/restore-verify.txt`: gate `/v1/models` 200 listing "Qwen3.8 27B
Uncensored MTP Q5", a gate chat answered "ready", llama-server back at
26,520 MiB). The gate answered nothing from 17:46 to 19:14, as planned.

## 5. Results

### 5.1 Weights

All read-only from `/mnt/9100s4tb/weights/` (`scripts/hash-weights.sh`,
`results/weights.sha256`). The unsloth folder is HF revision
`8078d149286eec7193795413b9e0643da6d11fa1` (its `.cache/huggingface`
metadata).

| Mount | File | Bytes | sha256 | Used by |
|---|---|---:|---|---|
| `/models/ud-q4kxl.gguf` | `unsloth-Inkling-2-Mini-GGUF/Old_Support/Inkling-2-Mini-UD-Q4_K_XL.gguf_file` | 17,629,759,456 | `e62d463c135ffe28…` | Strata (its pinned hash) |
| `/models/ud-q4kxl-mainline.gguf` | `…/Inkling-2-Mini-UD-Q4_K_XL.gguf` (= `Mainline_Support/…gguf_file`) | 17,629,760,736 | `e64276656f072e9b…` | llama.cpp |
| `/models/dspark.gguf` | `…/Old_Support/dspark-Inkling-2-Mini-Q8_0.gguf_file` (= `dspark-…-Q8_0_Old.gguf`) | 402,278,560 | `2ec78510da837e8f…` | Strata's drafter (its pinned hash) |
| `/models/dspark-mainline.gguf` | `…/Mainline_Support/dspark-Inkling-2-Mini-Q8_0.gguf_file` (= root `dspark-…-Q8_0.gguf`) | 503,889,952 | `3da00cf7c31bbe56…` | llama.cpp PR #32218's drafter |
| `/models/abl16.gguf` | `orcarouter-Inkling-2-Mini-abliterated-GGUF/Inkling-2-Mini-abliterated-Mix-16G.gguf` | | `0a8784aa059d6dba…` | Strata, second file |

[§10](#10-corrections-made-today)'s layout note explains the two UD-Q4_K_XL files.

### 5.2 Single-stream and four-stream decode (real prompts)

**Method** (`scripts/bench.py decode`): 3 code prompts and 3 prose prompts
(`data/prompts.json`, sha256 `6b1a35de…`; e.g. "a thread-safe LRU cache with
TTL … and a full unittest suite", "an essay of about 900 words on the 1918
influenza pandemic"), one warm-up each, then 5 runs per prompt, greedy
(`temperature 0, top_p 1`), `max_tokens 512` (every run produced 512). Decode
tok/s per stream = `(completion_tokens - 1) / (t_last_chunk - t_first_chunk)`
from the client's stream; the 4-stream aggregate = all tokens / the wall of
the decode window. Each cell is the median of 15 values (3 prompts x 5 runs).
Sources: `results/decode/*.json`, summarised in `results/SUMMARY.txt`.

**Thinking, and why llama.cpp has two rows.** Strata forces Inkling's
thinking off ([§12](#12-verdict-and-open-questions)). llama.cpp's chat endpoint leaves the template default
(thinking on): its chat-mode rows spent all 512 tokens in
`reasoning_content` (the saved `llama-off-s1.json.code0.txt` content is 0
bytes). So the like-for-like llama.cpp rows are the **raw** ones
(`bench.py --raw`): the prompt rendered by `/apply-template` with
`reasoning_effort: none, enable_thinking: false`, plus
`<|message_model|><|content_text|>`, sent to `/completion` with `top_k 1`,
which is what Strata's `inkling_dialect` renders. The chat-mode rows are kept
for the record.

| Engine, mode | File | Code tok/s | Prose tok/s | 4-stream total (code / prose) |
|---|---|---:|---:|---:|
| **Strata**, no drafter | `strata-off-s1.json` | **138.6** | **138.7** | |
| **Strata**, no drafter, 4 streams | `strata-off-s4.json` | 80.5 each | 81.7 each | **319.5 / 323.3** |
| **Strata**, DSpark (patched build) | `strata-dspark-s1.json` | **198.5** | **132.6** | |
| llama.cpp `af5c5834`, raw | `llama-off-s1-raw.json` | 96.9 | 96.9 | |
| llama.cpp `af5c5834`, raw, 4 streams | `llama-off-s4-raw.json` | 59.6 each | 60.0 each | 239.0 / 239.9 |
| llama.cpp PR #32218 `e4b1cb53`, DSpark n_max 5, raw | `llama-dspark5-s1-raw.json` | 170.2 | 78.5 | |
| llama.cpp `af5c5834`, chat (thinking on) | `llama-off-s1.json` | 96.6 | 96.8 | |
| llama.cpp `af5c5834`, chat, 4 streams | `llama-off-s4.json` | 60.0 each | 59.6 each | 240.3 / 238.9 |
| llama.cpp PR #32218, DSpark, chat (thinking on) | `llama-dspark5-s1.json` | 131.1 | 97.1 | |
| **Strata**, Mix-16G (OrcaRouter abliterated), no drafter | `strata-abl16-off-s1.json` | 134.6 | 134.8 | |
| **Strata**, Mix-16G, DSpark (patched) | `strata-abl16-dspark-s1.json` | 191.6 | 123.2 | |

Per-prompt medians show what a drafter does (code prompts 0/1/2, prose
0/1/2): Strata DSpark code 229.9 / 168.3 / 198.5, prose 132.6 / 126.8 /
145.0; llama.cpp DSpark raw code 185.1 / 106.3 / 170.2, prose 78.5 / 76.2 /
101.9. Without a drafter every prompt sits within 1 tok/s of the median.

Readings:

- **No drafter, Strata is 1.43x llama.cpp** (138.6 / 96.9) on the same
  tensors, and 1.34x at four streams (319.5 / 239.0).
- **Drafting helps code and hurts prose, on both engines.** Strata DSpark:
  +43% code, -4% prose. llama.cpp DSpark: +76% code, -19% prose. Strata's
  server counted 8,437 of 25,911 drafted tokens accepted over the DSpark run
  (32.6%, `results/decode/strata-dspark-s1.metrics.txt`, 36 requests
  including warm-ups).
- **The abliterated Mix-16G behaves like the stock file** (134.6 vs 138.6
  without a drafter); Strata loaded it although its `inkling_family.py`
  does not list it.
- Which build served the no-drafter rows: per the bench agent, the
  unmodified pin build; the serve logs do not record the binary path. It
  does not matter for `--draft off`: the patch only changes drafter tensor
  names, and `--bench` measured 133.8 (pin) against 134.1 (patched) for the
  same `--draft off` row.

### 5.3 Prefill

`bench.py prefill`: a prompt cut from `data/longtext.txt` (OpenBeast's own
docs concatenated, sha256 `60e22b42…`) at 2.83 chars per token, a unique
`[run N <ns>]` prefix per run to defeat prompt caches, `max_tokens 1`;
prefill tok/s = `usage.prompt_tokens / TTFT`; median of 5.

| Prompt tokens | Strata tok/s (TTFT) | llama.cpp tok/s (TTFT) | Ratio |
|---:|---:|---:|---:|
| 4,141 | **3,603.8** (1.15 s) | 2,409.0 (1.72 s) | 1.50x |
| 15,027 | **3,600.2** (4.17 s) | 2,054.4 (7.31 s) | 1.75x |
| 29,208 | **3,385.1** (8.63 s) | 1,678.3 (17.40 s) | 2.02x |

Files: `results/prefill/strata-4k16k.json`, `strata-32k.json`,
`llama-4k16k32k.json`. For reference, `llama-bench` on the same mainline
build (`results/llama-bench.md`): pp512 3,370.26, pp4096 2,660.80, pp16384
2,099.10, tg128 98.47 tok/s (it labels the file "Q8_0 16.41 GiB", its
dominant tensor type; it is the 17.63 GB UD-Q4_K_XL).

### 5.4 Strata's own `--bench`

`scripts/strata-benchmode.sh`: synthetic token ids, greedy, a fresh process
per run, 5 runs per row, `--network none`. Medians (`results/strata-benchmode.txt`,
`results/strata-benchmode-patched.txt`):

| Build, drafter | Parallel | Prompt / gen | Prefill tok/s | Decode tok/s, total (per slot) | KV |
|---|---:|---|---:|---:|---|
| pin, off | 1 | 64 / 32 | 2,050.4 | 140.2 | fp16 |
| pin, off | 1 | 512 / 512 | 4,467.1 | 133.8 | fp16 |
| pin, off | 4 | 512 / 256 | 4,451.7 | 321.3 (80.3) | fp16 |
| pin, off | 8 | 512 / 256 | 4,455.6 | 478.7 (59.8) | fp16 |
| pin, off | 16 | 512 / 256 | 4,452.4 | 676.5 (42.3) | fp8 |
| pin, off | 32 | 512 / 256 | 4,402.4 | 895.7 (28.0) | fp8 |
| pin, off | 64 | 512 / 256 | 4,411.5 | 1,137.7 (17.8) | fp8 |
| pin, off | 64 | 64 / 32 | 1,856.2 | 1,193.9 (18.7) | fp8 |
| pin, off | 1 | 4,096 / 16 | 4,152.1 | 126.6 | fp16 |
| pin, off | 1 | 16,384 / 16 | 3,841.3 | 115.3 | fp16 |
| patched, DSpark | 1 | 64 / 32 | 2,054.6 | 102.0 | fp16 |
| patched, DSpark | 1 | 512 / 512 | 4,462.1 | **398.6** | fp16 |
| patched, off | 1 | 512 / 512 | 4,462.1 | 134.1 | fp16 |

The script also asked for `--draft dspark` rows on the pin build; there are
none in the output, because every one exited at startup ([§7](#7-the-bug-dspark-on-cuda-cannot-find-its-tensors)). Strata
switched the KV cache to fp8 by itself from 16 slots up.

**`--bench`'s DSpark figure does not predict chat speed.** 398.6 tok/s on
synthetic ids is 2.97x the no-drafter row; on real prompts DSpark gave 1.43x
on code and 0.96x on prose ([§5.2](#52-single-stream-and-four-stream-decode-real-prompts)). Synthetic ids are easy to draft.

## 6. Correctness, needle and conformance

**Top-1 agreement** (`scripts/top1_gen.py`, `top1_cmp.py`; result
`results/correctness/top1_result.json`): llama.cpp mainline generated 340
greedy tokens (`ignore_eos`) for code prompt 0 and prose prompts 1 and 2;
Strata re-read prompt + continuation teacher-forced (`--dump-ref`) and its
argmax at each generated position was compared with llama.cpp's token.

| | Positions | Agree | Top-1 |
|---|---:|---:|---:|
| code 0 | 340 | 334 | 98.24% |
| prose 1 | 340 | 324 | 95.29% |
| prose 2 | 340 | 329 | 96.76% |
| **all** | **1,020** | **987** | **96.76%** |

Strata's perplexity on llama.cpp's greedy continuation: 1.360. On the 33
disagreements llama.cpp's token ranked 2nd (median) and at worst 4th under
Strata. That is near-ties resolving differently in two kernel sets, not a
broken model. It is not a quality score: the reference is llama.cpp, not the
original weights.

**Needle** (`bench.py needle`): "The secret passphrase for the archive vault
is cobalt-heron-4471" planted at 40% depth of 15,042 prompt tokens.

| Engine | Answer | OK | TTFT |
|---|---|---|---:|
| Strata | `cobalt-heron-4471` | yes | 4.18 s |
| llama.cpp | `cobalt-heron-4471` (after reasoning about whether to reveal it) | yes | 0.10 s* |

\* llama.cpp's 0.10 s is not a prefill measurement (15K tokens cannot
prefill in 0.1 s at 2,054 tok/s); most likely a prompt-cache hit from an
earlier request. Unexplained, ignore it.

**Conformance** (`scripts/backends/conformance.sh` against each server;
`results/conformance-{strata,llama}/`): **both PASS** (models, chat, stream,
tools, tool_result, parallel tool calls, max_tokens, 4-way concurrency).
Differences: Strata's `reasoning` check is UNKNOWN (no reasoning ever, thinking
forced off) where llama.cpp's is PASS; Strata streams each tool call in one
chunk (2 chunks for 2 calls) where llama.cpp streams 34; Strata exposes
`max_model_len 40960`. The extra streamed-tools probe (`scripts/stream_tools.py`,
`stream-tools.json`): both return two well-formed `get_weather` calls
(Paris, Tokyo, `unit: c`) with `finish_reason=tool_calls` and no content
leak; on the bat-and-ball prompt with `reasoning_effort: high`, Strata gives
0 reasoning chars (the request is overridden) and llama.cpp 505.

## 7. The bug: DSpark on CUDA cannot find its tensors

**Symptom.** Any `strata-inkling --draft dspark` run with a GGUF model and
the GGUF drafter exits 2 at startup. Upstream's reports quote the message:
`strata-inkling: missing tensor 'dspark.layers.0.self_attn.o_proj.weight'`.
Our own stderr was not kept (the bench script filtered to `[bench]` lines);
the evidence on our side is the missing pin-build DSpark rows in
`results/strata-benchmode.txt` and the source below.

**Cause** (read at `6a6db7ff`):

- The GGUF loader strips the prefix when it maps drafter names:
  `engine/src/inkling2/ink_gguf_loader.h:776`, `if (n.rfind("dspark.", 0) == 0) return n.substr(7);`,
  so the manifest stores `layers.N.self_attn.o_proj.weight`.
- The CPU path tolerates either form: `ink_manifest.h:209-214`,
  `find_dspark` tries `n`, then `"dspark." + n`.
- The CUDA path copies the keys verbatim, `ink_runner.cuh:933-940`,
  `man.dspark[kv.first] = std::move(ti);`, and then looks them up with the
  prefix: `cuda/ink_dspark.cuh:1264`, `const std::string p = "dspark.layers." + std::to_string(il);`.

**The patch** (`results/strata-dspark-cuda-names.patch`, applied to a copy of
the source in `build/strata-src-patched`, built as `cddb89e0…`):

```diff
--- i/engine/src/inkling2/cuda/ink_runner.cuh
+++ w/engine/src/inkling2/cuda/ink_runner.cuh
@@ -937,7 +937,7 @@ struct InkCudaModel {
             ti.shape = kv.second.shape;
             ti.off = kv.second.off;
             ti.bytes = kv.second.bytes;
-            man.dspark[kv.first] = std::move(ti);
+            man.dspark[kv.first.rfind("dspark.", 0) == 0 ? kv.first : "dspark." + kv.first] = std::move(ti);  // bench patch: GGUF/safetensors drafter names are un-prefixed (CPU manifest falls back the same way)
         }
```

**Upstream status: already reported; no new issue needed.** Searching
(`gh search issues --repo Niko1221/Strata dspark`) finds it open as
**#6762** (2026-11-23 04:52Z, "strata-inkling CUDA: GGUF model + DSpark GGUF
exits 2 … (CUDA twin of #6717)", which names the same three source lines),
with duplicates #6784, #6852 and #6876 and the SYCL twin #6717. A comment on
#6762 already proposes the identical prefix fix, tested on an RTX 5060 Ti
(sm_120). v0.2.20's release notes say "fixed DSpark speculative drafter"
for GGUF_RAW, yet the bug is present at `6a6db7ff`, 16 commits later.

The upstream action left for us (optional, Max's call, not done): add a
confirmation comment on #6762 with our sm_120 RTX 5090 result and the
measured effect (DSpark 198.5 code / 132.6 prose against 138.6 / 138.7).
Our patch is a bench patch: it is not installed anywhere and does not ship.

## 8. The bandwidth arithmetic

Single-stream decode without a drafter must read every weight it uses once
per token, so `bandwidth / bytes per token` bounds it.

Inputs, from the GGUF's own tensor table and metadata (`scripts/gguf_bytes.py`
run on the root UD-Q4_K_XL, `results/gguf-bytes.txt`):

- 24 blocks, 128 experts, 8 used per token; the loop runs blocks 6 to 17
  three times (`loop.start 6`, `loop.end 18`, `loop.count 3`), so a token
  makes 24 + 2 x 12 = **48 block visits**. Strata's v0.2.19 notes say the
  same: "5.4B looped-active across 48 virtual visits".
- Bytes: experts 15.798 GB, dense per-layer tensors 0.836 GB, output 0.492 GB,
  embeddings 0.492 GB (a row lookup per token, negligible); total 17.618 GB.

Per token:

| Part | Arithmetic | GB |
|---|---|---:|
| routed experts | 15.798 x 8/128 x 48/24 | 1.975 |
| dense per-layer | 0.836 x 48/24 | 1.672 |
| output head | 0.492 | 0.492 |
| **total** | | **4.14** |

At the RTX 5090's 1,792 GB/s (NVIDIA's published figure: 512-bit GDDR7 at 28
Gbps; not re-read today) that is **433 tok/s**, quoted as "~435" in the
session. Assumptions: perfect streaming efficiency, no reuse of an expert
across the three loop visits, dense bytes spread evenly over the blocks, KV
reads ignored. Real kernels reach a fraction of it: Strata's 138.6 is 32% of
the cap, llama.cpp's 96.9 is 22%.

What it implies:

- **4,087 can never be single-stream plain decode** of this model on this
  card. It needs batching (one weight read shared by many requests), which is
  what the 64-request aggregate is.
- A drafter can exceed the cap (one weight read verifies several tokens), so
  754 on code is not impossible in principle; it needs high acceptance and a
  smaller file.
- **The earlier "500-800 tok/s" cap was too loose.** It used 3.4B active
  parameters at 4 bits and ignored the loop's 48 visits (5.4B looped-active).
- A smaller pack reads proportionally less. Scaling by file size only (an
  estimate, not a measurement): SQ-10 (10.47 GB) would put the plain-decode cap
  near 4.14 x 10.47/17.63 = 2.46 GB per token, about 730 tok/s. That is why
  §11 measures the native packs.

## 9. Claim provenance: where 4,087 and 754 / 440 come from

**Strata's own v0.2.19 release notes** (`gh api
repos/Niko1221/Strata/releases/tags/v0.2.19 --jq .body`, published
2026-11-22 11:38:49Z), line 25, verbatim:

> **Sparse-MoE Bayes-Optimal `W=4` On-GPU Markov Beam Tree `DSpark` +
> Zero-Logits-D2H serving (`0.00000` KLD):** reaches up to **250.3 tok/s**
> solo (`198.9 tok/s` on lossless `SQ-20`), **11,306 tok/s** prompt prefill,
> and **4,089.7 tok/s** total output across **64 concurrent requests** on RTX
> 5090; …

and lines 8-12: three Strata Quantization packs on
[`strata-engine/Inkling-2-Mini-Strata`](https://huggingface.co/strata-engine/Inkling-2-Mini-Strata),
SQ-10 (10.47 GB), SQ-15 (14.35 GB, "96.68% top-1 agreement with the original
weights") and SQ-20 (19.35 GB, "bit-exact lossless"), with "built-in `W=4`
Multi-Branch On-GPU Markov Beam Tree `DSpark` speculative decoding". The pack
hashes are pinned in `terminal/strata_control/inkling_family.py`
(SQ-20 `0bbfd523…`, SQ-15 `2ea32eed…`, SQ-10 `ac4d452a…`).

So:

| Circulating claim | What the source says | Our closest measurement |
|---|---|---|
| "4,087 tok/s on a 5090" | **4,089.7 tok/s total across 64 concurrent requests** (a batched aggregate; the notes do not say which pack or whether DSpark was on for that figure) | 1,193.9 total at 64 slots, `--bench`, GGUF, no drafter (18.7 per slot) |
| single-stream | **250.3** solo (pack not named; "up to"), **198.9** on SQ-20 | **198.5** code / 132.6 prose, GGUF + DSpark (patched) |
| prefill | **11,306** tok/s | 3,603.8 (4K, real prompt); 4,467.1 (`--bench`, 512) |
| "754 code / 440 prose" | **nowhere in any official source we found** (release notes, repo, issues). Max relayed it as a Mia Labs post on X; we have not seen the post: **unverified** | 198.5 / 132.6 |
| 400+ single-stream | nowhere official | |

Differences that explain the gap between us and their 250.3: their own SQ
packs (we used unsloth's GGUF), their built-in W=4 beam-tree DSpark (we used
the GGUF drafter through a patched loader), and unknown prompts.

## 10. Corrections made today

Recorded so nobody repeats them.

1. **"4,087 has no source anywhere"** (pre-run review, 23:35Z). Wrong: the
   v0.2.19 release notes carry 4,089.7. The review's search missed it.
2. **"4,087 is a prefill figure"** (our report to Max at 03:15Z, after the
   bench, reasoning from `--bench` printing ~4,1xx prefill ppts at 4K).
   Wrong: it is the 64-request decode aggregate. Corrected at 03:41Z from
   the release notes.
3. **"Bandwidth caps single-stream decode at roughly 500-800 tok/s"**
   (pre-run review). Too loose: it ignored the loop. Corrected to ~433
   ([§8](#8-the-bandwidth-arithmetic)).
4. **"The only desktop-5090 benchmark is a PR opened today: 161 tok/s, 606 at
   32 clients, 8,713 prefill"** (pre-run review). We could not find that PR
   (`gh search prs --repo Niko1221/Strata "Inkling 5090"` returns nothing).
   **Unverified**; possibly a confusion with the Qwen3.8-Flash-Next figure
   "decode 161.6 tok/s" in the same release notes. Not used.
5. **"Report the DSpark bug upstream"** (our plan at 03:15Z). It was
   already reported, as #6762 and four duplicates ([§7](#7-the-bug-dspark-on-cuda-cannot-find-its-tensors)).
6. **The two UD-Q4_K_XL files.** Unsloth's repo carries two files under the
   one name: the root `Inkling-2-Mini-UD-Q4_K_XL.gguf` (17,629,760,736
   bytes, `e6427665…`, identical to `Mainline_Support/…gguf_file`) is the
   layout mainline llama.cpp reads; `Old_Support/…gguf_file` (17,629,759,456
   bytes, `e62d463c…`) is the older layout, and it is the one Strata pins
   (`inkling_family.py:44`, `unsloth_alternates.json` lists the same hash as
   an alternate). Likewise the drafter: Strata pins the Old_Support
   `2ec78510…` (402 MB), llama.cpp's PR reads `3da00cf7…` (504 MB). The two
   model files differ by 1,280 bytes; the bench treats them as the same
   tensors in different layouts (the comment in `scripts/llama-serve.sh`).
   That equivalence is **not independently verified** (no tensor-by-tensor
   hash comparison was run); the 96.76% top-1 agreement ([§6](#6-correctness-needle-and-conformance)) is consistent
   with it. Strata loaded the Old_Support file and reported it as
   `GGUF-NVFP4, fmt=gguf: trunk=3.93 GB, experts=3072 GPU0`
   (`logs/strata-bench-default.log`).

## 11. Pending: native SQ packs and other engines

> **Filled 2026-11-23 ~21:05 PST** from the second investigation's files on the
> rig, `~/openbeast-runs/inkling-perf-2026-11-23/` (paths below are relative
> to it): `results/SUMMARY.txt` (every median), `results/thinking/*.json`,
> `results/conformance-*/`, `scripts/`, `logs/`, `weights/*/sha256.txt`, and
> `gh api` on 2026-11-23. The heading keeps its name so existing links work.
> **Outcome: Basalt v0.5.7 with the Blackfrost "Derisked" pack serves
> Inkling 2 Mini for the v5 run, arm `I_T2`, launched 20:58:42 PST.**

### 11.1 What was compared, and how

The same probes as §5 (`scripts/bench.py`: greedy, 512 tokens, 3 code + 3
prose prompts x 5 runs, medians; prefill on the same 4,141 / 15,027 / 29,208
token prompts; the 15,042-token needle). Thinking "off" is `reasoning_effort:
none` (Inkling still writes a short thinking block); "high" is the template
default. Every engine ran in the hardened sandbox of §4 (read-only rootfs and
weights, uid 1000, `--cap-drop ALL`, no-egress bridge `strata-bench-net`,
loopback-only port, API key), from 19:56 with `--gpus` (the toolkit, §4).

| Candidate | Engine and pin | Weights (pinned, sha256-checked) |
|---|---|---|
| **Basalt** | `github.com/jesdga95/basalt` tag v0.5.7 = `400b411c58031d064812ea21a259b2381acdaa3e` (MIT), built from source offline (`scripts/build-basalt.sh`: `--network none`, `-DBASALT_MODELS=inkling2 -DCMAKE_CUDA_ARCHITECTURES=120`, nvcc 13.0.88), `bin/basalt` sha256 `5dc8a63ca0089bf8…` (`results/basalt-bin.sha256`) | stock: `jesdga/Inkling-2-Mini-Basalt` @ `c2afac3a`, `Inkling-2-Mini-NVFP4-MX8.basalt`, `28ec7072…`; derisked: converted below |
| **vLLM** | official image `vllm/vllm-openai@sha256:a485440c…` (Inkling tag, 2026-11-20) + a one-anchor SM120 patch (`build/vllm-sm120/patch_sm120.py`, from `github.com/mxfp0/Inkling-2-Mini-RTX-5090`, reviewed: on compute capability 12.0 it swaps `reshape_and_cache_flash` for vLLM's own Triton version; without it the DSpark draft layers hit "no kernel image"), image `ob-vllm-inkling-sm120:a485440c` | Blackfrost Derisked release format (NVFP4/MXFP8 + `mtp.safetensors`) |
| **Strata** | §3's pin `6a6db7ff`, unpatched, native packs (`scripts/strata-serve.sh`) | `strata-engine/Inkling-2-Mini-Strata` @ `04996a1e`: SQ-20 `0bbfd523…`, SQ-15 `2ea32eed…`, SQ-10 `ac4d452a…` (= the hashes Strata pins) |

**The Derisked pack.** `Blackfrost-AI/Inkling-2-Mini-Derisked` (HF revision
`31ec2eddecfdf93c8626d9e4dfc0a2aca6070900`, from its `.cache/huggingface`
metadata; in `/mnt/9100s4tb/weights/`), a "behaviorally modified derivative"
of `thinkingmachines/Inkling-2-Mini` (its README), shipped with its DWM edit
recipe. Basalt's own converter (`tools/convert/convert.py`, the recipe of the
official pack per Basalt's `docs/models/inkling2.md`) turned it into
`weights/basalt-blackfrost-derisked/Inkling-2-Mini-Derisked-NVFP4-MX8.basalt`,
offline, CPU only, in the sandbox (`scripts/convert-blackfrost.sh`,
`logs/convert-blackfrost.log`: 10 sections, 18.260 GiB, 23.7 s), sha256
**`d2b01bcc1fd2dc51cbcdaecb512c56d7c5db75181fee2d9b6c944e76e217a427`**
(`weights/basalt-blackfrost-derisked/sha256.txt`).

### 11.2 Results

Decode, single stream (tok/s, code / prose; `results/SUMMARY.txt`):

| Setup | Thinking off | Thinking high |
|---|---:|---:|
| **Basalt v0.5.7, derisked pack, DSpark** | **403 / 356** | |
| Basalt v0.5.7, stock pack, DSpark | 376 / 307 | 417 / 309 |
| Basalt on a `--parallel 4` server, one request (3 runs) | 414 / 339 | |
| vLLM + SM120 patch, DSpark 7 | 269 / 161 | 271 / 189 |
| Strata SQ-20, DSpark (thinking forced off) | 201 / 131 | (n/a) |
| Strata SQ-10, DSpark (lossy pack) | 274 / 174 | (n/a) |
| §5 for reference: Strata GGUF + DSpark (patched); llama.cpp PR #32218 DSpark | 198.5 / 132.6; 170.2 / 78.5 | |

Four streams, aggregate (code / prose): **Basalt `--parallel 4` 702 / 624**,
vLLM `max-num-seqs 4` **902 / 546**, Strata SQ-20 p4 335 / 342.

Prefill (tok/s at 4K / 16K / 32K): **Basalt 18,098 / 19,338 / 17,564**; vLLM
14,530 / 14,428 / 13,117; Strata SQ-20 6,333 / 6,313 / 5,699.

The engines' own benches: `basalt-bench` (its fixtures, seeded, `-r 2`) stock
pp16384 18,436, conv 341, struct 676, prose 294; derisked pp16384 19,449,
conv 331, struct 714, prose 319. Strata `--bench` (synthetic ids): SQ-20
DSpark p1 190, off 135, p4 338, **p64 ~1,400**; SQ-10 p64 ~1,470
(`results/strata-sq-benchmode.txt`). `llama-bench` tg256 (fa on): UD-Q4_K_XL
98.9, UD-IQ3_XXS 101.9, UD-IQ2_XXS 105.3 (`results/llama-bench-lowbit.md`):
llama.cpp is engine-bound, fewer bits buy 3-6%.

Correctness against UD-Q8_K_XL (llama.cpp's KLD base, 60 x 512-token chunks of
`data/longtext.txt`; Q8_K_XL is itself a requant, so this is a near-reference,
not the original weights):

| Weights | KLD | Top-1 | PPL (base 5.915) |
|---|---:|---:|---:|
| Basalt stock pack | 0.0128 | 94.93% | 5.901 |
| **Basalt derisked pack** | **0.0263** | **92.93%** | **5.952** |
| llama UD-Q4_K_XL | 0.0162 | 94.56% | |
| llama UD-IQ3_XXS | 0.0288 | 92.77% | |
| llama UD-IQ2_XXS | 0.0751 | 88.09% | |

Strata's SQ packs were not measured here (vendor card: SQ-20 lossless, SQ-15
KLD 0.009, SQ-10 0.050: **unverified**); vLLM: needle only. The 16K needle
passed on Basalt stock, Basalt derisked, vLLM and Strata SQ-20
(`results/correctness/needle-*.json`).

Harness fitness (`scripts/backends/conformance.sh` at c=1 and c=4;
`scripts/probe_think_tools.py`, `enable_thinking` true / false / unset,
streamed and not, `results/thinking/*.json`):

| Engine | Conformance | Reasoning channel | Tool calls with thinking on |
|---|---|---|---|
| **Basalt** (stock and derisked) | PASS / PASS | `reasoning_content`, separated, every probe | yes, streamed and not |
| vLLM | PASS / PASS | in `reasoning` (conformance WARN: not the field our runner reads) | yes |
| Strata SQ-20 | PASS / PASS | none: 0 reasoning in all 12 probes (thinking forced off, §12) | yes |

### 11.3 Claims against evidence

| Claim (source) | Evidence here | Reading |
|---|---|---|
| Strata "4,089.7 tok/s total output across 64 concurrent requests on RTX 5090" (v0.2.19 release notes, §9) | `--bench` SQ-20 p64 ~1,400, SQ-10 p64 ~1,470 (synthetic ids, the most favourable case) | **Not reproduced**: about a third. Their prompts, pack and settings for the figure are not stated |
| Strata "250.3 tok/s solo (198.9 on lossless SQ-20)" (same notes) | SQ-20 DSpark `--bench` 190; chat 201 code / 131 prose; SQ-10 chat 274 / 174 | SQ-20 roughly reproduced on code; 250 plausible on SQ-10 code only |
| v0.2.20 "fixed DSpark speculative drafter" | native SQ packs with the built-in drafter run unpatched at `6a6db7ff`; the GGUF-drafter path of §7 still needs the patch | the fix is for the native packs, not the CUDA GGUF path (#6762) |
| "754 code / 440 prose" (relayed as a Mia Labs post, §9) | Basalt derisked 403 / 356, stock 376 / 307 (thinking high: 417 / 309) | No engine reaches it. Basalt is the closest, still below §8's ~433 no-drafter bandwidth cap even with its drafter (§8's cap is for the 4.14 GB/token GGUF; the MX8 pack's bytes per token were not computed) |
| Strata SQ-20 "lossless", SQ-15 KLD 0.009, SQ-10 0.050 (vendor card) | not measured | **unverified** |
| Basalt's conversion is "the same recipe as the official pack" (its docs) | derisked pack speed within 7-16% of stock and KLD 0.0263 vs 0.0128 | consistent: the extra divergence is what a behavioural edit should add; not separately attributed |
| Fewer bits make llama.cpp faster (common advice) | tg256 98.9 → 105.3 from Q4_K_XL to IQ2_XXS, KLD 0.016 → 0.075 | true but small: the engine, not bandwidth, bounds llama.cpp here |

Supply chain, Basalt: MIT, created 2026-10-06, 1,950 stars, top contributors
`jesdga95` (2,351 commits) and `Niko1221` (534, Strata's author), v0.5.7
published 2026-11-24 02:18Z (`gh api repos/jesdga95/basalt`, `…/contributors`,
`…/releases`); the tag resolves to `400b411c` (`gh api
repos/jesdga95/basalt/commits/v0.5.7`); `main` has moved since (`db8c8948` at
03:23Z). Built from that source, offline; served with no TCP egress. **No written
supply-chain review of Basalt's code was found in the investigation folder**
(§11's brief required one): the mitigations were the source build at a tag and
the sandbox, not a read of the code. **Done later the same night: [§15](#15-basalt-security-and-supply-chain-review).**

### 11.4 The engine choice, and why

**Basalt v0.5.7 with the derisked pack, `scripts/inkling-v5.sh start
derisked`** = `basalt serve --parallel 1 --max-context 262144 --kv fp8`, DSpark
at the pack default, `--answer-room auto` (the default), alias
`inkling-2-mini` on `127.0.0.1:8099`, Bearer key in `scratch/basalt.key`.

1. **It drives T2.** Thinking on returns `reasoning_content` and tool calls,
   streamed and not; conformance PASS. Strata cannot (thinking forced off).
   vLLM can, but puts reasoning in `reasoning`, a WARN for our runner.
2. **Fastest single stream**, which is what `I_T2` uses (one stream, like
   Q_T2): 403 / 356 against vLLM's 269 / 161 and Strata's 201 / 131; and the
   fastest prefill (18-19K tok/s), which matters for long agent contexts.
   vLLM wins only the four-stream code aggregate (902 vs 702), which this arm
   does not use.
3. **The derisked pack, per policy.** OpenBeast prefers uncensored weights
   (honest, neutral answers for professionals). The derisked pack is also
   faster than stock here (403 / 356 against 376 / 307), at a KLD of 0.0263
   (between UD-Q4_K_XL and UD-IQ3_XXS). The stock Basalt pack is the control
   alternative, not run.
4. **`--parallel 1 --max-context 262144 --kv fp8`**: one stream at the full
   context, as Q_T2's 262K one-slot serve; fp8 KV to fit 262K on 32 GB.

**A known serve behaviour that shapes the row.** `--answer-room auto`
reserves `min(max(512, cap // 4), cap // 2)` output tokens for the answer
(Basalt `docs/configuration/sampling.md`, "Thinking"). T2 caps a turn at
8,192 tokens, so Basalt allows **6,144 thinking tokens**, then closes the
thinking with the template's own close. **Correction (§15.6):** the close
does not force an answer on Inkling. Basalt writes only `<|end_message|>`,
and the model may open a second thinking block, which then runs to the
cap with no answer (upstream #588, open at the pin). On
llama.cpp (Q_T2) a turn could think up to the full 8,192. This is a property
of the serve, recorded in the arm's note, not a harness change.

**Alternatives, and why they lost.** vLLM: slower single stream, reasoning in
the wrong field, a patched image. Strata: no thinking. llama.cpp (§5): 97
tok/s without a drafter. The stock pack: kept as the control. A "squid5090"
candidate was mentioned but **not run**.

## 12. Verdict and open questions

**Not adopted (2026-11-23).** Reasons, in order of weight:

1. **Thinking is forced off for Inkling.** `terminal/strata_control/inkling_family.py`:
   `HAS_THINKING = False`, `REASONING_EFFORTS = ("none",)`;
   `terminal/serve/inkling_dialect.py:399-418`: "Inkling-2-Mini has NO thinking
   mode", `normalize_effort()` always returns `"none"`, and `template_kwargs()`
   always sets `enable_thinking=False, reasoning_effort="none"`. Our measured
   best agent setup on every model is thinking-first (T2). An Inkling arm on
   Strata could only be a thinking-off arm, not `I_T2`. (The model does think
   when asked: llama.cpp returned 505 reasoning chars on the bat-and-ball probe.)
2. **The drafter is broken on CUDA at the pin** (§7); with it fixed, drafting
   helps code and costs prose.
3. **Alpha** (§2.2): open hangs at high concurrency and long prefill on 5090s,
   reasoning-channel bugs, ~140 commits a day from one author.
4. **The gain is real but bounded**: 1.43x single-stream, 1.5-2.0x prefill on
   the same tensors. Neither engine is near the 433 tok/s cap.

**Open questions:**

- ~~Which engine serves Inkling 2 Mini's v5 run?~~ Answered in §11: Basalt
  v0.5.7, Derisked pack; arm `I_T2` (T2 at one stream, comparable with
  Qwen3.8's Q38U-T2) launched 2026-11-23 20:58 PST.
- ~~Do the SQ packs reproduce 250.3 solo and 4,089.7 at 64?~~ §11.3: SQ-20
  ~190-201 solo; ~1,400 at 64 (`--bench`), not 4,089.7.
- ~~**Basalt has no written code review yet**~~ Done: [§15](#15-basalt-security-and-supply-chain-review).
  OK for the running eval; before it becomes a default, the four items of §15.8.
- Is the `dspark.` fix in a later upstream commit? Re-check `main` before any
  re-run: `gh api repos/Niko1221/Strata/commits/main`, then the three source
  lines in §7.

## 13. Reproduce

On the rig, from `~/openbeast-runs/strata-bench-2026-11-23/` (GPU free, the
default stack stopped). Since 2026-11-23 the toolkit is installed, so
`scripts/gpu-args.sh` can be replaced by `--gpus device=0`.

```bash
scripts/hash-weights.sh                         # expect results/weights.sha256
docker build -t ob-strata-env:2026-11-23 build/env   # the only networked step
scripts/build-strata.sh                         # expect 11df1066…
scripts/build-llama.sh                          # expect 68acd04d… (mainline), 6ee695f2… (PR #32218)
scripts/strata-benchmode.sh                     # §5.4
scripts/strata-serve.sh off ud-q4kxl 1          # then:
python3 scripts/bench.py decode --base http://127.0.0.1:8097 --key "$(cat scratch/strata.key)" \
  --prompts data/prompts.json --warmup --out results/decode/strata-off-s1.json
scripts/llama-serve.sh mainline 1 40960         # then the same with --base http://127.0.0.1:8098 --raw
```

## 14. Files

| Path (relative to `~/openbeast-runs/strata-bench-2026-11-23/` on the rig) | What |
|---|---|
| `results/SUMMARY.txt` | every median in this report, one line per run file |
| `results/decode/*.json` (+ `.codeN.txt`, `.proseN.txt` first-run outputs) | decode runs; `args`, `summary`, every row |
| `results/prefill/*.json`, `results/correctness/*.json` | prefill, needle, top-1 |
| `results/conformance-{strata,llama}/` | conformance reports and the streamed-tools probe |
| `results/strata-benchmode*.txt`, `results/llama-bench.md` | the engines' own benches |
| `results/*.sha256`, `results/gguf-bytes.txt` | binary and weight hashes; the GGUF byte breakdown |
| `results/strata-dspark-cuda-names.patch` | the §7 patch |
| `scripts/` | every script quoted here |
| `logs/` | builds, serves, stop/restore, pre-state |
| `build/env/Dockerfile`, `build/strata-6a6db7ff.bundle` | the image recipe; the source at the pin, offline |

## 15. Basalt security and supply-chain review

> **Done 2026-11-23 ~21:00-21:20 PST (05:00-05:20Z), read only**, while the
> `I_T2` run was serving from Basalt. Nothing from Basalt was built, installed
> or run for this review, and the running `basalt-serve` container was only
> inspected (`docker inspect`, `docker logs`). Review clone, full history:
> `~/.claude/jobs/400403b4/tmp/basalt-src` on the Mac
> (`git clone https://github.com/jesdga95/basalt` then `git checkout
> 400b411c58031d064812ea21a259b2381acdaa3e`; `git describe --tags` = `v0.5.7`;
> 1,527 tracked files). File:line references are to that commit. **Verified**
> means re-checked by the command or file:line given; **claim** means
> Basalt's own statement, not tested here.

### 15.1 Verdict

- **Keep serving the `I_T2` eval in the current sandbox: yes.** No malware,
  obfuscation, telemetry or credential access found. The engine (C++/CUDA)
  has no network code at all. The Python server's only outbound fetch
  (image URLs) is refused for any client that is not on loopback inside the
  container, and every request reaches it from the Docker gateway. TCP out
  of the sandbox times out (§15.4).
- **One isolation gap, low risk, not urgent:** the sandbox bridge resolves
  public DNS names (Docker's embedded resolver forwards them), so a
  compromised process could leak data one way through DNS queries. Nothing
  in Basalt's code does this. Close it before the next start, not by
  restarting the running eval (§15.4).
- **One correctness finding that affects the row:** the thinking-budget close
  does not force an answer on Inkling (upstream #588, open). In the live run,
  3 of the first 18 budget wraps ended at the 8,192 cap with no answer
  (§15.6).
- **Default serve for the 5090: not yet.** The four items of §15.8 come first.

### 15.2 What Basalt is (verified unless marked)

| Fact | Evidence |
|---|---|
| A local inference engine for five MoE / dense models (Qwen3.8-Flash-Next, DeepSeek-V4.1-Flash, Qwen3.8-27B, Hy4-flash, Inkling 2 Mini) on NVIDIA Blackwell sm_120, Linux and Windows | `README.md` lines 3-5; `CMakeLists.txt` model switch `-DBASALT_MODELS` |
| Engine: C++ and CUDA, 817 source files under `src/` and `include/` (199,722 lines); CUDA only, CPU kernels for spilled experts (AVX2 / AVX-512) | `git ls-files src include`; `include/basalt/kernels/cpu/` |
| Server: Python stdlib `http.server`, about 15,700 lines (`serve/`, tests excluded). `bin/basalt serve` execs `python3 -m serve.server --engine-bin <itself>` (the venv's python when present, else `python3`); the server then runs the engine binary as a child (`--serve`, pipes) | `src/program/cli/serve.cpp:51-56`, `src/platform/system.cpp:152-154`, `serve/engine/process.py:319` |
| APIs: OpenAI (`/v1/chat/completions`, `/v1/responses`) and Anthropic (`/v1/messages`); a web console; `/metrics` | `README.md` line 5; `serve/handler.py` |
| Formats: its own `.basalt` pack (sections: tokenizer, template, experts, GGUF-encoded dense tensors, vision, meta); reads GGUF and NVFP4 / MXFP8 safetensors only in the converter | `docs/model-file.md`; `tools/convert/convert.py`; the convert log in §11.1 lists the 10 sections |
| **DSpark** (Inkling's drafter): one block pass drafts up to 7 tokens from three dense layers over the target's visits 5, 23, 41 and its final state, plus a recurrent head; the target verifies every draft. **Claim:** greedy output with drafting equals plain greedy output | `docs/models/inkling2.md:139-145`; `docs/precision/inkling2.md:14,170-172`; `src/models/inkling2/dspark.cpp` |
| Lineage: "Strata, the project Basalt forks (MIT)", plus Infernix kernels (Apache-2.0) and ggml (MIT) | `README.md` Credits; `THIRD_PARTY_LICENSES` |
| Created 2026-10-06 22:17Z, MIT, 1,950 stars, 312 forks, not a GitHub fork | `gh api repos/jesdga95/basalt` |
| 3,526 commits at the pin; 13 releases | `gh api -i 'repos/jesdga95/basalt/commits?per_page=1&sha=400b411c…'` (`page=3526` rel last); `gh api repos/jesdga95/basalt/releases --paginate --jq '.[].tag_name' \| wc -l` |
| Contributors: `jesdga95` 2,351, `Niko1221` 534, then 52, 34, 21 | `gh api 'repos/jesdga95/basalt/contributors?per_page=10'` |
| **Niko1221's 534 are inherited history, not active work:** his commits in the clone run 2026-09-24 to 2026-10-13, the first is "Strata: Qwen3.8-Flash-Next on a 12-24 GB NVIDIA GPU…", and the Basalt repo was created 10-06. The author of Basalt is `jesdga95` (Jesus Gulfo), commits 2026-10-03 to the pin | `git log --author=Niko1221 --format=%ad --date=short \| sort \| sed -n '1p;$p'`; `git log --reverse --format='%h %ad %an %s' \| head -3` |
| Maturity: very young and very fast (about 3,500 commits in 60 days, 0.5.5 → 0.5.7 in two days), 98 open issues and 81 open PRs at review time | `gh api 'search/issues?q=repo:jesdga95/basalt+is:issue+is:open'` / `is:pr` |
| Tests: 135 C++ test files (`*_test.cpp/.cu`), 104 Python test files (84 under `serve/tests`); CI runs them, actions pinned by SHA | `git ls-files \| grep -c …`; `.github/workflows/ci.yml` |

Open Inkling issues relevant to us (`gh api 'search/issues?q=repo:jesdga95/basalt+is:issue+is:open+inkling'`, 14 hits):

| # | Opened (Z) | Title (abridged) | Applies to `I_T2`? |
|---|---|---|---|
| 588 | 11-23 07:08 | after the thinking budget closes the block, the model opens a new thinking block and never answers | **yes** (§15.6); fix proposed in PR #632, open, not in the pin (`git grep answer_lead` empty) |
| 577 | 11-23 00:18 | at temperature 0 the reasoning restarts and never emits the call (deterministic loop) | possibly (a model-at-T0 behaviour per the owner; the reporter's 50-case probe went 49 → 46/50 after a fix) |
| 608 | 11-23 16:06 | parallel 2: engine stall after a budget wrap-up re-read 216K tokens | no (we run `--parallel 1`) |
| 640 | 11-24 03:43 | conversation cache broken with a third chat | no (cache not configured) |
| 581 | 11-23 04:19 | should `tool_choice=none` suppress the native call marker | no (fixed in 0.5.7 per its changelog, #580) |

### 15.3 Supply chain (verified)

- **Checked-in binaries:** none executable. Every tracked file was typed
  with `git ls-files -z | xargs -0 file --mime-type`; the non-text files are
  images (PNG, GIF, SVG), two WOFF2 fonts for the web console, one test
  video (`tests/data/video/synth.mp4`) and five `data/profiles/*.prof`
  (binary integer tables of expert routing rank, loaded as data;
  `docs/configuration/index.md:112`). No ELF, PE, `.so`, jar or wheel. No
  submodules, no Git LFS. The largest files are `docs/img/frontend.gif`
  (690 KB) and the vendored `src/vision/stb_image.h`.
- **Build-time downloads:** none. `CMakeLists.txt` has no `FetchContent`,
  `ExternalProject` or `file(DOWNLOAD)`; its only `execute_process` calls
  run `git rev-parse` / `git status` for a version stamp
  (`cmake/git_stamp.cmake:4,9`, `CMakeLists.txt:792`) and probe a Python
  for numpy (`CMakeLists.txt:1050`). Our build ran with `--network none`
  and succeeded (§11.1), which confirms it.
- **Run-time downloads, `curl | sh`, installers:** none in the engine or
  server. `curl` / `wget` appear only in docs examples and the CI toolchain
  action (`.github/actions/toolchain/action.yml:19`, NVIDIA's apt keyring).
  Release archives are built and uploaded by CI (`release.yml`; v0.5.7's
  assets' uploader is `github-actions[bot]`, `gh api
  repos/jesdga95/basalt/releases/tags/v0.5.7`), better than Strata's
  hand-uploaded assets, but unsigned and without attestations. We do not use
  them.
- **Obfuscation, exec / eval of fetched content:** none. No `eval(`,
  `exec(`, `pickle`, `marshal`, `__import__` or `importlib` in `serve/` or
  `tools/convert/`; the `base64` decodes are `data:` URLs and documents
  (`serve/engine/image_source.py:184`, `serve/api/documents.py:188`). The
  C++ tree has no `system`, `popen` or `exec*`; it spawns processes only
  through `src/platform/process.cpp` (`posix_spawnp`, line 538) for the
  server and its own children. Chat templates render in Jinja's
  `ImmutableSandboxedEnvironment` (`serve/frontend.py:21,60-65`).
- **Telemetry / phone-home:** none. `serve/util/telemetry.py` is local
  hardware readings for `/metrics` (NVML through ctypes, psutil; lines 1-5).
  No update check: `git grep -iE "update.?check|api.github.com|basalt.jgulfo"`
  over `serve src include tools` finds nothing outside tests. The web
  console's only external URLs are donate and docs links
  (`serve/web/app.js:922-923`).
- **Outbound network code, complete list:**
  - Engine (C++): **none.** No socket, `netdb`, `winsock`, curl or WinHTTP
    include anywhere under `src/`, `include/`, `tools/`.
  - Server: `serve/engine/image_source.py` fetches `http(s)` image / video
    URLs and reads local files, **only for a request "provably from this
    machine"** (loopback peer, loopback Host, no forwarding headers, no
    Origin: `serve/security.py:200-220`); any other client gets `data:` URLs
    plus operator-allowed hosts and directories, and we allow none
    (`image_source.py:1-5`, `docs/configuration/image-sources.md:27`:
    "a published port's clients look remote"). In our container every
    request arrives from the Docker gateway `172.23.0.1` through
    `docker-proxy`, so it is remote: no URL fetch, no file read.
    `serve/security.py:65-67` opens a UDP socket to `10.255.255.255:1` only
    to learn its own address (a UDP connect sends nothing).
  - Tools, not used by the server: `tools/convert/mtp_fetch.py` (downloads
    a Qwen drafter from Hugging Face, line 31, 110; never called by
    `convert.py`, which takes its output as `--mtp-source`),
    `tools/community_sync.py`, `tools/llm_review.py` (CI only).
- **Credentials, `~/.ssh`, setuid, privileges:** none. No reference to
  `.ssh`, `id_rsa`, `.netrc`, `.aws`, `HF_TOKEN`, `keyring`, `sudo`,
  `setuid` or capabilities in `serve/`, `src/`, `include/`,
  `tools/convert/`. The API key is read from a file we pass
  (`serve/security.py:35-45`) and compared in constant time
  (`hmac.compare_digest`, line 53). It needs no privileges: it ran as uid
  1000 with every capability dropped.
- **CI with write access (upstream risk, not ours):** a GitHub App
  (`basalt-code-review-bot`) is a bypass actor on `main`'s ruleset and
  squash-merges community benchmark PRs that pass a gate plus an LLM review,
  limited to `docs/community/runs/*.json` (`docs/development/community-automerge.md`).
  The design checks out only the base commit. It is one more path to `main`;
  it is why we pin a reviewed commit and never track `main`.
- **Dependency pinning:** `requirements.txt` pins exact versions (numpy
  2.5.3 / 2.4.6 / 2.2.6 by Python range, jinja2 3.1.6, markupsafe 3.0.3,
  regex 2026.9.10, pyyaml 6.0.3, tqdm 4.70.1, pillow 12.3.0, pypdf 7.0.1,
  psutil 7.2.2; lines 2-12) **without hashes**. Their `Dockerfile` installs
  them from PyPI into a venv and uses an unpinned `nvidia/cuda:13.4.3`
  base. We used neither.
- **What our serve actually runs on (distro packages):** our image
  `ob-strata-env:2026-11-23` (`sha256:0c44ed98…`, built from
  `strata-bench-2026-11-23/build/env/Dockerfile`, base
  `nvidia/cuda:13.0.2-devel-ubuntu24.04@sha256:5dc1bca2…`) has no venv, so
  `bin/basalt serve` falls back to `python3` (`system.cpp:154`) with Ubuntu
  24.04 packages (`dpkg-query -W` in a throwaway, `--network none`
  container of the image):

  | Package | In the image | Basalt pins | Used by the server for |
  |---|---|---|---|
  | python3 | 3.12.3-0ubuntu2.1 | ≥ 3.10 | everything |
  | python3-jinja2 | 3.1.2-1ubuntu1.3 | 3.1.6 | the chat template (sandboxed) |
  | python3-markupsafe | 2.1.5-1build2 | 3.0.3 | Jinja |
  | python3-regex | 0.1.20221031-2build1 (regex 2022.10.31) | 2026.9.10 | `tools/tokenizer.py` (server-side tokenizer, `serve/server.py:243`) |
  | python3-psutil | 5.9.8-2build2 | 7.2.2 | `/metrics` |
  | python3-pil | 10.2.0-1ubuntu1.5 | 12.3.0 | images only |
  | python3-numpy | 1.26.4+ds-6ubuntu1 | 2.5.3 | the converter only |
  | python3-yaml, python3-tqdm | 6.0.1, 4.66.2 | 6.0.3, 4.70.1 | converter tools only |
  | python3-requests | 2.31.0 | (not used) | nothing in Basalt |
  | pypdf | **absent** | 7.0.1 | PDF documents: a request with one gets a 400 (`serve/api/documents.py:237-239`) |

  Jinja 3.1.2 is older than the pin, but Ubuntu backported the sandbox
  fixes that matter for templates from weights: CVE-2024-56201,
  CVE-2024-56326 (1ubuntu1.2) and CVE-2025-27516 (1ubuntu1.3), per
  `/usr/share/doc/python3-jinja2/changelog.Debian.gz` in the image. The
  combination of these older versions with Basalt 0.5.7 is **not what
  upstream tests**; our conformance and thinking probes passed on it (§11.2).

### 15.4 The running isolation

Read from `scripts/basalt-serve.sh` (called by `inkling-v5.sh start
derisked`) and `docker inspect basalt-serve` at 2026-11-24 05:0xZ (container
started 04:57:51Z, image `ob-strata-env:2026-11-23`):

| Requirement | State | Evidence |
|---|---|---|
| No egress network | **TCP: none. DNS: gap** | `NetworkMode strata-bench-net`; the network is `Internal: false`, `enable_ip_masquerade: false` (`docker network inspect`). A throwaway probe container (our image, not Basalt; `--network strata-bench-net --read-only --user 1000:1000 --cap-drop ALL`, `python3 -I`) got: TCP to `1.1.1.1:443`, the LAN router `192.168.40.1:80`, the host gateway `172.23.0.1:22` and `:8099` all **timed out**; but `getaddrinfo("example.com")` = `104.20.23.154` and `huggingface.co` = `108.138.246.71`, **resolved** |
| Weights read-only | yes | `/models` and `/models-derisked` binds `"RW": false` |
| No host home | yes | binds are only `…/inkling-perf-2026-11-23/{build/basalt-tree,weights/…,scratch/basalt.key}` and the driver's libraries, all `:ro`; `HOME=/tmp` |
| `--cap-drop ALL` | yes | `CapDrop ["ALL"]`, `CapAdd null`, `Privileged false` |
| `no-new-privileges` | yes | `SecurityOpt ["no-new-privileges"]`; Docker's default seccomp profile is on (`docker info`) |
| Non-root | yes | `User 1000:1000` |
| Port on 127.0.0.1 only | yes | `PortBindings 8099/tcp → HostIp 127.0.0.1`; `docker-proxy -host-ip 127.0.0.1 -host-port 8099`; `ss -ltn` shows `127.0.0.1:8099` only. The server binds `0.0.0.0` inside the container, reachable only on the bridge |
| Read-only root | yes | `ReadonlyRootfs true`; tmpfs `/tmp` 8 GB |
| API key | yes | `--api-key-file /run/basalt.key` (`:ro`); the log says `api-key=on`; every POST is checked first (`serve/handler.py:457`), and `/metrics` and `/api/logs` on GET (`handler.py:334-342,377-407`); `/health`, the console page and its static files answer without it |

**Gaps, in order of weight:**

1. **DNS resolves** (above). Docker's embedded resolver (`127.0.0.11`)
   answers from the daemon on the host, so the container can send data out
   one way as DNS queries even with no NAT. Same gap for every server of the
   Strata bench (§4 said "no route out"; corrected there). Basalt contains
   no code that does this; the risk is a compromised dependency. **Fix at
   the next start, not now:** create the network with `--internal` and put
   a loopback-published proxy in front (an internal network does not
   publish ports), or start the container with `--dns 127.0.0.1` so the
   embedded resolver has no upstream; then re-run the probe above and
   expect `dns … FAIL`. Not verified which works on this Docker; test first.
2. `--tmpfs /tmp:…,exec`: a file written to `/tmp` can be executed. Strata's
   serves ran without `exec`; whether Basalt needs it is untested.
3. No `--pids-limit` or memory limit (`PidsLimit null`, `Memory 0`): a
   runaway process could starve the host. Availability only.
4. uid 1000 is Max's host uid. All mounts are read-only, so it grants
   nothing extra today; a dedicated uid would be cleaner (the key file is
   mode 600, so it would need re-owning).
5. The image is a full build image (compilers, git, `libcurl`), more tools
   than a serve needs.

### 15.5 The converter (verified)

`scripts/convert-blackfrost.sh` ran Basalt's `tools/convert/convert.py`
with `python3 -I`, `--network none`, `--read-only`, uid 1000, `--cap-drop
ALL`, the checkpoint mounted `:ro`, CPU only. It **executes nothing from the
weights**:

- Safetensors are parsed by hand: an 8-byte length, a JSON header, then
  `np.fromfile` / `np.memmap` at offsets (`tools/convert/model_sections.py:540-550`,
  `tools/convert/checkpoint.py:47-90`). No `torch`, no `safetensors`
  library, no `pickle` (`git grep -nE "pickle|torch|trust_remote_code|transformers" -- '*.py'`
  finds only comments and test names).
- No `trust_remote_code` and no `transformers`: `config.json` and
  `tokenizer.json` are read with `json.loads`
  (`checkpoint.py:54,606-607`). The checkpoint's `auto_map` and its
  `DWM_RECIPE/scripts/*.py` (five files) are never imported; the converter
  only globs `*.safetensors` and reads named JSON files (`checkpoint.py:56`).
- The checkpoint's `chat_template.jinja` is copied into the pack as text
  (`checkpoint.py:654-657`) and later rendered sandboxed (§15.3). Its
  sha256 `53a9035f…` equals the template of the independent Strata pack
  `strata-Inkling-2-Mini-Strata-04996a1e`, so the Derisked edit did not
  change the template.
- `convert.py` puts its own directory first on `sys.path`
  (`convert.py:111-113`) after `-I` removed it, so it imports only the
  read-only `/opt/basalt/tools` tree.
- The log (`logs/convert-blackfrost.log`) shows 10 sections written, no
  network use, no `mtp_fetch` (Inkling's drafter came from the checkpoint's
  own `mtp.safetensors`).

### 15.6 Server behaviours that bear on the eval

All file:line at `400b411c`.

1. **The answer room does not force an answer on Inkling (upstream #588,
   open).** `answer_budget` caps thinking at `cap - reserve`, reserve
   `min(max(512, cap // 4), cap // 2)` (`serve/service.py:249-271`, constant
   line 54): 6,144 of 8,192. On reaching it the server sets `budget = None`
   and writes the dialect's `reasoning_close` (`service.py:1693-1704`),
   which for Inkling is only `<|end_message|>`
   (`serve/models/inkling2.py:507`: "the model opens the next block"). The
   model may open another thinking block; with the budget gone it can think
   to the cap, and the reply ends `length` with no content and no call
   (`service.py:1805-1808` logs "max tokens reached while thinking, no
   answer"). PR #632 (`Dialect.answer_lead`) would open an answer block
   instead; it is unmerged and not in the pin.
   **Observed in the live `I_T2` serve** (`docker logs basalt-serve`,
   05:11:39Z, 14 minutes in): 189 replies, 187 of them with the
   "thinking budget 6144 tokens, output cap 8192" line. **18 budget wraps:
   14 then ended `stop` (50 to 2,041 tokens after the wrap), 4 ran to the
   8,192 cap, and 3 of those 4 logged "no answer"** (the fourth ended
   inside its answer). One more reply ended `length` with no wrap and no
   warning. So far 3 of 189 replies (1.6%) are empty because of #588, and
   the rate was rising (1 of 12 wraps at 05:09Z). A unit whose turn hits it
   gets an empty assistant turn and either recovers on the next turn or
   fails. Record it in the arm's note and the integrity scan, and count
   empty `length` turns per unit when the run ends. **Counted 2026-11-24**
   from the transcripts (the server log went with the container): 203 empty
   capped turns in 21 units, 199 of them in 17 of the 22 failed units
   ([V5_INKLING2_MINI_RTX5090-2026-11-24.md](V5_INKLING2_MINI_RTX5090-2026-11-24.md) §6.1).
   **In the ablations** (server logs kept): 167 "no answer" replies in
   `I_T2_s1` (Derisked, seed 1) and 105 in `I_T2_stock` (the stock pack),
   equal to the transcript counts (that report §10.3, §10.6).
2. **A long-thinking turn gets less room than on llama.cpp.** 6,144 thinking
   tokens where Q_T2's llama.cpp serve allowed up to 8,192. Already recorded
   in §11.4; it is the same for every Basalt arm.
3. **No `max_tokens` means no practical cap.** An unset cap becomes the
   context minus 8 (`service.py:1143-1145`, `serve/prompt.py:12`), so the
   budget becomes about 196K. Our runner sends 8,192 on every agent turn
   (the 187 lines above); 2 more had cap 1,024.
4. **Lenient tool-call parsing.** Besides the native
   `NAME<|content_invoke_tool_json|>{"name":…,"args":{…}}` block
   (`inkling2.py:1-6`, block parser `_Blocks`), the server turns calls the
   model writes in XML notation inside its answer text (`<invoke name=…>`,
   with or without `<tool_call>` / `<function_calls>` wrappers) into real
   tool calls when the name is one of the request's tools and the call is
   whole and outside code (`inkling2.py:269-276`, `parse_xml` 297,
   `_XmlCalls` 363, wired in `Inkling2Parser` 449-478). A call whose JSON
   is whole but lacks its end marker still counts (`_whole`, 215; `finish`,
   240). A call cut off mid-way is **not** emitted as a call
   (`finish`, 240-266), so nothing is fabricated. This rescue is a server
   property that a stricter serve would not have: it can only raise the
   tool-call success rate. Fair to keep (it is what a Basalt user gets), but
   name it when comparing with a llama.cpp row. **In `I_T2`:** 88 turns ended at the
   cap still carrying a tool call (Q_T2 on llama.cpp: 5); the XML path is
   not countable from the transcripts (report §6.2). `I_T2_s1`: 73,
   `I_T2_stock`: 83; the kept server logs do not name the parser path
   either.
5. **Thinking-loop cutter.** One token repeated 256 times in a row inside
   the thinking closes it (`serve/config.py:656`,
   `service.py:1596-1599`). The live log shows none so far.
6. **History keeps reasoning.** The pack's template keeps every assistant
   message's `reasoning_content` in the prompt (`inkling2.py:514-517`), so
   long agent runs grow faster than on a template that drops it. It is the
   model's own template, not a Basalt choice.
7. **Determinism.** DSpark verifies every draft; Basalt claims greedy output
   is unchanged by drafting (`docs/precision/inkling2.md:14`, **claim**, not
   re-tested here). At `--parallel 1` the multi-slot issues (#608, #628)
   do not apply.

### 15.7 Commands and outputs used

```text
$ git -C basalt-src log -1 --format='%H %ad %an'; git -C basalt-src describe --tags
400b411c58031d064812ea21a259b2381acdaa3e Mon Nov 23 21:10:39 2026 -0500 Jesus Gulfo
v0.5.7
$ gh api repos/jesdga95/basalt/pulls/632 --jq '"\(.state) merged=\(.merged)"'
open merged=false
$ ssh beast 'docker network inspect strata-bench-net' | grep -E '"Internal"|masquerade'
    "Internal": false,
        "com.docker.network.bridge.enable_ip_masquerade": "false"
$ ssh beast 'docker run --rm --network strata-bench-net … ob-strata-env:2026-11-23 -I -c "<probe>"'
dns example.com ('104.20.23.154', 443)
dns huggingface.co ('108.138.246.71', 443)
tcp 172.23.0.1 22 FAIL TimeoutError timed out
tcp 192.168.40.1 80 FAIL TimeoutError timed out
tcp 1.1.1.1 443 FAIL TimeoutError timed out
tcp 172.23.0.1 8099 FAIL TimeoutError timed out
$ ssh beast 'docker logs basalt-serve 2>&1 | grep -A4 "wrapping up"'   # 05:07Z, the first no-answer case
[server] thinking budget reached (6144 tokens), wrapping up
[server] s1 done  pp=4948/4839c 0.1s  tg=8192 495.3t/s acc=79% …  end=length …
[server] warning: max tokens reached while thinking, no answer (set reasoning_budget_tokens, …)
$ ssh beast 'cat ~/openbeast-runs/inkling-perf-2026-11-23/results/basalt-bin.sha256' | head -2
63e830d670cec4041ac890a933f5b9664a9395ecbf8937aa206bfa5744edcf0e  …/build/basalt-tree/bin/basalt-vision
5dc8a63ca0089bf8eac749b22da764349d648b7f11f881a659d2511aede84abf  …/build/basalt-tree/bin/basalt
$ ssh beast 'git -C ~/openbeast-runs/inkling-perf-2026-11-23/build/basalt-tree status --porcelain --ignored'
!! bin/
$ ssh beast 'docker logs basalt-serve 2>&1 | awk "/wrapping up/{w=1;next} w&&/ done /{…print tg, end; w=0}"'   # 05:11:39Z
6215 stop / 6194 stop / 8192 length / 6594 stop / 6197 stop / 6224 stop / 6786 stop / 6734 stop / 6204 stop
6353 stop / 7298 stop / 7517 stop / 8192 length / 8185 stop / 6390 stop / 6461 stop / 8192 length / 8192 length
$ ssh beast 'docker logs basalt-serve 2>&1 | grep -c "no answer"'
3
```

The built tree is the pin with only the build's `bin/` added. Sources:
`https://github.com/jesdga95/basalt` (tag `v0.5.7`), issues
`https://github.com/jesdga95/basalt/issues/588`, `/577`, `/608`, `/640`,
PR `https://github.com/jesdga95/basalt/pull/632`; the pack repo
`https://huggingface.co/jesdga/Inkling-2-Mini-Basalt` (stock pack, §11.1).

### 15.8 Before Basalt becomes the 5090's default

1. **Close the DNS channel** (§15.4 gap 1) and prove it with the probe;
   drop `exec` from `/tmp` if Basalt starts without it; add
   `--pids-limit` and a memory limit.
2. **Fix the answer room on Inkling:** re-pin to a reviewed commit with
   #588 fixed (PR #632 or its successor), re-review that diff, and
   re-check with the #588 reproduction (`max_tokens` 3000,
   `reasoning_budget_tokens` 400) before trusting a budget wrap.
3. **Pin the Python side:** a venv built offline from hashed wheels of
   `requirements.txt` (staged with the source), or keep the distro set
   above and run Basalt's own `serve/tests` against it once, offline.
4. **An adapter** (`scripts/engines/basalt.sh`) that carries every hardening
   flag of `basalt-serve.sh` plus items 1 and 3, the offline path staged
   per the offline rule, and an `I_T2` result that justifies switching from
   Qwen3.8.
