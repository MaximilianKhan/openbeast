"""API tokens: ``<key id>.<secret>`` pairs, the secret stored hashed."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from ferrule.errors import Unauthorized
from ferrule.util.durations import parse_duration


@dataclass
class Token:
    key_id: str
    digest: str
    role: str
    tenant: str
    tier: str = "standard"
    expires_at: float = 0.0
    labels: Dict[str, str] = field(default_factory=dict)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class TokenStore:
    def __init__(self, settings) -> None:
        self.ttl_s = parse_duration(settings["auth"]["token_ttl"])
        self._tokens: Dict[str, Token] = {}

    def issue(self, role: str, tenant: str, tier: str = "standard") -> str:
        key_id = "k" + secrets.token_hex(6)
        secret = secrets.token_urlsafe(24)
        self._tokens[key_id] = Token(key_id, _digest(secret), role, tenant, tier,
                                     time.time() + self.ttl_s)
        return f"{key_id}.{secret}"

    def verify(self, raw: Optional[str]) -> Token:
        if not raw or "." not in raw:
            raise Unauthorized("missing or malformed token")
        key_id, secret = raw.split(".", 1)
        tok = self._tokens.get(key_id)
        if tok is None or not hmac.compare_digest(tok.digest, _digest(secret)):
            raise Unauthorized("unknown token")
        if tok.expires_at and time.time() > tok.expires_at:
            raise Unauthorized("token expired")
        return tok

    def revoke(self, key_id: str) -> bool:
        return self._tokens.pop(key_id, None) is not None
