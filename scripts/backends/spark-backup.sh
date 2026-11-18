#!/bin/bash
# spark-backup: one DGX Spark's OpenBeast state, onto an external drive, as a
# file-level overlay that can rebuild this node or populate N others.
# docs/SPARK_BACKUP_RESTORE.md
#
#   scripts/backends/spark-backup.sh inventory [--role head|worker] [--json]   # read-only: what a backup holds, sizes, what is unclaimed
#   scripts/backends/spark-backup.sh run --to /media/max/obbackup              # DRY RUN: the plan, the filter, the drive checks
#   scripts/backends/spark-backup.sh run --to /media/max/obbackup --apply      # write a new generation
#   scripts/backends/spark-backup.sh verify GEN [--deep] [--against-live]      # read-only: sizes + hard links; --deep re-hashes
#   scripts/backends/spark-backup.sh list /media/max/obbackup                  # the generations on a drive
#
#   run options: --no-rank-caches (skip the ~106 GB rank files; rebuilt in ~15 min)
#                --config-only (no weights, rank files or images: a worker, whose weights the head's backup holds)
#                --no-images  --with-optional-images (also nvcr pytorch)
#                --no-pause (copy docker volumes live)  --no-hash (manifest without sha256)
#                --allow-unencrypted (the drive is not LUKS: refused by default)
#
# WHAT. The items file scripts/backends/data/spark-backup.items says, line by
# line, what is kept, what is a secret, what is weights (hard-linked across
# Documents/, the HF caches and openbeast-models/), which rank caches go (the
# serving pack's) and which are rebuilt, what is per-node (never copied to
# another node) and what is skipped. `inventory` walks the live home against
# it and lists anything no line claims, so nothing is left out silently.
#
# HOW. One `rsync -aHAX --numeric-ids` with a filter generated from the items
# file into DRIVE/openbeast-backup/<hostname>/<UTC stamp>.partial, with
# --link-dest to the previous generation (unchanged files cost no space);
# docker images saved with zstd (linked from the previous generation when the
# image ID is unchanged); docker volumes exported through a throwaway alpine
# container with the containers using them PAUSED (crash-consistent SQLite,
# no restart); a read-only system record (dpkg, dgx-release, fabric, units,
# tailscale serve); MANIFEST.jsonl + SHA256SUMS. Only then is .partial renamed
# and `latest` moved. Nothing is stopped, nothing runs as root, nothing is
# written outside the drive.
#
# REFUSES: a destination that is not a mounted ext4/xfs/btrfs filesystem
# (exFAT/NTFS/vfat lose owners, modes, hard links and symlinks), a destination
# on the root filesystem, and a drive that is not LUKS-encrypted unless
# --allow-unencrypted (the backup holds every key and token of the node).
#
# OFFLINE: nothing here touches the network.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
case "${1:-}" in
  ""|-h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  inventory|run|verify|list) ;;
  *) echo "Unknown command: $1 (see --help)" >&2; exit 2 ;;
esac
exec python3 "$HERE/pylib/spark_backup.py" "$@"
