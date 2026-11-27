"""The login-bearing Unix socket, shared by beast-chat and beast-artifact.

Both servers take `Tailscale-User-Login` as a READ identity, and both can be
told (CHAT_LOGIN_FROM / ARTIFACT_LOGIN_FROM = unix) to honour it ONLY on a
0600 Unix socket that `tailscale serve` (root) proxies to — so code in a
host-network container, which is a 127.0.0.1 peer, can no longer name the
operator. Two copies of that rule drift; this is the one definition.

How a request is tied to the socket matters as much as the socket's mode.
The first version inferred "came in on the Unix socket" from the ABSENCE of
a peer address (`request.client is None`). uvicorn also reports no peer for
a TCP connection whose getpeername() fails — which is what a client that
sends its request and then resets the connection (SO_LINGER {1,0}) gets
between accept() and connection_made, while the bytes it queued are still
read and dispatched. Any local process could therefore send a forged login
over TCP loopback, reset, and be served (blind) as the operator, writing
`verified: true, peer: "unix"` audit rows in their name (review 2026-11-16).

So the socket is identified POSITIVELY: uvicorn sets scope["server"] from
getsockname(), which is `(ip, port)` for TCP — even after a reset — and
`(socket_path, None)` for AF_UNIX. A request counts as arriving on the login
socket only when that path equals the one we bound.
"""
from __future__ import annotations

import os
import socket
import stat as _st
import sys


def socket_path_of(request) -> str | None:
    """The Unix socket path a request was accepted on, or None for TCP (or
    for anything that does not look like an ASGI request)."""
    scope = getattr(request, "scope", None)
    if not isinstance(scope, dict):
        return None
    server = scope.get("server")
    if (isinstance(server, (tuple, list)) and len(server) == 2
            and server[1] is None and isinstance(server[0], str)
            and server[0]):
        return server[0]
    return None


def arrived_on(request, path: str | None) -> bool:
    """Did this request come in on the Unix socket bound at `path`?

    Never inferred from a missing peer address (see the module docstring):
    no configured path, or a TCP listener, is always False."""
    if not path:
        return False
    got = socket_path_of(request)
    if got is None:
        return False
    return os.path.abspath(got) == os.path.abspath(path)


def unix_listener(path: str, who: str = "openbeast"):
    """Bind the login-bearing Unix socket: 0600, in a directory only we own
    and only we can write.

    tailscaled runs as root, so 0600 costs it nothing; every OTHER local
    user, and every container, is kept out by the mode — which is the whole
    point of moving the login header off TCP loopback. A stale socket from
    a previous run is replaced; anything else at the path is left alone.

    The DIRECTORY matters as much as the socket: anyone who can write it can
    unlink our socket and bind their own at the path, and tailscale serve
    then hands them real tailnet logins and phones' device keys. makedirs'
    mode only applies to a directory it creates, and the recommended
    `.run/` usually exists already (0775 under a 0002 umask). So: refuse a
    directory someone else owns, and strip group/other WRITE from ours —
    read/traverse is left as it was, nothing else in .run/ depends on it.

    The path is bound ABSOLUTE, so the name getsockname() reports — which is
    what arrived_on() compares — never depends on the server's cwd.
    """
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    pst = os.stat(parent)
    if pst.st_uid != os.getuid():
        raise SystemExit(f"ERROR: {parent} is not owned by this user — "
                         f"refusing to put the login socket there")
    if pst.st_mode & 0o022:
        os.chmod(parent, _st.S_IMODE(pst.st_mode) & ~0o022)
        print(f"{who}: removed group/other write from {parent} "
              f"(it holds the login socket)", file=sys.stderr)
    try:
        st = os.lstat(path)
        if _st.S_ISSOCK(st.st_mode):
            os.unlink(path)
        else:
            raise SystemExit(f"ERROR: {path} exists and is not a socket — "
                             f"refusing to replace it")
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        sock.bind(path)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    sock.listen(128)
    return sock
