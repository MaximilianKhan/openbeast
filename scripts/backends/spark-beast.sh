#!/bin/bash
# spark-beast: one-word control of the spark-hydra pair, run on spark-1 (the
# head) as the account that owns the deployment. Installed as
# ~/.local/bin/beast-spark with the bash aliases beastup / beastdown /
# beastglm / beastds. docs/SPARK_HYDRA.md "Everyday shortcuts: beast-spark"
#
#   beast-spark up        # beastup:   start the selected model, then the command center
#   beast-spark down      # beastdown: stop the command center and both inference units
#   beast-spark glm       # beastglm:  switch to GLM-5.3-Flash (the GLM default)
#   beast-spark ds        # beastds:   switch to DeepSeek on Mia's EAQ v1.1 -v2 pack (the DS default)
#   beast-spark status    # which model is selected, what is running, health
#
# The pair serves two models only, one at a time (each needs both GPUs):
#   GLM       unit spark-hydra-inference (recipe ~/openbeast-recipes/glm53)
#   DeepSeek  unit deepseek-v41-inference (recipe ~/openbeast-recipes/deepseek-prep/runtime),
#             pack Mia EAQ v1.1 "-v2"; it supersedes the stock EAQ v1 pack, which stays
#             on disk only as a fallback (spark-deepseek-pack.sh use stock).
# "Selected" means the ENABLED inference unit: the one that starts at boot and
# the one `up` starts. `down` leaves the selection as it is.
#
# Switches reuse the installed helpers, so their checks stay in one place:
#   glm  rollback/restore-glm.sh (stop + disable DeepSeek, GLM's settings in
#        openbeast.conf, enable + start GLM, restart the command center)
#   ds   spark-deepseek-pack.sh use <-v2 folder> when another pack is selected,
#        then activate-stock.sh (canary, conformance, openbeast.conf, enable
#        DeepSeek, disable GLM, restart the command center). activate-stock.sh
#        serves whatever pack local.sh selects; "stock" is only its old name.
# Every step waits for /v1/models on 127.0.0.1:8000. Offline: no network
# beyond the spark-1 <-> spark-2 link.
set -euo pipefail

# Test seams.
PREP="${SB_PREP:-$HOME/openbeast-recipes/deepseek-prep}"
DS_PACK="${SB_DS_PACK:-$HOME/Documents/Mia-AiLab-DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw-v2}"
DS_ID=Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1.1-2.9-bpw-v2
DS_UNIT=deepseek-v41-inference.service
GLM_UNIT=spark-hydra-inference.service
OB_UNIT=openbeast.service
URL="${SB_URL:-http://127.0.0.1:8000}"
KEY_FILE="${SB_KEY_FILE:-$HOME/.config/openbeast/tensorfold-api-key}"
WAIT_S="${SB_WAIT_S:-1200}"
export PATH="$HOME/.local/bin:$PATH"

log() { printf '[beast-spark] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }
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
served_model() {
  curl -sf --max-time 3 -H "Authorization: Bearer $(cat "$KEY_FILE" 2>/dev/null)" "$URL/v1/models" 2>/dev/null |
    python3 -I -c 'import json,sys; print(",".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null || true
}
ds_pack() { "$PREP/spark-deepseek-pack.sh" list 2>/dev/null | sed -n 's/^serving (local.sh): \([^ ]*\) @ \([0-9a-f]*\).*/\1@\2/p'; }

wait_ready() {  # unit
  local t=0
  until [[ -n "$(served_model)" ]]; do
    active "$1" || sc is-activating --quiet "$1" 2>/dev/null || die "$1 is not running: journalctl --user -u $1 -n 50"
    (( t >= WAIT_S )) && die "no model on $URL after ${WAIT_S}s: journalctl --user -fu $1"
    (( t % 30 == 0 )) && log "waiting for $1 to serve on $URL (${t}s)"
    sleep 5; t=$((t + 5))
  done
  log "serving: $(served_model)"
}

cmd_status() {
  local s; s=$(selected)
  echo "selected: $s$( [[ $s == ds ]] && echo "  (pack $(ds_pack))")"
  for u in "$GLM_UNIT" "$DS_UNIT" "$OB_UNIT"; do
    printf '  %-32s %s / %s\n' "$u" "$(sc is-active "$u" 2>/dev/null || true)" "$(sc is-enabled "$u" 2>/dev/null || true)"
  done
  echo "serving on $URL: $(served_model | sed 's/^$/nothing/')"
}

cmd_up() {
  local s; s=$(selected)
  [[ "$s" != none ]] || { log "no model selected: selecting DeepSeek"; cmd_ds; return; }
  local u; u=$(unit_of "$s")
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
  if [[ "$(selected)" == glm ]] && active "$GLM_UNIT" && [[ -n "$(served_model)" ]]; then
    log "GLM is already selected and serving"; sc start "$OB_UNIT"; cmd_status; return
  fi
  log "switching to GLM: restore-glm.sh"
  bash "$PREP/rollback/restore-glm.sh"
  wait_ready "$GLM_UNIT"
  cmd_status
  log "on clients: openbeast-client use spark-hydra"
}

cmd_ds() {
  [[ -d "$DS_PACK" ]] || die "the DeepSeek pack folder $DS_PACK is missing"
  if [[ "$(selected)" == ds ]] && active "$DS_UNIT" && [[ "$(ds_pack)" == "$DS_ID@"* ]] && [[ -n "$(served_model)" ]]; then
    log "DeepSeek ($DS_ID) is already selected and serving"; sc start "$OB_UNIT"; cmd_status; return
  fi
  if active "$GLM_UNIT"; then log "stopping GLM"; sc stop "$GLM_UNIT"; fi
  if [[ "$(ds_pack)" != "$DS_ID@"* ]]; then
    log "selecting the -v2 pack (was $(ds_pack)): spark-deepseek-pack.sh use"
    "$PREP/spark-deepseek-pack.sh" use "$DS_PACK"
  fi
  log "activating DeepSeek: activate-stock.sh (canary, conformance, command center, boot selection)"
  bash "$PREP/activate-stock.sh"
  wait_ready "$DS_UNIT"
  cmd_status
  log "on clients: openbeast-client use spark-hydra"
}

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  glm) cmd_glm ;;
  ds|deepseek) cmd_ds ;;
  status) cmd_status ;;
  *) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
