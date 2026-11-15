"""/jobs endpoints."""

from __future__ import annotations

from ferrule.api.routing import Router, View, action, get
from ferrule.errors import ValidationFailed


class JobsView(View):
    resource = "jobs"

    @get("")
    def index(self, request):
        state = request.query.get("state")
        jobs = self.app.service.list(request.ctx, state=state, limit=int(request.query.get("limit", 100)))
        return 200, {"jobs": [j.to_dict() for j in jobs]}

    @action("", methods=("POST",), name="jobs.create")
    def create(self, request):
        body = request.json or {}
        if "kind" not in body:
            raise ValidationFailed("kind is required")
        job = self.app.service.submit(body["kind"], body.get("payload", {}), request.ctx,
                                      tags=body.get("tags"), queue=body.get("queue", "default"))
        return 201, job.to_dict()

    @action("/bulk", name="jobs.bulk_create")
    def bulk_create(self, request):
        items = (request.json or {}).get("jobs", [])
        out = [self.app.service.submit(i["kind"], i.get("payload", {}), request.ctx).id for i in items]
        return 201, {"ids": out}

    @get("/{job_id}")
    def show(self, request, job_id):
        return 200, self.app.service.get(job_id, request.ctx).to_dict()

    @action("/{job_id}/retry")
    def retry(self, request, job_id):
        return 200, self.app.service.retry(job_id, request.ctx).to_dict()

    @get("/{job_id}/retry", name="jobs.retry_preview")
    def retry_preview(self, request, job_id):
        job = self.app.service.get(job_id, request.ctx)
        policy = self.app.scheduler.policy(job.kind)
        return 200, {"attempts": job.attempts, "next_delay_ms": policy.delay_ms(job.attempts + 1)}

    @action("/{job_id}/cancel")
    def cancel(self, request, job_id):
        return 200, self.app.service.cancel(job_id, request.ctx).to_dict()


jobs_router = Router("/jobs")
jobs_router.mount_view(JobsView)
