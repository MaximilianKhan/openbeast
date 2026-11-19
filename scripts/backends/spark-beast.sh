#!/bin/bash
# spark-beast: one-word control of the spark-hydra pair, run on spark-1 (the
# head) as the account that owns the deployment. Installed as
# ~/.local/bin/beast-spark with the bash aliases beastup / beastdown /
# beastglm / beastds / beaststatus. docs/SPARK_HYDRA.md "Everyday shortcuts: beast-spark"
#
#   beast-spark up        # beastup:   start the selected model (the other unit stopped first), then the command center
#   beast-spark down      # beastdown: stop the command center and both inference units
#   beast-spark glm       # beastglm:  switch to GLM-5.3-Flash (the staged alternative)
#   beast-spark ds        # beastds:   switch to DeepSeek-V4.1-Flash (Mia EXL3), the pair's default model
#   beast-spark status    # beaststatus: which model is selected, what is running, health
#   beast-spark aliases   # print the five alias lines for ~/.bashrc
#   beast-spark install   # from a checkout: install beast-spark, the pack tools and the aliases
#
# The pair serves two models only, one at a time (each needs both GPUs):
#   DeepSeek  unit deepseek-v41-inference (recipe ~/openbeast-recipes/deepseek-prep/runtime):
#             THE DEFAULT (Max, 2026-11-18, after hands-on testing; v5 board 61/71, #1).
#             Serves DeepSeek-V4.1-Flash-1017 from DeepSeek-V4.1-Flash (Mia EXL3), the pack
#             whose folder/cache id ends in "-v2", unless ~/.config/openbeast/beast-spark.env
#             names another (DS_PACK=<folder> and DS_ID=<ORG/NAME>).
#   GLM       unit spark-hydra-inference (recipe ~/openbeast-recipes/glm53): the staged
#             alternative (`beastglm`). Serves GLM-5.3-Flash-EXL3.
# "Selected" means the ENABLED inference unit: the one that starts at boot and
# the one `up` starts. `down` leaves the selection as it is.
#
# This is THE switch path. The installed helpers it calls are internals:
#   glm  rollback/restore-glm.sh (stop + disable DeepSeek, GLM's settings in
#        openbeast.conf, enable + start GLM, restart the command center)
#   ds   stop GLM; spark-deepseek-pack.sh use DS_PACK --id DS_ID when another
#        pack is selected; then activate-stock.sh (start DeepSeek, canary,
#        conformance, openbeast.conf, enable DeepSeek, disable GLM, restart the
#        command center). activate-stock.sh never stops GLM itself: never run it
#        while GLM is active. If anything fails and GLM was serving when `ds`
#        began, restore-glm.sh brings GLM back.
# Every start waits until /v1/models on 127.0.0.1:8000 lists the EXPECTED
# served id. Switches hold ${XDG_RUNTIME_DIR:-/tmp}/spark-model.lock, which
# spark-deepseek-pack.sh takes too, so two switches never overlap.
# Offline: no network beyond the spark-1 <-> spark-2 link.
set -euo pipefail

# Test seams: SB_PREP, SB_DS_PACK, SB_DS_ID, SB_CONF, SB_URL, SB_KEY_FILE,
# SB_WAIT_S, SB_LOCK, SB_BIN_DIR, SB_BASHRC.
PREP="${SB_PREP:-$HOME/openbeast-recipes/deepseek-prep}"
CONF="${SB_CONF:-$HOME/.config/openbeast/beast-spark.env}"
V2_PACK="$HOME/Documents/Mia-AiLab-DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw-v2"
V2_ID=Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1.1-2.9-bpw-v2
DS_UNIT=deepseek-v41-inference.service
GLM_UNIT=spark-hydra-inference.service
OB_UNIT=openbeast.service
DS_SERVED=DeepSeek-V4.1-Flash-1017
GLM_SERVED=GLM-5.3-Flash-EXL3
URL="${SB_URL:-http://127.0.0.1:8000}"
KEY_FILE="${SB_KEY_FILE:-$HOME/.config/openbeast/tensorfold-api-key}"
WAIT_S="${SB_WAIT_S:-1200}"
LOCK="${SB_LOCK:-${XDG_RUNTIME_DIR:-/tmp}/spark-model.lock}"
BIN_DIR="${SB_BIN_DIR:-$HOME/.local/bin}"
BASHRC="${SB_BASHRC:-$HOME/.bashrc}"
export PATH="$HOME/.local/bin:$PATH"

log() { printf '[beast-spark] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# ~/.config/openbeast/beast-spark.env: KEY=value lines, PARSED, never sourced.
# DS_PACK (the pack folder `ds` selects) and DS_ID (the ORG/NAME it is staged
# as; `spark-deepseek-pack.sh list` shows it) go together.
conf_value() {  # KEY
  [[ -r "$CONF" ]] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$CONF" | tail -1 |
    sed -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//' -e "s/^[\"']\(.*\)[\"']\$/\1/" -e "s|^~/|$HOME/|" -e "s|^\\\$HOME/|$HOME/|"
}
DS_PACK="${SB_DS_PACK:-$(conf_value DS_PACK)}"
DS_ID="${SB_DS_ID:-$(conf_value DS_ID)}"
if [[ -z "$DS_PACK" ]]; then DS_PACK=$V2_PACK; DS_ID=${DS_ID:-$V2_ID}; fi

sc() { systemctl --user "$@"; }
enabled() { [[ "$(sc is-enabled "$1" 2>/dev/null || true)" == enabled ]]; }
active() { sc is-active --quiet "$1"; }

# The model the pair is set to serve: glm, ds, or none.
selected() {
  if enabled "$DS_UNIT"; then echo ds
  elif enabled "$GLM_UNIT"; then echo glm
  else echo none; fi
}
unit_of() { case "$1" in ds) echo "$DS_UNIT" ;; glm) echo "$GLM_UNIT" ;; esac; }
other_of() { case "$1" in ds) echo "$GLM_UNIT" ;; glm) echo "$DS_UNIT" ;; esac; }
served_of() { case "$1" in "$DS_UNIT") echo "$DS_SERVED" ;; "$GLM_UNIT") echo "$GLM_SERVED" ;; esac; }

# GET $URL$1 with the API key as a curl config line on fd 3 (the curl_auth.sh
# idiom, inlined because the installed copy lives in ~/.local/bin, away from
# the checkout): the key is never an argument of any process.
api_get() {
  local k=""
  [[ -r "$KEY_FILE" ]] && k=$(<"$KEY_FILE")
  if [[ -z "$k" ]]; then curl -sf --max-time 3 "$URL$1"; return; fi
  [[ "$k" =~ ^[A-Za-z0-9._~+/=-]+$ ]] || { log "the key in $KEY_FILE has unexpected characters: not sent"; return 1; }
  curl -sf --max-time 3 --config /dev/fd/3 "$URL$1" 3<<EOF
header = "Authorization: Bearer $k"
EOF
}
served_model() {  # the served ids, comma-separated ("" when nothing answers)
  api_get /v1/models 2>/dev/null |
    python3 -I -c 'import json,sys; print(",".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null || true
}
serving() { [[ ",$(served_model)," == *",$1,"* ]]; }   # serving <served id>
ds_pack() { "$PREP/spark-deepseek-pack.sh" list 2>/dev/null | sed -n 's/^serving (local.sh): \([^ ]*\) @ \([0-9a-f]*\).*/\1@\2/p'; }

wait_ready() {  # unit: until /v1/models lists THAT unit's served id
  local t=0 want; want=$(served_of "$1")
  until serving "$want"; do
    active "$1" || sc is-activating --quiet "$1" 2>/dev/null || die "$1 is not running: journalctl --user -u $1 -n 50"
    (( t >= WAIT_S )) && die "$want is not on $URL after ${WAIT_S}s (serving: $(served_model | sed 's/^$/nothing/')): journalctl --user -fu $1"
    (( t % 30 == 0 )) && log "waiting for $1 to serve $want on $URL (${t}s)"
    sleep 5; t=$((t + 5))
  done
  log "serving: $(served_model)"
}

# One model switch at a time, shared with spark-deepseek-pack.sh (which skips
# its own flock when SPARK_MODEL_LOCK_HELD=1, i.e. when called from here).
take_lock() {
  exec 8>>"$LOCK"
  flock -n 8 || die "another model switch is running (it holds $LOCK): wait for it to finish, then retry"
  export SPARK_MODEL_LOCK_HELD=1
}

cmd_status() {
  local s; s=$(selected)
  echo "selected: $s$( [[ $s == ds ]] && echo "  (pack $(ds_pack))")"
  for u in "$GLM_UNIT" "$DS_UNIT" "$OB_UNIT"; do
    printf '  %-32s %s / %s\n' "$u" "$(sc is-active "$u" 2>/dev/null || true)" "$(sc is-enabled "$u" 2>/dev/null || true)"
  done
  echo "serving on $URL: $(served_model | sed 's/^$/nothing/')"
  echo "ds pack: $DS_ID ($DS_PACK)$( [[ -r "$CONF" ]] && echo "  from $CONF")"
}

cmd_up() {
  local s; s=$(selected)
  [[ "$s" != none ]] || { log "no model selected: selecting DeepSeek-V4.1-Flash (Mia EXL3), the default"; cmd_ds; return; }
  local u o; u=$(unit_of "$s"); o=$(other_of "$s")
  if active "$o"; then log "stopping $o (not selected; both models need both GPUs)"; sc stop "$o"; fi
  log "starting $s ($u), then the command center"
  sc start "$u"
  wait_ready "$u"
  sc start "$OB_UNIT"
  cmd_status
}

cmd_down() {
  log "stopping the command center and both inference units (the selection stays: $(selected))"
  sc stop "$OB_UNIT" || true
  sc stop "$DS_UNIT" "$GLM_UNIT" || true
  cmd_status
}

cmd_glm() {
  if [[ "$(selected)" == glm ]] && active "$GLM_UNIT" && serving "$GLM_SERVED"; then
    log "GLM is already selected and serving"; sc start "$OB_UNIT"; cmd_status; return
  fi
  log "switching to GLM: restore-glm.sh"
  bash "$PREP/rollback/restore-glm.sh"
  wait_ready "$GLM_UNIT"
  cmd_status
  log "on clients: openbeast-client use spark-hydra"
}

# On any failure of `ds` that started from a serving GLM: put GLM back.
DS_DONE=1 GLM_AT_ENTRY=0
ds_exit() {
  local rc=$?
  trap - EXIT
  if (( ! DS_DONE && GLM_AT_ENTRY )); then
    log "the DeepSeek switch failed (exit $rc): GLM was serving when it began, restoring it (restore-glm.sh)"
    if bash "$PREP/rollback/restore-glm.sh" && wait_ready "$GLM_UNIT"; then
      log "GLM is serving again; DeepSeek was NOT activated"
    else
      log "GLM did not come back either: journalctl --user -u $GLM_UNIT -n 50"
    fi
  fi
  exit "$rc"
}

cmd_ds() {
  [[ -d "$DS_PACK" ]] || die "the DeepSeek pack folder $DS_PACK is missing"
  [[ "$DS_ID" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] ||
    die "DS_PACK=$DS_PACK needs DS_ID=<ORG/NAME> beside it in $CONF (spark-deepseek-pack.sh list shows the staged names)"
  if [[ "$(selected)" == ds ]] && active "$DS_UNIT" && [[ "$(ds_pack)" == "$DS_ID@"* ]] && serving "$DS_SERVED"; then
    log "DeepSeek ($DS_ID) is already selected and serving"; sc start "$OB_UNIT"; cmd_status; return
  fi
  if active "$GLM_UNIT"; then GLM_AT_ENTRY=1; fi
  DS_DONE=0
  trap ds_exit EXIT
  trap 'exit 130' INT; trap 'exit 143' TERM; trap 'exit 129' HUP
  if active "$GLM_UNIT"; then log "stopping GLM"; sc stop "$GLM_UNIT"; fi
  if [[ "$(ds_pack)" != "$DS_ID@"* ]]; then
    log "selecting the pack $DS_ID (was $(ds_pack)): spark-deepseek-pack.sh use"
    "$PREP/spark-deepseek-pack.sh" use "$DS_PACK" --id "$DS_ID"
  fi
  log "activating DeepSeek: activate-stock.sh (canary, conformance, command center, boot selection)"
  bash "$PREP/activate-stock.sh"
  wait_ready "$DS_UNIT"
  DS_DONE=1
  trap - INT TERM HUP
  cmd_status
  log "on clients: openbeast-client use spark-hydra"
}

ALIAS_MARK='# beast-spark shortcuts (OpenBeast scripts/backends/spark-beast.sh; docs/SPARK_HYDRA.md)'
cmd_aliases() {
  echo "$ALIAS_MARK"
  local a
  for a in up down glm ds status; do
    # shellcheck disable=SC2016  # $HOME expands when ~/.bashrc runs, not now
    printf 'alias beast%s="$HOME/.local/bin/beast-spark %s"\n' "$a" "$a"
  done
}

# Install from a checkout (scripts/backends/): beast-spark into ~/.local/bin,
# the two pack tools into the DeepSeek prep directory, the aliases into ~/.bashrc.
cmd_install() {
  local here; here="$(cd "$(dirname "$0")" && pwd)"
  [[ -f "$here/spark-deepseek-pack.sh" && -f "$here/spark-deepseek-prepare-local.sh" ]] ||
    die "run install from a checkout: ~/openbeast/scripts/backends/spark-beast.sh install ($here has no pack tools)"
  put() {  # src dest
    if cmp -s "$1" "$2"; then log "unchanged: $2"; return; fi
    mkdir -p "$(dirname "$2")"
    cp "$1" "$2.new" && chmod 0755 "$2.new" && mv -f "$2.new" "$2"
    log "installed: $2"
  }
  put "$here/spark-beast.sh" "$BIN_DIR/beast-spark"
  if [[ -d "$PREP" ]]; then
    put "$here/spark-deepseek-pack.sh" "$PREP/spark-deepseek-pack.sh"
    put "$here/spark-deepseek-prepare-local.sh" "$PREP/spark-deepseek-prepare-local.sh"
  else
    log "no $PREP (the DeepSeek recipe is not on this machine): pack tools not installed"
  fi
  if grep -qxF "$ALIAS_MARK" "$BASHRC" 2>/dev/null; then
    log "aliases: already in $BASHRC"
  else
    { echo; cmd_aliases; } >> "$BASHRC"
    log "aliases: appended to $BASHRC (open a new shell, or: source $BASHRC)"
  fi
}

case "${1:-}" in
  up) take_lock; cmd_up ;;
  down) take_lock; cmd_down ;;
  glm) take_lock; cmd_glm ;;
  ds|deepseek) take_lock; cmd_ds ;;
  status) cmd_status ;;
  aliases) cmd_aliases ;;
  install) cmd_install ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
