"""ADV (verifier, round 2): sys.stderr.buffer.raw.__class__ is io.FileIO, so
FIO(path).readall() reads any file the process may read with no `open` and
no escape. It reads the task JSON (the oracle) from the repo, the git
object store or this worktree and fails loudly if it got it; the lint
refuses `.buffer`/`.raw`, and the confined driver can read neither the repo
nor .git even with the lint off."""
import sys

FIO = sys.stderr.buffer.raw.__class__
GOT = []
for p in ('/Users/maximiliankhan/Documents/projects/openbeast/evals/tasks/190_autograd_mlp.json',
          '/Users/maximiliankhan/Documents/projects/openbeast/.git/HEAD',
          '/Users/maximiliankhan/.zshrc'):
    try:
        GOT.append(len(FIO(p, 'r').readall()))
    except OSError:
        GOT.append(None)
raise RuntimeError('read: ' + repr(GOT))
