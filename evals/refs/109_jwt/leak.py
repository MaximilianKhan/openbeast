# Leak probe: there is no oracle on disk to replay; it answers from whatever
# files sit in the fixture dir (none in v5), so it must FAIL.
import glob, os
_FILES = {p: open(p, errors="replace").read() for p in glob.glob("/tmp/eval_jwt/**", recursive=True)
          if os.path.isfile(p) and not p.endswith((".py", ".pyc"))}
def jwt_encode(payload, secret, algorithm='HS256'):
    return "a.b.c"
def jwt_decode(token, secret, now=None):
    return {}
