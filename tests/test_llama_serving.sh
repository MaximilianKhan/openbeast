#!/bin/bash
# llama-serving: serve.sh, its siblings (run.sh, serve-instinct-scorer.sh,
# measure-vram.sh, the MTP profilers) and the weight check they share.
# Hermetic, like test_scripts.sh §8b: every case runs a throwaway copy of the
# scripts with stub binaries (llama-server, llama-cli, nvidia-smi, curl,
# gpu-lease.sh). Nothing here reads this box's GPU or weights, binds a port
# the stack uses, or touches a real openbeast.conf. GNU userland (stat -c,
# truncate) — run it where the stack runs (Linux).
#
# Every case is a finding of the 2026-11 llama-serving review; the comment
# above it names which.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }
SCRATCHES=()
cleanup() { local d; for d in "${SCRATCHES[@]}"; do rm -rf "$d"; done; }
trap cleanup EXIT
_mk() { local d; d="$(mktemp -d)"; SCRATCHES+=("$d"); printf '%s' "$d"; }

# --- the serve.sh rig: a copy of serve.sh + lib, a stub llama-server that
# records its argv ($SV/argv) and environment ($SV/env), a stub nvidia-smi
# that reports the cards in $SV/gpus.
SV="$(_mk)"
mkdir -p "$SV/scripts/lib" "$SV/llama.cpp/build/bin" "$SV/weights" "$SV/bin" "$SV/home"
cp "$REPO_DIR/scripts/serve.sh" "$REPO_DIR/scripts/run.sh" "$SV/scripts/"
cp "$REPO_DIR"/scripts/lib/*.sh "$SV/scripts/lib/"
printf 'SEARXNG_SECRET=x\n' > "$SV/openbeast.conf"
: > "$SV/scripts/weights.registry"
cat > "$SV/llama.cpp/build/bin/llama-server" <<SH
#!/bin/bash
if [[ " \$* " == *" --help "* ]]; then cat "$SV/help.txt" 2>/dev/null; exit 0; fi
printf '%s\n' "\$@" > "$SV/argv"
env > "$SV/env"
SH
cat > "$SV/llama.cpp/build/bin/llama-cli" <<SH
#!/bin/bash
printf '%s\n' "\$@" > "$SV/cli-argv"
env > "$SV/cli-env"
SH
cat > "$SV/bin/nvidia-smi" <<SH
#!/bin/bash
[[ -s "$SV/gpus" ]] || exit 1
case "\$*" in
  *memory.total*) cat "$SV/gpus" ;;
  *name*)         sed 's/.*/Stub GPU/' "$SV/gpus" ;;
esac
exit 0
SH
chmod +x "$SV/llama.cpp/build/bin/llama-server" "$SV/llama.cpp/build/bin/llama-cli" "$SV/bin/nvidia-smi"
printf 'usage: llama-server\n-cram, --cache-ram N   set the maximum cache size in MiB (default: 8192)\n' > "$SV/help.txt"
: > "$SV/gpus"
# _pin <file> [bytes] — a registry row for weights/<file> (its real size by default)
_pin() {
  local b="${2:-$(stat -L -c%s "$SV/weights/$1")}"
  printf '%s\t%s\t%s\torg/repo\t-\n' "$(printf '0%.0s' {1..64})" "$b" "$1" >> "$SV/scripts/weights.registry"
}
# _serve [ENV=val...] -- <serve.sh args>  -> SV_OUT, SV_RC
_serve() {
  local envs=()
  while [[ "$1" != "--" ]]; do envs+=("$1"); shift; done; shift
  rm -f "$SV/argv" "$SV/env"; SV_RC=0
  SV_OUT="$(env -i HOME="$SV/home" PATH="$SV/bin:/usr/bin:/bin" ${envs[@]+"${envs[@]}"} \
    bash "$SV/scripts/serve.sh" "$@" 2>&1)" || SV_RC=$?
}
_sv_arg() { awk -v f="$1" 'p {print; exit} $0 == f {p = 1}' "$SV/argv" 2>/dev/null || true; }
_launched() { [[ -s "$SV/argv" ]]; }

echo ""
echo "weights behind a symlink (stat without -L):"
head -c 4096 /dev/zero > "$SV/weights/blob-4096"
ln -s "$SV/weights/blob-4096" "$SV/weights/linked.gguf"
_pin linked.gguf 4096
_serve OPENBEAST_WEIGHT_ENFORCE=strict -- -m "$SV/weights/linked.gguf" -c 8192
if [[ $SV_RC -eq 0 ]] && _launched && [[ "$SV_OUT" != *"registry pins"* ]]; then
  pass "a symlinked, correctly pinned weight launches under WEIGHT_ENFORCE=strict"
else
  fail "symlinked pinned weight under strict (rc=$SV_RC): $(tr '\n' ' ' <<< "$SV_OUT")"
fi
# The context scaler must see the size of what the link points at.
truncate -s 19535701408 "$SV/weights/big-blob"
ln -s "$SV/weights/big-blob" "$SV/weights/big-linked.gguf"
cp --sparse=always "$SV/weights/big-blob" "$SV/weights/big-real.gguf" 2>/dev/null \
  || truncate -s 19535701408 "$SV/weights/big-real.gguf"
printf '24564\n' > "$SV/gpus"
_serve OPENBEAST_WEIGHT_ENFORCE=off -- -m "$SV/weights/big-real.gguf" -c 262144;   _c_real="$(_sv_arg -c)"
_serve OPENBEAST_WEIGHT_ENFORCE=off -- -m "$SV/weights/big-linked.gguf" -c 262144; _c_link="$(_sv_arg -c)"
: > "$SV/gpus"
if [[ -n "$_c_real" && "$_c_real" == "$_c_link" && "$_c_real" -lt 262144 ]]; then
  pass "…and the context scaler budgets a symlinked weight at its real size (-c $_c_link)"
else
  fail "context for a symlinked 19.5 GB weight on a 24 GB card: -c '$_c_link', the same file unlinked: -c '$_c_real'"
fi
# verify-weights.sh is the operator's size check on the same files.
VW="$(_mk)"; mkdir -p "$VW/scripts/lib" "$VW/weights"
cp "$REPO_DIR/scripts/verify-weights.sh" "$VW/scripts/"; cp "$REPO_DIR"/scripts/lib/*.sh "$VW/scripts/lib/"
head -c 4096 /dev/zero > "$VW/blob"; ln -s "$VW/blob" "$VW/weights/linked.gguf"
printf '%s\t4096\tlinked.gguf\torg/repo\t-\n' "$(printf '0%.0s' {1..64})" > "$VW/scripts/weights.registry"
_vw_rc=0; _vw_out="$(env -i HOME="$VW" PATH=/usr/bin:/bin OPENBEAST_WEIGHTS_DIR="$VW/weights" bash "$VW/scripts/verify-weights.sh" 2>&1)" || _vw_rc=$?
[[ $_vw_rc -eq 0 && "$_vw_out" != *FAIL* ]] && pass "verify-weights.sh passes a symlinked weight of the pinned size" \
  || fail "verify-weights.sh on a symlinked weight (rc=$_vw_rc): $(tr '\n' ' ' <<< "$_vw_out")"

echo ""
echo "the whole weight set is checked (split shards, --mmproj, -md):"
for _i in 1 2 3; do head -c 2048 /dev/zero > "$SV/weights/Big-0000${_i}-of-00003.gguf"; done
_pin Big-00001-of-00003.gguf; _pin Big-00002-of-00003.gguf 9999; _pin Big-00003-of-00003.gguf
_serve OPENBEAST_WEIGHT_ENFORCE=strict -- -m "$SV/weights/Big-00001-of-00003.gguf" -c 8192
if [[ $SV_RC -eq 3 ]] && ! _launched && [[ "$SV_OUT" == *"Big-00002-of-00003.gguf' is 2048 bytes, registry pins 9999"* ]]; then
  pass "strict refuses (exit 3) when shard 2 of a split GGUF has the wrong size"
else
  fail "a wrong-size shard 2 under strict (rc=$SV_RC): $(tr '\n' ' ' <<< "$SV_OUT")"
fi
head -c 100 /dev/zero > "$SV/weights/proj.gguf"; _pin proj.gguf 101
head -c 4096 /dev/zero > "$SV/weights/main.gguf"; _pin main.gguf
_serve OPENBEAST_WEIGHT_ENFORCE=strict -- -m "$SV/weights/main.gguf" -c 8192 --mmproj "$SV/weights/proj.gguf"
[[ $SV_RC -eq 3 ]] && ! _launched && [[ "$SV_OUT" == *"'proj.gguf' is 100 bytes"* ]] \
  && pass "…and a wrong-size --mmproj" || fail "a wrong-size --mmproj under strict (rc=$SV_RC): $(tr '\n' ' ' <<< "$SV_OUT")"
head -c 100 /dev/zero > "$SV/weights/draft.gguf"
_serve OPENBEAST_WEIGHT_ENFORCE=strict -- -m "$SV/weights/main.gguf" -c 8192 -md "$SV/weights/draft.gguf"
[[ $SV_RC -eq 3 ]] && ! _launched && [[ "$SV_OUT" == *"'draft.gguf' is NOT in scripts/weights.registry"* ]] \
  && pass "…and an unpinned -md draft model" || fail "an unpinned -md under strict (rc=$SV_RC): $(tr '\n' ' ' <<< "$SV_OUT")"
head -c 100 /dev/zero > "$SV/weights/pending.gguf"
printf 'PENDING\t0\tpending.gguf\torg/repo\t-\n' >> "$SV/scripts/weights.registry"
_serve OPENBEAST_WEIGHT_ENFORCE=strict -- -m "$SV/weights/main.gguf" -c 8192 --mmproj "$SV/weights/pending.gguf"
[[ $SV_RC -eq 3 ]] && ! _launched && [[ "$SV_OUT" == *"'pending.gguf' is pinned PENDING"* ]] \
  && pass "…and a PENDING row (nothing to check the file against)" || fail "a PENDING-pinned file under strict (rc=$SV_RC): $(tr '\n' ' ' <<< "$SV_OUT")"
_pin Big-00002-of-00003.gguf   # the right size; first matching row wins, so rebuild
grep -v $'\tBig-00002-of-00003.gguf\t' "$SV/scripts/weights.registry" > "$SV/reg.tmp"; mv "$SV/reg.tmp" "$SV/scripts/weights.registry"; _pin Big-00002-of-00003.gguf
_pin draft.gguf; grep -v $'\tproj.gguf\t' "$SV/scripts/weights.registry" > "$SV/reg.tmp"; mv "$SV/reg.tmp" "$SV/scripts/weights.registry"; _pin proj.gguf
_serve OPENBEAST_WEIGHT_ENFORCE=strict -- -m "$SV/weights/Big-00001-of-00003.gguf" -c 8192 --mmproj "$SV/weights/proj.gguf" -md "$SV/weights/draft.gguf"
[[ $SV_RC -eq 0 ]] && _launched && pass "…a fully pinned set (3 shards, mmproj, draft) launches under strict (control)" \
  || fail "a fully pinned weight set was refused (rc=$SV_RC): $(tr '\n' ' ' <<< "$SV_OUT")"
_serve OPENBEAST_WEIGHT_ENFORCE=warn -- -m "$SV/weights/main.gguf" -c 8192 --mmproj "$SV/weights/pending.gguf"
[[ $SV_RC -eq 0 ]] && _launched && [[ "$SV_OUT" == *"WARNING: weight 'pending.gguf' is pinned PENDING"* ]] \
  && pass "…and warn mode warns about the extra file but launches" || fail "warn mode on a PENDING mmproj (rc=$SV_RC): $(tr '\n' ' ' <<< "$SV_OUT")"
# The scaler counts every shard: three 6 GiB shards cost what one 18 GiB file does.
for _i in 1 2 3; do truncate -s 6442450944 "$SV/weights/Six-0000${_i}-of-00003.gguf"; done
truncate -s 19327352832 "$SV/weights/Eighteen.gguf"
printf '24564\n' > "$SV/gpus"
_serve OPENBEAST_WEIGHT_ENFORCE=off -- -m "$SV/weights/Six-00001-of-00003.gguf" -c 262144; _c_split="$(_sv_arg -c)"
_serve OPENBEAST_WEIGHT_ENFORCE=off -- -m "$SV/weights/Eighteen.gguf" -c 262144;          _c_one="$(_sv_arg -c)"
[[ -n "$_c_one" && "$_c_split" == "$_c_one" ]] && pass "the context scaler sums the shards of a split GGUF (-c $_c_split)" \
  || fail "split GGUF context: -c '$_c_split', one file of the same total: -c '$_c_one'"
# With expert weights kept in host RAM the GGUF size says nothing about VRAM:
# no scaling and no false "can't hold this model's weights".
_serve OPENBEAST_WEIGHT_ENFORCE=off -- -m "$SV/weights/Six-00001-of-00003.gguf" -c 262144 --n-cpu-moe 35
[[ "$(_sv_arg -c)" == "262144" && "$SV_OUT" != *"can't hold this model"* && "$SV_OUT" == *"host RAM"* ]] \
  && pass "…and leaves -c alone (with a note) when --n-cpu-moe keeps weights in host RAM" \
  || fail "--n-cpu-moe launch was context-scaled: -c '$(_sv_arg -c)' :: $(grep -i context <<< "$SV_OUT" | tr '\n' ' ')"
: > "$SV/gpus"

echo ""
echo "host prompt cache vs the stack's memory scope:"
_pcm() { ( source "$REPO_DIR/scripts/lib/hardware.sh"; ob_prompt_cache_mb "$@" ); }
# 122 GB rig (127,926,272 kB), MEM_LIMIT_PCT 75 -> 93,696 MiB scope.
if [[ "$(_pcm 127926272)" == "43724" && "$(_pcm 127926272 75 0)" == "43724" \
      && "$(_pcm 127926272 75 66560)" == "18944" && "$(_pcm 127926272 75 94208)" == "0" ]]; then
  pass "ob_prompt_cache_mb: host-resident weights come out of the scope first (none: unchanged 35%)"
else
  fail "ob_prompt_cache_mb with host weights: none $(_pcm 127926272 75 0) (43724), 65 GiB $(_pcm 127926272 75 66560) (18944), 92 GiB $(_pcm 127926272 75 94208) (0)"
fi
# serve.sh e2e: a CPU-offloaded weight set as big as this host's scope gets no
# auto cache (the server default), a GPU-resident one keeps the auto size.
_memkb="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo)"
truncate -s $(( _memkb * 1024 * 3 / 4 )) "$SV/weights/Huge.gguf"
_serve OPENBEAST_WEIGHT_ENFORCE=off -- -m "$SV/weights/Huge.gguf" -c 8192 --n-cpu-moe 35 --load-mode none
_pc_cpu="$(_sv_arg --cache-ram)"
_serve OPENBEAST_WEIGHT_ENFORCE=off -- -m "$SV/weights/Huge.gguf" -c 8192
_pc_gpu="$(_sv_arg --cache-ram)"
_auto="$(_pcm "$_memkb")"; [[ "$_auto" == "0" ]] && _auto=""
if [[ -z "$_pc_cpu" && "$_pc_gpu" == "$_auto" ]]; then
  pass "serve.sh: no auto --cache-ram beside host-resident weights that fill the scope (GPU-resident: ${_pc_gpu:-none})"
else
  fail "auto --cache-ram: CPU-offloaded '$_pc_cpu' (want none), GPU-resident '$_pc_gpu' (want '$_auto')"
fi
rm -f "$SV/weights/Huge.gguf"

echo ""
echo "keyless llama-server on a network bind:"
_serve OPENBEAST_BIND=192.0.2.10 -- -m "$SV/weights/main.gguf" -c 8192
if [[ $SV_RC -eq 3 ]] && ! _launched && [[ "$SV_OUT" == *"ALLOW_OPEN_INFERENCE"* ]]; then
  pass "BIND_HOST=192.0.2.10 with no LLAMA_API_KEY is refused (exit 3), naming ALLOW_OPEN_INFERENCE"
else
  fail "keyless non-loopback bind (rc=$SV_RC, launched=$(_launched && echo yes || echo no)): $(tr '\n' ' ' <<< "$SV_OUT")"
fi
_serve -- -m "$SV/weights/main.gguf" -c 8192 --host 0.0.0.0
[[ $SV_RC -eq 3 ]] && ! _launched && pass "…so is --host 0.0.0.0 on the command line (the effective host is judged)" \
  || fail "keyless --host 0.0.0.0 (rc=$SV_RC): $(tr '\n' ' ' <<< "$SV_OUT")"
_serve OPENBEAST_BIND=192.0.2.10 OPENBEAST_ALLOW_OPEN_INFERENCE=true -- -m "$SV/weights/main.gguf" -c 8192
[[ $SV_RC -eq 0 && "$(_sv_arg --host)" == "192.0.2.10" ]] && pass "…ALLOW_OPEN_INFERENCE=true acknowledges it and launches" \
  || fail "ALLOW_OPEN_INFERENCE=true (rc=$SV_RC): $(tr '\n' ' ' <<< "$SV_OUT")"
_serve OPENBEAST_BIND=192.0.2.10 OPENBEAST_API_KEY=k1 -- -m "$SV/weights/main.gguf" -c 8192
[[ $SV_RC -eq 0 && "$(_sv_arg --host)" == "192.0.2.10" ]] && pass "…and a keyed server on the same bind launches (control)" \
  || fail "keyed non-loopback bind (rc=$SV_RC): $(tr '\n' ' ' <<< "$SV_OUT")"
_serve -- -m "$SV/weights/main.gguf" -c 8192
[[ $SV_RC -eq 0 && "$(_sv_arg --host)" == "127.0.0.1" ]] && pass "…as does the keyless loopback default (control)" \
  || fail "keyless loopback default (rc=$SV_RC): $(tr '\n' ' ' <<< "$SV_OUT")"

echo ""
echo "browser origins (keyless loopback llama-server):"
_serve -- -m "$SV/weights/main.gguf" -c 8192
if grep -qx 'LLAMA_ARG_CORS_ORIGINS=localhost' "$SV/env" 2>/dev/null; then
  pass "llama-server only reflects localhost origins by default (LLAMA_ARG_CORS_ORIGINS=localhost)"
else
  fail "llama-server started with CORS '$(grep '^LLAMA_ARG_CORS_ORIGINS=' "$SV/env" 2>/dev/null)' (want localhost)"
fi
_serve LLAMA_ARG_CORS_ORIGINS=https://ui.example -- -m "$SV/weights/main.gguf" -c 8192
grep -qx 'LLAMA_ARG_CORS_ORIGINS=https://ui.example' "$SV/env" 2>/dev/null \
  && pass "…an operator's own LLAMA_ARG_CORS_ORIGINS stands" \
  || fail "LLAMA_ARG_CORS_ORIGINS override lost: $(grep '^LLAMA_ARG_CORS' "$SV/env" 2>/dev/null)"

echo ""
echo "run.sh (interactive llama-cli):"
_run() { rm -f "$SV/cli-argv" "$SV/cli-env"; RUN_RC=0
  RUN_OUT="$(env -i HOME="$SV/home" PATH="$SV/bin:/usr/bin:/bin" "$@" 2>&1)" || RUN_RC=$?; }
_run bash "$SV/scripts/run.sh" -m "$SV/weights/main.gguf" -c 8192
grep -q "^LD_LIBRARY_PATH=$SV/llama.cpp/build/bin" "$SV/cli-env" 2>/dev/null \
  && pass "run.sh resolves llama-cli's shared libs next to the binary (LD_LIBRARY_PATH), as serve.sh does" \
  || fail "run.sh LD_LIBRARY_PATH: '$(grep '^LD_LIBRARY_PATH=' "$SV/cli-env" 2>/dev/null)'"
head -c 10 /dev/zero > "$SV/weights/short.gguf"; _pin short.gguf 11
_run OPENBEAST_WEIGHT_ENFORCE=strict bash "$SV/scripts/run.sh" -m "$SV/weights/short.gguf" -c 8192
[[ $RUN_RC -eq 3 && ! -e "$SV/cli-argv" ]] && pass "…and WEIGHT_ENFORCE=strict refuses a wrong-size weight there too (exit 3)" \
  || fail "run.sh under strict with a wrong-size weight (rc=$RUN_RC): $(tr '\n' ' ' <<< "$RUN_OUT")"

echo ""
echo "serve-instinct-scorer.sh pass-through arguments:"
IS="$(_mk)"; mkdir -p "$IS/scripts/lib" "$IS/run"
cp "$REPO_DIR/scripts/serve-instinct-scorer.sh" "$IS/scripts/"; cp "$REPO_DIR"/scripts/lib/*.sh "$IS/scripts/lib/"
printf '#!/bin/bash\nprintf "%%s\\n" "$@" > "%s/serve-argv"\nenv > "%s/serve-env"\n' "$IS" "$IS" > "$IS/scripts/serve.sh"
chmod +x "$IS/scripts/serve.sh"
head -c 100 /dev/zero > "$IS/model.gguf"
_is_port=$(( 20000 + RANDOM % 20000 ))
_scorer() { rm -f "$IS/serve-argv" "$IS/serve-env"; IS_RC=0
  IS_OUT="$(env -i HOME="$IS" PATH=/usr/bin:/bin INSTINCT_SCORER_MODEL="$IS/model.gguf" INSTINCT_RUN_DIR="$IS/run" \
    INSTINCT_SCORER_PORT="$_is_port" bash "$IS/scripts/serve-instinct-scorer.sh" "$@" 2>&1)" || IS_RC=$?; }
_scorer --host 0.0.0.0
[[ $IS_RC -eq 2 && ! -e "$IS/serve-argv" ]] && pass "--host 0.0.0.0 is refused, as the header promises (a non-loopback host)" \
  || fail "scorer --host 0.0.0.0 (rc=$IS_RC, launched=$([[ -e "$IS/serve-argv" ]] && echo yes || echo no)): $IS_OUT"
_scorer -p 8080
[[ $IS_RC -eq 2 && ! -e "$IS/serve-argv" ]] && pass "…so is -p 8080 (it would skip the port checks)" \
  || fail "scorer -p 8080 (rc=$IS_RC): $IS_OUT"
_scorer --port=8080
[[ $IS_RC -eq 2 && ! -e "$IS/serve-argv" ]] && pass "…and --port=8080" || fail "scorer --port=8080 (rc=$IS_RC): $IS_OUT"
_scorer
if [[ $IS_RC -eq 0 && "$(awk 'p {print; exit} $0 == "--host" {p = 1}' "$IS/serve-argv" 2>/dev/null)" == "127.0.0.1" ]]; then
  pass "…and a plain start launches on loopback (control)"
else
  fail "scorer plain start (rc=$IS_RC): $IS_OUT"
fi
_pc_is="$(grep '^OPENBEAST_PROMPT_CACHE_RAM_MB=' "$IS/serve-env" 2>/dev/null | cut -d= -f2)"
[[ "$_pc_is" =~ ^[0-9]+$ && "$_pc_is" -gt 0 && "$_pc_is" -le 2048 ]] \
  && pass "the scorer sizes its own host prompt cache (${_pc_is} MiB), not 35% of RAM beside the primary's" \
  || fail "scorer prompt cache: OPENBEAST_PROMPT_CACHE_RAM_MB='$_pc_is'"

echo ""
echo "========================================"
echo "llama-serving: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
