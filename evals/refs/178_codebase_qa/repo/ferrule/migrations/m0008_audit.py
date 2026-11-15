"""Audit trail. Large table; scheduled late."""

ID = "0008_audit"
DEPENDS = ["0001_init"]
PRIORITY = 5


def up(db):
    db.execute("CREATE TABLE audit (at REAL NOT NULL, event TEXT NOT NULL, job_id TEXT, actor TEXT, detail TEXT)")
