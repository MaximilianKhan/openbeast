"""Tenants."""

ID = "0002_tenants"
DEPENDS = ["0001_init"]


def up(db):
    db.execute("CREATE TABLE tenants (name TEXT PRIMARY KEY, created_at REAL NOT NULL, tier TEXT NOT NULL DEFAULT 'standard')")
