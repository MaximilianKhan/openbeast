"""Squash of 0004-0006 for fresh installs."""

ID = "0010_squash_jobs"
DEPENDS = ["0003_jobs"]
REPLACES = [
    "0004_job_index",
    "0005_job_tags",
    "0006_job_deadline",
]


def up(db):
    db.execute("CREATE INDEX jobs_tenant_state ON jobs (tenant, state)")
    db.execute("CREATE TABLE job_tags (job_id TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (job_id, tag))")
    db.execute("ALTER TABLE jobs ADD COLUMN deadline REAL")
