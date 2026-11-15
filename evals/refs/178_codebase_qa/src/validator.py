# Validator for 178_codebase_qa (embedded into the task JSON by build_task.py;
# EXPECTED is substituted with salted SHA-256 digests of the normalised
# reference answers, which solve.py derives by executing the bundled repo).
#
# The task JSON never holds a plaintext answer. Output is ONE line: either
# "OK: 12/12 answers correct" or the count and ids of the wrong answers,
# never an expected value.
#
# Isolation (evals/README.md, "Validator isolation"): nothing written by the
# agent is executed here; answers.json is only parsed as data, so there is no
# solution process to sandbox and no time limit to scale. The harness feeds
# this script on stdin (python -I -S -), so the digests are in no argv.
# The digests stop a casual read, not a determined one: q1/q2/q3/q9, q5 and
# q11 have small answer spaces and could be brute-forced from the salt. The
# real protection is that the agent never sees this script (the oracle seal
# on evals/tasks and evals/refs, where answers.json is plaintext anyway).
import hashlib, json, os, sys

PATH = '/tmp/eval_codeqa/answers.json'
SALT = '178-ferrule-v5'
EXPECTED = None  # {qid: hexdigest}, filled in by build_task.py
MAX_BYTES = 1 << 20  # a real answers.json is ~1 KB
INTS = ('q1', 'q2', 'q3', 'q9')
STRS = ('q6', 'q10', 'q11')
ORDERED = ('q4', 'q7', 'q12')
UNORDERED = ('q5', 'q8')


def _s(v):
    if not isinstance(v, str):
        raise TypeError
    return ' '.join(v.strip().strip('`').split())


def norm(qid, v):
    if qid in INTS:
        if isinstance(v, bool):
            raise TypeError
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, str) and v.strip().lstrip('-').isdigit():
            return int(v.strip())
        if not isinstance(v, int):
            raise TypeError
        return v
    if qid in STRS:
        return _s(v)
    if not isinstance(v, list):
        raise TypeError
    items = [_s(x) for x in v]
    return sorted(items) if qid in UNORDERED else items


def digest(qid, v):
    return hashlib.sha256(f'{SALT}|{qid}|{json.dumps(v)}'.encode()).hexdigest()


def main():
    if not os.path.isfile(PATH):
        print(f'FAIL: {PATH} not found')
        return 1
    try:
        with open(PATH, 'rb') as fh:
            raw = fh.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            print('FAIL: answers.json is larger than 1 MiB')
            return 1
        got = json.loads(raw.decode('utf-8'))
    except OSError:
        print(f'FAIL: {PATH} is not readable')
        return 1
    except (ValueError, UnicodeDecodeError, RecursionError):
        print('FAIL: answers.json is not valid JSON')
        return 1
    if not isinstance(got, dict):
        print('FAIL: answers.json must be a JSON object')
        return 1
    wrong = []
    for qid in sorted(EXPECTED, key=lambda q: int(q[1:])):
        try:
            ok = qid in got and digest(qid, norm(qid, got[qid])) == EXPECTED[qid]
        except (TypeError, ValueError, RecursionError):
            ok = False
        if not ok:
            wrong.append(qid)
    if wrong:
        print(f'FAIL: {len(wrong)} of {len(EXPECTED)} answers wrong: {" ".join(wrong)}')
        return 1
    print(f'OK: {len(EXPECTED)}/{len(EXPECTED)} answers correct')
    return 0


sys.exit(main())
