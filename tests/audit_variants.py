#!/usr/bin/env python3
"""Audit variant tasks. Drops a reference impl into the right path, runs
the validation script through the HARNESS's own setup/validation/cleanup
(evals/run_eval.py: same 30 s x scale timeouts, same rlimits), reports
pass/fail.

Where the reference comes from: the unit's task JSON declares it —
`ref_stem` (path under evals/refs/, no extension; the language picks it)
and `dest` (where the task says the solution lives). LEGACY_TARGETS is the
v4 hand map, kept only for v4 tasks that declare nothing; a declaration in
the JSON always wins, and a task that is neither declared nor mapped is
reported [SKIP]. The full v5 gate (wrong solutions with asserted failure
classes, leak probe, slow arm, --scale 2.0) is tests/prove_task.py; this is
the quick all-variants smoke.

Also runs the ZIG TUTORING BAN LINT (v5). v4 REQUIRED zig prompts to carry
the stdio API recipe (`&fr.interface`, `init.io`, `takeDelimiter`, flush),
which made a "packs OFF" arm assisted anyway: every zig unit was tutored.
v5 inverts the rule — a zig prompt states the contract (Zig 0.16, the entry
point the validator builds, the compile command) and teaches no API. Zig
guidance belongs only to the treatment arms (--packs, BEAST_ASSIST,
--escalate). tests/test_v5_suite.py enforces the ban on every v5 unit."""
import subprocess, sys, shutil, os, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO / 'evals' / 'tasks'
REFS = REPO / 'evals' / 'refs'
LANG_EXT = {'python': 'py', 'go': 'go', 'c': 'c', 'cpp': 'cpp', 'rust': 'rs', 'zig': 'zig',
            'shell': 'sh'}

sys.path.insert(0, str(REPO / 'evals'))
sys.path.insert(0, str(REPO / 'agents'))
import run_eval as _harness  # noqa: E402
import suite_manifest as _manifest  # noqa: E402

# The harness's validator budget (30 s x timeout_scale). The audit used to
# allow 120 s, so it could pass a unit the harness times out.
VALIDATION_TIMEOUT_S = _harness.VALIDATION_TIMEOUT_S

# Phrases a v5 zig prompt must NOT contain (the v4 tutoring recipe).
SPEC_BANNED = {'zig': list(_manifest.ZIG_TUTORING_BANNED)}


def lint_zig_tutoring(task_ids, fail_on=None):
    """Zig prompts that still carry the banned API tutoring. Returns the
    findings [(unit id, [phrases])]; prints them. `fail_on` (a set of unit
    ids, e.g. the v5 manifest's) marks which findings are errors — the
    dropped v4 zig prompts keep their text for history."""
    findings = []
    for tid in task_ids:
        for t in _harness.load_tasks([tid]):
            banned = SPEC_BANNED.get(_manifest.norm_lang(t.get('language')))
            if not banned:
                continue
            hit = [p for p in banned if p in t.get('task', '')]
            if hit:
                findings.append((t['id'], hit))
    errors = [f for f in findings if fail_on is not None and f[0] in fail_on]
    if findings:
        print("\nZIG TUTORING BAN LINT — zig prompts carrying API tutoring:")
        for tid, hit in findings:
            sev = "ERROR" if (tid, hit) in errors else "note "
            print(f"  {sev} {tid}: {hit}")
        print("  (v5: state the contract, teach no API — see skills/eval-variant-porter/SKILL.md)")
    else:
        print("\nZIG TUTORING BAN LINT — clean.")
    return findings


# Back-compat name for callers of the v4 lint.
lint_spec_completeness = lint_zig_tutoring

# v4 hand map: task -> (ref stem under evals/refs/, fixture dir). Used only
# for tasks whose JSON declares no ref_stem/dest. Do not add entries: declare
# `ref_stem` and `dest` in the task JSON instead.
LEGACY_TARGETS = {
    '31_is_power_of_two': ('pow2', '/tmp/eval_pow2'),
    '73_count_vowels':    ('vowels', '/tmp/eval_vowels'),
    '74_palindrome':      ('pal', '/tmp/eval_pal'),
    '19_three_way_quicksort': ('qs', '/tmp/eval_qs'),
    '51_toposort':        ('topo', '/tmp/eval_topo'),
    '52_unionfind':       ('uf', '/tmp/eval_uf'),
    '61_extgcd':          ('xgcd', '/tmp/eval_xgcd'),
    '65_miller_rabin':    ('mr', '/tmp/eval_mr'),
    '122_gemm_blocked':   ('gemm', '/tmp/eval_gemm'),
    '148_convex_hull':         ('hull', '/tmp/eval_hull'),
    '155_tonelli_shanks':      ('ts', '/tmp/eval_tonelli'),
    '158_karatsuba_bytes':     ('karat', '/tmp/eval_karat'),
    '159_ntt_convolution':     ('ntt', '/tmp/eval_ntt'),
    # Phase B — easy
    '32_dot_product':              ('dot', '/tmp/eval_dot'),
    '71_reverse_list':             ('rev', '/tmp/eval_rev'),
    '82_sigmoid':                  ('sig', '/tmp/eval_sig'),
    '92_popcount':                 ('pop', '/tmp/eval_pop'),
    '100_constant_time_compare':   ('ct', '/tmp/eval_ct'),
    # Phase B — medium
    '11_bst':                ('bst', '/tmp/eval_bst'),
    '20_priority_queue':     ('pq', '/tmp/eval_pq'),
    '54_astar':              ('astar', '/tmp/eval_astar'),
    '38_monte_carlo_pi':     ('mcpi', '/tmp/eval_mcpi'),
    '36_black_scholes':      ('bs', '/tmp/eval_bs'),
    '62_crt':                ('crt', '/tmp/eval_crt'),
    '63_det':                ('det', '/tmp/eval_det'),
    '136_gf256':             ('gf', '/tmp/eval_gf'),
    # Phase B — hard
    '27_brainfuck_interpreter': ('bf', '/tmp/eval_bf'),
    '115_fft':                  ('fft', '/tmp/eval_fft'),
    '123_nbody':                ('nbody', '/tmp/eval_nbody'),
    '127_aes_keysched':         ('aes', '/tmp/eval_aes'),
    '137_pollard_rho':          ('prho', '/tmp/eval_prho'),
}

TARGETS = LEGACY_TARGETS            # old name, still imported by tests
load_tasks = _harness.load_tasks


def ref_target(t: dict, requested: str | None = None) -> tuple[str, str] | None:
    """(reference file, destination path) for one unit: the JSON
    declaration (`ref_stem` / `dest`), else the legacy hand map, else None."""
    lang = _manifest.norm_lang(t.get('language'))
    ext = LANG_EXT.get(lang)
    if ext is None:
        return None
    if t.get('ref_stem') and t.get('dest'):
        base = REFS / t['ref_stem']
        ref = base if base.is_file() else REFS / f"{t['ref_stem']}.{ext}"
        return str(ref), t['dest']
    tid = next((k for k in (t.get('base_id'), requested, t['id']) if k in LEGACY_TARGETS), None)
    if tid is not None:
        stem, dest_dir = LEGACY_TARGETS[tid]
        return str(REFS / f'{stem}.{ext}'), f'{dest_dir}/{stem}.{ext}'
    return None


def declared_task_ids() -> list[str]:
    """Every base task with a ref declaration or a legacy mapping."""
    ids = set(LEGACY_TARGETS)
    for t in _harness.load_tasks(None):
        if t.get('ref_stem') and t.get('dest'):
            ids.add(t.get('base_id') or t['id'])
    return sorted(ids)


def audit(task_ids):
    """Returns the lines of every variant that did not pass ([FAIL]: setup or
    validation failed; [MISS]: no reference implementation to audit with)."""
    import builtins
    bad = []

    def print(line):                       # noqa: A001 — record what is reported
        builtins.print(line)
        if line.lstrip().startswith(("[FAIL]", "[MISS]")):
            bad.append(line.strip())

    quiet = lambda *_: None  # noqa: E731
    for tid in task_ids:
        tasks = load_tasks([tid])
        if not tasks:
            print(f"  [SKIP] {tid}: no such task"); continue
        for t in tasks:
            lang = _manifest.norm_lang(t.get('language'))
            target = ref_target(t, tid)
            if target is None:
                print(f"  [SKIP] {t['id']}: no ref_stem/dest declared and no legacy mapping"); continue
            ref_path, dest = target
            if not os.path.exists(ref_path):
                print(f"  [MISS] {t['id']} ({lang}): no ref at {ref_path}"); continue
            _harness.run_cleanup(t, log=quiet)
            if not _harness.run_setup(t, log=quiet):
                print(f"  [FAIL] {t['id']} ({lang}): setup failed"); continue
            os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
            shutil.copy(ref_path, dest)
            _harness.run_pre_validate(t, log=quiet)
            start = time.time()
            ok, out = _harness.run_validation(t, timeout_scale=1.0)
            elapsed = time.time() - start
            tail = (out or '').strip()[-150:]
            mark = '[PASS]' if ok else '[FAIL]'
            print(f'  {mark} {t["id"]:35s} ({lang:6s})  {elapsed:5.2f}s  {"" if ok else tail}')
            _harness.run_cleanup(t, log=quiet)
    return bad

if __name__ == '__main__':
    # EXIT 1 when a variant fails. This script used to print [FAIL] and exit 0
    # whatever happened, so the CI step that runs it could not go red.
    # --advisory keeps the old exit code (CI passes it until the runner's
    # toolchains are known to pass every variant; there is no zig there) and
    # turns each failure into a warning annotation instead.
    args = [a for a in sys.argv[1:] if a != '--advisory']
    advisory = '--advisory' in sys.argv[1:]
    task_ids = args or declared_task_ids()
    bad = audit(task_ids)
    lint_zig_tutoring(task_ids)
    if bad:
        print(f"\n{len(bad)} variant(s) did not pass the audit" + (" (advisory: exit 0)" if advisory else ""))
        if advisory and os.environ.get('GITHUB_ACTIONS') == 'true':
            for line in bad:
                print(f"::warning title=variant audit::{line[:200]}")
        sys.exit(0 if advisory else 1)
