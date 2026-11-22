#!/bin/bash
# spark-beast: one-word control of the spark-hydra pair, run on spark-1 (the
# head) as the account that owns the deployment. Installed as
# ~/.local/bin/beast-spark with the bash aliases beastup / beastdown /
# beastglm / beastds / beasthy4 / beaststatus. docs/SPARK_HYDRA.md "Everyday shortcuts: beast-spark"
#
#   beast-spark up        # beastup:   start the selected model (the other unit stopped first), then the command center
#   beast-spark down      # beastdown: stop the command center and both inference units
#   beast-spark glm       # beastglm:  switch to GLM-5.3-Flash (the staged alternative)
#   beast-spark ds        # beastds:   switch to DeepSeek-V4.1-Flash (Mia EXL3), the pair's default model
#   beast-spark hy4       # beasthy4:  serve Hy4-flash (NVFP4, vLLM TP2) for now: staged, never a boot default
#   beast-spark status    # beaststatus: which model is selected, what is running, health
#   beast-spark aliases   # print the six alias lines for ~/.bashrc
#   beast-spark install   # install/update beast-spark, the pack tools and the aliases from the checkout (~/openbeast)
#
# The pair serves one model at a time (each needs both GPUs). Two can be SELECTED:
#   DeepSeek  unit deepseek-v41-inference (recipe ~/openbeast-recipes/deepseek-prep/runtime):
#             THE DEFAULT (Max, 2026-11-18, after hands-on testing; v5 board 61/71, #1).
#             Serves DeepSeek-V4.1-Flash-1017 from DeepSeek-V4.1-Flash (Mia EXL3), the pack
#             whose folder/cache id ends in "-v2", unless ~/.config/openbeast/beast-spark.env
#             names another (DS_PACK=<folder> and DS_ID=<ORG/NAME>).
#   GLM       unit spark-hydra-inference (recipe ~/openbeast-recipes/glm53): the staged
#             alternative (`beastglm`). Serves GLM-5.3-Flash-EXL3.
# and one more can serve without being selected:
#   Hy4       unit hy4-vllm-inference (scripts/backends/hy4-vllm, installed into
#             ~/openbeast-recipes/hy4-vllm by spark-hy4-stage.sh install): Hy4-flash
#             NVFP4 on vLLM TP2 (`beasthy4`), for measuring it (v5 arm H_T2). Its unit
#             has no [Install] section, so it is never enabled, never starts at boot,
#             and `up` never picks it: the selection (and boot) stays DeepSeek or GLM.
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
#   hy4  stop DeepSeek / GLM (whichever runs; their enablement stays), start
#        hy4-vllm-inference, wait for Hy4-flash-NVFP4, set INFERENCE_BACKEND=vllm
#        and INFERENCE_MODEL in ~/openbeast/openbeast.conf, restart the command
#        center. If anything fails, Hy4 is stopped and the model that served
#        before is started again (its unit; both keys as they were). `ds`, `glm`,
#        `up` and `down` stop Hy4 when it runs and point openbeast.conf back at
#        the selected model (tensorfold, its id) when it still names Hy4.
# Every start waits until /v1/models on 127.0.0.1:8000 lists the EXPECTED
# served id. Switches hold ${XDG_RUNTIME_DIR:-/tmp}/spark-model.lock, which
# spark-deepseek-pack.sh takes too, so two switches never overlap.
# Offline: no network beyond the spark-1 <-> spark-2 link.
set -euo pipefail

# Test seams: SB_PREP, SB_DS_PACK, SB_DS_ID, SB_CONF, SB_URL, SB_KEY_FILE,
# SB_WAIT_S, SB_HY4_WAIT_S, SB_LOCK, SB_BIN_DIR, SB_BASHRC, SB_OB_CONF, SB_CHECKOUT.
PREP="${SB_PREP:-$HOME/openbeast-recipes/deepseek-prep}"
CONF="${SB_CONF:-$HOME/.config/openbeast/beast-spark.env}"
V2_PACK="$HOME/Documents/Mia-AiLab-DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw-v2"
V2_ID=Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1.1-2.9-bpw-v2
DS_UNIT=deepseek-v41-inference.service
GLM_UNIT=spark-hydra-inference.service
HY4_UNIT=hy4-vllm-inference.service
OB_UNIT=openbeast.service
DS_SERVED=DeepSeek-V4.1-Flash-1017
GLM_SERVED=GLM-5.3-Flash-EXL3
HY4_SERVED=Hy4-flash-NVFP4
URL="${SB_URL:-http://127.0.0.1:8000}"
KEY_FILE="${SB_KEY_FILE:-$HOME/.config/openbeast/tensorfold-api-key}"
WAIT_S="${SB_WAIT_S:-1200}"
# Hy4's first start on a new image autotunes FlashInfer's kernels (~10 min, CLAIMED) after a ~2 min load
HY4_WAIT_S="${SB_HY4_WAIT_S:-2400}"
OB_CONF="${SB_OB_CONF:-$HOME/openbeast/openbeast.conf}"
LOCK="${SB_LOCK:-${XDG_RUNTIME_DIR:-/tmp}/spark-model.lock}"
BIN_DIR="${SB_BIN_DIR:-$HOME/.local/bin}"
BASHRC="${SB_BASHRC:-$HOME/.bashrc}"
# The checkout `install` copies from (and the installed copy is compared with)
CHECKOUT="${SB_CHECKOUT:-$HOME/openbeast}"
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
# A stale install (~/.local/bin/beast-spark older than the checkout) lacks
# commands and fixes silently: an old copy answered `hy4` with the usage. One
# cmp per command (no network, no git): warn when the installed copy and the
# checkout's differ, whichever of the two is running.
check_install() {
  local src="$CHECKOUT/scripts/backends/spark-beast.sh" inst="$BIN_DIR/beast-spark"
  [[ -f "$src" && -f "$inst" ]] || return 0
  cmp -s "$src" "$inst" && return 0
  local who
  if [[ "$0" -ef "$inst" ]]; then who="this beast-spark ($inst) differs from the checkout's $src"
  elif [[ "$0" -ef "$src" ]]; then who="the installed $inst (what the beast* aliases run) differs from this checkout's copy"
  else return 0; fi
  log "WARNING: $who: it may lack commands or fixes. Update it (from $CHECKOUT, after a git pull there if the checkout is the older one): beast-spark install"
}

served_of() { case "$1" in "$DS_UNIT") echo "$DS_SERVED" ;; "$GLM_UNIT") echo "$GLM_SERVED" ;; "$HY4_UNIT") echo "$HY4_SERVED" ;; esac; }
hy4_installed() { [[ "$(sc show -p LoadState --value "$HY4_UNIT" 2>/dev/null)" == loaded ]]; }
# Stop Hy4 when it runs (its stop.sh takes both ranks down). When openbeast.conf
# still points at Hy4, it is pointed back at the SELECTED model (TensorFold, its
# served id): CONF_BACK=1 then, and the caller restarts the command center.
CONF_BACK=0
stop_hy4() {
  if active "$HY4_UNIT"; then log "stopping Hy4 ($HY4_UNIT)"; sc stop "$HY4_UNIT"; fi
  if [[ "$(ob_conf_get INFERENCE_MODEL)" == "$HY4_SERVED" ]]; then
    local s; s=$(selected); [[ "$s" == none ]] && s=ds
    log "openbeast.conf: INFERENCE_BACKEND=tensorfold, INFERENCE_MODEL=$(served_of "$(unit_of "$s")") (it pointed at Hy4)"
    ob_conf_set INFERENCE_BACKEND tensorfold
    ob_conf_set INFERENCE_MODEL "$(served_of "$(unit_of "$s")")"
    CONF_BACK=1
  fi
}

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

wait_ready() {  # unit [seconds]: until /v1/models lists THAT unit's served id
  local t=0 want limit=${2:-$WAIT_S}; want=$(served_of "$1")
  until serving "$want"; do
    active "$1" || sc is-activating --quiet "$1" 2>/dev/null || die "$1 is not running: journalctl --user -u $1 -n 50"
    (( t >= limit )) && die "$want is not on $URL after ${limit}s (serving: $(served_model | sed 's/^$/nothing/')): journalctl --user -fu $1"
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
  for u in "$GLM_UNIT" "$DS_UNIT" "$HY4_UNIT" "$OB_UNIT"; do
    if [[ "$u" == "$HY4_UNIT" ]] && ! active "$u" && ! hy4_installed; then
      printf '  %-32s %s\n' "$u" "not installed (spark-hy4-stage.sh install)"; continue
    fi
    printf '  %-32s %s / %s\n' "$u" "$(sc is-active "$u" 2>/dev/null || true)" "$(sc is-enabled "$u" 2>/dev/null || true)"
  done
  if active "$HY4_UNIT"; then echo "hy4: Hy4-flash (NVFP4) serves for now; the selection (and boot) stays $s: beast$s or beastup goes back"; fi
  echo "serving on $URL: $(served_model | sed 's/^$/nothing/')"
  echo "ds pack: $DS_ID ($DS_PACK)$( [[ -r "$CONF" ]] && echo "  from $CONF")"
}

cmd_up() {
  local s; s=$(selected)
  [[ "$s" != none ]] || { log "no model selected: selecting DeepSeek-V4.1-Flash (Mia EXL3), the default"; cmd_ds; return; }
  local u o; u=$(unit_of "$s"); o=$(other_of "$s")
  stop_hy4
  if active "$o"; then log "stopping $o (not selected; both models need both GPUs)"; sc stop "$o"; fi
  log "starting $s ($u), then the command center"
  sc start "$u"
  wait_ready "$u"
  if (( CONF_BACK )); then sc restart "$OB_UNIT"; else sc start "$OB_UNIT"; fi
  cmd_status
}

cmd_down() {
  log "stopping the command center and the inference units (the selection stays: $(selected))"
  sc stop "$OB_UNIT" || true
  sc stop "$DS_UNIT" "$GLM_UNIT" || true
  stop_hy4 || true
  cmd_status
}

cmd_glm() {
  if [[ "$(selected)" == glm ]] && active "$GLM_UNIT" && serving "$GLM_SERVED"; then
    log "GLM is already selected and serving"; sc start "$OB_UNIT"; cmd_status; return
  fi
  stop_hy4
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
  stop_hy4
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

# ---- Hy4-flash (NVFP4, vLLM TP2): serve it for now, never select it
# openbeast.conf KEY: the value of its last uncommented line ("" when none).
ob_conf_get() {
  [[ -r "$OB_CONF" ]] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$OB_CONF" | tail -1 |
    sed -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//' -e "s/^[\"']\(.*\)[\"']\$/\1/"
}
# ob_conf_set KEY VALUE: replace every uncommented KEY= line by one KEY="VALUE"
# where the first was (appended when there was none); atomic, mode kept.
ob_conf_set() {
  [[ -f "$OB_CONF" ]] || die "no $OB_CONF (the command center's checkout): INFERENCE_MODEL not set"
  python3 -I - "$OB_CONF" "$1" "$2" <<'PY'
import os, re, sys, tempfile
path, key, val = sys.argv[1:4]
lines = open(path).read().splitlines(keepends=True)
pat = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
new, done = [], False
for ln in lines:
    if pat.match(ln):
        if not done:
            new.append(f'{key}="{val}"\n')
            done = True
        continue
    new.append(ln)
if not done:
    if new and not new[-1].endswith("\n"):
        new.append("\n")
    new.append(f'{key}="{val}"\n')
d = os.path.dirname(os.path.abspath(path))
fd, tmp = tempfile.mkstemp(dir=d, prefix=".openbeast.conf.")
with os.fdopen(fd, "w") as f:
    f.writelines(new)
os.chmod(tmp, os.stat(path).st_mode & 0o7777)
os.replace(tmp, path)
PY
}

# On any failure of `hy4`: Hy4 stopped, the model that served before back.
HY4_DONE=1 HY4_PREV=none HY4_PREV_MODEL="" HY4_PREV_BACKEND="" HY4_CONF_SET=0
hy4_exit() {
  local rc=$?
  trap - EXIT
  if (( ! HY4_DONE )); then
    log "the Hy4 switch failed (exit $rc): stopping Hy4"
    sc stop "$HY4_UNIT" 2>/dev/null || true
    if (( HY4_CONF_SET )); then
      [[ -n "$HY4_PREV_MODEL" ]] && { ob_conf_set INFERENCE_MODEL "$HY4_PREV_MODEL" || true; }
      [[ -n "$HY4_PREV_BACKEND" ]] && { ob_conf_set INFERENCE_BACKEND "$HY4_PREV_BACKEND" || true; }
    fi
    case "$HY4_PREV" in
      ds|glm)
        local u; u=$(unit_of "$HY4_PREV")
        log "restoring $HY4_PREV ($u), which served when hy4 began"
        if sc start "$u" && (wait_ready "$u"); then log "$HY4_PREV is serving again; Hy4 was NOT activated"
        else log "$HY4_PREV did not come back either: journalctl --user -u $u -n 50; then beast$HY4_PREV"; fi
        (( HY4_CONF_SET )) && sc restart "$OB_UNIT" 2>/dev/null || true ;;
      *) log "nothing served when hy4 began: nothing to restore" ;;
    esac
  fi
  exit "$rc"
}

cmd_hy4() {
  hy4_installed || die "the unit $HY4_UNIT is not installed: scripts/backends/spark-hy4-stage.sh --check, then install (docs/SPARK_HYDRA.md \"Hy4-flash (NVFP4, vLLM TP2): staged\")"
  if active "$HY4_UNIT" && serving "$HY4_SERVED"; then
    log "Hy4 is already serving"
    if [[ "$(ob_conf_get INFERENCE_MODEL)" != "$HY4_SERVED" || "$(ob_conf_get INFERENCE_BACKEND)" != vllm ]]; then
      ob_conf_set INFERENCE_BACKEND vllm; ob_conf_set INFERENCE_MODEL "$HY4_SERVED"; sc restart "$OB_UNIT"
    fi
    cmd_status; return
  fi
  if active "$DS_UNIT"; then HY4_PREV=ds; elif active "$GLM_UNIT"; then HY4_PREV=glm; fi
  HY4_PREV_MODEL=$(ob_conf_get INFERENCE_MODEL)
  HY4_PREV_BACKEND=$(ob_conf_get INFERENCE_BACKEND)
  HY4_DONE=0
  trap hy4_exit EXIT
  trap 'exit 130' INT; trap 'exit 143' TERM; trap 'exit 129' HUP
  for u in "$DS_UNIT" "$GLM_UNIT"; do
    if active "$u"; then log "stopping $u (one model at a time; it stays $(sc is-enabled "$u" 2>/dev/null || echo disabled) for boot)"; sc stop "$u"; fi
  done
  log "starting Hy4-flash (NVFP4, vLLM TP2): $HY4_UNIT (first start on this image: ~10 min of kernel autotune)"
  sc start "$HY4_UNIT"
  wait_ready "$HY4_UNIT" "$HY4_WAIT_S"
  # vllm: the command center's readiness probe for vLLM (/health is an empty 200; TensorFold's says ok)
  log "INFERENCE_BACKEND=vllm, INFERENCE_MODEL=\"$HY4_SERVED\" in $OB_CONF (was ${HY4_PREV_BACKEND:-unset}, \"$HY4_PREV_MODEL\"), restarting the command center"
  HY4_CONF_SET=1
  ob_conf_set INFERENCE_BACKEND vllm
  ob_conf_set INFERENCE_MODEL "$HY4_SERVED"
  sc restart "$OB_UNIT"
  HY4_DONE=1
  trap - INT TERM HUP
  cmd_status
  log "back to the default: beastds (or beastglm); a reboot also comes back to the selection ($(selected))"
}

ALIAS_MARK='# beast-spark shortcuts (OpenBeast scripts/backends/spark-beast.sh; docs/SPARK_HYDRA.md)'
cmd_aliases() {
  echo "$ALIAS_MARK"
  local a
  for a in up down glm ds hy4 status; do
    # shellcheck disable=SC2016  # $HOME expands when ~/.bashrc runs, not now
    printf 'alias beast%s="$HOME/.local/bin/beast-spark %s"\n' "$a" "$a"
  done
}

# Install from a checkout (scripts/backends/): beast-spark into ~/.local/bin,
# the two pack tools into the DeepSeek prep directory, the aliases into ~/.bashrc.
# The installed copy (~/.local/bin/beast-spark install) hands over to the
# checkout's copy, so `beast-spark install` is the one update command.
cmd_install() {
  local here; here="$(cd "$(dirname "$0")" && pwd)"
  local co="$CHECKOUT/scripts/backends"
  if [[ ! -f "$here/spark-deepseek-pack.sh" && -f "$co/spark-beast.sh" && -f "$co/spark-deepseek-pack.sh" && -f "$co/spark-deepseek-prepare-local.sh" ]]; then
    log "installing from the checkout: $co/spark-beast.sh install"
    exec bash "$co/spark-beast.sh" install
  fi
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
    # an older install: add the alias lines it lacks (beasthy4), keep the rest
    local l added=0
    while IFS= read -r l; do
      [[ "$l" == alias* ]] || continue
      grep -qxF "$l" "$BASHRC" || { echo "$l" >> "$BASHRC"; added=$((added + 1)); }
    done < <(cmd_aliases)
    if (( added )); then log "aliases: $added added to $BASHRC"; else log "aliases: already in $BASHRC"; fi
  else
    { echo; cmd_aliases; } >> "$BASHRC"
    log "aliases: appended to $BASHRC (open a new shell, or: source $BASHRC)"
  fi
}

[[ "${1:-}" == install ]] || check_install
case "${1:-}" in
  up) take_lock; cmd_up ;;
  down) take_lock; cmd_down ;;
  glm) take_lock; cmd_glm ;;
  ds|deepseek) take_lock; cmd_ds ;;
  hy4) take_lock; cmd_hy4 ;;
  status) cmd_status ;;
  aliases) cmd_aliases ;;
  install) cmd_install ;;
  *) [[ -n "${1:-}" ]] && log "ERROR: unknown command: $1 (an older install? beast-spark install updates it from $CHECKOUT)"
     sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
