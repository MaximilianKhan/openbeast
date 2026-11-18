# Spark backup and restore: one backup, N Sparks

**The goal.** Max asked for *"the cleanest backup and restore possible where
this one spark backup can be easily used to populate N sparks. Whether it
comes with the connect over QSFP for chaining sparks or otherwise, it needs to
include everything we already use and take advantage of out of the box."*

This page is the plan and the runbook. The tools are
`scripts/backends/spark-backup.sh` (inventory, backup, verify, list) and
`scripts/backends/spark-restore.sh` (restore and per-node provisioning).
Both read one items file, `scripts/backends/data/spark-backup.items`, and
both default to a dry run.

Labels used on this page:

- **MEASURED**: run or read on the pair on 2026-11-18.
- **CLAIMED**: taken from NVIDIA's documentation (URLs in [Sources](#sources)) and not yet run by us.
- **VERIFY**: designed and tested against fakes, but not yet run on hardware.

Neither tool has written a real backup or restore yet. On spark-1 they have
run only as `inventory` and as a dry run, from a copy in `/tmp` that was
deleted afterwards. The first real run is [§5.1 drill 0](#51-the-drills).

---

## 0. The verdict

1. **Method: hybrid.** Reinstall stock DGX OS from NVIDIA's recovery USB, then
   restore an **OpenBeast overlay** from a file-level backup. The overlay is
   the account's checkout, secrets, units, tools, recipes, weights and rank
   caches, plus the docker images and the WebUI volume. Do **not** clone a
   disk image to N nodes. An image copies what must be unique per node
   (machine-id, ssh host keys, Tailscale's node key, Docker's engine ID,
   NVIDIA Sync's per-port netplan, the DGX serial record). It also skips the
   firmware update the recovery USB performs, and it is 3.7 TB per copy for
   1.2 TB of data.
2. **Format: `rsync -aHAX --numeric-ids`** onto an **ext4 filesystem inside
   LUKS2** on the 4 TB drive. Each backup is a dated generation, and
   `--link-dest` links unchanged files to the previous generation, so each new
   generation costs only what changed. A manifest records a sha256 for every
   file. DGX OS ships `rsync`, `cryptsetup` and `zstd`; restic, borg and age
   are not installed (MEASURED, `dpkg -l`). So nothing has to be installed to
   take a backup or to restore one offline.
3. **Size (MEASURED, decimal units).** **943 GB of files** plus about 54 GB of
   docker images (uncompressed; zstd makes them smaller) plus 1 GB of WebUI
   data. The first generation is about **1.0 TB on the drive**. The `-H`
   matters: the same weights are hard-linked into `Documents/`, the HF caches
   and `openbeast-models/`. Without `-H` the 723 GB of weights would take
   about 1.4 TB. 212 GB of rank files for packs that do not serve are left
   out and rebuilt when needed.
4. **N Sparks.** One backup, one fleet file
   (`scripts/backends/fleet.env.example`), and one
   `spark-restore.sh --index N` per node. Each node gets its own address,
   rank, Up ports, ssh peers, unit set and tailnet name. Root setup, Tailscale
   re-auth and fabric addressing stay per node and are done by hand. They are
   printed, never copied.
5. **A second drive is required** for the OS. NVIDIA's `CreateUSBKey` script
   **erases** the stick it writes, which must hold at least 16 GB (CLAIMED).
   It cannot share the backup drive.
6. **Recovery time (estimate):** about **2.5 to 3.5 hours** from bare metal to
   a serving pair with an NVMe-class USB drive, or **4 to 5 hours** with a
   spinning 4 TB drive ([§5.2](#52-recovery-time)).

---

## 1. What one Spark holds

`scripts/backends/spark-backup.sh inventory` walks the live home directory
against the items file. Every entry lands in exactly one class, and anything
no line claims is listed as UNCOVERED, so nothing is left out silently. On
spark-1 (`spark-3dc6`), 2026-11-18, MEASURED, every top-level entry was
claimed:

| Category | Size (unique) | Decision | Why |
|---|---|---|---|
| Weights: `Documents/` pack folders, `openbeast-models/deepseek-hf/hub` (DeepSeek base with the Engram shards, EAQ v1 and v1.1-v2 packs), `openbeast-models/glm53-flash-ablit-exl3`, `.cache/huggingface` (GLM layout plus the DFlash2 drafter blobs) | **722.6 GB** | **back up** | days to re-download, impossible offline. Hard-linked across the three trees: one rsync `-H` pass |
| Rank files of the **serving** pack (`tensorfold-dsv41/04c13f12/` rank 0, `worker-rank-cache-04c13f12/` rank 1) | **212.2 GB** | **back up** | rebuildable in about 15 min each (`spark-deepseek-prepare-local.sh`), but copying 106 GB from a USB SSD takes about 2 min. `--no-rank-caches` leaves them out |
| Rank files of other packs (`tensorfold-dsv41/45ee5d0f/`, `worker-rank-cache/`) | 212.2 GB | **rebuild** | `spark-deepseek-pack.sh use` rebuilds them when that pack is selected |
| Tools and config: `~/openbeast` (with `.git` and the **uncommitted** changes), `~/.local/{bin,opt,lib}` (native TensorFold 1.2.7, `beast-spark`, hf), `~/.opencode` (opencode v2.3.2), `~/.venvs/spark-tools`, `~/openbeast-recipes` (deepseek-prep runtime, rollback, conformance records; glm53), units in `~/.config/systemd/user`, `.openbeast` (docs mirror), `backups/`, `Downloads/`, the serving kit, kernel caches | 8.5 GB | **back up** | small, and what makes the node "ours" |
| Secrets: `openbeast.conf` (+ `.bak-*`), `~/.config/openbeast/{gate-keys/,tensorfold-api-key,hf-token,webui-max-password}`, `~/.ssh/{authorized_keys,id_ed25519_nvsync_cluster_assistant}`, `~/.gnupg` | 118 KB | **back up, encrypted drive** | listed by path, never printed. `inventory` flags a secret readable by group or other |
| Per-node files: `spark.env`, `cluster.env`, `~/.ssh/config`, `~/.ssh/known_hosts`, `default.target.wants/` links | 10 KB | **back up for the record, regenerate per node** | see [§3.1](#31-what-is-unique-per-node) |
| Docker images: `tensorfold-dsv41:exl3-tp2-8a98ae503d85` (24.9 GB), `tensorfold-glm53:v0.6.0` (24.6 GB), the compose-pinned `open-webui` (4.1 GB) and `searxng` (0.3 GB), `alpine` | 53.9 GB raw | **back up** (`docker save \| zstd`) | the inference images are built or pulled online. A loaded image has no RepoDigest, so the restore rewrites the compose pins to the loaded IDs (the same fix as `bundle.sh`) |
| `nvcr.io/nvidia/pytorch:26.07-py3` | 24.3 GB | optional (`--with-optional-images`) | the container engine's base. Not serving |
| Docker volume `openbeast_open-webui-data` (users, chats, settings) | about 1 GB | **back up** | exported with the container **paused** |
| Caches (pip, browser, mesa, `.nv`), snap, desktop folders, `.bash_history` | 4.8 GB | **skip** | rebuildable or noise. The shell history can hold pasted secrets |
| System: DGX OS 7.6.0 (OTA) on Ubuntu 24.04.5, kernel `7.0.0-1025-nvidia`, driver 580.178.04, CUDA 13.0.3, Docker 29.6.2, nvidia-container-toolkit 1.20.1, libnccl2 held, tailscale 1.104.7, netplan, `/etc/openbeast/spark-guard.nft` | (the OS) | **regenerate**: recovery USB + `spark-root-setup.sh` + `spark-fabric-mtu.sh` | recorded read-only in each generation's `system/` (dpkg list, dgx-release, ibdev2netdev, units, `tailscale serve status`) so a restore can be compared with the original |

**spark-2 (`spark-9f26`, MEASURED):** 1,039 GB in its home directory. Nearly
all of it is the same weights and rank files as spark-1 (the backup
convention mirrors `Documents/`). What only spark-2 has is small:
`opencode-model-link.service`, `~/offline-stage/debs` (the pinned libnccl2
and tailscale `.deb`s), scratch test clones, and `openbeast-backups/`. The
`--config-only` mode covers it in about 1 to 2 GB ([§2.9](#29-spark-2-and-other-workers)).

**Size on the 4 TB drive.** The first generation is about **1.0 TB**:
943 GB of files, the images after zstd (no more than 54 GB), and 1 GB of
WebUI data. A spark-2 config-only generation adds 1 to 2 GB. Formatted with
`-m 0`, the drive gives about 3.9 TB (3.6 TiB). That leaves about **2.8 TB**
for later generations. Each one costs only its changes; a new pack, for
example, is 120 to 200 GB of shards plus 212 GB of rank files. To hold two
**independent** full copies, use a second drive ([§2.8](#28-schedule-and-retention)).
Generations on one drive share their unchanged blocks.

---

## 2. Backup

### 2.1 Which method, and why

| | Whole-disk image (dd, partclone, Clonezilla) | Backup tool (restic, borg) | **Hybrid: stock DGX OS + rsync overlay (chosen)** |
|---|---|---|---|
| Restore one node | byte-exact | exact for files; OS by hand | OS from NVIDIA's USB, overlay exact |
| Populate N nodes | clones identity (machine-id, host keys, Tailscale key, Docker ID) and netplan port names. Needs per-clone surgery on a filesystem you cannot boot first | fine | **built for it**: per-node files are written, never copied |
| Firmware | not updated | not updated | updated by the recovery USB (CLAIMED) |
| Size | 3.7 TB raw, or about 1.2 TB used with partclone. The live system must be offline to image it consistently | small, dedup and compression (weights barely compress) | about 1.0 TB. Hard links are kept natively, and generations link unchanged files |
| Consistency | needs the node shut down (booted from USB) | live | live: weights are immutable, the WebUI container is paused for its copy |
| Tools on DGX OS | partclone/Clonezilla on a boot stick | **not installed** (installing offline is its own problem) | `rsync`, `cryptsetup`, `zstd` **already installed** (MEASURED) |
| Read it without the tool | no (an image) | no (a repository format) | **yes**: a plain tree. `sha256sum -c SHA256SUMS` checks it |

A disk image is the wrong tool for N nodes. Every node it is cloned to comes
up as the same machine to systemd, sshd, Tailscale and Docker. The netplan
also names the ports of the source node: spark-1's cable is in port 0
(`enp1s0f0np0`, `enP2p1s0f0np0`) and spark-2's in port 1 (`enp1s0f1np1`,
`enP2p1s0f1np1`), MEASURED. An image is a fine **extra** for one node you
want back bit for bit. If you want that, take a Clonezilla image of spark-1
during a planned outage on another drive. It is not the path this plan
depends on.

### 2.2 What is excluded

- **Kernel filesystems and the OS** (`/proc`, `/sys`, `/dev`, `/run`, `/tmp`,
  `/var/lib/docker`, `/etc`). The backup is of the account, never `/`.
  Nothing outside `/home/max` is copied, so none of those can sneak in. The OS
  comes from NVIDIA's USB, and the root bits from `spark-root-setup.sh`.
- **Rebuildable caches**: `~/.cache` apart from the HF cache and the kernel
  caches, `.nv` (CUDA JIT), snap, browser profiles, desktop folders.
- **Sockets and pid files**: `~/openbeast/.run/`.
- **Other packs' rank files** (class `rebuild`, 212 GB).
- **Optionally**: the serving rank files (`--no-rank-caches`, saves 212 GB,
  costs about 15 min per pack at restore) and every image (`--no-images`).

### 2.3 Hard links, symlinks and size

The weights exist once on disk and are hard-linked into three trees:

- `Documents/<pack>/`
- `openbeast-models/deepseek-hf/hub/models--*/snapshots/<rev>/`, through
  `spark-deepseek-pack.sh` staging
- `~/.cache/huggingface/hub/` (GLM, through [SPARK_HYDRA.md C2](SPARK_HYDRA.md#c2-hard-link-into-the-hf-cache-layout-the-recipe-reads))

On spark-1, of the files over 1 GB, 96 have 2 links, 164 have 3 and 60 have 4 (MEASURED). The HF cache
layout also uses relative symlinks (`snapshots/r/file -> ../../blobs/<sha>`).

So the backup is **one** `rsync -aHAX` over all three trees:

- `-H` sees the links only within one run.
- `-a` keeps the symlinks.
- `--numeric-ids` keeps uid 1000, which is `max` on both Sparks (MEASURED).

The inventory's NEW column counts each inode once, which is how the 943 GB
was measured. The test suite checks that a shard linked into three trees
arrives as one inode, in the backup and again after a restore.

### 2.4 The drive's filesystem: ext4

| Filesystem | Verdict |
|---|---|
| **ext4** | **use it**: owners, modes, hard links, symlinks, ACLs and xattrs. DGX OS reads it with no extra package |
| xfs, btrfs | also accepted by the tool |
| exFAT, vfat, NTFS | **refused**: no POSIX owners or modes, no hard links (the 723 GB of weights would become about 1.4 TB), no symlinks (the HF snapshot layout breaks), and vfat has a 4 GB file limit (shards and rank files are bigger) |
| APFS, HFS+ | refused: not writable from Linux |

The Mac cannot read an ext4 or LUKS drive natively. That is acceptable: the
drive is used only on a Spark. Plug it into either Spark, or into a third
one.

### 2.5 Secrets: encrypt the whole drive with LUKS2

The backup holds every credential of the node: `openbeast.conf`, the gate
device keys, the TensorFold API key, the HF token, the WebUI password and the
cluster ssh key. `spark-backup.sh run` **refuses** a destination that is not
on a dm-crypt device unless `--allow-unencrypted` is passed. Whole-drive
LUKS2 encrypts the weights too. That costs little: the GB10's Arm cores have
AES instructions, and the USB link is the bottleneck. In exchange, one
mechanism covers everything, and secrets never sit in a second format. age
is not installed, and `openssl enc` has no authenticated mode worth trusting
for this.

One-time preparation of the drive, on a Spark, as Max. This is the only root
step of a backup.

```bash
lsblk -o NAME,SIZE,MODEL,TRAN               # find the USB drive: /dev/sdX — triple-check, this erases it
sudo cryptsetup luksFormat --type luks2 /dev/sdX          # passphrase: password manager + paper, never on a Spark
sudo cryptsetup luksHeaderBackup /dev/sdX --header-backup-file obbackup-luks-header.img   # keep OFF the drive
sudo cryptsetup open /dev/sdX obbackup
sudo mkfs.ext4 -m 0 -L obbackup /dev/mapper/obbackup      # -m 0: no root reserve on a data drive
sudo mkdir -p /media/max/obbackup && sudo mount /dev/mapper/obbackup /media/max/obbackup
sudo chown max:max /media/max/obbackup
```

Every later use:

```bash
sudo cryptsetup open /dev/sdX obbackup && sudo mount /dev/mapper/obbackup /media/max/obbackup
# ... back up / restore ...
sudo umount /media/max/obbackup && sudo cryptsetup close obbackup
```

The GNOME desktop on DGX OS also offers to unlock a LUKS drive when it is
plugged in. Either way works.

### 2.6 Consistency without stopping the model

spark-1's root is plain ext4 on `nvme0n1p2`, with no LVM and no btrfs
(MEASURED). So there is no snapshot to take. Instead:

- **Weights and rank files** are written only by a fetch or a pack swap and
  are otherwise immutable. **Do not run a backup during
  `spark-deepseek-pack.sh use` or a model fetch.** Every other moment is
  consistent for them, with the model serving.
- **The Open WebUI database** (SQLite) is the one live writer that matters.
  The backup **pauses** every container that uses the volume
  (`docker pause`, which freezes it and does not restart it), tars the
  volume through a throwaway `alpine` container, then unpauses. That gives a
  crash-consistent copy, and SQLite is built to recover from exactly that
  state. The pause lasts for the copy of about 1 GB: seconds. `--no-pause`
  copies live.
- **The checkout, logs and recipe records** change rarely. A log rotated
  mid-copy makes rsync exit 24, which the tool accepts.
- **For a perfectly quiet backup**, for example before an OS reinstall, stop
  the command center and inference through their units first
  (`systemctl --user stop openbeast deepseek-v41-inference`), back up, and
  start them again.

### 2.7 Running it

```bash
cd ~/openbeast
scripts/backends/spark-backup.sh inventory                       # what will be in it; anything UNCOVERED?
scripts/backends/spark-backup.sh run --to /media/max/obbackup    # dry run: drive checks, filter, images, volume
scripts/backends/spark-backup.sh run --to /media/max/obbackup --apply
scripts/backends/spark-backup.sh verify /media/max/obbackup/openbeast-backup/spark-3dc6/latest --deep
```

The drive's layout:

```text
/media/max/obbackup/openbeast-backup/
  spark-3dc6/                       one directory per node, by hostname
    20261118T090000Z/               a generation (UTC)
      home/                         the overlay, laid out as /home/max
      docker/                       <image>.tar.zst, volume-<name>.tar.zst, images.json, volumes.json
      system/                       read-only record: os/dgx release, dpkg, netplan file names, fabric, units, tailscale serve, git status
      ITEMS                         the items file this generation was made with (the restore reads THIS one)
      FILTER                        the rsync filter it produced
      MANIFEST.jsonl                path, size, mtime, mode, sha256, hard-link group, symlink target
      SHA256SUMS                    the same hashes for `sha256sum -c`
      BACKUP.json                   host, user, uid, serving revision, image IDs, sizes, timings
    latest -> 20261118T090000Z      moved only after a generation completes
  spark-9f26/                       spark-2's --config-only generations
```

A generation is written as `<stamp>.partial` and renamed only when it is
complete. An interrupted run never becomes `latest`. The next run starts a
fresh generation, still linked against the last good one.

**Incremental runs.** `--link-dest` turns every file unchanged since
`latest` into a hard link of it. An unchanged image (same ID) is linked, not
saved again. A hard-linked file keeps the previous generation's hash, so
only new bytes are hashed. A second generation with no new pack costs a few
GB and minutes.

### 2.8 Schedule and retention

| When | What |
|---|---|
| **Before anything risky**: an OTA or OS upgrade, a driver or kernel change, an Open WebUI pin move, a pack swap | `run --apply` (quiet: units stopped, [§2.6](#26-consistency-without-stopping-the-model)) |
| **After** a new pack is serving | `run --apply` (its shards and rank files land once) |
| **Weekly**, while the drive is attached | `run --apply`, then `verify` |
| **Monthly** | `verify --deep` on `latest` (re-hashes about 1 TB: 20 to 30 min on an SSD) |
| **Quarterly** | a restore drill ([§5](#5-restore-drills-and-recovery-time)) |

Keep the last 3 generations plus the first one of each month. Delete a
generation with `rm -rf <stamp>`. The hard links make that safe: a file
stays on the drive while any generation still links it. Generations share
their unchanged blocks, so **one drive is one copy**. A bad sector in a shard
damages that shard in every generation. `verify --deep` finds that. A second
drive, kept elsewhere, is what protects against it. For a second drive, run
the same command with `--to` set to that drive.

### 2.9 spark-2 and other workers

A worker's weights and rank-1 file are byte-identical to the head's (the pack
script mirrors them). So the **head's** backup is the one that populates
workers. A worker's own unique state is small. Back it up with:

```bash
scripts/backends/spark-backup.sh run --to /media/max/obbackup --config-only --apply   # on spark-2: about 1-2 GB
```

That covers spark-2's units, staged debs and scratch. Before the first head
backup, copy spark-2's `~/offline-stage/` to spark-1. The head's backup then
carries the pinned `.deb`s, and every restore can run
`spark-root-setup.sh --debs ~/offline-stage/debs` offline.

---

## 3. Reinstall the OS, then restore

### 3.1 What is unique per node

| Identity | Where | On restore |
|---|---|---|
| Hostname | `/etc/hostname`, set at install (`spark-3dc6`, `spark-9f26`) | **left as DGX OS set it**. OpenBeast's names are the tailnet names (`spark-1`, `spark-2`, ...) |
| machine-id | `/etc/machine-id` | **never copied**. A clone confuses DHCP client IDs, journald and systemd |
| ssh host keys | `/etc/ssh/ssh_host_*` | **never copied**. New keys at install. `known_hosts` is not restored; the restore's ssh block uses `StrictHostKeyChecking accept-new` on the fabric aliases |
| Tailscale node | `/var/lib/tailscale` (root) | **never copied**: two nodes with one key fight over one identity. `spark-root-setup.sh spark-N` runs `tailscale up --hostname=spark-N` (re-auth by URL). Remove the old machine in the admin console first, or the new one becomes `spark-1-1`. Then `setup-tailscale.sh` on the head recreates the serve config (recorded in `system/tailscale-serve.txt`) |
| Fabric addresses and ports | NVIDIA Sync's `/etc/netplan/99-nvidia-sync-cluster.yaml`; our `99-zz-openbeast-fabric-mtu.yaml` | **never copied**. Sync addresses the rails per node. `spark-fabric-mtu.sh` stages MTU 9000 per node. The restore reads the ports that are Up (`ibdev2netdev`) into `SPARK_IFACE` / `SPARK_IB_HCA` |
| Rank and role | `spark.env` (`SPARK_NODE_IP`, `SPARK_HEAD_IP`), the head's `cluster.env` (`CLUSTER_NODES`), the recipe's `local.sh` (`WORKER`, `FABRIC_PEER`) | **written by the restore** from the fleet file and `--index` |
| Worker-only files | `worker-rank-cache-<REV8>/` (rank 1 of a TP-2 pair) | restored to every node (the head builds and pushes it). The rank-0 file goes to the head only. For TP > 2, rank files differ per rank: rebuild them |
| Boot units | `default.target.wants/` links | **not copied**. The restore enables the role's units: head `openbeast`, `deepseek-v41-inference` and the two timers; worker `opencode-model-link` |
| Gate device keys | `~/.config/openbeast/gate-keys/` (head) | come back with the head, so enrolled clients keep working. **A second, independent pair needs its own** (`scripts/clients.sh enroll`): one device key must not open two gates |
| Docker engine ID, image store | `/var/lib/docker` | **never copied**. Images are re-loaded. The compose digest pins are rewritten to the loaded IDs (the original is kept as `docker-compose.yml.pre-restore`) |
| NVIDIA licence and EULA | first-boot wizard | **by hand on each node** |
| DGX serial and OTA state | `/etc/dgx-release` | per unit, recorded only |
| Shared on purpose | `tensorfold-api-key` (the same on every node of a cluster), NVIDIA Sync's cluster ssh key `id_ed25519_nvsync_cluster_assistant` (the same on both Sparks, MEASURED by size) | restored everywhere: the head reaches its workers without re-running Sync's key step |

### 3.2 NVIDIA's recovery path (CLAIMED)

From NVIDIA's DGX Spark documentation (Founders Edition; OEM units may differ):

1. **Media.** Download the *DGX Spark System Recovery Image* (a `tar.gz`) and
   extract it. Run the creator **with administrator rights**: Linux
   `CreateUSBKey.sh`, macOS `CreateUSBKeyMacOS.sh`, Windows
   `CreateUSBKey.cmd`. It needs **a USB drive of at least 16 GB and erases
   it**. Forum users report that Mac-made sticks sometimes fail validation;
   make it on a Spark or a Linux box. This is the **second drive**. It cannot
   be the backup drive.
2. **UEFI.** Disconnect other external storage (**unplug the backup
   drive**), insert the stick, power on and press **Esc or Del**. Use a wired
   USB keyboard; Bluetooth may not work. Then: *Save & Exit*, *Restore
   Defaults*, *Load Optimized Defaults*, *Save Changes and Reset*. Enter UEFI
   again, then *Save & Exit*, *Boot Override*, and pick the USB stick. If it
   boots the installed OS instead, disable **Secure Boot** under *Security*,
   save, and boot-override again. Recovery turns Secure Boot back on.
3. **Recovery.** *START RECOVERY*. It reflashes firmware and **completely
   erases the internal SSD**, then reinstalls DGX OS. NVIDIA gives no
   duration. Forum reports say it is faster than the 40+ minute ISO install.
4. **First boot** (the OOBE). Either a monitor, keyboard and mouse, or a
   Wi-Fi hotspot plus a browser (SSID on the Quick Start sticker). Language,
   time zone, keyboard, **terms**, **user account**, optional telemetry,
   network, then **"download and install the full software image"** (it may
   reboot several times; do not interrupt it). It wants a fast internet
   connection. For an unreliable link NVIDIA suggests the recovery media
   instead. **There is no documented fully offline first boot** (see open
   question 1).
5. **System Repair** is the non-destructive alternative when the OS will not
   boot but the disk is fine: an older kernel from GRUB (hold Shift+Insert),
   or an Ubuntu ARM64 live USB with a chroot into `/dev/nvme0n1p2`. Try it
   before recovery when the data matters and the backup is old.

### 3.3 Bare metal to serving: the order

For each Spark. Times are estimates; [§5.2](#52-recovery-time) adds them up.

| # | Step | Who | Offline? |
|---|---|---|---|
| 1 | Recovery USB, firmware and OS ([§3.2](#32-nvidias-recovery-path-claimed)) | Max: UEFI keys, *START RECOVERY* | yes (the stick carries the image) |
| 2 | First boot: user **`max`** (the first account is uid 1000, matching the backup; the restore refuses another uid), EULA, network | Max | VERIFY (step 4 above) |
| 3 | `sudo usermod -aG docker max`, then log out and in. Docker, the driver, CUDA and nvidia-container-toolkit come with DGX OS | Max | yes |
| 4 | Cable the QSFP ports. Pair with **NVIDIA Sync** on the Mac: it addresses the rails, writes its netplan and installs the cluster ssh key and aliases | Max | Sync needs the Mac on the LAN, not the internet (VERIFY) |
| 5 | Mount the backup drive ([§2.5](#25-secrets-encrypt-the-whole-drive-with-luks2)) and copy the restore tool off it: `cp -a /media/max/obbackup/openbeast-backup/spark-3dc6/latest/home/openbeast ~/openbeast` (the drive carries the checkout, so no git or network is needed) | Max | yes |
| 6 | `scripts/backends/spark-restore.sh --from /media/max/obbackup/openbeast-backup/spark-3dc6/latest --fleet FLEET --index N` (dry run), then add `--apply` | Max | yes |
| 7 | `sudo scripts/backends/spark-root-setup.sh spark-N --guard --debs ~/offline-stage/debs`: libnccl2 pinned and held, linger, Tailscale installed and joined as `spark-N` (**re-auth URL**), spark-guard | Max | the Tailscale join needs a route; everything else does not |
| 8 | `sudo scripts/backends/spark-fabric-mtu.sh` (stage MTU 9000), then **reboot every Spark of the fabric together** | Max | yes |
| 9 | Head: `scripts/setup-tailscale.sh` (serve config), then `systemctl --user start deepseek-v41-inference openbeast` | Max | serve needs tailscaled up |
| 10 | Checks: `scripts/backends/spark-cluster.sh check` (head), `scripts/doctor.sh`, `scripts/backends/conformance.sh`, a chat from the Mac | Max | yes |

Step 6 on an already-restored node is idempotent. Images that are present
are skipped, rsync copies only differences, and a non-empty WebUI volume is
never overwritten without `--force-volumes`.

---

## 4. Populating N Sparks from one backup

### 4.1 The fleet file

`scripts/backends/fleet.env.example`. Parsed, never sourced. One line per
node, in rank order:

```bash
FLEET_NAME=spark-hydra
FLEET_USER=max
NODE_0=spark-1 head 10.100.216.1,10.100.217.1
NODE_1=spark-2 worker 10.100.216.2,10.100.217.2
```

The restore refuses a bad fleet before it touches anything:

- more than one head, or a head that is not `NODE_0`;
- a fabric address given to two nodes;
- fewer than 1 or more than 8 nodes;
- a name that is not a valid tailnet name.

For node N, the restore writes:

- **every node**: `spark.env` (`SPARK_NODE_IP`, `SPARK_HEAD_IP`, and
  `SPARK_IFACE` / `SPARK_IB_HCA` from the ports Up **on that node**), and a
  managed ssh block for the peers. NVIDIA Sync's own `Host` entries win when
  they exist.
- **head**: `cluster.env` (`CLUSTER_NODES="local max@<ip1> ..."`), the
  recipe's `WORKER` / `FABRIC_PEER` (pair only), and `TS_HOSTNAME`.
- **worker**: the `opencode-model-link` unit, pointed at the head.

Each replaced file is kept as `<file>.pre-restore`.

What each role receives:

| | head | worker |
|---|---|---|
| checkout, tools, docs mirror, shared keys (TensorFold API key, HF token, cluster ssh key) | yes | yes |
| weights, kit, rank-1 file | yes | yes |
| rank-0 file, recipes, `openbeast.conf`, gate keys, WebUI password, venvs, WebUI volume, compose images | yes | **no** |
| inference images (`tensorfold-dsv41`, `tensorfold-glm53`) | yes | yes |

### 4.2 QSFP topologies, and what our recipes support today

| Sparks | Fabric | What runs |
|---|---|---|
| 1 | none | rank 0 only (`spark-guard.sh --no-fabric`). The DeepSeek recipe does not fit one GB10; native TensorFold profiles with `TENSOR_PARALLEL_SIZE=1` do |
| **2 (today)** | one QSFP cable, both rails (`10.100.216.0/24`, `10.100.217.0/24`) | **the DeepSeek recipe (TP 2)** and the GLM recipe (TP 2). MEASURED |
| 3 | a ring needs a subnet per cable, which **`spark-node.sh` cannot rendezvous over** (`spark-cluster.sh check` refuses it). Use a switch, or one shared subnet | GLM's experimental `start-tp3.sh` handles a ring. The DeepSeek recipe is TP 2 only |
| 4 to 8 | a RoCE-capable 200 GbE switch, one port per Spark (NVIDIA's playbook covers 2 to 4 Sparks through a switch, CLAIMED) | `spark-cluster.sh` with native TensorFold profiles of `TENSOR_PARALLEL_SIZE=N`: built, tested against stubs, **not run past 2** |

So "N Sparks" means one of two shapes today:

- **N/2 pairs (works now).** Each pair is its own fleet: its own fleet file,
  head, gate, tailnet names and device enrollments. Restore one backup onto
  each pair with that pair's fleet file. Every pair serves the DeepSeek
  recipe exactly as spark-hydra does.
- **One cluster of N (VERIFY).** One fleet file with N nodes, restored N
  times. Serving then needs a TP-N profile through `spark-cluster.sh`. The
  restore keeps the TP-2 recipe's `local.sh` unchanged and says so. Rank
  files for TP > 2 are rebuilt per rank.

### 4.3 Seeding the workers fast

The drive seeds one node at USB speed. After that, the fabric is faster than
moving the drive:

- **From the drive, moved node to node:** about 1 GB/s on a USB 3.2 Gen 2
  NVMe enclosure (estimate).
- **From the head over the fabric:**
  `spark-restore.sh --from max@10.100.216.1:/media/max/obbackup/openbeast-backup/spark-3dc6/latest ...`
  reads the generation over ssh. That is one stream, 0.46 GB/s on a GB10
  (MEASURED for `sync-weights`), so about 30 min for a worker's 830 GB.
  Several workers can pull in parallel, until the drive's read speed is the
  limit.
- **Fastest for the weights:** restore the worker with `--skip-weights`,
  then on the head use `scripts/backends/spark-cluster.sh sync-weights`
  (4 streams, 1.73 GB/s MEASURED) for locked profiles, or rsync the weight
  trees with `-H` over several streams. VERIFY for the DeepSeek trees, which
  have no profile lock.

---

## 5. Restore drills and recovery time

### 5.1 The drills

None of these touches the live pair's serving state.

| Drill | How | How often |
|---|---|---|
| **0. First real backup** (not done yet) | the drive prep in [§2.5](#25-secrets-encrypt-the-whole-drive-with-luks2), `run` (dry), `run --apply`, then `verify --deep`. Check that `system/` and `docker/images.json` look right and that the open-webui image saves by digest (VERIFY) | once, at a quiet moment |
| **1. Manifest check** | `verify GEN --deep`: every file re-hashed, every hard-link group intact | monthly |
| **2. Drift check** | `verify GEN --against-live` on the node: files changed, new or gone since the generation. Read-only | before relying on an old generation |
| **3. Scratch restore** | restore into a scratch home on the head: `SPARK_BACKUP_HOME=/home/max/restore-drill SPARK_RESTORE_HOME_CHECK=0 spark-restore.sh --from GEN --fleet FLEET --index 1 --skip-images --no-units --apply --yes`, then `diff -r` the config trees against the live ones and `sha256sum -c` a few shards, then `rm -rf` the scratch. It needs disk for the weights (830 GB), so add `--skip-weights` to drill the config path alone. `--no-units` keeps it from enabling units in the real systemd, and `--skip-images` from touching docker | quarterly |
| **4. Image check** | in the scratch restore, `zstd -dc docker/tensorfold-dsv41*.tar.zst \| tar t \| head`, and once `docker load` on a node that lacks the image | with drill 3 |
| **5. Full restore** | on a **third** Spark when one arrives, or on spark-2 in a planned outage (the head keeps the model): the whole of [§3.3](#33-bare-metal-to-serving-the-order), time each step, fill in [§5.2](#52-recovery-time) | once, then after big changes |

### 5.2 Recovery time

These are estimates, apart from the measured rates noted. The column
assumes a USB 3.2 Gen 2 NVMe enclosure (about 0.9 GB/s), and the
parentheses a 4 TB spinning drive (about 0.2 GB/s).

| Step | One Spark |
|---|---|
| Recovery USB reflash and DGX OS install (CLAIMED: no figure given; forum: faster than the 40-min ISO) | 20 to 40 min |
| First boot, updates, user | 15 to 30 min (online) |
| Docker group, NVIDIA Sync pairing, mount the drive | 10 min |
| Overlay restore: 943 GB of files (830 GB on a worker) | 18 to 25 min (80 to 90 min) |
| `docker load` of about 54 GB | 5 to 10 min |
| Root setup, Tailscale re-auth, MTU, reboot | 10 to 15 min |
| First start: the model loads, conformance | 10 to 15 min |
| **Total, one Spark** | **about 1.5 to 2.5 h** (3 to 4 h) |
| **The pair** (worker restored over the fabric while the head finishes) | **about 2.5 to 3.5 h** (4 to 5 h) |
| Rank files rebuilt instead of copied (`--no-rank-caches`) | add about 15 min per pack |

The first full restore ([§5.1 drill 5](#51-the-drills)) replaces these
estimates with measurements.

---

## 6. All of OpenBeast's compute

| Machine | What exists today | Gap |
|---|---|---|
| **The Spark pair** | this page; the offline pieces in [SPARK_HYDRA.md H](SPARK_HYDRA.md#h-fully-offline-operation); `bundle.sh` (the command center's wheels and images); beast-ark (the rest of the software, [BEAST_ARK.md](BEAST_ARK.md)) | the first real run (drill 0) |
| **The 5090 rig `beast`** (x86_64, Arch, llama.cpp, GGUF weights) | weights are pinned and re-fetchable (`fetch-weight.sh`, `weights.registry`). The WebUI volume backup is a manual step in [UPDATING.md](UPDATING.md). `bundle.sh` installs OpenBeast offline. The rig is also a [beast-island](BEAST_ISLAND.md) node | **no backup of its own state.** `openbeast.conf`, the gate keys, the WebUI volume, opencode sessions and any local llama.cpp builds have no automated copy. `docs/TODO.md` already lists a "Backup/restore CLI (M)" for this. The items-file design here carries over: an items file for the rig (classes the same, weights from the registry), with `spark_backup.py` generalized past the Spark-only checks (aarch64 and GB10 in restore). Not designed here |
| **The Mac client** | `setup-client.sh` rebuilds the client. Its state is the per-rig gate keys (`~/.config/openbeast/*-gate.key`), the opencode config it writes, and opencode sessions | **no backup.** Time Machine covers the files only if it is on. The gate keys can be re-enrolled from the head (`clients.sh enroll`), so losing the Mac costs a re-enroll, not data. Recommendation: Time Machine on, plus re-enrolling as the documented recovery. Not designed here |
| **Software, offline** | beast-ark drive (pinned catalog), `bundle.sh`, `PYTHON_OFFLINE.md`, `TAILSCALE_OFFLINE.md` | the backup drive and the ark drive are separate on purpose (secrets versus public software). The NVIDIA recovery image itself is not in the ark catalog: keep the downloaded `tar.gz` on the ark drive or the Mac, so a stick can be made offline |

---

## 7. The tools

| File | What |
|---|---|
| `scripts/backends/spark-backup.sh` | `inventory` (read-only), `run --to DIR [--apply]`, `verify GEN [--deep] [--against-live]`, `list DIR`. Never root, never writes outside `--to`, refuses non-ext4 and non-LUKS drives |
| `scripts/backends/spark-restore.sh` | `--from GEN --fleet FILE --index N [--apply]` (`--no-units` for a scratch drill). Dry run by default; `--apply` asks for the node name (or `--yes`). Never root, never `--delete` |
| `scripts/backends/pylib/spark_backup.py` | the engine (stdlib, python >= 3.9) |
| `scripts/backends/data/spark-backup.items` | **what a backup is.** Edit it when something new lives on a Spark. `inventory` shows anything unclaimed |
| `scripts/backends/fleet.env.example` | the fleet file |
| `tests/test_spark_backup.sh` | hermetic: fake docker, findmnt, lsblk, zstd, nvidia-smi, ibdev2netdev and systemctl; scratch homes; real GNU rsync >= 3 for the copies (skipped without it). It covers refusals (exFAT, unencrypted, `/`, bad fleets, uid), hard links across three trees, the filter, generations and `--link-dest`, image reuse, pause and unpause, `verify --deep` catching a same-size edit, `--against-live`, the worker and head restores, per-node files, and the compose pin rewrite |

Test seams (environment): `SPARK_BACKUP_HOME`, `SPARK_BACKUP_ITEMS`,
`SPARK_BACKUP_SERVING_REV`, `SPARK_BACKUP_HOSTNAME`, `SPARK_BACKUP_NOW`,
`SPARK_BACKUP_UID`/`_GID`/`_USER`, `SPARK_RESTORE_HOME_CHECK=0`,
`SPARK_BACKUP_HELPER_IMAGE`, and the commands `DOCKER`, `RSYNC`, `ZSTD`,
`FINDMNT`, `LSBLK`, `NVIDIA_SMI`, `IBDEV2NETDEV`, `SYSTEMCTL`, `SSH`, `GIT`,
plus `UNAME_M`.

---

## 8. Open questions for Max

1. **Offline first boot.** NVIDIA's first-boot wizard downloads "the full
   software image" and documents no offline path. Is a connected first boot
   acceptable, with everything after it offline? Or should the first real
   drill find out what the wizard does with no network?
2. **What is the 4 TB drive?** An NVMe-class USB SSD or a spinning disk?
   That is the difference between 20-minute and 90-minute copies. A second
   drive for an independent copy, kept off-site, is recommended
   ([§2.8](#28-schedule-and-retention)).
3. **The LUKS passphrase and header backup**: where should they live? The
   proposal is the password manager plus paper, and the header image off the
   drive.
4. **Rank files**: keep backing up the serving pack's 212 GB (faster
   restore), or `--no-rank-caches` (smaller, plus 15 min per restore)?
5. **N Sparks**: more pairs, or one bigger cluster? That decides whether a
   switch is needed and whether a TP-N profile must be qualified before
   restoring onto it.
6. **pytorch base image** (24 GB): include it (`--with-optional-images`) or
   re-pull when needed?
7. **A disk image as well?** A one-off Clonezilla image of spark-1, on
   another drive and during a planned outage, gives a bit-exact way back for
   that one node. Wanted?

## Sources

- NVIDIA, DGX Spark User Guide, *System Recovery*: <https://docs.nvidia.com/dgx/dgx-spark/system-recovery.html>
- NVIDIA, *System Repair*: <https://docs.nvidia.com/dgx/dgx-spark/boot-repair.html>
- NVIDIA, *First Boot*: <https://docs.nvidia.com/dgx/dgx-spark/first-boot.html>
- NVIDIA, *Connect multiple Sparks* playbook (NVIDIA Sync or `setup-cluster.sh`; 2 to 4 Sparks, a switch with one 200 Gb/s link per device): <https://build.nvidia.com/spark/connect-two-sparks>
- Forum: *Reinstalling DGX OS on the DGX Spark* <https://forums.developer.nvidia.com/t/reinstalling-dgx-os-on-the-dgx-spark/347603>; *Cannot restore from recover image* <https://forums.developer.nvidia.com/t/cannot-restore-from-recover-image/350690>; *Community "Spark Recovery" project (data-preserving recovery; factory layout is one ESP + one root partition)* <https://forums.developer.nvidia.com/t/community-spark-recovery-project-data-preserving-os-recovery-for-dgx-spark/371102>
- Ours: [SPARK_HYDRA.md](SPARK_HYDRA.md) (the runbook), [DGX_SPARK_PLAN.md](DGX_SPARK_PLAN.md), [BEAST_ARK.md](BEAST_ARK.md), [BEAST_ISLAND.md](BEAST_ISLAND.md), [UPDATING.md](UPDATING.md)
