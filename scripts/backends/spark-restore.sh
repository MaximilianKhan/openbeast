#!/bin/bash
# spark-restore: put an OpenBeast backup onto a Spark of the pair (spark-hydra:
# two DGX Sparks on one QSFP cable). The OS comes first, from the golden base
# image (spark-backup.sh golden-image --plan); then this. docs/SPARK_BACKUP_RESTORE.md
#
#   scripts/backends/spark-restore.sh --from GEN --index N            # DRY RUN: the plan (N: 0 = spark-1, 1 = spark-2)
#   scripts/backends/spark-restore.sh --from GEN --index N --apply    # asks for the node name, then restores
#   scripts/backends/spark-restore.sh rebuild [--apply]               # head, after both: DeepSeek's rank files, offline (~35 min)
#   scripts/backends/spark-restore.sh identity --target /mnt/target --bundle F [--apply]
#        # live USB, root: write ONE node's identity into a restored golden image (or --fresh --hostname H)
#
#   GEN     a generation: /media/max/obbackup/openbeast-backup/spark-3dc6/latest,
#           or the same path on the head over ssh: max@10.100.216.1:/media/...
#   --fleet FILE  default scripts/backends/fleet.env.example: the pair
#   --skip-weights  --skip-rank-caches  --skip-images  --force-volumes  --yes
#   --no-units (enable nothing: a scratch-home restore drill)
#
# WHAT IT WRITES, and nothing else: (1) the backup's files for this node's ROLE,
# rsync -aHAX --numeric-ids, never --delete; per-node files are never copied,
# head-only files never reach the worker. (2) per-node files from the fleet
# file: spark.env, cluster.env, both recipes' WORKER, ssh peers, the worker's
# opencode-model-link unit (old copies kept as <file>.pre-restore). (3) docker
# load of the role's images, compose pins rewritten to the loaded IDs, the
# WebUI volume into an empty volume. (4) enables (never starts) the role's
# units. It prints what the backup left out on purpose (`rebuild` items).
#
# NEVER (this mode): root, hostname, machine-id, ssh host keys, Tailscale
# state, netplan, docker's engine ID. Those are the `identity` mode's, run as
# root from the live USB against the restored disk, never against /.
# OFFLINE: the network is used only for an ssh source.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
case "${1:-}" in
  -h|--help|"") sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  rebuild|identity) exec python3 "$HERE/pylib/spark_backup.py" "$@" ;;
esac
exec python3 "$HERE/pylib/spark_backup.py" restore "$@"
