#!/bin/bash
# Build the Basalt pack of SC117's abliterated Inkling 2 Mini, offline: the 20 edited attention-output tensors moved from
# SC117's Strata SQ-20 into Thinking Machines' release, then Basalt's own converter. Reproduces
# Inkling-2-Mini-SC117-abliterated-NVFP4-MX8.basalt (sha256 6eaa50d3..., scripts/weights.registry) byte for byte.
#
#   scripts/engines/inkling-sc117-pack.sh OFFICIAL_DIR OFFICIAL_SQ20 SC117_SQ20 BASALT_TREE WORK_DIR OUT_DIR [STOCK_PACK]
#
# OFFICIAL_DIR   thinkingmachines/Inkling-2-Mini @ b85c3cfa615fc413cc9af3379f491b963f3b586a (hf download ... --revision;
#                offline: from the ark copy). Weights identical to main c9d02830 (same LFS sha256); b85c3cfa is the
#                revision jesdga converted (the stock pack's meta.checkpoint.dir).
# OFFICIAL_SQ20  strata-engine/Inkling-2-Mini-Strata @ 04996a1e, Inkling-2-Mini-SQ-20.strata
# SC117_SQ20     SC117/Inkling-2-Mini-abliterated-Strata @ ae324ac3676b97a3f67b475ffc2534c7521ff7c4,
#                Inkling-2-Mini-SQ-20-abliterated.strata
# BASALT_TREE    the Basalt v0.5.7 tree (400b411c; tools/convert is the same with or without the #632 patch, which
#                touches serve/ only), e.g. ~/openbeast-runs/inkling-perf-2026-11-23/build/basalt-tree
# WORK_DIR       new directory for the extract and the patched checkpoint (~20 GB)
# OUT_DIR        where the pack goes
# STOCK_PACK     optional: the stock pack (28ec7072...) to diff against (only the 20 tensors + meta.checkpoint.dir differ)
#
# Every input is hashed against the pins below first. Nothing from the packs or the checkpoint is executed: the
# transplant is our stdlib script (python3 -I), the converter is Basalt's, run in the sandbox (no network, read-only
# root and inputs, no capabilities, uid 1000) in image ob-strata-env:2026-11-23 (OB_CONVERT_IMAGE to override; its
# python3 + numpy are all the converter needs). The checkpoint is mounted as /src/Inkling-2-Mini-b85c3cfa-SC117-ae324ac3
# and no --name is passed: Basalt writes the directory name into meta.checkpoint.dir, so both are part of the pin.
# Procedure and evidence: docs/reports/V5_INKLING2_MINI_SC117-2026-11-25.md section 2.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OFF=${1:?usage: see the header}; OSQ=${2:?}; SSQ=${3:?}; TREE=${4:?}; WORK=${5:?}; OUT=${6:?}; STOCK=${7:-}
IMAGE=${OB_CONVERT_IMAGE:-ob-strata-env:2026-11-23}
DIRNAME=Inkling-2-Mini-b85c3cfa-SC117-ae324ac3
NAME=Inkling-2-Mini-SC117-abliterated-NVFP4-MX8.basalt
WANT_PACK=6eaa50d3620a8f3913c726b239b7981880e440f7d9c2dffe0613148b01c6e89b
WANT_BLOB=84361020fdd8715a675ffb9a0d96a53d9b7e2c02507590e53a11454020783a78
WANT_SHARD=bd8132f97ce453765b1a83cfa7aac865c4f89f8c57f24601663d97a946083cf7
PIN=400b411c58031d064812ea21a259b2381acdaa3e
die() { echo "inkling-sc117-pack: $*" >&2; exit 1; }
check() {  # check FILE SHA256
  local got; got=$(sha256sum "$1" | cut -d' ' -f1)
  [[ $got == "$2" ]] || die "$1: sha256 $got, want $2"
  echo "  ok $2  $1"
}
[[ -e $WORK ]] && die "$WORK exists; pick a new directory"
echo "inputs:"
check "$OFF/model-00001-of-00001.safetensors" 65e60f51aa1284d4fb022584908c315270334d991828bb774af60584523539d1
check "$OFF/mtp.safetensors" 7c3b651317f01f73bcf4e1dceba66ef8b7e1a1eac6bc171e882e690a4b10a1d2
check "$OFF/config.json" a7a349601f0984c4abf999c480ef775d8567d21f5f84b4e8a3e035b44f288dc2
check "$OSQ" 0bbfd52344f0dd4779a7807b5f73554a812f24c035e6877b63cd0d499f7e1b97
check "$SSQ" 4633e0420168ebf0913b6d834397c297e330885bcd45f1501462ed350c50a0a2
[[ $(git -C "$TREE" rev-parse HEAD) == "$PIN" ]] || die "$TREE is not Basalt v0.5.7 ($PIN)"
[[ -z $(git -C "$TREE" status --porcelain --untracked-files=no -- tools) ]] || die "$TREE/tools has changes"
command -v zstd >/dev/null || die "zstd is needed to read a Strata manifest"
mkdir -p "$WORK" "$OUT"
T="python3 -I $HERE/inkling-sc117-transplant.py"
$T extract --official-sq20 "$OSQ" --sc117-sq20 "$SSQ" --checkpoint "$OFF" --out "$WORK/extract"
check "$WORK/extract/wo_ud-sc117-L04-L23.bin" $WANT_BLOB
$T patch --checkpoint "$OFF" --extract "$WORK/extract" --out "$WORK/$DIRNAME"
$T verify-ck --official "$OFF" --patched "$WORK/$DIRNAME" --extract "$WORK/extract"
check "$WORK/$DIRNAME/model-00001-of-00001.safetensors" $WANT_SHARD
mkdir -p "$WORK/convtmp"
docker run --rm --network none --read-only --tmpfs /tmp:rw,size=4g --user 1000:1000 --cap-drop ALL \
  --security-opt no-new-privileges -e HOME=/tmp -e TMPDIR=/work/tmp \
  -v "$TREE":/opt/basalt:ro -v "$WORK/$DIRNAME":/src/$DIRNAME:ro -v "$OUT":/out -v "$WORK/convtmp":/work/tmp \
  -w /opt/basalt "$IMAGE" python3 -I tools/convert/convert.py --checkpoint /src/$DIRNAME --out /out/$NAME
check "$OUT/$NAME" $WANT_PACK
if [[ -n $STOCK ]]; then
  check "$STOCK" 28ec70723feaa037942a02f55b9d5e898276f3e62a3b1fa083745f932a041b99
  $T diff-pack --a "$STOCK" --b "$OUT/$NAME" \
    --expect-tensors "$(seq -s, -f 'blk.%g.attn_output.weight' 4 23)"
fi
echo "OK: $OUT/$NAME"
