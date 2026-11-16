# Validator body for 196_polyphase_resampler (build.py prepends RUNNER = ...).
# Every case is generated here from a fixed seed; nothing is written to the
# fixture dir. A failure reports only the case index and the failure kind.
#
# Isolation: the submission is imported only by RUNNER, in separate processes
# started through vlib.run_solution (source on stdin, never `-c`), with one
# CPU budget shared by all of them and counted over each process tree.
# RUNNER gets the case INPUTS only (a file in this validator's private cwd);
# the tone parameters, the expected signals and every check stay in this
# process. So the submission can fake nothing but its own outputs, and every
# output that carries a signal is checked against the analytic answer.
#
# The cases run in GROUPS, one process each, so that no case can lean on
# another: a stream never shares a process with the one-shot run of its
# input (no cache replay), the two halves of a locality pair run in
# different processes (no copying the unperturbed run), and every stream is
# also run as PREFIX cases, each in a process that is given only the samples
# up to its cut, so its outputs at the cut are checked against data that
# process never had (no peeking ahead, however the peek is done).
import ast, cmath, json, math, os, random, struct, sys, tempfile
import vlib

SUB = "/tmp/eval_resamp196/resample.py"
CPU_S = 8.0           # CPU budget for all hidden cases at scale 1 (ref ~2-3 s)
L, M = 160, 147
WIN = 8192            # interior outputs analysed per tone case
M0 = 300              # first FFT-window output of a tone case (6.25 ms in)
EXCL_HZ = 100.0
WINDOW = 64           # y[m] may depend on x[n] only for |n - 147m/160| <= 64
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


def tones(k_lo, k_hi):
    """A random sum of k passband tones, total amplitude <= 1 (so |x| <= 1)."""
    k = rng.randint(k_lo, k_hi)
    raw = [rng.uniform(0.2, 1.0) for _ in range(k)]
    tot = rng.uniform(0.6, 1.0)
    return [(rng.uniform(20.0, 20000.0), A * tot / sum(raw),
             rng.uniform(-math.pi, math.pi)) for A in raw]


def synth(tl, n0, n1):
    """x[n] for n in n0..n1-1 of the tone sum tl (phases referred to n = 0)."""
    ws = [(2 * math.pi * f / 44100.0, A, ph) for f, A, ph in tl]
    return [sum(A * math.sin(w * n + ph) for w, A, ph in ws)
            for n in range(n0, n1)]


cases = []    # dicts sent to the runner (inputs only), plus validator-only keys
for n in (0, 1, 2, 3, 146, 147, 148, 293, 294):
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

# longer length cases carry band-limited multi-tone content, checked sample
# by sample, so a solution cannot return the right length of anything
for n in (1000, rng.randint(5000, 6000), rng.randint(10000, 12000)):
    tl = tones(3, 8)
    cases.append({"mode": "oneshot", "x": synth(tl, 0, n), "check": "multi",
                  "tones": tl})

# locality pair: the same multi-tone signal, then with x[a:b] replaced by
# other content. Every output whose 64-sample window misses a..b-1, on the
# past side as well as the future side, must not change.
NLOC = 6000
loc_t = tones(4, 8)
loc_x = synth(loc_t, 0, NLOC)
la = rng.randint(1800, 2400)
lb = la + rng.randint(400, 700)
loc_x2 = loc_x[:la] + synth(tones(3, 6), la, lb) + loc_x[lb:]
cases.append({"mode": "oneshot", "x": loc_x, "check": "multi", "tones": loc_t,
              "proc": "LA"})
cases.append({"mode": "oneshot", "x": loc_x2, "check": "local",
              "pair": len(cases) - 1, "region": (la, lb), "proc": "LB"})


def chunking(n, sizes):
    out, left = [], n
    while left > 0:
        k = min(left, rng.choice(sizes))
        out.append(k)
        left -= k
    return out


SIZES = [0, 1, 2, 3, 7, 63, 64, 65, 147, 160, 500, 1000, 3000]
# (x, chunks, check spec). Each stream is paired with a one-shot run of the
# same x; BOTH outputs are held to the content check, and the stream must
# also equal the one-shot output to 1e-9 and meet the lookahead rule.
tl_a, tl_b, tl_c = tones(5, 10), tones(3, 8), tones(2, 5)
st_f, st_A, st_ph = rng.uniform(20.0, 20000.0), rng.uniform(0.1, 1.0), \
    rng.uniform(-math.pi, math.pi)
st_w = 2 * math.pi * st_f / 44100.0
streams = [
    (synth(tl_a, 0, 20000), chunking(20000, SIZES),
     {"check": "multi", "tones": tl_a}),
    (synth(tl_b, 0, 3000), [1] * 3000, {"check": "multi", "tones": tl_b}),
    (synth(tl_c, 0, 441), [0, 441, 0, 0], {"check": "multi", "tones": tl_c}),
    (noise(50), [10, 0, 40], {"check": "len"}),
    ([], [0], {"check": "len"}),
    ([st_A * math.sin(st_w * n + st_ph) for n in range(NT)],
     chunking(NT, SIZES), {"check": "tone", "f": st_f, "A": st_A, "ph": st_ph}),
]
for x, ch, spec in streams:
    pair = len(cases)
    cases.append(dict({"mode": "oneshot", "x": x}, **spec))
    cases.append(dict({"mode": "stream", "x": x, "chunks": ch, "pair": pair,
                       "proc": "S"}, **spec))

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
cases.append({"mode": "oneshot", "x": perf_x, "check": "perf", "proc": "F"})

# second locality pair: noise with ONE sample changed; each half in its own
# process (LA / LB). Every output whose 64-sample window misses that sample
# must not move, which pins the window exactly on both sides.
NLOC2 = 4000
loc2_x = noise(NLOC2)
l2 = rng.randint(1500, 2500)
loc2_x2 = list(loc2_x)
loc2_x2[l2] = -loc2_x[l2] if abs(loc2_x[l2]) > 0.3 else 0.9
cases.append({"mode": "oneshot", "x": loc2_x, "check": "len", "proc": "LA"})
cases.append({"mode": "oneshot", "x": loc2_x2, "check": "local",
              "pair": len(cases) - 1, "region": (l2, l2 + 1), "proc": "LB"})

# stream prefixes: stream s fed only its first j chunks, no flush, in a
# process that holds nothing past the cut (P1..P3 take one cut per stream).
# The outputs returned by then must meet the lookahead rule and equal the
# full one-shot output, so they cannot use samples the process never got.
for s_i, ncut in ((25, 3), (27, 3), (35, 3), (29, 1)):
    sc = cases[s_i]
    ch, tots, tot = sc["chunks"], [], 0
    for k in ch:
        tot += k
        tots.append(tot)
    if ncut == 1:
        cuts = [2]
    else:
        n = len(sc["x"])
        ok = [j + 1 for j, t_ in enumerate(tots) if 200 <= t_ <= n - 200]
        cuts = sorted(rng.sample(ok, ncut))
    for g, j in enumerate(cuts):
        cases.append({"mode": "prefix", "x": sc["x"][:tots[j - 1]],
                      "chunks": ch[:j], "pair": sc["pair"], "check": "len",
                      "proc": "P%d" % (g + 1)})
GROUPS = ["G", "LA", "LB", "S", "P1", "P2", "P3", "F"]


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


def multi_check(y, tl, lo, hi):
    """Every output in lo..hi-1 against the tone sum tl, within the sum of
    the per-tone sample-accuracy bounds (what superposition gives a linear
    resampler that meets the single-tone bound)."""
    ws = [(2 * math.pi * f / 48000.0, A, ph) for f, A, ph in tl]
    tol = sum(A * (0.0136 + 2 * math.pi * f * 1e-6) for f, A, ph in tl)
    for m in range(lo, hi):
        want = sum(A * math.sin(w * m + ph) for w, A, ph in ws)
        if abs(y[m] - want) > tol:
            return "accuracy"
    return None


def content(c, y):
    """The content check named by c (tone / multi / len) on output y."""
    if c["check"] == "tone":
        return tone_check(y, c["f"], c["A"], c["ph"], EDGE, len(y) - EDGE, M0)
    if c["check"] == "multi":
        return multi_check(y, c["tones"], EDGE, len(y) - EDGE)
    return None


def main():
    lint()
    results = [None] * len(cases)
    used = 0.0                       # CPU seconds, all groups so far
    sc = vlib.scale()
    for g in GROUPS:
        idx = [i for i, c in enumerate(cases) if c.get("proc", "G") == g]
        left = CPU_S - used / sc     # unscaled; run_solution scales it
        if left <= 0:
            die("solution timed out (%.0f s CPU budget for all hidden cases)"
                % vlib.t(CPU_S))
        tmp = tempfile.mkdtemp(prefix="rs196-", dir=os.getcwd())
        path = os.path.join(tmp, "cases.json")
        with open(path, "w") as fh:
            json.dump([{k: cases[i][k] for k in ("mode", "x", "chunks")
                        if k in cases[i]} for i in idx], fh)
        try:
            r = vlib.run_solution([sys.executable, "-I", "-S", "-", SUB],
                                  input="CASES = %r\n" % path + RUNNER,
                                  cpu_s=left, wall_s=3 * left + 10,
                                  cwd="/tmp/eval_resamp196", text=False)
        finally:
            os.unlink(path)
            os.rmdir(tmp)
        used += r.cpu_s
        if r.verdict in ("cpu", "wall"):
            die("solution timed out (%.0f s CPU budget for all hidden cases; "
                "%s)" % (vlib.t(CPU_S), r.reason))
        if r.verdict != "ok":
            die("solution process refused (%s)" % r.verdict)
        data, pos = bytes(r.stdout), 0

        def take():
            nonlocal pos
            if pos + 9 > len(data):
                return None, None
            tag, n = struct.unpack_from("<cq", data, pos)
            pos += 9
            if n < 0 or pos + 8 * n > len(data):
                return None, None
            vals = list(struct.unpack_from("<%dd" % n, data, pos))
            pos += 8 * n
            return tag, vals

        for i in idx:
            tag, vals = take()
            counts = None
            if tag == b"C":
                counts = vals
                tag, vals = take()
            results[i] = (tag, vals, counts)

    for i, (c, (tag, y, counts)) in enumerate(zip(cases, results)):
        if tag is None:
            die("case %d failed (crash: the solution process died)" % i)
        if tag == b"T":
            die("case %d failed (type)" % i)
        if tag == b"E":
            die("case %d failed (exception)" % i)
        if tag != b"Y":
            die("case %d failed (crash: bad runner output)" % i)
        n = len(c["x"])
        if c["mode"] != "prefix" and len(y) != out_len(n):
            die("case %d failed (length)" % i)
        if c["mode"] == "prefix":
            if counts is None or len(counts) != len(c["chunks"]) \
                    or counts[-1] != len(y):
                die("case %d failed (crash: bad runner output)" % i)
            ref = results[c["pair"]][1]
            if len(y) > len(ref) or any(abs(p - q) > 1e-9
                                        for p, q in zip(y, ref)):
                die("case %d failed (stream)" % i)
            tot = 0
            for k, have in zip(c["chunks"], counts):
                tot += k
                need = 0 if tot <= WINDOW else out_len(tot - WINDOW)
                if have < need:
                    die("case %d failed (lookahead)" % i)
        if c["mode"] == "stream":
            if counts is None or len(counts) != len(c["chunks"]):
                die("case %d failed (crash: bad runner output)" % i)
            ref = results[c["pair"]][1]
            if any(abs(p - q) > 1e-9 for p, q in zip(y, ref)):
                die("case %d failed (stream)" % i)
            tot = 0
            for k, have in zip(c["chunks"], counts):
                tot += k
                need = 0 if tot <= WINDOW else out_len(tot - WINDOW)
                if have < need:
                    die("case %d failed (lookahead)" % i)
        if c["check"] in ("tone", "multi"):
            kind = content(c, y)
            if kind:
                die("case %d failed (%s)" % (i, kind))
        elif c["check"] == "local":
            ref = results[c["pair"]][1]
            ra, rb = c["region"]
            for m in range(len(y)):
                # output m may see x[a..b-1] only if 147m/160 is within 64
                if (M * m < L * (ra - WINDOW) or M * m > L * (rb - 1 + WINDOW)) \
                        and abs(y[m] - ref[m]) > 1e-9:
                    die("case %d failed (locality)" % i)
        elif c["check"] == "perf":
            for s0, f, A, ph in perf_segs:
                lo = (L * s0 + M - 1) // M + EDGE
                hi = (L * (s0 + PSEG) + M - 1) // M - EDGE
                kind = tone_check(y, f, A, ph, lo, hi, lo + 204)
                if kind:
                    die("case %d failed (%s)" % (i, kind))
    print("OK: %d hidden cases passed" % len(cases))


main()
