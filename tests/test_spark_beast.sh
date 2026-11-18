#!/bin/bash
# spark-beast.sh (beastup / beastdown / beastglm / beastds): which helpers each
# command calls, in which order. Hermetic: systemctl, curl and the installed
# helpers (spark-deepseek-pack.sh, activate-stock.sh, restore-glm.sh) are
# recording stubs over a scratch unit state; nothing touches a real unit.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SB="$REPO_DIR/scripts/backends/spark-beast.sh"
PASS=0 FAIL=0
pass() { echo "  PASS: $*"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
echo "=== spark-beast tests ==="

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/prep/rollback" "$T/state" "$T/pack"
export SB_PREP="$T/prep" SB_DS_PACK="$T/pack" SB_KEY_FILE="$T/key" SB_WAIT_S=10 ST="$T/state" LOG="$T/log"
echo k > "$T/key"
V2=Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1.1-2.9-bpw-v2
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
# curl: a model is served while either inference unit is active.
cat > "$T/bin/curl" <<'S'
#!/bin/bash
[[ -f "$ST/deepseek-v41-inference.service.active" || -f "$ST/spark-hydra-inference.service.active" ]] || exit 7
echo '{"data":[{"id":"M"}]}'
S
cat > "$T/prep/spark-deepseek-pack.sh" <<'S'
#!/bin/bash
if [[ "$1" == list ]]; then echo "serving (local.sh): $(cat "$ST/pack") @ 04c13f12   rank 1 cache: x"; exit 0; fi
echo "pack $*" >> "$LOG"; echo Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1.1-2.9-bpw-v2 > "$ST/pack"
S
cat > "$T/prep/activate-stock.sh" <<'S'
echo activate >> "$LOG"; touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled"
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

# beastds from GLM with the old pack selected: stop GLM, pack use -v2, activate
reset Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw
touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled"
if run ds && [[ "$(calls)" == "stop spark-hydra-inference.service;pack use $T/pack;activate" ]] && grep -q "pack $V2@04c13f12" "$T/out"; then
  pass "ds from glm on the old pack: stop GLM, select -v2, activate"
else fail "ds from glm: $(calls) / $(cat "$T/out")"; fi

# beastds from GLM with -v2 already selected: no pack step
reset; touch "$ST/spark-hydra-inference.service.active" "$ST/spark-hydra-inference.service.enabled"
if run ds && [[ "$(calls)" == "stop spark-hydra-inference.service;activate" ]]; then pass "ds from glm on -v2: no pack swap"
else fail "ds on -v2: $(calls)"; fi

# beastds while -v2 serves: a no-op apart from making sure the command center runs
reset; touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled"
if run ds && [[ "$(calls)" == "start openbeast.service" ]] && grep -q 'already selected and serving' "$T/out"; then pass "ds while serving -v2: no-op"
else fail "ds no-op: $(calls)"; fi

# beastdown: command center first, both units, selection kept
reset; touch "$ST/deepseek-v41-inference.service.active" "$ST/deepseek-v41-inference.service.enabled" "$ST/openbeast.service.active"
if run down && [[ "$(calls)" == "stop openbeast.service;stop deepseek-v41-inference.service;stop spark-hydra-inference.service" ]] \
   && [[ -f "$ST/deepseek-v41-inference.service.enabled" ]] && grep -q 'selected: ds' "$T/out"; then pass "down: command center, then both units; selection kept"
else fail "down: $(calls)"; fi

# beastup: the selected unit, then the command center
reset; touch "$ST/spark-hydra-inference.service.enabled"
if run up && [[ "$(calls)" == "start spark-hydra-inference.service;start openbeast.service" ]]; then pass "up: selected GLM first, then the command center"
else fail "up glm: $(calls) / $(cat "$T/out")"; fi

# beastup with nothing selected: DeepSeek
reset
if run up && [[ "$(calls)" == activate* ]]; then pass "up with no selection: DeepSeek"
else fail "up none: $(calls)"; fi

# a unit that dies while starting is reported, not waited on forever
reset; touch "$ST/deepseek-v41-inference.service.enabled"
# systemctl start no longer brings the unit up
sed -i.bak 's/^  start) for u; do echo "start $u" >> "$LOG"; touch "$ST\/$u.active"; done ;;/  start) for u; do echo "start $u" >> "$LOG"; done ;;/' "$T/bin/systemctl"
if ! run up && grep -q 'is not running' "$T/out"; then pass "up: a unit that does not come up fails fast"
else fail "up dead unit: $(cat "$T/out")"; fi

if bash "$SB" >/dev/null 2>&1; then fail "no subcommand should exit non-zero"; else pass "no subcommand: usage, non-zero exit"; fi

echo "=== $PASS passed, $FAIL failed ==="
(( FAIL == 0 ))
