#!/usr/bin/env bash
# See, and safely clear, this device's opencode sessions. Same on the rig
# (Omarchy/Linux) and on a Mac client: opencode keeps everything under the XDG
# base directories on every OS (no ~/Library special case), so the database is
# ${XDG_DATA_HOME:-~/.local/share}/opencode/opencode.db unless OPENCODE_DB says
# otherwise — this script asks opencode itself (`opencode db path`).
#
#   ./scripts/opencode-sessions.sh                   # summary (read-only)
#   ./scripts/opencode-sessions.sh clear             # DRY RUN: what would go
#   ./scripts/opencode-sessions.sh clear --go        # delete every session
#   ./scripts/opencode-sessions.sh clear --go --dir ~/code/app   # one project only
#
# Options for `clear`:
#   --go          actually delete (without it nothing is changed)
#   --dir PATH    only sessions opened in PATH or below it
#   --history     also clear prompt history, session diffs, tool output and
#                 the undo snapshots (whole-device clears only)
#   --no-backup   skip the backup (default: a consistent copy of the database
#                 is written next to it first, mode 600)
#
# SAFE BY CONSTRUCTION:
#   - Deletes through `opencode session delete`, never raw SQL: opencode's own
#     cascade removes the session, its sub-agent sessions, messages, parts,
#     todos and event log (verified on opencode 1.18 against a copy of a real
#     database: zero orphans afterwards).
#   - Refuses while any opencode process is running (a live TUI/server holds
#     the database open and would write back into it).
#   - Backs up first with `VACUUM INTO` (a consistent, compacted copy).
#   - Leaves providers/credentials, config, projects and logs alone.
#   - Reclaims the space afterwards (VACUUM + a truncating WAL checkpoint; in
#     WAL mode a VACUUM alone lands in the -wal file and frees nothing).
# Note: even read-only opencode commands (list, db) touch the database's
# mtime — that is opencode, not a write by this script.
#
# Bash 3.2-compatible on purpose (stock macOS): no mapfile/readarray/declare -A.
set -euo pipefail

die()  { echo "error: $*" >&2; exit 1; }
note() { echo "  $*"; }

OC="${OPENCODE_BIN:-}"
if [[ -z "$OC" ]]; then
  OC="$(command -v opencode 2>/dev/null || true)"
  [[ -z "$OC" && -x "$HOME/.opencode/bin/opencode" ]] && OC="$HOME/.opencode/bin/opencode"
fi
[[ -n "$OC" ]] || die "opencode not found (PATH or ~/.opencode/bin) — nothing to clear"

q() {  # q <sql> — one query through opencode, header row dropped
  "$OC" db --format tsv "$1" 2>/dev/null | tail -n +2
}
sq() { printf "%s" "$1" | sed "s/'/''/g"; }   # SQL string-literal escape
bytes() { if [[ -e "$1" ]]; then wc -c < "$1" | tr -d ' '; else echo 0; fi; }
human() {
  awk -v b="$1" 'BEGIN { s="B KB MB GB TB"; split(s,u," "); i=1
    while (b >= 1024 && i < 5) { b /= 1024; i++ } printf (i==1 ? "%d %s" : "%.1f %s"), b, u[i] }'
}
dirsize() { if [[ -d "$1" ]]; then du -sh "$1" 2>/dev/null | cut -f1; else echo "-"; fi; }

DB="$("$OC" db path 2>/dev/null || true)"
[[ -n "$DB" ]] || die "opencode did not report a database path (\`opencode db path\`)"
DATA_DIR="$(dirname "$DB")"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/opencode"
dbsize() { echo $(( $(bytes "$DB") + $(bytes "$DB-wal") )); }

summary() {
  echo "opencode sessions on $(hostname 2>/dev/null || echo this device)"
  note "database:   $DB ($(human "$(dbsize)"))"
  if [[ ! -f "$DB" ]]; then note "no database yet — no sessions"; return 0; fi
  note "sessions:   $(q 'select count(*) from session where parent_id is null') top-level, $(q 'select count(*) from session where parent_id is not null') sub-agent"
  note "by directory:"
  q "select count(*), directory from session where parent_id is null group by directory order by 1 desc" \
    | while IFS=$'\t' read -r n d; do printf '    %5s  %s\n' "$n" "$d"; done
  note "related:    snapshots $(dirsize "$DATA_DIR/snapshot"), session diffs $(dirsize "$DATA_DIR/storage/session_diff"), tool output $(dirsize "$DATA_DIR/tool-output"), prompt history $(human "$(bytes "$STATE_DIR/prompt-history.jsonl")")"
  echo "Clear with: $0 clear   (dry run; add --go to delete)"
}

clear_sessions() {
  local go=0 dir="" history=0 backup=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --go) go=1 ;;
      --dir) [[ $# -ge 2 ]] || die "--dir needs a path"; dir="$2"; shift ;;
      --dir=*) dir="${1#--dir=}" ;;
      --history) history=1 ;;
      --no-backup) backup=0 ;;
      -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
      *) die "unknown option: $1 (see --help)" ;;
    esac
    shift
  done
  [[ -f "$DB" ]] || { echo "No opencode database at $DB — nothing to clear."; return 0; }
  local where="parent_id is null"
  if [[ -n "$dir" ]]; then
    [[ $history -eq 0 ]] || die "--history clears device-wide files; it cannot be combined with --dir"
    local dl dp
    dl="$(cd "$dir" 2>/dev/null && pwd)" || die "--dir: no such directory: $dir"
    dp="$(cd "$dir" && pwd -P)"
    # Exact prefix match, not LIKE (`_` and `%` in a path are LIKE wildcards).
    # Both spellings: opencode records the cwd it was started in, which may be
    # the logical path (/tmp) or the physical one (/private/tmp on macOS).
    local cond="" p
    for p in "$dl" "$dp"; do
      p="$(sq "${p%/}")"
      cond="$cond or directory = '$p' or substr(directory, 1, length('$p') + 1) = '$p/'"
    done
    where="$where and (${cond# or })"
    dir="$dl"
  fi

  local ids n kids
  ids="$(q "select id from session where $where order by time_created")"
  n=0; [[ -n "$ids" ]] && n="$(printf '%s\n' "$ids" | wc -l | tr -d ' ')"
  kids="$(q "select count(*) from session where parent_id in (select id from session where $where)")"
  echo "opencode database: $DB ($(human "$(dbsize)"))"
  echo "Would delete: $n session(s)${dir:+ under $dir}, plus ${kids:-0} sub-agent session(s), with all their messages."
  [[ $history -eq 1 ]] && echo "Would also clear: prompt history, session diffs, tool output, undo snapshots."
  if [[ $go -eq 0 ]]; then
    echo "DRY RUN — nothing changed. Re-run with --go to delete."
    return 0
  fi

  # A live opencode (TUI, `serve`, `web`, `run`) keeps the database open and
  # writes to it; clearing under it races its writes.
  if pgrep -x opencode >/dev/null 2>&1 || pgrep -x .opencode >/dev/null 2>&1; then
    die "opencode is running on this device — quit every opencode window/server first, then re-run"
  fi

  if [[ $backup -eq 1 ]]; then
    local stamp bk
    stamp="$(date +%Y%m%d-%H%M%S)"
    bk="$DB.backup-$stamp"
    [[ -e "$bk" ]] && die "backup target exists: $bk"
    echo "Backing up to $bk ..."
    ( umask 077; "$OC" db "VACUUM INTO '$(sq "$bk")'" >/dev/null 2>&1 ) \
      || die "backup failed — nothing deleted (free space? try --no-backup only if you are sure)"
    [[ -s "$bk" ]] || die "backup came out empty — nothing deleted"
    chmod 600 "$bk"
    note "backup: $(human "$(bytes "$bk")") — to restore: quit opencode, mv it over $DB, remove its -wal and -shm files"
  fi

  local before failed=0 id
  before="$(dbsize)"
  if [[ -n "$ids" ]]; then
    while IFS= read -r id; do
      [[ "$id" =~ ^ses_[A-Za-z0-9]+$ ]] || { echo "  skip: unexpected id '$id'" >&2; failed=$((failed + 1)); continue; }
      # A sub-agent session is removed with its parent; one already gone is fine.
      [[ "$(q "select count(*) from session where id = '$id'")" == 1 ]] || continue
      if ! "$OC" session delete "$id" >/dev/null 2>&1; then
        echo "  FAILED: $id" >&2; failed=$((failed + 1))
      fi
    done <<< "$ids"
  fi
  local left
  left="$(q "select count(*) from session where $where")"
  echo "Deleted $((n - ${left:-0})) of $n session(s); $left left${dir:+ under $dir}."

  if [[ $history -eq 1 ]]; then
    rm -f -- "$STATE_DIR/prompt-history.jsonl"
    rm -rf -- "$DATA_DIR/storage/session_diff" "$DATA_DIR/tool-output" "$DATA_DIR/snapshot"
    note "cleared prompt history, session diffs, tool output and undo snapshots"
  fi

  echo "Reclaiming space ..."
  "$OC" db "VACUUM" >/dev/null 2>&1 || note "(VACUUM skipped — database busy)"
  "$OC" db "PRAGMA wal_checkpoint(TRUNCATE)" >/dev/null 2>&1 || true
  note "database: $(human "$before") → $(human "$(dbsize)")"
  [[ $failed -eq 0 && "${left:-0}" == 0 ]] || die "$failed deletion(s) failed, $left session(s) remain — see above"
  echo "Done."
}

case "${1:-}" in
  ""|summary|list) summary ;;
  clear) shift; clear_sessions "$@" ;;
  -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command: $1 (summary | clear [--go] [--dir PATH] [--history] [--no-backup])" ;;
esac
