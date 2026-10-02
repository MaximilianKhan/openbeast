#!/bin/bash
# Remote-skill import gate (scripts/skill-import.sh) — behavior tests.
#
# Usage: ./tests/test_skill_import.sh
#
# Everything runs against a THROWAWAY repo under $TMPDIR with a stub scanner
# and a stub git: the real skills/ and ledger are never written, nothing is
# downloaded, and no SkillSpector install is needed (CI has none). The stubs
# RECORD their calls, because half of what this gate promises is about what it
# does NOT do:
#
#   * a bad pin, URL or path is refused before git is ever run
#   * a scan that could not look (no scanner, crash, unknown report, analysis
#     the scanner calls incomplete) is a refusal, never a pass
#   * a blocked promote leaves skills/ and the ledger byte-identical
#   * the scan always runs --no-llm, so skill contents stay on the box
#   * an in-house skill is never overwritten by an import
#
# The last section runs `verify` against the REAL ledger, read-only.

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== skill-import.sh (remote-skill import gate) tests ==="
echo ""

# --- 1. The scripts themselves ---
echo "Script:"
if [[ -x "$REPO_DIR/scripts/skill-import.sh" ]] && bash -n "$REPO_DIR/scripts/skill-import.sh"; then
  pass "scripts/skill-import.sh is executable and passes bash -n"
else
  fail "scripts/skill-import.sh missing, not executable, or has a syntax error"
fi
if python3 -m py_compile "$REPO_DIR/scripts/lib/skill_import.py" 2>/dev/null; then
  pass "scripts/lib/skill_import.py compiles"
else
  fail "scripts/lib/skill_import.py does not compile"
  echo ""; echo "Results: $PASS passed, $FAIL failed"; exit 1
fi
# Sourcing lib/conf.sh appends a generated secret to openbeast.conf; `verify`
# runs from doctor and from this suite and must stay read-only.
if ! grep -v '^[[:space:]]*#' "$REPO_DIR/scripts/skill-import.sh" \
     | grep -qE '(^|[^[:alnum:]_])(source|\.)[[:space:]]+[^;]*lib/conf\.sh'; then
  pass "skill-import.sh does not source lib/conf.sh (no config side effect)"
else
  fail "skill-import.sh sources lib/conf.sh — verify would mutate openbeast.conf"
fi

# --- 2. Sandbox: throwaway repo, stub scanner, stub git ---
TMPROOT="$(mktemp -d "${TMPDIR:-/tmp}/openbeast-skillimport-test.XXXXXX")"
trap 'rm -rf "$TMPROOT"' EXIT
SANDBOX="$TMPROOT/repo"
mkdir -p "$SANDBOX/scripts/lib" "$SANDBOX/skills/in-house" "$TMPROOT/bin" "$TMPROOT/xdg"
cp "$REPO_DIR/scripts/skill-import.sh" "$SANDBOX/scripts/"
cp "$REPO_DIR/scripts/lib/skill_import.py" "$SANDBOX/scripts/lib/"
cp "$REPO_DIR/skills/REMOTE_PROVENANCE.md" "$SANDBOX/skills/"
# The fixture ledger must start EMPTY whatever the real one holds.
python3 - "$SANDBOX/skills/REMOTE_PROVENANCE.md" <<'PY'
import sys
p = sys.argv[1]
out, in_table = [], False
for line in open(p).read().split("\n"):
    if line.startswith("| Skill | Source URL"):
        in_table = True
        out += [line, "|" + "---|" * 8, "| _(none yet — first import will land here)_ | | | | | | | |"]
        continue
    if in_table and line.startswith("|"):
        continue
    in_table = False
    out.append(line)
open(p, "w").write("\n".join(out))
PY
printf -- '---\nname: in-house\ndescription: one of ours\n---\n\n# In-house\n\n%s\n' \
  "A skill written in this repository, with no ledger row, that an import must never replace." \
  > "$SANDBOX/skills/in-house/SKILL.md"

CLI="$SANDBOX/scripts/skill-import.sh"
LEDGER="$SANDBOX/skills/REMOTE_PROVENANCE.md"
STAGE="$SANDBOX/.run/skill-staging"
SCAN_LOG="$TMPROOT/scanner.calls"
SCAN_MODE="$TMPROOT/scanner.mode"
GIT_LOG="$TMPROOT/git.calls"
GIT_HEAD="$TMPROOT/git.head"
FIXTURE="$TMPROOT/upstream"
REV="0123456789abcdef0123456789abcdef01234567"
URL="https://github.com/example/skills"

# Stub scanner: records argv, writes the report shape of SkillSpector 2.12.0
# (captured from a real run on 2026-10-01) to --output, in the chosen mode.
cat > "$TMPROOT/bin/stub-scanner" <<'PY'
#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["STUB_SCAN_LOG"], "a") as fh:
    fh.write(" ".join(argv) + "\n")
mode = open(os.environ["STUB_SCAN_MODE"]).read().strip()
if mode == "crash":
    sys.stderr.write("boom\n"); sys.exit(2)
out = argv[argv.index("--output") + 1]
if mode == "garbage":
    open(out, "w").write("not json"); sys.exit(0)
issues = []
if mode == "findings":
    issues = [{"id": "TM1", "category": "Tool Misuse", "pattern": "Tool Parameter Abuse",
               "severity": "HIGH", "location": {"file": "SKILL.md", "start_line": 7},
               "finding": "shell=True"}]
report = {
    "skill": {"name": "x"},
    "risk_assessment": {"score": 54 if issues else 0, "severity": "HIGH" if issues else "LOW",
                        "recommendation": "DO_NOT_INSTALL" if issues else "CAUTION"},
    "issues": issues,
    "metadata": {"skillspector_version": "2.12.0", "llm_requested": False},
    "execution_successful": mode != "incomplete",
    "analysis_completeness": {
        "status": "partial", "execution_successful": mode != "incomplete",
        "entirely_uninspected_files": 1 if mode == "incomplete" else 0,
        "partially_inspected_files": 0,
        "ledger_exceptions": [{"reason_code": "reference_missing", "fatal": False}],
        "analyzer_statuses": [{"analyzer_id": "artifact_integrity", "status": "completed", "failed": 0}],
    },
}
if mode == "unknown":
    del report["analysis_completeness"]
json.dump(report, open(out, "w"))
# "crashclean": an error exit that still leaves a clean-looking report behind.
sys.exit(2 if mode == "crashclean" else 1 if issues else 0)
PY
chmod +x "$TMPROOT/bin/stub-scanner"

# Stub git: records each call; `checkout` materialises the fixture tree.
cat > "$TMPROOT/bin/git" <<'SH'
#!/bin/bash
while [[ "${1:-}" == "-c" ]]; do shift 2; done
echo "$*" >> "$STUB_GIT_LOG"
case "$1" in
  init)      mkdir -p "${@: -1}/.git" ;;
  checkout)  cp -a "$STUB_GIT_FIXTURE/." . ;;
  rev-parse) cat "$STUB_GIT_HEAD" ;;
esac
exit 0
SH
chmod +x "$TMPROOT/bin/git"

run() { # the CLI under the stubs; XDG_DATA_HOME hides any real scanner install
  PATH="$TMPROOT/bin:$PATH" XDG_DATA_HOME="$TMPROOT/xdg" OFFLINE=false \
  SKILL_SCANNER="$TMPROOT/bin/stub-scanner" STUB_SCAN_LOG="$SCAN_LOG" STUB_SCAN_MODE="$SCAN_MODE" \
  STUB_GIT_LOG="$GIT_LOG" STUB_GIT_FIXTURE="$FIXTURE" STUB_GIT_HEAD="$GIT_HEAD" \
  "$CLI" "$@"
}
reset_fixture() { # a clean one-skill upstream at $FIXTURE/pack/demo
  rm -rf "$FIXTURE" "$STAGE"; mkdir -p "$FIXTURE/pack/demo/scripts"
  printf -- '---\nname: demo\ndescription: A demo skill for the import tests.\n---\n\n# Demo\n\n%s\n' \
    "Body long enough to be a real skill: run scripts/helper.sh and report what it prints back." \
    > "$FIXTURE/pack/demo/SKILL.md"
  echo 'echo helper' > "$FIXTURE/pack/demo/scripts/helper.sh"
  echo "$REV" > "$GIT_HEAD"; echo clean > "$SCAN_MODE"; : > "$GIT_LOG"; : > "$SCAN_LOG"
}
ledger_sum() { sha256sum "$LEDGER" | cut -d' ' -f1; }
fetch_demo() { run fetch "$URL" --rev "$REV" --name demo --path pack/demo "$@"; }

# --- 3. Refusals that must happen before any download ---
echo ""
echo "Fetch refuses a bad pin, source or path:"
reset_fixture
refuse_no_git() { # refuse_no_git <label> <args...>
  local label="$1"; shift
  : > "$GIT_LOG"
  if run fetch "$@" >/dev/null 2>&1; then
    fail "$label was accepted"
  elif [[ -s "$GIT_LOG" ]]; then
    fail "$label was refused, but only after git ran: $(head -1 "$GIT_LOG")"
  else
    pass "$label is refused and git never runs"
  fi
}
refuse_no_git "a branch name as --rev"  "$URL" --rev main --name demo
refuse_no_git "a short SHA as --rev"    "$URL" --rev 0123456 --name demo
refuse_no_git "an ssh:// source"        "ssh://git@github.com/example/skills" --rev "$REV" --name demo
refuse_no_git "a file:// source"        "file:///etc" --rev "$REV" --name demo
refuse_no_git "a URL with credentials"  "https://user:pw@github.com/example/skills" --rev "$REV" --name demo
refuse_no_git "a --path with .."        "$URL" --rev "$REV" --name demo --path ../../etc
refuse_no_git "an uppercase/odd --name" "$URL" --rev "$REV" --name '../Demo'
: > "$GIT_LOG"
if PATH="$TMPROOT/bin:$PATH" XDG_DATA_HOME="$TMPROOT/xdg" OFFLINE=true SKILL_SCANNER="$TMPROOT/bin/stub-scanner" \
   STUB_GIT_LOG="$GIT_LOG" "$CLI" fetch "$URL" --rev "$REV" --name demo >/dev/null 2>&1 || [[ -s "$GIT_LOG" ]]; then
  fail "OFFLINE=true: fetch ran or reached git"
else
  pass "OFFLINE=true: fetch is refused and git never runs"
fi
: > "$GIT_LOG"
if PATH="$TMPROOT/bin:/usr/bin:/bin" XDG_DATA_HOME="$TMPROOT/xdg" OFFLINE=false SKILL_SCANNER="$TMPROOT/nope" \
   STUB_GIT_LOG="$GIT_LOG" "$CLI" fetch "$URL" --rev "$REV" --name demo >"$TMPROOT/out" 2>&1 || [[ -s "$GIT_LOG" ]]; then
  fail "no scanner: fetch ran or reached git"
elif grep -q "install-scanner" "$TMPROOT/out"; then
  pass "no scanner: fetch is refused before the download, and says how to install one"
else
  fail "no scanner: refused without naming install-scanner"
fi

# --- 4. A good fetch stages and does nothing else ---
echo ""
echo "Fetch stages only:"
reset_fixture
before="$(ledger_sum)"
if fetch_demo >"$TMPROOT/out" 2>&1 && [[ -f "$STAGE/demo/SKILL.md" && -f "$STAGE/demo/scripts/helper.sh" ]]; then
  pass "a pinned fetch stages the skill directory"
else
  fail "a pinned fetch did not stage the skill: $(tail -2 "$TMPROOT/out")"
fi
if grep -q "fetch -q --depth 1 origin $REV" "$GIT_LOG"; then
  pass "git fetched exactly the pinned commit"
else
  fail "git was not asked for the pinned commit: $(cat "$GIT_LOG")"
fi
if python3 -c "
import json,sys; d=json.load(open(sys.argv[1]))
assert d['url']==sys.argv[2] and d['rev']==sys.argv[3] and d['path']=='pack/demo'" \
     "$STAGE/demo.provenance.json" "$URL" "$REV" 2>/dev/null; then
  pass "the provenance record holds the URL, the commit and the path"
else
  fail "provenance record missing or wrong"
fi
if [[ ! -e "$SANDBOX/skills/demo" && "$(ledger_sum)" == "$before" && ! -e "$STAGE/demo/.git" ]]; then
  pass "fetch wrote nothing to skills/ or the ledger, and staged no .git"
else
  fail "fetch touched skills/, the ledger, or staged a .git directory"
fi
if grep -q -- "--no-llm" "$SCAN_LOG" && ! grep -qv -- "--no-llm" "$SCAN_LOG"; then
  pass "the scan ran --no-llm (skill contents stay on the box)"
else
  fail "a scan ran without --no-llm: $(cat "$SCAN_LOG")"
fi
if fetch_demo >/dev/null 2>&1; then
  fail "a second fetch silently replaced the staged (possibly edited) copy"
else
  pass "a second fetch refuses to replace the staged copy without --force"
fi
reset_fixture; echo "ffffffffffffffffffffffffffffffffffffffff" > "$GIT_HEAD"
if fetch_demo >/dev/null 2>&1 || [[ -e "$STAGE/demo" ]]; then
  fail "a checkout at a different commit than the pin was staged"
else
  pass "a checkout that is not at the pinned commit is refused"
fi
reset_fixture; ln -s /etc/passwd "$FIXTURE/pack/demo/notes.txt"
if fetch_demo >/dev/null 2>&1 || [[ -e "$STAGE/demo" ]]; then
  fail "a skill containing a symlink was staged"
else
  pass "a skill containing a symlink is refused"
fi

# --- 5. Promote: the human gate, then the scan gate ---
echo ""
echo "Promote:"
reset_fixture; fetch_demo >/dev/null 2>&1
before="$(ledger_sum)"
blocked() { [[ ! -e "$SANDBOX/skills/demo" && "$(ledger_sum)" == "$before" ]]; }
if ! run promote demo >/dev/null 2>&1 && blocked; then
  pass "promote without --reviewed-by is refused; skills/ and ledger untouched"
else
  fail "promote ran without a reviewer"
fi
for mode in crash crashclean garbage unknown incomplete; do
  echo "$mode" > "$SCAN_MODE"
  if ! run promote demo --reviewed-by MK >/dev/null 2>&1 && blocked; then
    pass "scanner '$mode': promote is refused; skills/ and ledger untouched"
  else
    fail "scanner '$mode': promote went through or left something behind"
  fi
done
echo findings > "$SCAN_MODE"
run promote demo --reviewed-by MK >"$TMPROOT/out" 2>&1; rc=$?
if [[ $rc -eq 3 ]] && blocked && grep -q "TM1" "$TMPROOT/out"; then
  pass "an open finding blocks promote (exit 3) and is shown to the reviewer"
else
  fail "an open finding did not block promote (rc=$rc)"
fi
if ! run promote demo --reviewed-by MK --accept PE3 >/dev/null 2>&1 && blocked; then
  pass "accepting a DIFFERENT rule id does not unblock the finding"
else
  fail "--accept PE3 unblocked a TM1 finding"
fi
if run promote demo --reviewed-by MK --accept TM1 --notes "remapped | tool names" >"$TMPROOT/out" 2>&1 \
   && [[ -f "$SANDBOX/skills/demo/SKILL.md" && -f "$SANDBOX/skills/demo/scripts/helper.sh" ]]; then
  pass "naming the rule id (--accept TM1) promotes the skill, helper files included"
else
  fail "promote with the finding accepted failed: $(tail -3 "$TMPROOT/out")"
fi
row="$(grep '^| `demo`' "$LEDGER" || true)"
want_sha="$(sha256sum "$SANDBOX/skills/demo/SKILL.md" | cut -d' ' -f1)"
want_tree="$(cd "$SANDBOX/skills/demo" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)"
if [[ "$row" == *"$want_sha"* && "$row" == *"$want_tree"* ]]; then
  pass "the ledger row pins sha256sum's own SKILL.md hash and the documented tree hash"
else
  fail "ledger hashes do not match an independent sha256sum: $row"
fi
if [[ "$row" == *"$REV"* && "$row" == *"/tree/$REV/pack/demo"* && "$row" == *"| MK |"* \
      && "$row" == *"accepted TM1"* && "$row" == *'remapped \| tool names'* ]]; then
  pass "the row records the commit permalink, the reviewer, the note and the accepted rule"
else
  fail "ledger row is missing provenance fields: $row"
fi
if ! grep -q "none yet" "$LEDGER" && [[ "$(grep -c '^| `demo`' "$LEDGER")" -eq 1 ]]; then
  pass "the placeholder row is gone and there is exactly one row"
else
  fail "placeholder still present, or the row count is not 1"
fi
if grep -q '^prompt_index: false$' "$SANDBOX/skills/demo/SKILL.md" \
   && [[ "$(grep -c '^---$' "$SANDBOX/skills/demo/SKILL.md")" -eq 2 ]]; then
  pass "the import is off the always-on menu (prompt_index: false inside the frontmatter)"
else
  fail "prompt_index: false was not added inside the frontmatter"
fi

# --- 6. Refresh, in-house protection, frontmatter ---
echo ""
echo "Refresh and collisions:"
echo clean > "$SCAN_MODE"
echo "extra upstream line" >> "$FIXTURE/pack/demo/SKILL.md"
if fetch_demo --force >/dev/null 2>&1 && run diff demo >"$TMPROOT/out" 2>&1; [[ $? -eq 1 ]] \
   && grep -q "extra upstream line" "$TMPROOT/out"; then
  pass "diff shows the staged revision against the live skill"
else
  fail "diff did not show the upstream change"
fi
run promote demo --reviewed-by MK >/dev/null 2>&1
if [[ "$(grep -c '^| `demo`' "$LEDGER")" -eq 1 ]] && grep -q "extra upstream line" "$SANDBOX/skills/demo/SKILL.md"; then
  pass "a refresh REPLACES the row (still one) and updates the skill"
else
  fail "a refresh appended a second row or did not update the skill"
fi
rm -rf "$FIXTURE/pack/in-house"; cp -a "$FIXTURE/pack/demo" "$FIXTURE/pack/in-house"
sed -i 's/^name: demo$/name: in-house/' "$FIXTURE/pack/in-house/SKILL.md"
own_before="$(sha256sum "$SANDBOX/skills/in-house/SKILL.md")"
run fetch "$URL" --rev "$REV" --name in-house --path pack/in-house >/dev/null 2>&1
if ! run promote in-house --reviewed-by MK >"$TMPROOT/out" 2>&1 \
   && [[ "$(sha256sum "$SANDBOX/skills/in-house/SKILL.md")" == "$own_before" ]] && grep -q "one of ours" "$TMPROOT/out"; then
  pass "an import never overwrites an in-house skill (no ledger row)"
else
  fail "an import replaced an in-house skill"
fi
run fetch "$URL" --rev "$REV" --name renamed --path pack/demo >/dev/null 2>&1
if ! run promote renamed --reviewed-by MK >"$TMPROOT/out" 2>&1 && [[ ! -e "$SANDBOX/skills/renamed" ]] \
   && grep -q "frontmatter name" "$TMPROOT/out"; then
  pass "a frontmatter name that does not match the import name is refused"
else
  fail "a name mismatch was promoted"
fi

# --- 7. Verify catches drift ---
echo ""
echo "Verify:"
if run verify >/dev/null 2>&1; then
  pass "verify passes on an untouched import"
else
  fail "verify failed on an untouched import"
fi
echo "tampered" >> "$SANDBOX/skills/demo/scripts/helper.sh"
run verify >"$TMPROOT/out" 2>&1; rc=$?
if [[ $rc -eq 3 ]] && grep -q "tree SHA-256" "$TMPROOT/out" && ! grep -q "pinned SHA-256$" "$TMPROOT/out"; then
  pass "a changed HELPER file fails verify (tree hash), with SKILL.md still matching"
else
  fail "a tampered helper file was not caught (rc=$rc)"
fi
sed -i '$ d' "$SANDBOX/skills/demo/scripts/helper.sh"
echo "tampered" >> "$SANDBOX/skills/demo/SKILL.md"
run verify >"$TMPROOT/out" 2>&1; rc=$?
if [[ $rc -eq 3 ]] && grep -q "pinned SHA-256" "$TMPROOT/out"; then
  pass "a changed SKILL.md fails verify"
else
  fail "a tampered SKILL.md was not caught (rc=$rc)"
fi
rm -rf "$SANDBOX/skills/demo"
if ! run verify >/dev/null 2>&1; then
  pass "a ledger row whose skill is gone fails verify"
else
  fail "verify passed with a ledger row and no skill"
fi

# --- 8. The real ledger (read-only) ---
echo ""
echo "Real tree:"
if "$REPO_DIR/scripts/skill-import.sh" verify --quiet >"$TMPROOT/out" 2>&1; then
  pass "every imported skill in this repo matches skills/REMOTE_PROVENANCE.md"
else
  fail "the real ledger does not verify: $(head -3 "$TMPROOT/out")"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
