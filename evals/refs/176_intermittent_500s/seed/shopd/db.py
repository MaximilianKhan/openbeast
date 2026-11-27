"""In-process table store.

Production runs this behind the same interface against its shared store;
the semantics the service relies on are only these:

* rows are dicts keyed by an integer primary key ``id`` that a table assigns
  in increasing order, starting at 1;
* ``unique`` columns reject duplicates with IntegrityError;
* ``Database.transaction()`` is all-or-nothing for every table touched
  inside it (it snapshots on entry and restores on exception).

Reads hand out copies, so callers can never mutate stored rows by accident.
"""
from __future__ import annotations

import contextlib
import copy
import threading


class IntegrityError(Exception):
    def __init__(self, table: str, column: str, value):
        super().__init__("%s.%s must be unique (got %r)" % (table, column, value))
        self.table = table
        self.column = column
        self.value = value


class Table:
    def __init__(self, name: str, unique: tuple[str, ...] = ()):
        self.name = name
        self.unique = unique
        self.rows: dict[int, dict] = {}
        self.next_id = 1
        self._uniq: dict[str, dict] = {c: {} for c in unique}

    # -- reads -------------------------------------------------------------
    def get(self, row_id: int) -> dict:
        """The row with this id. Raises KeyError when there is none."""
        return dict(self.rows[row_id])

    def find(self, row_id) -> dict | None:
        row = self.rows.get(row_id)
        return None if row is None else dict(row)

    def find_by(self, column: str, value) -> dict | None:
        if column in self._uniq:
            rid = self._uniq[column].get(value)
            return None if rid is None else dict(self.rows[rid])
        for row in self.rows.values():
            if row.get(column) == value:
                return dict(row)
        return None

    def scan(self, predicate=None) -> list[dict]:
        """All rows (copies) in primary-key order, optionally filtered."""
        out = []
        for rid in sorted(self.rows):
            row = self.rows[rid]
            if predicate is None or predicate(row):
                out.append(dict(row))
        return out

    def __len__(self) -> int:
        return len(self.rows)

    # -- writes ------------------------------------------------------------
    def insert(self, values: dict) -> dict:
        row = dict(values)
        for col in self.unique:
            if row.get(col) in self._uniq[col]:
                raise IntegrityError(self.name, col, row.get(col))
        row["id"] = self.next_id
        self.next_id += 1
        self.rows[row["id"]] = row
        for col in self.unique:
            self._uniq[col][row[col]] = row["id"]
        return dict(row)

    def update(self, row_id: int, changes: dict) -> dict:
        row = self.rows[row_id]
        for col in self.unique:
            if col in changes and changes[col] != row[col]:
                if changes[col] in self._uniq[col]:
                    raise IntegrityError(self.name, col, changes[col])
        for col in self.unique:
            if col in changes and changes[col] != row[col]:
                del self._uniq[col][row[col]]
                self._uniq[col][changes[col]] = row_id
        row.update(changes)
        row["id"] = row_id
        return dict(row)

    def delete(self, row_id: int) -> None:
        row = self.rows.pop(row_id)
        for col in self.unique:
            self._uniq[col].pop(row[col], None)

    # -- snapshots ---------------------------------------------------------
    def _snapshot(self):
        return copy.deepcopy((self.rows, self.next_id, self._uniq))

    def _restore(self, snap) -> None:
        self.rows, self.next_id, self._uniq = snap


class Database:
    def __init__(self):
        self.products = Table("products", unique=("sku",))
        self.customers = Table("customers", unique=("email",))
        self.orders = Table("orders")
        self.coupons = Table("coupons", unique=("code",))
        self.lock = threading.RLock()

    def tables(self) -> list[Table]:
        return [self.products, self.customers, self.orders, self.coupons]

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            snaps = [(t, t._snapshot()) for t in self.tables()]
            try:
                yield self
            except BaseException:
                for t, s in snaps:
                    t._restore(s)
                raise
