"""L1 — toolchain introspection: facts extracted mechanically, not written.

docs/BEAST_LANG_PLAN.md §3 splits every synthesized line into three tiers:

  VERIFIED   a compile fixture proves it on the installed toolchain
  GENERATED  mechanically extracted from the installed toolchain, checksum
             pinned and regenerable
  CURATED    a human or a model wrote it; never auto-injected

`agents/lang/verify.py` produces the VERIFIED tier. This module produces the
GENERATED tier, and it is the layer that makes beast-lang work for a language
nobody hand-authored claims for: a fact here costs no fixture, no review and
no GPU, because the toolchain is the author.

THE RULE THAT MAKES A FACT GENERATED, not curated: it must be reproducible
from the installed toolchain alone, by a command recorded in the artifact, to
a byte-identical result. `check()` re-runs every probe and compares — a fact
that cannot be regenerated is a fact somebody edited, and it is reported as
DRIFTED rather than served.

WHAT IS DELIBERATELY NOT HERE. Cross-version deltas ("this moved between
0.15 and 0.16") are L2c, and they need a SECOND toolchain from the L0 library,
which is not what this layer sees. Asking one installed compiler what a
different release did is how you get a confident wrong answer, so this module
reports only what it can observe and `scripts/lang-introspect.sh` says so.

SECURITY, same rule as drivers.py: nothing here executes a snippet, and
nothing imports a module to inspect it. Zig's std is PARSED as text; Python's
stdlib list comes from `sys.stdlib_module_names`, which is a frozenset baked
into the interpreter, not an import walk.

Two probes are worth understanding because they carry most of the value:

  cpp/c  `-dM -E`, WITH <version> INCLUDED, dumps every macro available at a
         given -std level. The DIFF between levels is therefore a
         mechanically-derived availability map: on this gcc __cpp_lib_format
         is absent at c++17 and 202304L at c++20, so "std::format needs
         -std=c++20" is observed, not recalled. This is the AVAILABILITY axis
         of the plan, generated.
         The <version> include is load-bearing and was missing: with empty
         stdin the preprocessor reports only the compiler's own predefined
         macros, so ZERO __cpp_lib_* were observed at any level while this
         paragraph claimed one as its example.
         The PACK summarises this rather than enumerating it. 269 facts do not
         fit in 2000 characters, and every mechanical way of picking ~30 is
         arbitrary — alphabetical kept std::adaptor_iterator_pair_constructor
         and dropped std::format — while a non-arbitrary pick would be CURATED,
         which this tier may not be. So the pack states the levels, their
         counts, the standard-version mapping and where the full set is; a
         specific feature is the escalation path's question to answer.

  zig    the top-level names in lib/std/std.zig, at their exact spelling.
         `std.Io` vs `std.io` is the single most expensive trap in the eval
         suite, and a model handed the real list does not have to guess.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

from . import _proc
from . import drivers

#: Where generated artifacts live. IN the repo, unlike the L0 corpus: these are
#: small, textual, reviewable and regenerable, which is exactly what git is for.
GEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated")

#: C++/C standard levels probed, oldest first. The diff of consecutive entries
#: is what becomes an availability line.
CPP_LEVELS = ("c++11", "c++14", "c++17", "c++20", "c++23")
C_LEVELS = ("c99", "c11", "c17", "c23")
#: Editions the rust driver is asked about. Probed, not assumed: an older
#: rustc refuses a newer edition, and that refusal is the fact.
RUST_EDITIONS = ("2015", "2018", "2021", "2024")

TIMEOUT_S = drivers.TIMEOUT_S


class ProbeError(RuntimeError):
    """A probe could not observe what it promises. Never a silent empty fact."""


def _run(argv: list[str], stdin: str | None = None,
         env: dict | None = None) -> tuple[int, str]:
    # Same guarded runner as the drivers (group-kill on timeout, memory cap,
    # bounded output): a probe is a compiler invocation like any other.
    try:
        return _proc.run(argv, TIMEOUT_S, stdin=stdin, env=env)
    except FileNotFoundError as e:
        raise ProbeError(f"{argv[0]}: not installed") from e
    except subprocess.TimeoutExpired as e:
        raise ProbeError(f"{' '.join(argv)}: timed out") from e
    except OSError as e:
        raise ProbeError(f"{argv[0]}: could not be started ({e})") from e


# --------------------------------------------------------------------------
# probes
# --------------------------------------------------------------------------

#: What we feed the preprocessor per language. EMPTY STDIN WAS A BUG: with no
#: includes, `-dM -E` reports only the compiler's own predefined macros, so
#: ZERO `__cpp_lib_*` were ever observed at any level — measured 0/0/0 at
#: c++17/20/23 — while this module's docstring cited `__cpp_lib_format` as its
#: headline example and `_fmt_feature` carried a `__cpp_lib_` branch that
#: could never fire. Library feature macros live in <version>, and including
#: it yields 72 at c++17 and 138 at c++20 — and `__cpp_lib_format` really is
#: absent at c++17 and 202304L at c++20, which is the claim that was being
#: made without evidence.
_PROBE_STDIN = {
    "c++": "#include <version>\n",
    # C has no <version>; its library feature macros are per-header and its
    # real signal is __STDC_VERSION__, which is predefined. Nothing to include.
    "c": "",
}


def _macros_at(exe: str, lang_flag: str, std: str) -> dict:
    """Every macro the compiler admits at one -std level, library included."""
    rc, out = _run([exe, f"-std={std}", "-dM", "-E", "-x", lang_flag, "-"],
                   stdin=_PROBE_STDIN.get(lang_flag, ""))
    if rc != 0:
        raise ProbeError(f"{exe} -std={std} rejected: {out.strip()[:200]}")
    got = {}
    for line in out.splitlines():
        m = re.match(r"#define\s+(\w+)\s+(.*)$", line.strip())
        if m:
            got[m.group(1)] = m.group(2).strip()
    if not got:
        raise ProbeError(f"{exe} -std={std} predefined no macros at all")
    return got


def _feature_map(exe: str, lang_flag: str, levels) -> dict:
    """{level: sorted feature macros}, plus what each level ADDS."""
    per, added, changed, supported, unsupported = {}, {}, {}, [], []
    prev: set[str] = set()
    prev_vals: dict = {}
    for std in levels:
        try:
            macros = _macros_at(exe, lang_flag, std)
        except ProbeError as e:
            # An -std this compiler does not know is a FACT about the
            # compiler, recorded by absence. ANY OTHER FAILURE IS NOT: an ICE,
            # an OOM, a missing libstdc++ header or a broken wrapper would
            # have been silently filed as "this standard is unsupported", and
            # the map served afterwards would look confident and be wrong. So
            # only the recognisable "unknown -std" message is tolerated.
            msg = str(e).lower()
            if ("invalid value" in msg or "unrecognized" in msg
                    or "unknown" in msg or "not valid" in msg
                    or "is not supported" in msg):
                unsupported.append(std)
                continue
            raise ProbeError(
                f"{exe} failed at -std={std} for a reason that is NOT an "
                f"unknown standard, so this is a broken toolchain rather than "
                f"a fact about it: {e}") from e
        supported.append(std)
        feats = {k: v for k, v in macros.items() if k.startswith("__cpp_")
                 or k in ("__STDC_VERSION__", "__cplusplus")}
        per[std] = dict(sorted(feats.items()))
        now = set(feats)
        added[std] = sorted(now - prev)
        # A macro can also CHANGE VALUE between levels, and for C that is the
        # entire signal: only __STDC_VERSION__ moves (199901L -> 202311L),
        # nothing is newly defined, so an added-only map rendered C as having
        # no facts at all.
        changed[std] = sorted(
            k for k in (now & prev)
            if per[std].get(k) != prev_vals.get(k))
        prev, prev_vals = now, dict(per[std])
    if not supported:
        raise ProbeError(f"{exe}: no probed -std level was accepted")
    return {"levels_supported": supported, "levels_rejected": unsupported,
            "features": per, "added_at": added, "changed_at": changed}


def probe_cpp() -> dict:
    d = drivers.DRIVERS["cpp"]
    if not d.available():
        raise ProbeError("g++ is not installed")
    out = _feature_map(d.exe, "c++", CPP_LEVELS)
    out["commands"] = [f"{d.exe} -std=<level> -dM -E -x c++ -" ]
    # A second compiler disagreeing is itself worth recording: a claim that
    # holds on only one of them is a portability trap, not a language fact.
    other = "clang++" if shutil.which("clang++") else None
    if other:
        try:
            second = _feature_map(other, "c++", CPP_LEVELS)
            out["second_opinion"] = {
                "compiler": other,
                "levels_supported": second["levels_supported"],
                "disagreements": _disagreements(out["features"],
                                                second["features"]),
            }
            out["commands"].append(f"{other} -std=<level> -dM -E -x c++ -")
        except ProbeError:
            pass
    return out


def probe_c() -> dict:
    d = drivers.DRIVERS["c"]
    if not d.available():
        raise ProbeError("gcc is not installed")
    out = _feature_map(d.exe, "c", C_LEVELS)
    out["commands"] = [f"{d.exe} -std=<level> -dM -E -x c -"]
    return out


def _disagreements(a: dict, b: dict) -> dict:
    """Feature macros present under one compiler and not the other, per level."""
    out = {}
    for std in sorted(set(a) & set(b)):
        only_a = sorted(set(a[std]) - set(b[std]))
        only_b = sorted(set(b[std]) - set(a[std]))
        if only_a or only_b:
            out[std] = {"only_first": only_a, "only_second": only_b}
    return out


def probe_zig() -> dict:
    """The top-level std namespace, parsed out of the installed lib/std."""
    d = drivers.DRIVERS["zig"]
    if not d.available():
        raise ProbeError("zig is not installed")
    rc, out = _run(["zig", "env"])
    if rc != 0:
        raise ProbeError(f"zig env failed: {out.strip()[:200]}")
    # `zig env` is ZON, not JSON (plan §4), so match the field rather than
    # parsing a format that has no parser here.
    m = (re.search(r'\.std_dir\s*=\s*"([^"]+)"', out)
         or re.search(r'"std_dir"\s*:\s*"([^"]+)"', out))
    if not m:
        raise ProbeError("zig env reported no std_dir")
    std_dir = m.group(1)
    if not os.path.isabs(std_dir):
        lib = (re.search(r'\.lib_dir\s*=\s*"([^"]+)"', out)
               or re.search(r'"lib_dir"\s*:\s*"([^"]+)"', out))
        if not lib:
            raise ProbeError("zig env reported a relative std_dir and no lib_dir")
        std_dir = os.path.join(lib.group(1), std_dir)
    root = os.path.join(std_dir, "std.zig")
    try:
        with open(root, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError as e:
        raise ProbeError(f"cannot read {root}: {e}") from e
    names = sorted(set(re.findall(r"^pub const ([A-Za-z_]\w*)\s*=", src, re.M)))
    if not names:
        raise ProbeError(f"parsed no top-level names out of {root}")
    # Which of those names is a namespace with its own file/directory, spelled
    # the same way. That is the set a model reaches THROUGH (std.Io.File), and
    # therefore the set where an exact spelling matters most.
    modules = sorted(n for n in names
                     if os.path.exists(os.path.join(std_dir, n + ".zig"))
                     or os.path.isdir(os.path.join(std_dir, n)))
    # std_dir deliberately NOT a fact: it is an absolute path under whoever
    # installed the toolchain, so hashing it would make the artifact
    # machine-specific — every other box would read as DRIFTED, and a home
    # directory would be committed to git for nothing. The namespace list is
    # the fact; where it was read from is provenance.
    return {
        "top_level": names,
        "modules": modules,
        "commands": ["zig env", "parse <std_dir>/std.zig"],
    }


def probe_python() -> dict:
    """stdlib module names straight out of the interpreter's own frozenset."""
    exe = sys.executable or "python3"
    rc, out = _run([exe, "-c",
                    "import sys, json; print(json.dumps({"
                    "'version': '%d.%d.%d' % sys.version_info[:3],"
                    "'modules': sorted(sys.stdlib_module_names)}))"])
    if rc != 0:
        raise ProbeError(f"{exe} could not report its stdlib: {out.strip()[:200]}")
    try:
        got = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        raise ProbeError(f"unparseable stdlib report: {out.strip()[:200]}") from e
    if not got.get("modules"):
        raise ProbeError("the interpreter reported an empty stdlib")
    got["commands"] = [f"{os.path.basename(exe)} -c "
                       "'sys.stdlib_module_names'"]
    return got


def probe_go() -> dict:
    d = drivers.DRIVERS["go"]
    if not d.available():
        raise ProbeError("go is not installed")
    # Offline env, as in the driver. NOT CGO_ENABLED=0 here: that would drop
    # runtime/cgo from the list and the fact would describe a different build.
    rc, out = _run(["go", "list", "std"], env=d.env())
    if rc != 0:
        raise ProbeError(f"go list std failed: {out.strip()[:200]}")
    pkgs = sorted(ln.strip() for ln in out.splitlines() if ln.strip()
                  and not ln.startswith("go:"))
    if not pkgs:
        raise ProbeError("go list std returned nothing")
    return {"packages": pkgs, "commands": ["go list std"]}


def probe_rust() -> dict:
    """Which editions the INSTALLED rustc accepts. The refusal is the fact."""
    d = drivers.DRIVERS["rust"]
    if not d.available():
        raise ProbeError("rustc is not installed")
    accepted, rejected = [], []
    import tempfile
    with tempfile.TemporaryDirectory(prefix="beastlang-intro-") as tmp:
        src = os.path.join(tmp, "probe.rs")
        with open(src, "w") as fh:
            fh.write("fn main() {}\n")
        for ed in RUST_EDITIONS:
            rc, _ = _run(["rustc", f"--edition={ed}", "--emit=metadata",
                          "--out-dir", tmp, src])
            (accepted if rc == 0 else rejected).append(ed)
    if not accepted:
        raise ProbeError("rustc accepted no probed edition")
    return {"editions_accepted": accepted, "editions_rejected": rejected,
            "commands": ["rustc --edition=<ed> --emit=metadata"]}


PROBES = {
    "zig": probe_zig,
    "cpp": probe_cpp,
    "c": probe_c,
    "python": probe_python,
    "go": probe_go,
    "rust": probe_rust,
}


# --------------------------------------------------------------------------
# artifacts
# --------------------------------------------------------------------------

def _hash(facts: dict) -> str:
    """Content hash over the FACTS only — never over the toolchain stamp or
    the timestamp, or every re-probe would look like drift."""
    blob = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def artifact_path(lang: str) -> str:
    return os.path.join(GEN_DIR, f"{lang}.json")


def probe(lang: str) -> dict:
    """Run one language's probe and wrap it with its provenance."""
    fn = PROBES.get(lang)
    if fn is None:
        raise ProbeError(f"no probe for {lang!r} "
                         f"(have: {', '.join(sorted(PROBES))})")
    d = drivers.driver_for(lang)
    facts = fn()
    commands = facts.pop("commands", [])
    return {
        "lang": lang,
        "tier": "GENERATED",
        "toolchain": (d.version() if d else None) or "unknown",
        "commands": commands,
        "facts": facts,
        "sha256": _hash(facts),
    }


def write(lang: str) -> str:
    """Probe and persist. Returns the path written."""
    rec = probe(lang)
    os.makedirs(GEN_DIR, mode=0o755, exist_ok=True)
    path = artifact_path(lang)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


#: Probing all six languages costs ~0.2s total (measured), which is why
#: nothing here serves from a cached file: `facts()` asks the toolchain every
#: time and the on-disk artifact is a REVIEW artifact, not a source of truth.
#: That is not a micro-optimisation, it is the L1 rule — the installed
#: toolchain is ground truth — made structural: there is no stale-artifact
#: path to guard, because there is no artifact on the serving path.
#:
#: The memo below is keyed on the driver's VERSION, asked every call (one
#: `--version`, ~ms), not on the language alone: a process-lifetime cache meant
#: a long-running server kept serving the OLD compiler's facts after a
#: toolchain upgrade — the stale-artifact path this comment says cannot exist.
#: And a FAILURE is never stored: one transient ProbeError (a timeout under
#: load) used to pin None for the life of the process.
_CACHE: dict = {}


def facts(lang: str) -> dict | None:
    """The live record for `lang`, probed once per (process, toolchain
    version). None if the toolchain is absent or the probe cannot observe
    what it promises — and that None is re-asked next time, not remembered."""
    d = drivers.driver_for(lang)
    try:
        version = d.version() if d and d.available() else None
    except Exception:                                 # noqa: BLE001
        version = None
    hit = _CACHE.get(lang)
    if hit is not None and version is not None and hit[0] == version:
        return hit[1]
    try:
        rec = probe(lang)
    except ProbeError:
        _CACHE.pop(lang, None)
        return None
    if version is not None:
        _CACHE[lang] = (version, rec)
    return rec


def load(lang: str) -> dict | None:
    try:
        with open(artifact_path(lang), "r", encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def check(lang: str) -> tuple[str, str]:
    """Re-probe and compare to the stored artifact.

    ('OK'|'DRIFTED'|'STALE'|'MISSING'|'UNAVAILABLE', detail)

    STALE is not a failure: the toolchain moved, so the artifact describes a
    compiler that is no longer here and must be regenerated. DRIFTED is the
    serious one — same toolchain, different facts, which means the file was
    edited by hand and is no longer GENERATED at all.
    """
    stored = load(lang)
    if stored is None:
        return "MISSING", f"no artifact at {artifact_path(lang)}"
    try:
        fresh = probe(lang)
    except ProbeError as e:
        return "UNAVAILABLE", str(e)
    # SELF-CONSISTENCY FIRST, and it has to be recomputed from the facts, not
    # read out of the file. Comparing the stored `sha256` FIELD against the
    # fresh probe missed a hand edit entirely: append a fake namespace to
    # facts, leave the field alone, and the stale field still equalled the
    # fresh hash — the check reported OK on a file somebody had edited, which
    # is the one thing it exists to catch. Verified by doing exactly that.
    stored_facts = stored.get("facts")
    if not isinstance(stored_facts, dict):
        return "DRIFTED", "the artifact has no facts object"
    recomputed = _hash(stored_facts)
    if stored.get("sha256") != recomputed:
        return "DRIFTED", (f"{os.path.basename(artifact_path(lang))} does not "
                           f"match its own content: recorded "
                           f"{stored.get('sha256')}, contents hash to "
                           f"{recomputed} — it was edited, not generated")
    if _short(stored.get("toolchain")) != _short(fresh.get("toolchain")):
        return "STALE", (f"artifact was generated on "
                         f"{stored.get('toolchain')!r}, installed is "
                         f"{fresh.get('toolchain')!r}")
    if recomputed != fresh.get("sha256"):
        return "DRIFTED", (f"same toolchain, different facts "
                           f"({recomputed} -> {fresh.get('sha256')}) "
                           f"— the file was edited, not generated")
    return "OK", f"{fresh['sha256']} on {fresh['toolchain']}"


def _short(v) -> str:
    """Compare toolchains on their version, not their banner text.

    THE PRERELEASE PART IS PART OF THE VERSION. Dropping it made
    `0.16.0-dev.412` and `0.16.0-dev.500` compare EQUAL, so a zig dev-build
    move — the case where std changes most — was invisible to the drift guard
    and stale facts would have been served as current. Same for
    `0.14.0-dev.1` vs `0.14.0`.
    """
    if not isinstance(v, str):
        return ""
    m = re.search(r"\d+(?:\.\d+){1,3}(?:[-+][0-9A-Za-z.]+)?", v)
    return m.group(0) if m else v.strip()


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
# Not every generated artifact belongs in an always-on pack. A 362-package Go
# list is a machine-readable fact and a budget catastrophe, so `render` emits
# lines only where a budgeted rendering actually teaches a model something,
# and says plainly when it has nothing to add. The artifact is produced either
# way, because the matcher and the verifier read it programmatically.

def _fmt_feature(macro: str) -> str:
    """__cpp_lib_format -> 'std::format (__cpp_lib_format)'. Mechanical."""
    if macro.startswith("__cpp_lib_"):
        return f"std::{macro[len('__cpp_lib_'):]} ({macro})"
    if macro.startswith("__cpp_"):
        return f"{macro[len('__cpp_'):].replace('_', ' ')} ({macro})"
    return macro


#: Rendering budget in CHARACTERS, not lines. A line-count cap is not a
#: budget: one "-std=c++23 adds …" line is 460 characters, so twelve of them
#: are a pack, not a hint. ~2000 chars is roughly the 500-token slice §3 gives
#: the always-on pack per language.
BUDGET_CHARS = 2000


def render(lang: str, budget_chars: int = BUDGET_CHARS) -> list[str]:
    """Pack lines for one language's generated facts, newest axis first.

    Never truncates silently: a capped rendering ends with a line saying how
    many facts were left out and where the full set is.
    """
    rec = facts(lang)
    if rec is None:
        return []
    facts_d = rec.get("facts") or {}
    out: list[str] = []
    if lang in ("cpp", "c"):
        added = facts_d.get("added_at") or {}
        feats = facts_d.get("features") or {}
        levels = list(facts_d.get("levels_supported", []))
        rejected = list(facts_d.get("levels_rejected") or [])
        tc = _short(rec.get("toolchain")) or rec.get("toolchain")
        second = facts_d.get("second_opinion") or {}
        dis = second.get("disagreements") or {}

        # SUMMARISE, DO NOT ENUMERATE — and this replaced an enumeration that
        # was quietly useless. Including <version> (the fix for observing any
        # library macro at all) took the fact count from ~90 to 269, and a
        # 2000-character pack has room for about 30. Every way of choosing
        # those 30 that stays MECHANICAL is also arbitrary: alphabetical put
        # std::adaptor_iterator_pair_constructor in the pack and left
        # std::format, std::ranges and std::span out — the three anyone
        # actually asks about. Choosing better would mean a curated
        # importance list, which is exactly what the GENERATED tier may not
        # contain (§3: CURATED is never auto-injected).
        #
        # So the pack states what it can state completely and truly: which
        # -std levels this compiler has, how many facts each adds, the
        # standard-version mapping, how to TEST one at compile time, and where
        # the full machine-readable set is. A specific answer for a specific
        # feature is the ESCALATION path's job (§3 L3), which is triggered by
        # the compiler error that names it — not a guess made in advance.
        for lv in reversed(levels):
            names = [n for n in added.get(lv, []) if n.startswith("__cpp")]
            lib = len([n for n in names if n.startswith("__cpp_lib_")])
            lang_n = len(names) - lib
            ver = (feats.get(lv) or {}).get("__cplusplus") \
                or (feats.get(lv) or {}).get("__STDC_VERSION__")
            bits = []
            if lib:
                bits.append(f"{lib} library")
            if lang_n:
                bits.append(f"{lang_n} language")
            what = " + ".join(bits) + " feature macro(s) newly available" \
                if bits else "no newly available feature macros"
            out.append(f"-std={lv}: {what}"
                       + (f"; the level sets it to {ver}" if ver else ""))
        if rejected:
            out.append(f"this compiler REFUSES -std={', -std='.join(rejected)}")
        probe = "__cpp_lib_format" if lang == "cpp" else "__STDC_VERSION__"
        # DO NOT CITE A FILE THAT MAY NOT EXIST. agents/lang/generated/ is
        # gitignored per-rig state, so on a fresh clone there is no such file
        # — and a pack that points a model at a nonexistent path is making a
        # false claim, in a pack whose whole premise is that it only carries
        # true ones. Say where it IS when it exists, and how to produce it
        # when it does not.
        where = (f"The complete set for THIS compiler ({tc}) is in "
                 f"agents/lang/generated/{lang}.json"
                 if os.path.exists(artifact_path(lang)) else
                 f"The complete set for THIS compiler ({tc}) can be written "
                 f"out with")
        out.append(
            f"Test one at compile time rather than guessing: "
            f"`#if defined({probe})`. {where} "
            f"(./scripts/lang-introspect.sh write {lang}).")
        if dis:
            # A JUSTIFIED selection, unlike the enumeration above: these are
            # the DISAGREEMENTS between the two installed compilers, a small
            # set, and each one is a place where a claim proved on one is not
            # a language fact.
            worst = []
            for lv in reversed(levels):
                v = dis.get(lv) or {}
                for n in (v.get("only_first") or [])[:2]:
                    worst.append(f"{n} ({tc} only, at {lv})")
                for n in (v.get("only_second") or [])[:2]:
                    worst.append(f"{n} ({second.get('compiler')} only, at {lv})")
                if len(worst) >= 4:
                    break
            if worst:
                line = ("NOT portable between the two compilers installed "
                        "here: " + "; ".join(worst[:4])
                        + " — a claim proved on one is not a language fact")
                if sum(len(x) for x in out) + len(line) <= budget_chars:
                    out.append(line)
        # A budget too small for the summary must not yield a PARTIAL level
        # list with no indication — that is the silent truncation this module
        # refuses to do. Degrade to the one statement that is complete on its
        # own (where the full set lives), and to nothing if even that will not
        # fit. `packs.py` already treats an empty render as "nothing to say".
        if sum(len(x) for x in out) > budget_chars:
            pointer = [ln for ln in out if "agents/lang/generated/" in ln]
            out = pointer if pointer and len(pointer[0]) <= budget_chars else []
    elif lang == "zig":
        # top_level, NOT `modules`. The line below claims that a name absent
        # from the list does not exist — and `modules` is only the subset
        # whose backing file is spelled the same way, so rendering it made
        # the pack ASSERT that std.AutoHashMap and std.StringHashMap do not
        # exist. They do. An authoritative falsehood injected into a model's
        # context is worse than saying nothing, and it is the exact failure
        # this whole layer exists to prevent.
        names = facts_d.get("top_level") or facts_d.get("modules") or []
        if names:
            out.append("std top-level names, EXACT spelling, from the "
                       "installed lib/std: " + ", ".join(names))
            out.append("A name not in that list does not exist on this "
                       "toolchain — including lower-case variants of the ones "
                       "that are (std.Io is a namespace; std.io is not).")
    elif lang == "python":
        # Same rule as the cpp/c pointer above: generated/ is gitignored
        # per-rig state, so cite the file only when it is actually there.
        where = ("A module not in agents/lang/generated/python.json"
                 if os.path.exists(artifact_path(lang)) else
                 "A module not in `sys.stdlib_module_names` (written out by "
                 "./scripts/lang-introspect.sh write python)")
        out.append(f"stdlib of the interpreter that will run this: "
                   f"{facts_d.get('version')}, "
                   f"{len(facts_d.get('modules') or [])} modules. {where} is "
                   f"not importable here without installing it.")
    elif lang == "rust":
        acc = facts_d.get("editions_accepted") or []
        rej = facts_d.get("editions_rejected") or []
        if acc:
            out.append(f"rustc here accepts --edition {', '.join(acc)}"
                       + (f"; it REFUSES {', '.join(rej)}" if rej else ""))
    elif lang == "go":
        out.append(f"the installed Go std has "
                   f"{len(facts_d.get('packages') or [])} packages; `go doc "
                   f"<pkg>` is authoritative and works offline")
    return out
