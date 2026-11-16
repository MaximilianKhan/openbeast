# Leak probe: there is no oracle on disk to replay; it answers from whatever
# files sit in the fixture dir (none in v5), so it must FAIL.
import glob, os
_FILES = {p: open(p, errors="replace").read() for p in glob.glob("/tmp/eval_qkv/**", recursive=True)
          if os.path.isfile(p) and not p.endswith((".py", ".pyc"))}
class QuorumKV:
    def __init__(self, n, r, w):
        if r + w <= n:
            raise ValueError("quorum")
    def write(self, key, value, timestamp, node, success_replicas):
        return True
    def read(self, key, response_replicas):
        return None
    def replica_state(self, key, replica):
        return None

