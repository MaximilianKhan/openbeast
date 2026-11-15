#!/usr/bin/env python3
"""Build evals/tasks/179_git_bisect_revert.json from gen_repo.py + validator.py.

Generates the fixture once in a private temp dir, derives the constants the
validator needs (original tip, culprit SHA, the culprit hunk's block in the
tip's semver.sh), sanity-checks the history's traps, and writes the JSON.

  python3 evals/refs/179_git_bisect_revert/build_task.py [--check]

--check: rebuild in memory and fail if the committed JSON differs.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT = os.path.join(REPO_ROOT, "evals", "tasks", "179_git_bisect_revert.json")
CULPRIT_MARK = "if [[ $x < $y ]]; then _SV_R=-1; return 0; fi"
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
ENV.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, LC_ALL="C")

TASK = """\
A git repository at /tmp/eval_bisect/semver holds semver.sh, a Bash SemVer 2.0.0 \
utility, and its test suite (bash tests/run.sh; exit status 0 means every test passed). \
On the tip of main (40 commits), `bash semver.sh sort 1.0.0-rc.10 1.0.0-rc.9` prints \
1.0.0-rc.10 first. That is a regression: SemVer 2.0.0 compares numeric prerelease \
identifiers numerically, whatever their number of digits, so 1.0.0-rc.9 must come first. \
The release tag v0.3.0 is known to be good. Find the commit that introduced the \
regression and fix it.

Only the committed state of the repository is graded:
1. main is checked out with no bisect session left in progress, and main has exactly \
one new commit on top of its current tip: an ordinary non-merge commit whose parent is \
the current tip. Do not amend, rebase or otherwise rewrite existing commits.
2. The new commit's message contains the full 40-character SHA of the first bad commit \
(the commit that introduced the regression) and no other commit's full SHA.
3. The new commit changes only semver.sh, and only inside the offending hunk. In the \
zero-context diff of the first bad commit (git show -U0 <sha> -- semver.sh) exactly one \
hunk is responsible for the regression; the lines that hunk added are still present, \
unchanged and contiguous, in the current semver.sh. That block is the only part you may \
change (replace, delete or add lines within it); every line before and after it must \
stay byte-for-byte identical.
4. In the committed tree, bash tests/run.sh passes, and the compare, sort, max, \
satisfies and filter commands follow SemVer 2.0.0 precedence on hidden inputs.

The repository already has a git user name and email configured for committing."""


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, env=ENV, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True).stdout


def sh(cmd, cwd, inp=None):
    p = subprocess.run(["bash", "-c", cmd], cwd=cwd, input=inp, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True, timeout=60,
                       env=dict(ENV))
    return p.returncode, p.stdout


def culprit_block(repo, orig, bad):
    """[R1, R2) = 0-based lines of ORIG:semver.sh that the culprit hunk added."""
    diff = git(["diff", "-U0", bad + "^", bad, "--", "semver.sh"], repo)
    hunks = re.split(r"(?m)^(?=@@ )", diff)
    hit = [h for h in hunks if h.startswith("@@ ") and CULPRIT_MARK in h]
    assert len(hit) == 1, "culprit hunk not unique"
    m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", hit[0])
    start, count = int(m.group(1)), int(m.group(2) or 1)
    added = range(start, start + count)
    blame = git(["blame", "--porcelain", orig, "--", "semver.sh"], repo)
    finals = []
    for ln in blame.splitlines():
        mm = re.match(r"^([0-9a-f]{40}) (\d+) (\d+)", ln)
        if mm and mm.group(1) == bad and int(mm.group(2)) in added:
            finals.append(int(mm.group(3)))
    assert len(finals) == count and finals == list(range(finals[0], finals[0] + count)), finals
    return finals[0] - 1, finals[0] - 1 + count, hit[0]


def analyse(repo, revs):
    """Print, per commit, what the obvious bisect predicates say."""
    rows = []
    for i, rev in enumerate(revs, 1):
        git(["checkout", "-q", rev], repo)
        rc, out = sh("bash tests/run.sh", repo) if os.path.exists(os.path.join(repo, "tests/run.sh")) else (0, "")
        vacuous = rc == 0 and "command not found" in out
        _, s_in = sh("printf '1.0.0-rc.10\\n1.0.0-rc.9\\n' | bash semver.sh sort", repo)
        _, s_arg = sh("bash semver.sh sort 1.0.0-rc.10 1.0.0-rc.9 </dev/null", repo)
        _, c = sh("bash semver.sh compare 1.0.0-rc.9 1.0.0-rc.10", repo)
        rows.append((i, rev[:7], "VAC" if vacuous else ("ok " if rc == 0 else "BAD"),
                     s_in.split("\n")[0][:12], s_arg.split("\n")[0][:20], c.strip()[:8]))
    git(["checkout", "-q", "main"], repo)
    return rows


def build():
    tmp = tempfile.mkdtemp(prefix="b179_")
    try:
        repo = os.path.join(tmp, "semver")
        gen = os.path.join(HERE, "gen_repo.py")
        orig = subprocess.run([sys.executable, gen, repo], check=True, stdout=subprocess.PIPE,
                              universal_newlines=True).stdout.strip()
        revs = git(["rev-list", "--reverse", orig], repo).split()
        assert len(revs) == 40
        bad = revs[19]
        r1, r2, hunk = culprit_block(repo, orig, bad)
        rows = analyse(repo, revs)
        lines = git(["show", orig + ":semver.sh"], repo).split("\n")
        block = lines[r1:r2]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    with open(gen) as f:
        gen_src = f.read()
    assert "GEN179_EOF" not in gen_src
    with open(os.path.join(HERE, "validator.py")) as f:
        val = f.read()
    val = (val.replace("@@ORIG@@", orig).replace("@@BAD@@", bad)
           .replace("@@R1@@", str(r1)).replace("@@R2@@", str(r2)))
    assert "@@" not in val.split("\n", 3)[3].replace("@@ ", "")
    task = {
        "id": "179_git_bisect_revert",
        "name": "git bisect + minimal revert over 40 commits",
        "difficulty": "hard",
        "category": "SWE / DevOps",
        "subcategory": "Regression bisection",
        "language": "shell",
        "max_iter": 25,
        "est_s": 165,
        "max_wall_s": 900,
        "role": "scored",
        "ref_stem": "179_git_bisect_revert/ref",
        "dest": "/tmp/eval_bisect/semver",
        "prove": {"mode": "exec", "note": "solutions are shell scripts run with cwd=dest; see prove.py"},
        "setup": ("rm -rf /tmp/eval_bisect && mkdir -p /tmp/eval_bisect && "
                  "python3 - /tmp/eval_bisect/semver %s >/dev/null <<'GEN179_EOF'\n%sGEN179_EOF" % (orig, gen_src)),
        "task": TASK,
        "validation": {"type": "python", "script": val},
        "cleanup": "rm -rf /tmp/eval_bisect",
    }
    info = {"orig": orig, "bad": bad, "r1": r1, "r2": r2, "block": block, "hunk": hunk, "rows": rows}
    return task, info


def main():
    task, info = build()
    text = json.dumps(task, indent=1, ensure_ascii=True) + "\n"
    if "--check" in sys.argv:
        with open(OUT) as f:
            same = f.read() == text
        print("up to date" if same else "STALE: rerun build_task.py")
        return 0 if same else 1
    print("orig", info["orig"], "\nbad ", info["bad"], "\nblock [%d,%d):" % (info["r1"], info["r2"]))
    for ln in info["block"]:
        print("   |" + ln)
    print(info["hunk"])
    print(" #  sha     suite  sort<stdin     sort args            compare")
    for r in info["rows"]:
        print("%2d  %s  %s    %-12s   %-20s %s" % r)
    with open(OUT, "w") as f:
        f.write(text)
    print("wrote", OUT, len(text), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
