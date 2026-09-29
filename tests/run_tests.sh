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

# --- BEAST_ESCALATE conf forwarding (review open-prs-5) ---
echo "--- BEAST_ESCALATE conf forwarding (scripts/lib/conf.sh) ---"
echo ""
if bash "$REPO_DIR/tests/test_escalate_conf.sh"; then
  echo ""
  echo "Escalate conf tests: ALL PASSED"
else
  echo ""
  echo "Escalate conf tests: SOME FAILED"
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

# --- The 2026-09-29 review's hermetic shell suites ---
# Each builds its own throwaway rig with stub binaries (no GPU, docker,
# network or stack). test_lifecycle.sh and test_uninstall.sh are not listed:
# test_scripts.sh above already runs both. CI runs these same files.
for _suite in \
  "test_conf_secrets.sh|Conf parsing + secrets-off-argv" \
  "test_gpu_ops.sh|GPU lease / update / ops" \
  "test_supply_chain.sh|Supply-chain (hash-pinned installs, pin parity)" \
  "test_shell_ops.sh|Shell ops (doctor, tailscale, conf, keys)" \
  "test_backends.sh|Inference backends (vLLM / TensorFold, unmanaged)"; do
  _file="${_suite%%|*}"; _label="${_suite#*|}"
  echo "--- $_label tests (tests/$_file) ---"
  echo ""
  if bash "$REPO_DIR/tests/$_file"; then
    echo ""
    echo "$_label tests: ALL PASSED"
  else
    echo ""
    echo "$_label tests: SOME FAILED"
    OVERALL=1
  fi
  echo ""
  echo ""
done

# --- Spark model onboarding (profiles, inspect, fetch, conformance) ---
# Hermetic: a stub Hugging Face Hub and stub OpenAI servers on ephemeral
# loopback ports. Also part of the full pytest run below; listed on its own
# so a failure here is named, as in CI.
if python3 -c "import pytest" 2>/dev/null; then
  echo "--- Model onboarding tests (tests/test_model_{profiles,inspect,fetch}.py, test_conformance.py, test_use_model.py) ---"
  echo ""
  if python3 -m pytest "$REPO_DIR/tests/test_model_profiles.py" "$REPO_DIR/tests/test_model_inspect.py" \
       "$REPO_DIR/tests/test_model_fetch.py" "$REPO_DIR/tests/test_conformance.py" \
       "$REPO_DIR/tests/test_use_model.py" -q; then
    echo ""
    echo "Model onboarding tests: ALL PASSED"
  else
    echo ""
    echo "Model onboarding tests: SOME FAILED"
    OVERALL=1
  fi
  echo ""
  echo ""
fi

# --- Python tool tests ---
echo "--- Python tool tests ---"
echo ""
export OPENBEAST_SKIP_NETWORK_TESTS="${OPENBEAST_SKIP_NETWORK_TESTS:-1}"  # network tests opt-in (httpbin flakiness)
# The identity tool server appends to .run/tool-audit.jsonl by default. The
# pytest suites point it at their own tmp dirs, but the unittest fallback
# below imports the same modules with nothing overriding it — and fixture
# identities (alice, bob, '../../etc') landed in the REAL rig's audit trail.
# Aim it at a throwaway file for the whole run unless the caller chose one.
if [[ -z "${OPENBEAST_TOOL_AUDIT_PATH:-}" ]]; then
  _AUDIT_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ob-tests-audit-XXXXXX")"
  trap 'rm -rf "$_AUDIT_TMP"' EXIT
  export OPENBEAST_TOOL_AUDIT_PATH="$_AUDIT_TMP/tool-audit.jsonl"
fi
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
