"""The X-Hydra-Caller token, as the agent router and beast-gate present it.

beast-hydra trusts forwarded identity (X-OpenBeast-Device, X-OpenWebUI-User-*)
only when the request also carries X-Hydra-Caller with the token start.sh
mints into .run/hydra-caller.token (0600) — docs/BEAST_HYDRA_PLAN.md §6.8.
The router and the gate both vouch for what they forward, so both read the
file named by OPENBEAST_HYDRA_CALLER_TOKEN_FILE (conf.sh exports the PATH,
never the token, and only under HYDRA=true).

  * Unset path  -> `configured` is False and the caller adds nothing: a
    stack without hydra sends exactly the headers it always did.
  * The file is re-read when its mtime/size changes (start.sh re-mints it per
    start; a long-lived router must not keep presenting a stale token).
  * FAIL CLOSED: a file that is missing, empty, not a regular file, or
    readable by group/other is never sent. hydra then treats the caller as
    untrusted — identity-keyed rules do not match — and inference still
    works. A token that leaked to other local users is not proof of anything.

Stated limit (plan §6.8, as for edge-local.token): local processes running
as this user can read the file. It protects against REMOTE spoofing only.
"""
from __future__ import annotations

import logging
import os
import stat

HEADER = "X-Hydra-Caller"
ENV = "OPENBEAST_HYDRA_CALLER_TOKEN_FILE"


class CallerToken:
    def __init__(self, path: str | None = None):
        if path is None:
            path = os.environ.get(ENV, "")
        self.path = (path or "").strip()
        self._sig: tuple | None = None
        self._token: str | None = None
        self._warned = False

    @property
    def configured(self) -> bool:
        return bool(self.path)

    def _refuse(self, why: str) -> None:
        if not self._warned:
            logging.warning("hydra caller token %s not sent: %s", self.path, why)
            self._warned = True
        self._token = None

    def get(self) -> str | None:
        """The token, or None (not configured, or refused — see module doc)."""
        if not self.path:
            return None
        try:
            st = os.stat(self.path)
        except OSError:
            self._sig = None
            self._refuse("missing")
            return None
        sig = (st.st_mtime_ns, st.st_size, st.st_mode, st.st_ino)
        if sig == self._sig:
            return self._token
        self._sig = sig
        if not stat.S_ISREG(st.st_mode):
            self._refuse("not a regular file")
            return None
        if st.st_mode & 0o077:
            self._refuse(f"mode {stat.S_IMODE(st.st_mode):o} (want 0600)")
            return None
        try:
            with open(self.path, encoding="utf-8") as fh:
                tok = fh.read().strip()
        except (OSError, UnicodeDecodeError):
            self._refuse("unreadable")
            return None
        if not tok or not tok.isascii() or any(c.isspace() for c in tok):
            self._refuse("empty or malformed")
            return None
        self._token = tok
        self._warned = False
        return tok
