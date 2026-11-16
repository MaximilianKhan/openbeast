"""Index jobs by tenant and state."""

ID = "0004_job_index"
DEPENDS = ["0003_jobs"]


def up(db):
    db.execute("CREATE INDEX jobs_tenant_state ON jobs (tenant, state)")
