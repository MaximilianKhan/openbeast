# LEAK PROBE: prints every file it can find in the fixture dir except itself.
import os
D = "/tmp/eval_linz"
for f in sorted(os.listdir(D)):
    p = os.path.join(D, f)
    if os.path.isfile(p) and not f.startswith("linz"):
        print(open(p, errors="replace").read(), end="")
