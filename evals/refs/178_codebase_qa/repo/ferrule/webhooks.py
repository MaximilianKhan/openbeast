"""Webhook subscriptions per (tenant, queue).

Delivery itself is the notify plugin's job; this module only keeps the
subscription list and validates URLs.
"""

from __future__ import annotations

import ipaddress
import threading
from dataclasses import dataclass
from typing import Dict, List, Tuple
from urllib.parse import urlsplit

from ferrule.errors import ValidationFailed

_ALLOWED_SCHEMES = ("https",)


@dataclass(frozen=True)
class Subscription:
    tenant: str
    queue: str
    url: str


def validate_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise ValidationFailed("webhook URLs must be https")
    host = parts.hostname or ""
    if not host:
        raise ValidationFailed("webhook URL has no host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if host == "localhost" or host.endswith(".internal"):
            raise ValidationFailed("webhook host is internal") from None
        return url
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ValidationFailed("webhook host is internal")
    return url


class WebhookRegistry:
    def __init__(self) -> None:
        self._subs: Dict[Tuple[str, str], List[Subscription]] = {}
        self._lock = threading.Lock()

    def add(self, tenant: str, queue: str, url: str) -> Subscription:
        sub = Subscription(tenant, queue, validate_url(url))
        with self._lock:
            subs = self._subs.setdefault((tenant, queue), [])
            if sub not in subs:
                subs.append(sub)
        return sub

    def remove(self, tenant: str, queue: str, url: str) -> bool:
        with self._lock:
            subs = self._subs.get((tenant, queue), [])
            before = len(subs)
            self._subs[(tenant, queue)] = [s for s in subs if s.url != url]
            return len(self._subs[(tenant, queue)]) != before

    def for_queue(self, tenant: str, queue: str) -> List[Subscription]:
        with self._lock:
            return list(self._subs.get((tenant, queue), []))
