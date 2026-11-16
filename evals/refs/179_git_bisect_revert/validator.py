# Validator for 179_git_bisect_revert (embedded in the task JSON by build_task.py;
# the @@...@@ constants are filled in from the deterministic fixture).
#
# Grades ONLY the committed state of main:
#   1. main checked out, no bisect in progress
#   2. main = exactly one new non-merge commit whose parent is the original tip
#   3. its message names the culprit's full SHA and no other history SHA
#   4. its tree differs from the tip only in semver.sh, and only inside the
#      culprit hunk's block: new == old[:R1] + X + old[R2:]
#   5. the committed tests/run.sh passes
#   6. ~330 seeded hidden cases (compare/sort/max/satisfies/filter, prerelease
#      heavy, numeric identifiers up to 26 digits) match a SemVer 2.0.0 oracle;
#      a mismatch reports only the case index
#
# Isolation (evals/README.md, "Validator isolation"): this script arrives on
# stdin; the committed code runs only through vlib.run_solution (own session,
# CPU budget over the whole tree, scrubbed env) and is judged by its output.
# Every limit is written at scale 1 and scaled by OPENBEAST_TIMEOUT_SCALE.
# Both solution runs share one wall budget, so a validation stays inside the
# 15 s authoring budget at scale 1 even when the suite is slow AND the hidden
# run hangs.
import os, random, re, shutil, subprocess, sys, tempfile, time
import vlib

REPO = "/tmp/eval_bisect/semver"
ORIG = "@@ORIG@@"
BAD = "@@BAD@@"
R1, R2 = @@R1@@, @@R2@@          # culprit block = lines [R1, R2) of ORIG:semver.sh (0-based)
SUITE_CPU_S = 6.0   # per run, scale 1, whole process tree (a sane bash fix: ~1 s)
HIDDEN_CPU_S = 6.0
SOLUTION_WALL_S = 11.0   # suite + hidden runs together, scale 1
GENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
GENV.update(GIT_NO_REPLACE_OBJECTS="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0", LC_ALL="C")


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def git(*args, raw=False):
    p = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", REPO] + list(args), env=GENV,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                       timeout=vlib.t(20))
    if p.returncode != 0:
        return None
    return p.stdout if raw else p.stdout.decode("utf-8", "replace").strip()


_deadline = [None]   # monotonic end of the shared solution wall budget


def run_committed(argv, cwd, data=None, cpu_s=6.0):
    """Run committed code via vlib.run_solution: own session and process group,
    CPU budget over every descendant (bash forks for $(...)), scrubbed env.
    Returns (rc, stdout, stderr), or (None, verdict, b"") over a budget."""
    now = time.monotonic()
    if _deadline[0] is None:
        _deadline[0] = now + vlib.t(SOLUTION_WALL_S)
    wall = max(0.5, (_deadline[0] - now) / vlib.scale())   # run_solution rescales it
    r = vlib.run_solution(argv, input=data, cpu_s=cpu_s, wall_s=wall, cwd=cwd, text=False,
                          allow_fork=True, env=vlib.clean_env({"LC_ALL": "C"}))
    if r.verdict != "ok":
        return None, r.verdict, b""
    return r.returncode, r.stdout or b"", r.stderr or b""


# ---- 1. repository state
if not os.path.isdir(REPO) or git("rev-parse", "--git-dir") is None:
    fail("no git repository at " + REPO)
if git("cat-file", "-t", ORIG) != "commit":
    fail("the original history is gone (rewritten or garbage-collected)")
gd = git("rev-parse", "--absolute-git-dir") or os.path.join(REPO, ".git")
if os.path.exists(os.path.join(gd, "BISECT_START")) or git("for-each-ref", "refs/bisect/"):
    fail("a git bisect session is still in progress (run git bisect reset)")
if git("symbolic-ref", "-q", "HEAD") != "refs/heads/main":
    fail("main is not checked out (HEAD is detached or on another branch)")

# ---- 2. exactly one new commit on top of the original tip
new = git("rev-parse", "--verify", "-q", "refs/heads/main^{commit}")
if not new or new == ORIG:
    fail("no new commit on main")
raw = git("cat-file", "commit", new, raw=True).decode("utf-8", "replace")
head, _, message = raw.partition("\n\n")
parents = [ln.split()[1] for ln in head.splitlines() if ln.startswith("parent ")]
if parents != [ORIG]:
    fail("main must be exactly one new non-merge commit on top of the original tip "
         "(history was rewritten, or more than one commit was added)")

# ---- 3. the message names the culprit, and only the culprit
history = set((git("rev-list", ORIG) or "").split())
named = {h.lower() for h in re.findall(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{40}(?![0-9A-Fa-f])", message)}
named &= history
if BAD not in named:
    fail("the commit message does not name the first bad commit (full 40-character SHA)")
if named - {BAD}:
    fail("the commit message names other commits of the history besides the first bad one")


# ---- 4. only semver.sh, only the culprit block
def tree(rev):
    out = {}
    for ln in (git("ls-tree", "-r", "--full-tree", rev) or "").splitlines():
        meta, _, path = ln.partition("\t")
        mode, typ, sha = meta.split()
        out[path] = (mode, sha)
    return out


t_old, t_new = tree(ORIG), tree(new)
changed = sorted(p for p in set(t_old) | set(t_new) if t_old.get(p) != t_new.get(p))
if changed != ["semver.sh"] or t_old["semver.sh"][0] != t_new["semver.sh"][0]:
    fail("the commit must change semver.sh and nothing else (changed: %s)" % (", ".join(changed[:6]) or "nothing"))
old = git("cat-file", "blob", t_old["semver.sh"][1], raw=True).split(b"\n")
cur = git("cat-file", "blob", t_new["semver.sh"][1], raw=True).split(b"\n")
tail = len(old) - R2
if not (len(cur) >= R1 + tail and cur[:R1] == old[:R1] and (tail == 0 or cur[len(cur) - tail:] == old[R2:])):
    fail("the diff touches lines outside the offending hunk")

# ---- 5. visible suite on the committed tree
work = tempfile.mkdtemp(prefix="v179_")
try:
    for path, (mode, sha) in t_new.items():
        if mode == "160000":
            continue
        full = os.path.join(work, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(git("cat-file", "blob", sha, raw=True))
    rc, out, err = run_committed(["bash", "tests/run.sh"], work, cpu_s=SUITE_CPU_S)
    if rc is None:
        fail("tests/run.sh timed out on the committed tree (%s budget)" % out)
    if rc != 0:
        last = (out + err).decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        fail("tests/run.sh fails on the committed tree (%s)" % last[0][:60])

    # ---- 6. hidden seeded cases against a SemVer 2.0.0 oracle
    rng = random.Random(0x179B15EC)
    ALNUM = ["alpha", "beta", "rc", "RC", "Alpha", "x", "x-y", "-", "a1", "1a", "0a", "z9", "beta-2", "B"]

    def ident():
        r = rng.random()
        if r < 0.50:
            return str(rng.randrange(0, 40))
        if r < 0.62:
            return str(rng.randrange(10 ** 14, 10 ** 15)) if rng.random() < 0.5 else str(rng.randrange(1, 10 ** rng.randrange(16, 27)))
        if r < 0.70:
            return str(rng.choice([9, 10, 99, 100, 999, 1000, 1, 2, 11, 19, 20]))
        return rng.choice(ALNUM)

    def pre(n=None):
        n = n if n is not None else rng.choice([1, 1, 2, 2, 2, 3])
        return ".".join(ident() for _ in range(n))

    def version(core=None):
        core = core or "%d.%d.%d" % (rng.randrange(0, 3), rng.randrange(0, 3), rng.randrange(0, 3))
        v = core
        if rng.random() < 0.8:
            v += "-" + pre()
        if rng.random() < 0.15:
            v += "+" + rng.choice(["b1", "sha.5114f85", "001", "exp-2"])
        return v

    def pair():
        a = version()
        if rng.random() < 0.7:   # same core, shared prefix: exercises the identifier loop
            core = a.split("-")[0].split("+")[0]
            ids = a.split("+")[0].split("-", 1)[1].split(".") if "-" in a.split("+")[0] else []
            keep = ids[:rng.randrange(0, len(ids) + 1)]
            ids2 = keep + [ident() for _ in range(rng.randrange(0 if keep else 1, 3))]
            b = core + ("-" + ".".join(ids2) if ids2 else "")
        else:
            b = version()
        return a, b

    def parse(v):
        v = v.split("+", 1)[0]
        core, _, p = v.partition("-")
        return tuple(int(x) for x in core.split(".")), (p.split(".") if p else [])

    def cmp(a, b):
        (ca, pa), (cb, pb) = parse(a), parse(b)
        if ca != cb:
            return -1 if ca < cb else 1
        if not pa or not pb:
            return (len(pb) > 0) - (len(pa) > 0) if (pa or pb) else 0
        for x, y in zip(pa, pb):
            nx, ny = x.isdigit(), y.isdigit()
            if nx and ny:
                if int(x) != int(y):
                    return -1 if int(x) < int(y) else 1
            elif nx or ny:
                return -1 if nx else 1
            elif x != y:
                return -1 if x.encode() < y.encode() else 1
        return (len(pa) > len(pb)) - (len(pa) < len(pb))

    def isort(vs):
        out = []
        for v in vs:                     # stable insertion sort == the tool's
            i = len(out)
            while i > 0 and cmp(out[i - 1], v) == 1:
                i -= 1
            out.insert(i, v)
        return out

    OPS = {">": lambda c: c == 1, ">=": lambda c: c != -1, "<": lambda c: c == -1,
           "<=": lambda c: c != 1, "=": lambda c: c == 0, "": lambda c: c == 0}

    def comparator():
        op = rng.choice([">", ">=", "<", "<=", "=", ""])
        return op, version()

    def rng_range():
        sets = [[comparator() for _ in range(rng.choice([1, 1, 2]))] for _ in range(rng.choice([1, 1, 2]))]
        text = " || ".join(" ".join(op + v for op, v in s) for s in sets)
        return sets, text

    def sat(v, sets):
        return any(all(OPS[op](cmp(v, w)) for op, w in s) for s in sets)

    cases = []   # (argv, expected stdout)
    for _ in range(200):
        a, b = pair()
        cases.append((["compare", a, b], str(cmp(a, b))))
    for k in range(45):
        vs = [version() for _ in range(rng.randrange(3, 9))]
        if rng.random() < 0.5:
            vs += [pair()[1] for _ in range(2)]
        rng.shuffle(vs)
        s = isort(vs)
        if k % 3 == 0:
            cases.append((["sort", "-r"] + vs, "\n".join(reversed(s))))
        else:
            cases.append((["sort"] + vs, "\n".join(s)))
    for _ in range(30):
        vs = [version() for _ in range(rng.randrange(2, 7))]
        best = vs[0]
        for v in vs[1:]:
            if cmp(v, best) == 1:
                best = v
        cases.append((["max"] + vs, best))
    for _ in range(40):
        v = version()
        sets, text = rng_range()
        cases.append((["satisfies", v, text], "yes" if sat(v, sets) else "no"))
    for _ in range(15):
        sets, text = rng_range()
        vs = [version() for _ in range(rng.randrange(3, 8))]
        cases.append((["filter", text] + vs, "\n".join(v for v in vs if sat(v, sets))))

    driver = os.path.join(work, ".hidden_driver.sh")
    with open(driver, "w") as f:
        f.write(". ./semver.sh\n"
                "while IFS=$'\\x1f' read -r -a args; do\n"
                "    out=$(main \"${args[@]}\" 2>/dev/null </dev/null)\n"
                "    printf '%s\\x1e' \"$out\"\n"
                "done\n")
    data = "".join("\x1f".join(argv) + "\n" for argv, _ in cases).encode()
    rc, out, _ = run_committed(["bash", driver], work, data=data, cpu_s=HIDDEN_CPU_S)
    if rc is None:
        fail("hidden tests timed out (%s budget)" % out)
    got = out.decode("utf-8", "replace").split("\x1e")
    for i, (_, want) in enumerate(cases):
        if i >= len(got) - 1 or got[i].strip("\n") != want:
            fail("hidden case %d mismatch" % i)
finally:
    shutil.rmtree(work, ignore_errors=True)
print("OK: culprit named, minimal diff, visible suite and %d hidden cases pass" % len(cases))
