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
  `com.docker.network.bridge.enable_ip_masquerade=false` (no route out),
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
03:23Z). Built from that source, offline; served with no egress. **No written
supply-chain review of Basalt's code was found in the investigation folder**
(§11's brief required one): the mitigations are the source build at a tag and
the sandbox, not a read of the code. Recorded as an open item (§12).

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
thinking with the template's own close and the model must answer. On
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
- **Basalt has no written code review yet** (§11.3). Before Basalt becomes a
  default or ships in `scripts/`, do the read-only review §2 did for Strata.
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
