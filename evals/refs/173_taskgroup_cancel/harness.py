# Scenario interpreter + virtual-clock event loop for 173_taskgroup_cancel.
# Embedded verbatim into the validator by build.py; exec'd once in the
# validator process (with the oracle TaskGroup) and once in a sandbox
# subprocess (with the submission's TaskGroup). Defines run_all(scenarios, TG).
import asyncio, selectors, inspect, random


class _Deadlock(Exception):
    pass


class _Runaway(Exception):
    pass


class _VSel(selectors.DefaultSelector):
    """Never blocks: when the loop would sleep, jump the clock to the next timer."""

    def __init__(self, loop_ref):
        super().__init__()
        self._lr = loop_ref

    def select(self, timeout=None):
        ev = super().select(0)
        if ev:
            return ev
        lp = self._lr[0]
        lp._vsteps += 1
        if lp._vsteps > 20000:
            raise _Runaway()
        if timeout is None:
            raise _Deadlock()
        if timeout > 0 and lp._scheduled:
            w = lp._scheduled[0]._when
            if w > lp._vnow:
                lp._vnow = w
        return []


class VLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        self._vnow = 0.0
        self._vsteps = 0
        ref = [None]
        super().__init__(_VSel(ref))
        ref[0] = self
        # all scenario times are integers; a sub-unit resolution keeps the
        # loop's "due within resolution" test exact at large magnitudes
        self._clock_resolution = 0.5

    def time(self):
        return self._vnow


EXC = {'ValueError': ValueError, 'KeyError': KeyError, 'OSError': OSError,
       'ZeroDivisionError': ZeroDivisionError, 'LookupError': LookupError}


def _mark(e, **kw):
    for k, v in kw.items():
        try:
            setattr(e, k, v)
        except Exception:
            pass
    return e


def canon(e):
    if isinstance(e, BaseExceptionGroup):
        # Child exceptions must come in completion order, the body's last.
        # Children cancelled in the same tick may finish in any order, so
        # members are compared sorted, plus two order flags: the body's
        # exception is last, and child raise times never decrease.
        xs = list(e.exceptions)
        body = [i for i, x in enumerate(xs) if getattr(x, '_h_body', False)]
        kids = [x for x in xs if not getattr(x, '_h_body', False)]
        ts = [getattr(x, '_h_t', None) for x in kids]
        flags = ''
        if body and body != [len(xs) - 1]:
            flags += '!body-not-last'
        if any(a is not None and b is not None and b < a for a, b in zip(ts, ts[1:])):
            flags += '!not-completion-order'
        parts = sorted(canon(x) for x in kids) + ['body:' + canon(xs[i]) for i in body]
        return '%s<%s>[%s]%s' % (type(e).__name__, e.message, ','.join(parts), flags)
    if isinstance(e, asyncio.CancelledError):
        return 'CancelledError'
    if isinstance(e, TimeoutError):
        return 'TimeoutError'
    return '%s(%s)' % (type(e).__name__, ','.join(map(str, e.args)))


class _Run:
    def __init__(self, TG):
        self.TG = TG
        self.log_ = []
        self.seq = {}
        self.reg = {}

    def log(self, ev):
        t = asyncio.current_task()
        n = t.get_name() if t is not None else '?'
        k = self.seq.get(n, 0)
        self.seq[n] = k + 1
        self.log_.append((asyncio.get_running_loop().time(), n, k, ev))

    async def task(self, spec, grp):
        me = asyncio.current_task()
        self.reg[me.get_name()] = me
        try:
            await self.steps(spec['steps'], grp)
        except asyncio.CancelledError:
            self.log('cancelled c=%d' % me.cancelling())
            oc = spec['on_cancel']
            if oc == 'reraise':
                raise
            if oc[0] == 'sleep':
                try:
                    await asyncio.sleep(oc[1])
                except asyncio.CancelledError:
                    self.log('cancelled-again')
                    raise
                self.log('cleanup-done')
                raise
            if oc[0] == 'raise':
                self.log('raise-in-cancel')
                raise _mark(EXC[oc[1]](oc[2]), _h_t=asyncio.get_running_loop().time()) from None
            await asyncio.sleep(oc[1])               # 'swallow'
            self.log('swallowed')
            return 'swallowed'
        except Exception as e:
            _mark(e, _h_t=asyncio.get_running_loop().time())
            self.log('fail ' + canon(e))
            raise
        self.log('finished')
        return 'ok'

    async def steps(self, steps, grp):
        for st in steps:
            op = st[0]
            if op == 'sleep':
                await asyncio.sleep(st[1])
                self.log('woke')
            elif op == 'raise':
                self.log('raise')
                raise EXC[st[1]](st[2])
            elif op == 'group':
                await self.group(st[1])
            elif op == 'cancel_group':
                self.log('cancel-group')
                grp.cancel()
            elif op == 'spawn':
                c = self.task(st[1], grp)
                try:
                    grp.create_task(c, name=st[1]['name'])
                    self.log('spawned')
                except RuntimeError:
                    self.log('spawn-refused ' + inspect.getcoroutinestate(c))
                    c.close()
            elif op == 'cancel_task':
                t = self.reg.get(st[1])
                if t is None or t.done():
                    self.log('cancel-miss')
                else:
                    t.cancel()
                    self.log('cancel-sent')
            elif op == 'atimeout':
                cm = asyncio.timeout(st[1])
                try:
                    async with cm:
                        await self.steps(st[2], grp)
                except TimeoutError:
                    self.log('atimeout-out expired=%s c=%d' % (
                        cm.expired(), asyncio.current_task().cancelling()))
            elif op == 'guard':
                try:
                    await self.steps(st[1], grp)
                except asyncio.CancelledError:
                    self.log('guard-caught')
                    await asyncio.sleep(st[2])
                    self.log('guard-resumed')

    async def group(self, G):
        me = asyncio.current_task()
        g = self.TG(timeout=G['timeout']) if G['timeout'] is not None else self.TG()
        try:
            async with g as gg:
                if gg is not g:
                    self.log('enter-returned-other')
                for c in G['children']:
                    g.create_task(self.task(c, g), name=c['name'])
                try:
                    await self.steps(G['body'], g)
                except Exception as e:
                    _mark(e, _h_body=True)
                    raise
        except asyncio.CancelledError:
            self.log('exit CancelledError c=%d' % me.cancelling())
            raise
        except Exception as e:
            self.log('exit %s c=%d' % (canon(e), me.cancelling()))
            if G['catch'] == 'none':
                raise
        else:
            self.log('exit ok c=%d' % me.cancelling())
        if G['stale']:
            async def _late():
                await asyncio.sleep(1)
            c = _late()
            try:
                g.create_task(c, name='late')
                self.log('stale-accepted')
            except RuntimeError:
                self.log('stale-refused ' + inspect.getcoroutinestate(c))
                c.close()

    async def killer(self, d, name):
        await asyncio.sleep(d)
        t = self.reg.get(name)
        if t is None or t.done():
            self.log('kill-miss')
        else:
            t.cancel()
            self.log('kill-sent')

    async def driver(self, S):
        lp = asyncio.get_running_loop()
        mt = lp.create_task(self.task(S['main'], None), name='main')
        ks = [lp.create_task(self.killer(d, n), name='k%d' % i)
              for i, (d, n) in enumerate(S['killers'])]
        await asyncio.wait([mt])
        for k in ks:
            k.cancel()
        if ks:
            await asyncio.wait(ks)
        if mt.cancelled():
            return 'main cancelled'
        if mt.exception() is not None:
            return 'main ' + canon(mt.exception())
        return 'main ' + str(mt.result())


async def _misuse(TG):
    out = []

    async def nap(d):
        await asyncio.sleep(d)

    async def boom(d):
        await asyncio.sleep(d)
        raise ValueError('boom')

    g = TG()
    c = nap(1)
    try:
        g.create_task(c)
        out.append('pre-create accepted')
    except RuntimeError:
        out.append('pre-create RT ' + inspect.getcoroutinestate(c))
    c.close()
    try:
        g.cancel()
        out.append('pre-cancel accepted')
    except RuntimeError:
        out.append('pre-cancel RT')
    async with g as x:
        out.append('enter-self %s' % (x is g))
        t = g.create_task(nap(3), name='zz')
        out.append('task %s %s' % (isinstance(t, asyncio.Task), t.get_name()))
    out.append('after t=%s done=%s' % (asyncio.get_running_loop().time(), t.done()))
    try:
        async with g:
            pass
        out.append('reenter accepted')
    except RuntimeError:
        out.append('reenter RT')
    c = nap(1)
    try:
        g.create_task(c)
        out.append('post-create accepted')
    except RuntimeError:
        out.append('post-create RT ' + inspect.getcoroutinestate(c))
    c.close()
    try:
        g.cancel()
        out.append('post-cancel ok')
    except Exception as e:
        out.append('post-cancel ' + type(e).__name__)
    g2 = TG()
    try:
        async with g2:
            g2.create_task(boom(2))
            try:
                await asyncio.sleep(50)
            except asyncio.CancelledError:
                c = nap(1)
                try:
                    g2.create_task(c)
                    out.append('shutdown-create accepted')
                except RuntimeError:
                    out.append('shutdown-create RT ' + inspect.getcoroutinestate(c))
                c.close()
                raise
    except Exception as e:
        out.append('g2 ' + canon(e))
    out.append('c=%d t=%s' % (asyncio.current_task().cancelling(), asyncio.get_running_loop().time()))
    async with TG(timeout=100) as g3:              # finishes well before the deadline
        g3.create_task(nap(4))
    out.append('g3 ok t=%s' % asyncio.get_running_loop().time())
    async with TG(timeout=7):                      # empty body, no children
        pass
    out.append('g4 ok')
    try:
        async with TG(timeout=5) as g5:
            g5.create_task(nap(1000))
            await asyncio.sleep(1000)
    except TimeoutError:
        out.append('g5 timeout t=%s c=%d' % (asyncio.get_running_loop().time(),
                                             asyncio.current_task().cancelling()))
    async with TG() as g6:
        g6.create_task(nap(9))
        g6.cancel()
        try:
            await asyncio.sleep(1000)
        except asyncio.CancelledError:
            out.append('g6 body cancelled t=%s' % asyncio.get_running_loop().time())
            raise
    out.append('g6 exit ok c=%d' % asyncio.current_task().cancelling())
    g7 = TG(timeout=5)                             # the deadline counts from __aenter__,
    await asyncio.sleep(3)                         # not from the constructor
    try:
        async with g7:
            g7.create_task(nap(4))
        out.append('g7 ok t=%s' % asyncio.get_running_loop().time())
    except BaseException as e:
        out.append('g7 %s t=%s' % (canon(e), asyncio.get_running_loop().time()))
    try:
        async with TG(timeout=5) as g8:            # ...and it still fires once entered
            await asyncio.sleep(3)
            g8.create_task(nap(4))
    except BaseException as e:
        out.append('g8 %s t=%s' % (canon(e), asyncio.get_running_loop().time()))
    else:
        out.append('g8 ok')
    try:
        async with TG() as g9:                     # completion order, body's error last
            g9.create_task(_raise_on_cancel(2, OSError('o3')))
            g9.create_task(_raise_later(1, KeyError('k1')))
            try:
                await asyncio.sleep(50)
            except asyncio.CancelledError:
                raise ZeroDivisionError('body') from None
    except BaseException as e:
        out.append('g9 t=%s %s order=%s' % (asyncio.get_running_loop().time(), canon(e),
                                            [canon(x) for x in getattr(e, 'exceptions', ())]))
    return out


async def _raise_on_cancel(d, e):
    try:
        await asyncio.sleep(1000)
    except asyncio.CancelledError:
        await asyncio.sleep(d)
        raise e from None


async def _raise_later(d, e):
    await asyncio.sleep(d)
    raise e


_all_tasks = asyncio.all_tasks                     # bound before any submission loads
_task_done = asyncio.Task.done                      # a Task subclass cannot override these
_handle_cancelled = asyncio.TimerHandle.cancelled


def _watched():
    import asyncio.base_events as be, asyncio.events as ev, asyncio.futures as fu
    import asyncio.selector_events as se, asyncio.tasks as tk, asyncio.timeouts as to
    import asyncio.coroutines as co
    return [asyncio, be, ev, fu, se, tk, to, co, selectors, inspect,
            VLoop, _VSel, asyncio.SelectorEventLoop, se.BaseSelectorEventLoop,
            be.BaseEventLoop, ev.AbstractEventLoop, ev.Handle, ev.TimerHandle,
            to.Timeout, selectors.DefaultSelector, selectors.BaseSelector,
            selectors._BaseSelectorImpl, _Run]


def _fingerprint():
    """Identity of every binding in the loop / asyncio modules and classes the
    scenario interpreter relies on. A submission that rebinds any of them (to
    hide pending tasks, timers or handler calls) changes it."""
    fp = []
    for o in _watched():
        fp.append((id(o), tuple(sorted((k, id(v)) for k, v in vars(o).items()
                                       if not (k.startswith('__') and k.endswith('__'))))))
    return fp


def _tampered(lp, st):
    if _fingerprint() != _BASE:
        return True
    if lp._scheduled is not st[0] or lp._ready is not st[1] or lp._exception_handler is not st[2]:
        return True
    cls = type(lp)
    return any(callable(getattr(cls, k, None)) for k in vars(lp))


def _one(fn, TG):
    lp = VLoop()
    errs = []
    eh = lambda l, ctx: errs.append(1)
    lp.set_exception_handler(eh)
    st = (lp._scheduled, lp._ready, eh)
    try:
        try:
            res = lp.run_until_complete(fn())
        except (_Deadlock, _Runaway) as e:
            return {'err': type(e).__name__}
        except BaseException as e:
            return {'err': 'crash ' + type(e).__name__}
        pend = sum(1 for t in _all_tasks(lp) if not _task_done(t))
        sched = sum(1 for h in st[0] if not _handle_cancelled(h))
        if _tampered(lp, st):
            return {'err': 'tampered with the event loop or asyncio'}
        return {'res': res, 'pend': pend, 'sched': sched, 'eh': len(errs)}
    finally:
        try:
            for t in _all_tasks(lp):
                t._log_destroy_pending = False
            lp.close()
        except Exception:
            pass


def run_all(scenarios, TG):
    out = []
    r = _one(lambda: _misuse(TG), TG)
    out.append(r)
    for S in scenarios:
        if 'err' in r:
            break                                  # no point running the rest
        R = _Run(TG)
        r = _one(lambda: R.driver(S), TG)
        r['trace'] = sorted(R.log_)
        out.append(r)
    return out


async def _warm():
    async with asyncio.timeout(1):
        await asyncio.sleep(0.5)
    t = asyncio.get_running_loop().create_task(asyncio.sleep(2))
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass


_BASE = None
_one(_warm, None)                                  # let lazy module state settle
_BASE = _fingerprint()


N_TAIL = 24


def gen_scenarios(seed, n):
    """n random scenarios, then N_TAIL cancel-tail ones (_gen_cancel_tail)."""
    out = []
    rng = random.Random(seed)
    while len(out) < n:
        try:
            out.append(_gen(rng))
        except IndexError:
            pass                                   # duration pool exhausted: draw again
    trng = random.Random(seed ^ 0xCA7A11)          # own stream: the n above are unchanged
    out += [_gen_cancel_tail(trng, i) for i in range(N_TAIL)]
    return out


def _gen_cancel_tail(rng, i):
    # The random generator always puts a sleep after cancel_group, so the
    # group's own parent.cancel() is always delivered inside the body. Here
    # the body ENDS right at g.cancel() (or raises right after it), with no
    # child left to wait for: nothing in the group ever awaits, and the
    # request must still be absorbed -- never seen by the sleep after the
    # `async with` (main 'woke' vs 'main cancelled'), nor by an enclosing
    # asyncio.timeout(). CPython <= 3.12's uncancel() leaves such a request
    # pending; 3.13's clears it.
    pool = [float(1 << k) for k in range(46)]
    rng.shuffle(pool)
    dur = pool.pop
    kind = i % 4
    a, b = sorted([dur(), dur()])
    body = [['sleep', b], ['cancel_group']]
    children = []
    if kind == 1:
        body.append(['raise', rng.choice(sorted(EXC)), 'm%d' % i])
    elif kind == 2:                                # a child that finished before
        children = [{'name': 't0', 'steps': [['sleep', a]], 'on_cancel': 'reraise'}]
    G = {'timeout': dur() if rng.random() < .5 else None, 'children': children, 'body': body,
         'catch': 'errors' if kind == 1 or rng.random() < .5 else 'none', 'stale': rng.random() < .3}
    if G['timeout'] is not None and G['timeout'] < b:
        G['timeout'] = b + dur()                   # must not fire before the cancel
    group = ['group', G]
    if kind == 3:                                  # an enclosing asyncio.timeout()
        group = ['atimeout', b + dur(), [['group', G], ['sleep', dur()]]]
    main = {'name': 'main', 'on_cancel': 'reraise',
            'steps': [['sleep', dur()], group, ['sleep', dur()]]}
    return {'main': main, 'killers': []}


def _gen(rng):
    # Every duration is a distinct power of two, so no two timers can ever
    # fire at the same loop time: event order is fixed by the clock alone,
    # never by how many loop iterations an implementation spends.
    pool = [float(1 << k) for k in range(46)]
    rng.shuffle(pool)
    names = []
    dur = pool.pop

    def exc():
        return rng.choice(sorted(EXC))

    def task(depth):
        name = 't%d' % len(names)
        names.append(name)
        st = steps(depth, True)
        r = rng.random()
        oc = ('reraise' if r < .40 else ['sleep', dur()] if r < .65 else
              ['raise', exc(), name] if r < .85 else ['swallow', dur()])
        return {'name': name, 'steps': st, 'on_cancel': oc}

    def steps(depth, in_grp):
        out = [['sleep', dur()]]
        for _ in range(rng.randint(0, 3)):
            r = rng.random()
            if r < .22:
                out.append(['sleep', dur()])
            elif r < .36:
                out.append(['raise', exc(), 'm%d' % rng.randrange(100)])
                break
            elif r < .54 and depth > 0:
                out.append(['group', group(depth - 1)])
            elif r < .63 and in_grp:
                out += [['cancel_group'], ['sleep', dur()]]
            elif r < .70 and in_grp:
                out += [['spawn', task(0)], ['sleep', dur()]]
            elif r < .78 and names:
                out += [['cancel_task', rng.choice(names + ['main'])], ['sleep', dur()]]
            elif r < .88 and depth > 0:
                out.append(['atimeout', dur(), [['sleep', dur()], ['group', group(depth - 1)]]
                            if rng.random() < .6 else steps(depth - 1, in_grp)])
            else:
                out.append(['guard', steps(max(depth - 1, 0), in_grp), dur()])
        return out

    def group(depth):
        return {'timeout': dur() if rng.random() < .35 else None,
                'children': [task(depth) for _ in range(rng.choice([0, 1, 1, 2, 2, 3]))],
                'body': steps(depth, True),
                'catch': 'errors' if rng.random() < .4 else 'none',
                'stale': rng.random() < .2}

    depth = rng.choice([1, 2, 2, 3])
    main = {'name': 'main', 'on_cancel': rng.choice(['reraise', 'reraise', ['swallow', dur()]]),
            'steps': [['sleep', dur()], ['group', group(depth)]]}
    if rng.random() < .3:
        main['steps'] = [['sleep', dur()], ['atimeout', dur(), [['group', group(depth)]]]]
    if rng.random() < .3:
        main['steps'].append(['group', group(1)])
    killers = [[dur(), rng.choice(names + ['main', 'main'])]
               for _ in range(rng.choice([0, 1, 1, 2, 3]))]
    if len(names) > 14:
        raise IndexError
    return {'main': main, 'killers': killers}
