#!/usr/bin/env python3
"""Build the anonymized supplementary-material archive for the TMLR submission.

    nice -n 19 python3 build_supp.py          # writes build/supplementary/, build/supplementary.zip, MANIFEST.md

What goes in is decided by rule, not by a file list, so the archive can be
rebuilt while experiments are still writing:

* Items. Every artifact the paper cites: the `[source: ...]` markers in
  ../sections/*.md are parsed by build.py's own `sources()`/`tag_for()`, so the
  item set and the archive paths are exactly Appendix B's "Supplementary
  material index" (repo path with `research/lowrank/` stripped). Plus the
  research-side llama.cpp patches (`kernels/*.patch` and their README).
* Files. Inside each item, text files only (TEXT_EXT, TEXT_NAMES), at most
  MAX_FILE bytes, not under a cache/code-dump directory (SKIP_DIR), not a copy
  of a public text corpus (is_corpus), not a symlink. Every skipped file is
  counted in MANIFEST.md with its reason; skipped text files are listed one by
  one with their sha256.
* Anonymization. SCRUB below, applied to every file's text and to every
  archive path; it is the mapping (the mapping itself is never shipped).
  Commit hashes are scrubbed only when they are commits of the author's own
  repositories (OWN_REPOS; llama.cpp commits that are not on its upstream
  remote), so upstream build numbers (b10865) and upstream / third-party
  commit hashes survive.
* Gate. After building, every archived file and path is grepped
  (case-insensitive where the pattern says so) for latex_tmlr.FORBIDDEN plus
  EXTRA_FORBIDDEN, after masking what is allowed to stay (cited authors' names
  from refs.json, third-party GitHub organisations in ALLOWED_GITHUB). Any hit,
  a Python file that no longer compiles, a shell file that fails `bash -n`, or
  a total above LIMIT fails the build (exit 1).

CPU only; reads files, never runs anything from the experiments.
"""
import collections, hashlib, json, os, pathlib, re, shutil, subprocess, sys, zipfile

HERE = pathlib.Path(__file__).resolve().parent          # paper/final/supplementary
FINAL = HERE.parent                                     # paper/final
LOWRANK = FINAL.parent.parent                           # research/lowrank
REPO = LOWRANK.parent.parent                            # repository root
OUT = HERE / "build"
STAGE = OUT / "supplementary"
ZIP = OUT / "supplementary.zip"
sys.path.insert(0, str(FINAL))
import build, latex_tmlr                                # noqa: E402  (build.py's tag_for is the source of truth)

MAX_FILE = 5 * 1024 * 1024
LIMIT = 95 * 1000 * 1000
TEXT_EXT = {".py", ".sh", ".md", ".txt", ".log", ".json", ".csv", ".tsv", ".patch", ".diff"}
TEXT_NAMES = {"SHA256SUMS"}
SKIP_DIR = re.compile(r"^(codes.*|.*cache.*|__pycache__|\..*)$")
# Copies of public corpora (wikitext, FineWeb-Edu, code, HellaSwag) used as calibration / eval text.
CORPUS_NAME = re.compile(r"(?i)(calib|train|test|control|corpus|wiki|fineweb|hellaswag|mix\d)")
CORPUS_MIN = 100 * 1024
EXTRA_ITEMS = {"kernels": ("research/lowrank/kernels/", re.compile(r"\.(patch|diff)$|^README\.md$"))}

# ------------------------------------------------------------------ the scrub mapping
# (pattern, replacement) in order; replacement may be a function. Applied to text and paths.
AUTHOR_EMAILS = r"maxjkh@gmail\.com|maximilian\.khan@[\w.]+|[\w.+-]+@(?:alumni\.)?scu\.edu"
ROOTS = r"/home/max/Documents/openbeast(?:-research)?(?:/\.wt-[\w.-]+|/\.claude/worktrees/[\w.-]+)?"


def _author(m):
    """'Max' -> 'the author' ('The author' at a sentence start), keeping a possessive."""
    before = m.string[:m.start()]
    tail = before.split("\n")[-1].rstrip(" *_#>|-(\"`'")
    if re.search(r"(?i)\bthe $", before):          # "the Max debrief" -> "the author debrief"
        return "author" + ("'s" if m.group(0).endswith("'s") else "")
    cap = tail == "" or tail[-1] in ".!?"
    word = "The author" if cap else "the author"
    return word + ("'s" if m.group(0).endswith("'s") else "")


SCRUB = [
    # git format-patch author line
    (re.compile(r"(?m)^From: [^\n<]*<[^>\n]*>$"), "From: Anonymous Authors <anonymous@example.invalid>"),
    (re.compile(r"(?i)" + AUTHOR_EMAILS), "<email withheld>"),
    (re.compile(r"(?i)(?:https?://)?(?:www\.)?(?:github\.com|huggingface\.co|hf\.co)/(?:MaximilianKhan|maxjkh)\b[\w./#?=&%-]*"),
     "<repository withheld>"),
    (re.compile(r"(?i)MaximilianKhan|maxjkh"), "<author>"),
    # absolute paths
    # absolute paths: <ROOT> = research/lowrank = the archive root; <REPO> = the repository; <HOME> = home
    (re.compile(ROOTS + r"/research/lowrank\b"), "<ROOT>"),
    (re.compile(ROOTS), "<REPO>"),
    (re.compile(r"/home/max\b"), "<HOME>"),
    (re.compile(r"-home-max-"), "-home-user-"),
    (re.compile(r"(?<![\w~/.$])~/"), "<HOME>/"),
    (re.compile(r"(?:\$\{?\w+\}?/|<REPO>/|\b)research/lowrank\b"), "<ROOT>"),
    # the author's name
    (re.compile(r"\b(?:Maximilian|Max) Khan\b"), "the author"),
    (re.compile(r"\bMaximilian\b(?! Kleinegger)"), "the author"),
    (re.compile(r"\bKhan\b"), "the author"),
    (re.compile(r"\bMax(?:'s)?\b"), _author),
    (re.compile(r"\bMK\b"), "the author"),
    # project and tool names
    (re.compile(r"OPENBEAST"), "SERVESTACK"),
    (re.compile(r"OpenBeast"), "ServeStack"),
    (re.compile(r"(?i)openbeast"), "servestack"),
    (re.compile(r"BEAST[-_]?RANK"), "LRK"),
    (re.compile(r"(?i)beast[-_]?rank"), "lrk"),
    (re.compile(r"(?i)\bbeastup\b"), "stack-up"),
    (re.compile(r"(?i)\bbeastdown\b"), "stack-down"),
    (re.compile(r"BEAST_"), "STACK_"),
    (re.compile(r"(?i)\bbeast([-_])(?=\w)"), r"stack\1"),
    (re.compile(r"\bBEAST\b"), "RIG"),
    (re.compile(r"\bBeast\b"), "Rig"),
    (re.compile(r"\bbeast\b"), "rig"),                  # the workstation's tailnet / host name
    # machine identifiers
    (re.compile(r"(?i)\bmaximilians-[\w-]+"), "<host>"),
    (re.compile(r"(?i)\biphone\d+\b"), "<host>"),
    (re.compile(r"(?i)\b[\w-]+\.tail[0-9a-f]+\.ts\.net\b|\b[\w-]+\.ts\.net\b"), "<host>"),
    (re.compile(r"(?i)\bomarchy\b"), "<host>"),
    (re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"), "<tailnet-ip>"),
    (re.compile(r"(?i)\bGPU-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "GPU-<uuid>"),
    (re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b"), "<mac>"),
    # references into the author's repository that the archive does not contain
    (re.compile(r"(?:\.\./)*\bdocs/[A-Za-z0-9_-]+\.md\b"), "<project notes, not included>"),
    (re.compile(r"\bAGENTS\.md\b"), "<project notes, not included>"),
    (re.compile(r"\beval-[a-z0-9.-]+?-\d{8}-\d{6}\.json\b"), "<eval result file>"),
    (re.compile(r"\beval-[a-z0-9.-]+-\*\.json"), "<eval result file>"),
]
HEX = re.compile(r"(?<![0-9A-Za-z_])[0-9a-f]{7,40}(?![0-9A-Za-z_])")

# ------------------------------------------------------------------ the gate
EXTRA_FORBIDDEN = [r"(?i)maximilian", r"(?i)maxjkh", r"(?i)scu\.edu", r"(?i)passboards", r"/home/max", r"home-max",
                   r"(?i)maximilians-", r"(?i)iphone\d", r"(?i)omarchy", r"(?i)\.ts\.net\b", r"(?i)tailnet-name",
                   r"\bMK\b", r"(?i)@gmail\.com", r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b",
                   # PR numbers of the author's own repository (upstream llama.cpp numbers are 4-5 digits)
                   r"(?<![\w&/])#(?:22|35|45|56|61|62)\b",
                   r"\bdocs/[A-Za-z0-9_-]+\.md\b", r"\bAGENTS\.md\b", r"\beval-[a-z0-9.-]+-\d{8}-\d{6}\.json\b",
                   r"(?i)\bthe author directive\b", r"(?i)\bthe author-ordered\b", r"\b[Hh]is\b"]
# Third-party GitHub owners that may stay (upstream llama.cpp, model vendors, cited code). An owner not
# listed here fails the gate, so a new link is looked at once and then added (or scrubbed).
ALLOWED_GITHUB = {"ggml-org", "ggerganov", "huggingface", "mistralai", "LostRuins", "meta-llama", "IST-DASLab",
                  "ivanyu", "cmp-nct", "JmlrOrg", "Dao-AILab", "NVIDIA", "openai", "google", "facebookresearch",
                  "pytorch", "vllm-project", "QwenLM", "deepseek-ai", "microsoft", "ikawrakow", "turboderp",
                  "turboderp-org", "Cornell-RelaxML", "mit-han-lab", "yaqa", "spcl", "Vahe1994", "SqueezeAILab",
                  "unslothai", "ModelCloud", "intel", "AutoGPTQ", "casper-hansen", "TimDettmers", "bitsandbytes-foundation"}


def own_hashes():
    """Full SHAs of commits in the author's repositories: every ref + reflog of the repo, and the
    local llama.cpp tree's commits that its upstream remote does not have."""
    out = set()
    def git(*a):
        r = subprocess.run(["git", *a], capture_output=True, text=True)
        return r.stdout.split() if r.returncode == 0 else []
    out.update(git("-C", str(REPO), "rev-list", "--all"))
    out.update(git("-C", str(REPO), "log", "-g", "--all", "--format=%H"))
    lc = REPO / "llama.cpp"
    if (lc / ".git").exists():
        out.update(git("-C", str(lc), "rev-list", "--all", "--not", "--remotes"))
        out.update(h for h in git("-C", str(lc), "log", "-g", "--all", "--format=%H")
                   if not git("-C", str(lc), "branch", "-r", "--contains", h)[:1])
    for p in (LOWRANK / "kernels").glob("*.patch"):        # format-patch "From <sha>" lines
        out.update(re.findall(r"(?m)^From ([0-9a-f]{40}) ", p.read_text(errors="replace")))
    return {h for h in out if re.fullmatch(r"[0-9a-f]{40}", h)}


OWN = set()
OWN_PREFIX = collections.defaultdict(set)


def is_own(tok):
    return any(h.startswith(tok) for h in OWN_PREFIX.get(tok[:7], ()))


def scrub(text, counts=None):
    for pat, rep in SCRUB:
        text, n = pat.subn(rep, text)
        if counts is not None and n:
            counts[pat.pattern] += n
    def hexrep(m):
        tok = m.group(0)
        if re.search(r"[a-f]", tok) and re.search(r"\d", tok) and is_own(tok):
            if counts is not None:
                counts["<own commit hash>"] += 1
            return "<commit>"
        return tok
    return HEX.sub(hexrep, text)


# Shell files: <ROOT> would parse as a redirection, so placeholders become variables there.
SHELL_VARS = [("<ROOT>", "${SUPP_ROOT}"), ("<REPO>", "${SUPP_REPO}"), ("<HOME>", "${HOME}")]


def scrub_file(text, suffix, counts):
    text = scrub(text, counts)
    if suffix == ".sh":
        for a, b in SHELL_VARS:
            text = text.replace(a, b)
    return text


# ------------------------------------------------------------------ targeted archive edits
# Line-level rewording applied to the ARCHIVE COPY only (after SCRUB, so `old` is the scrubbed text);
# the originals are never touched. (archive path) -> [(old, new, expected occurrences)]. The build
# fails if an `old` text does not occur exactly that many times, so the table cannot silently rot.
# Covers: persona prose (the author directing agents), PR numbers of the author's own repository
# (#22 #35 #45 #56 #61 #62; upstream llama.cpp numbers such as #28068 or #25489 stay), and one
# description of a corpus built from the author's own source tree.
ARCHIVE_EDITS = {
    "PROTOCOL.md": [
        ("(the author directive 2026-08-04d)", "(decided by the author, 2026-08-04d)", 1)],
    "experiments/36-submission/PLAN.md": [
        ('The author, 2026-10-15: "take the GPU and finish what we need to complete a formal\n'
         'submission of our research." This experiment',
         "The author asked (2026-10-15) for the GPU runs a formal submission needs.\nThis experiment", 1)],
    "JOURNAL.md": [
        ("Late night — the author extends the session 4 hours:", "Late night — the session is extended by 4 hours:", 1),
        ('(the author: "keep going, 4 more hours, trail-blaze")', "(extended by 4 more hours)", 1),
        ("(recorded in the author debrief)", "(recorded in the session debrief)", 1),
        ("(8-hour night campaign, the author asleep, full autonomy)", "(8-hour night campaign, unattended, full autonomy)", 1),
        ("The author's charges: continue the cycle;", "Goals set by the author: continue the cycle;", 1),
        ("(verify with the author in daylight)", "(to be verified the next day)", 1),
        ("BLOCK 4 (the author back, 4-hour clock, direction Q&A)", "BLOCK 4 (4-hour clock, direction Q&A)", 1),
        ("(his dream = pure factorization)", "(the author's original goal = pure factorization)", 1),
        ("his factors-at-runtime insight", "the author's factors-at-runtime idea", 1),
        ("REFERENCE arrived (the author) and validated", "REFERENCE arrived and validated", 1),
        ("(the author directive):", "(a decision by the author):", 2),
        ("**The author's diligence directive**", "**A diligence rule set by the author**", 1),
        ("ADVERSARIAL REVIEW (the author-ordered, 3 agents)", "ADVERSARIAL REVIEW (ordered by the author, 3 agents)", 1),
        ("incl. the author's own confessed held-out\nconfound", "incl. a held-out confound the author had\nreported", 1),
        ("(post-recon; the author ran stack-down 15:05)", "(post-recon; serving stack stopped 15:05)", 1),
        ("The author directive (this session):", "A decision by the author (this session):", 1),
        ("DOCTRINE (the author):", "DOCTRINE (a decision by the author):", 1),
        ('CAMPAIGN RELAUNCHED (the author: "go, launch the E32 chain\n'
         '### and all our remaining tasks to complete in order")',
         "CAMPAIGN RELAUNCHED (by the author: the E32 chain\n### and the remaining tasks, in order)", 1),
        ("The author: button the research up and publish with the state of things as they",
         "The author asked for the research to be written up and published with the state of things as they", 1),
        ("campaign paused by\n  The author the same evening", "campaign paused by\n  the author the same evening", 1),
        ("The author: take the GPU and finish what a formal submission needs.",
         "The author asked for the GPU runs a formal submission needs.", 1),
        ("B waits for the author's go to restart", "B waits for the author's approval to restart", 1),
        ("(the author's go)", "(approved by the author)", 1),
        ("Cache: PR #35's zero-token guard", "Cache: <PR>'s zero-token guard", 1),
        ("FULLY LIVE — the #56\npull", "FULLY LIVE — the <PR>\npull", 1)],
    "experiments/25-alloc-concurrency/REPORT.md": [
        ("Host caveat: the author's CPU jobs ran throughout", "Host caveat: unrelated CPU jobs ran throughout", 1),
        ("(the author's call, per <project notes, not included>)", "(a decision for the author)", 1)],
    "experiments/10-full-covariance/README.md": [
        ("<project notes, not included> honored — private-checkout work, any\n"
         "upstream version goes through the author with an RFC first",
         "project policy honored — private-checkout work, any\n"
         "upstream version needs the author's approval and an RFC first", 1)],
    "prior-art/MANIFOLD-CANDIDATES.md": [
        ("(the author directive\n# 2026-08-04b,", "(decided by the author,\n# 2026-08-04b,", 1)],
    "experiments/15-iq1-carrier/README.md": [
        ("the author's dream in servable form", "the original pure-factorization goal in servable form", 1)],
    "experiments/17-task-conditioned/README.md": [
        ("Corpora from our own repos (servestack + llama.cpp sources)",
         "Corpora from source code (the authors' own project and llama.cpp sources)", 1)],
    "paper/DEPLOYABLE-WINS.md": [
        ("Saved 2026-08-04 per the author.", "Saved 2026-08-04 at the author's request.", 1)],
    "review/verification-2026-09-08-rco-and-kernels.md": [
        ("# (The author's directive, 2026-09-08)", "# (requested by the author, 2026-09-08)", 1)],
    "review/adversarial-round2-paper.md": [
        ('§4.4: "**the author\'s** founding pure-factorization dream in\n'
         'its last servable form" — a personal name in the results section.',
         '§4.4: "**[the author\'s name]\'s** founding pure-factorization dream in\n'
         'its last servable form" — the author\'s name in the results section.', 1),
        ('De-anonymize-proof: "the author\'s" out,', "De-anonymize-proof: the author's name out,", 1)],
    "experiments/31-seeded-basis-duel/PREREG.md": [
        ("is pruned (PR #22),", "is pruned (<PR>),", 1)],
    "experiments/32-t117-gsq-head-to-head/README.md": [
        ("(qwen family policy, PR #45)", "(qwen family policy, <PR>)", 1)],
    "experiments/32-t117-gsq-head-to-head/capability-verdict-iq3.txt": [
        ("#61/#62", "<PR>", 2),
        ("unconditionally. #62 proved", "unconditionally. <PR> proved", 1)],
    "kernels/README.md": [
        ("[`<project notes, not included>`](<project notes, not included>)", "<project notes, not included>", 1)],
}
EDITS_DONE = collections.Counter()


def archive_edit(rel, text, problems):
    for old, new, n in ARCHIVE_EDITS.get(rel, ()):
        found = text.count(old)
        if found != n:
            problems.append(f"ARCHIVE_EDITS {rel}: expected {n} occurrence(s) of {old[:60]!r}, found {found}")
            continue
        text = text.replace(old, new)
        EDITS_DONE[rel] += 1
    return text


def scrub_path(rel):
    return "/".join(scrub(part) for part in rel.split("/"))


# ------------------------------------------------------------------ items
def cited_items():
    """tag -> (repo path, archive path, [section labels]) exactly as build.py / Appendix B derive them."""
    where = collections.defaultdict(list)
    for p in sorted((FINAL / "sections").glob("*.md")):
        label = p.stem
        for line in p.read_text().splitlines():
            h = re.match(r"^#+\s+(?:(\d+(?:\.\d+)*[a-z]?|[A-Z]\.\d+)\.?\s|(Appendix [A-Z])|(.+))", line)
            if h:
                label = "§" + h.group(1) if h.group(1) else (h.group(2) or h.group(3).strip())
            for t in re.findall(r"\x05([^\x06]*)\x06", build.sources(line)):
                for tag in t.split(", "):
                    if label not in where[tag]:
                        where[tag].append(label)
    items = {}
    for t, path in build.tags.items():
        items[t] = (path, path.removeprefix(latex_tmlr.SUPP_PREFIX), where.get(t, []))
    return items


def is_corpus(p, size):
    return p.suffix == ".txt" and size >= CORPUS_MIN and (CORPUS_NAME.search(p.name) or p.parent.name == "calib")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def collect(src, keep=None):
    """Yield (path, reason or None) for every file under src (a file or a directory)."""
    if src.is_file():
        yield src, None
        return
    for root, dirs, files in os.walk(src, followlinks=False):
        rootp = pathlib.Path(root)
        for d in sorted(dirs):
            if SKIP_DIR.match(d) or (rootp / d).is_symlink():
                yield rootp / d, "skipdir"
        dirs[:] = sorted(d for d in dirs if not SKIP_DIR.match(d) and not (rootp / d).is_symlink())
        for f in sorted(files):
            p = rootp / f
            if keep is not None and not keep.search(f):
                continue
            if p.is_symlink():
                yield p, "symlink"
            elif p.suffix not in TEXT_EXT and f not in TEXT_NAMES:
                yield p, "type"
            else:
                yield p, None


def dir_stats(d):
    n = s = 0
    for root, _, files in os.walk(d, followlinks=False):
        for f in files:
            try:
                s += os.lstat(os.path.join(root, f)).st_size
                n += 1
            except OSError:
                pass
    return n, s


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1000 or u == "GB":
            return f"{n:.1f} {u}" if u != "B" else f"{n} B"
        n /= 1000


# Where each excluded corpus copy comes from and which included script rebuilds it (archive paths).
# Matched against the archive path; an excluded corpus with no entry is listed as "provenance: see item".
CORPUS_SOURCES = [
    (r"17-task-conditioned/code-(train|test|calib)\.txt$",
     "source code of the authors' own project and of llama.cpp (identifying, so not shipped); no build script "
     "in the archive. Construction: `experiments/17-task-conditioned/README.md`; its uses and hashes: "
     "`experiments/36-submission/d08b/README.md`, `experiments/36-submission/d08b/calib/SHA256SUMS`"),
    (r"32-t117-gsq-head-to-head/fineweb-edu-control\.txt$",
     "FineWeb-Edu sample-10BT rows 0..399 joined by blank lines (HF datasets-server); "
     "`experiments/36-submission/d08b/calib/fetch_fineweb_calib.py` re-pulls these rows and refuses unless "
     "they equal this file; pull described in `experiments/32-t117-gsq-head-to-head/fineweb_control.sh`"),
    (r"d08b/calib/fineweb-edu-calib\.txt$",
     "FineWeb-Edu sample-10BT rows from 2000 up; rebuilt by `experiments/36-submission/d08b/calib/fetch_fineweb_calib.py` "
     "(row list: `fineweb-edu-calib.manifest.tsv`), checked by `build_calib.py` against `SHA256SUMS`"),
    (r"d08b/calib/(mix111|wiki-train-third-[abc])\.txt$",
     "derived from wikitext-2 train (+ code-train, FineWeb-Edu calib for mix111); rebuilt and sha256-checked by "
     "`experiments/36-submission/d08b/calib/build_calib.py` against `SHA256SUMS`"),
]


def corpus_source(rel):
    for pat, d in CORPUS_SOURCES:
        if re.search(pat, rel):
            return d
    return "provenance: see the item's README"


def title_of(d):
    for name in ("README.md", "REPORT.md", "PLAN.md", "PREREG.md", "RESULTS.md"):
        p = d / name
        if p.is_file():
            for line in p.read_text(errors="replace").splitlines():
                if line.startswith("#"):
                    return scrub(line.lstrip("# ").strip())
    return ""


# ------------------------------------------------------------------ main
def main():
    OWN.update(own_hashes())
    for h in OWN:
        OWN_PREFIX[h[:7]].add(h)
    items = cited_items()
    items["kernels"] = (EXTRA_ITEMS["kernels"][0], "kernels/", ["§2, §3, §6 (the patched llama-imatrix and kernels; not tagged)"])
    if OUT.exists():
        shutil.rmtree(OUT)
    STAGE.mkdir(parents=True)
    counts = collections.Counter()
    excluded = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))   # item -> reason -> [n, bytes]
    excluded_text = []                                   # (archive path, size, sha256, reason)
    shipped = collections.defaultdict(lambda: [0, 0])
    problems = []
    for tag, (repo_path, arch, _) in items.items():
        src = REPO / repo_path
        if not src.exists():
            problems.append(f"cited item {tag}: {repo_path} does not exist")
            continue
        keep = EXTRA_ITEMS[tag][1] if tag in EXTRA_ITEMS else None
        base = src if src.is_dir() else src.parent
        arch_base = arch if src.is_dir() else arch.rsplit("/", 1)[0] + "/" if "/" in arch else ""
        for p, reason in collect(src, keep):
            rel = arch_base + str(p.relative_to(base)) if src.is_dir() else arch
            if reason == "skipdir":
                n, s = dir_stats(p)
                excluded[tag][f"cache / code-dump directory `{p.name}/`"][0] += n
                excluded[tag][f"cache / code-dump directory `{p.name}/`"][1] += s
                continue
            try:
                size = p.lstat().st_size
            except OSError:
                continue
            if reason == "symlink":
                excluded[tag]["symlink (not followed)"][0] += 1
                continue
            if reason == "type":
                key = f"binary or non-text type `{p.suffix or p.name}`"
                excluded[tag][key][0] += 1
                excluded[tag][key][1] += size
                continue
            if size > MAX_FILE:
                excluded_text.append((scrub_path(rel), size, sha256(p), "larger than 5 MB"))
                excluded[tag]["text file > 5 MB"][0] += 1
                excluded[tag]["text file > 5 MB"][1] += size
                continue
            if is_corpus(p, size):
                why = ("corpus built from the authors' own source code" if p.name.startswith("code-")
                       else "copy of a public text corpus")
                excluded_text.append((scrub_path(rel), size, sha256(p), why))
                excluded[tag][why][0] += 1
                excluded[tag][why][1] += size
                continue
            raw = p.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                excluded[tag]["not UTF-8 text"][0] += 1
                excluded[tag]["not UTF-8 text"][1] += size
                continue
            arel = scrub_path(rel)
            out = STAGE / arel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(archive_edit(arel, scrub_file(text, p.suffix, counts), problems))
            shipped[tag][0] += 1
            shipped[tag][1] += out.stat().st_size
    for rel in ARCHIVE_EDITS:
        if rel not in EDITS_DONE and not any(rel in q for q in problems):
            problems.append(f"ARCHIVE_EDITS {rel}: file not in the archive")
    write_manifest(items, shipped, excluded, excluded_text)
    shutil.copy(HERE / "MANIFEST.md", STAGE / "MANIFEST.md")
    rc = gate(problems)
    make_zip()
    total = sum(f.stat().st_size for f in STAGE.rglob("*") if f.is_file())
    nfiles = sum(1 for f in STAGE.rglob("*") if f.is_file())
    zsize = ZIP.stat().st_size
    print(f"\nstaged: {nfiles} files, {human(total)} uncompressed; {ZIP.relative_to(HERE)}: {human(zsize)}")
    if max(total, zsize) > LIMIT:
        print(f"FAIL: above the {human(LIMIT)} limit")
        rc = 1
    print(f"archive edits applied: {sum(EDITS_DONE.values())} of "
          f"{sum(len(v) for v in ARCHIVE_EDITS.values())} table rows, in {len(EDITS_DONE)} files")
    print("\nscrub replacements by rule:")
    for k, v in counts.most_common():
        print(f"  {v:6d}  {k}")
    print("\nGATE:", "PASS" if rc == 0 else "FAIL")
    sys.exit(rc)


def gate(problems):
    pats = [re.compile(p) for p in latex_tmlr.FORBIDDEN + EXTRA_FORBIDDEN]
    cited = sorted({a for e in json.loads((FINAL / "refs.json").read_text()) for a in e["authors"]}, key=len, reverse=True)
    cited_re = re.compile("|".join(r"\s+".join(map(re.escape, n.split())) for n in cited))
    gh = re.compile(r"(?i)github\s*\.\s*com/([\w.-]+)")
    hits = []
    for f in sorted(STAGE.rglob("*")):
        rel = str(f.relative_to(STAGE))
        for pat in pats:
            if pat.search(rel):
                hits.append(f"{rel}: path matches {pat.pattern}")
        if not f.is_file():
            continue
        text = f.read_text()
        masked = cited_re.sub("", text)
        masked = gh.sub(lambda m: "<gh>" if m.group(1) in ALLOWED_GITHUB else m.group(0), masked)
        for n, line in enumerate(masked.splitlines(), 1):
            for pat in pats:
                for m in pat.finditer(line):
                    hits.append(f"{rel}:{n}: {m.group(0)!r} ({pat.pattern})  | {line.strip()[:140]}")
            for m in HEX.finditer(line):
                if is_own(m.group(0)) and re.search(r"[a-f]", m.group(0)) and re.search(r"\d", m.group(0)):
                    hits.append(f"{rel}:{n}: own commit hash {m.group(0)}")
        if f.suffix == ".py":
            try:
                compile(text, rel, "exec")
            except SyntaxError as e:
                problems.append(f"{rel}: does not compile after scrub ({e.msg}, line {e.lineno})")
        elif f.suffix == ".sh":
            r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
            if r.returncode:
                problems.append(f"{rel}: bash -n failed: {r.stderr.strip()[:200]}")
    for h in hits:
        print("FORBIDDEN", h)
    for p in problems:
        print("PROBLEM", p)
    print(f"gate: {len(hits)} forbidden hit(s), {len(problems)} problem(s)")
    return 1 if hits or problems else 0


def make_zip():
    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(STAGE.rglob("*")):
            if f.is_file():
                info = zipfile.ZipInfo(str(f.relative_to(OUT)), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, f.read_bytes())


DESCRIBE = {
    "journal": "the timestamped experiment log (pre-registrations, decisions, incidents), in order of writing",
    "protocol": "the measurement protocol (methodology v2) the experiments follow",
    "rollup": "consolidated results across experiments, with the corrections log",
    "ablation": "the missing-runs matrix the final experiments were planned from",
    "theory-L6": "theory note: family subsumption (L6)",
    "theory-alt": "theory note: convergence of the alternation",
    "wins": "the deployable-results summary, claims calibrated to the reviews",
    "manifolds": "literature note: manifold / information-geometry candidates (authored notes on cited work)",
    "review": "the adversarial reviews of claims, code, statistics and the paper, and the coherence audit",
    "todo": "the research task list",
    "kernels": "the research-side llama.cpp changes as `git format-patch` files against upstream llama.cpp "
               "(correction kernels, re-rounding support, Gram/gradient capture), with their README",
}


def write_manifest(items, shipped, excluded, excluded_text):
    key = lambda t: (0, int(re.sub(r"\D", "", t)), t) if re.match(r"E\d", t) else (1, 0, t)
    L = ["# Supplementary material: manifest", "",
         "Each top-level item below is one entry of the paper's Appendix B (\"Supplementary material index\"); "
         "a bracketed tag such as [Supp. E24] in the paper names the item. Paths are relative to the root of "
         "this archive. Paths in the files are written `<ROOT>/...` for the root of this archive (the directory the "
         "experiments ran in), `<REPO>/...` for the repository that held it (the llama.cpp build lives there) and "
         "`<HOME>/...` for the user's home directory; in shell scripts the same three are `${SUPP_ROOT}`, "
         "`${SUPP_REPO}` and `${HOME}`, so the scripts still parse. Names of the authors, their accounts, machines and own repositories, and "
         "commit hashes of their own repositories (`<commit>`), have been replaced mechanically. Upstream "
         "llama.cpp build numbers (for example b10865) and third-party commit hashes are unchanged.", "",
         "Only text is included: scripts, logs, results tables and notes. Model weights (GGUF), cached logits, "
         "Gram / covariance matrices (`.npy`, `.npz`), dumped quantization codes and copies of public corpora "
         "are not, either because of their size (tens of GB per experiment) or because they are third-party data "
         "that the included scripts regenerate; the counts are listed per item. The text corpora used for "
         "calibration and evaluation are public (wikitext-2, FineWeb-Edu, HellaSwag) except the code corpus of E17, "
         "which was cut from the authors' own source tree and is withheld; the excluded copies are listed with "
         "their sha256 and the included script that rebuilds and checks each one. The shared data "
         "directory the scripts read from (`<ROOT>/data/`: reference logits, Gram captures, imatrix files, corpora) "
         "is not cited as an item and is not included.", "",
         "## Items", "", "| item | location | cited in | what it is | files shipped |", "|---|---|---|---|---|"]
    for t in sorted(items, key=key):
        repo_path, arch, where = items[t]
        src = REPO / repo_path
        desc = DESCRIBE.get(t) or (title_of(STAGE / scrub_path(arch)) if src.is_dir() else "")
        n, s = shipped.get(t, (0, 0))
        L.append(f"| {t} | `{scrub_path(arch)}` | {', '.join(where) or '-'} | {desc.replace('|', '/')} | {n} ({human(s)}) |")
    L += ["", "## Excluded, by item and reason", "", "| item | reason | files | size |", "|---|---|---|---|"]
    for t in sorted(excluded, key=key):
        for reason, (n, s) in sorted(excluded[t].items()):
            L.append(f"| {t} | {reason} | {n} | {human(s) if s else '-'} |")
    L += ["", "## Excluded text files", "",
          "Each is listed with its sha256 and, for corpora, where it comes from and which included script "
          "rebuilds and checks it.", "",
          "| file | size | sha256 | reason | source / rebuild |", "|---|---|---|---|---|"]
    L += [f"| `{p}` | {human(s)} | `{h}` | {r} | {corpus_source(p) if 'corpus' in r else '-'} |"
          for p, s, h, r in sorted(excluded_text)]
    L += ["", "Not included: the paper's own sources and build scripts (the PDF is the submission), and "
          "notes the paper does not cite.", ""]
    (HERE / "MANIFEST.md").write_text("\n".join(L))


if __name__ == "__main__":
    main()
