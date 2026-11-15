"""Plan and apply migrations.

Squashes: a migration with ``REPLACES`` stands in for the migrations it
lists. On a database where none of the replaced migrations has been
applied, the squash is used: the replaced migrations are dropped from the
plan and any dependency on them is rewritten to the squash. On a database
where at least one of them has been applied, the squash is dropped instead
and the originals are planned as usual.
"""

from __future__ import annotations

import heapq
import importlib
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set

from ferrule.errors import MigrationError
from ferrule.migrations import MIGRATIONS


@dataclass
class Migration:
    id: str
    module: str
    depends: List[str]
    priority: int = 0
    replaces: List[str] = field(default_factory=list)
    up: Optional[Callable] = None


def discover(names: Iterable[str] = MIGRATIONS) -> List[Migration]:
    out = []
    for name in names:
        mod = importlib.import_module(f"ferrule.migrations.{name}")
        out.append(Migration(
            id=mod.ID,
            module=name,
            depends=list(getattr(mod, "DEPENDS", [])),
            priority=int(getattr(mod, "PRIORITY", 0)),
            replaces=list(getattr(mod, "REPLACES", [])),
            up=mod.up,
        ))
    return out


def resolve_squashes(migs: List[Migration], applied: Set[str]) -> List[Migration]:
    rename: Dict[str, str] = {}
    drop: Set[str] = set()
    for m in migs:
        if not m.replaces:
            continue
        if any(r in applied for r in m.replaces):
            drop.add(m.id)
        else:
            drop.update(m.replaces)
            for r in m.replaces:
                rename[r] = m.id
    out = []
    for m in migs:
        if m.id in drop:
            continue
        deps = []
        for d in m.depends:
            d = rename.get(d, d)
            if d not in deps and d != m.id:
                deps.append(d)
        out.append(Migration(m.id, m.module, deps, m.priority, m.replaces, m.up))
    missing = {d for m in out for d in m.depends} - {m.id for m in out} - applied - set(rename)
    if missing:
        raise MigrationError(f"unknown dependencies: {sorted(missing)}")
    return out


def plan(migs: List[Migration], applied: Set[str]) -> List[str]:
    pending = {m.id: m for m in resolve_squashes(migs, applied) if m.id not in applied}
    indegree = {mid: sum(1 for d in m.depends if d in pending) for mid, m in pending.items()}
    dependents: Dict[str, List[str]] = {mid: [] for mid in pending}
    for mid, m in pending.items():
        for d in m.depends:
            if d in pending:
                dependents[d].append(mid)
    ready = [(pending[mid].priority, mid) for mid, n in indegree.items() if n == 0]
    heapq.heapify(ready)
    order: List[str] = []
    while ready:
        _, mid = heapq.heappop(ready)
        order.append(mid)
        for nxt in dependents[mid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, (pending[nxt].priority, nxt))
    if len(order) != len(pending):
        raise MigrationError("dependency cycle among: " + ", ".join(sorted(set(pending) - set(order))))
    return order


class MigrationRunner:
    TABLE = "ferrule_migrations"

    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path)
        self.db.execute(f"CREATE TABLE IF NOT EXISTS {self.TABLE} (id TEXT PRIMARY KEY, at REAL NOT NULL)")
        self.migrations = discover()

    def applied(self) -> Set[str]:
        return {row[0] for row in self.db.execute(f"SELECT id FROM {self.TABLE}")}

    def plan(self) -> List[str]:
        return plan(self.migrations, self.applied())

    def apply(self, ids: List[str]) -> None:
        by_id = {m.id: m for m in self.migrations}
        for mid in ids:
            with self.db:
                by_id[mid].up(self.db)
                self.db.execute(f"INSERT INTO {self.TABLE}(id, at) VALUES (?, ?)", (mid, time.time()))
