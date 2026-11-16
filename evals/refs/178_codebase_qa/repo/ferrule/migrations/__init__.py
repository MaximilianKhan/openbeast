"""Schema migrations for the SQLite backend.

Only modules listed in ``MIGRATIONS`` are part of the plan; a module that
is present on disk but not listed is ignored. Each module defines:

* ``ID``        unique id, recorded in the ``ferrule_migrations`` table
* ``DEPENDS``   ids that must be applied first
* ``PRIORITY``  optional int; among migrations that are ready, lower runs
                first (default 0), then by ``ID``
* ``REPLACES``  optional list of ids this migration squashes
* ``up(db)``    the migration itself
"""

MIGRATIONS = [
    "m0001_init",
    "m0002_tenants",
    "m0003_jobs",
    "m0004_job_index",
    "m0005_job_tags",
    "m0006_job_deadline",
    "m0007_queues",
    "m0008_audit",
    "m0010_squash_jobs",
    "m0011_quota",
    "m0012_webhooks",
    "m0013_audit_index",
    "m0014_retention",
]
