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
  update.sh doctor.sh setup-mcpo-keys.sh clients.sh client.sh setup-client.sh
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
# fetch() CGNAT policy (beast-slot): FETCH_ALLOW_TAILNET must follow the same
# absent-vs-empty export contract as the other opt-in keys.
FAT_UNSET=$(env -i PATH="$PATH" HOME="$CONF_SCRATCH" REPO_DIR="$CONF_SCRATCH" \
  bash -c "source '$REPO_DIR/scripts/lib/conf.sh'; printf '%s' \"\${OPENBEAST_FETCH_ALLOW_TAILNET-ABSENT}\"") || FAT_UNSET="(source failed)"
if [[ "$FAT_UNSET" == "ABSENT" ]]; then
  pass "conf.sh leaves OPENBEAST_FETCH_ALLOW_TAILNET unset by default (no empty export)"
else
  fail "conf.sh exported OPENBEAST_FETCH_ALLOW_TAILNET without config (got: '${FAT_UNSET}')"
fi
printf 'FETCH_ALLOW_TAILNET=true\n' > "$CONF_SCRATCH/openbeast.conf"
FAT_SET=$(env -i PATH="$PATH" HOME="$CONF_SCRATCH" REPO_DIR="$CONF_SCRATCH" \
  bash -c "source '$REPO_DIR/scripts/lib/conf.sh'; printf '%s' \"\${OPENBEAST_FETCH_ALLOW_TAILNET-ABSENT}\"") || FAT_SET="(source failed)"
if [[ "$FAT_SET" == "true" ]]; then
  pass "conf.sh exports OPENBEAST_FETCH_ALLOW_TAILNET from the FETCH_ALLOW_TAILNET conf key"
else
  fail "conf.sh didn't export FETCH_ALLOW_TAILNET from conf (got: '${FAT_SET}')"
fi
rm -f "$CONF_SCRATCH/openbeast.conf"
if grep -qE '^#?FETCH_ALLOW_TAILNET=' "$REPO_DIR/openbeast.conf.example"; then
  pass "openbeast.conf.example documents FETCH_ALLOW_TAILNET"
else
  fail "openbeast.conf.example doesn't document FETCH_ALLOW_TAILNET"
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
  # A pinned row = 64-hex sha + numeric bytes; a PENDING row (weight shipped
  # but not yet downloaded/hashed) = literal PENDING + 0. Both are valid.
  if [[ "$sha" == "PENDING" ]]; then
    [[ "$bytes" == "0" && -n "$fname" && -n "$repo" ]] || fail "malformed PENDING row for '${fname:-?}'"
  elif [[ ! "$sha" =~ ^[0-9a-f]{64}$ || ! "$bytes" =~ ^[0-9]+$ || -z "$fname" || -z "$repo" ]]; then
    fail "malformed registry row for '${fname:-?}'"
  fi
done < "$REGISTRY"
pass "registry rows well-formed (64-hex sha + numeric size, or PENDING)"
if grep -q 'weights.registry' "$REPO_DIR/bootstrap.sh"; then
  pass "bootstrap.sh reads the default-model pin from the registry"
else
  fail "bootstrap.sh does not read weights.registry"
fi

# --- 13. Client mode (docs/BEAST_SLOT.md) -----------------------------------
echo ""
echo "Client mode:"
SC="$REPO_DIR/scripts/setup-client.sh"
if [[ -x "$SC" ]] && grep -q -- '--uninstall' "$SC" && grep -q -- '--no-search' "$SC" \
   && grep -q -- '--host' "$SC" && grep -q -- '--api-key' "$SC" \
   && grep -q -- '--local-search' "$SC"; then
  pass "setup-client.sh present with --host/--no-search/--local-search/--api-key/--uninstall"
else
  fail "setup-client.sh missing or flags incomplete"
fi
# The old entry point must survive as a passthrough (published in docs/README
# since 2026-07-17).
MC="$REPO_DIR/scripts/setup-mac-client.sh"
if [[ -x "$MC" ]] && grep -q 'setup-client.sh' "$MC" && grep -q 'exec' "$MC"; then
  pass "setup-mac-client.sh is a back-compat exec wrapper"
else
  fail "setup-mac-client.sh no longer delegates to setup-client.sh"
fi
# Stock macOS ships Bash 3.2 — bash-4+ constructs must not creep into any
# client-side script.
for _cs in "$SC" "$MC" "$REPO_DIR/scripts/client.sh"; do
  if ! grep -qE '\bmapfile\b|\breadarray\b|declare -A' "$_cs" \
     && head -1 "$_cs" | grep -q '/usr/bin/env bash'; then
    pass "$(basename "$_cs") is Bash 3.2-safe (no mapfile/readarray/declare -A)"
  else
    fail "$(basename "$_cs") uses bash-4+ constructs (breaks stock macOS)"
  fi
done
# The CLI is installed as a SYMLINK (~/.local/bin/openbeast-client), so $0
# must be resolved before deriving the repo root — otherwise every subcommand
# but `status` reaches into a nonexistent ~/.local/agents|scripts.
CC="$REPO_DIR/scripts/client.sh"
SYMTEST=$(mktemp -d)
ln -s "$CC" "$SYMTEST/openbeast-client"
if [[ "$(bash "$SYMTEST/openbeast-client" --help 2>/dev/null | head -1)" == *"client CLI"* ]] \
   && grep -q 'readlink' "$CC"; then
  pass "client.sh resolves \$0 through symlinks before deriving REPO"
else
  fail "client.sh does not resolve symlinks — the installed CLI breaks"
fi
rm -rf "$SYMTEST"
# Client CLI subcommand surface.
if [[ -x "$CC" ]] && grep -q 'status)' "$CC" && grep -q 'agent)' "$CC" \
   && grep -q 'search)' "$CC" && grep -q 'update)' "$CC" && grep -q 'uninstall)' "$CC"; then
  pass "client.sh has status/agent/search/update/uninstall"
else
  fail "client.sh subcommand surface incomplete"
fi
# The client CLI must never source the rig-shaped conf.sh (it would generate
# a SearXNG secret into a nonexistent openbeast.conf on the laptop).
if ! grep -qE '^[^#]*(source|\.)[[:space:]]+[^#]*lib/conf\.sh' "$CC" \
   && ! grep -qE '^[^#]*(source|\.)[[:space:]]+[^#]*lib/conf\.sh' "$SC"; then
  pass "client scripts never source scripts/lib/conf.sh (rig-shaped)"
else
  fail "a client script sources lib/conf.sh — rig-shaped behavior on a laptop"
fi
# Local-search compose variant: bridge network (Docker Desktop has no
# network_mode:host), loopback-only port map, fail-loud secret.
CSC="$REPO_DIR/scripts/client-searxng.compose.yml"
if [[ -f "$CSC" ]] && ! grep -qE '^[[:space:]]*network_mode:' "$CSC" \
   && grep -q '127.0.0.1:8888' "$CSC" \
   && grep -q 'OPENBEAST_SEARXNG_SECRET:?' "$CSC"; then
  pass "client-searxng.compose.yml: bridge net, loopback map, required secret"
else
  fail "client-searxng.compose.yml missing or violates the client contract"
fi
if grep -q -- '--publish-searxng' "$REPO_DIR/scripts/setup-tailscale.sh" \
   && grep -q -- '--unpublish-searxng' "$REPO_DIR/scripts/setup-tailscale.sh" \
   && grep -q 'https=8889' "$REPO_DIR/scripts/setup-tailscale.sh"; then
  pass "setup-tailscale.sh has the opt-in SearXNG publish/unpublish pair (:8889)"
else
  fail "setup-tailscale.sh missing --publish-searxng/--unpublish-searxng"
fi

# --- 14. Extension system (ODS-absorbed) ------------------------------------
echo ""
echo "Extension system:"
if [[ -x "$REPO_DIR/scripts/ext.sh" && -f "$REPO_DIR/scripts/lib/extensions.sh" ]]; then
  pass "ext.sh + lib/extensions.sh present"
else
  fail "ext.sh or lib/extensions.sh missing"
fi
# start.sh + stop.sh must both source the extension lib and merge compose args.
if grep -q 'lib/extensions.sh' "$REPO_DIR/start.sh" && grep -q 'lib/extensions.sh' "$REPO_DIR/stop.sh" \
   && grep -q 'ob_ext_compose_args' "$REPO_DIR/start.sh"; then
  pass "start.sh + stop.sh wire the extension system"
else
  fail "start.sh/stop.sh don't wire the extension system"
fi
# Every shipped extension must have a well-formed manifest (NAME/DESCRIPTION/KIND).
EXT_ERR=""
for _m in "$REPO_DIR"/extensions/*/manifest; do
  [[ -e "$_m" ]] || continue
  for _k in NAME DESCRIPTION KIND; do
    grep -qE "^${_k}=" "$_m" || EXT_ERR="$EXT_ERR $(basename "$(dirname "$_m")"):$_k"
  done
  _kind="$(grep -E '^KIND=' "$_m" | cut -d= -f2)"
  [[ "$_kind" == compose || "$_kind" == process ]] || EXT_ERR="$EXT_ERR $(basename "$(dirname "$_m")"):bad-KIND"
done
if [[ -z "$EXT_ERR" ]]; then
  pass "all extension manifests well-formed (NAME/DESCRIPTION/KIND)"
else
  fail "malformed extension manifests:$EXT_ERR"
fi

# --- 15. beast-slot + beast-gate (docs/BEAST_SLOT.md) -----------------------
echo ""
echo "beast-slot surface:"
# The gate is the only place on the inference path where identity exists —
# these pin the properties that make it worth having.
EDGE="$REPO_DIR/agents/edge.py"
if [[ -f "$EDGE" ]] && grep -q "ALLOWED_PATHS" "$EDGE" \
   && grep -q '"/v1/chat/completions"' "$EDGE"; then
  pass "beast-gate ships a path allowlist"
else
  fail "agents/edge.py missing or has no path allowlist"
fi
# Dangerous llama-server routes must NOT be in the allowlist.
if ! sed -n '/ALLOWED_PATHS = /,/})/p' "$EDGE" \
     | grep -qE 'lora-adapters|/slots|/v1/stream|/infill|/props'; then
  pass "beast-gate allowlist excludes lora-adapters/slots/stream/infill/props"
else
  fail "beast-gate allowlist admits a dangerous llama-server route"
fi
if grep -q 'body.pop("id_slot"' "$EDGE"; then
  pass "beast-gate strips client-supplied id_slot"
else
  fail "beast-gate does not strip id_slot (unauth'd slot pinning + queue jump)"
fi
# Fail-closed: an empty registry must not serve anonymous callers by default.
if grep -q 'ALLOW_ANON' "$EDGE" && grep -q '"no_registry"' "$EDGE"; then
  pass "beast-gate fails closed with no devices enrolled"
else
  fail "beast-gate has no fail-closed path for an empty registry"
fi
# Anchor to the exec arg list: a bare grep is satisfied by serve.sh's own
# explanatory COMMENT, so it could never detect the regression it claims to.
if grep -qE '^[[:space:]]*--metrics' "$REPO_DIR/scripts/serve.sh"; then
  pass "serve.sh enables llama-server metrics (queue depth for /api/slot)"
else
  fail "serve.sh lacks --metrics — capacity.queue_deferred will always be null"
fi
# Model governance: serve.sh must check the weight it is about to load against
# the registry, and must default to WARN (a hard refusal on the critical start
# path would be a foot-gun on upgrade).
# BEHAVIORAL, not a grep: extract the enforcement block into a scratch script
# and exercise every mode. A substring grep would pass for any regression that
# kept the words but broke the logic — and this block sits on the critical
# start path, so "it mentions the registry" is not evidence of anything.
WE_SCRATCH=$(mktemp -d)
mkdir -p "$WE_SCRATCH/scripts"
sed -n '/^# --- Model registry enforcement/,/^fi$/p' "$REPO_DIR/scripts/serve.sh" \
  > "$WE_SCRATCH/block.sh"
cp "$REPO_DIR/scripts/weights.registry" "$WE_SCRATCH/scripts/" 2>/dev/null
cat > "$WE_SCRATCH/run.sh" <<'WEEOF'
set -euo pipefail
SCRIPT_DIR="$1"; MODEL="$2"; WEIGHT_ENFORCE="${3:-warn}"
source "$SCRIPT_DIR/../block.sh"
exit 0
WEEOF
echo unlisted > "$WE_SCRATCH/unlisted.gguf"
_we_rc() { bash "$WE_SCRATCH/run.sh" "$WE_SCRATCH/scripts" "$1" "$2" >/dev/null 2>&1; echo $?; }
if [[ -s "$WE_SCRATCH/block.sh" ]]; then
  _rc_warn=$(_we_rc "$WE_SCRATCH/unlisted.gguf" warn)
  _rc_strict=$(_we_rc "$WE_SCRATCH/unlisted.gguf" strict)
  _rc_off=$(_we_rc "$WE_SCRATCH/unlisted.gguf" off)
  _rc_typo=$(_we_rc "$WE_SCRATCH/unlisted.gguf" NOTAMODE)
  # warn/off/typo MUST NOT block a launch; strict MUST refuse with exit 3 so
  # start.sh can tell a supply-chain refusal from a crash (and not roll back).
  if [[ "$_rc_warn" == "0" && "$_rc_off" == "0" && "$_rc_typo" == "0" && "$_rc_strict" == "3" ]]; then
    pass "weight enforcement: warn/off/bad-value pass, strict refuses with exit 3"
  else
    fail "weight enforcement rc wrong (warn=$_rc_warn off=$_rc_off typo=$_rc_typo strict=$_rc_strict; want 0/0/0/3)"
  fi
else
  fail "could not extract the weight-enforcement block from serve.sh"
fi
rm -rf "$WE_SCRATCH"
# start.sh must refuse to roll back on that exit code, or strict mode would
# silently serve a DIFFERENT model than the operator configured.
if grep -q 'Refusing to roll back' "$REPO_DIR/start.sh"; then
  pass "start.sh refuses MODEL_ROLLBACK on a supply-chain refusal"
else
  fail "start.sh would roll back past a WEIGHT_ENFORCE=strict refusal"
fi
WE_DEFAULT=$(env -i PATH="$PATH" HOME="$(mktemp -d)" REPO_DIR="$REPO_DIR" \
  bash -c "source '$REPO_DIR/scripts/lib/conf.sh' >/dev/null 2>&1; printf '%s' \"\$WEIGHT_ENFORCE\"") || WE_DEFAULT="(failed)"
if [[ "$WE_DEFAULT" == "warn" ]]; then
  pass "WEIGHT_ENFORCE defaults to warn (never blocks an upgrade's first start)"
else
  fail "WEIGHT_ENFORCE default is '$WE_DEFAULT', want 'warn'"
fi
# Every weight a shipped serve script targets should be pinned, or strict mode
# is unusable out of the box.
UNPINNED=""
for _ss in "$REPO_DIR"/scripts/serve-*.sh; do
  _g=$(grep -oE '\$WEIGHTS_DIR/[^"]+\.gguf' "$_ss" 2>/dev/null | head -1); _g="${_g##*/}"
  [[ -n "$_g" ]] || continue
  awk -F'\t' -v n="$_g" '$0 !~ /^#/ && $3 == n {found=1} END{exit !found}' \
    "$REPO_DIR/scripts/weights.registry" || UNPINNED="$UNPINNED $(basename "$_ss")"
done
if [[ -z "$UNPINNED" ]]; then
  pass "every shipped serve script targets a registry-pinned weight"
else
  fail "serve scripts targeting unpinned weights:$UNPINNED"
fi

if grep -q 'inference-audit.jsonl' "$REPO_DIR/scripts/logrotate-openbeast.conf"; then
  pass "logrotate covers the inference audit stream"
else
  fail "inference-audit.jsonl not in logrotate-openbeast.conf"
fi
if grep -q -- '--publish-slot' "$REPO_DIR/scripts/setup-tailscale.sh" \
   && grep -q -- '--unpublish-slot' "$REPO_DIR/scripts/setup-tailscale.sh" \
   && grep -q 'https=8444' "$REPO_DIR/scripts/setup-tailscale.sh"; then
  pass "setup-tailscale.sh publishes/unpublishes the slot API (:8444)"
else
  fail "setup-tailscale.sh missing --publish-slot/--unpublish-slot/:8444"
fi
if grep -q '/api/slot' "$REPO_DIR/extensions/dashboard/dashboard.py" \
   && grep -q 'beast_slot' "$REPO_DIR/extensions/dashboard/dashboard.py"; then
  pass "dashboard serves the /api/slot contract"
else
  fail "dashboard.py missing the /api/slot beast-slot contract"
fi
# Keyed installs: healthcheck must present the bearer to llama-server.
if grep -qE 'check "llama.cpp server".*LLAMA_API_KEY' "$REPO_DIR/scripts/healthcheck.sh" \
   && grep -q 'LLAMA_AUTH' "$REPO_DIR/scripts/healthcheck.sh"; then
  pass "healthcheck presents LLAMA_API_KEY to llama-server"
else
  fail "healthcheck.sh doesn't pass the bearer to llama-server checks"
fi
# The runner must resolve a key (keyed beast-slot endpoints) but never
# receive one on argv from the spawn path.
if grep -q 'resolve_api_key' "$REPO_DIR/agents/runner.py" \
   && ! grep -q -- '--api-key' "$REPO_DIR/agents/mcp_server.py"; then
  pass "runner resolves API key from env; spawn path keeps it off argv"
else
  fail "runner/mcp_server api-key wiring drifted (env-only contract)"
fi

# --- Summary ---
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================"

[[ $FAIL -eq 0 ]]
