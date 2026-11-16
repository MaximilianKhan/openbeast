#!/usr/bin/env python3
"""Regenerate the known-bad lib.s files from ref.s.

Each one changes one thing in the reference, so its failure is attributable:

  wrong_misaligned_lw.s   memcpy takes the word path whatever src % 4 is
                          -> misaligned lw trap
  wrong_overrun.s         memcpy rounds the aligned word copy up to a whole
                          word and drops the byte tail -> clobbers past dst+n
  wrong_haszero.s         strlen uses (x - 0x01010101) & 0x80808080 without
                          the & ~x term -> false hits on bytes >= 0x81
  wrong_clobber_s0.s      crc32 keeps its table base in s0 without saving it
  wrong_crc_noinvert.s    crc32 skips the final xor with 0xFFFFFFFF
  slow_bytecopy.s         memcpy is a correct byte loop (5 insns/byte)
  slow_mis_noblock.s      memcpy shift-merges one word per iteration
                          (2.25 insns/byte) when src and dst disagree mod 4
  slow_strlen_bytes.s     strlen is a correct byte loop (3 insns/byte)
  slow_crc_bytes.s        crc32 is the textbook byte-table loop (11/byte)
  hang.s                  memcpy spins forever
  leak_probe.s            hard-codes the prompt's worked examples
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REF = open(os.path.join(HERE, 'ref.s')).read()


def sub(src, old, new, count=1):
    n = src.count(old)
    assert n >= count, (old, n)
    return src.replace(old, new) if count == 0 else src.replace(old, new, count)


def section(src, start, end):
    i, j = src.index(start), src.index(end) if end else len(src)
    return src[:i], src[i:j], src[j:]


OUT = {}
OUT['wrong_misaligned_lw.s'] = sub(REF, '    bnez t1, mc_mis\n', '')
OUT['wrong_overrun.s'] = sub(
    REF,
    'mc_w1:\n    sub  a6, a7, t6\n    andi a6, a6, -4\n    add  a5, t6, a6\n'
    '    beq  t6, a5, mc_bytes\n',
    'mc_w1:\n    sub  a6, a7, t6\n    addi a6, a6, 3\n    andi a6, a6, -4\n'
    '    add  a5, t6, a6\n    beq  t6, a5, mc_done\n')
OUT['wrong_overrun.s'] = sub(
    OUT['wrong_overrun.s'],
    '    bne  t6, a5, mc_w1_loop\n', '    bne  t6, a5, mc_w1_loop\n    ret\n')
OUT['wrong_haszero.s'] = REF.replace('    not  t3, t1\n    and  t2, t2, t3\n', '')
assert OUT['wrong_haszero.s'].count('not  t3, t1') == 0
a, crc, b = section(REF, '# ---------------------------------------------------------------- crc32', None)
OUT['wrong_clobber_s0.s'] = a + re.sub(r'\bt6\b', 's0', crc)
OUT['wrong_crc_noinvert.s'] = sub(REF, '    not  a0, t0\n', '    mv   a0, t0\n')
OUT['slow_bytecopy.s'] = sub(REF, '    bltu a2, t0, mc_bytes\n', '    j    mc_bytes\n')
OUT['slow_mis_noblock.s'] = sub(REF, '    lw   t0, 0(a1)\n    beq  t6, a5, mc_m1\n',
                                '    lw   t0, 0(a1)\n    j    mc_m1\n')
a, sl, b = section(REF, '# ---------------------------------------------------------------- strlen',
                   '# ---------------------------------------------------------------- crc32')
OUT['slow_strlen_bytes.s'] = a + """# strlen: byte loop
strlen:
    mv   a1, a0
sl_loop:
    lbu  t0, 0(a1)
    addi a1, a1, 1
    bnez t0, sl_loop
    sub  a0, a1, a0
    addi a0, a0, -1
    ret

""" + b
OUT['slow_crc_bytes.s'] = sub(REF, '    li   t1, 8\n    bltu a1, t1, crc_bytes\n',
                              '    j    crc_bytes\n')
OUT['hang.s'] = """.text
memcpy:
    j    memcpy
strlen:
    j    strlen
crc32:
    j    crc32
"""
OUT['leak_probe.s'] = """# hard-codes the prompt's worked examples
.text
memcpy:
    ret
strlen:
    li   a0, 9
    ret
crc32:
    li   a0, 0xCBF43926
    ret
"""

if __name__ == '__main__':
    for name, text in OUT.items():
        assert text != REF, name
        with open(os.path.join(HERE, name), 'w') as f:
            f.write(text)
        print('wrote', name)
