#!/bin/bash
# End-to-end smoke test for the full stack.
#
# Prerequisites: ./start.sh must be running (llama.cpp + MCPO + Open WebUI + SearXNG)
#
# Tests:
#   1. llama.cpp health + model loaded
#   2. Parallel slots active
#   3. MCPO proxy serving OpenAPI docs
#   4. Open WebUI responding
#   5. SearXNG responding
#   6. Chat completion (model generates a response)
#   7. Tool use via chat API (model calls a tool)
#   8. MCP tool invocation via MCPO (direct tool call)
#
# Usage: ./tests/test_smoke.sh

set -euo pipefail

LLAMA_URL="${LLAMA_URL:-http://localhost:8080}"
MCPO_URL="${MCPO_URL:-http://localhost:3001}"
WEBUI_URL="${WEBUI_URL:-http://localhost:3000}"
SEARXNG_URL="${SEARXNG_URL:-http://localhost:8888}"

PASS=0
FAIL=0
SKIP=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
skip() { echo "  SKIP: $1"; SKIP=$((SKIP + 1)); }

echo "========================================"
echo " Smoke Test — Full Stack"
echo "========================================"
echo ""

# --- 1. llama.cpp health ---
echo "1. llama.cpp server"
if curl -s --max-time 5 "$LLAMA_URL/health" | grep -q "ok"; then
  pass "llama.cpp health check"
else
  fail "llama.cpp not responding at $LLAMA_URL/health"
fi

# --- 2. Parallel slots ---
echo ""
echo "2. Parallel slots"
SLOTS_JSON=$(curl -s --max-time 5 "$LLAMA_URL/slots" 2>/dev/null || echo "[]")
SLOT_COUNT=$(echo "$SLOTS_JSON" | python3 -c "import sys,json; data=json.load(sys.stdin); print(len(data) if isinstance(data,list) else 0)" 2>/dev/null || echo "0")
if [[ "$SLOT_COUNT" -gt 1 ]]; then
  pass "parallel slots active ($SLOT_COUNT slots)"
else
  fail "expected multiple slots, got $SLOT_COUNT"
fi

# --- 3. MCPO proxy ---
echo ""
echo "3. MCPO proxy"
if curl -s --max-time 5 "$MCPO_URL/openapi.json" | grep -q "openapi"; then
  pass "MCPO serving OpenAPI docs"
  # Check that new tools are registered
  MCPO_TOOLS=$(curl -s --max-time 5 "$MCPO_URL/openapi.json" 2>/dev/null)
  for tool in edit_file fetch web_search start_agent check_agent tail_agent; do
    if echo "$MCPO_TOOLS" | grep -q "$tool"; then
      pass "MCPO exposes $tool"
    else
      fail "MCPO missing tool: $tool"
    fi
  done
else
  fail "MCPO not responding at $MCPO_URL"
  for tool in edit_file fetch web_search start_agent check_agent tail_agent; do
    skip "MCPO tool check: $tool (MCPO down)"
  done
fi

# --- 4. Open WebUI ---
echo ""
echo "4. Open WebUI"
if curl -s --max-time 5 "$WEBUI_URL/api/version" | grep -q "version"; then
  pass "Open WebUI responding"
else
  fail "Open WebUI not responding at $WEBUI_URL"
fi

# --- 5. SearXNG ---
echo ""
echo "5. SearXNG"
if curl -s --max-time 5 "$SEARXNG_URL" | grep -qi "searx"; then
  pass "SearXNG responding"
else
  fail "SearXNG not responding at $SEARXNG_URL (run: docker compose up -d searxng)"
fi

# --- 6. Chat completion ---
echo ""
echo "6. Chat completion"
CHAT_RESPONSE=$(curl -s --max-time 60 "$LLAMA_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test",
    "messages": [{"role": "user", "content": "Reply with exactly: SMOKE_TEST_OK"}],
    "max_tokens": 50,
    "temperature": 0
  }' 2>/dev/null)

if echo "$CHAT_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
content = data['choices'][0]['message']['content']
print(content[:100])
sys.exit(0 if content.strip() else 1)
" 2>/dev/null; then
  pass "model generates a response"
else
  fail "chat completion failed or returned empty"
fi

# --- 7. Tool use via chat API ---
echo ""
echo "7. Tool use (function calling)"
TOOL_RESPONSE=$(curl -s --max-time 120 "$LLAMA_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test",
    "messages": [{"role": "user", "content": "What files are in the current directory? Use the list_files tool to check."}],
    "tools": [{
      "type": "function",
      "function": {
        "name": "list_files",
        "description": "List files in a directory",
        "parameters": {
          "type": "object",
          "properties": {
            "directory": {"type": "string", "default": "."}
          }
        }
      }
    }],
    "max_tokens": 200,
    "temperature": 0
  }' 2>/dev/null)

if echo "$TOOL_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
msg = data['choices'][0]['message']
has_tool_calls = 'tool_calls' in msg and len(msg['tool_calls']) > 0
print('tool_calls' if has_tool_calls else 'no_tool_calls')
sys.exit(0 if has_tool_calls else 1)
" 2>/dev/null; then
  pass "model produces tool_calls (native function calling works)"
else
  fail "model did not produce tool_calls — check function calling mode"
fi

# --- 8. Direct MCPO tool call ---
echo ""
echo "8. MCPO direct tool invocation"
MCPO_RESULT=$(curl -s --max-time 10 "$MCPO_URL/list_files" \
  -H "Content-Type: application/json" \
  -d '{"directory": "/tmp", "pattern": "*"}' 2>/dev/null)
if [[ -n "$MCPO_RESULT" ]] && ! echo "$MCPO_RESULT" | grep -qi "error"; then
  pass "MCPO tool invocation works (list_files)"
else
  fail "MCPO tool invocation failed"
fi

# --- Summary ---
echo ""
echo "========================================"
TOTAL=$((PASS + FAIL + SKIP))
echo " Results: $PASS passed, $FAIL failed, $SKIP skipped (of $TOTAL)"
if [[ $FAIL -eq 0 ]]; then
  echo " STACK IS HEALTHY"
else
  echo " ISSUES DETECTED — review failures above"
fi
echo "========================================"

[[ $FAIL -eq 0 ]]
