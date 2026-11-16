#!/usr/bin/env python3
"""Reference solver for 178_codebase_qa: derives every answer by EXECUTING
the bundled repo (not by restating what the author believes it does).

    python3 solve.py [REPO_DIR]  ->  prints answers.json to stdout

build_task.py runs this to produce the hashes embedded in the validator, so
the validator can never disagree with the code it asks about. The native
extension is stubbed exactly the way the repo's own conftest.py does it;
nothing any question asks about depends on its output.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import types
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "repo"))

stub = types.ModuleType("ferrule._native")
stub.ABI_VERSION = 3
stub.fingerprint = lambda data: hashlib.blake2b(data, digest_size=16).hexdigest()
stub.crc32c = lambda data, seed=0: zlib.crc32(data, seed)
stub.monotonic_ns = lambda: 0
sys.modules["ferrule._native"] = stub
sys.path.insert(0, REPO)
for k in [k for k in os.environ if k.startswith("FERRULE")]:
    del os.environ[k]

from ferrule.api.middleware import Request  # noqa: E402
from ferrule.api.views.jobs import JobsView  # noqa: E402
from ferrule.app import build_app  # noqa: E402
from ferrule.cache import JobCache  # noqa: E402
from ferrule.cli.main import REGISTRY  # noqa: E402
from ferrule.config import load_settings  # noqa: E402
from ferrule.jobs.clock import ManualClock  # noqa: E402
from ferrule.jobs.model import Job  # noqa: E402
from ferrule.jobs.retry import policy_for_kind  # noqa: E402
from ferrule.jobs.service import Context  # noqa: E402
from ferrule.jobs.states import JobStateMachine  # noqa: E402
from ferrule.migrations.runner import discover, plan  # noqa: E402
from ferrule.storage.base import Store  # noqa: E402

CONF = os.path.join(REPO, "conf")
A = {}

# q1: env layering + alias table + clamp
s1 = load_settings(conf_dir=CONF, env={"FERRULE_STORAGE__WAL__FSYNC_INTERVAL_MS": "5",
                                       "FERRULE_WAL__FSYNC_INTERVAL_MS": "3"})
A["q1"] = s1["storage"]["wal"]["fsync_interval_ms"]

shipped = load_settings(conf_dir=CONF, env={})
tmp = tempfile.mkdtemp(prefix="solve178-")
# Only the two filesystem locations change, so the app can start off-host.
runnable = load_settings(conf_dir=CONF, env={}, overrides={
    "storage.sqlite.path": os.path.join(tmp, "f.db"), "storage.wal.dir": os.path.join(tmp, "wal")})
for k in shipped:
    if k != "storage":
        assert shipped[k] == runnable[k], k


def fresh_app():
    return build_app(load_settings(conf_dir=CONF, env={}, overrides={
        "storage.sqlite.path": os.path.join(tempfile.mkdtemp(dir=tmp), "f.db"),
        "storage.wal.dir": os.path.join(tempfile.mkdtemp(dir=tmp), "wal")}), clock=ManualClock())


app = fresh_app()
A["q2"] = app.scheduler.workers

# q3: always-failing export.s3 job driven by the real scheduler
app3 = fresh_app()


def boom(job):
    raise RuntimeError("upload failed")


app3.scheduler.register("export.s3", boom)
job3 = app3.service.submit("export.s3", {"bucket": "b"}, Context(actor="u1", role="operator"))
app3.scheduler.run_until_idle()
assert app3.store.get(job3.id).state == "DEAD", app3.store.get(job3.id).state
A["q3"] = sum(app3.scheduler.delays_ms)

A["q4"] = app.hooks.callbacks("before_submit")

A["q5"] = sorted(r for r in app.policy.names() if app.policy.can(r, "jobs.cancel", owner=False))

_, cmd, _ = REGISTRY.lookup(["q", "ls", "--all"])
A["q6"] = f"{cmd.func.__module__}.{cmd.func.__name__}"

A["q7"] = plan(discover(), set())

A["q8"] = sorted(f"{r.method} {r.path}" for r in app.routes.routes if r.handler is JobsView.retry)

# q9: 100 job-creation requests at one clock instant through the real pipeline
app9 = fresh_app()
tok = app9.tokens.issue(role="operator", tenant="default", tier="standard")
create_path = [r.path for r in app9.routes.routes if r.name == "jobs.create" and r.method == "POST"
               and "/admin/" not in r.path]
assert len(create_path) == 1, create_path
codes = []
for i in range(100):
    req = Request("POST", create_path[0], headers={"authorization": f"Bearer {tok}"},
                  body=json.dumps({"kind": "export.s3", "payload": {"n": i}}).encode())
    codes.append(app9.server.handle(req)[0])
assert set(codes) <= {201, 429}, set(codes)
A["q9"] = sum(1 for c in codes if c != 429)

cache = JobCache(shipped, tenant="Acme")
cache.get("j:42")
A["q10"] = cache.backend.reads[-1]

job11 = Job(kind="ingest.http")
m = JobStateMachine()
for ev in ["submit", "lease", "expire", "start", "lease", "expire", "lease", "start", "fail",
           "wake", "lease", "start", "expire", "wake", "lease", "start", "complete"]:
    m.apply(job11, ev, policy_for_kind("ingest.http", shipped))
A["q11"] = f"{job11.state}/{job11.attempts}"

# q12: record which classes' own put() bodies run when submit persists a job
app12 = fresh_app()
calls = []


def all_subclasses(c):
    for sub in c.__subclasses__():
        yield sub
        yield from all_subclasses(sub)


for cls in set(all_subclasses(Store)) | {Store}:
    if "put" in vars(cls):
        def wrap(orig, cls=cls):
            def put(self, job):
                calls.append(f"{cls.__module__}.{cls.__qualname__}")
                return orig(self, job)
            return put
        cls.put = wrap(vars(cls)["put"])
app12.service.submit("export.s3", {"x": 1}, Context(actor="u1", role="operator"))
A["q12"] = calls

shutil.rmtree(tmp, ignore_errors=True)
print(json.dumps(A, indent=1))
