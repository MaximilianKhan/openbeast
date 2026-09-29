#!/bin/bash
# The rig's decommissioning and storage-hygiene scripts, against THROWAWAY
# trees only: scripts/uninstall.sh, scripts/logrotate.sh (+ its conf and
# units) and scratch/prune-2026-09-17.sh.
#
# Every external effect is stubbed on PATH and recorded (tailscale, docker,
# systemctl, sudo, pgrep, logrotate), HOME/XDG_CONFIG_HOME point into a temp
# dir, and each case builds its own tree — never the real repo, weights,
# /proc or docker. Run by tests/test_scripts.sh (so CI runs it) or directly:
#   bash tests/test_uninstall.sh
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0; FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
# Git inside the fixtures must not read the developer's config (signing, hooks).
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

# ---------------------------------------------------------------------------
# uninstall.sh
# ---------------------------------------------------------------------------
echo "uninstall.sh (rig):"
RIG="$T/proj/openbeast"; H="$T/home"; LOG="$T/log"
# A throwaway rig: a clean llama.cpp clone (with an upstream), venv, .run with
# both ephemera and durable state, sibling weights, conf, workspace, units.
_build() {
  rm -rf "$T/proj" "$H" "${T:?}/bin" "$T/other" "$T/origin.git" "$T/seed"
  mkdir -p "$RIG/scripts/lib" "$RIG/venv" "$RIG/.run/sessions" "$T/proj/weights" \
           "$H/openbeast-files/users/x" "$H/.config/systemd/user" "$T/bin"
  cp "$REPO_DIR/scripts/uninstall.sh" "$RIG/scripts/"
  cp "$REPO_DIR/scripts/lib/weights.sh" "$RIG/scripts/lib/"
  printf '#!/bin/bash\necho stop >> "$UN_LOG"\n' > "$RIG/stop.sh"; chmod +x "$RIG/stop.sh"
  # llama.cpp: cloned from a bare "upstream", nothing local → disposable
  git init -q "$T/seed" && echo a > "$T/seed/f" && git -C "$T/seed" add f && git -C "$T/seed" commit -qm a
  git clone -q --bare "$T/seed" "$T/origin.git" && git clone -q "$T/origin.git" "$RIG/llama.cpp"
  # .run: ephemera …
  for f in llama.pid chat-local.token gpu.lease.lock stack.log stack.log.1 serve-script; do echo x > "$RIG/.run/$f"; done
  # … and durable state
  for f in clients.json tool-audit.jsonl inference-audit.jsonl artifact-raw.key ssd-wear.json; do echo x > "$RIG/.run/$f"; done
  echo '{}' > "$RIG/.run/sessions/s1.json"
  echo weight > "$T/proj/weights/m.gguf"             # the DEFAULT layout: ../weights, no conf key
  echo "FILES_DIR=$H/openbeast-files" > "$RIG/openbeast.conf"
  echo page > "$H/openbeast-files/users/x/p.html"
  for u in openbeast-watchdog.timer openbeast-logrotate.timer openbeast-logrotate.service; do
    echo unit > "$H/.config/systemd/user/$u"
  done
  : > "$LOG"
  for c in tailscale systemctl sudo; do
    printf '#!/bin/bash\necho "%s $*" >> "$UN_LOG"\n[[ "$1 $2" == "serve status" ]] && echo "|-- / proxy http://127.0.0.1:3000"\nexit 0\n' "$c" > "$T/bin/$c"
    chmod +x "$T/bin/$c"
  done
  # docker: ours is found only by its compose labels; the unfiltered listing
  # also holds a foreign project's volume and a bare one.
  cat > "$T/bin/docker" <<'EOF'
#!/bin/bash
echo "docker $*" >> "$UN_LOG"
if [[ "$1 $2" == "volume ls" ]]; then
  if [[ "$*" == *"com.docker.compose.project=openbeast"* ]]; then echo openbeast_open-webui-data
  elif [[ "$*" != *"--filter"* ]]; then printf '%s\n' openbeast_open-webui-data homelab_open-webui-data open-webui-data models_open-webui-data
  fi
fi
exit 0
EOF
  chmod +x "$T/bin/docker"
}
_un() {   # _un [args…] — run from $UN_CWD (default: the rig)
  (cd "${UN_CWD:-$RIG}" && HOME="$H" XDG_CONFIG_HOME="$H/.config" UN_LOG="$LOG" PATH="$T/bin:$PATH" \
     OPENBEAST_WEIGHTS_DIR='' OPENBEAST_FILES_DIR='' bash "$RIG/scripts/uninstall.sh" "$@")
}

_build
OUT="$(_un 2>&1)"
if [[ -d "$RIG/llama.cpp" && -f "$RIG/.run/llama.pid" && -f "$T/proj/weights/m.gguf" ]] \
   && ! grep -qE "reset|stop$|disable|volume rm|^stop" "$LOG" \
   && grep -q "DRY RUN" <<< "$OUT" && grep -q "would  ./stop.sh" <<< "$OUT"; then
  pass "dry run by default: lists every step, removes nothing, mutates nothing"
else
  fail "dry run touched something: log=$(tr '\n' '|' < "$LOG") :: $OUT"
fi

OUT="$(_un --go 2>&1)" || true
if [[ ! -e "$RIG/llama.cpp" && ! -e "$RIG/venv" ]] \
   && [[ -f "$T/proj/weights/m.gguf" && -f "$RIG/openbeast.conf" && -f "$H/openbeast-files/users/x/p.html" ]] \
   && grep -q "^stop$" "$LOG" && grep -q "^sudo tailscale serve reset" "$LOG" \
   && grep -q "disable --now openbeast-watchdog.timer" "$LOG" \
   && [[ ! -e "$H/.config/systemd/user/openbeast-watchdog.timer" ]] \
   && ! grep -q "volume rm" "$LOG"; then
  pass "--go: stops, unpublishes, removes the unit + a clean llama.cpp clone + venv; KEEPS weights, conf, workspace, the WebUI volume"
else
  fail "--go removed the wrong things: $(ls -a "$RIG" | tr '\n' ' ') :: log=$(tr '\n' '|' < "$LOG") :: $OUT"
fi
# storage-03 / extensions-client-2: .run/ is durable state, not just pidfiles.
_eph_gone=1; for f in llama.pid chat-local.token gpu.lease.lock stack.log stack.log.1 serve-script; do [[ -e "$RIG/.run/$f" ]] && _eph_gone=0; done
_dur_kept=1; for f in clients.json tool-audit.jsonl inference-audit.jsonl artifact-raw.key ssd-wear.json sessions/s1.json; do [[ -e "$RIG/.run/$f" ]] || _dur_kept=0; done
if [[ $_eph_gone -eq 1 && $_dur_kept -eq 1 ]] && grep -q "keep.*\.run/ durable state.*clients.json" <<< "$OUT"; then
  pass "--go: .run/ ephemera (pids, locks, tokens, logs) go; device registry, audit trails, raw-URL key, ledger are KEPT and listed"
else
  fail "--go .run/: ephemera gone=$_eph_gone durable kept=$_dur_kept :: $(ls -a "$RIG/.run" 2>/dev/null | tr '\n' ' ')"
fi
# the logrotate timer is part of the footprint now
if grep -q "disable --now openbeast-logrotate.timer" "$LOG" && [[ ! -e "$H/.config/systemd/user/openbeast-logrotate.service" ]]; then
  pass "--go: removes the openbeast-logrotate timer + service"
else
  fail "--go left the logrotate units: $(ls "$H/.config/systemd/user" | tr '\n' ' ')"
fi

# ci-tests-1 / lifecycle-8: the DEFAULT layout (no conf key, weights in ../weights)
_build
OUT="$(_un --go --purge-all 2>&1)" || true
if [[ ! -e "$T/proj/weights" && ! -e "$RIG/openbeast.conf" && ! -e "$H/openbeast-files" && ! -e "$RIG/.run" ]] \
   && [[ -f "$RIG/scripts/uninstall.sh" ]]; then
  pass "--purge-all: sibling ../weights (the no-key default), conf, workspace and all of .run/ go; the checkout itself never does"
else
  fail "--purge-all: $(ls -a "$T/proj" "$RIG" | tr '\n' ' ') :: $OUT"
fi
# supply-chain-uninstall-foreign-volumes: only THIS compose project's volume
if grep -q "^docker volume rm openbeast_open-webui-data$" "$LOG" \
   && ! grep -qE "^docker volume rm (homelab_open-webui-data|open-webui-data|models_open-webui-data)$" "$LOG" \
   && grep -q "keep.*homelab_open-webui-data.*not labelled" <<< "$OUT"; then
  pass "--purge-data removes only the volume labelled com.docker.compose.project=openbeast; look-alikes are listed and kept"
else
  fail "volume selection: log=$(grep 'volume' "$LOG" | tr '\n' '|')"
fi

# relative WEIGHTS_DIR, run from an unrelated directory that has its own ../weights
_build
echo "WEIGHTS_DIR=../weights" >> "$RIG/openbeast.conf"
mkdir -p "$T/other/x" "$T/other/weights"; echo decoy > "$T/other/weights/keep.me"
OUT="$(UN_CWD="$T/other/x" _un --go --purge-weights 2>&1)" || true
if [[ -f "$T/other/weights/keep.me" && ! -e "$T/proj/weights" ]]; then
  pass "a relative WEIGHTS_DIR resolves against the repo (as lib/weights.sh does), never the caller's cwd"
else
  fail "relative WEIGHTS_DIR: decoy=$([[ -f "$T/other/weights/keep.me" ]] && echo kept || echo DELETED) real=$([[ -e "$T/proj/weights" ]] && echo survived || echo gone) :: $OUT"
fi

# ~ in FILES_DIR and WEIGHTS_DIR (the conf example's own forms); a space in a path
_build
mkdir -p "$H/models dir/gguf" "$H/modelsdir/gguf" "$H/ws"; echo w > "$H/models dir/gguf/m.gguf"; echo d > "$H/modelsdir/gguf/keep"
printf 'FILES_DIR=~/ws\nWEIGHTS_DIR="~/models dir/gguf"\n' > "$RIG/openbeast.conf"
OUT="$(_un --go --purge-data --purge-weights 2>&1)" || true
if [[ ! -e "$H/ws" && ! -e "$H/models dir/gguf" && -f "$H/modelsdir/gguf/keep" && -f "$T/proj/weights/m.gguf" ]]; then
  pass "~ expands in FILES_DIR/WEIGHTS_DIR, and a space inside a path is kept (no look-alike dir is hit)"
else
  fail "tilde/space: $(find "$H" -maxdepth 3 | tr '\n' ' ') :: $OUT"
fi
if [[ ! -e "$RIG/.run/sessions" && -f "$RIG/.run/clients.json" ]]; then
  pass "--purge-data takes the session ledger (.run/sessions) but not the device registry"
else
  fail "--purge-data .run: $(ls -a "$RIG/.run" | tr '\n' ' ')"
fi

# a purge whose target is missing says so instead of printing nothing
_build
echo "WEIGHTS_DIR=$T/nowhere" >> "$RIG/openbeast.conf"
OUT="$(_un --go --purge-weights 2>&1)" || true
if grep -q "not found at $T/nowhere — nothing to purge" <<< "$OUT"; then
  pass "a --purge-* whose target does not exist says 'not found at <path>'"
else
  fail "missing target was silent: $OUT"
fi

# refuse targets that are, or contain, $HOME or the checkout, or resolve into
# a system tree. NEVER name a real system path here: this suite also runs
# against old code to prove it fails, and old code would `rm -rf` it. The
# system-tree case goes through a symlink, which an unguarded rm -rf only
# unlinks.
ln -sfn /usr/share "$T/sys-link"
for bad in "~" ".." "$T/sys-link"; do
  _build
  echo marker > "$H/marker"
  echo "WEIGHTS_DIR=$bad" >> "$RIG/openbeast.conf"
  OUT="$(_un --go --purge-weights 2>&1)" || true
  if [[ -f "$H/marker" && -f "$RIG/scripts/uninstall.sh" && -L "$T/sys-link" ]] && grep -q "REFUSE rm -rf" <<< "$OUT" \
     && ! grep -q "do     rm -rf" <<< "$(grep -A1 '7. Model weights' <<< "$OUT")"; then
    pass "WEIGHTS_DIR=$bad is refused, nothing removed"
  else
    fail "WEIGHTS_DIR=$bad was not refused :: $OUT"
  fi
done

# supply-chain-uninstall-llama-local-branches: local-only commits keep the tree
_build
git -C "$RIG/llama.cpp" switch -qc my-kernels && echo k > "$RIG/llama.cpp/k" \
  && git -C "$RIG/llama.cpp" add k && git -C "$RIG/llama.cpp" commit -qm kernels
OUT="$(_un --go 2>&1)" || true
if [[ -f "$RIG/llama.cpp/k" ]] && grep -q "keep.*llama.cpp/ — it holds local work" <<< "$OUT" \
   && grep -q "commit(s) on local branches" <<< "$OUT"; then
  pass "llama.cpp/ with a local-only branch is kept under --go and the reason printed"
else
  fail "local-only llama.cpp commits: tree $([[ -d "$RIG/llama.cpp" ]] && echo kept || echo DELETED) :: $OUT"
fi
OUT="$(_un --go --purge-build 2>&1)" || true
if [[ ! -e "$RIG/llama.cpp" ]]; then
  pass "--purge-build removes llama.cpp/ anyway"
else
  fail "--purge-build kept llama.cpp/ :: $OUT"
fi
_build
echo dirty >> "$RIG/llama.cpp/f"
OUT="$(_un --go 2>&1)" || true
if [[ -d "$RIG/llama.cpp" ]] && grep -q "uncommitted changes" <<< "$OUT"; then
  pass "llama.cpp/ with uncommitted edits is kept"
else
  fail "dirty llama.cpp/ was removed :: $OUT"
fi

_build
RC=0; OUT="$(_un --bogus 2>&1)" || RC=$?
if [[ $RC -eq 2 && -d "$RIG/.run" && ! -s "$LOG" ]]; then
  pass "an unknown flag is a usage error, and nothing runs"
else
  fail "unknown flag: rc=$RC"
fi

# ---------------------------------------------------------------------------
# logrotate.sh + logrotate-openbeast.conf (storage-04)
# ---------------------------------------------------------------------------
echo ""
echo "logrotate.sh:"
LR="$T/lr/open beast"   # a space in the checkout path, on purpose
rm -rf "$T/lr"; mkdir -p "$LR/scripts" "$LR/.run" "$T/lrbin" "$T/lrhome/.config"
cp "$REPO_DIR/scripts/logrotate.sh" "$REPO_DIR/scripts/logrotate-openbeast.conf" \
   "$REPO_DIR/scripts/openbeast-logrotate.service" "$REPO_DIR/scripts/openbeast-logrotate.timer" "$LR/scripts/"
CONF_OUT="$("$LR/scripts/logrotate.sh" --print-conf)"
_missing=""
for f in stack.log tool-audit.jsonl inference-audit.jsonl chat-audit.jsonl artifact-audit.jsonl 'ext-\*.log'; do
  grep -q "^\"$LR/.run/$f\"$" <<< "$CONF_OUT" || _missing="$_missing $f"
done
if [[ -z "$_missing" ]] && ! grep -qE '@REPO@|^[[:space:]]*su ' <<< "$CONF_OUT"; then
  pass "rendered conf covers stack.log, every *-audit.jsonl and ext-*.log; no su line for a non-root run"
else
  fail "rendered conf missing:$_missing :: $CONF_OUT"
fi

# built-in rotation (no logrotate binary): only files over `size` rotate,
# copytruncate keeps the inode, delaycompress, at most `rotate` kept
_big() { head -c $((51 * 1024 * 1024)) /dev/zero > "$1"; }
_big "$LR/.run/chat-audit.jsonl"; _big "$LR/.run/ext-dashboard.log"; echo small > "$LR/.run/stack.log"
_ino="$(stat -c %i "$LR/.run/chat-audit.jsonl")"
for _i in $(seq 1 10); do
  OPENBEAST_LOGROTATE=/nonexistent "$LR/scripts/logrotate.sh" >/dev/null
  _big "$LR/.run/chat-audit.jsonl"
done
if [[ -f "$LR/.run/chat-audit.jsonl.1" && -f "$LR/.run/chat-audit.jsonl.2.gz" && -f "$LR/.run/chat-audit.jsonl.8.gz" \
      && ! -e "$LR/.run/chat-audit.jsonl.9.gz" && -f "$LR/.run/ext-dashboard.log.1" && ! -e "$LR/.run/stack.log.1" \
      && "$(stat -c %i "$LR/.run/chat-audit.jsonl")" == "$_ino" ]]; then
  pass "built-in rotation: >size rotates (glob included), small files do not, .2+ gzipped, 8 kept, inode preserved"
else
  fail "built-in rotation: $(ls "$LR/.run" | tr '\n' ' ')"
fi

# with a logrotate binary: it gets the rendered conf and a state file under .run
printf '#!/bin/bash\necho "$@" > "%s/lr-args"; cp "$3" "%s/lr-conf"\n' "$T" "$T" > "$T/lrbin/logrotate"; chmod +x "$T/lrbin/logrotate"
OPENBEAST_LOGROTATE="$T/lrbin/logrotate" "$LR/scripts/logrotate.sh"
if [[ "$(cat "$T/lr-args")" == "-s $LR/.run/logrotate.state $LR/.run/logrotate.conf" ]] && grep -q "chat-audit.jsonl" "$T/lr-conf"; then
  pass "with logrotate installed: logrotate -s .run/logrotate.state .run/logrotate.conf"
else
  fail "logrotate invocation: $(cat "$T/lr-args" 2>/dev/null)"
fi

# --install: user units rendered with this checkout's path, timer enabled; no sudo
printf '#!/bin/bash\necho "systemctl $*" >> "%s/lr-sysd"\n' "$T" > "$T/lrbin/systemctl"; chmod +x "$T/lrbin/systemctl"
: > "$T/lr-sysd"
HOME="$T/lrhome" XDG_CONFIG_HOME="$T/lrhome/.config" PATH="$T/lrbin:$PATH" "$LR/scripts/logrotate.sh" --install >/dev/null
_U="$T/lrhome/.config/systemd/user"
# ExecStart quoted: unquoted, systemd splits "$T/lr/open beast/…" at the space
if grep -qxF "ExecStart=\"$LR/scripts/logrotate.sh\"" "$_U/openbeast-logrotate.service" && [[ -f "$_U/openbeast-logrotate.timer" ]] \
   && grep -q "systemctl --user enable --now openbeast-logrotate.timer" "$T/lr-sysd" && ! grep -q sudo "$T/lr-sysd"; then
  pass "--install writes the user service + timer and enables the timer"
else
  fail "--install: $(ls "$_U" 2>/dev/null | tr '\n' ' ') :: $(tr '\n' '|' < "$T/lr-sysd")"
fi
# systemd's own parser, where the host has it: the rendered unit must resolve
# its ExecStart to the real script (verify only parses; it starts nothing).
if command -v systemd-analyze >/dev/null 2>&1; then
  if (cd "$_U" && systemd-analyze --user verify ./openbeast-logrotate.service >/dev/null 2>&1); then
    pass "systemd-analyze verify accepts the rendered unit (space in the checkout path)"
  else
    fail "systemd-analyze verify: $(cd "$_U" && systemd-analyze --user verify ./openbeast-logrotate.service 2>&1 | head -3)"
  fi
fi
# a checkout path systemd would otherwise expand (%specifier, $VAR) stays literal
LR2="$T/lr2/a%h\$HOME"; mkdir -p "$LR2/scripts"; cp "$LR/scripts/"* "$LR2/scripts/"
HOME="$T/lrhome" XDG_CONFIG_HOME="$T/lrhome/.config" PATH="$T/lrbin:$PATH" "$LR2/scripts/logrotate.sh" --install >/dev/null
if grep -qxF "ExecStart=\"$T/lr2/a%%h\$\$HOME/scripts/logrotate.sh\"" "$_U/openbeast-logrotate.service" \
   && grep -qxF "WorkingDirectory=$T/lr2/a%%h\$HOME" "$_U/openbeast-logrotate.service"; then
  pass "--install escapes % (and \$ in ExecStart) so systemd keeps the path literal"
else
  fail "--install escaping: $(grep -E '^(ExecStart|WorkingDirectory)=' "$_U/openbeast-logrotate.service" | tr '\n' '|')"
fi

# ---------------------------------------------------------------------------
# scratch/prune-2026-09-17.sh (storage-05, storage-07)
# ---------------------------------------------------------------------------
echo ""
echo "prune-2026-09-17.sh:"
OB="$T/pr/openbeast"; PH="$T/prhome"; PB="$T/prbin"; PLOG="$T/prlog"
rm -rf "$T/pr" "$PH" "$PB"; mkdir -p "$OB/scratch" "$OB/scripts" "$OB/weights" "$OB/.run" "$PH" "$PB" "$T/proc/4242"
cp "$REPO_DIR/scratch/prune-2026-09-17.sh" "$OB/scratch/"
for w in Qwen3.8-27B-Q6_K.gguf SocratTeachLLM-Q8_0.gguf glm-4-9b-chat-Q8_0.gguf; do echo w > "$OB/weights/$w"; done
# a campaign script NOT in any fixed list names one weight …
echo 'M=$W/glm-4-9b-chat-Q8_0.gguf' > "$OB/scratch/campaign_master4.sh"
# … and a live llama-server serves another: named on its cmdline, fd dir empty
printf '%s\0' llama-server -m "$OB/weights/Qwen3.8-27B-Q6_K.gguf" --port 8080 > "$T/proc/4242/cmdline"
: > "$T/proc/4242/maps"
# … and a second live server runs a SPLIT model the way the Flash-Next serve
# script does: -m names shard 00001 only, --load-mode none maps nothing. Its
# shards 2 and 3 are live too. A different split set (not served) must still go.
mkdir -p "$T/proc/4343"
for n in 1 2 3; do echo w > "$OB/weights/Qwen3.8-Flash-Next-Uncensored-IQ4_XS-0000$n-of-00003.gguf"; done
for n in 1 2; do echo w > "$OB/weights/Qwen3.8-Flash-Next-Uncensored-Q2_K-0000$n-of-00002.gguf"; done
printf '%s\0' llama-server -m "$OB/weights/Qwen3.8-Flash-Next-Uncensored-IQ4_XS-00001-of-00003.gguf" --load-mode none > "$T/proc/4343/cmdline"
: > "$T/proc/4343/maps"
printf 'services:\n  open-webui:\n    image: ghcr.io/open-webui/open-webui:main@sha256:aa\n  searxng:\n    image: docker.io/searxng/searxng:latest@sha256:bb\n' > "$OB/docker-compose.yml"
printf '#!/bin/bash\necho 4242; echo 4343\n' > "$PB/pgrep"
cat > "$PB/docker" <<'EOF'
#!/bin/bash
echo "docker $*" >> "$PLOG"
if [[ "$1 $2" == "image ls" ]]; then
  printf 'sha256:1111111111111111\tghcr.io/open-webui/open-webui\t2020-01-01 00:00:00 +0000 UTC\n'
  printf 'sha256:2222222222222222\tdocker.io/searxng/searxng\t2020-01-01 00:00:00 +0000 UTC\n'
  printf 'sha256:3333333333333333\told/junk\t2020-01-01 00:00:00 +0000 UTC\n'
  printf 'sha256:4444444444444444\tfresh/thing\t%s +0000 UTC\n' "$(date -u '+%Y-%m-%d %H:%M:%S')"
fi
exit 0
EOF
chmod +x "$PB/pgrep" "$PB/docker"
: > "$PLOG"
# The script's --go deletes real weights by absolute path unless it honours
# PRUNE_OB — never run a copy that does not (an older revision hardcodes it).
if ! grep -q 'OB="${PRUNE_OB:-' "$OB/scratch/prune-2026-09-17.sh"; then
  fail "prune script does not honour PRUNE_OB — NOT run (it would act on the real repo)"
else
OUT="$(HOME="$PH" PLOG="$PLOG" PATH="$PB:$PATH" PRUNE_OB="$OB" PRUNE_PROC="$T/proc" bash "$OB/scratch/prune-2026-09-17.sh" --go --flash-next 2>&1)" || true
if [[ -f "$OB/weights/Qwen3.8-27B-Q6_K.gguf" && -f "$OB/weights/glm-4-9b-chat-Q8_0.gguf" && ! -e "$OB/weights/SocratTeachLLM-Q8_0.gguf" ]]; then
  pass "KEEP guard sees a live server's weight via cmdline (not fd) and any scratch/*.sh; an unguarded target still goes"
else
  fail "prune KEEP guard: $(ls "$OB/weights" | tr '\n' ' ') :: $OUT"
fi
_fn="$OB/weights/Qwen3.8-Flash-Next-Uncensored"
if [[ -f "$_fn-IQ4_XS-00001-of-00003.gguf" && -f "$_fn-IQ4_XS-00002-of-00003.gguf" && -f "$_fn-IQ4_XS-00003-of-00003.gguf" \
      && ! -e "$_fn-Q2_K-00001-of-00002.gguf" && ! -e "$_fn-Q2_K-00002-of-00002.gguf" ]]; then
  pass "--flash-next: every shard of a live split model is kept (-m names 00001, --load-mode none); an unserved split set goes"
else
  fail "prune split-GGUF guard: $(ls "$OB/weights" | tr '\n' ' ')"
fi
if grep -q "^docker image rm sha256:3333333333333333$" "$PLOG" \
   && ! grep -qE "image rm sha256:(1111|2222|4444)" "$PLOG" && ! grep -q "image prune" "$PLOG"; then
  pass "docker: old unpinned images go; the compose-pinned open-webui/searxng and fresh images never do (no image prune -a)"
else
  fail "prune docker: $(tr '\n' '|' < "$PLOG")"
fi
fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"
[[ $FAIL -eq 0 ]]
