"""Built-in tools for the agent runner.

Each tool is defined as:
  - A schema (OpenAI function-calling format)
  - A handler function that executes the tool and returns a string result
"""

from __future__ import annotations

import codecs
import glob
import html
import http.client
import ipaddress
import json
import os
import re
import resource
import shlex
import signal
import socket
import stat
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextvars import ContextVar
from datetime import datetime
from typing import Any

# Largest slice of a child's output we retain in the PARENT process. The
# RLIMIT_AS below caps the child's own memory, but `cat /dev/zero` streams
# unboundedly *into the parent's* pipe buffer — which has no rlimit — so we
# must cap what the parent keeps too, or the box OOMs anyway. We keep
# draining past this (to avoid a pipe-full deadlock) but discard the excess.
_MAX_CAPTURE_BYTES = 4 * 1024 * 1024
# Largest file read_file will slurp; also refuses non-regular files so a
# read of /dev/zero / a FIFO can't hang or OOM the in-process runner.
_MAX_READ_BYTES = 64 * 1024 * 1024

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

# Per-process address-space cap for model-written code. subprocess timeouts
# with shell=True kill only the sh wrapper — before the 2026-07-07 fix below,
# the grandchild survived as an orphan and two runaway eval programs grew to
# ~140 GB each, exhausting RAM + 187 GB swap and OOM-killing the whole
# session. The killpg reaps the tree at timeout; this rlimit bounds how much
# a memory bomb can grab in the seconds before the timeout fires.
_CHILD_AS_LIMIT = 32 * 1024**3


def _killpg(proc):
    """SIGKILL the whole process group, then reap the leader."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.kill()  # belt-and-suspenders if the leader left its group
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_reaped(command, timeout, **popen_kw):
    """subprocess.run(shell=True)-alike that (a) kills the WHOLE process group
    on timeout and (b) bounds how much output the PARENT buffers.

    Plain subprocess.run kills only the direct child (/bin/sh) on timeout,
    orphaning whatever the shell spawned; and communicate() buffers the
    child's entire stdout in the parent, so `cat /dev/zero` OOMs the runner
    even though the child is rlimited. start_new_session puts the tree in its
    own group for a group SIGKILL, and a reader thread drains the pipe while
    retaining at most _MAX_CAPTURE_BYTES (discarding the rest to avoid a
    pipe-full deadlock). Output is decoded errors="replace" so binary bytes
    don't crash the call.

    Returns (returncode, output_str) — stderr folded into stdout.
    Raises subprocess.TimeoutExpired after reaping on timeout.
    """
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        **popen_kw,
    )
    # Cap the child's address space from the parent. prlimit (instead of a
    # preexec_fn) is safe when the caller is a threaded server (FastMCP);
    # /bin/sh's own children inherit the limit when it forks them.
    try:
        resource.prlimit(proc.pid, resource.RLIMIT_AS,
                         (_CHILD_AS_LIMIT, _CHILD_AS_LIMIT))
    except (OSError, ProcessLookupError):
        pass
    kept = bytearray()
    total = [0]

    def _drain():
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            total[0] += len(chunk)
            if len(kept) < _MAX_CAPTURE_BYTES:
                kept.extend(chunk[: _MAX_CAPTURE_BYTES - len(kept)])

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _killpg(proc)
        raise
    finally:
        reader.join(timeout=5)
        if reader.is_alive():
            # The shell exited but a backgrounded grandchild still holds the
            # stdout pipe — the reader would block forever (and so would we).
            # Killing the group unblocks it AND enforces the no-orphans
            # policy: nothing outlives the tool call. Long-lived daemons must
            # detach properly (setsid + redirect away from the pipe).
            _killpg(proc)
            reader.join(timeout=2)
        if reader.is_alive():
            # Grandchild escaped the group (setsid): force EOF on the raw fd.
            try:
                os.close(proc.stdout.fileno())
            except OSError:
                pass
            reader.join(timeout=2)
        try:
            proc.stdout.close()
        except Exception:
            pass
    out = kept.decode("utf-8", errors="replace")
    if total[0] > len(kept):
        out += f"\n[output truncated — {total[0]} bytes produced, kept {len(kept)}]"
    return proc.returncode, out


# Per-request workspace override. The identity tool server
# (agents/openapi_tools.py) serves many users from ONE process, so an env
# var can't carry the per-user shard — a ContextVar can: it is scoped to
# the request's thread/task, set before the tool call and reset after.
_BASE_DIR_OVERRIDE: ContextVar = ContextVar("openbeast_base_dir", default=None)


def set_base_dir_override(path: str):
    """Point relative tool paths (and the manifest) at `path` for the
    current context. Returns a token for reset_base_dir_override()."""
    return _BASE_DIR_OVERRIDE.set(path)


def reset_base_dir_override(token) -> None:
    _BASE_DIR_OVERRIDE.reset(token)


def _base_dir() -> str:
    """Directory that relative, model-supplied paths resolve against.

    The identity server sets a per-request override (the caller's workspace
    shard). A spawned background agent gets AGENT_WORKDIR (its task's
    working dir). A direct tool call from a chat turn gets
    OPENBEAST_FILES_DIR — a persistent, private (0700) workspace — so
    generated files (reports, charts) land somewhere durable and NOT
    world-readable in /tmp, and every conversation shares one predictable
    home instead of the model's ad-hoc default. Falls back to the process
    cwd only if none is set (e.g. bare `python tools.py`).

    NOT a confinement boundary: `..` and absolute paths leave it freely by
    design (agents do legitimate work anywhere the denylist allows). Writes
    are protected only by _guard_write_path's denylist; kernel-level
    confinement is Arsenal Phase 1 (Sandlock).
    """
    return (_BASE_DIR_OVERRIDE.get()
            or os.environ.get("AGENT_WORKDIR")
            or os.environ.get("OPENBEAST_FILES_DIR")
            or os.getcwd())


def _resolve(path: str) -> str:
    """Expand ~, anchor a relative path to _base_dir(), then realpath it.
    Absolute paths the model supplies are honored as-given (post-realpath)."""
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(_base_dir(), p)
    return os.path.realpath(p)


def _scrubbed_env() -> dict:
    """Copy of the process env minus the stack's secrets.

    The bash tool runs MODEL-authored commands: a prompt-injected `env`
    must not hand over the RBAC profile keys, the identity-JWT signing
    secret (with which the model could mint admin identities), or the
    WebUI admin password that the server process was launched with.
    Mirrors start.sh's systemd-setenv secret filter — stack-prefixed
    names containing KEY/SECRET/PASSWORD are dropped; the user's own
    unrelated env vars are left alone."""
    env = dict(os.environ)
    for name in list(env):
        up = name.upper()
        if (up.startswith(("OPENBEAST_", "WEBUI_", "LLAMA_", "SEARXNG_"))
                and any(t in up for t in ("KEY", "SECRET", "PASSWORD"))):
            env.pop(name)
    return env


def bash(command: str, timeout: int = 120) -> str:
    """Run a shell command and return stdout + stderr.

    Enforced safety: child process group is SIGKILLed on timeout,
    RLIMIT_AS caps the child's address space, and output is capped.
    """
    try:
        # Arsenal Phase 1 hook: OPENBEAST_BASH_WRAPPER is a command prefix
        # (e.g. "sandlock --profile openbeast --") that wraps every model
        # shell command in a kernel-level sandbox. Unset (the default and
        # the eval-validated configuration) runs the command directly.
        # Read per-call so a server doesn't need a restart to toggle it.
        wrapper = os.environ.get("OPENBEAST_BASH_WRAPPER", "").strip()
        if wrapper:
            command = f"{wrapper} /bin/sh -c {shlex.quote(command)}"
        returncode, output = run_reaped(
            command,
            timeout,
            cwd=_base_dir(),
            env=_scrubbed_env(),
        )
        if not output.strip():
            output = f"(exit code {returncode})"
        return output[:50_000]  # cap output size
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


# Credential / persistence locations model-written file ops must never touch.
# Realpath-resolved (so symlinks can't dodge it) and scoped to the real HOME —
# a project-local .npmrc or a dotfiles repo's copy of .bashrc is legitimate
# coding work; only the live copies under ~ are persistence/exfil targets.
# Defense-in-depth: the authoritative sandbox is Arsenal Phase 1 (Sandlock).
_PROTECTED_DIRS = (".ssh", ".gnupg", ".aws", ".kube", ".docker")
_PROTECTED_BASENAMES = {
    ".netrc", ".git-credentials", ".npmrc", ".pypirc",
    ".bashrc", ".bash_profile", ".zshrc", ".profile",
}

# Pseudo-filesystems that read_file refuses: regular-file-shaped but can be
# infinite (/proc/kcore), streaming (a 0-size /proc file), or side-effecting.
_HAZARD_PREFIXES = ("/proc", "/sys", "/dev")


def _hazard_path(rp: str) -> bool:
    """True if the (already-realpath'd) path lives in a pseudo-filesystem."""
    return any(rp == p or rp.startswith(p + os.sep) for p in _HAZARD_PREFIXES)


def _guard_write_path(path: str):
    """Return an error string if `path` is a protected credential/persistence
    target, else None. Applied to write_file / edit_file only — reads stay
    open (an agent may legitimately read config)."""
    rp = os.path.realpath(os.path.expanduser(path))
    home = os.path.realpath(os.path.expanduser("~"))
    rel = os.path.relpath(rp, home)
    inside_home = rel != os.pardir and not rel.startswith(os.pardir + os.sep)
    if inside_home:
        if rel.split(os.sep)[0] in _PROTECTED_DIRS:
            return f"Error: refusing to write inside a credential store ({rp})"
        if rel in _PROTECTED_BASENAMES:
            return f"Error: refusing to write protected file {rp}"
    if rp == "/etc" or rp.startswith("/etc/"):
        return f"Error: refusing to write under /etc ({rp})"
    if rp.endswith(f"{os.sep}.git{os.sep}config"):
        return f"Error: refusing to write a git config ({rp})"
    return None


def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    """Read lines from a file."""
    try:
        path = _resolve(path)
        # Pseudo-filesystems (procfs/sysfs/devfs) present as regular files but
        # can be infinite, blocking, or side-effecting to read — e.g. a 0-size
        # /proc file that streams unbounded content, or /dev/zero. _resolve
        # already realpath'd, so a symlink into them is caught here too.
        if _hazard_path(path):
            return (f"Error: refusing to read {path} — pseudo-filesystem paths "
                    f"(/proc, /sys, /dev) can be infinite or side-effecting")
        # O_NONBLOCK so opening a FIFO can't hang; fstat (not stat) so the
        # regular-file check and the read see the same inode.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            return f"Error: not a regular file (refusing to read {path})"
        if st.st_size > _MAX_READ_BYTES:
            os.close(fd)
            return (f"Error: file too large ({st.st_size} bytes > "
                    f"{_MAX_READ_BYTES}); read a slice with bash instead")
        os.set_blocking(fd, True)
        with os.fdopen(fd, "r", errors="replace") as f:
            # Bound the read regardless of the stat'd size: a pseudo-file (or a
            # file growing under us) can report size 0 yet stream forever. One
            # byte past the cap tells us it was truncated.
            data = f.read(_MAX_READ_BYTES + 1)
        truncated = len(data) > _MAX_READ_BYTES
        lines = data[:_MAX_READ_BYTES].splitlines(keepends=True)
        total = len(lines)
        selected = lines[offset : offset + limit]
        numbered = [f"{i + offset + 1}\t{line}" for i, line in enumerate(selected)]
        note = f" (+ more; read capped at {_MAX_READ_BYTES} bytes)" if truncated else ""
        header = f"[{path}] lines {offset + 1}-{offset + len(selected)} of {total}{note}\n"
        return header + "".join(numbered)
    except Exception as e:
        return f"Error: {e}"


def _manifest_log(action: str, path: str, nbytes: int) -> None:
    """Append a write record to the workspace manifest (.manifest.jsonl).

    The chat workspace (OPENBEAST_FILES_DIR) is a flat namespace shared by
    every conversation; the model only "knows" a file exists if its name is
    in context. The manifest is the durable index — a later turn (or user)
    can answer "what files have I made?" by reading it. Only writes that
    LAND inside the workspace are recorded (agent workdirs and absolute
    paths elsewhere are not workspace artifacts). Fail-soft by contract:
    a manifest problem must never break the write it describes.
    """
    try:
        # Shard-aware: under the identity server, the manifest lives at the
        # caller's shard root (each user indexes only their own files).
        base = _BASE_DIR_OVERRIDE.get() or os.environ.get("OPENBEAST_FILES_DIR")
        if not base:
            return
        base = os.path.realpath(os.path.expanduser(base))
        real = os.path.realpath(path)
        if os.path.commonpath([base, real]) != base:
            return  # not a workspace file
        if os.path.basename(real) == ".manifest.jsonl":
            return  # never index the index
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "action": action,
            "path": os.path.relpath(real, base),
            "bytes": nbytes,
        }
        with open(os.path.join(base, ".manifest.jsonl"), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def write_file(path: str, content: str) -> str:
    """Write content to a file, creating directories if needed."""
    try:
        # Resolve first (anchors relative paths to the private workspace), then
        # guard and write the SAME path — a symlink swapped in after the check
        # can't redirect the write.
        path = _resolve(path)
        blocked = _guard_write_path(path)
        if blocked:
            return blocked
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        _manifest_log("write", path, len(content))
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def list_files(directory: str = ".", pattern: str = "**/*") -> str:
    """List files matching a glob pattern."""
    try:
        directory = _resolve(directory)
        matches = sorted(glob.glob(os.path.join(directory, pattern), recursive=True))
        files = [m for m in matches if os.path.isfile(m)]
        if not files:
            return f"No files matching '{pattern}' in {directory}"
        result = "\n".join(files[:200])
        if len(files) > 200:
            result += f"\n... and {len(files) - 200} more"
        return result
    except Exception as e:
        return f"Error: {e}"


def grep(pattern: str, path: str = ".", file_glob: str = "") -> str:
    """Search file contents for a regex pattern."""
    try:
        cmd = f"grep -rn --include='*' -E {shlex.quote(pattern)} {shlex.quote(path)}"
        if file_glob:
            cmd = f"grep -rn --include={shlex.quote(file_glob)} -E {shlex.quote(pattern)} {shlex.quote(path)}"
        _, output = run_reaped(
            cmd, 30, cwd=_base_dir(),
        )
        return (output or "(no matches)")[:50_000]
    except subprocess.TimeoutExpired:
        return "Error: grep timed out"
    except Exception as e:
        return f"Error: {e}"


def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Replace an exact string in a file with new content."""
    try:
        # Resolve first, then guard/operate on the SAME path (see write_file).
        path = _resolve(path)
        blocked = _guard_write_path(path)
        if blocked:
            return blocked
        if not os.path.isfile(path):
            return f"Error: file not found: {path}"

        with open(path, "r") as f:
            content = f.read()

        if not old_string:
            return "Error: old_string must not be empty"

        if old_string == new_string:
            return "Error: old_string and new_string are identical — nothing to change"

        count = content.count(old_string)
        if count == 0:
            lines = old_string.split("\n")
            if len(lines) > 1 and content.find(lines[0]) != -1:
                return (
                    f"Error: exact match not found in {path}. "
                    f"The first line was found but the full multi-line string didn't match. "
                    f"Check whitespace and indentation."
                )
            return f"Error: old_string not found in {path}"

        if count > 1 and not replace_all:
            return (
                f"Error: old_string appears {count} times in {path}. "
                f"Include more surrounding context to make it unique, "
                f"or set replace_all=true to replace all occurrences."
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        with open(path, "w") as f:
            f.write(new_content)
        _manifest_log("edit", path, len(new_content))

        change_line = content[:content.index(old_string)].count("\n") + 1
        old_lines = old_string.count("\n") + 1
        new_lines = new_string.count("\n") + 1

        if replace_all and count > 1:
            return f"Replaced {count} occurrences in {path} ({len(old_string)} → {len(new_string)} chars each)"
        else:
            return (
                f"Edited {path} at line {change_line}: "
                f"replaced {old_lines} line{'s' if old_lines != 1 else ''} "
                f"with {new_lines} line{'s' if new_lines != 1 else ''}"
            )
    except Exception as e:
        return f"Error: {e}"


def _vet_addr(addr: str) -> str | None:
    """Refusal reason if `addr` is a non-public IP, else None."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])  # strip v6 zone id
    except ValueError:
        return f"unparseable address '{addr}'"
    if (ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_unspecified or ip.is_multicast or ip.is_reserved):
        return f"non-public address {ip}"
    return None


def _resolve_vetted(host: str, port, scheme: str):
    """Resolve `host` and vet EVERY result. Returns (vetted_ips, None) when
    all resolved addresses are public, else (None, reason). Returning the
    exact IPs it validated is what lets the caller CONNECT to one of them —
    closing the DNS-rebinding window where a second resolution could hand
    back 127.0.0.1 after the guard saw a public address."""
    try:
        infos = socket.getaddrinfo(
            host, port or (443 if scheme == "https" else 80),
            proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return None, f"could not resolve host '{host}': {e}"
    if not infos:
        return None, f"host '{host}' resolved to no addresses"
    ips = []
    for info in infos:
        addr = info[4][0]
        reason = _vet_addr(addr)
        if reason:
            return None, f"host '{host}' resolves to {reason}"
        ips.append(addr)
    return ips, None


def _fetch_url_blocked(url: str) -> str | None:
    """SSRF guard for fetch(): return a human-readable refusal reason, or
    None if the URL is safe. Scheme allowlist (http/https only) + resolve
    the hostname and refuse if ANY address is loopback/private/link-local/
    reserved. Blocks http://127.0.0.1:3001, http://169.254.169.254, and
    public names that resolve privately. The authoritative check is repeated
    atomically at connect time (see _PinnedHTTP*Handler) so a DNS flip
    between this call and the socket can't sneak through — this is the
    early, friendly-error copy."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        return f"scheme '{parsed.scheme}' not allowed (http/https only)"
    if not parsed.hostname:
        return "URL has no hostname"
    _, reason = _resolve_vetted(parsed.hostname, parsed.port, parsed.scheme.lower())
    return reason


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that dials a PRE-VETTED IP instead of re-resolving the
    hostname — the IP the SSRF guard approved is the exact IP we connect to."""
    def __init__(self, *a, pinned_ip=None, **kw):
        super().__init__(*a, **kw)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Same pin for TLS. Crucially keeps server_hostname = the ORIGINAL host
    so SNI and certificate validation still check the real name, not the IP."""
    def __init__(self, *a, pinned_ip=None, **kw):
        super().__init__(*a, **kw)
        self._pinned_ip = pinned_ip

    def connect(self):
        sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            sock = self.sock
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _pinned_open(handler, req, conn_class, scheme):
    """Resolve + vet + pin, atomically, for THIS request (initial or any
    redirect hop — urllib re-enters the handler per hop, so every hop is
    independently vetted against the address it actually dials)."""
    parsed = urllib.parse.urlparse(req.full_url)
    ips, reason = _resolve_vetted(parsed.hostname, parsed.port, scheme)
    if reason:
        raise urllib.error.URLError(f"fetch blocked: {reason}")
    return handler.do_open(conn_class, req, pinned_ip=ips[0])


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return _pinned_open(self, req, _PinnedHTTPConnection, "http")


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return _pinned_open(self, req, _PinnedHTTPSConnection, "https")


class _FetchRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject a bad redirect target EARLY with a clear message (defense in
    depth + friendly error). The atomic resolve-vet-pin still runs in the
    pinned handlers when urllib re-opens the redirected request, so this
    early check being one resolution ahead can't be exploited."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = _fetch_url_blocked(newurl)
        if reason:
            raise urllib.error.URLError(
                f"fetch blocked: redirect to {newurl}: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Default HTTP/HTTPS handlers replaced by the pinned variants (build_opener
# swaps same-type handlers), so no request path re-resolves post-guard.
_fetch_opener = urllib.request.build_opener(
    _PinnedHTTPHandler(), _PinnedHTTPSHandler(), _FetchRedirectHandler)


def fetch(url: str, max_length: int = 50_000) -> str:
    """Fetch content from a URL and return as text.

    Deliberately refuses local/private targets for EVERYONE (admin included —
    defense in depth, see docs/RBAC_PLAN.md): http/https schemes only, and any
    hostname resolving to loopback/private/link-local/reserved space is
    blocked, at request time and again on every redirect hop.
    """
    reason = _fetch_url_blocked(url)
    if reason:
        return f"Error: fetch blocked: {reason}"
    # max_length is model-controlled; without a ceiling, max_length*4 below
    # becomes an attempted multi-GB read into memory.
    max_length = max(1, min(int(max_length), 2_000_000))
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; local-agent/1.0)",
                "Accept": "text/html,application/json,text/plain,*/*",
            },
        )
        with _fetch_opener.open(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()
                # The header is server-controlled; a bogus charset name would
                # raise LookupError and fail the whole fetch.
                try:
                    codecs.lookup(charset)
                except LookupError:
                    charset = "utf-8"
            raw_bytes = resp.read(max_length * 4)
            text = raw_bytes.decode(charset, errors="replace")

        if "html" in content_type.lower() or text.strip()[:100].lower().startswith(("<!doctype", "<html")):
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<(br|hr|/p|/div|/h[1-6]|/li|/tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = html.unescape(text)
            text = re.sub(r"[^\S\n]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text.strip()

        if len(text) > max_length:
            text = text[:max_length] + f"\n\n[truncated at {max_length} chars — {len(raw_bytes)} bytes fetched]"

        return text if text else "(empty response)"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(2000).decode("utf-8", errors="replace")
        except Exception:
            pass
        return f"HTTP {e.code} {e.reason}" + (f"\n{body}" if body else "")
    except urllib.error.URLError as e:
        return f"URL error: {e.reason}"
    except Exception as e:
        return f"Error: {e}"


def web_search(query: str, max_results: int = 10) -> str:
    """Search the web using the local SearXNG instance."""
    searxng_url = os.environ.get("SEARXNG_URL", "http://localhost:8888")
    try:
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "categories": "general",
        })
        url = f"{searxng_url}/search?{params}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "local-agent/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError:
        return (
            "Error: SearXNG is not running. Start it with:\n"
            "  docker run -d -p 8888:8080 -e SEARXNG_BASE_URL=http://localhost:8888/ searxng/searxng\n"
            "Or set SEARXNG_URL env var if running on a different port."
        )
    except Exception as e:
        return f"Error: {e}"

    results = data.get("results", [])[:max_results]
    if not results:
        return f"No results for: {query}"

    lines = [f"Web search: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        url = r.get("url", "")
        snippet = r.get("content", "")[:200]
        lines.append(f"{i}. {title}")
        lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")
    return "\n".join(lines)


def task_done(summary: str) -> str:
    """Signal that the task is complete."""
    return f"TASK_DONE: {summary}"


# ---------------------------------------------------------------------------
# Tool registry — single source of truth
# ---------------------------------------------------------------------------

# Each entry: (handler_fn, openai_schema_dict).
# TOOL_SCHEMAS and TOOL_HANDLERS are derived from this list, so they can never
# drift out of sync.
_TOOL_REGISTRY: list[tuple[Any, dict]] = [
    (
        bash,
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run a shell command. Use for building, testing, git operations, installing packages, or any system task.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The shell command to execute"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)", "default": 120},
                    },
                    "required": ["command"],
                },
            },
        },
    ),
    (
        read_file,
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read lines from a file. Returns numbered lines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file"},
                        "offset": {"type": "integer", "description": "Line offset to start from (0-indexed)", "default": 0},
                        "limit": {"type": "integer", "description": "Max lines to read", "default": 500},
                    },
                    "required": ["path"],
                },
            },
        },
    ),
    (
        write_file,
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file. Creates directories if needed. Overwrites existing files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to write to"},
                        "content": {"type": "string", "description": "File content"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    ),
    (
        list_files,
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files matching a glob pattern in a directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "Directory to search", "default": "."},
                        "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')", "default": "**/*"},
                    },
                },
            },
        },
    ),
    (
        grep,
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": "Search file contents for a regex pattern. Returns matching lines with file paths and line numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regex pattern to search for"},
                        "path": {"type": "string", "description": "File or directory to search in", "default": "."},
                        "file_glob": {"type": "string", "description": "Filter files by glob (e.g. '*.py')"},
                    },
                    "required": ["pattern"],
                },
            },
        },
    ),
    (
        edit_file,
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Replace an exact string in a file with new content. Use this instead of write_file when modifying existing files — it's safer and more precise. The old_string must appear exactly once unless replace_all is true. To insert text, include surrounding context in old_string and add new text within that context in new_string.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the file to edit"},
                        "old_string": {"type": "string", "description": "The exact text to find (must be unique in the file)"},
                        "new_string": {"type": "string", "description": "The replacement text"},
                        "replace_all": {"type": "boolean", "description": "Replace all occurrences instead of requiring uniqueness", "default": False},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
    ),
    (
        fetch,
        {
            "type": "function",
            "function": {
                "name": "fetch",
                "description": "Fetch content from a URL and return it as text. HTML pages are cleaned (scripts/styles removed, tags stripped) to return readable text. JSON and plain text are returned as-is.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The URL to fetch (http or https)"},
                        "max_length": {"type": "integer", "description": "Maximum characters to return (default 50000)", "default": 50000},
                    },
                    "required": ["url"],
                },
            },
        },
    ),
    (
        web_search,
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web using the local SearXNG instance. Returns titles, URLs, and snippets. Requires SearXNG running on port 8888.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query string"},
                        "max_results": {"type": "integer", "description": "Maximum results to return (default 10)", "default": 10},
                    },
                    "required": ["query"],
                },
            },
        },
    ),
    (
        task_done,
        {
            "type": "function",
            "function": {
                "name": "task_done",
                "description": "Call this when the task is fully complete. Provide a summary of what was accomplished.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Summary of what was accomplished"},
                    },
                    "required": ["summary"],
                },
            },
        },
    ),
]

# Derived exports — always in sync with _TOOL_REGISTRY.
TOOL_SCHEMAS = [schema for _, schema in _TOOL_REGISTRY]

TOOL_HANDLERS: dict[str, Any] = {}
for _fn, _schema in _TOOL_REGISTRY:
    _name = _schema["function"]["name"]
    TOOL_HANDLERS[_name] = _fn  # runner calls handler(args_dict) directly
