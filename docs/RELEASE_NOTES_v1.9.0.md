# OpenBeast v1.9.0 — spark-hydra 🐉🐉: two DGX Sparks, one pair, live

**The DGX Sparks stopped being a plan. Two of them now serve one model
together, and they run their own command center.**

v1.6.0 built multi-engine inference for the DGX Sparks and v1.7.0 built the
router in front of it, both against stubs. v1.9.0 is the first release that
runs on the hardware. The pair is called **spark-hydra**. It serves
GLM-5.3-Flash Ablit tensor-parallel across `spark-1` and `spark-2`:
- 4 concurrent requests and a 1M-token window;
- code at 68-70 tok/s for one request, 224 tok/s across four.

The full OpenBeast command center runs on `spark-1`, so the pair is a
complete OpenBeast of its own, independent of the 5090 rig (`beast`). Both
layers come back after a reboot by themselves, without reaching the network.

v1.8.0 → v1.9.0: #153 (12 commits).

---

## ⚠️ Read before upgrading

1. **The eval era does NOT roll.** It stays `b5596c660b5ab819`.
2. **TensorFold is now sent the inference key.** Native TensorFold (>= 1.0)
   takes `--api-key-file`, so the stack presents `LLAMA_API_KEY` to it exactly
   as it does to vLLM. The affected places:
   - hydra validation and its runtime guard;
   - the dashboard;
   - `ob_backend_models`;
   - conformance;
   - use-model;
   - `hydra.sh add-node`;
   - doctor.

   What changes for a hydra `tensorfold` node:
   - With `key_env`/`key_file`, it used to be a config **error**. It is now
     valid.
   - **Without** a key, a remote one now gets the same warning as any other
     keyless remote node.

   The Python 0.x engine, which the GLM recipe runs, still has no auth. Keep
   it on loopback or behind beast-gate.
3. **`TENSORFOLD_ENGINE` defaults to `native`** in `spark.env.example`. An
   existing `spark.env` without that key now launches the host binary instead
   of the PyTorch container.
   - `TENSORFOLD_ENGINE=container` keeps the old path.
   - Native needs `TENSORFOLD_VERSION`, and `TENSORFOLD_API_KEY_FILE` on
     rank 0.
   - `TENSORFOLD_MASTER_PORT` is now empty by default: 29583 for native,
     29551 for container.
4. **GB10 and other unified-memory GPUs are detected.** `hardware.sh` reads an
   `[N/A]` `memory.total` as one unified pool, sized from MemTotal.
   - On a box with `INFERENCE_MANAGED=false`, bootstrap no longer builds
     llama.cpp, checks the GPU toolchain or downloads a GGUF.
   - Discrete-GPU hosts take the same path as before; the tests check that
     byte for byte.

---

## spark-hydra 🐉🐉

**Runbook:** [SPARK_HYDRA.md](SPARK_HYDRA.md). **Design record:**
[DGX_SPARK_PLAN.md](DGX_SPARK_PLAN.md) (§8a: the stack on a Spark).

| | spark-1 (head) | spark-2 (worker) |
|---|---|---|
| Runs | rank 0, the API (loopback), the command center | rank 1 |
| Boot | `spark-hydra-inference` + `openbeast` user units | started by the head over ssh |
| Links | ConnectX-7, both rails (10.100.216.0/24, 10.100.217.0/24), RoCE all-gathers | |

**The serving stack.**
- **The recipe:** Mia-AiLab's GLM-5.3-Flash EXL3 recipe (v1.8, `33b50fd`):
  TensorFold v0.6.0 plus its patches, in a digest-pinned image.
- **The weights:** `Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit` @
  `57edefd2`. Every file on each Spark matched the Hub's sha256. They are now
  locked in `scripts/backends/models/glm53-flash-ablit-exl3-tensorfold.lock`,
  so later copies are verified offline.
- **Drafting:** DFlash2, 4 parallel streams, fp8 KV, thinking off by default
  (the Ablit weights' recommendation).

**Measured** through the API on spark-1:

| | 1 stream | 4 streams aggregate (each) |
|---|---|---|
| prose | 43-44 tok/s | 145 tok/s (37) |
| code | 68-70 tok/s | 224 tok/s (57) |

**MTP drafting, measured against it:**
- It matches DFlash2 on prose (44 tok/s) but trails on code (56).
- It serves **one request at a time**: four queued requests waited 16-20 s
  for their first token.

DFlash2 is the configuration.

## What ships

- **`scripts/backends/spark-root-setup.sh`:** the one-time root steps for
  each Spark:
  - NCCL 2.28.9+cuda13.0, held at that version;
  - linger;
  - Tailscale as `spark-N`;
  - a ufw rule per ConnectX-7 rail that admits the peer.

  It opens no inference port by default. `--debs DIR` installs from staged
  packages for an offline box.
- **`scripts/backends/spark-hydra-inference.service`:** the pair's boot unit.
  It runs the recipe's foreground mode with `PREPARE=0`, so it never reaches
  the Hub or a registry at boot.
- **The native TensorFold engine** in `tensorfold/spark-node.sh`: the signed
  host binary under systemd `--user`. It is keyed and version-pinned, and it
  places GLM ranks with `TF_GLM_RANK/WORLD` and `NCCL_COMM_ID`.
- **`tensorfold/install-native.sh`:** it checks three things before
  installing:
  - the archive's sha256;
  - the release's ed25519 signature, against a key vendored in the repo;
  - every unpacked file, against `SHA256SUMS`.

  `--stage`/`--from` is the offline path. In testing, it refused a tampered
  signature.
- **The Ablit model profile and its lock.** `model-fetch --verify` now skips
  hidden `hf download` metadata, the same way the lock writer always has.
- **The command center on a Spark (T2):** GB10 detection, and bootstrap for
  unmanaged inference. Six shell suites pass on the hardware.

## Found on the hardware (all fixed or documented in SPARK_HYDRA.md)

- **Docker access from the stack:** the systemd user manager started before
  `max` joined the docker group, so Open WebUI and SearXNG could not reach
  Docker. One reboot fixes it.
- **ufw:** it was active by default on the Sparks. The root setup adds the
  rules the ranks need.
- **NCCL:** it was not installed. The root setup installs and holds it.
- **The NVIDIA Sync `.local` names:** they don't resolve after a reboot or
  away from home. Use the tailnet name, with `HostKeyAlias` so the host key
  stored for the `.local` name still verifies.

## Known open items

- **The full offline playbook.** It still has to cover:
  - the recipe image via `docker save`/`load`;
  - DFlash2;
  - an aarch64 air-gap bundle;
  - zig on the Sparks.
- **Remote access to the pair.** It needs:
  - beast-gate published with `tailscale serve`;
  - a `svc:spark-hydra` Tailscale Service name;
  - the rig's hydra node for it.

  Today the API is loopback-only on spark-1.
- **Switching the primary** between `beast` and `spark-hydra`.
- **`PARALLEL=8`** is unmeasured. spark-1 has about 10 GiB free with the
  command center up, and unified memory freezes instead of failing an
  allocation.
- **Native TensorFold 1.2.7** has not been raced against the recipe yet.
- **The dashboard shows "no GPU" on a GB10:** `_parse_gpus` reads `[N/A]`.
