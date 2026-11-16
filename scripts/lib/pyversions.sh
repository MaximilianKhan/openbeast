#!/bin/bash
# scripts/lib/pyversions.sh — installed versions of python packages, in ONE
# interpreter.
#
#   ob_py_versions PKG...   -> one "PKG VERSION" line per argument, in order;
#                              VERSION is empty when PKG is not installed.
#
# doctor.sh and update.sh --python used to run `python3 -m pip show` once per
# pin: each call imports all of pip (~0.4 s on a GB10), so six pins were 2.3 s
# of doctor's 4.3 s on spark-1, and update.sh paid it three times over
# (review 2026-11-16). importlib.metadata reads the same installed metadata
# from the same sys.path as pip show does, in about 40 ms for all of them.
# Uses whatever python3 is on PATH, exactly as the pip calls did. Prints
# nothing (and returns 1) when python3 is missing.

ob_py_versions() {
  (( $# )) || return 0
  command -v python3 >/dev/null 2>&1 || return 1
  python3 - "$@" <<'PY' 2>/dev/null
import sys
from importlib import metadata

for name in sys.argv[1:]:
    try:
        v = metadata.version(name)
    except Exception:  # PackageNotFoundError, or unreadable metadata: "not installed"
        v = ""
    print(name, v or "")
PY
}
