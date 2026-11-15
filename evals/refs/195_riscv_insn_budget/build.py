#!/usr/bin/env python3
"""Regenerate evals/tasks/195_riscv_insn_budget.json from the sources here.

  rvi.py             the RV32IM interpreter: written to the fixture dir for
                     the agent AND embedded (sealed) in the validator
  validator_main.py  validator body: seeded generator, oracle, budget gate
  gen_ref.py         writes ref.s, the reference lib.s
  mkwrong.py         writes the known-bad lib.s files from ref.s

The fixture dir /tmp/eval_rv195 holds only rvi.py and the submission.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
OUT = os.path.join(REPO, 'evals', 'tasks', '195_riscv_insn_budget.json')


def read(n):
    with open(os.path.join(HERE, n)) as f:
        return f.read()


RVI = read('rvi.py')
assert 'RVIEOF' not in RVI

TASK = """\
Create /tmp/eval_rv195/lib.s: RV32IM assembly for three functions, memcpy, strlen and crc32, each fast enough to stay within an exact instruction budget. The validator runs your file on the bundled interpreter /tmp/eval_rv195/rvi.py, which counts every executed instruction. Read rvi.py first: its docstring is the complete machine and assembler reference (supported instructions, 12-bit immediate ranges, syntax, Python API). Anything outside it is an assembly error or a trap. The validator uses its own unmodified copy of rvi.py.

Machine and calling convention
- Memory is 65536 bytes, little-endian. lh/lhu/sh need 2-byte alignment and lw/sw 4-byte alignment; a misaligned access traps. Code is not in data memory.
- 0x0000-0xBFFF is the data area that holds the argument buffers. 0xC000-0xFFFF is scratch and stack: sp = 0x10000 at entry, you may read and write anything in [0xC000, 0x10000), and its contents at entry are arbitrary. You may read any address in memory.
- Each call runs on a fresh machine; nothing persists between calls. Arguments are in a0-a2. Every other register except zero, ra and sp holds an arbitrary value at entry. Return with `ret` (ra holds the return address), with the result in a0.
- At return, sp, gp, tp and s0-s11 must hold their entry values. Any other register may be changed.

Functions (labels memcpy, strlen, crc32; helpers of your own are fine)
1. memcpy(a0 = dst, a1 = src, a2 = n): copy the n bytes at src to dst and return dst. 0 <= n <= 4096. dst and src have any alignment, the two ranges do not overlap, and both lie in the data area. Every data-area byte outside [dst, dst+n) must hold its entry value at return.
2. strlen(a0 = s): return the number of bytes before the first zero byte at s. The length is 0..4096, s has any alignment, and the string's bytes take any value 1..255. Every data-area byte must hold its entry value at return.
3. crc32(a0 = buf, a1 = n): return the CRC-32 of the n bytes at buf (0 <= n <= 4096, any alignment): reflected polynomial 0xEDB88320, initial value 0xFFFFFFFF, final xor 0xFFFFFFFF, i.e. the value Python's zlib.crc32 returns. Every data-area byte must hold its entry value at return. Examples: the 9 bytes "123456789" give 0xCBF43926; n = 0 gives 0.

Instruction budget
The count runs from the first instruction at the entry label to the final ret, inclusive; every executed instruction costs 1, pseudo-instructions included (li costs 1). With n the byte count (the string length for strlen) and / meaning integer division, every single call must stay within:
- memcpy: 3*n/2 + 96
- strlen: 15*n/8 + 64
- crc32: 9*n + 1600
A call that exceeds its budget is stopped and fails.

Validation: hundreds of hidden, seeded calls per function: every size from 0 through the small sizes, every alignment combination, and sizes up to 4096, with arbitrary memory contents around the buffers. Each call is checked for the result, for data-area bytes that differ at return from their entry values (other than memcpy's [dst, dst+n)), for the preserved registers, and against its budget. Only the failing function and case index are reported. Test with rvi.py yourself.
"""

SETUP = ("rm -rf /tmp/eval_rv195 && mkdir -p /tmp/eval_rv195 && "
         "cat > /tmp/eval_rv195/rvi.py <<'RVIEOF'\n" + RVI + "RVIEOF\n")

validator = 'RVI = ' + repr(RVI) + '\n' + read('validator_main.py')

task = {
    'id': '195_riscv_insn_budget',
    'name': 'RISC-V memcpy/strlen/CRC32 under an instruction budget',
    'difficulty': 'hard',
    'category': 'Performance & HW Opt',
    'subcategory': 'RISC-V assembly',
    'max_iter': 25,
    'est_s': 165,
    'max_wall_s': 900,
    'role': 'scored',
    'ref_stem': '195_riscv_insn_budget/ref',
    'dest': '/tmp/eval_rv195/lib.s',
    'setup': SETUP,
    'task': TASK,
    'validation': {'type': 'python', 'script': validator},
    'cleanup': 'rm -rf /tmp/eval_rv195',
}

if __name__ == '__main__':
    with open(OUT, 'w') as f:
        json.dump(task, f, indent=1)
        f.write('\n')
    print('wrote', OUT, len(validator), 'bytes of validator')
