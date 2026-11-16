# spark-hydra: the DGX Spark launch runbook

**Status, 2026-11-15: live.** GLM-5.3-Flash (Mia-AiLab EXL3 4-bit, Ablit
weights) serves tensor-parallel across `spark-1` + `spark-2`, with the
OpenBeast command center on `spark-1`. Both come back by themselves after a
reboot. `healthcheck.sh` on spark-1 reports all 9 services healthy.

This page takes you from bare boxes to a serving pair, start to finish, and then
covers day-2 operations and fully offline operation. Read it top to bottom the
first time. After that, jump to the section you need. Design background is in
[`DGX_SPARK_PLAN.md`](DGX_SPARK_PLAN.md) (§8a: the stack on a Spark; §14:
onboarding a model; §15: why GLM-5.3-Flash). Engine choice is in
[`ENGINES.md`](ENGINES.md). For the local model working on OpenBeast itself,
see [`SELF_DEVELOPMENT.md`](SELF_DEVELOPMENT.md).

**How to read the labels.** **Measured** means it ran on our pair and the
result is recorded here. **VERIFY ON HARDWARE** means the code exists and is
tested against stubs, but has never run on a real Spark. Every command on this
page exists in the code on `feat/spark-scale-1-8`. Flags were checked against
each script's `--help` on 2026-11-15.

| Section | What it gets you |
|---|---|
| [0. The pair at a glance](#0-the-pair-at-a-glance) | names, addresses, what runs where |
| [A. What you need](#a-what-you-need) | hardware, cabling for 1 / 2 / 3 / 4-8 Sparks, accounts, the offline staging checklist |
| [B. Bare boxes to root setup](#b-bare-boxes-to-root-setup) | `spark-root-setup.sh`, `spark-guard.sh`, checking them from another device |
| [C. Weights](#c-weights) | fetch or copy, lock, hard-link into the recipe's HF-cache layout, carry to workers |
| [D. Inference](#d-inference) | D1 the Mia recipe (what serves today), D2 native TensorFold through `spark-node.sh` / `spark-cluster.sh`, D3 the engine plane |
| [E. The command center on spark-1](#e-the-command-center-on-spark-1) | bootstrap, `openbeast.conf`, boot units, WebUI admin, tailnet publishing, device keys |
| [F. The Mac client](#f-the-mac-client) | `setup-client.sh`, the opencode v2 config layout, beast-apps, beast-docs |
| [G. Day-2 operations](#g-day-2-operations) | health, logs, restarts, memory, watchdog, updates, keys, troubleshooting |
| [H. Fully offline operation](#h-fully-offline-operation) | island and `OFFLINE=true`, what keeps working, staging every artifact |
| [Reference](#reference) | serving numbers, scaling notes, parity checks, known issues, not done yet |

---

## 0. The pair at a glance

### Names and addresses

| | spark-1 (head) | spark-2 (worker) |
|---|---|---|
| Role | rank 0 + API + command center | rank 1, nothing else |
| Tailnet | `spark-1` 100.66.191.118 (`spark-1.tail4109f9.ts.net`) | `spark-2` 100.110.143.15 (`spark-2.tail4109f9.ts.net`) |
| LAN (home) | 192.168.40.153 | 192.168.40.151 |
| ConnectX-7 rail 1 / rail 2 | 10.100.216.1 / 10.100.217.1 | 10.100.216.2 / 10.100.217.2 |
| Hostname / mDNS | `spark-3dc6` / `spark-3dc6.local` | `spark-9f26` / `spark-9f26.local` |
| NVIDIA Sync ssh alias | `spark-1` → `spark-3dc6.local` | `spark-2` → `spark-9f26.local` |

`spark-hydra` names the **pair**, never one box.

How to reach each box:

- **At home:** `ssh spark-1` works through NVIDIA Sync's `.local` alias.
- **Right after a reboot:** mDNS sometimes does not resolve. Use the LAN IP:
  `ssh -o HostName=192.168.40.153 spark-1`.
- **Away from home:** `.local` names never resolve. Use the tailnet names; see
  the ssh config in [F](#f1-ssh-from-the-mac).
- **spark-1 to spark-2:** use the fabric address, `ssh max@10.100.216.2`.
  Control traffic between the ranks never needs the LAN.

### What runs where

| Layer | Where | What |
|---|---|---|
| Inference | spark-1, `systemctl --user` unit `spark-hydra-inference` | Runs `~/openbeast-recipes/glm53/start.sh` in the FOREGROUND. This is Mia's recipe v1.8 (`33b50fd`): TensorFold v0.6.0 plus patches, in image `tensorfold-glm53:v0.6.0` (= `ghcr.io/miaai-lab/glm-5.3-flash-exl3-2x-dgx-sparks-tensorfold@sha256:cbb4b3c6…`). It starts rank 1 on spark-2 over ssh. Its settings are in `scripts/local.sh` ([D1](#d1-the-mia-recipe-what-serves-today)) |
| API | spark-1 | `http://127.0.0.1:8000/v1`, model `GLM-5.3-Flash-EXL3`. **Loopback only: v0.6.0 has no API key** |
| Command center | spark-1, `systemctl --user` unit `openbeast` | `~/openbeast`, a git checkout: WebUI :3000, tools :3001, SearXNG :8888, beast-gate :8090, beast-chat, beast-artifact, the dashboard (beast-slot), the island watcher. `openbeast.conf`: [E2](#e2-openbeastconf) |
| Weights | both | `~/openbeast-models/glm53-flash-ablit-exl3` (175.7 GB, 83 shards), hard-linked into `~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit/snapshots/57edefd2…`. Verified against the Hub revision (sha256, 0 mismatches) and locked in `scripts/backends/models/glm53-flash-ablit-exl3-tensorfold.lock` ([C](#c-weights)) |
| Drafter | both | `incoai/GLM-5.3-Flash-DFlash2 @ bf582e4e` (CC BY-NC-ND: personal use, Max's call). It lives in the HF cache only, in `hf download` layout (snapshot symlinks into `blobs/`). **No OpenBeast lock covers it**; [C4](#c4-the-drafter-dflash2) shows how to check it |
| Native TensorFold 1.2.7 | both | `~/.local/bin/tensorfold` (signed release). A challenger engine, **not serving** ([D2](#d2-native-tensorfold-through-spark-nodesh--spark-clustersh)) |
| spark-guard | both | `openbeast-spark-guard.service` (system unit, `active (exited)`): an nftables table that keeps the engines' rendezvous ports on the fabric ([B3](#b3-firewall-spark-guard)) |

**Checkout skew, 2026-11-15.** spark-1's `~/openbeast` was at `ecf50a2` and
spark-2's at `0988641`, both on `feat/spark-scale-1-8` but behind its head.
`scripts/engine.sh` does not exist at either commit yet. Before relying on the
"same repo on every node" rule (`spark-cluster.sh check` enforces it), put both
on the same commit. Run this on each Spark, quoting the command for ssh:
`ssh spark-2.tail4109f9.ts.net 'cd ~/openbeast && git pull --ff-only'`.
Restarting the command center afterwards (`systemctl --user restart openbeast`)
is what makes a new commit live. Do it at a quiet moment.

---

## A. What you need

### A1. Hardware

| Item | Per Spark | Notes |
|---|---|---|
| NVIDIA DGX Spark (GB10, 128 GB unified memory, aarch64, DGX OS = Ubuntu 24.04) | 1 | the driver is R580 = CUDA 13.0. `nvidia-smi --query-gpu=memory.*` reports `[N/A]`, and that is how `scripts/lib/hardware.sh` classifies the host as `spark` |
| Disk free | ≥ 400 GB on the head, ≥ 250 GB on a worker | the GLM checkpoint is 175.7 GB. The HF-cache hard links cost nothing extra. DFlash2 is 2.3 GB, the recipe image tens of GB, and a carried image tarball the same again while it is staged |
| QSFP / ConnectX-7 cable (200 GbE) | see A2 | the fabric: NCCL over RoCE, the ranks' rendezvous, weight sync |
| Ethernet to the LAN | 1 | first boot, ssh at home, apt. Not needed by the ranks |
| A Mac (or Linux laptop) | — | NVIDIA Sync for first-boot addressing and the ssh alias, plus OpenBeast client mode |
| USB stick / external SSD | — | only for offline staging ([H](#h-fully-offline-operation)). Size it for the weights (≥ 256 GB) if you carry them |

### A2. Cabling and addressing for 1, 2, 3 and 4-8 Sparks

The scripts handle **1 to 8** Sparks, one GPU each, one tensor-parallel rank per
Spark. A profile's `TENSOR_PARALLEL_SIZE` therefore equals the node count.
**Only N = 1 and N = 2 have run on hardware.** Our pair is the N = 2 case.
N ≥ 3 is built and tested against stubs: VERIFY ON HARDWARE.

| Sparks | Fabric | Addressing | Notes |
|---|---|---|---|
| 1 | none | none | `TENSOR_PARALLEL_SIZE=1`, rank 0 only. `spark-guard.sh --no-fabric` makes the engine ports loopback-only |
| 2 | one QSFP cable between the two Sparks | NVIDIA Sync addresses both PCIe "rails" of the port (`10.100.216.0/24` and `10.100.217.0/24` on our pair) | both rails carry the all-gathers when both twins are addressed. **Measured** |
| 3 | a switch, or one fabric subnet that every Spark is on | every node on one subnet | a cabled ring with a subnet per cable cannot rendezvous through `spark-node.sh`, because every rank meets at one `SPARK_HEAD_IP`. `spark-cluster.sh check` flags this. The GLM recipe's `start-tp3.sh` (experimental) does handle a ring: LAN rendezvous plus `NCCL_IB_SUBNET_AWARE_ROUTING=1`. That is the measured 3-Spark path for GLM today |
| 4-8 | a RoCE-capable 200 GbE switch | every node on the fabric subnet(s) | each Spark has two QSFP ports, so no cabled topology reaches past 3 |

After cabling, check on each Spark that the links are Up and addressed:

```bash
ibdev2netdev                      # each ConnectX netdev and "(Up)"
ip -4 -o addr show | grep 10.100  # the fabric addresses NVIDIA Sync assigned
```

### A3. Accounts and credentials

| What | Why | Where it lives (never in git, never on argv) |
|---|---|---|
| Linux user (`max`) on every Spark, in `sudo` and `docker` | everything runs as this user; root only for [B](#b-bare-boxes-to-root-setup) | — |
| An ssh key from the Mac, and one from spark-1 to every worker | NVIDIA Sync installs the Mac's key. The head's key is what lets `start.sh` and `spark-cluster.sh` reach the workers over the fabric | `~/.ssh/` |
| Hugging Face account and token | downloading the weights. Only needed once, while the box is online | `~/.config/openbeast/hf-token` (0600). The recipe's `local.sh` reads it from there. `spark.env` names it with `HF_TOKEN_FILE` |
| Tailscale account (the tailnet `tail4109f9.ts.net`) | remote access, client mode, the published URLs | joined by `spark-root-setup.sh` (`tailscale up`, which prints a login URL on first run) |
| GitHub access to the repo | cloning `openbeast` | — |
| Open WebUI admin | the browser chat | created in [E5](#e5-webui-auth-and-the-admin-account-before-publishing) |
| beast-gate device keys | every client that calls inference | `scripts/clients.sh enroll` prints each key once ([E7](#e7-device-keys-for-beast-gate)) |

### A4. The artifact checklist (what to stage for an offline build)

Everything a Spark needs that does not come over the fabric, where it comes
from, and what checks it when it arrives. Section [H](#h-fully-offline-operation)
has the commands.

| Artifact | Size | Comes from | Checked by | Section |
|---|---|---|---|---|
| `libnccl2 2.28.9-1+cuda13.0` and `tailscale 1.104.7` .debs | ~200 MB | `apt-get download` on an online Spark | sha256 pins in `spark-root-setup.sh` (`--check-debs`) | [H3](#h3-debs-libnccl2-and-tailscale) |
| The OpenBeast repo at one commit | ~100 MB | `git clone` / `git bundle` | the commit id | [H2](#h2-the-repo) |
| GLM-5.3-Flash Ablit EXL3 weights | 175.7 GB | the Hub at `57edefd2…` | `model-fetch.sh --verify` against the committed lock | [C](#c-weights) |
| DFlash2 drafter | 2.3 GB | the Hub at `bf582e4e…` | the blob-hash loop | [C4](#c4-the-drafter-dflash2) |
| Recipe image `tensorfold-glm53:v0.6.0` | tens of GB | `prepare.sh`, or `docker pull` from ghcr | image ID `sha256:ead01740…` after `docker load` | [H4](#h4-container-images) |
| The recipe checkout `~/openbeast-recipes/glm53` | small | git (MiaAI-Lab) | commit `33b50fd` | [D1](#d1-the-mia-recipe-what-serves-today) |
| Native TensorFold 1.2.7 (optional) | ~100 MB | GitHub release | ed25519 signature + SHA256SUMS (`install-native.sh --stage` / `--from`) | [H5](#h5-native-tensorfold-and-the-engine-plane) |
| Python wheels for the command center | ~200 MB | `pydeps.sh wheelhouse` or `bundle.sh build` **on a Spark** | `agents/requirements.lock` hashes | [H6](#h6-the-command-center-wheels-and-compose-images) |
| Compose images (open-webui, searxng, extensions) | a few GB | `docker save` on an online Spark | digest pins; `bundle.sh install` rewrites them to the content ID | [H6](#h6-the-command-center-wheels-and-compose-images) |
| pytest (to run the test suites) | small | `.github/ci-requirements/test.txt` | `--require-hashes` | [H7](#h7-test-and-language-toolchains) |
| beast-docs mirror | MBs | `scripts/docs.sh sync` + `pack` | `unpack` re-hashes against `BUNDLE.json` | [H8](#h8-beast-docs-mirror) |
| zig 0.16 aarch64 (optional) | ~50 MB | ziglang.org | sha256 `ea4b09bf…` | [H7](#h7-test-and-language-toolchains) |

---

## B. Bare boxes to root setup

### B1. First boot

1. Boot each Spark, create the user (`max`), and connect the LAN.
2. Pair each Spark with NVIDIA Sync on the Mac. Sync addresses the ConnectX
   rails (it owns `/etc/netplan/99-nvidia-sync-cluster.yaml`; nothing in
   OpenBeast edits it) and installs the ssh alias and key.
3. Clone the repo on **every** Spark, at the **same commit**:

   ```bash
   git clone https://github.com/MaximilianKhan/openbeast ~/openbeast
   cd ~/openbeast && git checkout feat/spark-scale-1-8   # or whatever branch/commit the cluster runs
   git log --oneline -1                                 # write it down; every node must match
   ```

4. Give the head an ssh key that reaches every worker over the fabric. The
   recipe's `start.sh` and `spark-cluster.sh` use it:

   ```bash
   # on spark-1
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ''        # only if it has none
   ssh-copy-id max@10.100.216.2
   ssh max@10.100.216.2 true && echo fabric-ssh-ok
   ```

### B2. `spark-root-setup.sh`: the only root step (once per Spark)

```bash
cd ~/openbeast
sudo scripts/backends/spark-root-setup.sh spark-1 --guard     # on spark-1 (the head)
sudo scripts/backends/spark-root-setup.sh spark-2 --guard     # on spark-2 (each worker: spark-N)
```

The argument is the **tailnet name** the box joins as. Run it as yourself
through sudo, because it needs `SUDO_USER`. It is idempotent. It does four
things and nothing else:

1. **libnccl2 `2.28.9-1+cuda13.0`**, installed and held. The native TensorFold
   binary dlopens `libnccl.so.2`; without it, `tensorfold capabilities --json`
   shows NCCL `found:false`. The driver is R580 = CUDA 13.0, so this is the
   +cuda13.0 build, not apt's newest +cuda13.5.
2. **`loginctl enable-linger` for you**, so `systemctl --user` services run
   without a login session and survive logout.
3. **Tailscale from its signed apt repo.** The repo key is checked against the
   pinned fingerprint `2596A99EAAB33821893C0A79458CA832957F5868` before apt
   trusts it. Then it runs `tailscale up` with the given name and
   `--operator=$SUDO_USER`, so you can run `tailscale serve` without sudo.
   The first run prints a login URL.
4. **ufw allow rules** for each ConnectX link that is Up, from that link's own
   private subnet, for the day ufw gets enabled. **ufw is installed but
   disabled on DGX OS**, and this script does not enable it, because its
   default deny would also drop LAN ssh. While ufw is off those rules filter
   nothing, and the script prints a warning that says so. `--guard` then runs
   `spark-guard.sh` ([B3](#b3-firewall-spark-guard)), which is
   what actually closes the engine ports.

Other flags:

- `--no-tailscale` skips step 3, for a box with no tailnet to join.
- `--debs DIR` and `--check-debs DIR` are the offline path ([H3](#h3-debs-libnccl2-and-tailscale)).
- `--check-keyring FILE` checks a Tailscale keyring file against the pin, without root.
- `--serve [--port N]` adds a ufw rule for a **keyed** engine on `tailscale0`.
  Leave it off. Inference stays on loopback behind beast-gate.

What it never touches: netplan, the MTU, the driver, Docker.

**Expected output.** This is an excerpt assembled from the script's own
messages, not a captured transcript:

```
==> 1/4 libnccl2 2.28.9-1+cuda13.0
already installed                                   # or apt's install log, then the hold
libnccl.so.2 visible to the loader

==> 2/4 linger for max

==> 3/4 Tailscale as 'spark-1'
  Tailscale repo key verified (2596A99EAAB33821893C0A79458CA832957F5868)
tailnet IPv4: 100.66.191.118

==> 4/4 ufw (fabric allow rules) and exposure
  ufw fabric rules on 2 link(s)

  !! ufw is inactive — the ufw rules above are recorded but FILTER NOTHING.
  ...
  !!   sudo …/spark-guard.sh            (or re-run this with --guard)

==> spark-guard
== spark-guard installed: /etc/openbeast/spark-guard.nft, openbeast-spark-guard.service (enabled; loads before the network at boot)
  note: ufw is inactive. spark-guard does not need it and does not enable it.
Verify from the Mac: nc -z -w 3 <this Spark's tailnet IP> 29551   # must FAIL while a rank listens

Done on spark-3dc6 as tailnet 'spark-1' (100.66.191.118).
```

**Verify:**

```bash
dpkg-query -W libnccl2                          # 2.28.9-1+cuda13.0
apt-mark showhold | grep nccl                   # libnccl2
loginctl show-user "$USER" -p Linger            # Linger=yes
tailscale status | head -3                      # this box, online
tailscale ip -4
```

**The docker group and the user manager (reboot once).** If you were added to
the `docker` group during setup (`sudo usermod -aG docker max`), the systemd
**user** manager that runs `openbeast` and `spark-hydra-inference` was started
before the group change and cannot reach Docker. The symptoms are WebUI and
SearXNG DOWN, and `.run/stack.log` saying "docker daemon not ready". Logging
out is not enough, because linger keeps the user manager alive. **Reboot once.**
Then check:

```bash
systemd-run --user --wait --pipe docker info >/dev/null && echo user-manager-sees-docker
```

### B3. Firewall: spark-guard

**Why it exists (found 2026-11-15).** ufw is installed but **disabled** on both
Sparks (`/etc/ufw/ufw.conf` has `ENABLED=no`), so nothing was filtered. Rank 0's
TensorFold TCPStore listens on `*:29551` with no authentication, and it answered
`nc -z 100.66.191.118 29551` from the Mac over the tailnet (very likely the LAN
too). Enabling ufw would not have closed the tailnet path, because Tailscale's
own `ts-input` chain accepts `tailscale0` traffic before ufw's rules run. sshd
also accepts passwords on `0.0.0.0:22`.

**What it does.** `scripts/backends/spark-guard.sh` closes the engine ports and
touches nothing else:

- It loads one nftables table of its own, `inet openbeast_guard`, with an input
  hook at priority `filter - 10`. That runs ahead of iptables-nft (ufw, Docker)
  and Tailscale.
- It drops TCP to 29501, 29551-29552 and 29583-29584 (plus spark.env's master
  ports and any `--port N`). It also drops TCP to the Spark's own fabric
  addresses, where NCCL's ephemeral bootstrap listeners are. Both drops apply
  **unless** the packet arrives on `lo` or on a ConnectX netdev
  (`ibdev2netdev`).
- It never flushes the ruleset. Ubuntu's `nftables.service` would flush it,
  wiping Docker's and Tailscale's rules, so that service is not used. Instead
  its own oneshot unit, `openbeast-spark-guard.service`, reloads
  `/etc/openbeast/spark-guard.nft` before `network-pre.target` at boot.

Unaffected: RoCE (RDMA bypasses netfilter), the NCCL bootstrap on the fabric,
ssh, `tailscale serve` (:443, :8443-8447, :8889) and Docker networking.

```bash
# on EACH Spark, from ~/openbeast at a commit that has it
scripts/backends/spark-guard.sh --render          # read the ruleset first (no root)
sudo scripts/backends/spark-guard.sh              # load + persist (idempotent); same as --apply
sudo scripts/backends/spark-guard.sh --status     # table, counters, listeners, ssh posture
sudo scripts/backends/spark-guard.sh --remove     # undo: table, unit and rules file gone
#   --port N (repeatable)  --iface IF (repeatable)  --no-fabric (one Spark, no peer: loopback-only)
```

What `--render` prints on spark-1 (2026-11-15):

```
# fabric: enp1s0f0np0 enp1s0f1np1 enP2p1s0f0np0 enP2p1s0f1np1   fabric IPv4: 10.100.216.1 10.100.217.1   guarded TCP: 29501 29551-29552 29583-29584
table inet openbeast_guard {
	chain input {
		type filter hook input priority filter - 10; policy accept;
		iifname "lo" accept
		iifname { "enp1s0f0np0", "enp1s0f1np1", "enP2p1s0f0np0", "enP2p1s0f1np1" } accept comment "ConnectX-7 fabric"
		tcp dport { 29501, 29551-29552, 29583-29584 } counter drop comment "engine rendezvous: fabric and loopback only"
		ip daddr { 10.100.216.1, 10.100.217.1 } meta l4proto tcp counter drop comment "fabric addresses (NCCL bootstrap): fabric only"
	}
}
```

`--status` without root still shows the unit and the listeners. It says
`loaded: unknown (listing nftables needs root …)` for the table:

```
== spark-guard on spark-3dc6
rules file: /etc/openbeast/spark-guard.nft
unit: openbeast-spark-guard.service enabled, active
TCP listeners (what each could be reached on):
  0.0.0.0:22
  100.66.191.118%tailscale0:443      … :8443 :8444 :8445 :8446 :8889   (tailscale serve)
  10.100.216.1:35335 …                                               (NCCL bootstrap, fabric only)
  127.0.0.1:3000 :3001 :3002 :3003 :3004 :8000 :8090 :8888           (the stack, loopback)
```

**Verify from another device** (the Mac, while the pair is serving):

```bash
nc -z -w 3 100.66.191.118 29551 && echo STILL OPEN || echo closed    # must print closed (measured 2026-11-15: closed)
nc -z -w 3 192.168.40.153 29551 && echo STILL OPEN || echo closed    # LAN, from home
nc -z -w 3 100.66.191.118 22 && echo ssh-still-reachable              # ssh must stay open
curl -sk https://spark-1.tail4109f9.ts.net:8444/api/slot | head -c 80 # tailscale serve still works
```

The pair keeps serving through the load, because the fabric is accepted before
the drops. After a reboot, `systemctl status openbeast-spark-guard` shows
`active (exited)`, and the counters in `sudo … --status` count the drops.
Re-run the guard whenever the fabric addresses change (NVIDIA Sync
re-addressing, a new switch).

**sshd hardening is opt-in and OFF by default.**
`sudo scripts/backends/spark-guard.sh --harden-ssh --yes` writes
`/etc/ssh/sshd_config.d/00-openbeast-guard.conf` (`PasswordAuthentication no`,
`KbdInteractiveAuthentication no`), validates it with `sshd -t`, and reloads
sshd. It refuses unless your account has a key in `authorized_keys`. Both
Sparks have one: the Mac uses the NVIDIA Sync key, and spark-1 to spark-2 uses
spark-1's own key. Its warning explains how not to lock yourself out:

1. Run it from an open ssh session.
2. From the Mac, test a NEW key login before closing that session.
3. From spark-1, also test `ssh max@10.100.216.2 true`.

`--unharden-ssh` undoes it.

---

## C. Weights

The recipe and OpenBeast's own launchers read weights in two different places.
Getting this wrong is the most common way to serve unverified bytes, so here is
the model:

- **OpenBeast's copy** is `~/openbeast-models/<profile>`. `model-fetch.sh`
  writes it, verifies it against the Hub's own hashes for one commit, and
  **locks** it: `scripts/backends/models/<profile>.lock` records each file, its
  size and its sha256. The lock is committed.
- **The recipe's copy** is the HF-cache layout,
  `~/.cache/huggingface/hub/models--<org>--<name>/snapshots/<rev>/`. The
  recipe's `snapshot()` reads only that, and it checks `config.json` there on
  every rank.
- **Hard links make them the same bytes.** Then the lock vouches for what the
  recipe serves. A re-download, or an editor save that replaces a file by
  rename, breaks the link silently. Check `stat -c %h` (≥ 2) after anything
  touches the cache.

### C1. Fetch (online) or verify a local copy

Profile: `scripts/backends/models/glm53-flash-ablit-exl3-tensorfold.env`, with
its lock committed next to it.

```bash
cd ~/openbeast
# online, from the Hub: downloads only the pinned REVISION, verifies, renames into place
HF_TOKEN_FILE=~/.config/openbeast/hf-token scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold

# a copy that is already on disk (a USB SSD, rsync from another box): re-hash against the committed lock
scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify
scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --locate   # what launchers call: fast check, prints the dir
```

Its guarantees:

- Only the profile's `REVISION` (a commit SHA) is ever requested.
- Every file is checked against the Hub's hash for that commit: LFS files by
  sha256, small files by git blob SHA-1.
- Nothing appears under the final name until every file has passed. Downloads
  stage in `MODELS_DIR/.NAME.partial`, and an interrupted run resumes.
- The lock is written on the first fetch and never rewritten. Every later
  fetch or verify must reproduce it exactly.
- Pickles, GGUF and `original/` copies are skipped unless `FETCH_INCLUDE` names
  them.

Exit codes:

- `--verify` and `--locate` exit **3** when there is no lock. Run them in a
  checkout that has the committed lock.
- For a local `SOURCE` with no lock, `--verify --trust-first-use` records a
  lock from the bytes on disk. That is "trust on first use": say so in the
  commit.

`--verify` used to fail with "unlocked file .cache/huggingface/…" on a copy
made with `hf download --local-dir`. This is fixed: hidden paths are skipped by
both lock and verify.

### C2. Hard-link into the HF-cache layout the recipe reads

On spark-1:

```bash
REV=57edefd2f5d9b371c8345883304d5af68b52fa24
C=~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit
mkdir -p "$C/refs" "$C/snapshots/$REV"
printf %s "$REV" > "$C/refs/main"
cp -al ~/openbeast-models/glm53-flash-ablit-exl3/. "$C/snapshots/$REV/"   # hard links, no copy
stat -c '%h %i %n' ~/openbeast-models/glm53-flash-ablit-exl3/config.json "$C/snapshots/$REV/config.json"  # same inode, links >= 2
```

### C3. Carry the weights to every worker

There are two ways, and both keep the hard links.

**By hand (what built our pair).** Send both trees in ONE rsync. `-H` only
links files within one transfer, and `--relative` with `/./` keeps the paths
under the remote home:

```bash
rsync -aH --partial --relative ~/./openbeast-models/glm53-flash-ablit-exl3 \
  ~/./.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit max@10.100.216.2:
ssh max@10.100.216.2 'cd ~/openbeast && scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify'
```

`--verify` on spark-2 needs the lock in spark-2's own checkout. If it is
missing, `--verify` fails rather than recording one. The verify vouches for the
snapshot only while the links hold, so re-run the `stat` line from C2 on each
Spark after anything rewrites the cache.

**With `spark-cluster.sh sync-weights`.** This is the general 1-8 node path. It
needs `scripts/backends/cluster.env` on the head (see
[D2](#d2-native-tensorfold-through-spark-nodesh--spark-clustersh)):

```bash
scripts/backends/spark-cluster.sh sync-weights --profile glm53-flash-ablit-exl3-tensorfold [--dry-run]
```

What it does:

- It refuses unless the head's lock pins every artifact (the model, plus the
  drafter if the profile names one).
- It checks that each worker's profile and lock hash-match the head's. A worker
  that has no lock gets the head's.
- It copies over the fabric, verifies on arrival, and confirms the worker's own
  launcher will find the copy.

It syncs `~/openbeast-models/<profile>` (OpenBeast's layout). For the recipe,
still make the HF-cache hard links on each worker as in C2. On our pair the
hand-made rsync above did both at once. Untested on our pair, since the pair
has no `cluster.env`; tested against stubs in `tests/test_spark_cluster.sh`.

### C4. The drafter, DFlash2

`incoai/GLM-5.3-Flash-DFlash2 @ bf582e4eacc1810f76656d1811693ff6c6737d2a`
lives only in the HF cache, in `hf download` layout, and no OpenBeast lock
covers it. Copy the whole repo dir; its relative symlinks into `blobs/` survive
`rsync -a`. Then check every blob against its own name. LFS blobs are named by
their sha256, small files by their git blob SHA-1:

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

That proves the blobs are the bytes their names claim. That the snapshot is the
pinned revision rests on the names above (read off spark-1, 2026-11-15).

---

## D. Inference

There are three ways to run the engine. **D1 is what serves the pair today.**
D2 is OpenBeast's own general launcher, the challenger. D3 is the one-command
engine plane for single-host and pin management.

### D1. The Mia recipe (what serves today)

The recipe is MiaAI-Lab's GLM-5.3-Flash 2× DGX Spark recipe, v1.8, commit
`33b50fd`, cloned to `~/openbeast-recipes/glm53` on spark-1. Its `start.sh`
starts rank 1 on the worker over ssh, then rank 0 locally. It stays attached to
rank 0's log, and when either rank ends it stops the other. `start-tp3.sh` is
its experimental 3-Spark variant.

**`~/openbeast-recipes/glm53/scripts/local.sh` on spark-1 (2026-11-15, the
live values).** `scripts/config.sh` sources it, and
`scripts/local.sh.example` documents every knob.

```bash
# OpenBeast spark-hydra settings (written 2026-11-13). Sourced by scripts/config.sh.
WORKER=max@10.100.216.2        # spark-2 over the ConnectX-7 link (rank 1)
ABLIT=1                        # Mia's abliterated checkpoint @ 57edefd2 (staged locally, not re-downloaded)
DRAFTER=dflash2                # Max 2026-11-13: DFlash2 (CC BY-NC-ND, personal use) — 4 streams
# v0.6.0 has no API key: listen on loopback only. Everything outside reaches it
# through spark-hydra's beast-gate (:8443, per-device keys). 8888 is SearXNG's.
HOST=127.0.0.1
PORT=8000
# The token lives in a 0600 file, never in this file or on argv.
[[ -r "$HOME/.config/openbeast/hf-token" ]] && HF_TOKEN="$(<"$HOME/.config/openbeast/hf-token")"
# 2026-11-15 (adversarial review, ops lens): keep memory headroom for the command
# center beside rank 0 on unified memory. A fixed pool instead of "whatever is free
# at start"; a bigger host reserve; a stalled rank exits so systemd restarts the pair.
KV_POOL_GIB=10
MEMORY_RESERVE_GIB=17.5
export TF_GLM_MULTI_WATCHDOG_EXIT=1
```

What the three memory lines do. These values are live: the running container's
environment shows `TF_GLM_CACHE_GIB=10`, `TENSORFOLD_MEMORY_RESERVE_GIB=17.5`
and `TF_GLM_MULTI_WATCHDOG_EXIT=1`.

- **`KV_POOL_GIB=10`** becomes `TF_GLM_CACHE_GIB`. It caps the KV pool at a
  fixed size instead of "whatever is free at start". Unified memory freezes
  rather than OOMs, so a pool that grows into the command center's share is a
  hang, not an error.
- **`MEMORY_RESERVE_GIB=17.5`** becomes `TENSORFOLD_MEMORY_RESERVE_GIB`. It is
  the host memory TensorFold leaves free at start, for the command center
  beside rank 0. The recipe's default is 14.5 plus extras.
- **`TF_GLM_MULTI_WATCHDOG_EXIT=1`**: a rank whose collective stalls past the
  watchdog (`TF_GLM_MULTI_WATCHDOG_S`, 300 s) exits instead of hanging.
  `start.sh` then stops the other rank, and systemd's `Restart=on-failure`
  restarts the pair.

Even with these values, spark-1 has only about **9.7-9.9 GiB** of MemAvailable
while serving with the full command center up (healthcheck, 2026-11-15).
[G4](#g4-memory-headroom-and-the-gb10-stack-cap) covers what that means.

**First run (online).** `scripts/prepare.sh` is the recipe's own staging
script. It pulls or builds the image, downloads weights and the drafter, and
copies to the worker. Read the recipe's README before running it. On our pair,
the weights were staged by OpenBeast ([C](#c-weights)) and only the image came
from the recipe.

**Install the unit (on spark-1):**

```bash
mkdir -p ~/.config/systemd/user
sed "s|@RECIPE@|$HOME/openbeast-recipes/glm53|" ~/openbeast/scripts/backends/spark-hydra-inference.service \
  > ~/.config/systemd/user/spark-hydra-inference.service
systemctl --user daemon-reload
systemctl --user enable --now spark-hydra-inference
journalctl --user -fu spark-hydra-inference      # loading takes 2-6 min
curl -s http://127.0.0.1:8000/health             # {"ok": true, "backend": "tensorfold", ... "streams": {... "max": 4}}
```

What the unit sets:

- `Environment=FOREGROUND=1 PREPARE=0`. With `PREPARE=0` the unit never reaches
  the Hub, ghcr or the worker's setup at boot. `HF_HUB_OFFLINE=1` is the
  recipe's own default.
- `TimeoutStartSec=infinity`, `Restart=on-failure`, `RestartSec=30`.

Before starting it, two things must be true:

- **Both Sparks are guarded** ([B3](#b3-firewall-spark-guard)).
- **The user manager sees the docker group** (reboot once, [B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark)).

**Changing a recipe setting:**

1. Back it up: `cp scripts/local.sh scripts/local.sh.bak-$(date +%Y%m%d%H%M)`.
2. Edit `~/openbeast-recipes/glm53/scripts/local.sh`.
3. Restart: `systemctl --user restart spark-hydra-inference`. Loading takes 2-6 min.

**While the unit is enabled, start and stop the pair only through
`systemctl --user`.** Never call the recipe's `start.sh` or `stop.sh` by hand,
and never `docker stop` a rank. systemd would see the exit and restart the pair
behind you (`Restart=on-failure`), or the unit would think the pair is up when
it is not.

### D2. Native TensorFold through `spark-node.sh` / `spark-cluster.sh`

OpenBeast's own launcher. It is general (any profile, 1 to 8 ranks), keyed
(`--api-key-file`), and launches only verified local weights. On our pair it is
the **challenger**: native TensorFold 1.2.7 is installed on both Sparks but does
not serve. Serving GLM-5.3-Flash on the pair through D2 is VERIFY ON HARDWARE,
and so is comparing it with the recipe ("Not done yet").

**Install the engine** on every Spark. `install-native.sh` verifies three
things, all required: the `.sha256` names exactly this archive, its ed25519
signature checks against the vendored `release-key.pem`, and every unpacked file
matches `SHA256SUMS`.

```bash
scripts/backends/tensorfold/install-native.sh                     # latest, download + verify + install
scripts/backends/tensorfold/install-native.sh --version 1.2.7     # a specific version
tensorfold capabilities --json | head                             # NCCL must be found:true (root setup installs it)
```

**Per-node settings: `scripts/backends/spark.env`**, copied from
`spark.env.example` on **each** node and parsed, never sourced. The keys that
matter:

| Key | Meaning |
|---|---|
| `TENSORFOLD_ENGINE` | `native` (default) or `container` (the frozen 0.x engine, which pip-installs from GitHub/PyPI at every start: needs the internet every time) |
| `TENSORFOLD_VERSION`, `TENSORFOLD_BIN` | `1.2.7`, `~/.local/bin/tensorfold` |
| `TENSORFOLD_API_KEY_FILE` | 0600 key file; the native engine is keyed |
| `SPARK_IFACE`, `SPARK_IB_HCA` | this node's fabric netdev(s) and RoCE device(s); several rails comma-separated |
| `SPARK_HEAD_IP` | rank 0's fabric address. **The same on every node** (`check` enforces it) |
| `SPARK_NODE_IP` | this node's fabric address |
| `SPARK_SERVE_HOST`, `SPARK_SERVE_PORT` | rank 0's HTTP bind. Keep `127.0.0.1` when the command center is on the head; `SPARK_ALLOW_WILDCARD_BIND=false` refuses `0.0.0.0` |
| `MODELS_DIR`, `HF_CACHE`, `HF_TOKEN_FILE` | where verified weights live |
| `VLLM_IMAGE`, `VLLM_IMAGE_DIGEST`, `VLLM_IMAGE_ID`, `TENSORFOLD_IMAGE*` | container engines. `*_IMAGE_ID` wins over a digest, which is what makes a `docker load`ed image work offline ([H4](#h4-container-images)) |
| `VLLM_MASTER_PORT`, `TENSORFOLD_MASTER_PORT` | rendezvous ports; spark-guard guards them too |

**One rank by hand** (start ranks N-1..1 FIRST, then rank 0):

```bash
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 1 --master 10.100.216.1 --print   # print the exact command, run nothing
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 1 --master 10.100.216.1           # on spark-2
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 0 --master 10.100.216.1           # then on spark-1
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 0 --stop                          # stop this rank (idempotent)
```

**The whole cluster from the head.** List the nodes in rank order in
`scripts/backends/cluster.env` (copy `cluster.env.example`), for example
`CLUSTER_NODES="local max@10.100.216.2"`, `CLUSTER_NAME=spark-hydra`,
`CLUSTER_REPO=~/openbeast`. A leading `~/` is sent as-is and expands on each
node. Then:

```bash
scripts/backends/spark-cluster.sh check                          # every node: aarch64 + GB10, docker, NCCL, same commit, fabric pairing, SPARK_HEAD_IP reach
scripts/backends/spark-cluster.sh sync-weights --profile NAME    # model + drafter over the fabric, verified vs the HEAD's lock
scripts/backends/spark-cluster.sh sync-image IMAGE               # docker save | load where it differs; prints the *_IMAGE_ID pin
scripts/backends/spark-cluster.sh save-image IMAGE FILE          # an image to a file, for a cluster with no registry
scripts/backends/spark-cluster.sh start --profile NAME [--engine tensorfold|vllm]
scripts/backends/spark-cluster.sh status
scripts/backends/spark-cluster.sh stop [--engine tensorfold|vllm]   # every rank, rank 0 first; both engines by default
#   --cluster FILE   --dry-run (print every command, run nothing)
```

How start and stop behave:

- `start` launches ranks N-1..1 and then 0, each with its own
  `spark-node.sh --rank R`. If a later rank fails, it stops the ranks it
  started.
- `stop` stops rank 0 first, and walks every rank even if one is already down.

**Do not run D2 on the ports D1 is using** while the pair serves: both use
:8000 and the 29551 range.

**Which engine scales how far** (2026-11-15):

| Engine | Ranks | |
|---|---|---|
| Mia-AiLab GLM recipe (TensorFold v0.6.0 + patches) | 2 (`start.sh`), 3 (`start-tp3.sh`, experimental) | what serves our pair; its own scripts drive the ranks |
| native TensorFold ≥ 1.0 (1.2.7) | GLM-5.3-Flash up to 16 (`TF_GLM_WORLD`); MiniMax-M3.1-Flash 2 only (`TF_MINIMAX_*`); Flash Next 1-2; Nemotron 1; other families `--tp` 2/4/8 | from the 1.2.7 binary's own messages and `capabilities --json`; `spark-node.sh` refuses sizes the engine refuses |
| container TensorFold 0.x (@9cd52ab) | per `data/tensorfold.json`: GLM 2, most families 1-2, qwen3_5_moe 1 | the launcher refuses an excluded size at that commit, and warns at any other |
| vLLM (native multi-node) | `--nnodes N`, TP = N | the model's head count must divide by N |

Whether a model *fits* at N is a memory question. `model-inspect.sh` reports the
smallest N that holds the weights with KV room, judges ranks against the native
TensorFold engine, and lists the container engine's ranks separately.
Onboarding a new model: `DGX_SPARK_PLAN.md` §14 (`model-inspect.sh <repo@sha>
--write-profile NAME`, then `model-fetch.sh --profile NAME`, then
`conformance.sh`).

**Several smaller groups instead of one big one.** For example, 4 Sparks can run
as two independent pairs, each its own hydra node (`[nodes.pairA]`,
`[nodes.pairB]`, both in one route). That trades the long single-request context
of TP 4 for twice the concurrent capacity.

### D3. The engine plane: `scripts/engine.sh`

`engine.sh` is one command to list, swap, install, update, pin and roll back the
engine ([ENGINES.md](ENGINES.md) has the full matrix):

```bash
scripts/engine.sh list                         # what this host can run; installed / pinned / active
scripts/engine.sh status                       # active engine: version, pin, endpoint readiness, model served
scripts/engine.sh update tensorfold            # latest release: download, verify, pin
scripts/engine.sh install tensorfold --version 1.2.9
scripts/engine.sh rollback tensorfold          # previous pin, offline when still on disk
scripts/engine.sh stage tensorfold --version 1.2.9 --platform linux-aarch64 ./carry   # connected box
scripts/engine.sh install tensorfold --from ./carry                                   # closed box
scripts/engine.sh history
scripts/engine.sh use <engine|default> --profile NAME --dry-run   # print the conf change + launch command
```

On a Spark:

- `install`, `update` and `rollback` also rewrite `scripts/backends/spark.env`
  (`TENSORFOLD_VERSION`, `VLLM_IMAGE` / `VLLM_IMAGE_DIGEST`), so `spark-node.sh`
  launches the pinned build. **Every rank must run the same build**: update
  each Spark, then restart all ranks.
- `engine.sh use` manages only a **single-host, TP 1** engine
  (`INFERENCE_MANAGED=true`). A multi-Spark job stays unmanaged: `start.sh`
  waits for it and never launches it, and you run it with D1 or D2.
- `engine.sh` does not manage the recipe (D1). The recipe pins its own image tag.

`engine.sh` is not yet on the live checkouts ([0](#what-runs-where)). On a
Spark it is tested hermetically (`tests/test_engines.sh`) but has not run
there: VERIFY ON HARDWARE.

---

## E. The command center on spark-1

### E1. Bootstrap

```bash
cd ~/openbeast
cp openbeast.conf.example openbeast.conf 2>/dev/null || touch openbeast.conf
chmod 600 openbeast.conf
# write the keys in E2 first: INFERENCE_BACKEND=tensorfold makes bootstrap skip llama.cpp and the GGUF
./bootstrap.sh --preflight       # what it will do, and what it cannot reach
./bootstrap.sh --no-start        # install deps, compose images, frontends; do not start yet
```

`INFERENCE_MANAGED` is forced false for TensorFold, so `./bootstrap.sh` builds
no llama.cpp and downloads no GGUF (DGX_SPARK_PLAN §8 row 19). All three compose
images are multi-arch at their pinned index digests, so the same
`docker-compose.yml` pulls arm64 on a Spark. The Python lock installs as-is on
aarch64 / python 3.12.3 (43 packages, measured on spark-1).

### E2. `openbeast.conf`

These are the keys in use on spark-1 on 2026-11-15. Secret values are not shown.

| Key | Value on spark-1 | Why |
|---|---|---|
| `INFERENCE_BACKEND` | `tensorfold` | the model is the recipe's TensorFold server |
| `INFERENCE_URL` | `http://127.0.0.1:8000` | same box, loopback. Spawned agents follow it (conf.sh exports `OPENBEAST_AGENT_INFERENCE_URL=$INFERENCE_URL/v1`) |
| `INFERENCE_MODEL` | `GLM-5.3-Flash-EXL3` | the served name; exported as `OPENBEAST_INFERENCE_MODEL`, which `agents/runner.py` sends |
| `INFERENCE_SLOTS` | `4` | matches the recipe's 4 streams; beast-slot reports it |
| `EDGE_GATE` | `true` | beast-gate (:8090) is the one inference door (:8443) |
| `WEBUI_AUTH` | `true` | the WebUI login wall; required before :443 is published |
| `WEBUI_ADMIN_EMAIL`, `WEBUI_ADMIN_PASSWORD` | *(secret)* | the admin `configure-webui.sh` uses to apply tool-server config on restarts |
| `SEARXNG_SECRET` | *(secret)* | SearXNG's secret key |
| `EXTENSIONS` | `"dashboard"` | the dashboard serves beast-slot (`/api/slot`) |
| `BEAST_CHAT` | `true` | the operator console |
| `CHAT_OPERATORS` | `maxjkh@gmail.com` | tailnet logins that may read beast-chat |
| `CHAT_LOGIN_FROM` | `unix` | beast-chat honours the login header only on `.run/chat.sock`; `:8445` proxies to that socket (`setup-tailscale.sh --publish-chat`), so the host-network containers below cannot claim a login ([BEAST_CHAT.md](BEAST_CHAT.md)) |
| `BEAST_ARTIFACT` | `true` | the artifact gallery and `publish_artifact` |
| `ARTIFACT_OPERATORS`, `ARTIFACT_ADMINS` | `maxjkh@gmail.com` | who reads and administers artifacts |
| `LANG_PACKS`, `LANG_PACK_CONTEXT` | `off` | language assistance OFF on the pair (below) |
| `BEAST_ASSIST`, `BEAST_ESCALATE` | `0` | ditto |

Not set, so the defaults apply:

- `ISLAND` = `auto`.
- `OFFLINE` = `false`.
- `MEM_RESERVE_GB` = `3`. This is the GiB the stack's cap leaves beside the
  model on unified memory ([G4](#g4-memory-headroom-and-the-gb10-stack-cap)).
- `MEM_LIMIT_PCT` = `75`.
- `EDGE_MAX_INFLIGHT` = `2`: beast-gate admits two concurrent generations
  per device key. The eval harness's agents all share one key, so a
  `--jobs 4` benchmark or profile of GLM-5.3-Flash from the Mac
  (`benchmark_all.py --models glm-5.3-flash`, `profile.py --slots 4`) is
  **clamped to 2**: `run_eval` reads the cap from the gate (a `n=1024`
  chat request the gate refuses before forwarding anything) and prints
  why. Before review 1116 it ran 4 workers anyway, half the generations got
  429, each burned an agent iteration, and failing units became unbanked
  `server_error`s. To measure with 4 streams, set `EDGE_MAX_INFLIGHT=4` in
  `~/openbeast/openbeast.conf` on spark-1 and restart the command center
  (`systemctl --user restart openbeast`, [G3](#g3-restarts-only-through-systemctl---user-while-the-unit-is-enabled));
  both steps are local and work offline. It raises the cap for every
  device, so revert it after the run if other clients share the pair.
- `WATCHDOG_AUTOINSTALL` and `LOGROTATE_AUTOINSTALL` = `true`.
- `BIND_HOST` = `127.0.0.1`.

**Language assistance is OFF on the pair** (Max, 2026-11-15). Packs, the pack
context, beast-assist and escalation were measured for the 27B and help it.
GLM-5.3-Flash needs far less help. `language_reference` stays available as a
tool the model can choose to call. Also not enabled on the pair: `AGENT_ROUTER`,
an intent classifier that exists because the 27B would not call `start_agent`
on its own, and `HYDRA` / `INSTINCT`, whose scorer is the rig's 27B.

### E3. Boot units

```bash
mkdir -p ~/.config/systemd/user
sed "s|@REPO@|$HOME/openbeast|" scripts/openbeast.service > ~/.config/systemd/user/openbeast.service
systemctl --user daemon-reload
systemctl --user enable --now openbeast
```

- `openbeast.service` is a oneshot with `RemainAfterExit`. It runs
  `start.sh -d`, which runs the stack inside its own memory-capped transient
  scope (`openbeast-stack`).
- The unit does not order against `spark-hydra-inference`. `start.sh` waits up
  to `OPENBEAST_LLAMA_LOAD_GRACE` for `INFERENCE_URL` and brings the rest of the
  stack up either way.
- `start.sh -d` installs and enables `openbeast-watchdog.timer`
  (`healthcheck.sh --restart` every 5 min) and `openbeast-logrotate.timer`
  (daily) when they are missing.

What is enabled on spark-1 (`systemctl --user list-unit-files | grep -E 'openbeast|spark|hydra'`):

```
openbeast.service              enabled
spark-hydra-inference.service  enabled
openbeast-watchdog.timer       enabled
openbeast-logrotate.timer      enabled
openbeast-stack.service        transient
```

Then reboot once. This is the docker-group gotcha from
[B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark). After the
reboot, everything comes back by itself:

```bash
systemctl --user status spark-hydra-inference openbeast --no-pager | grep -E 'Active|●'
~/openbeast/scripts/healthcheck.sh        # expect "All 9 services healthy" once the model has loaded
```

### E4. The healthy state

```
Stack health check — 2026-11-15 13:03:38
  OK   TensorFold (tensorfold @ http://127.0.0.1:8000)
  OK   Tool server
  OK   Open WebUI
  OK   SearXNG
  OK   beast-gate
  OK   beast-chat console
  OK   beast-artifact
  OK   Dashboard (beast-slot)
  --   Network: online (watching: islands after 90s without a route); internet reachable at last probe
  OK   Tailscale (remote access)

  Unified memory: 9751 MiB available of 124607 MiB (the model's share is in the total)

All 9 services healthy.
```

### E5. WebUI auth and the admin account, before publishing

Do this **before** `setup-tailscale.sh` publishes :443. Publishing an auth-off
WebUI makes every tailnet device an admin, with `bash`.

1. Put `WEBUI_AUTH=true` in `openbeast.conf` and (re)start the stack:
   `systemctl --user restart openbeast`.
2. On the first auth-on start, Open WebUI's built-in `admin@localhost` default
   password (`admin`) is rotated to a random one, saved as
   `WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_PASSWORD` in `openbeast.conf` (0600).
   `setup-tailscale.sh` also checks this.
3. Reach the WebUI over an ssh tunnel (it binds loopback):
   `ssh -L 3000:127.0.0.1:3000 spark-1.tail4109f9.ts.net`, then open
   `http://localhost:3000`. Log in with the rotated credentials and create your
   own admin in Admin Panel → Users. New signups land as `pending`; "first
   signup becomes admin" applies only to a WebUI database that has never had a
   user.
4. If you make your own account the one `configure-webui.sh` uses, put it in
   `WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_PASSWORD`. The rotated built-in password
   then moves to `WEBUI_DEFAULT_ADMIN_PASSWORD`.

On spark-1:

- The admin is `maxjkh@gmail.com`. Its initial password was left in
  `~/.config/openbeast/webui-max-password` (0600). Change it in Settings →
  Account, then delete that file (`shred -u`).
- The WebUI database is spark-1's own: accounts and chats from the beast rig do
  not carry over.

### E6. Publish on the tailnet (`setup-tailscale.sh`, as tailscaled's operator)

`spark-root-setup.sh` set `--operator=max`, so this needs no sudo on a Spark:

```bash
cd ~/openbeast
grep -q '^TS_HOSTNAME=' openbeast.conf || echo 'TS_HOSTNAME=spark-1' >> openbeast.conf
./scripts/setup-tailscale.sh --publish-slot --publish-chat --publish-artifact --publish-searxng
./scripts/setup-tailscale.sh --status        # read-only: which surface sits on which port
```

`TS_HOSTNAME` (conf key, env overrides) names the box on the tailnet. On a Spark it
has no default: the script refuses to join until it is set, because two Sparks
both defaulting to `beast` would collide. It applies only to the first join; a
Spark already on the tailnet keeps its name, and the script prints a note (never
a rename) when the conf names it differently. Re-running it on a joined Spark is
therefore safe. The script refuses to
publish :443 unless the **running** WebUI enforces auth. It refuses :8443 unless
beast-gate (`EDGE_GATE=true`) or a shared `LLAMA_API_KEY` is in front. The
overrides `--i-accept-open-webui` and `--i-accept-open-inference` exist; do not
use them here. Undo one surface with `--unpublish-<name>`.

The published URLs (live 2026-11-15):

| URL | What | Auth |
|---|---|---|
| `https://spark-1.tail4109f9.ts.net` | Open WebUI | WebUI login (`WEBUI_AUTH=true`) |
| `https://spark-1.tail4109f9.ts.net:8443/v1` | inference, **through beast-gate** | per-device key (`scripts/clients.sh`); 401 without one (checked from the Mac 2026-11-15) |
| `https://spark-1.tail4109f9.ts.net:8444/api/slot` | beast-slot discovery | open, read-only |
| `https://spark-1.tail4109f9.ts.net:8445` | beast-chat console | tailnet login to read, chat-scoped device key to act |
| `https://spark-1.tail4109f9.ts.net:8446` | beast-artifact gallery | tailnet login (`ARTIFACT_OPERATORS`) |
| `https://spark-1.tail4109f9.ts.net:8889` | SearXNG for thin clients | open to the tailnet |

The model itself still listens on `127.0.0.1:8000` only. `:8443` is the one
inference door, and it always goes through the gate. Public exposure (funnel)
is never offered.

**Not yet:** a stable `svc:spark-hydra` name. That is a Tailscale Service, which
has to be defined in the admin console (and spark-1 tagged) before
`tailscale serve --service=svc:spark-hydra` can advertise it. Until then the
pair is reached by spark-1's name.

### E7. Device keys for beast-gate

```bash
cd ~/openbeast
./scripts/clients.sh enroll macbook --label "Max's MacBook"     # prints the key ONCE; the registry keeps only its SHA-256
./scripts/clients.sh enroll phone --label "phone" --scope chat  # chat scope = may act in beast-chat
./scripts/clients.sh list
./scripts/clients.sh revoke <id>         # a lost device; hot-reloaded, no restart
./scripts/clients.sh rotate <id>         # new key for the same device
```

The registry is `.run/clients.json` (0600, gitignored).

Carry each key to its device **without printing it into a shared log**: copy it
straight into a 0600 file on the device. On the Mac it is
`~/.config/openbeast/spark-hydra-gate.key`.

**Known issue:** plaintext copies of the `macbook` and `phone` keys are in
`~/.config/openbeast/gate-keys/` on spark-1 (0600). They defeat the registry's
hash-only design. They are readable by `max`, the uid the admin-tier `bash` /
`read_file` tools run as, so a prompt-injected admin chat could read a working
device key. Once each device holds its key, delete the copies:
`shred -u ~/.config/openbeast/gate-keys/*.key`. If one is lost, re-enroll to
recover it.

---

## F. The Mac client

### F1. ssh from the Mac

NVIDIA Sync's `.local` names resolve only on the home LAN. For away from home,
the Mac's `~/.ssh/config` has tailnet entries that reuse the Sync key and pin
the known host keys with `HostKeyAlias`:

```
Host spark-1.tail4109f9.ts.net
  HostKeyAlias spark-3dc6.local
  User max
  IdentityFile <the NVIDIA Sync key>
Host spark-2.tail4109f9.ts.net
  HostKeyAlias spark-9f26.local
  User max
  IdentityFile <the NVIDIA Sync key>
```

Without `HostKeyAlias`, the first tailnet login asks to trust a "new" host
whose key is in fact already known under the `.local` name.

**Quote remote commands.** `ssh spark-1 cat ~/.config/openbeast/x` expands `~`
on the **Mac** (`/Users/…`) before ssh sends it. Write
`ssh spark-1 'cat ~/.config/openbeast/x'`. The same goes for `$HOME`, globs and
`&&` chains.

### F2. Client mode (`setup-client.sh`)

```bash
cd ~/Documents/projects/openbeast          # a checkout on the Mac
./scripts/setup-client.sh --host spark-1.tail4109f9.ts.net --api-key-stdin < ~/.config/openbeast/spark-hydra-gate.key
openbeast-client status                     # or scripts/client.sh status: reads :8444/api/slot
```

What you get:

- OpenCode on the Mac, with the full tool arsenal running **locally**: a stdio
  MCP subprocess (`agents/mcp_server.py`) in `~/.openbeast-client/venv`.
  `bash`, `edit_file` and the rest act on the Mac's files.
- Inference from `https://spark-1…:8443/v1` through the gate.
- `web_search` through `:8889`. The alternatives are `--local-search` or
  `--no-search`.

File contents the agent reads are sent to the pair as model context. The
promise is "nothing leaves the tailnet", not "nothing leaves the Mac".

### F3. The opencode v2 config layout (read this before editing)

The Mac runs **opencode v2.0.25**. Its global config is
`~/.config/opencode/opencode.json`.

**Everything MCP must live under `mcp.servers.<name>`.** opencode v2 does
**not** merge a v1 entry (`mcp.<name>`, with `enabled: true`) with v2 entries
(`mcp.servers.<name>`). A file that mixes them loses the v1 ones. The v2 server
schema is `additionalProperties: false`, so v1's `enabled` key is also invalid
there.

The working layout on the Mac (2026-11-15; values elided):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "openbeast-tools": { "type": "local", "command": ["…/.openbeast-client/venv/bin/python3", "…/agents/mcp_server.py"],
                           "environment": { "OPENBEAST_AGENT_INFERENCE_URL": "https://spark-1.tail4109f9.ts.net:8443/v1", "…": "…" } },
      "mobilebuildmcp":  { "type": "local", "command": ["…"], "environment": {}, "codemode": false }
    }
  },
  "provider": { "openbeast-rig": { "npm": "@ai-sdk/openai-compatible", "options": { "baseURL": "…:8443/v1", "apiKey": "<the device key>" }, "models": { … } } },
  "model": "openbeast-rig/…",
  "small_model": "openbeast-rig/…"
}
```

- `codemode: false` turns off v2's Code Mode, so the model gets the tools
  directly. beast-apps writes it.
- The provider id is `openbeast-rig` and the MCP key is `openbeast-tools`.
  These are distinct so the checkout's own `./opencode.json` (`llama-cpp`,
  `local-tools`) cannot shadow them when you run `opencode` from inside the
  repo.
- Permissions in v2 are a list:
  `permissions: [{action: "<server>_<tool>", resource: "*", effect: "ask"}]`.
  The last match wins.

**Known mismatch (needs a code fix):** `scripts/setup-client.sh` still writes
the v1 shape, `mcp["openbeast-tools"] = {…, "enabled": true}`. On opencode v2,
re-running it produces exactly the mixed file described above. Until it learns
v2, after any `setup-client.sh` run on a v2 Mac:

1. Move the entry under `mcp.servers`.
2. Drop its `enabled` key.
3. Back up first: `cp ~/.config/opencode/opencode.json ~/.config/opencode/opencode.json.bak-$(date +%s)`.

`scripts/apps.sh` already detects v1 and v2 and writes the right one
([BEAST_APPS.md](BEAST_APPS.md) "opencode").

### F4. beast-apps: external apps through MCP servers

```bash
scripts/apps.sh list                  # every capability, its status here, enabled or not
scripts/apps.sh check ios-build       # probes + the exact fix for anything missing
scripts/apps.sh enable ios-build --dry-run
scripts/apps.sh enable ios-build      # merges into mcp.servers (v2), backs the old file up first
scripts/apps.sh disable ios-build     # removes exactly what enable wrote
```

The Mac on 2026-11-15:

- READY: `ios-build` (enabled, `mobilebuildmcp`), `android-build`,
  `mobile-ui`, `sim-ros`.
- MISSING: `blender`, `figma`, `3d-print` (off by default: risk).

Offline, `check`, `enable` and `disable` never touch the network. `refresh`
uses `npx --offline` / `uvx --offline`. Skill: `app-control`.

### F5. beast-docs: platform docs, mirrored

```bash
./scripts/docs.sh sources                                   # what can be mirrored
./scripts/docs.sh sync swiftui --max-pages 300 --accept-terms
./scripts/docs.sh search NavigationStack
./scripts/docs.sh status                                    # pages, age, STALE flags
./scripts/docs.sh pack mirror.tar.gz                        # carry it; writes mirror.tar.gz.sha256
```

The store is `~/.openbeast/docs` (or `OPENBEAST_DOCS_DIR`). spark-1 has
**no mirror** yet: every source reads "not mirrored". To give the pair one, sync
on the Mac, `pack`, copy, and `unpack` on spark-1 ([H8](#h8-beast-docs-mirror)).
Models reach it through the `platform_docs` MCP tool or `scripts/docs.sh`.
Skill: `platform-docs`.

---

## G. Day-2 operations

### G1. Health

```bash
# on spark-1
~/openbeast/scripts/healthcheck.sh               # expect "All 9 services healthy" + the unified-memory line
curl -s http://127.0.0.1:8000/health             # ok, streams, pool tokens, requests_total
~/openbeast/scripts/doctor.sh                    # 39 ok, 1 warning on 2026-11-15 (SSD wear needs a sudoers rule)
~/openbeast/start.sh --status                    # stack + island state
python3 ~/openbeast/agents/island.py status      # online / island, and why
systemctl --user status spark-hydra-inference openbeast --no-pager
# from the Mac
curl -sk https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -m json.tool | head -30
```

### G2. Logs

```bash
journalctl --user -fu spark-hydra-inference          # rank 0 + start.sh
docker logs -f glm53-flash-tf                        # rank 0 container
ssh max@10.100.216.2 'docker logs -f glm53-flash-tf' # rank 1 (quote it)
tail -f ~/openbeast/.run/stack.log                   # the command center's supervisor
journalctl --user -u openbeast --since today
```

### G3. Restarts: only through `systemctl --user` while the unit is enabled

```bash
systemctl --user restart spark-hydra-inference       # both ranks (2-6 min to load)
systemctl --user stop spark-hydra-inference
systemctl --user restart openbeast                   # the command center (WebUI, tools, gate, …)
./scripts/healthcheck.sh --restart                   # restart only what is DOWN (the watchdog does this every 5 min)
```

Never `docker stop glm53-flash-tf`, `~/openbeast-recipes/glm53/stop.sh` or
`./stop.sh` by hand while the matching unit is enabled. systemd restarts the
pair behind you (`Restart=on-failure`), or the oneshot `openbeast` unit stays
"active" over a stopped stack.

After a mid-stream generation error on several ranks, restart **all** of them:
restart the unit, never one rank.

### G4. Memory headroom and the GB10 stack cap

GB10 memory is **unified**: the model's ~80 GiB per Spark is inside MemTotal
(124,607 MiB). Two caps keep the box from freezing. Unified memory freezes
rather than OOMs.

- **The engine side** ([D1](#d1-the-mia-recipe-what-serves-today)):
  `KV_POOL_GIB=10` fixes the KV pool, and `MEMORY_RESERVE_GIB=17.5` leaves host
  memory for the command center.
- **The stack side** (`start.sh`): `start.sh -d` runs the stack in a
  memory-capped systemd scope. On a unified-memory box running its own model,
  the cap is re-sized once the model is up, to **MemAvailable minus
  `MEM_RESERVE_GB`** (default 3). The chat jobs' cap is derived the same way.
  75 % of a MemTotal the model already holds would protect nothing.
  `healthcheck.sh` and `doctor.sh` warn when MemAvailable falls below
  `MEM_RESERVE_GB`.

Watch the `Unified memory: N MiB available` line in `healthcheck.sh`. With the
pair serving at `PARALLEL=4` and the full stack up, it read about **9.7-9.9 GiB**
on 2026-11-15, and spark-2 (rank 1 only) about 11 GiB. Before adding anything to
spark-1 (an extension, a second model, a heavy build), check that line. Do not
try `PARALLEL=8` without raising the reserve. The recipe's notes measured rank
0's lowest free memory at 7.0 GiB for `PARALLEL=8`.

### G5. The watchdog and self-healing

- **`openbeast-watchdog.timer`** runs `healthcheck.sh --restart` every 5 min.
  It restarts DOWN compose services. A down managed engine is only reported,
  never restarted. The unmanaged TensorFold pair is systemd's job, not the
  watchdog's.
- **`TF_GLM_MULTI_WATCHDOG_EXIT=1`**: a stalled rank exits, and the unit
  restarts the pair.
- **`healthcheck.sh`** also catches a stalled TensorFold pair that still says
  `ok`, via the `.run/tf-liveness` record.
- **beast-island** (`ISLAND=auto`): if the internet goes away, the web tools
  refuse at once after 90 s, and the stack rejoins by itself.

### G6. Updating

**OpenBeast** on every node, the same commit everywhere:

```bash
cd ~/openbeast && git fetch && git status -sb      # read first: never pull over local edits
git pull --ff-only
ssh max@10.100.216.2 'cd ~/openbeast && git pull --ff-only'
systemctl --user restart openbeast                 # makes the new commit live on the head
```

The worker runs no OpenBeast services. Its checkout matters for the lock
(`--verify`), for `spark-node.sh`, and for `spark-cluster.sh check`.

**The engine:**

- Native TensorFold: `scripts/engine.sh update tensorfold` (or
  `install-native.sh --version V`) on every Spark, then restart all ranks.
- The recipe: a new recipe version or image means a new `git pull` in
  `~/openbeast-recipes/glm53`, `prepare.sh` (online), the image carried to every
  rank, and `systemctl --user restart spark-hydra-inference`.
- Before moving any engine pin, read [ENGINES.md](ENGINES.md) "Research notes".

**A model:** a new profile is `model-inspect.sh` → `model-fetch.sh` (lock) →
`conformance.sh` → serve. The lock of a served model never changes. A new
revision is a new profile.

### G7. Rotating keys and secrets

| Secret | Rotate with | Notes |
|---|---|---|
| a device key | `./scripts/clients.sh rotate <id>` | hot-reloaded; give the device its new key |
| a lost device | `./scripts/clients.sh revoke <id>` | immediate |
| WebUI admin password | WebUI Settings → Account, then update `WEBUI_ADMIN_PASSWORD` if it is the configured admin | `systemctl --user restart openbeast` so `configure-webui.sh` uses it |
| HF token | replace `~/.config/openbeast/hf-token` (0600) | only used online |
| TensorFold / vLLM API key files | rewrite the 0600 file named in `spark.env`, restart all ranks | the recipe (v0.6.0) has no key |

### G8. When X fails: troubleshooting

Every row below was hit on the way to the live pair.

| Symptom | Cause | Fix |
|---|---|---|
| WebUI / SearXNG DOWN; `.run/stack.log`: "docker daemon not ready" | the systemd user manager started before `max` joined the `docker` group | reboot once; `systemd-run --user --wait --pipe docker info` proves it |
| `ssh spark-1` cannot resolve `spark-3dc6.local` | mDNS right after a reboot, or away from home | `ssh -o HostName=192.168.40.153 spark-1` at home; the tailnet name with `HostKeyAlias` away ([F1](#f1-ssh-from-the-mac)) |
| `ssh spark-1.tail4109f9.ts.net` asks to trust a new host key | no `HostKeyAlias` | add `HostKeyAlias spark-3dc6.local` to that `Host` block |
| `ssh spark-1 cat ~/.config/openbeast/x`: "No such file" | the Mac's shell expanded `~` to `/Users/…` | quote the remote command: `ssh spark-1 'cat ~/.config/openbeast/x'` |
| ranks cannot reach each other | a firewall drops the peer on the fabric (ufw, if someone enables it without the fabric rules) | `spark-root-setup.sh` adds a ufw rule per Up link for that link's subnet; `spark-guard.sh` always accepts the fabric. ufw itself is **disabled** on DGX OS (`/etc/ufw/ufw.conf` `ENABLED=no`) and was never what blocked or protected anything here |
| `nc -z <tailnet IP> 29551` succeeds from a laptop | rank 0's TCPStore binds `*:29551`, ufw is off, and Tailscale's `ts-input` accepts tailnet traffic first | `sudo scripts/backends/spark-guard.sh` on each Spark ([B3](#b3-firewall-spark-guard)) |
| `tensorfold capabilities` shows NCCL `found:false` | no libnccl2 | `spark-root-setup.sh` installs 2.28.9+cuda13.0 and holds it |
| `model-fetch --verify`: "unlocked file .cache/huggingface/…" | `hf download --local-dir` metadata | fixed: hidden paths are skipped by both lock and verify |
| `model-fetch --verify` / `--locate` exit 3 | no lock in this checkout | pull the commit with the lock, or `--verify --trust-first-use` for a local SOURCE |
| the recipe serves, but the lock "does not cover" it | the HF-cache snapshot lost its hard links (re-download, rename-save) | re-run the `stat` check (C2); re-link with `cp -al` and re-verify |
| a container engine on an offline worker tries to pull | `*_IMAGE_DIGEST` pin; `docker save/load` drops the RepoDigest | pin `*_IMAGE_ID` instead (`sync-image` prints the line) ([H4](#h4-container-images)) |
| `TENSORFOLD_ENGINE=container` fails offline | it pip-installs TensorFold from GitHub and deps from PyPI at every start | use the native engine (default) or the recipe |
| box freezes / ssh hangs under load | unified memory exhausted (it freezes rather than OOMs) | keep `KV_POOL_GIB` / `MEMORY_RESERVE_GIB` as in D1; watch `Unified memory` in healthcheck ([G4](#g4-memory-headroom-and-the-gb10-stack-cap)) |
| pair stops answering mid-stream, `/health` still `ok` | a stalled collective | `TF_GLM_MULTI_WATCHDOG_EXIT=1` makes the rank exit so the unit restarts both; healthcheck's liveness check flags it |
| `:443` not published after `setup-tailscale.sh` | the running WebUI still had auth off | `systemctl --user restart openbeast`, then re-run `setup-tailscale.sh` ([E5](#e5-webui-auth-and-the-admin-account-before-publishing)) |
| `:8443` answers 401 | no device key, or a revoked one | `clients.sh list`; give the client its key |
| opencode on the Mac lost `openbeast-tools` (or another server) | a v1 `mcp.<name>` entry next to v2 `mcp.servers.<name>`: v2 does not merge them | move everything under `mcp.servers`, drop `enabled` ([F3](#f3-the-opencode-v2-config-layout-read-this-before-editing)) |
| `tests/test_scripts.sh` failures on a copy without `.git` (7 seen on spark-2) | some suites need a git checkout | run from a git checkout |
| shell suites fail on the Mac but pass on a Spark | BSD vs GNU tools (`tests/test_engines.sh` needs GNU coreutils; `test_skill_import.sh` showed 5 BSD-only failures on 2026-11-15) | run shell suites on a Spark ([SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md) "Tests") |
| `python3 -m pytest` says no module | no pytest for that python (none on spark-1 or the Mac's default python, 2026-11-15) | the hash-pinned test venv ([H7](#h7-test-and-language-toolchains)) |
| `bootstrap.sh` offline asks for `bundle.sh build` "on a connected box" | the bundle must be built for the **target's** platform | build it on an online Spark (aarch64, same overlay2 store) ([H6](#h6-the-command-center-wheels-and-compose-images)) |
| web tools stall instead of refusing in a spawned agent | `agents/tools.py` (era-hashed) has no island guard; only `mcp_server.py` does | expected (BEAST_ISLAND "Known limits"); web_search is bounded at 15 s, fetch at 45 s |

---

## H. Fully offline operation

### H1. Two switches, and what keeps working

| Switch | Meaning | Effect |
|---|---|---|
| `ISLAND=auto` (default) / `force` (`./start.sh -d --offline`) | runtime: "the internet just went away" | `web_search` / `fetch` (MCP + `:3001`) refuse at once; WebUI in `OFFLINE_MODE`; compose `--pull never` when islanded at launch; healthcheck excuses an offline Tailscale ([BEAST_ISLAND.md](BEAST_ISLAND.md)) |
| `OFFLINE=true` in `openbeast.conf` (env `OPENBEAST_OFFLINE`) | install: "this box never has internet" | `bootstrap.sh`, `update.sh`, `engine.sh update`, `model-fetch.sh`, `apps.sh refresh` refuse the network up front and say what to stage instead |

**What keeps working offline:**

- **Inference** works: the recipe runs with `PREPARE=0` and `HF_HUB_OFFLINE=1`,
  and everything is local.
- **The whole command center** works except the web: WebUI, tools, agents,
  skills, files, beast-chat, beast-artifact, beast-gate, beast-slot.
- **opencode on spark-1** works (`opencode` in `~/openbeast`).
- **`./agent.sh`** works on spark-1.
- **`language_reference`, `platform_docs`, `scripts/docs.sh search`** work
  against what is mirrored.
- **beast-ark** works: `verify`, `restore` and `serve` an ark drive (H8a,
  [BEAST_ARK.md](BEAST_ARK.md)).
- **The tests** work (hermetic).
- **The tailnet** keeps working wherever the LAN still routes between devices.
  Tailscale needs its coordination server to set up new paths, so existing
  direct connections are the ones that survive.

What does not work: `web_search` (refused), `fetch` (refused), WebUI's built-in
Web Search toggle (empty results), Hub downloads, image pulls, apt, pip from an
index, `git pull` from GitHub.

### H2. The repo

`git bundle` carries the whole history; a plain copy of a checkout works too:

```bash
git -C ~/openbeast bundle create /media/usb/openbeast.bundle --all     # online box
git clone /media/usb/openbeast.bundle ~/openbeast                      # closed box
git -C ~/openbeast log --oneline -1                                     # the commit you expect
```

### H3. .debs: libnccl2 and tailscale

On a Spark that can reach apt's repos (apt checks the repo signatures as it
downloads):

```bash
mkdir debs && cd debs && apt-get download libnccl2=2.28.9-1+cuda13.0 tailscale=1.104.7 && cd ..
~/openbeast/scripts/backends/spark-root-setup.sh --check-debs debs        # no root: sha256 vs the pins -> "OK: debs holds the pinned .debs"
sudo ~/openbeast/scripts/backends/spark-root-setup.sh spark-N --debs debs --guard   # on the closed Spark
```

A `.deb` given by path skips apt's signature check. So `--debs` installs only
those exact versions, and only if each matches the sha256 pinned in the script
(`NCCL_DEB_SHA256`, `TS_DEB_SHA256`, taken from the signed repo indexes). A
newer tailscale means bumping `TS_DEB_VERSION` / `TS_DEB_SHA256` there
(`apt-cache show tailscale=<V> | grep SHA256`). A closed box cannot finish
`tailscale up` without a route to the coordination server. The script says so
and continues; re-run it once there is a route.

### H4. Container images

**The recipe image** is `tensorfold-glm53:v0.6.0`, ID
`sha256:ead0174052ac16b07d98fba9b68e7d7101b1e1db5c0ccdbde62f41ccf229c647`. The
recipe checks it by **tag**, so `docker load` satisfies it:

```bash
docker save tensorfold-glm53:v0.6.0 | ssh max@10.100.216.2 docker load      # spark-1 -> spark-2 over the fabric
docker save -o tensorfold-glm53-v0.6.0.tar tensorfold-glm53:v0.6.0 && sha256sum tensorfold-glm53-v0.6.0.tar   # to a stick
docker load -i tensorfold-glm53-v0.6.0.tar                                    # on the closed Spark
docker image inspect -f '{{.Id}}' tensorfold-glm53:v0.6.0                     # must print the ID above
```

The ID compares across the Sparks because both use the same classic overlay2
store. `scripts/bundle.sh` explains why it would not compare across store kinds.

**OpenBeast's container engines** (`spark-node.sh` with vLLM or container
TensorFold): `docker save | docker load` keeps an image's ID but drops its
RepoDigest. A digest pin (`*_IMAGE_DIGEST`) would therefore make an offline
worker try a registry pull. **Pin by ID instead:**

- `VLLM_IMAGE_ID` / `TENSORFOLD_IMAGE_ID` in `spark.env` win over a digest.
- The container runs as `sha256:<id>`; docker never pulls an ID.
- `spark-cluster.sh sync-image IMAGE` prints the exact line to pin.
- `save-image IMAGE FILE` writes the image to a file for a cluster with no
  registry at all.

The container TensorFold engine still needs GitHub and PyPI at every start, so
it is not offline-capable. The native engine is.

### H5. Native TensorFold and the engine plane

On any connected box, the Mac included:

```bash
scripts/backends/tensorfold/install-native.sh --stage tf-1.2.7                       # stages linux-aarch64 by default
scripts/backends/tensorfold/install-native.sh --from tf-1.2.7 --platform linux-aarch64   # on the Spark
# or through the engine plane, which also pins and records history:
scripts/engine.sh stage tensorfold --version 1.2.9 --platform linux-aarch64 /media/usb/tf
scripts/engine.sh install tensorfold --from /media/usb/tf
```

Both ends verify:

- The `.sha256` must name exactly `tensorfold-<V>-linux-aarch64.tar.gz`.
- Its ed25519 signature is checked against the vendored key. That needs
  OpenSSL ≥ 3 or python3 `cryptography`. On macOS the script finds Homebrew's
  `openssl@3` itself; LibreSSL and OpenSSL 1.1 cannot check ed25519.

Under `OFFLINE=true`, `engine.sh update` without `--from` refuses and says why.

### H6. The command center: wheels and compose images

`./bootstrap.sh` needs PyPI and the compose images. `scripts/bundle.sh build`
packs both, but for the **build host's** platform and image store. A bundle
built on the Mac holds macOS wheels and fails on a Spark. Build it **on a Spark
while it is online** (aarch64, the same overlay2 store) and keep it:

```bash
./scripts/bundle.sh build ~/openbeast-bundle      # on spark-1, online
./scripts/bundle.sh sign  ~/openbeast-bundle --key ~/.ssh/openbeast-bundle   # optional; prints the allowed-signers line
./scripts/bundle.sh verify  ~/openbeast-bundle [--key ~/.config/openbeast/allowed-signers]   # on the closed Spark
./scripts/bundle.sh install ~/openbeast-bundle [--key …]                                     # same commit as the build
echo 'OFFLINE=true' >> ~/openbeast/openbeast.conf
./bootstrap.sh --no-start && ./scripts/doctor.sh  # doctor says "serves offline" vs "can be maintained offline"
```

Off a Spark, only the wheels can be cross-built:

1. Run `./scripts/pydeps.sh wheelhouse wheels-spark` with the manylinux aarch64
   flags it prints. One `--platform manylinux_2_39_aarch64` alone misses cffi.
2. On the Spark, run `./scripts/pydeps.sh install --from wheels-spark`.

The lock never travels with the wheels. `install --from` accepts only the
checkout's committed lock, or one vouched for with `--lock-sha256`.

The compose images still have to come from a Spark-side `docker save`. They are
multi-arch at the pinned digests, so a connected Spark pulls the arm64 ones.
`bundle.sh install` rewrites the compose references to the loaded content IDs
and keeps a `.pre-bundle` copy.

`bootstrap.sh`'s OFFLINE message ("bundle.sh build on a connected box") does
not say the box must be aarch64. That is a known issue below.

### H7. Test and language toolchains

**pytest.** It is not in `agents/requirements.lock`, and neither spark-1's
`python3` (3.12.3) nor the Mac's default `python3` has it. The hash-pinned CI
closure is `.github/ci-requirements/test.txt`. This recipe was verified on the
Mac with python 3.12 on 2026-11-15 (95 tests passed). On a Spark it is the same
commands with `/usr/bin/python3`.

```bash
python3.12 -m venv ~/.venvs/openbeast-test
~/.venvs/openbeast-test/bin/python -m pip install -q --require-hashes -r ~/openbeast/agents/requirements.lock
~/.venvs/openbeast-test/bin/python -m pip install -q --require-hashes --no-deps -r ~/openbeast/.github/ci-requirements/test.txt
# offline: fill a wheelhouse online with the same files, then install with --no-index --find-links DIR
python3 -m pip download --require-hashes --no-deps -r .github/ci-requirements/test.txt -d wheels-test   # VERIFY ON HARDWARE for aarch64
```

**zig 0.16.** Nothing in the stack downloads a zig *compiler*. For
`language_reference` and the zig eval variants on a Spark, install it by hand,
pinned like CI's: `zig-aarch64-linux-0.16.0.tar.xz`, sha256
`ea4b09bfb22ec6f6c6ceac57ab63efb6b46e17ab08d21f69f3a48b38e1534f17` (ziglang.org
index, read 2026-11-13). `lang-library.sh acquire zig` fetches the x86_64
tarball on any host, and that is fine: it is read as a std-source corpus, never
executed. No zig is installed on spark-1 today.

**Rust, Go.** OpenBeast ships no acquirer for either. For an offline Spark, carry
the official standalone aarch64 installers with the sha256 the vendor publishes,
and check it before installing. VERIFY ON HARDWARE; none is installed on spark-1
today. spark-1 has only gcc / g++.

**The language library** (`scripts/lang-library.sh acquire | check | list |
verify`) lives outside git. Acquire it online and copy `lang-library.sh where`'s
directory.

### H8. beast-docs mirror

```bash
./scripts/docs.sh sync swiftui --max-pages 300 --accept-terms   # online box
./scripts/docs.sh pack mirror.tar.gz                             # writes mirror.tar.gz + mirror.tar.gz.sha256
scp mirror.tar.gz mirror.tar.gz.sha256 spark-1.tail4109f9.ts.net:
ssh spark-1.tail4109f9.ts.net 'cd ~/openbeast && ./scripts/docs.sh unpack ~/mirror.tar.gz && ./scripts/docs.sh status'
```

`unpack` checks the tarball's hash first. It refuses absolute paths, `..`,
links and devices. It stages, requires the member set to equal `BUNDLE.json`,
re-hashes every file, and only then swaps each source in and reindexes. The
tarball hash proves integrity, not authenticity; sign the `.sha256` the way
`bundle.sh sign` does if you need authenticity.

### H8a. beast-ark: the rest of the software, on a drive

H2-H8 carry OpenBeast itself. Building or testing a larger system next to
the pair needs much more software. beast-ark
([BEAST_ARK.md](BEAST_ARK.md)) holds it: 112 open-source packages, each
pinned to a commit, with a sha256 or digest for every file and a build for
linux-aarch64. That includes Kubernetes and k3s with their images,
containerd, Docker, Go, Rust, Zig 0.16, Node, Temurin, Postgres, NATS,
Prometheus and Grafana, headscale, Forgejo, step-ca, Trivy with its DB, and
DevDocs. Fill a drive on any connected box, then restore on the Spark with
no network:

```bash
./scripts/ark.sh fetch --to /Volumes/ARK --profile core --platform linux-aarch64   # ~5.4 GiB + source
# plug the drive into the Spark (or rsync it over the cable)
/media/max/ARK/ark verify /media/max/ARK                       # re-hash; signatures re-checked
/media/max/ARK/ark restore go --from /media/max/ARK            # → ~/.local/opt/go-1.27.4, ~/.local/bin/go
/media/max/ARK/ark restore k3s --from /media/max/ARK           # + the air-gap image bundle (BEAST_ARK §8.4)
/media/max/ARK/ark serve /media/max/ARK --tailnet              # registry + pip/npm/go mirrors for the other boxes
```

The drive runs its own copy of the tool (`DRIVE/ark`), so it needs no
checkout. The Spark's python 3.12 is enough. On a box with no python, the
wrapper unpacks the CPython archived on the drive after checking it against
`SHA256SUMS`. Verified on spark-2 on 2026-11-15 (Zig 0.16.0 with its
minisign signature, and jq: fetched, verified, restored, run). `fetch`,
`lock` and `update` exit 3 under `OFFLINE=true` or island mode. The
TensorFold and llama.cpp pins in the ark are the repo's own (H5, H6), held
equal by tests.

### H9. The order for a pair built entirely offline

This order has not been run end to end. Every piece is measured on its own, and
the sequence is VERIFY ON HARDWARE.

1. Cable, NVIDIA Sync addressing (A2, B1). The repo from a bundle (H2).
2. `spark-root-setup.sh spark-N --debs debs --guard` on each Spark (H3). Reboot once.
3. Weights: copy, `model-fetch --verify`, HF-cache hard links, rsync to workers,
   verify there (C1-C3). DFlash2 plus the blob loop (C4).
4. Recipe checkout, `local.sh` (D1), image via `docker load` with the ID
   checked (H4). Unit installed with `PREPARE=0`.
5. Native TensorFold, optional (H5).
6. The command center from a Spark-built bundle with `OFFLINE=true` (H6),
   `openbeast.conf` (E2), units (E3), reboot.
7. Optional: pytest venv, zig, beast-docs mirror (H7, H8).
8. Optional: everything else from a beast-ark drive (H8a). `ark verify`
   first, then `ark restore <name>` per tool, or `ark serve --tailnet` for
   the whole fleet.

---

## Reference

### Serving configuration (measured 2026-11-13)

DFlash2, 4 parallel streams, 1M-token window, fp8 KV (pool ~2.2-2.6M tokens at
the time; `KV_POOL_GIB=10` since 2026-11-15), q4 dense, thinking off by default
(Ablit), RoCE all-gathers on both rails.

| | 1 stream | 4 streams aggregate (each) | TTFT |
|---|---|---|---|
| prose | 43-44 tok/s | 145 tok/s (37) | ~160-250 ms |
| code | 68-70 tok/s | 224 tok/s (57) | ~170-260 ms |

With MTP instead (`DRAFTER=mtp`): 44 / 56 tok/s single, but **one request at a
time**; 4 queued requests wait 16-20 s. DFlash2 wins and is kept. Not yet
tried: `PARALLEL=8`, which needs a larger memory reserve (G4).

### Parity with the beast rig (verified 2026-11-15)

`openbeast.conf` on spark-1 enables what the rig ran in production (E2).

| Check | Result |
|---|---|
| `scripts/backends/conformance.sh --model GLM-5.3-Flash-EXL3 --heavy` | PASS: chat, stream, tools, tool_result, 2 parallel calls, max_tokens |
| `./agent.sh` (create / edit / run a file) | done in 5 iterations, 0 API errors |
| `./agent.sh` on a real repo fix, network tools disabled (SELF_DEVELOPMENT.md "Proof") | correct 2-file fix, test green, in 25 iterations / 68 s; hit the iteration cap before `task_done` |
| a WebUI chat that calls `bash` through the `:3001` tool server | executed server-side, answered `aarch64` / `20` |
| remote: every surface in E6 from off-LAN (phone hotspot) | 200; `:8443` 401 without a key, chat answers with one |
| `./scripts/doctor.sh` | 39 ok, 1 warning (SSD wear needs a sudoers rule) |

### Scaling: 1 to 8 Sparks

Scripts and their headers point here by this name. The content lives in the
linear sections:

- cabling and addressing per node count: [A2](#a2-cabling-and-addressing-for-1-2-3-and-4-8-sparks)
- root setup per node: [B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark), [B3](#b3-firewall-spark-guard)
- weights to every node: [C3](#c3-carry-the-weights-to-every-worker)
- `cluster.env`, `spark.env`, `spark-cluster.sh`, and which engine scales how far: [D2](#d2-native-tensorfold-through-spark-nodesh--spark-clustersh)
- the offline image pins: [H4](#h4-container-images)

### Offline staging

This is the old name for the staging steps, which the unit file's header still
uses. They are now:

- A, the .debs: [H3](#h3-debs-libnccl2-and-tailscale)
- B, the weights in the HF-cache layout: [C2](#c2-hard-link-into-the-hf-cache-layout-the-recipe-reads), [C3](#c3-carry-the-weights-to-every-worker)
- C, DFlash2: [C4](#c4-the-drafter-dflash2)
- D, the recipe image: [H4](#h4-container-images)
- E, native TensorFold: [H5](#h5-native-tensorfold-and-the-engine-plane)
- F, the command center: [H6](#h6-the-command-center-wheels-and-compose-images)

### Rebuild from scratch (short form)

1. Cable the CX-7 ports; NVIDIA Sync addresses both rails (A2, B1).
2. On each Spark: `sudo ~/openbeast/scripts/backends/spark-root-setup.sh spark-N --guard`
   (B2, B3). Offline: `--debs DIR` (H3). Reboot once.
3. Weights on spark-1, `model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify`,
   hard-link into the HF-cache layout, copy both to spark-2 with `-H` (C). The
   recipe never looks at `~/openbeast-models`.
4. Recipe to `~/openbeast-recipes/glm53`, `scripts/local.sh` as in D1,
   `scripts/prepare.sh` online. Offline: stage the image and DFlash2 (H4, C4)
   and leave `PREPARE=0`, as the unit does. Install the unit.
5. Native TensorFold, optional (D2, H5).
6. `~/openbeast`: `openbeast.conf` (E2), `./bootstrap.sh --no-start`, install
   both units (E3), reboot once, WebUI admin (E5), `setup-tailscale.sh` (E6),
   device keys (E7). Offline: H6.

### Known issues and errata (v1.9.0)

These are corrections to `RELEASE_NOTES_v1.9.0.md` and to this page as first
published, from the 2026-11-15 review:

- **ufw was never active.** The release notes and this page said ufw was
  "active by default", and that the root setup's rules were what let the ranks
  talk while keeping the LAN and tailnet out. ufw is installed but disabled on
  both Sparks, so the rules filtered nothing, and the unauthenticated
  rendezvous (29551) was reachable from the tailnet. Fixed by `spark-guard.sh`
  (B3), which runs on both Sparks: `active` on 2026-11-15, and 29551 closed
  from the Mac.
- **sshd accepts passwords** on both Sparks, and `max` is in `sudo` and
  `docker`. Hardening is opt-in: `spark-guard.sh --harden-ssh`.
- **Offline images for `spark-node.sh`**: pin `*_IMAGE_ID`, not a digest (H4).
  The container TensorFold engine needs GitHub and PyPI at every start.
- **The served snapshot is not what the lock names.** The recipe serves the
  HF-cache snapshot; the lock covers `~/openbeast-models/...`. They are the
  same bytes only while hard-linked (C2). DFlash2 is outside every lock (C4).
- **Plaintext device keys on spark-1** (E7): delete them once the devices hold
  their keys. The same goes for `webui-max-password` once it is changed (E5).
- **The recipe container runs with `--network host`**, as root, from
  third-party alpha code. Anything in it can reach `127.0.0.1:8000` (no key, no
  gate audit) and the loopback-trusting beast-chat / beast-artifact ports
  (`OPENBEAST_CHAT_LOGIN_FROM=loopback` trusts any local TCP peer's
  `Tailscale-User-Login`). **beast-chat: fixed on the pair (2026-11-15)** with
  `CHAT_LOGIN_FROM=unix` (E2), so the header counts only on the 0600 socket
  tailscaled proxies to. Still open: beast-artifact's loopback reads, and the
  recipe's network mode.
- **`bootstrap.sh` OFFLINE hint** points at `bundle.sh build` "on a connected
  box" without saying it must be an aarch64 Spark (H6).
- **`setup-client.sh` writes the opencode v1 MCP shape** (`mcp.<name>` +
  `enabled`). On opencode v2 that entry is not merged with `mcp.servers` (F3).
- **Checkout skew**: spark-1 and spark-2 are on different commits, both behind
  the branch head (0).

### Not done yet

- zig for `language_reference` on a Spark (H7), and a beast-docs mirror on spark-1 (F5).
- A `svc:spark-hydra` Tailscale Service name (an admin-console step), and the
  beast rig's hydra node for the pair (`https://spark-1.tail4109f9.ts.net:8443`,
  `engine = "openai"`, a gate device key), once the rig is back on.
- Switching the primary between `beast` and `spark-hydra` (clients + hydra).
- A `PARALLEL=8` measurement; native TensorFold 1.2.7 (D2) vs the recipe.
- A fully offline rebuild of the pair, end to end (H9).
- `tests/test_scripts.sh` showed 7 failures when run from a non-git copy on
  spark-2 (`test_uninstall.sh` among them). Rerun from a git checkout before
  merging.
