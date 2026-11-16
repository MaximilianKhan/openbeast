"""/admin endpoints: cross-tenant operations for operators.

The job endpoints are re-mounted per tenant so that support tooling can act
on a tenant's jobs without impersonating one of its users.
"""

from __future__ import annotations

from ferrule.api.routing import Router, View, action, get
from ferrule.api.views.jobs import jobs_router


class AdminView(View):
    resource = "admin"

    @get("/plugins")
    def plugins(self, request):
        from ferrule.plugins import loaded
        return 200, {"plugins": loaded(self.app.hooks)}

    @get("/hooks")
    def hooks(self, request):
        return 200, {h: self.app.hooks.callbacks(h) for h in self.app.hooks.hooks()}

    @action("/purge", name="jobs.purge")
    def purge(self, request):
        n = 0
        for job in list(self.app.store.scan()):
            if job.terminal:
                self.app.store.delete(job.id)
                n += 1
        return 200, {"purged": n}


admin_router = Router("/admin")
admin_router.mount_view(AdminView)
admin_router.include(jobs_router, prefix="/tenants/{tenant}")
