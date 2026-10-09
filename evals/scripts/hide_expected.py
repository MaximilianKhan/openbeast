#!/usr/bin/env python3
"""Keep the answer out of the agent's working directory (suite v4.1).

In v4, 132 variant units (22 base tasks x 6 languages) wrote
`expected.txt` next to `input.txt` at SETUP and validated with a `diff`
against it on that one input. A solution that prints the file's contents
passed 21 of the 22 Python variants.

This tool rewrites those units mechanically. For every variant of every
base task in HIDDEN below:

  setup         writes the SAMPLE input (and check.py where there is one),
                and no longer writes expected.txt.
  pre_validate  (run by the harness after the agent exits, before the
                validator) re-asserts setup, then overwrites input.txt with
                the sample cases PLUS the hidden cases below and writes the
                matching expected.txt.
  validation    is unchanged: one compile, one run, one diff/check.
  task          gains one sentence saying so (promise <-> assert parity).

The expected output is not typed in by hand: it is what the Python
reference solution (`evals/refs/<stem>.py`) prints for the combined input.
Before trusting the reference as the oracle, the tool checks that it
reproduces the v4 expected.txt on the sample. `tests/audit_variants.py`
then proves the other five languages' references agree on the hidden cases.

Hidden cases stay inside the contract the task text states AND inside the
value ranges the sample already shows (no negative numbers where the
sample has none, nothing wider than the sample's integers unless the text
promises it), so a solution that is correct for the documented format is
not failed on a case the text never promised.

    python3 evals/scripts/hide_expected.py            # rewrite evals/tasks in place (idempotent)
    python3 evals/scripts/hide_expected.py --check    # exit 1 if any spec would change

Adding hidden cases to another variant task: add an entry to HIDDEN and run
the tool, then `python3 tests/audit_variants.py <base_id>`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TASKS = REPO / "evals" / "tasks"
REFS = REPO / "evals" / "refs"

DELIM = "OB_HIDDEN_EOF"
BEGIN = "# --- validation-time cases (never in the agent's working directory) ---"

NOTE = (" A sample input is provided at {d}/input.txt; no expected-output file is"
        " provided. Validation runs your program on that sample plus additional"
        " hidden cases in the same format and compares the output, so implement"
        " the general algorithm rather than printing fixed answers.")


# --- how the hidden cases join the sample ---------------------------------
def t_cases(extra_cases: int, body: str):
    """Sample's line 1 is a case count T: add `extra_cases` and append `body`."""
    def merge(sample: str) -> str:
        first, rest = sample.split("\n", 1)
        return f"{int(first) + extra_cases}\n{rest}{body}"
    return merge


def more_lines(body: str):
    """No header: every line is a case. Append `body`."""
    return lambda sample: sample + body


def uf_merge(new_n: int, body: str):
    """52_unionfind: one instance, header 'N Q'. Grow N, add the ops."""
    def merge(sample: str) -> str:
        first, rest = sample.split("\n", 1)
        _n, q = first.split()
        return f"{new_n} {int(q) + body.count(chr(10))}\n{rest}{body}"
    return merge


# base task id -> (reference stem, merge function)
HIDDEN = {
    "100_constant_time_compare": ("ct", t_cases(7,
        "secret\nsecret\n"
        "secret\nsecres\n"
        "Secret\nsecret\n"
        "abc\nab\n"
        "x\n\n"
        "0123456789\n0123456780\n"
        "tok3n-AAAA\ntok3n-AAAA\n")),
    "11_bst": ("bst", t_cases(4,
        "6\n50 30 70 20 40 60\n"
        "5\n9 7 5 3 1\n"
        "8\n1 2 3 4 5 6 7 8\n"
        "9\n12 4 12 20 4 16 0 20 8\n")),
    "123_nbody": ("nbody", t_cases(1,
        "3 0.01 50\n"
        "2.0 0.0 0.0 0.0 0.0\n"
        "1.0 3.0 0.0 0.0 0.8\n"
        "1.0 -3.0 0.0 0.0 -0.8\n")),
    "127_aes_keysched": ("aes", t_cases(3,
        "00000000000000000000000000000000\n"
        "ffffffffffffffffffffffffffffffff\n"
        "6920e299a5202a6d656e636869746f2a\n")),
    "136_gf256": ("gf", t_cases(8,
        "+ 0 0\n"
        "+ 0x0F 0xF0\n"
        "+ 0xAB 12\n"
        "* 0 77\n"
        "* 3 7\n"
        "* 0x57 0x83\n"
        "* 200 100\n"
        "* 255 2\n")),
    "148_convex_hull": ("hull", t_cases(3,
        "5\n0 0\n5 0\n0 5\n1 1\n2 2\n"
        "9\n0 0\n3 0\n6 0\n6 3\n6 6\n3 6\n0 6\n0 3\n3 3\n"
        "7\n1 0\n5 0\n7 3\n4 7\n0 4\n3 3\n4 2\n")),
    "19_three_way_quicksort": ("qs", t_cases(4,
        "1\n42\n"
        "9\n9 8 7 6 5 4 3 2 1\n"
        "12\n4 -2 4 0 -2 11 4 0 -7 11 4 -2\n"
        "6\n2 2 2 1 1 1\n")),
    "20_priority_queue": ("pq", t_cases(2,
        "9\np 7\np 7\np 2\no\np 1\no\no\no\no\n"
        "8\no\np 40\np 10\np 30\no\np 20\no\no\n")),
    "27_brainfuck_interpreter": ("bf", t_cases(4,
        ",.,.,.\nabc\n"
        "++++++[>++++++++++<-]>+++++.\n\n"
        ",>,<.>.\nxy\n"
        "++[>+++[>++++++<-]<-]>>+.\n\n")),
    "31_is_power_of_two": ("pow2", more_lines(
        "32\n64\n6\n7\n1024\n1023\n536870912\n536870911\n-8\n-1024\n12\n65536\n65535\n")),
    "32_dot_product": ("dot", t_cases(4,
        "1\n7\n-3\n"
        "6\n3 -1 4 -1 5 -9\n2 7 -1 8 -2 8\n"
        "2\n2000000 3000000\n3000000 2000000\n"
        "4\n0 0 0 0\n9 8 7 6\n")),
    "36_black_scholes": ("bs", t_cases(4,
        "120 100 0.25 0.01 0.4\n"
        "90 110 1.5 0.02 0.15\n"
        "100 105 0.75 0.03 0.35\n"
        "42 40 0.5 0.1 0.2\n")),
    "38_monte_carlo_pi": ("mcpi", t_cases(4,
        "1 7\n"
        "2500 2024\n"
        "12000 1\n"
        "20000 31337\n")),
    "52_unionfind": ("uf", uf_merge(12,
        "c 9 5\n"
        "u 0 10\n"
        "c 10 0\n"
        "c 11 0\n"
        "u 4 9\n"
        "c 1 9\n"
        "c 3 5\n"
        "c 10 5\n")),
    "54_astar": ("astar", t_cases(4,
        "1 2\nSG\n"
        "4 6\n..#..G\n.##.#.\n.S..#.\n......\n"
        "3 5\nG.#.S\n..#..\n.....\n"
        "4 4\nS#..\n.#.#\n.#.#\n...G\n")),
    "62_crt": ("crt", t_cases(5,
        "1\n5 7\n"
        "2\n3 8\n1 12\n"
        "2\n2 4\n4 6\n"
        "3\n1 7\n4 11\n6 13\n"
        "2\n0 1\n3 5\n")),
    "65_miller_rabin": ("mr", t_cases(12,
        "0\n97\n91\n1105\n104729\n1000000007\n1000000008\n2147483629\n"
        "1373653\n25326001\n2047\n999999937\n")),
    "71_reverse_list": ("rev", t_cases(4,
        "2\n7 -7\n"
        "6\n0 0 1 0 0 2\n"
        "1\n-5\n"
        "4\n8 6 7 5\n")),
    "73_count_vowels": ("vowels", more_lines(
        "The quick brown fox\nrhythm\nQUEUE\naEiOu aEiOu\n12345 !?\nSky\n")),
    "74_palindrome": ("pal", more_lines(
        "Madam, I'm Adam\nNot a palindrome\n12321\n1231\nAble was I ere I saw Elba\nab\na\n")),
    "82_sigmoid": ("sig", t_cases(4,
        "4\n3 -3 0.25 -0.25\n"
        "3\n7.5 -7.5 1.5\n"
        "4\n20 -20 4 -4\n"
        "5\n0.1 -0.1 2.5 -2.5 6\n")),
    "92_popcount": ("pop", t_cases(8,
        "18446744073709551615\n9223372036854775808\n6\n1023\n4294967296\n"
        "1311768467463790320\n65535\n43690\n")),
}


# --- helpers ---------------------------------------------------------------
def _run_fixture(script: str, d: str) -> dict[str, str]:
    """Execute a setup script with its /tmp/eval_* dir redirected into a
    private temp root; return {filename: contents}."""
    with tempfile.TemporaryDirectory() as root:
        priv = os.path.join(root, os.path.basename(d))
        r = subprocess.run(["bash", "-c", script.replace(d, priv)],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise SystemExit(f"fixture script failed ({d}): {r.stderr[:200]}")
        return {p.name: p.read_text() for p in sorted(Path(priv).iterdir()) if p.is_file()}


def strip_expected(setup: str, d: str) -> str:
    """Remove the command that writes <d>/expected.txt from a setup script.
    Three shapes exist in the suite: a printf redirect, a quoted heredoc,
    and (136_gf256) a `python3 -c '...'` that computes the file."""
    path = re.escape(f"{d}/expected.txt")
    shapes = [
        # ... && printf '...' > D/expected.txt
        rf"\s*&&\s*printf '(?:[^']|'\\'')*' > {path}",
        # (after a heredoc) printf '...' > D/expected.txt on its own line
        rf"(?<=\n)printf '(?:[^']|'\\'')*' > {path}\n?",
        # cat > D/expected.txt <<'EOF' ... EOF   (joined by && or a newline)
        rf"(?:\s*&&\s*|(?<=\n))cat > {path} <<'(\w+)'\n.*?\n\1(?:\n|$)",
        # ... && python3 -c '... open("D/expected.txt","w") ...'
        rf"\s*&&\s*python3 -c '[^']*{path}[^']*'",
    ]
    for pat in shapes:
        # An `&& cat <<EOF` block followed by another command on the next
        # line must leave that line break behind.
        def gap(m):
            chained = m.group(0).lstrip().startswith("&&")
            return "\n" if chained and m.group(0).endswith("\n") else ""
        new, n = re.subn(pat, gap, setup, count=1, flags=re.S)
        if n and f"{d}/expected.txt" not in new:
            return new
    raise SystemExit(f"cannot strip the expected.txt write from setup for {d}")


def reference_output(stem: str, text: str) -> str:
    r = subprocess.run([sys.executable, str(REFS / f"{stem}.py")], input=text,
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"reference {stem}.py failed: {r.stderr[:300]}")
    return r.stdout


def _same(a: str, b: str, tolerant: bool) -> bool:
    if not tolerant:
        return a == b
    x, y = a.split(), b.split()
    return len(x) == len(y) and all(abs(float(p) - float(q)) < 1e-3 for p, q in zip(x, y))


def heredoc(path: str, content: str) -> str:
    if not content.endswith("\n"):
        raise SystemExit(f"{path}: content must end with a newline")
    if any(line == DELIM for line in content.split("\n")):
        raise SystemExit(f"{path}: content collides with the heredoc delimiter")
    return f"cat > {path} <<'{DELIM}'\n{content}{DELIM}\n"


def transform_variant(base_id: str, variant: dict) -> None:
    stem, merge = HIDDEN[base_id]
    m = re.search(r"mkdir -p (/tmp/eval_\w+)", variant["setup"])
    if not m:
        raise SystemExit(f"{base_id}: no fixture dir in setup")
    d = m.group(1)
    setup = variant["setup"]

    if f"{d}/expected.txt" in setup:
        before = _run_fixture(setup, d)
        setup = strip_expected(setup, d)
        after = _run_fixture(setup, d)
        want = {k: v for k, v in before.items() if k != "expected.txt"}
        if after != want:
            raise SystemExit(f"{base_id}: stripped setup no longer writes the same fixtures")
        # The reference is about to become the oracle: it must reproduce the
        # v4 answer on the sample first.
        tolerant = "check.py" in before
        if not _same(reference_output(stem, before["input.txt"]), before["expected.txt"], tolerant):
            raise SystemExit(f"{base_id}: {stem}.py does not reproduce the v4 expected.txt")
    files = _run_fixture(setup, d)
    if "expected.txt" in files:
        raise SystemExit(f"{base_id}: setup still writes expected.txt")
    sample = files["input.txt"]
    combined = merge(sample)
    if combined == sample:
        raise SystemExit(f"{base_id}: no hidden cases")
    expected = reference_output(stem, combined)

    variant["setup"] = setup
    variant["pre_validate"] = (setup.rstrip("\n") + "\n" + BEGIN + "\n"
                               + heredoc(f"{d}/input.txt", combined)
                               + heredoc(f"{d}/expected.txt", expected))
    note = NOTE.format(d=d)
    if not variant["task"].endswith(note):
        variant["task"] = variant["task"].rstrip() + note


def _style(obj, raw: str) -> tuple[bool, str]:
    """The file's own JSON style (the suite has four): (ensure_ascii, trailing newline)."""
    for ascii_ in (False, True):
        for nl in ("", "\n"):
            if json.dumps(obj, indent=2, ensure_ascii=ascii_) + nl == raw:
                return ascii_, nl
    raise SystemExit("unrecognised JSON style")


def main() -> int:
    check = "--check" in sys.argv[1:]
    changed = []
    for path in sorted(TASKS.glob("*.json")):
        raw = path.read_text()
        task = json.loads(raw)
        if task.get("id") not in HIDDEN:
            continue
        ascii_, nl = _style(task, raw)
        for variant in task.get("variants") or []:
            transform_variant(task["id"], variant)
        new = json.dumps(task, indent=2, ensure_ascii=ascii_) + nl
        if new != raw:
            changed.append(path.name)
            if not check:
                path.write_text(new)
    missing = set(HIDDEN) - {json.loads(p.read_text()).get("id") for p in TASKS.glob("*.json")}
    if missing:
        raise SystemExit(f"HIDDEN names tasks that do not exist: {sorted(missing)}")
    verb = "would change" if check else "rewrote"
    print(f"hide_expected: {verb} {len(changed)} of {len(HIDDEN)} task files")
    for name in changed:
        print(f"  {name}")
    return 1 if (check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
