"""Retention settings."""

ID = "0014_retention"
DEPENDS = ["0006_job_deadline", "0008_audit"]
PRIORITY = -2


def up(db):
    db.execute("CREATE TABLE retention (tenant TEXT PRIMARY KEY, keep_days INTEGER NOT NULL DEFAULT 30)")
