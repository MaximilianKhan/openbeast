#!/usr/bin/env bash
# OpenBeast — register any long-running command as a beast-chat JOB session.
#
#   ./scripts/job.sh run --title "T1.17 campaign" -- bash scratch/campaign_master.sh
#   ./scripts/job.sh list [--state running] [--json]
#   ./scripts/job.sh show <id> [--json]
#   ./scripts/job.sh stop <id> [--timeout N]
#
# WHY this exists: beast-chat can already show you every AGENT on the rig,
# because an agent is a runner.py process that writes its own session record.
# The things Max most wants to watch from a phone are not agents — they are
# plain bash: campaign orchestrators, quantization runs, overnight sweeps.
# This wrapper gives one of those the same session identity an agent has, so
# it appears in the console, tails live, and stops cleanly. See
# docs/BEAST_CHAT.md.
#
# What `run` guarantees:
#   * the job survives this shell exiting (nohup-style, stdin from /dev/null)
#   * the job gets its OWN process group, so `stop` can signal the whole tree
#     — a campaign script's children included — without touching the caller
#   * both pid and pgid land in the ledger record
#   * combined stdout+stderr stream to .run/sessions/<id>.log
#   * the terminal state is the truth: exit 0 -> done, anything else -> failed,
#     SIGTERM -> stopped
#
# Deliberately does NOT source lib/conf.sh. Nothing here needs a conf value,
# and sourcing it has a SIDE EFFECT: the SearXNG-secret bootstrap creates and
# appends to openbeast.conf. A read-only command like `job.sh list` must not
# mutate the rig's config. (Same reasoning as scripts/clients.sh.)
#
# Bash 3.2-compatible (no mapfile/readarray/associative arrays) — the same
# floor the rest of the client-facing CLI holds to.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SELF="$SCRIPT_DIR/job.sh"

_usage() { sed -n '3,8p' "$0" | sed 's/^# \{0,1\}//'; }
_die() { echo "ERROR: $*" >&2; exit 2; }

# Every ledger read/write goes through this one Python program (stdlib +
# agents/sessions.py only). Bash never parses or emits a session record —
# same discipline as clients.sh's _registry_op. Arguments arrive as sys.argv.
_ledger_op() {
  OB_REPO="$REPO_DIR" python3 - "$@" <<'PY'
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.environ["OB_REPO"], "agents"))
try:
    import sessions
except Exception as exc:                                  # pragma: no cover
    sys.stderr.write(
        "ERROR: the session ledger is unavailable (%s).\n"
        "       agents/sessions.py is what makes a job visible to beast-chat;\n"
        "       without it there is nothing to register a job against.\n" % exc)
    sys.exit(4)

CMD = os.environ.get("OB_CMD", "")
ARGV = sys.argv[1:]


def die(msg, code=2):
    sys.stderr.write("ERROR: %s\n" % msg)
    sys.exit(code)


def age_of(record):
    """Human elapsed time since the session started ('-' if unparseable)."""
    raw = record.get("started_at")
    if not isinstance(raw, str) or not raw:
        return "-"
    try:
        started = datetime.datetime.fromisoformat(raw.rstrip("Z"))
    except ValueError:
        return "-"
    now = datetime.datetime.now(started.tzinfo) if started.tzinfo else datetime.datetime.now()
    seconds = int((now - started).total_seconds())
    if seconds < 0:
        return "0s"
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm %ds" % divmod(seconds, 60)
    hours, rest = divmod(seconds, 3600)
    return "%dh %dm" % (hours, rest // 60)


def short(val, width):
    val = "-" if val in (None, "") else str(val)
    return val if len(val) <= width else val[: width - 1] + "…"


def need(session_id):
    record = sessions.get(session_id)
    if record is None:
        die("no session with id '%s' (see: ./scripts/job.sh list)" % session_id, 3)
    return record


# --- commands -----------------------------------------------------------
if CMD == "new":
    # Two lines: the fresh id, then where records and logs live. One python
    # start instead of two, on the one path where latency is user-visible.
    print(sessions.new_id("job"))
    print(sessions.SESSIONS_DIR)

elif CMD == "register":
    session_id, title, pid, pgid, workdir, log = ARGV[:6]
    command = ARGV[6:]
    sessions.register(
        session_id,
        kind="job",
        title=title or (command[0] if command else session_id),
        pid=int(pid),
        pgid=int(pgid) if pgid and pgid != "0" else None,
        workdir=workdir or None,
        transcript=log or None,
        meta={"command": command, "wrapper": "scripts/job.sh"},
    )

elif CMD == "finalize":
    session_id, state, summary = ARGV[0], ARGV[1], (ARGV[2] if len(ARGV) > 2 else "")
    sessions.finalize(session_id, state, summary=summary or None)

elif CMD == "list":
    state = ARGV[0] if ARGV and ARGV[0] else None
    records = sessions.list_sessions(kind="job", state=state)
    if os.environ.get("OB_JSON") == "1":
        print(json.dumps({"sessions": records}, indent=2))
        sys.exit(0)
    if not records:
        print("No jobs registered%s." % (" in state '%s'" % state if state else ""))
        print("  Register one:  ./scripts/job.sh run --title \"my campaign\" -- bash script.sh")
        sys.exit(0)
    fmt = "%-28s  %-8s  %-9s  %s"
    print(fmt % ("ID", "STATE", "AGE", "TITLE"))
    print(fmt % ("-" * 28, "-" * 8, "-" * 9, "-" * 30))
    for record in records:
        print(fmt % (short(record.get("id"), 28), short(record.get("state"), 8),
                     age_of(record), short(record.get("title"), 46)))
    running = sum(1 for r in records if r.get("state") == "running")
    print("")
    print("%d job(s): %d running." % (len(records), running))

elif CMD == "show":
    record = need(ARGV[0])
    if os.environ.get("OB_JSON") == "1":
        print(json.dumps(record, indent=2))
        sys.exit(0)
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    order = ["id", "kind", "title", "state", "pid", "pgid", "workdir",
             "started_at", "updated_at", "summary", "transcript"]
    for key in order:
        if key in record and record[key] not in (None, ""):
            print("  %-12s %s" % (key + ":", record[key]))
    print("  %-12s %s" % ("age:", age_of(record)))
    if meta.get("command"):
        print("  %-12s %s" % ("command:", " ".join(meta["command"])))
    log = record.get("transcript")
    if log and os.path.exists(log):
        print("")
        print("  --- last 20 lines of %s ---" % log)
        with open(log, "r", errors="replace") as fh:
            for line in fh.readlines()[-20:]:
                print("  " + line.rstrip("\n"))

elif CMD == "stopinfo":
    record = need(ARGV[0])
    # One line the shell can read with `read -r`: state, pid, pgid.
    print("%s %s %s" % (record.get("state") or "unknown",
                        record.get("pid") or 0, record.get("pgid") or 0))

else:
    die("internal: unknown OB_CMD '%s'" % CMD)
PY
}

# Process-group id of a live pid ("0" when it cannot be read — never signal
# a group we could not positively identify).
_pgid_of() {
  local out
  out="$(ps -o pgid= -p "$1" 2>/dev/null | tr -d ' ')" || out=""
  [[ "$out" =~ ^[0-9]+$ ]] || out="0"
  printf '%s\n' "$out"
}

_int_or_die() { # _int_or_die <value> <flag> <min>
  local re='^[0-9]+$'
  [[ "$1" =~ $re ]] || _die "$2 takes a non-negative integer (got: $1)"
  [[ "$1" -ge "$3" ]] || _die "$2 must be >= $3 (got: $1)"
}

# ---------------------------------------------------------------------------
# INTERNAL: the supervisor. `run` launches exactly this, in its own process
# group, and returns. It is the process the ledger records, so killing its
# group kills the job — and it is still alive when the job ends, which is how
# a terminal state gets written at all.
# ---------------------------------------------------------------------------
_supervise() {
  local session_id="$1" log="$2" title="$3" workdir="$4"
  shift 4
  [[ "${1:-}" == "--" ]] && shift

  local pgid stopped=0 child rc waited
  pgid="$(_pgid_of $$)"
  OB_CMD=register _ledger_op "$session_id" "$title" "$$" "$pgid" "$workdir" "$log" "$@"

  {
    echo "--- job $session_id started $(date '+%Y-%m-%d %H:%M:%S') ---"
    echo "--- workdir: $workdir"
    echo "--- command: $*"
    echo "---"
  } >> "$log"

  # The trap must be able to run, so the job runs in the BACKGROUND and we
  # wait on it: bash does not deliver a trap while a foreground child is
  # running, which would delay the ledger write until after the job died.
  trap 'stopped=1; [[ -n "${child:-}" ]] && kill -TERM "$child" 2>/dev/null || true' TERM INT HUP

  cd "$workdir" || { OB_CMD=finalize _ledger_op "$session_id" failed "workdir gone: $workdir"; exit 1; }
  "$@" >> "$log" 2>&1 &
  child=$!
  set +e
  wait "$child"
  rc=$?
  set -e

  if [[ $stopped -eq 1 ]]; then
    # Signalled. Give the child (and its own children — they share our group)
    # 30s to unwind, then SIGKILL just the child; the group-wide SIGKILL is
    # `job.sh stop`'s escalation, and doing it here would kill this process
    # before it could write the terminal state.
    waited=0
    while kill -0 "$child" 2>/dev/null && [[ $waited -lt 30 ]]; do
      sleep 1
      waited=$((waited + 1))
    done
    if kill -0 "$child" 2>/dev/null; then
      kill -KILL "$child" 2>/dev/null || true
    fi
    set +e
    wait "$child" 2>/dev/null
    set -e
    OB_CMD=finalize _ledger_op "$session_id" stopped "stopped by operator"
    echo "--- job $session_id STOPPED $(date '+%Y-%m-%d %H:%M:%S') ---" >> "$log"
    exit 143
  fi

  if [[ $rc -eq 0 ]]; then
    OB_CMD=finalize _ledger_op "$session_id" done "exit 0"
    echo "--- job $session_id DONE (exit 0) $(date '+%Y-%m-%d %H:%M:%S') ---" >> "$log"
  else
    OB_CMD=finalize _ledger_op "$session_id" failed "exit $rc"
    echo "--- job $session_id FAILED (exit $rc) $(date '+%Y-%m-%d %H:%M:%S') ---" >> "$log"
  fi
  exit "$rc"
}

# ---------------------------------------------------------------------------
cmd="${1:-list}"
if [[ $# -gt 0 ]]; then shift; fi

case "$cmd" in
  __supervise)
    _supervise "$@"
    ;;

  run)
    title=""; workdir="$PWD"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --title)    [[ $# -ge 2 ]] || _die "--title needs a value"; title="$2"; shift 2 ;;
        --title=*)  title="${1#*=}"; shift ;;
        --workdir)  [[ $# -ge 2 ]] || _die "--workdir needs a value"; workdir="$2"; shift 2 ;;
        --workdir=*) workdir="${1#*=}"; shift ;;
        --)         shift; break ;;
        -*)         _die "unknown option for run: $1" ;;
        *)          _die "run takes flags then '--' then the command (got: $1)" ;;
      esac
    done
    [[ $# -gt 0 ]] || _die "usage: job.sh run [--title \"text\"] [--workdir DIR] -- <command> [args...]"
    workdir="$(cd "$workdir" 2>/dev/null && pwd)" || _die "workdir does not exist: $workdir"
    [[ -n "$title" ]] || title="$1"

    _new="$(OB_CMD=new _ledger_op)"
    session_id="$(printf '%s\n' "$_new" | sed -n 1p)"
    sessions_dir="$(printf '%s\n' "$_new" | sed -n 2p)"
    [[ -n "$session_id" && -n "$sessions_dir" ]] || _die "could not allocate a session id"

    # 0700 / 0600: a job's log is whatever the job printed — credentials in a
    # build's environment dump, a stack trace with a token. Same posture as
    # the session records themselves.
    [[ -d "$sessions_dir" ]] || (umask 077; mkdir -p "$sessions_dir")
    log="$sessions_dir/$session_id.log"
    (umask 077; : >> "$log")
    chmod 600 "$log" 2>/dev/null || true

    # `set -m` is what buys the job its own process group: with job control
    # on, bash makes each background job a process-group LEADER, so $! is
    # also the pgid. (setsid would fork and hand us back a pid that is
    # already gone — a trap this repo has been bitten by before.) nohup +
    # </dev/null is what lets it outlive this shell and any terminal.
    set -m
    nohup "$SELF" __supervise "$session_id" "$log" "$title" "$workdir" -- "$@" \
      </dev/null >/dev/null 2>&1 &
    sup_pid=$!
    set +m

    # The supervisor writes the record; wait for it so `run` never reports a
    # job the console cannot find yet.
    registered=0
    for _i in 1 2 3 4 5 6 7 8 9 10; do
      if [[ -f "$sessions_dir/$session_id.json" ]]; then registered=1; break; fi
      kill -0 "$sup_pid" 2>/dev/null || break
      sleep 0.5
    done
    sup_pgid="$(_pgid_of "$sup_pid")"

    echo ""
    if [[ $registered -eq 1 ]]; then
      echo "Job started."
    else
      echo "Job launched, but it has not registered yet — check the log below."
    fi
    echo "  ID:       $session_id"
    echo "  Title:    $title"
    echo "  PID:      $sup_pid (process group $sup_pgid)"
    echo "  Workdir:  $workdir"
    echo "  Log:      $log"
    echo ""
    echo "  Watch:    ./scripts/job.sh show $session_id"
    echo "            tail -f $log"
    echo "  Stop:     ./scripts/job.sh stop $session_id"
    echo "  Phone:    it is now a session in beast-chat (docs/BEAST_CHAT.md)"
    echo ""
    ;;

  list)
    json=0; state=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --json)   json=1; shift ;;
        --state)  [[ $# -ge 2 ]] || _die "--state needs a value"; state="$2"; shift 2 ;;
        --state=*) state="${1#*=}"; shift ;;
        *)        _die "unknown option for list: $1" ;;
      esac
    done
    OB_CMD=list OB_JSON="$json" _ledger_op "$state"
    ;;

  show)
    session_id="${1:-}"
    [[ -n "$session_id" ]] || _die "usage: job.sh show <id> [--json]"
    shift
    json=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --json) json=1; shift ;;
        *)      _die "unknown option for show: $1" ;;
      esac
    done
    OB_CMD=show OB_JSON="$json" _ledger_op "$session_id"
    ;;

  stop)
    session_id="${1:-}"
    [[ -n "$session_id" ]] || _die "usage: job.sh stop <id> [--timeout N]"
    shift
    timeout=30
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --timeout)   [[ $# -ge 2 ]] || _die "--timeout needs a value"; timeout="$2"; shift 2 ;;
        --timeout=*) timeout="${1#*=}"; shift ;;
        *)           _die "unknown option for stop: $1" ;;
      esac
    done
    _int_or_die "$timeout" --timeout 1

    info="$(OB_CMD=stopinfo _ledger_op "$session_id")"
    state="$(printf '%s\n' "$info" | awk '{print $1}')"
    pid="$(printf '%s\n' "$info" | awk '{print $2}')"
    pgid="$(printf '%s\n' "$info" | awk '{print $3}')"

    if [[ "$state" != "running" ]]; then
      echo "Job '$session_id' is already '$state' — nothing to signal."
      exit 0
    fi

    # Signal the GROUP (kill -- -PGID) so a campaign script's children die
    # with it. Guard hard: pgid 0/1, and our own group, are never valid
    # targets — signalling our own group would kill this CLI and its parent
    # shell, which is exactly the accident this check exists to prevent.
    target="$pgid"
    [[ "$target" =~ ^[0-9]+$ ]] || target=0
    if [[ "$target" -le 1 || "$target" == "$(_pgid_of $$)" ]]; then
      if [[ "$pid" =~ ^[0-9]+$ ]] && [[ "$pid" -gt 1 ]]; then
        echo "No usable process group recorded — signalling PID $pid only." >&2
        kill -TERM "$pid" 2>/dev/null || true
      else
        _die "job '$session_id' has no signalable pid/pgid on record"
      fi
    else
      kill -TERM -- "-$target" 2>/dev/null || true
      echo "Sent SIGTERM to process group $target."
    fi

    waited=0
    while [[ $waited -lt $timeout ]]; do
      info="$(OB_CMD=stopinfo _ledger_op "$session_id")"
      state="$(printf '%s\n' "$info" | awk '{print $1}')"
      [[ "$state" == "running" ]] || break
      sleep 1
      waited=$((waited + 1))
    done

    if [[ "$state" == "running" ]]; then
      echo "Still running after ${timeout}s — escalating to SIGKILL."
      if [[ "$target" -gt 1 ]]; then
        kill -KILL -- "-$target" 2>/dev/null || true
      elif [[ "$pid" =~ ^[0-9]+$ ]] && [[ "$pid" -gt 1 ]]; then
        kill -KILL "$pid" 2>/dev/null || true
      fi
      sleep 1
      # A SIGKILLed supervisor never got to write its own terminal state, so
      # write it here. (reconcile() would eventually call it 'lost', which is
      # for crashes — an operator stop is not a crash.)
      info="$(OB_CMD=stopinfo _ledger_op "$session_id")"
      state="$(printf '%s\n' "$info" | awk '{print $1}')"
      if [[ "$state" == "running" ]]; then
        OB_CMD=finalize _ledger_op "$session_id" stopped "force-killed by operator"
        state="stopped"
      fi
    fi
    echo "Job '$session_id' is now '$state'."
    ;;

  -h|--help|help) _usage ;;
  *) echo "Unknown command: $cmd" >&2; echo "" >&2; _usage >&2; exit 2 ;;
esac
