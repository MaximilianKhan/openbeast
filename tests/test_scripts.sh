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
  serve-qwen-27b-q5.sh
  serve-qwen-27b-uncensored-q5.sh serve-qwen-35b-a3b.sh
  serve-qwen-35b-a3b-uncensored-q4.sh
  serve-qwen-27b-mtp-q5.sh serve-qwen-35b-a3b-mtp.sh
  serve-qwopus-27b-v2-q5.sh serve-qwopus-27b-v2-mtp-q5.sh
  serve-gemma-4-31b-q5.sh
  run-qwen-27b-q5.sh
  run-qwen-27b-uncensored-q5.sh run-qwen-35b-a3b.sh
  run-qwen-35b-a3b-uncensored-q4.sh
  run-qwen-27b-mtp-q5.sh run-qwen-35b-a3b-mtp.sh
  run-qwopus-27b-v2-q5.sh run-qwopus-27b-v2-mtp-q5.sh
  run-gemma-4-31b-q5.sh
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

# Model scripts should resolve weights via the WEIGHTS_DIR helper, not a
# hardcoded in-repo path — this keeps weights relocatable (NVMe/USB/NAS).
for script in "$REPO_DIR"/scripts/serve-*.sh "$REPO_DIR"/scripts/run-*.sh; do
  name=$(basename "$script")
  # Skip the generic launchers — they take -m from the caller.
  [[ "$name" == "serve.sh" || "$name" == "run.sh" ]] && continue
  if grep -q 'lib/weights.sh' "$script" && grep -q 'WEIGHTS_DIR/' "$script"; then
    pass "$name resolves weights via WEIGHTS_DIR"
  else
    fail "$name doesn't use the WEIGHTS_DIR resolver"
  fi
done

# The resolver must not hardcode an in-repo weights path in launch scripts.
for script in "$REPO_DIR"/scripts/serve-*.sh "$REPO_DIR"/scripts/run-*.sh; do
  name=$(basename "$script")
  if grep -q 'REPO_DIR/weights/' "$script"; then
    fail "$name still hardcodes \$REPO_DIR/weights/"
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
for pyfile in agents/runner.py agents/tools.py agents/mcp_server.py \
              evals/run_eval.py evals/scoring.py evals/benchmark_all.py; do
  if python3 -c "import py_compile; py_compile.compile('$REPO_DIR/$pyfile', doraise=True)" 2>/dev/null; then
    pass "$pyfile compiles"
  else
    fail "$pyfile has syntax errors"
  fi
done

# --- 6b. Eval task JSONs valid + validation scripts compile ---
echo ""
echo "Eval task validation:"
TASK_CHECK=$(python3 - <<'PY'
import json, ast, os, subprocess
tasks_dir = os.path.join(os.environ.get('REPO_DIR', '.'), 'evals', 'tasks')
errors = []
files = sorted(f for f in os.listdir(tasks_dir) if f.endswith('.json'))

def check_unit(label, unit):
    """A 'unit' is a legacy top-level task or a single variant — both must have task + validation + valid scripts."""
    for k in ('task', 'validation'):
        if k not in unit:
            errors.append(f'{label}: missing {k}')
    setup = unit.get('setup', '')
    if setup:
        r = subprocess.run(['bash', '-n', '-c', setup], capture_output=True, text=True)
        if r.returncode != 0: errors.append(f'{label}: setup bash {r.stderr.strip()[:80]}')
    script = unit.get('validation', {}).get('script', '')
    vtype = unit.get('validation', {}).get('type', 'bash')
    if vtype == 'python':
        try: ast.parse(script)
        except SyntaxError as e: errors.append(f'{label}: validation py {e}')
    elif vtype == 'bash':
        r = subprocess.run(['bash', '-n', '-c', script], capture_output=True, text=True)
        if r.returncode != 0: errors.append(f'{label}: validation bash {r.stderr.strip()[:80]}')

for fn in files:
    path = os.path.join(tasks_dir, fn)
    try:
        data = json.load(open(path))
    except Exception as e:
        errors.append(f'{fn}: JSON {e}'); continue
    for k in ('id', 'name', 'difficulty'):
        if k not in data: errors.append(f'{fn}: missing {k}')
    if 'variants' in data:
        for v in data['variants']:
            vid = v.get('id', '?')
            check_unit(f'{fn}[{vid}]', v)
    else:
        check_unit(fn, data)
print(f'COUNT={len(files)}')
for e in errors: print('ERROR:' + e)
PY
)
TASK_COUNT=$(echo "$TASK_CHECK" | grep '^COUNT=' | cut -d= -f2)
TASK_ERRORS=$(echo "$TASK_CHECK" | grep '^ERROR:' || true)
if [[ -z "$TASK_ERRORS" && "${TASK_COUNT:-0}" -ge 50 ]]; then
  pass "all $TASK_COUNT eval task JSONs valid (≥50 expected)"
else
  fail "eval tasks have problems: ${TASK_ERRORS:-none} (count=${TASK_COUNT:-0})"
fi

# --- 6c. Skills validation ---
echo ""
echo "Skills validation:"
SKILL_CHECK=$(python3 - <<'PY'
import os, sys
skills_dir = os.path.join(os.environ.get('REPO_DIR', '.'), 'skills')
errors = []
count = 0
if not os.path.isdir(skills_dir):
    print('COUNT=0')
else:
    for entry in sorted(os.listdir(skills_dir)):
        skill_path = os.path.join(skills_dir, entry)
        if not os.path.isdir(skill_path) or entry.startswith('_'):
            continue
        md_path = os.path.join(skill_path, 'SKILL.md')
        if not os.path.isfile(md_path):
            errors.append(f'{entry}: no SKILL.md'); continue
        try:
            text = open(md_path).read()
        except Exception as e:
            errors.append(f'{entry}: read failed: {e}'); continue
        if not text.startswith('---'):
            errors.append(f'{entry}: missing frontmatter'); continue
        end = text.find('---', 3)
        if end == -1:
            errors.append(f'{entry}: unterminated frontmatter'); continue
        fm = {}
        for line in text[3:end].strip().split('\n'):
            if ':' in line:
                k, _, v = line.partition(':')
                fm[k.strip()] = v.strip()
        for required in ('name', 'description'):
            if required not in fm:
                errors.append(f'{entry}: missing {required} in frontmatter')
        if fm.get('name') and fm['name'] != entry:
            errors.append(f'{entry}: frontmatter name={fm["name"]!r} does not match dir name')
        body = text[end+3:].strip()
        if len(body) < 50:
            errors.append(f'{entry}: body suspiciously short ({len(body)} chars)')
        count += 1
    print(f'COUNT={count}')
for e in errors: print('ERROR:' + e)
PY
)
SKILL_COUNT=$(echo "$SKILL_CHECK" | grep '^COUNT=' | cut -d= -f2)
SKILL_ERRORS=$(echo "$SKILL_CHECK" | grep '^ERROR:' || true)
if [[ -z "$SKILL_ERRORS" ]]; then
  pass "all $SKILL_COUNT skill SKILL.md files valid"
else
  fail "skill validation: ${SKILL_ERRORS}"
fi

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
