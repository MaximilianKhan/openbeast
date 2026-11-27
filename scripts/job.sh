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
#   * the job gets its OWN process group, so `stop` signals the supervisor and
#     every child that has NOT detached into a session of its own — a campaign
#     script's stages included — without ever touching the caller.
#     NOT "the whole tree": a descendant spawned with setsid/start_new_session
#     leaves this group by design (evals/run_eval.py does exactly that, so a
#     per-task timeout can SIGKILL one agent's group without touching the run).
#     Such a descendant has to reap itself on SIGTERM, and run_eval now does
#     (_install_signal_reaper) — it used to leave an eval unit running against
#     a relaunch. If you add another self-sessioning spawner, give it the same
#     handler; `stop` cannot reach it for you.
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
    # OB_OVERRIDE_LOST: `stop` sets it. finalize() is a compare-and-set that
    # refuses a record already terminal, and `lost` IS terminal — so both of
    # stop's "the operator did this, it is not a crash" corrections were
    # no-ops that still printed "is now 'stopped'" over a ledger saying `lost`.
    sessions.finalize(session_id, state, summary=summary or None,
                      override_lost=os.environ.get("OB_OVERRIDE_LOST") == "1")

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
    command = meta.get("command")
    if command:
        # A list from `job.sh run`; a string from a console-started job
        # (chat_server records the shell command as typed). " ".join() over a
        # string spaced out every character.
        print("  %-12s %s" % ("command:", command if isinstance(command, str)
                              else " ".join(str(c) for c in command)))
    log = record.get("transcript")
    if log and os.path.isfile(log):
        print("")
        print("  --- last 20 lines of %s ---" % log)
        # Bounded: seek near EOF, never read every line of a multi-day
        # campaign log (several GB of list for 20 lines). 64 KiB holds the
        # last 20 lines of anything readable; a longer line is clipped.
        with open(log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            fh.seek(max(0, end - 65536))
            tail = fh.read(65536).decode("utf-8", "replace").splitlines()
        if end > 65536 and tail:
            tail = tail[1:]                 # the first line is a fragment
        for line in tail[-20:]:
            print("  " + line)

elif CMD == "stopinfo":
    record = need(ARGV[0])
    # One line the shell can read with `read -r`: state, pid, pgid, proven.
    # `proven` is 1 only when the recorded pid is STILL the process that
    # registered (pid AND start time, same boot) — the signalling contract
    # chat_server holds to. A record with no start time on file used to be
    # signalled on bare-pid evidence here, i.e. whoever holds that pid now.
    proven = 1 if sessions.is_alive(record, require_start=True) else 0
    # `leads` is 1 only when the session LEADS the recorded group (pgid ==
    # pid) and the live process is still in it — the rule chat_server's
    # _signal_session_group holds to. Membership is not enough: a runner
    # registered with no pgid gets os.getpgid(pid), i.e. the group of the
    # non-interactive script that exec'd it, and signalling THAT group took
    # the whole campaign and every sibling stage down with one agent.
    leads = 0
    try:
        pid_i, pgid_i = int(record.get("pid") or 0), int(record.get("pgid") or 0)
        if pid_i > 1 and pid_i == pgid_i and os.getpgid(pid_i) == pgid_i:
            leads = 1
    except (OSError, TypeError, ValueError):
        leads = 0
    print("%s %s %s %d %d" % (record.get("state") or "unknown",
                              record.get("pid") or 0, record.get("pgid") or 0,
                              proven, leads))

elif CMD == "rawstate":
    # The state EXACTLY as it sits on disk, with NO reconciliation.
    # Deliberately not sessions.get(): that accessor reconciles first and
    # PERSISTS `lost` for a session whose process is gone, so a caller that
    # has just killed the process can never read anything else back. That is
    # what made `stop`'s operator-stop finalize dead code — an operator stop
    # was recorded as a crash. Unreadable/missing record -> "unknown".
    try:
        with open(sessions.record_path(ARGV[0]), "r") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raw = {}
    print((raw.get("state") if isinstance(raw, dict) else None) or "unknown")

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

# Direct liveness probe: true when <pid> exists AND is not a zombie. `ps` is
# what makes this portable (the ledger reads /proc; job.sh also runs on the
# macOS client box). A reaped-but-unwaited child still owns its pid, and
# treating that corpse as alive would suppress the terminal state a caller
# needs to write.
_pid_alive() { # _pid_alive <pid>
  local st re='^[0-9]+$'
  [[ "${1:-}" =~ $re ]] || return 1
  [[ "$1" -gt 1 ]] || return 1
  st="$(ps -o stat= -p "$1" 2>/dev/null | tr -d ' ')" || return 1
  [[ -n "$st" ]] || return 1
  case "$st" in Z*) return 1 ;; esac
  return 0
}

# Sets GROUP_OTHERS to the live, non-zombie members of process group <pgid>
# other than this shell, space-separated. A GLOBAL, not stdout: called as
# `x="$(_group_others ...)"` the function would run in a subshell that is
# itself a live member of the group -- and would list itself, forever. The ps
# snapshot's own processes (ps, its subshell) are gone, and fail kill -0,
# by the time the snapshot is walked. (No $BASHPID: bash 3.2.)
GROUP_OTHERS=""
_group_others() { # _group_others <pgid>
  local snap gpid gpg gst
  GROUP_OTHERS=""
  snap="$(ps -A -o pid= -o pgid= -o stat= 2>/dev/null)" || return 0
  while read -r gpid gpg gst; do
    [[ "$gpg" == "$1" && "$gpid" != "$$" ]] || continue
    case "$gst" in Z*) continue ;; esac
    if kill -0 "$gpid" 2>/dev/null; then
      GROUP_OTHERS="${GROUP_OTHERS:+$GROUP_OTHERS }$gpid"
    fi
  done <<EOS
$snap
EOS
  return 0
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

  # Lets whatever the job runs say which session it belongs to — e.g. an
  # artifact published from inside a job can link back to it.
  export OPENBEAST_SESSION_ID="$session_id"
  cd "$workdir" || { OB_CMD=finalize OB_OVERRIDE_LOST=1 _ledger_op "$session_id" failed "workdir gone: $workdir"; exit 1; }
  "$@" >> "$log" 2>&1 &
  child=$!
  set +e
  wait "$child"
  rc=$?
  set -e

  # Every finalize below passes OB_OVERRIDE_LOST=1: this supervisor HOLDS the
  # child and read its exit status, so it is the one writer that knows better
  # than the reconciler. Its verdict must replace a `lost` guess a reader
  # persisted meanwhile (a failed liveness probe on a busy macOS client)
  # instead of being refused by finalize()'s first-verdict-wins rule.
  if [[ $stopped -eq 1 ]]; then
    # Signalled. Give the child (and its own children — they share our group)
    # $grace s to unwind, then SIGKILL just the child; a group-wide SIGKILL
    # here would kill this process before it could write the terminal state.
    local grace="${OPENBEAST_JOB_STOP_GRACE_S:-30}" others
    [[ "$grace" =~ ^[0-9]+$ ]] || grace=30
    waited=0
    while kill -0 "$child" 2>/dev/null && [[ $waited -lt $grace ]]; do
      sleep 1
      waited=$((waited + 1))
    done
    if kill -0 "$child" 2>/dev/null; then
      kill -KILL "$child" 2>/dev/null || true
    fi
    set +e
    wait "$child" 2>/dev/null
    set -e
    # The child being gone is not the JOB being gone. A wrapper shell that
    # dies on SIGTERM can leave a worker in our group that traps or ignores
    # it (a server, a trainer with a graceful-shutdown handler), and once
    # `stopped` is written neither `job.sh stop` nor the console escalates
    # any more — the worker kept the GPU while the ledger said stopped. So
    # drain the GROUP on the same deadline, member by member (never -$$,
    # which is us), before writing the verdict. Only when we lead the group:
    # otherwise its other members are not ours to touch.
    if [[ "$pgid" == "$$" ]]; then
      _group_others "$pgid"; others="$GROUP_OTHERS"
      # TERM again: a pid-only stop (the group was never signalled) must
      # still give the rest of the job a polite chance first.
      [[ -n "$others" ]] && { kill -TERM $others 2>/dev/null || true; }
      while [[ -n "$others" && $waited -lt $grace ]]; do
        sleep 1
        waited=$((waited + 1))
        _group_others "$pgid"; others="$GROUP_OTHERS"
      done
      [[ -n "$others" ]] && { kill -KILL $others 2>/dev/null || true; }
    fi
    OB_CMD=finalize OB_OVERRIDE_LOST=1 _ledger_op "$session_id" stopped "stopped by operator"
    echo "--- job $session_id STOPPED $(date '+%Y-%m-%d %H:%M:%S') ---" >> "$log"
    exit 143
  fi

  if [[ $rc -eq 0 ]]; then
    OB_CMD=finalize OB_OVERRIDE_LOST=1 _ledger_op "$session_id" done "exit 0"
    echo "--- job $session_id DONE (exit 0) $(date '+%Y-%m-%d %H:%M:%S') ---" >> "$log"
  else
    # 137 = SIGKILL that nobody here sent. Under chat_server's memory-capped
    # scope (OOMPolicy=continue) that is almost always the kernel OOM killer,
    # so say so: "exit 137" alone reads like an operator action.
    local why="exit $rc"
    if [[ $rc -eq 137 ]]; then
      why="exit 137 (SIGKILL: out of memory?)"
    fi
    OB_CMD=finalize OB_OVERRIDE_LOST=1 _ledger_op "$session_id" failed "$why"
    echo "--- job $session_id FAILED ($why) $(date '+%Y-%m-%d %H:%M:%S') ---" >> "$log"
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
    proven="$(printf '%s\n' "$info" | awk '{print $4}')"
    leads="$(printf '%s\n' "$info" | awk '{print $5}')"

    if [[ "$state" != "running" ]]; then
      echo "Job '$session_id' is already '$state' — nothing to signal."
      exit 0
    fi
    if [[ "$proven" != "1" ]]; then
      # A pid is a reusable integer. Without the start time captured at
      # register() there is no proof PID $pid is still this job, and the
      # group kill below would land on whoever holds it now.
      echo "ERROR: cannot prove PID $pid is still job '$session_id' (no process" >&2
      echo "       start time on record, or the probe failed) — refusing to signal." >&2
      echo "       Check it by hand: ps -o pid,lstart,args -p $pid" >&2
      exit 1
    fi

    # Signal the GROUP (kill -- -PGID) so a campaign script's children die
    # with it. Guard hard: pgid 0/1, and our own group, are never valid
    # targets — signalling our own group would kill this CLI and its parent
    # shell, which is exactly the accident this check exists to prevent.
    # And only a group the session LEADS (stopinfo's `leads`): a session that
    # merely belongs to someone else's group — an agent exec'd by a campaign
    # script — gets its pid signalled, never its host's whole group.
    # target=0 on every refusal, so the SIGKILL escalation below never
    # reaches a group the TERM was not allowed to (our own included).
    target="$pgid"
    [[ "$target" =~ ^[0-9]+$ ]] || target=0
    why_pid_only="No usable process group recorded"
    if [[ "$target" -gt 1 && "$leads" != "1" ]]; then
      why_pid_only="Session '$session_id' does not lead process group $target"
      target=0
    fi
    if [[ "$target" -le 1 || "$target" == "$(_pgid_of $$)" ]]; then
      target=0
      if [[ "$pid" =~ ^[0-9]+$ ]] && [[ "$pid" -gt 1 ]]; then
        echo "$why_pid_only — signalling PID $pid only." >&2
        kill -TERM "$pid" 2>/dev/null || true
      else
        _die "job '$session_id' has no signalable pid/pgid on record"
      fi
    else
      kill -TERM -- "-$target" 2>/dev/null || true
      echo "Sent SIGTERM to process group $target."
    fi

    # Wait for the supervisor to write its OWN terminal state — the polite
    # path, and the only one that can report the job's real exit. This read
    # goes through the reconciling accessor on purpose: it is also how a
    # supervisor that died under our signal without writing shows up (as
    # `lost`), which the branches below correct.
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
      # write it here — an operator stop is not a crash.
      #
      # Liveness is probed DIRECTLY (`_pid_alive`, i.e. `ps`), and the record
      # is read RAW. Reading it back through `stopinfo` was the bug: that
      # accessor reconciles first and PERSISTS `lost` for exactly the process
      # we have just killed, so the guard below could never see anything but
      # `lost` and this finalize was dead code. The job then ended as `lost`,
      # "process gone without a terminal event", when an operator had
      # deliberately stopped it.
      state="$(OB_CMD=rawstate _ledger_op "$session_id")"
      if _pid_alive "$pid"; then
        echo "WARNING: PID $pid survived SIGKILL (uninterruptible sleep?) —" >&2
        echo "         leaving the record at '$state'; check it by hand." >&2
      else
        case "$state" in
          running|lost|unknown)
            OB_CMD=finalize OB_OVERRIDE_LOST=1 _ledger_op "$session_id" stopped "force-killed by operator"
            state="stopped"
            ;;
        esac
      fi
    elif [[ "$state" == "lost" ]]; then
      # SIGTERM reached the group and the supervisor went away without a
      # terminal event. Same correction, same reason: the operator did this.
      OB_CMD=finalize OB_OVERRIDE_LOST=1 _ledger_op "$session_id" stopped "stopped by operator"
      state="stopped"
    fi
    echo "Job '$session_id' is now '$state'."
    ;;

  -h|--help|help) _usage ;;
  *) echo "Unknown command: $cmd" >&2; echo "" >&2; _usage >&2; exit 2 ;;
esac
