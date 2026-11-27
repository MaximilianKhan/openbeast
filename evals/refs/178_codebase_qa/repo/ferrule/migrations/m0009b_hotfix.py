"""Hotfix for the 2.4.1 incident. Applied by hand on the affected clusters; superseded by 0010."""

ID = "0009b_hotfix"
DEPENDS = ["0003_jobs"]
PRIORITY = -5


def up(db):
    db.execute("UPDATE jobs SET state = 'QUEUED' WHERE state = 'LEASED'")
