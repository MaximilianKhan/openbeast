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
