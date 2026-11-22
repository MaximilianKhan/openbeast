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
mkdir -p "$T/bin" "$T/prep/rollback" "$T/state" "$T/pack"
V2=Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1.1-2.9-bpw-v2
export SB_PREP="$T/prep" SB_DS_PACK="$T/pack" SB_DS_ID="$V2" SB_CONF="$T/beast-spark.env" SB_KEY_FILE="$T/key" \
       SB_WAIT_S=10 SB_HY4_WAIT_S=10 SB_LOCK="$T/model.lock" SB_OB_CONF="$T/openbeast.conf" ST="$T/state" LOG="$T/log" \
       SB_BIN_DIR="$T/nobin" SB_CHECKOUT="$T/nocheckout"
echo SECRETKEY123 > "$T/key"
# systemctl --user: state files $ST/<unit>.active / .enabled; every mutating call logged.
cat > "$T/bin/systemctl" <<'S'
#!/bin/bash
shift  # --user
op=$1; shift
case "$op" in
  is-enabled) [[ -f "$ST/$1.enabled" ]] && { echo enabled; exit 0; }
              [[ -f "$ST/$1.installed" ]] && { echo static; exit 0; }; echo disabled; exit 1 ;;
  show) u=${!#}; [[ -f "$ST/$u.installed" ]] && echo loaded || echo not-found ;;
  restart) for u; do echo "restart $u" >> "$LOG"; done ;;
  is-active) q=0; [[ "$1" == --quiet ]] && { q=1; shift; }
             [[ -f "$ST/$1.active" ]] && { ((q)) || echo active; exit 0; }; ((q)) || echo inactive; exit 3 ;;
  is-activating) exit 1 ;;
  start) for u; do echo "start $u" >> "$LOG"; [[ "$u" == hy4-vllm-inference.service && -f "$ST/fail-hy4" ]] || touch "$ST/$u.active"; done ;;
  stop) for u; do echo "stop $u" >> "$LOG"; rm -f "$ST/$u.active"; done ;;
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
touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled"
S
chmod +x "$T"/bin/* "$T/prep/spark-deepseek-pack.sh"
export PATH="$T/bin:$PATH"
reset() { rm -f "$ST"/* "$LOG"; touch "$LOG"; echo "${1:-$V2}" > "$ST/pack"
  printf '# conf\nINFERENCE_BACKEND=tensorfold\nINFERENCE_THINKING=on\nINFERENCE_MODEL="DeepSeek-V4.1-Flash-1017"\nAGENT_PROFILE=deepseek-best\n' > "$T/openbeast.conf"; }
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
reset; touch "$ST/spark-hydra-inference.service.enabled"
if run up && [[ "$(calls)" == "start spark-hydra-inference.service;start openbeast.service" ]]; then pass "up: selected GLM first, then the command center"
else fail "up glm: $(calls) / $(cat "$T/out")"; fi

# beastup with the OTHER model still running: it is stopped first (both need both GPUs)
reset; touch "$ST/spark-hydra-inference.service.enabled" "$ST/deepseek-v41-inference.service.active"
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
# systemctl start no longer brings the unit up
sed -i.bak 's/^  start) for u; do echo "start $u" >> "$LOG"; .*/  start) for u; do echo "start $u" >> "$LOG"; done ;;/' "$T/bin/systemctl"
if ! run up && grep -q 'is not running' "$T/out"; then pass "up: a unit that does not come up fails fast"
else fail "up dead unit: $(cat "$T/out")"; fi
mv -f "$T/bin/systemctl.bak" "$T/bin/systemctl"   # units come up again

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
   && [[ -x "$T/inst/prep/spark-deepseek-pack.sh" ]] && [[ "$(grep -c '^alias beast' "$T/inst/bashrc")" == 6 ]] && grep -qx "# mine" "$T/inst/bashrc"; then
  pass "install (twice): beast-spark, the pack tools and the aliases in place, aliases once"
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

echo "=== $PASS passed, $FAIL failed ==="
(( FAIL == 0 ))
