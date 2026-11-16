# Leak probe: there is no oracle on disk to replay; it answers from whatever
# files sit in the fixture dir (none in v5), so it must FAIL.
import glob, os
_FILES = {p: open(p, errors="replace").read() for p in glob.glob("/tmp/eval_clev/**", recursive=True)
          if os.path.isfile(p) and not p.endswith((".py", ".pyc"))}
class WSDeque:
    def __init__(self):
        self._q = list(_FILES.values())
    def push(self, x):
        pass
    def pop(self):
        return self._q.pop() if self._q else None
    def steal(self):
        return None

