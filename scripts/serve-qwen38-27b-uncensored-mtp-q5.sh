#!/bin/bash
# Serve Qwen3.8-27B-Uncensored Q5_K_M **with MTP** — THE SHIPPED DEFAULT.
#
# Promoted to default 2026-08-19, replacing serve-heretic-v2-27b-mtp-q5.sh.
# It wins on all three axes at once, which is rare enough to spell out:
#   decode   140.2 tok/s   vs Heretic v2's ~136 tok/s
#   context  262144 native vs Heretic v2's 262144 (tie)
#   VRAM     27.7 GB used  vs Heretic v2's 29.6 GB  -> 4.76 GB free, not 2.97
# That headroom is the real prize. Heretic v2 shipped at 2.97 GB free, which
# is inside the sustained-load crash zone we hit with Gemma 4 31B at 2,080 MiB
# (see docs/MODELS.md). This config leaves nearly 5 GB.
#
# The model: JonathanColetti/Qwen3.8-27B-Uncensored-GGUF — Qwen3.8-27B with the
# refusal direction abliterated out of self_attn.o_proj and mlp.down_proj, then
# imatrix-quantized (496 entries / 200 chunks). Its sha256 matches the published
# HF LFS oid byte-for-byte; pinned in scripts/weights.registry.
#
# THE MTP HEAD IS INSIDE THIS FILE, as with all Qwen3.8 GGUFs:
# `qwen35.nextn_predict_layers = 1`, tensors at `blk.64.nextn.*`, block_count 65
# (64 trunk + 1 nextn). Served WITHOUT --spec-type they load and are ignored
# ("unused tensor" warnings at startup are expected and harmless);
# --spec-type draft-mtp activates them. Same file as
# serve-qwen38-27b-uncensored-q5.sh — this script is purely a different launch
# config. The upstream repo also ships `-noMTP-` twins with the head stripped;
# we deliberately do NOT use those — this file is a strict superset.
#
# TUNED 2026-08-19 by measured sweep at the native 262144 context (greedy,
# temp 0, seed 42, 700-token decode, identical prompt — so the generated tokens
# are byte-identical across every n and this is pure speed):
#   n-max  decode      draft acceptance   mean accepted len   VRAM
#   (off)   69.9 tok/s        --                 --           25,476 MiB
#   1      111.8 tok/s      87.9%              1.88           27,282 MiB
#   2      132.4 tok/s      71.4%              2.43           27,432 MiB
#   4      140.2 tok/s      56.1%              3.24           27,732 MiB  <- PEAK, shipped
#   6      128.3 tok/s      45.0%              3.70           28,030 MiB
#   8      120.9 tok/s      31.9%              3.54           28,330 MiB
#   10     112.1 tok/s      25.5%              3.54           28,629 MiB
# So n4 = 2.00x the no-MTP baseline. Re-profile with
# ./scripts/profile-qwen38-uncensored-mtp.sh on different hardware.
#
# The peak sits at n4, exactly where stock Qwen3.8-27B peaks (123.7 tok/s, 48%
# acc) and NOT at Qwen3.6-27B's n8 — abliteration moved the acceptance curve UP
# (56% vs 48% at n4, and 140 vs 124 tok/s) but did not move the optimum. Worth
# recording because the reverse was a live hypothesis: the nextn head drafts
# out of the residual stream that abliteration edits, so it could plausibly
# have degraded. It did not. Do not copy the Qwen3.6 n8 config here.
# p-min does NOT affect output quality (the target model verifies every draft).
#
# **CRITICAL CONSTRAINT (upstream):** `-np 1` is forced — MTP does not support
# more than one parallel slot. Concurrent requests serialize. This is the
# deliberate trade for 2x decode; switch to serve-qwen38-27b-uncensored-q5.sh
# for the 6 slots back at half the tokens/s.
#
# MTP rules (same as every MTP build we ship): keep temperature <= 1.0 and
# repetition_penalty = 1.0. Acceptance here is 56%, comfortably above the ~50%
# floor below which a non-MTP quant is the better choice.
#
# Endpoint: http://localhost:8080/v1/chat/completions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/lib/weights.sh"
exec "$SCRIPT_DIR/serve.sh" \
  -m "$WEIGHTS_DIR/Qwen3.8-27B-Uncensored-Q5_K_M.gguf" \
  -a "Qwen3.8 27B Uncensored MTP Q5" \
  -c 262144 \
  -np 1 \
  --spec-type draft-mtp \
  --spec-draft-n-max 4 \
  --spec-draft-p-min 0.0 \
  "$@"
