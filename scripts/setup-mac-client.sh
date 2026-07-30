#!/usr/bin/env bash
# Back-compat wrapper — client setup generalized beyond the Mac (macOS +
# Linux) and renamed. All flags pass through. See docs/BEAST_SLOT.md.
exec "$(cd "$(dirname "$0")" && pwd)/setup-client.sh" "$@"
