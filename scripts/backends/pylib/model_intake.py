#!/usr/bin/env python3
"""Offline intake preflight: is this model folder servable on spark-hydra, and how?

    spark-model-intake.sh DIR [DIR ...] [--json] [--checksums] [--registry FILE]

Reads ONLY metadata (config.json, quantization_config.json, the safetensors
index, each shard's header, tokenizer and template files, SHA256SUMS) and the
registry of families the pair serves (data/spark-families.json). It never
reads weights unless --checksums asks it to, never writes, never touches the
network, a unit, docker or the GPU. Safe to run on a serving Spark (but see
--checksums below).

One verdict per folder, on its last line:

  DROP-IN <family>            same model_type, the family's required quantization
                              fields and the reference checkpoint's exact tensor
                              index: the family's recipe serves it as-is (for
                              DeepSeek: spark-deepseek-pack.sh use DIR).
  NEEDS A PROFILE <engine>    no family of ours serves it, but an engine we have
                              claims its model_type and format: new profile or
                              recipe work (docs/EXL3_PLAYBOOK.md, case b).
  SAME FAMILY, NOT A DROP-IN  one of our architectures, but another tensor layout
                              or quantization variant: the recipe needs work
                              (case a, the hard half).
  NOT SERVABLE HERE           no engine we have reads it, or it does not fit the
                              pair (case c, or memory).
  INCOMPLETE                  shards named by the index missing or empty, or a
                              broken symlink: fix the copy first.
  NOT A MODEL                 no config.json (a recipe repo, an overlay kit, ...).

Exit 0 when every folder is a DROP-IN that fits, 1 otherwise, 2 on bad usage.

--checksums verifies SHA256SUMS by reading every listed file once. On a SERVING
Spark that much reading fills the page cache and can push MemAvailable under the
DeepSeek recipe's memguard floor (its docs: a 5.8 GB copy once stopped a rank):
run it on the drive before the copy, on a Spark that is not serving, or accept
the risk. Pages are dropped after each file (posix_fadvise DONTNEED) to limit it.

Test seams (environment): INTAKE_REGISTRY (registry path), INTAKE_HOME (what a
leading ~ in the registry expands to).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import model_inspect as mi  # noqa: E402

REGISTRY = Path(os.environ.get("INTAKE_REGISTRY") or HERE.parent / "data" / "spark-families.json")
GIB = 1024 ** 3
VERDICTS = ("DROP-IN", "NEEDS A PROFILE", "SAME FAMILY, NOT A DROP-IN", "NOT SERVABLE HERE", "INCOMPLETE",
            "NOT A MODEL")


def expand(p: str) -> Path:
    home = os.environ.get("INTAKE_HOME")
    if home and (p == "~" or p.startswith("~/")):
        return Path(home + p[1:])
    return Path(os.path.expanduser(p))


def load_registry(path: Path) -> dict:
    try:
        reg = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise SystemExit(f"Error: registry {path}: {e}")
    if not isinstance(reg.get("families"), list) or not isinstance(reg.get("pair"), dict):
        raise SystemExit(f"Error: registry {path}: needs 'pair' and 'families'")
    return reg


# --------------------------------------------------------------------------- reading the folder

def _json(p: Path) -> dict:
    try:
        v = json.loads(p.read_text())
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def index_keys(d: Path) -> list[str] | None:
    wm = _json(d / "model.safetensors.index.json").get("weight_map")
    return sorted(wm) if isinstance(wm, dict) else None


def keys_sha256(keys: list[str]) -> str:
    return hashlib.sha256(("\n".join(keys) + "\n").encode()).hexdigest()


def pack_rev(d: Path) -> str | None:
    """spark-deepseek-pack.sh's local revision: sha1 of the model-*.safetensors 'size name' lines, by name."""
    names = sorted(p.name for p in d.glob("model-*.safetensors") if p.is_file())
    if not names:
        return None
    return hashlib.sha1("".join(f"{(d / n).stat().st_size} {n}\n" for n in names).encode()).hexdigest()


MIB = 1024 * 1024


def _ends_sha256(p: Path, size: int) -> tuple[str, str]:
    with open(p, "rb") as f:
        head = hashlib.sha256(f.read(MIB)).hexdigest()
        f.seek(max(0, size - MIB))
        tail = hashlib.sha256(f.read(MIB)).hexdigest()
    return head, tail


def pack_fingerprint(d: Path, rev: str | None = None) -> str | None:
    """spark-deepseek-pack.sh's content fingerprint: sha256 of one 'name size sha256(first MiB) sha256(last MiB)'
    line per model-*.safetensors, sorted by name. Two packs can share a size listing (so a local REV); this tells
    them apart for 2 MiB a shard. The pack tool's cache (.pack-fingerprint, 'rev=REV fingerprint=FP') is used when
    it is for `rev`; never written here (intake never writes)."""
    names = sorted(p.name for p in d.glob("model-*.safetensors") if p.is_file())
    if not names:
        return None
    if rev:
        try:
            for line in (d / ".pack-fingerprint").read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] == f"rev={rev}" and parts[1].startswith("fingerprint="):
                    fp = parts[1][len("fingerprint="):]
                    if len(fp) == 64:
                        return fp
        except OSError:
            pass
    lines = []
    for n in names:
        size = (d / n).stat().st_size
        h, t = _ends_sha256(d / n, size)
        lines.append(f"{n} {size} {h} {t}\n")
    return hashlib.sha256("".join(sorted(lines)).encode()).hexdigest()


def classify_non_model(d: Path) -> str:
    names = {p.name for p in d.iterdir()}
    if {"start.sh", "scripts"} <= names:
        return "a recipe repository (start.sh, scripts/), not a checkpoint: bring it as the recipe, not the model"
    if any(n.endswith(".safetensors") for n in names) and any(n.endswith(".py") for n in names):
        return ("an overlay or patch kit (scripts + safetensors, no config.json): it modifies a checkpoint; "
                "apply it to a copy of one, then run intake on the result")
    return "no config.json: not a transformers-style checkpoint"


def completeness(d: Path, keys: list[str] | None) -> tuple[list[str], list[str]]:
    """(blocking problems, warnings)."""
    problems, warns = [], []
    shards = sorted(p.name for p in d.glob("*.safetensors"))
    broken = [p.name for p in d.iterdir() if p.is_symlink() and not p.exists()]
    if broken:
        problems.append(f"{len(broken)} broken symlink(s) ({', '.join(broken[:3])}): an hf-download layout copied "
                        "without its blobs/; copy the resolved files (cp -L / rsync -L)")
    if keys is None:
        if len(shards) > 1:
            problems.append("several safetensors shards but no model.safetensors.index.json")
        elif not shards:
            problems.append("no .safetensors files")
    else:
        wm = _json(d / "model.safetensors.index.json")["weight_map"]
        named = sorted(set(wm.values()))
        missing = [n for n in named if not (d / n).is_file()]
        empty = [n for n in named if (d / n).is_file() and (d / n).stat().st_size == 0]
        if missing:
            problems.append(f"{len(missing)} shard(s) the index names are missing ({', '.join(missing[:3])}"
                            + ("…" if len(missing) > 3 else "") + ")")
        if empty:
            problems.append(f"{len(empty)} empty shard(s) ({', '.join(empty[:3])})")
        extra = [n for n in shards if n not in named]
        if extra:
            warns.append(f"{len(extra)} safetensors file(s) the index does not name ({', '.join(extra[:3])}): "
                         "ignored by the engines; a leftover from another pack?")
    if not any((d / n).is_file() for n in ("tokenizer.json", "tokenizer.model")):
        warns.append("no tokenizer.json / tokenizer.model: the recipe may carry its own; bring the original's")
    tok = _json(d / "tokenizer_config.json")
    if not (d / "chat_template.jinja").is_file() and not tok.get("chat_template"):
        warns.append("no chat template (chat_template.jinja or tokenizer_config.json): tools and thinking need one")
    pick = [p.name for p in d.iterdir() if p.name.endswith(mi.PICKLE_EXT)]
    if pick:
        warns.append(f"pickled files present ({', '.join(pick[:3])}): never load them; they are not needed")
    return problems, warns


def checksums(d: Path, verify: bool) -> dict:
    # SHA256SUMS, or the Hub's LFS sums spark-hy4-stage.sh writes (SHA256SUMS.hub-lfs)
    f = next((d / n for n in ("SHA256SUMS", "SHA256SUMS.hub-lfs") if (d / n).is_file()), d / "SHA256SUMS")
    if not f.is_file():
        return {"present": False, "note": "no SHA256SUMS: compare against the source's own hashes (the Hub's LFS "
                                          "sha256 per file) before trusting the copy"}
    entries = []
    for line in f.read_text(errors="replace").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and len(parts[0]) == 64:
            entries.append((parts[0].lower(), parts[1].lstrip("*")))
    shards = {p.name for p in d.glob("*.safetensors")}
    listed = {n for _, n in entries}
    out = {"present": True, "file": f.name, "entries": len(entries),
           "missing_files": sorted(n for _, n in entries if not (d / n).is_file()),
           "shards_not_listed": sorted(shards - listed), "verified": None}
    if verify:
        bad, ok = [], 0
        for h, n in entries:
            p = d / n
            if not p.is_file():
                continue
            s = hashlib.sha256()
            with open(p, "rb") as fh:
                fd = fh.fileno()
                while chunk := fh.read(16 * 1024 * 1024):
                    s.update(chunk)
                if hasattr(os, "posix_fadvise"):
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            if s.hexdigest() == h:
                ok += 1
            else:
                bad.append(n)
        out["verified"] = {"ok": ok, "mismatch": bad}
    return out


# --------------------------------------------------------------------------- matching

def quant_info(d: Path, cfg: dict) -> dict:
    q = {}
    for s in (cfg, cfg.get("text_config") or {}):
        if isinstance(s.get("quantization_config"), dict):
            q = dict(s["quantization_config"])
            break
    side = _json(d / "quantization_config.json")
    for k, v in side.items():
        if k != "tensor_storage" and not isinstance(v, (dict, list)):
            q.setdefault(k, v)
    if q.get("bits") is None and q.get("avg_bits") is not None:
        q["bits"] = q["avg_bits"]
    return q


def quant_format(q: dict, scheme: str) -> str:
    """The checkpoint's format in native TensorFold's `capabilities --json` vocabulary."""
    m = str(q.get("quant_method") or "").lower()
    if m == "exl3":
        return "exl3"
    if not m and q.get("bits"):
        return f"mlx-q{q.get('bits')}g{q.get('group_size', 64)}"
    if m == "modelopt" or "ModelOpt" in scheme:
        return "modelopt-mixed" if "mixed" in scheme else "modelopt-nvfp4" if "NVFP4" in scheme else "modelopt-fp8"
    if m == "fp8":
        blk = q.get("weight_block_size")
        return "fp8-block128" if blk == [128, 128] else "fp8-block"
    if not m:
        return "bf16"
    return m


def _norm(k, v):
    if k == "bits":
        try:
            return float(v)
        except (TypeError, ValueError):
            return v
    return v


def quant_mismatch(want: dict, q: dict) -> list[str]:
    return [f"{k} {q.get(k)!r} (needs {v!r})" for k, v in want.items() if _norm(k, q.get(k)) != _norm(k, v)]


def index_diff(keys: list[str], ref_dir: Path) -> dict | None:
    ref = index_keys(ref_dir)
    if ref is None:
        return None
    a, b = set(keys), set(ref)
    added, removed = sorted(a - b), sorted(b - a)

    def stems(xs):
        out = {}
        for x in xs:
            s = x.rsplit(".", 1)[-1]
            out[s] = out.get(s, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))
    return {"against": str(ref_dir), "added": len(added), "removed": len(removed),
            "added_suffixes": stems(added), "removed_suffixes": stems(removed),
            "examples_added": added[:4], "examples_removed": removed[:4]}


def fit(weight_bytes: int, pair: dict, tps: list[int]) -> dict:
    factor = float(pair.get("rank_weight_factor", 1.0))
    comfy, limit = float(pair["rank_weight_comfortable_gib"]), float(pair["rank_weight_limit_gib"])
    rows = {}
    for tp in (1, 2):
        per = weight_bytes / tp * (factor if tp > 1 else 1.0) / GIB
        v = "fits" if per <= comfy else "tight" if per <= limit else "does not fit"
        rows[tp] = {"per_rank_gib": round(per, 1), "verdict": v, "allowed": tp in tps}
    usable = [tp for tp in (1, 2) if rows[tp]["allowed"] and rows[tp]["verdict"] != "does not fit"]
    return {"rows": rows, "tp": usable[0] if usable else None, "comfortable_gib": comfy, "limit_gib": limit}


def engine_claims(reg: dict, mt: str, fmt: str, q: dict) -> list[dict]:
    out = []
    for e in reg.get("engines", []):
        fams = e.get("families") or {}
        if mt in (e.get("refuses") or {}):
            continue
        if mt not in fams:
            continue
        if fmt not in fams[mt]:
            out.append({"engine": e["name"], "ok": False, "family": mt,
                        "why": f"{e['title']} serves {mt} only as {', '.join(fams[mt])}; this is {fmt}"})
            continue
        variant = (e.get("exl3_variants") or {}).get(mt) if fmt == "exl3" else None
        mism = quant_mismatch(variant, q) if variant else []
        if mism:
            out.append({"engine": e["name"], "ok": False, "family": mt,
                        "why": f"{e['title']} reads {mt} EXL3 only as {variant}: this has " + "; ".join(mism)})
            continue
        ranks_map = e.get("ranks") or {}
        ranks = ranks_map.get(f"{mt}/{fmt}") or ranks_map.get(mt)
        out.append({"engine": e["name"], "ok": True, "family": mt, "title": e["title"], "launch": e.get("launch"),
                    "backend": e.get("profile_backend") or "tensorfold",
                    "ranks": ranks or [1, 2], "ranks_verified": bool(ranks),
                    "note": (e.get("rank_notes") or {}).get(mt)})
    return out


def disk_free(d: Path) -> int | None:
    try:
        st = os.statvfs(d)
        return st.f_bavail * st.f_frsize
    except OSError:
        return None


# --------------------------------------------------------------------------- one folder

def intake(d: Path, reg: dict, verify_sums: bool = False) -> dict:
    d = d.expanduser().resolve()
    r: dict = {"dir": str(d), "verdict": None, "family": None, "reasons": [], "warnings": [], "next": []}
    if not d.is_dir():
        r.update(verdict="NOT A MODEL", reasons=[f"{d}: not a directory"])
        return r
    if not (d / "config.json").is_file():
        r.update(verdict="NOT A MODEL", reasons=[classify_non_model(d)])
        return r
    cfg = _json(d / "config.json")
    keys = index_keys(d)
    problems, warns = completeness(d, keys)
    r["warnings"] += warns
    mt = str(cfg.get("model_type") or (cfg.get("text_config") or {}).get("model_type") or "")
    archs = list(cfg.get("architectures") or [])
    q = quant_info(d, cfg)
    try:
        ins = mi.inspect(mi.LocalSource(d))
    except SystemExit as e:                # model_inspect's own refusal (no usable config)
        r.update(verdict="NOT A MODEL", reasons=[str(e)])
        return r
    scheme = ins["quantization"]["scheme"]
    if q.get("quant_method") == "exl3":
        scheme = f"EXL3 {q.get('bits', '?')} bpw" + (f", {q['codebook']} codebook" if q.get("codebook") else "") \
            + (f", scope {q['scope']}" if q.get("scope") else "")
    fmt = quant_format(q, ins["quantization"]["scheme"])
    wbytes = int(ins["weights"]["weight_bytes"] or 0)
    r.update(model_type=mt, architectures=archs, quant=scheme, format=fmt, weight_bytes=wbytes,
             shards=ins["weights"]["safetensors_files"], index_keys=len(keys) if keys else None,
             index_sha256=keys_sha256(keys) if keys else None, local_rev=pack_rev(d),
             native_context=ins["context"].get("native"), moe=ins.get("moe"),
             template=ins["chat_template"].get("present"), checksums=checksums(d, verify_sums))
    r["fingerprint"] = pack_fingerprint(d, r["local_rev"]) if r["local_rev"] else None
    if fmt != "exl3":
        r["warnings"].append(f"not EXL3 ({scheme}): this playbook is about EXL3; model-inspect.sh {d} covers "
                             "every format and vLLM")
    if ins["weights"].get("rank_sliced"):
        r["warnings"].append("rank-sliced EXL3 (…rank0.trellis): a runner-specific TP copy; TensorFold reads the "
                             "plain names, so this does not load")
    cs = r["checksums"]
    if cs.get("present"):
        if cs["missing_files"]:
            r["warnings"].append(f"SHA256SUMS lists {len(cs['missing_files'])} file(s) not in the folder")
        if cs["shards_not_listed"]:
            r["warnings"].append(f"SHA256SUMS does not cover {len(cs['shards_not_listed'])} shard(s)")
        v = cs.get("verified")
        if v and v["mismatch"]:
            r["warnings"].append(f"SHA256SUMS: {len(v['mismatch'])} mismatch(es) ({', '.join(v['mismatch'][:3])}…): "
                                 "a damaged copy, or a SHA256SUMS carried over from another pack (the EAQ -v2 "
                                 "folder's is v1's)")
    if problems:
        r.update(verdict="INCOMPLETE", reasons=problems)
        return r

    pair = reg["pair"]
    fam = next((f for f in reg["families"] if mt in f.get("model_types", [])), None)
    claims = engine_claims(reg, mt, fmt, q)
    r["engines"] = claims
    if fam:
        r["family"] = fam["name"]
        if fam.get("fit_note"):
            r["warnings"].append(f"{fam['name']}: {fam['fit_note']}")
        nf = (fam.get("formats_not_servable") or {}).get(fmt)
        if nf:
            # a format of this model that no deployment of ours can serve on the pair (Hy4-flash EXL3)
            r["fit"] = fit(wbytes, pair, [1])
            r.update(verdict="NOT SERVABLE HERE", reasons=[f"{mt} as {fmt} ({scheme}): {nf}"])
            r["reasons"] += [e["refuses"][mt] for e in reg.get("engines", []) if mt in (e.get("refuses") or {})]
            return r
        why = []
        fmts = fam.get("formats")
        if fmts and fmt not in fmts:
            why.append(f"format {fmt} (the family serves {', '.join(fmts)})")
        if archs and fam.get("architectures") and archs != fam["architectures"]:
            why.append(f"architectures {archs} (the family's: {fam['architectures']})")
        why += [f"quantization {m}" for m in quant_mismatch(fam.get("quant") or {}, q)]
        if keys is None:
            why.append("no tensor index to compare")
        elif keys_sha256(keys) != fam.get("index_sha256"):
            why.append(f"tensor index differs from the reference ({len(keys)} keys; reference {fam.get('index_keys')})")
            ref = next((expand(p) for p in fam.get("reference_dirs", []) if (expand(p) / "model.safetensors.index.json")
                        .is_file()), None)
            if ref:
                r["index_diff"] = index_diff(keys, ref)
        f = fit(wbytes, pair, [fam.get("tp", 2)])
        r["fit"] = f
        if not why:
            known = (fam.get("known_packs") or {}).get(r["local_rev"] or "")
            want_fp = (fam.get("pack_fingerprints") or {}).get(r["local_rev"] or "")
            if known and want_fp and r.get("fingerprint") and r["fingerprint"] != want_fp:
                r["warnings"].append(f"same local revision {r['local_rev'][:8]} (shard names and sizes) as the known pack "
                                     f"'{known}', but OTHER content (fingerprint {r['fingerprint'][:12]}, the known "
                                     f"pack's {want_fp[:12]}): it is NOT that pack, and spark-deepseek-pack.sh refuses "
                                     "to stage it over that pack's snapshot")
            elif known:
                r["warnings"].append(f"this is a known pack: {known}")
            row = f["rows"][fam.get("tp", 2)]
            if row["verdict"] == "does not fit":
                r.update(verdict="NOT SERVABLE HERE", reasons=[
                    f"a {fam['title']} drop-in by layout, but {row['per_rank_gib']} GiB of weights per rank at "
                    f"TP{fam.get('tp', 2)} is over the {f['limit_gib']:.0f} GiB a Spark can give one rank: more "
                    "Sparks, or a smaller quant"])
                return r
            r["verdict"] = "DROP-IN"
            r["reasons"].append(f"{fam['title']}: model_type {mt}, {scheme}, the reference tensor index "
                                f"({len(keys or [])} keys)")
            if row["verdict"] == "tight":
                r["warnings"].append(f"{row['per_rank_gib']} GiB per rank is tight (over {f['comfortable_gib']:.0f}): "
                                     "the head keeps only a few GiB beside the command center")
            r["next"].append(fam["swap"].replace("{dir}", str(d)))
            need = row["per_rank_gib"] * GIB
            free = disk_free(d)
            if fam.get("rank_files"):
                r["disk"] = {"head_extra_bytes": int(2 * need), "worker_extra_bytes": int(need + wbytes),
                             "head_free_bytes": free}
                if free is not None and free < 2 * need:
                    r["warnings"].append(f"disk: the head needs ~{2 * need / 1e9:.0f} GB for the two rank files, "
                                         f"{free / 1e9:.0f} GB free here")
            else:
                r["disk"] = {"head_extra_bytes": 0, "worker_extra_bytes": wbytes, "head_free_bytes": free}
            hf = expand(fam["hf_cache"]) if fam.get("hf_cache") else None
            if hf and hf.exists():
                same = hf.stat().st_dev == d.stat().st_dev
                r["hard_link"] = same
                if not same:
                    r["warnings"].append(f"{d} and {hf} are on different file systems: staging copies instead of "
                                         "hard-linking (double the disk)")
            return r
        r["family_mismatch"] = why
    ok = [c for c in claims if c["ok"]]
    if ok:
        c = ok[0]
        f = fit(wbytes, pair, c["ranks"])
        r["fit"] = f
        if f["tp"] is None:
            r.update(verdict="NOT SERVABLE HERE", reasons=[
                f"{c['title']} reads {mt} as {fmt}, on {c['ranks']} rank(s), but it does not fit: "
                + "; ".join(f"TP{tp} {row['per_rank_gib']} GiB/rank {row['verdict']}"
                            + ("" if row["allowed"] else " (rank count not allowed)") for tp, row in f["rows"].items())])
        else:
            r["verdict"] = "NEEDS A PROFILE"
            r["reasons"].append(f"{c['title']} claims model_type {mt} as {fmt}; TP{f['tp']} "
                                f"({f['rows'][f['tp']]['per_rank_gib']} GiB/rank, {f['rows'][f['tp']]['verdict']})"
                                + ("" if c["ranks_verified"] else "; rank counts UNVERIFIED for this family")
                                + (f"; {c['note']}" if c.get("note") else ""))
            if r.get("family_mismatch"):
                r["reasons"].append(f"not a drop-in for our {r['family']} recipe: " + "; ".join(r["family_mismatch"]))
            r["next"].append(f"scripts/backends/model-inspect.sh {d} --write-profile NAME --backend {c.get('backend', 'tensorfold')}")
            r["next"].append(f"then {c['launch']}")
        return r
    if r.get("family_mismatch"):
        r.update(verdict="SAME FAMILY, NOT A DROP-IN", reasons=[
            f"model_type {mt} is our {r['family']} family, but: " + "; ".join(r["family_mismatch"]),
            "the recipe must learn this layout (or quant variant): docs/EXL3_PLAYBOOK.md case (a), second half"])
        r["reasons"] += [c["why"] for c in claims if not c["ok"]]
        r["reasons"] += [e["refuses"][mt] for e in reg.get("engines", []) if mt in (e.get("refuses") or {})]
        return r
    reasons = [c["why"] for c in claims if not c["ok"]]
    refused = [e["refuses"][mt] for e in reg.get("engines", []) if mt in (e.get("refuses") or {})]
    reasons += refused
    tfs = ins["engines"]["tensorfold"]
    if not reasons:
        reasons.append(f"no engine we have serves model_type {mt!r} as {fmt} (the families of "
                       + ", ".join(e["name"] for e in reg.get("engines", [])) + ": "
                       + ", ".join(sorted(set().union(*[(e.get('families') or {}).keys()
                                                       for e in reg.get('engines', [])]))) + ")")
    reasons.append(f"vendored container TensorFold list @{tfs['vendored_commit'][:7]}: {tfs['status']} "
                   f"({'; '.join(tfs['notes'][:2])})")
    r.update(verdict="NOT SERVABLE HERE", reasons=reasons)
    return r


# --------------------------------------------------------------------------- output

def _gb(n) -> str:
    return "?" if n is None else f"{n / 1e9:.1f} GB ({n / GIB:.1f} GiB)"


def render(r: dict) -> str:
    L = [f"Folder     {r['dir']}"]
    if "model_type" in r:
        L.append(f"Model      model_type {r['model_type'] or '?'}   architectures {', '.join(r['architectures']) or '?'}")
        L.append(f"Weights    {r['quant']} (format {r['format']})   {_gb(r['weight_bytes'])} in {r['shards']} shard(s)")
        if r.get("index_keys"):
            L.append(f"Index      {r['index_keys']} tensor keys, sha256 {r['index_sha256'][:16]}…   local rev "
                     f"{(r.get('local_rev') or '-')[:8]}")
        if r.get("moe"):
            L.append(f"MoE        {r['moe']['experts']} experts, {r['moe']['active_per_token']} active per token")
        cs = r.get("checksums") or {}
        if cs.get("present"):
            v = cs.get("verified")
            L.append(f"Checksums  {cs.get('file', 'SHA256SUMS')}, {cs['entries']} entries; "
                     + (f"verified {v['ok']} ok, {len(v['mismatch'])} mismatched" if v else "not verified (--checksums)"))
        else:
            L.append(f"Checksums  {cs.get('note', '-')}")
    if r.get("family"):
        L.append(f"Family     {r['family']}")
    d = r.get("index_diff")
    if d:
        L.append(f"Index diff vs {d['against']}: +{d['added']} -{d['removed']} keys; added by suffix "
                 f"{d['added_suffixes']}, removed {d['removed_suffixes']}")
    f = r.get("fit")
    if f:
        L.append("Fit        " + "; ".join(
            f"TP{tp}: {row['per_rank_gib']} GiB/rank {row['verdict']}" + ("" if row["allowed"] else " (not allowed)")
            for tp, row in f["rows"].items())
            + f"   (a rank gets ≤ {f['limit_gib']:.0f} GiB of weights, comfortable ≤ {f['comfortable_gib']:.0f})")
    dk = r.get("disk")
    if dk:
        L.append(f"Disk       head +{dk['head_extra_bytes'] / 1e9:.0f} GB, spark-2 +{dk['worker_extra_bytes'] / 1e9:.0f} GB"
                 + (f"; {dk['head_free_bytes'] / 1e9:.0f} GB free here" if dk.get("head_free_bytes") is not None else "")
                 + ({True: "; hard links possible", False: "; NOT on the cache's file system"}.get(r.get("hard_link"), "")))
    for w in r["warnings"]:
        L.append(f"WARNING    {w}")
    for x in r["reasons"]:
        L.append(f"Why        {x}")
    for x in r["next"]:
        L.append(f"Next       {x}")
    L.append(f"VERDICT: {r['verdict']}" + (f" {r['family']}" if r["verdict"] in ("DROP-IN", "SAME FAMILY, NOT A DROP-IN")
                                          else ""))
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", metavar="DIR")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--checksums", action="store_true", help="verify SHA256SUMS (reads every listed file)")
    ap.add_argument("--registry", type=Path, default=REGISTRY)
    a = ap.parse_args(argv)
    reg = load_registry(a.registry)
    out = []
    for x in a.dirs:
        p = Path(x)
        if not p.exists() and not x.startswith(("/", ".", "~")) and "/" in x:
            print(f"Error: {x}: no such folder (intake is offline: it never fetches a Hub id; "
                  "model-inspect.sh reads the Hub)", file=sys.stderr)
            return 2
        out.append(intake(p, reg, a.checksums))
    if a.json:
        print(json.dumps(out if len(out) > 1 else out[0], indent=1, default=str))
    else:
        print("\n\n".join(render(r) for r in out))
    return 0 if all(r["verdict"] == "DROP-IN" for r in out) else 1


if __name__ == "__main__":
    sys.exit(main())
