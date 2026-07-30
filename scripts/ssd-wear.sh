#!/usr/bin/env bash
# OpenBeast — SSD/NVMe wear tracking.
#
# WHY THIS EXISTS
#   Agent/LLM workloads chew through drive endurance. Model loads, eval-cache
#   and KV writes, log churn, and swap storms all land on NAND — and a 2026-07-07
#   OOM on this box burned through 187 GB of swap in one incident. Reads are
#   nearly free on NAND (the 20 GB GGUF loads cost almost nothing); WRITES are
#   what kill drives. Reports of agent tooling quietly eating people's SSDs are
#   what prompted this: we want the numbers BEFORE it is a problem, computed
#   locally, on our own hardware, with nothing phoning home.
#
# WHAT IT DOES
#   Finds only the physical drives that actually back the weights dir and the
#   repo (through partitions, LUKS/dm-crypt and LVM), reads SMART read-only via
#   smartctl, and tracks the write counters over time in .run/ssd-wear.json so
#   it can report GB/day and a projected date for 100% life used.
#
# Usage:
#   ./scripts/ssd-wear.sh              # human report
#   ./scripts/ssd-wear.sh --json       # machine-readable (for doctor/dashboard)
#   ./scripts/ssd-wear.sh --snapshot   # record a datapoint, say almost nothing
#   ./scripts/ssd-wear.sh --doctor     # "status|message|fix" lines for doctor.sh
#
# Notes:
#   • Read-only. This script never issues a SMART self-test or any command that
#     changes device state — only `smartctl -j -a`.
#   • Advisory tooling, not a gate: missing smartctl, no root, or an unsupported
#     device degrade loudly but ALWAYS exit 0. It must never break a caller.
#   • GB/TB here are decimal (1e9 / 1e12 bytes), matching how drive vendors and
#     the NVMe spec count. 1 NVMe data unit = 512,000 bytes (NOT 512).
#   • Serial numbers are never printed — they are device identifiers. Only a
#     truncated hash is kept, and only to key history across device renames.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export REPO_DIR

MODE="report"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)     MODE="json" ;;
    --snapshot) MODE="snapshot" ;;
    --doctor)   MODE="doctor" ;;
    -h|--help)  sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

# --json and --doctor own stdout: their consumers parse it. All human chatter
# from here on goes to stderr in those modes.
say() {
  case "$MODE" in
    json|doctor) echo "$*" >&2 ;;
    *)           echo "$*" ;;
  esac
}
# One degraded doctor row, for the early-exit paths where there is nothing to
# report. Advisory: doctor should see "unknown", never a fabricated green.
doctor_unknown() { # doctor_unknown <message> <fix>
  [[ "$MODE" == "doctor" ]] && printf 'warn|%s|%s\n' "$1" "$2"
  return 0
}

STATE_DIR="$REPO_DIR/.run"
STATE_FILE="${OPENBEAST_WEAR_STATE:-$STATE_DIR/ssd-wear.json}"

TMPDIR_RUN="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-wear.XXXXXX")"
cleanup() { rm -rf "$TMPDIR_RUN"; }
trap cleanup EXIT
MANIFEST="$TMPDIR_RUN/manifest.tsv"
: > "$MANIFEST"

# ── 1. Which directories do we care about? ──────────────────────────────────
# Only the mounts OpenBeast actually writes to. Enumerating every block device
# on the box would be noise: a wear report is useful when it says "the drive
# holding your weights and your eval cache is at 3%", not when it lists a USB
# stick.
#
# Resolve WEIGHTS_DIR without lib/weights.sh's hard exit-on-missing (that exit
# is correct for serve scripts, fatal here): it exports WEIGHTS_DIR before the
# existence check, so a guarded subshell captures it either way.
WEIGHTS_DIR="$( (source "$SCRIPT_DIR/lib/weights.sh" >/dev/null 2>&1; printf '%s' "${WEIGHTS_DIR:-}") || true )"
[[ -n "$WEIGHTS_DIR" ]] || WEIGHTS_DIR="$REPO_DIR/weights"

# ── 2. dir → filesystem source → physical disk ──────────────────────────────
# `df` gives the filesystem source, which on this class of machine is very
# often NOT a physical device: /dev/mapper/root (LUKS), an LVM LV, or an md
# array. lsblk --inverse walks the stack down to the real disk(s) — a
# name-munging shortcut (strip the partition suffix) silently produces garbage
# on any encrypted or LVM root, which is the common Linux layout.
_fs_source() { # _fs_source <dir> -> /dev/... or empty
  local src
  src="$(df -P "$1" 2>/dev/null | awk 'NR==2 {print $1}')" || true
  [[ "$src" == /dev/* ]] || return 1
  printf '%s\n' "$src"
}

# Fallback for boxes without lsblk: strip the partition suffix by name.
#   nvme0n1p2 -> nvme0n1 ,  mmcblk0p1 -> mmcblk0 ,  sda2 -> sda
_strip_partition() { # _strip_partition /dev/X -> /dev/Y
  local n="${1#/dev/}"
  case "$n" in
    # Partition forms only. A WHOLE-disk name must pass through untouched:
    # nvme0n1 has no "p", and the generic *[0-9] rule would turn it into
    # "nvme" — a device that does not exist. Same for mmcblk0.
    nvme*n*p[0-9]*|mmcblk[0-9]*p[0-9]*) n="${n%p*}" ;;
    nvme*n[0-9]*|mmcblk[0-9]*)          : ;;
    sd[a-z]*[0-9]|vd[a-z]*[0-9]|hd[a-z]*[0-9]) n="${n%%[0-9]*}" ;;
  esac
  printf '/dev/%s\n' "$n"
}

_parent_disks() { # _parent_disks /dev/mapper/root -> one /dev/... per line
  local src="$1" out=""
  if command -v lsblk >/dev/null 2>&1; then
    out="$(lsblk -nrpo NAME,TYPE --inverse "$src" 2>/dev/null | awk '$2=="disk" {print $1}')" || true
  fi
  if [[ -n "$out" ]]; then printf '%s\n' "$out"; else _strip_partition "$src"; fi
}

# role -> disk mapping, deduped, roles accumulated per disk.
DEV_LIST=()
DEV_ROLES=()
_add_dev() { # _add_dev <disk> <role>
  local i
  for i in "${!DEV_LIST[@]}"; do
    if [[ "${DEV_LIST[$i]}" == "$1" ]]; then
      case ",${DEV_ROLES[$i]}," in *",$2,"*) : ;; *) DEV_ROLES[$i]="${DEV_ROLES[$i]},$2" ;; esac
      return 0
    fi
  done
  DEV_LIST+=("$1"); DEV_ROLES+=("$2")
}

UNRESOLVED=()
for pair in "weights:$WEIGHTS_DIR" "repo:$REPO_DIR"; do
  role="${pair%%:*}"; dir="${pair#*:}"
  [[ -d "$dir" ]] || continue
  if ! src="$(_fs_source "$dir")"; then
    # Network mounts (NFS/SMB), overlayfs and ZFS pools have no /dev source we
    # can read SMART from. Say so; do not guess.
    UNRESOLVED+=("$role:$dir")
    continue
  fi
  while IFS= read -r disk; do
    [[ -n "$disk" ]] || continue
    case "$disk" in /dev/zram*|/dev/loop*|/dev/ram*) continue ;; esac
    _add_dev "$disk" "$role"
  done < <(_parent_disks "$src")
done

if [[ ${#DEV_LIST[@]} -eq 0 ]]; then
  say "ssd-wear: no physical device could be resolved for the weights or repo mounts."
  for u in "${UNRESOLVED[@]:-}"; do
    [[ -n "$u" ]] && say "  ${u%%:*} (${u#*:}) is not backed by a local block device — SMART does not apply."
  done
  # NB: `if`, not `[[ ]] && printf` — under set -e a false && list is itself a
  # failing command, and this tool must never hand its caller a nonzero status.
  if [[ "$MODE" == "json" ]]; then
    printf '{"schema":1,"status":"unknown","reason":"no_local_device","devices":[]}\n'
  fi
  doctor_unknown "drive wear unknown: weights/repo are not on a local block device" \
                 "SMART does not apply to network/overlay filesystems"
  exit 0
fi

# ── 3. Read SMART, read-only ────────────────────────────────────────────────
# The binary is overridable so a site can point at a wrapper (and so the tests
# can exercise the "not installed" path without emptying PATH).
SMARTCTL="${OPENBEAST_SMARTCTL:-smartctl}"
if ! command -v "$SMARTCTL" >/dev/null 2>&1; then
  say "ssd-wear: smartctl not found — drive wear cannot be read."
  say "  → install smartmontools:  sudo pacman -S smartmontools   (Debian/Ubuntu: sudo apt install smartmontools)"
  say "  This is advisory tooling, so this is not an error."
  if [[ "$MODE" == "json" ]]; then
    printf '{"schema":1,"status":"unknown","reason":"smartctl_missing","devices":[]}\n'
  fi
  doctor_unknown "drive wear not tracked — smartctl is not installed" \
                 "install smartmontools, then ./scripts/ssd-wear.sh"
  exit 0
fi

# smartctl's exit code is a BITMASK, not a status: bit1(2)=device open failed,
# bit3(8)=drive is FAILING. A nonzero exit therefore does not mean the JSON is
# worthless — exit 8 is precisely the case we most want to report. So never
# gate on the exit code; parse the JSON and let it speak.
_smart_read() { # _smart_read <disk> <outfile> -> prints fetch status
  local disk="$1" out="$2" rc=0
  "$SMARTCTL" -j -a "$disk" >"$out" 2>/dev/null || rc=$?
  if grep -q '"nvme_smart_health_information_log"\|"ata_smart_attributes"\|"model_name"' "$out" 2>/dev/null; then
    printf 'ok\n'; return 0
  fi
  # Unprivileged is the overwhelmingly common case: smartctl needs raw device
  # access. Try a NON-INTERACTIVE sudo so a configured sudoers line just works
  # and an unconfigured box never hangs on a password prompt.
  if command -v sudo >/dev/null 2>&1; then
    # shellcheck disable=SC2024  # the redirect is intentionally OURS (a file in
    # our private mktemp dir); only smartctl needs to run elevated.
    if sudo -n "$SMARTCTL" -j -a "$disk" >"$out.sudo" 2>/dev/null || [[ -s "$out.sudo" ]]; then
      if grep -q '"nvme_smart_health_information_log"\|"ata_smart_attributes"\|"model_name"' "$out.sudo" 2>/dev/null; then
        mv "$out.sudo" "$out"; printf 'ok\n'; return 0
      fi
    fi
    rm -f "$out.sudo"
  fi
  if grep -qi 'permission denied\|requires.*root\|operation not permitted' "$out" 2>/dev/null || [[ $rc -eq 2 ]]; then
    printf 'denied\n'
  else
    printf 'unsupported\n'
  fi
}

ANY_OK=0 ANY_DENIED=0
for i in "${!DEV_LIST[@]}"; do
  disk="${DEV_LIST[$i]}"
  out="$TMPDIR_RUN/dev$i.json"
  status="$(_smart_read "$disk" "$out")"
  case "$status" in
    ok)     ANY_OK=1 ;;
    denied) ANY_DENIED=1 ;;
  esac
  printf '%s\t%s\t%s\t%s\n' "$disk" "${DEV_ROLES[$i]}" "$out" "$status" >> "$MANIFEST"
done

if [[ $ANY_OK -eq 0 && $ANY_DENIED -eq 1 ]]; then
  say "ssd-wear: smartctl needs root to read raw device data (every drive returned permission denied)."
  for i in "${!DEV_LIST[@]}"; do
    say "  found: ${DEV_LIST[$i]} (backs ${DEV_ROLES[$i]//,/, })"
  done
  say "  → run it once as root:      sudo ./scripts/ssd-wear.sh"
  say "  → or permit just this read, so doctor/snapshots work unattended:"
  _sc_path="$(command -v smartctl 2>/dev/null || echo /usr/sbin/smartctl)"
  say "        echo \"$(id -un) ALL=(root) NOPASSWD: ${_sc_path} -j -a /dev/*\" | sudo tee /etc/sudoers.d/openbeast-smartctl"
  say "        sudo chmod 440 /etc/sudoers.d/openbeast-smartctl"
  say "  (this script only ever runs 'smartctl -j -a' — read-only, no self-tests)"
  if [[ "$MODE" == "json" ]]; then
    printf '{"schema":1,"status":"unknown","reason":"permission_denied","devices":[]}\n'
  fi
  doctor_unknown "drive wear unreadable — smartctl needs root" \
                 "./scripts/ssd-wear.sh prints the one-line sudoers rule to permit the read"
  exit 0
fi

# ── 4. Parse, snapshot, project, report ─────────────────────────────────────
# All JSON handling lives in python: bash must never hand-roll JSON, and the
# state file is written atomically (mkstemp + os.replace, mode 0600) so a
# crash or a concurrent doctor run can never leave a truncated history behind.
# Under the recommended `sudo ./scripts/ssd-wear.sh`, a fresh mkdir here would
# create a ROOT-OWNED .run/ that the unprivileged stack can no longer write —
# breaking pidfiles and audit on the next start. Create it as the invoking
# user, and never create it at all when running as root under sudo.
_state_dir="$(dirname "$STATE_FILE")"
if [[ ! -d "$_state_dir" ]]; then
  if [[ ${EUID:-$(id -u)} -eq 0 && -n "${SUDO_UID:-}" ]]; then
    install -d -o "$SUDO_UID" -g "${SUDO_GID:-$SUDO_UID}" -m 700 "$_state_dir" 2>/dev/null || true
  else
    mkdir -p "$_state_dir" 2>/dev/null || true
  fi
fi
# Likewise, never leave a root-owned state file behind for the user's stack.
if [[ ${EUID:-$(id -u)} -eq 0 && -n "${SUDO_UID:-}" && -f "$STATE_FILE" ]]; then
  chown "$SUDO_UID:${SUDO_GID:-$SUDO_UID}" "$STATE_FILE" 2>/dev/null || true
fi

OB_WEAR_MANIFEST="$MANIFEST" \
OB_WEAR_STATE="$STATE_FILE" \
OB_WEAR_MODE="$MODE" \
OB_WEAR_MIN_INTERVAL="${OPENBEAST_WEAR_MIN_INTERVAL:-300}" \
OB_WEAR_MIN_SPAN="${OPENBEAST_WEAR_MIN_SPAN:-3600}" \
OB_WEAR_HISTORY="${OPENBEAST_WEAR_HISTORY:-64}" \
OB_WEAR_UNRESOLVED="$(printf '%s;' "${UNRESOLVED[@]:-}")" \
python3 <<'PY'
import hashlib, json, os, sys, tempfile, time

MANIFEST = os.environ["OB_WEAR_MANIFEST"]
STATE    = os.environ["OB_WEAR_STATE"]
MODE     = os.environ["OB_WEAR_MODE"]
MIN_INTERVAL = float(os.environ.get("OB_WEAR_MIN_INTERVAL") or 300)
MIN_SPAN     = float(os.environ.get("OB_WEAR_MIN_SPAN") or 3600)
MAX_POINTS   = max(2, int(os.environ.get("OB_WEAR_HISTORY") or 64))
UNRESOLVED   = [u for u in os.environ.get("OB_WEAR_UNRESOLVED", "").split(";") if u]

GB = 1e9
TB = 1e12
# The one number wear scripts get wrong: an NVMe "data unit" is 1000 * 512
# bytes, not 512. Off by 1000x in the alarming direction.
NVME_DATA_UNIT_BYTES = 512_000

now = time.time()


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    return None


def parse_nvme(d):
    log = d.get("nvme_smart_health_information_log") or {}
    if not log:
        return None
    duw = _num(log.get("data_units_written"))
    dur = _num(log.get("data_units_read"))
    return {
        "type": "nvme",
        "percentage_used": _num(log.get("percentage_used")),
        "bytes_written": duw * NVME_DATA_UNIT_BYTES if duw is not None else None,
        "bytes_read": dur * NVME_DATA_UNIT_BYTES if dur is not None else None,
        "media_errors": _num(log.get("media_errors")),
        "unsafe_shutdowns": _num(log.get("unsafe_shutdowns")),
        "power_on_hours": _num(log.get("power_on_hours")),
    }


def parse_ata(d):
    """Best-effort SATA/ATA. Vendors disagree on attribute names and raw units,
    so anything we cannot read confidently stays None and is reported as
    'unavailable' — never invented."""
    table = ((d.get("ata_smart_attributes") or {}).get("table")) or []
    if not table:
        return None
    by_name, by_id = {}, {}
    for a in table:
        if isinstance(a, dict):
            by_name[str(a.get("name", "")).lower()] = a
            by_id[_num(a.get("id"))] = a

    def raw(attr):
        if not attr:
            return None
        return _num((attr.get("raw") or {}).get("value"))

    def norm(attr):
        return _num(attr.get("value")) if attr else None

    # Life used. These attributes report REMAINING life as the normalized
    # value, so used = 100 - remaining.
    pct_used = None
    for key in ("percent_lifetime_remain", "ssd_life_left", "media_wearout_indicator",
                "wear_leveling_count"):
        a = by_name.get(key)
        n = norm(a)
        if n is not None and 0 <= n <= 100:
            pct_used = 100 - n
            break

    # Host writes. Unit depends entirely on the attribute name.
    bytes_written = None
    lbs = _num(d.get("logical_block_size")) or 512
    for key, mult in (("total_lbas_written", lbs),
                      ("total_host_writes", lbs),
                      ("host_writes_32mib", 32 * 1024 * 1024),
                      ("host_writes_gib", 1024 ** 3),
                      ("lifetime_writes_gib", 1024 ** 3)):
        r = raw(by_name.get(key))
        if r is not None:
            bytes_written = r * mult
            break

    bytes_read = None
    for key, mult in (("total_lbas_read", lbs), ("host_reads_32mib", 32 * 1024 * 1024)):
        r = raw(by_name.get(key))
        if r is not None:
            bytes_read = r * mult
            break

    reall = raw(by_id.get(5)) or 0
    uncorr = raw(by_id.get(187)) or 0
    pending = raw(by_id.get(197)) or 0
    poh = raw(by_name.get("power_on_hours"))
    if poh is None:
        poh = _num((d.get("power_on_time") or {}).get("hours"))

    return {
        "type": "ata",
        "percentage_used": pct_used,
        "bytes_written": bytes_written,
        "bytes_read": bytes_read,
        "media_errors": reall + uncorr + pending,
        "unsafe_shutdowns": None,
        "power_on_hours": poh,
    }


devices = []
for line in open(MANIFEST, encoding="utf-8"):
    line = line.rstrip("\n")
    if not line:
        continue
    dev, roles, path, fetch = line.split("\t")
    rec = {
        "device": dev,
        "backs": roles.split(","),
        "fetch": fetch,
        "type": None, "model": None, "id": None,
        "percentage_used": None, "bytes_written": None, "bytes_read": None,
        "media_errors": None, "unsafe_shutdowns": None, "power_on_hours": None,
        "smart_passed": None,
        "delta": None, "projection": None, "concerns": [], "status": "unknown",
    }
    data = None
    if fetch == "ok":
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = None
    if data is None:
        rec["note"] = {
            "denied": "smartctl could not open the device (needs root)",
            "unsupported": "device does not expose SMART data smartctl can read",
        }.get(fetch, "SMART data unreadable")
        devices.append(rec)
        continue

    rec["model"] = data.get("model_name") or data.get("model_family")
    status = data.get("smart_status") or {}
    if isinstance(status.get("passed"), bool):
        rec["smart_passed"] = status["passed"]
    # The serial is an identifier: never emitted, never persisted. A truncated
    # hash is enough to keep history attached to the right drive when device
    # names shuffle (nvme0n1 <-> nvme1n1 across reboots is real).
    serial = data.get("serial_number")
    if serial:
        rec["id"] = hashlib.sha256(str(serial).encode()).hexdigest()[:12]

    parsed = parse_nvme(data)
    if parsed is None:
        parsed = parse_ata(data)
    if parsed is None:
        rec["note"] = "SMART present but neither an NVMe health log nor an ATA attribute table was found"
        devices.append(rec)
        continue
    rec.update(parsed)
    if rec["bytes_written"] is None:
        rec["note"] = "this drive does not report a host-write counter — wear rate cannot be tracked"
    devices.append(rec)


# ── history: load, prune, append, atomic write ──────────────────────────────
def load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            s = json.load(fh)
        if isinstance(s, dict) and isinstance(s.get("devices"), dict):
            return s
    except Exception:
        pass
    return {"schema": 1, "devices": {}}


def save_state(state):
    d = os.path.dirname(STATE) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".ssd-wear.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE)          # atomic: readers see old or new, never half
        os.chmod(STATE, 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


state = load_state()
wrote_state = False
for rec in devices:
    if rec["bytes_written"] is None:
        continue
    key = rec["id"] or rec["device"]
    entry = state["devices"].setdefault(key, {"device": rec["device"], "points": []})
    entry["device"] = rec["device"]
    entry["model"] = rec["model"]
    pts = [p for p in entry.get("points", []) if isinstance(p, dict) and "t" in p and "w" in p]

    # A counter that went BACKWARDS means the drive was swapped or its counters
    # reset. Keeping the old points would produce a negative (or absurd) rate,
    # so the history for that device starts over.
    pts = [p for p in pts if _num(p.get("w")) is not None and p["w"] <= rec["bytes_written"]]

    # Coalesce rapid re-runs (doctor may call this often) so a burst of runs
    # cannot flush the useful window out of a bounded history.
    if not pts or (now - pts[-1]["t"]) >= MIN_INTERVAL:
        pts.append({
            "t": now,
            "w": rec["bytes_written"],
            "r": rec["bytes_read"],
            "p": rec["percentage_used"],
        })
        wrote_state = True
    # Bounded, but the OLDEST point is an anchor and is kept forever: it is what
    # makes the GB/day window keep growing instead of collapsing to the last
    # few runs. Pruning drops from the middle.
    if len(pts) > MAX_POINTS:
        pts = [pts[0]] + pts[-(MAX_POINTS - 1):]
    entry["points"] = pts
    rec["_points"] = pts

if wrote_state:
    state["updated_at"] = now
    try:
        save_state(state)
    except Exception as exc:                     # advisory tooling: never fatal
        print(f"ssd-wear: could not write {STATE}: {exc}", file=sys.stderr)


# ── deltas + projection ─────────────────────────────────────────────────────
for rec in devices:
    pts = rec.pop("_points", None)
    if not pts:
        continue
    first, last = pts[0], pts[-1]
    span = last["t"] - first["t"]
    if len(pts) < 2 or span < MIN_SPAN:
        rec["delta"] = {
            "available": False,
            "reason": ("need another datapoint, run again later"
                       if len(pts) < 2 else
                       f"only {span / 3600:.1f} h of history — need {MIN_SPAN / 3600:.0f} h"),
            "points": len(pts),
            "span_days": span / 86400.0,
        }
        rec["projection"] = {"available": False, "reason": "not enough history yet"}
        continue

    written = last["w"] - first["w"]
    per_day = written / (span / 86400.0)
    rec["delta"] = {
        "available": True, "points": len(pts),
        "span_days": span / 86400.0,
        "since": first["t"],
        "bytes_written": written,
        "gb_written": written / GB,
        "gb_per_day": per_day / GB,
    }

    pct = rec["percentage_used"]
    proj = {"available": False, "reason": "percentage_used not reported by this drive"}
    p0, p1 = _num(first.get("p")), _num(last.get("p"))
    if pct is not None and pct >= 100:
        proj = {"available": True, "days": 0.0, "years": 0.0, "method": "already at 100%"}
    elif p0 is not None and p1 is not None and p1 > p0:
        # Best case: life-used actually moved while we watched. Extrapolate the
        # observed wear rate directly.
        days = (100 - p1) / ((p1 - p0) / (span / 86400.0))
        proj = {"available": True, "days": days, "years": days / 365.25,
                "method": "observed wear rate"}
    elif pct is not None and per_day > 0 and rec["bytes_written"]:
        # percentage_used is an integer and barely moves — on a healthy drive it
        # can sit still for months. So convert the drive's whole life to date
        # into bytes-per-percent, then burn it down at OUR measured write rate.
        # pct is quantized (a reported 3 means [3,4)); the midpoint keeps the
        # estimate honest rather than optimistic.
        if pct >= 1:
            per_pct = rec["bytes_written"] / (pct + 0.5)
            days = (100 - pct) * per_pct / per_day
            proj = {"available": True, "days": days, "years": days / 365.25,
                    "method": "lifetime average endurance (coarse: life-used is a whole percent)"}
        else:
            proj = {"available": False,
                    "reason": "life used is still 0% — too early to project (that is good news)"}
    rec["projection"] = proj


# ── concerns ────────────────────────────────────────────────────────────────
overall = "ok"
all_concerns = []
for rec in devices:
    c = rec["concerns"]
    # "readable" = we got something we can actually judge the drive on. A drive
    # we could not read is `unknown`, never a green `ok` — silently reporting
    # health we never measured is the worst failure mode this tool has.
    readable = rec["fetch"] == "ok" and (
        rec["bytes_written"] is not None or rec["percentage_used"] is not None)
    if rec.get("smart_passed") is False:
        c.append("SMART overall-health self-assessment FAILED — back up now and replace the drive")
    pct = rec["percentage_used"]
    if pct is not None and pct >= 80:
        c.append(f"life used is {pct:.0f}% — plan a replacement")
    me = rec["media_errors"]
    if me:
        c.append(f"{me:.0f} media/uncorrectable error(s) reported")
    pr = rec["projection"] or {}
    if pr.get("available") and pr.get("years") is not None and pr["years"] < 2:
        c.append(f"at the current write rate this drive reaches 100% life in {pr['years']:.1f} year(s)")
    if c:
        rec["status"] = "warn"
        overall = "warn"
        all_concerns.extend(f"{rec['device']}: {x}" for x in c)
    else:
        rec["status"] = "ok" if readable else "unknown"
if overall != "warn" and all(r["status"] == "unknown" for r in devices):
    overall = "unknown"


def gb(v):
    if v is None:
        return "unavailable"
    if v >= TB:
        return f"{v / TB:.2f} TB"
    if v >= GB:
        return f"{v / GB:.1f} GB"
    return f"{v / 1e6:.1f} MB"


if MODE == "json":
    out = {
        "schema": 1,
        "generated_at": now,
        "state_file": STATE,
        "status": overall,
        "concerns": all_concerns,
        "unresolved": UNRESOLVED,
        "devices": devices,
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    raise SystemExit(0)

if MODE == "doctor":
    # One "status|message|fix" line per device, so doctor.sh stays a six-line
    # read loop and never has to parse JSON itself. Only ok/warn are emitted —
    # this is advisory and must never contribute a FAIL to doctor's verdict.
    for rec in devices:
        dev = rec["device"]
        backs = "/".join(rec["backs"])
        if rec["status"] == "unknown":
            print(f"warn|{dev} ({backs}): {rec.get('note', 'SMART unreadable')}"
                  f"|./scripts/ssd-wear.sh")
            continue
        pct = rec["percentage_used"]
        bits = [f"{pct:.0f}% life used" if pct is not None else "life used unavailable",
                f"{gb(rec['bytes_written'])} written"]
        d = rec["delta"] or {}
        if d.get("available"):
            bits.append(f"{d['gb_per_day']:.1f} GB/day")
        p = rec["projection"] or {}
        if p.get("available"):
            bits.append(f"~{p['years']:.1f} y to 100%")
        if rec["concerns"]:
            # The concern already states the offending number, so don't restate
            # the whole summary — just carry the write rate along, which is the
            # one thing that says how fast it is getting worse.
            rate = f" ({d['gb_per_day']:.1f} GB/day)" if d.get("available") else ""
            print(f"warn|{dev} ({backs}): {'; '.join(rec['concerns'])}{rate}"
                  f"|./scripts/ssd-wear.sh")
        else:
            print(f"ok|{dev} ({backs}): " + ", ".join(bits) + "|")
    raise SystemExit(0)

if MODE == "snapshot":
    n = sum(1 for r in devices if r["bytes_written"] is not None)
    if wrote_state:
        print(f"ssd-wear: snapshot recorded for {n} device(s) → {STATE}")
    else:
        print(f"ssd-wear: datapoint already current for {n} device(s) (nothing to record)")
    raise SystemExit(0)

bold = "\033[1m" if sys.stdout.isatty() else ""
off = "\033[0m" if sys.stdout.isatty() else ""
print(f"\n{bold}OpenBeast — SSD/NVMe wear{off}")
print("NAND dies by WRITES. Model loads are reads and nearly free; eval cache,")
print("KV spill, logs and swap storms are what actually cost you the drive.\n")

for rec in devices:
    label = rec["model"] or "unknown model"
    kind = (rec["type"] or "?").upper()
    print(f"{bold}{rec['device']}{off}  {label}  [{kind}]")
    print(f"  backs        : {', '.join(rec['backs'])}")
    if rec["fetch"] != "ok" or rec.get("note"):
        print(f"  status       : {rec.get('note', 'SMART unreadable')}")
        if rec["fetch"] != "ok":
            print("")
            continue
    pct = rec["percentage_used"]
    print(f"  life used    : {f'{pct:.0f}%' if pct is not None else 'unavailable (drive does not report it)'}")
    print(f"  written      : {gb(rec['bytes_written'])} total"
          + (f"   (read {gb(rec['bytes_read'])})" if rec["bytes_read"] is not None else ""))
    d = rec["delta"] or {}
    if d.get("available"):
        print(f"  recent rate  : {d['gb_written']:.1f} GB over {d['span_days']:.2f} d"
              f"  →  {d['gb_per_day']:.1f} GB/day")
    else:
        print(f"  recent rate  : {d.get('reason', 'no history yet — run again later')}")
    p = rec["projection"] or {}
    if p.get("available"):
        print(f"  projection   : 100% life used in ~{p['years']:.1f} years"
              f" ({p['days']:.0f} d) — {p['method']}")
    else:
        print(f"  projection   : {p.get('reason', 'unavailable')}")
    bits = []
    if rec["media_errors"] is not None:
        bits.append(f"media errors {rec['media_errors']:.0f}")
    if rec["unsafe_shutdowns"] is not None:
        bits.append(f"unsafe shutdowns {rec['unsafe_shutdowns']:.0f}")
    if rec["power_on_hours"] is not None:
        bits.append(f"power-on {rec['power_on_hours']:.0f} h")
    if bits:
        print(f"  health       : {', '.join(bits)}")
    for c in rec["concerns"]:
        print(f"  ! {c}")
    if not rec["concerns"] and rec["status"] == "ok":
        print("  ✓ no concerns")
    print("")

for u in UNRESOLVED:
    role, _, path = u.partition(":")
    print(f"note: {role} ({path}) is not on a local block device — SMART does not apply.")

if all_concerns:
    print(f"{bold}ssd-wear: {len(all_concerns)} concern(s){off}")
else:
    print("ssd-wear: no concerns.")
print(f"history: {STATE} (run again later — the projection sharpens with every datapoint)")
PY

exit 0
