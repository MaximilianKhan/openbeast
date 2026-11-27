#!/bin/bash
# spark-beast.sh (beastup / beastdown / beastglm / beastds / beasthy4 / beaststatus):
# which helpers each command calls, in which order, and what it refuses.
# Hermetic: systemctl, curl, flock and the installed helpers
# (spark-deepseek-pack.sh, activate-stock.sh, restore-glm.sh) are recording
# stubs over a scratch unit state; nothing touches a real unit.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SB="$REPO_DIR/scripts/backends/spark-beast.sh"
PASS=0 FAIL=0
pass() { echo "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
echo "=== spark-beast tests ==="

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/prep/rollback" "$T/state" "$T/pack" "$T/prep/runtime/scripts"
# DeepSeek's runtime serves 8 streams (spark-1's local.sh since 2026-11-23): what `ds` sets INFERENCE_SLOTS to
printf '# local\nCONTEXT=1048576\nPARALLEL=8   # streams\n' > "$T/prep/runtime/scripts/local.sh"
V2=Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1.1-2.9-bpw-v2
export SB_PREP="$T/prep" SB_DS_PACK="$T/pack" SB_DS_ID="$V2" SB_CONF="$T/beast-spark.env" SB_KEY_FILE="$T/key" \
       SB_WAIT_S=10 SB_HY4_WAIT_S=10 SB_LOCK="$T/model.lock" SB_OB_CONF="$T/openbeast.conf" ST="$T/state" LOG="$T/log" \
       SB_BIN_DIR="$T/nobin" SB_CHECKOUT="$T/nocheckout" SB_RECIPES_D="$T/recipes.d" SB_UNIT_DIR="$T/units"
echo SECRETKEY123 > "$T/key"
# systemctl --user: state files $ST/<unit>.active / .enabled; every mutating call logged.
cat > "$T/bin/systemctl" <<'S'
#!/bin/bash
shift  # --user
op=$1; shift
case "$op" in
  is-enabled) [[ -f "$ST/$1.enabled" ]] && { echo enabled; exit 0; }
              [[ -f "$ST/$1.installed" ]] && { echo static; exit 0; }; echo disabled; exit 1 ;;
  # show -p LoadState / -p ActiveState --value UNIT ($ST/<unit>.activating: starting, or a
  # Restart=on-failure unit in auto-restart)
  show) u=${!#}
        if [[ "$*" == *ActiveState* ]]; then
          if [[ -f "$ST/$u.active" ]]; then echo active; elif [[ -f "$ST/$u.activating" ]]; then echo activating; else echo inactive; fi
        else [[ -f "$ST/$u.installed" ]] && echo loaded || echo not-found; fi ;;
  # $ST/fail-cc-restart: restarting openbeast.service fails (its stop timed out, 2026-11-23);
  # $ST/fail-cc-start: so does starting it.
  restart) for u; do echo "restart $u" >> "$LOG"; if [[ "$u" == openbeast.service && -f "$ST/fail-cc-restart" ]]; then exit 1; fi; done ;;
  reset-failed) echo "reset-failed $*" >> "$LOG" ;;
  is-active) q=0; [[ "$1" == --quiet ]] && { q=1; shift; }
             [[ -f "$ST/$1.active" ]] && { ((q)) || echo active; exit 0; }; ((q)) || echo inactive; exit 3 ;;
  # systemd 255 has no is-activating verb (spark-1: "Unknown command verb"): as the real one, exit 1
  is-activating) echo "Unknown command verb 'is-activating', did you mean 'is-active'?" >&2; exit 1 ;;
  # $ST/start-activating: a started inference unit stays "activating"; $ST/fail-glm: GLM's unit dies at
# once; $ST/fail-start: every inference unit does
  start) for u; do echo "start $u" >> "$LOG"; [[ "$u" == openbeast.service && -f "$ST/fail-cc-start" ]] && exit 1
           if [[ -f "$ST/start-activating" && "$u" != openbeast.service ]]; then touch "$ST/$u.activating"; continue; fi
           [[ "$u" == spark-hydra-inference.service && -f "$ST/fail-glm" ]] && continue
           [[ "$u" != openbeast.service && -f "$ST/fail-start" ]] && continue
           [[ "$u" == hy4-vllm-inference.service && -f "$ST/fail-hy4" ]] || [[ "$u" == beast-recipe-* && -f "$ST/fail-recipe" ]] || touch "$ST/$u.active"; done ;;
  stop) for u; do echo "stop $u" >> "$LOG"; rm -f "$ST/$u.active"; done ;;
  enable) for u; do echo "enable $u" >> "$LOG"; touch "$ST/$u.enabled"; done ;;
  disable) for u; do echo "disable $u" >> "$LOG"; rm -f "$ST/$u.enabled"; done ;;
esac
S
# curl: serves the active unit's model id (or $ST/served-override); logs argv and fd 3.
cat > "$T/bin/curl" <<'S'
#!/bin/bash
echo "$*" >> "$ST/curl.argv"
for a; do [[ "$a" == /dev/fd/3 ]] && cat /dev/fd/3 >> "$ST/curl.fd3"; done
if [[ -f "$ST/served-override" ]]; then id=$(cat "$ST/served-override")
elif [[ -f "$ST/deepseek-v41-inference.service.active" ]]; then id=DeepSeek-V4.1-Flash-1017
elif [[ -f "$ST/spark-hydra-inference.service.active" ]]; then id=GLM-5.3-Flash-EXL3
elif [[ -f "$ST/hy4-vllm-inference.service.active" ]]; then id=Hy4-flash-NVFP4
elif [[ -f "$ST/beast-recipe-demo.service.active" ]]; then id=demo-served
elif [[ -f "$ST/beast-recipe-other.service.active" ]]; then id=other-served
else exit 7; fi
echo "{\"data\":[{\"id\":\"$id\"}]}"
S
# flock -n FD: busy while $ST/lock-busy exists (stands in for util-linux on any OS)
cat > "$T/bin/flock" <<'S'
#!/bin/bash
[[ ! -e "$ST/lock-busy" ]]
S
cat > "$T/prep/spark-deepseek-pack.sh" <<'S'
#!/bin/bash
if [[ "$1" == list ]]; then echo "serving (local.sh): $(cat "$ST/pack") @ 04c13f12   rank 1 cache: x"; exit 0; fi
echo "pack $*" >> "$LOG"
[[ "${SPARK_MODEL_LOCK_HELD:-}" == 1 ]] || echo "pack-without-lock-env" >> "$LOG"
while (( $# )); do [[ "$1" == --id ]] && { echo "$2" > "$ST/pack"; }; shift; done
S
cat > "$T/prep/activate-stock.sh" <<'S'
echo activate >> "$LOG"
[[ -f "$ST/fail-activate" ]] && exit 2
touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled"
rm -f "$ST/spark-hydra-inference.service.enabled"
S
cat > "$T/prep/rollback/restore-glm.sh" <<'S'
echo restore-glm >> "$LOG"; rm -f "$ST/deepseek-v41-inference.service."{active,enabled}
touch "$ST/spark-hydra-inference.service.enabled"
# $ST/glm-no-come-back: the script succeeds but GLM's unit dies (never serves)
[[ -f "$ST/glm-no-come-back" ]] || touch "$ST/spark-hydra-inference.service.active"
S
chmod +x "$T"/bin/* "$T/prep/spark-deepseek-pack.sh"
export PATH="$T/bin:$PATH"
reset() { rm -f "$ST"/* "$LOG"; touch "$LOG"; echo "${1:-$V2}" > "$ST/pack"
  printf '# conf\nINFERENCE_BACKEND=tensorfold\nINFERENCE_THINKING=on\nINFERENCE_MODEL="DeepSeek-V4.1-Flash-1017"\nAGENT_PROFILE=deepseek-best\nINFERENCE_SLOTS=8\n' > "$T/openbeast.conf"; }
# openbeast.conf as GLM leaves it (its 4 streams): for tests of a GLM-selected pair
glm_conf() { sed -i.bak -e 's/^INFERENCE_MODEL=.*/INFERENCE_MODEL="GLM-5.3-Flash-EXL3"/' -e 's/^INFERENCE_SLOTS=.*/INFERENCE_SLOTS=4/' "$T/openbeast.conf"; }
conf_slots() { sed -n 's/^INFERENCE_SLOTS=//p' "$T/openbeast.conf" | tr -d '"'; }
conf_model() { sed -n 's/^INFERENCE_MODEL=//p' "$T/openbeast.conf"; }
conf_backend() { sed -n 's/^INFERENCE_BACKEND=//p' "$T/openbeast.conf"; }
run() { bash "$SB" "$@" > "$T/out" 2>&1; }
calls() { paste -sd';' "$LOG"; }

# beastglm from DeepSeek: restore-glm.sh, nothing else switches
reset; touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled" "$ST/openbeast.service.active"
if run glm && [[ "$(calls)" == restore-glm ]] && grep -q 'selected: glm' "$T/out"; then pass "glm from ds: restore-glm.sh, GLM selected"
else fail "glm from ds: $(calls) / $(cat "$T/out")"; fi

# beastds from GLM with the old pack selected: stop GLM, pack use -v2 --id, activate
reset Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw
touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled"
if run ds && [[ "$(calls)" == "stop spark-hydra-inference.service;pack use $T/pack --id $V2;activate" ]] && grep -q "pack $V2@04c13f12" "$T/out"; then
  pass "ds from glm on the old pack: stop GLM, select -v2 (--id passed, lock held), activate"
else fail "ds from glm: $(calls) / $(cat "$T/out")"; fi

# beastds from GLM with -v2 already selected: no pack step
reset; touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled"
if run ds && [[ "$(calls)" == "stop spark-hydra-inference.service;activate" ]]; then pass "ds from glm on -v2: no pack swap"
else fail "ds on -v2: $(calls)"; fi

# beastds while -v2 serves: a no-op apart from making sure the command center runs
reset; touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled"
if run ds && [[ "$(calls)" == "start openbeast.service" ]] && grep -q 'already selected and serving' "$T/out"; then pass "ds while serving -v2: no-op"
else fail "ds no-op: $(calls)"; fi

# beastds fails after GLM was stopped: GLM is restored and waited for
reset Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw
touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled" "$ST/fail-activate"
if ! run ds && [[ "$(calls)" == "stop spark-hydra-inference.service;pack use $T/pack --id $V2;activate;restore-glm" ]] \
   && [[ -f "$ST/spark-hydra-inference.service.active" ]] && grep -q "GLM is serving again" "$T/out"; then
  pass "ds failing with GLM serving at entry: restore-glm.sh, GLM serving again, non-zero exit"
else fail "ds failure trap: $(calls) / $(cat "$T/out")"; fi
# ... but not when GLM was not serving when ds began
reset; touch "$ST/fail-activate"
if ! run ds && [[ "$(calls)" == "activate" ]]; then pass "ds failing from a stopped pair: GLM is not started behind your back"
else fail "ds failure, no GLM: $(calls)"; fi

# beastdown: command center first, both units, selection kept
reset; touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled" "$ST/openbeast.service.active"
if run down && [[ "$(calls)" == "stop openbeast.service;stop deepseek-v41-inference.service;stop spark-hydra-inference.service" ]] \
   && [[ -f "$ST/deepseek-v41-inference.service.enabled" ]] && grep -q 'selected: ds' "$T/out"; then pass "down: command center, then both units; selection kept"
else fail "down: $(calls)"; fi

# beastup: the selected unit, then the command center
reset; glm_conf; touch "$ST/spark-hydra-inference.service.enabled"
if run up && [[ "$(calls)" == "start spark-hydra-inference.service;start openbeast.service" ]]; then pass "up: selected GLM first, then the command center"
else fail "up glm: $(calls) / $(cat "$T/out")"; fi

# beastup with the OTHER model still running: it is stopped first (both need both GPUs)
reset; glm_conf; touch "$ST/spark-hydra-inference.service.enabled" "$ST/deepseek-v41-inference.service.active"
if run up && [[ "$(calls)" == "stop deepseek-v41-inference.service;start spark-hydra-inference.service;start openbeast.service" ]]; then
  pass "up: the non-selected unit is stopped before the selected one starts"
else fail "up stops other: $(calls) / $(cat "$T/out")"; fi

# beastup waits for the EXPECTED served id, not any model
reset; touch "$ST/spark-hydra-inference.service.enabled"; echo SomethingElse > "$ST/served-override"
if ! SB_WAIT_S=0 run up && grep -q "GLM-5.3-Flash-EXL3 is not on" "$T/out" && ! grep -q "start openbeast.service" "$LOG"; then
  pass "up: another id on :8000 is not 'ready' (the command center is not started)"
else fail "up wrong id: $(calls) / $(cat "$T/out")"; fi

# beastup with nothing selected: DeepSeek-V4.1-Flash (Mia EXL3), the default
reset
if run up && [[ "$(calls)" == activate ]] && grep -q 'selecting DeepSeek-V4.1-Flash (Mia EXL3), the default' "$T/out" \
   && grep -q 'selected: ds' "$T/out"; then pass "up with no selection: DeepSeek, the default (activate-stock.sh, never restore-glm)"
else fail "up none: $(calls) / $(cat "$T/out")"; fi

# beastup with nothing selected and another pack configured: the configured pack is selected first
reset Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw
if run up && [[ "$(calls)" == "pack use $T/pack --id $V2;activate" ]]; then pass "up with no selection on the old pack: pack use, then activate"
else fail "up none old pack: $(calls) / $(cat "$T/out")"; fi

# the API key travels on fd 3 (curl --config), never in argv
if [[ -s "$ST/curl.argv" ]] && ! grep -q SECRETKEY123 "$ST/curl.argv" && grep -q 'Authorization: Bearer SECRETKEY123' "$ST/curl.fd3"; then
  pass "the API key is never in curl's argv"
else fail "key in argv: $(cat "$ST/curl.argv" 2>&1)"; fi

# one switch at a time: the shared lock
reset; touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled" "$ST/lock-busy"
if ! run ds && [[ -z "$(calls)" ]] && grep -q "another model switch is running" "$T/out"; then pass "ds: refused while another switch holds the lock"
else fail "lock: $(calls) / $(cat "$T/out")"; fi
if run status; then pass "status: takes no lock"; else fail "status under lock: $(cat "$T/out")"; fi

# DS_PACK / DS_ID from ~/.config/openbeast/beast-spark.env (an adopted pack survives a round trip)
reset Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw
mkdir -p "$T/pack2"; printf 'DS_PACK=%s   # adopted 2026-11-18\nDS_ID="org/other"\n' "$T/pack2" > "$T/beast-spark.env"
touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled"
if env -u SB_DS_PACK -u SB_DS_ID bash "$SB" ds > "$T/out" 2>&1 && [[ "$(calls)" == "stop spark-hydra-inference.service;pack use $T/pack2 --id org/other;activate" ]]; then
  pass "ds: DS_PACK and DS_ID come from beast-spark.env"
else fail "conf: $(calls) / $(cat "$T/out")"; fi
reset; printf 'DS_PACK=%s\n' "$T/pack2" > "$T/beast-spark.env"
if ! env -u SB_DS_PACK -u SB_DS_ID bash "$SB" ds > "$T/out" 2>&1 && grep -q "needs DS_ID" "$T/out" && [[ -z "$(calls)" ]]; then
  pass "ds: a DS_PACK without DS_ID is refused before anything stops"
else fail "conf no id: $(calls) / $(cat "$T/out")"; fi
rm -f "$T/beast-spark.env"

# a unit that dies while starting is reported, not waited on forever
reset; touch "$ST/deepseek-v41-inference.service.enabled"
# systemctl start no longer brings the unit up ($ST/fail-start: no inference unit comes up)
touch "$ST/fail-start"
if ! run up && grep -q 'is not running' "$T/out"; then pass "up: a unit that does not come up fails fast"
else fail "up dead unit: $(cat "$T/out")"; fi
rm -f "$ST/fail-start"   # units come up again

# --- Hy4-flash (NVFP4, vLLM TP2): served for now, never selected ---------------------
HY4U=hy4-vllm-inference.service
# beasthy4 from DeepSeek: stop DeepSeek (still enabled for boot), start Hy4, wait, INFERENCE_MODEL, command center
reset; touch "$ST/$HY4U.installed" "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled"
if run hy4 && [[ "$(calls)" == "stop deepseek-v41-inference.service;start $HY4U;restart openbeast.service" ]] \
   && [[ "$(conf_model)" == '"Hy4-flash-NVFP4"' ]] && [[ -f "$ST/deepseek-v41-inference.service.enabled" ]] \
   && grep -q 'selected: ds' "$T/out" && grep -q 'Hy4-flash (NVFP4) serves for now' "$T/out" \
   && grep -qx 'AGENT_PROFILE=deepseek-best' "$T/openbeast.conf" && [[ "$(grep -c INFERENCE_MODEL "$T/openbeast.conf")" == 1 ]] \
   && [[ "$(conf_backend)" == '"vllm"' ]]; then
  pass "hy4 from ds: DeepSeek stopped (still the boot selection), Hy4 started and waited for, INFERENCE_BACKEND=vllm and INFERENCE_MODEL set once, command center restarted"
else fail "hy4 from ds: $(calls) / $(conf_model) / $(cat "$T/out")"; fi

# beasthy4 that fails: Hy4 stopped, DeepSeek started again, INFERENCE_MODEL untouched, non-zero exit
reset; touch "$ST/$HY4U.installed" "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled" "$ST/fail-hy4"
if ! run hy4 && [[ "$(calls)" == "stop deepseek-v41-inference.service;start $HY4U;stop $HY4U;start deepseek-v41-inference.service" ]] \
   && [[ -f "$ST/deepseek-v41-inference.service.active" ]] && [[ "$(conf_model)" == '"DeepSeek-V4.1-Flash-1017"' ]] \
   && [[ "$(conf_backend)" == tensorfold ]] && grep -q "ds is serving again; Hy4 was NOT activated" "$T/out"; then
  pass "hy4 failing: the trap stops Hy4 and brings DeepSeek back; openbeast.conf unchanged"
else fail "hy4 failure: $(calls) / $(conf_model) / $(cat "$T/out")"; fi
# ... from GLM: GLM comes back
reset; touch "$ST/$HY4U.installed" "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled" "$ST/fail-hy4"
if ! run hy4 && [[ "$(calls)" == "stop spark-hydra-inference.service;start $HY4U;stop $HY4U;start spark-hydra-inference.service" ]] \
   && [[ -f "$ST/spark-hydra-inference.service.active" ]]; then pass "hy4 failing from GLM: GLM is started again"
else fail "hy4 failure glm: $(calls) / $(cat "$T/out")"; fi
# ... the wrong model on :8000 is not "ready"
reset; touch "$ST/$HY4U.installed" "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled"; echo SomethingElse > "$ST/served-override"
if ! run hy4 && grep -q "Hy4-flash-NVFP4 is not on" "$T/out" && [[ "$(conf_model)" == '"DeepSeek-V4.1-Flash-1017"' ]] && ! grep -q "restart openbeast" "$LOG"; then
  pass "hy4: another id on :8000 is not ready; nothing written to openbeast.conf"
else fail "hy4 wrong id: $(calls) / $(cat "$T/out")"; fi

# not installed: refused before anything stops
reset; touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled"
if ! run hy4 && [[ -z "$(calls)" ]] && grep -q "spark-hy4-stage.sh" "$T/out"; then pass "hy4 without the unit installed: refused, nothing stopped"
else fail "hy4 not installed: $(calls) / $(cat "$T/out")"; fi

# already serving: only INFERENCE_MODEL / the command center when they lag
reset; touch "$ST/$HY4U.installed" "$ST/$HY4U.active"
if run hy4 && [[ "$(calls)" == "restart openbeast.service" ]] && [[ "$(conf_model)" == '"Hy4-flash-NVFP4"' ]] && grep -q "already serving" "$T/out"; then
  pass "hy4 while Hy4 serves: no restart of Hy4, INFERENCE_MODEL caught up"
else fail "hy4 no-op: $(calls) / $(cat "$T/out")"; fi

# ds / glm / up / down stop Hy4 when it runs
reset; touch "$ST/$HY4U.installed" "$ST/$HY4U.active" "$ST/deepseek-v41-inference.service.enabled"
if run ds && [[ "$(calls)" == "stop $HY4U;activate" ]]; then pass "ds from Hy4: Hy4 stopped, then activate-stock.sh"
else fail "ds from hy4: $(calls) / $(cat "$T/out")"; fi
reset; touch "$ST/$HY4U.installed" "$ST/$HY4U.active" "$ST/deepseek-v41-inference.service.enabled"
if run glm && [[ "$(calls)" == "stop $HY4U;restore-glm" ]]; then pass "glm from Hy4: Hy4 stopped, then restore-glm.sh"
else fail "glm from hy4: $(calls) / $(cat "$T/out")"; fi
reset; touch "$ST/$HY4U.installed" "$ST/$HY4U.active" "$ST/deepseek-v41-inference.service.enabled"
if run up && [[ "$(calls)" == "stop $HY4U;start deepseek-v41-inference.service;start openbeast.service" ]]; then
  pass "up with Hy4 running: Hy4 stopped, the SELECTED model (never Hy4) started"
else fail "up from hy4: $(calls) / $(cat "$T/out")"; fi
reset; touch "$ST/$HY4U.installed" "$ST/$HY4U.active"
if run up && [[ "$(calls)" == "stop $HY4U;activate" ]] && grep -q 'selecting DeepSeek' "$T/out"; then
  pass "up with nothing selected and Hy4 running: DeepSeek, the default (Hy4 is never picked)"
else fail "up none hy4: $(calls) / $(cat "$T/out")"; fi
# after a Hy4 session openbeast.conf names Hy4: `up` points it back at the selected model and RESTARTS the command center
reset; touch "$ST/$HY4U.installed" "$ST/$HY4U.active" "$ST/deepseek-v41-inference.service.enabled"
sed -i.bak -e 's/^INFERENCE_BACKEND=.*/INFERENCE_BACKEND="vllm"/' -e 's/^INFERENCE_MODEL=.*/INFERENCE_MODEL="Hy4-flash-NVFP4"/' "$T/openbeast.conf"
if run up && [[ "$(calls)" == "stop $HY4U;start deepseek-v41-inference.service;restart openbeast.service" ]] \
   && [[ "$(conf_model)" == '"DeepSeek-V4.1-Flash-1017"' && "$(conf_backend)" == '"tensorfold"' ]]; then
  pass "up after Hy4: openbeast.conf back to tensorfold + DeepSeek's id, the command center restarted to read it"
else fail "up conf back: $(calls) / $(cat "$T/openbeast.conf")"; fi
reset; touch "$ST/$HY4U.installed" "$ST/$HY4U.active" "$ST/openbeast.service.active"
if run down && [[ "$(calls)" == "stop openbeast.service;stop deepseek-v41-inference.service;stop spark-hydra-inference.service;stop $HY4U" ]]; then
  pass "down: Hy4 stopped too"
else fail "down hy4: $(calls)"; fi

# status: the Hy4 unit's row, and "not installed" before install
reset; touch "$ST/deepseek-v41-inference.service.enabled"
run status; s1=$(cat "$T/out")
touch "$ST/$HY4U.installed" "$ST/$HY4U.active"; run status; s2=$(cat "$T/out")
if grep -q "$HY4U.*not installed" <<<"$s1" && grep -q "$HY4U  *active / static" <<<"$s2" && grep -q 'serving on .*Hy4-flash-NVFP4' <<<"$s2" \
   && grep -q 'selected: ds' <<<"$s2"; then pass "status: Hy4's unit (not installed / active, static), the selection stays ds"
else fail "status hy4: $s1 / $s2"; fi

# aliases: the six lines for ~/.bashrc
want='alias beastup="$HOME/.local/bin/beast-spark up"
alias beastdown="$HOME/.local/bin/beast-spark down"
alias beastglm="$HOME/.local/bin/beast-spark glm"
alias beastds="$HOME/.local/bin/beast-spark ds"
alias beasthy4="$HOME/.local/bin/beast-spark hy4"
alias beaststatus="$HOME/.local/bin/beast-spark status"'
if [[ "$(bash "$SB" aliases | grep '^alias')" == "$want" ]]; then pass "aliases: the six beast* lines (beasthy4 among them)"; else fail "aliases: $(bash "$SB" aliases)"; fi

# install: beast-spark, the two pack tools, the aliases once
mkdir -p "$T/inst/prep"; echo "# mine" > "$T/inst/bashrc"
inst() { SB_BIN_DIR="$T/inst/bin" SB_PREP="$T/inst/prep" SB_BASHRC="$T/inst/bashrc" bash "$SB" install > "$T/out" 2>&1; }
if inst && inst && cmp -s "$SB" "$T/inst/bin/beast-spark" && [[ -x "$T/inst/bin/beast-spark" ]] \
   && cmp -s "$REPO_DIR/scripts/backends/spark-deepseek-pack.sh" "$T/inst/prep/spark-deepseek-pack.sh" \
   && cmp -s "$REPO_DIR/scripts/backends/spark-deepseek-prepare-local.sh" "$T/inst/prep/spark-deepseek-prepare-local.sh" \
   && [[ -x "$T/inst/prep/spark-deepseek-pack.sh" ]] && [[ "$(grep -c '^alias beast' "$T/inst/bashrc")" == 6 ]] && grep -qx "# mine" "$T/inst/bashrc" \
   && [[ -L "$T/inst/bin/beast-recipe" && "$(readlink "$T/inst/bin/beast-recipe")" == "$REPO_DIR/scripts/beast-recipe.sh" ]] \
   && "$T/inst/bin/beast-recipe" show --keys >/dev/null 2>&1; then
  pass "install (twice): beast-spark, the pack tools, the aliases once, and beast-recipe linked onto PATH (and it runs)"
else fail "install: $(cat "$T/out") / $(cat "$T/inst/bashrc")"; fi
# an older install (five aliases, no beasthy4): install adds only the missing line
{ echo "# mine"; bash "$SB" aliases | grep -v beasthy4; } > "$T/inst/bashrc"
if inst && [[ "$(grep -c '^alias beast' "$T/inst/bashrc")" == 6 && "$(grep -c beasthy4 "$T/inst/bashrc")" == 1 ]] && grep -q "aliases: 1 added" "$T/out"; then
  pass "install over an older install: beasthy4 added once, the other aliases not duplicated"
else fail "install older: $(cat "$T/out") / $(cat "$T/inst/bashrc")"; fi
cp "$SB" "$T/inst/lonely.sh"
if ! SB_BIN_DIR="$T/inst/bin2" bash "$T/inst/lonely.sh" install > "$T/out" 2>&1 && grep -q "run install from a checkout" "$T/out"; then
  pass "install: refused away from a checkout (the pack tools are not beside it)"
else fail "install lonely: $(cat "$T/out")"; fi

if bash "$SB" >/dev/null 2>&1; then fail "no subcommand should exit non-zero"; else pass "no subcommand: usage, non-zero exit"; fi
if ! bash "$SB" frobnicate > "$T/out" 2>&1 && grep -q "unknown command: frobnicate" "$T/out" && grep -q "beast-spark install" "$T/out" \
   && grep -q "beast-spark up" "$T/out"; then pass "an unknown subcommand: named in an error (with the install hint), then the usage, non-zero exit"
else fail "unknown subcommand: $(cat "$T/out")"; fi

# staleness: a checkout at $T/co, an install from it, then the checkout moves on
CO="$T/co/scripts/backends"; mkdir -p "$CO" "$T/st/prep"
cp "$SB" "$REPO_DIR/scripts/backends/spark-deepseek-pack.sh" "$REPO_DIR/scripts/backends/spark-deepseek-prepare-local.sh" "$CO/"
st() { SB_CHECKOUT="$T/co" SB_BIN_DIR="$T/st/bin" SB_PREP="$T/st/prep" SB_BASHRC="$T/st/bashrc" bash "$@" > "$T/out" 2>&1; }
reset; st "$CO/spark-beast.sh" install
st "$T/st/bin/beast-spark" status
if ! grep -q WARNING "$T/out"; then pass "staleness: no warning while the installed copy matches the checkout"
else fail "staleness, in sync: $(cat "$T/out")"; fi
echo "# a newer checkout" >> "$CO/spark-beast.sh"
st "$T/st/bin/beast-spark" status; s1=$(cat "$T/out")
st "$CO/spark-beast.sh" status; s2=$(cat "$T/out")
if grep -q "WARNING: this beast-spark ($T/st/bin/beast-spark) differs from the checkout" <<<"$s1" && grep -q "beast-spark install" <<<"$s1" \
   && grep -q "WARNING: the installed $T/st/bin/beast-spark .* differs from this checkout" <<<"$s2"; then
  pass "staleness: the installed copy and the checkout's differ: warned from either side, with the beast-spark install fix"
else fail "staleness, differ: $s1 / $s2"; fi
if st "$T/st/bin/beast-spark" install && grep -q "installing from the checkout" "$T/out" && cmp -s "$CO/spark-beast.sh" "$T/st/bin/beast-spark" \
   && st "$T/st/bin/beast-spark" status && ! grep -q WARNING "$T/out"; then
  pass "staleness: beast-spark install (the installed copy) updates from the checkout, and the warning is gone"
else fail "staleness, install: $(cat "$T/out")"; fi

# ---- an adopted beast-recipe recipe (beast-recipe adopt NAME --run writes $SB_RECIPES_D/NAME.env)
RU=beast-recipe-demo.service
mkdir -p "$T/recipes.d"
printf '# adopted\nUNIT=%s\nSERVED=demo-served\nBACKEND=tensorfold\nWAIT_S=10\n' "$RU" > "$T/recipes.d/demo.env"
printf 'UNIT=beast-recipe-other.service\nSERVED=other-served\nBACKEND=vllm\nWAIT_S=10\n' > "$T/recipes.d/other.env"
printf 'UNIT=$(touch %s/pwned).service\nSERVED=x\n' "$T" > "$T/recipes.d/evil.env"
reset; rm -f "$T/recipes.d/.previous"; touch "$ST/$RU.installed" "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled"
if run demo && [[ "$(calls)" == "stop deepseek-v41-inference.service;start $RU;restart openbeast.service" ]] \
   && [[ "$(conf_model)" == '"demo-served"' && "$(conf_backend)" == '"tensorfold"' ]] && [[ -f "$ST/deepseek-v41-inference.service.enabled" ]] \
   && grep -q '^PREVIOUS=ds$' "$T/recipes.d/.previous"; then
  pass "recipe from ds: DeepSeek stopped (still the boot selection), the recipe unit started and waited for, openbeast.conf set, .previous=ds"
else fail "recipe from ds: $(calls) / $(conf_model) / $(cat "$T/out")"; fi
# from GLM, whose restore-glm.sh leaves LLAMA_API_KEY empty: the keyed recipe gets the engine key
# (else beast-gate sends none and the native engine answers 401), and the key never reaches the output
reset; rm -f "$T/recipes.d/.previous"; touch "$ST/$RU.installed" "$ST/spark-hydra-inference.service.active"
printf 'LLAMA_API_KEY=\n' >> "$T/openbeast.conf"
if run demo && [[ "$(calls)" == "stop spark-hydra-inference.service;start $RU;restart openbeast.service" ]] \
   && [[ "$(sed -n 's/^LLAMA_API_KEY=//p' "$T/openbeast.conf")" == '"SECRETKEY123"' ]] \
   && [[ "$(grep -c '^LLAMA_API_KEY=' "$T/openbeast.conf")" == 1 ]] && grep -q 'LLAMA_API_KEY set from' "$T/out" \
   && ! grep -q SECRETKEY123 "$T/out"; then
  pass "recipe from glm: LLAMA_API_KEY (empty after restore-glm.sh) set from the key file, never printed"
else fail "recipe key sync: $(calls) / $(grep LLAMA "$T/openbeast.conf") / $(cat "$T/out")"; fi
reset; rm -f "$T/recipes.d/.previous"; touch "$ST/$RU.installed" "$ST/deepseek-v41-inference.service.active"
printf 'LLAMA_API_KEY="SECRETKEY123"\n' >> "$T/openbeast.conf"; cp "$T/openbeast.conf" "$T/conf.before"
if run demo && ! grep -q 'LLAMA_API_KEY set from' "$T/out" && [[ "$(grep LLAMA "$T/openbeast.conf")" == "$(grep LLAMA "$T/conf.before")" ]]; then
  pass "recipe with the key already in openbeast.conf: the line is left as it is"
else fail "recipe key unchanged: $(cat "$T/out")"; fi
# already serving: the key is synced and the command center restarted; with nothing to change it is only started
reset; touch "$ST/$RU.installed" "$ST/$RU.active"
printf 'INFERENCE_MODEL="demo-served"\nINFERENCE_BACKEND="tensorfold"\nLLAMA_API_KEY=\n' >> "$T/openbeast.conf"
if run demo && [[ "$(calls)" == "restart openbeast.service" ]] && [[ "$(sed -n 's/^LLAMA_API_KEY=//p' "$T/openbeast.conf")" == '"SECRETKEY123"' ]] \
   && run demo && [[ "$(calls)" == "restart openbeast.service;start openbeast.service" ]]; then
  pass "recipe already serving: key synced + command center restarted; again: only started"
else fail "recipe already serving: $(calls) / $(cat "$T/out")"; fi
reset; touch "$ST/$RU.installed" "$ST/spark-hydra-inference.service.active" "$ST/fail-recipe"
if ! run recipe demo && [[ "$(calls)" == "stop spark-hydra-inference.service;start $RU;stop $RU;start spark-hydra-inference.service" ]] \
   && [[ "$(conf_model)" == '"DeepSeek-V4.1-Flash-1017"' ]]; then
  pass "recipe failing (beast-spark recipe NAME): the trap stops it and brings GLM back; openbeast.conf unchanged"
else fail "recipe failure: $(calls) / $(cat "$T/out")"; fi
reset; touch "$ST/$RU.installed" "$ST/beast-recipe-other.service.installed" "$ST/beast-recipe-other.service.active"
if run demo && [[ "$(calls)" == "stop beast-recipe-other.service;start $RU;restart openbeast.service" ]] && grep -q '^PREVIOUS=other$' "$T/recipes.d/.previous"; then
  pass "recipe from another recipe: the other one stopped, .previous names it"
else fail "recipe from recipe: $(calls) / $(cat "$T/out")"; fi
reset; touch "$ST/$RU.installed" "$ST/$RU.active" "$ST/deepseek-v41-inference.service.enabled"
sed -i.bak -e 's/^INFERENCE_MODEL=.*/INFERENCE_MODEL="demo-served"/' "$T/openbeast.conf"
if run ds && [[ "$(calls)" == "stop $RU;activate" ]] && grep -q 'it pointed at the recipe demo' "$T/out"; then
  pass "ds from a recipe: the recipe unit stopped first, openbeast.conf pointed back"
else fail "ds from recipe: $(calls) / $(cat "$T/out")"; fi
reset; touch "$ST/deepseek-v41-inference.service.active"
if ! run demo && [[ -z "$(calls)" ]] && grep -q "beast-recipe up demo" "$T/out"; then pass "recipe whose unit is not installed: refused, nothing stopped"
else fail "recipe not installed: $(calls) / $(cat "$T/out")"; fi
# a recipe planned for another native TensorFold release: refused before anything is stopped
printf '#!/bin/bash\necho "tensorfold 1.2.10"\n' > "$T/tf-bin"; chmod +x "$T/tf-bin"
printf 'UNIT=%s\nSERVED=demo-served\nBACKEND=tensorfold\nWAIT_S=10\nTENSORFOLD_VERSION=1.3.3\n' "$RU" > "$T/recipes.d/demo.env"
reset; touch "$ST/$RU.installed" "$ST/deepseek-v41-inference.service.active"
if ! SB_TENSORFOLD_BIN="$T/tf-bin" run demo && [[ -z "$(calls)" ]] && grep -q "needs native TensorFold 1.3.3" "$T/out" \
   && grep -q "engine.sh install tensorfold --version 1.3.3" "$T/out" && [[ -f "$ST/deepseek-v41-inference.service.active" ]]; then
  pass "recipe pinned to another native TensorFold release: refused before anything is stopped"
else fail "recipe version gate: $(calls) / $(cat "$T/out")"; fi
printf 'UNIT=%s\nSERVED=demo-served\nBACKEND=tensorfold\nWAIT_S=10\nTENSORFOLD_VERSION=1.2.10\n' "$RU" > "$T/recipes.d/demo.env"
reset; rm -f "$T/recipes.d/.previous"; touch "$ST/$RU.installed" "$ST/deepseek-v41-inference.service.active"
if SB_TENSORFOLD_BIN="$T/tf-bin" run demo && [[ "$(calls)" == "stop deepseek-v41-inference.service;start $RU;restart openbeast.service" ]]; then
  pass "recipe whose native TensorFold release matches: switches as before"
else fail "recipe version match: $(calls) / $(cat "$T/out")"; fi
printf '# adopted\nUNIT=%s\nSERVED=demo-served\nBACKEND=tensorfold\nWAIT_S=10\n' "$RU" > "$T/recipes.d/demo.env"
if ! run evil && [[ ! -e "$T/pwned" ]] && ! run recipe nosuch && grep -q "no adopted recipe 'nosuch'" "$T/out"; then
  pass "a malformed registry file is ignored (never sourced, nothing run); an unknown recipe is refused"
else fail "recipe registry hygiene: $(cat "$T/out")"; fi
# hy4 while a recipe serves: the recipe is stopped first; a failed hy4 brings the recipe back
reset; touch "$ST/$HY4U.installed" "$ST/$RU.installed" "$ST/$RU.active" "$ST/fail-hy4"
sed -i.bak -e 's/^INFERENCE_MODEL=.*/INFERENCE_MODEL="demo-served"/' "$T/openbeast.conf"
if ! run hy4 && [[ "$(calls)" == "stop $RU;start $HY4U;stop $HY4U;start $RU" ]] && [[ -f "$ST/$RU.active" ]] \
   && [[ "$(conf_model)" == '"demo-served"' ]] && grep -q "demo is serving again; Hy4 was NOT activated" "$T/out"; then
  pass "hy4 from a recipe, failing: the recipe stopped first, then restored; openbeast.conf unchanged"
else fail "hy4 from recipe: $(calls) / $(conf_model) / $(cat "$T/out")"; fi
reset; touch "$ST/$HY4U.installed" "$ST/$RU.installed" "$ST/$RU.active"
if run hy4 && [[ "$(calls)" == "stop $RU;start $HY4U;restart openbeast.service" ]] && [[ ! -f "$ST/$RU.active" ]]; then
  pass "hy4 from a recipe: the recipe stopped before Hy4 starts (not left to Conflicts=)"
else fail "hy4 from recipe ok: $(calls) / $(cat "$T/out")"; fi
# a switch from nothing records PREVIOUS=none (rollback is then beast-spark down)
reset; rm -f "$T/recipes.d/.previous"; touch "$ST/$RU.installed"
if run demo && grep -q '^PREVIOUS=none$' "$T/recipes.d/.previous"; then pass "recipe from nothing: .previous=none"
else fail "previous none: $(cat "$T/recipes.d/.previous" 2>&1) / $(cat "$T/out")"; fi
reset; touch "$ST/$RU.installed" "$ST/$RU.active"
run status
if grep -q "$RU  *active (recipe demo)" "$T/out"; then pass "status lists the adopted recipes' units"; else fail "status recipes: $(cat "$T/out")"; fi

# ---- the command-center restart after a switch (2026-11-23: its stop hung 90 s, the restart
# failed, `beast-spark glm` exited 1 with GLM serving and a recipe switch rolled back)
CC_RETRY="restart openbeast.service;stop openbeast.service;reset-failed openbeast.service openbeast-stack.service;start openbeast.service"
# a recipe switch whose command-center restart fails: retried from a clean stop, NO rollback
reset; rm -f "$T/recipes.d/.previous"; touch "$ST/$RU.installed" "$ST/deepseek-v41-inference.service.active" "$ST/fail-cc-restart"
if run demo && [[ "$(calls)" == "stop deepseek-v41-inference.service;start $RU;$CC_RETRY" ]] \
   && [[ -f "$ST/$RU.active" ]] && [[ "$(conf_model)" == '"demo-served"' ]] && grep -q '^PREVIOUS=ds$' "$T/recipes.d/.previous" \
   && grep -q "the command center is up (second try)" "$T/out" && ! grep -q "NOT activated" "$T/out"; then
  pass "recipe: a failed command-center restart is retried (stop, reset-failed, start); the switch stands"
else fail "recipe cc retry: $(calls) / $(cat "$T/out")"; fi
# ... and when the command center does not come up at all: a warning, exit 3 (the model serves,
# beast-gate is down), still no rollback
reset; touch "$ST/$RU.installed" "$ST/deepseek-v41-inference.service.active" "$ST/fail-cc-restart" "$ST/fail-cc-start"
run demo; rc=$?
if (( rc == 3 )) && [[ -f "$ST/$RU.active" ]] && [[ ! -f "$ST/deepseek-v41-inference.service.active" ]] \
   && grep -q "WARNING: the command center did not start; the model serves and the switch stands" "$T/out" \
   && grep -q "systemctl --user start openbeast" "$T/out" && ! grep -q "start deepseek" "$LOG"; then
  pass "recipe: a command center that will not start is a warning with exit 3 (partial), never a rollback of a serving model"
else fail "recipe cc down (exit $rc): $(calls) / $(cat "$T/out")"; fi
# hy4 the same
reset; touch "$ST/$HY4U.installed" "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled" "$ST/fail-cc-restart"
if run hy4 && [[ "$(calls)" == "stop deepseek-v41-inference.service;start $HY4U;$CC_RETRY" ]] \
   && [[ -f "$ST/$HY4U.active" ]] && [[ "$(conf_model)" == '"Hy4-flash-NVFP4"' ]] && ! grep -q "NOT activated" "$T/out"; then
  pass "hy4: a failed command-center restart is retried; Hy4 is not rolled back"
else fail "hy4 cc retry: $(calls) / $(cat "$T/out")"; fi
# beastup: a failed command-center start is retried too, then a warning
reset; glm_conf; touch "$ST/spark-hydra-inference.service.enabled" "$ST/fail-cc-start"
run up; rc=$?
if (( rc == 3 )) && [[ "$(calls)" == "start spark-hydra-inference.service;start openbeast.service;stop openbeast.service;reset-failed openbeast.service openbeast-stack.service;start openbeast.service" ]] \
   && grep -q "WARNING: the command center did not start" "$T/out"; then
  pass "up: a failed command-center start is retried once, then a warning (exit 3: the model serves, the command center is down)"
else fail "up cc retry (exit $rc): $(calls) / $(cat "$T/out")"; fi
# beastglm: restore-glm.sh failing at its LAST step (the command-center restart) with GLM
# selected and serving: exit 0, the command center restarted by beast-spark
reset; touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled"
cp "$T/prep/rollback/restore-glm.sh" "$T/restore-glm.ok"
printf '%s\nexit 1\n' "$(cat "$T/restore-glm.ok")" > "$T/prep/rollback/restore-glm.sh"
if run glm && [[ "$(calls)" == "restore-glm;restart openbeast.service" ]] \
   && grep -q "WARNING: restore-glm.sh exited 1, but GLM is selected and running" "$T/out" && grep -q 'selected: glm' "$T/out"; then
  pass "glm: restore-glm.sh failing after GLM serves is not a failed switch (exit 0, the command center restarted)"
else fail "glm cc: $(calls) / $(cat "$T/out")"; fi
# ... but a restore-glm.sh that fails BEFORE GLM is selected and running still fails the switch
reset; touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled"
printf 'echo restore-glm >> "$LOG"; exit 1\n' > "$T/prep/rollback/restore-glm.sh"
if ! run glm && [[ "$(calls)" == restore-glm ]] && grep -q "restore-glm.sh failed (exit 1) and GLM is not selected and running" "$T/out"; then
  pass "glm: restore-glm.sh failing before GLM runs: exit non-zero, nothing papered over"
else fail "glm early failure: $(calls) / $(cat "$T/out")"; fi
cp "$T/restore-glm.ok" "$T/prep/rollback/restore-glm.sh"

# beastds: activate-stock.sh (spark-1's copy) restarts the command center BEFORE it enables
# DeepSeek and disables GLM, under set -e. When that restart fails, DeepSeek serves and
# use-model.sh has written openbeast.conf: beast-spark finishes the enablement itself, restarts
# the command center (cc_up), exits 0, and never rolls back to GLM.
cp "$T/prep/activate-stock.sh" "$T/activate-stock.ok"
cat > "$T/prep/activate-stock.sh" <<'S'
echo activate >> "$LOG"
touch "$ST/deepseek-v41-inference.service.active"
sleep 1.1   # the stamp check is -nt; one-second mtimes on some filesystems
[[ -f "$ST/no-use-model" ]] || printf 'OK   conformance PASSED\nwrote INFERENCE_MODEL="DeepSeek-V4.1-Flash-1017" -> openbeast.conf\n' > "$SB_PREP/use-model.log"
exit 1   # systemctl --user restart openbeast.service failed; enable/disable never ran
S
reset; rm -f "$T/prep/use-model.log"; touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled"
printf 'LLAMA_API_KEY=\n' >> "$T/openbeast.conf"
if run ds && [[ "$(calls)" == "stop spark-hydra-inference.service;activate;enable deepseek-v41-inference.service;disable spark-hydra-inference.service;restart openbeast.service" ]] \
   && [[ -f "$ST/deepseek-v41-inference.service.enabled" && ! -f "$ST/spark-hydra-inference.service.enabled" ]] \
   && [[ "$(sed -n 's/^LLAMA_API_KEY=//p' "$T/openbeast.conf")" == '"SECRETKEY123"' ]] \
   && grep -q "WARNING: activate-stock.sh exited 1 after the canary" "$T/out" && ! grep -q "restore-glm" "$LOG" \
   && grep -q 'selected: ds' "$T/out"; then
  pass "ds: activate-stock.sh failing at its command-center restart: DeepSeek enabled, GLM disabled, key synced, cc restarted, no rollback"
else fail "ds cc: $(calls) / $(cat "$T/out")"; fi
# ... the same with the command center down for good: exit 3, DeepSeek still selected
reset; rm -f "$T/prep/use-model.log"; touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled" "$ST/fail-cc-restart" "$ST/fail-cc-start"
run ds; rc=$?
if (( rc == 3 )) && [[ -f "$ST/deepseek-v41-inference.service.enabled" ]] && ! grep -q "restore-glm" "$LOG"; then
  pass "ds: the command center down after DeepSeek is activated: exit 3, no rollback"
else fail "ds cc down (exit $rc): $(calls) / $(cat "$T/out")"; fi
# ... but a failure BEFORE use-model.sh wrote openbeast.conf (canary, conformance) still rolls back to GLM,
# even though DeepSeek answers
reset; rm -f "$T/prep/use-model.log"; touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled" "$ST/no-use-model"
if ! run ds && [[ "$(calls)" == "stop spark-hydra-inference.service;activate;restore-glm" ]] && grep -q "GLM is serving again" "$T/out" \
   && grep -q "before DeepSeek was activated" "$T/out"; then
  pass "ds: activate-stock.sh failing before openbeast.conf (canary/conformance): GLM restored, non-zero"
else fail "ds early failure: $(calls) / $(cat "$T/out")"; fi
# ... and a use-model.log left by an EARLIER run does not count
reset; touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled" "$ST/no-use-model"
printf 'wrote INFERENCE_MODEL="DeepSeek-V4.1-Flash-1017"\n' > "$T/prep/use-model.log"; touch -t 202001010000 "$T/prep/use-model.log"
if ! run ds && [[ "$(calls)" == "stop spark-hydra-inference.service;activate;restore-glm" ]]; then
  pass "ds: a stale use-model.log (an earlier run) is not taken for this run's activation"
else fail "ds stale use-model.log: $(calls) / $(cat "$T/out")"; fi
cp "$T/activate-stock.ok" "$T/prep/activate-stock.sh"; rm -f "$T/prep/use-model.log"

# beasthy4 while Hy4 already serves (as for a recipe): the key is synced and the command center
# restarted; with nothing to change it is only started (it used to be left as it was)
reset; touch "$ST/$HY4U.installed" "$ST/$HY4U.active"
printf 'INFERENCE_MODEL="Hy4-flash-NVFP4"\nINFERENCE_BACKEND="vllm"\nLLAMA_API_KEY=\n' >> "$T/openbeast.conf"
if run hy4 && [[ "$(calls)" == "restart openbeast.service" ]] && [[ "$(sed -n 's/^LLAMA_API_KEY=//p' "$T/openbeast.conf")" == '"SECRETKEY123"' ]] \
   && run hy4 && [[ "$(calls)" == "restart openbeast.service;start openbeast.service" ]]; then
  pass "hy4 already serving: key synced + command center restarted; again: only started"
else fail "hy4 already serving: $(calls) / $(cat "$T/out")"; fi

# ---- 2026-11-24 review fixes
# wait_ready: a unit that is still activating (start, or Restart=on-failure's auto-restart after a rank
# crash) is waited for up to the limit, not declared dead at once (is-activating is not a verb)
reset; glm_conf; touch "$ST/spark-hydra-inference.service.enabled" "$ST/start-activating"
run up; rc=$?
if (( rc != 0 )) && grep -q "GLM-5.3-Flash-EXL3 is not on" "$T/out" && ! grep -q "is not running" "$T/out" && grep -q "waiting for spark-hydra" "$T/out"; then
  pass "wait_ready: an activating unit is waited for until the limit (ActiveState, not a nonexistent verb)"
else fail "wait_ready activating (rc $rc): $(cat "$T/out")"; fi
# ... and a unit that is neither active nor activating still fails fast
reset; glm_conf; touch "$ST/spark-hydra-inference.service.enabled" "$ST/fail-glm"
if ! run up && grep -q "spark-hydra-inference.service is not running" "$T/out"; then
  pass "wait_ready: a unit that is not running at all still fails at once"
else fail "wait_ready dead: $(cat "$T/out")"; fi

# ds failing, GLM restored by restore-glm.sh but never serving: the trap logs the guidance and keeps rc
reset Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw
touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled" "$ST/fail-activate" "$ST/glm-no-come-back"
run ds; rc=$?
if (( rc == 1 )) && grep -q "GLM did not come back either" "$T/out" && grep -q "activate-stock.sh failed" "$T/out"; then
  pass "ds rollback: GLM not coming back prints the guidance (wait_ready in a subshell inside the trap)"
else fail "ds_exit wait_ready (rc $rc): $(cat "$T/out")"; fi

# INFERENCE_SLOTS follows every switch: ds 8 (local.sh PARALLEL) -> glm 4 -> hy4 (HY4_SLOTS=3) -> recipe (SLOTS=6) -> ds 8
printf 'UNIT=%s\nSERVED=demo-served\nBACKEND=tensorfold\nWAIT_S=10\nSLOTS=6\n' "$RU" > "$T/recipes.d/demo.env"
printf 'HY4_SLOTS=3\n' > "$T/beast-spark.env"
reset; touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled" "$ST/$HY4U.installed" "$ST/$RU.installed"
seq=""
run glm && seq+="glm=$(conf_slots) "
run hy4 && seq+="hy4=$(conf_slots) "
run demo && seq+="demo=$(conf_slots) "
run ds && seq+="ds=$(conf_slots)"
if [[ "$seq" == "glm=4 hy4=3 demo=6 ds=8" ]]; then pass "slots: ds -> glm 4 -> hy4 3 -> recipe 6 -> ds 8 (every switch writes INFERENCE_SLOTS)"
else fail "slots sequence: '$seq' / $(cat "$T/out")"; fi
# a failed hy4 switch puts INFERENCE_SLOTS back with the model
reset; touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled" "$ST/$HY4U.installed" "$ST/fail-hy4"
if ! run hy4 && [[ "$(conf_slots)" == 8 && "$(conf_model)" == '"DeepSeek-V4.1-Flash-1017"' ]]; then pass "slots: a failed hy4 switch leaves INFERENCE_SLOTS as it was"
else fail "slots hy4 fail: $(conf_slots) / $(cat "$T/out")"; fi
# up after a Hy4 session on GLM: model AND slots back at GLM's
reset; glm_conf; touch "$ST/spark-hydra-inference.service.enabled" "$ST/$HY4U.installed"
sed -i.bak -e 's/^INFERENCE_MODEL=.*/INFERENCE_MODEL="Hy4-flash-NVFP4"/' -e 's/^INFERENCE_SLOTS=.*/INFERENCE_SLOTS=3/' "$T/openbeast.conf"
if run up && [[ "$(conf_slots)" == 4 && "$(conf_model)" == '"GLM-5.3-Flash-EXL3"' ]] && [[ "$(calls)" == "start spark-hydra-inference.service;restart openbeast.service" ]]; then
  pass "up after Hy4 on GLM: openbeast.conf back at GLM's id and 4 slots, the command center restarted"
else fail "up slots: $(conf_slots) $(conf_model) $(calls) / $(cat "$T/out")"; fi
rm -f "$T/beast-spark.env"
printf '# adopted\nUNIT=%s\nSERVED=demo-served\nBACKEND=tensorfold\nWAIT_S=10\n' "$RU" > "$T/recipes.d/demo.env"

# boot-check (openbeast.service ExecStartPre): after a reboot the enabled DeepSeek serves, openbeast.conf
# still names Hy4: pointed back (backend, id, slots), and nothing is started or stopped
reset; touch "$ST/deepseek-v41-inference.service.enabled" "$ST/$HY4U.installed"
sed -i.bak -e 's/^INFERENCE_BACKEND=.*/INFERENCE_BACKEND="vllm"/' -e 's/^INFERENCE_MODEL=.*/INFERENCE_MODEL="Hy4-flash-NVFP4"/' -e 's/^INFERENCE_SLOTS=.*/INFERENCE_SLOTS=4/' "$T/openbeast.conf"
if run boot-check && [[ "$(calls)" == "" && "$(conf_model)" == '"DeepSeek-V4.1-Flash-1017"' && "$(conf_backend)" == '"tensorfold"' && "$(conf_slots)" == 8 ]]; then
  pass "boot-check: openbeast.conf naming a Hy4 that does not run is pointed back at DeepSeek (id, tensorfold, 8 slots); no unit touched"
else fail "boot-check: $(calls) / $(conf_model) $(conf_backend) $(conf_slots) / $(cat "$T/out")"; fi
# ... the same for a recipe that does not run, with GLM selected
reset; glm_conf; touch "$ST/spark-hydra-inference.service.enabled"
sed -i.bak -e 's/^INFERENCE_MODEL=.*/INFERENCE_MODEL="demo-served"/' "$T/openbeast.conf"
if run boot-check && [[ "$(conf_model)" == '"GLM-5.3-Flash-EXL3"' && "$(conf_slots)" == 4 && "$(calls)" == "" ]]; then pass "boot-check: a stale recipe id goes back to the selected GLM"
else fail "boot-check recipe: $(conf_model) / $(cat "$T/out")"; fi
# ... but not while Hy4 serves (a restart of the command center during a Hy4 session), and not during a switch
reset; touch "$ST/deepseek-v41-inference.service.enabled" "$ST/$HY4U.installed" "$ST/$HY4U.active"
sed -i.bak -e 's/^INFERENCE_MODEL=.*/INFERENCE_MODEL="Hy4-flash-NVFP4"/' "$T/openbeast.conf"; cp "$T/openbeast.conf" "$T/conf.before"
if run boot-check && cmp -s "$T/openbeast.conf" "$T/conf.before"; then pass "boot-check: leaves openbeast.conf alone while Hy4 serves"
else fail "boot-check hy4 active: $(cat "$T/out")"; fi
rm -f "$ST/$HY4U.active"; touch "$ST/lock-busy"
if run boot-check && cmp -s "$T/openbeast.conf" "$T/conf.before" && grep -q "a model switch holds" "$T/out"; then pass "boot-check: skipped while a switch holds the lock"
else fail "boot-check lock: $(cat "$T/out")"; fi
rm -f "$ST/lock-busy"
# a conf that already names the selection: untouched
reset; touch "$ST/deepseek-v41-inference.service.enabled"; cp "$T/openbeast.conf" "$T/conf.before"
if run boot-check && cmp -s "$T/openbeast.conf" "$T/conf.before"; then pass "boot-check: a conf at the selection is untouched"
else fail "boot-check noop: $(cat "$T/out")"; fi

# install wires boot-check into openbeast.service (a drop-in), once
rm -rf "$T/units"
if inst && grep -qx "ExecStartPre=-$T/inst/bin/beast-spark boot-check" "$T/units/openbeast.service.d/beast-spark.conf" \
   && inst && grep -q "unchanged: $T/units/openbeast.service.d/beast-spark.conf" "$T/out"; then
  pass "install: the boot-check drop-in for openbeast.service (ExecStartPre=-…), unchanged the second time"
else fail "install drop-in: $(cat "$T/out") / $(cat "$T/units/openbeast.service.d/beast-spark.conf" 2>&1)"; fi

echo "=== $PASS passed, $FAIL failed ==="
(( FAIL == 0 ))
