#!/usr/bin/env bash
# beast-video on the rig: the ComfyUI engine and the beast-video service.
# (docs/BEAST_VIDEO.md)
#
#   scripts/video.sh install     the engine (ComfyUI at a pinned commit, its own
#                                venv with PyTorch) and the service's venv
#   scripts/video.sh up          start both; refuses while something else holds the GPU
#   scripts/video.sh down        stop both and free the card
#   scripts/video.sh status      what is running, the card, where clips are kept
#   scripts/video.sh publish     tailnet-only HTTPS on :$BEAST_VIDEO_TS_PORT (tailscale serve)
#   scripts/video.sh unpublish   take that mount away
#   scripts/video.sh logs        follow both logs
#
# Run it ON the rig. The video model and an LLM do not fit the card together:
# stop the stack first (./stop.sh), and `down` here before ./start.sh.
#
# Environment (all optional):
#   BEAST_VIDEO_HOME     engine, venvs, pid files, logs   (default ~/beast-video)
#   BEAST_VIDEO_WEIGHTS  the folder holding the model repos (default /mnt/9100s4tb/weights)
#   BEAST_VIDEO_DIR      jobs and clips   (default /mnt/9100s4tb/beast-video if that
#                        drive is mounted, else $BEAST_VIDEO_HOME/data)
#   BEAST_VIDEO_PORT     the service's port               (default 3010)
#   BEAST_VIDEO_BIND     where the service listens: 127.0.0.1 (default, then
#                        `publish` it), or `tailnet` to listen on this rig's
#                        Tailscale address itself: plain HTTP inside the
#                        tailnet's own encryption, no `tailscale serve` (and so
#                        no sudo) needed
#   BEAST_VIDEO_ENGINE_PORT  ComfyUI's loopback port      (default 8188)
#   BEAST_VIDEO_TS_PORT  the tailnet HTTPS port           (default 8448)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BV_HOME="${BEAST_VIDEO_HOME:-$HOME/beast-video}"
WEIGHTS="${BEAST_VIDEO_WEIGHTS:-/mnt/9100s4tb/weights}"
if [[ -z "${BEAST_VIDEO_DIR:-}" ]]; then
  if [[ -d /mnt/9100s4tb && -w /mnt/9100s4tb ]]; then BEAST_VIDEO_DIR=/mnt/9100s4tb/beast-video
  else BEAST_VIDEO_DIR="$BV_HOME/data"; fi
fi
PORT="${BEAST_VIDEO_PORT:-3010}"
ENGINE_PORT="${BEAST_VIDEO_ENGINE_PORT:-8188}"
TS_PORT="${BEAST_VIDEO_TS_PORT:-8448}"
BIND="${BEAST_VIDEO_BIND:-127.0.0.1}"
if [[ "$BIND" == "tailnet" ]]; then
  BIND="$(tailscale ip -4 2>/dev/null | head -1)"
  [[ -n "$BIND" ]] || { echo "video.sh: BEAST_VIDEO_BIND=tailnet, but tailscale has no address here" >&2; exit 1; }
fi
# ComfyUI v0.46.0 (2026-11-27): the first release line with MiniMax H3 and the
# int8 / w4a8 / NVFP4 weight formats the Sulphur-3 files use. Pinned: the
# engine runs model code, so it moves only when someone moves this line.
COMFY_URL="https://github.com/comfyanonymous/ComfyUI"
COMFY_REF="a14e815c1f72dff6fe57646c615d217f63da7c61"
RUN="$BV_HOME/run"

say()  { printf '%s\n' "$*"; }
die()  { printf 'video.sh: %s\n' "$*" >&2; exit 1; }
alive() { [[ -f "$1" ]] && kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null; }
py312() {
  for c in "${OPENBEAST_PYTHON:-}" python3.12 "$HOME/.local/bin/python3.12" python3.13 python3.11; do
    [[ -n "$c" ]] && command -v "$c" >/dev/null 2>&1 && { command -v "$c"; return 0; }
  done
  return 1
}

write_model_paths() {
  mkdir -p "$BV_HOME"
  cat > "$BV_HOME/extra_model_paths.yaml" <<EOF
# Written by scripts/video.sh. ComfyUI reads the weights where they already are.
beast:
  base_path: $WEIGHTS
  diffusion_models: |
    SulphurAI-Sulphur-3
    SulphurAI-Sulphur-3/quantized
    chatrlabs-Qwen-Image-2.1-Uncensored-Omni-Quantized
  text_encoders: |
    Comfy-Org-MiniMax-H3/text_encoders
    Comfy-Org-Qwen-Image-2.1/text_encoders
  vae: |
    unsloth-MiniMax-H3-GGUF/vae
    Comfy-Org-Qwen-Image-2.1/vae
  loras: chatrlabs-Qwen-Image-2.1-Uncensored-Omni-LoRA-v0.1
EOF
}

cmd_install() {
  command -v git >/dev/null || die "git is not installed"
  local py; py="$(py312)" || die "no python 3.11-3.13 found (PyTorch has no wheels for newer ones yet)"
  mkdir -p "$BV_HOME" "$RUN"
  if [[ ! -d "$BV_HOME/ComfyUI/.git" ]]; then
    say "cloning ComfyUI…"; git clone -q "$COMFY_URL" "$BV_HOME/ComfyUI"
  fi
  git -C "$BV_HOME/ComfyUI" cat-file -e "$COMFY_REF^{commit}" 2>/dev/null \
    || git -C "$BV_HOME/ComfyUI" fetch -q origin
  git -C "$BV_HOME/ComfyUI" checkout -q "$COMFY_REF"
  say "engine at $(git -C "$BV_HOME/ComfyUI" rev-parse --short HEAD)"
  [[ -x "$BV_HOME/venv/bin/python" ]] || "$py" -m venv "$BV_HOME/venv"
  say "installing PyTorch (CUDA 12.8) and the engine's requirements…"
  "$BV_HOME/venv/bin/pip" install -q --upgrade pip
  "$BV_HOME/venv/bin/pip" install -q torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
  "$BV_HOME/venv/bin/pip" install -q -r "$BV_HOME/ComfyUI/requirements.txt"
  "$BV_HOME/venv/bin/python" -c 'import torch; assert torch.cuda.is_available(), "PyTorch sees no CUDA device"; print("torch", torch.__version__, torch.cuda.get_device_name(0))'
  # The service needs four packages, at the versions agents/requirements.txt pins.
  [[ -x "$BV_HOME/app-venv/bin/python" ]] || "$py" -m venv "$BV_HOME/app-venv"
  local pins; pins="$(grep -E '^(fastapi|uvicorn|httpx)==' "$REPO/agents/requirements.txt" | tr '\n' ' ')"
  # shellcheck disable=SC2086
  "$BV_HOME/app-venv/bin/pip" install -q $pins python-multipart
  write_model_paths
  say "installed in $BV_HOME"
  local missing=0 f
  for f in "SulphurAI-Sulphur-3/quantized/s3_w4a8.safetensors" \
           "Comfy-Org-MiniMax-H3/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" \
           "unsloth-MiniMax-H3-GGUF/vae/minimax_h3_video_vae_fp16.safetensors" \
           "unsloth-MiniMax-H3-GGUF/vae/minimax_h3_audio_vae_fp32.safetensors"; do
    [[ -f "$WEIGHTS/$f" ]] || { say "  ! missing: $WEIGHTS/$f"; missing=1; }
  done
  if (( missing )); then
    say "  get the text encoder with:"
    say "    scripts/weights-download.sh --include 'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors' \\"
    say "      https://huggingface.co/Comfy-Org/MiniMax-H3 $WEIGHTS/Comfy-Org-MiniMax-H3"
  fi
}

# Anything else computing on the card: the video model needs nearly all of it.
gpu_holders() {
  command -v nvidia-smi >/dev/null || return 0
  local mine=""; alive "$RUN/engine.pid" && mine="$(cat "$RUN/engine.pid")"
  nvidia-smi --query-compute-apps=pid,used_memory,process_name --format=csv,noheader,nounits 2>/dev/null \
    | awk -F', *' -v mine="$mine" '$1 != mine && $2 + 0 >= 2000 { printf "pid %s, %s MiB, %s\n", $1, $2, $3 }'
}

wait_http() {  # url seconds
  local i; for (( i = 0; i < $2; i++ )); do curl -fsS -m 2 -o /dev/null "$1" 2>/dev/null && return 0; sleep 1; done; return 1
}

cmd_up() {
  [[ -x "$BV_HOME/venv/bin/python" && -d "$BV_HOME/ComfyUI" && -x "$BV_HOME/app-venv/bin/python" ]] \
    || die "not installed yet: scripts/video.sh install"
  mkdir -p "$RUN" "$BEAST_VIDEO_DIR/engine-output" "$BEAST_VIDEO_DIR/engine-input" "$BEAST_VIDEO_DIR/data"
  write_model_paths      # every time: the file is ours, and the folder list grows
  if ! alive "$RUN/engine.pid"; then
    local held; held="$(gpu_holders)"
    if [[ -n "$held" ]]; then
      say "the GPU is in use:"; say "$held" | sed 's/^/  /'
      die "stop that first (an LLM: ./stop.sh). The video model needs the whole card."
    fi
    # `cd; cmd &`, never `cd && cmd &`: the second backgrounds a subshell, so
    # $! is that shell (not the engine) and it keeps our stdout open.
    ( cd "$BV_HOME/ComfyUI" || exit 1
      setsid nohup "$BV_HOME/venv/bin/python" main.py \
        --listen 127.0.0.1 --port "$ENGINE_PORT" --disable-auto-launch \
        --extra-model-paths-config "$BV_HOME/extra_model_paths.yaml" \
        --output-directory "$BEAST_VIDEO_DIR/engine-output" \
        --input-directory "$BEAST_VIDEO_DIR/engine-input" \
        > "$RUN/engine.log" 2>&1 < /dev/null & echo $! > "$RUN/engine.pid" )
    say "engine starting (pid $(cat "$RUN/engine.pid"))…"
    wait_http "http://127.0.0.1:$ENGINE_PORT/system_stats" 180 \
      || die "the engine did not answer within 3 minutes: tail $RUN/engine.log"
  fi
  say "engine up on 127.0.0.1:$ENGINE_PORT"
  if ! alive "$RUN/server.pid"; then
    ( cd "$REPO" || exit 1
      # The engine folders first: these assignments run left to right, and
      # BEAST_VIDEO_DIR means the data folder from the next line on.
      BEAST_VIDEO_ENGINE_INPUT="$BEAST_VIDEO_DIR/engine-input" \
        BEAST_VIDEO_ENGINE_OUTPUT="$BEAST_VIDEO_DIR/engine-output" \
        BEAST_VIDEO_DIR="$BEAST_VIDEO_DIR/data" BEAST_VIDEO_PORT="$PORT" \
        BEAST_VIDEO_COMFY="http://127.0.0.1:$ENGINE_PORT" OPENBEAST_BIND="$BIND" \
        setsid nohup "$BV_HOME/app-venv/bin/python" "$REPO/agents/video_server.py" \
        > "$RUN/server.log" 2>&1 < /dev/null & echo $! > "$RUN/server.pid" )
    printf '%s\n' "$BIND" > "$RUN/server.bind"
    wait_http "http://$BIND:$PORT/healthz" 30 || die "beast-video did not start: tail $RUN/server.log"
  fi
  say "beast-video up on $(cat "$RUN/server.bind" 2>/dev/null || echo "$BIND"):$PORT (clips in $BEAST_VIDEO_DIR/data)"
  ts_url
}

stop_one() {  # pidfile name
  if alive "$1"; then
    local pid; pid="$(cat "$1")"
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    local i; for (( i = 0; i < 20; i++ )); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && { kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true; }
    say "stopped $2"
  fi
  rm -f "$1"
}

cmd_down() {
  stop_one "$RUN/server.pid" "beast-video"
  rm -f "$RUN/server.bind"
  stop_one "$RUN/engine.pid" "the engine (the card is free)"
}

ts_bin() { command -v tailscale 2>/dev/null || true; }
ts_url() {
  local ts; ts="$(ts_bin)"; [[ -n "$ts" ]] || return 0
  local bound; bound="$(cat "$RUN/server.bind" 2>/dev/null || true)"
  if [[ -n "$bound" && "$bound" != 127.* && "$bound" != "localhost" ]]; then
    local h; h="$("$ts" status --json 2>/dev/null | sed -n 's/.*"DNSName": *"\([^"]*\)\.".*/\1/p' | head -1)"
    say "open it from your tailnet: http://${h:-$bound}:$PORT/"
    return 0
  fi
  if "$ts" serve status 2>/dev/null | grep -q ":$TS_PORT "; then
    local host; host="$("$ts" status --json 2>/dev/null | sed -n 's/.*"DNSName": *"\([^"]*\)\.".*/\1/p' | head -1)"
    say "open it from your tailnet: https://${host:-<this-rig>}:$TS_PORT/"
  else
    say "not published yet: scripts/video.sh publish"
  fi
}

cmd_publish() {
  local ts; ts="$(ts_bin)"; [[ -n "$ts" ]] || die "tailscale is not installed"
  # Tailnet only (serve, never funnel), and only the service: ComfyUI stays on loopback.
  "$ts" serve --bg --https="$TS_PORT" "http://127.0.0.1:$PORT" >/dev/null \
    || die "tailscale serve refused (try: sudo tailscale set --operator=\$USER, then again)"
  ts_url
}

cmd_unpublish() {
  local ts; ts="$(ts_bin)"; [[ -n "$ts" ]] || die "tailscale is not installed"
  "$ts" serve --https="$TS_PORT" off >/dev/null 2>&1 || true
  say "unpublished :$TS_PORT"
}

cmd_status() {
  if alive "$RUN/engine.pid"; then say "engine      up   (pid $(cat "$RUN/engine.pid"), 127.0.0.1:$ENGINE_PORT)"; else say "engine      down"; fi
  if alive "$RUN/server.pid"; then say "beast-video up   (pid $(cat "$RUN/server.pid"), $(cat "$RUN/server.bind" 2>/dev/null || echo 127.0.0.1):$PORT)"; else say "beast-video down"; fi
  command -v nvidia-smi >/dev/null && say "card        $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader | head -1) used"
  say "clips       $BEAST_VIDEO_DIR/data/jobs ($(find "$BEAST_VIDEO_DIR/data/jobs" -name clip.mp4 2>/dev/null | wc -l | tr -d ' ') stored)"
  ts_url
}

case "${1:-status}" in
  install)   cmd_install ;;
  up)        cmd_up ;;
  down)      cmd_down ;;
  status)    cmd_status ;;
  publish)   cmd_publish ;;
  unpublish) cmd_unpublish ;;
  logs)      exec tail -n 40 -F "$RUN/engine.log" "$RUN/server.log" ;;
  -h|--help|help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command: $1 (install|up|down|status|publish|unpublish|logs)" ;;
esac
