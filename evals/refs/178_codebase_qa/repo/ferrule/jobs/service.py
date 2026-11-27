"""JobService: the entry point the API and CLI use to create and control jobs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ferrule.constants import MAX_PAYLOAD_KB, MAX_TAGS, PRIORITY_CORE
from ferrule.errors import Forbidden, NotFound, ValidationFailed
from ferrule.jobs.kinds import KNOWN_KINDS
from ferrule.jobs.model import Job
from ferrule.jobs.retry import policy_for_kind
from ferrule.jobs.states import JobStateMachine

log = logging.getLogger(__name__)


@dataclass
class Context:
    actor: Optional[str] = None
    role: str = "viewer"
    tenant: str = "default"
    dry_run: bool = False


class JobService:
    def __init__(self, settings, store, hooks, policy, events=None) -> None:
        self.settings = settings
        self.store = store
        self.hooks = hooks
        self.policy = policy
        self.events = events
        self.machine = JobStateMachine(hooks)
        hooks.register("before_submit", self.validate_payload, priority=PRIORITY_CORE,
                       name="service.validate_payload")

    # -- hooks ------------------------------------------------------------
    def validate_payload(self, job: Job, ctx: Context) -> None:
        if job.kind not in KNOWN_KINDS:
            raise ValidationFailed(f"unknown job kind {job.kind!r}")
        size_kb = len(repr(job.payload)) / 1024
        if size_kb > MAX_PAYLOAD_KB:
            raise ValidationFailed(f"payload is {size_kb:.0f} KiB, limit {MAX_PAYLOAD_KB}")
        if len(job.tags) > MAX_TAGS:
            raise ValidationFailed(f"at most {MAX_TAGS} tags")

    # -- commands ---------------------------------------------------------
    def submit(self, kind: str, payload: Dict[str, Any], ctx: Context,
               tags: Optional[List[str]] = None, queue: str = "default") -> Job:
        job = Job(kind=kind, tenant=ctx.tenant, payload=dict(payload), owner=ctx.actor,
                  queue=queue, tags=list(tags or []))
        self.hooks.fire("before_submit", job, ctx)
        if ctx.dry_run:
            return job
        self.machine.apply(job, "submit", policy_for_kind(kind, self.settings))
        self.store.put(job)
        if self.events is not None:
            self.events.publish("job.submitted", {"id": job.id, "kind": job.kind})
        return job

    def get(self, job_id: str, ctx: Context) -> Job:
        job = self.store.get(job_id)
        if job is None or job.tenant != ctx.tenant:
            raise NotFound(job_id)
        return job

    def cancel(self, job_id: str, ctx: Context) -> Job:
        job = self.get(job_id, ctx)
        own = job.owner is not None and job.owner == ctx.actor
        if not self.policy.can(ctx.role, "jobs.cancel", owner=own):
            raise Forbidden(f"role {ctx.role} may not cancel this job")
        self.hooks.fire("before_cancel", job, ctx)
        self.machine.apply(job, "cancel", policy_for_kind(job.kind, self.settings))
        self.store.put(job)
        return job

    def retry(self, job_id: str, ctx: Context) -> Job:
        job = self.get(job_id, ctx)
        if not self.policy.can(ctx.role, "jobs.retry", owner=job.owner == ctx.actor):
            raise Forbidden(f"role {ctx.role} may not retry this job")
        self.machine.apply(job, "resurrect", policy_for_kind(job.kind, self.settings))
        self.store.put(job)
        return job

    def list(self, ctx: Context, state: Optional[str] = None, limit: int = 100) -> List[Job]:
        jobs = [j for j in self.store.scan() if j.tenant == ctx.tenant]
        if state:
            jobs = [j for j in jobs if j.state == state]
        return jobs[:limit]
