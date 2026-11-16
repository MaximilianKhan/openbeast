# Runner for 196_polyphase_resampler. Embedded into the validator by build.py
# and executed in a separate process (vlib.run_solution, `python -I -S -`
# with this source on stdin, prefixed by `CASES = <path>`): it imports the
# submission, reads the case INPUTS of ONE group (JSON in the file CASES; no
# expected values or tone parameters are in this process), and writes the
# outputs as raw float64 on a private copy of fd 1. The submission's prints
# go to stderr.
#
# The validator starts one such process per case group, and a group never
# holds two cases that could help each other: a stream is never in the same
# process as the one-shot run of its input, the two halves of a locality
# pair run in different processes, and a stream PREFIX run is fed only the
# samples up to its cut, so no state, cache or peek can see the future.
#
# Nothing of the cases is kept in module globals (sys.modules['__main__'] is
# reachable without introspection): it all lives in main()'s locals, which
# only a frame object can reach, and the audit hook refuses every route to a
# frame (tb_frame / gi_frame / __code__ reads raise 'object.__getattr__';
# sys._getframe, settrace, setprofile, sys.monitoring; gc's object walks).
# Files cannot be opened at all once the submission runs (its source is
# read and compiled before the hook goes in), and os.* calls are refused, so
# no group can leave anything behind for a later one.
import array, json, math, os, struct, sys


def main(cases_path, sub_path):
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)

    def emit(tag, vals):
        a = array.array("d", vals)
        out.write(struct.pack("<cq", tag, len(a)))
        out.write(a.tobytes())

    def as_floats(v):
        if not isinstance(v, list):
            raise TypeError("not a list")
        for e in v:
            if type(e) not in (float, int) or not math.isfinite(e):
                raise TypeError("non-finite or non-float element")
        return v

    with open(cases_path) as f:
        cases = json.load(f)
    with open(sub_path, "rb") as f:
        src = f.read()

    # Enforce the import rule at run time as well as in the lint: preload
    # every allowed module, then the audit hook refuses any further module
    # load, any file open, any os / process / ctypes / socket call and any
    # frame introspection.
    for m in ("math", "cmath", "array", "itertools", "functools", "operator",
              "bisect", "collections", "collections.abc", "typing",
              "dataclasses", "fractions", "decimal", "abc", "enum", "numbers",
              "copy", "heapq"):
        __import__(m)
    code = compile(src, sub_path, "exec")
    mod = type(sys)("resample196")
    mod.__file__ = sub_path
    sys.modules["resample196"] = mod

    deny_prefix = ("import", "open", "os.", "subprocess.", "ctypes.",
                   "socket.", "_posixsubprocess.", "pty.", "shutil.", "gc.",
                   "mmap.", "sys.monitoring", "object.__getattr__",
                   "cpython.")
    deny_exact = {"sys._getframe", "sys._current_frames",
                  "sys._current_exceptions", "sys.settrace", "sys.setprofile",
                  "sys._settraceallthreads", "sys._setprofileallthreads",
                  "sys.addaudithook"}

    def audit(ev, args):
        if ev in deny_exact or ev.startswith(deny_prefix):
            raise PermissionError("%s is not allowed" % ev)

    sys.addaudithook(audit)
    exec(code, mod.__dict__)

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
                if c["mode"] == "stream":
                    y += as_floats(r.flush())
                emit(b"C", counts)
                emit(b"Y", y)
        except TypeError:
            emit(b"T", [])
        except Exception:
            emit(b"E", [])
        out.flush()
    out.close()


main(CASES, sys.argv[1])  # noqa: F821 (CASES is prefixed by the validator)
