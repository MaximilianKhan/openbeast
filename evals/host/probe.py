"""Integrity probe for the containerised eval host (scripts/eval-host-container.sh check).

Model-free and quick. Run INSIDE the container by the eval user, from the
mounted checkout, with the eval venv's python. It asserts what the harness's
own defences need, using the harness's own code (evals/vlib.py), so a pass
here means the shipped harness enforces it, not a re-implementation:

  pins       vlib.pin_toolchain resolves go, rustc, zig, gcc, g++ to
             root-owned binaries, go/rustc/zig to the pinned prefix
  planted    a fake `go`/`gcc` first on PATH in a user-writable dir (the
             review 1116 attack) is NOT what gets pinned, and a fake planted
             after pinning is caught by vlib.check_toolchain
  confine    vlib.run_solution(confine=...) confines a solution: it cannot
             read outside its allowlist, write outside its dir, open an
             AF_INET socket (TCP or UDP), and the mode is landlock

Exit 0 when every line is ok. Prints one line per assertion.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # evals/
import vlib  # noqa: E402

PREFIX = os.environ.get("OPENBEAST_EVAL_PREFIX", "/opt/openbeast-eval")
BAD = []


def report(ok: bool, what: str, detail: str = "") -> None:
    print(f"{'ok' if ok else 'FAIL':<5} {what}{': ' + detail if detail else ''}", flush=True)
    if not ok:
        BAD.append(what)


def pins() -> None:
    p = vlib.pin_toolchain()
    for name in ("go", "rustc", "zig", "gcc", "g++"):
        pin = p.get(name) or {}
        path = pin.get("path")
        ok = bool(path) and vlib._root_owned(path)
        if name in ("go", "rustc", "zig"):
            ok = ok and os.path.realpath(path).startswith(PREFIX + "/")
        report(ok, f"pin {name}", f"{path} (root-owned={bool(path) and vlib._root_owned(path)})")


def _fake(d: str, name: str) -> str:
    f = os.path.join(d, name)
    with open(f, "w") as fh:
        fh.write("#!/bin/sh\necho planted >&2\nexit 0\n")
    os.chmod(f, 0o755)
    return f


def planted() -> None:
    d = tempfile.mkdtemp(prefix="planted-", dir=os.path.expanduser("~"))
    try:
        for n in ("go", "gcc", "rustc"):
            _fake(d, n)
        path = d + os.pathsep + os.environ.get("PATH", "")
        p = vlib.pin_toolchain(("go", "gcc", "rustc"), path=path)
        for n in ("go", "gcc", "rustc"):
            got = (p.get(n) or {}).get("path") or ""
            report(bool(got) and not got.startswith(d) and vlib._root_owned(got),
                   f"planted {n} first on PATH is not pinned", got)
        # planted AFTER the pin (what a measured agent would do): refused
        clean = os.environ.get("PATH", "")
        p2 = vlib.pin_toolchain(("go", "gcc"), path=clean)
        probs = vlib.check_toolchain(p2, path=path)
        report(len(probs) >= 2, "planted after pinning is caught by check_toolchain",
               "; ".join(probs)[:200])
        # and the prefix is not writable by this user
        try:
            with open(os.path.join(PREFIX, "bin", "probe-write"), "w"):
                pass
            report(False, "prefix is read-only to the harness user", "a write succeeded")
        except OSError as e:
            report(True, "prefix is read-only to the harness user", e.strerror or str(e))
    finally:
        shutil.rmtree(d, ignore_errors=True)


SOL = r'''
import os, socket, sys
out = []
def t(name, fn):
    try:
        fn(); out.append(name + "=allowed")
    except OSError as e:
        out.append(name + "=denied")
t("read_passwd", lambda: open("/etc/passwd").read())
t("read_repo", lambda: open(sys.argv[1]).read())
t("write_outside", lambda: open(os.path.expanduser("~/probe-outside"), "w").write("x"))
t("write_own", lambda: open("own.txt", "w").write("x"))
t("tcp", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
t("udp", lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
t("tcp6", lambda: socket.socket(socket.AF_INET6, socket.SOCK_STREAM))
print(" ".join(out))
'''

WANT = {"read_passwd": "denied", "read_repo": "denied", "write_outside": "denied",
        "write_own": "allowed", "tcp": "denied", "udp": "denied", "tcp6": "denied"}


def confine() -> None:
    abi = vlib._landlock_abi()
    report(abi >= 4, "landlock available", f"abi {abi} (>= 4: TCP rules; seccomp filter for the rest)")
    work = vlib.private_build_dir({"sol.py": SOL.encode()})
    try:
        repo_file = os.path.join(os.path.dirname(HERE), "vlib.py")
        r = vlib.run_solution(vlib.python_cmd(os.path.join(work, "sol.py"), repo_file),
                              cpu_s=10, wall_s=20, cwd=work, confine=[work],
                              read_only=vlib.python_runtime(sys.executable, ("os", "socket", "sys")))
        report(r.verdict == "ok" and r.returncode == 0, "confined solution ran",
               f"verdict={r.verdict} rc={r.returncode} {(r.reason or r.stderr or '')[-200:]}")
        got = dict(kv.split("=", 1) for kv in (r.stdout or "").split() if "=" in kv)
        for k, want in WANT.items():
            report(got.get(k) == want, f"confine {k} {want}", got.get(k, "missing"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            os.remove(os.path.expanduser("~/probe-outside"))
        except OSError:
            pass


def main() -> int:
    st = os.stat(PREFIX)
    report(st.st_uid == 0 and not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and os.getuid() != 0,
           "prefix root-owned, harness user is not root", f"uid {os.getuid()}")
    pins()
    planted()
    confine()
    print(f"probe: {'all ok' if not BAD else str(len(BAD)) + ' FAILED: ' + ', '.join(BAD)}")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
