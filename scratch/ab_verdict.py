#!/usr/bin/env python3
"""Diagnostics A/B verdict vs the ship rule (LANG_AWARENESS_PLAN §7)."""
import json, glob, sys
from math import comb
sys.path.insert(0,'/home/max/Documents/openbeast/evals')

def latest(slug, after):
    fs=[f for f in sorted(glob.glob(f'/home/max/Documents/openbeast/evals/results/eval-{slug}-*.json')) if f.split('-')[-2]+f.split('-')[-1].split('.')[0] >= after]
    return fs

def rows(f):
    d=json.load(open(f)); return {t['id']: t for t in d['tasks']}, d

def mcnemar(a,b,ids):
    x=[i for i in ids if a[i] and not b[i]]; y=[i for i in ids if b[i] and not a[i]]
    n=len(x)+len(y); k=min(len(x),len(y))
    p=sum(comb(n,j) for j in range(k+1))/2**n*2 if n else 1.0
    return len(x),len(y),min(p,1.0)

langmap={}
for p in glob.glob('/home/max/Documents/openbeast/evals/tasks/*.json'):
    d=json.load(open(p)); vs=d.get('variants')
    if not vs: langmap[d['id']]='python'
    else:
        for v in vs: langmap[f"{d['id']}_{v['id']}"]=v.get('language','python')

# args: A0_file A1_file B1_file B0_full_file
A0,_=rows(sys.argv[1]); A1,_=rows(sys.argv[2]); B1,d1=rows(sys.argv[3]); B0full,_=rows(sys.argv[4])
pin=json.load(open('/home/max/Documents/openbeast/evals/suites/v5-fast.json'))
# assumed_failed units are measured live in this experiment (plan §7
# pre-flight item 2) — without them in the B0 filter a mini rescue is
# structurally invisible (B0∩B1 drops the unit).
units=set(pin['units'])|set(pin.get('assumed_failed',[]))
B0={k:v for k,v in B0full.items() if k in units}
def pm(m): return {k: bool(v.get('passed')) for k,v in m.items()}
a0,a1,b0,b1=pm(A0),pm(A1),pm(B0),pm(B1)
common_b=sorted(set(b0)&set(b1)); common_a=sorted(set(a0)&set(a1))
zig=[u for u in common_b if langmap.get(u)=='zig']; nz=[u for u in common_b if langmap.get(u)!='zig']
off,on,p=mcnemar(b0,b1,zig)
print(f"PRIMARY zig B0→B1: off-only {off}, on-only(rescues) {on}, net {on-off}, p={p:.4f}  → SHIP NEEDS net≥7 p<0.05")
o2,n2,p2=mcnemar(b0,b1,nz); print(f"non-zig guard B:    off-only {o2}, on-only {n2}, p={p2:.3f}")
o3,n3,p3=mcnemar(a0,a1,common_a); print(f"champion guard A:   off-only {o3}, on-only {n3}, p={p3:.3f}  → needs p>0.05")
az=[u for u in common_a if langmap.get(u)=='zig']; o4,n4,p4=mcnemar(a0,a1,az)
print(f"champion zig:       off-only {o4}, on-only {n4}, p={p4:.3f}")
trip=set(pin['tripwires'])
tf_b=[u for u in trip if u in b1 and not b1[u]]; tf_a=[u for u in trip if u in a1 and not a1[u]]
print(f"tripwire fails (on-arms): A1={tf_a} B1={tf_b}  → rerun-once then 0 required")
for tag,m in (('A1',A1),('B1',B1)):
    tok=sum(t.get('tokens_completion',0) for t in m.values()); print(f"{tag} completion tokens: {tok/1e6:.2f}M")
for tag,m in (('A0',A0),('B0',{k:B0full[k] for k in B0})):
    tok=sum(t.get('tokens_completion',0) for t in m.values()); print(f"{tag} completion tokens: {tok/1e6:.2f}M")
