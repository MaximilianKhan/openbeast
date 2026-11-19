#!/bin/bash
# spark-beast.sh (beastup / beastdown / beastglm / beastds / beaststatus):
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
       SB_WAIT_S=10 SB_LOCK="$T/model.lock" ST="$T/state" LOG="$T/log"
echo SECRETKEY123 > "$T/key"
# systemctl --user: state files $ST/<unit>.active / .enabled; every mutating call logged.
cat > "$T/bin/systemctl" <<'S'
#!/bin/bash
shift  # --user
op=$1; shift
case "$op" in
  is-enabled) [[ -f "$ST/$1.enabled" ]] && { echo enabled; exit 0; }; echo disabled; exit 1 ;;
  is-active) q=0; [[ "$1" == --quiet ]] && { q=1; shift; }
             [[ -f "$ST/$1.active" ]] && { ((q)) || echo active; exit 0; }; ((q)) || echo inactive; exit 3 ;;
  is-activating) exit 1 ;;
  start) for u; do echo "start $u" >> "$LOG"; touch "$ST/$u.active"; done ;;
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
reset() { rm -f "$ST"/* "$LOG"; touch "$LOG"; echo "${1:-$V2}" > "$ST/pack"; }
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
sed -i.bak 's/^  start) for u; do echo "start $u" >> "$LOG"; touch "$ST\/$u.active"; done ;;/  start) for u; do echo "start $u" >> "$LOG"; done ;;/' "$T/bin/systemctl"
if ! run up && grep -q 'is not running' "$T/out"; then pass "up: a unit that does not come up fails fast"
else fail "up dead unit: $(cat "$T/out")"; fi

# aliases: the five lines for ~/.bashrc
want='alias beastup="$HOME/.local/bin/beast-spark up"
alias beastdown="$HOME/.local/bin/beast-spark down"
alias beastglm="$HOME/.local/bin/beast-spark glm"
alias beastds="$HOME/.local/bin/beast-spark ds"
alias beaststatus="$HOME/.local/bin/beast-spark status"'
if [[ "$(bash "$SB" aliases | grep '^alias')" == "$want" ]]; then pass "aliases: the five beast* lines"; else fail "aliases: $(bash "$SB" aliases)"; fi

# install: beast-spark, the two pack tools, the aliases once
mkdir -p "$T/inst/prep"; echo "# mine" > "$T/inst/bashrc"
inst() { SB_BIN_DIR="$T/inst/bin" SB_PREP="$T/inst/prep" SB_BASHRC="$T/inst/bashrc" bash "$SB" install > "$T/out" 2>&1; }
if inst && inst && cmp -s "$SB" "$T/inst/bin/beast-spark" && [[ -x "$T/inst/bin/beast-spark" ]] \
   && cmp -s "$REPO_DIR/scripts/backends/spark-deepseek-pack.sh" "$T/inst/prep/spark-deepseek-pack.sh" \
   && cmp -s "$REPO_DIR/scripts/backends/spark-deepseek-prepare-local.sh" "$T/inst/prep/spark-deepseek-prepare-local.sh" \
   && [[ -x "$T/inst/prep/spark-deepseek-pack.sh" ]] && [[ "$(grep -c '^alias beast' "$T/inst/bashrc")" == 5 ]] && grep -qx "# mine" "$T/inst/bashrc"; then
  pass "install (twice): beast-spark, the pack tools and the aliases in place, aliases once"
else fail "install: $(cat "$T/out") / $(cat "$T/inst/bashrc")"; fi
cp "$SB" "$T/inst/lonely.sh"
if ! SB_BIN_DIR="$T/inst/bin2" bash "$T/inst/lonely.sh" install > "$T/out" 2>&1 && grep -q "run install from a checkout" "$T/out"; then
  pass "install: refused away from a checkout (the pack tools are not beside it)"
else fail "install lonely: $(cat "$T/out")"; fi

if bash "$SB" >/dev/null 2>&1; then fail "no subcommand should exit non-zero"; else pass "no subcommand: usage, non-zero exit"; fi

echo "=== $PASS passed, $FAIL failed ==="
(( FAIL == 0 ))
