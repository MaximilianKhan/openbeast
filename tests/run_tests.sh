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

# --- Python tool tests ---
echo "--- Python tool tests ---"
echo ""
if python3 -m pytest "$REPO_DIR/tests/test_tools.py" -v --tb=short 2>/dev/null; then
  echo ""
  echo "Tool tests: ALL PASSED"
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

echo ""
echo "========================================"
if [[ $OVERALL -eq 0 ]]; then
  echo " ALL TESTS PASSED"
else
  echo " SOME TESTS FAILED"
fi
echo "========================================"

exit $OVERALL
