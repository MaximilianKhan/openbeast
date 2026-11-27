#!/bin/bash
# AGENT_PROFILE and the daily-agent switches in openbeast.conf (scripts/lib/conf.sh).
#
# Usage: bash tests/test_agent_profile_conf.sh
#
# The v5 T2 switches (act nudge, verify-done, env block, wall warnings, edit
# hint, the 8192 turn cap, the v3 zig pack) were env-only: agents/runner.py and
# agents/tools.py read them from the PROCESS env and nothing forwarded them from
# openbeast.conf, so a rig could not run its daily agents the way the best
# DeepSeek-V4.1-Flash (Mia EXL3) arm was measured. conf.sh now exports each
# key, and AGENT_PROFILE=deepseek-best supplies all of them in one line.
# AGENT_PROFILE=qwen38-best (the RTX 5090 default, arm Q_T2) is an alias for
# the same switch table: every check below that matters runs for both names.
#
# Runs the REAL conf.sh against a throwaway conf in a clean env (env -i): no
# stack, no real openbeast.conf. The reader checks pipe the exports into the
# REAL agents/tools.py, agents/runner.py and agents/lang/pack_context.py, so
# "exported" means "the reader agrees".

set -uo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

T="$(mktemp -d "${TMPDIR:-/tmp}/ob-agent-profile-XXXXXX")"
trap 'rm -rf "$T"' EXIT

SB="$T/rig"
mkdir -p "$SB/scripts/lib"
install -m 644 "$REPO_DIR/scripts/lib/conf.sh" "$SB/scripts/lib/"

# conf_eval <conf body> <env assignments…> -- <snippet>; stderr → $T/conf.err
conf_eval() {
  local body="$1"; shift
  local envs=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do envs+=("$1"); shift; done
  shift
  printf '%s\n' "$body" > "$SB/openbeast.conf"
  env -i HOME="$T" PATH="$PATH" REPO_DIR="$SB" ${envs[@]+"${envs[@]}"} \
    bash -c "source '$SB/scripts/lib/conf.sh'; $1" 2>"$T/conf.err"
}

# Every variable the profile can reach, as NAME=value (or NAME=ABSENT).
SHOW='for v in BEAST_ASSIST OPENBEAST_INFERENCE_THINKING OPENBEAST_AGENT_MAX_TOKENS \
  OPENBEAST_AGENT_ACT_NUDGE OPENBEAST_AGENT_VERIFY_DONE OPENBEAST_AGENT_ENV_BLOCK \
  OPENBEAST_AGENT_WALL_WARN OPENBEAST_DIAG_EDIT_HINT OPENBEAST_LANG_PACK_VARIANT; do
  if printenv "$v" >/dev/null; then printf "%s=%s\n" "$v" "$(printenv "$v")"; else printf "%s=ABSENT\n" "$v"; fi; done'

want_line() { # want_line <output> <NAME=value> <label>
  if grep -qxF "$2" <<< "$1"; then pass "$3"; else fail "$3 (want $2; got: $(tr '\n' ' ' <<< "$1"))"; fi
}

echo "=== AGENT_PROFILE=deepseek-best (lib/conf.sh) ==="

OUT="$(conf_eval 'AGENT_PROFILE=deepseek-best' -- "$SHOW")"
for kv in BEAST_ASSIST=1 OPENBEAST_INFERENCE_THINKING=on OPENBEAST_AGENT_MAX_TOKENS=8192 \
          OPENBEAST_AGENT_ACT_NUDGE=1 OPENBEAST_AGENT_VERIFY_DONE=1 OPENBEAST_AGENT_ENV_BLOCK=1 \
          OPENBEAST_AGENT_WALL_WARN=1 OPENBEAST_DIAG_EDIT_HINT=1 OPENBEAST_LANG_PACK_VARIANT=v3; do
  want_line "$OUT" "$kv" "the profile exports $kv"
done
if [[ ! -s "$T/conf.err" ]]; then
  pass "the profile alone produces no warnings"
else
  fail "warnings from a clean profile conf: $(cat "$T/conf.err")"
fi

# Spelling: case, quotes and a trailing comment.
OUT="$(conf_eval 'AGENT_PROFILE="DeepSeek-Best"   # T2, 65/71' -- "$SHOW")"
want_line "$OUT" OPENBEAST_AGENT_ACT_NUDGE=1 "quoted, mixed-case profile with a comment is read"

# Negative control: no profile, no keys -> nothing exported at all.
OUT="$(conf_eval '# nothing' -- "$SHOW")"
if [[ "$(grep -vc '=ABSENT$' <<< "$OUT")" == "0" ]]; then
  pass "negative control: no profile and no keys export nothing"
else
  fail "something exported with nothing configured: $(grep -v '=ABSENT$' <<< "$OUT" | tr '\n' ' ')"
fi
OUT="$(conf_eval 'AGENT_PROFILE=none' -- "$SHOW")"
if [[ "$(grep -vc '=ABSENT$' <<< "$OUT")" == "0" && ! -s "$T/conf.err" ]]; then
  pass "AGENT_PROFILE=none is no profile, silently"
else
  fail "AGENT_PROFILE=none exported or warned: $OUT / $(cat "$T/conf.err")"
fi

# An unknown profile is ignored and said so (a typo must not half-apply).
OUT="$(conf_eval 'AGENT_PROFILE=deepseek-bset' -- "$SHOW")"
if [[ "$(grep -vc '=ABSENT$' <<< "$OUT")" == "0" ]] && grep -q "AGENT_PROFILE='deepseek-bset'" "$T/conf.err"; then
  pass "an unknown profile exports nothing and is reported"
else
  fail "unknown profile: $(tr '\n' ' ' <<< "$OUT") / stderr: $(cat "$T/conf.err")"
fi

# The lint names every valid profile.
if grep -q "is not one of: deepseek-best qwen38-best inkling-best, none" "$T/conf.err"; then
  pass "the unknown-profile warning lists deepseek-best, qwen38-best and inkling-best"
else
  fail "unknown-profile warning does not list the names: $(cat "$T/conf.err")"
fi

echo ""
echo "=== AGENT_PROFILE=inkling-best / qwen38-best (the RTX 5090 default and its fallback, same table) ==="

DS="$(conf_eval 'AGENT_PROFILE=deepseek-best' -- "$SHOW")"
for name in inkling-best qwen38-best; do
  OUT="$(conf_eval "AGENT_PROFILE=$name" -- "$SHOW")"
  for kv in BEAST_ASSIST=1 OPENBEAST_INFERENCE_THINKING=on OPENBEAST_AGENT_MAX_TOKENS=8192 \
            OPENBEAST_AGENT_ACT_NUDGE=1 OPENBEAST_AGENT_VERIFY_DONE=1 OPENBEAST_AGENT_ENV_BLOCK=1 \
            OPENBEAST_AGENT_WALL_WARN=1 OPENBEAST_DIAG_EDIT_HINT=1 OPENBEAST_LANG_PACK_VARIANT=v3; do
    want_line "$OUT" "$kv" "$name exports $kv"
  done
  if [[ ! -s "$T/conf.err" ]]; then
    pass "$name alone produces no warnings (the lint accepts the name)"
  else
    fail "warnings from a $name conf: $(cat "$T/conf.err")"
  fi
  if [[ "$OUT" == "$DS" ]]; then
    pass "$name and deepseek-best export identical values"
  else
    fail "the two names differ: $(diff <(echo "$DS") <(echo "$OUT") | tr '\n' ' ')"
  fi
done
OUT="$(conf_eval 'AGENT_PROFILE="Qwen38-Best"   # Q_T2, 52/71' -- "$SHOW")"
want_line "$OUT" OPENBEAST_AGENT_ACT_NUDGE=1 "quoted, mixed-case qwen38-best with a comment is read"

# The rig's openbeast.conf today: BEAST_ASSIST=1 on its own line, no conflict.
OUT="$(conf_eval 'BEAST_ASSIST=1
AGENT_PROFILE=qwen38-best' -- "$SHOW")"
if [[ "$OUT" == "$DS" && ! -s "$T/conf.err" ]]; then
  pass "the rig's conf (BEAST_ASSIST=1 + qwen38-best) resolves exactly the profile, silently"
else
  fail "rig conf: $(tr '\n' ' ' <<< "$OUT") / $(cat "$T/conf.err")"
fi

# Precedence holds for the alias too.
OUT="$(conf_eval 'AGENT_PROFILE=qwen38-best
AGENT_ACT_NUDGE=off
BEAST_ASSIST=0
INFERENCE_THINKING=off
LANG_PACK_VARIANT=v1
AGENT_MAX_TOKENS=16384' -- "$SHOW")"
want_line "$OUT" OPENBEAST_AGENT_ACT_NUDGE=0 "qwen38-best: AGENT_ACT_NUDGE=off in the conf wins"
want_line "$OUT" BEAST_ASSIST=0 "qwen38-best: BEAST_ASSIST=0 in the conf wins"
want_line "$OUT" OPENBEAST_INFERENCE_THINKING=off "qwen38-best: INFERENCE_THINKING=off in the conf wins"
want_line "$OUT" OPENBEAST_LANG_PACK_VARIANT=v1 "qwen38-best: LANG_PACK_VARIANT=v1 in the conf wins"
want_line "$OUT" OPENBEAST_AGENT_MAX_TOKENS=ABSENT "qwen38-best: a conf AGENT_MAX_TOKENS suppresses the 8192 export"
want_line "$OUT" OPENBEAST_AGENT_WALL_WARN=1 "qwen38-best: keys not overridden keep the profile's value"
OUT="$(conf_eval 'AGENT_PROFILE=qwen38-best' OPENBEAST_AGENT_VERIFY_DONE=0 OPENBEAST_AGENT_MAX_TOKENS=4096 -- "$SHOW")"
want_line "$OUT" OPENBEAST_AGENT_VERIFY_DONE=0 "qwen38-best: env OPENBEAST_AGENT_VERIFY_DONE=0 wins"
want_line "$OUT" OPENBEAST_AGENT_MAX_TOKENS=4096 "qwen38-best: env OPENBEAST_AGENT_MAX_TOKENS is left as set"
OUT="$(conf_eval 'AGENT_PROFILE=deepseek-best' OPENBEAST_AGENT_PROFILE=qwen38-best -- "$SHOW")"
want_line "$OUT" OPENBEAST_AGENT_ENV_BLOCK=1 "env OPENBEAST_AGENT_PROFILE=qwen38-best is accepted over the conf's"

echo ""
echo "=== explicit keys win over the profile ==="

OUT="$(conf_eval 'AGENT_PROFILE=deepseek-best
AGENT_ACT_NUDGE=off
BEAST_ASSIST=0
INFERENCE_THINKING=off
LANG_PACK_VARIANT=v1
DIAG_EDIT_HINT=false   # not on this rig' -- "$SHOW")"
want_line "$OUT" OPENBEAST_AGENT_ACT_NUDGE=0 "AGENT_ACT_NUDGE=off in the conf beats the profile (exported 0)"
want_line "$OUT" BEAST_ASSIST=0 "BEAST_ASSIST=0 in the conf beats the profile"
want_line "$OUT" OPENBEAST_INFERENCE_THINKING=off "INFERENCE_THINKING=off in the conf beats the profile"
want_line "$OUT" OPENBEAST_LANG_PACK_VARIANT=v1 "LANG_PACK_VARIANT=v1 in the conf beats the profile"
want_line "$OUT" OPENBEAST_DIAG_EDIT_HINT=0 "DIAG_EDIT_HINT=false # comment is canonicalised to 0"
want_line "$OUT" OPENBEAST_AGENT_VERIFY_DONE=1 "the keys not overridden keep the profile's value"

# AGENT_MAX_TOKENS in the conf: runner.py reads it itself, so the profile's
# 8192 must NOT be exported over it (an env value beats the conf there).
OUT="$(conf_eval 'AGENT_PROFILE=deepseek-best
AGENT_MAX_TOKENS=16384' -- "$SHOW")"
want_line "$OUT" OPENBEAST_AGENT_MAX_TOKENS=ABSENT "a conf AGENT_MAX_TOKENS suppresses the profile's 8192 export"

# The launching shell beats both.
OUT="$(conf_eval 'AGENT_PROFILE=deepseek-best' OPENBEAST_AGENT_ACT_NUDGE=0 OPENBEAST_AGENT_MAX_TOKENS=4096 \
       BEAST_ASSIST=0 OPENBEAST_AGENT_PROFILE=none -- "$SHOW")"
want_line "$OUT" OPENBEAST_AGENT_ACT_NUDGE=0 "env OPENBEAST_AGENT_ACT_NUDGE=0 beats the profile"
want_line "$OUT" OPENBEAST_AGENT_MAX_TOKENS=4096 "env OPENBEAST_AGENT_MAX_TOKENS is left as the caller set it"
want_line "$OUT" BEAST_ASSIST=0 "env BEAST_ASSIST=0 beats the profile"
want_line "$OUT" OPENBEAST_AGENT_ENV_BLOCK=ABSENT "env OPENBEAST_AGENT_PROFILE=none drops the conf's profile"

# Each switch on its own, without a profile.
OUT="$(conf_eval 'AGENT_VERIFY_DONE=yes
AGENT_ENV_BLOCK=1
AGENT_WALL_WARN=on' -- "$SHOW")"
want_line "$OUT" OPENBEAST_AGENT_VERIFY_DONE=1 "AGENT_VERIFY_DONE=yes alone exports 1"
want_line "$OUT" OPENBEAST_AGENT_WALL_WARN=1 "AGENT_WALL_WARN=on alone exports 1"
want_line "$OUT" OPENBEAST_AGENT_ACT_NUDGE=ABSENT "negative control: an unset switch stays unexported"

# A bad pack variant is reported (pack_context then serves no pack).
conf_eval 'LANG_PACK_VARIANT=v2' -- 'true' >/dev/null
if grep -q "LANG_PACK_VARIANT='v2' is not one of: v1 v3" "$T/conf.err"; then
  pass "LANG_PACK_VARIANT=v2 (never measured in production) is reported"
else
  fail "LANG_PACK_VARIANT=v2 was silent: $(cat "$T/conf.err")"
fi

echo ""
echo "=== re-sourcing: derived values follow the conf (start.sh -d) ==="

# A shell (or the -d daemon, which gets the OPENBEAST_* env forwarded) that
# sourced the profile once must not pin it after the conf drops it.
OUT="$(conf_eval 'AGENT_PROFILE=deepseek-best' -- "printf 'AGENT_PROFILE=none\n' > '$SB/openbeast.conf'; source '$SB/scripts/lib/conf.sh'; $SHOW")"
if [[ "$(grep -vc '=ABSENT$' <<< "$OUT")" == "1" ]] && grep -qxF BEAST_ASSIST=1 <<< "$OUT"; then
  # BEAST_ASSIST keeps its pre-existing (non-derived) export semantics.
  pass "after the profile is removed, every OPENBEAST_ switch it set is dropped on re-source"
else
  fail "re-source kept profile values: $(grep -v '=ABSENT$' <<< "$OUT" | tr '\n' ' ')"
fi

echo ""
echo "=== the readers agree ==="

# What conf.sh exports, the real readers honour.
for prof in deepseek-best qwen38-best inkling-best; do
  OUT="$(conf_eval "AGENT_PROFILE=$prof" -- "cd '$REPO_DIR/agents' && python3 -c '
import tools
from lang import pack_context as pc
print(tools.diagnostics_enabled(), tools.diag_edit_hint_enabled(), tools.verify_done_enabled(), pc.pack_variant()[0])'")"
  if [[ "$OUT" == "True True True v3" ]]; then
    pass "$prof: tools.py (diagnostics, edit hint, verify-done) and pack_context (v3) read the profile"
  else
    fail "$prof: readers disagree: '$OUT' ($(tail -3 "$T/conf.err"))"
  fi
done

# runner.py needs the openai package; use the eval venv when python3 lacks it.
RPY=""
for p in python3 "$HOME/.local/opt/openbeast-eval-venv/bin/python"; do
  if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import openai' 2>/dev/null; then RPY="$p"; break; fi
done
if [[ -n "$RPY" ]]; then
  for prof in deepseek-best qwen38-best inkling-best; do
    OUT="$(conf_eval "AGENT_PROFILE=$prof" -- "cd '$REPO_DIR/agents' && '$RPY' -c '
import runner
print(runner.act_nudge_enabled(), runner.env_block_enabled(), runner.wall_warn_enabled(),
      runner._max_completion_tokens(), runner._inference_thinking())'")"
    if [[ "$OUT" == "True True True 8192 on" ]]; then
      pass "$prof: runner.py: act nudge, env block, wall warn on, cap 8192, thinking on"
    else
      fail "$prof: runner.py disagrees: '$OUT' ($(tail -3 "$T/conf.err"))"
    fi
  done
  # ...and never inside an eval for what the runner gates on OPENBEAST_EVAL:
  # thinking follows only the arm's own switch there.
  OUT="$(conf_eval 'AGENT_PROFILE=deepseek-best' OPENBEAST_EVAL=1 -- "cd '$REPO_DIR/agents' && '$RPY' -c '
import runner
print(runner._agent_thinking_kwargs())'")"
  if [[ "$OUT" == "{}" ]]; then
    pass "under OPENBEAST_EVAL the host thinking default is not sent"
  else
    fail "eval run followed INFERENCE_THINKING: '$OUT'"
  fi
else
  echo "  SKIP: no python with the openai package; runner.py reader checks not run"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
