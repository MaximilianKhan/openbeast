/tmp/eval_codeqa/repo is a checkout of "ferrule" 2.7.0, a Python job-queue service (about 100 files, ~5 kLOC: code, config, docs, tests). Answer the 12 questions below about it and write the answers to /tmp/eval_codeqa/answers.json.

Ground rules
- The code is the ground truth. Comments, docs, README, CHANGELOG and tests may be out of date.
- "Shipped settings" means the object returned by ferrule.config.load_settings() using the repo's conf/ directory, with an environment that contains no variable whose name starts with FERRULE, no overrides and no profile argument.
- "The app" means ferrule.app.build_app() built from the shipped settings. Filesystem locations (storage.sqlite.path, storage.wal.dir) do not affect any answer, so you may point them elsewhere if you run the code.
- The compiled extension ferrule._native has not been built in this checkout.
- You may read, run and copy the code however you like; the answers must describe the repository exactly as shipped.

Questions
q1 (integer). load_settings() is called as for the shipped settings, except that the environment contains exactly these two variables: FERRULE_STORAGE__WAL__FSYNC_INTERVAL_MS=5 and FERRULE_WAL__FSYNC_INTERVAL_MS=3. What is settings["storage"]["wal"]["fsync_interval_ms"]?
q2 (integer). The value of app.scheduler.workers.
q3 (integer). A job of kind "export.s3" is submitted with app.service.submit(...), a handler that raises an exception on every call is registered for that kind with app.scheduler.register(...), and app.scheduler.run_until_idle() runs until the job is in a terminal state. What is the sum, in milliseconds, of all retry delays the scheduler schedules for this job (the values it appends to app.scheduler.delays_ms)?
q4 (list of strings, in run order). The names of the callbacks on the "before_submit" hook of app.hooks, in the order they run (what app.hooks.callbacks("before_submit") returns).
q5 (list of strings, sorted ascending). Every role name known to app.policy for which app.policy.can(role, "jobs.cancel", owner=False) is True.
q6 (string). With no FERRULE* environment variables, the CLI entry point called as ferrule.cli.main.main(["q", "ls", "--all"]) (the command line `ferrule q ls --all`) dispatches to which Python function? Give the module path and function name joined by a dot, e.g. "ferrule.cli.commands.example.do_thing".
q7 (list of strings, in order). On a fresh database where no migration has been applied, the migration IDs (the ID value each migration module defines) in the order MigrationRunner.plan() returns them.
q8 (list of strings, sorted ascending). Every route in app.routes whose handler is the function JobsView.retry, each written as "<METHOD> <path template>" exactly as the route table holds it, e.g. "GET /api/v9/things/{thing_id}".
q9 (integer). One API key whose token has tier "standard" and role "operator" sends 100 POST requests to the non-admin job-creation route (route name "jobs.create") through app.server.handle(), each with a valid, distinct job body. The rate limiter's clock does not advance at all during the 100 requests. How many of the 100 responses have a status other than 429?
q10 (string). The exact cache key that JobCache(settings, tenant="Acme").get("j:42") reads, where JobCache is imported from ferrule.cache and settings are the shipped settings.
q11 (string). A new Job(kind="ingest.http") (state NEW, attempts 0) is driven by a JobStateMachine() created with default arguments. Each event below is applied in turn with machine.apply(job, event, policy_for_kind("ingest.http", settings)), settings being the shipped settings:
submit, lease, expire, start, lease, expire, lease, start, fail, wake, lease, start, expire, wake, lease, start, complete
Answer "<final state>/<final attempts>", e.g. "QUEUED/2".
q12 (list of strings, outermost first). When app.service.submit(...) stores a new job (dry_run false), which classes' put methods execute? Give module-qualified class names ("package.module.ClassName"), in the order they are entered. List a class only if the put method that runs is defined in that class itself.

Answer format
/tmp/eval_codeqa/answers.json must be one JSON object with keys "q1" ... "q12". Integers as JSON numbers, strings as JSON strings, lists as JSON arrays of strings. Example of the shape (values are placeholders): {"q1": 0, "q2": 0, "q3": 0, "q4": ["a.b"], "q5": ["x"], "q6": "m.f", "q7": ["0000_x"], "q8": ["GET /x"], "q9": 0, "q10": "k", "q11": "NEW/0", "q12": ["m.C"]}
All 12 answers must be exactly right to pass.
