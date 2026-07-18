#!/bin/bash
# OpenBeast extension system (ODS-absorbed, docs/TODO.md).
#
# An extension is an OPTIONAL service that attaches to the stack without editing
# core files — the sanctioned way to add anything beyond the opinionated core,
# so the core stays lean. Each lives in extensions/<name>/ with:
#
#   manifest          KEY=value metadata (required): NAME, DESCRIPTION, KIND
#                     KIND is 'compose' (a docker-compose fragment) or
#                     'process' (a script start.sh runs in the background).
#   compose.yaml      KIND=compose: a compose fragment merged via `-f`.
#   run.sh            KIND=process: launched in the background by start.sh;
#                     must exec its server in the foreground (start.sh reaps it).
#
# Enabled extensions are listed in openbeast.conf `EXTENSIONS="a b"` (resolved
# by lib/conf.sh, space-separated). scripts/ext.sh is the enable/disable CLI.
#
# Requires REPO_DIR set. Sourced by start.sh, stop.sh, and scripts/ext.sh.
: "${REPO_DIR:?REPO_DIR must be set before sourcing lib/extensions.sh}"

_OB_EXT_DIR="$REPO_DIR/extensions"

# One metadata value from an extension manifest. $1=name $2=key.
ob_ext_meta() {
  local f="$_OB_EXT_DIR/$1/manifest" v
  [[ -f "$f" ]] || return 1
  v="$(grep -E "^[[:space:]]*$2[[:space:]]*=" "$f" | tail -1)" || return 1
  v="${v#*=}"; v="${v#"${v%%[![:space:]]*}"}"; v="${v%\"}"; v="${v#\"}"
  printf '%s\n' "$v"
}

# All extensions present on disk, one name per line (sorted).
ob_ext_available() {
  [[ -d "$_OB_EXT_DIR" ]] || return 0
  find "$_OB_EXT_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort
}

# Enabled extensions that actually exist on disk, one name per line. Reads the
# EXTENSIONS conf value (space-separated); silently skips names with no dir.
ob_ext_enabled() {
  local name
  for name in ${EXTENSIONS:-}; do
    [[ -d "$_OB_EXT_DIR/$name" ]] && printf '%s\n' "$name"
  done
}

ob_ext_is_enabled() { # $1=name -> 0 if enabled
  local n; for n in $(ob_ext_enabled); do [[ "$n" == "$1" ]] && return 0; done; return 1
}

# `-f <path>` args for every enabled compose-kind extension (for `docker
# compose`). Emits nothing if none — callers prepend the core -f themselves.
ob_ext_compose_args() {
  local name
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    if [[ "$(ob_ext_meta "$name" KIND)" == "compose" && -f "$_OB_EXT_DIR/$name/compose.yaml" ]]; then
      printf -- '-f\n%s\n' "$_OB_EXT_DIR/$name/compose.yaml"
    fi
  done < <(ob_ext_enabled)
}

# Enabled process-kind extension names, one per line.
ob_ext_processes() {
  local name
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    [[ "$(ob_ext_meta "$name" KIND)" == "process" && -x "$_OB_EXT_DIR/$name/run.sh" ]] && printf '%s\n' "$name"
  done < <(ob_ext_enabled)
}
