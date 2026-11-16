from ferrule.config import load_settings
from ferrule.jobs.model import Job
from ferrule.storage.cached import CachedStore
from ferrule.storage.factory import backend_name, create_store
from ferrule.storage.memory import MemoryStore
from ferrule.storage.sqlite_store import SqliteStore


def test_dev_store_is_plain_memory():
    s = load_settings(env={"FERRULE_PROFILE": "dev"})
    store = create_store(s)
    assert type(store) is MemoryStore
    assert backend_name(s) == "memory"


def test_sqlite_roundtrip(tmp_path):
    store = SqliteStore({"sqlite": {"path": str(tmp_path / "x.db"), "timeout_s": 1.0}})
    job = Job(kind="export.s3", payload={"a": 1})
    store.put(job)
    got = store.get(job.id)
    assert got.payload == {"a": 1} and got.kind == "export.s3"
    assert [j.id for j in store.by_state("NEW")] == [job.id]


def test_cache_wrapper_reads_through():
    inner = MemoryStore()
    store = CachedStore(inner, size=1)
    a, b = Job(kind="export.s3"), Job(kind="export.s3")
    store.put(a)
    store.put(b)
    assert store.get(a.id) is a
    assert store.name == "cachedstore(memory)"
