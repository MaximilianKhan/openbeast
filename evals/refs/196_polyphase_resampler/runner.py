# Runner for 196_polyphase_resampler. Embedded into the validator by build.py
# and executed in a separate process: it imports the submission, reads the
# cases (JSON on stdin), and writes the outputs as raw float64 on a private
# copy of fd 1. The submission's own prints go to stderr.
import array, importlib.util, json, math, os, struct, sys

_out = os.fdopen(os.dup(1), "wb")
os.dup2(2, 1)


def emit(tag, vals):
    a = array.array("d", vals)
    _out.write(struct.pack("<cq", tag, len(a)))
    _out.write(a.tobytes())


def as_floats(v):
    if not isinstance(v, list):
        raise TypeError("not a list")
    for e in v:
        if type(e) not in (float, int) or not math.isfinite(e):
            raise TypeError("non-finite or non-float element")
    return v


cases = json.loads(sys.stdin.read())

# Enforce the import rule at run time as well as in the lint: preload every
# allowed module, then an audit hook refuses any further module load, any
# file open outside the task dir, and any process / ctypes / socket use.
for _m in ("math", "cmath", "array", "itertools", "functools", "operator",
           "bisect", "collections", "collections.abc", "typing",
           "dataclasses", "fractions", "decimal", "abc", "enum", "numbers",
           "copy", "heapq"):
    __import__(_m)
_DIR = os.path.dirname(os.path.abspath(sys.argv[1])) + os.sep
_DENY = ("import", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
         "os.fork", "os.forkpty", "subprocess.", "ctypes.", "socket.",
         "_posixsubprocess.", "pty.")


def _audit(ev, args):
    if ev == "open":
        path = args[0]
        if (isinstance(path, (str, bytes)) and
                os.path.abspath(os.fsdecode(path)).startswith(_DIR)):
            return
        raise PermissionError("open is not allowed")
    if ev.startswith(_DENY):
        raise PermissionError("%s is not allowed" % ev)


sys.addaudithook(_audit)
spec = importlib.util.spec_from_file_location("resample196", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

for c in cases:
    x = c["x"]
    try:
        if c["mode"] == "oneshot":
            y = as_floats(mod.resample(list(x)))
            emit(b"Y", y)
        else:
            r = mod.Resampler()
            y, counts, pos = [], [], 0
            for k in c["chunks"]:
                y += as_floats(r.process(list(x[pos:pos + k])))
                pos += k
                counts.append(len(y))
            y += as_floats(r.flush())
            emit(b"C", counts)
            emit(b"Y", y)
    except TypeError:
        emit(b"T", [])
    except Exception:
        emit(b"E", [])
    _out.flush()
_out.close()
