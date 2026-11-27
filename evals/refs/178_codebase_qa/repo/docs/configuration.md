# Configuration

## Layers

1. `ferrule/config/defaults.py`
2. `conf/base.ini`
3. `conf/profiles/<profile>.ini` — the profile is `FERRULE_PROFILE`, else the
   `profile` key
4. Environment
5. `--set key=value` on the CLI

INI sections are dotted paths (`[storage.wal]`); `[ferrule]` is the root.
`key+ = x` appends to a list and `key- = x` removes from it.

## Keys

| Key | Type | Default | Notes |
|---|---|---|---|
| `scheduler.max_parallel` | int | 8 | Max concurrently running jobs |
| `scheduler.pool_size` | int | 4 | CPU shares leased on the node |
| `scheduler.reserve_control_worker` | bool | true | |
| `scheduler.lease_timeout` | duration | 45s | |
| `storage.backend` | str | memory | `memory`, `sqlite`, `wal` |
| `storage.cache` | bool | false | Read cache in front of the store |
| `storage.wal.enabled` | bool | false | Journal writes of non-WAL backends; `no` disables |
| `storage.wal.fsync_interval_ms` | int | 200 | |
| `retry.<family>.*` | | see `retry.default` | `base_ms`, `factor`, `cap_ms`, `max_attempts`, `count_expiry` |
| `ratelimit.burst` | int | 40 | Bucket size per API key |
| `ratelimit.rate_per_s` | float | 20 | Refill rate |
| `ratelimit.tiers.<tier>` | float | | Multiplier for burst and rate |
| `cache.namespace` | str | fr | Prefix of every cache key |

## Environment

`FERRULE_<SEGMENT>__<SEGMENT>...`, upper case. Only keys that exist in the
defaults or a config file can be set from the environment.

Set `FERRULE_LEGACY_ENV=1` to honour the 1.x names `FERRULE_WORKERS`,
`FERRULE_DB` and `FERRULE_WAL_DIR`.
