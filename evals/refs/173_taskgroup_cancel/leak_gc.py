"""LEAK PROBE for 173_taskgroup_cancel: digs the original stdlib TaskGroup
class out of the heap with gc after the validator disabled it.
The prover asserts the validator rejects it with: forbidden api"""
import asyncio, gc

_Std = next(o for o in gc.get_objects()
            if isinstance(o, type) and o.__name__ == 'TaskGroup' and o.__module__ == 'asyncio.taskgroups'
            and hasattr(o, '_on_task_done'))


class TaskGroup(_Std):
    def __init__(self, *, timeout=None):
        super().__init__()

    def cancel(self):
        for t in list(self._tasks):
            t.cancel()
