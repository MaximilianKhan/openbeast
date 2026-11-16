#!/usr/bin/env bash
# WRONG (no commit): correct fix left in the working tree.
set -eu
H=$(cd "$(dirname "$0")" && pwd)
python3 "$H/_fix.py" numcmp
bash tests/run.sh >/dev/null
