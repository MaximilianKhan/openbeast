"""The Host-header allowlist, shared by every OpenBeast HTTP surface.

This lives in its own module for one reason: it was defined inside
agents/chat_server.py, and the adversarial review of v1.4.0 found that
agents/artifact_server.py — written the same week, published on the tailnet
the same way — had no Host validation at all. The fix is not to copy the list
into the second server; two copies of a security allowlist drift, and the
drift is silent. One definition, imported by both.

Why Host pinning matters here: a DNS-rebinding page loaded from an attacker's
origin on the same port becomes same-origin with a loopback server, and
same-origin means it may set arbitrary request headers — including the
`Tailscale-User-Login` header that IS the read-auth mechanism on both
services. A browser cannot forge `Host`, so pinning it is what closes that
door. (It does nothing against a non-browser attacker who can already set
Host freely; that caller is kept out by binding to loopback and by the
write-side locality token, not by this list.)
"""
import ipaddress
import socket


def trusted_hosts(extra: str = "") -> list[str]:
    """Host values a server answers to (the rebinding allowlist).

    Loopback, whatever this machine calls itself, and the tailnet. `*.ts.net`
    is safe to wildcard: those names exist only inside MagicDNS, an attacker
    cannot mint one, and the published deployment is reached by exactly that
    name. Anything else — including a hostile DNS name pointed at 127.0.0.1 —
    is refused before a route ever runs.
    """
    hosts = {"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"}
    try:
        # gethostname() only. NOT getfqdn(), which does a reverse DNS lookup
        # and blocks for seconds whenever the resolver is slow or captured —
        # at startup, on a server whose whole job is to be reachable.
        name = (socket.gethostname() or "").strip().lower()
    except OSError:
        name = ""
    if name:
        hosts.add(name)
        hosts.add(name.split(".")[0])
    hosts.add("*.ts.net")
    for item in (extra or "").split(","):
        item = item.strip().lower()
        if item:
            hosts.add(item)
    return sorted(hosts)


def host_of(header: str) -> str:
    """The host in a Host header (or a URL's netloc): lowercased, port gone.

    An IPv6 literal keeps its brackets and is written the one canonical way,
    so `[0:0:0:0:0:0:0:1]:3001` and `[::1]:3001` are the same host. Starlette's
    TrustedHostMiddleware takes `Host.split(":")[0]`, which turns every
    bracketed address into "[" — it can never match the `[::1]` listed above,
    and a stack bound to an IPv6 address would refuse its own callers.
    """
    v = (header or "").strip().lower()
    if v.startswith("["):
        end = v.find("]")
        if end < 0:
            return ""
        try:
            return f"[{ipaddress.ip_address(v[1:end]).compressed}]"
        except ValueError:
            return ""
    return v.rsplit(":", 1)[0] if ":" in v else v


def host_allowed(header: str, allowed) -> bool:
    """True when the Host header names one of `allowed` (trusted_hosts()).

    A pattern matches exactly, or as a `*.suffix` wildcard. A bare `*` is NOT
    allow-any here: this list is fed from config, and config must never be
    able to switch the pinning off by accident.
    """
    host = host_of(header)
    if not host or "*" in host:
        return False
    for pattern in allowed:
        if host == pattern:
            return True
        if pattern.startswith("*.") and host.endswith(pattern[1:]):
            return True
    return False


def is_ip_literal(header: str) -> bool:
    """True when the Host header is a literal IP address, not a name.

    Rebinding needs a NAME: the attack is a DNS name the attacker controls,
    re-pointed at this machine. A page whose origin is a literal address was
    served by whatever answers on that address — for one of ours, by us. So a
    server reachable off loopback (a keyed tool server on a LAN or tailnet
    BIND_HOST, dialled as 192.168.1.50 or 100.x.y.z) can answer to any
    literal without reopening the door Host pinning closes.
    """
    try:
        ipaddress.ip_address(host_of(header).strip("[]"))
    except ValueError:
        return False
    return True


class PinnedHostMiddleware:
    """Host pinning as ASGI middleware: 400 unless Host is in `allowed_hosts`.

    Same allowlist and same answer as Starlette's TrustedHostMiddleware, for
    the servers that must also answer on an IPv6 literal (see host_of): the
    tool server and the agent router are dialled as `[::1]` when BIND_HOST is
    an IPv6 address. A request with no Host, or with two, is refused — two
    answers to "which host" is not one. `allow_ip_literals` also admits any
    literal address (see is_ip_literal) for a server that binds BIND_HOST.
    `allow_env` names the setting in the refusal, so whoever reads it knows
    the fix.
    """

    def __init__(self, app, allowed_hosts, allow_env: str = "",
                 allow_ip_literals: bool = False):
        self.app = app
        self.allowed_hosts = list(allowed_hosts)
        self.allow_env = allow_env
        self.allow_ip_literals = allow_ip_literals

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        hosts = [v.decode("latin-1") for k, v in scope.get("headers") or ()
                 if k.lower() == b"host"]
        if len(hosts) == 1 and (
                host_allowed(hosts[0], self.allowed_hosts)
                or (self.allow_ip_literals and is_ip_literal(hosts[0]))):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        msg = ("Invalid host header: this server answers only to loopback, "
               "this machine's own name and *.ts.net names.")
        if self.allow_env:
            msg += (f" To reach it by another name, start it with "
                    f"{self.allow_env}=<name> (comma-separated).")
        body = (msg + "\n").encode()
        await send({"type": "http.response.start", "status": 400,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
