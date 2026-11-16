# Weight streaming on the 5090 rig: the MoE expert cache, big mode, and the engine gate

This is the runbook for running MoE models larger than the RTX 5090's 32 GB
on the one-GPU rig ("beast"). The experts stream from host RAM through a VRAM
cache. It covers what ships, why it is sized the way it is for **128 GB of
RAM**, how to bring each profile up step by step (online or offline), the
tok/s acceptance check for each one, and how to roll each step back.

Everything here was written on 2026-11-15 while the rig was offline on
Tailscale. **No number in this file was measured on our rig unless it says
MEASURED (us).** The files and tests were verified offline. The profiles
themselves wait for the bring-up below.

Related: [ENGINES.md](ENGINES.md) (the engine plane: `scripts/engine.sh`),
[MODELS.md](MODELS.md) (every model we ship), [ARCHITECTURE.md](ARCHITECTURE.md),
[REFERENCE.md](REFERENCE.md) (`MEM_LIMIT_PCT`, `PROMPT_CACHE_RAM_MB`),
[TODO.md](TODO.md) "PERFORMANCE MATRIX" (the benchmarks still owed).

## What ships, in one table

| Piece | File | Default? | What it is |
|---|---|---|---|
| llama.cpp **v1.0.0** pin | `scripts/llama.cpp.ref` (`b1b770d9…`) | yes | The MoE expert cache (`--moe-cache-mib`, `--moe-cache-cpu-share`) and the `glm5-next` architecture. The old pin `d4389a4` (b10865) has neither |
| Flash-Next + cache | `scripts/serve-qwen38-flash-next-unc-iq4xs-moecache.sh` | no | The shelved Qwen3.8-Flash-Next back, with a VRAM expert cache. Daily-class: the normal 75% scope. Target **55–70 tok/s** |
| Flash-Next static | `scripts/serve-qwen38-flash-next-unc-iq4xs.sh` | no | The measured 38 tok/s baseline, unchanged except a pinned `--cache-ram 2048` |
| **Big mode** (GLM) | `scripts/serve-glm53-flash-abl-q2kxl-big.sh` | no, opt-in | GLM-5.3-Flash abliterated UD-Q2_K_XL. Every routed expert sits in pinned host RAM under its **own 90% scope**. Target **20–30 tok/s** |
| Per-profile scope | `scripts/lib/memscope.sh`, hook in `start.sh` | n/a | The `# openbeast-scope:` header line. Only a profile that declares it gets more than `MEM_LIMIT_PCT` (75), and only on the rig |
| tok/s meter | `scripts/tokps.py` | n/a | `measure`, `check --min N`, `gate` |
| Engine gate | `scripts/engine-gate.sh`, `ob_engine_gate_ok` in `scripts/lib/engine.sh` | n/a | Admits a fourth engine only at **≥ 1.5×** llama.cpp's decode tok/s, measured on this rig with the same weights |
| Project Maya adapter | `scripts/engines/maya.sh`, `scripts/engines/maya/` | no, gated | A fourth engine: reviewed commits, llama.cpp by commit, hash-locked Python packages, staged offline install |

The default model is unchanged: the uncensored Qwen3.8-27B MTP
(`serve-qwen38-27b-uncensored-mtp-q5.sh`, 140 tok/s). Nothing in this file
runs unless you ask for it by name.

## Why it is sized this way (128 GB, final)

Max, 2026-11-15: the rig keeps its **128 GB of dual-channel DDR5**. No
hardware changes. So everything is sized for that box, with headroom for the
OS and the stack:

- `MemTotal` is about **122 GiB** (127,926,272 kB).
- The daily memory scope is 75% of that, about **91.5 GiB**. Only big mode
  raises it to 90%, about **109.8 GiB**. The rest is left for the OS, docker
  (Open WebUI, SearXNG) and the page cache.
- The card has 32,607 MiB (31.8 GiB). Every profile leaves at least about
  3 GiB of it free. The daily driver's measured-safe headroom is 4.76 GB, and
  the crash zone we hit with Gemma 4 31B was about 2 GB
  ([MODELS.md](MODELS.md)).
- **Decode is bound by RAM bandwidth.** MEASURED (us, 2026-09-11): about
  **40 GB/s** effective for scattered expert reads on this box. Every gain
  comes from reading fewer expert bytes from RAM per token, and the VRAM
  cache is what does that.

The tensor sizes below are not estimates. They come from each GGUF's own
tensor table, read over HTTP range requests (headers only, no weights
downloaded) on 2026-11-15.

| Weights | File | Routed experts | Rest | Fits 128 GB? |
|---|---|---|---|---|
| Qwen3.8-Flash-Next-Uncensored IQ4_XS (orcarouter) | 90.8 GiB | 60.94 GiB (48 × 1.27) | 27.63 GiB of tables (token embeddings + PLE, always in host RAM), 2.21 GiB other | yes |
| GLM-5.3-Flash abliterated UD-Q2_K_XL (huihui-ai) | 101.25 GiB | 94.68 GiB (43 layers, 2.2–2.9 GiB each; blk 45 is the MTP layer) | 6.57 GiB | yes, with the 90% scope |
| GLM-5.3-Flash abliterated UD-IQ3_XXS (huihui-ai) | 112.1 GiB | ~105 GiB | ~7 GiB | **no**: more than the 90% scope before the stack |
| GLM-5.3-Flash Uncensored Q2_K (orcarouter, gated repo) | 108.8 GiB | ~102 GiB | ~7 GiB | barely: no room for the stack |
| DeepSeek-V4.1-Flash, any uncensored 3-bit | ~204 GB of experts + Engram tables | n/a | n/a | **no**, see below |

### DeepSeek-V4.1-Flash stays on the Sparks

Its routed experts alone are about 204 GB at 3 bpw (the research report
`streaming-report.md`, §4). On 128 GB that leaves two options, and neither is
useful:

- the 1-bit-expert GSQ IQ2_XXS GGUF (138.6 GB + 110.6 GB of Engram tables on
  NVMe), which has zero headroom and a quality risk;
- NVMe spill of about 95 GB of experts, ESTIMATED **~11 tok/s** (8 on a
  Gen4 drive).

Since the hardware is final, DeepSeek-V4.1-Flash is served by the
spark-hydra pair ([SPARK_HYDRA.md](SPARK_HYDRA.md)), not by the rig. No rig
profile ships for it.

### Best and fastest within 128 GB

| Rank | Profile | Decode (target) | Quality |
|---|---|---|---|
| fastest | the 27B daily driver (unchanged) | 140 tok/s MEASURED (us) | v4 SCORE 97.6 (non-MTP row) |
| fast MoE | Flash-Next + cache | 55–70 ESTIMATED | Flash-Next class, unbenchmarked on v4/v5 |
| best quality | big mode, GLM-5.3-Flash | 20–30 ESTIMATED | GLM-5.3-Flash class, the reason big mode exists |

## The memory plans

### Flash-Next + cache (75% scope, daily-class)

llama.cpp #29887's fastest form, `-cmoe` (every expert in host RAM, the GPU
holding only the cache), would put 60.9 + 27.6 = **88.6 GiB** in host RAM
before the stack. That does not fit the 75% scope. So 8 expert layers stay
resident on the GPU:

| Where | What | GiB |
|---|---|---|
| host RAM | 40 CPU expert layers × 1.27 | 50.8 |
| | tables (token embeddings + PLE) | 27.6 |
| | llama-server buffers | ~1.5 |
| | `--cache-ram 2048` | 2.0 |
| | the stack inside the scope (supervisor, tool server, router, dashboard) | ~3 |
| | **total, of the 91.5 GiB scope** | **~85** |
| VRAM | 8 resident expert layers × 1.27 | 10.2 |
| | non-expert weights | 2.2 |
| | KV at 262,144 (MEASURED 14 KB/token) | ~3.6 |
| | compute buffers + CUDA | ~3.0 |
| | `--moe-cache-mib 8192` | 8.0 |
| | **total, of 31.8 GiB** | **~27 (about 4.9 free)** |

Each layer you move back to the CPU frees 1.27 GiB of VRAM for the cache and
costs 1.27 GiB of host RAM. The 75% scope is the limit. The tuner (step 3)
picks the final cache size and CPU share.

### Big mode, GLM-5.3-Flash (its own 90% scope)

| Where | What | GiB |
|---|---|---|
| host RAM | all routed experts, pinned (`--cpu-moe --load-mode none`) | 94.7 |
| | llama-server buffers | ~1.5 |
| | `--cache-ram 2048` | 2.0 |
| | the stack inside the scope | ~3 |
| | **total, of the 109.8 GiB scope** | **~101** |
| outside the scope | OS, docker (Open WebUI, SearXNG), page cache | ~12 left |
| VRAM | non-expert weights (attention, KDA, 3 dense layers, shared experts, head) | ~6.2 |
| | KV: the MLA latent, `q8_0`, 131,072 tokens | ~3.6 |
| | compute (a 4,096-token prompt batch uploads a whole layer's experts) | ~4 |
| | CUDA | 0.5 |
| | `--moe-cache-mib 14336` | 14.0 |
| | **total, of 31.8 GiB** | **~28.3 (about 3.5 free)** |

Expected decode is 20–30 tok/s at short context. Each token routes 8 of 288
experts in each of 42 MoE layers, which is 2.57 GiB of expert bytes. A
14 GiB cache holds about 15% of the experts and is ASSUMED to serve 50–65%
of the picks. The misses either cross PCIe from pinned RAM or are computed
by the CPU, at about 40–46 GB/s here either way.

Anchor: Project Maya on our rig class (5090 + 9950X3D + 128 GB DDR5-5600),
with stock GLM EXL3 3.05 bpw, MEASURED 25.9–30.1 tok/s (Seppe Gadeyne,
2026-10, research report §2). Prefill: each 4,096-token batch reads every
expert over PCIe, about 2 s at Gen5 x16, so about 2,000 tok/s. It halves if
the slot is really x8 (step 1 measures this).

### While big mode is on

The rig serves GLM-5.3-Flash, not the 27B.

- **beast-hydra**: the rig deployment's upstream (the 27B) is no longer in
  `/v1/models`, so hydra marks it **MISMATCH** and stops routing to it. No
  hydra code changes for this ([BEAST_HYDRA_PLAN.md](BEAST_HYDRA_PLAN.md)
  §N6 and the health table).
  - **`routes.beast` falls to its next target, the Ti** (or the Sparks, per
    your `hydra.toml`).
  - **On a rig-only install with no other node, it returns 503.**
- Clients that name the 27B get the same answer.
- The WebUI, OpenCode and the tool server keep working; they talk to whatever
  model `:8080` serves.
- Going back to the daily driver is `./stop.sh && ./start.sh -d`, which picks
  the default serve script.

## The llama.cpp v1.0.0 pin

What changed for us between `d4389a4` (b10865, 2026-09-08) and `b1b770d9`
(v1.0.0, 2026-11-13), read from the two commits' sources:

- **Options removed** (`common/arg.cpp`): `--defrag-thold` / `-dt`, `--mmap`,
  `--mlock`, `--direct-io` / `-dio`. **No serve, run, profile or measure
  script uses any of them** (`tests/test_weight_streaming.sh` checks this).
  `--load-mode` (which the Flash-Next scripts use) exists in both.
- **Options added that we use**: `--moe-cache-mib N` (env
  `LLAMA_ARG_MOE_CACHE_MIB`; MiB, default 0 = off) and `--moe-cache-cpu-share
  F` (0–1, default 0).
  - Both act only when experts live in host RAM (`-cmoe`, `-ncmoe`, `-ot`).
  - The cache serves batches of 32 tokens or fewer (decode and speculative
    verification). A large prompt batch takes the old upload path.
  - `-lm none`, `-lm mlock` and `-lm dio` pin the host experts; mmap does
    not (upstream `docs/moe-cache.md`).
- **Also added**: `--ple` / `--shard` (extra GGUF shards on other drives),
  `--lazy-mode` (on-demand PLE rows, mmap only), FP8 KV (`-ctk f8_e4m3`),
  `--allow-unauthenticated-tools` and `--media-allow-private-urls` (server
  tools and media; we use neither).
- **Unchanged spellings checked**: `--spec-type draft-mtp`,
  `--spec-draft-n-max`, `--spec-draft-p-min`, `--reasoning`,
  `--reasoning-budget`, `--cache-ram`, `--kv-unified`, `--n-cpu-moe`,
  `--cpu-moe`, `-ngl` (a number is still accepted; the default became
  `auto`). All 186 flags the serve scripts pass are in v1.0.0's option list
  (`tests/fixtures/llama/arg-options-v1.0.0.txt`).
- **One behaviour change that would have broken remote clients.** v1.0.0's
  DNS-rebinding guard (#32098) refuses a request on a loopback socket whose
  `Host` names neither loopback nor an allowed origin. It answers 403
  "Cross-origin request not allowed (see --cors-origins)".
  - Tailscale Serve forwards the client's Host (`ipn/ipnlocal/serve.go`:
    `r.Out.Host = r.In.Host`), so the raw `:8443 → :8080` mount (no
    EDGE_GATE) would 403 every remote request.
  - `serve.sh` now allows `https://<tailnet fqdn>:8443` as the origin when
    it knows the tailnet name.
  - beast-gate, the router and hydra drop Host, so they are unaffected.
- **MTP and the MoE cache**: #31497 keeps the cache off the draft model. The
  shipped MoE profiles run with MTP off; see "Experiments after acceptance".

## Bring-up runbook

Run it in order, on the rig, as the normal user, not root. Each step says
what to expect, how to accept it and how to undo it. Each step that needs the
network has an offline path; the rig can stay islanded.

### Step 0: preflight (5 min)

```bash
cd ~/openbeast                      # the checkout
git status --short                  # clean, on main with this change merged
git -C llama.cpp status --short     # MUST be clean: engine.sh refuses to switch commits under local changes
./scripts/gpu-lease.sh status       # no lease held
./start.sh doctor | tail -20        # expect: "llama: installed b10865 …, pinned b1b770d90d94" as a WARN until step 2
```

If `llama.cpp/` has local changes (an old experiment branch such as
`beast-rank-kernels`), save them first:

```bash
git -C llama.cpp stash push -u -m "pre-v1.0.0"
```

### Step 1: measure the rig (about 1 h; most of it needs no GPU)

Every projection above depends on these numbers. Record them in
[RESULTS.md](RESULTS.md) under the hardware table.

```bash
sudo dmidecode -t memory | grep -E 'Configured Memory Speed|^\s+Size' | sort | uniq -c   # expect 4 x 32 GB; 5600 or 5200 MT/s? (a 2DPC fallback to 3600-4800 costs 10-50%)
nvidia-smi -q -d PCIE | grep -A3 -E 'Link (Gen|Width)'   # under load: Gen 5, x16 (x8 halves prefill)
nvme list                                             # model + generation of the weights drive
python3 - <<'EOF'                                      # rough RAM bandwidth (a big memcpy), no installs
import time, array
a = bytearray(4 << 30); b = bytearray(4 << 30)
t = time.time(); b[:] = a; dt = time.time() - t
print(f"memcpy ~{2 * 4 / dt:.0f} GiB/s (read+write)")
EOF
```

Offline: all of these are local tools. `dmidecode` needs root.

### Step 2: move llama.cpp to v1.0.0 (20–40 min, mostly the build)

The pin file already names v1.0.0 (`b1b770d9…`). Build it.

**Online:**

```bash
./stop.sh
scripts/engine.sh install llama --version v1.0.0
```

Expected:

```
llama.cpp v1.0.0 (b1b770d90d94) built; scripts/llama.cpp.ref now pins it — review and commit that file.
pinned llama v1.0.0 @ b1b770d90d94cad9e9ea5b993bdbc383a522fb28
```

`scripts/llama.cpp.ref` should not change (`git diff --stat` is empty),
because it already pins this commit.

**Offline** (the rig islanded): on any connected box with this checkout and
a `llama.cpp/` clone,

```bash
scripts/engine.sh stage llama --version v1.0.0 /media/usb/llama      # a one-commit bare repo + MANIFEST
```

carry `/media/usb/llama` over, then on the rig:

```bash
./stop.sh
scripts/engine.sh install llama --from /media/usb/llama               # verifies COMMIT=b1b770d9…, builds, pins
```

**Check the build**:

```bash
llama.cpp/build/bin/llama-server --help | grep -E -- '--moe-cache-mib|--moe-cache-cpu-share|--load-mode'
# three lines, one per option
scripts/engine.sh status            # pin: v1.0.0@b1b770d9…
```

**Acceptance: the daily driver did not regress.**

```bash
./start.sh -d                                         # the default 27B MTP profile
./start.sh doctor | grep -iE 'fail|warn' || echo clean
python3 scripts/tokps.py check --url http://127.0.0.1:8080 --min 125 --runs 3
# expect: decode ~140 tok/s (MEASURED 140.2, 2026-08-19); below 125 (-10%) is a regression
```

**Acceptance: remote clients still reach the raw mount** (only if
`setup-tailscale.sh` published `:8443` without EDGE_GATE). From a client:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $LLAMA_API_KEY" \
  https://beast.<tailnet>.ts.net:8443/v1/models        # 200, not 403
grep -c '(CORS) rejected' .run/stack.log   # 0 (llama-server's output lands in the stack log)
```

A 403 here means the tailnet name was not known when llama-server started.
`cat .run/webui-tailnet-name` should hold it. Set it explicitly with
`LLAMA_ARG_CORS_ORIGINS=https://beast.<tailnet>.ts.net:8443` in
`openbeast.conf` and restart.

**Rollback** (offline-safe; b10865's objects are still in `llama.cpp/`):

```bash
./stop.sh
scripts/engine.sh rollback llama      # "rolling llama back: v1.0.0@b1b770d9… → b10865@d4389a4d…", rebuilds
./start.sh -d
```

`rollback` rewrites `scripts/llama.cpp.ref` to the old commit. Commit that,
or `git checkout scripts/llama.cpp.ref` when you move forward again.

### Step 3: Flash-Next + the MoE cache (about 1 h)

The weights are the ones already on the rig (`Qwen3.8-Flash-Next-Uncensored-IQ4_XS-0000{1,2,3}-of-00003.gguf`,
pinned since 2026-09-11). Nothing to download.

**First, the static baseline on the new build**, so the comparison is on one
build:

```bash
./stop.sh && ./start.sh -d serve-qwen38-flash-next-unc-iq4xs.sh
python3 scripts/tokps.py measure --url http://127.0.0.1:8080 --label fn-static --record .engines/tokps/fn-static.json
# expect ~38 tok/s (MEASURED 2026-09-11 on b10865)
```

**Then the cache profile:**

```bash
./stop.sh && ./start.sh -d serve-qwen38-flash-next-unc-iq4xs-moecache.sh
# banner: "(memory-capped scope 'openbeast-stack': 75% of RAM = 91G, swap 8G)"  — the daily scope, NOT raised
python3 scripts/tokps.py check --url http://127.0.0.1:8080 --min 55 --label fn-cache --record .engines/tokps/fn-cache.json
nvidia-smi --query-gpu=memory.used,memory.total --format=csv     # expect ~27 GiB used, >= 2.5 GiB free
systemctl --user show openbeast-stack -p MemoryCurrent           # expect < 88 GiB (94,489,280,512 bytes)
```

**Tune it.** The two numbers in the script are a starting point. With the
stack stopped (the tuner starts its own server six times):

```bash
./stop.sh
W="$(source scripts/lib/weights.sh; echo "$WEIGHTS_DIR")"
python3 llama.cpp/scripts/moe-cache-tune.py --max-mib 9000 -- \
  llama.cpp/build/bin/llama-server -m "$W/Qwen3.8-Flash-Next-Uncensored-IQ4_XS-00001-of-00003.gguf" \
  -ngl 99 -c 262144 -np 1 --n-cpu-moe 40 --load-mode none -ctk q4_0 -ctv q4_0 -fa on
# ends with:  Recommended: --moe-cache-mib N --moe-cache-cpu-share F, X t/s
```

Write N and F into `serve-qwen38-flash-next-unc-iq4xs-moecache.sh`, run the
check again, and commit the script with the measured numbers in its header
(the way the 27B MTP script records its sweep). Keep `--max-mib` at or below
what leaves 2.5 GiB of VRAM free at 262K context.

**Acceptance**: decode **≥ 55 tok/s** at short context. Also record
`--prompt long` (about 8K tokens of context), plus VRAM and MemoryCurrent.

**If the 75% fit stays below 55 tok/s**: the fastest form (`-cmoe`, every
expert in RAM, about a 19 GB cache) needs about 92 GiB of host RAM, so it
needs the raised scope. Max scoped that raise to big mode only, so this is
his decision. Do not add a scope line to this profile without it.

**Rollback**: `./stop.sh && ./start.sh -d` (the default), or the static
script.

### Step 4: big mode, one-time setup (15 min, root)

**Memlock.** Big mode asks the scope for `LimitMEMLOCK=infinity`. A user unit
cannot exceed the user manager's hard limit. Until it is raised, `start.sh`
skips the property and warns. llama.cpp pins its host experts with CUDA, which
does not need this limit; Maya's RAM tier may. Raise it once:

```bash
sudo mkdir -p /etc/systemd/system/user@.service.d
printf '[Service]\nLimitMEMLOCK=infinity\n' | sudo tee /etc/systemd/system/user@.service.d/90-openbeast-memlock.conf
printf '%s soft memlock unlimited\n%s hard memlock unlimited\n' "$USER" "$USER" \
  | sudo tee /etc/security/limits.d/90-openbeast-memlock.conf
sudo systemctl daemon-reload
# log out and back in (or reboot), then:
ulimit -H -l          # unlimited
```

**systemd-oomd** (Ubuntu 22.04 and newer). Loading about 95 GiB reclaims the
page cache for minutes, and oomd can end the whole user session while RAM is
still free. Project Maya reports this as issue #497; it applies to llama.cpp
too. If `systemctl is-active systemd-oomd` says active:

```bash
printf '[Service]\nManagedOOMMemoryPressure=auto\n' \
  | sudo tee /etc/systemd/system/user@.service.d/90-openbeast-oomd.conf
sudo systemctl daemon-reload      # then log out and in
```

Rollback: delete the two drop-ins and the limits file, `daemon-reload`, log
in again.

### Step 5: big mode, the weights (online about 1 h at 30 MB/s; or sideloaded)

Four shards, 108,720,072,064 bytes, pinned by sha256 in
`scripts/weights.registry`. The repo is
`huihui-ai/Huihui-GLM-5.3-Flash-abliterated-GGUF`, rev `c43a9946`, not gated.

**Online** (each shard is size-checked against the disk first, staged,
sha256-verified, then renamed):

```bash
for i in 1 2 3 4; do ./scripts/fetch-weight.sh "Huihui-GLM-5.3-Flash-abliterated-UD-Q2_K_XL-0000$i-of-00004.gguf"; done
```

**Offline** (the rig islanded): on a connected box,

```bash
hf download huihui-ai/Huihui-GLM-5.3-Flash-abliterated-GGUF \
  --revision c43a994637c10f5c376b7a471b3afc7663f80b9b \
  --include 'UD-Q2_K_XL/*' --local-dir /media/usb/glm
```

then on the rig copy each file to `$WEIGHTS_DIR` under its LOCAL name and
verify it:

```bash
for i in 1 2 3 4; do
  cp /media/usb/glm/UD-Q2_K_XL/GLM-5.3-Flash-UD-Q2_K_XL-0000$i-of-00004.gguf \
     "$WEIGHTS_DIR/Huihui-GLM-5.3-Flash-abliterated-UD-Q2_K_XL-0000$i-of-00004.gguf"
  ./scripts/verify-weights.sh --file "Huihui-GLM-5.3-Flash-abliterated-UD-Q2_K_XL-0000$i-of-00004.gguf"
done
```

Rename all four, or none: llama.cpp finds shards 2–4 from shard 1's prefix.
Stock unsloth files carry the same remote names, so the local prefix keeps
the two apart, and the registry's sha256 tells them apart if they are ever
mixed.

### Step 6: big mode, start and accept (20 min)

```bash
./stop.sh && ./start.sh -d serve-glm53-flash-abl-q2kxl-big.sh
```

Expected banner line (90% of 122 GiB):

```
  (memory-capped scope 'openbeast-stack': 90% of RAM = 109G, swap 8G, raised for serve-glm53-flash-abl-q2kxl-big.sh, memlock unlimited)
```

`start.sh` refuses this profile on a Spark, on a box under 118 GiB, and while
someone else's GPU lease is held. The load takes several minutes (about 95 GiB
pinned).

```bash
tail -f .run/stack.log                          # until "ready"
python3 scripts/tokps.py check --url http://127.0.0.1:8080 --min 20 --label glm-llama --record .engines/tokps/glm-llama.json
python3 scripts/tokps.py measure --url http://127.0.0.1:8080 --prompt long --label glm-llama-long
nvidia-smi --query-gpu=memory.used,memory.total --format=csv     # >= 2.5 GiB free
systemctl --user show openbeast-stack -p MemoryCurrent           # < 106 GiB
```

**Acceptance**:

- decode **≥ 20 tok/s** at short context;
- MemoryCurrent at least 3 GiB under the scope;
- VRAM at least 2.5 GiB free;
- tool calls work: `scripts/backends/conformance.sh --url http://127.0.0.1:8080 --model glm`;
- the model answers without refusing, as every OpenBeast model must (the
  huihui ablation covers layers 15–35; check it with your own refusal
  prompts).

**If it does not load**:

- **The KV type.** GLM-5.3-Flash is nope-MLA plus KDA. If llama-server
  rejects `q8_0` for the latent cache, pass `-ctk f16`
  (`./start.sh -d serve-glm53-flash-abl-q2kxl-big.sh` takes no args; edit the
  script) and give the cache 1.8 GiB less.
- **A VRAM OOM at the first long prompt** means the prefill compute buffer
  is larger than the 4 GiB planned: lower `-ub` to 2048.

**Tune it** (stack stopped):

```bash
./stop.sh
python3 llama.cpp/scripts/moe-cache-tune.py --max-mib 15000 -- \
  llama.cpp/build/bin/llama-server -m "$W/Huihui-GLM-5.3-Flash-abliterated-UD-Q2_K_XL-00001-of-00004.gguf" \
  -ngl 99 -c 131072 -np 1 -ctk q8_0 -ctv q8_0 --cpu-moe --load-mode none -b 4096 -ub 4096 -fa on
```

Write the result into the script header with the date.

**Back to the daily driver**: `./stop.sh && ./start.sh -d`. Expect "75% of
RAM = 91G" again.

**If the published quant fails** (it does not load in v1.0.0, it refuses, or
its quality is unusable), follow the decision path below. Do not
self-quantize first.

### Step 7: Project Maya, only to test the 1.5× gate (2–3 h)

Maya is admitted only if it decodes **≥ 1.5×** what llama.cpp does on the
same weights on this rig (Max, 2026-11-15). Step 6's llama.cpp number is the
bar. At 22 tok/s, for example, Maya needs 33.

**Install** (reviewed v1.1.65, commit `c81fab6c`; its llama.cpp `3cf03257`;
21 packages hash-locked):

```bash
scripts/engine.sh install maya --version v1.1.65
# "Project Maya v1.1.65 (c81fab6c9d63) installed in ~/.local/opt/maya-v1.1.65 — not compiled yet"
```

**Offline**: on a connected box (the wheels for the rig's python; pick its
version),

```bash
MAYA_STAGE_PYTHON=3.12 ENG_STAGE_PLATFORM=manylinux_2_28_x86_64 \
  scripts/engine.sh stage maya --version v1.1.65 /media/usb/maya
```

and on the rig: `scripts/engine.sh install maya --from /media/usb/maya`.

**Prepare** (Maya's own build, about 30 min, plus its pack, about 1 GB
written beside the GGUF; the stack stopped):

```bash
./stop.sh
scripts/engines/maya/prepare.sh glm53-flash-abl-q2kxl --print     # the exact command
scripts/engines/maya/prepare.sh glm53-flash-abl-q2kxl
# "Prepared: ~/.local/opt/maya-v1.1.65/maya-<name>.json (recorded for profile glm53-flash-abl-q2kxl)."
```

**UNVERIFIED** (Maya was never run here):

- that `--gguf-dir` takes huihui's renamed UD-Q2_K_XL shards (Maya's docs
  call non-listed quants "experimental: they run");
- that the non-interactive setup writes a `maya-*.json`;
- that the routed experts, which are byte-identical to unsloth's stock quant,
  let Maya use its shipped GLM expert profile.

If prepare fails, read `maya-setup.log` in `~/.local/opt/maya-v1.1.65`.

**Gate it** (this runs llama.cpp big mode, then Maya, back to back; about
20 min):

```bash
scripts/engine-gate.sh maya --profile glm53-flash-abl-q2kxl --serve-script serve-glm53-flash-abl-q2kxl-big.sh --dry-run
scripts/engine-gate.sh maya --profile glm53-flash-abl-q2kxl --serve-script serve-glm53-flash-abl-q2kxl-big.sh
# engine gate maya: PASS — 33.1 vs 21.4 tok/s = 1.55x (needs 1.5x)     (illustrative)
# PASS: maya may be used (scripts/engine.sh use maya --profile glm53-flash-abl-q2kxl).
```

The verdict goes to `.engines/gate/maya.json`, with a line in
`scripts/engine.sh history`. The box is always switched back to llama.cpp
afterwards, pass or fail.

**On PASS**:

```bash
scripts/engine.sh use maya --profile glm53-flash-abl-q2kxl     # stops the stack, rewrites the conf, starts Maya
scripts/backends/conformance.sh --url http://127.0.0.1:8080 --model glm-5.3-flash-abl-q2kxl
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/v1/models   # 401 when LLAMA_API_KEY is set (MAYA_API_KEY reached it)
```

**On FAIL**: nothing to do. Maya stays installed and gated, and `engine.sh
use maya` refuses with the measured ratio. Record the numbers in
RESULTS.md. Removing it: `rm -rf ~/.local/opt/maya*` and drop
`ENGINE_PIN_MAYA` from `openbeast.conf`.

**Rollback**: `scripts/engine.sh use llama` (back to the default rig, byte
for byte). `scripts/engine.sh rollback maya` relinks the previous Maya
version, offline.

### Step 8: record and benchmark

1. Put every measured tok/s (short and long, prefill, VRAM, MemoryCurrent,
   the tuner's numbers) in [RESULTS.md](RESULTS.md) and in the serve
   scripts' headers, with the date.
2. The v4/v5 SCORE rows are the open items in [TODO.md](TODO.md)
   "PERFORMANCE MATRIX". The 27B tops v4, so for big mode the first run is a
   held-out slice of tasks the 27B fails, run paired. A full v5 cell at
   about 25 tok/s is 2–3 days of the GPU.
3. Once a profile is accepted, move its slug from `BENCH_EXCLUDED`
   (`evals/benchmark_all.py`) into `MODELS` when Max schedules it, and from
   `scripts/opencode-excluded.txt` into `opencode.json`.

### Rollback, all of it

| Undo | Command | Offline? |
|---|---|---|
| a profile | `./stop.sh && ./start.sh -d` (the default 27B) | yes |
| the llama.cpp pin | `scripts/engine.sh rollback llama` then `./start.sh -d` | yes (objects local) |
| Maya as the engine | `scripts/engine.sh use llama` | yes |
| a Maya version | `scripts/engine.sh rollback maya` | yes (relink) |
| memlock / oomd drop-ins | delete the files from step 4, `sudo systemctl daemon-reload`, log in again | yes |
| the GLM weights | `rm "$WEIGHTS_DIR"/Huihui-GLM-5.3-Flash-abliterated-UD-Q2_K_XL-0000?-of-00004.gguf` (and Maya's pack beside them) | yes |

## Uncensored weights: the decision path (self-quantize last)

Max's rule: every served model is uncensored, and we self-quantize only if no
published uncensored quant loads in the chosen engine. The path:

1. **A published uncensored GGUF, verified.** Today that is huihui-ai
   UD-Q2_K_XL. Its abliteration was checked against the stock quant, tensor
   by tensor over range reads: layers 15–35's `attn_output` and
   `ffn_down_shexp` differ; layers 5 and 40, the routed experts and the
   embeddings are byte-identical.
2. **If it fails** (no load, refusals, broken quality), try the next
   published one that fits, in this order. Each needs a registry pin and a
   serve-script line, not a quant run.
   - huihui-ai **UD-IQ2_XXS**: 94.9 GiB; more headroom, lower quality.
   - Baekpica **Mixed-IQ2XXS-IQ2XS-Q2K**: 87.5 GiB, from orcarouter's FP8.
   - orcarouter **Q2_K**: 108.8 GiB, a gated repo (accept the terms, then
     `HF_TOKEN_FILE`); it barely fits the 90% scope beside the stack.
3. **Only if none of them loads**: self-quantize, and not before Max says
   so.
   - Source: `orcarouter/GLM-5.3-Flash-Uncensored-FP8`, about 328 GB (hours
     of download and NVMe wear).
   - Tool: `llama.cpp/convert_hf_to_gguf.py` at the pinned commit, then
     `llama-quantize` with an imatrix to about 2.5–2.9 bpw.
   - Record the source repo@rev, the converter commit and the imatrix
     dataset in the registry comment, and pin the result by its sha256.
   - It needs about 450 GB of free NVMe. Nothing was downloaded for this on
     2026-11-15.

The same rule binds the Maya profile: it serves exactly these GGUF files.

## The engine gate

`scripts/engine-gate.sh <engine> --profile P --serve-script S` decides
whether a candidate engine is admitted.

- **What it measures.** One streamed chat completion per run (`tokps.py`):
  fixed prompt, temperature 0, seed 42, 256 tokens, one warm-up run, then the
  median of 3.
  - Decode tok/s is llama-server's own `timings.predicted_per_second` when
    the server reports it; otherwise the token spacing of the stream. TTFT
    is excluded either way.
  - Two records from different hosts, or with different prompts, are never
    compared.
- **The bar is 1.5×.** `ENGINE_GATE_RATIO` may raise it, never lower it. A
  verdict below 1.5× is refused even if someone hand-edits it to `"pass":
  true` (`ob_engine_gate_ok`).
- **What it opens.** Only a passing verdict on file lets the adapter's
  `profile_check` and `launch` through. `--force` does not bypass it.
  `OPENBEAST_ENGINE_GATE=measuring` is the one exception, and only the gate
  run sets it.
- The API key comes from `LLAMA_API_KEY` in the environment, never argv.

### Why Maya and not Strata

Both are single-author engines measured on our rig class.

- **Project Maya** covers GLM-5.3-Flash, DeepSeek-V4.1, MiniMax and
  Flash-Next, and takes any GLM GGUF through `--gguf-dir`. Big mode, where
  llama.cpp is weakest relative to the field, is its home ground: 25.9–30.1
  tok/s MEASURED (Seppe) against our 20–30 ESTIMATE. That is the plausible
  place for 1.5×.
- **Strata** (Niko1221, v0.2.14, 2026-11-15) ships per-platform engine
  tarballs (`strata-engine-0.1.60-linux-x86_64-cuda-cuda13.tar.gz`) with a
  `SHA256SUMS.txt`. Only the desktop app and the update feed carry `.sig`
  files; the engine tarballs and the SHA256SUMS do not.
  - It serves only Flash-Next and Qwen3.6-35B, from its own `.pack` files.
  - It measured 78.8 tok/s on Flash-Next on our rig class, against
    llama.cpp's 67.8 with the cache on a slower host. That is about 1.2×,
    unlikely to clear 1.5×.
  - An adapter for it would follow the same file contract, with an
    install-native-style archive and SHA256SUMS check. None ships.

## Experiments after acceptance (not shipped)

- **MTP on Flash-Next**: the orcarouter `…-MTP-draft.gguf` with `-md … --spec-type
  draft-mtp`. It needed an unmerged PR on b10865, and v1.0.0 support for
  `qwen4exp` drafts is unverified.
- **MTP on GLM**: blk 45 is GLM's nextn layer, inside the same file
  (`--spec-type draft-mtp`). The MoE cache does not cover it (#31497).
- **`--moe-cache-cpu-share` above 0.** With pinned experts, upstream says it
  rarely helps; the tuner tells.
- **`--load-mode dio`** instead of `none`, to keep the page cache from
  doubling during the load (also pins).

## Moving the pin again

1. `scripts/engine.sh install llama --version <tag>` (or stage + `--from`).
2. Regenerate the option list the test checks, from the new commit's
   `common/arg.cpp`: every quoted `-…` name inside the `common_arg({...})`
   braces, sorted, one per line, into
   `tests/fixtures/llama/arg-options-v<version>.txt` with the commit in its
   header. Then point `tests/test_weight_streaming.sh`'s `OPTS` at it.
   `bash tests/test_weight_streaming.sh` then names every serve-script flag
   the new build lacks.
3. Re-read upstream's release notes for server behaviour changes like
   #32098, which a flag check cannot see.

## Sources

Every fact below was read on **2026-11-15**. Numbers from other people are
labelled MEASURED (who, hardware) in the text above; they are not ours.

**Verified:**

- **llama.cpp v1.0.0**
  - `gh api repos/ggml-org/llama.cpp/releases/latest`: v1.0.0, published
    2026-11-13; annotated tag `a6d1a74d` peels to commit
    `b1b770d90d94cad9e9ea5b993bdbc383a522fb28` (`…/git/tags/a6d1a74d…`).
  - `common/arg.cpp` at `b1b770d9` and at `d4389a4d`: the option diff above,
    and the `--moe-cache-mib` / `--moe-cache-cpu-share` / `--load-mode` /
    `--fit` definitions.
  - `tools/server/server-http.cpp` at v1.0.0: `request_host_is_allowed`,
    `request_origin_is_allowed`.
  - `docs/moe-cache.md` and `scripts/moe-cache-tune.py` at v1.0.0.
  - `src/llama-arch.cpp`: `glm5-next` and `deepseek41` in v1.0.0, absent
    from `d4389a4d`.
  - `src/models/glm5-next.cpp`: hybrid KDA plus nope-MLA with a DSA
    indexer.
  - PRs #29887 (merged 2026-10-07; its 5090 table), #30966 (2026-11-07),
    #31497 (2026-11-08), #32098 (2026-11-11).
- **Tailscale**: `ipn/ipnlocal/serve.go` (main), `r.Out.Host = r.In.Host` for
  TCP proxies.
- **Hugging Face** (API tree listings, LFS sha256 and sizes):
  - `huihui-ai/Huihui-GLM-5.3-Flash-abliterated-GGUF` rev `c43a9946` and its
    model card;
  - `unsloth/GLM-5.3-Flash-GGUF` rev `a38483c8`;
  - `orcarouter/GLM-5.3-Flash-Uncensored-GGUF` rev `7c5e75e9` (gated);
  - `orcarouter/Qwen3.8-Flash-Next-Uncensored-GGUF` rev `98eb3357` (gated);
  - `Baekpica/GLM-5.3-Flash-Uncensored-Mixed-Quant-GGUF` rev `8f7b9c01`;
  - the search for every GLM-5.3-Flash uncensored or abliterated repo.
- **Tensor tables and the tensor comparison**: GGUF headers and tensor bytes
  read with HTTP range requests (scratch tooling, not shipped).
- **Project Maya**:
  - `gh api repos/mw00/project-maya`: MIT, tags v1.1.61–v1.1.65 with no
    assets; v1.1.65 is a lightweight tag on `c81fab6c9d63cf69b928f92460e0a662b28aa9ec`.
  - `thomas9120/project-maya` is a fork (parent `mw00/project-maya`).
  - At that commit: `setup.py` (`LLAMA_CPP_COMMIT`, `PY_PACKAGES`, the zip
    download, `pip_install` and its stamp); `maya.py` (`--llama-dir`,
    `--gguf-dir`, `--setup`, `--yes`, `--plain`, `--no-start`, `--config`,
    `--host`, `--port`); `serve/server.py` (`MAYA_API_KEY`, `/health`);
    `docs/SETUP.md` and `docs/SETTINGS.md` (`MAYA_GLM_RAM_HEADROOM_GB`,
    systemd-oomd #497).
- **Strata**: `gh api repos/Niko1221/Strata/releases`: v0.2.14, its assets.
- **PyPI**: the release dates behind the 14-day cooldown on
  `scripts/engines/maya/requirements.txt`; the lock built with
  `scripts/lib/pydeps_lock.py` on python 3.12.
- **Measured by us**: Flash-Next IQ4_XS static, 38 tok/s, 2026-09-11
  (MODELS.md); the 27B MTP, 140.2 tok/s, 2026-08-19.

**Unverified** (needs the rig): every tok/s and memory figure for the new
profiles; the GLM `q8_0` KV; Maya with huihui's GGUF and its non-interactive
setup; `LimitMEMLOCK=infinity` on this box's user manager; the PCIe link
width and DIMM speed; `qwen4exp` MTP drafts on v1.0.0.

**The research behind this**: the decision report of 2026-11-15
(`streaming-report.md` in that session's scratchpad, published as the
"weight-streaming" artifact). It holds 0xSero / sybil-solutions, Mia's AI
Lab, KTransformers, ik_llama.cpp and the other options this runbook does not
use, and why.
