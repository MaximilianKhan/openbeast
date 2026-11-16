"""rvi.py -- RV32IM interpreter with an exact retired-instruction counter.

Bundled with eval task 195. The validator uses its own pristine copy of this
file, so editing the copy in the fixture directory changes nothing.

Machine model
  * 32 integer registers, x0 hard-wired to zero. ABI names accepted.
  * Data memory: MEM_SIZE (65536) bytes, little-endian, addresses 0..0xFFFF.
    Any access that touches an address >= MEM_SIZE raises Trap.
    lh/lhu/sh need 2-byte alignment and lw/sw 4-byte alignment; a misaligned
    access raises Trap.
  * Code lives in its own address space (not in data memory): instruction i
    of the assembled file is at code address 4*i. Code cannot be read or
    written as data.
  * Every executed instruction, pseudo-instructions included, counts exactly
    1 step (cpu.steps). There are no cycle or cache effects.

Assembler syntax
  * One statement per line. '#' starts a comment.
  * Labels: `name:` at the start of a line (several may precede a statement).
    Names match [A-Za-z_.][A-Za-z0-9_.$]*. Numeric local labels (1:, 1b, 1f)
    are not supported. Branch and jump targets must be labels.
  * Directives .text .globl .global .type .size .align .p2align .option .file
    .section are accepted and ignored. Any other directive (.word, .byte,
    .data, ...) is an assembly error: there is no data section.
  * Immediates: Python int literals (decimal, 0x.., 0b.., 0o..), optional sign.

Instructions (12-bit immediates are signed, -2048..2047)
  R-type : add sub and or xor sll srl sra slt sltu
           mul mulh mulhsu mulhu div divu rem remu
  I-type : addi slti sltiu xori ori andi (12-bit imm); slli srli srai (0..31)
  lui    : lui rd, imm20            (0..0xFFFFF; rd = imm20 << 12)
  loads  : lb lh lw lbu lhu rd, off(rs1)   (12-bit off; "(rs1)" means 0)
  stores : sb sh sw rs2, off(rs1)
  branch : beq bne blt bge bltu bgeu rs1, rs2, label
  jumps  : jal label | jal rd, label | jalr rs1 | jalr rd, rs1, imm |
           jalr rd, imm(rs1)
  pseudo : li rd, imm32 (any value in -2**31..2**32-1, one instruction here)
           mv not neg seqz snez sltz sgtz nop j jr ret call
           beqz bnez blez bgez bltz bgtz rs, label
           bgt ble bgtu bleu rs1, rs2, label
  Not supported: auipc, ecall, ebreak, fence, CSRs, A/F/D/C/Zb* extensions.

Python API
  prog = assemble(source_text)            # raises AsmError
  cpu = prog.call('label', mem, regs={'a0': 1}, max_steps=10**6)
        # mem: bytearray of MEM_SIZE bytes, modified in place.
        # Registers not given in regs start at 0, except sp = MEM_SIZE and
        # ra = RET_ADDR. The call ends when control jumps to RET_ADDR
        # (e.g. `ret` with the entry ra). Raises Trap or StepLimit.
  cpu.x[10]   -> a0 after the call;  cpu.steps -> instructions executed
"""

import re
import sys

MEM_SIZE = 0x10000
RET_ADDR = 0xFFFFFFF0
M32 = 0xFFFFFFFF

ABI = ['zero', 'ra', 'sp', 'gp', 'tp', 't0', 't1', 't2', 's0', 's1',
       'a0', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7',
       's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9', 's10', 's11',
       't3', 't4', 't5', 't6']
REGS = {n: i for i, n in enumerate(ABI)}
REGS['fp'] = 8
for _i in range(32):
    REGS['x%d' % _i] = _i


class AsmError(Exception):
    pass


class Trap(Exception):
    pass


class StepLimit(Exception):
    pass


class _Ret(Exception):
    pass


_LABEL = re.compile(r'^([A-Za-z_.][A-Za-z0-9_.$]*)\s*:')
_OFFREG = re.compile(r'^(-?[+]?\w*)\s*\(\s*(\w+)\s*\)$')
_IGNORED = {'.text', '.globl', '.global', '.type', '.size', '.align',
            '.p2align', '.option', '.file', '.section'}

R_OPS = {'add', 'sub', 'and', 'or', 'xor', 'sll', 'srl', 'sra', 'slt',
         'sltu', 'mul', 'mulh', 'mulhsu', 'mulhu', 'div', 'divu', 'rem',
         'remu'}
I_OPS = {'addi', 'slti', 'sltiu', 'xori', 'ori', 'andi'}
SH_OPS = {'slli', 'srli', 'srai'}
LOADS = {'lb', 'lh', 'lw', 'lbu', 'lhu'}
STORES = {'sb', 'sh', 'sw'}
BR = {'beq', 'bne', 'blt', 'bge', 'bltu', 'bgeu'}
BR_SWAP = {'bgt': 'blt', 'ble': 'bge', 'bgtu': 'bltu', 'bleu': 'bgeu'}
BRZ = {'beqz', 'bnez', 'blez', 'bgez', 'bltz', 'bgtz'}


def _s(v):
    return v - 0x100000000 if v & 0x80000000 else v


def _div(a, b):
    a, b = _s(a), _s(b)
    if b == 0:
        return M32
    if a == -0x80000000 and b == -1:
        return 0x80000000
    q = abs(a) // abs(b)
    return (-q if (a < 0) != (b < 0) else q) & M32


def _rem(a, b):
    sa, sb = _s(a), _s(b)
    if sb == 0:
        return a
    if sa == -0x80000000 and sb == -1:
        return 0
    r = abs(sa) % abs(sb)
    return (-r if sa < 0 else r) & M32


_ALU = {
    'add': lambda a, b: (a + b) & M32,
    'sub': lambda a, b: (a - b) & M32,
    'and': lambda a, b: a & b,
    'or': lambda a, b: a | b,
    'xor': lambda a, b: a ^ b,
    'sll': lambda a, b: (a << (b & 31)) & M32,
    'srl': lambda a, b: a >> (b & 31),
    'sra': lambda a, b: (_s(a) >> (b & 31)) & M32,
    'slt': lambda a, b: 1 if _s(a) < _s(b) else 0,
    'sltu': lambda a, b: 1 if a < b else 0,
    'mul': lambda a, b: (a * b) & M32,
    'mulh': lambda a, b: ((_s(a) * _s(b)) >> 32) & M32,
    'mulhsu': lambda a, b: ((_s(a) * b) >> 32) & M32,
    'mulhu': lambda a, b: (a * b) >> 32,
    'div': _div,
    'divu': lambda a, b: M32 if b == 0 else a // b,
    'rem': _rem,
    'remu': lambda a, b: a if b == 0 else a % b,
}


def _reg(tok, ln):
    r = REGS.get(tok.strip().lower() if tok else tok)
    if r is None:
        raise AsmError('line %d: bad register %r' % (ln, tok))
    return r


def _imm(tok, ln, lo, hi):
    try:
        v = int(tok.strip(), 0)
    except (ValueError, AttributeError):
        raise AsmError('line %d: bad immediate %r' % (ln, tok))
    if not lo <= v <= hi:
        raise AsmError('line %d: immediate %d out of range [%d, %d]'
                       % (ln, v, lo, hi))
    return v


def _offreg(tok, ln):
    m = _OFFREG.match(tok.strip())
    if not m:
        raise AsmError('line %d: expected off(reg), got %r' % (ln, tok))
    off = m.group(1)
    off = _imm(off, ln, -2048, 2047) if off not in ('', '+', '-') else 0
    return off, _reg(m.group(2), ln)


def _parse(source):
    stmts, labels = [], {}
    for ln, raw in enumerate(source.split('\n'), 1):
        line = raw.split('#', 1)[0].strip()
        while True:
            m = _LABEL.match(line)
            if not m:
                break
            name = m.group(1)
            if name in labels:
                raise AsmError('line %d: duplicate label %r' % (ln, name))
            labels[name] = len(stmts)
            line = line[m.end():].strip()
        if not line:
            continue
        if line.startswith('.'):
            d = line.split()[0].lower()
            if d not in _IGNORED:
                raise AsmError('line %d: unsupported directive %r' % (ln, d))
            continue
        parts = line.split(None, 1)
        op = parts[0].lower()
        args = [a.strip() for a in parts[1].split(',')] if len(parts) > 1 else []
        stmts.append((ln, op, args))
    return stmts, labels


def _nargs(op, args, n, ln):
    if len(args) != n:
        raise AsmError('line %d: %s takes %d operands, got %d'
                       % (ln, op, n, len(args)))


class Program:
    """An assembled program; call() runs one function on a fresh CPU."""

    def __init__(self, source):
        stmts, self.labels = _parse(source)
        self.n = len(stmts)
        self._stmts = stmts
        dummy = bytearray(MEM_SIZE)
        self._build([0] * 32, dummy, None, None)  # operand checks up front

    def _lab(self, tok, ln):
        if tok not in self.labels:
            raise AsmError('line %d: unknown label %r' % (ln, tok))
        return self.labels[tok]

    def _build(self, x, mem, mem32, mem16):
        """Compile every statement into a closure f() -> next index."""
        code = []
        n = self.n
        MS = MEM_SIZE

        def target(addr, pc):
            if addr == RET_ADDR:
                raise _Ret()
            if addr & 3 or addr >= 4 * n:
                raise Trap('jump to invalid code address 0x%x at pc 0x%x'
                           % (addr, pc))
            return addr >> 2

        for i, (ln, op, a) in enumerate(self._stmts):
            nx = i + 1
            pc = 4 * i
            f = None
            if op in BR_SWAP:
                _nargs(op, a, 3, ln)
                op, a = BR_SWAP[op], [a[1], a[0], a[2]]
            if op in R_OPS:
                _nargs(op, a, 3, ln)
                rd, r1, r2 = _reg(a[0], ln), _reg(a[1], ln), _reg(a[2], ln)
                fn = _ALU[op]
                if op == 'add':
                    def f(rd=rd, r1=r1, r2=r2, nx=nx):
                        x[rd] = (x[r1] + x[r2]) & 0xFFFFFFFF
                        return nx
                elif op == 'sub':
                    def f(rd=rd, r1=r1, r2=r2, nx=nx):
                        x[rd] = (x[r1] - x[r2]) & 0xFFFFFFFF
                        return nx
                elif op == 'and':
                    def f(rd=rd, r1=r1, r2=r2, nx=nx):
                        x[rd] = x[r1] & x[r2]
                        return nx
                elif op == 'or':
                    def f(rd=rd, r1=r1, r2=r2, nx=nx):
                        x[rd] = x[r1] | x[r2]
                        return nx
                elif op == 'xor':
                    def f(rd=rd, r1=r1, r2=r2, nx=nx):
                        x[rd] = x[r1] ^ x[r2]
                        return nx
                elif op == 'srl':
                    def f(rd=rd, r1=r1, r2=r2, nx=nx):
                        x[rd] = x[r1] >> (x[r2] & 31)
                        return nx
                elif op == 'sll':
                    def f(rd=rd, r1=r1, r2=r2, nx=nx):
                        x[rd] = (x[r1] << (x[r2] & 31)) & 0xFFFFFFFF
                        return nx
                else:
                    def f(rd=rd, r1=r1, r2=r2, nx=nx, fn=fn):
                        x[rd] = fn(x[r1], x[r2])
                        return nx
                if rd == 0:
                    f = (lambda nx=nx: nx)
            elif op in I_OPS or op in SH_OPS:
                _nargs(op, a, 3, ln)
                rd, r1 = _reg(a[0], ln), _reg(a[1], ln)
                if op in SH_OPS:
                    im = _imm(a[2], ln, 0, 31)
                else:
                    im = _imm(a[2], ln, -2048, 2047)
                u = im & 0xFFFFFFFF
                if op == 'addi':
                    def f(rd=rd, r1=r1, u=u, nx=nx):
                        x[rd] = (x[r1] + u) & 0xFFFFFFFF
                        return nx
                elif op == 'andi':
                    def f(rd=rd, r1=r1, u=u, nx=nx):
                        x[rd] = x[r1] & u
                        return nx
                elif op == 'ori':
                    def f(rd=rd, r1=r1, u=u, nx=nx):
                        x[rd] = x[r1] | u
                        return nx
                elif op == 'xori':
                    def f(rd=rd, r1=r1, u=u, nx=nx):
                        x[rd] = x[r1] ^ u
                        return nx
                elif op == 'slli':
                    def f(rd=rd, r1=r1, im=im, nx=nx):
                        x[rd] = (x[r1] << im) & 0xFFFFFFFF
                        return nx
                elif op == 'srli':
                    def f(rd=rd, r1=r1, im=im, nx=nx):
                        x[rd] = x[r1] >> im
                        return nx
                else:
                    fn = {'srai': _ALU['sra'], 'slti': _ALU['slt'],
                          'sltiu': _ALU['sltu']}[op]
                    def f(rd=rd, r1=r1, u=u, nx=nx, fn=fn):
                        x[rd] = fn(x[r1], u)
                        return nx
                if rd == 0:
                    f = (lambda nx=nx: nx)
            elif op == 'lui':
                _nargs(op, a, 2, ln)
                rd, v = _reg(a[0], ln), _imm(a[1], ln, 0, 0xFFFFF) << 12
                def f(rd=rd, v=v, nx=nx):
                    x[rd] = v
                    return nx
                if rd == 0:
                    f = (lambda nx=nx: nx)
            elif op == 'li':
                _nargs(op, a, 2, ln)
                rd = _reg(a[0], ln)
                v = _imm(a[1], ln, -0x80000000, 0xFFFFFFFF) & 0xFFFFFFFF
                def f(rd=rd, v=v, nx=nx):
                    x[rd] = v
                    return nx
                if rd == 0:
                    f = (lambda nx=nx: nx)
            elif op in ('mv', 'not', 'neg', 'seqz', 'snez', 'sltz', 'sgtz'):
                _nargs(op, a, 2, ln)
                rd, r1 = _reg(a[0], ln), _reg(a[1], ln)
                fn = {'mv': lambda v: v,
                      'not': lambda v: v ^ 0xFFFFFFFF,
                      'neg': lambda v: (-v) & 0xFFFFFFFF,
                      'seqz': lambda v: 1 if v == 0 else 0,
                      'snez': lambda v: 1 if v else 0,
                      'sltz': lambda v: v >> 31,
                      'sgtz': lambda v: 1 if 0 < v < 0x80000000 else 0}[op]
                if op == 'mv':
                    def f(rd=rd, r1=r1, nx=nx):
                        x[rd] = x[r1]
                        return nx
                else:
                    def f(rd=rd, r1=r1, nx=nx, fn=fn):
                        x[rd] = fn(x[r1])
                        return nx
                if rd == 0:
                    f = (lambda nx=nx: nx)
            elif op == 'nop':
                _nargs(op, a, 0, ln)
                f = (lambda nx=nx: nx)
            elif op in LOADS:
                _nargs(op, a, 2, ln)
                rd = _reg(a[0], ln)
                off, rb = _offreg(a[1], ln)
                off &= 0xFFFFFFFF
                if op == 'lw':
                    def f(rd=rd, rb=rb, off=off, nx=nx, pc=pc):
                        ad = (x[rb] + off) & 0xFFFFFFFF
                        if ad & 3 or ad >= MS:
                            raise Trap('lw at 0x%x (pc 0x%x)' % (ad, pc))
                        if rd:
                            x[rd] = mem32[ad >> 2]
                        return nx
                elif op == 'lbu':
                    def f(rd=rd, rb=rb, off=off, nx=nx, pc=pc):
                        ad = (x[rb] + off) & 0xFFFFFFFF
                        if ad >= MS:
                            raise Trap('lbu at 0x%x (pc 0x%x)' % (ad, pc))
                        if rd:
                            x[rd] = mem[ad]
                        return nx
                elif op == 'lb':
                    def f(rd=rd, rb=rb, off=off, nx=nx, pc=pc):
                        ad = (x[rb] + off) & 0xFFFFFFFF
                        if ad >= MS:
                            raise Trap('lb at 0x%x (pc 0x%x)' % (ad, pc))
                        if rd:
                            v = mem[ad]
                            x[rd] = v | 0xFFFFFF00 if v & 0x80 else v
                        return nx
                elif op == 'lhu':
                    def f(rd=rd, rb=rb, off=off, nx=nx, pc=pc):
                        ad = (x[rb] + off) & 0xFFFFFFFF
                        if ad & 1 or ad >= MS:
                            raise Trap('lhu at 0x%x (pc 0x%x)' % (ad, pc))
                        if rd:
                            x[rd] = mem16[ad >> 1]
                        return nx
                else:  # lh
                    def f(rd=rd, rb=rb, off=off, nx=nx, pc=pc):
                        ad = (x[rb] + off) & 0xFFFFFFFF
                        if ad & 1 or ad >= MS:
                            raise Trap('lh at 0x%x (pc 0x%x)' % (ad, pc))
                        if rd:
                            v = mem16[ad >> 1]
                            x[rd] = v | 0xFFFF0000 if v & 0x8000 else v
                        return nx
            elif op in STORES:
                _nargs(op, a, 2, ln)
                rs = _reg(a[0], ln)
                off, rb = _offreg(a[1], ln)
                off &= 0xFFFFFFFF
                if op == 'sw':
                    def f(rs=rs, rb=rb, off=off, nx=nx, pc=pc):
                        ad = (x[rb] + off) & 0xFFFFFFFF
                        if ad & 3 or ad >= MS:
                            raise Trap('sw at 0x%x (pc 0x%x)' % (ad, pc))
                        mem32[ad >> 2] = x[rs]
                        return nx
                elif op == 'sb':
                    def f(rs=rs, rb=rb, off=off, nx=nx, pc=pc):
                        ad = (x[rb] + off) & 0xFFFFFFFF
                        if ad >= MS:
                            raise Trap('sb at 0x%x (pc 0x%x)' % (ad, pc))
                        mem[ad] = x[rs] & 0xFF
                        return nx
                else:
                    def f(rs=rs, rb=rb, off=off, nx=nx, pc=pc):
                        ad = (x[rb] + off) & 0xFFFFFFFF
                        if ad & 1 or ad >= MS:
                            raise Trap('sh at 0x%x (pc 0x%x)' % (ad, pc))
                        mem16[ad >> 1] = x[rs] & 0xFFFF
                        return nx
            elif op in BR:
                _nargs(op, a, 3, ln)
                r1, r2, t = _reg(a[0], ln), _reg(a[1], ln), self._lab(a[2], ln)
                if op == 'beq':
                    def f(r1=r1, r2=r2, t=t, nx=nx):
                        return t if x[r1] == x[r2] else nx
                elif op == 'bne':
                    def f(r1=r1, r2=r2, t=t, nx=nx):
                        return t if x[r1] != x[r2] else nx
                elif op == 'bltu':
                    def f(r1=r1, r2=r2, t=t, nx=nx):
                        return t if x[r1] < x[r2] else nx
                elif op == 'bgeu':
                    def f(r1=r1, r2=r2, t=t, nx=nx):
                        return t if x[r1] >= x[r2] else nx
                elif op == 'blt':
                    def f(r1=r1, r2=r2, t=t, nx=nx):
                        return t if (x[r1] ^ 0x80000000) < (x[r2] ^ 0x80000000) else nx
                else:
                    def f(r1=r1, r2=r2, t=t, nx=nx):
                        return t if (x[r1] ^ 0x80000000) >= (x[r2] ^ 0x80000000) else nx
            elif op in BRZ:
                _nargs(op, a, 2, ln)
                r1, t = _reg(a[0], ln), self._lab(a[1], ln)
                if op == 'beqz':
                    def f(r1=r1, t=t, nx=nx):
                        return nx if x[r1] else t
                elif op == 'bnez':
                    def f(r1=r1, t=t, nx=nx):
                        return t if x[r1] else nx
                else:
                    cond = {'blez': lambda v: v == 0 or v & 0x80000000,
                            'bgez': lambda v: not v & 0x80000000,
                            'bltz': lambda v: v & 0x80000000,
                            'bgtz': lambda v: 0 < v < 0x80000000}[op]
                    def f(r1=r1, t=t, nx=nx, cond=cond):
                        return t if cond(x[r1]) else nx
            elif op in ('j', 'jal', 'call'):
                if op == 'jal' and len(a) == 2:
                    rd, t = _reg(a[0], ln), self._lab(a[1], ln)
                else:
                    _nargs(op, a, 1, ln)
                    rd, t = (0 if op == 'j' else 1), self._lab(a[0], ln)
                ra = 4 * nx
                if rd:
                    def f(rd=rd, t=t, ra=ra):
                        x[rd] = ra
                        return t
                else:
                    f = (lambda t=t: t)
            elif op in ('jalr', 'jr', 'ret'):
                if op == 'ret':
                    _nargs(op, a, 0, ln)
                    rd, r1, off = 0, 1, 0
                elif op == 'jr' or len(a) == 1:
                    _nargs(op, a, 1, ln)
                    rd, r1, off = (0 if op == 'jr' else 1), _reg(a[0], ln), 0
                elif len(a) == 2:
                    rd = _reg(a[0], ln)
                    off, r1 = _offreg(a[1], ln)
                else:
                    _nargs(op, a, 3, ln)
                    rd, r1 = _reg(a[0], ln), _reg(a[1], ln)
                    off = _imm(a[2], ln, -2048, 2047)
                ra = 4 * nx
                def f(rd=rd, r1=r1, off=off, ra=ra, pc=pc):
                    t = ((x[r1] + off) & 0xFFFFFFFE) & 0xFFFFFFFF
                    if rd:
                        x[rd] = ra
                    return target(t, pc)
            else:
                raise AsmError('line %d: unsupported instruction %r' % (ln, op))
            code.append(f)
        return code

    def call(self, entry, mem, regs=None, max_steps=10_000_000):
        if entry not in self.labels:
            raise AsmError('entry label %r not found' % entry)
        if len(mem) != MEM_SIZE:
            raise ValueError('mem must be %d bytes' % MEM_SIZE)
        cpu = CPU()
        x = cpu.x
        x[1], x[2] = RET_ADDR, MEM_SIZE
        for k, v in (regs or {}).items():
            r = REGS[k] if isinstance(k, str) else k
            if r:
                x[r] = v & M32
        if sys.byteorder == 'little':
            mv = memoryview(mem)
            mem32, mem16 = mv.cast('I'), mv.cast('H')
        else:  # pragma: no cover - big-endian hosts
            mem32, mem16 = _LEView(mem, 4), _LEView(mem, 2)
        code = self._build(x, mem, mem32, mem16)
        pc = self.labels[entry]
        n = self.n
        steps = 0
        try:
            while True:
                if pc >= n:
                    raise Trap('fell off the end of the code at 0x%x' % (4 * pc))
                if steps >= max_steps:
                    cpu.steps = steps
                    raise StepLimit('more than %d instructions' % max_steps)
                steps += 1
                pc = code[pc]()
        except _Ret:
            pass
        finally:
            cpu.steps = steps
            x[0] = 0
            if sys.byteorder == 'little':
                mem32.release()
                mem16.release()
                mv.release()
        return cpu


class _LEView:  # pragma: no cover
    def __init__(self, mem, w):
        self.mem, self.w = mem, w

    def __getitem__(self, i):
        return int.from_bytes(self.mem[i * self.w:(i + 1) * self.w], 'little')

    def __setitem__(self, i, v):
        self.mem[i * self.w:(i + 1) * self.w] = v.to_bytes(self.w, 'little')

    def release(self):
        pass


class CPU:
    def __init__(self):
        self.x = [0] * 32
        self.steps = 0

    def reg(self, name):
        return self.x[REGS[name]]


def assemble(source):
    return Program(source)
