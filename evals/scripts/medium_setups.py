#!/usr/bin/env python3
"""Generate JSON for the 9 medium variant tasks."""
import json
import os
import sys
import importlib.util

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# Load the helper from easy_setups.py
spec = importlib.util.spec_from_file_location("easy", os.path.join(REPO, "evals/scripts/easy_setups.py"))
easy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(easy)
variant_template = easy.variant_template

# ---------------- 11_bst ----------------
bst_setup = (
    "mkdir -p /tmp/eval_bst && "
    "printf '4\\n7\\n5 3 8 1 4 7 9\\n1\\n10\\n4\\n5 5 5 5\\n11\\n3 1 4 1 5 9 2 6 5 3 5\\n' "
    "> /tmp/eval_bst/input.txt && "
    "printf '1 3 4 5 7 8 9\\n10\\n5\\n1 2 3 4 5 6 9\\n' > /tmp/eval_bst/expected.txt"
)
bst = variant_template(
    task_id="11_bst",
    slug="11_bst",
    name="Binary search tree (insert + in-order traversal)",
    difficulty="medium",
    category="Algorithms & DS",
    subcategory="Trees",
    max_iter=15,
    dest_dir="/tmp/eval_bst",
    file_stem="bst",
    setup=bst_setup,
    common_io=(
        "Reads from stdin: line 1 is T (test case count). Each case: line A is N "
        "(insert count, >= 1). Line B is N space-separated integers to insert into "
        "an initially empty BST in the given order. Skip duplicates (do not insert "
        "a value already present). For each case, output the in-order traversal of "
        "the resulting BST as space-separated integers on one line. Implement BST "
        "yourself — no language-builtin sorted-set / TreeMap."
    ),
    forbidden_per_lang={
        "python": r"sorted\(|\.sort\(",
        "go": r"sort\.(Ints|Slice|Sort)",
        "cpp": r"std::sort|std::set|std::map",
        "rust": r"\.sort(_unstable)?\(|BTreeSet|BTreeMap",
    },
)

# ---------------- 20_priority_queue ----------------
pq_setup = (
    "mkdir -p /tmp/eval_pq && "
    "printf '2\\n6\\np 5\\np 3\\np 8\\no\\no\\no\\n3\\np 100\\no\\no\\n' "
    "> /tmp/eval_pq/input.txt && "
    "printf '3\\n5\\n8\\n100\\n-\\n' > /tmp/eval_pq/expected.txt"
)
pq = variant_template(
    task_id="20_priority_queue",
    slug="20_priority_queue",
    name="Min-heap priority queue (push / pop-min)",
    difficulty="medium",
    category="Algorithms & DS",
    subcategory="Linear data structures",
    max_iter=15,
    dest_dir="/tmp/eval_pq",
    file_stem="pq",
    setup=pq_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each case: line A is Q (operation count). "
        "Next Q lines are operations: 'p V' (push integer V) or 'o' (pop minimum). "
        "For each 'o' op, output the popped value on its own line, or '-' if the "
        "heap is empty. Implement the binary min-heap yourself with sift-up on "
        "push and sift-down on pop — no language-builtin priority queue / heap."
    ),
    forbidden_per_lang={
        "python": r"\bimport heapq|from heapq",
        "go": r"container/heap",
        "cpp": r"std::priority_queue|std::make_heap|std::push_heap|std::pop_heap",
        "rust": r"BinaryHeap",
        "zig": r"std\.PriorityQueue",
    },
)

# ---------------- 54_astar ----------------
astar_setup = (
    "mkdir -p /tmp/eval_astar && "
    "cat > /tmp/eval_astar/input.txt <<'EOF'\n"
    "3\n"
    "3 4\n"
    "S...\n"
    "....\n"
    "...G\n"
    "5 5\n"
    "S....\n"
    "###.#\n"
    "....#\n"
    ".####\n"
    "....G\n"
    "3 3\n"
    "S.#\n"
    "###\n"
    "#.G\n"
    "EOF\n"
    "printf '5\\n14\\n-1\\n' > /tmp/eval_astar/expected.txt"
)
astar = variant_template(
    task_id="54_astar",
    slug="54_astar",
    name="Shortest path on grid (BFS / A*)",
    difficulty="medium",
    category="Algorithms & DS",
    subcategory="Graph algorithms",
    max_iter=15,
    dest_dir="/tmp/eval_astar",
    file_stem="astar",
    setup=astar_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each case: line A is 'H W' (rows, cols). "
        "Next H lines are W characters each — '.' open, '#' wall, 'S' start, 'G' "
        "goal. Find the shortest 4-connected path (up/down/left/right) from S to G "
        "and output its step count, or -1 if unreachable. BFS or A* both fine "
        "(uniform-cost grid). One integer per line."
    ),
    forbidden_per_lang={},
)

# ---------------- 38_monte_carlo_pi ----------------
# We implement the reference LCG behavior to compute the expected values.
def mcpi_count(t_samples: int, seed: int) -> int:
    state = seed & ((1 << 64) - 1)
    MASK = (1 << 64) - 1
    MULT = 6364136223846793005
    INC = 1442695040888963407
    R2 = (1 << 30) * (1 << 30)
    count = 0
    for _ in range(t_samples):
        state = (state * MULT + INC) & MASK
        x = (state >> 33) & 0x3FFFFFFF
        state = (state * MULT + INC) & MASK
        y = (state >> 33) & 0x3FFFFFFF
        if x * x + y * y < R2:
            count += 1
    return count

mcpi_cases = [(1000, 0), (10000, 42), (5000, 12345), (20000, 99999)]
mcpi_input_lines = [str(len(mcpi_cases))]
mcpi_expected_lines = []
for t_n, s in mcpi_cases:
    mcpi_input_lines.append(f"{t_n} {s}")
    mcpi_expected_lines.append(str(mcpi_count(t_n, s)))

mcpi_setup = (
    "mkdir -p /tmp/eval_mcpi && "
    f"cat > /tmp/eval_mcpi/input.txt <<'EOF'\n"
    f"{chr(10).join(mcpi_input_lines)}\n"
    f"EOF\n"
    f"cat > /tmp/eval_mcpi/expected.txt <<'EOF'\n"
    f"{chr(10).join(mcpi_expected_lines)}\n"
    f"EOF"
)

mcpi = variant_template(
    task_id="38_monte_carlo_pi",
    slug="38_monte_carlo_pi",
    name="Monte Carlo π (deterministic LCG, integer-only predicate)",
    difficulty="medium",
    category="Probability & Stats",
    subcategory="Monte Carlo simulation",
    max_iter=15,
    dest_dir="/tmp/eval_mcpi",
    file_stem="mcpi",
    setup=mcpi_setup,
    common_io=(
        "Reads from stdin: line 1 is T (test cases). Each next line is 'N seed'. "
        "Use a 64-bit LCG with multiplier 6364136223846793005 and increment "
        "1442695040888963407 (Knuth MMIX), wrapping at 2^64. Initialize state = "
        "seed. For N samples, advance the LCG once and take "
        "x = (state >> 33) & 0x3FFFFFFF (30 bits), advance again and take y "
        "similarly. Count samples where x*x + y*y < (1<<30)*(1<<30). Output that "
        "integer count on its own line. Use 64-bit unsigned arithmetic for state "
        "and 64-bit (or 128-bit) for the x*x+y*y comparison."
    ),
    forbidden_per_lang={},
)

# ---------------- 39_blocked_transpose ----------------
trans_setup = (
    "mkdir -p /tmp/eval_trans && "
    "printf '3\\n2 3\\n1 2 3\\n4 5 6\\n3 3\\n1 0 0\\n0 1 0\\n0 0 1\\n4 2\\n10 20\\n30 40\\n50 60\\n70 80\\n' "
    "> /tmp/eval_trans/input.txt && "
    "printf '1 4\\n2 5\\n3 6\\n1 0 0\\n0 1 0\\n0 0 1\\n10 30 50 70\\n20 40 60 80\\n' > /tmp/eval_trans/expected.txt"
)
trans = variant_template(
    task_id="39_blocked_transpose",
    slug="39_blocked_transpose",
    name="Cache-blocked matrix transpose",
    difficulty="medium",
    category="Performance & HW Opt",
    subcategory="Cache locality",
    max_iter=15,
    dest_dir="/tmp/eval_trans",
    file_stem="trans",
    setup=trans_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each case: line A is 'H W'. Next H lines "
        "are W space-separated integers (row-major). Output the W x H transpose: "
        "W lines of H space-separated integers. Use a cache-blocked iteration "
        "pattern (block size 16 or 32) to walk the input in cache-friendly order "
        "— even though the output is identical to the naive transpose, the access "
        "pattern matters at scale."
    ),
    forbidden_per_lang={},
)

# ---------------- 36_black_scholes ----------------
# BS expected values are precomputed via Python math.erf for the 4 cases
# below. Tolerance check (1e-3) accommodates models that use the A&S
# approximation in Rust/Zig (which lack stdlib erf). See task field for
# the constants the spec recommends to those languages.
bs_setup = (
    "mkdir -p /tmp/eval_bs && "
    "printf '4\\n100 100 1.0 0.05 0.2\\n50 50 0.5 0.03 0.3\\n80 100 2.0 0.04 0.25\\n100 50 1.0 0.05 0.5\\n' "
    "> /tmp/eval_bs/input.txt && "
    "printf '10.4506\\n4.5747\\n6.8985\\n53.4579\\n' > /tmp/eval_bs/expected.txt && "
    "cat > /tmp/eval_bs/check.py <<'PYEOF'\n"
    "exp = [float(x) for x in open('expected.txt').read().split()]\n"
    "got = [float(x) for x in open('out.txt').read().split()]\n"
    "assert len(exp) == len(got), f'got {len(got)} want {len(exp)}'\n"
    "for i,(a,b) in enumerate(zip(exp,got)):\n"
    "    assert abs(a-b) < 1e-3, f'case {i}: {a} vs {b}'\n"
    "print('OK')\n"
    "PYEOF"
)
bs = variant_template(
    task_id="36_black_scholes",
    slug="36_black_scholes",
    name="Black-Scholes European call price",
    difficulty="medium",
    category="Mathematical Finance",
    subcategory="Option pricing",
    max_iter=20,
    dest_dir="/tmp/eval_bs",
    file_stem="bs",
    setup=bs_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each next line is 'S K T r sigma' (5 "
        "floats: spot, strike, time-to-expiry years, risk-free rate, volatility). "
        "Output the Black-Scholes European call price c = S*N(d1) - K*exp(-r*T)*N(d2) "
        "where d1 = (ln(S/K) + (r + sigma^2/2)*T) / (sigma*sqrt(T)), d2 = d1 - "
        "sigma*sqrt(T), and N(.) is the standard normal CDF. Format to 4 decimals. "
        "If your stdlib lacks erf, use the Abramowitz & Stegun 7.1.26 approximation "
        "(p=0.3275911, a1=0.254829592, a2=-0.284496736, a3=1.421413741, a4=-1.453152027, "
        "a5=1.061405429)."
    ),
    forbidden_per_lang={},
)
# Post-process: swap strict-diff validation for tolerance-based check.py
for _v in bs['variants']:
    stem = 'bs'
    DIR = '/tmp/eval_bs'
    if _v['language'] == 'python':
        _v['validation']['script'] = f'set -e; cd {DIR} && python3 {stem}.py < input.txt > out.txt && python3 check.py'
    elif _v['language'] == 'go':
        _v['validation']['script'] = f'set -e; cd {DIR} && go build -o {stem} {stem}.go && ./{stem} < input.txt > out.txt && python3 check.py'
    elif _v['language'] == 'c':
        _v['validation']['script'] = f'set -e; cd {DIR} && gcc -O2 -std=c11 -Wall -Wextra -o {stem} {stem}.c -lm && ./{stem} < input.txt > out.txt && python3 check.py'
    elif _v['language'] == 'cpp':
        _v['validation']['script'] = f'set -e; cd {DIR} && g++ -O2 -std=c++17 -Wall -Wextra -o {stem} {stem}.cpp && ./{stem} < input.txt > out.txt && python3 check.py'
    elif _v['language'] == 'rust':
        _v['validation']['script'] = f'set -e; cd {DIR} && rustc -O {stem}.rs -o {stem} 2>/dev/null && ./{stem} < input.txt > out.txt && python3 check.py'
    elif _v['language'] == 'zig':
        _v['validation']['script'] = f'set -e; cd {DIR} && zig build-exe -O ReleaseFast {stem}.zig && ./{stem} < input.txt > out.txt && python3 check.py'

# ---------------- 62_crt ----------------
crt_setup = (
    "mkdir -p /tmp/eval_crt && "
    "printf '5\\n2\\n2 3\\n3 5\\n3\\n0 3\\n3 4\\n4 5\\n2\\n1 6\\n4 9\\n2\\n1 4\\n4 6\\n3\\n1 2\\n2 3\\n3 5\\n' "
    "> /tmp/eval_crt/input.txt && "
    "printf '8\\n39\\n13\\n-1\\n23\\n' > /tmp/eval_crt/expected.txt"
)
# Verify expected via Python
def crt_solve(rs, ms):
    M = 1
    x = 0
    from math import gcd
    for r, m in zip(rs, ms):
        g = gcd(M, m)
        if (r - x) % g != 0:
            return -1
        # Extend x mod M to mod lcm(M, m)
        m2 = m // g
        M2 = M // g
        # solve x + M*k ≡ r (mod m) → k ≡ (r-x)/g * (M/g)^-1 mod m/g
        # Compute inverse of M2 mod m2
        def modinv(a, n):
            from math import gcd as _g
            if _g(a, n) != 1:
                return None
            t, newt = 0, 1
            r0, newr = n, a % n
            while newr != 0:
                q = r0 // newr
                t, newt = newt, t - q * newt
                r0, newr = newr, r0 - q * newr
            return t % n
        inv = modinv(M2 % m2, m2)
        if inv is None:
            return -1
        k = ((r - x) // g) * inv % m2
        x = x + M * k
        M = M * m2
        x %= M
    return x
# sanity
assert crt_solve([2, 3], [3, 5]) == 8
assert crt_solve([0, 3, 4], [3, 4, 5]) == 39
# 1 mod 6, 4 mod 9: gcd(6,9)=3, (4-1)%3=0 ok. Solve: x=1+6k ≡ 4 (mod 9) → 6k ≡ 3 (mod 9) → 2k≡1 (mod 3) → k=2 → x=13. lcm=18. So 13.
assert crt_solve([1, 4], [6, 9]) == 13
assert crt_solve([1, 4], [4, 6]) == -1  # 1 mod 4, 4 mod 6: gcd(4,6)=2, (4-1)%2=1≠0 → no soln
# 1 mod 2, 2 mod 3, 3 mod 5: 1 → 5 → 23
assert crt_solve([1, 2, 3], [2, 3, 5]) == 23

crt = variant_template(
    task_id="62_crt",
    slug="62_crt",
    name="Chinese Remainder Theorem (general moduli)",
    difficulty="medium",
    category="Pure & Abstract Math",
    subcategory="Number theory",
    max_iter=20,
    dest_dir="/tmp/eval_crt",
    file_stem="crt",
    setup=crt_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each case: line A is K (number of equations, "
        ">= 1). Next K lines are 'r m' pairs (remainder and modulus, m >= 1, "
        "0 <= r < m). Output the smallest non-negative integer x satisfying "
        "x ≡ r_i (mod m_i) for all i, or -1 if no solution exists. Use general "
        "CRT (handles non-coprime moduli — gcd(m_i, m_j) need not be 1, but the "
        "remainders must be consistent on the gcd). Use 64-bit signed arithmetic; "
        "all answers fit in i64."
    ),
    forbidden_per_lang={},
)

# ---------------- 63_det ----------------
det_setup = (
    "mkdir -p /tmp/eval_det && "
    "printf '4\\n2\\n1 2\\n3 4\\n3\\n1 0 0\\n0 1 0\\n0 0 1\\n3\\n2 -1 0\\n-1 2 -1\\n0 -1 2\\n4\\n1 2 3 4\\n5 6 7 8\\n9 10 11 12\\n13 14 15 16\\n' "
    "> /tmp/eval_det/input.txt && "
    "printf -- '-2.000000\\n1.000000\\n4.000000\\n0.000000\\n' > /tmp/eval_det/expected.txt"
)
det = variant_template(
    task_id="63_det",
    slug="63_det",
    name="Matrix determinant via Gaussian elimination",
    difficulty="medium",
    category="Pure & Abstract Math",
    subcategory="Linear algebra",
    max_iter=20,
    dest_dir="/tmp/eval_det",
    file_stem="det",
    setup=det_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each case: line A is N (matrix dimension). "
        "Next N lines are N space-separated floats (row-major). Output the matrix "
        "determinant, formatted to 6 decimals. Use Gaussian elimination with "
        "partial pivoting — track sign flips on row swaps. If a pivot is exactly "
        "zero with no nonzero replacement below, the determinant is 0. Use double-"
        "precision floats."
    ),
    forbidden_per_lang={},
)

# ---------------- 136_gf256 ----------------
gf_setup = (
    "mkdir -p /tmp/eval_gf && "
    "printf '8\\n+ 1 2\\n+ 87 19\\n+ 255 255\\n* 1 1\\n* 2 4\\n* 87 19\\n* 0xFF 0xFF\\n* 0x53 0xCA\\n' "
    "> /tmp/eval_gf/input.txt && "
    # Compute expected:
    # GF(2^8) addition is XOR.
    # GF(2^8) multiplication mod AES poly 0x11B.
    "python3 -c '\n"
    "def gfmul(a,b):\n"
    "    p = 0\n"
    "    for _ in range(8):\n"
    "        if b & 1: p ^= a\n"
    "        b >>= 1\n"
    "        hi = a & 0x80\n"
    "        a = (a << 1) & 0xFF\n"
    "        if hi: a ^= 0x1B\n"
    "    return p\n"
    "cases = [(\"+\",1,2),(\"+\",87,19),(\"+\",255,255),(\"*\",1,1),(\"*\",2,4),(\"*\",87,19),(\"*\",0xFF,0xFF),(\"*\",0x53,0xCA)]\n"
    "outs = []\n"
    "for op,a,b in cases:\n"
    "    if op == \"+\": outs.append(str(a ^ b))\n"
    "    else: outs.append(str(gfmul(a,b)))\n"
    "open(\"/tmp/eval_gf/expected.txt\",\"w\").write(\"\\n\".join(outs)+\"\\n\")\n"
    "'"
)
gf = variant_template(
    task_id="136_gf256",
    slug="136_gf256",
    name="GF(2^8) finite-field arithmetic (AES polynomial)",
    difficulty="medium",
    category="Pure & Abstract Math",
    subcategory="Number theory",
    max_iter=15,
    dest_dir="/tmp/eval_gf",
    file_stem="gf",
    setup=gf_setup,
    common_io=(
        "Reads from stdin: line 1 is T. Each next line is 'op a b' where op is "
        "'+' or '*' and a, b are 0..255 (decimal, or hex with 0x prefix). For each, "
        "output the result of the operation in GF(2^8) with reduction polynomial "
        "0x11B (the AES Rijndael polynomial) on its own line as a decimal integer. "
        "'+' is XOR; '*' is polynomial multiplication mod 0x11B (shift-and-XOR). "
        "Output is always 0..255."
    ),
    forbidden_per_lang={},
)


for task in [bst, pq, astar, mcpi, trans, bs, crt, det, gf]:
    path = os.path.join(REPO, "evals/tasks", f"{task['id']}.json")
    with open(path, "w") as f:
        json.dump(task, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {path}  ({len(task['variants'])} variants)")
