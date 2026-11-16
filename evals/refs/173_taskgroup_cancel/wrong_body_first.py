"""WRONG for 173_taskgroup_cancel: the body's exception first instead of last."""
"""Reference solution for 173_taskgroup_cancel (copied to /tmp/eval_tg173/tg.py).

Structured-concurrency TaskGroup with a deadline and an explicit cancel(),
built only on asyncio.Task / Future primitives.
"""
import asyncio


class TaskGroup:
    def __init__(self, *, timeout=None):
        self._timeout = timeout
        self._entered = False
        self._exiting = False
        self._finished = False
        self._aborting = False
        self._loop = None
        self._parent = None
        self._tasks = {}                 # insertion-ordered set
        self._errors = []
        self._parent_cancel_requested = False
        self._waiter = None
        self._timer = None
        self._timed_out = False

    async def __aenter__(self):
        if self._entered:
            raise RuntimeError("TaskGroup has already been entered")
        self._loop = asyncio.get_running_loop()
        self._parent = asyncio.current_task()
        if self._parent is None:
            raise RuntimeError("TaskGroup cannot determine the parent task")
        self._entered = True
        if self._timeout is not None:
            self._timer = self._loop.call_at(self._loop.time() + self._timeout,
                                             self._on_timeout)
        return self

    # -- shutdown --------------------------------------------------------
    def _abort(self):
        self._aborting = True
        for t in list(self._tasks):
            if not t.done():
                t.cancel()

    def _internal_abort(self):
        self._abort()
        if not self._parent_cancel_requested:
            self._parent_cancel_requested = True
            self._parent.cancel()

    def _on_timeout(self):
        self._timer = None
        if self._aborting or self._finished:
            return
        self._timed_out = True
        self._internal_abort()

    def cancel(self):
        if not self._entered:
            raise RuntimeError("TaskGroup has not been entered")
        if self._aborting or self._finished:
            return
        self._internal_abort()

    # -- children --------------------------------------------------------
    def create_task(self, coro, *, name=None):
        if not self._entered:
            coro.close()
            raise RuntimeError("TaskGroup has not been entered")
        if self._finished or (self._exiting and not self._tasks):
            coro.close()
            raise RuntimeError("TaskGroup is finished")
        if self._aborting:
            coro.close()
            raise RuntimeError("TaskGroup is shutting down")
        task = self._loop.create_task(coro, name=name)
        self._tasks[task] = None
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task):
        self._tasks.pop(task, None)
        if self._waiter is not None and not self._tasks and not self._waiter.done():
            self._waiter.set_result(None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        self._errors.append(exc)
        if not self._aborting:
            self._internal_abort()

    # -- exit ------------------------------------------------------------
    async def __aexit__(self, et, exc, tb):
        self._exiting = True
        got_cancel = et is not None and issubclass(et, asyncio.CancelledError)
        if et is not None and not self._aborting:
            self._abort()
        while self._tasks:
            self._waiter = self._loop.create_future()
            try:
                await self._waiter
            except asyncio.CancelledError:
                got_cancel = True
                if not self._aborting:
                    self._abort()
            self._waiter = None
        self._finished = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._parent_cancel_requested:
            self._parent_cancel_requested = False
            self._parent.uncancel()
        outside = self._parent.cancelling() > 0
        errors = self._errors
        self._errors = []
        if et is not None and not issubclass(et, asyncio.CancelledError):
            errors.insert(0, exc)
        if errors:
            if outside:
                self._parent.uncancel()
                self._parent.cancel()
            raise ExceptionGroup("unhandled errors in a TaskGroup", errors) from None
        if got_cancel and outside:
            raise exc if (exc is not None and isinstance(exc, asyncio.CancelledError)) \
                else asyncio.CancelledError()
        if self._timed_out:
            raise TimeoutError
        return True          # swallow a CancelledError the group itself requested
