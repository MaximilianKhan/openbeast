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
> "Basalt" candidate) is running on the rig now; its slot is
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
  CUDA at the pin, and it is alpha. The engine for the Inkling v5 run waits on
  the investigation in §11.

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

> **To be filled by the next report.** Investigation started 2026-11-23
> ~19:39 PST, running on the rig in `~/openbeast-runs/inkling-perf-2026-11-23/`
> (owned by another agent; not read for this report).

Scope as briefed:

- Strata's own SQ-10 / SQ-15 / SQ-20 packs (pinned hashes above, downloaded
  and hash-checked) with the built-in DSpark at its defaults: single-stream
  code and prose, 4 and 64 concurrent streams (to test 4,089.7 directly),
  prefill, correctness, conformance; and whether v0.2.20's "fixed DSpark
  drafter" is the bug in §7.
- vLLM with the Blackfrost "Derisked" NVFP4 build and its MTP head.
- A "Basalt" engine candidate: built from source in the same hardened image
  (supply-chain review required before it runs; check that review in the next
  report).
- The recommendation: the fastest setup the v5 harness can drive with
  thinking and tool calls on.

Known so far, from the session record only: the Docker restart for the
toolkit install (19:57:17) interrupted it once; it resumed and was told to
switch to `--gpus` and re-run any measurement from about 19:56-19:59.

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

- Which engine serves Inkling 2 Mini's v5 run? Waits on §11. The fair test of
  Inkling against Qwen3.8's 52/71 is arm `I_T2` (T2 at one stream) on an
  engine with thinking and tool calls on: llama.cpp today, or whatever §11
  finds.
- Do the SQ packs reproduce 250.3 solo and 4,089.7 at 64 on our 5090? (§11)
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
