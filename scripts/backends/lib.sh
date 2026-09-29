#!/bin/bash
# Shared helpers for the DGX Spark launch scaffolds (scripts/backends/*/
# spark-node.sh). Sourced, never executed. These run ON A SPARK, not on the
# rig, and deliberately depend on nothing else in the repo: a Spark may hold
# only a copy of scripts/backends/.
#
# Settings resolve: command-line flag > environment variable > spark.env >
# default. spark.env is PARSED, never sourced — it holds an API-key path and
# network settings, and executing it would make every line code.

# _sp_env_value <file> <KEY> — the last KEY=... in <file>, unquoted.
_sp_env_value() {
  local file="$1" key="$2" line v
  [[ -f "$file" ]] || return 1
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" 2>/dev/null | tail -n1 || true)"
  [[ -n "$line" ]] || return 1
  v="${line#*=}"
  v="${v#"${v%%[![:space:]]*}"}"
  if [[ "$v" == \"*\"* ]]; then
    v="${v#\"}"; v="${v%%\"*}"
  elif [[ "$v" == \'*\'* ]]; then
    v="${v#\'}"; v="${v%%\'*}"
  else
    v="${v%%[[:space:]]#*}"        # `value   # comment`
    v="${v%"${v##*[![:space:]]}"}"
  fi
  printf '%s\n' "$v"
}

# sp_load <env-file> <KEY>... — set each KEY from the environment, else the
# file, leaving it empty when neither has it.
sp_load() {
  local file="$1" key v; shift
  for key in "$@"; do
    if [[ -n "${!key:-}" ]]; then
      continue
    fi
    v="$(_sp_env_value "$file" "$key" || true)"
    # shellcheck disable=SC2088  # matching a literal ~
    case "$v" in "~") v="$HOME" ;; "~/"*) v="$HOME/${v#\~/}" ;; esac
    printf -v "$key" '%s' "$v"
  done
}

SP_ERRORS=0
sp_need() {   # sp_need <VAR> <why> — record a missing required setting
  if [[ -z "${!1:-}" ]]; then
    echo "Error: $1 is not set — $2" >&2
    SP_ERRORS=$((SP_ERRORS + 1))
  fi
}
sp_err() { echo "Error: $*" >&2; SP_ERRORS=$((SP_ERRORS + 1)); }

sp_is_true() { case "$(printf '%s' "${1:-}" | tr 'A-Z' 'a-z')" in true|yes|1|on) return 0 ;; esac; return 1; }

# sp_image <image> <digest> <print-mode 0|1> — sets SP_IMAGE_REF, the
# reference to run (a global, not stdout: sp_err must count in THIS shell).
# A real run requires a sha256 digest; --print shows the tag with a warning.
sp_image() {
  local image="$1" digest="$2" printing="$3"
  SP_IMAGE_REF="$image"
  if [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    SP_IMAGE_REF="$image@$digest"
  elif [[ "$printing" -eq 1 ]]; then
    echo "Warning: no image digest pinned (placeholder '$digest') — showing the tag only; a real run refuses this." >&2
  else
    sp_err "image digest is not pinned ('$digest'): docker pull $image, then paste the sha256 from docker inspect --format '{{index .RepoDigests 0}}' $image"
  fi
}

# sp_check_bind <host> — a specific address, never a wildcard unless the
# operator acknowledged it (vLLM /health + /metrics are open even with a key;
# TensorFold has no key at all).
sp_check_bind() {
  case "$1" in
    ""|0.0.0.0|::|"[::]")
      if ! sp_is_true "${SPARK_ALLOW_WILDCARD_BIND:-}"; then
        sp_err "SPARK_SERVE_HOST='$1' — set it to this Spark's tailnet or private-LAN address (a wildcard needs SPARK_ALLOW_WILDCARD_BIND=true and your own firewall)"
      fi ;;
  esac
}

# sp_check_secret_file <path> — exists, a regular file, owned by us, and not
# readable by group/other.
sp_check_secret_file() {
  local f="$1" mode owner
  if [[ ! -f "$f" ]]; then
    sp_err "API key file '$f' does not exist (create it: (umask 077; openssl rand -hex 32 > '$f'))"
    return 0
  fi
  mode="$(stat -c '%a' "$f" 2>/dev/null || stat -f '%Lp' "$f" 2>/dev/null || echo "")"
  owner="$(stat -c '%u' "$f" 2>/dev/null || stat -f '%u' "$f" 2>/dev/null || echo "")"
  if [[ -z "$mode" || "${mode: -2}" != "00" ]]; then
    sp_err "API key file '$f' is mode ${mode:-?} — chmod 600 it"
  fi
  if [[ -n "$owner" && "$owner" != "$(id -u)" ]]; then
    sp_err "API key file '$f' is not owned by $(id -un)"
  fi
  if [[ ! -s "$f" ]]; then
    sp_err "API key file '$f' is empty"
  fi
}

# sp_print_cmd <argv...> — the exact command, shell-quoted, one line.
sp_print_cmd() {
  local out="" a
  for a in "$@"; do out+="$(printf '%q' "$a") "; done
  printf '%s\n' "${out% }"
}
