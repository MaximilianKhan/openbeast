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

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "agents" / "instinct" / "instinct.toml"
ADAPTERS = ("rules", "linear", "llamacpp_logprobs", "sglang_score")
LLM_ADAPTERS = ("llamacpp_logprobs", "sglang_score")

# Ports that belong to the edge / router / hydra — an engine may never be one.
# :8443 beast-gate (edge), :8088 agent router, :8095 hydra (reconciliation §4).
FORBIDDEN_ENGINE_PORTS = {8443: "beast-gate", 8088: "agent router", 8095: "hydra"}

_SERVICE_KEYS = {"host", "port", "key_file", "ledger_dir", "log_inputs", "retention_days",
                 "max_concurrency", "shadow_queue", "decisions_dir", "records_dir",
                 "probe_interval_s", "allow_remote", "require_committed_gate",
                 "state_dir"}
_BINDING_KEYS = {"adapter", "url", "key_file", "model", "model_sha256", "model_revision",
                 "image_digest", "sglang_commit", "exec", "n_probs", "timeout_ms",
                 "allow_primary", "tokenize_path", "mis_delimiter", "sis_url", "role"}


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

    def hash_identity(self) -> dict:
        """The engine half of decision_hash."""
        if self.adapter == "rules":
            return {"adapter": "rules", "model_sha256": "rules/1", "exec": None}
        if self.adapter == "linear":
            return {"adapter": "linear", "model_sha256": "linear/1", "exec": None}
        return {"adapter": self.adapter, "model_sha256": self.model_sha256,
                "model_revision": self.model_revision, "exec": self.exec}


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


def _norm_url(url: str) -> tuple[str, str, int]:
    u = urlsplit(url.strip())
    host = (u.hostname or "").lower()
    if host in ("localhost", "::1", "0.0.0.0"):
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


def lint_binding(b: EngineBinding, inference_url: str = "", hydra_url: str = "") -> str | None:
    """Return a refusal reason, or None if the binding may be used."""
    if b.adapter not in ADAPTERS:
        return f"unknown adapter {b.adapter!r}"
    if b.adapter in LLM_ADAPTERS:
        if not b.url:
            return "LLM adapters need url"
        try:
            scheme, host, port = _norm_url(b.url)
        except ValueError:
            return f"unparseable url {b.url!r}"
        if scheme not in ("http", "https"):
            return "url must be http(s)"
        if not host:
            return "url needs a host"
        if b.role != "instinct-engine":
            return "role must be 'instinct-engine'"
        if hydra_url and same_endpoint(b.url, hydra_url):
            return "url is the hydra endpoint (I7: engine traffic never routes through hydra)"
        if host == "127.0.0.1" and port in FORBIDDEN_ENGINE_PORTS:
            return (f"url is the {FORBIDDEN_ENGINE_PORTS[port]} port :{port} "
                    "(I7: engine traffic never routes through hydra or beast-gate)")
        if inference_url and same_endpoint(b.url, inference_url) and not b.allow_primary:
            return ("url is the primary INFERENCE_URL; set allow_primary = true and "
                    "mark every decision using it async_only")
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

    inference_url = env.get("INFERENCE_URL") or env.get("OPENBEAST_INFERENCE_URL") or ""
    hydra_url = env.get("HYDRA_URL") or ""
    for name, raw in (data.get("engines") or {}).items():
        if not isinstance(raw, dict):
            cfg.engine_errors[name] = "binding must be a table"
            continue
        unknown = set(raw) - _BINDING_KEYS
        if unknown:
            cfg.engine_errors[name] = f"unknown key(s) {sorted(unknown)}"
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
                role=str(raw.get("role", "instinct-engine")))
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
