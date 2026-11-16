"""SLA deadline column."""

ID = "0006_job_deadline"
DEPENDS = ["0004_job_index"]


def up(db):
    db.execute("ALTER TABLE jobs ADD COLUMN deadline REAL")
