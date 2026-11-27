#!/usr/bin/env python3
"""Generate evals/tasks/175_crdt_orset_rga.json from validator.py + the prompt.

validator.py is the single oracle; editing it (or the prompt below) and
re-running this script is the only supported way to change the task. The
sample test shipped to the agent is checked against crdt.py here first.
    python3 evals/refs/175/build_task.py
"""
import json, os, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
OUT = os.path.join(REPO, "evals", "tasks", "175_crdt_orset_rga.json")
D = "/tmp/eval_crdt"

VALIDATOR = open(os.path.join(HERE, "validator.py")).read()

SAMPLE = '''\
# Sample scenario for /tmp/eval_crdt/crdt.py. Run: cd /tmp/eval_crdt && python3 sample_test.py
# (The hidden tests are many larger scenarios with random delivery orders.)
from crdt import Replica

r = [Replica(0), Replica(1), Replica(2)]
m1 = r[0].insert(0, "a")          # id (1,0)
m2 = r[0].insert(1, "b")          # id (2,0), anchored on "a"
m3 = r[1].insert(0, "x")          # id (1,1), concurrent with m1 and m2
r[2].deliver(m2)                  # m1 is in m2's causal past: m2 is held
assert (r[2].text(), r[2].pending()) == ("", 1)
m4 = r[2].insert(0, "y")          # id (1,2): the held m2 does not raise the counter
r[2].deliver(m1)                  # m1 takes effect, then m2
r[2].deliver(m1)                  # a repeat does nothing
assert (r[2].text(), r[2].pending()) == ("yab", 0)

k1 = r[0].add("k")
for m in (k1, m2, m1):            # k1 is held until m1 and m2 have taken effect
    r[1].deliver(m)
assert (r[1].elements(), r[1].text()) == (["k"], "xab")
k2 = r[1].remove("k")             # cancels the one tag of "k" replica 1 has seen
k3 = r[2].add("k")                # concurrent with the remove
r[0].deliver(k2)                  # held: m3 had taken effect at replica 1 first
assert (r[0].elements(), r[0].pending()) == (["k"], 1)
r[0].deliver(m3)
assert (r[0].elements(), r[0].text(), r[0].pending()) == ([], "xab", 0)

msgs = [m1, m2, m3, m4, k1, k2, k3]
for rep in r:
    for m in msgs:
        rep.deliver(m)
for rep in r:
    assert (rep.elements(), rep.text(), rep.pending()) == (["k"], "yxab", 0)
d = r[0].delete(3)                # deletes "b"
r[1].deliver(d)
assert r[1].text() == "yxa"
print("sample OK")
'''

TASK = f"""Create {D}/crdt.py (Python 3, standard library only): an operation-based replicated data type for a cluster of exactly three replicas with ids 0, 1 and 2. Each replica holds two CRDTs at once, an observed-remove set (OR-Set) of strings and an RGA sequence of characters. The tests drive it only through this class:

class Replica:
    def __init__(self, rid: int)              # rid is 0, 1 or 2
    def add(self, x: str) -> str              # local ops: each takes effect at this replica at once
    def remove(self, x: str) -> str           #   and returns the op as a str message
    def insert(self, i: int, ch: str) -> str  # ch is one character, 0 <= i <= len(self.text())
    def delete(self, i: int) -> str           # 0 <= i < len(self.text())
    def deliver(self, msg: str) -> None
    def elements(self) -> list[str]           # the set's members, ascending
    def text(self) -> str                     # the visible characters, in order
    def pending(self) -> int

Every observation (elements(), text(), pending()) is checked exactly, at many points of each scenario, against these rules:

1. Op ids. Each add/remove/insert/delete call creates one op with id (c, r): r is the replica that created it and c = 1 + the largest c of any op that has taken effect at that replica (its own ops included), or 1 if none has. Ops that have been delivered but have not yet taken effect do not count. Ids compare as tuples: by c, then by r.

2. Delivery. The tests pass messages between replicas themselves: any message returned by any replica may be given to deliver() of any replica (including the replica that created it), any number of times, in any order, possibly much later. An op takes effect at a replica only after its own message has been delivered there, and only once every op in its causal past has taken effect there. The causal past of an op is the set of ops that had taken effect at its creating replica when it was created. A delivered op that is not ready yet is held, and takes effect automatically as soon as it becomes ready (during a later deliver() call; one delivery can release a cascade). Delivering an op that has already taken effect, or is already held, changes nothing. pending() is the number of distinct ops this replica holds.

3. OR-Set. The id of an add(x) op is a tag of x. A remove(x) op cancels exactly the tags of x that had taken effect at its creating replica when it was created (it is still an op, with an id, if it cancels nothing). x is a member iff at least one tag of x that has taken effect here is not cancelled by any remove that has taken effect here.

4. RGA. An insert(i, ch) op records an anchor: the element just left of position i in the creating replica's text(), or HEAD when i == 0. delete(i) marks the element at position i of text() as deleted. Deleted elements are never removed and stay valid anchors; deleting an element more than once is the same as once. The full sequence at a replica, over the inserts that have taken effect there, is seq(HEAD), where seq(e) = [e] followed by seq(c) for every element c anchored on e, those c taken in descending id order. text() is that sequence minus HEAD and the deleted elements.

5. Isolation. Replicas may share nothing but messages: the tests can build each replica from its own separately imported copy of your module, so module- or class-level shared state does not work. Messages are opaque to the tests, which never parse or change them.

Scale: scenarios have up to about 1500 ops, texts reach about 2500 characters, including runs of thousands of characters typed one after another. The whole hidden suite (about 60 scenarios) must finish within 10 seconds.

Sample: {D}/sample_test.py is a small scenario written as assertions; run it with: cd {D} && python3 sample_test.py. The hidden tests are much larger."""

SETUP = f"mkdir -p {D} && cat > {D}/sample_test.py <<'SAMPLE_EOF'\n{SAMPLE}SAMPLE_EOF"

# The sample must agree with the reference before it ships.
tmp = tempfile.mkdtemp(prefix="t175_")
try:
    shutil.copy(os.path.join(HERE, "crdt.py"), tmp)
    with open(os.path.join(tmp, "sample_test.py"), "w") as f:
        f.write(SAMPLE)
    r = subprocess.run([sys.executable, "-B", "sample_test.py"], cwd=tmp, capture_output=True, text=True)
    assert r.returncode == 0 and "sample OK" in r.stdout, r.stdout + r.stderr
finally:
    shutil.rmtree(tmp)

task = {
    "id": "175_crdt_orset_rga",
    "name": "CRDT OR-Set + RGA across 3 replicas",
    "difficulty": "hard",
    "category": "Distributed / SysDesign",
    "subcategory": "CRDTs & causal delivery",
    "max_iter": 25,
    "role": "scored",
    "est_s": 165,
    "max_wall_s": 900,
    "language": "python",
    "ref_stem": "175/crdt",
    "dest": f"{D}/crdt.py",
    "setup": SETUP,
    "task": TASK,
    "validation": {"type": "python", "script": VALIDATOR},
    "cleanup": f"rm -rf {D}",
}
with open(OUT, "w") as f:
    json.dump(task, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("wrote", OUT)
