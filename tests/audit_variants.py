#!/usr/bin/env python3
"""Audit variant tasks. Drops a reference impl into the right path, runs
the validation script, reports pass/fail."""
import json, subprocess, sys, shutil, os, time
from pathlib import Path

TASKS_DIR = Path('/home/max/Documents/models/evals/tasks')
REFS = Path('/tmp/refs')
LANG_EXT = {'python': 'py', 'go': 'go', 'c': 'c', 'cpp': 'cpp', 'rust': 'rs', 'zig': 'zig'}

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
            ref_path = f'/tmp/refs/{stem}.{ext}'
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
