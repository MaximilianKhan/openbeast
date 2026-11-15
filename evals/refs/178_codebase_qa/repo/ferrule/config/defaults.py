"""Built-in configuration defaults.

Every key that ferrule reads must have a default here: the loader only
applies environment overrides to keys that already exist in the merged
tree, and the schema uses the type of the default to coerce values read
from INI files and the environment (see ``ferrule.config.schema``).

Keep this module free of imports from the rest of the package; it is
imported by the CLI before logging is configured.
"""

DEFAULTS = {
    # Which conf/profiles/<name>.ini to layer on top of conf/base.ini.
    "profile": "dev",
    "tenant": "default",
    "api": {
        "prefix": "/api",
        "version": "v2",
        "trailing_slash": False,
        # Pre-2.0 colon-style action routes (POST /jobs/{id}:retry).
        "legacy_routes": True,
        "max_body_kb": 512,
    },
    "scheduler": {
        "max_parallel": 8,
        "pool_size": 4,
        # Keep one worker free for control-plane jobs (drain, purge).
        "reserve_control_worker": True,
        "lease_timeout": "45s",
        "heartbeat": "10s",
        "tick_ms": 250,
        # Use the pre-2.4 retry arithmetic (ferrule.jobs.retry_legacy).
        "legacy_retry": False,
    },
    "storage": {
        "backend": "memory",
        "cache": False,
        "wal": {
            "dir": "/var/lib/ferrule/wal",
            "fsync_interval_ms": 200,
            "segment_mb": 64,
        },
        "sqlite": {
            "path": ":memory:",
            "timeout_s": 5.0,
        },
    },
    "retry": {
        "default": {
            "base_ms": 500,
            "factor": 2.0,
            "cap_ms": 30000,
            "max_attempts": 5,
            "count_expiry": False,
        },
        "export": {
            "factor": 3.0,
            "cap_ms": 8000,
        },
        "ingest": {
            "max_attempts": 3,
            "count_expiry": True,
        },
    },
    "plugins": {
        "enabled": ["audit", "quota", "dedupe", "tagger", "notify", "trace"],
        "disabled": [],
    },
    "quota": {
        "enforce": True,
        "default_limit": 1000,
        "window": "1h",
    },
    "dedupe": {
        "window": "10m",
        "fields": ["kind", "tenant", "payload"],
    },
    "ratelimit": {
        "rate_per_s": 20.0,
        "burst": 40,
        "initial_fill": 1.0,
        "tiers": {
            "free": 0.5,
            "standard": 1.0,
            "pro": 4.0,
        },
    },
    "cache": {
        "namespace": "fr",
        "ttl_s": 300,
    },
    "auth": {
        # Overlays for ferrule.auth.roles.BUILTIN_ROLES; see docs/permissions.md.
        "roles": {},
        "anonymous_role": "viewer",
        "token_ttl": "12h",
    },
    "events": {
        "buffer": 1024,
        "drop_on_full": False,
    },
    "notify": {
        "webhook_timeout_s": 3.0,
        "channels": ["log"],
    },
    "sla": {
        "default_deadline": "15m",
    },
    "logging": {
        "level": "INFO",
        "json": False,
    },
}
