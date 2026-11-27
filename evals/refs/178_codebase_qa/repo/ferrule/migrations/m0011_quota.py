"""Quota overrides per tenant."""

ID = "0011_quota"
DEPENDS = ["0002_tenants", "0005_job_tags"]


def up(db):
    db.execute("CREATE TABLE quota (tenant TEXT PRIMARY KEY, limit_per_window INTEGER NOT NULL)")
