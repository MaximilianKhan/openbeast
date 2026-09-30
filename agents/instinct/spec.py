"""Decision specs: the TOML registry, its validator, and decision_hash.

One file per decision under agents/instinct/decisions/<id>.toml (plan §5.3).
The validator is strict on purpose — an unknown key is an error — because a
typo'd `policy.act` silently falling back to a default is exactly the kind of
drift that would let an ungated label act. A broken file invalidates ONLY its
own decision (`load_registry` returns it under `errors`); siblings keep
serving.

`decision_hash` pins everything that changes what a probability MEANS: the
wording, the labels and their token ids, the engine, the model bytes and the
execution mode. Thresholds are deliberately NOT in it — they live in the
calibration record, which is keyed by the hash (plan §5.3).
"""
from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODES = ("off", "shadow", "canary", "enforce")
TYPES = ("yes_no", "choice", "score", "rank")
FORMS = ("pointwise", "setwise")
INPUT_TYPES = ("text", "int", "bool")
LOG_INPUTS = ("hash", "excerpt", "full")
PROMPT_FORMATS = ("qwen3-nothink/1", "plain/1")
RULE_SETS = ("router_hints", "hydra_static", "none")

# The fixed gate-metric vocabulary (plan §5.3: "no eval()").
GATE_METRICS = (
    "act_errors", "act_coverage", "act_precision", "accuracy", "macro_f1",
    "nll", "brier", "ece", "aurc", "abstain_rate", "latency_p95_ms",
    "mcnemar_p_vs", "truncated_rate",
)
PER_LABEL_METRICS = ("act_errors", "act_coverage", "act_precision")
OPS = ("==", "<=", ">=", "<", ">")
SPLITS = ("train", "calib", "test", "ood", "adversarial", "load")

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SLOT_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_TRUNC_RE = re.compile(r"^head_tail:(\d+):(\d+)$")
# metric[label]@split+split op value  — e.g. act_errors[inline]@test+ood == 0
_CONSTRAINT_RE = re.compile(
    r"^\s*([a-z_0-9]+)(?:\[([a-z][a-z0-9_]*)\])?@([a-z+]+)\s*(==|<=|>=|<|>)\s*"
    r"([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*$")


class SpecError(ValueError):
    """A decision file that must not be served."""


@dataclass(frozen=True)
class Label:
    name: str
    text: str
    desc: str = ""


@dataclass(frozen=True)
class InputSpec:
    name: str
    type: str
    max_tokens: int = 0
    head: int = 0
    tail: int = 0
    truncate: str = ""


@dataclass(frozen=True)
class Constraint:
    metric: str
    label: str | None
    splits: tuple[str, ...]
    op: str
    value: float
    source: str = ""


@dataclass(frozen=True)
class GateCriterion:
    metric: str
    split: str
    op: str
    value: float
    label: str | None = None


@dataclass
class Policy:
    mode: str
    act: dict[str, float]
    min_label_mass: float
    deadline_ms: int
    canary_pct: int = 0
    review: dict[str, float] = field(default_factory=dict)
    min_margin: float = 0.0
    cost: dict[str, float] = field(default_factory=dict)
    hard_constraints: list[Constraint] = field(default_factory=list)
    async_only: bool = False


@dataclass
class Gate:
    criteria: list[GateCriterion]
    min_n: dict[str, int]
    shadow_min_decisions: int
    shadow_min_days: int


@dataclass
class DecisionSpec:
    id: str
    version: int
    owner: str
    description: str
    type: str
    form: str
    labels: list[Label]
    prompt_format: str
    prompt_system: str
    prompt_template: str
    inputs: dict[str, InputSpec]
    chain: list[str]
    policy: Policy
    gate: Gate
    forbidden_contexts: list[str] = field(default_factory=lambda: ["eval"])
    log_inputs: str | None = None
    rank_max_items: int = 16
    rank_positive: str | None = None
    mechanical: list[str] = field(default_factory=list)
    rule_set: str = "none"
    rule_params: dict[str, Any] = field(default_factory=dict)
    probe_known: list[dict[str, Any]] = field(default_factory=list)
    path: str = ""

    @property
    def label_names(self) -> list[str]:
        return [lb.name for lb in self.labels]

    def label(self, name: str) -> Label:
        for lb in self.labels:
            if lb.name == name:
                return lb
        raise KeyError(name)

    @property
    def positive(self) -> str:
        """For rank decisions: the label whose probability is the item's p."""
        return self.rank_positive or self.labels[0].name


def _check_keys(where: str, table: dict, allowed: set[str], required: set[str] = frozenset()):
    if not isinstance(table, dict):
        raise SpecError(f"{where}: expected a table")
    unknown = set(table) - allowed
    if unknown:
        raise SpecError(f"{where}: unknown key(s) {sorted(unknown)}")
    missing = set(required) - set(table)
    if missing:
        raise SpecError(f"{where}: missing required key(s) {sorted(missing)}")


def _num(where: str, v, lo=None, hi=None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SpecError(f"{where}: expected a number, got {v!r}")
    v = float(v)
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise SpecError(f"{where}: {v} outside [{lo}, {hi}]")
    return v


def _int(where: str, v, lo=None, hi=None) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise SpecError(f"{where}: expected an integer, got {v!r}")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise SpecError(f"{where}: {v} outside [{lo}, {hi}]")
    return v


def _str(where: str, v, allow_empty=False) -> str:
    if not isinstance(v, str) or (not allow_empty and not v.strip()):
        raise SpecError(f"{where}: expected a non-empty string")
    return v


def parse_constraint(text: str, label_names: list[str]) -> Constraint:
    m = _CONSTRAINT_RE.match(text or "")
    if not m:
        raise SpecError(f"hard_constraints: cannot parse {text!r} "
                        "(want metric[label]@split+split op number)")
    metric, label, splits, op, value = m.groups()
    if metric not in GATE_METRICS:
        raise SpecError(f"hard_constraints: unknown metric {metric!r}")
    if metric in PER_LABEL_METRICS and not label:
        raise SpecError(f"hard_constraints: {metric} needs a [label]")
    if label and label not in label_names:
        raise SpecError(f"hard_constraints: unknown label {label!r}")
    split_t = tuple(splits.split("+"))
    for s in split_t:
        if s not in SPLITS:
            raise SpecError(f"hard_constraints: unknown split {s!r}")
    return Constraint(metric, label, split_t, op, float(value), text)


_TOP_KEYS = {"id", "version", "owner", "description", "type", "form", "labels",
             "prompt", "inputs", "rank", "engines", "policy", "forbidden_contexts",
             "privacy", "gate", "mechanical", "rules", "probe"}
_TOP_REQUIRED = {"id", "version", "owner", "description", "type", "form", "labels",
                 "prompt", "inputs", "engines", "policy", "gate"}
_POLICY_KEYS = {"mode", "canary_pct", "act", "review", "min_margin", "min_label_mass",
                "cost", "hard_constraints", "deadline_ms", "async_only"}
_POLICY_REQUIRED = {"mode", "act", "min_label_mass", "deadline_ms"}


def parse_spec(data: dict, *, path: str = "", expect_id: str | None = None) -> DecisionSpec:
    """Validate one parsed TOML document. Raises SpecError on ANY problem."""
    _check_keys("decision", data, _TOP_KEYS, _TOP_REQUIRED)
    sid = _str("id", data["id"])
    if not _ID_RE.match(sid):
        raise SpecError(f"id {sid!r} must be <owner>.<name> in lower snake case")
    if expect_id is not None and sid != expect_id:
        raise SpecError(f"id {sid!r} does not match its filename {expect_id!r}")
    version = _int("version", data["version"], lo=1)
    dtype = data["type"]
    if dtype == "classify":
        raise SpecError("type 'classify' is reserved (FUTURE)")
    if dtype not in TYPES:
        raise SpecError(f"type must be one of {TYPES}")
    form = data["form"]
    if form not in FORMS:
        raise SpecError(f"form must be one of {FORMS}")

    raw_labels = data["labels"]
    if not isinstance(raw_labels, list) or not 2 <= len(raw_labels) <= 26:
        raise SpecError("labels: need 2-26 labels")
    labels: list[Label] = []
    for i, lb in enumerate(raw_labels):
        _check_keys(f"labels[{i}]", lb, {"name", "text", "desc"}, {"name", "text"})
        name = _str(f"labels[{i}].name", lb["name"])
        if not _NAME_RE.match(name):
            raise SpecError(f"labels[{i}].name {name!r} must be lower snake case")
        text = _str(f"labels[{i}].text", lb["text"])
        if text != text.strip():
            raise SpecError(f"labels[{i}].text must not carry whitespace; the "
                            "prompt format decides the leading space")
        labels.append(Label(name, text, str(lb.get("desc", ""))))
    names = [lb.name for lb in labels]
    if len(set(names)) != len(names):
        raise SpecError("labels: duplicate name")
    if len({lb.text for lb in labels}) != len(labels):
        raise SpecError("labels: duplicate text (labels must be pairwise distinct)")
    if dtype in ("yes_no", "rank") and len(labels) != 2:
        raise SpecError(f"type {dtype} needs exactly two labels")

    prompt = data["prompt"]
    _check_keys("prompt", prompt, {"format", "system", "template"},
                {"format", "system", "template"})
    fmt = prompt["format"]
    if fmt not in PROMPT_FORMATS:
        raise SpecError(f"prompt.format must be one of {PROMPT_FORMATS}")
    system = _str("prompt.system", prompt["system"])
    template = _str("prompt.template", prompt["template"])

    raw_inputs = data["inputs"]
    if not isinstance(raw_inputs, dict) or not raw_inputs:
        raise SpecError("inputs: need at least one input")
    inputs: dict[str, InputSpec] = {}
    for iname, ispec in raw_inputs.items():
        if not _NAME_RE.match(iname) or iname == "item":
            raise SpecError(f"inputs.{iname}: bad input name")
        _check_keys(f"inputs.{iname}", ispec, {"type", "max_tokens", "truncate"}, {"type"})
        itype = ispec["type"]
        if itype not in INPUT_TYPES:
            raise SpecError(f"inputs.{iname}.type must be one of {INPUT_TYPES}")
        max_tokens = head = tail = 0
        trunc = ""
        if itype == "text":
            if "max_tokens" not in ispec or "truncate" not in ispec:
                raise SpecError(f"inputs.{iname}: text inputs need max_tokens and truncate")
            max_tokens = _int(f"inputs.{iname}.max_tokens", ispec["max_tokens"], lo=1)
            trunc = _str(f"inputs.{iname}.truncate", ispec["truncate"])
            m = _TRUNC_RE.match(trunc)
            if not m:
                raise SpecError(f"inputs.{iname}.truncate must be head_tail:H:T")
            head, tail = int(m.group(1)), int(m.group(2))
            if head + tail > max_tokens or head + tail == 0:
                raise SpecError(f"inputs.{iname}: head+tail must be 1..max_tokens")
        elif "truncate" in ispec or "max_tokens" in ispec:
            raise SpecError(f"inputs.{iname}: only text inputs take max_tokens/truncate")
        inputs[iname] = InputSpec(iname, itype, max_tokens, head, tail, trunc)

    slots = _SLOT_RE.findall(template)
    item_slots = [s for s in slots if s == "item"]
    field_slots = [s for s in slots if s != "item"]
    if dtype == "rank":
        if len(item_slots) != 1:
            raise SpecError("rank templates need exactly one {item} slot")
    elif item_slots:
        raise SpecError("{item} is only valid in rank templates")
    if len(field_slots) != len(set(field_slots)):
        raise SpecError("prompt.template: each {field} slot may appear once")
    if set(field_slots) != set(inputs):
        raise SpecError(f"prompt.template slots {sorted(set(field_slots))} must "
                        f"equal inputs {sorted(inputs)}")

    rank_max_items, rank_positive = 16, None
    if "rank" in data:
        if dtype != "rank":
            raise SpecError("[rank] is only valid for type=rank")
        _check_keys("rank", data["rank"], {"max_items", "positive"})
        rank_max_items = _int("rank.max_items", data["rank"].get("max_items", 16), lo=1, hi=256)
        rank_positive = data["rank"].get("positive")
        if rank_positive is not None and rank_positive not in names:
            raise SpecError("rank.positive must be a label name")

    engines = data["engines"]
    _check_keys("engines", engines, {"chain"}, {"chain"})
    chain = engines["chain"]
    if (not isinstance(chain, list) or not chain
            or not all(isinstance(c, str) and c for c in chain)):
        raise SpecError("engines.chain: need a non-empty list of binding names")
    if len(set(chain)) != len(chain):
        raise SpecError("engines.chain: duplicate engine")

    pol = data["policy"]
    _check_keys("policy", pol, _POLICY_KEYS, _POLICY_REQUIRED)
    mode = pol["mode"]
    if mode not in MODES:
        raise SpecError(f"policy.mode must be one of {MODES}")
    act = pol["act"]
    if not isinstance(act, dict):
        raise SpecError("policy.act must be a table label -> threshold")
    act_f = {}
    for k, v in act.items():
        if k not in names:
            raise SpecError(f"policy.act: {k!r} is not a label")
        act_f[k] = _num(f"policy.act.{k}", v, lo=0.0, hi=1.0)
    review = pol.get("review", {})
    if not isinstance(review, dict):
        raise SpecError("policy.review must be a table")
    review_f = {}
    for k, v in review.items():
        if k not in names:
            raise SpecError(f"policy.review: {k!r} is not a label")
        review_f[k] = _num(f"policy.review.{k}", v, lo=0.0, hi=1.0)
    cost = pol.get("cost", {})
    if not isinstance(cost, dict):
        raise SpecError("policy.cost must be a table")
    cost_f = {}
    for k, v in cost.items():
        parts = k.split(">")
        if (len(parts) != 2 or parts[0] not in names
                or (parts[1] not in names and parts[1] != "abstain") or parts[0] == parts[1]):
            raise SpecError(f"policy.cost: key {k!r} must be 'true>pred' over labels")
        cost_f[k] = _num(f"policy.cost.{k}", v, lo=0.0)
    hcs = pol.get("hard_constraints", [])
    if not isinstance(hcs, list):
        raise SpecError("policy.hard_constraints must be a list of strings")
    constraints = [parse_constraint(c, names) for c in hcs]
    async_only = pol.get("async_only", False)
    if not isinstance(async_only, bool):
        raise SpecError("policy.async_only must be a bool")
    policy = Policy(
        mode=mode, act=act_f,
        min_label_mass=_num("policy.min_label_mass", pol["min_label_mass"], 0.0, 1.0),
        deadline_ms=_int("policy.deadline_ms", pol["deadline_ms"], lo=1, hi=60000),
        canary_pct=_int("policy.canary_pct", pol.get("canary_pct", 0), lo=0, hi=100),
        review=review_f,
        min_margin=_num("policy.min_margin", pol.get("min_margin", 0.0), 0.0, 1.0),
        cost=cost_f, hard_constraints=constraints, async_only=async_only)

    forbidden = data.get("forbidden_contexts", ["eval"])
    if not isinstance(forbidden, list) or not all(isinstance(x, str) for x in forbidden):
        raise SpecError("forbidden_contexts must be a list of strings")

    log_inputs = None
    if "privacy" in data:
        _check_keys("privacy", data["privacy"], {"log_inputs"})
        log_inputs = data["privacy"].get("log_inputs")
        if log_inputs is not None and log_inputs not in LOG_INPUTS:
            raise SpecError(f"privacy.log_inputs must be one of {LOG_INPUTS}")

    g = data["gate"]
    _check_keys("gate", g, {"criteria", "min_n", "shadow"}, {"criteria", "min_n", "shadow"})
    if not isinstance(g["criteria"], list) or not g["criteria"]:
        raise SpecError("gate.criteria: need at least one criterion")
    criteria = []
    for i, c in enumerate(g["criteria"]):
        _check_keys(f"gate.criteria[{i}]", c, {"metric", "split", "op", "value", "label"},
                    {"metric", "split", "op", "value"})
        metric = c["metric"]
        if metric not in GATE_METRICS:
            raise SpecError(f"gate.criteria[{i}]: unknown metric {metric!r}")
        split = _str(f"gate.criteria[{i}].split", c["split"])
        for s in split.split("+"):
            if s not in SPLITS:
                raise SpecError(f"gate.criteria[{i}]: unknown split {s!r}")
        if c["op"] not in OPS:
            raise SpecError(f"gate.criteria[{i}]: op must be one of {OPS}")
        label = c.get("label")
        if metric in PER_LABEL_METRICS:
            if label not in names:
                raise SpecError(f"gate.criteria[{i}]: {metric} needs a label from labels")
        elif metric == "mcnemar_p_vs":
            if not isinstance(label, str) or not label:
                raise SpecError(f"gate.criteria[{i}]: mcnemar_p_vs needs label = <engine>")
        elif label is not None:
            raise SpecError(f"gate.criteria[{i}]: {metric} takes no label")
        criteria.append(GateCriterion(metric, split, c["op"],
                                      _num(f"gate.criteria[{i}].value", c["value"]), label))
    min_n = g["min_n"]
    if not isinstance(min_n, dict):
        raise SpecError("gate.min_n must be a table split -> int")
    min_n_i = {}
    for k, v in min_n.items():
        if k not in SPLITS:
            raise SpecError(f"gate.min_n: unknown split {k!r}")
        min_n_i[k] = _int(f"gate.min_n.{k}", v, lo=0)
    _check_keys("gate.shadow", g["shadow"], {"min_decisions", "min_days"},
                {"min_decisions", "min_days"})
    gate = Gate(criteria, min_n_i,
                _int("gate.shadow.min_decisions", g["shadow"]["min_decisions"], lo=0),
                _int("gate.shadow.min_days", g["shadow"]["min_days"], lo=0))

    mechanical = data.get("mechanical", [])
    if not isinstance(mechanical, list) or any(m not in names for m in mechanical):
        raise SpecError("mechanical must list label names")

    rule_set, rule_params = "none", {}
    if "rules" in data:
        rules = data["rules"]
        _check_keys("rules", rules, {"set", "long_context_tokens"}, {"set"})
        rule_set = rules["set"]
        if rule_set not in RULE_SETS:
            raise SpecError(f"rules.set must be one of {RULE_SETS}")
        if "long_context_tokens" in rules:
            rule_params["long_context_tokens"] = _int(
                "rules.long_context_tokens", rules["long_context_tokens"], lo=1)
    if mechanical and rule_set == "none":
        raise SpecError("mechanical labels need a [rules] set that computes them")

    probe_known = []
    if "probe" in data:
        _check_keys("probe", data["probe"], {"known"})
        known = data["probe"].get("known", [])
        if not isinstance(known, list):
            raise SpecError("probe.known must be a list")
        for i, k in enumerate(known):
            _check_keys(f"probe.known[{i}]", k, {"inputs", "label"}, {"inputs", "label"})
            if k["label"] not in names or not isinstance(k["inputs"], dict):
                raise SpecError(f"probe.known[{i}]: bad label or inputs")
            probe_known.append({"inputs": dict(k["inputs"]), "label": k["label"]})

    return DecisionSpec(
        id=sid, version=version, owner=_str("owner", data["owner"]),
        description=_str("description", data["description"]), type=dtype, form=form,
        labels=labels, prompt_format=fmt, prompt_system=system, prompt_template=template,
        inputs=inputs, chain=list(chain), policy=policy, gate=gate,
        forbidden_contexts=list(forbidden), log_inputs=log_inputs,
        rank_max_items=rank_max_items, rank_positive=rank_positive,
        mechanical=list(mechanical), rule_set=rule_set, rule_params=rule_params,
        probe_known=probe_known, path=path)


def load_spec(path: str | Path) -> DecisionSpec:
    p = Path(path)
    try:
        with open(p, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise SpecError(f"TOML parse error: {exc}") from None
    return parse_spec(data, path=str(p), expect_id=p.name.removesuffix(".toml"))


def load_registry(directory: str | Path) -> tuple[dict[str, DecisionSpec], dict[str, str]]:
    """Every *.toml in `directory`. Returns (specs, errors) — an invalid file
    lands in `errors` keyed by its filename stem and never in `specs`."""
    specs: dict[str, DecisionSpec] = {}
    errors: dict[str, str] = {}
    d = Path(directory)
    if not d.is_dir():
        return specs, {"<registry>": f"decisions_dir {d} does not exist"}
    for p in sorted(d.glob("*.toml")):
        stem = p.name.removesuffix(".toml")
        try:
            specs[stem] = load_spec(p)
        except (SpecError, OSError) as exc:
            errors[stem] = str(exc)
    return specs, errors


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def decision_hash(spec: DecisionSpec, engine: dict[str, Any],
                  label_token_ids: dict[str, int] | None) -> str:
    """sha256 over the canonical JSON of everything that fixes a probability's
    meaning (plan §5.3). `engine` carries adapter, model identity and exec.
    Thresholds are NOT hashed."""
    payload = {
        "id": spec.id, "version": spec.version, "type": spec.type, "form": spec.form,
        "labels": [[lb.name, lb.text] for lb in spec.labels],
        "prompt.format": spec.prompt_format, "prompt.system": spec.prompt_system,
        "prompt.template": spec.prompt_template,
        "inputs": {k: [v.type, v.max_tokens, v.truncate] for k, v in sorted(spec.inputs.items())},
        "mechanical": sorted(spec.mechanical),
        "engine.adapter": engine.get("adapter"),
        "engine.model": engine.get("model_sha256") or engine.get("model_revision"),
        "engine.exec": engine.get("exec"),
        "label_token_ids": dict(sorted(label_token_ids.items())) if label_token_ids else None,
    }
    return hashlib.sha256(_canon(payload).encode()).hexdigest()
