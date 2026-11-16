"""/tenants endpoints (read-only; tenants are provisioned out of band)."""

from __future__ import annotations

from ferrule.api.routing import Router, View, get


class TenantsView(View):
    resource = "tenants"

    @get("")
    def index(self, request):
        tenants = sorted({j.tenant for j in self.app.store.scan()})
        return 200, {"tenants": tenants}

    @get("/{tenant}")
    def show(self, request, tenant):
        n = sum(1 for j in self.app.store.scan() if j.tenant == tenant)
        return 200, {"tenant": tenant, "jobs": n}


tenants_router = Router("/tenants")
tenants_router.mount_view(TenantsView)
