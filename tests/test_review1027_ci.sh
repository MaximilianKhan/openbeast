#!/bin/bash
# Review 2026-10-27 (R5 supply chain / CI): the script-side fixes, each with
# its negative control. Hermetic: gh, docker and sleep are recording stubs;
# nothing is fetched, approved, merged or started.
#
#   1. land-dependabot.sh — a red check stops the run before the merge (R5-03)
#   2. land-dependabot.sh — a PR that does not touch agents/requirements.txt
#      waits for the rebase only, not for a relock that never comes (R5-04)
#   3. vllm/spark-node.sh — OFFLINE refuses an unfetched profile; HF_ENDPOINT
#      and HF_TOKEN_FILE reach the container's download by NAME (R5-05)
#   4. tensorfold/spark-node.sh — refuses to start under OFFLINE (R5-06) unless
#      TENSORFOLD_PREBUILT=true (R10-05); vllm/spark-node.sh warns on a vLLM
#      below 0.31.0 in the image (R10-06)
#   5. install-skills.sh — a symlinked global skill is listed with its
#      target (R5-10)
#
# Usage: bash tests/test_review1027_ci.sh
set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0 FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
has() { grep -qF -- "$2" <<< "$1"; }
T="$(mktemp -d "${TMPDIR:-/tmp}/obrev1027ciXXXXXX")"
trap 'rm -rf "$T"' EXIT

# ===========================================================================
echo "1-2. land-dependabot.sh — red checks, and PRs with no relock:"
# ===========================================================================
if ! command -v jq >/dev/null 2>&1; then
  if [[ "${CI:-}" == true ]]; then fail "jq is not installed (the gh stub needs it)"; else echo "  SKIP: jq not installed"; fi
else
  LB="$T/bin_land" S="$T/state"; mkdir -p "$LB" "$S"
  printf '#!/bin/bash\nexit 0\n' > "$LB/sleep"
  # gh answers from fixtures and evaluates -q with real jq. $S/checks_rc
  # decides whether `gh pr checks --watch` ends green (0) or red (1).
  # PR 7 bumps agents/requirements.txt (relock path); PR 8 bumps a workflow.
  cat > "$LB/gh" <<'STUB'
#!/bin/bash
S="$OB_STUB_STATE"
echo "gh $*" >> "$S/gh.log"
q=""; args=()
while [[ $# -gt 0 ]]; do
  case "$1" in -q) q="$2"; shift 2 ;; *) args+=("$1"); shift ;; esac
done
set -- "${args[@]}"
out() { if [[ -n "$q" ]]; then jq -r "$q"; else cat; fi; }
HEAD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
case "$*" in
  "repo view"*)                    echo '{"nameWithOwner": "me/openbeast"}' | out ;;
  "pr view "[78]" --json headRefName"*) echo '{"headRefName": "dependabot/x"}' | out ;;
  "pr view 7 --json commits"*)     echo '{"commits": [{"messageHeadline": "deps: regenerate agents/requirements.lock"}]}' | out ;;
  "pr view 8 --json commits"*)     echo '{"commits": [{"messageHeadline": "Bump actions/checkout from 7.0.0 to 7.0.1"}]}' | out ;;
  "pr view "[78]" --json headRefOid"*) echo "{\"headRefOid\": \"$HEAD\"}" | out ;;
  "pr view "[78]" --json state"*)  if [[ -s "$S/merge.log" ]]; then echo '{"state": "MERGED"}'; else echo '{"state": "OPEN"}'; fi | out ;;
  "pr comment"*) echo ok ;;
  "pr merge"*)  echo "$*" >> "$S/merge.log"; echo ok ;;
  "pr checks"*)
      rc="$(cat "$S/checks_rc" 2>/dev/null || echo 0)"
      if [[ "$rc" == 0 ]]; then
        printf 'test\tpass\t5m\thttps://x/1\nLock matches a fresh resolve\tpass\t1m\thttps://x/2\n'
      else
        printf 'test\tpass\t5m\thttps://x/1\nLock matches a fresh resolve\tfail\t1m\thttps://x/2\n'
      fi
      exit "$rc" ;;
  "pr list"*)
      echo '[{"number": 8, "files": [{"path": ".github/workflows/ci.yml"}]},
             {"number": 7, "files": [{"path": "agents/requirements.txt"}]}]' | out ;;
  "api repos/me/openbeast/commits/main"*)  echo '{"sha": "m"}' | out ;;
  "api repos/me/openbeast/compare/"*)      echo '{"behind_by": 0}' | out ;;
  "api repos/me/openbeast/actions/runs?"*) echo '{"workflow_runs": []}' | out ;;
  "run list"*) echo '[]' | out ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
STUB
  chmod +x "$LB/gh" "$LB/sleep"
  LR="$T/land_repo"; mkdir -p "$LR/scripts"
  install -m 755 "$REPO_DIR/scripts/land-dependabot.sh" "$LR/scripts/"
  land() { # land <checks_rc> [args…] — sets _out/_rc
    echo "$1" > "$S/checks_rc"; shift
    : > "$S/gh.log"; : > "$S/merge.log"
    _out="$(env PATH="$LB:$PATH" OB_STUB_STATE="$S" bash "$LR/scripts/land-dependabot.sh" "$@" 2>&1)"; _rc=$?
  }

  land 1 7
  if [[ $_rc -ne 0 && ! -s "$S/merge.log" ]] && has "$_out" "a check is red" \
     && has "$_out" "Lock matches a fresh resolve"; then
    pass "R5-03: a red check (gh pr checks exit 1) stops the run before the merge, naming the check"
  else
    fail "R5-03 red check (rc=$_rc, merge=$(cat "$S/merge.log")): $_out"
  fi
  land 0 7
  if [[ $_rc -eq 0 && -s "$S/merge.log" ]] && has "$_out" "PR 7 -> MERGED" && ! has "$_out" "unexpected gh call"; then
    pass "R5-03 control: the same PR with green checks is merged"
  else
    fail "R5-03 control (rc=$_rc): $_out"
  fi

  land 0 8
  _polls="$(grep -c "compare/" "$S/gh.log")"
  if [[ $_rc -eq 0 ]] && has "$_out" "PR 8 -> MERGED" && has "$_out" "rebase only" \
     && [[ "$_polls" -eq 1 ]] && ! grep -q "dependabot-relock" "$S/gh.log"; then
    pass "R5-04: a named github-actions PR waits for the rebase only (1 poll, no relock query) and lands"
  else
    fail "R5-04 non-requirements PR (rc=$_rc, $_polls compare polls): $_out"
  fi
  # Control: the requirements PR still waits for its relock commit.
  land 0 7
  if [[ $_rc -eq 0 ]] && ! has "$_out" "rebase only" && grep -q "pr view 7 --json commits" "$S/gh.log"; then
    pass "R5-04 control: an agents/requirements.txt PR still waits for the relock commit"
  else
    fail "R5-04 control (rc=$_rc): $_out"
  fi
  land 1 8
  [[ $_rc -ne 0 && ! -s "$S/merge.log" ]] && pass "R5-03+04: a red check on a non-requirements PR also stops before the merge" \
    || fail "red check on PR 8 (rc=$_rc): $_out"
fi

# ===========================================================================
echo ""
echo "3-4. Spark launchers — OFFLINE, HF_ENDPOINT, HF_TOKEN_FILE:"
# ===========================================================================
K="$T/spark"; mkdir -p "$K/bin" "$K/home" "$K/profiles" "$K/models"
cat > "$K/bin/docker" <<EOF
#!/bin/bash
# The launcher's version probe: answer with \$VLLM_STUB_VERSION (or nothing).
if [[ "\$*" == *"import vllm"* ]]; then
  echo "PROBE \$*" >> "$K/docker.log"; echo "\${VLLM_STUB_VERSION:-}"; exit 0
fi
echo "ARGV \$*" >> "$K/docker.log"
echo "ENVTOKEN \${HF_TOKEN:-<unset>}" >> "$K/docker.log"
echo "ENVENDPOINT \${HF_ENDPOINT:-<unset>}" >> "$K/docker.log"
exit 0
EOF
chmod +x "$K/bin/docker"
(umask 077; printf 'sk-test-KEY\n' > "$K/key"; printf 'hf_SECRET_TOKEN_77\n' > "$K/hftoken")
sed -e "s|^SPARK_SERVE_HOST=.*|SPARK_SERVE_HOST=100.64.1.2|" \
    -e "s|^VLLM_API_KEY_FILE=.*|VLLM_API_KEY_FILE=$K/key|" \
    -e "s|^MODELS_DIR=.*|MODELS_DIR=$K/models|" \
    "$REPO_DIR/scripts/backends/spark.env.example" > "$K/spark.env"
REV=0123456789abcdef0123456789abcdef01234567
DIGEST="sha256:$(printf 'a%.0s' $(seq 1 64))"
cat > "$K/profiles/vnofetch.env" <<EOF
BACKEND=vllm
SOURCE=acme/Unfetched-Model
REVISION=$REV
SERVED_MODEL_NAME=m
TENSOR_PARALLEL_SIZE=1
TOOL_CALL_PARSER=hermes
EOF
cat > "$K/profiles/tftest.env" <<EOF
BACKEND=tensorfold
SOURCE=acme/Brand-New-Model-MLX-4bit
REVISION=$REV
SERVED_MODEL_NAME=local-model
TENSOR_PARALLEL_SIZE=1
EOF
# A fetched TensorFold profile, exactly as model-fetch.sh leaves it.
python3 - "$K/models" "$K/profiles/tftest.env" "acme/Brand-New-Model-MLX-4bit" "$REV" <<'PY'
import hashlib, json, sys
from pathlib import Path
mdir, prof, repo, rev = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
root = mdir / "tftest"; root.mkdir(parents=True, exist_ok=True)
(root / "config.json").write_text(json.dumps({"model_type": "brand_new"}))
(root / "model.safetensors").write_bytes(b"\0" * 64)
(root / ".openbeast-model.json").write_text(json.dumps({"source": repo, "revision": rev}))
files = {f: {"size": len((root / f).read_bytes()), "sha256": hashlib.sha256((root / f).read_bytes()).hexdigest(),
             "hub_oid": None, "lfs": False} for f in ("config.json", "model.safetensors")}
prof.with_suffix(".lock").write_text(json.dumps({"schema": 1, "artifacts": {"model": {
    "source": repo, "revision": rev, "dir": "tftest", "files": files}}}))
PY
VN="$REPO_DIR/scripts/backends/vllm/spark-node.sh"
TN="$REPO_DIR/scripts/backends/tensorfold/spark-node.sh"
sp() { # sp <script> [env...] -- [args...] -> _O, SPRC
  local s="$1"; shift; local envs=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do envs+=("$1"); shift; done
  shift || true
  SPRC=0
  env -i HOME="$K/home" PATH="$K/bin:/usr/bin:/bin" ${envs[@]+"${envs[@]}"} \
    bash "$s" "$@" > "$K/out" 2>&1 || SPRC=$?
  _O="$(cat "$K/out")"
}

sp "$VN" OFFLINE=true -- --profile "$K/profiles/vnofetch.env" --rank 0 --env "$K/spark.env" --print
if [[ $SPRC -eq 1 ]] && has "$_O" "OFFLINE is set" && has "$_O" "Refusing to start" && ! has "$_O" "serve acme/"; then
  pass "R5-05: OFFLINE (env) refuses an unfetched vLLM profile instead of downloading it"
else
  fail "R5-05 OFFLINE env (rc=$SPRC): $_O"
fi
{ cat "$K/spark.env"; echo "OFFLINE=true"; } > "$K/offline.env"
sp "$VN" -- --profile "$K/profiles/vnofetch.env" --rank 0 --env "$K/offline.env" --print
[[ $SPRC -eq 1 ]] && has "$_O" "OFFLINE is set" && pass "R5-05: …OFFLINE=true in spark.env counts too" \
  || fail "R5-05 OFFLINE in spark.env (rc=$SPRC): $_O"
sp "$VN" -- --profile "$K/profiles/vnofetch.env" --rank 0 --env "$K/spark.env" --print
if [[ $SPRC -eq 0 ]] && has "$_O" "serve acme/Unfetched-Model --revision $REV" && ! has "$_O" "HF_ENDPOINT" \
   && ! has "$_O" "-e HF_TOKEN"; then
  pass "R5-05 control: without OFFLINE it still serves from the Hub at the pin; no mirror/token unless set"
else
  fail "R5-05 control (rc=$SPRC): $_O"
fi
: > "$K/docker.log"
sp "$VN" HF_ENDPOINT=https://hf-mirror.internal HF_TOKEN_FILE="$K/hftoken" VLLM_IMAGE_DIGEST="$DIGEST" \
   -- --profile "$K/profiles/vnofetch.env" --rank 0 --env "$K/spark.env"
_dl="$(cat "$K/docker.log")"
if [[ $SPRC -eq 0 ]] && has "$_dl" " -e HF_ENDPOINT " && has "$_dl" " -e HF_TOKEN " \
   && has "$_dl" "ENVTOKEN hf_SECRET_TOKEN_77" && has "$_dl" "ENVENDPOINT https://hf-mirror.internal" \
   && ! grep -q "^ARGV.*hf_SECRET_TOKEN_77" "$K/docker.log" && ! grep -q "^ARGV.*hf-mirror" "$K/docker.log"; then
  pass "R5-05: HF_ENDPOINT and the HF_TOKEN_FILE token reach the container's environment by NAME (not argv)"
else
  fail "R5-05 forwarding (rc=$SPRC): $_O :: $_dl"
fi
chmod 644 "$K/hftoken"
sp "$VN" HF_TOKEN_FILE="$K/hftoken" -- --profile "$K/profiles/vnofetch.env" --rank 0 --env "$K/spark.env" --print
[[ $SPRC -eq 1 ]] && has "$_O" "HF_TOKEN_FILE file '$K/hftoken' is mode 644" \
  && pass "R5-05: a group/world-readable HF_TOKEN_FILE is refused" || fail "R5-05 0644 token (rc=$SPRC): $_O"
chmod 600 "$K/hftoken"

sp "$TN" OFFLINE=true -- --profile "$K/profiles/tftest.env" --rank 0 --env "$K/spark.env" --print
if [[ $SPRC -eq 1 ]] && has "$_O" "OFFLINE is set" && has "$_O" "unpinned dependencies" && has "$_O" "Refusing to start"; then
  pass "R5-06: the TensorFold launcher refuses to start under OFFLINE (it pip-installs at every start)"
else
  fail "R5-06 OFFLINE (rc=$SPRC): $_O"
fi
sp "$TN" -- --profile "$K/profiles/tftest.env" --rank 0 --env "$K/spark.env" --print
if [[ $SPRC -eq 0 ]] && has "$_O" "UNPINNED from PyPI" && has "$_O" "git+https://github.com/ashhart/TensorFold.git@"; then
  pass "R5-06 control: without OFFLINE it starts, and says its dependencies are unpinned"
else
  fail "R5-06 control (rc=$SPRC): $_O"
fi

# TensorFold with a prebuilt image (R10-05): no pip install, OFFLINE allowed.
sp "$TN" OFFLINE=true TENSORFOLD_PREBUILT=true -- --profile "$K/profiles/tftest.env" --rank 0 --env "$K/spark.env" --print
if [[ $SPRC -eq 0 ]] && has "$_O" "TENSORFOLD_PREBUILT=true" && ! has "$_O" "pip\\ install" && has "$_O" "exec\\ tensorfold"; then
  pass "R10-05: TENSORFOLD_PREBUILT=true runs the image's tensorfold, installs nothing, and is allowed OFFLINE"
else
  fail "R10-05 prebuilt (rc=$SPRC): $_O"
fi
sp "$TN" -- --profile "$K/profiles/tftest.env" --rank 0 --env "$K/spark.env" --print
[[ $SPRC -eq 0 ]] && has "$_O" "pip\\ install\\ --quiet" \
  && pass "R10-05 control: without it the launcher still pip-installs at start" || fail "R10-05 control: $_O"

# vLLM version in the image (R10-06): warn below 0.22 / 0.31; quiet when ok.
vrun() { : > "$K/docker.log"; sp "$VN" VLLM_IMAGE_DIGEST="$DIGEST" VLLM_STUB_VERSION="$1" \
           -- --profile "$K/profiles/vnofetch.env" --rank 0 --env "$K/spark.env"; }
vrun "0.21.3+nv26.05"
if [[ $SPRC -eq 0 ]] && has "$_O" "vLLM 0.21.3" && has "$_O" "GHSA-94f4-hr76-p5j6" && has "$_O" "does NOT protect" \
   && grep -q "^PROBE run --rm --network none --entrypoint python3 nvcr.io/nvidia/vllm:26.05-py3@$DIGEST" "$K/docker.log"; then
  pass "R10-06: the pinned image is probed offline and vLLM < 0.22 warns that the API key does not protect it"
else
  fail "R10-06 <0.22 (rc=$SPRC): $_O :: $(cat "$K/docker.log")"
fi
vrun "0.28.1"
has "$_O" "GHSA-h3rc-6mm3-gc2m" && ! has "$_O" "does NOT protect" && pass "R10-06: vLLM < 0.31 warns about GHSA-h3rc-6mm3-gc2m" \
  || fail "R10-06 <0.31: $_O"
vrun "0.31.2"
if has "$_O" "vLLM in the image: 0.31.2" && ! has "$_O" "WARNING"; then
  pass "R10-06 control: vLLM >= 0.31.0 is reported, no warning"
else
  fail "R10-06 control: $_O"
fi
vrun ""
has "$_O" "could not read the vLLM version" && pass "R10-06: an unreadable version says so" || fail "R10-06 unknown: $_O"
: > "$K/docker.log"
sp "$VN" VLLM_IMAGE_DIGEST="$DIGEST" SPARK_SKIP_VLLM_VERSION_CHECK=true -- --profile "$K/profiles/vnofetch.env" --rank 0 --env "$K/spark.env"
! grep -q "^PROBE" "$K/docker.log" && pass "R10-06: SPARK_SKIP_VLLM_VERSION_CHECK=true skips the probe" || fail "R10-06 skip: $(cat "$K/docker.log")"

# ===========================================================================
echo ""
echo "5. install-skills.sh — symlinked global skills are shown as links:"
# ===========================================================================
H="$T/skillhome"; mkdir -p "$H/.local/share/local-llm-skills/plain" "$T/elsewhere/linked"
ln -s "$T/elsewhere/linked" "$H/.local/share/local-llm-skills/linked"
_O="$(HOME="$H" bash "$REPO_DIR/scripts/install-skills.sh" 2>&1)"
if has "$_O" "  linked  -> $T/elsewhere/linked"; then
  pass "R5-10: a symlinked global skill is listed with its '-> target'"
else
  fail "R5-10 link not shown: $_O"
fi
if grep -qx "  plain" <<< "$_O"; then
  pass "R5-10 control: a real directory is listed without a target"
else
  fail "R5-10 control: $_O"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
