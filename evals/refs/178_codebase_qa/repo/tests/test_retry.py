from ferrule.config import load_settings
from ferrule.jobs.clock import ManualClock
from ferrule.jobs.model import Job
from ferrule.jobs.retry import RetryPolicy, policy_for_kind
from ferrule.jobs.scheduler import Scheduler
from ferrule.storage.memory import MemoryStore


def test_delay_progression():
    p = RetryPolicy(base_ms=100, factor=2.0, cap_ms=1000, max_attempts=5)
    assert [p.delay_ms(n) for n in range(0, 6)] == [0, 100, 200, 400, 800, 1000]


def test_family_overlay_dev():
    s = load_settings(env={"FERRULE_PROFILE": "dev"})
    p = policy_for_kind("export.gcs", s)
    assert p.factor == 3.0 and p.cap_ms == 8000 and p.base_ms == 500


def test_always_failing_job_dies_dev():
    s = load_settings(env={"FERRULE_PROFILE": "dev"})
    store = MemoryStore()
    sched = Scheduler(s, store, clock=ManualClock())

    def boom(job):
        raise RuntimeError("nope")

    sched.register("maintenance.vacuum", boom)
    job = Job(kind="maintenance.vacuum", state="QUEUED")
    store.put(job)
    sched.run_until_idle()
    assert store.get(job.id).state == "DEAD"
    assert store.get(job.id).attempts == 5
    assert sched.delays_ms == [500, 1000, 2000, 4000]
