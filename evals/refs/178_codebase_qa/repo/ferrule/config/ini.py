"""INI reader for ferrule configuration files.

Files are plain INI. The section name is the dotted path of the table the
keys belong to; the special section ``[ferrule]`` is the root of the tree::

    [ferrule]
    profile = prod

    [storage.wal]
    fsync_interval_ms = 100

A key may carry an operator suffix:

* ``key = a, b``   replace the value (lists are written comma separated)
* ``key+ = c``     append items to the list at ``key``
* ``key- = a``     remove items from the list at ``key``

The reader does not interpret values; every value is returned as the raw
string and typed later by ``ferrule.config.schema``.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

ROOT_SECTION = "ferrule"

SET = "set"
APPEND = "append"
REMOVE = "remove"


@dataclass(frozen=True)
class Op:
    path: Tuple[str, ...]
    kind: str
    value: str
    source: str

    @property
    def dotted(self) -> str:
        return ".".join(self.path)


def _split_key(raw: str) -> Tuple[str, str]:
    raw = raw.strip()
    if raw.endswith("+"):
        return raw[:-1].strip(), APPEND
    if raw.endswith("-"):
        return raw[:-1].strip(), REMOVE
    return raw, SET


def _section_path(section: str) -> Tuple[str, ...]:
    if section == ROOT_SECTION:
        return ()
    return tuple(part.strip() for part in section.split(".") if part.strip())


def read_ops(path: Path) -> List[Op]:
    """Parse one INI file into an ordered list of operations.

    A missing file is not an error: it simply contributes no operations.
    """
    path = Path(path)
    if not path.is_file():
        return []
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    parser.optionxform = str  # keys are case sensitive
    with path.open(encoding="utf-8") as fh:
        parser.read_file(fh, source=str(path))
    ops: List[Op] = []
    for section in parser.sections():
        base = _section_path(section)
        for raw_key, value in parser.items(section, raw=True):
            key, kind = _split_key(raw_key)
            ops.append(Op(base + (key,), kind, value.strip(), str(path)))
    return ops


def split_list(value: str) -> List[str]:
    """``"a, b,,c"`` -> ``["a", "b", "c"]``."""
    return [item.strip() for item in value.split(",") if item.strip()]
