# Validator body for 196_polyphase_resampler (build.py prepends RUNNER = ...).
# Every case is generated here from a fixed seed; nothing is written to the
# fixture dir. A failure reports only the case index and the failure kind.
import ast, cmath, json, math, os, random, struct, subprocess, sys

SUB = "/tmp/eval_resamp196/resample.py"
SOL_TIMEOUT_S = 10.0
L, M = 160, 147
WIN = 8192            # interior outputs analysed per tone case
M0 = 300              # first FFT-window output of a tone case (6.25 ms in)
EXCL_HZ = 100.0
ALLOWED = {"math", "cmath", "array", "itertools", "functools", "operator",
           "bisect", "collections", "typing", "dataclasses", "fractions",
           "decimal", "abc", "enum", "numbers", "copy", "heapq"}
BANNED_CALLS = {"exec", "eval", "compile", "__import__", "open", "globals",
                "vars", "breakpoint"}


def die(msg):
    print(msg)
    sys.exit(1)


def lint():
    if not os.path.isfile(SUB):
        die("no submission at " + SUB)
    try:
        tree = ast.parse(open(SUB).read())
    except SyntaxError:
        die("lint failed: syntax error")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""] if node.level == 0 else ["."]
        else:
            mods = []
        for name in mods:
            if name.split(".")[0] not in ALLOWED:
                die("lint failed: import of %r is not allowed" % name)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in BANNED_CALLS):
            die("lint failed: call to %s() is not allowed" % node.func.id)


def out_len(n):
    return (n * L + M - 1) // M


# ---------------------------------------------------------------- cases
rng = random.Random(0x196A11)


def noise(n):
    return [rng.uniform(-1.0, 1.0) for _ in range(n)]


cases = []    # dicts sent to the runner, plus validator-only keys
for n in (0, 1, 2, 3, 146, 147, 148, 293, 294, 1000,
          rng.randint(5000, 6000), rng.randint(10000, 12000)):
    cases.append({"mode": "oneshot", "x": noise(n), "check": "len"})

freqs = [0.0, 997.0, 19950.0, 20000.0] + [rng.uniform(20.0, 20000.0)
                                          for _ in range(6)]
NT = 9000
for f in freqs:
    A = rng.uniform(0.1, 1.0)
    ph = rng.uniform(-math.pi, math.pi) if f > 0 else 0.0
    w = 2 * math.pi * f / 44100.0
    x = ([A] * NT if f == 0 else
         [A * math.sin(w * n + ph) for n in range(NT)])
    cases.append({"mode": "oneshot", "x": x, "check": "tone",
                  "f": f, "A": A, "ph": ph})

base_x = noise(20000)


def chunking(n, sizes):
    out, left = [], n
    while left > 0:
        k = min(left, rng.choice(sizes))
        out.append(k)
        left -= k
    return out


streams = [
    (base_x, chunking(20000, [0, 1, 2, 3, 7, 63, 64, 65, 147, 160, 500,
                              1000, 3000])),
    (noise(3000), [1] * 3000),
    (noise(441), [0, 441, 0, 0]),
    (noise(50), [10, 0, 40]),
    ([], [0]),
]
for x, ch in streams:
    pair = len(cases)
    cases.append({"mode": "oneshot", "x": x, "check": "len"})
    cases.append({"mode": "stream", "x": x, "chunks": ch, "check": "stream",
                  "pair": pair})

# perf: 2 s of audio as 8 back-to-back tone segments of 11025 samples (each
# yields exactly 12000 outputs). The tones are fresh (no other case holds
# them) and EVERY output whose dependency window lies inside one segment is
# checked against that segment's tone, so no part of the output is free.
PSEG, NSEG = 11025, 8
perf_x, perf_segs = [], []
for s_i in range(NSEG):
    f = rng.uniform(20.0, 20000.0)
    A = rng.uniform(0.1, 1.0)
    ph = rng.uniform(-math.pi, math.pi)
    w = 2 * math.pi * f / 44100.0
    perf_x += [A * math.sin(w * n + ph) for n in range(PSEG)]
    # phase referred to absolute time 0 (the segment starts at s_i*PSEG)
    perf_segs.append((s_i * PSEG, f, A, ph - w * s_i * PSEG))
cases.append({"mode": "oneshot", "x": perf_x, "check": "perf"})


# ---------------------------------------------------------------- analysis
def fft(a):
    n = len(a)
    a = list(a)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    size = 2
    while size <= n:
        half = size // 2
        wl = cmath.exp(-2j * math.pi / size)
        tw = [wl ** k for k in range(half)]
        for s in range(0, n, size):
            for k in range(half):
                u = a[s + k]
                v = a[s + k + half] * tw[k]
                a[s + k] = u + v
                a[s + k + half] = u - v
        size *= 2
    return a


def i0(x):
    s = t = 1.0
    k = 1
    q = (x / 2.0) ** 2
    while t > 1e-18 * s:
        t *= q / (k * k)
        s += t
        k += 1
    return s


_KB = 14.0
_i0b = i0(_KB)
KWIN = [i0(_KB * math.sqrt(max(0.0, 1 - (2 * k / (WIN - 1) - 1) ** 2))) / _i0b
        for k in range(WIN)]


EDGE = 96            # 2 ms of outputs: interior starts this far from an edge


def tone_check(y, f, A, ph, lo, hi, m0):
    """Judge outputs y[lo:hi] (absolute indices, all interior) against the
    tone A*sin(2*pi*f*t + ph), t = m/48000; the gain/phase fit and the
    Kaiser FFT use the WIN outputs from m0, then every output in lo..hi-1
    is checked sample by sample (the bound gain + phase + spur error allow)."""
    w = 2 * math.pi * f / 48000.0
    seg = y[m0:m0 + WIN]
    if f == 0:
        g, perr = (sum(seg) / WIN) / A, 0.0
    else:
        s = [math.sin(w * (m0 + k)) for k in range(WIN)]
        co = [math.cos(w * (m0 + k)) for k in range(WIN)]
        ss = sum(v * v for v in s)
        cc = sum(v * v for v in co)
        sc = sum(p * q for p, q in zip(s, co))
        ys = sum(p * q for p, q in zip(seg, s))
        yc = sum(p * q for p, q in zip(seg, co))
        det = ss * cc - sc * sc
        a = (ys * cc - yc * sc) / det
        b = (yc * ss - ys * sc) / det
        g = math.hypot(a, b) / A
        perr = abs((math.atan2(b, a) - ph + math.pi) % (2 * math.pi) - math.pi)
    if not (g > 0 and abs(20 * math.log10(g)) <= 0.1):
        return "gain"
    if f > 0 and perr > 2 * math.pi * f * 1e-6:
        return "latency"
    Y = fft([v * k for v, k in zip(seg, KWIN)])
    mags = [abs(Y[k]) for k in range(WIN // 2 + 1)]
    df = 48000.0 / WIN
    near = [mags[k] for k in range(len(mags)) if abs(k * df - f) <= EXCL_HZ]
    far = [mags[k] for k in range(len(mags)) if abs(k * df - f) > EXCL_HZ]
    if max(far) > max(near) * 10 ** (-80 / 20):
        return "stopband"
    # sample accuracy over every interior output
    tol = A * (0.0136 + 2 * math.pi * f * 1e-6)
    for m in range(lo, hi):
        want = A if f == 0 else A * math.sin(w * m + ph)
        if abs(y[m] - want) > tol:
            return "accuracy"
    return None


def main():
    lint()
    payload = json.dumps([{k: c[k] for k in ("mode", "x", "chunks") if k in c}
                          for c in cases])
    try:
        p = subprocess.run([sys.executable, "-B", "-I", "-c", RUNNER, SUB],
                           input=payload.encode(), capture_output=True,
                           timeout=SOL_TIMEOUT_S, cwd="/tmp/eval_resamp196")
    except subprocess.TimeoutExpired:
        die("solution timed out (%.0f s budget for all hidden cases)"
            % SOL_TIMEOUT_S)
    data, pos, results = p.stdout, 0, []

    def take():
        nonlocal pos
        if pos + 9 > len(data):
            return None, None
        tag, n = struct.unpack_from("<cq", data, pos)
        pos += 9
        vals = list(struct.unpack_from("<%dd" % n, data, pos))
        pos += 8 * n
        return tag, vals

    for c in cases:
        tag, vals = take()
        counts = None
        if tag == b"C":
            counts = vals
            tag, vals = take()
        results.append((tag, vals, counts))

    for i, (c, (tag, y, counts)) in enumerate(zip(cases, results)):
        if tag is None:
            die("case %d failed (crash: the solution process died)" % i)
        if tag == b"T":
            die("case %d failed (type)" % i)
        if tag == b"E":
            die("case %d failed (exception)" % i)
        n = len(c["x"])
        if len(y) != out_len(n):
            die("case %d failed (length)" % i)
        if c["check"] == "tone":
            kind = tone_check(y, c["f"], c["A"], c["ph"], EDGE,
                              len(y) - EDGE, M0)
            if kind:
                die("case %d failed (%s)" % (i, kind))
        elif c["check"] == "stream":
            ref = results[c["pair"]][1]
            if any(abs(p - q) > 1e-9 for p, q in zip(y, ref)):
                die("case %d failed (stream)" % i)
            tot = 0
            for k, have in zip(c["chunks"], counts):
                tot += k
                need = 0 if tot <= 64 else out_len(tot - 64)
                if have < need:
                    die("case %d failed (lookahead)" % i)
        elif c["check"] == "perf":
            for s0, f, A, ph in perf_segs:
                lo = (L * s0 + M - 1) // M + EDGE
                hi = (L * (s0 + PSEG) + M - 1) // M - EDGE
                kind = tone_check(y, f, A, ph, lo, hi, lo + 204)
                if kind:
                    die("case %d failed (%s)" % (i, kind))
    print("OK: %d hidden cases passed" % len(cases))


main()
