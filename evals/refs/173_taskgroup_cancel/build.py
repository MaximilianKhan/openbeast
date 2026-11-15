#!/usr/bin/env python3
"""Regenerate evals/tasks/173_taskgroup_cancel.json from the sources here.

  harness.py        scenario generator + interpreter + virtual-clock loop
  ref.py            reference TaskGroup (also the validator's oracle)
  validator_main.py the validator body (lint, sandboxed run, compare)

The oracle and the generator live only inside the validator script; the
agent-visible fixture dir /tmp/eval_tg173 holds nothing but the submission.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
OUT = os.path.join(REPO, 'evals', 'tasks', '173_taskgroup_cancel.json')


def read(n):
    with open(os.path.join(HERE, n)) as f:
        return f.read()


TASK = """\
Create /tmp/eval_tg173/tg.py (Python >= 3.11, standard library only) defining class TaskGroup: a structured-concurrency task group for asyncio with a deadline and an explicit cancel(). Do not use asyncio.TaskGroup or the asyncio.taskgroups module (the validator disables them and rejects dynamic import, exec/eval and gc); asyncio.timeout and every Task/Future/loop API are allowed.

API
- TaskGroup(*, timeout=None): timeout is None or a positive number of seconds of event-loop time (loop.time()), counted from __aenter__.
- `async with TaskGroup(...) as g:` -- __aenter__ returns the group itself. The task that enters it is the group's parent. Entering the same group object a second time (even after it finished) raises RuntimeError.
- g.create_task(coro, *, name=None) -> asyncio.Task: runs coro as a child task with that name and returns the Task. It raises RuntimeError, after calling coro.close() so the coroutine never runs, if the group was never entered, is shutting down, or is finished. Finished = the exit has completed, or the exit has begun and no child is left. While the exit is waiting for children, a child may still add siblings (unless shutting down).
- g.cancel(): RuntimeError if never entered; does nothing if the group is shutting down or finished; otherwise triggers an internal shutdown.

Shutdown happens at most once, triggered by the first of:
 (1) a child finishing with an exception (a child that ends cancelled, by anyone, is not an error and triggers nothing);
 (2) the timeout expiring before the group is finished;
 (3) g.cancel();
 (4) the body of the `async with` exiting with an exception, including CancelledError;
 (5) the parent being cancelled while the exit waits for children.
Shutting down cancels every unfinished child, once (the group never cancels a child twice). For (1)-(3), internal shutdowns, the group also cancels its parent exactly once, so the body is interrupted at its current await, or the exit's wait is if the body already finished. The group must undo that request with Task.uncancel(): its own cancellation must never change the parent's cancelling() count, stay pending, or be seen outside the group (for example by an enclosing asyncio.timeout() or by code after the `async with`). Groups nest, in bodies and inside children; each group absorbs only the cancellations it requested itself.

Exit
The exit always waits until every child has finished, however many times the parent is cancelled meanwhile, and completes as soon as the last child finishes (in the same loop time; no polling). When it completes, the group leaves no callback scheduled on the loop. Child exceptions are collected in completion order, including ones raised after shutdown began (e.g. by a child that raises while handling its cancellation). The first matching rule decides the outcome:
 A. Let E = the collected child exceptions, then the body's exception if it is not a CancelledError. If E is non-empty, raise ExceptionGroup('unhandled errors in a TaskGroup', E). If the parent still has a cancellation request from outside the group (parent.cancelling() > 0 once the group's own request is undone), that cancellation stays pending: the parent's next await raises CancelledError, and its cancelling() count is unchanged.
 B. Else, if a CancelledError reached the group (out of the body, or interrupting the exit's wait) and parent.cancelling() > 0 once the group's own request is undone: re-raise CancelledError.
 C. Else, if the shutdown was caused by the timeout: raise TimeoutError.
 D. Else exit normally; a CancelledError caused only by the group's own request is swallowed.
Children raise only Exception subclasses.

Example: in `async with TaskGroup() as g: g.create_task(crash()); g.create_task(slow()); await asyncio.sleep(5)`, where crash() sleeps 1 s then raises ValueError('x') and slow() sleeps 10 s: at loop time 1, slow() and the body are cancelled, and the `async with` raises ExceptionGroup('unhandled errors in a TaskGroup', [ValueError('x')]) at loop time 1. Afterwards the parent's cancelling() is 0.

Validation: a few hundred hidden, seeded scenarios. They use nested groups, timeouts, cancel(), external Task.cancel() of parents and children, asyncio.timeout() around groups, late create_task calls, and children that clean up, raise, or swallow on cancellation. They run on an event loop with a virtual clock: loop.time() jumps straight to the next scheduled timer whenever every task is blocked, so busy-waiting never advances time. Checked exactly: every event's (loop time, task name), the outcome of every group exit with the parent's cancelling() right after it, and that afterwards no task is left pending and no callback is left scheduled. Only a failing case's index is reported. Write your own tests.
"""

validator = ('HARNESS = ' + repr(read('harness.py')) + '\n'
             + 'ORACLE = ' + repr(read('ref.py')) + '\n'
             + read('validator_main.py'))

task = {
    'id': '173_taskgroup_cancel',
    'name': 'asyncio TaskGroup structured cancellation',
    'difficulty': 'hard',
    'category': 'Concurrency & Systems',
    'subcategory': 'Async patterns',
    'max_iter': 25,
    'est_s': 165,
    'max_wall_s': 900,
    'role': 'scored',
    'ref_stem': '173_taskgroup_cancel/ref',
    'dest': '/tmp/eval_tg173/tg.py',
    'setup': 'rm -rf /tmp/eval_tg173 && mkdir -p /tmp/eval_tg173',
    'task': TASK,
    'validation': {'type': 'python', 'script': validator},
    'cleanup': 'rm -rf /tmp/eval_tg173',
}

with open(OUT, 'w') as f:
    json.dump(task, f, indent=1)
    f.write('\n')
print('wrote', OUT, len(validator), 'bytes of validator')
