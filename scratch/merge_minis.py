#!/usr/bin/env python3
"""Merge mini-cell rows into a copy of each main cell's results file.
usage: merge_minis.py <main.json> <mini.json> <out.json>"""
import json, sys
main, mini, out = sys.argv[1:4]
d = json.load(open(main))
m = json.load(open(mini))
have = {t['id'] for t in d['tasks']}
added = []
for t in m['tasks']:
    if t['id'] not in have:
        d['tasks'].append(t); added.append(t['id'])
d.setdefault('provenance', {})['mini_merged'] = {'from': mini, 'added': added}
json.dump(d, open(out, 'w'), indent=1)
print(f"{out}: +{len(added)} mini rows {added}")
