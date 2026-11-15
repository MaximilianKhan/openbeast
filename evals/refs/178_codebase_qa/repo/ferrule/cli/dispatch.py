"""Two-level command dispatch: ``ferrule <group> <command> [args...]``.

Both levels resolve a token the same way: an exact name, then an exact
alias, then a unique prefix of a *name* (aliases are never prefix-matched).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ferrule.errors import AmbiguousCommand, UnknownCommand

Func = Callable[..., int]


@dataclass
class Command:
    name: str
    func: Func
    aliases: Tuple[str, ...] = ()
    help: str = ""
    args: List[Tuple[Sequence[str], dict]] = field(default_factory=list)


@dataclass
class Group:
    name: str
    aliases: Tuple[str, ...] = ()
    help: str = ""
    commands: Dict[str, Command] = field(default_factory=dict)
    default: Optional[str] = None

    def command(self, name: str, aliases: Sequence[str] = (), help: str = ""):
        def deco(fn: Func) -> Func:
            cmd = Command(name, fn, tuple(aliases), help or (fn.__doc__ or "").strip().split("\n")[0])
            cmd.args = list(getattr(fn, "__cli_args__", []))
            self.commands[name] = cmd
            return fn
        return deco


def arg(*flags: str, **kwargs):
    """Declare an argparse argument for a command function."""
    def deco(fn):
        args = getattr(fn, "__cli_args__", [])
        args.insert(0, (flags, kwargs))
        fn.__cli_args__ = args
        return fn
    return deco


def resolve(table: Dict[str, object], token: str, what: str):
    if token in table:
        return table[token]
    for obj in table.values():
        if token in getattr(obj, "aliases", ()):
            return obj
    matches = sorted(name for name in table if name.startswith(token))
    if len(matches) == 1:
        return table[matches[0]]
    if matches:
        raise AmbiguousCommand(f"{what} {token!r} is ambiguous: {', '.join(matches)}")
    raise UnknownCommand(f"unknown {what} {token!r}")


class Registry:
    def __init__(self) -> None:
        self.groups: Dict[str, Group] = {}

    def add(self, group: Group) -> Group:
        self.groups[group.name] = group
        return group

    def lookup(self, argv: Sequence[str]) -> Tuple[Group, Command, List[str]]:
        if not argv:
            raise UnknownCommand("no command given")
        group = resolve(self.groups, argv[0], "group")
        rest = list(argv[1:])
        if not rest or rest[0].startswith("-"):
            if group.default is None:
                raise UnknownCommand(f"{group.name}: no command given")
            return group, group.commands[group.default], rest
        cmd = resolve(group.commands, rest[0], f"{group.name} command")
        return group, cmd, rest[1:]
