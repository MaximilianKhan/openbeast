#!/usr/bin/env python3
"""E36-D calibration texts (PLAN.md §D). Rebuilds every DERIVED calibration
file from its sources and checks it against SHA256SUMS. Never reads an
evaluation text except to prove disjointness.

Sources (all train-side; never an eval file):
  wiki  = data/wikitext-2-raw/wiki.train.raw            (eval: wiki.test.raw)
  code  = experiments/17-task-conditioned/code-train.txt (eval: code-test.txt)
  fw    = d08b/calib/fineweb-edu-calib.txt — sample-10BT rows 2000..2647
          (eval: E32 fineweb-edu-control.txt = rows 0..399); built by
          fetch_fineweb_calib.py (network), verified here by sha256
  mix2  = data/mixed-calib2.txt — E22's 2:1 wiki:code interleave, built by
          experiments/22-qwen35-08b/build_mixed_calib.sh (reused, not rebuilt)

Derived here:
  mix111.txt  1:1:1 wiki:code:fw interleave, E22 slicing style: the first
              1,000,000 bytes of each source, 2000-byte slices in turn
              (wiki, code, fw), raw byte slicing exactly as
              build_mixed_calib.sh does it.
  wiki-train-third-{a,b,c}.txt  T1.15: wiki.train cut at byte offsets
              n/3 and 2n/3, each cut moved FORWARD to the next newline so
              no slice starts mid-line / mid-UTF-8 sequence (moves each
              boundary by < 1 line; the slices stay disjoint and cover
              the file).

usage: python3 build_calib.py [--check-tokens]
  --check-tokens  count tokens with llama-tokenize (CPU, vocab only) and
                  require >= 40 x 512 + 1 per T1.15 slice (and every arm).
"""
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
L = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
OB = os.path.abspath(os.path.join(L, "..", ".."))
WIKI = os.path.join(L, "data/wikitext-2-raw/wiki.train.raw")
CODE = os.path.join(L, "experiments/17-task-conditioned/code-train.txt")
FW = os.path.join(HERE, "fineweb-edu-calib.txt")
MIX2 = os.path.join(L, "data/mixed-calib2.txt")
EVALS = {
    "wiki-test": os.path.join(L, "data/wikitext-2-raw/wiki.test.raw"),
    "code-test": os.path.join(L, "experiments/17-task-conditioned/code-test.txt"),
    "fw-eval": os.path.join(L, "experiments/32-t117-gsq-head-to-head/"
                               "fineweb-edu-control.txt"),
}
NEED_TOKENS = 40 * 512 + 1


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def build_mix111():
    w = open(WIKI, "rb").read()[:1_000_000]
    c = open(CODE, "rb").read()[:1_000_000]
    f = open(FW, "rb").read()[:1_000_000]
    out = bytearray()
    i = 0
    while i < max(len(w), len(c), len(f)):
        out += w[i:i + 2000]
        out += c[i:i + 2000]
        out += f[i:i + 2000]
        i += 2000
    p = os.path.join(HERE, "mix111.txt")
    open(p, "wb").write(bytes(out))
    return p


def build_thirds():
    b = open(WIKI, "rb").read()
    n = len(b)
    cuts = [0]
    for k in (1, 2):
        c = n * k // 3
        c = b.index(b"\n", c) + 1          # forward to the next line start
        cuts.append(c)
    cuts.append(n)
    out = []
    for name, lo, hi in zip("abc", cuts[:-1], cuts[1:]):
        p = os.path.join(HERE, f"wiki-train-third-{name}.txt")
        open(p, "wb").write(b[lo:hi])
        out.append((p, lo, hi))
    return out


def disjoint(cal_path):
    """no 200-byte window sampled from the calibration file occurs in any
    eval text (cheap containment probe; the sources are disjoint by
    construction, this guards against a wrong path)."""
    cal = open(cal_path, "rb").read()
    hits = 0
    for name, ep in EVALS.items():
        ev = open(ep, "rb").read()
        for k in range(0, max(1, len(cal) - 200), 50_000):
            if cal[k:k + 200] in ev:
                hits += 1
    return hits


def tokens(p):
    tok = os.path.join(OB, "llama.cpp/build/bin/llama-tokenize")
    m = os.path.join(OB, "weights/research-staging/Qwen3.5-0.8B-BF16.gguf")
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="")
    r = subprocess.run([tok, "-m", m, "-f", p, "--show-count", "--ids",
                        "--log-disable"], capture_output=True, text=True,
                       env=env, check=True)
    for line in r.stdout.splitlines()[::-1]:
        if line.startswith("Total number of tokens:"):
            return int(line.split(":")[1])
    raise RuntimeError("no token count")


def main():
    check_tokens = "--check-tokens" in sys.argv
    if not os.path.exists(FW):
        sys.exit(f"missing {FW}: run fetch_fineweb_calib.py first")
    mix = build_mix111()
    thirds = build_thirds()
    rows = [("wiki", WIKI), ("code", CODE), ("fw", FW), ("mix2", MIX2),
            ("mix111", mix)] + [(f"third-{os.path.basename(p)[-5]}", p)
                                for p, _, _ in thirds]
    for p, lo, hi in thirds:
        print(f"{os.path.basename(p)}: wiki.train bytes [{lo}, {hi})")
    lines = []
    for arm, p in rows:
        h = sha(p)
        sz = os.path.getsize(p)
        dj = disjoint(p)
        msg = f"{arm:8s} {sz:>10d} B  sha256 {h}  eval-window hits {dj}"
        if check_tokens:
            nt = tokens(p)
            msg += f"  tokens {nt}" + ("" if nt >= NEED_TOKENS else "  TOO FEW")
            if nt < NEED_TOKENS:
                sys.exit(msg)
        if dj:
            sys.exit(msg + "  <- calibration text found in an eval text")
        print(msg)
        lines.append(f"{h}  {os.path.relpath(p, L)}")
    sums = os.path.join(HERE, "SHA256SUMS")
    new = "\n".join(lines) + "\n"
    if os.path.exists(sums) and open(sums).read() != new:
        sys.exit(f"rebuilt files differ from {sums} — refusing to overwrite;"
                 " inspect before replacing")
    open(sums, "w").write(new)
    print(f"ok: {sums} (paths relative to research/lowrank)")


if __name__ == "__main__":
    main()
