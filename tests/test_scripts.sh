#!/bin/bash
# Validate script structure: existence, permissions, path references.
# Runs without a GPU or server — pure filesystem checks.
#
# Usage: ./tests/test_scripts.sh

set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export REPO_DIR  # the embedded Python heredocs read it from the environment

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== Script structure tests ==="
echo ""

# --- 1. Entry points exist and are executable ---
echo "Entry points:"
for script in bootstrap.sh start.sh stop.sh agent.sh; do
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
  serve.sh run.sh configure-webui.sh healthcheck.sh setup-tailscale.sh
  update.sh doctor.sh setup-mcpo-keys.sh
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
  ! -name "bootstrap.sh" ! -name "start.sh" ! -name "stop.sh" ! -name "agent.sh" -printf "%f\n" 2>/dev/null)
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

# Bind-surface hardening (Tailscale rollout 2026-07-07): services must take
# their listen address from the BIND_HOST resolver (lib/conf.sh), never a
# hardcoded 0.0.0.0 — the tailnet proxy is the only intended way in.
if grep -q 'lib/conf.sh' "$REPO_DIR/scripts/serve.sh" \
   && grep -q 'HOST="\$BIND_HOST"' "$REPO_DIR/scripts/serve.sh"; then
  pass "serve.sh takes its bind address from lib/conf.sh"
else
  fail "serve.sh doesn't resolve BIND_HOST via lib/conf.sh"
fi
for f in scripts/serve.sh scripts/healthcheck.sh start.sh docker-compose.yml; do
  if grep -q '0\.0\.0\.0' "$REPO_DIR/$f"; then
    fail "$f hardcodes 0.0.0.0 (use BIND_HOST / OPENBEAST_BIND)"
  else
    pass "$f has no hardcoded 0.0.0.0"
  fi
done

# Lifecycle: daemon mode + graceful stop (pidfile-based)
echo ""
echo "Lifecycle:"
if grep -q -- '--daemon' "$REPO_DIR/start.sh" && grep -q -- '--status' "$REPO_DIR/start.sh"; then
  pass "start.sh supports --daemon and --status"
else
  fail "start.sh missing --daemon/--status support"
fi
if grep -q 'doctor)' "$REPO_DIR/start.sh"; then
  pass "start.sh dispatches the doctor subcommand"
else
  fail "start.sh missing 'doctor' subcommand"
fi
# doctor runs to completion and prints its verdict (WARN/FAIL allowed when
# nothing is up — we only assert it doesn't crash and reaches the summary).
if bash "$REPO_DIR/scripts/doctor.sh" --quiet 2>&1 | grep -q '^doctor: '; then
  pass "doctor.sh runs and reports a verdict"
else
  fail "doctor.sh did not reach its summary line"
fi
if grep -q 'supervisor.pid' "$REPO_DIR/start.sh" && grep -q 'supervisor.pid' "$REPO_DIR/stop.sh"; then
  pass "start.sh and stop.sh share the supervisor pidfile"
else
  fail "supervisor pidfile not wired through start.sh + stop.sh"
fi
if grep -q 'MemoryMax' "$REPO_DIR/start.sh"; then
  pass "daemon mode uses a memory-capped scope (OOM containment)"
else
  fail "start.sh daemon mode has no memory cap"
fi
if grep -q '^\.run/' "$REPO_DIR/.gitignore"; then
  pass ".run/ is gitignored"
else
  fail ".run/ missing from .gitignore"
fi

# Skill index in the tools prompt must be present and fresh
echo ""
echo "Skill index:"
if grep -q "SKILL_INDEX_START" "$REPO_DIR/system-prompt-tools.md"; then
  pass "system-prompt-tools.md has the generated skill index markers"
else
  fail "system-prompt-tools.md missing SKILL_INDEX markers"
fi
if python3 "$REPO_DIR/scripts/generate-skill-index.py" --check >/dev/null 2>&1; then
  pass "skill index is fresh (matches skills/*/SKILL.md)"
else
  fail "skill index STALE — run scripts/generate-skill-index.py"
fi

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
for file in agents/runner.py agents/tools.py agents/mcp_server.py agents/router.py agents/requirements.txt; do
  if [[ -f "$REPO_DIR/$file" ]]; then
    pass "$file exists"
  else
    fail "$file missing"
  fi
done

# --- 6. Python files compile ---
echo ""
echo "Python compilation:"
for pyfile in agents/runner.py agents/tools.py agents/mcp_server.py agents/router.py \
              evals/run_eval.py evals/scoring.py evals/benchmark_all.py \
              evals/cache.py evals/tool_efficiency.py; do
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

# --- 8. GPU backend plumbing (hardware profiles Phase 1, 2026-07-07) ---
# bootstrap.sh and update.sh must build llama.cpp through the SAME shared
# lib functions — that's the no-drift guarantee of docs/HARDWARE_PROFILES.md.
echo ""
echo "GPU backend plumbing:"
for fn in ob_resolve_backend ob_cmake_flags ob_backend_preflight; do
  if grep -q "^${fn}()" "$REPO_DIR/scripts/lib/hardware.sh"; then
    pass "scripts/lib/hardware.sh defines $fn"
  else
    fail "scripts/lib/hardware.sh missing $fn"
  fi
done
for f in bootstrap.sh scripts/update.sh; do
  if grep -q 'ob_cmake_flags' "$REPO_DIR/$f" && grep -q 'ob_resolve_backend' "$REPO_DIR/$f"; then
    pass "$f builds via the shared backend lib (ob_resolve_backend + ob_cmake_flags)"
  else
    fail "$f doesn't build via ob_resolve_backend/ob_cmake_flags"
  fi
done
if grep -qE '^#?GPU_BACKEND=' "$REPO_DIR/openbeast.conf.example"; then
  pass "openbeast.conf.example documents GPU_BACKEND"
else
  fail "openbeast.conf.example doesn't document GPU_BACKEND"
fi
if grep -qE 'GPU_BACKEND=.*_ob_conf_value GPU_BACKEND' "$REPO_DIR/scripts/lib/conf.sh"; then
  pass "conf.sh resolves GPU_BACKEND (env → conf → default)"
else
  fail "conf.sh doesn't resolve GPU_BACKEND"
fi

# Adaptive context (Hardware Profiles Phase 2): ob_scale_context must keep
# reference-class cards at the measured value and scale smaller cards DOWN,
# monotonically, never above the reference.
if ( source "$REPO_DIR/scripts/lib/hardware.sh"
     ref=262144; w=13000
     [[ "$(ob_scale_context $ref 32607 $w)" == "$ref" ]] || exit 1   # 5090: unchanged
     [[ "$(ob_scale_context $ref 0 $w)"     == "$ref" ]] || exit 1   # unknown: unchanged
     small=$(ob_scale_context $ref 20480 $w)
     [[ "$small" -lt "$ref" && "$small" -ge 8192 ]] || exit 1        # 20GB: scaled down
     mid=$(ob_scale_context $ref 24564 $w)
     [[ "$mid" -gt "$small" && "$mid" -lt "$ref" ]] || exit 1 );then # monotonic
  pass "ob_scale_context: reference unchanged, smaller cards scale down monotonically"
else
  fail "ob_scale_context scaling is wrong"
fi
if grep -q 'ob_scale_context' "$REPO_DIR/scripts/serve.sh"; then
  pass "serve.sh applies adaptive context via ob_scale_context"
else
  fail "serve.sh doesn't call ob_scale_context"
fi

# --- 9. Entry-point shell syntax ---
echo ""
echo "Shell syntax:"
for f in start.sh stop.sh bootstrap.sh; do
  if bash -n "$REPO_DIR/$f" 2>/dev/null; then
    pass "$f passes bash -n"
  else
    fail "$f has bash syntax errors"
  fi
done

# --- 10. conf.sh contract (agent router + files dir, 2026-07-08) ---
# Sourced in a clean env with a scratch HOME and a scratch REPO_DIR (so a
# real openbeast.conf can't leak into the defaults under test).
echo ""
echo "conf.sh contract:"
CONF_SCRATCH=$(mktemp -d)
CONF_OUT=$(env -i PATH="$PATH" HOME="$CONF_SCRATCH" REPO_DIR="$CONF_SCRATCH" \
  bash -c "source '$REPO_DIR/scripts/lib/conf.sh'; printf '%s\n%s\n' \"\$OPENBEAST_FILES_DIR\" \"\$OPENBEAST_MODEL_URL\"") || CONF_OUT=""
CONF_FILES=$(echo "$CONF_OUT" | sed -n 1p)
CONF_URL=$(echo "$CONF_OUT" | sed -n 2p)
if [[ "$CONF_FILES" == "$CONF_SCRATCH/openbeast-files" ]]; then
  pass "conf.sh defaults OPENBEAST_FILES_DIR to \$HOME/openbeast-files"
else
  fail "conf.sh OPENBEAST_FILES_DIR default wrong (got: ${CONF_FILES:-empty})"
fi
if [[ "$CONF_URL" == "http://localhost:8080/v1" ]]; then
  pass "conf.sh default OPENBEAST_MODEL_URL is llama-server direct (:8080/v1)"
else
  fail "conf.sh default OPENBEAST_MODEL_URL wrong (got: ${CONF_URL:-empty})"
fi
ROUTER_URL=$(env -i PATH="$PATH" HOME="$CONF_SCRATCH" REPO_DIR="$CONF_SCRATCH" \
  OPENBEAST_AGENT_ROUTER=true \
  bash -c "source '$REPO_DIR/scripts/lib/conf.sh'; printf '%s\n' \"\$OPENBEAST_MODEL_URL\"") || ROUTER_URL=""
if [[ "$ROUTER_URL" == "http://localhost:8088/v1" ]]; then
  pass "OPENBEAST_AGENT_ROUTER=true flips OPENBEAST_MODEL_URL to the router (:8088/v1)"
else
  fail "AGENT_ROUTER=true didn't route OPENBEAST_MODEL_URL (got: ${ROUTER_URL:-empty})"
fi

# Distributed agents Phase 1 (docs/DISTRIBUTED_AGENTS_PLAN.md):
# OPENBEAST_AGENT_INFERENCE_URL must be ABSENT (not exported empty) when
# unset, and exported when set via the conf key.
AIU_UNSET=$(env -i PATH="$PATH" HOME="$CONF_SCRATCH" REPO_DIR="$CONF_SCRATCH" \
  bash -c "source '$REPO_DIR/scripts/lib/conf.sh'; printf '%s' \"\${OPENBEAST_AGENT_INFERENCE_URL-ABSENT}\"") || AIU_UNSET="(source failed)"
if [[ "$AIU_UNSET" == "ABSENT" ]]; then
  pass "conf.sh leaves OPENBEAST_AGENT_INFERENCE_URL unset by default (no empty export)"
else
  fail "conf.sh exported OPENBEAST_AGENT_INFERENCE_URL without config (got: '${AIU_UNSET}')"
fi
printf 'AGENT_INFERENCE_URL=https://worker.tail.ts.net:8443/v1\n' > "$CONF_SCRATCH/openbeast.conf"
AIU_SET=$(env -i PATH="$PATH" HOME="$CONF_SCRATCH" REPO_DIR="$CONF_SCRATCH" \
  bash -c "source '$REPO_DIR/scripts/lib/conf.sh'; printf '%s' \"\${OPENBEAST_AGENT_INFERENCE_URL-ABSENT}\"") || AIU_SET="(source failed)"
if [[ "$AIU_SET" == "https://worker.tail.ts.net:8443/v1" ]]; then
  pass "conf.sh exports OPENBEAST_AGENT_INFERENCE_URL from the AGENT_INFERENCE_URL conf key"
else
  fail "conf.sh didn't export AGENT_INFERENCE_URL from conf (got: '${AIU_SET}')"
fi
rm -f "$CONF_SCRATCH/openbeast.conf"
if grep -qE '^#?AGENT_INFERENCE_URL=' "$REPO_DIR/openbeast.conf.example"; then
  pass "openbeast.conf.example documents AGENT_INFERENCE_URL"
else
  fail "openbeast.conf.example doesn't document AGENT_INFERENCE_URL"
fi
# agent.sh must default --base-url from the exported worker endpoint.
if grep -q 'OPENBEAST_AGENT_INFERENCE_URL' "$REPO_DIR/agent.sh" \
   && grep -q -- '--base-url' "$REPO_DIR/agent.sh"; then
  pass "agent.sh defaults --base-url from OPENBEAST_AGENT_INFERENCE_URL"
else
  fail "agent.sh doesn't honor OPENBEAST_AGENT_INFERENCE_URL"
fi
rm -rf "$CONF_SCRATCH"

# --- 11. Collapsed skill tool surface (PRODUCTION_ROADMAP §B, 2026-07-08) ---
# list_skills/load_skill/reload_skills were folded into the single `skill`
# tool; no stale references may survive in the model-facing prompt.
echo ""
echo "Skill tool surface:"
if ! grep -qE 'list_skills|load_skill|reload_skills' "$REPO_DIR/system-prompt-tools.md"; then
  pass "system-prompt-tools.md has no stale list_skills/load_skill/reload_skills references"
else
  fail "system-prompt-tools.md still references collapsed skill tools"
fi
if ! grep -qE 'def (list_skills|load_skill|reload_skills)' "$REPO_DIR/agents/mcp_server.py" \
   && grep -q 'def skill(' "$REPO_DIR/agents/mcp_server.py"; then
  pass "mcp_server.py exposes the unified skill() tool (old trio removed)"
else
  fail "mcp_server.py skill tool collapse incomplete"
fi

# --- 12. Weight registry (supply-chain pins for every shipped GGUF) --------
# Every weight a serve script loads must have a registry row (sha256 + size
# + HF source), and bootstrap must read its default-model pin FROM the
# registry — a serve script added without a pin is the drift this catches.
echo ""
echo "Weight registry:"
REGISTRY="$REPO_DIR/scripts/weights.registry"
if [[ -f "$REGISTRY" && -x "$REPO_DIR/scripts/verify-weights.sh" ]]; then
  pass "weights.registry + verify-weights.sh present"
else
  fail "weights.registry or verify-weights.sh missing/not executable"
fi
MISSING_PINS=""
for f in "$REPO_DIR"/scripts/serve-*.sh; do
  w="$(grep -oE '\$WEIGHTS_DIR/[A-Za-z0-9._-]+\.gguf' "$f" | head -1 | sed 's|.*/||')"
  [[ -z "$w" ]] && continue
  grep -qP "\t\Q$w\E\t" "$REGISTRY" 2>/dev/null || MISSING_PINS="$MISSING_PINS $w"
done
if [[ -z "$MISSING_PINS" ]]; then
  pass "every serve-script weight has a registry pin"
else
  fail "weights missing registry pins:$MISSING_PINS"
fi
while IFS=$'\t' read -r sha bytes fname repo remote; do
  [[ -z "$sha" || "$sha" == \#* ]] && continue
  if [[ ! "$sha" =~ ^[0-9a-f]{64}$ || ! "$bytes" =~ ^[0-9]+$ || -z "$fname" || -z "$repo" ]]; then
    fail "malformed registry row for '${fname:-?}'"
  fi
done < "$REGISTRY"
pass "registry rows well-formed (64-hex sha + numeric size + source)"
if grep -q 'weights.registry' "$REPO_DIR/bootstrap.sh"; then
  pass "bootstrap.sh reads the default-model pin from the registry"
else
  fail "bootstrap.sh does not read weights.registry"
fi

# --- 13. Client mode (docs/MAC_CLIENT_PLAN.md) ------------------------------
echo ""
echo "Client mode:"
MC="$REPO_DIR/scripts/setup-mac-client.sh"
if [[ -x "$MC" ]] && grep -q -- '--uninstall' "$MC" && grep -q -- '--no-search' "$MC" \
   && grep -q -- '--host' "$MC"; then
  pass "setup-mac-client.sh present with --host/--no-search/--uninstall"
else
  fail "setup-mac-client.sh missing or flags incomplete"
fi
# Stock macOS ships Bash 3.2 — bash-4+ constructs must not creep in.
if ! grep -qE '\bmapfile\b|\breadarray\b|declare -A' "$MC" \
   && head -1 "$MC" | grep -q '/usr/bin/env bash'; then
  pass "setup-mac-client.sh is Bash 3.2-safe (no mapfile/readarray/declare -A)"
else
  fail "setup-mac-client.sh uses bash-4+ constructs (breaks stock macOS)"
fi
if grep -q -- '--publish-searxng' "$REPO_DIR/scripts/setup-tailscale.sh" \
   && grep -q -- '--unpublish-searxng' "$REPO_DIR/scripts/setup-tailscale.sh" \
   && grep -q 'https=8889' "$REPO_DIR/scripts/setup-tailscale.sh"; then
  pass "setup-tailscale.sh has the opt-in SearXNG publish/unpublish pair (:8889)"
else
  fail "setup-tailscale.sh missing --publish-searxng/--unpublish-searxng"
fi

# --- Summary ---
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

[[ $FAIL -eq 0 ]]
