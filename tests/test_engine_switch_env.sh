#!/bin/bash
# The engine switch must restart the stack on the NEW conf, not on the
# environment the switching command inherited from the OLD one.
#
# Rig, 2026-11-25: `scripts/engine.sh use default` wrote INFERENCE_BACKEND=
# basalt (managed, ENGINE_PROFILE=inkling2-mini-stock) to openbeast.conf and
# restarted the running stack, and start.sh launched the OLD llama
# SERVE_SCRIPT (Qwen) on :8080 under the Inkling profile. engine.sh had
# sourced lib/conf.sh before the rewrite; conf.sh exported
# OPENBEAST_INFERENCE_BACKEND=llama (and _MANAGED/_URL/_SLOTS/_MODEL) as
# plain exports, and the restarted start.sh read them back as operator
# overrides (env beats the file). A plain `./start.sh -d` from a fresh shell
# was right, which is how it was found.
#
# Two locks, both tested here:
#   1. conf.sh: those five go through _ob_override/_ob_derive, so a child
#      that re-sources it reads the conf again (what engine-gate.sh's
#      candidate start and any script that sources conf.sh and rewrites the
#      conf rely on); a real operator export still wins.
#   2. engine.sh: the restart runs start.sh with the environment engine.sh
#      was started with (no conf.sh exports, no OB_CONF_READONLY), keeping
#      the caller's own variables (the systemd user session).
#
# Hermetic: a sandbox copy of scripts/, a stub start/stop, no GPU, no docker,
# no network. Linux and macOS (bash 3.2).
#
# Usage: bash tests/test_engine_switch_env.sh
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
check() { if eval "$2"; then pass "$1"; else fail "$1${3:+ — $3}"; fi; }

T="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/obswitchenvXXXXXX")" && pwd)"
_SUP=""
cleanup() { [[ -n "$_SUP" ]] && kill "$_SUP" 2>/dev/null || true; rm -rf "$T"; }
trap cleanup EXIT

SB="$T/repo"
mkdir -p "$SB/.run" "$T/bin" "$T/home"
cp -R "$REPO_DIR/scripts" "$SB/scripts"
rm -f "$SB/scripts/backends/spark.env"
cp "$REPO_DIR/openbeast.conf.example" "$SB/" 2>/dev/null || true
for c in nvidia-smi systemctl systemd-run sudo docker; do
  printf '#!/bin/bash\nexit 1\n' > "$T/bin/$c"; chmod +x "$T/bin/$c"
done
PY="$(command -v python3)"
BASE_ENV=(HOME="$T/home" PATH="$T/bin:$(dirname "$PY"):/usr/bin:/bin" OPENBEAST_SEARXNG_SECRET=x
  OPENBEAST_GPU_BACKEND=cpu OPENBEAST_LOGROTATE_AUTOINSTALL=false
  OB_ENGINE_HOST_CLASS=rig OB_ENGINE_UNAME=Linux OB_ENGINE_ARCH=x86_64
  OB_ENGINE_GPU_NAME="NVIDIA GeForce RTX 5090")

# The rig's conf before the switch: llama with the Qwen launcher.
llama_conf() {
  printf 'INFERENCE_BACKEND=llama\nSERVE_SCRIPT=serve-qwen38-27b-uncensored-mtp-q5.sh\nAGENT_PROFILE=qwen38-best\n' > "$SB/openbeast.conf"
  chmod 600 "$SB/openbeast.conf"
}
basalt_conf() {
  printf 'INFERENCE_BACKEND=basalt\nINFERENCE_URL=http://127.0.0.1:8080\nINFERENCE_MANAGED=true\nENGINE_PROFILE=inkling2-mini-stock\nINFERENCE_MODEL="inkling-2-mini"\nINFERENCE_SLOTS=1\nSERVE_SCRIPT=serve-qwen38-27b-uncensored-mtp-q5.sh\nAGENT_PROFILE=inkling-best\n' > "$SB/openbeast.conf"
  chmod 600 "$SB/openbeast.conf"
}
# What a child that re-sources conf.sh resolves — start.sh's own first step.
RESOLVE='source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1; echo "$INFERENCE_BACKEND|$INFERENCE_MANAGED|${OPENBEAST_INFERENCE_MODEL:-}|${OPENBEAST_INFERENCE_URL:-}|${OPENBEAST_INFERENCE_SLOTS:-}"'

# ════════════════════════════════════════════════════════════════════════════
echo "conf.sh: a re-sourcing child reads the conf, not its parent's exports"
llama_conf
# parent: sources conf.sh (llama), the conf is rewritten (basalt), a child re-sources.
_o="$(env -i "${BASE_ENV[@]}" REPO_DIR="$SB" bash -c '
  source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1
  printf "INFERENCE_BACKEND=basalt\nINFERENCE_URL=http://127.0.0.1:8080\nINFERENCE_MANAGED=true\nENGINE_PROFILE=inkling2-mini-stock\nINFERENCE_MODEL=\"inkling-2-mini\"\nINFERENCE_SLOTS=1\n" > "$REPO_DIR/openbeast.conf"
  bash -c "$1"' _ "$RESOLVE")"
check "llama -> basalt: the child resolves basalt, managed, its model, URL and slot count" \
  '[[ "$_o" == "basalt|true|inkling-2-mini|http://127.0.0.1:8080|1" ]]' "$_o"

basalt_conf
_o="$(env -i "${BASE_ENV[@]}" REPO_DIR="$SB" bash -c '
  source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1
  printf "INFERENCE_BACKEND=llama\n" > "$REPO_DIR/openbeast.conf"
  bash -c "$1"' _ "$RESOLVE")"
check "basalt -> llama (rollback): llama, managed, and no stale model id / URL / slots reach the child" \
  '[[ "$_o" == "llama|true|||" ]]' "$_o"

llama_conf
_o="$(env -i "${BASE_ENV[@]}" REPO_DIR="$SB" OPENBEAST_INFERENCE_BACKEND=vllm OPENBEAST_INFERENCE_URL=http://10.0.0.5:8000 bash -c '
  source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1
  printf "INFERENCE_BACKEND=basalt\nINFERENCE_MANAGED=true\n" > "$REPO_DIR/openbeast.conf"
  bash -c "$1"' _ "$RESOLVE")"
check "an operator's own OPENBEAST_INFERENCE_BACKEND / _URL still win over the conf in the child" \
  '[[ "$_o" == "vllm|false|"*"|http://10.0.0.5:8000|" ]]' "$_o"

llama_conf
_o="$(env -i "${BASE_ENV[@]}" REPO_DIR="$SB" bash -c '
  source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1
  printf "INFERENCE_BACKEND=basalt\n" > "$REPO_DIR/openbeast.conf"
  export OPENBEAST_INFERENCE_BACKEND=vllm   # the operator exports his own in the same shell
  bash -c "$1"' _ "$RESOLVE")"
check "…and an export that differs from the record is an override again" \
  '[[ "$_o" == vllm"|"* ]]' "$_o"

# ════════════════════════════════════════════════════════════════════════════
echo "engine.sh use: the restarted start.sh sees the new engine"
# A "running stack": a recorded supervisor whose command line says start.sh.
printf '#!/bin/bash\nsleep 120\n' > "$T/start.sh"; bash "$T/start.sh" & _SUP=$!
echo "$_SUP" > "$SB/.run/supervisor.pid"
printf '#!/bin/bash\nexit 0\n' > "$T/bin/stopstub"
# The start stub records what the real start.sh would inherit and resolve.
cat > "$T/bin/startprobe" <<EOF
#!/bin/bash
{
  echo "argv=\$*"
  echo "env_backend=\${OPENBEAST_INFERENCE_BACKEND:-}"
  echo "readonly=\${OB_CONF_READONLY:-}"
  echo "caller=\${OB_TEST_CALLER_MARK:-}"
  REPO_DIR="$SB"
  source "$SB/scripts/lib/conf.sh" >/dev/null 2>&1
  echo "resolved=\$INFERENCE_BACKEND|\$INFERENCE_MANAGED|\${OPENBEAST_INFERENCE_MODEL:-}|\${OPENBEAST_INFERENCE_SLOTS:-}"
} > "$T/probe.log"
EOF
chmod +x "$T/bin/stopstub" "$T/bin/startprobe"
STUBS=(OB_ENGINE_STOP_CMD="$T/bin/stopstub" OB_ENGINE_START_CMD="$T/bin/startprobe -d")

llama_conf; rm -f "$T/probe.log"
_o="$(env -i "${BASE_ENV[@]}" "${STUBS[@]}" OB_TEST_CALLER_MARK=session bash "$SB/scripts/engine.sh" use default --force 2>&1 || true)"
_p="$(cat "$T/probe.log" 2>/dev/null || true)"
check "use default (basalt on the rig) wrote the basalt conf" \
  'grep -q "^INFERENCE_BACKEND=basalt" "$SB/openbeast.conf" && grep -q "^ENGINE_PROFILE=inkling2-mini-stock" "$SB/openbeast.conf"' "$_o"
check "…and restarted start.sh -d" '[[ "$_p" == *"argv=-d"* ]]' "$_o"
check "…which inherits no OPENBEAST_INFERENCE_BACKEND from the old conf" \
  '[[ "$_p" == *$'"'"'\nenv_backend=\n'"'"'* ]]' "$_p"
check "…and resolves basalt, managed, inkling-2-mini, 1 slot (the rig bug: it resolved llama)" \
  '[[ "$_p" == *"resolved=basalt|true|inkling-2-mini|1"* ]]' "$_p"
check "…without engine.sh's OB_CONF_READONLY, and with the caller's own environment kept" \
  '[[ "$_p" == *$'"'"'\nreadonly=\n'"'"'* && "$_p" == *"caller=session"* ]]' "$_p"

rm -f "$T/probe.log"
_o="$(env -i "${BASE_ENV[@]}" "${STUBS[@]}" OB_TEST_CALLER_MARK=session bash "$SB/scripts/engine.sh" use llama 2>&1 || true)"
_p="$(cat "$T/probe.log" 2>/dev/null || true)"
check "use llama (the rollback) restarts on llama, managed, with no stale model id or slots" \
  '[[ "$_p" == *"resolved=llama|true||"* && "$_p" == *$'"'"'\nenv_backend=\n'"'"'* ]] && ! grep -q "^INFERENCE_MODEL=" "$SB/openbeast.conf"' "$_p / $_o"

echo
echo "test_engine_switch_env: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
