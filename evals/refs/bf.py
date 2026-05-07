import sys
def run(code, inp):
    pairs = {}
    stack = []
    for i, ch in enumerate(code):
        if ch == '[': stack.append(i)
        elif ch == ']':
            j = stack.pop(); pairs[i] = j; pairs[j] = i
    tape = [0] * 30000
    ptr = 0; ip = 0; ipos = 0
    out = []
    while ip < len(code):
        c = code[ip]
        if c == '>': ptr += 1
        elif c == '<': ptr -= 1
        elif c == '+': tape[ptr] = (tape[ptr] + 1) & 0xFF
        elif c == '-': tape[ptr] = (tape[ptr] - 1) & 0xFF
        elif c == '.': out.append(chr(tape[ptr]))
        elif c == ',':
            tape[ptr] = ord(inp[ipos]) if ipos < len(inp) else 0
            ipos += 1
        elif c == '[' and tape[ptr] == 0: ip = pairs[ip]
        elif c == ']' and tape[ptr] != 0: ip = pairs[ip]
        ip += 1
    return ''.join(out)

lines = sys.stdin.read().split('\n')
T = int(lines[0])
out_lines = []
for k in range(T):
    code = lines[1 + 2*k]
    inp = lines[2 + 2*k]
    out_lines.append(run(code, inp))
sys.stdout.write('\n'.join(out_lines) + '\n')
