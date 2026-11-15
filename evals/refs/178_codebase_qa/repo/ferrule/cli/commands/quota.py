"""``ferrule quota ...``"""

from __future__ import annotations

from ferrule.cli.commands import app_from
from ferrule.cli.dispatch import Group, arg
from ferrule.plugins import quota as quota_plugin
from ferrule.util.text import table

group = Group("quota", aliases=("q", "quotas"), help="tenant submission quotas", default="show")


@group.command("show", aliases=("get",))
@arg("--tenant", default="default")
def cmd_show(ns) -> int:
    """Show the limit for one tenant."""
    app = app_from(ns)
    cfg = app.settings["quota"]
    limit = (cfg.get("tenants") or {}).get(ns.tenant, cfg["default_limit"])
    print(f"{ns.tenant}: {limit} per {cfg['window']}")
    return 0


@group.command("usage", aliases=("ls", "list"))
@arg("--all", action="store_true", help="include tenants with no usage")
def cmd_usage(ns) -> int:
    """Current usage for every tenant."""
    app = app_from(ns)
    tenants = sorted({j.tenant for j in app.store.scan()} | ({"default"} if ns.all else set()))
    print(table([(t, quota_plugin.usage(t)) for t in tenants], ["tenant", "used"]))
    return 0


@group.command("set")
@arg("tenant")
@arg("limit", type=int)
def cmd_set(ns) -> int:
    """Set a tenant's limit for this process (persist it in the config)."""
    quota_plugin._STATE["overrides"][ns.tenant] = ns.limit
    print(f"{ns.tenant}: {ns.limit}")
    return 0


@group.command("reset")
@arg("tenant")
def cmd_reset(ns) -> int:
    """Forget a tenant's usage window."""
    quota_plugin.reset(ns.tenant)
    return 0
