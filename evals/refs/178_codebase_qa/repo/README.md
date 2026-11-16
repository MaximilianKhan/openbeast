# ferrule

A small multi-tenant job queue: submit jobs over HTTP or the CLI, run them
on a cooperative scheduler, retry failures with backoff, and keep an audit
trail. One process, SQLite or a write-ahead log for storage.

```
pip install -e .            # builds the ferrule._native extension (needs cargo)
python -m ferrule config show
python -m ferrule job submit export.s3 --payload '{"bucket": "b"}'
python -m ferrule admin routes
```

## Layout

| Path | What |
|---|---|
| `ferrule/config/` | defaults, INI layering, environment overrides |
| `ferrule/jobs/` | job model, lifecycle, retry policy, scheduler, service |
| `ferrule/auth/` | roles, permission policy, API tokens |
| `ferrule/storage/` | memory, SQLite and WAL stores; journaling and cache wrappers |
| `ferrule/api/` | router, views, middleware, WSGI adapter |
| `ferrule/plugins/` | audit, dedupe, quota, tagger, notify, trace, sla |
| `ferrule/migrations/` | SQLite schema migrations |
| `ferrule/cli/` | the `ferrule` command |
| `conf/` | shipped configuration (`base.ini` + `profiles/*.ini`) |

## Configuration in one paragraph

Settings come from `ferrule/config/defaults.py`, then `conf/base.ini`, then
the profile file, then environment variables, then `--set key=value`.
Every key can be overridden from the environment as `FERRULE_` followed by
the dotted key upper-cased with `__` between segments, e.g.
`FERRULE_STORAGE__WAL__FSYNC_INTERVAL_MS=20`. See `docs/configuration.md`.

## Scheduler

The scheduler runs `scheduler.max_parallel` workers (8 by default). Jobs
that fail are retried with exponential backoff, `retry.<family>.base_ms *
factor^n`, up to `max_attempts` retries.

## CLI cheatsheet

```
ferrule q ls            # list queues  (q = queue)
ferrule job ls --state DEAD
ferrule quota show --tenant acme
ferrule admin migrate --dry-run
ferrule cfg env storage.wal.dir
```

## Development

```
pytest -q
```

The test suite stubs `ferrule._native` (see `conftest.py`) so it runs
without a Rust toolchain.
