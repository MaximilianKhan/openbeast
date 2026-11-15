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
| Command center | spark-1 `systemctl --user openbeast` | `~/openbeast` (a git checkout; on 2026-11-15 spark-1 was on `feat/spark-scale-1-8` and spark-2 on `feat/spark-hydra-remote` — put both on the same commit before relying on the "same repo on every node" rule below): WebUI :3000, tools :3001, SearXNG :8888, beast-gate :8090, island watcher. `openbeast.conf`: `INFERENCE_BACKEND=tensorfold`, `INFERENCE_URL=http://127.0.0.1:8000`, `INFERENCE_SLOTS=4`, `EDGE_GATE=true` |
| Weights | both | `~/openbeast-models/glm53-flash-ablit-exl3` (175.7 GB, 83 shards), hard-linked into `~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit/snapshots/57edefd2…`. Verified against the Hub revision (sha256, 0 mismatches) and locked: `scripts/backends/models/glm53-flash-ablit-exl3-tensorfold.lock`. The recipe serves the **snapshot path**, so the lock vouches for what is served only while every file stays hard-linked (same inode, `stat -c %h` ≥ 2); a re-download or editor save that replaces a file by rename breaks the link silently |
| Drafter | both | `incoai/GLM-5.3-Flash-DFlash2 @ bf582e4e` (CC BY-NC-ND — personal use, Max's call), in the HF cache only (`hf download` layout: snapshot symlinks into `blobs/`). **No OpenBeast lock covers it** — check it with the blob-hash loop in "Offline staging" |
| Native TensorFold 1.2.7 | both | `~/.local/bin/tensorfold` (signed release) — challenger engine, not serving |

## Remote access (live 2026-11-15)

spark-1 is the pair's front door on the tailnet. Published with
`TS_HOSTNAME=spark-1 ./scripts/setup-tailscale.sh --publish-slot --publish-chat --publish-artifact --publish-searxng`
(as tailscaled's operator, so no sudo):

| URL | What | Auth |
|---|---|---|
| `https://spark-1.tail4109f9.ts.net` | Open WebUI | WebUI login (`WEBUI_AUTH=true`) |
| `https://spark-1.tail4109f9.ts.net:8443/v1` | inference, **through beast-gate** | per-device key (`scripts/clients.sh`) — 401 without |
| `:8444/api/slot` | beast-slot discovery | open, read-only |
| `:8445` | beast-chat console | tailnet login to read, chat-scoped device key to act |
| `:8446` | beast-artifact gallery | tailnet login (`ARTIFACT_OPERATORS`) |
| `:8889` | SearXNG for thin clients | open to the tailnet |

The model itself still listens on `127.0.0.1:8000` only; `:8443` is the one
inference door, and it always goes through the gate.

- **WebUI accounts:** `maxjkh@gmail.com` (admin; password in
  `~/.config/openbeast/webui-max-password` on spark-1, 0600 — change it in
  Settings → Account). The built-in `admin@localhost` had its default password
  rotated at the first auth-on start (`WEBUI_ADMIN_*` in `openbeast.conf`).
  The WebUI database is spark-1's own: accounts and chats from the beast rig
  do not carry over.
- **Device keys** (beast-gate): `macbook` and `phone` (`phone` also has the
  `chat` scope). Plaintext copies of both keys are in
  `~/.config/openbeast/gate-keys/` on spark-1 (0600); the MacBook's is also at
  `~/.config/openbeast/spark-hydra-gate.key` on the Mac. Lost device:
  `./scripts/clients.sh revoke <id>`. **Known issue:** the plaintext copies
  on spark-1 defeat the registry's hash-only design (`scripts/clients.sh`
  stores only SHA-256): they are readable by `max`, the uid the admin-tier
  `bash`/`read_file` tools run as, so a prompt-injected admin chat could read
  a working device key. Once each device holds its key, delete the copies on
  spark-1 (`shred -u ~/.config/openbeast/gate-keys/*.key`; re-enroll to
  recover one). The same goes for `webui-max-password` once it is changed.
- **A laptop in client mode:**
  `./scripts/setup-client.sh --host spark-1.tail4109f9.ts.net --api-key-stdin < key`.
- **ssh away from home:** the NVIDIA Sync `.local` names only resolve on the
  home LAN. The Mac's `~/.ssh/config` has `spark-1.tail4109f9.ts.net` and
  `spark-2.tail4109f9.ts.net` entries, with the Sync key and `HostKeyAlias`
  pointing at the known `.local` host keys.

**Not yet:** a stable `svc:spark-hydra` name. That is a Tailscale Service,
which has to be defined in the admin console (and spark-1 tagged) before
`tailscale serve --service=svc:spark-hydra` can advertise it. Until then the
pair is reached by spark-1's name.

## Firewall: spark-guard

**Found 2026-11-15:** ufw is installed but **disabled** on both Sparks
(`/etc/ufw/ufw.conf` `ENABLED=no`; `ufw enable` was never run), so nothing is
filtered. Rank 0's TensorFold TCPStore listens on `*:29551` with no
authentication and answered `nc -z 100.66.191.118 29551` from the Mac over the
tailnet (the LAN very likely too). Enabling ufw would not have closed the
tailnet path: Tailscale's own `ts-input` chain accepts `tailscale0` traffic
before ufw's rules. sshd also accepts passwords on `0.0.0.0:22`.

`scripts/backends/spark-guard.sh` closes the engine ports without touching
anything else. It loads one nftables table of its own (`inet
openbeast_guard`), input hook at priority `filter - 10` — ahead of
iptables-nft (ufw, Docker) and Tailscale — that drops TCP to 29501,
29551-29552, 29583-29584 (and spark.env's master ports, `--port N`) and to the
Spark's fabric addresses (NCCL's ephemeral bootstrap listeners), **unless** the
packet comes in on `lo` or a ConnectX netdev (`ibdev2netdev`). It never
flushes the ruleset (Ubuntu's `nftables.service` would, wiping Docker's and
Tailscale's rules, so it is not used); its own oneshot unit
`openbeast-spark-guard.service` reloads `/etc/openbeast/spark-guard.nft`
before `network-pre.target` at boot. RoCE (RDMA bypasses netfilter), the NCCL
bootstrap on the fabric, ssh, `tailscale serve` (:443, :8443-8447, :8889) and
Docker networking are unaffected.

```bash
# on EACH Spark (spark-1 and spark-2), from ~/openbeast at a commit that has it
scripts/backends/spark-guard.sh --render          # read the ruleset first (no root)
sudo scripts/backends/spark-guard.sh              # load + persist (idempotent)
sudo scripts/backends/spark-guard.sh --status     # table, counters, listeners, ssh posture
sudo scripts/backends/spark-guard.sh --remove     # undo

# from the Mac afterwards (while the pair is serving)
nc -z -w 3 100.66.191.118 29551 && echo STILL OPEN || echo closed    # must print closed
nc -z -w 3 192.168.40.153 29551 && echo STILL OPEN || echo closed    # LAN, from home
ssh spark-1.tail4109f9.ts.net true && curl -sk https://spark-1.tail4109f9.ts.net:8444/api/slot | head -c 80
```

The pair keeps serving through the load (the fabric is accepted before the
drops); after a reboot `systemctl status openbeast-spark-guard` shows
`active (exited)`, and the counters in `--status` count the drops.

**sshd hardening is opt-in and OFF by default:** `sudo
scripts/backends/spark-guard.sh --harden-ssh --yes` writes
`/etc/ssh/sshd_config.d/00-openbeast-guard.conf` (`PasswordAuthentication no`,
`KbdInteractiveAuthentication no`), validates it with `sshd -t` and reloads
sshd. It refuses unless your account has a key in `authorized_keys` (both
Sparks do: the Mac uses the NVIDIA Sync key, spark-1 → spark-2 uses its own),
and its warning says how not to lock yourself out: run it from an open ssh
session, then test a NEW key login from the Mac (and `ssh max@10.100.216.2
true` from spark-1) before closing it. `--unharden-ssh` undoes it.

## Parity with the beast rig (verified 2026-11-15)

`openbeast.conf` on spark-1 enables what the rig ran in production:
`WEBUI_AUTH=true`, `EDGE_GATE=true`, `EXTENSIONS="dashboard"`,
`BEAST_CHAT=true`, `BEAST_ARTIFACT=true`, with `maxjkh@gmail.com` as the
chat and artifact operator. `healthcheck.sh` reports all 9 services healthy.

| Check | Result |
|---|---|
| `scripts/backends/conformance.sh --model GLM-5.3-Flash-EXL3 --heavy` | PASS: chat, stream, tools, tool_result, 2 parallel calls, max_tokens |
| `./agent.sh` (create / edit / run a file) | done in 5 iterations, 0 API errors |
| a WebUI chat that calls `bash` through the `:3001` tool server | executed server-side, answered `aarch64` / `20` |
| remote: every surface above from off-LAN (phone hotspot) | 200; `:8443` 401 without a key, chat answers with one |
| `./scripts/doctor.sh` | 39 ok, 1 warning (SSD wear needs a sudoers rule) |

**Language assistance is OFF on the pair** (Max, 2026-11-15). Packs, the pack
context, beast-assist and escalation were measured for, and help, the 27B.
GLM-5.3-Flash needs far less help:

```
LANG_PACKS=off  LANG_PACK_CONTEXT=off  BEAST_ASSIST=0  BEAST_ESCALATE=0
```

`language_reference` stays available as a tool the model can choose to call.
Not enabled on the pair: `AGENT_ROUTER` (an intent classifier that exists
because the 27B would not call `start_agent` on its own) and `HYDRA` /
`INSTINCT` (their scorer is the rig's 27B).

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

## Scaling: 1 to 8 Sparks

The scripts handle a cluster of **1 to 8** DGX Sparks, one GPU each, one
tensor-parallel rank per Spark (so a profile's `TENSOR_PARALLEL_SIZE` is the
node count). **Only N = 1 and N = 2 have run on hardware** (our pair is the
N = 2 case); N >= 3 is built and tested against stubs but VERIFY ON HARDWARE.
For a 3-Spark GLM today, the recipe's own `start-tp3.sh` (experimental) is
the measured path.

**Cabling and addressing**

| Sparks | Fabric | Notes |
|---|---|---|
| 1 | none | `TENSOR_PARALLEL_SIZE=1`, rank 0 only |
| 2 | one QSFP cable | both PCIe "rails" of the port are used when both twins are addressed (NVIDIA Sync does it) |
| 3 | a switch, or one fabric subnet every Spark is on | a cabled ring with a subnet per cable cannot rendezvous through `spark-node.sh` (every rank meets at one `SPARK_HEAD_IP`); `spark-cluster.sh check` flags it. The GLM recipe's `start-tp3.sh` handles a ring (LAN rendezvous + `NCCL_IB_SUBNET_AWARE_ROUTING=1`) |
| 4-8 | a RoCE-capable 200 GbE switch, every node on the fabric subnet(s) | each Spark has two QSFP ports, so no cabled topology reaches past 3 |

**Bring-up, per node:** `sudo scripts/backends/spark-root-setup.sh spark-N --guard`
(its ufw rules admit each ConnectX link's own private subnet — inert while
ufw is disabled, as it is on DGX OS — and `--guard` runs `spark-guard.sh`,
which keeps the rendezvous ports on the fabric for a cable, a ring and a
switch alike; see "Firewall: spark-guard"), the repo at the **same commit** on every node (`check` compares HEADs;
`CLUSTER_REPO=~/…` expands on each node), and each node's own
`scripts/backends/spark.env` (`SPARK_IFACE` / `SPARK_IB_HCA` may list several
rails, comma-separated; every node sets the **same** `SPARK_HEAD_IP`, rank 0's
fabric address — `check` enforces it).

**From the head**, list the nodes in rank order in
`scripts/backends/cluster.env` (from `cluster.env.example`), then:

```bash
scripts/backends/spark-cluster.sh check                          # every node, same commit, fabric pairing + SPARK_HEAD_IP reach
scripts/backends/spark-cluster.sh sync-weights --profile NAME    # model + drafter over the fabric, verified vs the HEAD's lock
scripts/backends/spark-cluster.sh sync-image IMAGE               # docker save | load where it differs; prints the *_IMAGE_ID pin
scripts/backends/spark-cluster.sh start --profile NAME [--engine tensorfold|vllm]
scripts/backends/spark-cluster.sh status
scripts/backends/spark-cluster.sh stop [--engine tensorfold|vllm]   # every rank, no profile needed; both engines by default
```

`start` launches ranks N-1..1 and then 0, each with its own
`spark-node.sh --rank R`, and stops the ranks it started if a later rank
fails; `stop` goes 0 first and walks every rank even if one is already down.
`--dry-run` prints every command. `sync-weights` refuses unless the head's
lock pins every artifact, checks each worker's profile and lock hash-match the
head's (sending the head's lock to a worker that has none), copies, verifies,
and confirms the worker's own launcher will find the copy.

**Offline caveats (2026-11-15, open):**
- `docker save | docker load` keeps an image's ID but drops its RepoDigest,
  so a digest pin (`*_IMAGE_DIGEST`) would make an offline worker try a
  registry pull. **Pin by ID instead** — `VLLM_IMAGE_ID` / `TENSORFOLD_IMAGE_ID`
  in `spark.env` win over a digest, the container runs as `sha256:<id>`
  (docker never pulls an ID), and `sync-image` prints the exact line to pin.
  The GLM recipe is unaffected: it checks its image by tag (see "Offline
  staging").
- The container TensorFold engine (`TENSORFOLD_ENGINE=container`)
  pip-installs TensorFold from GitHub, and its dependencies from PyPI,
  unpinned, at every rank start: it needs the internet every time. The native
  engine (the default) and the GLM recipe do not.

**Which engine scales how far** (as of 2026-11-15):

| Engine | Ranks | |
|---|---|---|
| Mia-AiLab GLM recipe (TensorFold v0.6.0 + patches) | 2 (`start.sh`), 3 (`start-tp3.sh`, experimental) | what serves our pair; its own scripts drive the ranks |
| native TensorFold >= 1.0 (1.2.7) | GLM-5.3-Flash up to 16 (`TF_GLM_WORLD`); MiniMax-M3.1-Flash 2 only (`TF_MINIMAX_*`); Flash Next 1-2; Nemotron 1; other families `--tp` 2/4/8 | from the 1.2.7 binary's own messages and `capabilities --json`; `spark-node.sh` refuses sizes the engine refuses |
| container TensorFold 0.x (@9cd52ab) | per `data/tensorfold.json`: GLM 2, most families 1-2, qwen3_5_moe 1 | the launcher refuses an excluded size at that commit, warns at any other |
| vLLM (native multi-node) | `--nnodes N`, TP = N | the model's head count must divide by N |

Whether a model *fits* at N is a memory question: `model-inspect.sh` reports
the smallest N that holds the weights with KV room, judges ranks against the
native TensorFold engine, and lists the container engine's ranks separately.

**Several smaller groups instead of one big one:** e.g. 4 Sparks as two
independent pairs, each its own hydra node (`[nodes.pairA]`, `[nodes.pairB]`,
both in one route), trades the long single-request context of TP 4 for twice
the concurrent capacity.

## Day-to-day

```bash
# health (on spark-1)
~/openbeast/scripts/healthcheck.sh                   # expect "All 9 services healthy"
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
| ranks cannot reach each other | a firewall drops the peer on the fabric (ufw, if someone enables it without the fabric rules) | `spark-root-setup.sh` adds a ufw rule per Up link admitting that link's subnet; `spark-guard.sh` always accepts the fabric. ufw itself is **disabled** on DGX OS (`/etc/ufw/ufw.conf` `ENABLED=no`) — it was never what blocked or protected anything here |
| `nc -z <tailnet IP> 29551` succeeds from a laptop | rank 0's TCPStore binds `*:29551`, ufw is off, and Tailscale's `ts-input` accepts tailnet traffic first | `sudo scripts/backends/spark-guard.sh` on each Spark ("Firewall: spark-guard") |
| `tensorfold capabilities` shows NCCL found:false | no libnccl2 | `spark-root-setup.sh` installs 2.28.9+cuda13.0 and holds it |
| `ssh spark-1` cannot resolve `spark-3dc6.local` | mDNS after reboot | `ssh -o HostName=192.168.40.153 spark-1` or the tailnet name |
| `model-fetch --verify` "unlocked file .cache/huggingface/…" | `hf download --local-dir` metadata | fixed: hidden paths are skipped in both lock and verify |

## Rebuild from scratch (short form)

1. Cable the CX-7 ports; NVIDIA Sync addresses both rails.
2. On each Spark: `sudo ~/openbeast/scripts/backends/spark-root-setup.sh spark-N --guard`
   (NCCL, linger, Tailscale, ufw fabric-subnet rules — inert, ufw is
   disabled — and spark-guard, which is what closes the engine ports).
   Offline: `--debs DIR` (see "Offline staging").
3. Weights: on spark-1 (copy or Hub) into `~/openbeast-models/glm53-flash-ablit-exl3`,
   `scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify`
   (the lock is committed, so this re-hashes against it), then **hard-link it
   into the HF-cache layout the recipe reads** and copy both to spark-2 with
   `-H` — "Offline staging", B. The recipe never looks at `~/openbeast-models`.
4. Recipe to `~/openbeast-recipes/glm53`, `scripts/local.sh` as on spark-1
   (WORKER=max@10.100.216.2, ABLIT=1, DRAFTER=dflash2, HOST=127.0.0.1,
   PORT=8000, HF_TOKEN from `~/.config/openbeast/hf-token`), `scripts/prepare.sh`
   (online; offline: stage the image and DFlash2 as in "Offline staging", C/D,
   and leave `PREPARE=0`, as the unit does).
5. Native TensorFold (optional): `scripts/backends/tensorfold/install-native.sh`
   (offline: "Offline staging", E).
6. `~/openbeast`: `openbeast.conf` as above, `./bootstrap.sh --no-start`,
   install `scripts/openbeast.service` and
   `scripts/backends/spark-hydra-inference.service` (headers say how), reboot once.
   Offline: "Offline staging", F.

## Offline staging

Everything a Spark needs that does not come from the fabric, with what checks
it on arrival. Stage while a box (a Spark, where it says so) is online.

**A. libnccl2 + tailscale .debs** — on a Spark with apt's repos (apt checks
the repo signatures as it downloads):

```bash
mkdir debs && cd debs && apt-get download libnccl2=2.28.9-1+cuda13.0 tailscale=1.104.7 && cd ..
~/openbeast/scripts/backends/spark-root-setup.sh --check-debs debs        # no root: sha256 vs the pins
sudo ~/openbeast/scripts/backends/spark-root-setup.sh spark-N --debs debs --guard   # on the closed Spark
```

A `.deb` given by path skips apt's signature check, so `--debs` installs only
those exact versions and only if each matches the sha256 pinned in the script
(from the signed repo indexes). A newer tailscale means bumping
`TS_DEB_VERSION`/`TS_DEB_SHA256` there (`apt-cache show tailscale=<V> | grep SHA256`).

**B. The GLM weights in the recipe's HF-cache layout.** The recipe's
`snapshot()` reads only `$HF_CACHE/hub/models--<org>--<name>/snapshots/<rev>/`
(it checks `config.json` there, on every rank). On spark-1:

```bash
REV=57edefd2f5d9b371c8345883304d5af68b52fa24
C=~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit
mkdir -p "$C/refs" "$C/snapshots/$REV"
printf %s "$REV" > "$C/refs/main"
cp -al ~/openbeast-models/glm53-flash-ablit-exl3/. "$C/snapshots/$REV/"   # hard links, no copy
stat -c '%h %i %n' ~/openbeast-models/glm53-flash-ablit-exl3/config.json "$C/snapshots/$REV/config.json"  # same inode, links >= 2
```

Then both trees to spark-2 in ONE rsync, so the hard links survive (`-H`
only links files within one transfer; `--relative` with `/./` keeps the
paths under the remote home):

```bash
rsync -aH --partial --relative ~/./openbeast-models/glm53-flash-ablit-exl3 \
  ~/./.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit max@10.100.216.2:
ssh max@10.100.216.2 'cd ~/openbeast && scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify'
```

`--verify` on spark-2 needs the
lock in spark-2's checkout — it fails, rather than recording one, if it is
missing. It vouches for the snapshot only while the links hold: re-run the
`stat` line on each Spark after anything rewrites the cache.

**C. DFlash2 (the drafter), `incoai/GLM-5.3-Flash-DFlash2 @ bf582e4eacc1810f76656d1811693ff6c6737d2a`.**
It lives only in the HF cache, in `hf download` layout, and no OpenBeast lock
covers it. Copy the whole repo dir (relative symlinks into `blobs/` survive
`rsync -a`), then check every blob against its own name — LFS blobs are named
by their sha256, small files by their git blob SHA-1:

```bash
D=.cache/huggingface/hub/models--incoai--GLM-5.3-Flash-DFlash2
rsync -a --partial --relative ~/./$D max@10.100.216.2:     # or from a USB copy of the same dir
cd ~/$D/blobs && for b in *; do
  case ${#b} in
    64) [ "$(sha256sum < "$b" | cut -c1-64)" = "$b" ] || echo "BAD $b" ;;
    40) [ "$(git hash-object "$b")" = "$b" ] || echo "BAD $b" ;;
  esac
done; echo checked
readlink ~/$D/snapshots/bf582e4eacc1810f76656d1811693ff6c6737d2a/model.safetensors
# -> ../../blobs/b038e1d9d1e7833fa3880c2c0135ba9b673013f03da1b29fb831931584759dac (2.34 GB)
```

That proves the blobs are the bytes their names claim; that the snapshot is
the pinned revision rests on the names above (read off spark-1, 2026-11-15).

**D. The recipe image** (`tensorfold-glm53:v0.6.0`, ID
`sha256:ead0174052ac16b07d98fba9b68e7d7101b1e1db5c0ccdbde62f41ccf229c647`).
The recipe checks it by tag, so `docker load` satisfies it:

```bash
docker save tensorfold-glm53:v0.6.0 | ssh max@10.100.216.2 docker load      # spark-1 -> spark-2 over the fabric
docker save -o tensorfold-glm53-v0.6.0.tar tensorfold-glm53:v0.6.0 && sha256sum tensorfold-glm53-v0.6.0.tar   # to a stick
docker load -i tensorfold-glm53-v0.6.0.tar                                    # on the closed Spark
docker image inspect -f '{{.Id}}' tensorfold-glm53:v0.6.0                     # must print the ID above
```

(The ID compares across the Sparks because both use the same classic
overlay2 store; `scripts/bundle.sh` explains why it would not across store
kinds.) OpenBeast's own `spark-node.sh` launchers cannot use a loaded image
yet — see "Offline caveats" in "Scaling".

**E. Native TensorFold 1.2.7** (optional) — on any connected box, the Mac
included:

```bash
scripts/backends/tensorfold/install-native.sh --stage tf-1.2.7          # stages linux-aarch64 by default
scripts/backends/tensorfold/install-native.sh --from tf-1.2.7 --platform linux-aarch64   # on the Spark
```

Both ends verify: the `.sha256` must name exactly
`tensorfold-1.2.7-linux-aarch64.tar.gz`, and its ed25519 signature is checked
against the vendored key. That needs OpenSSL >= 3 or python3 `cryptography`;
on macOS the script finds Homebrew's `openssl@3` itself (LibreSSL and
OpenSSL 1.1 cannot check ed25519).

**F. The command center.** `./bootstrap.sh` needs PyPI and the compose images.
`scripts/bundle.sh build` packs both, but for the BUILD host's platform and
image store — a bundle built on the Mac holds macOS wheels and fails on a
Spark. Build it **on a Spark while it is online** (aarch64, the same overlay2
store) and keep it:

```bash
./scripts/bundle.sh build ~/openbeast-bundle      # on spark-1, online
./scripts/bundle.sh install ~/openbeast-bundle    # on the closed Spark (same commit)
```

Off a Spark, only the wheels can be cross-built: `./scripts/pydeps.sh
wheelhouse wheels-spark` with the manylinux aarch64 flags it prints, then
`./scripts/pydeps.sh install --from wheels-spark` on the Spark; the three
compose images still have to come from a Spark-side `docker save` (they are
multi-arch at the pinned digests, so a connected Spark pulls the arm64 ones).
`bootstrap.sh`'s OFFLINE message ("bundle.sh build on a connected box") does
not say the box must be aarch64 — known issue below.

## Known issues and errata (v1.9.0)

Corrections to `RELEASE_NOTES_v1.9.0.md` and to this page as first published,
from the 2026-11-15 review:

- **ufw was never active.** The release notes and this page said ufw was
  "active by default" and that the root setup's rules were what let the ranks
  talk while keeping the LAN and tailnet out. ufw is installed but disabled on
  both Sparks, so the rules filtered nothing, and the unauthenticated
  rendezvous (29551) was reachable from the tailnet. Fixed by
  `spark-guard.sh` ("Firewall: spark-guard") — **run it on both Sparks**.
- **sshd accepts passwords** on both Sparks; `max` is in `sudo` and `docker`.
  Opt-in `spark-guard.sh --harden-ssh`.
- **Offline images for `spark-node.sh`:** not offline-capable yet (digest
  refs vs loaded images) — "Offline caveats" in "Scaling". The container
  TensorFold engine needs GitHub and PyPI at every start.
- **The served snapshot is not what the lock names.** The recipe serves the
  HF-cache snapshot; the lock covers `~/openbeast-models/...`. They are the
  same bytes only while hard-linked (B). DFlash2 is outside every lock (C).
- **Plaintext device keys on spark-1** (see "Remote access"): delete them
  once the devices hold their keys.
- **The recipe container runs with `--network host`**, as root, from
  third-party alpha code: anything in it can reach `127.0.0.1:8000` (no key,
  no gate audit) and the loopback-trusting beast-chat / beast-artifact ports
  (`OPENBEAST_CHAT_LOGIN_FROM=loopback` trusts any local TCP peer's
  `Tailscale-User-Login`). Not fixed here; the mitigation belongs in
  beast-chat's login source and in the recipe's network mode.
- **`bootstrap.sh` OFFLINE hint** points at `bundle.sh build` "on a connected
  box" without saying it must be an aarch64 Spark (F).

## Not done yet

- A guided walk-through for the rig's model; zig for `language_reference` on
  a Spark (DGX_SPARK_PLAN.md §8a).
- A `svc:spark-hydra` Tailscale Service name (admin-console step), and the
  beast rig's hydra node for the pair (`https://spark-1.tail4109f9.ts.net:8443`,
  `engine = "openai"`, a gate device key), once the rig is back on.
- Switching the primary between `beast` and `spark-hydra` (clients + hydra).
- `PARALLEL=8` measurement; native TensorFold 1.2.7 vs the recipe.
- `tests/test_scripts.sh` showed 7 failures when run from a non-git copy on
  spark-2 (`test_uninstall.sh` among them) — rerun from a git checkout before merging.
