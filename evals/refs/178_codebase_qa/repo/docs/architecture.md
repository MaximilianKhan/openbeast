# Architecture

```
            +-------------+     +-----------+
 HTTP  ---> | ApiServer   | --> | JobService| --> hooks (plugins)
            | ErrorMapper |     +-----------+          |
            | Authenticate|           |                v
            | Throttle    |           +------------> Store (cache -> journal -> backend)
            +-------------+                              ^
                                     Scheduler ----------+
```

* `build_app()` in `ferrule/app.py` is the only place that wires
  collaborators together.
* The scheduler is single-threaded; `workers` is a concurrency limit, not a
  thread count.
* Hooks are synchronous. `before_submit` callbacks may raise to reject a
  job; `after_transition` callbacks observe.
