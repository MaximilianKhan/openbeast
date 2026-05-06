#!/bin/bash
# install-skills.sh — set up the global skills directory and (optionally)
# symlink starter skills from this repo so they're available across all
# projects on this machine.
#
# Skill resolution order in agents/mcp_server.py:
#   1. <repo>/skills/<name>/SKILL.md          (repo skills — version controlled, project-local)
#   2. ~/.local/share/local-llm-skills/<name>/SKILL.md   (global skills — shared across projects)
# Repo wins on name collision.
#
# Usage:
#   ./scripts/install-skills.sh                    # create the dir, list what's available
#   ./scripts/install-skills.sh --link <skill>     # symlink one repo skill to global
#   ./scripts/install-skills.sh --link-all         # symlink every repo skill to global

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_SKILLS="$REPO_DIR/skills"
GLOBAL_SKILLS="$HOME/.local/share/local-llm-skills"

mkdir -p "$GLOBAL_SKILLS"
echo "Global skills directory: $GLOBAL_SKILLS"

case "${1:-}" in
  --link)
    NAME="${2:?--link requires a skill name}"
    SRC="$REPO_SKILLS/$NAME"
    DST="$GLOBAL_SKILLS/$NAME"
    if [[ ! -d "$SRC" ]]; then
      echo "Error: $SRC does not exist (not a known repo skill)" >&2
      exit 1
    fi
    if [[ -e "$DST" ]]; then
      echo "Error: $DST already exists; remove it first if you want to relink" >&2
      exit 1
    fi
    ln -s "$SRC" "$DST"
    echo "Linked: $DST -> $SRC"
    ;;

  --link-all)
    if [[ ! -d "$REPO_SKILLS" ]]; then
      echo "No repo skills found at $REPO_SKILLS"
      exit 0
    fi
    n=0
    for src in "$REPO_SKILLS"/*/; do
      [[ -d "$src" ]] || continue
      name="$(basename "$src")"
      dst="$GLOBAL_SKILLS/$name"
      if [[ -e "$dst" ]]; then
        echo "  skip: $name (already exists at $dst)"
        continue
      fi
      ln -s "$src" "$dst"
      echo "  link: $dst -> $src"
      n=$((n + 1))
    done
    echo "Linked $n new skill(s)."
    ;;

  --help|-h)
    grep '^#' "$0" | sed 's/^# *//'
    ;;

  "")
    # Inventory pass — show what's where
    echo ""
    echo "Repo skills ($REPO_SKILLS):"
    if [[ -d "$REPO_SKILLS" ]]; then
      for d in "$REPO_SKILLS"/*/; do
        [[ -d "$d" ]] || continue
        name="$(basename "$d")"
        if [[ -f "$d/SKILL.md" ]]; then
          desc="$(grep -m1 '^description:' "$d/SKILL.md" | sed 's/^description:[[:space:]]*//')"
          echo "  $name  — $desc"
        else
          echo "  $name  (no SKILL.md — broken)"
        fi
      done
    else
      echo "  (none)"
    fi
    echo ""
    echo "Global skills ($GLOBAL_SKILLS):"
    if [[ -d "$GLOBAL_SKILLS" ]]; then
      for d in "$GLOBAL_SKILLS"/*/; do
        [[ -d "$d" || -L "$d" ]] || continue
        name="$(basename "$d")"
        if [[ -L "$d" ]]; then
          target="$(readlink "$d")"
          echo "  $name  -> $target"
        else
          echo "  $name"
        fi
      done
    else
      echo "  (none)"
    fi
    echo ""
    echo "To link a starter skill globally:  ./scripts/install-skills.sh --link <name>"
    echo "To link every repo skill globally: ./scripts/install-skills.sh --link-all"
    ;;

  *)
    echo "Unknown option: $1" >&2
    echo "Try --help" >&2
    exit 1
    ;;
esac
