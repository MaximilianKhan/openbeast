#!/usr/bin/env python3
"""The remote-skill import gate behind scripts/skill-import.sh.

WHY A GATE AND NOT A COPY.  A skill is authoritative context: `skill(name)`
hands its body to the model and `start_skill_agent` puts it in a sub-agent's
system prompt. A poisoned SKILL.md is therefore a prompt injection the runner
has no defence against, and docs/TODO.md ("Selectively pull skills") has
required a per-skill review, a sandbox probe, a content hash and a ledger row
since 2026-05. Until this file every one of those steps was manual, and the
ledger (skills/REMOTE_PROVENANCE.md) stayed empty.

WHAT IS AUTOMATED AND WHAT IS NOT.  Fetching a pinned commit, the probe (a
static scan by NVIDIA SkillSpector), the hashes and the ledger row are done
here. Reading the skill end to end is not, and cannot be: `promote` refuses
without --reviewed-by, and a finding is only ever accepted by naming its rule
id. The scanner narrows what the reviewer must look at; it does not replace
the reviewer.

FAIL CLOSED, EVERYWHERE.  No scanner, a scanner that crashed, output this
file does not recognise, an analysis the scanner itself calls incomplete: all
of them refuse. A gate that passes when it could not look is not a gate.

MEASURED, 2026-10-01, SkillSpector 2.12.0 static (`--no-llm`), which is why
the policy is "accept by rule id" rather than "trust the recommendation":
  * a fixture that exfiltrates ~/.ssh to a remote host: DO_NOT_INSTALL (P1,
    PE3 x3, E1), exit 1. The scan does its job.
  * our own skills/security-audit: DO_NOT_INSTALL as well (TM1, PE3 x2, OH3),
    because a security checklist names `shell=True` and /etc/shadow. Static
    precision is moderate, so a hard block with no override would be wrong.
  * our own skills/code-review: recommendation CAUTION with ZERO findings.
    The cause is `analysis_completeness.status == "partial"`: a backticked
    path in the prose that is not a bundled file. So CAUTION alone carries no
    signal, and `partial` alone must not block.
  * MEASURED 2026-10-02: our own skills/eval-variant-porter and
    skills/performance-optimization each have one PARTLY INSPECTED file (a
    pattern analyzer hit `static_parse_limit` / `manifest_parse_error` on
    plain prose). A hard block on that count would refuse two of our own
    skills, so a partly inspected file is treated like a finding: it is open
    until the reviewer names the file (`--read-in-full SKILL.md`), which says
    "the scanner could not finish this file, so I read all of it". A file the
    scanner did not inspect AT ALL, a fatal exception and a failed analyzer
    still refuse with no override.

WHO SIGNS.  `--reviewed-by MK` means a human read every file. An agent asked
to do an import signs as what it is (`--agent-read <agent> --ordered-by MK`),
and the ledger row says so: the reader was the kind of system a poisoned
skill targets, so that row is weaker until a human runs `attest`.

EGRESS.  The scan runs with --no-llm, so file contents never leave the box.
SkillSpector's dependency check still sends the package names a skill
declares to api.osv.dev (it falls back to a bundled list when unreachable);
that is the scanner's design and is disclosed in docs/EXTERNAL_SKILLS_PLAN.md.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
SKILLS = os.path.join(REPO, "skills")
LEDGER = os.path.join(SKILLS, "REMOTE_PROVENANCE.md")
IN_HOUSE = os.path.join(SKILLS, "IN_HOUSE_SKILLS.txt")
STAGING = os.path.join(REPO, ".run", "skill-staging")

SCANNER_REPO = "https://github.com/NVIDIA/SkillSpector.git"
#: The v2.12.0 tag's commit. The verdict logic below was written against this
#: release's JSON; bump both together, after re-reading a real report.
SCANNER_REV = "c7958a3268d9498644b22edb75d0f051bbc8cbfc"
SCANNER_VERSION = "2.12.0"
SCAN_TIMEOUT = 600

EXIT_OK = 0
EXIT_ERROR = 1      # usage, missing scanner, unreadable output: could not judge
EXIT_BLOCKED = 3    # the gate looked and said no (findings, or a ledger mismatch)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
REV_RE = re.compile(r"^[0-9a-f]{40}$")
URL_RE = re.compile(r"^https://[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]+)?/[A-Za-z0-9._~/-]+$")
INITIALS_RE = re.compile(r"^[A-Za-z]{2,5}$")
AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,47}$")
#: The ledger's `Reviewed by` cell for an agent-read import. `verify` counts
#: these, and `attest` is how a human replaces one with their own initials.
AGENT_CELL_RE = re.compile(r"^agent ([a-z0-9][a-z0-9.-]{1,47}) for ([A-Z]{2,5})$")
RULE_RE = re.compile(r"^[A-Z]{1,4}[0-9]{1,3}$")

#: A skill is prose plus a few helper files. Anything near these is not one.
MAX_FILES = 2000
MAX_BYTES = 50 * 1024 * 1024

LEDGER_HEADER = ("Skill", "Source URL", "Upstream rev", "SHA-256", "Tree SHA-256",
                 "Imported", "Reviewed by", "Rewrite notes")
PLACEHOLDER = "| _(none yet — first import will land here)_ | | | | | | | |"

RECOMMENDATIONS = ("SAFE", "CAUTION", "DO_NOT_INSTALL")


class GateError(Exception):
    """Could not judge. Always a refusal (exit 1), never a pass."""


def say(msg: str = "") -> None:
    print(msg)


def warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr)


# --- config -----------------------------------------------------------------

def offline() -> bool:
    """OFFLINE=true, from the environment or openbeast.conf.

    Read by hand rather than by sourcing lib/conf.sh: conf.sh generates a
    SearXNG secret and appends it to openbeast.conf, and `verify` is called
    from doctor and from the test suite. A read-only check must not write.
    """
    val = os.environ.get("OFFLINE")
    if val is None:
        try:
            with open(os.path.join(REPO, "openbeast.conf"), encoding="utf-8") as fh:
                for line in fh:
                    m = re.match(r"^\s*OFFLINE=[\"']?([A-Za-z]+)", line)
                    if m:
                        val = m.group(1)
        except OSError:
            val = None
    return (val or "").lower() == "true"


def scanner_home() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "openbeast", "skillspector")


def find_scanner() -> str | None:
    explicit = os.environ.get("SKILL_SCANNER")
    if explicit:
        path = shutil.which(explicit) or (explicit if os.path.isfile(explicit) else None)
        return path if path and os.access(path, os.X_OK) else None
    managed = os.path.join(scanner_home(), "bin", "skillspector")
    if os.access(managed, os.X_OK):
        return managed
    return shutil.which("skillspector")


def need_scanner() -> str:
    path = find_scanner()
    if not path:
        raise GateError(
            "no skill scanner found, and nothing is imported unscanned.\n"
            "  install the pinned one:  ./scripts/skill-import.sh install-scanner\n"
            "  or point SKILL_SCANNER at a skillspector binary")
    return path


# --- files and hashes -------------------------------------------------------

def walk_skill(root: str) -> list[str]:
    """Relative paths of every file under root, sorted bytewise.

    Refuses symlinks and anything that is not a regular file: a link out of
    the tree is the classic way to make a reviewed directory mean something
    else on another machine, and neither the scanner nor the hash follows it.
    """
    out: list[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for d in dirnames:
            if os.path.islink(os.path.join(dirpath, d)):
                raise GateError(f"symlinked directory in the skill: {os.path.join(dirpath, d)}")
        for f in filenames:
            full = os.path.join(dirpath, f)
            if os.path.islink(full) or not os.path.isfile(full):
                raise GateError(f"not a regular file (symlink or special): {full}")
            total += os.path.getsize(full)
            out.append(os.path.relpath(full, root))
            if len(out) > MAX_FILES or total > MAX_BYTES:
                raise GateError(
                    f"{root} is too large to be a skill "
                    f"(> {MAX_FILES} files or > {MAX_BYTES // (1024 * 1024)} MiB)")
    return sorted(out, key=lambda p: p.encode())


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_sha256(root: str) -> str:
    """One digest over every file, so a changed helper script is caught too.

    Byte-identical to (the ledger documents this command):
      (cd DIR && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum)
    """
    h = hashlib.sha256()
    rels = sorted(("./" + r for r in walk_skill(root)), key=lambda p: p.encode())
    for rel in rels:
        h.update(f"{sha256_file(os.path.join(root, rel[2:]))}  {rel}\n".encode())
    return h.hexdigest()


# --- frontmatter (the loader's own rules) -----------------------------------

def split_frontmatter(text: str) -> tuple[dict, int]:
    """Parse the way agents/mcp_server.py does, and return (fields, end).

    Deliberately the same naive rules: the frontmatter ends at the FIRST
    `---` after the opening one, and every `key: value` line is a field.
    Validating with a real YAML parser would accept files the loader then
    reads differently, which is the failure this check exists to prevent.
    """
    if not text.startswith("---"):
        raise GateError("SKILL.md has no frontmatter (must start with ---)")
    end = text.find("---", 3)
    if end == -1:
        raise GateError("SKILL.md frontmatter is not terminated")
    fm: dict = {}
    for line in text[3:end].strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm, end


def check_skill_md(path: str, name: str) -> tuple[str, dict, int]:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        raise GateError(f"cannot read {path}: {e}") from e
    fm, end = split_frontmatter(text)
    if fm.get("name") != name:
        raise GateError(
            f"frontmatter name is {fm.get('name')!r}, the skill is being imported as {name!r}.\n"
            f"  edit {path} so they match (the loader and tests/test_scripts.sh require it)")
    desc = fm.get("description", "")
    if desc in ("", ">", "|", ">-", "|-", ">+", "|+"):
        raise GateError(
            "frontmatter description is empty or a multi-line YAML block.\n"
            f"  the loader reads one `description: text` line; flatten it in {path}")
    if len(text[end + 3:].strip()) < 50:
        raise GateError("SKILL.md body is under 50 characters; that is not a skill")
    return text, fm, end


def ensure_off_menu(path: str, name: str) -> bool:
    """Add `prompt_index: false` when the skill does not say. True if added.

    An imported skill stays out of the always-on menu by default: every menu
    entry is paid for on every local-model turn, and system-prompt-tools.md is
    one of the six era-hashed files, so a new entry rolls the eval era. Putting
    a remote skill on the menu is a separate, deliberate edit.
    """
    text, fm, end = check_skill_md(path, name)
    if "prompt_index" in fm:
        return False
    head = text[:end]
    if not head.endswith("\n"):
        head += "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + "prompt_index: false\n" + text[end:])
    return True


# --- the scan and its verdict -----------------------------------------------

def run_scanner(skill_dir: str) -> dict:
    scanner = need_scanner()
    fd, out = tempfile.mkstemp(prefix="skillscan-", suffix=".json")
    os.close(fd)
    try:
        cmd = [scanner, "scan", skill_dir, "--no-llm", "--format", "json", "--output", out]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=SCAN_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise GateError(f"the scanner did not finish: {e}") from e
        # 0 and 1 both mean "scan completed" (1 = risk score over 50). Anything
        # else is the scanner failing, and a failed scan is not a clean one.
        if proc.returncode not in (0, 1):
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            raise GateError(f"the scanner exited {proc.returncode}: {' | '.join(tail)}")
        try:
            with open(out, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError) as e:
            raise GateError(f"the scanner wrote no readable JSON report: {e}") from e
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def judge(report: dict, accept: set[str],
          read_in_full: set[str] | None = None) -> tuple[bool, list[str], dict]:
    """(allowed, lines to show the reviewer, summary for the ledger).

    Raises GateError when the report is not the shape this was written
    against: an unknown report is refused, never read optimistically.
    """
    read_in_full = read_in_full or set()
    try:
        risk = report["risk_assessment"]
        rec = risk["recommendation"]
        score = risk["score"]
        issues = report["issues"]
        version = report["metadata"]["skillspector_version"]
        comp = report["analysis_completeness"]
        ran = report["execution_successful"] and comp["execution_successful"]
        uninspected = int(comp["entirely_uninspected_files"])
        partial_files = int(comp["partially_inspected_files"])
        exceptions = comp["ledger_exceptions"]
        ids = [str(i["id"]).upper() for i in issues]
        # Which files, and why. A missing reference (a backticked path that is
        # not a bundled file) is reported as partial too but leaves every file
        # fully inspected, so it is not one of these.
        partial: dict[str, list[str]] = {}
        for exc in exceptions:
            if (exc.get("outcome") == "partial" and not exc.get("fatal")
                    and exc.get("phase") != "reference_resolution"):
                partial.setdefault(str(exc["path"]), []).append(str(exc["reason_code"]))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise GateError(
            f"unrecognised scanner report (missing {e}). This gate reads the "
            f"SkillSpector {SCANNER_VERSION} JSON; re-read a real report before "
            "trusting another version") from e
    if rec not in RECOMMENDATIONS or not isinstance(issues, list):
        raise GateError(f"unrecognised scanner report (recommendation={rec!r})")

    lines: list[str] = []
    if version != SCANNER_VERSION:
        lines.append(f"  ! scanner is {version}; this gate was written against {SCANNER_VERSION}")

    incomplete: list[str] = []
    if not ran:
        incomplete.append("the scanner reports its own execution as unsuccessful")
    if uninspected:
        incomplete.append(f"{uninspected} file(s) were not inspected at all")
    if partial_files != len(partial):
        # The count and the per-file reasons must agree, or this gate does not
        # know which files the scanner failed to finish.
        incomplete.append(f"{partial_files} file(s) were only partly inspected, but the "
                          f"report names {len(partial)}")
    for exc in exceptions:
        if isinstance(exc, dict) and exc.get("fatal"):
            incomplete.append(f"fatal analysis exception at {exc.get('path')}: {exc.get('reason_code')}")
    for st in comp.get("analyzer_statuses") or []:
        if isinstance(st, dict) and (st.get("status") == "failed" or st.get("failed")):
            incomplete.append(f"analyzer {st.get('analyzer_id')} failed")

    for i in issues:
        loc = i.get("location") or {}
        where = f"{loc.get('file', '?')}:{loc.get('start_line', '?')}"
        mark = "accepted" if str(i["id"]).upper() in accept else "OPEN"
        # Regex rules carry a pattern and the matched text; structural rules
        # (unpinned npx, undeclared permissions) carry only an explanation.
        label = i.get("pattern") or i.get("category") or "finding"
        text = " ".join(str(i.get("finding") or i.get("explanation") or "").split())[:70]
        lines.append(f"  {mark:8} {str(i.get('severity', '?')):8} {str(i['id']).upper():5} "
                     f"{where:22} {label}: {text}")

    for path in sorted(partial):
        mark = "read" if path in read_in_full else "OPEN"
        lines.append(f"  {mark:8} PARTIAL  {path:28} the scanner could not finish this file: "
                     f"{', '.join(sorted(set(partial[path])))}")

    open_ids = sorted(set(ids) - accept)
    open_partial = sorted(set(partial) - read_in_full)
    unused = sorted(accept - set(ids))
    for rule in unused:
        lines.append(f"  ! --accept {rule} matches no finding in this scan")
    for path in sorted(read_in_full - set(partial)):
        lines.append(f"  ! --read-in-full {path} is not a partly inspected file in this scan")
    for reason in incomplete:
        lines.append(f"  ✗ incomplete analysis: {reason}")
    if open_partial:
        lines.append(f"  ✗ {len(open_partial)} file(s) the scanner only partly inspected: "
                     f"{', '.join(open_partial)}")
        lines.append("    read each one end to end, then name it:  --read-in-full <FILE,FILE>")

    allowed = not incomplete and not open_ids and not open_partial
    if open_ids:
        lines.append(f"  ✗ {len(open_ids)} finding type(s) not accepted: {', '.join(open_ids)}")
        lines.append("    read each flagged line in the skill, then either rewrite the staged copy")
        lines.append("    or accept the rule ids you have read:  --accept <ID,ID>")
    summary = {"version": version, "score": score, "recommendation": rec,
               "accepted": sorted(set(ids) & accept), "findings": len(issues),
               "read_in_full": sorted(set(partial) & read_in_full)}
    return allowed, lines, summary


def scan_and_judge(skill_dir: str, accept: set[str],
                   read_in_full: set[str] | None = None) -> tuple[bool, dict]:
    report = run_scanner(skill_dir)
    allowed, lines, summary = judge(report, accept, read_in_full)
    say(f"scan: SkillSpector {summary['version']} static (--no-llm) — score "
        f"{summary['score']}, {summary['recommendation']}, {summary['findings']} finding(s)")
    for line in lines:
        say(line)
    if allowed:
        closed = []
        if summary["accepted"]:
            closed.append(f"accepted: {', '.join(summary['accepted'])}")
        if summary["read_in_full"]:
            closed.append(f"read in full: {', '.join(summary['read_in_full'])}")
        say("  ✓ nothing open" + (f" ({'; '.join(closed)})" if closed else ""))
    return allowed, summary


def parse_accept(raw: str | None) -> set[str]:
    out: set[str] = set()
    for part in (raw or "").split(","):
        part = part.strip().upper()
        if not part:
            continue
        if not RULE_RE.match(part):
            raise GateError(f"--accept takes scanner rule ids (TM1,PE3), not {part!r}")
        out.add(part)
    return out


def parse_read_in_full(raw: str | None) -> set[str]:
    """Paths inside the skill, as the scan prints them (SKILL.md, scripts/x.py)."""
    out: set[str] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if os.path.isabs(part) or ".." in part.split("/"):
            raise GateError(f"--read-in-full takes paths inside the skill, not {part!r}")
        out.add(part[2:] if part.startswith("./") else part)
    return out


def reviewer_cell(args) -> str:
    """The ledger's `Reviewed by` cell: a human's initials, or an agent's name
    and the human who ordered the import. Never both, never neither."""
    human, agent, boss = args.reviewed_by, args.agent_read, args.ordered_by
    if human and (agent or boss):
        raise GateError("--reviewed-by (a human read it) and --agent-read/--ordered-by "
                        "(an agent read it) are two different rows; pick the true one")
    if human:
        if not INITIALS_RE.match(human):
            raise GateError(f"--reviewed-by takes a human's initials (2-5 letters), not {human!r}")
        return human.upper()
    if agent or boss:
        if not (agent and boss):
            raise GateError("an agent-read import needs both --agent-read <agent> and "
                            "--ordered-by <initials of the human who asked for it>")
        if not AGENT_RE.match(agent):
            raise GateError(f"--agent-read takes a lowercase agent name (claude-opus-5-5), not {agent!r}")
        if not INITIALS_RE.match(boss):
            raise GateError(f"--ordered-by takes a human's initials (2-5 letters), not {boss!r}")
        return f"agent {agent} for {boss.upper()}"
    raise GateError(
        "a reviewer is required: --reviewed-by <initials> for the human who read this "
        "skill end to end,\n  or --agent-read <agent> --ordered-by <initials> when an agent "
        "read it on a human's instruction.\n"
        "  The scan narrows the review; it does not replace it.")


# --- the ledger -------------------------------------------------------------

def _cells(line: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", line.strip())
    return [p.strip() for p in parts[1:-1]]


def read_ledger() -> tuple[list[str], int, int, list[dict]]:
    """(all lines, table start, table end, rows). The table is the one under
    `## Ledger`; rows are replaced as a block so the prose around it is ours."""
    try:
        with open(LEDGER, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError as e:
        raise GateError(f"cannot read the ledger {LEDGER}: {e}") from e
    start = None
    for idx, line in enumerate(lines):
        if line.startswith("|") and _cells(line)[:2] == list(LEDGER_HEADER[:2]):
            start = idx
    if start is None:
        raise GateError(f"{LEDGER} has no ledger table (a row starting `| Skill | Source URL |`)")
    if tuple(_cells(lines[start])) != LEDGER_HEADER:
        raise GateError(f"{LEDGER} table header is not {' | '.join(LEDGER_HEADER)}")
    end = start + 1
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    rows: list[dict] = []
    for line in lines[start + 2:end]:
        cells = _cells(line)
        if not cells or cells[0].startswith("_("):
            continue
        if len(cells) != len(LEDGER_HEADER):
            raise GateError(f"malformed ledger row (want {len(LEDGER_HEADER)} cells): {line}")
        rows.append(dict(zip(LEDGER_HEADER, cells)))
    return lines, start, end, rows


def write_ledger(lines: list[str], start: int, end: int, rows: list[dict]) -> None:
    table = ["| " + " | ".join(LEDGER_HEADER) + " |",
             "|" + "---|" * len(LEDGER_HEADER)]
    if rows:
        for row in sorted(rows, key=lambda r: r["Skill"].strip("`")):
            table.append("| " + " | ".join(row[h] for h in LEDGER_HEADER) + " |")
    else:
        table.append(PLACEHOLDER)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines[:start] + table + lines[end:]))
    os.replace(tmp, LEDGER)


def permalink(url: str, rev: str, path: str) -> str:
    base = url[:-4] if url.endswith(".git") else url
    if base.startswith("https://github.com/"):
        return f"{base}/tree/{rev}" + (f"/{path}" if path else "")
    return base + (f" ({path})" if path else "")


# --- commands ---------------------------------------------------------------

def cmd_scanner(_args) -> int:
    path = find_scanner()
    say(f"pinned: SkillSpector {SCANNER_VERSION} @ {SCANNER_REV}")
    if not path:
        say("found:  none  (./scripts/skill-import.sh install-scanner)")
        return EXIT_ERROR
    try:
        ver = subprocess.run([path, "--version"], capture_output=True, text=True,
                             timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        ver = f"(could not run: {e})"
    say(f"found:  {path}  {ver}")
    return EXIT_OK


def cmd_install_scanner(_args) -> int:
    if offline():
        raise GateError("OFFLINE=true: installing the scanner needs PyPI and GitHub. "
                        "Install it on a connected box, or set SKILL_SCANNER.")
    home = scanner_home()
    say(f"installing SkillSpector {SCANNER_VERSION} ({SCANNER_REV[:12]}) into {home}")
    say("  an isolated venv: nothing is added to the stack's own Python closure.")
    say("  its dependencies are resolved by pip and are NOT hash-pinned.")
    os.makedirs(os.path.dirname(home), exist_ok=True)
    spec = f"skillspector @ git+{SCANNER_REPO}@{SCANNER_REV}"
    for cmd in ([sys.executable, "-m", "venv", "--clear", home],
                [os.path.join(home, "bin", "pip"), "install", "--quiet",
                 "--disable-pip-version-check", spec]):
        if subprocess.run(cmd).returncode != 0:
            raise GateError(f"failed: {' '.join(cmd)}")
    return cmd_scanner(None)


def cmd_scan(args) -> int:
    target = os.path.abspath(args.dir)
    if not os.path.isfile(os.path.join(target, "SKILL.md")):
        raise GateError(f"{target} has no SKILL.md")
    walk_skill(target)
    allowed, _ = scan_and_judge(target, parse_accept(args.accept),
                                parse_read_in_full(args.read_in_full))
    return EXIT_OK if allowed else EXIT_BLOCKED


def _git(args: list[str], cwd: str | None = None) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1")
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=never",
           "-c", "protocol.ext.allow=never", *args]
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GateError(f"git {args[0]} did not run: {e}") from e
    if proc.returncode != 0:
        raise GateError(f"git {args[0]} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout.strip()


def cmd_fetch(args) -> int:
    name, url, rev = args.name, args.url, args.rev
    sub = (args.path or "").strip("/")
    if not NAME_RE.match(name):
        raise GateError(f"--name must be lowercase letters, digits and dashes, not {name!r}")
    if not REV_RE.match(rev):
        raise GateError(
            f"--rev must be a full 40-hex commit SHA, not {rev!r}.\n"
            "  a branch or tag moves; the ledger pins what was actually reviewed")
    if not URL_RE.match(url):
        raise GateError(f"the source must be a plain https:// git URL, not {url!r}")
    if sub and (os.path.isabs(args.path) or ".." in sub.split("/")):
        raise GateError(f"--path must be a relative path inside the repository, not {args.path!r}")
    if offline():
        raise GateError("OFFLINE=true: fetch needs the network. Stage the skill on a "
                        "connected box and carry .run/skill-staging/ over.")
    dest = os.path.join(STAGING, name)
    if os.path.exists(dest) and not args.force:
        raise GateError(f"{dest} is already staged (edits may be in it). --force replaces it.")
    need_scanner()  # before the download, not after it

    os.makedirs(STAGING, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".fetch-", dir=STAGING)
    try:
        _git(["init", "-q", tmp])
        _git(["remote", "add", "origin", url], cwd=tmp)
        _git(["fetch", "-q", "--depth", "1", "origin", rev], cwd=tmp)
        _git(["checkout", "-q", "--detach", "FETCH_HEAD"], cwd=tmp)
        head = _git(["rev-parse", "HEAD"], cwd=tmp)
        if head != rev:
            raise GateError(f"asked for {rev}, the checkout is at {head or '(nothing)'}")
        src = os.path.realpath(os.path.join(tmp, sub))
        root = os.path.realpath(tmp)
        if src != root and not src.startswith(root + os.sep):
            raise GateError(f"--path {args.path!r} resolves outside the repository")
        if not os.path.isfile(os.path.join(src, "SKILL.md")):
            raise GateError(f"no SKILL.md at {sub or '(repository root)'} in {url}@{rev[:12]}")
        shutil.rmtree(os.path.join(src, ".git"), ignore_errors=True)
        files = walk_skill(src)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(src, dest, symlinks=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with open(dest + ".provenance.json", "w", encoding="utf-8") as fh:
        json.dump({"url": url, "rev": rev, "path": sub, "fetched": _today()}, fh, indent=1)
        fh.write("\n")

    say(f"staged {name}: {len(files)} file(s) from {url}@{rev[:12]}"
        + (f" ({sub})" if sub else ""))
    say(f"  {dest}")
    say("")
    try:
        scan_and_judge(dest, set())
    except GateError as e:
        warn(f"staged, but the scan could not run: {e}")
    say("")
    say("next, in this order:")
    say(f"  1. read every file in {os.path.relpath(dest, REPO)}/ end to end")
    say("  2. rewrite the staged copy for our tool names and paths; fix the frontmatter name")
    if os.path.isdir(os.path.join(SKILLS, name)):
        say(f"  3. ./scripts/skill-import.sh diff {name}        (this is a refresh)")
    say(f"  {'4' if os.path.isdir(os.path.join(SKILLS, name)) else '3'}. "
        f"./scripts/skill-import.sh promote {name} --reviewed-by <initials> --notes \"...\"")
    say("     (an agent doing this for you signs as itself: --agent-read <agent> --ordered-by <initials>)")
    return EXIT_OK


def _today() -> str:
    return datetime.date.today().isoformat()


def cmd_diff(args) -> int:
    live, staged = os.path.join(SKILLS, args.name), os.path.join(STAGING, args.name)
    for d in (live, staged):
        if not os.path.isdir(d):
            raise GateError(f"{d} does not exist")
    return subprocess.run(["diff", "-ru", live, staged]).returncode


def cmd_promote(args) -> int:
    name = args.name
    if not NAME_RE.match(name):
        raise GateError(f"not a skill name: {name!r}")
    reviewer = reviewer_cell(args)
    staged = os.path.join(STAGING, name)
    prov_path = staged + ".provenance.json"
    if not os.path.isdir(staged) or not os.path.isfile(prov_path):
        raise GateError(f"{name} is not staged (./scripts/skill-import.sh fetch ...)")
    try:
        with open(prov_path, encoding="utf-8") as fh:
            prov = json.load(fh)
        url, rev, sub = prov["url"], prov["rev"], prov.get("path", "")
    except (OSError, ValueError, KeyError) as e:
        raise GateError(f"unreadable provenance record {prov_path}: {e}") from e
    if not REV_RE.match(str(rev)) or not URL_RE.match(str(url)):
        raise GateError(f"provenance record {prov_path} does not hold a pinned https source")

    lines, start, end, rows = read_ledger()
    known = [r for r in rows if r["Skill"].strip("`") == name]
    live = os.path.join(SKILLS, name)
    if os.path.exists(live) and not known:
        raise GateError(
            f"skills/{name} exists and has no ledger row: it is one of ours.\n"
            "  an import never overwrites an in-house skill; stage it under another --name")

    md = os.path.join(staged, "SKILL.md")
    added = ensure_off_menu(md, name)
    _, fm, _ = check_skill_md(md, name)
    walk_skill(staged)

    say(f"promote {name}  ({url}@{rev[:12]})")
    allowed, summary = scan_and_judge(staged, parse_accept(args.accept),
                                      parse_read_in_full(args.read_in_full))
    if not allowed:
        say("")
        say(f"NOT promoted. skills/{name} and the ledger are untouched.")
        return EXIT_BLOCKED

    if os.path.exists(live):
        shutil.rmtree(live)
    shutil.copytree(staged, live, symlinks=True)

    notes = " ".join((args.notes or "").split()) or "(none recorded)"
    notes += (f" · scan: SkillSpector {summary['version']} static, score {summary['score']}, "
              f"{summary['recommendation']}")
    if summary["accepted"]:
        notes += f", accepted {','.join(summary['accepted'])}"
    if summary["read_in_full"]:
        notes += f", scanner partial on {','.join(summary['read_in_full'])} (read in full)"
    row = {
        "Skill": f"`{name}`",
        "Source URL": permalink(url, rev, sub),
        "Upstream rev": f"`{rev}`",
        "SHA-256": f"`{sha256_file(os.path.join(live, 'SKILL.md'))}`",
        "Tree SHA-256": f"`{tree_sha256(live)}`",
        "Imported": _today(),
        "Reviewed by": reviewer,
        "Rewrite notes": notes.replace("|", "\\|"),
    }
    write_ledger(lines, start, end, [r for r in rows if r not in known] + [row])

    say("")
    say(f"promoted: skills/{name}/  ({'refreshed' if known else 'new'} ledger row)")
    if added:
        say("  added `prompt_index: false`: off the always-on menu, reachable by skill(name)")
    elif str(fm.get("prompt_index", "")).lower() != "false":
        say("  ! this skill is ON the always-on menu: run scripts/generate-skill-index.py")
        say("    and expect the eval era to roll (./scripts/eval-era.sh)")
    if AGENT_CELL_RE.match(reviewer):
        say("  ! read by an agent, not a human. After reading it yourself:")
        say(f"    ./scripts/skill-import.sh attest {name} --reviewed-by <initials>")
    say(f"  stage both in one commit:  git add skills/{name} skills/REMOTE_PROVENANCE.md")
    return EXIT_OK


def cmd_attest(args) -> int:
    """A human takes over an agent-read row after reading the live skill.

    Only the `Reviewed by` cell changes. It is refused unless the files still
    match the row's hashes: the signature is for what the ledger pinned, not
    for whatever is on disk now.
    """
    name = args.name
    if not args.reviewed_by or not INITIALS_RE.match(args.reviewed_by):
        raise GateError("attest needs --reviewed-by <initials>: the human who read it")
    lines, start, end, rows = read_ledger()
    known = [r for r in rows if r["Skill"].strip("`") == name]
    if not known:
        raise GateError(f"{name} has no ledger row; there is nothing to attest")
    row = known[0]
    live = os.path.join(SKILLS, name)
    md = os.path.join(live, "SKILL.md")
    if (not os.path.isfile(md) or sha256_file(md) != row["SHA-256"].strip("`")
            or tree_sha256(live) != row["Tree SHA-256"].strip("`")):
        print(f"  ✗ {name}: the files do not match the ledger row; attest signs what was pinned")
        return EXIT_BLOCKED
    was = row["Reviewed by"]
    row["Reviewed by"] = args.reviewed_by.upper()
    write_ledger(lines, start, end, rows)
    say(f"attested: {name} — Reviewed by {was} → {row['Reviewed by']}")
    return EXIT_OK


def read_in_house() -> set[str]:
    """Directory names of the skills written in this repository.

    skills/IN_HOUSE_SKILLS.txt, one name per line, `#` comments. A missing
    file is an empty list, so every unpinned directory fails verify rather
    than passing because the list could not be read.
    """
    try:
        with open(IN_HOUSE, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return set()
    return {line.split("#", 1)[0].strip() for line in text.splitlines()} - {""}


def cmd_verify(args) -> int:
    _, _, _, rows = read_ledger()
    # "No row, no skill" — checked from the DISK, not from the ledger. Walking
    # only the rows meant a skill with no row was never looked at: delete an
    # imported skill's row (or add a new directory) and its SKILL.md stayed in
    # skills/, unpinned, with verify green. Every directory must be accounted
    # for by exactly one of the ledger and the committed in-house list.
    in_house = read_in_house()
    pinned = {row["Skill"].strip("`") for row in rows}
    try:
        on_disk = sorted(d for d in os.listdir(SKILLS)
                         if not d.startswith(".") and os.path.isdir(os.path.join(SKILLS, d)))
    except OSError:
        on_disk = []
    unaccounted = 0
    for name in on_disk:
        if name in pinned and name in in_house:
            unaccounted += 1
            print(f"  ✗ {name}: has a ledger row AND is listed in skills/{os.path.basename(IN_HOUSE)}"
                  " — it is imported or in-house, never both")
        elif name not in pinned and name not in in_house:
            unaccounted += 1
            print(f"  ✗ {name}: skills/{name}/ has no ledger row and is not listed in "
                  f"skills/{os.path.basename(IN_HOUSE)}")
    if unaccounted:
        print(f"skills: {unaccounted} director{'y' if unaccounted == 1 else 'ies'} with no provenance")
        print("  imported from elsewhere: it needs a ledger row — re-import it through the gate")
        print("    (./scripts/skill-import.sh fetch … then promote); a deleted row is restored from git")
        print(f"  written in this repo: add its name to skills/{os.path.basename(IN_HOUSE)}")
        return EXIT_BLOCKED
    bad = 0
    for row in rows:
        name = row["Skill"].strip("`")
        live = os.path.join(SKILLS, name)
        md = os.path.join(live, "SKILL.md")
        problems: list[str] = []
        if not NAME_RE.match(name):
            problems.append("not a valid skill name")
        elif not os.path.isfile(md):
            problems.append("has a ledger row but no skills/%s/SKILL.md" % name)
        else:
            try:
                if sha256_file(md) != row["SHA-256"].strip("`"):
                    problems.append("SKILL.md does not match its pinned SHA-256")
                if tree_sha256(live) != row["Tree SHA-256"].strip("`"):
                    problems.append("the skill's files do not match the pinned tree SHA-256")
            except GateError as e:
                problems.append(str(e))
        if problems:
            bad += 1
            for p in problems:
                print(f"  ✗ {name}: {p}")
        elif not args.quiet:
            say(f"  ✓ {name}: matches the ledger ({row['Upstream rev'].strip('`')[:12]})")
    if bad:
        print(f"remote skills: {bad} of {len(rows)} do NOT match skills/REMOTE_PROVENANCE.md")
        print("  an edit to an imported skill is a re-import: re-stage, re-scan, re-promote")
        return EXIT_BLOCKED
    agent_read = sum(1 for r in rows if AGENT_CELL_RE.match(r["Reviewed by"]))
    tail = f" ({agent_read} read by an agent, not yet by a human)" if agent_read else ""
    say(f"remote skills: OK — {len(rows)} imported, all match the ledger{tail}" if rows
        else "remote skills: OK — none imported")
    return EXIT_OK


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="skill-import.sh",
        description="Import a remote skill through the gate: pinned commit, scan, review, ledger.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scanner", help="which scanner would run, and the pinned version")
    sub.add_parser("install-scanner", help="install the pinned SkillSpector into its own venv")

    p = sub.add_parser("scan", help="scan a skill directory and print the gate's verdict")
    p.add_argument("dir")
    p.add_argument("--accept", help="rule ids already read and accepted, e.g. TM1,PE3")
    p.add_argument("--read-in-full", help="partly inspected files you read end to end")

    p = sub.add_parser("fetch", help="fetch a pinned commit into .run/skill-staging/ and scan it")
    p.add_argument("url")
    p.add_argument("--rev", required=True, help="full 40-hex commit SHA")
    p.add_argument("--name", required=True, help="local skill name (the directory under skills/)")
    p.add_argument("--path", default="", help="skill directory inside the repository")
    p.add_argument("--force", action="store_true", help="replace an existing staged copy")

    p = sub.add_parser("diff", help="diff the live skill against the staged copy (a refresh)")
    p.add_argument("name")

    p = sub.add_parser("promote", help="re-scan the staged copy, copy it to skills/, write the ledger row")
    p.add_argument("name")
    p.add_argument("--reviewed-by", help="initials of the human who read it end to end")
    p.add_argument("--agent-read", help="the agent that read it, when no human did")
    p.add_argument("--ordered-by", help="initials of the human who asked the agent for the import")
    p.add_argument("--accept", help="rule ids read and accepted, e.g. TM1,PE3")
    p.add_argument("--read-in-full", help="files the scanner only partly inspected and you read "
                                          "end to end, e.g. SKILL.md,scripts/run.py")
    p.add_argument("--notes", help="one line on what the rewrite changed")

    p = sub.add_parser("attest", help="a human signs an agent-read row after reading the skill")
    p.add_argument("name")
    p.add_argument("--reviewed-by", help="initials of the human who read it end to end")

    p = sub.add_parser("verify", help="every ledger row still matches the files on disk")
    p.add_argument("--quiet", action="store_true")

    args = ap.parse_args(argv)
    handler = {"scanner": cmd_scanner, "install-scanner": cmd_install_scanner,
               "scan": cmd_scan, "fetch": cmd_fetch, "diff": cmd_diff,
               "promote": cmd_promote, "attest": cmd_attest,
               "verify": cmd_verify}[args.cmd]
    try:
        return handler(args)
    except GateError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
