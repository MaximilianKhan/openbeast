#!/bin/bash
# Review 2026-10-27, launcher/ops findings (R4-01 … R4-07, R9-01). Each case
# builds its own sandbox copy of the scripts, stubs every host tool, and
# FAILS on the code before the fix (the negative controls say what the
# sandbox can observe at all).
#
# Same rules as tests/test_lifecycle.sh: no GPU, no docker, no network, no
# real stack; pkill/pgrep reach the real process table only for patterns
# anchored inside the sandbox. Never `pkill -f` here.
#
# Usage: bash tests/test_review1027_launcher.sh
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0; FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# No ERE metacharacter in the path (the pkill guard matches it literally).
_T="$(mktemp -d "${TMPDIR:-/tmp}/obrev1027XXXXXX")"
_PIDS=""
cleanup() {
  local p
  for p in $_PIDS; do kill -KILL "$p" 2>/dev/null || true; done
  rm -rf "$_T"
}
trap cleanup EXIT

_sandbox() { # _sandbox <dir>
  local d="$1" c
  mkdir -p "$d/scripts/lib" "$d/.run" "$d/bin" "$d/home" "$d/extensions" "$d/agents" \
           "$d/llama.cpp/build/bin"
  cp "$REPO_DIR/start.sh" "$REPO_DIR/stop.sh" "$d/"
  cp "$REPO_DIR/scripts/healthcheck.sh" "$REPO_DIR/scripts/ext.sh" "$REPO_DIR/scripts/update.sh" "$d/scripts/"
  cp "$REPO_DIR"/scripts/lib/*.sh "$d/scripts/lib/"
  : > "$d/openbeast.conf"
  for c in curl docker tailscale nvidia-smi sudo systemctl systemd-run smartctl; do
    printf '#!/bin/bash\nexit 1\n' > "$d/bin/$c"; chmod +x "$d/bin/$c"
  done
  for c in pkill pgrep; do
    printf '#!/bin/bash\nfor a in "$@"; do [[ "$a" == *%s* ]] && exec /usr/bin/%s "$@"; done\nexit 1\n' \
      "$d" "$c" > "$d/bin/$c"
    chmod +x "$d/bin/$c"
  done
}
RUN_ENV=()
_run() { # _run <dir> <script> [args...]
  local d="$1"; shift
  env -i HOME="$d/home" PATH="$d/bin:/usr/bin:/bin" ${RUN_ENV[@]+"${RUN_ENV[@]}"} \
    bash "$@" 2>&1 || true
}
_wait_dead() { # _wait_dead <pid> <tenths>
  local i
  for (( i = 0; i < $2; i++ )); do kill -0 "$1" 2>/dev/null || return 0; sleep 0.1; done
  return 1
}

echo "=== Review 2026-10-27: launcher / ops ==="

# ---------------------------------------------------------------------------
echo ""
echo "R4-03 — one-word conf keys survive an inline comment; the lint says so:"
_C="$_T/conf"; _sandbox "$_C"; cp "$REPO_DIR/openbeast.conf.example" "$_C/"
cat > "$_C/openbeast.conf" <<'CONF'
REASONING=off   # fastest first token
WEIGHT_ENFORCE=strict  # supply chain
BIND_HOST=127.0.0.1  # loopback only
SERVE_SCRIPT="serve-x.sh" # mine
FILES_SHARDING=chat
WEBUI_ADMIN_PASSWORD=pa ss #word
SEARXNG_SECRET=abc
CONF
_conf_dump() {
  env -i HOME="$_C/home" PATH=/usr/bin:/bin ${RUN_ENV[@]+"${RUN_ENV[@]}"} bash -c '
    set -euo pipefail; REPO_DIR="$1"; source "$1/scripts/lib/conf.sh"
    echo "R=[$REASONING] W=[$WEIGHT_ENFORCE] B=[$BIND_HOST] S=[$DEFAULT_SERVE_SCRIPT] F=[$FILES_SHARDING] P=[$WEBUI_ADMIN_PASSWORD] G=[${LLAMA_LOAD_GRACE:-}]"' _ "$_C" 2>&1 || true
}
_O="$(_conf_dump)"
if [[ "$_O" == *"R=[off] W=[strict] B=[127.0.0.1] S=[serve-x.sh] F=[chat]"* ]]; then
  pass "REASONING/WEIGHT_ENFORCE/BIND_HOST/SERVE_SCRIPT read their first word (was: the comment rode along; strict fell back to warn)"
else
  fail "one-word keys kept their comment: $(tail -n1 <<< "$_O")"
fi
if [[ "$_O" == *"P=[pa ss #word]"* ]]; then
  pass "…a password with a space and a '#' is untouched (control: no blanket comment strip)"
else
  fail "the password was mangled: $(tail -n1 <<< "$_O")"
fi
if grep -q "WARNING: openbeast.conf: WEIGHT_ENFORCE='strict  # supply chain' carries an inline comment" <<< "$_O" \
   && grep -q "REASONING='off   # fastest first token' carries an inline comment" <<< "$_O" \
   && ! grep -q "FILES_SHARDING" <<< "$_O"; then
  pass "ob_conf_lint flags the inline comments (and not a clean key)"
else
  fail "lint silent about inline comments: $(grep WARNING <<< "$_O" | tr '\n' '|')"
fi
printf 'REASONING=maybe\nWEIGHT_ENFORCE=strictly\nSEARXNG_SECRET=abc\n' > "$_C/openbeast.conf"
_O="$(_conf_dump)"
if [[ "$_O" == *"R=[] W=[strict]"* ]] && grep -q "REASONING='maybe' is not on, off or auto" <<< "$_O"; then
  pass "an unknown REASONING is dropped with a warning (was: --reasoning maybe killed llama-server); 'strictly' fails CLOSED"
else
  fail "bad enum values: $(tr '\n' '|' <<< "$_O")"
fi

# ---------------------------------------------------------------------------
echo ""
echo "R4-02 — LLAMA_LOAD_GRACE is a conf key both start.sh and the watchdog read:"
printf 'LLAMA_LOAD_GRACE=1800  # NAS weights\nSEARXNG_SECRET=abc\n' > "$_C/openbeast.conf"
_O="$(_conf_dump)"
[[ "$_O" == *"G=[1800]"* ]] && pass "conf LLAMA_LOAD_GRACE=1800 resolves to 1800" \
  || fail "conf grace not read: $(tail -n1 <<< "$_O")"
_O="$(RUN_ENV=(OPENBEAST_LLAMA_LOAD_GRACE=60); _conf_dump)"
[[ "$_O" == *"G=[60]"* ]] && pass "…the env override still wins" || fail "env grace lost: $(tail -n1 <<< "$_O")"
printf 'SEARXNG_SECRET=abc\n' > "$_C/openbeast.conf"
_O="$(_conf_dump)"
[[ "$_O" == *"G=[900]"* ]] && pass "…default 900 (control)" || fail "default grace: $(tail -n1 <<< "$_O")"
# The watchdog's _llama_loading, run for real with the conf key only: a
# recorded server 2 s into a load, grace 1 s from the conf → DOWN; grace
# 1800 from the conf → still LOADING. Before the fix the conf was ignored and
# both said LOADING (900 s default), so the first assertion failed.
_H="$_T/hc"; _sandbox "$_H"
printf '#!/bin/bash\nexec -a "$0" sleep 300\n' > "$_H/llama.cpp/build/bin/llama-server"
chmod +x "$_H/llama.cpp/build/bin/llama-server"
bash "$_H/llama.cpp/build/bin/llama-server" & _LL=$!; _PIDS="$_PIDS $_LL"
echo "$_LL" > "$_H/.run/llama.pid"
sleep 2.2
{
  echo 'set -uo pipefail'
  echo "REPO_DIR='$_H'; source '$_H/scripts/lib/conf.sh' 2>/dev/null"
  echo "source '$_H/scripts/lib/proc.sh'; source '$_H/scripts/lib/curl_auth.sh'"
  echo 'LLAMA_URL=http://127.0.0.1:9'
  sed -n '/^_LLAMA_ARGV0=/p; /^_llama_loading() {/,/^}/p' "$REPO_DIR/scripts/healthcheck.sh"
  echo '_llama_loading && echo LOADING || echo DOWN'
} > "$_H/loading.sh"
printf 'LLAMA_LOAD_GRACE=1\nSEARXNG_SECRET=abc\n' > "$_H/openbeast.conf"
_O1="$(_run "$_H" "$_H/loading.sh")"
printf 'LLAMA_LOAD_GRACE=1800\nSEARXNG_SECRET=abc\n' > "$_H/openbeast.conf"
_O2="$(_run "$_H" "$_H/loading.sh")"
if [[ "$_O1" == *DOWN* && "$_O2" == *LOADING* ]]; then
  pass "the watchdog's load bound follows the conf key (1 s → DOWN, 1800 s → LOADING)"
else
  fail "watchdog ignores the conf grace: grace=1 → '$_O1', grace=1800 → '$_O2'"
fi
kill "$_LL" 2>/dev/null || true

# ---------------------------------------------------------------------------
echo ""
echo "R4-04 / R4-05 — fallback sweeps hit the server, not an editor naming its file:"
_S="$_T/stop"; _sandbox "$_S"
printf 'SEARXNG_SECRET=abc\n' > "$_S/openbeast.conf"
printf 'import time\ntime.sleep(300)\n' > "$_S/agents/router.py"
cp "$_S/agents/router.py" "$_S/agents/openapi_tools.py"
# "editors": processes whose command line NAMES the file but does not run it
# (`tail -f` for real; an `nvim` stand-in is tail under argv[0] "nvim").
bash -c 'exec -a nvim tail -f "$1"' _ "$_S/agents/router.py" >/dev/null 2>&1 & _ED=$!; _PIDS="$_PIDS $_ED"
tail -f "$_S/agents/openapi_tools.py" >/dev/null 2>&1 & _TL=$!; _PIDS="$_PIDS $_TL"
python3 "$_S/agents/router.py" >/dev/null 2>&1 & _SRV=$!; _PIDS="$_PIDS $_SRV"
sleep 0.5
grep -qF "$_S/agents/router.py" "/proc/$_ED/cmdline" 2>/dev/null \
  || fail "control: the editor stand-in does not carry the path, so this proves nothing"
_O="$(_run "$_S" "$_S/stop.sh")"
if _wait_dead "$_SRV" 30; then
  pass "stop.sh's sweep still stops an orphaned 'python3 …/agents/router.py' (control)"
else
  fail "the anchored sweep no longer finds the real router: $_O"
fi
if kill -0 "$_ED" 2>/dev/null && kill -0 "$_TL" 2>/dev/null; then
  pass "an editor / reader whose argv names agents/*.py survives ./stop.sh (was: SIGTERMed)"
else
  fail "stop.sh killed a process that merely names the file"
fi
_MC=0
for _f in stop.sh scripts/healthcheck.sh; do
  grep -vE '^[[:space:]]*#' "$REPO_DIR/$_f" | grep -q 'pkill -f "mcpo --port"' && _MC=1
done
[[ $_MC -eq 0 ]] && pass "no unanchored 'pkill -f \"mcpo --port\"' (killed every mcpo on the box)" \
  || fail "stop.sh/healthcheck still pkill any mcpo"
# The same anchoring for llama-server: argv[0], not "mentions the binary".
_P="$(bash -c "source '$REPO_DIR/scripts/lib/proc.sh'; ob_bin_ere /r/llama.cpp/build/bin/llama-server" 2>/dev/null || true)"
if [[ "/r/llama.cpp/build/bin/llama-server -m x" =~ $_P && ! "gdb /r/llama.cpp/build/bin/llama-server" =~ $_P ]]; then
  pass "ob_bin_ere matches the server, not 'gdb <binary>'"
else
  fail "ob_bin_ere: '$_P'"
fi

# ---------------------------------------------------------------------------
echo ""
echo "R4-07 — stop.sh returns only once llama-server has exited:"
_W="$_T/wait"; _sandbox "$_W"
printf 'SEARXNG_SECRET=abc\n' > "$_W/openbeast.conf"
_BIN="$_W/llama.cpp/build/bin/llama-server"
# A server that takes 2 s to release after TERM (argv[0] IS the binary path).
_slow_llama() { # _slow_llama <seconds-after-TERM | ignore>
  bash -c 'exec -a "$1" python3 -c "
import signal, sys, time
mode = sys.argv[1]
def on_term(*a):
    if mode == \"ignore\":
        return
    time.sleep(float(mode)); sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
while True: time.sleep(0.1)
" "$2"' _ "$_BIN" "$1" >/dev/null 2>&1 &
  echo $!
}
_SL="$(_slow_llama 2)"; _PIDS="$_PIDS $_SL"; sleep 0.5
echo "$_SL" > "$_W/.run/llama.pid"
_O="$(_run "$_W" "$_W/stop.sh")"
if ! kill -0 "$_SL" 2>/dev/null && grep -q "llama-server has exited" <<< "$_O"; then
  pass "stop.sh waited for a server that took 2 s to exit (was: returned with it still alive)"
else
  fail "stop.sh returned with llama-server alive: $(grep -i llama <<< "$_O" | tr '\n' '|')"
fi
_SL="$(_slow_llama ignore)"; _PIDS="$_PIDS $_SL"; sleep 0.5
echo "$_SL" > "$_W/.run/llama.pid"
_O="$(RUN_ENV=(OPENBEAST_STOP_WAIT=1); _run "$_W" "$_W/stop.sh")"
if ! kill -0 "$_SL" 2>/dev/null && grep -q "escalating to SIGKILL" <<< "$_O"; then
  pass "a server ignoring TERM is KILLed after OPENBEAST_STOP_WAIT"
else
  fail "a TERM-ignoring server survived stop.sh: $(grep -i llama <<< "$_O" | tr '\n' '|')"
fi
_O="$(_run "$_W" "$_W/stop.sh")"
grep -q "Waiting for llama-server" <<< "$_O" && fail "stop.sh waited with nothing to wait for" \
  || pass "nothing running: no wait (control)"

# ---------------------------------------------------------------------------
echo ""
echo "R4-01 — a ./stop.sh landing mid-tick sticks:"
_R="$_T/wd"; _sandbox "$_R"
printf 'SEARXNG_SECRET=abc\n' > "$_R/openbeast.conf"
printf 'import os\nopen(os.environ["WD_LOG"], "a").write("toolserver launched\\n")\n' > "$_R/agents/openapi_tools.py"
printf '#!/bin/bash\necho "docker $*" >> "$WD_LOG"\nexit 0\n' > "$_R/bin/docker"; chmod +x "$_R/bin/docker"
# curl: every probe fails; the FIRST one (llama's /health) also plays the
# operator running ./stop.sh right then — it writes .run/stopped.
printf '#!/bin/bash\n[[ -n "${WD_STOP_AT_FIRST:-}" && ! -e "$WD_DIR/.curl1" ]] && { : > "$WD_DIR/.curl1"; echo "x ./stop.sh" > "$WD_DIR/.run/stopped"; }\nexit 7\n' > "$_R/bin/curl"
chmod +x "$_R/bin/curl"
: > "$_R/wd.log"
_O="$(RUN_ENV=(WD_LOG="$_R/wd.log" WD_DIR="$_R" WD_STOP_AT_FIRST=1 OPENBEAST_SERVE_SCRIPT=serve-none.sh); \
      timeout 60 env -i HOME="$_R/home" PATH="$_R/bin:/usr/bin:/bin" "${RUN_ENV[@]}" bash "$_R/scripts/healthcheck.sh" --restart 2>&1 || true)"
if ! grep -qE "toolserver launched|compose .*up" "$_R/wd.log" && grep -q "stopped on purpose during this check" <<< "$_O"; then
  pass "after a mid-tick stop, the watchdog relaunches nothing (was: tool server + WebUI + SearXNG came back)"
else
  fail "the watchdog undid a mid-tick stop: $(tr '\n' '|' < "$_R/wd.log")"
fi
rm -f "$_R/.run/stopped" "$_R/.curl1" "$_R/.run/watchdog-relaunches"; : > "$_R/wd.log"
_O="$(RUN_ENV=(WD_LOG="$_R/wd.log" WD_DIR="$_R" OPENBEAST_SERVE_SCRIPT=serve-none.sh); \
      timeout 60 env -i HOME="$_R/home" PATH="$_R/bin:/usr/bin:/bin" "${RUN_ENV[@]}" bash "$_R/scripts/healthcheck.sh" --restart 2>&1 || true)"
if grep -q "compose .*up -d open-webui" "$_R/wd.log"; then
  pass "with no stop, the same tick DOES restart (control: the sandbox observes relaunches)"
else
  fail "control: no relaunch observed without a stop: $(tr '\n' '|' < "$_R/wd.log") :: $(tail -n 3 <<< "$_O")"
fi

# ---------------------------------------------------------------------------
echo ""
echo "R4-06 — root refusal and an unreadable conf:"
_U="$_T/root"; _sandbox "$_U"
printf '#!/bin/bash\n[[ "$1" == -u ]] && { echo 0; exit 0; }\nexec /usr/bin/id "$@"\n' > "$_U/bin/id"; chmod +x "$_U/bin/id"
for _s in stop.sh scripts/update.sh "scripts/ext.sh list"; do
  rm -f "$_U/openbeast.conf"
  # shellcheck disable=SC2086
  _O="$(_run "$_U" "$_U/"$_s)"
  if [[ "$_O" == *"do not run"*"as root"* && ! -e "$_U/openbeast.conf" ]]; then
    pass "${_s%% *} refuses root before minting a root-owned conf"
  else
    fail "${_s%% *} ran as root: conf=$( [[ -e "$_U/openbeast.conf" ]] && echo created || echo none) :: $(head -n 2 <<< "$_O" | tr '\n' '|')"
  fi
done
printf '#!/bin/bash\necho "STOP.SH RAN $*" >> "%s/stop.log"\n' "$_U" > "$_U/stop.sh"
: > "$_U/stop.log"
_O="$(_run "$_U" "$_U/start.sh" stop)"
if [[ ! -s "$_U/stop.log" && "$_O" == *"as root"* ]]; then
  pass "./start.sh stop refuses root too (the guard used to sit after the exec)"
else
  fail "./start.sh stop ran stop.sh as root: $(cat "$_U/stop.log")"
fi
if [[ "$(id -u)" -ne 0 ]]; then
  _N="$_T/unread"; _sandbox "$_N"
  printf 'SEARXNG_SECRET=abc\n' > "$_N/openbeast.conf"; chmod 000 "$_N/openbeast.conf"
  _O="$(_run "$_N" -c "set -euo pipefail; REPO_DIR='$_N'; source '$_N/scripts/lib/conf.sh'; echo SURVIVED")"
  if [[ "$_O" == *"is not readable by"*"sudo chown"* && "$_O" != *"Permission denied"* && "$_O" != *SURVIVED* ]]; then
    pass "an unreadable conf: one clear message, no wall of grep errors"
  else
    fail "unreadable conf: $(head -n 3 <<< "$_O" | tr '\n' '|')"
  fi
  _O="$(_run "$_N" "$_N/stop.sh")"
  if [[ "$_O" == *"continuing WITHOUT openbeast.conf"* && "$_O" == *"Stopping llama.cpp server"* ]]; then
    pass "…and ./stop.sh still reaches its kill path"
  else
    fail "stop.sh died on an unreadable conf: $(tail -n 3 <<< "$_O" | tr '\n' '|')"
  fi
  chmod 600 "$_N/openbeast.conf"
fi

# ---------------------------------------------------------------------------
echo ""
echo "R9-01 — ./start.sh stop|doctor forward their arguments:"
_A="$_T/args"; _sandbox "$_A"
printf '#!/bin/bash\necho "STOP ARGS: [$*]"\n' > "$_A/stop.sh"; chmod +x "$_A/stop.sh"
printf '#!/bin/bash\necho "DOCTOR ARGS: [$*]"\n' > "$_A/scripts/doctor.sh"; chmod +x "$_A/scripts/doctor.sh"
_O="$(_run "$_A" "$_A/start.sh" stop --help)"
[[ "$_O" == *"STOP ARGS: [--help]"* ]] && pass "./start.sh stop --help reaches stop.sh as --help (was: a bare stop)" \
  || fail "stop args: $_O"
_O="$(_run "$_A" "$_A/start.sh" doctor --quiet)"
[[ "$_O" == *"DOCTOR ARGS: [--quiet]"* ]] && pass "./start.sh doctor --quiet forwards --quiet" || fail "doctor args: $_O"
_O="$(_run "$_A" "$_A/start.sh" stop)"
[[ "$_O" == *"STOP ARGS: []"* ]] && pass "./start.sh stop alone still stops (control)" || fail "bare stop: $_O"
# and against the REAL stop.sh: help, nothing stopped, no .run/stopped
_B="$_T/args2"; _sandbox "$_B"; rm -f "$_B/.run/stopped"
_O="$(_run "$_B" "$_B/start.sh" stop --help)"
if [[ ! -e "$_B/.run/stopped" && "$_O" == *"Usage:"* ]]; then
  pass "./start.sh stop --help prints help and leaves watchdog recovery on"
else
  fail "./start.sh stop --help stopped the stack: $(head -n 3 <<< "$_O" | tr '\n' '|')"
fi

# ---------------------------------------------------------------------------
echo ""
echo "R10 #9 — serve.sh's registry size check covers --mmproj and draft GGUFs:"
_V="$_T/serve"; mkdir -p "$_V/scripts"
sed -n '/^# --- Model registry enforcement/,/^fi$/p' "$REPO_DIR/scripts/serve.sh" > "$_V/block.sh"
{ printf '%s\t5\tmain.gguf\torg/x\t-\n' "$(printf '0%.0s' {1..64})"
  printf '%s\t999\tproj.gguf\torg/x\t-\n' "$(printf '0%.0s' {1..64})"
  printf '%s\t4\tdraft.gguf\torg/x\t-\n' "$(printf '0%.0s' {1..64})"
} > "$_V/scripts/weights.registry"
printf 'exact' > "$_V/main.gguf"; printf 'short' > "$_V/proj.gguf"; printf 'four' > "$_V/draft.gguf"
cat > "$_V/run.sh" <<'RUN'
set -euo pipefail
SCRIPT_DIR="$1"; MODEL="$2"; WEIGHT_ENFORCE="$3"; shift 3; EXTRA_ARGS=("$@")
source "$SCRIPT_DIR/../block.sh"
echo LAUNCH
RUN
_sv() { bash "$_V/run.sh" "$_V/scripts" "$_V/main.gguf" "$@" 2>&1; echo "rc=$?"; }
_O="$(_sv strict --mmproj "$_V/proj.gguf")"
if [[ "$_O" == *"projector 'proj.gguf' is 5 bytes, registry pins 999"* && "$_O" == *"rc=3"* ]]; then
  pass "a wrong-size --mmproj is refused under strict (exit 3)"
else
  fail "--mmproj not checked: $(tr '\n' '|' <<< "$_O")"
fi
_O="$(_sv strict --model-draft="$_V/proj.gguf")"
[[ "$_O" == *"draft model 'proj.gguf'"* && "$_O" == *"rc=3"* ]] && pass "…and a wrong-size --model-draft=<file>" \
  || fail "draft not checked: $(tr '\n' '|' <<< "$_O")"
_O="$(_sv strict -md "$_V/draft.gguf" --mmproj-offload)"
[[ "$_O" == *LAUNCH* && "$_O" == *"rc=0"* ]] && pass "a right-size draft model passes (control)" \
  || fail "right-size draft refused: $(tr '\n' '|' <<< "$_O")"
_O="$(_sv warn --mmproj "$_V/proj.gguf")"
[[ "$_O" == *"WARNING: projector"* && "$_O" == *LAUNCH* ]] && pass "…under warn it warns and launches" \
  || fail "warn mode: $(tr '\n' '|' <<< "$_O")"

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
