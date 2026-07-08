bitrev:
    li t0, 0          # result = 0
    li t1, 0          # i = 0
loop:
    li t2, 32
    bge t1, t2, done  # 32 iterations
    slli t0, t0, 1    # result <<= 1
    andi t3, a0, 1    # lsb of x
    or t0, t0, t3     # result |= lsb
    srli a0, a0, 1    # x >>= 1
    addi t1, t1, 1
    j loop
done:
    mv a0, t0
    ret
