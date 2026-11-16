"""Schema bookkeeping table."""

ID = "0001_init"
DEPENDS = []


def up(db):
    db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
