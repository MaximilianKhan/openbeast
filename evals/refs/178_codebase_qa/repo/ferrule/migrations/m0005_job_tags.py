"""Tag side table."""

ID = "0005_job_tags"
DEPENDS = ["0003_jobs"]


def up(db):
    db.execute("CREATE TABLE job_tags (job_id TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (job_id, tag))")
