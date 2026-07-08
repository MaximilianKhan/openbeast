main:
    li t0, 0          # sum = 0
    li t1, 0          # i = 0
loop:
    bge t1, a1, done  # if i >= len, done
    slli t2, t1, 2    # t2 = i*4
    add t3, a0, t2    # addr = base + i*4
    lw t4, 0(t3)      # load element
    add t0, t0, t4    # sum += element
    addi t1, t1, 1    # i++
    j loop
done:
    mv a0, t0         # return sum
    ret
