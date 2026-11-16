#!/bin/bash
# The engine plane (docs/ENGINES.md): which inference engine this host runs,
# which version of it, and how to swap / update / roll it back. Sourced by
# scripts/engine.sh, scripts/engine-serve.sh and — through small, delimited
# hooks — start.sh, stop.sh, healthcheck.sh and doctor.sh. Defines functions
# only: no output, no `set --`, no shell-option changes at source time.
#
# One ADAPTER per engine, scripts/engines/<name>.sh, defining eng_<name>_*
# functions (the contract is in docs/ENGINES.md "Adapter contract"). This file knows no
# engine: it discovers adapters, dispatches to them, and owns the parts every
# engine shares — host class, defaults, the pin history, conf writes.
#
# Pins:
#   llama       scripts/llama.cpp.ref (tracked — what a fresh bootstrap builds;
#               moving it is a reviewed change, docs/UPDATING.md)
#   vllm        ENGINE_PIN_VLLM=<tag>@sha256:<registry digest>   (openbeast.conf)
#   tensorfold  ENGINE_PIN_TENSORFOLD=<version>@sha256:<archive sha256>
#   maya        ENGINE_PIN_MAYA=<tag>@<40-hex commit> (reviewed per version in
#               scripts/engines/maya.sh; gated, see ob_engine_gate_ok)
# History: $OB_ENGINE_STATE_DIR/history.tsv (append-only; rollback reads it),
# conf backups: $OB_ENGINE_STATE_DIR/conf-backups/ (one per conf write).
#
# Requires REPO_DIR.

: "${REPO_DIR:?REPO_DIR must be set before sourcing lib/engine.sh}"
OB_ENGINE_ADAPTER_DIR="${OB_ENGINE_ADAPTER_DIR:-$REPO_DIR/scripts/engines}"
OB_ENGINE_STATE_DIR="${OPENBEAST_ENGINE_STATE_DIR:-$REPO_DIR/.engines}"
# The ones that ship, in display order; any other adapter file is appended.
# maya is opt-in and gated (ob_engine_gate_ok): listed, never a default.
OB_ENGINES_SHIPPED="llama vllm tensorfold maya"

# ob_engines — every engine with an adapter, shipped ones first.
ob_engines() {
  local e f out=""
  for e in $OB_ENGINES_SHIPPED; do
    [[ -f "$OB_ENGINE_ADAPTER_DIR/$e.sh" ]] && out+="$e "
  done
  for f in "$OB_ENGINE_ADAPTER_DIR"/*.sh; do
    [[ -e "$f" ]] || continue
    e="$(basename "$f" .sh)"
    [[ "$e" =~ ^[a-z][a-z0-9-]*$ ]] || continue
    [[ " $out " == *" $e "* ]] || out+="$e "
  done
  printf '%s\n' "${out% }"
}

# ob_engine_valid <name> — an adapter exists for it.
ob_engine_valid() {
  [[ "${1:-}" =~ ^[a-z][a-z0-9-]*$ && -f "$OB_ENGINE_ADAPTER_DIR/$1.sh" ]]
}

# ob_engine_fn <engine> <fn> — the adapter function's name (dashes → _).
ob_engine_fn() { local e="${1//-/_}"; printf 'eng_%s_%s' "$e" "$2"; }

# ob_engine_load <engine> — source its adapter once. 1 when there is none.
ob_engine_load() {
  local e="$1" v
  ob_engine_valid "$e" || return 1
  v="_OB_ENGINE_LOADED_${e//-/_}"
  [[ -n "${!v:-}" ]] && return 0
  # shellcheck source=/dev/null
  source "$OB_ENGINE_ADAPTER_DIR/$e.sh" || return 1
  printf -v "$v" 1
}

# ob_engine_has <engine> <fn> — the adapter defines that function.
ob_engine_has() {
  ob_engine_load "$1" || return 1
  declare -F "$(ob_engine_fn "$1" "$2")" >/dev/null 2>&1
}

# ob_engine_call <engine> <fn> [args] — call it; 127 when it is not defined.
ob_engine_call() {
  local e="$1" fn="$2"; shift 2
  ob_engine_has "$e" "$fn" || return 127
  "$(ob_engine_fn "$e" "$fn")" "$@"
}

# ── Host class ─────────────────────────────────────────────────────────────
# rig    any x86_64 / discrete-GPU Linux box (the RTX 5090 reference rig)
# spark  NVIDIA GB10 (DGX Spark): aarch64, unified memory — OB_GPU_UNIFIED=1
# mac    macOS (client mode; engines run elsewhere unless you install one)
# OB_ENGINE_HOST_CLASS overrides (tests, or a box detection gets wrong).
ob_engine_host_class() {
  if [[ -n "${OB_ENGINE_HOST_CLASS:-}" ]]; then
    printf '%s\n' "$OB_ENGINE_HOST_CLASS"; return 0
  fi
  if [[ "$(uname -s 2>/dev/null)" == Darwin ]]; then
    printf 'mac\n'; return 0
  fi
  if ! declare -F ob_detect_gpu >/dev/null 2>&1 \
     && [[ -f "$REPO_DIR/scripts/lib/hardware.sh" ]]; then
    # shellcheck source=hardware.sh
    source "$REPO_DIR/scripts/lib/hardware.sh"
  fi
  if declare -F ob_detect_gpu >/dev/null 2>&1; then
    ob_detect_gpu >/dev/null 2>&1 || true
    if [[ "${OB_GPU_UNIFIED:-0}" -eq 1 ]]; then printf 'spark\n'; return 0; fi
  fi
  printf 'rig\n'
}

# ob_engine_arch — x86_64 | aarch64 (OB_ENGINE_ARCH overrides).
ob_engine_arch() {
  local m="${OB_ENGINE_ARCH:-$(uname -m 2>/dev/null)}"
  case "$m" in
    aarch64|arm64) printf 'aarch64\n' ;;
    *)             printf '%s\n' "$m" ;;
  esac
}

# ob_engine_default [class] — the engine OpenBeast picks for a host class:
# rig → llama (GGUF, the measured default), spark → tensorfold (unified
# memory, TP across Sparks), mac → llama. ENGINE_DEFAULT (conf / env
# OPENBEAST_ENGINE_DEFAULT) overrides when it names an adapter.
ob_engine_default() {
  local c="${1:-$(ob_engine_host_class)}" d
  d="${OPENBEAST_ENGINE_DEFAULT:-$(ob_engine_conf_get ENGINE_DEFAULT)}"
  if [[ -n "$d" ]]; then
    if ob_engine_valid "$d"; then printf '%s\n' "$d"; return 0; fi
    echo "WARNING: ENGINE_DEFAULT='$d' names no engine adapter (have: $(ob_engines)) — using the host-class default." >&2
  fi
  case "$c" in
    spark) printf 'tensorfold\n' ;;
    *)     printf 'llama\n' ;;
  esac
}

# ── openbeast.conf: read and write single keys ─────────────────────────────
_ob_engine_conf() { printf '%s\n' "${OB_ENGINE_CONF:-$REPO_DIR/openbeast.conf}"; }

# ob_engine_conf_get <key> — last assignment, `# comment` and one layer of
# quotes dropped. Empty when absent. (conf.sh's _ob_conf_value semantics,
# without needing conf.sh sourced.)
ob_engine_conf_get() {
  local conf line
  conf="$(_ob_engine_conf)"
  [[ -f "$conf" ]] || return 0
  line="$(grep -E "^[[:space:]]*${1}[[:space:]]*=" "$conf" 2>/dev/null | tail -n1 || true)"
  [[ -n "$line" ]] || return 0
  line="${line#*=}"
  line="${line#"${line%%[![:space:]]*}"}"
  if [[ "$line" == \"*\"* ]]; then line="${line#\"}"; line="${line%%\"*}"
  elif [[ "$line" == \'*\'* ]]; then line="${line#\'}"; line="${line%%\'*}"
  else line="${line%%[[:space:]]#*}"; line="${line%"${line##*[![:space:]]}"}"
  fi
  printf '%s\n' "$line"
}

# ob_engine_conf_backup — copy openbeast.conf aside once per process
# (0600), so every `engine.sh use` is reversible by hand too.
_OB_ENGINE_CONF_BACKED_UP=""
ob_engine_conf_backup() {
  local conf dst
  conf="$(_ob_engine_conf)"
  [[ -f "$conf" && -z "$_OB_ENGINE_CONF_BACKED_UP" ]] || return 0
  ( umask 077; mkdir -p "$OB_ENGINE_STATE_DIR/conf-backups" )
  dst="$OB_ENGINE_STATE_DIR/conf-backups/openbeast.conf.$(date '+%Y%m%dT%H%M%S').$$"
  ( umask 077; cp -p "$conf" "$dst" )
  _OB_ENGINE_CONF_BACKED_UP="$dst"
}

# ob_engine_file_set <file> <key> <value> — replace every assignment of KEY with
# one (at the first one's place), or append. Comments stay. Atomic, keeps
# the file's mode (a new file is 0600: it can hold keys).
# One key, one line: a KEY that is not a shell-style name, or a VALUE with a
# CR or LF in it, is refused (return 2, the file untouched). Values reach
# here from install results whose text came from a staged MANIFEST; a line
# break in one would write extra KEY=VALUE lines (BIND_HOST=0.0.0.0, …) that
# conf.sh's last-assignment-wins then obeys. The line reaches awk through
# ENVIRON, never `-v`: -v expands backslash escapes, so a literal `\n` in
# the value became a real newline there.
ob_engine_file_set() {
  local f="$1" key="$2" val="$3" tmp
  if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "refusing to write '$key' to $f: not a KEY name" >&2; return 2
  fi
  if [[ "$val" == *$'\n'* || "$val" == *$'\r'* ]]; then
    echo "refusing to write $key to $f: its value has a line break in it" >&2; return 2
  fi
  tmp="$(mktemp "${f}.engXXXXXX")"
  if [[ -f "$f" ]]; then
    chmod --reference="$f" "$tmp" 2>/dev/null || chmod 600 "$tmp"
    _OB_FS_KEY="$key" _OB_FS_LINE="$key=$val" awk '
      $0 ~ "^[[:space:]]*" ENVIRON["_OB_FS_KEY"] "[[:space:]]*=" { if (!done) { print ENVIRON["_OB_FS_LINE"]; done = 1 } ; next }
      { print }
      END { if (!done) print ENVIRON["_OB_FS_LINE"] }' "$f" > "$tmp"
  else
    chmod 600 "$tmp"
    printf '%s=%s\n' "$key" "$val" > "$tmp"
  fi
  mv -f "$tmp" "$f"
}

# ob_engine_conf_set <key> <value> — ob_engine_file_set on openbeast.conf, after
# a once-per-process backup.
ob_engine_conf_set() {
  ob_engine_conf_backup
  ob_engine_file_set "$(_ob_engine_conf)" "$1" "$2"
}

# ob_engine_conf_unset <key> — delete every assignment of KEY (comments stay).
ob_engine_conf_unset() {
  local key="$1" conf tmp
  conf="$(_ob_engine_conf)"
  [[ -f "$conf" ]] || return 0
  grep -qE "^[[:space:]]*${key}[[:space:]]*=" "$conf" || return 0
  ob_engine_conf_backup
  tmp="$(mktemp "${conf}.engXXXXXX")"
  chmod --reference="$conf" "$tmp" 2>/dev/null || chmod 600 "$tmp"
  awk -v k="$key" '$0 ~ "^[[:space:]]*" k "[[:space:]]*=" { next } { print }' "$conf" > "$tmp"
  mv -f "$tmp" "$conf"
}

# ── Pins + history ─────────────────────────────────────────────────────────
# ob_engine_pin_key <engine> — ENGINE_PIN_<NAME> (llama has none: its pin is
# scripts/llama.cpp.ref, read by its adapter).
ob_engine_pin_key() { local u; u="$(printf '%s' "${1//-/_}" | tr 'a-z' 'A-Z')"; printf 'ENGINE_PIN_%s\n' "$u"; }

# ob_engine_pin <engine> — "<version>@<ref>" or empty.
ob_engine_pin() {
  if ob_engine_has "$1" pin; then ob_engine_call "$1" pin; return 0; fi
  ob_engine_conf_get "$(ob_engine_pin_key "$1")"
}

# ob_engine_history_add <engine> <action> <version> <ref> [note]
ob_engine_history_add() {
  ( umask 077; mkdir -p "$OB_ENGINE_STATE_DIR" )
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$1" "$2" "$3" "$4" "${5:-}" \
    >> "$OB_ENGINE_STATE_DIR/history.tsv"
}

# ob_engine_history [engine] — the history, oldest first (TSV:
# time engine action version ref note).
ob_engine_history() {
  local f="$OB_ENGINE_STATE_DIR/history.tsv"
  [[ -f "$f" ]] || return 0
  if [[ -n "${1:-}" ]]; then awk -F'\t' -v e="$1" '$2 == e' "$f"; else cat "$f"; fi
}

# ob_engine_pin_set <engine> <version> <ref> <action> [note] — record a pin.
# For engines whose pin is a conf key, write it; the adapter's own pin file
# (llama) is written by the adapter. Either way, one history line.
ob_engine_pin_set() {
  local e="$1" v="$2" r="$3" a="$4" n="${5:-}"
  ob_engine_pin_valid "$v" "$r" || return 2
  if ! ob_engine_has "$e" pin; then
    ob_engine_conf_set "$(ob_engine_pin_key "$e")" "$v@$r" || return 2
  fi
  ob_engine_history_add "$e" "$a" "$v" "$r" "${n//[$'\t\r\n']/ }"
}

# ob_engine_pin_valid <version> <ref> — the shape every pin has: a version
# name (v1.1.65, b8000, 1.2.9, v0.33.0, a 12-hex llama.cpp commit) and an
# immutable ref (40-hex commit, sha256:<64-hex>). Both end up in
# openbeast.conf, spark.env and history.tsv, and the version names a
# directory, so anything else — a newline, a `/`, `..` — is refused with
# the reason, whoever produced it (an adapter, or a MANIFEST it read).
ob_engine_pin_valid() {
  if [[ ! "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$ ]]; then
    echo "refusing to pin version '$1': not a version name ([A-Za-z0-9._+-], at most 64)" >&2; return 1
  fi
  if [[ ! "$2" =~ ^([0-9a-f]{40}|sha256:[0-9a-f]{64})$ ]]; then
    echo "refusing to pin ref '$2': not a 40-hex commit or sha256:<64-hex>" >&2; return 1
  fi
}

# ob_engine_prev_pin <engine> — the pin before the current one: walking the
# history newest-first, the first install/update/rollback entry whose ref
# differs from the current pin's. Prints "version<TAB>ref<TAB>note".
ob_engine_prev_pin() {
  local e="$1" cur
  cur="$(ob_engine_pin "$e")"
  # By REF only: a version's display name can change (llama: a hash until the
  # tag is known locally), the immutable ref cannot.
  ob_engine_history "$e" | awk -F'\t' -v cur="${cur#*@}" '
    $3 ~ /^(install|update|rollback|adopt)$/ { n++; v[n] = $4; r[n] = $5; note[n] = $6 }
    END {
      for (i = n; i >= 1; i--) if (r[i] != cur) { print v[i] "\t" r[i] "\t" note[i]; exit }
    }'
}

# ── Helpers adapters share ─────────────────────────────────────────────────
ob_engine_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

# ob_engine_offline — OFFLINE=true (conf.sh) or OPENBEAST_OFFLINE.
ob_engine_offline() {
  [[ "${OFFLINE:-${OPENBEAST_OFFLINE:-false}}" == "true" ]]
}

# ob_engine_manifest_get <file> <KEY> — KEY=VALUE manifests written by
# `engine.sh stage` (parsed, never sourced: the folder travelled).
ob_engine_manifest_get() {
  local line
  line="$(grep -E "^$2=" "$1" 2>/dev/null | tail -n1 || true)"
  printf '%s\n' "${line#*=}"
}

# ob_engine_github_latest <owner/repo> — the latest release's tag_name
# (api.github.com; OB_ENGINE_GITHUB_API overrides the base for tests and
# mirrors). Fails offline.
ob_engine_github_latest() {
  local api="${OB_ENGINE_GITHUB_API:-https://api.github.com}" body
  if ob_engine_offline; then
    echo "OFFLINE=true — cannot ask GitHub for the latest $1 release; pass --version V (and --from DIR)." >&2
    return 1
  fi
  body="$(curl -fsSL -m 20 -H 'Accept: application/vnd.github+json' "$api/repos/$1/releases/latest")" || {
    echo "could not read $api/repos/$1/releases/latest" >&2; return 1; }
  # The release NAME when it is a plain vX.Y.Z: vLLM's v0.33.0 release object
  # carries the tag v0.33.0rc3 (both tags name one commit; read 2026-11-15).
  printf '%s' "$body" | python3 -c '
import json, re, sys
d = json.load(sys.stdin)
n = (d.get("name") or "").strip()
print(n if re.fullmatch(r"v?\d+\.\d+\.\d+", n) else d["tag_name"])'
}

# ob_engine_profile_field <profile> <KEY> — one resolved profile key via
# scripts/backends/pylib/obprofile.py (parsed, never sourced).
ob_engine_profile_load() { # ob_engine_profile_load <profile> <engine> — sets PF_* (lib.sh)
  if ! declare -F sp_profile_load >/dev/null 2>&1; then
    # shellcheck source=../backends/lib.sh
    source "$REPO_DIR/scripts/backends/lib.sh"
  fi
  sp_profile_load "$1" "$2"
}

# ob_engine_models_dir — where verified checkpoints live (model-fetch.sh):
# ENGINE_MODELS_DIR, else MODELS_DIR, else ~/openbeast-models (spark.env's).
ob_engine_models_dir() {
  local d="${OPENBEAST_ENGINE_MODELS_DIR:-$(ob_engine_conf_get ENGINE_MODELS_DIR)}"
  d="${d:-${MODELS_DIR:-$HOME/openbeast-models}}"
  printf '%s\n' "${d/#\~/$HOME}"
}

# ob_engine_container_name <engine> — the managed container's name, unique
# per checkout (two worktrees on one box must not stop each other's).
ob_engine_container_name() {
  local h
  h="$(printf '%s' "$REPO_DIR" | { sha256sum 2>/dev/null || shasum -a 256; } | cut -c1-8)"
  printf 'openbeast-engine-%s-%s\n' "$1" "$h"
}

# ob_engine_url_hostport <url> — "host port" of a base URL (port 80/443
# when absent). IPv6 brackets dropped.
ob_engine_url_hostport() {
  local u="${1#*://}" h p
  u="${u%%/*}"
  if [[ "$u" == \[* ]]; then h="${u#[}"; h="${h%%]*}"; p="${u##*]}"; p="${p#:}"
  elif [[ "$u" == *:* ]]; then h="${u%:*}"; p="${u##*:}"
  else h="$u"; p=""; fi
  [[ -n "$p" ]] || { [[ "$1" == https://* ]] && p=443 || p=80; }
  printf '%s %s\n' "$h" "$p"
}

# ── The engine gate (docs/WEIGHT_STREAMING.md "The engine gate") ──────────
# A single-author engine (Project Maya) is admitted only after it measured
# at least 1.5 times llama.cpp's decode tok/s on THIS rig, same weights, same
# prompt (ob_engine_gate_ratio may raise that bar, never lower it):
# scripts/engine-gate.sh runs both and writes
# $OB_ENGINE_STATE_DIR/gate/<engine>.json (scripts/tokps.py gate).
# OPENBEAST_ENGINE_GATE=measuring lets engine-gate.sh itself launch the
# candidate before a verdict exists; nothing else sets it.
#
# A verdict admits exactly what was measured. It records the host and the
# adapter's binding (eng_<e>_gate_binding <profile>: for Maya the installed
# commit, the profile and its GGUF), and ob_engine_gate_ok compares them
# with what is about to run: a new Maya commit, another profile or other
# weights is unmeasured, so it is gated again until engine-gate.sh re-runs.
ob_engine_gate_file() { printf '%s/gate/%s.json\n' "$OB_ENGINE_STATE_DIR" "$1"; }

# ob_engine_gate_ratio — the bar: ENGINE_GATE_RATIO from the environment
# (OPENBEAST_ENGINE_GATE_RATIO, which start.sh -d forwards, or the bare name)
# or openbeast.conf, never below 1.5. One value for the gate run, `use` and
# the daemon's launch, so a raised bar cannot print PASS and then refuse.
ob_engine_gate_ratio() {
  local r="${OPENBEAST_ENGINE_GATE_RATIO:-${ENGINE_GATE_RATIO:-$(ob_engine_conf_get ENGINE_GATE_RATIO)}}"
  awk -v r="$r" 'BEGIN { if (r !~ /^[0-9]+(\.[0-9]+)?$/ || r + 0 < 1.5) r = 1.5; print r + 0 }'
}

# ob_engine_gate_binding <engine> [profile] — KEY=VALUE lines naming what a
# verdict for this engine + profile must have measured. Empty when the
# adapter defines no eng_<e>_gate_binding (or no profile is given); 1 when
# the adapter cannot say (an invalid profile).
ob_engine_gate_binding() {
  [[ -n "${2:-}" ]] || return 0
  ob_engine_has "$1" gate_binding || return 0
  ob_engine_call "$1" gate_binding "$2"
}

# ob_engine_gate_ok <engine> [profile] — 0 when a passing verdict is on file
# for this host and, given a profile, for what it binds (or the gate run
# itself is measuring). Prints why not on stderr.
ob_engine_gate_ok() {
  local e="$1" prof="${2:-}" f out need bind
  [[ "${OPENBEAST_ENGINE_GATE:-}" == measuring ]] && return 0
  f="$(ob_engine_gate_file "$e")"
  need="$(ob_engine_gate_ratio)"
  if [[ ! -f "$f" ]]; then
    echo "$e is gated: no measurement on file ($f). It must decode >= ${need}x llama.cpp's tok/s on this rig first: scripts/engine-gate.sh $e --profile NAME --serve-script serve-<same weights>.sh (docs/WEIGHT_STREAMING.md)" >&2
    return 1
  fi
  bind="$(ob_engine_gate_binding "$e" "$prof")" || {
    echo "$e is gated: cannot tell what profile '$prof' would run (above)" >&2; return 1; }
  out="$(python3 -c '
import json, platform, sys
d = json.load(open(sys.argv[1]))
need = float(sys.argv[3])
why = []
if not (d.get("schema") == "openbeast-engine-gate/1" and d.get("engine") == sys.argv[2]):
    why.append("not a verdict for " + sys.argv[2])
if d.get("host") != platform.node():
    why.append("measured on %r, this is %r" % (d.get("host"), platform.node()))
bound = d.get("bound") if isinstance(d.get("bound"), dict) else {}
for kv in filter(None, sys.argv[4].split("\n")):
    k, _, v = kv.partition("=")
    if bound.get(k) != v:
        why.append("measured %s %s, this would run %s" % (k, bound.get(k) or "(not recorded)", v))
ok = not why and d.get("pass") is True and float(d.get("ratio") or 0) >= need
print(("PASS " if ok else "FAIL ") + "%sx (needs %sx), %s vs %s tok/s, %s" % (
    d.get("ratio"), need, d.get("candidate_tps"), d.get("baseline_tps"), d.get("time"))
    + ("; " + "; ".join(why) if why else ""))
sys.exit(0 if ok else 1)' "$f" "$e" "$need" "$bind" 2>&1)" && return 0
  echo "$e is gated and its last measurement does not admit this: ${out:-unreadable $f}. Re-measure: scripts/engine-gate.sh $e --profile ${prof:-NAME} ..." >&2
  return 1
}

# ── Hooks for start.sh / stop.sh / healthcheck.sh / doctor.sh ──────────────
# ob_engine_managed_here — INFERENCE_MANAGED=true for a non-llama engine:
# start.sh launches scripts/engine-serve.sh in place of a serve script.
ob_engine_managed_here() {
  [[ "${INFERENCE_BACKEND:-llama}" != "llama" && "${INFERENCE_MANAGED:-false}" == "true" ]]
}

# ob_engine_can_manage <engine> — the adapter has a single-host launcher.
ob_engine_can_manage() { ob_engine_has "$1" launch; }

# ob_engine_owns_port <pid> <port> — the listener on <port> is the engine
# start.sh launched as <pid>. Adapters whose server is not a descendant of
# that pid (a container) answer it; 2 = "ask the default pid-tree check".
ob_engine_owns_port() {
  local e="${INFERENCE_BACKEND:-llama}"
  ob_engine_has "$e" owns_port || return 2
  ob_engine_call "$e" owns_port "$@"
}

# ob_engine_stop_managed — stop the managed non-llama engine of THIS
# checkout (stop.sh). Never touches an unmanaged one.
ob_engine_stop_managed() {
  ob_engine_managed_here || return 0
  ob_engine_call "$INFERENCE_BACKEND" stop || true
}

# ob_engine_doctor_rows — doctor.sh's "Inference engine" rows, through its
# pass / warn / info functions. Read-only, no network.
ob_engine_doctor_rows() {
  local cls def act e v p
  cls="$(ob_engine_host_class)"; def="$(ob_engine_default "$cls")"
  act="${INFERENCE_BACKEND:-llama}"
  info "host class $cls ($(ob_engine_arch)) — default engine here: $def; active: $act$([[ "${INFERENCE_MANAGED:-true}" == true ]] && echo ' (managed)' || echo ' (not managed here)')"
  for e in $(ob_engines); do
    v="$(ob_engine_call "$e" installed 2>/dev/null || true)"
    p="$(ob_engine_pin "$e" 2>/dev/null || true)"
    if [[ "$e" == "$act" && "${INFERENCE_MANAGED:-true}" == true && -z "$v" ]]; then
      warn "$e is the active, managed engine but is not installed here" "./scripts/engine.sh install $e"
    elif [[ -n "$v" && -n "$p" && "${p%%@*}" != "${v%% *}" ]]; then
      warn "$e: installed $v, pinned ${p%%@*}" "./scripts/engine.sh install $e --version ${p%%@*}  (or: engine.sh rollback $e)"
    elif [[ -n "$v" ]]; then
      pass "$e $v${p:+ (pin ${p%%@*})}"
    fi
  done
}
