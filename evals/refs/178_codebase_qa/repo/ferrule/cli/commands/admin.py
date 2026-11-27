"""``ferrule admin ...``"""

from __future__ import annotations

from ferrule.cli.commands import app_from
from ferrule.cli.dispatch import Group, arg
from ferrule.migrations.runner import MigrationRunner
from ferrule.util.text import table

group = Group("admin", help="maintenance", default=None)


@group.command("migrate")
@arg("--dry-run", action="store_true")
@arg("--db")
def migrate(ns) -> int:
    """Apply pending schema migrations."""
    app = app_from(ns)
    runner = MigrationRunner(ns.db or app.settings["storage"]["sqlite"]["path"])
    plan = runner.plan()
    for mid in plan:
        print(("would apply " if ns.dry_run else "apply ") + mid)
    if not ns.dry_run:
        runner.apply(plan)
    return 0


@group.command("hooks")
def hooks(ns) -> int:
    """Show every hook and its callbacks in run order."""
    app = app_from(ns)
    rows = [(h, ", ".join(app.hooks.callbacks(h))) for h in app.hooks.hooks()]
    print(table(rows, ["hook", "callbacks"]))
    return 0


@group.command("routes")
def routes(ns) -> int:
    """Print the route table."""
    app = app_from(ns)
    print(table([(r.method, r.path, r.name) for r in app.routes.routes], ["method", "path", "name"]))
    return 0


@group.command("plugins")
def plugins(ns) -> int:
    """Loaded plugins, in load order."""
    app = app_from(ns)
    print("\n".join(app.plugins))
    return 0
