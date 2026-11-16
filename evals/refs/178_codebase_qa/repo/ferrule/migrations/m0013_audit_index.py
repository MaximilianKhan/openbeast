"""Index the audit trail."""

ID = "0013_audit_index"
DEPENDS = ["0008_audit", "0012_hooks"]


def up(db):
    db.execute("CREATE INDEX audit_job ON audit (job_id, at)")
