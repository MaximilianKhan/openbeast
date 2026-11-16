#!/bin/bash
# Weight streaming (docs/WEIGHT_STREAMING.md): the per-profile memory scope,
# the MoE-cache and big-mode serve profiles, and the llama.cpp v1.0.0 flags
# they pass. Hermetic: no GPU, no network, no systemd; portable bash (macOS
# and Linux). The rig itself is checked by the runbook, not here.
#
# Usage: bash tests/test_weight_streaming.sh
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
T="$(mktemp -d "${TMPDIR:-/tmp}/obwsXXXXXX")"
trap 'rm -rf "$T"' EXIT

# 122 GiB usable on the 128 GB rig (MemTotal 127,926,272 kB, test_llama_serving.sh).
RIG_KB=127926272
source "$REPO_DIR/scripts/lib/memscope.sh"

echo ""
echo "per-profile memory scope (scripts/lib/memscope.sh):"
printf '#!/bin/bash\n# a daily profile\nexec serve.sh -m x\n' > "$T/serve-daily.sh"
printf '#!/bin/bash\n# openbeast-scope: mem-limit-pct=90 memlock=infinity host-class=rig min-mem-gib=118\n' > "$T/serve-big.sh"
_s() { _RC=0; _OUT="$(ob_mem_scope "$@" 2>&1)" || _RC=$?; }
_s "$T/serve-daily.sh" rig "$RIG_KB" 75
[[ $_RC -eq 0 && "$_OUT" == "75 - conf" ]] && pass "a profile with no declaration keeps MEM_LIMIT_PCT (75), no memlock" \
  || fail "daily profile: rc=$_RC '$_OUT'"
_s "$T/serve-big.sh" rig "$RIG_KB" 75
[[ $_RC -eq 0 && "$_OUT" == "90 infinity profile:serve-big.sh" ]] && pass "the big profile on the rig gets 90% and memlock infinity" \
  || fail "big profile on rig: rc=$_RC '$_OUT'"
_s "$T/serve-big.sh" rig "$RIG_KB" 60
[[ "$_OUT" == "90 infinity profile:serve-big.sh" ]] && pass "…whatever the conf's daily MEM_LIMIT_PCT is (60 here)" \
  || fail "big profile ignores conf pct: '$_OUT'"
_s "$T/serve-big.sh" rig "$RIG_KB" 75 85
[[ "$_OUT" == "85 infinity env" ]] && pass "…an explicit OPENBEAST_MEM_LIMIT_PCT for this launch still wins (memlock kept)" \
  || fail "env override: '$_OUT'"
_s "$T/serve-big.sh" spark "$RIG_KB" 75
[[ $_RC -eq 2 && "$_OUT" == *"rig-only profile"* ]] && pass "the big profile refuses a Spark (the raise is rig-only)" \
  || fail "big profile on spark: rc=$_RC '$_OUT'"
_s "$T/serve-big.sh" rig 65000000 75
[[ $_RC -eq 2 && "$_OUT" == *"at least 118 GiB"* ]] && pass "…and a 64 GB box (it could never hold the experts)" \
  || fail "big profile on 64 GB: rc=$_RC '$_OUT'"
printf '# openbeast-scope: mem-limit-pct=99\n' > "$T/serve-bad1.sh"
printf '# openbeast-scope: memlock=yes\n' > "$T/serve-bad2.sh"
printf '# openbeast-scope: mem-limt-pct=90\n' > "$T/serve-bad3.sh"
_bad=""
for _b in 1 2 3; do _s "$T/serve-bad$_b.sh" rig "$RIG_KB" 75; [[ $_RC -eq 2 ]] || _bad+=" bad$_b"; done
[[ -z "$_bad" ]] && pass "malformed declarations (pct 99, memlock=yes, a misspelt key) are refused, never ignored" \
  || fail "accepted:$_bad"
_s "$T/missing.sh" rig "$RIG_KB" 75
[[ "$_OUT" == "75 - conf" ]] && pass "a serve script that is not there declares nothing (start.sh reports it later)" \
  || fail "missing script: '$_OUT'"

# start.sh wires it into the daemon launch, and only there.
_st="$(sed -n '/>>> per-profile memory scope/,/<<< per-profile memory scope/p' "$REPO_DIR/start.sh")"
if [[ "$_st" == *'ob_mem_scope "$_scope_file"'* && "$_st" == *'LimitMEMLOCK=infinity'* \
      && "$_st" == *'export OPENBEAST_MEM_LIMIT_PCT="$MEM_LIMIT_PCT"'* ]] \
   && grep -q '${_SCOPE_PROPS\[@\]+"${_SCOPE_PROPS\[@\]}"}' "$REPO_DIR/start.sh"; then
  pass "start.sh -d sizes MemoryMax from ob_mem_scope, passes LimitMEMLOCK and carries the percent into the daemon"
else
  fail "start.sh's per-profile scope hook is missing or incomplete"
fi
if grep -q 'MEM_MAX_BYTES=$(( MEM_TOTAL_KB \* 1024 / 100 \* MEM_LIMIT_PCT ))' "$REPO_DIR/start.sh" \
   && [[ "$(grep -n 'per-profile memory scope (scripts' "$REPO_DIR/start.sh" | cut -d: -f1)" -lt \
         "$(grep -n 'MEM_MAX_BYTES=\$(( MEM_TOTAL_KB' "$REPO_DIR/start.sh" | cut -d: -f1)" ]]; then
  pass "…before MemoryMax is computed from the percent"
else
  fail "the scope hook runs after MEM_MAX_BYTES is computed"
fi

echo ""
echo "llama.cpp v1.0.0 flags (tests/fixtures/llama/arg-options-v1.0.0.txt):"
OPTS="$REPO_DIR/tests/fixtures/llama/arg-options-v1.0.0.txt"
_pin="$(sed -nE 's/^LLAMA_CPP_REF=([0-9a-f]{40})$/\1/p' "$REPO_DIR/scripts/llama.cpp.ref")"
grep -q "commit $_pin" "$OPTS" && pass "the option list is the pinned commit's (${_pin:0:12})" \
  || fail "scripts/llama.cpp.ref pins ${_pin:0:12} but the option fixture is for another commit — regenerate it"
# _flags <file> — every -flag word in the serve.sh exec block of a serve script.
_flags() {
  awk '/exec "\$SCRIPT_DIR\/serve.sh"/ {on = 1} on {print} on && !/\\$/ {on = 0}' "$1" \
    | tr ' \t' '\n\n' | grep -E '^--?[a-zA-Z]' | sed 's/=.*//' || true
}
_unknown=""; _n=0
for _ss in "$REPO_DIR"/scripts/serve-*.sh; do
  while IFS= read -r _f; do
    [[ -n "$_f" ]] || continue
    _n=$((_n + 1))
    grep -qxF -- "$_f" "$OPTS" || _unknown+=" $(basename "$_ss"):$_f"
  done < <(_flags "$_ss")
done
# serve.sh's own fixed flags on the llama-server exec line.
for _f in -m -ngl -c -np --kv-unified -ctk -ctv --metrics --host --port --cache-ram; do
  grep -qxF -- "$_f" "$OPTS" || _unknown+=" serve.sh:$_f"
done
[[ -z "$_unknown" && $_n -gt 50 ]] && pass "all $_n flags the serve scripts pass, and serve.sh's own, exist in v1.0.0" \
  || fail "flags v1.0.0 does not define (or too few found: $_n):$_unknown"
_removed=""
for _f in --defrag-thold -dt --mmap --mlock --direct-io -dio; do
  grep -rqE -- "(^|[[:space:]\"])$_f([[:space:]=\"]|$)" "$REPO_DIR"/scripts/serve*.sh "$REPO_DIR"/scripts/run*.sh \
    "$REPO_DIR"/scripts/profile-*.sh "$REPO_DIR"/scripts/measure-vram.sh 2>/dev/null && _removed+=" $_f"
done
[[ -z "$_removed" ]] && pass "no launcher uses an option v1.0.0 removed (--defrag-thold, --mmap, --mlock, --direct-io)" \
  || fail "removed in v1.0.0 but still passed:$_removed"

echo ""
echo "the MoE-cache and big-mode profiles:"
FN="$REPO_DIR/scripts/serve-qwen38-flash-next-unc-iq4xs-moecache.sh"
_fl="$(_flags "$FN" | tr '\n' ' ')"
if [[ -x "$FN" && "$_fl" == *"--moe-cache-mib"* && "$_fl" == *"--n-cpu-moe"* && "$_fl" == *"--cache-ram"* ]] \
   && [[ -z "$(ob_scope_decl "$FN")" ]]; then
  pass "Flash-Next + cache: host experts, a VRAM cache, a pinned prompt cache, and the daily 75% scope (no declaration)"
else
  fail "Flash-Next MoE-cache profile: flags '$_fl', scope '$(ob_scope_decl "$FN")'"
fi
# Its memory plan must fit the 75% scope: 40 CPU layers x 1.27 GiB + 27.63 GiB
# of tables + 1.5 buffers + the --cache-ram + 3 for the stack, against 75% of 122 GiB.
_ncm="$(awk '$1 == "--n-cpu-moe" {print $2}' "$FN")"; _cr="$(awk '$1 == "--cache-ram" {print $2}' "$FN")"
_need="$(awk -v n="$_ncm" -v c="$_cr" 'BEGIN {printf "%d", n * 1.27 + 27.63 + 1.5 + c / 1024 + 3}')"
_cap="$(awk -v k="$RIG_KB" 'BEGIN {printf "%d", k / 1048576 * 0.75}')"
[[ -n "$_ncm" && "$_need" -lt "$_cap" ]] && pass "…its host RAM plan (~${_need} GiB) fits the 75% scope (${_cap} GiB)" \
  || fail "Flash-Next MoE-cache needs ~${_need:-?} GiB of the ${_cap} GiB scope (n-cpu-moe '${_ncm}', cache-ram '${_cr}')"
grep -q -- '--cache-ram' "$REPO_DIR/scripts/serve-qwen38-flash-next-unc-iq4xs.sh" \
  && pass "…and the static Flash-Next baseline pins --cache-ram too (its experts share the scope)" \
  || fail "serve-qwen38-flash-next-unc-iq4xs.sh leaves the prompt cache unbounded beside 72 GiB of host weights"

BIG="$REPO_DIR/scripts/serve-glm53-flash-abl-q2kxl-big.sh"
_fl="$(_flags "$BIG" | tr '\n' ' ')"
_s "$BIG" rig "$RIG_KB" 75
if [[ -x "$BIG" && "$_OUT" == "90 infinity profile:serve-glm53-flash-abl-q2kxl-big.sh" \
      && "$_fl" == *"--cpu-moe"* && "$_fl" == *"--moe-cache-mib"* && "$_fl" == *"--load-mode"* ]]; then
  pass "big mode (GLM): all experts in host RAM, a VRAM cache, and the rig-only 90% / memlock scope"
else
  fail "big-mode profile: scope '$_OUT' flags '$_fl'"
fi
_s "$BIG" spark "$RIG_KB" 75
[[ $_RC -eq 2 ]] && pass "…which a Spark refuses" || fail "big-mode profile accepted on a spark: '$_OUT'"
# Host RAM plan against its 90% scope: 94.68 GiB of experts (the GGUF's
# tensor table) + 1.5 buffers + the --cache-ram + 3 for the stack.
_cr="$(awk '$1 == "--cache-ram" {print $2}' "$BIG")"
_need="$(awk -v c="$_cr" 'BEGIN {printf "%d", 94.68 + 1.5 + c / 1024 + 3}')"
_cap="$(awk -v k="$RIG_KB" 'BEGIN {printf "%d", k / 1048576 * 0.90}')"
[[ -n "$_cr" && "$_need" -lt "$_cap" ]] && pass "…its host RAM plan (~${_need} GiB) fits that scope (${_cap} GiB) with room to spare" \
  || fail "big mode needs ~${_need} GiB of a ${_cap} GiB scope"
# Every shard pinned with a real sha256, the remote names in the repo's
# subfolder, the local names one consistent prefix (llama.cpp rebuilds the
# siblings' names from shard 1's).
_rows="$(awk -F'\t' '$3 ~ /^Huihui-GLM-5\.3-Flash-abliterated-UD-Q2_K_XL-0000[1-4]-of-00004\.gguf$/ && length($1) == 64 && $1 !~ /[^0-9a-f]/ \
          && $4 == "huihui-ai/Huihui-GLM-5.3-Flash-abliterated-GGUF" && $5 ~ /^UD-Q2_K_XL\// {n++; b += $2} END {print n + 0, b + 0}' \
          "$REPO_DIR/scripts/weights.registry")"
[[ "$_rows" == "4 108720072064" ]] && pass "…its four shards are sha256-pinned (108,720,072,064 bytes = 101.3 GiB)" \
  || fail "GLM shard pins: '$_rows' (want 4 shards, 108720072064 bytes)"
# The weights must be uncensored (DGX_SPARK_PLAN §15): no shipped profile
# may point at a stock repo.
_stock=""
for _ss in "$FN" "$BIG"; do
  while IFS= read -r _w; do
    _repo="$(awk -F'\t' -v n="$_w" '$3 == n {print $4; exit}' "$REPO_DIR/scripts/weights.registry")"
    [[ "$_repo" =~ [Uu]ncensored|abliterated ]] || _stock+=" $(basename "$_ss"):$_repo"
  done < <(grep -oE '\$WEIGHTS_DIR/[^"]+\.gguf' "$_ss" | sed 's|.*/||')
done
[[ -z "$_stock" ]] && pass "both new profiles load uncensored (abliterated) weights" \
  || fail "a new profile loads a stock repo:$_stock"
_nb="$(python3 - "$REPO_DIR" <<'PY'
import ast, sys
src = open(sys.argv[1] + "/evals/benchmark_all.py").read()
t = ast.parse(src)
for n in ast.walk(t):
    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "BENCH_EXCLUDED":
        keys = [k.value for k in n.value.keys]
        print(sum(k in keys for k in ("scripts/serve-glm53-flash-abl-q2kxl-big.sh",
                                      "scripts/serve-qwen38-flash-next-unc-iq4xs-moecache.sh")))
PY
)"
[[ "$_nb" == 2 ]] && pass "…and neither joins a bare benchmark_all.py sweep (BENCH_EXCLUDED, with the reason)" \
  || fail "new profiles in BENCH_EXCLUDED: ${_nb:-0} of 2"

echo ""
echo "the engine gate (scripts/engine-gate.sh + tokps.py, a sandbox copy):"
SB="$T/sb"; mkdir -p "$SB/scripts" "$SB/.run" "$SB/bin"
cp -R "$REPO_DIR/scripts/lib" "$SB/scripts/"
cp "$REPO_DIR/scripts/engine-gate.sh" "$REPO_DIR/scripts/tokps.py" "$SB/scripts/"
mkdir -p "$SB/scripts/engines"
cp "$REPO_DIR/scripts/engines/llama.sh" "$SB/scripts/engines/"
printf 'eng_fakeeng_label() { echo Fake; }\neng_fakeeng_hosts() { echo rig; }\neng_fakeeng_formats() { echo GGUF; }\neng_fakeeng_installed() { echo 0.1; }\n' \
  > "$SB/scripts/engines/fakeeng.sh"
printf '#!/bin/bash\nexit 0\n' > "$SB/scripts/serve-same-weights.sh"; chmod +x "$SB/scripts/serve-same-weights.sh"
printf 'SEARXNG_SECRET=x\n' > "$SB/openbeast.conf"
# One stub server: "llama" decodes at 20 tok/s; the candidate at $SB/cand_tps.
cat > "$SB/stub.py" <<'PY'
import http.server, json, sys, threading, os
sb = sys.argv[1]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _who(self):
        try: return open(sb + "/active").read().strip()
        except OSError: return ""
    def do_GET(self):
        up = self._who() != ""
        body = json.dumps({"status": "ok"} if up else {"error": "down"}).encode()
        self.send_response(200 if up else 503); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
        who = self._who()
        tps = 20.0 if who == "llama" else float(open(sb + "/cand_tps").read())
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        ev = {"choices": [{"delta": {"content": "x"}}]}
        self.wfile.write(("data: %s\n\n" % json.dumps(ev)).encode())
        last = {"choices": [], "usage": {"completion_tokens": 16},
                "timings": {"predicted_per_second": tps, "prompt_per_second": 900.0}}
        self.wfile.write(("data: %s\n\ndata: [DONE]\n\n" % json.dumps(last)).encode())
threading.Timer(120, lambda: os._exit(0)).start()
s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
open(sb + "/port", "w").write(str(s.server_address[1]))
s.serve_forever()
PY
python3 "$SB/stub.py" "$SB" & _STUB=$!
trap 'kill $_STUB 2>/dev/null; wait $_STUB 2>/dev/null; rm -rf "$T"' EXIT
for _ in $(seq 1 50); do [[ -s "$SB/port" ]] && break; sleep 0.1; done
# engine.sh / start / stop stubs: record the calls, flip which "engine" serves.
cat > "$SB/bin/engine-sh" <<SH
#!/bin/bash
echo "engine.sh \$* gate=\${OPENBEAST_ENGINE_GATE:-}" >> "$SB/calls"
[[ "\$1" == use ]] && echo "\$2" > "$SB/backend"
exit 0
SH
cat > "$SB/bin/start" <<SH
#!/bin/bash
echo "start \$* gate=\${OPENBEAST_ENGINE_GATE:-}" >> "$SB/calls"
# cand_down: the candidate never comes up (a crashed launch).
[[ -f "$SB/cand_down" && "\$(cat "$SB/backend")" != llama ]] && exit 0
cp "$SB/backend" "$SB/active"
SH
cat > "$SB/bin/stop" <<SH
#!/bin/bash
echo "stop" >> "$SB/calls"; rm -f "$SB/active"
SH
chmod +x "$SB/bin/engine-sh" "$SB/bin/start" "$SB/bin/stop"
GATE_ENV=(OB_GATE_ENGINE_SH="$SB/bin/engine-sh" OB_ENGINE_START_CMD="$SB/bin/start" OB_ENGINE_STOP_CMD="$SB/bin/stop"
          OB_GATE_URL="http://127.0.0.1:$(cat "$SB/port")" OB_GATE_POLL=0.1 OB_ENGINE_HOST_CLASS=rig)
_gate() { # _gate <candidate tok/s> [extra engine-gate.sh args]
  echo "$1" > "$SB/cand_tps"; shift; : > "$SB/calls"; _RC=0
  _OUT="$(env "${GATE_ENV[@]}" bash "$SB/scripts/engine-gate.sh" fakeeng --profile p1 \
          --serve-script serve-same-weights.sh --runs 2 "$@" 2>&1)" || _RC=$?
}
_admit() { ( REPO_DIR="$SB"; source "$SB/scripts/lib/engine.sh"; ob_engine_gate_ok fakeeng 2>&1 ) && echo ADMITTED; }
_gate 29
_v="$SB/.engines/gate/fakeeng.json"
if [[ $_RC -eq 1 && "$_OUT" == *"FAIL"*"1.45x"* ]] && grep -q '"pass": false' "$_v" 2>/dev/null \
   && grep -q 'engine.sh use fakeeng --profile p1 --no-restart gate=measuring' "$SB/calls" \
   && grep -q 'start  gate=measuring' "$SB/calls" && grep -q 'start serve-same-weights.sh gate=$' "$SB/calls" \
   && [[ "$(tail -n 1 "$SB/backend")" == llama ]]; then
  pass "29 vs 20 tok/s (1.45x) fails the gate, the verdict is recorded, and the box is back on llama"
else
  fail "gate at 1.45x (rc=$_RC): $(tail -n 3 <<< "$_OUT" | tr '\n' ' ') :: $(tr '\n' ';' < "$SB/calls")"
fi
_gate 31
if [[ $_RC -eq 0 && "$_OUT" == *"PASS"* ]] && grep -q '"pass": true' "$_v" \
   && ( REPO_DIR="$SB"; source "$SB/scripts/lib/engine.sh"; ob_engine_gate_ok fakeeng 2>/dev/null ); then
  pass "31 vs 20 tok/s (1.55x) passes, and ob_engine_gate_ok then admits the engine"
else
  fail "gate at 1.55x (rc=$_RC): $(tail -n 3 <<< "$_OUT" | tr '\n' ' ')"
fi
if grep -q $'\tfakeeng\tgate\t' "$SB/.engines/history.tsv" 2>/dev/null; then
  pass "…each verdict is a line in .engines/history.tsv"
else
  fail "no gate line in history.tsv"
fi
# Without a verdict, or with a failing one, the gate holds.
_e="$( REPO_DIR="$SB"; source "$SB/scripts/lib/engine.sh"; ob_engine_gate_ok noverdict 2>&1 )" || true
[[ "$_e" == *"no measurement on file"* ]] && pass "an engine with no verdict on file is refused, with the command to measure it" \
  || fail "no-verdict refusal: '$_e'"
python3 - "$_v" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); d["ratio"] = 1.3; d["pass"] = True   # a hand-edited "pass" below 1.5
json.dump(d, open(sys.argv[1], "w"))
PY
_e="$( REPO_DIR="$SB"; source "$SB/scripts/lib/engine.sh"; ENGINE_GATE_RATIO=1.1 ob_engine_gate_ok fakeeng 2>&1 )" && _e="ADMITTED"
[[ "$_e" == *"does not admit this"* ]] && pass "…and a verdict below 1.5x is refused even marked pass, even with ENGINE_GATE_RATIO=1.1" \
  || fail "a 1.3x verdict was admitted: '$_e'"

# A raised bar is ONE bar: openbeast.conf's ENGINE_GATE_RATIO reaches the
# gate run (tokps --ratio, the verdict's "required") and the admission.
printf 'SEARXNG_SECRET=x\nENGINE_GATE_RATIO=1.6\n' > "$SB/openbeast.conf"
_gate 31
_e="$(_admit)"
if [[ $_RC -eq 1 && "$_OUT" == *"FAIL"*"needs 1.6x"* && "$_OUT" != *"PASS: fakeeng may be used"* ]] \
   && grep -q '"required": 1.6' "$_v" && [[ "$_e" != *ADMITTED* ]]; then
  pass "ENGINE_GATE_RATIO=1.6 in openbeast.conf: the gate run FAILs 1.55x itself (no PASS that use then refuses)"
else
  fail "conf ratio 1.6 (rc=$_RC): $(tail -n 2 <<< "$_OUT" | tr '\n' ' ') / $_e"
fi
printf 'SEARXNG_SECRET=x\n' > "$SB/openbeast.conf"

# The verdict is bound to the host it measured.
_gate 31
python3 - "$_v" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); d["host"] = "some-other-box"
json.dump(d, open(sys.argv[1], "w"))
PY
_e="$(_admit)"
[[ "$_e" == *"measured on 'some-other-box'"* && "$_e" != *ADMITTED* ]] \
  && pass "a PASS measured on another host does not admit the engine here" || fail "foreign-host verdict: '$_e'"

# A re-measure that dies leaves the engine gated, not on the old PASS.
_gate 31
touch "$SB/cand_down"
_gate 31 --load-timeout 1
_e="$(_admit)"
if [[ $_RC -eq 3 && ! -f "$_v" && -f "$_v.prev" && "$_e" == *"no measurement on file"* \
      && "$(cat "$SB/backend")" == llama ]]; then
  pass "a gate run whose candidate never comes up: the old PASS is moved aside, the engine is gated, the box on llama"
else
  fail "failed re-measure (rc=$_RC, verdict $( [[ -f "$_v" ]] && echo present || echo gone)): $_e / $(tail -n 2 <<< "$_OUT" | tr '\n' ' ')"
fi

# Ctrl-C / TERM mid-run (the trap): back on llama, and a stack that was
# running is started again.
# A "running stack": a recorded supervisor whose cmdline says start.sh (one
# process, so killing it leaves nothing holding this test's stdout open).
( exec -a "$T/start.sh" sleep 120 ) & _SUP=$!
echo "$_SUP" > "$SB/.run/supervisor.pid"
echo 31 > "$SB/cand_tps"; : > "$SB/calls"
env "${GATE_ENV[@]}" bash "$SB/scripts/engine-gate.sh" fakeeng --profile p1 --serve-script serve-same-weights.sh \
  --runs 1 --load-timeout 60 > "$T/gate-int.log" 2>&1 & _G=$!
for _ in $(seq 1 100); do grep -q 'use fakeeng' "$SB/calls" 2>/dev/null && break; sleep 0.1; done
sleep 0.3; kill -TERM "$_G" 2>/dev/null; _RC=0; wait "$_G" || _RC=$?
kill "$_SUP" 2>/dev/null; wait "$_SUP" 2>/dev/null; rm -f "$SB/.run/supervisor.pid" "$SB/cand_down"
_tail="$(awk '/use fakeeng/ {seen = 1} seen' "$SB/calls" | tr '\n' ';')"
if [[ $_RC -eq 143 && "$(cat "$SB/backend")" == llama && "$_tail" == *"use llama --no-restart"*";start  gate=;"* ]]; then
  pass "TERM during the candidate's load: the trap switches back to llama and restarts the stack that was running"
else
  fail "TERM mid-gate (rc=$_RC, backend $(cat "$SB/backend")): $_tail / $(tail -n 3 "$T/gate-int.log" | tr '\n' ' ')"
fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
