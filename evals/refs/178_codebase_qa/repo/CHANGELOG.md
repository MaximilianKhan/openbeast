# Changelog

## 2.7.0
- Admin API re-mounts the job endpoints under `/admin/tenants/{tenant}`.
- `lead` role.
- Rate limiter: `ratelimit.initial_fill` controls how full a new bucket starts.

## 2.6.0
- Cache keys are tenant-scoped; the cache schema version was bumped.
- `JobCache` split into `JobRecordCache` and `QueueStatsCache`.
- Permission evaluation is nearest-level-decides (was: any deny in the
  inheritance chain wins).

## 2.5.0
- `ingest.http` / `ingest.https` moved to the fetcher pool and the `fetch`
  retry family.
- Storage wrappers: `storage.wal.enabled` journals writes for non-WAL
  backends.

## 2.4.0
- Retry policy rewrite: per-family `factor`, `max_attempts` now counts
  attempts, not retries. The old arithmetic is available as
  `scheduler.legacy_retry`.

## 2.3.0
- CLI: `q` is now the alias for `quota`. Set `FERRULE_CLI_COMPAT=1` for the
  old shortcuts.
- Environment variable short names for `scheduler`, `ratelimit`,
  `storage.wal` and `storage.sqlite` subtrees.

## 2.2.0
- Migrations can declare `PRIORITY`.
- Squash migrations (`REPLACES`).

## 2.0.0
- Versioned API prefix (`/api/<version>`); colon-style action routes are
  behind `api.legacy_routes`.
