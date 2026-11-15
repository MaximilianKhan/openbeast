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


def canon(e):
    if isinstance(e, BaseExceptionGroup):
        return '%s<%s>[%s]' % (type(e).__name__, e.message,
                               ','.join(sorted(canon(x) for x in e.exceptions)))
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
                raise EXC[oc[1]](oc[2]) from None
            await asyncio.sleep(oc[1])               # 'swallow'
            self.log('swallowed')
            return 'swallowed'
        except Exception as e:
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
                await self.steps(G['body'], g)
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
    return out


def _one(fn, TG):
    lp = VLoop()
    errs = []
    lp.set_exception_handler(lambda l, ctx: errs.append(1))
    try:
        try:
            res = lp.run_until_complete(fn())
        except (_Deadlock, _Runaway) as e:
            return {'err': type(e).__name__}
        except BaseException as e:
            return {'err': 'crash ' + type(e).__name__}
        pend = sum(1 for t in asyncio.all_tasks(lp) if not t.done())
        sched = sum(1 for h in lp._scheduled if not h.cancelled())
        return {'res': res, 'pend': pend, 'sched': sched, 'eh': len(errs)}
    finally:
        try:
            for t in asyncio.all_tasks(lp):
                t._log_destroy_pending = False
            lp.close()
        except Exception:
            pass


def run_all(scenarios, TG):
    out = []
    r = _one(lambda: _misuse(TG), TG)
    out.append(r)
    for S in scenarios:
        R = _Run(TG)
        r = _one(lambda: R.driver(S), TG)
        r['trace'] = sorted(R.log_)
        out.append(r)
        if 'err' in r:
            break                                  # no point running the rest
    return out


def gen_scenarios(seed, n):
    out = []
    rng = random.Random(seed)
    while len(out) < n:
        try:
            out.append(_gen(rng))
        except IndexError:
            pass                                   # duration pool exhausted: draw again
    return out


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
