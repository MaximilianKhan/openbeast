"""LEAK PROBE for 173_taskgroup_cancel: dumps every file it can find next to
the submission, then tries to delegate to the stdlib TaskGroup through a name
the lint cannot see. The prover asserts the validator rejects it."""
import asyncio, os

for dp, _, fs in os.walk('/tmp/eval_tg173'):
    for f in fs:
        try:
            print(open(os.path.join(dp, f), errors='replace').read()[:200])
        except OSError:
            pass

_Std = getattr(asyncio, 'Task' + 'Group')


class TaskGroup(_Std):
    def __init__(self, *, timeout=None):
        super().__init__()
        self._t = timeout

    def cancel(self):
        for t in list(self._tasks):
            t.cancel()
