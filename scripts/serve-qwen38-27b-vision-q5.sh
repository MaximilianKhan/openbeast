#!/bin/bash
# Serve Qwen3.8-27B UD-Q5_K_XL **with vision** (image + video input) on RTX 5090
#
# Same weight file as serve-qwen38-27b-q5.sh plus a separate vision projector.
# Qwen3.8 is natively multimodal, but the LANGUAGE GGUF holds no vision tower —
# the tower + projector ship as their own file, mmproj-F16.gguf (928 MB), in the
# same unsloth repo. Both are required; --mmproj is what wires them together.
#
# How it works: the projector ("mmproj" = multimodal projector) is a 461M-param
# ViT (27 blocks, 1152-dim, patch 16, spatial_merge 2) whose output is mapped
# into the LLM's 5120-dim embedding space. The chat template emits
# `<|vision_start|><|image_pad|>...<|vision_end|>`; libmtmd replaces those pad
# placeholders with real projected patch embeddings before the forward pass, so
# the LLM sees one uniform embedding sequence and never knows which vectors came
# from pixels. Image cost: patch 16 with 2x2 merge = one token per 32x32 px, so
# a 768x512 image is 384 tokens; 1024x1024 is 1,024 tokens. Budget context
# accordingly — images are not free.
#
# VERIFIED end-to-end 2026-08-14: read back a rendered code string and named
# three shapes + colours in correct left-to-right order from a synthetic test
# image. Projector loads as `qwen3vl_merger` -> PROJECTOR_TYPE_QWEN3VL, which
# our build already implements (tools/mtmd/clip.cpp). No llama.cpp change needed.
#
# Context: 262144 = full native ceiling, unchanged from the text-only script.
# MEASURED 2026-08-14: 27,738 MiB used / 4,869 MiB free. The vision tower costs
# ~979 MiB on top of the text-only config's 26,702 MiB — cheap enough that the
# native context survives intact.
#
# The chat template also renders VIDEO blocks (`<|video_pad|>`); we have not
# tested video input.
#
# For 1.83x decode with vision, use serve-qwen38-27b-vision-mtp-q5.sh (MTP and
# --mmproj DO coexist — measured, see that script).
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-27B-UD-Q5_K_XL.gguf" \
  --mmproj "$WEIGHTS_DIR/mmproj-F16.gguf" \
  -a "Qwen3.8 27B Vision Q5" \
  -c 262144 \
  "$@"
