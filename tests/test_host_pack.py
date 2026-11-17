#!/usr/bin/env python3
"""The host pack (agents/lang/host_pack.py): probed host facts, never asserted.

Every probe runs through a fake `run` / `which` / `exists` here, fed the real
outputs of a macOS 26 host (BSD userland, Apple clang, bash 3.2) and of a
Debian host (GNU coreutils, glibc, gcc, bash 5). The rule pinned: a fact
appears only when its probe says so, so the Linux host renders nothing.
One test runs the real probes on whatever box runs the suite and checks only
the contract (budget, determinism), not which facts it found.

Run: python3 -m pytest tests/test_host_pack.py -q
"""
import os
import platform
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "agents"))

from lang import host_pack as HP  # noqa: E402

R = HP.Result

DARWIN_BINS = {"sed", "grep", "cat", "perl", "sysctl", "cc", "c++", "getconf"}
LINUX_BINS = {"sed", "grep", "cat", "perl", "cc", "c++", "gcc", "g++", "timeout",
              "nproc", "getconf"}


def darwin_run(argv, stdin="", timeout=None, cwd=None):
    a = " ".join(argv)
    if a == "sed --version":
        return R(1, "", "sed: illegal option -- -\nusage: sed script [-Ealnru] ...")
    # BSD sed: `\b` is a literal b, so the substitution is a silent no-op;
    # the [[:<:]] word bounds and perl both do the replacement.
    if a == "sed s/\\bi0\\b/lo/g":
        return R(0, stdin)
    if a == "sed -E s/[[:<:]]i0[[:>:]]/lo/g" or a == "perl -pe s/\\bi0\\b/lo/g":
        return R(0, "lo xi0 lo\n")
    if a == "perl -e alarm shift; exec @ARGV 5 true":
        return R(0)
    if a == "sysctl -n hw.ncpu":
        return R(0, "10\n")
    if a.startswith("grep -P"):
        return R(2, "", "grep: invalid option -- P\nusage: grep ...")
    if a == "cat -A":
        return R(1, "", "cat: illegal option -- A\nusage: cat [-belnstuv] [file ...]")
    if a == "cat -vet":
        return R(0, "x^I$\n")
    if a == "/usr/bin/time -v true":
        return R(1, "", "/usr/bin/time: illegal option -- v")
    if a == "/usr/bin/time -l true":
        return R(0, "", "  0.00 real ... 1234 maximum resident set size")
    if a == "/bin/bash --version":
        return R(0, "GNU bash, version 3.2.57(1)-release (arm64-apple-darwin25)\n")
    if argv[:2] == ["/bin/bash", "-c"]:
        return R(2, "", "bash: bad")            # declare -A, mapfile, ${v,,}: all bash 4
    if argv[0] in ("cc", "c++"):
        if "pthread_barrier_t" in stdin:
            return R(1, "", "<stdin>:2:1: error: unknown type name 'pthread_barrier_t'")
        if "bits/stdc++.h" in stdin:
            return R(1, "", "<stdin>:1:10: fatal error: 'bits/stdc++.h' file not found")
        if "sysctlbyname" in stdin:
            return R(0) if "sys/sysctl.h" in stdin else R(1, "", "error: call to undeclared function 'sysctlbyname'")
        if "sysconf" in stdin:
            return R(0) if "unistd.h" in stdin else R(1, "", "error: call to undeclared function 'sysconf'")
        if argv[1:] == ["--version"]:
            return R(0, "Apple clang version 21.0.0 (clang-2100.0.123.102)\nTarget: arm64-apple-darwin25\n")
    return R(127, "", f"unexpected probe {a}")


def linux_run(argv, stdin="", timeout=None, cwd=None):
    a = " ".join(argv)
    if a == "sed --version":
        return R(0, "sed (GNU sed) 4.9\n")
    if a == "sed s/\\bi0\\b/lo/g":                 # GNU sed knows \b
        return R(0, "lo xi0 lo\n")
    if a.startswith("grep -P"):
        return R(0, "a1\n")
    if a == "cat -A":
        return R(0, "x^I$\n")
    if a == "/usr/bin/time -v true":
        return R(0, "", "\tCommand being timed: \"true\"\n")
    if a == "/bin/bash --version":
        return R(0, "GNU bash, version 5.2.21(1)-release (aarch64-unknown-linux-gnu)\n")
    if argv[:2] == ["/bin/bash", "-c"]:
        return R(0)
    if argv[0] in ("cc", "c++"):
        if "pthread_barrier_t" in stdin or "bits/stdc++.h" in stdin:
            return R(0)
        if "sysctlbyname" in stdin:      # glibc has no sysctlbyname at all
            return R(1, "", "error: implicit declaration of function 'sysctlbyname'")
        if "sysconf" in stdin:           # gcc 13: implicit decl is a warning
            return R(0)
        if argv[1:] == ["--version"]:
            return R(0, "cc (Debian 13.2.0-25) 13.2.0\n")
    return R(127, "", f"unexpected probe {a}")


def darwin():
    return HP.Host("Darwin", run=darwin_run, which=lambda n: f"/usr/bin/{n}" if n in DARWIN_BINS else None,
                   exists=lambda p: p in ("/usr/bin/time", "/bin/bash"), shell="/bin/bash")


def linux():
    return HP.Host("Linux", run=linux_run, which=lambda n: f"/usr/bin/{n}" if n in LINUX_BINS else None,
                   exists=lambda p: p in ("/usr/bin/time", "/bin/bash", "/proc/cpuinfo"), shell="/bin/bash")


# --- the probes ------------------------------------------------------------------

def test_darwin_probes_find_every_audited_gap():
    keys = [f.key for f in HP.probe(darwin())]
    assert keys == ["sed_bsd", "timeout_missing", "nproc_missing", "grep_no_P", "cat_no_A",
                    "time_no_v", "bash_old", "sed_no_word_boundary", "no_pthread_barrier",
                    "sysctlbyname_header", "sysconf_header", "no_bits_stdcpp"]


def test_sed_word_boundary_is_probed_by_running_it():
    """C 122_e it6 (2026-11-17): `sed 's/\\bi0\\b/lo/g'` exited 0 and changed
    nothing on BSD sed. The fact comes from running that substitution; each
    remedy is offered only when it ran here and did the replacement."""
    f = {x.key: x for x in HP.probe(darwin())}["sed_no_word_boundary"]
    assert "silently changes nothing" in f.line
    assert "[[:<:]]w[[:>:]]" in f.line and "perl -pi -e" in f.line
    assert f.evidence == "i0 xi0 i0"                    # the unchanged output
    h = darwin()

    def run(argv, **kw):
        if argv[:2] == ["sed", "-E"]:
            return R(1, "", "sed: RE error: invalid character class")
        return darwin_run(argv, **kw)
    h.run = run
    h.which = lambda n: f"/usr/bin/{n}" if n in DARWIN_BINS - {"perl"} else None
    line = {x.key: x.line for x in HP.probe(h)}["sed_no_word_boundary"]
    assert "[[:<:]]" not in line and "perl" not in line
    assert "sed_no_word_boundary" not in {x.key for x in HP.probe(linux())}


def test_linux_probes_find_nothing():
    """A GNU/glibc host: every probe ran, none failed, so no fact — and
    sysctlbyname (absent from glibc) is NOT reported as a header problem."""
    assert HP.probe(linux()) == []
    assert HP.build(linux()).text == ""


def test_each_fact_carries_its_probe_evidence():
    facts = {f.key: f for f in HP.probe(darwin())}
    assert facts["grep_no_P"].evidence == "grep: invalid option -- P"
    assert "pthread_barrier_t" in facts["no_pthread_barrier"].evidence
    assert facts["bash_old"].evidence.startswith("GNU bash, version 3.2.57")
    assert facts["nproc_missing"].line.endswith("`sysctl -n hw.ncpu`.")
    assert "no /proc" in facts["nproc_missing"].line


def test_remedies_are_only_offered_when_their_tool_probed():
    """No perl → the timeout remedy is the background kill, and grep -P's
    perl alternative disappears; no sysctl → getconf."""
    bins = DARWIN_BINS - {"perl", "sysctl"}
    h = HP.Host("Darwin", run=darwin_run, which=lambda n: f"/usr/bin/{n}" if n in bins else None,
                exists=lambda p: p == "/bin/bash", shell="/bin/bash")

    def run(argv, **kw):
        if argv == ["getconf", "_NPROCESSORS_ONLN"]:
            return R(0, "10\n")
        return darwin_run(argv, **kw)
    h.run = run
    facts = {f.key: f.line for f in HP.probe(h)}
    assert "perl" not in facts["timeout_missing"] and "kill $p" in facts["timeout_missing"]
    assert "perl" not in facts["grep_no_P"]
    assert "getconf _NPROCESSORS_ONLN" in facts["nproc_missing"]
    assert "time_no_v" not in facts            # no /usr/bin/time on this host


def test_a_partly_gnu_darwin_reports_only_what_is_missing():
    """Homebrew coreutils on PATH: timeout and nproc exist, so no line says
    they don't — the probe, not the OS name, decides."""
    bins = DARWIN_BINS | {"timeout", "nproc"}
    h = darwin()
    h.which = lambda n: f"/usr/bin/{n}" if n in bins else None
    keys = {f.key for f in HP.probe(h)}
    assert "timeout_missing" not in keys and "nproc_missing" not in keys
    assert "grep_no_P" in keys


def test_bash_probe_names_only_the_failing_features():
    h = darwin()

    def run(argv, **kw):
        if argv[:2] == ["/bin/bash", "-c"]:
            return R(0) if argv[2].startswith("mapfile") else R(2)
        return darwin_run(argv, **kw)
    h.run = run
    line = {f.key: f.line for f in HP.probe(h)}["bash_old"]
    assert line == "/bin/bash is bash 3.2.57: no `declare -A`, `${v,,}`."


def test_a_crashing_probe_is_no_fact_not_a_crash():
    h = darwin()

    def run(argv, **kw):
        if argv[0] == "cat":
            raise RuntimeError("boom")
        return darwin_run(argv, **kw)
    h.run = run
    keys = {f.key for f in HP.probe(h)}
    assert "cat_no_A" not in keys and "grep_no_P" in keys


# --- rendering ---------------------------------------------------------------------

def test_darwin_render_within_budget_and_no_platform_line_duplicate():
    p = HP.build(darwin())
    assert p.text.startswith("=== Host notes: Darwin tool host")
    assert p.tokens <= HP.TOKEN_BUDGET
    # runner.platform_line already states BSD sed on Darwin: not repeated
    assert "sed -i ''" not in p.text
    for s in ("sysctl -n hw.ncpu", "grep -E", "cat -vet", "pthread_barrier_t", "bits/stdc++.h",
              "<sys/sysctl.h>", "<unistd.h>", "bash 3.2.57", "alarm shift", "/usr/bin/time -l",
              "C/C++ (Apple clang 21.0.0)"):
        assert s in p.text, s


def test_sed_is_rendered_where_no_platform_line_covers_it():
    """A BSD host that is not Darwin (FreeBSD): the platform line says
    nothing about sed there, so the host pack does."""
    h = darwin()
    h.system = "FreeBSD"
    assert "sed -i ''" in HP.build(h).text


def test_render_is_deterministic():
    assert HP.build(darwin()).data == HP.build(darwin()).data


def test_render_drops_lines_from_the_end_to_fit_the_budget():
    facts = HP.probe(darwin())
    small = HP.render("Darwin", facts, "Apple clang 21.0.0", budget_tokens=80)
    assert len(small.encode()) <= 80 * HP.CHARS_PER_TOKEN
    assert "no `timeout`" in small and "C/C++" not in small
    assert HP.render("Darwin", facts, budget_tokens=10) == ""


def test_render_says_nothing_new_is_empty():
    sed_only = [f for f in HP.probe(darwin()) if f.key == "sed_bsd"]
    assert HP.render("Darwin", sed_only) == ""


def test_write_is_content_addressed_and_private(tmp_path):
    p = HP.build(darwin())
    a = HP.write(p, str(tmp_path))
    b = HP.write(p.data, str(tmp_path))
    assert a == b and open(a, "rb").read() == p.data
    assert oct(os.stat(a).st_mode & 0o777) == "0o600"


def test_the_real_host_obeys_the_contract():
    """Whatever this box is: the probes run, the pack fits, and probing twice
    gives the same bytes (the cache component depends on it)."""
    a = HP.build(HP.Host(platform.system()))
    b = HP.build(HP.Host(platform.system()))
    assert a.data == b.data
    assert a.tokens <= HP.TOKEN_BUDGET
    for f in a.facts:
        assert f.probe and f.key
    import shutil
    keys = {f.key for f in a.facts}
    assert ("nproc_missing" in keys) == (shutil.which("nproc") is None)
    assert ("timeout_missing" in keys) == (shutil.which("timeout") is None)
