from ferrule.auth.policy import Policy
from ferrule.auth.roles import BUILTIN_ROLES


def make():
    return Policy(BUILTIN_ROLES)


def test_viewer_reads():
    assert make().can("viewer", "jobs.read")
    assert not make().can("viewer", "jobs.cancel")


def test_operator_cancels_own_only():
    p = make()
    assert p.can("operator", "jobs.cancel", owner=True)
    assert not p.can("operator", "jobs.cancel", owner=False)


def test_maintainer_cannot_purge():
    assert not make().can("maintainer", "jobs.purge")


def test_admin_everything():
    assert make().can("admin", "jobs.purge")
    assert make().can("admin", "anything.at.all")


def test_auditor_reads_audit_not_jobs():
    p = make()
    assert p.can("auditor", "audit.read")
    assert not p.can("auditor", "jobs.read")
