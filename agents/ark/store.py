"""beast-ark drive layout and the drive lock (`ark.lock`). docs/BEAST_ARK.md §5.

    DIR/
      ark                 shell entry point that runs the tool FROM the drive
                          (finds a python, or unpacks the archived CPython)
      ARK.md              what is on this drive and how to restore it, offline
      ark.lock            every stored file: path, sha256, size, source, how verified
      SHA256SUMS          the same hashes in coreutils format (`sha256sum -c`
                          works on a box with no python at all)
      catalog.toml        the catalog this drive was filled from
      tool/               a copy of agents/ark + scripts/ark.sh (self-hosting)
      git/<entry>/        <entry>-<tag>.bundle (full history) or
                          <entry>-<tag>.shallow.git.tar (the tag's commit only)
      files/<entry>/<version>/<platform>/   release files + their sums/signatures
      oci/<entry>/        <image>_<tag>.oci.tar
      pkgs/<kind>/<entry>-<version>/<platform>/   pip wheels, npm tarballs,
                          go module proxy tree, cargo vendor tarball
      .ark-stage/         partial downloads and pull state (resume); never served

The lock is rewritten atomically after EVERY item, so an interrupted fetch
keeps everything it finished, and the next run skips it.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
from typing import Dict, List, Optional

from . import catalog as C

LOCK = "ark.lock"
SUMS = "SHA256SUMS"
STAGE = ".ark-stage"
TOOL = "tool"


class StoreError(RuntimeError):
    pass


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "x"


def safe_rel(rel: str) -> str:
    """A lock path must stay inside the drive."""
    parts = rel.replace("\\", "/").split("/")
    if rel.startswith("/") or any(p in ("", "..") for p in parts):
        raise StoreError(f"unsafe path in lock: {rel!r}")
    return rel


class Drive:
    def __init__(self, root: str):
        self.root = os.path.abspath(os.path.expanduser(root))

    def p(self, rel: str) -> str:
        return os.path.join(self.root, safe_rel(rel))

    @property
    def lock_path(self) -> str:
        return os.path.join(self.root, LOCK)

    def stage(self, *parts: str) -> str:
        d = os.path.join(self.root, STAGE, *parts)
        os.makedirs(d, exist_ok=True)
        return d

    # -- lock ----------------------------------------------------------------
    def read_lock(self) -> dict:
        try:
            with open(self.lock_path, encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            return {"schema": 1, "tool": "beast-ark", "created": now(), "items": {}}
        except ValueError as e:
            raise StoreError(f"{self.lock_path} is not valid JSON ({e}); refusing to overwrite it") from None
        if not isinstance(d, dict) or d.get("schema") != 1 or not isinstance(d.get("items"), dict):
            raise StoreError(f"{self.lock_path} is not a beast-ark lock (schema 1)")
        return d

    def write_lock(self, lock: dict) -> None:
        lock["updated"] = now()
        tmp = self.lock_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lock, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, self.lock_path)
        self.write_sums(lock)

    def write_sums(self, lock: dict) -> None:
        rows = sorted({(f["path"], f["sha256"]) for it in lock["items"].values() for f in it.get("files", [])})
        tmp = os.path.join(self.root, SUMS + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for path, h in rows:
                f.write(f"{h}  {path}\n")
        os.replace(tmp, os.path.join(self.root, SUMS))


def item_bytes(item: dict) -> int:
    return sum(f.get("size", 0) for f in item.get("files", []))


# ── self-hosting: the tool rides on the drive ───────────────────────────

WRAPPER = r'''#!/bin/sh
# beast-ark, running from this drive. docs/BEAST_ARK.md §7 (bare box).
#   ./ark verify .          ./ark restore <name> --from .        ./ark serve .
# Needs python >= 3.9. None on this box? This script unpacks the CPython that
# is archived on the drive (sha256-checked against SHA256SUMS first).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
pick() {
  for c in "${OPENBEAST_PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3.10 python3 \
           "$HOME/.local/opt/ark-python/python/bin/python3"; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}
PY="$(pick || true)"
if [ -z "$PY" ]; then
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) plat=linux-x86_64 ;; Linux-aarch64|Linux-arm64) plat=linux-aarch64 ;;
    Darwin-arm64) plat=darwin-arm64 ;; *) echo "no python and no archived CPython for $(uname -sm)" >&2; exit 2 ;;
  esac
  tgz="$(cd "$HERE" && ls files/cpython*/*/"$plat"/cpython-*-install_only.tar.gz 2>/dev/null | sort | tail -1 || true)"
  [ -n "$tgz" ] || { echo "no python >= 3.9 here and no archived CPython for $plat on this drive" >&2; exit 2; }
  want="$(grep "  $tgz\$" "$HERE/SHA256SUMS" | awk '{print $1}')"
  if command -v sha256sum >/dev/null 2>&1; then got="$(sha256sum "$HERE/$tgz" | awk '{print $1}')"
  else got="$(shasum -a 256 "$HERE/$tgz" | awk '{print $1}')"; fi
  [ -n "$want" ] && [ "$got" = "$want" ] || { echo "REFUSED: $tgz does not match SHA256SUMS" >&2; exit 1; }
  mkdir -p "$HOME/.local/opt/ark-python"
  tar -xzf "$HERE/$tgz" -C "$HOME/.local/opt/ark-python"
  PY="$HOME/.local/opt/ark-python/python/bin/python3"
  echo "using the archived CPython: $PY" >&2
fi
exec "$PY" -I "$HERE/tool/agents/ark/cli.py" "$@"
'''


def install_tool(drive: Drive) -> None:
    """Copy the tool and catalog onto the drive so it restores itself."""
    src = C.HERE
    dst = os.path.join(drive.root, TOOL, "agents", "ark")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    docs_cfg = os.path.join(C.REPO_DIR, "agents", "docs")
    if os.path.isdir(docs_cfg):              # offline_reason(); optional on the drive
        d2 = os.path.join(drive.root, TOOL, "agents", "docs")
        os.makedirs(d2, exist_ok=True)
        for f in ("__init__.py", "config.py"):
            if os.path.isfile(os.path.join(docs_cfg, f)):
                shutil.copy2(os.path.join(docs_cfg, f), os.path.join(d2, f))
    w = os.path.join(drive.root, "ark")
    with open(w, "w", encoding="utf-8") as f:
        f.write(WRAPPER)
    os.chmod(w, 0o755)


def write_readme(drive: Drive, cat: C.Catalog, lock: dict) -> None:
    items = lock["items"]
    by_entry: Dict[str, List[dict]] = {}
    for it in items.values():
        by_entry.setdefault(it["entry"], []).append(it)
    lines = [
        "# beast-ark drive",
        "",
        f"Filled {lock.get('updated', now())} from catalog schema {cat.meta.get('schema')} "
        f"({len(by_entry)} entries, {len(items)} items, {sum(item_bytes(i) for i in items.values()) / 2**30:.1f} GiB).",
        "Full guide: docs/BEAST_ARK.md in the OpenBeast repo (also in tool/ if you copied the repo).",
        "",
        "## On a box with no network",
        "",
        "```sh",
        "./ark verify .                         # re-hash every file against ark.lock",
        "sha256sum -c SHA256SUMS                # the same, with no python (macOS: shasum -a 256 -c)",
        "./ark list --from .                    # what is here",
        "./ark restore go --from .              # unpack a toolchain into ~/.local/opt, link ~/.local/bin",
        "./ark restore postgres --from . --docker          # load images into docker",
        "./ark serve . --tailnet                # registry + files + pip/npm/go mirrors for the tailnet",
        "```",
        "",
        "## Contents",
        "",
        "| entry | category | version | items | GiB |",
        "|---|---|---|---|---|",
    ]
    for name in sorted(by_entry):
        its = by_entry[name]
        e = cat.entries.get(name)
        lines.append(f"| {name} | {e.category if e else '?'} | {its[0].get('version', '')} | {len(its)} | "
                     f"{sum(item_bytes(i) for i in its) / 2**30:.2f} |")
    with open(os.path.join(drive.root, "ARK.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def find_item(lock: dict, key: str) -> Optional[dict]:
    return lock["items"].get(key)
