#!/bin/bash
# Readiness parity: beast-hydra's Python probe (hydra_core.engine_ready) and
# the stack's bash probe (scripts/lib/backend.sh ob_backend_ready) must never
# disagree about whether an engine can serve (docs/BEAST_HYDRA_PLAN.md F3).
#
# One stdlib server on an ephemeral 127.0.0.1 port serves every fixture in
# tests/fixtures/hydra/ready/ at /<name>/health. For each fixture and each
# engine, bash asks ob_backend_ready and Python fetches the same URL and asks
# engine_ready; both answers must match each other AND the fixture's
# expectation. Negative controls are fixtures (llama 200 without ok, a
# TensorFold 200 without ok, a vLLM 503), plus a mutant predicate that must
# disagree — proof this test can fail.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FIX="$REPO_DIR/tests/fixtures/hydra/ready"
PASS=0; FAIL=0
ok()   { PASS=$((PASS + 1)); }
bad()  { FAIL=$((FAIL + 1)); echo "  FAIL: $*"; }

# shellcheck source=../scripts/lib/backend.sh
source "$REPO_DIR/scripts/lib/backend.sh"
unset http_proxy HTTP_PROXY https_proxy HTTPS_PROXY all_proxy ALL_PROXY

WORK="$(mktemp -d "${TMPDIR:-/tmp}/hydra-parity-XXXXXX")"
SRV_PID=""
cleanup() {
  [[ -n "$SRV_PID" ]] && kill "$SRV_PID" 2>/dev/null && wait "$SRV_PID" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

python3 - "$FIX" "$WORK/port" <<'PYEOF' &
import http.server, json, pathlib, sys
fx = {p.stem: json.loads(p.read_text()) for p in pathlib.Path(sys.argv[1]).glob("*.json")}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        name = self.path.strip("/").split("/")[0]
        f = fx.get(name)
        if f is None or not self.path.endswith("/health"):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        b = f["body"].encode()
        self.send_response(f["status"])
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
pathlib.Path(sys.argv[2]).write_text(str(srv.server_address[1]))
srv.serve_forever()
PYEOF
SRV_PID=$!
for _ in $(seq 1 100); do [[ -s "$WORK/port" ]] && break; sleep 0.05; done
[[ -s "$WORK/port" ]] || { echo "SKIP: could not start the fixture server (port binding?)"; exit 0; }
BASE="http://127.0.0.1:$(cat "$WORK/port")"

# Python's view, fetched over the same HTTP: name engine answer (one line each)
python3 - "$REPO_DIR" "$BASE" "$FIX" > "$WORK/py.txt" <<'PYEOF'
import json, pathlib, sys, urllib.error, urllib.request
sys.path.insert(0, sys.argv[1] + "/agents")
import hydra_core as core
for p in sorted(pathlib.Path(sys.argv[3]).glob("*.json")):
    try:
        with urllib.request.urlopen(f"{sys.argv[2]}/{p.stem}/health", timeout=3) as r:
            st, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        st, body = e.code, e.read()
    for eng in ("llama", "vllm", "tensorfold"):
        print(p.stem, eng, core.engine_ready(eng, st, body))
PYEOF

echo "=== Readiness parity: hydra_core.engine_ready vs backend.sh ob_backend_ready ==="
n=0
while read -r name eng py; do
  n=$((n + 1))
  want="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["expect"][sys.argv[2]])' \
          "$FIX/$name.json" "$eng")"
  if ob_backend_ready "$BASE/$name" "$eng"; then sh="ready"; else sh="not-ready"; fi
  echo "$name $eng $sh" >> "$WORK/sh.txt"
  pyb="not-ready"; [[ "$py" == "ready" ]] && pyb="ready"
  if [[ "$sh" == "$pyb" ]]; then ok; else bad "$name/$eng: bash=$sh python=$py"; fi
  if [[ "$py" == "$want" ]]; then ok; else bad "$name/$eng: python=$py expected=$want"; fi
done < "$WORK/py.txt"
[[ $n -ge 30 ]] && ok || bad "only $n fixture×engine cases ran"

# Negative controls exist as fixtures…
for need in llama_200_not_ok tf_ok_false vllm_503_dead; do
  [[ -f "$FIX/$need.json" ]] && ok || bad "negative-control fixture $need.json is missing"
done
# …and a mutant Python predicate ("any HTTP 200 is ready", the bug bash
# probes had before backend.sh) must disagree with bash somewhere: otherwise
# this test could not catch a real divergence.
diffs="$(python3 - "$WORK/sh.txt" "$FIX" <<'PYEOF'
import json, sys
n = 0
for line in open(sys.argv[1]):
    name, eng, sh = line.split()
    st = json.load(open(f"{sys.argv[2]}/{name}.json"))["status"]
    mutant = "ready" if st == 200 else "not-ready"
    n += mutant != sh
print(n)
PYEOF
)"
if [[ "${diffs:-0}" -ge 1 ]]; then ok; else bad "a mutant predicate agreed with bash everywhere — fixtures lack teeth"; fi

echo "  $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
