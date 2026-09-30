"""A tiny headless-Chromium driver for the beast-chat console tests.

Stdlib only: a minimal RFC 6455 client (text frames, masking, fragments,
ping/pong) and just enough of the Chrome DevTools Protocol to navigate,
evaluate, set headers and read the app manifest. No selenium, no playwright,
no websocket package — CI installs none of them.

find_chrome() returns None when no Chromium/Chrome is installed; callers
pytest.skip() on that. The browser is started under `nice -n 19` (and
`ionice -c3` where it exists), killed by its recorded pid, and its profile
directory removed.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

# One finder for every browser suite (tests/_cdp_pipe.py): it honours both
# CHROME_BIN and OPENBEAST_TEST_CHROME, so setting either one steers the
# artifact, e2e and chat-console tests alike (it used to steer only half).
from _cdp_pipe import find_chrome  # noqa: E402,F401


class WebSocket:
    def __init__(self, url: str, timeout: float = 30.0):
        u = urllib.parse.urlsplit(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80),
                                             timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        req = (f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
               f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake: connection closed")
            resp += chunk
        head, _, self.buf = resp.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"websocket handshake refused: {head[:80]!r}")

    def _recv_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send_text(self, text: str) -> None:
        data = text.encode("utf-8")
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(head) + body)

    def _send_ctrl(self, opcode: int, payload: bytes = b"") -> None:
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + body)

    def recv_text(self) -> str:
        parts = []
        while True:
            b0, b1 = self._recv_exact(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b1 & 0x80 else None
            payload = self._recv_exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:
                self._send_ctrl(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise ConnectionError("websocket closed by peer")
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self._send_ctrl(0x8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """One headless Chromium, one page, driven over CDP."""

    def __init__(self, width: int = 390, height: int = 844):
        exe = find_chrome()
        if not exe:
            raise RuntimeError("no chromium")
        self.profile = tempfile.mkdtemp(prefix="obchat-cdp-")
        argv = [exe, "--headless=new", "--remote-debugging-port=0",
                f"--user-data-dir={self.profile}", "--no-first-run",
                "--no-default-browser-check", "--disable-gpu",
                "--disable-extensions", "--disable-background-networking",
                "--disable-sync", "--mute-audio", "about:blank"]
        if os.geteuid() == 0 or os.environ.get("CI"):
            argv[1:1] = ["--no-sandbox", "--disable-dev-shm-usage"]
        prefix = ["nice", "-n", "19"]
        if shutil.which("ionice"):
            prefix += ["ionice", "-c3"]
        self.proc = subprocess.Popen(prefix + argv, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        port = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and port is None:
            try:
                with open(os.path.join(self.profile, "DevToolsActivePort")) as f:
                    port = int(f.read().split()[0])
            except (OSError, ValueError, IndexError):
                if self.proc.poll() is not None:
                    break
                time.sleep(0.1)
        if port is None:
            self.close()
            raise RuntimeError("chromium did not open a DevTools port")
        self.port = port
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list",
                                    timeout=10) as r:
            targets = json.loads(r.read())
        page = next((t for t in targets if t.get("type") == "page"), None)
        if page is None:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank",
                                         method="PUT")
            with urllib.request.urlopen(req, timeout=10) as r:
                page = json.loads(r.read())
        self.ws = WebSocket(page["webSocketDebuggerUrl"])
        self._id = 0
        self.events: list[dict] = []
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("Network.enable")
        self.send("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 2,
                   "mobile": True})

    def send(self, method: str, params: dict | None = None,
             timeout: float = 30.0) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send_text(json.dumps({"id": mid, "method": method,
                                      "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = json.loads(self.ws.recv_text())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result") or {}
            if "method" in msg:
                self.events.append(msg)
                del self.events[:-500]
        raise TimeoutError(method)

    def eval(self, expr: str, timeout: float = 30.0):
        r = self.send("Runtime.evaluate", {"expression": expr,
                                            "awaitPromise": True,
                                            "returnByValue": True},
                      timeout=timeout)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"eval failed: {str(r['exceptionDetails'])[:400]}")
        return (r.get("result") or {}).get("value")

    def headers(self, headers: dict) -> None:
        self.send("Network.setExtraHTTPHeaders", {"headers": headers})

    def goto(self, url: str) -> None:
        self.send("Page.navigate", {"url": url})
        self.until("document.readyState === 'complete'", timeout=20)

    def until(self, expr: str, timeout: float = 15.0, poll: float = 0.1):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                # A DOM node serialises to {} (falsy in Python): report it as
                # true; anything else truthy comes back as itself.
                last = self.eval(
                    f"Promise.resolve((function(){{try{{return ({expr});}}"
                    f"catch(e){{return null;}}}})()).then(function(v){{"
                    f"return v ? ((typeof v === 'object' && v.nodeType) ? true : v)"
                    f" : null;}}, function(){{return null;}})", timeout=timeout)
            except (RuntimeError, TimeoutError):
                last = None
            if last:
                return last
            time.sleep(poll)
        raise AssertionError(f"timed out waiting for: {expr} (last={last!r})")

    def close(self) -> None:
        try:
            if hasattr(self, "ws"):
                self.ws.close()
        except Exception:
            pass
        if getattr(self, "proc", None) is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, 9)   # its own session: the renderers too
            except OSError:
                self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        shutil.rmtree(getattr(self, "profile", ""), ignore_errors=True)
