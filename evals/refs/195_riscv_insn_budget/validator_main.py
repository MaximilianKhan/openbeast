# Validator body for eval 195. build.py prepends `RVI = '<rvi.py source>'`.
# The interpreter is the validator's own sealed copy; the fixture's rvi.py is
# never imported. Hidden cases come from a seeded generator and are fed to the
# interpreter from memory; a failure names only the function and case index.
#
# Isolation (skills/eval-task-author "Validator isolation"): the submission is
# never executed as host code. lib.s is DATA, parsed by the sealed assembler
# and stepped by the sealed interpreter, whose only reachable state is the
# 32-register file and the case's bytearray; there is no ecall, no host I/O
# and no Python escape. So there is no solution process to hand to
# vlib.run_solution, and no wall or CPU limit to scale with
# OPENBEAST_TIMEOUT_SCALE: the gate is the exact retired-instruction count
# (max_steps), identical on every host and at every --scale. A pathological
# lib.s (megabytes of code) can only run out the harness's validator timeout,
# which is already scaled, and that is a fail.
import random
import sys
import types
import zlib

rvi = types.ModuleType('rvi_sealed')
exec(compile(RVI, 'rvi_sealed', 'exec'), rvi.__dict__)  # noqa: F821

LIB = '/tmp/eval_rv195/lib.s'
MEM = rvi.MEM_SIZE
DATA_END = 0xC000
CALLEE = ['sp', 'gp', 'tp', 's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7',
          's8', 's9', 's10', 's11']


def fail(msg):
    print(msg)
    sys.exit(1)


def budget_memcpy(n):
    return 3 * n // 2 + 96


def budget_strlen(n):
    return 15 * n // 8 + 64


def budget_crc32(n):
    return 9 * n + 1600


try:
    with open(LIB, encoding='utf-8') as f:
        SRC = f.read()
except OSError:
    fail('lib.s not found')
except UnicodeDecodeError:
    fail('lib.s is not UTF-8 text')
try:
    PROG = rvi.assemble(SRC)
except rvi.AsmError as e:
    fail('assembly error: %s' % e)
except Exception as e:  # malformed operands the assembler did not foresee
    fail('assembly error (%s)' % type(e).__name__)
for lab in ('memcpy', 'strlen', 'crc32'):
    if lab not in PROG.labels:
        fail('missing entry label %s' % lab)

RNG = random.Random(0x195C0DE)
TOTAL = {'memcpy': [0, 0], 'strlen': [0, 0], 'crc32': [0, 0]}


def run(name, idx, mem, args, budget):
    regs = {r: RNG.getrandbits(32) for r in range(3, 32)}
    for k, v in args.items():
        regs[rvi.REGS[k]] = v
    try:
        cpu = PROG.call(name, mem, regs=regs, max_steps=budget)
    except rvi.StepLimit:
        fail('%s case %d: over instruction budget' % (name, idx))
    except rvi.Trap as e:
        fail('%s case %d: trap (%s)' % (name, idx, str(e).split(' ')[0]))
    except Exception as e:  # interpreter bug or odd input: never a traceback
        fail('%s case %d: trap (internal %s)' % (name, idx, type(e).__name__))
    for r in CALLEE:
        i = rvi.REGS[r]
        want = rvi.MEM_SIZE if r == 'sp' else regs[i]
        if cpu.x[i] != want:
            fail('%s case %d: callee-saved register %s not preserved'
                 % (name, idx, r))
    TOTAL[name][0] += cpu.steps
    TOTAL[name][1] += budget
    return cpu.x[10]


def fresh():
    return bytearray(RNG.randbytes(MEM))


def place(n, align, avoid=None):
    """Random start in [64, DATA_END-64-n] with start % 4 == align."""
    for _ in range(1000):
        s = RNG.randrange(64, DATA_END - 64 - n) & ~3 | align
        if avoid is None or s + n + 8 <= avoid[0] or avoid[1] + 8 <= s:
            return s
    raise RuntimeError('placement failed')


# ------------------------------------------------------------------ memcpy
def memcpy_cases():
    cases = []
    for n in range(0, 21):
        for da in range(4):
            for sa in range(4):
                cases.append((n, da, sa))
    for _ in range(48):
        cases.append((RNG.randrange(21, 301), RNG.randrange(4), RNG.randrange(4)))
    for da in range(4):
        for sa in range(4):
            cases.append((RNG.choice([4096, RNG.randrange(1000, 4097)]), da, sa))
    return cases


for idx, (n, da, sa) in enumerate(memcpy_cases()):
    mem = fresh()
    src = place(n, sa)
    dst = place(n, da, (src, src + n))
    before = bytes(mem[:DATA_END])
    ret = run('memcpy', idx, mem, {'a0': dst, 'a1': src, 'a2': n},
              budget_memcpy(n))
    if mem[dst:dst + n] != before[src:src + n] or ret != dst:
        fail('memcpy case %d: wrong result' % idx)
    want = before[:dst] + before[src:src + n] + before[dst + n:]
    if bytes(mem[:DATA_END]) != want:
        fail('memcpy case %d: clobbered memory outside dst' % idx)


# ------------------------------------------------------------------ strlen
TRICKY = [0x01, 0x7F, 0x80, 0x81, 0xFE, 0xFF]


def strlen_cases():
    cases = []
    for n in range(0, 41):
        for a in range(4):
            cases.append((n, a))
    for _ in range(40):
        cases.append((RNG.randrange(41, 601), RNG.randrange(4)))
    for _ in range(12):
        cases.append((RNG.choice([4096, RNG.randrange(2000, 4097)]), RNG.randrange(4)))
    return cases


for idx, (n, a) in enumerate(strlen_cases()):
    mem = fresh()
    s = place(n + 1, a)
    if RNG.random() < 0.4:
        body = bytes(RNG.choice(TRICKY) for _ in range(n))
    else:
        body = bytes(RNG.randrange(1, 256) for _ in range(n))
    mem[s:s + n] = body
    mem[s + n] = 0
    if RNG.random() < 0.5:
        mem[s - 3:s] = bytes(3)          # zero bytes just before the start
    if RNG.random() < 0.5:
        mem[s + n + 1:s + n + 8] = bytes(RNG.choice([0, 1, 0x80]) for _ in range(7))
    before = bytes(mem[:DATA_END])
    ret = run('strlen', idx, mem, {'a0': s}, budget_strlen(n))
    if ret != n:
        fail('strlen case %d: wrong result' % idx)
    if bytes(mem[:DATA_END]) != before:
        fail('strlen case %d: clobbered memory' % idx)


# ------------------------------------------------------------------ crc32
def crc_cases():
    cases = []
    for n in range(0, 17):
        for a in range(4):
            cases.append((n, a))
    for _ in range(20):
        cases.append((RNG.randrange(17, 501), RNG.randrange(4)))
    for _ in range(8):
        cases.append((RNG.choice([4096, RNG.randrange(2000, 4097)]), RNG.randrange(4)))
    return cases


for idx, (n, a) in enumerate(crc_cases()):
    mem = fresh()
    b = place(n, a)
    if idx % 5 == 1:
        mem[b:b + n] = bytes(RNG.choice([0, 0xFF]) for _ in range(n))
    before = bytes(mem[:DATA_END])
    ret = run('crc32', idx, mem, {'a0': b, 'a1': n}, budget_crc32(n))
    if ret != zlib.crc32(before[b:b + n]):
        fail('crc32 case %d: wrong result' % idx)
    if bytes(mem[:DATA_END]) != before:
        fail('crc32 case %d: clobbered memory' % idx)

print('OK  ' + '  '.join('%s %d/%d insns (%.0f%% of budget)'
                         % (k, v[0], v[1], 100.0 * v[0] / v[1])
                         for k, v in TOTAL.items()))
