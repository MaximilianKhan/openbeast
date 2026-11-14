# spark-hydra: the two-DGX-Spark pair (operations)

**Status, 2026-11-13: live.** GLM-5.3-Flash (Mia-AiLab EXL3 4-bit, Ablit
weights) serves tensor-parallel across `spark-1` + `spark-2`, with the
OpenBeast command center on `spark-1`. Both come back by themselves after a
reboot. This page is the checkpoint runbook; the full bring-up and offline
playbook is the next step (see "Not done yet"). Design background:
[`DGX_SPARK_PLAN.md`](DGX_SPARK_PLAN.md) (§8a: running the stack on a Spark).

## Names and addresses

| | spark-1 (head) | spark-2 (worker) |
|---|---|---|
| Role | rank 0 + API + command center | rank 1, nothing else |
| Tailnet | `spark-1` 100.66.191.118 | `spark-2` 100.110.143.15 |
| LAN | 192.168.40.153 | 192.168.40.151 |
| ConnectX-7 rail 1 / rail 2 | 10.100.216.1 / 10.100.217.1 | 10.100.216.2 / 10.100.217.2 |
| NVIDIA Sync ssh alias | `spark-1` → `spark-3dc6.local` | `spark-2` → `spark-9f26.local` |

`spark-hydra` names the *pair*, never one box. If a `.local` name does not
resolve (seen right after a reboot), use the tailnet name or LAN IP:
`ssh -o HostName=192.168.40.153 spark-1`.

## What runs where

| Layer | Where | What |
|---|---|---|
| Inference | spark-1 `systemctl --user spark-hydra-inference` | `~/openbeast-recipes/glm53/start.sh` (FOREGROUND) — Mia's recipe v1.8 (`33b50fd`): TensorFold v0.6.0 + patches in image `tensorfold-glm53:v0.6.0` (= `ghcr.io/miaai-lab/glm-5.3-flash-exl3-2x-dgx-sparks-tensorfold@sha256:cbb4b3c6…`). Starts rank 1 on spark-2 over ssh. Settings: `scripts/local.sh` |
| API | spark-1 | `http://127.0.0.1:8000/v1`, model `GLM-5.3-Flash-EXL3`. **Loopback only: v0.6.0 has no API key.** |
| Command center | spark-1 `systemctl --user openbeast` | `~/openbeast` on `feat/spark-cluster`: WebUI :3000, tools :3001, SearXNG :8888, beast-gate :8090, island watcher. `openbeast.conf`: `INFERENCE_BACKEND=tensorfold`, `INFERENCE_URL=http://127.0.0.1:8000`, `INFERENCE_SLOTS=4`, `EDGE_GATE=true` |
| Weights | both | `~/openbeast-models/glm53-flash-ablit-exl3` (175.7 GB, 83 shards), hard-linked into `~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit/snapshots/57edefd2…`. Verified against the Hub revision (sha256, 0 mismatches) and locked: `scripts/backends/models/glm53-flash-ablit-exl3-tensorfold.lock` |
| Drafter | both | `incoai/GLM-5.3-Flash-DFlash2 @ bf582e4e` (CC BY-NC-ND — personal use, Max's call) |
| Native TensorFold 1.2.7 | both | `~/.local/bin/tensorfold` (signed release) — challenger engine, not serving |

## Serving configuration (measured 2026-11-13)

DFlash2, 4 parallel streams, 1M-token window, fp8 KV (pool ~2.2-2.6M tokens),
q4 dense, thinking off by default (Ablit), RoCE all-gathers on both rails.

| | 1 stream | 4 streams aggregate (each) | TTFT |
|---|---|---|---|
| prose | 43-44 tok/s | 145 tok/s (37) | ~160-250 ms |
| code | 68-70 tok/s | 224 tok/s (57) | ~170-260 ms |

MTP instead (`DRAFTER=mtp`): 44 / 56 tok/s single, but **one request at a
time** (4 queued requests wait 16-20 s). DFlash2 wins; kept. Not yet tried:
`PARALLEL=8` (needs a larger memory reserve; spark-1 has ~10 GiB free with the
command center up — unified memory freezes rather than OOMs).

## Day-to-day

```bash
# health (on spark-1)
~/openbeast/scripts/healthcheck.sh                   # expect "All 6 services healthy"
curl -s http://127.0.0.1:8000/health                 # streams, pool tokens
systemctl --user status spark-hydra-inference openbeast

# logs
journalctl --user -fu spark-hydra-inference          # rank 0 + start.sh
docker logs -f glm53-flash-tf                        # rank 0 container
ssh max@10.100.216.2 docker logs -f glm53-flash-tf   # rank 1

# restart / stop the pair (both ranks)
systemctl --user restart spark-hydra-inference
systemctl --user stop spark-hydra-inference

# restart the command center
systemctl --user restart openbeast
```

Changing a recipe setting: edit `~/openbeast-recipes/glm53/scripts/local.sh`,
then `systemctl --user restart spark-hydra-inference` (loads in 2-6 min).

## Failure modes seen on the way

| Symptom | Cause | Fix |
|---|---|---|
| WebUI/SearXNG DOWN, `.run/stack.log`: "docker daemon not ready" | the systemd user manager started before `max` joined the `docker` group | reboot once (`systemd-run --user --wait --pipe docker info` shows it) |
| ranks cannot reach each other | ufw (active by default) drops the peer | `spark-root-setup.sh` adds a per-link peer rule |
| `tensorfold capabilities` shows NCCL found:false | no libnccl2 | `spark-root-setup.sh` installs 2.28.9+cuda13.0 and holds it |
| `ssh spark-1` cannot resolve `spark-3dc6.local` | mDNS after reboot | `ssh -o HostName=192.168.40.153 spark-1` or the tailnet name |
| `model-fetch --verify` "unlocked file .cache/huggingface/…" | `hf download --local-dir` metadata | fixed: hidden paths are skipped in both lock and verify |

## Rebuild from scratch (short form)

1. Cable the CX-7 ports; NVIDIA Sync addresses both rails.
2. On each Spark: `sudo ~/openbeast/scripts/backends/spark-root-setup.sh spark-N`
   (NCCL, linger, Tailscale, ufw peer rules). Offline: `--debs DIR` with
   `apt-get download libnccl2=2.28.9-1+cuda13.0 tailscale` staged beforehand.
3. Weights to spark-1 (copy or Hub), then
   `rsync -a ~/openbeast-models/glm53-flash-ablit-exl3/ 10.100.216.2:openbeast-models/glm53-flash-ablit-exl3/`
   and `scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify` on each.
4. Recipe to `~/openbeast-recipes/glm53`, `scripts/local.sh` as on spark-1
   (WORKER=max@10.100.216.2, ABLIT=1, DRAFTER=dflash2, HOST=127.0.0.1,
   PORT=8000, HF_TOKEN from `~/.config/openbeast/hf-token`), `scripts/prepare.sh`.
5. Native TensorFold (optional): `scripts/backends/tensorfold/install-native.sh`
   (offline: `--stage DIR` on a connected box, `--from DIR` on the Spark).
6. `~/openbeast`: `openbeast.conf` as above, `./bootstrap.sh --no-start`,
   install `scripts/openbeast.service` and
   `scripts/backends/spark-hydra-inference.service` (headers say how), reboot once.

## Not done yet

- The full playbook: every artifact's offline staging (recipe image via
  `docker save`/`load`, DFlash2, OpenBeast's air-gap bundle built on aarch64,
  zig), and a guided walk-through for the rig's model.
- Remote access to the pair: `tailscale serve` of beast-gate on spark-1 and a
  `svc:spark-hydra` Tailscale Service name; the beast rig's hydra node for it.
- Switching the primary between `beast` and `spark-hydra` (clients + hydra).
- `PARALLEL=8` measurement; native TensorFold 1.2.7 vs the recipe.
- `tests/test_scripts.sh` showed 7 failures when run from a non-git copy on
  spark-2 (`test_uninstall.sh` among them) — rerun from a git checkout before merging.
