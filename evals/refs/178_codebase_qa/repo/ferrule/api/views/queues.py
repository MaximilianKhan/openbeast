"""/queues endpoints."""

from __future__ import annotations

from collections import Counter

from ferrule.api.routing import Router, View, action, get


class QueuesView(View):
    resource = "queues"

    @get("")
    def index(self, request):
        counts = Counter(j.queue for j in self.app.service.list(request.ctx, limit=10**6))
        return 200, {"queues": [{"name": q, "jobs": n} for q, n in sorted(counts.items())]}

    @get("/{queue}")
    def show(self, request, queue):
        jobs = [j for j in self.app.service.list(request.ctx, limit=10**6) if j.queue == queue]
        by_state = Counter(j.state for j in jobs)
        return 200, {"name": queue, "states": dict(by_state)}

    @action("/{queue}/pause")
    def pause(self, request, queue):
        self.app.paused.add(queue)
        return 200, {"name": queue, "paused": True}

    @action("/{queue}/resume")
    def resume(self, request, queue):
        self.app.paused.discard(queue)
        return 200, {"name": queue, "paused": False}

    @action("/{queue}/drain", name="queues.drain")
    def drain(self, request, queue):
        n = 0
        for job in self.app.service.list(request.ctx, state="QUEUED", limit=10**6):
            if job.queue == queue:
                self.app.service.cancel(job.id, request.ctx)
                n += 1
        return 200, {"name": queue, "cancelled": n}


queues_router = Router("/queues")
queues_router.mount_view(QueuesView)
