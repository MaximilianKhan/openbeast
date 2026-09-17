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
# FOUNDATIONAL — checked first, because every other check below runs scripts
# that source conf.sh.
echo "Config library invariants:"
# conf.sh is SOURCED by start.sh, doctor.sh, update.sh and bundle.sh, each of
# which then parses "$@". A `set --` inside it replaces the sourcing shell's
# positional parameters — which happened: a fix for the inline-comment
# fail-open used `set -- $OFFLINE`, and `bundle.sh sign` silently became
# `bundle.sh false`. The suite caught it by aborting. Pin the invariant.
_CF_OUT="$(REPO_DIR="$REPO_DIR" bash -c \
    'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1; printf "%s|%s" "${1:-}" "$#"' \
    -- alpha beta 2>/dev/null || true)"
if [[ "$_CF_OUT" == "alpha|2" ]]; then
  pass "sourcing conf.sh leaves the caller's positional parameters intact"
else
  fail "conf.sh clobbers \$@ — every CLI that sources it loses its arguments (got '$_CF_OUT')"
fi


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
# NOTE on the `|| true` on every grep-to-variable below: under
# `set -euo pipefail` a grep that matches nothing fails the PIPELINE, and the
# assignment then ABORTS the whole suite mid-run with no summary line — so a
# renamed string would look like a crash instead of a failed check, and every
# check after it would silently not run. Each caller treats an empty value as
# a named failure, which is the behaviour we actually want.
echo "Offline bundle:"
# The docs and every failure message tell an operator to create these IN the
# repo root. Un-ignored, that is 43+ untracked wheels (or a multi-GB bundle) in
# `git status`, and a hurried `git add -A` on a closed box commits them.
for _art in wheels/x.whl wheelhouse/x.whl bundle/MANIFEST.json; do
  if git -C "$REPO_DIR" check-ignore -q "$_art" 2>/dev/null; then
    pass "$(dirname "$_art")/ is gitignored (per-rig transfer artifact)"
  else
    fail "$(dirname "$_art")/ is not gitignored — following our own instructions dirties the repo"
  fi
done
# ...but the LOCK is source and must stay tracked, or an offline box has
# nothing to verify a wheelhouse against.
if git -C "$REPO_DIR" ls-files --error-unmatch agents/requirements.lock >/dev/null 2>&1; then
  pass "agents/requirements.lock is tracked (it is the source of truth)"
else
  fail "the lock is not tracked — a closed box would have nothing to verify against"
fi
if [[ -x "$REPO_DIR/scripts/bundle.sh" ]]; then
  pass "bundle.sh exists and is executable"
else
  fail "scripts/bundle.sh missing or not executable"
fi
# A bundle is INSTALLED — it writes a source tree, installs packages, loads
# images and places a weight — so "verify before using any of it" is the whole
# safety property. Assert the order, not just the presence.
_BN="$REPO_DIR/scripts/bundle.sh"
_V_AT=$(grep -n 'verifying the bundle before using' "$_BN" | head -1 | cut -d: -f1 || true)
_L_AT=$(grep -n 'docker load' "$_BN" | head -1 | cut -d: -f1 || true)
_I_AT=$(grep -n 'pydeps.sh" install --from' "$_BN" | head -1 | cut -d: -f1 || true)
if [[ -n "$_V_AT" && -n "$_L_AT" && -n "$_I_AT" && $_V_AT -lt $_L_AT && $_V_AT -lt $_I_AT ]]; then
  pass "install verifies the manifest BEFORE loading images or installing wheels"
else
  fail "install uses bundle contents before verifying them (verify@${_V_AT:-none} load@${_L_AT:-none} pip@${_I_AT:-none})"
fi
# The digest trap: compose pins by REGISTRY digest, which save/load cannot
# carry, so install must rewrite the reference to the content ID — and must
# keep the original, because the rewrite loses digest pinning.
if grep -q 'docker-compose.yml.pre-bundle' "$_BN"; then
  pass "the compose rewrite keeps the original (it is reversible)"
else
  fail "install rewrites docker-compose.yml with no way back"
fi
if grep -qE 'docker inspect --format .\{\{\.Id\}\}' "$_BN"; then
  pass "install verifies the LOADED image against the recorded content ID"
else
  fail "install loads images without checking what it loaded"
fi
# Weights are opt-in; the manifest must SAY they were skipped rather than let
# a reader find out at install time.
if grep -q 'skipped "weights' "$_BN"; then
  pass "a bundle without weights records that it has none"
else
  fail "a weightless bundle does not say so"
fi
# build must refuse to run on the closed box — it is the connected side.
if grep -A2 'ob_offline && die' "$_BN" | grep -q 'BUILT on a connected box'; then
  pass "build refuses to run with OFFLINE=true (it is the connected-side step)"
else
  fail "build does not refuse on a closed network"
fi
# The lock travels with the wheels but NOT inside wheels/ — pydeps audits that
# directory for files the lock does not name, so a copy in there fails its own
# audit. (It did, during development.)
if grep -q 'DIR/meta/requirements.lock' "$_BN"; then
  pass "the bundled lock lives outside wheels/ (it would fail its own audit inside)"
else
  fail "the bundled lock is inside the wheelhouse it is meant to describe"
fi
if grep -q "bundle's lock differs" "$_BN"; then
  pass "install refuses a bundle whose lock does not match this checkout"
else
  fail "install would silently place a closure this checkout does not pin"
fi
# Hashes are integrity, a signature is authenticity, and the distinction is
# load-bearing: anyone who can write to the medium can rebuild the manifest to
# match their own payload, and every hash would then verify. Demonstrated
# during development by doing exactly that.
if grep -q 'ssh-keygen -Y sign' "$_BN"; then
  pass "bundle.sh can sign a manifest (ssh keys, no PKI to stand up)"
else
  fail "no signing path — a rebuilt manifest would be indistinguishable"
fi
# NO key material in the repo, ever.
if grep -qE 'BEGIN (OPENSSH|RSA|EC) PRIVATE KEY' "$_BN" "$REPO_DIR"/scripts/lib/bundle_manifest.py 2>/dev/null; then
  fail "a private key is embedded in the bundle tooling"
else
  pass "no key material lives in the repo (the operator supplies both halves)"
fi
# --key means authenticity was REQUIRED, so a missing signature must fail.
if grep -q 'authenticity was REQUIRED' "$_BN"; then
  pass "--key on an unsigned bundle is refused, not shrugged at"
else
  fail "--key on an unsigned bundle would silently fall back to hashes only"
fi
# The signature must be checked BEFORE the hashes: the manifest is what names
# the hashes, so checking them first checks a document against itself.
_S_AT=$(grep -n '_check_signature "\$DIR"' "$_BN" | head -1 | cut -d: -f1 || true)
_H_AT=$(grep -n '"\$PY" "\$HELPER" verify "\$DIR"' "$_BN" | head -1 | cut -d: -f1 || true)
if [[ -n "$_S_AT" && -n "$_H_AT" && $_S_AT -lt $_H_AT ]]; then
  pass "the signature is checked before the hashes it vouches for"
else
  fail "hashes are checked before the signature (sig@${_S_AT:-none} hash@${_H_AT:-none})"
fi
# A signature is only valid in its own namespace. The grep below proves the
# constant EXISTS; it cannot prove ssh-keygen enforces it, and asserting a
# constant against itself is the shape this suite keeps finding. The
# behavioural half is in the signature block further down, which signs in a
# foreign namespace and requires the verify to refuse.
if grep -q 'SIG_NS="openbeast-bundle"' "$_BN"; then
  pass "a signature namespace is set (enforcement is asserted behaviourally below)"
else
  fail "signatures have no namespace — one made for another purpose could be replayed"
fi
# BEHAVIOURAL, and it exists because the structural checks above all passed
# while the feature FAILED OPEN. `ssh-keygen -Y find-principals` only matches
# the signature's embedded key — it does NOT check the signature over the
# content — so the no---identity path (the DEFAULT when an operator passes
# --key without naming a signer) accepted a manifest modified after signing.
# Measured: tampered + find-principals -> rc=0; tampered + verify -I -> 255.
# The lesson generalises: I tested the strict path and shipped the loose one.
if command -v ssh-keygen >/dev/null 2>&1; then
  _SG="$(mktemp -d)"
  ssh-keygen -t ed25519 -N '' -C t -f "$_SG/k" </dev/null >/dev/null 2>&1
  mkdir -p "$_SG/b/meta"
  printf 'x\n' > "$_SG/b/meta/x"
  ( cd "$REPO_DIR" && python3 scripts/lib/bundle_manifest.py write "$_SG/b" \
      --component meta:meta >/dev/null 2>&1 )
  printf 'builder %s\n' "$(cat "$_SG/k.pub")" > "$_SG/allowed"
  # NOT an unguarded subshell: `( ... )` that fails aborts the whole suite
  # under `set -e`, with no named failure and every later check skipped. That
  # is exactly what happened when a conf.sh change clobbered bundle.sh's
  # arguments — the suite died here instead of reporting anything.
  ( cd "$REPO_DIR" && ./scripts/bundle.sh sign "$_SG/b" --key "$_SG/k" >/dev/null 2>&1 ) \
    || fail "bundle.sh sign failed outright (see: ./scripts/bundle.sh sign)"
  if [[ -f "$_SG/b/MANIFEST.json.sig" ]]; then
    # good signature, NO --identity: must pass
    if ( cd "$REPO_DIR" && ./scripts/bundle.sh verify "$_SG/b" --key "$_SG/allowed" >/dev/null 2>&1 ); then
      pass "a good signature verifies without --identity"
    else
      fail "a good signature is rejected when --identity is omitted"
    fi
    # TAMPER the manifest, still no --identity: must FAIL
    python3 - "$_SG/b/MANIFEST.json" <<'PYT'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["repo_commit"] = "ATTACKER"
json.dump(d, open(p, "w"), indent=2, sort_keys=True)
PYT
    if ( cd "$REPO_DIR" && ./scripts/bundle.sh verify "$_SG/b" --key "$_SG/allowed" >/dev/null 2>&1 ); then
      fail "FAIL-OPEN: a tampered manifest verified when --identity was omitted"
    else
      pass "a tampered manifest is refused even without --identity"
    fi
    # NAMESPACE ENFORCEMENT, behaviourally: an operator's signature over the
    # same bytes for a DIFFERENT purpose must not be replayable as a bundle
    # signature. (Measured directly: ssh-keygen -Y verify answers
    # "namespace does not match", rc=255.)
    ( cd "$REPO_DIR" && python3 - "$_SG/b/MANIFEST.json" <<'PYRESTORE'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["repo_commit"] = "restored"
json.dump(d, open(p, "w"), indent=2, sort_keys=True)
PYRESTORE
    )
    rm -f "$_SG/b/MANIFEST.json.sig"
    ssh-keygen -Y sign -n some-other-purpose -f "$_SG/k" \
      "$_SG/b/MANIFEST.json" </dev/null >/dev/null 2>&1
    if [[ -f "$_SG/b/MANIFEST.json.sig" ]]; then
      if ( cd "$REPO_DIR" && ./scripts/bundle.sh verify "$_SG/b" --key "$_SG/allowed" >/dev/null 2>&1 ); then
        fail "a signature from ANOTHER namespace was accepted as a bundle signature"
      else
        pass "a foreign-namespace signature is refused (no replay)"
      fi
    else
      skip "could not produce a foreign-namespace signature to test with"
    fi
  else
    skip "ssh-keygen present but signing produced no signature"
  fi
  rm -rf "$_SG"
else
  skip "no ssh-keygen — cannot exercise the signature paths"
fi
# A sharded weight travels as a SET. llama.cpp is handed only the first
# shard and finds its siblings, so a serve script names one file while the
# model is three — shipping 1 of 3 produces a bundle that verifies perfectly
# and cannot load the model. scripts/weights.registry has a real 3-shard
# entry today, so this is reachable, not hypothetical.
if grep -q 'of-\[0-9\]{5}\\.gguf\|of-([0-9]{5})' "$_BN"; then
  pass "build expands a sharded weight to the whole set"
else
  fail "build ships whichever shard the serve script names — a partial set cannot load"
fi
# Rebuilding into a directory that already holds a bundle used to leave BOTH
# sets of artifacts; the manifest recorded both, verify passed, and install
# picked one arbitrarily.
if grep -q 'already holds bundle content' "$_BN"; then
  pass "build refuses a dirty target (or --force clears it)"
else
  fail "build into a dirty target leaves stale artifacts the manifest then blesses"
fi
# ...and install must pick the source tarball the MANIFEST names, since the
# manifest is what the signature covers.
if grep -q '_want_commit' "$_BN"; then
  pass "install selects the source tarball by the recorded commit"
else
  fail "install picks a source tarball arbitrarily (head -1)"
fi
# The offline prebuilt-UI flag belongs in the SHARED cmake function, or
# update.sh's rebuild — the path advertised as the offline work — re-arms the
# 11-minute fetch that bootstrap avoids.
if grep -q 'LLAMA_USE_PREBUILT_UI=OFF' "$REPO_DIR/scripts/lib/hardware.sh"; then
  pass "the offline cmake flag lives in the shared ob_cmake_flags"
else
  fail "the offline cmake flag is inlined in one caller — bootstrap and update.sh will drift"
fi
# update.sh must gate the network call, not the branch that only prints advice.
if grep -A6 'update_opencode()' "$REPO_DIR/scripts/update.sh" | grep -q 'ob_offline' \
   && grep -B6 'if opencode upgrade' "$REPO_DIR/scripts/update.sh" | grep -q 'ob_offline'; then
  pass "update.sh gates 'opencode upgrade' itself when offline"
else
  fail "update.sh leaves the network call ungated and guards the advice branch instead"
fi

_BN_OUT="$(cd "$REPO_DIR" && ./scripts/bundle.sh show /nonexistent-bundle 2>&1 || true)"
if grep -qiE 'missing|not a bundle' <<< "$_BN_OUT"; then
  pass "show on a non-bundle says so instead of crashing"
else
  fail "show on a non-bundle: $_BN_OUT"
fi

echo ""
echo "OFFLINE (closed network):"
# The closed-network review's finding was not that the stack cannot run
# offline — an installed rig serves fine with no internet. It was that nothing
# could be TOLD there is no internet, so every install/update path stalled on
# a connect timeout and then misdiagnosed the stall. These checks are about
# the telling.
for _v in true TRUE yes 1 on; do
  _got="$(REPO_DIR="$REPO_DIR" OPENBEAST_OFFLINE="$_v" bash -c \
          'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1; ob_offline && echo on || echo off')"
  if [[ "$_got" == "on" ]]; then
    pass "OFFLINE=$_v resolves to on"
  else
    fail "OFFLINE=$_v resolved to $_got"
  fi
done
# PRESENCE, not truthiness (the LANG_PACKS precedent): a typo must not
# silently enable a mode that refuses installs.
for _v in maybe off false 0 ''; do
  _got="$(REPO_DIR="$REPO_DIR" OPENBEAST_OFFLINE="$_v" bash -c \
          'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1; ob_offline && echo on || echo off')"
  if [[ "$_got" == "off" ]]; then
    pass "OFFLINE='$_v' resolves to off (a typo must not enable it)"
  else
    fail "OFFLINE='$_v' resolved to $_got — a typo enabled offline mode"
  fi
done
# ob_offline comes from conf.sh, which bootstrap sources inside
# run_preflight. A call that runs BEFORE that source is "command not found",
# and under `set -euo pipefail` that is fatal — in the installer. Pin the
# order: every top-level use must come after the unconditional run_preflight,
# and any earlier use must live inside a function (called later).
_OB_SRC=$(grep -n 'run_preflight$' "$REPO_DIR/bootstrap.sh" | grep -v '()' | head -1 | cut -d: -f1 || true)
if [[ -n "$_OB_SRC" ]]; then
  pass "bootstrap calls run_preflight at line $_OB_SRC (which sources conf.sh)"
  _OB_BAD=""
  while IFS=: read -r _ln _txt; do
    [[ -n "$_ln" ]] || continue
    # indented uses are inside a function body; those are fine
    [[ "$_txt" =~ ^[[:space:]] ]] && continue
    [[ "$_ln" -gt "$_OB_SRC" ]] || _OB_BAD="$_OB_BAD $_ln"
  done < <(grep -n 'ob_offline' "$REPO_DIR/bootstrap.sh")
  if [[ -z "$_OB_BAD" ]]; then
    pass "no top-level ob_offline call precedes the conf.sh source"
  else
    fail "bootstrap calls ob_offline at top level before conf.sh is sourced (lines:$_OB_BAD)"
  fi
else
  fail "bootstrap no longer calls run_preflight unconditionally"
fi
# The FOURTH fetch — container images — must be refused too. It was not: the
# docker pull loop had no guard, so a closed network stalled on two registry
# pulls in the script that had just refused the other three by name.
# Section-based, not line-distance-based: a -B4 window broke the moment the
# refusal message grew past four lines, which is a property of the prose and
# not of the guard.
if python3 - "$REPO_DIR/bootstrap.sh" <<'PYSEC'
import re, sys
src = open(sys.argv[1]).read()
i = src.find("Frontend images")
j = src.find("# ---- OpenCode", i)
sec = src[i:j] if i >= 0 and j > i else ""
pull = "docker pull -q" in sec
guard = re.search(r"if ob_offline; then", sec) is not None
sys.exit(0 if (pull and guard) else 1)
PYSEC
then
  pass "the container-image pull is refused when offline (the 4th fetch)"
else
  fail "OFFLINE does not stop the image pull — the 'all four refused' claim is false"
fi

# BEHAVIOURAL, not structural: prove the probe does not happen. A stub curl
# that records every invocation is the only way to tell "skipped" from
# "succeeded quickly".
_OF_TMP="$(mktemp -d)"
cat > "$_OF_TMP/curl" <<'CURLSTUB'
#!/bin/bash
echo "$@" >> "$OB_CURL_LOG"
exit 6
CURLSTUB
chmod +x "$_OF_TMP/curl"
: > "$_OF_TMP/calls"
_OF_OUT="$(cd "$REPO_DIR" && PATH="$_OF_TMP:$PATH" OB_CURL_LOG="$_OF_TMP/calls" \
           OPENBEAST_OFFLINE=true OPENBEAST_GPU_BACKEND=cpu \
           ./bootstrap.sh --preflight --minimal 2>&1 | sed 's/\x1b\[[0-9;]*m//g' || true)"
if grep -q 'not probed' <<< "$_OF_OUT"; then
  pass "offline preflight says it did not probe"
else
  fail "offline preflight did not report skipping the probe"
fi
if grep -qE 'github\.com|pypi\.org|huggingface\.co' "$_OF_TMP/calls"; then
  fail "offline preflight called curl against $(tr '\n' ' ' < "$_OF_TMP/calls" | head -c 120)"
else
  pass "offline preflight called curl for NO reachability probe at all"
fi
if grep -q 'network is not reachable' <<< "$_OF_OUT"; then
  fail "offline mode reports the network as UNREACHABLE — it was told it is absent, which is not a fault"
else
  pass "offline mode does not report a fault for a configured absence"
fi
# ...and with OFFLINE off, the probe MUST happen, or the check above would
# pass on a bootstrap that simply never probes.
: > "$_OF_TMP/calls"
(cd "$REPO_DIR" && PATH="$_OF_TMP:$PATH" OB_CURL_LOG="$_OF_TMP/calls" \
   OPENBEAST_OFFLINE=false OPENBEAST_GPU_BACKEND=cpu \
   ./bootstrap.sh --preflight --minimal >/dev/null 2>&1 || true)
if grep -qE 'github\.com|pypi\.org|huggingface\.co' "$_OF_TMP/calls"; then
  pass "with OFFLINE off the reachability probe still runs"
else
  fail "the probe never runs at all — the offline check above proves nothing"
fi
rm -rf "$_OF_TMP"
# Every fetch a closed network cannot do must be refused BY NAME, with a
# recipe. A guard that dies without saying how to proceed just moves the dead
# end one line later.
for _need in 'llama.cpp' 'wheelhouse' 'WEIGHT_FILE'; do
  if grep -q "OFFLINE=true and" "$REPO_DIR/bootstrap.sh" \
     && grep -A6 "OFFLINE=true and" "$REPO_DIR/bootstrap.sh" | grep -q "$_need"; then
    pass "bootstrap refuses the $_need fetch with a recipe"
  else
    fail "bootstrap has no offline refusal naming $_need"
  fi
done
if grep -q 'LLAMA_USE_PREBUILT_UI=OFF' "$REPO_DIR/bootstrap.sh"; then
  pass "offline builds skip the prebuilt-UI fetch (it cannot succeed there)"
else
  fail "offline builds still attempt the prebuilt-UI fetch (~11 min stall, then fail)"
fi
# ...but ONLY offline: the default build follows upstream (Max, 2026-09-15).
if grep -B8 'LLAMA_USE_PREBUILT_UI=OFF' "$REPO_DIR/bootstrap.sh" | grep -q 'ob_offline'; then
  pass "the prebuilt-UI flag is gated on OFFLINE, not applied unconditionally"
else
  fail "the prebuilt-UI flag is unconditional — that changes the default build"
fi
if grep -q 'pull never' "$REPO_DIR/start.sh"; then
  pass "start.sh uses --pull never when offline"
else
  fail "start.sh lets compose reach a registry on a closed network"
fi
# Per FUNCTION, not per message string: the first version of this check
# grepped for the words "not pulling", and rewording the message broke it
# while the behaviour was intact. What matters is that each stage that does
# network work has an ob_offline branch.
for _fn in update_llama update_images update_python update_opencode; do
  if python3 - "$REPO_DIR/scripts/update.sh" "$_fn" <<'PYFN'
import sys
src = open(sys.argv[1]).read()
fn = sys.argv[2]
i = src.find(fn + "() {")
if i < 0:
    sys.exit(1)
# to the next top-level function definition (or EOF)
j = src.find("\n}\n", i)
body = src[i:j if j > i else len(src)]
sys.exit(0 if "ob_offline" in body else 1)
PYFN
  then
    pass "update.sh: $_fn has an offline branch"
  else
    fail "update.sh: $_fn does network work with no offline branch"
  fi
done

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
if grep -qE 'pkill -f "\$\(_ob_ere "\$REPO_DIR/agents/artifact_server\.py"\)"' \
        "$REPO_DIR/scripts/healthcheck.sh"; then
  pass "healthcheck can reap an unrecorded beast-artifact orphan (ERE-quoted)"
else
  fail "a beast-artifact orphan with no pidfile is unreapable — restart loops forever"
fi
# pkill -f takes an EXTENDED REGEX, not a literal, so a path-anchored pattern
# is only anchored if the path is QUOTED for ERE use. Measured: a `+` in the
# repo path makes the pattern fail to match its own process (the reap silently
# does nothing) and a `.` makes it match other paths (the sibling-worktree
# reap the comments call impossible). All four call sites must quote.
_HC_BARE=0
while IFS= read -r _ln; do
  _t="${_ln#"${_ln%%[![:space:]]*}"}"
  [[ "$_t" == \#* ]] && continue
  [[ "$_t" == *'$REPO_DIR'* ]] || continue
  [[ "$_t" == *'_ob_ere'* ]] || _HC_BARE=1
done < <(grep 'pkill -f' "$REPO_DIR/scripts/healthcheck.sh" || true)
if [[ $_HC_BARE -eq 0 ]]; then
  pass "every path-anchored pkill is ERE-quoted (a '+' or '.' in the repo path is safe)"
else
  fail "a pkill pattern uses \$REPO_DIR unquoted — a regex metacharacter in the path breaks it both ways"
fi
# The [17] guard deliberately leaves a live chat/artifact server alone, so its
# pidfile belongs to another process — and cleanup() must not delete it, or the
# guard's whole purpose (a reapable recorded pid) is undone on exit.
if grep -qE 'CHAT_OWNED|ARTIFACT_OWNED' "$REPO_DIR/start.sh"; then
  pass "cleanup only removes the pidfiles this start.sh actually created"
else
  fail "cleanup deletes chat.pid/artifact.pid unconditionally, including for a server it left alone"
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

# ---------------------------------------------------------------------------
# 2026-09-17 review — the scripts that SIGNAL things, and what they signal
# ---------------------------------------------------------------------------
echo ""
echo "Signalling (review 2026-09-17):"
_RV="$(mktemp -d)"
# Nothing this section starts may outlive it, pass or fail: every stub below
# `exec`s its sleeper (so the recorded pid IS the process — killing a bash
# wrapper orphaned a `sleep 300` that held the suite's stdout open for five
# minutes under `| tee`), and this trap sweeps whatever an abort leaves.
_RV_PIDS=""
trap 'for _p in $_RV_PIDS; do kill "$_p" 2>/dev/null || true; done; rm -rf "${_FB_TMP:-}" "$_RV"' EXIT
# shellcheck disable=SC1091
_rv_proc() { bash -c "source '$REPO_DIR/scripts/lib/proc.sh'; $1"; }

# lib/proc.sh is SOURCED by stop.sh and healthcheck.sh, which parse "$@" — so
# it must define functions and do nothing else (the `set --` lesson above).
_RV_ARGS="$(bash -c "set -- keep these; source '$REPO_DIR/scripts/lib/proc.sh'; echo \"\$*\"")"
if [[ "$_RV_ARGS" == "keep these" ]]; then
  pass "lib/proc.sh leaves the sourcing shell's arguments alone"
else
  fail "sourcing lib/proc.sh clobbered \$@: '$_RV_ARGS'"
fi

# A pidfile is a number on disk and .run/ survives a reboot. The STRANGER here
# is a real live process that merely owns the recorded pid.
sleep 300 & _RV_STRANGER=$!; _RV_PIDS="$_RV_PIDS $!"
if _rv_proc "ob_pid_matches $_RV_STRANGER 'chat_server\\.py'"; then
  fail "ob_pid_matches accepted an unrelated process as chat_server"
else
  pass "a recycled pid is NOT the server it used to be (identity, not liveness)"
fi
if _rv_proc "ob_pid_matches $_RV_STRANGER '^sleep 300'"; then
  pass "…and the same helper does match the process it should (control)"
else
  fail "ob_pid_matches rejected a process whose command line matches"
fi
for _bad in "" 0 1 abc "12 34"; do
  if _rv_proc "ob_pid_matches '$_bad' '.*'"; then
    fail "ob_pid_matches accepted the junk pid '$_bad'"
  fi
done
pass "junk pids (empty, 0, 1, non-numeric) never match"

# stop.sh's _stop_recorded, run for real against that stranger: it must
# survive, and the path fallback must still be reached.
# The harness is a script FILE fed by the environment: pkill -f matches whole
# command lines, so a `bash -c "<text containing the pattern>"` harness is
# itself a match and gets killed by the very sweep it is testing.
{
  echo 'set -euo pipefail'
  echo "source '$REPO_DIR/scripts/lib/proc.sh'"
  sed -n '/^_stop_recorded() {/,/^}/p' "$REPO_DIR/stop.sh"
  echo '_stop_recorded "chat server" "$RV_PIDFILE" "$RV_PATH"'
} > "$_RV/stop_recorded.sh"
echo "$_RV_STRANGER" > "$_RV/chat.pid"
_RV_OUT="$(RV_PIDFILE="$_RV/chat.pid" RV_PATH="$_RV/no/such/agents/chat_server.py" \
           bash "$_RV/stop_recorded.sh" 2>&1 || true)"
if kill -0 "$_RV_STRANGER" 2>/dev/null; then
  pass "stop.sh does not SIGTERM a stranger that inherited a stale chat.pid"
else
  fail "stop.sh killed an unrelated process via a stale pidfile: $_RV_OUT"
fi
if grep -q "was not running" <<< "$_RV_OUT"; then
  pass "…and falls through to the path sweep instead of reporting 'stopped'"
else
  fail "stale pidfile short-circuited the path fallback: $_RV_OUT"
fi
# Control: a process that IS the recorded server is stopped by that pid.
mkdir -p "$_RV/agents"
printf '#!/bin/bash\nexec -a "$0" sleep 300\n' > "$_RV/agents/chat_server.py"; chmod +x "$_RV/agents/chat_server.py"
bash "$_RV/agents/chat_server.py" & _RV_OURS=$!; _RV_PIDS="$_RV_PIDS $!"
sleep 0.3
echo "$_RV_OURS" > "$_RV/chat.pid"
_RV_OUT="$(RV_PIDFILE="$_RV/chat.pid" RV_PATH="$_RV/agents/chat_server.py" \
           bash "$_RV/stop_recorded.sh" 2>&1 || true)"
sleep 0.3
if ! kill -0 "$_RV_OURS" 2>/dev/null && grep -q "stopped (pid $_RV_OURS)" <<< "$_RV_OUT"; then
  pass "the real recorded server IS stopped by its pid (control)"
else
  fail "stop.sh did not stop its own recorded server: $_RV_OUT"
fi
kill "$_RV_STRANGER" 2>/dev/null || true; pkill -P "$_RV_OURS" 2>/dev/null || true

# Every path-anchored pkill/pgrep in BOTH signalling scripts is ERE-quoted.
for _f in stop.sh scripts/healthcheck.sh; do
  _RV_BARE=0
  while IFS= read -r _ln; do
    _t="${_ln#"${_ln%%[![:space:]]*}"}"
    [[ "$_t" == \#* ]] && continue
    [[ "$_t" == *'$REPO_DIR'* || "$_t" == *'$SCRIPT_DIR'* ]] || continue
    [[ "$_t" == *'_ob_ere'* ]] || _RV_BARE=1
  done < <(grep -E 'pkill -f|pgrep -f' "$REPO_DIR/$_f" || true)
  if [[ $_RV_BARE -eq 0 ]]; then
    pass "$_f: every path-anchored pkill/pgrep is ERE-quoted"
  else
    fail "$_f: a pkill/pgrep pattern uses the repo path unquoted"
  fi
done
if grep -vE '^[[:space:]]*#' "$REPO_DIR/scripts/healthcheck.sh" | grep -qE 'pkill -f "llama-server"'; then
  fail "healthcheck still kills llama-server by BARE name (reaps campaigns and sibling worktrees)"
else
  pass "healthcheck never kills llama-server by bare name"
fi

# THE WATCHDOG MUST NOT SHOOT A LOADING MODEL. The functions are lifted out of
# healthcheck.sh and run against a stub curl that answers what llama-server
# really answers during a load, and a stub `kill` target that records.
mkdir -p "$_RV/bin" "$_RV/repo/.run"
cat > "$_RV/bin/curl" <<'STUB'
#!/bin/bash
cat "$RV_CURL_BODY" 2>/dev/null
STUB
chmod +x "$_RV/bin/curl"
{
  echo 'set -uo pipefail'
  echo "source '$REPO_DIR/scripts/lib/proc.sh'"
  echo 'REPO_DIR="$RV_REPO"; LLAMA_URL=http://x; LLAMA_AUTH=(); LLAMA_BIN_ERE="$RV_BIN_ERE"'
  sed -n '/^_LLAMA_ARGV0=/p; /^_llama_loading() {/,/^}/p; /^_kill_own_llama() {/,/^}/p' "$REPO_DIR/scripts/healthcheck.sh"
  echo '"$@"'
} > "$_RV/hc.sh"
_rv_hc() { # _rv_hc <curl body> <function> — status is the function's
  printf '%s' "$1" > "$_RV/body"
  RV_CURL_BODY="$_RV/body" RV_REPO="$_RV/repo" RV_BIN_ERE='/no/such/build/bin/llama-server' \
    PATH="$_RV/bin:$PATH" bash "$_RV/hc.sh" "$2"
}
if _rv_hc '{"error":{"code":503,"message":"Loading model","type":"unavailable_error"}}' _llama_loading; then
  pass "a server answering 'Loading model' is LOADING, not down"
else
  fail "healthcheck reads a loading llama-server as down — --restart would kill it mid-load"
fi
if _rv_hc '{"status":"ok"}' _llama_loading; then
  fail "a healthy server was classed as loading"
else
  pass "a healthy server is not 'loading' (control)"
fi
rm -f "$_RV/repo/.run/llama.pid"
if _rv_hc '' _llama_loading; then
  fail "no server and no pid was classed as loading — a dead stack would never be restarted"
else
  pass "nothing answering and no recorded pid is DOWN, not loading (control)"
fi
# port not bound yet, but the recorded llama-server is seconds old
printf '#!/bin/bash\nexec -a "$0" sleep 300\n' > "$_RV/llama-server"; chmod +x "$_RV/llama-server"
bash "$_RV/llama-server" & _RV_LL=$!; _RV_PIDS="$_RV_PIDS $!"
sleep 0.3
echo "$_RV_LL" > "$_RV/repo/.run/llama.pid"
if _rv_hc '' _llama_loading; then
  pass "a seconds-old recorded llama-server with no port yet is still loading"
else
  fail "a just-launched llama-server is treated as down"
fi
# "Loading model" is BOUNDED too: a server wedged mid-load says it forever.
if OPENBEAST_LLAMA_LOAD_GRACE=0 _rv_hc '{"error":{"message":"Loading model"}}' _llama_loading; then
  fail "a recorded server still 'Loading model' past the grace is left alone forever"
else
  pass "past the grace, a recorded server that is STILL loading is down"
fi
# argv[0], not a mention: `tail -f llama-server.log` is not the server.
printf '#!/bin/bash\nexec -a "tail -f llama-server.log" sleep 300\n' > "$_RV/tailer"; chmod +x "$_RV/tailer"
bash "$_RV/tailer" & _RV_TAIL=$!; _RV_PIDS="$_RV_PIDS $!"
sleep 0.3
echo "$_RV_TAIL" > "$_RV/repo/.run/llama.pid"
_rv_hc '' _kill_own_llama || true; sleep 0.2
if kill -0 "$_RV_TAIL" 2>/dev/null && ! _rv_hc '' _llama_loading; then
  pass "a recycled pid that merely MENTIONS llama-server is neither 'loading' nor killed"
else
  fail "a process mentioning llama-server in its arguments was taken for the server"
fi
pkill -P "$_RV_TAIL" 2>/dev/null || true; kill "$_RV_TAIL" 2>/dev/null || true
echo "$_RV_LL" > "$_RV/repo/.run/llama.pid"
if OPENBEAST_LLAMA_LOAD_GRACE=0 _rv_hc '' _llama_loading; then
  fail "the load grace never expires — a wedged server would be left forever"
else
  pass "past the grace period a silent server IS down (control)"
fi
# _kill_own_llama takes the recorded pid, and only when it is a llama-server
sleep 300 & _RV_STRANGER=$!; _RV_PIDS="$_RV_PIDS $!"
echo "$_RV_STRANGER" > "$_RV/repo/.run/llama.pid"
_rv_hc '' _kill_own_llama || true; sleep 0.2
if kill -0 "$_RV_STRANGER" 2>/dev/null; then
  pass "a stale llama.pid naming a stranger is not killed"
else
  fail "_kill_own_llama killed an unrelated process via a stale llama.pid"
fi
echo "$_RV_LL" > "$_RV/repo/.run/llama.pid"
_rv_hc '' _kill_own_llama || true; sleep 0.3
if kill -0 "$_RV_LL" 2>/dev/null; then
  fail "_kill_own_llama did not kill the recorded llama-server"
else
  pass "the recorded llama-server is killed by its pid (control)"
fi
kill "$_RV_STRANGER" 2>/dev/null || true; pkill -P "$_RV_LL" 2>/dev/null || true
if grep -q '_gpu_leased' "$REPO_DIR/scripts/healthcheck.sh" \
   && grep -q 'gpu-lease.sh" status' "$REPO_DIR/stop.sh"; then
  pass "the watchdog and stop.sh both consult the GPU lease before touching llama-server"
else
  fail "a held GPU lease is ignored — the watchdog would relaunch into a campaign"
fi

# start.sh: cleanup() removes a pidfile only while it still names OUR child.
_RV_CL="$(sed -n '/^  _rm_own_pidfile() {/,/^  }/p' "$REPO_DIR/start.sh")"
if [[ -n "$_RV_CL" ]]; then
  echo 4242 > "$_RV/own.pid"; echo 9999 > "$_RV/replaced.pid"
  bash -c "set -euo pipefail; $_RV_CL
    _rm_own_pidfile '$_RV/own.pid' 4242; _rm_own_pidfile '$_RV/replaced.pid' 4242
    _rm_own_pidfile '$_RV/replaced.pid' ''; _rm_own_pidfile '$_RV/missing.pid' 4242"
  if [[ ! -e "$_RV/own.pid" && "$(cat "$_RV/replaced.pid")" == "9999" ]]; then
    pass "cleanup keeps a pidfile healthcheck rewrote for its replacement"
  else
    fail "cleanup deleted a pidfile that names another process"
  fi
else
  fail "start.sh cleanup no longer checks the pidfile's CONTENT before deleting it"
fi
if grep -A3 "WEIGHTS_DIR/\[A-Za-z0-9._-\]" "$REPO_DIR/start.sh" | grep -q '|| true)"'; then
  pass "the fast-boot weight probe cannot abort start.sh under pipefail"
else
  fail "start.sh: grep|head|sed in a substitution without || true aborts under pipefail"
fi

# OFFLINE, as an operator would plausibly WRITE it.
for _v in '"true"' "'true'" '"true" # air-gapped rig' 'true# x' 'TRUE  # x'; do
  _got="$(REPO_DIR="$REPO_DIR" OPENBEAST_OFFLINE="$_v" bash -c \
          'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1; ob_offline && echo on || echo off')"
  if [[ "$_got" == "on" ]]; then
    pass "OFFLINE=$_v resolves to on (quotes and a comment do not fail OPEN)"
  else
    fail "OFFLINE=$_v resolved to $_got — offline mode failed open"
  fi
done
for _v in '"maybe"' '#true' '"" # true' "'false'"; do
  _got="$(REPO_DIR="$REPO_DIR" OPENBEAST_OFFLINE="$_v" bash -c \
          'source "$REPO_DIR/scripts/lib/conf.sh" >/dev/null 2>&1; ob_offline && echo on || echo off')"
  if [[ "$_got" == "off" ]]; then
    pass "OFFLINE=$_v resolves to off (control)"
  else
    fail "OFFLINE=$_v resolved to on"
  fi
done

# The unlock must not take stderr with it (a bare `exec 9>&- 2>/dev/null` is
# permanent): a campaign's tracebacks went to /dev/null.
mkdir -p "$_RV/lease0"
_RV_ERR="$(PATH="$_RV/bin:$PATH" OPENBEAST_RUN_DIR="$_RV/lease0" OPENBEAST_LEASE_VRAM_FLOOR=99999999 \
  "$REPO_DIR/scripts/gpu-lease.sh" run rv0 -- bash -c 'echo to-stderr >&2' 2>&1 >/dev/null || true)"
if [[ "$_RV_ERR" == *to-stderr* ]]; then
  pass "gpu-lease run passes the command's stderr through"
else
  fail "gpu-lease run swallowed the command's stderr: '$_RV_ERR'"
fi

# gpu-lease run: an operator's SIGTERM reaches the command, and the lease
# lasts exactly as long as the command does.
# A stub nvidia-smi: gpu-lease calls it in acquire AND status, and on this rig
# the real one can take a second under a campaign — which is timing luck.
printf '#!/bin/bash\necho 0\n' > "$_RV/bin/nvidia-smi"; chmod +x "$_RV/bin/nvidia-smi"
_RV_GL() { PATH="$_RV/bin:$PATH" OPENBEAST_RUN_DIR="$_RV/lease" OPENBEAST_LEASE_VRAM_FLOOR=99999999 "$REPO_DIR/scripts/gpu-lease.sh" "$@"; }
mkdir -p "$_RV/lease"
# The wrapper records ITS OWN pid (exec keeps it): finding it with pgrep -f
# would match any process whose command line merely mentions the pattern.
( PATH="$_RV/bin:$PATH" OPENBEAST_RUN_DIR="$_RV/lease" OPENBEAST_LEASE_VRAM_FLOOR=99999999 \
    bash -c 'echo $$ > "$1/wrapper.pid"; exec "$2" run rv -- bash -c "trap \"echo got-TERM; exit 7\" TERM; sleep 5 & wait"' \
    _ "$_RV" "$REPO_DIR/scripts/gpu-lease.sh" > "$_RV/lease.out" 2>&1 \
    && echo "rc=0" >> "$_RV/lease.out" || echo "rc=$?" >> "$_RV/lease.out" ) &   # set -e: never a bare `; echo $?`
# Wait for the lease to EXIST (the wrapper has reached its wait), then signal.
for _i in $(seq 1 50); do
  [[ -s "$_RV/lease/gpu.lease" ]] && grep -q '^label=rv$' "$_RV/lease/gpu.lease" 2>/dev/null && break
  sleep 0.2
done
sleep 0.3
_RV_W="$(cat "$_RV/wrapper.pid" 2>/dev/null || echo 0)"
kill -TERM "$_RV_W" 2>/dev/null || true
for _i in $(seq 1 25); do grep -q got-TERM "$_RV/lease.out" 2>/dev/null && break; sleep 0.2; done
# Captured, never `| grep -q` under pipefail: the early-exiting grep SIGPIPEs
# the writer and a HELD lease reads as not-held.
_RV_ST="$(_RV_GL status 2>/dev/null || true)"
if [[ "$_RV_ST" == HELD* ]]; then
  pass "the lease stays HELD while a cell the dead master started is still in its group"
else
  fail "the lease went FREE with the command's group still on the card: $(tr '\n' ' ' < "$_RV/lease.out")"
fi
# Poll, don't sleep-and-hope: this suite runs under load.
for _i in $(seq 1 50); do grep -q '^rc=' "$_RV/lease.out" 2>/dev/null && break; sleep 0.2; done
if grep -q "got-TERM" "$_RV/lease.out" && grep -q "rc=7" "$_RV/lease.out"; then
  pass "gpu-lease run forwards SIGTERM and returns the command's status"
else
  fail "gpu-lease run swallowed SIGTERM: $(tr '\n' ' ' < "$_RV/lease.out")"
fi
_RV_ST="$(_RV_GL status 2>/dev/null || true)"
if [[ "$_RV_ST" == FREE* ]]; then
  pass "…and the lease is released once the command has gone"
else
  fail "lease not released after the command exited"
fi
rm -rf "$_RV"

# ---------------------------------------------------------------------------
# RUN IT, top to bottom. Every check above lifts FUNCTIONS out of
# healthcheck.sh; none executed the script — so `$OPENBEAST_CHAT_BIND`, bare,
# in the fall-through arm of a `case` whose selector carried the default,
# shipped to main: `set -u` killed healthcheck.sh on that line for EVERY user,
# and the same line in start.sh tore the whole stack down after the model had
# loaded whenever BEAST_CHAT=true. A real ./start.sh caught it, minutes later.
# ---------------------------------------------------------------------------
echo ""
echo "End-to-end under set -u (nothing exported):"
_E2E="$(mktemp -d)"
mkdir -p "$_E2E/repo/scripts/lib" "$_E2E/bin" "$_E2E/home"
cp "$REPO_DIR/scripts/healthcheck.sh" "$REPO_DIR/scripts/gpu-lease.sh" "$_E2E/repo/scripts/"
cp "$REPO_DIR"/scripts/lib/*.sh "$_E2E/repo/scripts/lib/"
for _c in curl docker tailscale nvidia-smi sudo systemctl; do
  printf '#!/bin/bash\nexit 1\n' > "$_E2E/bin/$_c"; chmod +x "$_E2E/bin/$_c"
done
for _mode in "default" "BEAST_CHAT=true BEAST_ARTIFACT=true EDGE_GATE=true"; do
  : > "$_E2E/repo/openbeast.conf"
  for _kv in $_mode; do [[ "$_kv" == *=* ]] && echo "$_kv" >> "$_E2E/repo/openbeast.conf"; done
  _E2E_OUT="$(env -i HOME="$_E2E/home" PATH="$_E2E/bin:/usr/bin:/bin" \
                bash "$_E2E/repo/scripts/healthcheck.sh" 2>&1 || true)"
  if [[ "$_E2E_OUT" != *"unbound variable"* && "$_E2E_OUT" == *"Stack health check"* \
        && "$_E2E_OUT" == *"DOWN llama.cpp server"* && "$_E2E_OUT" =~ (unhealthy|healthy) ]]; then
    pass "healthcheck.sh runs to its summary with nothing exported ($_mode)"
  else
    fail "healthcheck.sh died before its summary ($_mode): $(tail -n 3 <<< "$_E2E_OUT" | tr '\n' ' ')"
  fi
  if [[ "$_mode" != default && "$_E2E_OUT" != *"beast-chat console"* ]]; then
    fail "control: the chat branch did not run, so this test proved nothing"
  fi
done
rm -rf "$_E2E"
# ...and the static half, for start.sh, whose optional-service branches cannot
# be executed here: a variable that is NORMALLY UNSET is never named bare.
_BARE_U=0
for _f in start.sh stop.sh scripts/healthcheck.sh; do
  while IFS= read -r _ln; do
    _t="${_ln#"${_ln%%[![:space:]]*}"}"
    [[ "$_t" == \#* ]] && continue
    _BARE_U=1; echo "    $_f: $_t"
  done < <(grep -nE '\$(OPENBEAST_CHAT_BIND|OPENBEAST_ARTIFACT_BASE_URL|OPENBEAST_LLAMA_LOAD_GRACE)\b|\$\{(OPENBEAST_CHAT_BIND|OPENBEAST_ARTIFACT_BASE_URL|OPENBEAST_LLAMA_LOAD_GRACE)\}' "$REPO_DIR/$_f" || true)
done
if [[ $_BARE_U -eq 0 ]]; then
  pass "no normally-unset variable is expanded without a default in start/stop/healthcheck"
else
  fail "a normally-unset OPENBEAST_* variable is expanded bare — set -u kills the script there"
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
