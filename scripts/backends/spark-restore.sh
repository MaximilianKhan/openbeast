#!/bin/bash
# spark-restore: put an OpenBeast backup onto a freshly installed DGX Spark as
# node N of a fleet: the overlay from the backup, the per-node bits written for
# THIS node. docs/SPARK_BACKUP_RESTORE.md
#
#   scripts/backends/spark-restore.sh --from GEN --fleet FILE --index N            # DRY RUN: the plan
#   scripts/backends/spark-restore.sh --from GEN --fleet FILE --index N --apply    # asks for the node name, then restores
#
#   GEN     a generation: /media/max/obbackup/openbeast-backup/spark-3dc6/latest,
#           or the same path on another node over ssh: max@10.100.216.1:/media/...
#   FILE    the fleet file (scripts/backends/fleet.env.example): every node's
#           tailnet name, role (head|worker) and fabric address, rank order
#   N       this node's index in it (0 = the head)
#   --skip-weights  --skip-rank-caches  --skip-images  --force-volumes  --yes
#   --no-units (enable nothing: a scratch-home restore drill)
#
# ORDER. Stock DGX OS first (NVIDIA's recovery USB, first boot, the same user
# and uid as the backup), then this, then the root steps it prints
# (spark-root-setup.sh, spark-fabric-mtu.sh, Tailscale re-auth), then checks.
#
# WHAT IT WRITES, and nothing else:
#   1. the backup's files for this node's ROLE, rsync -aHAX --numeric-ids, never
#      --delete. Per-node files (spark.env, cluster.env, ssh config, known_hosts,
#      the units' *.wants links) are never copied; head-only files (openbeast.conf,
#      gate keys, recipes, WebUI data) never reach a worker.
#   2. per-node files from the fleet file: spark.env (SPARK_NODE_IP, SPARK_HEAD_IP,
#      SPARK_IFACE / SPARK_IB_HCA from the ConnectX ports that are Up), cluster.env
#      (head: CLUSTER_NODES in rank order), the DeepSeek recipe's WORKER /
#      FABRIC_PEER (head of a pair), TS_HOSTNAME (head), a managed ssh-config
#      block for the peers, the opencode-model-link unit (worker). Each file it
#      replaces is kept as <file>.pre-restore.
#   3. docker load of the role's images (skipped when the ID is present); the
#      compose digest pins rewritten to the loaded IDs (a loaded image has no
#      RepoDigest; the original is docker-compose.yml.pre-restore); the WebUI
#      volume imported only into an empty or new volume.
#   4. systemctl --user daemon-reload and enable (never start) the role's units.
#
# NEVER: root, hostname, /etc/machine-id, ssh host keys, Tailscale state,
# netplan, docker's engine ID. Those belong to the node; the printed root
# steps make them. OFFLINE: the network is used only for an ssh source.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
case "${1:-}" in -h|--help|"") sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac
exec python3 "$HERE/pylib/spark_backup.py" restore "$@"
