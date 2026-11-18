# Spark backup and restore: the pair, fully offline

**The goal.** Max asked for *"the cleanest backup and restore possible where
this one spark backup can be easily used to populate N sparks. Whether it
comes with the connect over QSFP for chaining sparks or otherwise, it needs to
include everything we already use and take advantage of out of the box."*
On 2026-11-18 he narrowed it ([§8](#8-decisions-and-open-questions)):

- everything works **fully offline**, the OS reinstall and first boot included;
- the backup drive is a **4 TB SSD**;
- the scope is **the pair**: two Sparks on one QSFP cable (spark-hydra).

This page is the plan and the runbook. The tools are
`scripts/backends/spark-backup.sh` (inventory, backup, verify, list, and the
golden-image plan) and `scripts/backends/spark-restore.sh` (restore, the
per-node identity, and the offline rebuild). Both read one items file,
`scripts/backends/data/spark-backup.items`, and both default to a dry run.

Labels used on this page:

- **MEASURED**: run or read on the pair on 2026-11-18, read-only.
- **VERIFIED**: tested by us, hermetically or read-only (named each time).
- **CLAIMED**: taken from NVIDIA's documentation or forum (URLs inline and in
  [Sources](#sources)), not yet run by us.
- **VERIFY**: designed and tested against fakes, not yet run on hardware.

Neither tool has written a real backup, image or restore yet. On spark-1 they
have run only as `inventory` and as a dry run. The first real runs are
[§5.1 drills 0 and G](#51-the-drills).

---

## 0. The verdict

1. **Method: a golden base image of DGX OS, plus the OpenBeast overlay.**
   - **The OS** comes back from our own **golden image**: a file-level copy
     of DGX OS 7.6.0 exactly as it runs today, taken from a live USB with
     `/home` and every per-node identity file left out. Restoring it skips
     NVIDIA's first boot entirely (the image is already past it), so it needs
     **no network** ([§3.2](#32-the-golden-base-image)).
   - **Each node's identity** (machine-id, ssh host keys, Tailscale state,
     NVIDIA Sync's fabric netplan, host name, serial) is written by
     `spark-restore.sh identity` from that node's own small **identity
     bundle**. A new unit gets fresh values instead.
   - **The overlay** (`spark-restore.sh`) is the account: checkout, secrets,
     units, tools, recipes, weights, docker images and the WebUI volume.
   - **What is derived is rebuilt offline, never copied**: DeepSeek's rank
     files, token maps and served folders, and GLM's compiled kernels
     ([§3.5](#35-the-offline-rebuild)).
2. **Format.** The overlay is `rsync -aHAX --numeric-ids` onto **ext4 inside
   LUKS2** on the 4 TB SSD, in dated generations linked with `--link-dest`.
   The golden image is a GNU `tar` of `/` plus a `dd` of the 298 MB EFI
   partition and an `sfdisk` dump of the GPT, on the same drive. Every tool
   used ships with DGX OS or with the Ubuntu live USB (MEASURED for DGX OS:
   `rsync`, `cryptsetup`, `zstd`, `tar`).
3. **Size (MEASURED inputs, decimal units).** About **0.85 TB** on the drive:
   - **731 GB** of files (VERIFIED: `inventory` with the new items file on
     spark-1, read-only, fed over ssh stdin so nothing was written there).
     That is the 943 GB measured before, minus the 212 GB of serving rank
     files, now rebuilt;
   - **at most 78 GB** of docker images, before zstd (the 24 GB PyTorch base
     image is now included);
   - about **25 GB** for the golden image;
   - about **6 GB** for NVIDIA's recovery image;
   - 1 GB of WebUI data and 1 to 2 GB for spark-2's own files.

   That leaves about **3 TB** of the 3.9 TB for later generations ([§1](#1-what-one-spark-holds)).
4. **Fully offline: yes, with caveats** ([§3.1](#31-offline-the-answer)). The
   golden image never runs NVIDIA's first boot. The caveats:
   - firmware (UEFI, EC, SSD) is not in a disk image;
   - a *replacement* unit needs NVIDIA Sync pairing, and its Tailscale join
     waits for the internet (serving does not);
   - it is all VERIFY until [drill G](#51-the-drills) has run on hardware.
5. **Drives: the SSD plus two USB sticks, and a third recommended**
   ([§2.5](#25-secrets-luks2-and-where-the-passphrase-and-header-live)):
   - the **4 TB SSD** (LUKS2): overlay, golden image, identity bundles,
     NVIDIA's recovery `tar.gz`;
   - **stick 1, the live USB** (8 GB or more): Ubuntu 24.04 arm64 desktop.
     It captures and restores the golden image;
   - **stick 2, the key stick** (any size, exFAT): the LUKS header backup
     (a second copy lives on the Mac);
   - **stick 3, NVIDIA's recovery USB** (16 GB or more, recommended): for
     firmware, or for a replacement unit. It can be made offline from the
     `tar.gz` on the SSD.
6. **Recovery time for the pair (estimate, SSD):** about **2 to 2.5 hours**
   from bare metal to GLM serving. DeepSeek is ready about 35 minutes later,
   once its rank files are rebuilt ([§5.2](#52-recovery-time)).

---

## 1. What one Spark holds

`scripts/backends/spark-backup.sh inventory` walks the live home directory
against the items file. Every entry lands in exactly one class. Anything no
line claims is listed as UNCOVERED, so nothing is left out silently. On
spark-1 (`spark-3dc6`), 2026-11-18, every top-level entry was claimed
(MEASURED). The classes changed on 2026-11-18, per Max's decision 4: model
checkpoints are kept, and anything that rebuilds offline is not.

| Category | Size (unique) | Decision | Why |
|---|---|---|---|
| Weights: `Documents/` pack folders, `openbeast-models/deepseek-hf/hub` (DeepSeek base with the Engram shards, EAQ v1 and v1.1-v2 packs), `openbeast-models/glm53-flash-ablit-exl3`, `.cache/huggingface` (GLM layout plus the DFlash2 drafter blobs) | **722.6 GB** | **back up** | Days to re-download, and impossible offline. Hard-linked across the three trees: one rsync `-H` pass |
| The DeepSeek kernel kit `tensorfold-dsv41/kit-abe07c7d/`, **with its `.git`** | 0.66 GB | **back up** | The offline rebuild refuses a kit without `.git`, because the recipe's `prepare.sh` would `git fetch` it (MEASURED, [§3.5](#35-the-offline-rebuild)) |
| **Rank files of every pack**: rank 0 in `tensorfold-dsv41/<REV8>/rank-cache/` and rank 1 in `worker-rank-cache*/`. **Token maps** (`dsv41_token_map.json`) and **served folders** (`<REV8>/model/`: links into the snapshot, plus the recipe's chat template) | 424 GB (MEASURED: 99 GiB per rank file, four of them) | **rebuild** | Recomputed offline from the checkpoint by `spark-restore.sh rebuild`, which runs `spark-deepseek-prepare-local.sh`: about 15 min per rank file ([§3.5](#35-the-offline-rebuild)) |
| GLM's compiled CUDA kernels `.cache/tensorfold-glm53/<image hash>/` | 0.17 GB | **rebuild** | Recompiled offline on GLM's first start, in a few minutes. Its `logs/` are kept |
| Tools and config: `~/openbeast` (with `.git` and the **uncommitted** changes), `~/.local/{bin,opt,lib}` (native TensorFold 1.2.7, `beast-spark`, hf), `~/.opencode` (opencode v2.3.2), `~/.venvs/spark-tools`, `~/openbeast-recipes` (deepseek-prep runtime with `.git`, rollback, conformance records; glm53), units in `~/.config/systemd/user`, `.openbeast` (docs mirror), `backups/`, `Downloads/` | 7.7 GB | **back up** | Small, and what makes the node "ours". `.venvs` would rebuild only with pip online, so it is kept |
| Secrets: `openbeast.conf` (+ `.bak-*`), `~/.config/openbeast/{gate-keys/,tensorfold-api-key,hf-token,webui-max-password}`, `~/.ssh/{authorized_keys,id_ed25519_nvsync_cluster_assistant}`, `~/.gnupg` | 118 KB | **back up, encrypted drive** | Listed by path, never printed. `inventory` flags a secret readable by group or other |
| Per-node files: `spark.env`, `cluster.env`, `~/.ssh/config`, `~/.ssh/known_hosts`, `default.target.wants/` links | 10 KB | **back up for the record, regenerate per node** | see [§3.3](#33-what-is-unique-per-node) |
| Docker images: `tensorfold-dsv41:exl3-tp2-8a98ae503d85` (24.9 GB), `tensorfold-glm53:v0.6.0` (24.6 GB), **`nvcr.io/nvidia/pytorch:26.07-py3` (24.3 GB, now included)**, the compose-pinned `open-webui` (4.1 GB) and `searxng` (0.3 GB), `alpine` | 78.2 GB raw | **back up** (`docker save \| zstd`) | The inference images are built or pulled online. The three big images share base layers (`docker system df`: 34.4 GB unique, MEASURED), but `docker save` writes each one whole, so 78 GB is the upper bound before zstd |
| Docker volume `openbeast_open-webui-data` (users, chats, settings) | about 1 GB | **back up** | Exported with the container **paused** |
| Caches (pip, browser, mesa, `.nv`), snap, desktop folders, `.bash_history`, the prepared marker in `.local/state/` | 4.8 GB | **skip** | Rebuildable or noise. The shell history can hold pasted secrets |
| The OS: DGX OS 7.6.0 (OTA) on Ubuntu 24.04.5, kernel `7.0.0-1025-nvidia`, driver 580.178.04, Docker 29.6.2, libnccl2 held, tailscale 1.104.7, spark-guard, linger | about 21 GB used outside `/home` (MEASURED: `/usr` 13 GB, `/opt` 3.3 GB, readable `/var` 4.2 GB) | **the golden image** ([§3.2](#32-the-golden-base-image)) | Each overlay generation also records it read-only in `system/`: dpkg, dgx-release, lsblk, fstab, efibootmgr, Secure Boot, fwupd firmware versions, fabric, units, `tailscale serve` |

**spark-2 (`spark-9f26`, MEASURED):** 1.1 TB in its home directory. Almost
all of it is the same weights and rank files as spark-1, and the rank files
are now rebuilt. What only spark-2 has is small:

- `opencode-model-link.service`;
- `~/offline-stage/debs` (the pinned libnccl2 and tailscale `.deb`s);
- `openbeast-backups/` (kilobytes);
- the test venv `~/obvenv` (about 150 MB), which is rebuilt;
- scratch test clones, which are re-cloned from the hub.

`--config-only` covers the first three in 1 to 2 GB
([§2.9](#29-spark-2)).

**Size on the 4 TB drive.**

| What | Size |
|---|---|
| Overlay files (weights 722.6 GB, `keep` 8.3 GB with the kit, secrets and per-node files tiny; `rebuild` 424.7 GB left out). VERIFIED by `inventory` on spark-1 | **730.9 GB** |
| Docker images, before zstd (an upper bound) | 78 GB |
| WebUI volume | 1 GB |
| Golden image: `root.tar` (estimate: 21 GB readable without root, plus root-only `/var` and `/root`), `esp.img` (0.3 GB) | about 25 GB |
| NVIDIA recovery `tar.gz` (CLAIMED: 5.9 GB for 1.145.33) | about 6 GB |
| spark-2 `--config-only`, identity bundles | 2 GB |
| **First full set** | **about 0.85 TB** (was 1.0 TB with the rank files) |

Formatted with `-m 0`, the drive gives about 3.9 TB (3.6 TiB), which leaves
about **3 TB** for later generations. Each new generation costs only its
changes; a new pack, for example, is 120 to 200 GB of shards and no rank
files. Generations on one drive share their unchanged blocks, so one drive
is one copy ([§2.8](#28-schedule-and-retention)).

---

## 2. Backup

### 2.1 Which method, and why

| | Disk image of the whole disk (dd, partclone, Clonezilla) | Backup tool (restic, borg) | **Golden OS image (tar, no /home) + rsync overlay (chosen)** |
|---|---|---|---|
| Offline OS reinstall | yes | no: the OS comes from NVIDIA's first boot | **yes**: the image is past the first boot |
| Size | 1.2 TB used: `/home` is on the root filesystem (MEASURED), and block-level tools cannot leave it out | small, dedup | about 25 GB for the OS plus 0.8 TB of overlay |
| Identity | cloned: machine-id, host keys, Tailscale key, netplan port names | n/a | **left out of the image**, written per node by `identity` |
| Different disk size | partclone needs at least the used size; dd the same size | n/a | any: p2 is re-made to fill the disk |
| Tools | Clonezilla arm64 is experimental (CLAIMED), partclone and fsarchiver are on neither DGX OS nor the Ubuntu live USB | **not installed** on DGX OS | GNU `tar`, `sfdisk`, `dd`, `mkfs.ext4`, `cryptsetup`: on the live USB (VERIFY for cryptsetup) |
| Read it without the tool | no | no | **yes**: a tar and a plain tree |

The overlay holds the **account**, and the golden image holds the **OS**.
They never overlap: `/home/*/*` is the one line of
`data/spark-golden.exclude` that the overlay fills.

### 2.2 What is excluded

From the **overlay**:

- **The OS**, which is the golden image's job ([§3.2](#32-the-golden-base-image)).
  Nothing outside `/home/max` is copied.
- **Everything recomputable offline** (class `rebuild`):
  - DeepSeek's rank files, token maps and served folders for every pack;
  - GLM's compiled kernels;
  - `~/obvenv`.
- **Caches and noise** (class `skip`): `~/.cache` apart from the HF cache
  and the logs, `.nv`, snap, browser profiles, desktop folders, sockets,
  `.bash_history`.

From the **golden image** (`data/spark-golden.exclude`, VERIFIED with GNU
tar 1.35 on a scratch tree in `tests/test_spark_backup.sh`):

- `/home/*/*` (the overlay);
- `/var/lib/docker` and `/var/lib/containerd` (the images come back with
  `docker load`);
- `/swap.img` (16 GiB, re-made at restore);
- journals, crash dumps, apt archives, `/tmp`;
- every identity file ([§3.3](#33-what-is-unique-per-node)).

The `rankcache` class still exists in the engine. An items line of that class
would copy a pack's rank files: a faster restore, 212 GB more drive. No line
uses it.

### 2.3 Hard links, symlinks and size

The weights exist once on disk and are hard-linked into three trees:

- `Documents/<pack>/`
- `openbeast-models/deepseek-hf/hub/models--*/snapshots/<rev>/`, through
  `spark-deepseek-pack.sh` staging
- `~/.cache/huggingface/hub/` (GLM, through [SPARK_HYDRA.md C2](SPARK_HYDRA.md#c2-hard-link-into-the-hf-cache-layout-the-recipe-reads))

On spark-1, of the files over 1 GB, 96 have 2 links, 164 have 3 and 60 have
4 (MEASURED). The HF cache layout also uses relative symlinks
(`snapshots/r/file -> ../../blobs/<sha>`).

So the backup is **one** `rsync -aHAX` over all three trees:

- `-H` sees the links only within one run.
- `-a` keeps the symlinks.
- `--numeric-ids` keeps uid 1000, which is `max` on both Sparks (MEASURED).

The inventory's NEW column counts each inode once. The test suite checks
that a shard linked into three trees arrives as one inode, in the backup and
again after a restore.

### 2.4 The drive's filesystem: ext4

| Filesystem | Verdict |
|---|---|
| **ext4** | **use it**: owners, modes, hard links, symlinks, ACLs and xattrs. DGX OS and the Ubuntu live USB read it with no extra package |
| xfs, btrfs | also accepted by the tool |
| exFAT, vfat, NTFS | **refused**: no POSIX owners or modes, no hard links (the 723 GB of weights would become about 1.4 TB), no symlinks, and vfat has a 4 GB file limit |
| APFS, HFS+ | refused: not writable from Linux |

The Mac cannot read an ext4 or LUKS drive natively. That is acceptable,
because the drive is used only on a Spark or from the live USB.

### 2.5 Secrets: LUKS2, and where the passphrase and header live

The backup holds every credential of the pair: `openbeast.conf`, the gate
device keys, the TensorFold API key, the HF token, the WebUI password, the
cluster ssh key, and in the identity bundles the ssh host keys and Tailscale
node keys. `spark-backup.sh run` **refuses** a destination that is not on a
dm-crypt device unless `--allow-unencrypted` is passed. Whole-drive LUKS2
covers everything with one mechanism. The GB10's Arm cores have AES
instructions, and the USB link is the bottleneck.

**The policy (Max, 2026-11-18):**

| What | Lives in | Never |
|---|---|---|
| **The passphrase** | Max's **password manager**, and a **printed copy** kept physically apart from the drive (not in the same bag or drawer) | on the SSD, on either Spark, in the repo, in a shell command line or history |
| **The LUKS header backup** (`obbackup-luks-header-<UUID>.img`, 16 MB) | the **key stick** (stick 2) **and the Mac** (for example `~/Documents/openbeast-keys/`, FileVault on). It is useless without the passphrase | on the SSD itself (a damaged header would take its backup with it), on either Spark (not even `/tmp`) |

**One-time preparation of the drive, on spark-1, as Max.** These are the
only root steps of a backup. The key stick is formatted exFAT so that both
the Mac and the Spark read it. GNOME mounts it at `/media/max/OBKEY` when
its label is `OBKEY`.

```bash
lsblk -o NAME,SIZE,MODEL,TRAN,SERIAL             # /dev/sdX = the 4 TB SSD, /dev/sdY = the key stick: triple-check
sudo cryptsetup luksFormat --type luks2 /dev/sdX # ERASES it. The passphrase is typed at the prompt, never on a command line
U=$(sudo cryptsetup luksUUID /dev/sdX)
K=/media/max/OBKEY/obbackup-luks-header-$U.img   # the key stick, mounted by the desktop
sudo cryptsetup luksHeaderBackup /dev/sdX --header-backup-file "$K"
sudo chown max: "$K" && sha256sum "$K" > "$K.sha256"
sudo cryptsetup luksDump "$K" | head -5          # a LUKS2 header, the drive's UUID
sudo cryptsetup open --test-passphrase --header "$K" /dev/sdX && echo "header + passphrase: OK"
sync && umount /media/max/OBKEY                  # then copy both files to the Mac, and put the stick away
sudo cryptsetup open /dev/sdX obbackup
sudo mkfs.ext4 -m 0 -L obbackup /dev/mapper/obbackup      # -m 0: no root reserve on a data drive
sudo mkdir -p /media/max/obbackup && sudo mount /dev/mapper/obbackup /media/max/obbackup
sudo chown max:max /media/max/obbackup
```

On the Mac: `shasum -a 256 -c obbackup-luks-header-*.img.sha256` after the
copy.

**Every later use.** The `mkdir` matters on a freshly restored Spark. The
GNOME desktop also offers to unlock a LUKS drive when it is plugged in.

```bash
sudo mkdir -p /media/max/obbackup
sudo cryptsetup open /dev/sdX obbackup && sudo mount /dev/mapper/obbackup /media/max/obbackup
# ... back up / restore ...
sudo umount /media/max/obbackup && sudo cryptsetup close obbackup
```

**When the header is damaged** (`cryptsetup open` says "not a valid LUKS
device"), first open the drive with the backup header and leave the drive
untouched:

```bash
sudo cryptsetup open --header /media/max/OBKEY/obbackup-luks-header-<UUID>.img /dev/sdX obbackup
```

When that works, write the header back. `luksHeaderRestore` asks for
confirmation:

```bash
sudo cryptsetup luksHeaderRestore /dev/sdX --header-backup-file /media/max/OBKEY/obbackup-luks-header-<UUID>.img
```

**After any key change** (`luksAddKey`, `luksChangeKey`, `luksKillSlot`),
take a new header backup on the stick and the Mac. Then **destroy the old
copies**: an old header still opens the drive with the OLD passphrase. That
is exactly what must stop working when a passphrase is retired.

### 2.6 Consistency without stopping the model

spark-1's root is plain ext4 on `nvme0n1p2`, with no LVM and no btrfs
(MEASURED). So there is no snapshot to take. Instead:

- **Weights** are written only by a fetch or a pack swap and are otherwise
  immutable. **Do not run a backup during `spark-deepseek-pack.sh use` or a
  model fetch.**
- **The Open WebUI database** (SQLite) is the one live writer that matters.
  The backup **pauses** every container that uses the volume (`docker
  pause`), tars the volume through a throwaway `alpine` container, then
  unpauses. The pause lasts for the copy of about 1 GB: seconds. `--no-pause`
  copies live.
- **The checkout, logs and recipe records** change rarely. A log rotated
  mid-copy makes rsync exit 24, which the tool accepts.
- **For a perfectly quiet backup**, for example before an OS change, stop
  the command center and inference first with `beastdown`, back up, and
  start them again with `beastup`.
- **The golden image** is captured with the node booted from the live USB
  and its root mounted read-only ([§3.2](#32-the-golden-base-image)):
  consistent by construction.

### 2.7 Running it

```bash
cd ~/openbeast
scripts/backends/spark-backup.sh inventory                       # what will be in it; anything UNCOVERED?
scripts/backends/spark-backup.sh run --to /media/max/obbackup    # dry run: drive checks, filter, images, volume
scripts/backends/spark-backup.sh run --to /media/max/obbackup --apply
scripts/backends/spark-backup.sh verify /media/max/obbackup/openbeast-backup/spark-3dc6/latest --deep
```

The pytorch image is now an ordinary `head` image, so `--with-optional-images`
is no longer needed for it.

The drive's layout:

```text
/media/max/obbackup/
  openbeast-backup/
    spark-3dc6/                       one directory per node, by hostname
      20261118T090000Z/               a generation (UTC)
        home/                         the overlay, laid out as /home/max
        docker/                       <image>.tar.zst, volume-<name>.tar.zst, images.json, volumes.json
        system/                       read-only record: os/dgx release, dpkg, lsblk, fstab, efibootmgr, Secure Boot,
                                      fwupd-devices (firmware versions), netplan file names, fabric, units, git status
        ITEMS  FILTER  MANIFEST.jsonl  SHA256SUMS  BACKUP.json
      latest -> 20261118T090000Z      moved only after a generation completes
    spark-9f26/                       spark-2's --config-only generations
  openbeast-golden/
    dgxos-7.6.0-spark-9f26-20261119/  the golden base image (§3.2): root.tar, esp.img, gpt.sfdisk,
                                      esp.blkid, root.blkid, dgx-release, os-release, dpkg-status, SHA256SUMS
    identity/spark-1.tar              each node's identity bundle (root's files, mode 600)
    identity/spark-2.tar
  nvidia-recovery/                    NVIDIA's DGX Spark recovery tar.gz (§3.1), to make stick 3 offline
  live-usb/                           the Ubuntu 24.04.x arm64 desktop ISO, to remake stick 1 offline
```

`run` works under `umask 077`, names every home entry no items line claims as
**NOT backed up**, and repeats the inventory's secret-mode check. A
generation is written as `<stamp>.partial` and renamed only when it is
complete. **Incremental runs:** `--link-dest` turns every file unchanged
since `latest` into a hard link, and an unchanged image (same ID) is linked,
not saved again.

### 2.8 Schedule and retention

| When | What |
|---|---|
| **Before anything risky**: an OTA or OS upgrade, a driver or kernel change, an Open WebUI pin move, a pack swap | `run --apply` (quiet, [§2.6](#26-consistency-without-stopping-the-model)) |
| **After an OTA or any root change** (apt, netplan, spark-root-setup) | a **new golden image** ([§3.2](#32-the-golden-base-image) B), and new identity bundles if netplan or Tailscale changed. Keep the previous image until the new one has restored once |
| **After** a new pack is serving | `run --apply` (its shards land once; its rank files are rebuilt, never copied) |
| **Weekly**, while the drive is attached | `run --apply`, then `verify` |
| **Monthly** | `verify --deep` on `latest` (re-hashes about 0.8 TB: about 15 to 20 min on the SSD), and `sha256sum -c` in the golden image's folder |
| **Quarterly** | a restore drill ([§5](#5-restore-drills-and-recovery-time)) |

Keep the last 3 generations plus the first one of each month, and the last
two golden images. Delete a generation with `rm -rf <stamp>`: a file stays on
the drive while any generation still links it. **One drive is one copy.** A
bad sector in a shard damages that shard in every generation, and `verify
--deep` finds it. A second SSD kept elsewhere protects against it (open
question 2).

### 2.9 spark-2

A worker's weights are byte-identical to the head's, and its rank-1 file is
rebuilt by the head. So the **head's** backup populates spark-2. spark-2's
own unique state is small:

```bash
scripts/backends/spark-backup.sh run --to /media/max/obbackup --config-only --apply   # on spark-2, the drive moved: 1-2 GB
```

Before the first head backup, copy spark-2's `~/offline-stage/` to spark-1,
so the head's backup carries the pinned `.deb`s.

---

## 3. Reinstall the OS, then restore: fully offline

### 3.1 Offline: the answer

**What NVIDIA's own path needs (CLAIMED):**

- **The recovery USB** ([system-recovery](https://docs.nvidia.com/dgx/dgx-spark/system-recovery.html)):
  - NVIDIA publishes it as a `tar.gz`, about 5.9 GB for 1.145.33. You write
    it to a 16 GB or larger stick with `CreateUSBKey.sh`, which erases the
    stick.
  - It boots a small installer, "DGX SPARK FASTOS". It holds the OS image
    (`fastos.partaa`/`.partab`), firmware (`fw/`) and an `sgdisk.txt.example`
    ([forum 350690](https://forums.developer.nvidia.com/t/cannot-restore-from-recover-image/350690)).
  - It **reflashes firmware and erases the internal SSD**. Nothing says it
    needs the network, and the image is on the stick.
- **First boot (OOBE).** The *First Boot* page still says: *"A fast, reliable
  internet connection … to download required updates during the initial
  setup"* and *"the system will automatically download and install the full
  software image"*
  ([first-boot](https://docs.nvidia.com/dgx/dgx-spark/first-boot.html)).
  This is what decision 1 rules out.
- **NVIDIA's own offline route, since 2026:**
  - The April 2026 release notes list *"Air Gapped Deployment and Updates"*
    and *"OOBE Bypass"*
    ([forum 368114](https://forums.developer.nvidia.com/t/dgx-spark-software-updates-04-2026/368114)).
  - An NVIDIA employee wrote on 2026-06-19: *"The latest Spark image no
    longer requires an internet connection to finish setup"*
    ([forum 373452](https://forums.developer.nvidia.com/t/possibility-instructions-for-offline-setup/373452)).
  - The mechanism is an `OEMDATA` FAT32 volume attached before power-on,
    with cloud-init `user-data` and `meta-data`. Optional `debs/` and
    `firmware/` (`.cab`/`.cap`) folders sit beside them. *"If `user-data`
    creates a user, cloud-init completes automated setup and OOBE is
    skipped."* It needs `nvidia-spark-cloud-init`, which ships *"in the
    October 2026 Release and later"*
    ([enterprise-custom-install](https://docs.nvidia.com/dgx/dgx-spark/enterprise-custom-install.html)).
  - Nobody on the forum has confirmed a fully offline run on hardware.

**What our Sparks show (MEASURED):**

- Both have `DGX_SWBUILD_VERSION="7.2.3"` (built 2025-09-10). That is the
  base the recovery image installed.
- `DGX_OTA_VERSION="7.6.0"` was dated 2026-11-12. The online updates after
  first boot brought the Sparks to what we run.
- `/opt/nvidia/dgx-oobe/oobe-complete-flag` is present.
- `nvidia-spark-cloud-init 1.0.2-1` is installed.

A recovery-only reinstall from an old stick would land on 7.2.3, kernel
6.11, not the 7.0 kernel and 580.178 driver the recipes were qualified on.

**Our answer: the golden base image (primary) and NVIDIA's OEMDATA route
(fallback).**

| | **Golden image (ours, primary)** | **Recovery USB + OEMDATA (NVIDIA's, fallback)** |
|---|---|---|
| Network | **none**: the image is past the OOBE (its `oobe-complete-flag` comes with it) | none, if the recovery image is the October 2026 release or later (CLAIMED) |
| OS you get | **exactly** today's 7.6.0, with libnccl2 held, Tailscale, the guard and linger already in | whatever version that recovery image carries, then `spark-root-setup.sh --debs` |
| Firmware | **not touched** (see below) | reflashed from the stick (CLAIMED) |
| Identity | the node's own, from its bundle: same host keys, Tailscale key and fabric netplan | all new: re-pair NVIDIA Sync, Tailscale re-auth (online) |
| Time per Spark | about 15 to 20 min | 20 to 40 min recovery, plus cloud-init |
| Status | VERIFY (drill G) | CLAIMED |

**Caveats of the golden route, all addressed in [§3.2](#32-the-golden-base-image):**

- **Firmware is not in a disk image.** UEFI, EC and SSD firmware are flashed
  by NVIDIA's recovery USB or by `fwupd` from LVFS, which is online (MEASURED:
  `lvfs.conf` enabled).
  - The ConnectX-7 firmware *is* in the OS: `spark-mlnx-firmware-manager`
    flashes `fw-ConnectX7.bin` from `/opt` (MEASURED).
  - On a unit we already own, the firmware simply stays.
  - On a replacement unit, run NVIDIA's recovery stick first (offline), or
    install LVFS `.cab` files kept on the SSD with `fwupdmgr install`.
- **Secure Boot.** It is enabled on both Sparks (MEASURED). The chain is
  `shimaa64.efi`, then `grubaa64.efi`, then the signed `7.0.0-1025-nvidia`
  kernel and the prebuilt signed `linux-modules-nvidia-580-open`. No DKMS and
  no MOK keys are involved (MEASURED).
  - The image restores those files byte for byte, so the signatures hold.
  - The one trap is **SBAT**: a unit whose firmware has seen a newer shim
    revokes older ones (MEASURED SBAT level 1.5.4 on spark-1).
  - So refresh the golden image after every OTA. If a replacement unit
    refuses the image's shim, disable Secure Boot once in UEFI, boot, and
    let the next OTA bring a newer shim. NVIDIA documents the same Secure
    Boot toggle for its own recovery stick.
- **A replacement unit** has no identity bundle. Without the network it gets:
  - a fresh identity, from `identity --fresh`;
  - its fabric addresses from NVIDIA Sync pairing. That runs from the Mac
    over the LAN; whether Sync needs the internet is VERIFY.
  - its Tailscale join, which waits for the internet.

  Serving on the pair needs none of these online: the recipes talk over the
  fabric, and the gate listens on the LAN.

### 3.2 The golden base image

**Which way to make it, and why (recommendation).**

- **(a) Image the running OS from a live USB, file level, without `/home`,
  and write the identity per node on restore. CHOSEN.**
  - `/home` (1.1 TB) is on the same ext4 filesystem as `/` (MEASURED:
    `nvme0n1p1` 298 MB vfat ESP, `nvme0n1p2` 3.7 TB ext4 root, no `/home`
    partition). So a partition image (partclone, Clonezilla, dd) cannot leave
    the models out.
  - A file-level `tar` of the read-only root with `data/spark-golden.exclude`
    can. It gives about 25 GB that restores onto any disk size.
- **(b) A fresh recovery install, completed online once, imaged
  immediately.** Rejected:
  - It needs one Spark wiped, a full online first boot and a full restore.
  - It captures *stock* DGX OS, not the qualified 7.6.0 with our root setup.
  - NVIDIA's OEMDATA route now covers "stock, offline" anyway.
- **Live-system copy (`sudo tar` of `/` while serving).** Kept only as an
  emergency fallback. `/var` keeps changing under it.

**The source node: spark-2.** The two Sparks are the same system (MEASURED,
`dpkg-query` on both):

- 2,344 packages each;
- the same 7.6.0, kernel 7.0.0-1025 and driver 580.178.04;
- the same `/etc/openbeast/spark-guard.nft`.

The only differences are the older fallback kernel (6.11.0-1014 on spark-1,
-1016 on spark-2), `nvidia-disable-bt-profiles` (25.07 vs 25.09) and a
leftover `nvidia-firmware-580-580.95.05` on spark-2. spark-2 has the newer of
each, and drill G restores onto it first. Either node works.

**The procedure.** `scripts/backends/spark-backup.sh golden-image --plan`
prints it with this node's measured values: disk, UUIDs, DGX OS version,
Secure Boot state, paths on the drive. The plan only prints; it runs nothing.
Its four parts:

**A. The identity bundles, live, on each node** (seconds, no outage; root,
read-only on the node). Each bundle holds:

- `/etc/machine-id`, `/etc/hostname`, `/etc/dgx-release` (for the serial);
- `/etc/ssh/ssh_host_*_key{,.pub}`;
- `/var/lib/tailscale`;
- NVIDIA Sync's `99-nvidia-sync-cluster.yaml` and our
  `99-zz-openbeast-fabric-mtu.yaml`.

The bundle is a mode-600 tar on the **LUKS drive only**: it holds private
keys.

```bash
sudo sh -c 'umask 077; cd / && tar --numeric-owner -cf /media/max/obbackup/openbeast-golden/identity/spark-1.tar \
  etc/machine-id etc/hostname etc/dgx-release etc/ssh/ssh_host_*_key etc/ssh/ssh_host_*_key.pub var/lib/tailscale \
  etc/netplan/99-nvidia-sync-cluster.yaml etc/netplan/99-zz-openbeast-fabric-mtu.yaml'
```

spark-2's bundle is written to its own home (mode 600), copied to the drive
on spark-1 with `scp`, and deleted. The plan prints both lines.

**B. Capture** (planned outage of about 20 min; the pair stops serving).

1. On spark-2, shut down, insert **stick 1** and the SSD, and power on.
   Press Esc or Del, choose *Boot Override*, pick the stick, then *Try
   Ubuntu*. NVIDIA documents this exact live USB for its System Repair
   ([boot-repair](https://docs.nvidia.com/dgx/dgx-spark/boot-repair.html),
   CLAIMED).
2. In a terminal, as root, run the plan's commands, which do the following:
   - unlock and mount the SSD at `/mnt/live-ob`;
   - mount `/dev/nvme0n1p2` **read-only** at `/mnt/src`;
   - `sfdisk --dump` (partition GUIDs) and `blkid` of both partitions (the
     UUIDs);
   - `dd` the 298 MB ESP;
   - run the one `tar`:

     ```bash
     tar --create --file=$G/root.tar --directory=/mnt/src --one-file-system --numeric-owner --xattrs \
         --xattrs-include='*' --acls --sparse --anchored --exclude-from=$EXCL .
     ```

   - copy `dgx-release`, `os-release` and the dpkg status;
   - write `SHA256SUMS`;
   - rename `.partial` to its final name.
3. **Checks:**
   - `tar --list` shows hundreds of thousands of entries;
   - it shows no `./home/max/<anything>`, `./var/lib/docker`,
     `./etc/ssh/ssh_host_*` or `./var/lib/tailscale/<anything>`.
4. Power off, pull the stick, and boot normally: nothing on spark-2 changed.

`--xattrs` matters: file capabilities (`security.capability`, ping and the
like) live there. `--numeric-owner` matters too: the live system's
`/etc/passwd` is not DGX OS's.

**C. Restore onto a Spark: this one, the other, or a replacement.** From
stick 1, offline:

1. Unlock the SSD, then `sha256sum -c SHA256SUMS`.
2. Replay `gpt.sfdisk` with `last-lba` and p2's size dropped, so p2 fills
   whatever disk this is. This **erases the internal SSD**. The partition
   GUIDs come back.
3. `dd` the ESP back. Its FAT volume ID `9DA2-3597` is what `/etc/fstab`
   mounts.
4. `mkfs.ext4 -U <the recorded root UUID> -L root`. The ESP's `grub.cfg`
   finds root by that UUID (`search.fs_uuid d27bfd26-…`, MEASURED), and so
   does `/etc/fstab`. **Nothing inside the image is edited.**
5. Mount at `/mnt/target`, then `tar --extract … --numeric-owner --xattrs
   --acls --same-permissions`.
6. Re-make `/swap.img` (16 GiB: `fallocate`, `chmod 600`, `mkswap`).
7. **The identity step**, from the SSD's copy of the checkout:

   ```bash
   python3 /mnt/live-ob/openbeast-backup/<host>/latest/home/openbeast/scripts/backends/pylib/spark_backup.py identity \
       --target /mnt/target --bundle /mnt/live-ob/openbeast-golden/identity/spark-2.tar          # dry run; then --apply
   ```

   It refuses `--target /`, a target that is not a DGX OS root, and a bundle
   member that is not one of the identity files. It then:
   - writes the node's own machine-id (or a new random one with `--fresh`);
   - writes its ssh host keys (or `ssh-keygen -A -f /mnt/target`);
   - writes its Tailscale state (or an empty `/var/lib/tailscale`);
   - writes its fabric netplan (or none);
   - writes the host name and the `127.0.1.1` line;
   - writes `DGX_SERIAL_NUMBER` (from the bundle, or `--serial`);
   - removes derived state: systemd's random seed, NetworkManager's
     `secret_key`, Docker's engine ID.
8. Unmount, pull the stick, and reboot.

**First boot:** no OOBE, the old login, the old host keys (so the Mac's
`known_hosts` stays valid), the fabric up with MTU 9000, and Tailscale back
with its node key when the network returns. The UEFI boot entry:

- on the **same** unit, it stays valid: the GPT's GUIDs are restored;
- on the **other** unit or a **replacement**, the NVRAM entry points at a
  partition that is not there. Firmware then falls back to the removable path
  `EFI/BOOT/BOOTAA64.EFI`, which is shim. Shim's `fbaa64.efi` recreates the
  entry from `EFI/ubuntu/BOOTAA64.CSV`. Both files are on the ESP (MEASURED);
  the behaviour is VERIFY.
- If it does not boot, use *Boot Override* once and pick the internal disk.

Checks: `mokutil --sb-state` says enabled; `/etc/machine-id` differs between
the two Sparks; `nvidia-smi`; `ibdev2netdev`; `docker info`.

**D. Firmware.** Each generation's `system/fwupd-devices.txt` records the
versions. spark-1 today (MEASURED):

| Component | Version |
|---|---|
| UEFI | 0x02009e0f |
| EC | 0x03000d07 |
| ConnectX-7 | 28.50.1002 |
| NVMe | NXHB202Q |
| SBAT | 1.5.4 |

On a replacement unit, compare with `fwupdmgr get-devices`. If it is older,
run NVIDIA's recovery stick **before** step C, or install `.cab` files.
NVIDIA's partial LVFS mirror for one device's GUIDs is about 50 MB (CLAIMED,
enterprise-custom-install); download it once into `nvidia-recovery/` on the
SSD.

**Restoring onto spark-1 from spark-2's image** is the same procedure with
`--bundle …/spark-1.tar`. The two systems differ only in the fallback-kernel
details above. After an OTA on one node only, take a new image from that node.

### 3.3 What is unique per node

| Identity | Where | On a golden restore (`identity`) | In the overlay restore |
|---|---|---|---|
| Hostname | `/etc/hostname`, `/etc/hosts` (`spark-3dc6`, `spark-9f26`: `spark-` + the last 4 hex of the 10GbE MAC, MEASURED for spark-1) | from the bundle, or `--hostname` | left alone. OpenBeast's names are the tailnet names |
| machine-id | `/etc/machine-id` (`/var/lib/dbus/machine-id` links to it) | the node's own, or new random. Two units with one ID confuse DHCP, journald and fwupd (CLAIMED: [forum 377208](https://forums.developer.nvidia.com/t/msi-edgexpert-dgx-spark-having-identical-machine-ids/377208)) | never |
| ssh host keys | `/etc/ssh/ssh_host_*` | the node's own, or `ssh-keygen -A`. cloud-init will not make them: its instance ID is the constant `iid-datasource-none` (MEASURED on spark-1), so it never re-runs | `known_hosts` is never copied |
| Tailscale node | `/var/lib/tailscale` (root) | the node's own (no re-auth), or empty (re-auth online with `spark-root-setup.sh`; remove the old machine in the admin console first) | never |
| Fabric netplan | NVIDIA Sync's `/etc/netplan/99-nvidia-sync-cluster.yaml`, our `99-zz-openbeast-fabric-mtu.yaml`. spark-1 cables port 0, spark-2 port 1 (MEASURED) | the node's own, or none: pair with NVIDIA Sync from the Mac, then `spark-fabric-mtu.sh` | `SPARK_IFACE` / `SPARK_IB_HCA` read from the ports that are Up |
| NVIDIA Sync's other state | Spark: `~/.config/NVIDIA/Sync/cache/` (a probe script), `~/.ssh/authorized_keys` and the cluster key (MEASURED). Mac: `~/Library/Application Support/NVIDIA/Sync/` (CLAIMED) | none (user files) | restored with the overlay |
| Serial | `/etc/dgx-release` `DGX_SERIAL_NUMBER` | from the bundle, or `--serial` | recorded only |
| Docker engine ID, image store | `/var/lib/docker` | not in the image; Docker makes a new one | images re-loaded, compose pins rewritten to the loaded IDs |
| Rank and role | `spark.env`, `cluster.env`, the DeepSeek recipe's `WORKER` / `FABRIC_PEER`, GLM's `WORKER=max@10.100.216.2` | n/a | **written by the restore** from the fleet file |
| Boot units | `default.target.wants/` links | n/a | the role's units enabled, and the inference unit the backup had enabled (GLM by default) |
| Shared on purpose | `tensorfold-api-key`, the cluster ssh key `id_ed25519_nvsync_cluster_assistant` (the same on both Sparks, MEASURED) | n/a | restored on both |

### 3.4 Bare metal to serving: the order

For the pair, with the SSD, stick 1, and the passphrase. Times are estimates;
[§5.2](#52-recovery-time) adds them up.

| # | Step | Where | Network |
|---|---|---|---|
| 1 | Golden restore of **spark-2** with `identity --bundle spark-2.tar` ([§3.2](#32-the-golden-base-image) C) | live USB | none |
| 2 | Move the SSD and stick 1; golden restore of **spark-1** with `spark-1.tar` | live USB | none |
| 3 | Boot both. Log in as `max` (the image's password). Mount the SSD on spark-1 ([§2.5](#25-secrets-luks2-and-where-the-passphrase-and-header-live)) | spark-1 | none |
| 4 | Head overlay, run **from the drive's copy of the checkout**: `G=/media/max/obbackup/openbeast-backup/spark-3dc6/latest; $G/home/openbeast/scripts/backends/spark-restore.sh --from $G --index 0` (dry run), then add `--apply`. The fleet file defaults to the pair | spark-1 | none |
| 5 | Seed spark-2 over the fabric (the rails are up: the identity bundle brought the netplan). On spark-1: `rsync -a ~/.ssh/authorized_keys ~/.ssh/id_ed25519_nvsync_cluster_assistant* max@10.100.216.2:.ssh/` and `rsync -a ~/openbeast/ max@10.100.216.2:openbeast/` (spark-2's login password, once; VERIFY that DGX OS's sshd takes passwords, or else move the SSD). On spark-2: `~/openbeast/scripts/backends/spark-restore.sh --from max@10.100.216.1:/media/max/obbackup/openbeast-backup/spark-3dc6/latest --index 1 --apply`. The tool pulls with the cluster key. Run it while step 4's `docker load` runs | both | fabric only |
| 6 | Checks only (the golden image already holds the root setup; idempotent): `sudo scripts/backends/spark-root-setup.sh spark-N --guard --debs ~/offline-stage/debs --no-tailscale`, `sudo scripts/backends/spark-fabric-mtu.sh`; reboot both if either changed anything | both | none |
| 7 | Head: `scripts/backends/spark-restore.sh rebuild --apply`: DeepSeek's rank files, offline, about 35 min, with no model running ([§3.5](#35-the-offline-rebuild)). Skip it to serve GLM sooner, and run it before the first `beastds` | spark-1 | fabric only |
| 8 | Head: `systemctl --user start spark-hydra-inference openbeast` (GLM, the default; its kernels compile on this first start), or `beastup` | spark-1 | none |
| 9 | Checks: `scripts/backends/spark-cluster.sh check`, `scripts/doctor.sh`, `scripts/backends/conformance.sh`, a chat from the Mac over the LAN | spark-1 | none |
| 10 | When the internet is back, optionally: `scripts/setup-tailscale.sh` (serve config), Tailscale re-auth only for a fresh identity | spark-1 | online |

A **replacement unit** adds two steps:

- NVIDIA's recovery stick first, if its firmware is older ([§3.2](#32-the-golden-base-image) D);
- `identity --fresh --hostname spark-XXXX --serial <its serial>`, then
  NVIDIA Sync pairing for its fabric addresses.

A **lost SSD in one Spark** (the other fine) is steps 1, 3, 4 or 5, 6 and 8
for that node alone.

Steps 4 and 5 are idempotent on an already-restored node: present images are
skipped, rsync copies only differences, and a non-empty WebUI volume is never
overwritten without `--force-volumes`.

### 3.5 The offline rebuild

`spark-restore.sh rebuild` (on the head, after both overlays are restored)
runs `scripts/backends/spark-deepseek-prepare-local.sh` for the pack
`local.sh` selects. Read-only review on spark-1 (MEASURED, 2026-11-18):

- **Pinned and present.** The runtime `~/openbeast-recipes/deepseek-prep/runtime`
  is a git checkout at `c7f2b851`, which the script checks. The kernel kit
  `tensorfold-dsv41/kit-abe07c7d` has its `.git` at `abe07c7d`, the
  `KIT_REF` that `config.sh` pins. Both are in the overlay.
- **No fetch.** The only network step in the sourced part of `prepare.sh` is
  the kit's `git fetch`. It runs only when `$KIT_DIR/.git` is missing, and
  the script refuses before that point when it is. `config.sh`, `nodes.sh`
  and `start.sh` contain no `git fetch/clone/pull`, `hf download`, `docker
  pull` or `pip install`; their `curl` calls are to the local engine only.
- **What it builds:**
  - inside `tensorfold-dsv41:exl3-tp2-…` with `--network none`: the token
    map (`make_token_map.py`), the rank 0 file (`tf-dsv41-rank-cache … 0 2`)
    and the rank 1 file (`… 1 2`);
  - outside the container: the served folder (links plus the recipe's chat
    template).
- **The worker's share.** It rsyncs the rank 1 file, the data dir and the kit
  (without `.git`) to spark-2 over the fabric. It then waits for spark-2's
  image and Engram files to check out, and writes the prepared marker.
- **What `rebuild` checks before it runs** (`tests/test_spark_backup.sh`):
  - the kit and its `.git`;
  - the DeepSeek image;
  - that **no inference unit is running** (the builders take about 48 GB of
    the GB10's memory).
- **Time:** about **15 min per rank file**, about 4 min to copy rank 1 over
  the fabric, so about **35 min**. The pack script's own estimate is about
  15 min for both, the first time; drill R will settle it.
- **GLM:** nothing to rebuild. It loads its checkpoint directly. Its kernels
  (`~/.cache/tensorfold-glm53/<image hash>/`, 168 MB, MEASURED) recompile on
  its first start, in a few minutes, offline.
- **Other packs** (`45ee5d0f`, the stock pack): `spark-deepseek-pack.sh use
  <pack>` rebuilds their rank files when one is selected, from the same kept
  checkpoints.

---

## 4. The pair

### 4.1 The fleet file

`scripts/backends/fleet.env.example` **is** the pair, and `spark-restore.sh`
uses it when `--fleet` is left out. Parsed, never sourced:

```bash
FLEET_NAME=spark-hydra
FLEET_USER=max
NODE_0=spark-1 head 10.100.216.1,10.100.217.1
NODE_1=spark-2 worker 10.100.216.2,10.100.217.2
```

For node N, the restore writes:

- **both**: `spark.env` (`SPARK_NODE_IP`, `SPARK_HEAD_IP`, `SPARK_IFACE` /
  `SPARK_IB_HCA` from the ports Up on that node) and a managed ssh block for
  the peer. NVIDIA Sync's own `Host` entries win.
- **head**: `cluster.env`, the DeepSeek recipe's `WORKER` / `FABRIC_PEER`,
  GLM's `WORKER=max@10.100.216.2` in `glm53/scripts/local.sh`, and
  `TS_HOSTNAME`.
- **worker**: the `opencode-model-link` unit, pointed at the head.

| | head (spark-1) | worker (spark-2) |
|---|---|---|
| checkout, tools, docs mirror, shared keys | yes | yes |
| weights and the kit | yes | yes |
| recipes, `openbeast.conf`, gate keys, WebUI password, venvs, WebUI volume, compose images, pytorch image | yes | **no** |
| inference images (`tensorfold-dsv41`, `tensorfold-glm53`) | yes | yes |
| rank files, token maps, served folders | rebuilt on the head ([§3.5](#35-the-offline-rebuild)) | copied there by the head's rebuild |

### 4.2 Seeding spark-2 fast

- **Over the fabric** (step 5 of [§3.4](#34-bare-metal-to-serving-the-order)):
  one ssh stream at about 0.46 GB/s on a GB10 (MEASURED for `sync-weights`),
  so about 26 min for spark-2's 724 GB. It runs alongside the head's
  `docker load`.
- **Faster for the weights:** restore spark-2 with `--skip-weights`, then on
  the head run `scripts/backends/spark-cluster.sh sync-weights` (4 streams,
  1.73 GB/s MEASURED) for locked profiles. VERIFY for the DeepSeek trees.
- **Or move the SSD** to spark-2 (about 15 min at SSD speed), at the cost of
  doing the two nodes one after the other.

---

## 5. Restore drills and recovery time

### 5.1 The drills

| Drill | How | How often |
|---|---|---|
| **0. First real backup** (not done yet) | the drive and key-stick prep in [§2.5](#25-secrets-luks2-and-where-the-passphrase-and-header-live), `run` (dry), `run --apply`, then `verify --deep`. Check `system/` and `docker/images.json` | once, at a quiet moment |
| **G. Golden image round trip** (not done yet) | identity bundles (A) and the capture on spark-2 (B): no change to spark-2. The restore half (C) **erases spark-2's disk**, so it runs as the first part of drill 5 | the capture after each OTA; the restore with drill 5 |
| **R. Offline rebuild** | on the head with DeepSeek stopped: move the serving pack's rank files aside (`mv` within the disk, seconds), `spark-restore.sh rebuild --apply` **with the Spark's internet unplugged**, time it, `beastds`, conformance, then delete the moved copies | once |
| **1. Manifest check** | `verify GEN --deep`, and `sha256sum -c` in the golden image | monthly |
| **2. Drift check** | `verify GEN --against-live` (read-only) | before relying on an old generation |
| **3. Scratch restore** | into a scratch home on the head, with `--skip-images --no-units` (and `--skip-weights` to drill the config path alone); `diff -r` against the live trees | quarterly |
| **5. Full restore** | on spark-2 in a planned outage, together with drill G: golden restore, identity, overlay over the fabric, rebuild, serve, with the uplink **unplugged** the whole time. Fill in [§5.2](#52-recovery-time) | once, then after big changes |

### 5.2 Recovery time

Estimates for the SSD in a USB 3.2 Gen 2 enclosure (about 0.9 GB/s, VERIFY
in drill 0). The fabric rate is MEASURED.

| Step | Time |
|---|---|
| Golden restore, per Spark: boot stick 1, `sha256sum -c`, partition, ESP, `mkfs`, extract about 25 GB, identity, reboot | 15 to 20 min |
| ... the second Spark (one stick: in sequence; a second live stick lets both run at once) | 15 to 20 min |
| Boot, log in, mount the SSD | 5 min |
| Overlay: the head's 731 GB from the SSD (14 to 20 min); spark-2's 724 GB over the fabric at the same time (about 26 min) | 25 to 35 min |
| `docker load`: 78 GB on the head and 50 GB on spark-2, overlapping the copy above | 10 to 15 min |
| Root-setup checks, MTU check, reboot both | 10 min |
| GLM's first start (kernel compile plus load), conformance | 15 to 20 min |
| **The pair, bare metal to GLM serving** | **about 2 to 2.5 h** |
| DeepSeek's rank files, rebuilt offline (before GLM starts, or later with GLM stopped) | add about 35 min |
| A replacement unit's firmware via NVIDIA's recovery stick | add 20 to 40 min |

Before Max's decisions, the estimate was 2.5 to 3.5 h, with an online first
boot and the rank files copied. The rank files now cost a rebuild instead of
a copy, but 212 GB less to copy and an OS that restores in minutes come out
ahead.

---

## 6. All of OpenBeast's compute

| Machine | What exists today | Gap |
|---|---|---|
| **The Spark pair** | this page; the offline pieces in [SPARK_HYDRA.md H](SPARK_HYDRA.md#h-fully-offline-operation); `bundle.sh`; beast-ark ([BEAST_ARK.md](BEAST_ARK.md)) | drills 0, G, R and 5 |
| **The 5090 rig `beast`** (x86_64, Arch, llama.cpp) | weights pinned and re-fetchable (`weights.registry`); the WebUI volume backup is manual ([UPDATING.md](UPDATING.md)); a [beast-island](BEAST_ISLAND.md) node | **no backup of its own state.** `docs/TODO.md` lists a "Backup/restore CLI (M)". The items-file design carries over. Not designed here |
| **The Mac client** | `setup-client.sh` rebuilds it; gate keys can be re-enrolled from the head | **no backup.** Time Machine on, plus re-enrolling. It also holds the second LUKS header copy and NVIDIA Sync's state |
| **Software, offline** | beast-ark drive, `bundle.sh`, `PYTHON_OFFLINE.md`, `TAILSCALE_OFFLINE.md` | the backup SSD and the ark drive stay separate (secrets versus public software). The NVIDIA recovery `tar.gz` and the Ubuntu live ISO now live on the backup SSD too ([§2.7](#27-running-it)) |

---

## 7. The tools

| File | What |
|---|---|
| `scripts/backends/spark-backup.sh` | `inventory` (read-only), `run --to DIR [--apply]`, `verify GEN [--deep] [--against-live]`, `list DIR`, **`golden-image --plan [--to DRIVE]`** (read-only: prints the image procedure with this node's disk, UUIDs, DGX OS version and Secure Boot state; there is no apply). Never root, never writes outside `--to`, refuses non-POSIX filesystems and non-LUKS drives |
| `scripts/backends/spark-restore.sh` | `--from GEN --index N [--fleet FILE] [--apply]` (the fleet defaults to the pair). **`rebuild [--apply]`** (head: the offline DeepSeek rebuild, with its checks). **`identity --target MNT (--bundle F \| --fresh --hostname H) [--serial S] [--apply]`** (the only root mode: from the live USB, on the mounted restored disk, never on `/`). All dry run by default |
| `scripts/backends/pylib/spark_backup.py` | the engine (stdlib, python >= 3.9) |
| `scripts/backends/data/spark-backup.items` | **what a backup is**: `keep`, `secret`, `weights`, `node`, `rebuild`, `skip`, `image`, `volume` (`rankcache` unused) |
| `scripts/backends/data/spark-golden.exclude` | what the golden image leaves out (GNU tar `--anchored --exclude-from`; one pattern per line, no comments) |
| `scripts/backends/fleet.env.example` | the pair |
| `tests/test_spark_backup.sh` | Hermetic: fakes for docker, findmnt, lsblk, zstd, nvidia-smi, ibdev2netdev, systemctl, mokutil, ssh-keygen, ssh and the prepare script; scratch homes and a scratch DGX OS root; real GNU rsync and GNU tar when present. Covered below |

What `tests/test_spark_backup.sh` covers:

- the refusals;
- hard links across three trees;
- the filter: rank files, token maps, served folders and GLM kernels out, the kit's `.git` in;
- the `rankcache` opt-in;
- generations and `--link-dest`, and verify;
- the worker and head restores, and the cluster key for an ssh source;
- `rebuild`'s refusals and its run;
- `golden-image --plan`, and the exclude file against a real GNU tar archive;
- `identity` from a bundle and fresh, and its refusals: `/`, foreign members, no host name, not root.

Test seams (environment): `SPARK_BACKUP_HOME`, `SPARK_BACKUP_ITEMS`,
`SPARK_BACKUP_SERVING_REV`, `SPARK_BACKUP_SERVING_WCD`,
`SPARK_BACKUP_HOSTNAME`, `SPARK_BACKUP_NOW`, `SPARK_BACKUP_UID`/`_GID`/`_USER`,
`SPARK_RESTORE_HOME_CHECK=0`, `SPARK_IDENTITY_ROOT_CHECK=0`,
`SPARK_PREPARE_LOCAL`, `SPARK_GOLDEN_DGX_RELEASE`, `SPARK_BACKUP_HELPER_IMAGE`,
and the commands `DOCKER`, `RSYNC`, `ZSTD`, `FINDMNT`, `LSBLK`, `NVIDIA_SMI`,
`IBDEV2NETDEV`, `SYSTEMCTL`, `SSH`, `GIT`, `MOKUTIL`, `FWUPDMGR`,
`SSH_KEYGEN`, plus `UNAME_M`.

---

## 8. Decisions and open questions

**Answered by Max, 2026-11-18:**

1. **Offline first boot?** ANSWERED: everything fully offline, the OS
   reinstall and first boot included, and an online first boot is not
   acceptable. Met by the golden base image ([§3.1](#31-offline-the-answer),
   [§3.2](#32-the-golden-base-image)), with NVIDIA's OEMDATA route as the
   fallback.
2. **What is the 4 TB drive?** ANSWERED: an SSD. Estimates are SSD only.
3. **LUKS passphrase and header?** ANSWERED (chosen for Max): the passphrase
   in the password manager plus a printed copy kept apart from the drive.
   The header backup on the key stick and on the Mac. Never on the SSD or
   the Sparks ([§2.5](#25-secrets-luks2-and-where-the-passphrase-and-header-live)).
4. **Rank files?** ANSWERED: back up the checkpoints, never what rebuilds
   offline. Rank files, token maps, served folders and GLM's kernels are
   `rebuild` ([§3.5](#35-the-offline-rebuild)).
5. **N Sparks?** ANSWERED: the pair on QSFP. The fleet tooling stays,
   defaulted to the pair; the rest is [Appendix A](#appendix-a-future-more-than-two-sparks).
6. **PyTorch base image?** ANSWERED: include it (now an ordinary `head`
   image line).
7. **A disk image?** ANSWERED (our recommendation): yes, as the golden base
   image that solves decision 1. It is file level, without `/home`, with the
   identity per node.

**Still open:**

0. **Two new home entries on spark-1 that no items line claims**, found by
   the 2026-11-18 inventory: `~/git` (27 MB) and `~/live-aside` (24 KB).
   Keep, skip or rebuild? Until a line claims them, `run` lists them as NOT
   backed up. The same inventory flags `~/.gnupg/sshcontrol` as readable by
   group and other (`chmod 600` it).

1. **When is the capture outage (drill G with drill 5)?** About 20 min for
   the capture, and about 2 h for the full drill on spark-2. GLM is down for
   both.
2. **A second SSD** for an independent copy, kept elsewhere? One drive is one
   copy ([§2.8](#28-schedule-and-retention)).
3. **NVIDIA's latest recovery image** (October 2026 or later): download it
   once onto the SSD, and make stick 3? It is the firmware path for a
   replacement unit and the OEMDATA fallback. This is the one online step,
   done ahead of time.
4. **A second live stick** (about 10 euros) so both Sparks restore their OS
   at once? It saves about 15 min.
5. **Firmware `.cab` files** for the five devices in [§3.2](#32-the-golden-base-image)
   D, fetched once from LVFS onto the SSD?

---

## Appendix A: future, more than two Sparks

The fleet support stays in the tools (`NODE_2` and up, 1 to 8 nodes, roles,
per-node files), tested against fakes, never run past 2:

- **More pairs.** Each pair is its own fleet: its own fleet file, head, gate,
  tailnet names and enrolled devices. Restore one backup onto each pair. The
  golden image restores onto any unit with `identity --fresh`.
- **One cluster of N.** It needs a RoCE-capable 200 GbE switch (NVIDIA's
  playbook covers 2 to 4 Sparks, CLAIMED). Three Sparks in a ring need a
  subnet per cable, which `spark-node.sh` cannot rendezvous over. Serving
  needs a TP-N profile through `spark-cluster.sh`. Both TP-2 recipes'
  `local.sh` are kept unchanged, and the restore says so. Rank files differ
  per rank and are rebuilt per rank.

## Sources

- NVIDIA, DGX Spark User Guide, *System Recovery*: <https://docs.nvidia.com/dgx/dgx-spark/system-recovery.html>
- NVIDIA, *First Boot*: <https://docs.nvidia.com/dgx/dgx-spark/first-boot.html>
- NVIDIA, *System Repair* (Ubuntu ARM64 live USB): <https://docs.nvidia.com/dgx/dgx-spark/boot-repair.html>
- NVIDIA, *Enterprise custom install* (OEMDATA cloud-init, `debs/`, `firmware/`, OOBE bypass, mirror sizes): <https://docs.nvidia.com/dgx/dgx-spark/enterprise-custom-install.html>
- NVIDIA, *PXE install*: <https://docs.nvidia.com/dgx/dgx-spark/pxe.html>; *OS and component update* (fwupd/LVFS): <https://docs.nvidia.com/dgx/dgx-spark/os-and-component-update.html>; *DGX OS* ("do not use the DGX OS ISO" on a Spark): <https://docs.nvidia.com/dgx/dgx-spark/dgx-os.html>
- NVIDIA, *Connect multiple Sparks* playbook: <https://build.nvidia.com/spark/connect-two-sparks>
- Forum:
  - offline setup, NVIDIA staff 2026-06-19: <https://forums.developer.nvidia.com/t/possibility-instructions-for-offline-setup/373452>
  - *DGX Spark software updates 04-2026*: <https://forums.developer.nvidia.com/t/dgx-spark-software-updates-04-2026/368114>
  - *DGX Spark / GB10 FAQ*: <https://forums.developer.nvidia.com/t/dgx-spark-gb10-faq/347344>
  - recovery USB contents: <https://forums.developer.nvidia.com/t/cannot-restore-from-recover-image/350690>
  - *Reinstalling DGX OS*: <https://forums.developer.nvidia.com/t/reinstalling-dgx-os-on-the-dgx-spark/347603>
  - *Community Spark Recovery project* (ESP + one root partition): <https://forums.developer.nvidia.com/t/community-spark-recovery-project-data-preserving-os-recovery-for-dgx-spark/371102>
  - identical machine-ids on cloned units: <https://forums.developer.nvidia.com/t/msi-edgexpert-dgx-spark-having-identical-machine-ids/377208>
- Ours: [SPARK_HYDRA.md](SPARK_HYDRA.md), [DGX_SPARK_PLAN.md](DGX_SPARK_PLAN.md), [BEAST_ARK.md](BEAST_ARK.md), [BEAST_ISLAND.md](BEAST_ISLAND.md), [UPDATING.md](UPDATING.md)
