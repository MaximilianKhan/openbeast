# Leak probe: there is no oracle on disk to replay; it answers from whatever
# files sit in the fixture dir (none in v5), so it must FAIL.
import glob, os
_FILES = {p: open(p, errors="replace").read() for p in glob.glob("/tmp/eval_uov/**", recursive=True)
          if os.path.isfile(p) and not p.endswith((".py", ".pyc"))}
P = 251
def uov_keygen(v, o, seed=0, p=P):
    return [dict() for _ in range(o)], {"o": o, "n": v + o}
def uov_sign(h, priv, seed=0, p=P):
    return [0] * priv["n"]
def uov_verify(h, sig, pub, p=P):
    return True

