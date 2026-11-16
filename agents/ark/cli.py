#!/usr/bin/env python3
"""beast-ark — pinned open-source software in cold storage. scripts/ark.sh is the entry point.

  list    [--category C] [--profile core] [--from DIR] [--json]
  plan    [--category C] [--only a,b] [--platform P] [--profile core] [--json]
  lint                                  the catalog schema check (CI runs it)
  lock    [--only a,b] [--category C] [--refresh] [--from DIR] [--dry-run]   (online)
  update  [--only a,b] [--category C] [--write]                              (online)
  fetch   --to DIR [--platform P ...] [--only a,b] [--category C] [--profile core]
          [--kinds file,oci,git,pip,npm,gomod,cargo] [--no-git] [--allow-unverified]
          [--recheck] [--dry-run]                                            (online)
  verify  DIR [--deep | --quick] [--json] re-hash everything; never dials
  restore NAME --from DIR [--platform P] [--prefix ~/.local/opt] [--bin ~/.local/bin]
          [--src ~/src] [--registry HOST:PORT | --docker | --podman] [--kinds ...]
          [--python PATH] [--force]                                          (offline)
  serve   DIR [--bind ADDR | --tailnet] [--port 8780]                        (offline)

Exit codes: 0 ok · 1 verification failed (mismatch, corruption, missing) ·
2 usage / catalog error · 3 offline refusal · 4 incomplete (some items failed).
Design: docs/BEAST_ARK.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "ark"  # noqa: A001

from ark import catalog as C            # noqa: E402
from ark import fetch as F              # noqa: E402
from ark import net, oci, pins          # noqa: E402
from ark import restore as R            # noqa: E402
from ark import store as S              # noqa: E402
from ark import verify as V             # noqa: E402

GiB = float(1 << 30)


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


def _csv(vals) -> list:
    out = []
    for v in vals or []:
        out += [x.strip() for x in v.split(",") if x.strip()]
    return out


def _fmt(n: int, est: bool = False) -> str:
    if n == 0:
        return "?"
    s = f"{n / GiB:.2f} GiB" if n >= (1 << 30) else f"{n / (1 << 20):.0f} MiB" if n >= (1 << 20) else f"{n >> 10} KiB"
    return ("~" + s) if est else s


def _platforms(vals) -> list:
    ps = _csv(vals)
    if not ps or ps == ["all"]:
        return list(C.PLATFORMS)
    if ps == ["host"]:
        return [R.host_platform()]
    bad = [p for p in ps if p not in C.PLATFORMS]
    if bad:
        raise C.CatalogError(f"unknown platform {bad}; one of {C.PLATFORMS}, 'all' or 'host'")
    return ps


def _entries(cat: C.Catalog, a) -> list:
    only = _csv(getattr(a, "only", None))
    unknown = [o for o in only if o not in cat.entries]
    if unknown:
        raise C.CatalogError(f"no such entry: {', '.join(unknown)} (ark list shows them)")
    return cat.select(only=only, category=_csv(getattr(a, "category", None)),
                      profile=getattr(a, "profile", "full") or "full")


# ── list / plan ──────────────────────────────────────────────────────────

def cmd_list(cat: C.Catalog, a) -> int:
    if a.from_dir:
        lock = S.Drive(a.from_dir).read_lock()
        by = {}
        for k, it in lock["items"].items():
            by.setdefault(it["entry"], []).append((k, it))
        if a.json:
            print(json.dumps(by, indent=1))
            return 0
        for name in sorted(by):
            its = by[name]
            print(f"{name:22} {its[0][1].get('version', ''):18} "
                  f"{', '.join(sorted({it['kind'] + ':' + it['platform'] for _k, it in its}))}")
        return 0
    rows = []
    for e in _entries(cat, a):
        kinds = sorted({x.kind for x in e.artifacts} | ({"git"} if e.git else set()))
        unv = sum(1 for x in e.artifacts if x.verified in ("unverified", ""))
        rows.append({"name": e.name, "category": e.category, "version": e.version, "core": e.core,
                     "license": e.license, "kinds": kinds, "artifacts": len(e.artifacts),
                     "unverified": unv, "commit": e.commit[:12], "description": e.description})
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    cur = None
    for r in sorted(rows, key=lambda r: (r["category"], r["name"])):
        if r["category"] != cur:
            cur = r["category"]
            print(f"\n[{cur}]")
        flag = "*" if r["core"] else " "
        unv = f"  ({r['unverified']} unverified)" if r["unverified"] else ""
        print(f" {flag} {r['name']:22} {r['version']:20} {r['license']:28} {'+'.join(r['kinds'])}{unv}")
    print(f"\n{len(rows)} entries (* = core profile). Details: docs/BEAST_ARK.md")
    return 0


def plan_table(cat: C.Catalog, entries, platforms) -> dict:
    """{category: {platform: [bytes, unknown, estimated]}, ...} plus git and totals."""
    cats: dict = {}
    git = [0, 0]
    union = [0, 0]
    for e in entries:
        row = cats.setdefault(e.category, {p: [0, 0, 0] for p in platforms})
        if e.git:
            if e.git_size:
                git[0] += e.git_size
            else:
                git[1] += 1
        for x in e.artifacts:
            sz = x.sizes()
            seen = False
            for p in platforms:
                if p not in sz:
                    continue
                n = sz[p]
                cell = row[p]
                if n:
                    cell[0] += n
                    if x.is_estimate():
                        cell[2] += 1
                else:
                    cell[1] += 1
                seen = True
            if seen:                         # one drive holding every selected platform
                if x.kind == "oci":
                    per = {}
                    for item in x.platform_sizes:
                        k, _, v = item.partition("=")
                        per[k] = int(v) if v.isdigit() else 0
                    want = {C.OCI_FOR[p] for p in platforms} & set(x.platforms)
                    tot = sum(per.get(p, 0) for p in want)
                    union[0] += tot
                    union[1] += 0 if tot else 1
                else:
                    n = next(iter(sz.values()), 0)
                    union[0] += n
                    union[1] += 0 if n else 1
    for e in entries:
        cats[e.category]["_n"] = cats[e.category].get("_n", 0) + 1
    return {"categories": cats, "git": git, "union": union}


def cmd_plan(cat: C.Catalog, a) -> int:
    plats = _platforms(a.platform)
    profiles = ["full", "core"] if a.profile == "both" else [a.profile or "full"]
    out = {}
    for prof in profiles:
        a.profile = prof
        ents = _entries(cat, a)
        out[prof] = plan_table(cat, ents, plats)
        out[prof]["entries"] = len(ents)
    if a.json:
        print(json.dumps(out, indent=1, default=str))
        return 0
    for prof, t in out.items():
        print(f"beast-ark plan — profile {prof}: {t['entries']} entries\n")
        w = 16
        print(f"{'category':16} {'entries':>7} " + " ".join(f"{p:>{w}}" for p in plats))
        tot = {p: [0, 0, 0] for p in plats}
        for c in sorted(t["categories"]):
            row = t["categories"][c]
            cells = []
            for p in plats:
                n, unk, est = row[p]
                tot[p][0] += n
                tot[p][1] += unk
                tot[p][2] += est
                cells.append(f"{_fmt(n, est > 0) + (f' +{unk}?' if unk else ''):>{w}}")
            print(f"{c:16} {row.get('_n', 0):>7} " + " ".join(cells))
        print("-" * (25 + (w + 1) * len(plats)))
        print(f"{'artifacts':16} {'':>7} " + " ".join(
            f"{_fmt(tot[p][0], tot[p][2] > 0) + (f' +{tot[p][1]}?' if tot[p][1] else ''):>{w}}" for p in plats))
        g = t["git"]
        print(f"{'git (source)':16} {'':>7} " + " ".join(
            f"{('≤' + _fmt(g[0])) + (f' +{g[1]}?' if g[1] else ''):>{w}}" for p in plats))
        u = t["union"]
        print(f"\none drive holding {'+'.join(plats)}: {_fmt(u[0])} of artifacts"
              f"{f' (+{u[1]} of unknown size)' if u[1] else ''} + ≤{_fmt(g[0])} of git")
        print("  ? = size unknown until `ark lock` / a fetch; ~ = includes estimates (package sets);")
        print("  ≤ = git: GitHub's full-repository size, an upper bound for a shallow pin\n")
    return 0


# ── verify ───────────────────────────────────────────────────────────────

def verify_drive(drive: S.Drive, deep: bool = False, quick: bool = False) -> dict:
    """quick: presence and size only (minutes, not hours, on a full drive) —
    NOT an integrity check, and the output says so."""
    lock = drive.read_lock()
    res = {"ok": 0, "missing": [], "corrupt": [], "bad_signature": [], "unlisted": [], "tofu": [],
           "stored_signatures": 0, "items": len(lock["items"])}
    listed = set()
    for key, it in sorted(lock["items"].items()):
        for f in it.get("files", []):
            listed.add(f["path"])
            p = drive.p(f["path"])
            if not os.path.isfile(p):
                res["missing"].append(f["path"])
                continue
            if os.path.getsize(p) != f["size"] or (not quick and net.sha256_file(p) != f["sha256"]):
                res["corrupt"].append(f["path"])
                continue
            res["ok"] += 1
        if "tofu" in it.get("verified", ""):
            res["tofu"].append(key)
        sig = it.get("signature") or {}
        if sig.get("status") == "stored":
            res["stored_signatures"] += 1
        if not quick and sig.get("status") == "verified" and sig.get("kind") in ("minisign", "ed25519-sums"):
            try:
                V.check_signature(sig["kind"], sig.get("key", ""), C.REPO_DIR, drive.p(sig["target"]),
                                  drive.p(sig["path"]), sig.get("prefix", ""), it["entry"])
            except (V.VerifyError, OSError, KeyError, ValueError) as e:
                res["bad_signature"].append(f"{key}: {e}")
        if deep and it.get("kind") == "oci" and os.path.isfile(drive.p(it["primary"])):
            arc = oci.OciArchive(drive.p(it["primary"]))
            try:
                for b in arc.verify():
                    res["corrupt"].append(f"{it['primary']}:{b}")
            finally:
                arc.close()
    for top in ("files", "git", "oci", "pkgs"):
        base = os.path.join(drive.root, top)
        for root, _d, files in os.walk(base):
            for fn in files:
                rel = os.path.relpath(os.path.join(root, fn), drive.root).replace(os.sep, "/")
                if rel not in listed:
                    res["unlisted"].append(rel)
    return res


def cmd_verify(a) -> int:
    drive = S.Drive(a.dir)
    if not os.path.isfile(drive.lock_path):
        _err(f"{drive.root} has no ark.lock — not a beast-ark drive")
        return 2
    r = verify_drive(drive, a.deep, a.quick)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        what = "present with the right size (--quick: NOT re-hashed)" if a.quick else "OK"
        print(f"verify {drive.root}: {r['ok']} files {what} across {r['items']} items")
        for k in ("missing", "corrupt", "bad_signature"):
            for x in r[k]:
                print(f"  {k.upper()}: {x}")
        if r["unlisted"]:
            print(f"  {len(r['unlisted'])} file(s) on the drive that ark.lock does not list (never served or restored):")
            for x in r["unlisted"][:20]:
                print(f"    {x}")
        if r["tofu"]:
            print(f"  {len(r['tofu'])} item(s) trusted on first use (no upstream checksum existed): "
                  f"{', '.join(r['tofu'][:8])}{' …' if len(r['tofu']) > 8 else ''}")
        if r["stored_signatures"]:
            print(f"  {r['stored_signatures']} gpg/cosign signature(s) kept but not checked here (docs/BEAST_ARK.md §9)")
    bad = r["missing"] or r["corrupt"] or r["bad_signature"]
    if not a.json:
        print("RESULT: " + ("FAILED" if bad else "OK"))
    return 1 if bad else 0


# ── main ─────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ark.sh", description="beast-ark: pinned software in cold storage")
    ap.add_argument("--catalog", default=None, help="catalog file (default agents/ark/catalog.toml, or $ARK_CATALOG)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def sel(p, prof=True):
        p.add_argument("--only", action="append")
        p.add_argument("--category", action="append")
        if prof:
            p.add_argument("--profile", default="full", choices=["full", "core", "both"])

    p = sub.add_parser("list")
    sel(p)
    p.add_argument("--from", dest="from_dir")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("plan")
    sel(p)
    p.add_argument("--platform", action="append")
    p.add_argument("--json", action="store_true")
    sub.add_parser("lint")
    p = sub.add_parser("lock")
    sel(p, prof=False)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--from", dest="from_dir")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-sizes", action="store_true")
    p.add_argument("--no-git-sizes", action="store_true")
    p = sub.add_parser("update")
    sel(p, prof=False)
    p.add_argument("--write", action="store_true")
    p = sub.add_parser("fetch")
    sel(p)
    p.add_argument("--to", required=True)
    p.add_argument("--platform", action="append")
    p.add_argument("--kinds", action="append")
    p.add_argument("--no-git", action="store_true")
    p.add_argument("--allow-unverified", action="store_true")
    p.add_argument("--recheck", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("verify")
    p.add_argument("dir")
    p.add_argument("--deep", action="store_true")
    p.add_argument("--quick", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("restore")
    p.add_argument("name")
    p.add_argument("--from", dest="from_dir", required=True)
    p.add_argument("--platform", default="")
    p.add_argument("--prefix", default="")
    p.add_argument("--bin", default="")
    p.add_argument("--src", default="")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--registry", default="")
    g.add_argument("--docker", action="store_true")
    g.add_argument("--podman", action="store_true")
    p.add_argument("--kinds", action="append")
    p.add_argument("--python", default="")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("serve")
    p.add_argument("dir")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--bind", default="127.0.0.1")
    g.add_argument("--tailnet", action="store_true")
    p.add_argument("--port", type=int, default=8780)
    a = ap.parse_args(argv)

    try:
        if a.cmd == "verify":
            return cmd_verify(a)
        if a.cmd in ("restore", "serve"):
            d = a.from_dir if a.cmd == "restore" else a.dir
            drive = S.Drive(d)
            if not os.path.isfile(drive.lock_path):
                _err(f"{drive.root} has no ark.lock — not a beast-ark drive")
                return 2
            if a.cmd == "restore":
                dcat = os.path.join(drive.root, "catalog.toml")
                cat = C.load(dcat) if os.path.isfile(dcat) else None
                eng = "docker" if a.docker else "podman" if a.podman else ""
                for line in R.restore(cat, drive, a.name, a.platform, a.prefix, a.bin, a.src, a.registry, eng,
                                      _csv(a.kinds), a.python, a.force):
                    print(line)
                return 0
            from ark import serve as SV  # noqa: PLC0415
            bind = SV.tailnet_ip() if a.tailnet else a.bind
            if bind in ("0.0.0.0", "::"):
                print("WARNING: binding every interface — the whole LAN can read this drive", file=sys.stderr)
            httpd = SV.serve(drive, bind, a.port)
            ix_note = f"http://{bind}:{a.port}/"
            print(f"beast-ark serving {drive.root} at {ix_note} (read-only; Ctrl-C stops)", flush=True)
            print(f"  images: {bind}:{a.port}/<repo>:<tag>   pip: {ix_note}pypi/simple/   "
                  f"npm: {ix_note}npm/   go: {ix_note}goproxy", flush=True)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
            return 0

        cat = C.load(a.catalog)
        if a.cmd == "lint":
            errs = C.lint(cat)
            for e in errs:
                print(f"  {e}")
            n_art = len(cat.artifacts)
            pinned = sum(1 for x in cat.artifacts if x.pinned())
            print(f"lint {cat.path}: {len(cat.entries)} entries, {n_art} artifacts "
                  f"({pinned} hash/digest-pinned), {len(errs)} problem(s)")
            return 1 if errs else 0
        if a.cmd == "list":
            return cmd_list(cat, a)
        if a.cmd == "plan":
            return cmd_plan(cat, a)
        if a.cmd in ("lock", "update"):
            ents = _entries(cat, a)
            if a.cmd == "lock":
                drv = S.Drive(a.from_dir) if a.from_dir else None
                ed, rep = pins.lock(cat, ents, a.refresh, drv, sizes=not a.no_sizes,
                                    git_sizes=not a.no_git_sizes)
                write_it = not a.dry_run
            else:
                ed, rep = pins.update(cat, ents)
                write_it = a.write
            for line in ([] if isinstance(rep, pins.Report) else rep):
                print(line, file=sys.stderr)
            if not len(ed):
                print(f"{a.cmd}: nothing to change", file=sys.stderr)
                return 0
            new = ed.apply(cat)
            diff = pins.show_diff(cat.text, new, os.path.relpath(cat.path))
            print(diff)
            if write_it:
                pins.write(cat, new)
                print(f"{a.cmd}: wrote {len(ed)} change(s) to {cat.path} — review `git diff` and commit", file=sys.stderr)
                if a.cmd == "update":
                    print("next: ./scripts/ark.sh lock   (re-derives the cleared hashes from upstream)", file=sys.stderr)
            else:
                print(f"{a.cmd}: {len(ed)} change(s) NOT written ({'--write' if a.cmd == 'update' else 'drop --dry-run'} applies them)",
                      file=sys.stderr)
            return 0
        if a.cmd == "fetch":
            net.require_online("ark fetch")       # refuse first: no point linting what cannot be fetched
            errs = C.lint(cat)
            if errs:
                _err(f"the catalog has {len(errs)} lint problem(s); fix them first (ark lint)")
                return 2
            plats = _platforms(a.platform)
            kinds = _csv(a.kinds) or None
            items = F.plan_items(cat, _entries(cat, a), plats, kinds, with_git=not a.no_git)
            print(f"fetch {len(items)} item(s) for {', '.join(plats)} into {os.path.abspath(a.to)}", file=sys.stderr)
            return F.run(cat, S.Drive(a.to), items, plats, a.allow_unverified, a.recheck, a.dry_run)
    except net.OfflineError as e:
        _err(str(e))
        return 3
    except (C.CatalogError, S.StoreError, R.RestoreError, oci.OciError, V.VerifyError, net.HTTPFailure,
            RuntimeError) as e:
        _err(str(e))
        return 1 if isinstance(e, (R.RestoreError, V.VerifyError)) else 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
