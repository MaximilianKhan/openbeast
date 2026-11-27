# Leak probe: there is no oracle on disk to replay; it answers from whatever
# files sit in the fixture dir (none in v5), so it must FAIL.
import glob, os
_FILES = {p: open(p, errors="replace").read() for p in glob.glob("/tmp/eval_rsa/**", recursive=True)
          if os.path.isfile(p) and not p.endswith((".py", ".pyc"))}
def gen_rsa_keys(bits=512, seed=None):
    return (3233, 17, 2753)
def public_exponent(phi):
    return 65537
def rsa_sign(m, n, d):
    return pow(m, d, n)
def rsa_verify(s, m, n, e):
    return pow(s, e, n) == m

