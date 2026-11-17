#!/usr/bin/env python3
"""The host pack: what THIS tool host's userland and libc lack, probed, never asserted.

WHY (GLM-5.3-Flash v5 failure taxonomy, 2026-11-16, 153 failed units):
host friction (macOS/BSD userland and libc) cost about 95 turns, more than
every non-zig knowledge error combined. `nproc: command not found`,
`grep -P`, `cat -A`, `timeout`, `/usr/bin/time -v`, `pthread_barrier_t` and
`sysctlbyname` without its header, in every language. runner v2's
platform_line() (agents/runner.py) already names the OS and the worst three
(BSD `sed -i ''`, no GNU `timeout`, no `cat -A`) on Darwin; this pack adds
the specific gaps and their workarounds.

THE RULE: every line comes from a probe that RAN on this host — a command
that failed, a 3-line C file that did not compile. A fact is a probe result
plus a remedy; nothing is rendered because "macOS is like that". A Linux host
with GNU coreutils and glibc gets Linux facts or none, and an empty pack is
not delivered at all. Remedies that name another tool (perl, sysctl) are
only offered when that tool's own probe ran too.

WHERE IT GOES. Eval only, today: evals/run_eval.py packs_flag() builds it
once per run when BEAST_PACKS=1 and hands it to EVERY unit (all languages)
through the same --context-file channel as the language pack; a zig unit
gets one file with both. Its sha is part of the pack1-<sha8> cache
component. Production agents do NOT get it (agents/lang/pack_context.py
serves only measured packs); it becomes a production treatment only after
an eval arm measures it.

DETERMINISM. The text holds no timestamps, CPU counts or paths that vary
between runs on one host, so the same host renders the same bytes and the
cache component is stable; a toolchain or OS update changes the text and,
correctly, the era.

  python3 agents/lang/host_pack.py            # print the pack for this host
  python3 agents/lang/host_pack.py --facts    # the probe results as JSON
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Callable

#: The rough 4-chars/token estimate the zig pack budgets with.
CHARS_PER_TOKEN = 4
TOKEN_BUDGET = 400
PROBE_TIMEOUT_S = 20

HEADER = "=== Host notes: {system} tool host (probed when this run started) ==="
INTRO = ("Each line below is a probe that ran on the machine where your tools "
         "run; trust it over habit.")


# --------------------------------------------------------------------------
# the probe interface — fakeable: tests hand in their own run/which
# --------------------------------------------------------------------------

@dataclass
class Result:
    rc: int
    out: str = ""
    err: str = ""


Run = Callable[..., Result]


def real_run(argv: list[str], stdin: str = "", timeout: float = PROBE_TIMEOUT_S,
             cwd: str | None = None) -> Result:
    try:
        r = subprocess.run(argv, input=stdin, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd,
                           env={**os.environ, "LC_ALL": "C"})
        return Result(r.returncode, r.stdout, r.stderr)
    except (OSError, subprocess.SubprocessError) as e:
        return Result(127, "", f"{type(e).__name__}: {e}")


def real_which(name: str) -> str | None:
    return shutil.which(name)


@dataclass
class Host:
    """What a probe may touch. `exists` is for /proc-style paths."""
    system: str
    run: Run = real_run
    which: Callable[[str], str | None] = real_which
    exists: Callable[[str], bool] = os.path.exists
    shell: str = ""

    def __post_init__(self):
        if not self.shell:
            self.shell = "/bin/bash" if os.access("/bin/bash", os.X_OK) else "/bin/sh"


@dataclass
class Fact:
    key: str
    line: str                       # rendered text, "" = probed but not rendered
    probe: str                      # what ran
    evidence: str = ""              # the output that decided it
    covered_by_platform_line: bool = False

    def as_dict(self) -> dict:
        return {"key": self.key, "line": self.line, "probe": self.probe,
                "evidence": self.evidence[:160],
                "covered_by_platform_line": self.covered_by_platform_line}


def _first(s: str) -> str:
    return (s.strip().splitlines() or [""])[0].strip()


# --------------------------------------------------------------------------
# userland probes
# --------------------------------------------------------------------------

def probe_sed(h: Host) -> Fact | None:
    """BSD sed: `sed --version` is not an option it knows. On Darwin the
    platform line already says `sed -i ''`, so the fact is recorded but not
    rendered there (no duplicate)."""
    if not h.which("sed"):
        return None
    r = h.run(["sed", "--version"])
    if r.rc == 0 and "GNU" in r.out:
        return None
    return Fact("sed_bsd", "BSD sed: in-place edit is `sed -i '' 's/a/b/' f` (no `-i` alone, no `\\n` in replacements).",
                "sed --version", _first(r.err or r.out),
                covered_by_platform_line=h.system == "Darwin")


def _perl_alarm_ok(h: Host) -> bool:
    if not h.which("perl"):
        return False
    return h.run(["perl", "-e", "alarm shift; exec @ARGV", "5", "true"]).rc == 0


def probe_timeout(h: Host) -> Fact | None:
    if h.which("timeout"):
        return None
    g = " (`gtimeout` is installed)" if h.which("gtimeout") else ""
    if _perl_alarm_ok(h):
        fix = "`perl -e 'alarm shift; exec @ARGV' 10 ./prog` (or `./prog & p=$!; sleep 10; kill $p`)"
    else:
        fix = "`./prog & p=$!; sleep 10; kill $p`"
    return Fact("timeout_missing", f"no `timeout`{g}: {fix}.", "command -v timeout; perl alarm",
                "timeout: not found")


def probe_nproc(h: Host) -> Fact | None:
    if h.which("nproc"):
        return None
    no_proc = not h.exists("/proc/cpuinfo")
    tail = " and no /proc" if no_proc else ""
    r = h.run(["sysctl", "-n", "hw.ncpu"]) if h.which("sysctl") else Result(127)
    if r.rc == 0 and r.out.strip().isdigit():
        fix = "`sysctl -n hw.ncpu`"
    elif h.run(["getconf", "_NPROCESSORS_ONLN"]).rc == 0:
        fix = "`getconf _NPROCESSORS_ONLN`"
    else:
        fix = "hard-code a thread count"
    return Fact("nproc_missing", f"no `nproc`{tail}: CPU count is {fix}.",
                "command -v nproc; sysctl -n hw.ncpu", "nproc: not found")


def probe_grep_p(h: Host) -> Fact | None:
    if not h.which("grep"):
        return None
    r = h.run(["grep", "-P", "a\\d"], stdin="a1\n")
    if r.rc == 0:
        return None
    perl = " or `perl -ne 'print if /re/'`" if h.which("perl") else ""
    return Fact("grep_no_P", f"`grep -P` unsupported: use `grep -E`{perl}.",
                "echo a1 | grep -P 'a\\d'", _first(r.err))


def probe_cat_a(h: Host) -> Fact | None:
    r = h.run(["cat", "-A"], stdin="x\t\n")
    if r.rc == 0:
        return None
    v = h.run(["cat", "-vet"], stdin="x\t\n")
    fix = "`cat -vet`" if v.rc == 0 else "`od -c`"
    return Fact("cat_no_A", f"no `cat -A`: use {fix} to see tabs and line ends.",
                "printf 'x\\t\\n' | cat -A", _first(r.err),
                # the platform line names the gap; this adds the working flag
                covered_by_platform_line=False)


def probe_time_v(h: Host) -> Fact | None:
    if not h.exists("/usr/bin/time"):
        return None
    r = h.run(["/usr/bin/time", "-v", "true"])
    if r.rc == 0:
        return None
    alt = h.run(["/usr/bin/time", "-l", "true"])
    fix = "`/usr/bin/time -l` (max RSS in bytes)" if alt.rc == 0 else "`time` (bash builtin)"
    return Fact("time_no_v", f"`/usr/bin/time -v` unsupported: use {fix}.",
                "/usr/bin/time -v true", _first(r.err))


def probe_bash(h: Host) -> Fact | None:
    """Which bash-4 features the tool's shell lacks — each one run, not
    inferred from the version number."""
    if not h.exists(h.shell):
        return None
    ver = _first(h.run([h.shell, "--version"]).out)
    m = re.search(r"version (\d+\.\d+(?:\.\d+)?)", ver)
    missing = []
    for feat, cmd in (("declare -A", "declare -A a"),
                      ("mapfile", "mapfile -t a < /dev/null"),
                      ("${v,,}", "v=A; echo ${v,,}")):
        if h.run([h.shell, "-c", cmd]).rc != 0:
            missing.append(f"`{feat}`")
    if not missing:
        return None
    name = os.path.basename(h.shell)
    v = f" {m.group(1)}" if m else ""
    return Fact("bash_old", f"{h.shell} is {name}{v}: no {', '.join(missing)}.",
                f"{h.shell} -c 'declare -A' / mapfile / ${{v,,}}", ver)


# --------------------------------------------------------------------------
# libc / compiler probes — a 3-line file through the host's own compiler
# --------------------------------------------------------------------------

def _compiles(h: Host, compiler: str, lang: str, src: str) -> Result:
    return h.run([compiler, "-fsyntax-only", "-x", lang, "-"], stdin=src)


def _cc(h: Host) -> str | None:
    for c in ("cc", "clang", "gcc"):
        if h.which(c):
            return c
    return None


def _cxx(h: Host) -> str | None:
    for c in ("c++", "clang++", "g++"):
        if h.which(c):
            return c
    return None


def probe_c(h: Host) -> list[Fact]:
    facts: list[Fact] = []
    cc, cxx = _cc(h), _cxx(h)
    if cc:
        r = _compiles(h, cc, "c", "#include <pthread.h>\npthread_barrier_t b;\nint main(void){return 0;}\n")
        if r.rc != 0:
            facts.append(Fact("no_pthread_barrier",
                              "no `pthread_barrier_t` in this libc: build a barrier from a "
                              "pthread_mutex_t + pthread_cond_t + counter, or spin on an atomic.",
                              f"{cc} -fsyntax-only: pthread_barrier_t", _first(r.err)))
        for key, fn, hdr, call in (
                ("sysctlbyname_header", "sysctlbyname", "sys/sysctl.h",
                 'size_t n = 0; return sysctlbyname("hw.ncpu", 0, &n, 0, 0);'),
                ("sysconf_header", "sysconf", "unistd.h",
                 "return (int)sysconf(_SC_NPROCESSORS_ONLN);")):
            body = "#include <stddef.h>\n{inc}int main(void){{ " + call + " }}\n"
            with_h = _compiles(h, cc, "c", body.format(inc=f"#include <{hdr}>\n"))
            without = _compiles(h, cc, "c", body.format(inc=""))
            if with_h.rc == 0 and without.rc != 0:
                facts.append(Fact(key, f"`{fn}` needs `#include <{hdr}>`.",
                                  f"{cc} -fsyntax-only: {fn} with/without <{hdr}>",
                                  _first(without.err)))
    if cxx:
        r = _compiles(h, cxx, "c++", "#include <bits/stdc++.h>\nint main(){return 0;}\n")
        if r.rc != 0:
            facts.append(Fact("no_bits_stdcpp",
                              "no `<bits/stdc++.h>`: include the real headers "
                              "(<vector>, <algorithm>, <cstdio>, ...).",
                              f"{cxx} -fsyntax-only: bits/stdc++.h", _first(r.err)))
    return facts


def compiler_name(h: Host) -> str:
    cc = _cc(h)
    if not cc:
        return ""
    v = _first(h.run([cc, "--version"]).out)
    m = re.match(r"(Apple clang|clang|gcc|cc)[^\d]*(\d+(?:\.\d+)*)", v)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = re.search(r"\((?:GCC|Debian|Ubuntu)[^)]*\)\s+(\d+(?:\.\d+)*)", v)
    return f"gcc {m.group(1)}" if m else v[:40]


USERLAND_PROBES = (probe_sed, probe_timeout, probe_nproc, probe_grep_p, probe_cat_a,
                   probe_time_v, probe_bash)


def probe(h: Host) -> list[Fact]:
    facts: list[Fact] = []
    for p in USERLAND_PROBES:
        try:
            f = p(h)
        except Exception:                              # noqa: BLE001 — a probe never kills a run
            f = None
        if f:
            facts.append(f)
    try:
        facts += probe_c(h)
    except Exception:                                  # noqa: BLE001
        pass
    return facts


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

@dataclass
class HostPack:
    system: str
    text: str
    facts: list[Fact] = field(default_factory=list)

    @property
    def data(self) -> bytes:
        return self.text.encode()

    @property
    def sha8(self) -> str:
        return hashlib.sha256(self.data).hexdigest()[:8]

    @property
    def tokens(self) -> int:
        return (len(self.data) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def render(system: str, facts: list[Fact], compiler: str = "",
           budget_tokens: int = TOKEN_BUDGET) -> str:
    """The pack text, or "" when no probe found a gap worth a line. Facts the
    runner's platform line already states are left out; lines that would
    break the budget are dropped from the end (C facts last)."""
    lines = [f.line for f in facts if f.line and not f.covered_by_platform_line]
    if not lines:
        return ""
    c_keys = {"no_pthread_barrier", "sysctlbyname_header", "sysconf_header", "no_bits_stdcpp"}
    out = [HEADER.format(system=system), INTRO]
    user = [f"- {f.line}" for f in facts
            if f.line and not f.covered_by_platform_line and f.key not in c_keys]
    c = [f.line for f in facts if f.key in c_keys and f.line]
    out += user
    if c:
        label = f"C/C++ ({compiler})" if compiler else "C/C++"
        out.append(f"- {label}: " + " ".join(c))
    text = "\n".join(out) + "\n"
    budget = budget_tokens * CHARS_PER_TOKEN
    while len(text.encode()) > budget and len(out) > 2:
        out.pop()
        text = "\n".join(out) + "\n"
    return text if len(out) > 2 else ""


_CACHE: dict = {}


def build(host: Host | None = None) -> HostPack:
    """Probe and render. Memoised per process for the real host (an arm
    scheduler builds every arm's ArmCtx; the host does not change between)."""
    real = host is None
    if real and "pack" in _CACHE:
        return _CACHE["pack"]
    h = host or Host(platform.system() or "unknown")
    facts = probe(h)
    comp = compiler_name(h) if any(f.key in ("no_pthread_barrier", "sysctlbyname_header",
                                             "sysconf_header", "no_bits_stdcpp")
                                   for f in facts) else ""
    pack = HostPack(h.system, render(h.system, facts, comp), facts)
    if real:
        _CACHE["pack"] = pack
    return pack


def write(pack: "HostPack | bytes", directory: str | None = None) -> str:
    """Content-addressed 0600 file for --context-file; same bytes, same path.
    Takes the pack, or any bytes (run_eval writes a language pack joined
    with the host pack through here too)."""
    data = pack.data if isinstance(pack, HostPack) else pack
    d = directory or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
        "openbeast", "host-pack")
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, f"host-{hashlib.sha256(data).hexdigest()[:24]}.md")
    if not os.path.exists(path):
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".host-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = build()
    if "--facts" in argv:
        print(json.dumps({"system": p.system, "sha8": p.sha8, "tokens": p.tokens,
                          "facts": [f.as_dict() for f in p.facts]}, indent=2))
        return 0
    sys.stdout.write(p.text or f"(no host gaps probed on this {p.system} host — empty pack)\n")
    print(f"# {p.tokens} tokens, sha {p.sha8}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
