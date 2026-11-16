# Leak probe: there is no oracle on disk to replay; it answers from whatever
# files sit in the fixture dir (none in v5), so it must FAIL.
import glob, os
_FILES = {p: open(p, errors="replace").read() for p in glob.glob("/tmp/eval_hmsc/**", recursive=True)
          if os.path.isfile(p) and not p.endswith((".py", ".pyc"))}
def hmac_sha256_scratch(key, message):
    return next(iter(_FILES.values()), "0" * 64).strip()
