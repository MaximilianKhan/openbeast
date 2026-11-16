"""beast-docs configuration: the sources file, the store root, offline state.

Stdlib only, and importable on Python 3.9 (a Mac's /usr/bin/python3): this
package ships to client mode, where `scripts/docs.sh` may run under whatever
python the laptop has. `tomllib` is used when present (3.11+); below that, a
deliberately small parser reads the subset `sources.toml` is written in —
`[[source]]` tables of strings, numbers, booleans and string arrays — and
REFUSES anything else rather than guessing at it.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from . import urls as U

HERE = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.dirname(HERE)
REPO_DIR = os.path.dirname(AGENTS_DIR)
DEFAULT_SOURCES = os.path.join(HERE, "sources.toml")

ADAPTERS = ("apple-docc-json", "html", "markdown")
DISCOVERY = ("links", "llms-txt", "sitemap")
UA_TOKEN = "openbeast-docs"
USER_AGENT = (f"{UA_TOKEN}/1.0 (offline documentation mirror; polite crawler; "
              f"+https://github.com/MaximilianKhan/openbeast)")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


class ConfigError(ValueError):
    pass


# ── TOML ─────────────────────────────────────────────────────────────────

def _parse_value(raw: str, where: str):
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            # A TOML basic string's escapes are a subset of JSON's for the
            # characters this file uses; json.loads also refuses a stray quote.
            return json.loads(raw)
        except ValueError as e:
            raise ConfigError(f"{where}: bad string {raw!r}: {e}") from None
    if raw.startswith("["):
        inner = raw[1:-1].strip() if raw.endswith("]") else None
        if inner is None:
            raise ConfigError(f"{where}: unterminated array")
        if not inner:
            return []
        out, buf, in_str, esc = [], "", False, False
        for ch in inner:
            if in_str:
                buf += ch
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str, buf = True, buf + ch
            elif ch == ",":
                if buf.strip():
                    out.append(_parse_value(buf, where))
                buf = ""
            else:
                buf += ch
        if buf.strip():
            out.append(_parse_value(buf, where))
        return out
    if raw in ("true", "false"):
        return raw == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    raise ConfigError(f"{where}: unsupported TOML value {raw!r}")


def _strip_comment(line: str) -> str:
    in_str, esc = False, False
    for i, ch in enumerate(line):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "#":
            return line[:i]
    return line


def parse_toml_subset(text: str) -> dict:
    """The fallback parser (no tomllib). Same result shape as tomllib.loads
    for the subset sources.toml uses; anything outside it is a ConfigError."""
    doc: dict = {}
    cur: Optional[dict] = None
    pending = ""
    pending_at = 0
    for n, line in enumerate(text.splitlines(), 1):
        line = _strip_comment(line).rstrip()
        if pending:                     # a multi-line array continues
            pending += " " + line.strip()
            if pending.count("[") == pending.count("]"):
                k, v = pending.split("=", 1)
                (cur if cur is not None else doc)[k.strip()] = _parse_value(v, f"line {pending_at}")
                pending = ""
            continue
        if not line.strip():
            continue
        s = line.strip()
        m = re.fullmatch(r"\[\[([A-Za-z0-9_-]+)\]\]", s)
        if m:
            cur = {}
            doc.setdefault(m.group(1), []).append(cur)
            continue
        if s.startswith("["):
            raise ConfigError(f"line {n}: only [[array-of-tables]] are supported")
        if "=" not in s:
            raise ConfigError(f"line {n}: expected key = value")
        k, v = s.split("=", 1)
        k = k.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", k):
            raise ConfigError(f"line {n}: bad key {k!r}")
        if v.strip().startswith("[") and v.count("[") != v.count("]"):
            pending, pending_at = s, n
            continue
        target = cur if cur is not None else doc
        if k in target:
            raise ConfigError(f"line {n}: duplicate key {k!r}")
        target[k] = _parse_value(v, f"line {n}")
    if pending:
        raise ConfigError(f"line {pending_at}: unterminated array")
    return doc


def load_toml(text: str) -> dict:
    try:
        import tomllib                     # noqa: PLC0415  (3.11+)
    except ImportError:
        return parse_toml_subset(text)
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(str(e)) from None


# ── sources ──────────────────────────────────────────────────────────────

@dataclass
class Source:
    name: str
    title: str
    adapter: str
    hosts: list
    roots: list
    allow_prefixes: list
    deny_prefixes: list = field(default_factory=list)
    discovery: str = "links"
    index_url: str = ""
    markdown_suffix: str = ".md.txt"
    content_selectors: list = field(default_factory=list)
    strip_selectors: list = field(default_factory=list)
    max_pages: int = 200
    max_depth: int = 4
    delay: float = 1.0
    refresh_days: int = 30
    license: str = ""
    robots_note: str = ""
    requires_ack: bool = False

    def in_scope(self, url: str) -> bool:
        """Host AND port in `hosts`, path under an allow prefix and under no
        deny prefix — decided on the NORMALIZED URL (agents/docs/urls.py), so
        `/docs/../secret` and `/docs/%2e%2e/secret` are judged as `/secret`,
        which is what a server would serve for them."""
        from urllib.parse import urlsplit  # noqa: PLC0415
        n = U.normalize_url(url)
        if not n or not U.host_allowed(self.hosts, n):
            return False
        low = (urlsplit(n).path or "/").lower()
        if any(low.startswith(d.lower()) for d in self.deny_prefixes):
            return False
        return any(low.startswith(a.lower()) for a in self.allow_prefixes)


_KNOWN = set(Source.__dataclass_fields__)


def _validate(raw: dict, i: int) -> Source:
    where = f"source #{i + 1}"
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ConfigError(f"{where}: name must match {_NAME_RE.pattern}")
    where = f"source {name!r}"
    unknown = set(raw) - _KNOWN
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
    for req in ("adapter", "hosts", "roots", "allow_prefixes"):
        if not raw.get(req):
            raise ConfigError(f"{where}: {req} is required")
    if raw["adapter"] not in ADAPTERS:
        raise ConfigError(f"{where}: adapter must be one of {ADAPTERS}")
    if raw.get("discovery", "links") not in DISCOVERY:
        raise ConfigError(f"{where}: discovery must be one of {DISCOVERY}")
    for k in ("hosts", "roots", "allow_prefixes", "deny_prefixes",
              "content_selectors", "strip_selectors"):
        v = raw.get(k, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ConfigError(f"{where}: {k} must be a list of strings")
    for h in raw["hosts"]:
        if not U.parse_host_entry(h):
            raise ConfigError(f"{where}: host {h!r} must be a host name, optionally with :port")
    src = Source(title=raw.get("title", name), **{k: v for k, v in raw.items() if k != "title"})
    if src.discovery != "links" and not src.index_url:
        raise ConfigError(f"{where}: discovery = {src.discovery} needs index_url")
    src.max_pages, src.max_depth = int(src.max_pages), int(src.max_depth)
    src.delay, src.refresh_days = float(src.delay), int(src.refresh_days)
    if src.delay < 0 or src.max_pages < 0 or src.max_depth < 0 or src.refresh_days < 1:
        raise ConfigError(f"{where}: delay/max_pages/max_depth must be >= 0, refresh_days >= 1")
    for r in src.roots:
        if not src.in_scope(r):
            raise ConfigError(f"{where}: root {r} is outside hosts/allow_prefixes")
    if src.index_url and not U.host_allowed(src.hosts, src.index_url):
        raise ConfigError(f"{where}: index_url host is not in hosts")
    src.requires_ack = bool(src.requires_ack)
    return src


def sources_path() -> str:
    return os.environ.get("OPENBEAST_DOCS_SOURCES") or DEFAULT_SOURCES


def load_sources(path: Optional[str] = None) -> dict:
    """{name: Source}, in file order. Raises ConfigError listing the problem."""
    path = path or sources_path()
    try:
        with open(path, encoding="utf-8") as fh:
            doc = load_toml(fh.read())
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from None
    out: dict = {}
    for i, raw in enumerate(doc.get("source") or []):
        src = _validate(raw, i)
        if src.name in out:
            raise ConfigError(f"duplicate source name {src.name!r}")
        out[src.name] = src
    return out


# ── the store ────────────────────────────────────────────────────────────

def store_root() -> str:
    """Where mirrors live. OUTSIDE git, like beast-lang's library: the
    content belongs to Apple / Google / whoever; we ship the acquirer."""
    return os.path.abspath(os.path.expanduser(
        os.environ.get("OPENBEAST_DOCS_DIR") or "~/.openbeast/docs"))


# ── offline / island ─────────────────────────────────────────────────────

def _conf_offline() -> Optional[str]:
    """OFFLINE= in openbeast.conf, parsed the way scripts/lib/conf.sh and
    scripts/lang-library.sh parse it: last assignment wins, quotes stripped,
    FIRST TOKEN ONLY, and only the explicit words mean on. An EMPTY
    OPENBEAST_OFFLINE counts as unset and falls through to the file, as
    `${OPENBEAST_OFFLINE:-$(conf)}` does in both shell parsers."""
    raw = os.environ.get("OPENBEAST_OFFLINE") or None
    if raw is None:
        conf = os.path.join(REPO_DIR, "openbeast.conf")
        try:
            with open(conf, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = re.match(r"^\s*OFFLINE\s*=(.*)$", line)
                    if m:
                        raw = m.group(1)
        except OSError:
            raw = None
    if raw is None:
        return None
    raw = raw.split("#", 1)[0].strip().strip("\"'")
    first = (raw.split() or ["false"])[0].strip("\"'").lower()
    return "OFFLINE=true (this install has no internet route by design)" \
        if first in ("true", "yes", "1", "on") else None


def offline_reason() -> Optional[str]:
    """Why the network must not be used right now, or None.

    Two settings, as in agents/island.py: OFFLINE=true is a property of the
    INSTALL, island mode is a RUNTIME state the watcher writes. Either one
    means "do not wait on the internet"."""
    r = _conf_offline()
    if r:
        return r
    try:
        import sys  # noqa: PLC0415
        if AGENTS_DIR not in sys.path:
            sys.path.insert(0, AGENTS_DIR)
        import island  # noqa: PLC0415
        st = island.read_state()
    except Exception:                      # noqa: BLE001 — never fatal
        return None
    if st and st.get("state") == "island":
        since = st.get("since_iso")
        since = since if isinstance(since, str) and re.fullmatch(r"[0-9T:-]{1,25}", since) else "?"
        return f"ISLAND MODE (no internet route since {since})"
    return None
