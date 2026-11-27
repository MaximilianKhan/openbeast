# Retries

A failed job waits `base_ms * factor^n` milliseconds before it runs again,
capped at `cap_ms`. `max_attempts` is the number of retries after the first
run; a job that keeps failing ends in `DEAD`.

Policies are per *family*: the part of the kind before the first dot
(`export.s3` -> `export`). Fields missing from `retry.<family>` come from
`retry.default`.

Lease expiries count as failures for families with `count_expiry = true`
(ingestion, by default).

`GET /jobs/{id}/retry` previews the next delay without changing the job.
