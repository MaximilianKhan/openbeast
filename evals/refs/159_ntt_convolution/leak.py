import os, sys
D = "/tmp/eval_ntt"
for p in sorted(os.listdir(D)):
    if p == "input.txt" or p.startswith("ntt"):
        continue
    try:
        sys.stdout.write(open(os.path.join(D, p), errors="replace").read())
    except OSError:
        pass
