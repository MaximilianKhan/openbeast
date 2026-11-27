"""WRONG solution for 173_taskgroup_cancel: the plausible first draft.
Waits with gather(), cancels the parent on every internal trigger, uncancels
once, and treats any CancelledError seen during the exit as its own.
The prover asserts the validator rejects it with: behaviour mismatch"""
import asyncio


class TaskGroup:
    def __init__(self, *, timeout=None):
        self._timeout = timeout
        self._entered = self._done = self._aborting = False
        self._tasks = []
        self._errors = []
        self._timer = None
        self._timed_out = False
        self._we_cancelled = False

    async def __aenter__(self):
        if self._entered:
            raise RuntimeError('entered twice')
        self._entered = True
        self._loop = asyncio.get_running_loop()
        self._parent = asyncio.current_task()
        if self._timeout is not None:
            self._timer = self._loop.call_later(self._timeout, self._expire)
        return self

    def _expire(self):
        if not self._done and not self._aborting:
            self._timed_out = True
            self._shutdown(True)

    def _shutdown(self, cancel_parent):
        self._aborting = True
        for t in self._tasks:
            t.cancel()
        if cancel_parent:
            self._we_cancelled = True
            self._parent.cancel()

    def cancel(self):
        if not self._entered:
            raise RuntimeError('not entered')
        if not self._done and not self._aborting:
            self._shutdown(True)

    def create_task(self, coro, *, name=None):
        if not self._entered or self._done or self._aborting:
            coro.close()
            raise RuntimeError('group not accepting tasks')
        t = self._loop.create_task(coro, name=name)
        self._tasks.append(t)
        t.add_done_callback(self._child_done)
        return t

    def _child_done(self, t):
        if t.cancelled() or t.exception() is None:
            return
        self._errors.append(t.exception())
        if not self._aborting:
            self._shutdown(True)

    async def __aexit__(self, et, exc, tb):
        if et is not None and not self._aborting:
            self._shutdown(False)
        while True:
            try:
                await asyncio.gather(*self._tasks, return_exceptions=True)
                break
            except asyncio.CancelledError:
                if not self._aborting:
                    self._shutdown(False)
        self._done = True
        if self._timer is not None:
            self._timer.cancel()
        if self._we_cancelled:
            self._parent.uncancel()
        errors = list(self._errors)
        if et is not None and not issubclass(et, asyncio.CancelledError):
            errors.append(exc)
        if errors:
            raise ExceptionGroup('unhandled errors in a TaskGroup', errors)
        if self._timed_out:
            raise TimeoutError
        if et is not None and issubclass(et, asyncio.CancelledError):
            return self._we_cancelled
        return False
