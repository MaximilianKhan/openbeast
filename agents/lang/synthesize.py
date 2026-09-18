#!/usr/bin/env python3
"""Synthesis (phase 3): a model DRAFTS candidate claims; the toolchain decides.

docs/BEAST_LANG_PLAN.md §3: *a document may propose a claim; only the
toolchain may confirm one.* A pack needs no LLM — the verified claims already
are the summary. What an LLM is good for is reading a changelog and proposing
"this stopped compiling, write this instead", which is a CANDIDATE claim and
nothing more. This module is the pipe between the two, and its whole job is
the ordering:

    L0 corpus + L1 facts -> prompt -> model -> JSON -> strict parse
        -> duplicate check -> verify.verify (the REAL drivers) -> STAGING

so that a model-written line can never reach a pack unverified. Four rules:

  HOSTILE INPUT. A drafted snippet is attacker-controlled text that we hand to
    a compiler. Everything that makes that safe already lives in drivers.py /
    _proc.py (the host-file refusal scan, the scrubbed environment, the
    process-group timeout, the address-space cap, never EXECUTING a snippet),
    and every candidate goes through `verify.verify` so it gets all of it.
    Nothing here compiles anything itself. The one hazard this module adds is
    its own: `verify.Claim` reads a one-line snippet ending in `.py`/`.c`/…
    as a FIXTURE PATH, so a candidate `"new": ["/home/u/secret.py"]` would
    have been a file-read primitive. Drafted snippets are code, never paths.
  NEVER REPAIRED. A model returns junk — prose around the JSON, a trailing
    comma, half a record. A record that does not parse, or parses into the
    wrong shape, is COUNTED AND DROPPED. It is never patched into something
    the model did not say: a repaired claim is one we wrote and then
    attributed to a source.
  STAGING, NOT SHIPPING. VERIFIED candidates land in claims/staging/, which
    `verify.load_claims` does not read (it lists one directory, no recursion
    — a test pins that). `promote` is a separate, explicit, human step; it
    re-verifies, refuses the whole batch if anything no longer passes, and
    rebuilds the escalation index.
  NO ENDPOINT BY DEFAULT. The HTTP client targets OPENBEAST_LANG_SYNTH_URL and
    there is deliberately NO fallback to localhost:8080: that port is
    whatever this rig is serving, and a synthesis run pointed at it by
    accident is a contaminated measurement. It also asks the GPU lease first
    (scripts/gpu-lease.sh) — drafting is a GPU job and verifying is a CPU one.

usage:
  python3 agents/lang/synthesize.py draft python --dry-run
  python3 agents/lang/synthesize.py draft python [--match REGEX] [--source FILE]
  python3 agents/lang/synthesize.py status
  python3 agents/lang/synthesize.py promote agents/lang/claims/staging/python-20260917.json --all
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENTS = os.path.dirname(_HERE)
_REPO = os.path.dirname(_AGENTS)
if _AGENTS not in sys.path:
    sys.path.insert(0, _AGENTS)

from lang import _proc               # noqa: E402
from lang import drivers as D        # noqa: E402
from lang import packs as P          # noqa: E402
from lang import reference as R      # noqa: E402
from lang import verify as V         # noqa: E402

STAGING_DIRNAME = "staging"
#: Where `promote` writes. One file per language, separate from the
#: hand-authored sets on purpose: zig-0.16.json is GENERATED from the fixture
#: manifest (regen_zig.py --check would fail on an append), and a reviewer
#: should be able to see at a glance which claims a model drafted.
SHIPPED_SUFFIX = "-synthesized.json"

ENV_URL = "OPENBEAST_LANG_SYNTH_URL"
ENV_MODEL = "OPENBEAST_LANG_SYNTH_MODEL"
ENV_KEY = "OPENBEAST_LANG_SYNTH_KEY"
ENV_THINKING = "OPENBEAST_LANG_SYNTH_THINKING"

# Budgets. Every one is a bound on something a model or a corpus controls.
MAX_PROMPTS = _proc.env_number("OPENBEAST_LANG_SYNTH_MAX_PROMPTS", 8, int, 1)
MAX_CANDIDATES = _proc.env_number("OPENBEAST_LANG_SYNTH_MAX_CANDIDATES", 40, int, 1)
MAX_PROMPT_CHARS = _proc.env_number("OPENBEAST_LANG_SYNTH_PROMPT_CHARS", 12_000, int, 2_000)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_SNIPPETS_PER_SIDE = 4
MAX_SNIPPET_CHARS = 4_000
MAX_SUMMARY_CHARS = 300
HTTP_TIMEOUT_S = _proc.env_number("OPENBEAST_LANG_SYNTH_TIMEOUT", 600.0, float, 1.0)
#: Generous on purpose: this rig's default is a REASONING model, and one that
#: spends its whole budget thinking returns an empty `content` — which would
#: read as "malformed" when the truth is "never got to answer".
MAX_TOKENS = _proc.env_number("OPENBEAST_LANG_SYNTH_MAX_TOKENS", 16384, int, 256)

_TEXT_EXT = (".txt", ".md", ".rst", ".html", ".htm", ".zig", ".go", ".rs")
_TAR_EXT = (".tar.xz", ".tar.bz2", ".tar.gz", ".tgz", ".tar")
#: What "this section is about a change between versions" looks like in a
#: path. A default, not a judgement: --match replaces it.
DEFAULT_MATCH = (r"whatsnew|what.?s.?new|changelog|changes|release.?notes|/news"
                 r"|migrat|deprecat|removed|compiler_support")
_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
#: A variant becomes `-std=<v>` / `--edition=<v>` in an argv. No shell is
#: involved, but a flag value is still not a place for arbitrary text.
_VARIANT_RE = re.compile(r"[A-Za-z0-9+.]{1,16}")
_FIXTURE_EXT = (".zig", ".c", ".cpp", ".rs", ".go", ".py")    # verify._read's list

# Outcomes, in report order.
DRAFTED, MALFORMED, DUPLICATE, REFUSED = "drafted", "malformed", "duplicate", "refused"
TRANSIENT, NOT_A_BREAK, REJECTED, VERIFIED = "transient", "not_a_break", "rejected", "verified"
OVER_BUDGET = "over_budget"


class SynthError(Exception):
    """A run that cannot start, with the exit code that says why."""

    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


def claims_dir() -> str:
    # Read through packs so a test (or a rig) that points the serving path at
    # another claim set points synthesis at the same one.
    return P.CLAIMS_DIR


def staging_dir() -> str:
    return os.path.join(claims_dir(), STAGING_DIRNAME)


# --------------------------------------------------------------------------
# the model, behind an interface
# --------------------------------------------------------------------------

class StubClient:
    """`draft(prompt) -> str` from a canned list. Records every prompt, so a
    test can assert what was (and was not) sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def draft(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else ""


class HTTPClient:
    """OpenAI-compatible chat completions over stdlib urllib.

    Constructed ONLY by client_from_env(), which refuses without
    OPENBEAST_LANG_SYNTH_URL. There is no default URL anywhere in this file.
    """

    def __init__(self, url: str, model: str = "local", api_key: str = "",
                 timeout: float = HTTP_TIMEOUT_S, thinking: bool | None = None):
        if not re.match(r"https?://[^/\s]+", url or ""):
            raise SynthError(f"{ENV_URL} must be an http(s) URL, got {url!r}")
        url = url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        self.url, self.model, self.api_key, self.timeout = url, model, api_key, timeout
        #: None = say nothing (portable to any OpenAI-compatible server);
        #: True/False = llama-server's per-request toggle, as agents/router.py
        #: sends it.
        self.thinking = thinking

    def draft(self, prompt: str) -> str:
        payload = {
            "model": self.model, "temperature": 0.2, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": prompt}],
        }
        if self.thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": self.thinking}
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:   # noqa: S310
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return ""                     # not a draft; counted as malformed
        doc = json.loads(raw.decode("utf-8", "replace"))
        return str(doc["choices"][0]["message"].get("content") or "")


def client_from_env(env=None) -> HTTPClient:
    env = os.environ if env is None else env
    url = (env.get(ENV_URL) or "").strip()
    if not url:
        raise SynthError(
            f"{ENV_URL} is not set, and there is deliberately no default: "
            f"localhost:8080 is whatever this rig is serving right now, and a "
            f"synthesis run must never land on it by accident. Point it at "
            f"the OpenAI-compatible endpoint that should draft —\n"
            f"  {ENV_URL}=http://<host>:<port>/v1\n"
            f"— and only at a server nothing else is measuring on. "
            f"--dry-run shows the prompts without calling anything.", 2)
    raw = (env.get(ENV_THINKING) or "").strip().lower()
    thinking = {"1": True, "true": True, "on": True,
                "0": False, "false": False, "off": False}.get(raw)
    return HTTPClient(url, (env.get(ENV_MODEL) or "local").strip(),
                      (env.get(ENV_KEY) or "").strip(), thinking=thinking)


# --------------------------------------------------------------------------
# the GPU lease
# --------------------------------------------------------------------------

def lease_status() -> str:
    """First line of `scripts/gpu-lease.sh status`, or "" if it cannot be read.

    Run from the MAIN tree: the lease file lives in that tree's .run/ (or
    OPENBEAST_RUN_DIR), so a git worktree asking its own copy of the script
    reads an empty directory and is told FREE while a campaign holds the card.
    """
    script = os.path.join(_REPO, "scripts", "gpu-lease.sh")
    try:
        r = subprocess.run(["bash", script, "status"], capture_output=True,
                           text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (r.stdout or "").splitlines()[0].strip() if r.stdout else ""


def _ancestors(pid: int | None = None) -> set[int]:
    """This process and everything above it. `gpu-lease.sh run -- <us>` makes
    the HOLDER our ancestor, and that lease is ours, not somebody else's."""
    seen: set[int] = set()
    cur = os.getpid() if pid is None else pid
    while cur > 1 and cur not in seen:
        seen.add(cur)
        try:
            with open(f"/proc/{cur}/stat") as fh:
                cur = int(fh.read().rsplit(") ", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return seen


def check_lease(ignore: bool = False, status_fn=lease_status,
                ancestors_fn=_ancestors) -> tuple[bool, str]:
    """(may we run, why). UNKNOWN IS NOT FREE: if the lease cannot be read the
    answer is no, the same rule that keeps an unverifiable claim out of a
    pack. --ignore-lease is the operator saying they looked."""
    line = status_fn() or ""
    if line.startswith("FREE"):
        return True, f"GPU lease: {line}"
    if line.startswith("HELD"):
        m = re.match(r"HELD by pid (\d+)", line)
        if m and int(m.group(1)) in ancestors_fn():
            return True, f"GPU lease: {line} (that is this run)"
        if ignore:
            return True, f"GPU lease: {line} — IGNORED (--ignore-lease)"
        return False, (f"the GPU lease is {line}\n  synthesis is a GPU job "
                       f"(the model drafts) and a CPU one (every candidate is "
                       f"compiled); running it now contaminates that work. "
                       f"Wait for it, or pass --ignore-lease if you are sure.")
    if ignore:
        return True, "GPU lease: could not be read — IGNORED (--ignore-lease)"
    return False, ("the GPU lease could not be read (scripts/gpu-lease.sh "
                   "status gave no HELD/FREE line), and unknown is not free. "
                   "Pass --ignore-lease if you have checked the card yourself.")


# --------------------------------------------------------------------------
# L0: source material, bounded, never extracted to disk
# --------------------------------------------------------------------------

def library_root() -> str:
    """Same default as scripts/lang-library.sh (`where`)."""
    return os.environ.get("OPENBEAST_LANG_DIR") or os.path.join(
        _REPO, "..", "openbeast-lang-library")


def _to_text(name: str, raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    if name.lower().endswith((".html", ".htm")):
        text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)     # markup's spacing means nothing
    # Plain text keeps its indentation: in a changelog the indented block IS
    # the code sample, and python's is syntax.
    return re.sub(r"\n\s*\n\s*", "\n\n", text).strip()


def corpus_sections(lang: str, root: str | None = None, match: str | None = None,
                    sources: list[str] | None = None,
                    max_sections: int = 64) -> tuple[list[dict], list[str]]:
    """([{origin, text}], notes). Archives are read member by member IN
    MEMORY — nothing is unpacked, so a hostile tarball has no path to
    traverse — regular files only, each capped, and only members whose path
    matches `match`. PDFs are skipped and SAID to be (plan §4: extraction
    destroys the structure we need)."""
    rx = re.compile(match or DEFAULT_MATCH, re.I)
    sections: list[dict] = []
    notes: list[str] = []

    def add(origin: str, raw: bytes) -> None:
        text = _to_text(origin, raw)
        if len(text) >= 200 and len(sections) < max_sections:
            sections.append({"origin": origin, "text": text})

    for path in sources or []:                 # explicit: no pattern filter
        try:
            with open(path, "rb") as fh:
                add(os.path.basename(path), fh.read(MAX_MEMBER_BYTES))
        except OSError as e:
            notes.append(f"--source {path}: {e}")
    base = os.path.join(root or library_root(), lang)
    if not os.path.isdir(base):
        if not sources:
            notes.append(f"no L0 corpus directory for {lang}: {base}")
        return sections, notes
    for dirpath, dirs, files in os.walk(base):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, base)
            low = name.lower()
            if len(sections) >= max_sections:
                return sections, notes
            if name == "manifest.json":
                continue
            if low.endswith(".pdf"):
                notes.append(f"skipped {rel}: PDF (no structure survives extraction)")
            elif low.endswith(_TAR_EXT):
                try:
                    with tarfile.open(full, "r|*") as tf:       # streaming
                        for m in tf:
                            if len(sections) >= max_sections:
                                break
                            if not m.isreg() or not m.name.lower().endswith(_TEXT_EXT):
                                continue
                            if not rx.search(m.name):
                                continue
                            fh = tf.extractfile(m)
                            if fh is not None:
                                add(f"{rel}::{m.name}", fh.read(MAX_MEMBER_BYTES))
                except (tarfile.TarError, OSError, EOFError) as e:
                    notes.append(f"skipped {rel}: unreadable archive ({e})")
            elif low.endswith(_TEXT_EXT) and rx.search(rel):
                try:
                    with open(full, "rb") as fh:
                        add(rel, fh.read(MAX_MEMBER_BYTES))
                except OSError as e:
                    notes.append(f"skipped {rel}: {e}")
    return sections, notes


def chunks(text: str, size: int) -> list[str]:
    """Paragraph-boundary chunks of at most `size` characters."""
    out, cur = [], ""
    for para in text.split("\n\n"):
        para = para[:size]
        if cur and len(cur) + len(para) + 2 > size:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


# --------------------------------------------------------------------------
# the prompt
# --------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You extract CHECKABLE migration facts about a programming language from "
    "its own release documentation. You answer with one JSON object and "
    "nothing else. Every claim you draft will be compiled: the OLD snippet "
    "must FAIL and the NEW snippet must COMPILE on the installed toolchain, "
    "or the claim is discarded. Do not guess; draft fewer claims rather than "
    "unsure ones.")

_FORMAT = '''Return exactly this JSON shape (no prose, no markdown fence):

{"claims": [
  {"id": "short-kebab-case-id",
   "topic": "the API or feature, e.g. \\"ArrayList\\" or \\"std::ranges\\"",
   "summary": "ONE line a programmer can act on: what is gone/changed and what to write instead, with the exact names in `backticks`",
   "since": "the version where this changed, e.g. \\"3.12\\"",
   "old": ["a COMPLETE small program or statement that USED to work and must now FAIL to compile"],
   "new": ["the COMPLETE replacement that must COMPILE"]%(variant_help)s}
]}

Rules:
- Snippets are code, inline, standard library only, no file or network access,
  no #include / import of anything outside the standard library.
- "summary" is mandatory: a claim without it cannot be delivered.
- If the documentation below describes nothing that stopped compiling, return {"claims": []}.'''

_VARIANT_HELP = ''',
   "old_variant": "%(old)s", "new_variant": "%(new)s"   (ONLY for "this needs a newer language level": then give "new" only, leave "old" empty, and the same code is compiled under both levels)'''


def build_prompt(lang: str, version: str, section: dict, chunk: str,
                 l1_lines: list[str], known: list[str]) -> str:
    drv = D.driver_for(lang)
    vh = ""
    if drv is not None and drv.variant_kind:
        example = {"std": ("c++17", "c++20") if lang == "cpp" else ("c17", "c23"),
                   "edition": ("2018", "2021")}.get(drv.variant_kind, ("", ""))
        vh = _VARIANT_HELP % {"old": example[0], "new": example[1]}
    parts = [f"Language: {lang}. Installed toolchain (the judge): {version}.",
             _FORMAT % {"variant_help": vh}]
    if l1_lines:
        parts.append("What the installed toolchain reports about itself "
                     "(GENERATED, trustworthy):\n"
                     + "\n".join(f"- {ln}" for ln in l1_lines))
    if known:
        parts.append("Already covered — do NOT draft these again:\n"
                     + "\n".join(f"- {k}" for k in known))
    head = "\n\n".join(parts)
    doc = f"Documentation excerpt ({section['origin']}):\n<<<\n{chunk}\n>>>"
    return head + "\n\n" + doc


def plan_prompts(lang: str, version: str, sections: list[dict],
                 l1_lines: list[str], known: list[str],
                 max_prompts: int = MAX_PROMPTS,
                 max_chars: int = MAX_PROMPT_CHARS) -> list[dict]:
    """[{origin, prompt}], each at most `max_chars`. The fixed part is sized
    first and the excerpt gets what is left — a budget counted the other way
    round is how a cap ends up being a suggestion."""
    # L1 and the known-list are context, not the payload: bound them first.
    l1 = _fit(l1_lines, max_chars // 6)
    kn = _fit(known, max_chars // 6)
    overhead = len(build_prompt(lang, version, {"origin": "x" * 120}, "", l1, kn))
    room = max_chars - overhead
    if room < 500:
        raise SynthError(f"--max-prompt-chars {max_chars} leaves no room for "
                         f"any documentation after the instructions", 2)
    out = []
    for sec in sections:
        for ch in chunks(sec["text"], room):
            if len(out) >= max_prompts:
                return out
            sec_short = {"origin": sec["origin"][-120:]}
            out.append({"origin": sec["origin"],
                        "prompt": build_prompt(lang, version, sec_short, ch, l1, kn)})
    return out


def _fit(lines: list[str], budget: int) -> list[str]:
    kept, total = [], 0
    for ln in lines:
        if total + len(ln) + 3 > budget:
            break
        kept.append(ln)
        total += len(ln) + 3
    return kept


# --------------------------------------------------------------------------
# parsing: strict, defensive, never a repair
# --------------------------------------------------------------------------

def _balanced_objects(text: str) -> tuple[list[str], str]:
    """(every top-level balanced {...} span, the UNCLOSED tail or "").
    String-aware, so a brace inside a snippet does not end the object."""
    spans, depth, start, in_str, esc = [], 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth:
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                spans.append(text[start:i + 1])
    return spans, (text[start:] if depth else "")


def _looks_like_record(doc) -> bool:
    return isinstance(doc, dict) and ("new" in doc or "old" in doc)


def _from_span(span: str, descend: bool) -> tuple[list, int]:
    try:
        doc = json.loads(span)
    except ValueError:
        if not descend:
            return [], 1
        # The ENVELOPE is broken (one trailing comma anywhere kills it). Look
        # one level down: a record that parses exactly as written is still
        # what the model said; one that does not costs itself, not its
        # neighbours.
        inner, tail = _balanced_objects(span[1:-1])
        records, bad = [], (1 if tail or not inner else 0)
        for s in inner:
            got, b = _from_span(s, descend=False)
            records += [r for r in got if _looks_like_record(r)]
            bad += b
        return records, bad
    if isinstance(doc, dict) and "claims" in doc:
        if isinstance(doc["claims"], list):
            return list(doc["claims"]), 0
        return [], 1                           # "claims" is not a list
    if _looks_like_record(doc):
        return [doc], 0                        # a bare record, no envelope
    return [], 0                               # some other object: not a candidate


def extract_records(text: str) -> tuple[list, int]:
    """(raw records, malformed count) from whatever the model said.

    The envelope is allowed to be junk — prose before and after, a markdown
    fence, a <think> block, a reply cut off mid-record. A RECORD is not: it
    is taken only if it parses as JSON exactly as written. Nothing is ever
    edited to make it parse.
    """
    text = re.sub(r"(?is)<think>.*?</think>", " ", text or "")
    spans, tail = _balanced_objects(text)
    records: list = []
    malformed = 0
    for span in spans:
        got, bad = _from_span(span, descend=True)
        records += got
        malformed += bad
    if tail:
        # Cut off mid-object (max_tokens). Whole records BEFORE the cut are
        # kept; the half record is one malformed candidate, never completed.
        inner, _ = _balanced_objects(tail[1:])
        for s in inner:
            got, bad = _from_span(s, descend=False)
            records += [r for r in got if _looks_like_record(r)]
            malformed += bad
        malformed += 1
    if not spans and not tail and text.strip():
        malformed += 1                         # all prose: one failed draft
    return records, malformed


def _snippets(value, what: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_SNIPPETS_PER_SIDE:
        raise ValueError(f"`{what}` must be a list of at most "
                         f"{MAX_SNIPPETS_PER_SIDE} snippets")
    out = []
    for s in value:
        if not isinstance(s, str) or not s.strip():
            raise ValueError(f"`{what}` holds something that is not code")
        if len(s) > MAX_SNIPPET_CHARS:
            raise ValueError(f"a `{what}` snippet is over {MAX_SNIPPET_CHARS} chars")
        if "\x00" in s:
            raise ValueError(f"a `{what}` snippet contains a NUL")
        out.append(s)
    return out


def names_a_file(snippet: str) -> bool:
    """Would verify._read() OPEN this instead of compiling it? (See the module
    docstring: for a drafted claim that is a file-read primitive.)"""
    return "\n" not in snippet and snippet.endswith(_FIXTURE_EXT)


def validate(rec, lang: str) -> dict:
    """A clean claim record, or ValueError saying why this one is dropped.
    Takes fields; never invents content. (The id is bookkeeping, not content:
    it is derived from the topic when the model gave none.)"""
    if not isinstance(rec, dict):
        raise ValueError("not a JSON object")
    if rec.get("lang") not in (None, lang):
        raise ValueError(f"drafted for {rec.get('lang')!r}, this run is {lang}")
    topic = rec.get("topic")
    if not isinstance(topic, str) or not topic.strip() or len(topic) > 80:
        raise ValueError("no usable `topic`")
    summary = rec.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("no `summary` — verifiable but undeliverable")
    summary = " ".join(summary.split())
    if len(summary) > MAX_SUMMARY_CHARS:
        raise ValueError(f"`summary` is over {MAX_SUMMARY_CHARS} chars — it is one line")
    new = _snippets(rec.get("new"), "new")
    old = _snippets(rec.get("old"), "old")
    if not new:
        raise ValueError("no `new` form")
    for s in new + old:
        if names_a_file(s):
            raise ValueError("a snippet names a FILE, not code — refused "
                             "(the verifier would have read it)")
    out = {"id": "", "lang": lang, "topic": topic.strip(), "summary": summary,
           "old": old, "new": new}
    ov, nv = rec.get("old_variant"), rec.get("new_variant")
    if ov is not None or nv is not None:
        drv = D.driver_for(lang)
        if drv is None or not drv.variant_kind:
            raise ValueError(f"{lang} has no language-level axis; variants refused")
        for v in (ov, nv):
            if not isinstance(v, str) or not _VARIANT_RE.fullmatch(v):
                raise ValueError(f"bad variant {v!r}")
        out["old_variant"], out["new_variant"] = ov, nv
    if not old and out.get("old_variant") == out.get("new_variant"):
        # verify() would call this VERIFIED ("no old form given"). For a
        # hand-written claim that is a choice; for a drafted one it means the
        # model asserted a break and showed no evidence of one.
        raise ValueError("no OLD form: a drafted claim must show what stopped working")
    cid = rec.get("id")
    if not isinstance(cid, str) or not _ID_RE.fullmatch(cid):
        cid = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:48] or "claim"
    out["id"] = cid
    if isinstance(rec.get("since"), str) and len(rec["since"]) <= 24:
        out["since"] = rec["since"].strip()
    return out


# --------------------------------------------------------------------------
# duplicates
# --------------------------------------------------------------------------

def _norm(snips: list[str]) -> tuple:
    return tuple(sorted(" ".join(s.split()) for s in snips))


def _as_claim(raw: dict, base: str) -> V.Claim:
    # Belt under names_a_file(): with a trailing newline no snippet can be
    # mistaken for a fixture path, whatever _read's rule becomes.
    safe = dict(raw)
    for side in ("old", "new"):
        safe[side] = [s if s.endswith("\n") else s + "\n" for s in raw.get(side) or []]
    return V.Claim(safe, "synthesized", base)


def duplicate_of(cand: V.Claim, existing: list[V.Claim]) -> str | None:
    """The id of the claim `cand` repeats, or None. Same code, or the same
    topic about (some of) the same names — reference.identifiers, so "same"
    means here what it means to the lookup tool."""
    ci, ct = R.identifiers(cand), (cand.topic or "").casefold()
    for e in existing:
        if e.lang != cand.lang:
            continue
        if _norm(e.new) == _norm(cand.new) and _norm(e.old) == _norm(cand.old):
            return e.id
        if ct and ct == (e.topic or "").casefold() and ci & R.identifiers(e):
            return e.id
    return None


def _staged_claims(lang: str) -> list[V.Claim]:
    d = staging_dir()
    return V.load_claims(d, lang) if os.path.isdir(d) else []


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def _classify(result: dict) -> tuple[str, str]:
    verdict, detail = result["verdict"], result.get("detail", "")
    if verdict == V.VERIFIED:
        return VERIFIED, detail
    if verdict == V.NOT_A_BREAK:
        return NOT_A_BREAK, detail
    if verdict == V.UNVERIFIABLE:
        # verify() folds a refusal and a timeout into one verdict because
        # neither is the toolchain's opinion. The report keeps them apart: a
        # refusal is a fact about the CANDIDATE, a timeout about the machine.
        return (REFUSED if "refused" in detail else TRANSIENT), detail
    return REJECTED, f"{verdict}: {detail}"


def draft_run(lang: str, client, sections: list[dict], *,
              max_prompts: int = MAX_PROMPTS, max_candidates: int = MAX_CANDIDATES,
              max_prompt_chars: int = MAX_PROMPT_CHARS, dry_run: bool = False,
              notes: list[str] | None = None, today: str | None = None) -> dict:
    """One synthesis run. Returns the report; writes the staging file."""
    drv = D.driver_for(lang)
    if drv is None or not drv.available():
        raise SynthError(f"no {lang} toolchain on this machine — a drafted "
                         f"claim could only ever be UNVERIFIABLE here", 3)
    version = drv.version() or "unknown"
    if not sections:
        raise SynthError(
            f"no source material for {lang}: nothing under "
            f"{os.path.join(library_root(), lang)} matched. Acquire it with "
            f"./scripts/lang-library.sh acquire {lang}, widen --match, or "
            f"pass --source FILE. Nothing is drafted from thin air.", 3)
    shipped = [c for c in V.load_claims(claims_dir()) if c.lang == lang]
    existing = shipped + _staged_claims(lang)
    known = [f"{c.topic}: {c.summary}" for c in existing if c.summary]
    try:
        from lang import introspect as I         # noqa: PLC0415
        l1 = list(I.render(lang))
    except Exception:                             # noqa: BLE001
        l1 = []
    # Sections that NAME the installed release go first (whatsnew/3.14.txt
    # before whatsnew/2.0.txt): the prompt budget is small, and the newest
    # notes are where a model's priors are most likely to be stale. Stable,
    # and mechanical — it reads the path, not the prose.
    mm = re.match(r"\d+\.\d+", P._short_version(version) or "")
    if mm:
        sections = sorted(sections, key=lambda sec: mm.group(0) not in sec["origin"])
    prompts = plan_prompts(lang, version, sections, l1, known,
                           max_prompts, max_prompt_chars)
    report = {"lang": lang, "toolchain": version,
              "started": _dt.datetime.now().isoformat(timespec="seconds"),
              "dry_run": dry_run, "prompts": len(prompts),
              "sections": [s["origin"] for s in sections], "notes": notes or [],
              "counts": {k: 0 for k in (DRAFTED, MALFORMED, DUPLICATE, REFUSED,
                                        TRANSIENT, NOT_A_BREAK, REJECTED,
                                        OVER_BUDGET, VERIFIED)},
              "calls_failed": 0, "candidates": [], "staging_file": None}
    if dry_run:
        report["would_send"] = [p["prompt"] for p in prompts]
        return report

    staged: list[dict] = []
    counts = report["counts"]
    with tempfile.TemporaryDirectory(prefix="beastlang-synth-") as base:
        for p in prompts:
            try:
                text = client.draft(p["prompt"])
            except Exception as e:                # noqa: BLE001 — one prompt, not the run
                report["notes"].append(f"{p['origin']}: the model call failed ({e})")
                report["calls_failed"] += 1
                continue
            records, bad = extract_records(text if isinstance(text, str) else "")
            counts[MALFORMED] += bad
            for rec in records:
                counts[DRAFTED] += 1
                rid = rec.get("id") if isinstance(rec, dict) else None
                entry = {"origin": p["origin"],
                         "id": rid[:64] if isinstance(rid, str) else None}
                report["candidates"].append(entry)
                if counts[DRAFTED] > max_candidates:
                    entry.update(outcome=OVER_BUDGET,
                                 reason=f"over --max-candidates {max_candidates}")
                    counts[OVER_BUDGET] += 1
                    continue
                try:
                    raw = validate(rec, lang)
                except ValueError as e:
                    # A refusal that validation makes (a snippet that names a
                    # file) is reported as a refusal, not as bad JSON.
                    kind = REFUSED if "refused" in str(e) else MALFORMED
                    entry.update(outcome=kind, reason=str(e))
                    counts[kind] += 1
                    continue
                cand = _as_claim(raw, base)
                entry["id"] = raw["id"]
                dup = duplicate_of(cand, existing)
                if dup:
                    entry.update(outcome=DUPLICATE, reason=f"repeats {dup}")
                    counts[DUPLICATE] += 1
                    continue
                outcome, why = _classify(V.verify(cand))
                entry.update(outcome=outcome, reason=why)
                counts[outcome] += 1
                if outcome != VERIFIED:
                    continue
                taken = {c.id for c in existing}
                n, cid = 2, raw["id"]
                while cid in taken:               # bookkeeping, not content
                    cid, n = f"{raw['id']}-s{n}", n + 1
                raw["id"] = entry["id"] = cid
                raw["source"] = p["origin"]
                raw["drafted_by"] = getattr(client, "model", client.__class__.__name__)
                staged.append(raw)
                existing.append(_as_claim(raw, base))
    if staged:
        report["staging_file"] = _write_staging(lang, version, staged, today)
    return report


def _atomic_write(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def _write_staging(lang: str, version: str, staged: list[dict],
                   today: str | None = None) -> str:
    today = today or _dt.date.today().strftime("%Y%m%d")
    path = os.path.join(staging_dir(), f"{lang}-{today}.json")
    doc = {"_comment": (
        "STAGING — drafted by a model, VERIFIED on the toolchain named below, "
        "NOT YET REVIEWED and not served: verify.load_claims does not read "
        "this directory. Review each claim (is the summary TRUE and useful, "
        "not merely compilable?), delete the ones that are not, then "
        "`scripts/lang-synthesize.sh promote <this file> --all`."),
        "lang": lang, "toolchain": version, "claims": []}
    try:
        with open(path, encoding="utf-8") as fh:
            have = json.load(fh)
        if isinstance(have, dict) and isinstance(have.get("claims"), list):
            doc["claims"] = have["claims"]          # a second run the same day
    except (OSError, ValueError):
        pass
    doc["claims"] += staged
    _atomic_write(path, doc)
    return path


def summary_line(report: dict) -> str:
    c = report["counts"]
    return (f"{report['lang']} ({report['toolchain']}): {report['prompts']} prompt(s) "
            f"-> drafted {c[DRAFTED]} / malformed {c[MALFORMED]} / refused "
            f"{c[REFUSED]} / not-a-break {c[NOT_A_BREAK]} / duplicate "
            f"{c[DUPLICATE]} / rejected {c[REJECTED]} / transient {c[TRANSIENT]}"
            + (f" / over-budget {c[OVER_BUDGET]}" if c[OVER_BUDGET] else "")
            + f" / VERIFIED {c[VERIFIED]}")


# --------------------------------------------------------------------------
# promote
# --------------------------------------------------------------------------

def _rebuild_index(lang: str) -> None:
    from lang import escalate as E               # noqa: PLC0415
    E.write_index(E.build_index([lang]))


def promote(staging_file: str, ids: list[str] | None = None) -> tuple[int, list[str]]:
    """Move reviewed claims from staging into the shipped set.

    ALL OR NOTHING. Every selected claim is re-verified first; if any one is
    no longer VERIFIED (the toolchain moved, someone edited the snippet) or
    now repeats a shipped claim, nothing is written and the messages say
    which. A partial promote would leave a staging file that half-describes
    what shipped.
    """
    try:
        with open(staging_file, encoding="utf-8") as fh:
            doc = json.load(fh)
        lang = doc["lang"]
        raws = [r for r in doc["claims"] if isinstance(r, dict)]
    except (OSError, ValueError, KeyError, TypeError) as e:
        return 1, [f"{staging_file}: not a staging file ({e})"]
    if os.path.dirname(os.path.abspath(staging_file)) != os.path.abspath(staging_dir()):
        return 1, [f"{staging_file} is not in {staging_dir()} — promote only "
                   f"moves claims out of staging"]
    chosen = [r for r in raws if not ids or r.get("id") in ids]
    missing = sorted(set(ids or []) - {r.get("id") for r in chosen})
    if missing or not chosen:
        return 1, [f"no such staged claim: {', '.join(missing) or '(file is empty)'}"]
    shipped = [c for c in V.load_claims(claims_dir()) if c.lang == lang]
    msgs, bad, clean = [], 0, []
    with tempfile.TemporaryDirectory(prefix="beastlang-synth-") as base:
        for r in chosen:
            try:
                raw = validate(r, lang)        # a hand edit is input too
                for k in ("source", "drafted_by", "since"):
                    if isinstance(r.get(k), str):
                        raw[k] = r[k]
                raw["id"] = r["id"]
                cand = _as_claim(raw, base)
            except (ValueError, KeyError, TypeError) as e:
                msgs.append(f"REFUSED {r.get('id', '?')}: {e}")
                bad += 1
                continue
            dup = duplicate_of(cand, shipped)
            if dup or any(c.id == cand.id for c in shipped):
                msgs.append(f"REFUSED {cand.id}: repeats shipped claim {dup or cand.id}")
                bad += 1
                continue
            outcome, why = _classify(V.verify(cand))
            if outcome != VERIFIED:
                msgs.append(f"REFUSED {cand.id}: no longer verifies here — {outcome}: {why}")
                bad += 1
                continue
            clean.append(raw)
            shipped.append(cand)
    if bad:
        return 1, msgs + ["nothing was promoted (all or nothing)."]
    target = os.path.join(claims_dir(), f"{lang}{SHIPPED_SUFFIX}")
    out = {"_comment": (
        "Claims DRAFTED by a model (agents/lang/synthesize.py), VERIFIED on "
        "the installed toolchain, reviewed by a person and promoted. Kept "
        "apart from the hand-authored sets so their origin stays visible."),
        "lang": lang, "claims": []}
    try:
        with open(target, encoding="utf-8") as fh:
            have = json.load(fh)
        if isinstance(have, dict) and isinstance(have.get("claims"), list):
            out["claims"] = have["claims"]
    except (OSError, ValueError):
        pass
    out["claims"] += clean
    _atomic_write(target, out)
    rest = [r for r in raws if r not in chosen]
    if rest:
        doc["claims"] = rest
        _atomic_write(staging_file, doc)
    else:
        os.unlink(staging_file)
    msgs.append(f"promoted {len(clean)} claim(s) -> {os.path.relpath(target, _REPO)}")
    try:
        _rebuild_index(lang)
        msgs.append(f"escalation index rebuilt for {lang}")
    except Exception as e:                        # noqa: BLE001
        msgs.append(f"! the escalation index was NOT rebuilt ({e}) — run "
                    f"python3 agents/lang/escalate.py --rebuild --lang {lang}")
        return 1, msgs
    return 0, msgs


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _report_path(lang: str) -> str:
    run_dir = os.environ.get("OPENBEAST_RUN_DIR") or os.path.join(_REPO, ".run")
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(run_dir, "lang-synth", f"{lang}-{stamp}.json")


def main(argv: list[str] | None = None, client=None,
         lease_fn=check_lease) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft", help="draft candidate claims and verify them into staging")
    d.add_argument("lang")
    d.add_argument("--match", help=f"regex over corpus paths (default: {DEFAULT_MATCH})")
    d.add_argument("--source", action="append", default=[],
                   help="an explicit source file (repeatable); not pattern-filtered")
    d.add_argument("--max-prompts", type=int, default=MAX_PROMPTS)
    d.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES)
    d.add_argument("--max-prompt-chars", type=int, default=MAX_PROMPT_CHARS)
    d.add_argument("--dry-run", action="store_true",
                   help="print the prompts that WOULD be sent; no model call, "
                        "no candidate compiled, nothing written")
    d.add_argument("--ignore-lease", action="store_true")
    d.add_argument("--report", help="where to write the JSON run report")
    pr = sub.add_parser("promote", help="move REVIEWED staging claims into the shipped set")
    pr.add_argument("staging_file")
    pr.add_argument("--id", action="append", default=[])
    pr.add_argument("--all", action="store_true")
    sub.add_parser("status", help="what is waiting in staging")
    a = ap.parse_args(argv)

    if a.cmd == "status":
        sd = staging_dir()
        names = sorted(n for n in os.listdir(sd) if n.endswith(".json")) \
            if os.path.isdir(sd) else []
        if not names:
            print(f"nothing staged ({os.path.relpath(sd, _REPO)} is empty)")
        for n in names:
            cs = V.load_claims(os.path.join(sd, n))
            print(f"{n}: {len(cs)} claim(s) awaiting review")
            for c in cs:
                print(f"  {c.id}: {c.summary}")
        return 0

    if a.cmd == "promote":
        if not a.id and not a.all:
            print("promote needs --id ID (repeatable) or --all", file=sys.stderr)
            return 2
        rc, msgs = promote(a.staging_file, a.id or None)
        print("\n".join(msgs), file=sys.stderr if rc else sys.stdout)
        return rc

    try:
        if a.max_prompts < 1 or a.max_candidates < 1:
            raise SynthError("--max-prompts and --max-candidates must be >= 1")
        sections, notes = corpus_sections(a.lang, match=a.match, sources=a.source)
        if not a.dry_run:
            # Configuration before the card: a missing URL is a typo, and it
            # should not take a query of the GPU to find that out.
            if client is None:
                client = client_from_env()
            ok, why = lease_fn(a.ignore_lease)
            print(why, file=sys.stderr)
            if not ok:
                return 4
        report = draft_run(a.lang, client, sections, max_prompts=a.max_prompts,
                           max_candidates=a.max_candidates,
                           max_prompt_chars=a.max_prompt_chars,
                           dry_run=a.dry_run, notes=notes)
    except re.error as e:
        print(f"Error: --match is not a valid regex: {e}", file=sys.stderr)
        return 2
    except SynthError as e:
        print(f"Error: {e}", file=sys.stderr)
        return e.code
    for n in report["notes"]:
        print(f"note: {n}", file=sys.stderr)
    if a.dry_run:
        print(f"===== system prompt (sent with every request) =====\n{SYSTEM_PROMPT}\n")
        for i, p in enumerate(report["would_send"], 1):
            print(f"===== prompt {i}/{report['prompts']} ({len(p)} chars) =====\n{p}\n")
        print(f"dry run: {report['prompts']} prompt(s) WOULD be sent; nothing "
              f"was sent, no candidate was compiled, nothing was written.")
        return 0
    path = a.report or _report_path(a.lang)
    _atomic_write(path, report)
    print(summary_line(report))
    for c in report["candidates"]:
        if c["outcome"] != VERIFIED:
            print(f"  {c['outcome']:12} {c.get('id') or '?'}: {c['reason'][:140]}")
    if report["staging_file"]:
        print(f"staged for REVIEW (not served): "
              f"{os.path.relpath(report['staging_file'], _REPO)}")
    print(f"report: {path}")
    if report["prompts"] and report["calls_failed"] == report["prompts"]:
        print("Error: every model call failed — nothing was drafted.", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
