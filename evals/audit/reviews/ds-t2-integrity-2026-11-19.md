# DS-T2 integrity review notes (2026-11-19, sealed)

Moved out of the public board note (evals/boards/v5/board.json is rendered
into README.md and docs/RESULTS.md, which the measured agent can read).

- The integrity scan flagged 173_taskgroup_cancel on a silent `curl` to
  CPython's taskgroups.py on GitHub. The sandbox blocked it: the follow-up
  showed `curl: (7) Couldn't connect to server`; `fetch` was refused too.
  Not contamination.
- The same unit then read CPython's own `asyncio/taskgroups.py` from the local
  stdlib (the eval prefix's 3.12 and Homebrew's 3.13) before writing its own
  TaskGroup without importing that module. Not a sealed path, so the pass
  counts. Task-design note: a reference implementation of what 173 asks for
  ships with every Python install; consider this in the next suite revision.
- Scan improvement: treat a network command with empty output as an attempt,
  not a read.
