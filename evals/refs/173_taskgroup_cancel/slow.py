"""SLOW solution for 173_taskgroup_cancel: __aenter__ spins forever. The
validator must fail it inside its own CPU budget, not by the harness's
validator timeout. The prover asserts: solution timed out"""


class TaskGroup:
    def __init__(self, *, timeout=None):
        self._timeout = timeout

    async def __aenter__(self):
        while True:
            pass

    async def __aexit__(self, *a):
        return False

    def create_task(self, coro, *, name=None):
        coro.close()
        raise RuntimeError('not entered')

    def cancel(self):
        raise RuntimeError('not entered')
