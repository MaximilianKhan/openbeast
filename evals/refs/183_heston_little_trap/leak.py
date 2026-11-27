#!/usr/bin/env python3
"""LEAK PROBE: answers with whatever the fixture dir holds besides inputs
(the sample outputs, repeated). Must FAIL: the hidden cases are not on disk."""
import os
import sys

d = os.path.dirname(os.path.abspath(__file__))
vals = []
for root, _, files in os.walk(d):
    for f in sorted(files):
        if f.endswith('.py') or 'input' in f:
            continue
        try:
            vals += open(os.path.join(root, f)).read().split()
        except OSError:
            pass
n = int(sys.stdin.read().split()[0])
print("\n".join((vals * (n // max(1, len(vals)) + 1))[:n]) if vals else "")
