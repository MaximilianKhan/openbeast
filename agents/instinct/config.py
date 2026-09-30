"""Service + engine-binding configuration (agents/instinct/instinct.toml).

Loader rules (plan §5.2):
  * an unknown key in a binding invalidates THAT binding only;
  * a binding whose url is the primary INFERENCE_URL is refused unless it sets
    allow_primary = true (and then only async_only decisions may use it — the
    service enforces that half, because it needs the decision registry);
  * a binding that points at hydra is refused (I7: instinct's engine traffic
    never routes through hydra or beast-gate).
Env overrides: INSTINCT_CONFIG (path), INSTINCT_PORT, INSTINCT_ENGINE_OVERRIDE
(replace every LLM engine in every chain with one binding — the stub smoke).
"""
from __future__ import annotations

import ipaddress
import os
import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "agents" / "instinct" / "instinct.toml"
ADAPTERS = ("rules", "linear", "llamacpp_logprobs", "sglang_score")
LLM_ADAPTERS = ("llamacpp_logprobs", "sglang_score")

# Ports that belong to the edge / router / hydra — an engine may never be one,
# on ANY host (the rig's gate on its tailnet address is still beast-gate).
# :8443 beast-gate (edge), :8088 agent router, :8095 hydra (reconciliation §4).
FORBIDDEN_ENGINE_PORTS = {8443: "beast-gate", 8088: "agent router", 8095: "hydra"}
# The primary when nothing says otherwise — the same default conf.sh and
# serve-instinct-scorer.sh use. instinct.sh never sources conf.sh, so without
# this the primary-URL lint would be inactive exactly where it matters.
DEFAULT_INFERENCE_URL = "http://127.0.0.1:8080"

_SERVICE_KEYS = {"host", "port", "key_file", "ledger_dir", "log_inputs", "retention_days",
                 "max_concurrency", "shadow_queue", "decisions_dir", "records_dir",
                 "probe_interval_s", "allow_remote", "require_committed_gate",
                 "state_dir"}
_BINDING_KEYS = {"adapter", "url", "key_file", "model", "model_sha256", "model_revision",
                 "image_digest", "sglang_commit", "exec", "n_probs", "timeout_ms",
                 "allow_primary", "tokenize_path", "mis_delimiter", "sis_url", "role",
                 "score_query"}


class ConfigError(ValueError):
    pass


@dataclass
class EngineBinding:
    name: str
    adapter: str
    url: str = ""
    key_file: str = ""
    model: str = ""
    model_sha256: str = ""
    model_revision: str = ""
    image_digest: str = ""
    sglang_commit: str = ""
    exec: str = "sis"
    n_probs: int = 100
    timeout_ms: int = 1000
    allow_primary: bool = False
    tokenize_path: str = ""
    mis_delimiter: str = ""
    sis_url: str = ""
    role: str = "instinct-engine"
    # sglang_score, non-rank decisions: "empty" sends query="" + the prompt as
    # the one item (the documented "complete prompt" convention); "prompt" is
    # the [HW] fallback — the prompt as query + one empty item.
    score_query: str = "empty"

    def hash_identity(self) -> dict:
        """The engine half of decision_hash."""
        if self.adapter == "rules":
            return {"adapter": "rules", "model_sha256": "rules/1", "exec": None}
        if self.adapter == "linear":
            return {"adapter": "linear", "model_sha256": "linear/1", "exec": None}
        ident = {"adapter": self.adapter, "model_sha256": self.model_sha256,
                 "model_revision": self.model_revision, "exec": self.exec}
        if self.score_query != "empty":
            ident["score_query"] = self.score_query   # a different request is a new hash
        return ident


@dataclass
class ServiceConfig:
    host: str = "127.0.0.1"
    port: int = 8094
    key_file: Path = REPO_ROOT / ".run" / "instinct.key"
    ledger_dir: Path = REPO_ROOT / ".run" / "instinct"
    state_dir: Path = REPO_ROOT / ".run" / "instinct"
    log_inputs: str = "hash"
    retention_days: int = 30
    max_concurrency: int = 8
    shadow_queue: int = 32
    decisions_dir: Path = REPO_ROOT / "agents" / "instinct" / "decisions"
    records_dir: Path = REPO_ROOT / "evals" / "decisions"
    probe_interval_s: int = 300
    allow_remote: bool = False
    require_committed_gate: bool = True
    engines: dict[str, EngineBinding] = field(default_factory=dict)
    engine_errors: dict[str, str] = field(default_factory=dict)
    engine_override: str = ""
    path: str = ""


def is_local_host(host: str) -> bool:
    """True for every spelling of "this machine": localhost (and
    localhost.), any 127/8 address in any inet_aton form (127.1, 0x7f.1,
    2130706433), ::1, IPv4-mapped loopback (::ffff:127.0.0.1) and the
    unspecified addresses (a 0.0.0.0 bind answers on loopback)."""
    h = (host or "").strip().lower().rstrip(".")
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(h))
        except (OSError, ValueError):
            return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified


def _norm_url(url: str) -> tuple[str, str, int]:
    u = urlsplit(url.strip())
    host = (u.hostname or "").lower().rstrip(".")
    if is_local_host(host):
        host = "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    return u.scheme, host, port


def same_endpoint(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return _norm_url(a) == _norm_url(b)
    except ValueError:
        return False


def _lint_url(field_name: str, url: str, b: EngineBinding, inference_url: str,
              hydra_url: str) -> str | None:
    try:
        scheme, host, port = _norm_url(url)
    except ValueError:
        return f"unparseable {field_name} {url!r}"
    if scheme not in ("http", "https"):
        return f"{field_name} must be http(s)"
    if not host:
        return f"{field_name} needs a host"
    if hydra_url and same_endpoint(url, hydra_url):
        return (f"{field_name} is the hydra endpoint (I7: engine traffic never routes "
                "through hydra)")
    if port in FORBIDDEN_ENGINE_PORTS:
        return (f"{field_name} is the {FORBIDDEN_ENGINE_PORTS[port]} port :{port} "
                "(I7: engine traffic never routes through hydra or beast-gate)")
    if inference_url and same_endpoint(url, inference_url) and not b.allow_primary:
        return (f"{field_name} is the primary INFERENCE_URL; set allow_primary = true and "
                "mark every decision using it async_only")
    return None


def lint_binding(b: EngineBinding, inference_url: str = "", hydra_url: str = "") -> str | None:
    """Return a refusal reason, or None if the binding may be used. Every URL
    the engine will call (url AND sis_url) is linted the same way."""
    if b.adapter not in ADAPTERS:
        return f"unknown adapter {b.adapter!r}"
    if b.adapter in LLM_ADAPTERS:
        if not b.url:
            return "LLM adapters need url"
        if b.role != "instinct-engine":
            return "role must be 'instinct-engine'"
        why = _lint_url("url", b.url, b, inference_url, hydra_url)
        if why:
            return why
        if b.sis_url:
            if b.adapter != "sglang_score":
                return "sis_url is only meaningful on sglang_score"
            why = _lint_url("sis_url", b.sis_url, b, inference_url, hydra_url)
            if why:
                return why
            # The engine's bearer key goes to sis_url too: it may only name the
            # same host (another port for the SIS reference server), never a
            # third party.
            if _norm_url(b.sis_url)[:2] != _norm_url(b.url)[:2]:
                return "sis_url must use the same scheme and host as url (it receives the key)"
        if b.score_query not in ("empty", "prompt"):
            return "score_query must be empty|prompt"
        if b.score_query != "empty" and b.adapter != "sglang_score":
            return "score_query is only meaningful on sglang_score"
        if b.exec not in ("sis", "mis"):
            return "exec must be sis|mis"
        if b.exec == "mis" and b.adapter != "sglang_score":
            return "exec=mis is only available on sglang_score"
        if not (b.model_sha256 or b.model_revision):
            return "LLM bindings need model_sha256 or model_revision (it is in decision_hash)"
        for pin in ("model_sha256", "model_revision", "image_digest", "sglang_commit"):
            if "<" in getattr(b, pin) or ">" in getattr(b, pin):
                return f"{pin} is a placeholder — unpinned bindings are refused"
    return None


def _resolve(root: Path, p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else (root / q)


def load_config(path: str | Path | None = None, *, env: dict | None = None,
                root: Path | None = None) -> ServiceConfig:
    env = os.environ if env is None else env
    root = root or REPO_ROOT
    path = Path(path or env.get("INSTINCT_CONFIG") or DEFAULT_CONFIG)
    if not path.is_absolute():
        path = root / path
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from None
    unknown_top = set(data) - {"service", "engines"}
    if unknown_top:
        raise ConfigError(f"unknown top-level table(s) {sorted(unknown_top)}")
    svc = data.get("service", {})
    unknown = set(svc) - _SERVICE_KEYS
    if unknown:
        raise ConfigError(f"[service]: unknown key(s) {sorted(unknown)}")
    cfg = ServiceConfig(path=str(path))
    for k in ("host", "log_inputs"):
        if k in svc:
            setattr(cfg, k, str(svc[k]))
    for k in ("port", "retention_days", "max_concurrency", "shadow_queue", "probe_interval_s"):
        if k in svc:
            v = svc[k]
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise ConfigError(f"[service].{k} must be a non-negative integer")
            setattr(cfg, k, v)
    for k in ("allow_remote", "require_committed_gate"):
        if k in svc:
            if not isinstance(svc[k], bool):
                raise ConfigError(f"[service].{k} must be a bool")
            setattr(cfg, k, svc[k])
    for k in ("key_file", "ledger_dir", "decisions_dir", "records_dir", "state_dir"):
        if k in svc:
            setattr(cfg, k, _resolve(root, str(svc[k])))
    if "state_dir" not in svc:
        cfg.state_dir = cfg.ledger_dir
    if cfg.log_inputs not in ("hash", "excerpt", "full"):
        raise ConfigError("[service].log_inputs must be hash|excerpt|full")
    if env.get("INSTINCT_PORT"):
        try:
            cfg.port = int(env["INSTINCT_PORT"])
        except ValueError:
            raise ConfigError("INSTINCT_PORT must be an integer") from None
    if cfg.host not in ("127.0.0.1", "localhost", "::1") and not cfg.allow_remote:
        raise ConfigError(f"[service].host {cfg.host!r} is not loopback; set "
                          "allow_remote = true to bind it (a key is still required)")

    inference_url = (env.get("INFERENCE_URL") or env.get("OPENBEAST_INFERENCE_URL")
                     or DEFAULT_INFERENCE_URL)
    hydra_url = env.get("HYDRA_URL") or ""
    for name, raw in (data.get("engines") or {}).items():
        if not isinstance(raw, dict):
            cfg.engine_errors[name] = "binding must be a table"
            continue
        unknown = set(raw) - _BINDING_KEYS
        if unknown:
            cfg.engine_errors[name] = f"unknown key(s) {sorted(unknown)}"
            continue
        if "allow_primary" in raw and not isinstance(raw["allow_primary"], bool):
            # bool("no") is True — a string here must never open the primary.
            cfg.engine_errors[name] = "allow_primary must be a TOML bool (true/false)"
            continue
        try:
            b = EngineBinding(
                name=name, adapter=str(raw.get("adapter", "")),
                url=str(raw.get("url", "")),
                key_file=str(_resolve(root, raw["key_file"])) if raw.get("key_file") else "",
                model=str(raw.get("model", "")), model_sha256=str(raw.get("model_sha256", "")),
                model_revision=str(raw.get("model_revision", "")),
                image_digest=str(raw.get("image_digest", "")),
                sglang_commit=str(raw.get("sglang_commit", "")),
                exec=str(raw.get("exec", "sis")), n_probs=int(raw.get("n_probs", 100)),
                timeout_ms=int(raw.get("timeout_ms", 1000)),
                allow_primary=bool(raw.get("allow_primary", False)),
                tokenize_path=str(raw.get("tokenize_path", "")),
                mis_delimiter=str(raw.get("mis_delimiter", "")),
                sis_url=str(raw.get("sis_url", "")),
                role=str(raw.get("role", "instinct-engine")),
                score_query=str(raw.get("score_query", "empty")))
        except (TypeError, ValueError) as exc:
            cfg.engine_errors[name] = f"bad value: {exc}"
            continue
        why = lint_binding(b, inference_url, hydra_url)
        if why:
            cfg.engine_errors[name] = why
            continue
        cfg.engines[name] = b
    # rules + linear are always available, even if the file omits them.
    cfg.engines.setdefault("rules", EngineBinding(name="rules", adapter="rules"))
    cfg.engines.setdefault("linear", EngineBinding(name="linear", adapter="linear"))
    override = env.get("INSTINCT_ENGINE_OVERRIDE", "").strip()
    if override:
        if override not in cfg.engines or cfg.engines[override].adapter not in LLM_ADAPTERS:
            raise ConfigError(f"INSTINCT_ENGINE_OVERRIDE={override!r} is not an LLM binding")
        cfg.engine_override = override
    return cfg


def effective_chain(chain: list[str], cfg: ServiceConfig) -> list[str]:
    """Apply INSTINCT_ENGINE_OVERRIDE: every LLM entry becomes the override."""
    if not cfg.engine_override:
        return list(chain)
    out: list[str] = []
    for name in chain:
        b = cfg.engines.get(name)
        is_llm = (b is None and name not in ("rules", "linear")) or (
            b is not None and b.adapter in LLM_ADAPTERS)
        new = cfg.engine_override if is_llm else name
        if new not in out:
            out.append(new)
    return out
