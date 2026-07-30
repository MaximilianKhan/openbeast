#!/usr/bin/env bash
# OpenBeast — per-device enrollment and revocation for beast-slot clients.
#
# Tailnet device identity is the default boundary (docs/BEAST_SLOT.md). A single
# shared LLAMA_API_KEY is the next step up — but one key for every device means
# losing a laptop costs you a stack-wide rotation. This registry gives each
# device its OWN bearer key, so a lost device is one `revoke` away from silence
# and every other device keeps working.
#
#   ./scripts/clients.sh enroll <id> [--label "text"] [--slot N] [--rate N]
#   ./scripts/clients.sh list [--json]
#   ./scripts/clients.sh show <id> [--json]
#   ./scripts/clients.sh revoke <id>
#   ./scripts/clients.sh unrevoke <id>
#   ./scripts/clients.sh rotate <id>            # = enroll <id> --force
#   ./scripts/clients.sh remove <id> --yes      # hard delete (drops the audit row)
#
# The registry lives at .run/clients.json (mode 0600, gitignored). It stores the
# SHA-256 of each key and NEVER the key itself: the plaintext is printed exactly
# once, at enroll/rotate time, and is not recoverable afterwards. The gate
# (agents/edge.py) hashes the presented bearer and matches it here, hot-reloading
# the file — revocation needs no restart.
#
# Registry schema (version 1) — the shared contract with agents/edge.py:
#   {"version":1,"devices":[{"id","label","key_sha256","enrolled_at",
#                            "revoked_at","slot","rate_limit_per_min",
#                            "last_seen"}]}
# Unknown/future fields are round-tripped untouched, so a newer edge.py can add
# fields (and keep writing last_seen) without this CLI dropping them.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
# Deliberately does NOT source lib/conf.sh. Nothing here needs a conf value,
# and sourcing it has a SIDE EFFECT: the SearXNG-secret bootstrap creates and
# appends to openbeast.conf. A read-only command like `clients.sh list` must
# not mutate the rig's config.

RUN_DIR="$REPO_DIR/.run"
REGISTRY="$RUN_DIR/clients.json"

_usage() { sed -n '10,16p' "$0" | sed 's/^# \{0,1\}//'; }
_die() { echo "ERROR: $*" >&2; exit 2; }

# 32 random bytes, hex. Same idiom as scripts/setup-mcpo-keys.sh / lib/conf.sh:
# openssl when present, /dev/urandom via od otherwise (no openssl dependency).
genkey() {
  openssl rand -hex 32 2>/dev/null \
    || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
}

# SHA-256 of stdin. Never takes the key on argv — argv is world-readable in ps.
_sha256_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  else
    python3 -c 'import sys,hashlib; sys.stdout.write(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
  fi
}

# Best-effort tailnet FQDN for the copy-paste client command. Read-only and
# fully optional — falls back to a placeholder when tailscale isn't around.
_rig_host() {
  local h=""
  h="$(tailscale status --json 2>/dev/null \
       | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null)" || h=""
  [[ -n "$h" ]] || h="<rig-fqdn>"
  printf '%s\n' "$h"
}

_no_registry() {
  echo "No devices enrolled yet." >&2
  echo "  Enroll one:  ./scripts/clients.sh enroll <id> --label \"My laptop\"" >&2
}

_valid_id() {
  local re='^[a-z0-9][a-z0-9-]{0,30}$'
  [[ "$1" =~ $re ]]
}

_int_or_die() { # _int_or_die <value> <flag> <min>
  local re='^[0-9]+$'
  [[ "$1" =~ $re ]] || _die "$2 takes a non-negative integer (got: $1)"
  [[ "$1" -ge "$3" ]] || _die "$2 must be >= $3 (got: $1)"
}

# Every read-modify-write of the registry goes through this single Python
# program (stdlib only). Bash never parses or emits JSON. The write is atomic:
# a 0600 temp file in .run/ replaced over the live file, so a concurrently
# reading edge.py sees either the old or the new document, never a truncated
# one, and the key material never lands in a world-readable temp.
_registry_op() {
  OB_REG="$REGISTRY" python3 - <<'PY'
import datetime
import json
import os
import sys
import tempfile

REG = os.environ["OB_REG"]
CMD = os.environ.get("OB_CMD", "")


def last_seen_of(dev):
    """Live last-seen, read from the gate's sidecar.

    beast-gate deliberately does NOT write clients.json (an unlocked
    read-modify-write on the data path could resurrect a revoked device, and
    rewriting it per request is needless disk churn). It records last-seen in
    .run/clients-lastseen.json instead, so this is a DISPLAY-ONLY overlay —
    without it the column reads "never" for every device forever, which is an
    affirmative false statement that feeds a revocation decision.
    """
    try:
        with open(os.path.join(os.path.dirname(REG), "clients-lastseen.json")) as fh:
            seen = json.load(fh) or {}
    except (OSError, ValueError):
        seen = {}
    return seen.get(dev.get("id")) or dev.get("last_seen")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def die(msg, code=2):
    sys.stderr.write("ERROR: %s\n" % msg)
    sys.exit(code)

def load():
    if not os.path.exists(REG):
        return {"version": 1, "devices": []}
    try:
        with open(REG, "r") as fh:
            doc = json.load(fh)
    except ValueError as exc:
        die("%s is not valid JSON (%s).\n       Refusing to touch it — fix or "
            "move it aside." % (REG, exc), 4)
    if not isinstance(doc, dict) or not isinstance(doc.get("devices"), list):
        die("%s has an unexpected shape (want {\"version\":1,\"devices\":[...]}).\n"
            "       Refusing to touch it." % REG, 4)
    doc.setdefault("version", 1)
    return doc

def save(doc):
    d = os.path.dirname(REG) or "."
    if not os.path.isdir(d):
        os.makedirs(d, 0o700)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".clients.json.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, REG)          # atomic within the filesystem
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(REG, 0o600)

def find(doc, dev_id):
    for dev in doc["devices"]:
        if isinstance(dev, dict) and dev.get("id") == dev_id:
            return dev
    return None

def need(doc, dev_id):
    dev = find(doc, dev_id)
    if dev is None:
        die("no device with id '%s' (see: ./scripts/clients.sh list)" % dev_id, 3)
    return dev

def status(dev):
    return "REVOKED" if dev.get("revoked_at") else "active"

def short(val, width):
    val = "-" if val in (None, "") else str(val)
    return val if len(val) <= width else val[: width - 1] + "…"

def stamp(val):
    if not val:
        return "never"
    val = str(val)
    return val[:16].replace("T", " ") if len(val) >= 16 else val

def redact(dev):
    """Public view of a device: hash prefix only, never the full digest."""
    out = {}
    for k, v in dev.items():
        if k == "key_sha256":
            out["key_sha256_prefix"] = (str(v)[:8] if v else None)
        else:
            out[k] = v
    out["status"] = "revoked" if dev.get("revoked_at") else "active"
    # Display-only overlay, same reason as list (see last_seen_of).
    out["last_seen"] = last_seen_of(dev)
    return out

# --- commands -----------------------------------------------------------
if CMD == "enroll":
    dev_id = os.environ["OB_ID"]
    doc = load()
    dev = find(doc, dev_id)
    force = os.environ.get("OB_FORCE") == "1"
    if dev is not None and not force:
        die("device '%s' is already enrolled.\n"
            "       Rotate its key instead:  ./scripts/clients.sh rotate %s"
            % (dev_id, dev_id), 3)
    if dev is None:
        dev = {
            "id": dev_id,
            "label": os.environ.get("OB_LABEL") or dev_id,
            "key_sha256": os.environ["OB_HASH"],
            "enrolled_at": now(),
            "revoked_at": None,
            "slot": None,
            "rate_limit_per_min": None,
            "last_seen": None,
        }
        doc["devices"].append(dev)
        action = "enrolled"
    else:
        # Rotation: new key, everything else (enrolled_at, label, slot, rate,
        # last_seen, unknown future fields) survives untouched unless the
        # caller explicitly passed a new value.
        dev["key_sha256"] = os.environ["OB_HASH"]
        action = "rotated"
    if os.environ.get("OB_HAS_LABEL") == "1":
        dev["label"] = os.environ.get("OB_LABEL") or dev_id
    if os.environ.get("OB_HAS_SLOT") == "1":
        dev["slot"] = int(os.environ["OB_SLOT"])
    if os.environ.get("OB_HAS_RATE") == "1":
        dev["rate_limit_per_min"] = int(os.environ["OB_RATE"])
    save(doc)
    print(action)
    if action == "rotated" and dev.get("revoked_at"):
        sys.stderr.write(
            "WARNING: '%s' is REVOKED — the new key stays rejected until:\n"
            "         ./scripts/clients.sh unrevoke %s\n" % (dev_id, dev_id))

elif CMD == "list":
    doc = load()
    devices = [d for d in doc["devices"] if isinstance(d, dict)]
    if os.environ.get("OB_JSON") == "1":
        print(json.dumps({"version": doc.get("version", 1),
                          "devices": [redact(d) for d in devices]}, indent=2))
        sys.exit(0)
    if not devices:
        print("No devices enrolled yet.")
        print("  Enroll one:  ./scripts/clients.sh enroll <id> --label \"My laptop\"")
        sys.exit(0)
    fmt = "%-20s  %-24s  %-4s  %-16s  %-16s  %s"
    print(fmt % ("ID", "LABEL", "SLOT", "ENROLLED", "LAST-SEEN", "STATUS"))
    print(fmt % ("-" * 20, "-" * 24, "----", "-" * 16, "-" * 16, "------"))
    for dev in devices:
        print(fmt % (short(dev.get("id"), 20), short(dev.get("label"), 24),
                     short(dev.get("slot"), 4), stamp(dev.get("enrolled_at")),
                     stamp(last_seen_of(dev)), status(dev)))
    revoked = sum(1 for d in devices if d.get("revoked_at"))
    print("")
    print("%d device(s): %d active, %d revoked."
          % (len(devices), len(devices) - revoked, revoked))

elif CMD == "show":
    doc = load()
    dev = need(doc, os.environ["OB_ID"])
    if os.environ.get("OB_JSON") == "1":
        print(json.dumps(redact(dev), indent=2))
        sys.exit(0)
    pub = redact(dev)
    prefix = pub.pop("key_sha256_prefix", None)
    order = ["id", "label", "status", "slot", "rate_limit_per_min",
             "enrolled_at", "revoked_at", "last_seen"]
    keys = [k for k in order if k in pub] + [k for k in pub if k not in order]
    for k in keys:
        val = pub[k]
        print("  %-20s %s" % (k + ":", "-" if val is None else val))
    print("  %-20s %s… (sha256; the key itself is not stored)"
          % ("key:", prefix or "?"))

elif CMD in ("revoke", "unrevoke"):
    dev_id = os.environ["OB_ID"]
    doc = load()
    dev = need(doc, dev_id)
    if CMD == "revoke":
        if dev.get("revoked_at"):
            print("'%s' was already revoked at %s — nothing to do."
                  % (dev_id, dev["revoked_at"]))
            sys.exit(0)
        dev["revoked_at"] = now()
        save(doc)
        print("Revoked '%s' at %s." % (dev_id, dev["revoked_at"]))
        print("")
        print("The device row stays in the registry for audit. Revocation takes")
        print("effect within the gate's hot-reload window — NO restart needed.")
    else:
        if not dev.get("revoked_at"):
            print("'%s' is already active — nothing to do." % dev_id)
            sys.exit(0)
        was = dev["revoked_at"]
        dev["revoked_at"] = None
        save(doc)
        print("Un-revoked '%s' (was revoked at %s). Its existing key works again."
              % (dev_id, was))

elif CMD == "remove":
    dev_id = os.environ["OB_ID"]
    doc = load()
    need(doc, dev_id)
    doc["devices"] = [d for d in doc["devices"]
                      if not (isinstance(d, dict) and d.get("id") == dev_id)]
    save(doc)
    print("Removed '%s' from the registry." % dev_id)
    print("")
    print("Its audit row is GONE — past `last_seen` and enrollment history for")
    print("this device are unrecoverable. `revoke` keeps that trail; `remove`")
    print("does not. A future enroll of the same id starts a fresh history.")

else:
    die("internal: unknown OB_CMD '%s'" % CMD)
PY
}

# ---------------------------------------------------------------------------
cmd="${1:-list}"
if [[ $# -gt 0 ]]; then shift; fi

case "$cmd" in
  enroll|rotate)
    dev_id="${1:-}"
    [[ -n "$dev_id" ]] || _die "usage: clients.sh $cmd <id> [--label \"text\"] [--slot N] [--rate N]"
    shift
    _valid_id "$dev_id" || _die "invalid id '$dev_id' — use [a-z0-9][a-z0-9-]{0,30} (e.g. laptop-air)"
    label=""; slot=""; rate=""
    has_label=0; has_slot=0; has_rate=0
    force=0
    # rotate implies --force, but ALSO requires the device to already exist:
    # rotating a mistyped id must fail loudly, not silently enroll a brand-new
    # live device while the key the operator meant to rotate stays valid.
    if [[ "$cmd" == "rotate" ]]; then
      force=1
      _exists=0
      if [[ -f "$REGISTRY" ]]; then
        _exists="$(OB_ID="$dev_id" python3 -c '
import json, os, sys
try:
    doc = json.load(open(sys.argv[1]))
except Exception:
    print(0); raise SystemExit
print(int(any(d.get("id") == os.environ["OB_ID"]
              for d in doc.get("devices", []))))' "$REGISTRY" 2>/dev/null || echo 0)"
      fi
      if [[ "$_exists" != "1" ]]; then
        echo "ERROR: no device '$dev_id' to rotate." >&2
        echo "       ./scripts/clients.sh list          # see enrolled devices" >&2
        echo "       ./scripts/clients.sh enroll $dev_id  # if you meant to add it" >&2
        exit 3
      fi
    fi
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --label)   label="${2:-}"; [[ $# -ge 2 ]] || _die "--label needs a value"; has_label=1; shift 2 ;;
        --label=*) label="${1#*=}"; has_label=1; shift ;;
        --slot)    [[ $# -ge 2 ]] || _die "--slot needs a value"; slot="$2"; has_slot=1; shift 2 ;;
        --slot=*)  slot="${1#*=}"; has_slot=1; shift ;;
        --rate)    [[ $# -ge 2 ]] || _die "--rate needs a value"; rate="$2"; has_rate=1; shift 2 ;;
        --rate=*)  rate="${1#*=}"; has_rate=1; shift ;;
        --force)   force=1; shift ;;
        *)         _die "unknown option for $cmd: $1" ;;
      esac
    done
    if [[ $has_slot -eq 1 ]]; then _int_or_die "$slot" --slot 0; fi
    if [[ $has_rate -eq 1 ]]; then _int_or_die "$rate" --rate 1; fi

    # .run/ may not exist on a stack that has never started. 0700: the registry
    # is key-adjacent material, and the temp file lives here mid-write.
    [[ -d "$RUN_DIR" ]] || (umask 077; mkdir -p "$RUN_DIR")

    key="$(genkey)"
    key_hash="$(printf '%s' "$key" | _sha256_stdin)"
    [[ ${#key} -eq 64 && ${#key_hash} -eq 64 ]] || _die "key generation failed (no openssl and no readable /dev/urandom?)"

    # The plaintext key is passed to nothing: only its hash crosses into Python.
    action="$(OB_CMD=enroll OB_ID="$dev_id" OB_LABEL="$label" OB_HASH="$key_hash" \
              OB_SLOT="$slot" OB_RATE="$rate" OB_FORCE="$force" \
              OB_HAS_LABEL="$has_label" OB_HAS_SLOT="$has_slot" OB_HAS_RATE="$has_rate" \
              _registry_op)"

    echo ""
    if [[ "$action" == "rotated" ]]; then
      echo "Rotated the key for '$dev_id'. Its PREVIOUS key stops working within"
      echo "the gate's hot-reload window."
    else
      echo "Enrolled '$dev_id'."
    fi
    echo ""
    echo "  ┌─ copy this now — it is NOT recoverable ──────────────────────────"
    echo "  │  $key"
    echo "  └──────────────────────────────────────────────────────────────────"
    echo ""
    echo "  Only its SHA-256 is stored (.run/clients.json). Nothing on this rig"
    echo "  can print it again — lose it and you rotate."
    echo ""
    echo "  On the client device, run:"
    echo "    ./scripts/setup-client.sh --host $(_rig_host) --api-key $key"
    echo ""
    ;;

  list)
    json=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --json) json=1; shift ;;
        *)      _die "unknown option for list: $1" ;;
      esac
    done
    if [[ ! -f "$REGISTRY" ]] && [[ $json -eq 0 ]]; then
      _no_registry
      exit 0
    fi
    OB_CMD=list OB_JSON="$json" _registry_op
    ;;

  show)
    dev_id="${1:-}"
    [[ -n "$dev_id" ]] || _die "usage: clients.sh show <id> [--json]"
    shift
    json=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --json) json=1; shift ;;
        *)      _die "unknown option for show: $1" ;;
      esac
    done
    if [[ ! -f "$REGISTRY" ]]; then _no_registry; exit 1; fi
    OB_CMD=show OB_ID="$dev_id" OB_JSON="$json" _registry_op
    ;;

  revoke|unrevoke)
    dev_id="${1:-}"
    [[ -n "$dev_id" ]] || _die "usage: clients.sh $cmd <id>"
    shift
    [[ $# -eq 0 ]] || _die "unknown option for $cmd: $1"
    if [[ ! -f "$REGISTRY" ]]; then _no_registry; exit 1; fi
    OB_CMD="$cmd" OB_ID="$dev_id" _registry_op
    ;;

  remove)
    dev_id="${1:-}"
    [[ -n "$dev_id" ]] || _die "usage: clients.sh remove <id> --yes"
    shift
    yes=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --yes) yes=1; shift ;;
        *)     _die "unknown option for remove: $1" ;;
      esac
    done
    if [[ ! -f "$REGISTRY" ]]; then _no_registry; exit 1; fi
    if [[ $yes -ne 1 ]]; then
      echo "Refusing: 'remove' hard-deletes '$dev_id' and DESTROYS its audit trail" >&2
      echo "(enrollment time, last-seen, the hash that ties past requests to it)." >&2
      echo "" >&2
      echo "  Cut the device off and keep the history:  ./scripts/clients.sh revoke $dev_id" >&2
      echo "  Really delete the row:                    ./scripts/clients.sh remove $dev_id --yes" >&2
      exit 2
    fi
    OB_CMD=remove OB_ID="$dev_id" _registry_op
    ;;

  -h|--help|help) _usage ;;
  *) echo "Unknown command: $cmd" >&2; echo "" >&2; _usage >&2; exit 2 ;;
esac
