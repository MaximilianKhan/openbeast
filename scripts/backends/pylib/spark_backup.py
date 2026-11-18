#!/usr/bin/env python3
"""spark_backup: the engine of spark-backup.sh and spark-restore.sh.

docs/SPARK_BACKUP_RESTORE.md. Stdlib only, python >= 3.9.

The backup is a file-level OVERLAY: the OpenBeast state of one Spark's
account (checkout, secrets, units, tools, recipes, weights; never what
rebuilds offline, like the rank files) plus its docker images and volumes, on
an ext4 (LUKS2) drive. A golden base image of DGX OS (golden-image --plan,
identity) plus this overlay rebuilds either Spark of the pair, offline; the per-node bits (fabric address, rank,
ssh peers, unit set) are written by the restore from a fleet file, never
copied from the node the backup came from.

    spark_backup.py inventory [--role head|worker] [--json]
    spark_backup.py run --to DIR [--apply] [--no-rank-caches] [--config-only] [--no-images]
                        [--with-optional-images] [--no-pause] [--no-hash]
                        [--allow-unencrypted]
    spark_backup.py verify GEN [--deep] [--against-live]
    spark_backup.py list DIR
    spark_backup.py restore --from GEN [--fleet FILE] --index N [--apply] [--yes]
                        [--skip-weights] [--skip-images] [--force-volumes] [--no-units]
    spark_backup.py rebuild [--apply]                        # head: DeepSeek's rank files, offline
    spark_backup.py golden-image --plan [--to DRIVE]         # read-only: the DGX OS base-image procedure
    spark_backup.py identity --target MNT (--bundle F | --fresh --hostname H) [--serial S] [--apply]
                                                             # live USB, root: one node's identity into a restored image

Nothing here runs as root. `run` writes only under --to; `restore` writes
only under $HOME (and docker's store through the docker CLI). Both print
their plan and change nothing without --apply.

Test seams (environment): SPARK_BACKUP_HOME, SPARK_BACKUP_ITEMS,
SPARK_BACKUP_SERVING_REV, SPARK_BACKUP_SERVING_WCD, SPARK_BACKUP_HOSTNAME, SPARK_BACKUP_NOW,
SPARK_BACKUP_UID / _GID / _USER, SPARK_RESTORE_HOME_CHECK=0, SPARK_IDENTITY_ROOT_CHECK=0,
SPARK_PREPARE_LOCAL, SPARK_GOLDEN_DGX_RELEASE, and the commands DOCKER, RSYNC, ZSTD,
FINDMNT, LSBLK, NVIDIA_SMI, IBDEV2NETDEV, SYSTEMCTL, SSH, GIT, MOKUTIL, FWUPDMGR,
SSH_KEYGEN, UNAME_M (a value, not a command).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time

TOOL_VERSION = 1
HERE = os.path.dirname(os.path.abspath(__file__))
BACKENDS = os.path.dirname(HERE)
FILE_CLASSES = ("keep", "secret", "weights", "rankcache", "node", "rebuild", "skip")
COPY_CLASSES = ("keep", "secret", "weights", "rankcache", "node")
RESTORE_CLASSES = ("keep", "secret", "weights", "rankcache")
ALL_CLASSES = FILE_CLASSES + ("image", "volume")
ROLES = ("head", "worker", "all", "opt")
LOCAL_SH = "openbeast-recipes/deepseek-prep/runtime/scripts/local.sh"
GLM_LOCAL_SH = "openbeast-recipes/glm53/scripts/local.sh"
# The pair's two inference units; exactly one is enabled (GLM's is the default).
GLM_UNIT = "spark-hydra-inference.service"
DS_UNIT = "deepseek-v41-inference.service"
INFERENCE_UNITS = (GLM_UNIT, DS_UNIT)
DEFAULT_HEAD_UNITS = f"openbeast.service {GLM_UNIT} openbeast-logrotate.timer openbeast-watchdog.timer"
# Filesystems that keep what the backup depends on: owners and modes, hard
# links (Documents/ and the HF caches are links of each other: without them
# 1.1 TB becomes 1.6 TB), symlinks (the HF snapshot layout), ACLs, xattrs.
GOOD_FS = ("ext4", "ext3", "xfs", "btrfs")
BAD_FS = {"exfat": "no owners, modes, hard links or symlinks",
          "vfat": "no owners, modes, hard links or symlinks; 4 GB file limit",
          "msdos": "no owners, modes, hard links or symlinks; 4 GB file limit",
          "ntfs": "no POSIX owners/modes through ntfs-3g by default",
          "ntfs3": "no POSIX owners/modes by default",
          "fuseblk": "a FUSE filesystem (exFAT/NTFS): no POSIX owners/modes",
          "apfs": "not writable from Linux", "hfsplus": "not writable from Linux reliably"}


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def warn(msg: str) -> None:
    print(f"  !! {msg}", file=sys.stderr)


def cmd(name: str, default: str) -> list:
    return shlex.split(os.environ.get(name) or default)


def run_out(argv: list, check: bool = False, inp: bytes | None = None, timeout: float | None = None) -> tuple:
    try:
        p = subprocess.run(argv, input=inp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"{argv[0]}: timed out"
    except FileNotFoundError:
        if check:
            die(f"{argv[0]} is not installed")
        return 127, "", f"{argv[0]}: not found"
    out, err = p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")
    if check and p.returncode != 0:
        die(f"{' '.join(map(shlex.quote, argv))} failed: {err.strip()}")
    return p.returncode, out, err


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000.0
    return f"{n:.1f} TB"


def home_dir() -> str:
    return os.path.realpath(os.environ.get("SPARK_BACKUP_HOME") or os.path.expanduser("~"))


def hostname() -> str:
    return os.environ.get("SPARK_BACKUP_HOSTNAME") or socket.gethostname().split(".")[0]


def env_value(text: str, key: str) -> str | None:
    """The last KEY=value in a KEY=VALUE file, one layer of quotes stripped."""
    val = None
    for line in text.splitlines():
        m = re.match(r"^\s*" + re.escape(key) + r"\s*=(.*)$", line)
        if not m:
            continue
        v = m.group(1).strip()
        if len(v) >= 2 and v[0] in "\"'" and v[0] in v[1:]:
            v = v[1:v.index(v[0], 1)]
        else:
            v = re.sub(r"\s+#.*$", "", v).strip()
        val = v
    return val


def set_env_key(text: str, key: str, value: str) -> str:
    """Set KEY=value: rewrite every existing assignment line, or append one."""
    lines = text.splitlines()
    pat = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    hit = False
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = f"{key}={value}"
            hit = True
    if not hit:
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


# ── The items file ────────────────────────────────────────────────────────────

class Item:
    def __init__(self, cls, roles, path, note, line):
        self.cls, self.roles, self.path, self.note, self.line = cls, roles, path, note, line
        self.is_dir = path.endswith("/")
        self.rx = None
        if cls in FILE_CLASSES:
            self.rx = re.compile("^" + pattern_regex(path.rstrip("/")) + ("(/.*)?$" if self.is_dir else "$"))

    def for_role(self, role: str) -> bool:
        return self.roles == "all" or self.roles == role

    def __repr__(self):
        return f"{self.cls} {self.roles} {self.path}"


def pattern_regex(p: str) -> str:
    out, i = "", 0
    while i < len(p):
        if p.startswith("**", i):
            out += ".*"
            i += 2
        elif p[i] == "*":
            out += "[^/]*"
            i += 1
        elif p[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(p[i])
            i += 1
    return out


def serving_rev(home: str) -> str:
    rev = os.environ.get("SPARK_BACKUP_SERVING_REV")
    if rev is None:
        try:
            with open(os.path.join(home, LOCAL_SH), encoding="utf-8", errors="replace") as f:
                rev = env_value(f.read(), "MODEL_REVISION") or ""
        except OSError:
            rev = ""
    return rev if re.fullmatch(r"[0-9a-f]{8,64}", rev or "") else ""


def serving_wcd(home: str, rev: str) -> str:
    """The folder name of the serving pack's rank-1 cache: basename of local.sh's (last) WORKER_CACHE_DIR.
    worker-rank-cache-<REV8> for a pack spark-deepseek-pack.sh staged, plain worker-rank-cache for the stock
    pack; worker-rank-cache-<REV8> when local.sh does not say."""
    w = os.environ.get("SPARK_BACKUP_SERVING_WCD")
    if w is None:
        try:
            with open(os.path.join(home, LOCAL_SH), encoding="utf-8", errors="replace") as f:
                w = env_value(f.read(), "WORKER_CACHE_DIR") or ""
        except OSError:
            w = ""
    w = os.path.basename((w or "").rstrip("/"))
    if re.fullmatch(r"[A-Za-z0-9._-]+", w) and w not in (".", ".."):
        return w
    return f"worker-rank-cache-{rev[:8]}" if rev else ""


def item_subst(rev: str, wcd: str) -> dict:
    """The {NAME} values an items line may use; a line whose value is unknown is skipped."""
    d = {}
    if rev:
        d["SERVING_REV8"] = rev[:8]
    if wcd:
        d["SERVING_WCD"] = wcd
    return d


def load_items(path: str, subst: dict) -> tuple:
    items, skipped = [], []
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError as e:
        die(f"cannot read the items file {path}: {e}")
    for n, raw in enumerate(lines, 1):
        body, _, note = raw.partition("#")
        parts = body.split()
        if not parts:
            continue
        if len(parts) != 3:
            die(f"{path}:{n}: want CLASS ROLES PATH, got {body.strip()!r}")
        cls, roles, p = parts
        if cls not in ALL_CLASSES:
            die(f"{path}:{n}: unknown class {cls!r} (one of {', '.join(ALL_CLASSES)})")
        if roles not in ROLES or (roles == "opt" and cls != "image"):
            die(f"{path}:{n}: roles must be head, worker or all (opt: images only), got {roles!r}")
        if cls in FILE_CLASSES:
            if p.startswith("/") or ".." in p.split("/") or p.startswith("~"):
                die(f"{path}:{n}: {p!r} must be relative to the home directory, without '..'")
            for k, v in subst.items():
                p = p.replace("{" + k + "}", v)
            if "{" in p:
                skipped.append((n, p))
                continue
        items.append(Item(cls, roles, p, note.strip(), n))
    return items, skipped


def items_for(args, home: str) -> tuple:
    path = os.environ.get("SPARK_BACKUP_ITEMS") or os.path.join(BACKENDS, "data", "spark-backup.items")
    rev = getattr(args, "serving_rev", None) or serving_rev(home)
    items, skipped = load_items(path, item_subst(rev, serving_wcd(home, rev)))
    return items, skipped, rev


def classify(items: list, rel: str):
    for it in items:
        if it.rx is not None and it.rx.match(rel):
            return it
    return None


def is_ancestor(items: list, rel: str) -> bool:
    """rel is a proper ancestor of some file item's path (pattern-wise)."""
    comps = rel.split("/")
    for it in items:
        if it.rx is None or "**" in it.path:
            continue
        pc = it.path.rstrip("/").split("/")
        if len(pc) > len(comps) and all(fnmatch.fnmatchcase(c, q) for c, q in zip(comps, pc)):
            return True
    return False


def rsync_filter(items: list, mode: str, role: str = "", rank_caches: bool = True, weights: bool = True) -> list:
    """rsync filter rules, first match wins, in items-file order.

    mode "backup": every copy class is included (node too, for the record);
    mode "restore": only the role's restore classes; node and other roles'
    lines become excludes at their position, so a head-only file inside an
    `all` tree (openbeast/openbeast.conf) stays off a worker."""
    rules, seen = [], set()

    def add(r):
        if r not in seen:
            seen.add(r)
            rules.append(r)

    for it in items:
        if it.rx is None:
            continue
        if mode == "backup":
            inc = it.cls in COPY_CLASSES and (rank_caches or it.cls != "rankcache") \
                and (weights or it.cls not in ("weights", "rankcache"))
        else:
            inc = it.cls in RESTORE_CLASSES and it.for_role(role) and (rank_caches or it.cls != "rankcache")
        p = it.path
        if inc:
            comps = p.rstrip("/").split("/")
            for k in range(1, len(comps)):
                add("+ /" + "/".join(comps[:k]) + "/")
            add("+ /" + p + "***" if it.is_dir else "+ /" + p)
        else:
            add("- /" + p)
    add("- *")
    return rules


# ── Walking the live tree ─────────────────────────────────────────────────────

def tree_bytes(path: str, seen: set) -> tuple:
    """(apparent bytes counting each inode once here, bytes new to `seen`, files)."""
    local, app, new, files = set(), 0, 0, 0
    stack = [path]
    while stack:
        p = stack.pop()
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            try:
                stack.extend(os.path.join(p, n) for n in os.listdir(p))
            except OSError:
                pass
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        files += 1
        key = (st.st_dev, st.st_ino)
        if key not in local:
            local.add(key)
            app += st.st_size
        if key not in seen:
            seen.add(key)
            new += st.st_size
    return app, new, files


def scan_home(items: list, home: str) -> tuple:
    """Every entry under home, claimed by its first matching item, else uncovered."""
    claims, uncovered = [], []

    def walk(d, rel):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            return
        for n in names:
            r = f"{rel}/{n}" if rel else n
            p = os.path.join(d, n)
            if os.path.isdir(p) and not os.path.islink(p) and is_ancestor(items, r):
                walk(p, r)
                continue
            it = classify(items, r)
            (claims if it else uncovered).append((r, it))

    walk(home, "")
    return claims, uncovered


def secret_mode_problems(home: str, rels: list) -> list:
    bad = []
    for rel in rels:
        p = os.path.join(home, rel)
        for dp, dns, fns in (os.walk(p) if os.path.isdir(p) else [(os.path.dirname(p), [], [os.path.basename(p)])]):
            for n in fns:
                f = os.path.join(dp, n)
                try:
                    m = os.lstat(f).st_mode
                except OSError:
                    continue
                if stat.S_ISREG(m) and m & 0o077:
                    bad.append(os.path.relpath(f, home))
    return bad


# ── Docker ────────────────────────────────────────────────────────────────────

def docker_ok() -> bool:
    rc, _, _ = run_out(cmd("DOCKER", "docker") + ["info", "--format", "{{.ServerVersion}}"])
    return rc == 0


def compose_refs(home: str) -> dict:
    refs = {}
    try:
        text = open(os.path.join(home, "openbeast", "docker-compose.yml"), encoding="utf-8").read()
    except OSError:
        return refs
    for m in re.finditer(r"^\s*image:\s*[\"']?([^\s\"']+)", text, re.M):
        ref = m.group(1)
        name = ref.split("@")[0].split(":")[0].rsplit("/", 1)[-1]
        refs.setdefault(name, ref)
    return refs


def docker_images() -> list:
    rc, out, _ = run_out(cmd("DOCKER", "docker") + ["image", "ls", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}"])
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and not parts[0].endswith(":<none>"):
            rows.append((parts[0], parts[1]))
    return rows


def image_id(ref: str) -> str:
    rc, out, _ = run_out(cmd("DOCKER", "docker") + ["image", "inspect", "-f", "{{.Id}}", ref])
    return out.strip() if rc == 0 else ""


def image_size(ref: str) -> int:
    rc, out, _ = run_out(cmd("DOCKER", "docker") + ["image", "inspect", "-f", "{{.Size}}", ref])
    try:
        return int(out.strip()) if rc == 0 else 0
    except ValueError:
        return 0


def resolve_images(items: list, home: str, with_optional: bool) -> tuple:
    """[(item, ref, id, compose_ref)], [problems]."""
    out, problems, compose = [], [], compose_refs(home)
    listed = docker_images()
    for it in items:
        if it.cls != "image" or (it.roles == "opt" and not with_optional):
            continue
        if it.path.startswith("compose:"):
            name = it.path.split(":", 1)[1]
            ref = compose.get(name)
            if not ref:
                problems.append(f"{it.path}: docker-compose.yml has no image for '{name}'")
                continue
            iid = image_id(ref)
            if not iid:
                problems.append(f"{it.path}: {ref} is not in the local image store")
                continue
            # Saved by repo@digest: `docker save` of a name:tag@digest reference
            # is not accepted everywhere, and the tag adds nothing to a digest.
            out.append((it, re.sub(r":[^/@]+@", "@", ref), iid, ref))
            continue
        hits = [(r, i) for r, i in listed if fnmatch.fnmatchcase(r, it.path)]
        if not hits:
            problems.append(f"{it.path}: no local image matches")
        for r, _ in hits:
            out.append((it, r, image_id(r), ""))
    return out, problems


def volume_rows(items: list) -> list:
    rows = []
    for it in items:
        if it.cls != "volume":
            continue
        rc, out, _ = run_out(cmd("DOCKER", "docker") + ["volume", "inspect", "-f", "{{.Mountpoint}}", it.path])
        rows.append((it, rc == 0))
    return rows


def safe_name(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", ref)[:120]


# ── inventory ─────────────────────────────────────────────────────────────────

def cmd_inventory(args) -> int:
    home = home_dir()
    items, skipped, rev = items_for(args, home)
    claims, uncovered = scan_home(items, home)
    seen, per_item = set(), {}
    for rel, it in claims:
        app, new, files = tree_bytes(os.path.join(home, rel), seen)
        agg = per_item.setdefault(it.line, {"item": it, "paths": 0, "app": 0, "new": 0, "files": 0})
        agg["paths"] += 1
        agg["app"] += app
        agg["new"] += new
        agg["files"] += files
    unc = []
    for rel, _ in uncovered:
        app, new, files = tree_bytes(os.path.join(home, rel), seen)
        unc.append((rel, new, files))
    role = args.role
    totals = {}
    for agg in per_item.values():
        it = agg["item"]
        if role and not it.for_role(role):
            continue
        totals[it.cls] = totals.get(it.cls, 0) + agg["new"]
    secrets = [rel for rel, it in claims if it.cls == "secret"]
    badmode = secret_mode_problems(home, secrets)
    imgs, iproblems, vols = [], [], []
    have_docker = docker_ok()
    if have_docker:
        imgs, iproblems = resolve_images(items, home, True)
        vols = volume_rows(items)
    if args.json:
        json.dump({
            "host": hostname(), "home": home, "serving_rev": rev,
            "items": [{"class": a["item"].cls, "roles": a["item"].roles, "path": a["item"].path,
                       "paths": a["paths"], "bytes": a["app"], "new_bytes": a["new"], "files": a["files"]}
                      for a in sorted(per_item.values(), key=lambda a: a["item"].line)],
            "uncovered": [{"path": r, "bytes": b, "files": f} for r, b, f in unc],
            "totals": totals, "secret_mode_problems": badmode,
            "images": [{"ref": r, "id": i, "roles": it.roles, "bytes": image_size(r)} for it, r, i, _ in imgs],
            "image_problems": iproblems,
            "volumes": [{"name": it.path, "present": ok} for it, ok in vols],
        }, sys.stdout, indent=1)
        print()
        return 0
    print(f"== inventory of {hostname()}:{home}" + (f" (role {role})" if role else ""))
    print(f"   serving DeepSeek revision: {rev[:8] if rev else 'unknown (rank-cache lines skipped)'}")
    for n, p in skipped:
        print(f"   (items line {n} skipped: {p} needs a value)")
    print(f"\n   {'CLASS':<9} {'ROLES':<6} {'SIZE':>9} {'NEW':>9} {'FILES':>7}  PATH")
    for agg in sorted(per_item.values(), key=lambda a: a["item"].line):
        it = agg["item"]
        if role and not it.for_role(role):
            continue
        print(f"   {it.cls:<9} {it.roles:<6} {human(agg['app']):>9} {human(agg['new']):>9} {agg['files']:>7}  {it.path}")
    missing = [it for it in items if it.rx is not None and it.line not in per_item
               and it.cls in RESTORE_CLASSES and (not role or it.for_role(role)) and "*" not in it.path]
    for it in missing:
        print(f"   {it.cls:<9} {it.roles:<6} {'-':>9} {'-':>9} {'-':>7}  {it.path}   (not on this node)")
    print("\n   NEW counts each hard-linked file once, in walk order: the sum is what the drive holds.")
    backed = sum(v for k, v in totals.items() if k in COPY_CLASSES)
    print(f"   backed up : {human(backed)}  (" + ", ".join(f"{k} {human(v)}" for k, v in sorted(totals.items()) if k in COPY_CLASSES) + ")")
    print("   left out  : " + ", ".join(f"{k} {human(v)}" for k, v in sorted(totals.items()) if k not in COPY_CLASSES))
    if unc:
        print("\n   UNCOVERED — no items line claims these, so a backup leaves them out:")
        for r, b, f in unc:
            print(f"     {human(b):>9} {f:>7}  {r}")
    else:
        print("\n   every entry of the home directory is claimed by an items line")
    if badmode:
        print(f"\n   !! {len(badmode)} secret file(s) readable by group/other (chmod 600): " + ", ".join(badmode[:8]))
    print("\n== docker")
    if not have_docker:
        print("   docker is not reachable for this user: images and volumes not listed")
    for it, r, i, cref in imgs:
        print(f"   image  {it.roles:<6} {human(image_size(r)):>9}  {r}  {i[:19]}")
    for p in iproblems:
        print(f"   image  !! {p}")
    for it, ok in vols:
        print(f"   volume {it.roles:<6} {'present' if ok else 'MISSING':>9}  {it.path}")
    return 0


# ── run (the backup) ──────────────────────────────────────────────────────────

def mount_facts(path: str) -> dict:
    rc, out, _ = run_out(cmd("FINDMNT", "findmnt") + ["-n", "-o", "TARGET,SOURCE,FSTYPE", "--target", path])
    facts = {"target": "", "source": "", "fstype": "", "crypt": False}
    parts = out.split()
    if rc == 0 and len(parts) >= 3:
        facts.update(target=parts[0], source=parts[1], fstype=parts[2])
        rc2, out2, _ = run_out(cmd("LSBLK", "lsblk") + ["-n", "-s", "-o", "TYPE", parts[1]])
        facts["crypt"] = rc2 == 0 and "crypt" in out2.split()
    return facts


def gen_stamp() -> str:
    return os.environ.get("SPARK_BACKUP_NOW") or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def system_record(home: str) -> dict:
    """Read-only facts and copies, for the record. No root, no secrets."""
    rec = {}

    def cat(p):
        try:
            return open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            return ""

    rec["os-release.txt"] = cat("/etc/os-release")
    rec["dgx-release.txt"] = cat("/etc/dgx-release")
    rec["spark-guard.nft"] = cat("/etc/openbeast/spark-guard.nft")
    rec["uname.txt"] = run_out(["uname", "-a"])[1]
    rec["dpkg-l.txt"] = run_out(["dpkg-query", "-W", "-f", "${Package}\t${Version}\t${Status}\n"])[1]
    rec["apt-mark-hold.txt"] = run_out(["apt-mark", "showhold"])[1]
    rec["netplan-files.txt"] = run_out(["ls", "-l", "/etc/netplan"])[1]
    rec["ibdev2netdev.txt"] = run_out(cmd("IBDEV2NETDEV", "ibdev2netdev"))[1]
    rec["ip-addr.txt"] = run_out(["ip", "-br", "addr"])[1]
    rec["nvidia-smi.txt"] = run_out(cmd("NVIDIA_SMI", "nvidia-smi") + ["-q"])[1]
    rec["docker-info.txt"] = run_out(cmd("DOCKER", "docker") + ["info"])[1]
    rec["docker-ps.txt"] = run_out(cmd("DOCKER", "docker") + ["ps", "-a", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"])[1]
    rec["systemd-system-units.txt"] = run_out(["systemctl", "list-unit-files", "--no-legend", "--no-pager"])[1]
    rec["systemd-user-units.txt"] = run_out(cmd("SYSTEMCTL", "systemctl") + ["--user", "list-unit-files", "--no-legend", "--no-pager"])[1]
    rec["tailscale-serve.txt"] = run_out(["tailscale", "serve", "status"])[1]
    rec["linger.txt"] = run_out(["loginctl", "show-user", os.environ.get("USER", ""), "-p", "Linger"])[1]
    # for the golden image and a replacement unit: the disk layout, the boot entries, Secure Boot, firmware versions
    rec["lsblk.txt"] = run_out(cmd("LSBLK", "lsblk") + ["-o", "NAME,SIZE,FSTYPE,LABEL,UUID,PARTUUID,MOUNTPOINTS"])[1]
    rec["fstab.txt"] = cat("/etc/fstab")
    rec["efibootmgr.txt"] = run_out(["efibootmgr", "-v"])[1]
    rec["secureboot.txt"] = run_out(cmd("MOKUTIL", "mokutil") + ["--sb-state"])[1]
    rec["fwupd-devices.txt"] = run_out(cmd("FWUPDMGR", "fwupdmgr") + ["get-devices", "--no-unreported-check"], timeout=60)[1]
    git = cmd("GIT", "git")
    for name, rel in (("openbeast", "openbeast"), ("recipe-deepseek", "openbeast-recipes/deepseek-prep/runtime"),
                      ("recipe-glm53", "openbeast-recipes/glm53")):
        d = os.path.join(home, rel)
        if os.path.isdir(os.path.join(d, ".git")):
            head = run_out(git + ["-C", d, "log", "--oneline", "-1"])[1]
            st = run_out(git + ["-C", d, "status", "--short"])[1]
            rec[f"git-{name}.txt"] = head + st
    return {k: v for k, v in rec.items() if v}


def latest_gen(hostdir: str) -> str:
    link = os.path.join(hostdir, "latest")
    if os.path.islink(link):
        target = os.path.join(hostdir, os.readlink(link))
        if os.path.isfile(os.path.join(target, "BACKUP.json")):
            return os.path.realpath(target)
    return ""


def load_manifest(gen: str) -> dict:
    out = {}
    try:
        with open(os.path.join(gen, "MANIFEST.jsonl"), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    e = json.loads(line)
                    out[e["path"]] = e
    except OSError:
        pass
    return out


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(8 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def write_manifest(gen: str, prev: str, do_hash: bool, jobs: int = 4) -> tuple:
    """MANIFEST.jsonl (authoritative) + SHA256SUMS (for `sha256sum -c`).

    A file that is a hard link of the previous generation's file at the same
    path (--link-dest made it so: same inode) keeps that generation's hash."""
    prevm = load_manifest(prev) if prev else {}
    entries, todo, first_of = [], [], {}
    for sub in ("home", "docker", "system"):
        root = os.path.join(gen, sub)
        for dp, dns, fns in os.walk(root):
            dns.sort()
            for n in sorted(fns) + [d for d in dns if os.path.islink(os.path.join(dp, d))]:
                p = os.path.join(dp, n)
                rel = os.path.relpath(p, gen)
                st = os.lstat(p)
                e = {"path": rel, "size": st.st_size, "mtime": int(st.st_mtime),
                     "mode": oct(stat.S_IMODE(st.st_mode)), "type": "f"}
                if stat.S_ISLNK(st.st_mode):
                    e.update(type="l", target=os.readlink(p), sha256="-")
                elif stat.S_ISREG(st.st_mode):
                    key = (st.st_dev, st.st_ino)
                    if key in first_of:
                        e["link"] = first_of[key]["path"]
                    else:
                        first_of[key] = e
                        pe = prevm.get(rel)
                        pp = os.path.join(prev, rel) if prev else ""
                        if pe and pe.get("sha256") not in (None, "-", "") and pp and os.path.exists(pp) \
                                and os.path.samefile(pp, p):
                            e["sha256"] = pe["sha256"]
                        elif do_hash:
                            todo.append(e)
                        else:
                            e["sha256"] = ""
                else:
                    continue
                entries.append(e)
    t0, done = time.time(), 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(sha256_file, os.path.join(gen, e["path"])): e for e in todo}
        for fut in concurrent.futures.as_completed(futs):
            futs[fut]["sha256"] = fut.result()
            done += futs[fut]["size"]
    by_path = {e["path"]: e for e in entries}
    for e in entries:
        if "link" in e:
            e["sha256"] = by_path[e["link"]].get("sha256", "")
    with open(os.path.join(gen, "MANIFEST.jsonl"), "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, sort_keys=True) + "\n")
    with open(os.path.join(gen, "SHA256SUMS"), "w", encoding="utf-8") as f:
        for e in entries:
            if e["type"] == "f" and e.get("sha256") and "\n" not in e["path"] and "\\" not in e["path"]:
                f.write(f"{e['sha256']}  {e['path']}\n")
    return entries, done, time.time() - t0


def enabled_user_units(home: str) -> list:
    """The unit names linked in ~/.config/systemd/user/*.wants/: what starts at boot."""
    d = os.path.join(home, ".config/systemd/user")
    out = set()
    try:
        for w in os.listdir(d):
            if w.endswith(".wants") and os.path.isdir(os.path.join(d, w)):
                out.update(os.listdir(os.path.join(d, w)))
    except OSError:
        pass
    return sorted(out)


def cmd_run(args) -> int:
    # Everything this writes on the drive is the account's alone (the secrets above all).
    os.umask(0o077)
    home = home_dir()
    items, skipped, rev = items_for(args, home)
    wcd = serving_wcd(home, rev)
    dest = os.path.abspath(args.to)
    host = hostname()
    hostdir = os.path.join(dest, "openbeast-backup", host)
    prev = latest_gen(hostdir) if os.path.isdir(hostdir) else ""
    stamp = gen_stamp()
    gen = os.path.join(hostdir, stamp)
    part = gen + ".partial"
    rank = not (args.no_rank_caches or args.config_only)
    if args.config_only:
        args.no_images = True
    rules = rsync_filter(items, "backup", rank_caches=rank, weights=not args.config_only)
    rsync = cmd("RSYNC", "rsync")
    ropts = ["-aHAX", "--numeric-ids", "--info=stats2,progress2"]
    if prev:
        ropts.append(f"--link-dest={os.path.join(prev, 'home')}")

    print(f"== backup of {host}:{home} -> {gen}" + ("" if args.apply else "   [DRY RUN: nothing is written]"))
    print(f"   serving DeepSeek revision: {rev[:8] if rev else 'unknown — rank-cache lines skipped'}"
          + (f", rank-1 cache {wcd}/" if wcd else ""))
    print(f"   previous generation: {prev or 'none (a full copy)'}" + ("  (unchanged files become hard links of it)" if prev else ""))
    for n, p in skipped:
        print(f"   (items line {n} skipped: {p} needs a value)")
    if args.config_only:
        print("   --config-only: weights, rank files, docker images and volumes are left out (the head's backup holds them)")
    claims, uncovered = scan_home(items, home)
    if uncovered:
        print("   NOT backed up: no items line claims " + ", ".join(r for r, _ in uncovered) +
              "  (spark-backup.sh inventory sizes them; add an items line to keep one)")
    badmode = secret_mode_problems(home, [rel for rel, it in claims if it.cls == "secret"])
    if badmode:
        print(f"   !! {len(badmode)} secret file(s) readable by group/other — chmod 600 before relying on this backup: "
              + ", ".join(badmode[:8]))

    problems = []
    if not os.path.isdir(dest):
        problems.append(f"{dest} does not exist (mount the backup drive there)")
    else:
        mf = mount_facts(dest)
        if not mf["fstype"]:
            problems.append(f"cannot tell what filesystem {dest} is on (findmnt)")
        elif mf["fstype"] in BAD_FS:
            problems.append(f"{dest} is {mf['fstype']}: {BAD_FS[mf['fstype']]} — format the drive ext4 (docs §2.4)")
        elif mf["fstype"] not in GOOD_FS:
            problems.append(f"{dest} is {mf['fstype']}: use ext4 (or xfs/btrfs)")
        elif mf["target"] == "/":
            problems.append(f"{dest} is on the root filesystem, not a separate drive")
        if mf["fstype"] and not mf["crypt"] and not args.allow_unencrypted:
            problems.append(f"{dest} is not on a dm-crypt (LUKS) device: the backup holds every secret of the node. "
                            "Open the drive with cryptsetup (docs §2.5), or pass --allow-unencrypted")
        print(f"   drive: {mf['source'] or '?'} {mf['fstype'] or '?'} mounted at {mf['target'] or '?'}"
              f"{', LUKS' if mf['crypt'] else ''}")
        try:
            free = shutil.disk_usage(dest).free
            print(f"   free on the drive: {human(free)}")
        except OSError:
            pass
    if os.path.exists(gen):
        problems.append(f"{gen} already exists")

    print("\n-- 1. files: one rsync, hard links kept across Documents/, the HF caches and openbeast-models/")
    print("   filter rules (first match wins):")
    for r in rules:
        print(f"     {r}")
    print("   " + " ".join(map(shlex.quote, rsync + ropts + [f"--filter=. {part}/FILTER", home + "/", part + "/home/"])))

    images, iproblems, vols = [], [], []
    if not args.no_images:
        if docker_ok():
            images, iproblems = resolve_images(items, home, args.with_optional_images)
            vols = [(it, ok) for it, ok in volume_rows(items)]
        else:
            iproblems.append("docker is not reachable for this user: images and volumes skipped")
    prev_images = {}
    if prev:
        try:
            for e in json.load(open(os.path.join(prev, "docker", "images.json"))):
                prev_images[e["id"]] = os.path.join(prev, "docker", e["file"])
        except (OSError, ValueError, KeyError):
            pass
    print("\n-- 2. docker images (docker save | zstd), reused from the previous generation when the ID is the same")
    for it, ref, iid, cref in images:
        how = "link from previous generation" if iid in prev_images and os.path.exists(prev_images[iid]) else "save"
        print(f"   {it.roles:<6} {ref}  {iid[:19]}  -> {how}")
    for p in iproblems:
        print(f"   !! {p}")
    print("\n-- 3. docker volumes (containers using them are PAUSED for the copy, then unpaused" +
          (", --no-pause: copied live)" if args.no_pause else ")"))
    for it, ok in vols:
        print(f"   {it.roles:<6} {it.path}" + ("" if ok else "  !! not present: skipped"))
    print("\n-- 4. system record (read-only facts: os, dgx release, dpkg, netplan file names, fabric, docker, units, tailscale serve)")
    print("-- 5. MANIFEST.jsonl + SHA256SUMS" + (" (hashes deferred: --no-hash)" if args.no_hash else ""))
    print("-- 6. rename .partial -> generation, point `latest` at it")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"   !! {p}")
    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to write the backup.")
        return 1 if problems else 0
    if problems:
        die("refusing to write a backup (above)")

    os.makedirs(os.path.join(part, "home"), exist_ok=True)
    os.makedirs(os.path.join(part, "docker"), exist_ok=True)
    os.makedirs(os.path.join(part, "system"), exist_ok=True)
    with open(os.path.join(part, "FILTER"), "w", encoding="utf-8") as f:
        f.write("\n".join(rules) + "\n")
    t0 = time.time()
    print("\n== 1. rsync", flush=True)
    rc = subprocess.call(rsync + ropts + [f"--filter=. {os.path.join(part, 'FILTER')}", home + "/", os.path.join(part, "home") + "/"])
    # 24: files vanished during the copy (a log rotated): not a failure of the backup.
    if rc not in (0, 24):
        die(f"rsync failed (exit {rc}); {part} is left for inspection and is never marked latest")

    img_meta = []
    zstd = cmd("ZSTD", "zstd")
    docker = cmd("DOCKER", "docker")
    for it, ref, iid, cref in images:
        fname = safe_name(ref) + ".tar.zst"
        out = os.path.join(part, "docker", fname)
        if iid in prev_images and os.path.exists(prev_images[iid]):
            os.link(prev_images[iid], out)
            print(f"== 2. {ref}: linked from the previous generation")
        else:
            print(f"== 2. {ref}: docker save", flush=True)
            with open(out + ".partial", "wb") as fo:
                p1 = subprocess.Popen(docker + ["save", ref], stdout=subprocess.PIPE)
                p2 = subprocess.Popen(zstd + ["-T0", "-3", "-q", "-c"], stdin=p1.stdout, stdout=fo)
                p1.stdout.close()
                r2, r1 = p2.wait(), p1.wait()
            if r1 != 0 or r2 != 0:
                die(f"docker save {ref} failed (save {r1}, zstd {r2})")
            os.replace(out + ".partial", out)
        img_meta.append({"ref": ref, "id": iid, "roles": it.roles, "compose_ref": cref, "file": fname})
    with open(os.path.join(part, "docker", "images.json"), "w", encoding="utf-8") as f:
        json.dump(img_meta, f, indent=1)

    vol_meta = []
    helper = os.environ.get("SPARK_BACKUP_HELPER_IMAGE", "alpine:latest")
    for it, ok in vols:
        if not ok:
            continue
        fname = f"volume-{safe_name(it.path)}.tar.zst"
        out = os.path.join(part, "docker", fname)
        rc, users, _ = run_out(docker + ["ps", "-q", "--filter", f"volume={it.path}"])
        users = users.split()
        paused = []
        try:
            if users and not args.no_pause:
                for c in users:
                    if run_out(docker + ["pause", c])[0] == 0:
                        paused.append(c)
                print(f"== 3. {it.path}: paused {len(paused)} container(s)", flush=True)
            with open(out + ".partial", "wb") as fo:
                p1 = subprocess.Popen(docker + ["run", "--rm", "--network", "none", "-v", f"{it.path}:/data:ro",
                                                helper, "tar", "cf", "-", "-C", "/data", "."], stdout=subprocess.PIPE)
                p2 = subprocess.Popen(zstd + ["-T0", "-3", "-q", "-c"], stdin=p1.stdout, stdout=fo)
                p1.stdout.close()
                r2, r1 = p2.wait(), p1.wait()
        finally:
            for c in paused:
                run_out(docker + ["unpause", c])
            if paused:
                print(f"   unpaused {len(paused)} container(s)")
        if r1 != 0 or r2 != 0:
            die(f"exporting volume {it.path} failed (tar {r1}, zstd {r2})")
        os.replace(out + ".partial", out)
        vol_meta.append({"name": it.path, "roles": it.roles, "file": fname})
    with open(os.path.join(part, "docker", "volumes.json"), "w", encoding="utf-8") as f:
        json.dump(vol_meta, f, indent=1)

    print("== 4. system record", flush=True)
    for name, text in system_record(home).items():
        with open(os.path.join(part, "system", name), "w", encoding="utf-8") as f:
            f.write(text)

    print("== 5. manifest" + (" (no hashes)" if args.no_hash else ", hashing new files"), flush=True)
    entries, hashed, secs = write_manifest(part, prev, not args.no_hash)
    total = sum(e["size"] for e in entries if e["type"] == "f" and "link" not in e)
    meta = {
        "tool_version": TOOL_VERSION, "host": host, "generation": stamp,
        "user": os.environ.get("SPARK_BACKUP_USER") or os.environ.get("USER", ""),
        "uid": int(os.environ.get("SPARK_BACKUP_UID") or os.getuid()),
        "gid": int(os.environ.get("SPARK_BACKUP_GID") or os.getgid()),
        "home": home, "serving_rev": rev, "serving_wcd": wcd, "rank_caches": rank, "config_only": bool(args.config_only),
        "enabled_user_units": enabled_user_units(home), "previous": os.path.basename(prev) if prev else "",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": len(entries), "bytes_unique": total, "hashed_bytes": hashed, "hashed": not args.no_hash,
        "seconds": int(time.time() - t0), "images": img_meta, "volumes": vol_meta,
    }
    with open(os.path.join(part, "BACKUP.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    shutil.copy2(os.environ.get("SPARK_BACKUP_ITEMS") or os.path.join(BACKENDS, "data", "spark-backup.items"),
                 os.path.join(part, "ITEMS"))
    os.rename(part, gen)
    tmp = os.path.join(hostdir, ".latest.tmp")
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(stamp, tmp)
    os.replace(tmp, os.path.join(hostdir, "latest"))
    print(f"\n== done: {gen}\n   {len(entries)} files, {human(total)} unique in this generation"
          f" (hashed {human(hashed)} in {secs:.0f} s), latest -> {stamp}")
    print(f"   verify: scripts/backends/spark-backup.sh verify {gen} --deep")
    return 0


# ── verify / list ─────────────────────────────────────────────────────────────

def cmd_verify(args) -> int:
    gen = os.path.abspath(args.gen)
    if os.path.islink(gen) or os.path.basename(gen) == "latest":
        gen = os.path.realpath(gen)
    man = load_manifest(gen)
    if not man:
        die(f"{gen} has no MANIFEST.jsonl")
    bad, checked = [], 0
    todo = []
    for rel, e in man.items():
        p = os.path.join(gen, rel)
        try:
            st = os.lstat(p)
        except OSError:
            bad.append(f"missing: {rel}")
            continue
        if e["type"] == "l":
            if not stat.S_ISLNK(st.st_mode) or os.readlink(p) != e.get("target"):
                bad.append(f"symlink changed: {rel}")
            continue
        if st.st_size != e["size"]:
            bad.append(f"size {st.st_size} != {e['size']}: {rel}")
            continue
        if "link" in e:
            try:
                if not os.path.samefile(p, os.path.join(gen, e["link"])):
                    bad.append(f"hard link to {e['link']} broken: {rel}")
            except OSError:
                bad.append(f"hard link target missing: {rel}")
            continue
        checked += 1
        if args.deep and e.get("sha256"):
            todo.append((rel, p, e["sha256"]))
    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(sha256_file, p): (rel, want) for rel, p, want in todo}
            for fut in concurrent.futures.as_completed(futs):
                rel, want = futs[fut]
                if fut.result() != want:
                    bad.append(f"sha256 mismatch: {rel}")
    unhashed = sum(1 for e in man.values() if e["type"] == "f" and not e.get("sha256"))
    print(f"== {gen}: {len(man)} entries, {checked} distinct files checked"
          f"{', re-hashed ' + str(len(todo)) if args.deep else ' (sizes and links; --deep re-hashes)'}")
    if unhashed:
        print(f"   {unhashed} file(s) have no recorded hash (backup ran with --no-hash)")
    drift = []
    if args.against_live:
        drift = against_live(gen, man)
    for b in bad[:50]:
        print(f"   !! {b}")
    if len(bad) > 50:
        print(f"   !! ... and {len(bad) - 50} more")
    if bad:
        print("== BACKUP DAMAGED")
        return 1
    print("== backup intact" + (" (live drift listed above)" if drift else ""))
    return 0


def against_live(gen: str, man: dict) -> list:
    """Read-only: what changed on this live node since the generation."""
    home = home_dir()
    try:
        meta = json.load(open(os.path.join(gen, "BACKUP.json")))
    except (OSError, ValueError):
        meta = {}
    os.environ.setdefault("SPARK_BACKUP_SERVING_REV", meta.get("serving_rev", "") or "")
    items, _, _ = items_for(argparse.Namespace(serving_rev=meta.get("serving_rev")), home)
    rules_items = [it for it in items if it.cls in COPY_CLASSES and (meta.get("rank_caches", True) or it.cls != "rankcache")]
    drift = []
    in_backup = {rel[len("home/"):]: e for rel, e in man.items() if rel.startswith("home/")}
    claims, _ = scan_home(items, home)
    live = set()
    for rel, it in claims:
        if it not in rules_items:
            continue
        root = os.path.join(home, rel)
        walker = os.walk(root) if os.path.isdir(root) and not os.path.islink(root) else [(os.path.dirname(root), [], [os.path.basename(root)])]
        for dp, dns, fns in walker:
            for n in fns + [d for d in dns if os.path.islink(os.path.join(dp, d))]:
                p = os.path.join(dp, n)
                r = os.path.relpath(p, home)
                if classify(items, r) is not None and classify(items, r).cls not in COPY_CLASSES:
                    continue
                live.add(r)
                e = in_backup.get(r)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if e is None:
                    drift.append(f"new since backup: {r}")
                elif stat.S_ISREG(st.st_mode) and (st.st_size != e["size"] or int(st.st_mtime) != e["mtime"]):
                    drift.append(f"changed since backup: {r}")
    for r in in_backup:
        if r not in live and not r.endswith("/"):
            drift.append(f"gone from the live node: {r}")
    print(f"   against the live node {hostname()}:{home}: {len(drift)} difference(s)")
    for d in drift[:40]:
        print(f"     {d}")
    if len(drift) > 40:
        print(f"     ... and {len(drift) - 40} more")
    return drift


def cmd_list(args) -> int:
    root = os.path.join(os.path.abspath(args.dir), "openbeast-backup")
    if not os.path.isdir(root):
        die(f"no openbeast-backup/ under {args.dir}")
    for host in sorted(os.listdir(root)):
        hd = os.path.join(root, host)
        if not os.path.isdir(hd):
            continue
        latest = os.readlink(os.path.join(hd, "latest")) if os.path.islink(os.path.join(hd, "latest")) else ""
        print(f"== {host}")
        for g in sorted(os.listdir(hd)):
            gp = os.path.join(hd, g)
            if g == "latest" or not os.path.isdir(gp) or os.path.islink(gp):
                continue
            try:
                m = json.load(open(os.path.join(gp, "BACKUP.json")))
                info = f"{human(m.get('bytes_unique', 0)):>9}  rev {(m.get('serving_rev') or '?')[:8]}  {m.get('files', 0)} files"
            except (OSError, ValueError):
                info = "INCOMPLETE (no BACKUP.json)" if g.endswith(".partial") else "no BACKUP.json"
            print(f"   {g}{'  <- latest' if g == latest else ''}  {info}")
    return 0


# ── restore ───────────────────────────────────────────────────────────────────

def parse_fleet(path: str) -> dict:
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        die(f"cannot read the fleet file {path}: {e}")
    fleet = {"name": env_value(text, "FLEET_NAME") or "spark-hydra",
             "user": env_value(text, "FLEET_USER") or "max",
             "ssh_key": env_value(text, "FLEET_SSH_KEY") or "id_ed25519_nvsync_cluster_assistant",
             "head_units": (env_value(text, "FLEET_HEAD_UNITS") or DEFAULT_HEAD_UNITS).split(),
             "worker_units": (env_value(text, "FLEET_WORKER_UNITS") or "opencode-model-link.service").split(),
             "model_link": (env_value(text, "FLEET_WORKER_MODEL_LINK") or "true") == "true",
             "nodes": []}
    i = 0
    while True:
        v = env_value(text, f"NODE_{i}")
        if v is None:
            break
        parts = v.split()
        if len(parts) != 3 or parts[1] not in ("head", "worker"):
            die(f"{path}: NODE_{i} must be '<tailnet-name> head|worker <fabric-ip>[,<fabric-ip>]', got {v!r}")
        name, role, ips = parts
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name):
            die(f"{path}: NODE_{i} name {name!r} is not a valid tailnet/host name")
        iplist = ips.split(",")
        for ip in iplist:
            if not re.fullmatch(r"(\d{1,3})(\.\d{1,3}){3}", ip):
                die(f"{path}: NODE_{i} fabric address {ip!r} is not IPv4")
        fleet["nodes"].append({"index": i, "name": name, "role": role, "ips": iplist})
        i += 1
    n = len(fleet["nodes"])
    if not 1 <= n <= 8:
        die(f"{path}: {n} NODE_i lines — 1 to 8 (one rank per Spark, NODE_0 first)")
    if fleet["nodes"][0]["role"] != "head" or any(x["role"] == "head" for x in fleet["nodes"][1:]):
        die(f"{path}: NODE_0 must be the head and the only head")
    names = [x["name"] for x in fleet["nodes"]]
    if len(set(names)) != n:
        die(f"{path}: node names repeat")
    allips = [ip for x in fleet["nodes"] for ip in x["ips"]]
    if len(set(allips)) != len(allips):
        die(f"{path}: a fabric address is given to two nodes")
    return fleet


class Source:
    """A generation on a mounted drive, or on another node over ssh (HOST:PATH)."""

    def __init__(self, spec: str, key: str = ""):
        m = re.match(r"^([A-Za-z0-9._@-]+):(/.*)$", spec)
        self.remote, self.path = (m.group(1), m.group(2)) if m else ("", os.path.realpath(spec))
        # the fleet's shared key, when present: a freshly restored worker has no ~/.ssh/config naming it yet
        self.ssh = cmd("SSH", "ssh") + (["-i", key] if key and self.remote else []) + ["-o", "BatchMode=yes"]

    def rsync_root(self) -> str:
        return f"{self.remote}:{self.path}" if self.remote else self.path

    def read(self, rel: str) -> bytes | None:
        if self.remote:
            rc = subprocess.run(self.ssh + [self.remote, "cat", shlex.quote(self.path + "/" + rel)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            return rc.stdout if rc.returncode == 0 else None
        try:
            return open(os.path.join(self.path, rel), "rb").read()
        except OSError:
            return None

    def listdir(self, rel: str) -> list | None:
        if self.remote:
            rc = subprocess.run(self.ssh + [self.remote, "ls", "-A",
                                                     shlex.quote(self.path + "/" + rel)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            return rc.stdout.decode().split() if rc.returncode == 0 else None
        try:
            return os.listdir(os.path.join(self.path, rel))
        except OSError:
            return None

    def stream_argv(self, rel: str) -> list:
        if self.remote:
            return self.ssh + [self.remote, "cat", shlex.quote(self.path + "/" + rel)]
        return ["cat", os.path.join(self.path, rel)]


def detect_fabric() -> tuple:
    """(ifaces, hcas, ipv4s) of the ConnectX ports that are Up."""
    rc, out, _ = run_out(cmd("IBDEV2NETDEV", "ibdev2netdev"))
    ifs, hcas = [], []
    for line in out.splitlines():
        m = re.match(r"^(\S+) port \d+ ==> (\S+) \(Up\)", line.strip())
        if m:
            hcas.append(m.group(1))
            ifs.append(m.group(2))
    return ifs, hcas


def ssh_block(fleet: dict, me: dict) -> str:
    lines = ["# >>> openbeast-fleet (managed: scripts/backends/spark-restore.sh)"]
    for n in fleet["nodes"]:
        if n["index"] == me["index"]:
            continue
        for alias in [n["name"]] + n["ips"]:
            lines += [f"Host {alias}", f"  HostName {n['ips'][0] if alias == n['name'] else alias}",
                      f"  User {fleet['user']}", f"  IdentityFile ~/.ssh/{fleet['ssh_key']}",
                      "  StrictHostKeyChecking accept-new", ""]
    lines.append("# <<< openbeast-fleet")
    return "\n".join(lines) + "\n"


def merge_ssh_config(old: str, block: str) -> str:
    old = re.sub(r"(?s)# >>> openbeast-fleet.*?# <<< openbeast-fleet\n?", "", old)
    # Aliases another block (NVIDIA Sync's) already defines win: ssh takes the
    # first match, and Sync's entries are the ones it maintains.
    taken = set(re.findall(r"(?mi)^\s*Host\s+(\S+)", old))
    out, cur, keep = [], [], True
    for line in block.splitlines():
        m = re.match(r"^Host (\S+)$", line)
        if m:
            if cur and keep:
                out += cur
            cur, keep = [line], m.group(1) not in taken
        elif line.startswith("# "):
            if cur and keep:
                out += cur
            cur = []
            out.append(line)
        else:
            cur.append(line)
    if cur and keep:
        out += cur
    return (old.rstrip("\n") + "\n\n" if old.strip() else "") + "\n".join(out) + "\n"


def model_link_unit(head_ip: str, user: str) -> str:
    return f"""# Managed by scripts/backends/spark-restore.sh (worker). Forwards 127.0.0.1:8000 here to
# the head's loopback model API over the ConnectX link, so opencode on this worker reaches
# the cluster's model without the LAN, tailnet or internet. docs/SPARK_HYDRA.md
[Unit]
Description=Model API from the head (127.0.0.1:8000) over the ConnectX link, for opencode
After=network-online.target

[Service]
ExecStart=/usr/bin/ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:8000:127.0.0.1:8000 {user}@{head_ip}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""


def backup_inference_unit(src: Source, meta: dict) -> tuple:
    """(unit, how): the inference unit the backed-up head had enabled for boot, from the generation's own record:
    BACKUP.json's enabled_user_units, else the *.wants links kept in home/ (class node), else the system record's
    `systemctl --user list-unit-files`. GLM's unit, the pair's default, when none of them says."""
    rec = meta.get("enabled_user_units")
    how = "BACKUP.json"
    if rec is None:
        rec = src.listdir("home/.config/systemd/user/default.target.wants")
        how = "the backup's default.target.wants links"
    if rec is None:
        text = (src.read("system/systemd-user-units.txt") or b"").decode()
        rec = [ln.split()[0] for ln in text.splitlines() if len(ln.split()) >= 2 and ln.split()[1] == "enabled"]
        how = "the backup's system record"
    hits = [u for u in INFERENCE_UNITS if u in rec]
    if len(hits) == 1:
        return hits[0], how
    return GLM_UNIT, "the default (the backup records " + ("both" if hits else "neither") + ")"


def head_units_for(fleet: dict, unit: str) -> list:
    """The fleet's head units with the inference unit set to `unit` (one model is enabled, never both)."""
    out = [u for u in fleet["head_units"] if u not in INFERENCE_UNITS]
    if any(u in INFERENCE_UNITS for u in fleet["head_units"]):
        out.insert(min(1, len(out)), unit)
    return out


def node_files(src: Source, fleet: dict, me: dict, home: str, ifs: list, hcas: list) -> tuple:
    """[(path, new_text, why)], [warnings]. Per-node files, never copied from the source node."""
    out, warns = [], []
    head = fleet["nodes"][0]
    head_ip = head["ips"][0]
    n = len(fleet["nodes"])
    tmpl = (src.read("home/openbeast/scripts/backends/spark.env") or b"").decode()
    if not tmpl:
        try:
            tmpl = open(os.path.join(home, "openbeast/scripts/backends/spark.env.example")).read()
        except OSError:
            tmpl = ""
        warns.append("the backup has no spark.env: built from spark.env.example — check its pins")
    t = set_env_key(tmpl, "SPARK_NODE_IP", me["ips"][0])
    t = set_env_key(t, "SPARK_HEAD_IP", head_ip)
    if ifs:
        t = set_env_key(t, "SPARK_IFACE", ",".join(ifs))
        t = set_env_key(t, "SPARK_IB_HCA", ",".join(hcas))
    elif n > 1:
        warns.append("no ConnectX port is Up (ibdev2netdev): SPARK_IFACE / SPARK_IB_HCA kept from the source node — "
                     "cable and address the fabric, then re-run the restore (the cable may sit in the other port)")
    out.append((os.path.join(home, "openbeast/scripts/backends/spark.env"), t, "this node's fabric address, head address, Up ports"))
    if me["role"] == "head":
        c = (src.read("home/openbeast/scripts/backends/cluster.env") or b"").decode() or "CLUSTER_REPO=~/openbeast\n"
        nodes = ["local"] + [f"{fleet['user']}@{x['ips'][0]}" for x in fleet["nodes"][1:]]
        c = set_env_key(c, "CLUSTER_NODES", '"' + " ".join(nodes) + '"')
        c = set_env_key(c, "CLUSTER_NAME", fleet["name"])
        out.append((os.path.join(home, "openbeast/scripts/backends/cluster.env"), c, f"{n} node(s) in rank order"))
        ls = (src.read("home/" + LOCAL_SH) or b"").decode()
        if ls:
            if n == 2:
                w = fleet["nodes"][1]
                ls2 = set_env_key(set_env_key(ls, "WORKER", w["name"]), "FABRIC_PEER", w["ips"][0])
                out.append((os.path.join(home, LOCAL_SH), ls2, f"the DeepSeek recipe's peer: {w['name']} at {w['ips'][0]}"))
            else:
                warns.append(f"the DeepSeek recipe is a TWO-Spark (TP 2) recipe and this fleet has {n} node(s): "
                             "local.sh is restored as is; run it on a pair (docs §4.2)")
        gls = (src.read("home/" + GLM_LOCAL_SH) or b"").decode()
        if gls and env_value(gls, "WORKER") is not None:
            if n == 2:
                w = fleet["nodes"][1]
                peer = f"{fleet['user']}@{w['ips'][0]}"
                if env_value(gls, "WORKER") != peer:
                    out.append((os.path.join(home, GLM_LOCAL_SH), set_env_key(gls, "WORKER", peer),
                                f"the GLM recipe's rank 1: WORKER={peer}"))
            else:
                warns.append(f"the GLM recipe's local.sh names one WORKER and this fleet has {n} node(s): "
                             "restored as is (docs §4.2)")
        conf = os.path.join(home, "openbeast/openbeast.conf")
        conf_src = (src.read("home/openbeast/openbeast.conf") or b"").decode()
        if conf_src and env_value(conf_src, "TS_HOSTNAME") not in (None, me["name"]):
            out.append((conf, set_env_key(conf_src, "TS_HOSTNAME", me["name"]), f"TS_HOSTNAME={me['name']}"))
    elif fleet["model_link"]:
        out.append((os.path.join(home, ".config/systemd/user/opencode-model-link.service"),
                    model_link_unit(head_ip, fleet["user"]), f"model API forward to the head {head_ip}"))
    sshc = os.path.join(home, ".ssh/config")
    old = (src.read("home/.ssh/config") or b"").decode()
    try:
        old = open(sshc).read() or old
    except OSError:
        pass
    if n > 1:
        out.append((sshc, merge_ssh_config(old, ssh_block(fleet, me)), "ssh aliases for the other nodes over the fabric"))
    return out, warns


def write_atomic(path: str, text: str, mode: int | None = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            if open(path).read() == text:
                return
        except OSError:
            pass
        shutil.copy2(path, path + ".pre-restore")
        mode = stat.S_IMODE(os.stat(path).st_mode) if mode is None else mode
    tmp = path + ".restore-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def rewrite_compose(home: str, ref: str, iid: str) -> bool:
    f = os.path.join(home, "openbeast", "docker-compose.yml")
    try:
        text = open(f).read()
    except OSError:
        return False
    new = re.sub(r"(?m)^(\s*image:\s*)[\"']?" + re.escape(ref) + r"[\"']?\s*$", r"\g<1>" + iid, text)
    if new == text:
        return False
    if not os.path.exists(f + ".pre-restore"):
        shutil.copy2(f, f + ".pre-restore")
    write_atomic(f, new)
    return True


def cmd_restore(args) -> int:
    home = home_dir()
    fleet_path = args.fleet or os.path.join(BACKENDS, "fleet.env.example")
    fleet = parse_fleet(fleet_path)
    key = os.path.join(home, ".ssh", fleet["ssh_key"])
    src = Source(args.src, key if os.path.isfile(key) else "")
    raw = src.read("BACKUP.json")
    if raw is None:
        die(f"{args.src}: no BACKUP.json — point --from at a generation (…/openbeast-backup/<host>/<stamp> or …/latest)")
    meta = json.loads(raw.decode())
    if not 0 <= args.index < len(fleet["nodes"]):
        die(f"--index {args.index}: the fleet has nodes 0..{len(fleet['nodes']) - 1}")
    me = fleet["nodes"][args.index]
    role = me["role"]
    items_text = (src.read("ITEMS") or b"").decode()
    if not items_text:
        die("the generation has no ITEMS file (made by an older tool?)")
    tmp_items = os.path.join(home, ".spark-restore-items.tmp")
    rev = meta.get("serving_rev", "")
    wcd = meta.get("serving_wcd") or (f"worker-rank-cache-{rev[:8]}" if rev else "")
    with open(tmp_items, "w") as f:
        f.write(items_text)
    try:
        items, skipped = load_items(tmp_items, item_subst(rev, wcd))
    finally:
        os.unlink(tmp_items)
    rank = meta.get("rank_caches", True) and not args.skip_rank_caches
    rules = rsync_filter(items, "restore", role, rank_caches=rank)
    if args.skip_weights:
        rules = ["- /" + it.path for it in items if it.cls in ("weights", "rankcache")] + rules
    rsync = cmd("RSYNC", "rsync")
    ropts = ["-aHAX", "--numeric-ids", "--info=stats2,progress2"]
    if src.remote:
        ropts += ["-e", " ".join(map(shlex.quote, src.ssh)) + " -c aes128-gcm@openssh.com -o Compression=no"]

    print(f"== restore {meta.get('host')}/{meta.get('generation')} -> {hostname()}:{home} as node {me['index']} "
          f"'{me['name']}' ({role}) of {fleet['name']} ({len(fleet['nodes'])} node(s), {fleet_path})"
          + ("" if args.apply else "   [DRY RUN: nothing is written]"))
    problems = []
    arch = os.environ.get("UNAME_M") or os.uname().machine
    if arch != "aarch64":
        problems.append(f"this machine is {arch}, not aarch64 — is this a DGX Spark?")
    rc, gpu, _ = run_out(cmd("NVIDIA_SMI", "nvidia-smi") + ["--query-gpu=name", "--format=csv,noheader"])
    if "GB10" not in gpu:
        problems.append(f"GPU {gpu.strip() or 'none'} is not a GB10 (nvidia-smi)")
    uid = int(os.environ.get("SPARK_BACKUP_UID") or os.getuid())
    if meta.get("uid") is not None and meta["uid"] != uid:
        problems.append(f"this account is uid {uid}, the backup's is {meta['uid']}: rsync --numeric-ids would hand "
                        f"the files to another uid — create the user with the same uid (docs §3.3 step 2)")
    user = os.environ.get("SPARK_BACKUP_USER") or os.environ.get("USER", "")
    if meta.get("user") and user and meta["user"] != user:
        problems.append(f"this account is '{user}', the backup's is '{meta['user']}' (paths under /home/{meta['user']} are baked into units and venvs)")
    if home != meta.get("home", home) and os.environ.get("SPARK_RESTORE_HOME_CHECK", "1") != "0":
        problems.append(f"home is {home}, the backup's was {meta.get('home')}: units, venvs and local.sh hold absolute paths")
    if not docker_ok() and not args.skip_images:
        problems.append("docker is not reachable for this user (add it to the docker group, log in again)")
    ifs, hcas = detect_fabric()
    print(f"   fabric ports Up here: {', '.join(ifs) or 'none'}")
    for n, p in skipped:
        print(f"   (items line {n} skipped: {p})")

    print(f"\n-- 1. files for role {role}: rsync, never --delete (an overlay on a stock DGX OS)")
    for r in rules:
        print(f"     {r}")
    print("   " + " ".join(map(shlex.quote, rsync + ropts + ["--filter=. FILTER", src.rsync_root() + "/home/", home + "/"])))

    files, warns = node_files(src, fleet, me, home, ifs, hcas)
    print("\n-- 2. per-node files (written for THIS node, the old one kept as <file>.pre-restore)")
    for p, _, why in files:
        print(f"   {os.path.relpath(p, home)}: {why}")
    for w in warns:
        print(f"   !! {w}")

    imgs = json.loads((src.read("docker/images.json") or b"[]").decode())
    vols = json.loads((src.read("docker/volumes.json") or b"[]").decode())
    imgs = [e for e in imgs if e.get("roles") in ("all", role, "opt")]
    vols = [e for e in vols if e.get("roles") in ("all", role)]
    print("\n-- 3. docker images (zstd -dc | docker load; skipped when the ID is already here)")
    if args.skip_images:
        print("   skipped (--skip-images)")
    for e in ([] if args.skip_images else imgs):
        print(f"   {e['ref']}  {e['id'][:19]}" + ("  (compose pin; rewritten to the loaded ID if the digest does not resolve)" if e.get("compose_ref") else ""))
    for e in ([] if args.skip_images else vols):
        print(f"   volume {e['name']}  (only into an empty or new volume; --force-volumes replaces)")
    inf_unit, inf_how = backup_inference_unit(src, meta)
    units = [] if args.no_units else (head_units_for(fleet, inf_unit) if role == "head" else fleet["worker_units"])
    print(f"\n-- 4. systemctl --user daemon-reload; enable (NOT start): {' '.join(units) or 'none'}")
    if role == "head":
        print(f"   the inference unit is {inf_unit}, from {inf_how}")
    print("\n-- 5. NOT in the backup: recomputed offline after the restore (docs §3.5)")
    for p, how in rebuild_rows(items, role) or [("nothing", "")]:
        print(f"   {p}" + (f": {how}" if how else ""))
    print_manual_steps(fleet, me, meta, inf_unit)

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"   !! {p}")
    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to restore.")
        return 1 if problems else 0
    if problems:
        die("refusing to restore (above)")
    if not args.yes:
        if not sys.stdin.isatty():
            die("--apply without --yes needs a terminal to confirm")
        ans = input(f"\nRestore onto THIS machine as '{me['name']}'? Type the node name to continue: ")
        if ans.strip() != me["name"]:
            die("not confirmed; nothing was changed")

    filt = os.path.join(home, ".spark-restore-filter")
    with open(filt, "w") as f:
        f.write("\n".join(rules) + "\n")
    print("\n== 1. rsync", flush=True)
    rc = subprocess.call(rsync + ropts + [f"--filter=. {filt}", src.rsync_root() + "/home/", home + "/"])
    os.unlink(filt)
    if rc != 0:
        die(f"rsync failed (exit {rc}); re-running the restore resumes it")
    print("== 2. per-node files")
    files, _ = node_files(src, fleet, me, home, ifs, hcas)   # again: the rsync may have brought files they build on
    for p, text, why in files:
        mode = 0o600 if p.endswith(("openbeast.conf", "/.ssh/config")) else None
        write_atomic(p, text, mode)
        print(f"   wrote {os.path.relpath(p, home)}")
    docker = cmd("DOCKER", "docker")
    zstd = cmd("ZSTD", "zstd")
    if not args.skip_images:
        for e in imgs:
            if image_id(e["ref"]) == e["id"] or image_id(e["id"]) == e["id"]:
                print(f"== 3. {e['ref']}: already here")
            else:
                print(f"== 3. {e['ref']}: docker load", flush=True)
                p0 = subprocess.Popen(src.stream_argv("docker/" + e["file"]), stdout=subprocess.PIPE)
                p1 = subprocess.Popen(zstd + ["-dc"], stdin=p0.stdout, stdout=subprocess.PIPE)
                p0.stdout.close()
                p2 = subprocess.Popen(docker + ["load"], stdin=p1.stdout, stdout=subprocess.DEVNULL)
                p1.stdout.close()
                if p2.wait() or p1.wait() or p0.wait():
                    die(f"docker load of {e['file']} failed")
                if image_id(e["id"]) != e["id"]:
                    warn(f"{e['ref']}: loaded, but its ID is not {e['id'][:19]} (another image store kind?)")
            if e.get("compose_ref") and not image_id(e["compose_ref"]):
                if rewrite_compose(home, e["compose_ref"], e["id"]):
                    print(f"   docker-compose.yml: {e['compose_ref'].split('@')[0]} -> {e['id'][:19]} "
                          "(a loaded image has no RepoDigest; the original is docker-compose.yml.pre-restore)")
        helper = os.environ.get("SPARK_BACKUP_HELPER_IMAGE", "alpine:latest")
        for e in vols:
            rc, mp, _ = run_out(docker + ["volume", "inspect", "-f", "{{.Name}}", e["name"]])
            if rc == 0 and not args.force_volumes:
                rc2, ls, _ = run_out(docker + ["run", "--rm", "--network", "none", "-v", f"{e['name']}:/data:ro", helper, "ls", "-A", "/data"])
                if ls.strip():
                    warn(f"volume {e['name']} exists and is not empty: left as is (--force-volumes replaces it)")
                    continue
            run_out(docker + ["volume", "create", e["name"]], check=True)
            print(f"== 3. volume {e['name']}: import", flush=True)
            p0 = subprocess.Popen(src.stream_argv("docker/" + e["file"]), stdout=subprocess.PIPE)
            p1 = subprocess.Popen(zstd + ["-dc"], stdin=p0.stdout, stdout=subprocess.PIPE)
            p0.stdout.close()
            clear = "find /data -mindepth 1 -delete && " if args.force_volumes else ""
            p2 = subprocess.Popen(docker + ["run", "--rm", "-i", "--network", "none", "-v", f"{e['name']}:/data", helper,
                                            "sh", "-c", clear + "tar xf - -C /data"], stdin=p1.stdout)
            p1.stdout.close()
            if p2.wait() or p1.wait() or p0.wait():
                die(f"importing volume {e['name']} failed")
    print("== 4. systemd --user")
    sc = cmd("SYSTEMCTL", "systemctl") + ["--user"]
    if units:
        run_out(sc + ["daemon-reload"])
    for u in units:
        if os.path.exists(os.path.join(home, ".config/systemd/user", u)):
            rc, _, err = run_out(sc + ["enable", u])
            print(f"   enable {u}: {'ok' if rc == 0 else err.strip()}")
        else:
            print(f"   {u}: no unit file in ~/.config/systemd/user — skipped")
    print("\n== restored. Now the manual steps above (root, Tailscale, fabric), then the checks.")
    return 0


def print_manual_steps(fleet: dict, me: dict, meta: dict, inf_unit: str = GLM_UNIT) -> None:
    head = fleet["nodes"][0]
    print("\n-- 6. by hand, in this order (docs §3): root and identity come from the golden image and its identity step,")
    print("   never from this backup. After a golden-image restore the next two only CHECK (idempotent, offline):")
    print(f"   sudo scripts/backends/spark-root-setup.sh {me['name']} --guard --debs ~/offline-stage/debs [--no-tailscale]"
          "   # libnccl2 held, linger, Tailscale, spark-guard")
    print("   sudo scripts/backends/spark-fabric-mtu.sh   # MTU 9000 staged (the identity bundle carries it); reboot BOTH Sparks together")
    if me["role"] == "head":
        print("   scripts/backends/spark-restore.sh rebuild --apply   # DeepSeek's rank files, offline, ~35 min; BEFORE starting a model")
        print("   scripts/setup-tailscale.sh                  # tailscale serve: WebUI, beast-gate :8443, :8444-8446, :8889 (online)")
        model = "GLM-5.3-Flash, the default" if inf_unit == GLM_UNIT else "DeepSeek, the backup's selection"
        print(f"   systemctl --user start {inf_unit.removesuffix('.service')} openbeast"
              f"   # {model}; or ~/.local/bin/beast-spark up")
    else:
        print(f"   ssh {head['name']} true                    # from the head: confirm it reaches this worker over the fabric")
    print("   checks: scripts/backends/spark-cluster.sh check   (on the head)   ·   scripts/doctor.sh   ·   "
          "scripts/backends/conformance.sh")


# ── Offline rebuild of what the backup leaves out (docs §3.5) ────────────────

def rebuild_rows(items: list, role: str) -> list:
    """The `rebuild` lines a restore of this role leaves to be recomputed, with how and how long."""
    rows = []
    for it in items:
        if it.cls != "rebuild" or not it.for_role(role):
            continue
        p = it.path
        if "tensorfold-dsv41/" in p:
            how = ("head: spark-restore.sh rebuild (spark-deepseek-prepare-local.sh, offline): rank 0 file, "
                   "token map, served folder" + ("" if role == "head" else "; the head copies them here"))
        elif "worker-rank-cache" in p:
            how = "head: spark-restore.sh rebuild builds rank 1 (~15 min) and rsyncs it to the worker over the fabric"
        elif "tensorfold-glm53" in p:
            how = "recompiled by the GLM recipe on its first start (a few minutes, offline)"
        elif p.startswith("obvenv"):
            how = "python3 -m venv ~/obvenv, then the repo's test dependencies"
        else:
            how = it.note or "see the items file"
        rows.append((p, how))
    return rows


def cmd_rebuild(args) -> int:
    """Recompute, offline, the DeepSeek files a backup leaves out: on the head, after both nodes are restored."""
    home = home_dir()
    rev = serving_rev(home)
    wcd = serving_wcd(home, rev)
    hf = os.path.join(home, "openbeast-models", "deepseek-hf")
    kits = sorted(d for d in (os.listdir(os.path.join(hf, "tensorfold-dsv41"))
                              if os.path.isdir(os.path.join(hf, "tensorfold-dsv41")) else []) if d.startswith("kit-"))
    prep = os.environ.get("SPARK_PREPARE_LOCAL") or os.path.join(BACKENDS, "spark-deepseek-prepare-local.sh")
    print("== rebuild: DeepSeek's rank files, token map and served folder, from the checkpoint, OFFLINE"
          + ("" if args.apply else "   [DRY RUN: nothing is built]"))
    print("   GLM-5.3-Flash: nothing to rebuild (it loads its checkpoint directly); its CUDA kernels recompile on the"
          " first start (a few minutes, offline)")
    problems = []
    if not rev:
        problems.append(f"no DeepSeek MODEL_REVISION in ~/{LOCAL_SH}: is this the head, restored?")
    if not kits:
        problems.append("no kernel kit (openbeast-models/deepseek-hf/tensorfold-dsv41/kit-*/): restore it from the backup")
    elif not any(os.path.isdir(os.path.join(hf, "tensorfold-dsv41", k, ".git")) for k in kits):
        problems.append(f"the kernel kit {kits[-1]} has no .git: the offline prepare refuses it (it would git-fetch). "
                        "Restore it from the head's backup, .git included")
    rc, out, _ = run_out(cmd("DOCKER", "docker") + ["image", "ls", "--format", "{{.Repository}}:{{.Tag}}"])
    if not any(line.startswith("tensorfold-dsv41:") for line in out.splitlines()):
        problems.append("no tensorfold-dsv41 image here: run the restore's docker load first (the builders run in it, --network none)")
    sc = cmd("SYSTEMCTL", "systemctl") + ["--user", "is-active"]
    for u in INFERENCE_UNITS:
        if run_out(sc + [u])[1].strip() == "active":
            problems.append(f"{u} is running: stop it first (the rank-file builders need ~48 GB of the GB10's memory)")
    if not os.path.isfile(prep):
        problems.append(f"{prep}: not found")
    rank0 = os.path.join(hf, "tensorfold-dsv41", rev[:8], "rank-cache") if rev else ""
    have0 = bool(rank0) and os.path.isdir(rank0) and any(f.startswith("rank0of2-") and f.endswith(".bin") for f in os.listdir(rank0))
    d1 = os.path.join(hf, wcd) if wcd else ""
    have1 = bool(d1) and os.path.isdir(d1) and any(f.startswith("rank1of2-") and f.endswith(".bin") for f in os.listdir(d1))
    print(f"   serving pack: {rev[:8] or '?'}   kit: {kits[-1] if kits else 'none'}   rank 0 file: "
          f"{'present' if have0 else 'to build'}   rank 1 file ({wcd or '?'}): {'present' if have1 else 'to build'}")
    print("   time: about 15 min per rank file (~106 GB each, CPU, in the image with --network none), plus about 4 min to "
          "copy rank 1 to the worker over the fabric: about 35 min; seconds when both are present")
    print(f"   runs: bash {shlex.quote(prep)}   (its own checks: pinned runtime commit and prepare.sh sha256, the image's "
          "patch label, the worker's files; it never fetches)")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"   !! {p}")
    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to build.")
        return 1 if problems else 0
    if problems:
        die("refusing to rebuild (above)")
    rc = subprocess.call(["bash", prep])
    if rc != 0:
        die(f"{prep} failed (exit {rc}); it is safe to run again")
    print("== rebuilt. DeepSeek can be selected (beastds) with no network.")
    return 0


# ── The golden base image of DGX OS (docs §3.2) ───────────────────────────────

GOLDEN_DIR = "openbeast-golden"
IDENTITY_MEMBERS = (r"etc/machine-id", r"etc/hostname", r"etc/dgx-release",
                    r"etc/ssh/ssh_host_[a-z0-9]+_key(\.pub)?",
                    r"var/lib/tailscale(/[A-Za-z0-9._-]+)*",
                    r"etc/netplan/99-nvidia-sync-cluster\.yaml", r"etc/netplan/99-zz-openbeast-fabric-mtu\.yaml")
FABRIC_NETPLAN = ("etc/netplan/99-nvidia-sync-cluster.yaml", "etc/netplan/99-zz-openbeast-fabric-mtu.yaml")
DERIVED_STATE = ("var/lib/systemd/random-seed", "var/lib/NetworkManager/secret_key", "etc/docker/key.json",
                 "var/lib/docker/engine-id")
IDENTITY_CAPTURE = ("etc/machine-id etc/hostname etc/dgx-release etc/ssh/ssh_host_*_key etc/ssh/ssh_host_*_key.pub "
                    "var/lib/tailscale " + " ".join(FABRIC_NETPLAN))


def disk_facts() -> dict:
    """The live node's disk layout, read-only (lsblk without root)."""
    rc, out, _ = run_out(cmd("LSBLK", "lsblk") + ["-J", "-o", "PATH,PKNAME,SIZE,FSTYPE,LABEL,UUID,PARTUUID,MOUNTPOINTS"])
    facts = {}
    try:
        devs = json.loads(out).get("blockdevices", [])
    except ValueError:
        return facts
    flat = []

    def walk(ds):
        for d in ds:
            flat.append(d)
            walk(d.get("children") or [])
    walk(devs)
    for d in flat:
        mps = [m for m in (d.get("mountpoints") or []) if m]
        if "/" in mps:
            facts["root"] = d
        if "/boot/efi" in mps:
            facts["esp"] = d
    if "root" in facts:
        pk = facts["root"].get("pkname") or ""
        facts["disk"] = "/dev/" + pk if pk and not pk.startswith("/") else pk
    return facts


def cmd_golden(args) -> int:
    if not args.plan:
        die("golden-image has only --plan: the capture and the restore run as root from a live USB, by hand, "
            "with the commands the plan prints (docs §3.2)")
    drive = args.to.rstrip("/") or "/media/max/obbackup"
    facts = disk_facts()
    fleet = parse_fleet(args.fleet or os.path.join(BACKENDS, "fleet.env.example"))

    def rel(p):
        try:
            return open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            return ""
    dgx = rel(os.environ.get("SPARK_GOLDEN_DGX_RELEASE") or "/etc/dgx-release")
    ota = env_value(dgx, "DGX_OTA_VERSION") or env_value(dgx, "DGX_SWBUILD_VERSION") or "unknown"
    sb = run_out(cmd("MOKUTIL", "mokutil") + ["--sb-state"])[1].strip() or "unknown (mokutil)"
    host = hostname()
    disk = facts.get("disk") or "/dev/nvme0n1"
    esp, root = facts.get("esp", {}), facts.get("root", {})
    esp_dev, root_dev = esp.get("path") or disk + "p1", root.get("path") or disk + "p2"
    excl_rel = "openbeast/scripts/backends/data/spark-golden.exclude"
    name = f"dgxos-{ota}-{host}-{time.strftime('%Y%m%d', time.gmtime())}"
    gd = f"{GOLDEN_DIR}/{name}"
    problems = []
    if not root.get("uuid") or (root.get("fstype") or "") != "ext4":
        problems.append(f"root filesystem not found as ext4 by lsblk (got {root.get('fstype') or 'nothing'})")
    if not esp.get("uuid") or (esp.get("fstype") or "") != "vfat":
        problems.append("no vfat EFI system partition mounted at /boot/efi")
    if not os.path.isfile(os.path.join(BACKENDS, "data", "spark-golden.exclude")):
        problems.append("data/spark-golden.exclude is missing from this checkout")

    L = "/mnt/live-ob"      # where the drive is mounted inside the live session
    tar_c = ("tar --create --file=$G/root.tar --directory=/mnt/src --one-file-system --numeric-owner --xattrs "
             "--xattrs-include='*' --acls --sparse --anchored --exclude-from=$EXCL .")
    tar_x = ("tar --extract --file=$G/root.tar --directory=/mnt/target --numeric-owner --xattrs "
             "--xattrs-include='*' --acls --same-permissions")
    print(f"== golden base image of DGX OS: the PLAN (read-only; nothing here runs it)   node {host}")
    print(f"   DGX OS {ota} (dgx-release), Secure Boot: {sb}")
    print(f"   disk {disk}: ESP {esp_dev} vfat UUID {esp.get('uuid') or '?'} PARTUUID {esp.get('partuuid') or '?'};"
          f" root {root_dev} ext4 UUID {root.get('uuid') or '?'} PARTUUID {root.get('partuuid') or '?'}")
    print("   The ESP's grub.cfg finds root by that ext4 UUID and /etc/fstab mounts both by UUID: the restore re-creates")
    print("   both UUIDs, so nothing in the image is edited. The GPT's partition GUIDs come back from gpt.sfdisk, so the")
    print("   UEFI boot entry stays valid on this unit; on another unit shim's fallback (EFI/BOOT/BOOTAA64.EFI) adds one.")
    print(f"   image: {drive}/{gd}/   (root.tar ~20-30 GB, esp.img 0.3 GB, gpt.sfdisk, *.blkid, SHA256SUMS)")
    print("   left out (data/spark-golden.exclude): /home/*/* (the overlay), /var/lib/docker and containerd (images come "
          "from the overlay), /swap.img (re-made), caches, logs, and every per-node identity file")

    print("\n-- A. now, on EACH node, live (sudo, read-only on the node, writes to the drive): its identity bundle")
    print(f"   sudo mkdir -p {drive}/{GOLDEN_DIR}/identity && sudo chown {fleet['user']} {drive}/{GOLDEN_DIR}/identity")
    for n in fleet["nodes"]:
        out = f"{drive}/{GOLDEN_DIR}/identity/{n['name']}.tar"
        if n["role"] == "head":
            print(f"   [{n['name']}, the drive attached] sudo sh -c 'umask 077; cd / && tar --numeric-owner -cf {out} "
                  f"{IDENTITY_CAPTURE}'")
        else:
            tmp = f"/home/{fleet['user']}/.identity-{n['name']}.tar"
            print(f"   [{n['name']}] sudo sh -c 'umask 077; cd / && tar --numeric-owner -cf {tmp} {IDENTITY_CAPTURE} "
                  f"&& chown {fleet['user']}: {tmp}'")
            print(f"   [{fleet['nodes'][0]['name']}] scp {n['name']}:{os.path.basename(tmp)} {out} && "
                  f"ssh {n['name']} rm {os.path.basename(tmp)}")
    print("   these hold host keys and the Tailscale node key: the LUKS drive only, never the Mac or a stick")

    print("\n-- B. capture (planned outage, ~20 min; the pair stops serving): boot the Ubuntu 24.04 arm64 live USB")
    print("   on the source node (Esc/Del at power-on, Boot Override; 'Try Ubuntu'), open a terminal, then:")
    steps_b = [
        "sudo -i",
        "lsblk -o NAME,SIZE,FSTYPE,LABEL,UUID,MODEL,TRAN      # nvme0n1 = internal; sdX = the backup SSD",
        f"cryptsetup open /dev/sdX obbackup && mkdir -p {L} && mount /dev/mapper/obbackup {L}",
        f"mkdir -p /mnt/src && mount -o ro {root_dev} /mnt/src",
        f"EXCL={L}/openbeast-backup/{host}/latest/home/{excl_rel}",
        f"G={L}/{gd}.partial && mkdir -p $G && umask 077",
        f"sfdisk --dump {disk} > $G/gpt.sfdisk",
        f"blkid -o export {esp_dev} > $G/esp.blkid && blkid -o export {root_dev} > $G/root.blkid",
        f"dd if={esp_dev} of=$G/esp.img bs=4M conv=fsync status=progress",
        tar_c,
        "cp /mnt/src/etc/dgx-release /mnt/src/etc/os-release $G/ && cp /mnt/src/var/lib/dpkg/status $G/dpkg-status",
        "(cd $G && sha256sum gpt.sfdisk esp.img root.tar *.blkid > SHA256SUMS) && mv $G ${G%.partial}",
        f"umount /mnt/src && umount {L} && cryptsetup close obbackup && poweroff",
    ]
    for s in steps_b:
        print(f"   {s}")
    print("   checks: tar --list --file=root.tar | grep -c . (hundreds of thousands); no ./home/max/<anything>,")
    print("   no ./var/lib/docker, no ./etc/ssh/ssh_host_*, no ./var/lib/tailscale/<anything> in the list")

    print("\n-- C. restore onto a Spark (this one, the other, or a replacement), OFFLINE, from the same live USB:")
    steps_c = [
        "sudo -i",
        f"cryptsetup open /dev/sdX obbackup && mkdir -p {L} && mount /dev/mapper/obbackup {L}",
        f"G={L}/{gd} && (cd $G && sha256sum -c SHA256SUMS)",
        "#   ERASES the internal disk. Size-independent: p2 is re-made to fill the disk",
        f"sed -e '/^last-lba:/d' -e '/p2 : /s/size= *[0-9]*, *//' $G/gpt.sfdisk | sfdisk --wipe always --wipe-partitions always {disk}",
        f"dd if=$G/esp.img of={esp_dev} bs=4M conv=fsync status=progress",
        f"( . $G/root.blkid && mkfs.ext4 -F -U \"$UUID\" -L \"${{LABEL:-root}}\" {root_dev} )",
        f"mkdir -p /mnt/target && mount {root_dev} /mnt/target",
        tar_x,
        "fallocate -l 16G /mnt/target/swap.img && chmod 600 /mnt/target/swap.img && mkswap /mnt/target/swap.img",
        f"python3 {L}/openbeast-backup/{host}/latest/home/openbeast/scripts/backends/pylib/spark_backup.py identity \\",
        f"    --target /mnt/target --bundle {L}/{GOLDEN_DIR}/identity/<node>.tar          # dry run; then add --apply",
        "#   a NEW unit (no bundle): --fresh --hostname spark-XXXX (its factory name: the last 4 hex of its 10GbE MAC)",
        f"umount /mnt/target && umount {L} && cryptsetup close obbackup && reboot     # pull the live USB",
    ]
    for s in steps_c:
        print(f"   {s}")
    print("   first boot: no OOBE (the image is past it), the old login; then the overlay restore (docs §3.4)")
    print("   checks: mokutil --sb-state (enabled); cat /etc/machine-id differs between the two Sparks; "
          "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub; nvidia-smi; ibdev2netdev; docker info")

    print("\n-- D. firmware is NOT in the image (UEFI, EC, SSD; ConnectX-7 firmware IS: spark-mlnx-firmware-manager)")
    print("   compare fwupdmgr get-devices with the generation's system/fwupd-devices.txt; older on a replacement unit:")
    print("   NVIDIA's recovery USB (offline, flashes the firmware it carries) BEFORE step C, or fwupdmgr install <file>.cab")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"   !! {p}")
    return 1 if problems else 0


IDENTITY_PARENTS = ("", ".", "etc", "etc/ssh", "etc/netplan", "var", "var/lib")


def _ident_member(name: str, is_dir: bool = False) -> str:
    n = name[2:] if name.startswith("./") else name
    n = n.rstrip("/")
    if is_dir and n in IDENTITY_PARENTS:
        return n or "."
    for rx in IDENTITY_MEMBERS:
        if re.fullmatch(rx, n):
            return n
    return ""


def cmd_identity(args) -> int:
    """Write ONE node's identity into a restored golden image mounted at --target (from a live USB, as root)."""
    import tarfile
    import uuid
    t = os.path.realpath(args.target)
    problems = []
    if t == "/":
        die("--target is / : identity never touches the running system (mount the restored disk, e.g. /mnt/target)")
    for f in ("etc/os-release", "etc/dgx-release"):
        if not os.path.isfile(os.path.join(t, f)):
            problems.append(f"{t}/{f} missing: --target is not a restored DGX OS root")
    if bool(args.bundle) == bool(args.fresh):
        die("give exactly one of --bundle FILE (this node's own identity, captured live) or --fresh (a new unit)")
    if args.fresh and not args.hostname:
        die("--fresh needs --hostname (the unit's factory name, spark-XXXX)")
    if args.hostname and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", args.hostname):
        die(f"--hostname {args.hostname!r} is not a valid host name")
    files = {}
    if args.bundle:
        try:
            with tarfile.open(args.bundle) as tf:
                for m in tf.getmembers():
                    n = _ident_member(m.name, m.isdir())
                    if not n:
                        problems.append(f"bundle member {m.name!r} is not an identity file: refused")
                        continue
                    if m.isdir() and (n in IDENTITY_PARENTS or n == "."):
                        continue
                    if m.isdir():
                        files.setdefault(n, None)
                        continue
                    if not m.isfile():
                        problems.append(f"bundle member {m.name!r} is not a regular file: refused")
                        continue
                    files[n] = (tf.extractfile(m).read(), m.mode & 0o7777, m.uid, m.gid)
        except (OSError, tarfile.TarError) as e:
            die(f"cannot read the bundle {args.bundle}: {e}")
        mid = (files.get("etc/machine-id") or (b"",))[0].decode(errors="replace").strip()
        if not re.fullmatch(r"[0-9a-f]{32}", mid):
            problems.append("the bundle has no valid etc/machine-id")
        if not any(re.fullmatch(r"etc/ssh/ssh_host_[a-z0-9]+_key", k) for k in files):
            problems.append("the bundle has no ssh host keys")
    host = args.hostname or ((files.get("etc/hostname") or (b"",))[0].decode(errors="replace").strip())
    if not host:
        problems.append("no host name: the bundle has no etc/hostname; pass --hostname")
    serial = args.serial or (env_value((files.get("etc/dgx-release") or (b"",))[0].decode(errors="replace"),
                                       "DGX_SERIAL_NUMBER") or "")
    src = "the bundle " + os.path.basename(args.bundle) if args.bundle else "fresh"
    print(f"== identity for {host or '?'} into {t} (from {src})" + ("" if args.apply else "   [DRY RUN: nothing is written]"))
    plan = [
        ("etc/machine-id", "the bundle's" if args.bundle else "new, random (as systemd-machine-id-setup makes it)"),
        ("etc/ssh/ssh_host_*", "the bundle's (the Mac's known_hosts stays valid)" if args.bundle else "new: ssh-keygen -A -f TARGET"),
        ("var/lib/tailscale/", "the bundle's node state: no re-auth" if any(k.startswith("var/lib/tailscale/") for k in files)
         else "empty: Tailscale re-auth when online (spark-root-setup.sh)"),
        ("etc/netplan/ fabric files", "the bundle's (NVIDIA Sync's rail addresses, MTU 9000)" if any(k in files for k in FABRIC_NETPLAN)
         else "none: pair with NVIDIA Sync from the Mac (LAN), then spark-fabric-mtu.sh"),
        ("etc/hostname, etc/hosts 127.0.1.1", host or "?"),
        ("etc/dgx-release DGX_SERIAL_NUMBER", serial or "left as the image has it (the SOURCE unit's): pass --serial"),
        ("derived state removed", ", ".join(DERIVED_STATE)),
    ]
    for k, v in plan:
        print(f"   {k}: {v}")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"   !! {p}")
    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to write.")
        return 1 if problems else 0
    if problems:
        die("refusing to write the identity (above)")
    if os.geteuid() != 0 and os.environ.get("SPARK_IDENTITY_ROOT_CHECK", "1") != "0":
        die("run it as root (sudo -i in the live session): the files are root's")

    def put(relp, data, mode, uid=0, gid=0):
        p = os.path.join(t, relp)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".ob-new"
        with open(tmp, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        if os.geteuid() == 0:
            os.chown(tmp, uid, gid)
        os.replace(tmp, p)

    def rm(relp):
        p = os.path.join(t, relp)
        if os.path.islink(p) or os.path.isfile(p):
            os.unlink(p)
    # machine-id
    rm("etc/machine-id")
    if args.bundle:
        d, mode, uid, gid = files["etc/machine-id"]
        put("etc/machine-id", d, mode, uid, gid)
    else:
        put("etc/machine-id", (uuid.uuid4().hex + "\n").encode(), 0o444)
    dbus = os.path.join(t, "var/lib/dbus/machine-id")
    if not os.path.lexists(dbus):
        os.makedirs(os.path.dirname(dbus), exist_ok=True)
        os.symlink("/etc/machine-id", dbus)
    # ssh host keys
    sshd = os.path.join(t, "etc/ssh")
    if os.path.isdir(sshd):
        for f in os.listdir(sshd):
            if f.startswith("ssh_host_"):
                os.unlink(os.path.join(sshd, f))
    # tailscale and the fabric netplan
    tsd = os.path.join(t, "var/lib/tailscale")
    if os.path.isdir(tsd):
        shutil.rmtree(tsd)
    os.makedirs(tsd, mode=0o700, exist_ok=True)
    os.chmod(tsd, 0o700)
    for f in FABRIC_NETPLAN:
        rm(f)
    for relp, v in sorted(files.items()):
        if v is None or relp in ("etc/machine-id", "etc/hostname", "etc/dgx-release"):
            continue
        d, mode, uid, gid = v
        put(relp, d, mode, uid, gid)
    if not args.bundle:
        rc = subprocess.call(cmd("SSH_KEYGEN", "ssh-keygen") + ["-A", "-f", t])
        if rc != 0:
            die("ssh-keygen -A failed")
    # host name
    put("etc/hostname", (host + "\n").encode(), 0o644)
    hp = os.path.join(t, "etc/hosts")
    try:
        hosts = open(hp, encoding="utf-8").read().splitlines()
    except OSError:
        hosts = ["127.0.0.1\tlocalhost"]
    hosts = [f"127.0.1.1\t{host}" if re.match(r"^127\.0\.1\.1\s", h) else h for h in hosts]
    if not any(re.match(r"^127\.0\.1\.1\s", h) for h in hosts):
        hosts.append(f"127.0.1.1\t{host}")
    put("etc/hosts", ("\n".join(hosts) + "\n").encode(), 0o644)
    if serial:
        dp = os.path.join(t, "etc/dgx-release")
        txt = open(dp, encoding="utf-8").read()
        put("etc/dgx-release", set_env_key(txt, "DGX_SERIAL_NUMBER", f'"{serial}"').encode(), 0o644)
    for f in DERIVED_STATE:
        rm(f)
    print(f"== identity written: {host}. Unmount, pull the live USB, reboot.")
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="spark_backup.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inventory")
    p.add_argument("--role", choices=("head", "worker"))
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("run")
    p.add_argument("--to", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--no-rank-caches", action="store_true")
    p.add_argument("--config-only", action="store_true")
    p.add_argument("--no-images", action="store_true")
    p.add_argument("--with-optional-images", action="store_true")
    p.add_argument("--no-pause", action="store_true")
    p.add_argument("--no-hash", action="store_true")
    p.add_argument("--allow-unencrypted", action="store_true")
    p = sub.add_parser("verify")
    p.add_argument("gen")
    p.add_argument("--deep", action="store_true")
    p.add_argument("--against-live", action="store_true")
    p = sub.add_parser("list")
    p.add_argument("dir")
    p = sub.add_parser("restore")
    p.add_argument("--from", dest="src", required=True)
    p.add_argument("--fleet", default=None, help="default: fleet.env.example beside this tool (the pair)")
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--skip-weights", action="store_true")
    p.add_argument("--skip-rank-caches", action="store_true")
    p.add_argument("--skip-images", action="store_true")
    p.add_argument("--force-volumes", action="store_true")
    p.add_argument("--no-units", action="store_true")
    p = sub.add_parser("rebuild")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("golden-image")
    p.add_argument("--plan", action="store_true")
    p.add_argument("--to", default="/media/max/obbackup")
    p.add_argument("--fleet", default=None)
    p = sub.add_parser("identity")
    p.add_argument("--target", required=True)
    p.add_argument("--bundle")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--hostname")
    p.add_argument("--serial")
    p.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    return {"inventory": cmd_inventory, "run": cmd_run, "verify": cmd_verify, "list": cmd_list,
            "restore": cmd_restore, "rebuild": cmd_rebuild, "golden-image": cmd_golden,
            "identity": cmd_identity}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
