import pytest

from ferrule.cli.main import REGISTRY
from ferrule.errors import AmbiguousCommand


def test_job_alias():
    group, cmd, rest = REGISTRY.lookup(["j", "ls", "--state", "DEAD"])
    assert group.name == "job" and cmd.name == "list" and rest == ["--state", "DEAD"]


def test_prefix():
    group, cmd, _ = REGISTRY.lookup(["adm", "rou"])
    assert (group.name, cmd.name) == ("admin", "routes")


def test_ambiguous_prefix():
    with pytest.raises(AmbiguousCommand):
        REGISTRY.lookup(["qu", "ls"])
