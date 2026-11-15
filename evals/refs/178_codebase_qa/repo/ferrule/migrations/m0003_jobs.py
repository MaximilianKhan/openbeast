"""The jobs table."""

ID = "0003_jobs"
DEPENDS = ["0001_init"]


def up(db):
    db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, tenant TEXT NOT NULL, state TEXT NOT NULL, doc TEXT NOT NULL)")
