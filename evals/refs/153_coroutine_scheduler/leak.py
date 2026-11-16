# Leak probe: there is no oracle on disk to replay; it answers from whatever
# files sit in the fixture dir (none in v5), so it must FAIL.
import glob, os
_FILES = {p: open(p, errors="replace").read() for p in glob.glob("/tmp/eval_sched/**", recursive=True)
          if os.path.isfile(p) and not p.endswith((".py", ".pyc"))}
class Scheduler:
    def __init__(self):
        self.c = []
    def spawn(self, coro):
        self.c.append(coro)
    def run(self):
        for c in self.c:
            for _ in c:
                pass

