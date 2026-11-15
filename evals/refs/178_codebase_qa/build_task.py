#!/usr/bin/env python3
"""Build evals/tasks/178_codebase_qa.json from this directory.

    python3 build_task.py            # rebuild the task JSON and the ref arms
    python3 build_task.py --check    # exit 1 if the committed JSON is stale

Inputs (all in this directory):
  repo/            the bundled ferrule repo the agent reads (shipped as a
                   deterministic .tar.gz, base64, inside `setup`)
  src/task.md      the prompt
  src/validator.py the validator; EXPECTED is filled with salted SHA-256
                   digests of the normalised answers
  solve.py         derives every answer by executing repo/ (single source of
                   truth for the digests and for answers.json)

Outputs: ../../tasks/178_codebase_qa.json, answers.json (the reference
solution) and the derived wrong/lenient arms under arms/.
"""

import base64
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
TASK_JSON = os.path.join(HERE, "..", "..", "tasks", "178_codebase_qa.json")
FIX = "/tmp/eval_codeqa"
SALT = "178-ferrule-v5"


def repo_files():
    out = []
    root = os.path.join(HERE, "repo")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for f in sorted(filenames):
            if f.endswith(".pyc") or f == ".DS_Store":
                continue
            full = os.path.join(dirpath, f)
            out.append((os.path.relpath(full, root), full))
    return sorted(out)


def tarball() -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for rel, full in repo_files():
            data = open(full, "rb").read()
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o755 if rel.startswith("scripts/") else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0, compresslevel=9) as fh:
        fh.write(raw.getvalue())
    return gz.getvalue()


def solve() -> dict:
    out = subprocess.run([sys.executable, "-B", os.path.join(HERE, "solve.py"), os.path.join(HERE, "repo")],
                         capture_output=True, text=True, check=True, env={"PATH": os.environ.get("PATH", "")})
    return json.loads(out.stdout)


def load_validator():
    """Import src/validator.py's norm() without running its main()."""
    src = open(os.path.join(HERE, "src", "validator.py")).read()
    ns = {"__name__": "v178"}
    exec(src.replace("sys.exit(main())", ""), ns)  # noqa: S102 - our own file
    return src, ns


def build():
    answers = solve()
    src, ns = load_validator()
    expected = {q: hashlib.sha256(f"{SALT}|{q}|{json.dumps(ns['norm'](q, v))}".encode()).hexdigest()
                for q, v in answers.items()}
    marker = "EXPECTED = None  # {qid: hexdigest}, filled in by build_task.py"
    assert marker in src
    script = src.replace(marker, "EXPECTED = " + json.dumps(expected, sort_keys=True))

    b64 = base64.b64encode(tarball()).decode()
    lines = "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))
    setup = (
        f"rm -rf {FIX} && mkdir -p {FIX}/repo\n"
        "python3 -c \"import base64,io,sys,tarfile; "
        "t=tarfile.open(fileobj=io.BytesIO(base64.b64decode(sys.stdin.read())), mode='r:gz'); "
        "kw={'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}; "
        f"t.extractall('{FIX}/repo', **kw)\" <<'FERRULE_B64'\n{lines}\nFERRULE_B64\n"
    )
    task = {
        "id": "178_codebase_qa",
        "name": "Codebase Q&A on a bundled ~5 kLOC repo",
        "difficulty": "hard",
        "category": "SWE / DevOps",
        "subcategory": "Code comprehension",
        "max_iter": 25,
        "est_s": 165,
        "max_wall_s": 900,
        "role": "scored",
        "language": "python",
        "ref_stem": "178_codebase_qa/answers",
        "dest": f"{FIX}/answers.json",
        "setup": setup,
        "task": open(os.path.join(HERE, "src", "task.md")).read().strip(),
        "validation": {"type": "python", "script": script},
        "cleanup": f"rm -rf {FIX}",
    }

    arms = os.path.join(HERE, "arms")
    os.makedirs(arms, exist_ok=True)

    def dump(name, obj):
        with open(os.path.join(arms, name), "w") as fh:
            json.dump(obj, fh, indent=1)
            fh.write("\n")

    with open(os.path.join(HERE, "answers.json"), "w") as fh:
        json.dump(answers, fh, indent=1)
        fh.write("\n")
    # Same answers in tolerated alternative spellings: must still pass.
    lenient = dict(answers)
    lenient["q2"] = str(answers["q2"])
    lenient["q5"] = list(reversed(answers["q5"]))
    lenient["q6"] = f"  `{answers['q6']}` "
    lenient["q8"] = [p.replace(" ", "  ") for p in reversed(answers["q8"])]
    dump("ref_lenient.json", lenient)
    # Single-trap misses: each must fail on exactly that question.
    w = dict(answers, q5=sorted(answers["q5"] + ["support"]))
    dump("wrong_q5_docs_append.json", w)
    w = dict(answers, q12=[c for c in answers["q12"] if "journal" not in c])
    dump("wrong_q12_wal_disabled.json", w)
    # Ties broken by PLUGIN_ORDER instead of plugins.enabled order.
    q4 = list(answers["q4"])
    q4[3], q4[4] = q4[4], q4[3]
    dump("wrong_q4_plugin_order.json", dict(answers, q4=q4))
    # Order of a set answer is free, order of a sequence answer is not.
    dump("wrong_q7_sorted.json", dict(answers, q7=sorted(answers["q7"])))
    # What a reader of README/docs (not the code) would answer.
    dump("wrong_docs_naive.json", {
        "q1": 5, "q2": 8, "q3": 22000,
        "q4": ["service.validate_payload", "dedupe.fingerprint", "dedupe.check", "sla.stamp_deadline",
               "quota.enforce", "tagger.apply_tags", "audit.record_submit"],
        "q5": ["admin", "lead", "maintainer", "oncall", "support"],
        "q6": "ferrule.cli.commands.queue.list_queues",
        "q7": ["0001_init", "0002_tenants", "0003_jobs", "0007_queues", "0008_audit", "0010_squash_jobs",
               "0011_quota", "0012_hooks", "0013_audit_index", "0014_retention"],
        "q8": ["POST /api/v3/jobs/{job_id}/retry"],
        "q9": 25,
        "q10": "fr-prod:v3:acme:job:j%3A42",
        "q11": "DEAD/3",
        "q12": ["ferrule.storage.cached.CachedStore", "ferrule.storage.sqlite_store.SqliteStore"],
    })
    with open(os.path.join(arms, "wrong_malformed.json"), "w") as fh:
        fh.write("{'q1': 10,}\n")
    return task


def main():
    task = build()
    text = json.dumps(task, indent=1, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        current = open(TASK_JSON).read() if os.path.exists(TASK_JSON) else ""
        if current != text:
            print("178_codebase_qa.json is stale; run build_task.py")
            return 1
        print("up to date")
        return 0
    with open(TASK_JSON, "w") as fh:
        fh.write(text)
    print(f"wrote {os.path.relpath(TASK_JSON)} ({len(text)} bytes, {len(repo_files())} repo files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
