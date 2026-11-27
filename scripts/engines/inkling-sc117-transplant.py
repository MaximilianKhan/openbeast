#!/usr/bin/env python3
"""SC117's abliterated Inkling 2 Mini on Basalt: move the 20 edited attention-output tensors from SC117's Strata SQ-20
pack into Thinking Machines' official checkpoint, so Basalt's own converter can build a .basalt pack of the edit.

Offline, stdlib only (plus the `zstd` CLI to read a Strata manifest). Reads the packs and the checkpoint as DATA: nothing
from either is executed. Run it with `python3 -I`. Report: docs/reports/V5_INKLING2_MINI_SC117-2026-11-25.md;
the whole procedure, offline: scripts/engines/inkling-sc117-pack.sh.

  extract   --official-sq20 F --sc117-sq20 F --checkpoint DIR --out DIR
            Parse both .strata v1.1 files (4 KiB frame, 6-section table, ZSTD JSON manifest in section 0; the layout read
            from Strata's engine/src/artifact/strata_reader.cpp and engine/src/inkling2/ink_manifest.h at 6a6db7ff),
            check every section's SHA-256 against its table entry, require the two manifests (section 0) and every
            section but section 1 to be equal, then read the 24 `model.llm.layers.N.attn.wo_ud.weight` tensors (BF16
            [2304, 3072]) from both. Rules: the official pack's 24 tensors equal the checkpoint's bytes exactly (proves
            the decoding and that SQ-20 stores them unrounded); SC117's layers 0-3 equal the official; layers 4-23 differ.
            Then section 1 of the two packs is compared in full: every differing byte must fall inside one of those 20
            tensors. Writes wo_ud-sc117-L04-L23.bin (the 20 tensors, in layer order) and manifest.json.
  patch     --checkpoint DIR --extract DIR --out DIR
            Copy the checkpoint directory (every file, .cache excluded) to OUT and overwrite the 20 tensors' data ranges
            of model-00001-of-00001.safetensors with the extracted bytes. The safetensors header, dtypes and shapes are
            unchanged. OUT must not exist.
  verify-ck --official DIR --patched DIR [--extract DIR]
            Every file but the safetensors shard is byte-identical; in the shard the header is identical and, tensor by
            tensor, exactly the 20 `attn.wo_ud` tensors of layers 4-23 differ (and equal the extract when given).
  diff-pack --a PACK --b PACK [--expect-tensors a,b,...]
            Compare two .basalt packs: section table, each section's bytes, and inside `gguf` each tensor named in the
            meta. Prints what differs; with --expect-tensors exits 1 unless exactly those gguf tensors differ.

Exit 0 only when every rule holds.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

CHUNK = 64 << 20
SHARD = "model-00001-of-00001.safetensors"
WO = "model.llm.layers.{}.attn.wo_ud.weight"
LAYERS = range(24)
EDITED = range(4, 24)
WO_SHAPE = [2304, 3072]
WO_BYTES = 2304 * 3072 * 2


def die(msg: str) -> None:
    print(f"FAIL: {msg}", flush=True)
    sys.exit(1)


def sha256_range(path, off, n) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(off)
        while n > 0:
            b = f.read(min(CHUNK, n))
            if not b:
                raise IOError(f"{path}: short read")
            h.update(b)
            n -= len(b)
    return h.hexdigest()


def read_range(path, off, n) -> bytes:
    with open(path, "rb") as f:
        f.seek(off)
        b = f.read(n)
    if len(b) != n:
        raise IOError(f"{path}: short read at {off}")
    return b


# ---------------------------------------------------------------- .strata v1.1
STRATA_MAGIC = b"STRATA\x00\x01"
SECTION = struct.Struct("<QQ32sIIQ")   # file_offset, byte_length, sha256, section_id, flags, reserved (64 B)


def strata_open(path: str, check_sections: bool = True) -> dict:
    fr = read_range(path, 0, 4096)
    if fr[:8] != STRATA_MAGIC:
        die(f"{path}: not a .strata file")
    flags, profile, n = struct.unpack_from("<IHH", fr, 8)
    if n != 6:
        die(f"{path}: {n} sections, expected 6")
    secs = []
    for i in range(6):
        off, ln, dig, sid, fl, _ = SECTION.unpack_from(fr, 0x10 + 64 * i)
        secs.append({"id": sid, "offset": off, "bytes": ln, "sha256": dig.hex(), "flags": fl})
    if check_sections:
        for s in secs:
            if s["bytes"]:
                got = sha256_range(path, s["offset"], s["bytes"])
                if got != s["sha256"]:
                    die(f"{path}: section {s['id']} SHA-256 {got} != table {s['sha256']}")
        print(f"  {Path(path).name}: all 6 section SHA-256 values match the table", flush=True)
    raw = read_range(path, secs[0]["offset"], secs[0]["bytes"])
    p = subprocess.run(["zstd", "-d", "-c", "-q"], input=raw, capture_output=True, check=False)
    if p.returncode:
        die(f"{path}: section 0 is not a ZSTD frame zstd can read: {p.stderr[:200]!r}")
    man = json.loads(p.stdout)
    return {"path": path, "flags": flags, "profile": profile, "sections": secs, "manifest": man,
            "size": os.path.getsize(path)}


def strata_trunk(st: dict) -> dict:
    return {t["name"]: t for t in st["manifest"].get("trunk_tensors", [])}


# ---------------------------------------------------------------- safetensors
def st_header(path: str) -> tuple[int, dict]:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n > 100 << 20:
            die(f"{path}: header of {n} bytes")
        h = json.loads(f.read(n))
    h.pop("__metadata__", None)
    return 8 + n, h


def st_range(base: int, info: dict) -> tuple[int, int]:
    a, b = info["data_offsets"]
    return base + a, b - a


# ---------------------------------------------------------------- extract
def cmd_extract(a) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    print("reading the .strata files (section SHA-256 checks read each whole file)", flush=True)
    off_st, sc_st = strata_open(a.official_sq20), strata_open(a.sc117_sq20)
    if off_st["size"] != sc_st["size"]:
        die("the two SQ-20 files differ in size")
    if off_st["manifest"] != sc_st["manifest"]:
        die("the manifests (section 0) differ")
    for so, ss in zip(off_st["sections"], sc_st["sections"]):
        if (so["offset"], so["bytes"]) != (ss["offset"], ss["bytes"]):
            die(f"section {so['id']}: different place or size")
        if so["id"] != 1 and so["sha256"] != ss["sha256"]:
            die(f"section {so['id']}: SHA-256 differs (only section 1 may)")
    print("  manifests equal; sections 0, 2, 3, 4, 5 have equal SHA-256; section 1 differs as expected:",
          off_st["sections"][1]["sha256"][:16], "->", sc_st["sections"][1]["sha256"][:16], flush=True)
    s1 = off_st["sections"][1]
    trunk = strata_trunk(off_st)
    ck = Path(a.checkpoint) / SHARD
    base, hdr = st_header(str(ck))
    rows, blob_parts, ranges = [], [], []
    for l in LAYERS:
        name = WO.format(l)
        t = trunk.get(name)
        if t is None:
            die(f"{name} not in the SQ-20 trunk table")
        if t["format"] != "BF16" or list(t["shape"]) != WO_SHAPE or int(t["bytes"]) != WO_BYTES:
            die(f"{name}: SQ-20 stores {t['format']} {t['shape']} {t['bytes']} B, expected BF16 {WO_SHAPE} {WO_BYTES}")
        abs_off = s1["offset"] + int(t["offset"])
        if not (s1["offset"] <= abs_off and abs_off + WO_BYTES <= s1["offset"] + s1["bytes"]):
            die(f"{name}: outside section 1")
        info = hdr.get(name)
        if info is None or info["dtype"] != "BF16" or info["shape"] != WO_SHAPE:
            die(f"{name}: checkpoint has {info}")
        ck_off, ck_n = st_range(base, info)
        b_ck = read_range(str(ck), ck_off, ck_n)
        b_off = read_range(a.official_sq20, abs_off, WO_BYTES)
        b_sc = read_range(a.sc117_sq20, abs_off, WO_BYTES)
        if b_off != b_ck:
            die(f"{name}: the official SQ-20 bytes differ from the checkpoint's (not an unrounded copy)")
        same = b_sc == b_off
        if l < 4 and not same:
            die(f"{name}: SC117 changed layer {l}, which its README says is untouched")
        if l >= 4 and same:
            die(f"{name}: SC117's layer {l} equals the official one")
        ndiff = 0 if same else sum(1 for x, y in zip(array("H", b_sc), array("H", b_off)) if x != y)
        rows.append({"layer": l, "name": name, "strata_offset": abs_off, "checkpoint_offset": ck_off,
                     "bytes": WO_BYTES, "sha256_official": hashlib.sha256(b_off).hexdigest(),
                     "sha256_sc117": hashlib.sha256(b_sc).hexdigest(), "edited": not same,
                     "bf16_values_changed": ndiff})
        if not same:
            blob_parts.append(b_sc)
            ranges.append((abs_off, abs_off + WO_BYTES))
        print(f"  L{l:02d} {'EDITED' if not same else 'same  '} strata@{abs_off} ck@{ck_off} "
              f"official==checkpoint, values changed {ndiff}/{WO_BYTES // 2}", flush=True)
    print("comparing section 1 of the two packs in full (every differing byte must be inside an edited tensor)", flush=True)
    outside, diff_bytes = 0, 0
    with open(a.official_sq20, "rb") as fo, open(a.sc117_sq20, "rb") as fs:
        pos, end = s1["offset"], s1["offset"] + s1["bytes"]
        fo.seek(pos)
        fs.seek(pos)
        while pos < end:
            n = min(CHUNK, end - pos)
            bo, bs = fo.read(n), fs.read(n)
            if bo != bs:
                for i in range(0, n, 4096):
                    if bo[i:i + 4096] != bs[i:i + 4096]:
                        diff_bytes += 4096   # counted in 4 KiB blocks
                        p0, p1 = pos + i, pos + min(i + 4096, n)
                        if any(r0 <= p0 and p1 <= r1 for r0, r1 in ranges):
                            continue
                        for j in range(i, min(i + 4096, n)):   # a block on a tensor's edge: byte by byte
                            if bo[j] != bs[j] and not any(r0 <= pos + j < r1 for r0, r1 in ranges):
                                outside += 1
            pos += n
    if outside:
        die(f"{outside} differing bytes of section 1 are outside the 20 edited tensors")
    print(f"  section 1: {diff_bytes // 4096} differing 4 KiB blocks, every differing byte inside the 20 edited tensors", flush=True)
    blob = b"".join(blob_parts)
    (out / "wo_ud-sc117-L04-L23.bin").write_bytes(blob)
    man = {"what": "SC117's 20 edited model.llm.layers.{4..23}.attn.wo_ud.weight tensors, BF16 [2304, 3072], layer order",
           "blob": "wo_ud-sc117-L04-L23.bin", "blob_bytes": len(blob), "blob_sha256": hashlib.sha256(blob).hexdigest(),
           "official_sq20": {"path": a.official_sq20, "size": off_st["size"], "sections": off_st["sections"]},
           "sc117_sq20": {"path": a.sc117_sq20, "size": sc_st["size"], "sections": sc_st["sections"]},
           "checkpoint_shard": {"path": str(ck), "size": ck.stat().st_size},
           "section1_differing_4k_blocks": diff_bytes // 4096, "tensors": rows}
    (out / "manifest.json").write_text(json.dumps(man, indent=1))
    print(f"OK extract: {len(blob_parts)} tensors, {len(blob)} bytes, sha256 {man['blob_sha256']}", flush=True)


# ---------------------------------------------------------------- patch
def cmd_patch(a) -> None:
    src, out, ex = Path(a.checkpoint), Path(a.out), Path(a.extract)
    if out.exists():
        die(f"{out} exists")
    man = json.loads((ex / "manifest.json").read_text())
    blob = (ex / man["blob"]).read_bytes()
    if hashlib.sha256(blob).hexdigest() != man["blob_sha256"]:
        die("the extract blob's SHA-256 does not match its manifest")
    out.mkdir(parents=True)
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if rel.parts[0] == ".cache" or p.is_dir():
            continue
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, out / rel)
    shard = out / SHARD
    base, hdr = st_header(str(shard))
    edited = [r for r in man["tensors"] if r["edited"]]
    with open(shard, "r+b") as f:
        for i, r in enumerate(edited):
            off, n = st_range(base, hdr[r["name"]])
            if n != WO_BYTES or off != r["checkpoint_offset"]:
                die(f"{r['name']}: checkpoint range differs from the extract's")
            f.seek(off)
            f.write(blob[i * WO_BYTES:(i + 1) * WO_BYTES])
        f.flush()
        os.fsync(f.fileno())
    print(f"OK patch: {len(edited)} tensors written into {shard}", flush=True)


# ---------------------------------------------------------------- verify-ck
def cmd_verify_ck(a) -> None:
    o, p = Path(a.official), Path(a.patched)
    files_o = sorted(str(x.relative_to(o)) for x in o.rglob("*") if x.is_file() and ".cache" not in x.relative_to(o).parts)
    files_p = sorted(str(x.relative_to(p)) for x in p.rglob("*") if x.is_file() and ".cache" not in x.relative_to(p).parts)
    if files_o != files_p:
        die(f"file lists differ: {set(files_o) ^ set(files_p)}")
    for f in files_o:
        if f == SHARD:
            continue
        if sha256_range(o / f, 0, (o / f).stat().st_size) != sha256_range(p / f, 0, (p / f).stat().st_size):
            die(f"{f} differs")
    print(f"  {len(files_o) - 1} side files identical", flush=True)
    so, sp = o / SHARD, p / SHARD
    if so.stat().st_size != sp.stat().st_size:
        die("shard sizes differ")
    bo, ho = st_header(str(so))
    bp, hp = st_header(str(sp))
    if bo != bp or ho != hp or read_range(so, 0, bo) != read_range(sp, 0, bp):
        die("safetensors headers differ")
    want = {WO.format(l) for l in EDITED}
    blob, ex_rows = None, {}
    if a.extract:
        man = json.loads((Path(a.extract) / "manifest.json").read_text())
        blob = (Path(a.extract) / man["blob"]).read_bytes()
        ex_rows = {r["name"]: i for i, r in enumerate(r for r in man["tensors"] if r["edited"])}
    differ, covered = [], []
    for name in sorted(ho, key=lambda k: ho[k]["data_offsets"][0]):
        off, n = st_range(bo, ho[name])
        covered.append((off, off + n))
        if sha256_range(so, off, n) != sha256_range(sp, off, n):
            differ.append(name)
            if blob is not None:
                i = ex_rows[name]
                if read_range(sp, off, n) != blob[i * WO_BYTES:(i + 1) * WO_BYTES]:
                    die(f"{name}: patched bytes differ from the extract")
    # the data area must be fully covered by tensors (so "every tensor equal" means "every byte equal")
    pos = bo
    for s, e in sorted(covered):
        if s != pos:
            if read_range(so, pos, s - pos) != read_range(sp, pos, s - pos):
                die(f"bytes outside any tensor differ at {pos}")
        pos = max(pos, e)
    if pos != so.stat().st_size and read_range(so, pos, so.stat().st_size - pos) != read_range(sp, pos, sp.stat().st_size - pos):
        die("trailing bytes differ")
    print(f"  {len(ho)} tensors in the shard; differing: {len(differ)}", flush=True)
    if set(differ) != want:
        die(f"differing tensors {sorted(set(differ) ^ want)} beyond/short of the 20 wo_ud of layers 4-23")
    print("OK verify-ck: exactly model.llm.layers.{4..23}.attn.wo_ud.weight differ"
          + (" and equal the extract" if blob is not None else ""), flush=True)


# ---------------------------------------------------------------- diff-pack
HEADER = struct.Struct("<8sIIQ40s")
ENTRY = struct.Struct("<64sQQ32s16s")


def pack_table(path) -> list:
    h = read_range(path, 0, 64 << 10)
    magic, ver, n, nbytes, _ = HEADER.unpack_from(h, 0)
    if magic != b"BASALTMF":
        die(f"{path}: not a .basalt file")
    rows = []
    for i in range(n):
        name, off, size, dig, _ = ENTRY.unpack_from(h, HEADER.size + i * ENTRY.size)
        rows.append((name.rstrip(b"\0").decode(), off, size, dig.hex()))
    return rows


def cmd_diff_pack(a) -> None:
    ta, tb = pack_table(a.a), pack_table(a.b)
    print(f"  sizes {os.path.getsize(a.a)} / {os.path.getsize(a.b)}", flush=True)
    if [n for n, _, _, _ in ta] != [n for n, _, _, _ in tb]:
        die(f"section names or order differ: {[r[0] for r in ta]} / {[r[0] for r in tb]}")
    for (n, o, s, _), (_, ob, sb, _) in zip(ta, tb):
        if (o, s) != (ob, sb):
            print(f"  section {n}: offset/size {o}/{s} -> {ob}/{sb}")
            if n != "meta":
                die(f"section {n} moved or changed size")
    secs_b = {n: (o, s, d) for n, o, s, d in tb}
    differ_secs = []
    for n, o, s, d in ta:
        ob, sb, db = secs_b.get(n, (None, None, None))
        same_digest = d == db
        same_bytes = sb == s and sha256_range(a.a, o, s) == sha256_range(a.b, ob, sb)
        print(f"  section {n:<30} table digest {'equal' if same_digest else 'DIFFERS'}; bytes {'equal' if same_bytes else 'DIFFER'}",
              flush=True)
        if not same_bytes:
            differ_secs.append(n)
    meta_a = json.loads(read_range(a.a, *[(o, s) for n, o, s, _ in ta if n == "meta"][0]))
    meta_b = json.loads(read_range(a.b, *[(o, s) for n, o, s, _ in tb if n == "meta"][0]))
    if meta_a != meta_b:
        def flat(d, pre=""):
            if isinstance(d, dict):
                for k, v in d.items():
                    yield from flat(v, f"{pre}.{k}" if pre else k)
            else:
                yield pre, d
        fa, fb = dict(flat(meta_a)), dict(flat(meta_b))
        for k in sorted(set(fa) | set(fb)):
            if fa.get(k) != fb.get(k):
                print(f"  meta {k}: {str(fa.get(k))[:120]!r} -> {str(fb.get(k))[:120]!r}")
    differ_t = []
    if "gguf" in differ_secs:
        ga = [(o, s) for n, o, s, _ in ta if n == "gguf"][0]
        gb = [(o, s) for n, o, s, _ in tb if n == "gguf"][0]
        tens_a, tens_b = meta_a["tensors"], meta_b["tensors"]
        if {k: (v["offset"], v["bytes"], v["type"]) for k, v in tens_a.items()} != \
                {k: (v["offset"], v["bytes"], v["type"]) for k, v in tens_b.items()}:
            die("gguf tensor tables differ in place, size or type")
        covered = []
        for name, t in sorted(tens_a.items(), key=lambda kv: kv[1]["offset"]):
            covered.append((t["offset"], t["offset"] + t["bytes"]))
            if read_range(a.a, ga[0] + t["offset"], t["bytes"]) != read_range(a.b, gb[0] + t["offset"], t["bytes"]):
                differ_t.append(name)
        pos = 0
        for s, e in sorted(covered):   # padding between tensors
            if s > pos and read_range(a.a, ga[0] + pos, s - pos) != read_range(a.b, gb[0] + pos, s - pos):
                die(f"gguf padding differs at {pos}")
            pos = max(pos, e)
        print(f"  gguf: {len(tens_a)} tensors, {len(differ_t)} differ: {differ_t}", flush=True)
    if a.expect_tensors is not None:
        want = set(x for x in a.expect_tensors.split(",") if x)
        allowed_secs = {"gguf", "meta"} if want else {"meta"}
        if set(differ_secs) - allowed_secs:
            die(f"sections beyond gguf/meta differ: {differ_secs}")
        if set(differ_t) != want:
            die(f"differing gguf tensors {sorted(set(differ_t) ^ want)} not as expected")
        print("OK diff-pack: only the expected gguf tensors differ (plus meta/table digests as printed)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--official-sq20", required=True)
    e.add_argument("--sc117-sq20", required=True)
    e.add_argument("--checkpoint", required=True)
    e.add_argument("--out", required=True)
    p = sub.add_parser("patch")
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--extract", required=True)
    p.add_argument("--out", required=True)
    v = sub.add_parser("verify-ck")
    v.add_argument("--official", required=True)
    v.add_argument("--patched", required=True)
    v.add_argument("--extract")
    d = sub.add_parser("diff-pack")
    d.add_argument("--a", required=True)
    d.add_argument("--b", required=True)
    d.add_argument("--expect-tensors")
    a = ap.parse_args()
    {"extract": cmd_extract, "patch": cmd_patch, "verify-ck": cmd_verify_ck, "diff-pack": cmd_diff_pack}[a.cmd](a)


if __name__ == "__main__":
    main()
