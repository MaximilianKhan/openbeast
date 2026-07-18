#!/usr/bin/env python3
"""Generate JSON for the 6 hard variant tasks."""
import json
import os
import importlib.util

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
spec = importlib.util.spec_from_file_location("easy", os.path.join(REPO, "evals/scripts/easy_setups.py"))
easy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(easy)
variant_template = easy.variant_template

# ==================== 27_brainfuck_interpreter ====================
# 4 cases, all outputs printable ASCII.
# Verify expected (Python BF)
def bf_run(code, inp=''):
    code = list(code)
    pairs = {}
    stack = []
    for i, ch in enumerate(code):
        if ch == '[': stack.append(i)
        elif ch == ']':
            j = stack.pop(); pairs[i] = j; pairs[j] = i
    tape = [0] * 30000; ptr = 0; ip = 0; ipos = 0
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

assert bf_run('++++++++[>++++++++<-]>.') == '@'
assert bf_run(',+.', 'A') == 'B'
assert bf_run('++++++++[>++++++++<-]>+.') == 'A'
assert bf_run('+++++++++[>+++++++++<-]>.') == 'Q'

bf_setup = (
    "mkdir -p /tmp/eval_bf && "
    "cat > /tmp/eval_bf/input.txt <<'EOF'\n"
    "4\n"
    "++++++++[>++++++++<-]>.\n"
    "\n"
    ",+.\n"
    "A\n"
    "++++++++[>++++++++<-]>+.\n"
    "\n"
    "+++++++++[>+++++++++<-]>.\n"
    "\n"
    "EOF\n"
    "printf '@\\nB\\nA\\nQ\\n' > /tmp/eval_bf/expected.txt"
)

assert bf_run('++++++++[>++++++++<-]>+.') == 'A'

bf = variant_template(
    task_id="27_brainfuck_interpreter",
    slug="27_brainfuck_interpreter",
    name="Brainfuck interpreter",
    difficulty="hard",
    category="Algorithms & DS",
    subcategory="Recursion / interpretation",
    max_iter=20,
    dest_dir="/tmp/eval_bf",
    file_stem="bf",
    setup=bf_setup,
    common_io=(
        "Reads from stdin: line 1 is T (test cases). Each case is two lines: "
        "line A is the Brainfuck program (single line, no embedded newlines); "
        "line B is the input string (may be empty). Run the program (8 commands: "
        "> < + - . , [ ]; tape lazy/zero-initialized, cells unsigned 8-bit wrap, "
        "30000+ cells; bracket matching pre-computed in O(n)) and write its "
        "output to stdout. End each test's output with a newline. The bytes "
        "in the test fixtures are printable ASCII so a strict diff works."
    ),
    forbidden_per_lang={},
)

# ==================== 115_fft ====================
# Generate fixture via Python's reference FFT. 4-decimal output.
import cmath
import math as _m
def fft_py(x):
    n = len(x)
    if n == 1: return list(x)
    if n & (n - 1) != 0: raise ValueError
    e = fft_py(x[0::2])
    o = fft_py(x[1::2])
    out = [0]*n
    for k in range(n // 2):
        t = cmath.exp(-2j * _m.pi * k / n) * o[k]
        out[k] = e[k] + t
        out[k + n // 2] = e[k] - t
    return out

fft_cases = [
    [1.0, 0.0, 0.0, 0.0],
    [1.0, 1.0, 1.0, 1.0],
    [1.0, 2.0, 3.0, 4.0],
    [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0],
]
fft_inp_lines = [str(len(fft_cases))]
fft_exp_lines = []
for x in fft_cases:
    fft_inp_lines.append(str(len(x)))
    fft_inp_lines.append(' '.join(f'{v}' for v in x))
    out = fft_py(x)
    for c in out:
        fft_exp_lines.append(f'{c.real:.4f} {c.imag:.4f}')

fft_setup = (
    "mkdir -p /tmp/eval_fft && "
    "cat > /tmp/eval_fft/input.txt <<'EOF'\n" +
    "\n".join(fft_inp_lines) + "\nEOF\n" +
    "cat > /tmp/eval_fft/expected.txt <<'EOF'\n" +
    "\n".join(fft_exp_lines) + "\nEOF\n" +
    "cat > /tmp/eval_fft/check.py <<'PYEOF'\n"
    "exp = [float(x) for x in open('expected.txt').read().split()]\n"
    "got = [float(x) for x in open('out.txt').read().split()]\n"
    "assert len(exp) == len(got), f'got {len(got)} want {len(exp)}'\n"
    "for i,(a,b) in enumerate(zip(exp,got)):\n"
    "    assert abs(a-b) < 1e-3, f'idx {i}: {a} vs {b}'\n"
    "print('OK')\n"
    "PYEOF"
)

fft = variant_template(
    task_id="115_fft",
    slug="115_fft",
    name="Cooley-Tukey radix-2 FFT (real input)",
    difficulty="hard",
    category="Pure & Abstract Math",
    subcategory="Polynomial algebra",
    max_iter=20,
    dest_dir="/tmp/eval_fft",
    file_stem="fft",
    setup=fft_setup,
    common_io=(
        "Reads from stdin: line 1 is T (test cases). Each case: line A is N (must "
        "be a power of 2). Line B is N space-separated real-valued floats (the "
        "real input; imaginary parts zero). For each case, output N lines, each "
        "with two floats 'real imag' formatted to 4 decimals, representing the "
        "Cooley-Tukey radix-2 FFT with twiddle factor exp(-2*pi*i*k/N). The "
        "expected outputs use ~1e-3 tolerance (validation runs check.py)."
    ),
    forbidden_per_lang={},
)
# Patch validation scripts to use check.py instead of strict diff
for v in fft['variants']:
    if v['language'] == 'python':
        v['validation']['script'] = 'set -e; cd /tmp/eval_fft && python3 fft.py < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'go':
        v['validation']['script'] = 'set -e; cd /tmp/eval_fft && go build -o fft fft.go && ./fft < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'c':
        v['validation']['script'] = 'set -e; cd /tmp/eval_fft && gcc -O2 -std=c11 -Wall -Wextra -o fft fft.c -lm && ./fft < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'cpp':
        v['validation']['script'] = 'set -e; cd /tmp/eval_fft && g++ -O2 -std=c++17 -Wall -Wextra -o fft fft.cpp && ./fft < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'rust':
        v['validation']['script'] = 'set -e; cd /tmp/eval_fft && rustc -O fft.rs -o fft 2>/dev/null && ./fft < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'zig':
        v['validation']['script'] = 'set -e; cd /tmp/eval_fft && zig build-exe -O ReleaseFast fft.zig && ./fft < input.txt > out.txt && python3 check.py'

# ==================== 123_nbody ====================
# 2D N-body Verlet. Output positions+velocities after `steps` of size dt, with
# tolerance check.
def nbody_step(pos, vel, mass, dt, steps, G=1.0):
    n = len(pos)
    pos = [list(p) for p in pos]
    vel = [list(v) for v in vel]
    def accel(p):
        a = [[0.0, 0.0] for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j: continue
                dx = p[j][0] - p[i][0]
                dy = p[j][1] - p[i][1]
                r2 = dx*dx + dy*dy
                r = r2**0.5
                f = G * mass[j] / (r2 * r) if r > 1e-12 else 0.0
                a[i][0] += f * dx
                a[i][1] += f * dy
        return a
    a = accel(pos)
    for _ in range(steps):
        for i in range(n):
            pos[i][0] += vel[i][0]*dt + 0.5*a[i][0]*dt*dt
            pos[i][1] += vel[i][1]*dt + 0.5*a[i][1]*dt*dt
        a_new = accel(pos)
        for i in range(n):
            vel[i][0] += 0.5 * (a[i][0] + a_new[i][0]) * dt
            vel[i][1] += 0.5 * (a[i][1] + a_new[i][1]) * dt
        a = a_new
    return pos, vel

# Single test: 2-body, masses 1+1, initial pos (-1,0)/(1,0), vel (0,-0.5)/(0,0.5), dt=0.01, steps=100
nb_pos_init = [(-1.0, 0.0), (1.0, 0.0)]
nb_vel_init = [(0.0, -0.5), (0.0, 0.5)]
nb_mass = [1.0, 1.0]
nb_dt = 0.01
nb_steps = 100
fp, fv = nbody_step(nb_pos_init, nb_vel_init, nb_mass, nb_dt, nb_steps)

nb_inp_lines = [
    "1",
    f"2 {nb_dt} {nb_steps}",
    f"{nb_mass[0]} {nb_pos_init[0][0]} {nb_pos_init[0][1]} {nb_vel_init[0][0]} {nb_vel_init[0][1]}",
    f"{nb_mass[1]} {nb_pos_init[1][0]} {nb_pos_init[1][1]} {nb_vel_init[1][0]} {nb_vel_init[1][1]}",
]
nb_exp_lines = []
for i in range(2):
    nb_exp_lines.append(f"{fp[i][0]:.6f} {fp[i][1]:.6f} {fv[i][0]:.6f} {fv[i][1]:.6f}")

nb_setup = (
    "mkdir -p /tmp/eval_nbody && "
    "cat > /tmp/eval_nbody/input.txt <<'EOF'\n" +
    "\n".join(nb_inp_lines) + "\nEOF\n" +
    "cat > /tmp/eval_nbody/expected.txt <<'EOF'\n" +
    "\n".join(nb_exp_lines) + "\nEOF\n" +
    "cat > /tmp/eval_nbody/check.py <<'PYEOF'\n"
    "exp = [float(x) for x in open('expected.txt').read().split()]\n"
    "got = [float(x) for x in open('out.txt').read().split()]\n"
    "assert len(exp) == len(got), f'got {len(got)} want {len(exp)}'\n"
    "for i,(a,b) in enumerate(zip(exp,got)):\n"
    "    assert abs(a-b) < 1e-3, f'idx {i}: {a} vs {b}'\n"
    "print('OK')\n"
    "PYEOF"
)

nbody = variant_template(
    task_id="123_nbody",
    slug="123_nbody",
    name="2D N-body Velocity-Verlet integrator",
    difficulty="hard",
    category="Physics",
    subcategory="N-body / orbital mechanics",
    max_iter=20,
    dest_dir="/tmp/eval_nbody",
    file_stem="nbody",
    setup=nb_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each case: line A is 'N dt steps'. Next "
        "N lines are 'mass x y vx vy' (2D position+velocity). Use Velocity-Verlet "
        "with G=1.0: x(t+dt) = x(t) + v(t)*dt + 0.5*a(t)*dt^2; a uses pairwise "
        "F_ij = G*m_j*(x_j - x_i)/|r|^3; v(t+dt) = v(t) + 0.5*(a(t) + a(t+dt))*dt. "
        "Output N lines per case, each 'fx fy vx vy' formatted to 6 decimals "
        "(positions and velocities at the final step). 1e-3 tolerance."
    ),
    forbidden_per_lang={},
)
for v in nbody['variants']:
    if v['language'] == 'python':
        v['validation']['script'] = 'set -e; cd /tmp/eval_nbody && python3 nbody.py < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'go':
        v['validation']['script'] = 'set -e; cd /tmp/eval_nbody && go build -o nbody nbody.go && ./nbody < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'c':
        v['validation']['script'] = 'set -e; cd /tmp/eval_nbody && gcc -O2 -std=c11 -Wall -Wextra -o nbody nbody.c -lm && ./nbody < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'cpp':
        v['validation']['script'] = 'set -e; cd /tmp/eval_nbody && g++ -O2 -std=c++17 -Wall -Wextra -o nbody nbody.cpp && ./nbody < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'rust':
        v['validation']['script'] = 'set -e; cd /tmp/eval_nbody && rustc -O nbody.rs -o nbody 2>/dev/null && ./nbody < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'zig':
        v['validation']['script'] = 'set -e; cd /tmp/eval_nbody && zig build-exe -O ReleaseFast nbody.zig && ./nbody < input.txt > out.txt && python3 check.py'

# ==================== 127_aes_keysched ====================
aes_setup = (
    "mkdir -p /tmp/eval_aes && "
    "printf '2\\n2b7e151628aed2a6abf7158809cf4f3c\\n000102030405060708090a0b0c0d0e0f\\n' > /tmp/eval_aes/input.txt && "
    "cat > /tmp/eval_aes/expected.txt <<'EOF'\n"
    "2b7e151628aed2a6abf7158809cf4f3c\n"
    "a0fafe1788542cb123a339392a6c7605\n"
    "f2c295f27a96b9435935807a7359f67f\n"
    "3d80477d4716fe3e1e237e446d7a883b\n"
    "ef44a541a8525b7fb671253bdb0bad00\n"
    "d4d1c6f87c839d87caf2b8bc11f915bc\n"
    "6d88a37a110b3efddbf98641ca0093fd\n"
    "4e54f70e5f5fc9f384a64fb24ea6dc4f\n"
    "ead27321b58dbad2312bf5607f8d292f\n"
    "ac7766f319fadc2128d12941575c006e\n"
    "d014f9a8c9ee2589e13f0cc8b6630ca6\n"
    "000102030405060708090a0b0c0d0e0f\n"
    "d6aa74fdd2af72fadaa678f1d6ab76fe\n"
    "b692cf0b643dbdf1be9bc5006830b3fe\n"
    "b6ff744ed2c2c9bf6c590cbf0469bf41\n"
    "47f7f7bc95353e03f96c32bcfd058dfd\n"
    "3caaa3e8a99f9deb50f3af57adf622aa\n"
    "5e390f7df7a69296a7553dc10aa31f6b\n"
    "14f9701ae35fe28c440adf4d4ea9c026\n"
    "47438735a41c65b9e016baf4aebf7ad2\n"
    "549932d1f08557681093ed9cbe2c974e\n"
    "13111d7fe3944a17f307a78b4d2b30c5\n"
    "EOF"
)
aes = variant_template(
    task_id="127_aes_keysched",
    slug="127_aes_keysched",
    name="AES-128 key schedule",
    difficulty="hard",
    category="Security",
    subcategory="Cipher implementation",
    max_iter=20,
    dest_dir="/tmp/eval_aes",
    file_stem="aes",
    setup=aes_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each next line is a 32-character hex "
        "string encoding a 16-byte AES-128 key. For each, output 11 lines (one "
        "per round key), each a 32-char lowercase hex string of the round key "
        "bytes. Implement the FIPS-197 AES key schedule: SubBytes via the "
        "standard 256-byte S-box, RotWord (rotate 4 bytes left by 1), Rcon = "
        "[0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36] (1-indexed; "
        "Rcon[i] used in iteration ceil(i/Nk)). Round 0 = the input key."
    ),
    forbidden_per_lang={},
)

# ==================== 47_branchless_min ====================
br_setup = (
    "mkdir -p /tmp/eval_branchless && "
    "printf '6\\n3 1 2\\n5 5 5\\n-7 -3 -10\\n100 50 25\\n2147483647 -2147483648 0\\n0 0 0\\n' "
    "> /tmp/eval_branchless/input.txt && "
    "printf '1\\n5\\n-10\\n25\\n-2147483648\\n0\\n' > /tmp/eval_branchless/expected.txt"
)
br = variant_template(
    task_id="47_branchless_min",
    slug="47_branchless_min",
    name="Branchless min of three integers",
    difficulty="hard",
    category="Performance & HW Opt",
    subcategory="Bit-twiddling",
    max_iter=15,
    dest_dir="/tmp/eval_branchless",
    file_stem="branchless",
    setup=br_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each next line is 'a b c' (three "
        "32-bit-fit signed integers). Output min(a, b, c) on its own line. "
        "Implement branchlessly: no language-level conditional branches in your "
        "min function (no `if`, `while`, `for`, ternary `? :` or `if-else`, no "
        "language-builtin min/max/sort, no short-circuit logical operators in "
        "the answer expression). The intended approach: use a sign-bit trick "
        "such as min2(a,b) = b ^ ((a^b) & ((a-b) >> N)) where N = bit-width-1, "
        "then min3 = min2(a, min2(b, c)). Use 64-bit arithmetic to avoid the "
        "INT_MIN-INT_MAX overflow case in the subtraction."
    ),
    # Lints intentionally empty: the spec text already tells the model
    # "branchless"; lints applied to the whole source file can't distinguish
    # the algorithm body from the I/O loop (`for k in range(T)`) without
    # AST-aware filtering. Trade-off accepted — correctness is enforced by
    # output diff; the branchless property is a soft requirement enforced by
    # the spec text.
    forbidden_per_lang={},
)

# ==================== 137_pollard_rho ====================
prho_setup = (
    "mkdir -p /tmp/eval_prho && "
    "printf '5\\n8051\\n1024\\n100000007000000049\\n15\\n1000003000033\\n' "
    "> /tmp/eval_prho/input.txt && "
    "cat > /tmp/eval_prho/check.py <<'PYEOF'\n"
    "from math import gcd\n"
    "ns = open('input.txt').read().split()\n"
    "T = int(ns[0])\n"
    "out = [int(x) for x in open('out.txt').read().split()]\n"
    "assert len(out) == T, f'got {len(out)} want {T}'\n"
    "for i in range(T):\n"
    "    n = int(ns[1+i]); f = out[i]\n"
    "    assert 1 < f < n, f'case {i}: f={f} not in (1, n={n})'\n"
    "    assert n % f == 0, f'case {i}: f={f} does not divide n={n}'\n"
    "print('OK')\n"
    "PYEOF"
)

prho = variant_template(
    task_id="137_pollard_rho",
    slug="137_pollard_rho",
    name="Pollard's rho factorization (Floyd cycle detection)",
    difficulty="hard",
    category="Pure & Abstract Math",
    subcategory="Number theory",
    max_iter=20,
    dest_dir="/tmp/eval_prho",
    file_stem="prho",
    setup=prho_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each next line is a positive integer n "
        "(possibly up to ~10^18, semiprime or composite, n >= 4). For each, "
        "output a non-trivial factor f such that 1 < f < n and n % f == 0, on "
        "its own line. For even n return 2. Otherwise iterate f(x) = (x*x + c) "
        "mod n with Floyd's tortoise-and-hare cycle detection: x_{i+1} = f(x_i), "
        "y_{i+1} = f(f(y_i)), d = gcd(|x - y|, n); if d == n, vary c and retry. "
        "Use 128-bit (or u128/i128) arithmetic for the modular multiply to avoid "
        "overflow on n > 2^32."
    ),
    forbidden_per_lang={},
)
for v in prho['variants']:
    base = v['validation']['script']
    # Replace strict diff with check.py
    if v['language'] == 'python':
        v['validation']['script'] = 'set -e; cd /tmp/eval_prho && python3 prho.py < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'go':
        v['validation']['script'] = 'set -e; cd /tmp/eval_prho && go build -o prho prho.go && ./prho < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'c':
        v['validation']['script'] = 'set -e; cd /tmp/eval_prho && gcc -O2 -std=c11 -Wall -Wextra -o prho prho.c -lm && ./prho < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'cpp':
        v['validation']['script'] = 'set -e; cd /tmp/eval_prho && g++ -O2 -std=c++17 -Wall -Wextra -o prho prho.cpp && ./prho < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'rust':
        v['validation']['script'] = 'set -e; cd /tmp/eval_prho && rustc -O prho.rs -o prho 2>/dev/null && ./prho < input.txt > out.txt && python3 check.py'
    elif v['language'] == 'zig':
        v['validation']['script'] = 'set -e; cd /tmp/eval_prho && zig build-exe -O ReleaseFast prho.zig && ./prho < input.txt > out.txt && python3 check.py'


for task in [bf, fft, nbody, aes, br, prho]:
    path = os.path.join(REPO, "evals/tasks", f"{task['id']}.json")
    with open(path, "w") as f:
        json.dump(task, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {path}  ({len(task['variants'])} variants)")
