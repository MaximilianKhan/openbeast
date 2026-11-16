#!/usr/bin/env python3
"""Regenerate ref.s, the reference lib.s for eval 195.

The CRC-32 byte table is emitted as 256 li/sw pairs (there is no data
section), then crc32 consumes aligned words: 7 instructions per byte.
memcpy copies 16-byte blocks (shift-merge when src and dst disagree mod 4);
strlen tests a word per 6 instructions with the has-zero-byte identity.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (0xEDB88320 if c & 1 else 0)
        t.append(c)
    return t


MEMCPY = r"""
# ---------------------------------------------------------------- memcpy
memcpy:
    mv   t6, a0                 # t6 = dst cursor (a0 kept for the return)
    add  a7, a0, a2             # a7 = dst end
    li   t0, 16
    bltu a2, t0, mc_bytes
    andi t1, t6, 3
    beqz t1, mc_dst_aligned
mc_head:                        # byte copy until dst is word aligned
    lbu  t2, 0(a1)
    addi a1, a1, 1
    sb   t2, 0(t6)
    addi t6, t6, 1
    andi t1, t6, 3
    bnez t1, mc_head
mc_dst_aligned:
    sub  a6, a7, t6
    andi a6, a6, -16
    add  a5, t6, a6             # a5 = end of the 16-byte blocks
    andi t1, a1, 3
    bnez t1, mc_mis
    beq  t6, a5, mc_w1
mc_w4:
    lw   t0, 0(a1)
    lw   t1, 4(a1)
    lw   t2, 8(a1)
    lw   t3, 12(a1)
    sw   t0, 0(t6)
    sw   t1, 4(t6)
    sw   t2, 8(t6)
    sw   t3, 12(t6)
    addi a1, a1, 16
    addi t6, t6, 16
    bne  t6, a5, mc_w4
mc_w1:
    sub  a6, a7, t6
    andi a6, a6, -4
    add  a5, t6, a6
    beq  t6, a5, mc_bytes
mc_w1_loop:
    lw   t0, 0(a1)
    addi a1, a1, 4
    sw   t0, 0(t6)
    addi t6, t6, 4
    bne  t6, a5, mc_w1_loop
mc_bytes:
    beq  t6, a7, mc_done
mc_b_loop:
    lbu  t0, 0(a1)
    addi a1, a1, 1
    sb   t0, 0(t6)
    addi t6, t6, 1
    bne  t6, a7, mc_b_loop
mc_done:
    ret
mc_mis:                         # src misaligned by k = t1 bytes
    slli t4, t1, 3              # right shift 8k
    li   t5, 32
    sub  t5, t5, t4             # left shift 32-8k
    andi a1, a1, -4
    lw   t0, 0(a1)
    beq  t6, a5, mc_m1
mc_m4:
    lw   t1, 4(a1)
    srl  t0, t0, t4
    sll  a4, t1, t5
    or   t0, t0, a4
    sw   t0, 0(t6)
    lw   t2, 8(a1)
    srl  t1, t1, t4
    sll  a4, t2, t5
    or   t1, t1, a4
    sw   t1, 4(t6)
    lw   t3, 12(a1)
    srl  t2, t2, t4
    sll  a4, t3, t5
    or   t2, t2, a4
    sw   t2, 8(t6)
    lw   t0, 16(a1)
    srl  t3, t3, t4
    sll  a4, t0, t5
    or   t3, t3, a4
    sw   t3, 12(t6)
    addi a1, a1, 16
    addi t6, t6, 16
    bne  t6, a5, mc_m4
mc_m1:
    sub  a6, a7, t6
    andi a6, a6, -4
    add  a5, t6, a6
    beq  t6, a5, mc_m_tail
mc_m1_loop:
    lw   t1, 4(a1)
    srl  t0, t0, t4
    sll  a4, t1, t5
    or   t0, t0, a4
    sw   t0, 0(t6)
    mv   t0, t1
    addi a1, a1, 4
    addi t6, t6, 4
    bne  t6, a5, mc_m1_loop
mc_m_tail:
    srli t1, t4, 3
    add  a1, a1, t1             # back to a byte pointer
    j    mc_bytes
"""

STRLEN = r"""
# ---------------------------------------------------------------- strlen
strlen:
    andi a1, a0, -4             # aligned word pointer
    andi t0, a0, 3
    slli t0, t0, 3
    li   t2, -1
    sll  t2, t2, t0
    not  t2, t2                 # 0xFF in each byte before the start
    li   a6, 0xFEFEFEFF         # -0x01010101
    li   a7, 0x80808080
    lw   t1, 0(a1)
    or   t1, t1, t2
    add  t2, t1, a6
    not  t3, t1
    and  t2, t2, t3
    and  t2, t2, a7
    bnez t2, sl_found
sl_loop:
    lw   t1, 4(a1)
    add  t2, t1, a6
    not  t3, t1
    and  t2, t2, t3
    and  t2, t2, a7
    bnez t2, sl_found4
    lw   t1, 8(a1)
    addi a1, a1, 8
    add  t2, t1, a6
    not  t3, t1
    and  t2, t2, t3
    and  t2, t2, a7
    beqz t2, sl_loop
    j    sl_found
sl_found4:
    addi a1, a1, 4
sl_found:                       # lowest flagged byte of t2 is the NUL
    andi t3, t2, 0x80
    bnez t3, sl_ret
    addi a1, a1, 1
    slli t3, t2, 16
    srli t3, t3, 31
    bnez t3, sl_ret
    addi a1, a1, 1
    slli t3, t2, 8
    srli t3, t3, 31
    bnez t3, sl_ret
    addi a1, a1, 1
sl_ret:
    sub  a0, a1, a0
    ret
"""

CRC_HEAD = r"""
# ---------------------------------------------------------------- crc32
crc32:
    li   t6, 0xC000             # byte table in scratch
"""

CRC_STEP = """    andi t1, t0, 255
    slli t1, t1, 2
    add  t1, t1, t6
    lw   t1, 0(t1)
    srli t0, t0, 8
    xor  t0, t0, t1
"""

CRC_BODY = r"""
    li   t0, -1                 # crc register
    add  a7, a0, a1             # end
    li   t1, 8
    bltu a1, t1, crc_bytes
crc_head:
    andi t1, a0, 3
    beqz t1, crc_words_setup
    lbu  t1, 0(a0)
    addi a0, a0, 1
    xor  t1, t1, t0
    andi t1, t1, 255
    slli t1, t1, 2
    add  t1, t1, t6
    lw   t1, 0(t1)
    srli t0, t0, 8
    xor  t0, t0, t1
    j    crc_head
crc_words_setup:
    sub  a6, a7, a0
    andi a6, a6, -4
    add  a5, a0, a6
crc_words:
    lw   t2, 0(a0)
    addi a0, a0, 4
    xor  t0, t0, t2
""" + CRC_STEP * 4 + r"""    bne  a0, a5, crc_words
crc_bytes:
    beq  a0, a7, crc_done
    lbu  t1, 0(a0)
    addi a0, a0, 1
    xor  t1, t1, t0
    andi t1, t1, 255
    slli t1, t1, 2
    add  t1, t1, t6
    lw   t1, 0(t1)
    srli t0, t0, 8
    xor  t0, t0, t1
    j    crc_bytes
crc_done:
    not  a0, t0
    ret
"""


def build():
    out = ["# reference lib.s for eval 195 (generated by gen_ref.py)", ".text",
           MEMCPY, STRLEN, CRC_HEAD]
    for i, v in enumerate(table()):
        out.append("    li   t1, 0x%08X\n    sw   t1, %d(t6)" % (v, 4 * i))
    out.append(CRC_BODY)
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    with open(os.path.join(HERE, "ref.s"), "w") as f:
        f.write(build())
    print("wrote ref.s")
