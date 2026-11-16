"""The TOML subset the beast-ark catalog is written in, parsed with line numbers.

Stdlib only and importable on Python 3.9 (a Mac's /usr/bin/python3, and the
CPython a bare box restores from the drive before anything else). One parser
on every python, deliberately: `tomllib` exists only on 3.11+, and the editor
below needs to know which LINE each value came from so `ark lock` and
`ark update` can rewrite one pin without reformatting the file a human
reviews. tests/test_ark.py checks this parser against tomllib on the real
catalog whenever tomllib is present.

The subset: comments, `[table]` and `[[array-of-tables]]` headers with bare
keys, and `key = value` where value is a basic "string", a literal 'string',
an integer, a boolean, or an array (single- or multi-line) of those. Anything
else is a TomlError, never a guess.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


class TomlError(ValueError):
    pass


_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _strip_comment(line: str) -> str:
    quote, esc = "", False
    for i, ch in enumerate(line):
        if quote:
            if quote == '"' and esc:
                esc = False
            elif quote == '"' and ch == "\\":
                esc = True
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def _split_array(inner: str, where: str) -> List[str]:
    out, buf, quote, esc, depth = [], "", "", False, 0
    for ch in inner:
        if quote:
            buf += ch
            if quote == '"' and esc:
                esc = False
            elif quote == '"' and ch == "\\":
                esc = True
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote, buf = ch, buf + ch
        elif ch == "[":
            depth += 1
            buf += ch
        elif ch == "]":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            if not buf.strip():
                raise TomlError(f"{where}: empty array element")
            out.append(buf)
            buf = ""
        else:
            buf += ch
    if quote:
        raise TomlError(f"{where}: unterminated string in array")
    if buf.strip():
        out.append(buf)
    return out


def parse_value(raw: str, where: str = "value") -> Any:
    raw = raw.strip()
    if raw.startswith('"'):
        if len(raw) < 2 or not raw.endswith('"'):
            raise TomlError(f"{where}: unterminated string {raw!r}")
        try:
            # TOML basic-string escapes are a subset of JSON's for what this
            # file uses; json.loads also refuses a stray inner quote.
            v = json.loads(raw)
        except ValueError as e:
            raise TomlError(f"{where}: bad string {raw!r}: {e}") from None
        return v
    if raw.startswith("'"):
        if len(raw) < 2 or not raw.endswith("'") or "'" in raw[1:-1]:
            raise TomlError(f"{where}: bad literal string {raw!r}")
        return raw[1:-1]
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise TomlError(f"{where}: unterminated array")
        return [parse_value(x, where) for x in _split_array(raw[1:-1], where)]
    if raw in ("true", "false"):
        return raw == "true"
    if re.fullmatch(r"[+-]?(0|[1-9][0-9_]*)", raw):
        return int(raw.replace("_", ""))
    raise TomlError(f"{where}: unsupported TOML value {raw!r}")


def _balanced(s: str) -> bool:
    """True when every [ opened outside a string is closed."""
    depth, quote, esc = 0, "", False
    for ch in s:
        if quote:
            if quote == '"' and esc:
                esc = False
            elif quote == '"' and ch == "\\":
                esc = True
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
    return depth <= 0


class Table(dict):
    """A table that remembers where it and each of its keys live."""

    def __init__(self, kind: str, header_line: int):
        super().__init__()
        self.kind = kind            # "" (top level), "name" or "[[name]]"
        self.header_line = header_line
        self.lines: Dict[str, Tuple[int, int]] = {}   # key -> (first, last) 1-based
        self.end_line = header_line


def parse(text: str) -> Tuple[dict, List[Table]]:
    """-> (document, every table in file order). Line numbers are 1-based."""
    doc: dict = {}
    root = Table("", 0)
    tables: List[Table] = [root]
    cur: Table = root
    pending: Optional[Tuple[str, str, int]] = None
    lines = text.splitlines()
    for n, full in enumerate(lines, 1):
        line = _strip_comment(full).rstrip()
        if pending:
            k, buf, start = pending
            buf += "\n" + line
            if _balanced(buf):
                cur[k] = parse_value(buf.replace("\n", " "), f"line {start}")
                cur.lines[k] = (start, n)
                cur.end_line = n
                pending = None
            else:
                pending = (k, buf, start)
            continue
        s = line.strip()
        if not s:
            continue
        m = re.fullmatch(r"\[\[\s*([A-Za-z0-9_-]+)\s*\]\]", s)
        if m:
            cur = Table(m.group(1), n)
            cur.kind = f"[[{m.group(1)}]]"
            doc.setdefault(m.group(1), [])
            if not isinstance(doc[m.group(1)], list):
                raise TomlError(f"line {n}: {m.group(1)} is both a table and an array of tables")
            doc[m.group(1)].append(cur)
            tables.append(cur)
            continue
        m = re.fullmatch(r"\[\s*([A-Za-z0-9_-]+)\s*\]", s)
        if m:
            if m.group(1) in doc:
                raise TomlError(f"line {n}: table [{m.group(1)}] defined twice")
            cur = Table(m.group(1), n)
            doc[m.group(1)] = cur
            tables.append(cur)
            continue
        if s.startswith("["):
            raise TomlError(f"line {n}: only [table] and [[array-of-tables]] headers are supported")
        if "=" not in s:
            raise TomlError(f"line {n}: expected key = value")
        k, v = s.split("=", 1)
        k = k.strip()
        if not _KEY.fullmatch(k):
            raise TomlError(f"line {n}: bad key {k!r} (dotted and quoted keys are not supported)")
        if k in cur:
            raise TomlError(f"line {n}: duplicate key {k!r}")
        if v.strip().startswith("[") and not _balanced(v):
            pending = (k, v, n)
            continue
        cur[k] = parse_value(v, f"line {n}")
        cur.lines[k] = (n, n)
        cur.end_line = n
    if pending:
        raise TomlError(f"line {pending[2]}: unterminated array")
    for k, v in root.items():
        doc[k] = v
    return doc, tables


def dumps_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(dumps_value(x) for x in v) + "]"
    raise TomlError(f"cannot write {type(v).__name__}")


def set_key(text: str, table: Table, key: str, value: Any) -> str:
    """Return `text` with `key = value` in `table`: replaced where it stands,
    or inserted after the table's last key. Only that line changes. The
    caller re-parses after each edit (line numbers move on an insert)."""
    lines = text.split("\n")
    new = f"{key} = {dumps_value(value)}"
    if key in table.lines:
        a, b = table.lines[key]
        old = lines[a - 1]
        indent = old[: len(old) - len(old.lstrip())]
        # keep a trailing comment on a one-line value
        tail = ""
        if a == b:
            body = _strip_comment(old)
            if len(body) < len(old):
                tail = "  " + old[len(body):].strip()
        lines[a - 1: b] = [indent + new + tail]
    else:
        at = table.end_line           # 1-based index of the last line of the table
        lines.insert(at, new)
    return "\n".join(lines)
