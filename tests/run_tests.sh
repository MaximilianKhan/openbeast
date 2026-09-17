#!/bin/bash
# Run the full test suite.
#
# Usage: ./tests/run_tests.sh
#
# Tests run without a GPU or llama.cpp server — they validate scripts,
# path references, tool implementations, and Python compilation.

set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "========================================"
echo " OpenBeast — Test Suite"
echo "========================================"
echo ""

OVERALL=0

# --- Script structure tests ---
echo "--- Script structure tests ---"
echo ""
if bash "$REPO_DIR/tests/test_scripts.sh"; then
  echo ""
  echo "Script tests: ALL PASSED"
else
  echo ""
  echo "Script tests: SOME FAILED"
  OVERALL=1
fi

echo ""
echo ""

# --- Per-device enrollment CLI tests ---
echo "--- Client enrollment tests (scripts/clients.sh) ---"
echo ""
if bash "$REPO_DIR/tests/test_clients.sh"; then
  echo ""
  echo "Client enrollment tests: ALL PASSED"
else
  echo ""
  echo "Client enrollment tests: SOME FAILED"
  OVERALL=1
fi

if bash "$REPO_DIR/tests/test_job_sh.sh"; then
  echo ""
  echo "Job session tests: ALL PASSED"
else
  echo ""
  echo "Job session tests: SOME FAILED"
  OVERALL=1
fi

echo ""
echo ""

# --- beast-artifact CLI tests ---
echo "--- Artifact CLI tests (scripts/artifact.sh) ---"
echo ""
if bash "$REPO_DIR/tests/test_artifact_cli.sh"; then
  echo ""
  echo "Artifact CLI tests: ALL PASSED"
else
  echo ""
  echo "Artifact CLI tests: SOME FAILED"
  OVERALL=1
fi

echo ""
echo ""

# --- Offline / bundle / fetch-weight fixes (2026-09-17 review) ---
echo "--- Offline + bundle + fetch-weight tests (stubbed hf/pip/docker/git) ---"
echo ""
if bash "$REPO_DIR/tests/test_offline_fixes.sh"; then
  echo ""
  echo "Offline fixes tests: ALL PASSED"
else
  echo ""
  echo "Offline fixes tests: SOME FAILED"
  OVERALL=1
fi

echo ""
echo ""

# --- Drive wear tracking tests ---
echo "--- SSD/NVMe wear tests (scripts/ssd-wear.sh) ---"
echo ""
if bash "$REPO_DIR/tests/test_ssd_wear.sh"; then
  echo ""
  echo "SSD wear tests: ALL PASSED"
else
  echo ""
  echo "SSD wear tests: SOME FAILED"
  OVERALL=1
fi

echo ""
echo ""

# --- Python tool tests ---
echo "--- Python tool tests ---"
echo ""
export OPENBEAST_SKIP_NETWORK_TESTS="${OPENBEAST_SKIP_NETWORK_TESTS:-1}"  # network tests opt-in (httpbin flakiness)
if python3 -c "import pytest" 2>/dev/null; then
  if python3 -m pytest "$REPO_DIR/tests/test_tools.py" -v --tb=short; then
    echo ""
    echo "Tool tests: ALL PASSED"
  else
    echo ""
    echo "Tool tests: SOME FAILED"
    OVERALL=1
  fi
else
  # Fallback: run with unittest if pytest not installed
  echo "(pytest not found, falling back to unittest)"
  echo ""
  if python3 -m unittest discover -s "$REPO_DIR/tests" -p "test_*.py" -v; then
    echo ""
    echo "Tool tests: ALL PASSED"
  else
    echo ""
    echo "Tool tests: SOME FAILED"
    OVERALL=1
  fi
fi

# --- Full Python suite -----------------------------------------------------
# The block above runs ONE file. CI runs `pytest tests/ -q` as a separate step,
# so for a long time every suite outside test_tools.py (the artifact, chat,
# session, steering and identity suites — several hundred tests) was green in
# CI and never executed by this script. A local runner that reports "ALL TESTS
# PASSED" while skipping most of the tests is worse than having no runner, so
# it now runs what CI runs.
if python3 -c "import pytest" 2>/dev/null; then
  echo ""
  echo "--- Full Python suite (everything CI runs) ---"
  echo ""
  if python3 -m pytest "$REPO_DIR/tests" -q; then
    echo ""
    echo "Full Python suite: ALL PASSED"
  else
    echo ""
    echo "Full Python suite: SOME FAILED"
    OVERALL=1
  fi
fi

echo ""
echo "========================================"
if [[ $OVERALL -eq 0 ]]; then
  echo " ALL TESTS PASSED"
else
  echo " SOME TESTS FAILED"
fi
echo "========================================"

exit $OVERALL
