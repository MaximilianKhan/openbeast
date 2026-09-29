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

    __slots__ = ("ok", "detail", "cmd", "transient", "refused")

    def __init__(self, ok: bool, detail: str = "", cmd: str = "",
                 transient: bool = False, refused: bool = False):
        self.ok, self.detail, self.cmd = ok, detail, cmd
        #: True when the driver declined to show the snippet to the toolchain
        #: at all. Like `transient` it is NOT a verdict: a refused OLD form has
        #: not been shown to fail, and must never make a claim VERIFIED.
        self.refused = refused
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
    NEVER written to os.environ — this module serves requests on threads.
    With no `env` the child gets _proc.scrubbed_env(): a toolchain can be made
    to print its environment into the diagnostic, and the diagnostic goes to a
    model."""
    if env is None:
        env = _proc.scrubbed_env()
    try:
        rc, out = _proc.run(argv, TIMEOUT_S, cwd=cwd, env=env, stdin=stdin)
    except subprocess.TimeoutExpired:
        return Result(False, f"timed out after {TIMEOUT_S:g}s", " ".join(argv),
                      transient=True)
    except OSError as e:
        return Result(False, f"{argv[0]}: could not be started ({e})",
                      " ".join(argv), transient=True)
    if rc is None:                                   # never started
        return Result(False, out, " ".join(argv), transient=True)
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
# A refusal is NOT a verdict. It comes back as Result(refused=True), and
# verify() treats it like a compile that never ran: a refused OLD form is not
# "the old form fails", which is half of what VERIFIED means.
#
# Two failure directions, and the scans are built against both:
#   * a BYPASS — the compiler sees a directive the scan did not (`??=include`,
#     `%:include`, a backslash-newline inside the keyword, `# /**/ include`, a
#     string literal holding "/*" to blind a comment stripper, a macro
#     imported under another name);
#   * a FALSE REFUSAL — the scan sees a directive the compiler does not (the
#     text `#include "/etc/passwd"` inside a printf string or a comment),
#     which used to fail a perfectly valid snippet.
# C gets a rule that needs no lexer at all, because a lexer is exactly what
# the bypasses attack. Rust and zig get a small honest lexer, and ANY doubt in
# it (an unterminated literal, a NUL) falls back to scanning the raw text —
# the conservative direction. When unsure, refuse.

_GAP_C = r"(?:[ \t]|/\*.*?\*/)*"           # blanks / block comments, not newlines
#: The start of a preprocessor directive. After trigraphs and line splicing a
#: directive is a LINE whose first token is `#` (or the digraph `%:`), and the
#: only things that may come before it on that line are blanks and comments —
#: so either nothing, or text ending in `*/` (a comment, or the tail of one
#: that began on an earlier line). Nothing a string literal or a `//` comment
#: contains can satisfy that, which is what makes the rule lexer-free. The
#: price is the conservative direction only: a line-start `#include "/abs"`
#: inside a multi-line comment or an `#if 0` block is refused too.
_C_DIRECTIVE = rf"^(?:[^\n]*\*/)?[ \t\v\f]*(?:\#|%:){_GAP_C}"
_C_KW = r"(?:include_next|include|import|embed)\b"
_C_HEADER = r"(?:\"([^\"\n]*)\"|<([^>\n]*)>)"
_C_INCLUDE = re.compile(_C_DIRECTIVE + _C_KW + _GAP_C + _C_HEADER, re.M | re.S)
_C_COMPUTED = re.compile(
    _C_DIRECTIVE + _C_KW + _GAP_C + r"(?![ \t\"<]|/\*|\n|\Z)", re.M | re.S)
#: `#if __has_include("/home/u/.ssh/id_ed25519")` reads nothing and still
#: answers a question about the host: it is a file-existence oracle.
_C_HAS = re.compile(
    _C_DIRECTIVE + r"[^\n]*?\b__has_(?:include_next|include|embed)\b" + _GAP_C
    + r"\(" + _GAP_C + r"(?:" + _C_HEADER + r")?", re.M | re.S)
#: …and the same operator NOT followed by `(`: `#define H __has_include` then
#: `#if H("/abs")` evaluates it with an argument no scan above ever sees.
#: Except the portable feature test, `#if defined(__has_include)` /
#: `#ifdef __has_include`, which yields 0 or 1 and never takes a path.
_C_HAS_BARE = re.compile(
    r"\b__has_(?:include_next|include|embed)(?:__)?\b(?!" + _GAP_C + r"\()")
_C_DIRECTIVE_AT = re.compile(_C_DIRECTIVE, re.M | re.S)
_C_HAS_FEATURE_TEST = re.compile(
    r"(?:\bdefined" + _GAP_C + r"(?:\(" + _GAP_C + r")?"
    r"|^" + _C_DIRECTIVE + r"(?:ifdef|ifndef|elifdef|elifndef)\b" + _GAP_C + r")\Z",
    re.M | re.S)
#: Token pasting builds any name, and the fragments can hide in any number
#: of macros: `CAT(__has_,include)("/abs")` inside an #if, `GCC
#: P(depend,ency)` beside a pragma — and `C(_Pr,agma)(...)`, which builds the
#: PRAGMA OPERATOR itself, so the snippet never spells "pragma", "dependency"
#: or "#if" anywhere (reproduced on gcc 16: "fatal error: /abs: No such file"
#: vs a clean compile). Every narrower rule was one more spelling to find, so
#: `##` (and its digraph `%:%:`) on a directive line is refused outright. A
#: refusal is "not judged", never a verdict — the conservative direction.
_C_PASTE = re.compile(_C_DIRECTIVE + r"[^\n]*(?:##|%:%:)", re.M | re.S)
#: `#line N "/abs"` (and the GNU `# N "/abs"` linemarker) makes gcc quote
#: lines of THAT file in the caret block of every later diagnostic. The
#: operand must be literal: digits, then optionally a plain relative name.
_C_LINE = re.compile(_C_DIRECTIVE + r"(?:line\b|(?=\d))([^\n]*)", re.M | re.S)
_C_LINE_OK = re.compile(r"\d+(?:[ \t]+\"([^\"\n]*)\")?(?:[ \t]+\d+)*")
#: `#pragma GCC dependency "/abs"` (or through _Pragma) opens that path: a
#: fatal "No such file" when it is absent, a clean compile when it is there.
_C_DEPENDENCY = re.compile(r"\bdependency\b")
#: C++20 header units: `import "/abs/file";` (only live under -fmodules, but
#: a flag is not a reason to leave a read primitive in).
_CPP_IMPORT = re.compile(
    r"^[ \t]*(?:export[ \t]+)?import[ \t]*" + _C_HEADER, re.M)

_LIT = r"\"(?:\x00(\d+)\x00|([^\"\\\n\x00]*))\""     # lexed placeholder | raw literal
_RS_INCLUDE = re.compile(r"\b(include|include_str|include_bytes)\s*!(?!=)")
_RS_INCLUDE_ARG = re.compile(rf"\s*[(\[{{]\s*{_LIT}\s*,?\s*[)\]}}]")
_RS_ENV = re.compile(r"\b(option_env|env)\s*!(?!=)")
_RS_PATH_ATTR = re.compile(r"#!?\s*\[[^\]]*\bpath\s*=", re.S)
#: `use std::include_str as inc; inc!("/abs")` — the macro under another name
#: sails past every scan for `include_str!`. A `use` that names one of these
#: AND renames anything is refused; plain `use std::env;` (the MODULE, which
#: real code imports all the time) is not.
_RS_USE = re.compile(r"\buse\b([^;]*);", re.S)
_RS_MACRO_NAME = re.compile(r"\b(include|include_str|include_bytes|env|option_env)\b")
#: The same names as bare IDENTIFIERS, with no `!` after them. A declarative
#: macro can take a builtin's name as a fragment and invoke it in its own body
#: — `macro_rules! call { ($m:ident, $p:literal) => { compile_error!{$m!($p)} } }
#: call!(include_str, "/abs");` — so no text ever reads `include_str!`, rustc
#: still expands it, and the host file lands in the FIRST diagnostic line.
#: Scanning invocation syntax is scanning the wrong thing; the name is the
#: primitive. Only a macro the snippet DEFINES can turn a bare name back into
#: an invocation (no std macro does `$m!`), so the scan applies when one is
#: defined — `let env = 1;` in ordinary code stays valid.
#:
#: Even then only a mention INSIDE a macro's token trees is a threat (a
#: `macro_rules!` pattern or body, or the argument of any `name!(…)`): a bare
#: name in ordinary code can never reach `$m!`. Inside one, nothing about the
#: surrounding text makes it safe — a pattern matches any token shape, so
#: `(use $m:ident;)` catches `call!(use include_str;)` and `($m:ident ::
#: $x:ident)` catches `call!(env::foo)` (both reproduced: the host file and
#: $HOME in diagnostic line 1). So there every mention is refused,
#: `std::env::var` included — the conservative direction.
_RS_BARE_NAME = re.compile(
    r"\b(include|include_str|include_bytes|option_env|env)\b(?!\s*!(?!=))")
_RS_MACRO_DEF = re.compile(r"\bmacro_rules\b|\bmacro\b")
#: `#[path = "/abs"] mod x;` built by a macro: `($m:meta) => { #[$m] mod x; }`
#: with `m!(path = "/etc/hostname")`, or `#[$a = "/abs"]` with `m!(path)`.
#: The text `path =` never appears inside a `#[…]`, and rustc parses the host
#: file as a module and quotes it in the diagnostic (reproduced on 1.98).
#: Chasing how the attribute is spelled is the losing game, so both ends are
#: closed instead: (1) a `#[…]` that holds a `$` (an attribute assembled from
#: fragments), and (2) the only thing #[path] can act on — an out-of-line
#: `mod name;`. Inside a macro's tokens a `mod` is allowed only as a plain
#: inline `mod name {`; `mod x;`, `mod $n`, `$k x;` with `m!(mod)` all refuse.
#: (An inline `mod a { mod b; }` is caught by its inner `mod b;`.)
_RS_ATTR_OPEN = re.compile(r"#!?\s*\[")
_RS_MOD = re.compile(r"\bmod\b(?!\s*(?:r#)?[A-Za-z_]\w*\s*\{)")
#: `#![debugger_visualizer(natvis_file = "/abs")]` makes rustc open that path:
#: a read, and at minimum a file-existence oracle.
_RS_FILE_ATTR = re.compile(r"\bdebugger_visualizer\b")
_ZIG_FILE = re.compile(r"@(embedFile|import|cInclude)\b")
_ZIG_FILE_ARG = re.compile(rf"\s*\(\s*{_LIT}\s*\)")


def _escapes(path: str) -> bool:
    """Absolute, home-relative, or climbing out of the temp dir."""
    return (path.startswith(("/", "~")) or os.path.isabs(path)
            or ".." in re.split(r"[\\/]", path))


def _refuse_c(source: str) -> str | None:
    # Phases 1-2 of translation happen before any directive is recognised:
    # line endings, trigraphs (live under -std=c99/c++11), then line splicing.
    src = source.replace("\r\n", "\n").replace("\r", "\n")
    src = src.replace("??=", "#").replace("??/", "\\")
    src = re.sub(r"\\[ \t]*\n", "", src)
    for rx in (_C_INCLUDE, _CPP_IMPORT):
        for m in rx.finditer(src):
            path = m.group(1) if m.group(1) is not None else m.group(2)
            if _escapes(path):
                return f"it includes a host file outside the snippet ({path!r})"
    if _C_COMPUTED.search(src):
        return "it uses a computed #include/#embed, whose target cannot be checked"
    for m in _C_HAS.finditer(src):
        path = m.group(1) if m.group(1) is not None else m.group(2)
        if path is None or _escapes(path):
            return ("__has_include on a host path (or a computed one) is a "
                    "file-existence oracle")
    for m in _C_HAS_BARE.finditer(src):
        start = src.rfind("\n", 0, m.start()) + 1
        if not _C_DIRECTIVE_AT.match(src, start):
            continue                             # not on a directive line
        if _C_HAS_FEATURE_TEST.search(src[start:m.start()]):
            continue                             # defined(__has_include)
        return ("__has_include named without its argument (aliased by a "
                "macro) is a file-existence oracle no scan can check")
    if _C_PASTE.search(src):
        return ("token pasting can assemble __has_include, _Pragma or `GCC "
                "dependency` out of fragments — file-existence oracles")
    for m in _C_LINE.finditer(src):
        operand = re.sub(r"/\*.*?\*/", " ", m.group(1)).split("//", 1)[0].strip()
        ok = _C_LINE_OK.fullmatch(operand)
        if not ok or (ok.group(1) is not None and _escapes(ok.group(1))):
            return ("a #line naming a host file (or a computed one) makes the "
                    "compiler quote that file in its diagnostics")
    if "pragma" in src.lower() and _C_DEPENDENCY.search(src):
        return "#pragma GCC dependency opens the file it names"
    return None


def _lex(src: str, lang: str) -> tuple[str, list] | None:
    """(code view, string table) for rust/zig, or None when the lexing is in
    ANY doubt — the caller then scans the raw text instead.

    Comments become a blank; every string literal becomes `"\\0N\\0"`, where
    table[N] is its text if it is a PLAIN literal (no escapes, no raw/byte
    prefix) and None otherwise; char literals become a blank. So a scan of
    the view cannot be fooled by what a comment or a string SAYS, and a macro
    argument can still be read back exactly.
    """
    if "\x00" in src:
        return None
    out: list[str] = []
    table: list = []
    i, n = 0, len(src)

    def emit(text):
        table.append(text)
        out.append(f'"\x00{len(table) - 1}\x00"')

    while i < n:
        c, two = src[i], src[i:i + 2]
        if two == "//":                                  # to end of line
            j = src.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif lang == "rust" and two == "/*":             # nests in rust
            depth, i = 1, i + 2
            while depth and i < n:
                if src[i:i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                elif src[i:i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                return None
            out.append(" ")
        elif lang == "zig" and two == "\\\\":            # multiline string line
            j = src.find("\n", i)
            i = n if j < 0 else j
            emit(None)
        elif c == '"' or (lang == "rust" and c in "rbc" and not (
                i and (src[i - 1].isalnum() or src[i - 1] == "_"))
                and re.match(r"(?:br|cr|r)#*\"|[bc]\"", src[i:i + 40])):
            m = re.match(r"(?:br|cr|r)(#*)\"", src[i:]) if c != '"' else None
            if m:                                        # raw: no escapes at all
                end = src.find('"' + m.group(1), i + m.end())
                if end < 0:
                    return None
                i = end + 1 + len(m.group(1))
                emit(None)
                continue
            start = src.index('"', i) + 1
            j, plain = start, c == '"'
            while j < n and src[j] != '"':
                if src[j] == "\\":
                    plain, j = False, j + 1
                elif src[j] == "\n" and lang == "zig":   # zig strings are one line
                    return None
                j += 1
            if j >= n:
                return None
            emit(src[start:j] if plain else None)
            i = j + 1
        elif c == "'":
            # rust: 'a' / '\n' / '"' are chars, 'a (no closing quote two on)
            # is a lifetime. Getting `'"'` wrong would open a phantom string
            # and hide everything after it, which is the bypass to fear.
            if i + 1 < n and src[i + 1] == "\\":
                j = i + 2
                while j < n and src[j] not in "'\n":
                    j += 2 if src[j] == "\\" else 1
                if j >= n or src[j] != "'":
                    return None
                i = j + 1
                out.append(" ")
            elif lang == "zig":
                j = src.find("'", i + 1)
                if j < 0 or "\n" in src[i:j]:
                    return None
                i = j + 1
                out.append(" ")
            elif i + 2 < n and src[i + 2] == "'":
                i += 3
                out.append(" ")
            else:
                out.append(c)                            # a lifetime
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out), table


def _literal(rx, view: str, at: int, table) -> str | None:
    """The path named by the literal argument starting at `at`, or None when
    what follows is anything other than ONE plain string literal."""
    m = rx.match(view, at)
    if not m:
        return None
    if m.group(1) is not None:                       # a lexed placeholder
        return table[int(m.group(1))] if table is not None else None
    return m.group(2)


def _refuse_rust(source: str) -> str | None:
    lexed = _lex(source, "rust")
    view, table = lexed if lexed else (source, None)
    if lexed is None:
        # The lexing is in doubt, so a comment could be hiding between a macro
        # name and its `!`. Do not reason about it: any MENTION is refused.
        m = _RS_MACRO_NAME.search(source)
        if m:
            return (f"the snippet could not be lexed unambiguously and "
                    f"mentions `{m.group(1)}`")
    for m in _RS_INCLUDE.finditer(view):
        path = _literal(_RS_INCLUDE_ARG, view, m.end(), table)
        if path is None or _escapes(path):
            return (f"{m.group(1)}! must name a plain relative string literal "
                    f"— anything else can read a host file")
    m = _RS_ENV.search(view)
    if m:
        # Same leak, different source: compile_error!(env!("API_KEY")) puts a
        # host environment variable into the diagnostic. (The child's env is
        # scrubbed as well — this is the half that explains itself.)
        return f"{m.group(1)}! reads the host environment at compile time"
    for m in _RS_USE.finditer(view):
        named = _RS_MACRO_NAME.search(m.group(1))
        if named and re.search(r"\bas\b", m.group(1)):
            return (f"a `use` that renames things and names `{named.group(1)}` "
                    f"can smuggle that macro in under another name")
    if _RS_MACRO_DEF.search(view):
        for m in _RS_BARE_NAME.finditer(view):
            if _in_token_tree(view, m.start()):
                return (f"`{m.group(1)}` named inside a macro's tokens can "
                        f"reach the builtin through the macro, which no scan "
                        f"of `{m.group(1)}!` sees")
        for m in _RS_ATTR_OPEN.finditer(view):
            if "$" in _balanced(view, m.end() - 1):
                return ("an attribute built from macro fragments (`#[$m]`) can "
                        "be #[path = …], which makes rustc read another file")
        for m in _RS_MOD.finditer(view):
            if _in_token_tree(view, m.start()):
                return ("an out-of-line `mod` inside a macro's tokens can take "
                        "a #[path] the macro assembles, which reads another file")
    if _RS_PATH_ATTR.search(view):
        return "a #[path = …] attribute makes rustc read another file"
    if _RS_FILE_ATTR.search(view):
        return "a #[debugger_visualizer] attribute makes rustc read another file"
    return None


#: Where a macro's token trees open: `name!(`, `name![`, `name!{` (with
#: `macro_rules! name {` among them), a 2.0 `macro name` item — and an
#: ATTRIBUTE, `#[call(include_str, "/abs")]`: `macro_rules!` attribute and
#: derive arms (`attr(..) (..) => ..`) invoke a user macro from there. They
#: are feature-gated on rustc 1.98 (E0658), but a stabilisation must not
#: silently reopen the hole, so attribute brackets count already.
_RS_TT_OPEN = re.compile(
    r"\bmacro_rules\s*!\s*\w+\s*[(\[{]|\bmacro\s+\w+|#!?\s*\["
    r"|\b(?!(?:return|if|while|match|in|else|break)\b)\w+\s*!(?!=)\s*[(\[{]")


def _balanced(view: str, at: int) -> str:
    """The bracketed group opening at `at`, brackets included; to the end of
    the view when it never closes (the safe side: more text is scanned)."""
    depth = 0
    for i in range(at, len(view)):
        if view[i] in "([{":
            depth += 1
        elif view[i] in ")]}":
            depth -= 1
            if depth == 0:
                return view[at:i + 1]
    return view[at:]


def _in_token_tree(view: str, at: int) -> bool:
    """Whether offset `at` of a LEXED rust view (no comments, no string
    contents) lies inside a macro definition or a macro invocation's
    argument. Unbalanced brackets count as inside — the safe side."""
    for m in _RS_TT_OPEN.finditer(view, 0, at):
        if re.match(r"macro\s", m.group(0)):
            # decl_macro: `macro name(..) {..}` or `macro name {..}` —
            # treat everything after it as its body (nightly-only syntax).
            return True
        depth, i = 0, m.end() - 1
        while i < len(view):
            ch = view[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if i >= len(view) or at < i:
            return True
    return False


def _refuse_zig(source: str) -> str | None:
    lexed = _lex(source, "zig")
    view, table = lexed if lexed else (source, None)
    for m in _ZIG_FILE.finditer(view):
        # In doubt (no lexing) a `//` comment may sit between the builtin and
        # its `(`; the strict tail below then fails to match, which refuses.
        path = _literal(_ZIG_FILE_ARG, view, m.end(), table)
        if path is None or _escapes(path):
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
            return Result(False, f"refused, not compiled: {why}", "refused",
                          refused=True)
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

    #: A test declaration: `test "name" {`, `test {` (unnamed) or `test
    #: ident {` (a doctest). `test` is a keyword, so a line opening with it
    #: is one of these.
    _TEST_DECL = re.compile(r'^[ \t]*test\b[ \t]*(?:"|@"|\{|[A-Za-z_])', re.M)

    def _is_test_root(self, source: str) -> bool:
        return "pub fn main" not in source and bool(self._TEST_DECL.search(source))

    def wrap(self, snippet: str) -> str:
        if "pub fn main" in snippet or self._TEST_DECL.search(snippet):
            return snippet
        head = "" if "@import(\"std\")" in snippet else 'const std = @import("std");\n'
        body = "\n".join("    " + ln for ln in snippet.splitlines())
        return f"{head}pub fn main() !void {{\n{body}\n}}\n"

    def refusal(self, source: str) -> str | None:
        return _refuse_zig(source)

    def compile_source(self, source: str, variant: str | None = None) -> Result:
        # -fno-emit-bin: we want the front end's verdict, not an executable.
        # A file of `test "…"` blocks has no main, and wrap() passes it
        # through untouched — build-exe then failed EVERY such file ("has no
        # member named 'main'"), so a test-block OLD form "proved" any claim
        # VERIFIED and a correct test-block NEW form was NEW_FAILS. It is
        # analysed as the test root it is; --test-no-exec plus no binary
        # keeps the rule that nothing here is ever run.
        if self._is_test_root(source):
            cmd = ["zig", "test", "--test-no-exec", "-fno-emit-bin"]
        else:
            cmd = ["zig", "build-exe", "-fno-emit-bin"]
        return self._in_tmp(source, "claim.zig", lambda p, d: cmd + [p])


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
        return _proc.scrubbed_env({**cls.OFFLINE_ENV, **extra},
                                  names=_proc.GO_ENV_NAMES)

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
    #: `test` is CPython's own regression suite: `import test.autotest` RUNS it.
    SIDE_EFFECT_MODULES = frozenset({"antigravity", "this", "__hello__",
                                     "__phello__", "idlelib", "turtledemo",
                                     "test"})

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
            why = self._unsafe(mod, attr)
            if why:
                # Not a verdict on the snippet (see Result.refused): nothing
                # was resolved, so nothing was learned about the claim.
                return Result(False, why, "refused", refused=True)
            # `from unittest import __main__` is the same import as
            # `import unittest.__main__`, so the imported NAME is gated too.
            jobs.append(self._refused(mod, attr)
                        or [mod, [attr] if attr else []])
        for mod, chain in _attr_chains(tree):
            jobs.append(self._refused(mod) or [mod, list(chain)])
        asked = [j for j in jobs if not isinstance(j, str)]
        try:
            answers = iter(self._resolve(asked) if asked else [])
        except RuntimeError as e:
            return Result(False, f"static resolution could not run: {e}",
                          "static attribute resolution", transient=True)
        missing, absent = [], []
        for j in jobs:
            msg = j if isinstance(j, str) else next(answers, None)
            if isinstance(msg, list):
                absent.extend(msg)
            elif msg:
                missing.append(msg)
        if missing:
            return Result(False, "; ".join(dict.fromkeys(missing[:4])),
                          "static attribute resolution")
        if absent:
            # `winreg` is in sys.stdlib_module_names on Linux and cannot be
            # imported there. That is this PLATFORM's build, not the API's
            # history — reported as a failure it made `import winreg` an OLD
            # form that "fails", and any claim VERIFIED on the strength of it.
            return Result(False, f"{', '.join(dict.fromkeys(absent))}: a stdlib "
                          f"module this platform does not ship (not judged)",
                          "static attribute resolution", transient=True)
        return Result(True, "syntax ok; every import and stdlib attribute resolves",
                      "static attribute resolution")

    @classmethod
    def _refused(cls, mod: str, attr: str | None = None) -> str | None:
        """Why `mod` will NOT be imported, or None.

        Importing a module EXECUTES its top level, so this is the one place
        the no-execution rule could be smuggled past — a claim naming a
        module that happens to sit on sys.path would run it. Bounding this to
        sys.stdlib_module_names keeps it to the domain these claims are about
        and makes the rest a refusal instead of an import.
        """
        parts = mod.split(".")
        root = parts[0]
        if root not in sys.stdlib_module_names:
            return f"{mod} is not a stdlib module (refused, not imported)"
        return None

    @classmethod
    def _unsafe(cls, mod: str, attr: str | None = None) -> str | None:
        """A STDLIB module whose import is an action. Unlike `_refused` this
        says nothing about whether the name exists, so it is never a verdict."""
        parts = mod.split(".")
        if parts[0] in cls.SIDE_EFFECT_MODULES:
            return f"{mod} has import side effects (refused, not imported)"
        # The stdlib gate looks at the ROOT, and `unittest.__main__` has a
        # stdlib root. It also has no `if __name__` guard: importing it runs
        # test discovery in the cwd, i.e. executes every test_*.py there
        # (reproduced — a marker file got written). venv.__main__ and
        # tkinter.__main__ are the same. No claim is about a dunder module, so
        # none is ever imported; __future__ is the one harmless exception and
        # the one a real snippet actually starts with.
        if mod != "__future__" and any(
                p.startswith("__") for p in parts + ([attr] if attr else [])):
            return (f"{mod}{'.' + attr if attr else ''} names a dunder module "
                    f"(refused, not imported)")
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
        # stdlib-LISTED, not built here: winreg on linux, or a module whose
        # C half is missing (tkinter -> _tkinter without libtk, curses,
        # dbm.gnu, ssl…) — the failing name, not the one asked for. Unjudged
        # only if that name really will not load: `asyncio.nope` names a
        # non-listed module, and `imp` is not listed once it is removed.
        "        gone = e.name if isinstance(e, ImportError) else None\n"
        "        if gone in sys.stdlib_module_names:\n"
        "            try:\n"
        "                importlib.import_module(gone)\n"
        "            except BaseException:\n"
        "                res.append([gone]); continue\n"
        "        res.append('import %s: %s' % (mod, e.__class__.__name__))\n"
        "        continue\n"
        "    path, msg = mod, None\n"
        "    for part in chain:\n"
        "        path += '.' + part\n"
        "        try:\n"
        "            cur = getattr(cur, part)\n"
        "        except BaseException:\n"
        "            try:\n"                       # `from xml import etree`:
        "                if part.startswith('_'): raise ImportError\n"
        "                cur = importlib.import_module(path)\n"   # a submodule
        "            except BaseException:\n"      # is not an attribute until
        "                msg = path + ' does not exist'\n"        # imported;
        "                break\n"                  # never a _private/__main__
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
        # cwd is a fresh EMPTY directory: whatever an import might decide to
        # discover, glob or open relative to "here" finds nothing of the
        # host's — the belt under the dunder refusal above.
        with tempfile.TemporaryDirectory(prefix="beastlang-py-") as d:
            r = _run([sys.executable or "python3", "-I", "-S", "-c", self._RESOLVER],
                     cwd=d, env=_proc.scrubbed_env({"BROWSER": "true"}),
                     stdin=json.dumps(jobs))
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


#: Documented attributes that exist only in SOME processes: sys.ps1/ps2 in an
#: interactive session, sys.last_* after an uncaught exception there, and
#: sys.tracebacklimit only once someone sets it. The resolver's child is none
#: of those, so "does not exist" would be a statement about it, not the API.
_CONDITIONAL_ATTRS = frozenset({
    ("sys", "ps1"), ("sys", "ps2"), ("sys", "last_type"), ("sys", "last_value"),
    ("sys", "last_traceback"), ("sys", "last_exc"), ("sys", "tracebacklimit"),
})


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
    # EVERY way a name gets rebound, not just `x = …`: the old walk missed
    # parameters, so `def f(os): return os.anything` failed "os.anything does
    # not exist" — a false failure, and an OLD form that fails falsely makes
    # a claim VERIFIED. Scope-blind on purpose: skipping is the safe side.
    assigned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            assigned.add(node.id)              # =, +=, for, with, walrus, comps
        elif isinstance(node, ast.arg):
            assigned.add(node.arg)             # def/lambda parameters
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            assigned.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            assigned.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            assigned.update(node.names)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            assigned.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            assigned.add(node.rest)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:               # `import x as os`, `from y import os`
                if a.asname or isinstance(node, ast.ImportFrom):
                    assigned.add(a.asname or a.name)
    augmented = {id(n.target) for n in ast.walk(tree) if isinstance(n, ast.AugAssign)}
    out: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.ctx, ast.Store) and id(node) not in augmented:
            # `logging.X = 1` CREATES X; the chain under it (a Load node of
            # its own) is still visited and still checked. `os.X += 1` and
            # `del os.X` READ X first, so those are checked like a load.
            continue
        parts: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id in imported and cur.id not in assigned:
            chain = list(reversed(parts))
            if (cur.id, chain[0]) in _CONDITIONAL_ATTRS:
                continue                       # exists only in some sessions
            out.append((cur.id, chain))
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
