#!/bin/bash
# GPU-ops review fixes (2026-09-29) — behavior tests for the tools that put a
# model on the card outside the stack: gpu-lease.sh, measure-vram.sh, the
# profile-*-mtp.sh sweeps, and update.sh's llama.cpp / image paths.
#
# Usage: bash tests/test_gpu_ops.sh
#
# HERMETIC. Everything runs against THROWAWAY copies of the scripts under
# $TMPDIR, with stub `nvidia-smi`, `curl`, `docker`, `git`, `cmake` and a stub
# serve script / llama-server on PATH. No port is bound, the GPU is never
# queried, and `pkill` / `pgrep` / `killall` are RECORDING stubs: if a script
# under test ever reaches for a kill-by-pattern again, the test sees the call
# instead of the rig losing its llama-server. Every process this suite starts
# is recorded by pid and reaped by pid on exit.
#
# Doctrine (as in the other suites): each test BUILDS ITS OWN CASE, the stubs
# RECORD what the script did, and each positive assertion has a NEGATIVE
# CONTROL beside it.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# OB_TEST_SRC: run the suite against another tree's scripts (e.g. an old
# revision from `git archive`) to see the regression cases FAIL there.
SRC="${OB_TEST_SRC:-$REPO_DIR}"
for _v in $(compgen -e | grep '^OPENBEAST_' || true); do unset "$_v"; done

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has()  { grep -qF -- "$2" <<< "$1"; }

T="$(mktemp -d "${TMPDIR:-/tmp}/ob-gpu-ops-XXXXXX")"
PIDS=()
cleanup() {
  local p
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
  rm -rf "$T"
}
trap cleanup EXIT

# Poll for a condition instead of sleeping and hoping — this suite runs under load.
wait_for() { local i; for i in $(seq 1 100); do eval "$1" && return 0; sleep 0.1; done; return 1; }

mkdir -p "$T/bin" "$T/state"
export OB_STUB_STATE="$T/state"
# nvidia-smi: a working driver, a 32 GB card, 1 GB in use.
cat > "$T/bin/nvidia-smi" <<'STUB'
#!/bin/bash
case " $* " in
  *memory.total*) echo 32607 ;;
  *memory.used*)  echo "$(cat "$OB_STUB_STATE/vram_used" 2>/dev/null || echo 1000)" ;;
esac
exit 0
STUB
for k in pkill pgrep killall; do
  printf '#!/bin/bash\necho "%s $*" >> "$OB_STUB_STATE/kills.log"\nexit 1\n' "$k" > "$T/bin/$k"
done
chmod +x "$T/bin/"*
: > "$T/state/kills.log"

# A sandbox repo: the REAL scripts under test, copied.
SB="$T/repo"
mkdir -p "$SB/scripts/lib" "$SB/.run"
install -m 755 "$SRC/scripts/gpu-lease.sh" "$SB/scripts/gpu-lease.sh"
GL="$SB/scripts/gpu-lease.sh"
export OPENBEAST_LEASE_VRAM_FLOOR=99999999
export PATH="$T/bin:$PATH"

echo "=== GPU-ops review fixes ==="

# ===========================================================================
echo ""
echo "gpu-lease.sh run — a --force takeover survives the first run's exit:"
# ===========================================================================
rm -f "$SB/.run/gpu.lease" "$T/state/go"
( "$GL" run "campaign A" -- bash -c 'while [[ ! -f "$1/go" ]]; do sleep 0.1; done' _ "$T/state" \
    > "$T/state/runA.out" 2>&1; echo "rc=$?" >> "$T/state/runA.out" ) &
PIDS+=($!)
wait_for 'grep -q "^label=campaign A$" "$SB/.run/gpu.lease" 2>/dev/null' \
  || fail "run A never took the lease"
# B takes the card by force. `acquire` records its CALLER, so the caller must
# outlive the command: a shell that execs into a long sleep keeps its pid AND
# its start time.
bash -c '"$1" acquire "manual B" --force >/dev/null 2>&1; exec sleep 30' _ "$GL" &
B_PID=$!; PIDS+=("$B_PID")
wait_for 'grep -q "^label=manual B$" "$SB/.run/gpu.lease" 2>/dev/null' \
  || fail "B never took the lease"
touch "$T/state/go"
wait_for 'grep -q "^rc=" "$T/state/runA.out"' || fail "run A never exited"
_st="$("$GL" status 2>/dev/null || true)"
if [[ "$_st" == "HELD by pid $B_PID"* ]] && has "$(cat "$T/state/runA.out")" "taken over"; then
  pass "run A's exit leaves B's lease in place (and says why)"
else
  fail "B's lease was deleted by run A's exit: status='$_st' out=$(tr '\n' ' ' < "$T/state/runA.out")"
fi
kill "$B_PID" 2>/dev/null || true
# NEGATIVE CONTROL: with no takeover, run still releases its own lease.
rm -f "$SB/.run/gpu.lease"
"$GL" run "plain" -- true >/dev/null 2>&1
_st="$("$GL" status 2>/dev/null || true)"
if [[ "$_st" == FREE* && ! -f "$SB/.run/gpu.lease" ]]; then
  pass "negative control: a run that was not taken over still releases its lease"
else
  fail "a plain run leaked its lease: $_st"
fi

# ===========================================================================
echo ""
echo "gpu-lease.sh check — ours / free / somebody else's, as an exit code:"
# ===========================================================================
rm -f "$SB/.run/gpu.lease"
_rc=0; _out="$("$GL" check 2>&1)" || _rc=$?
if [[ $_rc -eq 3 && "$_out" == FREE* ]]; then
  pass "no lease -> 3 (FREE)"
else
  fail "free lease: rc=$_rc out=$_out"
fi
_rc=0; _out="$("$GL" run "wrapper" -- bash -c 'r=0; o="$("$1" check)" || r=$?; echo "$r|$o"' _ "$GL" 2>/dev/null | tail -n 1)" || _rc=$?
if [[ "$_out" == "0|OURS"* ]]; then
  pass "a command running under 'run' is told the lease is ITS (0), through a \$(...) subshell"
else
  fail "check under run: '$_out' (rc=$_rc)"
fi
# NEGATIVE CONTROL: a live holder that is NOT our ancestor.
bash -c '"$1" acquire "someone else" >/dev/null 2>&1; exec sleep 30' _ "$GL" &
_H=$!; PIDS+=("$_H")
wait_for 'grep -q "^label=someone else$" "$SB/.run/gpu.lease" 2>/dev/null' || fail "holder never took the lease"
_rc=0; _out="$("$GL" check 2>&1)" || _rc=$?
if [[ $_rc -eq 4 && "$_out" == "HELD by pid $_H"* ]]; then
  pass "negative control: somebody else's live lease -> 4 (HELD)"
else
  fail "foreign lease: rc=$_rc out=$_out"
fi
kill "$_H" 2>/dev/null || true; wait "$_H" 2>/dev/null || true
rm -f "$SB/.run/gpu.lease"

# ===========================================================================
echo ""
echo "gpu-lease.sh run — a server that left the group (setsid) keeps the lease:"
# ===========================================================================
# Exactly benchmark_all.start_model's shape: Popen(start_new_session=True),
# then the launcher dies without stopping it (the SIGKILL case).
rm -f "$T/state/orphan.pid" "$T/state/runO.out"
( "$GL" run "cell" -- python3 -c '
import subprocess, sys
p = subprocess.Popen(["sleep", "30"], start_new_session=True)
open(sys.argv[1], "w").write(str(p.pid))
' "$T/state/orphan.pid" > "$T/state/runO.out" 2>&1; echo "rc=$?" >> "$T/state/runO.out" ) &
PIDS+=($!)
wait_for '[[ -s "$T/state/orphan.pid" ]]' || fail "the launcher never started its server"
_O="$(cat "$T/state/orphan.pid" 2>/dev/null || echo 0)"; PIDS+=("$_O")
sleep 1.5
_st="$("$GL" status 2>/dev/null || true)"
if [[ "$_st" == HELD* ]] && ! grep -q '^rc=' "$T/state/runO.out"; then
  pass "the lease stays HELD while the launcher's setsid'd server is alive"
else
  fail "the lease went FREE over a live server outside the group: $_st / $(tr '\n' ' ' < "$T/state/runO.out")"
fi
kill "$_O" 2>/dev/null || true
wait_for 'grep -q "^rc=" "$T/state/runO.out"' || fail "run never exited after the server went"
_st="$("$GL" status 2>/dev/null || true)"
if [[ "$_st" == FREE* ]] && grep -q '^rc=0$' "$T/state/runO.out"; then
  pass "...and it is released, with the command's status, once that server is gone"
else
  fail "after the server went: $_st / $(tr '\n' ' ' < "$T/state/runO.out")"
fi

# ===========================================================================
echo ""
echo "measure-vram.sh — lease, port, teardown, and a bind failure is not an OOM:"
# ===========================================================================
install -m 755 "$SRC/scripts/measure-vram.sh" "$SB/scripts/measure-vram.sh"
# curl: 'port_busy' = somebody else's server answers; 'up' = the serve stub
# has come up. Otherwise connection refused (rc 7), as a free port says.
cat > "$T/bin/curl" <<'STUB'
#!/bin/bash
echo "curl $*" >> "$OB_STUB_STATE/curl.log"
if [[ -f "$OB_STUB_STATE/port_busy" || -f "$OB_STUB_STATE/up" ]]; then
  echo '{"status":"ok"}'; exit 0
fi
exit 7
STUB
# The serve script under test: records its launch (and the lease it saw),
# then behaves as the case asks. `ok` forks a child, so teardown has to take
# the whole group, not just the leader.
cat > "$SB/scripts/serve-fake.sh" <<'STUB'
#!/bin/bash
S="$OB_STUB_STATE"
echo "launched $*" >> "$S/serve.log"
echo $$ > "$S/serve.pid"
cat "$(dirname "$0")/../.run/gpu.lease" > "$S/lease_during" 2>/dev/null || echo none > "$S/lease_during"
case "$(cat "$S/serve_mode")" in
  bind) echo "couldn't bind HTTP server socket, hostname: 127.0.0.1, port: 8080"; exit 1 ;;
  oom)  echo "ggml_backend_cuda_buffer_type_alloc_buffer: allocating 9000 MiB failed: out of memory"; exit 1 ;;
  ok)   sleep 60 & echo $! > "$S/serve_child.pid"; touch "$S/up"; wait ;;
esac
STUB
chmod +x "$T/bin/curl" "$SB/scripts/serve-fake.sh"
MV() {
  rm -f "$T/state/up" "$T/state/serve.pid" "$T/state/serve_child.pid" "$T/state/lease_during"
  : > "$T/state/serve.log"; : > "$T/state/kills.log"
  _rc=0
  _out="$(OPENBEAST_MEASURE_PORT=18080 timeout 60 "$SB/scripts/measure-vram.sh" serve-fake.sh "$@" 2>&1)" || _rc=$?
}

# --- somebody else's server already on the port ----------------------------
# A stand-in for the stack's llama-server, named so the OLD pattern kill
# ("llama-server.*--port 8080") would match it.
bash -c 'exec -a "llama-server -m /w/stack.gguf --port 8080" sleep 60' &
_STACK=$!; PIDS+=("$_STACK")
touch "$T/state/port_busy"; echo ok > "$T/state/serve_mode"
MV
rm -f "$T/state/port_busy"
if [[ $_rc -ne 0 ]] && has "$_out" "already listening" && ! has "$_out" "RESULT  " \
   && [[ ! -s "$T/state/serve.log" ]]; then
  pass "a busy :port is refused before anything is launched (no bogus RESULT)"
else
  fail "busy port (rc=$_rc, serve.log=$(cat "$T/state/serve.log")): $_out"
fi
if kill -0 "$_STACK" 2>/dev/null && [[ ! -s "$T/state/kills.log" ]]; then
  pass "the server already on the port survives, and no kill-by-pattern was attempted"
else
  fail "kill-by-pattern: $(cat "$T/state/kills.log"); stack alive=$(kill -0 "$_STACK" 2>/dev/null && echo y || echo n)"
fi

# --- somebody else's lease --------------------------------------------------
bash -c '"$1" acquire "campaign" >/dev/null 2>&1; exec sleep 60' _ "$GL" &
_H=$!; PIDS+=("$_H")
wait_for 'grep -q "^label=campaign$" "$SB/.run/gpu.lease" 2>/dev/null' || fail "holder never took the lease"
MV
if [[ $_rc -ne 0 ]] && has "$_out" "GPU lease is HELD by pid $_H" && [[ ! -s "$T/state/serve.log" ]]; then
  pass "a GPU lease held by someone else is refused before anything is launched"
else
  fail "foreign lease (rc=$_rc, serve.log=$(cat "$T/state/serve.log")): $_out"
fi
kill "$_H" 2>/dev/null || true; wait "$_H" 2>/dev/null || true
rm -f "$SB/.run/gpu.lease"

# --- the happy path ---------------------------------------------------------
MV
_child="$(cat "$T/state/serve_child.pid" 2>/dev/null || echo 0)"; PIDS+=("$_child")
if [[ $_rc -eq 0 ]] && has "$_out" "RESULT  serve-fake.sh" && has "$_out" "[OK]" \
   && grep -q '^label=measure-vram serve-fake.sh$' "$T/state/lease_during" \
   && grep -q -- '--port 18080' "$T/state/serve.log"; then
  pass "a clean measurement runs under its own lease and reports a RESULT"
else
  fail "happy path (rc=$_rc, lease_during=$(tr '\n' ' ' < "$T/state/lease_during" 2>/dev/null)): $_out"
fi
_sp="$(cat "$T/state/serve.pid" 2>/dev/null || echo 0)"
if ! kill -0 "$_sp" 2>/dev/null && ! kill -0 "$_child" 2>/dev/null \
   && [[ ! -s "$T/state/kills.log" ]] && [[ ! -f "$SB/.run/gpu.lease" ]]; then
  pass "teardown took exactly its own group (server + its child) and released the lease"
else
  fail "teardown: serve alive=$(kill -0 "$_sp" 2>/dev/null && echo y || echo n) child alive=$(kill -0 "$_child" 2>/dev/null && echo y || echo n) kills=$(cat "$T/state/kills.log") lease=$([[ -f "$SB/.run/gpu.lease" ]] && echo left || echo gone)"
fi

# --- a bind failure is a port conflict, an allocation failure is an OOM -----
echo bind > "$T/state/serve_mode"; MV
if [[ $_rc -ne 0 ]] && has "$_out" "PORT CONFLICT" && ! has "$_out" "likely OOM"; then
  pass "a server that could not bind is reported as a port conflict, not an OOM"
else
  fail "bind failure (rc=$_rc): $_out"
fi
echo oom > "$T/state/serve_mode"; MV
if [[ $_rc -eq 2 ]] && has "$_out" "FAILED TO LOAD" && ! has "$_out" "PORT CONFLICT"; then
  pass "negative control: a load that dies otherwise is still FAILED TO LOAD"
else
  fail "oom path (rc=$_rc): $_out"
fi
if kill -0 "$_STACK" 2>/dev/null; then
  pass "the stand-in stack server is still alive after every measure-vram case"
else
  fail "the stand-in stack server was killed"
fi

# ===========================================================================
echo ""
echo "profile-*-mtp.sh — never sweep into a campaign's server:"
# ===========================================================================
for f in profile-qwen38-uncensored-mtp.sh profile-heretic-v2-mtp.sh profile-fable-fusion-mtp.sh; do
  install -m 755 "$SRC/scripts/$f" "$SB/scripts/$f"
done
install -m 644 "$SRC/scripts/lib/weights.sh" "$SB/scripts/lib/weights.sh"
mkdir -p "$T/w" "$SB/llama.cpp/build/bin"
for m in Qwen3.8-27B-Uncensored-Q5_K_M.gguf \
         Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-Q5_K_M.gguf \
         Qwen3.6-27B-Fable-Fus-711-UnHeretic-NM-DAU-NEO-MAX-NEO-MTP-Q5_K_M.gguf; do
  : > "$T/w/$m"
done
# The profilers exec llama-server themselves; the stub records the launch and
# the lease it ran under, then becomes the "server" (exec keeps the pid the
# profiler will signal, so nothing is orphaned).
cat > "$SB/llama.cpp/build/bin/llama-server" <<'STUB'
#!/bin/bash
S="$OB_STUB_STATE"
echo "launched $*" >> "$S/ls.log"
cat "$(dirname "$0")/../../../.run/gpu.lease" > "$S/lease_during" 2>/dev/null || echo none > "$S/lease_during"
if [[ "$(cat "$S/ls_mode")" == bind ]]; then
  echo "couldn't bind HTTP server socket, hostname: 127.0.0.1, port: 8080"; exit 1
fi
echo "eval time = 1000 ms / 100 tokens ( 10.00 ms per token, 100.00 tokens per second)"
touch "$S/up"
exec sleep 60
STUB
# stop.sh: records the call. Under a HELD lease the real one leaves every
# llama-server alone, so by default the port stays busy; `stop_frees` models
# a stack stop that did free it.
cat > "$SB/stop.sh" <<'STUB'
#!/bin/bash
echo "stop.sh $*" >> "$OB_STUB_STATE/stop.log"
[[ -f "$OB_STUB_STATE/stop_frees" ]] && rm -f "$OB_STUB_STATE/port_busy"
exit 0
STUB
chmod +x "$SB/llama.cpp/build/bin/llama-server" "$SB/stop.sh"
PROF() {  # PROF <script> [args] — output in _out, status in _rc
  rm -f "$T/state/up" "$T/state/lease_during"
  : > "$T/state/ls.log"; : > "$T/state/stop.log"; : > "$T/state/curl.log"; : > "$T/state/kills.log"
  _rc=0
  _out="$(OPENBEAST_WEIGHTS_DIR="$T/w" SWEEP_N=1 timeout 60 "$SB/scripts/$@" 2>&1)" || _rc=$?
}
reqs() { grep -c 'chat/completions' "$T/state/curl.log" 2>/dev/null || true; }

# --- a campaign holds the card and its server is on :8080 -------------------
bash -c '"$1" acquire "campaign" >/dev/null 2>&1; exec sleep 60' _ "$GL" &
_H=$!; PIDS+=("$_H")
wait_for 'grep -q "^label=campaign$" "$SB/.run/gpu.lease" 2>/dev/null' || fail "holder never took the lease"
echo ok > "$T/state/ls_mode"
for _p in "profile-qwen38-uncensored-mtp.sh" "profile-heretic-v2-mtp.sh q5" "profile-fable-fusion-mtp.sh q5"; do
  touch "$T/state/port_busy"
  # shellcheck disable=SC2086
  PROF $_p
  if [[ $_rc -ne 0 ]] && has "$_out" "GPU lease is HELD by pid $_H" \
     && [[ ! -s "$T/state/ls.log" && ! -s "$T/state/stop.log" && "$(reqs)" == 0 ]]; then
    pass "${_p%% *}: a campaign's lease is refused — no stop, no launch, no requests into its server"
  else
    fail "${_p%% *} under a foreign lease (rc=$_rc, launches=$(wc -l < "$T/state/ls.log"), stop=$(cat "$T/state/stop.log"), reqs=$(reqs)): $_out"
  fi
done
kill "$_H" 2>/dev/null || true; wait "$_H" 2>/dev/null || true
rm -f "$SB/.run/gpu.lease"

# --- no lease, but stop.sh could not free the port --------------------------
touch "$T/state/port_busy"; rm -f "$T/state/stop_frees"
PROF profile-qwen38-uncensored-mtp.sh
if [[ $_rc -ne 0 ]] && has "$_out" "still answers on :8080" && [[ -s "$T/state/stop.log" ]] \
   && [[ ! -s "$T/state/ls.log" && "$(reqs)" == 0 ]]; then
  pass "a port still answering after stop.sh is a refusal, not a sweep into it"
else
  fail "port still busy after stop (rc=$_rc, launches=$(wc -l < "$T/state/ls.log"), reqs=$(reqs)): $_out"
fi

# --- the stack is up and stop.sh frees it: the sweep runs, under its lease --
touch "$T/state/port_busy" "$T/state/stop_frees"
PROF profile-qwen38-uncensored-mtp.sh
rm -f "$T/state/stop_frees"
if [[ $_rc -eq 0 ]] && [[ -s "$T/state/stop.log" ]] \
   && grep -q '^label=profile-qwen38-uncensored-mtp.sh' "$T/state/lease_during" \
   && has "$_out" "decode=100.00" && [[ ! -f "$SB/.run/gpu.lease" && ! -s "$T/state/kills.log" ]]; then
  pass "negative control: a free card is profiled under the sweep's own lease, released after"
else
  fail "happy path (rc=$_rc, lease_during=$(tr '\n' ' ' < "$T/state/lease_during" 2>/dev/null)): $_out"
fi

# --- our server could not bind ----------------------------------------------
echo bind > "$T/state/ls_mode"
PROF profile-qwen38-uncensored-mtp.sh
if has "$_out" "PORT_CONFLICT" && ! has "$_out" "FAILED_TO_START"; then
  pass "a bind failure is recorded as a port conflict, not as an OOM at this context"
else
  fail "bind failure (rc=$_rc): $_out"
fi

# ===========================================================================
echo ""
echo "Summary: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
