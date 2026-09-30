"""Prompt rendering: versioned formats, head+tail truncation, tag escaping.

Prompts reach engines as RAW TEXT (llama.cpp /completion, SGLang /v1/score),
never through a chat endpoint, so the answer boundary — the first assistant
token — is exact (plan §5.4).

Formats:
  qwen3-nothink/1  ChatML with an empty think block; labels have no leading
                   space ("yes"). [HW] must match the served checkpoint's own
                   template — diff it against the GGUF during the P1 probe.
  plain/1          "{system}\\n\\n{body}\\nAnswer:" with labels " yes"/" no".
                   For base models and the stub.

Escaping: user data is DATA. Every delimiter tag the template itself uses
(e.g. <user_turn>), every ChatML/think special, and the MIS delimiter text get a
zero-width space after their '<' so an injected "</user_turn>" or "<|im_end|>"
cannot close the data block. Benign markup (<b>) is left alone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .spec import DecisionSpec, InputSpec

ZW = "​"
SPECIAL_TOKENS = ("<|im_start|>", "<|im_end|>", "<|endoftext|>", "<think>", "</think>")
ELLIPSIS = " … "

_TAG_IN_TEMPLATE = re.compile(r"</?\s*([A-Za-z_][\w\-]*)\s*>")
_SLOT_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_WS_TOKEN = re.compile(r"\S+\s*|\s+")


class RenderError(ValueError):
    """The spec cannot be rendered safely (a template bug)."""


class InputError(ValueError):
    """The caller's inputs do not match the decision's input schema (422)."""


@dataclass
class Rendered:
    format: str
    prompt: str = ""                       # full text for non-rank decisions
    query: str = ""                        # rank: shared prefix
    items: list[str] = field(default_factory=list)   # rank: per-item text
    label_surface: dict[str, str] = field(default_factory=dict)
    truncated_fields: list[str] = field(default_factory=list)
    # Non-rank only: the filled, escaped template WITHOUT the format's wrapper,
    # and the system text — for engines that take (context, question) instead
    # of one raw prompt (openjev_head).
    body: str = ""
    system: str = ""

    @property
    def prefixes(self) -> list[str]:
        """The exact text each label token follows, one per scored row."""
        if self.items:
            return [self.query + it for it in self.items]
        return [self.prompt]


def label_surface(fmt: str, text: str) -> str:
    return (" " + text) if fmt == "plain/1" else text


def template_tags(template: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _TAG_IN_TEMPLATE.finditer(template)})


def escape_field(text: str, tags: list[str], extra: tuple[str, ...] = ()) -> str:
    out = text
    for tok in SPECIAL_TOKENS + tuple(e for e in extra if e):
        out = out.replace(tok, tok[0] + ZW + tok[1:])
    for tag in tags:
        out = re.sub(r"<(\s*/?\s*" + re.escape(tag) + r"\s*>)", "<" + ZW + r"\1",
                     out, flags=re.IGNORECASE)
    return out


def whitespace_pieces(text: str) -> list[str]:
    """The `linear` engine's (and the fallback) token approximation."""
    return _WS_TOKEN.findall(text)


def head_tail(pieces: list[str], head: int, tail: int) -> tuple[str, bool]:
    if len(pieces) <= head + tail:
        return "".join(pieces), False
    h = "".join(pieces[:head]).rstrip()
    t = "".join(pieces[len(pieces) - tail:]).lstrip() if tail else ""
    return h + ELLIPSIS + t, True


def truncate_text(text: str, ispec: InputSpec,
                  splitter: Callable[[str], list[str]] | None = None) -> tuple[str, bool]:
    pieces = (splitter or whitespace_pieces)(text)
    return head_tail(pieces, ispec.head, ispec.tail)


def validate_inputs(spec: DecisionSpec, inputs: Any) -> dict[str, Any]:
    if not isinstance(inputs, dict):
        raise InputError("inputs must be an object")
    unknown = set(inputs) - set(spec.inputs)
    if unknown:
        raise InputError(f"unknown input(s) {sorted(unknown)}")
    missing = set(spec.inputs) - set(inputs)
    if missing:
        raise InputError(f"missing input(s) {sorted(missing)}")
    out = {}
    for name, ispec in spec.inputs.items():
        v = inputs[name]
        if ispec.type == "text":
            if not isinstance(v, str):
                raise InputError(f"input {name!r} must be a string")
            if len(v) > 200_000:
                raise InputError(f"input {name!r} is too large")
        elif ispec.type == "int":
            if isinstance(v, bool) or not isinstance(v, int):
                raise InputError(f"input {name!r} must be an integer")
        elif ispec.type == "bool":
            if not isinstance(v, bool):
                raise InputError(f"input {name!r} must be a bool")
        out[name] = v
    return out


def _format_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _wrap(fmt: str, system: str, body: str) -> tuple[str, str]:
    """(header, footer) around the user body for a format."""
    if fmt == "qwen3-nothink/1":
        return (f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n",
                "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n")
    if fmt == "plain/1":
        return (f"{system}\n\n", "\nAnswer:")
    raise RenderError(f"unknown prompt format {fmt!r}")


def render(spec: DecisionSpec, inputs: dict[str, Any], items: list[str] | None = None, *,
           splitter: Callable[[str], list[str]] | None = None,
           mis_delimiter: str = "") -> Rendered:
    """Render a decision for an LLM engine. `inputs` must already be validated."""
    tags = template_tags(spec.prompt_template)
    extra = (mis_delimiter,) if mis_delimiter else ()
    truncated: list[str] = []
    values: dict[str, str] = {}
    for name, ispec in spec.inputs.items():
        v = inputs[name]
        if ispec.type == "text":
            text, cut = truncate_text(v, ispec, splitter)
            if cut:
                truncated.append(name)
            values[name] = escape_field(text, tags, extra)
        else:
            values[name] = _format_value(v)

    def fill(tmpl: str) -> str:
        # Single pass: a value containing "{x}" is never re-expanded.
        return _SLOT_RE.sub(lambda m: values[m.group(1)] if m.group(1) in values
                            else m.group(0), tmpl)

    header, footer = _wrap(spec.prompt_format, spec.prompt_system, "")
    surfaces = {lb.name: label_surface(spec.prompt_format, lb.text) for lb in spec.labels}
    if spec.type != "rank":
        body = fill(spec.prompt_template)
        return Rendered(spec.prompt_format, prompt=header + body + footer,
                        label_surface=surfaces, truncated_fields=truncated,
                        body=body, system=spec.prompt_system)
    if items is None:
        raise InputError("rank decisions need items")
    before, after = spec.prompt_template.split("{item}", 1)
    query = header + fill(before)
    # Joint and separate tokenization must agree at the split: only split after
    # a newline or the end of a special token (plan §5.4).
    if not (query.endswith("\n") or query.endswith(">")):
        raise RenderError("rank templates must put {item} right after a newline or tag")
    tail = fill(after) + footer
    out_items = []
    for it in items:
        if not isinstance(it, str):
            raise InputError("item text must be a string")
        if mis_delimiter and mis_delimiter in it:
            it = escape_field(it, [], (mis_delimiter,))
        out_items.append(escape_field(it, tags, extra) + tail)
    return Rendered(spec.prompt_format, query=query, items=out_items,
                    label_surface=surfaces, truncated_fields=truncated)


def placeholder_inputs(spec: DecisionSpec) -> dict[str, Any]:
    """Innocuous inputs used to render a representative prefix for label locks."""
    out: dict[str, Any] = {}
    for name, ispec in spec.inputs.items():
        out[name] = {"text": "hello", "int": 0, "bool": False}[ispec.type]
    return out


def linear_text(spec: DecisionSpec, inputs: dict[str, Any]) -> str:
    """The `linear` engine's view of the inputs: truncated text + typed facts."""
    parts = []
    for name, ispec in spec.inputs.items():
        v = inputs[name]
        if ispec.type == "text":
            parts.append(truncate_text(v, ispec)[0])
    return "\n".join(parts)
