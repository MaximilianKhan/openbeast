#!/bin/bash
# End to end: scripts/hydra-sim.sh (fake fleet + a real agents/hydra.py) runs
# the five plan scenarios, then a held sim is driven through every
# scripts/hydra.sh command. Real processes on ephemeral 127.0.0.1 ports; no
# GPU, no docker, never :8080. Skips when loopback ports cannot be bound.
# Everything started here is stopped here, by pid.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  ok: $*"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL: $*"; }

if ! python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); s.close()' 2>/dev/null; then
  echo "SKIP: cannot bind a loopback port"; exit 0
fi
if ! python3 -c 'import httpx, uvicorn, starlette' 2>/dev/null; then
  echo "SKIP: httpx/uvicorn/starlette not importable"; exit 0
fi
unset http_proxy HTTP_PROXY https_proxy HTTPS_PROXY all_proxy ALL_PROXY
export OPENBEAST_HYDRA_NO_CONF=1        # never read this box's openbeast.conf

W="$(mktemp -d "${TMPDIR:-/tmp}/hydra-sim-test-XXXXXX")"
SIM_PID=""
cleanup() {
  if [[ -n "$SIM_PID" ]] && kill -0 "$SIM_PID" 2>/dev/null; then
    kill "$SIM_PID" 2>/dev/null
    wait "$SIM_PID" 2>/dev/null
  fi
  rm -rf "$W"
}
trap cleanup EXIT

echo "=== hydra-sim.sh --scenarios ==="
out="$(timeout 240 bash "$REPO_DIR/scripts/hydra-sim.sh" --scenarios 2>&1)"; rc=$?
echo "$out" | sed 's/^/    /'
[[ $rc -eq 0 ]] && ok "all scenarios passed" || bad "scenarios exit $rc"
for i in 1 2 3 4 5; do
  grep -q "^PASS $i " <<< "$out" && ok "scenario $i" || bad "scenario $i did not pass"
done

echo "=== scripts/hydra.sh against a held sim ==="
bash "$REPO_DIR/scripts/hydra-sim.sh" --hold --dir "$W/sim" --env-file "$W/env" > "$W/sim.log" 2>&1 &
SIM_PID=$!
for _ in $(seq 1 300); do [[ -s "$W/env" ]] && break; kill -0 "$SIM_PID" 2>/dev/null || break; sleep 0.1; done
if [[ ! -s "$W/env" ]]; then
  bad "the held sim never came up"; cat "$W/sim.log"; echo "  $PASS passed, $FAIL failed"; exit 1
fi
set -a
# shellcheck source=/dev/null
source "$W/env"
set +a
H="$REPO_DIR/scripts/hydra.sh"

out="$(bash "$H" check "$OPENBEAST_HYDRA_CONFIG" 2>&1)" && grep -q '^OK ' <<< "$out" \
  && ok "check: valid config" || bad "check: $out"
printf 'schema = 1\n[nodes.x]\nurl = "http://8.8.8.8:1"\nengine = "llama"\n' > "$W/bad.toml"
out="$(bash "$H" check "$W/bad.toml" 2>&1)"; rc=$?
[[ $rc -eq 1 ]] && grep -q "ERROR: .*is public" <<< "$out" && ok "check: invalid config fails closed" \
  || bad "check bad: rc=$rc $out"

out="$(bash "$H" status 2>&1)"
grep -q "qwen38-unc-q5@rig *READY" <<< "$out" && grep -q "^ROUTES" <<< "$out" \
  && ok "status table" || bad "status: $out"
bash "$H" status --json | python3 -c 'import json,sys; assert json.load(sys.stdin)["deployments"]' \
  && ok "status --json" || bad "status --json"

out="$(bash "$H" explain '{"model":"qwen-27b-q5","messages":[{"role":"user","content":"hi"}]}')"
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["route"]=="beast" and d["attempts"][0]["d"]=="qwen38-unc-q5@rig", d' "$out" \
  && ok "explain resolves an alias" || bad "explain: $out"
printf '{"model":"beast:fast","messages":[]}' > "$W/req.json"
out="$(bash "$H" explain -f "$W/req.json" -H 'X-Conversation-Id: c1')"
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["route"]=="beast:fast", d' "$out" \
  && ok "explain -f -H" || bad "explain -f: $out"

bash "$H" drain rig >/dev/null && grep -q "DRAINED(manual)" <<< "$(bash "$H" status)" \
  && ok "drain" || bad "drain"
out="$(bash "$H" explain '{"model":"beast","messages":[]}')"
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["attempts"][0]["d"]=="qwen38-nvfp4@sparks", d' "$out" \
  && ok "drained rig is skipped" || bad "explain after drain: $out"
bash "$H" undrain rig >/dev/null && ! grep -q "DRAINED" <<< "$(bash "$H" status)" \
  && ok "undrain" || bad "undrain"

out="$(bash "$H" pin-smoke all 2>&1)"; rc=$?
[[ $rc -eq 0 ]] && [[ "$(grep -c ' ok$' <<< "$out")" -eq 6 ]] && ok "pin-smoke all (3 deployments x 2)" \
  || bad "pin-smoke rc=$rc: $out"
out="$(HYDRA_KEY_FILE="$W/nope" bash "$H" pin-smoke all 2>&1)"; rc=$?
[[ $rc -ne 0 ]] && ok "pin-smoke refuses an unreadable key file" || bad "pin-smoke with a bad key file passed"

bash "$H" decisions 3 | python3 -c 'import json,sys; assert json.load(sys.stdin)["decisions"]' \
  && ok "decisions" || bad "decisions"
grep -q '^hydra_config_info{hash=' <<< "$(bash "$H" metrics)" && ok "metrics" || bad "metrics"
out="$(bash "$H" tail 3)"
[[ "$(wc -l <<< "$out")" -eq 3 ]] && grep -q -- "-> pin" <<< "$out" && ok "tail" || bad "tail: $out"
bash "$H" reload | python3 -c 'import json,sys; assert json.load(sys.stdin)["ok"]' \
  && ok "reload" || bad "reload"

out="$(bash "$H" add-node newbox --url "$HYDRA_SIM_TI" --engine llama --key-file "$W/sim/ti.key" \
        --slots 2 --deployment moe@newbox 2>"$W/add.err")"; rc=$?
[[ $rc -eq 0 ]] && grep -q '^\[nodes.newbox\]' <<< "$out" && grep -q 'upstream = "qwen36-35b-a3b-q4"' <<< "$out" \
  && ok "add-node probes and prints a stanza" || bad "add-node rc=$rc: $out $(cat "$W/add.err")"
python3 - "$OPENBEAST_HYDRA_CONFIG" "$out" <<'PYEOF' && ok "add-node never edits hydra.toml" || bad "hydra.toml changed"
import sys
assert "newbox" not in open(sys.argv[1]).read()
PYEOF
out="$(bash "$H" add-node pub --url http://8.8.8.8:8080 --engine llama 2>&1)"; rc=$?
[[ $rc -ne 0 ]] && grep -q "public" <<< "$out" && ok "add-node refuses a public URL" || bad "add-node public: $out"
out="$(bash "$H" add-node tf --url "$HYDRA_SIM_TI" --engine tensorfold --key-file "$W/sim/ti.key" 2>&1)"; rc=$?
[[ $rc -ne 0 ]] && grep -q "never give it a key" <<< "$out" && ok "add-node refuses a key for TensorFold" \
  || bad "add-node tf key: $out"

timeout 180 bash "$H" conformance qwen38-nvfp4@sparks >"$W/conf.out" 2>&1
rep="$OPENBEAST_HYDRA_RUN_DIR/conformance/qwen38-nvfp4@sparks/latest.json"
python3 - "$rep" "$HYDRA_SIM_SPARKS" <<'PYEOF' && ok "conformance report lands per deployment, keyed, for the node" || { bad "conformance"; tail -5 "$W/conf.out"; }
import json, sys
d = json.load(open(sys.argv[1]))
assert d["url"] == sys.argv[2], d["url"]
assert d["facts"].get("model") == "qwen3.8-27b-nvfp4", d["facts"]
r = {x["name"]: x for x in d["results"]}
assert r["models"]["status"] == "pass", r["models"]     # the node key reached it (vLLM guards /v1)
PYEOF

out="$(OPENBEAST_HYDRA_RUN_DIR="$W/empty" bash "$H" status 2>&1)"; rc=$?
[[ $rc -ne 0 ]] && grep -q "hydra-local.token" <<< "$out" && ok "admin needs the local token file" \
  || bad "status without token: $out"

hport="${HYDRA_URL##*:}"
kill "$SIM_PID" 2>/dev/null; wait "$SIM_PID" 2>/dev/null; SIM_PID=""
if python3 -c 'import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) else 1)' "$hport"; then
  ok "stopping the sim stops its hydra"
else
  bad "hydra still listening on :$hport after the sim stopped"
fi

echo "  $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
