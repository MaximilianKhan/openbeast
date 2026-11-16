# CLI

`ferrule <group> <command> [options]`. Groups and commands can be
abbreviated to any unique prefix, and have short aliases:

| Group | Aliases | Commands |
|---|---|---|
| queue | queues, q | list (ls), pause, resume, drain |
| quota | quotas | show (get), usage, set, reset |
| job | jobs, j | list (ls), submit (add), show, cancel, retry |
| admin | | migrate, hooks, routes, plugins |
| config | cfg | show, env |

Every command accepts `--profile NAME` and `--set KEY=VALUE`.
