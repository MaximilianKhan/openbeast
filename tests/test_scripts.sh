#!/bin/bash
# Validate script structure: existence, permissions, path references.
#
# MUST pass on a box with NO GPU, no docker and no network — CI is such a box.
# Any check that needs one of those STUBS it on PATH (see the curl stub in the
# preflight test and the nvidia-smi stub in the GPU-lease test). A check that
# reads the ambient machine instead passes here and fails on CI, or worse
# passes on CI while asserting nothing.
#
# Usage: ./tests/test_scripts.sh

set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export REPO_DIR  # the embedded Python heredocs read it from the environment

PASS=0
FAIL=0
SKIP=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
# Skips are COUNTED and printed in the summary: a skip that reports as a pass
# is how a test quietly stops testing anything.
skip() { echo "  SKIP: $1"; SKIP=$((SKIP + 1)); }

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
  serve-qwen-27b-q5.sh serve-qwen-35b-a3b.sh
  serve-qwen-27b-mtp-q5.sh serve-qwen-35b-a3b-mtp.sh
  serve-qwopus-27b-v2-q5.sh serve-qwopus-27b-v2-mtp-q5.sh
  serve-gemma-4-31b-q5.sh
  run-qwen-27b-q5.sh run-qwen-35b-a3b.sh
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
# Capture first, THEN grep: piping straight into grep under `set -o pipefail`
# makes the pipeline's status doctor's OWN exit code, so a box where doctor
# correctly reports a failure (no GPU, no docker) failed a test that claims to
# assert only "it reached the summary". Same trap as the grep -q SIGPIPE family.
_DOC_OUT="$(bash "$REPO_DIR/scripts/doctor.sh" --quiet 2>&1 || true)"
if grep -q '^doctor: ' <<< "$_DOC_OUT"; then
  pass "doctor.sh runs and reports a verdict"
else
  fail "doctor.sh did not reach its summary line: $(tail -3 <<< "$_DOC_OUT")"
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
# --purge-logs must REFUSE to run against a rig checkout. A client uninstall
# destroying a server's agent history is data loss with no upside — this
# guard exists because that exact mistake cost 6035 transcripts once.
PURGE_T=$(mktemp -d); mkdir -p "$PURGE_T/agents/logs" "$PURGE_T/scripts" "$PURGE_T/weights"
cp "$REPO_DIR/scripts/setup-client.sh" "$PURGE_T/scripts/"
echo '{}' > "$PURGE_T/agents/logs/agent-guard.jsonl"
touch "$PURGE_T/openbeast.conf"                       # <- marks it as a RIG
PURGE_HOME=$(mktemp -d)
HOME="$PURGE_HOME" bash "$PURGE_T/scripts/setup-client.sh" --uninstall --purge-logs >/dev/null 2>&1 || true
if [[ -f "$PURGE_T/agents/logs/agent-guard.jsonl" ]]; then
  pass "--purge-logs refuses to delete a RIG checkout's agent transcripts"
else
  fail "--purge-logs deleted transcripts from a rig checkout (data loss)"
fi
rm -rf "$PURGE_T" "$PURGE_HOME"

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
  # `|| true` is load-bearing: under `set -euo pipefail` a serve script with no
  # $WEIGHTS_DIR/*.gguf line makes grep exit 1, and a bare `_g=$(...)` then
  # kills the ENTIRE suite mid-run rather than reporting a failure — the same
  # set -e trap already fixed once in serve.sh's ob_scale_context call.
  # Surfaced 2026-08-15 by the negative test for the drift guards below.
  _g=$(grep -oE '\$WEIGHTS_DIR/[^"]+\.gguf' "$_ss" 2>/dev/null | head -1 || true); _g="${_g##*/}"
  [[ -n "$_g" ]] || continue
  awk -F'\t' -v n="$_g" '$0 !~ /^#/ && $3 == n {found=1} END{exit !found}' \
    "$REPO_DIR/scripts/weights.registry" || UNPINNED="$UNPINNED $(basename "$_ss")"
done
if [[ -z "$UNPINNED" ]]; then
  pass "every shipped serve script targets a registry-pinned weight"
else
  fail "serve scripts targeting unpinned weights:$UNPINNED"
fi

# Two more tables that are supposed to mirror the serve scripts and silently
# did not. Both drifted unnoticed: on 2026-08-15, 8 of 23 serve scripts had no
# opencode.json entry (so remote clients could not select them at all, and the
# six Qwen3.8 configs were invisible the day after shipping), and 8 had no
# benchmark_all.py entry — including the DEFAULT the rig serves, which doctor
# had been warning about for weeks. Hand-maintained mirrors rot; assert them.
# opencode.json OR scripts/opencode-excluded.txt — either is fine, but it has
# to be a decision. Asserted in BOTH directions since 2026-08-19: the old
# one-way check only caught "script with no entry", so when the Fable-Fusion,
# Qwopus and Gemma 4 weights were deleted their catalog rows lingered and
# opencode kept offering models that could not load. An entry with no script is
# now a failure too.
_oc_excl() { # $1 = slug -> 0 if deliberately excluded
  awk -F'\t' -v s="$1" '$0 !~ /^#/ && $1 == s {found=1} END{exit !found}' \
    "$REPO_DIR/scripts/opencode-excluded.txt"
}
MISSING_OC=""; BOTH_OC=""
for _ss in "$REPO_DIR"/scripts/serve-*.sh; do
  [[ "$(basename "$_ss")" == "serve-bootstrap.sh" ]] && continue
  _slug="$(basename "$_ss" .sh)"; _slug="${_slug#serve-}"
  if grep -q "\"$_slug\"[[:space:]]*:" "$REPO_DIR/opencode.json"; then
    _oc_excl "$_slug" && BOTH_OC="$BOTH_OC $_slug"
  else
    _oc_excl "$_slug" || MISSING_OC="$MISSING_OC $_slug"
  fi
done
if [[ -n "$MISSING_OC" ]]; then
  fail "serve scripts in neither opencode.json nor opencode-excluded.txt:$MISSING_OC"
elif [[ -n "$BOTH_OC" ]]; then
  fail "serve scripts both advertised AND excluded (pick one):$BOTH_OC"
else
  pass "every shipped serve script is in opencode.json or opencode-excluded.txt"
fi

# The reverse direction: a catalog entry with no serve script behind it. This
# is what let deleted models keep showing up in the picker for weeks.
ORPHAN_OC="$(python3 -c '
import json,sys,os
repo=sys.argv[1]
models=json.load(open(os.path.join(repo,"opencode.json")))["provider"]["llama-cpp"]["models"]
print(" ".join(s for s in models
      if not os.path.exists(os.path.join(repo,"scripts","serve-%s.sh"%s))))
' "$REPO_DIR" 2>/dev/null)"
if [[ -z "$ORPHAN_OC" ]]; then
  pass "every opencode.json entry has a serve script behind it (no phantom models)"
else
  fail "opencode.json advertises models with no serve script:$ORPHAN_OC"
fi

# MODELS or BENCH_EXCLUDED — either is fine, but it has to be a decision.
MISSING_BENCH=""
for _ss in "$REPO_DIR"/scripts/serve-*.sh; do
  [[ "$(basename "$_ss")" == "serve-bootstrap.sh" ]] && continue
  _rel="scripts/$(basename "$_ss")"
  grep -q "\"$_rel\"" "$REPO_DIR/evals/benchmark_all.py" \
    || MISSING_BENCH="$MISSING_BENCH $(basename "$_ss")"
done
if [[ -z "$MISSING_BENCH" ]]; then
  pass "every shipped serve script is in benchmark_all.py MODELS or BENCH_EXCLUDED"
else
  fail "serve scripts absent from both MODELS and BENCH_EXCLUDED:$MISSING_BENCH"
fi

# The DEFAULT model is declared in four independent places, and openbeast.conf
# (where a rig actually overrides it) is gitignored — so a maintainer running a
# different default locally cannot see the shipped one drift. It did: on
# 2026-08-15 the docs claimed a default that conf.sh, openbeast.conf.example
# and bootstrap.sh had never heard of, because the docs were edited to match
# one machine's private override. Pin all four to each other.
DEFAULT_EXPECT="serve-qwen38-27b-uncensored-mtp-q5.sh"
DEFAULT_WEIGHT="Qwen3.8-27B-Uncensored-Q5_K_M.gguf"

_d_conf="$(grep -oE 'echo serve-[a-z0-9.\-]+\.sh' "$REPO_DIR/scripts/lib/conf.sh" | head -1 | sed 's/^echo //')"
_d_example="$(grep -oE '^#?SERVE_SCRIPT=serve-[a-z0-9.\-]+\.sh' "$REPO_DIR/openbeast.conf.example" | head -1 | sed 's/.*=//')"
_d_weight="$(grep -oE '^WEIGHT_FILE="[^"]+"' "$REPO_DIR/bootstrap.sh" | head -1 | sed 's/^WEIGHT_FILE="//;s/"$//')"

DEFAULT_BAD=""
[[ "$_d_conf"    == "$DEFAULT_EXPECT" ]] || DEFAULT_BAD="$DEFAULT_BAD conf.sh=$_d_conf"
[[ "$_d_example" == "$DEFAULT_EXPECT" ]] || DEFAULT_BAD="$DEFAULT_BAD conf.example=$_d_example"
[[ "$_d_weight"  == "$DEFAULT_WEIGHT" ]] || DEFAULT_BAD="$DEFAULT_BAD bootstrap-weight=$_d_weight"
# The default serve script must exist, and must load the weight bootstrap fetches.
[[ -x "$REPO_DIR/scripts/$DEFAULT_EXPECT" ]] || DEFAULT_BAD="$DEFAULT_BAD missing-script"
grep -q "$DEFAULT_WEIGHT" "$REPO_DIR/scripts/$DEFAULT_EXPECT" \
  || DEFAULT_BAD="$DEFAULT_BAD script-loads-a-different-weight"
# ...and that weight must be registry-pinned, or a fresh install cannot verify it.
awk -F'\t' -v n="$DEFAULT_WEIGHT" '$0 !~ /^#/ && $3 == n {found=1} END{exit !found}' \
  "$REPO_DIR/scripts/weights.registry" || DEFAULT_BAD="$DEFAULT_BAD weight-not-pinned"

if [[ -z "$DEFAULT_BAD" ]]; then
  pass "default model agrees across conf.sh, conf.example, bootstrap, and the serve script"
else
  fail "default model disagrees (want $DEFAULT_EXPECT /$DEFAULT_WEIGHT):$DEFAULT_BAD"
fi

# The user-facing docs must name the same default. These drifted for weeks
# while pointing at a model that was no longer served.
# Whitespace-normalised: prose wraps, and a doc test that silently depends on
# where a line breaks is a trap for whoever reflows the paragraph. README.md
# genuinely wraps this phrase mid-name.
DOC_BAD=""
for _doc in README.md docs/MODELS.md docs/FEATURES.md; do
  tr '\n' ' ' < "$REPO_DIR/$_doc" | tr -s ' ' \
    | grep -qi "Qwen3.8 27B Uncensored MTP Q5" || DOC_BAD="$DOC_BAD $_doc"
done
if [[ -z "$DOC_BAD" ]]; then
  pass "README/MODELS/FEATURES name the shipped default model"
else
  fail "docs do not name the shipped default (Qwen3.8 27B Uncensored MTP Q5):$DOC_BAD"
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

# --- 16. configure-webui.sh degrades without an admin token (2026-07-31) -----
# Regression: the script used to `exit 0` when the WebUI sign-in failed, which
# happens permanently as soon as the operator changes their WebUI password from
# the one in openbeast.conf. Everything downstream — model connection, web
# search, and the per-model entry that attaches tools via meta.toolIds — is
# written straight to the DB and needs no token, but was being skipped every
# startup. Symptom: a newly-defaulted model has no WebUI model entry, so no
# tools are attached and the UI silently can't make tool calls.
CW="$REPO_DIR/scripts/configure-webui.sh"
if ! grep -qE '^\s*exit 0\s*$' "$CW"; then
  pass "configure-webui.sh does not bail out when the admin token is missing"
else
  fail "configure-webui.sh still exits early without a token (skips DB config)"
fi
if grep -q 'TOKEN_OK' "$CW"; then
  pass "configure-webui.sh gates only the API-backed section on the token"
else
  fail "configure-webui.sh lost its TOKEN_OK degradation flag"
fi
# WebUI's built-in Web Search (the composer toggle) is separate from the
# web_search TOOL and ships disabled with no engine — wire it to our SearXNG.
if grep -q 'web.search.enable' "$CW" && grep -q 'web.search.searxng_query_url' "$CW"; then
  pass "configure-webui.sh wires built-in web search to SearXNG"
else
  fail "configure-webui.sh no longer configures built-in web search"
fi
# A DB-written setting is invisible until WebUI reloads its cached config.
if grep -q 'WEBSEARCH_CHANGED' "$CW" && grep -q 'docker restart open-webui' "$CW"; then
  pass "configure-webui.sh restarts WebUI when it changes cached config"
else
  fail "configure-webui.sh writes config without triggering a reload"
fi

# FAST_BOOT must DEGRADE, never fail the boot. The bridge weight
# (Qwen3-0.6B-Q8_0.gguf) is registry-pinned, conf-exposed and documented — and
# bootstrap.sh never downloads it, so on a fresh install with FAST_BOOT=true
# the whole stack used to die at the health wait with "bootstrap model failed
# to load", a message pointing at llama-server rather than at a file nobody
# fetched. Fast boot is an optimisation; a missing optimisation degrades.
echo ""
echo "Fast boot degradation:"
_FB_TMP="$(mktemp -d)"
trap 'rm -rf "$_FB_TMP"' EXIT
mkdir -p "$_FB_TMP/scripts/lib" "$_FB_TMP/weights"
install -m 755 "$REPO_DIR/scripts/serve-bootstrap.sh" "$_FB_TMP/scripts/serve-bootstrap.sh"
printf 'WEIGHTS_DIR="%s/weights"\n' "$_FB_TMP" > "$_FB_TMP/scripts/lib/weights.sh"
# Lift the gate out of start.sh and drive it directly — no server, no GPU.
python3 - "$REPO_DIR/start.sh" "$_FB_TMP/gate.sh" <<'PYGATE'
import sys
src = open(sys.argv[1]).read()
a = src.index('FAST_BOOT_ACTIVE=0\nif [[ "${FAST_BOOT:-false}" == "true"')
b = src.index('echo "Waiting for llama.cpp server to be ready..."')
open(sys.argv[2], "w").write(src[a:b])
PYGATE
_fb_run() {                      # _fb_run -> prints "ACTIVE=<0|1>"
  bash -c '
    SCRIPT_DIR="'"$_FB_TMP"'"; FAST_BOOT=true
    SERVE_SCRIPT="serve-real.sh"; BOOTSTRAP_SERVE="serve-bootstrap.sh"
    REAL_SERVE_SCRIPT="$SERVE_SCRIPT"
    source "'"$_FB_TMP"'/gate.sh"
    echo "ACTIVE=$FAST_BOOT_ACTIVE SCRIPT=$SERVE_SCRIPT"' 2>&1
}
rm -f "$_FB_TMP/weights/Qwen3-0.6B-Q8_0.gguf"
_FB_OUT="$(_fb_run)"
if grep -q 'ACTIVE=0 SCRIPT=serve-real.sh' <<< "$_FB_OUT" \
   && grep -q 'fetch-weight.sh' <<< "$_FB_OUT"; then
  pass "FAST_BOOT with no bridge weight falls back to the real model and says how to fix it"
else
  fail "FAST_BOOT with no bridge weight did not degrade cleanly: $_FB_OUT"
fi
: > "$_FB_TMP/weights/Qwen3-0.6B-Q8_0.gguf"
_FB_OUT="$(_fb_run)"
if grep -q 'ACTIVE=1 SCRIPT=serve-bootstrap.sh' <<< "$_FB_OUT"; then
  pass "FAST_BOOT with the bridge weight present still takes the fast path"
else
  fail "FAST_BOOT stopped working when the weight IS present: $_FB_OUT"
fi

# fetch-weight.sh is what that message names, so it must exist, be executable,
# and refuse an unknown name instead of inventing a download.
if [[ -x "$REPO_DIR/scripts/fetch-weight.sh" ]]; then
  pass "scripts/fetch-weight.sh exists and is executable"
  # Capture first, THEN grep: the script correctly exits non-zero on an
  # unknown name, and under `set -o pipefail` that failure propagates through
  # the pipe and inverts the test even when grep matches.
  _FW_OUT="$("$REPO_DIR/scripts/fetch-weight.sh" definitely-not-a-weight.gguf 2>&1 || true)"
  if grep -q 'no registry entry' <<< "$_FW_OUT"; then
    pass "fetch-weight.sh refuses a name that is not in the registry"
  else
    fail "fetch-weight.sh did not refuse an unregistered weight name: $_FW_OUT"
  fi
  # It must work on a box with NO weights directory — that is the box you
  # reach for it on. lib/weights.sh is otherwise fatal when the dir is
  # missing, and CI caught this the hard way. Asserted here too so the
  # property is not checked only on a machine that happens to lack one.
  _FW_TMP="$(mktemp -d)"
  _FW_OUT="$(OPENBEAST_WEIGHTS_DIR="$_FW_TMP/not-created-yet" \
             "$REPO_DIR/scripts/fetch-weight.sh" definitely-not-a-weight.gguf 2>&1 || true)"
  if grep -q 'no registry entry' <<< "$_FW_OUT"; then
    pass "fetch-weight.sh runs with no pre-existing weights directory"
  else
    fail "fetch-weight.sh dies when the weights dir is absent: $_FW_OUT"
  fi
  rm -rf "$_FW_TMP"
else
  fail "scripts/fetch-weight.sh missing — start.sh names it in its fallback message"
fi

# An honest preflight. It used to run eleven LOCAL probes and then print
# "Environment looks ready — run ./bootstrap.sh to install" on a machine that
# dies ~2 seconds later at the git clone; `curl` was checked for PRESENCE and
# never used. Driven here with a curl that always fails, which is what a
# closed network looks like from inside the script.
echo ""
echo "Preflight honesty:"
_PF_TMP="$(mktemp -d)"
printf '#!/bin/bash\nexit 1\n' > "$_PF_TMP/curl"
chmod +x "$_PF_TMP/curl"
# GPU_BACKEND=cpu + --minimal pin the LOCAL environment clean on any box, so
# the network is the only axis left. Without them this test read the ambient
# machine: the consequence line lives in the summary's PF_NO_NET branch, which
# is only reached when n_fail == 0, so a GPU-less or docker-less runner
# hard-failed on the toolchain and the assertion silently tested nothing.
# OPENBEAST_GPU_BACKEND is the ENV name; GPU_BACKEND is the conf-file key
# (scripts/lib/conf.sh:30). Setting the latter here looked like it worked and
# silently did nothing.
_PF_OUT="$(PATH="$_PF_TMP:$PATH" OPENBEAST_GPU_BACKEND=cpu "$REPO_DIR/bootstrap.sh" \
           --preflight --minimal 2>&1 | sed 's/\x1b\[[0-9;]*m//g' || true)"
if grep -q 'cannot reach' <<< "$_PF_OUT"; then
  pass "preflight reports unreachable hosts instead of only checking curl exists"
else
  fail "preflight said nothing about the network"
fi
if grep -q 'Environment looks ready' <<< "$_PF_OUT"; then
  fail "preflight still claims readiness on a box that cannot fetch anything"
else
  pass "preflight does not claim readiness when the network is unreachable"
fi
if grep -q 'first install will fail' <<< "$_PF_OUT"; then
  pass "preflight names the consequence (a first install cannot complete)"
else
  fail "preflight warns without saying what it costs"
fi
# A box with BOTH a local gap and a dead network used to be told only about
# the local gap, because the consequence line sat in the summary's PF_NO_NET
# branch and any failure jumped past it. Construct that box: ask for the sycl
# backend (no icpx on a normal runner) with the same dead-curl stub.
_PF2_OUT="$(PATH="$_PF_TMP:$PATH" OPENBEAST_GPU_BACKEND=sycl "$REPO_DIR/bootstrap.sh" \
            --preflight --minimal 2>&1 | sed 's/\x1b\[[0-9;]*m//g' || true)"
if grep -qE '^  ✗' <<< "$_PF2_OUT"; then
  if grep -q 'first install will fail' <<< "$_PF2_OUT"; then
    pass "preflight names the network consequence even when a local check fails"
  else
    fail "a box with a local gap AND no network hears only about the local gap"
  fi
else
  # icpx present — the case we wanted to build does not exist here.
  skip "no local check fails under --minimal sycl on this box"
fi
rm -rf "$_PF_TMP"
rm -rf "$_PF_TMP"

# update.sh must tell "the remote is unreachable" apart from "your worktree is
# dirty". It used to swallow git's stderr, burn a SECOND connect timeout, and
# then blame local changes — the wrong cause, in the script you reach for when
# already confused. The pattern is read OUT OF update.sh so this test cannot
# drift from the code it checks.
echo ""
echo "update.sh failure diagnosis:"
# `|| true`: under `set -e` a non-matching grep aborts the whole test file
# before it can report the failure, so the mutation that DELETES this pattern
# looked like a pass. The test has to survive its own failure case.
_UP_PAT="$(grep -oE "could not resolve host\|[^']*temporary failure in name resolution" \
           "$REPO_DIR/scripts/update.sh" | head -1 || true)"
if [[ -z "$_UP_PAT" ]]; then
  fail "could not find the network-vs-local pattern in update.sh"
else
  _UP_OK=1
  while IFS='|' read -r want msg; do
    [[ -z "$want" ]] && continue
    if grep -qiE "$_UP_PAT" <<< "$msg"; then got=NETWORK; else got=LOCAL; fi
    [[ "$got" == "$want" ]] || { _UP_OK=0; echo "      misclassified as $got: $msg"; }
  done <<'CASES'
NETWORK|fatal: unable to access 'https://github.com/x.git/': Could not resolve host: github.com
NETWORK|fatal: unable to access 'https://github.com/x.git/': Failed to connect to github.com port 443 after 129011 ms: Connection timed out
NETWORK|fatal: unable to access 'https://github.com/x/': Could not resolve proxy: Temporary failure in name resolution
LOCAL|error: Your local changes to the following files would be overwritten by merge:
LOCAL|fatal: Not possible to fast-forward, aborting.
LOCAL|hint: You have divergent branches and need to specify how to reconcile them.
CASES
  if [[ $_UP_OK -eq 1 ]]; then
    pass "update.sh distinguishes an unreachable remote from a dirty worktree"
  else
    fail "update.sh's diagnosis pattern misclassifies real git messages"
  fi
fi
# And the wait must be bounded, or "minutes of stall" survives the fix.
if grep -q 'http.lowSpeedLimit' "$REPO_DIR/scripts/update.sh"; then
  pass "update.sh bounds the git pull wait (lowSpeedLimit/Time)"
else
  fail "update.sh can still hang for minutes on an unreachable remote"
fi

# bootstrap must not hit the PyPI index when every pin is already installed.
# The old line was an unconditional `pip install -q -U huggingface_hub -r
# requirements.txt`, and the -U on an unpinned name forced an index query every
# run — fatal under `set -euo pipefail` on a closed network, so pre-seeding a
# USB wheelhouse still could not get past step 3 of the only supported
# installer. And it fired AFTER the llama.cpp build had burned 10-40 minutes.
echo ""
echo "bootstrap python-dep guard:"
_BD_FN="$(sed -n '/^ob_python_deps_satisfied()/,/^}$/p' "$REPO_DIR/bootstrap.sh")"
if [[ -z "$_BD_FN" ]]; then
  fail "bootstrap.sh has no ob_python_deps_satisfied guard"
else
  # A pin that IS installed must not be reported missing. Asserted against a
  # SYNTHETIC requirements file rather than the ambient environment: CI
  # installs agents/requirements.txt but NOT huggingface_hub, so "is this box
  # fully satisfied?" is not a property that holds everywhere — and a test
  # that depends on what happens to be installed is a test that fails
  # somewhere else, which is exactly how this one first broke.
  _BD_OK="$(mktemp -d)"
  mkdir -p "$_BD_OK/agents"
  _BD_VER="$(python3 -c 'import importlib.metadata as m; print(m.version("pytest"))' 2>/dev/null || true)"
  if [[ -z "$_BD_VER" ]]; then
    pass "satisfied-pin check skipped (pytest not installed here)"
  else
    printf 'pytest==%s\n' "$_BD_VER" > "$_BD_OK/agents/requirements.txt"
    _BD_OUT="$(bash -c "REPO_DIR='$_BD_OK'
$_BD_FN
ob_python_deps_satisfied" 2>/dev/null || true)"
    if grep -q 'pytest' <<< "$_BD_OUT"; then
      fail "the guard reported an INSTALLED pin as missing: $_BD_OUT"
    else
      pass "the guard does not report an installed pin as missing"
    fi
  fi
  rm -rf "$_BD_OK"
  # unsatisfied: a synthetic requirements file naming something impossible
  _BD_TMP="$(mktemp -d)"
  mkdir -p "$_BD_TMP/agents"
  printf 'openai==3.9.0\ndefinitely-not-installed-xyz==1.2.3\n' \
    > "$_BD_TMP/agents/requirements.txt"
  _BD_OUT="$(bash -c "REPO_DIR='$_BD_TMP'
$_BD_FN
ob_python_deps_satisfied" 2>/dev/null || true)"
  if grep -q 'definitely-not-installed-xyz' <<< "$_BD_OUT"; then
    pass "the guard names what is missing instead of silently skipping"
  else
    fail "the guard did not report a missing pin: $_BD_OUT"
  fi
  rm -rf "$_BD_TMP"
fi
# The failure message has to teach the offline path, or the guard just moves
# the dead end one line later. The recipe it teaches is now pydeps.sh (which
# hash-verifies the wheelhouse at both ends) rather than a raw
# `pip --no-index --find-links`, so the assertion is on the INTENT — and it
# goes further than the old one did: every command the message names must
# actually be a subcommand pydeps.sh implements. A recipe that points at a
# verb the script does not have is worse than no recipe.
if grep -q 'pydeps.sh wheelhouse' "$REPO_DIR/bootstrap.sh"; then
  pass "a failed pip install prints the pre-staged-wheelhouse recipe"
  _VERBS="$(grep -oE 'pydeps\.sh [a-z]+' "$REPO_DIR/bootstrap.sh" | awk '{print $2}' | sort -u)"
  _MISSING=""
  for _v in $_VERBS; do
    grep -qE "^  $_v\)" "$REPO_DIR/scripts/pydeps.sh" || _MISSING="$_MISSING $_v"
  done
  if [[ -z "$_MISSING" ]]; then
    pass "every pydeps.sh verb bootstrap recommends exists ($(echo "$_VERBS" | tr '\n' ' '))"
  else
    fail "bootstrap recommends pydeps.sh verbs that do not exist:$_MISSING"
  fi
else
  fail "pip failure gives no offline recipe"
fi

# doctor must notice a certificate that is about to expire. Renewal goes
# THROUGH tailscale's coordination server, so on a closed network a cached
# cert simply runs out — and nothing checked (no `tailscale cert` call
# anywhere, no expiry probe). Structural assertions: the check exists, it
# reads the LIVE port (no root), and it has all three outcomes.
echo ""
echo "doctor certificate expiry:"
_DR="$REPO_DIR/scripts/doctor.sh"
if grep -q 'openssl x509 -noout -enddate' "$_DR"; then
  pass "doctor reads the served certificate's expiry"
else
  fail "doctor never checks certificate expiry"
fi
if grep -q '/var/lib/tailscale' "$_DR"; then
  fail "doctor reads tailscaled's cert store (needs root); probe the live port instead"
else
  pass "doctor probes the live port rather than a root-only cert store"
fi
if grep -q 'EXPIRED' "$_DR" && grep -q 'renews at 30' "$_DR"; then
  pass "doctor distinguishes already-expired from expiring-soon"
else
  fail "doctor's expiry check has no expired/expiring distinction"
fi

# doctor's beast-chat row printed "reads=?, ? running session(s)" on EVERY
# rig. Two causes, both in this block: it probed /api/chat/health with no
# credential (the route deliberately answers an unidentified caller with
# exactly {"status":"ok"} so session counts are not a free map of the box), and
# its reads pattern was [a-z]-only while the server reports the hyphenated
# "any-identified". The consequence was not just a cosmetic "?": the
# no-allowlist WARNING below it compared against "open" and could never fire,
# so a rig where every tailnet login can read every session said nothing.
echo ""
echo "doctor beast-chat row:"
_DC="$REPO_DIR/scripts/doctor.sh"
if grep -q 'chat-local.token' "$_DC"; then
  pass "doctor presents the locality token to the chat health route"
else
  fail "doctor probes chat health unauthenticated — the detail fields stay empty"
fi
if grep -q 'header = "X-OpenBeast-Local' "$_DC"; then
  pass "the chat token goes through a --config file, not argv (ps is world-readable)"
else
  fail "the chat token may be passed in argv"
fi
# The extraction patterns must actually match the server's real payload. Read
# them OUT of doctor.sh so this cannot drift from the code it checks.
_DC_BODY='{"status":"ok","port":3003,"sessions_dir":"/x","running":2,"sessions":7,"reads":"any-identified","devices":false,"streams":1,"login":"local"}'
_DC_READS_PAT="$(grep -oE "'\"reads\":\"\[a-z-\]\*\"'" "$_DC" | head -1 | tr -d "'" || true)"
if [[ -z "$_DC_READS_PAT" ]]; then
  fail "could not find doctor's reads pattern"
else
  _got="$(echo "$_DC_BODY" | grep -o "$_DC_READS_PAT" | cut -d'"' -f4)"
  if [[ "$_got" == "any-identified" ]]; then
    pass "doctor's reads pattern matches the server's real value (any-identified)"
  else
    fail "doctor's reads pattern extracted '$_got' from the real payload"
  fi
fi
_got_run="$(echo "$_DC_BODY" | grep -o '"running":-\?[0-9]*' | cut -d: -f2)"
if [[ "$_got_run" == "2" ]]; then
  pass "doctor's running-count pattern matches the server's real payload"
else
  fail "doctor's running pattern extracted '$_got_run'"
fi
if grep -q 'any-identified' "$_DC"; then
  pass "the no-allowlist warning compares against the value the server sends"
else
  fail "doctor still compares reads against a value the server never sends"
fi

# The GPU lease (docs/BEAST_CAMPAIGN_PLAN.md P0). Nothing on this box has ever
# claimed the GPU, and the cost is documented: six parallel build agents ran
# inside a measurement's window on 2026-09-14 and contaminated 5 eval units.
# Those agents did not IGNORE a lease — they had nothing to consult.
echo ""
echo "Hash-pinned python closure:"
if [[ -x "$REPO_DIR/scripts/pydeps.sh" ]]; then
  pass "pydeps.sh exists and is executable"
else
  fail "scripts/pydeps.sh missing or not executable"
fi
if [[ -f "$REPO_DIR/agents/requirements.lock" ]]; then
  pass "agents/requirements.lock is committed"
else
  fail "no committed lock — only 4 direct versions are pinned, and no content"
fi
# OFFLINE. `verify` reads two files and hashes nothing over the network, which
# is what makes it safe to run in CI and on a closed box.
_PD_OUT="$(cd "$REPO_DIR" && ./scripts/pydeps.sh verify 2>&1 || true)"
if grep -q ': OK' <<< "$_PD_OUT"; then
  pass "the lock covers every direct pin at the same version"
else
  fail "lock verify: $(head -3 <<< "$_PD_OUT" | tr '\n' ' ')"
fi
# The closure must be bigger than the direct pins, or the lock is just
# requirements.txt with extra steps.
_PD_N=$(grep -cE '^[A-Za-z0-9][A-Za-z0-9._-]*==' "$REPO_DIR/agents/requirements.lock" || echo 0)
_PD_D=$(grep -cE '^[A-Za-z0-9][A-Za-z0-9._-]*==' "$REPO_DIR/agents/requirements.txt" || echo 0)
if [[ "$_PD_N" -gt "$_PD_D" ]]; then
  pass "the lock pins $_PD_N packages where requirements.txt pins $_PD_D"
else
  fail "the lock pins $_PD_N packages — it is not a closure"
fi
# Every pin must carry at least one hash: pip refuses the WHOLE file over one
# unhashed line, so a partial lock is not a weaker lock, it is no lock.
if grep -qE '^\s*--hash=sha256:[0-9a-f]{64}' "$REPO_DIR/agents/requirements.lock"; then
  pass "the lock carries sha256 hashes"
else
  fail "the lock has no hashes — --require-hashes would refuse it"
fi
# A committed generated file must not carry one machine's paths.
if grep -qE '/home/|/usr/lib/python' "$REPO_DIR/agents/requirements.lock"; then
  fail "the committed lock embeds an absolute path from the box that made it"
else
  pass "the lock carries no machine-specific path"
fi
# CI must install FROM the lock — that is the only place the cross-platform
# claim (a 3.14-resolved closure installing on 3.12) is actually tested.
if grep -q 'require-hashes -r agents/requirements.lock' "$REPO_DIR/.github/workflows/ci.yml"; then
  pass "CI installs from the lock with hashes enforced"
else
  fail "CI does not install from the lock — its cross-platform claim is untested"
fi
if grep -q 'pip-audit -r agents/requirements.lock' "$REPO_DIR/.github/workflows/pr-quality.yml"; then
  pass "the vulnerability audit covers the whole closure, not just the 4 direct pins"
else
  fail "pip-audit only sees requirements.txt — 39 of 43 packages are unaudited"
fi

echo ""
echo "beast-lang L1 (toolchain introspection):"
_LI="$REPO_DIR/scripts/lang-introspect.sh"
if [[ -x "$_LI" ]]; then
  pass "lang-introspect.sh exists and is executable"
else
  fail "scripts/lang-introspect.sh missing or not executable"
fi
_LI_OUT="$(cd "$REPO_DIR" && timeout 120 ./scripts/lang-introspect.sh list 2>&1 || true)"
if grep -q 'lang' <<< "$_LI_OUT" && grep -qE 'swift' <<< "$_LI_OUT"; then
  pass "list names every probe, including the one with no toolchain here"
else
  fail "lang-introspect.sh list did not report: $(head -2 <<< "$_LI_OUT")"
fi
# A language this box cannot build must be reported as such, never rendered
# as an authoritative blank — the whole point of the UNVERIFIABLE tier.
if grep -qE 'swift.*(no toolchain|not installed)' <<< "$_LI_OUT"; then
  pass "a language with no toolchain is named as such, not silently omitted"
else
  fail "swift is not reported as toolchain-less: $_LI_OUT"
fi
_LI_BAD="$(cd "$REPO_DIR" && ./scripts/lang-introspect.sh probe cobol 2>&1 || true)"
if grep -q 'no probe for' <<< "$_LI_BAD"; then
  pass "an unknown language is refused, not invented"
else
  fail "probe cobol did not refuse: $_LI_BAD"
fi
# The generated artifacts are per-rig state (a full probe costs ~0.2s), so
# they must NOT be committed — a machine's toolchain inventory is not source.
# Ask about a path INSIDE the directory, not the directory. The pattern ends
# in a slash so it matches directories only, and `git check-ignore` cannot
# tell that a path which does not EXIST is a directory — so the directory form
# passed on this box (where probes had created it) and failed on CI (where it
# never exists). A file path inside matches the pattern either way.
if git -C "$REPO_DIR" check-ignore -q agents/lang/generated/zig.json 2>/dev/null; then
  pass "agents/lang/generated/ is gitignored (per-rig state, not source)"
else
  fail "generated L1 artifacts are not gitignored — one box's toolchain would be committed"
fi
if git -C "$REPO_DIR" ls-files --error-unmatch agents/lang/generated >/dev/null 2>&1; then
  fail "a generated L1 artifact is tracked in git"
else
  pass "no generated L1 artifact is tracked"
fi

echo ""
echo "Orphaned-stack pid discipline:"
# [17] In the orphaned-stack state (supervisor SIGKILLed, EXIT trap never ran)
# a fresh start.sh spawned a replacement that cannot bind the port, wrote its
# pid OVER the pidfile, then deleted the file when the probe failed — erasing
# the live server's recorded pid, after which healthcheck.sh --restart could
# never find or kill the wedged orphan and looped on an unbindable
# replacement every 5 minutes. The spawn must be guarded, not the delete.
for _svc in chat artifact; do
  _blk="$(python3 - "$REPO_DIR/start.sh" "$_svc" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
svc = sys.argv[2]
flag = "BEAST_CHAT" if svc == "chat" else "BEAST_ARTIFACT"
i = src.index('if [[ "${%s:-false}" == "true" ]]; then' % flag)
# to the end of that top-level block
j = src.index("\nfi\n", src.index("$RUN_DIR/%s.pid" % svc, i))
print(src[i:j])
PY
)"
  # the pidfile write must be preceded by a liveness guard on that same file
  if grep -q "_pid_alive \"\$RUN_DIR/$_svc.pid\"" <<< "$_blk"; then
    pass "start.sh guards the $_svc spawn on its own pidfile"
  else
    fail "start.sh spawns $_svc without checking whether one is already live"
  fi
  _guard_at="$(grep -n "_pid_alive \"\$RUN_DIR/$_svc.pid\"" <<< "$_blk" | head -1 | cut -d: -f1)"
  _write_at="$(grep -n "> \"\$RUN_DIR/$_svc.pid\"" <<< "$_blk" | head -1 | cut -d: -f1)"
  if [[ -n "$_guard_at" && -n "$_write_at" && $_guard_at -lt $_write_at ]]; then
    pass "start.sh checks for a live $_svc BEFORE overwriting its pidfile"
  else
    fail "start.sh writes $_svc.pid at line $_write_at, guard at ${_guard_at:-none}"
  fi
done
# and the orphan with no recorded pid must still be reapable, path-anchored so
# a sibling worktree's server is never touched (healthcheck.sh's own rule)
if grep -q 'pkill -f "\$REPO_DIR/agents/artifact_server.py"' "$REPO_DIR/scripts/healthcheck.sh"; then
  pass "healthcheck can reap an unrecorded beast-artifact orphan"
else
  fail "a beast-artifact orphan with no pidfile is unreapable — restart loops forever"
fi
# Every pkill of artifact_server must be path-anchored. Checking for the
# absence of the string "artifact_server" after pkill would flag the anchored
# line too, so the assertion is per-line: each one must name $REPO_DIR.
_BARE=0
while IFS= read -r _ln; do
  _t="${_ln#"${_ln%%[![:space:]]*}"}"       # strip leading whitespace
  [[ "$_t" == \#* ]] && continue            # a COMMENT about pkill is not a pkill
  [[ "$_t" == *artifact_server* ]] || continue
  [[ "$_t" == *'$REPO_DIR'* ]] || _BARE=1
done < <(grep 'pkill' "$REPO_DIR/scripts/healthcheck.sh" || true)
if [[ $_BARE -eq 0 ]]; then
  pass "every pkill of artifact_server is path-anchored (never a sibling worktree)"
else
  fail "healthcheck reaps artifact by a BARE pattern — that reaps sibling worktrees too"
fi

echo ""
echo "GPU lease:"
_GL="$REPO_DIR/scripts/gpu-lease.sh"
_GL_DIR="$(mktemp -d)"
# A high VRAM floor so the real card (which may be busy) cannot skew the test.
_gl() { OPENBEAST_RUN_DIR="$_GL_DIR" OPENBEAST_LEASE_VRAM_FLOOR=99999999 "$_GL" "$@"; }
if [[ ! -x "$_GL" ]]; then
  fail "scripts/gpu-lease.sh missing or not executable"
else
  # A bare acquire must be held by the CALLER, or it is stale the instant it
  # returns — which is how the first version shipped, with a usage example
  # promising otherwise.
  # export, not a bare assignment: the script reads these from the
  # ENVIRONMENT, and an assignment on its own line only sets a shell variable
  # — which is how this test first "failed" against a perfectly good lease.
  _GL_OUT="$(bash -c "export OPENBEAST_RUN_DIR='$_GL_DIR' OPENBEAST_LEASE_VRAM_FLOOR=99999999
    '$_GL' acquire probe >/dev/null && '$_GL' status | head -1" 2>&1 || true)"
  if grep -q '^HELD' <<< "$_GL_OUT"; then
    pass "a bare acquire is held by the caller and survives the call"
  else
    fail "acquire did not produce a live lease: $_GL_OUT"
  fi
  rm -f "$_GL_DIR/gpu.lease"

  # A LIVE holder must be refused, with a distinguishable exit code.
  bash -c "export OPENBEAST_RUN_DIR='$_GL_DIR' OPENBEAST_LEASE_VRAM_FLOOR=99999999
           '$_GL' run holder -- sleep 4" >/dev/null 2>&1 &
  _GL_BG=$!
  sleep 1
  # Capture the code without letting `set -e` fire: a non-zero exit is the
  # EXPECTED result here, and testing $? after the fact aborts the whole file
  # before the `if` can read it. (Third time today this pattern bit a test of
  # mine — the others were a pipefail-through-grep and a bare assignment.)
  _GL_RC=0
  _gl acquire second >/dev/null 2>&1 || _GL_RC=$?
  if [[ "$_GL_RC" -eq 4 ]]; then
    pass "a live holder is refused (exit 4)"
  else
    fail "the lease was handed out while another process held it (rc=$_GL_RC)"
  fi
  wait "$_GL_BG" 2>/dev/null || true
  # run's EXIT trap must have released it
  _GL_OUT="$(_gl status 2>&1 || true)"
  if grep -q '^FREE' <<< "$_GL_OUT"; then
    pass "run releases the lease when its command exits"
  else
    fail "run leaked the lease: $_GL_OUT"
  fi

  # A RECYCLED pid must not look alive: identity is pid + start time, the
  # lesson agents/sessions.py already carries.
  printf 'pid=%s\nstart=1\nlabel=recycled\nsince=then\n' "$$" > "$_GL_DIR/gpu.lease"
  # Capture, THEN grep. `grep -q` exits on its first match, so acquire takes
  # SIGPIPE writing its next line and `pipefail` reports the whole pipeline
  # as failed — making a passing behaviour look broken. Fourth variant of the
  # same family today (pipefail-through-grep, set -e on an expected non-zero,
  # a bare env assignment, and now SIGPIPE).
  _GL_OUT="$(_gl acquire after-recycle 2>&1 || true)"
  if grep -q 'stale' <<< "$_GL_OUT"; then
    pass "a recycled pid does not make a dead lease look live"
  else
    fail "a lease was treated as live on pid alone: $_GL_OUT"
  fi
  rm -f "$_GL_DIR/gpu.lease"

  # It must refuse to claim a card somebody else is quietly using — and that
  # has to be testable on a box with NO GPU at all. The first version read
  # the real card, so it passed here (where 27 GB was in use) and failed on
  # CI (where used=0 and no floor can trigger). Stub the reading instead, the
  # same way the preflight test stubs curl.
  _GL_BIN="$(mktemp -d)"
  printf '#!/bin/bash\necho 8000\n' > "$_GL_BIN/nvidia-smi"
  chmod +x "$_GL_BIN/nvidia-smi"
  _GL_OUT="$(PATH="$_GL_BIN:$PATH" OPENBEAST_RUN_DIR="$_GL_DIR" \
             OPENBEAST_LEASE_VRAM_FLOOR=1000 "$_GL" acquire greedy 2>&1 || true)"
  if grep -qE 'refusing|already allocated' <<< "$_GL_OUT"; then
    pass "refuses to claim a GPU that has an unclaimed user"
  else
    fail "claimed a GPU already in use: $_GL_OUT"
  fi
  # …and takes it when the card is genuinely idle.
  printf '#!/bin/bash\necho 12\n' > "$_GL_BIN/nvidia-smi"
  rm -f "$_GL_DIR/gpu.lease"
  _GL_OUT="$(PATH="$_GL_BIN:$PATH" OPENBEAST_RUN_DIR="$_GL_DIR" \
             OPENBEAST_LEASE_VRAM_FLOOR=1000 "$_GL" acquire idle 2>&1 || true)"
  if grep -q 'lease acquired' <<< "$_GL_OUT"; then
    pass "takes the lease when the GPU is idle"
  else
    fail "refused an idle GPU: $_GL_OUT"
  fi
  rm -rf "$_GL_BIN"
fi
rm -rf "$_GL_DIR"

# The era assertion. Rows either side of a change to the six hashed files are
# not comparable, and until now era was a thing people remembered — the
# 2026-09-08 plan put the churn floor before a git pull and the cells it
# calibrates after it, and review did not catch it because it was nobody's job.
echo ""
echo "Eval era assertion:"
_EE="$REPO_DIR/scripts/eval-era.sh"
if [[ ! -x "$_EE" ]]; then
  fail "scripts/eval-era.sh missing or not executable"
else
  _EE_NOW="$("$_EE" 2>/dev/null || true)"
  if [[ "$_EE_NOW" =~ ^[0-9a-f]{16}$ ]]; then
    pass "eval-era.sh prints a 16-hex era ($_EE_NOW)"
  else
    fail "eval-era.sh printed '$_EE_NOW'"
  fi
  if "$_EE" --check "$_EE_NOW" >/dev/null 2>&1; then
    pass "--check passes against the current era"
  else
    fail "--check failed against the era it just printed"
  fi
  _EE_OUT="$("$_EE" --check deadbeefdeadbeef 2>&1 || true)"
  if grep -q 'ERA MOVED' <<< "$_EE_OUT" && grep -q 'runner.py' <<< "$_EE_OUT"; then
    pass "--check fails on a moved era and names the hashed inputs"
  else
    fail "--check did not report a moved era usefully: $_EE_OUT"
  fi
fi

# --- Summary ---
echo ""
echo "================================"
if [[ $SKIP -gt 0 ]]; then
  echo "Results: $PASS passed, $FAIL failed, $SKIP skipped"
else
  echo "Results: $PASS passed, $FAIL failed"
fi
echo "================================"

[[ $FAIL -eq 0 ]]
