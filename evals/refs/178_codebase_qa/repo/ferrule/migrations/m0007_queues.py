"""Queues, per tenant. Runs early: the importer needs it."""

ID = "0007_queues"
DEPENDS = ["0002_tenants"]
PRIORITY = -1


def up(db):
    db.execute("CREATE TABLE queues (tenant TEXT NOT NULL, name TEXT NOT NULL, paused INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (tenant, name))")
