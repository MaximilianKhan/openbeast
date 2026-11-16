"""Request-body validation.

A schema is a dict ``{field_name: Field(...)}``. ``validate(body, schema)``
returns a cleaned dict (only known fields, converted values) or raises
ValidationFailed listing every problem as ``{field: reason}``.
"""
from __future__ import annotations

import re

from . import money
from .errors import ValidationFailed

_MISSING = object()


class Field:
    def __init__(self, kind: str, required: bool = True, min=None, max=None,
                 choices=None, pattern: str | None = None, max_len: int | None = None,
                 items=None, nullable: bool = False):
        self.kind = kind  # "str" | "int" | "money" | "bool" | "list" | "object" | "pct"
        self.required = required
        self.min = min
        self.max = max
        self.choices = choices
        self.pattern = re.compile(pattern) if pattern else None
        self.max_len = max_len
        self.items = items  # a schema dict for lists of objects
        self.nullable = nullable

    def clean(self, value, path: str, problems: dict):
        if value is None and self.nullable:
            return None
        k = self.kind
        if k == "str":
            if not isinstance(value, str):
                problems[path] = "must be a string"
                return None
            value = value.strip()
            if not value:
                problems[path] = "must not be empty"
                return None
            if self.max_len is not None and len(value) > self.max_len:
                problems[path] = "longer than %d characters" % self.max_len
                return None
            if self.pattern is not None and not self.pattern.match(value):
                problems[path] = "has an invalid format"
                return None
            if self.choices is not None and value not in self.choices:
                problems[path] = "must be one of " + ", ".join(self.choices)
                return None
            return value
        if k == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                problems[path] = "must be an integer"
                return None
            if self.min is not None and value < self.min:
                problems[path] = "must be >= %d" % self.min
                return None
            if self.max is not None and value > self.max:
                problems[path] = "must be <= %d" % self.max
                return None
            return value
        if k in ("money", "pct"):
            if not isinstance(value, str):
                problems[path] = "must be a decimal string"
                return None
            try:
                d = money.parse(value)
            except money.MoneyFormatError:
                problems[path] = "must be a decimal string with at most 2 decimals"
                return None
            if k == "pct" and not (money.ZERO < d <= money.HUNDRED):
                problems[path] = "must be in (0, 100]"
                return None
            if self.min is not None and d < self.min:
                problems[path] = "must be >= %s" % self.min
                return None
            return d
        if k == "bool":
            if not isinstance(value, bool):
                problems[path] = "must be true or false"
                return None
            return value
        if k == "list":
            if not isinstance(value, list):
                problems[path] = "must be a list"
                return None
            if self.min is not None and len(value) < self.min:
                problems[path] = "needs at least %d item(s)" % self.min
                return None
            if self.max is not None and len(value) > self.max:
                problems[path] = "has more than %d items" % self.max
                return None
            out = []
            for i, item in enumerate(value):
                if self.items is None:
                    out.append(item)
                    continue
                if not isinstance(item, dict):
                    problems["%s[%d]" % (path, i)] = "must be an object"
                    continue
                out.append(_validate(item, self.items, "%s[%d]." % (path, i), problems))
            return out
        if k == "tags":
            if not isinstance(value, list) or not all(isinstance(t, str) and t.strip() for t in value):
                problems[path] = "must be a list of non-empty strings"
                return None
            return tuple(sorted({t.strip().lower() for t in value}))
        raise AssertionError("unknown field kind %r" % k)


def _validate(body: dict, schema: dict, prefix: str, problems: dict) -> dict:
    out = {}
    for name, field in schema.items():
        value = body.get(name, _MISSING)
        if value is _MISSING:
            if field.required:
                problems[prefix + name] = "is required"
            continue
        cleaned = field.clean(value, prefix + name, problems)
        if prefix + name not in problems:
            out[name] = cleaned
    return out


def validate(body, schema: dict, partial: bool = False) -> dict:
    """Validate a decoded JSON body against ``schema``.

    With ``partial=True`` (PATCH) no field is required but at least one must
    be present.
    """
    if not isinstance(body, dict):
        raise ValidationFailed({"body": "must be a JSON object"})
    problems: dict[str, str] = {}
    if partial:
        schema = {k: _optional(f) for k, f in schema.items()}
        if not any(k in schema for k in body):
            problems["body"] = "needs at least one of " + ", ".join(sorted(schema))
    out = _validate(body, schema, "", problems)
    if problems:
        raise ValidationFailed(problems)
    return out


def _optional(f: Field) -> Field:
    g = Field(f.kind, required=False)
    g.__dict__.update({k: v for k, v in f.__dict__.items() if k != "required"})
    g.required = False
    return g
