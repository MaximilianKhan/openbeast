#!/usr/bin/env python3
"""Audit variant tasks. Drops a reference impl into the right path, runs
the validation script, reports pass/fail.

Also runs a SPEC-COMPLETENESS LINT that checks the model-facing `task` text
for essential phrases per language. The lint catches the failure mode the
v3 sweep surfaced: spec teaches API X, reference impl uses API Y, audit
passes (because ref impl is correct), models score 0/13 (because they
follow the spec). The fix is to require the spec to mention the same
load-bearing tokens the ref impl actually uses."""
import json, subprocess, sys, shutil, os, time
from pathlib import Path

TASKS_DIR = Path('/home/max/Documents/models/evals/tasks')
REFS = Path('/home/max/Documents/models/evals/refs')
LANG_EXT = {'python': 'py', 'go': 'go', 'c': 'c', 'cpp': 'cpp', 'rust': 'rs', 'zig': 'zig'}

# Essential phrases the model-facing `task` text MUST mention per language,
# at least one phrase per group. Adding a phrase here is the way to harden
# the audit against future spec/ref-impl drift like the Zig 0.16 defect.
SPEC_REQUIREMENTS = {
    'zig': [
        # Group 1: the &fr.interface indirection (or a pointer to SKILL.md)
        ['&fr.interface', '&fw.interface', 'see SKILL.md', 'eval-variant-porter SKILL'],
        # Group 2: pass init.io to .reader()/.writer() (or pointer to SKILL)
        ['init.io', 'see SKILL.md', 'eval-variant-porter SKILL'],
        # Group 3: warn that flush is required
        ['flush', 'see SKILL.md', 'eval-variant-porter SKILL'],
    ],
    # Python/Go/C/C++/Rust have no required phrases yet — they were not
    # implicated in the v3 defect. Add groups here as future defects surface.
}


def lint_spec_completeness(task_ids):
    """Lint that each variant's `task` field mentions the load-bearing
    phrases for its language. Reports gaps but does not fail the audit —
    flag for human review."""
    sys.path.insert(0, '/home/max/Documents/models/evals')
    from run_eval import load_tasks
    findings = []
    for tid in task_ids:
        for t in load_tasks([tid]):
            lang = t.get('language', 'python')
            requirements = SPEC_REQUIREMENTS.get(lang)
            if not requirements:
                continue
            text = t.get('task', '')
            missing_groups = []
            for group in requirements:
                if not any(phrase in text for phrase in group):
                    missing_groups.append(group[0])
            if missing_groups:
                findings.append((t['id'], lang, missing_groups))
    if findings:
        print("\nSPEC-COMPLETENESS LINT — task text missing load-bearing phrases:")
        for tid, lang, missing in findings:
            print(f"  {tid} ({lang}): missing {missing}")
        print("  (Spec text should mention these or point to skills/eval-variant-porter/SKILL.md)")
    else:
        print("\nSPEC-COMPLETENESS LINT — clean.")

TARGETS = {
    '31_is_power_of_two': ('pow2', '/tmp/eval_pow2'),
    '73_count_vowels':    ('vowels', '/tmp/eval_vowels'),
    '74_palindrome':      ('pal', '/tmp/eval_pal'),
    '19_three_way_quicksort': ('qs', '/tmp/eval_qs'),
    '51_toposort':        ('topo', '/tmp/eval_topo'),
    '52_unionfind':       ('uf', '/tmp/eval_uf'),
    '53_bloom':           ('bloom', '/tmp/eval_bloom'),
    '61_extgcd':          ('xgcd', '/tmp/eval_xgcd'),
    '65_miller_rabin':    ('mr', '/tmp/eval_mr'),
    '122_gemm_blocked':   ('gemm', '/tmp/eval_gemm'),
    '145_segment_tree_lazy':   ('seg', '/tmp/eval_segtree'),
    '146_aho_corasick':        ('ac', '/tmp/eval_ac'),
    '148_convex_hull':         ('hull', '/tmp/eval_hull'),
    '152_chase_lev_deque':     ('clev', '/tmp/eval_clev'),
    '153_coroutine_scheduler': ('sched', '/tmp/eval_sched'),
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
    '39_blocked_transpose':  ('trans', '/tmp/eval_trans'),
    '36_black_scholes':      ('bs', '/tmp/eval_bs'),
    '62_crt':                ('crt', '/tmp/eval_crt'),
    '63_det':                ('det', '/tmp/eval_det'),
    '136_gf256':             ('gf', '/tmp/eval_gf'),
    '108_hmac_verify':       ('hmac', '/tmp/eval_hmac'),
    # Phase B — hard
    '27_brainfuck_interpreter': ('bf', '/tmp/eval_bf'),
    '115_fft':                  ('fft', '/tmp/eval_fft'),
    '123_nbody':                ('nbody', '/tmp/eval_nbody'),
    '127_aes_keysched':         ('aes', '/tmp/eval_aes'),
    '47_branchless_min':        ('branchless', '/tmp/eval_branchless'),
    '137_pollard_rho':          ('prho', '/tmp/eval_prho'),
}

sys.path.insert(0, '/home/max/Documents/models/evals')
from run_eval import load_tasks

def audit(task_ids):
    for tid in task_ids:
        if tid not in TARGETS:
            print(f"  [SKIP] {tid}: no target mapping"); continue
        stem, dest_dir = TARGETS[tid]
        tasks = load_tasks([tid])
        for t in tasks:
            lang = t.get('language', 'python')
            ext = LANG_EXT.get(lang)
            if ext is None:
                print(f"  [MISS] {t['id']} ({lang}): unknown language"); continue
            ref_path = str(REFS / f'{stem}.{ext}')
            if not os.path.exists(ref_path):
                print(f"  [MISS] {t['id']} ({lang}): no ref at {ref_path}"); continue
            r0 = subprocess.run(t['setup'], shell=True, capture_output=True, text=True, timeout=30)
            if r0.returncode != 0:
                print(f"  [FAIL] {t['id']} ({lang}): setup exit {r0.returncode}"); continue
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy(ref_path, f'{dest_dir}/{stem}.{ext}')
            if t.get('pre_validate'):
                subprocess.run(t['pre_validate'], shell=True, capture_output=True)
            start = time.time()
            r = subprocess.run(t['validation']['script'], shell=True, capture_output=True, text=True, timeout=120)
            elapsed = time.time() - start
            ok = r.returncode == 0
            tail = (r.stdout + r.stderr).strip()[-150:]
            mark = '[PASS]' if ok else '[FAIL]'
            print(f'  {mark} {t["id"]:35s} ({lang:6s})  {elapsed:5.2f}s  {"" if ok else tail}')
            subprocess.run(t['cleanup'], shell=True, capture_output=True)

if __name__ == '__main__':
    task_ids = sys.argv[1:] if len(sys.argv) > 1 else list(TARGETS.keys())
    audit(task_ids)
    lint_spec_completeness(task_ids)
