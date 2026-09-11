#!/usr/bin/env python3
"""Generate section (2) of the zig-0.16 awareness pack (agents/packs/zig-0.16.md).

Tier 3 of docs/LANG_AWARENESS_PLAN.md (§5). The pack has two sections:

  (1) CURATED rename/idiom map — hand-written, machine-verified by the
      compile fixtures in tests/fixtures/zig016/. Kept verbatim by this
      script (everything above the section-2 header line is copied
      through untouched). NOT checksum-pinned.
  (2) GENERATED signature digest — derived from the INSTALLED zig
      stdlib (`zig env` → lib_dir): the top-N current `pub fn`
      signatures of the failure-prone areas (Io.Writer/Reader plumbing,
      File/Dir, ArrayList, ascii, fmt, mem, math), ranked by in-tree
      reference count, each rendered as "signature → one-line usage
      example". Deterministic for a given stdlib; the header stamps the
      zig version and sha256 of the digest bytes so run_eval can detect
      drift (a pack generated against a different zig is a different
      experiment).

Budget: the whole pack must stay ≤ PACK_TOKEN_BUDGET tokens under the
rough 4-chars/token estimate; section (2) is fitted to whatever the
curated section leaves. The script prints the final count.

Usage:
  python3 agents/packs/gen_zig_pack.py            # rewrite the pack in place
  python3 agents/packs/gen_zig_pack.py --check    # regenerate to memory, exit 1 on drift
  python3 agents/packs/gen_zig_pack.py --lib-dir /path/to/zig/lib

Threat model (§6.3): reads ONLY the zig stdlib tree — never evals/.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK_PATH = HERE / "zig-0.16.md"
SECTION2_HEADER_PREFIX = "(2) GENERATED signature digest"
PACK_TOKEN_BUDGET = 2000
CHARS_PER_TOKEN = 4
# Leave headroom so the 4-chars/token estimate's noise can't push a
# regenerated pack over the budget.
SAFETY_MARGIN_CHARS = 200

# Areas of the failure surface (docs/LANG_AWARENESS_PLAN.md §5 + roadmap
# R2): file → (display prefix, receiver name for method examples, nested?)
# nested=True means the functions live inside a generic `pub fn X(...) type`
# body (ArrayList's Aligned) and are indented; otherwise only top-level
# `pub fn` lines count (files that ARE a struct: Writer.zig, Reader.zig…).
AREAS = [
    # (file, display prefix, receiver, nested, weight — lines per round-robin round)
    ("Io/Writer.zig", "std.Io.Writer", "w", False, 2),
    ("Io/Reader.zig", "std.Io.Reader", "r", False, 2),
    ("Io/File.zig", "std.Io.File", "file", False, 1),
    ("Io/Dir.zig", "std.Io.Dir", "dir", False, 1),
    ("array_list.zig", "std.ArrayList(T)", "list", True, 2),
    ("ascii.zig", "std.ascii", None, False, 1),
    ("fmt.zig", "std.fmt", None, False, 1),
    ("mem.zig", "std.mem", None, False, 2),
    ("math.zig", "std.math", None, False, 1),
]

# Names that are plumbing internals or exotic variants — they crowd out the
# calls an agent actually reaches for. Applied as a substring/regex deny
# list; still fully mechanical.
DENY = re.compile(
    r"(Assert|Bounded|Preserve|Splat|Vec\b|VecAll|Leb128|Struct|Enum|Positional|"
    r"Streaming|Wsa|Posix|Sentinel|Aligned|Unaligned|^failing|^unreachable|"
    r"^unimplemented|^default|^noop|Hashed|^hashed|sendFile|Header|^rebase|"
    r"^drain|^consume|^advance|^undo|^toss|^fill|^rebase|Lower(Bound)?$|"
    r"^count(Splat|SendFile)|^writable|^unusedCapacity|^ensure|^shrink|"
    r"^expandToCapacity|^allocatedSlice|^growCapacity|^SentinelSlice|"
    r"^print(Address|Value|Vector|Array|IntAny|Ascii|AsciiChar|UnicodeCodepoint|"
    r"FloatHex|FloatHexOptions|ByteSize|Base64)|^invalidFmtError|^alignBuffer|"
    r"^take(VarInt|StructPointer|Leb128|Array)|^peek(Array|StructPointer)|"
    r"^discard|^stream|^readVec|^appendExact|^appendRemaining|^limited$|"
    r"^raise|^float(Exponent|Mantissa|Fractional|True|Min|Max|Eps)|^ldexp|^scalbn|"
    r"^frexp|^modf|^ilogb|^nextAfter|^signbit|^copysign|^isSignalNan|^negateCast|"
    r"^alignCast|^ByteAlignedInt|^rotr|^rotl|^wrap$|^Log2Int|^IntFittingRange|"
    r"^Min$|^gamma|^lgamma|^log_int|^log10_int|^complex|^big$|^readVar|^writeVar|"
    r"^readPacked|^writePacked|^byteSwap|^toNative|^nativeTo|^littleTo|^bigTo|"
    r"^alignPointer|^alignForward|^alignBackward|^isAligned|^isValidAlign|"
    r"^alignInBytes|^asBytes|^toBytes|^bytesAs|^bytesTo|^sliceAsBytes|"
    r"^absorbSentinel|^doNotOptimizeAway|^zeroes|^zeroInit|^validationWrap|"
    r"^ValidationAllocator|^copyForwards|^copyBackwards|^collapseRepeats|"
    r"^replacementSize|^window$|^WindowIterator|^ReverseIterator|^span$|"
    r"^sliceTo|^len$|^findSentinel|^boundedOrderZ|^orderZ|^containsAtLeastScalar2|"
    r"^sort(Unstable)?Context|^Alt$|^alt$|^digits2|^charToDigit|^digitToChar|"
    r"^parseIntWithGenericCharacter|^parseIntSizeSuffix|^hexToBytes|^bytesToHex|"
    r"^hex$|^count$|^lowerString|^upperString|^allocLowerString|^allocUpperString|"
    r"^hexEscape|^HexEscape|^orderIgnoreCaseZ|^boundedOrderIgnoreCaseZ|"
    r"^findIgnoreCasePos|^findIgnoreCasePosLinear|^indexOfIgnoreCasePos|"
    r"^isAscii$|^isControl$|^isGraphical$|^isPrint$|^isHex$|^isPunctuation$|"
    r"^set(Length|Permissions|Owner|Timestamps|TimestampsNow)|^enableAnsi|"
    r"^supportsAnsi|^sync$|^lock$|^unlock$|^tryLock$|^downgradeLock|^realPath|"
    r"^hardLink|^symLink|^closeMany|^isTty|^stat$|^length$|^readerStreaming|"
    r"^writerStreaming|^moveToReader|^seek|^logicalPos|^end$|^getSize|^atEnd|"
    r"^initInterface|^initDetect|^initSize|^access|^iterate|^walk|^open(Dir|File)Absolute|"
    r"^create(Dir|File)Absolute|^delete(Dir|File)Absolute|^rename(Absolute|Preserve)|"
    r"^readFileAllocOptions|^updateFile|^statFile|^createDirPath(Open|Status)|"
    r"^cwdReal|^insertSlice|^addManyAt|^addManyAsArray|^replaceRange|^orderedRemoveMany|"
    r"^toManaged|^fromOwnedSlice|^toOwnedSliceSentinel|^clone$|^resize$|^shrinkAndFree|"
    r"^clearAndFree|^addOne$|^getLastOrNull|^appendUnalignedSlice|^printBounded|"
    r"^initBuffer|^cutScalarLast|^cutLast|^findLastLinear|^findPosLinear|^findLastAny|"
    r"^findLastNone|^findNonePos|^findAnyPos|^findScalarPos|^lastIndexOf(Linear|Any|None|Scalar)|"
    r"^indexOf(PosLinear|AnyPos|NonePos|ScalarPos)|^splitBackwards|^joinZ|^concat(With|Maybe)Sentinel|"
    r"^allEqual|^findMinMax|^indexOfMinMax|^minMax$|^replaceScalar|^rotate$|^reverseIterator|"
    r"^lessThan$|^order$|^findDiff|^indexOfDiff|^approxEq|^radiansToDegrees|^degreesToRadians|"
    r"^shlExact|^shl$|^shr$|^mulWide|^divTrunc|^divFloor|^divExact|^mod$|^rem$|^negate$|"
    r"^ceilPowerOfTwo(Promote|Assert)|^lossyCast|^cast$)"
)
# Keep-list overrides for things the deny regex would wrongly drop.
KEEP = {"cast", "lossyCast", "count", "isPrint", "order", "lessThan", "Io.File.Reader.init",
        "Io.File.Writer.init"}

SIG_RE = re.compile(r"^(?P<indent>\s*)pub (?:inline )?fn (?P<name>\w+)\((?P<params>.*)\)\s*(?P<ret>[^{]*?)\s*\{\s*$")


def zig_lib_dir() -> Path:
    out = subprocess.run(["zig", "env"], capture_output=True, text=True, timeout=20).stdout
    m = re.search(r'lib_dir\s*=\s*"([^"]+)"', out)
    if not m:  # older JSON form
        m = re.search(r'"lib_dir"\s*:\s*"([^"]+)"', out)
    if not m:
        raise SystemExit("cannot find lib_dir in `zig env` output")
    return Path(m.group(1))


def zig_version() -> str:
    return subprocess.run(["zig", "version"], capture_output=True, text=True, timeout=20).stdout.strip()


def _join_signatures(text: str) -> list[tuple[str, str]]:
    """Yield (indent, one-line signature) for every `pub fn` in `text`,
    joining multi-line parameter lists and dropping doc comments."""
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if re.match(r"^\s*pub (inline )?fn \w+\(", ln):
            buf = ln
            j = i
            while "{" not in buf and j + 1 < len(lines):
                j += 1
                nxt = lines[j].strip()
                if nxt.startswith("///") or nxt.startswith("//"):
                    continue
                buf += " " + nxt
            buf = re.sub(r"\s+", " ", buf).strip()
            buf = re.sub(r",\s*\)", ")", buf)
            indent = re.match(r"^\s*", ln).group(0)
            out.append((indent, buf))
            i = j + 1
        else:
            i += 1
    return out


def parse_area(lib: Path, rel: str, nested: bool) -> list[dict]:
    text = (lib / "std" / rel).read_text(errors="replace")
    if nested:
        # ArrayList: functions inside `pub fn Aligned(...) type { return struct { ... } }`
        start = text.find("pub fn Aligned(")
        text = text[start:] if start >= 0 else text
    sigs = []
    for indent, sig in _join_signatures(text):
        m = SIG_RE.match(sig)
        if not m:
            continue
        depth = len(indent.expandtabs(4)) // 4
        if nested and depth != 2:
            continue
        if not nested and depth != 0:
            continue
        sigs.append({"name": m.group("name"), "params": m.group("params").strip(),
                     "ret": m.group("ret").strip()})
    return sigs


def reference_counts(lib: Path) -> Counter:
    """Count qualified call-shaped references across the whole std tree —
    the ranking signal (roadmap R2: 'ranked by in-tree reference count').
    Keys are `ns.name` for namespace-qualified calls (`mem.eql(`,
    `std.fmt.bufPrint(`) and `.name` for method-shaped calls
    (`.print(`), so a bare `add(` elsewhere cannot inflate `math.add`."""
    c: Counter = Counter()
    method = re.compile(r"\.([A-Za-z_]\w*)\(")
    ns_pat = re.compile(r"\b(mem|fmt|math|ascii)\.([A-Za-z_]\w*)\(")
    for p in sorted((lib / "std").rglob("*.zig")):
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        c.update("." + n for n in method.findall(text))
        c.update(f"{ns}.{n}" for ns, n in ns_pat.findall(text))
    return c


def _split_params(params: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in params:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _sample_arg(pname: str, ptype: str) -> str:
    t = ptype.replace("std.", "")
    if pname in ("gpa", "allocator"):
        return "gpa"
    if t == "Io" or pname == "io":
        return "io"
    if pname == "fmt" or (pname == "format" and "[]const u8" in t):
        return '"{d}\\n"'
    if pname == "args" and t == "anytype":
        return ".{x}"
    if t == "type":
        return "u8"
    if t in ("Limit", "Io.Limit"):
        return ".unlimited"
    if t in ("Case", "fmt.Case"):
        return ".lower"
    if t.endswith("Endian"):
        return ".little"
    if t in ("Options", "fmt.Options", "Number", "fmt.Number") or t.endswith("Options"):
        return ".{}"
    if pname in ("delimiter", "sentinel", "byte", "scalar", "c"):
        return "'\\n'" if pname in ("delimiter", "sentinel") else "'a'"
    if pname == "base":
        return "10"
    if t in ("u8",):
        return "'a'"
    if t in ("usize", "u64", "u32", "i64", "i32"):
        return "n"
    if "[]const u8" in t:
        return "s"
    if "[]u8" in t:
        return "&buf" if pname in ("buffer", "buf", "output", "out_buffer") else pname
    if t.startswith("[]") or t.startswith("[:"):
        return pname
    if t == "anytype":
        return "x"
    if t == "bool":
        return "true"
    if t.startswith("*") and "ArrayList" in t:
        return "&list"
    if t in ("File", "Io.File"):
        return "file"
    return pname


def render(area_prefix: str, receiver: str | None, sig: dict) -> str:
    params = _split_params(sig["params"])
    parsed = []
    for p in params:
        if ":" in p:
            n, t = p.split(":", 1)
            parsed.append((n.strip().removeprefix("comptime ").strip(), t.strip(), p))
        else:
            parsed.append((p, "", p))
    self_taken = False
    shown_params = []
    args = []
    if receiver and parsed:
        n0, t0, _ = parsed[0]
        struct_name = area_prefix.split(".")[-1].split("(")[0]
        if "Self" in t0 or struct_name in t0 or n0 in ("self", receiver):
            self_taken = True
    for idx, (n, t, raw) in enumerate(parsed):
        if idx == 0 and self_taken:
            continue
        shown_params.append(raw)
        args.append(_sample_arg(n, t))
    ret = re.sub(r"\s+", " ", sig["ret"]).strip()
    call_prefix = "try " if "!" in ret else ""
    if self_taken:
        sig_txt = f"{receiver}.{sig['name']}({', '.join(shown_params)}) {ret}"
        ex = f"{call_prefix}{receiver}.{sig['name']}({', '.join(args)})"
    else:
        sig_txt = f"{area_prefix}.{sig['name']}({', '.join(shown_params)}) {ret}"
        ex = f"{call_prefix}{area_prefix}.{sig['name']}({', '.join(args)})"
    if ret.startswith("?"):
        ex = f"if ({ex}) |v| _ = v"
    return f"{sig_txt} → {ex}"


LEGEND = ("legend: io = init.io (std.process.Init); w = &file_writer.interface (*std.Io.Writer); "
          "r = &file_reader.interface (*std.Io.Reader); fw/fr = std.Io.File.Writer/Reader; "
          "list: std.ArrayList(T); gpa: std.mem.Allocator; dir = std.Io.Dir.cwd(). "
          "Ranked by in-tree reference count; example args are placeholders.")


def build_digest(lib: Path, budget_chars: int) -> list[str]:
    counts = reference_counts(lib)
    per_area: list[tuple[int, list[str]]] = []
    for rel, prefix, receiver, nested, weight in AREAS:
        sigs = parse_area(lib, rel, nested)
        ns = prefix.split(".")[-1].split("(")[0] if receiver is None else None
        keep = []
        seen = set()
        for s in sigs:
            name = s["name"]
            if name in seen:
                continue
            seen.add(name)
            qualified = f"{prefix}.{name}"
            if name not in KEEP and qualified not in KEEP and DENY.search(name):
                continue
            keep.append(s)

        def rank(s, ns=ns):
            key = f"{ns}.{s['name']}" if ns else f".{s['name']}"
            return (-counts.get(key, 0), s["name"])
        keep.sort(key=rank)
        per_area.append((weight, [f"- {render(prefix, receiver, s)}" for s in keep]))
    # Weighted round-robin fill across areas until the budget is exhausted
    # — every area gets representation before any area gets depth; the
    # failure-heavy areas (Writer/Reader/ArrayList/mem) take two lines a
    # round.
    lines: list[str] = []
    used = len(LEGEND) + 1
    cursors = [0] * len(per_area)
    active = True
    while active:
        active = False
        for i, (weight, area) in enumerate(per_area):
            for _ in range(weight):
                if cursors[i] < len(area):
                    cand = area[cursors[i]]
                    cursors[i] += 1
                    if used + len(cand) + 1 <= budget_chars:
                        lines.append(cand)
                        used += len(cand) + 1
                    active = True
    return lines


def compose(curated: str, lib: Path, version: str) -> str:
    curated = curated.rstrip("\n") + "\n"
    budget = PACK_TOKEN_BUDGET * CHARS_PER_TOKEN - SAFETY_MARGIN_CHARS - len(curated) - 160
    lines = build_digest(lib, budget)
    digest = LEGEND + "\n" + "\n".join(lines) + "\n"
    sha = hashlib.sha256(digest.encode()).hexdigest()[:16]
    header = (f"{SECTION2_HEADER_PREFIX} — zig {version} std, {len(lines)} lines, "
              f"sha256(digest)={sha} (agents/packs/gen_zig_pack.py — do not edit)\n")
    return curated + header + digest


def split_curated(pack_text: str) -> str:
    for i, ln in enumerate(pack_text.splitlines(keepends=True)):
        if ln.startswith(SECTION2_HEADER_PREFIX):
            return "".join(pack_text.splitlines(keepends=True)[:i])
    raise SystemExit(f"{PACK_PATH}: no '{SECTION2_HEADER_PREFIX}' header line — cannot locate section (1)")


def parse_header(pack_text: str) -> dict:
    """Return {'version':..., 'sha':..., 'digest':...} from a committed pack."""
    lines = pack_text.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.startswith(SECTION2_HEADER_PREFIX):
            m = re.search(r"zig (\S+) std, (\d+) lines, sha256\(digest\)=([0-9a-f]{16})", ln)
            if not m:
                raise ValueError("malformed section-2 header")
            digest = "".join(lines[i + 1:])
            return {"version": m.group(1), "lines": int(m.group(2)), "sha": m.group(3),
                    "digest": digest,
                    "digest_sha": hashlib.sha256(digest.encode()).hexdigest()[:16]}
    raise ValueError("no section-2 header")


def token_estimate(text: str) -> int:
    return (len(text.encode()) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib-dir", help="zig lib dir (default: from `zig env`)")
    ap.add_argument("--check", action="store_true", help="regenerate in memory; exit 1 if the committed pack differs")
    ap.add_argument("--pack", default=str(PACK_PATH), help="pack path (default: agents/packs/zig-0.16.md)")
    args = ap.parse_args()
    lib = Path(args.lib_dir) if args.lib_dir else zig_lib_dir()
    version = zig_version()
    pack_path = Path(args.pack)
    current = pack_path.read_text()
    curated = split_curated(current)
    new = compose(curated, lib, version)
    toks = token_estimate(new)
    print(f"pack: {pack_path} — {len(new.encode())} bytes ≈ {toks} tokens "
          f"(budget {PACK_TOKEN_BUDGET}; curated ≈ {token_estimate(curated)}, "
          f"generated ≈ {toks - token_estimate(curated)}); zig {version}")
    if toks > PACK_TOKEN_BUDGET:
        print("ERROR: over budget", file=sys.stderr)
        return 2
    if args.check:
        if new != current:
            print("DRIFT: committed pack differs from a fresh regeneration", file=sys.stderr)
            return 1
        print("ok: committed pack matches regeneration")
        return 0
    pack_path.write_text(new)
    print(f"wrote {pack_path}; digest sha {parse_header(new)['sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
