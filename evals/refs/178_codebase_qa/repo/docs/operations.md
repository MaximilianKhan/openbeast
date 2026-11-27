# Operations

## Deploying

The container runs `python -m ferrule serve` behind the sidecar. The
image ships `conf/`; mount deployment-specific overrides over
`conf/profiles/prod.ini`, or point `FERRULE_CONF_DIR` at another directory.

Health: `GET /healthz` (process up) and `GET /readyz` (store reachable).
Both are outside `/api/<version>` and need no token.

## Storage

* `sqlite`: one file at `storage.sqlite.path`. Run `ferrule admin migrate`
  after upgrades. Back up with `sqlite3 .backup`.
* `wal`: the log *is* the store. Segments roll at `storage.wal.segment_mb`.
* Journaling (`storage.wal.enabled`) adds a WAL in front of a non-WAL
  backend. Prod relies on volume snapshots instead.

## Rate limits

Per API key, token bucket. Tiers scale both the bucket and the refill rate.
`429` responses carry `retry_after_s`.

## Runbook: a tenant is stuck

1. `ferrule job ls --tenant T --state RETRY_WAIT`
2. Check `last_error` with `ferrule job show ID --tenant T`.
3. If the job is DEAD and the cause is fixed: `ferrule job retry ID`.
4. Quota: `ferrule quota show --tenant T`; usage with `ferrule quota usage`.
