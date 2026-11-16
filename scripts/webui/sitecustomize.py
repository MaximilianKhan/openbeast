"""OpenBeast's Host-header allowlist for the Open WebUI container.

Mounted read-only into the open-webui container (docker-compose.yml) and put
on PYTHONPATH there, so the image's own `python3 -m uvicorn open_webui.main`
imports it at startup through `site`. It adds one check in front of every
Starlette app in that process: an HTTP or WebSocket request whose Host header
names a host outside OB_WEBUI_HOSTS is refused (400 / close 1008).

Why: with WEBUI_AUTH=false (the default, loopback-only install) every
visitor is the WebUI admin, and the admin holds the bash tool connection.
Pinning CORS stops another site's script from READING a cross-origin answer,
but DNS rebinding needs no CORS: a page at evil.example:3000 re-resolves its
own name to 127.0.0.1, and the browser then treats the WebUI as that page's
own origin. Open WebUI has no Host allowlist of its own, so it answered —
and Firefox/Safari do not gate public-to-loopback requests the way Chrome's
Local Network Access does. Every such request still carries the attacker's
name in Host, so refusing unknown Hosts closes it.

OB_WEBUI_HOSTS: extra Host names (comma separated), or `*` for no check.
The allowlist and its matching are agents/hostpolicy.py's — the one Host
policy every OpenBeast HTTP surface shares (loopback, this machine's name,
*.ts.net, plus the extras); compose mounts that file next to this one.
scripts/lib/conf.sh sets the value: loopback and the hosts of WEBUI_ORIGINS
while login is off, `*` while login is on (a rebound page has no session) or
when ALLOW_OPEN_WEBUI=true / WEBUI_ORIGINS='*' says the WebUI is open.

Failure mode: if the hook cannot install (an upstream Starlette change), it
says so on stderr and the WebUI starts unguarded — a bricked chat UI on an
image bump is the worse outcome; tests/test_webui_exposure.py pins the hook.
Because it fails open (and an image that resets PYTHONPATH never loads this
file at all), the RUNNING container is checked from outside: lib/net.sh's
ob_webui_host_guard sends Host: rebind.invalid and expects 400 — doctor.sh
FAILs otherwise, start.sh warns, and update.sh --images rolls the open-webui
pin back.
"""

import os
import sys


def _install(raw):
    from hostpolicy import PinnedHostMiddleware, trusted_hosts
    from starlette.applications import Starlette

    if getattr(Starlette.__call__, "_ob_host_guard", False):
        return
    allowed = trusted_hosts(raw.replace(";", ","))
    orig = Starlette.__call__

    async def guarded(self, scope, receive, send):
        # Mounted sub-apps are Starlette too: check once, at the outermost.
        if scope.get("type") not in ("http", "websocket") or scope.get("ob_host_ok"):
            await orig(self, scope, receive, send)
            return

        async def inner(sc, rc, sd):
            sc["ob_host_ok"] = True
            await orig(self, sc, rc, sd)

        await PinnedHostMiddleware(inner, allowed)(scope, receive, send)

    guarded._ob_host_guard = True
    Starlette.__call__ = guarded


_raw = (os.environ.get("OB_WEBUI_HOSTS") or "").strip()
if _raw and _raw != "*":
    try:
        _install(_raw)
    except Exception as e:  # pragma: no cover - only on an upstream change
        print(f"openbeast: WebUI Host allowlist NOT installed ({e!r})", file=sys.stderr)
