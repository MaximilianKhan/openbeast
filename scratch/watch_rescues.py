#!/usr/bin/env python3
"""Net zig scoreboard: rescues (B0-failed -> B1-pass) and losses
(B0-passed -> B1-fail). Emits on every change; exits at B1 EXIT."""
import json, re, time, os, sys

FAILED = set(open('/home/max/Documents/openbeast/scratch/b0_failed_zig.txt').read().split())
PASSED = set(open('/home/max/Documents/openbeast/scratch/b0_passed_zig.txt').read().split())
RP = '/home/max/Documents/openbeast/evals/results/eval-qwen3-8-27b-uncensored-q5-k-m-20260909-130402.json'
CHAIN = '/tmp/ab2-chain.log'

rescues, losses = set(), set()
while True:
    changed = False
    try:
        d = json.load(open(RP))
        for t in d.get('tasks', []):
            tid = t.get('id')
            if tid in FAILED and t.get('passed') and tid not in rescues:
                rescues.add(tid); changed = True
                print(f"ZIG RESCUE #{len(rescues)}: {tid}", flush=True)
            if tid in PASSED and not t.get('passed') and tid not in losses:
                losses.add(tid); changed = True
                print(f"ZIG LOSS #{len(losses)}: {tid} (B0' passed, B1' failed)", flush=True)
        if changed:
            print(f"NET SCOREBOARD: +{len(rescues)} rescues, -{len(losses)} losses, NET {len(rescues)-len(losses)} (ship bar: net >=7)", flush=True)
    except (json.JSONDecodeError, OSError):
        pass
    if re.search(r'EXIT \d+ /tmp/ab2-B1\.log', open(CHAIN, errors='replace').read()):
        print(f"B1' COMPLETE — FINAL: +{len(rescues)}/-{len(losses)}, NET {len(rescues)-len(losses)}", flush=True)
        sys.exit(0)
    time.sleep(90)
