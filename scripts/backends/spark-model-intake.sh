#!/bin/bash
# spark-model-intake: offline preflight for a model folder brought to
# spark-hydra (spark-1 + spark-2). Says, before anything stops or copies:
# "drop-in for family X", "needs a profile on engine Y", "same family but not
# a drop-in", "not servable here, because ...", "incomplete" or "not a model".
# docs/EXL3_PLAYBOOK.md "The intake preflight"
#
#   scripts/backends/spark-model-intake.sh ~/Documents/<folder> [more folders]
#   scripts/backends/spark-model-intake.sh <folder> --json         # every field, for scripts
#   scripts/backends/spark-model-intake.sh <folder> --checksums    # also verify SHA256SUMS (reads the weights)
#
# WHY. The pair serves one model at a time on both GPUs, and a swap stops
# what Max is using. Whether a folder can be served at all is a question about
# its metadata: model_type, the quantization fields, the tensor index, sizes.
# Answering it first costs seconds and changes nothing.
#
# WHAT IT READS. config.json, quantization_config.json, the safetensors index,
# each shard's header (via pylib/model_inspect.py), tokenizer and template
# files, SHA256SUMS, and data/spark-families.json: the families we serve (each
# with its recipe, image, unit, served name and reference tensor-index
# signature) and the engines we have (native TensorFold 1.2.7's families and
# formats, read from `tensorfold capabilities --json` on spark-1). It never
# writes, never reads weights (unless --checksums), never starts or stops a
# unit, a container or the GPU.
#
# Exit: 0 when every folder is a DROP-IN that fits, 1 otherwise, 2 on bad usage.
#
# Offline: it reads local files only; a Hub id is refused (model-inspect.sh
# reads the Hub). Runs anywhere with python3 >= 3.8 and a copy of
# scripts/backends/ (the script, pylib/ and data/): on spark-1, on the Mac
# against a drive, or on a Spark with no network.
# Test seams: INTAKE_REGISTRY (registry file), INTAKE_HOME (what ~ in the
# registry expands to). Test: tests/test_spark_model_intake.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 -I "$HERE/pylib/model_intake.py" "$@"
