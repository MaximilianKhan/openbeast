#!/usr/bin/env python3
"""The Tier-3 awareness pack in PRODUCTION — the measured mechanism, reused.

WHAT WAS MEASURED (scratch/tier3-verdict-fresh-20260930.txt, era
b5596c660b5ab819): `agents/packs/zig-0.16.md`, byte-for-byte, handed to
`agents/runner.py --context-file <pack>` on zig tasks. Qwen3.8-27B-Uncensored:
SHIP, net +24 (26 rescues / 2 regressions, p<0.0001); the champion
Qwen3.6-27B also improved (23 -> 28 of 30). This module reproduces exactly
that delivery for agents started outside the eval harness — and nothing more:

  * THE SAME FILE. Only the committed pack whose sha256 is the one that was
    measured (MEASURED_SHA256) is injected, after the same drift check the
    harness runs at eval start (`pack_problem`, which evals/run_eval.py now
    calls too — one implementation, not two). A pack edited since the A/B is
    a different treatment; it is not served until it is re-measured and the
    pin moves. Any mismatch injects NOTHING and says so once.
  * THE SAME CHANNEL. `--context-file`, which the runner renders as its
    "Background context from the caller" block. runner.py is era-locked and
    already supports the flag, so no runner edit.
  * THE SAME POPULATION: agents on zig tasks. NOT Open WebUI chats and NOT
    opencode — the pack was never measured there.

DETECTION. A task is a zig task when its text names zig as a word (the same
`languages_in` rule the rest of beast-lang uses: "zig", "build.zig", ".zig",
never "zigzag"), or — when the text names no OTHER language — when the
working directory holds build.zig or *.zig in its top two levels (the workdir
and its immediate subdirectories, e.g. src/main.zig). The scan is bounded
(SCAN_MAX_ENTRIES) and skips VCS/dependency/cache directories, so an agent
started in $HOME costs a few hundred stat()s, not a crawl.

THE SWITCH. LANG_PACK_CONTEXT = auto (default) | off. Env
OPENBEAST_LANG_PACK_CONTEXT beats openbeast.conf, parsed the way
scripts/lib/conf.sh's _ob_bool parses: first token, trailing #comment and
quotes dropped; empty means the default; an unrecognised value means OFF
with a warning (a typo must not silently enable a treatment). LANG_PACKS
(which languages beast-lang serves at all) is honoured too: `LANG_PACKS=off`
or a list without zig means no zig pack here either.

NEVER UNDER EVAL. OPENBEAST_EVAL (set by run_eval.py in every child) or
OPENBEAST_TASK_PATHS in the environment turns this off unconditionally —
OPENBEAST_LANG_IN_EVAL does NOT re-enable it. An eval arm's pack is decided
by run_eval --packs and by nothing else, or the A/B stops being an A/B.

CALLER CONTEXT. runner.py's --context-file REPLACES --context (it does not
append), so when a caller has its own --context (start_skill_agent always
does — the skill body) the launcher gets ONE combined file: the caller's
context, then the pack. When the caller passed its own --context-file,
nothing is injected — the caller chose the context file explicitly, and
rewriting a file somebody else owns is not ours to do.

Everything here is FAIL-SOFT: a spawn never fails because of a pack. Any
exception means "no pack", logged once.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENTS = os.path.dirname(_HERE)
PACKS_DIR = os.path.join(_AGENTS, "packs")

#: Language -> committed pack file. The eval harness reads THIS map
#: (evals/run_eval.py PACK_FILES), so the two cannot name different files.
PACK_FILES = {"zig": "zig-0.16.md"}

#: sha256 of the pack bytes that the Tier-3 A/B measured (verdict line
#: `pack: {'zig': '5cdf4b64'}`). Production serves only these bytes. After an
#: edit to the pack, re-run the Tier-3 A/B and move this pin — until then the
#: edited pack is an unmeasured treatment and production serves no pack
#: (doctor.sh says so).
MEASURED_SHA256 = {
    "zig": "5cdf4b6417160751b95c3fd1e8a730193774a7061ce1be4f57435d591ae98127",
}

SCAN_MAX_ENTRIES = 500
SCAN_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "zig-cache", ".zig-cache",
    "zig-out", "__pycache__", ".venv", "venv", "target", "build", "dist",
})
#: Workdir markers per language (file names, then suffixes).
WORKDIR_MARKERS = {"zig": (("build.zig", "build.zig.zon"), (".zig",))}

_EVAL_ENV = ("OPENBEAST_EVAL", "OPENBEAST_TASK_PATHS")
_ON = ("auto", "on", "true", "yes", "1")
_OFF = ("off", "false", "no", "0", "none")
_LOGGED: set[str] = set()


def _log_once(key: str, msg: str) -> None:
    """stderr, never stdout: mcp_server speaks MCP over stdio."""
    if key in _LOGGED:
        return
    _LOGGED.add(key)
    try:
        print(f"[beast-lang] {msg}", file=sys.stderr, flush=True)
    except Exception:                                  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# the switch
# --------------------------------------------------------------------------

def _conf_value(key: str):
    try:
        from lang import packs as P                    # noqa: PLC0415
        return P._conf_value(key, missing=None)
    except Exception:                                  # noqa: BLE001
        return None


def setting() -> tuple[str, str]:
    """(resolved "auto"|"off", raw value). Env beats openbeast.conf."""
    raw = os.environ.get("OPENBEAST_LANG_PACK_CONTEXT")
    if raw is None:
        raw = _conf_value("LANG_PACK_CONTEXT") or ""
    tok = (raw.split() or [""])[0].split("#", 1)[0]
    tok = tok.replace('"', "").replace("'", "").lower()
    if not tok or tok in _ON:
        return "auto", raw
    if tok in _OFF:
        return "off", raw
    _log_once(f"bad:{raw}", f"LANG_PACK_CONTEXT={raw!r} is not auto|off — treating it as off")
    return "off", raw


def _eval_marker() -> str | None:
    for k in _EVAL_ENV:
        if os.environ.get(k):
            return k
    return None


def _lang_allowed(lang: str) -> bool:
    """LANG_PACKS is the operator's language allow list for beast-lang."""
    try:
        from lang import packs as P                    # noqa: PLC0415
        raw = P.allow_list_raw()
    except Exception:                                  # noqa: BLE001
        return True
    low = raw.lower()
    if low in ("off", "false", "none", "0", ""):
        return False
    if low == "auto":
        return True
    return lang in [x.strip().lower() for x in raw.split(",") if x.strip()]


# --------------------------------------------------------------------------
# the drift check — shared with evals/run_eval.py
# --------------------------------------------------------------------------

_PACK_HEADER_RE = re.compile(
    r"^\(2\) GENERATED signature digest — zig (\S+) std, (\d+) lines, sha256\(digest\)=([0-9a-f]{16})",
    re.MULTILINE)

_ZIG_VERSION: dict = {}


def _installed_zig(ttl: float = 60.0) -> str:
    now = time.monotonic()
    hit = _ZIG_VERSION.get("v")
    if hit and now - hit[0] < ttl:
        return hit[1]
    installed = ""
    if shutil.which("zig"):
        try:
            installed = subprocess.run(["zig", "version"], capture_output=True, text=True,
                                       timeout=10).stdout.strip()
        except Exception:                              # noqa: BLE001
            installed = ""
    _ZIG_VERSION["v"] = (now, installed)
    return installed


def pack_problem(lang: str, path: str, data: bytes) -> str | None:
    """Why this pack must not be served, or None. The eval harness's drift
    abort (roadmap R2) and production's refusal are this one function: the
    generated section's stamped sha must match its bytes, and the pack's zig
    version must match the installed compiler — a pack generated against
    another stdlib is a different experiment."""
    text = data.decode("utf-8", errors="replace")
    m = _PACK_HEADER_RE.search(text)
    if not m:
        return (f"pack {path}: no generated-section header — regenerate with "
                f"agents/packs/gen_zig_pack.py")
    version, _n, stamped = m.group(1), m.group(2), m.group(3)
    digest = text[m.end():].split("\n", 1)[1] if "\n" in text[m.end():] else ""
    actual = hashlib.sha256(digest.encode()).hexdigest()[:16]
    if actual != stamped:
        return (f"pack {path}: generated section drifted (stamped {stamped}, "
                f"actual {actual}) — regenerate with agents/packs/gen_zig_pack.py")
    if lang == "zig":
        installed = _installed_zig()
        if installed and installed != version:
            return (f"pack {path}: generated for zig {version} but zig {installed} is "
                    f"installed — regenerate with agents/packs/gen_zig_pack.py")
    return None


class ServedPack:
    __slots__ = ("lang", "path", "data", "sha8")

    def __init__(self, lang: str, path: str, data: bytes):
        self.lang, self.path, self.data = lang, path, data
        self.sha8 = hashlib.sha256(data).hexdigest()[:8]

    @property
    def label(self) -> str:
        return f"{self.lang}@{self.sha8}"


def served_pack(lang: str) -> tuple[ServedPack | None, str]:
    """(pack, "") when production would serve `lang`'s pack, else (None, why).
    Checks everything except the task: file present, measured bytes, drift."""
    fn = PACK_FILES.get(lang)
    if not fn:
        return None, f"no {lang} pack"
    path = os.path.join(PACKS_DIR, fn)
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        # A client checkout without agents/packs: nothing to inject, no crash.
        return None, f"{os.path.relpath(path, os.path.dirname(_AGENTS))} not present"
    problem = pack_problem(lang, path, data)
    if problem:
        return None, problem
    want = MEASURED_SHA256.get(lang)
    got = hashlib.sha256(data).hexdigest()
    if want != got:
        return None, (f"pack {path} is @{got[:8]}, but the measured pack is "
                      f"@{(want or '?')[:8]} — an edited pack is an unmeasured "
                      f"treatment; re-run the Tier-3 A/B and move MEASURED_SHA256")
    return ServedPack(lang, path, data), ""


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

def _text_langs(text: str) -> list[str]:
    try:
        from lang import packs as P                    # noqa: PLC0415
        return P.languages_in(text or "")
    except Exception:                                  # noqa: BLE001
        return ["zig"] if re.search(r"(?:\.zig|\bzig)\b", (text or "").lower()) else []


def workdir_langs(workdir: str | None, max_entries: int = SCAN_MAX_ENTRIES) -> list[str]:
    """Languages whose markers sit in `workdir` or one level below it.
    Bounded: at most `max_entries` directory entries are looked at."""
    if not workdir:
        return []
    found: set[str] = set()
    seen = 0
    queue = [(os.path.abspath(workdir), 0)]
    while queue and seen < max_entries:
        d, depth = queue.pop(0)
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                seen += 1
                if seen > max_entries:
                    break
                name = e.name
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if depth == 0 and name not in SCAN_SKIP_DIRS and not name.startswith("."):
                        queue.append((e.path, 1))
                    continue
                for lang, (names, suffixes) in WORKDIR_MARKERS.items():
                    if name in names or name.endswith(suffixes):
                        found.add(lang)
        if len(found) == len(WORKDIR_MARKERS):
            break
    return sorted(found)


def detect(task: str, workdir: str | None) -> list[str]:
    """Languages of a task: what its text names; the workdir only speaks when
    the text names no language at all — "fix the python tests" in a repo that
    happens to hold one .zig file is a python task."""
    langs = _text_langs(task)
    if langs:
        return langs
    return workdir_langs(workdir)


# --------------------------------------------------------------------------
# delivery
# --------------------------------------------------------------------------

def _combined_dir() -> str:
    d = os.environ.get("OPENBEAST_PACK_CONTEXT_DIR") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
        "openbeast", "pack-context")
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _write_combined(text: str) -> str:
    """Content-addressed 0600 file; the same text always gets the same path,
    so a console dry-run and its start plan the same argv."""
    data = text.encode()
    d = _combined_dir()
    path = os.path.join(d, f"ctx-{hashlib.sha256(data).hexdigest()[:24]}.md")
    if not os.path.exists(path):
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".ctx-")   # mkstemp: 0600
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
        _prune(d)
    return path


def _prune(d: str, max_age_s: float = 14 * 86400) -> None:
    cutoff = time.time() - max_age_s
    try:
        for e in os.scandir(d):
            if e.name.startswith("ctx-") and e.stat().st_mtime < cutoff:
                os.unlink(e.path)
    except OSError:
        pass


def production_context(task: str, workdir: str | None,
                       caller_context: str = "") -> tuple[str | None, dict]:
    """(context-file path or None, info) for a PRODUCTION agent spawn.

    info: {"setting", "langs", "packs": {lang: sha8}, "label", "reason"}.
    `label` ("zig@5cdf4b64") is what launchers record as provenance; it is ""
    when nothing was injected, and `reason` says why."""
    info = {"setting": "off", "langs": [], "packs": {}, "label": "", "reason": ""}
    try:
        mode, _raw = setting()
        info["setting"] = mode
        marker = _eval_marker()
        if marker:
            info["reason"] = f"under eval ({marker}) — run_eval --packs decides"
            return None, info
        if mode == "off":
            info["reason"] = "LANG_PACK_CONTEXT=off"
            return None, info
        langs = detect(task, workdir)
        info["langs"] = langs
        served: list[ServedPack] = []
        for lang in langs:
            if lang not in PACK_FILES:
                continue
            if not _lang_allowed(lang):
                info["reason"] = f"{lang} not in LANG_PACKS"
                continue
            p, why = served_pack(lang)
            if p is None:
                _log_once(f"why:{lang}:{why}", f"not injecting the {lang} pack: {why}")
                info["reason"] = why
                continue
            served.append(p)
        if not served:
            info["reason"] = info["reason"] or "no packed language detected"
            return None, info
        info["packs"] = {p.lang: p.sha8 for p in served}
        info["label"] = ",".join(p.label for p in served)
        info["reason"] = ""
        if len(served) == 1 and not caller_context:
            return served[0].path, info       # the measured delivery, exactly
        parts = ([caller_context.rstrip("\n")] if caller_context else [])
        parts += [p.data.decode("utf-8", errors="replace").rstrip("\n") for p in served]
        return _write_combined("\n\n".join(parts) + "\n"), info
    except Exception as e:                                 # noqa: BLE001
        _log_once(f"err:{type(e).__name__}", f"pack context unavailable ({e!r}) — no pack")
        info.update(packs={}, label="", reason=f"error: {e!r}")
        return None, info


def context_args(task: str, workdir: str | None,
                 caller_context: str = "") -> tuple[list[str], dict]:
    """The runner argv for a spawn's context: ["--context-file", p] when a
    pack is injected (the caller's context, if any, is folded into p — the
    runner's --context-file replaces --context), else ["--context", c] or []."""
    path, info = production_context(task, workdir, caller_context)
    if path:
        return ["--context-file", path], info
    return (["--context", caller_context] if caller_context else []), info


def announce(info: dict, where: str) -> None:
    """The audit line a launcher prints (stderr) when it injected a pack."""
    if info.get("label"):
        try:
            print(f"[beast-lang] {where}: agent gets the awareness pack "
                  f"{info['label']} (LANG_PACK_CONTEXT={info['setting']})",
                  file=sys.stderr, flush=True)
        except Exception:                                  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# CLI: agent.sh / client.sh rewrite the runner argv here; doctor.sh reads status
# --------------------------------------------------------------------------

def rewrite_runner_argv(argv: list[str], cwd: str | None = None) -> tuple[list[str], dict]:
    """A runner argv with the pack folded in. Options are only looked at
    before a `--`; everything after it is task text."""
    try:
        end = argv.index("--")
    except ValueError:
        end = len(argv)
    opts, rest = argv[:end], argv[end:]
    workdir, task_file, context, keep, words = None, None, "", [], []
    i = 0
    takes_value = {"--task-file", "-f", "--base-url", "--api-key", "--model", "--max-iter",
                   "--workdir", "-w", "--log-dir", "--log-file", "--context",
                   "--context-file", "--context-budget", "--resume", "--session-id",
                   "--system-prompt", "--system-prompt-file"}
    info0 = {"setting": setting()[0], "langs": [], "packs": {}, "label": "", "reason": ""}
    while i < len(opts):
        a = opts[i]
        name, eq, val = a.partition("=") if a.startswith("--") else (a, "", "")
        if name in ("--context-file", "--system-prompt", "--system-prompt-file"):
            info0["reason"] = f"caller passed {name}"
            return list(argv), info0
        if name in takes_value:
            if not eq:
                val = opts[i + 1] if i + 1 < len(opts) else ""
                i += 1
            if name == "--context":
                context = val
                i += 1
                continue                         # dropped; re-added below
            if name in ("--workdir", "-w"):
                workdir = val
            elif name in ("--task-file", "-f"):
                task_file = val
            keep += [a] if eq else [a, val]
        else:
            if not a.startswith("-"):
                words.append(a)
            keep.append(a)
        i += 1
    words += rest[1:]
    task = " ".join(words)
    if task_file:
        try:
            with open(task_file, errors="replace") as fh:
                task = fh.read(256 * 1024)
        except OSError:
            pass
    args, info = context_args(task, workdir or cwd or os.getcwd(), context)
    if not info.get("label"):
        return list(argv), info          # untouched: byte-identical to before
    return args + keep + rest, info


def status_line() -> str:
    mode, _raw = setting()
    if mode == "off":
        return "zig pack: off (LANG_PACK_CONTEXT=off)"
    if not _lang_allowed("zig"):
        return "zig pack: off (zig not in LANG_PACKS)"
    p, why = served_pack("zig")
    if p is None:
        return f"zig pack: NOT SERVED — {why}"
    rel = os.path.relpath(p.path, os.path.dirname(_AGENTS))
    return f"zig pack: auto (agents on zig tasks get {rel} @{p.sha8})"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(status_line())
        return 0
    if cmd == "argv":
        rest = argv[1:]
        if rest[:1] == ["--"]:
            rest = rest[1:]
        out, info = rewrite_runner_argv(rest)
        announce(info, "agent")
        sys.stdout.write("".join(a + "\0" for a in out))
        return 0
    print("usage: pack_context.py status | argv -- <runner args...>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    if _AGENTS not in sys.path:
        sys.path.insert(0, _AGENTS)
    sys.exit(main())
