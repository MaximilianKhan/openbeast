import pytest

from ferrule.config import load_settings
from ferrule.errors import RateLimited
from ferrule.jobs.clock import ManualClock
from ferrule.ratelimit import RateLimiter


def test_dev_bucket_refills():
    clock = ManualClock()
    rl = RateLimiter(load_settings(env={"FERRULE_PROFILE": "dev"}), clock=clock)
    for _ in range(1000):
        rl.check("k", "standard")
    with pytest.raises(RateLimited):
        rl.check("k", "standard")
    clock.advance(1000)
    rl.check("k", "standard")
