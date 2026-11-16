#!/bin/bash
# Engine-plane tests (docs/ENGINES.md): scripts/engine.sh, scripts/lib/engine.sh,
# the adapters in scripts/engines/, scripts/engine-serve.sh and the hooks in
# start.sh / stop.sh / healthcheck.sh / conf.sh / backend.sh.
#
# Hermetic: no GPU, no network, no real docker. docker, curl's GitHub
# downloads, the llama.cpp build, nvidia-smi and pkill/pgrep are stubs; the
# GitHub API and every "engine" are throwaway HTTP stubs on 127.0.0.1 /
# a random 127.x address; llama.cpp "upstream" is a local git repo; the
# TensorFold releases are real signed archives made with a key generated
# for this run and checked by the REAL install-native.sh (its vendored key
# swapped for ours in a sandbox copy). Everything runs in a sandbox copy of
# the repo, so the real openbeast.conf and scripts/llama.cpp.ref are never
# touched. Needs GNU coreutils and OpenSSL >= 3 (ed25519).
#
# Usage: bash tests/test_engines.sh
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
check() { if eval "$2"; then pass "$1"; else fail "$1${3:+ — $3}"; fi; }

T="$(mktemp -d "${TMPDIR:-/tmp}/obenginesXXXXXX")"
_PIDS=""
cleanup() {
  local p
  for p in $_PIDS; do kill "$p" 2>/dev/null || true; done
  # anything a docker-stub "container" left running
  for p in "$T"/dstate*/run-*.pid; do [[ -f "$p" ]] && kill "$(cat "$p")" 2>/dev/null || true; done
  rm -rf "$T"
}
trap cleanup EXIT
PY="$(command -v python3)"
_free_port() { "$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
_LO="127.$((20 + RANDOM % 200)).$((RANDOM % 250)).$((2 + RANDOM % 250))"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_NOSYSTEM=1

# ── HTTP stubs ─────────────────────────────────────────────────────────────
cat > "$T/httpstub.py" <<'PY'
import http.server, json, sys, threading, os
host, port, mode = sys.argv[1], int(sys.argv[2]), sys.argv[3]
REL = {"/repos/vllm-project/vllm/releases/latest": {"name": "v0.33.0", "tag_name": "v0.33.0rc3"},
       "/repos/ashhart/TensorFold/releases/latest": {"name": "TensorFold 1.2.9", "tag_name": "v1.2.9"}}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        code, body = 404, ""
        if mode == "github" and self.path in REL: code, body = 200, json.dumps(REL[self.path])
        elif mode == "vllm" and self.path == "/health": code, body = 200, ""
        elif mode == "tf" and self.path == "/health": code, body = 200, json.dumps({"ok": True})
        elif self.path == "/v1/models": code, body = 200, json.dumps({"data": [{"id": "rig-model"}]})
        self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body.encode())
threading.Timer(90, lambda: os._exit(0)).start()
http.server.ThreadingHTTPServer((host, port), H).serve_forever()
PY
GH_PORT="$(_free_port)"
"$PY" "$T/httpstub.py" 127.0.0.1 "$GH_PORT" github & _PIDS="$_PIDS $!"
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$GH_PORT") 2>/dev/null && break; sleep 0.1; done

# ── stubs on PATH ──────────────────────────────────────────────────────────
mkdir -p "$T/bin" "$T/home"
for c in nvidia-smi pkill pgrep killall systemctl systemd-run sudo tailscale; do
  printf '#!/bin/bash\necho "%s $*" >> "%s/calls.log"\nexit 1\n' "$c" "$T" > "$T/bin/$c"; chmod +x "$T/bin/$c"
done
: > "$T/calls.log"
# docker: images are lines "repo tag digest id" in $DOCKER_STUB_STATE/images;
# `run` serves an engine stub on --host/--port in the foreground.
cat > "$T/bin/docker" <<EOF
#!/bin/bash
S="\${DOCKER_STUB_STATE:?}"; mkdir -p "\$S"; touch "\$S/images"
echo "docker \$*" >> "\$S/calls.log"
H="$T/httpstub.py"; PY="$PY"
EOF
cat >> "$T/bin/docker" <<'EOF'
_h() { printf '%s' "$1" | sha256sum | cut -c1-64; }
lookup() { # lookup <ref> → "repo tag digest id" (last match)
  local r="$1"
  awk -v r="$r" '{ if (r == $1":"$2 || r == $1"@"$3 || r == $4) line = $0 } END { if (line != "") print line; else exit 1 }' "$S/images"
}
cmd="$1"; shift
case "$cmd" in
  pull)
    [[ -n "${DOCKER_STUB_FAIL_PULL:-}" ]] && { echo "pull refused" >&2; exit 1; }
    ref="$1"
    # the "registry" serves sha256(repo:tag) digests; anything else is unknown
    if [[ "$ref" == *@sha256:0000* ]]; then echo "manifest unknown" >&2; exit 1; fi
    if [[ "$ref" == *@* ]]; then repo="${ref%@*}"; tag="-"; dig="${ref#*@}"
    else repo="${ref%:*}"; tag="${ref##*:}"; dig="sha256:$(_h "$ref")"; fi
    echo "$repo $tag $dig sha256:$(_h "id-$dig")" >> "$S/images" ;;
  tag)
    l="$(lookup "$1")" || exit 1; read -r _ _ dig id <<< "$l"
    echo "${2%:*} ${2##*:} $dig $id" >> "$S/images" ;;
  image)
    [[ "$1" == inspect ]] || exit 1; shift; fmt=""
    [[ "${1:-}" == --format ]] && { fmt="$2"; shift 2; }
    l="$(lookup "$1")" || exit 1; read -r repo tag dig id <<< "$l"
    case "$fmt" in
      *RepoDigests*) [[ "$dig" != - ]] && echo "$repo@$dig" ;;
      *.Id*) echo "$id" ;;
      *) echo '[{}]' ;;
    esac ;;
  save)
    [[ "$1" == -o ]] || exit 1; out="$2"; l="$(lookup "$3")" || exit 1; echo "IMAGE $l" > "$out" ;;
  load)
    [[ "$1" == -i ]] || exit 1; read -r _ repo tag dig id < "$2"
    [[ -n "${DOCKER_STUB_LOAD_DROP_DIGEST:-}" ]] && dig=-
    echo "$repo $tag $dig $id" >> "$S/images"; echo "Loaded image: $repo:$tag" ;;
  run)
    name="" host=127.0.0.1 port=""
    while [[ $# -gt 0 ]]; do
      case "$1" in --name) name="$2"; shift ;; --host) host="$2"; shift ;; --port) port="$2"; shift ;; esac; shift
    done
    "$PY" "$H" "$host" "$port" vllm & c=$!
    echo "$c" > "$S/run-$name.pid"
    trap 'kill $c 2>/dev/null; rm -f "$S/run-$name.pid"; exit 143' TERM INT
    wait "$c"; rm -f "$S/run-$name.pid" ;;
  inspect)
    if [[ "$1" == -f ]]; then n="$3"; else n="$1"; fi
    [[ -f "$S/run-$n.pid" ]] && kill -0 "$(cat "$S/run-$n.pid")" 2>/dev/null || exit 1
    [[ "$1" == -f ]] && echo true; exit 0 ;;
  stop|rm)
    while [[ "${1:-}" == -* ]]; do [[ "$1" == -t ]] && shift; shift; done
    n="$1"; [[ -f "$S/run-$n.pid" ]] || { [[ "$cmd" == rm ]] && exit 0; exit 1; }
    kill "$(cat "$S/run-$n.pid")" 2>/dev/null; rm -f "$S/run-$n.pid"; echo "$n" ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$T/bin/docker"
# curl: TensorFold release downloads come from a local folder; the rest is real curl.
REAL_CURL="$(command -v curl)"
cat > "$T/bin/curl" <<EOF
#!/bin/bash
for a in "\$@"; do
  case "\$a" in
    https://github.com/ashhart/TensorFold/releases/download/*)
      echo "curl-github \$a" >> "$T/calls.log"
      f="\${a#https://github.com/ashhart/TensorFold/releases/download/}"
      out=""; prev=""; for b in "\$@"; do [[ "\$prev" == -o ]] && out="\$b"; prev="\$b"; done
      [[ -f "$T/tfrel/\$f" ]] || exit 22
      cp "$T/tfrel/\$f" "\$out"; exit 0 ;;
  esac
done
exec "$REAL_CURL" "\$@"
EOF
chmod +x "$T/bin/curl"

# ── the sandbox repo ───────────────────────────────────────────────────────
mksandbox() { # mksandbox <dir>
  local d="$1"
  mkdir -p "$d/.run" "$d/extensions"
  cp -R "$REPO_DIR/scripts" "$d/scripts"
  rm -f "$d/scripts/backends/spark.env"
  cp "$REPO_DIR/start.sh" "$REPO_DIR/stop.sh" "$REPO_DIR/openbeast.conf.example" "$d/"
  : > "$d/openbeast.conf"; chmod 600 "$d/openbeast.conf"
  printf '#!/bin/bash\necho "serve-marker $0" >> "%s/calls.log"\nexit 7\n' "$T" > "$d/scripts/serve-marker.sh"
  chmod +x "$d/scripts/serve-marker.sh"
}
SB="$T/repo"; mksandbox "$SB"
DST="$T/dstate"
BASE_ENV=(HOME="$T/home" PATH="$T/bin:$(dirname "$PY"):/usr/bin:/bin" OPENBEAST_SEARXNG_SECRET=x
  OPENBEAST_BIND="$_LO" OPENBEAST_GPU_BACKEND=cpu OPENBEAST_LOGROTATE_AUTOINSTALL=false
  OB_ENGINE_HOST_CLASS=rig OB_ENGINE_ARCH=x86_64 OB_ENGINE_TF_PLATFORM=linux-x86_64
  OB_ENGINE_GITHUB_API="http://127.0.0.1:$GH_PORT" OB_ENGINE_LLAMA_URL="file://$T/up"
  OB_ENGINE_LLAMA_BUILD="$T/bin/fake-build" OB_ENGINE_TF_INSTALLER="$T/tfinst/install-native.sh"
  GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t)
eng() { # eng [VAR=val ...] -- args   (runs the sandbox's engine.sh)
  local extra=()
  while [[ $# -gt 0 && "$1" != -- ]]; do extra+=("$1"); shift; done
  [[ "${1:-}" == -- ]] && shift
  env -i "${BASE_ENV[@]}" DOCKER_STUB_STATE="$DST" ${extra[@]+"${extra[@]}"} timeout 60 bash "$SB/scripts/engine.sh" "$@" 2>&1 || true
}
confv() { grep -E "^$1=" "$SB/openbeast.conf" | tail -n1 | cut -d= -f2-; }

# ── fixtures: checkpoints + profiles ───────────────────────────────────────
mkdir -p "$T/ckpt" "$T/ckpt-tf" "$T/prof"
echo '{"model_type": "qwen3"}' > "$T/ckpt/config.json"
echo '{"model_type": "qwen3"}' > "$T/ckpt-tf/config.json"
printf 'BACKEND=vllm\nSOURCE=%s\nSERVED_MODEL_NAME=rig-model\nTENSOR_PARALLEL_SIZE=1\nMAX_NUM_SEQS=4\nGPU_MEMORY_UTILIZATION=0.90\nREASONING_PARSER=qwen3\nTOOL_CALL_PARSER=qwen3_xml\n' "$T/ckpt" > "$T/prof/rigvllm.env"
printf 'BACKEND=vllm\nSOURCE=%s\nSERVED_MODEL_NAME=two\nTENSOR_PARALLEL_SIZE=2\n' "$T/ckpt" > "$T/prof/tp2.env"
printf 'BACKEND=tensorfold\nSOURCE=%s\nSERVED_MODEL_NAME=rig-model\nTENSOR_PARALLEL_SIZE=1\nTENSORFOLD_PARALLEL=2\n' "$T/ckpt-tf" > "$T/prof/rigtf.env"
# A local SOURCE is only "fetched + verified" once it has a lock (model-fetch
# refuses an unlocked local copy since the supply-chain review): record each
# one the way an operator would, with a plain fetch of the profile.
for _p in rigvllm tp2 rigtf; do
  python3 "$REPO_DIR/scripts/backends/pylib/model_fetch.py" --profile "$T/prof/$_p.env" >/dev/null 2>&1 \
    || { echo "fixture: could not lock $_p"; exit 1; }
done

# ════════════════════════════════════════════════════════════════════════════
echo "Library: engines, host class, defaults, conf writes"
_lib() { env -i "${BASE_ENV[@]}" REPO_DIR="$SB" "$@"; }
_o="$(_lib bash -c 'source "$REPO_DIR/scripts/lib/engine.sh"; ob_engines')"
check "the four shipped adapters are discovered, in order (maya last: opt-in, gated)" '[[ "$_o" == "llama vllm tensorfold maya" ]]' "$_o"
cp "$SB/scripts/engines/vllm.sh" "$T/sglang.sh"; sed -i 's/eng_vllm_/eng_sglang_/g' "$T/sglang.sh"
cp "$T/sglang.sh" "$SB/scripts/engines/sglang.sh"
_o="$(_lib bash -c 'source "$REPO_DIR/scripts/lib/engine.sh"; ob_engines; source "$REPO_DIR/scripts/lib/backend.sh"; ob_backend_normalize sglang; ob_backend_normalize nonsense 2>&1')"
check "a 4th engine is one new adapter file: listed, and a valid INFERENCE_BACKEND" \
  '[[ "$_o" == *"llama vllm tensorfold maya sglang"* && "$_o" == *$'"'"'\nsglang\n'"'"'* && "$_o" == *"not one of"*llama ]]' "$_o"
rm -f "$SB/scripts/engines/sglang.sh"
_o="$(_lib OB_ENGINE_HOST_CLASS= bash -c 'source "$REPO_DIR/scripts/lib/engine.sh"; ob_engine_host_class')"
check "no GPU (nvidia-smi fails) → host class rig" '[[ "$_o" == rig ]]' "$_o"
mkdir -p "$T/gb10bin"; printf '#!/bin/bash\ncase "$*" in *name*) echo "NVIDIA GB10";; *memory.total*) echo "[N/A]";; esac\nexit 0\n' > "$T/gb10bin/nvidia-smi"; chmod +x "$T/gb10bin/nvidia-smi"
printf 'MemTotal:       127598416 kB\n' > "$T/meminfo"
_o="$(env -i "${BASE_ENV[@]}" PATH="$T/gb10bin:$T/bin:/usr/bin:/bin" OB_MEMINFO="$T/meminfo" OB_ENGINE_HOST_CLASS= REPO_DIR="$SB" bash -c 'source "$REPO_DIR/scripts/lib/engine.sh"; c=$(ob_engine_host_class); echo "$c $(ob_engine_default "$c")"')"
check "GB10 (memory.total [N/A], OB_GPU_UNIFIED) → host class spark, default tensorfold" '[[ "$_o" == "spark tensorfold" ]]' "$_o"
_o="$(_lib OPENBEAST_ENGINE_DEFAULT=vllm bash -c 'source "$REPO_DIR/scripts/lib/engine.sh"; ob_engine_default rig')"
check "ENGINE_DEFAULT overrides the host-class default" '[[ "$_o" == vllm ]]' "$_o"
_o="$(_lib OPENBEAST_ENGINE_DEFAULT=nope bash -c 'source "$REPO_DIR/scripts/lib/engine.sh"; ob_engine_default spark' 2>&1)"
check "…and a bogus one warns and keeps the host-class default" '[[ "$_o" == *"names no engine adapter"*tensorfold ]]' "$_o"
printf '# keep me\nFOO=1\nINFERENCE_BACKEND=llama\nINFERENCE_BACKEND=vllm  # dup\nBAR=2\n' > "$T/c.conf"; chmod 600 "$T/c.conf"
_lib OB_ENGINE_CONF="$T/c.conf" OPENBEAST_ENGINE_STATE_DIR="$T/st0" bash -c 'source "$REPO_DIR/scripts/lib/engine.sh"; ob_engine_conf_set INFERENCE_BACKEND tensorfold; ob_engine_conf_unset BAR; ob_engine_conf_set NEW "x y"'
check "conf_set replaces every assignment once, in place; unset drops; comments stay" \
  '[[ "$(cat "$T/c.conf")" == $'"'"'# keep me\nFOO=1\nINFERENCE_BACKEND=tensorfold\nNEW=x y'"'"' ]]' "$(cat "$T/c.conf")"
check "…the conf keeps mode 600, and one backup was taken first" \
  '[[ "$(stat -c %a "$T/c.conf")" == 600 && $(ls "$T/st0/conf-backups" | wc -l) == 1 ]]'
# A value is one line: a literal backslash-n stays literal (awk -v used to
# expand it into a real newline), a real line break is refused, and so is a
# key that is not a name. Nothing else gets written either way.
printf 'BIND_HOST=127.0.0.1\n' > "$T/inj.conf"; chmod 600 "$T/inj.conf"
_o="$(_lib OB_ENGINE_CONF="$T/inj.conf" OPENBEAST_ENGINE_STATE_DIR="$T/st0" bash -c '
  source "$REPO_DIR/scripts/lib/engine.sh"
  ob_engine_conf_set ENGINE_PIN_MAYA "v9\nBIND_HOST=0.0.0.0@x"; echo "rc1=$?"
  ob_engine_conf_set ENGINE_PIN_MAYA "$(printf "v9\nBIND_HOST=0.0.0.0")"; echo "rc2=$?"
  ob_engine_conf_set "BAD KEY" x; echo "rc3=$?"' 2>&1)"
check "file_set: a literal \\n stays one line, a real line break or a bad key is refused" \
  '[[ "$_o" == *rc1=0*rc2=2*rc3=2* && "$(grep -c . "$T/inj.conf")" == 2 && "$(grep ^BIND_HOST= "$T/inj.conf")" == BIND_HOST=127.0.0.1 ]] && grep -qxF "ENGINE_PIN_MAYA=v9\nBIND_HOST=0.0.0.0@x" "$T/inj.conf"' "$_o / $(cat "$T/inj.conf")"
_o="$(_lib OB_ENGINE_CONF="$T/inj.conf" OPENBEAST_ENGINE_STATE_DIR="$T/st0" bash -c '
  source "$REPO_DIR/scripts/lib/engine.sh"
  ob_engine_pin_set vllm "v1/../x" sha256:0000000000000000000000000000000000000000000000000000000000000000 install; echo "rc1=$?"
  ob_engine_pin_set vllm v0.33.0 "sha256:00 BIND_HOST" install; echo "rc2=$?"
  ob_engine_pin_set vllm v0.33.0 sha256:0000000000000000000000000000000000000000000000000000000000000000 install; echo "rc3=$?"' 2>&1)"
check "pin_set refuses a version or ref that is not a pin's shape, and records a good one" \
  '[[ "$_o" == *rc1=2*rc2=2*rc3=0* && "$(grep -c ^ENGINE_PIN_VLLM= "$T/inj.conf")" == 1 ]]' "$_o"

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "conf.sh hook: managed means ONE host"
_conf() { printf '%s\n' "$1" > "$T/cc/openbeast.conf"; env -i "${BASE_ENV[@]}" REPO_DIR="$T/cc" bash -c 'source "$REPO_DIR/scripts/lib/conf.sh" 2>"$REPO_DIR/err"; echo "$INFERENCE_BACKEND|$INFERENCE_MANAGED|$INFERENCE_URL"'; }
mkdir -p "$T/cc"; cp -R "$REPO_DIR/scripts" "$T/cc/scripts"; cp "$REPO_DIR/openbeast.conf.example" "$T/cc/"
_o="$(_conf $'INFERENCE_BACKEND=vllm\nINFERENCE_URL=http://127.0.0.1:8080\nINFERENCE_MANAGED=true')"
check "vllm + a local INFERENCE_URL + MANAGED=true stays managed, silently" '[[ "$_o" == "vllm|true|http://127.0.0.1:8080" && ! -s "$T/cc/err" ]]' "$_o / $(cat "$T/cc/err")"
_o="$(_conf $'INFERENCE_BACKEND=tensorfold\nINFERENCE_URL=http://10.0.0.5:8000\nINFERENCE_MANAGED=true')"
check "…a remote URL is forced unmanaged with the old warning (the Sparks are untouched)" \
  '[[ "$_o" == "tensorfold|false|"* ]] && grep -q "INFERENCE_MANAGED=true is not supported" "$T/cc/err"' "$_o"
_o="$(_conf $'INFERENCE_BACKEND=vllm\nINFERENCE_URL=http://10.0.0.5:8000')"
check "vllm at a remote URL defaults to unmanaged (unchanged)" '[[ "$_o" == "vllm|false|http://10.0.0.5:8000" ]]' "$_o"
_o="$(_conf '')"
check "empty conf: llama, managed, :8080 on the probe host (unchanged)" '[[ "$_o" == "llama|true|http://$_LO:8080" ]]' "$_o"

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "engine.sh list / status / use (dry run and real)"
_o="$(eng -- list)"
check "list: host class + default, the three engines, llama active+default" \
  '[[ "$_o" == *"Host class: rig (x86_64) — OpenBeast default here: llama"* && "$_o" == *$'"'"'\nvllm '"'"'* && "$_o" == *$'"'"'\ntensorfold '"'"'* ]] && grep -E "^llama .*managed,default" <<< "$_o" >/dev/null' "$_o"
_o="$(eng -- status)"
check "status: names the engine, the pin, and says NOT ready when nothing answers" \
  '[[ "$_o" == *"engine:    llama"* && "$_o" == *"pin:       "*@* && "$_o" == *"NOT ready"* ]]' "$_o"
_o="$(eng -- use vllm --profile "$T/prof/rigvllm.env")"
check "use vllm refuses while vLLM is not installed (and says how)" '[[ "$_o" == *"engine.sh install vllm"* && ! -s "$SB/openbeast.conf" ]]' "$_o"
_o="$(eng -- install vllm --version v0.32.0)"
check "install vllm --version v0.32.0 pulls and pins tag@digest" \
  '[[ "$(confv ENGINE_PIN_VLLM)" =~ ^v0\.32\.0@sha256:[0-9a-f]{64}$ ]] && grep -q "docker pull vllm/vllm-openai:v0.32.0" "$DST/calls.log"' "$_o"
_sum="$(sha256sum "$SB/openbeast.conf")"
_o="$(eng -- use vllm --profile "$T/prof/rigvllm.env" --dry-run)"
check "use --dry-run prints the conf plan and the exact launch, changes nothing" \
  '[[ "$_o" == *"set   INFERENCE_BACKEND=vllm"* && "$_o" == *"set   INFERENCE_MANAGED=true"* && "$_o" == *"--served-model-name rig-model"* && "$_o" == *"--name openbeast-engine-vllm-"* && "$_o" == *"dry run"* && "$(sha256sum "$SB/openbeast.conf")" == "$_sum" ]]' "$_o"
check "…the launch mounts the verified checkpoint read-only and keeps CUDA graphs on a rig" \
  '[[ "$_o" == *"$T/ckpt:/models/rigvllm:ro"* && "$_o" != *enforce-eager* && "$_o" == *"--tool-call-parser qwen3_xml"* ]]' "$_o"
_o="$(eng -- use vllm --profile "$T/prof/tp2.env")"
check "a TP 2 profile is refused for managed mode (that is spark-cluster.sh)" '[[ "$_o" == *"TENSOR_PARALLEL_SIZE=2"*"one host"* ]]' "$_o"

# A "running stack": a recorded supervisor whose cmdline says start.sh.
printf '#!/bin/bash\nsleep 60\n' > "$T/start.sh"; bash "$T/start.sh" & _SUP=$!; _PIDS="$_PIDS $_SUP"
echo "$_SUP" > "$SB/.run/supervisor.pid"
printf '#!/bin/bash\necho "STOP backend=$(grep ^INFERENCE_BACKEND= %s/openbeast.conf || echo none)" >> %s/order.log\n' "$SB" "$T" > "$T/bin/stopstub"
printf '#!/bin/bash\necho "START $* backend=$(grep ^INFERENCE_BACKEND= %s/openbeast.conf)" >> %s/order.log\n' "$SB" "$T" > "$T/bin/startstub"
chmod +x "$T/bin/stopstub" "$T/bin/startstub"
STUBS=(OB_ENGINE_STOP_CMD="$T/bin/stopstub" OB_ENGINE_START_CMD="$T/bin/startstub -d")
_o="$(eng "${STUBS[@]}" -- use vllm --profile "$T/prof/rigvllm.env")"
check "use vllm on a running stack: stop (old conf) → write conf → start (new conf)" \
  '[[ "$(cat "$T/order.log")" == $'"'"'STOP backend=none\nSTART -d backend=INFERENCE_BACKEND=vllm'"'"' ]]' "$(cat "$T/order.log" 2>/dev/null) / $_o"
check "…conf: vllm, managed, local URL, profile, served id, slots from MAX_NUM_SEQS" \
  '[[ "$(confv INFERENCE_BACKEND)|$(confv INFERENCE_MANAGED)|$(confv INFERENCE_URL)|$(confv ENGINE_PROFILE)|$(confv INFERENCE_MODEL)|$(confv INFERENCE_SLOTS)" == "vllm|true|http://127.0.0.1:8080|$T/prof/rigvllm.env|\"rig-model\"|4" ]]' "$(cat "$SB/openbeast.conf")"
check "…a conf backup exists and the history says use" \
  '[[ -n "$(ls "$SB/.engines/conf-backups")" ]] && grep -qP "\tvllm\tuse\tmanaged\t" "$SB/.engines/history.tsv"'
: > "$T/order.log"
_o="$(eng "${STUBS[@]}" -- use llama)"
_empty="$(_conf '')"
cp "$SB/openbeast.conf" "$T/cc/openbeast.conf"
_back="$(env -i "${BASE_ENV[@]}" REPO_DIR="$T/cc" bash -c 'source "$REPO_DIR/scripts/lib/conf.sh" 2>/dev/null; echo "$INFERENCE_BACKEND|$INFERENCE_MANAGED|$INFERENCE_URL"')"
check "use llama restores the default rig: conf.sh resolves exactly what an empty conf does" \
  '[[ "$_back" == "$_empty" ]] && ! grep -qE "^(INFERENCE_URL|INFERENCE_MANAGED|ENGINE_PROFILE|INFERENCE_MODEL|INFERENCE_SLOTS)=" "$SB/openbeast.conf"' "$_back vs $_empty: $(cat "$SB/openbeast.conf")"
kill "$_SUP" 2>/dev/null || true; rm -f "$SB/.run/supervisor.pid"
_o="$(eng -- use vllm --url http://10.0.0.5:8000 --profile "$T/prof/tp2.env")"
check "use vllm --url <a Spark>: unmanaged, no install needed, TP 2 fine, served id recorded" \
  '[[ "$(confv INFERENCE_MANAGED)|$(confv INFERENCE_URL)|$(confv INFERENCE_MODEL)" == "false|http://10.0.0.5:8000|\"two\"" ]] && ! grep -q "^ENGINE_PROFILE=" "$SB/openbeast.conf"' "$_o"
eng -- use llama >/dev/null

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "vLLM install / update / rollback / stage → offline install"
_o="$(eng -- update vllm)"
check "update vllm resolves the latest RELEASE name (v0.33.0, not its tag v0.33.0rc3) and pins it" \
  '[[ "$(confv ENGINE_PIN_VLLM)" == v0.33.0@sha256:* ]]' "$_o"
_o="$(eng -- update vllm)"
check "…a second update is a no-op ('already pinned to the latest')" '[[ "$_o" == *"already pinned to the latest release (v0.33.0)"* ]]' "$_o"
_n0="$(grep -c "docker pull" "$DST/calls.log")"
_o="$(eng -- rollback vllm)"
check "rollback vllm → v0.32.0 from the local image, no pull" \
  '[[ "$(confv ENGINE_PIN_VLLM)" == v0.32.0@* && "$(grep -c "docker pull" "$DST/calls.log")" == "$_n0" ]]' "$_o"
_o="$(eng -- rollback vllm)"
check "…rollback again goes forward to v0.33.0 (toggle between the last two pins)" '[[ "$(confv ENGINE_PIN_VLLM)" == v0.33.0@* ]]' "$_o"
_pin="$(confv ENGINE_PIN_VLLM)"
_o="$(eng -- install vllm --version v0.33.0 --digest sha256:0000000000000000000000000000000000000000000000000000000000000000)"
check "install --digest that the registry does not serve: refused, pin unchanged" \
  '[[ "$_o" == *"failed"* && "$(confv ENGINE_PIN_VLLM)" == "$_pin" ]]' "$_o"
_o="$(eng -- stage vllm --version v0.33.0 "$T/stage-vllm")"
check "stage vllm writes a tarball + MANIFEST (digest, image id, sha256)" \
  '[[ -f "$T/stage-vllm/MANIFEST" ]] && grep -q "^TAR_SHA256=[0-9a-f]\{64\}$" "$T/stage-vllm/MANIFEST"' "$_o"
DST="$T/dstate2"; mkdir -p "$DST"   # a fresh, closed box: no images at all
sed -i '/^ENGINE_PIN_VLLM=/d' "$SB/openbeast.conf"
_o="$(eng OPENBEAST_OFFLINE=true DOCKER_STUB_FAIL_PULL=1 -- install vllm --from "$T/stage-vllm")"
check "OFFLINE install --from DIR: sha256 checked, docker load, image id verified, pinned" \
  '[[ "$_o" == *"verified:"* && "$(confv ENGINE_PIN_VLLM)" == v0.33.0@sha256:* ]] && ! grep -q "docker pull" "$DST/calls.log"' "$_o"
_o="$(eng OPENBEAST_OFFLINE=true -- update vllm)"
check "OFFLINE update without --from says why and changes nothing" '[[ "$_o" == *"OFFLINE=true"* && "$(confv ENGINE_PIN_VLLM)" == v0.33.0@* ]]' "$_o"
cp -R "$T/stage-vllm" "$T/stage-bad"; echo tamper >> "$T/stage-bad/$(grep ^TAR= "$T/stage-bad/MANIFEST" | cut -d= -f2)"
_o="$(eng OPENBEAST_OFFLINE=true -- install vllm --from "$T/stage-bad")"
check "…a tampered staged tarball is refused" '[[ "$_o" == *"checksum mismatch"* ]]' "$_o"
_sid="$(grep ^IMAGE_ID= "$T/stage-vllm/MANIFEST" | cut -d= -f2)"; _sdig="$(grep ^DIGEST= "$T/stage-vllm/MANIFEST" | cut -d= -f2)"
check "…the offline pin is the image id this box verified, not the MANIFEST's registry digest" \
  '[[ "$(confv ENGINE_PIN_VLLM)" == "v0.33.0@$_sid" && "$_sid" != "$_sdig" ]] && grep -qF "staged-digest=$_sdig (unverified)" "$SB/.engines/history.tsv"' "$(confv ENGINE_PIN_VLLM) / $_sdig"
printf 'VLLM_IMAGE=old\nVLLM_IMAGE_DIGEST=sha256:%064d\n' 0 > "$T/spark-vllm.env"
_o="$(eng OPENBEAST_OFFLINE=true -- install vllm --from "$T/stage-vllm" --spark-env "$T/spark-vllm.env")"
check "…and spark.env gets it as VLLM_IMAGE_ID (which spark-node.sh prefers), the old digest cleared" \
  'grep -qx "VLLM_IMAGE_ID=$_sid" "$T/spark-vllm.env" && grep -qx "VLLM_IMAGE_DIGEST=" "$T/spark-vllm.env"' "$(cat "$T/spark-vllm.env") / $_o"
_pin="$(confv ENGINE_PIN_VLLM)"
_bad=""
for _kv in 'TAR=../../etc/passwd' 'DIGEST=sha256:0\\nBIND_HOST=0.0.0.0' 'IMAGE_ID=latest' 'VERSION=v0.33.0\\nX=1' 'TAR_SHA256=abc'; do
  rm -rf "$T/stage-bad"; cp -R "$T/stage-vllm" "$T/stage-bad"
  sed -i "s|^${_kv%%=*}=.*|$_kv|" "$T/stage-bad/MANIFEST"
  _o="$(eng OPENBEAST_OFFLINE=true -- install vllm --from "$T/stage-bad")"
  [[ "$_o" == *"failed"* && "$(confv ENGINE_PIN_VLLM)" == "$_pin" ]] || _bad+=" ${_kv%%=*}"
done
check "…a MANIFEST field of the wrong shape (path, line break, not a sha256) is refused, the pin unchanged" \
  '[[ -z "$_bad" ]] && ! grep -q "^BIND_HOST=" "$SB/openbeast.conf"' "accepted:$_bad"
DST="$T/dstate3"; mkdir -p "$DST"
_o="$(eng OPENBEAST_OFFLINE=true DOCKER_STUB_LOAD_DROP_DIGEST=1 -- install vllm --from "$T/stage-vllm")"
_o2="$(eng -- list)"
check "an image whose registry digest did not survive save/load is found by its image id" \
  '[[ "$_o2" == *$'"'"'\nvllm        v0.33.0'"'"'* ]]' "$_o / $_o2"

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "Managed vLLM on a stub rig: start.sh launches it, its trap and stop.sh stop it"
DST="$T/dstate3"
VPORT="$(_free_port)"
_vpin="$(confv ENGINE_PIN_VLLM)"
cat > "$SB/openbeast.conf" <<EOF
INFERENCE_BACKEND=vllm
INFERENCE_URL=http://$_LO:$VPORT
INFERENCE_MANAGED=true
ENGINE_PROFILE=$T/prof/rigvllm.env
ENGINE_PIN_VLLM=$_vpin
EOF
: > "$DST/calls.log"; : > "$T/calls.log"
_o="$(env -i "${BASE_ENV[@]}" DOCKER_STUB_STATE="$DST" OPENBEAST_SERVE_SCRIPT=serve-marker.sh OPENBEAST_LLAMA_LOAD_GRACE=20 \
        timeout 60 bash "$SB/start.sh" 2>&1 || true)"
check "start.sh: 'Starting vLLM (engine-serve.sh)', then 'vLLM ready on <INFERENCE_URL>'" \
  '[[ "$_o" == *"Starting vLLM (engine-serve.sh)"* && "$_o" == *"vLLM ready on http://$_LO:$VPORT"* ]]' "$(tail -n 12 <<< "$_o")"
check "…ran ONE container named for this checkout, on the configured host:port" \
  'grep -E "docker run --rm --name openbeast-engine-vllm-[0-9a-f]{8} .*--host $_LO --port $VPORT" "$DST/calls.log" >/dev/null' "$(cat "$DST/calls.log")"
check "…and the supervisor trap stopped it by name on the way out" \
  'grep -E "docker stop -t 30 openbeast-engine-vllm-" "$DST/calls.log" >/dev/null && ! ls "$DST"/run-*.pid >/dev/null 2>&1'
check "…no llama serve script, no pkill/pgrep of llama-server" \
  '! grep -qE "serve-marker|^(pkill|pgrep).*llama-server" "$T/calls.log"' "$(cat "$T/calls.log")"
# stop.sh: a container left behind (SIGKILLed supervisor) is stopped by name.
_cn="$(grep -oE 'openbeast-engine-vllm-[0-9a-f]{8}' "$DST/calls.log" | head -n1)"
( exec -a fakecontainer sleep 60 ) & _fc=$!; _PIDS="$_PIDS $_fc"; echo "$_fc" > "$DST/run-$_cn.pid"
_o="$(env -i "${BASE_ENV[@]}" DOCKER_STUB_STATE="$DST" timeout 60 bash "$SB/stop.sh" 2>&1 || true)"
check "stop.sh stops the managed vLLM container of this checkout" \
  '[[ "$_o" == *"Stopping vLLM (managed by this stack)"* && "$_o" == *"vLLM container $_cn stopped"* ]] && ! kill -0 $_fc 2>/dev/null' "$_o"
check "…and never reaches for llama-server" '! grep -qE "^(pkill|pgrep).*llama-server" "$T/calls.log"' "$(cat "$T/calls.log")"
rm -f "$SB/.run/stopped"   # stop.sh marked it; --restart reports only while it is there
_o="$(env -i "${BASE_ENV[@]}" DOCKER_STUB_STATE="$DST" timeout 60 bash "$SB/scripts/healthcheck.sh" --restart 2>&1 || true)"
check "healthcheck --restart on a DOWN managed vLLM: reports it, relaunches nothing" \
  '[[ "$_o" == *"DOWN vLLM (vllm @ http://$_LO:$VPORT, managed)"* && "$_o" == *"not restarted here"* ]] && ! grep -q "docker run" <(tail -n 3 "$DST/calls.log") && ! grep -q serve-marker "$T/calls.log"' "$_o"

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "llama.cpp: install / update / rollback / stage, pinned by commit"
git init -q "$T/up"
( cd "$T/up" && echo 1 > f && git add f && git commit -qm c1 && git tag b100 \
  && echo 2 > f && git commit -qam c2 && git tag b101 && git tag -a v1.0.0 -m "v1.0.0" \
  && echo 3 > f && git commit -qam c3 && git tag b102 )
C1="$(git -C "$T/up" rev-parse b100)"; C2="$(git -C "$T/up" rev-parse b101)"; C3="$(git -C "$T/up" rev-parse b102)"
git -C "$T/up" config uploadpack.allowAnySHA1InWant true
git clone -q "$T/up" "$SB/llama.cpp"; git -C "$SB/llama.cpp" checkout -q --detach "$C1"
mkdir -p "$SB/llama.cpp/build/bin"; printf '#!/bin/bash\n' > "$SB/llama.cpp/build/bin/llama-server"; chmod +x "$SB/llama.cpp/build/bin/llama-server"
printf 'LLAMA_CPP_REF=%s\n' "$C1" > "$SB/scripts/llama.cpp.ref"
printf '#!/bin/bash\necho "BUILD at $(git -C %s/llama.cpp rev-parse HEAD)" >> %s/build.log\n[[ -z "${FAKE_BUILD_FAIL:-}" ]]\n' "$SB" "$T" > "$T/bin/fake-build"; chmod +x "$T/bin/fake-build"
pin_of() { sed -nE 's/^LLAMA_CPP_REF=([0-9a-f]{40})$/\1/p' "$SB/scripts/llama.cpp.ref"; }
eng -- install llama --version b100 >/dev/null   # adopt the current one into the history
_o="$(eng -- update llama)"
check "update llama → the latest STABLE release (annotated v1.0.0, peeled to its commit), built, pinned" \
  '[[ "$(pin_of)" == "$C2" && "$(git -C "$SB/llama.cpp" rev-parse HEAD)" == "$C2" ]] && grep -q "BUILD at $C2" "$T/build.log"' "$_o"
_o="$(eng -- list)"
check "…and list names it by its release tag" 'grep -E "^llama +v1\.0\.0 \(" <<< "$_o" >/dev/null' "$_o"
_o="$(eng FAKE_BUILD_FAIL=1 -- install llama --version latest-build)"
check "a failed build: llama.cpp/ back at the old commit, the pin does not move" \
  '[[ "$(pin_of)" == "$C2" && "$(git -C "$SB/llama.cpp" rev-parse HEAD)" == "$C2" && "$_o" == *FAILED* ]]' "$_o"
_o="$(eng -- rollback llama)"
check "rollback llama → b100's commit (objects local: no fetch needed)" \
  '[[ "$(pin_of)" == "$C1" && "$(git -C "$SB/llama.cpp" rev-parse HEAD)" == "$C1" ]]' "$_o"
_o="$(eng -- stage llama --version b102 "$T/stage-llama")"
check "stage llama b102 → a one-commit bare repo + MANIFEST" \
  '[[ -d "$T/stage-llama/llama.cpp.git" ]] && grep -q "^COMMIT=$C3$" "$T/stage-llama/MANIFEST"' "$_o"
_o="$(eng OPENBEAST_OFFLINE=true OB_ENGINE_LLAMA_URL=file:///nonexistent -- install llama --from "$T/stage-llama")"
check "OFFLINE install llama --from DIR: fetched from the folder, verified commit, built, pinned" \
  '[[ "$(pin_of)" == "$C3" && "$(git -C "$SB/llama.cpp" rev-parse HEAD)" == "$C3" ]]' "$_o"
sed -i "s/^COMMIT=.*/COMMIT=$C2/" "$T/stage-llama/MANIFEST"
_o="$(eng OPENBEAST_OFFLINE=true -- install llama --version "$C3" --from "$T/stage-llama")"
check "…a MANIFEST naming another commit than asked is refused" '[[ "$_o" == *"refusing"* ]]' "$_o"
_o="$(eng -- history llama)"
check "history lists every llama install/update/rollback" '[[ $(grep -cE " llama +(install|update|rollback) " <<< "$_o") -ge 4 ]]' "$_o"

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "TensorFold: signed native releases through the real install-native.sh"
if ! openssl version 2>/dev/null | grep -qE '^OpenSSL [3-9]'; then
  echo "  SKIP: needs OpenSSL >= 3 (ed25519)"
else
  mkdir -p "$T/tfinst" "$T/tfrel" "$T/tfkey"
  cp "$REPO_DIR/scripts/backends/tensorfold/install-native.sh" "$T/tfinst/"
  openssl genpkey -algorithm ed25519 -out "$T/tfkey/priv.pem" 2>/dev/null
  openssl pkey -in "$T/tfkey/priv.pem" -pubout -out "$T/tfinst/release-key.pem" 2>/dev/null
  mkrel() { # mkrel <version>
    local v="$1" p=linux-x86_64 d="$T/tfbuild/tensorfold-$1-linux-x86_64" a
    mkdir -p "$d/bin" "$T/tfrel/v$v"
    printf '#!/bin/bash\nif [[ "$1" == --version ]]; then echo "tensorfold %s"; exit 0; fi\necho "tensorfold-native $*" >> %s/calls.log\nh=127.0.0.1 p=8000\nwhile [[ $# -gt 0 ]]; do case "$1" in --host) h=$2; shift;; --port) p=$2; shift;; esac; shift; done\nexec %s %s "$h" "$p" tf\n' \
      "$v" "$T" "$PY" "$T/httpstub.py" > "$d/bin/tensorfold-native"
    chmod +x "$d/bin/tensorfold-native"
    (cd "$d" && sha256sum bin/tensorfold-native > SHA256SUMS)
    a="tensorfold-$v-$p.tar.gz"
    tar -czf "$T/tfrel/v$v/$a" -C "$T/tfbuild" "tensorfold-$v-$p"
    (cd "$T/tfrel/v$v" && sha256sum "$a" > "$a.sha256")
    { printf 'tensorfold-native-checksum-v1\n'; cat "$T/tfrel/v$v/$a.sha256"; } > "$T/tfbuild/signed"
    openssl pkeyutl -sign -inkey "$T/tfkey/priv.pem" -rawin -in "$T/tfbuild/signed" -out "$T/tfrel/v$v/$a.sha256.sig"
  }
  mkrel 1.2.8; mkrel 1.2.9; mkrel 1.3.0
  _o="$(eng -- install tensorfold --version 1.2.8)"
  check "install tensorfold 1.2.8: signature + sha256 verified, installed, pinned to the archive sha256" \
    '[[ "$_o" == *"verified: tensorfold-1.2.8-linux-x86_64.tar.gz"* && "$(confv ENGINE_PIN_TENSORFOLD)" =~ ^1\.2\.8@sha256:[0-9a-f]{64}$ && "$("$T/home/.local/bin/tensorfold" --version)" == "tensorfold 1.2.8" ]]' "$_o"
  _o="$(eng -- update tensorfold)"
  check "update tensorfold → 1.2.9 (GitHub latest), side by side with 1.2.8" \
    '[[ "$(confv ENGINE_PIN_TENSORFOLD)" == 1.2.9@* && -d "$T/home/.local/opt/tensorfold-1.2.8-linux-x86_64" && "$("$T/home/.local/bin/tensorfold" --version)" == "tensorfold 1.2.9" ]]' "$_o"
  _n="$(grep -c curl-github "$T/calls.log")"
  _o="$(eng OPENBEAST_OFFLINE=true -- rollback tensorfold)"
  check "rollback tensorfold → 1.2.8 by relinking (SHA256SUMS re-checked), offline, no download" \
    '[[ "$(confv ENGINE_PIN_TENSORFOLD)" == 1.2.8@* && "$("$T/home/.local/bin/tensorfold" --version)" == "tensorfold 1.2.8" && "$(grep -c curl-github "$T/calls.log")" == "$_n" ]]' "$_o"
  printf 'x' >> "$T/tfrel/v1.3.0/tensorfold-1.3.0-linux-x86_64.tar.gz.sha256.sig"
  _o="$(eng -- install tensorfold --version 1.3.0)"
  check "a bad release signature is refused; the pin and the binary stay" \
    '[[ "$_o" == *"does NOT verify"* && "$(confv ENGINE_PIN_TENSORFOLD)" == 1.2.8@* && "$("$T/home/.local/bin/tensorfold" --version)" == "tensorfold 1.2.8" ]]' "$_o"
  _o="$(eng -- stage tensorfold --version 1.2.9 "$T/stage-tf")"
  rm -rf "$T/home/.local/opt/tensorfold-1.2.9-linux-x86_64" "$SB/.engines/cache"
  _n="$(grep -c curl-github "$T/calls.log")"
  cp "$REPO_DIR/scripts/backends/spark.env.example" "$SB/scripts/backends/spark.env"
  _o="$(eng OPENBEAST_OFFLINE=true OB_ENGINE_HOST_CLASS=spark -- install tensorfold --from "$T/stage-tf")"
  check "OFFLINE install tensorfold --from a staged folder (re-verified), no download" \
    '[[ "$(confv ENGINE_PIN_TENSORFOLD)" == 1.2.9@* && "$(grep -c curl-github "$T/calls.log")" == "$_n" ]]' "$_o"
  check "…on a Spark, spark.env TENSORFOLD_VERSION follows the pin (the cluster launchers read it)" \
    'grep -qx "TENSORFOLD_VERSION=1.2.9" "$SB/scripts/backends/spark.env"' "$(grep TENSORFOLD_VERSION "$SB/scripts/backends/spark.env")"
  rm -f "$SB/scripts/backends/spark.env"

  echo ""
  echo "Managed TensorFold on a stub rig"
  TPORT="$(_free_port)"
  cat > "$SB/openbeast.conf" <<EOF
INFERENCE_BACKEND=tensorfold
INFERENCE_URL=http://$_LO:$TPORT
INFERENCE_MANAGED=true
ENGINE_PROFILE=$T/prof/rigtf.env
EOF
  : > "$T/calls.log"
  _o="$(env -i "${BASE_ENV[@]}" DOCKER_STUB_STATE="$DST" OPENBEAST_SERVE_SCRIPT=serve-marker.sh OPENBEAST_LLAMA_LOAD_GRACE=20 \
          timeout 60 bash "$SB/start.sh" 2>&1 || true)"
  check "start.sh launches the native binary in the foreground and waits for {\"ok\": true}" \
    '[[ "$_o" == *"Starting TensorFold (engine-serve.sh)"* && "$_o" == *"TensorFold ready on http://$_LO:$TPORT"* ]]' "$(tail -n 12 <<< "$_o")"
  check "…serve args: verified dir, --no-update-check, --drafter none, --parallel from the profile" \
    'grep -E "tensorfold-native serve $T/ckpt-tf --no-update-check --name rig-model --host $_LO --port $TPORT --drafter none --parallel 2" "$T/calls.log" >/dev/null' "$(cat "$T/calls.log")"
  check "…and nothing of llama's ran" '! grep -qE "serve-marker|^(pkill|pgrep).*llama-server" "$T/calls.log"'
fi

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "Project Maya: reviewed commits, llama.cpp by commit, hash-locked packages, the gate"
# Maya "upstream": a setup.py with the two facts the adapter vets. Its
# llama.cpp: a repo with the ggml/ and gguf-py/ Maya builds against.
_PYLINE="$(sed -n 's/^_ENG_MAYA_PYPKG_LINE=.\(.*\).$/\1/p' "$REPO_DIR/scripts/engines/maya.sh")"
git init -q "$T/mllama"
( cd "$T/mllama" && mkdir -p ggml gguf-py && echo 'project(ggml)' > ggml/CMakeLists.txt && echo x > gguf-py/x \
  && git add -A && git commit -qm l1 )
ML="$(git -C "$T/mllama" rev-parse HEAD)"; git -C "$T/mllama" config uploadpack.allowAnySHA1InWant true
git init -q "$T/maya-up"
_mk_maya() { # _mk_maya <tag> <py-packages line>
  ( cd "$T/maya-up" && printf 'LLAMA_CPP_COMMIT = "%s"\n%s\n' "$ML" "$2" > setup.py \
    && echo 'print("maya")' > maya.py && git add -A && git commit -qm "$1" && git tag "$1" )
}
_mk_maya v9.9.9 "$_PYLINE"
MC1="$(git -C "$T/maya-up" rev-parse v9.9.9)"
_mk_maya v9.9.10 'PY_PACKAGES = ["numpy", "something-new"]'
MC2="$(git -C "$T/maya-up" rev-parse v9.9.10)"
git -C "$T/maya-up" config uploadpack.allowAnySHA1InWant true
printf '#!/bin/bash\nmkdir -p "$1/bin"\n' > "$T/bin/fake-venv"
printf '#!/bin/bash\necho "pip $*" >> %s/pip.log\nif [[ "$1" == download ]]; then while [[ $# -gt 0 ]]; do [[ "$1" == -d ]] && { mkdir -p "$2"; : > "$2/numpy-2.5.4-x.whl"; }; shift; done; fi\n' "$T" > "$T/bin/fake-pip"
chmod +x "$T/bin/fake-venv" "$T/bin/fake-pip"
MENV=(OB_ENGINE_MAYA_URL="file://$T/maya-up" OB_ENGINE_LLAMA_URL="file://$T/mllama" OB_ENGINE_MAYA_OPT="$T/opt"
      OB_ENGINE_MAYA_MKVENV="$T/bin/fake-venv" OB_ENGINE_MAYA_PIP="$T/bin/fake-pip"
      OB_ENGINE_MAYA_REVIEWED="$(printf 'v9.9.9\t%s\t%s\nv9.9.10\t%s\t%s' "$MC1" "$ML" "$MC2" "$ML")")
: > "$SB/openbeast.conf"; : > "$T/pip.log"
_o="$(eng "${MENV[@]}" -- install maya --version v1.2.3)"
check "an unreviewed Maya version is refused, and nothing is pinned" \
  '[[ "$_o" == *"not a reviewed version"* && -z "$(confv ENGINE_PIN_MAYA)" && ! -e "$T/opt/maya" ]]' "$_o"
_o="$(eng "${MENV[@]}" -- install maya --version v9.9.9)"
check "install maya v9.9.9: the reviewed commit, pinned tag@commit, linked active" \
  '[[ "$(confv ENGINE_PIN_MAYA)" == "v9.9.9@$MC1" && "$(git -C "$T/opt/maya" rev-parse HEAD)" == "$MC1" ]]' "$_o"
check "…llama.cpp checked out at the commit its setup.py pins (no archive zip)" \
  '[[ "$(git -C "$T/opt/maya-v9.9.9/third_party/llama.cpp" rev-parse HEAD)" == "$ML" && -f "$T/opt/maya-v9.9.9/third_party/llama.cpp/ggml/CMakeLists.txt" ]]'
check "…packages through pip --require-hashes from the committed lock, and Maya's stamp written" \
  'grep -q -- "install --require-hashes --no-deps --only-binary :all:.*-r $SB/scripts/engines/maya/requirements.lock" "$T/pip.log" && grep -qF "\"jsonschema>=4.18,<5\"]" "$T/opt/maya-v9.9.9/.venv/.strata-pip.json"' "$(cat "$T/pip.log")"
_o="$(eng "${MENV[@]}" -- list)"
check "…and list shows it installed and pinned" 'grep -E "^maya +v9\.9\.9 \(" <<< "$_o" >/dev/null' "$_o"
_o="$(eng "${MENV[@]}" -- install maya --version v9.9.10)"
check "a Maya whose setup.py changed its package list is refused; the pin stays" \
  '[[ "$_o" == *"changed its Python package list"* && "$(confv ENGINE_PIN_MAYA)" == "v9.9.9@$MC1" && ! -d "$T/opt/maya-v9.9.10" ]]' "$_o"
_o="$(eng "${MENV[@]}" -- install maya --version v9.9.9 --digest "$MC2")"
check "…and a --digest that contradicts the reviewed commit is refused" '[[ "$_o" == *"names another commit"* ]]' "$_o"
_o="$(eng "${MENV[@]}" -- stage maya --version v9.9.9 "$T/stage-maya")"
check "stage maya: both commits as bare repos, the locked wheels, a MANIFEST" \
  '[[ -d "$T/stage-maya/maya.git" && -d "$T/stage-maya/llama.cpp.git" && -f "$T/stage-maya/wheels/numpy-2.5.4-x.whl" ]] && grep -q "^COMMIT=$MC1$" "$T/stage-maya/MANIFEST" && grep -q "^LLAMA_COMMIT=$ML$" "$T/stage-maya/MANIFEST" && grep -q "pip download --require-hashes" "$T/pip.log"' "$_o"
: > "$T/pip.log"
_o="$(eng "${MENV[@]}" ENG_STAGE_PLATFORM=manylinux_2_28_x86_64 MAYA_STAGE_PYTHON=3.12 -- stage maya --version v9.9.9 "$T/stage-maya-rig")"
check "stage maya with the runbook's env form (ENG_STAGE_PLATFORM) downloads the rig's wheels, not this box's" \
  'grep -q -- "pip download .*--platform manylinux_2_28_x86_64 --python-version 3.12" "$T/pip.log"' "$(cat "$T/pip.log") / $_o"
: > "$T/pip.log"
_o="$(eng "${MENV[@]}" ENG_STAGE_PLATFORM=manylinux_2_28_x86_64 -- stage maya --version v9.9.9 --platform manylinux_2_28_aarch64 "$T/stage-maya-rig")"
check "…and --platform still wins over the env" 'grep -q -- "--platform manylinux_2_28_aarch64" "$T/pip.log"' "$(cat "$T/pip.log")"
rm -rf "$T/opt"; : > "$T/pip.log"
_o="$(eng "${MENV[@]}" OPENBEAST_OFFLINE=true OB_ENGINE_MAYA_URL=file:///nonexistent OB_ENGINE_LLAMA_URL=file:///nonexistent -- install maya --from "$T/stage-maya")"
check "OFFLINE install maya --from DIR: verified commits from the folder, wheels with --no-index" \
  '[[ "$(git -C "$T/opt/maya" rev-parse HEAD)" == "$MC1" ]] && grep -q -- "--no-index --find-links $T/stage-maya/wheels" "$T/pip.log"' "$_o"
_o="$(eng "${MENV[@]}" OPENBEAST_OFFLINE=true -- install maya --version v9.9.9)"
check "…and OFFLINE without --from refuses to download" '[[ "$_o" == *"OFFLINE=true — no download"* ]]' "$_o"
# A staged folder is data, not a reviewer: its MANIFEST cannot name an
# unreviewed commit, a path, or extra conf lines.
_mpin="$(confv ENGINE_PIN_MAYA)"
_evil() { # _evil <VERSION value> → $T/stage-evil, a copy of the good stage
  rm -rf "$T/stage-evil"; cp -R "$T/stage-maya" "$T/stage-evil"
  sed -i "s|^VERSION=.*|VERSION=$1|; s|^COMMIT=.*|COMMIT=$MC2|" "$T/stage-evil/MANIFEST"
}
_evil v9.9.11
_o="$(eng "${MENV[@]}" OPENBEAST_OFFLINE=true -- install maya --from "$T/stage-evil")"
check "install --from a MANIFEST naming an unreviewed version + commit is refused (the folder is not --digest)" \
  '[[ "$_o" == *"not a reviewed version"* && "$_o" != *"because you named it"* && "$(confv ENGINE_PIN_MAYA)" == "$_mpin" && ! -e "$T/opt/maya-v9.9.11" ]]' "$_o"
_evil ../../victim
_o="$(eng "${MENV[@]}" OPENBEAST_OFFLINE=true -- install maya --from "$T/stage-evil" --digest "$MC2")"
check "…a VERSION with ../ in it is refused before any path is built, even with --digest" \
  '[[ "$_o" == *"not a Maya version"* && ! -e "$T/victim" && ! -e "$T/opt/victim" && ! -e "$T/opt/maya-.." && "$(confv ENGINE_PIN_MAYA)" == "$_mpin" ]]' "$_o / $(ls "$T" "$T/opt")"
_evil 'v9\\nBIND_HOST=0.0.0.0\\nX='
_o="$(eng "${MENV[@]}" OPENBEAST_OFFLINE=true -- install maya --from "$T/stage-evil" --digest "$MC2")"
check "…and a VERSION carrying \\n KEY= lines never reaches openbeast.conf" \
  '[[ "$_o" == *"not a Maya version"* ]] && ! grep -q "^BIND_HOST=" "$SB/openbeast.conf" && [[ "$(confv ENGINE_PIN_MAYA)" == "$_mpin" ]]' "$_o / $(cat "$SB/openbeast.conf")"
# The gate: no verdict, no use. The profile ships in scripts/engines/maya/profiles/.
printf 'LLAMA_API_KEY=sekrit-maya\n' > "$SB/openbeast.conf"; chmod 600 "$SB/openbeast.conf"
_o="$(eng "${MENV[@]}" -- use maya --profile glm53-flash-abl-q2kxl --force)"
check "use maya without a gate verdict is refused (even with --force), the conf untouched" \
  '[[ "$_o" == *"maya is gated: no measurement on file"* && "$(cat "$SB/openbeast.conf")" == "LLAMA_API_KEY=sekrit-maya" ]]' "$_o"
mkdir -p "$SB/.engines/gate"
_GG="$(sed -n 's/^GGUF=//p' "$SB/scripts/engines/maya/profiles/glm53-flash-abl-q2kxl.env")"
_verdict() { # _verdict <profile> <commit> [host] — a passing verdict bound to them
  "$PY" - "$SB/.engines/gate/maya.json" "$1" "$_GG" "$2" "${3:-}" <<'PY'
import json, platform, sys
out, prof, gguf, commit, host = sys.argv[1:6]
json.dump({"schema": "openbeast-engine-gate/1", "engine": "maya", "pass": True, "ratio": 1.62,
           "candidate_tps": 32.4, "baseline_tps": 20.0, "time": "t", "host": host or platform.node(),
           "bound": {"profile": prof, "gguf": gguf, "commit": commit}}, open(out, "w"))
PY
}
_verdict glm53-flash-abl-q2kxl "$MC1"
cp "$SB/scripts/engines/maya/profiles/glm53-flash-abl-q2kxl.env" "$T/prof/glm-other.env"
_o="$(eng "${MENV[@]}" -- use maya --profile "$T/prof/glm-other.env" --force --dry-run)"
check "a PASS for one profile does not admit another (the verdict is bound to what it measured)" \
  '[[ "$_o" == *"measured profile glm53-flash-abl-q2kxl, this would run glm-other"* && "$_o" != *"set   INFERENCE_BACKEND=maya"* ]]' "$_o"
_verdict glm53-flash-abl-q2kxl "$MC2"
_o="$(eng "${MENV[@]}" -- use maya --profile glm53-flash-abl-q2kxl --force --dry-run)"
check "…nor another Maya commit (an update, a --digest, a rollback is unmeasured)" \
  '[[ "$_o" == *"measured commit $MC2, this would run $MC1"* && "$_o" != *"set   INFERENCE_BACKEND=maya"* ]]' "$_o"
_verdict glm53-flash-abl-q2kxl "$MC1"
printf 'LLAMA_API_KEY=sekrit-maya\nENGINE_GATE_RATIO=2\n' > "$SB/openbeast.conf"
_o="$(eng "${MENV[@]}" -- use maya --profile glm53-flash-abl-q2kxl --force --dry-run)"
check "…and ENGINE_GATE_RATIO in openbeast.conf raises the bar for use (1.62x < 2x)" \
  '[[ "$_o" == *"needs 2.0x"* && "$_o" != *"unknown key"* && "$_o" != *"set   INFERENCE_BACKEND=maya"* ]]' "$_o"
printf 'LLAMA_API_KEY=sekrit-maya\n' > "$SB/openbeast.conf"
_o="$(eng "${MENV[@]}" -- use maya --profile glm53-flash-abl-q2kxl --force --dry-run)"
check "…with a passing verdict: the plan sets maya managed and prints the launch" \
  '[[ "$_o" == *"set   INFERENCE_BACKEND=maya"* && "$_o" == *"set   ENGINE_PROFILE=glm53-flash-abl-q2kxl"* && "$_o" == *"maya.py --config"* && "$_o" == *"MAYA_GLM_RAM_HEADROOM_GB=16"* ]]' "$_o"
check "…and the key travels by environment only: never in the printed command" \
  '[[ "$_o" == *"MAYA_API_KEY=\<from\ LLAMA_API_KEY"* && "$_o" != *sekrit-maya* ]]' "$_o"
# The real launch: /usr/bin/env must not carry the key on its argv (it sat in
# /proc/<pid>/cmdline until env exec'd python); python gets it by environment.
mkdir -p "$T/envbin" "$T/opt/maya/.venv/bin" "$SB/.engines/maya"
printf '#!/bin/bash\nprintf "%%s\\n" "$@" > %s/env-argv\nexec /usr/bin/env "$@"\n' "$T" > "$T/envbin/env"
printf '#!/bin/bash\necho "MAYA_API_KEY=${MAYA_API_KEY:-unset}" > %s/maya-env\n' "$T" > "$T/opt/maya/.venv/bin/python"
chmod +x "$T/envbin/env" "$T/opt/maya/.venv/bin/python"
echo '{"host": "x", "port": 1}' > "$T/maya-prepared.json"; echo "$T/maya-prepared.json" > "$SB/.engines/maya/glm53-flash-abl-q2kxl.config"
env -i "${BASE_ENV[@]}" "${MENV[@]}" PATH="$T/envbin:$T/bin:$(dirname "$PY"):/usr/bin:/bin" REPO_DIR="$SB" \
  OPENBEAST_ENGINE_PROFILE=glm53-flash-abl-q2kxl LLAMA_API_KEY=sekrit-maya \
  bash -c 'source "$REPO_DIR/scripts/lib/engine.sh"; ob_engine_load maya; eng_maya_launch' >/dev/null 2>&1 || true
check "…and the real launch hands MAYA_API_KEY to python by environment, never on env's argv" \
  '[[ -s "$T/env-argv" ]] && grep -q "maya.py" "$T/env-argv" && ! grep -q sekrit "$T/env-argv" && grep -qx "MAYA_API_KEY=sekrit-maya" "$T/maya-env"' \
  "argv: $(tr '\n' ' ' < "$T/env-argv" 2>/dev/null) / $(cat "$T/maya-env" 2>/dev/null)"
_o="$(env -i "${BASE_ENV[@]}" REPO_DIR="$SB" bash -c 'source "$REPO_DIR/scripts/lib/memscope.sh"; source "$REPO_DIR/scripts/lib/engine.sh"; ob_engine_load maya; ob_mem_scope "$(eng_maya_scope_file glm53-flash-abl-q2kxl)" rig 127926272 75')"
check "the Maya profile asks start.sh for the same rig-only 90% / memlock scope as llama.cpp's big mode" \
  '[[ "$_o" == "90 infinity profile:glm53-flash-abl-q2kxl.env" ]]' "$_o"
check "no awk in an adapter uses a {n} interval (mawk 1.3.4-20200120 reads it literally)" \
  '! grep -nE "awk.*~ /[^/]*\{[0-9]+(,[0-9]*)?\}" "$REPO_DIR"/scripts/engines/*.sh "$REPO_DIR"/scripts/lib/engine.sh'
rm -f "$SB/.engines/gate/maya.json"; : > "$SB/openbeast.conf"

# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "INFERENCE_BACKEND=llama: the launch path is unchanged"
: > "$SB/openbeast.conf"; : > "$T/calls.log"; : > "$DST/calls.log"
_o="$(env -i "${BASE_ENV[@]}" DOCKER_STUB_STATE="$DST" OPENBEAST_SERVE_SCRIPT=serve-marker.sh OPENBEAST_LLAMA_LOAD_GRACE=5 OPENBEAST_MODEL_ROLLBACK=false \
        timeout 60 bash "$SB/start.sh" 2>&1 || true)"
check "start.sh runs the serve script, prints the llama.cpp lines it always did" \
  '[[ "$_o" == *"Starting llama.cpp server (serve-marker.sh)..."* ]] && grep -q "serve-marker" "$T/calls.log"' "$(tail -n 8 <<< "$_o")"
check "…never touches engine-serve.sh or runs a container" '[[ "$_o" != *engine-serve* ]] && ! grep -qE "docker (run|stop|pull)" "$DST/calls.log"' "$(cat "$DST/calls.log")"
_hooks="$(grep -c 'engine-plane hook' "$REPO_DIR/start.sh")"
_lp="$(awk '/^wait_llama_health\(\) \{/,/^}/' "$REPO_DIR/start.sh" | grep -c '${_OB_READY_FN:-ob_llama_ready}')"
check "start.sh: the hooks are delimited, and wait_llama_health falls back to ob_llama_ready itself" \
  '[[ "$_hooks" -ge 5 && "$_lp" == 2 ]] && ! grep -n "_OB_READY_FN=" "$REPO_DIR/start.sh" | grep -v "engine-plane\|ob_backend_ready" | grep -q .' "hooks=$_hooks lp=$_lp"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
