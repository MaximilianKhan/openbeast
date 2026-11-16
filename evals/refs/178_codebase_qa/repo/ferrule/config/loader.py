"""Layered settings loader.

Precedence, lowest to highest:

1. ``DEFAULTS`` (ferrule/config/defaults.py)
2. ``conf/base.ini``
3. ``conf/profiles/<profile>.ini``
4. environment variables (``ferrule.config.envmap``)
5. explicit overrides passed by the caller (CLI ``--set key=value``)

The profile is chosen after the base file is applied, so ``base.ini`` may
set it; ``FERRULE_PROFILE`` or an explicit ``profile=`` argument win over it.
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from ferrule.config import envmap, ini
from ferrule.config.defaults import DEFAULTS
from ferrule.config.schema import coerce
from ferrule.errors import ConfigError

log = logging.getLogger(__name__)

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent


def default_conf_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    override = env.get("FERRULE_CONF_DIR")
    if override:
        return Path(override)
    return _PACKAGE_ROOT / "conf"


class Settings(dict):
    """A plain dict with dotted-path helpers.

    ``settings["storage"]["wal"]`` and ``settings.path("storage.wal")`` are
    the same object; there is no copy-on-read.
    """

    def path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def profile(self) -> str:
        return self["profile"]


def _get_parent(tree: Dict[str, Any], path: Tuple[str, ...]) -> Dict[str, Any]:
    node = tree
    for part in path[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    return node


def _as_items(value: Any) -> List[str]:
    if isinstance(value, list):
        return list(value)
    return ini.split_list(str(value))


def apply_op(tree: Dict[str, Any], op: ini.Op) -> None:
    parent = _get_parent(tree, op.path)
    key = op.path[-1]
    current = parent.get(key)
    if op.kind == ini.SET:
        if isinstance(current, list):
            parent[key] = ini.split_list(op.value)
        elif isinstance(current, dict):
            raise ConfigError(f"{op.source}: {op.dotted} is a table, not a value")
        else:
            parent[key] = op.value
    elif op.kind == ini.APPEND:
        items = _as_items(current) if current is not None else []
        for item in ini.split_list(op.value):
            if item not in items:
                items.append(item)
        parent[key] = items
    elif op.kind == ini.REMOVE:
        items = _as_items(current) if current is not None else []
        drop = set(ini.split_list(op.value))
        parent[key] = [item for item in items if item not in drop]
    else:  # pragma: no cover - ini.read_ops only produces the three kinds
        raise ConfigError(f"unknown operation {op.kind!r}")


def apply_ops(tree: Dict[str, Any], ops: Iterable[ini.Op]) -> None:
    for op in ops:
        apply_op(tree, op)


def _leaves(tree: Dict[str, Any], prefix: Tuple[str, ...] = ()) -> Iterator[Tuple[str, ...]]:
    for key, value in tree.items():
        here = prefix + (key,)
        if isinstance(value, dict):
            yield from _leaves(value, here)
        else:
            yield here


def _set_path(tree: Dict[str, Any], dotted: str, value: Any) -> None:
    path = tuple(p for p in dotted.split(".") if p)
    if not path:
        raise ConfigError("empty override key")
    _get_parent(tree, path)[path[-1]] = value


def apply_env(tree: Dict[str, Any], env: Mapping[str, str]) -> List[str]:
    """Apply environment overrides for keys already present in ``tree``.

    Returns the list of variable names that were used, for ``ferrule config
    show --sources``.
    """
    used = []
    for path in list(_leaves(tree)):
        name = envmap.env_name(".".join(path))
        if name in env:
            _get_parent(tree, path)[path[-1]] = env[name]
            used.append(name)
    for dotted, value in envmap.legacy_overrides(dict(env)):
        _set_path(tree, dotted, value)
        used.append(f"legacy:{dotted}")
    return used


def load_settings(conf_dir: Optional[os.PathLike] = None,
                  env: Optional[Mapping[str, str]] = None,
                  overrides: Optional[Mapping[str, Any]] = None,
                  profile: Optional[str] = None) -> Settings:
    env = os.environ if env is None else env
    conf = Path(conf_dir) if conf_dir is not None else default_conf_dir(env)

    tree: Dict[str, Any] = copy.deepcopy(DEFAULTS)
    apply_ops(tree, ini.read_ops(conf / "base.ini"))

    chosen = profile or env.get(envmap.env_name("profile")) or tree.get("profile")
    profile_file = conf / "profiles" / f"{chosen}.ini"
    if profile_file.is_file():
        apply_ops(tree, ini.read_ops(profile_file))
    else:
        log.warning("profile %r has no file at %s", chosen, profile_file)
    tree["profile"] = chosen

    apply_env(tree, env)
    for dotted, value in (overrides or {}).items():
        _set_path(tree, dotted, value)

    return Settings(coerce(tree))
