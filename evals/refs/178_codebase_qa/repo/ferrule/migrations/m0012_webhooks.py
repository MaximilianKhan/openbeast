"""Webhook subscriptions per queue."""

ID = "0012_hooks"
DEPENDS = ["0007_queues"]


def up(db):
    db.execute("CREATE TABLE webhooks (tenant TEXT NOT NULL, queue TEXT NOT NULL, url TEXT NOT NULL)")
