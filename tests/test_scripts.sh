#!/bin/bash
# Validate script structure: existence, permissions, path references.
# Runs without a GPU or server — pure filesystem checks.
#
# Usage: ./tests/test_scripts.sh

set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== Script structure tests ==="
echo ""

# --- 1. Entry points exist and are executable ---
echo "Entry points:"
for script in start.sh stop.sh agent.sh; do
  if [[ -x "$REPO_DIR/$script" ]]; then
    pass "$script exists and is executable"
  else
    fail "$script missing or not executable"
  fi
done

# --- 2. All scripts/ exist and are executable ---
echo ""
echo "Scripts directory:"
EXPECTED_SCRIPTS=(
  serve.sh run.sh configure-webui.sh healthcheck.sh
  serve-qwen-27b-q4.sh serve-qwen-27b-q5.sh
  serve-qwen-27b-uncensored-q5.sh serve-qwen-35b-a3b.sh
  run-qwen-27b-q4.sh run-qwen-27b-q5.sh
  run-qwen-27b-uncensored-q5.sh run-qwen-35b-a3b.sh
)
for script in "${EXPECTED_SCRIPTS[@]}"; do
  if [[ -x "$REPO_DIR/scripts/$script" ]]; then
    pass "scripts/$script exists and is executable"
  else
    fail "scripts/$script missing or not executable"
  fi
done

# --- 3. No stale .sh files at repo root (except entry points) ---
echo ""
echo "Root cleanliness:"
STALE=$(find "$REPO_DIR" -maxdepth 1 -name "*.sh" \
  ! -name "start.sh" ! -name "stop.sh" ! -name "agent.sh" -printf "%f\n" 2>/dev/null)
if [[ -z "$STALE" ]]; then
  pass "no stale .sh files at repo root"
else
  fail "unexpected .sh files at root: $STALE"
fi

# --- 4. Path references are correct ---
echo ""
echo "Path references:"

# serve.sh should reference REPO_DIR for llama.cpp
if grep -q 'REPO_DIR.*llama.cpp' "$REPO_DIR/scripts/serve.sh"; then
  pass "serve.sh uses REPO_DIR for llama.cpp path"
else
  fail "serve.sh doesn't use REPO_DIR for llama.cpp"
fi

# run.sh should reference REPO_DIR for llama-cli
if grep -q 'REPO_DIR.*llama.cpp' "$REPO_DIR/scripts/run.sh"; then
  pass "run.sh uses REPO_DIR for llama.cpp path"
else
  fail "run.sh doesn't use REPO_DIR for llama.cpp"
fi

# Model scripts should reference REPO_DIR for weights
for script in "$REPO_DIR"/scripts/serve-qwen-*.sh "$REPO_DIR"/scripts/run-qwen-*.sh; do
  name=$(basename "$script")
  if grep -q 'REPO_DIR.*weights/' "$script"; then
    pass "$name uses REPO_DIR for weights path"
  else
    fail "$name doesn't use REPO_DIR for weights"
  fi
done

# start.sh should reference scripts/ directory
if grep -q 'scripts/' "$REPO_DIR/start.sh"; then
  pass "start.sh references scripts/ directory"
else
  fail "start.sh doesn't reference scripts/"
fi

# configure-webui.sh should reference REPO_DIR for system-prompt
if grep -q 'REPO_DIR.*system-prompt' "$REPO_DIR/scripts/configure-webui.sh"; then
  pass "configure-webui.sh uses REPO_DIR for system-prompt.md"
else
  fail "configure-webui.sh doesn't use REPO_DIR for system-prompt.md"
fi

# --- 5. Agent infrastructure ---
echo ""
echo "Agent infrastructure:"
for file in agents/runner.py agents/tools.py agents/mcp_server.py agents/requirements.txt; do
  if [[ -f "$REPO_DIR/$file" ]]; then
    pass "$file exists"
  else
    fail "$file missing"
  fi
done

# --- 6. Python files compile ---
echo ""
echo "Python compilation:"
for pyfile in agents/runner.py agents/tools.py agents/mcp_server.py; do
  if python3 -c "import py_compile; py_compile.compile('$REPO_DIR/$pyfile', doraise=True)" 2>/dev/null; then
    pass "$pyfile compiles"
  else
    fail "$pyfile has syntax errors"
  fi
done

# --- 7. Config files exist ---
echo ""
echo "Config files:"
for file in opencode.json docker-compose.yml system-prompt.md; do
  if [[ -f "$REPO_DIR/$file" ]]; then
    pass "$file exists"
  else
    fail "$file missing"
  fi
done

# --- Summary ---
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

[[ $FAIL -eq 0 ]]
