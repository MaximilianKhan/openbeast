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
import importlib
import os
import shutil
import subprocess
import sys
import tempfile

# A compile must not hang the sweep. Zig's first compile of a big std import is
# the slowest thing here and still lands well inside this.
TIMEOUT_S = float(os.environ.get("OPENBEAST_LANG_COMPILE_TIMEOUT", "90"))


class Result:
    """(ok, detail) with a little structure, so callers can explain a verdict."""

    __slots__ = ("ok", "detail", "cmd")

    def __init__(self, ok: bool, detail: str = "", cmd: str = ""):
        self.ok, self.detail, self.cmd = ok, detail, cmd

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return f"Result(ok={self.ok}, detail={self.detail[:60]!r})"


def _run(argv: list[str], cwd: str | None = None) -> Result:
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           timeout=TIMEOUT_S)
    except FileNotFoundError:
        return Result(False, f"{argv[0]}: not installed", " ".join(argv))
    except subprocess.TimeoutExpired:
        return Result(False, f"timed out after {TIMEOUT_S:.0f}s", " ".join(argv))
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return Result(p.returncode == 0, out, " ".join(argv))


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

    # -- shared plumbing ----------------------------------------------------
    def _in_tmp(self, source: str, name: str, argv_for):
        with tempfile.TemporaryDirectory(prefix="beastlang-") as d:
            path = os.path.join(d, name)
            with open(path, "w") as fh:
                fh.write(source)
            return _run(argv_for(path, d), cwd=d)


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

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        std = variant or self.DEFAULT_VARIANT
        # -pedantic-errors is LOAD-BEARING, not tidiness. Without it gcc
        # accepts its GNU extensions at every -std level, so "this needs C23"
        # is un-falsifiable: the old form compiles under the old standard and
        # the claim reports NOT_A_BREAK. Verified while building this —
        # designated initializers compile under bare -std=c++17 and are
        # correctly rejected under -std=c++17 -pedantic-errors.
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

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        std = variant or self.DEFAULT_VARIANT
        # See CDriver: -pedantic-errors is what makes the standard level
        # binding instead of advisory. An availability claim checked without
        # it cannot fail, and a check that cannot fail is not a check.
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

    def version(self) -> str | None:
        r = _run(["go", "version"])
        return r.detail.strip() if r.ok else None

    def wrap(self, snippet: str) -> str:
        # Go is strict about unused imports, so a snippet that needs imports
        # must be a whole file. Only the bare-statements case is wrapped.
        if "package " in snippet:
            return snippet
        return f"package main\n\nfunc main() {{\n{snippet}\n}}\n"

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        def argv(path, d):
            # A module is required for `go build`; init it quietly in the temp
            # dir. GOFLAGS=-mod=mod keeps it from reaching the network.
            subprocess.run(["go", "mod", "init", "beastlangclaim"], cwd=d,
                           capture_output=True, text=True, timeout=TIMEOUT_S)
            return ["go", "build", "-o", os.devnull, path]
        env_before = os.environ.get("GOFLAGS")
        os.environ["GOFLAGS"] = "-mod=mod"
        try:
            return self._in_tmp(source, "claim.go", argv)
        finally:
            if env_before is None:
                os.environ.pop("GOFLAGS", None)
            else:
                os.environ["GOFLAGS"] = env_before


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

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return Result(False, f"SyntaxError: {e}", "ast.parse")
        missing = []
        # (a) every IMPORT must resolve. Missing this is how `import imp` —
        # a module genuinely removed in 3.12 — was reported as "still
        # compiles": the snippet had no attribute access, so nothing was
        # checked and the claim was wrongly demoted to NOT_A_BREAK.
        for mod, attr in _imports(tree):
            obj = self._stdlib(mod, missing)
            if obj is None:
                continue
            if attr and not hasattr(obj, attr):
                missing.append(f"{mod}.{attr} does not exist")
        # (b) every dotted attribute chain rooted at an import must resolve.
        for mod, chain in _attr_chains(tree):
            obj = self._stdlib(mod, missing)
            if obj is None:
                continue
            cur, path = obj, mod
            for part in chain:
                path += f".{part}"
                if not hasattr(cur, part):
                    missing.append(f"{path} does not exist")
                    break
                cur = getattr(cur, part)
        if missing:
            return Result(False, "; ".join(dict.fromkeys(missing[:4])),
                          "static attribute resolution")
        return Result(True, "syntax ok; every import and stdlib attribute resolves",
                      "static attribute resolution")

    @staticmethod
    def _stdlib(mod: str, missing: list[str]):
        """Import `mod` ONLY if it is a stdlib module, else refuse.

        Importing a module EXECUTES its top level, so this is the one place
        the no-execution rule could be smuggled past — a claim naming a
        module that happens to sit on sys.path would run it. Bounding this to
        sys.stdlib_module_names keeps it to the domain these claims are about
        and makes the rest a refusal instead of an import.
        """
        root = mod.split(".")[0]
        if root not in sys.stdlib_module_names:
            missing.append(f"{mod} is not a stdlib module (refused, not imported)")
            return None
        try:
            return importlib.import_module(mod)
        except Exception as e:                          # noqa: BLE001
            missing.append(f"import {mod}: {e.__class__.__name__}")
            return None


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
