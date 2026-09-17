"""Per-language compile drivers — the only thing allowed to CONFIRM a claim.

Design rule from docs/BEAST_LANG_PLAN.md §3: a document may PROPOSE a claim;
only the toolchain may confirm one. Every driver here answers exactly one
question — *would this compile on the toolchain actually installed on this
machine* — and answers it by running that toolchain.

SECURITY: no driver ever EXECUTES the snippet. Compile, syntax-check or
type-check only. This matters because phase 3 has a local model drafting
claims, and a verifier that runs model-written code to check it is a remote
code execution path wearing a test harness's costume. Python is the awkward
case (staleness there is a runtime property, not a syntax one) and it gets
static attribute resolution against the installed stdlib instead of a run —
see PythonDriver.

Each driver reports `available()` honestly. A language whose toolchain is
absent yields UNVERIFIABLE claims, never silently-passing ones: on this rig
that is Swift, and a Swift claim therefore stays CURATED and is never
auto-injected.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from . import _proc

# A compile must not hang the sweep. Zig's first compile of a big std import is
# the slowest thing here and still lands well inside this. Parsed with a
# fallback: an empty or mistyped value used to raise ValueError AT IMPORT.
TIMEOUT_S = _proc.env_number("OPENBEAST_LANG_COMPILE_TIMEOUT", 90.0, float, 0.01)


class Result:
    """(ok, detail) with a little structure, so callers can explain a verdict."""

    __slots__ = ("ok", "detail", "cmd", "transient")

    def __init__(self, ok: bool, detail: str = "", cmd: str = "",
                 transient: bool = False):
        self.ok, self.detail, self.cmd = ok, detail, cmd
        #: True when this is NOT the toolchain's verdict on the snippet — a
        #: timeout, a program that could not be started. "Did not compile"
        #: and "was never judged" must not look alike to verify(), where an
        #: OLD form failing is what makes a claim VERIFIED.
        self.transient = transient

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return f"Result(ok={self.ok}, detail={self.detail[:60]!r})"


def _run(argv: list[str], cwd: str | None = None, env: dict | None = None,
         stdin: str | None = None) -> Result:
    """Every process a driver starts goes through _proc.run: own process
    group, SIGKILLed as a GROUP on timeout (gcc's cc1plus used to survive it),
    address-space capped, output bounded. `env` is handed to the child and
    NEVER written to os.environ — this module serves requests on threads."""
    try:
        rc, out = _proc.run(argv, TIMEOUT_S, cwd=cwd, env=env, stdin=stdin)
    except FileNotFoundError:
        return Result(False, f"{argv[0]}: not installed", " ".join(argv))
    except subprocess.TimeoutExpired:
        return Result(False, f"timed out after {TIMEOUT_S:g}s", " ".join(argv),
                      transient=True)
    except OSError as e:
        return Result(False, f"{argv[0]}: could not be started ({e})",
                      " ".join(argv), transient=True)
    return Result(rc == 0, out.strip(), " ".join(argv))


# --------------------------------------------------------------------------
# host-file refusal
# --------------------------------------------------------------------------
# "Never EXECUTES the snippet" is not the whole security rule. A compiler will
# also READ any file the snippet names, and then quote it back in the
# diagnostic: `#include "/home/u/.env"` put a secret line into Result.detail,
# which is attached to a model's next turn. So a snippet that reaches outside
# its own temp dir is refused BEFORE the toolchain sees it.
#
# The scans are deliberately keyword-anchored rather than line-anchored. A
# line-based scan of C loses an arms race it cannot see (`??=include`,
# `%:include`, a backslash-newline inside the path, `# /**/ include`, a
# string literal holding "/*" to blind a comment stripper); looking at what
# FOLLOWS the keyword does not care how the keyword got there. When unsure,
# refuse: a refused claim is visible and explains itself, a leak is neither.

_GAP_C = r"(?:\s|/\*.*?\*/)*"                    # whitespace / block comments
_GAP_RS = r"(?:\s|/\*.*?\*/|//[^\n]*\n)*"
_GAP_ZIG = r"(?:\s|//[^\n]*\n)*"
_C_KW = r"(?:include_next|include|import|embed)"
_C_PATH = re.compile(rf"(?<![\w$]){_C_KW}\b{_GAP_C}(?:\"([^\"\n]*)\"|<([^>\n]*)>)", re.S)
_C_COMPUTED = re.compile(
    rf"(?:\#|%:){_GAP_C}{_C_KW}\b{_GAP_C}(?![\s\"<]|/\*)", re.S)
_RS_INCLUDE = re.compile(
    rf"\b(include|include_str|include_bytes){_GAP_RS}!(?!=){_GAP_RS}[(\[{{]"
    rf"(?:{_GAP_RS}\"([^\"\\\n]*)\"{_GAP_RS},?{_GAP_RS}[)\]}}])?", re.S)
_RS_ENV = re.compile(rf"\b(option_env|env){_GAP_RS}!(?!=)", re.S)
_RS_PATH_ATTR = re.compile(r"#!?\s*\[[^\]]*\bpath\s*=", re.S)
_ZIG_FILE = re.compile(
    rf"@(embedFile|import|cInclude){_GAP_ZIG}\("
    rf"(?:{_GAP_ZIG}\"([^\"\\\n]*)\"{_GAP_ZIG}\))?", re.S)


def _escapes(path: str) -> bool:
    """Absolute, home-relative, or climbing out of the temp dir."""
    return (path.startswith(("/", "~")) or os.path.isabs(path)
            or ".." in re.split(r"[\\/]", path))


def _refuse_c(source: str) -> str | None:
    # Phase 1-2 of translation happen before any directive is recognised:
    # trigraphs (live under -std=c99/c++11), then line splicing.
    src = source.replace("??=", "#").replace("??/", "\\")
    src = re.sub(r"\\[ \t]*\r?\n", "", src)
    for m in _C_PATH.finditer(src):
        path = m.group(1) if m.group(1) is not None else m.group(2)
        if _escapes(path):
            return f"it includes a host file outside the snippet ({path!r})"
    if _C_COMPUTED.search(src):
        return "it uses a computed #include/#embed, whose target cannot be checked"
    return None


def _refuse_rust(source: str) -> str | None:
    for m in _RS_INCLUDE.finditer(source):
        if m.group(2) is None or _escapes(m.group(2)):
            return (f"{m.group(1)}! must name a plain relative string literal "
                    f"— anything else can read a host file")
    m = _RS_ENV.search(source)
    if m:
        # Same leak, different source: compile_error!(env!("API_KEY")) puts a
        # host environment variable into the diagnostic.
        return f"{m.group(1)}! reads the host environment at compile time"
    if _RS_PATH_ATTR.search(source):
        return "a #[path = …] attribute makes rustc read another file"
    return None


def _refuse_zig(source: str) -> str | None:
    for m in _ZIG_FILE.finditer(source):
        if m.group(2) is None or _escapes(m.group(2)):
            return (f"@{m.group(1)} must name a plain relative string literal "
                    f"— anything else can read a host file")
    return None


class Driver:
    """One language's ground truth."""

    lang = "?"
    exe = "?"
    ext = ".txt"
    #: label for the version axis this driver understands ("edition", "std", …)
    variant_kind: str | None = None

    def available(self) -> bool:
        return shutil.which(self.exe) is not None

    def version(self) -> str | None:
        raise NotImplementedError

    def wrap(self, snippet: str) -> str:
        """Inline snippet -> a complete compilable unit. A snippet that is
        already a whole file must pass through untouched."""
        return snippet

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        raise NotImplementedError

    def refusal(self, source: str) -> str | None:
        """Why this snippet must NOT reach the toolchain, or None."""
        return None

    # -- shared plumbing ----------------------------------------------------
    def _in_tmp(self, source: str, name: str, argv_for, env: dict | None = None):
        why = self.refusal(source)
        if why:
            return Result(False, f"refused, not compiled: {why}", "refused")
        with tempfile.TemporaryDirectory(prefix="beastlang-") as d:
            path = os.path.join(d, name)
            with open(path, "w") as fh:
                fh.write(source)
            return _run(argv_for(path, d), cwd=d, env=env)


class ZigDriver(Driver):
    lang, exe, ext = "zig", "zig", ".zig"

    def version(self) -> str | None:
        r = _run(["zig", "version"])
        return r.detail.strip() if r.ok else None

    def wrap(self, snippet: str) -> str:
        if "pub fn main" in snippet or "test \"" in snippet:
            return snippet
        head = "" if "@import(\"std\")" in snippet else 'const std = @import("std");\n'
        body = "\n".join("    " + ln for ln in snippet.splitlines())
        return f"{head}pub fn main() !void {{\n{body}\n}}\n"

    def refusal(self, source: str) -> str | None:
        return _refuse_zig(source)

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        # -fno-emit-bin: we want the front end's verdict, not an executable.
        return self._in_tmp(source, "claim.zig",
                            lambda p, d: ["zig", "build-exe", "-fno-emit-bin", p])


class CDriver(Driver):
    lang, exe, ext = "c", "gcc", ".c"
    variant_kind = "std"
    DEFAULT_VARIANT = "c23"

    def version(self) -> str | None:
        r = _run(["gcc", "--version"])
        return r.detail.splitlines()[0] if r.ok and r.detail else None

    def wrap(self, snippet: str) -> str:
        if "int main" in snippet:
            return snippet
        return ("#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n"
                f"int main(void) {{\n{snippet}\n return 0;\n}}\n")

    def refusal(self, source: str) -> str | None:
        return _refuse_c(source)

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        std = variant or self.DEFAULT_VARIANT
        # -pedantic-errors is LOAD-BEARING, not tidiness. Without it gcc
        # accepts its GNU extensions at every -std level, so "this needs C23"
        # is un-falsifiable: the old form compiles under the old standard and
        # the claim reports NOT_A_BREAK. Verified while building this —
        # designated initializers compile under bare -std=c++17 and are
        # correctly rejected under -std=c++17 -pedantic-errors.
        # NOT -fno-diagnostics-show-caret, though it looks like a free belt
        # for the refusal above (the caret block echoes source lines). Tried
        # and measured: without the caret gcc REWORDS the message itself —
        # "no match for ‘operator<’ (operand types…" becomes "no match for
        # ‘operator<’ in ‘a < b’ (operand types…" — so the escalation index
        # would be built from a wording no model ever sees, since a model's
        # own compile runs with gcc's defaults. The refusal is the fix.
        return self._in_tmp(source, "claim.c",
                            lambda p, d: ["gcc", f"-std={std}", "-pedantic-errors",
                                          "-fsyntax-only", p])


class CppDriver(Driver):
    lang, exe, ext = "cpp", "g++", ".cpp"
    variant_kind = "std"
    DEFAULT_VARIANT = "c++23"

    def version(self) -> str | None:
        r = _run(["g++", "--version"])
        return r.detail.splitlines()[0] if r.ok and r.detail else None

    def wrap(self, snippet: str) -> str:
        if "int main" in snippet:
            return snippet
        return ("#include <algorithm>\n#include <cstdio>\n#include <string>\n"
                "#include <vector>\n"
                f"int main() {{\n{snippet}\n return 0;\n}}\n")

    def refusal(self, source: str) -> str | None:
        return _refuse_c(source)

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        std = variant or self.DEFAULT_VARIANT
        # See CDriver: -pedantic-errors is what makes the standard level
        # binding instead of advisory. An availability claim checked without
        # it cannot fail, and a check that cannot fail is not a check.
        # No -fno-diagnostics-show-caret either — see CDriver for why.
        return self._in_tmp(source, "claim.cpp",
                            lambda p, d: ["g++", f"-std={std}", "-pedantic-errors",
                                          "-fsyntax-only", p])

    def second_opinion(self, source: str, variant: str | None = None) -> Result:
        """C++ is the one language where two front ends routinely disagree,
        and a claim that only one of them accepts is not a claim about the
        LANGUAGE. Callers may use this to downgrade such a claim."""
        std = variant or self.DEFAULT_VARIANT
        if not shutil.which("clang++"):
            return Result(False, "clang++ not installed")
        return self._in_tmp(source, "claim.cpp",
                            lambda p, d: ["clang++", f"-std={std}", "-pedantic-errors",
                                          "-fsyntax-only", p])


class RustDriver(Driver):
    lang, exe, ext = "rust", "rustc", ".rs"
    variant_kind = "edition"
    DEFAULT_VARIANT = "2021"

    def version(self) -> str | None:
        r = _run(["rustc", "--version"])
        return r.detail.strip() if r.ok else None

    def wrap(self, snippet: str) -> str:
        if "fn main" in snippet:
            return snippet
        return f"fn main() {{\n{snippet}\n}}\n"

    def refusal(self, source: str) -> str | None:
        return _refuse_rust(source)

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        ed = variant or self.DEFAULT_VARIANT
        # --emit=metadata: type-check without linking, which is both faster and
        # incapable of producing something runnable.
        return self._in_tmp(
            source, "claim.rs",
            lambda p, d: ["rustc", f"--edition={ed}", "--emit=metadata",
                          "--crate-type=bin", "-o", os.path.join(d, "out.rmeta"), p])


class GoDriver(Driver):
    lang, exe, ext = "go", "go", ".go"

    #: What keeps `go` OFF THE NETWORK. The previous comment claimed
    #: GOFLAGS=-mod=mod did that; it does the opposite — -mod=mod ENABLES
    #: module resolution, and a snippet importing a non-std path produced a
    #: proxy lookup. -mod=readonly refuses to resolve, GOPROXY=off makes any
    #: lookup that is attempted fail locally, and GOTOOLCHAIN=local stops go
    #: from downloading a different TOOLCHAIN because a go.mod asked for one.
    OFFLINE_ENV = {"GOFLAGS": "-mod=readonly", "GOPROXY": "off",
                   "GOTOOLCHAIN": "local"}

    @classmethod
    def env(cls, **extra) -> dict:
        """A COPY of the environment for the child. os.environ is never
        written: this process serves requests on threads, and the old
        set-GOFLAGS/run/restore dance interleaved under two concurrent calls
        and left -mod=mod set for every later subprocess of any kind."""
        return dict(os.environ, **cls.OFFLINE_ENV, **extra)

    def version(self) -> str | None:
        r = _run(["go", "version"], env=self.env())
        return r.detail.strip() if r.ok else None

    def wrap(self, snippet: str) -> str:
        # Go is strict about unused imports, so a snippet that needs imports
        # must be a whole file. Only the bare-statements case is wrapped.
        if "package " in snippet:
            return snippet
        return f"package main\n\nfunc main() {{\n{snippet}\n}}\n"

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        # CGO_ENABLED=0: `import "C"` hands its comment preamble to gcc, so
        # `// #include "/etc/hostname"` came back quoted in the diagnostic
        # (reproduced). With cgo off the file is excluded instead of compiled.
        env = self.env(CGO_ENABLED="0")

        def argv(path, d):
            # A module is required for `go build`; init it quietly in the temp
            # dir — through the same guarded runner, so a hung or absent `go`
            # is a Result, not a TimeoutExpired/FileNotFoundError raised out
            # of compile_source.
            _run(["go", "mod", "init", "beastlangclaim"], cwd=d, env=env)
            return ["go", "build", "-o", os.devnull, path]
        return self._in_tmp(source, "claim.go", argv, env=env)


class PythonDriver(Driver):
    """The awkward one, and the reason this module has a security note.

    Python staleness is a RUNTIME property: `str.removeprefix` either exists
    on this interpreter or it does not, and syntax alone cannot tell you.
    The tempting fix — run the snippet — is exactly what we refuse to do,
    because phase 3 has a local model writing these.

    So: parse the snippet, walk it for dotted attribute chains rooted at an
    imported stdlib module, and resolve each chain with importlib + getattr.
    That answers "does this API exist on the installed interpreter" without
    executing one line of the claim.
    """

    lang, exe, ext = "python", sys.executable, ".py"

    def available(self) -> bool:
        return True

    def version(self) -> str | None:
        return f"Python {sys.version.split()[0]}"

    #: stdlib modules whose IMPORT is an action, not a lookup. `antigravity`
    #: opens a web browser; `this` prints a poem — and on an MCP stdio server
    #: stdout IS the JSON-RPC stream; the __hello__ pair print; idlelib and
    #: turtledemo drag in tkinter and a display. All are in
    #: sys.stdlib_module_names, so the stdlib gate alone let them through.
    SIDE_EFFECT_MODULES = frozenset({"antigravity", "this", "__hello__",
                                     "__phello__", "idlelib", "turtledemo"})

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return Result(False, f"SyntaxError: {e}", "ast.parse")
        # Everything is decided HERE, statically, in message order; the only
        # thing the child is asked is "does this name exist on your stdlib".
        #   (a) every IMPORT must resolve. Missing this is how `import imp` —
        #       a module genuinely removed in 3.12 — was reported as "still
        #       compiles": the snippet had no attribute access, so nothing was
        #       checked and the claim was wrongly demoted to NOT_A_BREAK.
        #   (b) every dotted attribute chain rooted at an import must resolve.
        jobs: list = []                  # a refusal string, or [mod, [parts]]
        for mod, attr in _imports(tree):
            jobs.append(self._refused(mod) or [mod, [attr] if attr else []])
        for mod, chain in _attr_chains(tree):
            jobs.append(self._refused(mod) or [mod, list(chain)])
        asked = [j for j in jobs if not isinstance(j, str)]
        try:
            answers = iter(self._resolve(asked) if asked else [])
        except RuntimeError as e:
            return Result(False, f"static resolution could not run: {e}",
                          "static attribute resolution", transient=True)
        missing = []
        for j in jobs:
            msg = j if isinstance(j, str) else next(answers, None)
            if msg:
                missing.append(msg)
        if missing:
            return Result(False, "; ".join(dict.fromkeys(missing[:4])),
                          "static attribute resolution")
        return Result(True, "syntax ok; every import and stdlib attribute resolves",
                      "static attribute resolution")

    @classmethod
    def _refused(cls, mod: str) -> str | None:
        """Why `mod` will NOT be imported, or None.

        Importing a module EXECUTES its top level, so this is the one place
        the no-execution rule could be smuggled past — a claim naming a
        module that happens to sit on sys.path would run it. Bounding this to
        sys.stdlib_module_names keeps it to the domain these claims are about
        and makes the rest a refusal instead of an import.
        """
        root = mod.split(".")[0]
        if root not in sys.stdlib_module_names:
            return f"{mod} is not a stdlib module (refused, not imported)"
        if root in cls.SIDE_EFFECT_MODULES:
            return f"{mod} has import side effects (refused, not imported)"
        return None

    #: Runs in the CHILD. fds 1 and 2 are pointed at /dev/null before the
    #: first import, so whatever a module prints while loading can neither
    #: reach the parent nor corrupt the one JSON line written to the saved fd.
    _RESOLVER = (
        "import importlib, json, os, sys\n"
        "jobs = json.loads(sys.stdin.read())\n"
        "out = os.fdopen(os.dup(1), 'w')\n"
        "null = os.open(os.devnull, os.O_RDWR)\n"
        "os.dup2(null, 0); os.dup2(null, 1); os.dup2(null, 2)\n"
        "res = []\n"
        "for mod, chain in jobs:\n"
        "    try:\n"
        "        cur = importlib.import_module(mod)\n"
        "    except BaseException as e:\n"
        "        res.append('import %s: %s' % (mod, e.__class__.__name__))\n"
        "        continue\n"
        "    path, msg = mod, None\n"
        "    for part in chain:\n"
        "        path += '.' + part\n"
        "        try:\n"
        "            cur = getattr(cur, part)\n"
        "        except BaseException:\n"
        "            try:\n"                       # `from xml import etree`:
        "                cur = importlib.import_module(path)\n"   # a submodule
        "            except BaseException:\n"      # is not an attribute until
        "                msg = path + ' does not exist'\n"        # imported
        "                break\n"
        "    res.append(msg)\n"
        "out.write(json.dumps(res)); out.flush()\n"
        "os._exit(0)\n"
    )

    def _resolve(self, jobs: list) -> list:
        """[message-or-None per job], answered by a SEPARATE interpreter.

        In-process importlib was the hole: an import's side effects happened
        inside the serving process, on its stdout. `-I -S` isolates the child
        from PYTHON* variables, the user site and site-packages; _proc.run
        gives it the same timeout, group-kill and memory cap as a compiler;
        BROWSER=true turns any webbrowser.open that slips through into a
        no-op. Same interpreter binary, so "exists here" still means HERE.
        """
        r = _run([sys.executable or "python3", "-I", "-S", "-c", self._RESOLVER],
                 env=dict(os.environ, BROWSER="true"), stdin=json.dumps(jobs))
        try:
            got = json.loads(r.detail) if r.ok else None
        except ValueError:
            got = None
        if not isinstance(got, list) or len(got) != len(jobs):
            raise RuntimeError(r.detail[:200] or "the resolver reported nothing")
        return got


def _imports(tree: ast.AST) -> list[tuple[str, str | None]]:
    """[(module, attr_or_None), …] for every import statement.

    `import a.b` -> ("a.b", None);  `from a import b` -> ("a", "b").
    """
    out: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append((a.name, None))
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.level:        # skip relative imports
                for a in node.names:
                    out.append((node.module, None if a.name == "*" else a.name))
    return out


def _attr_chains(tree: ast.AST) -> list[tuple[str, list[str]]]:
    """[(module, ['a','b']), …] for `import m` + `m.a.b` usages.

    Deliberately conservative: only chains rooted at a plain `import x` name
    are resolved. A local variable that shadows the name, a `from x import y`,
    or anything dynamic is skipped rather than guessed — a verifier that
    guesses is a verifier that reports false failures, and a false failure
    would demote a true claim to CURATED.
    """
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname is None and "." not in a.name:
                    imported.add(a.name)
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.For, ast.withitem)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    assigned.add(sub.id)
    out: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parts: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id in imported and cur.id not in assigned:
            out.append((cur.id, list(reversed(parts))))
    return out


DRIVERS: dict[str, Driver] = {
    d.lang: d for d in (ZigDriver(), CDriver(), CppDriver(), RustDriver(),
                        GoDriver(), PythonDriver())
}
#: Languages the plan covers but this rig cannot confirm. Claims for these are
#: UNVERIFIABLE by construction — never silently VERIFIED. (§4, §9 Q2.)
NO_TOOLCHAIN = ("swift",)


def driver_for(lang: str) -> Driver | None:
    return DRIVERS.get(lang)
